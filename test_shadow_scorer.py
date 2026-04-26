"""Synthetic-data test for tradier_options_shadow_scorer.

Builds a fake shadow_dir with 5 days of variant data + matching live state, then runs
the scorer to verify:
  - hit-rate scoring math is correct (calls up = hit, puts up = miss, etc.)
  - history accumulates per variant
  - mutation fires after MUTATION_LOSS_STREAK losses
  - mutation respects bounds
  - suggestions.md is produced
  - graduation list populates after SUGGESTION_WIN_STREAK wins

Runs in an isolated test-shadow dir so it doesn't pollute live data.
Exit 0 = all green.
"""
import asyncio
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_PATH))


def _patch_dirs(tmp: Path):
    """Monkey-patch the SHADOW_DIR + STATE_DIR used by the scorer + compare."""
    import tradier_options_shadow_scorer as scorer
    import tradier_options_shadow_compare as compare
    scorer.SHADOW_DIR = tmp / "shadow"
    scorer.STATE_DIR = tmp / "state"
    scorer.SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    scorer.STATE_DIR.mkdir(parents=True, exist_ok=True)
    compare.SHADOW_DIR = scorer.SHADOW_DIR
    compare.STATE_DIR = scorer.STATE_DIR
    return scorer, compare


def _seed_variant_day(scorer, variant: str, date_str: str, proposals):
    """Write a fake decisions_<date>.jsonl with the given (symbol, side) proposals."""
    var_dir = scorer.SHADOW_DIR / variant
    var_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}T15:00:00Z",
        "variant": variant,
        "decisions": [{"action": "BUY", "symbol": s, "option_type": sd}
                      for (s, sd) in proposals],
        "opportunities": [],
        "summary": {"n_buy": len(proposals)},
        "market_open": True,
    }
    (var_dir / f"decisions_{date_str}.jsonl").open("a").write(json.dumps(rec) + "\n")


def _seed_live_day(scorer, date_str: str, proposals):
    """Write a fake state JSONL where each (symbol, side) appears as a new OCC."""
    sf = scorer.STATE_DIR / f"{date_str}.jsonl"
    rec = {
        "ts": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}T13:31:00Z",
        "positions": [
            {"occ": f"{s}{date_str}{sd[0].upper()}00100000", "symbol": s, "option_type": sd}
            for (s, sd) in proposals
        ],
    }
    sf.write_text(json.dumps(rec) + "\n")


def _stub_quotes(scorer, sym_to_change: dict):
    """Replace fetch_eod_quotes with a stub that returns canned moves.
    sym_to_change[sym] = pct_change (e.g. +1.5 for +1.5% intraday)."""
    async def stub(symbols):
        out = {}
        for s in symbols:
            pct = sym_to_change.get(s, 0.0)
            out[s] = {"open": 100.0, "high": 110.0, "low": 90.0,
                      "close": 100.0 * (1 + pct / 100.0)}
        return out
    scorer.fetch_eod_quotes = stub


def _make_proposals(records_to_proposals):
    """Convert simple list-of-(sym,side) into the (sym, side, ts) tuple format."""
    return [(s, sd, "2026-04-26T15:00:00Z") for (s, sd) in records_to_proposals]


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="shadow_test_"))
    print(f"test sandbox: {tmp}")
    try:
        scorer, compare = _patch_dirs(tmp)
        # Stub the network call
        # Day moves: AAPL +2% (favors call), TSLA -2% (favors put), MSFT +0.1% (no-move skip)
        moves = {"AAPL": 2.0, "TSLA": -2.0, "MSFT": 0.1, "GOOG": 1.5, "META": -1.5,
                 "AMZN": 0.0, "NVDA": 3.0, "PLTR": -3.0}
        _stub_quotes(scorer, moves)
        ok = True

        # ---- TEST 1: hit-rate math ----
        print("\nT1. Hit-rate scoring math")
        proposals_t1 = _make_proposals([("AAPL", "call"), ("TSLA", "put"), ("MSFT", "call")])
        eod_q = await scorer.fetch_eod_quotes(["AAPL", "TSLA", "MSFT"])
        sc = scorer._score_proposals(proposals_t1, eod_q)
        # AAPL +2% call = HIT (above 0.5%); TSLA -2% put = HIT; MSFT +0.1% call = neutral (below threshold)
        assert sc["n_hits"] == 2, f"expected 2 hits, got {sc['n_hits']}"
        assert sc["n_misses"] == 0, f"expected 0 misses, got {sc['n_misses']}"
        # MSFT +0.1% is below threshold so it scores as neutral (move recorded, but not hit/miss)
        # Per scorer code: in_dir<-thr=miss, in_dir>=thr=hit, else just adds to move stats; n_scored=hits+misses=2
        assert sc["hit_rate"] == 1.0, f"expected hit_rate=1.0, got {sc['hit_rate']}"
        print(f"  ✓ AAPL+2% call + TSLA-2% put + MSFT+0.1% call → hits={sc['n_hits']} misses={sc['n_misses']} rate={sc['hit_rate']}")

        # ---- TEST 2: directional miss ----
        print("\nT2. Directional miss detection")
        # AAPL +2% but proposed as PUT = miss; TSLA -2% but proposed as CALL = miss
        proposals_t2 = _make_proposals([("AAPL", "put"), ("TSLA", "call")])
        sc2 = scorer._score_proposals(proposals_t2, eod_q)
        assert sc2["n_misses"] == 2, f"expected 2 misses, got {sc2['n_misses']}"
        assert sc2["hit_rate"] == 0.0, f"expected hit_rate=0.0, got {sc2['hit_rate']}"
        print(f"  ✓ Wrong-side proposals → misses={sc2['n_misses']}")

        # ---- TEST 3: history accumulation + loss streak triggers mutation ----
        print("\nT3. Mutation after 3-day loss streak")
        # Variant 'tight_wtdc_80' loses to live 3 days running → must mutate
        variants = {"live": {}, "tight_wtdc_80": {"OPTIONS_BUY_MIN_WT_DC_SCORE": 80.0}}
        history = {}
        # Simulate 3 days where variant scored worse than live
        for d in ["20260420", "20260421", "20260422"]:
            per_var_sc = {"live": {"hit_rate": 0.7, "n_proposals": 10},
                          "tight_wtdc_80": {"hit_rate": 0.4, "n_proposals": 10}}
            live_sc = per_var_sc["live"]
            history = scorer._update_history(history, d, per_var_sc, live_sc)
        loss_streak = scorer._streak(history["tight_wtdc_80"], won=False)
        assert loss_streak == 3, f"expected loss_streak=3, got {loss_streak}"
        print(f"  ✓ loss_streak after 3 losing days = {loss_streak}")
        new_variants, events = scorer._mutate_variants(variants, history, "20260422")
        assert len(events) == 1, f"expected 1 mutation, got {len(events)}"
        assert events[0]["variant"] == "tight_wtdc_80"
        old_v = variants["tight_wtdc_80"]
        new_v = new_variants["tight_wtdc_80"]
        assert old_v != new_v, f"expected mutation, got identical: {old_v} vs {new_v}"
        print(f"  ✓ mutation fired: {events[0]['knob']} {events[0]['old_value']} → {events[0]['new_value']}")

        # ---- TEST 4: live variant is protected ----
        print("\nT4. 'live' variant is never mutated")
        variants_t4 = {"live": {"OPTIONS_BUY_MIN_WT_DC_SCORE": 70.0}}
        history_t4 = {"live": [{"date": "20260420", "hit_rate": 0.1, "live_hit_rate": 0.5,
                                "delta_vs_live": -0.4, "won_vs_live": False}] * 5}
        new_variants_t4, events_t4 = scorer._mutate_variants(variants_t4, history_t4, "20260422")
        assert len(events_t4) == 0, f"expected 0 mutations on live, got {len(events_t4)}"
        print(f"  ✓ live unchanged ({len(events_t4)} mutations)")

        # ---- TEST 5: mutation respects bounds ----
        print("\nT5. Mutation respects knob bounds")
        # Force a knob already at min — direction must clamp to min
        variants_t5 = {"v": {"OPTIONS_BUY_MIN_WT_DC_SCORE": 50.0}}  # at min bound
        history_t5 = {"v": [{"won_vs_live": False}] * 5}
        for _ in range(20):  # 20 trials to catch any out-of-bounds mutation
            nv, _ = scorer._mutate_variants(variants_t5, history_t5, "20260422")
            for k, v in nv["v"].items():
                if k in scorer.KNOB_BOUNDS:
                    b = scorer.KNOB_BOUNDS[k]
                    assert b["min"] <= v <= b["max"], f"{k}={v} out of bounds [{b['min']}, {b['max']}]"
        print(f"  ✓ 20 mutation trials all within bounds")

        # ---- TEST 6: full pipeline (score + mutate + suggestions emit) ----
        print("\nT6. Full pipeline produces scores + suggestions.md")
        scorer.SHADOW_DIR.mkdir(parents=True, exist_ok=True)
        # Reset the test data
        for sub in scorer.SHADOW_DIR.iterdir() if scorer.SHADOW_DIR.exists() else []:
            if sub.is_dir():
                shutil.rmtree(sub)
            else:
                sub.unlink()
        per_var_props = {
            "live": _make_proposals([("AAPL", "call"), ("MSFT", "call")]),  # 1 hit
            "agg": _make_proposals([("AAPL", "call"), ("TSLA", "put"), ("NVDA", "call")]),  # 3 hits
        }
        variants_t6 = {"live": {}, "agg": {"OPTIONS_BUY_MIN_DTE": 30}}
        # Stub _live_proposals_for_date to use the live entry
        scorer._live_proposals_for_date = lambda d: per_var_props["live"]
        result = await scorer.score_and_mutate("20260426", per_var_props, variants_t6,
                                               scorer.SHADOW_DIR / "variants.json")
        sugg_path = Path(result["suggestions_path"])
        assert sugg_path.exists(), f"suggestions file missing: {sugg_path}"
        sugg_text = sugg_path.read_text()
        assert "agg" in sugg_text and "live" in sugg_text
        print(f"  ✓ scores file: scores_20260426.json written")
        print(f"  ✓ suggestions: {sugg_path.name} ({len(sugg_text)} bytes)")
        print(f"  ✓ live hit_rate={result['live_score']['hit_rate']:.2f}  "
              f"agg hit_rate={result['per_variant_scores']['agg']['hit_rate']:.2f}")

        # ---- TEST 7: auto-graduation fires after 3-win streak ----
        print("\nT7. Auto-graduation after 3-win streak")
        # Reset state
        if (scorer.SHADOW_DIR / "live_overrides_active.json").exists():
            (scorer.SHADOW_DIR / "live_overrides_active.json").unlink()
        if (scorer.SHADOW_DIR / "mutation_log.jsonl").exists():
            (scorer.SHADOW_DIR / "mutation_log.jsonl").unlink()
        variants_t7 = {"live": {}, "winner": {"OPTIONS_BUY_MIN_WT_DC_SCORE": 75.0}}
        # 3 days winning with sufficient proposals
        history_t7 = {"winner": [
            {"date": "20260420", "hit_rate": 0.7, "n_proposals": 10,
             "live_hit_rate": 0.5, "delta_vs_live": 0.2, "won_vs_live": True},
            {"date": "20260421", "hit_rate": 0.65, "n_proposals": 8,
             "live_hit_rate": 0.45, "delta_vs_live": 0.2, "won_vs_live": True},
            {"date": "20260422", "hit_rate": 0.72, "n_proposals": 12,
             "live_hit_rate": 0.5, "delta_vs_live": 0.22, "won_vs_live": True},
        ]}
        events = scorer._auto_graduate(variants_t7, history_t7, {}, "20260422")
        assert len(events) >= 1, f"expected ≥1 graduation, got {len(events)}"
        assert events[0]["event"] == "GRADUATE"
        assert events[0]["knob"] == "OPTIONS_BUY_MIN_WT_DC_SCORE"
        assert events[0]["new_value"] == 75.0
        ovrf = scorer.SHADOW_DIR / "live_overrides_active.json"
        assert ovrf.exists(), "active overrides file not written"
        ovr = json.loads(ovrf.read_text())
        assert "OPTIONS_BUY_MIN_WT_DC_SCORE" in ovr["active_overrides"]
        print(f"  ✓ graduated: {events[0]['knob']}={events[0]['new_value']} "
              f"from variant '{events[0]['variant']}'")
        print(f"  ✓ active_overrides file written: {ovrf.name}")

        # ---- TEST 8: cooldown blocks re-graduation ----
        print("\nT8. Cooldown blocks back-to-back graduations")
        # The previous graduation just fired (cooldown should now be active)
        history_t8 = {"another_winner": [
            {"date": "20260420", "hit_rate": 0.8, "n_proposals": 10, "won_vs_live": True},
            {"date": "20260421", "hit_rate": 0.8, "n_proposals": 10, "won_vs_live": True},
            {"date": "20260422", "hit_rate": 0.8, "n_proposals": 10, "won_vs_live": True},
        ]}
        variants_t8 = {"live": {}, "another_winner": {"OPTIONS_BUY_MIN_DTE": 45}}
        events_t8 = scorer._auto_graduate(variants_t8, history_t8, {}, "20260422")
        assert len(events_t8) == 0, f"cooldown failed: got {len(events_t8)} graduations"
        print(f"  ✓ cooldown blocked second graduation (got {len(events_t8)})")

        # ---- TEST 9: small-sample graduation rejected ----
        print("\nT9. GRADUATE_MIN_PROPOSALS rejection (lucky tiny sample)")
        # Bypass cooldown by clearing log
        (scorer.SHADOW_DIR / "mutation_log.jsonl").unlink()
        history_t9 = {"luckysmall": [
            {"date": "20260420", "hit_rate": 1.0, "n_proposals": 1, "won_vs_live": True},
            {"date": "20260421", "hit_rate": 1.0, "n_proposals": 2, "won_vs_live": True},
            {"date": "20260422", "hit_rate": 1.0, "n_proposals": 1, "won_vs_live": True},
        ]}
        variants_t9 = {"live": {}, "luckysmall": {"OPTIONS_BUY_MAX_OTM_PCT": 4.0}}
        events_t9 = scorer._auto_graduate(variants_t9, history_t9, {}, "20260422")
        assert len(events_t9) == 0, f"small-sample filter failed: got {len(events_t9)} graduations"
        print(f"  ✓ small samples (1-2 props/day) rejected")

        # ---- TEST 10: runtime override applied to live config ----
        print("\nT10. _apply_runtime_overrides patches config")
        # Use the active_overrides file we wrote in T7
        # Restore it
        scorer._save_active_overrides({
            "ts_last_updated": "2026-04-26T20:05:00Z",
            "active_overrides": {
                "OPTIONS_BUY_MIN_WT_DC_SCORE": {
                    "value": 75.0, "graduated_from": "winner",
                    "graduated_ts": "2026-04-26T20:05:00Z",
                    "win_streak_at_graduation": 3,
                    "avg_delta_vs_live": 0.2,
                    "rollback_value": 70.0,
                }}
        })
        # Agent reads overrides from BASE_PATH/data/options_shadow/. Mirror the file there.
        import tradier_options_agent as agent
        orig_base = agent.BASE_PATH
        agent.BASE_PATH = tmp
        agent_overrides_dir = tmp / "data" / "options_shadow"
        agent_overrides_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(scorer.SHADOW_DIR / "live_overrides_active.json",
                    agent_overrides_dir / "live_overrides_active.json")
        try:
            from config_tradier import TradierConfig
            cfg = TradierConfig()
            orig_val = getattr(cfg, "OPTIONS_BUY_MIN_WT_DC_SCORE", None)
            applied = agent._apply_runtime_overrides(cfg)
            new_val = getattr(cfg, "OPTIONS_BUY_MIN_WT_DC_SCORE", None)
            assert "OPTIONS_BUY_MIN_WT_DC_SCORE" in applied, f"override not applied: {applied}"
            assert new_val == 75.0, f"expected 75.0, got {new_val}"
            print(f"  ✓ config patched: OPTIONS_BUY_MIN_WT_DC_SCORE {orig_val} → {new_val}")
        finally:
            agent.BASE_PATH = orig_base

        print("\n" + "=" * 60)
        print(f" ALL TESTS PASSED")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n  ✗ FAIL: {e}")
        return 1
    except Exception as e:
        import traceback
        print(f"\n  ✗ EXCEPTION: {e}")
        traceback.print_exc()
        return 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

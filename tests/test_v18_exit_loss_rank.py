"""tests for tools/v18_exit_loss_rank.py (V18 L1 learning layer, doc 17 §3.2) — fixture ledgers + /history lines."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import v18_exit_loss_rank as R  # noqa: E402
from tools import v18_learn_common as C  # noqa: E402


def _row(fam, pp, pct, bars=1, mfe=None, post=None, sym="AAAUSDT_LONG", dep=100.0, peak=200.0, entry="B12"):
    return {"exit_family": fam, "entry_family": entry, "pnl_pp": pp, "pnl_pct": pct, "bars_held": bars, "mfe_pct": mfe,
            "post_exit_32b_pct": post, "sym_side": sym, "deployed": dep, "peak": peak}


def test_exit_family_vocabulary():
    assert C.exit_family("EXIT_VELOCITY_WT against-long g0.21%") == "EXIT_VELOCITY_WT"
    assert C.exit_family("MAKER_PROFIT_EXIT_STATEFUL_PORTED_EXIT g-0.53% age30m |VEC_EXACT") == "STATEFUL_PORTED_EXIT"
    assert C.exit_family("MAKER_PROFIT_EXIT_FIXED_STOP x") == "FIXED_STOP"
    assert C.exit_family("DC_HOPELESS_entry=43.36_dc=[35.36,42.70]") == "DC_HOPELESS"
    assert C.exit_family("DAYTRADE_TARGET_dc_15m_high_-0.10%_TARGET_BUF") == "DAYTRADE_TARGET"
    assert C.exit_family("VEC_EXACT_CLOSE_EXIT_OWED_ULTIMATE_DC_4h_HARD_STOP") == "VEC_EXIT_OWED"
    assert C.exit_family("E_3_STRUCTURE_EXIT_2TF_15m_4h") == "E_3_STRUCTURE_EXIT_2TF_15m_4h"
    assert C.is_stop_execution("FIXED_STOP") and C.is_stop_execution("VEC_CONVERGE_OPEN") and not C.is_stop_execution("EXIT_VELOCITY_WT")
    assert C.map_to_vocab("UNCOND_DC_LOW_15M_UNCOND", {"UNCOND_DC_LOW_15M", "UNCOND"}) == "UNCOND_DC_LOW_15M"


def test_family_stats_measures():
    rows = [_row("EXIT_VELOCITY_WT", -1.0, -0.5, bars=1, mfe=1.2, post=2.0),  # one-bar loser, drift (mfe>=1, closed<=0), premature
            _row("EXIT_VELOCITY_WT", 0.5, 1.0, bars=3, mfe=3.0, post=0.2),    # winner with giveback (3.0-1.0)*100/200 = 1.0
            _row("DAYTRADE_TARGET", 0.4, 0.8, bars=5, mfe=0.8, post=0.0)]
    st = {s["family"]: s for s in R.family_stats(rows)}
    ev = st["EXIT_VELOCITY_WT"]
    assert ev["n"] == 2 and ev["losers"] == 1 and ev["loss_sum_pct"] == -1.0
    assert ev["one_bar_share"] == 0.5 and ev["mean_bars_held"] == 2.0
    assert abs(ev["giveback_sum"] - 1.0) < 1e-9
    assert ev["drift_n"] == 1 and ev["drift_into_loss_sum"] == -1.0
    assert ev["premature_n"] == 1 and abs(ev["premature_sum"] - 1.0) < 1e-9  # 2.0 x 100/200
    assert st["DAYTRADE_TARGET"]["losers"] == 0
    ranked = R.family_stats(rows)
    assert ranked[0]["family"] == "EXIT_VELOCITY_WT"  # most negative loss sum first


def _write(p, lines):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(d) + "\n" for d in lines))


def test_live_lifecycles_era_cut_echo_and_separation(tmp_path):
    h = tmp_path / "data" / "history" / "ang"
    # pre-cut lifecycle (close 2026-10-13) must not be ranked; hermetic one (2026-10-15) must
    _write(h / "AAAUSDT_LONG.jsonl", [
        {"ts": "2026-10-13T10:00:00+00:00", "type": "AUGMENT", "qty": 10, "price": 1.0, "reason": "B12 |VEC_EXACT"},
        {"ts": "2026-10-13T11:00:00+00:00", "type": "REDUCE", "qty": 10, "price": 0.9, "reason": "EXIT_VELOCITY_WT against-long |VEC_EXACT"},
        {"ts": "2026-10-15T10:00:00+00:00", "type": "AUGMENT", "qty": 10, "price": 1.0, "reason": "B12 |VEC_EXACT"},
        {"ts": "2026-10-15T10:00:00+00:00", "type": "AUGMENT", "qty": 10, "price": 1.0, "reason": "B12 |VEC_EXACT"},  # duplicate line
        {"ts": "2026-10-15T10:00:01+00:00", "type": "AUGMENT", "qty": 10, "price": 1.0, "reason": "BROKER_RECONCILED order=1"},
        {"ts": "2026-10-15T10:15:00+00:00", "type": "REDUCE", "qty": 10, "price": 0.95, "reason": "EXIT_VELOCITY_WT against-long |VEC_EXACT"},
        {"ts": "2026-10-16T10:00:00+00:00", "type": "AUGMENT", "qty": 10, "price": 1.0, "reason": "B12 |VEC_EXACT"},
        {"ts": "2026-10-16T12:00:00+00:00", "type": "REDUCE", "qty": 10, "price": 0.98, "reason": "MAKER_PROFIT_EXIT_FIXED_STOP lvl |"},
    ])
    _write(h / "AAAUSDT_SHORT.jsonl", [
        {"ts": "2026-10-15T10:00:00+00:00", "type": "AUGMENT", "qty": 10, "price": 1.0, "reason": "B12 |VEC_EXACT"},
        {"ts": "2026-10-15T10:15:00+00:00", "type": "REDUCE", "qty": 10, "price": 1.1, "reason": "EXIT_VELOCITY_WT against-short |VEC_EXACT"},
    ])
    _write(tmp_path / "data" / "tradier" / "history" / "trb" / "AAA_LONG.jsonl", [
        {"ts": "2026-10-16T14:00:00+00:00", "type": "OPEN", "qty": 2, "price": 10.0, "reason": "DC_BREAK"},
        {"ts": "2026-10-18T14:00:00+00:00", "type": "CLOSE", "qty": 2, "price": 9.0, "reason": "DAYTRADE_TARGET_dc_15m_high"},
    ])
    rows, cnt = R.live_lifecycles("CRYPTO_LONG", tmp_path, None)
    assert {r["sym_side"] for r in rows} == {"AAAUSDT_LONG"}  # no SHORT file, no stock file: cat_sides strictly apart
    assert cnt["close_pre_hermetic"] == 1
    assert cnt["broker_echo_dropped"] == 1 and cnt["duplicate_event_dropped"] == 1
    cols = sorted((r["column"], r["exit_family"]) for r in rows)
    assert cols == [("live_hermetic", "EXIT_VELOCITY_WT"), ("stop_execution", "FIXED_STOP")]
    vel = next(r for r in rows if r["exit_family"] == "EXIT_VELOCITY_WT")
    assert abs(vel["pnl_dollars"] - (-0.5)) < 1e-9  # 10 x (0.95 - 1.0): the echo and the duplicate did not double the qty
    assert abs(vel["pnl_pp"] - (-0.5 / 10.0 * 100.0)) < 1e-9  # window peak cost basis 10 $
    srows, _ = R.live_lifecycles("STOCKS_LONG", tmp_path, None)
    assert len(srows) == 1 and srows[0]["column"] == "live_hermetic" and srows[0]["opened_pre_hermetic"] is True
    assert srows[0]["exit_family"] == "DAYTRADE_TARGET"


def test_measured_support_needs_gain_ok():
    raw = {"raw": {s: [{"family": "F", "switch": "S", "cand": "1", "n_base_losers": 3, "loss_before_pp": -2.0,
                        "family_loss_after_total_pp": -1.0, "d_gain": dg, "valid": True}] for s, dg in (("A", 0.1), ("B", -3.0), ("C", 0.0), ("D", 0.2))}}
    agg = R.aggregate_measured(raw)["F"][0]
    assert agg["n_syms_measured"] == 4 and agg["n_syms_loss_reduced"] == 4
    assert agg["n_syms_loss_reduced_gain_ok"] == 3  # B cut the family loss but lost 3 pp of board gain: not support


def _tidx():
    return {"switches": {"EXIT_VELOCITY_WT_MIN_TFS": ["1", "2", "3"], "WT_4H_VEL_EXIT_REQUIRE_PROFIT": ["True", "False"],
                         "EXIT_VELOCITY_WT_ENABLED": ["True", "False"], "ABLATION_DISABLE_FILTER_EXIT": ["False", "True"]},
            "defaults": {"EXIT_VELOCITY_WT_MIN_TFS": "1", "WT_4H_VEL_EXIT_REQUIRE_PROFIT": "True", "EXIT_VELOCITY_WT_ENABLED": "True"},
            "tabs": {"EXIT_VELOCITY": {"rows": [[3, "EXIT_VELOCITY_WT_MIN_TFS", "1", True], [4, "EXIT_VELOCITY_WT_MIN_TFS", "2", False],
                                                [5, "EXIT_VELOCITY_WT_ENABLED", "False", False]], "yellows": ["X_FILTER_TF=OFF"]}}}


def test_menu_curated_verified_against_template_and_pending():
    st = {"family": "EXIT_VELOCITY_WT", "n": 10, "losers": 6, "loss_sum_pct": -3.0, "drift_n": 0, "giveback_sum": 0.0}
    ev = {"naked": {"EXIT_VELOCITY_WT_MIN_TFS=2": [9, 9, 3, 1, 4.0, 0, 20, 10, "", 9]},
          "pairs": {"EXIT_VELOCITY_WT_MIN_TFS=2|X_FILTER_TF=OFF": [5, 5, 2, 1.0, 2, 0.4, 5, 5, 5]}}
    menu, dropped = R.build_menu("CRYPTO_LONG", [st], [], _tidx(), ev, {}, {})
    paths = menu["EXIT_VELOCITY_WT"]
    keys = {(p["switch"], p["cand"]) for p in paths}
    assert ("EXIT_VELOCITY_WT_MIN_TFS", "2") in keys and ("EXIT_VELOCITY_WT_MIN_TFS", "3") in keys
    assert all(p["learned_status"] == "pending_evidence" and p["forward_factor"] == 0.5 for p in paths)
    p2 = next(p for p in paths if (p["switch"], p["cand"]) == ("EXIT_VELOCITY_WT_MIN_TFS", "2"))
    assert p2["support_n"] == 9 and p2["support_syms"] == 3 and p2["companions"] == [{"filter": "X_FILTER_TF", "opt": "OFF"}]
    names = {d["switch"] for d in dropped}
    assert "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM" in names and "EXIT_VELOCITY_WT_MIN_HOLD_ENABLED" in names  # not template rows here
    assert not any(p["switch"].startswith("ABLATION_") for p in paths)

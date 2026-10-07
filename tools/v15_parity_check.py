#!/usr/bin/env python3
"""v15_parity_check — for one sym_side's WINNING overrides, run the vector engine
(evaluate_sanitized / v12_quick_engine) and the live-faithful scalar engine
(backtest_v12_engine.run_one) on the SAME frozen 30D NPZ slice and report parity.

Parity criteria (identical to v15_pilot.parity_ok): both valid, trade-count ratio
0.80..1.25, gain within 0.5pp OR 15% relative. Prints one PARITY line per sym_side.
Winning overrides come from data/reports/lifecycle_pilot/{SS}_v14_progress.json
(cumulative_overrides), or {SS}_best.json overrides as fallback.
"""
import argparse, json, os, pathlib, sys

ROOT = pathlib.Path(os.environ.get("V15_PARITY_ROOT") or pathlib.Path(__file__).resolve().parents[1])  # own repo (was: sandbox-pinned)
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))  # ROOT FIRST: a stray tools/v12_quick_engine.py once shadowed the engine here (vec=None on s1, 2026-10-04) — root modules always win
os.environ.setdefault("BASE_PATH", str(ROOT))
os.environ["V12_NPZ_CACHE"] = "32"
# 2026-10-06 parity lane A: without V8_FORCE_REAL=1, importing backtest_v12_engine applies the UNION of every sym's
# data/hourly_reconfig/trb/active_config.json overrides (224 keys / 208 syms) globally, so a sym_side's live leg ran with
# ~90-120 foreign keys its final set does not contain. V15_PARITY_FORCE_REAL=0 restores the old (contaminated) behaviour.
if os.environ.get("V15_PARITY_FORCE_REAL", "1") == "1":
    os.environ.setdefault("V8_FORCE_REAL", "1")

# V8_PRESERVE_DEBOUNCE=1 → set BT_PRESERVE_DEBOUNCE_ACROSS_BARS=True on both configs so the scalar
# live-faithful engine does NOT wipe per-bar cooldowns/debounces every simulated bar (the churn
# cause: exits/entries re-fire every bar). With it True, cooldowns expire by simulated wall-clock,
# matching live cadence. Used to test/enforce the churn fix (backtest_engine_churn_bug).
if os.environ.get("V8_PRESERVE_DEBOUNCE") == "1":
    try:
        import config as _c_pre
        _c_pre.BT_PRESERVE_DEBOUNCE_ACROSS_BARS = True
    except Exception:
        pass
    try:
        import config_tradier as _ct_pre
        _ct_pre.BT_PRESERVE_DEBOUNCE_ACROSS_BARS = True
        try:
            _ct_pre.TradierConfig.BT_PRESERVE_DEBOUNCE_ACROSS_BARS = True
        except Exception:
            pass
    except Exception:
        pass


PROGRESS_OVERRIDE = None  # FINAL PHASE 2026-10-02: --progress PATH = the exact final progress JSON whose cumulative_overrides are parity-tested


def load_overrides(symside):
    if PROGRESS_OVERRIDE and pathlib.Path(PROGRESS_OVERRIDE).exists():
        d = json.loads(pathlib.Path(PROGRESS_OVERRIDE).read_text())
        ov = d.get("cumulative_overrides")
        if isinstance(ov, dict):
            return ov
    pj = ROOT / "data" / "reports" / "lifecycle_pilot" / f"{symside}_v14_progress.json"
    if pj.exists():
        d = json.loads(pj.read_text())
        ov = d.get("cumulative_overrides")
        if isinstance(ov, dict) and ov:
            return ov
    bj = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{symside}_best.json"
    if bj.exists():
        d = json.loads(bj.read_text())
        return d.get("overrides") or d
    return {}


def parity_ok(live, vec):
    if not live.get("valid"):
        return False, f"live invalid: {live.get('invalid_reason')}"
    if not vec.get("valid"):
        return False, f"vector invalid: {vec.get('invalid_reason')}"
    lt = int(live.get("trades") or 0); vt = int(vec.get("trades") or 0)
    if lt == 0 or vt == 0:
        return False, f"zero trades live={lt} vec={vt}"
    ratio = vt / lt
    if not (0.80 <= ratio <= 1.25):
        return False, f"trade-count ratio {ratio:.2f} out of 0.80..1.25 (live {lt} vec {vt})"
    lg = float(live.get("gain_pct") or 0); vg = float(vec.get("gain_pct") or 0)
    if abs(lg - vg) > 0.5 and abs(lg - vg) / max(1e-9, abs(lg)) > 0.15:
        return False, f"gain mismatch live {lg:.4f} vec {vg:.4f}"
    return True, f"parity ok (live {lg:.2f}%/{lt}t vec {vg:.2f}%/{vt}t)"


def _ledger_span(ledger):
    rows = [t for t in (ledger or []) if isinstance(t, dict)]
    tss = []
    for t in rows:
        try:
            ts = float(t.get("ts") or 0.0)
        except Exception:
            continue
        if ts > 1e11:
            ts = ts / 1000.0
        if ts > 0:
            tss.append(ts)
    if not tss:
        return 0, None
    if len(tss) == 1:
        return 1, 0.0
    return len(tss), (max(tss) - min(tss)) / 86400.0


def check(symside, window_days=30):
    ov = load_overrides(symside)
    from tools.opt.v12_pilot import evaluate_sanitized
    try:
        vec = evaluate_sanitized(symside, dict(ov), window_days=window_days, include_ledger=True)
    except Exception as e:
        vec = {"valid": False, "invalid_reason": f"vec {e}", "gain_pct": 0, "trades": 0}
    import backtest_v12_engine as B
    try:
        live = B.run_one(symside, dict(ov), window_days=window_days)
    # 2026-10-04 parity-harness (staged): catch BaseException, not Exception.
    # RateGuard (test_rate_guard.py) calls sys.exit(2) on 0/low-trade scalar runs
    # (FINAL_BROKEN_RATE / EARLY_ABORT_LOW_RATE). SystemExit is not an Exception,
    # so it killed this process before the PARITY print -> the saturator recorded
    # "no_parity_line" (rc=2). Report it as a FAIL verdict instead — an honest
    # invalid leg, never a silent gap. Harness-only: no engine behavior change.
    except BaseException as e:
        import traceback
        live = {"valid": False, "invalid_reason": f"live {type(e).__name__}: {e}"[:150], "gain_pct": 0, "trades": 0, "trace": traceback.format_exc()[:1500]}
    ok, reason = parity_ok(live, vec)
    # USER 2026-10-06: every sheet trade must be performed live (or filtered correctly) — aggregate counts are not enough.
    # Trade-by-trade match of the SAME two legs (no extra run); a trade-level FAIL fails the parity verdict -> golive NEG_BLOCK.
    try:
        import v15_trade_parity as TP
        _il = not str(symside).upper().endswith("_SHORT")
        _vt = TP.round_trips(TP.vec_events(vec), _il)
        _lt = TP.round_trips(TP.live_events(live, "LONG" if _il else "SHORT")[0], _il)
        _tm = TP.match(_vt, _lt, float(os.environ.get("V15_TRADE_PARITY_TOL_MIN", "30")) * 60.0)
        _ts = TP.summarize(_vt, _lt, _tm, float(os.environ.get("V15_TRADE_PARITY_MIN_MATCH", "0.80")))
        print(f"TRADE_PARITY {symside} {_ts['verdict']} vec_trips={_ts['vec_trips']} live_trips={_ts['live_trips']} matched={_ts['matched']} vec_rate={_ts['vec_match_rate']} live_rate={_ts['live_match_rate']} exit_agree={_ts['exit_agree_rate']} top_gaps={list(_ts['gaps'].items())[:4]}", flush=True)
        _tdir = ROOT / "data" / "reports" / "trade_parity"
        _tdir.mkdir(parents=True, exist_ok=True)
        (_tdir / f"{symside}_trade_parity.json").write_text(json.dumps({**_ts, "symside": symside, "window_days": window_days, "engine_md5": TP.engine_md5(), "override_issues": live.get("override_issues") or [], "source": "v15_parity_check", "vec_only": _tm["vec_only"][:400], "live_only": _tm["live_only"][:400], "exit_diffs": [x for x in _tm["pairs"] if not x["exit_agree"]][:400]}, default=str, indent=1))
        if ok and _ts["verdict"] != "PASS" and os.environ.get("V15_TRADE_PARITY_GATE", "1") == "1" and live.get("execution_ledger") is not None:
            ok, reason = False, f"trade-level parity FAIL (matched {_ts['matched']} of vec {_ts['vec_trips']} / live {_ts['live_trips']}, need >= {os.environ.get('V15_TRADE_PARITY_MIN_MATCH', '0.80')} both ways)"
    except Exception as _tpe:
        print(f"TRADE_PARITY {symside} UNAVAILABLE :: {str(_tpe)[:120]}", flush=True)
    print(f"PARITY {symside} {'PASS' if ok else 'FAIL'} :: {reason} :: "
          f"vec_gain={vec.get('gain_pct')} vec_trades={vec.get('trades')} "
          f"live_gain={live.get('gain_pct')} live_trades={live.get('trades')} "
          f"ov_keys={len(ov)}", flush=True)
    vn, vs = _ledger_span(vec.get("ledger") or vec.get("execution_ledger"))
    ln, ls = _ledger_span(live.get("ledger"))
    print(f"WINDOW {symside} vec_n={vn} vec_span_d={vs if vs is None else f'{vs:.2f}'} "
          f"live_n={ln} live_span_d={ls if ls is None else f'{ls:.2f}'} window_days={window_days}", flush=True)
    if not ok and live.get("trace"):
        print(live["trace"], flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--progress")
    args = ap.parse_args()
    global PROGRESS_OVERRIDE
    PROGRESS_OVERRIDE = args.progress
    try:
        check(args.sym_side, args.window_days)
    except Exception as e:
        # always emit a parseable PARITY line so the saturator records a real reason, never a silent gap
        print(f"PARITY {args.sym_side} FAIL :: harness-error {str(e)[:100]} :: "
              f"vec_gain=NA vec_trades=NA live_gain=NA live_trades=NA ov_keys=0", flush=True)


if __name__ == "__main__":
    main()

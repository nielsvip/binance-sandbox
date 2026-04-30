#!/usr/bin/env python3
"""Coordinate descent from seed best (+1.5472).
Hold 5 knobs fixed, sweep 1 at a time. ~30 configs total.
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from v8_quick_engine import QuickConfig, load_npz, simulate

SYMS_48 = json.load(open(BASE / "backtest_48_symbols.json"))
START = "2022-01-01"
NPZ_DIR = str(BASE / "backtest_v8" / "indicators")

# Seed best from prior run
CENTER = {
    "ENTRY_SCORE_THRESHOLD":    0.0,
    "HTF_MIN_ALIGNED":          1,
    "CT_WT_VELOCITY_1H_MIN":    2.5,
    "REENTRY_RALLY_K15M_MAX":   50.0,
    "RANK_CONVICTION_MIN":      3,
    "WINNER_PROTECT_GAIN_PCT":  1.5,
}

# Per-knob neighborhoods (center + variations)
AXES = {
    "ENTRY_SCORE_THRESHOLD":    [0.0, 5.0, 10.0, 15.0, 20.0, 25.0],
    "HTF_MIN_ALIGNED":          [1, 2, 3],
    "CT_WT_VELOCITY_1H_MIN":    [1.5, 2.0, 2.5, 3.0, 3.5],
    "REENTRY_RALLY_K15M_MAX":   [30.0, 40.0, 50.0, 60.0, 70.0, 80.0],
    "RANK_CONVICTION_MIN":      [1, 2, 3],
    "WINNER_PROTECT_GAIN_PCT":  [1.0, 1.5, 2.0, 2.5, 3.0],
}


def run(overrides, stores):
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return simulate(stores, cfg, 10000.0)


def main():
    print(f"Loading 48 NPZ …")
    t0 = time.time()
    stores = load_npz("crypto", SYMS_48, START, NPZ_DIR)
    print(f"  {len(stores)} symbols in {time.time()-t0:.1f}s\n")

    # Center measurement
    r_center = run(CENTER, stores)
    print(f"CENTER: sharpe={r_center['sharpe']:+.4f}  trades={r_center['trades']}  WR={r_center['wr']:.1f}%  avg={r_center['avg_pnl_pct']:+.3f}%")
    print(f"        {CENTER}\n")

    all_results = []
    best = {"sharpe": r_center["sharpe"], "cfg": dict(CENTER), "trades": r_center["trades"], "wr": r_center["wr"], "avg": r_center["avg_pnl_pct"]}

    for axis, values in AXES.items():
        print(f"\n--- Sweeping {axis} ---")
        axis_results = []
        for v in values:
            ov = dict(CENTER)
            ov[axis] = v
            r = run(ov, stores)
            axis_results.append({"value": v, "result": r})
            flag = ""
            is_center = v == CENTER[axis]
            if r["sharpe"] > best["sharpe"] and r["trades"] >= 10:
                best = {"sharpe": r["sharpe"], "cfg": dict(ov), "trades": r["trades"], "wr": r["wr"], "avg": r["avg_pnl_pct"]}
                flag = " ★"
            center_tag = " (center)" if is_center else ""
            print(f"  {axis}={v!r:8s}  sharpe={r['sharpe']:+.4f}  tr={r['trades']:>5d}  WR={r['wr']:4.1f}%  avg={r['avg_pnl_pct']:+.3f}%{center_tag}{flag}")
            all_results.append({"axis": axis, "value": v, "cfg": dict(ov), "result": r})

    print("\n" + "=" * 80)
    print(f"BEST: sharpe={best['sharpe']:+.4f}  trades={best['trades']}  WR={best['wr']:.1f}%  avg={best['avg']:+.3f}%")
    print(f"  {best['cfg']}")

    out = BASE / "data" / "sweep_results" / f"coord_descent_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"center": CENTER, "center_result": r_center, "results": all_results, "best": best}, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Chapter-A threshold search. Chapter A killed ALL trades at default settings
(ENTRY_SCORE=24, K3M=30, HTF_MIN_ALIGNED=3). Sweep each knob down to find the
edge where A starts producing trades, then check combos.
"""
import itertools
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

A_CORE = {
    "REENTRY_B15_STRONG_TREND_ENABLED": True,
    "REENTRY_B04_DC_RETEST_ENABLED":    True,
    "REENTRY_B11_DC_BREAK_ENABLED":     True,
    "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
    "REENTRY_B10_STOCH_REV_ENABLED":    False,
    "REENTRY_B12_WT_MOM_ENABLED":       False,
    "REENTRY_B14_HA_TREND_ENABLED":     False,
}

# Sweep the 3 thresholds coarsely → find working edge
GRID_ENTRY_SCORE = [24.0, 18.0, 15.0, 12.0, 0.0]   # 0 = disabled
GRID_K3M_FLOOR   = [30.0, 25.0, 20.0, 15.0, 0.0]   # 0 = disabled
GRID_HTF_MIN     = [3, 2, 1]


def run(overrides, stores):
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    r = simulate(stores, cfg, 10000.0)
    return r


def main():
    print(f"Loading 48 crypto NPZ …")
    t0 = time.time()
    stores = load_npz("crypto", SYMS_48, START, NPZ_DIR)
    print(f"  {len(stores)} symbols in {time.time()-t0:.1f}s\n")

    # Baseline F for comparison
    r_f = run({}, stores)
    print(f"F baseline     sharpe={r_f['sharpe']:+.4f}  trades={r_f['trades']}  WR={r_f['wr']:.1f}%  avg={r_f['avg_pnl_pct']:+.3f}%\n")

    # Fixed: A_CORE reentry-block bundle. Vary 3 thresholds.
    print(f"{'ES':>5s} {'K3M':>5s} {'HTF':>3s}   {'sharpe':>8s}  {'trades':>7s}  {'WR':>5s}  {'avg%':>8s}")
    print("-" * 60)

    best = {"sharpe": -999, "cfg": None}
    results = []
    combos = list(itertools.product(GRID_ENTRY_SCORE, GRID_K3M_FLOOR, GRID_HTF_MIN))
    for es, k3m, htf in combos:
        ov = {**A_CORE,
              "ENTRY_SCORE_THRESHOLD": es,
              "K3M_FLOOR":             k3m,
              "HTF_MIN_ALIGNED":       htf}
        r = run(ov, stores)
        r["cfg"] = {"ES": es, "K3M": k3m, "HTF": htf}
        results.append(r)
        flag = ""
        if r["sharpe"] > best["sharpe"]:
            best = {"sharpe": r["sharpe"], "cfg": r["cfg"], "trades": r["trades"], "wr": r["wr"]}
            flag = " ★"
        print(f"{es:>5.0f} {k3m:>5.0f} {htf:>3d}   {r['sharpe']:+.4f}  {r['trades']:>6d}  {r['wr']:>4.1f}%  {r['avg_pnl_pct']:+.3f}%{flag}")

    print(f"\nBEST A-config: {best}")
    print(f"  vs F baseline: Δsharpe={best['sharpe'] - r_f['sharpe']:+.4f}")

    out = BASE / "data" / "sweep_results" / f"chapter_a_search_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"baseline_F": r_f, "results": results, "best": best}, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()

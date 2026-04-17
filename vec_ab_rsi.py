#!/usr/bin/env python3
"""A/B test RSI threshold variants on FULL tradier dataset (109 symbols × ~20k bars).

Tests the same validated LONG combo template with different RSI_15m and RSI_1h
thresholds. Reports per-trade Sharpe / WR / mean for each, to decide whether
flipping RSI_ENTRY_LONG_TRADIER 42 → 35 (or another value) is supported.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import build_tradier_conditions, fwd_returns, score
from vec_validate import load_full


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=20000)
    ap.add_argument("--min-bars", type=int, default=15000)
    ap.add_argument("--min-trades", type=int, default=500)
    ap.add_argument("--npz-dir", default="/home/niels/binance-sandbox/backtest_v4_tradier/indicators")
    args = ap.parse_args()

    t0 = time.time()
    loaded, n_bars = load_full("tradier", args.npz_dir, args.bars, args.min_bars)
    print(f"[{time.time()-t0:.1f}s] Loaded {len(loaded)} symbols × {n_bars} bars")
    C, close = build_tradier_conditions(loaded)
    HORIZONS = [128, 192, 256, 384, 512]
    fwd = fwd_returns(close, HORIZONS)
    print(f"[{time.time()-t0:.1f}s] Built {len(C)} conditions + {len(fwd)} forward-return horizons")

    # Test matrix: same winning template, vary only RSI thresholds
    # Winning template: mom_dcpos_gt50 + rsi15_lt{X} + rsi1h_lt{Y} + wt_2of3
    # Also test: rsi1h_lt{Y} + sma200up_D + wt_2of3 + wt_D (2nd winning pattern)
    rsi15_thrs = [22, 25, 28, 30, 32, 35, 38, 40, 42, 45]
    rsi1h_thrs = [22, 25, 28, 30, 35, 40, 42, 45, 50]

    tests = []
    # Template A: momentum-continuation + RSI pullback
    for x in rsi15_thrs:
        for y in rsi1h_thrs:
            keys = [f"L_mom_dcpos_gt50", f"L_rsi15_lt{x}", f"L_rsi1h_lt{y}", "L_wt_2of3"]
            if all(k in C for k in keys):
                tests.append((f"MOM_rsi15_lt{x}_rsi1h_lt{y}", keys))
    # Template B: mean-reversion + trend
    for y in rsi1h_thrs:
        keys = [f"L_rsi1h_lt{y}", "L_sma200up_D", "L_wt_2of3", "L_wt_D"]
        if all(k in C for k in keys):
            tests.append((f"MR_rsi1h_lt{y}", keys))

    print(f"[{time.time()-t0:.1f}s] A/B matrix: {len(tests)} configs × {len(HORIZONS)} horizons = {len(tests)*len(HORIZONS)} tests")

    results = []
    for name, keys in tests:
        mask = np.ones_like(C[keys[0]])
        for k in keys:
            mask &= C[k]
        for h in HORIZONS:
            m = score(mask, fwd[h], args.min_trades)
            if m is None:
                continue
            results.append({"name": name, "h": h, **m})

    # Sort by sharpe desc
    results.sort(key=lambda r: r["sharpe"], reverse=True)
    print(f"\n=== TOP 40 A/B RESULTS (full 109-sym × 3yr, min_trades={args.min_trades}) ===")
    print(f"{'H':>4} {'Sharpe':>7} {'WR%':>6} {'Mean%':>7} {'N':>6} {'PF':>6}  Template")
    for r in results[:40]:
        print(f"{r['h']:>4d} {r['sharpe']:>7.3f} {r['wr']:>6.1f} {r['mean']*100:>7.3f} {r['n']:>6d} {r['pf']:>6.2f}  {r['name']}")

    # Aggregate: best Sharpe per rsi15 threshold
    print(f"\n=== BEST PER rsi15 THRESHOLD (MOM template only) ===")
    by_rsi15 = {}
    for r in results:
        if not r["name"].startswith("MOM_"):
            continue
        # parse rsi15 threshold from name "MOM_rsi15_lt{X}_rsi1h_lt{Y}"
        x = int(r["name"].split("rsi15_lt")[1].split("_")[0])
        if x not in by_rsi15 or r["sharpe"] > by_rsi15[x]["sharpe"]:
            by_rsi15[x] = r
    for x in sorted(by_rsi15.keys()):
        r = by_rsi15[x]
        print(f"  rsi15<{x:>2}  best Sharpe={r['sharpe']:.3f} WR={r['wr']:.1f} Mean={r['mean']*100:.3f}% N={r['n']} H={r['h']}  {r['name']}")

    print(f"\n=== BEST PER rsi1h THRESHOLD (MR template only) ===")
    by_rsi1h = {}
    for r in results:
        if not r["name"].startswith("MR_"):
            continue
        y = int(r["name"].split("rsi1h_lt")[1])
        if y not in by_rsi1h or r["sharpe"] > by_rsi1h[y]["sharpe"]:
            by_rsi1h[y] = r
    for y in sorted(by_rsi1h.keys()):
        r = by_rsi1h[y]
        print(f"  rsi1h<{y:>2}  best Sharpe={r['sharpe']:.3f} WR={r['wr']:.1f} Mean={r['mean']*100:.3f}% N={r['n']} H={r['h']}  {r['name']}")


if __name__ == "__main__":
    main()

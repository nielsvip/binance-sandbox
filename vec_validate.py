#!/usr/bin/env python3
"""Validate top sweep winners on full dataset (all symbols, full history).
Loads all NPZ in the specified indicator dir and evaluates a curated list
of condition combos at multiple horizons.
"""
import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import (
    build_crypto_conditions,
    build_tradier_conditions,
    fwd_returns,
    score,
    stack_field,
    stack_bool,
)

HORIZONS = [4, 8, 16, 32, 64, 128, 256]


TRADIER_TOP_COMBOS = [
    ("mom_above_sma5pct", "mom_dcpos_gt50", "rsi15_lt40", "wt_4h"),
    ("mom_dcpos_gt50", "rsi15_lt40", "wt_4h", "wt_D"),
    ("mom_above_sma5pct", "mom_dcpos_gt50", "rsi15_lt40", "wt_D"),
    ("mom_above_sma5pct", "mom_wt_all3", "rsi15_lt40", "sma200up_1h"),
    ("dcpos_lt30", "k5_lt40", "mom_dc_x1h", "mom_wt_all3"),
    ("dcpos_lt30", "mom_dc_x1h", "mom_wt_all3", "wt_1h"),
    ("dcpos_lt30", "mom_dc_x1h", "wt_1h", "wt_D"),
    ("dcpos_lt30", "k15_lt40", "mom_dc_x1h", "mom_wt_all3"),
    ("mfi15_lt40", "mom_dc_x1h", "rsi5_lt30", "sma200up_1h"),
    ("mom_dc_x1h", "rsi15_lt40", "wt_1h", "wt_D"),
    ("mom_dcpos_gt50", "rsi15_lt40", "sma200up_D", "wt_4h"),
]


CRYPTO_TOP_COMBOS = [
    # Pick=5 winners from vec_mass_crypto 2026-04-16 (incl. RSI — honest val)
    ("dc_x1h", "k15_lt40", "mfi15_lt40", "rsi15_lt35", "sma200up"),
    ("dc_x1h", "k15_lt30", "k15_lt40", "rsi15_lt35", "sma200up"),
    ("dc_x1h", "mfi15_lt30", "mfi15_lt40", "rsi15_lt35", "sma200up"),
    ("dc_x1h", "dcpos_lt30", "mfi15_lt40", "wt_1h", "wt_4h"),
    ("dc_x1h", "dcpos_lt30", "k15_lt40", "mfi1h_lt40", "sma200up"),
    ("dc_x1h", "k15_lt40", "mom_wt_all3", "sma200up", "wt_1h"),
    ("dc_x1h", "k15_lt40", "mom_wt_all3", "sma200up", "wt_4h"),
    ("dc_x1h", "dcpos_lt30", "k15_lt30", "wt_1h", "wt_4h"),
    ("dc_x1h", "mfi15_lt40", "mom_wt_all3", "wt_1h", "wt_4h"),
    ("dc_x1h", "mfi15_lt40", "mom_wt_all3", "wt_4h", "wt_D"),
    # Pick=4 winners (no RSI, MFI-only flow — aligns with memory rule)
    ("dc_x1h", "dcpos_lt30", "mfi1h_lt40", "sma200up"),
    ("dc_x1h", "dcpos_lt30", "wt_1h", "wt_4h"),
    ("dc_x1h", "dcpos_lt30", "wt_1h", "wt_D"),
    ("dc_x1h", "mfi15_lt40", "mom_wt_all3", "wt_1h"),
    ("dc_x1h", "dcpos_lt30", "mfi15_lt40", "wt_1h"),
    ("dc_x1h", "dcpos_lt30", "k15_lt30", "mfi1h_lt40"),
]


def load_full(mode, npz_dir, max_bars=None, min_bars=5000):
    p = Path(npz_dir)
    files = sorted(p.glob("*.npz"))
    loaded = {}
    skipped_short = 0
    for f in files:
        z = dict(np.load(str(f), allow_pickle=True))
        if "close" not in z:
            continue
        n = len(z["close"])
        if n < min_bars:
            skipped_short += 1
            continue
        loaded[f.stem] = z
    if not loaded:
        return loaded, 0
    min_len = min(len(z["close"]) for z in loaded.values())
    if max_bars:
        min_len = min(min_len, max_bars)
    for s in list(loaded.keys()):
        z = loaded[s]
        for k in list(z.keys()):
            a = np.asarray(z[k])
            if a.ndim == 1 and len(a) >= min_len:
                z[k] = a[-min_len:]
    print(f"  Skipped {skipped_short} symbols with <{min_bars} bars history")
    return loaded, min_len


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--bars", type=int, default=20000)
    ap.add_argument("--min-trades", type=int, default=200)
    ap.add_argument("--min-bars", type=int, default=15000, help="Min bar history to include a symbol")
    ap.add_argument("--npz-dir", default="")
    args = ap.parse_args()

    if not args.npz_dir:
        for base in ("/home/niels/binance-sandbox", str(Path(__file__).resolve().parent)):
            sub = "backtest_v4_tradier" if args.mode == "tradier" else "backtest_v8"
            d = Path(base) / sub / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                args.npz_dir = str(d)
                break
    print(f"NPZ dir: {args.npz_dir}")

    t0 = time.time()
    loaded, n_bars = load_full(args.mode, args.npz_dir, args.bars, args.min_bars)
    print(f"[{time.time()-t0:.1f}s] Loaded {len(loaded)} symbols, {n_bars} bars each")

    if args.mode == "crypto":
        C, close = build_crypto_conditions(loaded)
        combos = CRYPTO_TOP_COMBOS
    else:
        C, close = build_tradier_conditions(loaded)
        combos = TRADIER_TOP_COMBOS

    fwd = fwd_returns(close, HORIZONS)
    print(f"[{time.time()-t0:.1f}s] Built {len(C)} conditions, fwd for {list(fwd.keys())}")

    results = []
    for combo in combos:
        prefix = "L"
        keys = [f"{prefix}_{k}" for k in combo]
        missing = [k for k in keys if k not in C]
        if missing:
            print(f"SKIP combo (missing): {missing}")
            continue
        mask = np.ones_like(C[keys[0]])
        for k in keys:
            mask &= C[k]
        for h, ret in fwd.items():
            m = score(mask, ret, args.min_trades)
            if m is None:
                continue
            results.append({"combo": "+".join(combo), "horizon": h, **m})

    results.sort(key=lambda r: r["sharpe"], reverse=True)

    out = Path(__file__).parent / "data" / "sweep_results" / f"vec_validate_{args.mode}_{int(time.time())}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["combo", "horizon", "n_trades", "sharpe", "wr", "mean_ret", "std", "pf"])
        for r in results:
            w.writerow([r["combo"], r["horizon"], r["n"], f"{r['sharpe']:.4f}", f"{r['wr']:.2f}", f"{r['mean']:.5f}", f"{r['std']:.5f}", f"{r['pf']:.3f}"])

    print(f"\n=== FULL DATASET VALIDATION ({args.mode}, {len(loaded)} sym, {n_bars} bars, min_trades={args.min_trades}) ===")
    print(f"{'Horizon':>7} {'Sharpe':>8} {'WR%':>6} {'Mean%':>7} {'N':>7} {'PF':>7}  Combo")
    for r in results[:40]:
        print(f"{r['horizon']:>7d} {r['sharpe']:>8.3f} {r['wr']:>6.1f} {r['mean']*100:>7.3f} {r['n']:>7d} {r['pf']:>7.2f}  {r['combo']}")
    print(f"\nWrote {len(results)} rows to {out}")


if __name__ == "__main__":
    main()

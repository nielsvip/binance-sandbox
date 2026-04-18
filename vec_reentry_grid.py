#!/usr/bin/env python3
"""Reentry parameter grid — sweeps v8_quick_engine QuickConfig variations on 48-sym 4yr pool.

Two modes:
  --mode ortho  : sweep one parameter at a time from baseline (fast — ~70 configs × 20s = ~25 min)
  --mode pair   : sweep promising pairs (top-N params from ortho) × full grid on pair
  --mode random : Latin Hypercube sample of N random configs (broad exploration)

Scores by composite: sharpe × log(1+trades) - 0.1*pool_dd. Captures "sharpe AND more trades".
"""
import argparse
import csv
import itertools
import os
import random
import sys
import time
import math
from copy import copy
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER


# Baseline = user's 2.25-Sharpe config
BASELINE = {
    "CT_WT_VELOCITY_GATE_ENABLED": True,
    "CT_WT_VELOCITY_1H_MIN": 6.0,
    "REENTRY_RALLY_K15M_MAX": 40.0,
    "REENTRY_MIN_GAP_BARS": 5,
    "REENTRY_SYMGATE_ENABLED": True,
    "REENTRY_SYMGATE_SPEED_MIN": 0.5,
    "ENTRY_SYMGATE_ENABLED": True,
    "STRENGTH_MIN_SCORE": 5.0,
    "HTF_MIN_ALIGNED": 1,
    "D_TREND_REQUIRED": True,
    "RANK_CONVICTION_ENABLED": True,
    "RANK_CONVICTION_MIN": 3,
    "WINNER_PROTECT_ENABLED": True,
    "WINNER_PROTECT_GAIN_PCT": 1.0,
    "K3M_FLOOR": 30.0,
    "MIN_HOLD_BARS": 10,
    "COOLDOWN_BARS": 3,
    "WT_EXIT_MIN_TFS": 2,
    "REENTRY_RALLY_HTF_MIN": 2,
    "DELTA_ENGINE_ENABLED": True,
    "DELTA_ENTRY_ENABLED": True,
    "RZ_EXIT_ENABLED": True,
    "SATOSHIT_ENABLED": True,
    "STRUCTURAL_RANGE_SHIFT_EXIT": True,
}

# Dimensions to sweep. Key = param name, value = list of candidate values.
GRID = {
    # Reentry gates (high priority for user's goal)
    "REENTRY_RALLY_K15M_MAX":       [25, 30, 35, 40, 50, 60, 70, 80, 90, 100],
    "REENTRY_MIN_GAP_BARS":         [1, 2, 3, 5, 8, 12, 20, 30],
    "REENTRY_SYMGATE_SPEED_MIN":    [0.0, 0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0],
    "REENTRY_RALLY_HTF_MIN":        [0, 1, 2, 3],
    "REENTRY_SYMGATE_ENABLED":      [True, False],
    # Entry gates (affect both fresh entries and reentries post-cooldown)
    "CT_WT_VELOCITY_1H_MIN":        [0.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0],
    "STRENGTH_MIN_SCORE":           [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0],
    "HTF_MIN_ALIGNED":              [0, 1, 2, 3],
    "RANK_CONVICTION_MIN":          [1, 2, 3],
    "K3M_FLOOR":                    [10, 15, 20, 25, 30, 35, 40, 50],
    "ENTRY_SYMGATE_ENABLED":        [True, False],
    # Holding + exit timing
    "MIN_HOLD_BARS":                [1, 3, 5, 8, 10, 15, 20, 30, 50],
    "COOLDOWN_BARS":                [0, 1, 3, 5, 10, 20, 40],
    "WT_EXIT_MIN_TFS":              [1, 2, 3, 4],
    "WINNER_PROTECT_GAIN_PCT":      [0.5, 1.0, 1.5, 2.0, 3.0],
}


def apply_overrides(cfg, overrides):
    c = cfg  # QuickConfig mutable
    for k, v in overrides.items():
        if hasattr(c, k):
            cur = getattr(c, k)
            if isinstance(cur, bool):
                setattr(c, k, bool(v))
            elif isinstance(cur, int) and not isinstance(cur, bool):
                setattr(c, k, int(v))
            elif isinstance(cur, float):
                setattr(c, k, float(v))
            else:
                setattr(c, k, v)
    return c


def fresh_cfg(mode, overrides):
    cfg = QuickConfig.from_override_file(os.environ.get("V8_OVERRIDE_FILE", ""))
    if mode == "tradier":
        cfg.apply_tradier_defaults()
    apply_overrides(cfg, BASELINE)
    apply_overrides(cfg, overrides)
    return cfg


def composite_score(sharpe, trades, dd=0.0):
    """Score favoring BOTH high Sharpe AND more trades. User directive."""
    if trades <= 0:
        return -99.0
    return float(sharpe) * math.log(1 + trades) * 0.3 - 0.05 * float(dd)


def run_one(mode, stores, cfg, capital, label):
    t0 = time.time()
    r = simulate(stores, cfg, capital)
    r["elapsed"] = round(time.time() - t0, 1)
    r["label"] = label
    r["composite"] = composite_score(r.get("sharpe", 0), r.get("trades", 0))
    return r


def mode_ortho(mode, stores, capital, grid, results):
    """Sweep one parameter at a time from baseline."""
    for param, values in grid.items():
        base_val = BASELINE.get(param, None)
        for v in values:
            if v == base_val:
                continue
            cfg = fresh_cfg(mode, {param: v})
            r = run_one(mode, stores, cfg, capital, f"{param}={v}")
            r["param"] = param; r["value"] = v
            results.append(r)
            print(f"  {param:<32}={str(v):<8} Sharpe={r.get('sharpe',0):.3f} N={r.get('trades',0):>4} WR={r.get('wr',0):.1f} Mean={r.get('avg_pnl_pct',0):.3f}%  composite={r['composite']:.3f}  ({r['elapsed']:.1f}s)", flush=True)


def mode_random(mode, stores, capital, grid, results, n_samples):
    """Latin-Hypercube-ish: randomly sample from each param independently."""
    rng = random.Random(42)
    for i in range(n_samples):
        overrides = {}
        for param, values in grid.items():
            overrides[param] = rng.choice(values)
        cfg = fresh_cfg(mode, overrides)
        label = "_".join(f"{k}={v}" for k, v in overrides.items())[:120]
        r = run_one(mode, stores, cfg, capital, label)
        r["overrides"] = overrides
        results.append(r)
        if (i + 1) % 20 == 0 or i < 5:
            print(f"  [{i+1:>4}/{n_samples}] Sharpe={r.get('sharpe',0):.3f} N={r.get('trades',0):>4} WR={r.get('wr',0):.1f} composite={r['composite']:.3f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--sweep", choices=["ortho", "random"], default="ortho",
                    help="ortho=one-param-at-a-time from baseline; random=random sample N configs")
    ap.add_argument("--samples", type=int, default=500, help="for --sweep random")
    ap.add_argument("--symbols", type=str, default="fast",
                    help="'fast' = 11/12 top symbols; or comma-separated list; or '48' for 48-pair list")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--npz-dir", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.symbols == "fast":
        symbols = (FAST_SYMBOLS_CRYPTO if args.mode == "crypto" else FAST_SYMBOLS_TRADIER).split(",")
    elif args.symbols == "48":
        # Try to load first 48 available USDT pairs from NPZ dir
        npz_search_dir = args.npz_dir or "/home/niels/binance-sandbox/backtest_v8/indicators"
        symbols = sorted([p.stem for p in Path(npz_search_dir).glob("*USDT.npz")])[:48]
    else:
        symbols = [s.strip() for s in args.symbols.split(",")]

    npz_dir = args.npz_dir
    if not npz_dir:
        for base in ("/home/niels/binance-sandbox", str(Path(__file__).resolve().parent)):
            d = Path(base) / "backtest_v8" / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                npz_dir = str(d); break

    print(f"vec_reentry_grid | mode={args.mode} sweep={args.sweep} syms={len(symbols)} start={args.start} npz={npz_dir}", flush=True)
    print(f"Loading NPZ for {len(symbols)} symbols...", flush=True)
    t0 = time.time()
    stores = load_npz(args.mode, symbols, args.start, npz_dir)
    print(f"[{time.time()-t0:.1f}s] Loaded {len(stores)} stores", flush=True)

    # Baseline run
    cfg_base = fresh_cfg(args.mode, {})
    print(f"\n=== BASELINE ===", flush=True)
    r_base = run_one(args.mode, stores, cfg_base, args.capital, "BASELINE")
    print(f"  Sharpe={r_base.get('sharpe',0):.3f} N={r_base.get('trades',0)} WR={r_base.get('wr',0):.1f}% Mean={r_base.get('avg_pnl_pct',0):.3f}% composite={r_base['composite']:.3f}", flush=True)

    results = [r_base]
    print(f"\n=== {args.sweep.upper()} SWEEP ===", flush=True)
    if args.sweep == "ortho":
        mode_ortho(args.mode, stores, args.capital, GRID, results)
    else:
        mode_random(args.mode, stores, args.capital, GRID, results, args.samples)

    # Rank
    results.sort(key=lambda r: r.get("composite", -99), reverse=True)

    # Write CSV
    out = Path(args.out) if args.out else Path(__file__).parent / "data" / "sweep_results" / f"vec_reentry_grid_{args.mode}_{args.sweep}_{int(time.time())}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        fields = ["label", "sharpe", "trades", "wr", "avg_pnl_pct", "pnl", "composite", "elapsed"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)

    # Top-30 + bottom-5
    print(f"\n=== TOP 30 by composite ===", flush=True)
    print(f"{'Label':<60} {'Sharpe':>7} {'N':>5} {'WR%':>6} {'Mean%':>7} {'Compo':>7}", flush=True)
    for r in results[:30]:
        print(f"{r['label'][:60]:<60} {r.get('sharpe',0):>7.3f} {r.get('trades',0):>5} {r.get('wr',0):>6.1f} {r.get('avg_pnl_pct',0):>7.3f} {r['composite']:>7.3f}", flush=True)
    print(f"\n=== BASELINE rank ===", flush=True)
    base_rank = next((i for i, r in enumerate(results) if r["label"] == "BASELINE"), -1)
    print(f"  BASELINE is #{base_rank+1}/{len(results)} by composite", flush=True)
    print(f"\nSaved: {out}", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Sector-based stock sweep — ~1000 configs per sector × N sectors.
Uses TF-aware engine (LTF=5m for stocks). Parallelized via multiprocessing fork.

Goal: baseline Sharpe > 2 per sector, identify per-sector winning configs.
"""
import itertools
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

# Fork-based NPZ sharing on Linux (copy-on-write, fast).
if sys.platform.startswith("linux"):
    mp.set_start_method("fork", force=True)

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

START = "2024-01-01"
NPZ_DIR = str(BASE / "backtest_v8" / "indicators")
SECTORS_FILE = BASE / "stocks_sectors.json"

# PARAM GRID — designed for ~1000 combos
# Tuned to stock behaviour: HTF-heavy, 5m LTF, PT and hold as switches, broad velocity range.
GRID = {
    "HTF_MIN_ALIGNED":              [2, 3],             # stocks: 2+ (per CLAUDE.md OPPOSITE)
    "D_TREND_REQUIRED":             [True],             # always on for stocks
    "CT_WT_VELOCITY_1H_MIN":        [0.0, 2.0, 4.0, 8.0],
    "REENTRY_RALLY_K15M_MAX":       [100.0, 50.0],
    "ENTRY_ZONE_K_TF":              ["15m", "1h"],
    "ENTRY_ZONE_LONG":              [0.0, 30.0],
    "ENTRY_ZONE_SHORT":             [100.0, 70.0],
    "STRENGTH_MIN_SCORE":           [3.0, 5.0, 7.0],
    "PROFIT_TARGET_ENABLED":        [False, True],
    "PROFIT_TARGET_PCT":            [0.5, 1.0],
    "MIN_HOLD_BARS":                [5, 10, 20, 40],
}
# 2×1×4×2×2×2×2×3×2×2×4 = 3,072 raw → ~1.5k after PT pruning.

_STORES = None  # inherited by fork workers


def build_configs():
    """Build a ~1000-config grid with pruning to avoid combinatorial explosion."""
    keys = list(GRID.keys())
    out = []
    seen = set()
    for combo in itertools.product(*[GRID[k] for k in keys]):
        cfg = dict(zip(keys, combo))
        # Prune: PT_PCT only matters if PT enabled
        if not cfg["PROFIT_TARGET_ENABLED"] and cfg["PROFIT_TARGET_PCT"] != 0.5:
            continue
        # Prune: if zone LONG=0 and zone SHORT=100 the K_TF is irrelevant
        if cfg["ENTRY_ZONE_LONG"] == 0.0 and cfg["ENTRY_ZONE_SHORT"] == 100.0 and cfg["ENTRY_ZONE_K_TF"] != "15m":
            continue
        key = tuple(sorted(cfg.items()))
        if key in seen: continue
        seen.add(key)
        out.append(cfg)
    return out


def run_one(args):
    from v8_quick_engine import QuickConfig, simulate
    overrides, = args
    cfg = QuickConfig()
    cfg.apply_tradier_defaults()  # LTF=5m, K_ZONE+MFI+VWAP+FH_MOM, etc.
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    r = simulate(_STORES, cfg, 10000.0)
    r["cfg"] = overrides
    return r


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sector", type=str, required=True, help="sector name from stocks_sectors.json")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="max configs (0 = all)")
    args = ap.parse_args()

    global _STORES
    from v8_quick_engine import load_npz

    sectors = json.load(open(SECTORS_FILE))
    syms = sectors.get(args.sector)
    if not syms:
        print(f"Unknown sector: {args.sector}. Available: {[k for k in sectors if not k.startswith('_')]}")
        sys.exit(1)

    print(f"SECTOR SWEEP  sector={args.sector}  symbols={len(syms)}  start={START}")
    print(f"  {syms}\n")
    t0 = time.time()
    _STORES = load_npz("tradier", syms, START, NPZ_DIR)
    print(f"  Loaded {len(_STORES)} symbols in {time.time()-t0:.1f}s\n")

    configs = build_configs()
    if args.limit > 0:
        configs = configs[:args.limit]
    print(f"Configs to test: {len(configs)}   Workers: {args.workers}\n")

    t_start = time.time()
    results = []
    best = {"sharpe": -999, "cfg": None}

    # Baseline first (no overrides)
    from v8_quick_engine import QuickConfig, simulate
    cfg_b = QuickConfig(); cfg_b.apply_tradier_defaults()
    r_base = simulate(_STORES, cfg_b, 10000.0)
    print(f"BASELINE: sharpe={r_base['sharpe']:+.4f}  trades={r_base['trades']}  WR={r_base['wr']:.1f}%  avg={r_base['avg_pnl_pct']:+.3f}%\n")

    with mp.Pool(args.workers) as pool:
        completed = 0
        for r in pool.imap_unordered(run_one, [(c,) for c in configs], chunksize=4):
            results.append(r)
            completed += 1
            flag = ""
            if r["sharpe"] > best["sharpe"] and r["trades"] >= 20:
                best = {"sharpe": r["sharpe"], "cfg": r["cfg"], "trades": r["trades"],
                        "wr": r["wr"], "avg": r["avg_pnl_pct"]}
                flag = " ★"
            if completed % 50 == 0 or flag or completed == len(configs):
                elapsed = time.time() - t_start
                rate = completed / elapsed if elapsed else 0
                eta = (len(configs) - completed) / rate / 60 if rate else 0
                cfg_str = " ".join(f"{k[:4]}={v}" for k, v in sorted(r["cfg"].items()))[:90]
                print(f"[{completed}/{len(configs)}] sh={r['sharpe']:+.4f} tr={r['trades']} WR={r['wr']:.1f}% best={best['sharpe']:+.4f} ETA={eta:.1f}m {cfg_str}{flag}", flush=True)

    # Ranked top 30 with trades >= 20
    ranked = sorted([r for r in results if r["trades"] >= 20], key=lambda r: -r["sharpe"])
    print("\n" + "=" * 110)
    print(f"BASELINE:  sharpe={r_base['sharpe']:+.4f}  trades={r_base['trades']}  WR={r_base['wr']}%")
    if best["cfg"] is not None:
        print(f"WINNER:    sharpe={best['sharpe']:+.4f}  trades={best['trades']}  WR={best['wr']:.1f}%  avg={best['avg']:+.3f}%")
        print(f"           cfg: {best['cfg']}")
    print(f"\nTOP 30 (trades >= 20):")
    for r in ranked[:30]:
        print(f"  sh={r['sharpe']:+.4f}  tr={r['trades']:>4d}  WR={r['wr']:4.1f}%  avg={r['avg_pnl_pct']:+.3f}%  {r['cfg']}")

    out = BASE / "data" / "sweep_results" / f"sector_sweep_{args.sector}_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"sector": args.sector, "symbols": syms, "baseline": r_base, "results": results, "best": best}, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()

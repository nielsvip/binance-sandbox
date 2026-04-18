#!/usr/bin/env python3
"""
v8_quick_sweep.py — Ultra-fast sweep harness for v8_quick_engine.

Runs thousands of config variants in-process (no subprocess per config).
NPZ data loaded ONCE, shared across all configs → amortized I/O.

Target: 1,000 configs × 48 symbols × 4yr in ~12 hours (vs weeks with scalar V8).

Usage:
  python v8_quick_sweep.py --mode crypto --symbols fast --start 2022-01-01 --workers 8
  python v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier entry_gates
"""
import argparse
import csv
import hashlib
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from v8_quick_engine import (
    QuickConfig, load_npz, simulate,
    FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER
)

BASE_PATH = Path(__file__).resolve().parent


def build_param_grid_entry_gates():
    """Sweep all entry gate combos + key thresholds."""
    grid = {
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "CT_WT_VELOCITY_1H_MIN": [0.0, 0.5, 1.0],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "SATOSHIT_ENABLED": [True, False],
        "DELTA_ENTRY_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "K3M_FLOOR": [20.0, 25.0, 30.0, 35.0],
        "ENTRY_SCORE_THRESHOLD": [15.0, 18.0, 20.0, 24.0],
    }
    return grid


def build_param_grid_exit_tuning():
    """Sweep exit parameter variations."""
    grid = {
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True],
        "SATOSHIT_ENABLED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "STRUCTURAL_RANGE_SHIFT_TF": ["dc_4h", "dc_1h", "bb_1h", "bb_4h"],
        "RZ_EXIT_ENABLED": [True, False],
        "REENTRY_RALLY_K15M_MAX": [60.0, 75.0, 100.0],
        "REENTRY_RALLY_HTF_MIN": [1, 2, 3],
    }
    return grid


def build_param_grid_full():
    """Full combinatorial sweep — use with caution (can be 100K+ configs)."""
    grid = {
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "CT_WT_VELOCITY_1H_MIN": [0.0, 0.5, 1.0, 2.0],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "SATOSHIT_ENABLED": [True, False],
        "DELTA_ENTRY_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "K3M_FLOOR": [20.0, 25.0, 30.0, 35.0],
        "ENTRY_SCORE_THRESHOLD": [12.0, 15.0, 18.0, 20.0, 24.0],
        "REENTRY_RALLY_K15M_MAX": [60.0, 80.0, 100.0],
        "REENTRY_RALLY_HTF_MIN": [1, 2, 3],
    }
    return grid


def build_param_grid_v3_core():
    """V3-core grid: sweeps the knobs that moved V8Q v3 from 0.5 to Sharpe 1.93 on TOP3.
    STRENGTH_MIN_SCORE, MIN_HOLD_BARS, PROFIT_TARGET_PCT, WT_EXIT_MIN_TFS, HTF_MIN_ALIGNED."""
    grid = {
        "STRENGTH_FILTER_ENABLED": [True],
        "STRENGTH_MIN_SCORE": [3.0, 5.0, 7.0, 10.0, 13.0],
        "MIN_HOLD_BARS": [5, 10, 15, 20, 30],
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [1.2, 1.4, 1.6, 1.8, 2.0],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "HTF_MIN_ALIGNED": [1, 2],
        "D_TREND_REQUIRED": [True, False],
        "COOLDOWN_BARS": [3, 6, 12],
        "ENTRY_SCORE_THRESHOLD": [15.0, 18.0, 24.0],
    }
    return grid


def build_param_grid_breakout_multi_lung():
    """D4 BREAKOUT MULTI-LUNG — sweep the multi-TF candle+volume composite.
    Origin: ez_breakout_agent.py orphan. Tests if composite breath beats baseline.
    Baseline rule: accept only Sharpe > 2 per MEMORY."""
    grid = {
        "BREAKOUT_MULTI_LUNG_ENABLED": [True],
        "BREAKOUT_MULTI_LUNG_MODE": ["AUGMENT", "REPLACE"],
        "BREAKOUT_MULTI_LUNG_TIER": ["CRYPTO", "MOVER"],  # tradier_core appends STOCK
        "BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE": [0.10, 0.15, 0.20, 0.25, 0.30],
        "BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE": [-0.20, -0.10, -0.05],
        "BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE": [0.10, 0.15, 0.25],
        "BREAKOUT_MULTI_LUNG_COOLDOWN_BARS": [2, 4, 8],
        # Baseline companions — keep proven v3_core defaults
        "STRENGTH_FILTER_ENABLED": [True],
        "STRENGTH_MIN_SCORE": [5.0],
        "MIN_HOLD_BARS": [10],
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [1.6],
        "WT_EXIT_MIN_TFS": [3],
    }
    return grid


def build_param_grid_breakout_multi_lung_tradier():
    """D4 tradier variant — STOCK tier only, wider PT to match stock volatility."""
    grid = {
        "BREAKOUT_MULTI_LUNG_ENABLED": [True],
        "BREAKOUT_MULTI_LUNG_MODE": ["AUGMENT", "REPLACE"],
        "BREAKOUT_MULTI_LUNG_TIER": ["STOCK"],
        "BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE": [0.10, 0.15, 0.20, 0.25, 0.30],
        "BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE": [-0.20, -0.10, -0.05],
        "BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE": [0.10, 0.15, 0.25],
        "BREAKOUT_MULTI_LUNG_COOLDOWN_BARS": [4, 8, 16],
        "STRENGTH_FILTER_ENABLED": [True],
        "MIN_HOLD_BARS": [20, 40],
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [1.5, 2.5],
        "WT_EXIT_MIN_TFS": [3],
        "ENTRY_SCORE_THRESHOLD": [24.0],
    }
    return grid


def build_param_grid_tradier_core():
    """Tradier-specific grid tuned around K_ZONE + FH_MOM + MFI_D alpha blocks.
    Seed test 2026-04-16: peak Sharpe 0.72 at score=6, pt=2.0, hold=20, wt_exit=3."""
    grid = {
        "STRENGTH_FILTER_ENABLED": [True],
        "STRENGTH_MIN_SCORE": [4.0, 5.0, 6.0, 7.0, 8.0],
        "MIN_HOLD_BARS": [10, 20, 30, 40],
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [1.0, 1.5, 2.0, 2.5, 3.0],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "HTF_MIN_ALIGNED": [2, 3],
        "D_TREND_REQUIRED": [True, False],
        "K_ZONE_LONG_THRESHOLD": [25, 35, 45],
        "MFI_LONG_THRESHOLD_D": [15.0, 20.0, 25.0, 30.0],
        "FH_MOMENTUM_MIN_MOVE_PCT": [0.3, 0.5, 0.7],
    }
    return grid


def build_param_grid_hedge_reentry_overhaul():
    """2026-04-17 user directive — sweep the hedge + reentry overhaul switches.
    Hedge switches live-only (vectorized engine has no hedge simulation); reentry switches sweep.
    Pairs with backtest_v8_engine hedge-capable simulation when available."""
    grid = {
        "HEDGE_EXIT_BYPASS_NOLOSS": [True, False],
        "HEDGE_SAME_SYMBOL_PCT": [0.5, 1.0, 1.5],
        "HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE": [True, False],
        "REENTRY_WT15M_CROSS_ENABLED": [True, False],
        "REENTRY_WT15M_SIZE_MULT": [1.0, 1.5, 2.0],
        "REENTRY_WT15M_K_MAX": [30.0, 50.0, 70.0],
        "REENTRY_WT15M_HTF_FAVOR_REQUIRED": [True, False],
        "REENTRY_K15M_PARTIAL_ENABLED": [True, False],
        "REENTRY_K15M_PARTIAL_THRESHOLD": [70.0, 80.0, 90.0],
        "REENTRY_K15M_PARTIAL_MULT": [0.3, 0.5, 0.7],
        "REENTRY_POST_CONSOL_ENABLED": [True, False],
        "REENTRY_POST_CONSOL_MULT": [1.3, 1.5, 2.0],
        "REENTRY_POST_CONSOL_TFS_REQUIRED": [1, 2, 3],
        "REENTRY2_STOCH_CROSS_ENABLED": [False],
    }
    return grid


def build_param_grid_reentry_sharpe_push():
    """2026-04-17 directive: push Sharpe above 2.25 baseline (48-sym × 4yr, 500 trades, 92.8% WR).
    Cartesian over every reentry tunable. 144 variants × ~8s = ~20 min on v8_quick vectorized engine.
    Only reentry-relevant params — hedge params omitted (no hedge sim in v8_quick)."""
    grid = {
        "REENTRY_WT15M_CROSS_ENABLED": [True],
        "REENTRY_WT15M_SIZE_MULT": [1.0, 1.5, 2.0],
        "REENTRY_WT15M_K_MAX": [30.0, 50.0, 70.0],
        "REENTRY_WT15M_HTF_FAVOR_REQUIRED": [True, False],
        "REENTRY_K15M_PARTIAL_ENABLED": [True],
        "REENTRY_K15M_PARTIAL_THRESHOLD": [80.0, 90.0],
        "REENTRY_K15M_PARTIAL_MULT": [0.5, 1.0],
        "REENTRY_POST_CONSOL_ENABLED": [True],
        "REENTRY_POST_CONSOL_MULT": [1.5, 2.0],
        "REENTRY_POST_CONSOL_TFS_REQUIRED": [2, 3],
    }
    return grid


def build_param_grid_reentry_sharpe_push_wide():
    """Even wider — includes ON/OFF for each block + timing knobs. ~500 variants ~70 min."""
    grid = {
        "REENTRY_WT15M_CROSS_ENABLED": [True, False],
        "REENTRY_WT15M_SIZE_MULT": [1.0, 1.5, 2.0],
        "REENTRY_WT15M_K_MAX": [30.0, 50.0, 70.0, 100.0],
        "REENTRY_K15M_PARTIAL_ENABLED": [True, False],
        "REENTRY_K15M_PARTIAL_THRESHOLD": [70.0, 90.0],
        "REENTRY_K15M_PARTIAL_MULT": [0.3, 0.5, 1.0],
        "REENTRY_POST_CONSOL_ENABLED": [True, False],
        "REENTRY_POST_CONSOL_MULT": [1.2, 1.5, 2.0, 2.5],
        "REENTRY_POST_CONSOL_TFS_REQUIRED": [1, 2, 3],
    }
    return grid


TIER_MAP = {
    "entry_gates": build_param_grid_entry_gates,
    "exit_tuning": build_param_grid_exit_tuning,
    "full": build_param_grid_full,
    "v3_core": build_param_grid_v3_core,
    "tradier_core": build_param_grid_tradier_core,
    "breakout_multi_lung": build_param_grid_breakout_multi_lung,
    "breakout_multi_lung_tradier": build_param_grid_breakout_multi_lung_tradier,
    "hedge_reentry_overhaul": build_param_grid_hedge_reentry_overhaul,
    "reentry_sharpe_push": build_param_grid_reentry_sharpe_push,
    "reentry_sharpe_push_wide": build_param_grid_reentry_sharpe_push_wide,
}


def grid_to_configs(grid: dict) -> list:
    """Expand parameter grid to list of config dicts."""
    keys = sorted(grid.keys())
    values = [grid[k] for k in keys]
    configs = []
    for combo in itertools.product(*values):
        cfg = dict(zip(keys, combo))
        configs.append(cfg)
    return configs


def config_hash(cfg: dict) -> str:
    return hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:8]


def run_one_config(args_tuple):
    """Worker function — runs one config on pre-loaded NPZ stores."""
    npz_dir, mode, symbols_list, start_date, cfg_dict, run_id = args_tuple
    stores = load_npz(mode, symbols_list, start_date, npz_dir)
    if not stores:
        return {"run_id": run_id, "sharpe": 0, "pnl": 0, "trades": 0, "wins": 0, "losses": 0, "status": "no_data", "config": cfg_dict}
    cfg = QuickConfig()
    cfg.MODE = mode
    if mode == "tradier":
        cfg.apply_tradier_defaults()
    for k, v in cfg_dict.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    if mode == "tradier" and cfg.STRUCTURAL_RANGE_SHIFT_TF == "dc_4h":
        cfg.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
    t0 = time.time()
    result = simulate(stores, cfg, 10000.0)
    elapsed = time.time() - t0
    result["run_id"] = run_id
    result["elapsed"] = round(elapsed, 1)
    # Status taxonomy: "useless" (early-aborted as Sharpe<floor), "no_trades", or "ok"
    if result.get("early_abort"):
        result["status"] = "useless"
    elif result["trades"] > 0:
        result["status"] = "ok"
    else:
        result["status"] = "no_trades"
    result["config"] = cfg_dict
    return result


def main():
    parser = argparse.ArgumentParser(description="V8 Quick Sweep — ultra-fast parameter search")
    parser.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    parser.add_argument("--symbols", type=str, default="fast")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tier", type=str, default="entry_gates", choices=list(TIER_MAP.keys()))
    parser.add_argument("--npz-dir", type=str, default="")
    parser.add_argument("--limit", type=int, default=0, help="Max configs to run (0=all)")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--kill-sharpe", type=float, default=0.5,
                        help="Abort sweep if best Sharpe stays below this after --kill-warmup configs (default 0.5)")
    parser.add_argument("--kill-warmup", type=int, default=200,
                        help="Configs to run before kill-rule applies (default 200)")
    args = parser.parse_args()

    symbols_list = None
    if args.symbols == "fast":
        symbols_list = (FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO).split(",")
    elif args.symbols:
        symbols_list = [s.strip() for s in args.symbols.split(",") if s.strip()]

    npz_dir = args.npz_dir
    if not npz_dir:
        for prefix in ["backtest_v8", "backtest_v7"]:
            d = BASE_PATH / prefix / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                npz_dir = str(d)
                break

    grid = TIER_MAP[args.tier]()
    configs = grid_to_configs(grid)
    if args.limit > 0:
        configs = configs[:args.limit]

    syms_tag = f"_{len(symbols_list)}sym" if symbols_list else ""
    csv_name = f"v8_quick_{args.mode}_{args.tier}{syms_tag}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    csv_path = BASE_PATH / "data" / "sweep_results" / csv_name
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    done_hashes = set()
    if args.resume and csv_path.exists():
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                # resume skips anything with a terminal status: ok, useless, no_trades
                # (prior runs with blank/error status will re-run)
                if row.get("status") in ("ok", "useless", "no_trades"):
                    done_hashes.add(row.get("config_hash", ""))

    todo = []
    for i, cfg_dict in enumerate(configs):
        h = config_hash(cfg_dict)
        if h in done_hashes:
            continue
        todo.append((npz_dir, args.mode, symbols_list, args.start, cfg_dict, f"q_{args.tier}_{i:05d}"))

    total = len(configs)
    skip = total - len(todo)
    print(f"\nV8 Quick Sweep: {args.mode} | tier={args.tier} | {total} configs ({skip} done, {len(todo)} todo) | workers={args.workers}")
    print(f"CSV: {csv_path}\n")

    if not todo:
        print("All configs done.")
        return

    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with open(csv_path, "a", newline="") as csvfile:
        fieldnames = ["run_id", "config_hash", "sharpe", "pnl", "trades", "wins", "losses", "wr", "avg_pnl_pct", "elapsed", "status", "early_abort", "symbols_used"]
        cfg_keys = sorted(configs[0].keys())
        for k in cfg_keys:
            fieldnames.append(f"cfg_{k}")
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        completed = 0
        best_sharpe = -999
        t_start = time.time()

        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(run_one_config, t): t for t in todo}
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as e:
                    print(f"  ERROR: {e}")
                    continue
                completed += 1
                cfg_dict = result.get("config", {})
                row = {
                    "run_id": result["run_id"],
                    "config_hash": config_hash(cfg_dict),
                    "sharpe": result.get("sharpe", 0),
                    "pnl": result.get("pnl", 0),
                    "trades": result.get("trades", 0),
                    "wins": result.get("wins", 0),
                    "losses": result.get("losses", 0),
                    "wr": result.get("wr", 0),
                    "avg_pnl_pct": result.get("avg_pnl_pct", 0),
                    "elapsed": result.get("elapsed", 0),
                    "status": result.get("status", "error"),
                    "early_abort": 1 if result.get("early_abort") else 0,
                    "symbols_used": result.get("symbols_used", 0),
                }
                for k in cfg_keys:
                    row[f"cfg_{k}"] = cfg_dict.get(k, "")
                writer.writerow(row)
                csvfile.flush()
                s = result.get("sharpe", 0)
                if s > best_sharpe:
                    best_sharpe = s
                elapsed_total = time.time() - t_start
                rate = completed / elapsed_total if elapsed_total > 0 else 0
                eta = (len(todo) - completed) / rate / 3600 if rate > 0 else 0
                if completed % 10 == 0 or completed <= 5:
                    print(f"  [{completed}/{len(todo)}] sharpe={s:.4f} trades={result.get('trades', 0)} "
                          f"best={best_sharpe:.4f} rate={rate:.1f}/s ETA={eta:.1f}h")
                # KILL RULE: abort if best_sharpe stays below threshold after warmup
                if completed >= args.kill_warmup and best_sharpe < args.kill_sharpe:
                    print(f"  KILL-RULE TRIGGERED: best_sharpe={best_sharpe:.4f} < {args.kill_sharpe} after {completed} configs")
                    print(f"  Aborting sweep — grid likely missing the right alpha knobs")
                    for f in futures:
                        f.cancel()
                    break

    print(f"\n{'='*70}")
    print(f"  SWEEP COMPLETE — {completed} configs in {time.time()-t_start:.0f}s")
    print(f"  Best Sharpe: {best_sharpe:.4f}")
    print(f"  Results: {csv_path}")
    print(f"{'='*70}")
    print(f"\nV8_QUICK_SWEEP_DONE: configs={completed} best_sharpe={best_sharpe:.4f} csv={csv_path}")


if __name__ == "__main__":
    main()

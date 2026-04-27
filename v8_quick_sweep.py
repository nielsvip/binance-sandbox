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
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed, BrokenExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from v8_quick_engine import (
    QuickConfig, load_npz, iter_npz, simulate,
    FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER,
    MEDIUM_SYMBOLS_CRYPTO, MEDIUM_SYMBOLS_TRADIER
)
from test_rate_guard import RateGuard

_WORKER_STORES = None
_WORKER_MODE = None
_WORKER_NPZ_PARAMS = None

def _worker_init_shared(npz_dir, mode, symbols_list, start_date):
    """Pre-load all NPZ into worker heap. Fast per config, but uses full NPZ RAM."""
    global _WORKER_STORES, _WORKER_MODE
    _WORKER_MODE = mode
    _WORKER_STORES = load_npz(mode, symbols_list, start_date, npz_dir)

def run_one_config_shared(payload):
    cfg_dict, run_id = payload
    return _run_config_with_stores(_WORKER_STORES, _WORKER_MODE, cfg_dict, run_id)

def _worker_init_streaming(npz_dir, mode, symbols_list, start_date):
    """Streaming init: store params only. NPZ loaded one symbol at a time per config.
    Peak RAM per worker = 1 symbol NPZ, not all symbols. Allows many more workers."""
    global _WORKER_NPZ_PARAMS, _WORKER_MODE
    _WORKER_MODE = mode
    _WORKER_NPZ_PARAMS = (npz_dir, mode, symbols_list, start_date)

def run_one_config_streaming(payload):
    """Streaming worker: loads NPZ symbols one at a time via iter_npz generator.
    OS page cache deduplicates repeated reads across concurrent workers."""
    cfg_dict, run_id = payload
    npz_dir, mode, symbols_list, start_date = _WORKER_NPZ_PARAMS
    stores = iter_npz(mode, symbols_list, start_date, npz_dir)
    return _run_config_with_stores(stores, mode, cfg_dict, run_id)

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
    STRENGTH_MIN_SCORE, MIN_HOLD_BARS, WT_EXIT_MIN_TFS, HTF_MIN_ALIGNED."""
    grid = {
        "STRENGTH_FILTER_ENABLED": [True],
        "STRENGTH_MIN_SCORE": [3.0, 5.0, 7.0, 10.0, 13.0],
        "MIN_HOLD_BARS": [5, 10, 15, 20, 30],
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
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "HTF_MIN_ALIGNED": [2, 3],
        "D_TREND_REQUIRED": [True, False],
        "K_ZONE_LONG_THRESHOLD": [25, 35, 45],
        "MFI_LONG_THRESHOLD_D": [15.0, 20.0, 25.0, 30.0],
        "FH_MOMENTUM_MIN_MOVE_PCT": [0.3, 0.5, 0.7],
    }
    return grid



def build_param_grid_sharpe3_tradier():
    """504-config tradier sweep: TP capped at exact target (limit-order semantics) → low std → high Sharpe.
    Param order: ESThresh/TP vary fastest so kill-sharpe sees all combos in first 28 configs."""
    grid = {
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "RZ_EXIT_ENABLED": [True],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "REENTRY_RALLY_K15M_MAX": [75.0],
        "DELTA_ENTRY_ENABLED": [True],
        "REENTRY_RALLY_HTF_MIN": [1, 2, 3],
        "MIN_HOLD_BARS": [10, 20],
        "K3M_FLOOR": [20.0, 25.0, 30.0],
        "ENTRY_SCORE_THRESHOLD": [0.0, 12.0, 18.0, 21.0],
        "EARLY_ABORT_SHARPE_FLOOR": [1.2],
        "EARLY_ABORT_MIN_SYMBOLS": [30],
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


def build_param_grid_indicator_audit():
    """2026-04-18 indicator-audit experimental switches — A/B test each independently.
    Fields in NPZ confirmed: wt_velocity_up_count, wt_velocity_down_count, wt_cross_bars_ago_*.
    Baseline: all switches OFF. Each row tests one switch on its own."""
    grid = {
        # Keep proven v3_core baseline values fixed
        "STRENGTH_FILTER_ENABLED": [True],
        "STRENGTH_MIN_SCORE": [5.0],
        "MIN_HOLD_BARS": [10],
        "WT_EXIT_MIN_TFS": [3],
        # R-G2: MTF WT velocity alignment gate
        "WT_MTF_VEL_GATE_ENABLED": [False, True],
        "WT_MTF_VEL_MIN": [2, 3, 4],
        # RE-1: cross freshness gate
        "REENTRY_CROSS_FRESHNESS_ENABLED": [False, True],
        "REENTRY_CROSS_MAX_BARS_AGO": [3, 5, 8, 12],
    }
    return grid


def build_param_grid_mega():
    """Mega grid: ~500k configs. Early-abort-at-3-syms keeps mean runtime ~5s.
    Covers entry gates + exits + profit targets + holds + velocity + reentry."""
    return {
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "CT_WT_VELOCITY_1H_MIN": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "SATOSHIT_ENABLED": [True, False],
        "DELTA_ENTRY_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "WINNER_PROTECT_ENABLED": [True, False],
        "K3M_FLOOR": [15.0, 20.0, 25.0, 30.0, 35.0],
        "ENTRY_SCORE_THRESHOLD": [12.0, 15.0, 18.0, 20.0, 24.0],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "MIN_HOLD_BARS": [3, 5, 8],
        "EARLY_ABORT_MIN_SYMBOLS": [3],
        "EARLY_ABORT_SHARPE_FLOOR": [2.5],
    }


def build_param_grid_mega_v2():
    """Mega V2 — final correct baseline 2026-04-19 ablation.
    Critical finding: REENTRY_RALLY_K15M_MAX is the key lever. Default=100 (no gate) → Sharpe 0.01.
      K15M=40 + PROFIT_TARGET_PCT=0.5 + HOLD=50 + VEL=8 → Sharpe=6.18 on 11-sym 4yr.
    Dead params removed: CYCLE_TP, ENTRY_SYMGATE, RANK_CONVICTION, DC_MOMENT.
    EARLY_ABORT_SHARPE_FLOOR=2.0 kills bad configs fast.
    ~98k configs × 11 syms × 4yr ≈ 2s/config → ~4h for 14 workers."""
    return {
        "MIN_HOLD_BARS": [5, 10, 20, 50],
        "REENTRY_RALLY_K15M_MAX": [20.0, 30.0, 40.0, 50.0, 60.0],
        "STRENGTH_MIN_SCORE": [3.0, 5.0],
        "WT_EXIT_MIN_TFS": [2, 3],
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "CT_WT_VELOCITY_1H_MIN": [0.0, 4.0, 6.0, 8.0],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [2.0],
    }


def build_param_grid_mega_stock():
    """Mega stock — ~140k configs around tradier apply_tradier_defaults baseline.
    Tight PT (0.3-1.0%) can push Sharpe >4 via low-variance precision exits.
    No per-config early abort (EARLY_ABORT_MIN_SYMBOLS=12 = run all fast symbols).
    Run with --kill-sharpe 0.0 to collect ALL results, sort by Sharpe afterward.
    Stock NPZ tiny (~650MB) → workers=7 safe. Rate ~3 configs/s → 12h for 130k configs."""
    return {
        "MIN_HOLD_BARS": [10, 20, 40, 60],
        "STRENGTH_MIN_SCORE": [3.0, 5.0, 7.0],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "HTF_MIN_ALIGNED": [1, 2, 3],
        "D_TREND_REQUIRED": [True, False],
        "ENTRY_ZONE_LONG": [0.0, 20.0, 40.0],
        "RANK_CONVICTION_MIN": [1, 2, 3],
        "FH_MOMENTUM_MIN_MOVE_PCT": [0.3, 0.7],
        "MFI_LONG_THRESHOLD_D": [15.0, 20.0, 30.0],
        "WINNER_PROTECT_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [12],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
    }


def build_param_grid_mega_v3():
    """Crypto best-vs-best tournament (2026-04-19 findings).
    Tournament results: K15M=40 → 8 syms 1251 trades Sharpe 7. VEL=4 → 480 trades Sharpe 9.
    Sweet spot for MAX TRADES + HIGH SHARPE: K15M=35-40, VEL=4-6, PT=0.2-0.3, HOLD=20-75.
    ~6k configs. Kill floor=4.0 — only real >4 results kept."""
    return {
        "REENTRY_RALLY_K15M_MAX": [30.0, 35.0, 40.0, 45.0, 50.0],
        "CT_WT_VELOCITY_1H_MIN": [4.0, 5.0, 6.0, 8.0],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "MIN_HOLD_BARS": [20, 50, 75],
        "WT_EXIT_MIN_TFS": [2, 3],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [5],
        "EARLY_ABORT_SHARPE_FLOOR": [4.0],
    }


def build_param_grid_stock_v3():
    """Stock best-vs-best tournament (2026-04-19 findings).
    Tournament: PT=0.15 → Sharpe 17.25! PT=0.2 → 11.55 (5 syms 359 trades).
    VEL_GATE=False confirmed better for stocks. HOLD=20-80 all viable.
    ~960 configs, kill floor=4.0. Run --mode tradier --start 2024-01-01."""
    return {
        "MIN_HOLD_BARS": [10, 20, 40, 60, 80],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [4],
        "EARLY_ABORT_SHARPE_FLOOR": [4.0],
    }


def build_param_grid_stock_v2():
    """Stock V2 — 2026-04-19 ablation baseline.
    Key: HOLD=40 + PT=0.5 + NOLOSS=True → Sharpe 3.22 on 12-sym 1yr (apply_tradier_defaults).
    K15M gate HURTS stocks (kills trades). Sweep HOLD+PT+VEL+exits around the proven baseline.
    No early abort — only ~1k configs, fast completion.
    Run with --mode tradier --start 2024-01-01 --workers 18."""
    return {
        "MIN_HOLD_BARS": [10, 20, 30, 40, 60],
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "CT_WT_VELOCITY_1H_MIN": [0.0, 4.0, 6.0, 8.0],
        "RZ_EXIT_ENABLED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [8],
        "EARLY_ABORT_SHARPE_FLOOR": [1.5],
    }


def build_param_grid_stock_v4():
    """Stock max-trades sweep (2026-04-19): PT=0.1 → Sharpe 17.85 (5 syms, 400 trades).
    Push lower PT to get more symbols qualifying and more trades.
    VEL_GATE=False confirmed. Use start=2023-01-01 for 2yr data.
    ~160 configs, kill floor=4.0, done in minutes."""
    return {
        "MIN_HOLD_BARS": [5, 10, 20, 40],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [5],
        "EARLY_ABORT_SHARPE_FLOOR": [4.0],
    }


def build_param_grid_mega_v5():
    """Crypto max-trades zone with positive PnL (2026-04-19 findings):
    K15M=55+VEL=6: 2,787 trades, Sharpe 8.64, PnL+1702. K15M=40+VEL=6: 1,284 trades, Sharpe 13.12, PnL+3470.
    Push K15M to [55-70] with VEL=5-6 to find max-trades positive-PnL ceiling.
    ~720 configs, kill floor=4.0."""
    return {
        "REENTRY_RALLY_K15M_MAX": [50.0, 55.0, 60.0, 65.0, 70.0],
        "CT_WT_VELOCITY_1H_MIN": [5.0, 6.0],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "MIN_HOLD_BARS": [20, 50],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [8],
        "EARLY_ABORT_SHARPE_FLOOR": [4.0],
    }


def build_param_grid_stock_validate2():
    """Stock scale validation v2 (2026-04-19): fill PT gap between 0.2 (+$1.2k) and 0.3 (+$6.6k).
    Run on all 262 symbols to find exact breakeven threshold and sweet spot.
    Also test STRUCTURAL=True/False and DC_CROSSOVER to see secondary param impact.
    30 configs × 262 syms, streaming, ~50min."""
    return {
        "MIN_HOLD_BARS": [40, 60],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "VWAP_FILTER_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [False],
        "RZ_EXIT_ENABLED": [False],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [1.0],
    }


def build_param_grid_stock_mega():
    """Wide stock sweep (2026-04-19 champion: PT=0.3 HOLD=40 → Sharpe 11.3 PnL +$6.5k at 262 syms).
    VEL_GATE=False and VWAP=False confirmed best for stocks.
    PT=0.2-1.0, HOLD=20-80. Early abort: 15 qualifying symbols (≥30 trades) with floor=2.5.
    Low floor because only ~82/262 syms qualify → first 15 qualifiers avg ≈ 3.5 for champion.
    Run on all 262 symbols via --symbols all --stream. ~192 configs per batch."""
    return {
        "MIN_HOLD_BARS": [20, 40, 60, 80],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "VWAP_FILTER_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [100],
        "EARLY_ABORT_SHARPE_FLOOR": [2.5],
    }


def build_param_grid_mega_v6():
    """Crypto champion space (2026-04-19 confirmed: K15=55 VEL=6 PT=0.15 HOLD=50 WT=3
    → 2741 trades Sharpe 8.99 PnL +$1360). Fine-scan PT 0.15-0.25 and HOLD 20-75.
    Early abort 8 syms floor=3.5 (matches mega_v5 which found 8.99). ~1024 configs."""
    return {
        "REENTRY_RALLY_K15M_MAX": [50.0, 55.0],
        "CT_WT_VELOCITY_1H_MIN": [5.0, 6.0],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "MIN_HOLD_BARS": [20, 30, 50, 75],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [8],
        "EARLY_ABORT_SHARPE_FLOOR": [3.5],
    }


def build_param_grid_stock_validate():
    """Scale validation on all 262 stock symbols (2026-04-19): PT=0.02 gave negative PnL at scale.
    Test PT=0.1-1.5 range that showed positive PnL on 12 symbols to find best real-world winner.
    30 configs only, EARLY_ABORT=50 syms needed.
    Run with --symbols all_stocks --stream."""
    return {
        "MIN_HOLD_BARS": [40, 60, 80],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "VWAP_FILTER_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [False],
        "RZ_EXIT_ENABLED": [False],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [1.0],
    }


def build_param_grid_stock_champion():
    """Single-config champion validation (2026-04-19 winner): PT=0.02, HOLD=60, VEL=False, VWAP=False.
    Run against all 259 stock symbols to validate robustness.
    1 config only — fast champion verification."""
    return {
        "MIN_HOLD_BARS": [60],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "VWAP_FILTER_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [False],
        "RZ_EXIT_ENABLED": [False],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [2.0],
    }


def build_param_grid_stock_v7():
    """Stock 4yr data + VWAP filter test (2026-04-19): vwap_D now in NPZ.
    PT=0.02 confirmed winner. Use start=2021-01-01 for 4yr data → more trades + more qualifying symbols.
    Test VWAP_FILTER_ENABLED=True now that vwap_D is populated.
    ~144 configs, kill floor=4.0, done in minutes."""
    return {
        "MIN_HOLD_BARS": [40, 60, 80],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "VWAP_FILTER_ENABLED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [5],
        "EARLY_ABORT_SHARPE_FLOOR": [4.0],
    }


def build_param_grid_stock_v6():
    """Stock ultra-low PT (2026-04-19): PT=0.02 → 448 trades, Sharpe 18.53 (7 syms).
    Push to 0.01-0.02 to see if more symbols qualify. HOLD=30-50 around proven sweet spot.
    Use start=2023-01-01. ~96 configs, kill floor=4.0."""
    return {
        "MIN_HOLD_BARS": [20, 30, 40, 60],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [False],
        "EARLY_ABORT_MIN_SYMBOLS": [5],
        "EARLY_ABORT_SHARPE_FLOOR": [4.0],
    }


def build_param_grid_stock_v5():
    """Stock max-trades phase 2 (2026-04-19): HOLD=40 best (Sharpe 17.43 vs HOLD=5 at 13.58).
    Push PT even lower (0.02-0.05) to get more symbols qualifying.
    VEL_GATE=False, HOLD=30-60 range around 40 sweet spot.
    Use start=2023-01-01 for 2yr data. ~192 configs, kill floor=4.0."""
    return {
        "MIN_HOLD_BARS": [20, 30, 40, 60],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [5],
        "EARLY_ABORT_SHARPE_FLOOR": [4.0],
    }


def build_param_grid_mega_v4():
    """Crypto max-trades regime (2026-04-19): K15M=50 + PT=0.2 + VEL=5-6 → 2,300 trades, Sharpe 7.3.
    Tournament winner: max trades while Sharpe >4. All 11 symbols qualify.
    Kill floor=4.0. Use start=2022-01-01 (4yr crypto data). ~4,320 configs."""
    return {
        "REENTRY_RALLY_K15M_MAX": [40.0, 45.0, 50.0, 55.0, 60.0],
        "CT_WT_VELOCITY_1H_MIN": [4.0, 5.0, 6.0],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "MIN_HOLD_BARS": [10, 20, 50],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [8],
        "EARLY_ABORT_SHARPE_FLOOR": [3.5],
    }


def build_param_grid_hunt_crypto():
    """Massive crypto hunt — honest engine (hedge + COOLDOWN=0).
    Goal: 10,000+ Sharpe>4 results. Run with --min-csv-sharpe 4.0 --kill-sharpe 2.5 --kill-secs 60.
    EARLY_ABORT floor=2.5 kills bad configs after 5 symbols. ~725k configs, effective ~200k with abort.
    Shuffle so good combos surface early. ~8-10h on 14 workers."""
    return {
        "MIN_HOLD_BARS": [1, 2, 5, 10, 20, 30, 50],
        "REENTRY_RALLY_K15M_MAX": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0],
        "CT_WT_VELOCITY_1H_MIN": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0],
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "RZ_EXIT_ENABLED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "STRENGTH_MIN_SCORE": [2.0, 4.0, 6.0],
        "EARLY_ABORT_MIN_SYMBOLS": [5],
        "EARLY_ABORT_SHARPE_FLOOR": [2.5],
    }


def build_param_grid_mega_v7():
    """Crypto champion cluster (2026-04-19): tight scan around confirmed winners.
    K15=[40-55] PT=[0.14-0.18] VEL=[5-6] HOLD=[20-50] all produced Sharpe>4 in mega_v4/v5.
    Early abort 4 syms floor=4.0 — any config averaging <4 after 12s is garbage, skip it.
    ~768 configs. Expected 40-60% pass early abort. ETA ~50min on 6 workers."""
    return {
        "REENTRY_RALLY_K15M_MAX": [40.0, 45.0, 50.0, 55.0],
        "CT_WT_VELOCITY_1H_MIN": [5.0, 6.0],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "MIN_HOLD_BARS": [20, 30, 50],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [4],
        "EARLY_ABORT_SHARPE_FLOOR": [4.0],
    }


def build_param_grid_hunt_stock():
    """Massive stock hunt — honest engine (hedge + COOLDOWN=0).
    Goal: 10,000+ Sharpe>4 results. Run with --min-csv-sharpe 4.0 --kill-sharpe 2.5 --kill-secs 60.
    EARLY_ABORT floor=2.5. ~725k configs effective ~200k with abort. ~8-10h on 18 workers."""
    return {
        "MIN_HOLD_BARS": [1, 2, 5, 10, 20, 40, 80],
        "REENTRY_RALLY_K15M_MAX": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0],
        "CT_WT_VELOCITY_1H_MIN": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0],
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "RZ_EXIT_ENABLED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "STRENGTH_MIN_SCORE": [2.0, 4.0, 6.0],
        "EARLY_ABORT_MIN_SYMBOLS": [5],
        "EARLY_ABORT_SHARPE_FLOOR": [2.5],
    }


def build_param_grid_stock_dc_hunt():
    """Focused search around proven winner (2026-04-20): VEL=4.0 + PT=0.5% + HOLD=20 → Sharpe=2.0
    on 262 symbols. Fine-scan around this to push above 2.5. All 262 symbols, full 4yr data.
    ~192 configs. EARLY_ABORT: 30 syms floor=1.5 — fast kill on bad configs.
    Run on S2: --mode tradier --symbols all --start 2022-01-01 --tier stock_dc_hunt
    --workers 6 --stream --min-csv-sharpe 1.5 --kill-secs 999999 --kill-sharpe 0"""
    return {
        "MIN_HOLD_BARS": [15, 20, 25, 30],
        "CT_WT_VELOCITY_1H_MIN": [2.0, 4.0, 6.0],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "WT_EXIT_MIN_TFS": [2, 3],
        "DC_RECOVERY_EXIT_TF": ["bb_1h", "dc_4h"],
        "EARLY_ABORT_MIN_SYMBOLS": [30],
        "EARLY_ABORT_SHARPE_FLOOR": [1.5],
    }


def build_param_grid_stock_dc_wide():
    """Wide validation of DC-recovery + continuous-stranded-exit baseline (2026-04-20).
    Continuous DC recovery now enabled — positions orphaned below BB_1H exit after min_hold
    without waiting for exit_sig. VEL=4.0 confirmed best from manual test (Sharpe 2.0-2.7).
    ~1,152 configs on all 262 symbols via --symbols all --stream.
    EARLY_ABORT: 30 qualifying symbols floor=1.5 — drops dead configs fast.
    Run on S1: --mode tradier --symbols all --start 2022-01-01 --tier stock_dc_wide
    --workers 6 --stream --min-csv-sharpe 1.5 --kill-secs 999999 --kill-sharpe 0"""
    return {
        "MIN_HOLD_BARS": [4, 20, 40, 80],
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "CT_WT_VELOCITY_1H_MIN": [2.0, 4.0, 8.0],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [30],
        "EARLY_ABORT_SHARPE_FLOOR": [1.5],
    }


def build_param_grid_stock_phase2():
    """Phase 2: No-PT + WT_EXIT=4 discovery (2026-04-20).
    Finding: PROFIT_TARGET disabled (tradier default) + HOLD=80 + VEL=2.0 + WT_EXIT=4
    → Sharpe 0.51, PnL +$65,862 on 262 symbols (honest NOLOSS=False).
    This is the highest-PnL honest result found so far. Sweep to confirm and extend.
    ~192 configs. Early abort: 50 qualifying syms, floor=0.3."""
    return {
        "MIN_HOLD_BARS": [20, 40, 80, 120],
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "CT_WT_VELOCITY_1H_MIN": [0.0, 2.0, 4.0],  # only matters when VEL_GATE=True
        "WT_EXIT_MIN_TFS": [3, 4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [0.3],
    }


def build_param_grid_stock_phase7():
    """Phase 7: High-quality zone cross (2026-04-20).
    Phase3: VEL=6.0, HOLD=10 → pool=0.4072, 3k trades (best Sharpe). Phase6: VEL=2.0 + wide zone → 17k trades, pool=0.24.
    Hypothesis: VEL=4-6 + permissive zone → HIGH Sharpe AND HIGH trades. Cross VEL × ZL × ZS × CONV × HTF.
    ~128 configs. EARLY_ABORT: 50 syms, floor=0.25."""
    return {
        "MIN_HOLD_BARS": [10],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [4.0, 6.0],
        "WT_EXIT_MIN_TFS": [4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "COOLDOWN_BARS": [0],
        "RANK_CONVICTION_MIN": [2, 3],
        "ENTRY_ZONE_LONG": [30.0, 40.0, 50.0, 60.0],
        "ENTRY_ZONE_SHORT": [30.0, 40.0, 50.0, 60.0],
        "HTF_MIN_ALIGNED": [1, 2],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [0.25],
    }


def build_param_grid_stock_phase6():
    """Phase 6: Zone gate frontier map (2026-04-20).
    Phase5 discovery: ZL=50,ZS=50 → 11,753 trades, pool=0.35, PnL=$131k vs ZL=30,ZS=70 → 2,076 trades.
    Zone gate is the #1 lever. Map the full frontier to find max-trades vs Sharpe tradeoff.
    Fix: HOLD=12, VEL=2.0, WT_EXIT=4, PT=False, CD=0, CONV=2.
    ~150 configs. EARLY_ABORT: 50 syms, floor=0.2."""
    return {
        "MIN_HOLD_BARS": [12],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [2.0],
        "WT_EXIT_MIN_TFS": [4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "COOLDOWN_BARS": [0],
        "RANK_CONVICTION_MIN": [2],
        "ENTRY_ZONE_LONG": [30.0, 40.0, 50.0, 60.0, 70.0, 80.0],
        "ENTRY_ZONE_SHORT": [20.0, 30.0, 40.0, 50.0, 60.0, 70.0],
        "HTF_MIN_ALIGNED": [1, 2],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [0.2],
    }


def build_param_grid_stock_phase5():
    """Phase 5: Entry quality gate sweep (2026-04-20).
    apply_tradier_defaults() sets RANK_CONVICTION_MIN=3 + ENTRY_ZONE_LONG=30 + HTF_MIN_ALIGNED=2.
    These haven't been swept — relaxing them should push trade count higher.
    Baseline lock: HOLD=12, VEL=2.0, WT_EXIT=4, PT=False, CD=0, SRS=True.
    ~180 configs. EARLY_ABORT: 50 syms, floor=0.25."""
    return {
        "MIN_HOLD_BARS": [12],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [2.0],
        "WT_EXIT_MIN_TFS": [4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "COOLDOWN_BARS": [0],
        "RANK_CONVICTION_MIN": [1, 2, 3],
        "ENTRY_ZONE_LONG": [20.0, 30.0, 40.0, 50.0],
        "ENTRY_ZONE_SHORT": [50.0, 60.0, 70.0, 80.0],
        "HTF_MIN_ALIGNED": [1, 2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [0.25],
    }


def build_param_grid_stock_phase4():
    """Phase 4: Max-trades hunt at proven NOLOSS=False params (2026-04-20).
    Phase3 winner: HOLD=10, VEL=1.0-6.0, WT_EXIT=4, No-PT.
    VEL=1.0 → 4,785 trades, pool=0.38, PnL=$57k.
    Push VEL lower (0.0-0.5) and HOLD to 5-10, CD=0 for max trade count.
    Also add ENTRY_SCORE gate to test quality filter.
    ~180 configs. EARLY_ABORT: 50 syms, floor=0.2."""
    return {
        "MIN_HOLD_BARS": [5, 8, 10, 12, 15],
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "CT_WT_VELOCITY_1H_MIN": [0.0, 0.5, 1.0, 2.0],
        "WT_EXIT_MIN_TFS": [4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "COOLDOWN_BARS": [0],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [0.2],
    }


def build_param_grid_stock_phase3():
    """Phase 3: Fine-tune around NOLOSS=False winner (2026-04-20).
    Phase2 honest result: No-PT + HOLD=20 + VEL=2.0-4.0 + WT_EXIT=4
    → pool=0.38, trades=3671-4390, WR=58%, PnL=$49-57k on 262 symbols.
    Fine-scan HOLD=10-40, VEL=1.0-6.0, WT_EXIT=[3,4], add COOLDOWN.
    ~288 configs. EARLY_ABORT: 50 syms, floor=0.25."""
    return {
        "MIN_HOLD_BARS": [10, 15, 20, 25, 30, 40],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [1.0, 2.0, 3.0, 4.0, 6.0],
        "WT_EXIT_MIN_TFS": [3, 4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "COOLDOWN_BARS": [0, 3, 6],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [0.25],
    }


def build_param_grid_stock_sweep_v1():
    """Stock sweep v1 (2026-04-19): expand around proven PT=0.3 HOLD=40-80 winner (262 syms).
    Add WT_EXIT_MIN_TFS, HTF_MIN_ALIGNED, D_TREND_REQUIRED, STRENGTH_MIN_SCORE.
    VEL_GATE=False confirmed best for stocks. EARLY_ABORT=50 syms floor=2.0 kills dead configs fast.
    ~864 configs × 262 syms streaming → ~3h on 18 workers."""
    return {
        "MIN_HOLD_BARS": [20, 40, 60, 80],
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "VWAP_FILTER_ENABLED": [False],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "HTF_MIN_ALIGNED": [1, 2, 3],
        "D_TREND_REQUIRED": [True, False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [False],
        "RZ_EXIT_ENABLED": [False],
        "EARLY_ABORT_MIN_SYMBOLS": [50],
        "EARLY_ABORT_SHARPE_FLOOR": [2.0],
    }


def build_param_grid_baseline255_ablation():
    """Ablation against the Sharpe 2.55 baseline (VEL=8 HOLD=250 PT=1.6 WT_EXIT=3).
    One config per new param being tested — identifies offenders (hurt Sharpe) vs helpers.
    Run on FAST_SYMBOLS 4yr: python v8_quick_sweep.py --mode crypto --symbols fast
      --start 2022-01-01 --tier baseline255_ablation --workers 1 --kill-sharpe 0 --kill-secs 999999

    Config 0 = pure baseline (should reproduce Sharpe ~2.55 on 11 symbols).
    Each subsequent config flips ONE param ON/OFF vs baseline.
    Offenders = configs with sharpe < baseline-0.1. Helpers = configs with sharpe > baseline+0.1.
    """
    baseline = {
        # ── Sharpe 2.55 config (from 2026-04-19 sweep) ────────────────────────
        "CT_WT_VELOCITY_GATE_ENABLED": True, "CT_WT_VELOCITY_1H_MIN": 8.0,
        "MIN_HOLD_BARS": 250,
        "WT_EXIT_MIN_TFS": 3, "REENTRY_RALLY_K15M_MAX": 100.0,
        "WINNER_PROTECT_ENABLED": True, "WINNER_PROTECT_GAIN_PCT": 1.0,
        "STRUCTURAL_RANGE_SHIFT_EXIT": True, "CT_DC_CROSSOVER_SKIP_ENABLED": True,
        "RZ_EXIT_ENABLED": False, "SATOSHIT_ENABLED": True,
        "HTF_MIN_ALIGNED": 1, "D_TREND_REQUIRED": True, "STRENGTH_MIN_SCORE": 5.0,
        # ── All new exit signals OFF (baseline must reproduce 2.55 without them) ─
        "WT_MOMENTUM_EXIT_ENABLED": False, "WT_STRUCT_EXIT_ENABLED": False,
        "WT_DIV_EXIT_ENABLED": False, "WT_PERCENTILE_EXIT_ENABLED": False,
        "WT_ZSCORE_EXIT_ENABLED": False, "WT_ACCEL_EXIT_ENABLED": False,
        "WT_WAVE_PHASE_EXIT_ENABLED": False, "WT_SCORE_FLIP_EXIT_ENABLED": False,
        "WT_VEL_MTF_EXIT_ENABLED": False, "WT_ALIGN_EXIT_ENABLED": False,
        "WT_COMP_DELTA_EXIT_ENABLED": False, "DC_POS_EXIT_ENABLED": False,
    }
    configs = [dict(baseline)]  # config 0 = pure 2.55 baseline
    # ── Test each new exit signal against the baseline ──────────────────────────
    for tf in ("1h", "4h"):
        for thr in (0, -1):
            configs.append({**baseline, "WT_MOMENTUM_EXIT_ENABLED": True, "WT_MOMENTUM_EXIT_TF": tf, "WT_MOMENTUM_EXIT_THRESHOLD": thr})
    for tf in ("1h", "4h"):
        configs.append({**baseline, "WT_ACCEL_EXIT_ENABLED": True, "WT_ACCEL_EXIT_TF": tf})
    for tf in ("1h", "4h"):
        configs.append({**baseline, "WT_WAVE_PHASE_EXIT_ENABLED": True, "WT_WAVE_PHASE_EXIT_TF": tf})
    for tf in ("3m", "15m"):
        configs.append({**baseline, "WT_SCORE_FLIP_EXIT_ENABLED": True, "WT_SCORE_FLIP_EXIT_TF": tf})
    for min_tfs in (2, 3):
        for thr in (-0.5, -1.0, -2.0):
            configs.append({**baseline, "WT_VEL_MTF_EXIT_ENABLED": True, "WT_VEL_MTF_EXIT_MIN_TFS": min_tfs, "WT_VEL_MTF_EXIT_THRESHOLD": thr})
    for tf in ("1h", "4h"):
        for thr in (75.0, 80.0, 85.0):
            configs.append({**baseline, "WT_PERCENTILE_EXIT_ENABLED": True, "WT_PERCENTILE_EXIT_TF": tf, "WT_PERCENTILE_EXIT_THRESHOLD": thr})
    # ── Test key entry param variants vs baseline ─────────────────────────────
    for vel in (4.0, 6.0, 10.0, 12.0):
        configs.append({**baseline, "CT_WT_VELOCITY_1H_MIN": vel})
    for hold in (50, 100, 150, 200, 300):
        configs.append({**baseline, "MIN_HOLD_BARS": hold})
    for wt_min in (2, 4):
        configs.append({**baseline, "WT_EXIT_MIN_TFS": wt_min})
    for k15m in (30.0, 40.0, 50.0, 60.0, 70.0, 80.0):
        configs.append({**baseline, "REENTRY_RALLY_K15M_MAX": k15m})
    configs.append({**baseline, "WINNER_PROTECT_ENABLED": False})
    configs.append({**baseline, "STRUCTURAL_RANGE_SHIFT_EXIT": False})
    configs.append({**baseline, "CT_DC_CROSSOVER_SKIP_ENABLED": False})
    configs.append({**baseline, "RZ_EXIT_ENABLED": True})
    configs.append({**baseline, "SATOSHIT_ENABLED": False})
    configs.append({**baseline, "D_TREND_REQUIRED": False})
    configs.append({**baseline, "HTF_MIN_ALIGNED": 2})
    configs.append({**baseline, "HTF_MIN_ALIGNED": 3})
    return configs


def build_param_grid_exit_wt_audit():
    """Ablation: test each wt/0dc exit metric independently over the proven v3_core baseline.
    Returns a pre-built list (not cartesian product) — one config per signal × threshold.
    Run on FAST_SYMBOLS first (~11 syms × 40 configs = fast). Winners go to full 48-sym sweep.

    Signal encoding in NPZ (all int8 or float32):
      wt_momentum_state: 2=bull_trend 1=bull_weak -1=bear_weak -2=bear_trend
      wt_peak_structure: 1=HH -1=LH | wt_trough_structure: 1=HL -1=LL
      wt_divergence: -1=BEAR 1=BULL | wt_wave_phase: 1=expanding -1=contracting
    """
    # Disable all named exits — only vel_exit (4h vel<-2, hardcoded) remains as safety net.
    # WT_EXIT_MIN_TFS=4 kills delta_exit (max wt_against=3, so 3>=4 never fires).
    # This isolates each new signal's pure contribution.
    baseline = {
        "STRENGTH_FILTER_ENABLED": True, "STRENGTH_MIN_SCORE": 5.0,
        "MIN_HOLD_BARS": 10,
        "SATOSHIT_ENABLED": False, "RZ_EXIT_ENABLED": False,
        "STRUCTURAL_RANGE_SHIFT_EXIT": False, "WT_VEL_DECAY_EXIT_ENABLED": False,
        "WT_MOMENTUM_EXIT_ENABLED": False, "WT_STRUCT_EXIT_ENABLED": False,
        "WT_DIV_EXIT_ENABLED": False, "WT_PERCENTILE_EXIT_ENABLED": False,
        "WT_ZSCORE_EXIT_ENABLED": False, "WT_ACCEL_EXIT_ENABLED": False,
        "WT_WAVE_PHASE_EXIT_ENABLED": False, "WT_SCORE_FLIP_EXIT_ENABLED": False,
        "WT_VEL_MTF_EXIT_ENABLED": False, "WT_ALIGN_EXIT_ENABLED": False,
        "WT_COMP_DELTA_EXIT_ENABLED": False, "DC_POS_EXIT_ENABLED": False,
    }
    configs = [dict(baseline)]  # config 0 = pure baseline
    for tf in ("1h", "4h"):
        for thr in (0, -1):
            configs.append({**baseline, "WT_MOMENTUM_EXIT_ENABLED": True, "WT_MOMENTUM_EXIT_TF": tf, "WT_MOMENTUM_EXIT_THRESHOLD": thr})
    for tf in ("1h", "4h"):
        configs.append({**baseline, "WT_STRUCT_EXIT_ENABLED": True, "WT_STRUCT_EXIT_TF": tf})
    for tf in ("1h", "4h"):
        configs.append({**baseline, "WT_DIV_EXIT_ENABLED": True, "WT_DIV_EXIT_TF": tf})
    for tf in ("1h", "4h"):
        for thr in (75.0, 80.0, 85.0, 90.0):
            configs.append({**baseline, "WT_PERCENTILE_EXIT_ENABLED": True, "WT_PERCENTILE_EXIT_TF": tf, "WT_PERCENTILE_EXIT_THRESHOLD": thr})
    for tf in ("1h", "4h"):
        for thr in (1.5, 2.0, 2.5):
            configs.append({**baseline, "WT_ZSCORE_EXIT_ENABLED": True, "WT_ZSCORE_EXIT_TF": tf, "WT_ZSCORE_EXIT_THRESHOLD": thr})
    for tf in ("1h", "4h"):
        configs.append({**baseline, "WT_ACCEL_EXIT_ENABLED": True, "WT_ACCEL_EXIT_TF": tf})
    for tf in ("1h", "4h"):
        configs.append({**baseline, "WT_WAVE_PHASE_EXIT_ENABLED": True, "WT_WAVE_PHASE_EXIT_TF": tf})
    for tf in ("3m", "15m"):
        configs.append({**baseline, "WT_SCORE_FLIP_EXIT_ENABLED": True, "WT_SCORE_FLIP_EXIT_TF": tf})
    for min_tfs in (2, 3):
        for thr in (-0.5, -1.0, -2.0):
            configs.append({**baseline, "WT_VEL_MTF_EXIT_ENABLED": True, "WT_VEL_MTF_EXIT_MIN_TFS": min_tfs, "WT_VEL_MTF_EXIT_THRESHOLD": thr})
    for min_al in (1, 2):
        configs.append({**baseline, "WT_ALIGN_EXIT_ENABLED": True, "WT_ALIGN_EXIT_MIN": min_al})
    for thr in (0.0, -10.0, -20.0):
        configs.append({**baseline, "WT_COMP_DELTA_EXIT_ENABLED": True, "WT_COMP_DELTA_EXIT_THRESHOLD": thr})
    for thr in (0.65, 0.70, 0.75, 0.80):
        configs.append({**baseline, "DC_POS_EXIT_ENABLED": True, "DC_POS_EXIT_THRESHOLD": thr})
    return configs


def build_param_grid_exit_wt_phase2():
    """Phase 2: combine Phase-1 exit winners in cartesian product.
    Phase 1 ranking (exit_wt_audit on 11 crypto symbols):
      #1 WT_ACCEL_EXIT (4h) +0.092  #2 WT_MOMENTUM_EXIT (1h thr=0) +0.054
      #3 WT_VEL_MTF_EXIT +0.053     #4 WT_WAVE_PHASE_EXIT (1h) +0.049
      #5 WT_SCORE_FLIP_EXIT +0.027  #6 WT_PERCENTILE_EXIT (>=75) +0.020
    Losers (excluded): DC_POS_EXIT, WT_DIV_EXIT, WT_ZSCORE_EXIT, WT_COMP_DELTA_EXIT.
    Run on FAST_SYMBOLS to find best combination, then promote winners to 48-sym sweep."""
    return {
        # ── proven entry baseline (fixed) ──────────────────────────────────────────
        "STRENGTH_FILTER_ENABLED": [True],
        "STRENGTH_MIN_SCORE": [5.0],
        "MIN_HOLD_BARS": [10],
        "WT_EXIT_MIN_TFS": [4],  # delta_exit disabled — winners only
        "SATOSHIT_ENABLED": [False], "RZ_EXIT_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [False], "WT_VEL_DECAY_EXIT_ENABLED": [False],
        # ── Phase-1 winners ─────────────────────────────────────────────────────────
        "WT_ACCEL_EXIT_ENABLED": [True, False],
        "WT_ACCEL_EXIT_TF": ["4h", "1h"],
        "WT_MOMENTUM_EXIT_ENABLED": [True, False],
        "WT_MOMENTUM_EXIT_TF": ["1h"],
        "WT_MOMENTUM_EXIT_THRESHOLD": [0],
        "WT_VEL_MTF_EXIT_ENABLED": [True, False],
        "WT_VEL_MTF_EXIT_MIN_TFS": [2, 3],
        "WT_VEL_MTF_EXIT_THRESHOLD": [-1.0, -2.0],
        "WT_WAVE_PHASE_EXIT_ENABLED": [True, False],
        "WT_WAVE_PHASE_EXIT_TF": ["1h", "4h"],
        "WT_SCORE_FLIP_EXIT_ENABLED": [True, False],
        "WT_SCORE_FLIP_EXIT_TF": ["3m", "15m"],
        "WT_PERCENTILE_EXIT_ENABLED": [True, False],
        "WT_PERCENTILE_EXIT_TF": ["1h"],
        "WT_PERCENTILE_EXIT_THRESHOLD": [75.0, 80.0],
    }


def build_param_grid_exit_wt_48sym():
    """Full 48-sym validation for best exit combination found in phase2.
    Replace the [True,False] with [True] for confirmed winners before running.
    Run on S1 with all 48 crypto symbols × 4yr to validate vs READONLY_LATEST baseline."""
    return {
        # ── entry baseline (proven v3_core best) ──
        "STRENGTH_FILTER_ENABLED": [True],
        "STRENGTH_MIN_SCORE": [5.0],
        "MIN_HOLD_BARS": [10, 20, 30],
        "WT_EXIT_MIN_TFS": [3],
        "SATOSHIT_ENABLED": [False], "RZ_EXIT_ENABLED": [False],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [False],
        # ── confirmed Phase-1 winners (fill in after phase2 picks top combo) ──
        "WT_ACCEL_EXIT_ENABLED": [True],
        "WT_ACCEL_EXIT_TF": ["4h"],
        "WT_MOMENTUM_EXIT_ENABLED": [True],
        "WT_MOMENTUM_EXIT_TF": ["1h"],
        "WT_MOMENTUM_EXIT_THRESHOLD": [0],
        "WT_WAVE_PHASE_EXIT_ENABLED": [True],
        "WT_WAVE_PHASE_EXIT_TF": ["1h"],
        "WT_VEL_MTF_EXIT_ENABLED": [True, False],
        "WT_VEL_MTF_EXIT_MIN_TFS": [2, 3],
        "WT_VEL_MTF_EXIT_THRESHOLD": [-1.0, -2.0],
        "WT_SCORE_FLIP_EXIT_ENABLED": [True, False],
        "WT_SCORE_FLIP_EXIT_TF": ["3m"],
        "WT_PERCENTILE_EXIT_ENABLED": [True, False],
        "WT_PERCENTILE_EXIT_TF": ["1h"],
        "WT_PERCENTILE_EXIT_THRESHOLD": [75.0, 80.0],
    }


def build_param_grid_mega_crypto_v8():
    """REBASED 2026-04-20 (v3) — correct crypto baseline params (Sharpe ~8 on 49 sym).
    LE INSIGHT: D_TREND_REQUIRED=True + LOCAL_EXTREMES_MIN_SCORE=45 = 0 trades on crypto.
    Reason: LE requires stoch_D < 40 (oversold = D trending DOWN) while D_TREND requires UP.
    Correct baseline: MIN_HOLD=50, PT=0.8%, STRENGTH=3, D_TREND=True, WT_EXIT=2 (native crypto params).
    Sweeping: DYNAMIC_COUNTER_EXIT (tradier winner — test on crypto), plus standard unknowns.
    Run: --mode crypto --symbols fast --start 2022-01-01 --min-csv-sharpe 3.0 --kill-sharpe 3.0 --kill-secs 90 --workers 4 --stream --shuffle
    """
    return {
        # ── FIXED: proven crypto baseline (produces ~8 Sharpe on 49 sym) ──────────
        "MIN_HOLD_BARS": [50],
        "STRENGTH_MIN_SCORE": [3.0],
        "D_TREND_REQUIRED": [True],
        "WT_EXIT_MIN_TFS": [2],
        "HTF_MIN_ALIGNED": [1],
        "DC_RECOVERY_EXIT_ENABLED": [True],
        "NOLOSS_ENABLED": [True],
        # ── RZ BREAKOUT: always ON, winner mode fixed (dc_low4_15m swept 2026-04-21, tied dc_low4_base by 0.0003) ──
        "RZ_BREAKOUT_ENTRY_ENABLED": [True],
        "RZ_BREAKOUT_NOLOSS_MODE": ["dc_low4_15m"],
        # ── UNKNOWNS TO SWEEP ─────────────────────────────────────────────────────
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": [True, False],
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": [45.0, 55.0],
        "CT_WT_VELOCITY_GATE_ENABLED": [True, False],
        "CT_WT_VELOCITY_1H_MIN": [8.0, 10.0],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "RZ_EXIT_ENABLED": [True, False],
        "EXIT_SCORER_ENABLED": [True, False],
        "EXIT_SCORER_MIN_CONDITIONS": [2, 3],
        "WINNER_PROTECT_ENABLED": [True, False],
        "PARTIAL_EXIT_ENABLED": [True, False],
        # ── FLOOR ─────────────────────────────────────────────────────────────────
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [3.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [15.0],
    }


def build_param_grid_mega_crypto_v8_a1234():
    """NEW 2026-04-26 — sweeps Improvement Framework A1-A4 switches on top of mega_crypto_v8 baseline.
    Inherits the FIXED baseline params from mega_crypto_v8 (proven crypto starting point).
    Old "UNKNOWNS" replaced with the 4 new primitives: SQUEEZE_FIRE, DIVERGENCE, FUNDING, OI.
    Engine guards already in place: ADDITIVE_SIGNAL_MIN_HTF for SQUEEZE/DIV, pre-cache pass-through for FUNDING/OI.
    Run: --mode crypto --tier mega_crypto_v8_a1234 --workers 4 --stream --shuffle --start 2022-01-01 --kill-secs 99999
    """
    return {
        # ── FIXED: same proven crypto baseline as mega_crypto_v8 ──
        "MIN_HOLD_BARS": [50],
        "STRENGTH_MIN_SCORE": [3.0],
        "D_TREND_REQUIRED": [True],
        "WT_EXIT_MIN_TFS": [2],
        "HTF_MIN_ALIGNED": [1],
        "DC_RECOVERY_EXIT_ENABLED": [True],
        "NOLOSS_ENABLED": [True],
        "RZ_BREAKOUT_ENTRY_ENABLED": [True],
        "RZ_BREAKOUT_NOLOSS_MODE": ["dc_low4_15m"],
        # ── A1-A4 NEW PRIMITIVES (the actual sweep target) ──
        "SQUEEZE_FIRE_ENTRY_ENABLED": [True, False],
        "SQUEEZE_FIRE_TF": ["1h", "4h"],
        "DIVERGENCE_ENTRY_ENABLED": [True, False],
        "DIVERGENCE_ENTRY_TF": ["1h", "4h"],
        "DIVERGENCE_INDICATOR": ["wt", "mfi", "either"],
        "FUNDING_GATE_ENABLED": [True, False],
        "FUNDING_GATE_LONG_MAX": [0.0003, 0.0005, 0.001],
        "FUNDING_GATE_SHORT_MIN": [-0.001, -0.0005, -0.0003],
        "OI_CONFIRM_ENABLED": [True, False],
        "OI_CONFIRM_MIN_CHANGE_PCT": [0.3, 0.5, 1.0],
        "ADDITIVE_SIGNAL_MIN_HTF": [1, 2],
        # ── FLOOR ──
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [2.0],  # lower than v8's 3.0 since this explores OFF-baseline territory
        "EARLY_ABORT_TIME_LIMIT_SEC": [15.0],
    }


def build_param_grid_mega_tradier_v8_a134():
    """NEW 2026-04-26 — tradier mirror of A1-A4 (excluding A1/A2 funding+OI: stocks have no perp funding/OI).
    Sweeps SQUEEZE_FIRE (A3) + DIVERGENCE (A4) on top of le_dynamic_v2 tradier baseline.
    Run: --mode tradier --tier mega_tradier_v8_a134 --workers 4 --stream --shuffle --start 2022-01-01 --kill-secs 99999
    """
    return {
        # FIXED: tradier baseline (mirrors mega_tradier_v8 inherited starts; placeholder — keep simple)
        "NOLOSS_ENABLED": [True],
        "RZ_BREAKOUT_ENTRY_ENABLED": [True],
        # A3+A4 sweep
        "SQUEEZE_FIRE_ENTRY_ENABLED": [True, False],
        "SQUEEZE_FIRE_TF": ["1h", "4h"],
        "DIVERGENCE_ENTRY_ENABLED": [True, False],
        "DIVERGENCE_ENTRY_TF": ["1h", "4h"],
        "DIVERGENCE_INDICATOR": ["wt", "mfi", "either"],
        "ADDITIVE_SIGNAL_MIN_HTF": [1, 2],
        "EARLY_ABORT_MIN_SYMBOLS": [10],
        "EARLY_ABORT_SHARPE_FLOOR": [2.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [15.0],
    }


def build_param_grid_mega_tradier_v8():
    """REBASED 2026-04-20 to le_dynamic_v2_baseline snapshot (Sharpe 6.577 on 262 sym).
    All le_dynamic_v2 winner params FIXED. RZ_BREAKOUT_ENTRY always ON (it's a fact).
    PRIMARY SWEEP: which NOLOSS bypass mode keeps losing RZ-breakout entries alive correctly?
    bar_structure = lower-high+lower-low on base-TF bar vs prev (within RZ_BREAKOUT_NOLOSS_BAR_WINDOW bars)
    dc_low4_base  = 4-bar DC low on base TF (5m stocks / 3m crypto)
    dc_low_base   = 20-bar DC low on base TF
    dc_low4_15m   = 4-bar DC low on 15m
    Also sweeps exit features on top: RZ_EXIT, EXIT_SCORER, DC_RECOVERY (most impactful).
    192 configs. Run tradier: --mode tradier --symbols medium --start 2024-01-01
      --min-csv-sharpe 1.5 --kill-sharpe 0.5 --kill-secs 300 --workers 4 --stream --shuffle
    Run crypto:   --mode crypto  --symbols medium --start 2022-01-01
      --min-csv-sharpe 1.5 --kill-sharpe 0.5 --kill-secs 300 --workers 4 --stream --shuffle
    """
    return {
        # ── FIXED: le_dynamic_v2 winner ───────────────────────────────────────────
        "LOCAL_EXTREMES_SCORER_ENABLED": [True],
        "LOCAL_EXTREMES_MIN_SCORE": [45.0],
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": [True],
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": [55.0],
        "MIN_HOLD_BARS": [20],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "WT_EXIT_MIN_TFS": [3],
        "STRENGTH_MIN_SCORE": [6.0],
        "D_TREND_REQUIRED": [True],
        "HTF_MIN_ALIGNED": [1],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        # ── RZ BREAKOUT: always ON, winner mode fixed (dc_low4_15m swept 2026-04-21) ──
        "RZ_BREAKOUT_ENTRY_ENABLED": [True],
        "RZ_BOT_BB_THRESHOLD": [0.20],
        "RZ_TOP_BB_THRESHOLD": [0.85],
        "RZ_BREAKOUT_NOLOSS_MODE": ["dc_low4_15m"],
        # ── EXIT FEATURE UNKNOWNS (most impactful — others fixed at default) ──────
        "RZ_EXIT_ENABLED": [True, False],
        "EXIT_SCORER_ENABLED": [True, False],
        "EXIT_SCORER_MIN_CONDITIONS": [2, 3],
        "DC_RECOVERY_EXIT_ENABLED": [True, False],
        # ── FLOOR: disabled — medium tradier symbols (large caps) have lower Sharpe than full 128-set ──
        "EARLY_ABORT_MIN_SYMBOLS": [999],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [30.0],
    }


def build_param_grid_mega_tradier_v8_focused():
    """2026-04-20: Focused follow-up to mega_tradier_v8. Fixed winner params from 3-winner cluster:
    PT=0.7%, hold=80, D_TREND=True, VELOCITY_GATE+MIN=10, STRENGTH=6.0, HTF=1.
    Sweeps only the 5 open questions: WT_EXIT, SRS, WINNER_PROTECT, DC_RECOVERY, DC_CROSSOVER_SKIP.
    48 configs. Run on full 128-symbol set for proper validation.
    Run: --mode tradier --symbols 128 --start 2024-01-01 --tier mega_tradier_v8_focused --workers 6
    """
    return {
        "MIN_HOLD_BARS": [80],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "D_TREND_REQUIRED": [True],
        "HTF_MIN_ALIGNED": [1],
        "STRENGTH_MIN_SCORE": [6.0],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "WINNER_PROTECT_ENABLED": [True, False],
        "DC_RECOVERY_EXIT_ENABLED": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [20],
        "EARLY_ABORT_SHARPE_FLOOR": [0.5],
        "EARLY_ABORT_TIME_LIMIT_SEC": [60.0],
    }


def build_param_grid_rz_exit_sweep():
    """TEST_PRIORITY: Isolate RZ_EXIT and EXIT_SCORER impact across both modes.
    Run crypto: --mode crypto --symbols fast --start 2022-01-01 --min-csv-sharpe 2.0 --workers 6 --stream
    Run tradier: --mode tradier --symbols fast --start 2024-01-01 --min-csv-sharpe 2.0 --kill-sharpe 0.3 --workers 6 --stream
    432 combos (crypto), 432 (tradier).
    """
    return {
        "RZ_EXIT_ENABLED": [True, False],
        "RZ_K_EXIT": [70.0, 75.0, 80.0, 85.0, 90.0, 95.0],
        "RZ_TOP_BB_THRESHOLD": [0.80, 0.85, 0.90],
        "RZ_MFI_EXIT": [75.0, 85.0, 95.0],
        "EXIT_SCORER_ENABLED": [True, False],
        "EXIT_SCORER_MIN_CONDITIONS": [2, 3, 4],
        "EXIT_SCORER_K_EXTREME": [65.0, 75.0, 85.0],
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [0.3],
        "EARLY_ABORT_TIME_LIMIT_SEC": [30.0],
    }


def build_param_grid_rz_breakout_tradier():
    """2026-04-20: RZ breakout entry — price exits extreme BB zone as entry signal.
    LONG: bb_pct_b_1h was <= RZ_BOT_BB_THRESHOLD, now above it.
    SHORT: bb_pct_b_1h was >= RZ_TOP_BB_THRESHOLD, now below it.
    HIGH RISK of falling back in → paired with NO_LOSS guard (dc_low_15m / dc_low_1h).
    Run tradier: --mode tradier --symbols fast --start 2024-01-01 --tier rz_breakout_tradier --workers 6 --min-csv-sharpe 2.0
    Run crypto:  --mode crypto  --symbols fast --start 2022-01-01 --tier rz_breakout_tradier --workers 6 --min-csv-sharpe 2.0
    288 configs (tradier), same for crypto.
    """
    return {
        "RZ_BREAKOUT_ENTRY_ENABLED": [True],
        "RZ_BREAKOUT_NOLOSS_GUARD_ENABLED": [True, False],
        "RZ_BREAKOUT_NOLOSS_GUARD_TF": ["15m", "1h"],
        "RZ_BOT_BB_THRESHOLD": [0.10, 0.15, 0.20],
        "RZ_TOP_BB_THRESHOLD": [0.80, 0.85, 0.90],
        "MIN_HOLD_BARS": [4, 10, 20],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [0.3],
        "EARLY_ABORT_TIME_LIMIT_SEC": [30.0],
    }


def build_param_grid_rz_noloss_mode():
    """2026-04-21: RZ breakout entry — sweep NOLOSS bypass mode with WT exits ONLY (no PT).
    4 bypass modes: bar_structure (lower-high+lower-low on base TF within first N bars),
    dc_low4_base (4-bar DC low on base TF: 5m tradier / 3m crypto),
    dc_low_base (20-bar DC low on base TF), dc_low4_15m (4-bar DC low on 15m).
    EXIT: WT turn only (HTF WT slows / LTF crosses) — NO profit target.
    LE disabled (LOCAL_EXTREMES_SCORER_ENABLED=False) so RZ is the ONLY entry — this is required to
    isolate which NOLOSS bypass mode protects failing RZ entries. With LE active (tradier default=True),
    LE fires 1000+/yr swamping the ~40 standalone RZ entries, making mode differences invisible.
    Run tradier: --mode tradier --symbols medium --start 2024-01-01 --tier rz_noloss_mode --workers 4 --stream --min-csv-sharpe 0.05 --kill-sharpe 0 --kill-secs 999999
    Run crypto:  --mode crypto  --symbols medium --start 2022-01-01 --tier rz_noloss_mode --workers 4 --stream --min-csv-sharpe 0.05 --kill-sharpe 0 --kill-secs 999999
    144 configs (4 modes × 3 bot × 3 top × 2 bar_window × 2 wt_exit_min).
    """
    return {
        "RZ_BREAKOUT_ENTRY_ENABLED": [True],
        "NOLOSS_ENABLED": [True],
        "LOCAL_EXTREMES_SCORER_ENABLED": [False],
        "RZ_BREAKOUT_NOLOSS_MODE": ["bar_structure", "dc_low4_base", "dc_low_base", "dc_low4_15m"],
        "RZ_BREAKOUT_NOLOSS_BAR_WINDOW": [2, 4],
        "RZ_BOT_BB_THRESHOLD": [0.10, 0.15, 0.20],
        "RZ_TOP_BB_THRESHOLD": [0.80, 0.85, 0.90],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [999],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [30.0],
    }


def build_param_grid_local_extremes_tradier():
    """2026-04-20: Local bottom/top swing strategy for tradier stocks — Phase 1: K-gate only.
    LONG: enter when k_1h < ENTRY_ZONE_LONG (oversold = local bottom).
    SHORT: enter when k_1h > ENTRY_ZONE_SHORT (overbought = local top).
    EXIT: profit target fires when price recovers + WT technical confirmation.
    Phase 1: find optimal K threshold + PT + hold period on 12 fast symbols.
    Phase 2: add LOCAL_EXTREMES_SCORER_ENABLED to confirm WT/DC/MFI alignment.
    Tradier defaults auto-applied: K_ZONE_ENTRY_ENABLED=True, ENTRY_ZONE_K_TF=1h, HTF_MIN_ALIGNED=2.
    Run: --mode tradier --symbols fast --start 2024-01-01 --tier local_extremes_tradier --workers 8
    """
    return {
        "ENTRY_ZONE_LONG": [10.0, 20.0, 30.0, 40.0],
        "ENTRY_ZONE_SHORT": [60.0, 70.0, 80.0, 90.0],
        "START_POSITION_SIZE": [500.0, 1000.0, 2000.0, 5000.0],
        "MIN_HOLD_BARS": [4, 10, 20],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [2.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [15.0],
    }


def build_param_grid_local_extremes_tradier_scorer():
    """2026-04-20: Local extremes Phase 2 — add 100-pt multi-indicator scorer gate.
    LOCAL_EXTREMES_SCORER_ENABLED=True adds WT+DC+MFI+HA+VOL confirmation on top of K-gate.
    Start from best Phase 1 ENTRY_ZONE winners. Sweep LOCAL_EXTREMES_MIN_SCORE thresholds.
    Run: --mode tradier --symbols fast --start 2024-01-01 --tier local_extremes_tradier_scorer --workers 8
    """
    return {
        "ENTRY_ZONE_LONG": [20.0, 30.0],
        "ENTRY_ZONE_SHORT": [70.0, 80.0],
        "LOCAL_EXTREMES_SCORER_ENABLED": [True],
        "LOCAL_EXTREMES_MIN_SCORE": [20.0, 30.0, 40.0, 50.0, 60.0],
        "START_POSITION_SIZE": [1000.0, 2000.0, 5000.0],
        "MIN_HOLD_BARS": [4, 10, 20],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [2.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [20.0],
    }


def build_param_grid_dc_low4_bypass_tradier():
    """2026-04-20: Test DC_LOW4_5M fast exit that bypasses NOLOSS gate.
    Risk: death by 1000 paper cuts. Sweep: enabled=[T/F], max_bars=[0,4,8,12], use_standard=[T/F].
    48 configs × 262 symbols. Baseline (enabled=False) must have Sharpe > winner or it's a paper-cut killer.
    Run: --mode tradier --symbols all --start 2024-01-01 --tier dc_low4_bypass_tradier --workers 6
    """
    return {
        "DC_LOW4_BYPASS_NOLOSS_ENABLED": [True, False],
        "DC_LOW4_BYPASS_MAX_BARS": [0, 4, 8, 12],
        "DC_LOW4_BYPASS_USE_STANDARD": [True, False],
        "MIN_HOLD_BARS": [20],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "D_TREND_REQUIRED": [True],
        "HTF_MIN_ALIGNED": [1],
        "STRENGTH_MIN_SCORE": [6.0],
        "WT_EXIT_MIN_TFS": [4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "EARLY_ABORT_MIN_SYMBOLS": [20],
        "EARLY_ABORT_SHARPE_FLOOR": [0.5],
        "EARLY_ABORT_TIME_LIMIT_SEC": [60.0],
    }


def build_param_grid_dc_low4_bypass_crypto():
    """2026-04-20: Test DC_LOW4_3M fast exit that bypasses NOLOSS gate (crypto).
    Same paper-cut safety check as tradier version but for 3m base TF.
    Run: --mode crypto --symbols all --start 2022-01-01 --tier dc_low4_bypass_crypto --workers 6
    """
    return {
        "DC_LOW4_BYPASS_NOLOSS_ENABLED": [True, False],
        "DC_LOW4_BYPASS_MAX_BARS": [0, 4, 8, 12],
        "DC_LOW4_BYPASS_USE_STANDARD": [True, False],
        "MIN_HOLD_BARS": [300],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "D_TREND_REQUIRED": [True],
        "STRENGTH_MIN_SCORE": [4.0],
        "WT_EXIT_MIN_TFS": [2],
        "EARLY_ABORT_MIN_SYMBOLS": [15],
        "EARLY_ABORT_SHARPE_FLOOR": [0.5],
        "EARLY_ABORT_TIME_LIMIT_SEC": [60.0],
    }


def build_param_grid_dc_low_tf_tradier():
    """2026-04-20: Test dc_low_5m vs dc_low_15m as stop-loss (bypass NOLOSS) on tradier.
    User request: find if any DC-low TF improves vs current. Baseline = bypass disabled.
    dc_low_15m is much wider channel — fires only on big structural breaks.
    9 configs × 262 symbols. Run: --mode tradier --symbols all --start 2024-01-01 --tier dc_low_tf_tradier --workers 6
    """
    return {
        "DC_LOW4_BYPASS_NOLOSS_ENABLED": [True, False],
        "DC_LOW4_BYPASS_TF": ["5m", "15m", ""],
        "DC_LOW4_BYPASS_USE_STANDARD": [True],
        "DC_LOW4_BYPASS_MAX_BARS": [0],
        "MIN_HOLD_BARS": [20],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "D_TREND_REQUIRED": [True],
        "HTF_MIN_ALIGNED": [1],
        "STRENGTH_MIN_SCORE": [6.0],
        "WT_EXIT_MIN_TFS": [4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "EARLY_ABORT_MIN_SYMBOLS": [30],
        "EARLY_ABORT_SHARPE_FLOOR": [0.5],
        "EARLY_ABORT_TIME_LIMIT_SEC": [90.0],
    }


def build_param_grid_dc_low_tf_crypto():
    """2026-04-20: Test dc_low_3m vs dc_low_15m as stop-loss (bypass NOLOSS) on crypto.
    9 configs × 48 symbols.
    Run: --mode crypto --symbols all --start 2022-01-01 --tier dc_low_tf_crypto --workers 6
    """
    return {
        "DC_LOW4_BYPASS_NOLOSS_ENABLED": [True, False],
        "DC_LOW4_BYPASS_TF": ["3m", "15m", ""],
        "DC_LOW4_BYPASS_USE_STANDARD": [True],
        "DC_LOW4_BYPASS_MAX_BARS": [0],
        "MIN_HOLD_BARS": [300],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "D_TREND_REQUIRED": [True],
        "STRENGTH_MIN_SCORE": [4.0],
        "WT_EXIT_MIN_TFS": [2],
        "EARLY_ABORT_MIN_SYMBOLS": [15],
        "EARLY_ABORT_SHARPE_FLOOR": [0.5],
        "EARLY_ABORT_TIME_LIMIT_SEC": [90.0],
    }


def build_param_grid_dc_breakout_failed_tradier():
    """2026-04-20: Breakout-entry-specific DC stop: if entry_price > bb_upper_1h at entry bar,
    exit when price falls back below bb_upper_1h (breakout failed). Only applies to breakout entries.
    Tests True/False × le_dynamic winner baseline. 2 configs × 262 symbols.
    Run: --mode tradier --symbols all --start 2024-01-01 --tier dc_breakout_failed_tradier --workers 6 --kill-sharpe 0 --kill-secs 999999
    """
    return {
        "DC_BREAKOUT_FAILED_STOP_ENABLED": [True, False],
        "LOCAL_EXTREMES_SCORER_ENABLED": [True],
        "LOCAL_EXTREMES_MIN_SCORE": [45.0],
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": [True],
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": [55.0],
        "MIN_HOLD_BARS": [20],
        "WT_EXIT_MIN_TFS": [3],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "D_TREND_REQUIRED": [True],
        "HTF_MIN_ALIGNED": [1],
        "STRENGTH_MIN_SCORE": [6.0],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "EARLY_ABORT_MIN_SYMBOLS": [999],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [999999.0],
    }


def build_param_grid_dc_breakout_failed_crypto():
    """2026-04-20: Breakout-entry-specific DC stop for crypto (3m base).
    If entry_price > dc_high_4h at entry bar, exit when price falls back below dc_high_4h.
    2 configs × 48 symbols.
    Run: --mode crypto --symbols all --start 2022-01-01 --tier dc_breakout_failed_crypto --workers 6 --kill-sharpe 0 --kill-secs 999999
    """
    return {
        "DC_BREAKOUT_FAILED_STOP_ENABLED": [True, False],
        "MIN_HOLD_BARS": [300],
        "WT_EXIT_MIN_TFS": [2],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "D_TREND_REQUIRED": [True],
        "STRENGTH_MIN_SCORE": [4.0],
        "EARLY_ABORT_MIN_SYMBOLS": [999],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [999999.0],
    }


def build_param_grid_all_tf_brake_tradier():
    """2026-04-20: Emergency brake: if ALL (or N of 7) TFs flip against position, bypass NOLOSS and exit.
    TFs checked: 5m, 15m, 1h, 4h, D, W, M (W/M may be zero for stocks → effectively ignored).
    Sweep min_tfs=[3,4,5] to find where signal is specific enough to not paper-cut.
    6 configs × 262 symbols.
    Run: --mode tradier --symbols all --start 2024-01-01 --tier all_tf_brake_tradier --workers 6 --kill-sharpe 0 --kill-secs 999999
    """
    return {
        "ALL_TF_BRAKE_ENABLED": [True, False],
        "ALL_TF_BRAKE_MIN_TFS": [3, 4, 5],
        "LOCAL_EXTREMES_SCORER_ENABLED": [True],
        "LOCAL_EXTREMES_MIN_SCORE": [45.0],
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": [True],
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": [55.0],
        "MIN_HOLD_BARS": [20],
        "WT_EXIT_MIN_TFS": [3],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "D_TREND_REQUIRED": [True],
        "HTF_MIN_ALIGNED": [1],
        "STRENGTH_MIN_SCORE": [6.0],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "EARLY_ABORT_MIN_SYMBOLS": [999],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [999999.0],
    }


def build_param_grid_all_tf_brake_crypto():
    """2026-04-20: Emergency brake for crypto (3m base). Sweep min_tfs=[4,5,6,7].
    Crypto has W and M TFs so more options available.
    8 configs × 48 symbols.
    Run: --mode crypto --symbols all --start 2022-01-01 --tier all_tf_brake_crypto --workers 6 --kill-sharpe 0 --kill-secs 999999
    """
    return {
        "ALL_TF_BRAKE_ENABLED": [True, False],
        "ALL_TF_BRAKE_MIN_TFS": [4, 5, 6, 7],
        "MIN_HOLD_BARS": [300],
        "WT_EXIT_MIN_TFS": [2],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [10.0],
        "D_TREND_REQUIRED": [True],
        "STRENGTH_MIN_SCORE": [4.0],
        "EARLY_ABORT_MIN_SYMBOLS": [999],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [999999.0],
    }


def build_param_grid_local_extremes_tradier_validate():
    """2026-04-20: Full-symbol validation of local_extremes_tradier Phase 1/2 winners.
    Run top configs from Phase 1 or 2 on all 128 tradier symbols over 2yr with no early abort.
    Paste winning params from Phase 1/2 CSV into ENTRY_ZONE_LONG/SHORT, SIZE, PT_PCT.
    Run: --mode tradier --symbols all --start 2024-01-01 --tier local_extremes_tradier_validate --workers 8 --kill-sharpe 0 --kill-secs 999999
    """
    return {
        "ENTRY_ZONE_LONG": [20.0, 30.0],
        "ENTRY_ZONE_SHORT": [70.0, 80.0],
        "START_POSITION_SIZE": [1000.0, 2000.0, 5000.0],
        "MIN_HOLD_BARS": [4, 10],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [999],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [999999.0],
    }


def build_param_grid_crypto_validate_top():
    """48-symbol 4yr REAL validation of consensus winners from 12-sym pre-screen.
    K_MAX=40 (83% of robust winners), VEL=12/14, PT=0.8/1.0, STR=3/4/5.
    No early abort — run all 48 symbols to get genuine Sharpe.
    Run: --symbols all --start 2022-01-01 --tier crypto_validate_top --workers 12 --kill-sharpe 0 --kill-secs 999999
    """
    return {
        "CT_WT_VELOCITY_1H_MIN": [12.0, 14.0],
        "MIN_HOLD_BARS": [250, 300, 400, 500],
        "STRENGTH_MIN_SCORE": [3.0, 4.0, 5.0],
        "REENTRY_RALLY_K15M_MAX": [40.0],
        "WT_EXIT_MIN_TFS": [2, 3],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "WINNER_PROTECT_ENABLED": [True],
        "D_TREND_REQUIRED": [True],
        "HTF_MIN_ALIGNED": [2],
        "DC_RECOVERY_EXIT_ENABLED": [True],
        "EARLY_ABORT_MIN_SYMBOLS": [999],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [999999.0],
    }


def build_param_grid_stock_wt_d_aug_pt():
    """wt_D augment + profit target on recovery — sweep AUGMENT_PT_PCT × MULTIPLIER.
    Sweep finding: REQUIRE_HIGHER_PRICE=True never fires (price still below entry at wt_D bounce).
    REQUIRE_HIGHER_WT=False wins. Now testing: does exiting the augmented position fast (at +0.3-1.0%)
    improve Sharpe vs holding until regular WT exit signal?
    32 configs. Run on S2: python v8_quick_sweep.py --mode tradier --symbols all --start 2022-01-01
    --tier stock_wt_d_aug_pt --workers 6 --stream --min-csv-sharpe 0.0 --kill-secs 999999 --kill-sharpe 0"""
    return {
        "AUGMENT_WT_D_BOUNCE_ENABLED": [False, True],
        "AUGMENT_WT_D_MULTIPLIER": [2.0, 3.0, 4.0],
        "AUGMENT_WT_D_REQUIRE_HIGHER_WT": [False],
        "AUGMENT_WT_D_REQUIRE_HIGHER_PRICE": [False],
        "AUGMENT_PT_ENABLED": [False, True],
        "AUGMENT_PT_PCT": [0.3, 0.5, 0.8, 1.0],
        "MIN_HOLD_BARS": [40],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [2.0],
        "WT_EXIT_MIN_TFS": [3],
        "EARLY_ABORT_MIN_SYMBOLS": [30],
        "EARLY_ABORT_SHARPE_FLOOR": [0.3],
    }


def build_param_grid_stock_60min_reentry():
    """60-min unconditional reentry — after WT exit, if price continues ≥MIN_PCT within 60min, reenter at 50%.
    Tests: ENABLED T/F × MIN_PCT [0.2,0.3,0.5,0.75,1.0] × WINDOW_BARS [6,12,24] (30/60/120min at 5m).
    Also cross with wt_D augment (best config 4x, no conditions) to measure interaction.
    Baseline: DC_RECOVERY=True, MIN_HOLD=40, VEL=2.0.
    Run: python v8_quick_sweep.py --mode tradier --symbols all --start 2022-01-01
    --tier stock_60min_reentry --workers 6 --stream --min-csv-sharpe 0.0 --kill-secs 999999 --kill-sharpe 0"""
    return {
        "QUICK_REENTRY_60MIN_ENABLED": [False, True],
        "QUICK_REENTRY_60MIN_WINDOW_BARS": [6, 12, 24],
        "QUICK_REENTRY_60MIN_MIN_PCT": [0.2, 0.3, 0.5, 0.75, 1.0],
        "AUGMENT_WT_D_BOUNCE_ENABLED": [False, True],
        "AUGMENT_WT_D_MULTIPLIER": [4.0],
        "AUGMENT_WT_D_REQUIRE_HIGHER_WT": [False],
        "AUGMENT_WT_D_REQUIRE_HIGHER_PRICE": [False],
        "MIN_HOLD_BARS": [40],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [2.0],
        "WT_EXIT_MIN_TFS": [3],
        "EARLY_ABORT_MIN_SYMBOLS": [30],
        "EARLY_ABORT_SHARPE_FLOOR": [0.3],
    }


def build_param_grid_stock_wt_d_aug():
    """wt_D bounce augment — double-down on losing positions when daily WT turns higher.
    Tests: AUGMENT_MULTIPLIER [1.5,2,3,4] × REQUIRE_HIGHER_WT [T/F] × REQUIRE_HIGHER_PRICE [T/F] vs baseline.
    32 configs × 262 symbols, 2022-01-01. ~5min on S2 with 6 workers.
    EXIT STRATEGY FOCUS: does averaging down on wt_D bounce improve Sharpe > 2.5 baseline?
    Run on S2: python v8_quick_sweep.py --mode tradier --symbols all --start 2022-01-01 --tier stock_wt_d_aug --workers 6 --stream --min-csv-sharpe 0.5 --kill-secs 999999 --kill-sharpe 0"""
    return {
        "AUGMENT_WT_D_BOUNCE_ENABLED": [False, True],
        "AUGMENT_WT_D_MULTIPLIER": [1.5, 2.0, 3.0, 4.0],
        "AUGMENT_WT_D_REQUIRE_HIGHER_WT": [True, False],
        "AUGMENT_WT_D_REQUIRE_HIGHER_PRICE": [True, False],
        "MIN_HOLD_BARS": [40],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "CT_WT_VELOCITY_1H_MIN": [2.0],
        "WT_EXIT_MIN_TFS": [3],
        "EARLY_ABORT_MIN_SYMBOLS": [30],
        "EARLY_ABORT_SHARPE_FLOOR": [0.5],
    }


def build_param_grid_crypto_wt_d_4h_aug():
    """Crypto double-down sweep: wt_D and wt_4h bounce augments tested independently and combined.
    wt_4h fires ~6x more frequently than wt_D. Both use independent _aug_done flags (can both fire once).
    Tests: D-only vs 4h-only vs both-enabled × multiplier [2,3,4] × PT enabled [F,T].
    36 configs × 48 symbols, 4yr. ~8min on S1 with 6 workers.
    Run on S1: python v8_quick_sweep.py --mode crypto --symbols all --start 2021-01-01 --tier crypto_wt_d_4h_aug --workers 6 --stream --min-csv-sharpe 0.5 --kill-secs 999999 --kill-sharpe 0"""
    configs = []
    for aug_d in [False, True]:
        for aug_4h in [False, True]:
            if not aug_d and not aug_4h:
                continue
            for mult in [2.0, 3.0, 4.0]:
                for pt_enabled in [False, True]:
                    configs.append({
                        "AUGMENT_WT_D_BOUNCE_ENABLED": aug_d,
                        "AUGMENT_WT_D_MULTIPLIER": mult,
                        "AUGMENT_WT_D_REQUIRE_HIGHER_WT": False,
                        "AUGMENT_WT_D_REQUIRE_HIGHER_PRICE": False,
                        "AUGMENT_WT_4H_BOUNCE_ENABLED": aug_4h,
                        "AUGMENT_WT_4H_MULTIPLIER": mult,
                        "AUGMENT_WT_4H_REQUIRE_HIGHER_WT": False,
                        "AUGMENT_WT_4H_REQUIRE_HIGHER_PRICE": False,
                        "AUGMENT_PT_ENABLED": pt_enabled,
                        "AUGMENT_PT_PCT": 0.5,
                        "MIN_HOLD_BARS": 40,
                        "CT_WT_VELOCITY_GATE_ENABLED": True,
                        "CT_WT_VELOCITY_1H_MIN": 8.0,
                        "WT_EXIT_MIN_TFS": 3,
                        "EARLY_ABORT_MIN_SYMBOLS": 12,
                        "EARLY_ABORT_SHARPE_FLOOR": 1.5,
                    })
    return configs


def build_param_grid_crypto_vel_sweep():
    """Crypto velocity × reentry sweep to find Sharpe>2.5 on non-broken data.
    Coord descent (Apr-17) found vel=6→2.249. vel=8 comment was on broken data (B15/B11=0).
    Tests: CT_WT_VELOCITY_1H_MIN [5,6,7,8,9] × REENTRY_RALLY_K15M_MAX [30,50,80,100] × HTF_MIN_ALIGNED [1,2].
    40 configs × 48 symbols, 4yr. ~12min on S1 with 6 workers.
    Run on S1: python v8_quick_sweep.py --mode crypto --symbols all --start 2021-01-01 --tier crypto_vel_sweep --workers 6 --stream --min-csv-sharpe 1.5 --kill-secs 999999 --kill-sharpe 0"""
    configs = []
    for vel in [5.0, 6.0, 7.0, 8.0, 9.0]:
        for rally in [30.0, 50.0, 80.0, 100.0]:
            for htf in [1, 2]:
                configs.append({
                    "CT_WT_VELOCITY_1H_MIN": vel,
                    "CT_WT_VELOCITY_GATE_ENABLED": True,
                    "REENTRY_RALLY_K15M_MAX": rally,
                    "HTF_MIN_ALIGNED": htf,
                    "MIN_HOLD_BARS": 40,
                    "WT_EXIT_MIN_TFS": 3,
                    "EARLY_ABORT_MIN_SYMBOLS": 12,
                    "EARLY_ABORT_SHARPE_FLOOR": 1.5,
                })
    return configs


def build_param_grid_le_dynamic_tradier():
    """2026-04-20: Dynamic LE scoring on top of REAL tradier_manage entry logic.

    Uses the full tradier baseline (wt_dc_score_entry + K_ZONE + WT velocity + canonical veto gates)
    with two new 5-min bar-level interventions:
      1. COUNTER-EXIT: while in position, if opposite-direction LE score >= threshold → exit (cuts losers fast)
      2. DYNAMIC AUGMENT: every N bars, if same-direction score jumped by MIN_JUMP → average in (improves entry)

    apply_tradier_defaults() auto-applied: LTF=5m, K_ZONE=True, HTF_MIN_ALIGNED=2, ENTRY_ZONE_LONG=25, SHORT=75.
    Sharpe lift comes from: fewer losers (counter-exit) + better avg entry on winners (augment).

    Run: --mode tradier --symbols fast --start 2024-01-01 --tier le_dynamic_tradier --workers 6 --stream --min-csv-sharpe 0.0
    """
    return {
        # LE score gate on entry — only trade when multi-indicator confluence >= threshold
        "LOCAL_EXTREMES_SCORER_ENABLED": [True],
        "LOCAL_EXTREMES_MIN_SCORE": [15.0, 25.0, 35.0, 45.0],
        # Counter-exit: cut position when opposite LE score fires (0=disabled via threshold=999)
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": [True, False],
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": [40.0, 55.0, 70.0],
        # Dynamic augment: every 5 bars, if score jumped → avg in
        "DYNAMIC_SCORE_AUGMENT_ENABLED": [True, False],
        "DYNAMIC_SCORE_AUGMENT_MIN_JUMP": [20.0, 35.0],
        "DYNAMIC_SCORE_AUGMENT_INTERVAL": [5],
        # Exits: short PT to capture local-extreme bounces
        "MIN_HOLD_BARS": [4, 10],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [1.5],
        "EARLY_ABORT_TIME_LIMIT_SEC": [25.0],
    }


def build_param_grid_le_dynamic_tradier_validate():
    """2026-04-20: Full 128-symbol 2yr validation of le_dynamic_tradier winners.
    Paste winning params from Phase 1 before running.
    Run: --mode tradier --symbols all --start 2024-01-01 --tier le_dynamic_tradier_validate --workers 8 --kill-sharpe 0 --kill-secs 999999
    """
    return {
        "LOCAL_EXTREMES_SCORER_ENABLED": [True],
        "LOCAL_EXTREMES_MIN_SCORE": [25.0, 35.0],
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": [True],
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": [40.0, 55.0],
        "DYNAMIC_SCORE_AUGMENT_ENABLED": [True, False],
        "DYNAMIC_SCORE_AUGMENT_MIN_JUMP": [20.0],
        "DYNAMIC_SCORE_AUGMENT_INTERVAL": [5],
        "MIN_HOLD_BARS": [4, 10],
        "WT_EXIT_MIN_TFS": [2, 3],
        "EARLY_ABORT_MIN_SYMBOLS": [999],
        "EARLY_ABORT_SHARPE_FLOOR": [0.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [999999.0],
    }


def build_param_grid_le_dynamic_tradier_v2():
    """2026-04-20: Push le_dynamic toward Sharpe>2.0.
    Winner from validate: MIN_SCORE=35, CE_THR=40, PT=0.5%, HOLD=10, WT_TFS=3.
    KEY new dimension: PROFIT_TARGET_ENABLED=False (technical exits, never tested).
    Also: tighter MIN_SCORE (45,55,65), 3yr window (2023-05-01), CE thresholds tighter.
    Run: --mode tradier --symbols fast --start 2023-05-01 --tier le_dynamic_tradier_v2 --workers 6 --stream --kill-sharpe 0 --kill-secs 999999
    Then validate winners: --symbols all --start 2023-05-01 --tier le_dynamic_tradier_v2_validate
    """
    configs = []
    base = {
        "LOCAL_EXTREMES_SCORER_ENABLED": True,
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": True,
        "DYNAMIC_SCORE_AUGMENT_ENABLED": False,
        "DYNAMIC_SCORE_AUGMENT_MIN_JUMP": 20.0,
        "DYNAMIC_SCORE_AUGMENT_INTERVAL": 5,
        "WT_EXIT_MIN_TFS": 3,
        "EARLY_ABORT_MIN_SYMBOLS": 6,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    for score in [25.0, 35.0, 45.0, 55.0, 65.0]:
        for ce_thr in [30.0, 40.0, 55.0]:
            for hold in [4, 10, 20]:
                for pt_enabled in [True, False]:
                    if pt_enabled:
                        for pt_pct in [0.5, 1.0, 2.0]:
                            configs.append({**base,
                                "LOCAL_EXTREMES_MIN_SCORE": score,
                                "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": ce_thr,
                                "MIN_HOLD_BARS": hold,
                            })
                    else:
                        configs.append({**base,
                            "LOCAL_EXTREMES_MIN_SCORE": score,
                            "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": ce_thr,
                            "MIN_HOLD_BARS": hold,
                        })
    return configs


def build_param_grid_le_dynamic_tradier_v2_validate():
    """2026-04-20: Full 128-symbol validation of le_dynamic_v2 winners.
    Winners from fast 12-sym sweep: score=45 + PT=0.5% dominates (Sharpe 3.73).
    PT=False = 0.54 max -> dead. Also test score=35/55 neighbors to confirm.
    Run: --mode tradier --symbols all --start 2024-01-01 --tier le_dynamic_tradier_v2_validate --workers 8 --kill-sharpe 0 --kill-secs 999999
    """
    base = {
        "LOCAL_EXTREMES_SCORER_ENABLED": True,
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": True,
        "DYNAMIC_SCORE_AUGMENT_ENABLED": False,
        "DYNAMIC_SCORE_AUGMENT_MIN_JUMP": 20.0,
        "DYNAMIC_SCORE_AUGMENT_INTERVAL": 5,
        "WT_EXIT_MIN_TFS": 3,
        "EARLY_ABORT_MIN_SYMBOLS": 999,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = []
    for score in [35.0, 45.0, 55.0]:
        for ce_thr in [30.0, 40.0, 55.0]:
            for hold in [4, 10, 20]:
                configs.append({**base,
                    "LOCAL_EXTREMES_MIN_SCORE": score,
                    "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": ce_thr,
                    "MIN_HOLD_BARS": hold,
                })
    return configs


def build_param_grid_le_dynamic_tradier_v2_validate_ea():
    """2026-04-20: Same as v2_validate but with early abort (floor=2.0 after 20 symbols).
    Configs that score below Sharpe 2.0 on first 20 symbols are killed and flagged as culprits.
    Run: --mode tradier --symbols all --start 2024-01-01 --tier le_dynamic_tradier_v2_validate_ea --workers 6 --stream --kill-sharpe 0 --kill-secs 999999
    """
    base = {
        "LOCAL_EXTREMES_SCORER_ENABLED": True,
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": True,
        "DYNAMIC_SCORE_AUGMENT_ENABLED": False,
        "DYNAMIC_SCORE_AUGMENT_MIN_JUMP": 20.0,
        "DYNAMIC_SCORE_AUGMENT_INTERVAL": 5,
        "WT_EXIT_MIN_TFS": 3,
        "EARLY_ABORT_MIN_SYMBOLS": 60,
        "EARLY_ABORT_SHARPE_FLOOR": 0.8,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = []
    for score in [35.0, 45.0, 55.0]:
        for ce_thr in [30.0, 40.0, 55.0]:
            for hold in [4, 10, 20]:
                configs.append({**base,
                    "LOCAL_EXTREMES_MIN_SCORE": score,
                    "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": ce_thr,
                    "MIN_HOLD_BARS": hold,
                })
    return configs


def build_param_grid_ratio_sentiment_tradier():
    """2026-04-20: Does cross-symbol market_sentiment_score improve entries?
    Tests RATIO_SENTIMENT_FILTER_ENABLED against FULL le_dynamic_v2_baseline_20260420_2156
    (Sharpe 6.577, 262 symbols, 2yr). All 12 canonical baseline params included.
    LONG blocked when mss < LONG_MIN; SHORT blocked when mss > SHORT_MAX.
    v2: Added D_TREND_REQUIRED, HTF_MIN_ALIGNED, STRENGTH_MIN_SCORE, STRUCTURAL_RANGE_SHIFT_EXIT,
        CT_WT_VELOCITY params to match full 6.577-Sharpe baseline (v1 was missing these).
    Run: --mode tradier --symbols all --start 2024-01-01 --tier ratio_sentiment_tradier --workers 8 --kill-sharpe 0 --kill-secs 999999
    """
    base = {
        "LOCAL_EXTREMES_SCORER_ENABLED": True,
        "LOCAL_EXTREMES_MIN_SCORE": 45.0,
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": True,
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": 55.0,
        "DYNAMIC_SCORE_AUGMENT_ENABLED": False,
        "MIN_HOLD_BARS": 20,
        "WT_EXIT_MIN_TFS": 3,
        "D_TREND_REQUIRED": True,
        "HTF_MIN_ALIGNED": 1,
        "STRENGTH_MIN_SCORE": 6.0,
        "STRUCTURAL_RANGE_SHIFT_EXIT": True,
        "CT_WT_VELOCITY_GATE_ENABLED": True,
        "CT_WT_VELOCITY_1H_MIN": 10.0,
        "EARLY_ABORT_MIN_SYMBOLS": 999,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = [
        {**base, "RATIO_SENTIMENT_FILTER_ENABLED": False,
         "RATIO_SENTIMENT_LONG_MIN": 40.0, "RATIO_SENTIMENT_SHORT_MAX": 60.0},
    ]
    for long_min, short_max in [(40.0, 60.0), (45.0, 55.0), (50.0, 50.0), (55.0, 45.0)]:
        configs.append({**base,
            "RATIO_SENTIMENT_FILTER_ENABLED": True,
            "RATIO_SENTIMENT_LONG_MIN": long_min,
            "RATIO_SENTIMENT_SHORT_MAX": short_max,
        })
    return configs


def build_param_grid_ratio_sentiment_crypto():
    """2026-04-20: Does RATIO_SENTIMENT_FILTER improve entries on crypto baseline (~8 Sharpe, 49 sym)?
    NOTE: Using native crypto baseline (MIN50+PT0.8+STR3+D=True). LE=True+MIN=45 gives 0 trades
    on crypto (LE requires stoch_D<40 which contradicts D_TREND_REQUIRED=True — see LE insight).
    Run: --mode crypto --symbols all --start 2022-01-01 --tier ratio_sentiment_crypto --workers 4 --kill-sharpe 0 --kill-secs 999999
    """
    base = {
        "MIN_HOLD_BARS": 50,
        "STRENGTH_MIN_SCORE": 3.0,
        "D_TREND_REQUIRED": True,
        "WT_EXIT_MIN_TFS": 2,
        "DC_RECOVERY_EXIT_ENABLED": True,
        "NOLOSS_ENABLED": True,
        "DYNAMIC_SCORE_AUGMENT_ENABLED": False,
        "EARLY_ABORT_MIN_SYMBOLS": 999,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = [
        {**base, "RATIO_SENTIMENT_FILTER_ENABLED": False,
         "RATIO_SENTIMENT_LONG_MIN": 40.0, "RATIO_SENTIMENT_SHORT_MAX": 60.0},
    ]
    for long_min, short_max in [(40.0, 60.0), (45.0, 55.0), (50.0, 50.0), (55.0, 45.0)]:
        configs.append({**base,
            "RATIO_SENTIMENT_FILTER_ENABLED": True,
            "RATIO_SENTIMENT_LONG_MIN": long_min,
            "RATIO_SENTIMENT_SHORT_MAX": short_max,
        })
    return configs


def build_param_grid_stock_exit_v1():
    """2026-04-20: Technical exit sweep for tradier stocks targeting Sharpe>2.5.
    Tests 3 new exits (vel_floor, kd_wt1h, adaptive_tfs) + 2 existing untested exits
    (WT_ALIGN, WT_VEL_MTF) against the tradier fast baseline.
    Each feature swept independently vs baseline, then top combos combined.
    ~56 configs × 12 fast symbols first, promote winners to --symbols all.
    Run on S2: python v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier stock_exit_v1 --workers 6 --stream --min-csv-sharpe 0.0 --kill-secs 999999 --kill-sharpe 0"""
    base = {
        "MIN_HOLD_BARS": 10,
        "WT_EXIT_MIN_TFS": 3,
        "STRUCTURAL_RANGE_SHIFT_EXIT": True,
        "CT_WT_VELOCITY_GATE_ENABLED": True,
        "CT_WT_VELOCITY_1H_MIN": 2.0,
        "WINNER_PROTECT_ENABLED": True,
        "WINNER_PROTECT_GAIN_PCT": 1.0,
        "WT_VEL_FLOOR_EXIT_ENABLED": False,
        "KD_WT1H_EXIT_ENABLED": False,
        "ADAPTIVE_EXIT_TFS_ENABLED": False,
        "WT_ALIGN_EXIT_ENABLED": False,
        "WT_VEL_MTF_EXIT_ENABLED": False,
        "EARLY_ABORT_MIN_SYMBOLS": 6,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = [dict(base)]  # config 0 = baseline
    # WT velocity floor: exit when wt_velocity_1h crosses below threshold
    for thr in [0.0, 0.5, 1.0]:
        configs.append({**base, "WT_VEL_FLOOR_EXIT_ENABLED": True, "WT_VEL_FLOOR_EXIT_LONG_MAX": thr})
    # k_D overbought + wt_1h bear: daily exhaustion confirmed by 1h turn
    for kd_thr in [70.0, 75.0, 80.0]:
        configs.append({**base, "KD_WT1H_EXIT_ENABLED": True, "KD_WT1H_EXIT_OVERBOUGHT": kd_thr})
    # Adaptive TFS: faster exit when deep in profit
    for gain_pct in [1.0, 1.5, 2.0, 3.0]:
        configs.append({**base, "ADAPTIVE_EXIT_TFS_ENABLED": True, "ADAPTIVE_EXIT_TFS_GAIN_PCT": gain_pct})
    # WT alignment exit (existing, untested on stocks)
    for al_min in [1, 2]:
        configs.append({**base, "WT_ALIGN_EXIT_ENABLED": True, "WT_ALIGN_EXIT_MIN": al_min})
    # WT velocity MTF (existing, untested on stocks)
    for v_tfs in [2, 3]:
        for v_thr in [-0.5, -1.0]:
            configs.append({**base, "WT_VEL_MTF_EXIT_ENABLED": True, "WT_VEL_MTF_EXIT_MIN_TFS": v_tfs, "WT_VEL_MTF_EXIT_THRESHOLD": v_thr})
    # Combinations of top candidates
    for kd_thr in [70.0, 75.0]:
        for gain_pct in [1.5, 2.0]:
            configs.append({**base, "KD_WT1H_EXIT_ENABLED": True, "KD_WT1H_EXIT_OVERBOUGHT": kd_thr, "ADAPTIVE_EXIT_TFS_ENABLED": True, "ADAPTIVE_EXIT_TFS_GAIN_PCT": gain_pct})
    for thr in [0.0, 0.5]:
        for gain_pct in [1.5, 2.0]:
            configs.append({**base, "WT_VEL_FLOOR_EXIT_ENABLED": True, "WT_VEL_FLOOR_EXIT_LONG_MAX": thr, "ADAPTIVE_EXIT_TFS_ENABLED": True, "ADAPTIVE_EXIT_TFS_GAIN_PCT": gain_pct})
    for kd_thr in [70.0, 75.0]:
        configs.append({**base, "KD_WT1H_EXIT_ENABLED": True, "KD_WT1H_EXIT_OVERBOUGHT": kd_thr, "WT_ALIGN_EXIT_ENABLED": True, "WT_ALIGN_EXIT_MIN": 1})
    return configs


def build_param_grid_crypto_exit_v1():
    """2026-04-20: Technical exit sweep for crypto targeting Sharpe>2.5.
    Same 3 new exits + 2 existing untested exits against the vel=9/rally=30 crypto baseline.
    ~46 configs × 48 symbols, 4yr. ~15min on S1 with 6 workers.
    Run on S1: python v8_quick_sweep.py --mode crypto --symbols all --start 2021-01-01 --tier crypto_exit_v1 --workers 6 --stream --min-csv-sharpe 0.0 --kill-secs 999999 --kill-sharpe 0"""
    base = {
        "CT_WT_VELOCITY_GATE_ENABLED": True,
        "CT_WT_VELOCITY_1H_MIN": 9.0,
        "REENTRY_RALLY_K15M_MAX": 30.0,
        "MIN_HOLD_BARS": 250,
        "WT_EXIT_MIN_TFS": 3,
        "WINNER_PROTECT_ENABLED": True,
        "WINNER_PROTECT_GAIN_PCT": 1.0,
        "WT_VEL_FLOOR_EXIT_ENABLED": False,
        "KD_WT1H_EXIT_ENABLED": False,
        "ADAPTIVE_EXIT_TFS_ENABLED": False,
        "WT_ALIGN_EXIT_ENABLED": False,
        "WT_VEL_MTF_EXIT_ENABLED": False,
        "EARLY_ABORT_MIN_SYMBOLS": 12,
        "EARLY_ABORT_SHARPE_FLOOR": 1.5,
    }
    configs = [dict(base)]  # config 0 = baseline
    # WT velocity floor
    for thr in [0.0, 0.5, 1.0]:
        configs.append({**base, "WT_VEL_FLOOR_EXIT_ENABLED": True, "WT_VEL_FLOOR_EXIT_LONG_MAX": thr})
    # k_D overbought + wt_1h bear
    for kd_thr in [70.0, 75.0, 80.0]:
        configs.append({**base, "KD_WT1H_EXIT_ENABLED": True, "KD_WT1H_EXIT_OVERBOUGHT": kd_thr})
    # Adaptive TFS
    for gain_pct in [1.0, 1.5, 2.0, 3.0]:
        configs.append({**base, "ADAPTIVE_EXIT_TFS_ENABLED": True, "ADAPTIVE_EXIT_TFS_GAIN_PCT": gain_pct})
    # WT alignment exit
    for al_min in [1, 2]:
        configs.append({**base, "WT_ALIGN_EXIT_ENABLED": True, "WT_ALIGN_EXIT_MIN": al_min})
    # WT velocity MTF
    for v_tfs in [2, 3]:
        for v_thr in [-0.5, -1.0]:
            configs.append({**base, "WT_VEL_MTF_EXIT_ENABLED": True, "WT_VEL_MTF_EXIT_MIN_TFS": v_tfs, "WT_VEL_MTF_EXIT_THRESHOLD": v_thr})
    # Combinations of top candidates
    for kd_thr in [70.0, 75.0]:
        for gain_pct in [1.5, 2.0]:
            configs.append({**base, "KD_WT1H_EXIT_ENABLED": True, "KD_WT1H_EXIT_OVERBOUGHT": kd_thr, "ADAPTIVE_EXIT_TFS_ENABLED": True, "ADAPTIVE_EXIT_TFS_GAIN_PCT": gain_pct})
    for thr in [0.0, 0.5]:
        for gain_pct in [1.5, 2.0]:
            configs.append({**base, "WT_VEL_FLOOR_EXIT_ENABLED": True, "WT_VEL_FLOOR_EXIT_LONG_MAX": thr, "ADAPTIVE_EXIT_TFS_ENABLED": True, "ADAPTIVE_EXIT_TFS_GAIN_PCT": gain_pct})
    for kd_thr in [70.0, 75.0]:
        configs.append({**base, "KD_WT1H_EXIT_ENABLED": True, "KD_WT1H_EXIT_OVERBOUGHT": kd_thr, "WT_ALIGN_EXIT_ENABLED": True, "WT_ALIGN_EXIT_MIN": 1})
    return configs


def build_param_grid_le_full_tradier():
    """2026-04-20: LE full system sweep — tests the new tradier defaults where LOCAL_EXTREMES_SCORER_ENABLED=True
    and LE_TIER_SIZING_ENABLED=True are both on by default. This tier validates whether the score gate (15-pt
    minimum = 25 distinct indicator checks across Stoch/WT/DC/BB/MFI/HA/LR/VOL) and dollar-weighted position
    sizing (score>=75→8.33x, >=60→4.17x, >=45→1.67x, >=30→0.42x, >=15→0.083x vs $600 base) move Sharpe
    from the 0.41 baseline toward 2.5. Config 0 = baseline (all defaults, tier sizing on, score>=15).
    Run on S2: python v8_quick_sweep.py --mode tradier --symbols all --start 2022-01-01 --tier le_full_tradier --workers 6 --stream --min-csv-sharpe 0.0 --kill-secs 999999 --kill-sharpe 0"""
    base = {
        "LOCAL_EXTREMES_SCORER_ENABLED": True,
        "LOCAL_EXTREMES_MIN_SCORE": 15.0,
        "LE_TIER_SIZING_ENABLED": True,
        "MIN_HOLD_BARS": 4,
        "WT_EXIT_MIN_TFS": 3,
        "EARLY_ABORT_MIN_SYMBOLS": 6,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = [dict(base)]  # config 0 = new-default baseline
    configs.append({**base, "LE_TIER_SIZING_ENABLED": False})  # gate only, no weighting
    configs.append({**base, "LOCAL_EXTREMES_SCORER_ENABLED": False, "LE_TIER_SIZING_ENABLED": False})  # old baseline
    for min_score in [20.0, 25.0, 30.0, 40.0, 50.0]:
        configs.append({**base, "LOCAL_EXTREMES_MIN_SCORE": min_score})
        configs.append({**base, "LOCAL_EXTREMES_MIN_SCORE": min_score, "LE_TIER_SIZING_ENABLED": False})
    for min_score in [20.0, 30.0]:
        configs.append({**base, "LOCAL_EXTREMES_MIN_SCORE": min_score, "WINNER_PROTECT_ENABLED": True, "WINNER_PROTECT_GAIN_PCT": 1.5})
    for min_hold in [4, 8, 16]:
        configs.append({**base, "LOCAL_EXTREMES_MIN_SCORE": 25.0, "MIN_HOLD_BARS": min_hold})
    return configs


def build_param_grid_le_k1h_rising():
    """2026-04-20 v2: K1H_RISING_GATE sweep — FIX: roll by 12 bars (1 full 1h period on 5m data).
    Previous version used roll(1) which compared to the same value 11/12 times since k_1h only
    changes once per 12 bars → killed 98% of trades. Now correctly checks k_1h vs 12 bars ago.
    Config 0 = LE baseline (no gate). Configs 1-5: rising gate at k_1h < 60/50/45/40/35.
    Configs 6-10: zone-only at same thresholds (isolates rising vs zone contribution).
    Configs 11-16: rising gate at 60/50/45 + varied score mins 15/25.
    Configs 17-20: rising gate at 60/50 + profit targets 1%/2%.
    Run on S2: python v8_quick_sweep.py --mode tradier --symbols all --start 2022-01-01 --tier le_k1h_rising --workers 6 --stream --min-csv-sharpe 0.0 --kill-secs 999999 --kill-sharpe 0"""
    base = {
        "LOCAL_EXTREMES_SCORER_ENABLED": True,
        "LOCAL_EXTREMES_MIN_SCORE": 15.0,
        "LE_TIER_SIZING_ENABLED": False,
        "K1H_RISING_GATE_ENABLED": False,
        "K1H_RISING_LONG_MAX": 60.0,
        "MIN_HOLD_BARS": 4,
        "WT_EXIT_MIN_TFS": 3,
        "EARLY_ABORT_MIN_SYMBOLS": 6,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = [dict(base)]  # config 0 = LE gate-only baseline (no k1h gate, no tier sizing)
    # Rising gate (k_1h < thresh AND higher than 12 bars ago)
    for thresh in [60.0, 50.0, 45.0, 40.0, 35.0]:
        configs.append({**base, "K1H_RISING_GATE_ENABLED": True, "K1H_RISING_LONG_MAX": thresh})
    # Zone-only (threshold but no rising requirement) — isolates rising benefit
    for thresh in [60.0, 50.0, 45.0, 40.0, 35.0]:
        configs.append({**base, "K1H_RISING_GATE_ENABLED": False, "K1H_RISING_LONG_MAX": thresh})
    # Rising gate + varied score minimums
    for thresh in [60.0, 50.0, 45.0]:
        for min_score in [15.0, 25.0]:
            configs.append({**base, "K1H_RISING_GATE_ENABLED": True, "K1H_RISING_LONG_MAX": thresh,
                            "LOCAL_EXTREMES_MIN_SCORE": min_score})
    return configs


def build_param_grid_le_partial_exit_tradier():
    """2026-04-20: Scale-out model with proper WT cross events for 15m+ TFs.
    WT_EXIT_USE_CROSS_EVENTS=True: use wt_cross_bear/bull_15m/1h fields (fires at turn only,
    for all 5m bars within the cross candle — 3 bars at 15m, 12 bars at 1h for stocks).
    Also sweeps: first-take %, trail arm %, remainder TFS, cross_events on/off.
    Run: --mode tradier --symbols fast --start 2024-01-01 --tier le_partial_exit_tradier --workers 6 --stream --kill-sharpe 0 --kill-secs 999999
    """
    base = {
        "LOCAL_EXTREMES_SCORER_ENABLED": True,
        "LOCAL_EXTREMES_MIN_SCORE": 45.0,
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": True,
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": 40.0,
        "DYNAMIC_SCORE_AUGMENT_ENABLED": False,
        "MIN_HOLD_BARS": 20,
        "WT_EXIT_MIN_TFS": 3,
        "EARLY_ABORT_MIN_SYMBOLS": 6,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = []
    # Baselines: no partial exit, both cross_events on and off for comparison
    configs.append({**base, "PARTIAL_EXIT_ENABLED": False, "WT_EXIT_USE_CROSS_EVENTS": False})
    configs.append({**base, "PARTIAL_EXIT_ENABLED": False, "WT_EXIT_USE_CROSS_EVENTS": True})
    # Partial exit variants — cross_events=True (the correct version)
    for use_cross in [True, False]:
        for pe_pct in [0.3, 0.5, 0.7]:
            for trail_arm in [0.5, 0.7, 1.0]:
                if trail_arm < pe_pct:
                    continue
                for trail_floor in [pe_pct * 0.8, pe_pct]:
                    for rem_tfs in [1, 2, 3]:
                        configs.append({**base,
                            "PARTIAL_EXIT_ENABLED": True,
                            "PARTIAL_EXIT_FRAC": 0.5,
                            "PARTIAL_EXIT_PCT": pe_pct,
                            "PARTIAL_TRAIL_ARM_PCT": trail_arm,
                            "PARTIAL_TRAIL_FLOOR_PCT": trail_floor,
                            "PARTIAL_REMAINDER_EXIT_TFS": rem_tfs,
                            "WT_EXIT_USE_CROSS_EVENTS": use_cross,
                        })
    return configs


def build_param_grid_le_partial_exit_tradier_validate():
    """2026-04-20: Full 128-symbol validation of partial exit winners.
    Update with top configs from fast sweep before running.
    Run: --mode tradier --symbols all --start 2024-01-01 --tier le_partial_exit_tradier_validate --workers 6 --stream --kill-sharpe 0 --kill-secs 999999
    """
    base = {
        "LOCAL_EXTREMES_SCORER_ENABLED": True,
        "LOCAL_EXTREMES_MIN_SCORE": 45.0,
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": True,
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": 40.0,
        "DYNAMIC_SCORE_AUGMENT_ENABLED": False,
        "MIN_HOLD_BARS": 20,
        "WT_EXIT_MIN_TFS": 3,
        "EARLY_ABORT_MIN_SYMBOLS": 999,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = [
        {**base, "PARTIAL_EXIT_ENABLED": False},  # v2 winner baseline
    ]
    # Top candidates — update after fast sweep
    for pe_pct in [0.3, 0.5]:
        for trail_arm in [0.5, 0.7]:
            for trail_floor in [pe_pct]:
                for rem_tfs in [1, 2]:
                    configs.append({**base,
                        "PARTIAL_EXIT_ENABLED": True,
                        "PARTIAL_EXIT_FRAC": 0.5,
                        "PARTIAL_EXIT_PCT": pe_pct,
                        "PARTIAL_TRAIL_ARM_PCT": trail_arm,
                        "PARTIAL_TRAIL_FLOOR_PCT": trail_floor,
                        "PARTIAL_REMAINDER_EXIT_TFS": rem_tfs,
                    })
    return configs


def build_param_grid_le_partial_exit_crypto():
    """2026-04-20: Partial scale-out sweep for crypto using velocity-gate baseline (Sharpe ~2.55).
    Cross events expanded to full candle (5 bars at 15m, 20 bars at 1h for 3m base TF).
    NOTE: LOCAL_EXTREMES_SCORER uses 5m fields — incompatible with crypto 3m NPZ.
    Uses velocity gate instead: CT_WT_VELOCITY_1H_MIN=8.0, MIN_HOLD_BARS=250.
    Run on S1: --mode crypto --symbols fast --start 2022-01-01 --tier le_partial_exit_crypto --workers 6 --stream --kill-sharpe 0 --kill-secs 999999
    """
    base = {
        "CT_WT_VELOCITY_GATE_ENABLED": True,
        "CT_WT_VELOCITY_1H_MIN": 8.0,
        "REENTRY_RALLY_K15M_MAX": 100.0,
        "MIN_HOLD_BARS": 250,
        "WT_EXIT_MIN_TFS": 3,
        "WINNER_PROTECT_ENABLED": False,
        "EARLY_ABORT_MIN_SYMBOLS": 6,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = []
    configs.append({**base, "PARTIAL_EXIT_ENABLED": False, "WT_EXIT_USE_CROSS_EVENTS": False})
    configs.append({**base, "PARTIAL_EXIT_ENABLED": False, "WT_EXIT_USE_CROSS_EVENTS": True})
    for use_cross in [True, False]:
        for pe_pct in [0.5, 0.7, 1.0]:
            for trail_arm in [0.7, 1.0, 1.3]:
                if trail_arm < pe_pct:
                    continue
                for trail_floor in [pe_pct * 0.8, pe_pct]:
                    for rem_tfs in [2, 3]:
                        configs.append({**base,
                            "PARTIAL_EXIT_ENABLED": True,
                            "PARTIAL_EXIT_FRAC": 0.5,
                            "PARTIAL_EXIT_PCT": pe_pct,
                            "PARTIAL_TRAIL_ARM_PCT": trail_arm,
                            "PARTIAL_TRAIL_FLOOR_PCT": trail_floor,
                            "PARTIAL_REMAINDER_EXIT_TFS": rem_tfs,
                            "WT_EXIT_USE_CROSS_EVENTS": use_cross,
                        })
    return configs


def build_param_grid_le_partial_exit_crypto_validate():
    """2026-04-20: Full 48-symbol validation of crypto partial exit winners.
    Run on S1: --mode crypto --symbols all --start 2021-01-01 --tier le_partial_exit_crypto_validate --workers 6 --stream --kill-sharpe 0 --kill-secs 999999
    """
    base = {
        "CT_WT_VELOCITY_GATE_ENABLED": True,
        "CT_WT_VELOCITY_1H_MIN": 8.0,
        "REENTRY_RALLY_K15M_MAX": 100.0,
        "MIN_HOLD_BARS": 250,
        "WT_EXIT_MIN_TFS": 3,
        "WINNER_PROTECT_ENABLED": False,
        "EARLY_ABORT_MIN_SYMBOLS": 999,
        "EARLY_ABORT_SHARPE_FLOOR": 0.0,
        "EARLY_ABORT_TIME_LIMIT_SEC": 999999.0,
    }
    configs = [{**base, "PARTIAL_EXIT_ENABLED": False}]
    for pe_pct in [0.7, 1.0]:
        for trail_arm in [1.0, 1.3]:
            if trail_arm < pe_pct:
                continue
            for rem_tfs in [2, 3]:
                configs.append({**base,
                    "PARTIAL_EXIT_ENABLED": True,
                    "PARTIAL_EXIT_FRAC": 0.5,
                    "PARTIAL_EXIT_PCT": pe_pct,
                    "PARTIAL_TRAIL_ARM_PCT": trail_arm,
                    "PARTIAL_TRAIL_FLOOR_PCT": pe_pct,
                    "PARTIAL_REMAINDER_EXIT_TFS": rem_tfs,
                    "WT_EXIT_USE_CROSS_EVENTS": True,
                })
    return configs


def build_param_grid_hedge_wt_kill():
    """2026-04-20: Test HEDGE_WT_KILL confirmation TF: none (3m only) vs 15m vs 1h.
    Rule: kill hedge when loser's WT recovers. This sweep asks: which TF confirmation is best?
    3 configs × 48 symbols × 4yr — very fast. Run on S1.
    Run: python v8_quick_sweep.py --mode crypto --symbols all --start 2021-01-01 --tier hedge_wt_kill --workers 6 --stream"""
    base = {
        "CT_WT_VELOCITY_GATE_ENABLED": True,
        "CT_WT_VELOCITY_1H_MIN": 9.0,
        "REENTRY_RALLY_K15M_MAX": 30.0,
        "MIN_HOLD_BARS": 250,
        "WT_EXIT_MIN_TFS": 3,
        "HEDGE_ENABLED": True,
        "HEDGE_MIN_HOLD_BARS": 10,
        "EARLY_ABORT_MIN_SYMBOLS": 12,
        "EARLY_ABORT_SHARPE_FLOOR": 1.5,
    }
    configs = []
    for tf in ['none', '15m', '1h']:
        configs.append({**base, "HEDGE_WT_KILL_CONFIRM_TF": tf})
    return configs


TIER_MAP = {
    "entry_gates": build_param_grid_entry_gates,
    "exit_tuning": build_param_grid_exit_tuning,
    "full": build_param_grid_full,
    "v3_core": build_param_grid_v3_core,
    "tradier_core": build_param_grid_tradier_core,
    "breakout_multi_lung": build_param_grid_breakout_multi_lung,
    "breakout_multi_lung_tradier": build_param_grid_breakout_multi_lung_tradier,
    "sharpe3_tradier": build_param_grid_sharpe3_tradier,
    "reentry_sharpe_push": build_param_grid_reentry_sharpe_push,
    "reentry_sharpe_push_wide": build_param_grid_reentry_sharpe_push_wide,
    "indicator_audit": build_param_grid_indicator_audit,
    "mega": build_param_grid_mega,
    "mega_v2": build_param_grid_mega_v2,
    "mega_v3": build_param_grid_mega_v3,
    "mega_v4": build_param_grid_mega_v4,
    "mega_stock": build_param_grid_mega_stock,
    "stock_v2": build_param_grid_stock_v2,
    "stock_v3": build_param_grid_stock_v3,
    "stock_v4": build_param_grid_stock_v4,
    "stock_v5": build_param_grid_stock_v5,
    "stock_v6": build_param_grid_stock_v6,
    "stock_v7": build_param_grid_stock_v7,
    "stock_champion": build_param_grid_stock_champion,
    "stock_validate": build_param_grid_stock_validate,
    "stock_validate2": build_param_grid_stock_validate2,
    "mega_v5": build_param_grid_mega_v5,
    "mega_v6": build_param_grid_mega_v6,
    "stock_mega": build_param_grid_stock_mega,
    "hunt_crypto": build_param_grid_hunt_crypto,
    "hunt_stock": build_param_grid_hunt_stock,
    "stock_dc_hunt": build_param_grid_stock_dc_hunt,
    "stock_dc_wide": build_param_grid_stock_dc_wide,
    "stock_phase2": build_param_grid_stock_phase2,
    "stock_phase3": build_param_grid_stock_phase3,
    "stock_phase4": build_param_grid_stock_phase4,
    "stock_phase5": build_param_grid_stock_phase5,
    "stock_phase6": build_param_grid_stock_phase6,
    "stock_phase7": build_param_grid_stock_phase7,
    "mega_v7": build_param_grid_mega_v7,
    "stock_sweep_v1": build_param_grid_stock_sweep_v1,
    "baseline255_ablation": build_param_grid_baseline255_ablation,
    "exit_wt_audit": build_param_grid_exit_wt_audit,
    "exit_wt_phase2": build_param_grid_exit_wt_phase2,
    "exit_wt_48sym": build_param_grid_exit_wt_48sym,
    "mega_crypto_v8": build_param_grid_mega_crypto_v8,
    "mega_crypto_v8_a1234": build_param_grid_mega_crypto_v8_a1234,
    "mega_tradier_v8_a134": build_param_grid_mega_tradier_v8_a134,
    "mega_tradier_v8": build_param_grid_mega_tradier_v8,
    "mega_tradier_v8_focused": build_param_grid_mega_tradier_v8_focused,
    "rz_exit_sweep": build_param_grid_rz_exit_sweep,
    "rz_breakout_tradier": build_param_grid_rz_breakout_tradier,
    "rz_noloss_mode": build_param_grid_rz_noloss_mode,
    "crypto_validate_top": build_param_grid_crypto_validate_top,
    "local_extremes_tradier": build_param_grid_local_extremes_tradier,
    "local_extremes_tradier_scorer": build_param_grid_local_extremes_tradier_scorer,
    "local_extremes_tradier_validate": build_param_grid_local_extremes_tradier_validate,
    "dc_low4_bypass_tradier": build_param_grid_dc_low4_bypass_tradier,
    "dc_low4_bypass_crypto": build_param_grid_dc_low4_bypass_crypto,
    "dc_low_tf_tradier": build_param_grid_dc_low_tf_tradier,
    "dc_low_tf_crypto": build_param_grid_dc_low_tf_crypto,
    "le_dynamic_tradier": build_param_grid_le_dynamic_tradier,
    "le_dynamic_tradier_validate": build_param_grid_le_dynamic_tradier_validate,
    "le_dynamic_tradier_v2": build_param_grid_le_dynamic_tradier_v2,
    "le_dynamic_tradier_v2_validate": build_param_grid_le_dynamic_tradier_v2_validate,
    "le_dynamic_tradier_v2_validate_ea": build_param_grid_le_dynamic_tradier_v2_validate_ea,
    "le_partial_exit_tradier": build_param_grid_le_partial_exit_tradier,
    "le_partial_exit_tradier_validate": build_param_grid_le_partial_exit_tradier_validate,
    "le_partial_exit_crypto": build_param_grid_le_partial_exit_crypto,
    "le_partial_exit_crypto_validate": build_param_grid_le_partial_exit_crypto_validate,
    "stock_wt_d_aug": build_param_grid_stock_wt_d_aug,
    "stock_wt_d_aug_pt": build_param_grid_stock_wt_d_aug_pt,
    "stock_60min_reentry": build_param_grid_stock_60min_reentry,
    "crypto_wt_d_4h_aug": build_param_grid_crypto_wt_d_4h_aug,
    "crypto_vel_sweep": build_param_grid_crypto_vel_sweep,
    "stock_exit_v1": build_param_grid_stock_exit_v1,
    "crypto_exit_v1": build_param_grid_crypto_exit_v1,
    "le_full_tradier": build_param_grid_le_full_tradier,
    "le_k1h_rising": build_param_grid_le_k1h_rising,
    "dc_breakout_failed_tradier": build_param_grid_dc_breakout_failed_tradier,
    "dc_breakout_failed_crypto": build_param_grid_dc_breakout_failed_crypto,
    "all_tf_brake_tradier": build_param_grid_all_tf_brake_tradier,
    "all_tf_brake_crypto": build_param_grid_all_tf_brake_crypto,
    "ratio_sentiment_tradier": build_param_grid_ratio_sentiment_tradier,
    "ratio_sentiment_crypto": build_param_grid_ratio_sentiment_crypto,
    "hedge_wt_kill": build_param_grid_hedge_wt_kill,
}


def grid_to_configs(grid) -> list:
    """Expand parameter grid to list of config dicts. Accepts pre-built list of dicts too."""
    if isinstance(grid, list):
        return grid
    keys = sorted(grid.keys())
    values = [grid[k] for k in keys]
    configs = []
    for combo in itertools.product(*values):
        cfg = dict(zip(keys, combo))
        configs.append(cfg)
    return configs


def grid_sample_random(grid, n: int, rng=None) -> list:
    """Sample n random configs from a dict grid without expanding the full cartesian product.
    Safe for trillion-combo grids. Each config is a fresh random draw — may have duplicates
    in theory but negligibly rare at n << grid_size."""
    if isinstance(grid, list):
        result = list(grid)
        if rng:
            rng.shuffle(result)
        else:
            random.shuffle(result)
        return result[:n]
    keys = sorted(grid.keys())
    values = [grid[k] for k in keys]
    _rand = rng.choice if rng else random.choice
    configs = []
    for _ in range(n):
        combo = tuple(_rand(v) for v in values)
        configs.append(dict(zip(keys, combo)))
    return configs


def config_hash(cfg: dict) -> str:
    return hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:8]


def run_one_config(args_tuple):
    """Worker function — runs one config on pre-loaded NPZ stores."""
    npz_dir, mode, symbols_list, start_date, cfg_dict, run_id = args_tuple
    stores = load_npz(mode, symbols_list, start_date, npz_dir)
    if not stores:
        return {"run_id": run_id, "sharpe": 0, "pnl": 0, "trades": 0, "wins": 0, "losses": 0, "status": "no_data", "config": cfg_dict}
    return _run_config_with_stores(stores, mode, cfg_dict, run_id)


def _run_config_with_stores(stores, mode, cfg_dict, run_id):
    """Inner: run one config given already-loaded NPZ stores (avoids reload per config)."""
    cfg = QuickConfig()
    cfg.MODE = mode
    if mode == "tradier":
        cfg.apply_tradier_defaults()
    for k, v in cfg_dict.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    if mode == "tradier" and cfg.STRUCTURAL_RANGE_SHIFT_TF == "dc_4h":
        cfg.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
    # Force honest exits unless the tier grid explicitly tests NOLOSS=True.
    # QuickConfig defaults NOLOSS_ENABLED=True (matches live), but sweeps must be honest:
    # with NOLOSS=True, losing positions never close → WR=95%+ artifact, Sharpe inflated.
    if mode == "tradier" and "NOLOSS_ENABLED" not in cfg_dict:
        cfg.NOLOSS_ENABLED = False
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
    parser.add_argument("--stream", action="store_true",
                        help="Stream NPZ one symbol at a time per config (low RAM, allows many workers)")
    parser.add_argument("--tier", type=str, default="entry_gates", choices=list(TIER_MAP.keys()))
    parser.add_argument("--npz-dir", type=str, default="")
    parser.add_argument("--limit", type=int, default=0, help="Max configs to run (0=all)")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--kill-sharpe", type=float, default=2.5,
                        help="Abort sweep if best Sharpe stays below this after --kill-secs seconds (default 2.5)")
    parser.add_argument("--kill-warmup", type=int, default=999999,
                        help="Legacy count-based kill warmup (default 999999 = disabled; use --kill-secs instead)")
    parser.add_argument("--kill-secs", type=float, default=60.0,
                        help="Abort sweep if best Sharpe < kill-sharpe after this many wall-clock seconds (default 60)")
    parser.add_argument("--min-csv-sharpe", type=float, default=0.0,
                        help="Only write rows to CSV if sharpe >= this (default 0.0 = write all)")
    parser.add_argument("--dead-log-path", type=str, default="",
                        help="Path to dead-log CSV for configs below --min-csv-sharpe. Use 'auto' to auto-name beside main CSV.")
    parser.add_argument("--shuffle", action="store_true",
                        help="Randomize config order before running (avoids dead zones in grid)")
    parser.add_argument("--max-configs", type=int, default=0,
                        help="Stop after testing this many configs (0 = unlimited). Use with --shuffle for random sampling.")
    parser.add_argument("--target-winners", type=int, default=0,
                        help="Keep sampling (re-shuffling) until N configs with pool_sharpe >= --min-csv-sharpe are saved. 0 = disabled.")
    parser.add_argument("--winner-floor", type=float, default=0.0,
                        help="pool_sharpe threshold to count as a winner for --target-winners (default: same as --min-csv-sharpe).")
    args = parser.parse_args()

    symbols_list = None
    if args.symbols == "fast":
        symbols_list = (FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO).split(",")
    elif args.symbols == "medium":
        symbols_list = (MEDIUM_SYMBOLS_TRADIER if args.mode == "tradier" else MEDIUM_SYMBOLS_CRYPTO).split(",")
    elif args.symbols and args.symbols != "all":
        # "all" → symbols_list stays None → iter_npz/load_npz auto-filters by mode
        symbols_list = [s.strip() for s in args.symbols.split(",") if s.strip()]

    npz_dir = args.npz_dir
    if not npz_dir:
        if args.mode == "tradier":
            tradier_candidates = [
                BASE_PATH / "backtest_v8" / "indicators",
                BASE_PATH / "backtest_v8" / "indicators_tradier",
                BASE_PATH / "backtest_v5" / "indicators_5m_tradier",
                BASE_PATH / "backtest_v4_tradier" / "indicators",
            ]
            for d in tradier_candidates:
                if d.exists() and any(d.glob("*.npz")):
                    npz_dir = str(d)
                    break
        if not npz_dir:
            for prefix in ["backtest_v8", "backtest_v7"]:
                d = BASE_PATH / prefix / "indicators"
                if d.exists() and any(d.glob("*.npz")):
                    npz_dir = str(d)
                    break

    raw_grid = TIER_MAP[args.tier]()
    # Compute grid size without expanding (may be trillions)
    if isinstance(raw_grid, list):
        grid_size = len(raw_grid)
    else:
        grid_size = 1
        for v in raw_grid.values():
            grid_size *= len(v)

    target_winners = args.target_winners
    winner_floor = args.winner_floor if args.winner_floor > 0 else args.min_csv_sharpe
    if target_winners > 0 and winner_floor <= 0:
        winner_floor = 4.0

    # For target-winners mode with huge grids, sample randomly per pass instead of expanding
    USE_SAMPLING = (target_winners > 0 and grid_size > 500_000) or (grid_size > 10_000_000)
    batch_size = args.max_configs if args.max_configs > 0 else min(2000, max(200, grid_size // 1000))

    if USE_SAMPLING:
        # Only sample a batch for bookkeeping purposes — actual expansion happens per-pass below
        sample_cfg = grid_sample_random(raw_grid, 1)
        cfg_keys = sorted(sample_cfg[0].keys()) if sample_cfg else []
        total = grid_size
    else:
        configs = grid_to_configs(raw_grid)
        if args.limit > 0:
            configs = configs[:args.limit]
        cfg_keys = sorted(configs[0].keys()) if configs else []
        total = len(configs)

    syms_tag = f"_{len(symbols_list)}sym" if symbols_list else ""
    csv_name = f"v8_quick_{args.mode}_{args.tier}{syms_tag}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    csv_path = BASE_PATH / "data" / "sweep_results" / csv_name
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    dead_log_path = None
    if args.dead_log_path:
        if args.dead_log_path == "auto":
            dead_name = csv_name.replace("v8_quick_", "v8_dead_")
            dead_log_path = BASE_PATH / "data" / "sweep_results" / dead_name
        else:
            dead_log_path = Path(args.dead_log_path)
        dead_log_path.parent.mkdir(parents=True, exist_ok=True)

    done_hashes = set()
    if args.resume and csv_path.exists():
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("status") in ("ok", "useless", "no_trades"):
                    done_hashes.add(row.get("config_hash", ""))

    total_str = f"{total:,}" if total > 100_000 else str(total)
    print(f"\nV8 Quick Sweep: {args.mode} | tier={args.tier} | {total_str} configs | workers={args.workers}")
    if USE_SAMPLING:
        print(f"SAMPLING mode: batch_size={batch_size} per pass (grid too large to expand)")
    if target_winners > 0:
        print(f"TARGET-WINNERS mode: collect {target_winners} results with pool_sharpe >= {winner_floor:.2f}")
    print(f"CSV: {csv_path}\n")

    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    fieldnames = ["run_id", "config_hash", "sharpe", "pool_sharpe", "sharpe_min", "sharpe_p25", "sharpe_med", "sharpe_p75", "sharpe_max", "syms_with_sharpe", "syms_excluded", "pnl", "accumulated_gain_pct", "max_dd_pct", "avg_dd_pct", "trades", "wins", "losses", "wr", "avg_pnl_pct", "elapsed", "status", "early_abort", "symbols_used"]
    for k in cfg_keys:
        fieldnames.append(f"cfg_{k}")

    completed = 0
    winners_found = 0
    best_sharpe = -999
    t_start = time.time()
    pass_num = 0
    _sw_rg_disabled = os.environ.get("V8_RATE_GUARD_DISABLED", "0") == "1"
    _sw_n_syms_guess = max(1, len(symbols_list) if symbols_list else 12)
    _sw_rg = None if _sw_rg_disabled else RateGuard(n_accts=_sw_n_syms_guess, label=f"v8_quick_sweep.{args.mode}.{args.tier}")
    _sw_total_trades = 0

    # Count existing winners from a resumed CSV
    if args.resume and csv_path.exists():
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    if float(row.get("pool_sharpe", 0)) >= winner_floor:
                        winners_found += 1
                except (TypeError, ValueError):
                    pass
        if winners_found > 0:
            print(f"  Resume: {winners_found} winners already in CSV")

    dead_write_header = dead_log_path is not None and (not dead_log_path.exists() or dead_log_path.stat().st_size == 0)
    dead_csvfile = open(dead_log_path, "a", newline="") if dead_log_path else None
    dead_writer = csv.DictWriter(dead_csvfile, fieldnames=fieldnames) if dead_csvfile else None
    if dead_write_header and dead_writer:
        dead_writer.writeheader()

    with open(csv_path, "a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        def _process_result(result, todo_len):
            nonlocal completed, best_sharpe, winners_found, _sw_total_trades
            completed += 1
            _sw_total_trades += int(result.get("trades", 0) or 0)
            if _sw_rg is not None:
                _sw_syms_used = max(1, int(result.get("symbols_used", _sw_n_syms_guess) or _sw_n_syms_guess))
                _sw_rg.n_accts = max(1, completed * _sw_syms_used)
                _sw_rg.tick(_sw_total_trades)
            cfg_dict = result.get("config", {})
            row = {
                "run_id": result["run_id"],
                "config_hash": config_hash(cfg_dict),
                "sharpe": result.get("sharpe", 0),
                "pool_sharpe": result.get("pool_sharpe", 0),
                "sharpe_min": result.get("sharpe_min", 0),
                "sharpe_p25": result.get("sharpe_p25", 0),
                "sharpe_med": result.get("sharpe_med", 0),
                "sharpe_p75": result.get("sharpe_p75", 0),
                "sharpe_max": result.get("sharpe_max", 0),
                "syms_with_sharpe": result.get("syms_with_sharpe", 0),
                "syms_excluded": result.get("syms_excluded", 0),
                "pnl": result.get("pnl", 0),
                "accumulated_gain_pct": result.get("accumulated_gain_pct", 0),
                "max_dd_pct": result.get("max_dd_pct", 0),
                "avg_dd_pct": result.get("avg_dd_pct", 0),
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
            s = result.get("sharpe", 0)
            ps = result.get("pool_sharpe", 0)
            if ps > best_sharpe:
                best_sharpe = ps
            _min_trades_per_sym = int(result.get("symbols_used", 1) or 1) * 30
            if ps >= args.min_csv_sharpe and result.get("trades", 0) >= _min_trades_per_sym:  # pool_sharpe only, min 30 trades/symbol
                writer.writerow(row)
                csvfile.flush()
            elif dead_writer and result.get("status") not in ("error", "mode_skip"):
                dead_writer.writerow(row)
                dead_csvfile.flush()
            if ps >= winner_floor:
                winners_found += 1
            elapsed_total = time.time() - t_start
            rate = completed / elapsed_total if elapsed_total > 0 else 0
            eta = (todo_len - completed) / rate / 3600 if rate > 0 else 0
            winner_str = f" winners={winners_found}" if target_winners > 0 else ""
            if completed % 10 == 0 or completed <= 5:
                print(f"  [{completed}/{todo_len}] sym_avg={s:.4f} pool={ps:.4f} trades={result.get('trades', 0)} "
                      f"best={best_sharpe:.4f}{winner_str} rate={rate:.1f}/s ETA={eta:.1f}h")
            return s

        def _run_pass(todo):
            if not todo:
                return
            if args.workers == 1:
                print(f"Loading NPZ data once (workers=1 fast path)...")
                stores = load_npz(args.mode, symbols_list, args.start, npz_dir)
                if not stores:
                    print("ERROR: no NPZ data loaded"); return
                print(f"Loaded {len(stores)} symbols. Running {len(todo)} configs in-process...")
                for t in todo:
                    _, mode_t, _, _, cfg_dict, run_id = t
                    try:
                        result = _run_config_with_stores(stores, mode_t, cfg_dict, run_id)
                    except Exception as e:
                        print(f"  ERROR: {e}"); continue
                    _process_result(result, len(todo))
                    if target_winners > 0 and winners_found >= target_winners:
                        break
                    if (time.time() - t_start) >= args.kill_secs and best_sharpe < args.kill_sharpe and target_winners == 0:
                        print(f"  KILL-RULE ({args.kill_secs:.0f}s): best_sharpe={best_sharpe:.4f} < {args.kill_sharpe} — moving on")
                        break
            else:
                if args.stream:
                    init_fn = _worker_init_streaming
                    worker_fn = run_one_config_streaming
                else:
                    init_fn = _worker_init_shared
                    worker_fn = run_one_config_shared
                try:
                    with ProcessPoolExecutor(
                        max_workers=args.workers,
                        initializer=init_fn,
                        initargs=(npz_dir, args.mode, symbols_list, args.start)
                    ) as executor:
                        futures = {executor.submit(worker_fn, (t[4], t[5])): t for t in todo}
                        for future in as_completed(futures):
                            try:
                                result = future.result()
                            except BrokenExecutor as e:
                                print(f"  BROKEN_POOL: {e} — restarting next pass")
                                break
                            except Exception as e:
                                print(f"  ERROR: {e}")
                                continue
                            _process_result(result, len(todo))
                            if target_winners > 0 and winners_found >= target_winners:
                                for f in futures:
                                    f.cancel()
                                break
                            if target_winners == 0 and (time.time() - t_start) >= args.kill_secs and best_sharpe < args.kill_sharpe:
                                print(f"  KILL-RULE ({args.kill_secs:.0f}s): best_sharpe={best_sharpe:.4f} < {args.kill_sharpe} — moving on")
                                for f in futures:
                                    f.cancel()
                                break
                except (BrokenExecutor, OSError) as e:
                    print(f"  POOL_CRASH: {e} — will retry next pass")

        if target_winners > 0:
            # TARGET-WINNERS mode: sample random batches until we hit the target.
            # For huge grids (USE_SAMPLING=True), each pass draws fresh random combos.
            # For small grids, shuffle and exhaust without repeating.
            seen_hashes = set(done_hashes)
            print(f"  Batch size per pass: {batch_size} configs.")
            while winners_found < target_winners:
                pass_num += 1
                if USE_SAMPLING:
                    sampled = grid_sample_random(raw_grid, batch_size)
                    batch = []
                    for i, cfg_dict in enumerate(sampled):
                        batch.append((npz_dir, args.mode, symbols_list, args.start, cfg_dict, f"q_{args.tier}_p{pass_num}_{i:05d}"))
                else:
                    shuffled = list(configs)
                    random.shuffle(shuffled)
                    batch = []
                    for i, cfg_dict in enumerate(shuffled):
                        h = config_hash(cfg_dict)
                        if h in seen_hashes:
                            continue
                        seen_hashes.add(h)
                        batch.append((npz_dir, args.mode, symbols_list, args.start, cfg_dict, f"q_{args.tier}_p{pass_num}_{i:05d}"))
                        if len(batch) >= batch_size:
                            break
                    if not batch:
                        seen_hashes = set()
                        print(f"  Pass {pass_num}: grid exhausted. Resetting for next pass.")
                        continue
                print(f"\nV8 Quick Sweep: {args.mode} | tier={args.tier} | {len(batch)} configs (pass {pass_num}) | workers={args.workers}")
                print(f"  Progress: {winners_found}/{target_winners} winners found so far")
                _run_pass(batch)
                if winners_found >= target_winners:
                    break
        else:
            # Standard mode: build todo from configs, run once
            todo = []
            if USE_SAMPLING:
                # Grid too large to expand in-memory; sample a random batch instead
                sampled = grid_sample_random(raw_grid, batch_size)
                for i, cfg_dict in enumerate(sampled):
                    todo.append((npz_dir, args.mode, symbols_list, args.start, cfg_dict, f"q_{args.tier}_{i:05d}"))
            else:
                for i, cfg_dict in enumerate(configs):
                    h = config_hash(cfg_dict)
                    if h in done_hashes:
                        continue
                    todo.append((npz_dir, args.mode, symbols_list, args.start, cfg_dict, f"q_{args.tier}_{i:05d}"))

            if args.shuffle:
                random.shuffle(todo)
            if args.max_configs > 0 and len(todo) > args.max_configs:
                todo = todo[:args.max_configs]

            skip = 0 if USE_SAMPLING else (total - len(todo))
            sample_str = f" (sampling {len(todo)}/{total:,} random)" if USE_SAMPLING else f" ({skip} done, {len(todo)} todo)"
            print(f"  {total:,} configs{sample_str}")
            if not todo:
                print("All configs done.")
                return
            _run_pass(todo)

    if dead_csvfile:
        dead_csvfile.close()

    if _sw_rg is not None and completed > 0:
        try:
            _sw_start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            _sw_per_cfg_days = max((datetime.now(timezone.utc) - _sw_start_dt).days, 1)
        except Exception:
            _sw_per_cfg_days = 365
        _sw_rg.n_accts = max(1, completed * _sw_n_syms_guess)
        _sw_rg.final_check(_sw_total_trades, test_window_days=_sw_per_cfg_days)

    winner_str = f"  Winners (sharpe>={winner_floor:.2f}): {winners_found}/{target_winners}\n" if target_winners > 0 else ""
    dead_str = f"  Dead log: {dead_log_path}\n" if dead_log_path else ""
    print(f"\n{'='*70}")
    print(f"  SWEEP COMPLETE — {completed} configs in {time.time()-t_start:.0f}s")
    print(f"  Best Sharpe: {best_sharpe:.4f}")
    print(f"{winner_str}{dead_str}  Results: {csv_path}")
    print(f"{'='*70}")
    print(f"\nV8_QUICK_SWEEP_DONE: configs={completed} best_sharpe={best_sharpe:.4f} winners={winners_found} csv={csv_path}")


if __name__ == "__main__":
    main()

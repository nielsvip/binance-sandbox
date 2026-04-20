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
    FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER
)

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



def build_param_grid_sharpe3_tradier():
    """504-config tradier sweep: TP capped at exact target (limit-order semantics) → low std → high Sharpe.
    Param order: ESThresh/TP vary fastest so kill-sharpe sees all combos in first 28 configs."""
    grid = {
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True],
        "CT_WT_VELOCITY_GATE_ENABLED": [True],
        "RZ_EXIT_ENABLED": [True],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
        "REENTRY_RALLY_K15M_MAX": [75.0],
        "PROFIT_TARGET_ENABLED": [True],
        "DELTA_ENTRY_ENABLED": [True],
        "REENTRY_RALLY_HTF_MIN": [1, 2, 3],
        "MIN_HOLD_BARS": [10, 20],
        "K3M_FLOOR": [20.0, 25.0, 30.0],
        "PROFIT_TARGET_PCT": [0.5, 0.8, 1.0, 1.3, 1.6, 2.0, 3.0],
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
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [1.6],
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
        "PROFIT_TARGET_PCT": [0.3, 0.5, 0.8, 1.2],
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
        "PROFIT_TARGET_PCT": [0.3, 0.5, 0.8, 1.0, 1.6],
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
        "PROFIT_TARGET_PCT": [0.3, 0.5, 0.8, 1.0, 1.5, 2.0],
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
        "PROFIT_TARGET_PCT": [0.2, 0.25, 0.3, 0.35, 0.4],
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
        "PROFIT_TARGET_PCT": [0.1, 0.15, 0.2, 0.25, 0.3, 0.35],
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
        "PROFIT_TARGET_PCT": [0.3, 0.5, 0.8, 1.0, 1.5],
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
        "PROFIT_TARGET_PCT": [0.05, 0.08, 0.1, 0.12, 0.15],
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
        "PROFIT_TARGET_PCT": [0.15, 0.2],
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
        "PROFIT_TARGET_PCT": [0.2, 0.22, 0.25, 0.27, 0.3],
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
        "PROFIT_TARGET_PCT": [0.2, 0.25, 0.3, 0.4, 0.5, 1.0],
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
        "PROFIT_TARGET_PCT": [0.15, 0.18, 0.20, 0.25],
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
        "PROFIT_TARGET_PCT": [0.1, 0.2, 0.3, 0.5, 1.0],
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
        "PROFIT_TARGET_PCT": [0.02],
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
        "PROFIT_TARGET_PCT": [0.015, 0.02, 0.025],
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
        "PROFIT_TARGET_PCT": [0.01, 0.015, 0.02, 0.025, 0.03],
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
        "PROFIT_TARGET_PCT": [0.02, 0.03, 0.04, 0.05, 0.06, 0.08],
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
        "PROFIT_TARGET_PCT": [0.15, 0.2, 0.25],
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
        "PROFIT_TARGET_PCT": [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5, 2.0],
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
        "PROFIT_TARGET_PCT": [0.14, 0.15, 0.16, 0.18],
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
        "PROFIT_TARGET_PCT": [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5, 2.0],
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
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [0.4, 0.5, 0.6, 0.7],
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
        "PROFIT_TARGET_ENABLED": [True, False],
        "PROFIT_TARGET_PCT": [0.3, 0.5, 1.0, 1.5],
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
        "PROFIT_TARGET_ENABLED": [True, False],
        "PROFIT_TARGET_PCT": [0.3, 0.5, 1.0],     # only matters when PT=True
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
        "PROFIT_TARGET_ENABLED": [False],
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
        "PROFIT_TARGET_ENABLED": [False],
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
        "PROFIT_TARGET_ENABLED": [False],
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
        "PROFIT_TARGET_ENABLED": [False],
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
        "PROFIT_TARGET_ENABLED": [False],
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
        "PROFIT_TARGET_PCT": [0.2, 0.25, 0.3, 0.4, 0.5, 0.7, 1.0],
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
        "MIN_HOLD_BARS": 250, "PROFIT_TARGET_ENABLED": True, "PROFIT_TARGET_PCT": 1.6,
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
    for pt in (0.8, 1.0, 1.2, 1.4, 1.8, 2.0, 2.5):
        configs.append({**baseline, "PROFIT_TARGET_PCT": pt})
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
        "MIN_HOLD_BARS": 10, "PROFIT_TARGET_ENABLED": False,
        "PROFIT_TARGET_PCT": 10.0, "WT_EXIT_MIN_TFS": 4,
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
        "PROFIT_TARGET_ENABLED": [False],
        "PROFIT_TARGET_PCT": [10.0],
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
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [1.4, 1.6, 1.8],
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
    """DC_RECOVERY_EXIT=True (correct NOLOSS: close stranded positions above dc_high_4h).
    VEL_GATE=False matches live default (CT_WT_VELOCITY_GATE_ENABLED=False in ez_manage line 194).
    Wider MIN_HOLD (20-300) + lower STRENGTH (1-5) to find entries that survive DC recovery.
    ~20k combos. Kill <3 Sharpe after 15s on 6 symbols. Save >=4.
    Run: --symbols fast --start 2022-01-01 --target-winners 10000 --min-csv-sharpe 4.0 --workers 12
    """
    return {
        "CT_WT_VELOCITY_GATE_ENABLED": [False],
        "MIN_HOLD_BARS": [20, 50, 100, 150, 200, 300],
        "PROFIT_TARGET_PCT": [0.5, 0.8, 1.0, 1.2, 1.6, 2.0],
        "STRENGTH_MIN_SCORE": [1.0, 2.0, 3.0, 4.0, 5.0],
        "REENTRY_RALLY_K15M_MAX": [40.0, 60.0, 80.0, 100.0],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "D_TREND_REQUIRED": [True, False],
        "HTF_MIN_ALIGNED": [1, 2],
        "DC_RECOVERY_EXIT_ENABLED": [True],
        "NOLOSS_ENABLED": [True],
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [3.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [15.0],
    }


def build_param_grid_mega_tradier_v8():
    """Tradier pre-screen: 12-symbol fast filter. Kill <2.0 Sharpe after 15s. Save >=4.0.
    PROFIT_TARGET_ENABLED=True so PT actually fires (apply_tradier_defaults sets it False).
    Target 10k winners on 12 symbols then promote to full validation.
    Run: --mode tradier --symbols fast --start 2024-01-01 --target-winners 10000 --min-csv-sharpe 4.0 --workers 8
    """
    return {
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5, 2.0],
        "MIN_HOLD_BARS": [4, 10, 20, 30, 40, 60, 80],
        "CT_WT_VELOCITY_GATE_ENABLED": [False, True],
        "CT_WT_VELOCITY_1H_MIN": [2.0, 4.0, 6.0, 8.0, 10.0],
        "WT_EXIT_MIN_TFS": [2, 3, 4],
        "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
        "CT_DC_CROSSOVER_SKIP_ENABLED": [True, False],
        "WINNER_PROTECT_ENABLED": [True, False],
        "STRENGTH_MIN_SCORE": [2.0, 4.0, 6.0],
        "D_TREND_REQUIRED": [True, False],
        "HTF_MIN_ALIGNED": [1, 2],
        "DC_RECOVERY_EXIT_ENABLED": [True, False],
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [2.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [15.0],
    }


def build_param_grid_local_extremes_tradier():
    """2026-04-20: Local bottom/top swing strategy for tradier stocks.
    LONG: enter when k_1h < ENTRY_ZONE_LONG (oversold = local bottom).
    SHORT: enter when k_1h > ENTRY_ZONE_SHORT (overbought = local top).
    EXIT: profit target fires when price reaches the "other extreme" + WT technical confirmation.
    Phase 1: find optimal oversold/overbought thresholds + position size tiers.
    Phase 2 (after winners): validate on full 128 symbols, then wire score-based $50-$5000 dynamic sizing.
    Tradier defaults auto-applied: K_ZONE_ENTRY_ENABLED=True, ENTRY_ZONE_K_TF=1h, HTF_MIN_ALIGNED=2.
    Run: --mode tradier --symbols fast --start 2024-01-01 --tier local_extremes_tradier --workers 8
    """
    return {
        # THE LOCAL BOTTOM/TOP GATE — how deep/high before entry fires
        "ENTRY_ZONE_LONG": [15.0, 25.0, 35.0, 45.0],    # k_1h < X for longs (local bottom depth)
        "ENTRY_ZONE_SHORT": [55.0, 65.0, 75.0, 85.0],   # k_1h > X for shorts (local top height)
        # SIZING: $500-$5000 tiers (Phase 2 will make this score-dynamic)
        "START_POSITION_SIZE": [500.0, 1000.0, 2000.0, 5000.0],
        # EXIT: profit target captures the "opposite extreme" for longs → tops, for shorts → bottoms
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [0.5, 1.0, 2.0, 3.0],
        # HOLD: minimum bars before exits fire (4=~20min, 10=~50min, 20=~100min on 5m base)
        "MIN_HOLD_BARS": [4, 10, 20],
        # WT TECHNICAL EXIT: TFs required to confirm reversal (never 5 = 0 trades rule)
        "WT_EXIT_MIN_TFS": [2, 3],
        # EARLY ABORT: kill bad configs fast on 6 fast symbols
        "EARLY_ABORT_MIN_SYMBOLS": [6],
        "EARLY_ABORT_SHARPE_FLOOR": [2.0],
        "EARLY_ABORT_TIME_LIMIT_SEC": [15.0],
    }


def build_param_grid_local_extremes_tradier_validate():
    """2026-04-20: Full-symbol validation of local_extremes_tradier Phase 1 winners.
    Run top configs from Phase 1 on all 128 tradier symbols over 2yr with no early abort.
    Paste winning params from Phase 1 CSV into ENTRY_ZONE_LONG/SHORT, SIZE, PT_PCT.
    Run: --mode tradier --symbols all --start 2024-01-01 --tier local_extremes_tradier_validate --workers 8 --kill-sharpe 0 --kill-secs 999999
    """
    return {
        # Fill from Phase 1 winners — placeholder ranges below
        "ENTRY_ZONE_LONG": [15.0, 25.0],
        "ENTRY_ZONE_SHORT": [75.0, 85.0],
        "START_POSITION_SIZE": [1000.0, 2000.0, 5000.0],
        "PROFIT_TARGET_ENABLED": [True],
        "PROFIT_TARGET_PCT": [0.5, 1.0, 2.0],
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
        "PROFIT_TARGET_PCT": [0.8, 1.0, 1.2],
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
    "mega_tradier_v8": build_param_grid_mega_tradier_v8,
    "crypto_validate_top": build_param_grid_crypto_validate_top,
    "local_extremes_tradier": build_param_grid_local_extremes_tradier,
    "local_extremes_tradier_validate": build_param_grid_local_extremes_tradier_validate,
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
    fieldnames = ["run_id", "config_hash", "sharpe", "pool_sharpe", "sharpe_min", "sharpe_p25", "sharpe_med", "sharpe_p75", "sharpe_max", "syms_with_sharpe", "syms_excluded", "pnl", "trades", "wins", "losses", "wr", "avg_pnl_pct", "elapsed", "status", "early_abort", "symbols_used"]
    for k in cfg_keys:
        fieldnames.append(f"cfg_{k}")

    completed = 0
    winners_found = 0
    best_sharpe = -999
    t_start = time.time()
    pass_num = 0

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

    with open(csv_path, "a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        def _process_result(result, todo_len):
            nonlocal completed, best_sharpe, winners_found
            completed += 1
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
            if max(s, ps) >= args.min_csv_sharpe:  # save if EITHER per-sym avg OR pool exceeds threshold
                writer.writerow(row)
                csvfile.flush()
            if ps >= winner_floor:
                winners_found += 1
            elapsed_total = time.time() - t_start
            rate = completed / elapsed_total if elapsed_total > 0 else 0
            eta = (todo_len - completed) / rate / 3600 if rate > 0 else 0
            winner_str = f" winners={winners_found}" if target_winners > 0 else ""
            if completed % 10 == 0 or completed <= 5:
                print(f"  [{completed}/{todo_len}] sharpe={s:.4f} pool={ps:.4f} trades={result.get('trades', 0)} "
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
            for i, cfg_dict in enumerate(configs):
                h = config_hash(cfg_dict)
                if h in done_hashes:
                    continue
                todo.append((npz_dir, args.mode, symbols_list, args.start, cfg_dict, f"q_{args.tier}_{i:05d}"))

            if args.shuffle:
                random.shuffle(todo)
            if args.max_configs > 0 and len(todo) > args.max_configs:
                todo = todo[:args.max_configs]

            skip = total - len(todo)
            print(f"  {total} configs ({skip} done, {len(todo)} todo)")
            if not todo:
                print("All configs done.")
                return
            _run_pass(todo)

    winner_str = f"  Winners (sharpe>={winner_floor:.2f}): {winners_found}/{target_winners}\n" if target_winners > 0 else ""
    print(f"\n{'='*70}")
    print(f"  SWEEP COMPLETE — {completed} configs in {time.time()-t_start:.0f}s")
    print(f"  Best Sharpe: {best_sharpe:.4f}")
    print(f"{winner_str}  Results: {csv_path}")
    print(f"{'='*70}")
    print(f"\nV8_QUICK_SWEEP_DONE: configs={completed} best_sharpe={best_sharpe:.4f} winners={winners_found} csv={csv_path}")


if __name__ == "__main__":
    main()

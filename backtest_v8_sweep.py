#!/usr/bin/env python3
"""
backtest_v8_sweep.py — Ablation sweep harness for backtest_v8_engine.

Each config variant runs as an isolated subprocess. Override values are written
to a JSON file and passed via the V8_OVERRIDE_FILE env var, which backtest_v8_engine
patches at 4 levels (module attr, Config class, dataclass defaults, live instances)
BEFORE importing ez_manage — so the real hedge/reentry code reads the overridden values.

Unlike v8_quick_sweep (vectorized, no hedge simulation), this harness runs the REAL
engine with full HedgeEngine execution, so hedge switches actually move numbers.

Results: one V8_RESULT line per variant captured from stdout; accumulated into a CSV
written incrementally so interruptions don't lose work.

Usage examples:
  # Test all hedge switches one-by-one (baseline + single-flip ablation)
  python3 backtest_v8_sweep.py --mode crypto --account ang \\
      --start 2026-01-01 --symbols BTCUSDT,ETHUSDT,SOLUSDT \\
      --tier hedge_one_by_one --workers 4

  # Full reentry-overhaul ablation
  python3 backtest_v8_sweep.py --mode crypto --account ang \\
      --start 2026-01-01 --symbols BTCUSDT,ETHUSDT,LINKUSDT,DOTUSDT \\
      --tier reentry_one_by_one --workers 4

  # Combined ablation
  python3 backtest_v8_sweep.py --mode crypto --account ang \\
      --start 2026-01-01 --tier hedge_reentry_ablation --workers 8

Tiers:
  hedge_one_by_one       — flip each hedge switch alone vs baseline (6 variants)
  reentry_one_by_one     — flip each reentry switch alone (10 variants)
  hedge_reentry_ablation — union of both (16 variants)
  hedge_full             — cartesian over hedge switches (48 variants)

Output CSV: data/sweep_results/backtest_v8_sweep_<tier>_<timestamp>.csv
"""
import argparse
import csv
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent
ENGINE_PATH = BASE_PATH / "backtest_v8_engine.py"
OVERRIDE_DIR = BASE_PATH / "data" / "sweep_overrides"
RESULTS_DIR = BASE_PATH / "data" / "sweep_results"
OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PY_BIN = sys.executable
# 2026-04-29: V8_RESULT now emits pool_sharpe + sym_sharpe + sharpe-alias (CLAUDE.md rule 4).
# sharpe_w / sharpe_ann are BANNED — sweep ranking now uses pool_sharpe (= sharpe_pt = per-trade canonical).
V8_RESULT_RE = re.compile(
    r"V8_RESULT:\s*"
    r"pool_sharpe=(?P<sharpe_w>[-\d.]+)\s+"        # pool_sharpe captured into sharpe_w field for back-compat
    r"sym_sharpe=(?P<sharpe_pt>[-\d.]+)\s+"         # sym_sharpe → sharpe_pt slot (caller treats as diagnostic)
    r"sharpe=(?P<sharpe_ann>[-\d.]+)\s+"            # alias = pool_sharpe (also into sharpe_ann slot for compat)
    r"gain_pct=(?P<gain_pct>[-+\d.]+)\s+"
    r"closes=(?P<closes>\d+)\s+"
    r"wins=(?P<wins>\d+)\s+"
    r"losses=(?P<losses>\d+)"
)
V8_RESULT_LIVE_RE = re.compile(r"V8_RESULT_LIVE:.*pool_sharpe=(?P<sharpe_w>[-\d.]+)")
# Fallback _v8_result_from_trades format (now also emits pool/sym/sharpe-alias):
V8_RESULT_TRADIER_RE = re.compile(
    r"V8_RESULT:\s*"
    r"pool_sharpe=[-\d.]+\s+sym_sharpe=[-\d.]+\s+"
    r"sharpe=(?P<sharpe>[-\d.]+)\s+"
    r"pnl=(?P<pnl>[-+\d.]+)\s+"
    r"trades=(?P<trades>\d+)\s+"
    r"wins=(?P<wins>\d+)\s+"
    r"losses=(?P<losses>\d+)"
)
# Tradier sweep-mode live format: V8_RESULT_LIVE: step=X/Y closes=X elapsed=Xs
V8_RESULT_LIVE_SIMPLE_RE = re.compile(r"V8_RESULT_LIVE:.*closes=(?P<closes>\d+)")


# ══════════════════════════════════════════════════════════════════════════════
# ABLATION GRIDS
# Each grid is a list of (label, overrides_dict). Baseline has empty dict → all
# defaults. Each subsequent entry flips exactly ONE switch from default.
# ══════════════════════════════════════════════════════════════════════════════

def grid_hedge_one_by_one():
    return [
        ("baseline", {}),
        ("HEDGE_EXIT_BYPASS_NOLOSS_OFF", {"HEDGE_EXIT_BYPASS_NOLOSS": False}),
        ("HEDGE_CLOSE_REMOVE_FROM_TRADEABLE_OFF", {"HEDGE_CLOSE_REMOVE_FROM_TRADEABLE": False}),
        ("HEDGE_SAME_SYMBOL_PCT_0.5", {"HEDGE_SAME_SYMBOL_PCT": 0.5}),
        ("HEDGE_SAME_SYMBOL_PCT_1.5", {"HEDGE_SAME_SYMBOL_PCT": 1.5}),
        ("HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE_OFF", {"HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE": False}),
    ]


def grid_reentry_one_by_one():
    return [
        ("baseline", {}),
        ("REENTRY_WT15M_CROSS_OFF", {"REENTRY_WT15M_CROSS_ENABLED": False}),
        ("REENTRY_WT15M_SIZE_1.0", {"REENTRY_WT15M_SIZE_MULT": 1.0}),
        ("REENTRY_WT15M_SIZE_2.0", {"REENTRY_WT15M_SIZE_MULT": 2.0}),
        ("REENTRY_WT15M_HTF_NOT_REQUIRED", {"REENTRY_WT15M_HTF_FAVOR_REQUIRED": False}),
        ("REENTRY_K15M_PARTIAL_OFF", {"REENTRY_K15M_PARTIAL_ENABLED": False}),
        ("REENTRY_K15M_THRESHOLD_80", {"REENTRY_K15M_PARTIAL_THRESHOLD": 80.0}),
        ("REENTRY_K15M_PARTIAL_MULT_0.3", {"REENTRY_K15M_PARTIAL_MULT": 0.3}),
        ("REENTRY_POST_CONSOL_OFF", {"REENTRY_POST_CONSOL_ENABLED": False}),
        ("REENTRY_POST_CONSOL_MULT_2.0", {"REENTRY_POST_CONSOL_MULT": 2.0}),
    ]


def grid_hedge_reentry_ablation():
    return grid_hedge_one_by_one() + grid_reentry_one_by_one()[1:]  # drop dup baseline


def grid_reentry_wide():
    """Extended reentry ablation — new overhaul switches at multiple values +
    all existing REENTRY2_* + REENTRY_B* block toggles. Use with 10-12 symbols
    over 2+ months for statistical power. User priority 2026-04-17: make
    reentries fire again."""
    return [
        ("baseline", {}),
        # ── NEW OVERHAUL SWITCHES (C/D/E) — multiple values each ──
        ("WT15M_CROSS_OFF", {"REENTRY_WT15M_CROSS_ENABLED": False}),
        ("WT15M_SIZE_1.0", {"REENTRY_WT15M_SIZE_MULT": 1.0}),
        ("WT15M_SIZE_1.3", {"REENTRY_WT15M_SIZE_MULT": 1.3}),
        ("WT15M_SIZE_2.0", {"REENTRY_WT15M_SIZE_MULT": 2.0}),
        ("WT15M_K_MAX_30", {"REENTRY_WT15M_K_MAX": 30.0}),
        ("WT15M_K_MAX_70", {"REENTRY_WT15M_K_MAX": 70.0}),
        ("WT15M_K_MAX_100", {"REENTRY_WT15M_K_MAX": 100.0}),
        ("WT15M_HTF_NOT_REQUIRED", {"REENTRY_WT15M_HTF_FAVOR_REQUIRED": False}),
        ("K15M_PARTIAL_OFF", {"REENTRY_K15M_PARTIAL_ENABLED": False}),
        ("K15M_THRESHOLD_70", {"REENTRY_K15M_PARTIAL_THRESHOLD": 70.0}),
        ("K15M_THRESHOLD_80", {"REENTRY_K15M_PARTIAL_THRESHOLD": 80.0}),
        ("K15M_THRESHOLD_100", {"REENTRY_K15M_PARTIAL_THRESHOLD": 100.0}),
        ("K15M_MULT_0.3", {"REENTRY_K15M_PARTIAL_MULT": 0.3}),
        ("K15M_MULT_0.7", {"REENTRY_K15M_PARTIAL_MULT": 0.7}),
        ("POST_CONSOL_OFF", {"REENTRY_POST_CONSOL_ENABLED": False}),
        ("POST_CONSOL_MULT_1.3", {"REENTRY_POST_CONSOL_MULT": 1.3}),
        ("POST_CONSOL_MULT_2.0", {"REENTRY_POST_CONSOL_MULT": 2.0}),
        ("POST_CONSOL_TFS_1", {"REENTRY_POST_CONSOL_TFS_REQUIRED": 1}),
        ("POST_CONSOL_TFS_3", {"REENTRY_POST_CONSOL_TFS_REQUIRED": 3}),
        # ── EXISTING REENTRY2_* BLOCKS (ablation) ──
        ("REENTRY2_MASTER_OFF", {"REENTRY_2_ENABLED": False}),
        ("REENTRY2_DIR_FAV_OFF", {"REENTRY2_DIR_FAV_ENABLED": False}),
        ("REENTRY2_DC_BREAK_OFF", {"REENTRY2_DC_BREAK_ENABLED": False}),
        ("REENTRY2_QUICK_RECOVERY_OFF", {"REENTRY2_QUICK_RECOVERY_ENABLED": False}),
        # ── EXISTING REENTRY_B* BLOCKS (ablation) ──
        ("REENTRY_B02_OFF", {"REENTRY_B02_BC156_BOTTOM_ENABLED": False}),
        ("REENTRY_B10_OFF", {"REENTRY_B10_STOCH_REV_ENABLED": False}),
        ("REENTRY_B11_OFF", {"REENTRY_B11_DC_BREAK_ENABLED": False}),
        ("REENTRY_B12_OFF", {"REENTRY_B12_WT_MOM_ENABLED": False}),
        ("REENTRY_B14_OFF", {"REENTRY_B14_HA_TREND_ENABLED": False}),
        ("REENTRY_B15_OFF", {"REENTRY_B15_STRONG_TREND_ENABLED": False}),
        # ── COMBINED: turn everything off to see marginal value of ALL reentries ──
        ("ALL_NEW_REENTRIES_OFF", {
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
        ("ALL_REENTRIES_OFF", {
            "REENTRY_2_ENABLED": False,
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
            "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
            "REENTRY_B10_STOCH_REV_ENABLED": False,
            "REENTRY_B11_DC_BREAK_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False,
            "REENTRY_B14_HA_TREND_ENABLED": False,
            "REENTRY_B15_STRONG_TREND_ENABLED": False,
        }),
    ]


def grid_hedge_full():
    keys = {
        "HEDGE_EXIT_BYPASS_NOLOSS": [True, False],
        "HEDGE_CLOSE_REMOVE_FROM_TRADEABLE": [True, False],
        "HEDGE_SAME_SYMBOL_PCT": [0.5, 1.0, 1.5],
        "HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE": [True, False],
    }
    names = list(keys.keys())
    variants = []
    for combo in itertools.product(*[keys[k] for k in names]):
        cfg = dict(zip(names, combo))
        label = "_".join(f"{k.replace('HEDGE_', '')}={v}" for k, v in cfg.items())
        variants.append((label, cfg))
    return variants


def grid_reentry_all_real():
    """2026-04-18: user directive — test EVERY reentry condition actually in code, not just a subset.
    Covers:
      evaluate_reentry (ez_manage.py:16154, ez_positions_quick.py:13020):
        B01_WT_2of3 (OFF default, ablation Sharpe 0.033)
        B02_BC156_BOTTOM (Sharpe 0.31 both)
        B04_DC_RETEST (Sharpe 0.39 crypto, 0.31 tradier)
        B09_SNAPBACK (OFF default, ablation Sharpe 0.022)
        B10_STOCH_REV (Sharpe 0.07-0.12, 69-75% WR)
        B11_DC_BREAK (Sharpe 0.34)
        B12_WT_MOM (Sharpe 0.15-0.17, 112K/73K trades volume king)
        B14_HA_TREND (Sharpe 0.11-0.13)
        B15_STRONG_TREND (Sharpe 0.89 crypto, 94-97% WR, top performer)
      evaluate_reentry_2 sub-blocks (process_single_reentry_evaluation):
        REENTRY2_DIR_FAV (BC_152 direction-favorable 2h window)
        REENTRY2_DC_BREAK (DC breakout fast-path)
        REENTRY2_TREND (strong trend/pullback)
        REENTRY2_QUICK_RECOVERY (quick recovery + momentum)
        REENTRY2_STOCH_CROSS (legacy, default OFF)
      Overhaul (2026-04-17): WT15M_CROSS, K15M_PARTIAL, POST_CONSOL
    Plus master switches and pairwise combos."""
    return [
        ("baseline", {}),
        # ── evaluate_reentry (B blocks) — each one off ──
        ("B01_ON", {"REENTRY_B01_WT_2of3_ENABLED": True}),  # default OFF, test ON
        ("B02_OFF", {"REENTRY_B02_BC156_BOTTOM_ENABLED": False}),
        ("B04_OFF", {"REENTRY_B04_DC_RETEST_ENABLED": False}),
        ("B09_ON", {"REENTRY_B09_SNAPBACK_ENABLED": True}),  # default OFF, test ON
        ("B10_OFF", {"REENTRY_B10_STOCH_REV_ENABLED": False}),
        ("B11_OFF", {"REENTRY_B11_DC_BREAK_ENABLED": False}),
        ("B12_OFF", {"REENTRY_B12_WT_MOM_ENABLED": False}),
        ("B14_OFF", {"REENTRY_B14_HA_TREND_ENABLED": False}),
        ("B15_OFF", {"REENTRY_B15_STRONG_TREND_ENABLED": False}),
        # ── evaluate_reentry_2 sub-blocks — each off ──
        ("R2_DIR_FAV_OFF", {"REENTRY2_DIR_FAV_ENABLED": False}),
        ("R2_DC_BREAK_OFF", {"REENTRY2_DC_BREAK_ENABLED": False}),
        ("R2_TREND_OFF", {"REENTRY2_TREND_ENABLED": False}),
        ("R2_QUICK_RECOVERY_OFF", {"REENTRY2_QUICK_RECOVERY_ENABLED": False}),
        ("R2_STOCH_CROSS_ON", {"REENTRY2_STOCH_CROSS_ENABLED": True}),  # default OFF
        ("R2_MASTER_OFF", {"REENTRY_2_ENABLED": False}),
        # ── 2026-04-17 overhaul blocks — each at key values ──
        ("WT15M_OFF", {"REENTRY_WT15M_CROSS_ENABLED": False}),
        ("WT15M_SIZE_2.0", {"REENTRY_WT15M_SIZE_MULT": 2.0}),
        ("WT15M_HTF_NOT_REQUIRED", {"REENTRY_WT15M_HTF_FAVOR_REQUIRED": False}),
        ("K15M_OFF", {"REENTRY_K15M_PARTIAL_ENABLED": False}),
        ("POST_CONSOL_OFF", {"REENTRY_POST_CONSOL_ENABLED": False}),
        ("POST_CONSOL_TFS_1", {"REENTRY_POST_CONSOL_TFS_REQUIRED": 1}),
        # ── timing / gating knobs ──
        ("MIN_GAP_3", {"REENTRY_MIN_GAP_MINUTES": 3.0}),
        ("MIN_GAP_30", {"REENTRY_MIN_GAP_MINUTES": 30.0}),
        ("REENTRY_SYMGATE_OFF", {"REENTRY_SYMGATE_ENABLED": False}),
        ("CT_VEL_3.0", {"CT_WT_VELOCITY_1H_MIN": 3.0}),
        ("CT_VEL_9.0", {"CT_WT_VELOCITY_1H_MIN": 9.0}),
        # ── COMBOS: turn off proven-weak blocks ──
        ("KILL_NOISE_B01_B09", {"REENTRY_B01_WT_2of3_ENABLED": False, "REENTRY_B09_SNAPBACK_ENABLED": False}),
        ("ENABLE_NOISE_B01_B09", {"REENTRY_B01_WT_2of3_ENABLED": True, "REENTRY_B09_SNAPBACK_ENABLED": True}),
        # ── COMBO: top-sharpe blocks only (B15+B04+B11+B02) ──
        ("TOP_BLOCKS_ONLY", {
            "REENTRY_B10_STOCH_REV_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False,
            "REENTRY_B14_HA_TREND_ENABLED": False,
        }),
        # ── COMBO: disable all volume-king B12 + strong-B15 conflicts ──
        ("NO_B12_NO_B14", {"REENTRY_B12_WT_MOM_ENABLED": False, "REENTRY_B14_HA_TREND_ENABLED": False}),
        # ── COMBO: R2 paths only (kill B-blocks) ──
        ("R2_ONLY", {
            "REENTRY_B01_WT_2of3_ENABLED": False, "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
            "REENTRY_B04_DC_RETEST_ENABLED": False, "REENTRY_B09_SNAPBACK_ENABLED": False,
            "REENTRY_B10_STOCH_REV_ENABLED": False, "REENTRY_B11_DC_BREAK_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False, "REENTRY_B14_HA_TREND_ENABLED": False,
            "REENTRY_B15_STRONG_TREND_ENABLED": False,
        }),
        # ── COMBO: B-blocks only (kill R2) ──
        ("B_BLOCKS_ONLY", {
            "REENTRY_2_ENABLED": False,
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
        # ── COMBO: Overhaul only (kill B + R2) ──
        ("OVERHAUL_ONLY", {
            "REENTRY_2_ENABLED": False,
            "REENTRY_B01_WT_2of3_ENABLED": False, "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
            "REENTRY_B04_DC_RETEST_ENABLED": False, "REENTRY_B09_SNAPBACK_ENABLED": False,
            "REENTRY_B10_STOCH_REV_ENABLED": False, "REENTRY_B11_DC_BREAK_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False, "REENTRY_B14_HA_TREND_ENABLED": False,
            "REENTRY_B15_STRONG_TREND_ENABLED": False,
        }),
        # ── COMBO: kill ALL reentries ──
        ("ALL_REENTRIES_OFF", {
            "REENTRY_2_ENABLED": False,
            "REENTRY_B01_WT_2of3_ENABLED": False, "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
            "REENTRY_B04_DC_RETEST_ENABLED": False, "REENTRY_B09_SNAPBACK_ENABLED": False,
            "REENTRY_B10_STOCH_REV_ENABLED": False, "REENTRY_B11_DC_BREAK_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False, "REENTRY_B14_HA_TREND_ENABLED": False,
            "REENTRY_B15_STRONG_TREND_ENABLED": False,
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
        # ── AGGRESSIVE bundle (all on, big sizes, loose gates) ──
        ("AGGRESSIVE_FULL", {
            "REENTRY_B01_WT_2of3_ENABLED": True,
            "REENTRY_B09_SNAPBACK_ENABLED": True,
            "REENTRY2_STOCH_CROSS_ENABLED": True,
            "REENTRY_WT15M_SIZE_MULT": 2.0,
            "REENTRY_K15M_PARTIAL_MULT": 1.0,
            "REENTRY_POST_CONSOL_MULT": 2.0,
            "REENTRY_MIN_GAP_MINUTES": 3.0,
            "REENTRY_SYMGATE_ENABLED": False,
        }),
        # ── CHAPTER-C variant: current live strict config ──
        ("CHAPTER_C_STRICT", {
            "REENTRY_SYMGATE_ENABLED": True,
            "ENTRY_SYMGATE_ENABLED": True,
            "REENTRY_MIN_GAP_MINUTES": 15.0,
            "REENTRY_RALLY_K15M_MAX": 40.0,
            "CT_WT_VELOCITY_1H_MIN": 6.0,
        }),
    ]


def grid_reentry_optimize():
    """2026-04-17 user directive: 'reentries are the key to doubling sharpe'.
    Wide sweep across every reentry tunable + high-signal combos. Run on tight 4-sym
    base so it fits alongside vec_backlog on S1 (~2hrs for ~60 variants workers=1)."""
    return [
        ("baseline", {}),
        # ── C: WT15M_CROSS ──
        ("WT15M_SIZE_1.0", {"REENTRY_WT15M_SIZE_MULT": 1.0}),
        ("WT15M_SIZE_1.3", {"REENTRY_WT15M_SIZE_MULT": 1.3}),
        ("WT15M_SIZE_1.8", {"REENTRY_WT15M_SIZE_MULT": 1.8}),
        ("WT15M_SIZE_2.5", {"REENTRY_WT15M_SIZE_MULT": 2.5}),
        ("WT15M_K_MAX_30", {"REENTRY_WT15M_K_MAX": 30.0}),
        ("WT15M_K_MAX_70", {"REENTRY_WT15M_K_MAX": 70.0}),
        ("WT15M_K_MAX_100", {"REENTRY_WT15M_K_MAX": 100.0}),
        ("WT15M_HTF_NOT_REQUIRED", {"REENTRY_WT15M_HTF_FAVOR_REQUIRED": False}),
        # ── D: K15M_PARTIAL ──
        ("K15M_T_70", {"REENTRY_K15M_PARTIAL_THRESHOLD": 70.0}),
        ("K15M_T_80", {"REENTRY_K15M_PARTIAL_THRESHOLD": 80.0}),
        ("K15M_T_95", {"REENTRY_K15M_PARTIAL_THRESHOLD": 95.0}),
        ("K15M_MULT_0.3", {"REENTRY_K15M_PARTIAL_MULT": 0.3}),
        ("K15M_MULT_0.7", {"REENTRY_K15M_PARTIAL_MULT": 0.7}),
        ("K15M_MULT_1.0", {"REENTRY_K15M_PARTIAL_MULT": 1.0}),
        # ── E: POST_CONSOL ──
        ("POST_C_MULT_1.2", {"REENTRY_POST_CONSOL_MULT": 1.2}),
        ("POST_C_MULT_1.8", {"REENTRY_POST_CONSOL_MULT": 1.8}),
        ("POST_C_MULT_2.5", {"REENTRY_POST_CONSOL_MULT": 2.5}),
        ("POST_C_TFS_1", {"REENTRY_POST_CONSOL_TFS_REQUIRED": 1}),
        ("POST_C_TFS_3", {"REENTRY_POST_CONSOL_TFS_REQUIRED": 3}),
        ("POST_C_ATR_0.10", {"REENTRY_POST_CONSOL_ATR_THRESHOLD": 0.10}),
        ("POST_C_ATR_0.20", {"REENTRY_POST_CONSOL_ATR_THRESHOLD": 0.20}),
        # ── Timing knobs (per user live config) ──
        ("MIN_GAP_3", {"REENTRY_MIN_GAP_MINUTES": 3.0}),
        ("MIN_GAP_5", {"REENTRY_MIN_GAP_MINUTES": 5.0}),
        ("MIN_GAP_30", {"REENTRY_MIN_GAP_MINUTES": 30.0}),
        ("AGGR_WIN_20", {"REENTRY_AGGRESSIVE_WINDOW_MIN": 20.0}),
        ("AGGR_WIN_60", {"REENTRY_AGGRESSIVE_WINDOW_MIN": 60.0}),
        ("RALLY_K_40", {"REENTRY_RALLY_K15M_MAX": 40.0}),
        ("RALLY_K_80", {"REENTRY_RALLY_K15M_MAX": 80.0}),
        ("RALLY_K_100", {"REENTRY_RALLY_K15M_MAX": 100.0}),
        # ── SYMGATE — bundled with Chapter-C per user config, test OFF ──
        ("REENTRY_SYMGATE_OFF", {"REENTRY_SYMGATE_ENABLED": False}),
        ("ENTRY_SYMGATE_OFF", {"ENTRY_SYMGATE_ENABLED": False}),
        ("BOTH_SYMGATE_OFF", {"REENTRY_SYMGATE_ENABLED": False, "ENTRY_SYMGATE_ENABLED": False}),
        # ── CT velocity gate knob ──
        ("CT_VEL_0.0", {"CT_WT_VELOCITY_1H_MIN": 0.0}),
        ("CT_VEL_1.0", {"CT_WT_VELOCITY_1H_MIN": 1.0}),
        ("CT_VEL_3.0", {"CT_WT_VELOCITY_1H_MIN": 3.0}),
        # ── BUNDLE: AGGRESSIVE reentry (big sizes, loose gates) ──
        ("AGGRESSIVE_BUNDLE", {
            "REENTRY_WT15M_SIZE_MULT": 2.0,
            "REENTRY_WT15M_K_MAX": 70.0,
            "REENTRY_WT15M_HTF_FAVOR_REQUIRED": False,
            "REENTRY_K15M_PARTIAL_MULT": 1.0,
            "REENTRY_POST_CONSOL_MULT": 2.0,
            "REENTRY_POST_CONSOL_TFS_REQUIRED": 1,
            "REENTRY_MIN_GAP_MINUTES": 3.0,
            "REENTRY_RALLY_K15M_MAX": 100.0,
        }),
        # ── BUNDLE: CONSERVATIVE reentry (small sizes, tight gates) ──
        ("CONSERVATIVE_BUNDLE", {
            "REENTRY_WT15M_SIZE_MULT": 1.0,
            "REENTRY_WT15M_K_MAX": 30.0,
            "REENTRY_WT15M_HTF_FAVOR_REQUIRED": True,
            "REENTRY_K15M_PARTIAL_MULT": 0.3,
            "REENTRY_POST_CONSOL_MULT": 1.2,
            "REENTRY_POST_CONSOL_TFS_REQUIRED": 3,
            "REENTRY_MIN_GAP_MINUTES": 30.0,
            "REENTRY_RALLY_K15M_MAX": 40.0,
        }),
        # ── BUNDLE: Synergize with proven B-blocks (B10 + B12 + B15 all alive) ──
        ("SYNERGY_ALL_ALIVE", {
            "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
            "REENTRY_B10_STOCH_REV_ENABLED": True,
            "REENTRY_B11_DC_BREAK_ENABLED": True,
            "REENTRY_B12_WT_MOM_ENABLED": True,
            "REENTRY_B14_HA_TREND_ENABLED": True,
            "REENTRY_B15_STRONG_TREND_ENABLED": True,
            "REENTRY_WT15M_CROSS_ENABLED": True,
            "REENTRY_K15M_PARTIAL_ENABLED": True,
            "REENTRY_POST_CONSOL_ENABLED": True,
        }),
        # ── BUNDLE: Kill B-blocks, keep REENTRY2 only ──
        ("REENTRY2_ONLY", {
            "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
            "REENTRY_B10_STOCH_REV_ENABLED": False,
            "REENTRY_B11_DC_BREAK_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False,
            "REENTRY_B14_HA_TREND_ENABLED": False,
            "REENTRY_B15_STRONG_TREND_ENABLED": False,
        }),
        # ── BUNDLE: B-blocks only, kill REENTRY2 ──
        ("B_BLOCKS_ONLY", {
            "REENTRY_2_ENABLED": False,
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
        # ── BUNDLE: B-blocks with B12 removed (B12 was dragging pre-patch) ──
        ("B_BLOCKS_NO_B12", {
            "REENTRY_2_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False,
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
        # ── BUNDLE: user's Chapter-C config ──
        ("CHAPTER_C_STRICT", {
            "REENTRY_SYMGATE_ENABLED": True,
            "ENTRY_SYMGATE_ENABLED": True,
            "REENTRY_MIN_GAP_MINUTES": 15.0,
            "REENTRY_RALLY_K15M_MAX": 60.0,
            "CT_WT_VELOCITY_1H_MIN": 2.0,
        }),
        # ── BUNDLE: Chapter-C relaxed ──
        ("CHAPTER_C_LOOSE", {
            "REENTRY_SYMGATE_ENABLED": False,
            "ENTRY_SYMGATE_ENABLED": False,
            "REENTRY_MIN_GAP_MINUTES": 3.0,
            "REENTRY_RALLY_K15M_MAX": 100.0,
            "CT_WT_VELOCITY_1H_MIN": 0.0,
        }),
    ]


def grid_reentry_killed_rerun():
    """Re-run the 12 variants that died with rc=-9 (OOM) at end of reentry_wide v3 on S1.
    Use lower worker count + maybe narrower symbols to avoid memory pressure."""
    return [
        ("baseline", {}),
        ("REENTRY2_MASTER_OFF", {"REENTRY_2_ENABLED": False}),
        ("REENTRY2_DIR_FAV_OFF", {"REENTRY2_DIR_FAV_ENABLED": False}),
        ("REENTRY2_DC_BREAK_OFF", {"REENTRY2_DC_BREAK_ENABLED": False}),
        ("REENTRY2_QUICK_RECOVERY_OFF", {"REENTRY2_QUICK_RECOVERY_ENABLED": False}),
        ("REENTRY_B02_OFF", {"REENTRY_B02_BC156_BOTTOM_ENABLED": False}),
        ("REENTRY_B10_OFF", {"REENTRY_B10_STOCH_REV_ENABLED": False}),
        ("REENTRY_B11_OFF", {"REENTRY_B11_DC_BREAK_ENABLED": False}),
        ("REENTRY_B12_OFF", {"REENTRY_B12_WT_MOM_ENABLED": False}),
        ("REENTRY_B14_OFF", {"REENTRY_B14_HA_TREND_ENABLED": False}),
        ("REENTRY_B15_OFF", {"REENTRY_B15_STRONG_TREND_ENABLED": False}),
        ("ALL_NEW_REENTRIES_OFF", {
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
        ("ALL_REENTRIES_OFF", {
            "REENTRY_2_ENABLED": False,
            "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
            "REENTRY_B10_STOCH_REV_ENABLED": False,
            "REENTRY_B11_DC_BREAK_ENABLED": False,
            "REENTRY_B12_WT_MOM_ENABLED": False,
            "REENTRY_B14_HA_TREND_ENABLED": False,
            "REENTRY_B15_STRONG_TREND_ENABLED": False,
            "REENTRY_WT15M_CROSS_ENABLED": False,
            "REENTRY_K15M_PARTIAL_ENABLED": False,
            "REENTRY_POST_CONSOL_ENABLED": False,
        }),
    ]


def grid_indicator_audit():
    """2026-04-19 indicator-audit v2 — exit quality sweep.
    Baseline: new config defaults (WT_MTF_VEL_GATE_ENABLED=True/min=2, WT_4H_VEL_EXIT fixed SHORT,
    DC_HOPELESS_EXIT_ENABLED=True). Goal: exit at top/channel break, never at random bottom.
    Run on BTCUSDT,ETHUSDT,SOLUSDT,DOTUSDT,LINKUSDT for stat power."""
    return [
        # Baseline = new config defaults (vel gate ON/2, fixed SHORT vel exit, DC hopeless ON)
        ("baseline", {}),
        # ── WT_4H_VEL_EXIT variants ──
        ("4H_EXIT_OFF", {"WT_4H_VEL_EXIT_ENABLED": False}),
        ("4H_EXIT_LONG_vel-1", {"WT_4H_VEL_EXIT_LONG_VEL_MIN": -1.0}),   # looser LONG threshold
        ("4H_EXIT_LONG_vel-3", {"WT_4H_VEL_EXIT_LONG_VEL_MIN": -3.0}),   # tighter LONG threshold
        ("4H_EXIT_LONG_vel-5", {"WT_4H_VEL_EXIT_LONG_VEL_MIN": -5.0}),   # very tight
        ("4H_EXIT_SHORT_vel1", {"WT_4H_VEL_EXIT_SHORT_VEL_MIN": 1.0}),   # looser SHORT threshold
        ("4H_EXIT_SHORT_vel3", {"WT_4H_VEL_EXIT_SHORT_VEL_MIN": 3.0}),   # tighter SHORT threshold
        ("4H_EXIT_SHORT_vel5", {"WT_4H_VEL_EXIT_SHORT_VEL_MIN": 5.0}),   # very tight
        # ── DC_HOPELESS_EXIT variants ──
        ("DC_HOPELESS_OFF", {"DC_HOPELESS_EXIT_ENABLED": False}),
        ("DC_HOPELESS_age300", {"DC_HOPELESS_EXIT_MIN_AGE_S": 300}),      # 5min min age
        ("DC_HOPELESS_age1800", {"DC_HOPELESS_EXIT_MIN_AGE_S": 1800}),    # 30min min age
        # ── MTF velocity entry gate variants (validated winner: vel_min=2) ──
        ("VEL_OFF", {"WT_MTF_VEL_GATE_ENABLED": False}),
        ("VEL_min3", {"WT_MTF_VEL_GATE_ENABLED": True, "WT_MTF_VEL_MIN": 3}),
        # ── COMBOS: best exit configuration candidates ──
        # All exits ON (current default)
        ("ALL_EXITS_DEFAULT", {}),
        # No 4h vel exit, DC hopeless only
        ("DC_ONLY_NO_4H", {"WT_4H_VEL_EXIT_ENABLED": False}),
        # Tight 4h vel + DC hopeless (conservative exits)
        ("TIGHT_4H_DC", {"WT_4H_VEL_EXIT_LONG_VEL_MIN": -3.0, "WT_4H_VEL_EXIT_SHORT_VEL_MIN": 3.0}),
        # All exits OFF (pure reentry structure exits only)
        ("ALL_NEW_EXITS_OFF", {"WT_4H_VEL_EXIT_ENABLED": False, "DC_HOPELESS_EXIT_ENABLED": False}),
    ]


def grid_indicator_audit_v2():
    """2026-04-19 indicator-audit v2 FULL — tests each R-S*/R-Z*/RE-*/E-* switch in isolation.
    Each variant flips exactly ONE switch vs baseline. Sweep on BTCUSDT+ETHUSDT+SOLUSDT+DOTUSDT+LINKUSDT
    for stat power. Baseline = all new switches OFF (current live behavior).
    Goal: rank each switch by Sharpe delta vs baseline; top performers go to full 48-sym sweep."""
    return [
        ("baseline", {}),  # all new switches OFF
        # ── R-S SCORING ──
        ("R_S1_delta_thr50", {"R_S1_WT_COMPOSITE_DELTA_USE_ENABLED": True, "R_S1_WT_COMPOSITE_DELTA_THR": 50.0}),
        ("R_S1_delta_thr30", {"R_S1_WT_COMPOSITE_DELTA_USE_ENABLED": True, "R_S1_WT_COMPOSITE_DELTA_THR": 30.0}),
        ("R_S1_delta_thr80", {"R_S1_WT_COMPOSITE_DELTA_USE_ENABLED": True, "R_S1_WT_COMPOSITE_DELTA_THR": 80.0}),
        ("R_S2_adaptive_os10", {"R_S2_WT_ADAPTIVE_OS_ENABLED": True, "R_S2_WT_PCT_OS_LONG": 10.0, "R_S2_WT_PCT_OB_SHORT": 90.0}),
        ("R_S2_adaptive_os5", {"R_S2_WT_ADAPTIVE_OS_ENABLED": True, "R_S2_WT_PCT_OS_LONG": 5.0, "R_S2_WT_PCT_OB_SHORT": 95.0}),
        ("R_S3_div_stack", {"R_S3_DIV_STACK_ENABLED": True}),
        ("R_S3_div_stack_heavy", {"R_S3_DIV_STACK_ENABLED": True, "R_S3_HIDDEN_BONUS": 35.0, "R_S3_MAIN_PENALTY": -30.0}),
        ("R_S4_ha_streak", {"R_S4_HA_STREAK_ENABLED": True}),
        ("R_S4_ha_streak_w7", {"R_S4_HA_STREAK_ENABLED": True, "R_S4_HA_STREAK_WEIGHT": 7.0}),
        ("R_S5_sent_vel", {"R_S5_SENT_VEL_ENABLED": True}),
        ("R_S6_mstate_LTF", {"R_S6_WT_MSTATE_GATE_MODE": 1}),
        ("R_S6_mstate_ALL", {"R_S6_WT_MSTATE_GATE_MODE": 2}),
        # ── R-Z SIZING ──
        ("R_Z1_rank_mult", {"RANKING_MULT_ENABLED": True}),
        ("R_Z2_pct_scaler", {"R_Z2_PERCENTILE_SCALER_ENABLED": True}),
        ("R_Z3_wt_comp_size", {"R_Z3_WT_COMPOSITE_SIZE_ENABLED": True}),
        ("R_Z3_wt_comp_agg", {"R_Z3_WT_COMPOSITE_SIZE_ENABLED": True, "R_Z3_T1_THR": 30.0, "R_Z3_T2_THR": 60.0, "R_Z3_T3_THR": 90.0}),
        ("R_Z4_crash_gradient", {"CRASH_MULT_GRADIENT_ENABLED": True}),
        ("R_Z5_dc_pullback", {"R_Z5_DC_PULLBACK_SIZING_ENABLED": True}),
        # ── RE REENTRY ──
        ("RE_2_use_pct", {"RE_2_USE_PERCENTILE_ENABLED": True}),
        ("RE_3_b12_rising", {"RE_3_B12_RISING_BONUS_ENABLED": True}),
        ("RE_4_b14_streak", {"RE_4_B14_HA_STREAK_CONV_ENABLED": True}),
        ("RE_5_b04_compression", {"RE_5_B04_COMPRESSION_BONUS_ENABLED": True}),
        ("RE_6_wave_phase1", {"RE_6_WAVE_PHASE_GATE_ENABLED": True, "RE_6_MIN_EXPANDING_TFS": 1}),
        ("RE_6_wave_phase2", {"RE_6_WAVE_PHASE_GATE_ENABLED": True, "RE_6_MIN_EXPANDING_TFS": 2}),
        # ── E EXITS ──
        ("E_1_delta_exit50", {"E_1_WT_EXIT_USE_DELTA_ENABLED": True, "E_1_EXIT_DELTA_THR": 50.0}),
        ("E_1_delta_exit30", {"E_1_WT_EXIT_USE_DELTA_ENABLED": True, "E_1_EXIT_DELTA_THR": 30.0}),
        ("E_1_delta_exit80", {"E_1_WT_EXIT_USE_DELTA_ENABLED": True, "E_1_EXIT_DELTA_THR": 80.0}),
        ("E_3_struct_shadow", {"E_3_USE_WT_STRUCTURE_EXIT_MODE": 1}),
        ("E_3_struct_on", {"E_3_USE_WT_STRUCTURE_EXIT_MODE": 2}),
        # ── R-S3 HTF-weighted divergences (under-representation fix) ──
        ("R_S3_htf_weighted", {"R_S3_DIV_STACK_ENABLED": True, "R_S3_HTF_WEIGHT_ENABLED": True}),
        ("R_S3_htf_weighted_heavy", {"R_S3_DIV_STACK_ENABLED": True, "R_S3_HTF_WEIGHT_ENABLED": True, "R_S3_TF_WEIGHT_4H": 3.5, "R_S3_TF_WEIGHT_D": 6.0}),
        # ── R-G10 HTF-only divergence hard gate ──
        ("R_G10_htf_div_4h_D", {"R_G10_HTF_DIV_GATE_ENABLED": True, "R_G10_HTF_DIV_TFS": "4h,D"}),
        ("R_G10_htf_div_D_only", {"R_G10_HTF_DIV_GATE_ENABLED": True, "R_G10_HTF_DIV_TFS": "D"}),
        ("R_G10_htf_div_1h_4h_D", {"R_G10_HTF_DIV_GATE_ENABLED": True, "R_G10_HTF_DIV_TFS": "1h,4h,D"}),
        # ── R-S7 HH/LL multi-indicator stacking ──
        ("R_S7_hhll_stack", {"R_S7_HHLL_STACK_ENABLED": True}),
        ("R_S7_hhll_strict", {"R_S7_HHLL_STACK_ENABLED": True, "R_S7_HHLL_MIN_INDICATORS": 3, "R_S7_HHLL_MIN_TFS_FOR_BONUS": 3}),
        ("R_S7_hhll_htf_only", {"R_S7_HHLL_STACK_ENABLED": True, "R_S7_HHLL_TFS": "1h,4h,D"}),
        ("R_S7_hhll_big_bonus", {"R_S7_HHLL_STACK_ENABLED": True, "R_S7_HHLL_BONUS_PER_TF": 6.0}),
        # ── COMBOS ──
        ("STRUCT_COMBO_all_on", {"R_S3_DIV_STACK_ENABLED": True, "R_S3_HTF_WEIGHT_ENABLED": True, "R_G10_HTF_DIV_GATE_ENABLED": True, "R_S7_HHLL_STACK_ENABLED": True}),
    ]


def grid_tradier_param_hunt():
    """2026-04-19: hunt best tradier params using the REAL backtest_v8_engine.
    Tests ACTUAL config_tradier.py parameter names (not V8Q_ dead params).

    54-combo cartesian of 4 core params + ablation of score/hold/vel-gate.
    Total: ~63 variants. ETA ~60s each / 4 workers ≈ ~16 min.

    Launch on S2:
      python3 backtest_v8_sweep.py --mode tradier --account trb --start 2024-01-01 \\
          --symbols AAPL,MSFT,NVDA,XOM,GLD,SPY,AMZN,GOOGL \\
          --tier tradier_param_hunt --workers 4 --timeout 900
    """
    from itertools import product as _product
    combos = [("baseline", {})]
    # ── CARTESIAN: 4 core real tradier params ── (3×3×3×2 = 54)
    for wt_exit, k_max, vel_min, htf_min in _product(
        [2, 3, 4],            # WT_EXIT_MIN_TFS_TRADIER default=5 (5=zero-trades, banned)
        [40.0, 80.0, 100.0],  # REENTRY_RALLY_K15M_MAX default=100
        [0.0, 3.0, 6.0],      # CT_WT_VELOCITY_1H_MIN default=0.0
        [2, 3],               # REENTRY_RALLY_HTF_MIN default=3
    ):
        label = f"wt{wt_exit}_k{int(k_max)}_vel{vel_min}_htf{htf_min}"
        combos.append((label, {
            "WT_EXIT_MIN_TFS_TRADIER": wt_exit,
            "REENTRY_RALLY_K15M_MAX": k_max,
            "CT_WT_VELOCITY_1H_MIN": vel_min,
            "REENTRY_RALLY_HTF_MIN": htf_min,
        }))
    # ── ABLATION: entry score threshold (default=24) ──
    for score in [20, 22, 26, 28]:
        combos.append((f"score{score}", {"TRADIER_ENTRY_SCORE_THRESHOLD": score}))
    # ── ABLATION: min hold bars (default=32) ──
    for hold in [16, 24, 48]:
        combos.append((f"hold{hold}", {"MIN_HOLD_BARS_TRADIER": hold}))
    # ── ABLATION: velocity gate toggle ──
    combos.append(("vel_gate_off", {"CT_WT_VELOCITY_GATE_ENABLED": False}))
    return combos


def grid_indicator_audit_v3_full():
    """2026-04-19 FULL indicator-audit matrix — every new switch gets systematic coverage.
    Four sections: (A) single-switch ablations, (B) value sweeps for numeric params,
    (C) category stacks (all R-S together, etc.), (D) full stack.
    Baseline = current live config (what's already ON stays on). Each variant flips
    ONLY the listed overrides vs baseline.
    Total variants: ~70. Recommended symbols: BTCUSDT,ETHUSDT,SOLUSDT,DOTUSDT,LINKUSDT."""
    return [
        # ══════════════════════════════════════════════════════════════════════
        # (A) BASELINE + SINGLE-SWITCH ABLATIONS
        # ══════════════════════════════════════════════════════════════════════
        ("baseline", {}),
        # ── Entry gates (R-G*) ─────────────────────────────────────────────
        ("R_G1_sent_top20", {"SENTIMENT_TOP_N_GATE_ENABLED": True, "SENTIMENT_TOP_N": 20}),
        ("R_G2_mtf_vel_min2", {"WT_MTF_VEL_GATE_ENABLED": True, "WT_MTF_VEL_MIN": 2}),
        ("R_G3_chop_max8", {"WT_CHOP_GATE_ENABLED": True, "WT_CHOP_MAX": 8}),
        ("R_G4_cdelta_off", {"WT_COMPOSITE_DELTA_GATE_ENABLED": False}),  # test turning OFF current default
        ("R_G5_exhaust_entry", {"WT_EXHAUST_ENTRY_GATE_ENABLED": True}),
        ("R_G7_div_entry_off", {"WT_DIV_ENTRY_GATE_ENABLED": False}),  # test turning OFF current default
        ("R_G8_mstate_LTF", {"R_S6_WT_MSTATE_GATE_MODE": 1}),
        ("R_G8_mstate_ALL", {"R_S6_WT_MSTATE_GATE_MODE": 2}),
        ("R_G10_htf_div", {"R_G10_HTF_DIV_GATE_ENABLED": True}),
        # ── Scoring (R-S*) ──────────────────────────────────────────────────
        ("R_S1_cdelta_score", {"R_S1_WT_COMPOSITE_DELTA_USE_ENABLED": True}),
        ("R_S2_adaptive_os", {"R_S2_WT_ADAPTIVE_OS_ENABLED": True}),
        ("R_S3_div_stack", {"R_S3_DIV_STACK_ENABLED": True}),
        ("R_S3_div_stack_htf_w", {"R_S3_DIV_STACK_ENABLED": True, "R_S3_HTF_WEIGHT_ENABLED": True}),
        ("R_S4_ha_streak", {"R_S4_HA_STREAK_ENABLED": True}),
        ("R_S5_sent_vel", {"R_S5_SENT_VEL_ENABLED": True}),
        ("R_S7_hhll_stack", {"R_S7_HHLL_STACK_ENABLED": True}),
        # ── Sizing (R-Z*) ──────────────────────────────────────────────────
        ("R_Z1_ranking_mult", {"RANKING_MULT_ENABLED": True}),
        ("R_Z2_pct_scaler", {"R_Z2_PERCENTILE_SCALER_ENABLED": True}),
        ("R_Z3_wt_comp_size", {"R_Z3_WT_COMPOSITE_SIZE_ENABLED": True}),
        ("R_Z4_crash_gradient", {"CRASH_MULT_GRADIENT_ENABLED": True}),
        ("R_Z5_dc_pullback", {"R_Z5_DC_PULLBACK_SIZING_ENABLED": True}),
        # ── Reentry (RE-*) ─────────────────────────────────────────────────
        ("RE_1_cross_fresh", {"REENTRY_CROSS_FRESHNESS_ENABLED": True}),
        ("RE_2_use_pct", {"RE_2_USE_PERCENTILE_ENABLED": True}),
        ("RE_3_b12_rising", {"RE_3_B12_RISING_BONUS_ENABLED": True}),
        ("RE_4_b14_streak", {"RE_4_B14_HA_STREAK_CONV_ENABLED": True}),
        ("RE_5_b04_compression", {"RE_5_B04_COMPRESSION_BONUS_ENABLED": True}),
        ("RE_6_wave_phase", {"RE_6_WAVE_PHASE_GATE_ENABLED": True}),
        ("REENTRY_exhausted_off", {"REENTRY_EXHAUSTED_PARTIAL_ENABLED": False}),
        ("REENTRY_B16_off", {"REENTRY_B16_SMA200_PULLBACK_ENABLED": False}),
        # ── Exits (E-*) ────────────────────────────────────────────────────
        ("E_1_delta_exit", {"E_1_WT_EXIT_USE_DELTA_ENABLED": True}),
        ("E_3_struct_shadow", {"E_3_USE_WT_STRUCTURE_EXIT_MODE": 1}),
        ("E_3_struct_live", {"E_3_USE_WT_STRUCTURE_EXIT_MODE": 2}),
        ("WT_4H_VEL_EXIT_off", {"WT_4H_VEL_EXIT_ENABLED": False}),
        ("DC_HOPELESS_off", {"DC_HOPELESS_EXIT_ENABLED": False}),
        ("WT_EXHAUST_EXIT_off", {"WT_EXHAUST_EXIT_ENABLED": False}),
        # ══════════════════════════════════════════════════════════════════════
        # (B) VALUE SWEEPS (numeric params for the most impactful switches)
        # ══════════════════════════════════════════════════════════════════════
        # R-G1 sentiment top-N sweep
        ("R_G1_top10", {"SENTIMENT_TOP_N_GATE_ENABLED": True, "SENTIMENT_TOP_N": 10}),
        ("R_G1_top30", {"SENTIMENT_TOP_N_GATE_ENABLED": True, "SENTIMENT_TOP_N": 30}),
        # R-G2 MTF-vel min sweep
        ("R_G2_vel_min3", {"WT_MTF_VEL_GATE_ENABLED": True, "WT_MTF_VEL_MIN": 3}),
        ("R_G2_vel_min4", {"WT_MTF_VEL_GATE_ENABLED": True, "WT_MTF_VEL_MIN": 4}),
        # R-G3 chop max sweep
        ("R_G3_chop_max6", {"WT_CHOP_GATE_ENABLED": True, "WT_CHOP_MAX": 6}),
        ("R_G3_chop_max12", {"WT_CHOP_GATE_ENABLED": True, "WT_CHOP_MAX": 12}),
        # R-G10 HTF-div scope sweep
        ("R_G10_D_only", {"R_G10_HTF_DIV_GATE_ENABLED": True, "R_G10_HTF_DIV_TFS": "D"}),
        ("R_G10_1h_4h_D", {"R_G10_HTF_DIV_GATE_ENABLED": True, "R_G10_HTF_DIV_TFS": "1h,4h,D"}),
        # R-S1 delta threshold sweep
        ("R_S1_thr30", {"R_S1_WT_COMPOSITE_DELTA_USE_ENABLED": True, "R_S1_WT_COMPOSITE_DELTA_THR": 30.0}),
        ("R_S1_thr80", {"R_S1_WT_COMPOSITE_DELTA_USE_ENABLED": True, "R_S1_WT_COMPOSITE_DELTA_THR": 80.0}),
        # R-S2 percentile sweep
        ("R_S2_os5", {"R_S2_WT_ADAPTIVE_OS_ENABLED": True, "R_S2_WT_PCT_OS_LONG": 5.0, "R_S2_WT_PCT_OB_SHORT": 95.0}),
        ("R_S2_os15", {"R_S2_WT_ADAPTIVE_OS_ENABLED": True, "R_S2_WT_PCT_OS_LONG": 15.0, "R_S2_WT_PCT_OB_SHORT": 85.0}),
        # R-S3 HTF weight magnitude sweep (how heavy to make HTF divergences)
        ("R_S3_htf_w_moderate", {"R_S3_DIV_STACK_ENABLED": True, "R_S3_HTF_WEIGHT_ENABLED": True, "R_S3_TF_WEIGHT_4H": 2.0, "R_S3_TF_WEIGHT_D": 3.0}),
        ("R_S3_htf_w_heavy", {"R_S3_DIV_STACK_ENABLED": True, "R_S3_HTF_WEIGHT_ENABLED": True, "R_S3_TF_WEIGHT_4H": 3.5, "R_S3_TF_WEIGHT_D": 6.0}),
        # R-S7 HH/LL configuration sweep
        ("R_S7_strict", {"R_S7_HHLL_STACK_ENABLED": True, "R_S7_HHLL_MIN_INDICATORS": 3, "R_S7_HHLL_MIN_TFS_FOR_BONUS": 3}),
        ("R_S7_htf_only", {"R_S7_HHLL_STACK_ENABLED": True, "R_S7_HHLL_TFS": "1h,4h,D"}),
        ("R_S7_big_bonus", {"R_S7_HHLL_STACK_ENABLED": True, "R_S7_HHLL_BONUS_PER_TF": 6.0}),
        # R-Z1 ranking mult MAX sweep
        ("R_Z1_max1_5", {"RANKING_MULT_ENABLED": True, "RANKING_MULT_MAX": 1.5}),
        ("R_Z1_max3_0", {"RANKING_MULT_ENABLED": True, "RANKING_MULT_MAX": 3.0}),
        # R-Z3 WT composite size threshold sweep
        ("R_Z3_aggressive", {"R_Z3_WT_COMPOSITE_SIZE_ENABLED": True, "R_Z3_T1_THR": 30.0, "R_Z3_T2_THR": 60.0, "R_Z3_T3_THR": 90.0}),
        # E-1 delta exit threshold sweep
        ("E_1_thr30", {"E_1_WT_EXIT_USE_DELTA_ENABLED": True, "E_1_EXIT_DELTA_THR": 30.0}),
        ("E_1_thr80", {"E_1_WT_EXIT_USE_DELTA_ENABLED": True, "E_1_EXIT_DELTA_THR": 80.0}),
        # ══════════════════════════════════════════════════════════════════════
        # (C) CATEGORY STACKS — all switches in a category ON together
        # ══════════════════════════════════════════════════════════════════════
        ("STACK_all_gates_on", {
            "SENTIMENT_TOP_N_GATE_ENABLED": True, "WT_MTF_VEL_GATE_ENABLED": True,
            "WT_CHOP_GATE_ENABLED": True, "R_G10_HTF_DIV_GATE_ENABLED": True,
            "R_S6_WT_MSTATE_GATE_MODE": 1,
        }),
        ("STACK_all_scoring_on", {
            "R_S1_WT_COMPOSITE_DELTA_USE_ENABLED": True, "R_S2_WT_ADAPTIVE_OS_ENABLED": True,
            "R_S3_DIV_STACK_ENABLED": True, "R_S3_HTF_WEIGHT_ENABLED": True,
            "R_S4_HA_STREAK_ENABLED": True, "R_S5_SENT_VEL_ENABLED": True,
            "R_S7_HHLL_STACK_ENABLED": True,
        }),
        ("STACK_all_sizing_on", {
            "RANKING_MULT_ENABLED": True, "R_Z2_PERCENTILE_SCALER_ENABLED": True,
            "R_Z3_WT_COMPOSITE_SIZE_ENABLED": True, "CRASH_MULT_GRADIENT_ENABLED": True,
            "R_Z5_DC_PULLBACK_SIZING_ENABLED": True,
        }),
        ("STACK_all_reentry_on", {
            "REENTRY_CROSS_FRESHNESS_ENABLED": True, "RE_2_USE_PERCENTILE_ENABLED": True,
            "RE_3_B12_RISING_BONUS_ENABLED": True, "RE_4_B14_HA_STREAK_CONV_ENABLED": True,
            "RE_5_B04_COMPRESSION_BONUS_ENABLED": True, "RE_6_WAVE_PHASE_GATE_ENABLED": True,
        }),
        ("STACK_all_exits_on", {
            "E_1_WT_EXIT_USE_DELTA_ENABLED": True, "E_3_USE_WT_STRUCTURE_EXIT_MODE": 2,
        }),
        ("STACK_structural_on", {  # everything HH/LL + HTF divergence related
            "R_S3_DIV_STACK_ENABLED": True, "R_S3_HTF_WEIGHT_ENABLED": True,
            "R_G10_HTF_DIV_GATE_ENABLED": True, "R_S7_HHLL_STACK_ENABLED": True,
            "E_3_USE_WT_STRUCTURE_EXIT_MODE": 1,  # shadow — observe before firing
        }),
        # ══════════════════════════════════════════════════════════════════════
        # (D) FULL STACK — every new switch ON (stress test)
        # ══════════════════════════════════════════════════════════════════════
        ("FULL_STACK_all_on", {
            # Gates
            "SENTIMENT_TOP_N_GATE_ENABLED": True, "WT_MTF_VEL_GATE_ENABLED": True,
            "WT_CHOP_GATE_ENABLED": True, "R_G10_HTF_DIV_GATE_ENABLED": True,
            "R_S6_WT_MSTATE_GATE_MODE": 1,
            # Scoring
            "R_S1_WT_COMPOSITE_DELTA_USE_ENABLED": True, "R_S2_WT_ADAPTIVE_OS_ENABLED": True,
            "R_S3_DIV_STACK_ENABLED": True, "R_S3_HTF_WEIGHT_ENABLED": True,
            "R_S4_HA_STREAK_ENABLED": True, "R_S5_SENT_VEL_ENABLED": True,
            "R_S7_HHLL_STACK_ENABLED": True,
            # Sizing
            "RANKING_MULT_ENABLED": True, "R_Z2_PERCENTILE_SCALER_ENABLED": True,
            "R_Z3_WT_COMPOSITE_SIZE_ENABLED": True, "CRASH_MULT_GRADIENT_ENABLED": True,
            "R_Z5_DC_PULLBACK_SIZING_ENABLED": True,
            # Reentry
            "REENTRY_CROSS_FRESHNESS_ENABLED": True, "RE_2_USE_PERCENTILE_ENABLED": True,
            "RE_3_B12_RISING_BONUS_ENABLED": True, "RE_4_B14_HA_STREAK_CONV_ENABLED": True,
            "RE_5_B04_COMPRESSION_BONUS_ENABLED": True, "RE_6_WAVE_PHASE_GATE_ENABLED": True,
            # Exits (delta exit ON, structure shadow only to observe)
            "E_1_WT_EXIT_USE_DELTA_ENABLED": True, "E_3_USE_WT_STRUCTURE_EXIT_MODE": 1,
        }),
    ]


# ══════════════════════════════════════════════════════════════════════════════
# VERSION GUARD — fingerprint critical engine files at sweep launch
# ══════════════════════════════════════════════════════════════════════════════

_CRITICAL_ENGINE_FILES = [
    "backtest_v8_engine.py", "backtest_v8_harness.py", "backtest_v8_precompute.py",
    "ez_positions_quick.py", "ez_manage.py", "config.py",
    "wt_dc_delta.py", "wt_dc_entry_scorer.py", "wt_dc_exit_scorer.py",
]


def _engine_fingerprint() -> dict:
    """Compute MD5 of every critical engine file. Result embedded in CSV + sidecar JSON.
    Any post-hoc review can verify which code version produced which results."""
    from pathlib import Path as _P
    base = _P(__file__).resolve().parent
    out = {}
    for fname in _CRITICAL_ENGINE_FILES:
        fp = base / fname
        if not fp.exists():
            out[fname] = "MISSING"
            continue
        h = hashlib.md5()
        with fp.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        out[fname] = h.hexdigest()
    return out


def grid_system_combo():
    """Full-system high-impact combo sweep — every entry & exit path stays
    ENABLED (no ablation), we only vary the knobs with proven leverage on
    Sharpe / WR / total-gain. Each variant is a real backtest_v8_engine run
    with the FULL pipeline live: WT_DC_ENTRY + B_MAIN_ENTRY (SATOSHIT,
    STDEV_*) + DELTA_ENTRY + REENTRY_B01-B15 + WT/DC/RZ/STRUCTURAL exits +
    PPL + HEDGE + STRICT_NO_LOSS + LH/HL filter + FUNDING_GATE +
    OI_CONFIRM. We don't add new strategies; we tune what's already wired.

    High-leverage knobs (per CLAUDE.md memory + canonical_switches.json):
      - Entry score floor (TRADIER_ENTRY_SCORE_THRESHOLD / ENTRY_SCORE_THRESHOLD)
      - K-zone reversal thresholds + bonus
      - Stoch entry thresholds
      - WT exit min-TFs (how many timeframes must agree to exit)
      - Structural exit on/off + TF
      - RZ_EXIT on/off
      - DC daytrade max-hold
      - Reentry B-block enable/disable + size mult
      - DELTA_ENTRY + DELTA_HTF gate
      - PPL gain trigger + arm pct (currently 0.5/0.75 — sweep around)
    """
    out = [("baseline", {})]

    # 1. Entry score threshold — primary trade-frequency vs quality gate
    for v in (16, 18, 20, 22, 24, 26, 28):
        out.append((f"ENTRY_SCORE_{v}", {"ENTRY_SCORE_THRESHOLD": v,
                                          "TRADIER_ENTRY_SCORE_THRESHOLD": v}))

    # 2. K-zone reversal bonus + thresholds (cross-bias sizing kick)
    for bonus in (4, 6, 8, 10, 12):
        out.append((f"K_ZONE_BONUS_{bonus}", {"TRADIER_K_ZONE_ENTRY_BONUS_TRADIER": bonus}))

    # 3. WT exit alignment count — how many TFs must agree to exit (1..5)
    for n in (1, 2, 3, 4, 5):
        out.append((f"WT_EXIT_MIN_TFS_{n}", {"TRADIER_WT_EXIT_MIN_TFS_TRADIER": n,
                                              "WT_EXIT_MIN_TFS": n}))

    # 4. Structural range-shift exit ON / OFF
    out.append(("STRUCTURAL_EXIT_OFF", {"STRUCTURAL_RANGE_SHIFT_EXIT": False}))
    for tf in ("3m", "15m", "1h", "4h", "D"):
        out.append((f"STRUCTURAL_TF_{tf}", {"STRUCTURAL_RANGE_SHIFT_EXIT": True,
                                             "STRUCTURAL_RANGE_SHIFT_TF": tf}))

    # 5. RZ exit ON / OFF (can be too aggressive)
    out.append(("RZ_EXIT_OFF", {"RZ_EXIT_ENABLED": False}))

    # 6. PPL trigger gain — locking profits earlier vs later
    for g in (0.3, 0.5, 0.75, 1.0):
        out.append((f"PPL_GAIN_{g}", {"PARTIAL_PROFIT_LOCK_GAIN_PCT": g,
                                       "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": g + 0.25}))

    # 7. Reentry B-block gates — sizing multipliers
    for m in (0.5, 1.0, 1.5, 2.0):
        out.append((f"REENTRY_SIZE_{m}", {"REENTRY_WT15M_SIZE_MULT": m,
                                           "REENTRY_K15M_PARTIAL_MULT": m * 0.5,
                                           "REENTRY_POST_CONSOL_MULT": m}))

    # 8. DELTA engine on/off + HTF gate
    for d_on in (True, False):
        for htf in ("none", "any", "all"):
            out.append((f"DELTA_{'ON' if d_on else 'OFF'}_HTF_{htf}",
                        {"DELTA_ENGINE_ENABLED": d_on, "DELTA_HTF_GATE": htf}))

    # 9. SATOSHIT entry (proven entry pattern) on/off
    out.append(("SATOSHIT_ON",  {"SATOSHIT_ENABLED": True, "SATOSHIT_ENABLED_TRADIER": True}))
    out.append(("SATOSHIT_OFF", {"SATOSHIT_ENABLED": False, "SATOSHIT_ENABLED_TRADIER": False}))

    # 10. FUNDING_GATE thresholds (proven directional per memory)
    for th in (0.0, 0.005, 0.01, 0.02):
        out.append((f"FUNDING_TH_{th}", {"FUNDING_GATE_LONG_MAX": -th,
                                          "FUNDING_GATE_SHORT_MIN": +th}))

    return out


TIER_MAP = {
    "hedge_one_by_one": grid_hedge_one_by_one,
    "reentry_one_by_one": grid_reentry_one_by_one,
    "reentry_wide": grid_reentry_wide,
    "reentry_optimize": grid_reentry_optimize,
    "reentry_all_real": grid_reentry_all_real,
    "reentry_killed_rerun": grid_reentry_killed_rerun,
    "hedge_reentry_ablation": grid_hedge_reentry_ablation,
    "hedge_full": grid_hedge_full,
    "indicator_audit": grid_indicator_audit,
    "indicator_audit_v2": grid_indicator_audit_v2,
    "indicator_audit_v3_full": grid_indicator_audit_v3_full,
    "tradier_param_hunt": grid_tradier_param_hunt,
    "system_combo": grid_system_combo,
}


# ══════════════════════════════════════════════════════════════════════════════
# SUBPROCESS RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def _config_hash(cfg: dict) -> str:
    return hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:8]


def run_one_variant(args_tuple):
    """Worker: writes override JSON, runs engine subprocess, parses V8_RESULT."""
    (label, overrides, mode, account, start, symbols, capital, npz_dir, timeout_s, kill_sharpe, kill_secs) = args_tuple
    # MERGE BASE OVERRIDES applied to every variant (including baseline):
    # - USDC_PREFERENCE_BLOCK_ENABLED=False: NPZ data is USDT-only, so the live USDC-preference
    #   gate would block 100% of USDT opens. Turn it off for backtest so entries are evaluated.
    #   This is a DATA shape concern, not a research variable.
    merged = {"USDC_PREFERENCE_BLOCK_ENABLED": False}
    merged.update(overrides)
    overrides = merged
    cfg_hash = _config_hash(overrides)
    override_path = OVERRIDE_DIR / f"v8sweep_{label}_{cfg_hash}.json"
    override_path.write_text(json.dumps(overrides, indent=2))

    cmd = [
        PY_BIN, str(ENGINE_PATH),
        "--mode", mode,
        "--account", account,
        "--start", start,
        "--capital", str(capital),
    ]
    if symbols:
        cmd += ["--symbols", symbols]
    if npz_dir:
        cmd += ["--npz-dir", npz_dir]

    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override_path)
    env["V8_SWEEP_MODE"] = "1"  # suppress per-trade logs, 10-50x speedup

    t0 = time.time()
    try:
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        stderr_lines = []
        def _read_stderr():
            try:
                for ln in proc.stderr:
                    stderr_lines.append(ln.rstrip())
            except Exception:
                pass
        t_err = threading.Thread(target=_read_stderr, daemon=True)
        t_err.start()
        match = None
        match_tradier = None
        live_sharpe = None
        live_closes = 0
        killed = False
        stdout_tail = []
        try:
            for line in proc.stdout:
                line = line.rstrip()
                stdout_tail.append(line)
                if len(stdout_tail) > 20:
                    stdout_tail.pop(0)
                m_live = V8_RESULT_LIVE_RE.search(line)
                if m_live:
                    try:
                        live_sharpe = float(m_live["sharpe_w"])
                    except Exception:
                        pass
                m_live_simple = V8_RESULT_LIVE_SIMPLE_RE.search(line)
                if m_live_simple:
                    try:
                        live_closes = int(m_live_simple["closes"])
                    except Exception:
                        pass
                m_final = V8_RESULT_RE.search(line)
                if m_final:
                    match = m_final
                m_tradier = V8_RESULT_TRADIER_RE.search(line)
                if m_tradier:
                    match_tradier = m_tradier
                elapsed = time.time() - t0
                if elapsed > timeout_s:
                    proc.kill()
                    killed = True
                    break
                if kill_sharpe > 0 and elapsed >= kill_secs:
                    if live_sharpe is not None and live_sharpe < kill_sharpe:
                        proc.kill()
                        killed = True
                        break
                    if live_sharpe is None and live_closes == 0:
                        proc.kill()
                        killed = True
                        break
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        t_err.join(timeout=2)
        elapsed = time.time() - t0
        if killed and not match and not match_tradier:
            status = "timeout" if elapsed > timeout_s else "killed_low_sharpe"
            return {
                "label": label, "overrides": overrides, "cfg_hash": cfg_hash,
                "status": status, "elapsed_s": round(elapsed, 1),
                "live_sharpe": live_sharpe, "live_closes": live_closes, "rc": proc.returncode,
                "stderr_tail": stderr_lines[-5:],
            }
        if match:
            return {
                "label": label,
                "overrides": overrides,
                "cfg_hash": cfg_hash,
                "status": "ok",
                "sharpe_w": float(match["sharpe_w"]),
                "sharpe_pt": float(match["sharpe_pt"]),
                "sharpe_ann": float(match["sharpe_ann"]),
                "gain_pct": float(match["gain_pct"]),
                "closes": int(match["closes"]),
                "wins": int(match["wins"]),
                "losses": int(match["losses"]),
                "elapsed_s": round(elapsed, 1),
                "rc": proc.returncode,
                "stderr_tail": stderr_lines[-5:],
            }
        if match_tradier:
            _sharpe = float(match_tradier["sharpe"])
            return {
                "label": label,
                "overrides": overrides,
                "cfg_hash": cfg_hash,
                "status": "ok",
                "sharpe_w": _sharpe,
                "sharpe_pt": _sharpe,
                "sharpe_ann": _sharpe,
                "gain_pct": float(match_tradier["pnl"]),
                "closes": int(match_tradier["trades"]),
                "wins": int(match_tradier["wins"]),
                "losses": int(match_tradier["losses"]),
                "elapsed_s": round(elapsed, 1),
                "rc": proc.returncode,
                "stderr_tail": stderr_lines[-5:],
            }
        return {
            "label": label, "overrides": overrides, "cfg_hash": cfg_hash,
            "status": "no_result", "elapsed_s": round(elapsed, 1), "rc": proc.returncode,
            "stderr_tail": stderr_lines[-5:],
            "stdout_tail": stdout_tail[-5:],
        }
    except Exception as e:
        return {
            "label": label, "overrides": overrides, "cfg_hash": cfg_hash,
            "status": "error", "error": str(e),
        }


# ══════════════════════════════════════════════════════════════════════════════
# CSV WRITER — incremental flush so interruptions don't lose results
# ══════════════════════════════════════════════════════════════════════════════

CSV_FIELDS = [
    "label", "cfg_hash", "status", "sharpe_w", "sharpe_pt", "sharpe_ann",
    "gain_pct", "closes", "wins", "losses", "win_rate", "elapsed_s", "rc",
    "overrides_json",
]


def _result_to_row(r: dict) -> dict:
    wins = r.get("wins", 0) or 0
    losses = r.get("losses", 0) or 0
    total = wins + losses
    return {
        "label": r.get("label", ""),
        "cfg_hash": r.get("cfg_hash", ""),
        "status": r.get("status", ""),
        "sharpe_w": r.get("sharpe_w", ""),
        "sharpe_pt": r.get("sharpe_pt", ""),
        "sharpe_ann": r.get("sharpe_ann", ""),
        "gain_pct": r.get("gain_pct", ""),
        "closes": r.get("closes", ""),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 1) if total > 0 else "",
        "elapsed_s": r.get("elapsed_s", ""),
        "rc": r.get("rc", ""),
        "overrides_json": json.dumps(r.get("overrides", {}), sort_keys=True),
    }


def write_csv_row(path: Path, row: dict, header_written: bool) -> None:
    mode = "a" if header_written else "w"
    with path.open(mode, newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not header_written:
            w.writeheader()
        w.writerow(row)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    ap.add_argument("--account", default="ang")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--symbols", default="", help="Comma-separated; empty=all NPZ symbols")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--npz-dir", default="", help="Explicit NPZ dir, leave empty for default")
    ap.add_argument("--tier", default="hedge_one_by_one", choices=sorted(TIER_MAP.keys()))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600, help="Per-variant subprocess timeout (s)")
    ap.add_argument("--kill-sharpe", type=float, default=0.0, help="Kill variant if live sharpe < this after --kill-secs (0=disabled)")
    ap.add_argument("--kill-secs", type=int, default=60, help="Seconds elapsed before early-kill is checked")
    ap.add_argument("--output", default="", help="CSV output path (default: auto-dated)")
    args = ap.parse_args()

    grid = TIER_MAP[args.tier]()
    kill_info = f"  kill_sharpe={args.kill_sharpe} kill_secs={args.kill_secs}" if args.kill_sharpe > 0 else ""
    print(f"[sweep] tier={args.tier}  variants={len(grid)}  mode={args.mode}  account={args.account}  start={args.start}  symbols={args.symbols or 'ALL'}  workers={args.workers}{kill_info}")

    # VERSION FINGERPRINT — print MD5 of every critical engine file so the sweep run
    # is self-describing. Any downstream CSV consumer can verify which code version
    # produced the results.
    fp = _engine_fingerprint()
    print("[sweep] engine fingerprint:")
    for _f, _h in fp.items():
        short = _h[:10] if _h != "MISSING" else _h
        print(f"[sweep]   {_f:40s} {short}")
    missing = [f for f, h in fp.items() if h == "MISSING"]
    if missing:
        print(f"[sweep] ❌ CRITICAL: {len(missing)} engine file(s) missing: {missing}. Refusing to run.")
        return 2

    out_path = Path(args.output) if args.output else (
        RESULTS_DIR / f"backtest_v8_sweep_{args.tier}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv"
    )
    print(f"[sweep] output -> {out_path}")

    # Write sidecar .version.json next to the CSV so fingerprints are never lost.
    import socket as _sock
    version_path = out_path.with_suffix(".version.json")
    version_meta = {
        "tier": args.tier,
        "mode": args.mode,
        "account": args.account,
        "start": args.start,
        "symbols": args.symbols or "ALL",
        "host": _sock.gethostname(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "engine_fingerprint": fp,
        "variant_count": len(grid),
    }
    version_path.parent.mkdir(parents=True, exist_ok=True)
    with version_path.open("w") as _vf:
        json.dump(version_meta, _vf, indent=2, sort_keys=True)
    print(f"[sweep] version sidecar -> {version_path}")

    tasks = [
        (label, overrides, args.mode, args.account, args.start, args.symbols, args.capital, args.npz_dir, args.timeout, args.kill_sharpe, args.kill_secs)
        for (label, overrides) in grid
    ]

    header_written = False
    t_start = time.time()
    completed = 0
    baseline_sharpe = None

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one_variant, t): t[0] for t in tasks}
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"label": label, "status": "worker_error", "error": str(e)}
            completed += 1
            row = _result_to_row(res)
            write_csv_row(out_path, row, header_written)
            header_written = True
            delta_s = ""
            if res.get("status") == "ok":
                s_w = res["sharpe_w"]
                if label == "baseline":
                    baseline_sharpe = s_w
                elif baseline_sharpe is not None:
                    delta_s = f" Δ={s_w - baseline_sharpe:+.3f}"
                print(f"[{completed:3d}/{len(tasks)}] {label:50s} sharpe_w={res['sharpe_w']:+.3f}{delta_s} gain={res['gain_pct']:+.2f}% closes={res['closes']} wr={row['win_rate']}% {res['elapsed_s']:.0f}s")
            elif res.get("status") == "killed_low_sharpe":
                print(f"[{completed:3d}/{len(tasks)}] {label:50s} KILLED live_sharpe={res.get('live_sharpe')} after {res.get('elapsed_s'):.0f}s")
            else:
                print(f"[{completed:3d}/{len(tasks)}] {label:50s} STATUS={res.get('status')} rc={res.get('rc')} {res.get('stderr_tail', [])}")

    elapsed_total = time.time() - t_start
    print(f"\n[sweep] done in {elapsed_total:.0f}s ({elapsed_total/60:.1f}m)  out={out_path}")

    # Best-of summary
    try:
        import csv as _csv
        rows = []
        with out_path.open() as f:
            for r in _csv.DictReader(f):
                if r["status"] == "ok" and r["sharpe_w"]:
                    rows.append(r)
        rows.sort(key=lambda r: float(r["sharpe_w"] or 0), reverse=True)
        print("\nTop 5 by sharpe_w:")
        for r in rows[:5]:
            print(f"  {r['label']:50s} sharpe_w={r['sharpe_w']} gain={r['gain_pct']}% closes={r['closes']} wr={r['win_rate']}%")
    except Exception:
        pass


if __name__ == "__main__":
    main()

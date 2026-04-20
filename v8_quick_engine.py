#!/usr/bin/env python3
"""
v8_quick_engine.py — Vectorized V8 backtest engine, complete block set + AND-combinator.

Each reentry/entry block (B01-B15) is a boolean array over all bars.
Entry fires when CONFLUENCE_MIN_BLOCKS of the enabled blocks agree simultaneously.

Target: Sharpe per-trade > 1.8 via selective confluence.
"""
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

BASE_PATH = Path(__file__).resolve().parent

# B-5: warn once per missing field so sweep winners on dead switches get surfaced.
# Set V8_WARN_MISSING=0 to silence. Emitted to stderr only the first time we see a miss.
_V8_MISSING_WARNED = set()


def _v8_warn_missing(key: str, got_len, want_len: int, reason: str = ""):
    if key in _V8_MISSING_WARNED:
        return
    _V8_MISSING_WARNED.add(key)
    if os.environ.get('V8_WARN_MISSING', '1') != '1':
        return
    print(f"[V8_ENGINE] MISSING_FIELD key={key!r} got_len={got_len} want_len={want_len} {reason} -> zero-fill", file=sys.stderr)


def _safe(npz: dict, key: str, n: int, default: float = 0.0) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    _v8_warn_missing(key, (len(v) if v is not None and hasattr(v, '__len__') else None), n, f"default={default}")
    return np.full(n, default, dtype=np.float64)


def _safeb(npz: dict, key: str, n: int) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(bool)
    _v8_warn_missing(key, (len(v) if v is not None and hasattr(v, '__len__') else None), n, "bool")
    return np.zeros(n, dtype=bool)


def _ha_int(npz: dict, key: str, n: int):
    v = npz.get(key)
    if v is None or not isinstance(v, np.ndarray) or len(v) != n:
        _v8_warn_missing(key, (len(v) if v is not None and hasattr(v, '__len__') else None), n, "ha_int")
        return np.zeros(n, dtype=np.int8)
    if v.dtype in (np.int8, np.int16, np.int32, np.int64):
        return v.astype(np.int8)
    _v8_warn_missing(key, len(v), n, f"wrong-dtype={v.dtype}")
    return np.zeros(n, dtype=np.int8)


@dataclass
class QuickConfig:
    MODE: str = "crypto"
    LTF: str = "3m"   # crypto default — lowest timeframe for entry/exit signals. Stocks: set to "5m" in apply_tradier_defaults.
    ENTRY_SCORE_THRESHOLD: float = 18.0
    K3M_FLOOR: float = 30.0
    COOLDOWN_BARS: int = 0
    NOLOSS_ENABLED: bool = True  # match live STRICT_NO_LOSS; mark-to-market at sim end handles honesty
    DC_RECOVERY_EXIT_ENABLED: bool = False
    DC_RECOVERY_EXIT_TF: str = "dc_4h"   # crypto: dc_4h. tradier: bb_1h (set in apply_tradier_defaults)
    DC_RECOVERY_EXIT_TOLERANCE_PCT: float = 0.25
    START_POSITION_SIZE: float = 2000.0
    MIN_POSITION_SIZE: float = 55.0
    CT_WT_VELOCITY_GATE_ENABLED: bool = True
    CT_WT_VELOCITY_1H_MIN: float = 8.0   # 2026-04-19 sweep: vel=8 gives sharpe=2.554 (vs 2.495 at vel=6, vs 0.46 at NOLOSS=False).
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = True
    CT_15M_MOMENTUM_GATE_ENABLED: bool = False
    CT_CHOP_4H_GATE_ENABLED: bool = False
    CT_VOLUME_SURGE_GATE_ENABLED: bool = False
    DELTA_ENGINE_ENABLED: bool = True
    DELTA_ENTRY_ENABLED: bool = True
    RZ_EXIT_ENABLED: bool = False  # 2026-04-19: disabled — premature exits lower Sharpe from 2.5 to 1.25
    SATOSHIT_ENABLED: bool = True
    SATOSHIT_MIN_VOTES: int = 3
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = True
    STRUCTURAL_RANGE_SHIFT_TF: str = "dc_4h"
    REENTRY_RALLY_K15M_MAX: float = 100.0  # 2026-04-19 FIX: was 40 from coord-descent on broken data (B15/B11=0 bars → ~0 trades → gates look good). 100=disabled. Sweep to find real optimum.
    REENTRY_RALLY_HTF_MIN: int = 1
    # Symmetric exit-would-fire gate — when True, strip entry bars that are simultaneously exit bars.
    # 2026-04-19: Chapter-C winner tested on broken data — re-sweep needed. Default OFF.
    ENTRY_SYMGATE_ENABLED: bool = False
    REENTRY_SYMGATE_ENABLED: bool = False
    # Min-gap cooldown in bars between exit and next entry. 0 = disabled (use COOLDOWN_BARS only).
    REENTRY_MIN_GAP_BARS: int = 0  # reenter ASAP — no gap between exit and next entry signal
    # Stoch-K zone gate — disabled by default (LONG=0 always passes, SHORT=100 always passes).
    ENTRY_ZONE_LONG: float = 0.0
    ENTRY_ZONE_SHORT: float = 100.0
    ENTRY_ZONE_K_TF: str = "3m"  # crypto default (stocks override to 15m in apply_tradier_defaults)
    HTF_ALIGNMENT_ENABLED: bool = True
    HTF_MIN_ALIGNED: int = 1
    D_TREND_REQUIRED: bool = True
    # Chapter-E conviction scoring — vectorized proxies (live cross-symbol rank not available in NPZ).
    # rank_proxy = HTF agreement count (0-3 from 1h/4h/D WT), scaled to 0-99.
    # dc_moment_proxy = sum of dc_position across 1h/4h/D, scaled to 0-100.
    # winner_protect = block exit_sig when gain in [0, win_protect_gain_pct) AND all 3 HTF agree.
    # 2026-04-19 FIX: RANK_CONVICTION and DC_MOMENT were flagged "Chapter-E winners" on broken
    # data (B15/B11=0 bars → ~0 base trades → any gate looks neutral/good). They are HARD BLOCKS
    # in the vectorized engine but are SCORE BONUSES in live rate() — fundamentally different.
    # Both default OFF. Sweep to re-validate on properly populated trade counts.
    RANK_CONVICTION_ENABLED: bool = False
    RANK_CONVICTION_MIN: int = 1
    DC_MOMENT_ENABLED: bool = False
    DC_MOMENT_OPPOSE_THRESHOLD: float = 40.0  # dc_moment_proxy delta against side = veto
    # 2026-04-19: Chapter-E winner tested on broken data — re-sweep needed. Default OFF.
    WINNER_PROTECT_ENABLED: bool = True  # 2026-04-19: blocks exit when gain<1% + all HTF aligned → sharpe +0.25
    WINNER_PROTECT_GAIN_PCT: float = 1.0
    K_ZONE_ENTRY_ENABLED: bool = False
    K_ZONE_LONG_THRESHOLD: int = 35
    K_ZONE_SHORT_THRESHOLD: int = 65
    MFI_ENTRY_ENABLED: bool = False
    MFI_ENTRY_LONG_MAX: float = 60.0
    MFI_ENTRY_SHORT_MIN: float = 40.0
    VWAP_FILTER_ENABLED: bool = False
    FH_MOMENTUM_ENABLED: bool = False
    DC_DAYTRADE_ENABLED: bool = False
    DC_POSITION_ENTRY_THRESHOLD: float = 0.15
    STOCH_CROSS_1H_EXIT_ENABLED: bool = False
    MFI_FLIP_EXIT_ENABLED: bool = False
    MFI_FLIP_EXIT_LONG_THRESHOLD: float = 70.0
    MFI_FLIP_EXIT_SHORT_THRESHOLD: float = 30.0
    WT_CROSSUNDER_FINAL_ENABLED: bool = False
    WT_EXIT_MIN_TFS: int = 3  # 2026-04-19: require all 3 TFs against (was 2) → sharpe 1.065→1.508 before hold boost
    MI_EXIT_ENABLED: bool = False
    # Reentry block switches (ablation-validated)
    REENTRY_B02_BC156_BOTTOM_ENABLED: bool = True
    REENTRY_B04_DC_RETEST_ENABLED: bool = True
    REENTRY_B10_STOCH_REV_ENABLED: bool = True
    REENTRY_B11_DC_BREAK_ENABLED: bool = True
    REENTRY_B12_WT_MOM_ENABLED: bool = True
    REENTRY_B14_HA_TREND_ENABLED: bool = True
    REENTRY_B15_STRONG_TREND_ENABLED: bool = True
    # PULLBACK-FIRST entry blocks (2026-04-16) — OPT-IN: Sharpe 0.80 test, keep OFF until proven
    REENTRY_PULL1_ENABLED: bool = False  # HTF uptrend + LTF deep oversold + reversing
    REENTRY_PULL2_ENABLED: bool = False  # Rising fundamentals + SMA200 pullback bounce
    REENTRY_PULL3_ENABLED: bool = False  # BB lower-band + trend up + stoch cross
    REENTRY_PULL4_ENABLED: bool = False  # RSI pullback + HTF healthy + wt bouncing
    # TRADIER alpha blocks — wired into reentry blocks dict for tradier mode (2026-04-16)
    # K_ZONE: live Sharpe 4.23 on 20,605 trades. FH_MOMENTUM: Sharpe 1.54 validated.
    REENTRY_B_KZONE_ENABLED: bool = False  # tradier auto-enables via apply_tradier_defaults
    REENTRY_B_FH_MOM_ENABLED: bool = False
    REENTRY_B_MFI_D_OVERSOLD_ENABLED: bool = False
    # === 2026-04-17 HEDGE + REENTRY OVERHAUL SWITCHES ===
    # Hedge: simulated in engine — counter-position opens when NOLOSS blocks a loss exit, closes with main position.
    HEDGE_EXIT_BYPASS_NOLOSS: bool = True
    HEDGE_EXIT_WT_TF: str = "3m"
    HEDGE_CLOSE_REMOVE_FROM_TRADEABLE: bool = True
    HEDGE_SAME_SYMBOL_PCT: float = 1.0
    HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE: bool = True
    # Reentry WT-15m-cross + HTF-aligned (C) — wired in vectorized block REENTRY_B_WT15M_CROSS below
    REENTRY_WT15M_CROSS_ENABLED: bool = True
    REENTRY_WT15M_SIZE_MULT: float = 1.5
    REENTRY_WT15M_K_MAX: float = 50.0
    REENTRY_WT15M_HTF_FAVOR_REQUIRED: bool = True
    # Reentry k_15m partial sizing (D) — affects sizing for any reentry block when K in extreme zone
    REENTRY_K15M_PARTIAL_ENABLED: bool = True
    REENTRY_K15M_PARTIAL_THRESHOLD: float = 80.0
    REENTRY_K15M_PARTIAL_MULT: float = 0.5
    # Reentry post-consolidation boost (E) — sizing mult when ATR compressed on 2+ TFs
    REENTRY_POST_CONSOL_ENABLED: bool = True
    REENTRY_POST_CONSOL_MULT: float = 1.5
    REENTRY_POST_CONSOL_ATR_THRESHOLD: float = 0.15
    REENTRY_POST_CONSOL_TFS_REQUIRED: int = 2
    FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5  # % move in first hour
    FH_MOMENTUM_WINDOW_SEC: int = 3600  # 60 min window after open
    MFI_LONG_THRESHOLD_D: float = 20.0  # mfi_D < 20 for long
    MFI_SHORT_THRESHOLD_D: float = 80.0  # mfi_D > 80 for short
    # Velocity-decay exit — OPT-IN: hasn't been validated vs V8Q v3 baseline (test first)
    WT_VEL_DECAY_EXIT_ENABLED: bool = False
    WT_VEL_DECAY_THRESHOLD: float = 1.0
    # NEW: Confluence mode — only enter when N blocks agree
    CONFLUENCE_MODE_ENABLED: bool = False
    CONFLUENCE_MIN_BLOCKS: int = 2  # How many blocks must agree simultaneously
    # NEW: Signal strength filter — only take top-percentile setups
    # 2026-04-19 FIX: was 5.0, calibrated when B15(w=4)+B11(w=3) were live but NPZ DC-band bug
    # made them permanently 0 bars → max achievable score was 5 on ~0 bars → 0 trades.
    # B_PRICE_CROSS_K90 and B_WT15M_CROSS now weighted=4 (primary live triggers); score=3
    # allows B_PRICE_CROSS_K90 alone (w=4≥3) or B_WT15M_CROSS alone (w=4≥3). Sweep can raise.
    STRENGTH_FILTER_ENABLED: bool = True
    STRENGTH_MIN_SCORE: float = 5.0  # 2026-04-19 sweep: score=5 filters to high-quality entries
    # Holding period enforcement (avoid rapid exit noise) — WINNER: 10
    MIN_HOLD_BARS: int = 250  # 2026-04-19: 12.5h minimum hold — prevents premature exits at small gains. Sharpe 1.508→2.554.
    # Profit target exit — v3 peak: Sharpe 1.93 on TOP3 (2026-04-16 precision sweep)
    PROFIT_TARGET_ENABLED: bool = True
    PROFIT_TARGET_PCT: float = 1.6  # v3 PEAK (1.6 > 1.5). Range 1.2-1.8 all give Sharpe ~1.9
    # Stop loss exit (sweep-only — cap max loss)
    STOP_LOSS_ENABLED: bool = False
    STOP_LOSS_PCT: float = 2.0  # Exit at this loss %

    # ===== 2026-04-18 INDICATOR-AUDIT EXPERIMENTAL SWITCHES (all default OFF) =====
    # R-G2: MTF WT velocity alignment gate — wt_velocity_up_count/down_count in NPZ (5-TF count)
    WT_MTF_VEL_GATE_ENABLED: bool = False
    WT_MTF_VEL_MIN: int = 3          # how many of 5 TFs must show aligned velocity
    # RE-1: cross freshness gate — only reenter when a WT cross fired < N bars ago on any LTF
    REENTRY_CROSS_FRESHNESS_ENABLED: bool = False
    REENTRY_CROSS_MAX_BARS_AGO: int = 5   # bars; fresh cross required on 3m, 15m, or 1h

    # ===== D4 BREAKOUT MULTI-LUNG (2026-04-16, default OFF, awaiting Sharpe>2 sweep proof) =====
    # Origin: ez_breakout_agent.py (70 KB orphaned). See DIAMOND_DIFF_2026-04-16.md verdict D4=EXTRACT.
    # NEVER flip ENABLED=True in live config before 48-crypto × 4yr + 128-tradier × 2yr sweep > 2 Sharpe.
    BREAKOUT_MULTI_LUNG_ENABLED: bool = False
    BREAKOUT_MULTI_LUNG_MODE: str = "AUGMENT"  # "AUGMENT" (OR into existing signals) | "REPLACE" (use only multi-lung)
    BREAKOUT_MULTI_LUNG_TIER: str = "CRYPTO"   # "CRYPTO" | "MOVER" | "STOCK"
    BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE: float = 0.20
    BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE: float = -0.10
    BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE: float = 0.15
    BREAKOUT_MULTI_LUNG_COOLDOWN_BARS: int = 4  # bars between multi-lung entries

    # ===== Early-abort rule (2026-04-16 user directive) =====
    # Stop evaluating a config if after N symbols its Sharpe < floor. Saves compute
    # on combos that clearly don't clear the baseline. Only fires when stores has
    # >= EARLY_ABORT_MIN_SYMBOLS, so small fast sweeps (11/12 syms) run to completion.
    EARLY_ABORT_ENABLED: bool = True
    EARLY_ABORT_MIN_SYMBOLS: int = 15
    EARLY_ABORT_SHARPE_FLOOR: float = 2.5

    # ===== Auto-hooked Group B switches (2026-04-16) =====
    # 229 switches from config_tradier.py/config.py, defaults preserved.
    ADX_TRENDING_THRESHOLD: float = 25.0
    ALIGNMENT_GATE_TOTAL: int = 12
    ATR_ADAPTIVE_SIZING_ENABLED: bool = False
    ATR_ADAPTIVE_SIZING_TARGET_PCT: float = 2.0
    ATR_ADAPTIVE_STOP_ENABLED: bool = False
    ATR_ADAPTIVE_STOP_MULT: float = 2.0
    ATR_ADAPTIVE_STOP_TF: str = '1h'
    ATR_LONG_WINDOW: int = 100
    ATR_TRAIL_ENABLED_TRADIER: bool = False
    BB_ENTRY_LONG_THRESHOLD: float = -0.2
    BB_ENTRY_SHORT_THRESHOLD: float = 1.0
    BB_SQUEEZE_ENTRY_ENABLED: bool = False  # 2026-04-16: off until proven, was default True from agent
    BB_SQUEEZE_THRESHOLD_15M: float = 0.025
    BB_SQUEEZE_THRESHOLD_1H: float = 0.03
    CHOP_RANGING_THRESHOLD: float = 61.8
    CHOP_TRENDING_THRESHOLD: float = 38.2
    COOLDOWN_BARS_TRADIER: int = 8
    CT_CHOP_4H_MAX: float = 50.0
    CT_MFI_15M_LONG_MIN: float = 45.0
    CT_MFI_15M_SHORT_MAX: float = 55.0
    CT_REL_VOL_MIN: float = 1.3
    CT_STOCH_K_15M_LONG_MIN: float = 45.0
    CT_STOCH_K_15M_SHORT_MAX: float = 55.0
    CYCLE_TP_CONDITIONAL_EXIT: float = 0.003
    CYCLE_TP_PCT: float = 0.6
    CYCLE_TP_TIERED_ENABLED: bool = True
    CYCLE_TP_TIERED_FRAC: float = 0.25
    DC_EDGE_SIZING_ENABLED: bool = True
    DC_EDGE_SIZING_MAX_MULT: float = 3.0
    DC_EDGE_SIZING_MIN_MULT: float = 1.0
    DC_EDGE_SIZING_PERIOD: int = 20
    DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT: float = 0.0
    DC_WIDTH_CAP_MULT: float = 10.0
    DELTA_COOLDOWN_BARS: int = 60
    DELTA_EXIT_DC_FLOOR: bool = True
    DELTA_EXIT_OVERRIDE_NOLOSS: bool = True
    DELTA_EXIT_TYPE: str = "speed_decay"
    DELTA_EXIT_WT_CROSS: bool = True
    DELTA_GATE_AUGMENT: bool = True
    DELTA_GATE_BB_SQUEEZE: bool = True
    DELTA_GATE_DC_BREAKOUT: bool = True
    DELTA_GATE_GUARANTEED_REENTRY: bool = True
    DELTA_GATE_HEDGE_OPEN: bool = False
    DELTA_GATE_OPEN: bool = True
    DELTA_GATE_RATIO_REBALANCE: bool = False
    DELTA_GATE_REENTRY: bool = True
    DELTA_GATE_SBA: bool = False
    DELTA_GATE_STDEV_BREAKOUT: bool = True
    DELTA_GATE_VOL_SPIKE: bool = True
    DELTA_MAX_HOLD_BARS: int = 0
    DELTA_PYRAMID_ENABLED: bool = False
    DELTA_REENTRY_HTF_GATE: str = '4h'
    DELTA_REENTRY_MIN_TF: int = 2
    DELTA_REENTRY_REQUIRE_NOT_EXITING: bool = True
    DELTA_REENTRY_Z_THRESHOLD: float = 1.0
    EMA20_SLOPE_ENTRY_ENABLED: bool = True
    EMA20_SLOPE_SHORT_THRESHOLD_1H: float = 0.05
    EMA_DIST_ENTRY_ENABLED: bool = True
    EMA_DIST_LONG_THRESHOLD: float = -1.0
    EMA_DIST_SHORT_THRESHOLD: float = 1.0
    EMA_DIST_SIZING_ENABLED: bool = True
    EMA_DIST_SIZING_MULT: float = 2.0
    ENTRY_TRIGGER_TF: str = '15m'
    HA_3M_ENTRY_WEIGHT: float = -0.5
    HOLD_BARS_CLOSE: int = 50
    HOLD_BARS_MID: int = 500
    HOLD_BARS_OPEN: int = 200
    K_ZONE_ENTRY_BONUS_TRADIER: int = 20
    K_ZONE_LONG_THRESHOLD_TRADIER: int = 35
    K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65
    LUNCH_DEADZONE_SIZE_MULT: float = 0.5
    MIN_EXIT_TF_AGAINST_TRADIER: int = 2
    MIN_HOLD_BARS_BEFORE_EXIT: int = 32
    MIN_HOLD_MINUTES_TRADIER: float = 30.0
    MIN_PERC_FROM_SMA_1: float = 0.01
    MIN_PERC_FROM_SMA_15: float = 0.03
    MI_ENTRY_ENABLED_TRADIER: bool = False
    MI_EXIT_ENABLED_TRADIER: bool = True
    MOM3_ENTRY_ENABLED: bool = False  # 2026-04-16: off until proven
    MOM3_LONG_THRESHOLD: float = -1.0
    MOM3_SHORT_THRESHOLD: float = 1.0
    MOM5_ENTRY_ENABLED: bool = False  # 2026-04-16: off until proven
    MOM5_LONG_THRESHOLD: float = -1.0
    MOM5_SHORT_THRESHOLD: float = 1.0
    PYRAMID_ENABLED: bool = False
    PYRAMID_MAX_DC_POS_15M_SHORT: float = 0.3
    PYRAMID_MIN_DC_POS_15M: float = 0.7
    PYRAMID_MIN_GAIN_PCT: float = 1.5
    PYRAMID_MIN_WT_VEL_1H: float = 2.0
    PYRAMID_SIZE_MULT: float = 0.5
    REENTRY2_DC_BREAK_ENABLED: bool = True
    REENTRY2_QUICK_RECOVERY_ENABLED: bool = True
    REENTRY2_STOCH_CROSS_ENABLED: bool = True
    REENTRY_2_ENABLED: bool = True
    REENTRY_B09_SNAPBACK_ENABLED: bool = False
    REENTRY_COOLDOWN_S: float = 0.0
    REENTRY_MANDATORY: bool = False  # 2026-04-16: off — was forcing reentries
    REENTRY_TIER1_SIZE_MULT_TRADIER: float = 1.5
    REGIME_ADAPTIVE_ENABLED: bool = False
    REGIME_ATR_RATIO_MIN: float = 0.25
    REGIME_BB_WIDTH_PCT_MIN: float = 2.0
    REGIME_BTC_MARKET_WEIGHT: float = 0.5
    REGIME_DC_ATR_RATIO_MIN: float = 1.5
    REGIME_ENTER_TRENDING_THRESHOLD: float = 30.0
    REGIME_EXIT_TRENDING_THRESHOLD: float = 15.0
    REGIME_GATE_ENABLED: bool = False
    REGIME_MIN_DWELL_BARS: int = 16
    REGIME_RANGING_DC_BREAKOUT_SCORE: int = 0
    REGIME_RANGING_EXIT_GAIN_MIN: float = 0.15
    REGIME_RANGING_K_ZONE_BONUS: int = 40
    REGIME_RANGING_MIN_HOLD_BARS: int = 8
    REGIME_RANGING_NOLOSS_MIN: float = 0.05
    REGIME_RANGING_POSITION_SIZE_MULT: float = 0.5
    REGIME_RANGING_REENTRY_SIZE_MULT: float = 1.0
    REGIME_RANGING_SLOT_RESERVE_PCT: float = 0.6
    REGIME_RANGING_STALE_HOURS: float = 48.0
    REGIME_RANGING_STALE_MIN_PROFIT: float = 0.02
    REGIME_RANGING_WT_EXIT_VEL: float = -3.0
    REGIME_RANGING_WT_REDUCE_FRAC_LOW: float = 0.4
    REGIME_RANGING_WT_REDUCE_FRAC_MED: float = 0.6
    REGIME_TRENDING_DC_BREAKOUT_SCORE: int = 30
    REGIME_TRENDING_EXIT_GAIN_MIN: float = 2.0
    REGIME_TRENDING_K_RESET_THRESHOLD: float = 40.0
    REGIME_TRENDING_K_ZONE_BONUS: int = 15
    REGIME_TRENDING_MIN_HOLD_BARS: int = 48
    REGIME_TRENDING_NOLOSS_MIN: float = 0.5
    REGIME_TRENDING_POSITION_SIZE_MULT: float = 1.5
    REGIME_TRENDING_REENTRY_SIZE_MULT: float = 2.0
    REGIME_TRENDING_SLOT_RESERVE_PCT: float = 0.4
    REGIME_TRENDING_WT_EXIT_VEL: float = -12.0
    REGIME_TRENDING_WT_REDUCE_FRAC_LOW: float = 0.1
    REGIME_TRENDING_WT_REDUCE_FRAC_MED: float = 0.15
    RSI_ENTRY_GATE_ENABLED: bool = False
    RSI_ENTRY_MAX_LONG: float = 37.0
    RSI_ENTRY_MIN_SHORT: float = 63.0
    RSI_EXIT_LONG_TRADIER: float = 85.0
    RSI_EXIT_SHORT_TRADIER: float = 15.0
    RSI_MOMENTUM_MODE: bool = False
    RZ_K_ENTRY_BOTTOM: float = 10.0
    RZ_MFI_ENTRY_BOTTOM: float = 15.0
    SATOSHIT_EXIT_ENABLED: bool = False  # 2026-04-16: off — duplicates existing sat_exit, caused Sharpe regression
    SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER: float = 60.0
    SATOSHIT_EXIT_PARTIAL_PCT: float = 0.7
    SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER: float = 42.0
    SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER: float = 50.0
    SATOSHIT_HTF_MFI_D_MIN_TRADIER: float = 30.0
    SATOSHIT_HTF_RVOL_1H_MIN_TRADIER: float = 0.3
    SATOSHIT_LONG_BB_PCTB_MAX: float = 0.5
    SATOSHIT_LONG_HA_STREAK_MAX: int = 1
    SATOSHIT_LONG_MFI_MAX_TRADIER: float = 60.0
    SATOSHIT_LONG_RSI_MAX_TRADIER: float = 50.0
    SATOSHIT_LONG_STOCH_K_MAX_TRADIER: float = 60.0
    SATOSHIT_MIN_VOTES_TRADIER: int = 3
    SATOSHIT_SHORT_BB_PCTB_MIN: float = 0.55
    SATOSHIT_SHORT_HA_STREAK_MIN: int = 0
    SATOSHIT_SHORT_MFI_MIN_TRADIER: float = 50.0
    SATOSHIT_SHORT_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_SHORT_STOCH_K_MIN_TRADIER: float = 50.0
    SMA200_DIST_LONG_THRESHOLD: float = -3.0
    SQUEEZE_ENABLED: bool = False
    STOCH_CROSS_3M_EXIT_ENABLED: bool = False
    STOCH_CROSS_ENTRY_TRADIER: bool = False
    TF_ALIGNMENT_MIN_LONG: int = 2
    TF_ALIGNMENT_MIN_SHORT: int = 2
    TF_ALIGNMENT_MIN_TOTAL: int = 4
    TF_FOCUS_ENTRY_HARD_GATE: bool = False  # 2026-04-16: off until sweep-proven
    TF_FOCUS_EXIT_HARD_GATE: bool = False  # 2026-04-16: off until sweep-proven
    TF_FOCUS_WEIGHT: float = 8.0
    TF_HTF1: str = "1h"
    TF_HTF3: str = "D"
    TF_MACRO: str = "D"
    TRADIER_DC_DAYTRADE_ENABLED: bool = True
    TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES: int = 240
    TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True
    TRADIER_DC_DAYTRADE_STOP_PCT: float = 0.005
    TRADIER_DC_DAYTRADE_TARGET_PCT: float = 0.005
    TRADIER_DC_POSITION_ENTRY_THRESHOLD: float = 0.15
    TRADIER_ENTRY_SCORE_THRESHOLD: int = 24
    TRADIER_FH_MOMENTUM_DC_CONFIRM: bool = True
    TRADIER_FH_MOMENTUM_DC_MAX_LONG: float = 0.33
    TRADIER_FH_MOMENTUM_ENABLED: bool = True
    TRADIER_FH_MOMENTUM_MFI_CONFIRM: bool = True
    TRADIER_FH_MOMENTUM_MFI_MIN: float = 55.0
    TRADIER_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5
    TRADIER_FH_MOMENTUM_WINDOW_MINUTES: int = 60
    TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER: int = 35
    TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65
    TRADIER_MFI_ENTRY_LONG_ENABLED: bool = True
    TRADIER_MFI_ENTRY_LONG_TRADIER: float = 60.0
    TRADIER_MI_ENTRY_ENABLED_TRADIER: bool = False
    TRADIER_MI_EXIT_ENABLED_TRADIER: bool = True
    TRADIER_MI_SUBSIGNAL_MIN_COUNT: int = 3
    TRADIER_RSI2_ENABLED: bool = False  # 2026-04-16: off until backtested tradier-only
    TRADIER_RSI2_EXIT_THRESHOLD_LONG: float = 90.0
    TRADIER_RSI2_EXIT_THRESHOLD_SHORT: float = 10.0
    TRADIER_RSI_ENTRY_LONG_TRADIER: float = -1.0
    TRADIER_RSI_ENTRY_SHORT_TRADIER: float = 70.0
    TRADIER_RSI_SHORT_REL_VOLUME_MIN: float = 1.2
    TRADIER_STOCH_ENTRY_LONG_TRADIER: int = 30
    TRADIER_STOCH_ENTRY_SHORT_TRADIER: int = 70
    TRADIER_STOCH_EXTREME_LONG_TRADIER: int = 15
    TRADIER_STOCH_EXTREME_SHORT_TRADIER: int = 85
    TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = True
    TRB_NOLOSS_MIN_PROFIT_PCT: float = 0.0
    UNIVERSAL_NOLOSS_GATE: bool = True
    VOLUME_CONFIRMATION_ENABLED: bool = False
    VOLUME_CONFIRMATION_MULT: float = 1.2
    VWAP_BOUNCE_DIST_PCT: float = 0.3
    VWAP_BOUNCE_ENTRY_ENABLED: bool = False  # 2026-04-16: off until sweep-proven
    WIN_TRAIL_EROSION_PCT: float = 0.5
    ABLATION_DISABLE_HEDGE: bool = False
    ABLATION_DISABLE_QUICK_ENTRY: bool = False
    ABLATION_DISABLE_QUICK_EXIT: bool = False
    BASIS_CONDITION: bool = False
    BACKTEST_VALIDATED_GATES_TRADIER: bool = True
    BB_SQUEEZE_ENABLED: bool = False  # 2026-04-16: off until sweep-proven
    BB_SQUEEZE_COOLDOWN: float = 300.0
    BB_SQUEEZE_MIN_ALIGNMENT: int = 10
    BB_SQUEEZE_WIDTH_PERCENTILE: float = 0.2
    BB_BREAKOUT_ENABLED: bool = False
    BB_BREAKOUT_TF: str = '1h'
    BB_BREAKOUT_SCORE: int = 20
    BB_RSI_STOCH_SCALP_ENABLED: bool = False
    BB_RSI_STOCH_SCALP_SCORE: int = 25
    BEAR_MARKET_MODE_TRADIER: bool = True
    AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER: bool = True
    BOUNCE_AUGMENT_ENABLED: bool = True
    BOUNCE_AUGMENT_K_D_CROSSING_UP: bool = True
    BOUNCE_AUGMENT_K_D_THRESHOLD: float = 20.0
    BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE: float = 0.02
    BOUNCE_AUGMENT_MIN_LOSS_PCT: float = -0.5
    BB_RECOVERY_EXIT_ENABLED_TRADIER: bool = False
    BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER: float = 0.0
    BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER: float = 0.3

    @classmethod
    def from_override_file(cls, path: str) -> "QuickConfig":
        cfg = cls()
        if path and Path(path).exists():
            with open(path) as f:
                overrides = json.load(f)
            for k, v in overrides.items():
                if hasattr(cfg, k):
                    cur = getattr(cfg, k)
                    if isinstance(cur, bool):
                        setattr(cfg, k, bool(v))
                    elif isinstance(cur, int):
                        setattr(cfg, k, int(v))
                    elif isinstance(cur, float):
                        setattr(cfg, k, float(v))
                    else:
                        setattr(cfg, k, v)
        return cfg

    def apply_tradier_defaults(self):
        self.MODE = "tradier"
        self.LTF = "5m"   # stocks: 5m LTF (crypto default 3m). NEVER swap — stock NPZ only has _5m fields.
        self.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"  # stocks: bb_1h (crypto: dc_4h) — NEVER swap
        self.K_ZONE_ENTRY_ENABLED = True
        self.MFI_ENTRY_ENABLED = True
        self.VWAP_FILTER_ENABLED = True
        self.FH_MOMENTUM_ENABLED = True
        self.DC_DAYTRADE_ENABLED = True
        self.STOCH_CROSS_1H_EXIT_ENABLED = True
        self.MFI_FLIP_EXIT_ENABLED = True
        self.WT_CROSSUNDER_FINAL_ENABLED = True
        # 2026-04-16: wire 3 real tradier alpha sources into reentry block dict
        self.REENTRY_B_KZONE_ENABLED = True      # live Sharpe 4.23 / 20,605 trades
        self.REENTRY_B_FH_MOM_ENABLED = True     # live Sharpe 1.54 validated
        self.REENTRY_B_MFI_D_OVERSOLD_ENABLED = True  # MFI_D<20 oversold entry
        # TF-alignment — stocks require 2+ (per CLAUDE.md: crypto=1, stock=2)
        self.HTF_MIN_ALIGNED = 2
        # WT cross alignment — stocks need 3 (per CLAUDE.md)
        self.WT_EXIT_MIN_TFS = 3
        self.MI_EXIT_ENABLED = True
        # 2026-04-17: ENTRY_SCORE_THRESHOLD=24 was mathematically unreachable after score-gate wiring
        # (max achievable score ~21 from B15+B04+B11+B02+B_KZONE+B_FH_MOM+B_MFI_D_OVERSOLD weights).
        # Set to 0 (disabled). Sweeps can raise it; 24 blocks ALL entries.
        self.ENTRY_SCORE_THRESHOLD = 0.0
        self.K3M_FLOOR = 30.0
        # Entry zone gates — stocks use explicit zones (crypto relies on reentry blocks)
        # Default off (LONG=0, SHORT=100) until swept — see live config_tradier ENTRY_ZONE_LONG=35, ENTRY_ZONE_SHORT=65
        self.ENTRY_ZONE_LONG = 0.0
        self.ENTRY_ZONE_SHORT = 100.0
        self.ENTRY_ZONE_K_TF = "15m"  # stocks: 15m entry-zone TF (crypto: 3m). Swap = disaster.
        # Reentry gap — stocks 15 bars (~15min on 1m), crypto 3 bars (~3min on 1m). Default off.
        self.REENTRY_MIN_GAP_BARS = 0
        # SYMGATE — default off; flip True to test symmetric entry/exit gate
        self.ENTRY_SYMGATE_ENABLED = False
        self.REENTRY_SYMGATE_ENABLED = False
        # D4: default tier STOCK for tradier mode when enabled
        self.BREAKOUT_MULTI_LUNG_TIER = "STOCK"
        # 2026-04-18: STOCKS BASELINE = S_H + S_E winner.
        # S_E with RANK_CONVICTION_MIN=3 selects high-quality entries (avg Sharpe 2.47 on 15/114 syms).
        # B15/B11 NPZ bug only affected CRYPTO blocks — tradier WT-based conviction is unaffected.
        self.CT_WT_VELOCITY_1H_MIN = 2.0    # stocks tuned (crypto 6.0 too tight)
        self.REENTRY_RALLY_K15M_MAX = 100.0 # disabled — stocks use ENTRY_ZONE instead
        # S_H (HTF entry zones — the breakthrough for stocks):
        self.ENTRY_ZONE_K_TF = "1h"         # 1h K-zone filters better than 15m for stocks
        self.ENTRY_ZONE_LONG = 30.0         # LONG requires k_1h < 30 (deeply oversold)
        self.ENTRY_ZONE_SHORT = 70.0        # SHORT requires k_1h > 70
        self.HTF_MIN_ALIGNED = 2
        # S_E (conviction scoring — HTF-proxied in backtest):
        self.RANK_CONVICTION_ENABLED = True
        self.RANK_CONVICTION_MIN = 3        # all 3 of 1h/4h/D agree
        self.DC_MOMENT_ENABLED = True
        self.DC_MOMENT_OPPOSE_THRESHOLD = 40.0
        self.WINNER_PROTECT_ENABLED = True
        self.WINNER_PROTECT_GAIN_PCT = 1.5
        # 2026-04-19 FIX: Tradier has fewer trading symbols per run (15-20 of 114 trade with S_E).
        # 2.5 floor caused early_abort at exactly 15 symbols when avg was 2.4755. Use 1.5 so full
        # 114-sym set evaluates and we get the real baseline Sharpe. Sweeps set their own floors.
        self.EARLY_ABORT_SHARPE_FLOOR = 1.5
        # 2026-04-19 FIX: PROFIT_TARGET_ENABLED=True (crypto default 1.6%) bleeds into tradier.
        # Tradier uses "exit at slowdown" (pure technical exits). PT creates fake Sharpe (low variance
        # from fixed TP) while masking real exit quality. Disable for honest tradier baselines.
        # Sweeps can test PT explicitly via PROFIT_TARGET_ENABLED: [True, False] in the grid.
        self.PROFIT_TARGET_ENABLED = False
        # 2026-04-19 FIX: MIN_HOLD_BARS defaults to 250 (crypto 12.5h). For tradier "exit at
        # slowdown" model, that equals 62.5h hold on 15m base — blocks ALL exits → WR=39%.
        # Tradier has no hedge engine and exits whenever momentum slows (in gain). Reset to 4
        # bars (60min on 15m base) so exits fire promptly. Sweepable.
        self.MIN_HOLD_BARS = 4
        # Tradier has no simulated hedge — disable so counter-positions don't contaminate P&L.
        self.HEDGE_ENABLED = False
        # 2026-04-20 FIX: DC_RECOVERY_EXIT rescues NOLOSS-stuck positions. Disabled by default (crypto
        # uses hedge instead). For tradier, this is the ONLY escape valve — without it, positions stranded
        # below entry hold forever and block all reentries. Use bb_1h range (stocks: bb_1h, not dc_4h).
        self.DC_RECOVERY_EXIT_ENABLED = True
        self.DC_RECOVERY_EXIT_TF = "bb_1h"


def _resolve_npz_dir(npz_dir=""):
    if npz_dir:
        d = Path(npz_dir)
    else:
        for prefix in ["backtest_v8", "backtest_v7"]:
            d = BASE_PATH / prefix / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                break
    return d


def iter_npz(mode, symbols, start_date, npz_dir=""):
    """Yield (sym, data_dict) one symbol at a time. Caller must discard
    the data_dict after each iteration to release memory. Required for
    large symbol sets that would OOM if preloaded."""
    d = _resolve_npz_dir(npz_dir)
    if not d.exists():
        print(f"NPZ dir not found: {d}")
        return
    CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    from datetime import datetime, timezone
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) if start_date else 0
    count = 0
    for npz_path in sorted(d.glob("*.npz")):
        sym = npz_path.stem
        if symbols and sym not in symbols:
            continue
        if not symbols:
            is_crypto = any(sym.endswith(s) for s in CRYPTO_SUFFIXES)
            if mode == "crypto" and not is_crypto: continue
            if mode == "tradier" and is_crypto: continue
        try:
            data = dict(np.load(str(npz_path), allow_pickle=True))
        except Exception as e:
            print(f"[WARN] {sym}: {e}")
            continue
        ts = data.get('timestamps', data.get('timestamp_3m', np.array([])))
        if len(ts) == 0:
            continue
        if start_ts and ts[-1] < start_ts:
            continue
        start_idx = np.searchsorted(ts, start_ts) if start_ts else 0
        trimmed = {k: v[start_idx:] if isinstance(v, np.ndarray) and len(v) > start_idx else v for k, v in data.items()}
        count += 1
        yield sym, trimmed
    print(f"Streamed {count} symbols from {d}")


def load_npz(mode, symbols, start_date, npz_dir=""):
    """Backward-compat wrapper: collects iter_npz into a dict. Only use
    for small symbol sets (<15). Large sets must use iter_npz directly
    or the streaming simulate_streaming path."""
    return dict(iter_npz(mode, symbols, start_date, npz_dir))


def _close_with_mode_check(npz, n, cfg, call_site: str) -> np.ndarray:
    """B-7: mode-aware close loader. Falls back to close_5m for stock NPZ (tradier LTF=5m)
    but warns when mode/LTF mismatch so a tradier NPZ running with LTF=3m is not silent."""
    _ltf = getattr(cfg, 'LTF', '3m')
    _mode = getattr(cfg, 'MODE', 'crypto')
    close = _safe(npz, f'close_{_ltf}', n)
    if close.sum() == 0:
        _expected = 'close_5m' if _mode == 'tradier' else 'close_3m'
        if f'close_{_ltf}' != _expected and f"{call_site}:{_ltf}:{_mode}" not in _V8_MISSING_WARNED:
            _V8_MISSING_WARNED.add(f"{call_site}:{_ltf}:{_mode}")
            print(f"[V8_ENGINE] CLOSE_FALLBACK mode={_mode} LTF={_ltf} -> close_5m at {call_site}. Check LTF config matches NPZ.", file=sys.stderr)
        close = _safe(npz, 'close_5m', n)
    return close


def compute_reentry_blocks(npz, n, is_long, cfg):
    """Returns dict of block_name -> boolean array (True = block fires)."""
    _ltf = getattr(cfg, 'LTF', '3m')
    close = _close_with_mode_check(npz, n, cfg, 'compute_reentry_blocks')
    k_ltf = _safe(npz, f'stoch_k_{_ltf}', n, 50); d_ltf = _safe(npz, f'stoch_d_{_ltf}', n, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50); k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    k_ltf_prev = np.roll(k_ltf, 1); k_ltf_prev[0] = k_ltf[0]
    wt1_ltf = _safe(npz, f'wt1_{_ltf}', n); wt2_ltf = _safe(npz, f'wt2_{_ltf}', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_ltf = _safe(npz, f'wt_velocity_{_ltf}', n); wt_vel_15m = _safe(npz, 'wt_velocity_15m', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    wt_bull_ltf = _safeb(npz, f'wt_bullish_{_ltf}', n); wt_bull_15m = _safeb(npz, 'wt_bullish_15m', n)
    wt_bull_1h = _safeb(npz, 'wt_bullish_1h', n); wt_bull_4h = _safeb(npz, 'wt_bullish_4h', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n); dc_low_4h = _safe(npz, 'dc_low_4h', n)
    dc_high_1h = _safe(npz, 'dc_high_1h', n); dc_low_1h = _safe(npz, 'dc_low_1h', n)
    dc_high_15m = _safe(npz, 'dc_high_15m', n); dc_low_15m = _safe(npz, 'dc_low_15m', n)
    ha_ltf = _ha_int(npz, f'ha_{_ltf}', n); ha_15m = _ha_int(npz, 'ha_15m', n); ha_1h = _ha_int(npz, 'ha_1h', n)

    blocks = {}
    if cfg.REENTRY_B02_BC156_BOTTOM_ENABLED:
        if is_long:
            wt_bull_cnt = wt_bull_ltf.astype(int) + wt_bull_15m.astype(int) + wt_bull_1h.astype(int) + wt_bull_4h.astype(int)
            blocks["B02"] = (wt1_15m < -20) & (wt_vel_15m > 0) & (wt1_1h > wt2_1h) & (wt_bull_cnt >= 2)
        else:
            wt_bear_cnt = (~wt_bull_ltf).astype(int) + (~wt_bull_15m).astype(int) + (~wt_bull_1h).astype(int) + (~wt_bull_4h).astype(int)
            blocks["B02"] = (wt1_15m > 20) & (wt_vel_15m < 0) & (wt1_1h < wt2_1h) & (wt_bear_cnt >= 2)
    if cfg.REENTRY_B04_DC_RETEST_ENABLED:
        dc_high_4h_prev = np.roll(dc_high_4h, 5); dc_high_4h_prev[:5] = dc_high_4h[:5]
        dc_low_4h_prev = np.roll(dc_low_4h, 5); dc_low_4h_prev[:5] = dc_low_4h[:5]
        if is_long:
            exp = (dc_high_4h > dc_high_4h_prev * 1.015) & (dc_high_4h_prev > 0)
            pb = (close < dc_high_4h_prev * 1.005) & (close > dc_high_4h_prev * 0.99)
            blocks["B04"] = exp & pb & (k_ltf > d_ltf) & (k_ltf < 50)
        else:
            exp = (dc_low_4h < dc_low_4h_prev * 0.985) & (dc_low_4h_prev > 0)
            pb = (close > dc_low_4h_prev * 0.995) & (close < dc_low_4h_prev * 1.01)
            blocks["B04"] = exp & pb & (k_ltf < d_ltf) & (k_ltf > 50)
    if cfg.REENTRY_B10_STOCH_REV_ENABLED:
        if is_long:
            blocks["B10"] = (k_ltf_prev <= d_ltf) & (k_ltf > d_ltf) & (k_ltf < 25) & (k_15m < 40)
        else:
            blocks["B10"] = (k_ltf_prev >= d_ltf) & (k_ltf < d_ltf) & (k_ltf > 75) & (k_15m > 60)
    if cfg.REENTRY_B11_DC_BREAK_ENABLED:
        # B11 uses PREVIOUS 1h bar's DC band — dc_high_1h in NPZ includes current bar's high so
        # close > dc_high_1h is mathematically impossible. Shift by 20 3m bars (1 1h bar).
        _dc1h_prev = np.roll(dc_high_1h, 20); _dc1h_prev[:20] = 0
        _dc1l_prev = np.roll(dc_low_1h, 20); _dc1l_prev[:20] = 0
        if is_long:
            blocks["B11"] = (_dc1h_prev > 0) & (close > _dc1h_prev * 1.001) & (wt1_15m > wt2_15m)
        else:
            blocks["B11"] = (_dc1l_prev > 0) & (close < _dc1l_prev * 0.999) & (wt1_15m < wt2_15m)
    if cfg.REENTRY_B12_WT_MOM_ENABLED:
        if is_long:
            aligned = (wt1_ltf > wt2_ltf) & (wt1_15m > wt2_15m) & (wt1_1h > wt2_1h)
            blocks["B12"] = aligned & (wt_vel_ltf > 1.0)
        else:
            aligned = (wt1_ltf < wt2_ltf) & (wt1_15m < wt2_15m) & (wt1_1h < wt2_1h)
            blocks["B12"] = aligned & (wt_vel_ltf < -1.0)
    if cfg.REENTRY_B14_HA_TREND_ENABLED:
        if is_long:
            blocks["B14"] = (ha_ltf == 1) & (ha_15m == 1) & (ha_1h == 1) & (k_ltf < 60)
        else:
            blocks["B14"] = (ha_ltf == -1) & (ha_15m == -1) & (ha_1h == -1) & (k_ltf > 40)
    if cfg.REENTRY_B15_STRONG_TREND_ENABLED:
        # B15 uses PREVIOUS 4h bar's DC band — dc_high_4h in NPZ includes current bar so
        # close > dc_high_4h is mathematically impossible. Shift by 80 3m bars (1 4h bar).
        _dc4h_prev = np.roll(dc_high_4h, 80); _dc4h_prev[:80] = 0
        _dc4l_prev = np.roll(dc_low_4h, 80); _dc4l_prev[:80] = 0
        if is_long:
            blocks["B15"] = (_dc4h_prev > 0) & (close > _dc4h_prev) & (wt_vel_1h > 2.0) & (k_1h < 85)
        else:
            blocks["B15"] = (_dc4l_prev > 0) & (close < _dc4l_prev) & (wt_vel_1h < -2.0) & (k_1h > 15)
    # === 2026-04-17 REENTRY OVERHAUL BLOCKS (C + D) ===
    # B_WT15M_CROSS (C): 15m WT crossover in position direction + HTF favorable (1h or 4h) + k_15m gate
    # B_PRICE_CROSS_K90 (D): proxy — k_15m in favorable zone. "Price crosses exit" requires per-trade state
    # handled in simulate() loop, so the vectorized block emits candidates and simulate() gates on exit-price cross.
    if getattr(cfg, 'REENTRY_WT15M_CROSS_ENABLED', True):
        wt1_15m_prev = np.roll(wt1_15m, 1); wt1_15m_prev[0] = wt1_15m[0]
        wt2_15m_prev = np.roll(wt2_15m, 1); wt2_15m_prev[0] = wt2_15m[0]
        wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
        _k_max = float(getattr(cfg, 'REENTRY_WT15M_K_MAX', 50.0))
        _htf_req = bool(getattr(cfg, 'REENTRY_WT15M_HTF_FAVOR_REQUIRED', True))
        if is_long:
            _just_crossed = (wt1_15m > wt2_15m) & (wt1_15m_prev <= wt2_15m_prev)
            _htf_fav = (wt1_1h > wt2_1h) | (wt1_4h > wt2_4h)
            _gate = _just_crossed & (_htf_fav | (not _htf_req)) & (k_15m < _k_max)
            blocks["B_WT15M_CROSS"] = _gate
        else:
            _just_crossed = (wt1_15m < wt2_15m) & (wt1_15m_prev >= wt2_15m_prev)
            _htf_fav = (wt1_1h < wt2_1h) | (wt1_4h < wt2_4h)
            _gate = _just_crossed & (_htf_fav | (not _htf_req)) & (k_15m > (100.0 - _k_max))
            blocks["B_WT15M_CROSS"] = _gate
    if getattr(cfg, 'REENTRY_K15M_PARTIAL_ENABLED', True):
        # D is a sizing-adjusted variant — the signal is "any bar with favorable k_15m".
        # The "price crosses exit" gate and 100%-vs-partial multiplier happen in simulate()
        # using the bar's prev-exit price. Block emits the candidate window.
        _k_thr = float(getattr(cfg, 'REENTRY_K15M_PARTIAL_THRESHOLD', 90.0))
        if is_long:
            blocks["B_PRICE_CROSS_K90"] = (k_15m < _k_thr) & (wt1_15m > wt2_15m)
        else:
            blocks["B_PRICE_CROSS_K90"] = (k_15m > (100.0 - _k_thr)) & (wt1_15m < wt2_15m)

    # ═══════════════════════════════════════════════════════════════
    # PULLBACK-IN-TREND BLOCKS — enter EARLY, not at end of move
    # Core idea: fundamentally rising (HTF up) ticker + temp pullback → enter at temp bottom
    # Mirror for shorts: fundamentally falling + temp rally → enter at temp top
    # ═══════════════════════════════════════════════════════════════
    # Additional indicators for pullback detection
    bb_pctb_1h_arr = _safe(npz, 'bb_pct_b_1h', n, 0.5)
    wt_vel_4h_arr = _safe(npz, 'wt_velocity_4h', n)
    wt_vel_D_arr = _safe(npz, 'wt_velocity_D', n)
    rsi_1h_arr = _safe(npz, 'rsi_1h', n, 50)
    ha_D_arr = _ha_int(npz, 'ha_D', n)
    ha_4h_arr = _ha_int(npz, 'ha_4h', n)
    sma200_1h = _safe(npz, 'sma_200_1h', n)
    k_ltf_prev2 = np.roll(k_ltf, 2); k_ltf_prev2[:2] = k_ltf[:2]
    # Higher TF WT not declared in this scope — pull from npz
    wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_D = _safe(npz, 'wt1_D', n); wt2_D = _safe(npz, 'wt2_D', n)

    # TIGHTENED PULLBACK BLOCKS (2026-04-16) — strict fundamental trend + deep pullback + reversal confirm
    # Principle: only enter when BOTH (1) long-term trend is CLEARLY in our direction
    # AND (2) temporary pullback gives us a better price. Skip neutral/sideways.

    # B_PULL1: ALL HTFs aligned + LTF DEEP oversold + stoch+vel both turning up
    if getattr(cfg, 'REENTRY_PULL1_ENABLED', True):
        if is_long:
            # STRICT: D green, 1h+4h trend up, 1h NOT overbought
            htf_uptrend = (wt1_1h > wt2_1h) & (wt1_4h > wt2_4h) & (wt1_D > wt2_D) & (ha_D_arr == 1) & (k_1h < 70)
            deep_pullback = (k_ltf < 20) & (wt1_ltf < -35)  # TIGHTER: deep oversold
            reversing = (k_ltf > k_ltf_prev) & (wt_vel_ltf > 0) & (k_ltf_prev < k_ltf_prev2)  # actively turning
            blocks["B_PULL1"] = htf_uptrend & deep_pullback & reversing
        else:
            htf_downtrend = (wt1_1h < wt2_1h) & (wt1_4h < wt2_4h) & (wt1_D < wt2_D) & (ha_D_arr == -1) & (k_1h > 30)
            deep_rally = (k_ltf > 80) & (wt1_ltf > 35)
            reversing = (k_ltf < k_ltf_prev) & (wt_vel_ltf < 0) & (k_ltf_prev > k_ltf_prev2)
            blocks["B_PULL1"] = htf_downtrend & deep_rally & reversing

    # B_PULL2: D rising strongly (wt_vel_D > 1) + price PULLED BACK to SMA200_1h
    if getattr(cfg, 'REENTRY_PULL2_ENABLED', True):
        if is_long:
            rising_fundamentals = (wt_vel_D_arr > 1.0) & (wt_vel_4h_arr > 0) & (ha_D_arr == 1) & (ha_4h_arr >= 0)
            pullback_near_sma = (sma200_1h > 0) & (close < sma200_1h * 1.01) & (close > sma200_1h * 0.98)
            momentum_returning = (k_ltf > d_ltf) & (k_ltf < 35) & (wt_vel_ltf > 0)
            blocks["B_PULL2"] = rising_fundamentals & pullback_near_sma & momentum_returning
        else:
            falling_fundamentals = (wt_vel_D_arr < -1.0) & (wt_vel_4h_arr < 0) & (ha_D_arr == -1) & (ha_4h_arr <= 0)
            rally_near_sma = (sma200_1h > 0) & (close > sma200_1h * 0.99) & (close < sma200_1h * 1.02)
            momentum_weakening = (k_ltf < d_ltf) & (k_ltf > 65) & (wt_vel_ltf < 0)
            blocks["B_PULL2"] = falling_fundamentals & rally_near_sma & momentum_weakening

    # B_PULL3: BB EXTREME lower band (pctb < 0.15) in strict uptrend + stoch cross
    if getattr(cfg, 'REENTRY_PULL3_ENABLED', True):
        if is_long:
            trend_up = (wt1_1h > wt2_1h) & (wt1_4h > wt2_4h) & (ha_D_arr == 1)
            at_extreme_band = bb_pctb_1h_arr < 0.15
            stoch_turning_up = (k_ltf_prev <= d_ltf) & (k_ltf > d_ltf) & (k_ltf < 30)
            blocks["B_PULL3"] = trend_up & at_extreme_band & stoch_turning_up
        else:
            trend_down = (wt1_1h < wt2_1h) & (wt1_4h < wt2_4h) & (ha_D_arr == -1)
            at_extreme_band = bb_pctb_1h_arr > 0.85
            stoch_turning_down = (k_ltf_prev >= d_ltf) & (k_ltf < d_ltf) & (k_ltf > 70)
            blocks["B_PULL3"] = trend_down & at_extreme_band & stoch_turning_down

    # B_PULL4: RSI extreme pullback in trend + LTF WT turning from zero line
    if getattr(cfg, 'REENTRY_PULL4_ENABLED', True):
        if is_long:
            pullback_rsi = rsi_1h_arr < 35  # TIGHTER: deeper RSI pullback
            htf_healthy = (ha_4h_arr == 1) & (ha_D_arr == 1)  # STRICT both TF green
            wt_bouncing_ltf = (wt_vel_ltf > 0) & (wt1_ltf < -15) & (wt1_ltf > wt1_15m * 0.7)  # bouncing from below
            blocks["B_PULL4"] = pullback_rsi & htf_healthy & wt_bouncing_ltf
        else:
            rally_rsi = rsi_1h_arr > 65
            htf_bearish = (ha_4h_arr == -1) & (ha_D_arr == -1)
            wt_rolling_ltf = (wt_vel_ltf < 0) & (wt1_ltf > 15) & (wt1_ltf < wt1_15m * 1.3)
            blocks["B_PULL4"] = rally_rsi & htf_bearish & wt_rolling_ltf

    # ═══════════════════════════════════════════════════════════════
    # TRADIER ALPHA BLOCKS (2026-04-16) — ported from tradier_manage.py
    # K_ZONE: live Sharpe 4.23 / 20,605 trades (live config value 35 / 65)
    # FH_MOMENTUM: live Sharpe 1.54 validated (2026-04-07)
    # MFI_D_OVERSOLD: MFI_LONG_THRESHOLD_D=20 from live config
    # ═══════════════════════════════════════════════════════════════
    k_4h_arr = _safe(npz, 'stoch_k_4h', n, 50)
    d_4h_arr = _safe(npz, 'stoch_d_4h', n, 50)

    # B_KZONE: K_4h in zone AND turning (K crosses over D for longs)
    if getattr(cfg, 'REENTRY_B_KZONE_ENABLED', False):
        kz_long_th = float(getattr(cfg, 'K_ZONE_LONG_THRESHOLD_TRADIER',
                              getattr(cfg, 'K_ZONE_LONG_THRESHOLD', 35)))
        kz_short_th = float(getattr(cfg, 'K_ZONE_SHORT_THRESHOLD_TRADIER',
                              getattr(cfg, 'K_ZONE_SHORT_THRESHOLD', 65)))
        if is_long:
            blocks["B_KZONE"] = (k_4h_arr < kz_long_th) & (k_4h_arr > d_4h_arr)
        else:
            blocks["B_KZONE"] = (k_4h_arr > kz_short_th) & (k_4h_arr < d_4h_arr)

    # B_FH_MOM: first 60 min after US market open (13:30 UTC) + minimum move
    if getattr(cfg, 'REENTRY_B_FH_MOM_ENABLED', False):
        ts = npz.get('timestamps')
        if ts is None or len(ts) == 0:
            blocks["B_FH_MOM"] = np.zeros(n, dtype=bool)
        else:
            ts = np.asarray(ts, dtype=np.int64)[:n]
            if len(ts) < n:
                pad = np.full(n - len(ts), ts[-1] if len(ts) else 0, dtype=np.int64)
                ts = np.concatenate([ts, pad])
            sec_of_day = ts % 86400
            win = int(getattr(cfg, 'FH_MOMENTUM_WINDOW_SEC', 3600))
            # Market open = 13:30 UTC = 48600s. First-hour window: [48600, 48600+win]
            fh_window = (sec_of_day >= 48600) & (sec_of_day < 48600 + win)
            close_1h = _safe(npz, 'close_1h', n)
            open_1h = _safe(npz, 'open_1h', n)
            mv_pct = np.zeros(n, dtype=np.float32)
            safe_o = np.where(open_1h > 0, open_1h, 1.0)
            mv_pct = (close_1h - open_1h) / safe_o * 100.0
            min_move = float(getattr(cfg, 'FH_MOMENTUM_MIN_MOVE_PCT', 0.5))
            if is_long:
                blocks["B_FH_MOM"] = fh_window & (mv_pct >= min_move)
            else:
                blocks["B_FH_MOM"] = fh_window & (mv_pct <= -min_move)

    # B_MFI_D_OVERSOLD: MFI_D < 20 for long / > 80 for short (mean-reversion)
    if getattr(cfg, 'REENTRY_B_MFI_D_OVERSOLD_ENABLED', False):
        mfi_D_arr = _safe(npz, 'mfi_D', n, 50)
        long_th = float(getattr(cfg, 'MFI_LONG_THRESHOLD_D', 20))
        short_th = float(getattr(cfg, 'MFI_SHORT_THRESHOLD_D', 80))
        if is_long:
            blocks["B_MFI_D_OVERSOLD"] = mfi_D_arr < long_th
        else:
            blocks["B_MFI_D_OVERSOLD"] = mfi_D_arr > short_th

    return blocks


def compute_entry_signals(npz, n, is_long, cfg):
    _ltf = getattr(cfg, 'LTF', '3m')
    close = _close_with_mode_check(npz, n, cfg, 'compute_entry_signals')
    k_ltf = _safe(npz, f'stoch_k_{_ltf}', n, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    d_ltf = _safe(npz, f'stoch_d_{_ltf}', n, 50)
    wt1_ltf = _safe(npz, f'wt1_{_ltf}', n); wt2_ltf = _safe(npz, f'wt2_{_ltf}', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_D = _safe(npz, 'wt1_D', n); wt2_D = _safe(npz, 'wt2_D', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50)
    mfi_D = _safe(npz, 'mfi_D', n, 50)
    ha_D = _ha_int(npz, 'ha_D', n); ha_1h = _ha_int(npz, 'ha_1h', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n); dc_low_4h = _safe(npz, 'dc_low_4h', n)

    # Gate filters — K3M_FLOOR name retained for config compat; gate applies to LTF stoch K.
    kltf_ok = (k_ltf < (100 - cfg.K3M_FLOOR)) if is_long else (k_ltf > cfg.K3M_FLOOR)
    ct_vel_ok = np.ones(n, dtype=bool)
    if cfg.CT_WT_VELOCITY_GATE_ENABLED:
        m = cfg.CT_WT_VELOCITY_1H_MIN
        ct_vel_ok = (wt_vel_1h >= m) if is_long else (wt_vel_1h <= -m)
    ct_dc_ok = np.ones(n, dtype=bool)
    if cfg.CT_DC_CROSSOVER_SKIP_ENABLED and not is_long:
        dc_co_15m = _safeb(npz, 'dc_basis_crossover_15m', n)
        dc_co_1h = _safeb(npz, 'dc_basis_crossover_1h', n)
        ct_dc_ok = ~(dc_co_15m | dc_co_1h)
    htf_ok = np.ones(n, dtype=bool)
    if cfg.HTF_ALIGNMENT_ENABLED:
        if is_long:
            htf_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
        else:
            htf_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        htf_ok = htf_cnt >= cfg.HTF_MIN_ALIGNED
        if cfg.D_TREND_REQUIRED:
            d_aligned = (ha_D == 1) if is_long else (ha_D == -1)
            d_neutral = (ha_D == 0)
            htf_ok = htf_ok & (d_aligned | d_neutral)

    # Get all enabled reentry blocks
    blocks = compute_reentry_blocks(npz, n, is_long, cfg)
    if not blocks:
        return np.zeros(n, dtype=bool)

    # Tradier extras
    mfi_gate = np.ones(n, dtype=bool)
    if cfg.MFI_ENTRY_ENABLED:
        if is_long: mfi_gate = mfi_1h < cfg.MFI_ENTRY_LONG_MAX
        else: mfi_gate = mfi_1h > cfg.MFI_ENTRY_SHORT_MIN
    vwap_ok = np.ones(n, dtype=bool)
    if cfg.VWAP_FILTER_ENABLED:
        vwap = _safe(npz, 'vwap_D', n)
        if vwap.sum() > 0:
            vwap_ok = (close > vwap) if is_long else (close < vwap)

    # CONFLUENCE MODE: require N blocks to agree simultaneously
    if cfg.CONFLUENCE_MODE_ENABLED:
        stacked = np.stack(list(blocks.values()), axis=0)
        agree_count = stacked.sum(axis=0)
        raw = agree_count >= cfg.CONFLUENCE_MIN_BLOCKS
    else:
        # OR mode: any block fires
        raw = np.zeros(n, dtype=bool)
        for b in blocks.values():
            raw = raw | b

    # STRENGTH FILTER: REVERTED to V8Q v3 proven weights (Sharpe 1.93 on TOP3).
    # Pullback-first weighting (tested above) only hit Sharpe 0.80 — reverted 2026-04-16.
    # PULL blocks remain as opt-in sweep knobs with LOW weight (don't pollute proven score).
    if cfg.STRENGTH_FILTER_ENABLED:
        weights = {
            # ORIGINAL V8Q v3 weights — proven Sharpe 1.93 on TOP3
            "B15": 4,       # Strong trend continuation (DC breakout above prev 4h band)
            "B04": 3,       # DC retest (Sharpe 0.39)
            "B11": 3,       # DC break above prev 1h band (Sharpe 0.34, 94% WR)
            "B02": 2,       # BC156 bottom bounce (Sharpe 0.31)
            "B10": 1, "B12": 1, "B14": 1,
            # 2026-04-19 FIX: B_PRICE_CROSS_K90 and B_WT15M_CROSS are primary live reentry
            # triggers but had weight=1 (default). With B15/B11 dead (0 bars due to NPZ DC bug),
            # max achievable score was 5 on near-zero bars → backtest traded 1000x less than live.
            # Weight=4 matches B15 (same importance: price above DC structure = mandatory reentry).
            "B_PRICE_CROSS_K90": 4,  # live mandatory: price crossed exit level proxy
            "B_WT15M_CROSS": 4,      # live primary reentry: 15m WT cross aligned with HTF
            # Pullback blocks (2026-04-16 experiment — tested, kept at low weight)
            "B_PULL1": 1, "B_PULL2": 1, "B_PULL3": 1, "B_PULL4": 1,
            # Tradier alpha blocks (2026-04-16) — weights match historical Sharpe
            "B_KZONE": 4,       # live Sharpe 4.23
            "B_FH_MOM": 3,      # live Sharpe 1.54
            "B_MFI_D_OVERSOLD": 2,
        }
        score = np.zeros(n, dtype=np.float32)
        for name, arr in blocks.items():
            score = score + arr.astype(np.float32) * weights.get(name, 1)
        raw = raw & (score >= cfg.STRENGTH_MIN_SCORE)
        # ENTRY_SCORE_THRESHOLD — second score floor swept independently.
        # DELTA_ENTRY_ENABLED proxy: live delta_tracker fires on velocity zone transitions,
        # bypassing the scorer. Proxy: velocity-strong bars skip ENTRY_SCORE_THRESHOLD (not STRENGTH_MIN_SCORE).
        _est = float(getattr(cfg, 'ENTRY_SCORE_THRESHOLD', 0.0) or 0.0)
        if _est > 0:
            _delta_en = bool(getattr(cfg, 'DELTA_ENTRY_ENABLED', True))
            if _delta_en and cfg.CT_WT_VELOCITY_GATE_ENABLED:
                _vel_min = float(getattr(cfg, 'CT_WT_VELOCITY_1H_MIN', 0.0))
                _vel_bypass = (wt_vel_1h >= _vel_min) if is_long else (wt_vel_1h <= -_vel_min)
                raw = raw & ((score >= _est) | _vel_bypass)
            else:
                raw = raw & (score >= _est)

    # ===== Auto-hooked entry gates (Group B switches) =====
    # Each adds a simple filter; when flipped, impacts entry signal density.
    extra_ok = np.ones(n, dtype=bool)
    # Tradier MFI entry long gate — tradier mode only (crypto regression 2026-04-16)
    if getattr(cfg, 'TRADIER_MFI_ENTRY_LONG_ENABLED', False) and is_long and getattr(cfg, 'MODE', 'crypto') == 'tradier':
        mfi_1h_arr = _safe(npz, 'mfi_1h', n, 50)
        extra_ok = extra_ok & (mfi_1h_arr < getattr(cfg, 'TRADIER_MFI_ENTRY_LONG_TRADIER', 60.0))
    # RSI entry gate
    if getattr(cfg, 'RSI_ENTRY_GATE_ENABLED', False):
        rsi_1h = _safe(npz, 'rsi_1h', n, 50)
        if is_long:
            extra_ok = extra_ok & (rsi_1h < getattr(cfg, 'RSI_ENTRY_MAX_LONG', 37.0))
        else:
            extra_ok = extra_ok & (rsi_1h > getattr(cfg, 'RSI_ENTRY_MIN_SHORT', 63.0))
    # Stoch cross entry tradier — LTF-parameterized
    if getattr(cfg, 'STOCH_CROSS_ENTRY_TRADIER', False):
        k_ltf_arr = _safe(npz, f'stoch_k_{_ltf}', n, 50)
        d_ltf_arr = _safe(npz, f'stoch_d_{_ltf}', n, 50)
        k_ltf_arr_prev = np.roll(k_ltf_arr, 1); k_ltf_arr_prev[0] = k_ltf_arr[0]
        if is_long:
            extra_ok = extra_ok & ((k_ltf_arr_prev <= d_ltf_arr) & (k_ltf_arr > d_ltf_arr))
        else:
            extra_ok = extra_ok & ((k_ltf_arr_prev >= d_ltf_arr) & (k_ltf_arr < d_ltf_arr))
    # TF alignment min total (tradier-only; regression if applied to crypto per 2026-04-16 test)
    if getattr(cfg, 'BACKTEST_VALIDATED_GATES_TRADIER', False) and getattr(cfg, 'MODE', 'crypto') == 'tradier':
        wt1_4h_arr = _safe(npz, 'wt1_4h', n); wt2_4h_arr = _safe(npz, 'wt2_4h', n)
        wt1_D_arr = _safe(npz, 'wt1_D', n); wt2_D_arr = _safe(npz, 'wt2_D', n)
        if is_long:
            tf_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h_arr > wt2_4h_arr).astype(int) + (wt1_D_arr > wt2_D_arr).astype(int)
        else:
            tf_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h_arr < wt2_4h_arr).astype(int) + (wt1_D_arr < wt2_D_arr).astype(int)
        tf_gate_total = getattr(cfg, 'TF_ALIGNMENT_MIN_TOTAL', 0)
        tf_need = max(1, min(3, int(tf_gate_total // 4))) if tf_gate_total > 0 else 1
        extra_ok = extra_ok & (tf_cnt >= tf_need)
    # Volume confirmation
    if getattr(cfg, 'VOLUME_CONFIRMATION_ENABLED', False):
        vol_1h = _safe(npz, 'volume_1h', n, 0)
        vol_ma = _safe(npz, 'volume_sma_1h', n, 0)
        if vol_ma.sum() > 0:
            extra_ok = extra_ok & (vol_1h > vol_ma * getattr(cfg, 'VOLUME_CONFIRMATION_MULT', 1.2))
    # Ablation: disable quick entry path
    if getattr(cfg, 'ABLATION_DISABLE_QUICK_ENTRY', False):
        return np.zeros(n, dtype=bool)
    # Ablation: disable hedge/reentry second path (B_PULL*, B09 snapback)
    if getattr(cfg, 'ABLATION_DISABLE_REENTRY', False):
        # Block reentry-like blocks, keep raw trend-follow only — for sensitivity test
        pass
    # Tradier entry score MFI_D filter — ONLY tradier mode
    entry_score_min = getattr(cfg, 'TRADIER_ENTRY_SCORE_THRESHOLD', 0)
    if entry_score_min >= 24 and getattr(cfg, 'MODE', 'crypto') == 'tradier':
        mfi_D_arr = _safe(npz, 'mfi_D', n, 50)
        if is_long: extra_ok = extra_ok & (mfi_D_arr >= 40)
        else: extra_ok = extra_ok & (mfi_D_arr <= 60)
    # ENTRY_ZONE gate — LONG requires k_TF < ENTRY_ZONE_LONG ceiling (oversold); SHORT requires > ENTRY_ZONE_SHORT floor (overbought).
    # Disabled defaults (LONG=0 / SHORT=100) trivially pass. When set (e.g. LONG=35, SHORT=65) they become real gates.
    _zone_tf = str(getattr(cfg, 'ENTRY_ZONE_K_TF', _ltf) or _ltf)
    if _zone_tf == '15m':
        _zone_k_arr = k_15m
    elif _zone_tf == '1h':
        _zone_k_arr = k_1h
    else:
        _zone_k_arr = k_ltf
    if is_long:
        _zl = float(getattr(cfg, 'ENTRY_ZONE_LONG', 0.0) or 0.0)
        if _zl > 0.0:
            extra_ok = extra_ok & (_zone_k_arr < _zl)
    else:
        _zs = float(getattr(cfg, 'ENTRY_ZONE_SHORT', 100.0) or 100.0)
        if _zs < 100.0:
            extra_ok = extra_ok & (_zone_k_arr > _zs)
    # REENTRY_RALLY_K15M_MAX — cap on k_15m for entries during rally. 100=disabled.
    _rally_cap = float(getattr(cfg, 'REENTRY_RALLY_K15M_MAX', 100.0) or 100.0)
    if _rally_cap < 100.0:
        if is_long:
            extra_ok = extra_ok & (k_15m < _rally_cap)
        else:
            extra_ok = extra_ok & (k_15m > (100.0 - _rally_cap))
    # REENTRY_RALLY_HTF_MIN — require N of 3 HTF channels (1h/4h/D) aligned. 1=disabled (any 1 of 3 ok).
    _re_htf_min = int(getattr(cfg, 'REENTRY_RALLY_HTF_MIN', 1))
    if _re_htf_min > 1:
        if is_long:
            _re_htf_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
        else:
            _re_htf_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        extra_ok = extra_ok & (_re_htf_cnt >= _re_htf_min)
    # ═══ Chapter-E RANK_CONVICTION (vectorized proxy) ═══
    # Live uses 0ranking_points_global (cross-symbol). NPZ has no cross-symbol data, so proxy
    # rank_proxy via HTF agreement count (0-3). Side-agreement ≥ RANK_CONVICTION_MIN to allow.
    if bool(getattr(cfg, 'RANK_CONVICTION_ENABLED', False)):
        _rc_min = int(getattr(cfg, 'RANK_CONVICTION_MIN', 2))
        if is_long:
            _rc_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
        else:
            _rc_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        extra_ok = extra_ok & (_rc_cnt >= _rc_min)
    # ═══ Chapter-E DC_MOMENT (vectorized proxy) ═══
    # Live uses 0dc_moment (precomputed composite). Proxy via sum of dc_position on 1h/4h/D.
    # Blocks entry when dc_position opposes side by margin.
    if bool(getattr(cfg, 'DC_MOMENT_ENABLED', False)):
        _dcp_1h = _safe(npz, 'dc_position_1h', n, 0.5)
        _dcp_4h = _safe(npz, 'dc_position_4h', n, 0.5)
        _dcp_D = _safe(npz, 'dc_position_D', n, 0.5)
        _dcm_proxy = (_dcp_1h + _dcp_4h + _dcp_D) / 3.0 * 100.0  # 0-100
        _dcm_thr = float(getattr(cfg, 'DC_MOMENT_OPPOSE_THRESHOLD', 40.0))
        if is_long:
            extra_ok = extra_ok & (_dcm_proxy >= (50.0 - _dcm_thr))
        else:
            extra_ok = extra_ok & (_dcm_proxy <= (50.0 + _dcm_thr))
    # R-G2: MTF WT velocity alignment gate (2026-04-18 indicator audit)
    mtf_vel_ok = np.ones(n, dtype=bool)
    if bool(getattr(cfg, 'WT_MTF_VEL_GATE_ENABLED', False)):
        _vel_min_tfs = int(getattr(cfg, 'WT_MTF_VEL_MIN', 3))
        if is_long:
            _vel_cnt = _safe(npz, 'wt_velocity_up_count', n, 0).astype(np.int8)
            mtf_vel_ok = _vel_cnt >= _vel_min_tfs
        else:
            _vel_cnt = _safe(npz, 'wt_velocity_down_count', n, 0).astype(np.int8)
            mtf_vel_ok = _vel_cnt >= _vel_min_tfs
    # RE-1: cross freshness gate (2026-04-18 indicator audit)
    cross_fresh_ok = np.ones(n, dtype=bool)
    if bool(getattr(cfg, 'REENTRY_CROSS_FRESHNESS_ENABLED', False)):
        _max_bars = int(getattr(cfg, 'REENTRY_CROSS_MAX_BARS_AGO', 5))
        _b3 = _safe(npz, 'wt_cross_bars_ago_3m', n, 999.0)
        _b15 = _safe(npz, 'wt_cross_bars_ago_15m', n, 999.0)
        _b1h = _safe(npz, 'wt_cross_bars_ago_1h', n, 999.0)
        cross_fresh_ok = (_b3 < _max_bars) | (_b15 < _max_bars) | (_b1h < _max_bars)
    base_sig = raw & kltf_ok & ct_vel_ok & ct_dc_ok & htf_ok & mfi_gate & vwap_ok & extra_ok & mtf_vel_ok & cross_fresh_ok
    # D4: BREAKOUT MULTI-LUNG entry augmentation (default OFF)
    if getattr(cfg, 'BREAKOUT_MULTI_LUNG_ENABLED', False):
        try:
            from breakout_multi_lung import multi_lung_entry_signal
            ml_sig = multi_lung_entry_signal(npz, n, is_long, cfg)
            mode = str(getattr(cfg, 'BREAKOUT_MULTI_LUNG_MODE', 'AUGMENT')).upper()
            if mode == 'REPLACE':
                return ml_sig
            return base_sig | ml_sig
        except Exception:
            pass  # graceful fallback if NPZ missing OHLCV fields
    return base_sig


def compute_exit_signals(npz, n, is_long, cfg):
    _ltf = getattr(cfg, 'LTF', '3m')
    close = _close_with_mode_check(npz, n, cfg, 'compute_exit_signals')
    wt1_ltf = _safe(npz, f'wt1_{_ltf}', n); wt2_ltf = _safe(npz, f'wt2_{_ltf}', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_4h = _safe(npz, 'wt_velocity_4h', n)
    wt_vel_ltf = _safe(npz, f'wt_velocity_{_ltf}', n)
    k_ltf = _safe(npz, f'stoch_k_{_ltf}', n, 50); d_ltf = _safe(npz, f'stoch_d_{_ltf}', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50); d_1h = _safe(npz, 'stoch_d_1h', n, 50)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50); mfi_ltf = _safe(npz, f'mfi_{_ltf}', n, 50)
    bb_pctb_1h = _safe(npz, 'bb_pct_b_1h', n, 0.5)

    if is_long:
        wt_against = (wt1_ltf < wt2_ltf).astype(int) + (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int)
    else:
        wt_against = (wt1_ltf > wt2_ltf).astype(int) + (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int)
    delta_exit = wt_against >= cfg.WT_EXIT_MIN_TFS
    vel_exit = (wt_vel_4h < -2.0) if is_long else (wt_vel_4h > 2.0)

    # WT-VELOCITY-DECAY exit (user priority: "sell when wt delta slows down")
    # Exit when 1h velocity magnitude drops below threshold after being strong
    wt_vel_1h_exit = _safe(npz, 'wt_velocity_1h', n)
    wt_vel_1h_prev = np.roll(wt_vel_1h_exit, 1); wt_vel_1h_prev[0] = wt_vel_1h_exit[0]
    vel_decay_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_VEL_DECAY_EXIT_ENABLED', True):
        decay_threshold = float(getattr(cfg, 'WT_VEL_DECAY_THRESHOLD', 1.0))
        if is_long:
            # Was strong positive momentum, now decayed below threshold AND LTF vel also dropping
            was_strong = wt_vel_1h_prev > decay_threshold * 2
            now_decayed = wt_vel_1h_exit < decay_threshold
            vel_decay_exit = was_strong & now_decayed & (wt_vel_ltf < wt_vel_1h_prev * 0.5)
        else:
            was_strong = wt_vel_1h_prev < -decay_threshold * 2
            now_decayed = wt_vel_1h_exit > -decay_threshold
            vel_decay_exit = was_strong & now_decayed & (wt_vel_ltf > wt_vel_1h_prev * 0.5)
    srs_exit = np.zeros(n, dtype=bool)
    if cfg.STRUCTURAL_RANGE_SHIFT_EXIT:
        tf_map = {'dc_1h': ('dc_high_1h', 'dc_low_1h'), 'dc_4h': ('dc_high_4h', 'dc_low_4h'),
                  'bb_1h': ('bb_upper_1h', 'bb_lower_1h'), 'bb_4h': ('bb_upper_4h', 'bb_lower_4h')}
        hk, lk = tf_map.get(cfg.STRUCTURAL_RANGE_SHIFT_TF, ('dc_high_4h', 'dc_low_4h'))
        hi = _safe(npz, hk, n); lo = _safe(npz, lk, n)
        k_1h_prev = np.roll(k_1h, 1); k_1h_prev[0] = k_1h[0]
        if is_long:
            prox = (hi > 0) & (np.abs(close - hi) / np.maximum(hi, 1e-9) <= 0.01)
            srs_exit = prox & (k_1h >= 75) & (k_1h < k_1h_prev)
        else:
            prox = (lo > 0) & (np.abs(close - lo) / np.maximum(lo, 1e-9) <= 0.01)
            srs_exit = prox & (k_1h <= 25) & (k_1h > k_1h_prev)
    sat_exit = np.zeros(n, dtype=bool)
    if cfg.SATOSHIT_ENABLED:
        k_ltf_prev = np.roll(k_ltf, 1); k_ltf_prev[0] = k_ltf[0]
        mfi_ltf_prev = np.roll(mfi_ltf, 1); mfi_ltf_prev[0] = mfi_ltf[0]
        if is_long:
            sat_exit = (k_ltf_prev >= 80) & (k_ltf_prev >= d_ltf) & (k_ltf < d_ltf) & (mfi_ltf < mfi_ltf_prev)
        else:
            sat_exit = (k_ltf_prev <= 20) & (k_ltf_prev <= d_ltf) & (k_ltf > d_ltf) & (mfi_ltf > mfi_ltf_prev)
    rz_exit = np.zeros(n, dtype=bool)
    if cfg.RZ_EXIT_ENABLED:
        if is_long:
            rz_exit = ((bb_pctb_1h > 0.85) | (k_1h >= 80)) & (wt_vel_ltf < -1.0)
        else:
            rz_exit = ((bb_pctb_1h < 0.15) | (k_1h <= 20)) & (wt_vel_ltf > 1.0)
    stoch_1h_exit = np.zeros(n, dtype=bool)
    if cfg.STOCH_CROSS_1H_EXIT_ENABLED:
        k_1h_prev = np.roll(k_1h, 1); k_1h_prev[0] = k_1h[0]
        if is_long:
            stoch_1h_exit = (k_1h_prev >= d_1h) & (k_1h < d_1h) & (k_1h_prev >= 70)
        else:
            stoch_1h_exit = (k_1h_prev <= d_1h) & (k_1h > d_1h) & (k_1h_prev <= 30)
    mfi_flip_exit = np.zeros(n, dtype=bool)
    if cfg.MFI_FLIP_EXIT_ENABLED:
        if is_long: mfi_flip_exit = mfi_1h > cfg.MFI_FLIP_EXIT_LONG_THRESHOLD
        else: mfi_flip_exit = mfi_1h < cfg.MFI_FLIP_EXIT_SHORT_THRESHOLD
    wt_cu_exit = np.zeros(n, dtype=bool)
    if cfg.WT_CROSSUNDER_FINAL_ENABLED:
        wt1_ltf_prev = np.roll(wt1_ltf, 1); wt1_ltf_prev[0] = wt1_ltf[0]
        if is_long:
            wt_cu_exit = (wt1_ltf_prev >= wt2_ltf) & (wt1_ltf < wt2_ltf) & (k_ltf >= 70)
        else:
            wt_cu_exit = (wt1_ltf_prev <= wt2_ltf) & (wt1_ltf > wt2_ltf) & (k_ltf <= 30)
    mi_exit = np.zeros(n, dtype=bool)
    if cfg.MI_EXIT_ENABLED:
        mfi_1h_prev = np.roll(mfi_1h, 1); mfi_1h_prev[0] = mfi_1h[0]
        if is_long:
            mi_exit = (mfi_1h_prev > 70) & (mfi_1h < mfi_1h_prev) & (k_1h > 70)
        else:
            mi_exit = (mfi_1h_prev < 30) & (mfi_1h > mfi_1h_prev) & (k_1h < 30)

    # ===== Auto-hooked exit gates (Group B switches) =====
    extra_exit = np.zeros(n, dtype=bool)
    # RSI exit long/short tradier
    rsi_1h_arr = _safe(npz, 'rsi_1h', n, 50)
    if getattr(cfg, 'RSI_EXIT_LONG_TRADIER', 0) > 0 and is_long:
        extra_exit = extra_exit | (rsi_1h_arr >= getattr(cfg, 'RSI_EXIT_LONG_TRADIER', 85.0))
    if getattr(cfg, 'RSI_EXIT_SHORT_TRADIER', 100) < 100 and not is_long:
        extra_exit = extra_exit | (rsi_1h_arr <= getattr(cfg, 'RSI_EXIT_SHORT_TRADIER', 15.0))
    # RSI2 exit tradier (Connors-style) — LTF-parameterized
    if getattr(cfg, 'TRADIER_RSI2_ENABLED', False):
        rsi2_ltf = _safe(npz, f'rsi2_{_ltf}', n, 50)
        if rsi2_ltf.sum() > 0:
            if is_long:
                extra_exit = extra_exit | (rsi2_ltf >= getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_LONG', 90.0))
            else:
                extra_exit = extra_exit | (rsi2_ltf <= getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_SHORT', 10.0))
    # Satoshit exit (simplified — RSI+Stoch votes) — LTF-parameterized
    if getattr(cfg, 'SATOSHIT_EXIT_ENABLED', False):
        k_ltf_arr2 = _safe(npz, f'stoch_k_{_ltf}', n, 50)
        if is_long:
            rsi_hit = rsi_1h_arr >= getattr(cfg, 'SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER', 55.0)
            stoch_hit = k_ltf_arr2 >= getattr(cfg, 'SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER', 60.0)
        else:
            rsi_hit = rsi_1h_arr <= getattr(cfg, 'SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER', 42.0)
            stoch_hit = k_ltf_arr2 <= getattr(cfg, 'SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER', 50.0)
        votes_needed = getattr(cfg, 'SATOSHIT_MIN_VOTES_TRADIER', 3)
        # 2 visible + 1 latent (bb_pctb would be 3rd) — simpler: require 2 of 2 if votes >= 3
        if votes_needed >= 3:
            extra_exit = extra_exit | (rsi_hit & stoch_hit)
        else:
            extra_exit = extra_exit | (rsi_hit | stoch_hit)
    # Stoch cross LTF exit — LTF-parameterized (config key name retained)
    if getattr(cfg, 'STOCH_CROSS_3M_EXIT_ENABLED', False):
        k_ltf_arr2 = _safe(npz, f'stoch_k_{_ltf}', n, 50)
        d_ltf_arr2 = _safe(npz, f'stoch_d_{_ltf}', n, 50)
        k_ltf_arr2_prev = np.roll(k_ltf_arr2, 1); k_ltf_arr2_prev[0] = k_ltf_arr2[0]
        if is_long:
            extra_exit = extra_exit | ((k_ltf_arr2_prev >= d_ltf_arr2) & (k_ltf_arr2 < d_ltf_arr2) & (k_ltf_arr2 > 60))
        else:
            extra_exit = extra_exit | ((k_ltf_arr2_prev <= d_ltf_arr2) & (k_ltf_arr2 > d_ltf_arr2) & (k_ltf_arr2 < 40))
    # Ablation: disable quick exit path
    if getattr(cfg, 'ABLATION_DISABLE_QUICK_EXIT', False):
        return np.zeros(n, dtype=bool)
    # Cycle TP early cap
    if getattr(cfg, 'CYCLE_TP_TIERED_ENABLED', False):
        # Engine has PROFIT_TARGET_PCT; CYCLE_TP_PCT acts as upper cap
        pass  # handled in simulate() via PROFIT_TARGET_PCT
    # ═══ WT/0DC AUDIT EXIT SIGNALS (ablation sweep — all OFF by default) ═══
    # wt_momentum_state encoding: 2=bull_trend, 1=bull_weak, -1=bear_weak, -2=bear_trend
    # wt_peak_structure: 1=HH, -1=LH | wt_trough_structure: 1=HL, -1=LL
    # wt_divergence: -1=BEAR, 1=BULL | wt_wave_phase: 1=expanding, -1=contracting
    wt_mom_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_MOMENTUM_EXIT_ENABLED', False):
        _tfs_m = str(getattr(cfg, 'WT_MOMENTUM_EXIT_TF', '1h'))
        _mom = _safe(npz, f'wt_momentum_state_{_tfs_m}', n, 0).astype(np.int8)
        _mom_thr = int(getattr(cfg, 'WT_MOMENTUM_EXIT_THRESHOLD', 0))
        wt_mom_exit = (_mom <= _mom_thr) if is_long else (_mom >= -_mom_thr)
    wt_struct_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_STRUCT_EXIT_ENABLED', False):
        _tfs_s = str(getattr(cfg, 'WT_STRUCT_EXIT_TF', '1h'))
        _pk = _safe(npz, f'wt_peak_structure_{_tfs_s}', n, 0).astype(np.int8)
        _tr = _safe(npz, f'wt_trough_structure_{_tfs_s}', n, 0).astype(np.int8)
        wt_struct_exit = (_pk == -1) if is_long else (_tr == -1)
    wt_div_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_DIV_EXIT_ENABLED', False):
        _tfs_d = str(getattr(cfg, 'WT_DIV_EXIT_TF', '1h'))
        _div = _safe(npz, f'wt_divergence_{_tfs_d}', n, 0).astype(np.int8)
        wt_div_exit = (_div == -1) if is_long else (_div == 1)
    wt_pct_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_PERCENTILE_EXIT_ENABLED', False):
        _tfs_p = str(getattr(cfg, 'WT_PERCENTILE_EXIT_TF', '1h'))
        _pct = _safe(npz, f'wt_percentile_{_tfs_p}', n, 50.0)
        _pct_thr = float(getattr(cfg, 'WT_PERCENTILE_EXIT_THRESHOLD', 80.0))
        wt_pct_exit = (_pct >= _pct_thr) if is_long else (_pct <= (100.0 - _pct_thr))
    wt_zscore_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_ZSCORE_EXIT_ENABLED', False):
        _tfs_z = str(getattr(cfg, 'WT_ZSCORE_EXIT_TF', '1h'))
        _zs = _safe(npz, f'wt_zscore_{_tfs_z}', n, 0.0)
        _z_thr = float(getattr(cfg, 'WT_ZSCORE_EXIT_THRESHOLD', 2.0))
        wt_zscore_exit = (_zs >= _z_thr) if is_long else (_zs <= -_z_thr)
    wt_accel_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_ACCEL_EXIT_ENABLED', False):
        _tfs_a = str(getattr(cfg, 'WT_ACCEL_EXIT_TF', '1h'))
        _acc = _safe(npz, f'wt_acceleration_{_tfs_a}', n, 0.0)
        wt_accel_exit = (_acc < 0) if is_long else (_acc > 0)
    wt_wave_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_WAVE_PHASE_EXIT_ENABLED', False):
        _tfs_w = str(getattr(cfg, 'WT_WAVE_PHASE_EXIT_TF', '1h'))
        _wp = _safe(npz, f'wt_wave_phase_{_tfs_w}', n, 0).astype(np.int8)
        wt_wave_exit = _wp == -1
    wt_score_flip_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_SCORE_FLIP_EXIT_ENABLED', False):
        _tfs_sf = str(getattr(cfg, 'WT_SCORE_FLIP_EXIT_TF', '3m'))
        _sc = _safe(npz, f'wt_score_{_tfs_sf}', n, 0.0)
        wt_score_flip_exit = (_sc < 0) if is_long else (_sc > 0)
    wt_vel_mtf_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_VEL_MTF_EXIT_ENABLED', False):
        _v3 = _safe(npz, 'wt_velocity_3m', n, 0.0); _v15 = _safe(npz, 'wt_velocity_15m', n, 0.0)
        _v1h = _safe(npz, 'wt_velocity_1h', n, 0.0); _v4h = _safe(npz, 'wt_velocity_4h', n, 0.0)
        _v_thr = float(getattr(cfg, 'WT_VEL_MTF_EXIT_THRESHOLD', -1.0))
        _v_min_tfs = int(getattr(cfg, 'WT_VEL_MTF_EXIT_MIN_TFS', 3))
        if is_long:
            _vcnt = (_v3 < _v_thr).astype(int) + (_v15 < _v_thr).astype(int) + (_v1h < _v_thr).astype(int) + (_v4h < _v_thr / 2).astype(int)
        else:
            _vcnt = (_v3 > -_v_thr).astype(int) + (_v15 > -_v_thr).astype(int) + (_v1h > -_v_thr).astype(int) + (_v4h > _v_thr / 2).astype(int)
        wt_vel_mtf_exit = _vcnt >= _v_min_tfs
    wt_align_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_ALIGN_EXIT_ENABLED', False):
        _al_thr = int(getattr(cfg, 'WT_ALIGN_EXIT_MIN', 2))
        if is_long:
            _al = _safe(npz, 'wt_bull_alignment', n, 3).astype(np.int8)
            wt_align_exit = _al < _al_thr
        else:
            _al = _safe(npz, 'wt_bear_alignment', n, 3).astype(np.int8)
            wt_align_exit = _al < _al_thr
    wt_comp_delta_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_COMP_DELTA_EXIT_ENABLED', False):
        _cd = _safe(npz, 'wt_composite_delta', n, 0.0)
        _cd_thr = float(getattr(cfg, 'WT_COMP_DELTA_EXIT_THRESHOLD', 0.0))
        wt_comp_delta_exit = (_cd < _cd_thr) if is_long else (_cd > -_cd_thr)
    dc_pos_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'DC_POS_EXIT_ENABLED', False):
        _dcp_1h = _safe(npz, 'dc_position_1h', n, 0.5); _dcp_4h = _safe(npz, 'dc_position_4h', n, 0.5); _dcp_D = _safe(npz, 'dc_position_D', n, 0.5)
        _dc_avg = (_dcp_1h + _dcp_4h + _dcp_D) / 3.0
        _dc_thr = float(getattr(cfg, 'DC_POS_EXIT_THRESHOLD', 0.7))
        dc_pos_exit = (_dc_avg >= _dc_thr) if is_long else (_dc_avg <= (1.0 - _dc_thr))
    base_exit = delta_exit | vel_exit | srs_exit | sat_exit | rz_exit | stoch_1h_exit | mfi_flip_exit | wt_cu_exit | mi_exit | vel_decay_exit | extra_exit | wt_mom_exit | wt_struct_exit | wt_div_exit | wt_pct_exit | wt_zscore_exit | wt_accel_exit | wt_wave_exit | wt_score_flip_exit | wt_vel_mtf_exit | wt_align_exit | wt_comp_delta_exit | dc_pos_exit
    # D4: BREAKOUT MULTI-LUNG exit augmentation (default OFF)
    if getattr(cfg, 'BREAKOUT_MULTI_LUNG_ENABLED', False):
        try:
            from breakout_multi_lung import multi_lung_exit_signal
            ml_exit = multi_lung_exit_signal(npz, n, is_long, cfg)
            mode = str(getattr(cfg, 'BREAKOUT_MULTI_LUNG_MODE', 'AUGMENT')).upper()
            if mode == 'REPLACE':
                return ml_exit
            return base_exit | ml_exit
        except Exception:
            pass
    return base_exit


def simulate(stores, cfg, capital=10000.0):
    """stores can be a dict {sym: data} (preloaded) or an iterable of
    (sym, data) tuples (streaming). Streaming releases memory per symbol."""
    all_pnl = []
    per_symbol_pnl = {}
    start_size = cfg.START_POSITION_SIZE
    cooldown = cfg.COOLDOWN_BARS
    min_hold = cfg.MIN_HOLD_BARS
    ea_enabled = bool(getattr(cfg, 'EARLY_ABORT_ENABLED', True))
    ea_min_syms = int(getattr(cfg, 'EARLY_ABORT_MIN_SYMBOLS', 15))
    ea_floor = float(getattr(cfg, 'EARLY_ABORT_SHARPE_FLOOR', 1.0))
    symbols_processed = 0
    early_abort = False
    _ltf = getattr(cfg, 'LTF', '3m')
    _iter = stores.items() if isinstance(stores, dict) else stores
    for sym, npz in _iter:
        sym_pnl = []
        # Derive n from LTF close (stocks may not have timestamps/timestamp_3m fields at all).
        ts = npz.get('timestamps', npz.get(f'timestamp_{_ltf}', npz.get('timestamp_3m', np.array([]))))
        n = len(ts)
        if n < 100:
            # Fallback: use LTF close length (stock NPZ has close_5m but no timestamp array)
            _close_ltf = npz.get(f'close_{_ltf}')
            if _close_ltf is not None and hasattr(_close_ltf, '__len__') and len(_close_ltf) >= 100:
                n = len(_close_ltf)
            else:
                continue
        close = _close_with_mode_check(npz, n, cfg, 'simulate')
        _dc_tf_map = {'dc_4h': ('dc_high_4h', 'dc_low_4h'), 'dc_1h': ('dc_high_1h', 'dc_low_1h'),
                      'bb_1h': ('bb_upper_1h', 'bb_lower_1h'), 'bb_4h': ('bb_upper_4h', 'bb_lower_4h')}
        _dc_hk, _dc_lk = _dc_tf_map.get(getattr(cfg, 'DC_RECOVERY_EXIT_TF', 'dc_4h'), ('dc_high_4h', 'dc_low_4h'))
        dc_high_4h = _safe(npz, _dc_hk, n)
        dc_low_4h = _safe(npz, _dc_lk, n)
        # Hedge engine: continuous per-bar condition. No event needed.
        # LONG main → SHORT hedge active whenever: gain<0 AND wt1_LTF<wt2_LTF AND wt1_1h<wt2_1h
        # SHORT main → LONG hedge: gain<0 AND wt1_LTF>wt2_LTF AND wt1_1h>wt2_1h
        # Hedge closes the instant condition is false, reopens the instant it's true again.
        _wt1_ltf = _safe(npz, f'wt1_{_ltf}', n); _wt2_ltf = _safe(npz, f'wt2_{_ltf}', n)
        _wt1_1h = _safe(npz, 'wt1_1h', n); _wt2_1h = _safe(npz, 'wt2_1h', n)
        for is_long in [True, False]:
            entry_sig = compute_entry_signals(npz, n, is_long, cfg)
            exit_sig = compute_exit_signals(npz, n, is_long, cfg)
            # SYMGATE: strip entries that coincide with exit signals — mirror exit rubric applied to entries.
            # ENTRY_SYMGATE_ENABLED covers fresh entries; REENTRY_SYMGATE_ENABLED mirrors it in the vectorized loop
            # (no separate reentry path here) — either flag turns it on.
            if bool(getattr(cfg, 'ENTRY_SYMGATE_ENABLED', False)) or bool(getattr(cfg, 'REENTRY_SYMGATE_ENABLED', False)):
                entry_sig = entry_sig & ~exit_sig
            # Chapter-E WINNER_PROTECT proxy — precompute per-bar "HTF-aligned" mask for use inside loop.
            wp_enabled = bool(getattr(cfg, 'WINNER_PROTECT_ENABLED', False))
            wp_gain_pct = float(getattr(cfg, 'WINNER_PROTECT_GAIN_PCT', 2.0))
            if wp_enabled:
                _wp_wt1_1h = _safe(npz, 'wt1_1h', n); _wp_wt2_1h = _safe(npz, 'wt2_1h', n)
                _wp_wt1_4h = _safe(npz, 'wt1_4h', n); _wp_wt2_4h = _safe(npz, 'wt2_4h', n)
                _wp_wt1_D = _safe(npz, 'wt1_D', n); _wp_wt2_D = _safe(npz, 'wt2_D', n)
                if is_long:
                    _wp_aligned = (_wp_wt1_1h > _wp_wt2_1h) & (_wp_wt1_4h > _wp_wt2_4h) & (_wp_wt1_D > _wp_wt2_D)
                else:
                    _wp_aligned = (_wp_wt1_1h < _wp_wt2_1h) & (_wp_wt1_4h < _wp_wt2_4h) & (_wp_wt1_D < _wp_wt2_D)
            in_pos = False; ep = 0.0; eb = 0; cd = 0
            hedge_in_pos = False; hedge_ep = 0.0
            pt_enabled = cfg.PROFIT_TARGET_ENABLED
            pt_pct = cfg.PROFIT_TARGET_PCT
            sl_enabled = cfg.STOP_LOSS_ENABLED
            sl_pct = cfg.STOP_LOSS_PCT
            # REENTRY_MIN_GAP_BARS — extra cooldown after exit before next entry. 0 = use COOLDOWN_BARS only.
            min_gap_bars = int(getattr(cfg, 'REENTRY_MIN_GAP_BARS', 0) or 0)
            for i in range(n):
                if cd > 0: cd -= 1; continue
                px = close[i]
                if px <= 0: continue
                if not in_pos and entry_sig[i]:
                    in_pos = True; ep = px; eb = i
                    continue
                if in_pos:
                    live_pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    # Continuous hedge — disabled when HEDGE_ENABLED=False (tradier has no hedge engine).
                    if getattr(cfg, 'HEDGE_ENABLED', True):
                        if is_long:
                            hc = live_pnl < 0 and _wt1_ltf[i] < _wt2_ltf[i] and _wt1_1h[i] < _wt2_1h[i]
                        else:
                            hc = live_pnl < 0 and _wt1_ltf[i] > _wt2_ltf[i] and _wt1_1h[i] > _wt2_1h[i]
                        if hc and not hedge_in_pos:
                            hedge_in_pos = True; hedge_ep = px
                        elif not hc and hedge_in_pos and hedge_ep > 0:
                            h_pnl = ((hedge_ep - px) / hedge_ep * 100) if is_long else ((px - hedge_ep) / hedge_ep * 100)
                            all_pnl.append(h_pnl); sym_pnl.append(h_pnl); hedge_in_pos = False; hedge_ep = 0.0
                    # Profit target (live_pnl>=pt_pct is mutually exclusive with hc, so hedge is already closed)
                    if pt_enabled and live_pnl >= pt_pct:
                        all_pnl.append(pt_pct); sym_pnl.append(pt_pct)
                        in_pos = False; cd = max(cooldown, min_gap_bars); continue
                    # 2026-04-20: Continuous DC recovery — close stranded losing positions immediately
                    # without waiting for exit_sig. Prevents orphaned positions from blocking reentries
                    # for months. Fires after min_hold to avoid exiting on transient BB compressions.
                    # Uses same cfg.DC_RECOVERY_EXIT_TF as the exit_sig-gated check (set per mode).
                    if cfg.DC_RECOVERY_EXIT_ENABLED and cfg.NOLOSS_ENABLED and (i - eb) >= min_hold and live_pnl < 0:
                        if is_long:
                            _dc_stranded = ep > dc_high_4h[i] and dc_high_4h[i] > 0
                        else:
                            _dc_stranded = ep < dc_low_4h[i] and dc_low_4h[i] > 0
                        if _dc_stranded:
                            all_pnl.append(live_pnl); sym_pnl.append(live_pnl)
                            in_pos = False; cd = max(cooldown, min_gap_bars); continue
                    if sl_enabled and live_pnl <= -sl_pct:
                        all_pnl.append(live_pnl); sym_pnl.append(live_pnl)
                        if hedge_in_pos and hedge_ep > 0:
                            h_pnl = ((hedge_ep - px) / hedge_ep * 100) if is_long else ((px - hedge_ep) / hedge_ep * 100)
                            all_pnl.append(h_pnl); sym_pnl.append(h_pnl); hedge_in_pos = False; hedge_ep = 0.0
                        in_pos = False; cd = max(cooldown, min_gap_bars); continue
                if in_pos and exit_sig[i] and (i - eb) >= min_hold:
                    pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    if wp_enabled and 0.0 <= pnl < wp_gain_pct and _wp_aligned[i]:
                        continue
                    if cfg.NOLOSS_ENABLED and pnl < 0:
                        if cfg.DC_RECOVERY_EXIT_ENABLED:
                            if is_long:
                                stranded = ep > dc_high_4h[i] and dc_high_4h[i] > 0
                            else:
                                stranded = ep < dc_low_4h[i] and dc_low_4h[i] > 0
                            if not stranded:
                                continue
                        else:
                            continue
                    all_pnl.append(pnl); sym_pnl.append(pnl)
                    if hedge_in_pos and hedge_ep > 0:
                        h_pnl = ((hedge_ep - px) / hedge_ep * 100) if is_long else ((px - hedge_ep) / hedge_ep * 100)
                        all_pnl.append(h_pnl); sym_pnl.append(h_pnl); hedge_in_pos = False; hedge_ep = 0.0
                    in_pos = False; cd = max(cooldown, min_gap_bars)
            if in_pos:
                final_px = close[n - 1]
                if final_px > 0 and ep > 0:
                    final_pnl = ((final_px - ep) / ep * 100) if is_long else ((ep - final_px) / ep * 100)
                    all_pnl.append(final_pnl); sym_pnl.append(final_pnl)
                if hedge_in_pos and final_px > 0 and hedge_ep > 0:
                    h_pnl = ((hedge_ep - final_px) / hedge_ep * 100) if is_long else ((final_px - hedge_ep) / hedge_ep * 100)
                    all_pnl.append(h_pnl); sym_pnl.append(h_pnl)
                in_pos = False; hedge_in_pos = False; hedge_ep = 0.0
        per_symbol_pnl[sym] = sym_pnl
        symbols_processed += 1
        if ea_enabled and symbols_processed >= ea_min_syms:
            per_sym_sharpes_chk = _per_symbol_sharpes(per_symbol_pnl)
            if per_sym_sharpes_chk:
                avg_chk = float(np.mean(per_sym_sharpes_chk))
                if avg_chk < ea_floor:
                    early_abort = True
                    break
    return _finalize_result(per_symbol_pnl, all_pnl, start_size, symbols_processed, early_abort)


def _per_symbol_sharpes(per_symbol_pnl, min_trades=30, std_floor=1e-3, cap=20.0):
    out = []
    for sym, plist in per_symbol_pnl.items():
        if len(plist) < min_trades:
            continue
        arr = np.array(plist)
        m = arr.mean(); s = max(arr.std(), std_floor)
        sh = max(min(m / s, cap), -cap)
        out.append(sh)
    return out


def _pool_sharpe(all_pnl, min_trades=30, cap=20.0):
    if len(all_pnl) < min_trades:
        return 0.0
    p = np.array(all_pnl)
    m = p.mean(); s = p.std()
    if s < 1e-6:
        return 0.0
    return round(float(max(min(m / s, cap), -cap)), 4)


def _finalize_result(per_symbol_pnl, all_pnl, start_size, symbols_processed, early_abort):
    per_sym_sharpes = _per_symbol_sharpes(per_symbol_pnl)
    n_trades = len(all_pnl)
    ps = _pool_sharpe(all_pnl)
    syms_excluded = symbols_processed - len(per_sym_sharpes)
    if not per_sym_sharpes:
        p = np.array(all_pnl) if all_pnl else np.array([0.0])
        w = int((p > 0).sum()); l = int((p <= 0).sum())
        return {"sharpe": 0, "sharpe_min": 0, "sharpe_p25": 0, "sharpe_med": 0,
                "sharpe_p75": 0, "sharpe_max": 0, "syms_with_sharpe": 0,
                "pool_sharpe": ps, "syms_excluded": syms_excluded,
                "pnl": round(p.sum() / 100 * start_size, 2), "trades": n_trades,
                "wins": w, "losses": l, "avg_pnl_pct": round(float(p.mean()), 4) if n_trades else 0,
                "wr": round(w / n_trades * 100, 1) if n_trades > 0 else 0,
                "early_abort": early_abort, "symbols_used": symbols_processed}
    arr = np.array(per_sym_sharpes)
    p = np.array(all_pnl) if all_pnl else np.array([0.0])
    w = int((p > 0).sum()); l = int((p <= 0).sum())
    return {
        "sharpe": round(float(arr.mean()), 4),
        "sharpe_min": round(float(arr.min()), 4),
        "sharpe_p25": round(float(np.percentile(arr, 25)), 4),
        "sharpe_med": round(float(np.median(arr)), 4),
        "sharpe_p75": round(float(np.percentile(arr, 75)), 4),
        "sharpe_max": round(float(arr.max()), 4),
        "syms_with_sharpe": len(per_sym_sharpes),
        "pool_sharpe": ps,
        "syms_excluded": syms_excluded,
        "pnl": round(p.sum() / 100 * start_size, 2),
        "trades": n_trades, "wins": w, "losses": l,
        "avg_pnl_pct": round(float(p.mean()), 4),
        "wr": round(w / n_trades * 100, 1) if n_trades > 0 else 0,
        "early_abort": early_abort,
        "symbols_used": symbols_processed,
    }


FAST_SYMBOLS_CRYPTO = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,UNIUSDT"
FAST_SYMBOLS_TRADIER = "AAPL,MSFT,NVDA,AMZN,JPM,XOM,ABBV,TSLA,SPY,META,BA,GLD"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--symbols", default="BTCUSDT")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--npz-dir", default="")
    args = p.parse_args()
    t0 = time.time()
    cfg = QuickConfig.from_override_file(os.environ.get("V8_OVERRIDE_FILE", ""))
    if args.mode == "tradier":
        cfg.apply_tradier_defaults()
        ov = os.environ.get("V8_OVERRIDE_FILE", "")
        if ov and Path(ov).exists():
            with open(ov) as f:
                for k, v in json.load(f).items():
                    if hasattr(cfg, k):
                        cur = getattr(cfg, k)
                        if isinstance(cur, bool): setattr(cfg, k, bool(v))
                        elif isinstance(cur, int): setattr(cfg, k, int(v))
                        elif isinstance(cur, float): setattr(cfg, k, float(v))
                        else: setattr(cfg, k, v)
    syms = None
    if args.symbols == "fast":
        syms = (FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO).split(",")
    elif args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if syms and len(syms) > 15:
        stores = iter_npz(args.mode, syms, args.start, args.npz_dir)
    else:
        stores = load_npz(args.mode, syms, args.start, args.npz_dir)
        if not stores:
            print("No data"); return
    r = simulate(stores, cfg, args.capital)
    el = time.time() - t0
    print(f"V8_QUICK_RESULT: sharpe={r['sharpe']} pool_sharpe={r.get('pool_sharpe',0)} "
          f"(syms={r.get('syms_with_sharpe',0)} excl={r.get('syms_excluded',0)} "
          f"min={r.get('sharpe_min',0)} p25={r.get('sharpe_p25',0)} "
          f"med={r.get('sharpe_med',0)} p75={r.get('sharpe_p75',0)} max={r.get('sharpe_max',0)}) "
          f"pnl={r['pnl']:.2f} trades={r['trades']} wins={r['wins']} "
          f"losses={r['losses']} wr={r['wr']}% avg_pnl={r['avg_pnl_pct']:.4f}% "
          f"early_abort={r.get('early_abort',False)} elapsed={el:.1f}s")


if __name__ == "__main__":
    main()

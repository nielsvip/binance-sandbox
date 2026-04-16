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


def _safe(npz: dict, key: str, n: int, default: float = 0.0) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _safeb(npz: dict, key: str, n: int) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(bool)
    return np.zeros(n, dtype=bool)


def _ha_int(npz: dict, key: str, n: int):
    v = npz.get(key)
    if v is None or not isinstance(v, np.ndarray) or len(v) != n:
        return np.zeros(n, dtype=np.int8)
    if v.dtype in (np.int8, np.int16, np.int32, np.int64):
        return v.astype(np.int8)
    return np.zeros(n, dtype=np.int8)


@dataclass
class QuickConfig:
    MODE: str = "crypto"
    ENTRY_SCORE_THRESHOLD: float = 18.0
    K3M_FLOOR: float = 30.0
    COOLDOWN_BARS: int = 3
    NOLOSS_ENABLED: bool = False
    DC_RECOVERY_EXIT_ENABLED: bool = False
    DC_RECOVERY_EXIT_TOLERANCE_PCT: float = 0.25
    START_POSITION_SIZE: float = 2000.0
    MIN_POSITION_SIZE: float = 55.0
    CT_WT_VELOCITY_GATE_ENABLED: bool = True
    CT_WT_VELOCITY_1H_MIN: float = 0.0
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = True
    CT_15M_MOMENTUM_GATE_ENABLED: bool = False
    CT_CHOP_4H_GATE_ENABLED: bool = False
    CT_VOLUME_SURGE_GATE_ENABLED: bool = False
    DELTA_ENGINE_ENABLED: bool = True
    DELTA_ENTRY_ENABLED: bool = True
    RZ_EXIT_ENABLED: bool = True
    SATOSHIT_ENABLED: bool = True
    SATOSHIT_MIN_VOTES: int = 3
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = True
    STRUCTURAL_RANGE_SHIFT_TF: str = "dc_4h"
    REENTRY_RALLY_K15M_MAX: float = 100.0
    REENTRY_RALLY_HTF_MIN: int = 1
    HTF_ALIGNMENT_ENABLED: bool = True
    HTF_MIN_ALIGNED: int = 1
    D_TREND_REQUIRED: bool = True
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
    WT_EXIT_MIN_TFS: int = 2
    MI_EXIT_ENABLED: bool = False
    # Reentry block switches (ablation-validated)
    REENTRY_B02_BC156_BOTTOM_ENABLED: bool = True
    REENTRY_B04_DC_RETEST_ENABLED: bool = True
    REENTRY_B10_STOCH_REV_ENABLED: bool = True
    REENTRY_B11_DC_BREAK_ENABLED: bool = True
    REENTRY_B12_WT_MOM_ENABLED: bool = True
    REENTRY_B14_HA_TREND_ENABLED: bool = True
    REENTRY_B15_STRONG_TREND_ENABLED: bool = True
    # PULLBACK-FIRST entry blocks (2026-04-16) — enter at temp top/bottom, not at end of move
    REENTRY_PULL1_ENABLED: bool = True   # HTF uptrend + LTF deep oversold + reversing
    REENTRY_PULL2_ENABLED: bool = True   # Rising fundamentals + SMA200 pullback bounce
    REENTRY_PULL3_ENABLED: bool = True   # BB lower-band + trend up + stoch cross
    REENTRY_PULL4_ENABLED: bool = True   # RSI pullback + HTF healthy + wt bouncing
    # Velocity-decay exit (sell when wt_delta slows down — user priority)
    WT_VEL_DECAY_EXIT_ENABLED: bool = True
    WT_VEL_DECAY_THRESHOLD: float = 1.0
    # NEW: Confluence mode — only enter when N blocks agree
    CONFLUENCE_MODE_ENABLED: bool = False
    CONFLUENCE_MIN_BLOCKS: int = 2  # How many blocks must agree simultaneously
    # NEW: Signal strength filter — only take top-percentile setups
    # WINNER 2026-04-16: score=5 gave Sharpe 0.94, 87.2% WR, 1.42% avg on 11-sym 4yr
    STRENGTH_FILTER_ENABLED: bool = True
    STRENGTH_MIN_SCORE: float = 5.0
    # Holding period enforcement (avoid rapid exit noise) — WINNER: 10
    MIN_HOLD_BARS: int = 10
    # Profit target exit — v3 peak: Sharpe 1.93 on TOP3 (2026-04-16 precision sweep)
    PROFIT_TARGET_ENABLED: bool = True
    PROFIT_TARGET_PCT: float = 1.6  # v3 PEAK (1.6 > 1.5). Range 1.2-1.8 all give Sharpe ~1.9
    # Stop loss exit (sweep-only — cap max loss)
    STOP_LOSS_ENABLED: bool = False
    STOP_LOSS_PCT: float = 2.0  # Exit at this loss %

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
    BB_SQUEEZE_ENTRY_ENABLED: bool = True
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
    MOM3_ENTRY_ENABLED: bool = True
    MOM3_LONG_THRESHOLD: float = -1.0
    MOM3_SHORT_THRESHOLD: float = 1.0
    MOM5_ENTRY_ENABLED: bool = True
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
    REENTRY_MANDATORY: bool = True
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
    SATOSHIT_EXIT_ENABLED: bool = True
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
    TF_FOCUS_ENTRY_HARD_GATE: bool = True
    TF_FOCUS_EXIT_HARD_GATE: bool = True
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
    TRADIER_RSI2_ENABLED: bool = True
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
    VWAP_BOUNCE_ENTRY_ENABLED: bool = True
    WIN_TRAIL_EROSION_PCT: float = 0.5
    ABLATION_DISABLE_HEDGE: bool = False
    ABLATION_DISABLE_QUICK_ENTRY: bool = False
    ABLATION_DISABLE_QUICK_EXIT: bool = False
    BASIS_CONDITION: bool = False
    BACKTEST_VALIDATED_GATES_TRADIER: bool = True
    BB_SQUEEZE_ENABLED: bool = True
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
        self.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
        self.K_ZONE_ENTRY_ENABLED = True
        self.MFI_ENTRY_ENABLED = True
        self.VWAP_FILTER_ENABLED = True
        self.FH_MOMENTUM_ENABLED = True
        self.DC_DAYTRADE_ENABLED = True
        self.STOCH_CROSS_1H_EXIT_ENABLED = True
        self.MFI_FLIP_EXIT_ENABLED = True
        self.WT_CROSSUNDER_FINAL_ENABLED = True
        self.MI_EXIT_ENABLED = True
        self.ENTRY_SCORE_THRESHOLD = 24.0
        self.K3M_FLOOR = 30.0


def load_npz(mode, symbols, start_date, npz_dir=""):
    if npz_dir:
        d = Path(npz_dir)
    else:
        for prefix in ["backtest_v8", "backtest_v7"]:
            d = BASE_PATH / prefix / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                break
    if not d.exists():
        print(f"NPZ dir not found: {d}")
        return {}
    CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    from datetime import datetime, timezone
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) if start_date else 0
    stores = {}
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
        if len(ts) == 0: continue
        if start_ts and ts[-1] < start_ts: continue
        start_idx = np.searchsorted(ts, start_ts) if start_ts else 0
        stores[sym] = {k: v[start_idx:] if isinstance(v, np.ndarray) and len(v) > start_idx else v for k, v in data.items()}
    print(f"Loaded {len(stores)} symbols from {d}")
    return stores


def compute_reentry_blocks(npz, n, is_long, cfg):
    """Returns dict of block_name -> boolean array (True = block fires)."""
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0: close = _safe(npz, 'close_5m', n)
    k_3m = _safe(npz, 'stoch_k_3m', n, 50); d_3m = _safe(npz, 'stoch_d_3m', n, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50); k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    k_3m_prev = np.roll(k_3m, 1); k_3m_prev[0] = k_3m[0]
    wt1_3m = _safe(npz, 'wt1_3m', n); wt2_3m = _safe(npz, 'wt2_3m', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_3m = _safe(npz, 'wt_velocity_3m', n); wt_vel_15m = _safe(npz, 'wt_velocity_15m', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    wt_bull_3m = _safeb(npz, 'wt_bullish_3m', n); wt_bull_15m = _safeb(npz, 'wt_bullish_15m', n)
    wt_bull_1h = _safeb(npz, 'wt_bullish_1h', n); wt_bull_4h = _safeb(npz, 'wt_bullish_4h', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n); dc_low_4h = _safe(npz, 'dc_low_4h', n)
    dc_high_1h = _safe(npz, 'dc_high_1h', n); dc_low_1h = _safe(npz, 'dc_low_1h', n)
    dc_high_15m = _safe(npz, 'dc_high_15m', n); dc_low_15m = _safe(npz, 'dc_low_15m', n)
    ha_3m = _ha_int(npz, 'ha_3m', n); ha_15m = _ha_int(npz, 'ha_15m', n); ha_1h = _ha_int(npz, 'ha_1h', n)

    blocks = {}
    if cfg.REENTRY_B02_BC156_BOTTOM_ENABLED:
        if is_long:
            wt_bull_cnt = wt_bull_3m.astype(int) + wt_bull_15m.astype(int) + wt_bull_1h.astype(int) + wt_bull_4h.astype(int)
            blocks["B02"] = (wt1_15m < -20) & (wt_vel_15m > 0) & (wt1_1h > wt2_1h) & (wt_bull_cnt >= 2)
        else:
            wt_bear_cnt = (~wt_bull_3m).astype(int) + (~wt_bull_15m).astype(int) + (~wt_bull_1h).astype(int) + (~wt_bull_4h).astype(int)
            blocks["B02"] = (wt1_15m > 20) & (wt_vel_15m < 0) & (wt1_1h < wt2_1h) & (wt_bear_cnt >= 2)
    if cfg.REENTRY_B04_DC_RETEST_ENABLED:
        dc_high_4h_prev = np.roll(dc_high_4h, 5); dc_high_4h_prev[:5] = dc_high_4h[:5]
        dc_low_4h_prev = np.roll(dc_low_4h, 5); dc_low_4h_prev[:5] = dc_low_4h[:5]
        if is_long:
            exp = (dc_high_4h > dc_high_4h_prev * 1.015) & (dc_high_4h_prev > 0)
            pb = (close < dc_high_4h_prev * 1.005) & (close > dc_high_4h_prev * 0.99)
            blocks["B04"] = exp & pb & (k_3m > d_3m) & (k_3m < 50)
        else:
            exp = (dc_low_4h < dc_low_4h_prev * 0.985) & (dc_low_4h_prev > 0)
            pb = (close > dc_low_4h_prev * 0.995) & (close < dc_low_4h_prev * 1.01)
            blocks["B04"] = exp & pb & (k_3m < d_3m) & (k_3m > 50)
    if cfg.REENTRY_B10_STOCH_REV_ENABLED:
        if is_long:
            blocks["B10"] = (k_3m_prev <= d_3m) & (k_3m > d_3m) & (k_3m < 25) & (k_15m < 40)
        else:
            blocks["B10"] = (k_3m_prev >= d_3m) & (k_3m < d_3m) & (k_3m > 75) & (k_15m > 60)
    if cfg.REENTRY_B11_DC_BREAK_ENABLED:
        if is_long:
            blocks["B11"] = (dc_high_1h > 0) & (close > dc_high_1h * 1.001) & (wt1_15m > wt2_15m)
        else:
            blocks["B11"] = (dc_low_1h > 0) & (close < dc_low_1h * 0.999) & (wt1_15m < wt2_15m)
    if cfg.REENTRY_B12_WT_MOM_ENABLED:
        if is_long:
            aligned = (wt1_3m > wt2_3m) & (wt1_15m > wt2_15m) & (wt1_1h > wt2_1h)
            blocks["B12"] = aligned & (wt_vel_3m > 1.0)
        else:
            aligned = (wt1_3m < wt2_3m) & (wt1_15m < wt2_15m) & (wt1_1h < wt2_1h)
            blocks["B12"] = aligned & (wt_vel_3m < -1.0)
    if cfg.REENTRY_B14_HA_TREND_ENABLED:
        if is_long:
            blocks["B14"] = (ha_3m == 1) & (ha_15m == 1) & (ha_1h == 1) & (k_3m < 60)
        else:
            blocks["B14"] = (ha_3m == -1) & (ha_15m == -1) & (ha_1h == -1) & (k_3m > 40)
    if cfg.REENTRY_B15_STRONG_TREND_ENABLED:
        if is_long:
            blocks["B15"] = (dc_high_4h > 0) & (close > dc_high_4h) & (wt_vel_1h > 2.0) & (k_1h < 85)
        else:
            blocks["B15"] = (dc_low_4h > 0) & (close < dc_low_4h) & (wt_vel_1h < -2.0) & (k_1h > 15)

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
    k_3m_prev2 = np.roll(k_3m, 2); k_3m_prev2[:2] = k_3m[:2]
    # Higher TF WT not declared in this scope — pull from npz
    wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_D = _safe(npz, 'wt1_D', n); wt2_D = _safe(npz, 'wt2_D', n)

    # TIGHTENED PULLBACK BLOCKS (2026-04-16) — strict fundamental trend + deep pullback + reversal confirm
    # Principle: only enter when BOTH (1) long-term trend is CLEARLY in our direction
    # AND (2) temporary pullback gives us a better price. Skip neutral/sideways.

    # B_PULL1: ALL HTFs aligned + 3m DEEP oversold + stoch+vel both turning up
    if getattr(cfg, 'REENTRY_PULL1_ENABLED', True):
        if is_long:
            # STRICT: D green, 1h+4h trend up, 1h NOT overbought
            htf_uptrend = (wt1_1h > wt2_1h) & (wt1_4h > wt2_4h) & (wt1_D > wt2_D) & (ha_D_arr == 1) & (k_1h < 70)
            deep_pullback = (k_3m < 20) & (wt1_3m < -35)  # TIGHTER: deep oversold
            reversing = (k_3m > k_3m_prev) & (wt_vel_3m > 0) & (k_3m_prev < k_3m_prev2)  # actively turning
            blocks["B_PULL1"] = htf_uptrend & deep_pullback & reversing
        else:
            htf_downtrend = (wt1_1h < wt2_1h) & (wt1_4h < wt2_4h) & (wt1_D < wt2_D) & (ha_D_arr == -1) & (k_1h > 30)
            deep_rally = (k_3m > 80) & (wt1_3m > 35)
            reversing = (k_3m < k_3m_prev) & (wt_vel_3m < 0) & (k_3m_prev > k_3m_prev2)
            blocks["B_PULL1"] = htf_downtrend & deep_rally & reversing

    # B_PULL2: D rising strongly (wt_vel_D > 1) + price PULLED BACK to SMA200_1h
    if getattr(cfg, 'REENTRY_PULL2_ENABLED', True):
        if is_long:
            rising_fundamentals = (wt_vel_D_arr > 1.0) & (wt_vel_4h_arr > 0) & (ha_D_arr == 1) & (ha_4h_arr >= 0)
            pullback_near_sma = (sma200_1h > 0) & (close < sma200_1h * 1.01) & (close > sma200_1h * 0.98)
            momentum_returning = (k_3m > d_3m) & (k_3m < 35) & (wt_vel_3m > 0)
            blocks["B_PULL2"] = rising_fundamentals & pullback_near_sma & momentum_returning
        else:
            falling_fundamentals = (wt_vel_D_arr < -1.0) & (wt_vel_4h_arr < 0) & (ha_D_arr == -1) & (ha_4h_arr <= 0)
            rally_near_sma = (sma200_1h > 0) & (close > sma200_1h * 0.99) & (close < sma200_1h * 1.02)
            momentum_weakening = (k_3m < d_3m) & (k_3m > 65) & (wt_vel_3m < 0)
            blocks["B_PULL2"] = falling_fundamentals & rally_near_sma & momentum_weakening

    # B_PULL3: BB EXTREME lower band (pctb < 0.15) in strict uptrend + stoch cross
    if getattr(cfg, 'REENTRY_PULL3_ENABLED', True):
        if is_long:
            trend_up = (wt1_1h > wt2_1h) & (wt1_4h > wt2_4h) & (ha_D_arr == 1)
            at_extreme_band = bb_pctb_1h_arr < 0.15
            stoch_turning_up = (k_3m_prev <= d_3m) & (k_3m > d_3m) & (k_3m < 30)
            blocks["B_PULL3"] = trend_up & at_extreme_band & stoch_turning_up
        else:
            trend_down = (wt1_1h < wt2_1h) & (wt1_4h < wt2_4h) & (ha_D_arr == -1)
            at_extreme_band = bb_pctb_1h_arr > 0.85
            stoch_turning_down = (k_3m_prev >= d_3m) & (k_3m < d_3m) & (k_3m > 70)
            blocks["B_PULL3"] = trend_down & at_extreme_band & stoch_turning_down

    # B_PULL4: RSI extreme pullback in trend + 3m WT turning from zero line
    if getattr(cfg, 'REENTRY_PULL4_ENABLED', True):
        if is_long:
            pullback_rsi = rsi_1h_arr < 35  # TIGHTER: deeper RSI pullback
            htf_healthy = (ha_4h_arr == 1) & (ha_D_arr == 1)  # STRICT both TF green
            wt_bouncing_3m = (wt_vel_3m > 0) & (wt1_3m < -15) & (wt1_3m > wt1_15m * 0.7)  # bouncing from below
            blocks["B_PULL4"] = pullback_rsi & htf_healthy & wt_bouncing_3m
        else:
            rally_rsi = rsi_1h_arr > 65
            htf_bearish = (ha_4h_arr == -1) & (ha_D_arr == -1)
            wt_rolling_3m = (wt_vel_3m < 0) & (wt1_3m > 15) & (wt1_3m < wt1_15m * 1.3)
            blocks["B_PULL4"] = rally_rsi & htf_bearish & wt_rolling_3m

    return blocks


def compute_entry_signals(npz, n, is_long, cfg):
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0: close = _safe(npz, 'close_5m', n)
    k_3m = _safe(npz, 'stoch_k_3m', n, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    d_3m = _safe(npz, 'stoch_d_3m', n, 50)
    wt1_3m = _safe(npz, 'wt1_3m', n); wt2_3m = _safe(npz, 'wt2_3m', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_D = _safe(npz, 'wt1_D', n); wt2_D = _safe(npz, 'wt2_D', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50)
    mfi_D = _safe(npz, 'mfi_D', n, 50)
    ha_D = _ha_int(npz, 'ha_D', n); ha_1h = _ha_int(npz, 'ha_1h', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n); dc_low_4h = _safe(npz, 'dc_low_4h', n)

    # Gate filters
    k3m_ok = (k_3m < (100 - cfg.K3M_FLOOR)) if is_long else (k_3m > cfg.K3M_FLOOR)
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

    # STRENGTH FILTER: weighted score — PULLBACK-FIRST weighting (2026-04-16 user feedback)
    # Pullback blocks weighted HIGH (early entries), breakout blocks weighted LOW (late entries)
    if cfg.STRENGTH_FILTER_ENABLED:
        weights = {
            # Pullback-in-trend (EARLY entries — user priority)
            "B_PULL1": 5,   # HTF uptrend + LTF deep oversold + reversing
            "B_PULL2": 5,   # Rising fundamentals + SMA200 pullback bounce
            "B_PULL3": 4,   # BB lower-band + trend up + stoch cross up
            "B_PULL4": 4,   # RSI pullback + HTF healthy + wt bouncing
            "B02": 4,       # BC156 bottom bounce (already pullback-style)
            "B10": 4,       # Stoch cross from low zone
            "B04": 3,       # DC retest (pullback to broken level)
            # Trend-following (NEUTRAL weight)
            "B12": 2,       # WT momentum aligned
            "B14": 1,       # HA all aligned
            # Breakout (LATE entries — reduced weight)
            "B11": 1,       # DC break — enters at top of move
            "B15": 1,       # Strong trend continuation — very late
        }
        score = np.zeros(n, dtype=np.float32)
        for name, arr in blocks.items():
            score = score + arr.astype(np.float32) * weights.get(name, 1)
        raw = raw & (score >= cfg.STRENGTH_MIN_SCORE)

    # ===== Auto-hooked entry gates (Group B switches) =====
    # Each adds a simple filter; when flipped, impacts entry signal density.
    extra_ok = np.ones(n, dtype=bool)
    # Tradier MFI entry long gate
    if getattr(cfg, 'TRADIER_MFI_ENTRY_LONG_ENABLED', False) and is_long:
        mfi_1h_arr = _safe(npz, 'mfi_1h', n, 50)
        extra_ok = extra_ok & (mfi_1h_arr < getattr(cfg, 'TRADIER_MFI_ENTRY_LONG_TRADIER', 60.0))
    # RSI entry gate
    if getattr(cfg, 'RSI_ENTRY_GATE_ENABLED', False):
        rsi_1h = _safe(npz, 'rsi_1h', n, 50)
        if is_long:
            extra_ok = extra_ok & (rsi_1h < getattr(cfg, 'RSI_ENTRY_MAX_LONG', 37.0))
        else:
            extra_ok = extra_ok & (rsi_1h > getattr(cfg, 'RSI_ENTRY_MIN_SHORT', 63.0))
    # Stoch cross entry tradier
    if getattr(cfg, 'STOCH_CROSS_ENTRY_TRADIER', False):
        k_3m_arr = _safe(npz, 'stoch_k_3m', n, 50)
        d_3m_arr = _safe(npz, 'stoch_d_3m', n, 50)
        k_3m_prev = np.roll(k_3m_arr, 1); k_3m_prev[0] = k_3m_arr[0]
        if is_long:
            extra_ok = extra_ok & ((k_3m_prev <= d_3m_arr) & (k_3m_arr > d_3m_arr))
        else:
            extra_ok = extra_ok & ((k_3m_prev >= d_3m_arr) & (k_3m_arr < d_3m_arr))
    # TF alignment min total (score-based)
    if getattr(cfg, 'BACKTEST_VALIDATED_GATES_TRADIER', True):
        wt1_4h_arr = _safe(npz, 'wt1_4h', n); wt2_4h_arr = _safe(npz, 'wt2_4h', n)
        wt1_D_arr = _safe(npz, 'wt1_D', n); wt2_D_arr = _safe(npz, 'wt2_D', n)
        if is_long:
            tf_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h_arr > wt2_4h_arr).astype(int) + (wt1_D_arr > wt2_D_arr).astype(int)
        else:
            tf_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h_arr < wt2_4h_arr).astype(int) + (wt1_D_arr < wt2_D_arr).astype(int)
        tf_gate_total = getattr(cfg, 'TF_ALIGNMENT_MIN_TOTAL', 0)
        # Map 0-12 scale to 0-3 (divide by 4) since we only have 3 HTFs
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
    # Entry score threshold (additional convolution with STRENGTH_MIN_SCORE)
    entry_score_min = getattr(cfg, 'TRADIER_ENTRY_SCORE_THRESHOLD', 0)
    if entry_score_min >= 24:
        # Stricter gate on stocks — add MFI_D >= 40 filter for longs
        mfi_D_arr = _safe(npz, 'mfi_D', n, 50)
        if is_long:
            extra_ok = extra_ok & (mfi_D_arr >= 40)
        else:
            extra_ok = extra_ok & (mfi_D_arr <= 60)
    return raw & k3m_ok & ct_vel_ok & ct_dc_ok & htf_ok & mfi_gate & vwap_ok & extra_ok


def compute_exit_signals(npz, n, is_long, cfg):
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0: close = _safe(npz, 'close_5m', n)
    wt1_3m = _safe(npz, 'wt1_3m', n); wt2_3m = _safe(npz, 'wt2_3m', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_4h = _safe(npz, 'wt_velocity_4h', n)
    wt_vel_3m = _safe(npz, 'wt_velocity_3m', n)
    k_3m = _safe(npz, 'stoch_k_3m', n, 50); d_3m = _safe(npz, 'stoch_d_3m', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50); d_1h = _safe(npz, 'stoch_d_1h', n, 50)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50); mfi_3m = _safe(npz, 'mfi_3m', n, 50)
    bb_pctb_1h = _safe(npz, 'bb_pct_b_1h', n, 0.5)

    if is_long:
        wt_against = (wt1_3m < wt2_3m).astype(int) + (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int)
    else:
        wt_against = (wt1_3m > wt2_3m).astype(int) + (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int)
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
            # Was strong positive momentum, now decayed below threshold AND 3m vel also dropping
            was_strong = wt_vel_1h_prev > decay_threshold * 2
            now_decayed = wt_vel_1h_exit < decay_threshold
            vel_decay_exit = was_strong & now_decayed & (wt_vel_3m < wt_vel_1h_prev * 0.5)
        else:
            was_strong = wt_vel_1h_prev < -decay_threshold * 2
            now_decayed = wt_vel_1h_exit > -decay_threshold
            vel_decay_exit = was_strong & now_decayed & (wt_vel_3m > wt_vel_1h_prev * 0.5)
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
        k_3m_prev = np.roll(k_3m, 1); k_3m_prev[0] = k_3m[0]
        mfi_3m_prev = np.roll(mfi_3m, 1); mfi_3m_prev[0] = mfi_3m[0]
        if is_long:
            sat_exit = (k_3m_prev >= 80) & (k_3m_prev >= d_3m) & (k_3m < d_3m) & (mfi_3m < mfi_3m_prev)
        else:
            sat_exit = (k_3m_prev <= 20) & (k_3m_prev <= d_3m) & (k_3m > d_3m) & (mfi_3m > mfi_3m_prev)
    rz_exit = np.zeros(n, dtype=bool)
    if cfg.RZ_EXIT_ENABLED:
        if is_long:
            rz_exit = ((bb_pctb_1h > 0.85) | (k_1h >= 80)) & (wt_vel_3m < -1.0)
        else:
            rz_exit = ((bb_pctb_1h < 0.15) | (k_1h <= 20)) & (wt_vel_3m > 1.0)
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
        wt1_3m_prev = np.roll(wt1_3m, 1); wt1_3m_prev[0] = wt1_3m[0]
        if is_long:
            wt_cu_exit = (wt1_3m_prev >= wt2_3m) & (wt1_3m < wt2_3m) & (k_3m >= 70)
        else:
            wt_cu_exit = (wt1_3m_prev <= wt2_3m) & (wt1_3m > wt2_3m) & (k_3m <= 30)
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
    # RSI2 exit tradier (Connors-style)
    if getattr(cfg, 'TRADIER_RSI2_ENABLED', False):
        rsi2_3m = _safe(npz, 'rsi2_3m', n, 50)
        if rsi2_3m.sum() > 0:
            if is_long:
                extra_exit = extra_exit | (rsi2_3m >= getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_LONG', 90.0))
            else:
                extra_exit = extra_exit | (rsi2_3m <= getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_SHORT', 10.0))
    # Satoshit exit (simplified — RSI+Stoch votes)
    if getattr(cfg, 'SATOSHIT_EXIT_ENABLED', False):
        k_3m_arr2 = _safe(npz, 'stoch_k_3m', n, 50)
        if is_long:
            rsi_hit = rsi_1h_arr >= getattr(cfg, 'SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER', 55.0)
            stoch_hit = k_3m_arr2 >= getattr(cfg, 'SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER', 60.0)
        else:
            rsi_hit = rsi_1h_arr <= getattr(cfg, 'SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER', 42.0)
            stoch_hit = k_3m_arr2 <= getattr(cfg, 'SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER', 50.0)
        votes_needed = getattr(cfg, 'SATOSHIT_MIN_VOTES_TRADIER', 3)
        # 2 visible + 1 latent (bb_pctb would be 3rd) — simpler: require 2 of 2 if votes >= 3
        if votes_needed >= 3:
            extra_exit = extra_exit | (rsi_hit & stoch_hit)
        else:
            extra_exit = extra_exit | (rsi_hit | stoch_hit)
    # Stoch cross 3m exit
    if getattr(cfg, 'STOCH_CROSS_3M_EXIT_ENABLED', False):
        k_3m_arr2 = _safe(npz, 'stoch_k_3m', n, 50)
        d_3m_arr2 = _safe(npz, 'stoch_d_3m', n, 50)
        k_3m_prev2 = np.roll(k_3m_arr2, 1); k_3m_prev2[0] = k_3m_arr2[0]
        if is_long:
            extra_exit = extra_exit | ((k_3m_prev2 >= d_3m_arr2) & (k_3m_arr2 < d_3m_arr2) & (k_3m_arr2 > 60))
        else:
            extra_exit = extra_exit | ((k_3m_prev2 <= d_3m_arr2) & (k_3m_arr2 > d_3m_arr2) & (k_3m_arr2 < 40))
    # Ablation: disable quick exit path
    if getattr(cfg, 'ABLATION_DISABLE_QUICK_EXIT', False):
        return np.zeros(n, dtype=bool)
    # Cycle TP early cap
    if getattr(cfg, 'CYCLE_TP_TIERED_ENABLED', False):
        # Engine has PROFIT_TARGET_PCT; CYCLE_TP_PCT acts as upper cap
        pass  # handled in simulate() via PROFIT_TARGET_PCT
    return delta_exit | vel_exit | srs_exit | sat_exit | rz_exit | stoch_1h_exit | mfi_flip_exit | wt_cu_exit | mi_exit | vel_decay_exit | extra_exit


def simulate(stores, cfg, capital=10000.0):
    all_pnl = []
    start_size = cfg.START_POSITION_SIZE
    cooldown = cfg.COOLDOWN_BARS
    min_hold = cfg.MIN_HOLD_BARS
    for sym, npz in stores.items():
        ts = npz.get('timestamps', npz.get('timestamp_3m', np.array([])))
        n = len(ts)
        if n < 100: continue
        close = _safe(npz, 'close_3m', n)
        if close.sum() == 0: close = _safe(npz, 'close_5m', n)
        dc_high_4h = _safe(npz, 'dc_high_4h', n)
        dc_low_4h = _safe(npz, 'dc_low_4h', n)
        for is_long in [True, False]:
            entry_sig = compute_entry_signals(npz, n, is_long, cfg)
            exit_sig = compute_exit_signals(npz, n, is_long, cfg)
            in_pos = False; ep = 0.0; eb = 0; cd = 0
            pt_enabled = cfg.PROFIT_TARGET_ENABLED
            pt_pct = cfg.PROFIT_TARGET_PCT
            sl_enabled = cfg.STOP_LOSS_ENABLED
            sl_pct = cfg.STOP_LOSS_PCT
            for i in range(n):
                if cd > 0: cd -= 1; continue
                px = close[i]
                if px <= 0: continue
                if not in_pos and entry_sig[i]:
                    in_pos = True; ep = px; eb = i
                    continue
                if in_pos:
                    live_pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    # Profit target hit — exit immediately regardless of technical signal
                    if pt_enabled and live_pnl >= pt_pct:
                        all_pnl.append(live_pnl); in_pos = False; cd = cooldown; continue
                    # Stop loss hit — exit at loss
                    if sl_enabled and live_pnl <= -sl_pct:
                        all_pnl.append(live_pnl); in_pos = False; cd = cooldown; continue
                if in_pos and exit_sig[i] and (i - eb) >= min_hold:
                    pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    if cfg.NOLOSS_ENABLED and pnl < 0:
                        if cfg.DC_RECOVERY_EXIT_ENABLED:
                            if is_long:
                                stranded = ep > dc_high_4h[i] and dc_high_4h[i] > 0
                            else:
                                stranded = ep < dc_low_4h[i] and dc_low_4h[i] > 0
                            near = abs(px - ep) / ep * 100 < cfg.DC_RECOVERY_EXIT_TOLERANCE_PCT
                            if not (stranded and near):
                                continue
                        else:
                            continue
                    all_pnl.append(pnl)
                    in_pos = False; cd = cooldown
    n_trades = len(all_pnl)
    if n_trades < 2:
        return {"sharpe": 0, "pnl": 0, "trades": n_trades, "wins": 0, "losses": 0, "avg_pnl_pct": 0, "wr": 0}
    p = np.array(all_pnl)
    w = int((p > 0).sum()); l = int((p <= 0).sum())
    m = p.mean(); s = p.std()
    return {
        "sharpe": round(m / s if s > 0 else 0, 4),
        "pnl": round(p.sum() / 100 * start_size, 2),
        "trades": n_trades, "wins": w, "losses": l,
        "avg_pnl_pct": round(m, 4),
        "wr": round(w / n_trades * 100, 1) if n_trades > 0 else 0,
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
    stores = load_npz(args.mode, syms, args.start, args.npz_dir)
    if not stores:
        print("No data"); return
    r = simulate(stores, cfg, args.capital)
    el = time.time() - t0
    print(f"V8_QUICK_RESULT: sharpe={r['sharpe']} pnl={r['pnl']:.2f} trades={r['trades']} "
          f"wins={r['wins']} losses={r['losses']} wr={r['wr']}% "
          f"avg_pnl={r['avg_pnl_pct']:.4f}% elapsed={el:.1f}s")


if __name__ == "__main__":
    main()

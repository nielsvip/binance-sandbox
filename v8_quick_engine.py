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
    # NEW: Confluence mode — only enter when N blocks agree
    CONFLUENCE_MODE_ENABLED: bool = False
    CONFLUENCE_MIN_BLOCKS: int = 2  # How many blocks must agree simultaneously
    # NEW: Signal strength filter — only take top-percentile setups
    # WINNER 2026-04-16: score=5 gave Sharpe 0.94, 87.2% WR, 1.42% avg on 11-sym 4yr
    STRENGTH_FILTER_ENABLED: bool = True
    STRENGTH_MIN_SCORE: float = 5.0
    # Holding period enforcement (avoid rapid exit noise) — WINNER: 10
    MIN_HOLD_BARS: int = 10
    # Profit target exit (sweep-only — boosts Sharpe by locking gains)
    PROFIT_TARGET_ENABLED: bool = False
    PROFIT_TARGET_PCT: float = 1.5  # Exit at this gain % even without technical signal
    # Stop loss exit (sweep-only — cap max loss)
    STOP_LOSS_ENABLED: bool = False
    STOP_LOSS_PCT: float = 2.0  # Exit at this loss %

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

    # STRENGTH FILTER: weighted score
    if cfg.STRENGTH_FILTER_ENABLED:
        # Higher-conviction blocks weighted more (B15=4, B04/B11=3, B02=2, others=1)
        weights = {"B02": 2, "B04": 3, "B11": 3, "B15": 4, "B10": 1, "B12": 1, "B14": 1}
        score = np.zeros(n, dtype=np.float32)
        for name, arr in blocks.items():
            score = score + arr.astype(np.float32) * weights.get(name, 1)
        raw = raw & (score >= cfg.STRENGTH_MIN_SCORE)

    return raw & k3m_ok & ct_vel_ok & ct_dc_ok & htf_ok & mfi_gate & vwap_ok


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

    return delta_exit | vel_exit | srs_exit | sat_exit | rz_exit | stoch_1h_exit | mfi_flip_exit | wt_cu_exit | mi_exit


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

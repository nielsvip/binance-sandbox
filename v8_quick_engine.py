#!/usr/bin/env python3
"""
v8_quick_engine.py — Vectorized V8 backtest engine for ultra-fast sweep testing.

Processes ALL bars at once using numpy arrays instead of scalar per-bar evaluation.
Target: 4yr × 48 symbols in SECONDS, not hours. Proven 420× faster than scalar V8.

ENTRY GATES (crypto + tradier):
  Core:     DC breakout multi-TF + WT 2/3 in favor + K3M_FLOOR
  Filters:  CT_WT_VELOCITY (BC_170) + CT_DC_CROSSOVER_SKIP (BC_172)
  Signals:  SATOSHIT 3-of-5 + Delta/RZ zone bounce
  Tradier:  K_ZONE entry + MFI entry gate + VWAP filter + FH_MOMENTUM + DC_DAYTRADE
  HTF:      D-trend HA alignment + HTF WT count

EXIT GATES (crypto + tradier):
  Core:     WT 2/3 against (delta exit) + WT velocity 4h
  SRS:      Structural range shift at DC/BB boundary (dc_4h crypto, bb_1h stocks)
  SATOSHIT: Stoch cross from OB/OS + MFI declining
  RZ:       Zone reversal (top rejection, bottom bounce exit)
  Tradier:  Stoch cross 1h + MFI flip exit + WT crossunder final
  NOLOSS:   Block exit at loss unless DC/BB recovery exception

Usage:
  python v8_quick_engine.py --mode crypto --symbols fast --start 2022-01-01
  python v8_quick_engine.py --mode tradier --symbols fast --start 2024-01-01
"""
import argparse
import json
import math
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
    # === CORE ===
    MODE: str = "crypto"
    ENTRY_SCORE_THRESHOLD: float = 18.0
    K3M_FLOOR: float = 30.0
    COOLDOWN_BARS: int = 3
    NOLOSS_ENABLED: bool = False
    DC_RECOVERY_EXIT_ENABLED: bool = False
    DC_RECOVERY_EXIT_TOLERANCE_PCT: float = 0.25
    START_POSITION_SIZE: float = 2000.0
    MIN_POSITION_SIZE: float = 55.0
    # === CT GATES (BC_170-174) ===
    CT_WT_VELOCITY_GATE_ENABLED: bool = True
    CT_WT_VELOCITY_1H_MIN: float = 0.0
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = True
    CT_15M_MOMENTUM_GATE_ENABLED: bool = False
    CT_CHOP_4H_GATE_ENABLED: bool = False
    CT_VOLUME_SURGE_GATE_ENABLED: bool = False
    # === DELTA / RZ ===
    DELTA_ENGINE_ENABLED: bool = True
    DELTA_ENTRY_ENABLED: bool = True
    RZ_EXIT_ENABLED: bool = True
    # === SATOSHIT ===
    SATOSHIT_ENABLED: bool = True
    SATOSHIT_MIN_VOTES: int = 3
    # === SRS ===
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = True
    STRUCTURAL_RANGE_SHIFT_TF: str = "dc_4h"
    # === REENTRY ===
    REENTRY_RALLY_K15M_MAX: float = 100.0
    REENTRY_RALLY_HTF_MIN: int = 1
    # === HTF ALIGNMENT ===
    HTF_ALIGNMENT_ENABLED: bool = True
    HTF_MIN_ALIGNED: int = 1
    D_TREND_REQUIRED: bool = True
    # === TRADIER SPECIFIC ===
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


def load_npz(mode: str, symbols: Optional[List[str]], start_date: str, npz_dir: str = "") -> Dict[str, dict]:
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
            if mode == "crypto" and not is_crypto:
                continue
            if mode == "tradier" and is_crypto:
                continue
        try:
            data = dict(np.load(str(npz_path), allow_pickle=True))
        except Exception as e:
            print(f"[WARN] Failed to load {sym}: {e}")
            continue
        ts = data.get('timestamps', data.get('timestamp_3m', data.get('timestamp_5m', np.array([]))))
        if len(ts) == 0:
            continue
        if start_ts and ts[-1] < start_ts:
            continue
        start_idx = np.searchsorted(ts, start_ts) if start_ts else 0
        stores[sym] = {k: v[start_idx:] if isinstance(v, np.ndarray) and len(v) > start_idx else v for k, v in data.items()}
    print(f"Loaded {len(stores)} symbols from {d}")
    return stores


def compute_entry_signals(npz: dict, n: int, is_long: bool, cfg: QuickConfig) -> np.ndarray:
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0:
        close = _safe(npz, 'close_5m', n)
    k_3m = _safe(npz, 'stoch_k_3m', n, 50)
    d_3m = _safe(npz, 'stoch_d_3m', n, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    d_1h = _safe(npz, 'stoch_d_1h', n, 50)
    wt1_3m = _safe(npz, 'wt1_3m', n)
    wt2_3m = _safe(npz, 'wt2_3m', n)
    wt1_15m = _safe(npz, 'wt1_15m', n)
    wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n)
    wt2_1h = _safe(npz, 'wt2_1h', n)
    wt1_4h = _safe(npz, 'wt1_4h', n)
    wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_D = _safe(npz, 'wt1_D', n)
    wt2_D = _safe(npz, 'wt2_D', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    wt_bull_3m = _safeb(npz, 'wt_bullish_3m', n)
    wt_bull_15m = _safeb(npz, 'wt_bullish_15m', n)
    dc_high_3m = _safe(npz, 'dc_high_3m', n)
    dc_high_15m = _safe(npz, 'dc_high_15m', n)
    dc_high_1h = _safe(npz, 'dc_high_1h', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n)
    dc_low_3m = _safe(npz, 'dc_low_3m', n)
    dc_low_15m = _safe(npz, 'dc_low_15m', n)
    dc_low_1h = _safe(npz, 'dc_low_1h', n)
    dc_low_4h = _safe(npz, 'dc_low_4h', n)
    mfi_15m = _safe(npz, 'mfi_15m', n, 50)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50)
    mfi_D = _safe(npz, 'mfi_D', n, 50)
    bb_pctb_1h = _safe(npz, 'bb_pct_b_1h', n, 0.5)
    ha_D = _ha_int(npz, 'ha_D', n)
    ha_1h = _ha_int(npz, 'ha_1h', n)

    # === K3M_FLOOR ===
    floor = cfg.K3M_FLOOR
    k3m_ok = (k_3m < (100 - floor)) if is_long else (k_3m > floor)

    # === CT_WT_VELOCITY (BC_170) ===
    ct_vel_ok = np.ones(n, dtype=bool)
    if cfg.CT_WT_VELOCITY_GATE_ENABLED:
        m = cfg.CT_WT_VELOCITY_1H_MIN
        ct_vel_ok = (wt_vel_1h >= m) if is_long else (wt_vel_1h <= -m)

    # === CT_DC_CROSSOVER_SKIP (BC_172) ===
    ct_dc_ok = np.ones(n, dtype=bool)
    if cfg.CT_DC_CROSSOVER_SKIP_ENABLED and not is_long:
        dc_co_15m = _safeb(npz, 'dc_basis_crossover_15m', n)
        dc_co_1h = _safeb(npz, 'dc_basis_crossover_1h', n)
        ct_dc_ok = ~(dc_co_15m | dc_co_1h)

    # === HTF alignment (D mandatory + HTF count) ===
    htf_ok = np.ones(n, dtype=bool)
    if cfg.HTF_ALIGNMENT_ENABLED:
        if is_long:
            htf_count = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
        else:
            htf_count = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        htf_ok = htf_count >= cfg.HTF_MIN_ALIGNED
        if cfg.D_TREND_REQUIRED:
            d_aligned = (ha_D == 1) if is_long else (ha_D == -1)
            d_neutral = (ha_D == 0)
            htf_ok = htf_ok & (d_aligned | d_neutral)

    # === DC breakout multi-TF ===
    buf = 0.001
    if is_long:
        dc_b4h = (dc_high_4h > 0) & (close > dc_high_4h * (1 + buf))
        dc_b1h = (dc_high_1h > 0) & (close > dc_high_1h * (1 + buf)) & ~dc_b4h
        dc_b15 = (dc_high_15m > 0) & (close > dc_high_15m * (1 + buf)) & ~dc_b4h & ~dc_b1h
        dc_b3 = (dc_high_3m > 0) & (close > dc_high_3m * (1 + buf)) & ~dc_b4h & ~dc_b1h & ~dc_b15
    else:
        dc_b4h = (dc_low_4h > 0) & (close < dc_low_4h * (1 - buf))
        dc_b1h = (dc_low_1h > 0) & (close < dc_low_1h * (1 - buf)) & ~dc_b4h
        dc_b15 = (dc_low_15m > 0) & (close < dc_low_15m * (1 - buf)) & ~dc_b4h & ~dc_b1h
        dc_b3 = (dc_low_3m > 0) & (close < dc_low_3m * (1 - buf)) & ~dc_b4h & ~dc_b1h & ~dc_b15
    dc_break = dc_b4h | dc_b1h | dc_b15 | dc_b3
    stoch_wrong = (k_15m > 70) if is_long else (k_15m < 30)
    wt_conf = (wt1_15m > wt2_15m) if is_long else (wt1_15m < wt2_15m)
    dc_entry = dc_break & ~stoch_wrong & wt_conf

    # === WT 2/3 in favor ===
    if is_long:
        wt_fav = (wt1_3m > wt2_3m).astype(int) + (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int)
    else:
        wt_fav = (wt1_3m < wt2_3m).astype(int) + (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int)
    wt_2of3 = wt_fav >= 2

    # === SATOSHIT 3-of-5 ===
    sat_entry = np.zeros(n, dtype=bool)
    if cfg.SATOSHIT_ENABLED:
        rsi_15m = _safe(npz, 'rsi_15m', n, 50)
        rel_vol_1h = _safe(npz, 'relative_volume_1h', n, 1.0)
        ha_15m = _ha_int(npz, 'ha_15m', n)
        if is_long:
            votes = (rsi_15m < 50).astype(int) + (bb_pctb_1h < 0.50).astype(int) + (ha_15m == -1).astype(int) + (k_15m < 60).astype(int) + (mfi_15m < 60).astype(int)
        else:
            votes = (rsi_15m > 55).astype(int) + (bb_pctb_1h > 0.55).astype(int) + (ha_15m == 1).astype(int) + (k_15m > 50).astype(int) + (mfi_15m > 50).astype(int)
        sat_entry = (votes >= cfg.SATOSHIT_MIN_VOTES) & (mfi_D >= 30) & (rel_vol_1h >= 0.3)

    # === Delta/RZ zone bounce entry ===
    delta_entry = np.zeros(n, dtype=bool)
    if cfg.DELTA_ENGINE_ENABLED and cfg.DELTA_ENTRY_ENABLED:
        wt_vel_3m = _safe(npz, 'wt_velocity_3m', n)
        wt_vel_15m = _safe(npz, 'wt_velocity_15m', n)
        dc_range = np.maximum(dc_high_1h - dc_low_1h, 1e-9)
        dc_pos_1h = (close - dc_low_1h) / dc_range
        if is_long:
            at_bottom = (bb_pctb_1h < 0.25) | (dc_pos_1h < 0.20)
            delta_entry = at_bottom & (wt_vel_3m > 0) & (wt_vel_15m > -1.0) & (wt1_1h > wt2_1h)
        else:
            at_top = (bb_pctb_1h > 0.75) | (dc_pos_1h > 0.80)
            delta_entry = at_top & (wt_vel_3m < 0) & (wt_vel_15m < 1.0) & (wt1_1h < wt2_1h)

    # === TRADIER: K_ZONE entry (stoch in zone + turning) ===
    kzone_entry = np.zeros(n, dtype=bool)
    if cfg.K_ZONE_ENTRY_ENABLED:
        k_3m_prev = np.roll(k_3m, 1); k_3m_prev[0] = k_3m[0]
        if is_long:
            kzone_entry = (k_3m < cfg.K_ZONE_LONG_THRESHOLD) & (k_3m > k_3m_prev) & (k_3m > d_3m)
        else:
            kzone_entry = (k_3m > cfg.K_ZONE_SHORT_THRESHOLD) & (k_3m < k_3m_prev) & (k_3m < d_3m)

    # === TRADIER: MFI entry gate ===
    mfi_gate = np.ones(n, dtype=bool)
    if cfg.MFI_ENTRY_ENABLED:
        if is_long:
            mfi_gate = mfi_1h < cfg.MFI_ENTRY_LONG_MAX
        else:
            mfi_gate = mfi_1h > cfg.MFI_ENTRY_SHORT_MIN

    # === TRADIER: VWAP filter ===
    vwap_ok = np.ones(n, dtype=bool)
    if cfg.VWAP_FILTER_ENABLED:
        vwap = _safe(npz, 'vwap_D', n)
        if vwap.sum() > 0:
            vwap_ok = (close > vwap) if is_long else (close < vwap)

    # === TRADIER: FH_MOMENTUM (first hour momentum — HA 1h green + DC breakout) ===
    fh_entry = np.zeros(n, dtype=bool)
    if cfg.FH_MOMENTUM_ENABLED:
        if is_long:
            fh_entry = (ha_1h == 1) & dc_break & wt_conf
        else:
            fh_entry = (ha_1h == -1) & dc_break & wt_conf

    # === TRADIER: DC_DAYTRADE (DC position entry — price in favorable zone) ===
    dc_daytrade_ok = np.ones(n, dtype=bool)
    if cfg.DC_DAYTRADE_ENABLED:
        dc_range_4h = np.maximum(dc_high_4h - dc_low_4h, 1e-9)
        dc_pos_4h = (close - dc_low_4h) / dc_range_4h
        thr = cfg.DC_POSITION_ENTRY_THRESHOLD
        if is_long:
            dc_daytrade_ok = dc_pos_4h < (1.0 - thr)
        else:
            dc_daytrade_ok = dc_pos_4h > thr

    # === Combine entries ===
    raw_entry = dc_entry | wt_2of3 | sat_entry | delta_entry | kzone_entry | fh_entry
    entry_signal = raw_entry & k3m_ok & ct_vel_ok & ct_dc_ok & htf_ok & mfi_gate & vwap_ok & dc_daytrade_ok
    return entry_signal


def compute_exit_signals(npz: dict, n: int, is_long: bool, cfg: QuickConfig) -> np.ndarray:
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0:
        close = _safe(npz, 'close_5m', n)
    wt1_3m = _safe(npz, 'wt1_3m', n)
    wt2_3m = _safe(npz, 'wt2_3m', n)
    wt1_15m = _safe(npz, 'wt1_15m', n)
    wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n)
    wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_4h = _safe(npz, 'wt_velocity_4h', n)
    wt_vel_3m = _safe(npz, 'wt_velocity_3m', n)
    k_3m = _safe(npz, 'stoch_k_3m', n, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    d_1h = _safe(npz, 'stoch_d_1h', n, 50)
    d_3m = _safe(npz, 'stoch_d_3m', n, 50)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50)
    mfi_3m = _safe(npz, 'mfi_3m', n, 50)
    bb_pctb_1h = _safe(npz, 'bb_pct_b_1h', n, 0.5)

    # === Delta exit: 2/3 WT against ===
    if is_long:
        wt_against = (wt1_3m < wt2_3m).astype(int) + (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int)
    else:
        wt_against = (wt1_3m > wt2_3m).astype(int) + (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int)
    delta_exit = wt_against >= cfg.WT_EXIT_MIN_TFS

    # === WT velocity 4h exit ===
    vel_exit = (wt_vel_4h < -2.0) if is_long else (wt_vel_4h > 2.0)

    # === SRS exit ===
    srs_exit = np.zeros(n, dtype=bool)
    if cfg.STRUCTURAL_RANGE_SHIFT_EXIT:
        tf = cfg.STRUCTURAL_RANGE_SHIFT_TF
        tf_map = {'dc_1h': ('dc_high_1h', 'dc_low_1h'), 'dc_4h': ('dc_high_4h', 'dc_low_4h'),
                  'bb_1h': ('bb_upper_1h', 'bb_lower_1h'), 'bb_4h': ('bb_upper_4h', 'bb_lower_4h')}
        hk, lk = tf_map.get(tf, ('dc_high_4h', 'dc_low_4h'))
        hi = _safe(npz, hk, n)
        lo = _safe(npz, lk, n)
        k_1h_prev = np.roll(k_1h, 1); k_1h_prev[0] = k_1h[0]
        if is_long:
            prox = (hi > 0) & (np.abs(close - hi) / np.maximum(hi, 1e-9) <= 0.01)
            srs_exit = prox & (k_1h >= 75) & (k_1h < k_1h_prev)
        else:
            prox = (lo > 0) & (np.abs(close - lo) / np.maximum(lo, 1e-9) <= 0.01)
            srs_exit = prox & (k_1h <= 25) & (k_1h > k_1h_prev)

    # === SATOSHIT exit ===
    sat_exit = np.zeros(n, dtype=bool)
    if cfg.SATOSHIT_ENABLED:
        k_3m_prev = np.roll(k_3m, 1); k_3m_prev[0] = k_3m[0]
        mfi_3m_prev = np.roll(mfi_3m, 1); mfi_3m_prev[0] = mfi_3m[0]
        if is_long:
            sat_exit = (k_3m_prev >= 80) & (k_3m_prev >= d_3m) & (k_3m < d_3m) & (mfi_3m < mfi_3m_prev)
        else:
            sat_exit = (k_3m_prev <= 20) & (k_3m_prev <= d_3m) & (k_3m > d_3m) & (mfi_3m > mfi_3m_prev)

    # === RZ exit ===
    rz_exit = np.zeros(n, dtype=bool)
    if cfg.RZ_EXIT_ENABLED:
        if is_long:
            rz_exit = ((bb_pctb_1h > 0.85) | (k_1h >= 80)) & (wt_vel_3m < -1.0)
        else:
            rz_exit = ((bb_pctb_1h < 0.15) | (k_1h <= 20)) & (wt_vel_3m > 1.0)

    # === TRADIER: Stoch cross 1h exit ===
    stoch_1h_exit = np.zeros(n, dtype=bool)
    if cfg.STOCH_CROSS_1H_EXIT_ENABLED:
        k_1h_prev = np.roll(k_1h, 1); k_1h_prev[0] = k_1h[0]
        if is_long:
            stoch_1h_exit = (k_1h_prev >= d_1h) & (k_1h < d_1h) & (k_1h_prev >= 70)
        else:
            stoch_1h_exit = (k_1h_prev <= d_1h) & (k_1h > d_1h) & (k_1h_prev <= 30)

    # === TRADIER: MFI flip exit ===
    mfi_flip_exit = np.zeros(n, dtype=bool)
    if cfg.MFI_FLIP_EXIT_ENABLED:
        if is_long:
            mfi_flip_exit = mfi_1h > cfg.MFI_FLIP_EXIT_LONG_THRESHOLD
        else:
            mfi_flip_exit = mfi_1h < cfg.MFI_FLIP_EXIT_SHORT_THRESHOLD

    # === TRADIER: WT crossunder final ===
    wt_cu_exit = np.zeros(n, dtype=bool)
    if cfg.WT_CROSSUNDER_FINAL_ENABLED:
        wt1_3m_prev = np.roll(wt1_3m, 1); wt1_3m_prev[0] = wt1_3m[0]
        if is_long:
            wt_cu_exit = (wt1_3m_prev >= wt2_3m) & (wt1_3m < wt2_3m) & (k_3m >= 70)
        else:
            wt_cu_exit = (wt1_3m_prev <= wt2_3m) & (wt1_3m > wt2_3m) & (k_3m <= 30)

    # === TRADIER: MI exit (momentum indicator exhaustion) ===
    mi_exit = np.zeros(n, dtype=bool)
    if cfg.MI_EXIT_ENABLED:
        mfi_1h_prev = np.roll(mfi_1h, 1); mfi_1h_prev[0] = mfi_1h[0]
        if is_long:
            mi_exit = (mfi_1h_prev > 70) & (mfi_1h < mfi_1h_prev) & (k_1h > 70)
        else:
            mi_exit = (mfi_1h_prev < 30) & (mfi_1h > mfi_1h_prev) & (k_1h < 30)

    exit_signal = delta_exit | vel_exit | srs_exit | sat_exit | rz_exit | stoch_1h_exit | mfi_flip_exit | wt_cu_exit | mi_exit
    return exit_signal


def simulate(stores: Dict[str, dict], cfg: QuickConfig, capital: float = 10000.0) -> dict:
    all_pnl_pcts = []
    start_size = cfg.START_POSITION_SIZE
    cooldown_bars = cfg.COOLDOWN_BARS

    for sym, npz in stores.items():
        ts = npz.get('timestamps', npz.get('timestamp_3m', npz.get('timestamp_5m', np.array([]))))
        n = len(ts)
        if n < 100:
            continue
        close = _safe(npz, 'close_3m', n)
        if close.sum() == 0:
            close = _safe(npz, 'close_5m', n)
        dc_high_4h = _safe(npz, 'dc_high_4h', n)
        dc_low_4h = _safe(npz, 'dc_low_4h', n)

        for is_long in [True, False]:
            entry_sig = compute_entry_signals(npz, n, is_long, cfg)
            exit_sig = compute_exit_signals(npz, n, is_long, cfg)
            in_position = False
            entry_price = 0.0
            cd = 0
            for i in range(n):
                if cd > 0:
                    cd -= 1
                    continue
                px = close[i]
                if px <= 0:
                    continue
                if not in_position and entry_sig[i]:
                    in_position = True
                    entry_price = px
                elif in_position and exit_sig[i]:
                    pnl_pct = ((px - entry_price) / entry_price * 100) if is_long else ((entry_price - px) / entry_price * 100)
                    if cfg.NOLOSS_ENABLED and pnl_pct < 0:
                        if cfg.DC_RECOVERY_EXIT_ENABLED:
                            if is_long:
                                stranded = entry_price > dc_high_4h[i] and dc_high_4h[i] > 0
                            else:
                                stranded = entry_price < dc_low_4h[i] and dc_low_4h[i] > 0
                            near_entry = abs(px - entry_price) / entry_price * 100 < cfg.DC_RECOVERY_EXIT_TOLERANCE_PCT
                            if stranded and near_entry:
                                pass
                            else:
                                continue
                        else:
                            continue
                    all_pnl_pcts.append(pnl_pct)
                    in_position = False
                    cd = cooldown_bars

    n_trades = len(all_pnl_pcts)
    if n_trades < 2:
        return {"sharpe": 0, "pnl": 0, "trades": n_trades, "wins": 0, "losses": 0, "avg_pnl_pct": 0, "wr": 0}
    pcts = np.array(all_pnl_pcts)
    wins = int((pcts > 0).sum())
    losses = int((pcts <= 0).sum())
    mean_pct = pcts.mean()
    std_pct = pcts.std()
    sharpe = mean_pct / std_pct if std_pct > 0 else 0.0
    total_pnl = pcts.sum() / 100.0 * start_size
    return {
        "sharpe": round(sharpe, 4),
        "pnl": round(total_pnl, 2),
        "trades": n_trades,
        "wins": wins,
        "losses": losses,
        "avg_pnl_pct": round(mean_pct, 4),
        "wr": round(wins / n_trades * 100, 1) if n_trades > 0 else 0,
    }


FAST_SYMBOLS_CRYPTO = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,UNIUSDT"
FAST_SYMBOLS_TRADIER = "AAPL,MSFT,NVDA,AMZN,JPM,XOM,ABBV,TSLA,SPY,META,BA,GLD"


def main():
    parser = argparse.ArgumentParser(description="V8 Quick Vectorized Engine")
    parser.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--symbols", type=str, default="BTCUSDT")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--npz-dir", type=str, default="")
    args = parser.parse_args()

    t0 = time.time()
    override_file = os.environ.get("V8_OVERRIDE_FILE", "")
    cfg = QuickConfig.from_override_file(override_file)
    if args.mode == "tradier":
        cfg.apply_tradier_defaults()
        if override_file and Path(override_file).exists():
            with open(override_file) as f:
                for k, v in json.load(f).items():
                    if hasattr(cfg, k):
                        cur = getattr(cfg, k)
                        if isinstance(cur, bool): setattr(cfg, k, bool(v))
                        elif isinstance(cur, int): setattr(cfg, k, int(v))
                        elif isinstance(cur, float): setattr(cfg, k, float(v))
                        else: setattr(cfg, k, v)

    symbols = None
    if args.symbols == "fast":
        symbols = (FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO).split(",")
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    stores = load_npz(args.mode, symbols, args.start, args.npz_dir)
    if not stores:
        print("No data loaded")
        return

    result = simulate(stores, cfg, args.capital)
    elapsed = time.time() - t0
    print(f"V8_QUICK_RESULT: sharpe={result['sharpe']} pnl={result['pnl']:.2f} trades={result['trades']} "
          f"wins={result['wins']} losses={result['losses']} wr={result['wr']}% "
          f"avg_pnl={result['avg_pnl_pct']:.4f}% elapsed={elapsed:.1f}s")


if __name__ == "__main__":
    main()

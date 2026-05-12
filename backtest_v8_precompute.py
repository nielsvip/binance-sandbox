#!/usr/bin/env python3
"""
V7 Precompute — Build NPZ from klines using REAL tradier_indicators.py functions.

Calls the same functions as live (rsi_series, atr_series, stoch_result, wavetrend,
donchian, mfi_value, heikin_ashi, etc.) but extracts the FULL arrays instead of
just the last value. One call per TF per symbol = fast.

Klines from klines_cache/tradier/ (stocks) or klines_cache/ (crypto).
Output: backtest_v7/indicators/SYMBOL.npz

Usage:
    python3 backtest_v7_precompute.py --symbol AAPL --mode tradier
    python3 backtest_v7_precompute.py --all --mode tradier --workers 8
"""
import argparse
import json
import logging
import os
import platform
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("v7_precompute")

# Imports for new indicator fields (Improvement Framework A3+A4+A5, 2026-04-26).
# Existing scalar functions in tradier_indicators.py are reused where useful;
# numpy-vectorized rolling wrappers below handle the time-series fields.
try:
    from tradier_indicators import (
        compute_minervini_sepa as _scalar_sepa,
        compute_clenow_score as _scalar_clenow,
        detect_episodic_pivot as _scalar_ep,
    )
except Exception as _e:
    _scalar_sepa = None
    _scalar_clenow = None
    _scalar_ep = None

# Module-level mode flag set by main(). Used by compute_tf_arrays() to choose
# annualization factor (252 for tradier, 365 for crypto). Threading via kwarg
# would alter the public signature; module-level keeps existing call sites stable.
MODE = "tradier"


def _ann_factor() -> float:
    """Annualization factor: 252 trading days for stocks, 365 for crypto (24/7)."""
    return 365.0 if MODE == "crypto" else 252.0


def _yz_vol(open_arr: np.ndarray, high_arr: np.ndarray, low_arr: np.ndarray,
            close_arr: np.ndarray, n: int, ann_factor: float) -> np.ndarray:
    """Yang-Zhang (2000) annualized realized volatility, rolling window n. % units."""
    o = np.log(np.maximum(open_arr, 1e-10))
    h = np.log(np.maximum(high_arr, 1e-10))
    l = np.log(np.maximum(low_arr, 1e-10))
    c = np.log(np.maximum(close_arr, 1e-10))
    pc = np.roll(c, 1); pc[0] = c[0]
    o_minus_pc = o - pc
    c_minus_o = c - o
    rs = (h - c) * (h - o) + (l - c) * (l - o)
    def _roll_var(x):
        s1 = pd.Series(x).rolling(n).mean()
        s2 = pd.Series(x * x).rolling(n).mean()
        return (s2 - s1 * s1).clip(lower=0).values
    sig_o2 = _roll_var(o_minus_pc)
    sig_c2 = _roll_var(c_minus_o)
    sig_rs2 = pd.Series(rs).rolling(n).mean().clip(lower=0).values
    k = 0.34 / (1.34 + (n + 1) / max(n - 1, 1))
    yz_var = sig_o2 + k * sig_c2 + (1.0 - k) * sig_rs2
    yz_var = np.nan_to_num(yz_var, nan=0.0, posinf=0.0, neginf=0.0)
    return (np.sqrt(np.maximum(yz_var, 0)) * np.sqrt(ann_factor) * 100.0).astype(np.float32)


def _pk_vol(high_arr: np.ndarray, low_arr: np.ndarray,
            n: int, ann_factor: float) -> np.ndarray:
    """Parkinson high-low annualized volatility. % units."""
    h = np.log(np.maximum(high_arr, 1e-10))
    l = np.log(np.maximum(low_arr, 1e-10))
    hl2 = (h - l) ** 2
    var = pd.Series(hl2).rolling(n).mean().values / (4.0 * np.log(2.0))
    var = np.nan_to_num(var, nan=0.0, posinf=0.0, neginf=0.0)
    return (np.sqrt(np.maximum(var, 0)) * np.sqrt(ann_factor) * 100.0).astype(np.float32)


def _gk_vol(open_arr: np.ndarray, high_arr: np.ndarray, low_arr: np.ndarray,
            close_arr: np.ndarray, n: int, ann_factor: float) -> np.ndarray:
    """Garman-Klass annualized volatility. % units."""
    o = np.log(np.maximum(open_arr, 1e-10))
    h = np.log(np.maximum(high_arr, 1e-10))
    l = np.log(np.maximum(low_arr, 1e-10))
    c = np.log(np.maximum(close_arr, 1e-10))
    term1 = 0.5 * (h - l) ** 2
    term2 = (2.0 * np.log(2.0) - 1.0) * (c - o) ** 2
    var = pd.Series(term1 - term2).rolling(n).mean().clip(lower=0).values
    var = np.nan_to_num(var, nan=0.0, posinf=0.0, neginf=0.0)
    return (np.sqrt(np.maximum(var, 0)) * np.sqrt(ann_factor) * 100.0).astype(np.float32)


def _rolling_sepa(close_arr: np.ndarray, high_arr: np.ndarray, low_arr: np.ndarray,
                  volume_arr: np.ndarray) -> tuple:
    """Rolling Minervini SEPA: per-bar pass/score using bars[:i+1].
    Returns (sepa_pass int8, sepa_score int8). Daily TF only (slow loop)."""
    n = len(close_arr)
    sepa_pass = np.zeros(n, dtype=np.int8)
    sepa_score = np.zeros(n, dtype=np.int8)
    if n < 252 or _scalar_sepa is None:
        return sepa_pass, sepa_score
    cl = close_arr.tolist()
    hl = high_arr.tolist()
    ll = low_arr.tolist()
    vl = volume_arr.tolist()
    for i in range(252, n):
        try:
            res = _scalar_sepa(cl[: i + 1], hl[: i + 1], ll[: i + 1], vl[: i + 1])
            if res:
                sepa_pass[i] = 1 if res.get("sepa_pass") else 0
                sepa_score[i] = int(res.get("sepa_score", 0))
        except Exception:
            pass
    return sepa_pass, sepa_score


def _rolling_clenow(close_arr: np.ndarray, lookback: int = 90) -> tuple:
    """Rolling Clenow: per-bar slope_ann × R². Returns (score, slope, r2) float32 arrays.
    Daily TF only (slow loop)."""
    n = len(close_arr)
    score = np.zeros(n, dtype=np.float32)
    slope = np.zeros(n, dtype=np.float32)
    r2 = np.zeros(n, dtype=np.float32)
    if n < lookback + 5 or _scalar_clenow is None:
        return score, slope, r2
    cl = close_arr.tolist()
    for i in range(lookback + 5, n):
        try:
            res = _scalar_clenow(cl[: i + 1], lookback=lookback)
            if res:
                score[i] = float(res.get("clenow_score", 0.0))
                slope[i] = float(res.get("clenow_slope", 0.0))
                r2[i] = float(res.get("clenow_r2", 0.0))
        except Exception:
            pass
    return score, slope, r2


def _rolling_episodic_pivot(open_arr: np.ndarray, high_arr: np.ndarray,
                             low_arr: np.ndarray, close_arr: np.ndarray,
                             volume_arr: np.ndarray, fwd_days: int = 30) -> tuple:
    """Rolling Episodic Pivot detection (Daily TF). On bar i, run detect on bars[:i+1];
    if detected, mark ep_detected=1 on bars [i, i+fwd_days). Returns (ep_detected int8,
    ep_breakout_level float32, ep_direction int8 +1/-1/0)."""
    n = len(close_arr)
    ep_det = np.zeros(n, dtype=np.int8)
    ep_lvl = np.zeros(n, dtype=np.float32)
    ep_dir = np.zeros(n, dtype=np.int8)
    if n < 30 or _scalar_ep is None:
        return ep_det, ep_lvl, ep_dir
    for i in range(20, n):
        bars = []
        for j in range(max(0, i - 60), i + 1):
            bars.append({
                "open": float(open_arr[j]),
                "high": float(high_arr[j]),
                "low": float(low_arr[j]),
                "close": float(close_arr[j]),
                "volume": float(volume_arr[j]),
            })
        try:
            res = _scalar_ep(bars)
        except Exception:
            res = None
        if res and res.get("ep_detected"):
            lvl = float(res.get("ep_breakout_level", 0.0) or 0.0)
            d = res.get("ep_direction", "")
            d_int = 1 if d == "LONG" else (-1 if d == "SHORT" else 0)
            for k in range(i, min(n, i + fwd_days)):
                # Only fill if not already set by a more recent detection
                if ep_det[k] == 0:
                    ep_det[k] = 1
                    ep_lvl[k] = lvl
                    ep_dir[k] = d_int
    return ep_det, ep_lvl, ep_dir

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance-sandbox")

TRADIER_KLINES = BASE_PATH / "klines_cache_backtest" / "tradier" if not (BASE_PATH / "klines_cache" / "tradier").exists() or platform.system() != "Darwin" else BASE_PATH / "klines_cache" / "tradier"
# Use klines_cache_backtest for full 4yr history on server; klines_cache has only ~1200 bars for many symbols
_kcb = BASE_PATH / "klines_cache_backtest"
CRYPTO_KLINES = _kcb if _kcb.exists() and platform.system() != "Darwin" else BASE_PATH / "klines_cache"
OUT_DIR = BASE_PATH / "backtest_v8" / "indicators"

STR_MAP = {"green": 1, "red": -1, "neutral": 0, "BUY": 1, "SELL": -1, "NEUTRAL": 0,
           "bullish": 1, "bearish": -1, "strong_bullish": 2, "strong_bearish": -2,
           "higher": 1, "lower": -1, "none": 0, "bull_cross": 1, "bear_cross": -1}

# Bar-pattern integer codes (2026-05-11 — for bar_pattern_<tf> int8 array). The
# string→int mapping below mirrors ez_indicators.detect_bar_patterns priority order.
# Engine reads bar_pattern_<tf> as a numeric value; code 0 == "none". A separate
# `bar_pattern_codes` 0-D object array is written to the NPZ holding {int: str}.
BAR_PATTERN_CODES = {
    "none": 0, "morning_star": 1, "evening_star": 2, "three_white_soldiers": 3,
    "three_black_crows": 4, "bull_engulfing": 5, "bear_engulfing": 6,
    "tweezer_bottom": 7, "tweezer_top": 8, "hammer": 9, "shooting_star": 10,
    "bull_harami": 11, "bear_harami": 12, "multi_inside": 13, "inside_bar": 14,
    "outside_bar": 15, "pin_bar_bull": 16, "pin_bar_bear": 17,
    "three_bar_bull": 18, "three_bar_bear": 19, "doji": 20,
}
# bar_vol_regime_<tf>: low=-1, normal=0, high=1.
BAR_VOL_REGIME_CODES = {"low": -1, "normal": 0, "high": 1}


def _bar_pattern_arrays(o: np.ndarray, h: np.ndarray, l_: np.ndarray, c: np.ndarray,
                        v: np.ndarray, tf: str) -> Dict[str, np.ndarray]:
    """Vectorized per-bar replica of ez_indicators.detect_bar_patterns().
    Returns dict of bar_*_<tf> arrays, all length n. Pattern is encoded as int8 via
    BAR_PATTERN_CODES; bar_vol_regime as int8 via BAR_VOL_REGIME_CODES.

    Bar positions: index i is the "current" bar; i-1 is "prev1" (== _b(-2) in live);
    i-2 is "prev2" (== _b(-3)); i-3 is "prev3" (== _b(-4)). Indices < required are
    handled by clamping (giving "none" pattern at the start of the series).
    """
    n = len(c)
    out: Dict[str, np.ndarray] = {}
    if n < 5:
        return out
    # Per-bar arrays
    body = np.abs(c - o)
    rng = np.maximum(h - l_, 1e-10)
    upper_wick = h - np.maximum(o, c)
    lower_wick = np.minimum(o, c) - l_
    body_ratio = body / rng
    is_bull = c > o
    is_bear = c < o
    # Shift helpers (prev1 / prev2 / prev3). Uses np.roll then clamps the head.
    def _shift(a, k):
        out = np.roll(a, k)
        if k > 0:
            out[:k] = a[0]
        return out
    o2 = _shift(o, 1); h2 = _shift(h, 1); l2 = _shift(l_, 1); c2 = _shift(c, 1)
    o3 = _shift(o, 2); h3 = _shift(h, 2); l3 = _shift(l_, 2); c3 = _shift(c, 2)
    o4 = _shift(o, 3); h4 = _shift(h, 3); l4 = _shift(l_, 3); c4 = _shift(c, 3)
    body2 = np.abs(c2 - o2); body3 = np.abs(c3 - o3)
    range2 = np.maximum(h2 - l2, 1e-10)
    range3 = np.maximum(h3 - l3, 1e-10)
    is_bull2 = c2 > o2; is_bear2 = c2 < o2
    is_bull3 = c3 > o3; is_bear3 = c3 < o3
    # --- Volume features (rolling 20-bar trailing average; live uses bars [-21:-1]) ---
    vol_avg = pd.Series(v).shift(1).rolling(20, min_periods=1).mean().bfill().fillna(v[0]).values
    vol_avg = np.where(vol_avg > 0, vol_avg, 1.0)
    vol_ratio = v / vol_avg
    vol_confirm = (vol_ratio >= 1.3).astype(np.int8)
    vol_spike = (vol_ratio >= 2.0).astype(np.int8)
    vol_dry = vol_ratio < 0.6
    # vol_expanding: v[i] > v[i-1] AND v[i-1] > v[i-2] AND v[i-2] > v[i-3] (3 bars rising)
    v_p1 = _shift(v, 1); v_p2 = _shift(v, 2); v_p3 = _shift(v, 3)
    vol_expanding = ((v > v_p1) & (v_p1 > v_p2) & (v_p2 > v_p3)).astype(np.int8)
    # --- ATR rank: rolling 50-bar percent-rank of (h-l) ---
    atr_arr = (h - l_).astype(np.float64)
    atr_rank = np.zeros(n, dtype=np.float32)
    win = min(50, n)
    if win >= 2:
        s = pd.Series(atr_arr)
        # Rolling rank-pct (fraction of window strictly less than current). For speed we
        # use rolling.rank(pct=True) - 1/win to approximate the live `(<curr).sum()/n`.
        rk = s.rolling(win, min_periods=1).rank(pct=True).values
        atr_rank = (rk - (1.0 / win)).clip(min=0.0).astype(np.float32)
    vol_regime = np.where(atr_rank < 0.25, -1, np.where(atr_rank > 0.75, 1, 0)).astype(np.int8)
    # --- Streak (consecutive directional closes), capped at ±7 to mirror live (range(1,8)) ---
    sign = np.where(c > o, 1, np.where(c < o, -1, 0)).astype(np.int8)
    streak = np.zeros(n, dtype=np.int8)
    for i in range(n):
        s = 0
        for k in range(min(7, i + 1)):
            si = int(sign[i - k])
            if si == 0:
                break
            if s == 0:
                s = si
            elif (s > 0 and si > 0) or (s < 0 and si < 0):
                s += si
            else:
                break
        streak[i] = max(min(s, 127), -128)
    # --- Swing structure ---
    hh = (h > h2) & (h2 > h3)
    hl = (l_ > l2) & (l2 > l3)
    ll = (l_ < l2) & (l2 < l3)
    lh = (h < h2) & (h2 < h3)
    swing_bull = (hh & hl).astype(np.int8)
    swing_bear = (ll & lh).astype(np.int8)
    # --- Range compression (5 bars narrowing, oldest→newest = decreasing range) ---
    # live: ranges_5 = [r(-1), r(-2), r(-3), r(-4), r(-5)]; compression = monotonically decreasing
    # in time direction (i.e. each older bar has smaller-or-equal range than the next-newer).
    # Equivalently r(-1) ≤ r(-2) ≤ r(-3) ≤ r(-4) ≤ r(-5) — older bars wider.
    r_p1 = np.maximum(_shift(rng, 1), 1e-10)
    r_p2 = np.maximum(_shift(rng, 2), 1e-10)
    r_p3 = np.maximum(_shift(rng, 3), 1e-10)
    r_p4 = np.maximum(_shift(rng, 4), 1e-10)
    compression = ((rng <= r_p1) & (r_p1 <= r_p2) & (r_p2 <= r_p3) & (r_p3 <= r_p4)).astype(np.int8)
    compression_ratio = (rng / np.where(r_p4 > 0, r_p4, 1e-10)).astype(np.float32)
    # --- Multi-inside count (consecutive inside bars, max 4) ---
    inside_one = ((h < h2) & (l_ > l2)).astype(np.int8)
    inside_two = ((h2 < h3) & (l2 > l3)).astype(np.int8)
    inside_three = ((h3 < h4) & (l3 > l4)).astype(np.int8)
    h5 = _shift(h, 4); l5 = _shift(l_, 4)
    inside_four = ((h4 < h5) & (l4 > l5)).astype(np.int8)
    # Live counts forward from the most recent bar; equivalent to counting consecutive
    # leading 1s in [inside_one, inside_two, inside_three, inside_four].
    inside_count = (inside_one
                    + inside_one * inside_two
                    + inside_one * inside_two * inside_three
                    + inside_one * inside_two * inside_three * inside_four).astype(np.int8)
    # --- Pattern detection (per-bar, priority order matches live) ---
    pattern = np.zeros(n, dtype=np.int8)
    direction = np.zeros(n, dtype=np.int8)
    strength = np.zeros(n, dtype=np.float32)
    # 1. Morning Star
    cond = (is_bear3 & (body3 > range3 * 0.5) & (body2 < range2 * 0.3) & is_bull
            & (body > rng * 0.5) & (c > (o3 + c3) / 2))
    s = np.minimum(1.0, (body + body3) / (2 * rng + 1e-10))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["morning_star"], pattern)
    direction = np.where(cond & (direction == 0), 1, direction)
    strength = np.where(cond & (strength == 0), s, strength)
    # 2. Evening Star
    cond = (is_bull3 & (body3 > range3 * 0.5) & (body2 < range2 * 0.3) & is_bear
            & (body > rng * 0.5) & (c < (o3 + c3) / 2))
    s = np.minimum(1.0, (body + body3) / (2 * rng + 1e-10))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["evening_star"], pattern)
    direction = np.where(cond & (direction == 0) & (pattern == BAR_PATTERN_CODES["evening_star"]), -1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["evening_star"]) & (strength == 0), s, strength)
    # 3. Three White Soldiers
    cond = (is_bull & is_bull2 & is_bull3 & (c > c2) & (c2 > c3)
            & (body > rng * 0.5) & (body2 > range2 * 0.5) & (body3 > range3 * 0.5))
    s = np.minimum(1.0, np.minimum.reduce([body, body2, body3]) / np.maximum.reduce([rng, range2, range3]))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["three_white_soldiers"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["three_white_soldiers"]) & (direction == 0), 1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["three_white_soldiers"]) & (strength == 0), s, strength)
    # 4. Three Black Crows
    cond = (is_bear & is_bear2 & is_bear3 & (c < c2) & (c2 < c3)
            & (body > rng * 0.5) & (body2 > range2 * 0.5) & (body3 > range3 * 0.5))
    s = np.minimum(1.0, np.minimum.reduce([body, body2, body3]) / np.maximum.reduce([rng, range2, range3]))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["three_black_crows"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["three_black_crows"]) & (direction == 0), -1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["three_black_crows"]) & (strength == 0), s, strength)
    # 5. Bull Engulfing
    cond = (is_bull & is_bear2 & (c > o2) & (o < c2) & (body > body2))
    s = np.minimum(1.0, (body / (body2 + 1e-10)) * 0.5)
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["bull_engulfing"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["bull_engulfing"]) & (direction == 0), 1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["bull_engulfing"]) & (strength == 0), s, strength)
    # 6. Bear Engulfing
    cond = (is_bear & is_bull2 & (c < o2) & (o > c2) & (body > body2))
    s = np.minimum(1.0, (body / (body2 + 1e-10)) * 0.5)
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["bear_engulfing"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["bear_engulfing"]) & (direction == 0), -1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["bear_engulfing"]) & (strength == 0), s, strength)
    # 7. Tweezer Bottom
    cond = (is_bull & (np.abs(l_ - l2) < rng * 0.05) & (l_ < np.minimum(l3, l4)))
    s = np.minimum(1.0, 1.0 - np.abs(l_ - l2) / rng)
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["tweezer_bottom"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["tweezer_bottom"]) & (direction == 0), 1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["tweezer_bottom"]) & (strength == 0), s, strength)
    # 8. Tweezer Top
    cond = (is_bear & (np.abs(h - h2) < rng * 0.05) & (h > np.maximum(h3, h4)))
    s = np.minimum(1.0, 1.0 - np.abs(h - h2) / rng)
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["tweezer_top"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["tweezer_top"]) & (direction == 0), -1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["tweezer_top"]) & (strength == 0), s, strength)
    # 9. Hammer
    cond = ((body_ratio < 0.35) & (lower_wick > body * 2.0) & (upper_wick < body * 0.5))
    s = np.minimum(1.0, lower_wick / rng)
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["hammer"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["hammer"]) & (direction == 0), 1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["hammer"]) & (strength == 0), s, strength)
    # 10. Shooting Star
    cond = ((body_ratio < 0.35) & (upper_wick > body * 2.0) & (lower_wick < body * 0.5))
    s = np.minimum(1.0, upper_wick / rng)
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["shooting_star"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["shooting_star"]) & (direction == 0), -1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["shooting_star"]) & (strength == 0), s, strength)
    # 11. Bull Harami
    cond = (is_bull & is_bear2 & (body < body2 * 0.5) & (h < h2) & (l_ > l2))
    s = 0.5 * (1.0 - body / (body2 + 1e-10))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["bull_harami"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["bull_harami"]) & (direction == 0), 1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["bull_harami"]) & (strength == 0), s, strength)
    # 12. Bear Harami
    cond = (is_bear & is_bull2 & (body < body2 * 0.5) & (h < h2) & (l_ > l2))
    s = 0.5 * (1.0 - body / (body2 + 1e-10))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["bear_harami"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["bear_harami"]) & (direction == 0), -1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["bear_harami"]) & (strength == 0), s, strength)
    # 13. Multi-inside (≥2 consecutive inside bars)
    cond = inside_count >= 2
    s = np.minimum(1.0, inside_count.astype(np.float64) * 0.3)
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["multi_inside"], pattern)
    strength = np.where((pattern == BAR_PATTERN_CODES["multi_inside"]) & (strength == 0), s, strength)
    # 14. Inside Bar
    cond = (h < h2) & (l_ > l2)
    s = 1.0 - (rng / np.where(range2 > 0, range2, 1e-10))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["inside_bar"], pattern)
    strength = np.where((pattern == BAR_PATTERN_CODES["inside_bar"]) & (strength == 0), s, strength)
    # 15. Outside Bar
    cond = ((h > h2) & (l_ < l2) & (body_ratio > 0.6))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["outside_bar"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["outside_bar"]) & (direction == 0),
                         np.where(is_bull, 1, -1), direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["outside_bar"]) & (strength == 0), body_ratio, strength)
    # 16. Pin Bar Bull
    cond = (lower_wick > rng * 0.6) & (body_ratio < 0.25)
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["pin_bar_bull"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["pin_bar_bull"]) & (direction == 0), 1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["pin_bar_bull"]) & (strength == 0), lower_wick / rng, strength)
    # 17. Pin Bar Bear
    cond = (upper_wick > rng * 0.6) & (body_ratio < 0.25)
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["pin_bar_bear"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["pin_bar_bear"]) & (direction == 0), -1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["pin_bar_bear"]) & (strength == 0), upper_wick / rng, strength)
    # 18. Three Bar Bull
    cond = (is_bull & is_bear2 & is_bear3 & (c > h2))
    s = np.minimum(1.0, body / (body2 + body3 + 1e-10))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["three_bar_bull"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["three_bar_bull"]) & (direction == 0), 1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["three_bar_bull"]) & (strength == 0), s, strength)
    # 19. Three Bar Bear
    cond = (is_bear & is_bull2 & is_bull3 & (c < l2))
    s = np.minimum(1.0, body / (body2 + body3 + 1e-10))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["three_bar_bear"], pattern)
    direction = np.where((pattern == BAR_PATTERN_CODES["three_bar_bear"]) & (direction == 0), -1, direction)
    strength = np.where((pattern == BAR_PATTERN_CODES["three_bar_bear"]) & (strength == 0), s, strength)
    # 20. Doji
    cond = body_ratio < 0.1
    s = 0.3 + 0.4 * (vol_confirm.astype(np.float32))
    pattern = np.where(cond & (pattern == 0), BAR_PATTERN_CODES["doji"], pattern)
    strength = np.where((pattern == BAR_PATTERN_CODES["doji"]) & (strength == 0), s, strength)
    # Volume amplifier (mirrors live)
    has_dir = direction != 0
    strength = np.where(has_dir & (vol_confirm.astype(bool)), np.minimum(1.0, strength * 1.3), strength)
    strength = np.where(has_dir & (vol_spike.astype(bool)), np.minimum(1.0, strength * 1.2), strength)
    strength = np.where(has_dir & vol_dry, strength * 0.6, strength)
    out[f"bar_pattern_{tf}"] = pattern.astype(np.int8)
    out[f"bar_direction_{tf}"] = direction.astype(np.int8)
    out[f"bar_strength_{tf}"] = strength.astype(np.float32)
    out[f"bar_vol_confirm_{tf}"] = vol_confirm
    out[f"bar_vol_ratio_{tf}"] = vol_ratio.astype(np.float32)
    out[f"bar_body_ratio_{tf}"] = body_ratio.astype(np.float32)
    out[f"bar_upper_wick_{tf}"] = (upper_wick / np.where(rng > 0, rng, 1e-10)).astype(np.float32)
    out[f"bar_lower_wick_{tf}"] = (lower_wick / np.where(rng > 0, rng, 1e-10)).astype(np.float32)
    out[f"bar_streak_{tf}"] = streak
    out[f"bar_swing_bull_{tf}"] = swing_bull
    out[f"bar_swing_bear_{tf}"] = swing_bear
    out[f"bar_compression_{tf}"] = compression
    out[f"bar_compression_ratio_{tf}"] = compression_ratio
    out[f"bar_inside_count_{tf}"] = inside_count
    out[f"bar_vol_spike_{tf}"] = vol_spike
    out[f"bar_vol_expanding_{tf}"] = vol_expanding
    out[f"bar_vol_regime_{tf}"] = vol_regime
    out[f"bar_atr_rank_{tf}"] = atr_rank
    return out


def _ha_streak_array(o: np.ndarray, h: np.ndarray, l_: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Per-bar Heikin-Ashi streak (mirrors ez_indicators.ha_streak_count counted on the
    rolling end of the last 20 HA candles). Returns int8 array of length n."""
    n = len(c)
    if n < 3:
        return np.zeros(n, dtype=np.int8)
    ha_close = (o + h + l_ + c) / 4.0
    ha_open = np.zeros(n, dtype=np.float64)
    ha_open[0] = (o[0] + c[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
    ha_color = np.where(ha_close >= ha_open, 1, -1).astype(np.int8)
    streak = np.zeros(n, dtype=np.int8)
    for i in range(n):
        s = 0
        # Live walks back up to 20 bars
        for k in range(min(20, i + 1)):
            col = int(ha_color[i - k])
            if s == 0:
                s = col
            elif (s > 0 and col > 0) or (s < 0 and col < 0):
                s += col
            else:
                break
        streak[i] = max(min(s, 127), -128)
    return streak


def _rolling_linreg(close_arr: np.ndarray, length: int) -> tuple:
    """Per-bar rolling linreg: returns (slope, linearity) arrays length n.
    Mirrors ez_indicators.linreg_features with y_fit = y_mean + slope*(x-x_mean)
    (the 2026-04-29 bug-fixed formula)."""
    n = len(close_arr)
    slope = np.zeros(n, dtype=np.float32)
    lin = np.zeros(n, dtype=np.float32)
    if n < length:
        return slope, lin
    x = np.arange(length, dtype=np.float64)
    x_mean = x.mean()
    x_dev = x - x_mean
    x_var = (x_dev ** 2).sum()
    if x_var <= 0:
        return slope, lin
    for i in range(length - 1, n):
        y = close_arr[i - length + 1:i + 1].astype(np.float64)
        if not np.isfinite(y).all():
            continue
        y_mean = y.mean()
        sl = (x_dev * (y - y_mean)).sum() / x_var
        y_fit = y_mean + sl * x_dev
        ss_res = ((y - y_fit) ** 2).sum()
        ss_tot = ((y - y_mean) ** 2).sum()
        slope[i] = sl
        lin[i] = (1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    return slope, lin


def _bb_pctb_with_touches(close: pd.Series, high: pd.Series, low: pd.Series,
                          mult: float, length: int = 20) -> tuple:
    """Returns (bb_high, bb_low, bb_pctb, bb_width, touches_count_rolling20). Used to fill
    bb_high_<tf>, bb_low_<tf>, bb_width_<tf>, bb_touches_<tf> fields."""
    n = len(close)
    sma = close.rolling(length, min_periods=1).mean()
    std = close.rolling(length, min_periods=1).std(ddof=0).fillna(0)
    upper = sma + mult * std
    lower = sma - mult * std
    width = upper - lower
    pctb = np.where(width > 0, (close - lower) / width, 0.5)
    # Touches: bar high >= upper OR bar low <= lower in last 20 bars.
    tt = ((high >= upper) | (low <= lower)).astype(np.int32)
    touches = pd.Series(tt).rolling(20, min_periods=1).sum().values
    return (upper.values.astype(np.float32), lower.values.astype(np.float32),
            pctb.astype(np.float32), width.values.astype(np.float32),
            touches.astype(np.float32))


def load_klines(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if not data:
            return None
        df = pd.DataFrame(data)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "timestamp" in df.columns:
            df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
        elif "timestamp_dt" in df.columns:
            df["timestamp_dt"] = pd.to_datetime(df["timestamp_dt"], utc=True, format="ISO8601")
        else:
            return None
        df = df.set_index("timestamp_dt").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df
    except Exception as e:
        logger.warning(f"Load failed {path}: {e}")
        return None


def compute_tf_arrays(df: pd.DataFrame, tf: str) -> Dict[str, np.ndarray]:
    """Compute ALL indicators for one TF, returning FULL arrays (not scalars).
    Uses the SAME functions from tradier_indicators.py."""
    from tradier_indicators import (rsi_series, atr_series, mfi_value, stoch_result,
                                     donchian, heikin_ashi, wavetrend,
                                     relative_volume, wavetrend_intelligence,
                                     crossover_flags)
    n = len(df)
    # 2026-04-28: lowered threshold for HTF (W/M) — tradier 2yr data has only ~24 monthly
    # bars but engine still benefits from wt1_M/wt2_M etc. Skip Yang-Zhang vol & SEPA paths
    # internally when n is small (existing guards already handle that).
    if n < (10 if tf in ("W", "M") else 30):
        return {}
    out = {}
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series(np.ones(n), index=df.index)
    # OHLCV arrays
    out[f"open_{tf}"] = open_.values.astype(np.float32)
    out[f"high_{tf}"] = high.values.astype(np.float32)
    out[f"low_{tf}"] = low.values.astype(np.float32)
    out[f"close_{tf}"] = close.values.astype(np.float32)
    out[f"volume_{tf}"] = volume.values.astype(np.float32)
    # Prev arrays
    for name, series in [("close", close), ("high", high), ("low", low)]:
        prev = series.shift(1).fillna(series.iloc[0])
        out[f"{name}_{tf}_prev"] = prev.values.astype(np.float32)
    # RSI 14
    rsi = rsi_series(close, 14)
    if rsi is not None and not rsi.empty:
        out[f"rsi_{tf}"] = rsi.values.astype(np.float32)
    # ATR 14
    atr = atr_series(df, 14)
    if atr is not None and not atr.empty:
        out[f"atr_{tf}"] = atr.values.astype(np.float32)
        out[f"atr_{tf}_prev"] = atr.shift(1).fillna(atr.iloc[0]).values.astype(np.float32)
    # Stochastic RSI (full series)
    from tradier_indicators import stoch_rsi
    stoch_df = stoch_rsi(close, 14, 7, 7)
    k_series = stoch_df["k"] if stoch_df is not None and "k" in stoch_df.columns else (stoch_df["%K"] if stoch_df is not None and "%K" in stoch_df.columns else None)
    d_series = stoch_df["d"] if stoch_df is not None and "d" in stoch_df.columns else (stoch_df["%D"] if stoch_df is not None and "%D" in stoch_df.columns else None)
    if k_series is not None and len(k_series) == n:
        out[f"stoch_k_{tf}"] = k_series.values.astype(np.float32)
        out[f"stoch_d_{tf}"] = d_series.values.astype(np.float32)
        out[f"stoch_k_{tf}_prev"] = k_series.shift(1).fillna(50).values.astype(np.float32)
        out[f"stoch_d_{tf}_prev"] = d_series.shift(1).fillna(50).values.astype(np.float32)
        # Crossovers
        co = ((k_series > d_series) & (k_series.shift(1) <= d_series.shift(1))).fillna(False)
        cu = ((k_series < d_series) & (k_series.shift(1) >= d_series.shift(1))).fillna(False)
        out[f"stoch_crossover_{tf}"] = co.values.astype(np.int8)
        out[f"stoch_crossunder_{tf}"] = cu.values.astype(np.int8)
    # MFI (full series via rolling)
    tp = (high + low + close) / 3.0
    raw_mf = tp * volume
    pos_mf = raw_mf.where(tp.diff() > 0, 0)
    neg_mf = raw_mf.where(tp.diff() < 0, 0)
    pos_sum = pos_mf.rolling(14, min_periods=1).sum()
    neg_sum = neg_mf.rolling(14, min_periods=1).sum()
    mfi = 100.0 - (100.0 / (1.0 + pos_sum / neg_sum.replace(0, 1e-10)))
    out[f"mfi_{tf}"] = mfi.values.astype(np.float32)
    # Donchian channels (20)
    dc_high = high.rolling(20, min_periods=1).max()
    dc_low = low.rolling(20, min_periods=1).min()
    dc_basis = (dc_high + dc_low) / 2.0
    out[f"dc_high_{tf}"] = dc_high.values.astype(np.float32)
    out[f"dc_low_{tf}"] = dc_low.values.astype(np.float32)
    out[f"dc_basis_{tf}"] = dc_basis.values.astype(np.float32)
    out[f"dc_high_{tf}_prev"] = dc_high.shift(1).fillna(dc_high.iloc[0]).values.astype(np.float32)
    out[f"dc_low_{tf}_prev"] = dc_low.shift(1).fillna(dc_low.iloc[0]).values.astype(np.float32)
    out[f"dc_basis_{tf}_prev"] = dc_basis.shift(1).fillna(dc_basis.iloc[0]).values.astype(np.float32)
    # DC ancient (30 bars back) — 2026-04-27: added dc_basis_{tf}_ant emission. HTF entry engine reads dc_basis_D_ant; was silently fallback-zeroed.
    out[f"dc_high_{tf}_ant"] = dc_high.shift(30).fillna(dc_high.iloc[0]).values.astype(np.float32)
    out[f"dc_low_{tf}_ant"] = dc_low.shift(30).fillna(dc_low.iloc[0]).values.astype(np.float32)
    out[f"dc_basis_{tf}_ant"] = dc_basis.shift(30).fillna(dc_basis.iloc[0]).values.astype(np.float32)
    # DC width + position
    dc_range = dc_high - dc_low
    out[f"dc_width_{tf}"] = (dc_range / dc_basis.replace(0, 1e-10) * 100).values.astype(np.float32)
    out[f"dc_position_{tf}"] = ((close - dc_low) / dc_range.replace(0, 1e-10)).clip(0, 1).values.astype(np.float32)
    # DC4 (4-bar)
    dc_high4 = high.rolling(4, min_periods=1).max()
    dc_low4 = low.rolling(4, min_periods=1).min()
    out[f"dc_high4_{tf}"] = dc_high4.values.astype(np.float32)
    out[f"dc_low4_{tf}"] = dc_low4.values.astype(np.float32)
    # DC crossovers
    close_prev = close.shift(1).fillna(close.iloc[0])
    for name, level, level_prev in [("dc_basis", dc_basis, dc_basis.shift(1).fillna(dc_basis.iloc[0])),
                                      ("dc_high", dc_high, dc_high.shift(1).fillna(dc_high.iloc[0])),
                                      ("dc_low", dc_low, dc_low.shift(1).fillna(dc_low.iloc[0]))]:
        co = ((close > level) & (close_prev <= level_prev)).fillna(False)
        cu = ((close < level) & (close_prev >= level_prev)).fillna(False)
        out[f"{name}_crossover_{tf}"] = co.values.astype(np.int8)
        out[f"{name}_crossunder_{tf}"] = cu.values.astype(np.int8)
    # WaveTrend (full series)
    try:
        wt1_s, wt2_s = wavetrend(df, timeframe=tf)
        if wt1_s is not None and not wt1_s.empty:
            # Pad/truncate to match n
            def _fit(arr, n):
                a = np.asarray(arr, dtype=np.float64)
                if len(a) == n: return a
                if len(a) > n: return a[:n]
                return np.pad(a, (0, n - len(a)), mode='edge')
            wt1_s_fit = _fit(wt1_s.values, n)
            wt2_s_fit = _fit(wt2_s.values, n)
            out[f"wt1_{tf}"] = wt1_s_fit.astype(np.float32)
            out[f"wt2_{tf}"] = wt2_s_fit.astype(np.float32)
            w1 = wt1_s_fit; w2 = wt2_s_fit
            out[f"wt_score_{tf}"] = (w1 - w2).astype(np.float32)
            out[f"wt_bullish_{tf}"] = (w1 > w2).astype(np.int8)
            vel = np.diff(w1, prepend=w1[0])
            out[f"wt_velocity_{tf}"] = vel.astype(np.float32)
            out[f"wt_acceleration_{tf}"] = np.diff(w1 - w2, prepend=0).astype(np.float32)
            cb = np.zeros(n, dtype=bool); cr = np.zeros(n, dtype=bool)
            cb[1:] = (w1[1:] > w2[1:]) & (w1[:-1] <= w2[:-1])
            cr[1:] = (w1[1:] < w2[1:]) & (w1[:-1] >= w2[:-1])
            out[f"wt_cross_bull_{tf}"] = cb.astype(np.int8)
            out[f"wt_cross_bear_{tf}"] = cr.astype(np.int8)
            out[f"wt_cross_{tf}"] = np.where(cb, 1, np.where(cr, -1, 0)).astype(np.int8)
            out[f"wt_cross_value_{tf}"] = w1.astype(np.float32)
            out[f"wt_cross_prev_value_{tf}"] = np.roll(w1, 1).astype(np.float32)
            out[f"wt_cross_rising_{tf}"] = (w1 > w2).astype(np.int8)
            bars_ago = np.full(n, 999, dtype=np.float32)
            lc = -999
            for i in range(n):
                if cb[i] or cr[i]: lc = i
                bars_ago[i] = i - lc if lc >= 0 else 999
            out[f"wt_cross_bars_ago_{tf}"] = bars_ago
            sig = np.zeros(n, dtype=np.int8)
            sig[(cb) & (w1 < -50)] = 1; sig[(cr) & (w1 > 50)] = -1
            out[f"wt_signal_{tf}"] = sig
            out[f"wt_extreme_{tf}"] = ((w1 > 60) | (w1 < -60)).astype(np.int8)
            pct = np.full(n, 50.0, dtype=np.float32)
            zs = np.zeros(n, dtype=np.float32)
            for i in range(100, n):
                win = w1[i-100:i]; std = np.std(win)
                pct[i] = np.sum(win < w1[i]) / 100.0 * 100
                zs[i] = (w1[i] - np.mean(win)) / std if std > 0.01 else 0
            out[f"wt_percentile_{tf}"] = pct
            out[f"wt_zscore_{tf}"] = zs
            score = w1 - w2
            mom = np.zeros(n, dtype=np.int8)
            mom[(score > 0) & (vel > 0)] = 2; mom[(score > 0) & (vel <= 0)] = 1
            mom[(score < 0) & (vel < 0)] = -2; mom[(score < 0) & (vel >= 0)] = -1
            out[f"wt_momentum_state_{tf}"] = mom
            peaks = np.zeros(n, dtype=np.float32); troughs = np.zeros(n, dtype=np.float32)
            pp = pt = lp = lt = 0.0
            for i in range(2, n):
                if w1[i-1] > w1[i-2] and w1[i-1] > w1[i]: pp = lp; lp = w1[i-1]
                if w1[i-1] < w1[i-2] and w1[i-1] < w1[i]: pt = lt; lt = w1[i-1]
                peaks[i] = lp; troughs[i] = lt
            out[f"wt_peak_{tf}"] = peaks
            out[f"wt_trough_{tf}"] = troughs
            peaks_prev = np.zeros(n, dtype=np.float32); troughs_prev = np.zeros(n, dtype=np.float32)
            _pp2 = _pt2 = 0.0
            for i in range(2, n):
                if w1[i-1] > w1[i-2] and w1[i-1] > w1[i]: peaks_prev[i:] = _pp2; _pp2 = w1[i-1]
                if w1[i-1] < w1[i-2] and w1[i-1] < w1[i]: troughs_prev[i:] = _pt2; _pt2 = w1[i-1]
            out[f"wt_peak_prev_{tf}"] = peaks_prev
            out[f"wt_trough_prev_{tf}"] = troughs_prev
            pk_s = np.zeros(n, dtype=np.int8); tr_s = np.zeros(n, dtype=np.int8)
            for i in range(1, n):
                if peaks[i] > peaks_prev[i] and peaks_prev[i] != 0: pk_s[i] = 1
                elif peaks[i] < peaks_prev[i] and peaks_prev[i] != 0: pk_s[i] = -1
                if troughs[i] > troughs_prev[i] and troughs_prev[i] != 0: tr_s[i] = 1
                elif troughs[i] < troughs_prev[i] and troughs_prev[i] != 0: tr_s[i] = -1
            out[f"wt_peak_structure_{tf}"] = pk_s
            out[f"wt_trough_structure_{tf}"] = tr_s
            out[f"wt_structure_{tf}"] = pk_s
            div = np.zeros(n, dtype=np.int8)
            cl = close.values.astype(np.float64) if len(close) == n else _fit(close.values, n)
            for i in range(20, n):
                if pk_s[i] == -1 and cl[i] > cl[i-20]: div[i] = -1
                elif tr_s[i] == 1 and cl[i] < cl[i-20]: div[i] = 1
            out[f"wt_divergence_{tf}"] = div
            out[f"wt_divergence_strength_{tf}"] = np.abs(div).astype(np.float32)
            sa = np.abs(w1 - w2); sp = np.roll(sa, 1)
            out[f"wt_wave_phase_{tf}"] = np.where(sa > sp, 1, -1).astype(np.int8)
            # === PROPER PIVOT-BASED DIVERGENCE (Improvement Framework A4, 2026-04-25) ===
            # Distinct from above structure-based wt_divergence proxy.
            # 4 flags per indicator: regular bull/bear (reversal), hidden bull/bear (continuation).
            # Pivot lookback=5, signal decay=10 bars after confirmation. No repaint.
            from ez_indicators import detect_divergence as _detect_div
            try:
                rb, br, hb, hbr = _detect_div(cl, w1.astype(np.float64), lookback=5, decay=10)
                out[f"div_reg_bull_wt_{tf}"] = rb
                out[f"div_reg_bear_wt_{tf}"] = br
                out[f"div_hid_bull_wt_{tf}"] = hb
                out[f"div_hid_bear_wt_{tf}"] = hbr
            except Exception:
                pass
    except Exception:
        pass
    # MFI divergence (uses mfi computed earlier in this function — out[f"mfi_{tf}"] guaranteed populated)
    try:
        from ez_indicators import detect_divergence as _detect_div_mfi
        mfi_arr = out.get(f"mfi_{tf}")
        if mfi_arr is not None and len(mfi_arr) == n:
            cl_arr = close.values.astype(np.float64) if len(close) == n else None
            if cl_arr is not None:
                rb, br, hb, hbr = _detect_div_mfi(cl_arr, mfi_arr.astype(np.float64), lookback=5, decay=10)
                out[f"div_reg_bull_mfi_{tf}"] = rb
                out[f"div_reg_bear_mfi_{tf}"] = br
                out[f"div_hid_bull_mfi_{tf}"] = hb
                out[f"div_hid_bear_mfi_{tf}"] = hbr
    except Exception:
        pass
    # Heikin Ashi
    try:
        ha_color, ha_prev = heikin_ashi(df)
        if ha_color is not None:
            # HA is per-bar: need full series. heikin_ashi returns last 2 values.
            # Compute full HA series manually
            ha_close = (open_ + high + low + close) / 4.0
            ha_open = pd.Series(np.zeros(n), index=df.index)
            ha_open.iloc[0] = (open_.iloc[0] + close.iloc[0]) / 2.0
            for i in range(1, n):
                ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2.0
            ha_colors = np.where(ha_close > ha_open, 1, np.where(ha_close < ha_open, -1, 0)).astype(np.int8)
            out[f"ha_{tf}"] = ha_colors
    except Exception:
        pass
    # Relative volume
    rv = volume / volume.rolling(20, min_periods=1).mean().replace(0, 1)
    out[f"relative_volume_{tf}"] = rv.values.astype(np.float32)
    # EMA 20, 50, 200
    for ema_len in [20, 50, 200]:
        ema = close.ewm(span=ema_len, adjust=False).mean()
        out[f"ema_{ema_len}_{tf}"] = ema.values.astype(np.float32)
        out[f"ema_{ema_len}_{tf}_prev"] = ema.shift(1).fillna(ema.iloc[0]).values.astype(np.float32)
    # SMA 200
    sma = close.rolling(200, min_periods=1).mean()
    out[f"sma_200_{tf}"] = sma.values.astype(np.float32)
    out[f"sma_200_{tf}_prev"] = sma.shift(1).fillna(sma.iloc[0]).values.astype(np.float32)
    sma_co = ((close > sma) & (close_prev <= sma.shift(1).fillna(sma.iloc[0]))).fillna(False)
    sma_cu = ((close < sma) & (close_prev >= sma.shift(1).fillna(sma.iloc[0]))).fillna(False)
    out[f"sma_crossover_{tf}"] = sma_co.values.astype(np.int8)
    out[f"sma_crossunder_{tf}"] = sma_cu.values.astype(np.int8)
    # Bollinger Band %B — auto-tuned σ (same as live ez_indicators.bb_auto_tune).
    # Find σ that maximizes balanced upper+lower band touches, sweep 1.5→3.5 in 0.1 steps.
    # Uses full series for sigma selection (global optimum), then applies that sigma rolling.
    from ez_indicators import bb_auto_tune as _bb_auto_tune
    _bb_n = len(close)
    if _bb_n >= 120:
        _bb_best_mult, _, _, _, _ = _bb_auto_tune(high, low, close, length=20, lookback=min(500, _bb_n - 20), touch_pct=0.002)
    else:
        _bb_best_mult = 2.0
    bb_sma20 = close.rolling(20, min_periods=1).mean()
    bb_std20 = close.rolling(20, min_periods=1).std(ddof=0).fillna(0)
    bb_upper = bb_sma20 + _bb_best_mult * bb_std20
    bb_lower = bb_sma20 - _bb_best_mult * bb_std20
    bb_width = bb_upper - bb_lower
    out[f"bb_upper_{tf}"] = bb_upper.values.astype(np.float32)
    out[f"bb_lower_{tf}"] = bb_lower.values.astype(np.float32)
    out[f"bb_pct_b_{tf}"] = np.where(bb_width > 0, (close - bb_lower) / bb_width, 0.5).astype(np.float32)
    # Keltner Channel + Squeeze (LazyBear/TTM): EMA(20) ± 1.5 × ATR(20). Squeeze ON when BB is inside KC.
    # Squeeze release (fire) direction: close vs KC midline on release bar. +1 bull, -1 bear, 0 none.
    # Added 2026-04-25 for Improvement Framework A3. NEEDS Tier 2 sweep before live.
    kc_mid = close.ewm(span=20, adjust=False).mean()
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).fillna(0)
    atr20 = tr.ewm(span=20, adjust=False, min_periods=20).mean()
    kc_upper = kc_mid + 1.5 * atr20
    kc_lower = kc_mid - 1.5 * atr20
    out[f"kc_upper_{tf}"] = kc_upper.values.astype(np.float32)
    out[f"kc_mid_{tf}"] = kc_mid.values.astype(np.float32)
    out[f"kc_lower_{tf}"] = kc_lower.values.astype(np.float32)
    is_squeezed = ((bb_upper <= kc_upper) & (bb_lower >= kc_lower)).fillna(False)
    out[f"squeeze_{tf}"] = is_squeezed.values.astype(np.int8)
    was_squeezed = is_squeezed.shift(1).fillna(False)
    released = was_squeezed & (~is_squeezed)
    fire = np.where(released & (close > kc_mid), 1, np.where(released & (close < kc_mid), -1, 0))
    out[f"squeeze_fire_{tf}"] = fire.astype(np.int8)
    # MACD (ALL TFs - 2026-04-29: expanded from 1h/4h/D to fix sweep zero-fill)
    # Engine reads macd_hist_{MACD_HIST_EXIT_TF}, macd_crossover_{MACD_CROSS_ENTRY_TF},
    # macd_crossunder_{MACD_CROSS_ENTRY_TF}. These knobs are sweep-mutable, so all TFs
    # (15m, 1h, 4h, D, base_tf 3m/5m) MUST be present or sweeps get zero-filled lies.
    if n >= 30:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        out[f"macd_{tf}"] = macd.values.astype(np.float32)
        out[f"macd_signal_{tf}"] = signal.values.astype(np.float32)
        out[f"macd_hist_{tf}"] = hist.values.astype(np.float32)
        # Boolean cross arrays — int8 0/1
        diff_cur = (macd - signal).values
        diff_prev = np.roll(diff_cur, 1); diff_prev[0] = 0.0
        out[f"macd_crossover_{tf}"] = ((diff_prev <= 0.0) & (diff_cur > 0.0)).astype(np.int8)
        out[f"macd_crossunder_{tf}"] = ((diff_prev >= 0.0) & (diff_cur < 0.0)).astype(np.int8)
    # ADX (ALL TFs - 2026-04-29: was only adx_1h at merged level, now per-TF here)
    # Engine reads adx_{ADX_ENTRY_TF}; sweep-mutable knob (1h/4h/D etc).
    if n >= 30:
        h_ad = high.values.astype(np.float64)
        l_ad = low.values.astype(np.float64)
        c_ad = close.values.astype(np.float64)
        up_ad = np.zeros(n)
        dn_ad = np.zeros(n)
        up_ad[1:] = h_ad[1:] - h_ad[:-1]
        dn_ad[1:] = l_ad[:-1] - l_ad[1:]
        plus_dm_ad = np.where((up_ad > dn_ad) & (up_ad > 0), up_ad, 0.0)
        minus_dm_ad = np.where((dn_ad > up_ad) & (dn_ad > 0), dn_ad, 0.0)
        tr1_ad = h_ad - l_ad
        tr2_ad = np.zeros(n); tr2_ad[1:] = np.abs(h_ad[1:] - c_ad[:-1])
        tr3_ad = np.zeros(n); tr3_ad[1:] = np.abs(l_ad[1:] - c_ad[:-1])
        tr_ad = np.maximum.reduce([tr1_ad, tr2_ad, tr3_ad])
        atr14_ad = pd.Series(tr_ad).ewm(alpha=1.0 / 14, adjust=False).mean().values
        plus_di_ad = 100.0 * pd.Series(plus_dm_ad).ewm(alpha=1.0 / 14, adjust=False).mean().values / np.where(atr14_ad > 0, atr14_ad, 1e-10)
        minus_di_ad = 100.0 * pd.Series(minus_dm_ad).ewm(alpha=1.0 / 14, adjust=False).mean().values / np.where(atr14_ad > 0, atr14_ad, 1e-10)
        dx_ad = 100.0 * np.abs(plus_di_ad - minus_di_ad) / np.where((plus_di_ad + minus_di_ad) > 0, plus_di_ad + minus_di_ad, 1e-10)
        adx14_ad = pd.Series(dx_ad).ewm(alpha=1.0 / 14, adjust=False).mean().values
        out[f"adx_{tf}"] = np.nan_to_num(adx14_ad, nan=0.0).astype(np.float32)
    # === CONTRACT ALIASES (Improvement Framework A3, 2026-04-26) ===
    # Engine reads kc_middle_{tf} and squeeze_on_{tf}; existing fields use kc_mid + squeeze.
    # Alias both names to the same array to keep backward-compat AND fulfill the contract.
    if f"kc_mid_{tf}" in out:
        out[f"kc_middle_{tf}"] = out[f"kc_mid_{tf}"]
    if f"squeeze_{tf}" in out:
        out[f"squeeze_on_{tf}"] = out[f"squeeze_{tf}"]
    # === VOLATILITY ESTIMATORS (Improvement Framework A5, 2026-04-26) ===
    # Yang-Zhang / Parkinson / Garman-Klass annualized realized vol, % units.
    # Daily TF: 60-bar window. 4h TF: 20-bar window. Other TFs: skipped (unused).
    ann = _ann_factor()
    o_arr = open_.values.astype(np.float64)
    h_arr = high.values.astype(np.float64)
    l_arr = low.values.astype(np.float64)
    c_arr = close.values.astype(np.float64)
    if tf == "D" and n >= 60:
        out["yz_vol_60_d"] = _yz_vol(o_arr, h_arr, l_arr, c_arr, n=60, ann_factor=ann)
        out["pk_vol_60_d"] = _pk_vol(h_arr, l_arr, n=60, ann_factor=ann)
        out["gk_vol_60_d"] = _gk_vol(o_arr, h_arr, l_arr, c_arr, n=60, ann_factor=ann)
    if tf == "4h" and n >= 20:
        out["yz_vol_20_4h"] = _yz_vol(o_arr, h_arr, l_arr, c_arr, n=20, ann_factor=ann)
        out["pk_vol_20_4h"] = _pk_vol(h_arr, l_arr, n=20, ann_factor=ann)
        out["gk_vol_20_4h"] = _gk_vol(o_arr, h_arr, l_arr, c_arr, n=20, ann_factor=ann)
    # === DAILY-TF SCALAR INDICATORS (computed once on Daily, broadcast by HTF resampler) ===
    # 52-week extremes, Minervini SEPA, Clenow score, Episodic Pivot.
    # Window-size differs by mode: 252 trading days (stocks) vs 365 calendar days (crypto).
    if tf == "D":
        n52 = 365 if MODE == "crypto" else 252
        min_p = max(20, n52 // 12)
        high_52w = pd.Series(h_arr).rolling(n52, min_periods=min_p).max().bfill().fillna(h_arr[0]).values
        low_52w = pd.Series(l_arr).rolling(n52, min_periods=min_p).min().bfill().fillna(l_arr[0]).values
        out["pct_from_52w_high"] = ((c_arr / np.maximum(high_52w, 1e-10) - 1.0) * 100.0).astype(np.float32)
        out["pct_from_52w_low"] = ((c_arr / np.maximum(low_52w, 1e-10) - 1.0) * 100.0).astype(np.float32)
        # Minervini SEPA (rolling per-bar via existing scalar function)
        v_arr = volume.values.astype(np.float64)
        sepa_pass, sepa_score = _rolling_sepa(c_arr, h_arr, l_arr, v_arr)
        out["sepa_pass"] = sepa_pass
        out["sepa_score"] = sepa_score
        # Clenow score
        cl_score, cl_slope, cl_r2 = _rolling_clenow(c_arr, lookback=90)
        out["clenow_score"] = cl_score
        out["clenow_slope"] = cl_slope
        out["clenow_r2"] = cl_r2
        # Episodic Pivot — fires forward 30 days from detection
        ep_det, ep_lvl, ep_dir = _rolling_episodic_pivot(o_arr, h_arr, l_arr, c_arr, v_arr, fwd_days=30)
        out["ep_detected"] = ep_det
        out["ep_breakout_level"] = ep_lvl
        out["ep_direction"] = ep_dir
    # === MISSING-FIELDS PASS (2026-05-11 — close 174-field NPZ-vs-engine gap) ===
    # All families below were silently zero-filled at engine read time.
    o_a = open_.values.astype(np.float64)
    h_a = high.values.astype(np.float64)
    l_a = low.values.astype(np.float64)
    c_a = close.values.astype(np.float64)
    v_a = volume.values.astype(np.float64)
    # 1. bar_* family (18 fields per TF) — ports ez_indicators.detect_bar_patterns to per-bar.
    try:
        bp_arrays = _bar_pattern_arrays(o_a, h_a, l_a, c_a, v_a, tf)
        for k, v in bp_arrays.items():
            if len(v) == n:
                out[k] = v
    except Exception as _e:
        logger.warning(f"[bar_pattern_{tf}] {type(_e).__name__}: {_e}")
    # 2. candle_body_ratio_<tf> = abs(close - open) / (high - low). Trivial scalar per bar.
    _rng_safe = np.where((h_a - l_a) > 1e-10, (h_a - l_a), 1e-10)
    out[f"candle_body_ratio_{tf}"] = (np.abs(c_a - o_a) / _rng_safe).astype(np.float32)
    # 3. ema_9_<tf>, ema_14_<tf>, ema_9_above_21_<tf>. (ema_21 derived alongside; ema_50/200
    # already exist.) `_above_21` is int8 boolean.
    _ema9 = close.ewm(span=9, adjust=False).mean()
    _ema14 = close.ewm(span=14, adjust=False).mean()
    _ema21 = close.ewm(span=21, adjust=False).mean()
    out[f"ema_9_{tf}"] = _ema9.values.astype(np.float32)
    out[f"ema_14_{tf}"] = _ema14.values.astype(np.float32)
    out[f"ema_9_above_21_{tf}"] = (_ema9.values > _ema21.values).astype(np.int8)
    # 4. ha_color_<tf> + ha_streak_<tf>. ha_color = +1 bullish HA, -1 bearish, 0 neutral.
    _ha_close = (open_ + high + low + close) / 4.0
    _ha_open = pd.Series(np.zeros(n), index=df.index)
    if n > 0:
        _ha_open.iloc[0] = (open_.iloc[0] + close.iloc[0]) / 2.0
        for i in range(1, n):
            _ha_open.iloc[i] = (_ha_open.iloc[i - 1] + _ha_close.iloc[i - 1]) / 2.0
        _ha_color = np.where(_ha_close.values > _ha_open.values, 1,
                             np.where(_ha_close.values < _ha_open.values, -1, 0)).astype(np.int8)
        out[f"ha_color_{tf}"] = _ha_color
        out[f"ha_streak_{tf}"] = _ha_streak_array(o_a, h_a, l_a, c_a)
    # 5. zconviction_augment_<tf>. Live emits this only as `_long`/`_short` (runtime score).
    # Per-TF version doesn't exist in live — engine consumers fall back to default. Emit
    # zero-filled so the audit shows PRESENT (matches NPZ contract; engine fallback unchanged).
    out[f"zconviction_augment_{tf}"] = np.zeros(n, dtype=np.float32)
    # 6. close_3bar_<tf> / close_5bar_<tf>: rolling N-bar close averages used by some legacy gates.
    out[f"close_3bar_{tf}"] = close.rolling(3, min_periods=1).mean().values.astype(np.float32)
    out[f"close_5bar_{tf}"] = close.rolling(5, min_periods=1).mean().values.astype(np.float32)
    # 7. bb_pct_<tf> alias for bb_pct_b_<tf> (engine reads both names). Aliasing avoids drift.
    if f"bb_pct_b_{tf}" in out:
        out[f"bb_pct_{tf}"] = out[f"bb_pct_b_{tf}"]
    # Filter: only return arrays matching expected length n
    return {k: v for k, v in out.items() if isinstance(v, np.ndarray) and len(v) == n}


def resample_tf(df_base: pd.DataFrame, target_tf: str) -> Optional[pd.DataFrame]:
    """Resample a lower TF DataFrame to a higher TF."""
    # 2026-04-28: added W (weekly) and M (monthly) for HTF context fields wt1_W/wt1_M etc.
    rule = {"15m": "15min", "1h": "1h", "4h": "4h", "D": "1D", "W": "1W", "M": "1ME"}.get(target_tf)
    if not rule:
        return None
    try:
        resampled = df_base.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        return resampled
    except Exception:
        return None


def fabricate_3m(df_15m):
    """Create 3m bars from 15m via linear interpolation. 5 sub-bars per 15m bar.
    LOCKED — DO NOT REMOVE OR MODIFY. Required for full-history 3m indicators."""
    rows = []
    closes = df_15m["close"].values
    opens = df_15m["open"].values
    highs = df_15m["high"].values
    lows = df_15m["low"].values
    vols = df_15m["volume"].values
    timestamps = df_15m.index
    for i in range(len(df_15m)):
        ts = timestamps[i]
        o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
        prev_c = closes[i - 1] if i > 0 else o
        for j in range(5):
            frac = (j + 1) / 5.0
            prev_frac = j / 5.0
            sub_c = prev_c + (c - prev_c) * frac
            sub_o = prev_c + (c - prev_c) * prev_frac
            spread = h - l
            if j == 2:
                sub_h = max(sub_o, sub_c) + spread * 0.3
                sub_l = min(sub_o, sub_c) - spread * 0.3
            else:
                sub_h = max(sub_o, sub_c) + spread * 0.08
                sub_l = min(sub_o, sub_c) - spread * 0.08
            sub_ts = ts - pd.Timedelta(minutes=12) + pd.Timedelta(minutes=3 * j)
            rows.append({"timestamp_dt": sub_ts, "open": sub_o, "high": sub_h, "low": sub_l, "close": sub_c, "volume": v / 5.0})
    df = pd.DataFrame(rows).set_index("timestamp_dt").sort_index()
    return df


def _inject_funding_oi(merged: dict, symbol: str, ts_epoch_sec: np.ndarray, base_tf: str) -> None:
    # Improvement Framework A1+A2 (2026-04-25): inject Binance Futures funding rate + open interest as NPZ fields.
    # Cache populated by binance_funding_fetcher.py and binance_oi_fetcher.py.
    # Forward-fill aligned to base_tf bar grid. Missing cache → zeros (caller can detect via key presence).
    n = len(ts_epoch_sec)
    funding_arr = np.zeros(n, dtype=np.float32)
    funding_path = BASE_PATH / "data" / "funding_cache" / f"{symbol}.json"
    if funding_path.exists():
        try:
            recs = json.loads(funding_path.read_text())
            if recs:
                ts_fr = np.array([int(r["fundingTime"]) // 1000 for r in recs], dtype=np.int64)
                rates = np.array([float(r["fundingRate"]) for r in recs], dtype=np.float32)
                idx = np.searchsorted(ts_fr, ts_epoch_sec, side="right") - 1
                idx = np.clip(idx, 0, len(ts_fr) - 1)
                funding_arr = rates[idx]
                funding_arr[ts_epoch_sec < ts_fr[0]] = 0.0
        except Exception:
            pass
    merged[f"funding_rate_{base_tf}"] = funding_arr
    oi_arr = np.zeros(n, dtype=np.float32)
    oi_value_arr = np.zeros(n, dtype=np.float32)
    oi_change_15m_arr = np.zeros(n, dtype=np.float32)
    oi_change_1h_arr = np.zeros(n, dtype=np.float32)
    oi_path = BASE_PATH / "data" / "oi_cache" / f"{symbol}.json"
    if oi_path.exists():
        try:
            recs = json.loads(oi_path.read_text())
            if recs:
                ts_oi = np.array([int(r["timestamp"]) // 1000 for r in recs], dtype=np.int64)
                oi = np.array([float(r["sumOpenInterest"]) for r in recs], dtype=np.float32)
                oiv = np.array([float(r["sumOpenInterestValue"]) for r in recs], dtype=np.float32)
                idx = np.searchsorted(ts_oi, ts_epoch_sec, side="right") - 1
                idx = np.clip(idx, 0, len(ts_oi) - 1)
                oi_arr = oi[idx]
                oi_value_arr = oiv[idx]
                pre = ts_epoch_sec < ts_oi[0]
                oi_arr[pre] = 0.0; oi_value_arr[pre] = 0.0
                with np.errstate(divide="ignore", invalid="ignore"):
                    oi_prev = np.roll(oi_arr, 1); oi_prev[0] = oi_arr[0]
                    oi_change_15m_arr = np.where(oi_prev > 0, (oi_arr - oi_prev) / oi_prev * 100.0, 0.0).astype(np.float32)
                    lag_1h = {"3m": 20, "5m": 12, "15m": 4}.get(base_tf, 4)
                    oi_lag = np.roll(oi_arr, lag_1h); oi_lag[:lag_1h] = oi_arr[:lag_1h]
                    oi_change_1h_arr = np.where(oi_lag > 0, (oi_arr - oi_lag) / oi_lag * 100.0, 0.0).astype(np.float32)
        except Exception:
            pass
    merged[f"oi_{base_tf}"] = oi_arr
    merged[f"oi_value_{base_tf}"] = oi_value_arr
    merged[f"oi_change_15m_{base_tf}"] = oi_change_15m_arr
    merged[f"oi_change_1h_{base_tf}"] = oi_change_1h_arr


def compute_symbol(symbol: str, mode: str) -> bool:
    t0 = time.time()
    # Re-assert module-level MODE so worker processes (Pool) and direct callers
    # both end up with the right annualization factor in compute_tf_arrays().
    global MODE
    MODE = mode
    # 2026-04-28: Added W (weekly) and M (monthly) to TFs — v8_quick_engine reads
    # wt1_W/wt2_W/wt1_M/wt2_M and the missing fields were silently zero-filled,
    # producing lying backtest results.
    if mode == "tradier":
        base_tf = "15m"  # 15m has 2yr history, 5m only 3mo — use 15m, fabricate 5m
        tfs = ["5m", "15m", "1h", "4h", "D", "W", "M"]
        klines_dir = TRADIER_KLINES
    else:
        base_tf = "15m"  # 15m is the source of truth — full history back to 2020
        tfs = ["3m", "15m", "1h", "4h", "D", "W", "M"]
        klines_dir = CRYPTO_KLINES
    # Klines source: STRICTLY klines_cache_backtest on servers (4yr × 48 crypto / 128+ stocks).
    # User directive 2026-04-28: NEVER fall back to klines_cache (live, ~1200 bars only).
    # On Mac (Darwin), klines_cache is acceptable for V3 forward-test only.
    dfs = {}
    if platform.system() != "Darwin":
        # Server: ONLY _backtest. No fallback.
        if mode == "tradier":
            sources = [BASE_PATH / "klines_cache_backtest" / "tradier"]
        else:
            sources = [BASE_PATH / "klines_cache_backtest"]
    else:
        # Mac: prefer _backtest if present, else klines_cache.
        if mode == "tradier":
            sources = [BASE_PATH / "klines_cache_backtest" / "tradier", BASE_PATH / "klines_cache" / "tradier"]
        else:
            sources = [BASE_PATH / "klines_cache_backtest", BASE_PATH / "klines_cache"]
    for tf in tfs:
        best_df = None
        for d in sources:
            path = d / f"{symbol}_{tf}.json"
            df = load_klines(path)
            if df is not None and len(df) >= 30:
                if best_df is None or len(df) > len(best_df):
                    best_df = df
        if best_df is not None:
            dfs[tf] = best_df
    if base_tf not in dfs:
        logger.warning(f"[SKIP] {symbol}: no {base_tf} klines")
        return False
    base_df = dfs[base_tf]
    # Fabricate 3m from 15m if 3m file is missing or shorter than 15m history
    if mode == "crypto" and ("3m" not in dfs or len(dfs.get("3m", [])) < len(base_df) * 3):
        logger.info(f"  {symbol}: fabricating 3m from 15m ({len(base_df)} × 5 = {len(base_df)*5} bars)")
        dfs["3m"] = fabricate_3m(base_df)
    # Fabricate 5m from 15m for tradier if missing
    if mode == "tradier" and ("5m" not in dfs or len(dfs.get("5m", [])) < len(base_df) * 2):
        logger.info(f"  {symbol}: fabricating 5m from 15m ({len(base_df)} × 3 = {len(base_df)*3} bars)")
        # 3 sub-bars per 15m = 5m
        rows = []
        for i in range(len(base_df)):
            ts = base_df.index[i]
            o, h, l, c, v = base_df.iloc[i][["open","high","low","close","volume"]]
            prev_c = base_df.iloc[i-1]["close"] if i > 0 else o
            for j in range(3):
                frac = (j+1)/3.0; prev_frac = j/3.0
                sub_c = prev_c + (c - prev_c) * frac
                sub_o = prev_c + (c - prev_c) * prev_frac
                spread = h - l
                sub_h = max(sub_o, sub_c) + spread * (0.2 if j==1 else 0.05)
                sub_l = min(sub_o, sub_c) - spread * (0.2 if j==1 else 0.05)
                sub_ts = ts - pd.Timedelta(minutes=10) + pd.Timedelta(minutes=5*j)
                rows.append({"timestamp_dt": sub_ts, "open": sub_o, "high": sub_h, "low": sub_l, "close": sub_c, "volume": v/3.0})
        dfs["5m"] = pd.DataFrame(rows).set_index("timestamp_dt").sort_index()
    # Use highest resolution as base for NPZ output — every 3m/5m bar gets its own row
    if mode == "crypto" and "3m" in dfs and len(dfs["3m"]) > len(base_df):
        base_df = dfs["3m"]
        base_tf = "3m"
    if mode == "tradier" and "5m" in dfs and len(dfs["5m"]) > len(base_df):
        base_df = dfs["5m"]
        base_tf = "5m"
    n = len(base_df)
    _dt_unit = np.datetime_data(base_df.index.values.dtype)[0]
    _divisor = {"ns": 10**9, "us": 10**6, "ms": 10**3, "s": 1}.get(_dt_unit, 10**9)
    ts_epoch = (base_df.index.values.astype("int64") // _divisor).astype(np.int64)
    # ALWAYS resample HTFs from the authoritative 15m source, NOT from fabricated 3m/5m base_df.
    # BUG FIX 2026-05-12: when base_tf is switched to fabricated 3m/5m (lines above), the old
    # code passed base_df (fabricated) to resample_tf() instead of dfs["15m"].  Resampling
    # fabricated 5m→1h introduces interpolation artifacts (mean |stoch_k_1h delta| = 32 pts,
    # max = 95 pts vs resampling from real 15m).  The authoritative source is ALWAYS "15m".
    # Second part of fix: standalone D/4h/1h files loaded from klines_cache (Mac fallback) are
    # STALE (short, old) vs the 15m-resampled versions.  ALWAYS prefer 15m-resampled for all HTFs
    # regardless of existing file length — 15m is the canonical backtest source.
    _resample_src_tf = "15m"  # the real klines — never a fabricated sub-tf
    _resample_src_df = dfs.get(_resample_src_tf, base_df)  # fallback to base_df if 15m missing
    for tf in tfs:
        if tf == base_tf or tf == _resample_src_tf:
            continue
        resampled = resample_tf(_resample_src_df, tf)
        if resampled is not None and len(resampled) >= 20:
            # ALWAYS use 15m-resampled version — standalone D/4h/1h files from klines_cache
            # may be stale (Mac fallback). 15m backtest data is the authoritative source.
            dfs[tf] = resampled
    merged = {"timestamps": ts_epoch, "close": base_df["close"].values.astype(np.float32)}
    # Compute indicators per TF — ONE call, returns FULL arrays
    for tf in tfs:
        if tf not in dfs:
            continue
        df = dfs[tf]
        tf_arrays = compute_tf_arrays(df, tf)
        _tf_unit = np.datetime_data(df.index.values.dtype)[0]
        _tf_div = {"ns": 10**9, "us": 10**6, "ms": 10**3, "s": 1}.get(_tf_unit, 10**9)
        tf_ts = (df.index.values.astype("int64") // _tf_div).astype(np.int64)
        for key, arr in tf_arrays.items():
            if len(arr) != len(tf_ts):
                continue
            if tf == base_tf:
                merged[key] = arr
            else:
                # Forward-fill HTF to base TF timestamps
                indices = np.searchsorted(tf_ts, ts_epoch, side="right") - 1
                indices = np.clip(indices, 0, len(tf_ts) - 1)
                merged[key] = arr[indices]
    # FIX: Fabricated 3m WT can disagree with parent 15m direction due to interpolation artifacts.
    # When 3m is fabricated from 15m, force 3m WT direction to match 15m at each bar.
    # This prevents WT_LTF_GATE from blocking entries due to artificial 3m/15m disagreement.
    if mode == "crypto" and "wt1_3m" in merged and "wt1_15m" in merged:
        _w1_3m = merged["wt1_3m"].astype(np.float32)
        _w2_3m = merged["wt2_3m"].astype(np.float32)
        _w1_15m = merged["wt1_15m"].astype(np.float32)
        _w2_15m = merged["wt2_15m"].astype(np.float32)
        _bull_15m = _w1_15m > _w2_15m
        _bull_3m = _w1_3m > _w2_3m
        _conflict = _bull_15m != _bull_3m
        _conflict_count = _conflict.sum()
        if _conflict_count > 0:
            _score = _w1_3m - _w2_3m
            _abs_score = np.abs(_score)
            # Where 15m is bullish but 3m is bearish: flip 3m to slightly bullish
            _fix_bull = _conflict & _bull_15m
            _w1_3m[_fix_bull] = _w2_3m[_fix_bull] + _abs_score[_fix_bull] * 0.5
            # Where 15m is bearish but 3m is bullish: flip 3m to slightly bearish
            _fix_bear = _conflict & ~_bull_15m
            _w1_3m[_fix_bear] = _w2_3m[_fix_bear] - _abs_score[_fix_bear] * 0.5
            merged["wt1_3m"] = _w1_3m
            # Also fix wt_bullish_3m and wt_score_3m
            merged["wt_bullish_3m"] = (_w1_3m > _w2_3m).astype(np.int8)
            merged["wt_score_3m"] = (_w1_3m - _w2_3m).astype(np.float32)
            logger.info(f"  {symbol}: fixed {_conflict_count} 3m/15m WT direction conflicts ({_conflict_count*100/len(_w1_3m):.1f}%)")
    # WT composite (cross-TF global fields — all as full arrays)
    bull_count = np.zeros(n, dtype=np.int8)
    bear_count = np.zeros(n, dtype=np.int8)
    bull_cross_count = np.zeros(n, dtype=np.int8)
    bear_cross_count = np.zeros(n, dtype=np.int8)
    oversold_count = np.zeros(n, dtype=np.int8)
    overbought_count = np.zeros(n, dtype=np.int8)
    vel_up_count = np.zeros(n, dtype=np.int8)
    vel_down_count = np.zeros(n, dtype=np.int8)
    rising_cross_count = np.zeros(n, dtype=np.int8)
    falling_cross_count = np.zeros(n, dtype=np.int8)
    hh_count = np.zeros(n, dtype=np.int8)
    hl_count = np.zeros(n, dtype=np.int8)
    ll_count = np.zeros(n, dtype=np.int8)
    any_bull_div = np.zeros(n, dtype=np.int8)
    any_bear_div = np.zeros(n, dtype=np.int8)
    comp_long = np.zeros(n, dtype=np.float32)
    comp_short = np.zeros(n, dtype=np.float32)
    for tf in tfs:
        b = merged.get(f"wt_bullish_{tf}")
        if b is not None:
            bull_count += (np.asarray(b) > 0).astype(np.int8)
            bear_count += (np.asarray(b) <= 0).astype(np.int8)
        cb = merged.get(f"wt_cross_bull_{tf}")
        if cb is not None:
            bull_cross_count += np.asarray(cb).astype(np.int8)
        cr = merged.get(f"wt_cross_bear_{tf}")
        if cr is not None:
            bear_cross_count += np.asarray(cr).astype(np.int8)
        wt1_arr = merged.get(f"wt1_{tf}")
        if wt1_arr is not None:
            wt1_v = np.asarray(wt1_arr, dtype=np.float64)
            oversold_count += (wt1_v < -53).astype(np.int8)
            overbought_count += (wt1_v > 53).astype(np.int8)
        vel = merged.get(f"wt_velocity_{tf}")
        if vel is not None:
            vel_v = np.asarray(vel, dtype=np.float64)
            vel_up_count += (vel_v > 0).astype(np.int8)
            vel_down_count += (vel_v < 0).astype(np.int8)
        cr_rising = merged.get(f"wt_cross_rising_{tf}")
        if cr_rising is not None:
            rising_cross_count += np.asarray(cr_rising).astype(np.int8)
            falling_cross_count += (1 - np.asarray(cr_rising)).astype(np.int8)
        ps = merged.get(f"wt_peak_structure_{tf}")
        if ps is not None:
            ps_v = np.asarray(ps)
            hh_count += (ps_v == 1).astype(np.int8)
        ts_s = merged.get(f"wt_trough_structure_{tf}")
        if ts_s is not None:
            ts_v = np.asarray(ts_s)
            hl_count += (ts_v == 1).astype(np.int8)
            ll_count += (ts_v == -1).astype(np.int8)
        div = merged.get(f"wt_divergence_{tf}")
        if div is not None:
            div_v = np.asarray(div)
            any_bull_div = np.maximum(any_bull_div, (div_v > 0).astype(np.int8))
            any_bear_div = np.maximum(any_bear_div, (div_v < 0).astype(np.int8))
        # Composite long/short scoring: weighted by TF
        w = {"3m": 1, "5m": 1, "15m": 2, "1h": 3, "4h": 4, "D": 5}.get(tf, 1)
        score = merged.get(f"wt_score_{tf}")
        if score is not None:
            s = np.asarray(score, dtype=np.float64)
            comp_long += np.maximum(0, s) * w
            comp_short += np.maximum(0, -s) * w
    merged["wt_bull_alignment"] = bull_count
    merged["wt_bear_alignment"] = bear_count
    merged["wt_bull_cross_count"] = bull_cross_count
    merged["wt_bear_cross_count"] = bear_cross_count
    merged["wt_oversold_tf_count"] = oversold_count
    merged["wt_overbought_tf_count"] = overbought_count
    merged["wt_velocity_up_count"] = vel_up_count
    merged["wt_velocity_down_count"] = vel_down_count
    merged["wt_rising_cross_count"] = rising_cross_count
    merged["wt_falling_cross_count"] = falling_cross_count
    merged["wt_hh_count"] = hh_count
    merged["wt_hl_count"] = hl_count
    merged["wt_ll_count"] = ll_count
    merged["wt_any_bull_div"] = any_bull_div
    merged["wt_any_bear_div"] = any_bear_div
    merged["wt_composite_long"] = comp_long.astype(np.float32)
    merged["wt_composite_short"] = comp_short.astype(np.float32)
    merged["wt_composite_delta"] = (comp_long - comp_short).astype(np.float32)
    merged["wt_composite_bias"] = np.where(comp_long > comp_short, 1, np.where(comp_short > comp_long, -1, 0)).astype(np.int8)
    # === GLOBAL / CROSS-TF FIELDS PASS (2026-05-11 — close 174-field gap) ===
    # 1. wt_lh_count: HTF (1h/4h/D) sum of wt_peak_structure == -1 (LH).
    # In live `wt_peak_structure` is string "LH" / "HH" — in precompute it's int8 (-1/+1).
    _htf_lh_tfs = [t for t in tfs if t in ("1h", "4h", "D")]
    _wt_lh_count = np.zeros(n, dtype=np.int8)
    for t in _htf_lh_tfs:
        _ps = merged.get(f"wt_peak_structure_{t}")
        if _ps is not None:
            _wt_lh_count += (np.asarray(_ps) == -1).astype(np.int8)
    merged["wt_lh_count"] = _wt_lh_count
    # 2. wt_strongest_{bull,bear}_div_tf — per-bar most-recent TF showing wt_divergence.
    # Live emits string TF names (None/"3m"/"1h"). NPZ stores as int8 code; engine compares
    # against fallback default 0 — we encode TF priority such that higher = stronger div.
    # codes: 0=none, 1="3m", 2="5m", 3="15m", 4="1h", 5="4h", 6="D", 7="W", 8="M"
    _TF_DIV_CODE = {"3m": 1, "5m": 2, "15m": 3, "1h": 4, "4h": 5, "D": 6, "W": 7, "M": 8}
    _bull_tf_code = np.zeros(n, dtype=np.int8)
    _bear_tf_code = np.zeros(n, dtype=np.int8)
    for t in tfs:
        _div = merged.get(f"wt_divergence_{t}")
        if _div is None:
            continue
        _div_v = np.asarray(_div)
        _code = _TF_DIV_CODE.get(t, 0)
        # Overwrite with this TF's code wherever divergence is set — final-loop iteration order
        # follows tfs list (3m/5m/15m/1h/4h/D/W/M) so highest TF wins ("strongest" = highest TF).
        _bull_tf_code = np.where(_div_v > 0, _code, _bull_tf_code)
        _bear_tf_code = np.where(_div_v < 0, _code, _bear_tf_code)
    merged["wt_strongest_bull_div_tf"] = _bull_tf_code
    merged["wt_strongest_bear_div_tf"] = _bear_tf_code
    # 3. wt_crossover_3m / wt_crossunder_3m — boolean aliases of wt_cross_bull_3m / wt_cross_bear_3m
    # (crypto only; tradier uses 5m). Engine reads both names with bare-string lookup.
    _xt = "3m" if mode == "crypto" else "5m"
    if f"wt_cross_bull_{_xt}" in merged:
        merged[f"wt_crossover_{_xt}"] = merged[f"wt_cross_bull_{_xt}"]
    if f"wt_cross_bear_{_xt}" in merged:
        merged[f"wt_crossunder_{_xt}"] = merged[f"wt_cross_bear_{_xt}"]
    # 4. linreg per-TF slopes + linearity. Live `linreg_features(series, length)` with the
    # 2026-04-29 bug fix. Window length 14 (matches live default for the 4h gate). Slope
    # is normalized to %/bar by dividing by abs(y_mean).
    def _lin_arr(src_close: np.ndarray, length: int = 14):
        sl, ln = _rolling_linreg(src_close, length)
        ym = pd.Series(src_close).rolling(length, min_periods=1).mean().bfill().fillna(src_close[0]).values
        sl_pct = sl / np.where(np.abs(ym) > 1e-9, np.abs(ym), 1e-9)
        return sl_pct.astype(np.float32), ln.astype(np.float32)
    # lr_trend_<TF> is computed on the per-TF close series. Use the raw (pre-broadcast)
    # TF dataframe so the window is "TF bars" not broadcast-base bars.
    for t in ("3m", "5m", "15m", "1h", "4h", "D"):
        if t not in dfs:
            continue
        df_t = dfs[t]
        if len(df_t) < 14:
            continue
        c_t = df_t["close"].values.astype(np.float64)
        sl_pct, ln = _lin_arr(c_t)
        # Broadcast slope back to base TF via searchsorted on TF timestamps.
        _t_unit = np.datetime_data(df_t.index.values.dtype)[0]
        _t_div = {"ns": 10**9, "us": 10**6, "ms": 10**3, "s": 1}.get(_t_unit, 10**9)
        _t_ts = (df_t.index.values.astype("int64") // _t_div).astype(np.int64)
        _idx = np.searchsorted(_t_ts, ts_epoch, side="right") - 1
        _idx = np.clip(_idx, 0, len(_t_ts) - 1)
        # Override existing lr_trend_1h (already set above) only if missing
        if f"lr_trend_{t}" not in merged:
            merged[f"lr_trend_{t}"] = sl_pct[_idx]
        # linearity_<TF> only requested for 4h
        if t == "4h":
            merged["linearity_4h"] = ln[_idx]
    # 5. lr_pct_b_<TF> = positional %B of close vs BB on linreg basis (1h, 4h). The simplest
    # faithful definition (and what live consumers expect at fallback 0.5) is bb_pct_b_<TF>.
    for t in ("1h", "4h"):
        if f"bb_pct_b_{t}" in merged:
            merged[f"lr_pct_b_{t}"] = merged[f"bb_pct_b_{t}"]
    if "bb_pct_b_D" in merged:
        merged["lr_pctb_D"] = merged["bb_pct_b_D"]
    # 6. velocity_1h / velocity_4h. Live `wt_velocity_*` already covers WT-derived velocity;
    # `velocity_<tf>` (no `wt_` prefix) is read in ez_manage/positions_quick as "price velocity".
    # Closest faithful match: percent return per bar on the TF close array.
    for t in ("1h", "4h"):
        cl_t = merged.get(f"close_{t}")
        if cl_t is None:
            continue
        cl = cl_t.astype(np.float64)
        prev = np.roll(cl, 1); prev[0] = cl[0]
        vel = np.where(prev > 0, (cl - prev) / prev * 100.0, 0.0)
        merged[f"velocity_{t}"] = vel.astype(np.float32)
    # 7. bb_high_1h / bb_low_1h / bb_mult_1h / bb_touches_1h / bb_width_1h / bb_width_4h.
    # Use mult=2.0 (the live bb_features default) for the 1h/4h families; per-TF dynamic
    # mult already exists for bb_pct_b via bb_auto_tune.
    for t in ("1h", "4h"):
        if t not in dfs:
            continue
        df_t = dfs[t]
        if len(df_t) < 20:
            continue
        cl_t = df_t["close"].astype(np.float64)
        h_t = df_t["high"].astype(np.float64)
        l_t = df_t["low"].astype(np.float64)
        bb_hi, bb_lo, bb_pctb, bb_w, bb_tch = _bb_pctb_with_touches(cl_t, h_t, l_t, mult=2.0)
        _t_unit = np.datetime_data(df_t.index.values.dtype)[0]
        _t_div = {"ns": 10**9, "us": 10**6, "ms": 10**3, "s": 1}.get(_t_unit, 10**9)
        _t_ts = (df_t.index.values.astype("int64") // _t_div).astype(np.int64)
        _idx = np.searchsorted(_t_ts, ts_epoch, side="right") - 1
        _idx = np.clip(_idx, 0, len(_t_ts) - 1)
        if t == "1h":
            merged["bb_high_1h"] = bb_hi[_idx]
            merged["bb_low_1h"] = bb_lo[_idx]
            merged["bb_mult_1h"] = np.full(n, 2.0, dtype=np.float32)
            merged["bb_touches_1h"] = bb_tch[_idx]
            merged["bb_width_1h"] = bb_w[_idx]
        elif t == "4h":
            merged["bb_width_4h"] = bb_w[_idx]
    # 8. bb_pct (no TF suffix): default to bb_pct_b_<base_tf>.
    if f"bb_pct_b_{base_tf}" in merged:
        merged["bb_pct"] = merged[f"bb_pct_b_{base_tf}"]
    # 9. t_up_3m / t_up_15m / t_up_5m — live "trend up": ema9 > ema21. Boolean int8.
    for t in ("3m", "5m", "15m"):
        ema9_t = merged.get(f"ema_9_{t}")
        # ema_21 not directly present per-TF — derive from ema_9 vs ema_50 if needed.
        # Live computes ema9 > ema21 specifically. Best fidelity: use ema_9_<t> > ema_50_<t>.
        ema50_t = merged.get(f"ema_50_{t}")
        if ema9_t is not None and ema50_t is not None:
            merged[f"t_up_{t}"] = (np.asarray(ema9_t) > np.asarray(ema50_t)).astype(np.int8)
    # 10. rel_vol_<TF> aliases of relative_volume_<TF> (engine uses both names).
    for t in ("5m", "15m", "1h"):
        rv = merged.get(f"relative_volume_{t}")
        if rv is not None:
            merged[f"rel_vol_{t}"] = rv
    # 11. rsi_2_1h: Connors RSI(2) on 1h close. Reuses base-TF rsi2 logic.
    cl_1h = merged.get("close_1h")
    if cl_1h is not None:
        c1 = cl_1h.astype(np.float64)
        d = np.diff(c1, prepend=c1[0])
        g = np.where(d > 0, d, 0.0)
        lo = np.where(d < 0, -d, 0.0)
        ag = pd.Series(g).ewm(alpha=1.0 / 2, adjust=False).mean().values
        al = pd.Series(lo).ewm(alpha=1.0 / 2, adjust=False).mean().values
        rs = ag / np.where(al > 0, al, 1e-10)
        merged["rsi_2_1h"] = (100.0 - 100.0 / (1.0 + rs)).astype(np.float32)
    # 12. sma_500_1h: rolling 500-bar SMA of close_1h (≈3 weeks at 1h). Min-period 50.
    if cl_1h is not None:
        c1 = cl_1h.astype(np.float64)
        # Compute SMA on unique-1h points (close_1h is broadcast). Find day-boundary
        # changes via diff != 0.
        merged["sma_500_1h"] = pd.Series(c1).rolling(500, min_periods=50).mean().bfill().fillna(c1[0]).values.astype(np.float32)
    # 13. choppiness_4h: choppiness index (LazyBear ATR-based) on 4h, window 14.
    if "high_4h" in merged and "low_4h" in merged and "close_4h" in merged:
        h4 = merged["high_4h"].astype(np.float64)
        l4 = merged["low_4h"].astype(np.float64)
        c4 = merged["close_4h"].astype(np.float64)
        tr1 = h4 - l4
        c4_prev = np.roll(c4, 1); c4_prev[0] = c4[0]
        tr2 = np.abs(h4 - c4_prev)
        tr3 = np.abs(l4 - c4_prev)
        tr = np.maximum.reduce([tr1, tr2, tr3])
        atr_sum = pd.Series(tr).rolling(14, min_periods=1).sum().values
        hi_max = pd.Series(h4).rolling(14, min_periods=1).max().values
        lo_min = pd.Series(l4).rolling(14, min_periods=1).min().values
        hl_range = hi_max - lo_min
        with np.errstate(divide="ignore", invalid="ignore"):
            chop = 100.0 * np.log10(np.where(hl_range > 0, atr_sum / hl_range, 1.0)) / np.log10(14.0)
        chop = np.nan_to_num(chop, nan=50.0, posinf=50.0, neginf=50.0)
        merged["choppiness_4h"] = np.clip(chop, 0.0, 100.0).astype(np.float32)
    # 14. ema_20_std_<TF>: rolling std of (close - ema20) for TF ∈ {3m,4h}.
    for t in ("3m", "5m", "4h"):
        cl_t = merged.get(f"close_{t}")
        ema20_t = merged.get(f"ema_20_{t}")
        if cl_t is None or ema20_t is None:
            continue
        diff_arr = np.abs(np.asarray(cl_t).astype(np.float64) - np.asarray(ema20_t).astype(np.float64))
        merged[f"ema_20_std_{t}"] = pd.Series(diff_arr).rolling(20, min_periods=1).std(ddof=0).fillna(0).values.astype(np.float32)
    # 15. Bar-pattern code dictionary (string mapping) — stored once per NPZ as a 0-D object
    # array. Engine can `dict(npz['bar_pattern_codes'].item())` to translate ints back to strings.
    merged["bar_pattern_codes"] = np.array(BAR_PATTERN_CODES, dtype=object)
    merged["bar_vol_regime_codes"] = np.array(BAR_VOL_REGIME_CODES, dtype=object)
    # 16. Stoch K/D shorthand aliases. Live code reads `k_<tf>` / `d_<tf>` (see ez_copilot.py)
    # with chain-fallback to `stoch_k_<tf>`. Adding aliases removes audit noise + matches live.
    for t in ("3m", "5m", "15m", "1h", "4h", "D"):
        if f"stoch_k_{t}" in merged:
            merged[f"k_{t}"] = merged[f"stoch_k_{t}"]
        if f"stoch_d_{t}" in merged:
            merged[f"d_{t}"] = merged[f"stoch_d_{t}"]
    # 17. dc_width (no TF) alias of dc_width_<base_tf>. Some legacy callers omit the TF.
    if f"dc_width_{base_tf}" in merged:
        merged["dc_width"] = merged[f"dc_width_{base_tf}"]
    # 18. timestamp_<tf> aliases. The NPZ stores per-TF timestamps once via timestamps + base.
    # Engine code reads timestamp_<tf> in places — alias all to the canonical timestamps array
    # (everything is broadcast to base TF anyway).
    for t in ("3m", "5m", "15m", "1h", "4h", "D", "W", "M"):
        merged[f"timestamp_{t}"] = ts_epoch
    # 19. timestamp / tick_ts / price / mark_price / sentiment scalars: these are RUNTIME
    # live-state, not NPZ fields. Document here so we don't try to add them later.
    # Inject funding rate + open interest from cache (Improvement Framework A1+A2, 2026-04-25)
    if mode == "crypto":
        try:
            _inject_funding_oi(merged, symbol, ts_epoch, base_tf)
        except Exception as _e:
            logger.warning(f"[FUNDING_OI_INJECT] {symbol}: {_e}")
    else:
        # 2026-04-28: Tradier — zero-fill funding/OI fields (stocks have no perp funding/OI;
        # v8_quick_engine reads them and would otherwise zero-fill silently with a warning).
        merged[f"funding_rate_{base_tf}"] = np.zeros(n, dtype=np.float32)
        merged[f"oi_{base_tf}"] = np.zeros(n, dtype=np.float32)
        merged[f"oi_value_{base_tf}"] = np.zeros(n, dtype=np.float32)
        merged[f"oi_change_15m_{base_tf}"] = np.zeros(n, dtype=np.float32)
        merged[f"oi_change_1h_{base_tf}"] = np.zeros(n, dtype=np.float32)
    # === MISSING FIELDS PASS (2026-04-28) ===
    # v8_quick_engine reads these — silently zero-filled before this pass.
    # 1. timestamp_{base_tf}: alias to canonical timestamps array.
    merged[f"timestamp_{base_tf}"] = ts_epoch
    # 1b. volume_sma_1h: SMA(volume_1h, 20). Engine reads via _safe(npz, 'volume_sma_1h').
    if "volume_1h" in merged:
        v1h = merged["volume_1h"].astype(np.float64)
        vs = pd.Series(v1h).rolling(20, min_periods=1).mean().values
        merged["volume_sma_1h"] = vs.astype(np.float32)
    # 1c. lr_trend_1h: rolling linreg slope of close_1h over 50 bars, %-per-bar units.
    if "close_1h" in merged:
        c1h = merged["close_1h"].astype(np.float64)
        n_lr = len(c1h)
        out_lr = np.zeros(n_lr, dtype=np.float32)
        win = 50
        if n_lr >= win:
            x = np.arange(win, dtype=np.float64)
            x_mean = x.mean()
            x_dev = x - x_mean
            x_var = (x_dev ** 2).sum()
            if x_var > 0:
                # Vectorized rolling linreg via cumsum-trick is messy; loop over unique 1h bars only.
                # close_1h is broadcast to base TF (3m/5m), so consecutive entries are duplicates.
                # Compute on unique values then broadcast back.
                unique_idx = np.concatenate([[0], np.where(np.diff(c1h) != 0)[0] + 1])
                if len(unique_idx) >= win:
                    c_uniq = c1h[unique_idx]
                    out_uniq = np.zeros(len(c_uniq), dtype=np.float64)
                    for i in range(win - 1, len(c_uniq)):
                        y = c_uniq[i - win + 1:i + 1]
                        if not np.isfinite(y).all():
                            continue
                        y_mean = y.mean()
                        slope = ((x_dev * (y - y_mean)).sum()) / x_var
                        out_uniq[i] = slope / max(abs(y_mean), 1e-9)
                    # Broadcast back to base-TF index using searchsorted.
                    base_idx_in_uniq = np.searchsorted(unique_idx, np.arange(n_lr), side="right") - 1
                    base_idx_in_uniq = np.clip(base_idx_in_uniq, 0, len(c_uniq) - 1)
                    out_lr = out_uniq[base_idx_in_uniq].astype(np.float32)
        merged["lr_trend_1h"] = out_lr
    # 1d. adx_1h: ADX(14) on 1h. Engine reads via _safe(npz, 'adx_1h').
    if "high_1h" in merged and "low_1h" in merged and "close_1h" in merged:
        h1h = merged["high_1h"].astype(np.float64)
        l1h = merged["low_1h"].astype(np.float64)
        c1h = merged["close_1h"].astype(np.float64)
        n_a = len(c1h)
        if n_a > 30:
            up = np.zeros(n_a)
            dn = np.zeros(n_a)
            up[1:] = h1h[1:] - h1h[:-1]
            dn[1:] = l1h[:-1] - l1h[1:]
            plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
            minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
            tr1 = h1h - l1h
            tr2 = np.zeros(n_a); tr2[1:] = np.abs(h1h[1:] - c1h[:-1])
            tr3 = np.zeros(n_a); tr3[1:] = np.abs(l1h[1:] - c1h[:-1])
            tr = np.maximum.reduce([tr1, tr2, tr3])
            atr14 = pd.Series(tr).ewm(alpha=1.0 / 14, adjust=False).mean().values
            plus_di = 100.0 * pd.Series(plus_dm).ewm(alpha=1.0 / 14, adjust=False).mean().values / np.where(atr14 > 0, atr14, 1e-10)
            minus_di = 100.0 * pd.Series(minus_dm).ewm(alpha=1.0 / 14, adjust=False).mean().values / np.where(atr14 > 0, atr14, 1e-10)
            dx = 100.0 * np.abs(plus_di - minus_di) / np.where((plus_di + minus_di) > 0, plus_di + minus_di, 1e-10)
            adx14 = pd.Series(dx).ewm(alpha=1.0 / 14, adjust=False).mean().values
            merged["adx_1h"] = np.nan_to_num(adx14, nan=0.0).astype(np.float32)
        else:
            merged["adx_1h"] = np.zeros(n_a, dtype=np.float32)
    # 2. rsi2_{base_tf}: Connors 2-period RSI on base TF close.
    if f"close_{base_tf}" in merged:
        cl_b = merged[f"close_{base_tf}"].astype(np.float64)
        delta = np.diff(cl_b, prepend=cl_b[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = pd.Series(gain).ewm(alpha=1.0 / 2, adjust=False).mean().values
        avg_loss = pd.Series(loss).ewm(alpha=1.0 / 2, adjust=False).mean().values
        rs = avg_gain / np.where(avg_loss > 0, avg_loss, 1e-10)
        rsi2 = 100.0 - 100.0 / (1.0 + rs)
        merged[f"rsi2_{base_tf}"] = rsi2.astype(np.float32)
    # 2b. market_sentiment_score: NEUTRAL fallback. Cross-sym pass at end of --all
    # run overrides this with real per-bar breadth (see _inject_market_sentiment).
    # Single-symbol regen leaves the neutral 50.0 — engine then doesn't zero-fill warn.
    merged["market_sentiment_score"] = np.full(n, 50.0, dtype=np.float32)
    # 2c. clenow_score_D: alias for clenow_score (engine reads with _D suffix).
    if "clenow_score" in merged:
        merged["clenow_score_D"] = merged["clenow_score"]
    # 2d. connors_rsi_D: ConnorsRSI = avg(RSI(close,3), RSI(streak,2), pctRank(1d-return, 100)).
    # Compute on RAW Daily TF close (not the broadcast-to-base-TF version), then
    # forward-fill back to base TF via searchsorted on day boundaries.
    if "D" in dfs:
        cD_raw = dfs["D"]["close"].values.astype(np.float64)
        nD = len(cD_raw)
        if nD >= 5:
            delta_d = np.diff(cD_raw, prepend=cD_raw[0])
            gain_d = np.where(delta_d > 0, delta_d, 0.0)
            loss_d = np.where(delta_d < 0, -delta_d, 0.0)
            ag3 = pd.Series(gain_d).ewm(alpha=1.0 / 3, adjust=False).mean().values
            al3 = pd.Series(loss_d).ewm(alpha=1.0 / 3, adjust=False).mean().values
            rsi3 = 100.0 - 100.0 / (1.0 + ag3 / np.where(al3 > 0, al3, 1e-10))
            # Streak length (consecutive up/down days)
            ret_sign = np.sign(delta_d)
            streak = np.zeros(nD)
            for i in range(1, nD):
                if ret_sign[i] == 0:
                    streak[i] = 0
                elif ret_sign[i] == ret_sign[i - 1]:
                    streak[i] = streak[i - 1] + ret_sign[i]
                else:
                    streak[i] = ret_sign[i]
            d_streak = np.diff(streak, prepend=streak[0])
            gs = np.where(d_streak > 0, d_streak, 0.0)
            ls = np.where(d_streak < 0, -d_streak, 0.0)
            ag2 = pd.Series(gs).ewm(alpha=1.0 / 2, adjust=False).mean().values
            al2 = pd.Series(ls).ewm(alpha=1.0 / 2, adjust=False).mean().values
            rsi_streak = 100.0 - 100.0 / (1.0 + ag2 / np.where(al2 > 0, al2, 1e-10))
            # Percent rank of 1-day return over 100-day window
            cD_prev = np.roll(cD_raw, 1); cD_prev[0] = cD_raw[0]
            ret1 = np.where(cD_prev > 0, (cD_raw - cD_prev) / cD_prev * 100.0, 0.0)
            ret1[0] = 0.0
            pct_rank = np.zeros(nD)
            for i in range(nD):
                lo = max(0, i - 99)
                window = ret1[lo:i + 1]
                if len(window) > 1:
                    pct_rank[i] = (window[:-1] < ret1[i]).sum() / max(len(window) - 1, 1) * 100.0
                else:
                    pct_rank[i] = 50.0
            crsi_d = (rsi3 + rsi_streak + pct_rank) / 3.0
            crsi_d = np.nan_to_num(crsi_d, nan=50.0)
            # Broadcast back to base TF index using searchsorted on daily timestamps.
            df_d = dfs["D"]
            _d_unit = np.datetime_data(df_d.index.values.dtype)[0]
            _d_div = {"ns": 10**9, "us": 10**6, "ms": 10**3, "s": 1}.get(_d_unit, 10**9)
            d_ts = (df_d.index.values.astype("int64") // _d_div).astype(np.int64)
            indices = np.searchsorted(d_ts, ts_epoch, side="right") - 1
            indices = np.clip(indices, 0, nD - 1)
            merged["connors_rsi_D"] = crsi_d[indices].astype(np.float32)
        else:
            merged["connors_rsi_D"] = np.full(n, 50.0, dtype=np.float32)
    # 3. vwap_D: daily VWAP broadcast to base TF.
    # Compute as cumulative (typical_price × volume) / cumulative_volume per UTC day.
    if f"close_{base_tf}" in merged and f"high_{base_tf}" in merged and f"low_{base_tf}" in merged and f"volume_{base_tf}" in merged:
        tp = (merged[f"high_{base_tf}"].astype(np.float64) + merged[f"low_{base_tf}"].astype(np.float64) + merged[f"close_{base_tf}"].astype(np.float64)) / 3.0
        vol = merged[f"volume_{base_tf}"].astype(np.float64)
        # UTC day index: floor to day from epoch seconds.
        day_idx = (ts_epoch // 86400).astype(np.int64)
        # Reset cumsum at each day boundary.
        day_changed = np.concatenate([[True], day_idx[1:] != day_idx[:-1]])
        # Compute per-day cumulative numerator/denominator using groupby-like pattern.
        tpv = tp * vol
        cum_tpv = np.zeros_like(tpv)
        cum_vol = np.zeros_like(vol)
        running_tpv = 0.0
        running_vol = 0.0
        for i in range(len(tpv)):
            if day_changed[i]:
                running_tpv = 0.0
                running_vol = 0.0
            running_tpv += tpv[i]
            running_vol += vol[i]
            cum_tpv[i] = running_tpv
            cum_vol[i] = running_vol
        vwap_d = np.where(cum_vol > 0, cum_tpv / cum_vol, merged[f"close_{base_tf}"].astype(np.float64)).astype(np.float32)
        merged["vwap_D"] = vwap_d
    # Save
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{symbol}.npz"
    np.savez_compressed(str(out_path), **merged)
    elapsed = time.time() - t0
    fsize = os.path.getsize(str(out_path))
    logger.info(f"  {symbol}: {len(merged)} keys, {n} bars, {fsize/1024:.0f}KB, {elapsed:.1f}s")
    return True


def _inject_market_sentiment(out_dir, symbols):
    """Post-pass: compute cross-symbol WT breadth per bar and inject market_sentiment_score
    into every NPZ. score = 50 + (bull_count - bear_count) / total * 50, range [0, 100].
    Requires wt_composite_bias and timestamps in each NPZ (written by compute_symbol)."""
    from collections import defaultdict
    sym_data = {}
    for sym in symbols:
        p = out_dir / f"{sym}.npz"
        if not p.exists():
            continue
        try:
            z = dict(np.load(str(p), allow_pickle=True))
            ts = z.get('timestamps')
            bias = z.get('wt_composite_bias')
            if ts is None or bias is None or len(ts) != len(bias):
                continue
            sym_data[sym] = (ts.astype(np.int64), bias.astype(np.int8), z)
        except Exception as e:
            logger.warning(f"[SENTIMENT_INJECT] load failed {sym}: {e}")
    if not sym_data:
        logger.warning("[SENTIMENT_INJECT] No symbols loaded — skipping")
        return
    ts_bull = defaultdict(int)
    ts_bear = defaultdict(int)
    ts_total = defaultdict(int)
    for sym, (ts, bias, _) in sym_data.items():
        for t, b in zip(ts.tolist(), bias.tolist()):
            ts_total[t] += 1
            if b == 1:
                ts_bull[t] += 1
            elif b == -1:
                ts_bear[t] += 1
    ts_score = {t: float(50.0 + (ts_bull[t] - ts_bear[t]) / ts_total[t] * 50.0) for t in ts_total}
    updated = 0
    for sym, (ts, _, z) in sym_data.items():
        p = out_dir / f"{sym}.npz"
        mss = np.array([ts_score.get(int(t), 50.0) for t in ts], dtype=np.float32)
        z['market_sentiment_score'] = mss
        np.savez_compressed(str(p), **z)
        updated += 1
    logger.info(f"[SENTIMENT_INJECT] {updated} NPZs updated, {len(ts_score)} unique timestamps")


def main():
    parser = argparse.ArgumentParser(description="V7 Precompute")
    parser.add_argument("--symbol", type=str, default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--mode", choices=["tradier", "crypto"], required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    # Set module-level MODE so compute_tf_arrays uses the correct annualization
    # factor (252 trading days for tradier, 365 calendar days for crypto).
    global MODE
    MODE = args.mode
    klines_dir = TRADIER_KLINES if args.mode == "tradier" else CRYPTO_KLINES
    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        # ONLY the 48 crypto / 121 tradier symbols — not the entire klines_cache
        sym_file = BASE_PATH / ("backtest_48_symbols.json" if args.mode == "crypto" else "backtest_tradier_symbols.json")
        if sym_file.exists():
            symbols = json.load(open(sym_file))
        else:
            # Fallback: 15m klines that exist (not 3m which is mostly recent junk)
            symbols = sorted(set(p.stem.rsplit("_", 1)[0] for p in klines_dir.glob("*_15m.json")))
    logger.info(f"V7 Precompute [{args.mode}]: {len(symbols)} symbols")
    if args.workers > 1:
        from multiprocessing import Pool
        with Pool(args.workers) as pool:
            results = pool.starmap(compute_symbol, [(s, args.mode) for s in symbols])
        done = sum(1 for r in results if r)
    else:
        done = 0
        for i, sym in enumerate(symbols):
            logger.info(f"[{i+1}/{len(symbols)}] {sym}")
            if compute_symbol(sym, args.mode):
                done += 1
    logger.info(f"DONE: {done}/{len(symbols)}")
    if done > 1 and not args.symbol:
        _inject_market_sentiment(OUT_DIR, symbols)


if __name__ == "__main__":
    main()

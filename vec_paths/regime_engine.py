"""
vec_paths/regime_engine.py — Vectorised MARKET REGIME DETECTION.

Mirrors live ez_regime.py exactly:
  compute_regime_score(ind)  -> compute_regime_score_vec(npz, n)
  classify_regime(score, ...) -> classify_regime_vec(score_arr, n, cfg)
  get_regime_params(mode, ...) -> build_regime_thresholds(regime_arr, n, cfg)

7 USER-MANDATED CONFIG KNOBS (CLAUDE.md 2026-05-26):
  REGIME_BTC_MARKET_WEIGHT         — BTC influence on market-wide regime (cross-sym blend)
  REGIME_RANGING_EXIT_GAIN_MIN     — exit floor in RANGING mode
  REGIME_RANGING_NOLOSS_MIN        — noloss floor in RANGING mode
  REGIME_RANGING_WT_REDUCE_FRAC_LOW — reduce fraction at low gain in RANGING
  REGIME_TRENDING_EXIT_GAIN_MIN    — exit floor in TRENDING mode
  REGIME_TRENDING_K_RESET_THRESHOLD — pullback K reset in TRENDING
  REGIME_TRENDING_SLOT_RESERVE_PCT — slot reservation in TRENDING

Plus REGIME_DETECTION_ENABLED master switch and hysteresis params
(REGIME_ENTER_TRENDING_THRESHOLD, REGIME_EXIT_TRENDING_THRESHOLD,
REGIME_MIN_DWELL_BARS) read from config — see config.py:2423-2455.

LOGIC (mirrors ez_regime.compute_regime_score):
  score = adx_component*0.40 + dc_component*0.25 + wt_component*0.25 + sma_component*0.10
  score >  REGIME_ENTER_TRENDING_THRESHOLD -> TRENDING_UP   (regime_int = 1)
  score < -REGIME_ENTER_TRENDING_THRESHOLD -> TRENDING_DOWN (regime_int = 2)
  RANGING (regime_int = 0) requires score in [-EXIT, +EXIT] for hysteresis,
  with REGIME_MIN_DWELL_BARS minimum dwell after a transition.

WIRE-IN POINT (v8_vec_sweep.py):
  When REGIME_DETECTION_ENABLED=True, regime thresholds REPLACE the scalar
  min_gain / MIN_EXIT_GAIN_PCT for non-emergency exit gates. Specifically:
    - bars in RANGING:  exit_gain_min = REGIME_RANGING_EXIT_GAIN_MIN  (~0.15%)
    - bars in TRENDING: exit_gain_min = REGIME_TRENDING_EXIT_GAIN_MIN (~2.0%)

OUTPUTS:
  build_regime_arrays(npz, n, cfg) -> dict with keys:
    "enabled"        : bool         (master switch + npz field availability)
    "regime"         : np.ndarray[int8] of length n  (0=RANGING, 1=UP, 2=DOWN)
    "score"          : np.ndarray[float32] of length n
    "exit_gain_min"  : np.ndarray[float32] of length n  (per-bar non-emergency exit floor)
    "noloss_min"     : np.ndarray[float32] of length n  (per-bar noloss floor)
    "wt_reduce_frac" : np.ndarray[float32] of length n  (per-bar reduce fraction at low gain)
    "slot_reserve"   : np.ndarray[float32] of length n  (per-bar slot reservation pct)
    "k_reset_thr"    : np.ndarray[float32] of length n  (per-bar pullback K reset, TRENDING only)
    "btc_market_weight" : float    (REGIME_BTC_MARKET_WEIGHT — scalar, used by cross-sym caller)
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np


REGIME_RANGING_INT = 0
REGIME_TRENDING_UP_INT = 1
REGIME_TRENDING_DOWN_INT = 2


def _arr(npz: Dict[str, np.ndarray], key: str, n: int, default: float = 0.0) -> np.ndarray:
    """Fetch NPZ field as float32 array of length n with NaN→default replacement.
    Returns full-default array when key is missing or wrong length."""
    arr = npz.get(key)
    if arr is None:
        return np.full(n, default, dtype=np.float32)
    a = np.asarray(arr, dtype=np.float32)
    if a.shape[0] < n:
        # pad with default
        padded = np.full(n, default, dtype=np.float32)
        padded[: a.shape[0]] = a
        a = padded
    elif a.shape[0] > n:
        a = a[:n]
    return np.nan_to_num(a, nan=default).astype(np.float32, copy=False)


def compute_regime_score_vec(npz: Dict[str, np.ndarray], n: int) -> np.ndarray:
    """Vectorised version of ez_regime.compute_regime_score — returns per-bar score in [-100, +100]."""
    adx_1h = _arr(npz, "adx_1h", n, 0.0)
    adx_4h = _arr(npz, "adx_4h", n, 0.0)
    chop_1h = _arr(npz, "choppiness_1h", n, 50.0)
    chop_4h = _arr(npz, "choppiness_4h", n, 50.0)
    dc_w_1h = _arr(npz, "dc_width_1h", n, 0.0)
    dc_w_4h = _arr(npz, "dc_width_4h", n, 0.0)
    dc_w_D = _arr(npz, "dc_width_D", n, 0.0)
    dc_p_1h = _arr(npz, "dc_position_1h", n, 0.5)
    dc_p_4h = _arr(npz, "dc_position_4h", n, 0.5)
    dc_p_D = _arr(npz, "dc_position_D", n, 0.5)
    # wt_bullish_* arrays: live ez_regime treats >0.5 as 1, else 0 (int conversion)
    wt_b_1h_raw = _arr(npz, "wt_bullish_1h", n, 0.0)
    wt_b_4h_raw = _arr(npz, "wt_bullish_4h", n, 0.0)
    wt_b_D_raw = _arr(npz, "wt_bullish_D", n, 0.0)
    wt_b_1h = (wt_b_1h_raw > 0.5).astype(np.float32)
    wt_b_4h = (wt_b_4h_raw > 0.5).astype(np.float32)
    wt_b_D = (wt_b_D_raw > 0.5).astype(np.float32)
    wt_v_1h = _arr(npz, "wt_velocity_1h", n, 0.0)
    wt_v_4h = _arr(npz, "wt_velocity_4h", n, 0.0)
    wt_v_D = _arr(npz, "wt_velocity_D", n, 0.0)
    sma_1h = _arr(npz, "sma_200_1h", n, 0.0)
    sma_1h_prev = _arr(npz, "sma_200_1h_prev", n, 0.0)
    sma_D = _arr(npz, "sma_200_D", n, 0.0)
    sma_D_prev = _arr(npz, "sma_200_D_prev", n, 0.0)

    # --- ADX + Choppiness component (weight 40%) ---
    adx_avg = adx_1h * 0.6 + adx_4h * 0.4
    chop_avg = chop_1h * 0.6 + chop_4h * 0.4
    wt_direction = np.where(wt_b_1h > 0.5, 1.0, -1.0).astype(np.float32)
    # strong trend zone: adx>=25 and chop<=45
    strong = (adx_avg >= 25.0) & (chop_avg <= 45.0)
    trend_strength = np.minimum(100.0, (adx_avg - 15.0) * 5.0)
    # no trend zone: adx<=20 and chop>=55
    no_trend = (adx_avg <= 20.0) & (chop_avg >= 55.0)
    # neutral fallback
    adx_neutral = (adx_avg - 22.5) * 4.0 * wt_direction
    adx_component = np.where(strong, trend_strength * wt_direction,
                             np.where(no_trend, 0.0, adx_neutral)).astype(np.float32)

    # --- DC Width component (weight 25%) ---
    dc_avg = dc_w_1h * 0.5 + dc_w_4h * 0.3 + dc_w_D * 0.2
    dc_pos_avg = dc_p_1h * 0.5 + dc_p_4h * 0.3 + dc_p_D * 0.2
    dc_strong = dc_avg > 5.0
    dc_flat = dc_avg < 2.0
    # neutral interpolation
    dc_neutral = (dc_pos_avg - 0.5) * 100.0 * np.maximum(dc_avg - 2.0, 0.0) / 3.0
    dc_component = np.where(dc_strong, (dc_pos_avg - 0.5) * 200.0,
                            np.where(dc_flat, 0.0, dc_neutral)).astype(np.float32)

    # --- WT Alignment component (weight 25%) ---
    wt_bull_count = (wt_b_1h + wt_b_4h + wt_b_D).astype(np.float32)
    wt_vel_avg = wt_v_1h * 0.5 + wt_v_4h * 0.3 + wt_v_D * 0.2
    all_bull = wt_bull_count >= 3.0
    all_bear = wt_bull_count <= 0.0
    wt_mixed = (wt_bull_count - 1.5) * 30.0
    wt_component = np.where(
        all_bull,
        np.minimum(100.0, 60.0 + np.abs(wt_vel_avg) * 5.0),
        np.where(all_bear, np.maximum(-100.0, -60.0 - np.abs(wt_vel_avg) * 5.0), wt_mixed)
    ).astype(np.float32)

    # --- SMA200 Slope component (weight 10%) ---
    # ez_regime: slope_1h = (sma200_1h - sma200_1h_prev) / sma200_1h * 10000 if sma>0 else 0
    with np.errstate(divide="ignore", invalid="ignore"):
        slope_1h = np.where(sma_1h > 0, (sma_1h - sma_1h_prev) / np.maximum(sma_1h, 1e-9) * 10000.0, 0.0)
        slope_D = np.where(sma_D > 0, (sma_D - sma_D_prev) / np.maximum(sma_D, 1e-9) * 10000.0, 0.0)
    sma_component = np.clip((slope_1h * 0.6 + slope_D * 0.4) * 20.0, -100.0, 100.0).astype(np.float32)

    score = adx_component * 0.40 + dc_component * 0.25 + wt_component * 0.25 + sma_component * 0.10
    return np.clip(score, -100.0, 100.0).astype(np.float32)


def classify_regime_vec(score_arr: np.ndarray, n: int, cfg: Any) -> np.ndarray:
    """Per-bar regime label with hysteresis + min-dwell, mirroring ez_regime.classify_regime.

    Returns int8 array: 0=RANGING, 1=TRENDING_UP, 2=TRENDING_DOWN.
    """
    enter_thresh = float(getattr(cfg, "REGIME_ENTER_TRENDING_THRESHOLD", 30.0))
    exit_thresh = float(getattr(cfg, "REGIME_EXIT_TRENDING_THRESHOLD", 15.0))
    min_dwell = int(getattr(cfg, "REGIME_MIN_DWELL_BARS", 16))
    regime = np.zeros(n, dtype=np.int8)
    current = REGIME_RANGING_INT
    bars_in_current = 0
    for i in range(n):
        s = float(score_arr[i]) if i < score_arr.shape[0] else 0.0
        bars_in_current += 1
        if bars_in_current >= min_dwell:
            if current == REGIME_RANGING_INT:
                if s > enter_thresh:
                    current = REGIME_TRENDING_UP_INT
                    bars_in_current = 0
                elif s < -enter_thresh:
                    current = REGIME_TRENDING_DOWN_INT
                    bars_in_current = 0
            elif current == REGIME_TRENDING_UP_INT:
                if s < exit_thresh:
                    current = REGIME_RANGING_INT
                    bars_in_current = 0
            elif current == REGIME_TRENDING_DOWN_INT:
                if s > -exit_thresh:
                    current = REGIME_RANGING_INT
                    bars_in_current = 0
        regime[i] = current
    return regime


def build_regime_arrays(npz: Dict[str, np.ndarray], n: int, cfg: Any) -> Dict[str, Any]:
    """Top-level vec API — returns per-bar regime label + adapted thresholds.

    Call once per (symbol, side) before the bar loop. Caller reads e.g.
    `regime_arrays["exit_gain_min"][i]` instead of scalar config.MIN_EXIT_GAIN_PCT.

    Returns dict with key "enabled" indicating whether regime adaptation is on.
    """
    enabled = bool(getattr(cfg, "REGIME_DETECTION_ENABLED", False))
    if not enabled or n <= 0:
        return {"enabled": False}

    score = compute_regime_score_vec(npz, n)
    regime = classify_regime_vec(score, n, cfg)

    # RANGING-mode thresholds
    rng_noloss = float(getattr(cfg, "REGIME_RANGING_NOLOSS_MIN", 0.05))
    rng_exit = float(getattr(cfg, "REGIME_RANGING_EXIT_GAIN_MIN", 0.15))
    rng_wt_red_low = float(getattr(cfg, "REGIME_RANGING_WT_REDUCE_FRAC_LOW", 0.40))
    rng_slot = float(getattr(cfg, "REGIME_RANGING_SLOT_RESERVE_PCT", 0.60))
    # TRENDING-mode thresholds
    trd_noloss = float(getattr(cfg, "REGIME_TRENDING_NOLOSS_MIN", 0.50))
    trd_exit = float(getattr(cfg, "REGIME_TRENDING_EXIT_GAIN_MIN", 2.0))
    trd_wt_red_low = float(getattr(cfg, "REGIME_TRENDING_WT_REDUCE_FRAC_LOW", 0.10))
    trd_slot = float(getattr(cfg, "REGIME_TRENDING_SLOT_RESERVE_PCT", 0.40))
    trd_k_reset = float(getattr(cfg, "REGIME_TRENDING_K_RESET_THRESHOLD", 40.0))

    # 7th user-mandated knob — BTC market weight (scalar, applied by cross-sym caller)
    btc_market_weight = float(getattr(cfg, "REGIME_BTC_MARKET_WEIGHT", 0.5))

    is_trending = regime != REGIME_RANGING_INT

    noloss_min = np.where(is_trending, trd_noloss, rng_noloss).astype(np.float32)
    exit_gain_min = np.where(is_trending, trd_exit, rng_exit).astype(np.float32)
    wt_reduce_frac = np.where(is_trending, trd_wt_red_low, rng_wt_red_low).astype(np.float32)
    slot_reserve = np.where(is_trending, trd_slot, rng_slot).astype(np.float32)
    # K reset only meaningful in TRENDING; in RANGING the global default is used
    k_reset_thr = np.where(is_trending, trd_k_reset, 50.0).astype(np.float32)

    return {
        "enabled": True,
        "regime": regime,
        "score": score,
        "noloss_min": noloss_min,
        "exit_gain_min": exit_gain_min,
        "wt_reduce_frac": wt_reduce_frac,
        "slot_reserve": slot_reserve,
        "k_reset_thr": k_reset_thr,
        "btc_market_weight": btc_market_weight,
    }


def regime_adapted_min_gain(regime_arrays: Dict[str, Any], i: int, default_min_gain: float) -> float:
    """Helper: per-bar regime-adapted MIN_EXIT_GAIN floor.

    Returns the regime-conditional exit_gain_min at bar i, falling back to
    `default_min_gain` when regime adaptation is disabled or array missing.
    """
    if not regime_arrays.get("enabled", False):
        return float(default_min_gain)
    arr = regime_arrays.get("exit_gain_min")
    if arr is None or i >= arr.shape[0]:
        return float(default_min_gain)
    return float(arr[i])


def regime_label(regime_arrays: Dict[str, Any], i: int) -> str:
    """Human-readable label at bar i — for diagnostic / log emission."""
    if not regime_arrays.get("enabled", False):
        return "DISABLED"
    arr = regime_arrays.get("regime")
    if arr is None or i >= arr.shape[0]:
        return "DISABLED"
    v = int(arr[i])
    if v == REGIME_TRENDING_UP_INT:
        return "TRENDING_UP"
    if v == REGIME_TRENDING_DOWN_INT:
        return "TRENDING_DOWN"
    return "RANGING"

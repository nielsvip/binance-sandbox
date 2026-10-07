"""
vec_paths/quality_bottom_entry.py — REAL bottom/top entry detector for vec engine.

USER MANDATE 2026-05-22: "~1-2 bottom opportunities per day. They always exist.
Don't enter on every 1m crossover." This module replaces scattergun micro-entries
with a multi-factor AND-gate that fires only when ALL of these align:

LONG (bottom):
  1. HTF BULLISH        — close > ema_20_D AND close > ema_20_4h
  2. SUBSTANTIAL PULLBACK— close <= (1 - QUALITY_BOTTOM_PULLBACK_PCT) × rolling_max(high_1h, last 24h)
  3. MULTI-TF OVERSOLD  — stoch_k_15m < K_15M_MAX AND stoch_k_1h < K_1H_MAX
  4. REVERSAL CONFIRM   — (ha_3m flip red→green in last 3 bars OR ha_15m flip) AND stoch_k_3m rising
  5. LOWER-BAND PROX    — bb_pct_b_15m < BB_PCTB_MAX OR close within DC_LOW_PROX_PCT of dc_low_4h
  6. VOLUME CONFIRM     — volume_3m > VOL_MULT × rolling_mean(volume_3m, last 20 bars)
  7. DAILY CAP          — entries_today < MAX_PER_DAY (state-tracked in SymState)

SHORT (top): mirror — HTF bearish, rally ≥ PULLBACK_PCT from rolling 1h low, K_15m > 75 AND K_1h > 65,
  HA flip green→red, upper-band proximity, volume confirm, same daily cap.

NPZ fields required (all present in standard precompute):
  close, ema_20_D, ema_20_4h, high_1h, low_1h, stoch_k_15m, stoch_k_1h,
  stoch_k_3m, stoch_k_3m_prev, ha_3m, ha_15m, bb_pct_b_15m,
  dc_low_4h, dc_high_4h, volume_3m, timestamps

CONFIG (SweepConfig + live config.py mirror):
  QUALITY_BOTTOM_ENTRY_ENABLED       (default True — new primary entry gate)
  QUALITY_BOTTOM_PULLBACK_PCT        (default 3.0 — required drawdown from local high)
  QUALITY_BOTTOM_K_15M_MAX           (default 25.0 — 15m K oversold cap)
  QUALITY_BOTTOM_K_1H_MAX            (default 35.0 — 1h K oversold cap)
  QUALITY_BOTTOM_PULLBACK_WINDOW_BARS (default 96 — 24h at 15m basis)
  QUALITY_BOTTOM_BB_PCTB_MAX         (default 0.20 — lower-band threshold)
  QUALITY_BOTTOM_DC_LOW_PROX_PCT     (default 0.5 — % distance from dc_low_4h to count as "at support")
  QUALITY_BOTTOM_VOL_MULT            (default 1.3 — volume vs 20-bar mean)
  QUALITY_BOTTOM_VOL_WINDOW_BARS     (default 20)
  QUALITY_BOTTOM_HA_FLIP_LOOKBACK    (default 3 — bars to look back for HA flip)
  QUALITY_BOTTOM_MAX_PER_DAY         (default 3 — entries per sym per UTC day)
  QUALITY_BOTTOM_DISABLE_SCATTERGUN  (default True — when active, micro-entry paths suppressed)

OUTPUT:
  precompute_quality_bottom_long_mask(npz, cfg) -> np.ndarray[bool], shape (N,)
  precompute_quality_top_short_mask(npz, cfg)   -> np.ndarray[bool], shape (N,)
"""
from __future__ import annotations
from typing import Any, Dict
import numpy as np


def _safe_get(npz: Dict, key: str, default_val: float, n: int) -> np.ndarray:
    arr = npz.get(key)
    if arr is None:
        return np.full(n, default_val, dtype=np.float32)
    if isinstance(arr, np.ndarray) and arr.ndim >= 1 and len(arr) == n:
        return arr
    return np.full(n, default_val, dtype=np.float32)


def _ha_str_to_bool(arr: np.ndarray, target: str, n: int) -> np.ndarray:
    """HA color array → bool array (True where color == target).

    2026-05-22 FIX: NPZ stores HA as int8: 1=green, -1=red, 0=neutral.
    Earlier impl treated -1 as truthy via .astype(bool), confusing red with green.
    Now: target=="green" → arr > 0, target=="red" → arr < 0.
    """
    if arr is None or len(arr) != n:
        return np.zeros(n, dtype=bool)
    if arr.dtype.kind in ("i", "u"):
        # Integer-encoded HA: 1=green, -1=red, 0=neutral
        if target == "green":
            return arr > 0
        if target == "red":
            return arr < 0
        return arr == 0
    if arr.dtype == bool:
        # Bool-encoded (rare) — True is "green" convention
        return arr if target == "green" else (~arr)
    # Object/string array
    out = np.zeros(n, dtype=bool)
    for i in range(n):
        try:
            if str(arr[i]).lower() == target:
                out[i] = True
        except Exception:
            pass
    return out


def _rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling max over `window` bars; pads start with -inf."""
    n = len(arr)
    if window <= 1:
        return arr.copy()
    pad = window - 1
    padded = np.concatenate([np.full(pad, -np.inf, dtype=arr.dtype), arr])
    return np.lib.stride_tricks.sliding_window_view(padded, window).max(axis=1)


def _rolling_min(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    if window <= 1:
        return arr.copy()
    pad = window - 1
    padded = np.concatenate([np.full(pad, np.inf, dtype=arr.dtype), arr])
    return np.lib.stride_tricks.sliding_window_view(padded, window).min(axis=1)


def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    if window <= 1:
        return arr.copy()
    pad = window - 1
    padded = np.concatenate([np.full(pad, 0.0, dtype=arr.dtype), arr.astype(np.float64)])
    return np.lib.stride_tricks.sliding_window_view(padded, window).mean(axis=1)


def precompute_quality_bottom_long_mask(npz: Dict, cfg: Any) -> np.ndarray:
    """Returns bool mask shape (N,) — True where LONG bottom entry should fire."""
    if not bool(getattr(cfg, "QUALITY_BOTTOM_ENTRY_ENABLED", True)):
        return np.zeros(len(npz.get("close", [])), dtype=bool)
    close = npz["close"].astype(np.float32)
    n = len(close)
    if n < 100:
        return np.zeros(n, dtype=bool)

    ema_D = _safe_get(npz, "ema_20_D", 0.0, n)
    ema_4h = _safe_get(npz, "ema_20_4h", 0.0, n)
    high_1h = _safe_get(npz, "high_1h", 0.0, n)
    k_15m = _safe_get(npz, "stoch_k_15m", 50.0, n)
    k_1h = _safe_get(npz, "stoch_k_1h", 50.0, n)
    k_3m = _safe_get(npz, "stoch_k_3m", 50.0, n)
    k_3m_prev = _safe_get(npz, "stoch_k_3m_prev", 50.0, n)
    bb_pctb_15m = _safe_get(npz, "bb_pct_b_15m", 0.5, n)
    dc_low_4h = _safe_get(npz, "dc_low_4h", 0.0, n)
    volume_3m = _safe_get(npz, "volume_3m", 0.0, n)

    pullback_pct = float(getattr(cfg, "QUALITY_BOTTOM_PULLBACK_PCT", 3.0))
    pullback_win = int(getattr(cfg, "QUALITY_BOTTOM_PULLBACK_WINDOW_BARS", 96))
    k15_max = float(getattr(cfg, "QUALITY_BOTTOM_K_15M_MAX", 25.0))
    k1h_max = float(getattr(cfg, "QUALITY_BOTTOM_K_1H_MAX", 35.0))
    bb_max = float(getattr(cfg, "QUALITY_BOTTOM_BB_PCTB_MAX", 0.20))
    dc_prox = float(getattr(cfg, "QUALITY_BOTTOM_DC_LOW_PROX_PCT", 0.5))
    vol_mult = float(getattr(cfg, "QUALITY_BOTTOM_VOL_MULT", 1.3))
    vol_win = int(getattr(cfg, "QUALITY_BOTTOM_VOL_WINDOW_BARS", 20))
    ha_lb = int(getattr(cfg, "QUALITY_BOTTOM_HA_FLIP_LOOKBACK", 3))

    # 1. HTF bullish
    htf_ok = (close > ema_D) & (close > ema_4h) & (ema_D > 0) & (ema_4h > 0)

    # 2. Substantial pullback from rolling 24h high
    roll_hi = _rolling_max(high_1h, pullback_win)
    pullback_ok = close <= roll_hi * (1.0 - pullback_pct / 100.0)
    pullback_ok &= roll_hi > 0  # valid window

    # 3. Multi-TF oversold
    oversold_ok = (k_15m < k15_max) & (k_1h < k1h_max)

    # 4. Reversal — HA flip on 3m or 15m AND K_3m rising
    ha_3m_green = _ha_str_to_bool(npz.get("ha_3m"), "green", n)
    ha_15m_green = _ha_str_to_bool(npz.get("ha_15m"), "green", n)
    # Look back ha_lb bars for red presence then current green
    ha_3m_was_red = np.zeros(n, dtype=bool)
    ha_15m_was_red = np.zeros(n, dtype=bool)
    ha_3m_red = _ha_str_to_bool(npz.get("ha_3m"), "red", n)
    ha_15m_red = _ha_str_to_bool(npz.get("ha_15m"), "red", n)
    for lb in range(1, ha_lb + 1):
        ha_3m_was_red[lb:] |= ha_3m_red[:-lb]
        ha_15m_was_red[lb:] |= ha_15m_red[:-lb]
    reversal_3m = ha_3m_green & ha_3m_was_red
    reversal_15m = ha_15m_green & ha_15m_was_red
    k3m_rising = k_3m > k_3m_prev
    reversal_ok = (reversal_3m | reversal_15m) & k3m_rising

    # 5. Lower-band proximity (BB or DC4)
    band_ok = (bb_pctb_15m < bb_max) | ((close <= dc_low_4h * (1.0 + dc_prox / 100.0)) & (dc_low_4h > 0))

    # 6. Volume confirm — parens to fix precedence (& binds tighter than >)
    vol_roll = _rolling_mean(volume_3m, vol_win)
    volume_ok = (volume_3m > (vol_mult * vol_roll)) & (vol_roll > 0)

    fire_mask = htf_ok & pullback_ok & oversold_ok & reversal_ok & band_ok & volume_ok
    return fire_mask


def precompute_quality_top_short_mask(npz: Dict, cfg: Any) -> np.ndarray:
    """Returns bool mask shape (N,) — True where SHORT top entry should fire (mirror of LONG)."""
    if not bool(getattr(cfg, "QUALITY_BOTTOM_ENTRY_ENABLED", True)):
        return np.zeros(len(npz.get("close", [])), dtype=bool)
    close = npz["close"].astype(np.float32)
    n = len(close)
    if n < 100:
        return np.zeros(n, dtype=bool)

    ema_D = _safe_get(npz, "ema_20_D", 0.0, n)
    ema_4h = _safe_get(npz, "ema_20_4h", 0.0, n)
    low_1h = _safe_get(npz, "low_1h", 0.0, n)
    k_15m = _safe_get(npz, "stoch_k_15m", 50.0, n)
    k_1h = _safe_get(npz, "stoch_k_1h", 50.0, n)
    k_3m = _safe_get(npz, "stoch_k_3m", 50.0, n)
    k_3m_prev = _safe_get(npz, "stoch_k_3m_prev", 50.0, n)
    bb_pctb_15m = _safe_get(npz, "bb_pct_b_15m", 0.5, n)
    dc_high_4h = _safe_get(npz, "dc_high_4h", 0.0, n)
    volume_3m = _safe_get(npz, "volume_3m", 0.0, n)

    rally_pct = float(getattr(cfg, "QUALITY_BOTTOM_PULLBACK_PCT", 3.0))
    rally_win = int(getattr(cfg, "QUALITY_BOTTOM_PULLBACK_WINDOW_BARS", 96))
    k15_min_short = 100.0 - float(getattr(cfg, "QUALITY_BOTTOM_K_15M_MAX", 25.0))  # mirror: K > 75
    k1h_min_short = 100.0 - float(getattr(cfg, "QUALITY_BOTTOM_K_1H_MAX", 35.0))   # mirror: K > 65
    bb_min_short = 1.0 - float(getattr(cfg, "QUALITY_BOTTOM_BB_PCTB_MAX", 0.20))   # mirror: BB > 0.80
    dc_prox = float(getattr(cfg, "QUALITY_BOTTOM_DC_LOW_PROX_PCT", 0.5))
    vol_mult = float(getattr(cfg, "QUALITY_BOTTOM_VOL_MULT", 1.3))
    vol_win = int(getattr(cfg, "QUALITY_BOTTOM_VOL_WINDOW_BARS", 20))
    ha_lb = int(getattr(cfg, "QUALITY_BOTTOM_HA_FLIP_LOOKBACK", 3))

    # 1. HTF bearish (mirror)
    htf_ok = (close < ema_D) & (close < ema_4h) & (ema_D > 0) & (ema_4h > 0)

    # 2. Rally from rolling 24h low (mirror)
    roll_lo = _rolling_min(low_1h, rally_win)
    rally_ok = close >= roll_lo * (1.0 + rally_pct / 100.0)
    rally_ok &= roll_lo > 0

    # 3. Multi-TF overbought (mirror)
    overbought_ok = (k_15m > k15_min_short) & (k_1h > k1h_min_short)

    # 4. Reversal — HA flip green→red AND K_3m falling
    ha_3m_red = _ha_str_to_bool(npz.get("ha_3m"), "red", n)
    ha_15m_red = _ha_str_to_bool(npz.get("ha_15m"), "red", n)
    ha_3m_was_green = np.zeros(n, dtype=bool)
    ha_15m_was_green = np.zeros(n, dtype=bool)
    ha_3m_green = _ha_str_to_bool(npz.get("ha_3m"), "green", n)
    ha_15m_green = _ha_str_to_bool(npz.get("ha_15m"), "green", n)
    for lb in range(1, ha_lb + 1):
        ha_3m_was_green[lb:] |= ha_3m_green[:-lb]
        ha_15m_was_green[lb:] |= ha_15m_green[:-lb]
    reversal_3m = ha_3m_red & ha_3m_was_green
    reversal_15m = ha_15m_red & ha_15m_was_green
    k3m_falling = k_3m < k_3m_prev
    reversal_ok = (reversal_3m | reversal_15m) & k3m_falling

    # 5. Upper-band proximity
    band_ok = (bb_pctb_15m > bb_min_short) | ((close >= dc_high_4h * (1.0 - dc_prox / 100.0)) & (dc_high_4h > 0))

    # 6. Volume confirm
    vol_roll = _rolling_mean(volume_3m, vol_win)
    volume_ok = (volume_3m > (vol_mult * vol_roll)) & (vol_roll > 0)

    fire_mask = htf_ok & rally_ok & overbought_ok & reversal_ok & band_ok & volume_ok
    return fire_mask

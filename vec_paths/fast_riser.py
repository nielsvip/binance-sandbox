"""
vec_paths/fast_riser.py — FAST_RISER_REDUCE exit path (vectorized).

LIVE SOURCE:
  ez_manage.py:42465-42662  process_position() FAST_RISER block
    Config guard: config.ENABLE_FAST_RISER_REDUCE (default True in config.py:2184)
    AND config.FAST_RISER_DOUBLE_ENABLED (default False — DOUBLE half is disabled per
    BACKTEST_CHANGE_115 note at line 42465).

  FAST_RISER_REDUCE fires when ALL of:
    1. k_3m > 95 (LONG) or k_3m < 5 (SHORT) — very overbought / oversold stochastic
    2. k_3m turning: (k_1m > k_1m_prev) OR (k_3m < k_3m_prev for LONG / k_3m > k_3m_prev for SHORT)
    3. mark_price < current_price_from_i — mark is lagging (slippage signal)
    4. reduction_cooldown_ok (REDUCTION_COOLDOWN_SECONDS elapsed since last reduce)
    5. config.MANAGE_REDUCE is True
    Reduces 50% at prev close price (close_3m_prev).
    Reason: FAST_RISER_REDUCE_50%_at_prev_close_{close_3m_prev}

  NOTE: FAST_RISER_DOUBLE (augment 100% when quick jump up) is permanently disabled per
  BACKTEST_CHANGE_115 ablation results (net negative PnL). This module only implements REDUCE.

PURPOSE:
  Detects a position that has risen very fast into extreme overbought / oversold territory
  (for LONG: stochastic >95; for SHORT: stochastic <5) AND shows early signs of momentum
  turning (k_3m reverting). Reduces 50% to lock partial profit before a likely reversal.
  Only fires when the mark_price is lagging current price (confirming the move is fast/real).

VEC APPROXIMATION:
  The live version checks k_1m / k_1m_prev (1m stochastic turning) as an OR with k_3m
  reverting. Since 1m is rarely in NPZ, the vec version uses the k_3m reversal condition
  alone (k_3m_prev available in NPZ when precomputed). The mark_price < current_price lag
  check is also dropped in the vec version (no live mark_price in NPZ) — the extreme stoch
  condition plus k_3m reversal is the dominant signal. This makes the vec version slightly
  less precise than live but captures the same tail-reversal pattern.

  The config-level gain requirement (vec): FAST_RISER_REDUCE_REQUIRE_GAIN_PCT (default 0.8%)
  mirrors config.ENABLE_FAST_RISER_REDUCE block requiring current_gain > 0.8 at line 42472.
  In live this is a DOUBLE gate — for REDUCE the gain check is not explicit, so vec sets
  FAST_RISER_REDUCE_REQUIRE_GAIN_PCT=0 to match live (any gain).

NPZ FIELDS:
  stoch_k_3m        — stochastic K on 3m TF
  stoch_k_3m_prev   — previous bar K on 3m TF (or stoch_k_3m_prev alias)
  close             — bar close price (for price-change rate fallback)

CONFIG KEYS:
  ENABLE_FAST_RISER_REDUCE          (default True)  — mirrors live config.ENABLE_FAST_RISER_REDUCE
  FAST_RISER_REDUCE_ENABLED         (default False) — vec-level master switch (separate from live,
                                                       default OFF to avoid surprise fires in sweep)
  FAST_RISER_K3M_OVERBOUGHT_LONG    (default 95.0)  — stoch K threshold for LONG reduce
  FAST_RISER_K3M_OVERSOLD_SHORT     (default 5.0)   — stoch K threshold for SHORT reduce
  FAST_RISER_REQUIRE_K3M_REVERSAL   (default True)  — require k_3m < k_3m_prev (LONG) or > (SHORT)
  FAST_RISER_REDUCE_REQUIRE_GAIN_PCT (default 0.0)  — min gain% before reduce fires (0=any)
  FAST_RISER_WINDOW_BARS            (default 5)     — look-back bars for computing price-move rate
                                                       (used in vec only, not in live scalar)
  FAST_RISER_MOVE_PCT               (default 2.0)   — min price-move % over WINDOW_BARS for fallback

RETURNS (check_fast_riser_reduce):
  dict with keys:
    side    — "LONG" or "SHORT"
    reason  — str matching live FAST_RISER_REDUCE_50% format
    reduce  — bool (always True when returned)
  or None if conditions not met.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


def check_fast_riser_reduce(
    store,
    bar_idx: int,
    pos_state,
    mode: str,
    cfg,
) -> Optional[Dict[str, Any]]:
    """FAST_RISER_REDUCE exit signal (crypto + tradier, default OFF in vec).

    Args:
        store:     _NPZStore for this symbol, or a plain dict (indicator_cache entry).
                   If None, returns None.
        bar_idx:   Current bar index (unused when store is a dict).
        pos_state: Object with .open, .side, .gain_pct/.gain attributes.
        mode:      "crypto" or "tradier".
        cfg:       Config object.

    Returns:
        dict if FAST_RISER_REDUCE fires, None otherwise.
        dict keys: side, reason, reduce (=True)
    """
    live_enabled = getattr(cfg, "ENABLE_FAST_RISER_REDUCE", True)
    vec_enabled = getattr(cfg, "FAST_RISER_REDUCE_ENABLED", False)
    if not live_enabled or not vec_enabled:
        return None
    if pos_state is None:
        return None
    if hasattr(pos_state, "open") and not pos_state.open:
        return None
    side = getattr(pos_state, "side", None) or getattr(pos_state, "position_side", None)
    if not side:
        return None
    is_long = (side == "LONG")
    min_gain = float(getattr(cfg, "FAST_RISER_REDUCE_REQUIRE_GAIN_PCT", 0.0))
    if min_gain > 0:
        gain = float(getattr(pos_state, "gain_pct", None) or getattr(pos_state, "gain", 0) or 0)
        if gain < min_gain:
            return None
    k3m_long_thr = float(getattr(cfg, "FAST_RISER_K3M_OVERBOUGHT_LONG", 95.0))
    k3m_short_thr = float(getattr(cfg, "FAST_RISER_K3M_OVERSOLD_SHORT", 5.0))
    require_reversal = getattr(cfg, "FAST_RISER_REQUIRE_K3M_REVERSAL", True)
    if store is None:
        return None
    if isinstance(store, dict):
        k3m = float(store.get("stoch_k_3m", 50.0) or 50.0)
        k3m_prev = float(store.get("stoch_k_3m_prev", k3m) or k3m)
        price = float(store.get("current_price", 0) or 0)
    else:
        k3m = store.f("stoch_k_3m", bar_idx, 50.0)
        k3m_prev = store.f("stoch_k_3m_prev", bar_idx, k3m)
        price = store.price(bar_idx)
    if price <= 0:
        return None
    if is_long:
        overbought = k3m > k3m_long_thr
        if not overbought:
            return None
        if require_reversal and k3m >= k3m_prev:
            return None
        reason = f"FAST_RISER_REDUCE_50%_at_prev_close_{price:.6f}"
    else:
        oversold = k3m < k3m_short_thr
        if not oversold:
            return None
        if require_reversal and k3m <= k3m_prev:
            return None
        reason = f"FAST_RISER_REDUCE_50%_at_prev_close_{price:.6f}"
    return {
        "side": side,
        "reason": reason,
        "reduce": True,
    }


def check_fast_riser_reduce_vec(
    close_arr: np.ndarray,
    pos_open_arr: np.ndarray,
    pos_gain_arr: np.ndarray,
    pos_side_arr: np.ndarray,
    mode: str,
    cfg,
    stoch_k_arr: Optional[np.ndarray] = None,
    stoch_k_prev_arr: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Vectorized FAST_RISER_REDUCE signal across all bars.

    Args:
        close_arr:        float64 array, shape (N,) — bar close prices.
        pos_open_arr:     bool array, shape (N,) — True when position is open.
        pos_gain_arr:     float64 array, shape (N,) — current gain% per bar.
        pos_side_arr:     int8 array, shape (N,) — +1 = LONG, -1 = SHORT.
        mode:             "crypto" or "tradier".
        cfg:              Config object.
        stoch_k_arr:      float64 array, shape (N,) — stoch_k_3m (or 5m for tradier).
                          If None, falls back to price-move rate detection.
        stoch_k_prev_arr: float64 array, shape (N,) — previous bar stoch K.
                          If None, computed as np.roll(stoch_k_arr, 1).

    Returns:
        bool array shape (N,) — True where FAST_RISER_REDUCE fires.
    """
    live_enabled = getattr(cfg, "ENABLE_FAST_RISER_REDUCE", True)
    vec_enabled = getattr(cfg, "FAST_RISER_REDUCE_ENABLED", False)
    n = len(close_arr)
    result = np.zeros(n, dtype=bool)
    if not live_enabled or not vec_enabled:
        return result
    k3m_long_thr = float(getattr(cfg, "FAST_RISER_K3M_OVERBOUGHT_LONG", 95.0))
    k3m_short_thr = float(getattr(cfg, "FAST_RISER_K3M_OVERSOLD_SHORT", 5.0))
    require_reversal = getattr(cfg, "FAST_RISER_REQUIRE_K3M_REVERSAL", True)
    min_gain = float(getattr(cfg, "FAST_RISER_REDUCE_REQUIRE_GAIN_PCT", 0.0))
    window = int(getattr(cfg, "FAST_RISER_WINDOW_BARS", 5))
    move_pct = float(getattr(cfg, "FAST_RISER_MOVE_PCT", 2.0))
    valid = pos_open_arr.astype(bool) & (close_arr > 0)
    if min_gain > 0:
        valid = valid & (pos_gain_arr >= min_gain)
    is_long = pos_side_arr > 0
    is_short = pos_side_arr < 0
    if stoch_k_arr is not None and len(stoch_k_arr) == n:
        k3m = stoch_k_arr.astype(float)
        if stoch_k_prev_arr is not None and len(stoch_k_prev_arr) == n:
            k3m_prev = stoch_k_prev_arr.astype(float)
        else:
            k3m_prev = np.roll(k3m, 1)
            k3m_prev[0] = k3m[0]
        long_ob = k3m > k3m_long_thr
        short_os = k3m < k3m_short_thr
        if require_reversal:
            long_rev = k3m < k3m_prev
            short_rev = k3m > k3m_prev
        else:
            long_rev = np.ones(n, dtype=bool)
            short_rev = np.ones(n, dtype=bool)
        long_fire = valid & is_long & long_ob & long_rev
        short_fire = valid & is_short & short_os & short_rev
    else:
        if window < 2 or n < window:
            return result
        prev_close = np.roll(close_arr, window)
        prev_close[:window] = close_arr[:window]
        with np.errstate(divide="ignore", invalid="ignore"):
            move = np.where(
                prev_close > 0,
                (close_arr - prev_close) / prev_close * 100.0,
                0.0,
            )
        long_fire = valid & is_long & (move >= move_pct)
        short_fire = valid & is_short & (move <= -move_pct)
    result = long_fire | short_fire
    return result

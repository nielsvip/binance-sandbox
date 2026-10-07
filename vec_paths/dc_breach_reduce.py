"""
vec_paths/dc_breach_reduce.py — DC_BREACH_REDUCE exit path (vectorized).

LIVE SOURCE:
  ez_manage.py:27002-27224  monitor_dc_breach_reduce() — async monitor loop (3s interval)
    Two sub-paths:
      1. Hedged losers (lines ~27017-27112): iterates active_hedges → finds losing_position_key
         → fetches dc_low_15m / dc_high_15m from live indicators → if breached, queue REDUCE
         to pos_min_qty. Sets pending_reentries with reentry_condition="below_exit_price".
      2. Unhedged losers (lines ~27113-27221): iterates positions_by_account → gain < 0 AND
         not in STRICT_NO_LOSS_ACCOUNTS → same DC breach check → queue REDUCE.
    Cooldown: 120s per position_key (self._dc_breach_cooldown dict).
    Config: DC_BREACH_REDUCE_ENABLED (implicit — monitor is launched at startup when wired).
    Reason format: DC_BREACH_REDUCE_{LOW|HIGH}_15m_price_{price} (hedged)
                   DC_BREACH_REDUCE_UNHEDGED_{LOW|HIGH}_15m_price_{price} (unhedged)

PURPOSE:
  When a losing position's price crosses the 15m Donchian Channel boundary in the adverse
  direction (LONG: close < dc_low_15m; SHORT: close > dc_high_15m), reduce (or fully close
  to min_qty) the position. This is the DC-channel emergency reduce for underwater positions,
  analogous to R1_DC_LOW4 but using the 15m DC rather than the 3m 4-bar DC.

VEC APPROXIMATION:
  The live version uses real-time mark_price from the Redis cache and the current indicators
  snapshot from ii(). The vec version reads dc_low_15m / dc_high_15m and close from the NPZ
  store (or indicator_cache dict). Since NPZ fields are bar-aligned, the vec version fires at
  the exact bar where breach occurs — no 3s polling lag. Gain check uses pos_state.gain_pct
  (computed from entry_price vs current close at the engine level).

NPZ FIELDS:
  dc_high_15m  — 15m Donchian Channel high (upper band)
  dc_low_15m   — 15m Donchian Channel low (lower band)
  close        — current bar close price (also store.price())

CONFIG KEYS:
  DC_BREACH_REDUCE_ENABLED        (default False) — master switch
  DC_BREACH_REDUCE_REQUIRE_LOSS   (default True)  — only fire when gain < 0

RETURNS (check_dc_breach_reduce):
  dict with keys:
    side    — "LONG" or "SHORT"
    reason  — str matching live DC_BREACH_REDUCE_* format (without price suffix for vec)
    reduce  — bool (always True when returned)
  or None if conditions not met.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


def check_dc_breach_reduce(
    store,
    bar_idx: int,
    pos_state,
    mode: str,
    cfg,
) -> Optional[Dict[str, Any]]:
    """DC_BREACH_REDUCE exit signal (crypto + tradier, default OFF).

    Args:
        store:     _NPZStore for this symbol, OR a plain dict (indicator_cache entry).
                   If None, falls back to reading from pos_state directly (not supported —
                   returns None). If a dict, uses dict.get() instead of store.f().
        bar_idx:   Current bar index (unused when store is a dict).
        pos_state: Object with .open, .side, .gain_pct (or .gain) attributes.
        mode:      "crypto" or "tradier".
        cfg:       Config object with DC_BREACH_REDUCE_* attributes.

    Returns:
        dict if DC_BREACH_REDUCE fires, None otherwise.
        dict keys: side, reason, reduce (=True)
    """
    # 2026-05-26 — live config uses EXIT_DC_BREACH_REDUCE_ENABLED (config.py:1196).
    # Accept either knob; live wins. DC_BREACH_REDUCE_ENABLED kept as alias for sweep tests.
    enabled = bool(getattr(cfg, "EXIT_DC_BREACH_REDUCE_ENABLED",
                           getattr(cfg, "DC_BREACH_REDUCE_ENABLED", False)))
    if not enabled:
        return None
    if pos_state is None:
        return None
    if hasattr(pos_state, "open") and not pos_state.open:
        return None
    side = getattr(pos_state, "side", None) or getattr(pos_state, "position_side", None)
    if not side:
        return None
    is_long = (side == "LONG")
    require_loss = getattr(cfg, "DC_BREACH_REDUCE_REQUIRE_LOSS", True)
    if require_loss:
        gain = float(getattr(pos_state, "gain_pct", None) or getattr(pos_state, "gain", 0) or 0)
        if gain >= 0:
            return None
    if store is None:
        return None
    if isinstance(store, dict):
        price = float(store.get("current_price", 0) or 0)
        dc_low_15m = float(store.get("dc_low_15m", 0) or 0)
        dc_high_15m = float(store.get("dc_high_15m", 0) or 0)
    else:
        price = store.price(bar_idx)
        dc_low_15m = store.f("dc_low_15m", bar_idx, 0.0)
        dc_high_15m = store.f("dc_high_15m", bar_idx, 0.0)
    if price <= 0:
        return None
    if is_long:
        if dc_low_15m <= 0:
            return None
        if price >= dc_low_15m:
            return None
        reason = f"DC_BREACH_REDUCE_LOW_15m_price_{price:.6f}"
    else:
        if dc_high_15m <= 0:
            return None
        if price <= dc_high_15m:
            return None
        reason = f"DC_BREACH_REDUCE_HIGH_15m_price_{price:.6f}"
    return {
        "side": side,
        "reason": reason,
        "reduce": True,
    }


def check_dc_breach_reduce_vec(
    close_arr: np.ndarray,
    dc_high_arr: np.ndarray,
    dc_low_arr: np.ndarray,
    pos_open_arr: np.ndarray,
    pos_gain_arr: np.ndarray,
    pos_side_arr: np.ndarray,
    mode: str,
    cfg,
) -> np.ndarray:
    """Vectorized DC_BREACH_REDUCE signal across all bars.

    Args:
        close_arr:    float64 array, shape (N,) — bar close prices.
        dc_high_arr:  float64 array, shape (N,) — dc_high_15m per bar.
        dc_low_arr:   float64 array, shape (N,) — dc_low_15m per bar.
        pos_open_arr: bool array, shape (N,) — True when position is open.
        pos_gain_arr: float64 array, shape (N,) — current gain% per bar.
        pos_side_arr: int8 array, shape (N,) — +1 = LONG, -1 = SHORT.
        mode:         "crypto" or "tradier".
        cfg:          Config object.

    Returns:
        bool array shape (N,) — True where DC_BREACH_REDUCE fires.
    """
    # 2026-05-26 — accept EXIT_DC_BREACH_REDUCE_ENABLED (live name) or alias.
    enabled = bool(getattr(cfg, "EXIT_DC_BREACH_REDUCE_ENABLED",
                           getattr(cfg, "DC_BREACH_REDUCE_ENABLED", False)))
    n = len(close_arr)
    result = np.zeros(n, dtype=bool)
    if not enabled:
        return result
    require_loss = getattr(cfg, "DC_BREACH_REDUCE_REQUIRE_LOSS", True)
    valid = pos_open_arr.astype(bool)
    if require_loss:
        valid = valid & (pos_gain_arr < 0)
    price_valid = close_arr > 0
    is_long = pos_side_arr > 0
    is_short = pos_side_arr < 0
    long_breach = (
        valid & is_long & price_valid
        & (dc_low_arr > 0)
        & (close_arr < dc_low_arr)
    )
    short_breach = (
        valid & is_short & price_valid
        & (dc_high_arr > 0)
        & (close_arr > dc_high_arr)
    )
    result = long_breach | short_breach
    return result

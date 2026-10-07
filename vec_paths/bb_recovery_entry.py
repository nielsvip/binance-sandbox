"""
vec_paths/bb_recovery_entry.py — BB_RECOVERY_ENTRY signal (vec module, sweep testing only).

NEW SIGNAL — not yet in live code. Vec module for sweep testing only.

SIGNAL LOGIC: failed BB breakout/breakdown entry (mean-reversion entry after overshoot).
  LONG:  close[bar-1] < bb_lower_1h[bar-1] AND close[bar] > bb_lower_1h[bar]
         → price broke below lower band (panic/overshoot) then recovered above it
         → failed breakdown → enter LONG expecting continued mean-reversion upward.
  SHORT: close[bar-1] > bb_upper_1h[bar-1] AND close[bar] < bb_upper_1h[bar]
         → price broke above upper band (euphoria/overshoot) then dropped back below it
         → failed breakout → enter SHORT expecting continued mean-reversion downward.

CONFIRMATION (BB_RECOVERY_ENTRY_REQUIRE_HA_CONFIRM=True, default):
  LONG:  ha_3m == "green" OR stoch_k_3m > stoch_k_3m_prev  (momentum recovering)
  SHORT: ha_3m == "red"   OR stoch_k_3m < stoch_k_3m_prev  (momentum fading)

NPZ FIELDS REQUIRED:
  close             — base-TF close price (used for cross detection)
  bb_upper_1h       — Bollinger upper band, 1h (= bb_high_1h in live indicators)
  bb_lower_1h       — Bollinger lower band, 1h (= bb_low_1h in live indicators)
  ha_3m             — Heiken-Ashi 3m direction string ("green" / "red" / "neutral")
  stoch_k_3m        — Stochastic %K, 3m current bar
  stoch_k_3m_prev   — Stochastic %K, 3m previous bar

CONFIG FLAGS (all new, all default OFF):
  BB_RECOVERY_ENTRY_ENABLED          — crypto (default False)
  BB_RECOVERY_ENTRY_ENABLED_TRADIER  — tradier (default False)
  BB_RECOVERY_ENTRY_REQUIRE_HA_CONFIRM — require ha_3m or stoch_k confirmation (default True)

SIZING: standard size (no multiplier). This is a test entry signal.

REASON FORMAT:
  LONG:  BB_RECOVERY_ENTRY_LONG_bb={bb_lower:.4f}_px={px:.4f}
  SHORT: BB_RECOVERY_ENTRY_SHORT_bb={bb_upper:.4f}_px={px:.4f}

RELATIONSHIP TO BB_RECOVERY_EXIT:
  bb_recovery.py (EXIT): "position was entered OUTSIDE the BB, price recovered back toward
    entry → exit before it worsens." (protective exit for stranded entries)
  bb_recovery_entry.py (ENTRY, this module): "price JUST crossed BACK inside the BB after
    an overshoot → enter in the direction of mean-reversion." (opportunistic mean-reversion entry)

# Sweep test signal — requires full 114-sym tradier + 48-sym crypto sweep before any live consideration
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


def check_bb_recovery_entry(
    store,
    bar_idx: int,
    mode: str,
    cfg,
) -> Optional[Dict[str, Any]]:
    """BB_RECOVERY_ENTRY signal for one symbol at one bar (scalar path).

    Args:
        store:    _NPZStore for this symbol.
        bar_idx:  Current bar index (must be >= 1 to have a prior bar).
        mode:     "crypto" or "tradier".
        cfg:      VecConfig with BB_RECOVERY_ENTRY_* fields.

    Returns:
        dict if signal fires, None otherwise.
        dict keys:
          side    — "LONG" or "SHORT"
          reason  — str matching BB_RECOVERY_ENTRY_* format
          entry   — True (always True when returned)
    """
    if mode == "tradier":
        enabled = getattr(cfg, "BB_RECOVERY_ENTRY_ENABLED_TRADIER", False)
    else:
        enabled = getattr(cfg, "BB_RECOVERY_ENTRY_ENABLED", False)
    if not enabled:
        return None
    if bar_idx < 1:
        return None
    require_confirm = getattr(cfg, "BB_RECOVERY_ENTRY_REQUIRE_HA_CONFIRM", True)
    close_cur = store.f("close", bar_idx, 0.0)
    close_prev = store.f("close", bar_idx - 1, 0.0)
    if close_cur <= 0 or close_prev <= 0:
        return None
    bb_upper_cur = store.f("bb_upper_1h", bar_idx, 0.0)
    bb_upper_prev = store.f("bb_upper_1h", bar_idx - 1, 0.0)
    bb_lower_cur = store.f("bb_lower_1h", bar_idx, 0.0)
    bb_lower_prev = store.f("bb_lower_1h", bar_idx - 1, 0.0)
    if bb_upper_cur <= 0 or bb_lower_cur <= 0 or bb_upper_prev <= 0 or bb_lower_prev <= 0:
        return None
    long_cross = (close_prev < bb_lower_prev) and (close_cur > bb_lower_cur)
    short_cross = (close_prev > bb_upper_prev) and (close_cur < bb_upper_cur)
    if not long_cross and not short_cross:
        return None
    ha_3m = store.s("ha_3m", bar_idx)
    k3 = store.f("stoch_k_3m", bar_idx, 50.0)
    k3p = store.f("stoch_k_3m_prev", bar_idx, k3)
    if long_cross:
        if require_confirm:
            confirmed = (ha_3m == "green") or (k3 > k3p)
            if not confirmed:
                return None
        reason = f"BB_RECOVERY_ENTRY_LONG_bb={bb_lower_cur:.4f}_px={close_cur:.4f}"
        return {"side": "LONG", "reason": reason, "entry": True}
    if require_confirm:
        confirmed = (ha_3m == "red") or (k3 < k3p)
        if not confirmed:
            return None
    reason = f"BB_RECOVERY_ENTRY_SHORT_bb={bb_upper_cur:.4f}_px={close_cur:.4f}"
    return {"side": "SHORT", "reason": reason, "entry": True}


def check_bb_recovery_entry_vec(
    close_arr: np.ndarray,
    bb_upper_arr: np.ndarray,
    bb_lower_arr: np.ndarray,
    ha_3m_arr: np.ndarray,
    stoch_k_arr: np.ndarray,
    stoch_k_prev_arr: np.ndarray,
    mode: str,
    cfg,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized BB_RECOVERY_ENTRY signal across all bars (sweep path).

    Args:
        close_arr:        float array, shape (N,), base-TF close prices.
        bb_upper_arr:     float array, shape (N,), bb_upper_1h forward-filled to base TF.
        bb_lower_arr:     float array, shape (N,), bb_lower_1h forward-filled to base TF.
        ha_3m_arr:        object/str array, shape (N,), values "green"/"red"/"neutral".
        stoch_k_arr:      float array, shape (N,), stoch_k_3m current bar.
        stoch_k_prev_arr: float array, shape (N,), stoch_k_3m previous bar.
        mode:             "crypto" or "tradier".
        cfg:              VecConfig with BB_RECOVERY_ENTRY_* fields.

    Returns:
        (long_signals, short_signals) — bool arrays of length N_bars.
        Index 0 is always False (requires a prior bar).
        Signal at index i means: enter at bar i.
    """
    if mode == "tradier":
        enabled = getattr(cfg, "BB_RECOVERY_ENTRY_ENABLED_TRADIER", False)
    else:
        enabled = getattr(cfg, "BB_RECOVERY_ENTRY_ENABLED", False)
    n = len(close_arr)
    false_arr = np.zeros(n, dtype=bool)
    if not enabled or n < 2:
        return false_arr, false_arr.copy()
    require_confirm = getattr(cfg, "BB_RECOVERY_ENTRY_REQUIRE_HA_CONFIRM", True)
    close_cur = close_arr[1:]
    close_prev = close_arr[:-1]
    bb_upper_cur = bb_upper_arr[1:]
    bb_upper_prev = bb_upper_arr[:-1]
    bb_lower_cur = bb_lower_arr[1:]
    bb_lower_prev = bb_lower_arr[:-1]
    valid = (
        (close_cur > 0)
        & (close_prev > 0)
        & (bb_upper_cur > 0)
        & (bb_lower_cur > 0)
        & (bb_upper_prev > 0)
        & (bb_lower_prev > 0)
    )
    long_cross = valid & (close_prev < bb_lower_prev) & (close_cur > bb_lower_cur)
    short_cross = valid & (close_prev > bb_upper_prev) & (close_cur < bb_upper_cur)
    if require_confirm:
        ha_cur = ha_3m_arr[1:]
        k_cur = stoch_k_arr[1:]
        k_prev = stoch_k_prev_arr[1:]
        long_confirm = (ha_cur == "green") | (k_cur > k_prev)
        short_confirm = (ha_cur == "red") | (k_cur < k_prev)
        long_cross = long_cross & long_confirm
        short_cross = short_cross & short_confirm
    long_signals = np.zeros(n, dtype=bool)
    short_signals = np.zeros(n, dtype=bool)
    long_signals[1:] = long_cross
    short_signals[1:] = short_cross
    return long_signals, short_signals

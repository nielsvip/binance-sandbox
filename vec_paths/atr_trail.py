"""
vec_paths/atr_trail.py — ATR trailing stop, sweep-test only.

PURPOSE:
    Sweep-test a configurable ATR trailing stop with an arm-gain requirement.
    The naive 1.5x always-on ATR trail was the #1 stock PnL destroyer
    (-2557% cumulative loss, disabled via BACKTEST_CHANGE_T58).
    This module tests whether a wider multiplier + arm-gain requirement
    (don't activate until the position reaches +ARM_GAIN_PCT%) performs better.

LIVE STATUS:
    ATR_TRAIL_ENABLED_TRADIER=False
    ATR_TRAIL_2X_EXIT_ENABLED=False
    DEAD. History of -2557% cumulative loss from always-on 1.5x naive version.
    THIS MODULE IS FOR SWEEP TESTING ONLY.
    NEVER wire into live without full 114-sym 4yr proof reviewed by user.

LOGIC:
    Once position gain >= ATR_TRAIL_SWEEP_ARM_GAIN_PCT, activate trail.
    Trail level for LONG: entry_price - ATR_TRAIL_SWEEP_MULT * atr_14
    Trail level for SHORT: entry_price + ATR_TRAIL_SWEEP_MULT * atr_14
    If ATR_TRAIL_SWEEP_LOCK_PROFIT=True, trail can only move in the position's
    favor (higher for LONG, lower for SHORT) — never step back.
    Stop fires when close price crosses through the trail level.

NPZ FIELDS:
    atr_14_5m   (stocks / tradier mode)
    atr_14_3m   (crypto mode)
    close       (current bar close price)

CONFIG KEYS:
    ATR_TRAIL_SWEEP_ENABLED       bool   default False — master switch
    ATR_TRAIL_SWEEP_MULT          float  default 2.0   — ATR multiplier
    ATR_TRAIL_SWEEP_ARM_GAIN_PCT  float  default 1.0   — arm when gain >= this %
    ATR_TRAIL_SWEEP_LOCK_PROFIT   bool   default True  — trail only moves favorably
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np


# ─── ATR field selection by mode ───────────────────────────────────────────────

def _atr_field(mode: str) -> str:
    return "atr_14_5m" if mode == "tradier" else "atr_14_3m"


# ─── Scalar stateful path ──────────────────────────────────────────────────────

def update_atr_trail_level(
    pos_state: Any,
    bar_idx: int,
    store: Any,
    mode: str,
    cfg: Any,
) -> float:
    """Update pos_state.atr_trail_level and return the current trail level.

    Call once per bar for every open position to maintain the trail.
    If the module is disabled or the position is not yet armed, returns 0.0
    and leaves atr_trail_level as None.

    Args:
        pos_state: _PositionState — must have .gain_pct, .entry_price, .side.
                   atr_trail_level attribute is read and written here.
        bar_idx:   Current bar index in NPZ.
        store:     _NPZStore with .f(key, idx, default) and .price(idx).
        mode:      "crypto" or "tradier".
        cfg:       Config object with ATR_TRAIL_SWEEP_* fields.

    Returns:
        Current trail level (float), or 0.0 if not active.
    """
    if not getattr(cfg, "ATR_TRAIL_SWEEP_ENABLED", False):
        return 0.0
    if pos_state is None or not getattr(pos_state, "open", False):
        return 0.0
    gain = float(getattr(pos_state, "gain_pct", 0.0))
    arm_pct = float(getattr(cfg, "ATR_TRAIL_SWEEP_ARM_GAIN_PCT", 1.0))
    if gain < arm_pct:
        return 0.0
    mult = float(getattr(cfg, "ATR_TRAIL_SWEEP_MULT", 2.0))
    lock_profit = bool(getattr(cfg, "ATR_TRAIL_SWEEP_LOCK_PROFIT", True))
    entry_price = float(getattr(pos_state, "entry_price", 0.0))
    if entry_price <= 0:
        return 0.0
    atr = store.f(_atr_field(mode), bar_idx, 0.0)
    if atr <= 0:
        return 0.0
    is_long = (getattr(pos_state, "side", "LONG") == "LONG")
    if is_long:
        new_trail = entry_price - mult * atr
    else:
        new_trail = entry_price + mult * atr
    prev_trail = getattr(pos_state, "atr_trail_level", None)
    if lock_profit and prev_trail is not None:
        if is_long:
            new_trail = max(new_trail, prev_trail)
        else:
            new_trail = min(new_trail, prev_trail)
    pos_state.atr_trail_level = new_trail
    return new_trail


def check_atr_trail_stop(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    """ATR trailing stop check (stateful, per-bar scalar path).

    Call AFTER update_atr_trail_level() so that pos_state.atr_trail_level
    reflects the most current trail for this bar.

    SWEEP TESTING ONLY. DO NOT wire into live without full 114-sym 4yr proof.

    Args:
        store:     _NPZStore with .f(key, idx, default) and .price(idx).
        bar_idx:   Current bar index in NPZ.
        pos_state: _PositionState — must be open with .entry_price, .gain_pct,
                   .side, and .atr_trail_level set by update_atr_trail_level().
        mode:      "crypto" or "tradier".
        cfg:       Config object with ATR_TRAIL_SWEEP_* fields.

    Returns:
        dict  — {"side": ..., "reason": ..., "close": True} when stop fires.
        None  — stop did not fire.

    Stop fires when close price crosses through the trail level.
    """
    if not getattr(cfg, "ATR_TRAIL_SWEEP_ENABLED", False):
        return None
    if pos_state is None or not getattr(pos_state, "open", False):
        return None
    # Step 1: arm check
    gain = float(getattr(pos_state, "gain_pct", 0.0))
    arm_pct = float(getattr(cfg, "ATR_TRAIL_SWEEP_ARM_GAIN_PCT", 1.0))
    if gain < arm_pct:
        return None
    # Step 2: read trail level (should already be set by update_atr_trail_level)
    trail = getattr(pos_state, "atr_trail_level", None)
    if trail is None or trail <= 0:
        return None
    # Step 3: read current price
    px = store.price(bar_idx)
    if px <= 0:
        px = store.f("close", bar_idx, 0.0)
    if px <= 0:
        return None
    is_long = (getattr(pos_state, "side", "LONG") == "LONG")
    mult = float(getattr(cfg, "ATR_TRAIL_SWEEP_MULT", 2.0))
    # Step 4: fire if price crossed through trail
    fired = (is_long and px <= trail) or ((not is_long) and px >= trail)
    if not fired:
        return None
    side = getattr(pos_state, "side", "LONG")
    reason = (
        f"ATR_TRAIL_SWEEP_trail={trail:.4f}_px={px:.4f}_mult={mult}"
    )
    return {
        "side": side,
        "reason": reason,
        "close": True,
    }


# ─── Stateless vectorized batch path ──────────────────────────────────────────

def check_atr_trail_stop_vec(
    close_arr: np.ndarray,
    entry_price_arr: np.ndarray,
    atr_arr: np.ndarray,
    pos_open_arr: np.ndarray,
    pos_side_arr: np.ndarray,
    max_gain_arr: np.ndarray,
    mode: str,
    cfg: Any,
) -> np.ndarray:
    """Stateless vectorized ATR trailing stop for bulk sweep evaluation.

    # STATELESS APPROX: uses current ATR, not running max trail.
    # May differ from stateful scalar version.
    # The stateful version tracks the peak trail level across bars;
    # this version computes trail = entry ± (MULT × atr) at the current bar only.
    # Result is a lower bound estimate — fewer fires than the stateful path.

    SWEEP TESTING ONLY. DO NOT wire into live without full 114-sym 4yr proof.

    Args:
        close_arr:       shape (N,) float64 — current close prices.
        entry_price_arr: shape (N,) float64 — entry prices for open positions.
        atr_arr:         shape (N,) float64 — ATR values (atr_14_5m or atr_14_3m).
        pos_open_arr:    shape (N,) bool    — True when position is open.
        pos_side_arr:    shape (N,) object  — "LONG" or "SHORT" strings.
        max_gain_arr:    shape (N,) float64 — maximum gain pct reached so far.
        mode:            "crypto" or "tradier" (not used directly here; caller
                         supplies the correct atr_arr for the mode).
        cfg:             Config object with ATR_TRAIL_SWEEP_* fields.

    Returns:
        shape (N,) bool — True where the ATR trail stop fires.
    """
    n = len(close_arr)
    result = np.zeros(n, dtype=bool)
    if not getattr(cfg, "ATR_TRAIL_SWEEP_ENABLED", False):
        return result
    mult = float(getattr(cfg, "ATR_TRAIL_SWEEP_MULT", 2.0))
    arm_pct = float(getattr(cfg, "ATR_TRAIL_SWEEP_ARM_GAIN_PCT", 1.0))
    # Arm mask: position open AND max_gain reached the arm threshold
    open_mask = np.asarray(pos_open_arr, dtype=bool)
    max_gain = np.asarray(max_gain_arr, dtype=float)
    armed = open_mask & (max_gain >= arm_pct)
    if not armed.any():
        return result
    close_f = np.asarray(close_arr, dtype=float)
    entry_f = np.asarray(entry_price_arr, dtype=float)
    atr_f = np.asarray(atr_arr, dtype=float)
    side_arr = np.asarray(pos_side_arr, dtype=object)
    is_long = (side_arr == "LONG")
    valid = armed & (entry_f > 0) & (atr_f > 0) & (close_f > 0)
    # Trail levels
    trail_long = entry_f - mult * atr_f
    trail_short = entry_f + mult * atr_f
    # Stop fires
    fire_long = valid & is_long & (close_f <= trail_long)
    fire_short = valid & (~is_long) & (close_f >= trail_short)
    result = fire_long | fire_short
    return result

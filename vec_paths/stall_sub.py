"""
vec_paths/stall_sub.py — STALL_SUB exit path (vectorized).

LIVE SOURCE:
  ez_manage.py:28787-28913 inside ratio_rebalance_loop.
  Guarded by config.STALL_SUB_ENABLED (defaults True in live — getattr default).
  Fires 168 REDUCE + 31 AUGMENT events per 30-day live history window.

PURPOSE:
  Close stalled positions to free capital for high-delta symbols.
  "Stalled" means a position has been open longer than MIN_AGE_MIN minutes,
  its gain is within a near-zero band (|gain| < STALL_GAIN_ABS_MAX), AND
  price velocity (rate of change over the last VELOCITY_WINDOW bars) is below
  VELOCITY_PCT_THRESHOLD — i.e. the price has not moved meaningfully since entry.

  Unlike R1/R2 this is capital-recycling, not a loss-protection path.  It fires
  BEFORE the UNIVERSAL_NOLOSS_GATE in the rebalance cycle (see live comment:
  "Bypasses STRICT_NO_LOSS per user directive").

LIVE SIGNALS:
  - positionAmt              — position open/size check
  - gain (%)                 — P&L since open
  - opened_at timestamp      — to compute age in minutes
  - delta_bull_speed /
    delta_bear_speed (max)   — live velocity proxy from indicators_snapshot

VEC APPROXIMATION:
  Live uses delta_bull_speed / delta_bear_speed from a runtime indicators_snapshot
  that is not stored in the NPZ.  We substitute a price-based velocity:
    velocity_pct = |close[bar_idx] - close[bar_idx - VELOCITY_WINDOW]|
                   / close[bar_idx - VELOCITY_WINDOW] * 100
  This is a conservative approximation — price velocity and delta speed are
  directionally correlated.  The vec version may fire slightly more or less
  often than live depending on divergence between delta speed and raw price ROC.

  Age is approximated via bar_since_entry (provided by the engine):
    age_bars > STALL_SUB_MIN_AGE_BARS
  Crypto bars are 3m → STALL_SUB_MIN_AGE_BARS * 3 = minutes.
  Tradier bars are 5m → STALL_SUB_MIN_AGE_BARS * 5 = minutes.

NPZ FIELDS:
  close  — base timeframe OHLCV close price (always present)
  No additional NPZ fields required.

CONFIG FIELDS (all have defaults; no live config.py entry required):
  STALL_SUB_ENABLED              bool    default False
      (live defaults True via getattr fallback; vec defaults False = conservative)
  STALL_SUB_MIN_AGE_BARS         int     default 40
      crypto: 40 × 3m = 120 min; tradier: 40 × 5m = 200 min
  STALL_SUB_MAX_GAIN_PCT         float   default 3.0
      upper bound of the profitable-but-stalled gain band
  STALL_SUB_MIN_GAIN_PCT         float   default 0.1
      lower bound — positions below this (near zero or losing) are already handled
      by R1/R2/NOLOSS paths; STALL_SUB targets small-but-real profit
  STALL_SUB_VELOCITY_WINDOW      int     default 10
      look-back bars for the velocity computation
  STALL_SUB_VELOCITY_PCT_THRESHOLD float default 0.3
      price moved less than 0.3% in last VELOCITY_WINDOW bars = stall

RETURNS (check_stall_sub_exit):
  dict:
    side   — "LONG" or "SHORT"
    reason — "STALL_SUB_age={age}bars_gain={g:.2f}%_vel={v:.3f}%"
    close  — True
  or None if conditions not met.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


def check_stall_sub_exit(
    store,
    bar_idx: int,
    pos_state,
    mode: str,
    cfg,
) -> Optional[Dict[str, Any]]:
    """STALL_SUB exit — close a profitable-but-flat position to free capital.

    Args:
        store:     _NPZStore for this symbol.
        bar_idx:   Current bar index (0-based).
        pos_state: _PositionState (must be open=True with entry_price set).
                   Expected attributes: .open, .side, .gain_pct, .bar_since_entry
        mode:      "crypto" or "tradier".
        cfg:       VecConfig with STALL_SUB_* fields.

    Returns:
        dict if STALL_SUB fires, None otherwise.
        dict keys: side (str), reason (str), close (bool=True).
    """
    if not bool(getattr(cfg, "STALL_SUB_ENABLED", False)):
        return None
    if pos_state is None or not pos_state.open:
        return None
    min_age_bars = int(getattr(cfg, "STALL_SUB_MIN_AGE_BARS", 40))
    bar_age = int(getattr(pos_state, "bar_since_entry", 0))
    if bar_age < min_age_bars:
        return None
    gain = float(getattr(pos_state, "gain_pct", 0.0))
    min_gain = float(getattr(cfg, "STALL_SUB_MIN_GAIN_PCT", 0.1))
    max_gain = float(getattr(cfg, "STALL_SUB_MAX_GAIN_PCT", 3.0))
    if not (min_gain <= gain <= max_gain):
        return None
    vel_window = int(getattr(cfg, "STALL_SUB_VELOCITY_WINDOW", 10))
    vel_thr = float(getattr(cfg, "STALL_SUB_VELOCITY_PCT_THRESHOLD", 0.3))
    lookback = bar_idx - vel_window
    if lookback < 0:
        return None
    price_now = store.price(bar_idx)
    price_prev = store.price(lookback)
    if price_prev <= 0 or price_now <= 0:
        return None
    velocity_pct = abs(price_now - price_prev) / price_prev * 100.0
    if velocity_pct >= vel_thr:
        return None
    reason = (
        f"STALL_SUB_age={bar_age}bars_gain={gain:.2f}%_vel={velocity_pct:.3f}%"
    )
    return {
        "side": pos_state.side,
        "reason": reason,
        "close": True,
    }


def check_stall_sub_exit_vec(
    close_arr: np.ndarray,
    entry_price_arr: np.ndarray,
    pos_open_arr: np.ndarray,
    bar_since_entry_arr: np.ndarray,
    mode: str,
    cfg,
) -> np.ndarray:
    """Vectorized STALL_SUB exit detector.

    All input arrays must be of length N_bars (aligned to the same bar timeline).
    Each element i represents the state at bar i.

    Args:
        close_arr:          float64[N] — close price at each bar.
        entry_price_arr:    float64[N] — entry price at each bar (0.0 = no open position).
        pos_open_arr:       bool[N]    — True when a position is open at bar i.
        bar_since_entry_arr:int[N]     — how many bars ago the position was opened.
        mode:               "crypto" or "tradier".
        cfg:                VecConfig with STALL_SUB_* fields.

    Returns:
        bool[N] — True at bars where STALL_SUB exit condition is satisfied.
    """
    n = len(close_arr)
    result = np.zeros(n, dtype=bool)
    if not bool(getattr(cfg, "STALL_SUB_ENABLED", False)):
        return result
    min_age_bars = int(getattr(cfg, "STALL_SUB_MIN_AGE_BARS", 40))
    min_gain = float(getattr(cfg, "STALL_SUB_MIN_GAIN_PCT", 0.1))
    max_gain = float(getattr(cfg, "STALL_SUB_MAX_GAIN_PCT", 3.0))
    vel_window = int(getattr(cfg, "STALL_SUB_VELOCITY_WINDOW", 10))
    vel_thr = float(getattr(cfg, "STALL_SUB_VELOCITY_PCT_THRESHOLD", 0.3))
    # Guard: need at least vel_window bars of history
    if n <= vel_window:
        return result
    # --- position open mask ---
    open_mask = np.asarray(pos_open_arr, dtype=bool)
    # --- age gate ---
    age_arr = np.asarray(bar_since_entry_arr, dtype=np.int64)
    age_ok = age_arr >= min_age_bars
    # --- gain gate ---
    ep = np.asarray(entry_price_arr, dtype=np.float64)
    cl = np.asarray(close_arr, dtype=np.float64)
    safe_ep = np.where(ep > 0, ep, np.nan)
    gain_pct = (cl - safe_ep) / safe_ep * 100.0
    gain_ok = (gain_pct >= min_gain) & (gain_pct <= max_gain)
    # --- velocity gate ---
    # velocity_pct[i] = |close[i] - close[i - vel_window]| / close[i - vel_window] * 100
    cl_prev = np.empty(n, dtype=np.float64)
    cl_prev[:vel_window] = np.nan
    cl_prev[vel_window:] = cl[: n - vel_window]
    safe_cl_prev = np.where(cl_prev > 0, cl_prev, np.nan)
    velocity_pct = np.abs(cl - safe_cl_prev) / safe_cl_prev * 100.0
    vel_ok = velocity_pct < vel_thr
    # --- combine all conditions ---
    result = open_mask & age_ok & gain_ok & vel_ok
    # NaN propagation: where any input was invalid, set False
    result = result & ~np.isnan(gain_pct) & ~np.isnan(velocity_pct)
    return result.astype(bool)

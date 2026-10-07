"""
vec_paths/breakeven_gain_erosion.py — BREAKEVEN_GAIN_EROSION_STOP exit gate.

LIVE SOURCE:
  ez_positions_quick.py:14009 inside process_single_exit.
  One of the most active exit paths: 135 AUGMENT (re-opens) + 328 REDUCE events = 463
  events/30d in production.

LOGIC:
  A position previously reached a gain peak above BREAKEVEN_GAIN_EROSION_PEAK_MIN_PCT
  (e.g. +0.5%), then eroded back to within BREAKEVEN_GAIN_EROSION_BAND_PCT of zero gain
  (e.g. current gain ≤ 0.2%).  When both conditions hold the position is closed.

  Reason string format (matches live):
    BREAKEVEN_GAIN_EROSION_STOP_age{age}m_gain{gain:.2f}%
  The vec scalar version omits age (not tracked in pos_state) and uses:
    BREAKEVEN_GAIN_EROSION_STOP_peak{peak:.2f}%_gain{g:.2f}%

DIFFERENCE FROM PEAK_GIVEBACK (peak_giveback_be_erosion.py):
  - PEAK_GIVEBACK requires an absolute drop% from the peak (gain < peak - DROP_PCT).
  - check_be_erosion_exit (same file) requires gain < 0 (actual loss after fees).
  - BREAKEVEN_GAIN_EROSION_STOP only requires "gain is near zero" regardless of how
    large the drop was.  A position that peaked at +5% and now sits at +0.15% would
    fire BREAKEVEN_GAIN_EROSION but NOT PEAK_GIVEBACK (drop=4.85% > DROP_PCT but gain
    is still positive so PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN blocks it).

NPZ FIELDS NEEDED:
  close — current bar close price.  All gain arithmetic uses entry_price from pos_state.
  (No additional NPZ fields required.)

CONFIG FIELDS (all read via getattr with defaults):
  BREAKEVEN_GAIN_EROSION_ENABLED      bool  default False — master switch.
  BREAKEVEN_GAIN_EROSION_MIN_GAIN     float default 0.0   — gain floor when "eroded"
                                                            (current gain <= this to fire).
  BREAKEVEN_GAIN_EROSION_PEAK_MIN_PCT float default 0.5   — peak must have been ≥ this %.
  BREAKEVEN_GAIN_EROSION_BAND_PCT     float default 0.2   — "near breakeven" threshold;
                                                            current gain <= this value fires.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sfloat(x: Any, default: float = 0.0) -> float:
    """Safe float cast."""
    try:
        if x is None:
            return default
        v = float(x)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Scalar (per-bar, per-position) check
# ---------------------------------------------------------------------------

def check_breakeven_gain_erosion(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[Dict[str, Any]]:
    """BREAKEVEN_GAIN_EROSION_STOP scalar check.

    Args:
        store:     NPZStore for this symbol (not used directly — close price
                   is read from pos_state.gain when available, or recomputed
                   from store.price() and pos_state.entry_price).
        bar_idx:   Current bar index.
        pos_state: _PositionState — must have .open, .side, .entry_price,
                   .max_gain_pct (or .max_gain), .gain_pct (or .gain).
        mode:      "crypto" or "tradier".
        cfg:       Config object with BREAKEVEN_GAIN_EROSION_* fields.

    Returns:
        dict with keys (side, reason, close=True) if gate fires, else None.
    """
    if not bool(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_ENABLED', False)):
        return None
    if pos_state is None or not getattr(pos_state, 'open', False):
        return None
    entry_price = _sfloat(getattr(pos_state, 'entry_price', 0.0))
    if entry_price <= 0:
        return None
    # Current gain — prefer .gain_pct; fall back to .gain
    gain = _sfloat(
        getattr(pos_state, 'gain_pct', None)
        if getattr(pos_state, 'gain_pct', None) is not None
        else getattr(pos_state, 'gain', None)
    )
    # Running peak gain — prefer .max_gain_pct; fall back to .max_gain
    max_gain = _sfloat(
        getattr(pos_state, 'max_gain_pct', None)
        if getattr(pos_state, 'max_gain_pct', None) is not None
        else getattr(pos_state, 'max_gain', None)
    )
    peak_min = _sfloat(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_PEAK_MIN_PCT', 0.5))
    band_pct = _sfloat(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_BAND_PCT', 0.2))
    # Peak must have been reached
    if max_gain < peak_min:
        return None
    # Current gain must be at or below the near-zero band
    if gain > band_pct:
        return None
    side = getattr(pos_state, 'side', 'LONG')
    reason = f"BREAKEVEN_GAIN_EROSION_STOP_peak{max_gain:.2f}%_gain{gain:.2f}%"
    return {
        "side": side,
        "reason": reason,
        "close": True,
    }


# ---------------------------------------------------------------------------
# Vectorized check (batch over all bars for one symbol)
# ---------------------------------------------------------------------------

def check_breakeven_gain_erosion_vec(
    close_arr: np.ndarray,
    entry_price_arr: np.ndarray,
    max_gain_arr: np.ndarray,
    pos_open_arr: np.ndarray,
    mode: str,
    cfg: Any,
    side_arr: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Vectorized BREAKEVEN_GAIN_EROSION_STOP across all bars.

    Args:
        close_arr:        float64 array of shape (N,) — bar close prices.
        entry_price_arr:  float64 array of shape (N,) — entry price at each bar
                          (broadcast from pos_state.entry_price for open bars).
        max_gain_arr:     float64 array of shape (N,) — running peak gain % for
                          the open position at each bar.
        pos_open_arr:     bool array of shape (N,) — True when position is open.
        mode:             "crypto" or "tradier" (reserved for future mode splits).
        cfg:              Config object with BREAKEVEN_GAIN_EROSION_* fields.
        side_arr:         Optional int8/bool array — True (or 1) = LONG, False (or 0)
                          = SHORT.  When provided the gain direction is adjusted for
                          SHORT positions.  When None all bars are treated as LONG.

    Returns:
        bool ndarray of shape (N,) — True where the gate fires.

    Gain formula:
        LONG:  (close / entry_price - 1) * 100
        SHORT: (entry_price / close - 1) * 100
    """
    if not bool(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_ENABLED', False)):
        return np.zeros(len(close_arr), dtype=bool)
    peak_min = float(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_PEAK_MIN_PCT', 0.5))
    band_pct = float(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_BAND_PCT', 0.2))
    close_arr = np.asarray(close_arr, dtype=np.float64)
    entry_price_arr = np.asarray(entry_price_arr, dtype=np.float64)
    max_gain_arr = np.asarray(max_gain_arr, dtype=np.float64)
    pos_open_arr = np.asarray(pos_open_arr, dtype=bool)
    # Guard against zero entry prices
    safe_entry = np.where(entry_price_arr > 0, entry_price_arr, np.nan)
    if side_arr is not None:
        is_long = np.asarray(side_arr, dtype=bool)
        long_gain = (close_arr / safe_entry - 1.0) * 100.0
        short_gain = (safe_entry / np.where(close_arr > 0, close_arr, np.nan) - 1.0) * 100.0
        current_gain_arr = np.where(is_long, long_gain, short_gain)
    else:
        current_gain_arr = (close_arr / safe_entry - 1.0) * 100.0
    peak_ok = max_gain_arr >= peak_min
    near_zero = current_gain_arr <= band_pct
    return pos_open_arr & peak_ok & near_zero

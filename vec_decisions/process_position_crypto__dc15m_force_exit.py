"""SHARED scalar+vectorized predicate for the LIVE decision DC15M_FORCE_EXIT.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~43111-43138.

"DELTA_EXIT_DC15M_FLOOR_BREAK". Closes a LOSING position whose price has fallen
through the 15m Donchian floor (long) / risen through the 15m ceiling (short):

  FLOOR  : LONG  : dc_low_15m  > 0 AND price < dc_low_15m
           SHORT : dc_high_15m > 0 AND price > dc_high_15m
  fires iff (floor breached) AND current_gain < 0.0

The live action layer is then gated: if the account is a STRICT_NO_LOSS account
AND DELTA_ENGINE_ENABLED is False the close is SUPPRESSED (hold, let L/S ratio
hedge); otherwise the CLOSE fires. STRICT_NO_LOSS is currently eliminated
(STRICT_NO_LOSS_ACCOUNTS=[]) so the suppression is normally inert, but the core
below exposes the suppression flag so the caller can reproduce live exactly when
it is non-empty. The FIRE PREDICATE itself (floor breach + loss) is pure-given-
state and is what the vec mask computes.

CLASSIFICATION: stateful_seam. Pure per-bar over NPZ dc_low_15m/dc_high_15m +
price PLUS the per-position seam value `current_gain` (engine supplies it from
the state walk). The STRICT_NO_LOSS suppression is an account-membership gate the
caller applies on top, identical to live.
"""
from typing import Tuple
import numpy as np


def _dc15m_floor_breached(price: float, dc_low_15m: float, dc_high_15m: float,
                          is_long: bool) -> bool:
    """Pure per-bar floor/ceiling breach (no gain gate)."""
    if is_long:
        return dc_low_15m > 0 and price < dc_low_15m
    return dc_high_15m > 0 and price > dc_high_15m


def _dc15m_force_exit_fires(price: float, gain: float, dc_low_15m: float,
                            dc_high_15m: float, is_long: bool) -> bool:
    """Full live fire predicate: floor breach AND position underwater."""
    if not (gain < 0.0):
        return False
    return _dc15m_floor_breached(price, dc_low_15m, dc_high_15m, is_long)


def check_dc15m_force_exit(config, indicators: dict, is_long: bool, price: float,
                           gain: float) -> Tuple[bool, str]:
    """LIVE/scalar path. gain from per-position state. STRICT_NO_LOSS suppression by caller."""
    g = indicators or {}
    dl = float(g.get("dc_low_15m", 0) or 0)
    dh = float(g.get("dc_high_15m", 0) or 0)
    if not _dc15m_force_exit_fires(price, gain, dl, dh, is_long):
        return False, ""
    return True, f"DELTA_EXIT_DC15M_FLOOR_BREAK_g{gain:.2f}%_px{price:.6f}"


def check_dc15m_force_exit_vec(config, price_arr, gain_arr, dc_low_15m_arr,
                               dc_high_15m_arr, is_long):
    """VECTORIZED per-bar fire mask. SAME logic as _dc15m_force_exit_fires."""
    p = np.asarray(price_arr, dtype=float)
    g = np.asarray(gain_arr, dtype=float)
    dl = np.asarray(dc_low_15m_arr, dtype=float)
    dh = np.asarray(dc_high_15m_arr, dtype=float)
    loss = g < 0.0
    if is_long:
        breach = (dl > 0) & (p < dl)
    else:
        breach = (dh > 0) & (p > dh)
    return loss & breach

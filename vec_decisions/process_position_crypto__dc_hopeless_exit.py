"""SHARED scalar+vectorized predicate for the LIVE decision DC_HOPELESS_EXIT.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~41358-41381.

Closes when the position's ENTRY price is now outside the dc_4h channel —
structure failed (bought above the ceiling / sold below the floor).

  GUARD : dc_high_4h>0 AND dc_low_4h>0 AND entry_px>0
  LONG  hopeless iff entry_px > dc_high_4h
  SHORT hopeless iff entry_px < dc_low_4h
  fires iff hopeless   (age>DC_HOPELESS_EXIT_MIN_AGE_S gate applied by caller)

CLASSIFICATION: stateful_seam. Pure per-bar over NPZ dc_high_4h/dc_low_4h PLUS
the per-position seam value `entry_price`. Age gate uses opened_at (state) and is
applied by the caller, exactly like live.
"""
from typing import Tuple
import numpy as np


def _dc_hopeless_fires(entry_px: float, dc_high_4h: float, dc_low_4h: float,
                       is_long: bool) -> bool:
    """Pure per-bar fire test (caller still applies the age gate)."""
    if not (dc_high_4h > 0 and dc_low_4h > 0 and entry_px > 0):
        return False
    if is_long:
        return entry_px > dc_high_4h
    return entry_px < dc_low_4h


def check_dc_hopeless_exit(config, indicators: dict, is_long: bool, entry_px: float) -> Tuple[bool, str]:
    """LIVE/scalar path. entry_px from per-position state. age gate by caller."""
    dh = float((indicators or {}).get("dc_high_4h", 0) or 0)
    dl = float((indicators or {}).get("dc_low_4h", 0) or 0)
    if not _dc_hopeless_fires(entry_px, dh, dl, is_long):
        return False, ""
    return True, f"DC_HOPELESS_entry={entry_px:.4f}_dc=[{dl:.4f},{dh:.4f}]"


def check_dc_hopeless_exit_vec(config, entry_px_arr, dc_high_4h_arr, dc_low_4h_arr, is_long):
    """VECTORIZED per-bar fire mask (age gate separate). SAME logic."""
    ep = np.asarray(entry_px_arr, dtype=float)
    dh = np.asarray(dc_high_4h_arr, dtype=float)
    dl = np.asarray(dc_low_4h_arr, dtype=float)
    guard = (dh > 0) & (dl > 0) & (ep > 0)
    if is_long:
        return guard & (ep > dh)
    return guard & (ep < dl)

"""SHARED scalar+vectorized predicate for the LIVE decision WT15M_AGAINST.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~39697-39740.

When the 15m WT has crossed against the position, live FIRES a same-symbol HEDGE
if no hedge is already active, else FORCE-CLOSES. The hedge-vs-close branch
depends on runtime hedge-tracker state (active_hedges / _hedge_same_in_flight) —
that is NOT vectorizable (runtime). But the TRIGGER predicate is pure:

  GUARD : wt1_15m != 0 OR wt2_15m != 0
  LONG  against iff wt1_15m < wt2_15m
  SHORT against iff wt1_15m > wt2_15m

CLASSIFICATION (this module): vectorized — extracts ONLY the pure trigger
predicate. The hedge/close decision that follows is runtime_blocked (needs live
hedge tracker) and is documented in the branch map, not vectorized here.
"""
from typing import Tuple
import numpy as np


def _wt15m_against(wt1_15m: float, wt2_15m: float, is_long: bool) -> bool:
    """Pure trigger predicate. Guard mirrors live `if w1 != 0 or w2 != 0`."""
    if wt1_15m == 0 and wt2_15m == 0:
        return False
    if is_long:
        return wt1_15m < wt2_15m
    return wt1_15m > wt2_15m


def check_wt15m_against(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar TRIGGER path (hedge-vs-close action decided by caller w/ runtime state)."""
    w1 = float((indicators or {}).get("wt1_15m", 0) or 0)
    w2 = float((indicators or {}).get("wt2_15m", 0) or 0)
    if not _wt15m_against(w1, w2, is_long):
        return False, ""
    return True, f"WT15M_AGAINST_w1{w1:.1f}_w2{w2:.1f}"


def check_wt15m_against_vec(config, wt1_15m_arr, wt2_15m_arr, is_long):
    """VECTORIZED per-bar trigger mask. SAME logic as _wt15m_against."""
    w1 = np.asarray(wt1_15m_arr, dtype=float)
    w2 = np.asarray(wt2_15m_arr, dtype=float)
    guard = (w1 != 0) | (w2 != 0)
    if is_long:
        return guard & (w1 < w2)
    return guard & (w1 > w2)

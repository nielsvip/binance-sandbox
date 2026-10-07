"""SHARED scalar+vectorized predicate for the LIVE decision E_1_WT_DELTA_EXIT.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~41545-41550.

Exit when wt_composite_delta crosses a threshold against the position.

  GUARD : wt_composite_delta is not None
  LONG  fire iff delta < -E_1_EXIT_DELTA_THR   (default 50.0)
  SHORT fire iff delta > +E_1_EXIT_DELTA_THR

CLASSIFICATION: vectorized. Pure per-bar predicate on the NPZ field
wt_composite_delta. (Default-OFF via E_1_WT_EXIT_USE_DELTA_ENABLED; the enable
gate is config, not state.)
"""
from typing import Tuple
import numpy as np


def _e1_wt_delta_fires(delta: float, is_long: bool, thr: float) -> bool:
    if is_long:
        return delta < -thr
    return delta > thr


def _e1_thr(config) -> float:
    return float(getattr(config, "E_1_EXIT_DELTA_THR", 50.0))


def check_e1_wt_delta_exit(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). delta None → no fire (live guard)."""
    raw = (indicators or {}).get("wt_composite_delta", None)
    if raw is None:
        return False, ""
    delta = float(raw)
    thr = _e1_thr(config)
    if not _e1_wt_delta_fires(delta, is_long, thr):
        return False, ""
    return True, f"E_1_WT_DELTA_EXIT_delta={delta:+.0f}_thr={thr:.0f}"


def check_e1_wt_delta_exit_vec(config, delta_arr, is_long, present_mask=None):
    """VECTORIZED per-bar fire mask. present_mask mirrors the live `delta is not
    None` guard (True where the field exists); default all-present. SAME logic."""
    d = np.asarray(delta_arr, dtype=float)
    thr = _e1_thr(config)
    if is_long:
        fire = d < -thr
    else:
        fire = d > thr
    if present_mask is not None:
        fire = fire & np.asarray(present_mask, dtype=bool)
    return fire

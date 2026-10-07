"""SHARED scalar+vectorized predicate for the LIVE decision WT_PERCENTILE_EXIT.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~41457-41471.

Closes when Daily AND 4h WT percentile are both at an extreme against position.

  LONG  exit iff wt_percentile_D > OB_D (90)  AND wt_percentile_4h > OB_4H (75)
  SHORT exit iff wt_percentile_D < OS_D (10)  AND wt_percentile_4h < OS_4H (25)

CLASSIFICATION: vectorized. Pure per-bar predicate on two NPZ percentile fields.
No state required for the fire decision.
"""
from typing import Tuple
import numpy as np


def _wt_percentile_fires(pct_D: float, pct_4h: float, is_long: bool,
                         ob_D: float, ob_4h: float, os_D: float, os_4h: float) -> bool:
    if is_long:
        return pct_D > ob_D and pct_4h > ob_4h
    return pct_D < os_D and pct_4h < os_4h


def _wt_percentile_params(config):
    return (float(getattr(config, "WT_PERCENTILE_EXIT_OB_D", 90)),
            float(getattr(config, "WT_PERCENTILE_EXIT_OB_4H", 75)),
            float(getattr(config, "WT_PERCENTILE_EXIT_OS_D", 10)),
            float(getattr(config, "WT_PERCENTILE_EXIT_OS_4H", 25)))


def check_wt_percentile_exit(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path."""
    ob_D, ob_4h, os_D, os_4h = _wt_percentile_params(config)
    pD = float((indicators or {}).get("wt_percentile_D", 50) or 50)
    p4 = float((indicators or {}).get("wt_percentile_4h", 50) or 50)
    if not _wt_percentile_fires(pD, p4, is_long, ob_D, ob_4h, os_D, os_4h):
        return False, ""
    return True, f"WT_PERCENTILE_pctD={pD:.0f}_4h={p4:.0f}"


def check_wt_percentile_exit_vec(config, pct_D_arr, pct_4h_arr, is_long):
    """VECTORIZED per-bar fire mask. SAME logic."""
    pD = np.asarray(pct_D_arr, dtype=float)
    p4 = np.asarray(pct_4h_arr, dtype=float)
    ob_D, ob_4h, os_D, os_4h = _wt_percentile_params(config)
    if is_long:
        return (pD > ob_D) & (p4 > ob_4h)
    return (pD < os_D) & (p4 < os_4h)

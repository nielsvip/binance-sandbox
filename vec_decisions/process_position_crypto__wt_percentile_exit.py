"""SHARED scalar+vectorized predicate for the LIVE decision WT_PERCENTILE_EXIT.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() WT_PERCENTILE_EXIT lane
(percentile extremes + 15m WT turn-confirm); stocks twin
vec_decisions/twin_sizing_reduce.wt_percentile_fires (same shape).

Closes when Daily AND 4h WT percentile are both at an extreme against the
position AND the 15m WT confirms the turn.

  LONG  exit iff wt_percentile_D > OB_D (75)  AND wt_percentile_4h > OB_4H (55)
              AND wt1_15m < wt2_15m
  SHORT exit iff wt_percentile_D < OS_D AND wt_percentile_4h < OS_4H (25)
              AND wt1_15m > wt2_15m

Threshold defaults equal the config defaults (OB_D 75 / OB_4H 55 / OS_D
per-venue default / OS_4H 25) — never a guessed 90. Per-sym boards override
via the resolved cfg/getter at the call site.

CLASSIFICATION: vectorized. Pure per-bar predicate on NPZ percentile + WT
fields. No state required for the fire decision.
"""
from typing import Tuple
import numpy as np


def _wt_percentile_fires(pct_D: float, pct_4h: float, w1: float, w2: float,
                         is_long: bool, ob_D: float, ob_4h: float,
                         os_D: float, os_4h: float) -> bool:
    if is_long:
        return pct_D > ob_D and pct_4h > ob_4h and w1 < w2
    return pct_D < os_D and pct_4h < os_4h and w1 > w2


def _wt_percentile_params(config):
    return (float(getattr(config, "WT_PERCENTILE_EXIT_OB_D", 75)),
            float(getattr(config, "WT_PERCENTILE_EXIT_OB_4H", 55)),
            float(getattr(config, "WT_PERCENTILE_EXIT_OS_D", 10)),
            float(getattr(config, "WT_PERCENTILE_EXIT_OS_4H", 25)))


def check_wt_percentile_exit(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path."""
    ob_D, ob_4h, os_D, os_4h = _wt_percentile_params(config)
    m = indicators or {}
    pD = float(m.get("wt_percentile_D", 50) or 50)
    p4 = float(m.get("wt_percentile_4h", 50) or 50)
    w1 = float(m.get("wt1_15m", 50) or 50)
    w2 = float(m.get("wt2_15m", 50) or 50)
    if not _wt_percentile_fires(pD, p4, w1, w2, is_long, ob_D, ob_4h, os_D, os_4h):
        return False, ""
    return True, f"WT_PERCENTILE_pctD={pD:.0f}_4h={p4:.0f}"


def check_wt_percentile_exit_vec(config, pct_D_arr, pct_4h_arr, is_long,
                                 wt1_15m_arr=None, wt2_15m_arr=None):
    """VECTORIZED per-bar fire mask. SAME logic (percentile + 15m confirm)."""
    pD = np.asarray(pct_D_arr, dtype=float)
    p4 = np.asarray(pct_4h_arr, dtype=float)
    ob_D, ob_4h, os_D, os_4h = _wt_percentile_params(config)
    if wt1_15m_arr is None or wt2_15m_arr is None:
        w1 = np.full_like(pD, 0.0)
        w2 = np.full_like(pD, 100.0 if is_long else -100.0)
    else:
        w1 = np.asarray(wt1_15m_arr, dtype=float)
        w2 = np.asarray(wt2_15m_arr, dtype=float)
    ok = np.isfinite(pD) & np.isfinite(p4) & np.isfinite(w1) & np.isfinite(w2)
    if is_long:
        return ok & (pD > ob_D) & (p4 > ob_4h) & (w1 < w2)
    return ok & (pD < os_D) & (p4 < os_4h) & (w1 > w2)

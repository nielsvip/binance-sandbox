# -*- coding: utf-8 -*-
"""bottom_exit_veto — faithful numpy twin of the BOTTOM_EXIT_HTF_WT_VETO live veto.

Live: ez_manage.py process_position, three identical sites (2026-09-27 BOTTOM-EXIT FIX):
  ULTIMATE_DC breach (:46767-46782), NEWBORN_LOSS_KILL (:46869-46890), R1 breach (:47108-47126);
  tradier_manage.py process_position carries the same ULTIMATE veto (:10741).
Predicate (identical at all sites): when BOTTOM_EXIT_HTF_WT_VETO_ENABLED (default True) and HTF
WaveTrend is still WITH the position on ANY of 1h/15m/4h (wt1>wt2 long / wt1<wt2 short), the
bottom exit is SUPPRESSED (breach cleared / veto flag set). Missing indicators read 0.0
(safe_fetch_float default); all-zero reads -> no veto (strict inequalities), same as live.
Live wraps each site in try/except -> fail-open to STOP on error; the twin does the same
(exception -> all-False mask, breach stands).

Vec: R1 has no vec counterpart (R1_DC_LOW4_3M_EMERGENCY not implemented in vec) -> the twin
gates the two vec bottom exits that exist: NEWBORN_LOSS_KILL + ULTIMATE_DC hard stop in the
simulate_one bar walk. Pure functions: no state, no I/O.
"""
import numpy as np


def htf_with_mask_vec(npz, n, is_long, _safe):
    """Bool ndarray[n]: True where HTF WT is with the position (veto condition).

    Faithful vectorization of the ez veto predicate (strict >/<, 0.0 defaults, any-of-3-TFs).
    """
    try:
        w1_1h = _safe(npz, 'wt1_1h', n, 0.0)
        w2_1h = _safe(npz, 'wt2_1h', n, 0.0)
        w1_15m = _safe(npz, 'wt1_15m', n, 0.0)
        w2_15m = _safe(npz, 'wt2_15m', n, 0.0)
        w1_4h = _safe(npz, 'wt1_4h', n, 0.0)
        w2_4h = _safe(npz, 'wt2_4h', n, 0.0)
        if is_long:
            m = (w1_1h > w2_1h) | (w1_15m > w2_15m) | (w1_4h > w2_4h)
        else:
            m = (w1_1h < w2_1h) | (w1_15m < w2_15m) | (w1_4h < w2_4h)
        return np.asarray(m, dtype=bool)
    except Exception:
        return np.zeros(n, dtype=bool)


def _ff(x):
    """Scalar safe_fetch_float(x, 0.0) replica."""
    try:
        v = float(x)
        return v
    except Exception:
        return 0.0


def vetoed_scalar(wt1_1h, wt2_1h, wt1_15m, wt2_15m, wt1_4h, wt2_4h, is_long):
    """Scalar live-predicate replica for predicate-parity tests (mirrors the ez sites)."""
    a = _ff(wt1_1h)
    b = _ff(wt2_1h)
    c = _ff(wt1_15m)
    d = _ff(wt2_15m)
    e = _ff(wt1_4h)
    f = _ff(wt2_4h)
    if is_long:
        return bool((a > b) or (c > d) or (e > f))
    return bool((a < b) or (c < d) or (e < f))

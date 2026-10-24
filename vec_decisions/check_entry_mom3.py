"""MOM3 momentum entry core (USER 2026-10-24 unlock: wire missing producers).

Single source of truth for the B_MOM3 family, an exact mirror of the vector
predicate (v12_quick_engine.py:11051-11061):
  mom3 = (close_15m - close_3bar_15m) / close_3bar_15m * 100  (0.0 when c3 <= 0)
  LONG:  mom3 < MOM3_LONG_THRESHOLD  (default -1.0)
  SHORT: mom3 > MOM3_SHORT_THRESHOLD (default 1.0)
Gate switch: MOM3_ENTRY_ENABLED (caller-owned, like every family).
Thresholds are per-sym resolvable live via _cfg with the same defaults.
"""
from __future__ import annotations


def mom3_value(close: float, c3: float) -> float:
    try:
        close, c3 = float(close), float(c3)
    except (TypeError, ValueError):
        return 0.0
    if not (c3 > 0):
        return 0.0
    return (close - c3) / max(c3, 1e-9) * 100.0


def mom3_fires_scalar(close, c3, thr_long=-1.0, thr_short=1.0, is_long=True) -> bool:
    try:
        m3 = mom3_value(close, c3)
        if bool(is_long):
            return bool(m3 < float(thr_long))
        return bool(m3 > float(thr_short))
    except (TypeError, ValueError):
        return False


def mom3_mask_vec(close, c3, thr_long=-1.0, thr_short=1.0, is_long=True):
    import numpy as np

    def _a(v):
        return np.asarray(v, dtype=float)

    m3 = (_a(close) - _a(c3)) / np.maximum(_a(c3), 1e-9) * 100.0
    if bool(is_long):
        return m3 < float(thr_long)
    return m3 > float(thr_short)

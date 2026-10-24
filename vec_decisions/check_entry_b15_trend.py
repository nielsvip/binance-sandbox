"""B15 strong-trend entry core (USER 2026-10-24 unlock: wire missing producers).

Single source of truth for the B15 family, an exact mirror of the vector
predicate (v12_quick_engine.py:10658-10669):
  LONG:  dc_high_4h>0 AND close>dc_high_4h AND wt_vel_1h>2.0 AND k_1h<85
  SHORT: dc_low_4h>0 AND close<dc_low_4h AND wt_vel_1h<-2.0 AND k_1h>15
Gate switch: REENTRY_B15_STRONG_TREND_ENABLED (caller-owned; name says
reentry but the vector block gates fresh entries too).
Every input exists in the 15m lane — no fallback, exact twin.
"""
from __future__ import annotations


def b15_fires_scalar(close, dc_4h, wt_vel_1h, k_1h, is_long) -> bool:
    try:
        if bool(is_long):
            return bool(
                float(dc_4h) > 0.0
                and float(close) > float(dc_4h)
                and float(wt_vel_1h) > 2.0
                and float(k_1h) < 85.0
            )
        return bool(
            float(dc_4h) > 0.0
            and float(close) < float(dc_4h)
            and float(wt_vel_1h) < -2.0
            and float(k_1h) > 15.0
        )
    except (TypeError, ValueError):
        return False


def b15_mask_vec(close, dc_4h, wt_vel_1h, k_1h, is_long):
    import numpy as np

    def _a(v):
        return np.asarray(v, dtype=float)

    if bool(is_long):
        return (
            (_a(dc_4h) > 0.0)
            & (_a(close) > _a(dc_4h))
            & (_a(wt_vel_1h) > 2.0)
            & (_a(k_1h) < 85.0)
        )
    return (
        (_a(dc_4h) > 0.0)
        & (_a(close) < _a(dc_4h))
        & (_a(wt_vel_1h) < -2.0)
        & (_a(k_1h) > 15.0)
    )

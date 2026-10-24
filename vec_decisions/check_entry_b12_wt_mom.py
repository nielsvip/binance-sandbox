"""B12 WT-momentum entry core (USER 2026-10-24 VEC-LAST catch-up).

Single source of truth for the WT_CROSS_B12 family, an exact mirror of the
vector predicate (v12_quick_engine.py:10640-10647):
  LONG:  wt1_3m>wt2_3m AND wt1_15m>wt2_15m AND wt1_1h>wt2_1h AND wt_vel_3m>1.0
  SHORT: mirrored, wt_vel_3m<-1.0
Gate switch: REENTRY_B12_WT_MOM_ENABLED (caller-owned, like every family).
Both venues call b12_fires_scalar; the sweep engine keeps its inline copy until
the gate PASS replays scalar==vec (then it is refactored onto b12_mask_vec).
"""
from __future__ import annotations


def b12_fires_scalar(wt1_3m, wt2_3m, wt1_15m, wt2_15m, wt1_1h, wt2_1h, wt_vel_3m, is_long) -> bool:
    try:
        if bool(is_long):
            return bool(
                float(wt1_3m) > float(wt2_3m)
                and float(wt1_15m) > float(wt2_15m)
                and float(wt1_1h) > float(wt2_1h)
                and float(wt_vel_3m) > 1.0
            )
        return bool(
            float(wt1_3m) < float(wt2_3m)
            and float(wt1_15m) < float(wt2_15m)
            and float(wt1_1h) < float(wt2_1h)
            and float(wt_vel_3m) < -1.0
        )
    except (TypeError, ValueError):
        return False


def b12_mask_vec(wt1_3m, wt2_3m, wt1_15m, wt2_15m, wt1_1h, wt2_1h, wt_vel_3m, is_long):
    import numpy as np

    def _a(v):
        return np.asarray(v, dtype=float)

    if bool(is_long):
        aligned = (_a(wt1_3m) > _a(wt2_3m)) & (_a(wt1_15m) > _a(wt2_15m)) & (_a(wt1_1h) > _a(wt2_1h))
        return aligned & (_a(wt_vel_3m) > 1.0)
    aligned = (_a(wt1_3m) < _a(wt2_3m)) & (_a(wt1_15m) < _a(wt2_15m)) & (_a(wt1_1h) < _a(wt2_1h))
    return aligned & (_a(wt_vel_3m) < -1.0)

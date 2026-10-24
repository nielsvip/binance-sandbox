"""B14 HA-stack entry core (USER 2026-10-24 unlock: wire missing producers).

Single source of truth for the B14 family, an exact mirror of the vector
predicate (v12_quick_engine.py:10653-10657):
  LONG:  ha_3m==1 AND ha_15m==1 AND ha_1h==1 AND k_3m<60
  SHORT: ha_3m==-1 AND ha_15m==-1 AND ha_1h==-1 AND k_3m>40
Gate switch: REENTRY_B14_HA_TREND_ENABLED (caller-owned; name says reentry but
the vector block gates fresh entries too).
NO-3M PARITY (B12 precedent): the 15m lane carries no ha_3m/k_3m keys, so the
caller passes ha_15m/k_15m into the 3m slots and documents it. Full closure
needs 1/3/5m lane data; until then the 15m analogue fires and the template
chain scores it per sym_side like every family.
"""
from __future__ import annotations


def ha_to_num(v) -> float:
    """Live HA legs are 'green'/'red'/'neutral' strings (both venues); NPZ lanes
    carry ±1 ints. Map once here so every caller shares one coercion."""
    try:
        if isinstance(v, str):
            s = v.strip().lower()
            if s == "green":
                return 1.0
            if s == "red":
                return -1.0
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def b14_fires_scalar(ha_3m, ha_15m, ha_1h, k_3m, is_long) -> bool:
    try:
        ha_3m, ha_15m, ha_1h = ha_to_num(ha_3m), ha_to_num(ha_15m), ha_to_num(ha_1h)
        if bool(is_long):
            return bool(
                float(ha_3m) == 1.0
                and float(ha_15m) == 1.0
                and float(ha_1h) == 1.0
                and float(k_3m) < 60.0
            )
        return bool(
            float(ha_3m) == -1.0
            and float(ha_15m) == -1.0
            and float(ha_1h) == -1.0
            and float(k_3m) > 40.0
        )
    except (TypeError, ValueError):
        return False


def b14_mask_vec(ha_3m, ha_15m, ha_1h, k_3m, is_long):
    import numpy as np

    def _a(v):
        return np.asarray(v, dtype=float)

    if bool(is_long):
        return (
            (_a(ha_3m) == 1.0)
            & (_a(ha_15m) == 1.0)
            & (_a(ha_1h) == 1.0)
            & (_a(k_3m) < 60.0)
        )
    return (
        (_a(ha_3m) == -1.0)
        & (_a(ha_15m) == -1.0)
        & (_a(ha_1h) == -1.0)
        & (_a(k_3m) > 40.0)
    )

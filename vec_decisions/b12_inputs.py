"""Resolve Tradier B12 inputs using the shared LTF enable contract."""

import numpy as np


def resolve_b12_scalar(indicators, use_ltf):
    source = indicators or {}

    def present(key):
        try:
            if key not in source:
                return None
            value = source[key]
        except (KeyError, TypeError):
            return None
        return value if value is not None else None

    if use_ltf:
        w1 = present("wt1_3m")
        w2 = present("wt2_3m")
        velocity = present("wt_velocity_3m")
        if w1 is None or w2 is None or velocity is None:
            return None
    else:
        w1 = present("wt1_15m")
        w2 = present("wt2_15m")
        velocity = present("wt_velocity_15m")
        if velocity is None:
            velocity = present("wt_velocity_1h")
        if w1 is None or w2 is None or velocity is None:
            return None

    values = tuple(present(key) for key in ("wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h"))
    if any(value is None for value in values):
        return None
    return (w1, w2, *values, velocity)


def resolve_b12_arrays(npz, n, use_ltf, safe):
    zero = np.zeros(n, dtype=float)
    keys = tuple(npz.keys())
    if use_ltf:
        needed = ("wt1_3m", "wt2_3m", "wt_velocity_3m")
        if any(key not in keys or npz.get(key) is None for key in needed):
            return None
        w1_key, w2_key, velocity_key = needed
    else:
        needed = ("wt1_15m", "wt2_15m", "wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h")
        if any(key not in keys or npz.get(key) is None for key in needed):
            return None
        w1_key, w2_key, velocity_key = "wt1_15m", "wt2_15m", "wt_velocity_15m"
        if velocity_key not in keys or npz.get(velocity_key) is None:
            velocity_key = "wt_velocity_1h"
    aux_keys = ("wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h")
    if any(key not in keys or npz.get(key) is None for key in aux_keys):
        return None
    if velocity_key not in keys or npz.get(velocity_key) is None:
        return None
    arrays = (
        safe(npz, w1_key, n),
        safe(npz, w2_key, n),
        *(safe(npz, key, n) for key in aux_keys),
        safe(npz, velocity_key, n),
    )
    return tuple(np.asarray(value, dtype=float) if value is not None else zero for value in arrays)

"""Pure per-bar vector twin for ez_manage.check_dc_high_break_retest.

The live predicate searches 4h, 1h, then 15m Donchian break-and-retest
levels. NPZ-backed evaluation uses the same timeframe priority and maps the
live 3m stochastic confirmation to the available 15m stochastic arrays.
"""

import numpy as np


_TIMEFRAMES = (("4h", 2.0), ("1h", 1.5), ("15m", 1.0))


def dc_high_break_retest_mask(npz, n, is_long, cfg, safe):
    """Return (fire_mask, size_multiplier, winning_tf) for enabled B04 rows."""
    fire = np.zeros(n, dtype=bool)
    multiplier = np.ones(n, dtype=float)
    winning_tf = np.full(n, "", dtype=object)
    if not bool(cfg.REENTRY_B04_DC_RETEST_ENABLED):
        return fire, multiplier, winning_tf
    if "close" not in npz:
        return fire, multiplier, winning_tf
    price = np.asarray(safe(npz, "close", n, 0.0), dtype=float)
    k_key = "stoch_k_15m" if "stoch_k_15m" in npz else "k_15m"
    d_key = "stoch_d_15m" if "stoch_d_15m" in npz else "d_15m"
    if k_key not in npz or d_key not in npz:
        return fire, multiplier, winning_tf
    k = np.asarray(safe(npz, k_key, n, np.nan), dtype=float)
    d = np.asarray(safe(npz, d_key, n, np.nan), dtype=float)
    k_prev_key = next((key for key in ("stoch_k_15m_prev", "k_15m_prev", "stoch_k_15m_ant", "k_15m_ant") if key in npz), None)
    if k_prev_key is not None:
        k_prev = np.asarray(safe(npz, k_prev_key, n, np.nan), dtype=float)
    else:
        k_prev = np.full(n, np.nan, dtype=float)
        if n > 1:
            k_prev[1:] = k[:-1]
    valid_stoch = np.isfinite(k) & np.isfinite(d) & np.isfinite(k_prev)
    valid_price = np.isfinite(price) & (price > 0.0)
    stoch_turn = (k > d) & (k > k_prev) if is_long else (k < d) & (k < k_prev)
    for tf, size_mult in _TIMEFRAMES:
        high_key = f"dc_high_{tf}"
        low_key = f"dc_low_{tf}"
        if is_long:
            current_key, previous_key = high_key, f"{high_key}_ant"
        else:
            current_key, previous_key = low_key, f"{low_key}_ant"
        if current_key not in npz or previous_key not in npz:
            continue
        current = np.asarray(safe(npz, current_key, n, np.nan), dtype=float)
        previous = np.asarray(safe(npz, previous_key, n, np.nan), dtype=float)
        valid = np.isfinite(current) & np.isfinite(previous) & (current > 0.0) & (previous > 0.0)
        if is_long:
            expansion = (current - previous) / previous
            zone = (price >= previous * 0.99) & (price <= previous * 1.005)
        else:
            expansion = (previous - current) / previous
            zone = (price >= previous * 0.995) & (price <= previous * 1.01)
        candidate = valid & valid_price & valid_stoch & (expansion >= 0.015) & zone & stoch_turn & ~fire
        fire[candidate] = True
        multiplier[candidate] = size_mult
        winning_tf[candidate] = tf
    return fire, multiplier, winning_tf

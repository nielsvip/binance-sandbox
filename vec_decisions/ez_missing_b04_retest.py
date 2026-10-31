"""Pure per-bar vector twin for ez_manage.check_dc_high_break_retest.

The live predicate searches 4h, 1h, then 15m Donchian break-and-retest
levels. NPZ-backed evaluation uses the same timeframe priority and maps the
live 3m stochastic confirmation to the available 15m stochastic arrays.
"""

import numpy as np


_TIMEFRAMES = (("4h", 2.0), ("1h", 1.5), ("15m", 1.0))


def dc_high_break_retest_core(price, is_long, channels, k, d, k_prev):
    """Shared scalar/vector rule core; inputs may be scalars or equal-length arrays."""
    price = np.atleast_1d(np.asarray(price, dtype=float))
    k = np.broadcast_to(np.atleast_1d(np.asarray(k, dtype=float)), price.shape)
    d = np.broadcast_to(np.atleast_1d(np.asarray(d, dtype=float)), price.shape)
    k_prev = np.broadcast_to(np.atleast_1d(np.asarray(k_prev, dtype=float)), price.shape)
    fire = np.zeros(price.size, dtype=bool)
    multiplier = np.ones(price.size, dtype=float)
    winning_tf = np.full(price.size, "", dtype=object)
    expansion_pct = np.full(price.size, np.nan, dtype=float)
    retest_level = np.full(price.size, np.nan, dtype=float)
    valid_price = np.isfinite(price) & (price > 0.0)
    valid_stoch = np.isfinite(k) & np.isfinite(d) & np.isfinite(k_prev)
    stoch_turn = (k > d) & (k > k_prev) if is_long else (k < d) & (k < k_prev)
    for tf, size_mult in _TIMEFRAMES:
        current = channels.get(tf, (None, None))[0]
        previous = channels.get(tf, (None, None))[1]
        if current is None or previous is None:
            continue
        current = np.broadcast_to(np.atleast_1d(np.asarray(current, dtype=float)), price.shape)
        previous = np.broadcast_to(np.atleast_1d(np.asarray(previous, dtype=float)), price.shape)
        valid_channel = np.isfinite(current) & np.isfinite(previous) & (current > 0.0) & (previous > 0.0)
        if is_long:
            expansion = (current - previous) / previous
            zone = (price >= previous * 0.99) & (price <= previous * 1.005)
        else:
            expansion = (previous - current) / previous
            zone = (price >= previous * 0.995) & (price <= previous * 1.01)
        candidate = valid_channel & valid_price & valid_stoch & (expansion >= 0.015) & zone & stoch_turn & ~fire
        fire[candidate] = True
        multiplier[candidate] = size_mult
        winning_tf[candidate] = tf
        expansion_pct[candidate] = expansion[candidate] * 100.0
        retest_level[candidate] = previous[candidate]
    return fire, multiplier, winning_tf, expansion_pct, retest_level


def dc_high_break_retest_live_adapter(indicators, current_price, is_long):
    """Scalar ez_manage adapter using the shared 15m-lowest-TF semantics."""
    k = indicators.get("k_15m", indicators.get("stoch_k_15m"))
    d = indicators.get("d_15m", indicators.get("stoch_d_15m"))
    if k is None or d is None:
        return False, "NO_DC_RETEST", 1.0
    k_prev = indicators.get("k_15m_prev", indicators.get("stoch_k_15m_prev", k))
    channels = {}
    for tf, _ in _TIMEFRAMES:
        key = f"dc_high_{tf}" if is_long else f"dc_low_{tf}"
        current, previous = indicators.get(key), indicators.get(f"{key}_ant")
        if current is not None and previous is not None:
            channels[tf] = (current, previous)
    result = dc_high_break_retest_core(current_price, is_long, channels, k, d, k_prev)
    if not bool(result[0][0]):
        return False, "NO_DC_RETEST", 1.0
    tf = str(result[2][0]).upper()
    reason = f"DC_{'HIGH' if is_long else 'LOW'}_RETEST_{tf}_bp={result[3][0]:.1f}%_level={result[4][0]:.6f}_mult={result[1][0]:.1f}x"
    return True, reason, float(result[1][0])


def dc_high_break_retest_mask(npz, n, is_long, cfg, safe):
    """Return (fire_mask, size_multiplier, winning_tf) for enabled B04 rows."""
    channels = {}
    if not bool(cfg.REENTRY_B04_DC_RETEST_ENABLED):
        return np.zeros(n, dtype=bool), np.ones(n, dtype=float), np.full(n, "", dtype=object)
    if "close" not in npz:
        return np.zeros(n, dtype=bool), np.ones(n, dtype=float), np.full(n, "", dtype=object)
    price = np.asarray(safe(npz, "close", n, 0.0), dtype=float)
    k_key = "stoch_k_15m" if "stoch_k_15m" in npz else "k_15m"
    d_key = "stoch_d_15m" if "stoch_d_15m" in npz else "d_15m"
    if k_key not in npz or d_key not in npz:
        return np.zeros(n, dtype=bool), np.ones(n, dtype=float), np.full(n, "", dtype=object)
    k = np.asarray(safe(npz, k_key, n, np.nan), dtype=float)
    d = np.asarray(safe(npz, d_key, n, np.nan), dtype=float)
    k_prev_key = next((key for key in ("stoch_k_15m_prev", "k_15m_prev", "stoch_k_15m_ant", "k_15m_ant") if key in npz), None)
    if k_prev_key is not None:
        k_prev = np.asarray(safe(npz, k_prev_key, n, np.nan), dtype=float)
    else:
        k_prev = np.full(n, np.nan, dtype=float)
        if n > 1:
            k_prev[1:] = k[:-1]
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
        channels[tf] = (current, previous)
    fire, multiplier, winning_tf, _, _ = dc_high_break_retest_core(price, is_long, channels, k, d, k_prev)
    return fire, multiplier, winning_tf

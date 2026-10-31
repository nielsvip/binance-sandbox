"""15-minute-floor adapter for Tradier EXIT_K5M_BOUNCE_ENABLED."""
import numpy as np


def _previous_channel(npz, name, n, safe):
    prev = npz.get(f"{name}_prev")
    if prev is not None and len(prev) == n:
        return np.asarray(prev, dtype=float)
    current = np.asarray(safe(npz, name, n, 0.0), dtype=float)
    shifted = np.roll(current, 1)
    if n:
        shifted[0] = 0.0
    return shifted


def exit_k5m_bounce_fire(px, dc_low, dc_high, k, gain_pct, is_long, min_gain):
    """Scalar native predicate with 5m inputs mapped to closed 15m observations."""
    if is_long:
        return dc_low > 0 and px < dc_low and k < 30 and gain_pct >= min_gain
    return dc_high > 0 and px > dc_high and k > 70 and gain_pct >= min_gain


def exit_k5m_bounce_mask(npz, n, is_long, gain_pct, cfg, safe):
    """Return the active position's K5M bounce exit predicate on the 15m grid."""
    if not bool(getattr(cfg, "EXIT_K5M_BOUNCE_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    gain = np.asarray(gain_pct, dtype=float)
    if gain.ndim == 0:
        gain = np.full(n, float(gain), dtype=float)
    if len(gain) != n:
        return np.zeros(n, dtype=bool)
    px = np.asarray(safe(npz, "close", n, 0.0), dtype=float)
    k = np.asarray(safe(npz, "stoch_k_15m", n, 50.0), dtype=float)
    try:
        minimum_gain = float(getattr(cfg, "NOLOSS_MIN_PROFIT_PCT_TRADIER"))
    except (AttributeError, TypeError, ValueError):
        return np.zeros(n, dtype=bool)
    if is_long:
        dc = _previous_channel(npz, "dc_low_15m", n, safe)
        return (dc > 0) & (px < dc) & (k < 30) & (gain >= minimum_gain)
    dc = _previous_channel(npz, "dc_high_15m", n, safe)
    return (dc > 0) & (px > dc) & (k > 70) & (gain >= minimum_gain)


def exit_k5m_bounce_live(config, indicators, current_price, gain_pct, is_long):
    """Scalar adapter using latest completed 15m bands and current mark."""
    if not bool(getattr(config, "EXIT_K5M_BOUNCE_ENABLED", False)):
        return False
    indicators = indicators or {}
    try:
        px = float(current_price)
        gain = float(gain_pct)
        k = indicators.get("stoch_k_15m")
        if k is None:
            k = indicators.get("k_15m")
        k = float(50.0 if k is None else k)
        minimum_gain = float(getattr(config, "NOLOSS_MIN_PROFIT_PCT_TRADIER"))
        dc_low = indicators.get("dc_low_15m")
        dc_high = indicators.get("dc_high_15m")
        if is_long and dc_low is None:
            return False
        if not is_long and dc_high is None:
            return False
        return exit_k5m_bounce_fire(
            px,
            float(dc_low or 0.0),
            float(dc_high or 0.0),
            k,
            gain,
            is_long,
            minimum_gain,
        )
    except (TypeError, ValueError):
        return False

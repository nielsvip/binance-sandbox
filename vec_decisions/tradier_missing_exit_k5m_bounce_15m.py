"""15-minute-floor adapter for Tradier EXIT_K5M_BOUNCE_ENABLED."""


def exit_k5m_bounce_mask(npz, n, is_long, gain_pct, cfg, safe):
    """Return the active position's K5M bounce exit predicate on the 15m grid."""
    import numpy as np

    if not bool(getattr(cfg, "EXIT_K5M_BOUNCE_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    gain = np.asarray(gain_pct, dtype=float)
    if gain.ndim == 0:
        gain = np.full(n, float(gain), dtype=float)
    if len(gain) != n:
        return np.zeros(n, dtype=bool)
    px = safe(npz, "close", n, 0.0)
    k = safe(npz, "stoch_k_15m", n, 50.0)
    minimum_gain = float(
        getattr(cfg, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 3.0)
    )
    if is_long:
        dc_low = safe(npz, "dc_low_15m", n, 0.0)
        return (dc_low > 0) & (px < dc_low) & (k < 30) & (gain >= minimum_gain)
    dc_high = safe(npz, "dc_high_15m", n, 0.0)
    return (dc_high > 0) & (px > dc_high) & (k > 70) & (gain >= minimum_gain)


def exit_k5m_bounce_live(config, indicators, current_price, gain_pct, is_long):
    """Scalar adapter using explicit 15m fields and the native gain floor."""
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
        minimum_gain = float(
            getattr(config, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 3.0)
        )
        if is_long:
            dc = indicators.get("dc_low_15m")
            if dc is None:
                return False
            return float(dc) > 0 and px < float(dc) and k < 30 and gain >= minimum_gain
        dc = indicators.get("dc_high_15m")
        if dc is None:
            return False
        return float(dc) > 0 and px > float(dc) and k > 70 and gain >= minimum_gain
    except (TypeError, ValueError):
        return False

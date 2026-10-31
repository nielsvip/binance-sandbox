"""15-minute-floor adapter for the Tradier RULE_B_5M_EXIT predicate."""
import numpy as np


def _f(value, default=0.0):
    try:
        result = float(value)
        return result if np.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def rule_b_exit_15m_fires(get, is_long, indicators, gain_pct, field_tf="15m"):
    """Scalar native predicate with explicit 15m-floor or available 5m OHLC inputs."""
    if not bool(get("RULE_B_5M_EXIT_ENABLED", False)):
        return False, ""
    indicators = indicators or {}
    low = _f(indicators.get(f"low_{field_tf}"))
    low_prev = _f(indicators.get(f"low_{field_tf}_prev"))
    high = _f(indicators.get(f"high_{field_tf}"))
    high_prev = _f(indicators.get(f"high_{field_tf}_prev"))
    gain = _f(gain_pct, -np.inf)
    min_gain = _f(get("NOLOSS_MIN_PROFIT_PCT_TRADIER", None), np.inf)
    if gain < min_gain:
        return False, ""
    if is_long and low > 0 and low_prev > 0 and high > 0 and high_prev > 0:
        if low < low_prev and high < high_prev:
            return True, f"RULE_B_15M_LL_LH_gain:{gain:.2f}%"
    if not is_long and high > 0 and high_prev > 0 and low > 0 and low_prev > 0:
        if high > high_prev and low > low_prev:
            return True, f"RULE_B_15M_HH_HL_gain:{gain:.2f}%"
    return False, ""


def rule_b_exit_15m_mask(npz, n, is_long, cfg, gain_pct, safe, field_tf="15m"):
    """Vector mask equivalent to the scalar predicate; gain is percent per bar."""
    if not bool(getattr(cfg, "RULE_B_5M_EXIT_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    if gain_pct is None:
        return np.zeros(n, dtype=bool)
    gain = np.asarray(gain_pct, dtype=float)
    if gain.ndim == 0 or len(gain) != n:
        return np.zeros(n, dtype=bool)
    low = np.asarray(safe(npz, f"low_{field_tf}", n, 0.0), dtype=float)
    low_prev = np.asarray(safe(npz, f"low_{field_tf}_prev", n, np.nan), dtype=float)
    high = np.asarray(safe(npz, f"high_{field_tf}", n, 0.0), dtype=float)
    high_prev = np.asarray(safe(npz, f"high_{field_tf}_prev", n, np.nan), dtype=float)
    if np.all(~np.isfinite(low_prev)):
        low_prev = np.roll(low, 1)
        if n:
            low_prev[0] = low[0]
    if np.all(~np.isfinite(high_prev)):
        high_prev = np.roll(high, 1)
        if n:
            high_prev[0] = high[0]
    try:
        min_gain = float(getattr(cfg, "NOLOSS_MIN_PROFIT_PCT_TRADIER"))
    except (AttributeError, TypeError, ValueError):
        return np.zeros(n, dtype=bool)
    valid = (low > 0) & (low_prev > 0) & (high > 0) & (high_prev > 0) & (gain >= min_gain)
    if is_long:
        return valid & (low < low_prev) & (high < high_prev)
    return valid & (high > high_prev) & (low > low_prev)

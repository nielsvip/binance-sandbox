"""NumPy mirror of ez_trading_policy.check_htf_trend for ASR reentry context."""

from collections.abc import Mapping

import numpy as np


_NUMERIC_FIELDS = {
    "stoch_k_1h": ("stoch_k_1h", "k_1h", 50.0),
    "stoch_d_1h": ("stoch_d_1h", "d_1h", 50.0),
    "stoch_k_4h": ("stoch_k_4h", "k_4h", 50.0),
    "stoch_d_4h": ("stoch_d_4h", "d_4h", 50.0),
    "stoch_k_D": ("stoch_k_D", "k_D", 50.0),
    "stoch_d_D": ("stoch_d_D", "d_D", 50.0),
    "dc_basis_1h": ("dc_basis_1h", "dc_basis_1h", 0.0),
    "dc_basis_4h": ("dc_basis_4h", "dc_basis_4h", 0.0),
    "sma_200_1h": ("sma_200_1h", "sma_200_1h", 0.0),
    "high_1h": ("high_1h", "high_1h", 0.0),
    "high_1h_prev": ("high_1h_prev", "high_1h_prev", 0.0),
    "low_1h": ("low_1h", "low_1h", 0.0),
    "low_1h_prev": ("low_1h_prev", "low_1h_prev", 0.0),
    "high_4h": ("high_4h", "high_4h", 0.0),
    "high_4h_prev": ("high_4h_prev", "high_4h_prev", 0.0),
    "low_4h": ("low_4h", "low_4h", 0.0),
    "low_4h_prev": ("low_4h_prev", "low_4h_prev", 0.0),
    "rsi_1h": ("rsi_1h", "rsi_1h", 50.0),
    "rsi_4h": ("rsi_4h", "rsi_4h", 50.0),
    "mfi_1h": ("mfi_1h", "mfi_1h", 50.0),
    "mfi_4h": ("mfi_4h", "mfi_4h", 50.0),
    "mfi_D": ("mfi_D", "mfi_D", 50.0),
    "bar_direction_1h": ("bar_direction_1h", "bar_direction_1h", 0.0),
    "bar_direction_4h": ("bar_direction_4h", "bar_direction_4h", 0.0),
    "bar_direction_D": ("bar_direction_D", "bar_direction_D", 0.0),
    "bar_strength_1h": ("bar_strength_1h", "bar_strength_1h", 0.0),
    "bar_strength_4h": ("bar_strength_4h", "bar_strength_4h", 0.0),
    "bar_strength_D": ("bar_strength_D", "bar_strength_D", 0.0),
}


def _column(indicators, names, default, n):
    raw = next((indicators[name] for name in names if name in indicators), None)
    if raw is None:
        return np.full(n, default, dtype=float)
    arr = np.asarray(raw, dtype=object)
    if arr.ndim == 0:
        arr = np.full(n, arr.item(), dtype=object)
    arr = arr.reshape(-1)
    if arr.size != n:
        raise ValueError(f"{names[0]} length {arr.size} does not match n={n}")
    out = np.full(n, default, dtype=float)
    for i, value in enumerate(arr):
        try:
            number = float(value)
            if np.isfinite(number):
                out[i] = number
        except (TypeError, ValueError):
            pass
    return out


def _truth_column(indicators, key, n):
    raw = indicators.get(key, False)
    values = np.asarray(raw, dtype=object)
    if values.ndim == 0:
        values = np.full(n, values.item(), dtype=object)
    values = values.reshape(-1)
    if values.size != n:
        raise ValueError(f"{key} length {values.size} does not match n={n}")
    return np.asarray([bool(value) for value in values], dtype=bool)


def _int_column(indicators, key, n):
    raw = indicators.get(key, 0)
    values = np.asarray(raw, dtype=object)
    if values.ndim == 0:
        values = np.full(n, values.item(), dtype=object)
    values = values.reshape(-1)
    if values.size != n:
        raise ValueError(f"{key} length {values.size} does not match n={n}")
    return np.asarray([int(value or 0) for value in values], dtype=np.int64)


def check_htf_trend_array(indicators, current_price):
    """Match ez_manage.TradingPolicy.check_htf_trend including bar and flow votes."""
    if not isinstance(indicators, Mapping):
        raise ValueError("indicators must be a mapping")
    price = np.asarray(current_price, dtype=float)
    if price.ndim == 0:
        price = price.reshape(1)
    price = price.reshape(-1)
    n = price.size
    if n < 1:
        raise ValueError("current_price must be nonempty")
    cols = {key: _column(indicators, (native, alias), default, n) for key, (native, alias, default) in _NUMERIC_FIELDS.items()}
    bull = np.zeros(n, dtype=float)
    bear = np.zeros(n, dtype=float)
    for tf in ("1h", "4h", "D"):
        bull += cols[f"stoch_k_{tf}"] > cols[f"stoch_d_{tf}"]
        bear += cols[f"stoch_k_{tf}"] <= cols[f"stoch_d_{tf}"]
    for tf in ("1h", "4h", "D"):
        raw = indicators.get(f"ha_{tf}", "neutral")
        ha = np.asarray(raw, dtype=object)
        if ha.ndim == 0:
            ha = np.full(n, ha.item(), dtype=object)
        ha = ha.reshape(-1)
        if ha.size != n:
            raise ValueError(f"ha_{tf} length {ha.size} does not match n={n}")
        bull += ha == "green"
        bear += ha == "red"
    for key in ("dc_basis_1h", "dc_basis_4h", "sma_200_1h"):
        above = (cols[key] > 0.0) & (price > cols[key])
        below = (cols[key] > 0.0) & ~above
        bull += above
        bear += below
    for tf in ("1h", "4h"):
        valid = ((cols[f"high_{tf}"] > 0.0) & (cols[f"high_{tf}_prev"] > 0.0)
                 & (cols[f"low_{tf}"] > 0.0) & (cols[f"low_{tf}_prev"] > 0.0))
        bull += valid & (cols[f"high_{tf}"] > cols[f"high_{tf}_prev"]) & (cols[f"low_{tf}"] > cols[f"low_{tf}_prev"])
        bear += valid & (cols[f"high_{tf}"] < cols[f"high_{tf}_prev"]) & (cols[f"low_{tf}"] < cols[f"low_{tf}_prev"])
    for tf, weight in (("1h", 1), ("4h", 2), ("D", 3)):
        direction = cols[f"bar_direction_{tf}"]
        strength = cols[f"bar_strength_{tf}"]
        volume_confirm = _truth_column(indicators, f"bar_vol_confirm_{tf}", n)
        active = (direction != 0.0) & (strength >= 0.3)
        points = weight * (1 + volume_confirm.astype(np.int64)) * np.where(strength >= 0.6, 1.0, 0.5)
        bull += (active & (direction > 0.0)) * points
        bear += (active & (direction < 0.0)) * points
        bull += _truth_column(indicators, f"bar_swing_bull_{tf}", n) * (weight * 2)
        bear += _truth_column(indicators, f"bar_swing_bear_{tf}", n) * (weight * 2)
        streak = _int_column(indicators, f"bar_streak_{tf}", n)
        bull += (streak >= 3) * weight
        bear += (streak <= -3) * weight
        vol_spike = _truth_column(indicators, f"bar_vol_spike_{tf}", n) & (direction != 0.0)
        bull += vol_spike & (direction > 0.0) * weight
        bear += vol_spike & (direction < 0.0) * weight
    for name, weight in (("rsi_1h", 1), ("rsi_4h", 2), ("mfi_1h", 1), ("mfi_4h", 2), ("mfi_D", 2)):
        value = cols[name]
        upper, lower = ((55.0, 45.0) if name.startswith("rsi_") else (60.0, 40.0))
        bull += (value > upper) * weight
        bear += (value < lower) * weight
    score = bull - bear
    direction = np.where(score >= 5, "BULL", np.where(score <= -5, "BEAR", "NEUTRAL"))
    return direction.astype(object), score

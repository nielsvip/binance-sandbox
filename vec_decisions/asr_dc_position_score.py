"""Shared scalar and NumPy implementation of the ASR DC-position score block."""
import numpy as np

from vec_decisions.asr_input_adapter import paired_npz_view


_TFS = ("3m", "15m", "1h", "4h", "D")


def _dc_position(price, low, high):
    if high <= low or low <= 0:
        return 0.5
    return max(0.0, min(1.0, (price - low) / (high - low)))


def apply_dc_position_score_scalar(score, reasons, indicators, current_price, is_long):
    """Mirror native cumulative and per-timeframe DC-position score ordering."""
    ind = indicators or {}
    position = {}
    for tf in _TFS:
        low = _float(ind.get(f"dc_low_{tf}"), 0.0)
        high = _float(ind.get(f"dc_high_{tf}"), 0.0)
        position[tf] = _dc_position(current_price, low, high) if low and high else 0.5
    cumulative = (
        position["3m"] * 0.15
        + position["15m"] * 0.25
        + position["1h"] * 0.30
        + position["4h"] * 0.20
        + position["D"] * 0.10
    )
    result = float(score)
    if is_long:
        if cumulative < 0.20:
            result += 6.0
            reasons.append(f"DC_Bottom({cumulative:.2f})")
        elif cumulative < 0.35:
            result += 3.0
            reasons.append(f"DC_Low({cumulative:.2f})")
        elif cumulative > 0.80:
            result -= 5.0
            reasons.append(f"DC_Top({cumulative:.2f})")
        elif cumulative > 0.65:
            result -= 2.0
            reasons.append(f"DC_High({cumulative:.2f})")
        for tf, threshold, amount in (("15m", .20, 3), ("1h", .25, 2), ("4h", .20, 2)):
            if position[tf] < threshold:
                result += amount
        for tf, threshold, amount in (("15m", .85, -3), ("1h", .85, -2)):
            if position[tf] > threshold:
                result += amount
    else:
        if cumulative > 0.80:
            result += 6.0
            reasons.append(f"DC_Top({cumulative:.2f})")
        elif cumulative > 0.65:
            result += 3.0
            reasons.append(f"DC_High({cumulative:.2f})")
        elif cumulative < 0.20:
            result -= 5.0
            reasons.append(f"DC_Bottom({cumulative:.2f})")
        elif cumulative < 0.35:
            result -= 2.0
            reasons.append(f"DC_Low({cumulative:.2f})")
        for tf, threshold, amount in (("15m", .80, 3), ("1h", .75, 2), ("4h", .80, 2)):
            if position[tf] > threshold:
                result += amount
        for tf, threshold, amount in (("15m", .15, -3), ("1h", .15, -2)):
            if position[tf] < threshold:
                result += amount
    return result, reasons


def apply_dc_position_score_vec(npz, n, is_long, score=None, current_price=None, use_ltf=False):
    """Vector twin for causal per-bar close and five timeframe channel arrays."""
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if result.shape != (n,):
        raise ValueError("score must have shape (n,)")
    npz = paired_npz_view(npz, bool(use_ltf))
    price = _column(npz, "close", n, 0.0) if current_price is None else _as_column(current_price, n, 0.0)
    positions = {}
    for tf in _TFS:
        low = _column(npz, f"dc_low_{tf}", n, 0.0)
        high = _column(npz, f"dc_high_{tf}", n, 0.0)
        valid = (low != 0) & (high != 0)
        ratio = np.divide(price - low, high - low, out=np.full(n, np.nan), where=(high - low) != 0)
        # Native `max(0, min(1, nan))` yields 1.0 for a computed NaN.
        pos = np.where((high <= low) | (low <= 0), 0.5, np.where(np.isnan(ratio), 1.0, np.clip(ratio, 0.0, 1.0)))
        positions[tf] = np.where(valid, pos, 0.5)
    cumulative = positions["3m"] * .15 + positions["15m"] * .25 + positions["1h"] * .30 + positions["4h"] * .20 + positions["D"] * .10
    masks = {}

    def add(name, mask, amount):
        mask = np.asarray(mask, dtype=bool)
        masks[name] = mask
        result[:] += mask.astype(float) * amount

    if is_long:
        bottom = cumulative < .20
        low_cum = ~bottom & (cumulative < .35)
        top = cumulative > .80
        high_cum = ~top & (cumulative > .65)
        add("DC_CUM_BOTTOM", bottom, 6)
        add("DC_CUM_LOW", low_cum, 3)
        add("DC_CUM_TOP", top, -5)
        add("DC_CUM_HIGH", high_cum, -2)
        for tf, threshold, amount in (("15m", .20, 3), ("1h", .25, 2), ("4h", .20, 2)):
            add(f"DC_{tf.upper()}_LOW", positions[tf] < threshold, amount)
        for tf, threshold, amount in (("15m", .85, -3), ("1h", .85, -2)):
            add(f"DC_{tf.upper()}_HIGH", positions[tf] > threshold, amount)
    else:
        top = cumulative > .80
        high_cum = ~top & (cumulative > .65)
        bottom = (cumulative < .20) & ~(top | high_cum)
        low_cum = (cumulative < .35) & ~(top | high_cum | bottom)
        add("DC_CUM_TOP", top, 6)
        add("DC_CUM_HIGH", high_cum, 3)
        add("DC_CUM_BOTTOM", bottom, -5)
        add("DC_CUM_LOW", low_cum, -2)
        for tf, threshold, amount in (("15m", .80, 3), ("1h", .75, 2), ("4h", .80, 2)):
            add(f"DC_{tf.upper()}_HIGH", positions[tf] > threshold, amount)
        for tf, threshold, amount in (("15m", .15, -3), ("1h", .15, -2)):
            add(f"DC_{tf.upper()}_LOW", positions[tf] < threshold, amount)
    masks["DC_CUMULATIVE"] = cumulative
    return result, masks


def _float(value, default):
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _column(npz, key, n, default):
    if key not in npz:
        return np.full(n, default, dtype=float)
    try:
        array = np.asarray(npz[key], dtype=float).reshape(-1)
        return array if array.size == n else np.full(n, default, dtype=float)
    except (TypeError, ValueError):
        return np.full(n, default, dtype=float)


def _as_column(value, n, default):
    try:
        array = np.asarray(value, dtype=float).reshape(-1)
        return array if array.size == n else np.full(n, default, dtype=float)
    except (TypeError, ValueError):
        return np.full(n, default, dtype=float)

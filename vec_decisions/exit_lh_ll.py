"""Shared EXIT_LH_LL gate for live managers and the vector engine."""

import numpy as np


TIMEFRAMES = ("15m", "1h", "4h", "D")
SWITCHES = {
    tf: f"EXIT_BLOCKER_LH_LL_{tf.upper()}_ENABLED" for tf in TIMEFRAMES
}


def enabled_timeframes(flags, legacy_enabled=False):
    selected = {tf for tf in TIMEFRAMES if bool(flags.get(tf, False))}
    if legacy_enabled:
        selected.add("15m")
    return tuple(tf for tf in TIMEFRAMES if tf in selected)


def _is_structure(value, word, code):
    try:
        return bool(float(value) == code)
    except (TypeError, ValueError):
        return str(value or "").strip().upper() in (word, str(code))


def live_pass(indicators, enabled, is_long):
    if not indicators or not enabled:
        return True
    for tf in enabled:
        peak = indicators.get(f"wt_peak_structure_{tf}", "")
        trough = indicators.get(f"wt_trough_structure_{tf}", "")
        low = _number(indicators.get(f"low_{tf}"))
        low_prev = _number(indicators.get(f"low_{tf}_prev"))
        high = _number(indicators.get(f"high_{tf}"))
        high_prev = _number(indicators.get(f"high_{tf}_prev"))
        forming_ll = (low > 0 and low_prev > 0 and low < low_prev) or (
            high > 0 and high_prev > 0 and high < high_prev
        )
        closed_lh = _is_structure(peak, "LH", -1)
        closed_hl = closed_lh or _is_structure(trough, "HL", 1)
        if ((closed_lh or forming_ll) if is_long else (closed_hl or forming_ll)):
            return True
    return False


def _number(value):
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _matches(values, word, code):
    values = np.asarray(values)
    if values.dtype.kind in "OUS":
        text = np.char.upper(values.astype(str))
        return (text == word) | (text == str(code))
    return values == code


def blocked_mask(npz, n, enabled, is_long, safe):
    """Return a bool array for bars where every enabled timeframe blocks."""
    if not enabled:
        return np.zeros(n, dtype=bool)
    passed = np.zeros(n, dtype=bool)
    for tf in enabled:
        peak = safe(npz, f"wt_peak_structure_{tf}", n, 0.0)
        trough = safe(npz, f"wt_trough_structure_{tf}", n, 0.0)
        low = safe(npz, f"low_{tf}", n, 0.0)
        low_prev = safe(npz, f"low_{tf}_prev", n, 0.0)
        high = safe(npz, f"high_{tf}", n, 0.0)
        high_prev = safe(npz, f"high_{tf}_prev", n, 0.0)
        forming_ll = ((low > 0) & (low_prev > 0) & (low < low_prev)) | (
            (high > 0) & (high_prev > 0) & (high < high_prev)
        )
        closed_lh = _matches(peak, "LH", -1)
        closed_hl = closed_lh | _matches(trough, "HL", 1)
        if is_long:
            passed |= closed_lh | forming_ll
        else:
            passed |= closed_hl | forming_ll
    return ~passed

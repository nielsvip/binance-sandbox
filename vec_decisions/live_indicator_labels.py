"""Decode stored NPZ enum columns to the labels emitted by live indicators."""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Integral

_TIMEFRAMES = {"1m", "3m", "5m", "15m", "1h", "4h", "D", "W", "M"}
_ENUMS = {
    "wt_cross": {-1: "BEAR", 0: None, 1: "BULL"},
    "wt_divergence": {
        -2: "HIDDEN_BEAR",
        -1: "BEAR",
        0: None,
        1: "BULL",
        2: "HIDDEN_BULL",
    },
    "wt_momentum_state": {
        -2: "EXHAUST_DOWN",
        -1: "IMPULSE_DOWN",
        0: "EXHAUST_DOWN",
        1: "IMPULSE_UP",
        2: "EXHAUST_UP",
    },
    "wt_peak_structure": {-1: "LH", 0: None, 1: "HH"},
    "wt_trough_structure": {-1: "LL", 0: None, 1: "HL"},
    "wt_wave_phase": {-1: "CONTRACTING", 0: "TRANSITIONING", 1: "EXPANDING"},
    "wt_signal": {-1: "SELL", 0: "NEUTRAL", 1: "BUY"},
    "ha": {-1: "red", 0: "neutral", 1: "green"},
}
_BOOLS = ("wt_cross_rising", "wt_extreme")
_LEGACY_LABELS = {
    "wt_cross": {"NONE": None},
    "wt_divergence": {"": None, "NEUTRAL": None},
    "wt_momentum_state": {"NEUTRAL": "EXHAUST_DOWN"},
    "wt_peak_structure": {"": None, "NEUTRAL": None},
    "wt_trough_structure": {"": None, "NEUTRAL": None},
    "wt_wave_phase": {"NEUTRAL": "TRANSITIONING"},
    "wt_signal": {"BULL": "BUY", "BEAR": "SELL"},
}


def _enum_name(key: str) -> str | None:
    for name in _ENUMS:
        if key.startswith(name + "_") and key[len(name) + 1 :] in _TIMEFRAMES:
            return name
    return None


def decode_live_indicator_labels(indicators: Mapping) -> dict:
    """Return a copy whose encoded enum fields match live scalar label types."""
    result = dict(indicators)
    for key, value in tuple(result.items()):
        if not isinstance(key, str):
            continue
        family = _enum_name(key)
        if (
            family is not None
            and isinstance(value, Integral)
            and not isinstance(value, bool)
        ):
            mapping = _ENUMS[family]
            if int(value) not in mapping:
                raise ValueError(f"{key}: unsupported live indicator enum {value!r}")
            result[key] = mapping[int(value)]
        elif family is not None and isinstance(value, str):
            result[key] = _LEGACY_LABELS.get(family, {}).get(value, value)
        elif any(
            key.startswith(name + "_") and key[len(name) + 1 :] in _TIMEFRAMES
            for name in _BOOLS
        ) and isinstance(value, Integral):
            if int(value) not in (0, 1):
                raise ValueError(f"{key}: expected encoded boolean 0/1, got {value!r}")
            result[key] = bool(value)
    for tf in _TIMEFRAMES:
        key = f"wt_structure_{tf}"
        value = result.get(key)
        if isinstance(value, Integral) and not isinstance(value, bool):
            peak = result.get(f"wt_peak_structure_{tf}")
            trough = result.get(f"wt_trough_structure_{tf}")
            if peak in ("HH", "LH"):
                result[key] = peak
            elif trough in ("HL", "LL"):
                result[key] = trough
            elif int(value) == 0:
                result[key] = None
            else:
                raise ValueError(
                    f"{key}: cannot decode {value!r} without source structure labels"
                )
    return result

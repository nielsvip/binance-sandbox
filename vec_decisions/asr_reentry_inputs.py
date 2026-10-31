"""Bind causal lifecycle, event-cache, volume and HTF trend inputs for ASR reentry."""

from collections.abc import Mapping

import numpy as np

from vec_decisions.asr_htf_trend import check_htf_trend_array
from vec_decisions.asr_ordered_composer import bind_asr_npz_arrays


def _cfg(cfg, key, default):
    return cfg.get(key, default) if isinstance(cfg, Mapping) else getattr(cfg, key, default)


def _array(values, key, n, dtype=float):
    if key not in values:
        raise ValueError(f"ASR reentry requires explicit {key}")
    try:
        result = np.asarray(values[key], dtype=dtype)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid ASR reentry input {key}") from exc
    if result.ndim == 0:
        raise ValueError(f"ASR reentry input {key} must be a causal per-bar array")
    result = result.reshape(-1)
    if result.size != n:
        raise ValueError(f"{key} length {result.size} does not match n={n}")
    return result


def _source_number(values, key, n, default):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=float)
    values = np.asarray(raw, dtype=object)
    if values.ndim == 0:
        values = np.full(n, values.item(), dtype=object)
    values = values.reshape(-1)
    if values.size != n:
        raise ValueError(f"{key} length {values.size} does not match n={n}")
    result = np.full(n, default, dtype=float)
    for i, value in enumerate(values):
        try:
            number = float(value)
            if np.isfinite(number):
                result[i] = number
        except (TypeError, ValueError):
            pass
    return result


def bind_asr_reentry_inputs(npz, cfg, lifecycle, n, is_long, current_price, true_lag,
                            prev_cross_price, bounce_reentry_k_reset, arrays_bound=False):
    """Build inputs for `apply_asr_reentry_entry_stage` without state invention.

    Volume fallback 1.0 and per-field oscillator defaults match the native read
    sites. Tick lag, previous WT cross price, and persisted bounce-reset state
    must be supplied by the causal caller, since a symbol NPZ cannot provide them.
    """
    n = int(n)
    if n < 1 or not isinstance(lifecycle, Mapping):
        raise ValueError("valid n and explicit lifecycle mapping are required")
    selected = dict(npz) if arrays_bound else bind_asr_npz_arrays(npz, cfg)
    current = _array({"current_price": current_price}, "current_price", n)
    lag = _array({"true_lag": true_lag}, "true_lag", n)
    previous_cross = _array({"prev_cross_price": prev_cross_price}, "prev_cross_price", n)
    if bounce_reentry_k_reset is None:
        raise ValueError("persisted bounce_reentry_k_reset is required")
    position_amt = _array(lifecycle, "position_amt", n)
    gain = _array(lifecycle, "gain", n)
    reduction_price = _array(lifecycle, "last_reduction_price", n)
    reduction_minutes = _array(lifecycle, "minutes_since_reduction", n)
    rel3 = _source_number(selected, "relative_volume_3m", n, 1.0)
    rel15 = _source_number(selected, "relative_volume_15m", n, 1.0)
    minimum_volume = float(_cfg(cfg, "ENTRY_VOL_MIN_RATIO", 1.0))
    volume_ok = (rel3 >= minimum_volume) | (rel15 >= minimum_volume)
    volume_strong = (rel3 >= 1.5) | (rel15 >= 1.5)
    trend_input = dict(selected)
    trend_input["close"] = current
    trend, trend_score = check_htf_trend_array(trend_input, current)
    indicators = dict(selected)
    context = {
        "current_price": current,
        "true_lag": lag,
        "prev_cross_price": previous_cross,
        "position_amt": position_amt,
        "pnl_pct": gain,
        "last_reduction_price": reduction_price,
        "minutes_since_reduction": reduction_minutes,
        "vol_ok": volume_ok,
        "vol_strong": volume_strong,
        "rel_vol_3m": rel3,
        "trend_is_bullish": trend == "BULL",
        "trend_is_bearish": trend == "BEAR",
        "bounce_reentry_k_reset": bool(bounce_reentry_k_reset),
    }
    return {"indicators": indicators, "context": context, "htf_trend": trend,
            "htf_trend_score": trend_score, "relative_volume_15m": rel15}

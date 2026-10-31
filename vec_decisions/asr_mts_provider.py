"""NumPy implementation of ez_positions_quick.analyze_multi_tf_state."""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from vec_decisions.asr_ordered_composer import bind_asr_npz_arrays


_TFS = ("1m", "3m", "15m", "1h", "4h", "D")
_DEFAULT_WEIGHTS = {"1m": 1.0, "3m": 2.0, "15m": 3.0, "1h": 5.0, "4h": 8.0, "D": 5.0}


def _cfg(cfg, name, default):
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _array(source, key, n, default, dtype=float):
    if key not in source:
        return np.full(n, default, dtype=dtype)
    value = np.asarray(source[key])
    if value.ndim == 0:
        return np.full(n, value.item(), dtype=dtype)
    value = value.reshape(-1)
    if value.size != n:
        raise ValueError(f"{key}: length {value.size} does not match n={n}")
    if dtype is object:
        return value.astype(object, copy=False)
    if dtype is bool:
        return np.fromiter((bool(item) for item in value), dtype=bool, count=n)
    return value.astype(dtype, copy=False)


def _truth(value):
    return np.fromiter((bool(item) for item in np.asarray(value, dtype=object)), dtype=bool, count=len(value))


def _source_clamp(value, low, high):
    """Match max(low, min(high, value)), including native NaN behavior."""
    arr = np.asarray(value, dtype=float)
    bounded_high = np.where(np.isnan(arr), high, np.minimum(high, arr))
    return np.maximum(low, bounded_high)

def _source_min_first(bound, value):
    arr = np.asarray(value, dtype=float)
    return np.where(np.isnan(arr), bound, np.minimum(bound, arr))

def _source_max_first(bound, value):
    arr = np.asarray(value, dtype=float)
    return np.where(np.isnan(arr), bound, np.maximum(bound, arr))


def analyze_mts_state_array(npz, *, cfg, is_long, n=None, external_context=None):
    """Return the native MTS fields needed by ASR as arrays.

    `external_context` may provide causal 0dc_* overlays from the same native
    producer. When absent the function uses exactly analyze_multi_tf_state's
    `.get(..., source_default)` behavior; it never derives these fields from
    unrelated NPZ channels.
    """
    selected = bind_asr_npz_arrays(npz, cfg)
    if n is None:
        n = len(np.asarray(selected["timestamps"])) if "timestamps" in selected else 0
    n = int(n)
    if n < 1:
        raise ValueError("n must be positive")
    context = external_context or {}
    if not isinstance(context, Mapping):
        raise TypeError("external_context must be a mapping")
    missing_inputs = set()
    def get(key, default, dtype=float):
        source = context if key.startswith("0dc_") and key in context else selected
        if key not in source:
            missing_inputs.add(key)
        return _array(source, key, n, default, dtype=dtype)

    tot_b = np.zeros(n, dtype=float)
    tot_e = np.zeros(n, dtype=float)
    tot_d = np.zeros(n, dtype=float)
    tot_r = np.zeros(n, dtype=float)
    total_w = 0.0
    extreme = np.zeros(n, dtype=np.int64)
    htf_bullish = np.zeros(n, dtype=np.int64)
    breakdown = {}
    for tf in _TFS:
        w = float(_cfg(cfg, f"MTS_WEIGHT_{tf}", _DEFAULT_WEIGHTS[tf]))
        k = get(f"k_{tf}", 50.0)
        d = get(f"d_{tf}", 50.0)
        kp = get(f"k_{tf}_prev", 50.0) if f"k_{tf}_prev" in selected else k
        wt1 = get(f"wt1_{tf}", 0.0)
        wt2 = get(f"wt2_{tf}", 0.0)
        wt_score = wt1 - wt2
        cross = get(f"wt_cross_{tf}", None, dtype=object)
        xval = get(f"wt_cross_value_{tf}", None, dtype=object)
        xprev = get(f"wt_cross_prev_value_{tf}", None, dtype=object)
        rising = get(f"wt_cross_rising_{tf}", None, dtype=object)
        vel = get(f"wt_velocity_{tf}", 0.0)
        bars_ago = get(f"wt_cross_bars_ago_{tf}", 999.0)
        divergence = get(f"wt_divergence_{tf}", "", dtype=object)
        momentum = get(f"wt_momentum_state_{tf}", "", dtype=object)
        bullish_raw = get(f"wt_bullish_{tf}", None, dtype=object)
        dc_raw = get(f"dc_position_{tf}", 0.5)
        dc_width = get(f"dc_width_{tf}", 0.0)
        k = np.asarray(k, dtype=float)
        d = np.asarray(d, dtype=float)
        kp = np.asarray(kp, dtype=float)
        wt_score = np.asarray(wt_score, dtype=float)
        vel = np.asarray(vel, dtype=float)
        bars_ago = np.asarray(bars_ago, dtype=float)
        dc_raw = np.asarray(dc_raw, dtype=float)
        dc_pos = np.where(dc_raw > 1.0, dc_raw / 100.0, dc_raw)
        dc_pos = _source_clamp(dc_pos, 0.0, 1.0)
        rising = _truth(rising)
        bullish = _truth(bullish_raw)
        cross_bull = np.asarray(cross, dtype=object) == "BULL"
        cross_bear = np.asarray(cross, dtype=object) == "BEAR"
        xval_num = np.fromiter((float(x) if x is not None else np.nan for x in xval), dtype=float, count=n)
        xprev_num = np.fromiter((float(x) if x is not None else np.nan for x in xprev), dtype=float, count=n)
        has_xval = np.fromiter((x is not None for x in xval), dtype=bool, count=n)
        has_xprev = np.fromiter((x is not None for x in xprev), dtype=bool, count=n)
        k_rising = k > kp
        k_extreme = (k < 10.0) if is_long else (k > 90.0)
        k_zone = (k < 30.0) if is_long else (k > 70.0)
        extreme += k_extreme.astype(np.int64)
        htf_bullish += bullish.astype(np.int64)
        b = np.zeros(n, dtype=float)
        e = np.zeros(n, dtype=float)
        if is_long:
            b += np.where(k_extreme, 20.0, 0.0) + np.where(k_zone, 10.0, 0.0)
            b += np.where(cross_bull & (bars_ago < 5), 15.0, 0.0)
            b += np.where(has_xval & (xval_num < -40), 15.0, np.where(has_xval & (xval_num < -20), 8.0, 0.0))
            b += np.where(rising, 10.0, 0.0)
            b += np.where((divergence == "BULL") | (divergence == "HIDDEN_BULL"), 20.0, 0.0)
            b += np.where(momentum == "EXHAUST_DOWN", 10.0, 0.0)
            b += np.where((vel > 0) & k_rising, 5.0, 0.0)
            b += np.where(dc_pos < 0.10, 25.0, np.where(dc_pos < 0.20, 18.0, np.where(dc_pos < 0.30, 12.0, np.where(dc_pos < 0.40, 5.0, np.where(dc_pos > 0.80, -10.0, 0.0)))))
            e += np.where(has_xval & has_xprev & (xval_num > xprev_num), 15.0, 0.0)
            e += np.where(bars_ago < 3, 15.0, np.where(bars_ago < 10, 5.0, 0.0))
            e += np.where(rising, 10.0, 0.0) + np.where((dc_pos < 0.25) & bullish, 15.0, 0.0)
            r = (1.0 - dc_pos) * 100.0
            directional = np.where(bullish, 30.0, 0.0)
        else:
            b += np.where(k_extreme, 20.0, 0.0) + np.where(k_zone, 10.0, 0.0)
            b += np.where(cross_bear & (bars_ago < 5), 15.0, 0.0)
            b += np.where(has_xval & (xval_num > 40), 15.0, np.where(has_xval & (xval_num > 20), 8.0, 0.0))
            b += np.where(rising, 10.0, 0.0)
            b += np.where((divergence == "BEAR") | (divergence == "HIDDEN_BEAR"), 20.0, 0.0)
            b += np.where(momentum == "EXHAUST_UP", 10.0, 0.0)
            b += np.where((vel < 0) & ~k_rising, 5.0, 0.0)
            b += np.where(dc_pos > 0.90, 25.0, np.where(dc_pos > 0.80, 18.0, np.where(dc_pos > 0.70, 12.0, np.where(dc_pos > 0.60, 5.0, np.where(dc_pos < 0.20, -10.0, 0.0)))))
            e += np.where(has_xval & has_xprev & (xval_num < xprev_num), 15.0, 0.0)
            e += np.where(bars_ago < 3, 15.0, np.where(bars_ago < 10, 5.0, 0.0))
            e += np.where(rising, 10.0, 0.0) + np.where((dc_pos > 0.75) & ~bullish, 15.0, 0.0)
            r = dc_pos * 100.0
            directional = np.where(bullish, 30.0, 0.0)
        directional += np.where(wt_score > 10, 20.0, np.where(wt_score > 0, 10.0, np.where(wt_score < -10, -20.0, np.where(wt_score < 0, -10.0, 0.0))))
        directional += np.where(k > d, 10.0, -10.0)
        directional += np.where(vel > 2, 15.0, np.where(vel < -2, -15.0, 0.0))
        r = np.where(dc_width > 5.0, r * 1.3, np.where(dc_width < 2.0, r * 0.5, r))
        tot_b += b * w
        tot_e += e * w
        tot_d += directional * w
        tot_r += r * w
        total_w += w
        breakdown[tf] = {"bottom": b, "entry": e, "direction": directional, "range": r,
                         "k": k, "d": d, "dc_pos": dc_pos, "dc_width": dc_width,
                         "wt_score": wt_score, "wt_bullish": bullish, "wt_velocity": vel,
                         "wt_bars_ago": bars_ago}
    if total_w > 0:
        bottom = tot_b / total_w
        entry = tot_e / total_w
        direction = tot_d / total_w
        range_score = tot_r / total_w
    else:
        bottom = np.zeros(n); entry = np.zeros(n); direction = np.zeros(n); range_score = np.full(n, 50.0)
    details = np.full(n, "", dtype=object)
    bottom *= np.where(extreme >= 4, 2.0, np.where(extreme >= 3, 1.5, 1.0))
    entry += htf_bullish * 5.0
    bottom = _source_clamp(bottom, -100.0, 100.0)
    entry = _source_clamp(entry, -100.0, 100.0)
    direction = _source_clamp(direction, -100.0, 100.0)
    htf_pos = get("0dc_htf_pos", 0.5)
    ltf_pos = get("0dc_ltf_pos", 0.5)
    moment = get("0dc_moment", 0.0)
    expansion = get("0dc_expansion", 1.0)
    width = get("0dc_width_composite", 5.0)
    if is_long:
        bottom += np.where((htf_pos < .30) & (ltf_pos < .30), 15.0, np.where((htf_pos < .40) & (ltf_pos < .40), 8.0, 0.0))
        entry += np.where((ltf_pos > htf_pos) & (htf_pos < .30), 10.0, 0.0)
        bottom -= np.where(htf_pos > .80, 10.0, 0.0)
        bottom += np.where(moment < -30.0, 8.0, 0.0)
    else:
        bottom += np.where((htf_pos > .70) & (ltf_pos > .70), 15.0, np.where((htf_pos > .60) & (ltf_pos > .60), 8.0, 0.0))
        entry += np.where((ltf_pos < htf_pos) & (htf_pos > .70), 10.0, 0.0)
        bottom -= np.where(htf_pos < .20, 10.0, 0.0)
        bottom += np.where(moment > 30.0, 8.0, 0.0)
    entry += np.where(expansion > 1.2, 5.0, 0.0)
    bottom += np.where(expansion < .7, 5.0, 0.0)
    bottom = _source_clamp(bottom, -100.0, 100.0)
    entry = _source_clamp(entry, -100.0, 100.0)
    gain = _source_min_first(100.0, width * 8.0) * (range_score / 50.0)
    gain = np.minimum(100.0, _source_max_first(0.0, gain))
    gain = np.minimum(100.0, gain * np.where(np.abs(direction) > 50.0, 1.2, 1.0))
    size = _source_clamp(0.5 + gain / 50.0, 0.3, 3.0)
    conv_bottom = np.zeros(n, dtype=np.int64); conv_top = np.zeros(n, dtype=np.int64)
    conv_bottom_score = np.zeros(n, dtype=float); conv_top_score = np.zeros(n, dtype=float)
    conv_bottom_names = [[] for _ in range(n)]; conv_top_names = [[] for _ in range(n)]
    for tf in _TFS:
        tfd = breakdown[tf]
        dc_p = tfd["dc_pos"]; k = tfd["k"]; wt_sc = tfd["wt_score"]; wt_ba = tfd["wt_bars_ago"]; bull = tfd["wt_bullish"]
        sigs_b = (dc_p < .15).astype(int) + (wt_sc < -15).astype(int) + (k < 25).astype(int) + ((wt_ba < 12) & bull).astype(int)
        mask_b = sigs_b >= 2
        conv_bottom += mask_b.astype(int)
        conv_bottom_score += np.where(mask_b, np.minimum(1.0, sigs_b / 4.0) * (1.0 - dc_p / max(.15, .01)), 0.0)
        sigs_t = (dc_p > .85).astype(int) + (wt_sc > 15).astype(int) + (k > 75).astype(int) + ((wt_ba < 12) & ~bull).astype(int)
        mask_t = sigs_t >= 2
        conv_top += mask_t.astype(int)
        conv_top_score += np.where(mask_t, np.minimum(1.0, sigs_t / 4.0) * (1.0 - (1.0 - dc_p) / max(.15, .01)), 0.0)
        for i in np.flatnonzero(mask_b): conv_bottom_names[i].append(tf)
        for i in np.flatnonzero(mask_t): conv_top_names[i].append(tf)
    scalp_tf = str(_cfg(cfg, "TF_SCALP", "3m"))
    focus = get(f"dc_position_{scalp_tf}", .5)
    focus = np.where(focus > 1.0, focus / 100.0, focus)
    below = focus < .05; above = focus > .95; near_low = focus < .15; near_high = focus > .85
    btb = np.ones(n, dtype=float); conv_count = np.zeros(n, dtype=np.int64); conv_score = np.zeros(n, dtype=float); through = np.zeros(n, dtype=bool)
    conv_names = [[] for _ in range(n)]
    if is_long:
        bounce = (conv_bottom >= 2) & (near_low | below)
        broken = ~bounce & (conv_top >= 2) & above
        for mask, counts, scorearr, names in ((bounce, conv_bottom, conv_bottom_score, conv_bottom_names),(broken,conv_top,conv_top_score,conv_top_names)):
            conv_count = np.where(mask, counts, conv_count); conv_score = np.where(mask, scorearr, conv_score)
            for i in np.flatnonzero(mask): conv_names[i] = names[i]
        btb = np.where(bounce, np.where(conv_bottom >= 4,3.0,np.where(conv_bottom>=3,2.5,1.8)) * np.where(below,1.2,1.0), btb)
        btb = np.where(broken, np.where(conv_top>=4,3.5,np.where(conv_top>=3,3.0,2.0)), btb)
        through = (bounce & below) | broken
        bottom += np.where(bounce,10.0*conv_bottom,0.0) + np.where(broken,15.0*conv_top,0.0)
        entry += np.where(bounce,8.0*conv_bottom,0.0) + np.where(broken,12.0*conv_top,0.0)
    else:
        bounce = (conv_top >= 2) & (near_high | above)
        broken = ~bounce & (conv_bottom >= 2) & below
        for mask, counts, scorearr, names in ((bounce, conv_top, conv_top_score, conv_top_names),(broken,conv_bottom,conv_bottom_score,conv_bottom_names)):
            conv_count = np.where(mask, counts, conv_count); conv_score = np.where(mask, scorearr, conv_score)
            for i in np.flatnonzero(mask): conv_names[i] = names[i]
        btb = np.where(bounce, np.where(conv_top >= 4,3.0,np.where(conv_top>=3,2.5,1.8)) * np.where(above,1.2,1.0), btb)
        btb = np.where(broken, np.where(conv_bottom>=4,3.5,np.where(conv_bottom>=3,3.0,2.0)), btb)
        through = (bounce & above) | broken
        bottom += np.where(bounce,10.0*conv_top,0.0) + np.where(broken,15.0*conv_bottom,0.0)
        entry += np.where(bounce,8.0*conv_top,0.0) + np.where(broken,12.0*conv_bottom,0.0)
    size = _source_clamp(size * btb, 0.3, 5.0)
    bottom = _source_clamp(bottom, -100.0, 100.0)
    entry = _source_clamp(entry, -100.0, 100.0)
    return {"bottom_score": bottom, "entry_quality": entry, "direction": direction,
            "gain_potential": gain, "size_multiplier": size, "range_score": range_score,
            "convergence_count": conv_count, "convergence_score": conv_score,
            "convergence_tfs": conv_names, "btb_multiplier": btb,
            "price_through_extreme": through, "dc_htf_pos": htf_pos,
            "dc_ltf_pos": ltf_pos, "dc_moment": moment, "dc_expansion": expansion,
            "tf_breakdown": breakdown, "missing_inputs": tuple(sorted(missing_inputs)),
            "external_context_supplied": bool(context)}

"""Causal NumPy contributions applied after ASR's late score and before finish_scalar."""
from collections.abc import Mapping

import numpy as np


def _cfg(cfg, key, default):
    return cfg.get(key, default) if isinstance(cfg, Mapping) else getattr(cfg, key, default)


def _numeric(values, key, n, default):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=float)
    try:
        arr = np.asarray(raw, dtype=float).reshape(-1)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid ASR post-late input {key}") from error
    if arr.size != n:
        raise ValueError(f"{key} length {arr.size} does not match n={n}")
    return np.where(np.isfinite(arr), arr, float(default))


def _text(values, key, n, default=""):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=object)
    arr = np.asarray(raw, dtype=object).reshape(-1)
    if arr.size != n:
        raise ValueError(f"{key} length {arr.size} does not match n={n}")
    return np.asarray([default if value is None else str(value) for value in arr], dtype=object)


def _bool(values, key, n):
    raw = values.get(key)
    if raw is None:
        return np.zeros(n, dtype=bool)
    arr = np.asarray(raw).reshape(-1)
    if arr.size != n:
        raise ValueError(f"{key} length {arr.size} does not match n={n}")
    if arr.dtype.kind in "SUO":
        return np.asarray([str(value).strip().lower() in ("1", "true", "yes") for value in arr], dtype=bool)
    return arr.astype(bool)


def market_quality_parts_array(values, current_price, is_long):
    """Vector port of ez_positions_quick._market_quality_score (quality + reversal)."""
    px = np.asarray(current_price, dtype=float).reshape(-1)
    n = px.size
    num = lambda key, default: _numeric(values, key, n, default)
    quality = np.zeros(n, dtype=float)
    reversal = np.zeros(n, dtype=float)
    bb1, bb4 = num("bb_width_1h", 10.0), num("bb_width_4h", 10.0)
    quality += np.where((bb1 < 6.5) & (bb4 < 10.0), 1.5, np.where((bb1 < 9.0) & (bb4 < 13.0), 0.5, 0.0))
    dc1 = _numeric(values, "dc_width_1h", n, 8.0) if "dc_width_1h" in values else num("dc_width", 8.0)
    dc4 = num("dc_width_4h", 12.0)
    quality += ((dc1 < 7.0) & (dc4 < 12.0)) * 0.5
    adx1, adx4 = num("adx_1h", 30.0), num("adx_4h", 30.0)
    quality += np.where((adx1 < 20.0) & (adx4 < 25.0), 1.0, np.where(adx1 < 25.0, 0.5, 0.0))
    sma = num("sma_200_1h", 0.0)
    pct = np.full(n, -99.0, dtype=float)
    np.divide((px - sma) * 100.0, sma, out=pct, where=(sma > 0.0) & (px > 0.0))
    quality += np.where((pct > -3.0) & (pct < 3.0), 1.0, np.where((pct > -5.0) & (pct < 5.0), 0.5, 0.0))
    rv15, rv1 = num("relative_volume_15m", 1.5), num("relative_volume_1h", 1.5)
    quality += np.where((rv1 < 0.95) & (rv15 < 1.2), 1.0, np.where(rv1 < 1.2, 0.5, 0.0))
    m15, m1, m4 = num("mfi_15m", 50.0), num("mfi_1h", 50.0), num("mfi_4h", 50.0)
    r15, r1, r4 = num("rsi_15m", 50.0), num("rsi_1h", 50.0), num("rsi_4h", 50.0)
    if is_long:
        quality += np.where((m1 > 40.0) & (m4 > 40.0), np.where((m1 > 50.0) & (m4 > 45.0), 1.0, 0.5), 0.0)
    else:
        quality += np.where((r1 > 55.0) & (r4 > 50.0) & (rv1 >= 1.0), 1.0, np.where((r1 > 50.0) & (r4 > 45.0) & (rv1 >= 1.0), 0.5, 0.0))

    k3, k15, k1h = num("k_3m", 50.0), num("k_15m", 50.0), num("k_1h", 50.0)
    cross15 = _bool(values, "stoch_crossover_15m" if is_long else "stoch_crossunder_15m", n)
    cross1h = _bool(values, "stoch_crossover_1h" if is_long else "stoch_crossunder_1h", n)
    reversal += (cross15 & ((k15 < 30.0) if is_long else (k15 > 70.0))) * 1.0
    reversal += (cross1h & ((k1h < 35.0) if is_long else (k1h > 65.0))) * 0.5
    oversold = ((k3 < 25.0) & (k15 < 30.0) & (k1h < 40.0)) if is_long else ((k3 > 75.0) & (k15 > 70.0) & (k1h > 60.0))
    reversal += oversold * 1.0
    buy = "BUY" if is_long else "SELL"
    wt15, wt1h = _text(values, "wt_signal_15m", n), _text(values, "wt_signal_1h", n)
    wt15ok, wt1hok = wt15 == buy, wt1h == buy
    reversal += np.where(wt15ok & wt1hok, 1.5, np.where(wt15ok | wt1hok, 0.5, 0.0))
    goodha = "green" if is_long else "red"
    ha3, ha3p = _text(values, "ha_3m", n), _text(values, "ha_3m_prev", n)
    ha15, ha15p = _text(values, "ha_15m", n), _text(values, "ha_15m_prev", n)
    flip3 = (ha3 == goodha) & (ha3p != goodha)
    flip15 = (ha15 == goodha) & (ha15p != goodha)
    reversal += np.where(flip3 & flip15, 1.5, np.where(flip3 | flip15, 0.5, 0.0))
    divtag = "BULL" if is_long else "BEAR"
    div1, div4 = _text(values, "wt_divergence_1h", n), _text(values, "wt_divergence_4h", n)
    div1ok, div4ok = div1 == divtag, div4 == divtag
    reversal += np.where(div4ok, 1.5, np.where(div1ok, 1.0, 0.0))
    exhtag = "EXHAUST_DOWN" if is_long else "EXHAUST_UP"
    reversal += (_text(values, "wt_momentum_state_1h", n) == exhtag) * 0.5
    reversal += (_text(values, "wt_momentum_state_4h", n) == exhtag) * 0.5
    pattern = _text(values, "bar_pattern_15m", n)
    pats = ("hammer", "bullish_engulfing", "tweezer_bottom") if is_long else ("shooting_star", "bearish_engulfing", "tweezer_top")
    reversal += np.isin(pattern, pats) * 0.5
    if is_long:
        reversal += np.where((m15 < 30.0) & (m1 < 35.0), 1.0, np.where((m15 > m1) & (m1 < 40.0), 0.5, 0.0))
        rsi2 = num("rsi_2_1h", 50.0)
        reversal += (rsi2 < 15.0) * 0.5
    else:
        reversal += np.where((r15 > 70.0) & (r1 > 65.0) & (rv15 >= 1.0), 1.0, np.where((r15 < r1) & (r1 > 60.0), 0.5, 0.0))
        rsi2 = num("rsi_2_1h", 50.0)
        reversal += (rsi2 > 85.0) * 0.5
    return quality, reversal


def apply_asr_post_late_entry_array(npz, cfg, is_long, state, score):
    """Add native MQ and Delta entry bonuses before ASR's final score tail.

    DeltaTracker outputs are explicit causal state. Missing outputs are errors
    only when the native DeltaTracker provider was present and the switch active.
    """
    if not isinstance(state, Mapping):
        raise ValueError("post-late ASR state must be an explicit mapping")
    result = np.asarray(score, dtype=float).reshape(-1).copy()
    n = result.size
    if n < 1:
        raise ValueError("score must contain at least one bar")
    masks = {}
    if bool(_cfg(cfg, "MARKET_QUALITY_SCORE_ENABLED", False)):
        quality, reversal = market_quality_parts_array(npz, _numeric(state, "current_price", n, 0.0), is_long)
        total = quality + reversal
        bonus = np.where(total >= 6.0, 5.0, np.where(total >= 4.0, 2.0, 0.0))
        result += bonus
        masks["MARKET_QUALITY_SCORE"] = bonus > 0.0
        masks["MARKET_QUALITY_TOTAL"] = total
    else:
        masks["MARKET_QUALITY_SCORE"] = np.zeros(n, dtype=bool)
    if bool(_cfg(cfg, "DELTA_ENGINE_ENABLED", False)):
        if "delta_tracker_available" not in state:
            raise ValueError("DELTA_ENGINE_ENABLED requires explicit delta_tracker_available")
        tracker = np.broadcast_to(np.asarray(state["delta_tracker_available"], dtype=bool), (n,))
        present_raw = state.get("delta_signal_present")
        if np.any(tracker) and present_raw is None:
            raise ValueError("active DeltaTracker requires delta_signal_present")
        present = np.zeros(n, dtype=bool) if present_raw is None else np.broadcast_to(np.asarray(present_raw, dtype=bool), (n,))
        active = tracker & present
        if np.any(active):
            for key in ("delta_entry_ok", "delta_active_tfs"):
                if key not in state:
                    raise ValueError(f"active DeltaTracker requires {key}")
            entry_ok = np.broadcast_to(np.asarray(state["delta_entry_ok"], dtype=bool), (n,))
            active_tfs = _numeric(state, "delta_active_tfs", n, 0.0)
            minimum = int(_cfg(cfg, "DELTA_MIN_TF_FOR_ACTION", 2))
            bonus = float(_cfg(cfg, "DELTA_ENTRY_SCORE_BONUS", 15))
            penalty = float(_cfg(cfg, "DELTA_ENTRY_SCORE_PENALTY", -25))
            confirms = active & entry_ok & (active_tfs >= minimum)
            opposes = active & ~entry_ok & (active_tfs < minimum)
            result += confirms * bonus + opposes * penalty
            masks["DELTA_CONFIRM"] = confirms
            masks["DELTA_OPPOSE"] = opposes
            htf_gate = str(_cfg(cfg, "DELTA_HTF_GATE", "none"))
            if htf_gate in ("4h", "4h_D"):
                gate_against = {}
                for tf in ("4h", "D") if htf_gate == "4h_D" else ("4h",):
                    for key in (f"wt1_{tf}", f"wt2_{tf}"):
                        if key not in npz:
                            raise ValueError(f"DELTA_HTF_GATE={htf_gate} requires NPZ field {key}")
                    w1 = _numeric(npz, f"wt1_{tf}", n, 0.0)
                    w2 = _numeric(npz, f"wt2_{tf}", n, 0.0)
                    gate_against[tf] = (w1 <= w2) if is_long else (w1 >= w2)
                against_4h = active & gate_against["4h"]
                result += against_4h * penalty
                masks["DELTA_HTF_4H_AGAINST"] = against_4h
                if htf_gate == "4h_D":
                    against_d = active & gate_against["D"]
                    result += against_d * penalty
                    masks["DELTA_HTF_D_AGAINST"] = against_d
                masks["DELTA_HTF_AGAINST"] = against_4h | (active & gate_against.get("D", np.zeros(n, dtype=bool)))
        else:
            masks["DELTA_CONFIRM"] = np.zeros(n, dtype=bool)
            masks["DELTA_OPPOSE"] = np.zeros(n, dtype=bool)
            masks["DELTA_HTF_AGAINST"] = np.zeros(n, dtype=bool)
    from vec_decisions.asr_family_lane_score import apply_family_lane_score_array
    result, family_masks = apply_family_lane_score_array(
        npz, _numeric(state, "current_price", n, 0.0), is_long, cfg, result
    )
    masks.update({f"family:{key}": value for key, value in family_masks.items()})
    return result, masks

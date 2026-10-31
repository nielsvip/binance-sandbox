"""Ordered NumPy port of ASR's pre-score, non-scalp entry vetoes (3730–4034)."""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np


def _get(cfg, name, default):
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _number(values, key, n, default):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=float)
    try:
        array = np.asarray(raw, dtype=float)
    except (TypeError, ValueError):
        return np.full(n, default, dtype=float)
    if array.ndim == 0:
        array = np.full(n, array.item(), dtype=float)
    array = array.reshape(-1)
    if array.size != n:
        raise ValueError(f"{key} length {array.size} does not match n={n}")
    return np.where(np.isfinite(array), array, default)


def _optional_number(values, key, n):
    raw = values.get(key)
    if raw is None:
        return np.zeros(n, dtype=float), np.zeros(n, dtype=bool)
    array = np.asarray(raw, dtype=object)
    if array.ndim == 0:
        array = np.full(n, array.item(), dtype=object)
    array = array.reshape(-1)
    if array.size != n:
        raise ValueError(f"{key} length {array.size} does not match n={n}")
    result = np.zeros(n, dtype=float)
    valid = np.zeros(n, dtype=bool)
    for index, value in enumerate(array):
        try:
            result[index] = float(value)
            valid[index] = np.isfinite(result[index])
        except (TypeError, ValueError):
            continue
    return result, valid


def _text(values, key, n, default=""):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=object)
    array = np.asarray(raw, dtype=object)
    if array.ndim == 0:
        array = np.full(n, array.item(), dtype=object)
    array = array.reshape(-1)
    if array.size != n:
        raise ValueError(f"{key} length {array.size} does not match n={n}")
    return np.asarray([str(value or "").upper() for value in array], dtype=object)


def asr_pre_score_entry_gates(npz, cfg, is_long, current_price, skip_boycott=False, is_hedge=False, freshness_state=None):
    """Return ordered first-veto masks for native's non-scalp entry gates.

    Missing fields use the exact scalar fallback at the corresponding native
    read site. `skip_boycott` and `is_hedge` are caller lifecycle state, not
    inferred from the NPZ.
    """
    arrays = dict(npz)
    price = np.asarray(current_price, dtype=float)
    if price.ndim == 0:
        price = price.reshape(1)
    price = price.reshape(-1)
    n = price.size
    if n < 1:
        raise ValueError("current_price must be nonempty")
    masks = {}
    reasons = np.full(n, "", dtype=object)

    def add(name, fire, reason):
        fire = np.asarray(fire, dtype=bool)
        if fire.ndim == 0:
            fire = np.full(n, bool(fire), dtype=bool)
        fire = fire.reshape(-1)
        if fire.size != n:
            raise ValueError(f"{name} mask length {fire.size} does not match n={n}")
        active = reasons == ""
        chosen = active & fire
        masks[name] = chosen
        reasons[chosen] = reason

    if freshness_state is not None:
        if not isinstance(freshness_state, Mapping):
            raise ValueError("freshness_state must be a mapping")
        age_raw = freshness_state.get("indicator_age_minutes")
        if age_raw is not None:
            age = _number(freshness_state, "indicator_age_minutes", n, np.nan)
            tick_age = _number(freshness_state, "tick_age_seconds", n, np.inf)
            stale_candidate = np.isfinite(age) & (age > 90.0) & ~(tick_age < 120.0)
            if np.any(stale_candidate) and not is_hedge:
                for key in ("gain", "position_amt"):
                    if key not in freshness_state:
                        raise ValueError(f"stale-indicator gate requires freshness_state.{key}")
                winner_bypass = (_number(freshness_state, "gain", n, 0.0) >= 3.0) & (_number(freshness_state, "position_amt", n, 0.0) > 0.0)
                add("STALE_INDICATORS", stale_candidate & ~winner_bypass, "STALE_INDICATORS")
    if not skip_boycott:
        add("WT_DC_LONG_DISABLED", bool(is_long) and not bool(_get(cfg, "WT_DC_LONG_ENABLED", True)), "WT_DC_LONG_DISABLED")
        add("WT_DC_SHORT_DISABLED", (not bool(is_long)) and not bool(_get(cfg, "WT_DC_SHORT_ENABLED", True)), "WT_DC_SHORT_DISABLED")
        add("WT_DC_ENTRY_DISABLED", not bool(_get(cfg, "WT_DC_ENTRY_ENABLED", True)), "WT_DC_ENTRY_DISABLED")
    if bool(_get(cfg, "BASIS_CONDITION", False)) and not skip_boycott:
        b15 = _number(arrays, "dc_basis_15m", n, 0.0)
        b1h = _number(arrays, "dc_basis_1h", n, 0.0)
        b4h = _number(arrays, "dc_basis_4h", n, 0.0)
        bd = _number(arrays, "dc_basis_D", n, 0.0)
        k15 = _number(arrays, "k_15m", n, 50.0)
        d15 = _number(arrays, "d_15m", n, 50.0)
        sma1h = _number(arrays, "sma_200_1h", n, 0.0)
        valid_basis = (b15 > 0) & (b1h > 0) & (b4h > 0) & (bd > 0)
        if is_long:
            add("BELOW_BASIS_ALL_TF", valid_basis & ((price < b15) | (price < b1h)), "BELOW_BASIS_ALL_TF")
            add("STOCH_15M_BEARISH", valid_basis & (k15 < d15), "STOCH_15M_BEARISH")
            add("BELOW_SMA200", valid_basis & (sma1h > 0) & (price < sma1h), "BELOW_SMA200")
        else:
            add("ABOVE_BASIS_ALL_TF", valid_basis & ((price > b15) | (price > b1h) | (price > b4h) | (price > bd)), "ABOVE_BASIS_ALL_TF")
            add("STOCH_15M_BULLISH", valid_basis & (k15 > d15), "STOCH_15M_BULLISH")
            add("ABOVE_SMA200", valid_basis & (sma1h > 0) & (price > sma1h), "ABOVE_SMA200")
    if not is_hedge:
        if bool(_get(cfg, "SENTIMENT_TOP_N_GATE_ENABLED", False)):
            rank = np.trunc(_number(arrays, "0sentiment_rank", n, 0.0)).astype(np.int64)
            points = _number(arrays, "0ranking_points", n, -1.0)
            top_n = int(_get(cfg, "SENTIMENT_TOP_N", 20))
            add("SENT_RANK", (rank > 0) & ((rank > top_n) if is_long else ((points >= 0) & (points > top_n * 100.0 / 350.0))), "SENTIMENT_TOP_N")
        if bool(_get(cfg, "WT_MTF_VEL_GATE_ENABLED", False)):
            minimum = int(_get(cfg, "WT_MTF_VEL_MIN", 3))
            key = "wt_velocity_up_count" if is_long else "wt_velocity_down_count"
            count = np.trunc(_number(arrays, key, n, -1.0)).astype(np.int64)
            count = np.where(count == 0, -1, count)
            add("WT_MTF_VELOCITY", (count >= 0) & (count < minimum), "WT_MTF_VELOCITY")
        if bool(_get(cfg, "WT_CHOP_GATE_ENABLED", False)):
            chop_max = int(_get(cfg, "WT_CHOP_MAX", 8))
            stem = "wt_cross_count_bull_" if is_long else "wt_cross_count_bear_"
            counts = [_number(arrays, stem + tf, n, 0.0) for tf in ("3m", "15m", "1h")]
            high_count_tf = sum((count >= chop_max).astype(np.int8) for count in counts)
            add("WT_CHOP", high_count_tf >= 2, "WT_CHOP")
        if bool(_get(cfg, "WT_COMPOSITE_DELTA_GATE_ENABLED", True)):
            delta, delta_valid = _optional_number(arrays, "wt_composite_delta", n)
            threshold = float(_get(cfg, "WT_COMPOSITE_DELTA_LONG_MIN", -100.0) if is_long else _get(cfg, "WT_COMPOSITE_DELTA_SHORT_MAX", 100.0))
            add("WT_COMPOSITE_DELTA", delta_valid & ((delta < threshold) if is_long else (delta > threshold)), "WT_COMPOSITE_DELTA")
        if bool(_get(cfg, "WT_EXHAUST_ENTRY_GATE_ENABLED", True)):
            m3 = _text(arrays, "wt_momentum_state_3m", n)
            m15 = _text(arrays, "wt_momentum_state_15m", n)
            target = "EXHAUST_UP" if is_long else "EXHAUST_DOWN"
            add("WT_EXHAUST", (m3 == target) & (m15 == target), "WT_EXHAUST")
        if bool(_get(cfg, "WT_PERCENTILE_ENTRY_GATE_ENABLED", True)):
            pct, pct_valid = _optional_number(arrays, "wt_percentile_D", n)
            threshold = float(_get(cfg, "WT_PERCENTILE_ENTRY_OB_D", 90.0) if is_long else _get(cfg, "WT_PERCENTILE_ENTRY_OS_D", 10.0))
            add("WT_PERCENTILE_D", pct_valid & (pct > threshold if is_long else pct < threshold), "WT_PERCENTILE_D")
        if bool(_get(cfg, "WT_DIV_ENTRY_GATE_ENABLED", True)):
            key = "wt_any_bear_div" if is_long else "wt_any_bull_div"
            raw = arrays.get(key, False)
            fire = np.asarray(raw, dtype=bool)
            if fire.ndim == 0:
                fire = np.full(n, bool(fire), dtype=bool)
            add("WT_DIVERGENCE", fire, "WT_DIVERGENCE")
        if bool(_get(cfg, "R_G10_HTF_DIV_GATE_ENABLED", False)):
            tfs = tuple(tf.strip() for tf in str(_get(cfg, "R_G10_HTF_DIV_TFS", "4h,D") or "").split(",") if tf.strip())
            target = "BEAR" if is_long else "BULL"
            fire = np.zeros(n, dtype=bool)
            for tf in tfs:
                fire |= _text(arrays, f"wt_divergence_{tf}", n) == target
            add("R_G10_HTF_DIV", fire, "R_G10_HTF_DIV")
        mode = int(_get(cfg, "R_S6_WT_MSTATE_GATE_MODE", 0))
        if mode > 0:
            tfs = ("3m", "15m") if mode == 1 else (("3m", "15m", "1h", "4h", "D") if mode in (2, 3) else ())
            target = "EXHAUST_UP" if is_long else "EXHAUST_DOWN"
            fire = np.zeros(n, dtype=bool)
            for tf in tfs:
                fire |= _text(arrays, f"wt_momentum_state_{tf}", n) == target
            add("R_S6_MSTATE", fire if mode != 3 else np.zeros(n, dtype=bool), "R_S6_MSTATE")
    return {"blocked": reasons != "", "reason": reasons, "masks": masks}

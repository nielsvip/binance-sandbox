"""Ordered NumPy port of selected AdvancedSignalRater entry gates and scores.

This currently covers portions of the HTF-to-WT path. Earlier lifecycle,
mandatory-reentry, later score families, and execution thresholds remain open,
so callers must not treat it as a complete rate().
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from vec_decisions.asr_mts_entry_gate import mts_entry_gate_array
from vec_decisions.asr_inline_score import (
    all_tf_wt_entry_array, bc155_score_array, data_blind_gate_array, hlr_rally_score_array, k_zone_score_array,
    momentum_fade_score_array, mover_score_array, satoshit_score_array, stoch_kx_score_array,
    rsi_mfi_score_array, dc_momentum_bota_score_array, stochastic_alignment_score_array,
)
from vec_decisions.asr_mts_mi_score import mts_mi_score_array
from vec_decisions.asr_portfolio_ratio_score import portfolio_ratio_array
from vec_decisions.asr_score_core import (
    apply_15m_momentum_vec,
    apply_channel_k_cross_score_vec,
    apply_ma_distance_score_vec,
    apply_rank_conviction_vec,
    apply_score_prefix_vec,
    apply_structure_score_vec,
)
from vec_decisions.asr_stoch_entry_score import stoch_entry_score_array
from vec_decisions.asr_input_adapter import paired_npz_view
from vec_decisions.asr_ordered_composer import bind_asr_npz_arrays
from vec_decisions.asr_reentry_stage import apply_asr_reentry_entry_stage
from vec_decisions.asr_pre_score_gates import asr_pre_score_entry_gates


def _cfg(cfg, name, default):
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _col(values, key, n, default=0.0, dtype=float):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=dtype)
    try:
        result = np.asarray(raw, dtype=dtype).reshape(-1)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid ASR prefix field {key}") from error
    if result.size != n:
        raise ValueError(f"{key} length {result.size} does not match n={n}")
    return result


def _required_col(values, key, n, dtype=float):
    if key not in values:
        raise ValueError(f"missing required ASR prefix input {key}")
    return _col(values, key, n, dtype=dtype)


def score_asr_entry_prefix_array(npz, n, is_long, cfg, context=None, score=None):
    """Run ordered entry gates/scores through channel and K/DC-cross scoring.

    ``context`` supplies account/market state the NPZ cannot provide. If the
    corresponding native feature is enabled, missing state raises instead of
    inventing a zero score. Returned masks indicate the first native veto and
    later score masks are evaluated only for rows still eligible at that point.
    """
    if int(n) < 1:
        raise ValueError("n must be positive")
    n = int(n)
    context = {} if context is None else context
    if not isinstance(context, Mapping):
        raise ValueError("context must be a mapping")
    arrays = bind_asr_npz_arrays(npz, cfg)
    current_price = _required_col(context, "current_price", n)
    score_arrays = dict(arrays)
    score_arrays["close"] = current_price
    result = np.zeros(n, dtype=float) if score is None else _col({"score": score}, "score", n)
    masks = {}
    blocked = np.zeros(n, dtype=bool)

    def veto(name, mask):
        nonlocal blocked
        active = ~blocked
        fire = active & np.asarray(mask, dtype=bool)
        masks[name] = fire
        blocked |= fire

    def add(name, proposed, extra_masks=None):
        nonlocal result
        active = ~blocked
        proposed = np.asarray(proposed, dtype=float)
        if proposed.shape != (n,):
            raise ValueError(f"{name} score shape {proposed.shape} does not match n={n}")
        delta = proposed - result
        result = np.where(active, proposed, result)
        if extra_masks:
            for key, mask in extra_masks.items():
                masks[f"{name}:{key}"] = active & np.asarray(mask, dtype=bool)
        masks[f"{name}:active"] = active

    pre_score_gates = asr_pre_score_entry_gates(
        arrays, cfg, is_long, current_price,
        skip_boycott=bool(context.get("skip_boycott", False)),
        is_hedge=bool(context.get("is_hedge", False)),
    )
    veto("ASR_PRE_SCORE", pre_score_gates["blocked"])
    masks.update({f"pre_score:{key}": value for key, value in pre_score_gates["masks"].items()})
    masks["pre_score:first_reason"] = pre_score_gates["reason"]

    # Native 4700 gate: composite gate takes precedence over HTF_STRICT.
    wt_comp_enabled = bool(_cfg(cfg, "WT_COMPOSITE_SCORING_ENABLED", False)) or bool(
        _cfg(cfg, "WT_COMPOSITE_SCORING_ENABLED_TRADIER", False)
    )
    wt_htf_gate = bool(_cfg(cfg, "WT_COMPOSITE_HTF_GATE", False)) and wt_comp_enabled
    if wt_htf_gate:
        align_key = "wt_bull_alignment" if is_long else "wt_bear_alignment"
        comp_key = "wt_composite_long" if is_long else "wt_composite_short"
        align = _col(arrays, align_key, n, 0.0)
        composite = _col(arrays, comp_key, n, 0.0)
        veto("WT_HTF_ALIGN", align < 3)
        veto("WT_COMP_BLOCK", composite < float(_cfg(cfg, "WT_COMPOSITE_ENTRY_BLOCK", -20.0)))
    elif bool(_cfg(cfg, "HTF_STRICT", True)):
        k1h = _col(arrays, "k_1h", n, 50.0)
        d1h = _col(arrays, "d_1h", n, 50.0)
        veto("HTF_1H_REQUIRED", (k1h <= d1h) if is_long else (k1h >= d1h))

    proposed, prefix_masks = apply_score_prefix_vec(score_arrays, n, is_long, cfg, result, context.get("metrics"))
    add("score_prefix", proposed, prefix_masks)
    proposed, against = apply_15m_momentum_vec(arrays, n, is_long, cfg, result)
    add("wt15m_momentum", proposed, {"WT15M_AGAINST": against})
    proposed, structure_masks = apply_structure_score_vec(arrays, n, is_long, cfg, result)
    add("structure", proposed, structure_masks)

    # Regime detection is cross-context state; consume its exact native delta.
    if bool(_cfg(cfg, "REGIME_DETECTION_ENABLED", False)):
        if "regime_delta" not in context:
            raise ValueError("REGIME_DETECTION_ENABLED requires context.regime_delta")
        regime_delta = _col(context, "regime_delta", n)
        result = np.where(~blocked, result + regime_delta, result)
        masks["regime_detection:active"] = ~blocked
    elif bool(_cfg(cfg, "ADX_REGIME_FILTER_ENABLED", False)):
        adx_tf = str(_cfg(cfg, "ADX_TF", "1h"))
        adx = _col(arrays, f"adx_{adx_tf}", n, 50.0)
        k_focus = _col(arrays, f"k_{_cfg(cfg, 'TF_FOCUS', '3m')}", n, 50.0)
        threshold = float(_cfg(cfg, "ADX_RANGING_THRESHOLD", 20.0))
        extreme_ok = (k_focus < 20) if is_long else (k_focus > 80)
        veto("ADX_RANGING_BLOCK", (adx < threshold) & ~extreme_ok)

    if "portfolio_ratio_active" not in context:
        raise ValueError("context must explicitly state portfolio_ratio_active")
    ratio_active = np.broadcast_to(np.asarray(context["portfolio_ratio_active"], dtype=bool), (n,))
    if np.any(ratio_active):
        ratio = _required_col(context, "portfolio_ratio", n)
        eligible = _required_col(context, "portfolio_ratio_available", n, bool)
        delta, ratio_masks = portfolio_ratio_array(
            ratio, eligible, float(_cfg(cfg, "LS_RATIO_MIN", 0.40)),
            float(_cfg(cfg, "LS_RATIO_MAX", 2.50)), is_long,
        )
        result = np.where(~blocked & ratio_active, result + delta, result)
        masks.update({f"portfolio_ratio:{key}": ~blocked & value for key, value in ratio_masks.items()})

    proposed, rank_masks = apply_rank_conviction_vec(arrays, n, is_long, cfg, result)
    add("rank_conviction", proposed, rank_masks)

    stoch_keys = ("wt_bullish_scalp", "wt_score_scalp", "wt_velocity_scalp", "wt_cross_bull_scalp",
                  "wt_momentum_state_1h", "k_15m", "k_15m_prev", "k_3m", "d_3m")
    stoch_values = {key: _col(arrays, key, n, False if key in ("wt_bullish_scalp", "wt_cross_bull_scalp") else 0.0,
                              bool if key in ("wt_bullish_scalp", "wt_cross_bull_scalp") else (object if key == "wt_momentum_state_1h" else float))
                    for key in stoch_keys}
    stoch_delta, long_ok, short_ok = stoch_entry_score_array(stoch_values, is_long)
    result = np.where(~blocked, result + stoch_delta, result)
    masks["stoch_entry:long_ok"] = ~blocked & long_ok
    masks["stoch_entry:short_ok"] = ~blocked & short_ok

    mts_source = dict(arrays)
    mts_source.update({key: context[key] for key in ("mts_bottom", "mts_entry_quality") if key in context})
    mts_bottom = _required_col(mts_source, "mts_bottom", n)
    mts_quality = _required_col(mts_source, "mts_entry_quality", n)
    tradier_mts = bool(_cfg(cfg, "MTS_GATE_ENABLED_TRADIER", False))
    generic_mts = bool(_cfg(cfg, "MTS_GATE_ENABLED", False))
    mts_block, mts_masks = mts_entry_gate_array(mts_bottom, mts_quality, is_long, tradier_mts, generic_mts, cfg)
    mts_active = ~blocked
    veto("MTS_GATE", mts_block)
    masks.update({f"mts_gate:{key}": mts_active & value for key, value in mts_masks.items()})
    mts_values = {
        "mts_bottom": mts_bottom, "mts_entry_quality": mts_quality,
        **{key: _col(arrays, key, n, "", object) for key in (
                "wt_trough_structure_1h", "wt_trough_structure_4h", "wt_momentum_state_1h",
                "wt_momentum_state_4h", "wt_divergence_1h", "wt_peak_structure_1h", "wt_peak_structure_4h",
        )},
    }
    mi_delta, mi_masks = mts_mi_score_array(mts_values, is_long, cfg)
    result = np.where(~blocked, result + mi_delta, result)
    masks.update({f"mts_mi:{key}": mts_active & value for key, value in mi_masks.items()})

    sma200d = _col(arrays, "sma_200_D", n, 0.0)
    price = current_price
    dist = np.zeros(n, dtype=float)
    np.divide(price - sma200d, sma200d, out=dist, where=sma200d > 0)
    veto("SMA200D_EXTREME", (sma200d > 0) & ((dist > 0.35) if is_long else (dist < -0.35)))

    proposed, ma_masks = apply_ma_distance_score_vec(score_arrays, n, is_long, result)
    add("ma_distance", proposed, ma_masks)

    k3 = _col(arrays, "k_3m", n, 50.0)
    cap = float(_cfg(cfg, "K3M_CAP", 80.0))
    bypass = bool(_cfg(cfg, "K3M_CAP_BREAKOUT_BYPASS", True))
    dc_high1 = _col(arrays, "dc_high_1h", n, 0.0)
    dc_high3 = _col(arrays, "dc_high_3m", n, 0.0)
    dc_low1 = _col(arrays, "dc_low_1h", n, 0.0)
    dc_low3 = _col(arrays, "dc_low_3m", n, 0.0)
    breakout = (((price > dc_high1) & (dc_high1 > 0)) | ((price > dc_high3) & (dc_high3 > 0))) if is_long else (((price < dc_low1) & (dc_low1 > 0)) | ((price < dc_low3) & (dc_low3 > 0)))
    cap_hit = (k3 >= cap) if is_long else (k3 <= 100.0 - cap)
    veto("K3M_CAP", (cap > 0) & cap_hit & ~(bypass & breakout))

    if not is_long:
        rsi_min = float(_cfg(cfg, "SHORT_RSI_MIN_1H", 0.0))
        rsi1h = _col(arrays, "rsi_1h", n, 50.0)
        veto("SHORT_RSI_MIN", (rsi_min > 0) & (rsi1h < rsi_min))

    chase_enabled = bool(context.get("long_stoch_chase_block", _cfg(cfg, "LONG_STOCH_CHASE_BLOCK", False)))
    ha1h = np.asarray(arrays.get("ha_1h", np.full(n, "neutral", dtype=object)), dtype=object).reshape(-1)
    if ha1h.size != n:
        raise ValueError("ha_1h length does not match n")
    streak_fallback = np.where(ha1h == "green", 1.0, 0.0)
    streak = _col(arrays, "ha_streak_1h", n, 0.0)
    if "ha_streak_1h" not in arrays:
        streak = streak_fallback
    k1h = _col(arrays, "k_1h", n, 50.0)
    veto("LONG_CHASE_BLOCK", is_long & chase_enabled & (k1h > 70.0) & (streak > 2.0))

    atr_min = float(context.get("entry_atr_pct_min", _cfg(cfg, "ENTRY_ATR_PCT_MIN", 0.0)))
    atr1h = _col(arrays, "atr_1h", n, 0.0)
    atr_pct = np.zeros(n, dtype=float)
    np.divide(atr1h * 100.0, current_price, out=atr_pct, where=current_price > 0)
    veto("ATR_TOO_LOW", (atr_min > 0.0) & (atr1h > 0.0) & (current_price > 0.0) & (atr_pct < atr_min))

    proposed, channel_masks = apply_channel_k_cross_score_vec(
        score_arrays, n, is_long, result, bool(_cfg(cfg, "USE_1M_3M_SIGNALS_ENABLED", False))
    )
    add("channel_k_cross", proposed, channel_masks)

    proposed, bc_masks = bc155_score_array(arrays, current_price, is_long, cfg, score=result)
    add("bc155", proposed, bc_masks)
    proposed, kz_masks = k_zone_score_array(arrays, is_long, cfg, score=result)
    add("k_zone", proposed, kz_masks)

    if "mover_registry_available" not in context:
        raise ValueError("context must explicitly state mover_registry_available")
    mover_available = np.broadcast_to(np.asarray(context["mover_registry_available"], dtype=bool), (n,))
    mover_direction = _col(context, "mover_direction", n, "", object) if np.any(mover_available) else np.full(n, "", dtype=object)
    proposed, mover_masks = mover_score_array(mover_direction, mover_available, is_long, cfg, score=result)
    add("mover", proposed, mover_masks)

    proposed, fade_masks = momentum_fade_score_array(arrays, is_long, cfg, score=result)
    add("momentum_fade", proposed, fade_masks)

    satoshit_on = bool(_cfg(cfg, "SATOSHIT_ENABLED", False))
    if satoshit_on and "satoshit_account_allowed" not in context:
        raise ValueError("SATOSHIT_ENABLED requires context.satoshit_account_allowed")
    satoshit_allowed = bool(context.get("satoshit_account_allowed", False))
    satoshit_values = dict(arrays)
    if "stoch_k_15m" not in satoshit_values and "k_15m" in arrays:
        satoshit_values["stoch_k_15m"] = arrays["k_15m"]
    proposed, satoshit_masks = satoshit_score_array(
        satoshit_values, is_long, cfg, satoshit_on and satoshit_allowed, score=result
    )
    add("satoshit", proposed, satoshit_masks)

    align_delta, align_masks = stochastic_alignment_score_array(arrays, long_ok, short_ok, is_long)
    proposed = result + align_delta
    add("stochastic_alignment", proposed, align_masks)

    result, hlr_size_multiplier, hlr_bypass, hlr_masks = hlr_rally_score_array(
        arrays, current_price, is_long, cfg, score=result
    )
    masks.update({f"hlr_rally:{key}": value for key, value in hlr_masks.items()})
    masks["hlr_rally:bypass"] = hlr_bypass

    k1, d1 = _col(arrays, "k_1m", n, 50.0), _col(arrays, "d_1m", n, 50.0)
    k3, d3 = _col(arrays, "k_3m", n, 50.0), _col(arrays, "d_3m", n, 50.0)
    k15, d15 = _col(arrays, "k_15m", n, 50.0), _col(arrays, "d_15m", n, 50.0)
    c1 = k1 > d1 if is_long else k1 < d1
    c3 = k3 > d3 if is_long else k3 < d3
    pullback = (k15 < 30.0) if is_long else (k15 > 70.0)
    veto("SHIT_IDEA", ~(c1 | c3 | pullback | hlr_bypass))
    no_c3 = ~c3
    aligned_both = c1 & c3
    add_score = np.where(no_c3, -2.0, np.where(aligned_both, 3.0, 1.0))
    result = np.where(~blocked, result + add_score, result)
    masks["candle_alignment:score_penalty"] = ~blocked & no_c3
    masks["candle_alignment:both_bonus"] = ~blocked & aligned_both
    masks["candle_alignment:c3_bonus"] = ~blocked & (c3 & ~aligned_both)
    unreliable = (k3 == 50.0) & (d3 == 50.0) & (k15 == 50.0) & (d15 == 50.0) & (_col(arrays, "k_1h", n, 50.0) == 50.0) & (_col(arrays, "d_1h", n, 50.0) == 50.0)
    veto("UNRELIABLE_INDICATORS", unreliable)

    # Native 5485 DATA_BLIND veto, then KX score, then all-timeframe WT gate.
    blind, blind_masks = data_blind_gate_array(arrays, context)
    veto("DATA_BLIND_5050", blind)
    masks.update({f"data_blind:{key}": value for key, value in blind_masks.items()})

    state_keys = ("last_reduction_price", "minutes_since_reduction", "gain", "true_lag")
    missing_state = [key for key in state_keys if key not in context]
    if missing_state:
        raise ValueError(f"KX score requires explicit context state: {','.join(missing_state)}")
    kx_state = {key: _required_col(context, key, n) for key in state_keys}
    kx_state["current_price"] = current_price
    proposed, kx_masks = stoch_kx_score_array(score_arrays, kx_state, is_long, score=result)
    add("stoch_kx", proposed, kx_masks)

    wt_active = ~blocked
    proposed, wt_masks = all_tf_wt_entry_array(arrays, is_long, score=result)
    wt_block = wt_masks["WT_DIR_LTF"] | wt_masks["WT_STRUCT"] | wt_masks["WT_DIV"] | wt_masks["WT_VEL"]
    veto("WT_ALL_TF", wt_block)
    result = np.where(wt_active & ~wt_block, proposed, result)
    for key, value in wt_masks.items():
        masks[f"all_tf_wt:{key}"] = wt_active & value
    reentry = apply_asr_reentry_entry_stage(score_arrays, context, is_long, cfg, result, active=~blocked)
    result = reentry["score"]
    veto("ASR_REENTRY_STAGE", ~reentry["entry_allowed"])
    masks.update({f"reentry:{key}": value for key, value in reentry.items() if isinstance(value, np.ndarray) and key not in ("score", "entry_allowed")})
    masks["reentry:bounce_reset_final"] = np.full(n, reentry["bounce_reentry_k_reset"], dtype=bool)

    entry_active = ~blocked
    proposed, rsi_masks = rsi_mfi_score_array(arrays, is_long, score=result)
    result = np.where(entry_active, proposed, result)
    masks.update({f"rsi_mfi:{key}": entry_active & value for key, value in rsi_masks.items()})
    dcb_state = {"current_price": current_price, "pnl_pct": _required_col(context, "pnl_pct", n)}
    proposed, dcb_masks = dc_momentum_bota_score_array(arrays, dcb_state, is_long, score=result)
    result = np.where(entry_active, proposed, result)
    masks.update({f"dc_bota:{key}": entry_active & value for key, value in dcb_masks.items() if np.asarray(value).dtype == bool})
    dcb_bypass = dcb_masks["DC_MOMENTUM"] | dcb_masks["BOTA_PULLBACK"]
    k1, d1 = _col(arrays, "k_1m", n, 50.0), _col(arrays, "d_1m", n, 50.0)
    k3, d3 = _col(arrays, "k_3m", n, 50.0), _col(arrays, "d_3m", n, 50.0)
    protect = (_required_col(context, "pnl_pct", n) > 1.8) & (_required_col(context, "pnl_pct", n) < 2.3)
    protect &= ((k1 < d1) | (k3 < d3)) if is_long else ((k1 > d1) | (k3 > d3))
    veto("EXCLUDED_SCALP_REDUCE", protect)
    second_active = ~blocked & ~dcb_bypass
    k1p = _col(arrays, "k_1m_prev", n, 50.0)
    k3p = _col(arrays, "k_3m_prev", n, 50.0)
    k15, k15p = _col(arrays, "k_15m", n, 50.0), _col(arrays, "k_15m_prev", n, 50.0)
    lag = _required_col(context, "true_lag", n)
    k1co = (k1 > d1) & (lag < 10.0) & (k1p <= d1) & (k1 < 30.0)
    k1cu = (k1 < d1) & (lag < 10.0) & (k1p >= d1) & (k1 > 70.0)
    k3co = (k3 > d3) & (k3p <= d3) & (k3 < 30.0)
    k3cu = (k3 < d3) & (k3p >= d3) & (k3 > 70.0)
    k15co = (k15 > _col(arrays, "d_15m", n, 50.0)) & (_col(arrays, "k_15m_prev", n, 50.0) <= _col(arrays, "d_15m", n, 50.0)) & (k15 < 30.0)
    k15cu = (k15 < _col(arrays, "d_15m", n, 50.0)) & (_col(arrays, "k_15m_prev", n, 50.0) >= _col(arrays, "d_15m", n, 50.0)) & (k15 > 70.0)
    if is_long:
        second_good = (k3 < 25.0) | ((((k1 < 15.0) & (k1 > k1p)) | k1co) & (lag < 10.0)) | k3co | reentry["force_reentry"]
    else:
        second_good = (k3 > 75.0) | ((((k1 > 85.0) & (k1 < k1p)) | k1cu) & (lag < 10.0)) | k3cu | reentry["force_reentry"]
    veto("POST_BOTA_ENTRY_MOMENT", second_active & ~second_good)
    second_fire = second_active & second_good & ~blocked
    if is_long:
        result += second_fire * (~reentry["tier1_forced"] & ~reentry["tier2_chase"] & ~reentry["force_reentry"] & ~dcb_bypass) * (-10.0 * (~_col(context, "trend_is_bullish", n, False, bool)))
        combo = (k1co & (k3 < 80.0) & (lag < 10.0)) | (k3co & (k15 > 80.0))
        result += (second_fire & combo) * 5.0
        result += (second_fire & combo & (((k15 > 50.0) & (k3 > d3)) | ((k15 < 30.0) & (k3 > d3)))) * 5.0
        result += (second_fire & k15co) * 4.0
    else:
        result += second_fire * (~reentry["tier1_forced"] & ~reentry["tier2_chase"] & ~reentry["force_reentry"] & ~dcb_bypass) * (-10.0 * (~_col(context, "trend_is_bearish", n, False, bool)))
        combo = (k1cu & (k3 > 20.0) & (lag < 10.0)) | (k3cu & (k15 < 20.0))
        result += (second_fire & combo) * 5.0
        result += (second_fire & combo & (((k15 < 50.0) & (k3 < d3)) | ((k15 > 70.0) & (k3 < d3)))) * 5.0
        result += (second_fire & k15cu) * 4.0
    return {
        "score": result,
        "entry_allowed": ~blocked,
        "hlr_size_multiplier": hlr_size_multiplier,
        "hlr_bypass": hlr_bypass,
        "reentry_state_out": {"bounce_reentry_k_reset": reentry["bounce_reentry_k_reset"]},
        "masks": masks,
        "complete_rate": False,
        "covered_native_spans": ("ez_positions_quick.py:4696-4954 selected gate/score blocks including chase/ATR vetoes", "ez_positions_quick.py:4955-5071 channel/K/DC-cross and directional scores", "ez_positions_quick.py:5072-5434 BC155 through HLR/candle-alignment and unreliable-indicator gates",
            "ez_positions_quick.py:5485-5672 data-blind veto, KX score, and all-TF WT veto/penalties",
            "ez_positions_quick.py:5675-6052 stateful mandatory reentry, early entry moment, and score bonuses",
            "ez_positions_quick.py:6053-6264 RSI/MFI, DC/BOTA scoring, and second entry-moment gate"),
        "remaining": (
            "pre_score_lifecycle_gates", "regime_input_producer",
            "tracker_position_and_market_state:ez_positions_quick.py:5435-5484",
            "scalp_paths_excluded_by_user:ez_positions_quick.py:6265-7158",
            "non-scapl_exit_and_action_threshold_paths", "action_threshold_and_execution",
        ),
    }

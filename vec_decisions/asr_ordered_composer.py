"""Compose the explicitly ported ASR score stages from DC structure through final tail.

The caller must supply the exact native score immediately before the DC-stage
span and all external arrays. Earlier score mutations, vetoes, and action gates
are not included, so the result is not a complete AdvancedSignalRater.rate().
"""

import numpy as np

from collections.abc import Mapping

from vec_decisions.asr_breakout_pa_score import breakout_pa_score_array
from vec_decisions.asr_channel_confluence_score import apply_channel_confluence_vec
from vec_decisions.asr_dc_structure_score import dc_structure_votes_array
from vec_decisions.asr_history_score import history_score_array
from vec_decisions.asr_post_rank_score import apply_asr_dc_sufficiency_vec, apply_asr_post_rank_vec
from vec_decisions.asr_rank_sentiment_score import apply_rank_sentiment_vec
from vec_decisions.asr_volume_setup_score import volume_setup_array
from vec_decisions.asr_wt_intelligence_score import wt_intelligence_score_array
from vec_decisions.asr_directional_score import directional_score_array
from vec_decisions.asr_input_adapter import paired_npz_view
from vec_decisions.asr_late_score import apply_asr_late_score_vec
from vec_decisions.asr_score_tail import finish_array
from vec_decisions.asr_pre_dc_entry_score import pre_dc_entry_score_array


ASR_LATE_STAGE_ORDER = (
    "pre_dc_entry_state",
    "dc_structure",
    "breakout_pa",
    "directional",
    "wt_intelligence",
    "volume_setup",
    "tracker_history_modifier",
    "channel_confluence",
    "rank_sentiment",
    "post_rank_transform",
    "dc_sufficiency",
    "late_score_including_research",
    "market_quality_and_delta_entry_contributions",
    "finish_tail",
)

ASR_REMAINING_AFTER_COMPOSER = (
    "rate_account_position_tradeability_and_hot_state_hydration:ez_positions_quick.py:3616-3669,4035-4695",
    "portfolio_ratio_regime_mover_and_tracker_context:ez_positions_quick.py:4744-4803,5199-5210,5435-5484",
    "full_universe_sentiment_context_producer:ez_indicators.py:4671-4954",
    "native_full_rate_oracle_with_explicit_causal_context_and_reason_order",
    "quick_open_augment_source_order_and_execution_dispatch:ez_positions_quick.py:28341-29773",
    "actual_v12_simulate_one_candidate_consumer",
)



_ASR_ENUM_PREFIXES = ("wt_cross", "wt_divergence", "wt_momentum_state", "wt_peak_structure", "wt_trough_structure", "wt_wave_phase", "wt_signal", "ha")
_ASR_TIMEFRAMES = {"1m", "3m", "5m", "15m", "1h", "4h", "D", "W", "M"}

def bind_asr_npz_arrays(npz, cfg):
    """Apply per-symbol TF policy and the shared live-indicator label decoder."""
    use_ltf = bool(cfg.get("USE_1M_3M_SIGNALS_ENABLED", False) if isinstance(cfg, dict) else getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", False))
    from vec_decisions.live_indicator_labels import decode_live_indicator_labels
    selected = paired_npz_view(npz, use_ltf)
    result = dict(selected)
    for key, raw in tuple(result.items()):
        if not isinstance(key, str):
            continue
        family = next((name for name in _ASR_ENUM_PREFIXES if key.startswith(name + "_") and key[len(name) + 1:] in _ASR_TIMEFRAMES), None)
        if family is None:
            continue
        array = np.asarray(raw)
        if array.dtype.kind not in "biuf":
            continue
        unique, inverse = np.unique(array, return_inverse=True)
        labels = []
        for value in unique:
            if not np.isfinite(value) or float(value) != float(int(value)):
                raise ValueError(f"{key}: unrecognized encoded ASR label {value!r}")
            labels.append(decode_live_indicator_labels({key: int(value)})[key])
        result[key] = np.asarray(labels, dtype=object)[inverse].reshape(array.shape)
    for key, raw in tuple(result.items()):
        if not isinstance(key, str) or not key.startswith("wt_structure_") or key[len("wt_structure_"):] not in _ASR_TIMEFRAMES:
            continue
        array = np.asarray(raw)
        if array.dtype.kind not in "biuf":
            continue
        tf = key[len("wt_structure_"):]
        peak = np.asarray(result.get(f"wt_peak_structure_{tf}", np.zeros(array.shape)))
        trough = np.asarray(result.get(f"wt_trough_structure_{tf}", np.zeros(array.shape)))
        if peak.shape != array.shape or trough.shape != array.shape:
            raise ValueError(f"{key}: structure companion shape mismatch")
        flat_values = array.reshape(-1)
        flat_peak = peak.reshape(-1)
        flat_trough = trough.reshape(-1)
        if not np.all(np.isfinite(flat_values)) or np.any(flat_values != np.trunc(flat_values)):
            bad = flat_values[~np.isfinite(flat_values) | (flat_values != np.trunc(flat_values))][0]
            raise ValueError(f"{key}: unrecognized encoded ASR label {bad!r}")
        combos = set(zip((int(value) for value in flat_values), flat_peak.tolist(), flat_trough.tolist()))
        labels = {}
        for value, peak_label, trough_label in combos:
            labels[(value, peak_label, trough_label)] = decode_live_indicator_labels({
                key: value, f"wt_peak_structure_{tf}": peak_label,
                f"wt_trough_structure_{tf}": trough_label,
            })[key]
        decoded = [labels[(int(value), peak_label, trough_label)]
                   for value, peak_label, trough_label in zip(flat_values, flat_peak, flat_trough)]
        result[key] = np.asarray(decoded, dtype=object).reshape(array.shape)
    for key, raw in tuple(result.items()):
        if isinstance(key, str) and (key.startswith("wt_cross_rising_") or key.startswith("wt_extreme_")) and np.asarray(raw).dtype.kind in "biuf":
            arr = np.asarray(raw)
            if np.any(~np.isfinite(arr)) or np.any(~np.isin(arr, (0, 1))):
                raise ValueError(f"{key}: expected encoded boolean values 0/1")
            result[key] = arr.astype(bool)
    return result


def assemble_asr_composer_inputs(npz, cfg, causal_state, n, is_long, arrays_bound=False):
    """Bind raw causal NPZ columns and explicit replay state into the composer schema.

    Missing arrays raise; the only defaults here are source-defined current-channel
    fallbacks for absent ``*_ant`` Donchian columns and native initial ``WAIT`` rec.
    """
    if not isinstance(causal_state, dict):
        raise ValueError("causal_state must be an explicit mapping")
    selected = dict(npz) if arrays_bound else bind_asr_npz_arrays(npz, cfg)
    def col(key):
        if key not in selected:
            raise ValueError(f"missing causal NPZ field {key}")
        return _column(selected[key], key, n)
    def state(key, dtype=float):
        if key not in causal_state:
            raise ValueError(f"missing causal state field {key}")
        return _column(causal_state[key], key, n, dtype)
    price = state("current_price")
    lag = state("true_lag")
    dc = {}
    for tf in ("3m", "15m", "1h", "4h"):
        for edge in ("low", "high"):
            key = f"dc_{edge}_{tf}"
            dc[key] = col(key)
            ant = f"{key}_ant"
            dc[ant] = col(ant) if ant in selected else dc[key].copy()
    breakout = {
        "current_price": price, "true_lag": lag,
        "k_1m": col("k_1m"), "d_1m": col("d_1m"), "k_1m_prev": col("k_1m_prev"),
        "k_3m": col("k_3m"), "d_3m": col("d_3m"), "k_3m_prev": col("k_3m_prev"),
        "k_15m": col("k_15m"), "d_15m": col("d_15m"),
        "higher_high_15m": state("higher_high_15m", bool), "lower_low_15m": state("lower_low_15m", bool),
        "prev_cross_price": state("prev_cross_price"), "lr_slope": state("lr_slope"),
    }
    for tf in ("3m", "15m", "1h", "4h"):
        breakout[f"dc_high_{tf}"] = dc[f"dc_high_{tf}"]
        breakout[f"dc_low_{tf}"] = dc[f"dc_low_{tf}"]
    breakout_delta, _, pa_confirm = breakout_pa_score_array(breakout, is_long)
    directional = {"current_price": price, "k_1h": col("k_1h"), "d_1h": col("d_1h"),
                   "sma_200_1m": col("sma_200_1m"), "sma_200_15m": col("sma_200_15m"), "sma_200_1h": col("sma_200_1h"),
                   "ha_15m": col("ha_15m"), "ha_1h": col("ha_1h"),
                   "wt_score_15m": col("wt_score_15m"), "wt_score_1h": col("wt_score_1h")}
    for tf in ("15m", "1h", "4h"):
        directional[f"dc_basis_{tf}"] = col(f"dc_basis_{tf}")
    wt = {}
    for key in ("wt_divergence_1h", "wt_divergence_4h", "wt_momentum_state_1h", "wt_momentum_state_4h",
                "wt_trough_structure_1h", "wt_peak_structure_1h", "wt_trough_structure_4h", "wt_peak_structure_4h",
                "wt_cross_1h", "wt_cross_rising_1h", "wt_percentile_1h", "wt_percentile_4h", "wt_extreme_1h",
                "wt_trough_structure_D", "wt_peak_structure_D"):
        wt[key] = col(key)
    volume = {"current_price": price, "rel_vol": state("rel_vol"), "z_conviction": state("z_conviction"),
              "dc_high_1h": dc["dc_high_1h"], "dc_low_1h": dc["dc_low_1h"],
              "trend_ok": state("trend_ok", bool), "pa_confirm": pa_confirm, "pr_confirm": state("pr_confirm", bool),
              "k_1m_prev": breakout["k_1m_prev"], "k_1m": breakout["k_1m"], "k_3m": breakout["k_3m"]}
    history = {key: state(key, bool if key == "history_present" else float) for key in ("history_present", "win_rate", "total_trades", "total_realized_pnl")}
    rank = {"top_sent": state("top_sent", bool), "bottom_sent": state("bottom_sent", bool),
            "ranking_points": state("ranking_points"), "has_indicators": state("has_indicators", bool),
            "local_sentiment": state("local_sentiment"), "global_sentiment": state("global_sentiment"),
            "sentiment_classification": state("sentiment_classification", object)}
    post = {"k1": breakout["k_1m"], "d1": breakout["d_1m"], "k1_prev": breakout["k_1m_prev"],
            "k3": breakout["k_3m"], "d3": breakout["d_3m"], "k3_prev": breakout["k_3m_prev"],
            "k15": breakout["k_15m"], "d15": breakout["d_15m"], "k15_prev": col("k_15m_prev"),
            "true_lag": lag, "scalp_entry_detected": state("scalp_entry_detected", bool),
            "lower_low15": breakout["lower_low_15m"], "higher_high15": breakout["higher_high_15m"],
            "gain": state("gain"), "min_gain": float(getattr(cfg, "MIN_GAIN", 0.0)),
            "trend_continuation": state("trend_continuation", bool)}
    dc_sufficiency = {f"dc_{edge}_{tf}": dc[f"dc_{edge}_{tf}"] for tf in ("3m", "15m", "1h") for edge in ("low", "high")}
    pre_dc = {"current_price": price, "last_reduction_price": state("last_reduction_price"),
              "minutes_since_reduction": state("minutes_since_reduction"), "k_1m": breakout["k_1m"], "d_1m": breakout["d_1m"],
              "k_1m_prev": breakout["k_1m_prev"], "k_3m": breakout["k_3m"], "d_3m": breakout["d_3m"],
              "k_3m_prev": breakout["k_3m_prev"], "k_15m": breakout["k_15m"], "d_15m": breakout["d_15m"],
              "k_1h": col("k_1h"), "d_1h": col("d_1h"), "k_4h": col("k_4h"), "true_lag": lag,
              "lower_low_15m": breakout["lower_low_15m"], "higher_high_15m": breakout["higher_high_15m"],
              "position_last_reduction_price": state("position_last_reduction_price"),
              "position_last_augmentation_price": state("position_last_augmentation_price"),
              "dc_basis_15m": col("dc_basis_15m"), "dc_basis_1h": col("dc_basis_1h"),
              "dc_high_4h": dc["dc_high_4h"], "dc_high_4h_ant": dc["dc_high_4h_ant"],
              "dc_low_4h": dc["dc_low_4h"], "dc_low_4h_ant": dc["dc_low_4h_ant"],
              "recent_exit": state("recent_exit", bool), "k_3m_cross_over": state("k_3m_cross_over", bool),
              "k_3m_cross_under": state("k_3m_cross_under", bool)}
    return {"n": n, "cfg": cfg, "npz_arrays_bound": bool(arrays_bound), "score_before_dc": state("score_before_dc"), "stages": {
        "pre_dc_entry_state": pre_dc, "dc_structure": dc, "breakout_pa": breakout, "directional": directional, "wt_intelligence": wt,
        "volume_setup": volume, "tracker_history_modifier": history,
        "channel_confluence": {"npz": selected, "current_price": price, "use_ltf": bool(getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", False))},
        "rank_sentiment": rank, "post_rank_transform": post, "dc_sufficiency": dc_sufficiency,
        "late_score_including_research": {"npz": selected, "gain": state("gain"), "current_price": price},
        "finish_tail": {"current_price": price, "dc_high_1h": dc["dc_high_1h"], "dc_high_3m": dc["dc_high_3m"],
                        "dc_low_1h": dc["dc_low_1h"], "dc_low_3m": dc["dc_low_3m"],
                        "k_1m": breakout["k_1m"], "k_3m": breakout["k_3m"], "true_lag": lag,
                        "recommendation_before_tail": np.full(n, "WAIT", dtype=object)},
    }}

def _column(value, name, n, dtype=None):
    array = np.asarray(value, dtype=dtype).reshape(-1)
    if array.size != n:
        raise ValueError(f"{name} length {array.size} does not match n={n}")
    return array


def compose_asr_late_score(inputs, is_long, is_exit=False, post_late_entry_state=None):
    """Run the ordered NumPy score stages through the final tail.

    Post-late MQ and Delta state is consumed only when supplied explicitly. Earlier
    lifecycle gates and native result/action dispatch still prevent full-rate use.
    """
    n = int(inputs["n"])
    if n < 1:
        raise ValueError("n must be positive")
    stages = {key: dict(value) if isinstance(value, dict) else value for key, value in inputs["stages"].items()}
    arrays_bound = bool(inputs.get("npz_arrays_bound", False))
    decoded_npz = dict(stages["channel_confluence"]["npz"]) if arrays_bound else bind_asr_npz_arrays(stages["channel_confluence"]["npz"], inputs["cfg"])
    if "wt_intelligence" in stages:
        stages["wt_intelligence"] = dict(stages["wt_intelligence"]) if arrays_bound else bind_asr_npz_arrays(stages["wt_intelligence"], inputs["cfg"])
    if "directional" in stages:
        stages["directional"] = dict(stages["directional"]) if arrays_bound else bind_asr_npz_arrays(stages["directional"], inputs["cfg"])
    stages["channel_confluence"]["npz"] = decoded_npz
    if "late_score_including_research" in stages:
        late_npz = stages["late_score_including_research"]["npz"]
        stages["late_score_including_research"]["npz"] = dict(late_npz) if arrays_bound else bind_asr_npz_arrays(late_npz, inputs["cfg"])
    absent = [name for name in ASR_LATE_STAGE_ORDER if name != "market_quality_and_delta_entry_contributions" and name not in stages]
    if absent:
        raise ValueError(f"missing ordered ASR stages: {','.join(absent)}")
    pre_dc = stages["pre_dc_entry_state"]
    pre_dc_values = {key: value for key, value in pre_dc.items()}
    pre_dc_values["score"] = _column(inputs["score_before_dc"], "score_before_dc", n)
    pre_dc_result = pre_dc_entry_score_array(pre_dc_values, is_long)
    score = pre_dc_result["score"].copy()
    masks = {f"pre_dc:{key}": value for key, value in pre_dc_result["masks"].items()}
    dc_delta, dc_valid = dc_structure_votes_array(stages["dc_structure"], is_long)
    score += dc_delta
    masks["dc_valid_setup"] = dc_valid
    breakout_delta, breakout_masks, pa_confirm = breakout_pa_score_array(stages["breakout_pa"], is_long)
    score += breakout_delta
    masks.update({f"breakout:{key}": value for key, value in breakout_masks.items()})
    masks["pa_confirm"] = pa_confirm
    score += directional_score_array(stages["directional"], is_long)
    wt_delta, wt_trigger_count = wt_intelligence_score_array(stages["wt_intelligence"], is_long)
    score += wt_delta
    masks["wt_trigger_count"] = wt_trigger_count
    volume_delta, volume_setup, volume_masks = volume_setup_array(stages["volume_setup"], is_long)
    score += volume_delta
    masks["volume_setup_valid"] = volume_setup
    masks.update({f"volume:{key}": value for key, value in volume_masks.items()})
    history, history_masks = history_score_array(**stages["tracker_history_modifier"], is_exit=is_exit)
    masks.update({f"history:{key}": value for key, value in history_masks.items()})
    score, channel_masks = apply_channel_confluence_vec(
        stages["channel_confluence"]["npz"], n, is_long, score=score,
        current_price=stages["channel_confluence"]["current_price"],
        use_ltf=stages["channel_confluence"]["use_ltf"],
    )
    masks.update({f"channel:{key}": value for key, value in channel_masks.items()})
    rank = stages["rank_sentiment"]
    score = apply_rank_sentiment_vec(
        score, is_long, rank["top_sent"], rank["bottom_sent"], rank["ranking_points"],
        history, rank["has_indicators"], rank["local_sentiment"], rank["global_sentiment"],
        rank["sentiment_classification"],
    )
    post = stages["post_rank_transform"]
    score, pretrend_final_score = apply_asr_post_rank_vec(score, is_long, **post)
    masks["pretrend_final_score"] = pretrend_final_score
    sufficient = stages["dc_sufficiency"]
    score, dc_sufficient = apply_asr_dc_sufficiency_vec(
        score, sufficient["dc_high_3m"], sufficient["dc_low_3m"],
        sufficient["dc_high_15m"], sufficient["dc_low_15m"],
        sufficient["dc_high_1h"], sufficient["dc_low_1h"],
    )
    masks["dc_sufficient"] = dc_sufficient
    score_after_dc_sufficiency = score.copy()
    late = stages["late_score_including_research"]
    score, late_masks = apply_asr_late_score_vec(
        late["npz"], n, is_long, inputs["cfg"], late["gain"], score=score,
        current_price=late["current_price"], is_exit=is_exit,
    )
    masks.update({f"late:{key}": value for key, value in late_masks.items()})
    score_after_late_score = score.copy()
    if post_late_entry_state is not None and not is_exit:
        from vec_decisions.asr_post_late_entry import apply_asr_post_late_entry_array
        score, post_late_masks = apply_asr_post_late_entry_array(
            decoded_npz, inputs["cfg"], is_long, post_late_entry_state, score
        )
        masks.update({f"post_late:{key}": value for key, value in post_late_masks.items()})
    score_after_post_late_score = score.copy()
    tail = stages["finish_tail"]
    final_score, recommendation = finish_array(
        inputs["cfg"], score, tail["current_price"], is_long, tail["dc_high_1h"],
        tail["dc_high_3m"], tail["dc_low_1h"], tail["dc_low_3m"], tail["k_1m"],
        tail["k_3m"], tail["true_lag"], tail["recommendation_before_tail"],
    )
    final_score = np.where(pre_dc_result["quick_reentry"], 12, final_score)
    recommendation = np.where(pre_dc_result["quick_reentry"], "QUICK_REENTRY", recommendation)
    return {
        "score_before_dc": pre_dc_result["score"],
        "quick_reentry": pre_dc_result["quick_reentry"],
        "quick_reentry_reason": pre_dc_result["quick_reentry_reason"],
        "pr_confirm": pre_dc_result["pr_confirm"],
        "valid_setup_before_dc": pre_dc_result["valid_setup"],
        "score_after_dc_sufficiency": score_after_dc_sufficiency,
        "score_after_late_score": score_after_late_score,
        "score_after_post_late_score": score_after_post_late_score,
        "final_score": final_score,
        "recommendation": recommendation,
        "masks": masks,
        "stage_order": ASR_LATE_STAGE_ORDER,
        "complete_rate": False,
        "remaining_stages": ASR_REMAINING_AFTER_COMPOSER,
    }


def compose_asr_entry_candidate_array(prefix_result, late_inputs, is_long, upstream_entry_eligible, post_late_entry_state=None, lifecycle_state=None):
    """Join the causal entry prefix to the ordered post-7169 score chain.

    The caller supplies all earlier lifecycle/portfolio gates explicitly in
    ``upstream_entry_eligible`` and the exact causal state/arrays required by
    ``late_inputs``. This does not model scalp or exit lanes and is not a full
    AdvancedSignalRater.rate() until the currently uncovered prefix gates and
    native recommendation overrides and action dispatch are composed.
    """
    if not isinstance(prefix_result, dict) or not isinstance(late_inputs, dict):
        raise ValueError("prefix_result and late_inputs must be mappings")
    if "entry_allowed" not in prefix_result or "score" not in prefix_result:
        raise ValueError("prefix_result must contain score and entry_allowed")
    n = int(late_inputs.get("n", 0))
    if n < 1:
        raise ValueError("late_inputs.n must be positive")
    if upstream_entry_eligible is None:
        raise ValueError("upstream_entry_eligible must be explicit")
    upstream_detail = None
    if isinstance(upstream_entry_eligible, Mapping):
        from vec_decisions.asr_upstream_entry_gates import asr_upstream_entry_gates
        upstream_detail = asr_upstream_entry_gates(upstream_entry_eligible)
        upstream_raw = upstream_detail["eligible"]
    else:
        upstream_raw = upstream_entry_eligible
    score = _column(prefix_result["score"], "prefix score", n)
    prefix_allowed = _column(prefix_result["entry_allowed"], "prefix entry_allowed", n, bool)
    upstream = _column(upstream_raw, "upstream_entry_eligible", n, bool)
    allowed = prefix_allowed & upstream
    lifecycle_detail = None
    if lifecycle_state is not None:
        from vec_decisions.asr_no_double_augment_gate import asr_no_double_augment_gate
        lifecycle_detail = asr_no_double_augment_gate(lifecycle_state, late_inputs["cfg"], n)
        allowed &= ~lifecycle_detail["blocked"]
    inputs = dict(late_inputs)
    inputs["score_before_dc"] = np.where(allowed, score, 0.0)
    if post_late_entry_state is None:
        late = compose_asr_late_score(inputs, is_long, is_exit=False)
    else:
        late = compose_asr_late_score(inputs, is_long, is_exit=False, post_late_entry_state=post_late_entry_state)
    output = dict(late)
    output["score_before_dc"] = inputs["score_before_dc"].copy()
    output["entry_allowed"] = allowed
    output["final_score"] = np.where(allowed, late["final_score"], 0)
    output["recommendation"] = np.where(allowed, late["recommendation"], "WAIT")
    output["masks"] = dict(late["masks"])
    output["masks"]["upstream_entry_veto"] = ~upstream
    output["masks"]["entry_prefix_veto"] = upstream & ~prefix_allowed
    if lifecycle_detail is not None:
        output["masks"]["no_double_augment_veto"] = lifecycle_detail["blocked"]
        output["masks"]["position_has_recent_augmentation"] = lifecycle_detail["position_has_recent_augmentation"]
    if upstream_detail is not None:
        output["upstream_reason"] = upstream_detail["reason"]
        output["masks"].update({f"upstream:{key}": value for key,value in upstream_detail["masks"].items()})
    output["complete_rate"] = False
    output["remaining_stages"] = tuple(ASR_REMAINING_AFTER_COMPOSER) + (
        "native_recommendation_to_quick_open_or_augment_dispatch:ez_positions_quick.py:28341-29773",
    )
    return output


def evaluate_asr_entry_candidate_array(npz,n,is_long,cfg,prefix_context,late_causal_state,upstream_entry_state,score=None):
    """Run the bound prefix and ordered late score chain on one raw NPZ.

    `late_causal_state` feeds the native pre-DC/post-7169 and tracker/rank
    stages; `upstream_entry_state` carries exact account membership/permission
    facts. This is still a component candidate scorer, not a complete `rate()`
    or v12 action consumer while listed native stages remain uncovered.
    """
    from vec_decisions.asr_entry_prefix import score_asr_entry_prefix_array
    n=int(n)
    if n<1:
        raise ValueError("n must be positive")
    if "current_price" not in prefix_context or "current_price" not in late_causal_state:
        raise ValueError("both prefix and late causal state require current_price")
    prefix_price=_column(prefix_context["current_price"],"prefix current_price",n)
    late_price=_column(late_causal_state["current_price"],"late current_price",n)
    if not np.array_equal(prefix_price,late_price,equal_nan=True):
        raise ValueError("prefix and late-stage current_price must match bar-for-bar")
    candidate_prefix_context = dict(prefix_context)
    reentry_keys = ("true_lag", "prev_cross_price", "bounce_reentry_k_reset")
    reentry_present = any(key in late_causal_state for key in reentry_keys)
    if reentry_present:
        missing = [key for key in reentry_keys if key not in late_causal_state]
        if missing or "lifecycle_state" not in late_causal_state:
            raise ValueError(f"incomplete causal reentry binding inputs: {','.join(missing + ([] if 'lifecycle_state' in late_causal_state else ['lifecycle_state']))}")
        from vec_decisions.asr_reentry_inputs import bind_asr_reentry_inputs
        reentry = bind_asr_reentry_inputs(
            npz, cfg, late_causal_state["lifecycle_state"], n, is_long,
            late_causal_state["current_price"], late_causal_state["true_lag"],
            late_causal_state["prev_cross_price"], late_causal_state["bounce_reentry_k_reset"],
        )
        candidate_prefix_context.update(reentry["context"])
        candidate_prefix_context["htf_trend"] = reentry["htf_trend"]
        candidate_prefix_context["htf_trend_score"] = reentry["htf_trend_score"]
    prefix=score_asr_entry_prefix_array(npz,n,is_long,cfg,candidate_prefix_context,score=score)
    late_inputs=assemble_asr_composer_inputs(npz,cfg,late_causal_state,n,is_long)
    post_late_state = late_causal_state.get("post_late_entry_state")
    _delta_enabled = (cfg.get("DELTA_ENGINE_ENABLED", False) if isinstance(cfg, Mapping) else getattr(cfg, "DELTA_ENGINE_ENABLED", False))
    if bool(_delta_enabled):
        if "delta_tracker_available" not in late_causal_state:
            raise ValueError("DELTA_ENGINE_ENABLED requires explicit delta_tracker_available")
        available = np.broadcast_to(np.asarray(late_causal_state["delta_tracker_available"], dtype=bool), (n,))
        if np.any(available) and not np.all(available):
            raise ValueError("delta_tracker_available must be stable for this symbol replay")
        post_late_state = dict(post_late_state or {})
        post_late_state.setdefault("current_price", late_causal_state["current_price"])
        post_late_state["delta_tracker_available"] = available
        if np.all(available):
            replay = late_causal_state.get("delta_tracker_replay")
            if not isinstance(replay, Mapping):
                raise ValueError("active DeltaTracker requires delta_tracker_replay context")
            for key in ("tracker_cfg", "timestamps", "position_states"):
                if key not in replay:
                    raise ValueError(f"active DeltaTracker requires replay.{key}")
            if "symbol" not in replay:
                raise ValueError("active DeltaTracker requires replay.symbol")
            from vec_decisions.asr_delta_provider import replay_delta_tracker_arrays
            tracker_output = replay_delta_tracker_arrays(
                npz, symbol=replay["symbol"], cfg=replay["tracker_cfg"],
                use_ltf=bool(cfg.get("USE_1M_3M_SIGNALS_ENABLED", False) if isinstance(cfg, Mapping) else getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", False)),
                timestamps=replay["timestamps"], is_long=is_long,
                position_states=replay["position_states"],
                warmup_mask=replay.get("warmup_mask"),
                external_context=replay.get("external_context"),
            )
            post_late_state["delta_signal_present"] = tracker_output["delta_signal_present"]
            post_late_state["delta_entry_ok"] = tracker_output["delta_entry_ok"]
            post_late_state["delta_active_tfs"] = tracker_output["delta_active_tfs"]
            post_late_state["delta_tracker_state_scope"] = tracker_output["state_scope"]
            post_late_state["delta_tracker_missing_inputs"] = tracker_output["missing_read_keys"]
        else:
            post_late_state["delta_signal_present"] = np.zeros(n, dtype=bool)
    lifecycle_state = late_causal_state.get("lifecycle_state")
    kwargs = {}
    if post_late_state is not None:
        kwargs["post_late_entry_state"] = post_late_state
    if lifecycle_state is not None:
        kwargs["lifecycle_state"] = lifecycle_state
    result = compose_asr_entry_candidate_array(
        prefix, late_inputs, is_long, upstream_entry_state, **kwargs
    )
    result["prefix_result"]=prefix
    result["reentry_state_out"]=prefix.get("reentry_state_out")
    result["masks"].update({f"prefix:{key}":value for key,value in prefix.get("masks",{}).items()})
    result["complete_rate"]=False
    if reentry_present:
        result["htf_trend"] = reentry["htf_trend"]
        result["htf_trend_score"] = reentry["htf_trend_score"]
        result["reentry_inputs_bound"] = True
    else:
        result["reentry_inputs_bound"] = False
    return result

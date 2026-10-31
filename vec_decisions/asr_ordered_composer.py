"""Composable NumPy implementation of the explicitly ported ASR late score span.

The composer starts at the native pre-DC score and deliberately reports that it
is partial: later native late-score, research, and tail stages remain separate.
"""

import numpy as np

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


ASR_LATE_STAGE_ORDER = (
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
    "finish_tail",
)

ASR_REMAINING_AFTER_COMPOSER = ("pre_7410_native_score_mutations", "early_vetoes_and_lifecycle_gates", "market_quality_bonus", "delta_signal", "family_lane_contributors", "entry_threshold_and_action")



_ASR_LABELS = {
    "wt_divergence": {-2: "HIDDEN_BEAR", -1: "BEAR", 0: "", 1: "BULL", 2: "HIDDEN_BULL"},
    "wt_momentum_state": {-2: "EXHAUST_DOWN", -1: "IMPULSE_DOWN", 0: "", 1: "IMPULSE_UP", 2: "EXHAUST_UP"},
    "wt_peak_structure": {-1: "LH", 0: "", 1: "HH"},
    "wt_trough_structure": {-1: "LL", 0: "", 1: "HL"},
    "wt_cross": {-1: "BEAR", 0: "", 1: "BULL"},
    "ha": {-1: "red", 0: "", 1: "green"},
}

def bind_asr_npz_arrays(npz, cfg):
    """Apply per-symbol TF policy and decode measured NPZ enum columns for ASR stages."""
    use_ltf = bool(getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", False))
    selected = paired_npz_view(npz, use_ltf)
    result = dict(selected)
    for key, raw in tuple(result.items()):
        family = next((name for name in _ASR_LABELS if key.startswith(name + "_") and key[len(name) + 1:] in {"1m", "3m", "5m", "15m", "1h", "4h", "D", "W", "M"}), None)
        if family is None:
            continue
        array = np.asarray(raw)
        if array.dtype.kind not in "biuf":
            continue
        mapping = _ASR_LABELS[family]
        flat = array.reshape(-1)
        unknown = sorted({int(value) for value in flat if np.isfinite(value) and int(value) not in mapping})
        if unknown:
            raise ValueError(f"{key}: unrecognized encoded ASR labels {unknown}")
        decoded = np.asarray([mapping.get(int(value), "") for value in flat], dtype=object).reshape(array.shape)
        result[key] = decoded
    for key, raw in tuple(result.items()):
        if key.startswith("wt_cross_rising_") and np.asarray(raw).dtype.kind in "biuf":
            arr = np.asarray(raw)
            if np.any(~np.isin(arr, (0, 1))):
                raise ValueError(f"{key}: expected encoded boolean values 0/1")
            result[key] = arr.astype(bool)
        elif key.startswith("wt_extreme_") and np.asarray(raw).dtype.kind in "biuf":
            arr = np.asarray(raw)
            if np.any(~np.isin(arr, (0, 1))):
                raise ValueError(f"{key}: expected encoded boolean values 0/1")
            result[key] = arr.astype(bool)
    return result


def _column(value, name, n, dtype=float):
    array = np.asarray(value, dtype=dtype).reshape(-1)
    if array.size != n:
        raise ValueError(f"{name} length {array.size} does not match n={n}")
    return array


def compose_asr_late_score(inputs, is_long, is_exit=False):
    """Run the known ordered NumPy span from native DC votes through DC sufficiency.

    All stage inputs are explicit arrays. No lifecycle, sentiment, tracker, or
    portfolio values are synthesized. The return is not a complete ASR rate().
    """
    n = int(inputs["n"])
    if n < 1:
        raise ValueError("n must be positive")
    stages = inputs["stages"]
    decoded_npz = bind_asr_npz_arrays(stages["channel_confluence"]["npz"], inputs["cfg"])
    if "wt_intelligence" in stages:
        stages["wt_intelligence"] = bind_asr_npz_arrays(stages["wt_intelligence"], inputs["cfg"])
    if "directional" in stages:
        stages["directional"] = bind_asr_npz_arrays(stages["directional"], inputs["cfg"])
    stages["channel_confluence"]["npz"] = decoded_npz
    absent = [name for name in ASR_LATE_STAGE_ORDER if name not in stages]
    if absent:
        raise ValueError(f"missing ordered ASR stages: {','.join(absent)}")
    score = _column(inputs["score_before_dc"], "score_before_dc", n).copy()
    masks = {}
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
    late = stages["late_score_including_research"]
    score, late_masks = apply_asr_late_score_vec(
        late["npz"], n, is_long, inputs["cfg"], late["gain"], score=score,
        current_price=late["current_price"], is_exit=is_exit,
    )
    masks.update({f"late:{key}": value for key, value in late_masks.items()})
    tail = stages["finish_tail"]
    final_score, recommendation = finish_array(
        inputs["cfg"], score, tail["current_price"], is_long, tail["dc_high_1h"],
        tail["dc_high_3m"], tail["dc_low_1h"], tail["dc_low_3m"], tail["k_1m"],
        tail["k_3m"], tail["true_lag"], tail["recommendation_before_tail"],
    )
    return {
        "score_after_dc_sufficiency": score,
        "final_score": final_score,
        "recommendation": recommendation,
        "masks": masks,
        "stage_order": ASR_LATE_STAGE_ORDER,
        "complete_rate": False,
        "remaining_stages": ASR_REMAINING_AFTER_COMPOSER,
    }

"""Compose the explicitly ported ASR score stages from DC structure through final tail.

The caller must supply the exact native score immediately before the DC-stage
span and all external arrays. Earlier score mutations, vetoes, and action gates
are not included, so the result is not a complete AdvancedSignalRater.rate().
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
    use_ltf = bool(cfg.get("USE_1M_3M_SIGNALS_ENABLED", False) if isinstance(cfg, dict) else getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", False))
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
        invalid = [value.item() if isinstance(value, np.generic) else value for value in flat if not np.isfinite(value) or float(value) != float(int(value)) or int(value) not in mapping]
        if invalid:
            raise ValueError(f"{key}: unrecognized encoded ASR labels {invalid[:8]}")
        decoded = np.asarray([mapping[int(value)] for value in flat], dtype=object).reshape(array.shape)
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


def assemble_asr_composer_inputs(npz, cfg, causal_state, n, is_long):
    """Bind raw causal NPZ columns and explicit replay state into the composer schema.

    Missing arrays raise; the only defaults here are source-defined current-channel
    fallbacks for absent ``*_ant`` Donchian columns and native initial ``WAIT`` rec.
    """
    if not isinstance(causal_state, dict):
        raise ValueError("causal_state must be an explicit mapping")
    selected = bind_asr_npz_arrays(npz, cfg)
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
    return {"n": n, "cfg": cfg, "score_before_dc": state("score_before_dc"), "stages": {
        "dc_structure": dc, "breakout_pa": breakout, "directional": directional, "wt_intelligence": wt,
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


def compose_asr_late_score(inputs, is_long, is_exit=False):
    """Run the declared ordered NumPy span, including research and final tail.

    Every stage input is explicit. No lifecycle, sentiment, tracker, or portfolio
    values are synthesized. The return is not a complete ASR rate().
    """
    n = int(inputs["n"])
    if n < 1:
        raise ValueError("n must be positive")
    stages = {key: dict(value) if isinstance(value, dict) else value for key, value in inputs["stages"].items()}
    decoded_npz = bind_asr_npz_arrays(stages["channel_confluence"]["npz"], inputs["cfg"])
    if "wt_intelligence" in stages:
        stages["wt_intelligence"] = bind_asr_npz_arrays(stages["wt_intelligence"], inputs["cfg"])
    if "directional" in stages:
        stages["directional"] = bind_asr_npz_arrays(stages["directional"], inputs["cfg"])
    stages["channel_confluence"]["npz"] = decoded_npz
    if "late_score_including_research" in stages:
        late_npz = stages["late_score_including_research"]["npz"]
        stages["late_score_including_research"]["npz"] = bind_asr_npz_arrays(late_npz, inputs["cfg"])
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
    score_after_dc_sufficiency = score.copy()
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
        "score_after_dc_sufficiency": score_after_dc_sufficiency,
        "score_after_late_score": score,
        "final_score": final_score,
        "recommendation": recommendation,
        "masks": masks,
        "stage_order": ASR_LATE_STAGE_ORDER,
        "complete_rate": False,
        "remaining_stages": ASR_REMAINING_AFTER_COMPOSER,
    }

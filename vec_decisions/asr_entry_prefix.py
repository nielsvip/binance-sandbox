"""Ordered NumPy port of the AdvancedSignalRater entry score prefix.

This covers the source-ordered span from the HTF gate through channel/K/DC
cross scoring. Earlier lifecycle checks and later inline score families are
outside this function, so callers must not treat it as a complete rate().
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from vec_decisions.asr_mts_entry_gate import mts_entry_gate_array
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
    arrays = paired_npz_view(npz, bool(_cfg(cfg, "USE_1M_3M_SIGNALS_ENABLED", False)))
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

    proposed, channel_masks = apply_channel_k_cross_score_vec(
        score_arrays, n, is_long, result, bool(_cfg(cfg, "USE_1M_3M_SIGNALS_ENABLED", False))
    )
    add("channel_k_cross", proposed, channel_masks)
    return {
        "score": result,
        "entry_allowed": ~blocked,
        "masks": masks,
        "complete_rate": False,
        "covered_native_spans": ("ez_positions_quick.py:4696-4880 extracted score/gate blocks", "ez_positions_quick.py:4956-5010 channel/K/DC-cross and adjacent directional scores"),
        "remaining": (
            "pre_score_lifecycle_gates", "regime_input_producer", "entry_atr_and_chase_gates:ez_positions_quick.py:4881-4955",
            "inline_research_and_family_scores:ez_positions_quick.py:5070-5154", "later_inline_family_scores:ez_positions_quick.py:5155-7409",
            "reentry_state_machine",
            "action_threshold_and_execution",
        ),
    }

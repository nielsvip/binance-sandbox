"""Ordered ASR score segment after the no-double-augment gate."""
import numpy as np

from vec_decisions.asr_dc_position_score import apply_dc_position_score_scalar, apply_dc_position_score_vec
from vec_decisions.asr_research_score import apply_research_score_scalar, apply_research_score_vec
from vec_decisions.asr_score_core import apply_gain_quality_bonus_scalar, apply_gain_quality_bonus_vec
from vec_decisions.asr_input_adapter import paired_npz_view


def apply_asr_late_score_scalar(score, reasons, indicators, current_price, is_long, cfg, gain, dc_values, k_3m, d_3m, k_3m_prev, k_15m, is_exit=False):
    """Apply native knife, gain, DC-position and research score stages in order."""
    result = float(score)
    if is_long:
        knife = (k_3m < d_3m and k_3m < 20 and k_3m < k_3m_prev) or k_15m < 30
        if knife:
            reasons.append("Falling_Knife_3m")
    else:
        knife = (k_3m > d_3m and k_3m > 80 and k_3m > k_3m_prev) or k_15m > 70
        if knife:
            reasons.append("Rocket_Ship_3m")
    if knife:
        result -= 10.0
    result, _ = apply_gain_quality_bonus_scalar(result, gain)
    result, reasons = apply_dc_position_score_scalar(result, reasons, dc_values, current_price, is_long)
    result, reasons = apply_research_score_scalar(result, reasons, indicators, current_price, is_long, cfg, is_exit)
    return result, reasons


def apply_asr_late_score_vec(npz, n, is_long, cfg, gain, score=None, current_price=None, is_exit=False):
    """NumPy counterpart for the same ordered contiguous score segment."""
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if result.shape != (n,):
        raise ValueError("score must have shape (n,)")
    use_ltf = bool(getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", False))
    npz = paired_npz_view(npz, use_ltf)
    k3 = _column(npz, "k_3m", n, 50.0)
    d3 = _column(npz, "d_3m", n, 50.0)
    k3_prev = _column(npz, "k_3m_prev", n, 50.0)
    k15 = _column(npz, "k_15m", n, 50.0)
    if is_long:
        knife = ((k3 < d3) & (k3 < 20) & (k3 < k3_prev)) | (k15 < 30)
    else:
        knife = ((k3 > d3) & (k3 > 80) & (k3 > k3_prev)) | (k15 > 70)
    result -= knife.astype(float) * 10.0
    result, gain_masks = apply_gain_quality_bonus_vec(result, gain)
    result, dc_masks = apply_dc_position_score_vec(npz, n, is_long, result, current_price, use_ltf)
    result, research_masks = apply_research_score_vec(npz, n, is_long, cfg, result, current_price, is_exit)
    masks = {"KNIFE_PENALTY": knife}
    masks.update(gain_masks)
    masks.update({f"{key}": value for key, value in dc_masks.items()})
    masks.update({f"RESEARCH_{key}": value for key, value in research_masks.items()})
    return result, masks


def _column(npz, key, n, default):
    if key not in npz:
        return np.full(n, default, dtype=float)
    try:
        arr = np.asarray(npz[key], dtype=float).reshape(-1)
        return arr if arr.size == n else np.full(n, default, dtype=float)
    except (TypeError, ValueError):
        return np.full(n, default, dtype=float)

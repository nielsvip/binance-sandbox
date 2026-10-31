"""Ordered MTS quality and Momentum Interception entry score bonuses."""

import numpy as np


def mts_mi_score_scalar(values, is_long, cfg):
    score = 0.0
    reasons = []
    bottom = values["mts_bottom"]
    quality = values["mts_entry_quality"]
    bottom_strong = getattr(cfg, "MTS_BOTTOM_STRONG_THRESHOLD", 40.0)
    bottom_bonus = getattr(cfg, "MTS_BOTTOM_BONUS_THRESHOLD", 25.0)
    quality_strong = getattr(cfg, "MTS_ENTRY_QUALITY_STRONG", 40.0)
    quality_bonus = getattr(cfg, "MTS_ENTRY_QUALITY_BONUS", 25.0)
    if bottom > bottom_strong:
        score += 8
        reasons.append(f"MTS_STRONG_BOTTOM({bottom:.0f},+8)")
    elif bottom > bottom_bonus:
        score += 4
        reasons.append(f"MTS_BOTTOM({bottom:.0f},+4)")
    if quality > quality_strong:
        score += 5
        reasons.append(f"MTS_ENTRY_QUALITY({quality:.0f},+5)")
    elif quality > quality_bonus:
        score += 2
        reasons.append(f"MTS_ENTRY_OK({quality:.0f},+2)")
    if getattr(cfg, "MI_ENTRY_ENABLED", False):
        structure_bonus = getattr(cfg, "MI_ENTRY_STRUCT_BONUS", 10)
        exhaustion_bonus = getattr(cfg, "MI_ENTRY_EXHAUST_BONUS", 8)
        checks = (
            ("wt_trough_structure_1h", "HL", structure_bonus, "MI_HL_1h"),
            ("wt_trough_structure_4h", "HL", structure_bonus, "MI_HL_4h"),
            ("wt_momentum_state_1h", "EXHAUST_DOWN", exhaustion_bonus, "MI_EXH_DOWN_1h"),
            ("wt_momentum_state_4h", "EXHAUST_DOWN", exhaustion_bonus, "MI_EXH_DOWN_4h"),
            ("wt_divergence_1h", "BULL", exhaustion_bonus, "MI_BULL_DIV_1h"),
        ) if is_long else (
            ("wt_peak_structure_1h", "LH", structure_bonus, "MI_LH_1h"),
            ("wt_peak_structure_4h", "LH", structure_bonus, "MI_LH_4h"),
            ("wt_momentum_state_1h", "EXHAUST_UP", exhaustion_bonus, "MI_EXH_UP_1h"),
            ("wt_momentum_state_4h", "EXHAUST_UP", exhaustion_bonus, "MI_EXH_UP_4h"),
            ("wt_divergence_1h", "BEAR", exhaustion_bonus, "MI_BEAR_DIV_1h"),
        )
        for key, expected, bonus, tag in checks:
            if values[key] == expected:
                score += bonus
                reasons.append(f"{tag}(+{bonus})")
    return score, reasons


def mts_mi_score_array(values, is_long, cfg):
    bottom = np.asarray(values["mts_bottom"], dtype=np.float64)
    quality = np.asarray(values["mts_entry_quality"], dtype=np.float64)
    shape = np.broadcast_shapes(bottom.shape, quality.shape)
    score = np.zeros(shape, dtype=np.float64)
    masks = {}

    def add(name, mask, weight):
        nonlocal score
        masks[name] = mask
        score += np.asarray(mask, dtype=bool) * float(weight)

    bottom_strong = float(getattr(cfg, "MTS_BOTTOM_STRONG_THRESHOLD", 40.0))
    bottom_bonus = float(getattr(cfg, "MTS_BOTTOM_BONUS_THRESHOLD", 25.0))
    quality_strong = float(getattr(cfg, "MTS_ENTRY_QUALITY_STRONG", 40.0))
    quality_bonus = float(getattr(cfg, "MTS_ENTRY_QUALITY_BONUS", 25.0))
    add("MTS_STRONG_BOTTOM", bottom > bottom_strong, 8)
    add("MTS_BOTTOM", (bottom <= bottom_strong) & (bottom > bottom_bonus), 4)
    add("MTS_ENTRY_QUALITY", quality > quality_strong, 5)
    add("MTS_ENTRY_OK", (quality <= quality_strong) & (quality > quality_bonus), 2)
    if getattr(cfg, "MI_ENTRY_ENABLED", False):
        structure_bonus = float(getattr(cfg, "MI_ENTRY_STRUCT_BONUS", 10))
        exhaustion_bonus = float(getattr(cfg, "MI_ENTRY_EXHAUST_BONUS", 8))
        checks = (
            ("wt_trough_structure_1h", "HL", structure_bonus, "MI_HL_1h"),
            ("wt_trough_structure_4h", "HL", structure_bonus, "MI_HL_4h"),
            ("wt_momentum_state_1h", "EXHAUST_DOWN", exhaustion_bonus, "MI_EXH_DOWN_1h"),
            ("wt_momentum_state_4h", "EXHAUST_DOWN", exhaustion_bonus, "MI_EXH_DOWN_4h"),
            ("wt_divergence_1h", "BULL", exhaustion_bonus, "MI_BULL_DIV_1h"),
        ) if is_long else (
            ("wt_peak_structure_1h", "LH", structure_bonus, "MI_LH_1h"),
            ("wt_peak_structure_4h", "LH", structure_bonus, "MI_LH_4h"),
            ("wt_momentum_state_1h", "EXHAUST_UP", exhaustion_bonus, "MI_EXH_UP_1h"),
            ("wt_momentum_state_4h", "EXHAUST_UP", exhaustion_bonus, "MI_EXH_UP_4h"),
            ("wt_divergence_1h", "BEAR", exhaustion_bonus, "MI_BEAR_DIV_1h"),
        )
        for key, expected, bonus, tag in checks:
            add(tag, np.asarray(values[key], dtype=object) == expected, bonus)
    return score, masks

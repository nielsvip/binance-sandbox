"""Ordered post-rank score multipliers and DC-sufficiency stage from ASR."""
from __future__ import annotations

import numpy as np


def apply_asr_post_rank_scalar(score, is_long, k1, d1, k1_prev, k3, d3, k3_prev, k15, d15, k15_prev, true_lag, scalp_entry_detected, lower_low15, higher_high15, gain, min_gain, trend_continuation):
    result = float(score)
    c1_long = k1 > d1
    c1_short = k1 < d1 and true_lag < 10.0
    if not c1_long and is_long:
        result = 0.3 * result
    if is_long and k1 < 20 or k3 < 20:
        result = 1.5 * result
    if not is_long and k1 > 80 or k3 > 80:
        result = 1.5 * result
    if not c1_short and not is_long:
        result = 0.3 * result
    if not scalp_entry_detected:
        if (is_long and (k15 < d15 or lower_low15)) or (not is_long and (k15 > d15 or higher_high15)):
            result -= 5.0
        if (is_long and k3 < d3) or (not is_long and k3 > d3):
            result -= 5.0
    if result >= 5.0:
        if gain > 3 * min_gain:
            result += 4.0
        elif gain > 2 * min_gain:
            result += 3.0
        elif gain > min_gain:
            result += 1.5
    if (is_long and k1 > 85 or k3 > 85) or (not is_long and k1 < 15 or k3 < 15):
        result = 0.2 * result
    if (is_long and (k1 < d1 and true_lag < 10.0) and k3 < d3) or (not is_long and k1 > d1 and k3 > d3):
        result = 0.1 * result
    elif (is_long and ((k1 < d1 and true_lag < 10.0) or k3 < d3)) or (not is_long and (k1 > d1 or k3 > d3)):
        result = 0.4 * result
    if (is_long and k1 > k1_prev + 40.0 or k3 > k3_prev + 30.0 or k15 > k15_prev + 25.0) or (not is_long and k1 < k1_prev - 40.0 or k3 < k3_prev - 30.0 or k15 < k15_prev - 25.0):
        result += 6.0
    final_score = max(0, min(30, int(round(result))))
    if trend_continuation:
        result = max(0.8 * result, 10.0)
    return result, final_score


def apply_asr_dc_sufficiency_scalar(score, high3, low3, high15, low15, high1h, low1h):
    result = float(score)
    if low3 and low15 and low1h:
        width3 = (high3 - low3) / low3
        sufficient = width3 > 0.3 * (high1h - low1h) / low1h or width3 > 0.5 * (high15 - low15) / low15
        if not sufficient:
            result *= 0.35
        return result, bool(sufficient)
    return result, True


def apply_asr_post_rank_vec(score, is_long, k1, d1, k1_prev, k3, d3, k3_prev, k15, d15, k15_prev, true_lag, scalp_entry_detected, lower_low15, higher_high15, gain, min_gain, trend_continuation):
    arrays = np.broadcast_arrays(*[np.asarray(value) for value in (score, k1, d1, k1_prev, k3, d3, k3_prev, k15, d15, k15_prev, true_lag, scalp_entry_detected, lower_low15, higher_high15, gain, trend_continuation)])
    result = np.asarray(arrays[0], dtype=np.float64).copy()
    k1, d1, k1_prev, k3, d3, k3_prev, k15, d15, k15_prev, lag = [np.asarray(a, dtype=np.float64) for a in arrays[1:11]]
    scalp, low_low15, high_high15 = [np.asarray(a, dtype=bool) for a in arrays[11:14]]
    gain = np.asarray(arrays[14], dtype=np.float64)
    trend = np.asarray(arrays[15], dtype=bool)
    if is_long:
        result *= np.where(k1 <= d1, 0.3, 1.0)
    result *= np.where(((is_long and (k1 < 20)) | (k3 < 20)), 1.5, 1.0)
    result *= np.where(((not is_long and (k1 > 80)) | (k3 > 80)), 1.5, 1.0)
    if not is_long:
        result *= np.where((k1 >= d1) | (lag >= 10.0), 0.3, 1.0)
    if is_long:
        first_penalty = (k15 < d15) | low_low15
        second_penalty = k3 < d3
    else:
        first_penalty = (k15 > d15) | high_high15
        second_penalty = k3 > d3
    result -= (~scalp & first_penalty).astype(float) * 5.0
    result -= (~scalp & second_penalty).astype(float) * 5.0
    gain_add = np.select((gain > 3 * min_gain, gain > 2 * min_gain, gain > min_gain), (4.0, 3.0, 1.5), default=0.0)
    result += (result >= 5.0).astype(float) * gain_add
    if is_long:
        extreme = (k1 > 85) | (k3 > 85) | (k3 < 15)
        both_against = (k1 < d1) & (lag < 10.0) & (k3 < d3)
        one_against = ((k1 < d1) & (lag < 10.0)) | (k3 < d3)
        overshoot = (k1 > k1_prev + 40.0) | (k3 > k3_prev + 30.0) | (k15 > k15_prev + 25.0) | (k3 < k3_prev - 30.0) | (k15 < k15_prev - 25.0)
    else:
        extreme = (k1 < 15) | (k3 < 15) | (k3 > 85)
        both_against = (k1 > d1) & (k3 > d3)
        one_against = (k1 > d1) | (k3 > d3)
        overshoot = (k3 > k3_prev + 30.0) | (k15 > k15_prev + 25.0) | (k1 < k1_prev - 40.0) | (k3 < k3_prev - 30.0) | (k15 < k15_prev - 25.0)
    result *= np.where(extreme, 0.2, 1.0)
    result *= np.where(both_against, 0.1, np.where(one_against, 0.4, 1.0))
    result += overshoot.astype(float) * 6.0
    final_score = np.clip(np.rint(result), 0, 30).astype(np.int64)
    result = np.where(trend, np.maximum(0.8 * result, 10.0), result)
    return result, final_score


def apply_asr_dc_sufficiency_vec(score, high3, low3, high15, low15, high1h, low1h):
    values = np.broadcast_arrays(*[np.asarray(value, dtype=np.float64) for value in (score, high3, low3, high15, low15, high1h, low1h)])
    result, h3, l3, h15, l15, h1, l1 = [v.copy() for v in values]
    valid = (l3 != 0.0) & (l15 != 0.0) & (l1 != 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        width3 = (h3 - l3) / l3
        sufficient = (width3 > 0.3 * (h1 - l1) / l1) | (width3 > 0.5 * (h15 - l15) / l15)
    sufficient |= ~valid
    result = np.where(valid & ~sufficient, result * 0.35, result)
    return result, sufficient

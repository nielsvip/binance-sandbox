"""Directional score votes immediately following ASR's rate2 log."""

import numpy as np


def directional_score_scalar(values, is_long):
    """Return the native ordered additive score for scalar indicator values."""
    score = 0.0
    if is_long and values["k_1h"] > values["d_1h"]:
        score += 1.0
    if not is_long and values["k_1h"] < values["d_1h"]:
        score += 1.0
    for timeframe in ("15m", "1h", "4h"):
        basis = values[f"dc_basis_{timeframe}"]
        if is_long and values["current_price"] > basis or not is_long and values["current_price"] < basis:
            score += 1.0
        if is_long and values["current_price"] < basis or not is_long and values["current_price"] > basis:
            score -= 2.0
    for timeframe in ("1m", "15m", "1h"):
        average = values[f"sma_200_{timeframe}"]
        if is_long and values["current_price"] > average or not is_long and values["current_price"] < average:
            score += 1.0
        if is_long and values["current_price"] < average or not is_long and values["current_price"] > average:
            score -= 2.0
    ha15 = values["ha_15m"]
    ha1h = values["ha_1h"]
    if is_long:
        if ha15 == "green":
            score += 1.5
        if ha1h == "green":
            score += 1.5
        if ha15 == "red":
            score -= 2.0
    else:
        if ha15 == "red":
            score += 1.5
        if ha1h == "red":
            score += 1.5
        if ha15 == "green":
            score -= 2.0
    wt15 = values["wt_score_15m"]
    wt1h = values["wt_score_1h"]
    if is_long:
        if wt15 > 0 and wt1h > -20:
            score += 1.0
        if wt15 < -50:
            score += 1.5
    else:
        if wt15 < 0 and wt1h < 20:
            score += 1.0
        if wt15 > 50:
            score += 1.5
    return score


def directional_score_array(values, is_long):
    """Return one native-equivalent additive score per bar."""
    current = np.asarray(values["current_price"])
    score = np.zeros(np.broadcast_shapes(current.shape, np.asarray(values["k_1h"]).shape), dtype=np.float64)
    k1h = np.asarray(values["k_1h"])
    d1h = np.asarray(values["d_1h"])
    score += np.where(k1h > d1h if is_long else k1h < d1h, 1.0, 0.0)
    for timeframe in ("15m", "1h", "4h"):
        basis = np.asarray(values[f"dc_basis_{timeframe}"])
        aligned = current > basis if is_long else current < basis
        opposed = current < basis if is_long else current > basis
        score += np.where(aligned, 1.0, 0.0)
        score += np.where(opposed, -2.0, 0.0)
    for timeframe in ("1m", "15m", "1h"):
        average = np.asarray(values[f"sma_200_{timeframe}"])
        aligned = current > average if is_long else current < average
        opposed = current < average if is_long else current > average
        score += np.where(aligned, 1.0, 0.0)
        score += np.where(opposed, -2.0, 0.0)
    ha15 = np.asarray(values["ha_15m"])
    ha1h = np.asarray(values["ha_1h"])
    score += np.where(ha15 == ("green" if is_long else "red"), 1.5, 0.0)
    score += np.where(ha1h == ("green" if is_long else "red"), 1.5, 0.0)
    score += np.where(ha15 == ("red" if is_long else "green"), -2.0, 0.0)
    wt15 = np.asarray(values["wt_score_15m"])
    wt1h = np.asarray(values["wt_score_1h"])
    if is_long:
        score += np.where((wt15 > 0) & (wt1h > -20), 1.0, 0.0)
        score += np.where(wt15 < -50, 1.5, 0.0)
    else:
        score += np.where((wt15 < 0) & (wt1h < 20), 1.0, 0.0)
        score += np.where(wt15 > 50, 1.5, 0.0)
    return score

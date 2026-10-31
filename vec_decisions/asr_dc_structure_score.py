"""Ordered Donchian structure votes used by AdvancedSignalRater."""

import numpy as np


def dc_structure_votes_scalar(values, is_long):
    """Return (score_delta, ordered reasons, valid_setup) for native scalar inputs."""
    direction = 1.0 if is_long else -1.0
    low = {tf: values[f"dc_low_{tf}"] for tf in ("3m", "15m", "1h", "4h")}
    high = {tf: values[f"dc_high_{tf}"] for tf in ("3m", "15m", "1h", "4h")}
    low_ant = {tf: values[f"dc_low_{tf}_ant"] for tf in low}
    high_ant = {tf: values[f"dc_high_{tf}_ant"] for tf in high}
    changed = {tf: (low[tf] - low_ant[tf]) * direction > 0 or (high[tf] - high_ant[tf]) * direction > 0 for tf in low}
    against = {tf: (low[tf] - low_ant[tf]) * direction < 0 or (high[tf] - high_ant[tf]) * direction < 0 for tf in low}
    both_against = {tf: (low[tf] - low_ant[tf]) * direction < 0 and (high[tf] - high_ant[tf]) * direction < 0 for tf in low}
    reasons = []
    score = 0.0
    valid_setup = False
    if all(changed[tf] for tf in ("3m", "15m", "1h", "4h")):
        valid_setup = True
        score += 4.0
        reasons.append("dc 3-15-1h-4h up" if is_long else "dc 3-15-1h-4h down")
    if changed["3m"] and changed["15m"] and (changed["1h"] or changed["4h"]):
        valid_setup = True
        score += 3.0
        reasons.append("dc 3-15-1h or 4h up" if is_long else "dc 3-15-1h or 4h down")
    if changed["3m"] or changed["15m"] or changed["1h"] or changed["4h"]:
        valid_setup = True
        score += 1.0
        reasons.append("dc 3-15 or 1h or 4h up" if is_long else "dc 3-15 or 1h or 4h down")
    if both_against["3m"] or both_against["15m"]:
        score -= 4.0
        reasons.append("dc 3 and 15 down" if is_long else "dc 3 and 15 up")
    if against["3m"] and against["15m"]:
        score -= 3.0
        reasons.append("dc 3 or 15 down" if is_long else "dc 3 or 15 up")
    if both_against["1h"] or both_against["4h"]:
        score -= 2.0
        reasons.append("dc 1h or 4h down" if is_long else "dc 1h or 4h up")
    return score, reasons, valid_setup


def dc_structure_votes_array(values, is_long):
    """Return per-bar score delta and valid-setup arrays for NumPy inputs."""
    direction = 1.0 if is_long else -1.0
    changed = {}
    against = {}
    both_against = {}
    for tf in ("3m", "15m", "1h", "4h"):
        low_delta = (np.asarray(values[f"dc_low_{tf}"]) - np.asarray(values[f"dc_low_{tf}_ant"])) * direction
        high_delta = (np.asarray(values[f"dc_high_{tf}"]) - np.asarray(values[f"dc_high_{tf}_ant"])) * direction
        changed[tf] = (low_delta > 0) | (high_delta > 0)
        against[tf] = (low_delta < 0) | (high_delta < 0)
        both_against[tf] = (low_delta < 0) & (high_delta < 0)
    shape = np.broadcast_shapes(*(array.shape for array in changed.values()))
    score = np.zeros(shape, dtype=np.float64)
    valid = np.zeros(shape, dtype=bool)
    all4 = changed["3m"] & changed["15m"] & changed["1h"] & changed["4h"]
    three_plus = changed["3m"] & changed["15m"] & (changed["1h"] | changed["4h"])
    any_tf = changed["3m"] | changed["15m"] | changed["1h"] | changed["4h"]
    vote4 = all4
    vote3 = three_plus
    vote1 = any_tf
    vote_minus4 = both_against["3m"] | both_against["15m"]
    vote_minus3 = against["3m"] & against["15m"]
    vote_minus2 = both_against["1h"] | both_against["4h"]
    for mask, weight in ((vote4, 4.0), (vote3, 3.0), (vote1, 1.0), (vote_minus4, -4.0), (vote_minus3, -3.0), (vote_minus2, -2.0)):
        score += np.where(mask, weight, 0.0)
    valid = vote4 | vote3 | vote1
    return score, valid

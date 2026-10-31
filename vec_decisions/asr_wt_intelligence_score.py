"""Ordered WT divergence, momentum, structure, percentile, and trigger score."""

import numpy as np


def wt_intelligence_score_scalar(values, is_long):
    score = 0.0
    reasons = []
    div1 = values["wt_divergence_1h"]
    div4 = values["wt_divergence_4h"]
    mom1 = values["wt_momentum_state_1h"]
    mom4 = values.get("wt_momentum_state_4h", "")
    if div1 == "BULL" and is_long:
        score += 4.0
        reasons.append("WT_BULL_DIV_1h")
    elif div1 == "BEAR" and not is_long:
        score += 4.0
        reasons.append("WT_BEAR_DIV_1h")
    elif div1 == "HIDDEN_BULL" and is_long:
        score += 2.0
        reasons.append("WT_HIDDEN_BULL_1h")
    elif div1 == "HIDDEN_BEAR" and not is_long:
        score += 2.0
        reasons.append("WT_HIDDEN_BEAR_1h")
    if div4 == "BULL" and is_long:
        score += 5.0
        reasons.append("WT_BULL_DIV_4h")
    elif div4 == "BEAR" and not is_long:
        score += 5.0
        reasons.append("WT_BEAR_DIV_4h")
    if is_long and mom1 == "EXHAUST_DOWN":
        score += 3.0
        reasons.append("WT_EXHAUST_DOWN_1h(buy_signal)")
    elif not is_long and mom1 == "EXHAUST_UP":
        score += 3.0
        reasons.append("WT_EXHAUST_UP_1h(sell_signal)")
    if is_long and mom1 == "IMPULSE_UP":
        score += 1.5
        reasons.append("WT_IMPULSE_UP_1h")
    elif not is_long and mom1 == "IMPULSE_DOWN":
        score += 1.5
        reasons.append("WT_IMPULSE_DOWN_1h")
    if is_long and mom1 == "IMPULSE_DOWN":
        score -= 4.0
        reasons.append("WT_AGAINST_IMPULSE_DN(-4)")
    elif not is_long and mom1 == "IMPULSE_UP":
        score -= 4.0
        reasons.append("WT_AGAINST_IMPULSE_UP(-4)")
    trough1 = values["wt_trough_structure_1h"]
    peak1 = values["wt_peak_structure_1h"]
    trough4 = values["wt_trough_structure_4h"]
    peak4 = values["wt_peak_structure_4h"]
    if is_long and trough1 == "HL":
        score += 2.0
        reasons.append("WT_HL_1h(strengthening)")
    elif not is_long and peak1 == "LH":
        score += 2.0
        reasons.append("WT_LH_1h(weakening)")
    if is_long and peak1 == "LH" and peak4 == "LH":
        score -= 3.0
        reasons.append("WT_PEAK_EXHAUST_1h+4h")
    elif not is_long and trough1 == "HL" and trough4 == "HL":
        score -= 3.0
        reasons.append("WT_TROUGH_EXHAUST_1h+4h")
    cross1 = values["wt_cross_1h"]
    rising1 = values["wt_cross_rising_1h"]
    if cross1 == "BULL" and is_long:
        score += 3.0
        reasons.append("WT_BULL_CROSS_1h")
        if rising1:
            score += 2.0
            reasons.append("WT_CROSS_RISING(stronger)")
    elif cross1 == "BEAR" and not is_long:
        score += 3.0
        reasons.append("WT_BEAR_CROSS_1h")
        if rising1 is False:
            score += 2.0
            reasons.append("WT_CROSS_FALLING(stronger)")
    pct1 = values["wt_percentile_1h"]
    if is_long and pct1 < 10:
        score += 3.0
        reasons.append(f"WT_DEEP_OS({pct1:.0f}pct)")
    elif not is_long and pct1 > 90:
        score += 3.0
        reasons.append(f"WT_DEEP_OB({pct1:.0f}pct)")
    if values["wt_extreme_1h"]:
        if is_long and pct1 > 90:
            score -= 4.0
            reasons.append("WT_EXTREME_OB_BLOCK")
        elif not is_long and pct1 < 10:
            score -= 4.0
            reasons.append("WT_EXTREME_OS_BLOCK")
    pct4 = values["wt_percentile_4h"]
    div4 = values["wt_divergence_4h"]
    struct4 = values["wt_trough_structure_4h"] if is_long else values["wt_peak_structure_4h"]
    structD = values["wt_trough_structure_D"] if is_long else values["wt_peak_structure_D"]
    if is_long:
        trigger_count = int(pct1 < 30) + int(pct4 < 30)
        trigger_count += int(values["wt_divergence_1h"] in ("BULL", "HIDDEN_BULL"))
        trigger_count += int(div4 in ("BULL", "HIDDEN_BULL"))
        trigger_count += int(struct4 == "HL") + int(structD == "HL")
    else:
        trigger_count = int(pct1 > 70) + int(pct4 > 70)
        trigger_count += int(values["wt_divergence_1h"] in ("BEAR", "HIDDEN_BEAR"))
        trigger_count += int(div4 in ("BEAR", "HIDDEN_BEAR"))
        trigger_count += int(struct4 == "LH") + int(structD == "LH")
    if trigger_count >= 4:
        score += 4.0
        reasons.append(f"WT_TRIGGER_STRONG({trigger_count}/6)")
    elif trigger_count >= 3:
        score += 2.5
        reasons.append(f"WT_TRIGGER_OK({trigger_count}/6)")
    elif trigger_count >= 2:
        score += 1.0
        reasons.append(f"WT_TRIGGER_WEAK({trigger_count}/6)")
    elif trigger_count == 0:
        score -= 3.0
        reasons.append("WT_NO_TRIGGER(0/6)")
    return score, reasons, trigger_count


def wt_intelligence_score_array(values, is_long):
    """Return score delta and six-factor trigger counts for NumPy values."""
    arrays = {key: np.asarray(value) for key, value in values.items()}
    shape = np.broadcast_shapes(*(value.shape for value in arrays.values()))
    score = np.zeros(shape, dtype=np.float64)
    div1 = arrays["wt_divergence_1h"]
    div4 = arrays["wt_divergence_4h"]
    mom1 = arrays["wt_momentum_state_1h"]
    mom4 = arrays.get("wt_momentum_state_4h", np.full(shape, "", dtype=object))

    def add(mask, amount):
        nonlocal score
        score += np.where(mask, amount, 0.0)

    add((div1 == ("BULL" if is_long else "BEAR")), 4.0)
    add((div1 == ("HIDDEN_BULL" if is_long else "HIDDEN_BEAR")), 2.0)
    add(div4 == ("BULL" if is_long else "BEAR"), 5.0)
    add(mom1 == ("EXHAUST_DOWN" if is_long else "EXHAUST_UP"), 3.0)
    add(mom1 == ("IMPULSE_UP" if is_long else "IMPULSE_DOWN"), 1.5)
    add(mom1 == ("IMPULSE_DOWN" if is_long else "IMPULSE_UP"), -4.0)
    trough1 = arrays["wt_trough_structure_1h"]
    peak1 = arrays["wt_peak_structure_1h"]
    trough4 = arrays["wt_trough_structure_4h"]
    peak4 = arrays["wt_peak_structure_4h"]
    add(trough1 == ("HL" if is_long else "__never__"), 2.0)
    add(peak1 == ("LH" if not is_long else "__never__"), 2.0)
    add((peak1 == "LH") & (peak4 == "LH") if is_long else (trough1 == "HL") & (trough4 == "HL"), -3.0)
    cross1 = arrays["wt_cross_1h"]
    rising = arrays["wt_cross_rising_1h"]
    add(cross1 == ("BULL" if is_long else "BEAR"), 3.0)
    if is_long:
        add((cross1 == "BULL") & np.asarray(rising, dtype=bool), 2.0)
    else:
        falling = np.frompyfunc(lambda value: isinstance(value, (bool, np.bool_)) and not bool(value), 1, 1)(rising).astype(bool)
        add((cross1 == "BEAR") & falling, 2.0)
    pct1 = np.asarray(arrays["wt_percentile_1h"], dtype=float)
    add((pct1 < 10) if is_long else (pct1 > 90), 3.0)
    extreme = np.asarray(arrays["wt_extreme_1h"], dtype=bool)
    add(extreme & ((pct1 > 90) if is_long else (pct1 < 10)), -4.0)
    pct4 = np.asarray(arrays["wt_percentile_4h"], dtype=float)
    if is_long:
        struct4 = trough4
        structD = arrays["wt_trough_structure_D"]
        trigger_count = (pct1 < 30).astype(int) + (pct4 < 30).astype(int)
        trigger_count += np.isin(div1, ("BULL", "HIDDEN_BULL"))
        trigger_count += np.isin(div4, ("BULL", "HIDDEN_BULL"))
        trigger_count += (struct4 == "HL").astype(int) + (structD == "HL").astype(int)
    else:
        struct4 = peak4
        structD = arrays["wt_peak_structure_D"]
        trigger_count = (pct1 > 70).astype(int) + (pct4 > 70).astype(int)
        trigger_count += np.isin(div1, ("BEAR", "HIDDEN_BEAR"))
        trigger_count += np.isin(div4, ("BEAR", "HIDDEN_BEAR"))
        trigger_count += (struct4 == "LH").astype(int) + (structD == "LH").astype(int)
    score += np.where(trigger_count >= 4, 4.0, np.where(trigger_count >= 3, 2.5, np.where(trigger_count >= 2, 1.0, np.where(trigger_count == 0, -3.0, 0.0))))
    return score, trigger_count

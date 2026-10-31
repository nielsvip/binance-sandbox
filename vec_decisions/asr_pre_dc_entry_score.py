"""Stateful entry-score mutations immediately before ASR's DC structure votes."""

import numpy as np


def pre_dc_entry_score_scalar(score, values, is_long):
    """Mirror native force/special re-entry, recent-exit, K4h, sniper and price structure."""
    result = float(score)
    reasons = []
    force_reentry = False
    if values["last_reduction_price"] > 0 and values["minutes_since_reduction"] < 1200:
        if is_long and values["current_price"] > values["last_reduction_price"] * 0.998:
            if values["k_1m"] > values["d_1m"] or values["k_3m"] > values["k_3m_prev"]:
                force_reentry = True
                reasons.append(f"RECLAIM_LEVEL_{values['last_reduction_price']}")
        elif not is_long and values["current_price"] < values["last_reduction_price"] * 1.002:
            if values["k_1m"] < values["d_1m"] or values["k_3m"] < values["k_3m_prev"]:
                force_reentry = True
                reasons.append(f"RECLAIM_LEVEL_{values['last_reduction_price']}")
    if force_reentry:
        result += 20.0
    special_reentry = False
    if is_long:
        condition_15m = values["k_15m"] < 30 or ((values["k_15m"] < values["d_15m"] or values["lower_low_15m"]) and ((values["k_15m"] > 50 and values["k_3m"] > values["d_3m"]) or (values["k_15m"] < 30 and values["k_3m"] > values["d_3m"])))
        if condition_15m and (values["k_1h"] > values["d_1h"] or values["k_1h"] > 50) and values["k_1m"] > values["k_1m_prev"]:
            special_reentry = True
            reasons.append("15mLow_1hHigh_ReEntry")
    else:
        condition_15m = values["k_15m"] > 70 or ((values["k_15m"] > values["d_15m"] or values["higher_high_15m"]) and ((values["k_15m"] < 50 and values["k_3m"] < values["d_3m"]) or (values["k_15m"] > 70 and values["k_3m"] < values["d_3m"])))
        if condition_15m and (values["k_1h"] < values["d_1h"] or values["k_1h"] < 50) and values["k_1m"] < values["k_1m_prev"]:
            special_reentry = True
            reasons.append("15mHigh_1hLow_ReEntry")
    c1_long = values["k_1m"] > values["d_1m"]
    c1_short = values["k_1m"] < values["d_1m"] and values["true_lag"] < 10.0
    if not c1_long and is_long and not special_reentry and not force_reentry:
        result *= 0.3
    if not c1_short and not is_long and not special_reentry and not force_reentry:
        result *= 0.3
    pr_confirm = bool(values["recent_exit"] and ((is_long and values["current_price"] > values["dc_basis_15m"]) or (not is_long and values["current_price"] < values["dc_basis_15m"])))
    if is_long and values["dc_high_4h"] > values["dc_high_4h_ant"]:
        result += 2.0
        reasons.append("4h_Exp_Up")
    elif not is_long and values["dc_low_4h"] < values["dc_low_4h_ant"]:
        result += 2.0
        reasons.append("4h_Exp_Down")
    if is_long:
        if values["k_4h"] < 50:
            result += 3.0
        elif values["k_4h"] > 85:
            result -= 3.0
    else:
        if values["k_4h"] > 50:
            result += 3.0
        elif values["k_4h"] < 15:
            result -= 3.0
    valid_setup = False
    set_a = bool(values["recent_exit"] and is_long and ((values["k_15m"] > 50 and values["k_3m"] > values["d_3m"]) or (values["k_15m"] < 30 and values["k_3m"] > values["d_3m"])) and values["k_1m"] > values["d_1m"] and values["true_lag"] < 10.0 and values["k_3m"] > values["d_3m"] and values["k_1m"] > 50 and values["k_3m"] < 50 and (values["k_15m"] > values["d_15m"] or values["higher_high_15m"]))
    set_b = bool(values["recent_exit"] and not is_long and ((values["k_15m"] < 50 and values["k_3m"] < values["d_3m"]) or (values["k_15m"] > 70 and values["k_3m"] < values["d_3m"])) and values["k_1m"] < values["d_1m"] and values["true_lag"] < 10.0 and values["k_3m"] < values["d_3m"] and values["k_1m"] > 50 and values["k_3m"] > 50 and (values["k_15m"] < values["d_15m"] or values["lower_low_15m"]))
    last_reduction = values["position_last_reduction_price"]
    set_c = bool(values["recent_exit"] and is_long and last_reduction > 0 and values["current_price"] > last_reduction and values["k_1m"] > values["d_1m"] and values["true_lag"] < 10.0 and values["k_1m"] < 50 and values["k_3m"] < 50 and (values["k_15m"] > values["d_15m"] or values["higher_high_15m"]))
    set_d = bool(values["recent_exit"] and not is_long and last_reduction > 0 and values["current_price"] < last_reduction and values["k_1m"] < values["d_1m"] and values["true_lag"] < 10.0 and values["k_1m"] > 50 and values["k_3m"] > 50 and (values["k_15m"] < values["d_15m"] or values["lower_low_15m"]))
    if set_a:
        valid_setup = True
        result += 3.0
        reasons.append("Trend_Continuation_ReEntry")
    if set_b:
        valid_setup = True
        result += 3.0
        reasons.append("Trend_Continuation_ReEntry")
    if set_c:
        valid_setup = True
        result += 5.0
        reasons.append("Continuing up")
    if set_d:
        valid_setup = True
        result += 5.0
        reasons.append("Continuing down")
    set_e = bool(
        values["recent_exit"]
        and (
            (is_long and (values["current_price"] > values["dc_basis_15m"] or values["current_price"] > values["dc_basis_1h"] * 1.03) and values["k_15m"] > values["d_15m"] and values["k_15m"] < 70)
            or (not is_long and (values["current_price"] < values["dc_basis_15m"] or values["current_price"] < values["dc_basis_1h"] * 0.97) and values["k_15m"] < values["d_15m"] and values["k_15m"] > 30)
        )
    )
    if set_e:
        result += 2.0
    quick_reentry = bool((set_a or set_b or set_c or set_d) and set_e)
    if quick_reentry:
        return 12.0, True, pr_confirm, True, tuple(reasons), {"force_reentry": force_reentry, "special_reentry": special_reentry, "set_a": set_a, "set_b": set_b, "set_c": set_c, "set_d": set_d, "set_e": set_e, "sniper": False, "higher_low": False, "price_rising": False, "lower_high": False, "price_falling": False}
    sniper = bool((is_long and values["k_1m_prev"] < 20 and values["k_1m"] > values["k_1m_prev"]) or (not is_long and values["k_1m_prev"] > 80 and values["k_1m"] < values["k_1m_prev"]))
    if sniper:
        valid_setup = True
        result += 1.0
        reasons.append("Sniper_Long" if is_long else "Sniper_Short")
    last_aug = values["position_last_augmentation_price"]
    highlow = bool(is_long and values["k_3m_cross_over"] and values["current_price"] < last_reduction and values["current_price"] > last_aug)
    rising = bool(is_long and not highlow and values["k_3m_cross_over"] and values["current_price"] > last_aug)
    lowhigh = bool(not is_long and values["k_3m_cross_under"] and values["current_price"] > last_reduction and values["current_price"] < last_aug)
    falling = bool(not is_long and not lowhigh and values["k_3m_cross_under"] and values["current_price"] < last_aug)
    if highlow or lowhigh:
        valid_setup = True
        result += 6.0
        reasons.append("higher low" if is_long else "lower high")
    elif rising or falling:
        valid_setup = True
        result += 2.0
        reasons.append("price rising" if is_long else "price falling")
    return result, quick_reentry, pr_confirm, valid_setup, tuple(reasons), {"force_reentry": force_reentry, "special_reentry": special_reentry, "set_a": set_a, "set_b": set_b, "set_c": set_c, "set_d": set_d, "set_e": set_e, "sniper": sniper, "higher_low": highlow, "price_rising": rising, "lower_high": lowhigh, "price_falling": falling}


def pre_dc_entry_score_array(values, is_long):
    """Return ordered per-bar score transforms, setup, and exact QUICK_REENTRY mask."""
    v = {key: np.asarray(value) for key, value in values.items()}
    n = np.asarray(v["current_price"]).size
    if any(array.reshape(-1).size != n for array in v.values()):
        raise ValueError("all pre-DC ASR inputs must have length n")
    f = {key: np.asarray(value, dtype=float).reshape(-1) for key, value in v.items() if key not in ("lower_low_15m", "higher_high_15m", "recent_exit", "k_3m_cross_over", "k_3m_cross_under")}
    b = {key: np.asarray(value, dtype=bool).reshape(-1) for key, value in v.items() if key in ("lower_low_15m", "higher_high_15m", "recent_exit", "k_3m_cross_over", "k_3m_cross_under")}
    score = f["score"].copy()
    force = np.zeros(n, bool)
    if is_long:
        force = (f["last_reduction_price"] > 0) & (f["minutes_since_reduction"] < 1200) & (f["current_price"] > f["last_reduction_price"] * .998) & ((f["k_1m"] > f["d_1m"]) | (f["k_3m"] > f["k_3m_prev"]))
    else:
        force = (f["last_reduction_price"] > 0) & (f["minutes_since_reduction"] < 1200) & (f["current_price"] < f["last_reduction_price"] * 1.002) & ((f["k_1m"] < f["d_1m"]) | (f["k_3m"] < f["k_3m_prev"]))
    score += force.astype(float) * 20.0
    if is_long:
        cond15 = (f["k_15m"] < 30) | (((f["k_15m"] < f["d_15m"]) | b["lower_low_15m"]) & (((f["k_15m"] > 50) & (f["k_3m"] > f["d_3m"])) | ((f["k_15m"] < 30) & (f["k_3m"] > f["d_3m"]))))
        special = cond15 & ((f["k_1h"] > f["d_1h"]) | (f["k_1h"] > 50)) & (f["k_1m"] > f["k_1m_prev"])
        c1 = f["k_1m"] > f["d_1m"]
    else:
        cond15 = (f["k_15m"] > 70) | (((f["k_15m"] > f["d_15m"]) | b["higher_high_15m"]) & (((f["k_15m"] < 50) & (f["k_3m"] < f["d_3m"])) | ((f["k_15m"] > 70) & (f["k_3m"] < f["d_3m"]))))
        special = cond15 & ((f["k_1h"] < f["d_1h"]) | (f["k_1h"] < 50)) & (f["k_1m"] < f["k_1m_prev"])
        c1 = (f["k_1m"] < f["d_1m"]) & (f["true_lag"] < 10.0)
    score *= np.where(~c1 & ~special & ~force, .3, 1.0)
    pr_confirm = b["recent_exit"] & ((f["current_price"] > f["dc_basis_15m"]) if is_long else (f["current_price"] < f["dc_basis_15m"]))
    dc_expand = f["dc_high_4h"] > f["dc_high_4h_ant"] if is_long else f["dc_low_4h"] < f["dc_low_4h_ant"]
    score += dc_expand.astype(float) * 2.0
    k4_bonus = np.where(f["k_4h"] < 50, 3.0, np.where(f["k_4h"] > 85, -3.0, 0.0)) if is_long else np.where(f["k_4h"] > 50, 3.0, np.where(f["k_4h"] < 15, -3.0, 0.0))
    score += k4_bonus
    if is_long:
        set_a = b["recent_exit"] & (((f["k_15m"] > 50) & (f["k_3m"] > f["d_3m"])) | ((f["k_15m"] < 30) & (f["k_3m"] > f["d_3m"]))) & (f["k_1m"] > f["d_1m"]) & (f["true_lag"] < 10) & (f["k_3m"] > f["d_3m"]) & (f["k_1m"] > 50) & (f["k_3m"] < 50) & ((f["k_15m"] > f["d_15m"]) | b["higher_high_15m"])
        set_b = np.zeros(n, bool)
        set_c = b["recent_exit"] & (f["position_last_reduction_price"] > 0) & (f["current_price"] > f["position_last_reduction_price"]) & (f["k_1m"] > f["d_1m"]) & (f["true_lag"] < 10) & (f["k_1m"] < 50) & (f["k_3m"] < 50) & ((f["k_15m"] > f["d_15m"]) | b["higher_high_15m"])
        set_d = np.zeros(n, bool)
        set_e = b["recent_exit"] & ((f["current_price"] > f["dc_basis_15m"]) | (f["current_price"] > f["dc_basis_1h"] * 1.03)) & (f["k_15m"] > f["d_15m"]) & (f["k_15m"] < 70)
    else:
        set_a = np.zeros(n, bool)
        set_b = b["recent_exit"] & (((f["k_15m"] < 50) & (f["k_3m"] < f["d_3m"])) | ((f["k_15m"] > 70) & (f["k_3m"] < f["d_3m"]))) & (f["k_1m"] < f["d_1m"]) & (f["true_lag"] < 10) & (f["k_3m"] < f["d_3m"]) & (f["k_1m"] > 50) & (f["k_3m"] > 50) & ((f["k_15m"] < f["d_15m"]) | b["lower_low_15m"])
        set_c = np.zeros(n, bool)
        set_d = b["recent_exit"] & (f["position_last_reduction_price"] > 0) & (f["current_price"] < f["position_last_reduction_price"]) & (f["k_1m"] < f["d_1m"]) & (f["true_lag"] < 10) & (f["k_1m"] > 50) & (f["k_3m"] > 50) & ((f["k_15m"] < f["d_15m"]) | b["lower_low_15m"])
        set_e = b["recent_exit"] & ((f["current_price"] < f["dc_basis_15m"]) | (f["current_price"] < f["dc_basis_1h"] * .97)) & (f["k_15m"] < f["d_15m"]) & (f["k_15m"] > 30)
    score += (set_a | set_b).astype(float) * 3.0
    score += (set_c | set_d).astype(float) * 5.0
    score += set_e.astype(float) * 2.0
    quick = (set_a | set_b | set_c | set_d) & set_e
    score = np.where(quick, 12.0, score)
    sniper = (f["k_1m_prev"] < 20) & (f["k_1m"] > f["k_1m_prev"]) if is_long else (f["k_1m_prev"] > 80) & (f["k_1m"] < f["k_1m_prev"])
    score += (~quick & sniper).astype(float)
    last_red, last_aug = f["position_last_reduction_price"], f["position_last_augmentation_price"]
    if is_long:
        highlow = ~quick & b["k_3m_cross_over"] & (f["current_price"] < last_red) & (f["current_price"] > last_aug)
        rising = ~quick & ~highlow & b["k_3m_cross_over"] & (f["current_price"] > last_aug)
        lowhigh = np.zeros(n, bool)
        falling = np.zeros(n, bool)
    else:
        lowhigh = ~quick & b["k_3m_cross_under"] & (f["current_price"] > last_red) & (f["current_price"] < last_aug)
        falling = ~quick & ~lowhigh & b["k_3m_cross_under"] & (f["current_price"] < last_aug)
        highlow = np.zeros(n, bool)
        rising = np.zeros(n, bool)
    score += (highlow | lowhigh).astype(float) * 6.0
    score += (rising | falling).astype(float) * 2.0
    valid = set_a | set_b | set_c | set_d | (~quick & sniper) | highlow | lowhigh | rising | falling
    quick_reason = np.asarray([f"a:{a} b:{bb} c:{c} d:{d} and price >< basis" if fire else "" for a, bb, c, d, fire in zip(set_a, set_b, set_c, set_d, quick)], dtype=object)
    return {"score": score, "quick_reentry": quick, "quick_reentry_reason": quick_reason, "pr_confirm": pr_confirm, "valid_setup": valid,
            "masks": {"force_reentry": force, "special_reentry": special, "set_a": set_a, "set_b": set_b,
                      "set_c": set_c, "set_d": set_d, "set_e": set_e, "sniper": sniper,
                      "higher_low": highlow, "price_rising": rising, "lower_high": lowhigh, "price_falling": falling}}

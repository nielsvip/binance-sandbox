"""Ordered DC price-breakout, K-acceleration, and PA confirmation score stage."""

import numpy as np


def breakout_pa_score_scalar(values, is_long):
    score = 0.0
    reasons = []
    px = values["current_price"]
    if is_long:
        if px >= values["dc_high_4h"] and values["k_1m"] > values["d_1m"] and values["true_lag"] < 10.0 and (values["k_15m"] > values["d_15m"] or values["higher_high_15m"]):
            score += 5
            reasons.append(">dc4hhigh")
        if px >= values["dc_high_1h"] and values["k_1m"] > values["d_1m"] and values["true_lag"] < 10.0 and (values["k_15m"] > values["d_15m"] or values["higher_high_15m"]):
            score += 4
            reasons.append(">dc1hhigh")
        if px >= values["dc_high_15m"] and values["k_1m"] > values["d_1m"] and values["true_lag"] < 10.0 and (values["k_15m"] > values["d_15m"] or values["higher_high_15m"]):
            score += 3
            reasons.append(">dc15mhigh")
        elif px >= values["dc_high_3m"] and values["k_1m"] > values["d_1m"] and values["true_lag"] < 10.0 and (values["k_15m"] > values["d_15m"] or values["higher_high_15m"]) and (values["k_3m"] < 40 or values["k_15m"] < 20):
            score += 6
            reasons.append(">dc3mhigh")
        elif px >= values["dc_high_3m"] and values["k_1m"] > values["d_1m"] and values["true_lag"] < 10.0 and (values["k_15m"] > values["d_15m"] or values["higher_high_15m"]):
            score += 2
            reasons.append(">dc3mhigh")
    else:
        if px <= values["dc_low_4h"] and values["k_1m"] < values["d_1m"] and values["true_lag"] < 10.0 and (values["k_15m"] < values["d_15m"] or values["lower_low_15m"]):
            score += 5
            reasons.append("<dc4hlow")
        if px <= values["dc_low_1h"] and values["k_3m"] < values["d_3m"] and (values["k_15m"] < values["d_15m"] or values["lower_low_15m"]):
            score += 4
            reasons.append("<dc1hlow")
        if px <= values["dc_low_15m"] and values["k_1m"] < values["d_1m"] and values["true_lag"] < 10.0 and (values["k_15m"] < values["d_15m"] or values["lower_low_15m"]) and (values["k_3m"] > 60 or values["k_15m"] > 80):
            score += 6
            reasons.append("<dc15mlow")
        elif px <= values["dc_low_3m"] and values["k_3m"] < values["d_3m"] and (values["k_15m"] < values["d_15m"] or values["lower_low_15m"]):
            score += 2
            reasons.append("<dc3mlow")
    k1, k1_prev = values["k_1m"], values["k_1m_prev"]
    k3, k3_prev = values["k_3m"], values["k_3m_prev"]
    if (is_long and k1 > k1_prev + 50.0 or k3 > k3_prev + 40.0) or (not is_long and k1 < k1_prev - 50.0 or k3 < k3_prev - 40.0):
        score += 7
    elif (is_long and k1 > k1_prev + 40.0 or k3 > k3_prev + 30.0) or (not is_long and k1 < k1_prev - 40.0 or k3 < k3_prev - 30.0):
        score += 3
    elif (is_long and k1 > k1_prev + 20.0 or k3 > k3_prev + 20.0) or (not is_long and k1 < k1_prev - 20.0 or k3 < k3_prev - 20.0):
        score += 1
    pa_confirm = False
    if is_long:
        if values["prev_cross_price"] != 0 and px >= values["prev_cross_price"]:
            pa_confirm = True
            score += 2
        elif values["lr_slope"] > 0:
            pa_confirm = True
            score += 1
    else:
        if values["prev_cross_price"] != 0 and px <= values["prev_cross_price"]:
            pa_confirm = True
            score += 2
        elif values["lr_slope"] < 0:
            pa_confirm = True
            score += 1
    return score, reasons, pa_confirm


def breakout_pa_score_array(values, is_long):
    def col(name, dtype=float):
        return np.asarray(values[name], dtype=dtype)

    px = col("current_price")
    lag = col("true_lag")
    k1, d1 = col("k_1m"), col("d_1m")
    k3, d3 = col("k_3m"), col("d_3m")
    k15, d15 = col("k_15m"), col("d_15m")
    score = np.zeros(np.broadcast_shapes(px.shape, k1.shape, k3.shape), dtype=np.float64)
    reasons = {}
    if is_long:
        timing = (k1 > d1) & (lag < 10.0) & ((k15 > d15) | col("higher_high_15m", bool))
        masks = ((">dc4hhigh", px >= col("dc_high_4h"), 5.0), (">dc1hhigh", px >= col("dc_high_1h"), 4.0), (">dc15mhigh", px >= col("dc_high_15m"), 3.0))
        for name, channel, weight in masks:
            reasons[name] = channel & timing
            score += reasons[name] * weight
        dc3 = (px >= col("dc_high_3m")) & timing
        fire15 = reasons[">dc15mhigh"]
        fire3strong = ~fire15 & dc3 & ((k3 < 40) | (k15 < 20))
        fire3normal = ~fire15 & dc3 & ~((k3 < 40) | (k15 < 20))
        score += fire3strong * 6.0 + fire3normal * 2.0
        reasons[">dc3mhigh"] = fire3strong | fire3normal
    else:
        timing4 = (k1 < d1) & (lag < 10.0) & ((k15 < d15) | col("lower_low_15m", bool))
        reasons["<dc4hlow"] = (px <= col("dc_low_4h")) & timing4
        score += reasons["<dc4hlow"] * 5.0
        timing1 = (k3 < d3) & ((k15 < d15) | col("lower_low_15m", bool))
        reasons["<dc1hlow"] = (px <= col("dc_low_1h")) & timing1
        score += reasons["<dc1hlow"] * 4.0
        timing15 = (k1 < d1) & (lag < 10.0) & ((k15 < d15) | col("lower_low_15m", bool))
        fire15 = (px <= col("dc_low_15m")) & timing15 & ((k3 > 60) | (k15 > 80))
        fire3 = (px <= col("dc_low_3m")) & timing1 & ~fire15
        score += fire15 * 6.0 + fire3 * 2.0
        reasons["<dc15mlow"] = fire15
        reasons["<dc3mlow"] = fire3
    k1prev, k3prev = col("k_1m_prev"), col("k_3m_prev")
    tier7 = ((k1 > k1prev + 50.0) if is_long else False) | (k3 > k3prev + 40.0) | ((k1 < k1prev - 50.0) if not is_long else False) | (k3 < k3prev - 40.0)
    tier3 = ((k1 > k1prev + 40.0) if is_long else False) | (k3 > k3prev + 30.0) | ((k1 < k1prev - 40.0) if not is_long else False) | (k3 < k3prev - 30.0)
    tier1 = ((k1 > k1prev + 20.0) if is_long else False) | (k3 > k3prev + 20.0) | ((k1 < k1prev - 20.0) if not is_long else False) | (k3 < k3prev - 20.0)
    score += tier7 * 7.0 + (~tier7 & tier3) * 3.0 + (~tier7 & ~tier3 & tier1)
    reasons["K_ACCEL_7"] = tier7
    reasons["K_ACCEL_3"] = ~tier7 & tier3
    reasons["K_ACCEL_1"] = ~tier7 & ~tier3 & tier1
    cross = col("prev_cross_price")
    slope = col("lr_slope")
    if is_long:
        cross_fire = (cross != 0) & (px >= cross)
        slope_fire = ~cross_fire & (slope > 0)
        pa = cross_fire | slope_fire
        score += np.where(cross_fire, 2.0, np.where(slope_fire, 1.0, 0.0))
    else:
        cross_fire = (cross != 0) & (px <= cross)
        slope_fire = ~cross_fire & (slope < 0)
        pa = cross_fire | slope_fire
        score += np.where(cross_fire, 2.0, np.where(slope_fire, 1.0, 0.0))
    return score, reasons, pa

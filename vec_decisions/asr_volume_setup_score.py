"""Ordered volume/conviction/resistance votes and ASR setup validity flags."""

import numpy as np


def volume_setup_scalar(values, is_long):
    score = 0.0
    reasons = []
    rel_vol = values["rel_vol"]
    if rel_vol > 1.5:
        score += 1.0
        reasons.append("HighVol")
    elif rel_vol < 0.5:
        score -= 1.5
        reasons.append("LowVol")
    zconviction = values["z_conviction"]
    if zconviction > 20:
        score += 2.0
        reasons.append(f"Z_Conviction({int(zconviction or 0.0)})")
    if is_long:
        if values["current_price"] > values["dc_high_1h"] * 0.995 and rel_vol < 2.0:
            score -= 2.0
            reasons.append("Near_1H_Res")
    elif values["current_price"] < values["dc_low_1h"] * 1.005 and rel_vol < 2.0:
        score -= 2.0
        reasons.append("Near_1H_Sup")
    valid = False
    if values["trend_ok"] and values["pa_confirm"]:
        valid = True
        reasons.append("Trend+PA")
    elif values["pr_confirm"]:
        valid = True
        reasons.append("ReEntry_Struct")
    elif is_long and values["k_1m_prev"] < 10 and values["k_3m"] < 25 and values["k_1m"] > values["k_1m_prev"]:
        valid = True
        reasons.append("Sniper_Long")
    elif not is_long and values["k_1m_prev"] > 90 and values["k_3m"] > 75 and values["k_1m"] < values["k_1m_prev"]:
        valid = True
        reasons.append("Sniper_Short")
    return score, reasons, valid


def volume_setup_array(values, is_long):
    """Return score deltas, setup-valid masks, and component masks by bar."""
    px = np.asarray(values["current_price"], dtype=float)
    n = px.size
    score = np.zeros(n, dtype=np.float64)
    masks = {}

    def col(name, dtype=float):
        array = np.asarray(values[name], dtype=dtype).reshape(-1)
        if array.size != n:
            raise ValueError(f"{name} length {array.size} does not match current_price length {n}")
        return array

    rel_vol = col("rel_vol")
    high = rel_vol > 1.5
    low = (~high) & (rel_vol < 0.5)
    score += high.astype(float) - low.astype(float) * 1.5
    masks["HighVol"] = high
    masks["LowVol"] = low
    z = col("z_conviction")
    z_mask = z > 20
    score += z_mask.astype(float) * 2.0
    masks["Z_Conviction"] = z_mask
    if is_long:
        near = (px > col("dc_high_1h") * 0.995) & (rel_vol < 2.0)
        masks["Near_1H_Res"] = near
    else:
        near = (px < col("dc_low_1h") * 1.005) & (rel_vol < 2.0)
        masks["Near_1H_Sup"] = near
    score -= near.astype(float) * 2.0
    trend = col("trend_ok", bool)
    pa = col("pa_confirm", bool)
    pr = col("pr_confirm", bool)
    kprev = col("k_1m_prev")
    k3 = col("k_3m")
    k1 = col("k_1m")
    trend_pa = trend & pa
    reentry = ~trend_pa & pr
    if is_long:
        sniper = ~trend_pa & ~reentry & (kprev < 10) & (k3 < 25) & (k1 > kprev)
    else:
        sniper = ~trend_pa & ~reentry & (kprev > 90) & (k3 > 75) & (k1 < kprev)
    masks["Trend+PA"] = trend_pa
    masks["ReEntry_Struct"] = reentry
    masks["Sniper"] = sniper
    return score, trend_pa | reentry | sniper, masks

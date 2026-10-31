"""WT/K entry-quality flags and the ordered native score penalty."""

import numpy as np


def stoch_entry_score_scalar(values, is_long, is_exit=False):
    long_ok = (
        (values["wt_bullish_scalp"] and values["wt_score_scalp"] > -20 and values["wt_velocity_scalp"] > -1.0)
        or (values["wt_cross_bull_scalp"] and values["wt_score_scalp"] < -30)
        or (values["k_15m"] <= 32 and values["k_3m"] > values["d_3m"])
    )
    short_ok = (
        (not values["wt_bullish_scalp"] and values["wt_score_scalp"] < -10 and values["wt_velocity_scalp"] < 1.0)
        or (values["wt_momentum_state_1h"] == "EXHAUST_UP" and not values["wt_bullish_scalp"])
        or (values["k_15m"] > 70 and values["k_15m"] < values["k_15m_prev"] and values["k_3m"] < values["d_3m"])
    )
    reasons = []
    score = 0.0
    if not is_exit and is_long and not long_ok:
        score -= 15.0
        reasons.append(f"WT_PENALTY_LONG(-15,wts={values['wt_score_scalp']:.0f},vel={values['wt_velocity_scalp']:.1f})")
    elif not is_exit and not is_long and not short_ok:
        score -= 15.0
        reasons.append(f"WT_PENALTY_SHORT(-15,wts={values['wt_score_scalp']:.0f},vel={values['wt_velocity_scalp']:.1f})")
    return score, reasons, long_ok, short_ok


def stoch_entry_score_array(values, is_long, is_exit=False):
    def col(name, dtype=float):
        return np.asarray(values[name], dtype=dtype)

    bullish = col("wt_bullish_scalp", bool)
    score = col("wt_score_scalp")
    velocity = col("wt_velocity_scalp")
    k15 = col("k_15m")
    k3 = col("k_3m")
    d3 = col("d_3m")
    k15prev = col("k_15m_prev")
    mom1 = col("wt_momentum_state_1h", object)
    long_ok = (bullish & (score > -20) & (velocity > -1.0)) | (col("wt_cross_bull_scalp", bool) & (score < -30)) | ((k15 <= 32) & (k3 > d3))
    short_ok = ((~bullish) & (score < -10) & (velocity < 1.0)) | ((mom1 == "EXHAUST_UP") & (~bullish)) | ((k15 > 70) & (k15 < k15prev) & (k3 < d3))
    if is_exit:
        delta = np.zeros(np.broadcast_shapes(long_ok.shape, short_ok.shape), dtype=np.float64)
    elif is_long:
        delta = np.where(long_ok, 0.0, -15.0)
    else:
        delta = np.where(short_ok, 0.0, -15.0)
    return delta, long_ok, short_ok

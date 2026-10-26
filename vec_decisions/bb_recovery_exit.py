"""Switch-backed BB recovery exit predicate and estimated-fee guard."""

import math

import numpy as np


def bb_recovery_exit_mask(price, upper, lower, stoch_k, is_long):
    price = np.asarray(price, dtype=float)
    upper = np.asarray(upper, dtype=float)
    lower = np.asarray(lower, dtype=float)
    stoch_k = np.asarray(stoch_k, dtype=float)
    price, upper, lower, stoch_k = np.broadcast_arrays(price, upper, lower, stoch_k)
    valid = (
        np.isfinite(price)
        & np.isfinite(upper)
        & np.isfinite(lower)
        & np.isfinite(stoch_k)
        & (upper > 0.0)
        & (lower > 0.0)
    )
    midpoint = (upper + lower) / 2.0
    if is_long:
        return valid & (price >= midpoint) & (stoch_k >= 70.0)
    return valid & (price <= midpoint) & (stoch_k <= 30.0)


def positive_after_estimated_fees(gain_pct, fee_pct):
    try:
        gain = float(gain_pct)
        fee = float(fee_pct)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(gain) and math.isfinite(fee) and fee >= 0.0 and gain - fee > 0.0


def tradier_recovery_bypass_mask(
    entry_price,
    price,
    bb_upper_1h,
    bb_lower_1h,
    atr_3m,
    ha_3m,
    k_3m,
    k_3m_prev,
    is_long,
    tolerance_pct,
    tolerance_atr_mult,
):
    entry, price, upper, lower, atr, k, k_prev = np.broadcast_arrays(
        np.asarray(entry_price, dtype=float),
        np.asarray(price, dtype=float),
        np.asarray(bb_upper_1h, dtype=float),
        np.asarray(bb_lower_1h, dtype=float),
        np.asarray(atr_3m, dtype=float),
        np.asarray(k_3m, dtype=float),
        np.asarray(k_3m_prev, dtype=float),
    )
    ha = np.broadcast_to(np.asarray(ha_3m), entry.shape)
    try:
        ha_num = np.asarray(ha, dtype=float)
    except (TypeError, ValueError):
        ha_num = np.zeros(entry.shape, dtype=float)
    ha_text = np.char.lower(ha.astype(str))
    valid = (
        np.isfinite(entry)
        & np.isfinite(price)
        & np.isfinite(upper)
        & np.isfinite(lower)
        & np.isfinite(atr)
        & np.isfinite(k)
        & np.isfinite(k_prev)
        & (entry > 0.0)
        & (price > 0.0)
        & (upper > 0.0)
        & (lower > 0.0)
    )
    tol_pct = max(0.0, float(tolerance_pct))
    tol_atr = max(0.0, float(tolerance_atr_mult))
    tol_abs = np.where((tol_atr > 0.0) & (atr > 0.0), tol_atr * atr, entry * tol_pct / 100.0)
    within = np.abs(price - entry) <= tol_abs
    if is_long:
        reversal = (ha_text == "red") | (ha_num < 0.0) | (k < k_prev)
        return valid & (entry > upper) & within & reversal
    reversal = (ha_text == "green") | (ha_num > 0.0) | (k > k_prev)
    return valid & (entry < lower) & within & reversal

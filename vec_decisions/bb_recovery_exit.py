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

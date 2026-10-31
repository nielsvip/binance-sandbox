"""Account portfolio-ratio score contribution for AdvancedSignalRater."""

import numpy as np


def portfolio_ratio_scalar(ratio, minimum, maximum, is_long):
    """Return the ordered score adjustment and native reason for one account."""
    if is_long and ratio < minimum:
        return 15.0, [f"RATIO_FORTIFY_LONG(R={ratio:.2f}<{minimum},+15)"]
    if not is_long and ratio > maximum:
        return 15.0, [f"RATIO_FORTIFY_SHORT(R={ratio:.2f}>{maximum},+15)"]
    if is_long and ratio > maximum:
        return -10.0, [f"RATIO_OVERWEIGHT_LONG(R={ratio:.2f},-10)"]
    if not is_long and ratio < minimum:
        return -10.0, [f"RATIO_OVERWEIGHT_SHORT(R={ratio:.2f},-10)"]
    return 0.0, []


def portfolio_ratio_array(ratio, eligible, minimum, maximum, is_long):
    """Return per-bar score adjustments and mutually-exclusive reason masks."""
    values, active = np.broadcast_arrays(np.asarray(ratio, dtype=np.float64), np.asarray(eligible, dtype=bool))
    if is_long:
        fortify = active & (values < minimum)
        overweight = active & ~fortify & (values > maximum)
        score = fortify.astype(float) * 15.0 - overweight.astype(float) * 10.0
        return score, {"RATIO_FORTIFY_LONG": fortify, "RATIO_OVERWEIGHT_LONG": overweight}
    fortify = active & (values > maximum)
    overweight = active & ~fortify & (values < minimum)
    score = fortify.astype(float) * 15.0 - overweight.astype(float) * 10.0
    return score, {"RATIO_FORTIFY_SHORT": fortify, "RATIO_OVERWEIGHT_SHORT": overweight}

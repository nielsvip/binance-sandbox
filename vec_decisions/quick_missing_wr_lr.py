"""15m-grid WR/LR pullback entry mask for the QUICK_OPEN_GOOD producer."""
import numpy as np


def wr_lr_pullback_15m_mask(ha_4h, k_1h, k_15m, k_15m_prev, wt_cross_15m, in_list, is_long):
    """Mirror the live WR/LR branch when USE_1M_3M_SIGNALS_ENABLED is False.

    The live 3m and 1m slots both resolve to 15m under that mode. Membership is
    supplied by the caller from the historical WR/LR list archive, not assumed.
    """
    htf = np.char.lower(np.asarray(ha_4h, dtype=str))
    k1h = np.asarray(k_1h, dtype=float)
    k15 = np.asarray(k_15m, dtype=float)
    k15p = np.asarray(k_15m_prev, dtype=float)
    cross = np.asarray(wt_cross_15m, dtype=str)
    listed = np.asarray(in_list, dtype=bool)
    n = k15.shape[0]
    if not (htf.shape == k1h.shape == k15p.shape == cross.shape == listed.shape == (n,)):
        raise ValueError("WR/LR pullback inputs must be 1D arrays with matching lengths")
    if is_long:
        return listed & (htf != "red") & (k1h < 45.0) & (k15 < 45.0) & (k15 < 40.0) & ((cross == "BULL") | ((k15 > k15p) & (k15 < 30.0)))
    return listed & (htf != "green") & (k1h > 55.0) & (k15 > 55.0) & (k15 > 60.0) & ((cross == "BEAR") | ((k15 < k15p) & (k15 > 70.0)))


def wr_lr_good_score(score, fire_mask):
    """Mirror the live `score = max(score, 22)` performed on a WR/LR fire."""
    values = np.asarray(score, dtype=float)
    fires = np.asarray(fire_mask, dtype=bool)
    if values.shape != fires.shape:
        raise ValueError("score and fire_mask must have matching shapes")
    return np.where(fires, np.maximum(values, 22.0), values)

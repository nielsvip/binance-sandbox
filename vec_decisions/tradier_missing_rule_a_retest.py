"""Tradier RULE_A_RETEST entry mask on the frozen 15-minute decision grid.

The live rule's sub-15m stochastic trigger is mapped to the available 15m
stochastic arrays under SWITCH_WIRING_GUIDE.md §4. No 3m/5m series is inferred.
The caller owns flat-position and tradeability gates and emits the OPEN reason.
"""
import numpy as np


def rule_a_retest_mask(npz, n, is_long, cfg, safe):
    """Return bars where the stock RULE_A_RETEST entry predicate fires."""
    if not bool(getattr(cfg, "BREAKOUT_RETEST_ARMED_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    close = safe(npz, "close", n, 0.0)
    wt1_d = safe(npz, "wt1_D", n, 0.0)
    wt2_d = safe(npz, "wt2_D", n, 0.0)
    wt1_w = safe(npz, "wt1_W", n, 0.0)
    wt2_w = safe(npz, "wt2_W", n, 0.0)
    dc_basis_d = safe(npz, "dc_basis_D", n, 0.0)
    atr_d = safe(npz, "atr_D", n, 0.0)
    wt1_15m = safe(npz, "wt1_15m", n, 0.0)
    wt2_15m = safe(npz, "wt2_15m", n, 0.0)
    wt1_1h = safe(npz, "wt1_1h", n, 0.0)
    wt2_1h = safe(npz, "wt2_1h", n, 0.0)
    k_15m = safe(npz, "stoch_k_15m", n, 50.0)
    d_15m = safe(npz, "stoch_d_15m", n, 50.0)
    k_15m_prev = np.roll(k_15m, 1)
    if n:
        k_15m_prev[0] = k_15m[0]
    try:
        retest_mult = float(getattr(cfg, "BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT", 0.30))
    except (TypeError, ValueError):
        retest_mult = 0.30
    valid = (np.abs(wt1_d) > 1e-9) & (np.abs(wt2_d) > 1e-9) & (dc_basis_d > 0) & (atr_d > 0) & (close > 0)
    dist_atr = np.abs(close - dc_basis_d) / np.where(atr_d > 0, atr_d, 1.0)
    near_basis = dist_atr < retest_mult
    if is_long:
        armed = (wt1_d > wt2_d) & (wt1_w > wt2_w) & (close > dc_basis_d)
        stoch_turn = (k_15m_prev < 30) & (k_15m > d_15m) & (k_15m > k_15m_prev)
        confirmations = (wt1_15m > wt2_15m) & (wt1_1h > wt2_1h)
    else:
        armed = (wt1_d < wt2_d) & (wt1_w < wt2_w) & (close < dc_basis_d)
        stoch_turn = (k_15m_prev > 70) & (k_15m < d_15m) & (k_15m < k_15m_prev)
        confirmations = (wt1_15m < wt2_15m) & (wt1_1h < wt2_1h)
    return valid & near_basis & armed & stoch_turn & confirmations

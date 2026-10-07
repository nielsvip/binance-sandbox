"""vec_paths/dc_momentum_bota_scorer.py — DC momentum + BOTA pullback + decent-gain push.

LIVE SOURCE: ez_positions_quick.py:AdvancedSignalRater.rate() lines 3178-3212.
DECENT_GAIN_PROTECT (early-return path at 3213-3216) is NOT extracted — stays inline.

Pure function. Same module called by live + vec backtest = bit-identical scoring.
"""
from __future__ import annotations
from typing import List, Tuple


def score_dc_momentum_bota(
    is_long: bool,
    is_exit: bool,
    current_price: float,
    pnl_pct: float,
    dc_low_15m: float,
    dc_high_15m: float,
    dc_high_3m: float,
    dc_low_3m: float,
    k_3m: float,
    k_3m_prev: float,
    k_1m: float,
    k_1m_prev: float,
    d_1m: float,
    d_3m: float,
    k_15m: float,
    true_lag: float,
    ema_50_15m: float,
    sma_200_15m: float,
) -> Tuple[float, List[str], float, bool]:
    """Mirrors ez_positions_quick.py rate() lines 3178-3212 — verbatim.

    Returns: (delta_score, reasons_added, dc_expansion_pct, is_massive_expansion)
    """
    score = 0.0
    reasons: List[str] = []
    dc_expansion_pct = 0.0
    if dc_low_15m > 0:
        dc_expansion_pct = ((dc_high_15m - dc_low_15m) / dc_low_15m) * 100.0
    is_massive_expansion = dc_expansion_pct > 4.5
    if not is_exit:
        in_dc_breakout_long = dc_high_3m > 0 and current_price >= dc_high_3m
        in_dc_breakout_short = dc_low_3m > 0 and current_price <= dc_low_3m
        if is_long and in_dc_breakout_long:
            if k_3m > k_3m_prev or (k_1m > d_1m and true_lag < 10.0):
                score += 25.0
                reasons.append("DC_MOMENTUM_SCALP_REENTRY_LONG(+25)")
        elif not is_long and in_dc_breakout_short:
            if k_3m < k_3m_prev or (k_1m < d_1m and true_lag < 10.0):
                score += 25.0
                reasons.append("DC_MOMENTUM_SCALP_REENTRY_SHORT(+25)")
        if is_massive_expansion and "DC_MOMENTUM_SCALP" not in str(reasons):
            if is_long:
                near_sma = current_price <= (ema_50_15m * 1.03) or current_price <= (sma_200_15m * 1.03)
                stoch_15m_reset = k_15m < 35
                micro_turn = k_1m > d_1m and k_1m > k_1m_prev and k_3m > d_3m
                if near_sma and stoch_15m_reset and micro_turn:
                    score += 30.0
                    reasons.append("BOTA_PULLBACK_REENTRY_LONG(+30)")
            else:
                near_sma = current_price >= (ema_50_15m * 0.97) or current_price >= (sma_200_15m * 0.97)
                stoch_15m_reset = k_15m > 65
                micro_turn = k_1m < d_1m and k_1m < k_1m_prev and k_3m < d_3m
                if near_sma and stoch_15m_reset and micro_turn:
                    score += 30.0
                    reasons.append("BOTA_PULLBACK_REENTRY_SHORT(+30)")
        if 2.8 < pnl_pct < 3.5:
            is_bullish = (is_long and k_1m > d_1m and k_3m > d_3m) or (not is_long and k_1m < d_1m and k_3m < d_3m)
            if is_bullish:
                score += 15.0
                reasons.append("DECENT_GAIN_PUSH_3PCT")
    return score, reasons, dc_expansion_pct, is_massive_expansion

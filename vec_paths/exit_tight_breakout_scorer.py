"""vec_paths/exit_tight_breakout_scorer.py — exit-mode TIGHT_BREAKOUT + HTF_MOM + PROFIT_STALL.

LIVE SOURCE: ez_positions_quick.py:AdvancedSignalRater.rate() lines 3255-3272.
Only fires when is_exit=True. Pure function — no early returns, no state mutation.
"""
from __future__ import annotations
from typing import List, Tuple


def score_exit_tight_breakout(
    is_long: bool,
    avg_entry: float,
    current_price: float,
    pnl_pct: float,
    dc_high_15m: float,
    dc_low_15m: float,
    k_1m: float,
    k_1m_prev: float,
    d_1m: float,
    k_3m: float,
    d_3m: float,
    k_15m: float,
    d_15m: float,
    k_1h: float,
    d_1h: float,
    true_lag: float,
) -> Tuple[float, List[str]]:
    """Mirrors ez_positions_quick.py rate() lines 3255-3272 — verbatim."""
    score = 0.0
    reasons: List[str] = []
    if is_long:
        if avg_entry > (dc_high_15m * 0.985):
            if k_1m < d_1m and k_1m < k_1m_prev and k_3m < 75:
                score -= 20.0
                reasons.append("TIGHT_BREAKOUT_STOP_LONG(-20)")
        if k_15m < d_15m and k_1h < d_1h:
            score -= 5; reasons.append("HTF_MOM_AGAINST(-5)")
        if (k_1m < d_1m and true_lag < 10.0) or k_3m < d_3m:
            if pnl_pct > 0.3 or current_price >= dc_high_15m:
                score -= 3; reasons.append("PROFIT_STALL(-3)")
            elif pnl_pct < -0.5 and k_3m < d_3m:
                score -= 2; reasons.append("LOSS_MOM_CUT(-2)")
    else:
        if avg_entry < (dc_low_15m * 1.015):
            if k_1m > d_1m and k_1m > k_1m_prev and k_3m > 25:
                score -= 20.0
                reasons.append("TIGHT_BREAKOUT_STOP_SHORT(-20)")
        if k_15m > d_15m and k_1h > d_1h:
            score -= 5; reasons.append("HTF_MOM_AGAINST(-5)")
        if (k_1m > d_1m and true_lag < 10.0) or k_3m > d_3m:
            if pnl_pct > 0.3 or current_price <= dc_low_15m:
                score -= 3; reasons.append("PROFIT_STALL(-3)")
            elif pnl_pct < -0.5 and k_3m > d_3m:
                score -= 2; reasons.append("LOSS_MOM_CUT(-2)")
    return score, reasons

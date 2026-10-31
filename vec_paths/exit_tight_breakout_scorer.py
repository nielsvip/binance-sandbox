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


def score_exit_tight_breakout_array(values, is_long):
    """Return the exact per-row score delta and fired reason masks."""
    import numpy as np
    required = ("avg_entry", "current_price", "pnl_pct", "dc_high_15m", "dc_low_15m",
                "k_1m", "k_1m_prev", "d_1m", "k_3m", "d_3m", "k_15m", "d_15m",
                "k_1h", "d_1h", "true_lag")
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError(f"exit tight-breakout missing inputs: {','.join(missing)}")
    arrays = np.broadcast_arrays(*[np.asarray(values[key], dtype=float) for key in required])
    data = {key: np.asarray(array, dtype=float) for key, array in zip(required, arrays)}
    if is_long:
        tight = (data["avg_entry"] > data["dc_high_15m"] * .985) & (data["k_1m"] < data["d_1m"]) & (data["k_1m"] < data["k_1m_prev"]) & (data["k_3m"] < 75.0)
        htf = (data["k_15m"] < data["d_15m"]) & (data["k_1h"] < data["d_1h"])
        momentum = ((data["k_1m"] < data["d_1m"]) & (data["true_lag"] < 10.0)) | (data["k_3m"] < data["d_3m"])
        profit = (data["pnl_pct"] > .3) | (data["current_price"] >= data["dc_high_15m"])
        loss = (data["pnl_pct"] < -.5) & (data["k_3m"] < data["d_3m"])
        names = {"TIGHT_BREAKOUT_STOP_LONG": tight, "HTF_MOM_AGAINST": htf,
                 "PROFIT_STALL": momentum & profit, "LOSS_MOM_CUT": momentum & ~profit & loss}
    else:
        tight = (data["avg_entry"] < data["dc_low_15m"] * 1.015) & (data["k_1m"] > data["d_1m"]) & (data["k_1m"] > data["k_1m_prev"]) & (data["k_3m"] > 25.0)
        htf = (data["k_15m"] > data["d_15m"]) & (data["k_1h"] > data["d_1h"])
        momentum = ((data["k_1m"] > data["d_1m"]) & (data["true_lag"] < 10.0)) | (data["k_3m"] > data["d_3m"])
        profit = (data["pnl_pct"] > .3) | (data["current_price"] <= data["dc_low_15m"])
        loss = (data["pnl_pct"] < -.5) & (data["k_3m"] > data["d_3m"])
        names = {"TIGHT_BREAKOUT_STOP_SHORT": tight, "HTF_MOM_AGAINST": htf,
                 "PROFIT_STALL": momentum & profit, "LOSS_MOM_CUT": momentum & ~profit & loss}
    shape = arrays[0].shape
    delta = np.zeros(shape, dtype=float)
    for name, weight in (("TIGHT_BREAKOUT_STOP_LONG" if is_long else "TIGHT_BREAKOUT_STOP_SHORT", -20.0),
                         ("HTF_MOM_AGAINST", -5.0), ("PROFIT_STALL", -3.0), ("LOSS_MOM_CUT", -2.0)):
        delta -= np.asarray(names[name], dtype=bool) * abs(weight)
    return delta, {name: np.asarray(mask, dtype=bool) for name, mask in names.items()}

"""vec_paths/stoch_kx_scorer.py — Stoch K-cross scoring block.

LIVE SOURCE: ez_positions_quick.py:AdvancedSignalRater.rate() lines 2885-2927
(K-cross scoring section).

This is a pure function. Both live (ez_positions_quick.py) AND vec backtest
(v8_vec_sweep.py + vec_paths/live_only_signals_batch5.py) call this single
implementation — guaranteeing bit-identical scoring for the K-cross signals.

Inputs:
  is_long: bool
  is_exit: bool
  current_price: float
  last_reduction_price: float
  min_since_red: float    # minutes since last reduction
  gain: float              # current position gain %
  true_lag: float          # bars since last k-cross
  # DC channel context
  dc_basis_D, dc_low_D, dc_high_D, dc_wD: float
  dc_basis_4h, dc_low_4h, dc_high_4h, dc_w4h: float
  # Stoch K crossover booleans (set by upstream logic)
  k_1mco, k_1mcu, k_3mco, k_3mcu, k_15mco, k_15mcu: bool
  # Recorded cross prices
  crossover_price_3m, crossover_price_previous_3m: float
  crossunder_price_3m, crossunder_price_previous_3m: float
  crossover_price_15m, crossover_price_previous_15m: float
  crossunder_price_15m, crossunder_price_previous_15m: float

Returns: (delta_score: float, reasons_added: list[str])
"""
from __future__ import annotations
from typing import List, Tuple


def score_stoch_kx(
    is_long: bool,
    is_exit: bool,
    current_price: float,
    last_reduction_price: float,
    min_since_red: float,
    gain: float,
    true_lag: float,
    dc_basis_D: float,
    dc_low_D: float,
    dc_high_D: float,
    dc_wD: float,
    dc_basis_4h: float,
    dc_low_4h: float,
    dc_high_4h: float,
    dc_w4h: float,
    k_1mco: bool,
    k_1mcu: bool,
    k_3mco: bool,
    k_3mcu: bool,
    k_15mco: bool,
    k_15mcu: bool,
    crossover_price_3m: float,
    crossover_price_previous_3m: float,
    crossunder_price_3m: float,
    crossunder_price_previous_3m: float,
    crossover_price_15m: float,
    crossover_price_previous_15m: float,
    crossunder_price_15m: float,
    crossunder_price_previous_15m: float,
) -> Tuple[float, List[str]]:
    """Mirrors ez_positions_quick.py rate() lines 2885-2927 — verbatim semantics."""
    score = 0.0
    reasons: List[str] = []

    if ((is_long and current_price > last_reduction_price) or (not is_long and current_price < last_reduction_price)) and min_since_red < 35:
        score += 8
    if not is_exit and last_reduction_price > 0 and dc_low_D > 0:
        if is_long and dc_wD > 4.0 and current_price > dc_low_D and current_price < dc_basis_D:
            score += 20; reasons.append("DC_REENTRY_PB_D(+20)")
        elif is_long and dc_w4h > 3.0 and dc_low_4h > 0 and current_price > dc_low_4h and current_price < dc_basis_4h:
            score += 15; reasons.append("DC_REENTRY_PB_4H(+15)")
        if not is_long and dc_wD > 4.0 and current_price < dc_high_D and current_price > dc_basis_D:
            score += 20; reasons.append("DC_REENTRY_PB_D(+20)")
        elif not is_long and dc_w4h > 3.0 and dc_high_4h > 0 and current_price < dc_high_4h and current_price > dc_basis_4h:
            score += 15; reasons.append("DC_REENTRY_PB_4H(+15)")
    if (is_long and k_1mco) or (not is_long and k_1mcu) and true_lag < 10.0:
        score += 1
        if (is_long and current_price > crossover_price_3m) or (not is_long and current_price < crossunder_price_3m):
            score += 2
            if (is_long and crossover_price_3m > crossover_price_previous_3m) or (not is_long and crossunder_price_3m < crossunder_price_previous_3m):
                score += 3
        if min_since_red < 25:
            score += 3
    if ((is_long and k_1mcu) or (not is_long and k_1mco)) and true_lag < 10.0:
        score -= 1
        if (is_long and current_price < crossover_price_3m) or (not is_long and current_price > crossunder_price_3m):
            score -= 1
            if (is_long and crossover_price_3m < crossover_price_previous_3m) or (not is_long and crossunder_price_3m > crossunder_price_previous_3m):
                score -= 4
        if gain < 0.1:
            score -= 5
    if (is_long and k_3mco) or (not is_long and k_3mcu):
        score += 4
        if (is_long and current_price > crossover_price_3m) or (not is_long and current_price < crossunder_price_3m):
            score += 2
            if (is_long and crossover_price_3m > crossover_price_previous_3m) or (not is_long and crossunder_price_3m < crossunder_price_previous_3m):
                score += 2
        if gain < 0.1:
            score -= 8
        if min_since_red < 35:
            score += 3
    if (is_long and k_3mcu) or (not is_long and k_3mco):
        score -= 3
        if (is_long and current_price < crossover_price_3m) or (not is_long and current_price > crossunder_price_3m):
            score -= 2
            if (is_long and crossover_price_3m < crossover_price_previous_3m) or (not is_long and crossunder_price_3m > crossunder_price_previous_3m):
                score -= 3
        if gain < 0.1:
            score -= 7
    if (is_long and k_15mco) or (not is_long and k_15mcu):
        score += 7
        if (is_long and current_price > crossover_price_15m) or (not is_long and current_price < crossunder_price_15m):
            score += 5
            if (is_long and crossover_price_15m > crossover_price_previous_15m) or (not is_long and crossunder_price_15m < crossunder_price_previous_15m):
                score += 3
        if gain < 0.1:
            score -= 9
    if (is_long and k_15mcu) or (not is_long and k_15mco):
        score -= 9
        if (is_long and current_price < crossover_price_15m) or (not is_long and current_price > crossunder_price_15m):
            score -= 4
            if (is_long and crossover_price_15m < crossover_price_previous_15m) or (not is_long and crossunder_price_15m > crossunder_price_previous_15m):
                score -= 4
        if gain < 0.1:
            score -= 19

    return score, reasons

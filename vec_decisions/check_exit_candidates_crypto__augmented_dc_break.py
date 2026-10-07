"""check_exit_candidates_crypto__augmented_dc_break — AUGMENTED_DC_BREAK_REDUCE_TO_MIN
exit predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
14261-14274:

    is_augmented = position.positionAmt > 1.2 * pos_min_qty
    dc_broken = (is_long and current_price < dc_low_3m) or (not is_long and current_price > dc_high_3m)
    if not hard_exit_reason and not _in_grace_period and is_augmented and dc_broken
       and current_gain > 0.1 and not already_hedged:
        hard_exit_reason = f"AUGMENTED_DC_BREAK_REDUCE_TO_MIN_{current_gain:.2f}%"

A profit-protect REDUCE for augmented (oversized) positions when the 3m Donchian
20-bar channel breaks against the position, while still in (small) profit.

INDICATOR FIRE (this module): dc_broken — a pure per-bar test on (price, dc_low_3m,
dc_high_3m). The is_augmented (positionAmt vs pos_min_qty), gain>0.1, and
not-already_hedged conditions are STATE seams the engine maintains; the wrappers
accept them so the full decision is reproducible without indicator/state drift.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar +
vec share the pure core _dc_broken_3m() — CANNOT drift.

NPZ / indicator fields read:
  - dc_low_3m / dc_high_3m   — present in NPZ
  - current_price / close
State seams: is_augmented (bool), current_gain (>0.1 gate), already_hedged (bool)

Config: none (live block is unconditional on enable; gain floor 0.1 is hardcoded,
exposed as AUGMENTED_DC_BREAK_MIN_GAIN default 0.1 to preserve parity if absent).
"""
from typing import Tuple
import numpy as np


def _dc_broken_3m(current_price: float, dc_low_3m: float, dc_high_3m: float,
                  is_long: bool) -> bool:
    """PURE per-bar 3m Donchian break against position. Mirrors live 14262 EXACTLY
    (no >0 guard on dc levels — live does not guard here)."""
    if is_long:
        return current_price < dc_low_3m
    return current_price > dc_high_3m


def check_augmented_dc_break(config, indicators: dict, current_price: float, is_long: bool,
                             is_augmented: bool, current_gain: float,
                             already_hedged: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). Combines the pure dc-break core with
    the engine state seams exactly as the live `if` at 14273 does."""
    min_gain = float(getattr(config, "AUGMENTED_DC_BREAK_MIN_GAIN", 0.1))
    dc_low_3m = float(indicators.get("dc_low_3m", 0) or 0)
    dc_high_3m = float(indicators.get("dc_high_3m", 0) or 0)
    if not (is_augmented and _dc_broken_3m(current_price, dc_low_3m, dc_high_3m, is_long)
            and current_gain > min_gain and not already_hedged):
        return False, ""
    return True, f"AUGMENTED_DC_BREAK_REDUCE_TO_MIN_{current_gain:.2f}%"


def check_augmented_dc_break_vec(config, current_price_arr, dc_low_3m_arr, dc_high_3m_arr,
                                 is_long, is_augmented_arr, gain_arr,
                                 already_hedged_arr=None) -> np.ndarray:
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as scalar.
    is_augmented_arr / gain_arr / already_hedged_arr are per-bar state seams.
    already_hedged_arr defaults to all-False (backtest typically has no hedge state)."""
    p = np.asarray(current_price_arr, dtype=float)
    n = len(p)
    min_gain = float(getattr(config, "AUGMENTED_DC_BREAK_MIN_GAIN", 0.1))
    if is_long:
        dc_broken = p < np.asarray(dc_low_3m_arr, dtype=float)
    else:
        dc_broken = p > np.asarray(dc_high_3m_arr, dtype=float)
    aug = np.asarray(is_augmented_arr, dtype=bool)
    g = np.asarray(gain_arr, dtype=float)
    if already_hedged_arr is None:
        hedged = np.zeros(n, dtype=bool)
    else:
        hedged = np.asarray(already_hedged_arr, dtype=bool)
    return aug & dc_broken & (g > min_gain) & ~hedged

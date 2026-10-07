"""check_entry_candidates_crypto__trend_entry_gate.py

SHARED scalar+vectorized predicate for TREND_ENTRY_GATE in
check_entry_candidates_for_account (ez_positions_quick.py:15682-15692).

For TREND_ACCOUNTS, an otherwise-armed entry is BLOCKED unless HTF conviction
(htf_trend_score) is strong enough on the side:

FAITHFUL EXTRACTION of ez_positions_quick.py:15683-15690:

    _te_min_bull = TREND_HTF_MIN_BULL  (default 7)
    _te_min_bear = TREND_HTF_MIN_BEAR  (default 7)
    if is_long and htf_trend_score < _te_min_bull:        should_trade=False (BLOCK)
    elif short and htf_trend_score > -_te_min_bear:       should_trade=False (BLOCK)
    else:                                                 (PASS)

This module returns the PASS predicate (True == entry allowed through the gate).
Pure per-bar test on htf_trend_score — a scalar derived from the HTF indicators
(trading_policy.check_htf_trend) the engine already computes per bar. The
account-membership check (`account_key in TREND_ACCOUNTS`) is a caller flag
(constant for the run), mirroring the pyramid template's is_long. Single shared
core so scalar and vec cannot drift.
"""
from typing import Tuple
import numpy as np


def _trend_entry_gate_passes(htf_trend_score: float, is_long: bool,
                             min_bull: float, min_bear: float) -> bool:
    """PURE per-bar test. True == gate PASSES. Mirrors ez_positions_quick.py:15685-15690."""
    if is_long and htf_trend_score < min_bull:
        return False
    if (not is_long) and htf_trend_score > -min_bear:
        return False
    return True


def _thresholds(config) -> Tuple[float, float]:
    return (float(getattr(config, "TREND_HTF_MIN_BULL", 7)),
            float(getattr(config, "TREND_HTF_MIN_BEAR", 7)))


def check_trend_entry_gate(config, htf_trend_score: float, is_long: bool,
                           is_trend_account: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (passes, reason). When the account is NOT a TREND
    account the gate is inert (always passes), exactly like live (the whole block is
    guarded by `account_key in TREND_ACCOUNTS`)."""
    if not is_trend_account:
        return True, ""
    min_bull, min_bear = _thresholds(config)
    if _trend_entry_gate_passes(float(htf_trend_score), is_long, min_bull, min_bear):
        return True, ""
    side = "LONG" if is_long else "SHORT"
    thr = min_bull if is_long else -min_bear
    return False, f"TREND_ENTRY_BLOCK_{side}_htf={htf_trend_score} thr={thr}"


def check_trend_entry_gate_vec(config, htf_trend_score_arr, is_long,
                               is_trend_account):
    """VECTORIZED per-bar PASS mask. SAME logic as scalar. is_trend_account is a
    scalar bool (constant for the run)."""
    s = np.asarray(htf_trend_score_arr, dtype=float)
    if not is_trend_account:
        return np.ones(s.shape[0], dtype=bool)
    min_bull, min_bear = _thresholds(config)
    if is_long:
        return ~(s < min_bull)
    return ~(s > -min_bear)

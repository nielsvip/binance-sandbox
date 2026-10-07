"""check_exit_candidates_crypto__trend_reversal_exit — TREND_REVERSAL_EXIT
LONG/SHORT exit predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
14094-14102:

    if not hard_exit_reason and not is_hedge and not _winner_protect_skip_trend_reversal
       and account_key in config.TREND_ACCOUNTS:
        _tr_min_gain = config.TREND_MIN_GAIN_EXIT   (default 0.10)
        _tr_flip     = config.TREND_EXIT_SCORE_FLIP (default 0)
        if current_gain >= _tr_min_gain:
            if is_long  and htf_trend_score <= _tr_flip:        -> TREND_REVERSAL_EXIT_LONG
            elif (not is_long) and htf_trend_score >= -_tr_flip: -> TREND_REVERSAL_EXIT_SHORT

A poll-based HTF-trend-flip exit for TREND_ACCOUNTS: when the position is at least
mildly in profit (gain >= TREND_MIN_GAIN_EXIT) AND the composite HTF trend score has
flipped against the position direction, close.

CLASSIFICATION: stateful_seam. The composite `htf_trend_score` is NOT a single NPZ
field — it is produced by `trading_policy.check_htf_trend` at runtime (coverage report
crypto-exit item #13). It therefore enters the core as a per-position STATE/SCORE array
supplied by the engine's state-walk, alongside the `current_gain` state array. Given
those arrays the LONG/SHORT fire decision is a pure per-bar predicate, faithfully
reproducible. The `account_key in TREND_ACCOUNTS` membership + not-hard_exit +
not-is_hedge + winner_protect_skip gates are applied by the caller exactly as live.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar + vec
share the pure core _trend_reversal_fires() — CANNOT drift.

State seam inputs (engine-maintained):
  - htf_trend_score (composite HTF trend score, runtime scorer → engine state array)
  - current_gain (gain%)

Config:
  TREND_MIN_GAIN_EXIT    (default 0.10)
  TREND_EXIT_SCORE_FLIP  (default 0)
"""
from typing import Tuple
import numpy as np


def _trend_reversal_fires(htf_trend_score: float, current_gain: float, is_long: bool,
                          min_gain: float, flip: float) -> bool:
    """PURE per-bar fire test on (htf_trend_score, gain) state. Mirrors live
    14098-14102 EXACTLY."""
    if current_gain < min_gain:
        return False
    if is_long:
        return htf_trend_score <= flip
    return htf_trend_score >= -flip


def _trend_reversal_thresholds(config):
    return (float(getattr(config, "TREND_MIN_GAIN_EXIT", 0.10)),
            float(getattr(config, "TREND_EXIT_SCORE_FLIP", 0)))


def check_trend_reversal_exit(config, htf_trend_score: float, current_gain: float,
                              is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). The caller applies the
    not-hard_exit / not-is_hedge / not-winner_protect_skip / account-in-TREND_ACCOUNTS
    gates exactly as the live `if` at 14095."""
    min_gain, flip = _trend_reversal_thresholds(config)
    if not _trend_reversal_fires(htf_trend_score, current_gain, is_long, min_gain, flip):
        return False, ""
    if is_long:
        return True, f"TREND_REVERSAL_EXIT_LONG_score{htf_trend_score}_gain{current_gain:.2f}%"
    return True, f"TREND_REVERSAL_EXIT_SHORT_score{htf_trend_score}_gain{current_gain:.2f}%"


def check_trend_reversal_exit_vec(config, htf_trend_score_arr, gain_arr, is_long) -> np.ndarray:
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as scalar.
    htf_trend_score_arr / gain_arr are per-bar state seams (the composite HTF trend
    score is a runtime scorer the engine state-walk must supply; gain% is engine
    state). Caller AND-masks with account-in-TREND_ACCOUNTS / not-hard_exit /
    not-is_hedge / not-winner_protect_skip exactly as live."""
    s = np.asarray(htf_trend_score_arr, dtype=float)
    g = np.asarray(gain_arr, dtype=float)
    min_gain, flip = _trend_reversal_thresholds(config)
    in_gain = g >= min_gain
    if is_long:
        return in_gain & (s <= flip)
    return in_gain & (s >= -flip)

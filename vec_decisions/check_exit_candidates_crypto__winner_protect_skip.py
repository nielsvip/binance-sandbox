"""check_exit_candidates_crypto__winner_protect_skip — WINNER_PROTECT skip-flag
predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
14084-14093:

    _winner_protect_skip_trend_reversal = False
    if config.WINNER_PROTECT_ENABLED and not is_hedge:
        _wp_rp  = ranking_points (0ranking_points_global, fallback 0ranking_points)
        _wp_thr = config.RP_PROTECT_THRESHOLD  (default 70.0)
        _wp_min_gain = config.RP_PROTECT_MIN_GAIN (default 2.0 in getattr; config field 1.0)
        _wp_match = (is_long and rp >= thr) or (not is_long and rp <= -thr)
        if _wp_match and 0 <= current_gain < _wp_min_gain:
            _winner_protect_skip_trend_reversal = True

This is NOT an exit — it is a pure per-bar boolean that, when True, SKIPS the
TREND_REVERSAL_EXIT poll gate for high-ranked same-direction winners that have not yet
locked RP_PROTECT_MIN_GAIN. It feeds check_trend_reversal_exit's caller AND-mask.

CLASSIFICATION: manual → core (coverage report crypto-exit manual list "WINNER_PROTECT
skip"). It was manual because `ranking_points` is not a standard NPZ field; live reads
`0ranking_points_global` (runtime ranking pipeline). Here the ranking-points value
enters the core as a per-position STATE/SCORE input — given (rp, gain) the skip flag is
a pure predicate, faithfully reproducible. The not-is_hedge gate is applied by caller.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar + vec
share the pure core _winner_protect_skip() — CANNOT drift.

State seam inputs (engine-maintained / runtime ranking pipeline):
  - ranking_points (0ranking_points_global)
  - current_gain (gain%)

Config:
  WINNER_PROTECT_ENABLED  (live getattr default True; config field default False)
  RP_PROTECT_THRESHOLD    (default 70.0)
  RP_PROTECT_MIN_GAIN     (live getattr default 2.0)
"""
import numpy as np


def _winner_protect_skip(ranking_points: float, current_gain: float, is_long: bool,
                         thr: float, min_gain: float) -> bool:
    """PURE per-bar skip-flag test on (ranking_points, gain) state. Mirrors live
    14090-14092 EXACTLY."""
    match = (is_long and ranking_points >= thr) or ((not is_long) and ranking_points <= -thr)
    return bool(match and (0 <= current_gain < min_gain))


def _winner_protect_thresholds(config):
    return (float(getattr(config, "RP_PROTECT_THRESHOLD", 70.0)),
            float(getattr(config, "RP_PROTECT_MIN_GAIN", 2.0)))


def check_winner_protect_skip(config, ranking_points: float, current_gain: float,
                              is_long: bool) -> bool:
    """LIVE/scalar path. Returns the skip flag (True => skip TREND_REVERSAL_EXIT).
    not-is_hedge gate applied by caller exactly as live 14085."""
    if not bool(getattr(config, "WINNER_PROTECT_ENABLED", True)):
        return False
    thr, min_gain = _winner_protect_thresholds(config)
    return _winner_protect_skip(ranking_points, current_gain, is_long, thr, min_gain)


def check_winner_protect_skip_vec(config, ranking_points_arr, gain_arr, is_long) -> np.ndarray:
    """VECTORIZED per-bar skip mask — backtest path. SAME predicate as scalar.
    ranking_points_arr / gain_arr are per-bar state seams (runtime ranking pipeline +
    engine gain%)."""
    rp = np.asarray(ranking_points_arr, dtype=float)
    n = len(rp)
    if not bool(getattr(config, "WINNER_PROTECT_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    g = np.asarray(gain_arr, dtype=float)
    thr, min_gain = _winner_protect_thresholds(config)
    if is_long:
        match = rp >= thr
    else:
        match = rp <= -thr
    return match & (g >= 0) & (g < min_gain)

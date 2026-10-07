"""IBS_EXHAUSTION — Internal Bar Strength exhaustion exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6916-6933.
  Guard: gain > 1.0 (state).
  5m bar (prev completed): bar_range_5m = high_5m_prev - low_5m_prev ; needs >0 and close_5m_prev>0.
    ibs_5m = (close_5m_prev - low_5m_prev) / bar_range_5m
    LONG  fires when ibs_5m > 0.9 ; SHORT fires when ibs_5m < 0.1.
  15m bar (additional, only when gain > 1.5):
    ibs_15m = (close_15m_prev - low_15m_prev) / (high_15m_prev - low_15m_prev)
    LONG fires when ibs_15m > 0.9 ; SHORT fires when ibs_15m < 0.1.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_ibs_exhaustion) AND vec (check_ibs_exhaustion_vec) share the SAME pure per-bar
predicate _ibs_exhaustion_fires(), so the two paths CANNOT drift. Pure core: no config,
no state.

STATE SEAM: the gain>1.0 (5m) / gain>1.5 (15m) thresholds are per-position state (live
position.gain). They are applied by the wrapper/caller. The pure core covers the IBS
computation + the >0.9/<0.1 thresholds on whichever bar is supplied. The wrapper handles
the 5m-first-then-15m ordering exactly as live (5m checked first, returns 5m reason; only
if 5m did not fire is the 15m bar checked, and only when gain>1.5).

This is a profit-taking EXIT (gain>1.0 required) — never closes a loser.

NPZ / indicator fields read by the core:
  high_5m_prev, low_5m_prev, close_5m_prev   (prev completed 5m bar)
  high_15m_prev, low_15m_prev, close_15m_prev (prev completed 15m bar)
"""
from typing import Optional, Tuple


def _ibs_exhaustion_fires(high_prev: float, low_prev: float, close_prev: float,
                          is_long: bool) -> Optional[bool]:
    """PURE per-bar IBS exhaustion condition for a single (prev) bar. Faithful replica of
    tradier_manage.py lines 6918-6924 (the bar_range>0 & close>0 guard + ibs threshold).
    Returns True/False if the bar is valid; returns None when the bar is invalid
    (range<=0 or close<=0) so the caller knows nothing fired on this bar."""
    bar_range = high_prev - low_prev
    if not (bar_range > 0 and close_prev > 0):
        return None
    ibs = (close_prev - low_prev) / bar_range
    if is_long:
        return ibs > 0.9
    return ibs < 0.1


def check_ibs_exhaustion(config, indicators: dict, gain_pct: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). IBS computation from the shared
    predicate; the gain>1.0 / gain>1.5 state gates + 5m-then-15m ordering applied here,
    faithful to tradier_manage.py lines 6916-6933."""
    if not (gain_pct > 1.0):
        return False, ""
    h5p = float(indicators.get("high_5m_prev", 0) or 0)
    l5p = float(indicators.get("low_5m_prev", 0) or 0)
    c5p = float(indicators.get("close_5m_prev", 0) or 0)
    fired5 = _ibs_exhaustion_fires(h5p, l5p, c5p, is_long)
    if fired5:
        ibs_5m = (c5p - l5p) / (h5p - l5p)
        side = "LONG" if is_long else "SHORT"
        return True, f"IBS_EXHAUSTION_{side}_ibs:{ibs_5m:.2f}_gain:{gain_pct:.1f}%"
    if gain_pct > 1.5:
        h15p = float(indicators.get("high_15m_prev", 0) or 0)
        l15p = float(indicators.get("low_15m_prev", 0) or 0)
        c15p = float(indicators.get("close_15m_prev", 0) or 0)
        fired15 = _ibs_exhaustion_fires(h15p, l15p, c15p, is_long)
        if fired15:
            ibs_15m = (c15p - l15p) / (h15p - l15p)
            side = "LONG" if is_long else "SHORT"
            return True, f"IBS_15m_EXHAUSTION_{side}_ibs:{ibs_15m:.2f}_gain:{gain_pct:.1f}%"
    return False, ""


def check_ibs_exhaustion_vec(config, high_prev_arr, low_prev_arr, close_prev_arr, is_long):
    """VECTORIZED per-bar IBS exhaustion fire mask for ONE bar series (5m OR 15m) — backtest
    path. SAME predicate as the live scalar core. Returns a bool ndarray. The caller layers
    the gain>1.0 (5m) / gain>1.5 (15m) state gate and the 5m-before-15m precedence on top,
    exactly as the live wrapper does.
    Arrays per-bar (NPZ): high_<tf>_prev, low_<tf>_prev, close_<tf>_prev."""
    import numpy as np
    h = np.asarray(high_prev_arr, dtype=float)
    lo = np.asarray(low_prev_arr, dtype=float)
    c = np.asarray(close_prev_arr, dtype=float)
    rng = h - lo
    valid = (rng > 0) & (c > 0)
    ibs = np.where(valid, (c - lo) / np.where(rng == 0, 1.0, rng), np.nan)
    if is_long:
        return valid & (ibs > 0.9)
    return valid & (ibs < 0.1)

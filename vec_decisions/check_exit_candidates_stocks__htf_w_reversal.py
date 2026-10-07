"""HTF_W_REVERSAL_EXIT — Weekly WaveTrend reversal exit (+ optional Daily confirm) (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6035-6053.
  Gated by HTF_W_REVERSAL_EXIT_TRADIER_ENABLED (default False) AND gain > 0 (state).
  _w_has = (wt1_W != 0 OR wt2_W != 0) ; _d_has = (wt1_D != 0 OR wt2_D != 0)
  Only evaluated when _w_has is True.
  LONG : _w_against = wt1_W < wt2_W ; _d_against = (wt1_D < wt2_D) AND _d_has
  SHORT: _w_against = wt1_W > wt2_W ; _d_against = (wt1_D > wt2_D) AND _d_has
  need_d = HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D (default True)
  fires when _w_against AND (_d_against OR not need_d).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_htf_w_reversal) AND vec (check_htf_w_reversal_vec) share the SAME pure per-bar
predicate _htf_w_reversal_fires(), so the two paths CANNOT drift. Pure core: no config.

STATE SEAM: gain > 0 is per-position state — applied by the wrapper/caller (faithful to
live line 6035). The pure core is the indicator-only W (+optional D) reversal test.

NPZ / indicator fields read by the core:
  wt1_W, wt2_W, wt1_D, wt2_D.
"""
from typing import Tuple


def _htf_w_reversal_fires(wt1_W: float, wt2_W: float, wt1_D: float, wt2_D: float,
                          need_d: bool, is_long: bool) -> bool:
    """PURE per-bar condition. Faithful replica of tradier_manage.py lines 6041-6051."""
    w_has = (wt1_W != 0 or wt2_W != 0)
    if not w_has:
        return False
    d_has = (wt1_D != 0 or wt2_D != 0)
    if is_long:
        w_against = wt1_W < wt2_W
        d_against = (wt1_D < wt2_D) and d_has
    else:
        w_against = wt1_W > wt2_W
        d_against = (wt1_D > wt2_D) and d_has
    return w_against and (d_against or not need_d)


def check_htf_w_reversal(config, indicators: dict, gain_pct: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Indicator-only fire from the shared
    predicate; ENABLED flag + gain>0 state gate applied here (faithful to line 6035)."""
    if not bool(getattr(config, "HTF_W_REVERSAL_EXIT_TRADIER_ENABLED", False)):
        return False, ""
    if not (gain_pct > 0):
        return False, ""
    need_d = bool(getattr(config, "HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D", True))
    wt1_W = float(indicators.get("wt1_W", 0) or 0)
    wt2_W = float(indicators.get("wt2_W", 0) or 0)
    wt1_D = float(indicators.get("wt1_D", 0) or 0)
    wt2_D = float(indicators.get("wt2_D", 0) or 0)
    if not _htf_w_reversal_fires(wt1_W, wt2_W, wt1_D, wt2_D, need_d, is_long):
        return False, ""
    return True, f"HTF_W_REVERSAL_EXIT(W={wt1_W:.1f}/{wt2_W:.1f}_D={wt1_D:.1f}/{wt2_D:.1f}_g={gain_pct:.2f}%)"


def check_htf_w_reversal_vec(config, wt1_W_arr, wt2_W_arr, wt1_D_arr, wt2_D_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as the live scalar.
    Returns a bool ndarray; caller layers gain>0 state gate. Arrays per-bar (NPZ): wt1_W,
    wt2_W, wt1_D, wt2_D."""
    import numpy as np
    w1w = np.asarray(wt1_W_arr, dtype=float)
    if not bool(getattr(config, "HTF_W_REVERSAL_EXIT_TRADIER_ENABLED", False)):
        return np.zeros(len(w1w), dtype=bool)
    need_d = bool(getattr(config, "HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D", True))
    w2w = np.asarray(wt2_W_arr, dtype=float)
    w1d = np.asarray(wt1_D_arr, dtype=float)
    w2d = np.asarray(wt2_D_arr, dtype=float)
    w_has = (w1w != 0) | (w2w != 0)
    d_has = (w1d != 0) | (w2d != 0)
    if is_long:
        w_against = w1w < w2w
        d_against = (w1d < w2d) & d_has
    else:
        w_against = w1w > w2w
        d_against = (w1d > w2d) & d_has
    m = w_against & (d_against | (not need_d))
    return w_has & m

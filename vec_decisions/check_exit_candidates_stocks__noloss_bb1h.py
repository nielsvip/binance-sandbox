"""NOLOSS_BB1H_GATE — stocks structural-break loss-exit predicate (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6178-6188.
  Fires (CLOSE at a loss, overrides NO_LOSS) when gain < 0 AND price breaks OUTSIDE
  the 1h Bollinger Band in the wrong direction:
    LONG  : bb_upper_1h > 0 AND bb_lower_1h > 0 AND current_price > 0 AND price < bb_lower_1h
    SHORT : bb_upper_1h > 0 AND bb_lower_1h > 0 AND current_price > 0 AND price > bb_upper_1h
  Gated by NOLOSS_BB1H_GATE_ENABLED (default True) and gain < 0 (state).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): the live
scalar (check_noloss_bb1h) AND the vectorized backtest (check_noloss_bb1h_vec) BOTH
derive their fire decision from the SAME pure per-bar predicate _noloss_bb1h_fires()
below, so the two paths CANNOT drift. Pure core: no config, no state. Data source
differs (live indicators vs NPZ arrays) but the predicate is identical.

STATE SEAM: the gain < 0 condition is per-position state (live position.gain). It is
applied by the wrapper/caller (faithful to live line 6178). The pure core covers ONLY
the indicator-only structural-break portion. This matches the existing vec_decisions
convention (state gates layered by caller, e.g. wt_4h_vel_exit profit/age).

NPZ / indicator fields read by the core:
  - bb_upper_1h, bb_lower_1h  (1h Bollinger Band edges)
  - close (live: current_price)
"""
from typing import Tuple


def _noloss_bb1h_fires(current_price: float, bb_upper_1h: float, bb_lower_1h: float,
                       is_long: bool) -> bool:
    """PURE per-bar indicator-only structural-break condition. Faithful replica of
    tradier_manage.py lines 6182-6188 (the price-vs-band test, after the >0 guards)."""
    if not (bb_upper_1h > 0 and bb_lower_1h > 0 and current_price > 0):
        return False
    if is_long:
        return current_price < bb_lower_1h
    return current_price > bb_upper_1h


def check_noloss_bb1h(config, indicators: dict, gain_pct: float, current_price: float,
                      is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason).
    Indicator-only structural break comes from the shared _noloss_bb1h_fires() predicate;
    the gain < 0 state gate + ENABLED flag are applied here, faithful to line 6178."""
    if not bool(getattr(config, "NOLOSS_BB1H_GATE_ENABLED", True)):
        return False, ""
    if not (gain_pct < 0):
        return False, ""
    bb_upper_1h = float(indicators.get("bb_upper_1h", 0) or 0)
    bb_lower_1h = float(indicators.get("bb_lower_1h", 0) or 0)
    if not _noloss_bb1h_fires(current_price, bb_upper_1h, bb_lower_1h, is_long):
        return False, ""
    if is_long:
        return True, f"NOLOSS_BB1H_BREAKDOWN_LONG_px{current_price:.4f}<bb_low{bb_lower_1h:.4f}_g{gain_pct:.2f}%"
    return True, f"NOLOSS_BB1H_BREAKDOWN_SHORT_px{current_price:.4f}>bb_up{bb_upper_1h:.4f}_g{gain_pct:.2f}%"


def check_noloss_bb1h_vec(config, current_price_arr, bb_upper_1h_arr, bb_lower_1h_arr,
                          is_long):
    """VECTORIZED per-bar indicator-only fire mask — backtest path. SAME predicate as the
    live scalar (vectorized via numpy). Returns a bool ndarray; the caller layers the
    gain < 0 state gate on top, faithful to live line 6178.
    Arrays are per-bar (NPZ): close, bb_upper_1h, bb_lower_1h."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    if not bool(getattr(config, "NOLOSS_BB1H_GATE_ENABLED", True)):
        return np.zeros(len(p), dtype=bool)
    bu = np.asarray(bb_upper_1h_arr, dtype=float)
    bl = np.asarray(bb_lower_1h_arr, dtype=float)
    guard = (bu > 0) & (bl > 0) & (p > 0)
    if is_long:
        return guard & (p < bl)
    return guard & (p > bu)

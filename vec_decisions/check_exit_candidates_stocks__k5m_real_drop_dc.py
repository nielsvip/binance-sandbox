"""K5M_REAL_DROP_DC / K5M_REAL_RISE_DC — 5m Donchian break + stoch-K extreme exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6894-6897.
  LONG  : dc_low_5m > 0 AND current_price < dc_low_5m AND stoch_k_5m < 30  (then gain >= noloss state)
  SHORT : dc_high_5m > 0 AND current_price > dc_high_5m AND stoch_k_5m > 70 (then gain >= noloss state)
  where noloss = getattr(config, 'NOLOSS_MIN_PROFIT_PCT_TRADIER', 3.0).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_k5m_real_drop_dc) AND vec (check_k5m_real_drop_dc_vec) share the SAME pure per-bar
predicate _k5m_real_drop_dc_fires(), so the two paths CANNOT drift. Pure core: no config,
no state.

STATE SEAM: gain >= noloss is per-position state — applied by the wrapper/caller (faithful
to live). The pure core is the indicator-only break-+-K-extreme test.

NPZ / indicator fields read by the core:
  dc_low_5m, dc_high_5m, stoch_k_5m, close (live: current_price).
"""
from typing import Tuple


def _k5m_real_drop_dc_fires(current_price: float, dc_low_5m: float, dc_high_5m: float,
                            stoch_k_5m: float, is_long: bool) -> bool:
    """PURE per-bar indicator-only condition. Faithful replica of tradier_manage.py
    lines 6894 (LONG) / 6896 (SHORT) minus the gain state gate."""
    if is_long:
        return dc_low_5m > 0 and current_price < dc_low_5m and stoch_k_5m < 30
    return dc_high_5m > 0 and current_price > dc_high_5m and stoch_k_5m > 70


def _k5m_min_gain(config) -> float:
    return float(getattr(config, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 3.0))


def check_k5m_real_drop_dc(config, indicators: dict, gain_pct: float, current_price: float,
                           is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Indicator-only fire from the shared
    predicate; gain >= noloss state gate applied here. Live .get defaults: dc=0, stoch_k=50."""
    dc_low_5m = float(indicators.get("dc_low_5m", 0) or 0)
    dc_high_5m = float(indicators.get("dc_high_5m", 0) or 0)
    stoch_k_5m = float(indicators.get("stoch_k_5m", 50) or 50)
    if not _k5m_real_drop_dc_fires(current_price, dc_low_5m, dc_high_5m, stoch_k_5m, is_long):
        return False, ""
    if gain_pct < _k5m_min_gain(config):
        return False, ""
    if is_long:
        return True, f"K5M_REAL_DROP_DC_k5:{stoch_k_5m:.1f}<dc5:{dc_low_5m:.2f}_gain:{gain_pct:.2f}%"
    return True, f"K5M_REAL_RISE_DC_k5:{stoch_k_5m:.1f}>dc5:{dc_high_5m:.2f}_gain:{gain_pct:.2f}%"


def check_k5m_real_drop_dc_vec(config, current_price_arr, dc_low_5m_arr, dc_high_5m_arr,
                               stoch_k_5m_arr, is_long):
    """VECTORIZED per-bar indicator-only fire mask — backtest path. SAME predicate as the
    live scalar. Returns a bool ndarray; caller layers gain >= noloss state gate.
    Arrays per-bar (NPZ): close, dc_low_5m, dc_high_5m, stoch_k_5m."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    dl = np.asarray(dc_low_5m_arr, dtype=float)
    dh = np.asarray(dc_high_5m_arr, dtype=float)
    k = np.asarray(stoch_k_5m_arr, dtype=float)
    if is_long:
        return (dl > 0) & (p < dl) & (k < 30)
    return (dh > 0) & (p > dh) & (k > 70)

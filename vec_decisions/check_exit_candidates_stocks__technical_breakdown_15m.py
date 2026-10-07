"""Extreme_Overbought/Oversold_TP + Technical_Breakdown_15m — standard technical exits (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6960-6973.
  LONG:
    Extreme_Overbought_TP : gain > 1.5 AND current_price > dc_high_1h AND rsi_15m > 80
    Technical_Breakdown_15m: gain >= noloss AND rsi_15m < 40 AND stoch_k_15m < stoch_d_15m AND ha_15m == "red"
  SHORT:
    Extreme_Oversold_TP   : gain > 1.5 AND current_price < dc_low_1h AND rsi_15m < 20
    Technical_Breakdown_15m: gain >= noloss AND rsi_15m > 60 AND stoch_k_15m > stoch_d_15m AND ha_15m == "green"
  where noloss = getattr(config, 'NOLOSS_MIN_PROFIT_PCT_TRADIER', 3.0).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
AND vec share the SAME pure per-bar predicates below, so the two paths CANNOT drift.
Two distinct decisions are extracted (each its own pure core + scalar + vec):
  _extreme_tp_fires           -> Extreme_Overbought_TP / Extreme_Oversold_TP
  _technical_breakdown_fires  -> Technical_Breakdown_15m

STATE SEAM: gain>1.5 (extreme TP) and gain>=noloss (breakdown) are per-position state —
applied by the wrappers/callers. ha_15m is a string categorical encoded to an int code
for the vec path (red=1, green=2, other=0), passed as a boolean to the shared core.

NPZ / indicator fields read:
  close (current_price), dc_high_1h, dc_low_1h, rsi_15m, stoch_k_15m, stoch_d_15m,
  ha_15m (categorical).
"""
from typing import Tuple


def _extreme_tp_fires(current_price: float, dc_high_1h: float, dc_low_1h: float,
                      rsi_15m: float, is_long: bool) -> bool:
    """PURE per-bar condition for Extreme_Overbought_TP (LONG) / Extreme_Oversold_TP (SHORT).
    Faithful replica of lines 6963 / 6970 minus the gain>1.5 state gate."""
    if is_long:
        return current_price > dc_high_1h and rsi_15m > 80
    return current_price < dc_low_1h and rsi_15m < 20


def _technical_breakdown_fires(rsi_15m: float, stoch_k_15m: float, stoch_d_15m: float,
                               ha_red: bool, ha_green: bool, is_long: bool) -> bool:
    """PURE per-bar condition for Technical_Breakdown_15m. Faithful replica of lines
    6965 / 6972 minus the gain>=noloss state gate. ha_red/ha_green are pre-decoded booleans."""
    if is_long:
        return rsi_15m < 40 and stoch_k_15m < stoch_d_15m and ha_red
    return rsi_15m > 60 and stoch_k_15m > stoch_d_15m and ha_green


def _tb_min_gain(config) -> float:
    return float(getattr(config, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 3.0))


def check_extreme_tp(config, indicators: dict, gain_pct: float, current_price: float,
                     is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar Extreme_Overbought/Oversold_TP. Indicator-only fire from the shared
    predicate; gain>1.5 state gate applied here. Live .get defaults: dc_high_1h=999999,
    dc_low_1h=0, rsi_15m=50."""
    if not (gain_pct > 1.5):
        return False, ""
    dc_high_1h = float(indicators.get("dc_high_1h", 999999) or 999999)
    dc_low_1h = float(indicators.get("dc_low_1h", 0) or 0)
    rsi_15m = float(indicators.get("rsi_15m", 50) or 50)
    if not _extreme_tp_fires(current_price, dc_high_1h, dc_low_1h, rsi_15m, is_long):
        return False, ""
    return True, f"Extreme_{'Overbought' if is_long else 'Oversold'}_TP_gain:{gain_pct:.2f}%"


def check_technical_breakdown_15m(config, indicators: dict, gain_pct: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar Technical_Breakdown_15m. Indicator-only fire from the shared predicate;
    gain>=noloss state gate applied here. Live .get defaults: rsi_15m=50, stoch=50, ha=neutral."""
    if not (gain_pct >= _tb_min_gain(config)):
        return False, ""
    rsi_15m = float(indicators.get("rsi_15m", 50) or 50)
    k15 = float(indicators.get("stoch_k_15m", 50) or 50)
    d15 = float(indicators.get("stoch_d_15m", 50) or 50)
    ha_15m = str(indicators.get("ha_15m", "neutral"))
    if not _technical_breakdown_fires(rsi_15m, k15, d15, ha_15m == "red", ha_15m == "green", is_long):
        return False, ""
    return True, f"Technical_Breakdown_15m_gain:{gain_pct:.2f}%"


def check_extreme_tp_vec(config, current_price_arr, dc_high_1h_arr, dc_low_1h_arr, rsi_15m_arr,
                         is_long):
    """VECTORIZED Extreme_TP fire mask — backtest path. SAME predicate. Returns bool ndarray;
    caller layers gain>1.5 state gate. Arrays per-bar (NPZ): close, dc_high_1h, dc_low_1h, rsi_15m."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    dh = np.asarray(dc_high_1h_arr, dtype=float)
    dl = np.asarray(dc_low_1h_arr, dtype=float)
    r = np.asarray(rsi_15m_arr, dtype=float)
    if is_long:
        return (p > dh) & (r > 80)
    return (p < dl) & (r < 20)


def check_technical_breakdown_15m_vec(config, rsi_15m_arr, stoch_k_15m_arr, stoch_d_15m_arr,
                                      ha_15m_code_arr, is_long):
    """VECTORIZED Technical_Breakdown_15m fire mask — backtest path. SAME predicate. Returns
    bool ndarray; caller layers gain>=noloss state gate. ha_15m passed as int code
    (red=1, green=2, other=0). Arrays per-bar (NPZ): rsi_15m, stoch_k_15m, stoch_d_15m, ha_15m code."""
    import numpy as np
    r = np.asarray(rsi_15m_arr, dtype=float)
    k = np.asarray(stoch_k_15m_arr, dtype=float)
    d = np.asarray(stoch_d_15m_arr, dtype=float)
    ha = np.asarray(ha_15m_code_arr, dtype=int)
    if is_long:
        return (r < 40) & (k < d) & (ha == 1)
    return (r > 60) & (k > d) & (ha == 2)

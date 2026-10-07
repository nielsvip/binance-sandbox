"""STRUCT_BREAK_DC_1h — Structure_Break_1H_Basis exit predicate (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop()
  LONG  (line ~6961): gain >= _noloss_min_t AND current_price < dc_basis_1h AND lr_trend_15m < 0
  SHORT (line ~6969): gain >= _noloss_min_t AND current_price > dc_basis_1h AND lr_trend_15m > 0
where _noloss_min_t = getattr(config, 'NOLOSS_MIN_PROFIT_PCT_TRADIER', 3.0).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): the live
scalar (check_struct_break_dc_1h) AND the vectorized backtest
(check_struct_break_dc_1h_vec) BOTH derive their fire decision from the same pure
per-bar predicate _struct_break_dc_1h_fires() below, so the two paths CANNOT drift.
Pure core: no config, no state. Data source differs (live market_data indicators
vs NPZ arrays) but the predicate is identical.

This is a profit-taking EXIT (closes a winner on a 1H-basis structure break with
15m linear-regression trend turning against the position). It is NOT a loss-exit:
gain >= min_gain is required, so it never closes a losing position.

NPZ / indicator fields read:
  - close          (live: current_price)
  - dc_basis_1h    (Donchian 1h basis/midline)
  - lr_trend_15m   (15m linear-regression trend sign)
  All three confirmed present in backtest NPZ (verified on S1 indicators/*.npz).
"""
from typing import Tuple


def _struct_break_dc_1h_fires(gain_pct: float, current_price: float, dc_basis_1h: float,
                              lr_trend_15m: float, is_long: bool, min_gain: float) -> bool:
    """PURE per-bar fire condition for Structure_Break_1H_Basis exit.
    Faithful replica of tradier_manage.py evaluate_stop lines 6961 (LONG) / 6969 (SHORT)."""
    if gain_pct < min_gain:
        return False
    if is_long:
        return current_price < dc_basis_1h and lr_trend_15m < 0
    return current_price > dc_basis_1h and lr_trend_15m > 0


def _struct_break_dc_1h_min_gain(config) -> float:
    return float(getattr(config, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 3.0))


def check_struct_break_dc_1h(config, indicators: dict, gain_pct: float, current_price: float,
                             is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason).
    Fire decision comes from the shared _struct_break_dc_1h_fires() predicate.

    Live default values mirror the .get(...) defaults in tradier_manage.py:
      dc_basis_1h default: LONG=0 (price < 0 never True), SHORT=999999 (price > big never True)
      lr_trend_15m default: 0
    """
    min_gain = _struct_break_dc_1h_min_gain(config)
    _dc_default = 0.0 if is_long else 999999.0
    dc_basis_1h = float(indicators.get("dc_basis_1h", _dc_default))
    lr_trend_15m = float(indicators.get("lr_trend_15m", 0) or 0)
    if not _struct_break_dc_1h_fires(gain_pct, current_price, dc_basis_1h, lr_trend_15m, is_long, min_gain):
        return False, ""
    return True, f"Structure_Break_1H_Basis_gain:{gain_pct:.2f}%"


def check_struct_break_dc_1h_vec(config, gain_arr, current_price_arr, dc_basis_1h_arr,
                                 lr_trend_15m_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME threshold + SAME predicate as
    the live scalar check_struct_break_dc_1h (vectorized via numpy). Returns a bool ndarray.
    Arrays are per-bar (NPZ in backtest): gain%, current_price(=close), dc_basis_1h, lr_trend_15m."""
    import numpy as np
    g = np.asarray(gain_arr, dtype=float)
    min_gain = _struct_break_dc_1h_min_gain(config)
    p = np.asarray(current_price_arr, dtype=float)
    b = np.asarray(dc_basis_1h_arr, dtype=float)
    t = np.asarray(lr_trend_15m_arr, dtype=float)
    m = g >= min_gain
    if is_long:
        m &= (p < b) & (t < 0)
    else:
        m &= (p > b) & (t > 0)
    return m

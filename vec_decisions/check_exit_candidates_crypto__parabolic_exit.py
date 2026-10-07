"""check_exit_candidates_crypto__parabolic_exit — PARABOLIC_EXIT exit predicate
(shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
13727-13750 (PARABOLIC EXHAUSTION EXIT, USER RULE 2026-04-10):

  GATE (caller state): not hard_exit_reason, not is_hedge, not _in_grace_period
  _pe_no_decel = True  (default; overridden by delta_tracker accel — see CAVEAT)
  LONG  fires when: k_15m > 90 AND dc_high_3m > 0 AND price > dc_high_3m AND _pe_no_decel
                    AND low_3m > 0 AND low_3m_prev > 0 AND low_3m < low_3m_prev
  SHORT fires when: k_15m < 10 AND dc_low_3m > 0 AND price < dc_low_3m AND _pe_no_decel
                    AND high_3m > 0 AND high_3m_prev > 0 AND high_3m > high_3m_prev

Catches parabolic tops/bottoms: LTF (15m) extreme + DC breakout + 3m structure crack
(lower-low for LONG / higher-high for SHORT).

CAVEAT — _pe_no_decel SEAM: live multiplies the fire by a DeltaTracker acceleration
flag (_pe_sig.bull_accel for LONG / bear_accel for SHORT). When no delta_tracker is
present, live DEFAULTS _pe_no_decel = True (line 13735). The DeltaTracker accel is a
runtime/stateful feed NOT in NPZ, so this module extracts the pure-indicator portion
and accepts _pe_no_decel as an explicit boolean seam (default True = the live no-tracker
behavior, faithful). The indicator-only core is exact.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar +
vec share the pure core _parabolic_exit_fires() — CANNOT drift.

NPZ / indicator fields read:
  - stoch_k_15m
  - dc_high_3m / dc_low_3m
  - low_3m / low_3m_prev (LONG), high_3m / high_3m_prev (SHORT)
  - current_price / close
All present in NPZ.

Config:
  PARABOLIC_K15M_HIGH  (default 90.0)  — live HARDCODED 90
  PARABOLIC_K15M_LOW   (default 10.0)  — live HARDCODED 10
"""
from typing import Tuple
import numpy as np


def _parabolic_exit_fires(k_15m: float, dc_high_3m: float, dc_low_3m: float,
                          current_price: float, low_3m: float, low_3m_prev: float,
                          high_3m: float, high_3m_prev: float, is_long: bool,
                          no_decel: bool, k_high: float, k_low: float) -> bool:
    """PURE per-bar fire test. Mirrors live 13743-13750 EXACTLY (no_decel seam)."""
    if is_long:
        if not (k_15m > k_high and dc_high_3m > 0 and current_price > dc_high_3m and no_decel):
            return False
        return low_3m > 0 and low_3m_prev > 0 and low_3m < low_3m_prev
    if not (k_15m < k_low and dc_low_3m > 0 and current_price < dc_low_3m and no_decel):
        return False
    return high_3m > 0 and high_3m_prev > 0 and high_3m > high_3m_prev


def _parabolic_thresholds(config):
    return (float(getattr(config, "PARABOLIC_K15M_HIGH", 90.0)),
            float(getattr(config, "PARABOLIC_K15M_LOW", 10.0)))


def check_parabolic_exit(config, indicators: dict, current_price: float, is_long: bool,
                         no_decel: bool = True) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). no_decel defaults True (live no-tracker
    behavior); pass the DeltaTracker bull/bear accel when a tracker is available."""
    k_high, k_low = _parabolic_thresholds(config)
    g = lambda k: float(indicators.get(k, 0) or 0)
    k_15m = float(indicators.get("stoch_k_15m", 50) or 50)
    if not _parabolic_exit_fires(k_15m, g("dc_high_3m"), g("dc_low_3m"), current_price,
                                 g("low_3m"), g("low_3m_prev"), g("high_3m"), g("high_3m_prev"),
                                 is_long, no_decel, k_high, k_low):
        return False, ""
    if is_long:
        return True, f"PARABOLIC_EXIT_LONG_k15={k_15m:.0f}_px>{g('dc_high_3m'):.6f}"
    return True, f"PARABOLIC_EXIT_SHORT_k15={k_15m:.0f}_px<{g('dc_low_3m'):.6f}"


def check_parabolic_exit_vec(config, k_15m_arr, dc_high_3m_arr, dc_low_3m_arr,
                             current_price_arr, low_3m_arr, low_3m_prev_arr,
                             high_3m_arr, high_3m_prev_arr, is_long,
                             no_decel_arr=None) -> np.ndarray:
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as scalar.
    no_decel_arr defaults to all-True (live no-DeltaTracker behavior)."""
    p = np.asarray(current_price_arr, dtype=float)
    n = len(p)
    k_high, k_low = _parabolic_thresholds(config)
    k15 = np.asarray(k_15m_arr, dtype=float)
    if no_decel_arr is None:
        no_decel = np.ones(n, dtype=bool)
    else:
        no_decel = np.asarray(no_decel_arr, dtype=bool)
    if is_long:
        dch = np.asarray(dc_high_3m_arr, dtype=float)
        lo = np.asarray(low_3m_arr, dtype=float); lop = np.asarray(low_3m_prev_arr, dtype=float)
        base = (k15 > k_high) & (dch > 0) & (p > dch) & no_decel
        return base & (lo > 0) & (lop > 0) & (lo < lop)
    dcl = np.asarray(dc_low_3m_arr, dtype=float)
    hi = np.asarray(high_3m_arr, dtype=float); hip = np.asarray(high_3m_prev_arr, dtype=float)
    base = (k15 < k_low) & (dcl > 0) & (p < dcl) & no_decel
    return base & (hi > 0) & (hip > 0) & (hi > hip)

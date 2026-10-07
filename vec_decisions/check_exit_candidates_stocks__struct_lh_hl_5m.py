"""STRUCT_LH5M_EXIT / STRUCT_HL5M_EXIT — 5m candle structure-break exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6901-6907.
  _higher_low_5m = low_5m>0 AND low_5m_prev>0 AND low_5m > low_5m_prev
  _lower_high_5m = high_5m>0 AND high_5m_prev>0 AND high_5m < high_5m_prev
  LONG  fires when _lower_high_5m AND stoch_k_5m < stoch_k_5m_prev AND hold_time_min>15
  SHORT fires when _higher_low_5m AND stoch_k_5m > stoch_k_5m_prev AND hold_time_min>15
  Outer guard (caller): gain >= noloss AND _exit_confirmed (>=2 TFs WT against).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_struct_lh_hl_5m) AND vec (check_struct_lh_hl_5m_vec) share the SAME pure per-bar
predicate _struct_lh_hl_5m_fires(), so the two paths CANNOT drift. Pure core: no config.

STATE SEAM: hold_time_min>15 is per-position state (live position age); gain>=noloss and
the _exit_confirmed multi-TF gate are also caller state. They are layered by the caller
exactly as live. The pure core is the indicator-only LH/HL + K-turn portion.

The hold_time_min>15 gate is included in BOTH scalar and vec here (passed as a per-bar
age array) because it is a numeric per-bar comparison once the engine supplies the age
seam — faithful to the existing wt_4h_vel pattern where age is a state seam. To keep the
core PURE (indicator-only) for deterministic parity, the age gate is applied by the
wrapper/vec caller, NOT inside _struct_lh_hl_5m_fires.

NPZ / indicator fields read by the core:
  low_5m, low_5m_prev, high_5m, high_5m_prev, stoch_k_5m, stoch_k_5m_prev.
"""
from typing import Tuple


def _struct_lh_hl_5m_fires(low_5m: float, low_5m_prev: float, high_5m: float, high_5m_prev: float,
                           stoch_k_5m: float, stoch_k_5m_prev: float, is_long: bool) -> bool:
    """PURE per-bar indicator-only condition. Faithful replica of tradier_manage.py
    lines 6901-6906 minus the hold>15 / gain / exit_confirmed state gates."""
    higher_low = low_5m > 0 and low_5m_prev > 0 and low_5m > low_5m_prev
    lower_high = high_5m > 0 and high_5m_prev > 0 and high_5m < high_5m_prev
    if is_long:
        return lower_high and stoch_k_5m < stoch_k_5m_prev
    return higher_low and stoch_k_5m > stoch_k_5m_prev


def check_struct_lh_hl_5m(config, indicators: dict, gain_pct: float, hold_time_min: float,
                          exit_tf_against: int, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Indicator-only fire from the shared
    predicate; gain>=noloss, _exit_confirmed (>=2 TFs) and hold>15 state gates applied here,
    faithful to tradier_manage.py lines 6903-6907. Live .get defaults: low/high=0, K=50."""
    if not (gain_pct >= _struct_min_gain(config)):
        return False, ""
    if not (exit_tf_against >= 2):
        return False, ""
    if not (hold_time_min > 15):
        return False, ""
    low_5m = float(indicators.get("low_5m", 0) or 0)
    low_5m_prev = float(indicators.get("low_5m_prev", 0) or 0)
    high_5m = float(indicators.get("high_5m", 0) or 0)
    high_5m_prev = float(indicators.get("high_5m_prev", 0) or 0)
    k = float(indicators.get("stoch_k_5m", 50) or 50)
    kp = float(indicators.get("stoch_k_5m_prev", 50) or 50)
    if not _struct_lh_hl_5m_fires(low_5m, low_5m_prev, high_5m, high_5m_prev, k, kp, is_long):
        return False, ""
    if is_long:
        return True, f"STRUCT_LH5M_EXIT_bc100_k5:{k:.1f}<prev:{kp:.1f}_hi5:{high_5m:.2f}<prev:{high_5m_prev:.2f}_gain:{gain_pct:.2f}%_WT{exit_tf_against}TF"
    return True, f"STRUCT_HL5M_EXIT_bc100_k5:{k:.1f}>prev:{kp:.1f}_lo5:{low_5m:.2f}>prev:{low_5m_prev:.2f}_gain:{gain_pct:.2f}%_WT{exit_tf_against}TF"


def _struct_min_gain(config) -> float:
    return float(getattr(config, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 3.0))


def check_struct_lh_hl_5m_vec(config, low_5m_arr, low_5m_prev_arr, high_5m_arr, high_5m_prev_arr,
                              stoch_k_5m_arr, stoch_k_5m_prev_arr, is_long):
    """VECTORIZED per-bar indicator-only fire mask — backtest path. SAME predicate as the
    live scalar. Returns a bool ndarray; caller layers gain>=noloss, exit_confirmed and
    hold>15 state gates.
    Arrays per-bar (NPZ): low_5m, low_5m_prev, high_5m, high_5m_prev, stoch_k_5m, stoch_k_5m_prev."""
    import numpy as np
    lo = np.asarray(low_5m_arr, dtype=float)
    lop = np.asarray(low_5m_prev_arr, dtype=float)
    hi = np.asarray(high_5m_arr, dtype=float)
    hip = np.asarray(high_5m_prev_arr, dtype=float)
    k = np.asarray(stoch_k_5m_arr, dtype=float)
    kp = np.asarray(stoch_k_5m_prev_arr, dtype=float)
    higher_low = (lo > 0) & (lop > 0) & (lo > lop)
    lower_high = (hi > 0) & (hip > 0) & (hi < hip)
    if is_long:
        return lower_high & (k < kp)
    return higher_low & (k > kp)

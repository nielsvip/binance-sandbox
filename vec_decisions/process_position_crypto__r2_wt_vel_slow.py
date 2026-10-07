"""SHARED scalar+vectorized predicate for the LIVE decision R2_WT_VEL_SLOW.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~38894-38947.

R2 (USER 2026-05-08, tightened 2026-05-09): peak-then-collapse near-breakeven
exit. Per TF in R2_TF_LIST, fires CLOSE when:

  GATE   : max_gain >= R2_PEAK_MIN_PCT  AND  floor <= gain < band
  AGAINST: wt_velocity_<TF> opposes the position
             LONG : v < 0 ;  SHORT : v > 0
  SLOW   : DECEL  : |v| < |v_prev| * WT_VEL_DECEL_RATIO  AND  |v_prev| > 1e-6
           OR
           DYING  : (not decel_only) AND |v| <= WT_15M_VEL_NEAR_ZERO_THRESHOLD
  fires the first TF for which (AGAINST and (DECEL or DYING)).

  v_prev is DERIVED: v_prev = wt_velocity_<TF> - wt_acceleration_<TF>
  (live bug-fix 2026-05-18: accel = velocity - velocity_prev).

A separate HTF-HOLD veto cancels the fire if Daily WT supports the side
(wt1_D vs wt2_D, only when both |wt1_D|>1e-9 and |wt2_D|>1e-9). That veto is
included here as a final guard so the predicate matches live exactly.

CLASSIFICATION: stateful_seam. Pure per-bar over NPZ velocity/accel/wt1_D/wt2_D
fields PLUS two per-position seam values the engine maintains: gain and max_gain.
This module exposes a single-TF core (the per-TF fire test) which the caller
iterates over R2_TF_LIST taking the first True — identical to live's `break`.
"""
from typing import Tuple
import numpy as np


def _r2_tf_fires(gain: float, max_gain: float, v: float, accel: float,
                 wt1_D: float, wt2_D: float, is_long: bool,
                 peak_min: float, floor: float, band: float,
                 decel_ratio: float, near_zero: float, decel_only: bool) -> bool:
    """Pure per-TF fire test (one timeframe). HTF-HOLD veto applied at the end."""
    if not (max_gain >= peak_min and floor <= gain < band):
        return False
    v_prev = v - accel
    against = (is_long and v < 0) or ((not is_long) and v > 0)
    if not against:
        return False
    decel = abs(v) < abs(v_prev) * decel_ratio and abs(v_prev) > 1e-6
    dying = (not decel_only) and abs(v) <= near_zero
    if not (decel or dying):
        return False
    htf_ok = abs(wt1_D) > 1e-9 and abs(wt2_D) > 1e-9
    if htf_ok:
        htf_supports = (is_long and wt1_D > wt2_D) or ((not is_long) and wt1_D < wt2_D)
        if htf_supports:
            return False
    return True


def _r2_params(config, psym=None):
    g = (psym or {})
    return (float(getattr(config, "R2_PEAK_MIN_PCT", 0.5)),
            float(g.get("WT_15M_VEL_SLOW_GAIN_BAND_PCT", getattr(config, "WT_15M_VEL_SLOW_GAIN_BAND_PCT", 0.10))),
            float(g.get("WT_15M_VEL_SLOW_GAIN_FLOOR_PCT", getattr(config, "WT_15M_VEL_SLOW_GAIN_FLOOR_PCT", 0.01))),
            float(g.get("WT_VEL_DECEL_RATIO", getattr(config, "WT_VEL_DECEL_RATIO", 0.5))),
            float(g.get("WT_15M_VEL_NEAR_ZERO_THRESHOLD", getattr(config, "WT_15M_VEL_NEAR_ZERO_THRESHOLD", 0.1))),
            bool(g.get("WT_VEL_USE_DECEL_RATIO_ONLY", getattr(config, "WT_VEL_USE_DECEL_RATIO_ONLY", True))))


def check_r2_wt_vel_slow(config, indicators: dict, is_long: bool, gain: float,
                         max_gain: float, tf: str = "15m", psym=None) -> Tuple[bool, str]:
    """LIVE/scalar path for ONE timeframe. gain/max_gain from per-position state."""
    peak_min, band, floor, decel_ratio, near_zero, decel_only = _r2_params(config, psym)
    v = float((indicators or {}).get(f"wt_velocity_{tf}", 0) or 0)
    accel = float((indicators or {}).get(f"wt_acceleration_{tf}", 0) or 0)
    wt1_D = float((indicators or {}).get("wt1_D", 0) or 0)
    wt2_D = float((indicators or {}).get("wt2_D", 0) or 0)
    if not _r2_tf_fires(gain, max_gain, v, accel, wt1_D, wt2_D, is_long,
                        peak_min, floor, band, decel_ratio, near_zero, decel_only):
        return False, ""
    return True, f"R2_WT_VEL_SLOW_{tf}_g{gain:.3f}%_vel{v:.3f}"


def check_r2_wt_vel_slow_vec(config, gain_arr, max_gain_arr, v_arr, accel_arr,
                             wt1_D_arr, wt2_D_arr, is_long, psym=None):
    """VECTORIZED per-bar single-TF R2 fire mask. SAME logic as _r2_tf_fires."""
    g = np.asarray(gain_arr, dtype=float)
    mg = np.asarray(max_gain_arr, dtype=float)
    v = np.asarray(v_arr, dtype=float)
    a = np.asarray(accel_arr, dtype=float)
    d1 = np.asarray(wt1_D_arr, dtype=float)
    d2 = np.asarray(wt2_D_arr, dtype=float)
    peak_min, band, floor, decel_ratio, near_zero, decel_only = _r2_params(config, psym)
    gate = (mg >= peak_min) & (g >= floor) & (g < band)
    v_prev = v - a
    if is_long:
        against = v < 0
    else:
        against = v > 0
    decel = (np.abs(v) < np.abs(v_prev) * decel_ratio) & (np.abs(v_prev) > 1e-6)
    if decel_only:
        slow = decel
    else:
        dying = np.abs(v) <= near_zero
        slow = decel | dying
    fire = gate & against & slow
    htf_ok = (np.abs(d1) > 1e-9) & (np.abs(d2) > 1e-9)
    if is_long:
        htf_supports = d1 > d2
    else:
        htf_supports = d1 < d2
    veto = htf_ok & htf_supports
    return fire & (~veto)

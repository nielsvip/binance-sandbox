"""SHARED scalar+vectorized predicate for the LIVE decision WT_4H_VEL_EXIT.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~41277-41325.

Closes (MANDATORY_REENTRY) when 4h WT velocity turns against the position, the
position is profitable, and stoch K is in the extreme zone against direction.

  AGAINST : LONG  wt_velocity_4h < WT_4H_VEL_EXIT_LONG_VEL_MIN  (default -2.0)
            SHORT wt_velocity_4h > WT_4H_VEL_EXIT_SHORT_VEL_MIN (default +2.0)
  PROFIT  : (gain >= COMMISSION_BUFFER_PCT) if WT_4H_VEL_EXIT_REQUIRE_PROFIT
  K_EXTREME (if WT_4H_VEL_EXIT_REQUIRE_K_EXTREME):
            LONG  : k_3m >= K_HI (80) OR k_15m >= K_HI
            SHORT : k_3m <= K_LO (20) OR k_15m <= K_LO
  fires iff AGAINST and profit_ok and kx_ok   (age>360s gate applied by caller)

CLASSIFICATION: stateful_seam. Pure per-bar over NPZ fields (wt_velocity_4h,
stoch_k_3m, stoch_k_15m) PLUS the per-position seam value `gain`. The age>360s
gate uses opened_at (state) and is applied by the caller, exactly like live.
"""
from typing import Tuple
import numpy as np


def _wt_4h_vel_fires(wt_vel_4h: float, gain: float, k_3m: float, k_15m: float,
                     is_long: bool, long_min: float, short_min: float,
                     req_profit: bool, comm_buf: float,
                     req_kx: bool, kx_hi: float, kx_lo: float) -> bool:
    """Pure per-bar fire test (caller still applies the age>360s gate)."""
    if is_long:
        against = wt_vel_4h < long_min
    else:
        against = wt_vel_4h > short_min
    if not against:
        return False
    profit_ok = (gain >= comm_buf) if req_profit else True
    if not profit_ok:
        return False
    if req_kx:
        if is_long:
            kx_ok = (k_3m >= kx_hi) or (k_15m >= kx_hi)
        else:
            kx_ok = (k_3m <= kx_lo) or (k_15m <= kx_lo)
    else:
        kx_ok = True
    return kx_ok


def _wt_4h_params(config):
    return (float(getattr(config, "WT_4H_VEL_EXIT_LONG_VEL_MIN", -2.0)),
            float(getattr(config, "WT_4H_VEL_EXIT_SHORT_VEL_MIN", 2.0)),
            bool(getattr(config, "WT_4H_VEL_EXIT_REQUIRE_PROFIT", True)),
            float(getattr(config, "COMMISSION_BUFFER_PCT", 0.10)),
            bool(getattr(config, "WT_4H_VEL_EXIT_REQUIRE_K_EXTREME", True)),
            float(getattr(config, "WT_4H_VEL_EXIT_K_EXTREME_HIGH", 80.0)),
            float(getattr(config, "WT_4H_VEL_EXIT_K_EXTREME_LOW", 20.0)))


def check_wt_4h_vel_exit(config, indicators: dict, is_long: bool, gain: float) -> Tuple[bool, str]:
    """LIVE/scalar path. gain from per-position state. age gate applied by caller."""
    lo_min, sh_min, req_p, cb, req_kx, khi, klo = _wt_4h_params(config)
    v = float((indicators or {}).get("wt_velocity_4h", 0) or 0)
    k3 = float((indicators or {}).get("stoch_k_3m", 50.0) or 50.0)
    k15 = float((indicators or {}).get("stoch_k_15m", 50.0) or 50.0)
    if not _wt_4h_vel_fires(v, gain, k3, k15, is_long, lo_min, sh_min, req_p, cb, req_kx, khi, klo):
        return False, ""
    return True, f"WT_4H_VEL_EXIT_vel={v:.1f}_g={gain:.2f}%"


def check_wt_4h_vel_exit_vec(config, wt_vel_4h_arr, gain_arr, k_3m_arr, k_15m_arr, is_long):
    """VECTORIZED per-bar fire mask (age gate applied separately). SAME logic."""
    v = np.asarray(wt_vel_4h_arr, dtype=float)
    g = np.asarray(gain_arr, dtype=float)
    k3 = np.asarray(k_3m_arr, dtype=float)
    k15 = np.asarray(k_15m_arr, dtype=float)
    lo_min, sh_min, req_p, cb, req_kx, khi, klo = _wt_4h_params(config)
    if is_long:
        against = v < lo_min
    else:
        against = v > sh_min
    profit_ok = (g >= cb) if req_p else np.ones_like(g, dtype=bool)
    if req_kx:
        if is_long:
            kx_ok = (k3 >= khi) | (k15 >= khi)
        else:
            kx_ok = (k3 <= klo) | (k15 <= klo)
    else:
        kx_ok = np.ones_like(g, dtype=bool)
    return against & profit_ok & kx_ok

"""WT_4H_VEL_EXIT — 4h WT-velocity slowdown exit predicate (shared scalar+vec).

LIVE SOURCE: ez_manage.py process_position exit loop, lines ~41258-41331.
  _4h_against:
    LONG  : wt_velocity_4h < WT_4H_VEL_EXIT_LONG_VEL_MIN   (default -2.0)
    SHORT : wt_velocity_4h > WT_4H_VEL_EXIT_SHORT_VEL_MIN   (default  2.0)
  _kx_ok (only when WT_4H_VEL_EXIT_REQUIRE_K_EXTREME, default True):
    LONG  : stoch_k_3m >= K_EXTREME_HIGH  OR  stoch_k_15m >= K_EXTREME_HIGH  (default 80)
    SHORT : stoch_k_3m <= K_EXTREME_LOW   OR  stoch_k_15m <= K_EXTREME_LOW   (default 20)
  Fires (closes 0.95) when: _4h_against AND _kx_ok AND _pos_age_s > 360 AND _profit_ok
  where _profit_ok = gain >= COMMISSION_BUFFER_PCT (default 0.10) when REQUIRE_PROFIT (default True).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): the live
scalar (check_wt_4h_vel_exit) AND the vectorized backtest (check_wt_4h_vel_exit_vec)
BOTH derive their fire decision from the SAME pure per-bar predicate
_wt_4h_vel_exit_fires() below, so the two paths CANNOT drift. Pure core: no config,
no state. Data source differs (live market_data indicators vs NPZ arrays) but the
predicate is identical.

STATE/AGE/PROFIT SEAM (faithful to live + to the existing position_evaluator.py
`wt_4h_vel_full` mask, which also leaves profit+age to the caller): the shared core
covers ONLY the indicator-only portion (vel-against AND k-extreme). The age>360s gate
and the profit_ok (gain >= commission buffer) gate are STATE-dependent (need position
opened_at and live gain), so the caller layers them on top — exactly as ez_manage.py
does (line 41313: `if _4h_against and _pos_age_s > 360 and _profit_ok and _kx_ok`) and
exactly as v8_vec_sweep.py:3833 does (`exit_gates["wt_4h_vel_full"][i] and age_s > 360
and gain >= _min_gain_bar`). This keeps the pure core deterministic for parity testing.

NPZ / indicator fields read by the core (via the wrappers):
  - wt_velocity_4h   (4h WaveTrend velocity)            — live: i.get("wt_velocity_4h")
  - stoch_k_3m       (3m stoch K; crypto base TF)       — live: i.get("stoch_k_3m")
  - stoch_k_15m      (15m stoch K)                       — live: i.get("stoch_k_15m")
Profit/age (caller-supplied, NOT in this core): position gain%, position age seconds.

NPZ presence: wt_velocity_4h, stoch_k_3m, stoch_k_15m are read by the existing
position_evaluator.evaluate_exit_gates_vec wt_4h_vel_full path (stoch_k via f('stoch_k_{ltf}')
and f('stoch_k_15m')), so they are expected present in the crypto backtest NPZ. For the
stocks/tradier base TF (5m) the live K field would be stoch_k_5m — the wrapper takes the
base-TF K field name as a parameter so it stays faithful per mode.
"""
from typing import Tuple


def _wt_4h_vel_exit_fires(wt_velocity_4h: float, stoch_k_base: float, stoch_k_15m: float,
                          is_long: bool, long_vel_min: float, short_vel_min: float,
                          require_k_extreme: bool, k_extreme_high: float,
                          k_extreme_low: float) -> bool:
    """PURE per-bar indicator-only fire condition for WT_4H_VEL_EXIT.
    Faithful replica of ez_manage.py lines 41268-41312 (_4h_against AND _kx_ok).
    Profit (gain >= commission buffer) and age (>360s) are state gates layered by the
    caller, exactly as in the live code and the v8_vec_sweep per-bar loop."""
    if is_long:
        four_h_against = wt_velocity_4h < long_vel_min
    else:
        four_h_against = wt_velocity_4h > short_vel_min
    if not four_h_against:
        return False
    if not require_k_extreme:
        return True
    if is_long:
        return stoch_k_base >= k_extreme_high or stoch_k_15m >= k_extreme_high
    return stoch_k_base <= k_extreme_low or stoch_k_15m <= k_extreme_low


def _wt_4h_vel_exit_thresholds(config):
    return (float(getattr(config, "WT_4H_VEL_EXIT_LONG_VEL_MIN", -2.0)),
            float(getattr(config, "WT_4H_VEL_EXIT_SHORT_VEL_MIN", 2.0)),
            bool(getattr(config, "WT_4H_VEL_EXIT_REQUIRE_K_EXTREME", True)),
            float(getattr(config, "WT_4H_VEL_EXIT_K_EXTREME_HIGH", 80.0)),
            float(getattr(config, "WT_4H_VEL_EXIT_K_EXTREME_LOW", 20.0)))


def check_wt_4h_vel_exit(config, indicators: dict, gain_pct: float, pos_age_s: float,
                         is_long: bool, base_k_field: str = "stoch_k_3m") -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason).
    Indicator-only fire decision comes from the shared _wt_4h_vel_exit_fires() predicate;
    the profit (gain >= COMMISSION_BUFFER_PCT) and age (>360s) state gates are applied here,
    faithful to ez_manage.py line 41313.

    base_k_field: live crypto uses 'stoch_k_3m' (base TF 3m); tradier/stocks use 'stoch_k_5m'.
    Live default reads (mirror ez_manage .get defaults): wt_velocity_4h=0, stoch_k_*=50.0."""
    if not getattr(config, "WT_4H_VEL_EXIT_ENABLED", True):
        return False, ""
    long_min, short_min, req_kx, kx_hi, kx_lo = _wt_4h_vel_exit_thresholds(config)
    wt_velocity_4h = float(indicators.get("wt_velocity_4h", 0) or 0)
    stoch_k_base = float(indicators.get(base_k_field, 50.0) or 50.0)
    stoch_k_15m = float(indicators.get("stoch_k_15m", 50.0) or 50.0)
    if not _wt_4h_vel_exit_fires(wt_velocity_4h, stoch_k_base, stoch_k_15m, is_long, long_min,
                                 short_min, req_kx, kx_hi, kx_lo):
        return False, ""
    if pos_age_s <= 360.0:
        return False, ""
    require_profit = bool(getattr(config, "WT_4H_VEL_EXIT_REQUIRE_PROFIT", True))
    comm_buf = float(getattr(config, "COMMISSION_BUFFER_PCT", 0.10))
    if require_profit and gain_pct < comm_buf:
        return False, ""
    return True, f"WT_4H_VEL_EXIT_vel={wt_velocity_4h:.1f}_g={gain_pct:.2f}%_k={stoch_k_base:.0f}/{stoch_k_15m:.0f}_MANDATORY_REENTRY"


def check_wt_4h_vel_exit_vec(config, wt_velocity_4h_arr, stoch_k_base_arr, stoch_k_15m_arr,
                             is_long):
    """VECTORIZED per-bar indicator-only fire mask — backtest path. SAME thresholds + SAME
    predicate as the live scalar check_wt_4h_vel_exit (vectorized via numpy). Returns a bool
    ndarray; the caller layers age>360s and gain>=commission-buffer (state gates) on top, exactly
    as v8_vec_sweep.py:3833 does. This is the 'wt_4h_vel_full' equivalent.
    Arrays are per-bar (NPZ): wt_velocity_4h, stoch_k_<baseTF>, stoch_k_15m."""
    import numpy as np
    v = np.asarray(wt_velocity_4h_arr, dtype=float)
    if not getattr(config, "WT_4H_VEL_EXIT_ENABLED", True):
        return np.zeros(len(v), dtype=bool)
    long_min, short_min, req_kx, kx_hi, kx_lo = _wt_4h_vel_exit_thresholds(config)
    kb = np.asarray(stoch_k_base_arr, dtype=float)
    k15 = np.asarray(stoch_k_15m_arr, dtype=float)
    if is_long:
        m = v < long_min
    else:
        m = v > short_min
    if req_kx:
        if is_long:
            m = m & ((kb >= kx_hi) | (k15 >= kx_hi))
        else:
            m = m & ((kb <= kx_lo) | (k15 <= kx_lo))
    return m

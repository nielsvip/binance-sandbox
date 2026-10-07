"""DELTA_EXIT_TOP / DELTA_EXIT_BOTTOM — DeltaTracker RZ-zone exit predicate
(shared scalar+vec).

LIVE SOURCE: wt_dc_delta.py DeltaTracker.update() RZ-zone block.
  - TOP-zone EXIT_LONG  (lines ~895-920):  zone==TOP  AND  sig.exit_long set by
      T1 (line 911-915): position LONG, in TOP zone, and
                         (_exhaust_up or _bull_div or _wt_vel_1h < 0 or _k_1h > 90)
      T2 (line 917-920): position LONG, in TOP zone, NOT _broke_above,
                         _dc_pos_1h > 0.7 and _wt_vel_1h < 0
  - BOTTOM-zone EXIT_SHORT (lines ~931-961): zone==BOTTOM AND sig.exit_short set by
      bounce branch (line 956-961): position SHORT, in BOTTOM zone, NOT the phase-1
                         breakdown-truck branch, and
                         (_k_15m < 5) OR (_dc_low4_1h > 0 and price <= _dc_low4_1h*1.002
                                          and _wt_vel_3m > 2.0)

The live consumer is ez_manage.py:41943-41991: when trade_manager.delta_tracker.update()
returns sig.exit_long (LONG pos) / sig.exit_short (SHORT pos), it fires a QUICK_CLOSE with
reason "DELTA_EXIT_...". The discrepancy_monitor labels the TOP-zone path "DELTA_EXIT_TOP".

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): the live
scalar (check_delta_exit_top) AND the vectorized backtest (check_delta_exit_top_vec)
BOTH derive their fire decision from the same pure per-bar predicate
_delta_exit_top_fires() below, so the two paths CANNOT drift. Pure core: no config, no
state. Data source differs (live market_data indicators vs NPZ arrays) but predicate is
identical.

FAITHFULNESS CAVEATS (read notes in the returned spec):
  - This extracts ONLY the RZ-zone TOP-exit-long / BOTTOM-exit-short branches. The
    DeltaTracker.update() also has a separate "phase 1/2" speed-decay exit block
    (wt_dc_delta.py ~429-544) plus a HTF-slowdown veto (~447-455 / ~534-542) and a
    divergence/zscore two-phase exit (~570-601). Those are NOT part of this predicate;
    they are stateful (prev dict, exit_pending) and are a separate decision. This module
    is strictly the zone-based TOP/BOTTOM RZ exit.
  - The live zone gating uses RZ_EXIT_ENABLED / DELTA_EXIT_ENABLED master switches
    (checked in ez_manage and wt_dc_delta). Honored here via _delta_exit_enabled().

NPZ / indicator fields read (all confirmed present in backtest NPZ on S1 indicators/*.npz):
  TOP (EXIT_LONG):
    current_price (live) / close (NPZ)   -- price level
    bb_upper_1h, dc_high_1h              -- _above_bb / _above_dc_high (-> _near_top, _broke_above)
    bb_pct_b_1h                          -- _bb_1h  (-> _near_top)
    dc_position_1h                       -- _dc_pos_1h (-> _near_top, T2)
    wt_zscore_1h, wt_zscore_4h           -- _zscore_overbought (-> _near_top)
    wt_momentum_state_1h, wt_momentum_state_4h  -- _exhaust_up (==2 both)
    wt_divergence_1h, wt_divergence_4h, wt_divergence_D  -- _bull_div (any == -1)
    wt_velocity_1h                       -- T1/T2
    stoch_k_1h                           -- T1 (_k_1h > 90)
    dc_high_crossover_1h                 -- _broke_above (high_co)  [present in NPZ]
  BOTTOM (EXIT_SHORT):
    current_price / close
    bb_lower_1h, dc_low_1h               -- _below_bb / _below_dc_low (-> _near_bottom, _broke_below)
    bb_pct_b_1h, dc_position_1h          -- _near_bottom
    wt_zscore_1h, wt_zscore_4h           -- _zscore_oversold
    wt_momentum_state_1h, wt_momentum_state_4h  -- _impulse_down (==-1 either), _exhaust_down
    stoch_k_15m                          -- _k_15m
    dc_low4_1h                           -- bounce
    wt_velocity_3m (fallback wt_velocity_3m)  -- bounce (live uses rz_ltf_micro, default 3m)
    stoch_k_4h, mfi_1h, mfi_4h           -- _bear_legs (for phase-1 breakdown-truck exclusion)
    dc_low_crossunder_1h                 -- _broke_below (low_cu) [present in NPZ]
"""
from typing import Tuple


# ── shared zone helpers (pure) ────────────────────────────────────────────────
def _near_top_fires(price, bb_upper_1h, dc_high_1h, bb_pct_b_1h, dc_pos_1h,
                    zs_1h, zs_4h, rz_top_bb, zscore_enabled):
    _above_bb = price > bb_upper_1h > 0
    _above_dc_high = price > dc_high_1h > 0
    _zscore_overbought = zscore_enabled and (zs_4h > 2.0 or (zs_1h > 2.0 and zs_4h > 1.5))
    return _above_bb or _above_dc_high or bb_pct_b_1h > rz_top_bb or dc_pos_1h > rz_top_bb or _zscore_overbought


def _near_bottom_fires(price, bb_lower_1h, dc_low_1h, bb_pct_b_1h, dc_pos_1h,
                       zs_1h, zs_4h, rz_bot_bb, zscore_enabled):
    _below_bb = (0 < price < bb_lower_1h) if bb_lower_1h > 0 else False
    _below_dc_low = (0 < price < dc_low_1h) if dc_low_1h > 0 else False
    _zscore_oversold = zscore_enabled and (zs_4h < -2.0 or (zs_1h < -2.0 and zs_4h < -1.5))
    return _below_bb or _below_dc_low or bb_pct_b_1h < rz_bot_bb or dc_pos_1h < rz_bot_bb or _zscore_oversold


# ── PURE per-bar fire condition ───────────────────────────────────────────────
def _delta_exit_top_fires(is_long, price, bb_upper_1h, bb_lower_1h, dc_high_1h, dc_low_1h,
                          bb_pct_b_1h, dc_pos_1h, zs_1h, zs_4h,
                          wt_mom_1h, wt_mom_4h, wt_div_1h, wt_div_4h, wt_div_D,
                          wt_vel_1h, k_1h, dc_high_co_1h,
                          k_15m, dc_low4_1h, wt_vel_3m, k_4h, mfi_1h, mfi_4h, dc_low_cu_1h,
                          rz_top_bb, rz_bot_bb, zscore_enabled):
    """PURE per-bar fire condition for the RZ-zone DELTA_EXIT.
    LONG  -> DELTA_EXIT_TOP    (faithful replica wt_dc_delta.py lines 895-920)
    SHORT -> DELTA_EXIT_BOTTOM (faithful replica wt_dc_delta.py lines 931-961)."""
    if is_long:
        _near_top = _near_top_fires(price, bb_upper_1h, dc_high_1h, bb_pct_b_1h, dc_pos_1h,
                                    zs_1h, zs_4h, rz_top_bb, zscore_enabled)
        if not _near_top:
            return False
        _exhaust_up = wt_mom_1h == 2 and wt_mom_4h == 2
        _bull_div = wt_div_1h == -1 or wt_div_4h == -1 or wt_div_D == -1
        _above_bb = price > bb_upper_1h > 0
        _above_dc_high = price > dc_high_1h > 0
        _broke_above = dc_high_co_1h or _above_bb or _above_dc_high
        # T1: exit long on exhaustion at top
        t1 = _exhaust_up or _bull_div or wt_vel_1h < 0 or k_1h > 90
        # T2: failed breakout — back below resistance with negative vel
        t2 = (not _broke_above) and dc_pos_1h > 0.7 and wt_vel_1h < 0
        return bool(t1 or t2)
    # SHORT -> BOTTOM
    _near_bottom = _near_bottom_fires(price, bb_lower_1h, dc_low_1h, bb_pct_b_1h, dc_pos_1h,
                                      zs_1h, zs_4h, rz_bot_bb, zscore_enabled)
    if not _near_bottom:
        return False
    _impulse_down = wt_mom_1h == -1 or wt_mom_4h == -1
    _exhaust_down = wt_mom_1h == -2 and wt_mom_4h == -2
    _below_bb = (0 < price < bb_lower_1h) if bb_lower_1h > 0 else False
    _below_dc_low = (0 < price < dc_low_1h) if dc_low_1h > 0 else False
    _broke_below = dc_low_cu_1h or _below_bb or _below_dc_low
    _bear_legs = min(k_4h, mfi_4h)
    if _exhaust_down:
        _bear_legs *= 0.3
    # Phase 1 = breakdown-truck SELL branch (NOT an exit_short) — the bounce-exit is the
    # `elif` so phase-1 firing means the bounce-exit cannot fire on this bar.
    _phase1_breakdown = _broke_below and _impulse_down and _bear_legs > 15 and k_15m > 20
    if _phase1_breakdown:
        return False
    # Phase 2 = BOUNCE -> EXIT_SHORT (the elif branch, lines 956-961)
    _bounce = (k_15m < 5) or (dc_low4_1h > 0 and price <= dc_low4_1h * 1.002 and wt_vel_3m > 2.0)
    return bool(_bounce)


def _delta_exit_enabled(config):
    return bool(getattr(config, "DELTA_ENGINE_ENABLED", False)) \
        and bool(getattr(config, "DELTA_EXIT_ENABLED", True)) \
        and bool(getattr(config, "RZ_EXIT_ENABLED", True))


def _delta_exit_thresholds(config):
    return (float(getattr(config, "RZ_TOP_BB_THRESHOLD", 0.85)),
            float(getattr(config, "RZ_BOT_BB_THRESHOLD", 0.15)),
            bool(getattr(config, "RZ_ZSCORE_ZONE_ENABLED", True)))


def check_delta_exit_top(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason).
    Fire decision comes from the shared _delta_exit_top_fires() predicate.
    Defaults mirror the _safe_float(...) or-fallbacks in wt_dc_delta.py."""
    if not _delta_exit_enabled(config):
        return False, ""
    rz_top_bb, rz_bot_bb, zscore_enabled = _delta_exit_thresholds(config)

    def _f(key, default=0.0):
        v = indicators.get(key)
        try:
            return float(v) if v is not None else float(default)
        except (TypeError, ValueError):
            return float(default)

    def _i(key):
        return int(_f(key, 0))

    def _b(key):
        return bool(indicators.get(key))

    price = _f("current_price", 0.0)
    bb_upper_1h = _f("bb_upper_1h", 0.0)
    bb_lower_1h = _f("bb_lower_1h", 0.0)
    dc_high_1h = _f("dc_high_1h", 0.0)
    dc_low_1h = _f("dc_low_1h", 0.0)
    bb_pct_b_1h = _f("bb_pct_b_1h", 0.5)
    dc_pos_1h = _f("dc_position_1h", 0.5)
    zs_1h = _f("wt_zscore_1h", 0.0)
    zs_4h = _f("wt_zscore_4h", 0.0)
    wt_mom_1h = _i("wt_momentum_state_1h")
    wt_mom_4h = _i("wt_momentum_state_4h")
    wt_div_1h = _i("wt_divergence_1h")
    wt_div_4h = _i("wt_divergence_4h")
    wt_div_D = _i("wt_divergence_D")
    wt_vel_1h = _f("wt_velocity_1h", 0.0)
    k_1h = _f("stoch_k_1h", 50.0)
    dc_high_co_1h = _b("dc_high_crossover_1h")
    k_15m = _f("stoch_k_15m", 50.0)
    dc_low4_1h = _f("dc_low4_1h", 0.0)
    _vel3 = indicators.get("wt_velocity_3m")
    wt_vel_3m = _f("wt_velocity_3m", 0.0) if _vel3 is not None else _f("wt_velocity_3m", 0.0)
    k_4h = _f("stoch_k_4h", 50.0)
    mfi_1h = _f("mfi_1h", 50.0)
    mfi_4h = _f("mfi_4h", 50.0)
    dc_low_cu_1h = _b("dc_low_crossunder_1h")
    fires = _delta_exit_top_fires(is_long, price, bb_upper_1h, bb_lower_1h, dc_high_1h, dc_low_1h,
                                  bb_pct_b_1h, dc_pos_1h, zs_1h, zs_4h,
                                  wt_mom_1h, wt_mom_4h, wt_div_1h, wt_div_4h, wt_div_D,
                                  wt_vel_1h, k_1h, dc_high_co_1h,
                                  k_15m, dc_low4_1h, wt_vel_3m, k_4h, mfi_1h, mfi_4h, dc_low_cu_1h,
                                  rz_top_bb, rz_bot_bb, zscore_enabled)
    if not fires:
        return False, ""
    if is_long:
        return True, f"DELTA_EXIT_TOP_vel1h={wt_vel_1h:.1f}_k1h={k_1h:.0f}_bb={bb_pct_b_1h:.2f}_dc={dc_pos_1h:.2f}"
    return True, f"DELTA_EXIT_BOTTOM_k15m={k_15m:.0f}_dc4={dc_low4_1h:.4f}_vel3m={wt_vel_3m:.1f}"


def check_delta_exit_top_vec(config, price_arr, bb_upper_1h_arr, bb_lower_1h_arr,
                             dc_high_1h_arr, dc_low_1h_arr, bb_pct_b_1h_arr, dc_pos_1h_arr,
                             zs_1h_arr, zs_4h_arr, wt_mom_1h_arr, wt_mom_4h_arr,
                             wt_div_1h_arr, wt_div_4h_arr, wt_div_D_arr, wt_vel_1h_arr,
                             k_1h_arr, dc_high_co_1h_arr, k_15m_arr, dc_low4_1h_arr,
                             wt_vel_3m_arr, k_4h_arr, mfi_1h_arr, mfi_4h_arr,
                             dc_low_cu_1h_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME thresholds + SAME predicate as
    the live scalar check_delta_exit_top (vectorized via numpy). Returns a bool ndarray.
    Arrays are per-bar NPZ. Caller takes first True per position (once-per-position)."""
    import numpy as np
    p = np.asarray(price_arr, dtype=float)
    n = len(p)
    if not _delta_exit_enabled(config):
        return np.zeros(n, dtype=bool)
    rz_top_bb, rz_bot_bb, zscore_enabled = _delta_exit_thresholds(config)
    bb_up = np.asarray(bb_upper_1h_arr, dtype=float)
    bb_lo = np.asarray(bb_lower_1h_arr, dtype=float)
    dc_hi = np.asarray(dc_high_1h_arr, dtype=float)
    dc_lo = np.asarray(dc_low_1h_arr, dtype=float)
    bb_pb = np.asarray(bb_pct_b_1h_arr, dtype=float)
    dc_pos = np.asarray(dc_pos_1h_arr, dtype=float)
    zs1 = np.asarray(zs_1h_arr, dtype=float)
    zs4 = np.asarray(zs_4h_arr, dtype=float)
    mom1 = np.asarray(wt_mom_1h_arr, dtype=float)
    mom4 = np.asarray(wt_mom_4h_arr, dtype=float)
    div1 = np.asarray(wt_div_1h_arr, dtype=float)
    div4 = np.asarray(wt_div_4h_arr, dtype=float)
    divD = np.asarray(wt_div_D_arr, dtype=float)
    vel1 = np.asarray(wt_vel_1h_arr, dtype=float)
    k1 = np.asarray(k_1h_arr, dtype=float)
    hi_co = np.asarray(dc_high_co_1h_arr).astype(bool)
    k15 = np.asarray(k_15m_arr, dtype=float)
    dc_lo4 = np.asarray(dc_low4_1h_arr, dtype=float)
    vel3 = np.asarray(wt_vel_3m_arr, dtype=float)
    k4 = np.asarray(k_4h_arr, dtype=float)
    mfi4 = np.asarray(mfi_4h_arr, dtype=float)
    lo_cu = np.asarray(dc_low_cu_1h_arr).astype(bool)
    if is_long:
        _above_bb = (p > bb_up) & (bb_up > 0)
        _above_dc_high = (p > dc_hi) & (dc_hi > 0)
        _zob = zscore_enabled & ((zs4 > 2.0) | ((zs1 > 2.0) & (zs4 > 1.5)))
        _near_top = _above_bb | _above_dc_high | (bb_pb > rz_top_bb) | (dc_pos > rz_top_bb) | _zob
        _exhaust_up = (mom1 == 2) & (mom4 == 2)
        _bull_div = (div1 == -1) | (div4 == -1) | (divD == -1)
        _broke_above = hi_co | _above_bb | _above_dc_high
        t1 = _exhaust_up | _bull_div | (vel1 < 0) | (k1 > 90)
        t2 = (~_broke_above) & (dc_pos > 0.7) & (vel1 < 0)
        return _near_top & (t1 | t2)
    _below_bb = (p > 0) & (p < bb_lo) & (bb_lo > 0)
    _below_dc_low = (p > 0) & (p < dc_lo) & (dc_lo > 0)
    _zos = zscore_enabled & ((zs4 < -2.0) | ((zs1 < -2.0) & (zs4 < -1.5)))
    _near_bottom = _below_bb | _below_dc_low | (bb_pb < rz_bot_bb) | (dc_pos < rz_bot_bb) | _zos
    _impulse_down = (mom1 == -1) | (mom4 == -1)
    _exhaust_down = (mom1 == -2) & (mom4 == -2)
    _broke_below = lo_cu | _below_bb | _below_dc_low
    _bear_legs = np.where(_exhaust_down, np.minimum(k4, mfi4) * 0.3, np.minimum(k4, mfi4))
    _phase1 = _broke_below & _impulse_down & (_bear_legs > 15) & (k15 > 20)
    _bounce = (k15 < 5) | ((dc_lo4 > 0) & (p <= dc_lo4 * 1.002) & (vel3 > 2.0))
    return _near_bottom & (~_phase1) & _bounce

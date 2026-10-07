# -*- coding: utf-8 -*-
"""ported_exit — faithful numpy twins of ez_manage/tradier_manage exit-lifecycle switches.
Owned by the exit wiring agent (SWITCH_WIRING_GUIDE.md). Rules: IDENTICAL to live (same thresholds,
operators, TF; 15m floor, 3m/5m->15m), NO proxies, NO fabrication (BIBLE §19). Each switch block is
gated on its cfg value differing from the effective default and applies a REAL mask to the signal.
apply() is a pure passthrough when no switch is active (returns sig unchanged).

WIRED families (real, gain-agnostic, pure-per-bar live decisions in ez_manage.process_position;
NPZ keys confirmed via backtest_v8_precompute.py):
  1. WT_PERCENTILE_EXIT      — ez_manage.py:49948-49973  (ENABLED, OB_D, OB_4H, OS_D, OS_4H)
  2. E_1_WT_DELTA_EXIT       — ez_manage.py:49992-50004  (E_1_WT_EXIT_USE_DELTA_ENABLED, E_1_EXIT_DELTA_THR)
  3. E_3_STRUCTURE_EXIT      — ez_manage.py:50023-50061  (E_3_USE_WT_STRUCTURE_EXIT_MODE)
  4. HTF_AGAINST_FORCE_CLOSE — ez_manage.py:46770-46818  (ENABLED, WT_CROSS_EXIT_REQUIRE_15M_CONFIRM,
                               CONFIRM_4H, CONFIRM_3M, CONFIRM_D)
  5. EXIT_BLOCKER_REQUIRE_LH_LL — ez_manage.py:30116-30141 (EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED; a VETO)

Enum encodings (backtest_v8_precompute.py:1197-1211):
  wt_structure_/wt_peak_structure_ int8: +1=HH, -1=LH, 0=neutral.
  wt_trough_structure_ int8: +1=HL, -1=LL, 0=neutral.

NOT wired here (documented in the agent report):
  - stateful_seam exits (need per-position gain/entry_price/age not passed to apply): WT_4H_VEL_EXIT*,
    WT_EXHAUST_EXIT*, DC_HOPELESS_EXIT*, LOSS_EXIT_STOP_FUNCTIONS_KILL, HARD_BREAKEVEN*, BOTTOM_EXIT_HTF_WT_VETO
    (vetoes a stateful hard-stop / gain+age NEWBORN_LOSS_KILL).
  - MULTI_TF_EXIT score components (gain-dependent threshold, can't fire independently):
    DC_BREAK_WAIT_WT15_CLOSE_ENABLED, WT_15M_LH_WAIT_EXIT_ENABLED, WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED.
  - VEC_UNSUPPORTED (data not in NPZ): funding_rate, order-book walls (SCALP_V3_K_OB_*), session UTC clock
    (INTRADAY_SESSION_FORCE_EXIT_UTC).
  - SKIPPED (no real live decision — registry entries + no-op stub farms only): MACD_EXIT*, MU_CORRECTION,
    PEAK_GIVEBACK, SIMPLE_TP, SCALP_V3_*, STDEV_*, TREND_*, REVERSE_ON_EXIT, RULE_B_3M, DYN_STRUCT_TRAIL,
    HTF_EXIT_VETO*, BB_SQUEEZE_EXIT, ATR_TRAIL_SWEEP, ALL_TF_AGAINST_CLOSE* (real close code is DEAD_CODE).
  - ABSENT both venues: DAYTRADE_DC_STOP_TF, EXIT_VELOCITY_WT_TFS.
"""
import numpy as np


def apply(npz, n, is_long, cfg, sig, _safe, close):
    # === EXIT PORTED SWITCHES (one gated block per family; each mirrors the live predicate 1:1) ===

    # -- 1. WT_PERCENTILE_EXIT (ez_manage.py:49948-49973) -------------------------------------------
    # Gain-AGNOSTIC close: D + 4h WT percentile both extreme AND 15m WT crossed against.
    #   LONG  : pct_D > OB_D(90) AND pct_4h > OB_4H(75) AND wt1_15m < wt2_15m
    #   SHORT : pct_D < OS_D(10) AND pct_4h < OS_4H(25) AND wt1_15m > wt2_15m
    try:
        if bool(getattr(cfg, 'WT_PERCENTILE_EXIT_ENABLED', False)):
            _pct_D = _safe(npz, 'wt_percentile_D', n, 50.0)
            _pct_4h = _safe(npz, 'wt_percentile_4h', n, 50.0)
            _wp1 = _safe(npz, 'wt1_15m', n, 50.0)
            _wp2 = _safe(npz, 'wt2_15m', n, 50.0)
            if is_long:
                _fire = ((_pct_D > float(getattr(cfg, 'WT_PERCENTILE_EXIT_OB_D', 90) or 90))
                         & (_pct_4h > float(getattr(cfg, 'WT_PERCENTILE_EXIT_OB_4H', 75) or 75))
                         & (_wp1 < _wp2))
            else:
                _fire = ((_pct_D < float(getattr(cfg, 'WT_PERCENTILE_EXIT_OS_D', 10) or 10))
                         & (_pct_4h < float(getattr(cfg, 'WT_PERCENTILE_EXIT_OS_4H', 25) or 25))
                         & (_wp1 > _wp2))
            sig = sig | _fire
    except Exception:
        pass

    # -- 2. E_1_WT_DELTA_EXIT (ez_manage.py:49992-50004) --------------------------------------------
    # Exit when wt_composite_delta crosses threshold against the position (no gain gate).
    #   guard: delta present (npz field exists);  LONG: delta < -THR ;  SHORT: delta > +THR
    try:
        if bool(getattr(cfg, 'E_1_WT_EXIT_USE_DELTA_ENABLED', False)):
            _e1_thr = float(getattr(cfg, 'E_1_EXIT_DELTA_THR', 50.0) or 50.0)
            _delta = _safe(npz, 'wt_composite_delta', n, 0.0)
            _present = ('wt_composite_delta' in npz)
            if _present:
                _fire = (_delta < -_e1_thr) if is_long else (_delta > _e1_thr)
                sig = sig | _fire
    except Exception:
        pass

    # -- 3. E_3_STRUCTURE_EXIT (ez_manage.py:50023-50061) -------------------------------------------
    # wt_structure HH/HL reversal across {15m,1h,4h}; fires when >=2 TFs against. mode 0=off,
    # 1=shadow (log only, NO action), 2=live exit. Only mode==2 changes the ledger.
    # int8 encoding: wt_structure == -1 is 'LH' (long-against), == +1 is 'HH' (short-against).
    try:
        _e3_mode = int(getattr(cfg, 'E_3_USE_WT_STRUCTURE_EXIT_MODE', 0) or 0)
        if _e3_mode == 2:
            _e3_against = np.zeros(n, dtype=int)
            for _tf in ('15m', '1h', '4h'):
                _st = _safe(npz, f'wt_structure_{_tf}', n, 0.0)
                if is_long:
                    _e3_against = _e3_against + (_st == -1).astype(int)
                else:
                    _e3_against = _e3_against + (_st == 1).astype(int)
            sig = sig | (_e3_against >= 2)
    except Exception:
        pass

    # -- 4. HTF_AGAINST_FORCE_CLOSE (ez_manage.py:46770-46818) --------------------------------------
    # Gain-AGNOSTIC: close the instant wt1_1h is against the position, optionally confirmed on
    # 15m / 4h / 3m / D. Each confirm is an AND-tightening only applied when data is present and the
    # prior confirm still holds (mirrors the live sequential `if ... and _hac_confirm_ok` chain).
    # 3m absent in NPZ -> the 3m confirm is skipped, exactly as the live-faithful scalar sees it.
    try:
        if bool(getattr(cfg, 'HTF_AGAINST_FORCE_CLOSE_ENABLED', False)):
            _w1_1h = _safe(npz, 'wt1_1h', n, 0.0)
            _w2_1h = _safe(npz, 'wt2_1h', n, 0.0)
            _hac_data = (np.abs(_w1_1h) > 1e-9) | (np.abs(_w2_1h) > 1e-9)
            _against_1h = (_w1_1h < _w2_1h) if is_long else (_w1_1h > _w2_1h)
            _hac_1h_against = _hac_data & _against_1h
            _confirm_ok = np.ones(n, dtype=bool)
            if bool(getattr(cfg, 'WT_CROSS_EXIT_REQUIRE_15M_CONFIRM', True)):
                _w1_15 = _safe(npz, 'wt1_15m', n, 0.0)
                _w2_15 = _safe(npz, 'wt2_15m', n, 0.0)
                _c15 = (_w1_15 < _w2_15) if is_long else (_w1_15 > _w2_15)
                _confirm_ok = _confirm_ok & _c15
            if bool(getattr(cfg, 'HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H', False)):
                _w1_4h = _safe(npz, 'wt1_4h', n, 0.0)
                _w2_4h = _safe(npz, 'wt2_4h', n, 0.0)
                _c4 = (_w1_4h < _w2_4h) if is_long else (_w1_4h > _w2_4h)
                _confirm_ok = _confirm_ok & _c4
            if bool(getattr(cfg, 'HTF_AGAINST_FORCE_CLOSE_CONFIRM_3M', True)):
                _w1_3m = _safe(npz, 'wt1_3m', n, 0.0)
                _w2_3m = _safe(npz, 'wt2_3m', n, 0.0)
                _p3 = (np.abs(_w1_3m) > 1e-9) | (np.abs(_w2_3m) > 1e-9)
                _c3 = (_w1_3m < _w2_3m) if is_long else (_w1_3m > _w2_3m)
                _confirm_ok = _confirm_ok & np.where(_p3, _c3, True)
            if bool(getattr(cfg, 'HTF_AGAINST_FORCE_CLOSE_CONFIRM_D', True)):
                _w1_D = _safe(npz, 'wt1_D', n, 0.0)
                _w2_D = _safe(npz, 'wt2_D', n, 0.0)
                _pD = (np.abs(_w1_D) > 1e-9) | (np.abs(_w2_D) > 1e-9)
                _cD = (_w1_D < _w2_D) if is_long else (_w1_D > _w2_D)
                _confirm_ok = _confirm_ok & np.where(_pD, _cD, True)
            sig = sig | (_hac_1h_against & _confirm_ok)
    except Exception:
        pass

    # -- 5. EXIT_BLOCKER_REQUIRE_LH_LL (ez_manage.py:30116-30141) -----------------------------------
    # VETO: block a technical CLOSE unless 15m structure confirms a reversal in our favor.
    #   pass (LONG)  = wt_peak_structure_15m == -1 (LH closed)  OR  forming LL (low<low_prev OR high<high_prev)
    #   pass (SHORT) = (wt_peak_structure_15m == -1 OR wt_trough_structure_15m == +1)  OR  forming LL
    # Blocked exits are removed: exit_sig &= pass.
    try:
        if bool(getattr(cfg, 'EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED', False)):
            _pk = _safe(npz, 'wt_peak_structure_15m', n, 0.0)
            _tr = _safe(npz, 'wt_trough_structure_15m', n, 0.0)
            _lo = _safe(npz, 'low_15m', n, 0.0)
            _lop = _safe(npz, 'low_15m_prev', n, 0.0)
            _hi = _safe(npz, 'high_15m', n, 0.0)
            _hip = _safe(npz, 'high_15m_prev', n, 0.0)
            _forming_ll = (((_lo > 0) & (_lop > 0) & (_lo < _lop))
                           | ((_hi > 0) & (_hip > 0) & (_hi < _hip)))
            if is_long:
                _pass = (_pk == -1) | _forming_ll
            else:
                _pass = ((_pk == -1) | (_tr == 1)) | _forming_ll
            sig = sig & _pass
    except Exception:
        pass

    # -- 6. BOTTOM/TOP exit families (all EXIT_FAMS ORed; fail-open per family) ------------------
    # LONG exits on bear-side extremes (tops), SHORT on bull-side (bottoms). Shared table in
    # bottom_top_signals.exit_fire_masks (same predicates live mirrors call). INERT unless a
    # family ENABLED is True (defaults False) + live mirror exists (DIV/SMFI staged: no rows).
    try:
        import vec_decisions.bottom_top_signals as _bts
        for _fam, _m in (_bts.exit_fire_masks(npz, n, is_long, cfg) or {}).items():
            _m = np.asarray(_m, dtype=bool)
            if _m.shape == sig.shape:
                sig = sig | _m
    except Exception:
        pass

    return sig

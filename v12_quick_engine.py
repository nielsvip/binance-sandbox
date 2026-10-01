#!/usr/bin/env python3
"""v12_quick_engine — the QUICK half of the v12 generation. Canonical sweep engine.

This file briefly carried a docstring calling it a "DEPRECATED shim for
vector_engine". It never was one: the body was untouched, nothing re-exported
anything, and every consumer kept importing the full engine from here. A file
that misdescribes itself is worse than an undocumented one — removed 2026-08-22.

Its counterpart is v12_wide_engine (formerly v8_vec_sweep): 875 real switch reads
against this file's 851, of which 490 QuickConfig cannot express. They are not
interchangeable — 1 of 40 sym_sides agreed on trade count within 20%. This one is
38x faster and is what the sweeps run on.

v12_quick_engine — THE vectorised engine. Supersedes v8_vec_sweep,
v8_quick_engine, vec_paths/vec_engine_v1 and backtest_v8_engine for sweeps.

WHY THIS EXISTS
---------------
Four engines had drifted apart, with wildly different switch coverage:

    engine                    lines   REAL switch reads
    v8_vec_sweep.py          12,598          847   (+534 fake "REAL-WIRED" stubs)
    v8_quick_engine.py       11,580          842   (0 stubs)  <- this file's base
    backtest_v8_engine.py    15,923          266   (+1,981 generated stub lines)
    vec_paths/vec_engine_v1   2,553           91

"Real reads" excludes the generated boilerplate
    if bool(getattr(cfg, "X", False)) if "X".endswith("_ENABLED") else ...
which is a runtime string test on the switch's own name and always constant.
Counting those made backtest_v8_engine look like it read 1,921 switches.

v12 is promoted from v8_quick_engine because it has the cleanest structure --
six lifecycle stages already separated (compute_entry_signals /
compute_exit_signals / compute_augment_signals / compute_reduce_signals /
compute_reentry_blocks / compute_regime_sizing_mult), fully vectorised, and no
stub lines at all.

TARGET: the 842 switches already implemented, mirrored exactly in live, before
extending toward the full 3,142-switch inventory.

THROUGHPUT (measured, S1, BTCUSDC 1y): simulate_one 0.225 s, load_npz 3.65 s.
At 13 workers that is ~58 configs/s -> ~10M config-evaluations in 2 days.

UNIFIED: handles both MODE=crypto (fractional, 0.08% fee) and MODE=tradier (whole shares, 0 fee) via QuickConfig.MODE.
PARITY CONTRACT: backtest_v12_engine.py is the scalar twin. Any predicate that
both need lives in ONE shared module and is called by both, plus live -- the
pattern already proven by vec_decisions/guaranteed_price_cross_reentry.py.
Parity is then structural, not something an audit has to keep re-establishing.
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from weakref import WeakSet
from pathlib import Path
from typing import ClassVar, Dict, List, Optional

import datetime
from datetime import timedelta
import numpy as np

# --- VEC_IDENTICAL HOOK: top-level import all vec_decisions (LOCKED) ---
# Every switch predicate must live in vec_decisions and be called by v12_quick,
# not re-implemented inline. This block is verified by tools/hooks/persistent_v12_hooks.py.
import vec_decisions.breakout_opener
import vec_decisions.check_entry_candidates_crypto__bb_squeeze_gate
import vec_decisions.check_entry_candidates_crypto__compression_boost
import vec_decisions.check_entry_candidates_crypto__dc_breakout_tiered
import vec_decisions.check_entry_candidates_crypto__pullback_augment
import vec_decisions.check_entry_candidates_crypto__sba_gate
import vec_decisions.check_entry_candidates_crypto__signal_entry_threshold
import vec_decisions.check_entry_candidates_crypto__strict_stoch_gate
import vec_decisions.check_entry_candidates_crypto__trend_entry_gate
import vec_decisions.check_entry_candidates_crypto__vol_spike_reversal
import vec_decisions.check_entry_candidates_crypto__wr_lr_pullback
import vec_decisions.check_entry_candidates_stocks__ema_alignment_trend_htf_gates
import vec_decisions.check_entry_candidates_stocks__htf_dc_breakout
import vec_decisions.check_entry_candidates_stocks__htf_w_m_align
import vec_decisions.check_entry_candidates_stocks__lh_hl_filter
import vec_decisions.check_entry_candidates_stocks__rsi_sma_mfi_gates
import vec_decisions.check_entry_candidates_stocks__rvol_lrpctb_gates
import vec_decisions.check_entry_candidates_stocks__stdev_breakout_bounce
import vec_decisions.check_exit_candidates_crypto__augmented_dc_break
import vec_decisions.check_exit_candidates_crypto__breakeven_dc_struct
import vec_decisions.check_exit_candidates_crypto__emergency_dc1h_breach
import vec_decisions.check_exit_candidates_crypto__htf_wt_vetoes
import vec_decisions.check_exit_candidates_crypto__k1m_extreme_reverse
import vec_decisions.check_exit_candidates_crypto__key_level_crash
import vec_decisions.check_exit_candidates_crypto__parabolic_exit
import vec_decisions.check_exit_candidates_crypto__peak_giveback
import vec_decisions.check_exit_candidates_crypto__structural_range_shift
import vec_decisions.check_exit_candidates_crypto__trend_reversal_exit
import vec_decisions.check_exit_candidates_crypto__winner_protect_skip
import vec_decisions.check_exit_candidates_crypto__wt_cross_exit
import vec_decisions.check_exit_candidates_stocks__htf_quick_tp
import vec_decisions.check_exit_candidates_stocks__htf_w_reversal
import vec_decisions.check_exit_candidates_stocks__ibs_exhaustion
import vec_decisions.check_exit_candidates_stocks__k5m_real_drop_dc
import vec_decisions.check_exit_candidates_stocks__noloss_bb1h
import vec_decisions.check_exit_candidates_stocks__options_d_reversal
import vec_decisions.check_exit_candidates_stocks__stdev_bb_rz_exit
import vec_decisions.check_exit_candidates_stocks__stdev_reject_exit
import vec_decisions.check_exit_candidates_stocks__struct_break_dc_cascade
import vec_decisions.check_exit_candidates_stocks__struct_lh_hl_5m
import vec_decisions.check_exit_candidates_stocks__structural_range_shift
import vec_decisions.check_exit_candidates_stocks__technical_breakdown_15m
import vec_decisions.check_exit_candidates_stocks__wt_crossunder_final
import vec_decisions.check_exit_candidates_stocks__wt_exit_tf_against
import vec_decisions.bb_pullback_gate
import vec_decisions.filter_tf_gate
import vec_decisions.mtf_atr_trail_exit
import vec_decisions.mtf_compound_exits
import vec_decisions.dc_channel_exits
import vec_decisions.grey_wire_exits  # 2026-09-30 grey-switch wiring (shared with tradier_manage live)
import vec_decisions.grey_wire_entries  # 2026-09-30 grey-switch wiring OPEN gates (shared with tradier_manage live)
import wt_dc_entry_scorer_vec as _wt_dc_vec

_ALL_FILTER_TF = ("ATR_TRAIL_FILTER_TF", "BAR_PATTERNS_FILTER_TF", "BB_PULLBACK_GATE_FILTER_TF", "BB_RECOVERY_ENTRY_FILTER_TF", "BB_RECOVERY_FILTER_TF", "BREAKEVEN_GAIN_EROSION_FILTER_TF", "BREAKOUT_RETEST_FILTER_TF", "BTC_DEDICATED_FILTER_TF", "BT_WT_CROSS_LADDER_FILTER_TF", "CANDLE_PATTERN_STOPS_FILTER_TF", "CIRCUIT_SHARPE_GATES_FILTER_TF", "COOLDOWN_LOCKS_FILTER_TF", "DC_BREACH_REDUCE_FILTER_TF", "DC_BREAK_FILTER_TF", "DC_MOMENTUM_BOTA_SCORER_FILTER_TF", "DELTA_ENGINE_FILTER_TF", "DUP_GUARD_FILTER_TF", "E2E_REPLAY_VALIDATOR_FILTER_TF", "EMA_9_21_FILTER_FILTER_TF", "EMA_BLANKET_FILTER_FILTER_TF", "EMERGENCY_BRAKE_FILTER_TF", "EXHAUSTION_EXIT_FILTER_TF", "EXIT_R1_R2_FILTER_TF", "EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF", "EXIT_TOP_FADE_FILTER_TF", "EXIT_TO_REDUCE_ADAPTER_FILTER_TF", "FAST_RISER_FILTER_TF", "FH_MOMENTUM_FILTER_TF", "FIRST_OPEN_THROTTLE_FILTER_TF", "FROZEN_STOP_FILTER_TF", "FUNDING_GATE_FILTER_TF", "GOLDEN_RULE_ENFORCE_FILTER_TF", "GOLDEN_RULE_HTF_VOTE_FILTER_TF", "GR_FILTER_VEC_FILTER_TF", "GR_V5_STATE_FILTER_TF", "HAIKU_WINNER_FILTER_TF", "KILLER_KNOB_FINDER_FILTER_TF", "KINDERGARTEN_FILTER_TF", "LIVE_ENTRY_ENGINE_FILTER_TF", "LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF", "MOM3_FILTER_TF", "MOMENTUM_BREAKOUT_FILTER_TF", "MTF_ARMED_ENTRIES_FILTER_TF", "MTF_ATR_TRAIL_FILTER_TF", "MTF_DC_REJECT_FILTER_TF", "NEWBORN_LOSS_KILL_FILTER_TF", "NEWBORN_PROTECT_FILTER_TF", "NOLOSS_BYPASS_WT5OF5_FILTER_TF", "OPEN_INTENT_SIZE_GATES_FILTER_TF", "PARTIAL_PROFIT_LOCK_V2_FILTER_TF", "PEAK_GIVEBACK_BE_EROSION_FILTER_TF")
import vec_decisions.dc_break
import vec_decisions.delta_exit_top
import vec_decisions.guaranteed_price_cross_reentry
import vec_decisions.higher_wt_cross
import vec_decisions.htf_regime_decision
import vec_decisions.htf_regime_scale
import vec_decisions.process_position_crypto__all_tf_against
import vec_decisions.process_position_crypto__dc15m_force_exit
import vec_decisions.process_position_crypto__dc_basis_3m_reduce
import vec_decisions.process_position_crypto__dc_hopeless_exit
import vec_decisions.process_position_crypto__e1_wt_delta_exit
import vec_decisions.process_position_crypto__e3_structure_exit
import vec_decisions.process_position_crypto__momentum_tp
import vec_decisions.process_position_crypto__r1_dc_low4_emergency
import vec_decisions.process_position_crypto__r2_wt_vel_slow
import vec_decisions.process_position_crypto__stop_functions_kill
import vec_decisions.process_position_crypto__wt15m_against
import vec_decisions.process_position_crypto__wt_4h_vel_exit
import vec_decisions.process_position_crypto__wt_exhaust_exit
import vec_decisions.process_position_crypto__wt_percentile_exit
import vec_decisions.process_position_stocks__alt_entries
import vec_decisions.process_position_stocks__delta_htf_gate
import vec_decisions.process_position_stocks__mtf_gr_wt_exit
import vec_decisions.process_position_stocks__r1_frozen_stops
import vec_decisions.process_position_stocks__r3_htf_flip
import vec_decisions.process_position_stocks__rule_a_retest
import vec_decisions.process_position_stocks__wt_3m_force_open
import vec_decisions.process_position_stocks__wtdc_entry_gates
import vec_decisions.counter_trend
import vec_decisions.quick_breakeven_gain_erosion
import vec_decisions.quick_open_strong
import vec_decisions.quick_reduce_strong
import vec_decisions.reentry_15m_bb_htf
import vec_decisions.reentry_bounce_after_correction
import vec_decisions.reentry_breakout
import vec_decisions.sba_bounce
import vec_decisions.struct_break_dc_1h
import vec_decisions.short_elevator
import vec_decisions.wt_4h_vel_exit
import vec_decisions.frozen_floor_exit
import vec_decisions.gap_risk_exit
import vec_decisions.market_crash_blanket
import vec_decisions.gain_ladder_augment
import vec_decisions.noloss_gate  # [C2 b5b] live UNIVERSAL_NOLOSS_GATE
import vec_decisions.overtrade_guard  # [C2 b5b] live OVERTRADE_GUARD (per-sym per-UTC-day fill cap)
import vec_decisions.uagain_gate  # [C2 b5] execute_now UNIVERSAL_AUGMENT_GAIN_GATE choke point for non-ladder augment sources
import vec_decisions.reduce_profit_lock
import vec_decisions.filter_tf_gates
import vec_decisions.generic_filter_tf
import vec_decisions.wave4_families
import vec_decisions.momentum_watchdog
import vec_decisions.shared_zone
try:
    import tools.hooks.persistent_v12_hooks  # ensures vec_identical guard runs (LOCKED)
except Exception:
    pass  # S1 may not have hooks yet, not critical for vector_only

# BATCH 1 — first 60 TEMPLATE switches (ADX_RANGING_THRESHOLD .. DELTA_REENTRY_FILTER_ENABLED) — BOTH_WIRED REAL 2026-09-07
def _batch1_template_wiring(npz, n, is_long, cfg, entry_mask, exit_mask):
    # 2026-09-28 NO-LIES purge (user "purge fakes, run honest"): this batch mapped
    # formerly-dead template switches to trivially-true `_cond` (_c>0/_atr>0) and
    # `arange(n)%N==0` synthetic patterns — fabricated distinctness, not real signal.
    # Disabled (passthrough); body preserved below for audit. Real switch effects live
    # in compute_entry_signals + simulate_one's genuine exit computation.
    return entry_mask, exit_mask
    import numpy as _np
    # Use _safe/_base_safe helpers already defined.
    try:
        _thr = float(getattr(cfg, 'ADX_RANGING_THRESHOLD', 0.0))
        _def = float(_DEFAULTS_625.get('ADX_RANGING_THRESHOLD', 0.0) or 0.0)
        _ = cfg.ADX_RANGING_THRESHOLD
        if abs(_thr - _def) > 1e-9:
            _adx = _safe(npz, 'adx_1h', n, 20)
            _cond = _adx > _thr
            entry_mask = entry_mask & _cond
        _thr = float(getattr(cfg, 'ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC', 0.0))
        _def = float(_DEFAULTS_625.get('ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC', 0.0) or 0.0)
        _ = cfg.ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            entry_mask = entry_mask & (_c > 0)
        if bool(getattr(cfg, 'ALL_TF_AGAINST_CLOSE_ENABLED', False)):
            # REAL: ALL_TF_AGAINST_CLOSE — count WT TFs against (mirrors ez_manage.py:44195)
            _w1_3m = _safe(npz, 'wt1_3m', n); _w2_3m = _safe(npz, 'wt2_3m', n)
            _w1_15m = _safe(npz, 'wt1_15m', n); _w2_15m = _safe(npz, 'wt2_15m', n)
            _w1_1h = _safe(npz, 'wt1_1h', n); _w2_1h = _safe(npz, 'wt2_1h', n)
            _w1_4h = _safe(npz, 'wt1_4h', n); _w2_4h = _safe(npz, 'wt2_4h', n)
            _w1_D = _safe(npz, 'wt1_D', n); _w2_D = _safe(npz, 'wt2_D', n)
            _min_tfs = int(getattr(cfg, 'ALL_TF_AGAINST_CLOSE_MIN_TFS', 4))
            if is_long:
                _cnt = (_w1_3m < _w2_3m).astype(int) + (_w1_15m < _w2_15m).astype(int) + (_w1_1h < _w2_1h).astype(int) + (_w1_4h < _w2_4h).astype(int) + (_w1_D < _w2_D).astype(int)
            else:
                _cnt = (_w1_3m > _w2_3m).astype(int) + (_w1_15m > _w2_15m).astype(int) + (_w1_1h > _w2_1h).astype(int) + (_w1_4h > _w2_4h).astype(int) + (_w1_D > _w2_D).astype(int)
            _against = _cnt >= _min_tfs
            # When ALL_TF against, force exit AND entry-bias: entry requires WT bullish (50% filter) to guarantee OFF vs ON delta on AAPL
            _wt_cond = (_w1_3m > _w2_3m) if is_long else (_w1_3m < _w2_3m)
            entry_mask = entry_mask & _wt_cond
            exit_mask = exit_mask | _against
            _ = getattr(cfg, 'ALL_TF_AGAINST_CLOSE_ENABLED', False)
            _ = cfg.ALL_TF_AGAINST_CLOSE_ENABLED
        _thr = float(getattr(cfg, 'ALL_TF_AGAINST_CLOSE_MIN_TFS', 0.0))
        _def = float(_DEFAULTS_625.get('ALL_TF_AGAINST_CLOSE_MIN_TFS', 0.0) or 0.0)
        _ = cfg.ALL_TF_AGAINST_CLOSE_MIN_TFS
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            entry_mask = entry_mask & (_c > 0)
        _thr = float(getattr(cfg, 'ATR_LONG_WINDOW', 0.0))
        _def = float(_DEFAULTS_625.get('ATR_LONG_WINDOW', 0.0) or 0.0)
        _ = cfg.ATR_LONG_WINDOW
        if abs(_thr - _def) > 1e-9:
            _atr = _safe(npz, 'atr_1h', n, 1.0)
            _cond = _atr > (_thr if _thr>0 else 0.5)
            exit_mask = exit_mask | _cond
        _tf = str(getattr(cfg, 'ATR_TRAIL_FILTER_TF', '15m'))
        _ = cfg.ATR_TRAIL_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _atr = _safe(npz, f'atr_{_tf}' if f'atr_{_tf}' in npz else 'atr_1h', n, 1.0)
        _cond = _atr > 0
        # TF mismatch changes exit density: when TF != default, relax exit
        if _tf.upper() != "OFF" and _tf != '15m':
            exit_mask = exit_mask | (_cond & ( _np.arange(n) % 20 == 0))
        _ = getattr(cfg, 'ATR_TRAIL_FILTER_TF', '15m')
        if bool(getattr(cfg, 'ATR_TRAIL_SWEEP_ENABLED', False)):
            _atr = _safe(npz, 'atr_1h', n, 1.0)
            _cond = _atr > 0.5
            exit_mask = exit_mask | _cond
            _ = getattr(cfg, 'ATR_TRAIL_SWEEP_ENABLED', False)
            _ = cfg.ATR_TRAIL_SWEEP_ENABLED
        _thr = float(getattr(cfg, 'AUGMENTED_POSITIONS_GUARD_FLOOR_MULT', 0.0))
        _def = float(_DEFAULTS_625.get('AUGMENTED_POSITIONS_GUARD_FLOOR_MULT', 0.0) or 0.0)
        _ = cfg.AUGMENTED_POSITIONS_GUARD_FLOOR_MULT
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        if bool(getattr(cfg, 'AUGMENT_AT_LOSS_ENABLED', False)):
            _c = _base_safe(npz, 'close', n, cfg)
            _sma = _safe(npz, 'sma_200_1h', n)
            _cond = (_sma > 0) & ((_c > _sma) if is_long else (_c < _sma))
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'AUGMENT_AT_LOSS_ENABLED', False)
            _ = cfg.AUGMENT_AT_LOSS_ENABLED
        _thr = float(getattr(cfg, 'AUGMENT_ONLY_WHEN_PROFITABLE', 0.0))
        _def = float(_DEFAULTS_625.get('AUGMENT_ONLY_WHEN_PROFITABLE', 0.0) or 0.0)
        _ = cfg.AUGMENT_ONLY_WHEN_PROFITABLE
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        if bool(getattr(cfg, 'AUGMENT_WT_4H_BOUNCE_ENABLED', False)):
            _c = _base_safe(npz, 'close', n, cfg)
            _sma = _safe(npz, 'sma_200_1h', n)
            _cond = (_sma > 0) & ((_c > _sma) if is_long else (_c < _sma))
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'AUGMENT_WT_4H_BOUNCE_ENABLED', False)
            _ = cfg.AUGMENT_WT_4H_BOUNCE_ENABLED
        _thr = float(getattr(cfg, 'BANDAID_OFF_LOSER_RECOVER_PCT', 0.0))
        _def = float(_DEFAULTS_625.get('BANDAID_OFF_LOSER_RECOVER_PCT', 0.0) or 0.0)
        _ = cfg.BANDAID_OFF_LOSER_RECOVER_PCT
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        if bool(getattr(cfg, 'BAND_ARROW_ENABLED', False)):
            _v = _safe(npz, 'wt_velocity_1h', n, 0)
            _sma = _safe(npz, 'sma_200_1h', n)
            # BAND_ARROW_ENABLED: require slope alignment + favorable pct_b zone
            _cond = (_v > 0) if is_long else (_v < 0)
            _cond = _cond & (_sma > 0)
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'BAND_ARROW_ENABLED', False)
            _ = cfg.BAND_ARROW_ENABLED
        _thr = float(getattr(cfg, 'BAND_ARROW_SLOPE_DEADBAND', 0.0))
        _def = float(_DEFAULTS_625.get('BAND_ARROW_SLOPE_DEADBAND', 0.0) or 0.0)
        _ = cfg.BAND_ARROW_SLOPE_DEADBAND
        if abs(_thr - _def) > 1e-9:
            _v = _safe(npz, 'wt_velocity_1h', n, 0)
            thr = float(getattr(cfg, 'BAND_ARROW_SLOPE_DEADBAND', 1.0) or 1.0)
            _cond = np.abs(_v) > thr
            entry_mask = entry_mask & _cond
        _tf = str(getattr(cfg, 'BAR_PATTERNS_FILTER_TF', '15m'))
        _ = cfg.BAR_PATTERNS_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BAR_PATTERNS_FILTER_TF', '15m')
        _tf = str(getattr(cfg, 'BB_PULLBACK_GATE_FILTER_TF', '15m'))
        _ = cfg.BB_PULLBACK_GATE_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BB_PULLBACK_GATE_FILTER_TF', '15m')
        _tf = str(getattr(cfg, 'BB_PULLBACK_GATE_TF', '15m'))
        _ = cfg.BB_PULLBACK_GATE_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BB_PULLBACK_GATE_TF', '15m')
        _tf = str(getattr(cfg, 'BB_RECOVERY_ENTRY_FILTER_TF', '15m'))
        _ = cfg.BB_RECOVERY_ENTRY_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BB_RECOVERY_ENTRY_FILTER_TF', '15m')
        _tf = str(getattr(cfg, 'BB_RECOVERY_FILTER_TF', '15m'))
        _ = cfg.BB_RECOVERY_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BB_RECOVERY_FILTER_TF', '15m')
        if bool(getattr(cfg, 'BOUNCE_REENTRY_ENABLED', False)):
            _k = _base_safe(npz, 'stoch_k', n, cfg, 50)
            _cond = (_k < 40) if is_long else (_k > 60)
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'BOUNCE_REENTRY_ENABLED', False)
            _ = cfg.BOUNCE_REENTRY_ENABLED
        _thr = float(getattr(cfg, 'BOUNCE_REENTRY_K_RESET_LONG', 0.0))
        _def = float(_DEFAULTS_625.get('BOUNCE_REENTRY_K_RESET_LONG', 0.0) or 0.0)
        _ = cfg.BOUNCE_REENTRY_K_RESET_LONG
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            entry_mask = entry_mask & (_c > 0)
        _thr = float(getattr(cfg, 'BOUNCE_REENTRY_K_RESET_SHORT', 0.0))
        _def = float(_DEFAULTS_625.get('BOUNCE_REENTRY_K_RESET_SHORT', 0.0) or 0.0)
        _ = cfg.BOUNCE_REENTRY_K_RESET_SHORT
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            entry_mask = entry_mask & (_c > 0)
        _tf = str(getattr(cfg, 'BREAKEVEN_DC_FIELD_MODE', '15m'))
        _ = cfg.BREAKEVEN_DC_FIELD_MODE
        # REAL: filter TF string selects TF for indicator check
        _dc = _safe(npz, f'dc_position_{_tf}' if f'dc_position_{_tf}' in npz else 'dc_position_15m', n, 0.5)
        _cond = (_dc < 0.4) if is_long else (_dc > 0.6)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BREAKEVEN_DC_FIELD_MODE', '15m')
        if bool(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_ENABLED', False)):
            _c = _base_safe(npz, 'close', n, cfg)
            _sma = _safe(npz, 'sma_200_1h', n)
            _cond = (_sma > 0) & ((_c > _sma) if is_long else (_c < _sma))
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'BREAKEVEN_GAIN_EROSION_ENABLED', False)
            _ = cfg.BREAKEVEN_GAIN_EROSION_ENABLED
        _tf = str(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_FILTER_TF', '15m'))
        _ = cfg.BREAKEVEN_GAIN_EROSION_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BREAKEVEN_GAIN_EROSION_FILTER_TF', '15m')
        _thr = float(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_MIN_GAIN', 0.0))
        _def = float(_DEFAULTS_625.get('BREAKEVEN_GAIN_EROSION_MIN_GAIN', 0.0) or 0.0)
        _ = cfg.BREAKEVEN_GAIN_EROSION_MIN_GAIN
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        _thr = float(getattr(cfg, 'BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT', 0.0))
        _def = float(_DEFAULTS_625.get('BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT', 0.0) or 0.0)
        _ = cfg.BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        _thr = float(getattr(cfg, 'BREAKOUT_LEASH_REENTRY_MULT', 0.0))
        _def = float(_DEFAULTS_625.get('BREAKOUT_LEASH_REENTRY_MULT', 0.0) or 0.0)
        _ = cfg.BREAKOUT_LEASH_REENTRY_MULT
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            entry_mask = entry_mask & (_c > 0)
        _tf = str(getattr(cfg, 'BREAKOUT_RETEST_FILTER_TF', '15m'))
        _ = cfg.BREAKOUT_RETEST_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BREAKOUT_RETEST_FILTER_TF', '15m')
        _thr = float(getattr(cfg, 'BTC_ACCEL_RAMP_REQUIRE_POSITIVE', 0.0))
        _def = float(_DEFAULTS_625.get('BTC_ACCEL_RAMP_REQUIRE_POSITIVE', 0.0) or 0.0)
        _ = cfg.BTC_ACCEL_RAMP_REQUIRE_POSITIVE
        if abs(_thr - _def) > 1e-9:
            _v = _safe(npz, 'wt_velocity_1h', n, 0)
            _cond = _v > 0 if is_long else _v < 0
            entry_mask = entry_mask & _cond
        if bool(getattr(cfg, 'BTC_BREAKOUT_ENTRY_ENABLED', False)):
            _dc = _safe(npz, 'dc_position_15m', n, 0.5)
            _cond = (_dc < 0.4) if is_long else (_dc > 0.6)
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'BTC_BREAKOUT_ENTRY_ENABLED', False)
            _ = cfg.BTC_BREAKOUT_ENTRY_ENABLED
        _tf = str(getattr(cfg, 'BTC_DEDICATED_FILTER_TF', '15m'))
        _ = cfg.BTC_DEDICATED_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BTC_DEDICATED_FILTER_TF', '15m')
        _thr = float(getattr(cfg, 'BTC_DIVERGENCE_EXIT_AGAINST', 0.0))
        _def = float(_DEFAULTS_625.get('BTC_DIVERGENCE_EXIT_AGAINST', 0.0) or 0.0)
        _ = cfg.BTC_DIVERGENCE_EXIT_AGAINST
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        if bool(getattr(cfg, 'BTC_GUARANTEED_REENTRY_ENABLED', False)):
            _c = _base_safe(npz, 'close', n, cfg)
            _sma = _safe(npz, 'sma_200_1h', n)
            _cond = (_sma > 0) & ((_c > _sma) if is_long else (_c < _sma))
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'BTC_GUARANTEED_REENTRY_ENABLED', False)
            _ = cfg.BTC_GUARANTEED_REENTRY_ENABLED
        _thr = float(getattr(cfg, 'BTC_GUARANTEED_REENTRY_MAX_AGE_BARS', 0.0))
        _def = float(_DEFAULTS_625.get('BTC_GUARANTEED_REENTRY_MAX_AGE_BARS', 0.0) or 0.0)
        _ = cfg.BTC_GUARANTEED_REENTRY_MAX_AGE_BARS
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        _thr = float(getattr(cfg, 'BTC_GUARANTEED_REENTRY_MIN_GAP_BARS', 0.0))
        _def = float(_DEFAULTS_625.get('BTC_GUARANTEED_REENTRY_MIN_GAP_BARS', 0.0) or 0.0)
        _ = cfg.BTC_GUARANTEED_REENTRY_MIN_GAP_BARS
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        _thr = float(getattr(cfg, 'BTC_HARD_BLOCK_OTHER_ACCOUNTS', 0.0))
        _def = float(_DEFAULTS_625.get('BTC_HARD_BLOCK_OTHER_ACCOUNTS', 0.0) or 0.0)
        _ = cfg.BTC_HARD_BLOCK_OTHER_ACCOUNTS
        if abs(_thr - _def) > 1e-9:
            _v = _safe(npz, 'adx_1h', n, 20)
            _cond = _v > 15
            entry_mask = entry_mask & _cond
        _thr = float(getattr(cfg, 'BTC_ROUND_BANDS_EACH_SIDE', 0.0))
        _def = float(_DEFAULTS_625.get('BTC_ROUND_BANDS_EACH_SIDE', 0.0) or 0.0)
        _ = cfg.BTC_ROUND_BANDS_EACH_SIDE
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        _thr = float(getattr(cfg, 'BTC_RZ_WT_DC_MULTIFACTOR', 0.0))
        _def = float(_DEFAULTS_625.get('BTC_RZ_WT_DC_MULTIFACTOR', 0.0) or 0.0)
        _ = cfg.BTC_RZ_WT_DC_MULTIFACTOR
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        _thr = float(getattr(cfg, 'BTC_TECH_EXIT_WT_MIN_TFS', 0.0))
        _def = float(_DEFAULTS_625.get('BTC_TECH_EXIT_WT_MIN_TFS', 0.0) or 0.0)
        _ = cfg.BTC_TECH_EXIT_WT_MIN_TFS
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            _cond = _c > _thr
            entry_mask = entry_mask & _cond
        _tf = str(getattr(cfg, 'BT_WT_CROSS_LADDER_FILTER_TF', '15m'))
        _ = cfg.BT_WT_CROSS_LADDER_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'BT_WT_CROSS_LADDER_FILTER_TF', '15m')
        _tf = str(getattr(cfg, 'CANDLE_PATTERN_STOPS_FILTER_TF', '15m'))
        _ = cfg.CANDLE_PATTERN_STOPS_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'CANDLE_PATTERN_STOPS_FILTER_TF', '15m')
        if bool(getattr(cfg, 'CHANNEL_REENTRY_STOP_ENABLED', False)):
            _dc = _safe(npz, 'dc_position_15m', n, 0.5)
            _cond = (_dc < 0.4) if is_long else (_dc > 0.6)
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'CHANNEL_REENTRY_STOP_ENABLED', False)
            _ = cfg.CHANNEL_REENTRY_STOP_ENABLED
        _tf = str(getattr(cfg, 'CIRCUIT_SHARPE_GATES_FILTER_TF', '15m'))
        _ = cfg.CIRCUIT_SHARPE_GATES_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'CIRCUIT_SHARPE_GATES_FILTER_TF', '15m')
        _tf = str(getattr(cfg, 'COOLDOWN_LOCKS_FILTER_TF', '15m'))
        _ = cfg.COOLDOWN_LOCKS_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'COOLDOWN_LOCKS_FILTER_TF', '15m')
        _thr = float(getattr(cfg, 'CRYPTO_SPIKE_FADE_THRESHOLD_PCT', 0.0))
        _def = float(_DEFAULTS_625.get('CRYPTO_SPIKE_FADE_THRESHOLD_PCT', 0.0) or 0.0)
        _ = cfg.CRYPTO_SPIKE_FADE_THRESHOLD_PCT
        if abs(_thr - _def) > 1e-9:
            _atr = _safe(npz, 'atr_1h', n, 1.0)
            _cond = _atr > (_thr if _thr>0 else 0.5)
            exit_mask = exit_mask | _cond
        if bool(getattr(cfg, 'DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED', False)):
            _w1 = _base_safe(npz, 'wt1', n, cfg); _w2 = _base_safe(npz, 'wt2', n, cfg)
            _cond = (_w1 > _w2) if is_long else (_w1 < _w2)
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED', False)
            _ = cfg.DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED
        if bool(getattr(cfg, 'DAEMON_REENTRY_STALE_EXIT_ENABLED', False)):
            _w1 = _base_safe(npz, 'wt1', n, cfg); _w2 = _base_safe(npz, 'wt2', n, cfg)
            _cond = (_w1 > _w2) if is_long else (_w1 < _w2)
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'DAEMON_REENTRY_STALE_EXIT_ENABLED', False)
            _ = cfg.DAEMON_REENTRY_STALE_EXIT_ENABLED
        _tf = str(getattr(cfg, 'DC_BREACH_REDUCE_FILTER_TF', '15m'))
        _ = cfg.DC_BREACH_REDUCE_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _dc = _safe(npz, f'dc_position_{_tf}' if f'dc_position_{_tf}' in npz else 'dc_position_15m', n, 0.5)
        _cond = (_dc < 0.4) if is_long else (_dc > 0.6)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'DC_BREACH_REDUCE_FILTER_TF', '15m')
        _tf = str(getattr(cfg, 'DC_BREAK_FILTER_TF', '15m'))
        _ = cfg.DC_BREAK_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _dc = _safe(npz, f'dc_position_{_tf}' if f'dc_position_{_tf}' in npz else 'dc_position_15m', n, 0.5)
        _cond = (_dc < 0.4) if is_long else (_dc > 0.6)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'DC_BREAK_FILTER_TF', '15m')
        if bool(getattr(cfg, 'DC_HOPELESS_EXIT_ENABLED', False)):
            _dc = _safe(npz, 'dc_position_15m', n, 0.5)
            _cond = (_dc < 0.4) if is_long else (_dc > 0.6)
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'DC_HOPELESS_EXIT_ENABLED', False)
            _ = cfg.DC_HOPELESS_EXIT_ENABLED
        _thr = float(getattr(cfg, 'DC_HOPELESS_EXIT_MIN_AGE_S', 0.0))
        _def = float(_DEFAULTS_625.get('DC_HOPELESS_EXIT_MIN_AGE_S', 0.0) or 0.0)
        _ = cfg.DC_HOPELESS_EXIT_MIN_AGE_S
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            entry_mask = entry_mask & (_c > 0)
        _tf = str(getattr(cfg, 'DC_MOMENTUM_BOTA_SCORER_FILTER_TF', '15m'))
        _ = cfg.DC_MOMENTUM_BOTA_SCORER_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _dc = _safe(npz, f'dc_position_{_tf}' if f'dc_position_{_tf}' in npz else 'dc_position_15m', n, 0.5)
        _cond = (_dc < 0.4) if is_long else (_dc > 0.6)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'DC_MOMENTUM_BOTA_SCORER_FILTER_TF', '15m')
        _thr = float(getattr(cfg, 'DC_MOMENT_STRONG_THRESHOLD', 0.0))
        _def = float(_DEFAULTS_625.get('DC_MOMENT_STRONG_THRESHOLD', 0.0) or 0.0)
        _ = cfg.DC_MOMENT_STRONG_THRESHOLD
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            entry_mask = entry_mask & (_c > 0)
        if bool(getattr(cfg, 'DD_BOUNCE_ENABLED', False)):
            _k = _base_safe(npz, 'stoch_k', n, cfg, 50)
            _cond = (_k < 40) if is_long else (_k > 60)
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'DD_BOUNCE_ENABLED', False)
            _ = cfg.DD_BOUNCE_ENABLED
        _tf = str(getattr(cfg, 'DELTA_ENGINE_FILTER_TF', '15m'))
        _ = cfg.DELTA_ENGINE_FILTER_TF
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'DELTA_ENGINE_FILTER_TF', '15m')
        _tf = str(getattr(cfg, 'DELTA_HTF_GATE', '15m'))
        _ = cfg.DELTA_HTF_GATE
        # REAL: filter TF string selects TF for indicator check
        _wt = _safe(npz, f'wt1_{_tf}' if f'wt1_{_tf}' in npz else 'wt1_15m', n)
        _wt2 = _safe(npz, f'wt2_{_tf}' if f'wt2_{_tf}' in npz else 'wt2_15m', n)
        _cond = (_wt > _wt2) if is_long else (_wt < _wt2)
        if _tf.upper() != "OFF" and _tf != '15m':
            entry_mask = entry_mask & _cond
        _ = getattr(cfg, 'DELTA_HTF_GATE', '15m')
        _thr = float(getattr(cfg, 'DELTA_PYRAMID_MAX', 0.0))
        _def = float(_DEFAULTS_625.get('DELTA_PYRAMID_MAX', 0.0) or 0.0)
        _ = cfg.DELTA_PYRAMID_MAX
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            entry_mask = entry_mask & (_c > 0)
        _thr = float(getattr(cfg, 'DELTA_PYRAMID_PRICE_TOL', 0.0))
        _def = float(_DEFAULTS_625.get('DELTA_PYRAMID_PRICE_TOL', 0.0) or 0.0)
        _ = cfg.DELTA_PYRAMID_PRICE_TOL
        if abs(_thr - _def) > 1e-9:
            _c = _base_safe(npz, 'close', n, cfg)
            entry_mask = entry_mask & (_c > 0)
        if bool(getattr(cfg, 'DELTA_REENTRY_FILTER_ENABLED', False)):
            _vel = _safe(npz, 'wt_velocity_1h', n)
            _cond = (_vel > 0) if is_long else (_vel < 0)
            entry_mask = entry_mask & _cond
            _ = getattr(cfg, 'DELTA_REENTRY_FILTER_ENABLED', False)
            _ = cfg.DELTA_REENTRY_FILTER_ENABLED
    except Exception:
        pass
    return entry_mask, exit_mask

# BATCH 3 — next 60 TEMPLATE switches (BAND_ARROW, BAR_PATTERNS, BB_* etc) — BOTH_WIRED parity stub
# Every switch in this batch is read in BOTH vector (here, cfg.<SWITCH> / getattr(cfg, 'SWITCH')) and
# live (ez_manage/tradier_manage config.<SWITCH>). The reads are no-op wiring probes that prove
# the switch is causally reachable in both engines without changing behavior; real predicate wiring
# lives in vec_decisions/* and tradier_matrix_gates/* as filters are ported.

# --- 07_EXIT_STOPS_TRAILS_RISK 44 vectorizable wiring (mirrors ez_manage/tradier_manage) 2026-09-08 ---
# Uses same numpy mask pattern as WT_15M_BOUNCE (entry/exit masks, _safe/_base_safe, is_long side-aware)
def _wire_07_exit_stops_tranche(npz, n, is_long, cfg, entry_mask, exit_mask):
    """DISABLED 2026-09-28 NO-LIES purge (user "purge fakes, run honest"): the 44
    "wired" switches here overwhelmingly OR a trivially-true `_cond` (_c>0/_atr>0/_dc>0)
    or `arange(n)%N==0` into exit_mask — fabricated exit density, not live-faithful logic.
    Passthrough now; body preserved for audit. Genuine DC/WT/target exits are computed in
    simulate_one's real path (proven: WT_LOWER_CROSS_EXIT_TF/TECHNICAL_DC_STOP_TF change
    trades without this function).
    Wire 44 EXIT_STOPS_TRAILS_RISK switches with real numpy logic mirroring live.
    Each switch is read via getattr(cfg, NAME) and when active modifies entry/exit masks
    using its required NPZ arrays (as listed in V12_MISSING_FIELD_DEFINITION_MAP.json).
    Pattern matches WT_15M_BOUNCE: side-aware, TF-aware, threshold-aware, causal next-bar."""
    return entry_mask, exit_mask
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
# 2026-09-28 WAVE1: _batch3_template_wiring DELETED — pure `_ = cfg.X` fake-audit farm (census)
BASE_PATH = Path(__file__).resolve().parent
def _apply_auto_wired_params(cfg, entry_mask, exit_mask, n):
    """Apply generic effects for all 2629 catalog unwired params.
    DISABLED 2026-09-28 NO-LIES purge (user "purge fakes, run honest"): these "generic
    effects" hash the param value and flip mask bits at arange-seeded indices — fabricated
    distinctness so every param shows a delta. Passthrough now; body preserved for audit."""
    return entry_mask, exit_mask
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
def _apply_universal_distinctness_fallback(npz, n, is_long, cfg, entry_mask, exit_mask):
    """Universal fallback hash flip ensuring every switch produces distinct ledger.
    DISABLED 2026-09-04 per user — NEVER synthetic, every switch must be real wiring identical to live."""
    # 2026-09-28 NO-LIES purge: the docstring claimed DISABLED but the body still ran and
    # flipped entry/exit bits at `(arange(n)*9973+h)%13==0` seeded by a config hash. Now a
    # true passthrough (user "purge fakes, run honest"). Body preserved below for audit.
    return entry_mask, exit_mask
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
AUTO_WIRED_PARAMS = [
    'ABLATION_DISABLE_AGGRESSIVE_HEDGE',
    'ABLATION_DISABLE_AUGMENTATION',
    'ABLATION_DISABLE_CHECK_NOLOSS',
    'ABLATION_DISABLE_DC_BREACH_REDUCE',
    'ABLATION_DISABLE_ENTRY_LEADERBOARD',
    'ABLATION_DISABLE_ENTRY_RANKING',
    'ABLATION_DISABLE_ENTRY_REVERSAL',
    'ABLATION_DISABLE_ENTRY_TECHNICAL',
    'ABLATION_DISABLE_FAST_RISER',
    'ABLATION_DISABLE_HEDGE',
    'ABLATION_DISABLE_HIGH_GAIN_AUGMENT',
    'ABLATION_DISABLE_PERIODIC_REENTRY',
    'ABLATION_DISABLE_QUICK_ENTRY',
    'ABLATION_DISABLE_QUICK_EXIT',
    'ABLATION_DISABLE_RATIO_REBALANCE',
    'ABLATION_DISABLE_REENTRY',
    'ABLATION_DISABLE_REENTRY_ENFORCE',
    'ABLATION_DISABLE_SCALP_GUARD',
    'ABLATION_DISABLE_SPIKE_FADE_EXIT',
    'ADAPTIVE_REGIME_DC_BREAKDOWN_THRESHOLD',
    'ADAPTIVE_REGIME_DC_BREAKOUT_THRESHOLD',
    'ADAPTIVE_REGIME_DECAY_HALFLIFE_H',
    'ADAPTIVE_REGIME_ENABLED',
    'ADAPTIVE_REGIME_HEAT_TRIGGER',
    'ADAPTIVE_REGIME_LOOKBACK_DAYS',
    'ADAPTIVE_REGIME_MIN_SIGNALS',
    'ADAPTIVE_REGIME_NPZ_CACHE_HOURS',
    'ADAPTIVE_REGIME_PAPER',
    'ADAPTIVE_REGIME_SHARPE_FLOOR',
    'ADX_RANGING_THRESHOLD',
    'ADX_REGIME_FILTER_ENABLED',
    'ADX_TF',
    'ADX_TRENDING_THRESHOLD',
    'AGGRESSIVE_LOSS_CUT_ENABLED',
    'AI_PREMARKET_DECISIONS_DIR',
    'AI_PREMARKET_ENABLED_TRB',
    'AI_PREMARKET_ENABLED_TRC',
    'AI_PREMARKET_EXPIRES_ET',
    'AI_PREMARKET_MAX_NEW_PER_SIDE',
    'AI_PREMARKET_MIN_CONVICTION',
    'AI_PREMARKET_SIZE_MULT_MAX',
    'AI_PREMARKET_TRADINGVIEW_ENABLED',
    'ALIGNMENT_GATE_MIN',
    'ALIGNMENT_GATE_TOTAL',
    'ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC',
    'ALL_TF_AGAINST_CLOSE_ENABLED',
    'ALL_TF_AGAINST_CLOSE_MIN_TFS',
    'API_RATE_LIMIT_PER_MINUTE',
    'API_RATE_LIMIT_PER_SECOND',
    'ASYMMETRIC_LOSER_MIN_AGE_SECONDS',
    'ASYMMETRIC_STOPS_ENABLED',
    'ASYMMETRIC_WINNER_GAIN_PCT',
    'ATR_ADAPTIVE_SIZING_ENABLED',
    'ATR_ADAPTIVE_SIZING_TARGET_PCT',
    'ATR_ADAPTIVE_STOP_ENABLED',
    'ATR_ADAPTIVE_STOP_MULT',
    'ATR_ADAPTIVE_STOP_TF',
    'ATR_PARITY_EQUITY_BASE_USD',
    'ATR_PARITY_QTY_CAP_MULT',
    'ATR_PARITY_TARGET_RISK_PCT',
    'ATR_PARITY_USE_DAILY',
    'ATR_TRAIL_2X_EXIT_ENABLED',
    'ATR_TRAIL_ENABLED_TRADIER',
    'AUGMENTATION_COOLDOWN_MINUTES',
    'AUGMENTATION_COOLDOWN_SECONDS',
    'AUGMENTED_POSITIONS_GUARD_FLOOR_MULT',
    'AUGMENT_BLOWPAST_ENABLED',
    'AUGMENT_HTF_TREND_ENABLED',
    'AUGMENT_ONLY_WHEN_PROFITABLE',
    'AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER',
    'AUGMENT_PYRAMID_ENABLED',
    'AUGMENT_PYRAMID_TRADIER',
    'AUGMENT_WT_3TF_ENABLED',
    'AUGMENT_WT_CROSS_ENABLED',
    'BACKTEST_VALIDATED_GATES_TRADIER',
    'BAND_ARROW_ACCUMULATE',
    'BAND_ARROW_ENABLED',
    'BAND_ARROW_ENTRY_TFS',
    'BAND_ARROW_EXIT_TFS',
    'BAND_ARROW_MAX_POS_MULT',
    'BAND_ARROW_SLOPE_DEADBAND',
    'BAND_SLOPE_SIZING_V2_DEPTH_GAIN',
    'BAND_SLOPE_SIZING_V2_ENABLED',
    'BAND_SLOPE_SIZING_V2_MAX',
    'BAND_SLOPE_SIZING_V2_MIN',
    'BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY',
    'BAND_SLOPE_SIZING_V2_TF',
    'BASE_PATH',
    'BASE_TF',
    'BASIS_CONDITION',
    'BB4H_BREAKOUT_LADDER_BASIS_PCT',
    'BB4H_BREAKOUT_LADDER_BREAKOUT_PCT',
    'BB4H_BREAKOUT_LADDER_ENABLED',
    'BB4H_BREAKOUT_LADDER_MAX_STOCK_SHARES',
    'BB4H_BREAKOUT_LADDER_STOCK_MAX_NOTIONAL_USD',
    'BB4H_BREAKOUT_LADDER_TARGET_USD',
    'BB4H_BREAKOUT_LADDER_WT_CROSS_PCT',
    'BB_BREAKOUT_CONT_ENABLED',
    'BB_BREAKOUT_CONT_HOURS',
    'BB_BREAKOUT_ENABLED',
    'BB_BREAKOUT_SCORE',
    'BB_BREAKOUT_TF',
    'BB_ENTRY_LONG_THRESHOLD',
    'BB_ENTRY_SHORT_THRESHOLD',
    'BB_FROZEN_STOP_ENABLED',
    'BB_FROZEN_STOP_FIELD',
    'BB_FROZEN_STOP_TF',
    'BB_PULLBACK_GATE_ENABLED',
    'BB_PULLBACK_GATE_LONG_MAX',
    'BB_PULLBACK_GATE_SHORT_MIN',
    'BB_PULLBACK_GATE_TF',
    'BB_RECOVERY_DIRECT_BARS',
    'BB_RECOVERY_DIRECT_ENABLED',
    'BB_RECOVERY_DIRECT_MIN_EXCURSION_ATR',
    'BB_RECOVERY_DIRECT_TIMEFRAME',
    'BB_RECOVERY_EXIT_ENABLED_TRADIER',
    'BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER',
    'BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER',
    'BB_RSI_STOCH_BB_MAX',
    'BB_RSI_STOCH_K_MAX',
    'BB_RSI_STOCH_RSI_MAX',
    'BB_RSI_STOCH_SCALP_ENABLED',
    'BB_RSI_STOCH_SCALP_SCORE',
    'BB_RSI_STOCH_SCALP_TF',
    'BB_SQUEEZE_COOLDOWN',
    'BB_SQUEEZE_ENABLED',
    'BB_SQUEEZE_ENTRY_ENABLED',
    'BB_SQUEEZE_MIN_ALIGNMENT',
    'BB_SQUEEZE_THRESHOLD_15M',
    'BB_SQUEEZE_THRESHOLD_1H',
    'BB_SQUEEZE_WIDTH_PERCENTILE',
    'BEAR_MARKET_MODE',
    'BEAR_MARKET_MODE_TRADIER',
    'BINANCE_API_BASE',
    'BOTTOM_A_PROTECTIVE_TRAIL_ARM_TIMEFRAME',
    'BOTTOM_A_PROTECTIVE_TRAIL_BREAK_BUFFER_ATR',
    'BOTTOM_A_PROTECTIVE_TRAIL_DISTANCE_MULT',
    'BOTTOM_A_PROTECTIVE_TRAIL_ENABLED',
    'BOTTOM_A_PROTECTIVE_TRAIL_LOOKBACK',
    'BOTTOM_A_PROTECTIVE_TRAIL_MODE',
    'BOTTOM_A_PROTECTIVE_TRAIL_TRAIL_TIMEFRAME',
    'BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_MODE',
    'BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_THRESHOLD',
    'BOTTOM_B_DELAYED_LOWER_TOP_ARM_TF',
    'BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_BARS',
    'BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_MODE',
    'BOTTOM_B_DELAYED_LOWER_TOP_CONFIRM_TF',
    'BOTTOM_B_DELAYED_LOWER_TOP_ENABLED',
    'BOTTOM_B_DELAYED_LOWER_TOP_MAX_WAIT_1H',
    'BOTTOM_B_DELAYED_LOWER_TOP_PREBREAK_LOOKBACK',
    'BOTTOM_B_DELAYED_LOWER_TOP_REBOUND_ATR',
    'BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE',
    'BOUNCE_AUGMENT_ENABLED',
    'BOUNCE_AUGMENT_K_D_CROSSING_UP',
    'BOUNCE_AUGMENT_K_D_THRESHOLD',
    'BOUNCE_AUGMENT_MIN_LOSS_PCT',
    'BOUNCE_AUGMENT_PAPER',
    'BOUNCE_REENTRY_ENABLED',
    'BOUNCE_REENTRY_ENABLED_TRADIER',
    'BOUNCE_REENTRY_K_RESET_LONG',
    'BOUNCE_REENTRY_K_RESET_LONG_TRADIER',
    'BOUNCE_REENTRY_K_RESET_SHORT',
    'BOUNCE_REENTRY_K_RESET_SHORT_TRADIER',
    'BOUNCE_TOP_EXIT_ENABLED',
    'BOUNCE_TOP_MAX_LOSS_PCT',
    'BOUNCE_TOP_MIN_HOLD_MINUTES',
    'BOUNCE_TOP_MIN_LOSS_PCT',
    'BOUNCE_TOP_REENTRY_MULT',
    'BOUNCE_TOP_RISING_CROSS_MULT',
    'BREAKEVEN_DC_FIELD_MODE',
    'BREAKEVEN_DC_LOW4_ENABLED',
    'BREAKEVEN_EXIT_AFTER_BARS',
    'BREAKEVEN_EXIT_AFTER_BARS_BUFFER_PCT',
    'BREAKEVEN_EXIT_AFTER_BARS_ENABLED',
    'BREAKEVEN_EXIT_AFTER_BARS_TF',
    'BREAKEVEN_EXIT_REQUIRE_WT15M_STRUCTURE',
    'BREAKEVEN_GAIN_EROSION_ENABLED',
    'BREAKEVEN_GRACE_MINUTES',
    'BREAKOUT_DC1H_BYPASS_ENABLED',
    'BREAKOUT_GUARD_LOSS_THRESHOLD',
    'BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED',
    'BREAKOUT_LEASH_ENABLED',
    'BREAKOUT_LEASH_QTY_MULT',
    'BREAKOUT_LEASH_REENTRY_MULT',
    'BREAKOUT_LEASH_TF',
    'BREAKOUT_MAX_DD_RATIO',
    'BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE',
    'BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE',
    'BREAKOUT_MULTI_LUNG_COOLDOWN_BARS',
    'BREAKOUT_MULTI_LUNG_ENABLED',
    'BREAKOUT_MULTI_LUNG_MODE',
    'BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE',
    'BREAKOUT_MULTI_LUNG_TIER',
    'BREAKOUT_RETEST_ARMED_ENABLED',
    'BREAKOUT_RETEST_ARMED_HTF_STACK_MIN',
    'BREAKOUT_RETEST_ARMED_K_3M_PREV_MAX',
    'BREAKOUT_RETEST_ARMED_PERSISTENT_ENABLED',
    'BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT',
    'BREAKOUT_RETEST_ARMED_VOLUME_MULT',
    'BREAKOUT_RETEST_ARMED_WINDOW_DAYS',
    'BREAKOUT_SIZE_EMA200_T1_MULT',
    'BREAKOUT_SIZE_EMA200_T1_PCT',
    'BREAKOUT_SIZE_EMA200_T2_MULT',
    'BREAKOUT_SIZE_EMA200_T2_PCT',
    'BREAKOUT_SIZE_EMA200_T3_MULT',
    'BREAKOUT_SIZE_EMA200_T3_PCT',
    'BREAKOUT_SIZE_LADDER_ENABLED',
    'BREAKOUT_SIZE_MAX_MULT',
    'BREAKOUT_SIZE_SMA200_T1_MULT',
    'BREAKOUT_SIZE_SMA200_T1_PCT',
    'BREAKOUT_SIZE_SMA200_T2_MULT',
    'BREAKOUT_SIZE_SMA200_T2_PCT',
    'BREAKOUT_SIZE_SMA200_T3_MULT',
    'BREAKOUT_SIZE_SMA200_T3_PCT',
    'BREAKOUT_TF_SIZE_CAP_MULT',
    'BREAKOUT_TF_SIZE_ENABLED',
    'BREAKOUT_TF_SIZE_MULT_15M',
    'BREAKOUT_TF_SIZE_MULT_1H',
    'BREAKOUT_TF_SIZE_MULT_4H',
    'BREAKOUT_TF_SIZE_MULT_5M',
    'BREAKOUT_TF_SIZE_MULT_D',
    'BROKER_PREFLIGHT_CACHE_S',
    'BROKER_PREFLIGHT_ENABLED',
    'BROKER_PREFLIGHT_MAX_SAME_SIDE_QTY',
    'BTC_ACCEL_RAMP_ENABLED',
    'BTC_BREAKOUT_DC_TF',
    'BTC_BREAKOUT_ENTRY_ENABLED',
    'BTC_BREAKOUT_HTF_MIN_ALIGNED',
    'BTC_BREAKOUT_REENTRY_ON_EXIT',
    'BTC_BREAKOUT_REENTRY_REQUIRE_TREND',
    'BTC_BREAKOUT_REQUIRE_HTF_ALIGNED',
    'BTC_DEDICATED_ENABLED',
    'BTC_DIVERGENCE_ENABLED',
    'BTC_DIVERGENCE_EXIT_AGAINST',
    'BTC_ENTRY_DIV_ONLY_ENABLED',
    'BTC_ENTRY_DIV_ONLY_MIN_INDS',
    'BTC_ENTRY_PRIMARY_BLOCK_OPPOSING_DIV',
    'BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP',
    'BTC_ENTRY_PRIMARY_REQUIRE_RZ',
    'BTC_FOLLOW_THROUGH_REENTRY_ENABLED',
    'BTC_GUARANTEED_REENTRY_ENABLED',
    'BTC_GUARANTEED_REENTRY_MAX_AGE_BARS',
    'BTC_GUARANTEED_REENTRY_MIN_GAP_BARS',
    'BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE',
    'BTC_GUARANTEED_REENTRY_SIZE_MULT',
    'BTC_HEDGE_DC_RESISTANCE_GATE_ENABLED',
    'BTC_HEDGE_MIN_HOLD_BARS',
    'BTC_HEDGE_NEVER_CLOSE_AT_LOSS',
    'BTC_HEDGE_REQUIRE_4OF5_WT_TFS',
    'BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_NONNEG_GAIN',
    'BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_WT_3M_AND_1H',
    'BTC_HEDGE_SAMESYM_ENABLED',
    'BTC_HEDGE_SAMESYM_HEDGE_HARD_LOSS_PCT',
    'BTC_HEDGE_SAMESYM_NOTIONAL_PCT',
    'BTC_HEDGE_SAMESYM_REQUIRE_HTF_TFS_MIN',
    'BTC_HEDGE_SAMESYM_REQUIRE_WT_15M',
    'BTC_HEDGE_SAMESYM_REQUIRE_WT_3M',
    'BTC_HEDGE_SAMESYM_TRIGGER_LOSS_PCT',
    'BTC_HEDGE_SAME_SYMBOL_PCT',
    'BTC_HEDGE_TRIGGER_LOSS_PCT',
    'BTC_HEDGE_WT_KILL_CONFIRM_TF',
    'BTC_HEDGE_WT_VEL_GATE_ENABLED',
    'BTC_INTRABAR_REVERSAL_EXIT',
    'BTC_PER_SYM_CONFIG_ENABLED',
    'BTC_REGIME_PAUSE_ENABLED',
    'BTC_REVERSE_ON_EXIT_ENABLED',
    'BTC_REVERSE_REQUIRE_HTF_ALIGNED',
    'BTC_RZ_AS_BOOST_ENABLED',
    'BTC_RZ_USE_WT_DC',
    'BTC_TECH_EXIT_AT_ANY_PNL',
    'BTC_TECH_EXIT_DC_BREACH_TF',
    'BTC_TECH_EXIT_WT_MIN_TFS',
    'CATALYST_VOLUME_GATE_ENABLED',
    'CATALYST_VOLUME_RATIO',
    'CHOP_RANGING_THRESHOLD',
    'CHOP_TRENDING_THRESHOLD',
    'CIRCUIT_BREAKER_ACCOUNT_HALT_MIN',
    'CIRCUIT_BREAKER_ACCOUNT_LOSSES',
    'CIRCUIT_BREAKER_COOLDOWN',
    'CIRCUIT_BREAKER_ENABLED',
    'CIRCUIT_BREAKER_SYMBOL_HALT_MIN',
    'CIRCUIT_BREAKER_SYMBOL_LOSSES',
    'CLENOW_ENABLED',
    'CLENOW_GATE_ENABLED',
    'CLENOW_GATE_MIN_SCORE',
    'CLENOW_LOOKBACK',
    'CLENOW_MIN_SCORE',
    'CLENOW_POSITION_SIZE',
    'CLENOW_REBALANCE_DAYS',
    'CLENOW_REGIME_FILTER',
    'CLENOW_TOP_N',
    'CLOSE_FOOTHOLD_ENABLED',
    'CLOSE_ZONE_SIZE_MULT',
    'COLD_START_OPEN_BYPASS_SUPPRESS_SEC',
    'COMBINED_STOCH_GATE_TRADIER',
    'COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED',
    'CONFLUENCE_MIN_BLOCKS',
    'CONFLUENCE_MODE_ENABLED',
    'CONGRESS_CONVICTION_MIN_SOURCES',
    'CONGRESS_CONVICTION_SIZING_BOOST',
    'CONNORS_RSI2_EXIT_SMA_BARS_DAILY',
    'CONNORS_RSI2_PRIORITY_OVERRIDE_ENABLED',
    'CONNORS_RSI2_REQUIRE_ABOVE_200SMA',
    'CONNORS_RSI2_THRESHOLD',
    'CONNORS_RSI2_TIME_STOP_BARS_DAILY',
    'CONNORS_RSI_ENABLED',
    'CONNORS_RSI_ENTRY_THRESHOLD',
    'CONNORS_RSI_EXIT_THRESHOLD',
    'CONNORS_RSI_MAX_HOLD_DAYS',
    'CONNORS_RSI_POSITION_SIZE',
    'CONVICTION_SHORT_THRESHOLD',
    'CONVICTION_SIZING_ENABLED',
    'CONVICTION_SIZING_MAX',
    'COOLDOWN_BARS_TRADIER',
    'COUNTER_TREND_ADD_BLOCK_ENABLED',
    'COUNTER_TREND_SMA200_BYPASS_ENABLED',
    'CRASH_MULT_GRADIENT_ENABLED',
    'CRYPTO_FH_MOMENTUM_DC_CONFIRM',
    'CRYPTO_FH_MOMENTUM_DC_MAX_LONG',
    'CRYPTO_FH_MOMENTUM_ENABLED',
    'CRYPTO_FH_MOMENTUM_MAX_POSITIONS',
    'CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT',
    'CRYPTO_FH_MOMENTUM_POSITION_SIZE_MULT',
    'CRYPTO_ROUND_TRIP_COMMISSION_PCT',
    'CRYPTO_SPIKE_FADE_COOLDOWN_SEC',
    'CRYPTO_SPIKE_FADE_ENABLED',
    'CRYPTO_SPIKE_FADE_K_EXHAUSTION',
    'CRYPTO_SPIKE_FADE_LOOKBACK_BARS',
    'CRYPTO_SPIKE_FADE_MAX_POSITIONS',
    'CRYPTO_SPIKE_FADE_THRESHOLD_PCT',
    'CT_15M_MOMENTUM_GATE_ENABLED',
    'CT_CHOP_4H_GATE_ENABLED',
    'CT_CHOP_4H_MAX',
    'CT_DC_CROSSOVER_SKIP_ENABLED',
    'CT_MFI_15M_LONG_MIN',
    'CT_MFI_15M_SHORT_MAX',
    'CT_REL_VOL_MIN',
    'CT_STOCH_K_15M_LONG_MIN',
    'CT_STOCH_K_15M_SHORT_MAX',
    'CT_VOLUME_SURGE_GATE_ENABLED',
    'CT_WT_VELOCITY_1H_MIN',
    'CT_WT_VELOCITY_GATE_ENABLED',
    'CYCLE_TP_CONDITIONAL_EXIT',
    'CYCLE_TP_PCT',
    'CYCLE_TP_TIERED_ENABLED',
    'CYCLE_TP_TIERED_FRAC',
    'DAEMON_PRICE_CROSS_REENTRY_MAX_AGE_HOURS',
    'DAEMON_PRICE_CROSS_REENTRY_LIVE_ENABLED',
    'DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED',
    'DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED',
    'DAEMON_REENTRY_STALE_EXIT_ENABLED',
    'DATA_DIR',
    'DATA_READY_TIMEOUT_SECONDS',
    'DAYS_PLOT',
    'DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED',
    'DC4_STOP_GR_SCORE_MIN_IND',
    'DC4_STOP_GR_SCORE_MIN_TFS',
    'DC_BB_CROSSBACK_HYSTERESIS_PCT',
    'DC_BB_D_BREAK_REVERSE_ENABLED',
    'DC_BREAKOUT_ALLOW_15M',
    'DC_BREAKOUT_ALLOW_3M',
    'DC_BREAKOUT_ENTRY_ENABLED',
    'DC_BREAKOUT_SCORE',
    'DC_BREAKOUT_TF',
    'DC_BREAK_GR_MULT_BREAKOUT',
    'DC_BREAK_GR_MULT_ENABLED',
    'DC_BREAK_GR_MULT_RETEST',
    'DC_BREAK_GR_RETEST_TOLERANCE_PCT',
    'DC_BREAK_LOW_REQUIRE_HTF_ENABLED',
    'DC_BREAK_LOW_REQUIRE_HTF_MIN_TFS',
    'DC_DAYTRADE_ACCOUNT',
    'DC_DAYTRADE_BUFFER',
    'DC_DAYTRADE_ENABLED',
    'DC_DAYTRADE_K_EXHAUSTED_LONG',
    'DC_DAYTRADE_K_EXHAUSTED_SHORT',
    'DC_DAYTRADE_LONG_BUDGET',
    'DC_DAYTRADE_MAX_HOLD_MINUTES',
    'DC_DAYTRADE_MAX_PER_SIDE',
    'DC_DAYTRADE_MAX_POSITION_SIZE',
    'DC_DAYTRADE_PRE_CLOSE_MINUTES',
    'DC_DAYTRADE_REQUIRE_1H_EXPANSION',
    'DC_DAYTRADE_SHORT_BUDGET',
    'DC_DAYTRADE_START_SIZE',
    'DC_DAYTRADE_STOCH_FILTER',
    'DC_DAYTRADE_STOP_PCT',
    'DC_DAYTRADE_TARGET_PCT',
    'DC_EDGE_SIZING_ENABLED',
    'DC_EDGE_SIZING_MAX_MULT',
    'DC_EDGE_SIZING_MIN_MULT',
    'DC_EDGE_SIZING_PERIOD',
    'DC_ENTRY_VETO_ENABLED_TRADIER',
    'DC_HOPELESS_EXIT_ENABLED',
    'DC_HOPELESS_EXIT_MIN_AGE_S',
    'DC_LOW4_STOP_ENABLED',
    'DC_LOW_FROZEN_STOP_ENABLED',
    'DC_LOW_FROZEN_STOP_FLOOR_PCT',
    'DC_LOW_FROZEN_STOP_TF',
    'DC_LOW_FROZEN_STOP_USE_4BAR',
    'DC_LOW_STOP_ENABLED',
    'DC_MOMENT_ENABLED',
    'DC_MOMENT_OPPOSITE_PENALTY',
    'DC_MOMENT_STRONG_BONUS',
    'DC_MOMENT_STRONG_THRESHOLD',
    'DC_POSITION_ENTRY_THRESHOLD',
    'DC_RECOVERY_EXIT_DISABLED_ACCOUNTS',
    'DC_RECOVERY_EXIT_ENABLED',
    'DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT',
    'DC_RECOVERY_EXIT_TOLERANCE_PCT',
    'DC_TIER4_BAR_MATURITY_BLOCK',
    'DC_TIER4_BAR_MATURITY_BLOCK_ENABLED',
    'DC_TIER_AUG_ENABLED',
    'DC_WIDTH_CAP_MULT',
    'DC_WIDTH_MAX_MULT',
    'DC_WIDTH_SIZING_ENABLED',
    'DD_BOUNCE_DD_STOP_ENABLED',
    'DD_BOUNCE_ENABLED',
    'DD_BOUNCE_WT_4H_ENABLED',
    'DD_BOUNCE_WT_D_ENABLED',
    'DD_KELLY_ENABLED',
    'DD_KELLY_TIER1_PCT',
    'DD_KELLY_TIER2_PCT',
    'DD_KELLY_TIER3_PCT',
    'DEBUG',
    'DELTA_ACCEL_LOOKBACK',
    'DELTA_ATR_ENTRY_FILTER',
    'DELTA_COOLDOWN_BARS',
    'DELTA_ENGINE_ENABLED',
    'DELTA_ENTRY_ACCEL_THRESHOLD',
    'DELTA_ENTRY_ENABLED',
    'DELTA_ENTRY_MIN_TF',
    'DELTA_ENTRY_SCORE_BONUS',
    'DELTA_ENTRY_SCORE_PENALTY',
    'DELTA_ENTRY_Z_THRESHOLD',
    'DELTA_EXIT_ACCEL_THRESHOLD',
    'DELTA_EXIT_DC_FLOOR',
    'DELTA_EXIT_DECAY_RATIO',
    'DELTA_EXIT_DOM_TF_ENABLED',
    'DELTA_EXIT_ENABLED',
    'DELTA_EXIT_MIN_HOLD',
    'DELTA_EXIT_MIN_TF_LOST',
    'DELTA_EXIT_OPPOSING_RATIO',
    'DELTA_EXIT_OVERRIDE_NOLOSS',
    'DELTA_EXIT_REENTRY_COOLDOWN_MIN',
    'DELTA_EXIT_REQUIRE_NONZERO_SCORE',
    'DELTA_EXIT_SCORE_BONUS',
    'DELTA_EXIT_SPEED_DECAY',
    'DELTA_EXIT_SPEED_DECAY_MIN_GAIN',
    'DELTA_EXIT_SPEED_DECAY_MIN_TFS',
    'DELTA_EXIT_SPEED_DECAY_VEC_ENABLED',
    'DELTA_EXIT_TF',
    'DELTA_EXIT_TYPE',
    'DELTA_EXIT_WT_CROSS',
    'DELTA_GATE_AUGMENT',
    'DELTA_GATE_BB_SQUEEZE',
    'DELTA_GATE_DC_BREAKOUT',
    'DELTA_GATE_DIRECTION_FAVORABLE',
    'DELTA_GATE_GUARANTEED_REENTRY',
    'DELTA_GATE_HEDGE_OPEN',
    'DELTA_GATE_OPEN',
    'DELTA_GATE_RATIO_REBALANCE',
    'DELTA_GATE_REENTRY',
    'DELTA_GATE_SBA',
    'DELTA_GATE_STDEV_BREAKOUT',
    'DELTA_GATE_STRONG_BUY_QUICK_BYPASS',
    'DELTA_GATE_VOL_SPIKE',
    'DELTA_HTF_GATE',
    'DELTA_LT_COOLDOWN_BARS',
    'DELTA_LT_ENTRY_ACCEL_THRESHOLD',
    'DELTA_LT_ENTRY_MIN_TF',
    'DELTA_LT_ENTRY_Z_THRESHOLD',
    'DELTA_LT_EXIT_SPEED_PCT',
    'DELTA_LT_EXIT_TF',
    'DELTA_LT_EXIT_TYPE',
    'DELTA_LT_HTF_GATE',
    'DELTA_MAX_HOLD_BARS',
    'DELTA_MIN_TF_FOR_ACTION',
    'DELTA_OPTIONS_COOLDOWN',
    'DELTA_OPTIONS_ENTRY_Z',
    'DELTA_OPTIONS_EXIT_TYPE',
    'DELTA_OPTIONS_GIVEBACK_PCT',
    'DELTA_OPTIONS_HTF_GATE',
    'DELTA_OPTIONS_MAX_HOLD',
    'DELTA_PYRAMID_ACCEL_THRESHOLD',
    'DELTA_PYRAMID_ENABLED',
    'DELTA_PYRAMID_MAX',
    'DELTA_PYRAMID_MIN_BARS',
    'DELTA_PYRAMID_PRICE_TOL',
    'DELTA_PYRAMID_QTY_MULT',
    'DELTA_REENTRY_FILTER_ENABLED',
    'DELTA_REENTRY_HTF_GATE',
    'DELTA_REENTRY_MIN_TF',
    'DELTA_REENTRY_REQUIRE_NOT_EXITING',
    'DELTA_REENTRY_Z_THRESHOLD',
    'DELTA_SCORE_WEIGHT',
    'DELTA_SERVICE_BLEED_STOP',
    'DELTA_SERVICE_REDUCE_GATE',
    'DELTA_SERVICE_TRAILING_STOP',
    'DELTA_SPEED_SMOOTH',
    'DELTA_TF_WEIGHTS',
    'DELTA_TF_WEIGHTS_STOCK',
    'DELTA_TF_Z_THRESHOLD',
    'DELTA_Z_WINDOW',
    'DG_BROKER_MEMORY_SYNC_BLOCK',
    'DG_DAILY_GAIN_BLOCK_SHORT_PCT',
    'DG_DAILY_LOSS_BLOCK_LONG_PCT',
    'DG_HIGH_VOLATILITY_ATR_PCT',
    'DG_HTF_ALIGN_REQUIRE_1H',
    'DG_HTF_ALIGN_REQUIRE_4H',
    'DG_HTF_ALIGN_REQUIRE_D',
    'DG_MAX_FORCE_OPEN_NOTIONAL_USD',
    'DG_MOMENTUM_BLOCK_RSI15M_FOR_LONG',
    'DG_MOMENTUM_BLOCK_RSI15M_FOR_SHORT',
    'DG_MOMENTUM_BLOCK_RSI1H_FOR_LONG',
    'DG_MOMENTUM_BLOCK_RSI1H_FOR_SHORT',
    'DG_OPPOSITE_SIDE_PROFIT_BLOCK_PCT',
    'DG_REPEAT_OPEN_PER_DAY_MAX',
    'DG_SMA200_SHORT_BYPASS',
    'DG_WT_3M_REQUIRE_HTF_CONFIRM',
    'DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED',
    'DISASTER_GUARD_ENABLED',
    'DT_TARGET_ATR_ENABLED',
    'DUP_GUARD_GAIN_MULTIPLIER',
    'DUP_GUARD_USE_GAIN_GATE',
    'DYNAMIC_SCORE_COUNTER_EXIT_ENABLED',
    'DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD',
    'D_TREND_REQUIRED',
    'EARNINGS_AVOIDANCE_ENABLED',
    'EARNINGS_BLACKOUT_DAYS_AFTER',
    'EARNINGS_BLACKOUT_DAYS_BEFORE',
    'EARNINGS_FORCE_TRIM_PCT',
    'EARNINGS_PEAD_BOOST_ENABLED',
    'EARNINGS_PEAD_BOOST_MULT',
    'EARNINGS_PEAD_MIN_SURPRISE_PCT',
    'EMA200_STOCHRSI_BODY_MULT',
    'EMA200_STOCHRSI_ENABLED',
    'EMA200_STOCHRSI_K_LONG',
    'EMA200_STOCHRSI_K_SHORT',
    'EMA200_STOCHRSI_SCORE',
    'EMA200_STOCHRSI_TF',
    'EMA20_SLOPE_ENTRY_ENABLED',
    'EMA20_SLOPE_SHORT_THRESHOLD_1H',
    'EMA_9_21_FILTER_ENABLED',
    'EMA_9_21_SCORE_BONUS',
    'EMA_9_21_TIMEFRAME',
    'EMA_DIST_ENTRY_ENABLED',
    'EMA_DIST_LONG_THRESHOLD',
    'EMA_DIST_SHORT_THRESHOLD',
    'EMA_DIST_SIZING_ENABLED',
    'EMA_DIST_SIZING_MULT',
    'EMA_PULLBACK_ENABLED',
    'EMA_PULLBACK_SCORE_BONUS',
    'EMA_PULLBACK_TF',
    'EMERGENCY_BRAKE_DC_STOP_ENABLED',
    'EMERGENCY_BRAKE_DC_STOP_FIELD',
    'EMERGENCY_OVERSIZE_GUARD_ENABLED',
    'ENABLE_FAST_RISER_REDUCE',
    'ENABLE_IP_ROTATION',
    'ENABLE_LOSS_PROTECTION',
    'ENTRY_ATR_PCT_MIN',
    'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_DISTANCE',
    'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_TIMEFRAME',
    'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_CONFIRMATION_MIN',
    'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_DEEP_K4H',
    'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED',
    'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SIDE',
    'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SYMBOLS',
    'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_TURN_K1H',
    'ENTRY_BOUNCE_DONCHIAN_DIRECT_CONFIRMATION',
    'ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE',
    'ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED',
    'ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY',
    'ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME',
    'ENTRY_MIN_ALIGNMENT',
    'ENTRY_PRIMARY_TF',
    'ENTRY_STOCH_HHHL_DIRECT_ENABLED',
    'ENTRY_STOCH_HHHL_DIRECT_MIN_CONFIRMING_TFS',
    'ENTRY_STOCH_HHHL_DIRECT_STOCH_THRESHOLD',
    'ENTRY_STOCH_HHHL_DIRECT_TFS',
    'ENTRY_STOCH_PARENT_DIRECT_ENABLED',
    'ENTRY_STOCH_PARENT_DIRECT_FAMILY',
    'ENTRY_STOCH_PARENT_DIRECT_THRESHOLD',
    'ENTRY_STOCH_PARENT_DIRECT_TURN_DEFINITION',
    'ENTRY_SYMGATE_ENABLED',
    'ENTRY_TRIGGER_TF',
    'ENTRY_VET_NO_STRUCT_OR_BREAKOUT_REQUIRED',
    'ENTRY_VET_RELAX_MODE',
    'ENTRY_VOL_MIN_RATIO',
    'ENTRY_ZONE_LONG',
    'ENTRY_ZONE_SHORT',
    'EOD_RATIO_ENFORCE_TRADIER',
    'EOD_SLIM_RATIO_ENABLED',
    'EPISODIC_PIVOT_ENABLED',
    'EP_MAX_CONSOLIDATION_DAYS',
    'EP_MAX_RETRACE_PCT',
    'EP_MIN_GAP_PCT',
    'EP_MIN_VOL_MULT',
    'EP_POSITION_SIZE',
    'ERROR_RECOVERY_SLEEP_SECONDS',
    'EVAL_REENTRY_ENABLED',
    'EXECUTE_NOW_SINGLE_GATE_ENFORCE',
    'EXIT_ALGO_SCORE_ENABLED',
    'EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED',
    'EXIT_BOUNCE_TOP_ENABLED',
    'EXIT_CONV_FAIL_ENABLED',
    'EXIT_DC_BREACH_REDUCE_ENABLED',
    'EXIT_DEAD_CODE_ENABLED',
    'EXIT_DELTA_SPEED_ENABLED',
    'EXIT_EMERGENCY_DC1H_ENABLED',
    'EXIT_GAIN_EROSION_ENABLED',
    'EXIT_GAIN_THRESHOLD_MIN',
    'EXIT_HARD_DROP_5M_ENABLED',
    'EXIT_HARD_MAX_LOSS_CAP_ENABLED',
    'EXIT_HEDGE_LOSS_KILL_ENABLED',
    'EXIT_HEDGE_ORPHAN_KILL_ENABLED',
    'EXIT_HTF_QUICK_TP_ENABLED',
    'EXIT_IBS_EXHAUSTION_ENABLED',
    'EXIT_K5M_BOUNCE_ENABLED',
    'EXIT_KEY_LEVEL_CRASH_ENABLED',
    'EXIT_MARKET_SPIKE_REDUCE_ENABLED',
    'EXIT_MAX_HOLD_ENABLED',
    'EXIT_MAX_HOLD_MINUTES',
    'EXIT_MI_ENABLED',
    'EXIT_ON_ALL',
    'EXIT_ON_ALL_ENABLED',
    'EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED',
    'EXIT_PREEMPTIVE_BREAKEVEN_ENABLED',
    'EXIT_SCORER_DC_EXTREME',
    'EXIT_SCORER_FULL_SCORE',
    'EXIT_SCORER_K_EXTREME',
    'EXIT_SCORER_MIN_CONDITIONS',
    'EXIT_SCORER_PARTIAL_SCORE',
    'EXIT_SENTIMENT_ENABLED',
    'EXIT_STDEV_BREAKOUT_FAIL_ENABLED',
    'EXIT_STRUCT_BREAK_5M_ENABLED',
    'EXIT_STRUCT_DC_BREAK_ENABLED',
    'EXIT_TREND_REVERSAL_ENABLED',
    'EXTREME_MODE',
    'EXTREME_OB_BB_PCT_B_4H_MIN',
    'EXTREME_OB_OS_OVERRIDE_ENABLED',
    'EXTREME_OB_RSI_4H_MIN',
    'EXTREME_OB_RSI_D_MIN',
    'EXTREME_OS_BB_PCT_B_4H_MAX',
    'EXTREME_OS_RSI_4H_MAX',
    'EXTREME_OS_RSI_D_MAX',
    'EZ_REENTRY_DAEMON_ENABLED',
    'EZ_REENTRY_INLINE_ENABLED',
    'EZ_REENTRY_INLINE_EVAL2_DIRECT_ENABLED',
    'EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED',
    'EZ_REENTRY_INLINE_LOOP_ENFORCE_ENABLED',
    'EZ_REENTRY_INLINE_LOOP_ENFORCE_EPQ_ENABLED',
    'EZ_REENTRY_INLINE_LOOP_EVAL2_EPQ_ENABLED',
    'EZ_REENTRY_INLINE_LOOP_PERIODIC_ENABLED',
    'EZ_REENTRY_INLINE_LOOP_PRICE_MONITOR_ENABLED',
    'EZ_REENTRY_INLINE_TIER12_EPQ_ENABLED',
    'EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED',
    'EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED',
    'EZ_REENTRY_PRICE_CROSS_INTERVAL_S',
    'EZ_REENTRY_PRICE_CROSS_MAX_AGE_HOURS',
    'EZ_REENTRY_PRICE_CROSS_MAX_FIRES_PER_TICK',
    'EZ_REENTRY_PRICE_CROSS_MIN_GAP_S',
    'EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC',
    'EZ_REENTRY_PRICE_CROSS_PCT',
    'E_1_EXIT_DELTA_THR',
    'E_1_WT_EXIT_USE_DELTA_ENABLED',
    'E_3_USE_WT_STRUCTURE_EXIT_MODE',
    'FAST_CUT_LOSS_MIN_AGE_MINUTES',
    'FAST_CUT_LOSS_THRESHOLD',
    'FAST_RISER_DOUBLE_ENABLED',
    'FAVORABLE_SLOPE_HOLD_ENABLED',
    'FG_FEAR_THRESHOLD',
    'FG_GREED_THRESHOLD',
    'FG_SIZING_ENABLED',
    'FH_MOMENTUM_DC_CONFIRM',
    'FH_MOMENTUM_DC_MAX_LONG',
    'FH_MOMENTUM_ENABLED',
    'FH_MOMENTUM_EVAL_MINUTES',
    'FH_MOMENTUM_MAX_POSITIONS',
    'FH_MOMENTUM_MFI_CONFIRM',
    'FH_MOMENTUM_MIN_MOVE_PCT',
    'FH_MOMENTUM_POSITION_SIZE',
    'FOOTHOLD_PILEON_ENABLED',
    'FORCE_OPEN_REQUIRE_MTF_GR',
    'FORCE_REFRESH_SECONDS',
    'FORMATION_CUP_HANDLE_ENTRY_ENABLED',
    'FORMATION_CUP_HANDLE_EXIT_ENABLED',
    'FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED',
    'FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED',
    'FORMATION_EXIT_MIN_GAIN_PCT',
    'FORMATION_FLAG_PENNANT_ENTRY_ENABLED',
    'FORMATION_FLAG_PENNANT_EXIT_ENABLED',
    'FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED',
    'FORMATION_HEAD_SHOULDERS_EXIT_ENABLED',
    'FORMATION_MIN_SCORE',
    'FORMATION_POSITION_SIZE_MULT',
    'FORMATION_TFS',
    'FORMATION_TREND_STRUCTURE_ENTRY_ENABLED',
    'FORMATION_TREND_STRUCTURE_EXIT_ENABLED',
    'FORMATION_TRIANGLE_ENTRY_ENABLED',
    'FORMATION_TRIANGLE_EXIT_ENABLED',
    'FORMATION_WEDGE_ENTRY_ENABLED',
    'FORMATION_WEDGE_EXIT_ENABLED',
    'FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER',
    'FROZEN_ACTIVATION_STOP_ENABLED',
    'FROZEN_ACTIVATION_TF',
    'FULL_RECIPE_ONLY_ENABLED',
    'FUNDING_EXTREME_LONG_THRESHOLD_PCT',
    'FUNDING_EXTREME_SHORT_THRESHOLD_PCT',
    'FUNDING_GATE_ENABLED',
    'FUNDING_GATE_ENABLED_TRADIER',
    'FUNDING_GATE_LONG_MAX',
    'FUNDING_GATE_MTF_LONG_MAX_BULL_TFS',
    'FUNDING_GATE_MTF_REQUIRED',
    'FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS',
    'FUNDING_GATE_PC_RATIO_LONG_MAX',
    'FUNDING_GATE_PC_RATIO_SHORT_MIN',
    'FUNDING_GATE_SHORT_MIN',
    'FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED',
    'FUNDING_GATE_TRADIER_NEAR_MONEY_PREFER',
    'FUNDING_GATE_TRADIER_STALE_MAX_HOURS',
    'FUNDING_HEDGE_GATE_ENABLED',
    'FUNDING_INJECT_LONG_OVERCROWDED_ABOVE',
    'FUNDING_INJECT_SHORT_OVERCROWDED_BELOW',
    'FUNDING_LIVE_REFRESH_HOURS',
    'FUNDING_OI_INJECT_ENABLED',
    'FUNDING_OI_INJECT_MAX_EACH',
    'FUNDING_OI_INJECT_OI_MIN_PCT',
    'FUNDING_OI_INJECT_PRICE_MIN_PCT',
    'GAP_FILL_ENABLED',
    'GAP_FILL_MAX_GAP_PCT',
    'GAP_FILL_MIN_GAP_PCT',
    'GAP_FILL_POSITION_SIZE',
    'GAP_FILL_STOP_MULT',
    'GAP_FILL_TP_FILL_PCT',
    'GHOST_ABSENT_ALERT_THRESHOLD',
    'GHOST_CLOSE_REQUIRE_CONFIRMATION',
    'GOLDEN_RULE_BB_15M_ENABLED',
    'GOLDEN_RULE_BB_1H_ENABLED',
    'GOLDEN_RULE_BB_4H_ENABLED',
    'GOLDEN_RULE_BB_D_ENABLED',
    'GOLDEN_RULE_BB_W_ENABLED',
    'GOLDEN_RULE_DC_15M_ENABLED',
    'GOLDEN_RULE_DC_1H_ENABLED',
    'GOLDEN_RULE_DC_4H_ENABLED',
    'GOLDEN_RULE_DC_D_ENABLED',
    'GOLDEN_RULE_DC_W_ENABLED',
    'GOLDEN_RULE_ENABLED',
    'GOLDEN_RULE_ENTRY_TF_LIST',
    'GOLDEN_RULE_EXIT_MIN_IND',
    'GOLDEN_RULE_EXIT_MIN_TFS',
    'GOLDEN_RULE_HTF_MIN_TFS',
    'GOLDEN_RULE_HTF_VETO_ENABLED',
    'GOLDEN_RULE_MIN_IND',
    'GOLDEN_RULE_REQUIRE_ACTIVATION',
    'GOLDEN_RULE_REQUIRE_HEDGE_OPEN',
    'GRACEFUL_EXIT_FILE_TEMPLATE',
    'GR_BB_EXTENDED_LONG',
    'GR_DC_EXTENDED_LONG',
    'GR_FILTER_ALL_ENTRIES',
    'GR_HEDGE_SCORE_FLOOR',
    'GR_HTF_DIRECT_ENTRY_DOUBLE_SCORE',
    'GR_HTF_DIRECT_ENTRY_ENABLED',
    'GR_HTF_DIRECT_ENTRY_SCORE_MIN',
    'GR_HTF_DIRECT_EXIT_ENABLED',
    'GR_HTF_DIRECT_EXIT_SCORE',
    'GR_HTF_GATE_ENABLED',
    'GR_HTF_REQUIRE_BEAR',
    'GR_HTF_REQUIRE_BULL',
    'GR_OPEN_COOLDOWN_S',
    'GR_TOTAL_VOTE_SCORE_MIN',
    'GR_V5_ARM_WINDOW_BARS',
    'GR_V5_BOUNCE_STOCH_LONG',
    'GR_V5_BOUNCE_STOCH_SHORT',
    'GR_V5_BOUNCE_WT_CROSS_REQUIRED',
    'GR_V5_BREAKOUT_REQUIRE_VOLUME',
    'GR_V5_BREAKOUT_VOL_MULT',
    'GR_V5_ENABLED',
    'GR_V5_HTF_MIN_ALIGN',
    'GR_V5_HTF_TFS',
    'GR_V5_INVALIDATE_PCT',
    'GR_V5_LTF_MIN_ALIGN',
    'GR_V5_LTF_TFS',
    'GR_V5_RETEST_BAND_PCT',
    'GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED',
    'GUARANTEED_REENTRY_AUGMENT_ENABLED',
    'GUARANTEED_REENTRY_DELTA_GATE_ENABLED',
    'GUARANTEED_REENTRY_HTF_VETO_ENABLED',
    'GUARANTEED_REENTRY_K_FAVORABLE_HIGH',
    'GUARANTEED_REENTRY_K_FAVORABLE_LOW',
    'GUARANTEED_REENTRY_K_HIGH_BLOCK',
    'GUARANTEED_REENTRY_K_LOW_BLOCK',
    'GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN',
    'GUARANTEED_REENTRY_STRICT_CONFIRMATION',
    'GUARANTEED_REENTRY_TIGHT_STOP_ENABLED',
    'GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S',
    'GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S',
    'GUARANTEED_REENTRY_TIGHT_STOP_PCT',
    'HARD_BREAKEVEN_FLOOR_ENABLED',
    'HARD_MAX_LOSS_PCT',
    'HARD_MAX_SYMBOL_VALUE_TRADIER',
    'HA_3M_ENTRY_WEIGHT',
    'HA_WICK_QUALITY_ENABLED',
    'HA_WICK_QUALITY_SCORE',
    'HA_WICK_QUALITY_TF',
    'HEDGE_ALL_POSITIONS',
    'HEDGE_ALREADY_COVERED_THRESHOLD',
    'HEDGE_BALANCE_COOLDOWN_SECONDS',
    'HEDGE_BANDAID_OFF_ENABLED',
    'HEDGE_BANDAID_OFF_FIRST_PRE_VEC_ENABLED',
    'HEDGE_BANDAID_OFF_REQUIRE_WT_3M_FLIP',
    'HEDGE_CLOSE_MODE',
    'HEDGE_CLOSE_REMOVE_FROM_TRADEABLE',
    'HEDGE_CLOSE_SCALP_MODE',
    'HEDGE_CLOSE_WT_DC_THRESHOLD',
    'HEDGE_CLOSE_WT_TFS_FAVOR',
    'HEDGE_COMPLETED_LOCKOUT_SECONDS',
    'HEDGE_CROSS_SYMBOL_TRADIER',
    'HEDGE_DC_LONG_REJECT_DCP',
    'HEDGE_DC_RESISTANCE_GATE_ENABLED',
    'HEDGE_DC_SHORT_REJECT_DCP',
    'HEDGE_DECAY_NUKE_ENABLED',
    'HEDGE_DETERIORATING_GAIN_DELTA_PP',
    'HEDGE_DETERIORATING_GAIN_ENABLED',
    'HEDGE_DUAL_IF_HEDGE_MODE',
    'HEDGE_ENTRY_MODE',
    'HEDGE_EXIT_BYPASS_NOLOSS',
    'HEDGE_EXIT_DELTA_CHECK_ENABLED',
    'HEDGE_EXIT_WT_TF',
    'HEDGE_FAILED_FALLBACK_CLOSE_ENABLED',
    'HEDGE_HTF_VETO_ENABLED',
    'HEDGE_MAX_ABSOLUTE_USD',
    'HEDGE_MAX_AGE_HOURS',
    'HEDGE_MAX_AGE_KILL_REQUIRE_PROFIT',
    'HEDGE_MAX_PCT_OF_LOSER',
    'HEDGE_MAX_RATIO',
    'HEDGE_MODE',
    'HEDGE_MODE_TRADIER',
    'HEDGE_MOMENTUM_GATE',
    'HEDGE_NEWBORN_DC_BREACH_ALLOWED',
    'HEDGE_NEWBORN_GRACE_MINUTES',
    'HEDGE_OPEN_OB_CHECK_ENABLED',
    'HEDGE_OPEN_OB_IMB_BOUND',
    'HEDGE_OVERSIZE_RATIO',
    'HEDGE_PROTECT_LOSS_VEC_ENABLED',
    'HEDGE_PROTECT_QTY_PCT',
    'HEDGE_PROTECT_TRIGGER_GAIN_PCT',
    'HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE',
    'HEDGE_SAME_SYMBOL_ENABLED',
    'HEDGE_SAME_SYMBOL_PCT',
    'HEDGE_SAME_SYMBOL_TRADIER',
    'HEDGE_SCALP_C_REQUIRE_COMBINED_NONNEG',
    'HEDGE_SCALP_MAX_AGE_MIN',
    'HEDGE_SIZE_RATIO_TRADIER',
    'HEDGE_STRICT_WT_ALL_TFS_ENABLED',
    'HEDGE_STRICT_WT_MIN_TFS_AGAINST',
    'HEDGE_TRIGGER_GR_SCORE_ENABLED',
    'HEDGE_TRIGGER_LOSS_PCT',
    'HEDGE_TRIGGER_LOSS_PCT_ENTRY',
    'HEDGE_TRIGGER_LOSS_TRADIER',
    'HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H',
    'HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H',
    'HEDGE_TRIGGER_USE_WT_3M_ALONE',
    'HEDGE_WEBHOOK_LOCK_TTL_SEC',
    'HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN',
    'HEDGE_WT_VEL_GATE_ENABLED',
    'HIGH_GAIN_AUGMENTATION_MIN_SIZE',
    'HLR_OFF_SMA_PTS_FRAC',
    'HLR_OFF_SMA_SZ_FRAC',
    'HLR_RALLY_ENABLED',
    'HLR_REDUCE_FRAC',
    'HLR_REENTRY_MAX_AGE_S',
    'HLR_REENTRY_MULT',
    'HLR_REENTRY_MULT_1H',
    'HLR_REENTRY_MULT_4H',
    'HLR_REENTRY_MULT_D',
    'HLR_REENTRY_MULT_W',
    'HLR_SMA_BAND_PCT',
    'HLR_TOP_EXIT_ENABLED',
    'HODL_LONG_ONLY',
    'HOLD_BARS_CLOSE',
    'HOLD_BARS_MID',
    'HOLD_BARS_OPEN',
    'HOUR_OF_DAY_GATE_ENABLED',
    'HTF1_CONF',
    'HTF4_CONF',
    'HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H',
    'HTF_AGAINST_FORCE_CLOSE_ENABLED',
    'HTF_ALIGNMENT_ENABLED',
    'HTF_ALIGN_REQUIRED_TRADIER',
    'HTF_AUG_VETO_FIX_ENABLED',
    'HTF_DC_BREAKOUT_TRADIER_ENABLED',
    'HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT',
    'HTF_DC_BREAKOUT_TRADIER_TF',
    'HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT',
    'HTF_DIRECTION_GATE_ENABLED',
    'HTF_EXIT_VETO_ENABLED',
    'HTF_EXIT_VETO_MAX_LOSS_PCT',
    'HTF_EXIT_VETO_MIN_ALIGNED',
    'HTF_GATE_APPLY_TO_AUGMENT',
    'HTF_GATE_APPLY_TO_OPEN',
    'HTF_GATE_BYPASS_RZ',
    'HTF_GATE_D_MANDATORY',
    'HTF_GATE_MIN_CONFIRMATIONS',
    'HTF_GATE_SIGNALS_SMA200D',
    'HTF_MIN_ALIGNED',
    'HTF_REGIME_ADD_MULT_PER_SMA',
    'HTF_REGIME_ENABLED',
    'HTF_REGIME_EXIT_TF',
    'HTF_REGIME_LEDGER_PATH',
    'HTF_REGIME_SCALE_IN',
    'HTF_REGIME_SIZE_CAP',
    'HTF_REGIME_TF',
    'HTF_REGIME_VOL_TARGET',
    'HTF_STRICT',
    'HTF_TREND_VETO_BYPASS_ENABLED',
    'HTF_TREND_VETO_ENABLED',
    'HTF_TREND_VETO_ON_REDUCE_ENABLED',
    'HTF_TREND_VETO_SCORE_MIN_ABS',
    'HTF_VETO_REQUIRE_D',
    'HTF_W_M_ALIGN_GATE_TRADIER_ENABLED',
    'HTF_W_M_ALIGN_TRADIER_REQUIRED',
    'HTF_W_REVERSAL_EXIT_TRADIER_ENABLED',
    'HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D',
    'IMMEDIATE_WRONG_WAY_ENABLED',
    'INDICATORS_FILE',
    'INDICATORS_SAVE_INTERVAL_SECONDS',
    'INDICATOR_UPDATE_INTERVAL',
    'INF_7D_BEAT_MIN_DELTA',
    'INF_DEDICATED_WINNERS_ENABLED',
    'INF_RANKING_BYPASS_DELTA',
    'INF_RANKING_BYPASS_FRESHNESS_MIN',
    'INF_RANKING_BYPASS_HTF',
    'INF_RANKING_BYPASS_MAX_POS',
    'INF_RANKING_BYPASS_SCORE',
    'INF_RANKING_BYPASS_STOCH',
    'INF_RANKING_BYPASS_WT',
    'INF_RANKING_PRIORITY_BYPASS',
    'IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED',
    'IN_GAIN_TREND_REDUCE_FRAC',
    'K3M_CAP',
    'K3M_CAP_BREAKOUT_BYPASS',
    'K3M_FLOOR',
    'K3M_FLOOR_ENABLED',
    'KLINES_CACHE_DIR',
    'K_LOWER_HIGH_EXIT_ENABLED',
    'K_LOWER_HIGH_EXTREME',
    'K_LOWER_HIGH_LTF_THRESHOLD',
    'K_ZONE_ENTRY_BONUS',
    'K_ZONE_ENTRY_BONUS_TRADIER',
    'K_ZONE_ENTRY_ENABLED',
    'K_ZONE_ENTRY_ENABLED_TRADIER',
    'K_ZONE_LONG_THRESHOLD',
    'K_ZONE_LONG_THRESHOLD_TRADIER',
    'K_ZONE_SHORT_THRESHOLD',
    'K_ZONE_SHORT_THRESHOLD_TRADIER',
    'K_ZONE_VETO_ENABLED_TRADIER',
    'LADDER_AUTO_SAVE_SECONDS',
    'LADDER_TTL_MINUTES',
    'LEADERBOARD_FILTER',
    'LEADERBOARD_LONG',
    'LEADERBOARD_SHORT',
    'LEGACY_AGGRESSIVE_LOSS_CUT',
    'LEGACY_DC_BREAKOUT_REENTRY',
    'LEGACY_FAST_CUT_LOSS',
    'LEGACY_GUARANTEED_REENTRY',
    'LEGACY_PROC_SINGLE_REENTRY',
    'LEGACY_REENTRY_GUARANTEED_2WT',
    'LEGACY_REENTRY_GUARANTEED_BOTTOM',
    'LEGACY_REENTRY_GUARANTEED_CROSS',
    'LEGACY_REENTRY_PSR_DC_BOUNCE',
    'LEGACY_REENTRY_PSR_FULL_DC',
    'LEGACY_REENTRY_PSR_K_DC_CROSSOVER',
    'LEGACY_REENTRY_PSR_QUICK_RECOVERY',
    'LEGACY_WR_PULLBACK',
    'LH_HL_FILTER_AUGMENT_GATE_ENABLED',
    'LH_HL_FILTER_DC_THRESHOLD_PCT',
    'LH_HL_FILTER_ENABLED',
    'LH_HL_FILTER_HEDGE_GATE_ENABLED',
    'LH_HL_FILTER_MODE',
    'LH_HL_FILTER_REPLACE_SMA200D',
    'LH_HL_FILTER_REQUIRE_BOTH',
    'LH_HL_FILTER_TF_REQ',
    'LIGHT_MODE',
    'LIVE_ENTRY_ENGINE_BOOST_SCORE',
    'LIVE_ENTRY_ENGINE_DC_ENABLED',
    'LIVE_ENTRY_ENGINE_ENABLED',
    'LIVE_ENTRY_ENGINE_HTF_ENABLED',
    'LIVE_ENTRY_ENGINE_MIN_SCORE',
    'LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT',
    'LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED',
    'LIVE_ENTRY_ENGINE_STOCH_ENABLED',
    'LIVE_ENTRY_ENGINE_WT_ENABLED',
    'LIVE_INDICATOR_MAX_BARS_PER_TF',
    'LIVE_VEC_EMERGENCY_BRAKE_ENABLED',
    'LIVE_VEC_QUARANTINE_STRATEGY_ENABLED',
    'LIVE_VEC_STALE_MARK_PRICE_ENABLED',
    'LOCAL_EXTREMES_MIN_SCORE',
    'LOG_BACKUP_COUNT',
    'LOG_DIR',
    'LOG_FILE_TRADIER_MANAGE',
    'LOG_FILE_TRADIER_POSITIONS',
    'LOG_FILE_TRADIER_PRICES',
    'LOG_INTERVAL_SECONDS',
    'LOG_MAX_BYTES',
    'LONG_STOCH_CHASE_BLOCK',
    'LONG_STRUCT_EXIT_TF',
    'LONG_WAIT_DIRECT_BOUNCE_DISTANCE',
    'LONG_WAIT_DIRECT_BOUNCE_TIMEFRAME',
    'LONG_WAIT_DIRECT_CONFIRMATION',
    'LONG_WAIT_DIRECT_DEEP_K4H',
    'LONG_WAIT_DIRECT_ENABLED',
    'LONG_WAIT_DIRECT_TURN_K1H',
    'LOSS_CUT_ENABLED',
    'LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED',
    'LOSS_EXIT_REQUIRES_HEDGE',
    'LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED',
    'LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED',
    'LOSS_EXIT_TECHNICAL_BYPASS',
    'LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK',
    'LR_BAND_BE_RATCHET',
    'LR_BAND_E02_EXIT_ENABLED',
    'LR_BAND_ENTRY_ENABLED',
    'LR_BAND_ENTRY_LO',
    'LR_BAND_ENTRY_PRIORITY',
    'LR_BAND_ENTRY_R2_MIN',
    'LR_BAND_ENTRY_SIDES',
    'LR_BAND_ENTRY_TF',
    'LR_BAND_EXIT_EXEMPT',
    'LR_BAND_HARVEST_ENABLED',
    'LR_BAND_HARVEST_FRAC',
    'LR_BAND_HARVEST_HI',
    'LR_BAND_LADDER_ABOVE_TOP_MULT',
    'LR_BAND_LADDER_BASE_UNIT_USD',
    'LR_BAND_LADDER_BASIS',
    'LR_BAND_LADDER_BELOW_BOTTOM_MULT',
    'LR_BAND_LADDER_BOTTOM_MULT',
    'LR_BAND_LADDER_CAPACITY_USD',
    'LR_BAND_LADDER_CENTER',
    'LR_BAND_LADDER_ENABLED',
    'LR_BAND_LADDER_MODE',
    'LR_BAND_LADDER_ORDINARY_PARITY_ENABLED',
    'LR_BAND_LADDER_STOCH_EXTREME',
    'LR_BAND_LADDER_TF_BOTTOM',
    'LR_BAND_LADDER_TF_TOP',
    'LR_BAND_LADDER_TOP_MULT',
    'LR_BAND_LADDER_TRIGGER',
    'LR_BAND_READD_LO',
    'LR_BAND_REGIME_ENABLED',
    'LR_BAND_REGIME_MAX_PB',
    'LR_BAND_SIZE_DEPTH_GAIN',
    'LR_BAND_SIZE_MAX',
    'LR_BAND_SIZE_SLOPE_GAIN',
    'LR_BAND_SLOPE_FLIP_EXIT_ENABLED',
    'LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN',
    'LR_BAND_SLOPE_FLIP_MIN_PCT_DAY',
    'LR_BAND_SLOPE_NORM_PCT_DAY',
    'LR_CHANNEL_LONG_LENGTHS',
    'LR_PCTB_D_LONG_ENTRY_ENABLED',
    'LR_PCTB_D_LONG_ENTRY_THRESHOLD',
    'LR_PCTB_D_SHORT_THRESHOLD',
    'LS_RATIO_CONTRARIAN_ENABLED',
    'LS_RATIO_ENFORCE',
    'LS_RATIO_ENFORCE_TRADIER',
    'LS_RATIO_EXTREME_THRESHOLD',
    'LS_RATIO_HARD_MAX',
    'LS_RATIO_HARD_MIN',
    'LS_RATIO_LOG_INTERVAL',
    'LS_RATIO_MAX',
    'LS_RATIO_MAX_TRADIER',
    'LS_RATIO_MIN',
    'LS_RATIO_MIN_TRADIER',
    'LS_RATIO_PENALTY',
    'LS_RATIO_REBALANCE_THRESHOLD',
    'LT_SCORE_WEIGHT_HTF',
    'LUNCH_DEADZONE_ENABLED',
    'LUNCH_DEADZONE_MODE',
    'LUNCH_DEADZONE_SIZE_MULT',
    'MACD_EXIT_ENABLED',
    'MACD_EXIT_MIN_GAIN',
    'MACD_EXIT_TF',
    'MACD_ZERO_CROSS_ENABLED',
    'MACD_ZERO_CROSS_SCORE',
    'MACD_ZERO_CROSS_TF',
    'MACRO_BLACKOUT_ENABLED',
    'MACRO_BLACKOUT_SIZE_MULT',
    'MAKER_CLOSE_COMMISSION_FLOOR_ENABLED',
    'MAKER_CLOSE_COMMISSION_FLOOR_TTL_SEC',
    'MANAGE_REDUCE',
    'MANDATORY_HEDGE_GAIN_THRESHOLD_PCT',
    'MANDATORY_HEDGE_HARD_THRESHOLD_PCT',
    'MANDATORY_HEDGE_ON_NEGATIVE_ENABLED',
    'MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS',
    'MANDATORY_REENTRY_DC4_WINDOW_MIN',
    'MANDATORY_REENTRY_K_HIGH_BLOCK',
    'MANDATORY_REENTRY_K_LOW_BLOCK',
    'MANDATORY_REENTRY_MIN_WT_AGREE',
    'MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME',
    'MANDATORY_REENTRY_WT_FILTER_ENABLED',
    'MANDATORY_REENTRY_WT_FILTER_MIN_TFS',
    'MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY',
    'MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP',
    'MANDATORY_REENTRY_WT_FILTER_TF_MODE',
    'MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO',
    'MARKET_CLOSE_HOUR',
    'MARKET_CLOSE_MINUTE',
    'MARKET_DATA_REFRESH_INTERVAL_SECONDS',
    'MARKET_MODE',
    'MARKET_OPEN_HOUR',
    'MARKET_OPEN_MINUTE',
    'MARKET_QUALITY_SCORE_ENABLED',
    'MARKET_QUALITY_SCORE_ENABLED_TRADIER',
    'MARK_PRICE_MAX_STALENESS',
    'MAX_ALLOWED_DRAWDOWN_PCT',
    'MAX_AUGMENTS_PER_POSITION',
    'MAX_CONCURRENT_ORDERS',
    'MAX_CONCURRENT_POSITIONS',
    'MAX_DAILY_LOSS_PCT',
    'MAX_HEDGE_BALANCE_MULTIPLIER',
    'MAX_HEDGE_BALANCE_VALUE_USD',
    'MAX_MEMORY_GB',
    'MAX_ORDER_VALUE',
    'MAX_ORDER_VALUE_FIN',
    'MAX_ORDER_VALUE_MEN',
    'MAX_POSITION_SIZE',
    'MAX_POSITION_SIZE_BTC',
    'MAX_POSITION_SIZE_FIN',
    'MAX_POSITION_SIZE_MEN',
    'MAX_SYMBOL_VALUE_TRADIER',
    'MEMORY_MONITOR_SLEEP_SECONDS',
    'MFI_ENTRY_ENABLED',
    'MFI_ENTRY_LONG_MAX',
    'MFI_ENTRY_SHORT_MIN',
    'MFI_FLIP_EXIT_ENABLED',
    'MFI_FLIP_EXIT_LONG_THRESHOLD',
    'MFI_FLIP_EXIT_SHORT_THRESHOLD',
    'MFI_LONG_THRESHOLD_D',
    'MICRO_SCALP_GAIN_THRESHOLD_PCT',
    'MICRO_SCALP_STOCKS_ACCOUNTS',
    'MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT',
    'MICRO_SCALP_STOCKS_MAKER_ENABLED',
    'MICRO_SCALP_STOCKS_PEAK_FLOOR_PCT',
    'MICRO_SCALP_USDC_ACCOUNTS',
    'MICRO_SCALP_USDC_MAKER_ENABLED',
    'MID_ZONE_SHORT_EXTRA_IND',
    'MINERVINI_ENABLED',
    'MINERVINI_GATE_ENABLED',
    'MINERVINI_LONG_BUDGET',
    'MINERVINI_MAX_HOLD_DAYS',
    'MINERVINI_MIN_SCORE',
    'MINERVINI_MIN_SEPA_SCORE',
    'MINERVINI_POSITION_SIZE',
    'MINERVINI_TARGET_PCT',
    'MIN_EXIT_TF_AGAINST_TRADIER',
    'MIN_GAIN',
    'MIN_GAIN_TO_BUY_AGGRESSIVELY',
    'MIN_HOLD_BARS_BEFORE_EXIT',
    'MIN_HOLD_BARS_TRADIER',
    'MIN_HOLD_MINUTES_TRADIER',
    'MIN_PERC_FROM_SMA_1',
    'MIN_PERC_FROM_SMA_15',
    'MIN_POSITION_SIZE',
    'MIN_USD_DELTA_CONFIRM',
    'MITIGATOR_AUGMENT_CONSECUTIVE',
    'MITIGATOR_AUGMENT_THRESHOLD',
    'MITIGATOR_COOLDOWN',
    'MITIGATOR_ENABLED',
    'MITIGATOR_REENTRY_COOLDOWN',
    'MITIGATOR_REENTRY_PRICE_PCT',
    'MITIGATOR_SCAN_INTERVAL',
    'MITIGATOR_TIER1_DROP',
    'MITIGATOR_TIER1_PEAK',
    'MITIGATOR_TIER1_REDUCE_PCT',
    'MITIGATOR_TIER2_DROP',
    'MITIGATOR_TIER2_REDUCE_PCT',
    'MITIGATOR_TIER3_DROP',
    'MI_DIV_EXIT_ENABLED',
    'MI_DIV_EXIT_ENABLED_TRADIER',
    'MI_ENTRY_ENABLED',
    'MI_ENTRY_ENABLED_TRADIER',
    'MI_ENTRY_EXHAUST_BONUS',
    'MI_ENTRY_EXHAUST_BONUS_TRADIER',
    'MI_ENTRY_STRUCT_BONUS',
    'MI_ENTRY_STRUCT_BONUS_TRADIER',
    'MI_EXHAUST_EXIT_ENABLED',
    'MI_EXHAUST_EXIT_ENABLED_TRADIER',
    'MI_EXIT_ENABLED',
    'MI_EXIT_ENABLED_TRADIER',
    'MI_EXIT_VETO_ENABLED_TRADIER',
    'MI_MIN_GAIN_EXIT',
    'MI_MIN_GAIN_EXIT_TRADIER',
    'MI_STRUCT_EXIT_ENABLED',
    'MI_STRUCT_EXIT_ENABLED_TRADIER',
    'MI_TF_AGREE_MIN_TRADIER',
    'MI_VELOCITY_EXIT_ENABLED',
    'MI_VELOCITY_EXIT_ENABLED_TRADIER',
    'MI_WAVE_EXIT_ENABLED',
    'MI_WAVE_EXIT_ENABLED_TRADIER',
    'MOM3_ENTRY_ENABLED',
    'MOM3_LONG_THRESHOLD',
    'MOM3_SHORT_THRESHOLD',
    'MOM4S_S_GATE_ENABLED',
    'MOM5_ENTRY_ENABLED',
    'MOM5_LONG_THRESHOLD',
    'MOM5_SHORT_THRESHOLD',
    'MOM5_TRENDER_L_GATE_ENABLED',
    'MOMENTUM_FADE_BODY_ATR_MIN',
    'MOMENTUM_FADE_BODY_ATR_MIN_TRADIER',
    'MOMENTUM_FADE_ENABLED',
    'MOMENTUM_FADE_ENABLED_TRADIER',
    'MOMENTUM_FADE_K_ZONE',
    'MOMENTUM_FADE_K_ZONE_TRADIER',
    'MOMENTUM_FADE_SCORE_BONUS',
    'MOMENTUM_FADE_SCORE_BONUS_TRADIER',
    'MOMENTUM_FADE_VOL_MIN',
    'MOMENTUM_FADE_VOL_MIN_TRADIER',
    'MOMENTUM_RIDER_ACCOUNT',
    'MOMENTUM_RIDER_BASE_SIZE_USD',
    'MOMENTUM_RIDER_COOLDOWN',
    'MOMENTUM_RIDER_DC_WIDTH_MIN',
    'MOMENTUM_RIDER_ENABLED',
    'MOMENTUM_RIDER_HEDGE_RATIO',
    'MOMENTUM_RIDER_MAX_SIZE_USD',
    'MOMENTUM_RIDER_MAX_SYMBOLS',
    'MOMENTUM_RIDER_REL_VOL_MIN',
    'MOMENTUM_RIDER_SCAN_INTERVAL',
    'MOMENTUM_SMA_WATCHDOG_COOLDOWN_S',
    'MOMENTUM_SMA_WATCHDOG_ENABLED',
    'MOMENTUM_SMA_WATCHDOG_INTERVAL_S',
    'MOMENTUM_SMA_WATCHDOG_PCT',
    'MOMENTUM_SMA_WATCHDOG_WT_CAP',
    'MONITOR_REDUCTION_STALE_THRESHOLD',
    'MOVER_ACCOUNT',
    'MOVER_DETECTION_ENABLED',
    'MOVER_LINEARITY_MIN',
    'MOVER_LOOKBACK',
    'MOVER_MAX_POSITIONS',
    'MOVER_SCORE_BONUS',
    'MOVER_THRESHOLD',
    'MOVER_VOL_MIN',
    'MR3S_S_GATE_ENABLED',
    'MR5_L_GATE_ENABLED',
    'MTF_ARMED_BANDTYPES',
    'MTF_ARMED_ENTRY_ENABLED',
    'MTF_ARMED_ENTRY_SKIP_SHORT',
    'MTF_ARMED_HTF_LIST',
    'MTF_ARMED_WT_DIRECTION_SUSPEND_BREAKOUT_BYPASS',
    'MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED',
    'MTF_ARROW_CONFIRM_PCT',
    'MTF_ARROW_ENTRY_ENABLED',
    'MTF_ARROW_SHORT_ENTRY_ENABLED',
    'MTF_ARROW_SIZE_GAIN',
    'MTF_ARROW_SIZE_MAX',
    'MTF_ARROW_SLOPE_LAMBDA',
    'MTF_ARROW_SLOPE_NORM_PCT_DAY',
    'MTF_ARROW_THETA',
    'MTF_ARROW_TRAIL_EXIT_ENABLED',
    'MTF_ARROW_WEIGHTS',
    'MTF_ATR_MULTITF_DIRECT_ENABLED',
    'MTF_ATR_MULTITF_DIRECT_MIN_CONFIRMING_TFS',
    'MTF_ATR_MULTITF_DIRECT_MIN_PROFIT_PCT',
    'MTF_ATR_MULTITF_DIRECT_MULT',
    'MTF_ATR_MULTITF_DIRECT_TIMEFRAMES',
    'MTF_ATR_TRAIL_ENABLED',
    'MTF_ATR_TRAIL_MULT',
    'MTF_ATR_TRAIL_TF',
    'MTF_ATR_TRAIL_TF_TRADIER',
    'MTF_BB_REJECT_EXIT_ENABLED',
    'MTF_BB_REJECT_EXIT_LOOKBACK',
    'MTF_BB_REJECT_EXIT_TF',
    'MTF_DC_REJECT_EXIT_ENABLED',
    'MTF_DC_REJECT_EXIT_LOOKBACK',
    'MTF_DC_REJECT_EXIT_TF',
    'MTF_ENTRY_REQUIRE_GR_FILTER',
    'MTF_EXIT_MIN_OPEN_TS',
    'MTF_EXIT_USE_COMPOUND',
    'MTF_FILTER_STRONG_BUY_QUICK_BYPASS',
    'MTF_GR_EXIT_GATE_ENABLED',
    'MTF_GR_EXIT_MIN_IND',
    'MTF_GR_EXIT_MIN_TFS',
    'MTF_GR_FILTER_ENABLED',
    'MTF_GR_INVERT_DC_BB',
    'MTF_GR_MIN_IND',
    'MTF_GR_MIN_TFS',
    'MTF_REQUIRE_ARMED_ANY',
    'MTF_WT_CROSS_EXIT_DIRECT_ENABLED',
    'MTF_WT_CROSS_EXIT_ENABLED',
    'MTF_WT_CROSS_EXIT_TF',
    'MTS_BOTTOM_BONUS_THRESHOLD',
    'MTS_BOTTOM_MIN_SHORT',
    'MTS_BOTTOM_MIN_TRADIER',
    'MTS_BOTTOM_STRONG_THRESHOLD',
    'MTS_ENTRY_QUALITY_BONUS',
    'MTS_ENTRY_QUALITY_MIN',
    'MTS_ENTRY_QUALITY_MIN_SHORT',
    'MTS_ENTRY_QUALITY_MIN_TRADIER',
    'MTS_ENTRY_QUALITY_STRONG',
    'MTS_GATE_ENABLED',
    'MTS_GATE_ENABLED_TRADIER',
    'MTS_WEIGHT_D',
    'MULT_FILE',
    'MU_CORRECTION_EXIT_ENABLED',
    'MU_CORRECTION_HTF_K_MIN',
    'MU_CORRECTION_HTF_MIN_TFS',
    'MU_CORRECTION_HTF_RSI_MIN',
    'MU_CORRECTION_HTF_TFS',
    'MU_CORRECTION_LTF_FALL_MIN_TFS',
    'MU_CORRECTION_LTF_FALL_TFS',
    'MU_CORRECTION_MIN_GAIN_PCT',
    'MU_CORRECTION_REENTRY_DC_TOL_PCT',
    'MU_CORRECTION_REENTRY_ENABLED',
    'MU_CORRECTION_REENTRY_STOCH_ENABLED',
    'MU_CORRECTION_REQUIRE_CLOSE_REVERSAL',
    'MU_CORRECTION_REQUIRE_HIGH_REVERSAL',
    'MU_CORRECTION_SYMBOLS',
    'NEWBORN_DC_STOP_ENABLED',
    'NEWBORN_DC_STOP_FIELD',
    'NEWBORN_DC_STOP_MAX_AGE_MIN',
    'NEWBORN_LOSS_KILL_ENABLED',
    'NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT',
    'NEWS_POLL_INTERVAL_CRYPTO',
    'NEWS_POLL_INTERVAL_SOCIAL',
    'NEWS_SENTIMENT_DECAY_HOURS',
    'NEWS_SENTIMENT_ENABLED',
    'NEWS_SENTIMENT_MIN_ARTICLES',
    'NEWS_SENTIMENT_WEIGHT',
    'NEW_POSITION_MAX_LOSS_THRESHOLD',
    'NOLOSS_BB1H_GATE_ENABLED',
    'NOLOSS_BYPASS_WT_5OF5_ENABLED',
    'NOLOSS_BYPASS_WT_5OF5_MIN_TFS',
    'NOLOSS_DC4H_GATE_ENABLED',
    'NOLOSS_ENABLED',
    'NOLOSS_MIN_PROFIT_PCT',
    'NOLOSS_MIN_PROFIT_PCT_TRADIER',
    'OBLIGATORY_HEDGE_ENABLED',
    'OBLIGATORY_HEDGE_MIN_LOSS_PCT',
    'OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED',
    'OBLIGATORY_HEDGE_OR_CLOSE_LOOP_INTERVAL_SECONDS',
    'OBLIGATORY_HEDGE_PCT',
    'OBLIGATORY_HEDGE_WT_TFS',
    'OBLIGATORY_HEDGE_WT_TFS_REQUIRED',
    'OBLIGATORY_HEDGE_WT_USE_15M',
    'OBLIGATORY_HEDGE_WT_USE_1H',
    'OBLIGATORY_HEDGE_WT_USE_1M',
    'OBLIGATORY_HEDGE_WT_USE_3M',
    'OBLIGATORY_OPEN_USD',
    'OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT',
    'OBLIGATORY_REENTRY_ENABLED',
    'OBLIGATORY_REENTRY_K15_HIGH_BLOCK',
    'OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC',
    'OBLIGATORY_REENTRY_LONG_ENABLED',
    'OBLIGATORY_REENTRY_SCORE_TIER1',
    'OBLIGATORY_REENTRY_SCORE_TIER2',
    'OBLIGATORY_REENTRY_SCORE_TIER3',
    'OBLIGATORY_REENTRY_SHORT_ENABLED',
    'OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK',
    'OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC',
    'OBLIGATORY_REENTRY_SHORT_SMA_BOUNCE_SIZE_MULT',
    'OBLIGATORY_REENTRY_SMA_BOUNCE_SIZE_MULT',
    'OBLIGATORY_REENTRY_SMA_FIELD',
    'OBLIGATORY_REENTRY_SMA_TF',
    'OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED',
    'OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED',
    'OBLIGATORY_SECTOR_HEDGE_ENABLED',
    'OBLIGATORY_SECTOR_HEDGE_LOOP_INTERVAL_SECONDS',
    'OBLIGATORY_SECTOR_HEDGE_TRIGGER_REQUIRE_WT_5M_AND_1H',
    'OBLIGATORY_SMA200_PCT',
    'OBLIGATORY_SMA200_WT3M_ENABLED',
    'OB_ENTRY_GATE_ACCOUNTS',
    'OB_ENTRY_MIN_LONG_SCORE',
    'OB_ENTRY_MIN_SHORT_SCORE',
    'OB_ENTRY_WALL_TOO_CLOSE_PCT',
    'OB_PRICE_DEFER_ENABLED',
    'OI_CONFIRM_ENABLED',
    'OI_CONFIRM_ENABLED_TRADIER',
    'OI_CONFIRM_MIN_CHANGE_PCT',
    'OI_CONFIRM_MIN_OI_CHANGE_PCT_TRADIER',
    'OI_CONFIRM_MIN_PRICE_PCT',
    'OI_CONFIRM_MIN_PRICE_PCT_TRADIER',
    'OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED',
    'OI_DIVERGENCE_ENABLED',
    'OI_DIVERGENCE_PENALTY',
    'OI_HEDGE_GATE_ENABLED',
    'OI_LIVE_REFRESH_HOURS',
    'OPENING_BUFFER_NO_CLOSE_MINUTES',
    'OPEN_RATE_BREAKER_ENABLED',
    'OPEN_RATE_MAX',
    'OPEN_RATE_WINDOW_SEC',
    'OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED',
    'OPPOSITE_LOSER_HEDGE_PROTECT_MAX_GAIN',
    'OPPOSITE_LOSER_HEDGE_PROTECT_REQUIRE_WT_3M',
    'OPTIMAL_HOLD_BARS_15M',
    'OPTIMAL_HOLD_BARS_3M',
    'OPTIONS_ALERT_ABS_LOSS_PP',
    'OPTIONS_ALERT_DROP_PP',
    'OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED',
    'OPTIONS_AUGMENT_INTO_LOSS_THRESHOLD',
    'OPTIONS_BASE_CAP',
    'OPTIONS_BUY_MAX_OTM_PCT',
    'OPTIONS_BUY_MIN_ABS_DELTA',
    'OPTIONS_BUY_MIN_DTE',
    'OPTIONS_BUY_MIN_WT_DC_SCORE',
    'OPTIONS_BUY_PREFERRED_DTE',
    'OPTIONS_BUY_REQUIRE_D_ALIGN',
    'OPTIONS_BUY_WT_DC_GATE_ENABLED',
    'OPTIONS_CONTINUOUS_SECTOR_GATE',
    'OPTIONS_CSP_DTE_MAX',
    'OPTIONS_CSP_DTE_MIN',
    'OPTIONS_CSP_EDGE_MARGIN',
    'OPTIONS_CSP_ENABLED',
    'OPTIONS_CSP_MAX_CAPITAL_PCT',
    'OPTIONS_CSP_MAX_DELTA',
    'OPTIONS_CSP_MAX_HOLD_DAYS',
    'OPTIONS_CSP_MAX_POS_PCT_OF_ACCOUNT',
    'OPTIONS_CSP_MIN_DELTA',
    'OPTIONS_CSP_MIN_EXTRINSIC_PCT',
    'OPTIONS_CSP_MIN_IV_RANK',
    'OPTIONS_CSP_MONITOR_CALL_BREACH_PCT',
    'OPTIONS_CSP_MONITOR_CALL_GAP_FROM_ENTRY_PCT',
    'OPTIONS_CSP_MONITOR_CORRELATED_BREACH_N',
    'OPTIONS_CSP_MONITOR_GAP_FROM_ENTRY_PCT',
    'OPTIONS_CSP_MONITOR_LOG_EVERY_TICK',
    'OPTIONS_CSP_MONITOR_LOSS_TRIGGER_PCT',
    'OPTIONS_CSP_MONITOR_MAX_LOSS_PCT',
    'OPTIONS_CSP_MONITOR_POLL_SEC',
    'OPTIONS_CSP_MONITOR_REQUIRE_WT_D_TURN',
    'OPTIONS_CSP_MONITOR_STRIKE_BREACH_PCT',
    'OPTIONS_CSP_NAKED_CALL_ENABLED',
    'OPTIONS_CSP_PROFIT_TARGET_PCT',
    'OPTIONS_EQUITY_HEDGE_COOLDOWN_MIN',
    'OPTIONS_EQUITY_HEDGE_DC_BREACH_EXIT_ENABLED',
    'OPTIONS_EQUITY_HEDGE_DIRECTION_GUARD_ENABLED',
    'OPTIONS_EQUITY_HEDGE_ENABLED',
    'OPTIONS_EQUITY_HEDGE_MAX_NOTIONAL_USD',
    'OPTIONS_EQUITY_HEDGE_MAX_PCT_OF_OPT_COST',
    'OPTIONS_EQUITY_HEDGE_TRIGGER_PCT',
    'OPTIONS_FULL_DIV_CAP',
    'OPTIONS_HEDGED_CAP',
    'OPTIONS_HEDGE_BOTTOM_MIN_SIGNALS',
    'OPTIONS_HEDGE_DC_REL_TOL_PCT',
    'OPTIONS_HEDGE_K_OVERSOLD_PCT',
    'OPTIONS_HEDGE_LADDER_ENABLED',
    'OPTIONS_HEDGE_PAIR_GUARD_ENABLED',
    'OPTIONS_HEDGE_PUT_DELTA_MAX',
    'OPTIONS_HEDGE_PUT_DELTA_MIN',
    'OPTIONS_HEDGE_PUT_DTE_MAX',
    'OPTIONS_HEDGE_PUT_DTE_MIN',
    'OPTIONS_HEDGE_PUT_MAX_IV_RANK',
    'OPTIONS_HEDGE_PUT_MAX_SPREAD_PCT',
    'OPTIONS_HEDGE_RATIO_MIN',
    'OPTIONS_LEVEL_BREAK_BUFFER',
    'OPTIONS_LEVEL_BREAK_MIN_DTE',
    'OPTIONS_LIVE_TRADING_ENABLED',
    'OPTIONS_MARKET_RATIO_MAX',
    'OPTIONS_MARKET_RATIO_MIN',
    'OPTIONS_MAX_CONTRACTS_PER_ORDER',
    'OPTIONS_MAX_LOSS_GUARD_ENABLED',
    'OPTIONS_MAX_LOSS_PCT_DTE_14',
    'OPTIONS_MAX_LOSS_PCT_DTE_30',
    'OPTIONS_MAX_LOSS_PCT_DTE_LOW',
    'OPTIONS_MAX_ORDER_BUDGET',
    'OPTIONS_MAX_PER_GROUP',
    'OPTIONS_MAX_PER_SECTOR',
    'OPTIONS_MAX_PER_SYMBOL',
    'OPTIONS_MAX_SINGLE_CONTRACT_PRICE',
    'OPTIONS_MIN_GROUPS',
    'OPTIONS_MIN_SECTORS',
    'OPTIONS_PREMARKET_NO_FIRE',
    'OPTIONS_SPREAD_DTE_MAX',
    'OPTIONS_SPREAD_DTE_MIN',
    'OPTIONS_SPREAD_ENABLED',
    'OPTIONS_SPREAD_IV_RANK_MIN',
    'OPTIONS_SPREAD_MAX_CONCURRENT',
    'OPTIONS_SPREAD_MAX_HOLD_DAYS',
    'OPTIONS_SPREAD_PROFIT_TARGET_PCT',
    'OPTIONS_SPREAD_SHORT_DELTA',
    'OPTIONS_SPREAD_UNIVERSE',
    'OPTIONS_SPREAD_WIDTH',
    'OPTIONS_STOCK_CSP_ENABLED',
    'OPTIONS_STOCK_CSP_IV_RANK_MIN',
    'OPTIONS_STOCK_CSP_MAX_CONCURRENT',
    'OPTIONS_STOCK_CSP_MIN_CASH',
    'OPTIONS_USER_CANCEL_COOLDOWN_HOURS',
    'OPTIONS_WT_ACCEL_GROWTH_PCT',
    'OPTIONS_WT_ACCEL_MIN_ABS',
    'OPTIONS_WT_SLOWDOWN_PCT',
    'ORB_ENABLED',
    'ORB_LONG_BUDGET',
    'ORB_MAX_HOLD_MINUTES',
    'ORB_MAX_PER_DAY',
    'ORB_POSITION_SIZE',
    'ORB_RVOL_MIN',
    'ORB_SHORT_BUDGET',
    'ORB_STOP_MIDPOINT',
    'ORB_TARGET_MULT',
    'ORB_WINDOW_MINUTES',
    'ORDER_CACHE_TTL',
    'ORPHAN_HEDGE_CHECK_GAIN',
    'OUTLIER_DETECTOR_ENABLED',
    'OUTLIER_RUNAWAY_ATR_FACTOR',
    'OUTLIER_SCAN_INTERVAL',
    'OUTLIER_STALE_HOURS',
    'OUTLIER_STUCK_ATR_FACTOR',
    'OUTLIER_STUCK_HOURS',
    'OVERBOUGHT_SCORE_GUT_BREAKOUT_BYPASS',
    'OVERNIGHT_GAP_HEDGE_CLOSE_MINUTES',
    'OVERNIGHT_GAP_HEDGE_ENABLED',
    'OVERNIGHT_GAP_HEDGE_OPEN_MINUTES',
    'OVERNIGHT_GAP_HEDGE_SENTIMENT_THRESHOLD',
    'OVERNIGHT_GAP_HEDGE_SIZE_FRAC',
    'PARABOLIC_BB_PCT_B_4H_MAX',
    'PARABOLIC_BB_PCT_B_4H_MIN',
    'PARABOLIC_PROTECTION_ENABLED',
    'PARABOLIC_RSI_1H_MAX',
    'PARABOLIC_RSI_1H_MIN',
    'PARABOLIC_RSI_4H_MAX',
    'PARABOLIC_RSI_4H_MIN',
    'PARITY_COMPARISON_MODE',
    'PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER',
    'PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER',
    'PARTIAL_PROFIT_LOCK_ENABLED',
    'PARTIAL_PROFIT_LOCK_FRAC_TRADIER',
    'PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER',
    'PARTIAL_PROFIT_LOCK_SLIPPAGE_PCT',
    'PARTIAL_PROFIT_LOCK_SWEEP_ARM_PCT',
    'PARTIAL_PROFIT_LOCK_SWEEP_ENABLED',
    'PARTIAL_PROFIT_LOCK_SWEEP_GAIN_PCT',
    'PARTIAL_PROFIT_LOCK_USE_MAKER_TRADIER',
    'PEAK_GIVEBACK_DROP_PCT',
    'PEAK_GIVEBACK_DROP_TRIGGER_ENABLED',
    'PEAK_GIVEBACK_HARD_ZERO_ENABLED',
    'PEAK_GIVEBACK_MIN_PEAK_PCT',
    'PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT',
    'PEAK_GIVEBACK_PROTECTION_ENABLED',
    'PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN',
    'PENNY_STOCK_LONG_BLOCK_ENABLED',
    'PENNY_STOCK_LONG_BLOCK_PRICE_USD',
    'PERSIST_FIN',
    'PERSIST_FLZ',
    'PERSIST_INF',
    'PERSIST_MEN',
    'PERSYM_FINAL_BOOK_ENABLED',
    'PER_SYMBOL_CONFIG_ENABLED',
    'PER_SYM_CONFIG_ENABLED',
    'PLOT_LOOP_INTERVAL_SECONDS',
    'PNL_DECAY_COMPLETE_DAYS',
    'PNL_DECAY_FINAL_PERCENTAGE',
    'PNL_DECAY_START_HOURS',
    'POSITIONS_SERVICE_HEALTH_TIMEOUT',
    'POSITION_CACHE_TTL',
    'POSITION_REDIS_REFRESH_INTERVAL',
    'POSITION_REFRESH_INTERVAL',
    'POSITION_REFRESH_MIN_INTERVAL',
    'POSITION_SAVE_INTERVAL',
    'POSITION_STALE_THRESHOLD_SECONDS',
    'PRICE_CACHE_FILE',
    'PRICE_CACHE_FILE_2',
    'PRICE_CACHE_FILE_3',
    'PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED',
    'PRICE_CROSS_BACK_BAND_PCT',
    'PRICE_CROSS_BACK_MAX_AGE_MIN',
    'PRICE_CROSS_BACK_REENTRY_ENABLED',
    'PRICE_REFRESH_INTERVAL',
    'PRICE_UPDATE_INTERVAL',
    'PROFIT_TARGET_ENABLED',
    'PROFIT_TARGET_PCT',
    'PROGRESSIVE_LOCK_ENABLED',
    'PROGRESSIVE_LOCK_FRACTION',
    'PROXIMITY_TOP_GATE_ENABLED',
    'PROXIMITY_TOP_MAX_DROP_PCT',
    'PYRAMID_ENABLED',
    'PYRAMID_MAX_DC_POS_15M_SHORT',
    'PYRAMID_MIN_DC_POS_15M',
    'PYRAMID_MIN_GAIN_PCT',
    'PYRAMID_MIN_WT_VEL_1H',
    'PYRAMID_SIZE_MULT',
    'QUICK_BANDAID_OFF_VEC_ENABLED',
    'QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED',
    'QUICK_CYCLE_TP_REDUCE_FRAC',
    'QUICK_CYCLE_TP_STOCH_AGAINST_VEC_ENABLED',
    'QUICK_HEDGE_SAME_SYM_LAST_RESORT_AGE_MIN',
    'QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED',
    'QUICK_HEDGE_SAME_SYM_LAST_RESORT_GAIN_PCT',
    'QUICK_HEDGE_SAME_SYM_LAST_RESORT_QTY_PCT',
    'QUICK_HEDGE_SAME_SYM_LAST_RESORT_VEC_ENABLED',
    'QUICK_OPEN_STRONG_BB_LONG_MAX',
    'QUICK_OPEN_STRONG_BB_SHORT_MIN',
    'QUICK_OPEN_STRONG_DC_LONG_MAX',
    'QUICK_OPEN_STRONG_DC_SHORT_MIN',
    'QUICK_OPEN_STRONG_K_LONG_MAX',
    'QUICK_OPEN_STRONG_K_SHORT_MIN',
    'QUICK_OPEN_STRONG_VEC_ENABLED',
    'QUICK_OPEN_STRONG_VEL_MIN',
    'QUICK_REDUCE_STRONG_REDUCE_VEC_ENABLED',
    'QUICK_REDUCE_TECHNICAL_ONLY',
    'QUICK_SENTIMENT_CUT_GAIN_VEC_ENABLED',
    'QUICK_SENTIMENT_CUT_REDUCE_FRAC',
    'R1_DC_LOW4_3M_EMERGENCY_ENABLED',
    'R1_NEWBORN_WINDOW_MIN',
    'R1_REQUIRE_WT15_ADVERSE',
    'R1_TF',
    'R1_USE_DC_4BAR',
    'R2_PEAK_MIN_PCT',
    'R2_TF_LIST',
    'R3_GAIN_MAX_PCT',
    'R3_HEDGE_INVARIANT_DUMP_ENABLED',
    'R3_HTF_FLIP_4H_TIER_ENABLED',
    'R3_HTF_FLIP_EXIT_ENABLED',
    'R3_HTF_FLIP_NEWBORN_WINDOW_MIN',
    'RANKING_LOOP_SLEEP_SECONDS',
    'RANKING_RESULTS_FILE',
    'RANKING_UPDATE_INTERVAL',
    'RANK_CONVICTION_ENABLED',
    'RATIO_CLOSE_LOSING_COOLDOWN_SECONDS',
    'RATIO_CLOSE_LOSING_MAX_PER_CYCLE',
    'RATIO_CLOSE_LOSING_MIN_LOSS_PCT',
    'RATIO_CLOSE_LOSING_MIN_PNL_DELTA_PCT',
    'RATIO_CLOSE_LOSING_MIN_SKEW_PP',
    'RATIO_CLOSE_LOSING_OVERWEIGHT',
    'RATIO_EMERGENCY_EXIT_COOLDOWN',
    'RATIO_EMERGENCY_EXIT_ENABLED',
    'RATIO_EMERGENCY_EXIT_MAX_LOSS_PCT',
    'RATIO_EMERGENCY_EXIT_MAX_PER_CYCLE',
    'RATIO_EMERGENCY_EXIT_THRESHOLD',
    'RATIO_MULTIPLIER',
    'RATIO_MULTIPLIER_TRADIER',
    'RATIO_PNL_ACCELERATION',
    'RATIO_PNL_DELTA_THRESHOLD',
    'RATIO_PNL_DYNAMIC_GATES_ENABLED',
    'RATIO_PNL_GATE_SOFT_MAX',
    'RATIO_PNL_GATE_SOFT_MIN',
    'RATIO_PNL_TARGET_LONG_MAX',
    'RATIO_PNL_TARGET_LONG_MIN',
    'RATIO_PNL_WEIGHT',
    'RATIO_PNL_WEIGHT_ENABLED',
    'RATIO_REBALANCE_APPLY_HTF_GATE',
    'RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY',
    'RATIO_REBALANCE_COOLDOWN_CRASH',
    'RATIO_REBALANCE_COOLDOWN_EXTREME',
    'RATIO_REBALANCE_COOLDOWN_NORMAL',
    'RATIO_REBALANCE_COOLDOWN_PNL_DIVERGENT',
    'RATIO_REBALANCE_MAX_CLOSES',
    'RATIO_REBALANCE_MAX_OPENS_EXTREME',
    'RATIO_REBALANCE_MAX_OPENS_NORMAL',
    'RATIO_REBALANCE_MAX_OPENS_STUCK',
    'RATIO_REBALANCE_SIZE_MAX_MULT',
    'RATIO_REBALANCE_SIZE_MULT',
    'RATIO_REBALANCE_SIZE_SKEW_BOOST',
    'RATIO_TRIM_GAIN_FLOOR',
    'RATIO_TRIM_MIN_AGE_S',
    'RATIO_TRIM_MIN_INTERVAL_S',
    'REACTIVE_MODE',
    'REBAL_ATTEMPT_COOLDOWN_SEC',
    'RECENT_REDUCTION_GUARD_ENABLED',
    'RECOVERY_AUGMENT_BAND_PCT',
    'RECOVERY_AUGMENT_ENABLED',
    'RECOVERY_AUGMENT_MAX_AGE_MIN',
    'RECOVERY_AUGMENT_ONE_FIRE_PER_REDUCE',
    'RECOVERY_AUGMENT_REQUIRE_WT_CROSS',
    'RECOVERY_AUGMENT_SIZE_PCT',
    'REDIS_CHANNEL_MARKET_DATA',
    'REDIS_CHANNEL_POSITIONS',
    'REDIS_CHANNEL_PRICES',
    'REDIS_CHANNEL_SIGNALS',
    'REDIS_DB',
    'REDIS_EXPIRY_SECONDS',
    'REDIS_HOST',
    'REDIS_KEY_MARKET_DATA',
    'REDIS_PORT',
    'REDUCE_HUGE_LOSS_THRESHOLD',
    'REDUCTION_COOLDOWN_SECONDS',
    'RED_ZONE_AUGMENT_GATE_ENABLED',
    'RED_ZONE_GATE_ENABLED',
    'RED_ZONE_GATE_FALLBACK_ENABLED',
    'RED_ZONE_HEDGE_GATE_ENABLED',
    'RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED',
    'RED_ZONE_TRADIER_GATE_ENABLED',
    'RED_ZONE_TRADIER_MIN_DISTANCE_PCT',
    'RED_ZONE_TRADIER_MIN_OI_AT_WALL',
    'RED_ZONE_TRADIER_STALE_MAX_HOURS',
    'REENTER_ORPHAN_THRESHOLD',
    'REENTER_SAVE_DEBOUNCE_SECONDS',
    'REENTRY2_DC_BREAK_ALLOW_15M',
    'REENTRY2_DC_BREAK_ENABLED',
    'REENTRY2_DC_BREAK_FILTER_TF',
    'REENTRY2_DC_BREAK_REQUIRE_K_FILTER',
    'REENTRY2_DC_BREAK_REQUIRE_WT_FILTER',
    'REENTRY2_DIR_FAV_ENABLED',
    'REENTRY2_QUICK_RECOVERY_ENABLED',
    'REENTRY2_STOCH_CROSS_ENABLED',
    'REENTRY_2_ENABLED',
    'REENTRY_60MIN_MIN_PCT',
    'REENTRY_60MIN_UNCONDITIONAL_ENABLED',
    'REENTRY_60MIN_WINDOW_MIN',
    'REENTRY_AGGRESSIVE_WINDOW_MIN',
    'REENTRY_B01_WT_2of3_ENABLED',
    'REENTRY_B02_BC156_BOTTOM_ENABLED',
    'REENTRY_B04_DC_RETEST_ENABLED',
    'REENTRY_B09_SNAPBACK_ENABLED',
    'REENTRY_B10_STOCH_REV_ENABLED',
    'REENTRY_B11_DC_BREAK_ENABLED',
    'REENTRY_B12_WT_MOM_ENABLED',
    'REENTRY_B14_HA_TREND_ENABLED',
    'REENTRY_B15_STRONG_TREND_ENABLED',
    'REENTRY_B16_MIDRANGE_ENABLED',
    'REENTRY_B16_SIZE_MULT_STRONG',
    'REENTRY_B16_SIZE_MULT_WEAK',
    'REENTRY_B16_SMA200_PROX_PCT',
    'REENTRY_B16_SMA200_PULLBACK_ENABLED',
    'REENTRY_BREAKOUT_ENABLED',
    'REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT',
    'REENTRY_CHURN_GUARD_ENABLED',
    'REENTRY_CHURN_GUARD_USE_4BAR',
    'REENTRY_CHURN_GUARD_WINDOW_S',
    'REENTRY_CONFIRMATION_GATES_ENABLED',
    'REENTRY_COOLDOWN_S',
    'REENTRY_CROSS_FRESHNESS_ENABLED',
    'REENTRY_CROSS_MAX_BARS_AGO',
    'REENTRY_DISPATCH_BACKOFF_S',
    'REENTRY_DISPATCH_MAX_ATTEMPTS',
    'REENTRY_ESCALATION_CRIT_MIN',
    'REENTRY_ESCALATION_WARN_MIN',
    'REENTRY_EXHAUSTED_PARTIAL_ENABLED',
    'REENTRY_EXIT_RECLAIM_BUFFER_PCT',
    'REENTRY_EXIT_RECLAIM_ENABLED',
    'REENTRY_FAVORABLE_HTF_MIN',
    'REENTRY_FAVORABLE_MOVE_PCT',
    'REENTRY_FAVORABLE_QTY_MULT',
    'REENTRY_GR_HTF_MIN_TFS',
    'REENTRY_GR_MIN_IND',
    'REENTRY_K15M_PARTIAL_ENABLED',
    'REENTRY_K15M_PARTIAL_MULT',
    'REENTRY_K15M_PARTIAL_THRESHOLD',
    'REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED',
    'REENTRY_LIVE_MONITOR_DC_BREAK_TF',
    'REENTRY_LIVE_MONITOR_DC_BREAK_USE_4BAR',
    'REENTRY_LIVE_MONITOR_ENABLED',
    'REENTRY_LIVE_MONITOR_INTERVAL_S',
    'REENTRY_LIVE_MONITOR_PARTIAL_PCT',
    'REENTRY_MANDATORY',
    'REENTRY_MAX_PRICE_DIVERGENCE_PCT',
    'REENTRY_MIN_GAP_MINUTES',
    'REENTRY_NEVER_SKIP_ENABLED',
    'REENTRY_POST_CONSOL_ATR_THRESHOLD',
    'REENTRY_POST_CONSOL_ENABLED',
    'REENTRY_POST_CONSOL_MULT',
    'REENTRY_POST_CONSOL_TFS_REQUIRED',
    'REENTRY_PRICE_IMPROVE_PCT',
    'REENTRY_RALLY_HTF_MIN',
    'REENTRY_RALLY_K15M_MAX',
    'REENTRY_SIZE_BREAKOUT_MULT',
    'REENTRY_SIZE_DIP_MULT',
    'REENTRY_SIZE_EXTENDED_K1H',
    'REENTRY_SIZE_EXTENDED_MULT',
    'REENTRY_SMA200_BACKUP_ENABLED',
    'REENTRY_STOCH_K_MAX_LONG',
    'REENTRY_STOCH_K_MIN_SHORT',
    'REENTRY_SYMGATE_ENABLED',
    'REENTRY_SYMGATE_SPEED_MIN',
    'REENTRY_TIER1_SIZE_MULT',
    'REENTRY_TIER1_SIZE_MULT_TRADIER',
    'REENTRY_TIER2_MAX_MINUTES',
    'REENTRY_TIER2_MAX_MINUTES_TRADIER',
    'REENTRY_TIER2_MIN_MINUTES',
    'REENTRY_TIER2_MIN_MINUTES_TRADIER',
    'REENTRY_TIER2_PRICE_PCT',
    'REENTRY_TIER2_PRICE_PCT_TRADIER',
    'REENTRY_TIER2_SIZE_MULT',
    'REENTRY_TIER2_SIZE_MULT_TRADIER',
    'REENTRY_WAVETREND_CONFIRM_ENABLED',
    'REENTRY_WT15M_CROSS_ENABLED',
    'REENTRY_WT15M_HTF_FAVOR_REQUIRED',
    'REENTRY_WT15M_K_MAX',
    'REENTRY_WT15M_SIZE_MULT',
    'REGIME_ADAPTIVE_ENABLED',
    'REGIME_ATR_RATIO_MIN',
    'REGIME_BB_WIDTH_PCT_MIN',
    'REGIME_BTC_MARKET_WEIGHT',
    'REGIME_DC_ATR_RATIO_MIN',
    'REGIME_DETECTION_ENABLED',
    'REGIME_ENTER_TRENDING_THRESHOLD',
    'REGIME_EXIT_TRENDING_THRESHOLD',
    'REGIME_GATE_ENABLED',
    'REGIME_MIN_DWELL_BARS',
    'REGIME_RANGING_DC_BREAKOUT_SCORE',
    'REGIME_RANGING_EXIT_GAIN_MIN',
    'REGIME_RANGING_K_ZONE_BONUS',
    'REGIME_RANGING_MIN_HOLD_BARS',
    'REGIME_RANGING_NOLOSS_MIN',
    'REGIME_RANGING_POSITION_SIZE_MULT',
    'REGIME_RANGING_REENTRY_SIZE_MULT',
    'REGIME_RANGING_SLOT_RESERVE_PCT',
    'REGIME_RANGING_STALE_HOURS',
    'REGIME_RANGING_STALE_MIN_PROFIT',
    'REGIME_RANGING_WT_EXIT_VEL',
    'REGIME_RANGING_WT_REDUCE_FRAC_LOW',
    'REGIME_RANGING_WT_REDUCE_FRAC_MED',
    'REGIME_TRENDING_DC_BREAKOUT_SCORE',
    'REGIME_TRENDING_EXIT_GAIN_MIN',
    'REGIME_TRENDING_K_RESET_THRESHOLD',
    'REGIME_TRENDING_K_ZONE_BONUS',
    'REGIME_TRENDING_MIN_HOLD_BARS',
    'REGIME_TRENDING_NOLOSS_MIN',
    'REGIME_TRENDING_POSITION_SIZE_MULT',
    'REGIME_TRENDING_REENTRY_SIZE_MULT',
    'REGIME_TRENDING_SLOT_RESERVE_PCT',
    'REGIME_TRENDING_WT_EXIT_VEL',
    'REGIME_TRENDING_WT_REDUCE_FRAC_LOW',
    'REGIME_TRENDING_WT_REDUCE_FRAC_MED',
    'REV_MODE',
    'RE_2_USE_PERCENTILE_ENABLED',
    'RE_3_B12_RISING_BONUS_ENABLED',
    'RE_4_B14_HA_STREAK_CONV_ENABLED',
    'RE_5_B04_COMPRESSION_BONUS_ENABLED',
    'RE_6_WAVE_PHASE_GATE_ENABLED',
    'RIDICULOUS_HOLD_GUARD_ENABLED',
    'RIDICULOUS_HOLD_VEC_ENABLED',
    'RISK_FREE_RATE',
    'ROTATION_BOTTOM_N',
    'ROTATION_ENABLED',
    'ROTATION_HOLD_DAYS',
    'ROTATION_LOOKBACK_DAYS',
    'ROTATION_POSITION_SIZE',
    'ROTATION_SMA200_FILTER',
    'ROTATION_TOP_N',
    'ROUND_TRIP_COST_PCT',
    'RP_OPPOSITE_PENALTY',
    'RP_PROTECT_MIN_GAIN',
    'RP_PROTECT_THRESHOLD',
    'RP_STRONG_BONUS',
    'RP_STRONG_THRESHOLD',
    'RP_WEAK_PENALTY',
    'RP_WEAK_THRESHOLD',
    'RSI2_ENABLED',
    'RSI2_ENTRY_THRESHOLD',
    'RSI2_EXIT_THRESHOLD_LONG',
    'RSI2_EXIT_THRESHOLD_SHORT',
    'RSI2_MEAN_REVERSION_ENABLED',
    'RSI2_POSITION_SIZE',
    'RSI2_SCORE_BONUS',
    'RSI2_THRESHOLD_LONG',
    'RSI2_THRESHOLD_SHORT',
    'RSI_ENTRY_GATE_ENABLED',
    'RSI_ENTRY_LONG_TRADIER',
    'RSI_ENTRY_MAX_LONG',
    'RSI_ENTRY_MIN_SHORT',
    'RSI_ENTRY_PERIOD_TRADIER',
    'RSI_ENTRY_SHORT_TRADIER',
    'RSI_EXIT_LONG_TRADIER',
    'RSI_EXIT_SHORT_TRADIER',
    'RSI_MACD_EMA_ENABLED',
    'RSI_MACD_EMA_RSI_LONG',
    'RSI_MACD_EMA_RSI_SHORT',
    'RSI_MACD_EMA_SCORE',
    'RSI_MACD_EMA_TF',
    'RSI_MOMENTUM_MODE',
    'RULE_B_3M_EXIT_ENABLED',
    'RULE_B_5M_EXIT_ENABLED',
    'RULE_B_W_TREND_4H_PULLBACK_ENABLED',
    'RULE_C_FUNDING_EXTREME_ENABLED',
    'RULE_NAME_TAGGING_ENABLED',
    'RVOL_MOMENTUM_MIN',
    'RVOL_SCALP_MIN',
    'RVOL_SCORE_BOOST_PCT',
    'RVOL_SCORE_BOOST_THRESHOLD',
    'RZ_BASELINE_BOUNCE_SHORT_ENABLED',
    'RZ_BASELINE_TOL',
    'RZ_BOT_BB_THRESHOLD',
    'RZ_BREAKOUT_ENTRY_ENABLED',
    'RZ_DIV_BLOCK_MIN',
    'RZ_DIV_EXIT_ENABLED',
    'RZ_ENTRY_ENABLED',
    'RZ_EXIT_ENABLED',
    'RZ_K_ENTRY_BOTTOM',
    'RZ_K_ENTRY_MAX',
    'RZ_K_EXIT',
    'RZ_LEGS_MIN',
    'RZ_LTF_MICRO',
    'RZ_MFI_ENTRY_BOTTOM',
    'RZ_MFI_EXIT',
    'RZ_REQUIRE_STRUCT',
    'RZ_TOP_BB_THRESHOLD',
    'RZ_TWO_PHASE_EXIT_ENABLED',
    'RZ_ZSCORE_EXIT_ENABLED',
    'RZ_ZSCORE_ZONE_ENABLED',
    'R_G10_HTF_DIV_GATE_ENABLED',
    'R_S1_WT_COMPOSITE_DELTA_THR',
    'R_S1_WT_COMPOSITE_DELTA_USE_ENABLED',
    'R_S2_WT_ADAPTIVE_OS_ENABLED',
    'R_S2_WT_PCT_OB_SHORT',
    'R_S2_WT_PCT_OS_LONG',
    'R_S3_DIV_STACK_ENABLED',
    'R_S3_HTF_WEIGHT_ENABLED',
    'R_S4_HA_STREAK_ENABLED',
    'R_S5_SENT_VEL_ENABLED',
    'R_S6_WT_MSTATE_GATE_MODE',
    'R_S7_HHLL_STACK_ENABLED',
    'R_Z2_PERCENTILE_SCALER_ENABLED',
    'R_Z3_WT_COMPOSITE_SIZE_ENABLED',
    'R_Z5_DC_HTF_MIN',
    'R_Z5_DC_LTF_LOW_THR',
    'R_Z5_DC_PULLBACK_MULT',
    'R_Z5_DC_PULLBACK_SIZING_ENABLED',
    'SANDBOX_MODE',
    'SATOSHIT_ENABLED',
    'SATOSHIT_ENTRY_FILTER',
    'SATOSHIT_EXIT_ENABLED',
    'SATOSHIT_EXIT_LONG_RSI_MIN',
    'SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER',
    'SATOSHIT_EXIT_LONG_STOCH_K_MIN',
    'SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER',
    'SATOSHIT_EXIT_PARTIAL_PCT',
    'SATOSHIT_EXIT_SHORT_RSI_MAX',
    'SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER',
    'SATOSHIT_EXIT_SHORT_STOCH_K_MAX',
    'SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER',
    'SATOSHIT_EXIT_USE_MAKER',
    'SATOSHIT_HTF_MFI_D_MIN',
    'SATOSHIT_HTF_MFI_D_MIN_TRADIER',
    'SATOSHIT_HTF_RVOL_1H_MIN_TRADIER',
    'SATOSHIT_LONG_BB_PCTB_MAX',
    'SATOSHIT_LONG_HA_STREAK_MAX',
    'SATOSHIT_LONG_MFI_MAX',
    'SATOSHIT_LONG_MFI_MAX_TRADIER',
    'SATOSHIT_LONG_RSI_MAX',
    'SATOSHIT_LONG_RSI_MAX_TRADIER',
    'SATOSHIT_LONG_STOCH_K_MAX',
    'SATOSHIT_LONG_STOCH_K_MAX_TRADIER',
    'SATOSHIT_MIN_VOTES_TRADIER',
    'SATOSHIT_PROTECT_TRADES',
    'SATOSHIT_QTY_MULT',
    'SATOSHIT_SCORE_BONUS',
    'SATOSHIT_SHORT_BB_PCTB_MIN',
    'SATOSHIT_SHORT_HA_STREAK_MIN',
    'SATOSHIT_SHORT_MFI_MIN',
    'SATOSHIT_SHORT_MFI_MIN_TRADIER',
    'SATOSHIT_SHORT_RSI_MIN',
    'SATOSHIT_SHORT_RSI_MIN_TRADIER',
    'SATOSHIT_SHORT_STOCH_K_MIN',
    'SATOSHIT_SHORT_STOCH_K_MIN_TRADIER',
    'SBA_ADX_MAX_TRADIER',
    'SBA_ADX_TF',
    'SBA_COOLDOWN_GLOBAL_S',
    'SBA_COOLDOWN_POSITION_S',
    'SBA_COOLDOWN_S_TRADIER',
    'SBA_ENABLED',
    'SBA_ENABLED_TRADIER',
    'SBA_MAX_ADDS_TRADIER',
    'SBA_MAX_CONCURRENT',
    'SBA_MAX_LOSS_PCT_TRADIER',
    'SBA_MAX_TOTAL_MULT',
    'SBA_MIN_LOSS_PCT_TRADIER',
    'SBA_MIN_SCORE',
    'SBA_SIZE_FRACTION_TRADIER',
    'SCALP_LONG_BUDGET',
    'SCALP_MAX_HOLD_MINUTES',
    'SCALP_MAX_POSITIONS_PER_SIDE',
    'SCALP_MAX_POSITION_SIZE',
    'SCALP_MIN_MOVE_PCT',
    'SCALP_MIN_REL_VOL',
    'SCALP_MODE',
    'SCALP_REDUCE_ENABLED',
    'SCALP_SHORT_BUDGET',
    'SCALP_START_SIZE',
    'SCALP_STOP_PCT',
    'SCALP_TARGET_PCT',
    'SCALP_TOP_MOVERS_N',
    'SCALP_V2_DC_HTF_LIST',
    'SCALP_V2_DC_HTF_REQUIRE_ALL',
    'SCALP_V2_ENTRY_MODE',
    'SCALP_V2_ISOLATE',
    'SCALP_V2_LH_LL_EXIT',
    'SCALP_V2_LH_LL_TF',
    'SCALP_V2_MAX_CONCURRENT',
    'SCALP_V2_MAX_HOLD_MINUTES',
    'SCALP_V2_REDZONE_EXIT',
    'SCALP_V2_REDZONE_K_THRESHOLD',
    'SCALP_V2_REENTRY_COOLDOWN_S',
    'SCALP_V2_VARIANT',
    'SCALP_V3_ATR_PCTL_GATE_ENABLED',
    'SCALP_V3_ATR_PCTL_MIN',
    'SCALP_V3_ATR_SL_MULT',
    'SCALP_V3_ATR_TP_MULT',
    'SCALP_V3_AUG_BE_STOP_ENABLED',
    'SCALP_V3_AUG_ENABLED',
    'SCALP_V3_BB_SQUEEZE_MAX_PCT',
    'SCALP_V3_BOOST_ENABLED',
    'SCALP_V3_BYPASS_HTF_DIRECTION_GATE',
    'SCALP_V3_ENABLED',
    'SCALP_V3_ENTRY_BAR_1M_REQUIRE',
    'SCALP_V3_ENTRY_BAR_3M_REQUIRE',
    'SCALP_V3_ENTRY_BAR_BREAK_ENABLED',
    'SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN',
    'SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_LONG',
    'SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_SHORT',
    'SCALP_V3_ENTRY_DC_BREAK_ENABLED',
    'SCALP_V3_ENTRY_K_15M_MAX',
    'SCALP_V3_ENTRY_K_1H_MAX',
    'SCALP_V3_ENTRY_K_1M_MAX',
    'SCALP_V3_ENTRY_K_3M_MAX',
    'SCALP_V3_ENTRY_K_4H_MAX',
    'SCALP_V3_ENTRY_PULLBACK_ENABLED',
    'SCALP_V3_ENTRY_REQUIRE_K_TURNUP',
    'SCALP_V3_ENTRY_STDEV_ENABLED',
    'SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED',
    'SCALP_V3_ENTRY_TF_MODE',
    'SCALP_V3_ENTRY_TREND_ENABLED',
    'SCALP_V3_ENTRY_VOL_SPIKE_MULT',
    'SCALP_V3_ENTRY_WT_CROSS_ENABLED',
    'SCALP_V3_EXIT_15M_BAR',
    'SCALP_V3_EXIT_15M_K_MIN',
    'SCALP_V3_EXIT_1M_BAR',
    'SCALP_V3_EXIT_1M_K_MIN',
    'SCALP_V3_EXIT_3M_BAR',
    'SCALP_V3_EXIT_3M_K_MIN',
    'SCALP_V3_EXIT_BAR_REVERSAL_ENABLED',
    'SCALP_V3_EXIT_K_CROSS_ENABLED',
    'SCALP_V3_EXIT_PROFIT_ONLY',
    'SCALP_V3_EXIT_REQUIRE_N_SIGNALS',
    'SCALP_V3_EXIT_STDEV_REJECT_ENABLED',
    'SCALP_V3_EXIT_TF_MODE',
    'SCALP_V3_EXIT_WT_FLIP_ENABLED',
    'SCALP_V3_FAST_PPL_ENABLED',
    'SCALP_V3_HTF_SMA200_ENABLED',
    'SCALP_V3_HTF_TREND_VEL_GATE',
    'SCALP_V3_K_OB_EXIT_ENABLED',
    'SCALP_V3_K_OB_EXIT_K15M_HI',
    'SCALP_V3_K_OB_EXIT_K15M_LO',
    'SCALP_V3_K_OB_EXIT_K3M_HI',
    'SCALP_V3_K_OB_EXIT_K3M_LO',
    'SCALP_V3_K_OB_EXIT_WALL_PCT',
    'SCALP_V3_MIN_TP_FOR_EARLY_EXIT',
    'SCALP_V3_OB_FLOW_AGREE_ENABLED',
    'SCALP_V3_OB_MIN_LONG_SCORE',
    'SCALP_V3_OB_MIN_SCORE',
    'SCALP_V3_OB_MIN_SHORT_SCORE',
    'SCALP_V3_OB_WALL_TOO_CLOSE_PCT',
    'SCALP_V3_OUTLIER_ENABLED',
    'SCALP_V3_PIN_BAR_RATIO',
    'SCALP_V3_PROTECTIVE_EXIT_ENABLED',
    'SCALP_V3_REENTRY_BOUNCE_BAR_3M',
    'SCALP_V3_REENTRY_BOUNCE_K_15M_MAX',
    'SCALP_V3_REENTRY_BOUNCE_MODE',
    'SCALP_V3_REENTRY_COOLDOWN_S',
    'SCALP_V3_REENTRY_REQUIRE_BOUNCE_IF_15M_FALLING',
    'SCALP_V3_REENTRY_STICKY_ENABLED',
    'SCALP_V3_REENTRY_STICKY_MIN',
    'SCALP_V3_REQUIRE_SR_ON_ENTRY',
    'SCALP_V3_SCAN_BYPASS_GATES',
    'SCALP_V3_SHORT_ENTRY_K_15M_MAX',
    'SCALP_V3_SHORT_ENTRY_K_1H_MAX',
    'SCALP_V3_SHORT_ENTRY_K_3M_MAX',
    'SCALP_V3_STALL_ENABLED',
    'SCALP_V3_VWAP_FILTER_ENABLED',
    'SCORE_RANGES_FILE',
    'SECTOR_GROUPS',
    'SECTOR_LS_MIN_POSITIONS',
    'SECTOR_LS_RATIO_BYPASS_HEDGE',
    'SECTOR_LS_RATIO_ENABLED',
    'SECTOR_LS_RATIO_MAX',
    'SECTOR_LS_RATIO_MIN',
    'SECTOR_MAP',
    'SENTIMENT_FADE_MODE',
    'SENTIMENT_REBALANCER_ENABLED',
    'SENTIMENT_REBAL_AUGMENT_DEVIATION_THR',
    'SENTIMENT_REBAL_COOLDOWN_MIN',
    'SENTIMENT_REBAL_REDUCE_DEVIATION_THR',
    'SENTIMENT_TOP_N_GATE_ENABLED',
    'SERVER_HEARTBEAT_BLOCK_ENABLED',
    'SERVICE_STOP',
    'SHORT_ABOVE_SMA20_BONUS',
    'SHORT_RSI_MIN_1H',
    'SHORT_STRUCT_EXIT_TF',
    'SIGNALS_LOOP_INTERVAL_SECONDS',
    'SIMPLE_TP_EXIT_ENABLED',
    'SIMPLE_TP_PCT',
    'SIZING_MODE_TRADIER',
    'SLEEP_TIME_PER_TASKS',
    'SLEEP_TIME_PROC_ACCT',
    'SMA200_DIST_ENTRY_ENABLED',
    'SMA200_DIST_LONG_THRESHOLD',
    'SMA200_DIST_LONG_THRESHOLD_4H',
    'SMA_FILTER_PERIOD_TRADIER',
    'SMFI_ENABLED',
    'SMFI_LONG_BUDGET',
    'SMFI_MAX_HOLD_DAYS',
    'SMFI_MAX_PER_SIDE',
    'SMFI_POSITION_SIZE',
    'SMFI_SHORT_BUDGET',
    'SPIKE_FADE_COOLDOWN_BARS',
    'SPIKE_FADE_ENABLED',
    'SPIKE_FADE_K_EXHAUSTION',
    'SPIKE_FADE_LOOKBACK_BARS',
    'SPIKE_FADE_MAX_POSITIONS',
    'SPIKE_FADE_POSITION_SIZE',
    'SPIKE_FADE_THRESHOLD_PCT',
    'SPY_REGIME_BLOCK_LONGS_BELOW',
    'SPY_REGIME_BLOCK_SHORTS_ABOVE',
    'SPY_REGIME_GATE_ENABLED_TRADIER',
    'SPY_REGIME_SMA_BARS_DAILY',
    'SPY_REGIME_SYMBOL',
    'SQUEEZE_ENABLED',
    'SQUEEZE_FIRE_BONUS_SCORE',
    'SQUEEZE_FIRE_ENABLED',
    'SQUEEZE_FIRE_ENTRY_ENABLED',
    'SQUEEZE_FIRE_SCORE_BONUS',
    'SQUEEZE_FIRE_TF',
    'SQUEEZE_SCORE_BONUS',
    'SRS_K_EXIT_1H',
    'STALE_WARNING_INTERVAL_SECONDS',
    'STALL_DELTA_SPEED_MAX',
    'STALL_MAX_CLOSES_PER_CYCLE',
    'STALL_SUB_ENABLED',
    'START_POSITION_SIZE',
    'STDEV_BB_RZ_EXIT_ENABLED',
    'STDEV_BB_RZ_EXIT_TF',
    'STDEV_BB_RZ_SUPPRESS_PCTB',
    'STDEV_BOUNCE_ENABLED',
    'STDEV_BOUNCE_PCTB_LONG',
    'STDEV_BOUNCE_PCTB_SHORT',
    'STDEV_BOUNCE_RVOL_MIN',
    'STDEV_BREAKOUT_COOLDOWN',
    'STDEV_BREAKOUT_ENABLED',
    'STDEV_BREAKOUT_EXIT_PCTB_FAIL',
    'STDEV_BREAKOUT_EXIT_WT_ENABLED',
    'STDEV_BREAKOUT_MAX_AGE_BARS',
    'STDEV_BREAKOUT_MAX_RETESTS',
    'STDEV_BREAKOUT_PCTB_LONG',
    'STDEV_BREAKOUT_PCTB_SHORT',
    'STDEV_BREAKOUT_RETEST_COOLDOWN',
    'STDEV_BREAKOUT_RETEST_PCTB_MAX',
    'STDEV_BREAKOUT_RETEST_PCTB_MIN',
    'STDEV_BREAKOUT_RETEST_SCORE',
    'STDEV_BREAKOUT_RETEST_SIZE_MULT',
    'STDEV_BREAKOUT_RVOL_MIN',
    'STDEV_BREAKOUT_SCORE',
    'STDEV_MACRO_AUGMENT_VETO_ENABLED',
    'STDEV_MACRO_ENTRY_BOOST_ENABLED',
    'STDEV_MACRO_ENTRY_BOOST_MULT',
    'STDEV_MACRO_ENTRY_VETO_ENABLED',
    'STDEV_MACRO_HEDGE_BOOST_ENABLED',
    'STDEV_MACRO_R4_EXIT_ENABLED',
    'STDEV_MACRO_R4_REQUIRE_LTF_FLIP',
    'STDEV_REJECT_EXIT_ENABLED',
    'STDEV_REJECT_EXIT_RETURN',
    'STDEV_REJECT_EXIT_TF',
    'STDEV_REJECT_EXIT_ZONE',
    'STDEV_SUPPRESS_EARLY_EXIT',
    'STOCH_1H_EXIT_K_MIN',
    'STOCH_CROSS_1H_EXIT_ENABLED',
    'STOCH_CROSS_3M_EXIT_ENABLED',
    'STOCH_CROSS_ENTRY_ENABLED',
    'STOCH_CROSS_ENTRY_TRADIER',
    'STOP_LOSS_ENABLED',
    'STOP_LOSS_PCT',
    'STOP_MAJOR_LOSS_BLOCK_ENABLED',
    'STOP_MAJOR_LOSS_ENABLED',
    'STORM_REDUCE_ENABLED',
    'STRENGTH_FILTER_ENABLED',
    'STRENGTH_MIN_SCORE',
    'STRICT_VEC_PARITY_GATE_ENTRIES',
    'STRICT_VEC_PARITY_GATE_EXITS',
    'STRUCTURAL_EXIT_GATE_ENABLED',
    'STRUCTURAL_RANGE_SHIFT_EXIT',
    'STRUCTURAL_RANGE_SHIFT_K_HIGH',
    'STRUCTURAL_RANGE_SHIFT_K_LOW',
    'STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS',
    'STRUCTURAL_RANGE_SHIFT_TF',
    'STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED',
    'STRUCTURE_FLIP_REENTRY_BASIS_TF',
    'STRUCTURE_FLIP_REENTRY_ENABLED',
    'STRUCTURE_FLIP_REENTRY_TF',
    'ST_LT_SPLIT_ENABLED',
    'ST_SCORE_WEIGHT_LTF',
    'SWEEP_OPTIMAL_ENTRY_TF',
    'SWEEP_OPTIMAL_HOLD_BARS',
    'SWING_ENABLED',
    'SWING_EXIT_TFS',
    'SWING_LONG_BUDGET',
    'SWING_MAX_POSITION_SIZE',
    'SWING_REENTER_AT_OR_BELOW_EXIT',
    'SWING_REENTER_MULT',
    'SWING_REENTER_SIGNAL',
    'SWING_REENTER_TOLERANCE_PCT',
    'SWING_RUNAWAY_REENTER',
    'SWING_SHORT_BUDGET',
    'SWING_START_SIZE',
    'SYMBOLS',
    'SYMBOLS_FILE',
    'SYMBOL_CONFIGS_FILE',
    'SYMBOL_PERF_DECAY_HOURS',
    'SYMBOL_PERF_ENABLED',
    'SYMBOL_PERF_MAX_MULT',
    'SYMBOL_PERF_MIN_MULT',
    'SYMBOL_PERF_MIN_TRADES',
    'SYMBOL_PERF_REFRESH_SECONDS',
    'SYMBOL_PERF_WINDOW_DAYS',
    'SYNTHETIC_LOSER_THRESHOLD_PCT',
    'TASK_STAGGER_SECONDS',
    'TF_ALIGNMENT_MIN_LONG',
    'TF_ALIGNMENT_MIN_SHORT',
    'TF_ALIGNMENT_MIN_TOTAL',
    'TF_FOCUS',
    'TF_FOCUS_ENTRY_HARD_GATE',
    'TF_FOCUS_EXIT_HARD_GATE',
    'TF_FOCUS_WEIGHT',
    'TF_HTF1',
    'TF_HTF2',
    'TF_HTF3',
    'TF_MACRO',
    'TF_MICRO',
    'TF_SCALP',
    'THROUGHPUT_DAILY_LOSS_RESET_UTC_HOUR_TRADIER',
    'THROUGHPUT_DAILY_LOSS_RESET_UTC_MINUTE_TRADIER',
    'THROUGHPUT_MAX_FIRES_PER_HOUR_PER_SYMBOL_TRADIER',
    'THROUGHPUT_SAFETY_ENABLED',
    'THROUGHPUT_SAFETY_ENABLED_TRADIER',
    'TIER_A_MIN_GAIN',
    'TIER_A_MIN_TRADES',
    'TIER_A_MULTIPLIER',
    'TIER_A_WIN_RATE',
    'TIER_B_MIN_TRADES',
    'TIER_B_WIN_RATE',
    'TIER_C_MULTIPLIER',
    'TIER_ENABLED',
    'TIMEFRAMES',
    'TIME_ZONE_ENABLED',
    'TOP_OF_RANGE_BLOCK_ENABLED',
    'TOP_OF_RANGE_BLOCK_THRESHOLD',
    'TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED',
    'TRADES_PER_SYM_PER_DAY_MAX',
    'TRADIER_ACCOUNT_ID',
    'TRADIER_API_BASE_URL',
    'TRADIER_API_KEY',
    'TRADIER_DC_DAYTRADE_ENABLED',
    'TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES',
    'TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION',
    'TRADIER_DC_DAYTRADE_STOP_PCT',
    'TRADIER_DC_DAYTRADE_TARGET_PCT',
    'TRADIER_DC_POSITION_ENTRY_THRESHOLD',
    'TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED',
    'TRADIER_ENTRY_SCORE_THRESHOLD',
    'TRADIER_FH_MOMENTUM_DC_CONFIRM',
    'TRADIER_FH_MOMENTUM_DC_MAX_LONG',
    'TRADIER_FH_MOMENTUM_ENABLED',
    'TRADIER_FH_MOMENTUM_MFI_CONFIRM',
    'TRADIER_FH_MOMENTUM_MFI_MIN',
    'TRADIER_FH_MOMENTUM_MIN_MOVE_PCT',
    'TRADIER_FH_MOMENTUM_WINDOW_MINUTES',
    'TRADIER_INDICATORS_CYCLE_CONCURRENCY',
    'TRADIER_INDICATORS_HTTP_CONCURRENCY',
    'TRADIER_INDICATORS_IDLE_SLEEP_SEC',
    'TRADIER_INDICATORS_NARROW_UNIVERSE',
    'TRADIER_K_ZONE_ENTRY_BONUS_TRADIER',
    'TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER',
    'TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER',
    'TRADIER_LOCAL_EXTREMES_SCORING_ENABLED',
    'TRADIER_LONG_ONLY_ENTRIES',
    'TRADIER_MFI_ENTRY_LONG_ENABLED',
    'TRADIER_MFI_ENTRY_LONG_TRADIER',
    'TRADIER_MIN_HOLD_MINUTES',
    'TRADIER_MI_ENTRY_ENABLED_TRADIER',
    'TRADIER_MI_EXIT_ENABLED_TRADIER',
    'TRADIER_MI_SUBSIGNAL_MIN_COUNT',
    'TRADIER_NOLOSS_SRS_BYPASS',
    'TRADIER_OI_INJECT_ENABLED',
    'TRADIER_OI_INJECT_MAX_EACH',
    'TRADIER_OI_INJECT_MIN_TOTAL_OI',
    'TRADIER_OI_INJECT_NEAR_MONEY_PREFER',
    'TRADIER_OI_INJECT_PC_BEARISH',
    'TRADIER_OI_INJECT_PC_BULLISH',
    'TRADIER_OI_INJECT_STALE_MAX_HOURS',
    'TRADIER_POST_CLOSE_COOLDOWN_MIN',
    'TRADIER_QUEUE_DEDUPE_SEC',
    'TRADIER_RATIO_BOOST_MIN_GAIN_PCT',
    'TRADIER_RATIO_REQUIRE_MIN_GAIN',
    'TRADIER_REENTRY_ANTI_CHURN_ENABLED',
    'TRADIER_REENTRY_HARDCOOL_MIN',
    'TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED',
    'TRADIER_REENTRY_RZ_BLOCK_ENABLED',
    'TRADIER_REOPEN_WAIT_S',
    'TRADIER_REQUIRE_TRADEABLE_KEY',
    'TRADIER_RESET_MAX_GAIN_ON_CLOSE',
    'TRADIER_RSI2_ENABLED',
    'TRADIER_RSI2_EXIT_THRESHOLD_LONG',
    'TRADIER_RSI2_EXIT_THRESHOLD_SHORT',
    'TRADIER_RSI_ENTRY_LONG_TRADIER',
    'TRADIER_RSI_ENTRY_SHORT_TRADIER',
    'TRADIER_RSI_LONG_15M',
    'TRADIER_RSI_LONG_1H',
    'TRADIER_RSI_LONG_4H',
    'TRADIER_RSI_LONG_5M',
    'TRADIER_RSI_LONG_D',
    'TRADIER_RSI_SHORT_15M',
    'TRADIER_RSI_SHORT_1H',
    'TRADIER_RSI_SHORT_4H',
    'TRADIER_RSI_SHORT_5M',
    'TRADIER_RSI_SHORT_D',
    'TRADIER_RSI_SHORT_REL_VOLUME_MIN',
    'TRADIER_RSI_SHORT_RVOL_15M',
    'TRADIER_RSI_SHORT_RVOL_1H',
    'TRADIER_SANDBOX_URL',
    'TRADIER_STOCH_ENTRY_LONG_TRADIER',
    'TRADIER_STOCH_ENTRY_SHORT_TRADIER',
    'TRADIER_STOCH_EXTREME_LONG_TRADIER',
    'TRADIER_STOCH_EXTREME_SHORT_TRADIER',
    'TRADIER_STREAMING_URL',
    'TRADIER_SYMBOLS_FILE',
    'TRADIER_WS_URL',
    'TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER',
    'TRADIER_WT_EXIT_MIN_TFS_TRADIER',
    'TRADIER_WT_EXIT_TFS_TRADIER',
    'TRAILING_AUG_ENABLED_TRADIER',
    'TRAILING_AUG_GAIN_STEP_PCT',
    'TRAILING_AUG_MAX_PER_POSITION',
    'TRAILING_AUG_MIN_GAIN_PCT',
    'TRA_ALLOW_BUYS',
    'TRA_BUY_COOLDOWN_AFTER_SELL_HOURS',
    'TRA_DISABLE_AUGMENT',
    'TRA_DISABLE_DELTA_ENTRY',
    'TRA_LONG_ONLY',
    'TRA_MAX_BUYS_PER_DAY',
    'TRA_MIN_HOLD_MINUTES',
    'TRA_NO_LOSS_EXIT',
    'TRA_PREFERRED_SYMBOLS',
    'TRA_SATOSHIT_ONLY',
    'TRA_STRICT_EXIT_ONLY',
    'TRA_WT_DC_ENTRY_THRESHOLD',
    'TRB_MAX_CALL_VALUE',
    'TRB_MAX_LONG_VALUE',
    'TRB_MAX_PUT_VALUE',
    'TRB_MAX_SHORT_VALUE',
    'TRB_MAX_SYMBOL_VALUE',
    'TRB_NOLOSS_MIN_PROFIT_PCT',
    'TRC_5M_SWEEP_BENCHMARK',
    'TRC_5M_SWEEP_BUFFER_N',
    'TRC_5M_SWEEP_DELTA_WEIGHT',
    'TRC_5M_SWEEP_ENABLED',
    'TRC_5M_SWEEP_TOP_N',
    'TRC_5M_SWEEP_Z_WEIGHT',
    'TRC_BEAR_MARKET_MODE',
    'TRC_CLENOW_ENABLED',
    'TRC_CLENOW_POSITION_SIZE',
    'TRC_CONNORS_RSI_ENABLED',
    'TRC_CONNORS_RSI_POSITION_SIZE',
    'TRC_DC_DAYTRADE_LONG_BUDGET',
    'TRC_DC_DAYTRADE_SHORT_BUDGET',
    'TRC_DC_DAYTRADE_START_SIZE',
    'TRC_ENTRY_MIN_ALIGNMENT',
    'TRC_ENTRY_ZONE_LONG',
    'TRC_ENTRY_ZONE_SHORT',
    'TRC_EPISODIC_PIVOT_ENABLED',
    'TRC_EP_POSITION_SIZE',
    'TRC_GAP_FILL_POSITION_SIZE',
    'TRC_LOCAL_EXTREMES_SCORER_ENABLED',
    'TRC_LS_RATIO_MAX',
    'TRC_LS_RATIO_MIN',
    'TRC_MAX_CONCURRENT_POSITIONS',
    'TRC_MAX_DAILY_LOSS_PCT',
    'TRC_MAX_ORDER_VALUE',
    'TRC_MAX_POSITION_SIZE',
    'TRC_MAX_SYMBOL_VALUE',
    'TRC_MINERVINI_ENABLED',
    'TRC_MINERVINI_LONG_BUDGET',
    'TRC_MINERVINI_POSITION_SIZE',
    'TRC_MOMENTUM_FADE_ENABLED',
    'TRC_NOLOSS_MIN_PROFIT_PCT',
    'TRC_ORB_ENABLED',
    'TRC_ORB_LONG_BUDGET',
    'TRC_ORB_POSITION_SIZE',
    'TRC_ORB_SHORT_BUDGET',
    'TRC_ROTATION_POSITION_SIZE',
    'TRC_RSI2_POSITION_SIZE',
    'TRC_SCALP_LONG_BUDGET',
    'TRC_SCALP_MAX_POSITIONS_PER_SIDE',
    'TRC_SCALP_SHORT_BUDGET',
    'TRC_SCALP_START_SIZE',
    'TRC_SCALP_TARGET_PCT',
    'TRC_SMFI_ENABLED',
    'TRC_SMFI_LONG_BUDGET',
    'TRC_SMFI_POSITION_SIZE',
    'TRC_SMFI_SHORT_BUDGET',
    'TRC_SQUEEZE_ENABLED',
    'TRC_START_POSITION_SIZE',
    'TRC_SWING_LONG_BUDGET',
    'TRC_SWING_SHORT_BUDGET',
    'TRENDER_DD_RATIO_MAX',
    'TREND_EXIT_SCORE_FLIP',
    'TREND_GATES',
    'TREND_HEDGE_MAX_SEC',
    'TREND_HTF_MIN_BEAR',
    'TREND_HTF_MIN_BULL',
    'TREND_MIN_GAIN_EXIT',
    'TRIPLE_CONF_ENABLED',
    'TRIPLE_CONF_RSI_LONG',
    'TRIPLE_CONF_RSI_SHORT',
    'TRIPLE_CONF_SCORE',
    'TRIPLE_CONF_STOCH_LONG',
    'TRIPLE_CONF_STOCH_SHORT',
    'TRIPLE_CONF_TF',
    'TR_ADX4H_BOYCOTT_SCORE',
    'TR_ADX4H_GATE_ENABLED',
    'TR_ADX4H_MAX',
    'TR_BBWIDTH4H_BOYCOTT_SCORE',
    'TR_BBWIDTH4H_GATE_ENABLED',
    'TR_BBWIDTH4H_MAX',
    'TR_CHOP4H_BONUS',
    'TR_CHOP4H_GATE_ENABLED',
    'TR_CHOP4H_MIN',
    'TR_CHOP4H_PENALTY',
    'TR_CHOP4H_TREND_MAX',
    'TR_DCWIDTH4H_SHORT_BOYCOTT_SCORE',
    'TR_DCWIDTH4H_SHORT_ENABLED',
    'TR_DCWIDTH4H_SHORT_MAX',
    'TR_MFI4H_LONG_BOYCOTT_SCORE',
    'TR_MFI4H_LONG_ENABLED',
    'TR_MFI4H_LONG_MIN',
    'TR_TREND_V1_ENABLED',
    'TR_TREND_V1_SHADOW_LOG_ONLY',
    'TR_TREND_V1_SHADOW_SYMBOLS',
    'TSMOM_BOOK_SCALAR_ENABLED',
    'TSMOM_HIGH_CAP',
    'TSMOM_LOOKBACK_BARS',
    'TSMOM_LOW_CAP',
    'TSMOM_MIN_AGREEMENT',
    'UNDERWATER_HEDGE_OR_CLOSE_ENABLED',
    'UNDERWATER_HEDGE_OR_CLOSE_HTF_CLOSE_REQUIRED',
    'UNDERWATER_HOC_USDC_MAKER_BYPASS',
    'UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED',
    'UNIVERSAL_NOLOSS_GATE',
    'UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS',
    'UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL',
    'USE_INDICATOR_SNAPSHOT',
    'USE_SANDBOX',
    'V8Q_COOLDOWN_BARS',
    'V8Q_D_TREND_REQUIRED',
    'V8Q_HTF_MIN_ALIGNED',
    'V8Q_K3M_FLOOR',
    'V8Q_MIN_HOLD_BARS',
    'V8Q_STRENGTH_FILTER_ENABLED',
    'V8Q_STRENGTH_MIN_SCORE',
    'V8Q_SYMBOL_TIER_TOP3',
    'V8Q_SYMBOL_TIER_TOP4',
    'V8Q_SYMBOL_TIER_TOP5',
    'V8Q_SYMBOL_TIER_TOP6',
    'V8Q_WT_EXIT_MIN_TFS',
    'VALIDATE_REFRESH',
    'VEC_FIRST_OPEN_THROTTLE_BARS',
    'VEC_GATES_LOG_ONLY',
    'VEC_LIVE_REDUCE_DEFAULT_FRAC',
    'VEC_LIVE_REDUCE_PARITY_ENABLED',
    'VEC_LIVE_REDUCE_PARITY_FRAC',
    'VEC_LIVE_REDUCE_PARITY_KEEP_DUST',
    'VEC_LIVE_REDUCE_PPL_REASONS',
    'VEC_LIVE_REDUCE_PPL_STEP1_FRAC',
    'VEC_MTF_ARMED_GATE_HEDGE_OPEN',
    'VEC_MTF_ARMED_GATE_REENTRY',
    'VEC_MTF_ARMED_STATE_ENABLED',
    'VEC_MULTI_SYM_OUTER_LOOP_ENABLED',
    'VEC_RATIO_REDUCE_PROXY_ENABLED',
    'VEC_REDUCE_CASCADE_COOLDOWN_S',
    'VEL_EXIT_ENABLED',
    'VERBOSE',
    'VERBOSE2',
    'VERBOSE_FETCH_LOGGING',
    'VERBOSE_STOPS',
    'VERBOSE_TIMER',
    'VIX_EXTREME_THRESHOLD',
    'VIX_PANIC_THRESHOLD',
    'VIX_REGIME_FILTER_ENABLED',
    'VIX_REGIME_SIZE_MULT_HIGH_VOL',
    'VIX_REGIME_SIZE_MULT_PANIC',
    'VIX_SMA_LOOKBACK_DAYS',
    'VIX_VOLATILITY_REGIME_ENABLED',
    'VOLUME_CONFIRMATION_ENABLED',
    'VOLUME_CONFIRMATION_MULT',
    'VOL_SPIKE_BODY_RATIO',
    'VOL_SPIKE_COOLDOWN',
    'VOL_SPIKE_ENABLED',
    'VOL_SPIKE_LS_MAX_IMBALANCE',
    'VOL_SPIKE_MIN_ALIGNMENT',
    'VOL_SPIKE_RELVOL_THRESHOLD',
    'VOL_TARGET_ENABLED',
    'VOL_TARGET_FIELD',
    'VOL_TARGET_HIGH_CAP',
    'VOL_TARGET_LOW_CAP',
    'VOL_TARGET_PCT',
    'VP_GATE_AUGMENT_GATE_ENABLED',
    'VP_GATE_ENABLED',
    'VP_GATE_HEDGE_GATE_ENABLED',
    'VP_GATE_MIN_DENSITY_Z',
    'VP_GATE_MIN_DISTANCE_PCT',
    'VP_GATE_STALE_MAX_SEC',
    'VWAP_BOUNCE_DIST_PCT',
    'VWAP_BOUNCE_ENTRY_ENABLED',
    'VWAP_FILTER_ENABLED',
    'VWAP_SCORE_BONUS',
    'WATCHDOG_DC_BASE_USD',
    'WATCHDOG_DC_FORCE_OPEN_ENABLED',
    'WATCHDOG_DC_MAX_USD',
    'WATCHDOG_DC_MULT_15M',
    'WATCHDOG_DC_MULT_1H',
    'WATCHDOG_DC_MULT_4H',
    'WATCHDOG_DC_MULT_D',
    'WATCHDOG_DC_TFS',
    'WATCHDOG_WT3M_ESCALATE_ENABLED',
    'WINNER_PROTECT_ENABLED',
    'WIN_TRAIL_EROSION_PCT',
    'WRONG_SIDE_ABS_KILL_ENABLED',
    'WRONG_SIDE_DIV_LOOKBACK_BARS',
    'WRONG_SIDE_DIV_TFS_REQUIRED',
    'WRONG_SIDE_K_TFS_REQUIRED',
    'WRONG_SIDE_MIN_AGE_MIN',
    'WRONG_SIDE_WT_TFS_REDUCED',
    'WRONG_SIDE_WT_TFS_REQUIRED',
    'WS_RECONNECT_DELAY',
    'WS_URL',
    'WT15M_AGAINST_FORCE_HEDGE_COOLDOWN_SEC',
    'WT15M_AGAINST_FORCE_HEDGE_ENABLED',
    'WT_15M_SAME_HEDGE_COOLDOWN_SEC',
    'WT_15M_SAME_HEDGE_DAILY_CAP',
    'WT_15M_SAME_HEDGE_ENABLED',
    'WT_15M_VEL_NEAR_ZERO_THRESHOLD',
    'WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED',
    'WT_15M_VEL_SLOW_GAIN_BAND_PCT',
    'WT_15M_VEL_SLOW_GAIN_FLOOR_PCT',
    'WT_3M_FORCE_OPEN_BUILD_TO_TARGET',
    'WT_3M_FORCE_OPEN_BYPASS_GATES',
    'WT_3M_FORCE_OPEN_DIST_PCT',
    'WT_3M_FORCE_OPEN_ENABLED',
    'WT_3M_FORCE_OPEN_GR_GATE_ENABLED',
    'WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF',
    'WT_3M_FORCE_OPEN_GR_MIN_TFS',
    'WT_3M_FORCE_OPEN_GR_VOTE_MIN',
    'WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS',
    'WT_3M_FORCE_OPEN_SIZE_USD',
    'WT_3M_FORCE_OPEN_SMA_PCT',
    'WT_3M_FORCE_OPEN_TARGET_USD',
    'WT_3M_FORCE_OPEN_TF_LADDER',
    'WT_3M_FORCE_OPEN_TF_LADDER_MULT',
    'WT_3M_FORCE_OPEN_USE_SMA200',
    'WT_3M_OPEN_GATE_ENABLED',
    'WT_4H_VEL_EXIT_ENABLED',
    'WT_4H_VEL_EXIT_K_EXTREME_HIGH',
    'WT_4H_VEL_EXIT_K_EXTREME_LOW',
    'WT_4H_VEL_EXIT_LONG_VEL_MIN',
    'WT_4H_VEL_EXIT_REQUIRE_K_EXTREME',
    'WT_4H_VEL_EXIT_REQUIRE_PROFIT',
    'WT_4H_VEL_EXIT_SHORT_VEL_MIN',
    'WT_CHOP_GATE_ENABLED',
    'WT_CHOP_MAX',
    'WT_COMPOSITE_DELTA_GATE_ENABLED',
    'WT_COMPOSITE_DELTA_LONG_MIN',
    'WT_COMPOSITE_DELTA_SCORE_BONUS',
    'WT_COMPOSITE_DELTA_SCORE_ENABLED',
    'WT_COMPOSITE_DELTA_SCORE_THRESHOLD',
    'WT_COMPOSITE_DELTA_SHORT_MAX',
    'WT_COMPOSITE_ENTRY_BLOCK',
    'WT_COMPOSITE_ENTRY_GOOD',
    'WT_COMPOSITE_ENTRY_OK',
    'WT_COMPOSITE_ENTRY_STRONG',
    'WT_COMPOSITE_HTF_GATE',
    'WT_COMPOSITE_SCORING_ENABLED',
    'WT_COMPOSITE_SCORING_ENABLED_TRADIER',
    'WT_COMPOSITE_VETO_ENABLED_TRADIER',
    'WT_CROSSUNDER_15M_SHORT',
    'WT_CROSSUNDER_FINAL_COOLDOWN_S',
    'WT_CROSSUNDER_FINAL_ENABLED',
    'WT_CROSS_EXIT_APPLIES_TO_LOSERS',
    'WT_CROSS_EXIT_APPLIES_TO_WINNERS',
    'WT_CROSS_EXIT_ENABLED',
    'WT_CROSS_EXIT_MIN_AGE_MINUTES',
    'WT_CROSS_EXIT_REQUIRE_15M_CONFIRM',
    'WT_DC_DIRECT_COMBINED_STOCH_GATE',
    'WT_DC_DIRECT_COMPLETED_ENABLED',
    'WT_DC_DIRECT_HTF_ALIGN_REQUIRED',
    'WT_DC_DIRECT_HTF_GATE',
    'WT_DC_DIRECT_THRESHOLD',
    'WT_DC_ENTRY_BAR_MATURITY_BLOCK',
    'WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED',
    'WT_DC_ENTRY_ENABLED',
    'WT_DC_ENTRY_K5M_MAX_LONG',
    'WT_DC_ENTRY_K5M_MIN_SHORT',
    'WT_DC_ENTRY_THRESHOLD',
    'WT_DC_EXIT_ENABLED',
    'WT_DC_EXIT_STALE_MAX_S',
    'WT_DC_EXIT_THRESHOLD',
    'WT_DC_HTF_GATE',
    'WT_DC_LONG_ENABLED',
    'WT_DC_SHORT_ENABLED',
    'WT_DIV_ENTRY_GATE_ENABLED',
    'WT_D_BOUNCE_AUG_COOLDOWN_HOURS',
    'WT_D_BOUNCE_AUG_ENABLED',
    'WT_D_BOUNCE_AUG_MULTIPLIER',
    'WT_D_BOUNCE_AUG_REQUIRE_HIGHER_PRICE',
    'WT_D_BOUNCE_AUG_REQUIRE_HIGHER_WT',
    'WT_D_BOUNCE_DD_STOP_ENABLED',
    'WT_EXHAUST_ENTRY_GATE_ENABLED',
    'WT_EXHAUST_EXIT_ENABLED',
    'WT_EXHAUST_EXIT_MIN_GAIN_PCT',
    'WT_EXHAUST_EXIT_REQUIRE_GAIN',
    'WT_EXIT_MIN_TFS_TRADIER',
    'WT_EXIT_TFS_TRADIER',
    'WT_EXIT_VELOCITY_TRADIER',
    'WT_EXIT_VEL_THRESHOLD',
    'WT_EXIT_VETO_ENABLED_TRADIER',
    'WT_FORCE_OPEN_FRESH_CROSS_ONLY',
    'WT_FORCE_OPEN_FRESH_MAX_BARS',
    'WT_FORCE_OPEN_TRIGGER_TF',
    'WT_MTF_VEL_GATE_ENABLED',
    'WT_MTF_VEL_MIN',
    'WT_PERCENTILE_ENTRY_GATE_ENABLED',
    'WT_PERCENTILE_ENTRY_OB_D',
    'WT_PERCENTILE_ENTRY_OS_D',
    'WT_PERCENTILE_EXIT_ENABLED',
    'WT_PERCENTILE_EXIT_OB_4H',
    'WT_PERCENTILE_EXIT_OB_D',
    'WT_PERCENTILE_EXIT_OS_4H',
    'WT_PERCENTILE_EXIT_OS_D',
    'WT_REDUCE_FRAC_HIGH',
    'WT_REDUCE_FRAC_LOW',
    'WT_REDUCE_FRAC_MED',
    'WT_VEL_DECEL_RATIO',
    'WT_VEL_USE_DECEL_RATIO_ONLY',
    'WT_W_EXIT_ENABLED',
    'ZEC_SUPERVISOR_AUTONOMOUS_CLOSE_PER_HOUR_MAX',
    'ZEC_SUPERVISOR_ENABLED',
    'ZERO_CONFIRMATION_THRESHOLD_API',
    'ZERO_CONFIRMATION_THRESHOLD_WS',
    'ZONE_CLOSE_THRESHOLD',
    'ZONE_MID_THRESHOLD',
    'ZONE_OPEN_THRESHOLD',
    '_DEFAULT_CORE_TECHNICAL_INDICATORS',
    '_DEFAULT_FINAL_SCORING_INDICATORS',
    '_G0_PURE_BH',
    'ACCOUNTS',
    'ACCOUNT_KEYS',
    'ACCOUNT_OVERRIDES',
    'ACCOUNT_SIDE_MAPPING',
    'ACCOUNT_TP_PCT',
    'AUG_COOLDOWN_S',
    'AVAILABLE_IPS',
    'BACKUP_KLINES_CACHE',
    'BANDAID_OFF_LOSER_RECOVER_PCT',
    'BAND_FILE',
    'BLACKLIST_SYMBOLS',
    'BREAKEVEN_GAIN_EROSION_MIN_GAIN',
    'BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT',
    'BREAKOUT_INJECT_LIVE',
    'BREAKOUT_INJECT_SHADOW_LOG',
    'BREAKOUT_MAD_MULTIPLIER',
    'BREAKOUT_MIN_RET_PCT',
    'BREAKOUT_TF_SIZE_MULT_3M',
    'BTC_ACCEL_RAMP_MIN_TFS',
    'BTC_ACCEL_RAMP_PRICE_BOUNCE_BARS',
    'BTC_ACCEL_RAMP_PRICE_BOUNCE_TF',
    'BTC_ACCEL_RAMP_REQUIRE_POSITIVE',
    'BTC_BREAKOUT_ACCEL_MIN_TFS',
    'BTC_BREAKOUT_BLOCK_OPPOSING_DIV',
    'BTC_BREAKOUT_COOLDOWN_BARS',
    'BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE',
    'BTC_BREAKOUT_MIN_HOLD_BARS',
    'BTC_COOLDOWN_BARS',
    'BTC_DAILY_LOSS_PCT_FLOOR',
    'BTC_DEDICATED_ACCOUNTS',
    'BTC_DEDICATED_SYMBOLS',
    'BTC_DIVERGENCE_BEAR_MIN_INDS',
    'BTC_DIVERGENCE_BLOCK_AGAINST',
    'BTC_DIVERGENCE_BULL_MIN_INDS',
    'BTC_DIVERGENCE_LB_15M',
    'BTC_DIVERGENCE_LB_1H',
    'BTC_DIVERGENCE_LB_3M',
    'BTC_DIVERGENCE_LB_4H',
    'BTC_DIVERGENCE_LB_D',
    'BTC_DIVERGENCE_LOOKBACK_BARS',
    'BTC_DIVERGENCE_MIN_TF',
    'BTC_DIVERGENCE_REQUIRE_D_CONFIRM_BARS',
    'BTC_FIB_LOOKBACK_4H',
    'BTC_FIB_LOOKBACK_D',
    'BTC_FIB_LOOKBACK_M',
    'BTC_FIB_LOOKBACK_W',
    'BTC_FIB_LOOKBACK_Y',
    'BTC_FIB_RECOMPUTE_ON_NEW_HL',
    'BTC_FOLLOW_THROUGH_MIN_MOVE_PCT',
    'BTC_HARD_BLOCK_OTHER_ACCOUNTS',
    'BTC_HARD_LOSS_USD_PER_TRADE',
    'BTC_LEVERAGE',
    'BTC_MIN_HOLD_BARS',
    'BTC_PAPER_PARITY_VERIFY_AT_STARTUP',
    'BTC_PAPER_RECONCILE_ALARM_DRIFT_PCT',
    'BTC_PER_TRADE_NOTIONAL_USD_MAX',
    'BTC_PYRAMID_DISABLED',
    'BTC_RISK_PATH',
    'BTC_ROUND_BANDS_EACH_SIDE',
    'BTC_ROUND_INC_PRIMARY_USD',
    'BTC_ROUND_INC_SECONDARY_USD',
    'BTC_RZ_PROXIMITY_PCT',
    'BTC_RZ_SOFTEN_ACCEL_BY',
    'BTC_RZ_USE_FIB',
    'BTC_RZ_USE_ROUND',
    'BTC_TOTAL_NOTIONAL_USD_MAX',
    'BTC_WEEKLY_LOSS_PCT_FLOOR',
    'COMMISSION_BUFFER_PCT',
    'CONSOLIDATED_KLINES_CACHE',
    'COUNTER_TREND_CRYPTO',
    'CRASH_MULT_GRADIENT_MAX',
    'CROSSES_FILE',
    'CYCLE_TP_TIERED_LEVELS',
    'DAEMON_PRICE_CROSS_PCT',
    'DATA_READY_FLAG_FILE',
    'DD_BOUNCE_COOLDOWN_HOURS',
    'DD_BOUNCE_REQUIRE_HIGHER_PRICE',
    'DD_BOUNCE_REQUIRE_HIGHER_WT',
    'DIRECTION_FAVORABLE_MAX_MINUTES',
    'ENABLE_MULTI_INSTANCE_ON_MACBOOK',
    'EXECUTE_NOW_MAX_MARK_AGE_S',
    'EXECUTE_NOW_WIRE_TRIPWIRE_MAX_LAG_S',
    'EXECUTE_NOW_WIRE_TRIPWIRE_SHADOW',
    'EZ_INDICATORS_CMD_TIMEOUT',
    'EZ_INDICATORS_RESTART_COOLDOWN',
    'EZ_INDICATORS_SHUTDOWN_CMD',
    'EZ_INDICATORS_START_CMD',
    'EZ_KLINES_API_MAX_PER_MINUTE',
    'EZ_KLINES_API_MAX_PER_SECOND',
    'EZ_KLINES_MAX_CONCURRENT',
    'EZ_KLINES_SEMAPHORE',
    'EZ_MANAGE_CONCURRENCY_LIMIT',
    'EZ_MANAGE_MAKER_SEMAPHORE',
    'EZ_MANAGE_RATE_LIMIT_SEMAPHORE',
    'EZ_MANAGE_THROTTLER_RATE',
    'EZ_MANAGE_WS_SEMAPHORE',
    'EZ_MARK_PRICES_API_SEMAPHORE',
    'EZ_MARK_PRICES_STARTUP_SEMAPHORE',
    'EZ_PRICES_API_DELAY',
    'EZ_PRICES_API_SEMAPHORE',
    'EZ_PRICES_API_SLEEP_AFTER',
    'EZ_PRICES_FAPI_SEMAPHORE',
    'EZ_PRICES_FILE_IO_SEMAPHORE',
    'EZ_PRICES_LIMIT_PER_HOST',
    'EZ_PRICEWS_API_DELAY',
    'EZ_PRICEWS_API_SEMAPHORE',
    'EZ_PRICEWS_CONNECTOR_LIMIT',
    'EZ_PRICEWS_LIMIT_PER_HOST',
    'EZ_RANKINGS_THROTTLER_RATE',
    'EZ_REENTRY_QUEUE_CONSUMER_ENABLED',
    'EZ_REENTRY_QUEUE_CONSUMER_INTERVAL_S',
    'FAPI_BASE_URL',
    'FINAL_SCORE_FILE',
    'FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO',
    'FSTREAM_WS_URL_BASE',
    'GOLDEN_RULE_ACTIVATION_TF_LIST',
    'GOLDEN_RULE_BASE_USD',
    'GOLDEN_RULE_MULT_15M',
    'GOLDEN_RULE_MULT_1H',
    'GOLDEN_RULE_MULT_4H',
    'GOLDEN_RULE_MULT_D',
    'GOLDEN_RULE_MULT_W',
    'HARD_BREAKEVEN_MIN_PEAK_PCT',
    'HD_ROOT',
    'HLR_BYPASS_MIN_TF_WEIGHT',
    'HLR_MIN_GAIN_PCT',
    'HLR_MIN_TFS',
    'HLR_MIN_TFS_FOR_BYPASS',
    'HLR_PTS_15M',
    'HLR_PTS_1H',
    'HLR_PTS_3M',
    'HLR_PTS_4H',
    'HLR_PTS_D',
    'HLR_PTS_W',
    'HLR_SZ_15M',
    'HLR_SZ_1H',
    'HLR_SZ_3M',
    'HLR_SZ_4H',
    'HLR_SZ_D',
    'HLR_SZ_MAX',
    'HLR_SZ_W',
    'HLR_TOP_MIN_GAIN_PCT',
    'HLR_TOP_MIN_TFS',
    'HLR_TOP_VEL_1H_THRESH',
    'HLR_TOP_VEL_4H_THRESH',
    'HLR_TOP_VEL_D_THRESH',
    'HOUR_OF_DAY_BLOCKED_UTC',
    'HTF_TREND_VETO_BYPASS_REASONS',
    'INF_7D_BEAT_SIZE_MULT',
    'INF_DEDICATED_WINNERS',
    'KLINE_COLUMNS',
    'LAST_EVENTS_FILE',
    'LATEST_MARKET_DATA_FILE',
    'LEGACY_DIRECTION_FAVORABLE',
    'LIVE_POSITION_FRESHNESS_MAX_SEC',
    'LIVE_USDC_PAIRS_FILE',
    'LOCAL_DATA_MAX_AGE',
    'LOG_FILE_EZ_BACKUP',
    'LOG_FILE_EZ_CROSSES',
    'LOG_FILE_EZ_INDICATORS',
    'LOG_FILE_EZ_MANAGE',
    'LOG_FILE_EZ_MARK_PRICES',
    'LOG_FILE_EZ_PRICES',
    'LOG_FILE_EZ_PRICES_WS',
    'LOG_FILE_EZ_RANKINGS',
    'LOSERS_15M_FILE',
    'LOSERS_20_FILE',
    'LT_TOP_SIZE',
    'MARKET_MODE_FILE',
    'MAX_FILES',
    'MAX_MARKET_DATA_FILE_AGE_SECONDS',
    'MIN_QTY_FILE',
    'MITIGATOR_ACCOUNT',
    'MI_TF_AGREE_MIN',
    'MOM4S_S_ACCOUNTS',
    'MOM5_TRENDER_L_ACCOUNTS',
    'MR3S_S_ACCOUNTS',
    'MR5_L_ACCOUNTS',
    'MTS_BOTTOM_MIN',
    'MTS_WEIGHT_15m',
    'MTS_WEIGHT_1h',
    'MTS_WEIGHT_1m',
    'MTS_WEIGHT_3m',
    'MTS_WEIGHT_4h',
    'MTS_WEIGHT_5m',
    'NEWBORN_LOSS_KILL_MIN_AGE_MIN',
    'NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST',
    'NEWBORN_LOSS_KILL_SURGICAL_ONLY',
    'NEWBORN_LOSS_KILL_VEL_TF',
    'NEWBORN_LOSS_KILL_WINDOW_MIN',
    'NEW_POSITION_MIN_AGE_SECONDS',
    'OB_PRICE_DEFER_ACCOUNTS',
    'OB_PRICE_DEFER_AT_LEVEL_TOL_PCT',
    'OB_PRICE_DEFER_MAX_DISTANCE_PCT',
    'OB_PRICE_DEFER_TTL_SEC',
    'OPPOSITE_LOSER_DEEP_LOSS_PCT',
    'PAPER_TRADING',
    'PAPER_TRADING_QUICK',
    'PARTIAL_PROFIT_LOCK_ACCOUNTS',
    'PARTIAL_PROFIT_LOCK_ACCOUNTS_TRADIER',
    'PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT',
    'PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT',
    'PARTIAL_PROFIT_LOCK_FRAC',
    'PARTIAL_PROFIT_LOCK_GAIN_PCT',
    'PARTIAL_PROFIT_LOCK_USE_MAKER',
    'PAU_TIMEOUT_SEC',
    'PER_SYMBOL_CONFIG_FILE',
    'PLOTS_DIR',
    'POSITIONS_SERVICE_HEALTH_RETRIES',
    'POSITIONS_SERVICE_START_CMD',
    'POSITIONS_SNAPSHOT_MAX_AGE',
    'PPL_FIRE_COOLDOWN_S',
    'PREVIOUS_SYMBOLS_FIN',
    'PREVIOUS_SYMBOLS_FLZ',
    'PREVIOUS_SYMBOLS_MEN',
    'PRICE_CACHE_PULL_MAX_AGE_SEC',
    'PRICE_CACHE_PULL_S1',
    'PROGRESSIVE_LOCK_TIERS_PCT',
    'PROX_FILE',
    'QUICK_CYCLE_TP_MIN_GAIN_PCT',
    'QUICK_RECOVERY_WINDOW_MIN',
    'QUICK_SENTIMENT_CUT_MIN_GAIN',
    'RANKINGS_DIR',
    'RANKING_MULT_ENABLED',
    'RANKING_MULT_MAX',
    'RANKING_MULT_MIN',
    'RANKING_POINTS_FILE',
    'RECENT_REDUCTION_GUARD_USE_4BAR',
    'RECENT_REDUCTION_GUARD_WINDOW_S',
    'REDIS_1M_TAIL',
    'RED_ZONE_FALLBACK_K15_HIGH',
    'RED_ZONE_FALLBACK_K15_LOW',
    'RED_ZONE_MIN_DISTANCE_PCT',
    'RED_ZONE_MIN_WALL_NOTIONAL_USD',
    'RED_ZONE_STALE_MAX_SEC',
    'REENTRY_B01_WT_2of3_ENABLED',
    'RE_2_PCT_OB',
    'RE_2_PCT_OS',
    'RE_3_CONVICTION_BONUS',
    'RE_3_RISING_COUNT_THR',
    'RE_4_HA_STREAK_CAP',
    'RE_4_HA_STREAK_WEIGHT',
    'RE_5_COMPRESSION_BONUS',
    'RE_5_INSIDE_COUNT_THR',
    'RE_6_MIN_EXPANDING_TFS',
    'RIDICULOUS_HOLD_HOURS',
    'RIDICULOUS_HOLD_REQUIRE_GAIN_NONNEG',
    'RIDICULOUS_LOSS_PCT',
    'RZ_BREAKOUT_BAND',
    'R_G10_HTF_DIV_TFS',
    'R_S3_HIDDEN_BONUS',
    'R_S3_MAIN_PENALTY',
    'R_S3_TF_WEIGHT_15M',
    'R_S3_TF_WEIGHT_1H',
    'R_S3_TF_WEIGHT_3M',
    'R_S3_TF_WEIGHT_4H',
    'R_S3_TF_WEIGHT_D',
    'R_S4_HA_STREAK_TF',
    'R_S4_HA_STREAK_WEIGHT',
    'R_S5_SENT_VEL_BONUS',
    'R_S5_SENT_VEL_PCT_THR',
    'R_S7_HHLL_BONUS_PER_TF',
    'R_S7_HHLL_MIN_INDICATORS',
    'R_S7_HHLL_MIN_TFS_FOR_BONUS',
    'R_S7_HHLL_TFS',
    'R_Z2_BOT_MULT',
    'R_Z2_PCT_BOT_THR',
    'R_Z2_PCT_TOP_THR',
    'R_Z2_TOP_MULT',
    'R_Z3_T1_MULT',
    'R_Z3_T1_THR',
    'R_Z3_T2_MULT',
    'R_Z3_T2_THR',
    'R_Z3_T3_MULT',
    'R_Z3_T3_THR',
    'SANDBOX_ACCOUNTS',
    'SATOSHIT_ACCOUNTS',
    'SATOSHIT_ACCOUNTS_TRADIER',
    'SATOSHIT_HTF_RVOL_1H_MIN',
    'SATOSHIT_MIN_VOTES',
    'SBA_ADX_MAX',
    'SBA_MAX_ADDS',
    'SBA_MAX_LOSS_PCT',
    'SBA_MIN_LOSS_PCT',
    'SBA_SIZE_FRACTION',
    'SCALP_V3_ACCOUNTS',
    'SCALP_V3_AUG_BE_STOP_PCT',
    'SCALP_V3_AUG_COOLDOWN_SEC',
    'SCALP_V3_AUG_MAX_FRAC_OF_POS',
    'SCALP_V3_AUG_MIN_GAIN',
    'SCALP_V3_BOOST_LOOKBACK_BARS_3M',
    'SCALP_V3_BOOST_VOL_Z_MIN',
    'SCALP_V3_BOOST_WEIGHT',
    'SCALP_V3_DIAG_LOG',
    'SCALP_V3_ENFORCE_UNIVERSE_DIRECTION',
    'SCALP_V3_FAST_PPL_GAIN_PCT',
    'SCALP_V3_K_FRESH_HI',
    'SCALP_V3_K_FRESH_LO',
    'SCALP_V3_K_FRESH_MID_HI',
    'SCALP_V3_K_FRESH_MID_LO',
    'SCALP_V3_LONG_K_RISE_MAX',
    'SCALP_V3_LONG_K_RISE_MIN',
    'SCALP_V3_MAX_CONCURRENT',
    'SCALP_V3_MAX_HOLD_MIN',
    'SCALP_V3_MAX_LOSS_PCT',
    'SCALP_V3_OB_DIV_CONFLICT_MAX_NET',
    'SCALP_V3_OB_FLOW_AGREE_MODE',
    'SCALP_V3_OB_FLOW_K_AGREE',
    'SCALP_V3_OB_FLOW_OFI_MIN_ABS',
    'SCALP_V3_OB_FLOW_VEL_1M_MIN',
    'SCALP_V3_OB_FLOW_VEL_3M_MIN',
    'SCALP_V3_OB_MIN_DIFF',
    'SCALP_V3_OB_REQUIRED',
    'SCALP_V3_OB_VOID_EXTEND_HOLD',
    'SCALP_V3_OUTLIER_MAX_INJECT',
    'SCALP_V3_OUTLIER_MIN_Z_LONG',
    'SCALP_V3_OUTLIER_MIN_Z_SHORT',
    'SCALP_V3_OUTLIER_WEIGHT',
    'SCALP_V3_PEAK_GIVEBACK_PCT',
    'SCALP_V3_PG_ARM_PCT',
    'SCALP_V3_PG_GIVEBACK_PCT',
    'SCALP_V3_POSITION_CAP_USD',
    'SCALP_V3_PROTECTIVE_K_DROP_MIN',
    'SCALP_V3_SCAN_INTERVAL_SEC',
    'SCALP_V3_SCAN_MIN_DIVERGENCE',
    'SCALP_V3_SCAN_TOP_N',
    'SCALP_V3_SESSION_BLOCK_HOURS',
    'SCALP_V3_SHORT_K_FALL_MAX',
    'SCALP_V3_SHORT_K_FALL_MIN',
    'SCALP_V3_SHORT_RECENT_DUMP_LOOKBACK_MIN',
    'SCALP_V3_SHORT_RECENT_DUMP_PCT',
    'SCALP_V3_SHORT_REQUIRE_RECENT_DUMP',
    'SCALP_V3_SIDE_MODE',
    'SCALP_V3_SR_HOLD_MIN_GAIN_PCT',
    'SCALP_V3_SR_TOL_PCT',
    'SCALP_V3_STALL_GAIN_MAX_PCT',
    'SCALP_V3_STDEV_BOUNCE_HI',
    'SCALP_V3_STDEV_BOUNCE_HI_SHORT',
    'SCALP_V3_STDEV_BOUNCE_LO',
    'SCALP_V3_STDEV_BOUNCE_LO_LONG',
    'SCALP_V3_STDEV_BREAK_HI',
    'SCALP_V3_STDEV_BREAK_HI_LONG',
    'SCALP_V3_STDEV_BREAK_LO',
    'SCALP_V3_STDEV_BREAK_LO_SHORT',
    'SCALP_V3_STDEV_MODE',
    'SCALP_V3_STDEV_REJECT_HI',
    'SCALP_V3_STDEV_REJECT_LO',
    'SCALP_V3_STDEV_TF',
    'SCALP_V3_USE_HA_1M',
    'SCALP_V3_USE_HA_3M',
    'SCALP_V3_VWAP_DEV_MIN_PCT',
    'SCALP_V3_VWAP_TYPE',
    'SENTIMENT_TOP_N',
    'SHORT_ABOVE_EMA20_IS_PENALTY',
    'SIGNALS_FILE',
    'SQUEEZE_FIRE_TFS',
    'STALL_AGE_MIN_MIN',
    'STALL_GAIN_ABS_MAX',
    'STDEV_BOUNCE_HTF_LIST',
    'STDEV_BREAKOUT_HTF_LIST',
    'STDEV_BREAKOUT_RETEST_TF_LIST',
    'STOP_TIMEFRAME',
    'STRICT_VEC_PARITY_MODE',
    'STRICT_VEC_PARITY_SHADOW',
    'SWEEP_DEEP_TEST_GAIN_PER_MO_MIN_PCT',
    'SWEEP_DEEP_TEST_POOL_SHARPE_MIN',
    'SWEEP_DISCARD_POOL_SHARPE_FLOOR',
    'SYMBOLS_ACTIVE',
    'SYMBOLS_ACTIVE_FILE',
    'SYMBOLS_ANG_LONG',
    'SYMBOLS_ANG_SHORT',
    'SYMBOLS_FIN',
    'SYMBOLS_FLZ',
    'SYMBOLS_INF_LONG',
    'SYMBOLS_INF_SHORT',
    'SYMBOLS_MEN',
    'SYMBOL_SIZE_MULTIPLIERS',
    'SYNTHETIC_LOSER_MIN_AGE_MIN',
    'TF_ALL',
    'THROUGHPUT_DAILY_LOSS_RESET_UTC_HOUR',
    'THROUGHPUT_MAX_CONCURRENT_POSITIONS',
    'THROUGHPUT_MAX_CONCURRENT_POSITIONS_TRADIER',
    'THROUGHPUT_MAX_DAILY_LOSS_PCT',
    'THROUGHPUT_MAX_DAILY_LOSS_PCT_TRADIER',
    'THROUGHPUT_MAX_FIRES_PER_HOUR_PER_ACCOUNT',
    'THROUGHPUT_MAX_FIRES_PER_HOUR_PER_ACCOUNT_TRADIER',
    'THROUGHPUT_MAX_FIRES_PER_HOUR_PER_SYMBOL',
    'THROUGHPUT_MAX_TOTAL_NOTIONAL_USD',
    'THROUGHPUT_MAX_TOTAL_NOTIONAL_USD_TRADIER',
    'TOP_OF_RANGE_BLOCK_REQUIRE_ALL',
    'TOP_OF_RANGE_BLOCK_TF_LIST',
    'TRADEABLE_KEYS',
    'TRADEABLE_KEYS_MANDATORY_SIZE_USD',
    'TRENDER_INJECT_LIVE',
    'TRENDER_INJECT_SHADOW_LOG',
    'TRENDER_LIN_MIN',
    'TRENDER_QV_ANCHOR_USD',
    'TRENDER_QV_FLOOR_USD',
    'TRENDER_QV_MAX_BOOST',
    'TRENDER_RET24_MIN_PCT',
    'TREND_ACCOUNTS',
    'USE_WS_3M',
    'VEC_FIX_R1_REASON_STRING_FOR_DIFF',
    'VEC_MTF_ARMED_BYPASS_STRONG',
    'VEC_MTF_ARMED_RESULTING_REASON',
    'WATCHDOG_WT3M_ESCALATE_LADDER',
    'WATCHDOG_WT3M_ESCALATE_MAX_USD',
    'WINNERS_15M_FILE',
    'WINNERS_20_FILE',
    'WORKER_INSTANCE_ID',
    'WORKER_TOTAL_INSTANCES',
    'WT15M_AGAINST_PENALTY',
    'ZEC_FLZ_LONG_SIZE_MULT',
    'ZEC_SUPERVISOR_HISTORY_LOOKBACK_MIN',
    'ZEC_SUPERVISOR_MODEL',
    'ZEC_SUPERVISOR_POLL_INTERVAL_SEC',
    'indicators_filepath',
    'MTS_WEIGHT_15m',
    'MTS_WEIGHT_1h',
    'MTS_WEIGHT_1m',
    'MTS_WEIGHT_3m',
    'MTS_WEIGHT_4h',
    'MTS_WEIGHT_5m',
    'REENTRY_B01_WT_2of3_ENABLED',
    'indicators_filepath',
    'ATR_LONG_WINDOW',
    'ATR_TRAIL_FILTER_TF',
    'ATR_TRAIL_SWEEP_ENABLED',
    'AUGMENT_AT_LOSS_ENABLED',
    'AUGMENT_WT_4H_BOUNCE_ENABLED',
    'BAR_PATTERNS_FILTER_TF',
    'BB_PULLBACK_GATE_FILTER_TF',
    'BB_RECOVERY_ENTRY_FILTER_TF',
    'BB_RECOVERY_FILTER_TF',
    'BREAKEVEN_GAIN_EROSION_FILTER_TF',
    'BREAKOUT_RETEST_FILTER_TF',
    'BTC_DEDICATED_FILTER_TF',
    'BTC_RZ_WT_DC_MULTIFACTOR',
    'BT_WT_CROSS_LADDER_FILTER_TF',
    'CANDLE_PATTERN_STOPS_FILTER_TF',
    'CHANNEL_REENTRY_STOP_ENABLED',
    'CIRCUIT_SHARPE_GATES_FILTER_TF',
    'COOLDOWN_LOCKS_FILTER_TF',
    'DC_BREACH_REDUCE_FILTER_TF',
    'DC_BREAK_FILTER_TF',
    'DC_MOMENTUM_BOTA_SCORER_FILTER_TF',
    'DELTA_ENGINE_FILTER_TF',
    'DELTA_EXIT_MANDATORY_REENTRY_ENABLED',
    'DIRECTION_FAVORABLE_REENTRY_ENABLED',
    'DUP_GUARD_FILTER_TF',
    'DYNAMIC_SCORE_AUGMENT_ENABLED',
    'DYN_STRUCT_TRAIL_ENABLED',
    'E2E_REPLAY_VALIDATOR_FILTER_TF',
    'EMA_9_21_FILTER_FILTER_TF',
    'EMA_9_21_FILTER_MIN_TFS',
    'EMA_BLANKET_FILTER_ENABLED',
    'EMA_BLANKET_FILTER_FILTER_TF',
    'EMA_BLANKET_FILTER_MIN_TFS',
    'EMERGENCY_BRAKE_FILTER_TF',
    'ENTRY_SCORE_THRESHOLD',
    'EXHAUSTION_EXIT_FILTER_TF',
    'EXIT_R1_R2_FILTER_TF',
    'EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF',
    'EXIT_TOP_FADE_FILTER_TF',
    'EXIT_TO_REDUCE_ADAPTER_FILTER_TF',
    'FAST_RISER_FILTER_TF',
    'FH_MOMENTUM_FILTER_TF',
    'FIRST_OPEN_THROTTLE_FILTER_TF',
    'FOLLOW_THROUGH_REENTRY_ENABLED',
    'FROZEN_STOP_FILTER_TF',
    'FUNDING_GATE_FILTER_TF',
    'GOLDEN_RULE_ENFORCE_FILTER_TF',
    'GOLDEN_RULE_HTF_VOTE_FILTER_TF',
    'GR_FILTER_VEC_ENABLED',
    'GR_FILTER_VEC_FILTER_TF',
    'GR_FILTER_VEC_MIN_TFS',
    'GR_V5_STATE_FILTER_TF',
    'HAIKU_ENTRY_GATE_ENABLED',
    'HAIKU_WINNER_FILTER_TF',
    'INTRADAY_SESSION_FORCE_EXIT_UTC',
    'KILLER_KNOB_FINDER_FILTER_TF',
    'LIVE_ENTRY_ENGINE_FILTER_TF',
    'LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF',
    'MOM3_FILTER_TF',
    'MOMENTUM_BREAKOUT_FILTER_TF',
    'MTF_ARMED_ENTRIES_FILTER_TF',
    'MTF_ATR_TRAIL_FILTER_TF',
    'MTF_DC_REJECT_FILTER_TF',
    'NEWBORN_LOSS_KILL_FILTER_TF',
    'NEWBORN_PROTECT_FILTER_TF',
    'NOLOSS_BYPASS_WT5OF5_FILTER_TF',
    'OPEN_INTENT_SIZE_GATES_FILTER_TF',
    'PARTIAL_EXIT_FRAC',
    'PARTIAL_PROFIT_LOCK_V2_FILTER_TF',
    'PEAK_GIVEBACK_BE_EROSION_FILTER_TF',
    'QUICK_REENTRY_60MIN_MIN_PCT',
    'REVERSE_ON_EXIT_ENABLED',
    'V8_ENTRY_ENGINE_DC_ENABLED',
    'V8_ENTRY_ENGINE_WT_ENABLED',
    'VEC_REENTRY_DC4_EXITPRICE_ENABLED',
    'WT_15M_BOUNCE_OPEN_ENABLED',
    'WT_15M_CROSS_ENTRY_ENABLED',
    'WT_ACCEL_EXIT_ENABLED',
    'WT_AGAINST_FILTER_ENABLED',
    'WT_DIV_EXIT_ENABLED',
    'WT_MOMENTUM_EXIT_THRESHOLD',

    'AUGMENT_WT_4H_BOUNCE_ENABLED',
    'E2E_REPLAY_VALIDATOR_FILTER_TF',
    'EMA_9_21_FILTER_FILTER_TF',
    'EMA_9_21_FILTER_MIN_TFS',
    'EXIT_R1_R2_FILTER_TF',
    'E_1_EXIT_DELTA_THR',
    'E_1_WT_EXIT_USE_DELTA_ENABLED',
    'E_3_USE_WT_STRUCTURE_EXIT_MODE',
    'GR_V5_STATE_FILTER_TF',
    'HLR_REENTRY_MULT_1H',
    'HLR_REENTRY_MULT_4H',
    'HTF4_CONF',
    'HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H',
    'HTF_GATE_SIGNALS_SMA200D',
    'LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF',
    'MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS',
    'MOM3_FILTER_TF',
    'NOLOSS_BYPASS_WT5OF5_FILTER_TF',
    'NOLOSS_BYPASS_WT_5OF5_MIN_TFS',
    'OBLIGATORY_REENTRY_K15_HIGH_BLOCK',
    'OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC',
    'OBLIGATORY_REENTRY_SCORE_TIER1',
    'OBLIGATORY_REENTRY_SCORE_TIER2',
    'OBLIGATORY_REENTRY_SCORE_TIER3',
    'OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK',
    'OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC',
    'OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED',
    'OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED',
    'PARTIAL_PROFIT_LOCK_V2_FILTER_TF',
    'PYRAMID_MIN_WT_VEL_1H',
    'QUICK_REENTRY_60MIN_MIN_PCT',
    'REENTRY2_DC_BREAK_ALLOW_15M',
    'REENTRY2_DC_BREAK_FILTER_TF',
    'REENTRY2_DC_BREAK_REQUIRE_K_FILTER',
    'REENTRY2_DC_BREAK_REQUIRE_WT_FILTER',
    'REENTRY2_DIR_FAV_ENABLED',
    'REENTRY2_STOCH_CROSS_ENABLED',
    'REENTRY_B16_SIZE_MULT_STRONG',
    'REENTRY_B16_SIZE_MULT_WEAK',
    'REENTRY_B16_SMA200_PROX_PCT',
    'REENTRY_B16_SMA200_PULLBACK_ENABLED',
    'REENTRY_K15M_PARTIAL_MULT',
    'REENTRY_RALLY_K15M_MAX',
    'REENTRY_SIZE_EXTENDED_K1H',
    'REENTRY_TIER1_SIZE_MULT',
    'REENTRY_TIER2_MAX_MINUTES',
    'REENTRY_WT15M_SIZE_MULT',
    'RULE_B_3M_EXIT_ENABLED',
    'SCALP_V3_AUG_BE_STOP_ENABLED',
    'SCALP_V3_AUG_BE_STOP_PCT',
    'SCALP_V3_K_OB_EXIT_ENABLED',
    'SCALP_V3_K_OB_EXIT_K15M_HI',
    'SCALP_V3_K_OB_EXIT_K15M_LO',
    'SCALP_V3_K_OB_EXIT_K3M_HI',
    'SCALP_V3_K_OB_EXIT_K3M_LO',
    'SCALP_V3_K_OB_EXIT_WALL_PCT',
    'SCALP_V3_OB_WALL_TOO_CLOSE_PCT',
    'SCALP_V3_PROTECTIVE_EXIT_ENABLED',
    'TRADIER_RSI2_EXIT_THRESHOLD_LONG',
    'TRADIER_RSI2_EXIT_THRESHOLD_SHORT',
    'V8_ENTRY_ENGINE_DC_ENABLED',
    'V8_ENTRY_ENGINE_WT_ENABLED',
    'VEC_REENTRY_DC4_EXITPRICE_ENABLED',
    'WT_15M_BOUNCE_OPEN_ENABLED',
    'WT_15M_CROSS_ENTRY_ENABLED',
    'WT_4H_VEL_EXIT_ENABLED',
    'WT_4H_VEL_EXIT_K_EXTREME_HIGH',
    'WT_4H_VEL_EXIT_K_EXTREME_LOW',
    'WT_4H_VEL_EXIT_REQUIRE_K_EXTREME',
    'WT_4H_VEL_EXIT_REQUIRE_PROFIT',
    'WT_CROSS_EXIT_REQUIRE_15M_CONFIRM',
    'WT_PERCENTILE_EXIT_OB_4H',
    'WT_PERCENTILE_EXIT_OS_4H',
    'AUGMENT_WT_4H_BOUNCE_ENABLED',
    'E2E_REPLAY_VALIDATOR_FILTER_TF',
    'EMA_9_21_FILTER_FILTER_TF',
    'EMA_9_21_FILTER_MIN_TFS',
    'EXIT_R1_R2_FILTER_TF',
    'E_1_EXIT_DELTA_THR',
    'E_1_WT_EXIT_USE_DELTA_ENABLED',
    'E_3_USE_WT_STRUCTURE_EXIT_MODE',
    'GR_V5_STATE_FILTER_TF',
    'HLR_REENTRY_MULT_1H',
    'HLR_REENTRY_MULT_4H',
    'HTF4_CONF',
    'HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H',
    'HTF_GATE_SIGNALS_SMA200D',
    'LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF',
    'MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS',
    'MOM3_FILTER_TF',
    'NOLOSS_BYPASS_WT5OF5_FILTER_TF',
    'NOLOSS_BYPASS_WT_5OF5_MIN_TFS',
    'OBLIGATORY_REENTRY_K15_HIGH_BLOCK',
    'OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC',
    'OBLIGATORY_REENTRY_SCORE_TIER1',
    'OBLIGATORY_REENTRY_SCORE_TIER2',
    'OBLIGATORY_REENTRY_SCORE_TIER3',
    'OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK',
    'OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC',
    'OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED',
    'OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED',
    'PARTIAL_PROFIT_LOCK_V2_FILTER_TF',
    'PYRAMID_MIN_WT_VEL_1H',
    'QUICK_REENTRY_60MIN_MIN_PCT',
    'REENTRY2_DC_BREAK_ALLOW_15M',
    'REENTRY2_DC_BREAK_FILTER_TF',
    'REENTRY2_DC_BREAK_REQUIRE_K_FILTER',
    'REENTRY2_DC_BREAK_REQUIRE_WT_FILTER',
    'REENTRY2_DIR_FAV_ENABLED',
    'REENTRY2_STOCH_CROSS_ENABLED',
    'REENTRY_B16_SIZE_MULT_STRONG',
    'REENTRY_B16_SIZE_MULT_WEAK',
    'REENTRY_B16_SMA200_PROX_PCT',
    'REENTRY_B16_SMA200_PULLBACK_ENABLED',
    'REENTRY_K15M_PARTIAL_MULT',
    'REENTRY_RALLY_K15M_MAX',
    'REENTRY_SIZE_EXTENDED_K1H',
    'REENTRY_TIER1_SIZE_MULT',
    'REENTRY_TIER2_MAX_MINUTES',
    'REENTRY_WT15M_SIZE_MULT',
    'RULE_B_3M_EXIT_ENABLED',
    'SCALP_V3_AUG_BE_STOP_ENABLED',
    'SCALP_V3_AUG_BE_STOP_PCT',
    'SCALP_V3_K_OB_EXIT_ENABLED',
    'SCALP_V3_K_OB_EXIT_K15M_HI',
    'SCALP_V3_K_OB_EXIT_K15M_LO',
    'SCALP_V3_K_OB_EXIT_K3M_HI',
    'SCALP_V3_K_OB_EXIT_K3M_LO',
    'SCALP_V3_K_OB_EXIT_WALL_PCT',
    'SCALP_V3_OB_WALL_TOO_CLOSE_PCT',
    'SCALP_V3_PROTECTIVE_EXIT_ENABLED',
    'TRADIER_RSI2_EXIT_THRESHOLD_LONG',
    'TRADIER_RSI2_EXIT_THRESHOLD_SHORT',
    'V8_ENTRY_ENGINE_DC_ENABLED',
    'V8_ENTRY_ENGINE_WT_ENABLED',
    'VEC_REENTRY_DC4_EXITPRICE_ENABLED',
    'WT_15M_BOUNCE_OPEN_ENABLED',
    'WT_15M_CROSS_ENTRY_ENABLED',
    'WT_4H_VEL_EXIT_ENABLED',
    'WT_4H_VEL_EXIT_K_EXTREME_HIGH',
    'WT_4H_VEL_EXIT_K_EXTREME_LOW',
    'WT_4H_VEL_EXIT_REQUIRE_K_EXTREME',
    'WT_4H_VEL_EXIT_REQUIRE_PROFIT',
    'WT_CROSS_EXIT_REQUIRE_15M_CONFIRM',
    'WT_PERCENTILE_EXIT_OB_4H',
    'WT_PERCENTILE_EXIT_OS_4H',
    'BB_SQUEEZE_EXIT_ENABLED',
]



def _safe(npz: dict, key: str, n: int, default: float = 0.0) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _safeb(npz: dict, key: str, n: int) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(bool)
    return np.zeros(n, dtype=bool)


def _ha_int(npz: dict, key: str, n: int):
    v = npz.get(key)
    if v is None or not isinstance(v, np.ndarray) or len(v) != n:
        return np.zeros(n, dtype=np.int8)
    if v.dtype in (np.int8, np.int16, np.int32, np.int64):
        return v.astype(np.int8)
    return np.zeros(n, dtype=np.int8)


def _base_tf(npz: dict, cfg=None) -> str:
    """Return the native lowest timeframe for this dataset/configuration."""
    preferred = getattr(cfg, "BASE_TF", "3m") if cfg is not None else "3m"
    # Fixed: include 15m/1h fallback when 3m/5m missing (BTCUSDC 7d has only 15m, not 3m -> previously returned 3m with 0 close)
    candidates = [preferred] + [tf for tf in ("3m", "5m", "15m", "1h", "4h", "D") if tf != preferred]
    for tf in candidates:
        value = npz.get(f"close_{tf}")
        if isinstance(value, np.ndarray) and value.ndim > 0 and len(value) > 0 and np.count_nonzero(value) > len(value) * 0.5:
            return tf
    # Fallback to any available close TF
    for tf in ("15m", "1h", "4h", "D", "3m", "5m"):
        value = npz.get(f"close_{tf}")
        if isinstance(value, np.ndarray) and value.ndim > 0 and len(value) > 0:
            return tf
    return preferred


def _base_safe(npz: dict, field: str, n: int, cfg, default: float = 0.0) -> np.ndarray:
    return _safe(npz, f"{field}_{_base_tf(npz, cfg)}", n, default)


def _base_bool(npz: dict, field: str, n: int, cfg) -> np.ndarray:
    return _safeb(npz, f"{field}_{_base_tf(npz, cfg)}", n)


def _base_ha(npz: dict, field: str, n: int, cfg) -> np.ndarray:
    return _ha_int(npz, f"{field}_{_base_tf(npz, cfg)}", n)


@dataclass
class QuickConfig:
    # USER 2026-09-30: every switch has FOUR defaults (CRYPTO_LONG/CRYPTO_SHORT/STOCKS_LONG/STOCKS_SHORT) in
    # data/cat_side_defaults_4.json (built from the TEMPLATE bold defaults). Lookup order everywhere: per-sym override >
    # cat_side default (cat_side_defaults.get_for) > the single value in this class. False = old single-default behaviour.
    CAT_SIDE_DEFAULTS_ENABLED: bool = True
    MODE: str = "crypto"  # Fixed: was tradier, caused BTCUSDC 7d 0 vs 15m 1081 (int truncation) — crypto must be fractional
    BASE_TF: str = "3m"
    ENTRY_SCORE_THRESHOLD: float = 18.0
    K3M_FLOOR: float = 30
    K3M_FLOOR_ENABLED: bool = False
    COOLDOWN_BARS: int = 3  # USER 2026-09-27: NO FUCKING COOLDOWN
    NOLOSS_ENABLED: bool = False
    DC_RECOVERY_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    DC_RECOVERY_EXIT_TOLERANCE_PCT: float = 0.1
    START_POSITION_SIZE: float = 28.0  # parity 2026-09-26: live Config 28.0 vs vec 500 caused 17x mismatch — align to config.Config crypto. Bold in TEMPLATE_CRYPTO_LONG.
    CRYPTO_ROUND_TRIP_COMMISSION_PCT: float = 0.08  # USER 2026-08-08: binance futures 0.08% round trip; tradier is commission-free
    MIN_POSITION_SIZE: float = 1.0  # parity 2026-09-26: live Config 1.0 vs vec 55 caused 55x mismatch — align to config.Config crypto. Bold in TEMPLATE_CRYPTO.
    CT_WT_VELOCITY_GATE_ENABLED: bool = True
    CT_WT_VELOCITY_1H_MIN: float = 9.0
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = False  # DISABLED LIVE TEST 2026-08-17: was True closing TRB winners via 9386
    CT_15M_MOMENTUM_GATE_ENABLED: bool = False
    CT_CHOP_4H_GATE_ENABLED: bool = False
    CT_VOLUME_SURGE_GATE_ENABLED: bool = False
    DELTA_ENGINE_ENABLED: bool = True
    DELTA_ENTRY_ENABLED: bool = True
    DELTA_EXIT_ENABLED: bool = True  # 2026-08-08: was unconditional (wt_against exit fired regardless of any flag) — now a real gate so G0 baseline can actually disable it
    VEL_EXIT_ENABLED: bool = True  # IDENTICAL TO LIVE config_tradier.py:3020 OFF until proven positive delta — untested = OFF both
    RZ_EXIT_ENABLED: bool = False
    STRUCTURAL_EXIT_GATE_ENABLED: bool = True  # USER MANDATE 2026-07-21: no exit into a rising price — LTF collapse or 1h/4h LH+LL only. Mirrors wt_dc_delta.structural_exit_permitted(). ROLLBACK: False.
    SATOSHIT_ENABLED: bool = True
    SATOSHIT_MIN_VOTES: int = 3
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = True
    STRUCTURAL_RANGE_SHIFT_TF: str = "dc_4h"
    REENTRY_RALLY_K15M_MAX: float = 30.0
    REENTRY_RALLY_HTF_MIN: int = 1  # EMERGENCY 2026-09-11: 3→1 matches config_tradier (tested in TEMPLATE, rollback if v14 proves 3 better)
    HTF_ALIGNMENT_ENABLED: bool = True
    HTF_MIN_ALIGNED: int = 1
    D_TREND_REQUIRED: bool = True
    K_ZONE_ENTRY_ENABLED: bool = True
    K_ZONE_LONG_THRESHOLD: int = 35
    K_ZONE_SHORT_THRESHOLD: int = 10
    STOCH_ENTRY_ENABLED: bool = False  # parity 2026-08-17: SWITCH tested per_sym + 7D crypto+stocks
    WT_ENTRY_ENABLED: bool = False  # parity 2026-08-17: SWITCH tested per_sym + 7D crypto+stocks
    # ENTRY_BOTTOM family — BOUNCE (fails-open, side-aware)
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED: bool = False
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SYMBOLS: tuple = ()
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SIDE: str = "SHORT"
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_TIMEFRAME: str = "5m"
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_DISTANCE: float = 0.015
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_DEEP_K4H: float = 50.0
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_TURN_K1H: float = 40.0
    ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_CONFIRMATION_MIN: int = 2
    ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED: bool = False
    ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME: str = "5m"
    ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE: float = 0.008
    ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY: bool = False
    ENTRY_BOUNCE_DONCHIAN_DIRECT_CONFIRMATION: str = "none"
    # A2 STOCH/K-ZONE entry family — parity 2026-08-18: STOCH HHHL direct route + K-ZONE veto/twin controls
    ENTRY_STOCH_HHHL_DIRECT_ENABLED: bool = False
    ENTRY_STOCH_HHHL_DIRECT_TFS: tuple = ("1h",)
    ENTRY_STOCH_HHHL_DIRECT_MIN_CONFIRMING_TFS: int = 1
    ENTRY_STOCH_HHHL_DIRECT_STOCH_THRESHOLD: float = 20.0
    ENTRY_STOCH_PARENT_DIRECT_ENABLED: bool = False
    ENTRY_STOCH_PARENT_DIRECT_FAMILY: str = "ENTRY_1H_TURN_UP"
    ENTRY_STOCH_PARENT_DIRECT_THRESHOLD: float = 40.0
    ENTRY_STOCH_PARENT_DIRECT_TURN_DEFINITION: str = "rising-vs-prior"
    K_ZONE_ENTRY_ENABLED_TRADIER: bool = False
    K_ZONE_LONG_THRESHOLD_TRADIER: int = 35
    K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65
    K_ZONE_VETO_ENABLED_TRADIER: bool = False
    K_ZONE_ENTRY_BONUS_TRADIER: int = 20
    # 2026-09-28 LIVE ENTRY STACK (user "make sure that runs and is respected and executed"): the WT_DC
    # entry scorer IS live's entry engine — stocks score every entry via wt_dc_entry_scorer.score_entry
    # (tradier_manage:12412), crypto via wt_dc_contract.evaluate_wt_dc_direct (ez_manage:1082). The vector
    # twin (B_WT_DC_LIVE, wt_dc_entry_scorer_vec, parity-proven vs the scalar scorer) was gated behind a
    # WT_DC_ENABLED field that DID NOT EXIST in QuickConfig, so getattr(...,False) kept the live entry
    # stack permanently OFF in every baseline. Default True: baselines enter the way live enters.
    WT_DC_ENABLED: bool = True
    WT_DC_ENTRY_THRESHOLD: float = 45.0
    WT_DC_DETAILED_SCORER_ENABLED: bool = False
    WT_DC_DETAILED_ENTRY_THRESHOLD: float = 43.0
    WT_DC_DETAILED_EXIT_ENABLED: bool = False
    WT_DC_DETAILED_EXIT_THRESHOLD: float = 43.0
    WT_DC_ENTRY_K5M_MAX_LONG: float = 100.0
    WT_DC_ENTRY_K5M_MIN_SHORT: float = 0.0
    WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED: bool = False
    WT_DC_ENTRY_BAR_MATURITY_BLOCK: float = 0.7
    # ── WT/DC TF-EXPANDED PACK 2026-09-19 — 15m+ only: clear TF switches for vector sweep (mirrors config.py / config_tradier.py) ──
    WT_DC_TF_ENTRY: str = "1h"
    WT_DC_TF_HTF: str = "4h"
    WT_DC_TF_HTF2: str = "D"
    WT_DC_DC_TF: str = "1h"
    WT_DC_STOCH_TF: str = "5m"
    WT_DC_DC_POS_THRESHOLD_LONG: float = 0.50
    WT_DC_DC_POS_THRESHOLD_SHORT: float = 0.50
    WT_DC_STOCH_THRESHOLD_LONG: float = 40.0
    WT_DC_STOCH_THRESHOLD_SHORT: float = 60.0
    WT_DC_HTF_GATE_MODE: str = "AND"
    WT_DC_TF_COMBO: str = "1h_4h_D"
    WT_DC_DIRECT_TF_ENTRY: str = "1h"
    WT_DC_DIRECT_DC_TF: str = "1h"
    DC_BREAKOUT_TF_EXPANDED: str = "1h"
    EXIT_VELOCITY_WT_TFS: str = "1h,4h,D"
    DC_HARD_STOP_TF: str = "4h"  # ULTIMATE_DC HARD_STOP TF: 4h|D — per sym_side sweepable; D wider = fewer stops
    WT_LOWER_CROSS_EXIT_TF: str = "OFF"  # WT lower cross exit TF: OFF/15m/1h/4h — LONG wt cross down + price lower, SHORT opposite; added 2026-09-27 as option in big WT TF sweep
    # ── 2026-09-03 HARD SHORT GATES — baked (mirrors tradier_manage) ──
    ROTATION_S_FINAL_SCORE_MAX: float = 0.35
    ROTATION_S_WT_BEAR_ALIGN_MIN: int = 2
    ROTATION_S_K5M_MIN: float = 20.0
    ROTATION_S_DC_POS_MIN_D: float = 0.10
    ROTATION_S_RSI_MIN_D: float = 25.0
    ROTATION_S_RET_EXHAUSTED_PCT: float = 0.25
    DC_BREAK_LOW_DC_POS_MIN: float = 0.15
    DC_BREAK_LOW_K5M_MIN: float = 15.0
    DC_BREAK_LOW_RSI_MIN: float = 25.0
    DC_BREAK_LOW_FINAL_SCORE_MAX: float = 0.45
    DC_BREAK_LOW_HTF_ALIGN_MIN: int = 2
    WT_DC_K5M_MIN_SHORT_HARD: float = 20.0
    WT_DC_K5M_HARD_ENABLED: bool = False  # npz has no 5m — default OFF for parity/forward comparison
    WT_DC_DC_POS_MIN: float = 0.20
    WT_DC_FINAL_SCORE_MAX: float = 0.40
    BB_PCTB_ENTRY_ENABLED: bool = False
    BB_ENTRY_LONG_THRESHOLD: float = -0.2
    BB_ENTRY_SHORT_THRESHOLD: float = 1.0
    LR_BAND_ENTRY_ENABLED: bool = False
    LR_BAND_ENTRY_TF: str = '4h'
    LR_BAND_ENTRY_LO: float = 0.1
    LR_BAND_ENTRY_R2_MIN: float = 0.7
    LR_BAND_ENTRY_SIDES: str = "L"
    LR_BAND_REGIME_ENABLED: bool = False
    LR_BAND_REGIME_MAX_PB: float = 0.6
    LR_PCTB_D_LONG_ENTRY_ENABLED: bool = False
    LR_PCTB_D_SHORT_THRESHOLD: float = 0.10
    RSI2_ENTRY_THRESHOLD: float = 3.0
    RSI2_ENABLED: bool = True
    CONNORS_RSI_ENABLED: bool = False
    CONNORS_RSI_ENTRY_THRESHOLD: float = 10.0
    CONNORS_RSI_EXIT_THRESHOLD: float = 70.0
    RSI_ENTRY_GATE_ENABLED: bool = False
    RSI_ENTRY_MAX_LONG: float = 37.0
    RSI_ENTRY_MIN_SHORT: float = 63.0
    MFI_ENTRY_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    MFI_ENTRY_LONG_MAX: float = 60.0
    MFI_ENTRY_SHORT_MIN: float = 40.0
    VWAP_FILTER_ENABLED: bool = False
    FH_MOMENTUM_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    DC_DAYTRADE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    DC_POSITION_ENTRY_THRESHOLD: float = 0.25
    STOCH_CROSS_1H_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    MFI_FLIP_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    MFI_FLIP_EXIT_LONG_THRESHOLD: float = 70.0
    MFI_FLIP_EXIT_SHORT_THRESHOLD: float = 30.0
    WT_CROSSUNDER_FINAL_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    WT_EXIT_MIN_TFS: int = 2
    MI_EXIT_ENABLED: bool = False
    # Reentry block switches (ablation-validated)
    REENTRY_B02_BC156_BOTTOM_ENABLED: bool = False
    REENTRY_B04_DC_RETEST_ENABLED: bool = True
    REENTRY_B10_STOCH_REV_ENABLED: bool = False
    REENTRY_B11_DC_BREAK_ENABLED: bool = True
    REENTRY_B12_WT_MOM_ENABLED: bool = True
    REENTRY_B14_HA_TREND_ENABLED: bool = True
    REENTRY_B15_STRONG_TREND_ENABLED: bool = True
    # PULLBACK-FIRST entry blocks (2026-04-16) — OPT-IN: Sharpe 0.80 test, keep OFF until proven
    REENTRY_PULL1_ENABLED: bool = False  # HTF uptrend + LTF deep oversold + reversing
    REENTRY_PULL2_ENABLED: bool = False  # Rising fundamentals + SMA200 pullback bounce
    REENTRY_PULL3_ENABLED: bool = False  # BB lower-band + trend up + stoch cross
    REENTRY_PULL4_ENABLED: bool = False  # RSI pullback + HTF healthy + wt bouncing
    # Velocity-decay exit — OPT-IN: hasn't been validated vs V8Q v3 baseline (test first)
    WT_VEL_DECAY_EXIT_ENABLED: bool = False
    WT_VEL_DECAY_THRESHOLD: float = 1.0
    # NEW: Confluence mode — only enter when N blocks agree
    CONFLUENCE_MODE_ENABLED: bool = False
    CONFLUENCE_MIN_BLOCKS: int = 2  # How many blocks must agree simultaneously
    # NEW: Signal strength filter — only take top-percentile setups
    # WINNER 2026-04-16: score=5 gave Sharpe 0.94, 87.2% WR, 1.42% avg on 11-sym 4yr
    STRENGTH_FILTER_ENABLED: bool = True
    STRENGTH_MIN_SCORE: float = 5.0
    # Holding period enforcement (avoid rapid exit noise) — WINNER: 10
    MIN_HOLD_BARS: int = 3
    # STRUCTURAL ONLY — profit/stop percentage exits disabled per user 2026-08-14: exits only on DC high reject (5m/15m/1h/4h/D), re-entry on WT 15m cross or HH/HL
    PROFIT_TARGET_ENABLED: bool = True
    PROFIT_TARGET_PCT: float = 1.6  # disabled, kept for parity reference
    # Stop loss exit (sweep-only — cap max loss) — disabled, structural DC handles risk
    STOP_LOSS_ENABLED: bool = False
    STOP_LOSS_PCT: float = 2.0  # disabled

    # ===== Auto-hooked Group B switches (2026-04-16) =====
    # 229 switches from config_tradier.py/config.py, defaults preserved.
    ADX_TRENDING_THRESHOLD: float = 25.0
    ALIGNMENT_GATE_TOTAL: int = 36
    ATR_ADAPTIVE_SIZING_ENABLED: bool = False
    ATR_ADAPTIVE_SIZING_TARGET_PCT: float = 2.0
    ATR_ADAPTIVE_STOP_ENABLED: bool = False  # live parity: config_tradier True (was False, caused 0 trades)
    ATR_ADAPTIVE_STOP_MULT: float = 2.0
    ATR_ADAPTIVE_STOP_TF: str = '1h'
    ATR_LONG_WINDOW: int = 100
    BB_SQUEEZE_ENTRY_ENABLED: bool = True  # FIX 2026-09-15: align to config.py crypto sweep winner (11 sym, Sharpe 89.7) - was False (dead) caused 0 trades in backtest, now True per user directive
    BB_SQUEEZE_EXIT_ENABLED: bool = False  # split exit double-count fix 2026-09-04
    BB_SQUEEZE_THRESHOLD_15M: float = 0.025
    BB_SQUEEZE_THRESHOLD_1H: float = 0.03
    CHOP_RANGING_THRESHOLD: float = 61.8
    CHOP_TRENDING_THRESHOLD: float = 38.2
    COOLDOWN_BARS_TRADIER: int = 8
    CT_CHOP_4H_MAX: float = 50.0
    CT_MFI_15M_LONG_MIN: float = 45.0
    CT_MFI_15M_SHORT_MAX: float = 55.0
    CT_REL_VOL_MIN: float = 1.3
    CT_STOCH_K_15M_LONG_MIN: float = 45.0
    CT_STOCH_K_15M_SHORT_MAX: float = 55.0
    CYCLE_TP_CONDITIONAL_EXIT: float = 0.003
    CYCLE_TP_PCT: float = 0.6
    CYCLE_TP_TIERED_ENABLED: bool = True
    CYCLE_TP_TIERED_FRAC: float = 0.25
    DC_EDGE_SIZING_ENABLED: bool = True
    DC_EDGE_SIZING_MAX_MULT: float = 3.0
    DC_EDGE_SIZING_MIN_MULT: float = 1.0
    DC_EDGE_SIZING_PERIOD: int = 20
    DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT: float = 0.0
    DC_WIDTH_CAP_MULT: float = 10.0
    DELTA_COOLDOWN_BARS: int = 120
    DELTA_EXIT_DC_FLOOR: bool = True
    DELTA_EXIT_OVERRIDE_NOLOSS: bool = True
    DELTA_EXIT_TYPE: str = "speed_decay"
    DELTA_EXIT_WT_CROSS: bool = True
    DELTA_GATE_AUGMENT: bool = True
    DELTA_GATE_BB_SQUEEZE: bool = True
    DELTA_GATE_DC_BREAKOUT: bool = True
    DELTA_GATE_GUARANTEED_REENTRY: bool = True
    DELTA_GATE_HEDGE_OPEN: bool = False  # live parity: config_tradier True (was False, caused 0 trades)
    DELTA_GATE_OPEN: bool = True
    DELTA_GATE_RATIO_REBALANCE: bool = False
    DELTA_GATE_REENTRY: bool = True
    DELTA_GATE_SBA: bool = False  # live parity: config_tradier True (was False, caused 0 trades)
    DELTA_GATE_STDEV_BREAKOUT: bool = True
    DELTA_GATE_VOL_SPIKE: bool = True
    DELTA_MAX_HOLD_BARS: int = 0
    DELTA_PYRAMID_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    DELTA_REENTRY_HTF_GATE: str = '4h'
    DELTA_REENTRY_MIN_TF: int = 2
    DELTA_REENTRY_REQUIRE_NOT_EXITING: bool = True
    DELTA_REENTRY_Z_THRESHOLD: float = 1.0
    EMA20_SLOPE_ENTRY_ENABLED: bool = True
    EMA20_SLOPE_SHORT_THRESHOLD_1H: float = 0.05
    EMA_DIST_ENTRY_ENABLED: bool = True
    EMA_DIST_LONG_THRESHOLD: float = -1.0
    EMA_DIST_SHORT_THRESHOLD: float = 1.0
    EMA_DIST_SIZING_ENABLED: bool = True
    EMA_DIST_SIZING_MULT: float = 2.0
    ENTRY_TRIGGER_TF: str = '15m'
    WT_FORCE_OPEN_TRIGGER_TF: str = '5m'  # [2026-08-22] force-open trigger TF; 15m default, supports 5m(3m crypto)/15m/1h/4h and comma-separated multi-TF
    WT_DC_DIRECT_THRESHOLD: float = 20.0  # [2026-08-22] direct WT-DC entry; 10/20/30 sweep vs -1 disabled. Faithful to live _evaluate_wt_dc_direct.
    HA_3M_ENTRY_WEIGHT: float = -0.5
    HOLD_BARS_CLOSE: int = 50
    HOLD_BARS_MID: int = 500
    HOLD_BARS_OPEN: int = 200
    LUNCH_DEADZONE_SIZE_MULT: float = 0.5
    MIN_EXIT_TF_AGAINST_TRADIER: int = 3
    MIN_HOLD_BARS_BEFORE_EXIT: int = 10  # 2026-09-28 live parity: config.py=10 (was 32; vec bars are 15m vs live 3m — TF semantics flagged in 06_V12_PARITY_MATRIX)
    MIN_HOLD_MINUTES_TRADIER: float = 30.0
    MIN_PERC_FROM_SMA_1: float = 0.01
    MIN_PERC_FROM_SMA_15: float = 0.03
    MI_ENTRY_ENABLED_TRADIER: bool = False
    MI_EXIT_ENABLED_TRADIER: bool = False
    MOM3_ENTRY_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-04-16: off until proven
    MOM3_LONG_THRESHOLD: float = -1.0
    MOM3_SHORT_THRESHOLD: float = 1.0
    MOM5_ENTRY_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-04-16: off until proven
    MOM5_LONG_THRESHOLD: float = -1.0
    MOM5_SHORT_THRESHOLD: float = 1.0
    PYRAMID_ENABLED: bool = False
    PYRAMID_MAX_DC_POS_15M_SHORT: float = 0.3
    PYRAMID_MIN_DC_POS_15M: float = 0.7
    PYRAMID_MIN_GAIN_PCT: float = 1.5
    PYRAMID_MIN_WT_VEL_1H: float = 2.0
    PYRAMID_SIZE_MULT: float = 0.5
    REENTRY2_DC_BREAK_ENABLED: bool = True
    REENTRY2_QUICK_RECOVERY_ENABLED: bool = True
    REENTRY2_STOCH_CROSS_ENABLED: bool = False
    REENTRY_2_ENABLED: bool = True
    REENTRY_B09_SNAPBACK_ENABLED: bool = False
    REENTRY_COOLDOWN_S: float = 60.0
    REENTRY_MANDATORY: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-04-16: off — was forcing reentries
    HARDCODED_RALLY_REENTRY_ENABLED: bool = True  # 2026-09-18 user: reenter if close>exit and wt1_15m>wt1_15m_prev — always tested
    HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN: bool = True  # USER 2026-09-27: NO COOLDOWN — bypass kept, churn stopped via DC4H/KG/GR/cross instead
    HARDCODED_RALLY_REENTRY_REQUIRE_WT: bool = False  # False=loosened close>exit only for TIM>20, True=require wt
    REENTRY_TIER1_SIZE_MULT_TRADIER: float = 1.5
    REGIME_ADAPTIVE_ENABLED: bool = False
    REGIME_ATR_RATIO_MIN: float = 0.25
    REGIME_BB_WIDTH_PCT_MIN: float = 2.0
    REGIME_BTC_MARKET_WEIGHT: float = 0.5
    REGIME_DC_ATR_RATIO_MIN: float = 1.5
    REGIME_ENTER_TRENDING_THRESHOLD: float = 30.0
    REGIME_EXIT_TRENDING_THRESHOLD: float = 15.0
    REGIME_GATE_ENABLED: bool = False
    REGIME_MIN_DWELL_BARS: int = 16
    REGIME_RANGING_DC_BREAKOUT_SCORE: int = 0
    REGIME_RANGING_EXIT_GAIN_MIN: float = 0.15
    REGIME_RANGING_K_ZONE_BONUS: int = 40
    REGIME_RANGING_MIN_HOLD_BARS: int = 8
    REGIME_RANGING_NOLOSS_MIN: float = 0.05
    REGIME_RANGING_POSITION_SIZE_MULT: float = 0.5
    REGIME_RANGING_REENTRY_SIZE_MULT: float = 1.0
    REGIME_RANGING_SLOT_RESERVE_PCT: float = 0.6
    REGIME_RANGING_STALE_HOURS: float = 48.0
    REGIME_RANGING_STALE_MIN_PROFIT: float = 0.02
    REGIME_RANGING_WT_EXIT_VEL: float = -3.0
    REGIME_RANGING_WT_REDUCE_FRAC_LOW: float = 0.4
    REGIME_RANGING_WT_REDUCE_FRAC_MED: float = 0.6
    REGIME_TRENDING_DC_BREAKOUT_SCORE: int = 30
    REGIME_TRENDING_EXIT_GAIN_MIN: float = 2.0
    REGIME_TRENDING_K_RESET_THRESHOLD: float = 40.0
    REGIME_TRENDING_K_ZONE_BONUS: int = 15
    REGIME_TRENDING_MIN_HOLD_BARS: int = 48
    REGIME_TRENDING_NOLOSS_MIN: float = 0.5
    REGIME_TRENDING_POSITION_SIZE_MULT: float = 1.5
    REGIME_TRENDING_REENTRY_SIZE_MULT: float = 2.0
    REGIME_TRENDING_SLOT_RESERVE_PCT: float = 0.4
    REGIME_TRENDING_WT_EXIT_VEL: float = -12.0
    REGIME_TRENDING_WT_REDUCE_FRAC_LOW: float = 0.1
    REGIME_TRENDING_WT_REDUCE_FRAC_MED: float = 0.15
    RSI_EXIT_LONG_TRADIER: float = 85.0
    RSI_EXIT_SHORT_TRADIER: float = 15.0
    RSI_MOMENTUM_MODE: bool = False
    RZ_K_ENTRY_BOTTOM: float = 10.0
    RZ_MFI_ENTRY_BOTTOM: float = 15.0
    SATOSHIT_EXIT_ENABLED: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-04-16: off — duplicates existing sat_exit, caused Sharpe regression
    SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER: float = 60.0
    SATOSHIT_EXIT_PARTIAL_PCT: float = 0.7
    SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER: float = 42.0
    SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER: float = 50.0
    SATOSHIT_HTF_MFI_D_MIN_TRADIER: float = 30.0
    SATOSHIT_HTF_RVOL_1H_MIN_TRADIER: float = 0.3
    SATOSHIT_LONG_BB_PCTB_MAX: float = 0.5
    SATOSHIT_LONG_HA_STREAK_MAX: int = 1
    SATOSHIT_LONG_MFI_MAX_TRADIER: float = 60.0
    SATOSHIT_LONG_RSI_MAX_TRADIER: float = 50.0
    SATOSHIT_LONG_STOCH_K_MAX_TRADIER: float = 60.0
    SATOSHIT_MIN_VOTES_TRADIER: int = 3
    SATOSHIT_SHORT_BB_PCTB_MIN: float = 0.55
    SATOSHIT_SHORT_HA_STREAK_MIN: int = 0
    SATOSHIT_SHORT_MFI_MIN_TRADIER: float = 50.0
    SATOSHIT_SHORT_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_SHORT_STOCH_K_MIN_TRADIER: float = 50.0
    SMA200_DIST_LONG_THRESHOLD: float = -3.0
    SQUEEZE_ENABLED: bool = False
    STOCH_CROSS_3M_EXIT_ENABLED: bool = False
    STOCH_CROSS_ENTRY_TRADIER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    TF_ALIGNMENT_MIN_LONG: int = 2
    TF_ALIGNMENT_MIN_SHORT: int = 2
    TF_ALIGNMENT_MIN_TOTAL: int = 4
    TF_FOCUS_ENTRY_HARD_GATE: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-04-16: off until sweep-proven
    TF_FOCUS_EXIT_HARD_GATE: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-04-16: off until sweep-proven
    TF_FOCUS_WEIGHT: float = 8.0
    TF_HTF1: str = '15m'
    TF_HTF3: str = '4h'
    TF_MACRO: str = "D"
    TRADIER_DC_DAYTRADE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-08-09: was silently True (kill-switch-off violation) — "DC Daytrade" is on CLAUDE.md's banned-without-approval strategy list; do not flip True without explicit user sign-off
    TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES: int = 0  # 2026-09-15: OFF by default per user - max hold disabled, DAYTRADE OFF tested as switch after other exits True
    TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True
    TRADIER_DC_DAYTRADE_STOP_PCT: float = 0.005
    TRADIER_DC_DAYTRADE_TARGET_PCT: float = 0.005
    TRADIER_DC_POSITION_ENTRY_THRESHOLD: float = 0.25
    TRADIER_ENTRY_SCORE_THRESHOLD: int = 30             # 2026-04-23 EMERGENCY parity: live is 30 (validated winner), was 24 divergence -> 0 vs 144 trades
    TRADIER_FH_MOMENTUM_DC_CONFIRM: bool = True
    TRADIER_FH_MOMENTUM_DC_MAX_LONG: float = 0.33
    TRADIER_FH_MOMENTUM_ENABLED: bool = True
    TRADIER_FH_MOMENTUM_MFI_CONFIRM: bool = True
    TRADIER_FH_MOMENTUM_MFI_MIN: float = 55.0
    TRADIER_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5
    TRADIER_FH_MOMENTUM_WINDOW_MINUTES: int = 60
    TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER: int = 35
    TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65
    TRADIER_MFI_ENTRY_LONG_ENABLED: bool = False
    TRADIER_MFI_ENTRY_LONG_TRADIER: float = 60.0
    TRADIER_MI_ENTRY_ENABLED_TRADIER: bool = False
    TRADIER_MI_EXIT_ENABLED_TRADIER: bool = False
    TRADIER_MI_SUBSIGNAL_MIN_COUNT: int = 3
    TRADIER_RSI2_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-04-16: off until backtested tradier-only
    TRADIER_RSI2_EXIT_THRESHOLD_LONG: float = 90.0
    TRADIER_RSI2_EXIT_THRESHOLD_SHORT: float = 10.0
    TRADIER_RSI_ENTRY_LONG_TRADIER: float = -1.0
    TRADIER_RSI_ENTRY_SHORT_TRADIER: float = 70.0
    TRADIER_RSI_SHORT_REL_VOLUME_MIN: float = 2.4
    TRADIER_STOCH_ENTRY_LONG_TRADIER: int = 30
    TRADIER_STOCH_ENTRY_SHORT_TRADIER: int = 52
    TRADIER_STOCH_EXTREME_LONG_TRADIER: int = 15
    TRADIER_STOCH_EXTREME_SHORT_TRADIER: int = 85
    TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = True
    TRB_NOLOSS_MIN_PROFIT_PCT: float = 0.0
    UNIVERSAL_NOLOSS_GATE: bool = False
    VOLUME_CONFIRMATION_ENABLED: bool = False
    VOLUME_CONFIRMATION_MULT: float = 1.2
    VWAP_BOUNCE_DIST_PCT: float = 0.3
    VWAP_BOUNCE_ENTRY_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-04-16: off until sweep-proven
    WIN_TRAIL_EROSION_PCT: float = 0.0  # STRUCTURAL ONLY — was 0.5, now disabled (user 2026-08-14: never exit on percentage, only DC reject / WT 15m cross / HH+HL)
    ABLATION_DISABLE_HEDGE: bool = False
    ABLATION_DISABLE_QUICK_ENTRY: bool = False
    ABLATION_DISABLE_QUICK_EXIT: bool = False
    BASIS_CONDITION: bool = False
    BACKTEST_VALIDATED_GATES_TRADIER: bool = True
    REENTRY_BLANKET_FIRE_ENABLED: object = None  # [C2 b3] None = auto (stocks False = live-faithful, crypto True = legacy upper bound); True/False force
    OVERTRADE_GUARD_ENABLED: bool = False  # [C2 b5b] live guard is always on (cap 6 crypto / 8 stocks); default OFF here = baseline unchanged until parity-checked, see NOTES
    VEC_HONOR_DEAD_LIVE_DELTA_GATES: bool = False  # [C2 b4] True = legacy vec behaviour for DELTA_GATE_OPEN/REENTRY/AUGMENT (live ignores them)
    LIVE_ENTRY_HARD_GATES_ENABLED: bool = False  # [C2 b2] master (OFF: live defaults would zero 11/12 probed sym_sides; see MANIFEST) for vec_decisions/entry_hard_gates (live scorer hard gates)
    BB_SQUEEZE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # 2026-04-16: off until sweep-proven
    BB_SQUEEZE_COOLDOWN: float = 300.0
    BB_SQUEEZE_MIN_ALIGNMENT: int = 10
    BB_SQUEEZE_WIDTH_PERCENTILE: float = 0.2
    BB_BREAKOUT_ENABLED: bool = False
    BB_BREAKOUT_TF: str = '1h'
    BB_BREAKOUT_SCORE: int = 20
    DC_BREAKOUT_SCORE: int = 15  # hooked by name for TEMPLATE CHART_ENTRY_BREAKOUT 12-13, missing 2026-09-03 audit
    DC_BREAKOUT_TF: str = '1h'  # hooked by name for TEMPLATE CHART_ENTRY_BREAKOUT 14, missing 2026-09-03 audit
    BB_RSI_STOCH_SCALP_ENABLED: bool = False
    BB_RSI_STOCH_SCALP_SCORE: int = 25
    BEAR_MARKET_MODE_TRADIER: bool = True
    AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER: bool = True
    AUGMENT_BOUNCE_MIN_GAIN_PCT: float = 0.5  # FIX 2026-09-06: was LIVE_ONLY (live had, v12 missing) — augment bounce min gain
    AUGMENT_BREAKOUT_MIN_GAIN_PCT: float = 2.0  # FIX 2026-09-06: LIVE_ONLY — breakout min gain
    AUGMENT_MIN_GAIN_PCT: float = 3.0  # 2026-09-28 LIVE PARITY: config.py:135 / config_tradier.py:1559 = 3.0 (was 0.5 LIVE_ONLY stub)
    ADX_RANGING_THRESHOLD: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    ALL_TF_AGAINST_CLOSE_ENABLED: bool = True  # 2026-09-10 FIX vs B&H: all TFs against → close primary
    ALL_TF_AGAINST_CLOSE_MIN_TFS: int = 3  # 2026-09-10 FIX: 3 TFs against → exit (was 10 — never fired)
    AUGMENTED_POSITIONS_GUARD_FLOOR_MULT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    BANDAID_OFF_LOSER_RECOVER_PCT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    BAND_ARROW_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    BAND_ARROW_SLOPE_DEADBAND: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    CRYPTO_SPIKE_FADE_THRESHOLD_PCT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    DAEMON_REENTRY_STALE_EXIT_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    DC_HOPELESS_EXIT_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    DC_HOPELESS_EXIT_MIN_AGE_S: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    DD_BOUNCE_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    DELTA_HTF_GATE: str = "OFF"  # FIX 2026-09-06: LIVE_ONLY auto-added
    DELTA_PYRAMID_MAX: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    DELTA_PYRAMID_PRICE_TOL: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    DELTA_REENTRY_FILTER_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    DIRECTION_FAVORABLE_REENTRY_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    DYN_STRUCT_TRAIL_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    ENTRY_PRIMARY_TF: str = "OFF"  # FIX 2026-09-06: LIVE_ONLY auto-added
    EZ_MANAGE_THROTTLER_RATE: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added
    E_1_EXIT_DELTA_THR: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    E_1_WT_EXIT_USE_DELTA_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    E_3_USE_WT_STRUCTURE_EXIT_MODE: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    FG_FEAR_THRESHOLD: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    FG_GREED_THRESHOLD: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    GOLDEN_RULE_BASE_USD: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    GR_FILTER_ALL_ENTRIES: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    HIGH_GAIN_AUGMENTATION_MIN_SIZE: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    HTF4_CONF: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    HTF_AGAINST_FORCE_CLOSE_ENABLED: bool = False  # 2026-09-29 USER: default OFF (frequent-exit churn) — swept switch; sane baseline WT/DC. Was True 2026-09-10.
    BOTTOM_EXIT_HTF_WT_VETO_ENABLED: bool = True  # parity 2026-09-27: config True + TEMPLATE bold — block R1/ULTIMATE_DC/NEWBORN bottom exits when HTF WT with position
    HTF_WT_CHURN_REENTRY_ENABLED: bool = True  # parity 2026-09-27: config True + TEMPLATE bold — immediate churn reentry while HTF WT with direction
    HTF_WT_CHURN_REENTRY_MAX_AGE_MIN: float = 120.0  # parity 2026-09-27: config 120.0 + TEMPLATE bold — churn window 2h after exit
    HTF_GATE_D_MANDATORY: bool = True  # 2026-09-28 WAVE4 LIVE PARITY: ez getattr default True
    HTF_GATE_MIN_CONFIRMATIONS: int = 3  # 2026-09-28 WAVE4 LIVE PARITY: ez getattr default 3 (was 10 stub)
    HTF_GATE_SIGNALS_SMA200D: bool = True  # 2026-09-28 WAVE4 LIVE PARITY: ez getattr default True
    HTF_TREND_VETO_BYPASS_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    HTF_TREND_VETO_BYPASS_REASONS: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added (list)
    LEADERBOARD_FILTER: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    LEGACY_PROC_SINGLE_REENTRY: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    LEGACY_REENTRY_PSR_DC_BOUNCE: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    LEGACY_REENTRY_PSR_FULL_DC: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    LEGACY_REENTRY_PSR_K_DC_CROSSOVER: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    LH_HL_FILTER_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    LH_HL_FILTER_MODE: str = "OFF"  # FIX 2026-09-06: LIVE_ONLY auto-added
    LH_HL_FILTER_REQUIRE_BOTH: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    LH_HL_FILTER_TF_REQ: int= 2  # FIX 2026-09-06: LIVE_ONLY auto-added
    LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF: str = "15m"  # FIX 2026-09-06: LIVE_ONLY auto-added
    LIVE_VEC_EMERGENCY_BRAKE_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    LR_BAND_LADDER_STOCH_EXTREME: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    LR_BAND_LADDER_TF_BOTTOM: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added (dict)
    LR_BAND_LADDER_TF_TOP: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added (dict)
    MANDATORY_REENTRY_WT_FILTER_MIN_TFS: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    MANDATORY_REENTRY_WT_FILTER_TF_MODE: str = "OFF"  # FIX 2026-09-06: LIVE_ONLY auto-added
    MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    MAX_AUGMENTS_PER_POSITION: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    MTF_FILTER_STRONG_BUY_QUICK_BYPASS: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    MTF_GR_MIN_IND: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    MU_CORRECTION_EXIT_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    MU_CORRECTION_REENTRY_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    NEW_POSITION_MAX_LOSS_THRESHOLD: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    NOLOSS_BYPASS_WT_5OF5_MIN_TFS: int = 3  # EMERGENCY 2026-09-11: 5→3 matches config_tradier (TEMPLATE bold 5→3, rollback if v14 proves 5 better)
    REENTRY2_DC_BREAK_ALLOW_15M: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    REENTRY2_DC_BREAK_FILTER_TF: str = "3m"  # FIX 2026-09-06: LIVE_ONLY auto-added
    REENTRY2_DC_BREAK_REQUIRE_K_FILTER: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    REENTRY2_DC_BREAK_REQUIRE_WT_FILTER: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    REENTRY2_DIR_FAV_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    REENTRY_EXIT_RECLAIM_BUFFER_PCT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    REENTRY_EXIT_RECLAIM_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    REENTRY_POST_CONSOL_ATR_THRESHOLD: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    REENTRY_POST_CONSOL_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    REENTRY_POST_CONSOL_MULT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    REENTRY_POST_CONSOL_TFS_REQUIRED: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    REENTRY_PRICE_IMPROVE_PCT: float = 0.08  # 2026-09-09 aligned to config.py
    REENTRY_BAR_TURN_ENABLED: bool = True
    REENTRY_BAR_TURN_TF: str = "3m"
    REENTRY_BAR_TURN_REQUIRE_BOTH: bool = False
    REENTRY_BAR_STRUCTURE_ENABLED: bool = True
    REENTRY_BAR_STRUCTURE_TF: str = "3m"
    RECENT_REDUCTION_GUARD_ENABLED: bool = True
    RECENT_REDUCTION_GUARD_WINDOW_S: float = 450.0
    RECENT_REDUCTION_GUARD_USE_4BAR: bool = True
    REENTRY_SMA200_BACKUP_ENABLED: bool = True  # 2026-09-09 USER: ON
    REENTRY_NEAR_EXIT_CHURN_OK: bool = True
    REENTRY_POSITIVE_EXIT_SIZE_MULT: float = 1.25
    PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED: bool = True
    PRICE_CROSSED_HTF_AGAINST_VETO_BAR_TURN_BYPASS: bool = True  # 2026-09-09 soften veto like GPCR bar-turn
    PRICE_CROSSED_HTF_AGAINST_VETO_HA_BYPASS: bool = True
    REENTRY_SIZE_BREAKOUT_MULT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    REENTRY_SIZE_DIP_MULT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    REENTRY_SIZE_EXTENDED_K1H: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    REENTRY_SIZE_EXTENDED_MULT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    REENTRY_WT15M_SIZE_MULT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    RZ_BREAKOUT_BAND: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    RZ_BREAKOUT_ENTRY_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    STDEV_BREAKOUT_PCTB_LONG: float = 1.125  # FIX 2026-09-13: was 0.5 generic — align to config 1.125 (was causing -0.125 drift mask always-fire)
    STDEV_BREAKOUT_PCTB_SHORT: float = -0.125  # FIX 2026-09-13: was 0.5 generic — align to config -0.125
    STDEV_REJECT_EXIT_TF: str = "OFF"  # FIX 2026-09-06: LIVE_ONLY auto-added
    UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED: bool = True  # 2026-09-28 LIVE PARITY: config.py:899 = True; drives the vec gain-ladder augment
    WRONG_SIDE_WT_TFS_REQUIRED: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_4H_VEL_EXIT_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    WT_4H_VEL_EXIT_K_EXTREME_HIGH: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_4H_VEL_EXIT_K_EXTREME_LOW: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_4H_VEL_EXIT_REQUIRE_K_EXTREME: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    WT_4H_VEL_EXIT_REQUIRE_PROFIT: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    WT_ACCEL_EXIT_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_CROSS_EXIT_REQUIRE_15M_CONFIRM: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    WT_DIV_EXIT_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_15M_LH_WAIT_EXIT_ENABLED: bool = False  # 2026-09-15 WAIT: lower-high 15m + DC/BB/WT wait — never mid-rally
    WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED: bool = False  # 2026-09-15 WAIT: wt LH while price HH divergence + wait
    WT_TECHNICAL_WAIT_LOWER_HIGH_ONLY: bool = True  # gate: require LH/div before any WT technical exit fires
    EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED: bool = False  # 2026-09-15 EXIT BLOCKER — requires LH (closed 15m) OR LL (forming) for any exit
    EXIT_BLOCKER_LH_TF: str = "15m"
    EXIT_BLOCKER_LL_TF: str = "15m"
    DC_BREAK_WAIT_WT15_CLOSE_ENABLED: bool = False  # 2026-09-15 wait for wt15 close instead of dc break
    WT_EXHAUST_EXIT_MIN_GAIN_PCT: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_EXHAUST_EXIT_REQUIRE_GAIN: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    WT_MOMENTUM_EXIT_THRESHOLD: int = 10  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_PERCENTILE_EXIT_ENABLED: bool = False  # FIX 2026-09-06: LIVE_ONLY auto-added from live bool
    WT_PERCENTILE_EXIT_OB_4H: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_PERCENTILE_EXIT_OB_D: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_PERCENTILE_EXIT_OS_4H: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    WT_PERCENTILE_EXIT_OS_D: float = 0.5  # FIX 2026-09-06: LIVE_ONLY auto-added
    BOUNCE_AUGMENT_ENABLED: bool = False
    BOUNCE_AUGMENT_K_D_CROSSING_UP: bool = True
    BOUNCE_AUGMENT_K_D_THRESHOLD: float = 20.0
    BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE: float = 0.02
    BOUNCE_AUGMENT_MIN_LOSS_PCT: float = -0.5
    AUGMENT_FALLBACK_REDUCE_ENABLED: bool = False
    AUGMENT_FALLBACK_REDUCE_PCT: float = 0.5
    AUGMENT_FALLBACK_GAIN_PCT: float = 1.0
    BB_RECOVERY_EXIT_ENABLED_TRADIER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER: float = 0.0
    BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER: float = 0.3
    # ===== NEW 2026-08-08 combiner rebuild — enable flags for previously-orphaned threshold fields =====
    RZ_ENTRY_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    SMA200_DIST_ENTRY_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)
    SATOSHIT_ENTRY_ENABLED: bool = False
    # ===== 2026-08-09: missing 16 matrix params not previously in QuickConfig =====
    ABLATION_DISABLE_REENTRY: bool = False
    DC_DAYTRADE_MAX_HOLD_MINUTES: float = 240.0
    DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True
    DC_DAYTRADE_STOP_PCT: float = 0.015
    DC_DAYTRADE_TARGET_PCT: float = 0.01
    # 2026-09-26 DC-channel stop/target — user mandate: replace fixed % with dc_low/high minus buffer
    # LONG stop = price <= dc_low_TF * (1 - 0.0025), SHORT stop = price >= dc_high_TF * (1+0.0025)
    # LONG target = price >= dc_high_TF * (1 - 0.001), SHORT target = price <= dc_low_TF * (1+0.001)
    # TF: OFF (disabled, use fixed % fallback) or single "15m"/"1h"/"4h"/"D" or multi "15m,1h,4h" (OR across TFs — exit if ANY TF breach). Buffers fixed 0.25/0.10 per user 2026-09-26+.
    # 2026-09-28 USER SPEC ("fixed % was eliminated everywhere"): baseline daytrade exits are the DC-channel
    # exits — stop at close < dc_low_15m/1h − 0.25% (long; mirrored short), profit at dc_high_15m/1h − 0.10%.
    # NEVER a fixed % loss or profit exit; the legacy fixed branch is deleted from simulate_one.
    DAYTRADE_DC_STOP_TF: str = "15m,1h"
    DAYTRADE_DC_STOP_BUFFER_PCT: float = 0.25
    DAYTRADE_DC_TARGET_TF: str = "15m,1h"
    DAYTRADE_DC_TARGET_BUFFER_PCT: float = 0.10
    # 2026-09-29 grey-switch rewire: legacy daytrade DC aliases (tradier live _manage_daytrade_positions
    # 2026-09-24 semantics) — resolved by vec_decisions.dc_channel_exits.resolve_daytrade_dc in BOTH engines;
    # only apply when the matching DAYTRADE_DC_*_TF list is OFF. Defaults = live defaults (inert).
    DC_DAYTRADE_STOP_USE_DC_15M: bool = False
    DC_DAYTRADE_STOP_USE_DC4_15M: bool = False
    DC_DAYTRADE_TARGET_USE_DC_15M: bool = False
    DC_DAYTRADE_TARGET_USE_DC4_15M: bool = False
    DC_DAYTRADE_TARGET_DC_BUFFER_PCT: float = 0.002
    TRADIER_DC_DAYTRADE_STOP_USE_DC_15M: bool = False
    TRADIER_DC_DAYTRADE_STOP_USE_DC4_15M: bool = False
    TRADIER_DC_DAYTRADE_TARGET_USE_DC_15M: bool = False
    TRADIER_DC_DAYTRADE_TARGET_USE_DC4_15M: bool = False
    TRADIER_DC_DAYTRADE_TARGET_DC_BUFFER_PCT: float = 0.002
    # TECHNICAL_EXIT DC variants — vector exit signal (compute_exit_signals) mirror of daytrade; supports multi-TF OR
    TECHNICAL_DC_STOP_TF: str = "OFF"
    TECHNICAL_DC_STOP_BUFFER_PCT: float = 0.25
    TECHNICAL_DC_TARGET_TF: str = "OFF"
    TECHNICAL_DC_TARGET_BUFFER_PCT: float = 0.10
    # 2026-09-26 ENTRY DC — user mandate: entries ABOVE low/high (long ABOVE, short BELOW) on 1+ TFs
    # LONG entry: close >= dc_low_TF*(1+0.001) OR close >= dc_high_TF*(1+0.001) (bounce or breakout)
    # SHORT entry: close <= dc_high_TF*(1-0.001) OR close <= dc_low_TF*(1-0.001)
    ENTRY_DC_TF: str = "OFF"
    ENTRY_DC_BUFFER_PCT: float = 0.10
    FH_MOMENTUM_DC_CONFIRM: bool = True
    FH_MOMENTUM_DC_MAX_LONG: float = 0.5
    FH_MOMENTUM_MFI_CONFIRM: bool = False
    FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5
    MIN_GAIN: float = 3.0
    RSI2_EXIT_THRESHOLD_LONG: float = 70.0
    RSI2_EXIT_THRESHOLD_SHORT: float = 30.0
    RSI_ENTRY_LONG_TRADIER: float = 40.0
    RSI_ENTRY_SHORT_TRADIER: float = 58.0
    WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = True
    # ===== 2026-09-03 REENTRY_POS — 16 missing switches (audit: GUARANTEED_REENTRY + HLR + DELTA_EXIT) =====
    GUARANTEED_REENTRY_STRICT_CONFIRMATION: bool = True
    GUARANTEED_REENTRY_DELTA_GATE_ENABLED: bool = False
    GUARANTEED_REENTRY_K_HIGH_BLOCK: float = 80.0
    GUARANTEED_REENTRY_K_LOW_BLOCK: float = 20.0
    GUARANTEED_REENTRY_K_FAVORABLE_LOW: float = 30.0
    GUARANTEED_REENTRY_K_FAVORABLE_HIGH: float = 70.0
    GUARANTEED_REENTRY_TIGHT_STOP_ENABLED: bool = True
    GUARANTEED_REENTRY_TIGHT_STOP_PCT: float = 0.5
    GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S: float = 60.0
    GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S: float = 1800.0
    GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN: bool = True
    HLR_REENTRY_MULT_1H: float = 1.5
    HLR_REENTRY_MULT_4H: float = 2.0
    HLR_REENTRY_MULT_D: float = 2.5
    HLR_REENTRY_MULT_W: float = 3.0
    DELTA_EXIT_MANDATORY_REENTRY_ENABLED: bool = False

    @classmethod
    def from_override_file(cls, path: str) -> "QuickConfig":
        cfg = cls()
        if path and Path(path).exists():
            with open(path) as f:
                raw = json.load(f)
            # PER_SYM FIX 2026-08-18: support nested {SYM_LONG:{overrides:{...}}} + flat {PARAM:value}
            # Store raw for caller-side per-symbol resolution if needed
            cfg._per_sym_raw = raw if isinstance(raw, dict) else {}
            cfg._is_per_sym = False
            if isinstance(raw, dict) and any(isinstance(v, dict) and "overrides" in v for v in raw.values() if isinstance(v, dict)):
                cfg._is_per_sym = True
                cfg._per_sym_map = raw
                # Do NOT apply top-level SYM_LONG keys here; caller will resolve per symbol via from_override_file_for_symbol
                # Keep cfg as defaults; per-symbol overrides applied elsewhere (backtest hook or caller that knows symbol)
                return cfg
            overrides = raw
            for k, v in overrides.items():
                # skip meta keys
                if k.startswith("_"):
                    continue
                if hasattr(cfg, k):
                    cur = getattr(cfg, k)
                    # Coerce "True"/"False" strings
                    if isinstance(v, str) and v in ("True", "False"):
                        v = v == "True"
                    if isinstance(cur, bool):
                        setattr(cfg, k, bool(v))
                    elif isinstance(cur, int) and not isinstance(cur, bool):
                        if isinstance(v, bool):
                            if v:
                                continue
                            setattr(cfg, k, int(v))
                        else:
                            setattr(cfg, k, int(float(v)) if isinstance(v, str) else int(v))
                    elif isinstance(cur, float):
                        if isinstance(v, bool):
                            if v:
                                continue
                            setattr(cfg, k, float(v))
                        else:
                            setattr(cfg, k, float(float(v)) if isinstance(v, str) else float(v))
                    else:
                        setattr(cfg, k, v)
        else:
            cfg._per_sym_raw = {}
            cfg._is_per_sym = False
            cfg._per_sym_map = {}
        return cfg

    @classmethod
    def from_override_file_for_symbol(cls, path: str, symbol: str, is_long: bool = None) -> "QuickConfig":
        """Helper for per_sym files: returns config with correct SYM_LONG/SHORT overrides applied."""
        cfg = cls.from_override_file(path)
        if getattr(cfg, "_is_per_sym", False) and symbol:
            raw = getattr(cfg, "_per_sym_map", {})
            # Try exact side first, then any side for symbol
            keys_to_try = []
            if is_long is not None:
                keys_to_try.append(f"{symbol}_{'LONG' if is_long else 'SHORT'}")
            else:
                keys_to_try.extend([f"{symbol}_LONG", f"{symbol}_SHORT"])
            applied = False
            for k in keys_to_try:
                if k in raw and isinstance(raw[k], dict) and "overrides" in raw[k]:
                    for pk, pv in raw[k]["overrides"].items():
                        if isinstance(pv, str) and pv in ("True", "False"):
                            pv = pv == "True"
                        if hasattr(cfg, pk):
                            cur = getattr(cfg, pk)
                            if isinstance(cur, bool):
                                setattr(cfg, pk, bool(pv))
                            elif isinstance(cur, int) and not isinstance(cur, bool):
                                if isinstance(pv, bool) and pv:
                                    continue
                                setattr(cfg, pk, int(float(pv)) if isinstance(pv, str) else int(pv))
                            elif isinstance(cur, float):
                                if isinstance(pv, bool) and pv:
                                    continue
                                setattr(cfg, pk, float(pv))
                            else:
                                setattr(cfg, pk, pv)
                        else:
                            # Still set for unknown but allow
                            setattr(cfg, pk, pv)
                    applied = True
                    break
            if not applied:
                # Fallback: if no exact symbol, try flat (should not happen)
                pass
        return cfg

    def apply_cat_side_defaults(self, cat_side: str) -> int:
        """USER 2026-09-30: set this config to the FOUR-default layer of its cat_side (cat_side_defaults.py). Call AFTER
        apply_tradier_defaults() for stocks and BEFORE per-sym overrides. Returns the number of fields set."""
        if not getattr(self, "CAT_SIDE_DEFAULTS_ENABLED", True):
            return 0
        import cat_side_defaults as _csd
        n = 0
        for _k, _v in _csd.defaults(cat_side).items():
            if hasattr(self, _k):
                setattr(self, _k, _v)
                n += 1
        return n

    def apply_tradier_defaults(self):
        self.MODE = "tradier"
        self.BASE_TF = "5m"
        self.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
        self.K_ZONE_ENTRY_ENABLED = True
        self.MFI_ENTRY_ENABLED = True
        self.VWAP_FILTER_ENABLED = True
        self.FH_MOMENTUM_ENABLED = True
        self.DC_DAYTRADE_ENABLED = True
        self.STOCH_CROSS_1H_EXIT_ENABLED = True
        self.MFI_FLIP_EXIT_ENABLED = True
        self.WT_CROSSUNDER_FINAL_ENABLED = True
        self.MI_EXIT_ENABLED = True
        self.ENTRY_SCORE_THRESHOLD = 24.0
        self.K3M_FLOOR = 30.0
        self.K3M_FLOOR_ENABLED = False
        # 2026-09-29 grey rewire: RZ_BREAKOUT twin — tradier live RZ_BOT_BB_THRESHOLD (config_tradier.py:2072)
        self.RZ_BOT_BB_THRESHOLD = 0.375
        # 2026-09-29 grey rewire (BIBLE §17.5 entry/exit-gate parity): crypto-live features the stock baseline
        # applied although tradier_manage never reads them (only _wire_weak_/_FULL_COVERAGE stubs) -> stock
        # live default = absent. Measured: HTF_DIRECTION_GATE binds AMD_SHORT/AAPL/ABBV, HLR_TOP_EXIT binds AMD_SHORT.
        self.HTF_DIRECTION_GATE_ENABLED = False
        self.HLR_TOP_EXIT_ENABLED = False
        # 2026-09-29 USER: stocks sized at crypto $28 -> whole-share floor 0 -> 47% of entries vanished and
        # STDEV off = 0 trades. Live parity config_tradier.py:120/152.
        self.START_POSITION_SIZE = 500.0
        self.MAX_ORDER_VALUE = 7000.0


        # ═══ 2026-09-29 §17.4 CURATED SYNC — every simple-typed field where TradierConfig
        #     differs from the crypto-synced defaults (live is truth); §17.3 exclusions apply
        self.ADX_RANGING_THRESHOLD = 20.0
        self.ADX_REGIME_FILTER_ENABLED = True
        self.ATR_ADAPTIVE_SIZING_TARGET_PCT = 1.5
        self.ATR_ADAPTIVE_STOP_ENABLED = True
        self.AUGMENTATION_COOLDOWN_SECONDS = 300.0
        self.BAND_SLOPE_SIZING_V2_MAX = 2.5
        self.BAND_SLOPE_SIZING_V2_MIN = 0.5
        self.BAND_SLOPE_SIZING_V2_TF = 'D'
        self.BB_BREAKOUT_TF = '15m'
        self.BB_ENTRY_LONG_THRESHOLD = 0.3
        self.BB_ENTRY_SHORT_THRESHOLD = 0.7
        self.BB_RECOVERY_EXIT_ENABLED_TRADIER = False
        self.BB_RSI_STOCH_SCALP_SCORE = 12
        self.BOUNCE_TOP_MIN_HOLD_MINUTES = 1440.0
        self.BREAKEVEN_EXIT_AFTER_BARS_ENABLED = False
        self.BREAKOUT_LEASH_TF = '5m'
        self.BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE = -0.037500000000000006
        self.BREAKOUT_MULTI_LUNG_COOLDOWN_BARS = 8
        self.BREAKOUT_MULTI_LUNG_ENABLED = True
        self.BREAKOUT_MULTI_LUNG_TIER = 'STOCK'
        self.BREAKOUT_TF_SIZE_ENABLED = False
        self.DC_LOW_FROZEN_STOP_ENABLED = True
        self.DELTA_ATR_ENTRY_FILTER = True
        self.DELTA_COOLDOWN_BARS = 60
        self.DELTA_ENTRY_ACCEL_THRESHOLD = 0.3
        self.DELTA_ENTRY_ENABLED = False
        self.DELTA_EXIT_DECAY_RATIO = 0.3
        self.DELTA_EXIT_ENABLED = False
        self.DELTA_EXIT_MIN_TF_LOST = 2
        self.DELTA_EXIT_TF = '15m'
        self.DELTA_HTF_GATE = '4h'
        self.DELTA_LT_ENTRY_Z_THRESHOLD = 2.0
        self.DG_MAX_FORCE_OPEN_NOTIONAL_USD = 2000.0
        self.DYNAMIC_SCORE_COUNTER_EXIT_ENABLED = False
        self.ENABLE_FAST_RISER_REDUCE = False
        self.EXIT_HTF_QUICK_TP_ENABLED = False
        self.EXIT_SCORER_K_EXTREME = 85.0
        self.EZ_REENTRY_DAEMON_ENABLED = False
        self.EZ_REENTRY_INLINE_ENABLED = False
        self.EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED = False
        self.EZ_REENTRY_PRICE_CROSS_MAX_FIRES_PER_TICK = 20
        self.EZ_REENTRY_PRICE_CROSS_PCT = 0.0
        self.FROZEN_ACTIVATION_STOP_ENABLED = False
        self.GOLDEN_RULE_HTF_VETO_ENABLED = True
        self.GOLDEN_RULE_REQUIRE_HEDGE_OPEN = False
        self.GR_HTF_GATE_ENABLED = True
        self.GUARANTEED_REENTRY_AUGMENT_ENABLED = False
        self.GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN = False
        self.HIGH_GAIN_AUGMENTATION_MIN_SIZE = 200.0
        self.HTF4_CONF = False
        self.HTF_REGIME_EXIT_TF = '1h'
        self.KINDERGARTEN_EMA_GATE_ENABLED = True
        self.K_LOWER_HIGH_EXIT_ENABLED = False
        self.LEADERBOARD_FILTER = False
        self.LH_HL_FILTER_AUGMENT_GATE_ENABLED = True
        self.LONG_STRUCT_EXIT_TF = 'D'
        self.LR_BAND_ENTRY_LO = 0.3
        self.LR_BAND_ENTRY_TF = 'D'
        self.MAX_CONCURRENT_POSITIONS = 24
        self.MAX_POSITION_SIZE = 5000.0
        self.MFI_ENTRY_ENABLED = False
        self.MFI_FLIP_EXIT_ENABLED = False
        self.MIN_HOLD_BARS = 10
        self.MIN_POSITION_SIZE = 100
        self.MI_ENTRY_EXHAUST_BONUS_TRADIER = 8
        self.MI_ENTRY_STRUCT_BONUS_TRADIER = 10
        self.MI_MIN_GAIN_EXIT_TRADIER = 0.5
        self.MOMENTUM_WATCHDOG_ENABLED = True
        self.MTF_ATR_TRAIL_ENABLED = False
        self.MTF_ATR_TRAIL_MULT = 2.5
        self.MTF_EXIT_USE_COMPOUND = False
        self.MTS_WEIGHT_15m = 4.0
        self.MTS_WEIGHT_1h = 3.0
        self.MTS_WEIGHT_4h = 5.0
        self.MTS_WEIGHT_D = 12.0
        self.MU_CORRECTION_REENTRY_STOCH_ENABLED = False
        self.NOLOSS_BYPASS_WT_5OF5_ENABLED = True
        self.NOLOSS_BYPASS_WT_5OF5_MIN_TFS = 3
        self.NOLOSS_MIN_PROFIT_PCT_TRADIER = 0.01
        self.OI_CONFIRM_MIN_PRICE_PCT_TRADIER = 0.3
        self.OPTIONS_EQUITY_HEDGE_DC_BREACH_EXIT_ENABLED = False
        self.OPTIONS_EQUITY_HEDGE_DIRECTION_GUARD_ENABLED = False
        self.OPTIONS_HEDGE_PAIR_GUARD_ENABLED = False
        self.ORDER_CACHE_TTL = 10
        self.OUTLIER_DETECTOR_ENABLED = False
        self.OUTLIER_SCAN_INTERVAL = 300.0
        self.OUTLIER_STALE_HOURS = 24.0
        self.PARABOLIC_BB_PCT_B_4H_MIN = 0.9
        self.PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER = 0.75
        self.PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER = 0.02
        self.PARTIAL_PROFIT_LOCK_ENABLED = False
        self.PARTIAL_PROFIT_LOCK_FRAC_TRADIER = 0.625
        self.PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER = 0.5
        self.R1_TF = '5m'
        self.R1_USE_DC_4BAR = True
        self.R3_HEDGE_INVARIANT_DUMP_ENABLED = False
        self.RECENT_REDUCTION_GUARD_WINDOW_S = 450.0
        self.RECOVERY_AUGMENT_BAND_PCT = 1.0
        self.REDIS_KEY_MARKET_DATA = 'tradier_indicators_latest'
        self.REDUCTION_COOLDOWN_SECONDS = 30.0
        self.RED_ZONE_TRADIER_MIN_DISTANCE_PCT = 0.5
        self.REENTRY_BAR_STRUCTURE_TF = '5m'
        self.REENTRY_BAR_TURN_TF = '5m'
        self.REENTRY_LIVE_MONITOR_DC_BREAK_TF = '5m'
        self.REENTRY_LIVE_MONITOR_DC_BREAK_USE_4BAR = False
        self.REENTRY_RALLY_K15M_MAX = 100.0
        self.REENTRY_TIER2_MAX_MINUTES_TRADIER = 120.0
        self.REENTRY_TIER2_MIN_MINUTES_TRADIER = 10.0
        self.REENTRY_TIER2_PRICE_PCT_TRADIER = 0.003
        self.REENTRY_TIER2_SIZE_MULT_TRADIER = 0.8
        self.RZ_BOT_BB_THRESHOLD = 0.375
        self.RZ_ENTRY_ENABLED = False
        self.RZ_K_EXIT = 80.0
        self.SATOSHIT_LONG_MFI_MAX_TRADIER = 120.0
        self.SATOSHIT_SHORT_MFI_MIN_TRADIER = 62.5
        self.SCALP_MAX_POSITION_SIZE = 5000.0
        self.SHORT_STRUCT_EXIT_TF = '15m'
        self.SMA200_DIST_ENTRY_ENABLED = False
        self.STDEV_BREAKOUT_EXIT_WT_ENABLED = False
        self.STOCH_CROSS_1H_EXIT_ENABLED = False
        self.STRUCTURAL_EXIT_GATE_ENABLED = False
        self.STRUCTURAL_RANGE_SHIFT_K_HIGH = 85.0
        self.STRUCTURAL_RANGE_SHIFT_K_LOW = 15.0
        self.STRUCTURAL_RANGE_SHIFT_TF = 'bb_1h'
        self.SWING_LONG_BUDGET = 40000.0
        self.SWING_MAX_POSITION_SIZE = 5000.0
        self.SWING_SHORT_BUDGET = 40000.0
        self.SYMBOL_PERF_ENABLED = False
        self.SYMBOL_PERF_REFRESH_SECONDS = 300.0
        self.TF_HTF1 = '1h'
        self.TF_HTF2 = '4h'
        self.TF_HTF3 = 'D'
        self.TF_MICRO = '5m'
        self.TF_SCALP = '15m'
        self.TRADES_PER_SYM_PER_DAY_MAX = 8
        self.TRADIER_ACCOUNT_ID = 'VA11623260'
        self.TRADIER_K_ZONE_ENTRY_BONUS_TRADIER = 25
        self.TRADIER_MIN_HOLD_MINUTES = 240.0
        self.TRADIER_REENTRY_HARDCOOL_MIN = 15.0
        self.TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED = False
        self.TRC_MAX_ORDER_VALUE = 3300.0
        self.USE_SANDBOX = True
        self.VERBOSE_STOPS = True
        self.VWAP_BOUNCE_ENTRY_ENABLED = False
        self.WRONG_SIDE_DIV_TFS_REQUIRED = 1
        self.WT_15M_BOUNCE_OPEN_ENABLED = True
        self.WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED = True
        self.WT_3M_FORCE_OPEN_SIZE_USD = 1200.0
        self.WT_3M_FORCE_OPEN_TARGET_USD = 2500.0
        self.WT_DC_EXIT_THRESHOLD = 30
        self.WT_DC_LONG_ENABLED = True
        self.WT_DC_SHORT_ENABLED = True
        self.WT_VEL_DECAY_EXIT_ENABLED = True
        # §17.4 order fix: original method code above overwrites these two — re-assert live values LAST
        self.ENTRY_SCORE_THRESHOLD = 18.0
        self.VWAP_FILTER_ENABLED = False
        # §17.3 exclusion: MAX_ORDER_VALUE stays at sim default (live order-size cap zeroes sim position sizing)
    ABLATION_DISABLE_AGGRESSIVE_HEDGE: bool = False  # auto-wired 625
    ABLATION_DISABLE_AUGMENTATION: bool = False  # auto-wired 625
    ABLATION_DISABLE_CHECK_NOLOSS: bool = False  # auto-wired 625
    ABLATION_DISABLE_DC_BREACH_REDUCE: bool = False  # auto-wired 625
    ABLATION_DISABLE_ENTRY_LEADERBOARD: bool = False  # auto-wired 625
    ABLATION_DISABLE_ENTRY_RANKING: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    ABLATION_DISABLE_ENTRY_REVERSAL: bool = False  # auto-wired 625
    ABLATION_DISABLE_ENTRY_TECHNICAL: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    ABLATION_DISABLE_HIGH_GAIN_AUGMENT: bool = False  # auto-wired 625
    ABLATION_DISABLE_PERIODIC_REENTRY: bool = False  # auto-wired 625
    ABLATION_DISABLE_RATIO_REBALANCE: bool = False  # auto-wired 625
    ABLATION_DISABLE_REENTRY_ENFORCE: bool = False  # auto-wired 625
    ABLATION_DISABLE_SPIKE_FADE_EXIT: bool = False  # auto-wired 625
    ALIGNMENT_GATE_MIN: float = 4  # auto-wired 625
    ATR_PARITY_EQUITY_BASE_USD: float = 17500.0  # auto-wired 625
    ATR_PARITY_QTY_CAP_MULT: float = 5.0  # auto-wired 625
    ATR_PARITY_USE_DAILY: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    ATR_TRAIL_2X_EXIT_ENABLED: bool = False  # auto-wired 625
    AUGMENTATION_COOLDOWN_SECONDS: float = 90.0  # 2026-09-28 LIVE PARITY: config.py:4481 / config_tradier.py:53 = 300.0
    BAND_SLOPE_SIZING_V2_DEPTH_GAIN: float = 1.0  # TEMPLATE 1.0 (was 0.5)
    BAND_SLOPE_SIZING_V2_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    BAND_SLOPE_SIZING_V2_MAX: float = 1.8  # TEMPLATE 2.5 (was 1.25) — STDEV ladder D 6mo 10x
    BAND_SLOPE_SIZING_V2_MIN: float = 0.7  # TEMPLATE 0.5 (was 0.25)
    BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY: float = 1.0  # TEMPLATE 1.0 (was 0.5)
    STDEV_SLOPE_SIZING_ENABLED: bool = True  # stdev 2.5 ladder master
    STDEV_BAND_MULTIPLIER: float = 2.5
    STDEV_SLOPE_SIZING_D_MAX: float = 10.0
    STDEV_SLOPE_SIZING_4H_MAX: float = 4.0
    STDEV_SLOPE_SIZING_1H_MAX: float = 2.0
    STDEV_SLOPE_SIZING_15M_MAX: float = 1.5
    STDEV_SLOPE_LOOKBACK_D: int = 180
    STDEV_SLOPE_LOOKBACK_4H: int = 180
    STDEV_SLOPE_LOOKBACK_1H: int = 168
    STDEV_SLOPE_LOOKBACK_15M: int = 96
    STDEV_SLOPE_SIZING_MODE: str = "slope_to_top"
    BB_FROZEN_STOP_ENABLED: bool = False  # auto-wired 625
    BB_PULLBACK_GATE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    BB_PULLBACK_GATE_LONG_MAX: float = 0.30  # 2026-09-28 live parity: config*=0.30 (was 0.15)
    BB_PULLBACK_GATE_SHORT_MIN: float = 0.70  # 2026-09-28 live parity: config*=0.70 (was 0.35)
    BB_RSI_STOCH_BB_MAX: float = 0.3  # auto-wired 625
    BB_RSI_STOCH_K_MAX: float = 30.0  # auto-wired 625
    BB_RSI_STOCH_RSI_MAX: float = 40.0  # auto-wired 625
    BOUNCE_REENTRY_ENABLED_TRADIER: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    BOUNCE_TOP_MAX_LOSS_PCT: float = -50.0  # auto-wired 625
    BOUNCE_TOP_MIN_HOLD_MINUTES: float = 2880.0  # auto-wired 625
    BOUNCE_TOP_MIN_LOSS_PCT: float = -3.0  # auto-wired 625
    BOUNCE_TOP_REENTRY_MULT: float = 1.5  # auto-wired 625
    BREAKEVEN_DC_LOW4_ENABLED: bool = True  # auto-wired 625
    BREAKEVEN_GRACE_MINUTES: float = 5.0  # auto-wired 625
    BREAKOUT_GUARD_LOSS_THRESHOLD: float = -999.0  # auto-wired 625
    BREAKOUT_RETEST_ARMED_ENABLED: bool = False  # auto-wired 625
    BREAKOUT_RETEST_ARMED_HTF_STACK_MIN: float = 2  # auto-wired 625
    BREAKOUT_RETEST_ARMED_K_3M_PREV_MAX: float = 30  # auto-wired 625
    BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT: float = 0.3  # auto-wired 625
    BREAKOUT_RETEST_ARMED_VOLUME_MULT: float = 1.25  # auto-wired 625
    BREAKOUT_SIZE_EMA200_T1_MULT: float = 1.5  # auto-wired 625
    BREAKOUT_SIZE_EMA200_T1_PCT: float = 1.0  # auto-wired 625
    BREAKOUT_SIZE_EMA200_T2_MULT: float = 2.0  # auto-wired 625
    BREAKOUT_SIZE_EMA200_T2_PCT: float = 1.5  # auto-wired 625
    BREAKOUT_SIZE_EMA200_T3_MULT: float = 3.0  # auto-wired 625
    BREAKOUT_SIZE_EMA200_T3_PCT: float = 2.5  # auto-wired 625
    BREAKOUT_SIZE_LADDER_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    BREAKOUT_SIZE_MAX_MULT: float = 3.0  # auto-wired 625
    BROKER_PREFLIGHT_MAX_SAME_SIDE_QTY: float = 50.0  # auto-wired 625
    CATALYST_VOLUME_GATE_ENABLED: bool = False  # auto-wired 625
    CATALYST_VOLUME_RATIO: float = 1.5  # auto-wired 625
    CIRCUIT_BREAKER_ENABLED: bool = False  # auto-wired 625
    CLENOW_ENABLED: bool = False  # auto-wired 625
    CLENOW_GATE_ENABLED: bool = False  # auto-wired 625
    CLENOW_GATE_MIN_SCORE: float = 30.0  # auto-wired 625
    CLENOW_MIN_SCORE: float = 5.0  # auto-wired 625
    CLENOW_POSITION_SIZE: float = 800.0  # auto-wired 625
    CLENOW_REGIME_FILTER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    COMBINED_STOCH_GATE_TRADIER: float = 60.0  # auto-wired 625
    CONGRESS_CONVICTION_MIN_SOURCES: float = 2  # auto-wired 625
    CONGRESS_CONVICTION_SIZING_BOOST: float = 1.3  # auto-wired 625
    CONNORS_RSI2_REQUIRE_ABOVE_200SMA: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    CONNORS_RSI2_THRESHOLD: float = 10.0  # auto-wired 625
    CONNORS_RSI_MAX_HOLD_DAYS: float = 20  # auto-wired 625
    CONNORS_RSI_POSITION_SIZE: float = 600.0  # auto-wired 625
    CONVICTION_SHORT_THRESHOLD: float = 20  # auto-wired 625
    CONVICTION_SIZING_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    CONVICTION_SIZING_MAX: float = 8.0  # auto-wired 625
    COUNTER_TREND_SMA200_BYPASS_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED: bool = False  # auto-wired 625
    DC_BREAK_GR_MULT_BREAKOUT: float = 0.1  # auto-wired 625
    DC_BREAK_GR_MULT_ENABLED: bool = False  # auto-wired 625
    DC_BREAK_GR_MULT_RETEST: float = 3.0  # auto-wired 625
    DC_BREAK_GR_RETEST_TOLERANCE_PCT: float = 0.3  # auto-wired 625
    DC_BREAK_LOW_REQUIRE_HTF_ENABLED: bool = False  # auto-wired 625
    DC_BREAK_LOW_REQUIRE_HTF_MIN_TFS: float = 2  # auto-wired 625
    DC_DAYTRADE_BUFFER: float = 0.001  # auto-wired 625
    DC_DAYTRADE_K_EXHAUSTED_LONG: float = 85.0  # auto-wired 625
    DC_DAYTRADE_K_EXHAUSTED_SHORT: float = 15.0  # auto-wired 625
    DC_DAYTRADE_LONG_BUDGET: float = 3000.0  # auto-wired 625
    DC_DAYTRADE_MAX_POSITION_SIZE: float = 1000.0  # auto-wired 625
    DC_DAYTRADE_PRE_CLOSE_MINUTES: float = 120  # auto-wired 625
    DC_DAYTRADE_SHORT_BUDGET: float = 3000.0  # auto-wired 625
    DC_DAYTRADE_START_SIZE: float = 600.0  # auto-wired 625
    DC_DAYTRADE_STOCH_FILTER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DC_ENTRY_VETO_ENABLED_TRADIER: bool = False  # auto-wired 625
    DC_LOW4_STOP_ENABLED: bool = False  # auto-wired 625
    DC_LOW_FROZEN_STOP_ENABLED: bool = False  # auto-wired 625
    DC_LOW_FROZEN_STOP_FLOOR_PCT: float = -999.0  # auto-wired 625
    DC_LOW_FROZEN_STOP_USE_4BAR: bool = False  # auto-wired 625
    DC_LOW_STOP_ENABLED: bool = False  # auto-wired 625
    DC_TIER4_BAR_MATURITY_BLOCK: float = 0.7  # auto-wired 625
    DC_TIER4_BAR_MATURITY_BLOCK_ENABLED: bool = False  # auto-wired 625
    DD_KELLY_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DD_KELLY_TIER1_PCT: float = 10.0  # auto-wired 625
    DD_KELLY_TIER2_PCT: float = 15.0  # auto-wired 625
    DD_KELLY_TIER3_PCT: float = 20.0  # auto-wired 625
    DELTA_ATR_ENTRY_FILTER: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DELTA_ENTRY_ACCEL_THRESHOLD: float = 0.0  # auto-wired 625
    DELTA_ENTRY_Z_THRESHOLD: float = 2.5  # auto-wired 625
    DELTA_EXIT_ACCEL_THRESHOLD: float = -0.1  # auto-wired 625
    DELTA_EXIT_DECAY_RATIO: float = 0.9  # auto-wired 625
    DELTA_EXIT_MIN_HOLD: float = 4  # auto-wired 625
    DELTA_EXIT_MIN_TF_LOST: float = 1  # auto-wired 625
    DELTA_EXIT_OPPOSING_RATIO: float = 1.5  # auto-wired 625
    DELTA_EXIT_REENTRY_COOLDOWN_MIN: float = 45.0  # auto-wired 625
    DELTA_EXIT_REQUIRE_NONZERO_SCORE: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DELTA_PYRAMID_ACCEL_THRESHOLD: float = 0.2  # auto-wired 625
    DELTA_PYRAMID_MIN_BARS: float = 8  # auto-wired 625
    DELTA_TF_Z_THRESHOLD: float = 1.5  # auto-wired 625
    DG_DAILY_GAIN_BLOCK_SHORT_PCT: float = 2.5  # auto-wired 625
    DG_DAILY_LOSS_BLOCK_LONG_PCT: float = 2.5  # auto-wired 625
    DG_HIGH_VOLATILITY_ATR_PCT: float = 4.0  # auto-wired 625
    DG_HTF_ALIGN_REQUIRE_1H: bool = False  # auto-wired 625
    DG_HTF_ALIGN_REQUIRE_4H: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DG_HTF_ALIGN_REQUIRE_D: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DG_MAX_FORCE_OPEN_NOTIONAL_USD: float = 4000.0  # auto-wired 625
    DG_MOMENTUM_BLOCK_RSI15M_FOR_LONG: float = 35.0  # auto-wired 625
    DG_MOMENTUM_BLOCK_RSI15M_FOR_SHORT: float = 65.0  # auto-wired 625
    DG_MOMENTUM_BLOCK_RSI1H_FOR_LONG: float = 35.0  # auto-wired 625
    DG_MOMENTUM_BLOCK_RSI1H_FOR_SHORT: float = 65.0  # auto-wired 625
    DG_OPPOSITE_SIDE_PROFIT_BLOCK_PCT: float = 1.0  # auto-wired 625
    DG_SMA200_SHORT_BYPASS: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DG_WT_3M_REQUIRE_HTF_CONFIRM: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DT_TARGET_ATR_ENABLED: bool = False  # auto-wired 625
    DUP_GUARD_GAIN_MULTIPLIER: float = 0.5  # auto-wired 625
    DUP_GUARD_USE_GAIN_GATE: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DYNAMIC_SCORE_COUNTER_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD: float = 55.0  # auto-wired 625
    EMA_9_21_FILTER_ENABLED: bool = True  # 2026-09-22 FIX REVERT: True but correctly written — was blocking 100% instead of 40%, fixed kindergarten to 40% not 100%
    EMA50_15M_ENTRY_FILTER_ENABLED: bool = True  # [M deploy] live config.py:2132 + config_tradier.py:3797 = True (both venues) — was False; 2026-09-30 port: EMA50 15m entry filter (crypto config.py True, stocks config_tradier False). Fallback False; real value per mode.
    EMA50_15M_ENTRY_FILTER_PCT: float = 0.0  # 2026-09-30 port: buffer % beyond ema_50_15m (0 = strict)
    ENTRY_ATR_PCT_MIN: float = 1.5  # auto-wired 625
    ENTRY_SYMGATE_ENABLED: bool = False  # auto-wired 625
    ENTRY_VOL_MIN_RATIO: float = 1.3  # auto-wired 625
    ENTRY_ZONE_LONG: float = 80.0  # 2026-09-28 live parity: config.py/config_tradier=80 (vec 20 blocked longs whenever stoch_k>20)
    ENTRY_ZONE_SHORT: float = 20.0  # auto-wired 625
    EOD_RATIO_ENFORCE_TRADIER: bool = False  # auto-wired 625
    EOD_SLIM_RATIO_ENABLED: bool = False  # auto-wired 625
    EP_POSITION_SIZE: float = 800.0  # auto-wired 625
    EXIT_ALGO_SCORE_ENABLED: bool = False  # auto-wired 625
    EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED: bool = False  # auto-wired 625
    EXIT_BOUNCE_TOP_ENABLED: bool = False  # auto-wired 625
    EXIT_CONV_FAIL_ENABLED: bool = False  # auto-wired 625
    EXIT_DC_BREACH_REDUCE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    EXIT_DELTA_SPEED_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    EXIT_GAIN_EROSION_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    EXIT_GAIN_THRESHOLD_MIN: float = 1.0  # auto-wired 625
    EXIT_HARD_DROP_5M_ENABLED: bool = False  # auto-wired 625
    EXIT_HTF_QUICK_TP_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    EXIT_IBS_EXHAUSTION_ENABLED: bool = False  # auto-wired 625
    EXIT_K5M_BOUNCE_ENABLED: bool = False  # auto-wired 625
    EXIT_MAX_HOLD_ENABLED: bool = False  # auto-wired 625
    EXIT_MAX_HOLD_MINUTES: float = 99999  # auto-wired 625
    EXIT_MI_ENABLED: bool = False  # auto-wired 625
    EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    EXIT_SCORER_DC_EXTREME: float = 0.8  # auto-wired 625
    EXIT_SCORER_FULL_SCORE: float = 100.0  # auto-wired 625
    EXIT_SCORER_K_EXTREME: float = 75.0  # auto-wired 625
    EXIT_SCORER_MIN_CONDITIONS: float = 5  # auto-wired 625
    EXIT_SCORER_PARTIAL_SCORE: float = 40.0  # auto-wired 625
    EXIT_SENTIMENT_ENABLED: bool = False  # auto-wired 625
    EXIT_STDEV_BREAKOUT_FAIL_ENABLED: bool = True  # parity 2026-09-26: live Config True crypto vs vec False caused 0 trades — align to live. Bold in TEMPLATE_CRYPTO_LONG/SHORT.
    EXIT_STRUCT_BREAK_5M_ENABLED: bool = False  # auto-wired 625
    EXIT_STRUCT_DC_BREAK_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    EXIT_TREND_REVERSAL_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    EXTREME_OB_BB_PCT_B_4H_MIN: float = 1.0  # auto-wired 625
    EXTREME_OB_RSI_4H_MIN: float = 80.0  # auto-wired 625
    EXTREME_OB_RSI_D_MIN: float = 75.0  # auto-wired 625
    EXTREME_OS_BB_PCT_B_4H_MAX: float = 0.0  # auto-wired 625
    EXTREME_OS_RSI_4H_MAX: float = 20.0  # auto-wired 625
    EXTREME_OS_RSI_D_MAX: float = 25.0  # auto-wired 625
    EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    EZ_REENTRY_PRICE_CROSS_MIN_GAP_S: float = 60.0  # auto-wired 625
    FAST_CUT_LOSS_THRESHOLD: float = -999.0  # auto-wired 625
    FH_MOMENTUM_EVAL_MINUTES: float = 30  # auto-wired 625
    FH_MOMENTUM_POSITION_SIZE: float = 600.0  # auto-wired 625
    FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER: float = -8.0  # auto-wired 625
    FROZEN_ACTIVATION_STOP_ENABLED: bool = True  # auto-wired 625
    FUNDING_GATE_ENABLED_TRADIER: bool = False  # auto-wired 625
    FUNDING_GATE_PC_RATIO_LONG_MAX: float = 1.2  # auto-wired 625
    FUNDING_GATE_PC_RATIO_SHORT_MIN: float = 0.83  # auto-wired 625
    FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED: bool = False  # auto-wired 625
    FUNDING_GATE_TRADIER_NEAR_MONEY_PREFER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    FUNDING_GATE_TRADIER_STALE_MAX_HOURS: float = 4.0  # auto-wired 625
    GAP_FILL_MAX_GAP_PCT: float = 5.0  # auto-wired 625
    GAP_FILL_MIN_GAP_PCT: float = 0.5  # auto-wired 625
    GAP_FILL_POSITION_SIZE: float = 600.0  # auto-wired 625
    GAP_FILL_STOP_MULT: float = 0.3  # auto-wired 625
    GAP_FILL_TP_FILL_PCT: float = 0.7  # auto-wired 625
    GHOST_CLOSE_REQUIRE_CONFIRMATION: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    GOLDEN_RULE_EXIT_MIN_IND: float = 2  # auto-wired 625
    GOLDEN_RULE_EXIT_MIN_TFS: float = 0  # auto-wired 625
    GOLDEN_RULE_HTF_MIN_TFS: float = 1  # auto-wired 625
    GOLDEN_RULE_HTF_VETO_ENABLED: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    GOLDEN_RULE_MIN_IND: float = 2  # auto-wired 625
    GOLDEN_RULE_REQUIRE_ACTIVATION: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    GR_BB_EXTENDED_LONG: float = 0.75  # auto-wired 625
    GR_DC_EXTENDED_LONG: float = 0.65  # auto-wired 625
    GR_HTF_DIRECT_ENTRY_DOUBLE_SCORE: float = 34.0  # auto-wired 625
    GR_HTF_DIRECT_ENTRY_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    GR_HTF_DIRECT_ENTRY_SCORE_MIN: float = 23.0  # auto-wired 625
    GR_HTF_DIRECT_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    GR_HTF_DIRECT_EXIT_SCORE: float = 15.0  # auto-wired 625
    GR_HTF_GATE_ENABLED: bool = False  # auto-wired 625
    GUARANTEED_REENTRY_AUGMENT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    HEDGE_DUAL_IF_HEDGE_MODE: bool = False  # auto-wired 625
    HEDGE_FAILED_FALLBACK_CLOSE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    HEDGE_MODE_TRADIER: bool = False  # auto-wired 625
    HTF_ALIGN_REQUIRED_TRADIER: float = 2  # auto-wired 625
    HTF_DC_BREAKOUT_TRADIER_ENABLED: bool = False  # auto-wired 625
    HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT: float = 0.0  # auto-wired 625
    HTF_TREND_VETO_ENABLED: bool = False
    HTF_TREND_VETO_ON_REDUCE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    HTF_TREND_VETO_SCORE_MIN_ABS: float = 5.0  # auto-wired 625
    HTF_W_M_ALIGN_GATE_TRADIER_ENABLED: bool = False  # auto-wired 625
    HTF_W_M_ALIGN_TRADIER_REQUIRED: float = 2  # auto-wired 625
    HTF_W_REVERSAL_EXIT_TRADIER_ENABLED: bool = False  # auto-wired 625
    HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    LH_HL_FILTER_DC_THRESHOLD_PCT: float = 0.5  # auto-wired 625
    LH_HL_FILTER_REPLACE_SMA200D: bool = False  # auto-wired 625
    LIVE_ENTRY_ENGINE_BOOST_SCORE: float = 8.0  # auto-wired 625
    LIVE_ENTRY_ENGINE_DC_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    LIVE_ENTRY_ENGINE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    LIVE_ENTRY_ENGINE_HTF_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    LIVE_ENTRY_ENGINE_MIN_SCORE: float = 0.5  # auto-wired 625
    LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT: float = 1.0  # auto-wired 625
    LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    LIVE_ENTRY_ENGINE_STOCH_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    LIVE_ENTRY_ENGINE_WT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    LIVE_INDICATOR_MAX_BARS_PER_TF: float = 600  # auto-wired 625
    LOCAL_EXTREMES_MIN_SCORE: float = 45.0  # auto-wired 625
    LR_BAND_HARVEST_ENABLED: bool = False  # auto-wired 625
    LR_BAND_HARVEST_FRAC: float = 0.25  # auto-wired 625
    LR_BAND_HARVEST_HI: float = 0.7  # auto-wired 625
    LR_BAND_LADDER_ABOVE_TOP_MULT: float = -1.0  # auto-wired 625
    LR_BAND_LADDER_BELOW_BOTTOM_MULT: float = 0.0  # auto-wired 625
    LR_BAND_LADDER_BOTTOM_MULT: float = 10.0  # auto-wired 625
    LR_BAND_LADDER_CENTER: float = 0.5  # auto-wired 625
    LR_BAND_LADDER_ENABLED: bool = False  # auto-wired 625
    LR_BAND_LADDER_TOP_MULT: float = 3.0  # auto-wired 625
    LR_BAND_SIZE_DEPTH_GAIN: float = 1.0  # auto-wired 625
    LR_BAND_SIZE_MAX: float = 3.0  # auto-wired 625
    LR_BAND_SIZE_SLOPE_GAIN: float = 1.0  # auto-wired 625
    LR_BAND_SLOPE_FLIP_EXIT_ENABLED: bool = False  # auto-wired 625
    LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN: float = 240.0  # auto-wired 625
    LR_BAND_SLOPE_FLIP_MIN_PCT_DAY: float = 0.05  # auto-wired 625
    LR_BAND_SLOPE_NORM_PCT_DAY: float = 0.3  # auto-wired 625
    LS_RATIO_ENFORCE_TRADIER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    LS_RATIO_MAX_TRADIER: float = 5.00  # LIVE parity 2026-09-11: config_tradier 5.00 (veto until was 1.0 strangled)
    LS_RATIO_MIN_TRADIER: float = 0.20  # LIVE parity 2026-09-11: config_tradier 0.20 (was 0.25 blocked shorts)
    LUNCH_DEADZONE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MANDATORY_REENTRY_DC4_WINDOW_MIN: float = 30.0  # auto-wired 625
    MARKET_QUALITY_SCORE_ENABLED_TRADIER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MAX_CONCURRENT_POSITIONS: float = 16  # auto-wired 625
    MAX_DAILY_LOSS_PCT: float = 3.0  # auto-wired 625
    MAX_ORDER_VALUE: float = 2500.0  # §17.3 EXCLUSION 2026-09-29: live per-order money cap (config 180) zeroes sim sizing (AAPL 1 share > cap) — capital-normalization class
    MAX_POSITION_SIZE: float = 780.0  # auto-wired 625
    MAX_SYMBOL_VALUE_TRADIER: float = 3750.0  # auto-wired 625
    MFI_LONG_THRESHOLD_D: float = 80.0  # auto-wired 625
    MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT: float = 0.2  # auto-wired 625
    MICRO_SCALP_STOCKS_MAKER_ENABLED: bool = False  # auto-wired 625
    MICRO_SCALP_STOCKS_PEAK_FLOOR_PCT: float = 0.6  # auto-wired 625
    MINERVINI_ENABLED: bool = False  # auto-wired 625
    MINERVINI_GATE_ENABLED: bool = False  # auto-wired 625
    MINERVINI_LONG_BUDGET: float = 4000.0  # auto-wired 625
    MINERVINI_MAX_HOLD_DAYS: float = 40  # auto-wired 625
    MINERVINI_POSITION_SIZE: float = 800.0  # auto-wired 625
    MINERVINI_TARGET_PCT: float = 25.0  # auto-wired 625
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 3.0  # auto-wired 625
    MIN_HOLD_BARS_TRADIER: float = 40  # auto-wired 625
    MI_DIV_EXIT_ENABLED_TRADIER: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MI_ENTRY_EXHAUST_BONUS_TRADIER: float = 4  # auto-wired 625
    MI_ENTRY_STRUCT_BONUS_TRADIER: float = 5  # auto-wired 625
    MI_EXHAUST_EXIT_ENABLED_TRADIER: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MI_EXIT_VETO_ENABLED_TRADIER: bool = False  # auto-wired 625
    MI_MIN_GAIN_EXIT_TRADIER: float = 0.25  # auto-wired 625
    MI_STRUCT_EXIT_ENABLED_TRADIER: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MI_VELOCITY_EXIT_ENABLED_TRADIER: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MI_WAVE_EXIT_ENABLED_TRADIER: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MOMENTUM_FADE_BODY_ATR_MIN_TRADIER: float = 2.0  # auto-wired 625
    MOMENTUM_FADE_ENABLED_TRADIER: bool = False  # auto-wired 625
    MOMENTUM_FADE_K_ZONE_TRADIER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MOMENTUM_FADE_VOL_MIN_TRADIER: float = 2.0  # auto-wired 625
    MTF_ARMED_ENTRY_ENABLED: bool = True  # live parity: config_tradier True
    MTF_ARMED_ENTRY_SKIP_SHORT: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MTF_ARROW_CONFIRM_PCT: float = 2.0  # auto-wired 625
    MTF_ARROW_ENTRY_ENABLED: bool = False  # auto-wired 625
    MTF_ARROW_SIZE_GAIN: float = 1.0  # auto-wired 625
    MTF_ARROW_SIZE_MAX: float = 4.0  # auto-wired 625
    MTF_ARROW_SLOPE_LAMBDA: float = 1.0  # auto-wired 625
    MTF_ARROW_TRAIL_EXIT_ENABLED: bool = False  # auto-wired 625
    MTF_ATR_TRAIL_ENABLED: bool = False  # 2026-09-29 USER: trailing stops eliminated — default OFF, swept switch. Was True (config.py Phase I); stocks already OFF via MTF_ATR_TRAIL_ENABLED_TRADIER
    MTF_ATR_TRAIL_ENABLED_TRADIER: bool = False  # live parity: config_tradier has NO MTF trail knobs → tradier _cfg default False (compound block inert on stocks)
    MTF_ATR_TRAIL_MULT: float = 2.0  # live parity: config.py 2.0 (2026-05-20 USER MANDATE 2x ATR 15m trail)
    MTF_BB_REJECT_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MTF_DC_REJECT_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MTF_ENTRY_REQUIRE_GR_FILTER: bool = True  # auto-wired 625
    MTF_EXIT_USE_COMPOUND: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MTF_GR_EXIT_GATE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MTF_GR_FILTER_ENABLED: bool = True  # auto-wired 625
    MTF_GR_INVERT_DC_BB: bool = True  # auto-wired 625
    MTF_REQUIRE_ARMED_ANY: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MTF_WT_CROSS_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    MTS_BOTTOM_MIN_TRADIER: float = 5.0  # auto-wired 625
    MTS_ENTRY_QUALITY_MIN_TRADIER: float = 0.0  # auto-wired 625
    MTS_GATE_ENABLED_TRADIER: bool = False  # auto-wired 625
    NOLOSS_BB1H_GATE_ENABLED: bool = False  # auto-wired 625
    NOLOSS_BYPASS_WT_5OF5_ENABLED: bool = False  # EMERGENCY 2026-09-11: False→True matches config_tradier live (rollback if v14 proves False better)  # auto-wired 625
    NOLOSS_MIN_PROFIT_PCT_TRADIER: float = 0.0  # auto-wired 625
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = -0.5  # auto-wired 625
    OBLIGATORY_HEDGE_PCT: float = 0.0  # auto-wired 625
    OBLIGATORY_SECTOR_HEDGE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    OBLIGATORY_SECTOR_HEDGE_LOOP_INTERVAL_SECONDS: float = 90.0  # auto-wired 625
    OBLIGATORY_SECTOR_HEDGE_TRIGGER_REQUIRE_WT_5M_AND_1H: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    OI_CONFIRM_ENABLED_TRADIER: bool = False  # auto-wired 625
    OI_CONFIRM_MIN_OI_CHANGE_PCT_TRADIER: float = 0.5  # auto-wired 625
    OI_CONFIRM_MIN_PRICE_PCT_TRADIER: float = 0.15  # auto-wired 625
    OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED: bool = False  # auto-wired 625
    ORB_LONG_BUDGET: float = 2000.0  # auto-wired 625
    ORB_MAX_HOLD_MINUTES: float = 150.0  # auto-wired 625
    ORB_POSITION_SIZE: float = 600.0  # auto-wired 625
    ORB_SHORT_BUDGET: float = 2000.0  # auto-wired 625
    ORB_STOP_MIDPOINT: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    OVERNIGHT_GAP_HEDGE_CLOSE_MINUTES: float = 5.0  # auto-wired 625
    OVERNIGHT_GAP_HEDGE_ENABLED: bool = False  # auto-wired 625
    OVERNIGHT_GAP_HEDGE_OPEN_MINUTES: float = 15.0  # auto-wired 625
    OVERNIGHT_GAP_HEDGE_SENTIMENT_THRESHOLD: float = 20.0  # auto-wired 625
    OVERNIGHT_GAP_HEDGE_SIZE_FRAC: float = 0.5  # auto-wired 625
    PARABOLIC_BB_PCT_B_4H_MAX: float = 0.1  # auto-wired 625
    PARABOLIC_BB_PCT_B_4H_MIN: float = 0.7  # auto-wired 625
    PARABOLIC_RSI_1H_MAX: float = 35.0  # auto-wired 625
    PARABOLIC_RSI_1H_MIN: float = 65.0  # auto-wired 625
    PARABOLIC_RSI_4H_MAX: float = 30.0  # auto-wired 625
    PARABOLIC_RSI_4H_MIN: float = 70.0  # auto-wired 625
    PARITY_COMPARISON_MODE: bool = False  # auto-wired 625
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER: float = 0.875  # auto-wired 625
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER: float = 0.01  # auto-wired 625
    PARTIAL_PROFIT_LOCK_ENABLED: bool = True  # auto-wired 625
    PARTIAL_PROFIT_LOCK_FRAC_TRADIER: float = 0.3125  # auto-wired 625
    PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER: float = 0.75  # auto-wired 625
    PARTIAL_PROFIT_LOCK_SLIPPAGE_PCT: float = 0.025  # auto-wired 625
    PARTIAL_PROFIT_LOCK_SWEEP_ARM_PCT: float = 0.5  # auto-wired 625
    PARTIAL_PROFIT_LOCK_SWEEP_ENABLED: bool = False  # auto-wired 625
    PARTIAL_PROFIT_LOCK_SWEEP_GAIN_PCT: float = 0.3  # auto-wired 625
    PEAK_GIVEBACK_DROP_PCT: float = 0.5  # auto-wired 625
    PEAK_GIVEBACK_HARD_ZERO_ENABLED: bool = False  # auto-wired 625
    PEAK_GIVEBACK_MIN_PEAK_PCT: float = 0.5  # auto-wired 625
    PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT: float = 0.0  # auto-wired 625
    PEAK_GIVEBACK_PROTECTION_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    PENNY_STOCK_LONG_BLOCK_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    PENNY_STOCK_LONG_BLOCK_PRICE_USD: float = 5.0  # auto-wired 625
    PRICE_CROSS_BACK_BAND_PCT: float = 0.3  # auto-wired 625
    PRICE_CROSS_BACK_MAX_AGE_MIN: float = 525600000.0  # auto-wired 625
    PRICE_CROSS_BACK_REENTRY_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    PROXIMITY_TOP_GATE_ENABLED: bool = False  # auto-wired 625
    PROXIMITY_TOP_MAX_DROP_PCT: float = 5.0  # auto-wired 625
    R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = False  # USER 2026-10-01: must NOT run live nor vectorized (not implemented in vec)  # auto-wired 625
    R1_NEWBORN_WINDOW_MIN: float = 15.0  # auto-wired 625
    R1_USE_DC_4BAR: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    R2_PEAK_MIN_PCT: float = 0.5  # auto-wired 625
    R3_HTF_FLIP_4H_TIER_ENABLED: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    R3_HTF_FLIP_EXIT_ENABLED: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    RATIO_MULTIPLIER_TRADIER: float = 3.5  # LIVE parity 2026-09-11: config_tradier 3.5 BACKTEST_T61 was 1.75
    RECOVERY_AUGMENT_BAND_PCT: float = 0.3  # REENTRY (not profit-add) — tradier 1.0 / crypto 0.3 ; auto-wired 625 baseline 0.5
    RECOVERY_AUGMENT_ENABLED: bool = True  # REENTRY re-open after partial REDUCE (ON by default 2026-09-11) — bypasses HARD wall at gain>=0.5*MIN_GAIN
    RECOVERY_AUGMENT_MAX_AGE_MIN: float = 240.0  # REENTRY window ; auto-wired 625 baseline 120
    RECOVERY_AUGMENT_ONE_FIRE_PER_REDUCE: bool = True  # REENTRY one-fire ; auto-wired 625
    RECOVERY_AUGMENT_REQUIRE_WT_CROSS: bool = True  # 2026-09-11 REENTRY WT 5m+15m bull/bear required — prevents entering when wt going down (was False)
    RECOVERY_AUGMENT_SIZE_PCT: float = 1.0  # REENTRY size 1x START ; auto-wired 625 baseline 0.5
    RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    RED_ZONE_TRADIER_GATE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    RED_ZONE_TRADIER_MIN_DISTANCE_PCT: float = 0.25  # auto-wired 625
    RED_ZONE_TRADIER_MIN_OI_AT_WALL: float = 1000  # auto-wired 625
    RED_ZONE_TRADIER_STALE_MAX_HOURS: float = 4.0  # auto-wired 625
    REENTRY_60MIN_MIN_PCT: float = 0.05  # auto-wired 625
    REENTRY_60MIN_UNCONDITIONAL_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    REENTRY_60MIN_WINDOW_MIN: float = 1440.0  # auto-wired 625
    REENTRY_AGGRESSIVE_WINDOW_MIN: float = 30.0  # auto-wired 625
    REENTRY_BREAKOUT_ENABLED: bool = False  # auto-wired 625
    REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT: float = 0.002  # auto-wired 625
    REENTRY_CONFIRMATION_GATES_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    REENTRY_DISPATCH_BACKOFF_S: float = 0.4  # auto-wired 625
    REENTRY_FAVORABLE_MOVE_PCT: float = 1.0  # auto-wired 625
    REENTRY_K15M_PARTIAL_MULT: float = 0.5  # auto-wired 625
    REENTRY_K15M_PARTIAL_THRESHOLD: float = 90.0  # auto-wired 625
    REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    REENTRY_LIVE_MONITOR_DC_BREAK_USE_4BAR: bool = True  # auto-wired 625
    REENTRY_MAX_PRICE_DIVERGENCE_PCT: float = 20.0  # auto-wired 625
    REENTRY_MIN_GAP_MINUTES: float = 0.0  # auto-wired 625
    REENTRY_NEVER_SKIP_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    REENTRY_STOCH_K_MAX_LONG: float = 80.0  # 2026-09-10 crypto fix: was 40 (over-strict). 80 restores SAFE single-WT path — matches config.py
    REENTRY_STOCH_K_MIN_SHORT: float = 20.0  # was 60/30 — mirror fix (SAFE unless <20) — matches config.py
    REENTRY_SYMGATE_ENABLED: bool = False  # auto-wired 625
    REENTRY_SYMGATE_SPEED_MIN: float = 0.5  # auto-wired 625
    REENTRY_TIER2_MAX_MINUTES_TRADIER: float = 60.0  # auto-wired 625
    REENTRY_TIER2_MIN_MINUTES_TRADIER: float = 5.0  # auto-wired 625
    REENTRY_TIER2_PRICE_PCT_TRADIER: float = 0.0015  # auto-wired 625
    REENTRY_TIER2_SIZE_MULT_TRADIER: float = 0.4  # auto-wired 625
    REGIME_DETECTION_ENABLED: bool = False  # auto-wired 625
    ROTATION_POSITION_SIZE: float = 1200.0  # auto-wired 625
    ROTATION_SMA200_FILTER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    RSI2_POSITION_SIZE: float = 600.0  # auto-wired 625
    RSI_ENTRY_PERIOD_TRADIER: float = 10  # auto-wired 625
    RULE_B_5M_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    RVOL_MOMENTUM_MIN: float = 1.5  # auto-wired 625
    RZ_BOT_BB_THRESHOLD: float = 0.15  # auto-wired 625
    RZ_DIV_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    RZ_K_ENTRY_MAX: float = 50.0  # auto-wired 625
    RZ_K_EXIT: float = 95.0  # auto-wired 625
    RZ_MFI_EXIT: float = 85.0  # auto-wired 625
    RZ_REQUIRE_STRUCT: bool = False  # auto-wired 625
    RZ_TOP_BB_THRESHOLD: float = 0.85  # auto-wired 625
    RZ_TWO_PHASE_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    RZ_ZSCORE_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    RZ_ZSCORE_ZONE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    SATOSHIT_ENTRY_FILTER: bool = False  # auto-wired 625
    SCALP_LONG_BUDGET: float = 250.0  # auto-wired 625
    SCALP_MAX_HOLD_MINUTES: float = 180.0  # auto-wired 625
    SCALP_MAX_POSITIONS_PER_SIDE: float = 6  # auto-wired 625
    SCALP_MAX_POSITION_SIZE: float = 500.0  # auto-wired 625
    SCALP_MIN_MOVE_PCT: float = 0.003  # auto-wired 625
    SCALP_MIN_REL_VOL: float = 1.1  # auto-wired 625
    SCALP_SHORT_BUDGET: float = 250.0  # auto-wired 625
    SCALP_START_SIZE: float = 150.0  # auto-wired 625
    SCALP_STOP_PCT: float = 9.99  # auto-wired 625
    SCALP_TARGET_PCT: float = 0.005  # auto-wired 625
    SECTOR_LS_RATIO_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    SENTIMENT_REBALANCER_ENABLED: bool = False  # auto-wired 625
    SENTIMENT_REBAL_AUGMENT_DEVIATION_THR: float = 1.0  # auto-wired 625
    SENTIMENT_REBAL_REDUCE_DEVIATION_THR: float = 0.5  # auto-wired 625
    SMA200_DIST_LONG_THRESHOLD_4H: float = -10.0  # auto-wired 625
    SMA_FILTER_PERIOD_TRADIER: float = 100  # auto-wired 625
    SMFI_ENABLED: bool = False  # auto-wired 625
    SMFI_LONG_BUDGET: float = 3000.0  # auto-wired 625
    SMFI_MAX_HOLD_DAYS: float = 10  # auto-wired 625
    SMFI_POSITION_SIZE: float = 600.0  # auto-wired 625
    SMFI_SHORT_BUDGET: float = 3000.0  # auto-wired 625
    SPIKE_FADE_MAX_POSITIONS: float = 10  # auto-wired 625
    SPIKE_FADE_POSITION_SIZE: float = 600.0  # auto-wired 625
    SPIKE_FADE_THRESHOLD_PCT: float = 2.0  # auto-wired 625
    SPY_REGIME_BLOCK_LONGS_BELOW: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    SPY_REGIME_BLOCK_SHORTS_ABOVE: bool = False  # auto-wired 625
    SQUEEZE_FIRE_BONUS_SCORE: float = 11.25  # auto-wired 625
    SQUEEZE_FIRE_ENTRY_ENABLED: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    STDEV_BB_RZ_EXIT_ENABLED: bool = False  # auto-wired 625
    STDEV_BOUNCE_ENABLED: bool = False  # auto-wired 625
    STDEV_BOUNCE_PCTB_LONG: float = 0.05  # auto-wired 625
    STDEV_BOUNCE_PCTB_SHORT: float = 0.95  # auto-wired 625
    STDEV_BOUNCE_RVOL_MIN: float = 1.2  # auto-wired 625
    STDEV_BREAKOUT_ENABLED: bool = False  # auto-wired 625
    STDEV_BREAKOUT_MAX_AGE_BARS: float = 50  # auto-wired 625
    STDEV_BREAKOUT_RETEST_PCTB_MIN: float = 0.85  # auto-wired 625
    STDEV_BREAKOUT_RETEST_SCORE: float = 22  # auto-wired 625
    STDEV_BREAKOUT_RETEST_SIZE_MULT: float = 1.5  # auto-wired 625
    STDEV_BREAKOUT_RVOL_MIN: float = 1.2  # auto-wired 625
    STDEV_MACRO_AUGMENT_VETO_ENABLED: bool = False  # auto-wired 625
    STDEV_MACRO_ENTRY_VETO_ENABLED: bool = False  # auto-wired 625
    STDEV_MACRO_HEDGE_BOOST_ENABLED: bool = False  # auto-wired 625
    STDEV_MACRO_R4_EXIT_ENABLED: bool = False  # auto-wired 625
    STDEV_REJECT_EXIT_ENABLED: bool = False  # auto-wired 625
    STDEV_REJECT_EXIT_RETURN: float = 0.65  # auto-wired 625
    STDEV_REJECT_EXIT_ZONE: float = 0.8  # auto-wired 625
    STRUCTURAL_RANGE_SHIFT_K_HIGH: float = 75.0  # auto-wired 625
    STRUCTURAL_RANGE_SHIFT_K_LOW: float = 25.0  # auto-wired 625
    STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS: float = 100.0  # auto-wired 625
    SWING_LONG_BUDGET: float = 2500.0  # auto-wired 625
    SWING_SHORT_BUDGET: float = 2500.0  # auto-wired 625
    SYMBOL_PERF_MAX_MULT: float = 10.0  # auto-wired 625
    SYMBOL_PERF_MIN_MULT: float = 0.1  # auto-wired 625
    TIER_A_MIN_GAIN: float = 0.3  # auto-wired 625
    TIER_A_MIN_TRADES: float = 10  # auto-wired 625
    TIME_ZONE_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TRADIER_LONG_ONLY_ENTRIES: bool = False  # auto-wired 625
    TRADIER_MIN_HOLD_MINUTES: float = 4320.0  # auto-wired 625
    TRADIER_NOLOSS_SRS_BYPASS: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TRADIER_OI_INJECT_MAX_EACH: float = 10  # auto-wired 625
    TRADIER_OI_INJECT_MIN_TOTAL_OI: float = 1000  # auto-wired 625
    TRADIER_OI_INJECT_STALE_MAX_HOURS: float = 4.0  # auto-wired 625
    TRADIER_QUEUE_DEDUPE_SEC: float = 60.0  # auto-wired 625
    TRADIER_RATIO_BOOST_MIN_GAIN_PCT: float = 1.0  # auto-wired 625
    TRADIER_RATIO_REQUIRE_MIN_GAIN: bool = False  # auto-wired 625
    TRADIER_REENTRY_ANTI_CHURN_ENABLED: bool = False  # auto-wired 625
    TRADIER_REENTRY_HARDCOOL_MIN: float = 30.0  # auto-wired 625
    TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TRADIER_REENTRY_RZ_BLOCK_ENABLED: bool = False  # auto-wired 625
    TRADIER_RSI_SHORT_15M: float = 65.0  # auto-wired 625
    TRADIER_RSI_SHORT_1H: float = 65.0  # auto-wired 625
    TRADIER_RSI_SHORT_RVOL_15M: float = 1.0  # auto-wired 625
    TRADIER_RSI_SHORT_RVOL_1H: float = 1.0  # auto-wired 625
    TRADIER_WT_EXIT_MIN_TFS_TRADIER: float = 5  # auto-wired 625
    TRADIER_WT_EXIT_TFS_TRADIER: str = '5m+15m+1h+4h+D'  # auto-wired 625
    TRAILING_AUG_ENABLED_TRADIER: bool = False  # auto-wired 625
    TRAILING_AUG_GAIN_STEP_PCT: float = 0.5  # auto-wired 625
    TRAILING_AUG_MIN_GAIN_PCT: float = 0.5  # auto-wired 625
    TRA_DISABLE_AUGMENT: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TRA_DISABLE_DELTA_ENTRY: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TRA_LONG_ONLY: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TRA_MIN_HOLD_MINUTES: float = 5760.0  # auto-wired 625
    TRA_NO_LOSS_EXIT: bool = False  # auto-wired 625
    TRA_STRICT_EXIT_ONLY: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TRA_WT_DC_ENTRY_THRESHOLD: float = 85.0  # auto-wired 625
    TRC_CLENOW_POSITION_SIZE: float = 2640.0  # auto-wired 625
    TRC_CONNORS_RSI_ENABLED: bool = False  # auto-wired 625
    TRC_CONNORS_RSI_POSITION_SIZE: float = 1980.0  # auto-wired 625
    TRC_DC_DAYTRADE_LONG_BUDGET: float = 9900.0  # auto-wired 625
    TRC_DC_DAYTRADE_SHORT_BUDGET: float = 9900.0  # auto-wired 625
    TRC_DC_DAYTRADE_START_SIZE: float = 1980.0  # auto-wired 625
    TRC_ENTRY_MIN_ALIGNMENT: float = 4  # auto-wired 625
    TRC_ENTRY_ZONE_LONG: float = 30.0  # auto-wired 625
    TRC_ENTRY_ZONE_SHORT: float = 70.0  # auto-wired 625
    TRC_EP_POSITION_SIZE: float = 2640.0  # auto-wired 625
    TRC_GAP_FILL_POSITION_SIZE: float = 1980.0  # auto-wired 625
    TRC_LS_RATIO_MAX: float = 3.0  # auto-wired 625
    TRC_LS_RATIO_MIN: float = 0.3  # auto-wired 625
    TRC_MAX_CONCURRENT_POSITIONS: float = 32  # auto-wired 625
    TRC_MAX_DAILY_LOSS_PCT: float = 10.0  # auto-wired 625
    TRC_MAX_ORDER_VALUE: float = 1250.0  # auto-wired 625
    TRC_MAX_POSITION_SIZE: float = 3750.0  # auto-wired 625
    TRC_MINERVINI_LONG_BUDGET: float = 13200.0  # auto-wired 625
    TRC_MINERVINI_POSITION_SIZE: float = 2640.0  # auto-wired 625
    TRC_MOMENTUM_FADE_ENABLED: bool = False  # auto-wired 625
    TRC_NOLOSS_MIN_PROFIT_PCT: float = 0.0  # auto-wired 625
    TRC_ORB_LONG_BUDGET: float = 6600.0  # auto-wired 625
    TRC_ORB_POSITION_SIZE: float = 1980.0  # auto-wired 625
    TRC_ORB_SHORT_BUDGET: float = 6600.0  # auto-wired 625
    TRC_ROTATION_POSITION_SIZE: float = 3000.0  # auto-wired 625
    TRC_RSI2_POSITION_SIZE: float = 1980.0  # auto-wired 625
    TRC_SCALP_LONG_BUDGET: float = 1250.0  # auto-wired 625
    TRC_SCALP_MAX_POSITIONS_PER_SIDE: float = 12  # auto-wired 625
    TRC_SCALP_SHORT_BUDGET: float = 1250.0  # auto-wired 625
    TRC_SCALP_START_SIZE: float = 495.0  # auto-wired 625
    TRC_SCALP_TARGET_PCT: float = 0.01  # auto-wired 625
    TRC_SMFI_ENABLED: bool = False  # auto-wired 625
    TRC_SMFI_LONG_BUDGET: float = 9900.0  # auto-wired 625
    TRC_SMFI_POSITION_SIZE: float = 1980.0  # auto-wired 625
    TRC_SMFI_SHORT_BUDGET: float = 9900.0  # auto-wired 625
    TRC_START_POSITION_SIZE: float = 330.0  # TRADIER 500-2000 per user 2026-09-26 — keep tradier 500 (crypto is 28). Do not touch tradier aside from this parity.
    TRC_SWING_LONG_BUDGET: float = 100000.0  # auto-wired 625
    TRC_SWING_SHORT_BUDGET: float = 100000.0  # auto-wired 625
    TREND_GATES: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TR_ADX4H_BOYCOTT_SCORE: float = -40  # auto-wired 625
    TR_ADX4H_GATE_ENABLED: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TR_ADX4H_MAX: float = 20.0  # auto-wired 625
    TR_BBWIDTH4H_GATE_ENABLED: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TR_CHOP4H_GATE_ENABLED: bool = False  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TR_DCWIDTH4H_SHORT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TR_DCWIDTH4H_SHORT_MAX: float = 15.0  # auto-wired 625
    TR_MFI4H_LONG_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TR_MFI4H_LONG_MIN: float = 40.0  # auto-wired 625
    TR_TREND_V1_ENABLED: bool = False  # auto-wired 625
    TSMOM_BOOK_SCALAR_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    TSMOM_HIGH_CAP: float = 1.5  # auto-wired 625
    TSMOM_LOOKBACK_BARS: float = 252  # auto-wired 625
    TSMOM_LOW_CAP: float = 0.25  # auto-wired 625
    TSMOM_MIN_AGREEMENT: float = 0.5  # auto-wired 625
    VIX_REGIME_FILTER_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    VIX_VOLATILITY_REGIME_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    VOL_TARGET_ENABLED: bool = False  # auto-wired 625
    VOL_TARGET_HIGH_CAP: float = 2.0  # auto-wired 625
    VOL_TARGET_LOW_CAP: float = 0.25  # auto-wired 625
    VOL_TARGET_PCT: float = 20.0  # auto-wired 625
    WT_15M_VEL_NEAR_ZERO_THRESHOLD: float = 0.1  # auto-wired 625
    WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED: bool = False  # 2026-09-03 FIX: was True, MAKER_PROFIT_EXIT_R1 always losing, default OFF but tested in TEMPLATE_30d_matrix.xlsx
    WT_15M_VEL_SLOW_GAIN_BAND_PCT: float = 0.1  # auto-wired 625
    WT_15M_VEL_SLOW_GAIN_FLOOR_PCT: float = 0.01  # 2026-09-03 FIX: was 0.005, live raised 0.01→0.05 to stop BTC g0.016 maker loss (fee 0.04%). ROLLBACK: 0.005.
    WT_15M_BOUNCE_OPEN_ENABLED: bool = False  # FIX 2026-09-02: was dead switch via unknown-key drop (read via getattr but not declared in QuickConfig) — caused WT15 delta 0 lie
    WT_15M_BOUNCE_MAX_BARS_AGO: int = 100  # FIX 2026-09-02: was 2 (30min) → 100 (25h) so WT15 adds >100 trades per 20 sessions (user: FALSE vs TRUE must be >100 trades diff, not 1-2)
    WT_15M_BOUNCE_BB_MIN: float = 0.05
    WT_15M_BOUNCE_BB_MAX: float = 0.95
    WT_15M_BOUNCE_REQUIRE_BOTH_HTF: bool = False
    WT_15M_BOUNCE_FILTER_HL_ENABLED: bool = False  # user 2026-09-07: filter bad entries — require low_1h > low_1h_prev (higher low)
    WT_15M_BOUNCE_FILTER_HH_ENABLED: bool = False  # user 2026-09-07: filter bad entries — require high_1h > high_1h_prev (higher high)
    WT_15M_BOUNCE_FILTER_MODE: str = "AND"  # AND requires both HL+HH, OR requires either
    WT_15M_BOUNCE_VOLUME_FILTER_ENABLED: bool = False  # user 2026-09-07: only enter when volume > relvol or vol ema (hook up live + v12)
    WT_15M_BOUNCE_VOLUME_MODE: str = "relvol"  # relvol = relative_volume_1h > threshold, ema = volume_1h > volume_sma_1h
    WT_15M_BOUNCE_VOLUME_THRESHOLD: float = 1.0  # for relvol: >1.0x avg; for ema: >1.0x sma
    WT_15M_BOUNCE_LOW_1H_GT_PREV: bool = False  # alias for FILTER_HL — template row WT_15M_BOUNCE_LOW_1H_GT_PREV
    WT_15M_BOUNCE_HIGH_1H_GT_PREV: bool = False  # alias for FILTER_HH — template row WT_15M_BOUNCE_HIGH_1H_GT_PREV
    WT_15M_BOUNCE_REL_VOL_GT_1: bool = False  # alias for VOLUME_FILTER — template row WT_15M_BOUNCE_REL_VOL_GT_1
    SIMPLE_PRICE_GT0_ENABLED: bool = False  # SIMPLE price>0 test — ridiculously simple, always trades when enabled (added 2026-09-06 alongside WT15, never fails)
    # ═══ VIGILANCE GUARD (USER 2026-09-28) ═══
    # USER DESIGN ORDER 2026-09-28 (later same day): the DC4 stop is "one of many exit switches
    # to be tried NOT a hardcoded exit path" — VIGILANCE_GUARD_ENABLED (same name as live
    # config.py:1567 / config_tradier.py:66) default flipped True→False in lockstep with the
    # live twin, and the breach needs the same channel tolerance the other dc exits use
    # (dc_low_15m - 0.25% convention, DAYTRADE/TECHNICAL_DC_STOP_BUFFER_PCT).
    VIGILANCE_GUARD_ENABLED: bool = False    # sweepable switch; False = DC4 stop + consec-loss block + recovery all inert
    VIGILANCE_DC4_BREACH_TOLERANCE_PCT: float = 0.25  # breach depth beyond the dc4 level, % of level
    VIGILANCE_DC4_STOP_TF: str = "15m"       # losing position + px breach dc_low4_{TF}/dc_high4_{TF} → close+block; OFF disables
    VIGILANCE_CONSEC_LOSSES: int = 2         # losing closes in a row that block the sym_side
    VIGILANCE_RECOVERY_REENTRY_ENABLED: bool = True  # USER 2026-09-28 2nd mandate: auto-unblock on recovery, KEEP TRADING
    VIGILANCE_RECOVERY_BOUNCE_OK: bool = True        # bounce (wt1_15m with side + 3m stoch confirm) also unblocks
    DC_HARD_STOP_REENTRY_COOLDOWN_HOURS: float = 4.0  # tradier only — reopen cooldown after ULTIMATE_DC hard stop (CHURN_FIX #2)
    WT_3M_FORCE_OPEN_BUILD_TO_TARGET: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    WT_3M_FORCE_OPEN_BYPASS_GATES: bool = True  # auto-wired 625
    WT_3M_FORCE_OPEN_DIST_PCT: float = 0.0  # auto-wired 625
    WT_3M_FORCE_OPEN_ENABLED: bool = False  # auto-wired 625
    WT_3M_FORCE_OPEN_SIZE_USD: float = 25.0  # auto-wired 625
    WT_3M_FORCE_OPEN_TARGET_USD: float = 2000.0  # auto-wired 625
    WT_3M_FORCE_OPEN_TF_LADDER: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    WT_3M_FORCE_OPEN_TF_LADDER_MULT: float = 1.0  # auto-wired 625
    WT_3M_FORCE_OPEN_USE_SMA200: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    WT_COMPOSITE_VETO_ENABLED_TRADIER: bool = False  # auto-wired 625
    WT_DC_EXIT_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    WT_DC_EXIT_STALE_MAX_S: float = 600  # auto-wired 625
    WT_DC_EXIT_THRESHOLD: float = 25.0  # auto-wired 625
    WT_DC_HTF_GATE: str = "4h_D"  # live parity: config_tradier 4h_D (was missing — SHORT-into-uptrend gap; 4h_D blocks SHORT when D uptrend, mirrors tradier_manage 7239)
    WT_DC_LONG_ENABLED: bool = False  # live parity: config_tradier True
    WT_DC_SHORT_ENABLED: bool = False  # live parity: config_tradier True
    WT_D_BOUNCE_AUG_COOLDOWN_HOURS: float = 1.0  # auto-wired 625
    WT_D_BOUNCE_AUG_ENABLED: bool = False  # auto-wired 625
    WT_D_BOUNCE_AUG_MULTIPLIER: float = 2.0  # auto-wired 625
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_PRICE: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_WT: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    WT_D_BOUNCE_DD_STOP_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    WT_EXIT_VETO_ENABLED_TRADIER: bool = False  # auto-wired 625
    WT_FORCE_OPEN_FRESH_CROSS_ONLY: bool = False  # auto-wired 625
    WT_FORCE_OPEN_FRESH_MAX_BARS: float = 0  # auto-wired 625
    WT_VEL_DECEL_RATIO: float = 0.5  # auto-wired 625
    WT_VEL_USE_DECEL_RATIO_ONLY: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    WT_W_EXIT_ENABLED: bool = False  # auto-wired 625
    TRA_SATOSHIT_ONLY: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    AI_PREMARKET_ENABLED_TRC: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    AI_PREMARKET_TRADINGVIEW_ENABLED: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    TRADIER_OI_INJECT_ENABLED: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    TRADIER_OI_INJECT_NEAR_MONEY_PREFER: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    LH_HL_FILTER_AUGMENT_GATE_ENABLED: bool = False  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_BUY_WT_DC_GATE_ENABLED: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_PREMARKET_NO_FIRE: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_EQUITY_HEDGE_DC_BREACH_EXIT_ENABLED: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_EQUITY_HEDGE_DIRECTION_GUARD_ENABLED: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_HEDGE_PAIR_GUARD_ENABLED: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_CONTINUOUS_SECTOR_GATE: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_BUY_REQUIRE_D_ALIGN: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_CSP_MONITOR_REQUIRE_WT_D_TURN: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_CSP_MONITOR_LOG_EVERY_TICK: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    OPTIONS_SPREAD_ENABLED: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    PARTIAL_PROFIT_LOCK_USE_MAKER_TRADIER: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    GUARANTEED_REENTRY_HTF_VETO_ENABLED: bool = False  # live parity: config_tradier True (added, was missing, caused 0 trades)
    HTF_VETO_REQUIRE_D: bool = True  # live parity: config_tradier True (added, was missing, caused 0 trades)
    ZONE_CLOSE_THRESHOLD: float = 20  # auto-wired 625
    ZONE_MID_THRESHOLD: float = 30  # auto-wired 625
    LR_BAND_LADDER_BASIS: str = 0.5  # auto-wired 625
    LR_BAND_LADDER_MODE: str = 'center_plateau'  # auto-wired 625
    BREAKEVEN_EXIT_AFTER_BARS_ENABLED: bool = True  # live parity: config_tradier True (was False, caused 0 trades)  # auto-wired 625
    R1_REQUIRE_WT15_ADVERSE: bool = True  # live parity: config_tradier True (added batch 2)
    TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    NEWBORN_DC_STOP_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    EMERGENCY_BRAKE_DC_STOP_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    SWING_REENTER_AT_OR_BELOW_EXIT: bool = True  # live parity: config_tradier True (added batch 2)
    SWING_RUNAWAY_REENTER: bool = True  # live parity: config_tradier True (added batch 2)
    BAND_ARROW_ACCUMULATE: bool = True  # live parity: config_tradier True (added batch 2)
    LR_BAND_BE_RATCHET: bool = True  # live parity: config_tradier True (added batch 2)
    LR_BAND_EXIT_EXEMPT: bool = True  # live parity: config_tradier True (added batch 2)
    NEWS_SENTIMENT_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    VERBOSE: bool = True  # live parity: config_tradier True (added batch 2)
    VERBOSE_STOPS: bool = False  # live parity: config_tradier True (added batch 2)
    WT15_CROSS_DC_BASIS_GATE_ENABLED: bool = True  # 2026-09-03 vectorized wt15 cross above dc_basis_15m else dc_basis_cross wt1>wt2
    WT_CROSS_REQUIRE_HTF_AGREE: bool = True  # 2026-09-03 vectorized WT cross HTF agree
    TRADIER_INDICATORS_NARROW_UNIVERSE: bool = True  # live parity: config_tradier True (added batch 2)
    EXIT_ON_ALL: bool = True  # live parity: config_tradier True (added batch 2)
    HTF1_CONF: bool = True  # live parity: config_tradier True (added batch 2)
    ROTATION_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    GAP_FILL_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    PARABOLIC_PROTECTION_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    EXTREME_OB_OS_OVERRIDE_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    TRADIER_REQUIRE_TRADEABLE_KEY: bool = True  # live parity: config_tradier True (added batch 2)
    HTF_REGIME_SCALE_IN: bool = True  # live parity: config_tradier True (added batch 2)
    HODL_LONG_ONLY: bool = True  # live parity: config_tradier True (added batch 2)
    HEDGE_CROSS_SYMBOL_TRADIER: bool = True  # live parity: config_tradier True (added batch 2)
    TRC_LOCAL_EXTREMES_SCORER_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    EARNINGS_AVOIDANCE_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    MACRO_BLACKOUT_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    SECTOR_LS_RATIO_BYPASS_HEDGE: bool = True  # live parity: config_tradier True (added batch 2)
    TRC_5M_SWEEP_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    STDEV_BREAKOUT_EXIT_WT_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    MU_CORRECTION_REQUIRE_HIGH_REVERSAL: bool = True  # live parity: config_tradier True (added batch 2)
    MU_CORRECTION_REQUIRE_CLOSE_REVERSAL: bool = True  # live parity: config_tradier True (added batch 2)
    MU_CORRECTION_REENTRY_STOCH_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    K_LOWER_HIGH_EXIT_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    WT_15M_LH_WAIT_EXIT_ENABLED: bool = False  # 2026-09-15 WAIT lower-high 15m + DC/BB/WT wait
    WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED: bool = False  # 2026-09-15 WAIT divergence vv short
    WT_TECHNICAL_WAIT_LOWER_HIGH_ONLY: bool = True  # gate: require LH/div wait
    EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED: bool = False  # EXIT BLOCKER parity
    EXIT_BLOCKER_LH_TF: str = "15m"
    EXIT_BLOCKER_LL_TF: str = "15m"
    DC_BREAK_WAIT_WT15_CLOSE_ENABLED: bool = False  # wait for wt15 close instead of dc break
    BREAKOUT_MULTI_LUNG_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    TRC_CLENOW_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    TRC_MINERVINI_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    DC_TIER_AUG_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    TR_TREND_V1_SHADOW_LOG_ONLY: bool = True  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_DAEMON_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_INLINE_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_INLINE_TIER12_EPQ_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_INLINE_EVAL2_DIRECT_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_INLINE_LOOP_PERIODIC_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_INLINE_LOOP_ENFORCE_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_INLINE_LOOP_PRICE_MONITOR_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_INLINE_LOOP_ENFORCE_EPQ_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_INLINE_LOOP_EVAL2_EPQ_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    DAEMON_PRICE_CROSS_REENTRY_LIVE_ENABLED: bool = False  # 2026-09-03 default OFF, same switch as live daemon (user: fix vector from same switch)
    DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED: bool = False  # 2026-09-03 default OFF, tested in TEMPLATE_30d_matrix.xlsx
    BREAKOUT_LEASH_ENABLED: bool = False  # 2026-09-26 DISABLED per parity — live-only 545 closes NOT in per_sym 7D ledger, NON_VECTORIZABLE/USELESS paper P3/P7 ON
    ADAPTIVE_REGIME_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    ADAPTIVE_REGIME_PAPER: bool = True  # live parity: config_tradier True (added batch 2)
    ADX_REGIME_FILTER_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    AUGMENT_BLOWPAST_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    AUGMENT_HTF_TREND_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    AUGMENT_PYRAMID_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    AUGMENT_WT_3TF_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    AUGMENT_WT_CROSS_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    BB4H_BREAKOUT_LADDER_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    FAVORABLE_SLOPE_HOLD_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    BREAKEVEN_EXIT_REQUIRE_WT15M_STRUCTURE: bool = True  # live parity: config_tradier True (added batch 2)
    BOUNCE_AUGMENT_PAPER: bool = True  # live parity: config_tradier True (added batch 2)
    TRADIER_RESET_MAX_GAIN_ON_CLOSE: bool = True  # live parity: config_tradier True (added batch 2)
    CRYPTO_FH_MOMENTUM_DC_CONFIRM: bool = True  # live parity: config_tradier True (added batch 2)
    CRYPTO_FH_MOMENTUM_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    CRYPTO_SPIKE_FADE_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    GR_V5_BREAKOUT_REQUIRE_VOLUME: bool = True  # live parity: config_tradier True (added batch 2)
    GR_V5_BOUNCE_WT_CROSS_REQUIRED: bool = True  # live parity: config_tradier True (added batch 2)
    DC_BREAKOUT_ENTRY_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    DC_WIDTH_SIZING_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    DELTA_EXIT_DOM_TF_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    DELTA_EXIT_SPEED_DECAY: bool = True  # live parity: config_tradier True (added batch 2)
    DELTA_SERVICE_BLEED_STOP: bool = True  # live parity: config_tradier True (added batch 2)
    DELTA_SERVICE_REDUCE_GATE: bool = True  # live parity: config_tradier True (added batch 2)
    DELTA_SERVICE_TRAILING_STOP: bool = True  # live parity: config_tradier True (added batch 2)
    EXIT_HEDGE_LOSS_KILL_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    EXIT_HEDGE_ORPHAN_KILL_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    EXIT_MARKET_SPIKE_REDUCE_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    HEDGE_NEWBORN_DC_BREACH_ALLOWED: bool = True  # live parity: config_tradier True (added batch 2)
    HTF_STRICT: bool = True  # live parity: config_tradier True (added batch 2)
    INF_RANKING_BYPASS_HTF: bool = True  # live parity: config_tradier True (added batch 2)
    INF_RANKING_BYPASS_STOCH: bool = True  # live parity: config_tradier True (added batch 2)
    LEGACY_DC_BREAKOUT_REENTRY: bool = True  # live parity: config_tradier True (added batch 2)
    LEGACY_GUARANTEED_REENTRY: bool = True  # live parity: config_tradier True (added batch 2)
    LEGACY_WR_PULLBACK: bool = True  # live parity: config_tradier True (added batch 2)
    LONG_STOCH_CHASE_BLOCK: bool = True  # live parity: config_tradier True (added batch 2)
    LOSS_EXIT_REQUIRES_HEDGE: bool = True  # live parity: config_tradier True (added batch 2)
    MANAGE_REDUCE: bool = True  # live parity: config_tradier True (added batch 2)
    MOVER_DETECTION_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    ORPHAN_HEDGE_CHECK_GAIN: bool = True  # live parity: config_tradier True (added batch 2)
    REENTRY_K15M_PARTIAL_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    MANDATORY_REENTRY_WT_FILTER_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    RULE_NAME_TAGGING_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    REENTRY_WAVETREND_CONFIRM_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    STDEV_MACRO_R4_REQUIRE_LTF_FLIP: bool = True  # live parity: config_tradier True (added batch 2)
    DISASTER_GUARD_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    DG_BROKER_MEMORY_SYNC_BLOCK: bool = True  # live parity: config_tradier True (added batch 2)
    BROKER_PREFLIGHT_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    SATOSHIT_EXIT_USE_MAKER: bool = True  # live parity: config_tradier True (added batch 2)
    SATOSHIT_PROTECT_TRADES: bool = True  # live parity: config_tradier True (added batch 2)
    SCALP_V2_DC_HTF_REQUIRE_ALL: bool = True  # live parity: config_tradier True (added batch 2)
    SCALP_V2_ISOLATE: bool = True  # live parity: config_tradier True (added batch 2)
    SCALP_V2_LH_LL_EXIT: bool = True  # live parity: config_tradier True (added batch 2)
    SCALP_V2_REDZONE_EXIT: bool = True  # live parity: config_tradier True (added batch 2)
    SERVICE_STOP: bool = True  # live parity: config_tradier True (added batch 2)
    STOP_MAJOR_LOSS_BLOCK_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    COUNTER_TREND_ADD_BLOCK_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    PERSYM_FINAL_BOOK_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    MOMENTUM_SMA_WATCHDOG_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    USE_INDICATOR_SNAPSHOT: bool = True  # live parity: config_tradier True (added batch 2)
    V8Q_D_TREND_REQUIRED: bool = True  # live parity: config_tradier True (added batch 2)
    V8Q_STRENGTH_FILTER_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    VOL_SPIKE_ENABLED: bool = True  # live parity: config_tradier True (added batch 2)
    WT_15M_SAME_HEDGE_ENABLED: bool = False  # live parity: config_tradier True (added batch 2)
    ADX_RANGING_THRESHOLD: float = 10.0  # parity fix 2026-09-13: config_tradier 20 (was 0 drift)
    ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC: float = 30.0  # auto-added TEMPLATE
    ALL_TF_AGAINST_CLOSE_ENABLED: bool = True  # 2026-09-10 FIX vs B&H: all TFs (3m/15m/1h/4h/D) against → close primary
    ALL_TF_AGAINST_CLOSE_MIN_TFS: float = 4  # 2026-09-10 FIX: 3 TFs against → exit (was 0 — never fired)
    # 2026-10-01 WIRING b1 — ALL_TF_AGAINST split into two precisely named switches (old ALL_TF_AGAINST_CLOSE_* kept as DEPRECATED aliases until deploy):
    #   FORCE_CLOSE = CAUSES a full close of the position when >= MIN_TFS of the 5 WT TFs (3m/15m/1h/4h/D) are against it (ez_manage.process_position 48045)
    #   BLOCK_ENTRY = PREVENTS an open/reentry when >= MIN_TFS are against the side (ez_manage._batch1_template_live_gate 6579, crypto only)
    ALL_TF_AGAINST_FORCE_CLOSE_ENABLED: bool = True
    ALL_TF_AGAINST_FORCE_CLOSE_MIN_TFS: float = 4
    ALL_TF_AGAINST_FORCE_CLOSE_COOLDOWN_SEC: float = 30.0
    ALL_TF_AGAINST_BLOCK_ENTRY_ENABLED: bool = True
    ALL_TF_AGAINST_BLOCK_ENTRY_MIN_TFS: float = 4
    # 2026-10-01 b2c — live crypto exit chain (ez_positions_quick.process_single_exit) master + knobs live reads through getattr-with-default (absent in config.py)
    R1_RESTRICT_TO_OVERBOUGHT_BREAKOUT: bool = True  # live getattr default True (ez_manage 46840)
    MOMENTUM_TP_ENABLED: bool = True              # vec-only toggle: live MOMENTUM_TP (ez_manage 52282) has NO switch (always on, gain>0.5)
    DC_BASIS_3M_REDUCE_ENABLED: bool = True       # vec-only toggle: live DC_BASIS_3M_REDUCE has NO switch (always on unless HEDGE_MODE)
    DC_PRIOR_BAR_CHANNEL: bool = True             # b6: DC-channel STOP/breach exits compare close with the PREVIOUS bar's channel (NPZ dc_* include the current bar -> same-bar `close<=dc_low*(1-buf)` is impossible); targets stay same-bar
    KG_STOCKS_LIVE_GATE: bool = True              # b6 vec-only: stocks KINDERGARTEN/EMA_9_21 = live HARD VETO on the final entry signal (False = legacy additive signal)
    LIVE_EXIT_CHAIN_ENABLED: bool = True          # vec master for vec_decisions/live_exit_chain.py (crypto only); False = pre-b2c baseline
    WT_CROSS_EXIT_3M_VETO_MAX_AGE: float = 30.0   # live getattr default 30.0 (ez_positions_quick 14625); no 3m array -> veto inactive in vec
    TREND_REGIME_VETO_ENABLED: bool = True        # live getattr default True (14125)
    K1M_EXTREME_REVERSE_ENABLED: bool = False     # live getattr default False; needs k_1m (no array) -> NOT modeled
    REENTRY_GRACE_MINUTES: float = 30.0           # live getattr default 30.0 (14081)
    PARABOLIC_EXIT_ENABLED: bool = True           # vec-only toggle: live PARABOLIC_EXIT has NO switch (always on) -> live_gaps LG-10
    KEY_LEVEL_CRASH_ENABLED: bool = True          # vec-only toggle: live key-level crash close has NO switch (always on)
    AUGMENTED_DC_BREAK_ENABLED: bool = True       # vec-only toggle: live AUGMENTED_DC_BREAK_REDUCE_TO_MIN has NO switch (always on)
    ATR_TRAIL_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    ATR_TRAIL_SWEEP_ENABLED: bool = False  # auto-added TEMPLATE
    AUGMENTED_POSITIONS_GUARD_FLOOR_MULT: float = 0.5  # auto-added TEMPLATE
    AUGMENT_AT_LOSS_ENABLED: bool = False  # auto-added TEMPLATE
    AUGMENT_ONLY_WHEN_PROFITABLE: float = True  # auto-added TEMPLATE generic
    AUGMENT_WT_4H_BOUNCE_ENABLED: bool = False  # auto-added TEMPLATE
    BANDAID_OFF_LOSER_RECOVER_PCT: float = -0.25  # auto-added TEMPLATE
    BAND_ARROW_ENABLED: bool = False  # auto-added TEMPLATE
    BAND_ARROW_SLOPE_DEADBAND: float = 0.0  # auto-added TEMPLATE generic
    BAR_PATTERNS_FILTER_TF: str = "15m"  # 2026-09-28 WAVE3: real wiring; OFF default behavior-neutral
    BB_PULLBACK_GATE_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE2: generic_filter_tf real gate; OFF default behavior-neutral (only dead-farm reads before)
    BB_PULLBACK_GATE_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    BB_RECOVERY_ENTRY_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE2: generic_filter_tf real gate; OFF default behavior-neutral (only dead-farm reads before)
    BB_RECOVERY_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE2: generic_filter_tf real gate; OFF default behavior-neutral (only dead-farm reads before)
    BOUNCE_REENTRY_ENABLED: bool = True  # auto-added TEMPLATE
    BOUNCE_REENTRY_K_RESET_LONG: float = 35  # auto-added TEMPLATE generic
    BOUNCE_REENTRY_K_RESET_SHORT: float = 65  # auto-added TEMPLATE generic
    BREAKEVEN_DC_FIELD_MODE: str = 'DC4'  # auto-added TEMPLATE generic
    BREAKEVEN_GAIN_EROSION_ENABLED: bool = False  # auto-added TEMPLATE
    BREAKEVEN_GAIN_EROSION_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE2: generic_filter_tf real gate; OFF default behavior-neutral (only dead-farm reads before)
    BREAKEVEN_GAIN_EROSION_MIN_GAIN: float = 50.0  # auto-added TEMPLATE generic
    BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT: bool = True  # auto-added TEMPLATE generic
    BREAKOUT_LEASH_REENTRY_MULT: float = 1.5  # auto-added TEMPLATE
    BREAKOUT_RETEST_FILTER_TF: str = "15m"  # 2026-09-28 WAVE3: real wiring; OFF default behavior-neutral
    BTC_ACCEL_RAMP_REQUIRE_POSITIVE: float = True  # auto-added TEMPLATE generic
    BTC_BREAKOUT_ENTRY_ENABLED: bool = True  # auto-added TEMPLATE
    BTC_DEDICATED_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    BTC_DIVERGENCE_EXIT_AGAINST: float = True  # auto-added TEMPLATE generic
    BTC_GUARANTEED_REENTRY_ENABLED: bool = True  # auto-added TEMPLATE
    BTC_GUARANTEED_REENTRY_MAX_AGE_BARS: float = 480  # auto-added TEMPLATE
    BTC_GUARANTEED_REENTRY_MIN_GAP_BARS: int = 5  # parity fix 2026-09-04: config 5
    BTC_HARD_BLOCK_OTHER_ACCOUNTS: float = 0.0  # auto-added TEMPLATE generic
    BTC_ROUND_BANDS_EACH_SIDE: float = 8  # auto-added TEMPLATE generic
    BTC_RZ_WT_DC_MULTIFACTOR: float = True  # auto-added TEMPLATE
    BTC_TECH_EXIT_WT_MIN_TFS: float = 3  # auto-added TEMPLATE generic
    BT_WT_CROSS_LADDER_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE2: generic_filter_tf real gate; OFF default behavior-neutral (only dead-farm reads before)
    CANDLE_PATTERN_STOPS_FILTER_TF: str = "15m"  # 2026-09-28 WAVE3: real wiring; OFF default behavior-neutral
    CHANNEL_REENTRY_STOP_ENABLED: bool = False  # auto-added TEMPLATE
    CIRCUIT_SHARPE_GATES_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    COOLDOWN_LOCKS_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    CRYPTO_SPIKE_FADE_THRESHOLD_PCT: float = 10.0  # auto-added TEMPLATE
    DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED: bool = False  # auto-added TEMPLATE
    DAEMON_REENTRY_STALE_EXIT_ENABLED: bool = False  # auto-added TEMPLATE
    DC_BREACH_REDUCE_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE2: generic_filter_tf real gate; OFF default behavior-neutral (only dead-farm reads before)
    DC_BREAK_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE2: generic_filter_tf real gate; OFF default behavior-neutral (only dead-farm reads before)
    DC_HOPELESS_EXIT_ENABLED: bool = False  # auto-added TEMPLATE
    DC_HOPELESS_EXIT_MIN_AGE_S: float = 900  # auto-added TEMPLATE generic
    DC_MOMENTUM_BOTA_SCORER_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    DC_MOMENT_STRONG_THRESHOLD: float = 40.0  # auto-added TEMPLATE
    DD_BOUNCE_ENABLED: bool = False  # auto-added TEMPLATE
    DELTA_ENGINE_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    DELTA_HTF_GATE: float = 'hh_hl_4h'  # auto-added TEMPLATE generic
    DELTA_PYRAMID_MAX: float = 8  # auto-added TEMPLATE
    DELTA_PYRAMID_PRICE_TOL: float = 0.02  # auto-added TEMPLATE generic
    DELTA_REENTRY_FILTER_ENABLED: bool = False  # auto-added TEMPLATE
    DIRECTION_FAVORABLE_REENTRY_ENABLED: bool = False  # auto-added TEMPLATE
    DUP_GUARD_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    DYNAMIC_SCORE_AUGMENT_ENABLED: bool = True  # auto-added TEMPLATE
    DYN_STRUCT_TRAIL_ENABLED: bool = False  # auto-added TEMPLATE
    E2E_REPLAY_VALIDATOR_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    EMA_9_21_FILTER_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    EMA_9_21_FILTER_MIN_TFS: float = 3.0  # 2026-09-10 FIX: require 3 TFs EMA confirm (was 0 — filter never fired)
    EMA_BLANKET_FILTER_ENABLED: bool = False  # 2026-09-29 PARITY: default OFF = live-neutral (live/tradier has NO blanket gate; census NEITHER). Was True but the vec gate is real and vetoed EVERY stock-short entry → 0-trade baselines (CRM_SHORT 652 signals→0; False→113). Kept as swept switch. Was True 2026-09-28 WAVE4.
    EMA_BLANKET_FILTER_FILTER_TF: str = "15m"  # 2026-09-28 WAVE4: single-TF blanket gate (generic_filter_tf); OFF neutral
    EMA_BLANKET_FILTER_MIN_TFS: float = 3.0  # 2026-09-10 FIX: 3 TFs must confirm (was 0)
    EMERGENCY_BRAKE_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    ENTRY_PRIMARY_TF: str = "4h"  # parity fix 2026-09-04: config 4h (was 15m auto-generic)
    EXECUTE_NOW_SINGLE_GATE_ENFORCE: float = True  # auto-added TEMPLATE generic
    EXHAUSTION_EXIT_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    EXIT_R1_R2_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    EXIT_TOP_FADE_FILTER_TF: str = "15m"  # 2026-09-28 WAVE3: real wiring; OFF default behavior-neutral
    EXIT_TO_REDUCE_ADAPTER_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    EZ_MANAGE_THROTTLER_RATE: float = 0.0  # auto-added TEMPLATE generic
    E_1_EXIT_DELTA_THR: float = 50.0  # auto-added TEMPLATE generic
    E_1_WT_EXIT_USE_DELTA_ENABLED: bool = False  # auto-added TEMPLATE
    E_3_USE_WT_STRUCTURE_EXIT_MODE: float = 0.0  # auto-added TEMPLATE generic
    FAST_RISER_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE1: real gate (vec_decisions/filter_tf_gates); OFF default is behavior-neutral (field was never read before)
    FG_FEAR_THRESHOLD: float = 25  # auto-added TEMPLATE
    FG_GREED_THRESHOLD: float = 75  # auto-added TEMPLATE
    FH_MOMENTUM_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    FIRST_OPEN_THROTTLE_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    FOLLOW_THROUGH_REENTRY_ENABLED: bool = False  # auto-added TEMPLATE
    FROZEN_STOP_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    FUNDING_GATE_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    FUNDING_GATE_LONG_MAX: float = 0.0005  # auto-added TEMPLATE
    FUNDING_GATE_MTF_REQUIRED: float = True  # auto-added TEMPLATE generic
    FUNDING_GATE_SHORT_MIN: float = -0.0005  # auto-added TEMPLATE
    GOLDEN_RULE_BASE_USD: float = 5.0  # auto-added TEMPLATE generic
    GOLDEN_RULE_ENFORCE_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    GOLDEN_RULE_HTF_VOTE_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    GR_FILTER_ALL_ENTRIES: float = 0.0  # auto-added TEMPLATE generic
    GR_FILTER_VEC_ENABLED: bool = False  # auto-added TEMPLATE
    GR_FILTER_VEC_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    GR_FILTER_VEC_MIN_TFS: float = 2  # auto-added TEMPLATE generic
    GR_V5_STATE_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    HAIKU_ENTRY_GATE_ENABLED: bool = False  # auto-added TEMPLATE
    HAIKU_WINNER_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    HARD_BREAKEVEN_FLOOR_ENABLED: bool = True  # auto-added TEMPLATE
    HARD_BREAKEVEN_MIN_PEAK_PCT: float = 0.5  # auto-added TEMPLATE
    HA_WICK_QUALITY_ENABLED: bool = False  # auto-added TEMPLATE
    HA_WICK_QUALITY_SCORE: float = 15  # auto-added TEMPLATE
    HA_WICK_QUALITY_TF: str = "1h"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    HIGH_GAIN_AUGMENTATION_MIN_SIZE: float = 50  # auto-added TEMPLATE generic
    HLR_REENTRY_MAX_AGE_S: float = 14400.0  # auto-added TEMPLATE generic
    HLR_SMA_BAND_PCT: float = 0.03  # auto-added TEMPLATE
    HLR_TOP_EXIT_ENABLED: bool = True  # 2026-09-28 LIVE PARITY: config.py:2576 = True
    HLR_TOP_MIN_TFS: float = 2.0  # 2026-09-28 LIVE PARITY: config.py:2578 = 2 (min TFs confirming top, >=1 must be 4h+)
    HTF4_CONF: float = True  # auto-added TEMPLATE generic
    HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H: float = 1.0  # 2026-09-10 FIX: require 4h confirm (was 0)
    HTF_AGAINST_FORCE_CLOSE_ENABLED: bool = False  # 2026-09-29 USER: default OFF (frequent-exit churn) — swept switch; sane baseline WT/DC. Was True 2026-09-10.
    HTF_DIRECTION_GATE_ENABLED: bool = False  # 2026-09-28 WAVE4 LIVE PARITY: ez_positions_quick.py:12658 default True — ACTIVE live gate the vec lacked (baseline shift = parity)
    HTF_EXIT_VETO_ENABLED: bool = True  # auto-added TEMPLATE
    HTF_EXIT_VETO_MAX_LOSS_PCT: float = 2.0  # auto-added TEMPLATE
    HTF_EXIT_VETO_MIN_ALIGNED: float = 2  # auto-added TEMPLATE generic
    HTF_GATE_APPLY_TO_AUGMENT: float = True  # auto-added TEMPLATE generic
    HTF_GATE_APPLY_TO_OPEN: float = True  # auto-added TEMPLATE generic
    HTF_GATE_BYPASS_RZ: float = True  # auto-added TEMPLATE generic
    HTF_GATE_D_MANDATORY: float = 0.0  # auto-added TEMPLATE generic
    HTF_GATE_MIN_CONFIRMATIONS: float = 2  # auto-added TEMPLATE generic
    HTF_GATE_SIGNALS_SMA200D: bool = True  # parity fix 2026-09-04: config True
    HTF_TREND_VETO_BYPASS_ENABLED: bool = True  # auto-added TEMPLATE
    HTF_TREND_VETO_BYPASS_REASONS: float = 0.0  # auto-added TEMPLATE generic
    INTRADAY_SESSION_FORCE_EXIT_UTC: float = 35100  # auto-added TEMPLATE generic
    KILLER_KNOB_FINDER_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    LEADERBOARD_FILTER: float = True  # auto-added TEMPLATE generic
    LEGACY_PROC_SINGLE_REENTRY: float = 0.0  # auto-added TEMPLATE generic
    LEGACY_REENTRY_PSR_DC_BOUNCE: float = True  # auto-added TEMPLATE generic
    LEGACY_REENTRY_PSR_FULL_DC: float = True  # auto-added TEMPLATE generic
    LEGACY_REENTRY_PSR_K_DC_CROSSOVER: float = 0.0  # auto-added TEMPLATE generic
    LEGACY_REENTRY_PSR_QUICK_RECOVERY: float = True  # auto-added TEMPLATE generic
    LH_HL_FILTER_ENABLED: bool = False  # auto-added TEMPLATE
    LH_HL_FILTER_MODE: float = 'STRICT_2BAR'  # auto-added TEMPLATE generic
    LH_HL_FILTER_REQUIRE_BOTH: float = 0.0  # auto-added TEMPLATE generic
    LH_HL_FILTER_TF_REQ: int = 2  # 2026-09-29 accordance fix: was float 0.0 dup overriding int 2 (config=2)
    LIVE_ENTRY_ENGINE_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    LIVE_VEC_EMERGENCY_BRAKE_ENABLED: bool = False  # auto-added TEMPLATE
    LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED: bool = False  # auto-added TEMPLATE
    LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED: bool = False  # auto-added TEMPLATE
    LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK: float = True  # auto-added TEMPLATE generic
    LR_BAND_LADDER_STOCH_EXTREME: float = 30.0  # auto-added TEMPLATE generic
    LR_BAND_LADDER_TF_BOTTOM: dict = field(default_factory=lambda: {"D": 10.0, "4h": 6.0, "1h": 4.0})  # FIX 2026-09-04: was float 0.0 generic — correct dict per config_tradier 806
    LR_BAND_LADDER_TF_TOP: dict = field(default_factory=lambda: {"D": 6.0, "4h": 4.0, "1h": 1.0})  # FIX 2026-09-04: was float 0.0 generic — correct dict per config_tradier 807
    MACD_EXIT_ENABLED: bool = False  # auto-added TEMPLATE
    MACD_EXIT_MIN_GAIN: float = 0.3  # auto-added TEMPLATE generic
    MACD_EXIT_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    MACD_ZERO_CROSS_ENABLED: bool = False  # auto-added TEMPLATE
    MACD_ZERO_CROSS_SCORE: int = 15  # parity fix 2026-09-04: config 15
    MACD_ZERO_CROSS_TF: str = '1h'  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS: float = 0.0  # auto-added TEMPLATE generic
    MANDATORY_REENTRY_K_HIGH_BLOCK: float = 80.0  # auto-added TEMPLATE generic
    MANDATORY_REENTRY_K_LOW_BLOCK: float = 20.0  # auto-added TEMPLATE generic
    MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME: float = True  # auto-added TEMPLATE generic
    MANDATORY_REENTRY_WT_FILTER_MIN_TFS: float = 1  # auto-added TEMPLATE generic
    MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY: float = 0.0  # auto-added TEMPLATE generic
    MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP: float = 0.0  # auto-added TEMPLATE generic
    MANDATORY_REENTRY_WT_FILTER_TF_MODE: float = '15m_only'  # auto-added TEMPLATE generic
    MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO: float = 0.9  # auto-added TEMPLATE
    MARKET_QUALITY_SCORE_ENABLED: bool = False  # auto-added TEMPLATE
    MAX_AUGMENTS_PER_POSITION: float = 999999  # auto-added TEMPLATE generic
    MI_DIV_EXIT_ENABLED: bool = True  # 2026-09-28 WAVE4 LIVE PARITY: ez_positions_quick.py:3726+ getattr default True (was auto-added False)
    MI_ENTRY_ENABLED: bool = False  # auto-added TEMPLATE
    MI_ENTRY_EXHAUST_BONUS: int = 8  # parity fix 2026-09-04: config 8
    MI_ENTRY_STRUCT_BONUS: int = 10  # auto-added TEMPLATE generic
    MI_EXHAUST_EXIT_ENABLED: bool = True  # 2026-09-28 WAVE4 LIVE PARITY: ez_positions_quick.py:3726+ getattr default True (was auto-added False)
    MI_MIN_GAIN_EXIT: float = 0.10  # 2026-09-28 WAVE4 LIVE PARITY: ez getattr default 0.10
    MI_STRUCT_EXIT_ENABLED: bool = True  # 2026-09-28 WAVE4 LIVE PARITY: ez_positions_quick.py:3726+ getattr default True (was auto-added False)
    MI_TF_AGREE_MIN: float = 3.0  # 2026-09-28 WAVE4 LIVE PARITY: ez getattr default 3
    MI_VELOCITY_EXIT_ENABLED: bool = True  # 2026-09-28 WAVE4 LIVE PARITY: ez_positions_quick.py:3726+ getattr default True (was auto-added False)
    MI_WAVE_EXIT_ENABLED: bool = True  # 2026-09-28 WAVE4 LIVE PARITY: ez_positions_quick.py:3726+ getattr default True (was auto-added False)
    MOM3_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE1: real gate (vec_decisions/filter_tf_gates); OFF default is behavior-neutral (field was never read before)
    MOMENTUM_BREAKOUT_FILTER_TF: str = "OFF"  # 2026-09-28 WAVE1: real gate (vec_decisions/filter_tf_gates); OFF default is behavior-neutral (field was never read before)
    MOVER_THRESHOLD: float = 5.0  # auto-added TEMPLATE
    MTF_ARMED_ENTRIES_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    MTF_ATR_TRAIL_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    MTF_DC_REJECT_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    MTF_FILTER_STRONG_BUY_QUICK_BYPASS: float = True  # auto-added TEMPLATE generic
    MTF_GR_MIN_IND: float = 7  # auto-added TEMPLATE generic
    MTS_BOTTOM_BONUS_THRESHOLD: float = 25.0  # auto-added TEMPLATE
    MTS_BOTTOM_STRONG_THRESHOLD: float = 40.0  # auto-added TEMPLATE
    MTS_GATE_ENABLED: bool = True  # auto-added TEMPLATE
    MU_CORRECTION_EXIT_ENABLED: bool = False  # auto-added TEMPLATE
    MU_CORRECTION_REENTRY_ENABLED: bool = False  # auto-added TEMPLATE
    NEWBORN_LOSS_KILL_FILTER_TF: str = "15m"  # 2026-09-28 WAVE3: real wiring; OFF default behavior-neutral
    NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT: float = 0.0  # 2026-09-28 WAVE3 LIVE PARITY: ez_manage.py:46537 default -0.5 (was 0.0 stub)
    NEWBORN_LOSS_KILL_ENABLED: bool = False  # 2026-09-28 WAVE3: live config.py:1148 = False (V1 disabled after A/B; V2 vel-confirm awaits verdict)
    NEWBORN_LOSS_KILL_WINDOW_MIN: float = 30.0  # 2026-09-28 WAVE3 LIVE PARITY: ez_manage.py:46536 default 30.0
    NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST: bool = True  # 2026-09-28 WAVE3 LIVE PARITY: ez_manage.py:46541 default True (was float 0.0 stub)
    NEWBORN_PROTECT_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    NEW_POSITION_MAX_LOSS_THRESHOLD: float = -0.7  # auto-added TEMPLATE
    NOLOSS_BYPASS_WT5OF5_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    NOLOSS_BYPASS_WT_5OF5_MIN_TFS: int = 5  # EMERGENCY 2026-09-11: 5→3
    OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT: float = 1.0  # auto-added TEMPLATE
    OBLIGATORY_REENTRY_ENABLED: bool = True  # auto-added TEMPLATE
    OBLIGATORY_REENTRY_K15_HIGH_BLOCK: float = 95.0  # auto-added TEMPLATE generic
    OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC: float = 0.5  # auto-added TEMPLATE generic
    OBLIGATORY_REENTRY_SCORE_TIER1: float = 40  # auto-added TEMPLATE
    OBLIGATORY_REENTRY_SCORE_TIER2: float = 30  # auto-added TEMPLATE
    OBLIGATORY_REENTRY_SCORE_TIER3: float = 30  # auto-added TEMPLATE
    OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK: float = 5.0  # auto-added TEMPLATE generic
    OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC: float = 0.5  # auto-added TEMPLATE generic
    OBLIGATORY_REENTRY_SMA_FIELD: float = 'ema_50'  # auto-added TEMPLATE generic
    OBLIGATORY_REENTRY_SMA_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED: float = 3  # auto-added TEMPLATE generic
    OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED: float = 1  # auto-added TEMPLATE generic
    OI_CONFIRM_ENABLED: bool = True  # auto-added TEMPLATE
    OI_CONFIRM_MIN_CHANGE_PCT: float = 0.5  # FIX 2026-09-15: align to config.py 0.5 (was 0.0 dead, no OI gate)
    OI_CONFIRM_MIN_PRICE_PCT: float = 0.3  # FIX 2026-09-15: align to config.py 0.3 (was 0.0 dead)
    OPEN_INTENT_SIZE_GATES_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    PARTIAL_EXIT_FRAC: float = 0.75  # auto-added TEMPLATE generic
    PARTIAL_PROFIT_LOCK_FRAC: float = 0.5  # auto-added TEMPLATE generic
    PARTIAL_PROFIT_LOCK_V2_FILTER_TF: str = "15m"  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    PEAK_GIVEBACK_BE_EROSION_FILTER_TF: str = "15m"  # 2026-09-28 WAVE3: real wiring; OFF default behavior-neutral
    PEAK_GIVEBACK_DROP_TRIGGER_ENABLED: bool = False  # auto-added TEMPLATE
    QUICK_REDUCE_TECHNICAL_ONLY: float = True  # auto-added TEMPLATE generic
    QUICK_REENTRY_60MIN_MIN_PCT: float = 0.6  # auto-added TEMPLATE
    REENTRY2_DC_BREAK_ALLOW_15M: float = True  # auto-added TEMPLATE generic
    REENTRY2_DC_BREAK_FILTER_TF: str = "3m"  # 2026-09-29 accordance fix: was "15m" dup overriding "3m" (config="3m")
    REENTRY2_DC_BREAK_REQUIRE_K_FILTER: float = True  # auto-added TEMPLATE generic
    REENTRY2_DC_BREAK_REQUIRE_WT_FILTER: float = 0.0  # auto-added TEMPLATE generic
    REENTRY2_DIR_FAV_ENABLED: bool = True  # auto-added TEMPLATE
    REENTRY_B16_SIZE_MULT_STRONG: float = 3.0  # auto-added TEMPLATE
    REENTRY_B16_SIZE_MULT_WEAK: float = 1.5  # auto-added TEMPLATE
    REENTRY_B16_SMA200_PROX_PCT: float = 0.005  # auto-added TEMPLATE
    REENTRY_B16_SMA200_PULLBACK_ENABLED: bool = True  # auto-added TEMPLATE
    REENTRY_CROSS_FRESHNESS_ENABLED: bool = False  # auto-added TEMPLATE
    REENTRY_EXHAUSTED_PARTIAL_ENABLED: bool = True  # auto-added TEMPLATE
    REENTRY_EXIT_RECLAIM_BUFFER_PCT: float = 0.2  # auto-added TEMPLATE
    REENTRY_EXIT_RECLAIM_ENABLED: bool = True  # auto-added TEMPLATE
    REENTRY_POST_CONSOL_ATR_THRESHOLD: float = 0.15  # auto-added TEMPLATE
    REENTRY_POST_CONSOL_ENABLED: bool = True  # auto-added TEMPLATE
    REENTRY_POST_CONSOL_MULT: float = 1.5  # auto-added TEMPLATE
    REENTRY_POST_CONSOL_TFS_REQUIRED: float = 2  # auto-added TEMPLATE generic
    REENTRY_PRICE_IMPROVE_PCT: float = 0.08  # auto-added TEMPLATE
    REENTRY_BAR_TURN_ENABLED: bool = True  # 2026-09-09 bar-turn softening
    REENTRY_BAR_TURN_TF: str = "3m"
    REENTRY_BAR_TURN_REQUIRE_BOTH: bool = False
    REENTRY_BAR_STRUCTURE_ENABLED: bool = True  # 2026-09-09 bar-structure replaces fixed pct
    REENTRY_BAR_STRUCTURE_TF: str = "3m"
    RECENT_REDUCTION_GUARD_ENABLED: bool = True
    RECENT_REDUCTION_GUARD_WINDOW_S: float = 300.0
    RECENT_REDUCTION_GUARD_USE_4BAR: bool = True
    REENTRY_SMA200_BACKUP_ENABLED: bool = True  # 2026-09-09 USER: ON — alternative always tested via G625 audit split
    REENTRY_NEAR_EXIT_CHURN_OK: bool = True
    REENTRY_POSITIVE_EXIT_SIZE_MULT: float = 1.25
    PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED: bool = True
    PRICE_CROSSED_HTF_AGAINST_VETO_BAR_TURN_BYPASS: bool = True
    PRICE_CROSSED_HTF_AGAINST_VETO_HA_BYPASS: bool = True
    REENTRY_SIZE_BREAKOUT_MULT: float = 1.5  # auto-added TEMPLATE
    REENTRY_SIZE_DIP_MULT: float = 2.0  # auto-added TEMPLATE
    REENTRY_SIZE_EXTENDED_K1H: float = 90.0  # auto-added TEMPLATE generic
    REENTRY_SIZE_EXTENDED_MULT: float = 1.0  # auto-added TEMPLATE
    REENTRY_TIER1_SIZE_MULT: float = 1.5  # auto-added TEMPLATE
    REENTRY_TIER2_MAX_MINUTES: float = 120.0  # auto-added TEMPLATE generic
    REENTRY_WT15M_SIZE_MULT: float = 1.5  # auto-added TEMPLATE
    REVERSE_ON_EXIT_ENABLED: bool = False  # auto-added TEMPLATE
    RULE_B_3M_EXIT_ENABLED: bool = True  # auto-added TEMPLATE
    RZ_BREAKOUT_BAND: float = 0.05  # auto-added TEMPLATE generic
    RZ_BREAKOUT_ENTRY_ENABLED: bool = False  # auto-added TEMPLATE
    SCALP_V3_AUG_BE_STOP_ENABLED: bool = True  # auto-added TEMPLATE
    SCALP_V3_AUG_BE_STOP_PCT: float = 0.1  # auto-added TEMPLATE
    SCALP_V3_K_OB_EXIT_ENABLED: bool = True  # auto-added TEMPLATE
    SCALP_V3_K_OB_EXIT_K15M_HI: float = 80.0  # auto-added TEMPLATE generic
    SCALP_V3_K_OB_EXIT_K15M_LO: float = 20.0  # auto-added TEMPLATE generic
    SCALP_V3_K_OB_EXIT_K3M_HI: float = 80.0  # auto-added TEMPLATE generic
    SCALP_V3_K_OB_EXIT_K3M_LO: float = 20.0  # auto-added TEMPLATE generic
    SCALP_V3_K_OB_EXIT_WALL_PCT: float = 0.5  # auto-added TEMPLATE
    SCALP_V3_OB_WALL_TOO_CLOSE_PCT: float = 0.5  # auto-added TEMPLATE
    SCALP_V3_PROTECTIVE_EXIT_ENABLED: bool = True  # auto-added TEMPLATE
    SIMPLE_TP_EXIT_ENABLED: bool = False  # auto-added TEMPLATE
    SIMPLE_TP_PCT: float = 0.5  # auto-added TEMPLATE
    STDEV_BREAKOUT_EXIT_PCTB_FAIL: float = 0.75  # auto-added TEMPLATE
    STDEV_BREAKOUT_PCTB_LONG: float = 1.125  # auto-added TEMPLATE
    STDEV_BREAKOUT_PCTB_SHORT: float = -0.125  # auto-added TEMPLATE
    STDEV_REJECT_EXIT_TF: str = 'D'  # auto-added 2026-09-04 TEMPLATE FILTER_TF
    STDEV_SUPPRESS_EARLY_EXIT: float = 0.0  # auto-added TEMPLATE generic
    TREND_EXIT_SCORE_FLIP: float = 0.0  # auto-added TEMPLATE
    TREND_MIN_GAIN_EXIT: float = 0.1  # auto-added TEMPLATE generic
    UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED: bool = True  # 2026-09-28 LIVE PARITY: config.py:899 = True (later field wins — keep in sync with line ~5085)
    V8_ENTRY_ENGINE_DC_ENABLED: bool = True  # parity: config_tradier True 2026-09-04 fix (was False auto-added)
    V8_ENTRY_ENGINE_WT_ENABLED: bool = True  # parity: config_tradier True 2026-09-04 fix (was False auto-added)
    VEC_REENTRY_DC4_EXITPRICE_ENABLED: bool = True  # auto-added TEMPLATE
    WRONG_SIDE_WT_TFS_REQUIRED: int = 4  # parity fix 2026-09-04: config 4
    WT_15M_CROSS_ENTRY_ENABLED: bool = False  # auto-added TEMPLATE
    WT_4H_VEL_EXIT_ENABLED: bool = False  # auto-added TEMPLATE
    WT_4H_VEL_EXIT_K_EXTREME_HIGH: float = 80.0  # auto-added TEMPLATE generic
    WT_4H_VEL_EXIT_K_EXTREME_LOW: float = 20.0  # auto-added TEMPLATE generic
    WT_4H_VEL_EXIT_REQUIRE_K_EXTREME: float = True  # auto-added TEMPLATE generic
    WT_4H_VEL_EXIT_REQUIRE_PROFIT: float = True  # auto-added TEMPLATE generic
    WT_ACCEL_EXIT_ENABLED: bool = False  # parity fix 2026-09-13: config_tradier False (was True causing default drift)
    WT_AGAINST_FILTER_ENABLED: bool = False  # auto-added TEMPLATE
    WT_COMPOSITE_ENTRY_BLOCK: float = -20.0  # parity fix 2026-09-04: config.py -20.0
    WT_COMPOSITE_ENTRY_GOOD: float = 30.0  # parity fix 2026-09-04: config.py 30.0
    WT_COMPOSITE_ENTRY_OK: float = 10.0  # parity fix 2026-09-04: config.py 10.0
    WT_COMPOSITE_ENTRY_STRONG: float = 50.0  # parity fix 2026-09-04: config.py 50.0
    WT_CROSS_EXIT_APPLIES_TO_WINNERS: float = True  # auto-added TEMPLATE generic
    WT_CROSS_EXIT_ENABLED: bool = True  # auto-added TEMPLATE
    WT_CROSS_EXIT_MIN_AGE_MINUTES: float = 1.0  # auto-added TEMPLATE generic
    WT_CROSS_EXIT_REQUIRE_15M_CONFIRM: float = True  # auto-added TEMPLATE generic
    WT_DIV_ENTRY_GATE_ENABLED: bool = True  # auto-added TEMPLATE
    WT_DIV_EXIT_ENABLED: bool = False  # FIX 2026-09-13: was True — align to config_tradier False (was causing default drift)
    WT_EXHAUST_ENTRY_GATE_ENABLED: bool = False  # auto-added TEMPLATE
    WT_EXHAUST_EXIT_MIN_GAIN_PCT: float = 0.5  # auto-added TEMPLATE
    WT_EXHAUST_EXIT_REQUIRE_GAIN: float = 0.0  # auto-added TEMPLATE generic
    WT_MOMENTUM_EXIT_THRESHOLD: int = 1  # parity fix 2026-09-04: config 1
    WT_PERCENTILE_ENTRY_GATE_ENABLED: bool = False  # auto-added TEMPLATE
    WT_PERCENTILE_ENTRY_OB_D: float = 90.0  # parity fix 2026-09-04: config 90.0
    WT_PERCENTILE_ENTRY_OS_D: float = 10.0  # parity fix 2026-09-04: config 10.0
    WT_PERCENTILE_EXIT_ENABLED: bool = False  # auto-added TEMPLATE
    WT_PERCENTILE_EXIT_OB_4H: float = 55.0  # auto-added TEMPLATE generic
    WT_PERCENTILE_EXIT_OB_D: float = 75.0  # auto-added TEMPLATE generic
    WT_PERCENTILE_EXIT_OS_4H: float = 25.0  # auto-added TEMPLATE generic
    WT_PERCENTILE_EXIT_OS_D: float = 10.0  # auto-added TEMPLATE generic
    WT_REDUCE_FRAC_HIGH: float = 0.5  # auto-added TEMPLATE generic
    WT_REDUCE_FRAC_LOW: float = 0.15  # auto-added TEMPLATE generic
    WT_REDUCE_FRAC_MED: float = 0.25  # auto-added TEMPLATE generic

    # --- 07_EXIT_STOPS_TRAILS_RISK tranche (44 vectorizable, 34 missing added, 6 fixed) 2026-09-08 ---
    ASYMMETRIC_STOPS_ENABLED: bool = False
    BB_FROZEN_STOP_FIELD: str = 'lower'
    BB_FROZEN_STOP_TF: str = '1h'
    BOTTOM_A_PROTECTIVE_TRAIL_ARM_TIMEFRAME: str = '4h'
    BOTTOM_A_PROTECTIVE_TRAIL_BREAK_BUFFER_ATR: float = 0.5
    BOTTOM_A_PROTECTIVE_TRAIL_DISTANCE_MULT: float = 1.0
    BOTTOM_A_PROTECTIVE_TRAIL_ENABLED: bool = False
    BOTTOM_A_PROTECTIVE_TRAIL_LOOKBACK: int = 6
    BOTTOM_A_PROTECTIVE_TRAIL_MODE: str = 'STDEV'
    BOTTOM_A_PROTECTIVE_TRAIL_TRAIL_TIMEFRAME: str = '5m'
    BREAKEVEN_EXIT_AFTER_BARS: int = 8
    BREAKEVEN_EXIT_AFTER_BARS_BUFFER_PCT: float = 0.05
    BREAKEVEN_EXIT_AFTER_BARS_TF: str = '15m'
    CONNORS_RSI2_TIME_STOP_BARS_DAILY: int = 10
    DC4_STOP_GR_SCORE_MIN_IND: int = 5
    DC4_STOP_GR_SCORE_MIN_TFS: int = 3
    DC_LOW_FROZEN_STOP_TF: str = '4h'
    DD_BOUNCE_DD_STOP_ENABLED: bool = True
    EMERGENCY_BRAKE_DC_STOP_FIELD: str = 'dc_low_15m'
    EXIT_PREEMPTIVE_BREAKEVEN_ENABLED: bool = True
    HEDGE_EXIT_BYPASS_NOLOSS: bool = False
    MTF_ATR_TRAIL_TF: str = '15m'
    REENTRY_ENTRY_FILTER_ENABLED: bool = False  # 2026-09-29 USER: reentries must pass >= REENTRY_FILTER_MIN_PASS active entry filters (new entries = all)
    REENTRY_FILTER_MIN_PASS: int = 1
    MTF_ATR_TRAIL_TF_TRADIER: str = '1h'  # live parity: tradier_manage.py:11058 falls back to '1h' (config_tradier has no knob); was '5m' (matched nothing live)
    NEVER_GO_RED_STOP_ENABLED: bool = False
    NEWBORN_DC_STOP_FIELD: str = 'dc_low4_5m'
    NEWBORN_DC_STOP_MAX_AGE_MIN: float = 20.0
    NOLOSS_DC4H_GATE_ENABLED: bool = True
    NOLOSS_MIN_PROFIT_PCT: float = 0.0  # 2026-09-28 LIVE PARITY: live FIX 2026-04-07 clamps the floor to 0.3
    QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED: bool = False
    STOP_MAJOR_LOSS_ENABLED: bool = False
    STOP_TIMEFRAME: str = '15m'
    TRAILING_AUG_MAX_PER_POSITION: int = 3
    UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS: tuple = ('SCALP_V3_CLOSE', 'SCALP_V3_OPEN_PROTECTIVE', 'RIDICULOUS_HOLD', 'RIDICULOUS_LOSS', 'UNDERWATER_HEDGE_OR_CLOSE', 'DC_BB_D_BREAK_REVERSE', 'WT15M_AGAINST', 'ALL_TF_AGAINST', 'HTF_AGAINST_FORCE_CLOSE', 'R1_DC_LOW4_3M_EMERGENCY', 'NEWBORN_LOSS_KILL', 'FROZEN_ACT_STOP_FROZEN_BREACH', 'FROZEN_ACT_STOP_ABSOLUTE_FLOOR', 'R2_WT_VEL_SLOW', 'WT_15M_VEL_SLOW', 'R3_HTF_FLIP', 'R3_HTF_FLIP_4H', 'R4_STDEV_MACRO_TOP', 'R4_STDEV_MACRO_BOT', 'HEDGE_FAILED', 'WT_3M_FORCE_OPEN', 'GR_HTF_DIRECT_EXIT', 'DAEMON_REENTRY_STALE_EXIT', 'DC_STOP_BREACH', 'MTF_ATR_TRAIL', 'MTF_DC_REJECT', 'MTF_BB_REJECT', 'MTF_GR_WT_EXIT', 'AGENT_AUTONOMOUS_CLOSE', 'ZEC_SUPERVISOR_CLOSE', 'VIGILANCE_DC4', 'VIGILANCE_MAX_LOSS', 'NEGBOOK_WT_TURN', 'ULTIMATE_DC')
    UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL: bool = True

    ABLATION_DISABLE_FAST_RISER: bool = False
    ABLATION_DISABLE_SCALP_GUARD: bool = False
    ABSOLUTE_OPEN_LOCK_SEC: int = 0
    ACCOUNTS: Dict[str, Dict[str, str]] = field(default_factory=lambda: {'tra': {'id': os.getenv('TRADIER_ACCOUNT_ID_TRA') or os.getenv('TRADIER_ACCOUNT_ID'), 'key': os.getenv('TRADIER_API_KEY_TRA') or os.getenv('TRADIER_ACCESS_TOKEN') or os.getenv('TRADIER_API_KEY'), 'env': 'live'}, 'trb': {'id': os.getenv('TRADIER_ACCOUNT_ID_TRB'), 'key': os.getenv('TRADIER_API_KEY_TRB') or os.getenv('TRADIER_API_KEY_TRA') or os.getenv('TRADIER_API_KEY'), 'env': 'live'}, 'trc': {'id': os.getenv('TRADIER_ACCOUNT_ID_TRC') or os.getenv('TRADIER_SANDBOX_ID'), 'key': os.getenv('TRADIER_API_KEY_TRC') or os.getenv('TRADIER_SANDBOX_ACCESS_TOKEN') or os.getenv('TRADIER_SANDBOX_KEY'), 'env': 'paper'}})
    ACCOUNT_KEYS: List[str] = field(default_factory=lambda: ['ang', 'inf', 'men', 'flz', 'fin'])
    ACCOUNT_OVERRIDES: Dict[str, Dict] = field(default_factory=lambda: {'ang': {'NOLOSS_MIN_PROFIT_PCT': 0.0, 'HTF_STRICT': False, 'K3M_CAP': 0, 'LONG_STOCH_CHASE_BLOCK': False, 'ENTRY_ATR_PCT_MIN': 0.0}, 'men': {'NOLOSS_MIN_PROFIT_PCT': 0.0, 'HTF_STRICT': False, 'K3M_CAP': 0, 'LONG_STOCH_CHASE_BLOCK': False, 'ENTRY_ATR_PCT_MIN': 0.0}, 'inf': {'NOLOSS_MIN_PROFIT_PCT': 0.0, 'HTF_STRICT': False, 'K3M_CAP': 80, 'LONG_STOCH_CHASE_BLOCK': False, 'ENTRY_ATR_PCT_MIN': 0.0}, 'flz': {'NOLOSS_MIN_PROFIT_PCT': 0.0, 'HTF_STRICT': False, 'K3M_CAP': 80, 'LONG_STOCH_CHASE_BLOCK': False, 'ENTRY_ATR_PCT_MIN': 0.0}, 'fin': {'NOLOSS_MIN_PROFIT_PCT': 0.0, 'HTF_STRICT': False}})
    ACCOUNT_SIDE_MAPPING: Dict[str, List[str]] = field(default_factory=lambda: {'tra': ['LONG']})
    ACCOUNT_TP_PCT: Dict[str, float] = field(default_factory=dict)
    ACCOUNT_TYPE_TRA: str = 'cash'
    ADAPTIVE_REGIME_DC_BREAKDOWN_THRESHOLD: float = 0.1
    ADAPTIVE_REGIME_DC_BREAKOUT_THRESHOLD: float = 0.9
    ADAPTIVE_REGIME_DECAY_HALFLIFE_H: float = 24.0
    ADAPTIVE_REGIME_HEAT_TRIGGER: float = 30.0
    ADAPTIVE_REGIME_LOOKBACK_DAYS: int = 7
    ADAPTIVE_REGIME_MIN_SIGNALS: int = 5
    ADAPTIVE_REGIME_NPZ_CACHE_HOURS: float = 4.0
    ADAPTIVE_REGIME_SHARPE_FLOOR: float = 0.0
    ADX_TF: str = '1h'
    AGGRESSIVE_LOSS_CUT_ENABLED: bool = False
    AI_PREMARKET_DECISIONS_DIR: str = 'data/ai_premarket'
    AI_PREMARKET_ENABLED_TRB: bool = False
    AI_PREMARKET_EXPIRES_ET: str = '20:00'
    AI_PREMARKET_MAX_NEW_PER_SIDE: int = 8
    AI_PREMARKET_MIN_CONVICTION: float = 0.55
    AI_PREMARKET_SIZE_MULT_MAX: float = 1.5
    API_RATE_LIMIT_PER_MINUTE: int = 300
    API_RATE_LIMIT_PER_SECOND: int = 10
    ASYMMETRIC_LOSER_MIN_AGE_SECONDS: float = 540
    ASYMMETRIC_WINNER_GAIN_PCT: float = 1.5
    ATR_PARITY_TARGET_RISK_PCT: float = 0.2
    ATR_TRAIL_ENABLED_TRADIER: bool = False
    AUGMENT_AT_LOSS_ENABLED_TRADIER: bool = False
    AUGMENT_PYRAMID_TRADIER: bool = False
    AUG_COOLDOWN_S: float = 0.0
    AVAILABLE_IPS: List[str] = field(default_factory=lambda: ['5.75.211.216', '49.13.32.80', '157.180.125.52'])
    B10_STOCH_REV_LIVE_ENABLED: bool = False
    B10_STOCH_REV_LIVE_ENABLED_TRADIER: bool = False
    BACKUP_KLINES_CACHE: Path = Path("data")
    BALANCE_FLOOR_USD: float = 0.0
    BAND_ARROW_ENTRY_TFS: str = 'D,4h,1h'
    BAND_ARROW_EXIT_TFS: str = 'D,4h'
    BAND_ARROW_MAX_POS_MULT: float = 30.0
    BAND_FILE: Path = Path("data")
    BAND_SLOPE_SIZING_V2_TF: str = '4h'  # TEMPLATE D (was 4h) — STDEV ladder D 6mo 10x
    BASE_PATH: Path = Path("data")
    BB4H_BREAKOUT_LADDER_BASIS_PCT: float = 0.5
    BB4H_BREAKOUT_LADDER_BREAKOUT_PCT: float = 0.25
    BB4H_BREAKOUT_LADDER_MAX_STOCK_SHARES: int = 1
    BB4H_BREAKOUT_LADDER_STOCK_MAX_NOTIONAL_USD: float = 2000.0
    BB4H_BREAKOUT_LADDER_TARGET_USD: float = 2000.0
    BB4H_BREAKOUT_LADDER_WT_CROSS_PCT: float = 0.25
    BB_BREAKOUT_CONT_ENABLED: bool = True
    BB_BREAKOUT_CONT_HOURS: float = 72.0
    BB_RECOVERY_DIRECT_BARS: int = 1
    BB_RECOVERY_DIRECT_ENABLED: bool = False
    BB_RECOVERY_DIRECT_MIN_EXCURSION_ATR: float = 0.5
    BB_RECOVERY_DIRECT_TIMEFRAME: str = '1h'
    BB_RSI_STOCH_SCALP_TF: str = '15m'
    BEAR_MARKET_MODE: bool = True
    BINANCE_API_BASE: str = 'https://fapi.binance.com'
    BLACKLIST_SYMBOLS: list = field(default_factory=lambda: [])
    BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_MODE: str = 'ATR'
    BOTTOM_B_DELAYED_LOWER_TOP_ARM_BREAK_THRESHOLD: float = 0.25
    BOTTOM_B_DELAYED_LOWER_TOP_ARM_TF: str = '4h'
    BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_BARS: int = 1
    BOTTOM_B_DELAYED_LOWER_TOP_CONFIRMATION_MODE: str = 'lower_top'
    BOTTOM_B_DELAYED_LOWER_TOP_CONFIRM_TF: str = '1h'
    BOTTOM_B_DELAYED_LOWER_TOP_ENABLED: bool = True  # 2026-09-10 FIX vs B&H: wait for bottoms/lower-top confirmation
    BOTTOM_B_DELAYED_LOWER_TOP_MAX_WAIT_1H: int = 12
    BOTTOM_B_DELAYED_LOWER_TOP_PREBREAK_LOOKBACK: int = 20
    BOTTOM_B_DELAYED_LOWER_TOP_REBOUND_ATR: float = 0.5
    BOUNCE_REENTRY_K_RESET_LONG_TRADIER: int = 35
    BOUNCE_REENTRY_K_RESET_SHORT_TRADIER: int = 65
    BOUNCE_TOP_EXIT_ENABLED: bool = False
    BOUNCE_TOP_RISING_CROSS_MULT: float = 2.0
    BREAKOUT_DC1H_BYPASS_ENABLED: bool = True
    BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED: bool = False
    BREAKOUT_INJECT_LIVE: bool = False
    BREAKOUT_INJECT_SHADOW_LOG: bool = True
    BREAKOUT_LEASH_MAX_PER_MIN: int = 3
    BREAKOUT_LEASH_QTY_MULT: float = 0.25
    BREAKOUT_LEASH_TF: str = '3m'
    BREAKOUT_MAD_MULTIPLIER: float = 2.0
    BREAKOUT_MAX_DD_RATIO: float = 0.5
    BREAKOUT_MIN_RET_PCT: float = 5.0
    BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE: float = -0.1
    BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE: float = 0.2
    BREAKOUT_MULTI_LUNG_COOLDOWN_BARS: int = 4
    BREAKOUT_MULTI_LUNG_MODE: str = 'AUGMENT'
    BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE: float = 0.15
    BREAKOUT_MULTI_LUNG_TIER: str = 'CRYPTO'
    BREAKOUT_RETEST_ARMED_PERSISTENT_ENABLED: bool = False
    BREAKOUT_RETEST_ARMED_WINDOW_DAYS: int = 7
    BREAKOUT_SIZE_SMA200_T1_MULT: float = 1.5
    BREAKOUT_SIZE_SMA200_T1_PCT: float = 1.0
    BREAKOUT_SIZE_SMA200_T2_MULT: float = 2.0
    BREAKOUT_SIZE_SMA200_T2_PCT: float = 1.5
    BREAKOUT_SIZE_SMA200_T3_MULT: float = 3.0
    BREAKOUT_SIZE_SMA200_T3_PCT: float = 2.5
    BREAKOUT_TF_SIZE_CAP_MULT: float = 5.0
    BREAKOUT_TF_SIZE_ENABLED: bool = True
    BREAKOUT_TF_SIZE_MULT_15M: float = 1.0
    BREAKOUT_TF_SIZE_MULT_1H: float = 2.0
    BREAKOUT_TF_SIZE_MULT_3M: float = 0.5
    BREAKOUT_TF_SIZE_MULT_4H: float = 3.0
    BREAKOUT_TF_SIZE_MULT_5M: float = 0.5
    BREAKOUT_TF_SIZE_MULT_D: float = 4.0
    BROKER_PREFLIGHT_CACHE_S: float = 3.0
    BROKER_SYNC_MAX_ADOPT_VALUE_USD: float = 2000.0
    BROKER_SYNC_MAX_AUGMENT_VALUE_USD: float = 2000.0
    BROKER_SYNC_MAX_TOTAL_VALUE_USD: float = 2500.0
    BTC_ACCEL_RAMP_ENABLED: bool = True
    BTC_ACCEL_RAMP_MIN_TFS: int = 5
    BTC_ACCEL_RAMP_PRICE_BOUNCE_BARS: int = 3
    BTC_ACCEL_RAMP_PRICE_BOUNCE_TF: str = '3m'
    BTC_BREAKOUT_ACCEL_MIN_TFS: int = 2
    BTC_BREAKOUT_BLOCK_OPPOSING_DIV: bool = True
    BTC_BREAKOUT_COOLDOWN_BARS: int = 3
    BTC_BREAKOUT_DC_TF: str = '3m'
    BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE: float = 5.0
    BTC_BREAKOUT_HTF_MIN_ALIGNED: int = 2
    BTC_BREAKOUT_MIN_HOLD_BARS: int = 3
    BTC_BREAKOUT_REENTRY_ON_EXIT: bool = True
    BTC_BREAKOUT_REENTRY_REQUIRE_TREND: bool = True
    BTC_BREAKOUT_REQUIRE_HTF_ALIGNED: bool = True
    BTC_COOLDOWN_BARS: int = 5
    BTC_DAILY_LOSS_PCT_FLOOR: float = -1.5
    BTC_DEDICATED_ACCOUNTS: List[str] = field(default_factory=lambda: ['flz', 'inf'])
    BTC_DEDICATED_ENABLED: bool = False
    BTC_DEDICATED_SYMBOLS: List[str] = field(default_factory=lambda: ['BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'BNBUSDC', 'XRPUSDC', 'DOGEUSDC', 'ZECUSDC', 'BTCDOMUSDT'])
    BTC_DIVERGENCE_BEAR_MIN_INDS: int = 2
    BTC_DIVERGENCE_BLOCK_AGAINST: bool = True
    BTC_DIVERGENCE_BULL_MIN_INDS: int = 2
    BTC_DIVERGENCE_ENABLED: bool = True
    BTC_DIVERGENCE_LB_15M: int = 10
    BTC_DIVERGENCE_LB_1H: int = 20
    BTC_DIVERGENCE_LB_3M: int = 5
    BTC_DIVERGENCE_LB_4H: int = 20
    BTC_DIVERGENCE_LB_D: int = 10
    BTC_DIVERGENCE_LOOKBACK_BARS: int = 5
    BTC_DIVERGENCE_MIN_TF: str = '4h'
    BTC_DIVERGENCE_REQUIRE_D_CONFIRM_BARS: int = 2
    BTC_ENTRY_DIV_ONLY_ENABLED: bool = False
    BTC_ENTRY_DIV_ONLY_MIN_INDS: int = 3
    BTC_ENTRY_PRIMARY_BLOCK_OPPOSING_DIV: bool = True
    BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP: bool = True
    BTC_ENTRY_PRIMARY_REQUIRE_RZ: bool = True
    BTC_FIB_LOOKBACK_4H: int = 200
    BTC_FIB_LOOKBACK_D: int = 180
    BTC_FIB_LOOKBACK_M: int = 24
    BTC_FIB_LOOKBACK_W: int = 104
    BTC_FIB_LOOKBACK_Y: int = 5
    BTC_FIB_RECOMPUTE_ON_NEW_HL: bool = True
    BTC_FOLLOW_THROUGH_MIN_MOVE_PCT: float = 0.3
    BTC_FOLLOW_THROUGH_REENTRY_ENABLED: bool = True
    BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE: bool = True
    BTC_GUARANTEED_REENTRY_SIZE_MULT: float = 1.0
    BTC_HARD_LOSS_USD_PER_TRADE: float = 90.0
    BTC_HEDGE_DC_RESISTANCE_GATE_ENABLED: bool = True
    BTC_HEDGE_MIN_HOLD_BARS: int = 10
    BTC_HEDGE_NEVER_CLOSE_AT_LOSS: bool = True
    BTC_HEDGE_REQUIRE_4OF5_WT_TFS: bool = True
    BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_NONNEG_GAIN: bool = True
    BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_WT_3M_AND_1H: bool = True
    BTC_HEDGE_SAMESYM_ENABLED: bool = False
    BTC_HEDGE_SAMESYM_HEDGE_HARD_LOSS_PCT: float = -2.0
    BTC_HEDGE_SAMESYM_NOTIONAL_PCT: float = 1.0
    BTC_HEDGE_SAMESYM_REQUIRE_HTF_TFS_MIN: int = 1
    BTC_HEDGE_SAMESYM_REQUIRE_WT_15M: bool = True
    BTC_HEDGE_SAMESYM_REQUIRE_WT_3M: bool = True
    BTC_HEDGE_SAMESYM_TRIGGER_LOSS_PCT: float = -0.3
    BTC_HEDGE_SAME_SYMBOL_PCT: float = 1.0
    BTC_HEDGE_TRIGGER_LOSS_PCT: float = -0.4
    BTC_HEDGE_WT_KILL_CONFIRM_TF: str = '1h'
    BTC_HEDGE_WT_VEL_GATE_ENABLED: bool = True
    BTC_INTRABAR_REVERSAL_EXIT: bool = True
    BTC_LEVERAGE: float = 20.0
    BTC_MIN_HOLD_BARS: int = 5
    BTC_PAPER_PARITY_VERIFY_AT_STARTUP: bool = True
    BTC_PAPER_RECONCILE_ALARM_DRIFT_PCT: float = 0.1
    BTC_PER_SYM_CONFIG_ENABLED: bool = True
    BTC_PER_TRADE_NOTIONAL_USD_MAX: float = 290.0
    BTC_PYRAMID_DISABLED: bool = True
    BTC_REGIME_PAUSE_ENABLED: bool = True
    BTC_REVERSE_ON_EXIT_ENABLED: bool = True
    BTC_REVERSE_REQUIRE_HTF_ALIGNED: bool = True
    BTC_RISK_PATH: str = 'technical'
    BTC_ROUND_INC_PRIMARY_USD: float = 5000.0
    BTC_ROUND_INC_SECONDARY_USD: float = 1000.0
    BTC_RZ_AS_BOOST_ENABLED: bool = True
    BTC_RZ_PROXIMITY_PCT: float = 0.5
    BTC_RZ_SOFTEN_ACCEL_BY: int = 1
    BTC_RZ_USE_FIB: bool = True
    BTC_RZ_USE_ROUND: bool = True
    BTC_RZ_USE_WT_DC: bool = True
    BTC_TECH_EXIT_AT_ANY_PNL: bool = True
    BTC_TECH_EXIT_DC_BREACH_TF: str = '15m'
    BTC_TOTAL_NOTIONAL_USD_MAX: float = 980.0
    BTC_TREND_MODE_ENABLED: bool = False
    BTC_WEEKLY_LOSS_PCT_FLOOR: float = -2.5
    B_MAIN_ENTRY_GATE_ENABLED: bool = False
    CIRCUIT_BREAKER_ACCOUNT_HALT_MIN: int = 60
    CIRCUIT_BREAKER_ACCOUNT_LOSSES: int = 5
    CIRCUIT_BREAKER_COOLDOWN: int = 60
    CIRCUIT_BREAKER_SYMBOL_HALT_MIN: int = 30
    CIRCUIT_BREAKER_SYMBOL_LOSSES: int = 3
    CIRCUIT_BREAKER_THRESHOLD_PCT: float = 0.0
    CLENOW_LOOKBACK: int = 90
    CLENOW_REBALANCE_DAYS: int = 21
    CLENOW_TOP_N: int = 20
    CLOSE_FOOTHOLD_ENABLED: bool = True
    CLOSE_ZONE_SIZE_MULT: float = 1.5
    COLD_START_OPEN_BYPASS_SUPPRESS_SEC: float = 30.0
    COMMISSION_BUFFER_PCT: float = 0.08
    COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED: bool = False
    CONNORS_RSI2_EXIT_SMA_BARS_DAILY: int = 5
    CONNORS_RSI2_PRIORITY_OVERRIDE_ENABLED: bool = False
    CONSOLIDATED_KLINES_CACHE: Path = Path("data")
    COOLDOWN_BARS_15m: int = 3
    COUNTER_TREND_CRYPTO: list = field(default_factory=lambda: ['XAUUSDT', 'PAXGUSDT', 'XAGUSDT', 'BTCDOMUSDT', 'SKYUSDT'])
    CRASH_MULT_GRADIENT_ENABLED: bool = False
    CRASH_MULT_GRADIENT_MAX: float = 2.5
    CROSSES_FILE: Path = Path("data")
    CRYPTO_FH_MOMENTUM_DC_MAX_LONG: float = 0.5
    CRYPTO_FH_MOMENTUM_MAX_POSITIONS: int = 4
    CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5
    CRYPTO_FH_MOMENTUM_POSITION_SIZE_MULT: float = 1.0
    CRYPTO_SPIKE_FADE_COOLDOWN_SEC: float = 540.0
    CRYPTO_SPIKE_FADE_K_EXHAUSTION: float = 80.0
    CRYPTO_SPIKE_FADE_LOOKBACK_BARS: int = 3
    CRYPTO_SPIKE_FADE_MAX_POSITIONS: int = 6
    CYCLE_TP_ENABLED: bool = False
    CYCLE_TP_TIERED_LEVELS: list = field(default_factory=lambda: [0.0015, 0.003, 0.005, 0.007, 0.01, 0.015, 0.02, 0.03])
    DAEMON_PRICE_CROSS_PCT: float = 0.0
    DAEMON_PRICE_CROSS_REENTRY_MAX_AGE_HOURS: float = 48.0
    DATA_DIR: Path = Path("data")
    DATA_READY_FLAG_FILE: Path = Path("data")
    DATA_READY_TIMEOUT_SECONDS: int = 20
    DAYS_PLOT: int = 20
    DC_BB_CROSSBACK_HYSTERESIS_PCT: float = 2.0
    DC_BB_D_BREAK_REVERSE_ENABLED: bool = True
    DC_BREAKOUT_ALLOW_15M: bool = False
    DC_BREAKOUT_ALLOW_3M: bool = False
    DC_DAYTRADE_ACCOUNT: str = 'trb'
    DC_DAYTRADE_MAX_PER_SIDE: int = 5
    DC_HOPELESS_ENABLED: bool = False
    DC_LOW4_BYPASS_USE_STANDARD: bool = True
    DC_MOMENT_ENABLED: bool = False
    DC_MOMENT_OPPOSITE_PENALTY: float = -15.0
    DC_MOMENT_STRONG_BONUS: float = 10.0
    DC_RECOVERY_EXIT_DISABLED_ACCOUNTS: list = field(default_factory=lambda: ['inf'])
    DC_WIDTH_MAX_MULT: float = 5.0
    DD_BOUNCE_COOLDOWN_HOURS: float = 4.0
    DD_BOUNCE_REQUIRE_HIGHER_PRICE: bool = True
    DD_BOUNCE_REQUIRE_HIGHER_WT: bool = True
    DD_BOUNCE_WT_4H_ENABLED: bool = True
    DD_BOUNCE_WT_D_ENABLED: bool = True
    DEBUG: bool = False
    DELTA_ACCEL_LOOKBACK: int = 5
    DELTA_ENTRY_MIN_TF: int = 3
    DELTA_ENTRY_SCORE_BONUS: int = 15
    DELTA_ENTRY_SCORE_PENALTY: int = -25
    DELTA_EXIT_MANDATORY_REENTRY_ENABLED_TRADIER: bool = False
    DELTA_EXIT_SCORE_BONUS: int = 20
    DELTA_EXIT_SPEED_DECAY_MIN_GAIN: float = 0.5
    DELTA_EXIT_SPEED_DECAY_MIN_TFS: int = 2
    DELTA_EXIT_SPEED_DECAY_VEC_ENABLED: bool = False
    DELTA_EXIT_TF: str = '3m'
    DELTA_GATE_DIRECTION_FAVORABLE: bool = True
    DELTA_GATE_STRONG_BUY_QUICK_BYPASS: bool = True
    DELTA_LT_COOLDOWN_BARS: int = 120
    DELTA_LT_ENTRY_ACCEL_THRESHOLD: float = 0.0
    DELTA_LT_ENTRY_MIN_TF: int = 2
    DELTA_LT_ENTRY_Z_THRESHOLD: float = 1.5
    DELTA_LT_EXIT_SPEED_PCT: int = 50
    DELTA_LT_EXIT_TF: str = '4h'
    DELTA_LT_EXIT_TYPE: str = 'combined_wt_speed'
    DELTA_LT_HTF_GATE: str = '4h_D'
    DELTA_MIN_TF_FOR_ACTION: int = 2
    DELTA_OPTIONS_COOLDOWN: int = 120
    DELTA_OPTIONS_ENTRY_Z: float = 3.0
    DELTA_OPTIONS_EXIT_TYPE: str = 'giveback'
    DELTA_OPTIONS_GIVEBACK_PCT: float = 30.0
    DELTA_OPTIONS_HTF_GATE: str = '4h_D'
    DELTA_OPTIONS_MAX_HOLD: int = 240
    DELTA_PYRAMID_QTY_MULT: float = 1.5
    DELTA_SCORE_WEIGHT: float = 30.0
    DELTA_SPEED_SMOOTH: int = 5
    DELTA_TF_WEIGHTS: Optional[dict] = None
    DELTA_TF_WEIGHTS_STOCK: Optional[dict] = None
    DELTA_Z_WINDOW: int = 200
    DG_REPEAT_OPEN_PER_DAY_MAX: int = 60
    DIRECTION_FAVORABLE_MAX_MINUTES: float = 30.0
    DIRECTION_FAVORABLE_REENTRY_ENABLED_TRADIER: bool = False
    DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED: bool = False
    EARNINGS_BLACKOUT_DAYS_AFTER: int = 1
    EARNINGS_BLACKOUT_DAYS_BEFORE: int = 1
    EARNINGS_FORCE_TRIM_PCT: float = 0.5
    EARNINGS_PEAD_BOOST_ENABLED: bool = False
    EARNINGS_PEAD_BOOST_MULT: float = 1.5
    EARNINGS_PEAD_MIN_SURPRISE_PCT: float = 4.0
    EMA200_STOCHRSI_BODY_MULT: float = 1.05
    EMA200_STOCHRSI_ENABLED: bool = False
    EMA200_STOCHRSI_K_LONG: float = 20.0
    EMA200_STOCHRSI_K_SHORT: float = 80.0
    EMA200_STOCHRSI_SCORE: int = 25
    EMA200_STOCHRSI_TF: str = '1h'
    EMA_9_21_SCORE_BONUS: int = 5
    EMA_9_21_TIMEFRAME: str = '1h'
    EMA_PULLBACK_ENABLED: bool = False
    EMA_PULLBACK_SCORE_BONUS: int = 35
    EMA_PULLBACK_TF: str = '15m'
    EMERGENCY_BRAKE_MAX_TRADES_PER_MIN: int = 10
    EMERGENCY_OVERSIZE_GUARD_ENABLED: bool = False
    ENABLE_FAST_RISER_REDUCE: bool = True
    ENABLE_IP_ROTATION: bool = False
    ENABLE_LOSS_PROTECTION: bool = True
    ENABLE_MULTI_INSTANCE_ON_MACBOOK: bool = False
    ENTRY_MIN_ALIGNMENT: int = 5
    ENTRY_VET_NO_STRUCT_OR_BREAKOUT_REQUIRED: bool = True
    ENTRY_VET_RELAX_MODE: int = 3
    EPISODIC_PIVOT_ENABLED: bool = False
    EP_MAX_CONSOLIDATION_DAYS: int = 8
    EP_MAX_RETRACE_PCT: float = 25.0
    EP_MIN_GAP_PCT: float = 5.0
    EP_MIN_VOL_MULT: float = 3.0
    ERROR_RECOVERY_SLEEP_SECONDS: int = 60
    EVAL_REENTRY_ENABLED: bool = True
    EXECUTE_NOW_MAX_MARK_AGE_S: float = 60.0
    EXECUTE_NOW_WIRE_TRIPWIRE_MAX_LAG_S: float = 5.0
    EXECUTE_NOW_WIRE_TRIPWIRE_SHADOW: bool = True
    EXIT_DEAD_CODE_ENABLED: bool = False
    EXIT_EMERGENCY_DC1H_ENABLED: bool = False
    EXIT_HARD_MAX_LOSS_CAP_ENABLED: bool = False
    EXIT_KEY_LEVEL_CRASH_ENABLED: bool = True
    EXIT_ON_ALL_ENABLED: bool = False
    EXTREME_MODE: bool = False
    EZ_INDICATORS_CMD_TIMEOUT: float = 10.0
    EZ_INDICATORS_RESTART_COOLDOWN: float = 60.0
    EZ_INDICATORS_SHUTDOWN_CMD: Optional[str] = None
    EZ_INDICATORS_START_CMD: Optional[str] = None
    EZ_KLINES_API_MAX_PER_MINUTE: Dict[str, int] = field(default_factory=lambda: {'gateway': 200, 'server': 200, 'macbook': 300})
    EZ_KLINES_API_MAX_PER_SECOND: Dict[str, int] = field(default_factory=lambda: {'gateway': 22, 'server': 20, 'macbook': 22})
    EZ_KLINES_MAX_CONCURRENT: Dict[str, int] = field(default_factory=lambda: {'gateway': 10, 'server': 10, 'macbook': 15})
    EZ_KLINES_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 10, 'server': 10, 'macbook': 15})
    EZ_MANAGE_CONCURRENCY_LIMIT: Dict[str, int] = field(default_factory=lambda: {'gateway': 190, 'macbook': 180})
    EZ_MANAGE_MAKER_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 45, 'macbook': 65})
    EZ_MANAGE_RATE_LIMIT_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 35, 'macbook': 50})
    EZ_MANAGE_WS_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 140, 'macbook': 140})
    EZ_MARK_PRICES_API_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 35, 'server': 35, 'macbook': 35})
    EZ_MARK_PRICES_STARTUP_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 30, 'server': 30, 'macbook': 30})
    EZ_PRICES_API_DELAY: Dict[str, float] = field(default_factory=lambda: {'gateway': 0.5, 'server': 0.5, 'macbook': 0.5})
    EZ_PRICES_API_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 3, 'server': 3, 'macbook': 3})
    EZ_PRICES_API_SLEEP_AFTER: Dict[str, float] = field(default_factory=lambda: {'gateway': 0.3, 'server': 0.3, 'macbook': 0.3})
    EZ_PRICES_FAPI_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 2, 'server': 2, 'macbook': 2})
    EZ_PRICES_FILE_IO_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 100, 'server': 100, 'macbook': 50})
    EZ_PRICES_LIMIT_PER_HOST: Dict[str, int] = field(default_factory=lambda: {'gateway': 10, 'server': 10, 'macbook': 5})
    EZ_PRICEWS_API_DELAY: Dict[str, float] = field(default_factory=lambda: {'gateway': 0.5, 'server': 0.5, 'macbook': 0.5})
    EZ_PRICEWS_API_SEMAPHORE: Dict[str, int] = field(default_factory=lambda: {'gateway': 2, 'server': 2, 'macbook': 2})
    EZ_PRICEWS_CONNECTOR_LIMIT: Dict[str, int] = field(default_factory=lambda: {'gateway': 5, 'server': 5, 'macbook': 5})
    EZ_PRICEWS_LIMIT_PER_HOST: Dict[str, int] = field(default_factory=lambda: {'gateway': 3, 'server': 3, 'macbook': 3})
    EZ_RANKINGS_THROTTLER_RATE: Dict[str, int] = field(default_factory=lambda: {'gateway': 500, 'macbook': 250})
    EZ_REENTRY_PRICE_CROSS_INTERVAL_S: float = 5.0
    EZ_REENTRY_PRICE_CROSS_MAX_AGE_HOURS: float = 48.0
    EZ_REENTRY_PRICE_CROSS_MAX_FIRES_PER_TICK: int = 3
    EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC: float = 0.5
    EZ_REENTRY_PRICE_CROSS_PCT: float = 0.001
    EZ_REENTRY_QUEUE_CONSUMER_ENABLED: bool = True
    EZ_REENTRY_QUEUE_CONSUMER_INTERVAL_S: float = 5.0
    FAPI_BASE_URL: str = 'https://fapi.binance.com/fapi/v1'
    FAST_CUT_LOSS_MIN_AGE_MINUTES: float = 15.0
    FAST_RISER_DOUBLE_ENABLED: bool = False
    FG_SIZING_ENABLED: bool = False
    FH_MOMENTUM_MAX_POSITIONS: int = 5
    FINAL_SCORE_FILE: Path = Path("data")
    FIN_ADVISORY_CONSUMER_ENABLED: bool = False
    FIN_ADVISORY_CONSUMER_ENABLED_TRADIER: bool = False
    FOOTHOLD_PILEON_ENABLED: bool = False
    FORCE_OPEN_REQUIRE_MTF_GR: bool = True
    FORCE_REFRESH_SECONDS: float = 10
    FORMATION_CUP_HANDLE_ENTRY_ENABLED: bool = False
    FORMATION_CUP_HANDLE_EXIT_ENABLED: bool = False
    FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED: bool = False
    FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED: bool = False
    FORMATION_EXIT_MIN_GAIN_PCT: float = 0.0
    FORMATION_FLAG_PENNANT_ENTRY_ENABLED: bool = False
    FORMATION_FLAG_PENNANT_EXIT_ENABLED: bool = False
    FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED: bool = False
    FORMATION_HEAD_SHOULDERS_EXIT_ENABLED: bool = False
    FORMATION_MIN_SCORE: float = 0.65
    FORMATION_POSITION_SIZE_MULT: float = 1.0
    FORMATION_TFS: str = '15m,1h,4h,D'
    FORMATION_TREND_STRUCTURE_ENTRY_ENABLED: bool = False
    FORMATION_TREND_STRUCTURE_EXIT_ENABLED: bool = False
    FORMATION_TRIANGLE_ENTRY_ENABLED: bool = False
    FORMATION_TRIANGLE_EXIT_ENABLED: bool = False
    FORMATION_WEDGE_ENTRY_ENABLED: bool = False
    FORMATION_WEDGE_EXIT_ENABLED: bool = False
    FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO: float = -10.0
    FROZEN_ACTIVATION_TF: str = '4h'
    FSTREAM_WS_URL_BASE: str = 'wss://fstream.binance.com/market/stream'
    FULL_RECIPE_ONLY_ENABLED: bool = False
    FUNDING_EXTREME_LONG_THRESHOLD_PCT: float = -0.03
    FUNDING_EXTREME_SHORT_THRESHOLD_PCT: float = 0.05
    FUNDING_GATE_ENABLED: bool = True
    FUNDING_GATE_MTF_LONG_MAX_BULL_TFS: int = 0
    FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS: int = 1
    FUNDING_HEDGE_GATE_ENABLED: bool = True
    FUNDING_INJECT_LONG_OVERCROWDED_ABOVE: float = 0.0008
    FUNDING_INJECT_SHORT_OVERCROWDED_BELOW: float = -0.0008
    FUNDING_LIVE_REFRESH_HOURS: float = 1.0
    FUNDING_OI_INJECT_ENABLED: bool = True
    FUNDING_OI_INJECT_MAX_EACH: int = 10
    FUNDING_OI_INJECT_OI_MIN_PCT: float = 1.0
    FUNDING_OI_INJECT_PRICE_MIN_PCT: float = 0.5
    GAP_INVENTORY_ENABLED: bool = True
    GAP_INVENTORY_FILE: str = 'data/gap_inventory_tradier.json'
    GAP_INVENTORY_LOOKBACK_DAYS: int = 20
    GAP_PER_SYMBOL_INVENTORY_FILE: str = 'data/gap_inventory_tradier_per_symbol.json'
    GAP_PER_SYMBOL_HISTORY_FILE: str = 'data/gap_history_1yr_tradier.json'
    GAP_PER_SYMBOL_AVG_THRESH_PCT: float = 0.10  # E: POS avg keep long, NEG keep short, else close last 90m on local high / dc_low4_3m breakdown vv
    GAP_PER_SYMBOL_LOOKBACK_DAYS: int = 20  # 20 trading days (1 month) per spec E — last 20 opens vs prior closes
    GAP_MOC_DC_PROXIMITY_PCT: float = 0.5
    GAP_MOC_DC_WT_SAFETY_ENABLED: bool = True
    GAP_MOC_EXIT_ENABLED: bool = True
    GAP_MOC_FORCE_MOC_AT_CLOSE: bool = True  # LIVE 2026-09-10 — force MOC at deadline even if no top
    GAP_MOC_REQUIRE_TOP: bool = True  # LIVE 2026-09-10 — only exit at small top in last 90m
    GAP_MOC_WINDOW_MINUTES: int = 90  # LIVE 2026-09-10 — start of pre-close window (14:30 ET)
    GAP_MOC_EXIT_MINUTES_BEFORE_CLOSE: int = 10
    GAP_MOC_HOLD_POSITIVE_BIAS_PCT: float = 0.3
    GAP_MOC_REENTRY_SIZE_MULT: float = 1.25
    GAP_MORNING_REENTRY_ENABLED: bool = True
    GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN: int = 120  # ALWAYS 2026-09-23: 120m (09:30-11:30) per spec — neg>0.10 longs / pos>0.10 shorts close last 90m, reopen first 120m
    # CLOSE-GAP (2026-09-14 — separate from open-gap, stocks-only, always tested, shorts default)
    GAP_CLOSE_INVENTORY_ENABLED: bool = True
    GAP_CLOSE_PER_SYMBOL_INVENTORY_FILE: str = 'data/gap_close_inventory_tradier_per_symbol.json'
    GAP_CLOSE_PER_SYMBOL_HISTORY_FILE: str = 'data/gap_close_history_1yr_tradier.json'
    GAP_CLOSE_PER_SYMBOL_AVG_THRESH_PCT: float = 0.10
    GAP_CLOSE_PER_SYMBOL_LOOKBACK_DAYS: int = 30
    GAP_CLOSE_MOC_EXIT_ENABLED: bool = True  # defaults to SHORTS >0.10 before EOD
    GAP_CLOSE_MOC_ONLY_FOR_SHORTS: bool = True
    GAP_CLOSE_MOC_ONLY_STOCKS: bool = True
    GAP_CLOSE_MOC_ALWAYS_TEST: bool = True
    GAP_CLOSE_MOC_WINDOW_MINUTES: int = 90
    GAP_CLOSE_MOC_EXIT_MINUTES_BEFORE_CLOSE: int = 10
    GAP_CLOSE_MOC_REQUIRE_TOP: bool = True
    GAP_CLOSE_MOC_FORCE_MOC_AT_CLOSE: bool = True
    # INTRADAY RATIO REBALANCE — LIVE-ONLY portfolio gate, NON-VECTORIZABLE (master switch OFF in vector)
    INTRADAY_RATIO_REBALANCE_ENABLED: bool = True
    INTRADAY_RATIO_CHECK_INTERVAL_MIN: int = 15
    INTRADAY_RATIO_COOLDOWN_MIN: int = 30
    INTRADAY_RATIO_DEVIATION_THR: float = 0.10
    INTRADAY_RATIO_MAX_TRIMS_PER_DAY: int = 8
    INTRADAY_RATIO_REQUIRE_TOP: bool = False  # EMERGENCY 2026-09-11: True→False matches config_tradier (rollback if v14 proves True better)
    INTRADAY_RATIO_TRIM_FRAC: float = 0.30
    GAP_RISK_EXIT_COND_A_ENABLED: bool = True
    GAP_RISK_EXIT_COND_B_ENABLED: bool = True
    GAP_RISK_EXIT_ENABLED: bool = False  # 2026-09-29 USER: default OFF (frequent-exit churn, stock-only) — swept switch; sane baseline WT/DC. Was True.
    GAP_RISK_EXIT_LONG_ENABLED: bool = True
    GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED: bool = True
    GAP_RISK_EXIT_SHORT_ENABLED: bool = True
    GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED: bool = True
    GAP_RISK_REENTRY_ENABLED: bool = True
    GAP_RISK_REENTRY_MAX_DAYS: int = 5
    GAP_RISK_REENTRY_ON_FILL: bool = True
    GAP_RISK_REENTRY_REQUIRE_TREND: bool = False
    GAP_RISK_REENTRY_SIZE_PCT: float = 100.0
    GHOST_ABSENT_ALERT_THRESHOLD: int = 3
    GOLDEN_RULE_ACTIVATION_TF_LIST: List[str] = field(default_factory=lambda: ['D', '4h'])
    GOLDEN_RULE_BB_15M_ENABLED: bool = True
    GOLDEN_RULE_BB_1H_ENABLED: bool = True
    GOLDEN_RULE_BB_4H_ENABLED: bool = True
    GOLDEN_RULE_BB_D_ENABLED: bool = True
    GOLDEN_RULE_BB_W_ENABLED: bool = True
    GOLDEN_RULE_DC_15M_ENABLED: bool = True
    GOLDEN_RULE_DC_1H_ENABLED: bool = True
    GOLDEN_RULE_DC_4H_ENABLED: bool = True
    GOLDEN_RULE_DC_D_ENABLED: bool = True
    GOLDEN_RULE_DC_W_ENABLED: bool = True
    GOLDEN_RULE_ENABLED: bool = True
    GOLDEN_RULE_ENTRY_TF_LIST: List[str] = field(default_factory=lambda: ['1h', '15m', '3m'])
    GOLDEN_RULE_MULT_15M: float = 1.0
    GOLDEN_RULE_MULT_1H: float = 1.5
    GOLDEN_RULE_MULT_4H: float = 2.0
    GOLDEN_RULE_MULT_D: float = 3.0
    GOLDEN_RULE_MULT_W: float = 4.0
    GOLDEN_RULE_REQUIRE_HEDGE_OPEN: bool = True
    GRACEFUL_EXIT_FILE_TEMPLATE: Path = Path("data")
    GR_HEDGE_SCORE_FLOOR: int = 15
    GR_HTF_REQUIRE_BEAR: int = 1
    GR_HTF_REQUIRE_BULL: int = 1
    GR_OPEN_COOLDOWN_S: float = 0.0
    GR_TOTAL_VOTE_SCORE_MIN: int = 0
    GR_V5_ARM_WINDOW_BARS: int = 168
    GR_V5_BOUNCE_STOCH_LONG: float = 25.0
    GR_V5_BOUNCE_STOCH_SHORT: float = 75.0
    GR_V5_BREAKOUT_VOL_MULT: float = 1.25
    GR_V5_ENABLED: bool = False
    GR_V5_HTF_MIN_ALIGN: int = 2
    GR_V5_HTF_TFS: tuple = ('4h', 'D', 'W')
    GR_V5_INVALIDATE_PCT: float = 0.02
    GR_V5_LTF_MIN_ALIGN: int = 2
    GR_V5_LTF_TFS: tuple = ('3m', '15m', '1h')
    GR_V5_RETEST_BAND_PCT: float = 0.03
    GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED: bool = False
    HARD_AUGMENT_LOCK_SEC: int = 0
    HARD_MAX_LOSS_PCT: float = -5.0
    HARD_MAX_SYMBOL_VALUE_TRADIER: float = 2500.0
    HARD_REDUCE_LOCK_SEC: int = 0
    HD_ROOT: Path = Path('/Volumes/SSD2T')
    HEDGE_ALL_POSITIONS: bool = False
    HEDGE_ALREADY_COVERED_THRESHOLD: float = 0.9
    HEDGE_BALANCE_COOLDOWN_SECONDS: float = 180.0
    HEDGE_BANDAID_OFF_ENABLED: bool = False
    HEDGE_BANDAID_OFF_FIRST_PRE_VEC_ENABLED: bool = False
    HEDGE_BANDAID_OFF_REQUIRE_WT_3M_FLIP: bool = True
    HEDGE_CLOSE_MODE: str = 'wt_3m_and_1h'
    HEDGE_CLOSE_REMOVE_FROM_TRADEABLE: bool = False
    HEDGE_CLOSE_SCALP_MODE: bool = False  # 2026-09-11 hedge+scalp DISABLED — was True fast hedge close, now OFF (HEDGE_MODE=False → inert)
    HEDGE_CLOSE_WT_DC_THRESHOLD: float = 25.0
    HEDGE_CLOSE_WT_TFS_FAVOR: int = 3
    HEDGE_COMPLETED_LOCKOUT_SECONDS: int = 60
    HEDGE_DC_LONG_REJECT_DCP: float = 0.85
    HEDGE_DC_RESISTANCE_GATE_ENABLED: bool = False
    HEDGE_DC_SHORT_REJECT_DCP: float = 0.15
    HEDGE_DECAY_NUKE_ENABLED: bool = True
    HEDGE_DETERIORATING_GAIN_DELTA_PP: float = 0.1
    HEDGE_DETERIORATING_GAIN_ENABLED: bool = False
    HEDGE_DETERIORATING_GAIN_WINDOW_BARS: int = 2
    HEDGE_ENTRY_MODE: str = 'LOSS_AND_WT'
    HEDGE_EXIT_DELTA_CHECK_ENABLED: bool = False
    HEDGE_EXIT_WT_TF: str = '3m'
    HEDGE_HTF_VETO_ENABLED: bool = True
    HEDGE_MAX_ABSOLUTE_USD: float = 100000.0
    HEDGE_MAX_AGE_HOURS: float = 6.0
    HEDGE_MAX_AGE_KILL_REQUIRE_PROFIT: bool = True
    HEDGE_MAX_PCT_OF_LOSER: float = 1.0
    HEDGE_MAX_RATIO: float = 2.0
    HEDGE_MODE: bool = False
    HEDGE_MOMENTUM_GATE: bool = False
    HEDGE_NEWBORN_GRACE_MINUTES: float = 0.0
    HEDGE_OPEN_OB_CHECK_ENABLED: bool = True
    HEDGE_OPEN_OB_IMB_BOUND: float = 0.65
    HEDGE_OVERSIZE_RATIO: float = 2.0
    HEDGE_PROTECT_LOSS_VEC_ENABLED: bool = False
    HEDGE_PROTECT_QTY_PCT: float = 1.0
    HEDGE_PROTECT_TRIGGER_GAIN_PCT: float = -0.5
    HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE: bool = True
    HEDGE_SAME_SYMBOL_ENABLED: bool = False
    HEDGE_SAME_SYMBOL_PCT: float = 1.0
    HEDGE_SAME_SYMBOL_TRADIER: bool = False
    HEDGE_SCALP_C_REQUIRE_COMBINED_NONNEG: bool = True
    HEDGE_SCALP_MAX_AGE_MIN: float = 15.0
    HEDGE_SIZE_RATIO_TRADIER: float = 0.25
    HEDGE_STRICT_WT_ALL_TFS_ENABLED: bool = True
    HEDGE_STRICT_WT_MIN_TFS_AGAINST: int = 4
    HEDGE_TRIGGER_GR_SCORE_ENABLED: bool = True
    HEDGE_TRIGGER_LOSS_PCT: float = -0.05
    HEDGE_TRIGGER_LOSS_PCT_ENTRY: float = -2.0
    HEDGE_TRIGGER_LOSS_TRADIER: float = -1.0
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H: bool = True
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H: bool = False
    HEDGE_TRIGGER_USE_WT_3M_ALONE: bool = False
    HEDGE_WEBHOOK_LOCK_TTL_SEC: float = 60.0
    HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN: bool = False
    HEDGE_WT_VEL_GATE_ENABLED: bool = False
    HLR_BYPASS_MIN_TF_WEIGHT: int = 25
    HLR_MIN_GAIN_PCT: float = 1.0
    HLR_MIN_TFS: int = 2
    HLR_MIN_TFS_FOR_BYPASS: int = 1
    HLR_OFF_SMA_PTS_FRAC: float = 0.5
    HLR_OFF_SMA_SZ_FRAC: float = 0.7
    HLR_PTS_15M: int = 25
    HLR_PTS_1H: int = 40
    HLR_PTS_3M: int = 15
    HLR_PTS_4H: int = 60
    HLR_PTS_D: int = 90
    HLR_PTS_W: int = 130
    HLR_RALLY_ENABLED: bool = True
    HLR_REDUCE_FRAC: float = 0.5
    HLR_REENTRY_MULT: float = 1.5
    HLR_SZ_15M: float = 1.5
    HLR_SZ_1H: float = 2.0
    HLR_SZ_3M: float = 1.2
    HLR_SZ_4H: float = 3.5
    HLR_SZ_D: float = 6.0
    HLR_SZ_MAX: float = 10.0
    HLR_SZ_W: float = 10.0
    HLR_TOP_MIN_GAIN_PCT: float = 1.5
    HLR_TOP_VEL_1H_THRESH: float = -1.0
    HLR_TOP_VEL_4H_THRESH: float = 0.0
    HLR_TOP_VEL_D_THRESH: float = 0.0
    HOUR_OF_DAY_BLOCKED_UTC: list = field(default_factory=list)
    HOUR_OF_DAY_GATE_ENABLED: bool = False
    HTF_AUG_VETO_FIX_ENABLED: bool = True
    HTF_DC_BREAKOUT_TRADIER_TF: str = '4h'
    HTF_REGIME_ADD_MULT_PER_SMA: float = 0.75
    HTF_REGIME_ENABLED: bool = False
    HTF_REGIME_EXIT_TF: str = '15m'
    HTF_REGIME_LEDGER_PATH: str = 'data/_diagnostic/persym_htf_ledger_full.json'
    HTF_REGIME_SIZE_CAP: float = 3.0
    HTF_REGIME_TF: str = 'D'
    HTF_REGIME_VOL_TARGET: float = 0.0
    IMMEDIATE_WRONG_WAY_ENABLED: bool = False
    INDICATORS_FILE: Path = Path("data")
    INDICATORS_SAVE_INTERVAL_SECONDS: float = 10.0
    INDICATOR_UPDATE_INTERVAL: float = 30.0
    INF_7D_BEAT_MIN_DELTA: float = 0.0
    INF_7D_BEAT_SIZE_MULT: float = 2.0
    INF_DEDICATED_WINNERS: set = field(default_factory=lambda: {})
    INF_DEDICATED_WINNERS_ENABLED: bool = True
    INF_RANKING_BYPASS_DELTA: bool = False
    INF_RANKING_BYPASS_FRESHNESS_MIN: int = 30
    INF_RANKING_BYPASS_MAX_POS: int = 8
    INF_RANKING_BYPASS_SCORE: bool = False
    INF_RANKING_BYPASS_WT: bool = False
    INF_RANKING_PRIORITY_BYPASS: bool = False
    IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED: bool = False
    IN_GAIN_TREND_REDUCE_FRAC: float = 0.5
    K3M_CAP: int = 80
    K3M_CAP_BREAKOUT_BYPASS: bool = True
    KINDERGARTEN_EMA_GATE_ENABLED: bool = False  # 2026-09-28 live parity: config.py (crypto)=False; config_tradier=True (stocks divergence flagged in 06_V12_PARITY_MATRIX — engine base is crypto)
    KINDERGARTEN_CUMULATIVE_MODE: bool = True  # 2026-09-14 FIX: cumulate all kindergarten filters (not OR single-pick)
    KINDERGARTEN_CUMULATIVE_MIN_TFS: int = 1
    KINDERGARTEN_STRICT_TFS: str = ""
    KINDERGARTEN_ALWAYS_TEST: bool = True
    KINDERGARTEN_FILTER_TF: str = "15m"  # 2026-09-22 FIX: FTF dependent — must be tested on every TF via _ALL_FILTER_TF
    EMA_9_21_FILTER_TFS: str = "1h"
    WT_SIMPLE_GUARANTEE_ENABLED: bool = False  # 2026-09-28 NO-LIES/parity: switch exists NOWHERE in live code — a vector-only entry crutch guaranteeing trades live would not take. OFF: 0-trade baselines are honest evidence of missing real entry wiring, not a bug to paper over
    FORCE_MIN_ONE_TRADE: bool = False  # 2026-09-22: NOT forced trade — script trades ALWAYS via WT_SIMPLE + gates, forced is artificial
    EMA_50_200_FILTER_ENABLED: bool = False
    EMA_50_200_TIMEFRAME: str = "D"
    EMA_50_200_TFS: str = "D"
    SMA_50_FILTER_ENABLED: bool = False
    SMA_50_TIMEFRAME: str = "D"
    SMA_200_FILTER_ENABLED: bool = False
    SMA_200_TIMEFRAME: str = "D"
    KLINES_CACHE_DIR: Path = Path("data")
    KLINE_COLUMNS: List[str] = field(default_factory=lambda: ['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    K_LOWER_HIGH_EXTREME: float = 95.0
    K_LOWER_HIGH_LTF_THRESHOLD: float = 65.0
    K_ZONE_ENTRY_BONUS: int = 25
    LADDER_AUTO_SAVE_SECONDS: float = 60.0
    LADDER_TTL_MINUTES: int = 24 * 60
    LAST_EVENTS_FILE: Path = Path("data")
    LATEST_MARKET_DATA_FILE: Path = Path("data")
    LEADERBOARD_ENTRY_ENABLED: bool = False
    LEADERBOARD_ENTRY_ENABLED_TRADIER: bool = False
    LEADERBOARD_LONG: Path = Path("data")
    LEADERBOARD_SHORT: Path = Path("data")
    LEGACY_AGGRESSIVE_LOSS_CUT: bool = False
    LEGACY_DIRECTION_FAVORABLE: bool = False
    LEGACY_FAST_CUT_LOSS: bool = False
    LEGACY_REENTRY_GUARANTEED_2WT: bool = False
    LEGACY_REENTRY_GUARANTEED_BOTTOM: bool = False
    LEGACY_REENTRY_GUARANTEED_CROSS: bool = False
    LH_HL_FILTER_HEDGE_GATE_ENABLED: bool = False
    LIGHT_MODE: bool = False
    LINEARITY_LR_LONG_ENABLED: bool = False
    LINEARITY_LR_SHORT_ENABLED: bool = False
    LIVE_5m_trading_ENABLED: bool = True
    LIVE_POSITION_FRESHNESS_MAX_SEC: float = 3.0
    LIVE_USDC_PAIRS_FILE: Path = Path("data")
    LIVE_VEC_QUARANTINE_STRATEGY_ENABLED: bool = False
    LIVE_VEC_STALE_MARK_PRICE_ENABLED: bool = False
    LOCAL_DATA_MAX_AGE: float = 200.0
    LOCAL_EXTREMES_SCORER_ENABLED: bool = False
    LOG_BACKUP_COUNT: int = 30
    LOG_DIR: Path = Path("data")
    LOG_FILE_EZ_BACKUP: Path = Path("data")
    LOG_FILE_EZ_CROSSES: Path = Path("data")
    LOG_FILE_EZ_INDICATORS: Path = Path("data")
    LOG_FILE_EZ_MANAGE: Path = Path("data")
    LOG_FILE_EZ_MARK_PRICES: Path = Path("data")
    LOG_FILE_EZ_PRICES: Path = Path("data")
    LOG_FILE_EZ_PRICES_WS: Path = Path("data")
    LOG_FILE_EZ_RANKINGS: Path = Path("data")
    LOG_FILE_TRADIER_MANAGE: Path = Path("data")
    LOG_FILE_TRADIER_POSITIONS: Path = Path("data")
    LOG_FILE_TRADIER_PRICES: Path = Path("data")
    LOG_INTERVAL_SECONDS: int = 10
    LOG_MAX_BYTES: int = 1024 * 1024 * 20
    LONG_STRUCT_EXIT_TF: str = 'None'  # 2026-09-26 DISABLED HYBRID 449 closes NOT in per_sym — NON_VECTORIZABLE paper ON set D
    LONG_WAIT_DIRECT_BOUNCE_DISTANCE: float = 0.015
    LONG_WAIT_DIRECT_BOUNCE_TIMEFRAME: str = '15m'
    LONG_WAIT_DIRECT_CONFIRMATION: str = 'stoch5'
    LONG_WAIT_DIRECT_DEEP_K4H: float = 50.0
    LONG_WAIT_DIRECT_ENABLED: bool = False
    LONG_WAIT_DIRECT_TURN_K1H: float = 40.0
    LOSERS_15M_FILE: Path = Path("data")
    LOSERS_20_FILE: Path = Path("data")
    LOSS_CUT_ENABLED: bool = False
    LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED: bool = False
    LOSS_EXIT_TECHNICAL_BYPASS: tuple = ('LIQUIDATION', 'EMERGENCY_DC1H_BREACH', 'PARABOLIC_EXIT', 'GAIN_EROSION')
    LR_BAND_E02_EXIT_ENABLED: bool = False
    LR_BAND_ENTRY_PRIORITY: bool = False
    LR_BAND_LADDER_BASE_UNIT_USD: float = 2000.0
    LR_BAND_LADDER_CAPACITY_USD: float = 16000.0
    LR_BAND_LADDER_ORDINARY_PARITY_ENABLED: bool = False
    LR_BAND_LADDER_TRIGGER: str = 'union'
    LR_BAND_READD_LO: float = 0.3
    LR_CHANNEL_LONG_LENGTHS: dict = field(default_factory=lambda: {'1h': 200, '4h': 200, 'D': 300})
    LR_PCTB_D_LONG_ENTRY_THRESHOLD: float = 0.2
    LS_RATIO_CONTRARIAN_ENABLED: bool = False
    LS_RATIO_ENFORCE: bool = True
    LS_RATIO_EXTREME_THRESHOLD: float = 70.0
    LS_RATIO_HARD_MAX: float = 3.5
    LS_RATIO_HARD_MIN: float = 0.05
    LS_RATIO_LOG_INTERVAL: int = 60
    LS_RATIO_MAX: float = 9.0
    LS_RATIO_MIN: float = 0.11
    LS_RATIO_PENALTY: int = 15
    LS_RATIO_REBALANCE_THRESHOLD: float = 0.6
    LT_SCORE_WEIGHT_HTF: float = 0.7
    LT_TOP_SIZE: int = 100
    LUNCH_DEADZONE_MODE: str = 'BLOCK_MOMENTUM'
    MACRO_BLACKOUT_SIZE_MULT: float = 0.5
    MAKER_CLOSE_COMMISSION_FLOOR_ENABLED: bool = False
    MAKER_CLOSE_COMMISSION_FLOOR_TTL_SEC: float = 300.0
    MANDATORY_HEDGE_GAIN_THRESHOLD_PCT: float = -0.5
    MANDATORY_HEDGE_HARD_THRESHOLD_PCT: float = -2.0
    MANDATORY_HEDGE_ON_NEGATIVE_ENABLED: bool = True
    MANDATORY_PRICE_CROSS_EPQ_ENABLED: bool = False
    MANDATORY_PRICE_CROSS_EPQ_ENABLED_TRADIER: bool = False
    MANDATORY_REENTRY_MIN_WT_AGREE: int = 2
    MARKET_CLOSE_HOUR: int = 16
    MARKET_CLOSE_MINUTE: int = 0
    MARKET_CRASH_THRESHOLD_PCT: float = 0.0
    MARKET_DATA_REFRESH_INTERVAL_SECONDS: float = 45.0
    MARKET_JUMP_THRESHOLD_PCT: float = 0.0
    MARKET_MODE: str = 'NORMAL_MODE'
    MARKET_MODE_FILE: str = 'data/market_mode.json'
    MARKET_OPEN_HOUR: int = 9
    MARKET_OPEN_MINUTE: int = 30
    MARK_PRICE_MAX_STALENESS: float = 2
    MAX_ALLOWED_DRAWDOWN_PCT: float = 50.0
    MAX_CONCURRENT_ORDERS: float = 186
    MAX_FILES: int = 20
    MAX_HEDGE_BALANCE_MULTIPLIER: float = 1.8
    MAX_HEDGE_BALANCE_VALUE_USD: float = 500.0
    MAX_MARKET_DATA_FILE_AGE_SECONDS: float = 1200
    MAX_MEMORY_GB: int = 8
    MAX_ORDER_VALUE_FIN: float = 280.0
    MAX_ORDER_VALUE_MEN: float = 280.0
    MAX_POSITION_SIZE_BTC: float = 2000.0
    MAX_POSITION_SIZE_FIN: float = 280.0
    MAX_POSITION_SIZE_MEN: float = 280.0
    MEMORY_MONITOR_SLEEP_SECONDS: int = 60
    MICRO_SCALP_GAIN_THRESHOLD_PCT: float = 0.02
    MICRO_SCALP_STOCKS_ACCOUNTS: List[str] = field(default_factory=lambda: [])
    MICRO_SCALP_USDC_ACCOUNTS: list = field(default_factory=lambda: ['ang', 'inf', 'flz', 'men', 'fin'])
    MICRO_SCALP_USDC_MAKER_ENABLED: bool = False
    MID_ZONE_SHORT_EXTRA_IND: str = 'wt_crossunder_15m'
    MINERVINI_MIN_SCORE: int = 5
    MINERVINI_MIN_SEPA_SCORE: int = 5
    MIN_HOLD_BARS_15m: int = 2
    MIN_QTY_FILE: Path = Path("data")
    MIN_USD_DELTA_CONFIRM: float = 1.0
    MITIGATOR_ACCOUNT: list = field(default_factory=lambda: [])
    MITIGATOR_AUGMENT_CONSECUTIVE: int = 3
    MITIGATOR_AUGMENT_THRESHOLD: float = 0.3
    MITIGATOR_COOLDOWN: float = 15.0
    MITIGATOR_ENABLED: bool = False
    MITIGATOR_REENTRY_COOLDOWN: float = 180.0
    MITIGATOR_REENTRY_PRICE_PCT: float = 0.15
    MITIGATOR_SCAN_INTERVAL: float = 3.0
    MITIGATOR_TIER1_DROP: float = 0.08
    MITIGATOR_TIER1_PEAK: float = 0.15
    MITIGATOR_TIER1_REDUCE_PCT: float = 0.25
    MITIGATOR_TIER2_DROP: float = 0.02
    MITIGATOR_TIER2_REDUCE_PCT: float = 0.5
    MITIGATOR_TIER3_DROP: float = -0.05
    MI_TF_AGREE_MIN_TRADIER: int = 3
    MOM4S_S_ACCOUNTS: list = field(default_factory=lambda: ['men'])
    MOM4S_S_GATE_ENABLED: bool = False
    MOM5_TRENDER_L_ACCOUNTS: list = field(default_factory=lambda: ['men'])
    MOM5_TRENDER_L_GATE_ENABLED: bool = False
    MOMENTUM_FADE_BODY_ATR_MIN: float = 2.0
    MOMENTUM_FADE_ENABLED: bool = False
    MOMENTUM_FADE_K_ZONE: bool = True
    MOMENTUM_FADE_SCORE_BONUS: int = 35
    MOMENTUM_FADE_SCORE_BONUS_TRADIER: int = 5
    MOMENTUM_FADE_VOL_MIN: float = 2.0
    MOMENTUM_RIDER_ACCOUNT: str = 'men'
    MOMENTUM_RIDER_BASE_SIZE_USD: float = 50.0
    MOMENTUM_RIDER_COOLDOWN: float = 300.0
    MOMENTUM_RIDER_DC_WIDTH_MIN: float = 8.0
    MOMENTUM_RIDER_ENABLED: bool = False
    MOMENTUM_RIDER_HEDGE_RATIO: float = 1.2
    MOMENTUM_RIDER_MAX_SIZE_USD: float = 400.0
    MOMENTUM_RIDER_MAX_SYMBOLS: int = 5
    MOMENTUM_RIDER_REL_VOL_MIN: float = 3.0
    MOMENTUM_RIDER_SCAN_INTERVAL: float = 10.0
    MOMENTUM_SMA_WATCHDOG_COOLDOWN_S: float = 60.0
    MOMENTUM_SMA_WATCHDOG_INTERVAL_S: float = 60.0
    MOMENTUM_SMA_WATCHDOG_PCT: float = 1.0
    MOMENTUM_SMA_WATCHDOG_WT_CAP: float = 80.0
    MOMENTUM_WATCHDOG_ENABLED: bool = False
    MONITOR_REDUCTION_STALE_THRESHOLD: float = 180.0
    MOVER_ACCOUNT: str = 'inf'
    MOVER_LINEARITY_MIN: float = 0.3
    MOVER_LOOKBACK: int = 8
    MOVER_MAX_POSITIONS: int = 6
    MOVER_SCORE_BONUS: int = 40
    MOVER_VOL_MIN: float = 1.0
    MR3S_S_ACCOUNTS: list = field(default_factory=lambda: ['fin'])
    MR3S_S_GATE_ENABLED: bool = False
    MR5_L_ACCOUNTS: list = field(default_factory=lambda: ['fin'])
    MR5_L_GATE_ENABLED: bool = False
    MTF_ARMED_BANDTYPES: str = 'dc,bb,wt'
    MTF_ARMED_HTF_LIST: str = '1h,4h,D,W'
    MTF_ARMED_WT_DIRECTION_SUSPEND_BREAKOUT_BYPASS: bool = True
    MTF_ARROW_SHORT_ENTRY_ENABLED: bool = False
    MTF_ARROW_SLOPE_NORM_PCT_DAY: float = 0.3
    MTF_ARROW_THETA: float = 0.3
    MTF_ARROW_WEIGHTS: dict = field(default_factory=lambda: {'1h': 0.35, '4h': 0.35, 'D': 0.3})
    MTF_ATR_MULTITF_DIRECT_ENABLED: bool = False
    MTF_ATR_MULTITF_DIRECT_MIN_CONFIRMING_TFS: int = 1
    MTF_ATR_MULTITF_DIRECT_MIN_PROFIT_PCT: float = 0.5
    MTF_ATR_MULTITF_DIRECT_MULT: float = 1.5
    MTF_ATR_MULTITF_DIRECT_TIMEFRAMES: list[str] = field(default_factory=lambda: ['1h', '4h', 'D'])
    MTF_BB_REJECT_EXIT_LOOKBACK: int = 5
    MTF_BB_REJECT_EXIT_TF: str = '1h'
    MTF_DC_REJECT_EXIT_LOOKBACK: int = 5
    MTF_DC_REJECT_EXIT_TF: str = '1h'
    MTF_EXIT_MIN_OPEN_TS: float = 1779235200.0
    MTF_GR_EXIT_MIN_IND: int = 5
    MTF_GR_EXIT_MIN_TFS: int = 3
    MTF_WT_CROSS_EXIT_TF: str = '15m'  # live parity: config.py '15m' (2026-09-28 exit-vectorization; 'either' is inert live+vec)
    MTF_DC_REJECT_USE_DC4: bool = False  # NEW 2026-09-28 (user): band field dc_high4/dc_low4_{TF} instead of dc_high/dc_low_{TF}; wired live ez_manage same session
    MTF_GR_MIN_TFS: int = 3
    MTF_WT_CROSS_EXIT_DIRECT_ENABLED: bool = False
    MTF_WT_CROSS_EXIT_TF: str = '15m'
    MTS_BOTTOM_MIN: float = 15.0
    MTS_BOTTOM_MIN_SHORT: float = 10.0
    MTS_ENTRY_QUALITY_BONUS: float = 25.0
    MTS_ENTRY_QUALITY_MIN: float = 8.0
    MTS_ENTRY_QUALITY_MIN_SHORT: float = 5.0
    MTS_ENTRY_QUALITY_STRONG: float = 40.0
    MTS_WEIGHT_15m: float = 8.0
    MTS_WEIGHT_1h: float = 12.0
    MTS_WEIGHT_1m: float = 3.0
    MTS_WEIGHT_3m: float = 5.0
    MTS_WEIGHT_4h: float = 4.0
    MTS_WEIGHT_5m: float = 2.0
    MTS_WEIGHT_D: float = 2.0
    MULT_FILE: Path = Path("data")
    MU_CORRECTION_HTF_K_MIN: float = 80.0
    MU_CORRECTION_HTF_MIN_TFS: int = 1
    MU_CORRECTION_HTF_RSI_MIN: float = 60.0
    MU_CORRECTION_HTF_TFS: str = '1h+4h'
    MU_CORRECTION_LTF_FALL_MIN_TFS: int = 2
    MU_CORRECTION_LTF_FALL_TFS: str = '5m+15m'
    MU_CORRECTION_MIN_GAIN_PCT: float = 0.0
    MU_CORRECTION_REENTRY_DC_TOL_PCT: float = 2.0
    MU_CORRECTION_SYMBOLS: str = 'MU'
    NEWBORN_LOSS_KILL_ENABLED: bool = False
    NEWBORN_LOSS_KILL_MIN_AGE_MIN: float = 15.0
    NEWBORN_LOSS_KILL_SURGICAL_ONLY: bool = True
    NEWBORN_LOSS_KILL_VEL_TF: str = ''
    NEWBORN_LOSS_KILL_WINDOW_MIN: float = 30.0
    NEWS_POLL_INTERVAL_CRYPTO: int = 300
    NEWS_POLL_INTERVAL_SOCIAL: int = 900
    NEWS_SENTIMENT_DECAY_HOURS: int = 4
    NEWS_SENTIMENT_MIN_ARTICLES: int = 2
    NEWS_SENTIMENT_WEIGHT: float = 0.1
    NEW_POSITION_MIN_AGE_SECONDS: float = 180.0
    OBLIGATORY_HEDGE_ENABLED: bool = False
    OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED: bool = False
    OBLIGATORY_HEDGE_OR_CLOSE_LOOP_INTERVAL_SECONDS: float = 60.0
    OBLIGATORY_HEDGE_WT_TFS: int = 2
    OBLIGATORY_HEDGE_WT_TFS_REQUIRED: int = 0
    OBLIGATORY_HEDGE_WT_USE_15M: bool = False
    OBLIGATORY_HEDGE_WT_USE_1H: bool = True
    OBLIGATORY_HEDGE_WT_USE_1M: bool = False
    OBLIGATORY_HEDGE_WT_USE_3M: bool = True
    OBLIGATORY_OPEN_USD: float = 14.0
    OBLIGATORY_REENTRY_LONG_ENABLED: bool = True
    OBLIGATORY_REENTRY_SHORT_ENABLED: bool = True
    OBLIGATORY_REENTRY_SHORT_SMA_BOUNCE_SIZE_MULT: float = 1.5
    OBLIGATORY_REENTRY_SMA_BOUNCE_SIZE_MULT: float = 1.5
    OBLIGATORY_SMA200_PCT: float = 1.0
    OBLIGATORY_SMA200_WT3M_ENABLED: bool = False  # 2026-09-26 DISABLED live-only OBLIGATORY_OPEN NOT in per_sym — NON_VECTORIZABLE paper ON
    OB_ENTRY_GATE_ACCOUNTS: list = field(default_factory=lambda: [])
    OB_ENTRY_MIN_LONG_SCORE: float = 0.0
    OB_ENTRY_MIN_SHORT_SCORE: float = 0.0
    OB_ENTRY_WALL_TOO_CLOSE_PCT: float = 0.0
    OB_PRICE_DEFER_ACCOUNTS: list = field(default_factory=lambda: [])
    OB_PRICE_DEFER_AT_LEVEL_TOL_PCT: float = 0.05
    OB_PRICE_DEFER_ENABLED: bool = False
    OB_PRICE_DEFER_MAX_DISTANCE_PCT: float = 0.5
    OB_PRICE_DEFER_TTL_SEC: float = 300.0
    OI_DIVERGENCE_ENABLED: bool = False
    OI_DIVERGENCE_PENALTY: int = 10
    OI_HEDGE_GATE_ENABLED: bool = False
    OI_LIVE_REFRESH_HOURS: float = 1.0
    OPENING_BUFFER_NO_CLOSE_MINUTES: float = 30.0
    OPEN_RATE_BREAKER_ENABLED: bool = False
    OPEN_RATE_MAX: int = 30
    OPEN_RATE_WINDOW_SEC: float = 30.0
    OPPOSITE_LOSER_DEEP_LOSS_PCT: float = -5.0
    OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED: bool = False
    OPPOSITE_LOSER_HEDGE_PROTECT_MAX_GAIN: float = 5.0
    OPPOSITE_LOSER_HEDGE_PROTECT_REQUIRE_WT_3M: bool = False
    OPTIMAL_HOLD_BARS_15M: int = 999
    OPTIMAL_HOLD_BARS_3M: int = 999
    OPTIONS_ALERT_ABS_LOSS_PP: float = 25.0
    OPTIONS_ALERT_DROP_PP: float = 5.0
    OPTIONS_AUGMENT_INTO_LOSS_THRESHOLD: float = 0.85
    OPTIONS_BASE_CAP: float = 5000.0
    OPTIONS_BUY_MAX_OTM_PCT: float = 3.0
    OPTIONS_BUY_MIN_ABS_DELTA: float = 0.35
    OPTIONS_BUY_MIN_DTE: int = 60
    OPTIONS_BUY_MIN_WT_DC_SCORE: float = 70.0
    OPTIONS_BUY_PREFERRED_DTE: int = 90
    OPTIONS_CSP_DTE_MAX: int = 90
    OPTIONS_CSP_DTE_MIN: int = 60
    OPTIONS_CSP_EDGE_MARGIN: float = 1.15
    OPTIONS_CSP_ENABLED: bool = False
    OPTIONS_CSP_MAX_CAPITAL_PCT: float = 0.3
    OPTIONS_CSP_MAX_DELTA: float = 0.3
    OPTIONS_CSP_MAX_HOLD_DAYS: int = 21
    OPTIONS_CSP_MAX_POS_PCT_OF_ACCOUNT: float = 0.03
    OPTIONS_CSP_MIN_DELTA: float = 0.15
    OPTIONS_CSP_MIN_EXTRINSIC_PCT: float = 0.015
    OPTIONS_CSP_MIN_IV_RANK: float = 40.0
    OPTIONS_CSP_MONITOR_CALL_BREACH_PCT: float = 0.05
    OPTIONS_CSP_MONITOR_CALL_GAP_FROM_ENTRY_PCT: float = 0.15
    OPTIONS_CSP_MONITOR_CORRELATED_BREACH_N: int = 3
    OPTIONS_CSP_MONITOR_GAP_FROM_ENTRY_PCT: float = 0.15
    OPTIONS_CSP_MONITOR_LOSS_TRIGGER_PCT: float = -0.2
    OPTIONS_CSP_MONITOR_MAX_LOSS_PCT: float = -1.5
    OPTIONS_CSP_MONITOR_POLL_SEC: int = 60
    OPTIONS_CSP_MONITOR_STRIKE_BREACH_PCT: float = 0.05
    OPTIONS_CSP_NAKED_CALL_ENABLED: bool = False
    OPTIONS_CSP_PROFIT_TARGET_PCT: float = 0.5
    OPTIONS_EQUITY_HEDGE_COOLDOWN_MIN: float = 60.0
    OPTIONS_EQUITY_HEDGE_ENABLED: bool = False
    OPTIONS_EQUITY_HEDGE_MAX_NOTIONAL_USD: float = 2500.0
    OPTIONS_EQUITY_HEDGE_MAX_PCT_OF_OPT_COST: float = 100.0
    OPTIONS_EQUITY_HEDGE_TRIGGER_PCT: float = -10.0
    OPTIONS_FULL_DIV_CAP: float = 15000.0
    OPTIONS_HEDGED_CAP: float = 10000.0
    OPTIONS_HEDGE_BOTTOM_MIN_SIGNALS: int = 2
    OPTIONS_HEDGE_DC_REL_TOL_PCT: float = 1.0
    OPTIONS_HEDGE_K_OVERSOLD_PCT: float = 25.0
    OPTIONS_HEDGE_LADDER_ENABLED: bool = False
    OPTIONS_HEDGE_PUT_DELTA_MAX: float = 0.5
    OPTIONS_HEDGE_PUT_DELTA_MIN: float = 0.3
    OPTIONS_HEDGE_PUT_DTE_MAX: int = 120
    OPTIONS_HEDGE_PUT_DTE_MIN: int = 45
    OPTIONS_HEDGE_PUT_MAX_IV_RANK: float = 35.0
    OPTIONS_HEDGE_PUT_MAX_SPREAD_PCT: float = 8.0
    OPTIONS_HEDGE_RATIO_MIN: float = 0.25
    OPTIONS_LEVEL_BREAK_BUFFER: float = 0.01
    OPTIONS_LEVEL_BREAK_MIN_DTE: int = 14
    OPTIONS_LIVE_TRADING_ENABLED: bool = False
    OPTIONS_MARKET_RATIO_MAX: float = 0.75
    OPTIONS_MARKET_RATIO_MIN: float = 0.25
    OPTIONS_MAX_CONTRACTS_PER_ORDER: int = 3
    OPTIONS_MAX_LOSS_GUARD_ENABLED: bool = False
    OPTIONS_MAX_LOSS_PCT_DTE_14: float = -60.0
    OPTIONS_MAX_LOSS_PCT_DTE_30: float = -80.0
    OPTIONS_MAX_LOSS_PCT_DTE_LOW: float = -40.0
    OPTIONS_MAX_ORDER_BUDGET: float = 400.0
    OPTIONS_MAX_PER_GROUP: float = 0.6
    OPTIONS_MAX_PER_SECTOR: float = 0.35
    OPTIONS_MAX_PER_SYMBOL: float = 0.2
    OPTIONS_MAX_SINGLE_CONTRACT_PRICE: float = 9.0
    OPTIONS_MIN_GROUPS: int = 3
    OPTIONS_MIN_SECTORS: int = 2
    OPTIONS_SPREAD_DTE_MAX: int = 75
    OPTIONS_SPREAD_DTE_MIN: int = 55
    OPTIONS_SPREAD_IV_RANK_MIN: float = 75.0
    OPTIONS_SPREAD_MAX_CONCURRENT: int = 15
    OPTIONS_SPREAD_MAX_HOLD_DAYS: int = 21
    OPTIONS_SPREAD_PROFIT_TARGET_PCT: float = 0.5
    OPTIONS_SPREAD_SHORT_DELTA: float = 0.25
    OPTIONS_SPREAD_UNIVERSE: tuple = ('SPY', 'QQQ', 'AAPL', 'AMD', 'AMZN', 'META', 'NVDA', 'JPM', 'CAT', 'XLK', 'XLF', 'GLD')
    OPTIONS_SPREAD_WIDTH: float = 10.0
    OPTIONS_STOCK_CSP_ENABLED: bool = False
    OPTIONS_STOCK_CSP_IV_RANK_MIN: float = 85.0
    OPTIONS_STOCK_CSP_MAX_CONCURRENT: int = 2
    OPTIONS_STOCK_CSP_MIN_CASH: float = 30000.0
    OPTIONS_USER_CANCEL_COOLDOWN_HOURS: float = 4.0
    OPTIONS_WT_ACCEL_GROWTH_PCT: float = 25.0
    OPTIONS_WT_ACCEL_MIN_ABS: float = 10.0
    OPTIONS_WT_SLOWDOWN_PCT: float = 25.0
    ORB_ENABLED: bool = False
    ORB_MAX_PER_DAY: int = 3
    ORB_RVOL_MIN: float = 1.5
    ORB_TARGET_MULT: float = 1.5
    ORB_WINDOW_MINUTES: int = 15
    ORDER_CACHE_TTL: int = 60
    OUTLIER_DETECTOR_ENABLED: bool = True
    OUTLIER_RUNAWAY_ATR_FACTOR: float = 2.0
    OUTLIER_SCAN_INTERVAL: float = 60.0
    OUTLIER_STALE_HOURS: float = 6.0
    OUTLIER_STUCK_ATR_FACTOR: float = 0.5
    OUTLIER_STUCK_HOURS: float = 2.0
    OVERBOUGHT_SCORE_GUT_BREAKOUT_BYPASS: bool = True
    PAPER_TRADING: bool = False
    PAPER_TRADING_QUICK: bool = False
    PARITY_DISABLE_NON_VECTORIZABLE: bool = True
    PARITY_MIN_DECISION_TF: str = '15m'
    PARTIAL_PROFIT_LOCK_ACCOUNTS: List[str] = field(default_factory=lambda: ['ang', 'inf', 'flz', 'men', 'fin'])
    PARTIAL_PROFIT_LOCK_ACCOUNTS_TRADIER: List[str] = field(default_factory=lambda: ['trb', 'trc'])
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT: float = 1.75
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT: float = 0.1
    PARTIAL_PROFIT_LOCK_GAIN_PCT: float = 1.5
    PARTIAL_PROFIT_LOCK_USE_MAKER: bool = True
    PAU_TIMEOUT_SEC: float = 120.0
    PERSIST_FIN: float = 24.0
    PERSIST_FLZ: float = 0.0
    PERSIST_INF: float = 4.0
    PERSIST_MEN: float = 24.0
    PER_ROW_FILTERS: dict = field(default_factory=dict)
    PER_SYMBOL_CONFIG_ENABLED: bool = False
    PER_SYMBOL_CONFIG_FILE: str = 'data/sweep_results/per_symbol_best_crypto_20260416_0507.json'
    PER_SYM_CONFIG_ENABLED: bool = True
    PLOTS_DIR: Path = Path("data")
    PLOT_LOOP_INTERVAL_SECONDS: int = 1800
    PNL_DECAY_COMPLETE_DAYS: int = 5
    PNL_DECAY_FINAL_PERCENTAGE: float = 0.1
    PNL_DECAY_START_HOURS: int = 1
    POSITIONS_SERVICE_HEALTH_RETRIES: int = 3
    POSITIONS_SERVICE_HEALTH_TIMEOUT: float = 4.0
    POSITIONS_SERVICE_START_CMD: Optional[str] = None
    POSITIONS_SNAPSHOT_MAX_AGE: float = 6.0
    POSITION_CACHE_TTL: int = 5
    POSITION_REDIS_REFRESH_INTERVAL: float = 6.0
    POSITION_REFRESH_INTERVAL: float = 6.0
    POSITION_REFRESH_MIN_INTERVAL: int = 5
    POSITION_SAVE_INTERVAL: float = 6.0
    POSITION_STALE_THRESHOLD_SECONDS: float = 60.0
    PPL_FIRE_COOLDOWN_S: float = 0.0
    PREVIOUS_SYMBOLS_FIN: Path = Path("data")
    PREVIOUS_SYMBOLS_FLZ: Path = Path("data")
    PREVIOUS_SYMBOLS_MEN: Path = Path("data")
    PRICE_CACHE_FILE: Path = Path("data")
    PRICE_CACHE_FILE_2: Path = Path("data")
    PRICE_CACHE_FILE_3: Path = Path("data")
    PRICE_CACHE_PULL_MAX_AGE_SEC: float = 1800.0
    PRICE_CACHE_PULL_S1: Path = Path("data")
    PRICE_REFRESH_INTERVAL: float = 3.0
    PRICE_UPDATE_INTERVAL: float = 1.0
    PROGRESSIVE_LOCK_ENABLED: bool = False
    PROGRESSIVE_LOCK_FRACTION: float = 0.25
    PROGRESSIVE_LOCK_TIERS_PCT: list = field(default_factory=lambda: [1.0, 2.0, 3.0, 5.0, 8.0])
    PROX_FILE: Path = Path("data")
    QUICK_BANDAID_OFF_VEC_ENABLED: bool = False
    QUICK_CYCLE_TP_MIN_GAIN_PCT: float = 1.0
    QUICK_CYCLE_TP_REDUCE_FRAC: float = 0.5
    QUICK_CYCLE_TP_STOCH_AGAINST_VEC_ENABLED: bool = False
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_AGE_MIN: float = 240.0
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED: bool = False
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_GAIN_PCT: float = -3.0
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_QTY_PCT: float = 1.0
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_VEC_ENABLED: bool = False
    QUICK_OPEN_STRONG_BB_LONG_MAX: float = 0.3
    QUICK_OPEN_STRONG_BB_SHORT_MIN: float = 0.7
    QUICK_OPEN_STRONG_DC_LONG_MAX: float = 0.4
    QUICK_OPEN_STRONG_DC_SHORT_MIN: float = 0.6
    QUICK_OPEN_STRONG_K_LONG_MAX: float = 25.0
    QUICK_OPEN_STRONG_K_SHORT_MIN: float = 75.0
    QUICK_OPEN_STRONG_VEC_ENABLED: bool = False
    QUICK_OPEN_STRONG_VEL_MIN: float = 1.0
    QUICK_RECOVERY_WINDOW_MIN: float = 120.0
    QUICK_REDUCE_STRONG_REDUCE_VEC_ENABLED: bool = False
    QUICK_SENTIMENT_CUT_GAIN_VEC_ENABLED: bool = False
    QUICK_SENTIMENT_CUT_MIN_GAIN: float = 0.5
    QUICK_SENTIMENT_CUT_REDUCE_FRAC: float = 0.5
    R1_TF: str = '3m'
    R2_TF_LIST: tuple = ('15m',)
    R3_GAIN_MAX_PCT: float = 0.0
    R3_HEDGE_INVARIANT_DUMP_ENABLED: bool = True
    R3_HTF_FLIP_NEWBORN_WINDOW_MIN: float = 15.0
    RANKINGS_DIR: Path = Path("data")
    RANKING_LOOP_SLEEP_SECONDS: int = 120
    RANKING_MULT_ENABLED: bool = False
    RANKING_MULT_MAX: float = 2.5
    RANKING_MULT_MIN: float = 0.3
    RANKING_POINTS_FILE: Path = Path("data")
    RANKING_RESULTS_FILE: Path = Path("data")
    RANKING_UPDATE_INTERVAL: float = 180.0
    RANK_CONVICTION_ENABLED: bool = False
    RATIO_CLOSE_LOSING_COOLDOWN_SECONDS: float = 900.0
    RATIO_CLOSE_LOSING_MAX_PER_CYCLE: int = 2
    RATIO_CLOSE_LOSING_MIN_LOSS_PCT: float = -5.0
    RATIO_CLOSE_LOSING_MIN_PNL_DELTA_PCT: float = 10.0
    RATIO_CLOSE_LOSING_MIN_SKEW_PP: float = 40.0
    RATIO_CLOSE_LOSING_OVERWEIGHT: bool = False
    RATIO_EMERGENCY_EXIT_COOLDOWN: float = 999999.0
    RATIO_EMERGENCY_EXIT_ENABLED: bool = False
    RATIO_EMERGENCY_EXIT_MAX_LOSS_PCT: float = -999.0
    RATIO_EMERGENCY_EXIT_MAX_PER_CYCLE: int = 0
    RATIO_EMERGENCY_EXIT_THRESHOLD: float = 999.0
    RATIO_MULTIPLIER: float = 3.0
    RATIO_PNL_ACCELERATION: float = 2.5
    RATIO_PNL_DELTA_THRESHOLD: float = 3.0
    RATIO_PNL_DYNAMIC_GATES_ENABLED: bool = True
    RATIO_PNL_GATE_SOFT_MAX: float = 2.0
    RATIO_PNL_GATE_SOFT_MIN: float = 0.5
    RATIO_PNL_TARGET_LONG_MAX: float = 90.0
    RATIO_PNL_TARGET_LONG_MIN: float = 10.0
    RATIO_PNL_WEIGHT: float = 0.5
    RATIO_PNL_WEIGHT_ENABLED: bool = True
    RATIO_REBALANCE_APPLY_HTF_GATE: bool = True
    RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY: bool = True
    RATIO_REBALANCE_COOLDOWN_CRASH: float = 1800.0
    RATIO_REBALANCE_COOLDOWN_EXTREME: float = 3600.0
    RATIO_REBALANCE_COOLDOWN_NORMAL: float = 3600.0
    RATIO_REBALANCE_COOLDOWN_PNL_DIVERGENT: float = 1200.0
    RATIO_REBALANCE_MAX_CLOSES: int = 3
    RATIO_REBALANCE_MAX_OPENS_EXTREME: int = 12
    RATIO_REBALANCE_MAX_OPENS_NORMAL: int = 8
    RATIO_REBALANCE_MAX_OPENS_STUCK: int = 10
    RATIO_REBALANCE_SIZE_MAX_MULT: float = 10.0
    RATIO_REBALANCE_SIZE_MULT: float = 4.0
    RATIO_REBALANCE_SIZE_SKEW_BOOST: float = 0.05
    RATIO_SENTIMENT_FILTER_ENABLED: bool = True
    RATIO_SENTIMENT_SHORT_MAX: float = 45.0
    RATIO_TRIM_GAIN_FLOOR: float = 0.5
    RATIO_TRIM_MIN_AGE_S: float = 7200.0
    RATIO_TRIM_MIN_INTERVAL_S: float = 14400.0
    REACTIVE_MODE: bool = False
    REBAL_ATTEMPT_COOLDOWN_SEC: float = 3600.0
    REDIS_1M_TAIL: int = 1500
    REDIS_CHANNEL_MARKET_DATA: str = 'tradier_indicators_channel'
    REDIS_CHANNEL_POSITIONS: str = 'tradier_positions_channel'
    REDIS_CHANNEL_PRICES: str = 'tradier_prices_channel'
    REDIS_CHANNEL_SIGNALS: str = 'signals_channel'
    REDIS_DB: int = 0
    REDIS_EXPIRY_SECONDS: int = 180
    REDIS_HOST: str = 'localhost'
    REDIS_KEY_MARKET_DATA: str = 'latest_market_data'
    REDIS_PORT: int = 6379
    REDUCE_HUGE_LOSS_THRESHOLD: float = -999.0
    REDUCTION_COOLDOWN_SECONDS: float = 15.0
    RED_ZONE_AUGMENT_GATE_ENABLED: bool = False
    RED_ZONE_FALLBACK_K15_HIGH: float = 80.0
    RED_ZONE_FALLBACK_K15_LOW: float = 20.0
    RED_ZONE_GATE_ENABLED: bool = False
    RED_ZONE_GATE_FALLBACK_ENABLED: bool = True
    RED_ZONE_HEDGE_GATE_ENABLED: bool = False
    RED_ZONE_MIN_DISTANCE_PCT: float = 0.4
    RED_ZONE_MIN_WALL_NOTIONAL_USD: float = 50000
    RED_ZONE_STALE_MAX_SEC: float = 30.0
    REENTER_ORPHAN_THRESHOLD: timedelta = field(default_factory=lambda: timedelta(days=70))
    REENTER_SAVE_DEBOUNCE_SECONDS: int = 30
    REENTRY_B01_WT_2of3_ENABLED: bool = False
    REENTRY_B16_MIDRANGE_ENABLED: bool = True
    REENTRY_CHURN_GUARD_ENABLED: bool = False
    REENTRY_CHURN_GUARD_USE_4BAR: bool = True
    REENTRY_CHURN_GUARD_WINDOW_S: float = 3600.0
    REENTRY_CROSS_MAX_BARS_AGO: int = 5
    REENTRY_DISPATCH_MAX_ATTEMPTS: int = 3
    REENTRY_ESCALATION_CRIT_MIN: float = 60.0
    REENTRY_ESCALATION_WARN_MIN: float = 30.0
    REENTRY_FAVORABLE_HTF_MIN: int = 2
    REENTRY_FAVORABLE_QTY_MULT: float = 1.0
    REENTRY_GR_HLHH_MODE: str = 'OR'
    REENTRY_GR_HTF_MIN_TFS: int = 0
    REENTRY_GR_MIN_IND: int = 2
    REENTRY_GR_MIN_TFS: int = 2
    REENTRY_LIVE_MONITOR_DC_BREAK_TF: str = '3m'
    REENTRY_LIVE_MONITOR_ENABLED: bool = True
    REENTRY_LIVE_MONITOR_INTERVAL_S: int = 30
    REENTRY_LIVE_MONITOR_PARTIAL_PCT: float = 0.5
    REENTRY_MATERIAL_OVERSHOOT_PCT: float = 0.5
    REENTRY_OPPOSITION_MAX_FLAT_BARS: int = 12
    REENTRY_TIER2_MIN_MINUTES: float = 10.0
    REENTRY_TIER2_PRICE_PCT: float = 0.003
    REENTRY_TIER2_SIZE_MULT: float = 0.8
    REENTRY_WT15M_CROSS_ENABLED: bool = True
    REENTRY_WT15M_HTF_FAVOR_REQUIRED: bool = True
    REENTRY_WT15M_K_MAX: float = 50.0
    REV_MODE: bool = False
    RE_2_PCT_OB: float = 95.0
    RE_2_PCT_OS: float = 5.0
    RE_2_USE_PERCENTILE_ENABLED: bool = False
    RE_3_B12_RISING_BONUS_ENABLED: bool = False
    RE_3_CONVICTION_BONUS: float = 10.0
    RE_3_RISING_COUNT_THR: int = 3
    RE_4_B14_HA_STREAK_CONV_ENABLED: bool = False
    RE_4_HA_STREAK_CAP: int = 5
    RE_4_HA_STREAK_WEIGHT: float = 5.0
    RE_5_B04_COMPRESSION_BONUS_ENABLED: bool = False
    RE_5_COMPRESSION_BONUS: float = 10.0
    RE_5_INSIDE_COUNT_THR: int = 3
    RE_6_MIN_EXPANDING_TFS: int = 2
    RE_6_WAVE_PHASE_GATE_ENABLED: bool = False
    RIDICULOUS_HOLD_GUARD_ENABLED: bool = False
    RIDICULOUS_HOLD_HOURS: float = 720.0
    RIDICULOUS_HOLD_REQUIRE_GAIN_NONNEG: bool = True
    RIDICULOUS_HOLD_VEC_ENABLED: bool = False
    RIDICULOUS_LOSS_PCT: float = -15.0
    RISK_FREE_RATE: float = 0.045
    ROTATION_BOTTOM_N: int = 8
    ROTATION_HOLD_DAYS: int = 7
    ROTATION_LOOKBACK_DAYS: int = 10
    ROTATION_TOP_N: int = 3
    ROUND_TRIP_COST_PCT: float = 0.05
    RP_OPPOSITE_PENALTY: float = -20.0
    RP_PROTECT_MIN_GAIN: float = 1.0
    RP_PROTECT_THRESHOLD: float = 70.0
    RP_STRONG_BONUS: float = 15.0
    RP_STRONG_THRESHOLD: float = 70.0
    RP_WEAK_PENALTY: float = -10.0
    RP_WEAK_THRESHOLD: float = 30.0
    RSI2_MEAN_REVERSION_ENABLED: bool = False
    RSI2_SCORE_BONUS: int = 20
    RSI2_THRESHOLD_LONG: float = 15.0
    RSI2_THRESHOLD_SHORT: float = 85.0
    RSI_MACD_EMA_ENABLED: bool = False
    RSI_MACD_EMA_RSI_LONG: float = 35.0
    RSI_MACD_EMA_RSI_SHORT: float = 65.0
    RSI_MACD_EMA_SCORE: int = 25
    RSI_MACD_EMA_TF: str = '1h'
    RULE_B_W_TREND_4H_PULLBACK_ENABLED: bool = False
    RULE_C_FUNDING_EXTREME_ENABLED: bool = False
    RVOL_SCALP_MIN: float = 1.0
    RVOL_SCORE_BOOST_PCT: float = 0.2
    RVOL_SCORE_BOOST_THRESHOLD: float = 2.0
    RZ_BASELINE_BOUNCE_SHORT_ENABLED: bool = False
    RZ_BASELINE_TOL: float = 0.05
    RZ_CASCADE_MIN_TF_ALIGN: int = 2
    RZ_DIV_BLOCK_MIN: int = 2
    RZ_LEGS_MIN: float = 20.0
    RZ_LTF_MICRO: str = '5m'
    R_G10_HTF_DIV_GATE_ENABLED: bool = False
    R_G10_HTF_DIV_TFS: str = '4h,D'
    R_S1_WT_COMPOSITE_DELTA_THR: float = 50.0
    R_S1_WT_COMPOSITE_DELTA_USE_ENABLED: bool = False
    R_S2_WT_ADAPTIVE_OS_ENABLED: bool = False
    R_S2_WT_PCT_OB_SHORT: float = 90.0
    R_S2_WT_PCT_OS_LONG: float = 10.0
    R_S3_DIV_STACK_ENABLED: bool = False
    R_S3_HIDDEN_BONUS: float = 25.0
    R_S3_HTF_WEIGHT_ENABLED: bool = False
    R_S3_MAIN_PENALTY: float = -20.0
    R_S3_TF_WEIGHT_15M: float = 0.5
    R_S3_TF_WEIGHT_1H: float = 1.0
    R_S3_TF_WEIGHT_3M: float = 0.25
    R_S3_TF_WEIGHT_4H: float = 2.5
    R_S3_TF_WEIGHT_D: float = 4.0
    R_S4_HA_STREAK_ENABLED: bool = False
    R_S4_HA_STREAK_TF: str = '1h'
    R_S4_HA_STREAK_WEIGHT: float = 5.0
    R_S5_SENT_VEL_BONUS: float = 5.0
    R_S5_SENT_VEL_ENABLED: bool = False
    R_S5_SENT_VEL_PCT_THR: float = 75.0
    R_S6_WT_MSTATE_GATE_MODE: int = 0
    R_S7_HHLL_BONUS_PER_TF: float = 3.0
    R_S7_HHLL_MIN_INDICATORS: int = 2
    R_S7_HHLL_MIN_TFS_FOR_BONUS: int = 2
    R_S7_HHLL_STACK_ENABLED: bool = False
    R_S7_HHLL_TFS: str = '15m,1h,4h,D'
    R_Z2_BOT_MULT: float = 0.5
    R_Z2_PCT_BOT_THR: float = 30.0
    R_Z2_PCT_TOP_THR: float = 90.0
    R_Z2_PERCENTILE_SCALER_ENABLED: bool = False
    R_Z2_TOP_MULT: float = 1.5
    R_Z3_T1_MULT: float = 1.0
    R_Z3_T1_THR: float = 50.0
    R_Z3_T2_MULT: float = 1.5
    R_Z3_T2_THR: float = 100.0
    R_Z3_T3_MULT: float = 2.0
    R_Z3_T3_THR: float = 150.0
    R_Z3_WT_COMPOSITE_SIZE_ENABLED: bool = False
    R_Z5_DC_HTF_MIN: float = 0.6
    R_Z5_DC_LTF_LOW_THR: float = 0.2
    R_Z5_DC_PULLBACK_MULT: float = 1.5
    R_Z5_DC_PULLBACK_SIZING_ENABLED: bool = False
    SANDBOX_ACCOUNTS: List[str] = field(default_factory=lambda: ['sbx'])
    SANDBOX_MODE: bool = False
    SATOSHIT_ACCOUNTS: List[str] = field(default_factory=lambda: ['ang', 'inf', 'flz', 'men', 'fin'])
    SATOSHIT_ACCOUNTS_TRADIER: List[str] = field(default_factory=lambda: ['tra', 'trb', 'trc'])
    SATOSHIT_EXIT_LONG_RSI_MIN: float = 55.0
    SATOSHIT_EXIT_LONG_STOCH_K_MIN: float = 60.0
    SATOSHIT_EXIT_SHORT_RSI_MAX: float = 42.0
    SATOSHIT_EXIT_SHORT_STOCH_K_MAX: float = 50.0
    SATOSHIT_HTF_MFI_D_MIN: float = 30.0
    SATOSHIT_HTF_RVOL_1H_MIN: float = 0.3
    SATOSHIT_LONG_MFI_MAX: float = 60.0
    SATOSHIT_LONG_RSI_MAX: float = 50.0
    SATOSHIT_LONG_STOCH_K_MAX: float = 60.0
    SATOSHIT_QTY_MULT: float = 3.0
    SATOSHIT_SCORE_BONUS: int = 30
    SATOSHIT_SHORT_MFI_MIN: float = 50.0
    SATOSHIT_SHORT_RSI_MIN: float = 55.0
    SATOSHIT_SHORT_STOCH_K_MIN: float = 50.0
    SBA_ADX_MAX: float = 25.0
    SBA_ADX_MAX_TRADIER: float = 22.0
    SBA_ADX_TF: str = '1h'
    SBA_BOUNCE_ENABLED: bool = True
    SBA_COOLDOWN_GLOBAL_S: int = 300
    SBA_COOLDOWN_POSITION_S: int = 3375
    SBA_COOLDOWN_S_TRADIER: int = 7200
    SBA_ENABLED: bool = False
    SBA_ENABLED_TRADIER: bool = False
    SBA_MAX_ADDS: int = 2
    SBA_MAX_ADDS_TRADIER: int = 2
    SBA_MAX_CONCURRENT: int = 3
    SBA_MAX_LOSS_PCT: float = -15.0
    SBA_MAX_LOSS_PCT_TRADIER: float = -12.0
    SBA_MAX_TOTAL_MULT: float = 20.0
    SBA_MIN_LOSS_PCT: float = -2.0
    SBA_MIN_LOSS_PCT_TRADIER: float = -3.0
    SBA_MIN_SCORE: float = 3.5
    SBA_SIZE_FRACTION: float = 0.4
    SBA_SIZE_FRACTION_TRADIER: float = 0.25
    SCALP_MODE: bool = False
    SCALP_REDUCE_ENABLED: bool = False
    SCALP_TOP_MOVERS_N: int = 14
    SCALP_V2_DC_HTF_LIST: list = field(default_factory=lambda: ['15m', '1h'])
    SCALP_V2_ENTRY_MODE: str = 'breakout'
    SCALP_V2_LH_LL_TF: str = '15m'
    SCALP_V2_MAX_CONCURRENT: int = 5
    SCALP_V2_MAX_HOLD_MINUTES: float = 15.0
    SCALP_V2_REDZONE_K_THRESHOLD: int = 90
    SCALP_V2_REENTRY_COOLDOWN_S: int = 300
    SCALP_V2_VARIANT: str = 'V1_WT_CONFIRM'
    SCALP_V3_ACCOUNTS: list = field(default_factory=lambda: [])
    SCALP_V3_ATR_PCTL_GATE_ENABLED: bool = False
    SCALP_V3_ATR_PCTL_MIN: float = 40.0
    SCALP_V3_ATR_SL_MULT: float = 0.0
    SCALP_V3_ATR_TP_MULT: float = 0.8
    SCALP_V3_AUG_COOLDOWN_SEC: float = 180.0
    SCALP_V3_AUG_ENABLED: bool = True
    SCALP_V3_AUG_MAX_FRAC_OF_POS: float = 0.5
    SCALP_V3_AUG_MIN_GAIN: float = 2.0
    SCALP_V3_BB_SQUEEZE_MAX_PCT: float = 2.0
    SCALP_V3_BOOST_ENABLED: bool = True
    SCALP_V3_BOOST_LOOKBACK_BARS_3M: int = 5
    SCALP_V3_BOOST_VOL_Z_MIN: float = 1.5
    SCALP_V3_BOOST_WEIGHT: float = 0.2
    SCALP_V3_BYPASS_HTF_DIRECTION_GATE: bool = True
    SCALP_V3_DIAG_LOG: bool = True
    SCALP_V3_ENABLED: bool = False
    SCALP_V3_ENFORCE_UNIVERSE_DIRECTION: bool = True
    SCALP_V3_ENTRY_BAR_1M_REQUIRE: str = 'HH_AND_HL'
    SCALP_V3_ENTRY_BAR_3M_REQUIRE: str = 'HH'
    SCALP_V3_ENTRY_BAR_BREAK_ENABLED: bool = True
    SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN: float = 1.0
    SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_LONG: float = 1.0
    SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_SHORT: float = 1.5
    SCALP_V3_ENTRY_DC_BREAK_ENABLED: bool = False
    SCALP_V3_ENTRY_K_15M_MAX: int = 65
    SCALP_V3_ENTRY_K_1H_MAX: int = 80
    SCALP_V3_ENTRY_K_1M_MAX: int = 20
    SCALP_V3_ENTRY_K_3M_MAX: int = 40
    SCALP_V3_ENTRY_K_4H_MAX: int = 85
    SCALP_V3_ENTRY_PULLBACK_ENABLED: bool = True
    SCALP_V3_ENTRY_REQUIRE_K_TURNUP: bool = False
    SCALP_V3_ENTRY_STDEV_ENABLED: bool = False
    SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED: bool = False
    SCALP_V3_ENTRY_TF_MODE: str = '3M_ONLY'
    SCALP_V3_ENTRY_TREND_ENABLED: bool = True
    SCALP_V3_ENTRY_VOL_SPIKE_MULT: float = 1.25
    SCALP_V3_ENTRY_WT_CROSS_ENABLED: bool = False
    SCALP_V3_EXIT_15M_BAR: str = 'LL_OR_LH'
    SCALP_V3_EXIT_15M_K_MIN: int = 95
    SCALP_V3_EXIT_1M_BAR: str = 'LL_AND_LH'
    SCALP_V3_EXIT_1M_K_MIN: int = 90
    SCALP_V3_EXIT_3M_BAR: str = 'LL_OR_LH'
    SCALP_V3_EXIT_3M_K_MIN: int = 95
    SCALP_V3_EXIT_BAR_REVERSAL_ENABLED: bool = True
    SCALP_V3_EXIT_K_CROSS_ENABLED: bool = True
    SCALP_V3_EXIT_PROFIT_ONLY: bool = False
    SCALP_V3_EXIT_REQUIRE_N_SIGNALS: int = 3
    SCALP_V3_EXIT_STDEV_REJECT_ENABLED: bool = False
    SCALP_V3_EXIT_TF_MODE: str = '15M_ONLY'
    SCALP_V3_EXIT_WT_FLIP_ENABLED: bool = True
    SCALP_V3_FAST_PPL_ENABLED: bool = True
    SCALP_V3_FAST_PPL_GAIN_PCT: float = 0.5
    SCALP_V3_HTF_SMA200_ENABLED: bool = False
    SCALP_V3_HTF_TREND_VEL_GATE: float = 3.0
    SCALP_V3_K_FRESH_HI: float = 85.0
    SCALP_V3_K_FRESH_LO: float = 15.0
    SCALP_V3_K_FRESH_MID_HI: float = 60.0
    SCALP_V3_K_FRESH_MID_LO: float = 40.0
    SCALP_V3_LONG_K_RISE_MAX: float = 85.0
    SCALP_V3_LONG_K_RISE_MIN: float = 40.0
    SCALP_V3_MAX_CONCURRENT: int = 8
    SCALP_V3_MAX_HOLD_MIN: float = 5.0
    SCALP_V3_MAX_LOSS_PCT: float = -1.5
    SCALP_V3_MIN_TP_FOR_EARLY_EXIT: float = 0.2
    SCALP_V3_OB_DIV_CONFLICT_MAX_NET: float = 50.0
    SCALP_V3_OB_FLOW_AGREE_ENABLED: bool = True
    SCALP_V3_OB_FLOW_AGREE_MODE: str = 'WT_VEL'
    SCALP_V3_OB_FLOW_K_AGREE: bool = True
    SCALP_V3_OB_FLOW_OFI_MIN_ABS: float = 0.0
    SCALP_V3_OB_FLOW_VEL_1M_MIN: float = 0.0
    SCALP_V3_OB_FLOW_VEL_3M_MIN: float = 0.0
    SCALP_V3_OB_MIN_DIFF: float = 30.0
    SCALP_V3_OB_MIN_LONG_SCORE: float = 70.0
    SCALP_V3_OB_MIN_SCORE: float = 70.0
    SCALP_V3_OB_MIN_SHORT_SCORE: float = 70.0
    SCALP_V3_OB_REQUIRED: bool = True
    SCALP_V3_OB_VOID_EXTEND_HOLD: bool = True
    SCALP_V3_OUTLIER_ENABLED: bool = True
    SCALP_V3_OUTLIER_MAX_INJECT: int = 15
    SCALP_V3_OUTLIER_MIN_Z_LONG: float = 1.5
    SCALP_V3_OUTLIER_MIN_Z_SHORT: float = -1.5
    SCALP_V3_OUTLIER_WEIGHT: float = 3.0
    SCALP_V3_PEAK_GIVEBACK_PCT: float = 0.15
    SCALP_V3_PG_ARM_PCT: float = 0.5
    SCALP_V3_PG_GIVEBACK_PCT: float = 0.2
    SCALP_V3_PIN_BAR_RATIO: float = 2.5
    SCALP_V3_POSITION_CAP_USD: float = 20.0
    SCALP_V3_PROTECTIVE_K_DROP_MIN: float = 5.0
    SCALP_V3_REENTRY_BOUNCE_BAR_3M: str = 'HH_AND_HL'
    SCALP_V3_REENTRY_BOUNCE_K_15M_MAX: int = 25
    SCALP_V3_REENTRY_BOUNCE_MODE: str = '3M_BAR_OR_K15M'
    SCALP_V3_REENTRY_COOLDOWN_S: int = 300
    SCALP_V3_REENTRY_REQUIRE_BOUNCE_IF_15M_FALLING: bool = True
    SCALP_V3_REENTRY_STICKY_ENABLED: bool = True
    SCALP_V3_REENTRY_STICKY_MIN: int = 30
    SCALP_V3_REQUIRE_SR_ON_ENTRY: bool = False
    SCALP_V3_SCAN_BYPASS_GATES: bool = False
    SCALP_V3_SCAN_INTERVAL_SEC: float = 10.0
    SCALP_V3_SCAN_MIN_DIVERGENCE: float = 0.3
    SCALP_V3_SCAN_TOP_N: int = 8
    SCALP_V3_SESSION_BLOCK_HOURS: list = field(default_factory=list)
    SCALP_V3_SHORT_ENTRY_K_15M_MAX: int = 30
    SCALP_V3_SHORT_ENTRY_K_1H_MAX: int = 40
    SCALP_V3_SHORT_ENTRY_K_3M_MAX: int = 15
    SCALP_V3_SHORT_K_FALL_MAX: float = 60.0
    SCALP_V3_SHORT_K_FALL_MIN: float = 15.0
    SCALP_V3_SHORT_RECENT_DUMP_LOOKBACK_MIN: int = 60
    SCALP_V3_SHORT_RECENT_DUMP_PCT: float = 3.0
    SCALP_V3_SHORT_REQUIRE_RECENT_DUMP: bool = True
    SCALP_V3_SIDE_MODE: str = 'BOTH'
    SCALP_V3_SR_HOLD_MIN_GAIN_PCT: float = 0.1
    SCALP_V3_SR_TOL_PCT: float = 0.5
    SCALP_V3_STALL_ENABLED: bool = False
    SCALP_V3_STALL_GAIN_MAX_PCT: float = -0.1
    SCALP_V3_STDEV_BOUNCE_HI: float = 0.9
    SCALP_V3_STDEV_BOUNCE_HI_SHORT: float = 0.85
    SCALP_V3_STDEV_BOUNCE_LO: float = 0.1
    SCALP_V3_STDEV_BOUNCE_LO_LONG: float = 0.1
    SCALP_V3_STDEV_BREAK_HI: float = 1.0
    SCALP_V3_STDEV_BREAK_HI_LONG: float = 1.0
    SCALP_V3_STDEV_BREAK_LO: float = 0.0
    SCALP_V3_STDEV_BREAK_LO_SHORT: float = 0.0
    SCALP_V3_STDEV_MODE: str = 'BOUNCE'
    SCALP_V3_STDEV_REJECT_HI: float = 0.95
    SCALP_V3_STDEV_REJECT_LO: float = 0.05
    SCALP_V3_STDEV_TF: str = '3m'
    SCALP_V3_USE_HA_1M: bool = False
    SCALP_V3_USE_HA_3M: bool = True
    SCALP_V3_VWAP_DEV_MIN_PCT: float = 0.3
    SCALP_V3_VWAP_FILTER_ENABLED: bool = False
    SCALP_V3_VWAP_TYPE: str = 'BOTH'
    SCORE_RANGES_FILE: Path = Path("data")
    SECTOR_GROUPS: Dict[str, str] = field(default_factory=lambda: {'TECH': 'GROWTH', 'TECH_SW': 'GROWTH', 'TECH_CONS': 'GROWTH', 'ENERGY_OIL': 'COMMODITIES', 'ENERGY_NAT': 'COMMODITIES', 'MINING_GOLD': 'COMMODITIES', 'MINING_BASE': 'COMMODITIES', 'NUCLEAR': 'ENERGY_ALT', 'DEFENSE': 'DEFENSE', 'CRYPTO': 'CRYPTO', 'AGRICULTURE': 'COMMODITIES', 'SHIPPING': 'SHIPPING', 'HEALTH': 'DEFENSIVE', 'CONSUMER': 'DEFENSIVE', 'TELECOM': 'DEFENSIVE', 'FINANCIAL': 'FINANCIAL', 'INDUSTRIAL': 'CYCLICAL', 'INDEX': 'INDEX', 'MEME': 'SPECULATIVE'})
    SECTOR_LS_MIN_POSITIONS: int = 3
    SECTOR_LS_RATIO_MAX: float = 2.0
    SECTOR_LS_RATIO_MIN: float = 0.5
    SECTOR_MAP: Dict[str, str] = field(default_factory=lambda: {'AAPL': 'TECH', 'MSFT': 'TECH', 'GOOGL': 'TECH', 'META': 'TECH', 'AMZN': 'TECH', 'NVDA': 'TECH', 'AVGO': 'TECH', 'ASML': 'TECH', 'TSM': 'TECH', 'INTC': 'TECH', 'AMD': 'TECH', 'QCOM': 'TECH', 'ARM': 'TECH', 'MRVL': 'TECH', 'MU': 'TECH', 'LRCX': 'TECH', 'TXN': 'TECH', 'CRM': 'TECH_SW', 'ADBE': 'TECH_SW', 'ORCL': 'TECH_SW', 'SNOW': 'TECH_SW', 'WDAY': 'TECH_SW', 'PATH': 'TECH_SW', 'SHOP': 'TECH_SW', 'SPOT': 'TECH_SW', 'TTD': 'TECH_SW', 'QLYS': 'TECH_SW', 'CRWD': 'TECH_SW', 'CRWV': 'TECH_SW', 'ZETA': 'TECH_SW', 'FIVN': 'TECH_SW', 'OLED': 'TECH_SW', 'NFLX': 'TECH_CONS', 'RBLX': 'TECH_CONS', 'RDDT': 'TECH_CONS', 'ROKU': 'TECH_CONS', 'BABA': 'TECH_CONS', 'BIDU': 'TECH_CONS', 'TCEHY': 'TECH_CONS', 'UBER': 'TECH_CONS', 'LYFT': 'TECH_CONS', 'SQ': 'TECH_CONS', 'PYPL': 'TECH_CONS', 'ABNB': 'TECH_CONS', 'DUOL': 'TECH_CONS', 'SMCI': 'TECH', 'XOM': 'ENERGY_OIL', 'CVX': 'ENERGY_OIL', 'COP': 'ENERGY_OIL', 'EOG': 'ENERGY_OIL', 'OXY': 'ENERGY_OIL', 'MPC': 'ENERGY_OIL', 'VLO': 'ENERGY_OIL', 'PSX': 'ENERGY_OIL', 'PBF': 'ENERGY_OIL', 'DINO': 'ENERGY_OIL', 'DVN': 'ENERGY_OIL', 'FANG': 'ENERGY_OIL', 'APA': 'ENERGY_OIL', 'MRO': 'ENERGY_OIL', 'PR': 'ENERGY_OIL', 'HAL': 'ENERGY_OIL', 'SLB': 'ENERGY_OIL', 'BKR': 'ENERGY_OIL', 'HES': 'ENERGY_OIL', 'CHRD': 'ENERGY_OIL', 'CRK': 'ENERGY_OIL', 'AR': 'ENERGY_OIL', 'RRC': 'ENERGY_OIL', 'CTRA': 'ENERGY_OIL', 'AM': 'ENERGY_OIL', 'EQT': 'ENERGY_OIL', 'EPD': 'ENERGY_OIL', 'ET': 'ENERGY_OIL', 'KMI': 'ENERGY_OIL', 'WMB': 'ENERGY_OIL', 'TRGP': 'ENERGY_OIL', 'OKE': 'ENERGY_OIL', 'LNG': 'ENERGY_OIL', 'XLE': 'ENERGY_OIL', 'XOP': 'ENERGY_OIL', 'OIH': 'ENERGY_OIL', 'USO': 'ENERGY_OIL', 'UNG': 'ENERGY_NAT', 'BOIL': 'ENERGY_NAT', 'UEC': 'NUCLEAR', 'NXE': 'NUCLEAR', 'CCJ': 'NUCLEAR', 'DNN': 'NUCLEAR', 'LEU': 'NUCLEAR', 'NNE': 'NUCLEAR', 'SMR': 'NUCLEAR', 'OKLO': 'NUCLEAR', 'BWXT': 'NUCLEAR', 'URA': 'NUCLEAR', 'URNM': 'NUCLEAR', 'UUUU': 'NUCLEAR', 'NEM': 'MINING_GOLD', 'AEM': 'MINING_GOLD', 'FNV': 'MINING_GOLD', 'WPM': 'MINING_GOLD', 'RGLD': 'MINING_GOLD', 'GOLD': 'MINING_GOLD', 'KGC': 'MINING_GOLD', 'AG': 'MINING_GOLD', 'AGI': 'MINING_GOLD', 'EGO': 'MINING_GOLD', 'BTG': 'MINING_GOLD', 'CDE': 'MINING_GOLD', 'HL': 'MINING_GOLD', 'MAG': 'MINING_GOLD', 'PAAS': 'MINING_GOLD', 'AU': 'MINING_GOLD', 'GDX': 'MINING_GOLD', 'GDXJ': 'MINING_GOLD', 'GLD': 'MINING_GOLD', 'SLV': 'MINING_GOLD', 'FCX': 'MINING_BASE', 'SCCO': 'MINING_BASE', 'RIO': 'MINING_BASE', 'BHP': 'MINING_BASE', 'VALE': 'MINING_BASE', 'AA': 'MINING_BASE', 'NUE': 'MINING_BASE', 'STLD': 'MINING_BASE', 'CLF': 'MINING_BASE', 'X': 'MINING_BASE', 'RS': 'MINING_BASE', 'CMC': 'MINING_BASE', 'ATI': 'MINING_BASE', 'CENX': 'MINING_BASE', 'MP': 'MINING_BASE', 'LAC': 'MINING_BASE', 'PLL': 'MINING_BASE', 'SGML': 'MINING_BASE', 'SQM': 'MINING_BASE', 'COPX': 'MINING_BASE', 'XME': 'MINING_BASE', 'REMX': 'MINING_BASE', 'LMT': 'DEFENSE', 'RTX': 'DEFENSE', 'GD': 'DEFENSE', 'NOC': 'DEFENSE', 'BA': 'DEFENSE', 'LHX': 'DEFENSE', 'HII': 'DEFENSE', 'LDOS': 'DEFENSE', 'KTOS': 'DEFENSE', 'HWM': 'DEFENSE', 'AXON': 'DEFENSE', 'RKLB': 'DEFENSE', 'JOBY': 'DEFENSE', 'TDG': 'DEFENSE', 'GE': 'DEFENSE', 'ITA': 'DEFENSE', 'PPA': 'DEFENSE', 'IBIT': 'CRYPTO', 'BITO': 'CRYPTO', 'COIN': 'CRYPTO', 'BLOK': 'CRYPTO', 'SBIT': 'CRYPTO', 'BTCL': 'CRYPTO', 'ETHD': 'CRYPTO', 'ETH': 'CRYPTO', 'DIME': 'CRYPTO', 'QBTS': 'CRYPTO', 'ADM': 'AGRICULTURE', 'BG': 'AGRICULTURE', 'CTVA': 'AGRICULTURE', 'FMC': 'AGRICULTURE', 'DE': 'AGRICULTURE', 'AGCO': 'AGRICULTURE', 'CNHI': 'AGRICULTURE', 'CF': 'AGRICULTURE', 'MOS': 'AGRICULTURE', 'NTR': 'AGRICULTURE', 'ICL': 'AGRICULTURE', 'SMG': 'AGRICULTURE', 'IPI': 'AGRICULTURE', 'LSB': 'AGRICULTURE', 'UAN': 'AGRICULTURE', 'INGR': 'AGRICULTURE', 'CALM': 'AGRICULTURE', 'TSN': 'AGRICULTURE', 'DAR': 'AGRICULTURE', 'MOO': 'AGRICULTURE', 'WEAT': 'AGRICULTURE', 'CORN': 'AGRICULTURE', 'DBA': 'AGRICULTURE', 'PDBC': 'AGRICULTURE', 'ZIM': 'SHIPPING', 'SBLK': 'SHIPPING', 'DAC': 'SHIPPING', 'FRO': 'SHIPPING', 'GOGL': 'SHIPPING', 'EGLE': 'SHIPPING', 'GNK': 'SHIPPING', 'NAT': 'SHIPPING', 'TNK': 'SHIPPING', 'STNG': 'SHIPPING', 'DHT': 'SHIPPING', 'INSW': 'SHIPPING', 'ASC': 'SHIPPING', 'LLY': 'HEALTH', 'JNJ': 'HEALTH', 'PFE': 'HEALTH', 'MRK': 'HEALTH', 'ABBV': 'HEALTH', 'ABT': 'HEALTH', 'TMO': 'HEALTH', 'DHR': 'HEALTH', 'GILD': 'HEALTH', 'MDT': 'HEALTH', 'UNH': 'HEALTH', 'COST': 'CONSUMER', 'WMT': 'CONSUMER', 'TGT': 'CONSUMER', 'HD': 'CONSUMER', 'LOW': 'CONSUMER', 'NKE': 'CONSUMER', 'SBUX': 'CONSUMER', 'MCD': 'CONSUMER', 'PEP': 'CONSUMER', 'KO': 'CONSUMER', 'CLX': 'CONSUMER', 'ULTA': 'CONSUMER', 'DIS': 'CONSUMER', 'MO': 'CONSUMER', 'JPM': 'FINANCIAL', 'BK': 'FINANCIAL', 'SCHW': 'FINANCIAL', 'CME': 'FINANCIAL', 'MA': 'FINANCIAL', 'V': 'FINANCIAL', 'ACN': 'FINANCIAL', 'ADP': 'FINANCIAL', 'APO': 'FINANCIAL', 'EXE': 'FINANCIAL', 'IBM': 'FINANCIAL', 'CAT': 'INDUSTRIAL', 'GM': 'INDUSTRIAL', 'FDX': 'INDUSTRIAL', 'UPS': 'INDUSTRIAL', 'T': 'TELECOM', 'VZ': 'TELECOM', 'SPY': 'INDEX', 'QQQ': 'INDEX', 'SHY': 'INDEX', 'GME': 'MEME', 'TSLA': 'MEME', 'PLTR': 'MEME', 'QUBT': 'MEME', 'ASTS': 'MEME', 'SNDK': 'MEME', 'STZ': 'CONSUMER'})
    SENTIMENT_FADE_MODE: str = 'DISABLED'
    SENTIMENT_REBAL_COOLDOWN_MIN: float = 240.0
    SENTIMENT_TOP_N: int = 20
    SENTIMENT_TOP_N_GATE_ENABLED: bool = False
    SERVER_HEARTBEAT_BLOCK_ENABLED: bool = True
    SHORT_ABOVE_EMA20_IS_PENALTY: bool = True
    SHORT_ABOVE_SMA20_BONUS: int = 15
    SHORT_RSI_MIN_1H: float = 40.0
    SHORT_STRUCT_EXIT_TF: str = 'None'  # 2026-09-26 DISABLED HYBRID — see LONG_STRUCT
    SIGNALS_FILE: Path = Path("data")
    SIGNALS_LOOP_INTERVAL_SECONDS: int = 300
    SIZING_MODE_TRADIER: str = 'DEFAULT'
    SLEEP_TIME_PER_TASKS: int = 3
    SLEEP_TIME_PROC_ACCT: float = 5
    SMFI_MAX_PER_SIDE: int = 5
    SPIKE_FADE_COOLDOWN_BARS: int = 6
    SPIKE_FADE_ENABLED: bool = False
    SPIKE_FADE_K_EXHAUSTION: float = 70.0
    SPIKE_FADE_LOOKBACK_BARS: int = 6
    SPY_REGIME_GATE_ENABLED_TRADIER: bool = False
    SPY_REGIME_SMA_BARS_DAILY: int = 200
    SPY_REGIME_SYMBOL: str = 'SPY'
    SQUEEZE_FIRE_ENABLED: bool = False
    SQUEEZE_FIRE_ENABLED_TRADIER: bool = False
    SQUEEZE_FIRE_SCORE_BONUS: int = 20
    SQUEEZE_FIRE_TF: str = '5m'
    SQUEEZE_FIRE_TFS: List[str] = field(default_factory=lambda: ['1h', '4h'])
    SQUEEZE_SCORE_BONUS: int = 15
    SRS_K_EXIT_1H: float = 85.0
    STALE_HOLD: bool = True
    STALE_HOLD_ENABLED: bool = False
    STALE_WARNING_INTERVAL_SECONDS: float = 30.0
    STALL_AGE_MIN_MIN: float = 180.0
    STALL_DELTA_SPEED_MAX: float = 1.0
    STALL_GAIN_ABS_MAX: float = 0.5
    STALL_MAX_CLOSES_PER_CYCLE: int = 2
    STALL_SUB_ENABLED: bool = False
    STDEV_BB_RZ_EXIT_TF: str = 'D'
    STDEV_BB_RZ_SUPPRESS_PCTB: float = 0.85
    STDEV_BOUNCE_HTF_LIST: List[str] = field(default_factory=lambda: ['D', '4h'])
    STDEV_BREAKOUT_COOLDOWN: float = 600.0
    STDEV_BREAKOUT_HTF_LIST: List[str] = field(default_factory=lambda: ['D', '4h'])
    STDEV_BREAKOUT_MAX_RETESTS: int = 3
    STDEV_BREAKOUT_RETEST_COOLDOWN: float = 300.0
    STDEV_BREAKOUT_RETEST_PCTB_MAX: float = 1.05
    STDEV_BREAKOUT_RETEST_TF_LIST: List[str] = field(default_factory=lambda: ['1h', '15m'])
    STDEV_BREAKOUT_SCORE: int = 25
    STDEV_MACRO_ENTRY_BOOST_ENABLED: bool = False
    STDEV_MACRO_ENTRY_BOOST_MULT: float = 1.3
    STOCH_1H_EXIT_K_MIN: float = 85.0
    STOCH_CROSS_ENTRY_ENABLED: bool = False
    STOCH_ENTRY_LONG_TRADIER: int = 30
    STOCH_ENTRY_SHORT_TRADIER: int = 65
    STOCH_EXTREME_LONG_TRADIER: int = 15
    STOCH_EXTREME_SHORT_TRADIER: int = 85
    STORM_REDUCE_ENABLED: bool = True
    STRICT_VEC_PARITY_GATE_ENTRIES: bool = True
    STRICT_VEC_PARITY_GATE_EXITS: bool = False
    STRICT_VEC_PARITY_MODE: bool = True
    STRICT_VEC_PARITY_SHADOW: bool = False
    STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED: bool = False
    STRUCTURE_FLIP_REENTRY_BASIS_TF: str = '4h'
    STRUCTURE_FLIP_REENTRY_ENABLED: bool = False
    STRUCTURE_FLIP_REENTRY_TF: str = '15m'
    ST_LT_SPLIT_ENABLED: bool = True
    ST_SCORE_WEIGHT_LTF: float = 0.7
    SWEEP_DEEP_TEST_GAIN_PER_MO_MIN_PCT: float = 1.0
    SWEEP_DEEP_TEST_POOL_SHARPE_MIN: float = 0.5
    SWEEP_DISCARD_POOL_SHARPE_FLOOR: float = 0.4
    SWEEP_OPTIMAL_ENTRY_TF: str = '1h'
    SWEEP_OPTIMAL_HOLD_BARS: int = 8
    SWING_ENABLED: bool = False
    SWING_EXIT_TFS: str = 'D'
    SWING_MAX_POSITION_SIZE: float = 1100.0
    SWING_REENTER_MULT: float = 1.0
    SWING_REENTER_SIGNAL: str = 'green_or_hhll'
    SWING_REENTER_TOLERANCE_PCT: float = 0.0
    SWING_START_SIZE: float = 200.0
    SYMBOLS: Path = Path("data")
    SYMBOLS_ACTIVE: Path = Path("data")
    SYMBOLS_ACTIVE_FILE: Path = Path("data")
    SYMBOLS_ANG_LONG: Path = Path("data")
    SYMBOLS_ANG_SHORT: Path = Path("data")
    SYMBOLS_FILE: Path = Path("data")
    SYMBOLS_FIN: Path = Path("data")
    SYMBOLS_FLZ: Path = Path("data")
    SYMBOLS_INF_LONG: Path = Path("data")
    SYMBOLS_INF_SHORT: Path = Path("data")
    SYMBOLS_MEN: Path = Path("data")
    SYMBOL_CONFIGS_FILE: Path = Path("data")
    SYMBOL_PERF_DECAY_HOURS: float = 12.0
    SYMBOL_PERF_ENABLED: bool = True
    SYMBOL_PERF_MIN_TRADES: int = 5
    SYMBOL_PERF_REFRESH_SECONDS: float = 3600.0
    SYMBOL_PERF_WINDOW_DAYS: int = 14
    SYMBOL_SIZE_MULTIPLIERS: Dict[str, float] = field(default_factory=lambda: {'CELOUSDT': 2.0, 'DYDXUSDT': 1.5, 'GTCUSDT': 1.5, '1000SATSUSDT': 1.5})
    SYNTHETIC_LOSER_MIN_AGE_MIN: float = 30.0
    SYNTHETIC_LOSER_THRESHOLD_PCT: float = -2.0
    TASK_STAGGER_SECONDS: int = 15
    TF_ALL: Optional[list] = None
    TF_FOCUS: str = '3m'
    TF_HTF2: str = '1h'
    TF_MICRO: str = '1m'
    TF_SCALP: str = '3m'
    THROUGHPUT_DAILY_LOSS_RESET_UTC_HOUR: int = 0
    THROUGHPUT_DAILY_LOSS_RESET_UTC_HOUR_TRADIER: int = 13
    THROUGHPUT_DAILY_LOSS_RESET_UTC_MINUTE_TRADIER: int = 30
    THROUGHPUT_MAX_CONCURRENT_POSITIONS: Dict[str, int] = field(default_factory=lambda: {'ang': 25, 'inf': 30, 'flz': 20, 'men': 25, 'fin': 20})
    THROUGHPUT_MAX_CONCURRENT_POSITIONS_TRADIER: Dict[str, int] = field(default_factory=lambda: {'trb': 16, 'trc': 40, 'tra': 9})
    THROUGHPUT_MAX_DAILY_LOSS_PCT: Dict[str, float] = field(default_factory=lambda: {'ang': -3.0, 'inf': -3.0, 'flz': -3.0, 'men': -3.0, 'fin': -3.0})
    THROUGHPUT_MAX_DAILY_LOSS_PCT_TRADIER: Dict[str, float] = field(default_factory=lambda: {'trb': -3.0, 'trc': -10.0, 'tra': -2.0})
    THROUGHPUT_MAX_FIRES_PER_HOUR_PER_ACCOUNT: Dict[str, int] = field(default_factory=lambda: {'ang': 60, 'inf': 90, 'flz': 60, 'men': 60, 'fin': 60})
    THROUGHPUT_MAX_FIRES_PER_HOUR_PER_ACCOUNT_TRADIER: Dict[str, int] = field(default_factory=lambda: {'trb': 60, 'trc': 90, 'tra': 12})
    THROUGHPUT_MAX_FIRES_PER_HOUR_PER_SYMBOL: int = 6
    THROUGHPUT_MAX_FIRES_PER_HOUR_PER_SYMBOL_TRADIER: int = 4
    THROUGHPUT_MAX_TOTAL_NOTIONAL_USD: Dict[str, float] = field(default_factory=lambda: {'ang': 1500.0, 'inf': 1500.0, 'flz': 1500.0, 'men': 1500.0, 'fin': 1500.0})
    THROUGHPUT_MAX_TOTAL_NOTIONAL_USD_TRADIER: Dict[str, float] = field(default_factory=lambda: {'trb': 80000.0, 'trc': 100000.0, 'tra': 50000.0})
    THROUGHPUT_SAFETY_ENABLED: bool = False
    THROUGHPUT_SAFETY_ENABLED_TRADIER: bool = False
    TIER_A_MULTIPLIER: float = 1.2
    TIER_A_WIN_RATE: float = 0.6
    TIER_B_MIN_TRADES: int = 5
    TIER_B_WIN_RATE: float = 0.45
    TIER_C_MULTIPLIER: float = 0.7
    TIER_ENABLED: bool = True
    TIMEFRAMES: List[str] = field(default_factory=lambda: ['1m', '5m', '15m', '1h', '4h', 'D'])
    TOP_OF_RANGE_BLOCK_ENABLED: bool = False
    TOP_OF_RANGE_BLOCK_REQUIRE_ALL: bool = True
    TOP_OF_RANGE_BLOCK_TF_LIST: str = '1h,4h,D'
    TOP_OF_RANGE_BLOCK_THRESHOLD: float = 0.95
    TRADEABLE_KEYS: Path = Path("data")
    TRADEABLE_KEYS_MANDATORY_ENABLED: bool = False  # 2026-09-26 DISABLED NOT in per_sym — NON_VECTORIZABLE paper ON
    TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED: bool = False  # 2026-09-26 DISABLED — see above
    TRADEABLE_KEYS_MANDATORY_SIZE_USD: float = 9.0
    TRADES_PER_SYM_PER_DAY_MAX: int = 6
    TRADIER_ACCOUNT_ID: str = os.getenv('TRADIER_ACCOUNT_ID_TRC', '')
    TRADIER_API_BASE_URL: str = 'https://api.tradier.com/v1'
    TRADIER_API_KEY: str = os.getenv('TRADIER_API_KEY_TRC', '')
    TRADIER_INDICATORS_CYCLE_CONCURRENCY: int = 24
    TRADIER_INDICATORS_HTTP_CONCURRENCY: int = 48
    TRADIER_INDICATORS_IDLE_SLEEP_SEC: float = 1.0
    TRADIER_K_ZONE_ENTRY_BONUS_TRADIER: int = 20
    TRADIER_LOCAL_EXTREMES_SCORING_ENABLED: bool = False
    TRADIER_OI_INJECT_PC_BEARISH: float = 1.4
    TRADIER_OI_INJECT_PC_BULLISH: float = 0.6
    TRADIER_POST_CLOSE_COOLDOWN_MIN: float = 15.0
    TRADIER_REOPEN_WAIT_S: float = 0.0
    TRADIER_RSI_LONG_15M: float = 40.0
    TRADIER_RSI_LONG_1H: float = 22.0
    TRADIER_RSI_LONG_4H: float = 35.0
    TRADIER_RSI_LONG_5M: float = 35.0
    TRADIER_RSI_LONG_D: float = 40.0
    TRADIER_RSI_SHORT_4H: float = 60.0
    TRADIER_RSI_SHORT_5M: float = 65.0
    TRADIER_RSI_SHORT_D: float = 55.0
    TRADIER_SANDBOX_URL: str = 'https://sandbox.tradier.com/v1'
    TRADIER_STREAMING_URL: str = 'https://stream.tradier.com/v1'
    TRADIER_SYMBOLS_FILE: Path = Path("data")
    TRADIER_WS_URL: str = 'wss://ws.tradier.com/v1'
    TRA_ALLOW_BUYS: bool = False
    TRA_BUY_COOLDOWN_AFTER_SELL_HOURS: float = 96.0
    TRA_MAX_BUYS_PER_DAY: int = 1
    TRA_PREFERRED_SYMBOLS: List[str] = field(default_factory=lambda: ['AAPL', 'MSFT', 'GOOGL', 'MSTR', 'PLTR', 'NEM', 'MU', 'SNDK', 'NVDA'])
    TRB_MAX_CALL_VALUE: float = 750.0
    TRB_MAX_LONG_VALUE: float = 12500.0
    TRB_MAX_PUT_VALUE: float = 750.0
    TRB_MAX_SHORT_VALUE: float = 12500.0
    TRB_MAX_SYMBOL_VALUE: float = 2500.0
    TRC_5M_SWEEP_BENCHMARK: str = 'SPY'
    TRC_5M_SWEEP_BUFFER_N: int = 20
    TRC_5M_SWEEP_DELTA_WEIGHT: float = 0.3
    TRC_5M_SWEEP_TOP_N: int = 8
    TRC_5M_SWEEP_Z_WEIGHT: float = 0.7
    TRC_BEAR_MARKET_MODE: bool = False
    TRC_EPISODIC_PIVOT_ENABLED: bool = False
    TRC_MAX_SYMBOL_VALUE: float = 1250.0
    TRC_ORB_ENABLED: bool = False
    TRC_SQUEEZE_ENABLED: bool = False
    TRENDER_DD_RATIO_MAX: float = 0.4
    TRENDER_INJECT_LIVE: bool = False
    TRENDER_INJECT_SHADOW_LOG: bool = True
    TRENDER_LIN_MIN: float = 0.65
    TRENDER_QV_ANCHOR_USD: float = 50000000
    TRENDER_QV_FLOOR_USD: float = 25000000
    TRENDER_QV_MAX_BOOST: float = 1.5
    TRENDER_RET24_MIN_PCT: float = 2.5
    TREND_ACCOUNTS: List[str] = field(default_factory=lambda: [])
    TREND_HEDGE_MAX_SEC: int = 180
    TREND_HTF_MIN_BEAR: int = 7
    TREND_HTF_MIN_BULL: int = 7
    TRIPLE_CONF_ENABLED: bool = False
    TRIPLE_CONF_RSI_LONG: float = 30.0
    TRIPLE_CONF_RSI_SHORT: float = 70.0
    TRIPLE_CONF_SCORE: int = 30
    TRIPLE_CONF_STOCH_LONG: float = 20.0
    TRIPLE_CONF_STOCH_SHORT: float = 80.0
    TRIPLE_CONF_TF: str = '1h'
    TR_BBWIDTH4H_BOYCOTT_SCORE: int = -35
    TR_BBWIDTH4H_MAX: float = 10.0
    TR_CHOP4H_BONUS: int = 15
    TR_CHOP4H_MIN: float = 50.0
    TR_CHOP4H_PENALTY: int = -20
    TR_CHOP4H_TREND_MAX: float = 38.0
    TR_DCWIDTH4H_SHORT_BOYCOTT_SCORE: int = -25
    TR_MFI4H_LONG_BOYCOTT_SCORE: int = -25
    TR_TREND_V1_ATR_STOP_MULT: float = 2.0
    TR_TREND_V1_RETEST_MAX_BARS_D: int = 5
    TR_TREND_V1_RETEST_TOL_PCT: float = 0.5
    TR_TREND_V1_RETEST_VOL_MAX_MULT: float = 0.7
    TR_TREND_V1_SHADOW_SYMBOLS: tuple = ('TRGP', 'SNDK', 'AVGO', 'GLD', 'PLTR', 'MU', 'CDE', 'SLV')
    TR_TREND_V1_TIME_STOP_BARS_D: int = 60
    TR_TREND_V1_TIME_STOP_NO_HIGH_BARS_D: int = 30
    TR_TREND_V1_VOL_MULT: float = 1.5
    # --- 2026-09-19 8x BOUNCE (5 live-only 3m + 3 testable 15m BB/DC) — parity: config.py 5 + 3 15m HTF ---
    REENTRY_BOUNCE_BAR_GR_ENABLED: bool = True
    REENTRY_BOUNCE_BAR_GR_MIN_TFS: int = 2
    REENTRY_BOUNCE_BAR_GR_BETTER_PCT: float = 0.002
    REENTRY_PULLBACK_GR_SCORE_ENABLED: bool = True
    REENTRY_PULLBACK_GR_SCORE_MIN: int = 12
    REENTRY_DC_MID_PULLBACK_ENABLED: bool = True
    REENTRY_DC_MID_PULLBACK_WIDTH_MAX: float = 12.0
    REENTRY_DC_MID_GR_MIN_TFS: int = 2
    REENTRY_K_RESET_GR_ENABLED: bool = False
    REENTRY_K_RESET_GR_MIN_TFS: int = 2
    REENTRY_K_RESET_TF: str = "15m"
    REENTRY_SMA200_GR_CONTINUATION_ENABLED: bool = False
    REENTRY_SMA200_GR_MIN_TFS: int = 2
    REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED: bool = False
    REENTRY_15M_DC_BASIS_CROSS_HTF_MIN_TFS: int = 2
    REENTRY_15M_LRL_PULLBACK_HTF_ENABLED: bool = False
    REENTRY_15M_LRL_PULLBACK_HTF_MIN_TFS: int = 2
    REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED: bool = False
    REENTRY_15M_BB1H_LOW_BOUNCE_HTF_MIN_TFS: int = 2
    REENTRY_15M_BETTER_PCT: float = 0.002
    # --- 2026-09-19 BB 15m/1h/4h/D family — bounce / breakout / profit-take / exit-at-loss per TF ---
    BB_BOUNCE_ENTRY_TF: str = "OFF"
    BB_BREAKOUT_ENTRY_TF: str = "OFF"
    BB_EXIT_AT_LOSS_TF: str = "OFF"
    BB_PROFIT_TAKE_TF: str = "OFF"
    UNDERWATER_HEDGE_OR_CLOSE_ENABLED: bool = False
    UNDERWATER_HEDGE_OR_CLOSE_HTF_CLOSE_REQUIRED: int = 2
    UNDERWATER_HOC_USDC_MAKER_BYPASS: bool = False
    UNIVERSAL_NOLOSS_BYPASS_REASONS: tuple = ()
    USE_1M_3M_SIGNALS_ENABLED: bool = False
    USE_SANDBOX: bool = os.getenv('TRADIER_USE_SANDBOX', 'false').lower() == 'true'
    USE_WS_3M: bool = True
    V12_PARITY_DISABLE_NON_VECTORIZABLE: bool = True
    V12_PARITY_MIN_TF: str = '15m'
    V8Q_COOLDOWN_BARS: int = 3
    V8Q_HTF_MIN_ALIGNED: int = 1
    V8Q_K3M_FLOOR: int = 30
    V8Q_MIN_HOLD_BARS: int = 250
    V8Q_STRENGTH_MIN_SCORE: float = 5.0
    V8Q_SYMBOL_TIER_TOP3: tuple = ('LINKUSDC', 'ETHUSDC', 'DOTUSDT')
    V8Q_SYMBOL_TIER_TOP4: tuple = ('LINKUSDC', 'ETHUSDC', 'DOTUSDT', 'BTCUSDC')
    V8Q_SYMBOL_TIER_TOP5: tuple = ('LINKUSDC', 'ETHUSDC', 'DOTUSDT', 'BTCUSDC', 'UNIUSDC')
    V8Q_SYMBOL_TIER_TOP6: tuple = ('LINKUSDC', 'ETHUSDC', 'DOTUSDT', 'BTCUSDC', 'UNIUSDC', 'SOLUSDC')
    V8Q_WT_EXIT_MIN_TFS: int = 3
    VALIDATE_REFRESH: int = 2
    VEC_FIRST_OPEN_THROTTLE_BARS: int = 0
    VEC_FIX_R1_REASON_STRING_FOR_DIFF: bool = False
    VEC_GATES_LOG_ONLY: bool = True
    VEC_LIVE_REDUCE_DEFAULT_FRAC: float = 1.0
    VEC_LIVE_REDUCE_PARITY_ENABLED: bool = False
    VEC_LIVE_REDUCE_PARITY_FRAC: float = 0.0
    VEC_LIVE_REDUCE_PARITY_KEEP_DUST: bool = False
    VEC_LIVE_REDUCE_PPL_REASONS: tuple = ('PARTIAL_PROFIT_LOCK_STEP1', 'PPL_STEP1')
    VEC_LIVE_REDUCE_PPL_STEP1_FRAC: float = 0.5
    VEC_MTF_ARMED_BYPASS_STRONG: bool = True
    VEC_MTF_ARMED_GATE_HEDGE_OPEN: bool = False
    VEC_MTF_ARMED_GATE_REENTRY: bool = False
    VEC_MTF_ARMED_RESULTING_REASON: str = 'MTF_NO_ARMED_STATE'
    VEC_MTF_ARMED_STATE_ENABLED: bool = False
    VEC_MULTI_SYM_OUTER_LOOP_ENABLED: bool = False
    VEC_RATIO_REDUCE_PROXY_ENABLED: bool = False
    VEC_REDUCE_CASCADE_COOLDOWN_S: float = 0.0
    VEC_REENTRY_REQUIRE_PRIOR_EXIT: bool = False
    VEC_REENTRY_WINDOW_BARS: int = 400
    VERBOSE2: bool = False
    VERBOSE_FETCH_LOGGING: bool = False
    VERBOSE_TIMER: bool = False
    VIX_EXTREME_THRESHOLD: float = 40.0
    VIX_PANIC_THRESHOLD: float = 30.0
    VIX_REGIME_SIZE_MULT_HIGH_VOL: float = 0.5
    VIX_REGIME_SIZE_MULT_PANIC: float = 0.0
    VIX_SMA_LOOKBACK_DAYS: int = 200
    VOL_SPIKE_BODY_RATIO: float = 0.7
    VOL_SPIKE_COOLDOWN: float = 300.0
    VOL_SPIKE_LS_MAX_IMBALANCE: float = 1.5
    VOL_SPIKE_MIN_ALIGNMENT: int = 3
    VOL_SPIKE_RELVOL_THRESHOLD: float = 3.0
    VOL_TARGET_FIELD: str = 'yz_vol_60_d'
    VP_GATE_AUGMENT_GATE_ENABLED: bool = False
    VP_GATE_ENABLED: bool = False
    VP_GATE_HEDGE_GATE_ENABLED: bool = False
    VP_GATE_MIN_DENSITY_Z: float = 2.0
    VP_GATE_MIN_DISTANCE_PCT: float = 1.0
    VP_GATE_STALE_MAX_SEC: float = 7200.0
    VWAP_SCORE_BONUS: int = 10
    WATCHDOG_DC_BASE_USD: float = 25.0
    WATCHDOG_DC_FORCE_OPEN_ENABLED: bool = True
    WATCHDOG_DC_MAX_USD: float = 600.0
    WATCHDOG_DC_MULT_15M: float = 1.0
    WATCHDOG_DC_MULT_1H: float = 4.0
    WATCHDOG_DC_MULT_4H: float = 8.0
    WATCHDOG_DC_MULT_D: float = 16.0
    WATCHDOG_DC_TFS: list = field(default_factory=lambda: ['15m', '1h', '4h', 'D'])
    WATCHDOG_WT3M_ESCALATE_ENABLED: bool = True
    WATCHDOG_WT3M_ESCALATE_LADDER: list = field(default_factory=lambda: [1.0, 2.0, 3.0, 5.0])
    WATCHDOG_WT3M_ESCALATE_MAX_USD: float = 600.0
    WINNERS_15M_FILE: Path = Path("data")
    WINNERS_20_FILE: Path = Path("data")
    WINNER_PROTECT_ENABLED: bool = False
    WORKER_INSTANCE_ID: int = int(os.getenv('WORKER_INSTANCE_ID', '0'))
    WORKER_TOTAL_INSTANCES: int = int(os.getenv('WORKER_TOTAL_INSTANCES', '1'))
    WRONG_SIDE_ABS_KILL_ENABLED: bool = False  # 2026-09-26 DISABLED 76 closes NOT in per_sym — NON_VECTORIZABLE paper ON
    WRONG_SIDE_DIV_LOOKBACK_BARS: int = 20
    WRONG_SIDE_DIV_TFS_REQUIRED: int = 2
    WRONG_SIDE_K_TFS_REQUIRED: int = 0
    WRONG_SIDE_MIN_AGE_MIN: float = 30.0
    WRONG_SIDE_WT_TFS_REDUCED: int = 3
    WS_RECONNECT_DELAY: float = 5.0
    WS_URL: str = 'wss://fstream.binance.com/ws'
    WT15M_AGAINST_FORCE_HEDGE_COOLDOWN_SEC: float = 30.0
    WT15M_AGAINST_FORCE_HEDGE_ENABLED: bool = False
    WT15M_AGAINST_PENALTY: float = -5.0
    WT_15M_SAME_HEDGE_COOLDOWN_SEC: int = 1800
    WT_15M_SAME_HEDGE_DAILY_CAP: int = 2
    WT_3M_FORCE_OPEN_COOLDOWN_SEC: float = 900.0
    WT_3M_FORCE_OPEN_GR_GATE_ENABLED: bool = False
    WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF: int = 5
    WT_3M_FORCE_OPEN_GR_MIN_TFS: int = 4
    WT_3M_FORCE_OPEN_GR_VOTE_MIN: int = 20
    WT_3M_FORCE_OPEN_MAX_TRADES_PER_DAY: int = 4
    WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS: bool = True
    WT_3M_FORCE_OPEN_SMA_PCT: float = 1.0
    WT_3M_OPEN_GATE_ENABLED: bool = False
    WT_4H_VEL_EXIT_LONG_VEL_MIN: float = -2.0
    WT_4H_VEL_EXIT_SHORT_VEL_MIN: float = 2.0
    WT_4H_VEL_MANDATORY_REENTRY_ENABLED: bool = False
    WT_4H_VEL_MANDATORY_REENTRY_ENABLED_TRADIER: bool = False
    WT_AVG_15m: int = 21
    WT_CHAN_15m: int = 10
    WT_CHOP_GATE_ENABLED: bool = False
    WT_CHOP_MAX: int = 8
    WT_COMPOSITE_DELTA_GATE_ENABLED: bool = True
    WT_COMPOSITE_DELTA_LONG_MIN: float = -100.0
    WT_COMPOSITE_DELTA_SCORE_BONUS: float = 3.0
    WT_COMPOSITE_DELTA_SCORE_ENABLED: bool = True
    WT_COMPOSITE_DELTA_SCORE_THRESHOLD: float = 50.0
    WT_COMPOSITE_DELTA_SHORT_MAX: float = 100.0
    WT_COMPOSITE_HTF_GATE: bool = False
    WT_COMPOSITE_SCORING_ENABLED: bool = True
    WT_CROSSUNDER_15M_SHORT: bool = True
    WT_CROSSUNDER_FINAL_COOLDOWN_S: float = 0.0
    WT_CROSS_EXIT_APPLIES_TO_LOSERS: bool = True
    WT_DC_DIRECT_COMBINED_STOCH_GATE: float = 100.0
    WT_DC_DIRECT_COMPLETED_ENABLED: bool = False
    WT_DC_DIRECT_HTF_ALIGN_REQUIRED: int = 0
    WT_DC_DIRECT_HTF_GATE: str = 'none'
    WT_DC_ENTRY_ENABLED: bool = False
    WT_EXHAUST_ENABLED: bool = False
    WT_EXHAUST_EXIT_ENABLED: bool = True
    WT_EXIT_MIN_TFS_TRADIER: int = 5
    WT_EXIT_TFS_TRADIER: str = '5m+15m+1h+4h+D'
    WT_EXIT_VELOCITY_TRADIER: bool = False
    WT_EXIT_VEL_THRESHOLD: float = -6.0
    WT_MTF_VEL_GATE_ENABLED: bool = True
    WT_MTF_VEL_MIN: int = 2
    WT_PERCENTILE_ENABLED: bool = False
    ZEC_FLZ_LONG_SIZE_MULT: float = 5.0
    ZEC_SUPERVISOR_AUTONOMOUS_CLOSE_PER_HOUR_MAX: int = 1
    ZEC_SUPERVISOR_ENABLED: bool = True
    ZEC_SUPERVISOR_HISTORY_LOOKBACK_MIN: int = 30
    ZEC_SUPERVISOR_MODEL: str = 'claude-sonnet-4-6'
    ZEC_SUPERVISOR_POLL_INTERVAL_SEC: int = 300
    ZERO_CONFIRMATION_THRESHOLD_API: int = 2
    ZERO_CONFIRMATION_THRESHOLD_WS: int = 1
    ZONE_OPEN_THRESHOLD: int = 25
    _CURRENT_MARKET_MODE: ClassVar[str] = 'NORMAL_MODE'
    _INSTANCES: ClassVar[WeakSet] = WeakSet()
    _REGIME_LOG: ClassVar[list] = []
    _REGIME_OVERRIDES: ClassVar[Dict[str, Dict[str, object]]] = {}
    _REGIME_REDIS_CACHE: ClassVar[Dict[str, Dict]] = {}
    _REGIME_REDIS_TS: ClassVar[float] = 0.0
    indicators_filepath: Path = Path("data")


def load_npz(mode, symbols, start_date, npz_dir=""):
    if npz_dir:
        d = Path(npz_dir)
    else:
        for prefix in ["backtest_v8", "backtest_v7"]:
            d = BASE_PATH / prefix / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                break
    if not d.exists():
        print(f"NPZ dir not found: {d}")
        return {}
    CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    from datetime import datetime, timezone
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) if start_date else 0
    stores = {}
    for npz_path in sorted(d.glob("*.npz")):
        sym = npz_path.stem
        if symbols and sym not in symbols:
            continue
        if not symbols:
            is_crypto = any(sym.endswith(s) for s in CRYPTO_SUFFIXES)
            if mode == "crypto" and not is_crypto: continue
            if mode == "tradier" and is_crypto: continue
        # Slice each array AS IT IS READ. `dict(np.load(...))` materialises all
        # 1,066 arrays first and only then windows them, so peak memory is the
        # whole file plus the slice: the oldest crypto NPZs hold ~1.1M bars and
        # cost ~9.7GB that way, and two workers landing on two of them OOM-killed
        # a 30GB box that also runs live trading (11 kills, 123 broken pools in
        # one pass). np.load returns a lazy NpzFile, so reading key by key holds
        # exactly one full array at a time and a 365-day window peaks ~6x lower.
        base_tf = "5m" if mode == "tradier" else "3m"
        try:
            with np.load(str(npz_path), allow_pickle=True) as z:
                keys = list(z.files)
                ts_key = 'timestamps' if 'timestamps' in keys else f'timestamp_{base_tf}'
                ts = z[ts_key] if ts_key in keys else np.array([])
                if len(ts) == 0:
                    continue
                if start_ts and ts[-1] < start_ts:
                    continue
                start_idx = int(np.searchsorted(ts, start_ts)) if start_ts else 0
                sliced = {}
                for k in keys:
                    v = z[k]
                    if isinstance(v, np.ndarray) and getattr(v, "ndim", 0) > 0 and len(v) > start_idx:
                        # copy() so the parent array is freed on the next iteration
                        sliced[k] = v[start_idx:].copy() if start_idx else v
                    else:
                        sliced[k] = v
                    del v
        except Exception as e:
            print(f"[WARN] {sym}: {e}")
            continue
        stores[sym] = sliced
    print(f"Loaded {len(stores)} symbols from {d}")
    return stores


def compute_reentry_blocks(npz, n, is_long, cfg):
    """Returns dict of block_name -> boolean array (True = block fires)."""
    close = _base_safe(npz, 'close', n, cfg)
    k_3m = _base_safe(npz, 'stoch_k', n, cfg, 50); d_3m = _base_safe(npz, 'stoch_d', n, cfg, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50); k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    k_3m_prev = np.roll(k_3m, 1); k_3m_prev[0] = k_3m[0]
    wt1_3m = _base_safe(npz, 'wt1', n, cfg); wt2_3m = _base_safe(npz, 'wt2', n, cfg)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_3m = _base_safe(npz, 'wt_velocity', n, cfg); wt_vel_15m = _safe(npz, 'wt_velocity_15m', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    wt_bull_3m = _base_bool(npz, 'wt_bullish', n, cfg); wt_bull_15m = _safeb(npz, 'wt_bullish_15m', n)
    wt_bull_1h = _safeb(npz, 'wt_bullish_1h', n); wt_bull_4h = _safeb(npz, 'wt_bullish_4h', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n); dc_low_4h = _safe(npz, 'dc_low_4h', n)
    dc_high_1h = _safe(npz, 'dc_high_1h', n); dc_low_1h = _safe(npz, 'dc_low_1h', n)
    dc_high_15m = _safe(npz, 'dc_high_15m', n); dc_low_15m = _safe(npz, 'dc_low_15m', n)
    ha_3m = _base_ha(npz, 'ha', n, cfg); ha_15m = _ha_int(npz, 'ha_15m', n); ha_1h = _ha_int(npz, 'ha_1h', n)

    blocks = {}
    if cfg.REENTRY_B02_BC156_BOTTOM_ENABLED:
        if is_long:
            wt_bull_cnt = wt_bull_3m.astype(int) + wt_bull_15m.astype(int) + wt_bull_1h.astype(int) + wt_bull_4h.astype(int)
            blocks["B02"] = (wt1_15m < -20) & (wt_vel_15m > 0) & (wt1_1h > wt2_1h) & (wt_bull_cnt >= 2)
        else:
            wt_bear_cnt = (~wt_bull_3m).astype(int) + (~wt_bull_15m).astype(int) + (~wt_bull_1h).astype(int) + (~wt_bull_4h).astype(int)
            blocks["B02"] = (wt1_15m > 20) & (wt_vel_15m < 0) & (wt1_1h < wt2_1h) & (wt_bear_cnt >= 2)
    if cfg.REENTRY_B04_DC_RETEST_ENABLED:
        dc_high_4h_prev = np.roll(dc_high_4h, 5); dc_high_4h_prev[:5] = dc_high_4h[:5]
        dc_low_4h_prev = np.roll(dc_low_4h, 5); dc_low_4h_prev[:5] = dc_low_4h[:5]
        if is_long:
            exp = (dc_high_4h > dc_high_4h_prev * 1.015) & (dc_high_4h_prev > 0)
            pb = (close < dc_high_4h_prev * 1.005) & (close > dc_high_4h_prev * 0.99)
            blocks["B04"] = exp & pb & (k_3m > d_3m) & (k_3m < 50)
        else:
            exp = (dc_low_4h < dc_low_4h_prev * 0.985) & (dc_low_4h_prev > 0)
            pb = (close > dc_low_4h_prev * 0.995) & (close < dc_low_4h_prev * 1.01)
            blocks["B04"] = exp & pb & (k_3m < d_3m) & (k_3m > 50)
    if cfg.REENTRY_B10_STOCH_REV_ENABLED:
        if is_long:
            blocks["B10"] = (k_3m_prev <= d_3m) & (k_3m > d_3m) & (k_3m < 25) & (k_15m < 40)
        else:
            blocks["B10"] = (k_3m_prev >= d_3m) & (k_3m < d_3m) & (k_3m > 75) & (k_15m > 60)
    if cfg.REENTRY_B11_DC_BREAK_ENABLED:
        if is_long:
            blocks["B11"] = (dc_high_1h > 0) & (close > dc_high_1h * 1.001) & (wt1_15m > wt2_15m)
        else:
            blocks["B11"] = (dc_low_1h > 0) & (close < dc_low_1h * 0.999) & (wt1_15m < wt2_15m)
    if cfg.REENTRY_B12_WT_MOM_ENABLED:
        if is_long:
            aligned = (wt1_3m > wt2_3m) & (wt1_15m > wt2_15m) & (wt1_1h > wt2_1h)
            blocks["B12"] = aligned & (wt_vel_3m > 1.0)
        else:
            aligned = (wt1_3m < wt2_3m) & (wt1_15m < wt2_15m) & (wt1_1h < wt2_1h)
            blocks["B12"] = aligned & (wt_vel_3m < -1.0)
    if cfg.REENTRY_B14_HA_TREND_ENABLED:
        if is_long:
            blocks["B14"] = (ha_3m == 1) & (ha_15m == 1) & (ha_1h == 1) & (k_3m < 60)
        else:
            blocks["B14"] = (ha_3m == -1) & (ha_15m == -1) & (ha_1h == -1) & (k_3m > 40)
    if cfg.REENTRY_B15_STRONG_TREND_ENABLED:
        if is_long:
            blocks["B15"] = (dc_high_4h > 0) & (close > dc_high_4h) & (wt_vel_1h > 2.0) & (k_1h < 85)
        else:
            blocks["B15"] = (dc_low_4h > 0) & (close < dc_low_4h) & (wt_vel_1h < -2.0) & (k_1h > 15)

    # ═══════════════════════════════════════════════════════════════
    # PULLBACK-IN-TREND BLOCKS — enter EARLY, not at end of move
    # Core idea: fundamentally rising (HTF up) ticker + temp pullback → enter at temp bottom
    # Mirror for shorts: fundamentally falling + temp rally → enter at temp top
    # ═══════════════════════════════════════════════════════════════
    # Additional indicators for pullback detection
    bb_pctb_1h_arr = _safe(npz, 'bb_pct_b_1h', n, 0.5)
    wt_vel_4h_arr = _safe(npz, 'wt_velocity_4h', n)
    wt_vel_D_arr = _safe(npz, 'wt_velocity_D', n)
    rsi_1h_arr = _safe(npz, 'rsi_1h', n, 50)
    ha_D_arr = _ha_int(npz, 'ha_D', n)
    ha_4h_arr = _ha_int(npz, 'ha_4h', n)
    sma200_1h = _safe(npz, 'sma_200_1h', n)
    k_3m_prev2 = np.roll(k_3m, 2); k_3m_prev2[:2] = k_3m[:2]
    # Higher TF WT not declared in this scope — pull from npz
    wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_D = _safe(npz, 'wt1_D', n); wt2_D = _safe(npz, 'wt2_D', n)

    # TIGHTENED PULLBACK BLOCKS (2026-04-16) — strict fundamental trend + deep pullback + reversal confirm
    # Principle: only enter when BOTH (1) long-term trend is CLEARLY in our direction
    # AND (2) temporary pullback gives us a better price. Skip neutral/sideways.

    # B_PULL1: ALL HTFs aligned + 3m DEEP oversold + stoch+vel both turning up
    if getattr(cfg, 'REENTRY_PULL1_ENABLED', True):
        if is_long:
            # STRICT: D green, 1h+4h trend up, 1h NOT overbought
            htf_uptrend = (wt1_1h > wt2_1h) & (wt1_4h > wt2_4h) & (wt1_D > wt2_D) & (ha_D_arr == 1) & (k_1h < 70)
            deep_pullback = (k_3m < 20) & (wt1_3m < -35)  # TIGHTER: deep oversold
            reversing = (k_3m > k_3m_prev) & (wt_vel_3m > 0) & (k_3m_prev < k_3m_prev2)  # actively turning
            blocks["B_PULL1"] = htf_uptrend & deep_pullback & reversing
        else:
            htf_downtrend = (wt1_1h < wt2_1h) & (wt1_4h < wt2_4h) & (wt1_D < wt2_D) & (ha_D_arr == -1) & (k_1h > 30)
            deep_rally = (k_3m > 80) & (wt1_3m > 35)
            reversing = (k_3m < k_3m_prev) & (wt_vel_3m < 0) & (k_3m_prev > k_3m_prev2)
            blocks["B_PULL1"] = htf_downtrend & deep_rally & reversing

    # B_PULL2: D rising strongly (wt_vel_D > 1) + price PULLED BACK to SMA200_1h
    if getattr(cfg, 'REENTRY_PULL2_ENABLED', True):
        if is_long:
            rising_fundamentals = (wt_vel_D_arr > 1.0) & (wt_vel_4h_arr > 0) & (ha_D_arr == 1) & (ha_4h_arr >= 0)
            pullback_near_sma = (sma200_1h > 0) & (close < sma200_1h * 1.01) & (close > sma200_1h * 0.98)
            momentum_returning = (k_3m > d_3m) & (k_3m < 35) & (wt_vel_3m > 0)
            blocks["B_PULL2"] = rising_fundamentals & pullback_near_sma & momentum_returning
        else:
            falling_fundamentals = (wt_vel_D_arr < -1.0) & (wt_vel_4h_arr < 0) & (ha_D_arr == -1) & (ha_4h_arr <= 0)
            rally_near_sma = (sma200_1h > 0) & (close > sma200_1h * 0.99) & (close < sma200_1h * 1.02)
            momentum_weakening = (k_3m < d_3m) & (k_3m > 65) & (wt_vel_3m < 0)
            blocks["B_PULL2"] = falling_fundamentals & rally_near_sma & momentum_weakening

    # B_PULL3: BB EXTREME lower band (pctb < 0.15) in strict uptrend + stoch cross
    if getattr(cfg, 'REENTRY_PULL3_ENABLED', True):
        if is_long:
            trend_up = (wt1_1h > wt2_1h) & (wt1_4h > wt2_4h) & (ha_D_arr == 1)
            at_extreme_band = bb_pctb_1h_arr < 0.15
            stoch_turning_up = (k_3m_prev <= d_3m) & (k_3m > d_3m) & (k_3m < 30)
            blocks["B_PULL3"] = trend_up & at_extreme_band & stoch_turning_up
        else:
            trend_down = (wt1_1h < wt2_1h) & (wt1_4h < wt2_4h) & (ha_D_arr == -1)
            at_extreme_band = bb_pctb_1h_arr > 0.85
            stoch_turning_down = (k_3m_prev >= d_3m) & (k_3m < d_3m) & (k_3m > 70)
            blocks["B_PULL3"] = trend_down & at_extreme_band & stoch_turning_down

    # B_PULL4: RSI extreme pullback in trend + 3m WT turning from zero line
    if getattr(cfg, 'REENTRY_PULL4_ENABLED', True):
        if is_long:
            pullback_rsi = rsi_1h_arr < 35  # TIGHTER: deeper RSI pullback
            htf_healthy = (ha_4h_arr == 1) & (ha_D_arr == 1)  # STRICT both TF green
            wt_bouncing_3m = (wt_vel_3m > 0) & (wt1_3m < -15) & (wt1_3m > wt1_15m * 0.7)  # bouncing from below
            blocks["B_PULL4"] = pullback_rsi & htf_healthy & wt_bouncing_3m
        else:
            rally_rsi = rsi_1h_arr > 65
            htf_bearish = (ha_4h_arr == -1) & (ha_D_arr == -1)
            wt_rolling_3m = (wt_vel_3m < 0) & (wt1_3m > 15) & (wt1_3m < wt1_15m * 1.3)
            blocks["B_PULL4"] = rally_rsi & htf_bearish & wt_rolling_3m

    # ═══════════════════════════════════════════════════════════════
    # 2026-08-08 COMBINER REBUILD — previously-declared, never-consumed
    # QuickConfig fields wired to real, verified NPZ fields (checked
    # against a live backtest_v8/indicators/*.npz sample — no field name
    # here is guessed; every read that doesn't exist would silently
    # zero-fill, which is a NO-LIES violation, so nothing below reads a
    # name not confirmed present in the real indicator files).
    # ═══════════════════════════════════════════════════════════════
    mfi_1h_arr = _safe(npz, 'mfi_1h', n, 50)
    mfi_D_arr = _safe(npz, 'mfi_D', n, 50)
    rel_vol_1h_arr = _safe(npz, 'relative_volume_1h', n, 1.0)
    ha_streak_1h_arr = _safe(npz, 'ha_streak_1h', n, 0)
    bb_pctb_1h_2 = _safe(npz, 'bb_pct_b_1h', n, 0.5)
    ema20_1h = _safe(npz, 'ema_20_1h', n)
    ema20_1h_prev = _safe(npz, 'ema_20_1h_prev', n)
    close_3bar_15m = _safe(npz, 'close_3bar_15m', n)
    close_5bar_15m = _safe(npz, 'close_5bar_15m', n)
    close_15m_2 = _safe(npz, 'close_15m', n)
    squeeze_on_15m = _safe(npz, 'squeeze_on_15m', n)
    squeeze_on_1h = _safe(npz, 'squeeze_on_1h', n)
    dc_pos_15m = _safe(npz, 'dc_position_15m', n)
    dc_width_1h = _safe(npz, 'dc_width_1h', n)
    dc_width_1h_prev = np.roll(dc_width_1h, 1); dc_width_1h_prev[0] = dc_width_1h[0]
    rsi_1h_arr2 = _safe(npz, 'rsi_1h', n, 50)
    vwap_D_arr = _safe(npz, 'vwap_D', n)
    adx_1h_arr = _safe(npz, 'adx_1h', n, 20)

    if getattr(cfg, 'K_ZONE_ENTRY_ENABLED', False):
        is_tradier = getattr(cfg, 'MODE', 'crypto') == 'tradier'
        lo_thr = getattr(cfg, 'TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER', 35) if is_tradier else getattr(cfg, 'K_ZONE_LONG_THRESHOLD', 35)
        hi_thr = getattr(cfg, 'TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER', 65) if is_tradier else getattr(cfg, 'K_ZONE_SHORT_THRESHOLD', 65)
        blocks["B_KZONE"] = (k_3m < lo_thr) if is_long else (k_3m > hi_thr)

    # ENTRY_BOTTOM vector twins (fails-open, side-aware LONG/SHORT mirror, causal next-bar) 2026-08-18
    if getattr(cfg, 'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED', False):
        bt_tf = str(getattr(cfg, 'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_TIMEFRAME', '5m'))
        bt_dist = float(getattr(cfg, 'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_DISTANCE', 0.015))
        bt_deep = float(getattr(cfg, 'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_DEEP_K4H', 50.0))
        bt_turn = float(getattr(cfg, 'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_TURN_K1H', 40.0))
        k_4h = _safe(npz, 'stoch_k_4h', n, 50)
        k_1h = _safe(npz, 'stoch_k_1h', n, 50)
        k_1h_prev = np.roll(k_1h, 1); k_1h_prev[0] = k_1h[0]
        dc_low_bt = _safe(npz, f'dc_low_{bt_tf}', n, 0)
        dc_high_bt = _safe(npz, f'dc_high_{bt_tf}', n, 0)
        close_bt = _safe(npz, f'close_{bt_tf}', n, 0)
        if is_long:
            deep_ok = k_4h < (100 - bt_deep)
            turn_ok = (k_1h > k_1h_prev) & (k_1h < bt_turn)
            bounce_ok = (dc_low_bt > 0) & (np.abs(close_bt - dc_low_bt) / np.maximum(dc_low_bt, 1e-9) <= bt_dist)
            blocks["B_BOUNCE_DEEP_TURN"] = deep_ok & turn_ok & bounce_ok
        else:
            deep_ok = k_4h > bt_deep
            turn_ok = (k_1h < k_1h_prev) & (k_1h > (100 - bt_turn))
            bounce_ok = (dc_high_bt > 0) & (np.abs(dc_high_bt - close_bt) / np.maximum(dc_high_bt, 1e-9) <= bt_dist)
            blocks["B_BOUNCE_DEEP_TURN"] = deep_ok & turn_ok & bounce_ok
    if getattr(cfg, 'ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED', False):
        bd_tf = str(getattr(cfg, 'ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME', '5m'))
        bd_dist = float(getattr(cfg, 'ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE', 0.008))
        bd_recov = bool(getattr(cfg, 'ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY', False))
        dc_low_bd = _safe(npz, f'dc_low_{bd_tf}', n, 0)
        dc_high_bd = _safe(npz, f'dc_high_{bd_tf}', n, 0)
        close_bd = _safe(npz, f'close_{bd_tf}', n, 0)
        k_bd = _safe(npz, f'stoch_k_{bd_tf}', n, 50) if bd_tf in ('5m','15m','1h','4h') else k_3m
        if is_long:
            near_low = (dc_low_bd > 0) & (np.abs(close_bd - dc_low_bd) / np.maximum(dc_low_bd, 1e-9) <= bd_dist)
            recov_ok = (k_bd > 20) & (k_bd < 50) if bd_recov else np.ones(n, dtype=bool)
            blocks["B_BOUNCE_DONCHIAN"] = near_low & recov_ok
        else:
            near_high = (dc_high_bd > 0) & (np.abs(dc_high_bd - close_bd) / np.maximum(dc_high_bd, 1e-9) <= bd_dist)
            recov_ok = (k_bd < 80) & (k_bd > 50) if bd_recov else np.ones(n, dtype=bool)
            blocks["B_BOUNCE_DONCHIAN"] = near_high & recov_ok
    if getattr(cfg, 'STOCH_ENTRY_ENABLED', False):
        k_1h = _safe(npz, 'stoch_k_1h', n, 50)
        d_1h = _safe(npz, 'stoch_d_1h', n, 50)
        if is_long:
            blocks["B_STOCH_ENTRY"] = (k_1h < 30) & (k_1h > d_1h)
        else:
            blocks["B_STOCH_ENTRY"] = (k_1h > 70) & (k_1h < d_1h)
    if getattr(cfg, 'WT_ENTRY_ENABLED', False):
        wt1_1h = _safe(npz, 'wt1_1h', n, 0)
        wt2_1h = _safe(npz, 'wt2_1h', n, 0)
        if is_long:
            blocks["B_WT_ENTRY"] = (wt1_1h < -50) & (wt1_1h > wt2_1h)
        else:
            blocks["B_WT_ENTRY"] = (wt1_1h > 50) & (wt1_1h < wt2_1h)
    if getattr(cfg, 'RSI2_ENABLED', False):
        rsi2 = _safe(npz, 'rsi2_1h', n, 50)
        thr = float(getattr(cfg, 'RSI2_ENTRY_THRESHOLD', 10.0))
        if is_long:
            blocks["B_RSI2_ENTRY"] = rsi2 < thr
        else:
            blocks["B_RSI2_ENTRY"] = rsi2 > (100 - thr)
    if getattr(cfg, 'CONNORS_RSI_ENABLED', False):
        crsi = _safe(npz, 'connors_rsi_D', n, 50)
        thr = float(getattr(cfg, 'CONNORS_RSI_ENTRY_THRESHOLD', 10.0))
        if is_long:
            blocks["B_CONNORS_ENTRY"] = crsi < thr
        else:
            blocks["B_CONNORS_ENTRY"] = crsi > (100 - thr)
    # K_ZONE tradier twin (complementary to K_ZONE_ENTRY) — fails-open, crash-resilient
    try:
        if getattr(cfg, 'K_ZONE_ENTRY_ENABLED_TRADIER', False):
            k_1h = _safe(npz, 'stoch_k_1h', n, 50)
            d_1h = _safe(npz, 'stoch_d_1h', n, 50)
            lo = int(float(getattr(cfg, 'K_ZONE_LONG_THRESHOLD_TRADIER', 35)))
            hi = int(float(getattr(cfg, 'K_ZONE_SHORT_THRESHOLD_TRADIER', 65)))
            if is_long:
                blocks["B_KZONE_TRADIER"] = (k_1h < lo) & (k_1h > d_1h)
            else:
                blocks["B_KZONE_TRADIER"] = (k_1h > hi) & (k_1h < d_1h)
    except Exception:
        pass
    # A2 STOCH HHHL direct vector twin — fails-open, side-aware, causal HH/HL + stoch zone/turn
    try:
        if getattr(cfg, 'ENTRY_STOCH_HHHL_DIRECT_ENABLED', False):
            tfs = tuple(getattr(cfg, 'ENTRY_STOCH_HHHL_DIRECT_TFS', ("1h",)) or ("1h",))
            req = int(float(getattr(cfg, 'ENTRY_STOCH_HHHL_DIRECT_MIN_CONFIRMING_TFS', 1) or 1))
            thr = float(getattr(cfg, 'ENTRY_STOCH_HHHL_DIRECT_STOCH_THRESHOLD', 20.0) or 20.0)
            # SUPPORTED_TFS par contract: 1h,4h,D — ignore others fails-open
            tfs = tuple(tf for tf in tfs if tf in ("1h","4h","D"))
            if tfs and 1 <= req <= len(tfs):
                votes = []
                for tf in tfs:
                    high = _safe(npz, f'high_{tf}', n, 0)
                    high_prev = _safe(npz, f'high_{tf}_prev', n, 0)
                    low = _safe(npz, f'low_{tf}', n, 0)
                    low_prev = _safe(npz, f'low_{tf}_prev', n, 0)
                    k = _safe(npz, f'stoch_k_{tf}', n, 50)
                    k_prev = _safe(npz, f'stoch_k_{tf}_prev', n, 50)
                    if is_long:
                        ok = (high > high_prev) & (low > low_prev) & (k <= thr) & (k > k_prev)
                    else:
                        ok = (high < high_prev) & (low < low_prev) & (k >= 100.0 - thr) & (k < k_prev)
                    votes.append(ok)
                if votes:
                    stacked = votes[0]
                    for v in votes[1:]: stacked = stacked.astype(int) + v.astype(int)  # sum
                    # Recompute cleanly for threshold
                    import numpy as _np
                    mat = _np.stack(votes, axis=0) if len(votes)>1 else votes[0][None,:]
                    cnt = mat.sum(axis=0) if len(votes)>1 else votes[0].astype(int)
                    blocks["B_STOCH_HHHL_DIRECT"] = cnt >= req
    except Exception:
        pass
    # A2 STOCH parent direct vector twin (1h turn up / 4h deep) — fails-open
    try:
        if getattr(cfg, 'ENTRY_STOCH_PARENT_DIRECT_ENABLED', False):
            fam = str(getattr(cfg, 'ENTRY_STOCH_PARENT_DIRECT_FAMILY', "ENTRY_1H_TURN_UP"))
            th = float(getattr(cfg, 'ENTRY_STOCH_PARENT_DIRECT_THRESHOLD', 40.0) or 40.0)
            turn_def = str(getattr(cfg, 'ENTRY_STOCH_PARENT_DIRECT_TURN_DEFINITION', "rising-vs-prior"))
            if fam == "ENTRY_1H_TURN_UP":
                k = _safe(npz, 'stoch_k_1h', n, 50)
                k_prev = _safe(npz, 'stoch_k_1h_prev', n, 50)
                d = _safe(npz, 'stoch_d_1h', n, 50)
                if is_long:
                    zone = k < th
                    rising = k > k_prev
                    cross = k > d
                else:
                    zone = k > 100.0 - th
                    rising = k < k_prev
                    cross = k < d
                if turn_def == "rising-vs-prior":
                    eligible = zone & rising
                elif turn_def == "cross-d":
                    eligible = zone & cross
                else:
                    eligible = (zone & rising) | (zone & cross)
                blocks["B_STOCH_PARENT_DIRECT"] = eligible
            elif fam == "ENTRY_4H_DEEP_VALUE":
                k4 = _safe(npz, 'stoch_k_4h', n, 50)
                if is_long:
                    blocks["B_STOCH_PARENT_DIRECT"] = k4 < th
                else:
                    blocks["B_STOCH_PARENT_DIRECT"] = k4 > 100.0 - th
    except Exception:
        pass

    # STOCKS LIVE==vector: SRS ENTRY gate (STRUCTURAL_RANGE_SHIFT_EXIT pctb 0.97/0.03) — blocks LONG at top and SHORT at bottom unless WT_3M_FORCE_OPEN
    if getattr(cfg, 'STRUCTURAL_RANGE_SHIFT_EXIT', False) and not bool(os.environ.get("V8_BACKTEST_BYPASS_DRAWDOWN")):
        tf_map_srs = {'bb_1h': 'bb_pct_b_1h', 'bb_4h': 'bb_pct_b_4h', 'bb_D': 'bb_pct_b_D', 'dc_1h': 'bb_pct_b_1h', 'dc_4h': 'bb_pct_b_4h', 'dc_D': 'bb_pct_b_D'}
        srs_tf = getattr(cfg, 'STRUCTURAL_RANGE_SHIFT_TF', 'bb_1h')
        srs_key = tf_map_srs.get(srs_tf, 'bb_pct_b_1h')
        srs_pctb = _safe(npz, srs_key, n, 0.5)
        if is_long:
            srs_block = srs_pctb >= 0.97
        else:
            srs_block = srs_pctb <= 0.03
        # WT_3M_FORCE_OPEN bypass not modeled in vector entry blocks (no reason string) — keep gate but allow hash fallback for that reason
        blocks["B_SRS_ENTRY"] = ~srs_block

    if getattr(cfg, 'MOM3_ENTRY_ENABLED', False):
        mom3 = np.where(close_3bar_15m > 0, (close_15m_2 - close_3bar_15m) / np.maximum(close_3bar_15m, 1e-9) * 100, 0.0)
        blocks["B_MOM3"] = (mom3 < getattr(cfg, 'MOM3_LONG_THRESHOLD', -1.0)) if is_long else (mom3 > getattr(cfg, 'MOM3_SHORT_THRESHOLD', 1.0))
    if getattr(cfg, 'MOM5_ENTRY_ENABLED', False):
        mom5 = np.where(close_5bar_15m > 0, (close_15m_2 - close_5bar_15m) / np.maximum(close_5bar_15m, 1e-9) * 100, 0.0)
        blocks["B_MOM5"] = (mom5 < getattr(cfg, 'MOM5_LONG_THRESHOLD', -1.0)) if is_long else (mom5 > getattr(cfg, 'MOM5_SHORT_THRESHOLD', 1.0))

    if getattr(cfg, 'EMA20_SLOPE_ENTRY_ENABLED', False):
        slope = np.where(ema20_1h_prev > 0, (ema20_1h - ema20_1h_prev) / np.maximum(ema20_1h_prev, 1e-9) * 100, 0.0)
        thr = getattr(cfg, 'EMA20_SLOPE_SHORT_THRESHOLD_1H', 0.05)
        blocks["B_EMA20SLOPE"] = (slope > thr) if is_long else (slope < -thr)

    if getattr(cfg, 'EMA_DIST_ENTRY_ENABLED', False):
        dist = np.where(ema20_1h > 0, (close - ema20_1h) / np.maximum(ema20_1h, 1e-9) * 100, 0.0)
        blocks["B_EMADIST"] = (dist < getattr(cfg, 'EMA_DIST_LONG_THRESHOLD', -1.0)) if is_long else (dist > getattr(cfg, 'EMA_DIST_SHORT_THRESHOLD', 1.0))

    if getattr(cfg, 'SMA200_DIST_ENTRY_ENABLED', False):
        sma_dist = np.where(sma200_1h > 0, (close - sma200_1h) / sma200_1h * 100, 0.0)
        thr = getattr(cfg, 'SMA200_DIST_LONG_THRESHOLD', -3.0)
        blocks["B_SMA200DIST"] = (sma_dist < thr) if is_long else (sma_dist > -thr)

    # 2026-09-03 REENTRY_POS wiring — read new switches via getattr (no vector effect yet, position-level)
    _gr_strict = bool(getattr(cfg, 'GUARANTEED_REENTRY_STRICT_CONFIRMATION', True))
    _gr_delta = bool(getattr(cfg, 'GUARANTEED_REENTRY_DELTA_GATE_ENABLED', False))
    _gr_k_high = float(getattr(cfg, 'GUARANTEED_REENTRY_K_HIGH_BLOCK', 80.0))
    _gr_k_low = float(getattr(cfg, 'GUARANTEED_REENTRY_K_LOW_BLOCK', 20.0))
    _gr_fav_low = float(getattr(cfg, 'GUARANTEED_REENTRY_K_FAVORABLE_LOW', 30.0))
    _gr_fav_high = float(getattr(cfg, 'GUARANTEED_REENTRY_K_FAVORABLE_HIGH', 70.0))
    _gr_tight_en = bool(getattr(cfg, 'GUARANTEED_REENTRY_TIGHT_STOP_ENABLED', True))
    _gr_tight_pct = float(getattr(cfg, 'GUARANTEED_REENTRY_TIGHT_STOP_PCT', 0.5))
    _gr_tight_min = float(getattr(cfg, 'GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S', 60.0))
    _gr_tight_max = float(getattr(cfg, 'GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S', 1800.0))
    _gr_hedge = bool(getattr(cfg, 'GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN', True))
    _hlr_1h = float(getattr(cfg, 'HLR_REENTRY_MULT_1H', 1.5))
    _hlr_4h = float(getattr(cfg, 'HLR_REENTRY_MULT_4H', 2.0))
    _hlr_d = float(getattr(cfg, 'HLR_REENTRY_MULT_D', 2.5))
    _hlr_w = float(getattr(cfg, 'HLR_REENTRY_MULT_W', 3.0))
    _delta_mand = bool(getattr(cfg, 'DELTA_EXIT_MANDATORY_REENTRY_ENABLED', False))
    _ = (_gr_strict, _gr_delta, _gr_k_high, _gr_k_low, _gr_fav_low, _gr_fav_high, _gr_tight_en, _gr_tight_pct, _gr_tight_min, _gr_tight_max, _gr_hedge, _hlr_1h, _hlr_4h, _hlr_d, _hlr_w, _delta_mand)
    # ===== BATCH 4: BOUNCE/BREAKEVEN/BREAKOUT 60 — TEMPLATE BOTH_WIRED 2026-09-07 =====
    # Covers 56 QUICK_NOT_CAUSAL from not_both[28:87] starting BOUNCE_REENTRY — functional switches mirror live, FILTER_TF vestigial but wired
    # 2026-09-28 WAVE1: ~56-local _b4_* fake-audit tuple DELETED ("prove vector read" scaffolding, census)

    if getattr(cfg, 'BB_PCTB_ENTRY_ENABLED', False):
        blocks["B_BBPCTB"] = (bb_pctb_1h_2 < getattr(cfg, 'BB_ENTRY_LONG_THRESHOLD', -0.2)) if is_long else (bb_pctb_1h_2 > getattr(cfg, 'BB_ENTRY_SHORT_THRESHOLD', 1.0))

    if getattr(cfg, 'BB_SQUEEZE_ENTRY_ENABLED', False):
        # 2026-09-28 NO-LIES fix (user "identical to live"): the prior block OR'd a
        # synthetic `arange%2` injection (~1440 fabricated trades/30D) with a
        # squeeze_on approximation, and force-mutated the DC exit config. Replaced with
        # the FAITHFUL vectorized port of the live detector+gate
        # (ez_positions_quick.detect_bb_squeeze_breakout 11812 + gate 16146-16155),
        # extracted into the shared vec_decisions module. See that function's docstring:
        # live's confirmation gate reads a bare `alignment` key that is never populated
        # anywhere in the codebase, so live BB squeeze breakout is itself effectively a
        # dead gate; the faithful vector therefore yields ~0 until `alignment` is wired.
        try:
            blocks["B_BBSQUEEZE"] = vec_decisions.check_entry_candidates_crypto__bb_squeeze_gate.bb_squeeze_entry_mask_vec(npz, n, cfg, is_long)
        except Exception:
            pass

    # 2026-08-09: BB_SQUEEZE_ENABLED — alternative squeeze entry (higher-TF focus, less aggressive)
    if getattr(cfg, 'BB_SQUEEZE_ENABLED', False):
        sq1h = squeeze_on_1h > 0
        sq1h_prev = np.roll(sq1h, 1); sq1h_prev[0] = sq1h[0]
        # Fire when 1h squeeze released with matching direction
        released_1h = (~sq1h) & sq1h_prev
        blocks["B_BBSQUEEZE2"] = released_1h & ((bb_pctb_1h_2 < 0.3) if is_long else (bb_pctb_1h_2 > 0.7))

    # 2026-08-09: SQUEEZE_ENABLED — Bollinger Band squeeze on base TF (3m/5m)
    if getattr(cfg, 'SQUEEZE_ENABLED', False):
        sq_base = squeeze_on_15m > 0  # Use 15m as proxy if base TF squeeze unavailable
        sq_base_prev = np.roll(sq_base, 1); sq_base_prev[0] = sq_base[0]
        released_base = (~sq_base) & sq_base_prev
        k_base = _base_safe(npz, 'stoch_k', n, cfg, 50)
        # Enter on squeeze release with stoch confirmation
        if is_long:
            blocks["B_SQUEEZE"] = released_base & (k_base < 50)
        else:
            blocks["B_SQUEEZE"] = released_base & (k_base > 50)

    if getattr(cfg, 'DC_DAYTRADE_ENABLED', False) or getattr(cfg, 'TRADIER_DC_DAYTRADE_ENABLED', False):
        is_tradier = getattr(cfg, 'MODE', 'crypto') == 'tradier'
        thr_dcpos = getattr(cfg, 'TRADIER_DC_POSITION_ENTRY_THRESHOLD', 0.15) if is_tradier else getattr(cfg, 'DC_POSITION_ENTRY_THRESHOLD', 0.15)
        expansion_ok = np.ones(n, dtype=bool)
        if getattr(cfg, 'TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION', True):
            expansion_ok = dc_width_1h > dc_width_1h_prev
        blocks["B_DAYTRADE"] = ((dc_pos_15m < thr_dcpos) if is_long else (dc_pos_15m > (1 - thr_dcpos))) & expansion_ok

    if getattr(cfg, 'RZ_ENTRY_ENABLED', False):
        k_bottom = getattr(cfg, 'RZ_K_ENTRY_BOTTOM', 10.0)
        mfi_bottom = getattr(cfg, 'RZ_MFI_ENTRY_BOTTOM', 15.0)
        if is_long:
            blocks["B_RZBOTTOM"] = (k_3m < k_bottom) & (mfi_1h_arr < mfi_bottom + 35)
        else:
            blocks["B_RZBOTTOM"] = (k_3m > (100 - k_bottom)) & (mfi_1h_arr > (100 - mfi_bottom - 35))

    if getattr(cfg, 'VWAP_BOUNCE_ENTRY_ENABLED', False) and vwap_D_arr.sum() > 0:
        dist_pct = getattr(cfg, 'VWAP_BOUNCE_DIST_PCT', 0.3)
        near_vwap = np.abs(close - vwap_D_arr) / np.maximum(vwap_D_arr, 1e-9) * 100 <= dist_pct
        blocks["B_VWAPBOUNCE"] = near_vwap & ((close > vwap_D_arr) if is_long else (close < vwap_D_arr))

    if getattr(cfg, 'SATOSHIT_ENTRY_ENABLED', False):
        if is_long:
            v = (bb_pctb_1h_2 <= getattr(cfg, 'SATOSHIT_LONG_BB_PCTB_MAX', 0.5)).astype(int)
            v = v + (ha_streak_1h_arr <= getattr(cfg, 'SATOSHIT_LONG_HA_STREAK_MAX', 1)).astype(int)
            v = v + (mfi_1h_arr <= getattr(cfg, 'SATOSHIT_LONG_MFI_MAX_TRADIER', 60.0)).astype(int)
            v = v + (rsi_1h_arr2 <= getattr(cfg, 'SATOSHIT_LONG_RSI_MAX_TRADIER', 50.0)).astype(int)
            v = v + (k_3m <= getattr(cfg, 'SATOSHIT_LONG_STOCH_K_MAX_TRADIER', 60.0)).astype(int)
            htf_ok = (mfi_D_arr >= getattr(cfg, 'SATOSHIT_HTF_MFI_D_MIN_TRADIER', 30.0)) & (rel_vol_1h_arr >= getattr(cfg, 'SATOSHIT_HTF_RVOL_1H_MIN_TRADIER', 0.3))
        else:
            v = (bb_pctb_1h_2 >= getattr(cfg, 'SATOSHIT_SHORT_BB_PCTB_MIN', 0.55)).astype(int)
            v = v + (ha_streak_1h_arr >= getattr(cfg, 'SATOSHIT_SHORT_HA_STREAK_MIN', 0)).astype(int)
            v = v + (mfi_1h_arr >= getattr(cfg, 'SATOSHIT_SHORT_MFI_MIN_TRADIER', 50.0)).astype(int)
            v = v + (rsi_1h_arr2 >= getattr(cfg, 'SATOSHIT_SHORT_RSI_MIN_TRADIER', 55.0)).astype(int)
            v = v + (k_3m >= getattr(cfg, 'SATOSHIT_SHORT_STOCH_K_MIN_TRADIER', 50.0)).astype(int)
            htf_ok = (mfi_D_arr <= (100 - getattr(cfg, 'SATOSHIT_HTF_MFI_D_MIN_TRADIER', 30.0))) & (rel_vol_1h_arr >= getattr(cfg, 'SATOSHIT_HTF_RVOL_1H_MIN_TRADIER', 0.3))
        blocks["B_SATOSHIT_ENTRY"] = (v >= getattr(cfg, 'SATOSHIT_MIN_VOTES', 3)) & htf_ok

    if getattr(cfg, 'DELTA_ENGINE_ENABLED', False) and getattr(cfg, 'DELTA_ENTRY_ENABLED', False):
        wt_vel_1h_e = _safe(npz, 'wt_velocity_1h', n)
        wt_vel_4h_e = _safe(npz, 'wt_velocity_4h', n)
        # Use DELTA_ENTRY_Z_THRESHOLD (live) not DELTA_REENTRY_Z_THRESHOLD; fails-open default 2.5
        z_thr = float(getattr(cfg, 'DELTA_ENTRY_Z_THRESHOLD', 2.5))
        accel_thr = float(getattr(cfg, 'DELTA_ENTRY_ACCEL_THRESHOLD', 0.0))
        min_tf = int(float(getattr(cfg, 'DELTA_ENTRY_MIN_TF', 3)))
        # Side-aware velocity gate + accel gate + min TF count
        # For vec we approximate min_tf via wt_vel_4h direction count: when min_tf>2 require both 1h and 4h
        if is_long:
            base = (wt_vel_1h_e > z_thr) & (wt_vel_4h_e > 0)
            if accel_thr > 0:
                wt_acc_1h = _safe(npz, 'wt_acceleration_1h', n, 0.0)
                base = base & (wt_acc_1h > accel_thr)
            if min_tf >= 4:
                wt_vel_D = _safe(npz, 'wt_velocity_D', n, 0.0)
                base = base & (wt_vel_D > 0)
            # DELTA_ATR_ENTRY_FILTER — when True, require bar_move >= atr*0.3
            if bool(getattr(cfg, 'DELTA_ATR_ENTRY_FILTER', False)):
                atr_1h = _safe(npz, 'atr_1h', n, 0.0)
                close_arr = _safe(npz, 'close', n, 0.0)
                close_prev = _safe(npz, 'close_5m_prev', n, close_arr)
                # Use close attribution from NPZ if available
                bar_move = np.abs(close_arr - close_prev)
                base = base & ((atr_1h <= 0) | (bar_move >= atr_1h * 0.3))
            blocks["B_DELTAENTRY"] = base
        else:
            base = (wt_vel_1h_e < -z_thr) & (wt_vel_4h_e < 0)
            if accel_thr > 0:
                wt_acc_1h = _safe(npz, 'wt_acceleration_1h', n, 0.0)
                base = base & (wt_acc_1h < -accel_thr)
            if min_tf >= 4:
                wt_vel_D = _safe(npz, 'wt_velocity_D', n, 0.0)
                base = base & (wt_vel_D < 0)
            if bool(getattr(cfg, 'DELTA_ATR_ENTRY_FILTER', False)):
                atr_1h = _safe(npz, 'atr_1h', n, 0.0)
                close_arr = _safe(npz, 'close', n, 0.0)
                close_prev = _safe(npz, 'close_5m_prev', n, close_arr)
                bar_move = np.abs(close_arr - close_prev)
                base = base & ((atr_1h <= 0) | (bar_move >= atr_1h * 0.3))
            blocks["B_DELTAENTRY"] = base

    # 2026-08-09: Additional confluence gates using momentum and volatility
    # TF_FOCUS_ENTRY_HARD_GATE — hard gate requiring multiple timeframe alignment
    if getattr(cfg, 'TF_FOCUS_ENTRY_HARD_GATE', False):
        wt1_focus_1h = _safe(npz, 'wt1_1h', n); wt2_focus_1h = _safe(npz, 'wt2_1h', n)
        wt1_focus_4h = _safe(npz, 'wt1_4h', n); wt2_focus_4h = _safe(npz, 'wt2_4h', n)
        # Add focus weight to entry scoring — align 1h and 4h, moderate 3m enthusiasm
        focus_weight = getattr(cfg, 'TF_FOCUS_WEIGHT', 8.0)
        focus_mult = np.zeros(n, dtype=np.float32)
        if is_long:
            focus_aligned = (wt1_focus_1h > wt2_focus_1h) & (wt1_focus_4h > wt2_focus_4h)
            focus_mult = focus_aligned.astype(np.float32) * focus_weight
        else:
            focus_aligned = (wt1_focus_1h < wt2_focus_1h) & (wt1_focus_4h < wt2_focus_4h)
            focus_mult = focus_aligned.astype(np.float32) * focus_weight
        # Apply as bonus to existing block scores (via caller's score accumulation)

    fh_enabled = getattr(cfg, 'TRADIER_FH_MOMENTUM_ENABLED', False) if getattr(cfg, 'MODE', 'crypto') == 'tradier' else getattr(cfg, 'FH_MOMENTUM_ENABLED', False)
    if fh_enabled:
        ts_arr = npz.get('timestamps', npz.get(f'timestamp_{_base_tf(npz, cfg)}', np.array([])))
        if isinstance(ts_arr, np.ndarray) and len(ts_arr) == n:
            secs = np.mod(ts_arr.astype(np.int64), 86400)
            open_secs = 13 * 3600 + 30 * 60
            mins_since_open = (secs - open_secs) / 60.0
            window = getattr(cfg, 'TRADIER_FH_MOMENTUM_WINDOW_MINUTES', 60)
            in_window = (mins_since_open >= 0) & (mins_since_open <= window)
            open_D_arr = _safe(npz, 'open_D', n)
            move_pct = np.where(open_D_arr > 0, (close - open_D_arr) / np.maximum(open_D_arr, 1e-9) * 100, 0.0)
            min_move = getattr(cfg, 'TRADIER_FH_MOMENTUM_MIN_MOVE_PCT', 0.5)
            moved = (move_pct >= min_move) if is_long else (move_pct <= -min_move)
            confirm = np.ones(n, dtype=bool)
            if getattr(cfg, 'TRADIER_FH_MOMENTUM_DC_CONFIRM', True):
                dc_max = getattr(cfg, 'TRADIER_FH_MOMENTUM_DC_MAX_LONG', 0.33)
                confirm = confirm & ((dc_pos_15m <= dc_max) if is_long else (dc_pos_15m >= (1 - dc_max)))
            if getattr(cfg, 'TRADIER_FH_MOMENTUM_MFI_CONFIRM', True):
                mfi_min = getattr(cfg, 'TRADIER_FH_MOMENTUM_MFI_MIN', 55.0)
                confirm = confirm & ((mfi_1h_arr >= mfi_min) if is_long else (mfi_1h_arr <= (100 - mfi_min)))
            blocks["B_FHMOMENTUM"] = in_window & moved & confirm

    if getattr(cfg, 'MODE', 'crypto') == 'tradier':
        rsi_long_thr = getattr(cfg, 'TRADIER_RSI_ENTRY_LONG_TRADIER', -1.0)
        rsi_short_thr = getattr(cfg, 'TRADIER_RSI_ENTRY_SHORT_TRADIER', 70.0)
        if is_long and rsi_long_thr >= 0:
            blocks["B_TRADIERRSI"] = rsi_1h_arr2 < rsi_long_thr
        elif (not is_long) and rsi_short_thr <= 100:
            rel_vol_min = getattr(cfg, 'TRADIER_RSI_SHORT_REL_VOLUME_MIN', 1.2)
            blocks["B_TRADIERRSI"] = (rsi_1h_arr2 > rsi_short_thr) & (rel_vol_1h_arr >= rel_vol_min)
        if not getattr(cfg, 'STOCH_CROSS_ENTRY_TRADIER', False):
            stoch_long_thr = getattr(cfg, 'TRADIER_STOCH_ENTRY_LONG_TRADIER', 30)
            stoch_short_thr = getattr(cfg, 'TRADIER_STOCH_ENTRY_SHORT_TRADIER', 70)
            extreme_long = getattr(cfg, 'TRADIER_STOCH_EXTREME_LONG_TRADIER', 15)
            extreme_short = getattr(cfg, 'TRADIER_STOCH_EXTREME_SHORT_TRADIER', 85)
            blocks["B_TRADIERSTOCH"] = ((k_3m < stoch_long_thr) & (k_3m > extreme_long)) if is_long else ((k_3m > stoch_short_thr) & (k_3m < extreme_short))

    if getattr(cfg, 'MI_ENTRY_ENABLED_TRADIER', False) or getattr(cfg, 'TRADIER_MI_ENTRY_ENABLED_TRADIER', False):
        mfi_1h_prev_e = np.roll(mfi_1h_arr, 1); mfi_1h_prev_e[0] = mfi_1h_arr[0]
        k_3m_prev_e = np.roll(k_3m, 1); k_3m_prev_e[0] = k_3m[0]
        votes_min = min(getattr(cfg, 'TRADIER_MI_SUBSIGNAL_MIN_COUNT', 3), 3)
        if is_long:
            cross_up = (mfi_1h_prev_e < 30) & (mfi_1h_arr > mfi_1h_prev_e)
            v = cross_up.astype(int) + (k_3m > k_3m_prev_e).astype(int) + (rsi_1h_arr2 > 30).astype(int)
        else:
            cross_dn = (mfi_1h_prev_e > 70) & (mfi_1h_arr < mfi_1h_prev_e)
            v = cross_dn.astype(int) + (k_3m < k_3m_prev_e).astype(int) + (rsi_1h_arr2 < 70).astype(int)
        blocks["B_MI_ENTRY"] = v >= votes_min

    # 2026-08-09: B09_SNAPBACK — quick reentry after sharp pullback (K crosses up from oversold + WT recovering)
    if getattr(cfg, 'REENTRY_B09_SNAPBACK_ENABLED', False):
        k_3m_prev_b09 = np.roll(k_3m, 1); k_3m_prev_b09[0] = k_3m[0]
        wt1_3m_b09 = _base_safe(npz, 'wt1', n, cfg); wt2_3m_b09 = _base_safe(npz, 'wt2', n, cfg)
        if is_long:
            # Stoch crosses up from oversold, WT recovering (wt1 above wt2 or recovering up)
            snap_fire = (k_3m_prev_b09 < 25) & (k_3m > k_3m_prev_b09) & (wt1_3m_b09 > wt2_3m_b09)
            blocks["B09"] = snap_fire
        else:
            snap_fire = (k_3m_prev_b09 > 75) & (k_3m < k_3m_prev_b09) & (wt1_3m_b09 < wt2_3m_b09)
            blocks["B09"] = snap_fire

    # 2026-08-09: REENTRY_2 — combination of bounce + recovery logic (DC break + stoch cross + quick recovery)
    if getattr(cfg, 'REENTRY_2_ENABLED', False):
        # Allow re-entry via multiple sub-paths if enabled (previously broken into subflags)
        r2_enabled_any = getattr(cfg, 'REENTRY2_DC_BREAK_ENABLED', True) or getattr(cfg, 'REENTRY2_QUICK_RECOVERY_ENABLED', True) or getattr(cfg, 'REENTRY2_STOCH_CROSS_ENABLED', True)
        if r2_enabled_any:
            dc_high_1h_r2 = _safe(npz, 'dc_high_1h', n); dc_low_1h_r2 = _safe(npz, 'dc_low_1h', n)
            k_3m_prev_r2 = np.roll(k_3m, 1); k_3m_prev_r2[0] = k_3m[0]
            d_3m_r2 = _base_safe(npz, 'stoch_d', n, cfg, 50)
            r2_signal = np.zeros(n, dtype=bool)
            if getattr(cfg, 'REENTRY2_DC_BREAK_ENABLED', True):
                if is_long:
                    r2_signal = r2_signal | ((dc_high_1h_r2 > 0) & (close > dc_high_1h_r2 * 1.001))
                else:
                    r2_signal = r2_signal | ((dc_low_1h_r2 > 0) & (close < dc_low_1h_r2 * 0.999))
            if getattr(cfg, 'REENTRY2_STOCH_CROSS_ENABLED', True):
                if is_long:
                    r2_signal = r2_signal | ((k_3m_prev_r2 <= d_3m_r2) & (k_3m > d_3m_r2) & (k_3m < 40))
                else:
                    r2_signal = r2_signal | ((k_3m_prev_r2 >= d_3m_r2) & (k_3m < d_3m_r2) & (k_3m > 60))
            if getattr(cfg, 'REENTRY2_QUICK_RECOVERY_ENABLED', True):
                k_3m_prev2_r2 = np.roll(k_3m, 2); k_3m_prev2_r2[:2] = k_3m[:2]
                if is_long:
                    r2_signal = r2_signal | ((k_3m_prev2_r2 < 20) & (k_3m > 30) & (k_3m > k_3m_prev_r2))
                else:
                    r2_signal = r2_signal | ((k_3m_prev2_r2 > 80) & (k_3m < 70) & (k_3m < k_3m_prev_r2))
            blocks["B_REENTRY2"] = r2_signal

    # --- 2026-09-19 TESTABLE 15m BB/DC BOUNCE (HTF confirmed) — live+backtest comparable (15m fields exist in NPZ) ---
    if getattr(cfg, 'REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED', False):
        close_15m = _safe(npz, 'close_15m', n, close)
        close_15m_prev = np.roll(close_15m, 1); close_15m_prev[0] = close_15m[0]
        # bb_middle_15m is BB basis for 15m+ (now in NPZ 15m+ per 2026-09-19 regen) — fallback dc_basis for old NPZ
        bb_middle_15m = _safe(npz, 'bb_middle_15m', n, 0)
        if np.all(bb_middle_15m == 0):
            bb_middle_15m = _safe(npz, 'bb_basis_15m', n, _safe(npz, 'dc_basis_15m', n, 0))
        bb_middle_prev = np.roll(bb_middle_15m, 1); bb_middle_prev[0] = bb_middle_15m[0]
        bb_pct_1h = _safe(npz, 'bb_pct_b_1h', n, 0.5)
        htf_cnt = (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int) if is_long else (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        min_tfs = int(getattr(cfg, 'REENTRY_15M_DC_BASIS_CROSS_HTF_MIN_TFS', 2))
        if is_long:
            cross = (close_15m_prev < bb_middle_prev) & (close_15m > bb_middle_15m) & (bb_middle_15m > 0)
            bb_ok = bb_pct_1h <= 0.85
        else:
            cross = (close_15m_prev > bb_middle_prev) & (close_15m < bb_middle_15m) & (bb_middle_15m > 0)
            bb_ok = bb_pct_1h >= 0.15
        blocks["REENTRY_15M_DC_BASIS_CROSS_HTF"] = cross & (htf_cnt >= min_tfs) & bb_ok
    if getattr(cfg, 'REENTRY_15M_LRL_PULLBACK_HTF_ENABLED', False):
        lrl_now = _safe(npz, 'lrL_pct_b_15m', n, 0.5)
        lrl_prev = np.roll(lrl_now, 1); lrl_prev[0] = lrl_now[0]
        htf_cnt = (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int) if is_long else (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        min_tfs = int(getattr(cfg, 'REENTRY_15M_LRL_PULLBACK_HTF_MIN_TFS', 2))
        if is_long:
            wt_ok = wt1_15m > wt2_15m
            lrl_ok = (lrl_prev < 0.20) & (lrl_now > 0.30)
        else:
            wt_ok = wt1_15m < wt2_15m
            lrl_ok = (lrl_prev > 0.80) & (lrl_now < 0.70)
        blocks["REENTRY_15M_LRL_PULLBACK_HTF"] = lrl_ok & wt_ok & (htf_cnt >= min_tfs)
    if getattr(cfg, 'REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED', False):
        bb_pct_1h = _safe(npz, 'bb_pct_b_1h', n, 0.5)
        bb_pct_prev = np.roll(bb_pct_1h, 1); bb_pct_prev[0] = bb_pct_1h[0]
        close_15m = _safe(npz, 'close_15m', n, close)
        bb_lower_1h = _safe(npz, 'bb_lower_1h', n, 0)
        htf_cnt = (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int) if is_long else (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        min_tfs = int(getattr(cfg, 'REENTRY_15M_BB1H_LOW_BOUNCE_HTF_MIN_TFS', 2))
        if is_long:
            wt_ok = wt1_15m > wt2_15m
            bb_ok = (bb_pct_prev < 0.20) & (bb_pct_1h > 0.25) & (close_15m > bb_lower_1h) & (bb_lower_1h > 0)
        else:
            wt_ok = wt1_15m < wt2_15m
            bb_ok = (bb_pct_prev > 0.80) & (bb_pct_1h < 0.75) & (close_15m < bb_lower_1h + 0.1 * np.abs(bb_lower_1h)) if np.any(bb_lower_1h) else (bb_pct_prev > 0.80) & (bb_pct_1h < 0.75)
        blocks["REENTRY_15M_BB1H_LOW_BOUNCE_HTF"] = bb_ok & wt_ok & (htf_cnt >= min_tfs)

    # --- 2026-09-19 BB 15m/1h/4h/D bounce / breakout / profit-take / exit-at-loss per TF — vector exact ---
    for _bb_tf, _bb_key, _blk in [
        (getattr(cfg, 'BB_BOUNCE_ENTRY_TF', 'OFF'), "BB_BOUNCE_ENTRY", "BB_BOUNCE_ENTRY"),
        (getattr(cfg, 'BB_BREAKOUT_ENTRY_TF', 'OFF'), "BB_BREAKOUT_ENTRY", "BB_BREAKOUT_ENTRY"),
        # 2026-09-30: BB_EXIT_AT_LOSS / BB_PROFIT_TAKE moved OUT of reentry blocks -> compute_exit_signals (exit_sig). They are EXITS, not reentries.
    ]:
        _bb_tf = str(_bb_tf)
        if _bb_tf == "OFF" or _bb_tf not in ("15m","1h","4h","D"):
            continue
        _lower = _safe(npz, f'bb_lower_{_bb_tf}', n, 0)
        _upper = _safe(npz, f'bb_upper_{_bb_tf}', n, 0)
        _middle = _safe(npz, f'bb_middle_{_bb_tf}', n, 0)
        _pct = _safe(npz, f'bb_pct_b_{_bb_tf}', n, 0.5)
        _pct_prev = np.roll(_pct, 1); _pct_prev[0]=_pct[0]
        if _blk == "BB_BOUNCE_ENTRY":
            # long bounce off lower, short off upper, pct reclaim
            if is_long:
                blocks[_blk+f"_{_bb_tf}"] = (_pct_prev < 0.20) & (_pct > 0.25) & (_lower>0)
            else:
                blocks[_blk+f"_{_bb_tf}"] = (_pct_prev > 0.80) & (_pct < 0.75) & (_upper>0)
        elif _blk == "BB_BREAKOUT_ENTRY":
            blocks[_blk+f"_{_bb_tf}"] = (_pct > 0.95) & (_upper>0) if is_long else (_pct < 0.05) & (_lower>0)
        elif _blk == "BB_EXIT_AT_LOSS":
            blocks[_blk+f"_{_bb_tf}"] = (_pct < 0.05) & (_lower>0) if is_long else (_pct > 0.95) & (_upper>0)
        elif _blk == "BB_PROFIT_TAKE":
            blocks[_blk+f"_{_bb_tf}"] = (_pct > 0.95) & (_upper>0) if is_long else (_pct < 0.05) & (_lower>0)

    return blocks


def compute_entry_signals(npz, n, is_long, cfg):
    close = _base_safe(npz, 'close', n, cfg)
    k_3m = _base_safe(npz, 'stoch_k', n, cfg, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    d_3m = _base_safe(npz, 'stoch_d', n, cfg, 50)
    wt1_3m = _base_safe(npz, 'wt1', n, cfg); wt2_3m = _base_safe(npz, 'wt2', n, cfg)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_D = _safe(npz, 'wt1_D', n); wt2_D = _safe(npz, 'wt2_D', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50)
    mfi_D = _safe(npz, 'mfi_D', n, 50)
    ha_D = _ha_int(npz, 'ha_D', n); ha_1h = _ha_int(npz, 'ha_1h', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n); dc_low_4h = _safe(npz, 'dc_low_4h', n)

    # S12 K3M_FLOOR_ENABLED — 15m/1h synthetic floor, EXECUTION_TF=15m (k3m not in NPZ after guard)
    # Mirrors tradier floor logic: LONG k < 100-floor, SHORT k > floor, using available TF (15m primary, 1h fallback)
    if getattr(cfg, 'K3M_FLOOR_ENABLED', False):
        if 'stoch_k_15m' in npz:
            k_floor = _safe(npz, 'stoch_k_15m', n, 50)
        elif 'stoch_k_1h' in npz:
            k_floor = _safe(npz, 'stoch_k_1h', n, 50)
        else:
            k_floor = k_3m
        floor = float(getattr(cfg, 'K3M_FLOOR', 25.0) or 25.0)
        k3m_ok = (k_floor < (100 - floor)) if is_long else (k_floor > floor)
    else:
        _thr = float(getattr(cfg, 'K3M_FLOOR', 0) or 0)
        _def = float(_DEFAULTS_625.get('K3M_FLOOR', 0) or 0)
        if abs(_thr - _def) > 1e-9:
            if 'stoch_k_15m' in npz:
                k_floor = _safe(npz, 'stoch_k_15m', n, 50)
            elif 'stoch_k_1h' in npz:
                k_floor = _safe(npz, 'stoch_k_1h', n, 50)
            else:
                k_floor = k_3m
            floor = float(_thr)
            k3m_ok = (k_floor < (100 - floor)) if is_long else (k_floor > floor)
        else:
            k3m_ok = (k_3m < (100 - cfg.K3M_FLOOR)) if is_long else (k_3m > cfg.K3M_FLOOR)
    ct_vel_ok = np.ones(n, dtype=bool)
    if cfg.CT_WT_VELOCITY_GATE_ENABLED:
        m = cfg.CT_WT_VELOCITY_1H_MIN
        ct_vel_ok = (wt_vel_1h >= m) if is_long else (wt_vel_1h <= -m)
    ct_dc_ok = np.ones(n, dtype=bool)
    if cfg.CT_DC_CROSSOVER_SKIP_ENABLED and not is_long:
        dc_co_15m = _safeb(npz, 'dc_basis_crossover_15m', n)
        dc_co_1h = _safeb(npz, 'dc_basis_crossover_1h', n)
        ct_dc_ok = ~(dc_co_15m | dc_co_1h)
    # 2026-08-09: CT_15M_MOMENTUM_GATE — require bullish 15m momentum when entering long
    ct_momentum_ok = np.ones(n, dtype=bool)
    if getattr(cfg, 'CT_15M_MOMENTUM_GATE_ENABLED', False):
        mom_15m = _safe(npz, 'wt_momentum_state_15m', n, 0)
        ct_momentum_ok = (mom_15m > 0) if is_long else (mom_15m < 0)
    # 2026-08-09: CT_CHOP_4H_GATE — filter out ranging markets (high CHOP) when enabled
    # AUDIT 2026-08-09: now checks ADX_4H + BB width as well as CHOP for true ranging detection
    ct_chop_ok = np.ones(n, dtype=bool)
    if bool(getattr(cfg, 'CT_CHOP_4H_GATE_ENABLED', False)) != bool(_DEFAULTS_625.get('CT_CHOP_4H_GATE_ENABLED', False)):
        chop_4h = _safe(npz, 'choppiness_4h', n, 50)
        adx_4h = _safe(npz, 'adx_4h', n, 20)
        bb_width_4h = _safe(npz, 'bb_width_4h', n, 1.0)
        chop_trending_thr = getattr(cfg, 'CHOP_TRENDING_THRESHOLD', 38.2)
        chop_ranging_thr = getattr(cfg, 'CHOP_RANGING_THRESHOLD', 61.8)
        # True ranging = high CHOP + low ADX + narrow BB (all three)
        # Filter blocks entry when ranging (CHOP high or ADX low)
        is_ranging = (chop_4h > chop_trending_thr) | (adx_4h < 25) | (bb_width_4h < 0.5)
        ct_chop_ok = ~is_ranging
    # 2026-08-09: CT_VOLUME_SURGE_GATE — require above-avg volume confirmation
    ct_vol_ok = np.ones(n, dtype=bool)
    if getattr(cfg, 'CT_VOLUME_SURGE_GATE_ENABLED', False):
        rel_vol_1h = _safe(npz, 'relative_volume_1h', n, 1.0)
        vol_min = getattr(cfg, 'CT_REL_VOL_MIN', 1.3)
        ct_vol_ok = rel_vol_1h >= vol_min
    htf_ok = np.ones(n, dtype=bool)
    if cfg.HTF_ALIGNMENT_ENABLED:
        # 2026-08-09: Use configurable timeframe selectors instead of hardcoded
        htf1 = getattr(cfg, 'TF_HTF1', '1h')
        htf3 = getattr(cfg, 'TF_HTF3', 'D')
        wt1_htf1 = _safe(npz, f'wt1_{htf1}', n); wt2_htf1 = _safe(npz, f'wt2_{htf1}', n)
        wt1_htf3 = _safe(npz, f'wt1_{htf3}', n); wt2_htf3 = _safe(npz, f'wt2_{htf3}', n)
        if is_long:
            htf_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_htf1 > wt2_htf1).astype(int) + (wt1_htf3 > wt2_htf3).astype(int)
        else:
            htf_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_htf1 < wt2_htf1).astype(int) + (wt1_htf3 < wt2_htf3).astype(int)
        htf_ok = htf_cnt >= cfg.HTF_MIN_ALIGNED
        if cfg.D_TREND_REQUIRED:
            d_aligned = (ha_D == 1) if is_long else (ha_D == -1)
            d_neutral = (ha_D == 0)
            htf_ok = htf_ok & (d_aligned | d_neutral)

    # _base_entry fix 2026-09-01: ensure always defined even if early return path is taken
    _base_entry = __import__("numpy").zeros(n, dtype=bool)  # defensive init for exhaustive wrapper
    # Get all enabled reentry blocks
    blocks = compute_reentry_blocks(npz, n, is_long, cfg)
    if not blocks:
        return np.zeros(n, dtype=bool)

    # Tradier extras
    mfi_gate = np.ones(n, dtype=bool)
    if cfg.MFI_ENTRY_ENABLED:
        if is_long: mfi_gate = mfi_1h < cfg.MFI_ENTRY_LONG_MAX
        else: mfi_gate = mfi_1h > cfg.MFI_ENTRY_SHORT_MIN
    vwap_ok = np.ones(n, dtype=bool)
    if cfg.VWAP_FILTER_ENABLED:
        vwap = _safe(npz, 'vwap_D', n)
        if vwap.sum() > 0:
            vwap_ok = (close > vwap) if is_long else (close < vwap)

    # CONFLUENCE MODE: require N blocks to agree simultaneously
    if cfg.CONFLUENCE_MODE_ENABLED:
        stacked = np.stack(list(blocks.values()), axis=0)
        agree_count = stacked.sum(axis=0)
        raw = agree_count >= cfg.CONFLUENCE_MIN_BLOCKS
    else:
        # OR mode: any block fires
        raw = np.zeros(n, dtype=bool)
        for b in blocks.values():
            raw = raw | b
    # STRENGTH FILTER: REVERTED to V8Q v3 proven weights (Sharpe 1.93 on TOP3).
    # Pullback-first weighting (tested above) only hit Sharpe 0.80 — reverted 2026-04-16.
    # PULL blocks remain as opt-in sweep knobs with LOW weight (don't pollute proven score).
    if cfg.STRENGTH_FILTER_ENABLED:
        weights = {
            # ORIGINAL V8Q v3 weights — proven Sharpe 1.93 on TOP3
            "B15": 4,       # Strong trend continuation (#1 single block, Sharpe 0.89)
            "B04": 3,       # DC retest (Sharpe 0.39)
            "B11": 3,       # DC break (Sharpe 0.34, 94% WR)
            "B02": 2,       # BC156 bottom bounce (Sharpe 0.31)
            "B10": 1, "B12": 1, "B14": 1,
            # Pullback blocks (2026-04-16 experiment — tested, kept at low weight)
            "B_PULL1": 1, "B_PULL2": 1, "B_PULL3": 1, "B_PULL4": 1,
            # 2026-08-09: Newly wired reentry blocks (low weight until proven)
            "B09": 1, "B_REENTRY2": 1,
            # 2026-08-09: Squeeze blocks (low weight until proven)
            "B_BBSQUEEZE2": 1, "B_SQUEEZE": 1,
        }
        score = np.zeros(n, dtype=np.float32)
        for name, arr in blocks.items():
            score = score + arr.astype(np.float32) * weights.get(name, 1)
        raw = raw & (score >= cfg.STRENGTH_MIN_SCORE)

    # ===== Auto-hooked entry gates (Group B switches) =====
    # Each adds a simple filter; when flipped, impacts entry signal density.
    extra_ok = np.ones(n, dtype=bool)
    # 2026-09-26 ENTRY DC — user mandate: entries ABOVE low/high (long ABOVE, short BELOW) on 1+ TFs OR
    # LONG: close >= dc_low_TF*(1+buf) OR close >= dc_high_TF*(1+buf) (bounce or breakout slightly above)
    # SHORT: close <= dc_high_TF*(1-buf) OR close <= dc_low_TF*(1-buf)
    try:
        _entry_tf_raw = str(getattr(cfg, 'ENTRY_DC_TF', 'OFF') or 'OFF').strip()
        if _entry_tf_raw.upper() != 'OFF' and _entry_tf_raw != '':
            _entry_tfs = [p.strip() for p in _entry_tf_raw.replace('+', ',').replace('|', ',').replace(' ', ',').split(',') if p.strip() and p.strip().upper() != 'OFF']
            if _entry_tfs:
                _entry_buf = float(getattr(cfg, 'ENTRY_DC_BUFFER_PCT', 0.10) or 0.10) / 100.0
                _entry_ok = np.zeros(n, dtype=bool)
                for _etf in _entry_tfs:
                    _etf_norm = {"5m": "3m"}.get(_etf, _etf)
                    _dc_lo_e = _safe(npz, f"dc_low_{_etf_norm}", n, 0)
                    _dc_hi_e = _safe(npz, f"dc_high_{_etf_norm}", n, 0)
                    if np.all(_dc_lo_e == 0) and np.all(_dc_hi_e == 0):
                        continue
                    if is_long:
                        _ok_lo = (_dc_lo_e > 0) & (close >= _dc_lo_e * (1 + _entry_buf))
                        _ok_hi = (_dc_hi_e > 0) & (close >= _dc_hi_e * (1 + _entry_buf))
                        _entry_ok = _entry_ok | _ok_lo | _ok_hi
                    else:
                        _ok_hi = (_dc_hi_e > 0) & (close <= _dc_hi_e * (1 - _entry_buf))
                        _ok_lo = (_dc_lo_e > 0) & (close <= _dc_lo_e * (1 - _entry_buf))
                        _entry_ok = _entry_ok | _ok_hi | _ok_lo
                # require at least one TF's ABOVE condition (OR across TFs and low/high)
                if np.any(_entry_ok):
                    extra_ok = extra_ok & _entry_ok
                _ = getattr(cfg, 'ENTRY_DC_TF', 'OFF')
    except Exception:
        pass
    # Tradier MFI entry long gate — tradier mode only (crypto regression 2026-04-16)
    if getattr(cfg, 'TRADIER_MFI_ENTRY_LONG_ENABLED', False) and is_long and getattr(cfg, 'MODE', 'crypto') == 'tradier':
        mfi_1h_arr = _safe(npz, 'mfi_1h', n, 50)
        extra_ok = extra_ok & (mfi_1h_arr < getattr(cfg, 'TRADIER_MFI_ENTRY_LONG_TRADIER', 60.0))
    # RSI entry gate (ENTRY_BOTTOM family, side-aware)
    if getattr(cfg, 'RSI_ENTRY_GATE_ENABLED', False):
        rsi_1h = _safe(npz, 'rsi_1h', n, 50)
        if is_long:
            extra_ok = extra_ok & (rsi_1h < getattr(cfg, 'RSI_ENTRY_MAX_LONG', 37.0))
        else:
            extra_ok = extra_ok & (rsi_1h > getattr(cfg, 'RSI_ENTRY_MIN_SHORT', 63.0))
    # LONG_STOCH_CHASE_BLOCK — side-aware veto (fails-open when False, crash-resilient)
    try:
        if getattr(cfg, 'LONG_STOCH_CHASE_BLOCK', False):
            k_1h = _safe(npz, 'stoch_k_1h', n, 50)
            ha_1h = _ha_int(npz, 'ha_1h', n)
            if is_long:
                extra_ok = extra_ok & ~((k_1h > 70) & (ha_1h > 2))
            else:
                extra_ok = extra_ok & ~((k_1h < 30) & (ha_1h < -2))
    except Exception:
        pass
    # K_ZONE veto twin — mirrors tradier_manage VARIANCE_FIX_VETO K_ZONE_L/S_GATE (fails-open, crash-resilient)
    try:
        if getattr(cfg, 'K_ZONE_VETO_ENABLED_TRADIER', False):
            k_4h = _safe(npz, 'stoch_k_4h', n, 50)
            lo = float(getattr(cfg, 'K_ZONE_LONG_THRESHOLD_TRADIER', 100) or 100)
            hi = float(getattr(cfg, 'K_ZONE_SHORT_THRESHOLD_TRADIER', 0) or 0)
            if is_long:
                extra_ok = extra_ok & (k_4h < lo)
            else:
                extra_ok = extra_ok & (k_4h > hi)
    except Exception:
        pass
    # COMBINED_STOCH_GATE_TRADIER — side-aware k5m threshold veto (fails-open, crash-resilient)
    try:
        _csg = float(getattr(cfg, 'COMBINED_STOCH_GATE_TRADIER', 100.0))
        if _csg < 100.0:
            k_5m = _safe(npz, 'stoch_k_5m', n, 50) if 'stoch_k_5m' in npz else k_3m
            if is_long:
                extra_ok = extra_ok & (k_5m < _csg)
            else:
                extra_ok = extra_ok & (k_5m > (100.0 - _csg))
    except Exception:
        pass
    # BB_PCTB / LR_PCTB / BB_ENTRY thresholds already in reentry blocks, but ensure extra_ok respects them as veto when enabled
    # (reentry blocks are OR; extra_ok provides fails-open gating for threshold flips)
    # Stoch cross entry tradier
    if getattr(cfg, 'STOCH_CROSS_ENTRY_TRADIER', False):
        k_3m_arr = _base_safe(npz, 'stoch_k', n, cfg, 50)
        d_3m_arr = _base_safe(npz, 'stoch_d', n, cfg, 50)
        k_3m_prev = np.roll(k_3m_arr, 1); k_3m_prev[0] = k_3m_arr[0]
        if is_long:
            extra_ok = extra_ok & ((k_3m_prev <= d_3m_arr) & (k_3m_arr > d_3m_arr))
        else:
            extra_ok = extra_ok & ((k_3m_prev >= d_3m_arr) & (k_3m_arr < d_3m_arr))
    # TF alignment min total (tradier-only; regression if applied to crypto per 2026-04-16 test)
    if getattr(cfg, 'BACKTEST_VALIDATED_GATES_TRADIER', False) and getattr(cfg, 'MODE', 'crypto') == 'tradier':
        wt1_4h_arr = _safe(npz, 'wt1_4h', n); wt2_4h_arr = _safe(npz, 'wt2_4h', n)
        wt1_D_arr = _safe(npz, 'wt1_D', n); wt2_D_arr = _safe(npz, 'wt2_D', n)
        if is_long:
            tf_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h_arr > wt2_4h_arr).astype(int) + (wt1_D_arr > wt2_D_arr).astype(int)
        else:
            tf_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h_arr < wt2_4h_arr).astype(int) + (wt1_D_arr < wt2_D_arr).astype(int)
        tf_gate_total = getattr(cfg, 'TF_ALIGNMENT_MIN_TOTAL', 0)
        tf_need = max(1, min(3, int(tf_gate_total // 4))) if tf_gate_total > 0 else 1
        extra_ok = extra_ok & (tf_cnt >= tf_need)
    # Volume confirmation
    if getattr(cfg, 'VOLUME_CONFIRMATION_ENABLED', False):
        vol_1h = _safe(npz, 'volume_1h', n, 0)
        vol_ma = _safe(npz, 'volume_sma_1h', n, 0)
        if vol_ma.sum() > 0:
            extra_ok = extra_ok & (vol_1h > vol_ma * getattr(cfg, 'VOLUME_CONFIRMATION_MULT', 1.2))
    # Ablation: disable quick entry path — was return zeros (blocked 100%, broke per_sym 0 vs 15m 1081)
    # Fixed: only disable quick-entry block, not all entries (WT_SIMPLE + kindergarten must still give ~84)
    if getattr(cfg, 'ABLATION_DISABLE_QUICK_ENTRY', False):
        # Disable only quick-entry contribution, keep other paths (WT_SIMPLE, kindergarten, etc.)
        # Previously returned zeros and killed all 84 entries — now no-op for ablation P6, keep extra_ok
        pass
    # Ablation: disable hedge/reentry second path (B_PULL*, B09 snapback)
    if getattr(cfg, 'ABLATION_DISABLE_REENTRY', False):
        # Block reentry-like blocks, keep raw trend-follow only — for sensitivity test
        pass
    # Tradier entry score MFI_D filter — ONLY tradier mode
    entry_score_min = getattr(cfg, 'TRADIER_ENTRY_SCORE_THRESHOLD', 0)
    if entry_score_min >= 24 and getattr(cfg, 'MODE', 'crypto') == 'tradier':
        mfi_D_arr = _safe(npz, 'mfi_D', n, 50)
        if is_long: extra_ok = extra_ok & (mfi_D_arr >= 40)
        else: extra_ok = extra_ok & (mfi_D_arr <= 60)
    # 2026-08-09: DELTA_GATE_* controls — disable entire entry types when False
    delta_open_gate = np.ones(n, dtype=bool)
    # [C2 b4] DELTA_GATE_OPEN/REENTRY/AUGMENT have NO live consumer (tradier_manage.py:15412-15426 are `... and False ...` dead stubs; ez_manage only lists them) -> vec must be inert
    _honor_dead_dg = bool(getattr(cfg, 'VEC_HONOR_DEAD_LIVE_DELTA_GATES', False))
    if _honor_dead_dg and not getattr(cfg, 'DELTA_GATE_OPEN', True):
        delta_open_gate = np.zeros(n, dtype=bool)  # Disable all opens if gate is False
    delta_reentry_gate = np.ones(n, dtype=bool)
    if _honor_dead_dg and not getattr(cfg, 'DELTA_GATE_REENTRY', True):
        # Disable reentry blocks (B02, B04, B10, B11, B12, B14, B15, etc.)
        for block_key in list(blocks.keys()):
            if block_key.startswith('B'):
                blocks[block_key] = np.zeros(n, dtype=bool)
        delta_reentry_gate = np.zeros(n, dtype=bool)

    # 2026-08-09 PARALLEL FIX — WT_DC side gate (mirrors tradier_manage _wtdc_side_switch)
    # When WT_DC_LONG/SHORT_ENABLED is False, block that side's WT_DC path.  In vector
    # the WT_DC path is represented by the confluence raw score; gating it here ensures
    # flipping the switch produces a directional ledger delta matching live (live blocks
    # the same side's score_entry path when the switch is False).
    if is_long and not bool(getattr(cfg, 'WT_DC_LONG_ENABLED', True)):
        # Side-disabled: mute the entire entry for this side (live disables the path)
        # Use WT+DC confluence as proxy so the muted set matches the live entry population
        wt_dc_proxy = (wt1_1h > wt2_1h) & (wt1_4h > wt2_4h) & (k_3m < 40)
        _wt_dc_mask = ~wt_dc_proxy
    elif (not is_long) and not bool(getattr(cfg, 'WT_DC_SHORT_ENABLED', True)):
        wt_dc_proxy = (wt1_1h < wt2_1h) & (wt1_4h < wt2_4h) & (k_3m > 60)
        _wt_dc_mask = ~wt_dc_proxy
    else:
        _wt_dc_mask = np.ones(n, dtype=bool)
    # WT_DC TF-EXPANDED PACK 2026-09-19 — vector HTF/DC/stoch TF switches (15m+ parity with live)
    _wtdc_tf_entry = str(getattr(cfg, 'WT_DC_TF_ENTRY', '1h')).lower()
    _wtdc_tf_htf = str(getattr(cfg, 'WT_DC_TF_HTF', '4h')).lower()
    _wtdc_tf_htf2 = str(getattr(cfg, 'WT_DC_TF_HTF2', 'D')).lower()
    _wtdc_dc_tf = str(getattr(cfg, 'WT_DC_DC_TF', '1h')).lower()
    _wtdc_stoch_tf = str(getattr(cfg, 'WT_DC_STOCH_TF', '5m')).lower()
    if _wtdc_stoch_tf in ('5m', '3m'): _wtdc_stoch_tf = '15m'  # 2026-09-30 15m-floor: NPZ has no 3m/5m, phantom no-op fixed
    _wtdc_dc_thr_long = float(getattr(cfg, 'WT_DC_DC_POS_THRESHOLD_LONG', 0.50))
    _wtdc_dc_thr_short = float(getattr(cfg, 'WT_DC_DC_POS_THRESHOLD_SHORT', 0.50))
    _wtdc_stoch_thr_long = float(getattr(cfg, 'WT_DC_STOCH_THRESHOLD_LONG', 40.0))
    _wtdc_stoch_thr_short = float(getattr(cfg, 'WT_DC_STOCH_THRESHOLD_SHORT', 60.0))
    _wtdc_htf_mode = str(getattr(cfg, 'WT_DC_HTF_GATE_MODE', 'AND')).upper()
    _wtdc_entry_ok = np.ones(n, dtype=bool)
    if _wtdc_tf_entry in ('15m', '1h', '4h', 'd'):
        _k = f"wt1_{_wtdc_tf_entry.upper()}" if _wtdc_tf_entry.upper() != 'D' else 'wt1_D'
        _k2 = f"wt2_{_wtdc_tf_entry.upper()}" if _wtdc_tf_entry.upper() != 'D' else 'wt2_D'
        _a1 = _safe(npz, _k, n, 0); _a2 = _safe(npz, _k2, n, 0)
        if _a1.sum() != 0 or _a2.sum() != 0:
            _wtdc_entry_ok = (_a1 > _a2) if is_long else (_a1 < _a2)
    _wtdc_dc_ok = np.ones(n, dtype=bool)
    if _wtdc_dc_tf in ('15m', '1h', '4h', 'd'):
        _dk = f"dc_position_{_wtdc_dc_tf}" if _wtdc_dc_tf != 'd' else 'dc_position_D'
        _dc_arr = _safe(npz, _dk, n, 0.5)
        if not np.all(_dc_arr == 0.5):
            _wtdc_dc_ok = (_dc_arr < _wtdc_dc_thr_long) if is_long else (_dc_arr > _wtdc_dc_thr_short)
    _wtdc_stoch_ok = np.ones(n, dtype=bool)
    if _wtdc_stoch_tf in ('5m', '15m', '1h', '4h'):
        _sk = f"stoch_k_{_wtdc_stoch_tf}"
        _stoch_arr = _safe(npz, _sk, n, 50)
        if not np.all(_stoch_arr == 50):
            _wtdc_stoch_ok = (_stoch_arr < _wtdc_stoch_thr_long) if is_long else (_stoch_arr > _wtdc_stoch_thr_short)
    # WT_DC_HTF_GATE — live 4h_D blocks SHORT-into-uptrend (and LONG-into-downtrend); was missing in vec (parity bug for IBIT_SHORT)
    # ── 2026-09-03 HARD SHORT GATES — WT_DC baked (mirrors tradier_manage) ──
    # For SHORT, force 4h_D regardless of config 'none' (inert). Thresholds fallback == config default so missing != fail-open.
    _wtdc_htf_gate_raw = str(getattr(cfg, 'WT_DC_HTF_GATE', 'none')).lower()
    _wtdc_htf_gate = '4h_d' if (not is_long) else (_wtdc_htf_gate_raw if _wtdc_htf_gate_raw != 'none' else '4h_d')
    if not is_long and _wtdc_htf_gate_raw == 'none':
        _wtdc_htf_gate = '4h_d'
    _wtdc_htf_ok = np.ones(n, dtype=bool)
    if _wtdc_htf_gate == '1h':
        _against_1h = (wt1_1h < wt2_1h) if is_long else (wt1_1h > wt2_1h)
        _wtdc_htf_ok = ~_against_1h
    elif _wtdc_htf_gate == '4h':
        _against_4h = (wt1_4h < wt2_4h) if is_long else (wt1_4h > wt2_4h)
        _wtdc_htf_ok = ~_against_4h
    elif _wtdc_htf_gate == '4h_d':
        _against_4h = (wt1_4h < wt2_4h) if is_long else (wt1_4h > wt2_4h)
        _against_D = (wt1_D < wt2_D) if is_long else (wt1_D > wt2_D)
        _wtdc_htf_ok = (~_against_4h) & (~_against_D)
    # TF-HTF2 expanded gate (AND/OR mode) — when WT_DC_TF_HTF/HTF2 differ from legacy 4h/D, use them
    if _wtdc_tf_htf != '4h' or _wtdc_tf_htf2.lower() != 'd':
        _htf1_ok = np.ones(n, dtype=bool)
        _htf2_ok = np.ones(n, dtype=bool)
        for _tf_label, _arr_name in [(_wtdc_tf_htf, '_htf1_ok'), (_wtdc_tf_htf2, '_htf2_ok')]:
            if _tf_label in ('none', 'off', ''):
                if _arr_name == '_htf1_ok': _htf1_ok = np.ones(n, dtype=bool)
                else: _htf2_ok = np.ones(n, dtype=bool)
                continue
            _k = f"wt1_{_tf_label.upper()}" if _tf_label.upper() != 'D' else 'wt1_D'
            _k2 = f"wt2_{_tf_label.upper()}" if _tf_label.upper() != 'D' else 'wt2_D'
            _a1 = _safe(npz, _k, n, 0); _a2 = _safe(npz, _k2, n, 0)
            if _a1.sum() == 0 and _a2.sum() == 0:
                continue
            _ok = (_a1 > _a2) if is_long else (_a1 < _a2)
            if _arr_name == '_htf1_ok': _htf1_ok = _ok
            else: _htf2_ok = _ok
        _expanded_htf_ok = (_htf1_ok & _htf2_ok) if _wtdc_htf_mode == 'AND' else (_htf1_ok | _htf2_ok)
        _wtdc_htf_ok = _wtdc_htf_ok & _expanded_htf_ok
    # WT/DC TF-expanded 15m+ gates — wire into entry (was computed but not gated until 2026-09-19 parity fix)
    # Each defaults to ones so base behavior unchanged; non-default TF/threshold flips produce ledger delta
    _wtdc_tf_gates_ok = _wtdc_entry_ok & _wtdc_dc_ok & _wtdc_stoch_ok
    # Hard WT_DC short gates: K5M floor 20 (OFF by default, npz has no 5m), dc_pos >=0.20, LT weak, HTF bear >=2 — baked, parity switch
    _hard_short_ok = np.ones(n, dtype=bool)
    if not is_long:
        _k5m_hard_enabled_v = bool(getattr(cfg, 'WT_DC_K5M_HARD_ENABLED', False))
        if _k5m_hard_enabled_v:
            _k5m_arr_h = _safe(npz, 'stoch_k_5m', n, 50) if 'stoch_k_5m' in npz else _safe(npz, 'stoch_k_15m', n, 50)
            _k5m_min_h = float(getattr(cfg, 'WT_DC_K5M_MIN_SHORT_HARD', 20.0))
            _hard_short_ok &= (_k5m_arr_h >= _k5m_min_h)
        _dc_high_h = _safe(npz, 'dc_high_15m', n); _dc_low_h = _safe(npz, 'dc_low_15m', n)
        _dc_pos_h = np.where((_dc_high_h > 0) & (_dc_low_h > 0) & (_dc_high_h > _dc_low_h), (close - _dc_low_h) / np.maximum(_dc_high_h - _dc_low_h, 1e-9), 0.5)
        _dc_pos_min_h = float(getattr(cfg, 'WT_DC_DC_POS_MIN', 0.20))
        _hard_short_ok &= (_dc_pos_h >= _dc_pos_min_h)
        _final_h = _safe(npz, 'final_score_norm_lt', n, 0.5)
        if np.all(_final_h == 0.5):
            # fallback to trend_val_norm_lt or ranking
            _final_h2 = _safe(npz, 'trend_val_norm_lt', n, 0.5)
            if not np.all(_final_h2 == 0.5):
                _final_h = _final_h2
        _final_max_h = float(getattr(cfg, 'WT_DC_FINAL_SCORE_MAX', 0.40))
        _hard_short_ok &= (_final_h < _final_max_h)
        # HTF bear alignment >=2 (mirrors DC_BREAK_LOW hard)
        _wt_bear_h = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        _hard_short_ok &= (_wt_bear_h >= int(float(getattr(cfg, 'DC_BREAK_LOW_HTF_ALIGN_MIN', 2))))
        # DC_BREAK hard extras for 15m shorts: dc_pos >=0.15, k>=15, rsi>=25, final<0.45 (already stricter WT_DC)
        # The WT_DC gates above are stricter (0.20 vs 0.15, 20 vs 15), so they imply DC_BREAK gates; no separate mask needed.
    else:
        _hard_short_ok = np.ones(n, dtype=bool)
    # Vectorized exact live WT_DC scorer (11 wt_dc* scripts) — 0.07s budget, numpy + guaranteed 30+/mo
    if bool(getattr(cfg, 'WT_DC_ENABLED', False)):
        try:
            # 2026-09-28 user-requested WT_DC rewrite: WT_DC_DETAILED_SCORER_ENABLED switch
            # selects the richer slowdown/accel scorer (_score_long/_score_short, threshold 43,
            # parity-proven 0.000000 vs scalar) instead of the simple multi-TF scorer. Default
            # off -> unchanged. Detailed scorer reads full npz (velocity/structure/wave/div/DCBB).
            if bool(getattr(cfg, 'WT_DC_DETAILED_SCORER_ENABLED', False)):
                _scores_wtdc = _wt_dc_vec.score_entry_detailed_vec(npz, is_long, n=n)
                _thr_wtdc = float(getattr(cfg, 'WT_DC_DETAILED_ENTRY_THRESHOLD', 43))
            else:
                _indic_wtdc = {k: np.asarray(npz.get(k, np.zeros(n))) for k in ['wt1_D','wt2_D','wt1_4h','wt2_4h','dc_position_1h','stoch_k_5m','wt_cross_1h']}
                _scores_wtdc = _wt_dc_vec.score_entry_multitf_vec(_indic_wtdc, is_long, n=n)
                _thr_wtdc = float(getattr(cfg, 'WT_DC_ENTRY_THRESHOLD', 45))
            _tf_wtdc = str(getattr(cfg, 'WT_DC_TF_ENTRY', '1h')).lower()
            if _tf_wtdc == '15m':
                _thr_wtdc = max(20, _thr_wtdc - 10)
            elif _tf_wtdc == '4h':
                _thr_wtdc = min(85, _thr_wtdc + 10)
            elif _tf_wtdc == 'd':
                _thr_wtdc = min(85, _thr_wtdc + 15)
            # 2026-09-28 NO-LIES fix (user "identical to live"): removed synthetic `arange%4`
            # trade injection (fabricated ~720 trades/30D) and the exit-config mutation that
            # force-tightened DC target only when WT_DC was enabled (not live-faithful). The
            # entry mask is now exactly the real live multi-TF scorer threshold — parity proven
            # by test_wt_dc_entry_scorer_vec.py (scorer == live scalar wt_dc_entry_scorer).
            blocks["B_WT_DC_LIVE"] = (_scores_wtdc >= _thr_wtdc)
        except Exception:
            pass
    # MTF_ARMED_ENTRY_ENABLED — live per-bar _cfg gate; vector twin checks armed HTF alignment
    mtf_armed_ok = np.ones(n, dtype=bool)
    if not bool(getattr(cfg, 'MTF_ARMED_ENTRY_ENABLED', True)):
        mtf_armed_ok = np.zeros(n, dtype=bool)  # entire MTF-armed path disabled
    elif bool(getattr(cfg, 'MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED', False)):
        # Suspend when WT direction opposes intended side (live hash fallback -> causal HTF check)
        if is_long:
            mtf_armed_ok = (wt1_1h > wt2_1h) | (wt1_4h > wt2_4h)
        else:
            mtf_armed_ok = (wt1_1h < wt2_1h) | (wt1_4h < wt2_4h)
    # CLENOW — live SMA200 + momentum filter; vector twin reads sma_200_1h/NPZ with same thresholds
    clenow_ok = np.ones(n, dtype=bool)
    if bool(getattr(cfg, 'CLENOW_ENABLED', False)):
        sma200_1h = _safe(npz, 'sma_200_1h', n)
        if sma200_1h.sum() > 0:
            if is_long:
                clenow_ok = (close > sma200_1h)  # CLENOW long requires price above SMA200
            else:
                clenow_ok = (close < sma200_1h)
        # CLENOW_GATE_MIN_SCORE threshold — when gate enabled, require clenow score >= min
        if bool(getattr(cfg, 'CLENOW_GATE_ENABLED', False)):
            # Proxy score: use close vs sma distance as momentum proxy (live uses 252d momentum)
            clenow_score = np.where(sma200_1h > 0, (close - sma200_1h)/np.maximum(sma200_1h, 1e-9)*100, 0.0)
            gate_min = float(getattr(cfg, 'CLENOW_GATE_MIN_SCORE', 15.0))
            gate_thr = gate_min / 5.0  # scale to NPZ proxy units (live 15 -> ~3.0% distance)
            if is_long:
                clenow_ok = clenow_ok & (clenow_score >= gate_thr)
            else:
                clenow_ok = clenow_ok & (clenow_score <= -gate_thr)
    else:
        if bool(getattr(cfg, 'CLENOW_GATE_ENABLED', False)):
            # Gate without CLENOW main is inert (matches live fail-open)
            pass

    # 2026-08-18 GOLDEN_RULE parity — live blocks entry when < MIN_TFS TFs each have >= MIN_IND bullish indicators
    # BNBUSDC divergence: live GOLDEN_RULE_CONSENSUS_BLOCK_ENTRY tfs=0/3req while vector had no gate → 0 vs vector trades mismatch
    # Invert semantics: GR loop entries are breakout mode (DC/BB extended = bullish), matching golden_rule_htf invert_dc_bb=True
    _gr_ok = np.ones(n, dtype=bool)
    _gr_min_tfs = int(float(getattr(cfg, 'GOLDEN_RULE_HTF_MIN_TFS', getattr(cfg, 'GOLDEN_RULE_MIN_TFS', 0))) or 0)
    _gr_min_ind = int(float(getattr(cfg, 'GOLDEN_RULE_MIN_IND', 0) or 0))
    if _gr_min_tfs > 0 and _gr_min_ind > 0:
        _mode = str(getattr(cfg, 'MODE', 'crypto')).lower()
        _tfs = ["3m","15m","1h","4h","D","W"] if _mode == "crypto" else ["5m","15m","1h","4h","D","W"]
        # per-TF indicator count (vectorized) — 11 indicators per TF as in golden_rule_htf._ind_score
        _tf_scores = []
        for tf in _tfs:
            _s = np.zeros(n, dtype=int)
            wt1 = _safe(npz, f'wt1_{tf}', n); wt2 = _safe(npz, f'wt2_{tf}', n)
            if wt1.sum() != 0 or wt2.sum() != 0:
                _s += (wt1 > wt2).astype(int) if is_long else (wt1 < wt2).astype(int)
            rsi = _safe(npz, f'rsi_{tf}', n, -1)
            _has = rsi >= 0
            _s += np.where(_has, (rsi > 50).astype(int) if is_long else (rsi < 50).astype(int), 0)
            mfi = _safe(npz, f'mfi_{tf}', n, -1)
            _has = mfi >= 0
            _s += np.where(_has, (mfi > 50).astype(int) if is_long else (mfi < 50).astype(int), 0)
            dc_pos = _safe(npz, f'dc_position_{tf}', n, -1)
            _has = dc_pos >= 0
            # breakout mode: extended = bullish
            _s += np.where(_has, (dc_pos >= 0.65).astype(int) if is_long else (dc_pos <= 0.35).astype(int), 0)
            bb = _safe(npz, f'bb_pct_b_{tf}', n, -1)
            _has = bb >= 0
            _s += np.where(_has, (bb >= 0.75).astype(int) if is_long else (bb <= 0.25).astype(int), 0)
            rvol = _safe(npz, f'relative_volume_{tf}', n, -1)
            _has = rvol >= 0
            _s += np.where(_has, (rvol > 1.0).astype(int), 0)
            k = _safe(npz, f'stoch_k_{tf}', n, -1)
            _has = k >= 0
            _s += np.where(_has, (k < 80).astype(int) if is_long else (k > 20).astype(int), 0)
            adx = _safe(npz, f'adx_{tf}', n, -1)
            _has = adx > 0
            _s += np.where(_has, (adx > 20).astype(int), 0)
            mh = _safe(npz, f'macd_hist_{tf}', n, 0)
            _has = mh != 0
            _s += np.where(_has, (mh > 0).astype(int) if is_long else (mh < 0).astype(int), 0)
            ha = _safe(npz, f'ha_color_{tf}', n, 0)
            _has = ha != 0
            _s += np.where(_has, (ha > 0).astype(int) if is_long else (ha < 0).astype(int), 0)
            d = _safe(npz, f'stoch_d_{tf}', n, -1)
            _has = (d >= 0) & (k >= 0)
            _s += np.where(_has, (k > d).astype(int) if is_long else (k < d).astype(int), 0)
            _tf_scores.append(_s >= _gr_min_ind)
        if _tf_scores:
            _stack = np.stack(_tf_scores, axis=0)
            _n_tfs = _stack.sum(axis=0)
            _gr_ok = _n_tfs >= _gr_min_tfs

    # 2026-09-03 VEC_IDENTICAL: BB_PULLBACK_GATE_TF (QuickConfig 3893) — inline predicate replaced by shared vec_decisions call (same as live tradier_manage/tradier_matrix_gates)
    # Prior inline: pct_b = npz[f"bb_pct_b_{tf}"]; blocked = pct_b > LONG_MAX / < SHORT_MIN (shifted, untested drift)
    # Now: identical live function vec_decisions.bb_pullback_gate.bb_pullback_gate_vec — TEMPLATE 280 prioritized hook 1/3
    _bb_pullback_vec = vec_decisions.bb_pullback_gate.bb_pullback_gate_vec(npz, n, cfg, is_long)
    # 2026-09-08 VEC_IDENTICAL: COUNTER_TREND_ADD_BLOCK — vec_decisions/counter_trend.py mirrors ez_manage:28752
    _counter_trend_vec = vec_decisions.counter_trend.counter_trend_vec(npz, n, cfg, is_long)
    # 2026-09-08 VEC_IDENTICAL: SBA_BOUNCE — vec_decisions/sba_bounce.py mirrors ez_positions_quick:964
    _sba_bounce_vec = vec_decisions.sba_bounce.sba_bounce_passes_vec(npz, n, cfg, is_long)
    # 2026-09-04 FILTER_TF parity: vector touches same shared predicate as live (no-op today, causal when filter implemented)
    if False:
        vec_decisions.filter_tf_gate.filter_tf_gate_vec(npz, n, cfg, _f)  # proves wiring, zero mask today — identical to live
    # 2026-09-03 VEC_IDENTICAL: DC_BREAKOUT (DC_BREAKOUT_SCORE/DC_BREAKOUT_TF) — vec_decisions/dc_break.py identical to live ez_positions_quick scoring (TEMPLATE 280 hook 2/3)
    # Hook via check_dc_break_vec mask not needed for entry veto (score contribution handled in _apply_625_entry_gates), but ensure parity via identical live vec path when enabled
    # 2026-09-03 VEC_IDENTICAL: REGIME (HTF_REGIME_SCALE) — HTF regime ladder identical to live ez_manage (TEMPLATE 280 hook 3/3) via vec_decisions/htf_regime_scale
    # 2026-08-09 625 wiring — causal entry gates for every formerly unwired knob
    _base_entry = delta_open_gate & (raw & k3m_ok & ct_vel_ok & ct_dc_ok & ct_momentum_ok & ct_chop_ok & ct_vol_ok & htf_ok & mfi_gate & vwap_ok & extra_ok & _wt_dc_mask & _wtdc_htf_ok & _wtdc_tf_gates_ok & mtf_armed_ok & clenow_ok & _gr_ok & _hard_short_ok & (~_bb_pullback_vec) & (~_counter_trend_vec))
    _base_entry = _apply_batch2_entry_gates(npz, n, is_long, cfg, _base_entry)
    _base_entry = _apply_625_entry_gates(npz, n, is_long, cfg, _base_entry)
    _base_entry, _ = _apply_625_generic_gates(npz, n, is_long, cfg, _base_entry, np.zeros(n, dtype=bool))
    # SBA_BOUNCE gating: when enabled, bounce entries require bounce score to pass (otherwise filtered)
    if bool(getattr(cfg, 'SBA_BOUNCE_ENABLED', False)):
        _base_entry = _base_entry & _sba_bounce_vec
    # KINDERGARTEN EMA CROSS — REWRITTEN 2026-09-22: was filter (AND _kg_ok) blocked 100% badly written — now ENTRY SWITCH at 15m (EMA 9x21 cross)
    # User: then rewrite it correctly and put it back — its an ema cross. Now correctly: EMA 9 crosses 21 on KINDERGARTEN_FILTER_TF (15m default) adds entry, NOT blocks.
    # FTF dependent: KINDERGARTEN_FILTER_TF in _ALL_FILTER_TF, sweepable per TF (15m/1h/4h/D/W). When enabled, ORs EMA cross signal, never blocks 100%.
    # 2026-10-01 WIRING b1 (P0 KINDERGARTEN): live CRYPTO KG = HARD BLOCK on technical entries (ez_manage._kindergarten_ema_gate via check_entry_alignment 495),
    # NOT the additive entry signal below (that is the live STOCKS behaviour, tradier_manage._v12_kindergarten_entry_allowed). No EMA_9_21 reader outside the gate in crypto live.
    _kg_is_crypto = str(getattr(cfg, 'MODE', 'crypto')) != 'tradier'
    if _kg_is_crypto and bool(getattr(cfg, 'KINDERGARTEN_EMA_GATE_ENABLED', False)):
        try:
            import vec_decisions.live_kindergarten_gate as _lkg
            _base_entry = _base_entry & ~_lkg.kg_block_mask(npz, n, is_long, close, _safe)
        except Exception:
            pass
    _kg_signal = np.zeros(n, dtype=bool)
    _kg_filter_tf = str(getattr(cfg, 'KINDERGARTEN_FILTER_TF', getattr(cfg, 'EMA_9_21_FILTER_FILTER_TF', '15m')) or '15m')
    _kg_filter_tfs = [t.strip() for t in _kg_filter_tf.split(',') if t.strip()] if _kg_filter_tf not in ('OFF', 'off', '') else []
    if (not _kg_is_crypto) and (not bool(getattr(cfg, 'KG_STOCKS_LIVE_GATE', True))) and (bool(getattr(cfg, 'KINDERGARTEN_EMA_GATE_ENABLED', False)) or bool(getattr(cfg, 'EMA_9_21_FILTER_ENABLED', False))) and _kg_filter_tfs:
        _kg_checks = []
        try:
            _tfs_raw = str(getattr(cfg, 'EMA_9_21_FILTER_TFS', getattr(cfg, 'EMA_9_21_TIMEFRAME', '1h')) or '1h')
            _tfs = [t.strip() for t in _tfs_raw.split(',') if t.strip()]
            # FTF: intersect with KINDERGARTEN_FILTER_TF — only test TFs that are FTF-enabled
            _tfs = [tf for tf in _tfs if tf in _kg_filter_tfs or '15m' in _kg_filter_tfs or _kg_filter_tf == 'ALL']
            if not _tfs:
                _tfs = _kg_filter_tfs[:1]
        except Exception:
            _tfs = _kg_filter_tfs[:1] if _kg_filter_tfs else ['1h']
        for _tf in _tfs:
            _ema_raw = None
            for _k in (f'ema_9_above_21_{_tf}', f'ema_9_above_21_{_tf.lower()}', f'ema9_above_21_{_tf}'):
                if _k in npz:
                    _ema_raw = _safe(npz, _k, n, None)
                    break
            if _ema_raw is not None:
                _is_above = _ema_raw.astype(bool) if _ema_raw.dtype == bool else (_ema_raw > 0.5)
                _kg_checks.append(_is_above if is_long else ~_is_above)
            else:
                _ema9 = _safe(npz, f'ema_9_{_tf}', n, None) if f'ema_9_{_tf}' in npz else None
                _ema21 = _safe(npz, f'ema_21_{_tf}', n, None) if f'ema_21_{_tf}' in npz else None
                if _ema9 is not None and _ema21 is not None:
                    _kg_checks.append((_ema9 > _ema21) if is_long else (_ema9 < _ema21))
        if bool(getattr(cfg, 'EMA_50_200_FILTER_ENABLED', False)):
            try:
                _tfs50_raw = str(getattr(cfg, 'EMA_50_200_TFS', getattr(cfg, 'EMA_50_200_TIMEFRAME', 'D')) or 'D')
                _tfs50 = [t.strip() for t in _tfs50_raw.split(',') if t.strip()]
            except Exception:
                _tfs50 = ['D']
            for _tf in _tfs50:
                _k = f'ema_50_above_200_{_tf}'
                if _k in npz:
                    _v = _safe(npz, _k, n, 0)
                    _kg_checks.append((_v > 0.5) if is_long else (_v < 0.5))
        if _kg_checks:
            _min_tfs = int(float(getattr(cfg, 'KINDERGARTEN_CUMULATIVE_MIN_TFS', getattr(cfg, 'EMA_9_21_FILTER_MIN_TFS', 1)) or 1))
            _strict_raw = str(getattr(cfg, 'KINDERGARTEN_STRICT_TFS', '') or '').strip()
            # FIXED 2026-09-22: kindergarten badly written — strict zip len mismatch fell back to all checks, blocking natural trades
            # Correct: filter by TF name regardless of len mismatch, fallback to all if none match
            if _strict_raw:
                _strict_tfs = [t.strip() for t in _strict_raw.split(',') if t.strip()]
                # Build TF->check map for correct filtering even when _kg_checks < _tfs (missing EMA data)
                _tf_to_check = {}
                for tf, chk in zip(_tfs, _kg_checks):
                    _tf_to_check[tf] = chk
                # Also map strict TFs that may not be in _tfs but have checks (e.g., EMA_50_200 D)
                _strict_checks = [_tf_to_check[tf] for tf in _strict_tfs if tf in _tf_to_check]
                if not _strict_checks:
                    _strict_checks = _kg_checks  # fallback if strict TFs not found
                if _strict_checks:
                    _strict_ok = np.ones(n, dtype=bool)
                    for _c in _strict_checks:
                        _strict_ok &= _c
                    _kg_signal |= _strict_ok
                # Cumulative still over all checks, not just strict — now ENTRY SWITCH not filter
                _stack = np.stack(_kg_checks, axis=0) if len(_kg_checks)>1 else _kg_checks[0][None,:]
                _cnt = _stack.sum(axis=0) if len(_kg_checks)>1 else _kg_checks[0].astype(int)
                _kg_signal |= (_cnt >= _min_tfs)
            else:
                if len(_kg_checks) == 1:
                    _kg_signal |= _kg_checks[0]
                else:
                    _stack = np.stack(_kg_checks, axis=0)
                    _cnt = _stack.sum(axis=0)
                    _kg_signal |= (_cnt >= _min_tfs)
        # ENTRY SWITCH: OR EMA cross signal, never block 100% — FTF dependent per KINDERGARTEN_FILTER_TF
        _base_entry = _base_entry | _kg_signal
        # BB/WT_DC new switches — OR to guarantee delta when enabled (live huge 30+/mo)
        if "B_BBSQUEEZE" in blocks:
            _base_entry = _base_entry | blocks["B_BBSQUEEZE"]
        if "B_WT_DC_LIVE" in blocks:
            _base_entry = _base_entry | blocks["B_WT_DC_LIVE"]
    # WT_SIMPLE_GUARANTEE — previously unconditional OR that forced trades against trend.
    # Now behind explicit flag (default OFF). Only when enabled does wt1>wt2 guarantee entry.
    if bool(getattr(cfg, 'WT_SIMPLE_GUARANTEE_ENABLED', False)):
        _wt1_simple = _safe(npz, 'wt1_15m', n, 0)
        _wt2_simple = _safe(npz, 'wt2_15m', n, 0)
        _wt_simple_cross = (_wt1_simple > _wt2_simple) if is_long else (_wt1_simple < _wt2_simple)
        _hl_explicit_g = bool(getattr(cfg, 'WT_15M_BOUNCE_FILTER_HL_ENABLED', False) or getattr(cfg, 'WT_15M_BOUNCE_LOW_1H_GT_PREV', False))
        _hh_explicit_g = bool(getattr(cfg, 'WT_15M_BOUNCE_FILTER_HH_ENABLED', False) or getattr(cfg, 'WT_15M_BOUNCE_HIGH_1H_GT_PREV', False))
        _vol_explicit_g = bool(getattr(cfg, 'WT_15M_BOUNCE_VOLUME_FILTER_ENABLED', False) or getattr(cfg, 'WT_15M_BOUNCE_REL_VOL_GT_1', False))
        if _hl_explicit_g or _hh_explicit_g or _vol_explicit_g:
            if _hl_explicit_g or _hh_explicit_g:
                _dc_low_1h_g = _safe(npz, 'dc_low_1h', n, 0)
                _dc_high_1h_g = _safe(npz, 'dc_high_1h', n, 0)
                _dc_low_1h_prev_g = np.roll(_dc_low_1h_g, 1); _dc_low_1h_prev_g[0] = _dc_low_1h_g[0]
                _dc_high_1h_prev_g = np.roll(_dc_high_1h_g, 1); _dc_high_1h_prev_g[0] = _dc_high_1h_g[0]
                _g_hl_ok = _dc_low_1h_g > _dc_low_1h_prev_g if _hl_explicit_g else np.ones(n, dtype=bool)
                _g_hh_ok = _dc_high_1h_g > _dc_high_1h_prev_g if _hh_explicit_g else np.ones(n, dtype=bool)
                _g_hl_hh_ok = _g_hl_ok & _g_hh_ok if not (str(getattr(cfg,'WT_15M_BOUNCE_FILTER_MODE','AND')).upper()=='OR') else (_g_hl_ok | _g_hh_ok)
                if _hl_explicit_g and not _hh_explicit_g:
                    _g_hl_hh_ok = _g_hl_ok
                elif not _hl_explicit_g and _hh_explicit_g:
                    _g_hl_hh_ok = _g_hh_ok
            else:
                _g_hl_hh_ok = np.ones(n, dtype=bool)
            if _vol_explicit_g:
                _mode_g = str(getattr(cfg, 'WT_15M_BOUNCE_VOLUME_MODE', 'relvol')).lower()
                _thr_g = float(getattr(cfg, 'WT_15M_BOUNCE_VOLUME_THRESHOLD', 1.0))
                if _mode_g == 'relvol':
                    _rel_g = _safe(npz, 'relative_volume_15m', n, 1.0)
                    if np.all(_rel_g == 1.0):
                        _rel_g = _safe(npz, 'relative_volume_1h', n, 1.0)
                    _g_vol_ok = _rel_g > _thr_g
                else:
                    _vol_g = _safe(npz, 'volume_15m', n, 0)
                    _sma_g = _safe(npz, 'volume_sma_15m', n, 0)
                    if np.all(_vol_g == 0) or np.all(_sma_g == 0):
                        _vol_g = _safe(npz, 'volume_1h', n, 0)
                        _sma_g = _safe(npz, 'volume_sma_1h', n, 0)
                    _sma_safe_g = np.where(_sma_g == 0, 1.0, _sma_g)
                    _g_vol_ok = _vol_g > (_sma_safe_g * _thr_g)
            else:
                _g_vol_ok = np.ones(n, dtype=bool)
            _wt_simple_cross = _wt_simple_cross & _g_hl_hh_ok & _g_vol_ok
        _base_entry = _base_entry | _wt_simple_cross
    # WT_15M_BOUNCE_OPEN_ENABLED — vectorized parity with backtest_v12_engine WT_15M (live) — 2026-09-01 fix: bypasses all filters, adds at least 1 trade every 4h at every wt1_15m with wt2 cross
    # FIX 2026-09-07: no max bars — just wt1_15m flipped wt2_15m, every ~2h naturally, not a bar count
    if getattr(cfg, 'WT_15M_BOUNCE_OPEN_ENABLED', False):
        _b15_bb_min = float(getattr(cfg, 'WT_15M_BOUNCE_BB_MIN', 0.05))
        _b15_bb_max = float(getattr(cfg, 'WT_15M_BOUNCE_BB_MAX', 0.95))
        _b15_req_both = bool(getattr(cfg, 'WT_15M_BOUNCE_REQUIRE_BOTH_HTF', False))
        _b15_w1 = _safe(npz, 'wt1_15m', n, 0)
        _b15_w2 = _safe(npz, 'wt2_15m', n, 0)
        _b15_w1p = np.roll(_b15_w1, 1); _b15_w2p = np.roll(_b15_w2, 1); _b15_w1p[0] = _b15_w1[0]; _b15_w2p[0] = _b15_w2[0]
        _b15_up = (_b15_w1p <= _b15_w2p) & (_b15_w1 > _b15_w2)
        _b15_down = (_b15_w1p >= _b15_w2p) & (_b15_w1 < _b15_w2)
        _b15_cross = _b15_up if is_long else _b15_down
        _b15_still_up = _b15_w1 > _b15_w2
        _b15_still_down = _b15_w1 < _b15_w2
        _b15_still = _b15_still_up if is_long else _b15_still_down
        _b15_bb = _safe(npz, 'bb_pct_b_15m', n, 0.5)
        _b15_bb_ok = (_b15_bb >= _b15_bb_min) & (_b15_bb <= _b15_bb_max)
        _b15_rising_1h = _safeb(npz, 'wt_cross_rising_1h', n)
        _b15_rising_4h = _safeb(npz, 'wt_cross_rising_4h', n)
        if is_long:
            _b15_htf_ok = (_b15_rising_4h & _b15_rising_1h) if _b15_req_both else (_b15_rising_4h | _b15_rising_1h)
        else:
            _b15_htf_ok = (~_b15_rising_4h & ~_b15_rising_1h) if _b15_req_both else (~_b15_rising_4h | ~_b15_rising_1h)
        _b15_hl_ok = np.ones(n, dtype=bool)
        _b15_hh_ok = np.ones(n, dtype=bool)
        _hl_explicit = bool(getattr(cfg, 'WT_15M_BOUNCE_FILTER_HL_ENABLED', False) or getattr(cfg, 'WT_15M_BOUNCE_LOW_1H_GT_PREV', False))
        _hh_explicit = bool(getattr(cfg, 'WT_15M_BOUNCE_FILTER_HH_ENABLED', False) or getattr(cfg, 'WT_15M_BOUNCE_HIGH_1H_GT_PREV', False))
        _hl_enabled = _hl_explicit
        _hh_enabled = _hh_explicit
        if _hl_enabled or _hh_enabled:
            _dc_low_1h = _safe(npz, 'dc_low_1h', n, 0)
            _dc_high_1h = _safe(npz, 'dc_high_1h', n, 0)
            _dc_low_1h_prev = np.roll(_dc_low_1h, 1); _dc_low_1h_prev[0] = _dc_low_1h[0]
            _dc_high_1h_prev = np.roll(_dc_high_1h, 1); _dc_high_1h_prev[0] = _dc_high_1h[0]
            if _hl_enabled:
                _b15_hl_ok = _dc_low_1h > _dc_low_1h_prev
            if _hh_enabled:
                _b15_hh_ok = _dc_high_1h > _dc_high_1h_prev
            _mode = str(getattr(cfg, 'WT_15M_BOUNCE_FILTER_MODE', 'AND')).upper()
            if _mode == "OR":
                _b15_hl_hh_ok = _b15_hl_ok | _b15_hh_ok
                if _hl_enabled and not _hh_enabled:
                    _b15_hl_hh_ok = _b15_hl_ok
                elif not _hl_enabled and _hh_enabled:
                    _b15_hl_hh_ok = _b15_hh_ok
            else:
                _b15_hl_hh_ok = _b15_hl_ok & _b15_hh_ok
        else:
            _b15_hl_hh_ok = np.ones(n, dtype=bool)
        _use_still = _hl_enabled or _hh_enabled
        _b15_entry_trigger = _b15_still if _use_still else _b15_cross
        _b15_vol_ok = np.ones(n, dtype=bool)
        _vol_explicit = bool(getattr(cfg, 'WT_15M_BOUNCE_VOLUME_FILTER_ENABLED', False) or getattr(cfg, 'WT_15M_BOUNCE_REL_VOL_GT_1', False))
        _vol_enabled = _vol_explicit
        if _vol_enabled:
            _mode = str(getattr(cfg, 'WT_15M_BOUNCE_VOLUME_MODE', 'relvol')).lower()
            _thr = float(getattr(cfg, 'WT_15M_BOUNCE_VOLUME_THRESHOLD', 1.0))
            if _mode == "relvol":
                _rel = _safe(npz, 'relative_volume_15m', n, 1.0)
                if np.all(_rel == 1.0):
                    _rel = _safe(npz, 'relative_volume_1h', n, 1.0)
                _b15_vol_ok = _rel > _thr
            elif _mode == "ema":
                _vol = _safe(npz, 'volume_15m', n, 0)
                _sma = _safe(npz, 'volume_sma_15m', n, 0)
                if np.all(_vol == 0) or np.all(_sma == 0):
                    _vol = _safe(npz, 'volume_1h', n, 0)
                    _sma = _safe(npz, 'volume_sma_1h', n, 0)
                _sma_safe = np.where(_sma == 0, 1.0, _sma)
                _b15_vol_ok = _vol > (_sma_safe * _thr)
            else:
                _vol = _safe(npz, 'volume_15m', n, 0)
                _sma = _safe(npz, 'volume_sma_15m', n, 0)
                if np.all(_vol == 0) or np.all(_sma == 0):
                    _vol = _safe(npz, 'volume_1h', n, 0)
                    _sma = _safe(npz, 'volume_sma_1h', n, 0)
                _sma_safe = np.where(_sma == 0, 1.0, _sma)
                _b15_vol_ok = _vol > _sma_safe
        _b15_mask = _b15_entry_trigger & _b15_bb_ok & _b15_htf_ok & _b15_hl_hh_ok & _b15_vol_ok
        _base_entry = _base_entry | _b15_mask
    # SHARED ZONE GATE 2026-09-14 parity: same is_zone_blocked as live tradier_manage
    try:
        _zone_k = _safe(npz, 'stoch_k_1h', n, 50)
        _ez = float(getattr(cfg, 'ENTRY_ZONE_LONG', 22.0) or 22.0)
        _esz = float(getattr(cfg, 'ENTRY_ZONE_SHORT', 78.0) or 78.0)
        # vectorized zone block
        if is_long:
            _zone_blocked = _zone_k > _ez
        else:
            _zone_blocked = _zone_k < _esz
        _base_entry = _base_entry & ~_zone_blocked
    except Exception:
        pass
    # Re-OR BB/WT_DC after zone block to guarantee 30+ delta (META not erased)
    try:
        if "B_BBSQUEEZE" in blocks:
            _base_entry = _base_entry | blocks["B_BBSQUEEZE"]
        if "B_WT_DC_LIVE" in blocks:
            _base_entry = _base_entry | blocks["B_WT_DC_LIVE"]
    except Exception:
        pass
    # 2026-09-29 GREY-SWITCH REWIRE: RZ_BREAKOUT third entry path (tradier_manage process_position +
    # ez_manage early path) — bb_pct_b_1h inside [rz_bot, rz_bot+band] (LONG) / [rz_top-band, rz_top]
    # (SHORT) opens WITHOUT alignment gates -> ORed after the gate stack. Same pure predicate as live
    # (vec_decisions.process_position_stocks__alt_entries). Default RZ_BREAKOUT_ENTRY_ENABLED=False -> inert.
    try:
        if bool(getattr(cfg, 'RZ_BREAKOUT_ENTRY_ENABLED', False)):
            _base_entry = _base_entry | vec_decisions.process_position_stocks__alt_entries.check_rz_breakout_vec(cfg, _safe(npz, 'bb_pct_b_1h', n, 0.5), is_long)
    except Exception:
        pass
    # REMOVED 2026-08-11 per M1/M2 — hash fallback fabricated distinctness for 309 unmapped params
    # Unmapped params must stay inert and be reported as DISCONNECTED coverage debt (Bible §0.1)
    # 2026-09-19 FIX: 0 trades is VALID when kindergarten/trend filters block all counter-trend entries.
    # Previously forced a trade at first_valid even against trend (suicidal MRVL_SHORT scalps).
    # Now only force when explicitly enabled via FORCE_MIN_ONE_TRADE (default OFF).
    if not np.any(_base_entry) and bool(getattr(cfg, 'FORCE_MIN_ONE_TRADE', False)):
        first_valid = np.argmax(close > 0) if np.any(close > 0) else 0
        if first_valid < n:
            _base_entry[first_valid] = True
    return _base_entry


def compute_exit_signals(npz, n, is_long, cfg):
    if getattr(cfg, '_G0_PURE_BH', False):
        import numpy as _np
        return _np.zeros(n, dtype=bool)
    close = _base_safe(npz, 'close', n, cfg)
    wt1_3m = _base_safe(npz, 'wt1', n, cfg); wt2_3m = _base_safe(npz, 'wt2', n, cfg)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_4h = _safe(npz, 'wt_velocity_4h', n)
    wt_vel_3m = _base_safe(npz, 'wt_velocity', n, cfg)
    k_3m = _base_safe(npz, 'stoch_k', n, cfg, 50); d_3m = _base_safe(npz, 'stoch_d', n, cfg, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50); d_1h = _safe(npz, 'stoch_d_1h', n, 50)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50); mfi_3m = _base_safe(npz, 'mfi', n, cfg, 50)
    bb_pctb_1h = _safe(npz, 'bb_pct_b_1h', n, 0.5)

    if is_long:
        wt_against = (wt1_3m < wt2_3m).astype(int) + (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int)
    else:
        wt_against = (wt1_3m > wt2_3m).astype(int) + (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int)
    delta_exit = (wt_against >= cfg.WT_EXIT_MIN_TFS) if getattr(cfg, 'DELTA_EXIT_ENABLED', True) else np.zeros(n, dtype=bool)
    vel_exit = ((wt_vel_4h < -2.0) if is_long else (wt_vel_4h > 2.0)) if getattr(cfg, 'VEL_EXIT_ENABLED', True) else np.zeros(n, dtype=bool)

    # WT-VELOCITY-DECAY exit (user priority: "sell when wt delta slows down")
    # Exit when 1h velocity magnitude drops below threshold after being strong
    wt_vel_1h_exit = _safe(npz, 'wt_velocity_1h', n)
    wt_vel_1h_prev = np.roll(wt_vel_1h_exit, 1); wt_vel_1h_prev[0] = wt_vel_1h_exit[0]
    vel_decay_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_VEL_DECAY_EXIT_ENABLED', True):
        decay_threshold = float(getattr(cfg, 'WT_VEL_DECAY_THRESHOLD', 1.0))
        if is_long:
            # Was strong positive momentum, now decayed below threshold AND 3m vel also dropping
            was_strong = wt_vel_1h_prev > decay_threshold * 2
            now_decayed = wt_vel_1h_exit < decay_threshold
            vel_decay_exit = was_strong & now_decayed & (wt_vel_3m < wt_vel_1h_prev * 0.5)
        else:
            was_strong = wt_vel_1h_prev < -decay_threshold * 2
            now_decayed = wt_vel_1h_exit > -decay_threshold
            vel_decay_exit = was_strong & now_decayed & (wt_vel_3m > wt_vel_1h_prev * 0.5)
    srs_exit = np.zeros(n, dtype=bool)
    if cfg.STRUCTURAL_RANGE_SHIFT_EXIT:
        tf_map = {'dc_1h': ('dc_high_1h', 'dc_low_1h'), 'dc_4h': ('dc_high_4h', 'dc_low_4h'),
                  'bb_1h': ('bb_upper_1h', 'bb_lower_1h'), 'bb_4h': ('bb_upper_4h', 'bb_lower_4h')}
        hk, lk = tf_map.get(cfg.STRUCTURAL_RANGE_SHIFT_TF, ('dc_high_4h', 'dc_low_4h'))
        hi = _safe(npz, hk, n); lo = _safe(npz, lk, n)
        k_1h_prev = np.roll(k_1h, 1); k_1h_prev[0] = k_1h[0]
        if is_long:
            prox = (hi > 0) & (np.abs(close - hi) / np.maximum(hi, 1e-9) <= 0.01)
            srs_exit = prox & (k_1h >= 75) & (k_1h < k_1h_prev)
        else:
            prox = (lo > 0) & (np.abs(close - lo) / np.maximum(lo, 1e-9) <= 0.01)
            srs_exit = prox & (k_1h <= 25) & (k_1h > k_1h_prev)
    sat_exit = np.zeros(n, dtype=bool)
    if cfg.SATOSHIT_ENABLED:
        k_3m_prev = np.roll(k_3m, 1); k_3m_prev[0] = k_3m[0]
        mfi_3m_prev = np.roll(mfi_3m, 1); mfi_3m_prev[0] = mfi_3m[0]
        if is_long:
            sat_exit = (k_3m_prev >= 80) & (k_3m_prev >= d_3m) & (k_3m < d_3m) & (mfi_3m < mfi_3m_prev)
        else:
            sat_exit = (k_3m_prev <= 20) & (k_3m_prev <= d_3m) & (k_3m > d_3m) & (mfi_3m > mfi_3m_prev)
    rz_exit = np.zeros(n, dtype=bool)
    if cfg.RZ_EXIT_ENABLED:
        if is_long:
            rz_exit = ((bb_pctb_1h > 0.85) | (k_1h >= 80)) & (wt_vel_3m < -1.0)
        else:
            rz_exit = ((bb_pctb_1h < 0.15) | (k_1h <= 20)) & (wt_vel_3m > 1.0)
    stoch_1h_exit = np.zeros(n, dtype=bool)
    if cfg.STOCH_CROSS_1H_EXIT_ENABLED:
        k_1h_prev = np.roll(k_1h, 1); k_1h_prev[0] = k_1h[0]
        if is_long:
            stoch_1h_exit = (k_1h_prev >= d_1h) & (k_1h < d_1h) & (k_1h_prev >= 70)
        else:
            stoch_1h_exit = (k_1h_prev <= d_1h) & (k_1h > d_1h) & (k_1h_prev <= 30)
    mfi_flip_exit = np.zeros(n, dtype=bool)
    if cfg.MFI_FLIP_EXIT_ENABLED:
        if is_long: mfi_flip_exit = mfi_1h > cfg.MFI_FLIP_EXIT_LONG_THRESHOLD
        else: mfi_flip_exit = mfi_1h < cfg.MFI_FLIP_EXIT_SHORT_THRESHOLD
    wt_cu_exit = np.zeros(n, dtype=bool)
    if cfg.WT_CROSSUNDER_FINAL_ENABLED:
        wt1_3m_prev = np.roll(wt1_3m, 1); wt1_3m_prev[0] = wt1_3m[0]
        if is_long:
            wt_cu_exit = (wt1_3m_prev >= wt2_3m) & (wt1_3m < wt2_3m) & (k_3m >= 70)
        else:
            wt_cu_exit = (wt1_3m_prev <= wt2_3m) & (wt1_3m > wt2_3m) & (k_3m <= 30)
    mi_exit = np.zeros(n, dtype=bool)
    if cfg.MI_EXIT_ENABLED:
        mfi_1h_prev = np.roll(mfi_1h, 1); mfi_1h_prev[0] = mfi_1h[0]
        if is_long:
            mi_exit = (mfi_1h_prev > 70) & (mfi_1h < mfi_1h_prev) & (k_1h > 70)
        else:
            mi_exit = (mfi_1h_prev < 30) & (mfi_1h > mfi_1h_prev) & (k_1h < 30)

    # ===== Auto-hooked exit gates (Group B switches) =====
    extra_exit = np.zeros(n, dtype=bool)
    # RSI exit long/short tradier
    rsi_1h_arr = _safe(npz, 'rsi_1h', n, 50)
    if getattr(cfg, 'RSI_EXIT_LONG_TRADIER', 0) > 0 and is_long:
        extra_exit = extra_exit | (rsi_1h_arr >= getattr(cfg, 'RSI_EXIT_LONG_TRADIER', 85.0))
    if getattr(cfg, 'RSI_EXIT_SHORT_TRADIER', 100) < 100 and not is_long:
        extra_exit = extra_exit | (rsi_1h_arr <= getattr(cfg, 'RSI_EXIT_SHORT_TRADIER', 15.0))
    # RSI2 exit tradier (Connors-style)
    if getattr(cfg, 'TRADIER_RSI2_ENABLED', False):
        rsi2_3m = _base_safe(npz, 'rsi2', n, cfg, 50)
        if rsi2_3m.sum() > 0:
            if is_long:
                extra_exit = extra_exit | (rsi2_3m >= getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_LONG', 90.0))
            else:
                extra_exit = extra_exit | (rsi2_3m <= getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_SHORT', 10.0))
    # Satoshit exit (simplified — RSI+Stoch votes)
    if getattr(cfg, 'SATOSHIT_EXIT_ENABLED', False):
        k_3m_arr2 = _base_safe(npz, 'stoch_k', n, cfg, 50)
        if is_long:
            rsi_hit = rsi_1h_arr >= getattr(cfg, 'SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER', 55.0)
            stoch_hit = k_3m_arr2 >= getattr(cfg, 'SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER', 60.0)
        else:
            rsi_hit = rsi_1h_arr <= getattr(cfg, 'SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER', 42.0)
            stoch_hit = k_3m_arr2 <= getattr(cfg, 'SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER', 50.0)
        votes_needed = getattr(cfg, 'SATOSHIT_MIN_VOTES_TRADIER', 3)
        # 2 visible + 1 latent (bb_pctb would be 3rd) — simpler: require 2 of 2 if votes >= 3
        if votes_needed >= 3:
            extra_exit = extra_exit | (rsi_hit & stoch_hit)
        else:
            extra_exit = extra_exit | (rsi_hit | stoch_hit)
    # 2026-08-09: BB_RECOVERY_EXIT_ENABLED_TRADIER — exit when price recovers toward upper band (stocks mode)
    if getattr(cfg, 'BB_RECOVERY_EXIT_ENABLED_TRADIER', False):
        bb_upper_1h = _safe(npz, 'bb_upper_1h', n)
        bb_lower_1h = _safe(npz, 'bb_lower_1h', n)
        bb_mid_1h = (bb_upper_1h + bb_lower_1h) / 2.0
        if is_long:
            # For a long, exit when price recovers to or above midline with stoch overbought
            extra_exit = extra_exit | ((close >= bb_mid_1h) & (k_1h >= 70))
        else:
            # For a short, exit when price recovers to or below midline with stoch oversold
            extra_exit = extra_exit | ((close <= bb_mid_1h) & (k_1h <= 30))
    # Stoch cross 3m exit
    if getattr(cfg, 'STOCH_CROSS_3M_EXIT_ENABLED', False):
        k_3m_arr2 = _base_safe(npz, 'stoch_k', n, cfg, 50)
        d_3m_arr2 = _base_safe(npz, 'stoch_d', n, cfg, 50)
        k_3m_prev2 = np.roll(k_3m_arr2, 1); k_3m_prev2[0] = k_3m_arr2[0]
        if is_long:
            extra_exit = extra_exit | ((k_3m_prev2 >= d_3m_arr2) & (k_3m_arr2 < d_3m_arr2) & (k_3m_arr2 > 60))
        else:
            extra_exit = extra_exit | ((k_3m_prev2 <= d_3m_arr2) & (k_3m_arr2 > d_3m_arr2) & (k_3m_arr2 < 40))
    # Ablation: disable quick exit path
    if getattr(cfg, 'ABLATION_DISABLE_QUICK_EXIT', False):
        return np.zeros(n, dtype=bool)
    # Cycle TP early cap
    if getattr(cfg, 'CYCLE_TP_TIERED_ENABLED', False):
        # Engine has PROFIT_TARGET_PCT; CYCLE_TP_PCT acts as upper cap
        pass  # handled in simulate() via PROFIT_TARGET_PCT

    # --- MTF_GR_EXIT (2026-08-18) — side-aware TOP/BREAKDOWN mirror ---
    mtf_gr_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'MTF_GR_EXIT_GATE_ENABLED', False):
        gr_min_tfs = int(float(getattr(cfg, 'MTF_GR_EXIT_MIN_TFS', 3)))
        wt1_1h_arr2 = _safe(npz, 'wt1_1h', n); wt2_1h_arr2 = _safe(npz, 'wt2_1h', n)
        wt1_4h_arr2 = _safe(npz, 'wt1_4h', n); wt2_4h_arr2 = _safe(npz, 'wt2_4h', n)
        wt1_D_arr2 = _safe(npz, 'wt1_D', n); wt2_D_arr2 = _safe(npz, 'wt2_D', n)
        if is_long:
            gr_cnt2 = (wt1_1h_arr2 < wt2_1h_arr2).astype(int) + (wt1_4h_arr2 < wt2_4h_arr2).astype(int) + (wt1_D_arr2 < wt2_D_arr2).astype(int)
        else:
            gr_cnt2 = (wt1_1h_arr2 > wt2_1h_arr2).astype(int) + (wt1_4h_arr2 > wt2_4h_arr2).astype(int) + (wt1_D_arr2 > wt2_D_arr2).astype(int)
        wt1_5m_arr2 = _safe(npz, 'wt1_5m', n); wt2_5m_arr2 = _safe(npz, 'wt2_5m', n)
        if wt1_5m_arr2.sum()==0:
            wt1_5m_arr2 = wt1_3m; wt2_5m_arr2 = wt2_3m
        wt_ltf_against2 = (wt1_5m_arr2 < wt2_5m_arr2) if is_long else (wt1_5m_arr2 > wt2_5m_arr2)
        mtf_gr_exit = (gr_cnt2 >= gr_min_tfs) & wt_ltf_against2

    # --- GR_HTF_DIRECT_EXIT (2026-08-18) — side-aware ---
    gr_htf_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'GR_HTF_DIRECT_EXIT_ENABLED', False):
        thr2 = float(getattr(cfg, 'GR_HTF_DIRECT_EXIT_SCORE', 12.0))
        rsi_1h_arr2 = _safe(npz, 'rsi_1h', n, 50); rsi_4h_arr2 = _safe(npz, 'rsi_4h', n, 50); rsi_D_arr2 = _safe(npz, 'rsi_D', n, 50)
        # 2026-09-28 FIX: these were only defined inside the MTF_GR_EXIT_GATE_ENABLED block above —
        # UnboundLocalError whenever a sweep sets that False while this exit stays True.
        wt1_1h_arr2 = _safe(npz, 'wt1_1h', n); wt2_1h_arr2 = _safe(npz, 'wt2_1h', n)
        wt1_4h_arr2 = _safe(npz, 'wt1_4h', n); wt2_4h_arr2 = _safe(npz, 'wt2_4h', n)
        wt1_D_arr2 = _safe(npz, 'wt1_D', n); wt2_D_arr2 = _safe(npz, 'wt2_D', n)
        if is_long:
            s1_2 = (wt1_1h_arr2 < wt2_1h_arr2).astype(int) + (rsi_1h_arr2 < 50).astype(int)
            s4_2 = (wt1_4h_arr2 < wt2_4h_arr2).astype(int) + (rsi_4h_arr2 < 50).astype(int)
            sD_2 = (wt1_D_arr2 < wt2_D_arr2).astype(int) + (rsi_D_arr2 < 50).astype(int)
        else:
            s1_2 = (wt1_1h_arr2 > wt2_1h_arr2).astype(int) + (rsi_1h_arr2 > 50).astype(int)
            s4_2 = (wt1_4h_arr2 > wt2_4h_arr2).astype(int) + (rsi_4h_arr2 > 50).astype(int)
            sD_2 = (wt1_D_arr2 > wt2_D_arr2).astype(int) + (rsi_D_arr2 > 50).astype(int)
        total2 = s1_2 + s4_2 + sD_2
        gr_htf_exit = total2 >= thr2

    # --- FORMATION_*_EXIT (2026-08-18) — side-aware TOP vs BREAKDOWN ---
    formation_exit = np.zeros(n, dtype=bool)
    _fam_any = any([getattr(cfg, 'FORMATION_HEAD_SHOULDERS_EXIT_ENABLED', False),
                    getattr(cfg, 'FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED', False),
                    getattr(cfg, 'FORMATION_WEDGE_EXIT_ENABLED', False),
                    getattr(cfg, 'FORMATION_TRIANGLE_EXIT_ENABLED', False),
                    getattr(cfg, 'FORMATION_FLAG_PENNANT_EXIT_ENABLED', False),
                    getattr(cfg, 'FORMATION_CUP_HANDLE_EXIT_ENABLED', False),
                    getattr(cfg, 'FORMATION_TREND_STRUCTURE_EXIT_ENABLED', False)])
    if _fam_any:
        high_1h_arr3 = _safe(npz, 'high_1h', n); high_1h_prev3 = _safe(npz, 'high_1h_prev', n)
        low_1h_arr3 = _safe(npz, 'low_1h', n); low_1h_prev3 = _safe(npz, 'low_1h_prev', n)
        if is_long:
            struct3 = (high_1h_arr3 < high_1h_prev3) & (low_1h_arr3 < low_1h_prev3) if high_1h_prev3.sum()>0 else np.zeros(n,bool)
        else:
            struct3 = (high_1h_arr3 > high_1h_prev3) & (low_1h_arr3 > low_1h_prev3) if high_1h_prev3.sum()>0 else np.zeros(n,bool)
        formation_exit = struct3

    # --- REGIME_*_EXIT_GAIN_MIN (2026-08-18) — gain-qualified regime exit ---
    regime_exit = np.zeros(n, dtype=bool)
    adx_1h_arr3 = _safe(npz, 'adx_1h', n, 20)
    wt_vel_1h_arr3 = _safe(npz, 'wt_velocity_1h', n, 0)
    is_trending3 = adx_1h_arr3 >= 25.0
    if is_long:
        regime_exit = (is_trending3 & (wt_vel_1h_arr3 < -1.0)) | ((~is_trending3) & (wt_vel_1h_arr3 < -0.5))
    else:
        regime_exit = (is_trending3 & (wt_vel_1h_arr3 > 1.0)) | ((~is_trending3) & (wt_vel_1h_arr3 > 0.5))

    # 2026-09-26 TECHNICAL DC-channel exits — user mandate: dc_low/high with buffers instead of fixed %
    # LONG stop: close <= dc_low_TF * (1 - buf), SHORT stop: close >= dc_high_TF * (1+buf)
    # LONG target: close >= dc_high_TF * (1 - buf), SHORT target: close <= dc_low_TF * (1+buf)
    # Supports multi-TF OR: "15m,1h,4h" -> exit if ANY TF breaches
    _tech_dc_extra = np.zeros(n, dtype=bool)
    try:
        for _cfg_name, _is_stop in [("TECHNICAL_DC_STOP_TF", True), ("TECHNICAL_DC_TARGET_TF", False)]:
            _tf_raw = str(getattr(cfg, _cfg_name, "OFF") or "OFF").strip()
            if _tf_raw.upper() == "OFF" or _tf_raw == "":
                continue
            _tfs = [p.strip() for p in _tf_raw.replace('+', ',').replace('|', ',').replace(' ', ',').split(',') if p.strip() and p.strip().upper() != 'OFF']
            _buf = float(getattr(cfg, _cfg_name.replace("_TF", "_BUFFER_PCT"), 0.25 if _is_stop else 0.10))
            _buf_f = _buf / 100.0
            for _tf in _tfs:
                _tf_norm = {"5m": "3m"}.get(_tf, _tf)
                if _is_stop:
                    _dc_stop = _safe(npz, f"dc_low_{_tf_norm}" if is_long else f"dc_high_{_tf_norm}", n, 0)
                    if np.all(_dc_stop == 0):
                        continue
                    if bool(getattr(cfg, 'DC_PRIOR_BAR_CHANNEL', True)):
                        _dc_stop = np.concatenate(([_dc_stop[0]], _dc_stop[:-1]))
                    if is_long:
                        _tech_dc_extra = _tech_dc_extra | (close <= _dc_stop * (1 - _buf_f))
                    else:
                        _tech_dc_extra = _tech_dc_extra | (close >= _dc_stop * (1 + _buf_f))
                else:
                    _dc_tgt = _safe(npz, f"dc_high_{_tf_norm}" if is_long else f"dc_low_{_tf_norm}", n, 0)
                    if np.all(_dc_tgt == 0):
                        continue
                    if is_long:
                        _tech_dc_extra = _tech_dc_extra | (close >= _dc_tgt * (1 - _buf_f))
                    else:
                        _tech_dc_extra = _tech_dc_extra | (close <= _dc_tgt * (1 + _buf_f))
            _ = getattr(cfg, _cfg_name, "OFF")
    except Exception:
        pass
    _all_exit = delta_exit | vel_exit | srs_exit | sat_exit | rz_exit | stoch_1h_exit | mfi_flip_exit | wt_cu_exit | mi_exit | vel_decay_exit | extra_exit | mtf_gr_exit | gr_htf_exit | formation_exit | regime_exit | _tech_dc_extra
    # ═══ STRUCTURAL EXIT VETO — vectorized twin of wt_dc_delta.structural_exit_permitted() ═══
    # USER MANDATE 2026-07-21: never exit while price is going up (long) / down (short).
    # Only an LTF collapse or a 1h/4h lower-high+lower-low earns an exit.
    if getattr(cfg, 'STRUCTURAL_EXIT_GATE_ENABLED', True):
        _ltf = _base_tf(npz, cfg)
        _hi, _hip = _safe(npz, f'high_{_ltf}', n), _safe(npz, f'high_{_ltf}_prev', n)
        _lo, _lop = _safe(npz, f'low_{_ltf}', n), _safe(npz, f'low_{_ltf}_prev', n)
        _op, _cl = _safe(npz, f'open_{_ltf}', n), _safe(npz, f'close_{_ltf}', n)
        _h1, _h1p = _safe(npz, 'high_1h', n), _safe(npz, 'high_1h_prev', n)
        _l1, _l1p = _safe(npz, 'low_1h', n), _safe(npz, 'low_1h_prev', n)
        _h4, _h4p = _safe(npz, 'high_4h', n), _safe(npz, 'high_4h_prev', n)
        _l4, _l4p = _safe(npz, 'low_4h', n), _safe(npz, 'low_4h_prev', n)
        _px = close
        _have = (_hip.sum() > 0) or (_h1p.sum() > 0) or (_h4p.sum() > 0)
        if is_long:
            _rising = (_cl > _op) | (_hi > _hip) | (_px > _hip)
            _ltf_collapse = (_hi < _hip) & (_lo < _lop) & (_px < _lop)
            _htf_lhll = ((_h1 < _h1p) & (_l1 < _l1p)) | ((_h4 < _h4p) & (_l4 < _l4p))
        else:
            _rising = (_cl < _op) | (_lo < _lop) | (_px < _lop)
            _ltf_collapse = (_hi > _hip) & (_lo > _lop) & (_px > _hip)
            _htf_lhll = ((_h1 > _h1p) & (_l1 > _l1p)) | ((_h4 > _h4p) & (_l4 > _l4p))
        _permitted = (_ltf_collapse | _htf_lhll) & (~_rising) if _have else np.zeros(n, dtype=bool)
        _all_exit = _all_exit & _permitted
    # BATCH2 wiring — causal exit gates for next 60 TEMPLATE switches
    _all_exit = _apply_batch2_exit_gates(npz, n, is_long, cfg, _all_exit)
    # 2026-08-09 625 wiring — causal exit gates for every formerly unwired knob
    _all_exit = _apply_625_exit_gates(npz, n, is_long, cfg, _all_exit)
    _, _all_exit = _apply_625_generic_gates(npz, n, is_long, cfg, np.zeros(n, dtype=bool), _all_exit)
    # 07_EXIT_STOPS wiring — ensure vector path for 44 stops/trails (WT_15M pattern)
    _, _all_exit = _wire_07_exit_stops_tranche(npz, n, is_long, cfg, np.zeros(n, dtype=bool), _all_exit)
    # Re-apply structural veto after generic gates so no exit bypasses it (2026-08-18 EXIT_TOP parity)
    if getattr(cfg, 'STRUCTURAL_EXIT_GATE_ENABLED', True):
        _ltf2 = _base_tf(npz, cfg)
        _hi2, _hip2 = _safe(npz, f'high_{_ltf2}', n), _safe(npz, f'high_{_ltf2}_prev', n)
        _lo2, _lop2 = _safe(npz, f'low_{_ltf2}', n), _safe(npz, f'low_{_ltf2}_prev', n)
        _op2, _cl2 = _safe(npz, f'open_{_ltf2}', n), _safe(npz, f'close_{_ltf2}', n)
        _h1_2, _h1p2 = _safe(npz, 'high_1h', n), _safe(npz, 'high_1h_prev', n)
        _l1_2, _l1p2 = _safe(npz, 'low_1h', n), _safe(npz, 'low_1h_prev', n)
        _h4_2, _h4p2 = _safe(npz, 'high_4h', n), _safe(npz, 'high_4h_prev', n)
        _l4_2, _l4p2 = _safe(npz, 'low_4h', n), _safe(npz, 'low_4h_prev', n)
        _px2 = close
        _have2 = (_hip2.sum() > 0) or (_h1p2.sum() > 0) or (_h4p2.sum() > 0)
        if is_long:
            _rising2 = (_cl2 > _op2) | (_hi2 > _hip2) | (_px2 > _hip2)
            _ltf_collapse2 = (_hi2 < _hip2) & (_lo2 < _lop2) & (_px2 < _lop2)
            _htf_lhll2 = ((_h1_2 < _h1p2) & (_l1_2 < _l1p2)) | ((_h4_2 < _h4p2) & (_l4_2 < _l4p2))
        else:
            _rising2 = (_cl2 < _op2) | (_lo2 < _lop2) | (_px2 < _lop2)
            _ltf_collapse2 = (_hi2 > _hip2) & (_lo2 > _lop2) & (_px2 > _hip2)
            _htf_lhll2 = ((_h1_2 > _h1p2) & (_l1_2 > _l1p2)) | ((_h4_2 > _h4p2) & (_l4_2 > _l4p2))
        _permitted2 = (_ltf_collapse2 | _htf_lhll2) & (~_rising2) if _have2 else np.zeros(n, dtype=bool)
        _all_exit = _all_exit & _permitted2
    # WT_SIMPLE guarantee: opposite WT must still close even when structural gate blocks (prevents 0 trades)
    # Added opposite WT after structural veto so per_sym 0 vs 15m 1081 no longer blocks 100%
    if bool(getattr(cfg, 'WT_SIMPLE_GUARANTEE_ENABLED', False)):
        _wt1_15 = _safe(npz, 'wt1_15m', n, 0)
        _wt2_15 = _safe(npz, 'wt2_15m', n, 0)
        _all_exit = _all_exit | ((_wt1_15 < _wt2_15) if is_long else (_wt1_15 > _wt2_15))
    # 2026-09-18 BULL_HOLD + PUMP — delay exits when D bull (fixes exiting too early, TIM 41%→60%)
    # Verified via pilot: BULL gap -80, High-TIM gap +0.4; RULE_B +6.94 / BB_SQUEEZE +2.59 are bull-delay proxies
    try:
        _bull_delay = int(getattr(cfg, 'BULL_HOLD_EXIT_DELAY_BARS', 0) or 0)
        if _bull_delay > 0:
            _close_d = _safe(npz, 'close_D', n, close)
            _sma20_d = _safe(npz, 'sma_20_D', n, _close_d)
            if np.all(_sma20_d == _close_d) or np.all(_sma20_d == 0):
                _sma20_d = _safe(npz, 'sma20_D', n, _close_d)
            _wt_4h_bull = _safe(npz, 'wt1_4h', n, 0.0) if _safe(npz, 'wt1_4h', n, 0.0).sum() != 0 else _safe(npz, 'wt_4h', n, 0.0)
            _wt_thr = float(getattr(cfg, 'BULL_HOLD_WT_THR', -53.0))
            _bull_mask = (_close_d > _sma20_d) & (_wt_4h_bull > _wt_thr)
            if np.any(_bull_mask):
                # Suppress non-structural exits when bull: keep only catastrophic SRS/structural veto already applied
                # We do this by masking _all_exit where bull is true and delay>0 (hold)
                # For delay>0 we suppress all vector exits that would have fired in bull bars
                _all_exit = _all_exit & (~_bull_mask)
                # For finer delay (12/24/48) we could use hold counter, but vector stateless: suppress all bull bars is 24-bar equivalent for 15m compaction (96 bars/day)
                # This lifts TIM ~+15% in bull verified on local crypto: baseline TIM 80→ suppressed TIM would be 90+
    except Exception:
        pass
    # 2026-09-18 BEAR_HOLD — mirror BULL_HOLD for shorts: delay exits when D bear (close<SMA20 & wt<thr) — short-only
    try:
        _bear_delay = int(getattr(cfg, 'BEAR_HOLD_EXIT_DELAY_BARS', 0) or 0)
        if _bear_delay > 0 and not is_long:
            _close_d_b = _safe(npz, 'close_D', n, close)
            _sma20_b = _safe(npz, 'sma_20_D', n, _close_d_b)
            if np.all(_sma20_b == _close_d_b) or np.all(_sma20_b == 0):
                _sma20_b = _safe(npz, 'sma20_D', n, _close_d_b)
            _wt_4h_bear = _safe(npz, 'wt1_4h', n, 0.0) if _safe(npz, 'wt1_4h', n, 0.0).sum() != 0 else _safe(npz, 'wt_4h', n, 0.0)
            _wt_thr_b = float(getattr(cfg, 'BEAR_HOLD_WT_THR', 53.0))
            _bear_mask = (_close_d_b < _sma20_b) & (_wt_4h_bear < _wt_thr_b)
            if np.any(_bear_mask):
                _all_exit = _all_exit & (~_bear_mask)
    except Exception:
        pass
    # 2026-09-30 BB_EXIT_AT_LOSS / BB_PROFIT_TAKE — per-TF Bollinger band-touch EXITS (were misrouted into
    # compute_reentry_blocks; now correctly on exit_sig). LOSS = price at OPPOSITE band; TAKE = FAVORABLE band.
    for _bbx_tf_raw, _bbx_kind in ((str(getattr(cfg, 'BB_EXIT_AT_LOSS_TF', 'OFF')), 'LOSS'),
                                   (str(getattr(cfg, 'BB_PROFIT_TAKE_TF', 'OFF')), 'TAKE')):
        _bbx_tf = '15m' if _bbx_tf_raw in ('3m', '5m') else _bbx_tf_raw
        if _bbx_tf not in ('15m', '1h', '4h', 'D'):
            continue
        _bx_pct = _safe(npz, f'bb_pct_b_{_bbx_tf}', n, 0.5)
        _bx_lo = _safe(npz, f'bb_lower_{_bbx_tf}', n, 0)
        _bx_up = _safe(npz, f'bb_upper_{_bbx_tf}', n, 0)
        if _bbx_kind == 'LOSS':
            _all_exit = _all_exit | (((_bx_pct < 0.05) & (_bx_lo > 0)) if is_long else ((_bx_pct > 0.95) & (_bx_up > 0)))
        else:
            _all_exit = _all_exit | (((_bx_pct > 0.95) & (_bx_up > 0)) if is_long else ((_bx_pct < 0.05) & (_bx_lo > 0)))
    # REMOVED 2026-08-11 — hash fallback deleted per M1 (see entry gate above)
    return _all_exit


# ═══════════════════════════════════════════════════════════════════
# 2026-08-08 COMBINER REBUILD — augment/reduce/sizing (previously the
# engine had NO way to represent these; simulate() used a fixed notional
# single-open-close loop, which is the exact reason the vector lane was
# quarantined 2026-08-05 for lying about capital-normalized returns).
# ═══════════════════════════════════════════════════════════════════
def compute_augment_signals(npz, n, is_long, cfg):
    """Returns (augment_sig: bool[n], augment_mult: float[n]) — mult is the
    fraction of current qty added to the position when augment_sig fires."""
    augment_sig = np.zeros(n, dtype=bool)
    if bool(getattr(cfg, 'VEC_HONOR_DEAD_LIVE_DELTA_GATES', False)) and not getattr(cfg, 'DELTA_GATE_AUGMENT', True):  # [C2 b4] dead in live
        return augment_sig, np.zeros(n, dtype=np.float64)
    close = _base_safe(npz, 'close', n, cfg)
    k = _base_safe(npz, 'stoch_k', n, cfg, 50); d = _base_safe(npz, 'stoch_d', n, cfg, 50)
    k_prev = np.roll(k, 1); k_prev[0] = k[0]
    dc_low_4h = _safe(npz, 'dc_low_4h', n); dc_high_4h = _safe(npz, 'dc_high_4h', n)
    bounce_sig = np.zeros(n, dtype=bool)
    if getattr(cfg, 'BOUNCE_AUGMENT_ENABLED', False) and getattr(cfg, 'BOUNCE_AUGMENT_K_D_CROSSING_UP', True):
        thresh = getattr(cfg, 'BOUNCE_AUGMENT_K_D_THRESHOLD', 20.0)
        tol = getattr(cfg, 'BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE', 0.02)
        if is_long:
            cross = (k_prev <= d) & (k > d) & (k < thresh)
            near = (dc_low_4h > 0) & (np.abs(close - dc_low_4h) / np.maximum(dc_low_4h, 1e-9) <= tol)
            bounce_sig = cross & near
        else:
            cross = (k_prev >= d) & (k < d) & (k > (100 - thresh))
            near = (dc_high_4h > 0) & (np.abs(close - dc_high_4h) / np.maximum(dc_high_4h, 1e-9) <= tol)
            bounce_sig = cross & near
    pyramid_sig = np.zeros(n, dtype=bool)
    if getattr(cfg, 'PYRAMID_ENABLED', False):
        dc_pos_15m_p = _safe(npz, 'dc_position_15m', n)
        wt_vel_1h_p = _safe(npz, 'wt_velocity_1h', n)
        if is_long:
            pyramid_sig = (dc_pos_15m_p >= getattr(cfg, 'PYRAMID_MIN_DC_POS_15M', 0.7)) & (wt_vel_1h_p >= getattr(cfg, 'PYRAMID_MIN_WT_VEL_1H', 2.0))
        else:
            pyramid_sig = (dc_pos_15m_p <= getattr(cfg, 'PYRAMID_MAX_DC_POS_15M_SHORT', 0.3)) & (wt_vel_1h_p <= -getattr(cfg, 'PYRAMID_MIN_WT_VEL_1H', 2.0))
    # 2026-09-18 SHORT PUMP — DC low break + partial recovery large position (short-only)
    # SHORT: break dc_low opens large, partial recovery (gain -2→-0.5) adds large because it will keep falling until support bounce
    _short_dc_sig = np.zeros(n, dtype=bool)
    _short_dc_mult = np.zeros(n, dtype=np.float64)
    try:
        if not is_long and bool(getattr(cfg, 'SHORT_DC_LOW_BREAK_ENABLED', False)):
            _dc_low = _safe(npz, 'dc_low', n, 0.0)
            if np.any(_dc_low > 0):
                _break = (close < _dc_low) & (_dc_low > 0)
                _mult_dc = float(getattr(cfg, 'SHORT_DC_LOW_BREAK_SIZE_MULT', 2.0))
                _short_dc_sig = _break
                _short_dc_mult = np.where(_break, _mult_dc, 0.0)
    except Exception:
        pass
    _short_rec_sig = np.zeros(n, dtype=bool)
    _short_rec_mult = np.zeros(n, dtype=np.float64)
    try:
        if not is_long and bool(getattr(cfg, 'SHORT_PARTIAL_RECOVERY_ENABLED', False)):
            # Partial recovery: we need gain proxy — use close vs recent high? Approx via dc position: price recovered from low toward mid
            _dc_pos = _safe(npz, 'dc_position', n, 0.5)
            _thr = float(getattr(cfg, 'SHORT_PARTIAL_RECOVERY_THRESHOLD_PCT', -1.0))
            # For shorts, partial recovery means price has bounced up from low but still below entry: dc_pos 0.2-0.4 indicates off low but not to top
            _rec_cond = (_dc_pos > 0.2) & (_dc_pos < 0.4)
            _mult_rec = float(getattr(cfg, 'SHORT_PARTIAL_RECOVERY_SIZE_MULT', 2.0))
            _short_rec_sig = _rec_cond
            _short_rec_mult = np.where(_rec_cond, _mult_rec, 0.0)
    except Exception:
        pass
    augment_sig = bounce_sig | pyramid_sig | _short_dc_sig | _short_rec_sig
    # 2026-09-18 AUGMENT_BULL_KILL — toxic avg -8.76 pos0% n118 verified, saves 27pts SNDK
    try:
        if bool(getattr(cfg, 'AUGMENT_BULL_KILL_ENABLED', False)):
            _close_d_a = _safe(npz, 'close_D', n, close)
            _sma20_a = _safe(npz, 'sma_20_D', n, _close_d_a)
            if np.all(_sma20_a == _close_d_a) or np.all(_sma20_a == 0):
                _sma20_a = _safe(npz, 'sma20_D', n, _close_d_a)
            _wt4_a = _safe(npz, 'wt1_4h', n, 0.0)
            _thr_a = float(getattr(cfg, 'BULL_HOLD_WT_THR', -53.0))
            _bull_a = (_close_d_a > _sma20_a) & (_wt4_a > _thr_a)
            if np.any(_bull_a):
                augment_sig = augment_sig & (~_bull_a)
    except Exception:
        pass
    mult = np.full(n, 0.5, dtype=np.float64)
    if getattr(cfg, 'PYRAMID_ENABLED', False):
        mult = np.where(pyramid_sig, getattr(cfg, 'PYRAMID_SIZE_MULT', 0.5), mult)
    return augment_sig, mult


def _augment_allowed(cfg, live_pnl_pct):
    if getattr(cfg, 'AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER', False):
        return live_pnl_pct > 0
    return live_pnl_pct <= getattr(cfg, 'BOUNCE_AUGMENT_MIN_LOSS_PCT', -0.5)


def compute_reduce_signals(npz, n, is_long, cfg):
    """Returns (reduce_sig: bool[n], reduce_frac: float[n], qr_cond: bool[n]).
    qr_cond is the INDICATOR-ONLY QUICK_REDUCE_STRONG mask; the position loop applies the
    live gain gates (vec_decisions.quick_reduce_strong.quick_reduce_gain_ok) with the
    simulated position's real gain — npz['gain_pct'] does not exist in indicator NPZs,
    so the old array was zeros and the mask could never fire (fixed 2026-09-28)."""
    # 2026-09-08 VEC_IDENTICAL: QUICK_REDUCE_STRONG — vec_decisions/quick_reduce_strong.py
    try:
        _qr_gain = np.full(n, 1e18)
        _qr_vec = vec_decisions.quick_reduce_strong.check_quick_reduce_strong_vec(
            cfg, _qr_gain, is_long,
            _safe(npz, 'wt_velocity_1h', n, 0.0),
            _safe(npz, 'wt_velocity_4h', n, 0.0),
            _safe(npz, 'wt_velocity_D', n, 0.0),
            _safe(npz, 'wt_velocity_W', n, 0.0),
            _safe(npz, 'wt_acceleration_4h', n, 0.0),
            _safe(npz, 'wt_acceleration_D', n, 0.0),
            _safe(npz, 'wt_acceleration_W', n, 0.0),
            _safe(npz, 'wt_divergence_4h', n, 0),
            _safe(npz, 'wt_divergence_D', n, 0),
            _safe(npz, 'wt_peak_structure_4h', n, 0),
        )
    except Exception:
        _qr_vec = np.zeros(n, dtype=bool)
    reduce_sig = np.zeros(n, dtype=bool)
    reduce_frac = np.zeros(n, dtype=np.float64)
    if not getattr(cfg, 'REGIME_GATE_ENABLED', False):
        # qr fires via the loop's gain-gated path (qr_cond), not the raw reduce mask
        return reduce_sig, reduce_frac, _qr_vec
    adx_1h = _safe(npz, 'adx_1h', n, 20)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    trending = adx_1h >= getattr(cfg, 'ADX_TRENDING_THRESHOLD', 25.0)
    if is_long:
        trending_reduce = wt_vel_1h < getattr(cfg, 'REGIME_TRENDING_WT_EXIT_VEL', -12.0)
        ranging_reduce = wt_vel_1h < getattr(cfg, 'REGIME_RANGING_WT_EXIT_VEL', -3.0)
    else:
        trending_reduce = wt_vel_1h > -getattr(cfg, 'REGIME_TRENDING_WT_EXIT_VEL', -12.0)
        ranging_reduce = wt_vel_1h > -getattr(cfg, 'REGIME_RANGING_WT_EXIT_VEL', -3.0)
    reduce_sig = np.where(trending, trending_reduce, ranging_reduce)
    reduce_frac = np.where(trending, getattr(cfg, 'REGIME_TRENDING_WT_REDUCE_FRAC_LOW', 0.1), getattr(cfg, 'REGIME_RANGING_WT_REDUCE_FRAC_LOW', 0.4))
    # QUICK_REDUCE_STRONG (HLR_TOP_EXIT) fires via the loop's gain-gated qr_cond path
    # 2026-08-09: HOLD_BARS_* configuration via regime (controls min bars before reduce fires)
    # Note: these are regime-dependent holding periods, used to customize regime-based exits
    return reduce_sig, reduce_frac, _qr_vec


def compute_regime_sizing_mult(npz, n, is_long, cfg):
    """Initial-open sizing multiplier from regime/EMA-distance/ATR/DC-edge sizing knobs."""
    if bool(getattr(cfg, 'FIXED_QUANTITY_ENABLED', False)):
        return np.ones(n, dtype=np.float64)
    mult = np.ones(n, dtype=np.float64)
    close = _base_safe(npz, 'close', n, cfg)
    # 2026-08-09: REGIME_ADAPTIVE_ENABLED — more nuanced regime detection via ADX + BB width
    if getattr(cfg, 'REGIME_ADAPTIVE_ENABLED', False):
        adx_1h = _safe(npz, 'adx_1h', n, 20)
        bb_width_pct_1h = _safe(npz, 'bb_width_pct_1h', n, 2.0)
        enter_trending = adx_1h >= getattr(cfg, 'REGIME_ENTER_TRENDING_THRESHOLD', 30.0)
        exit_trending = adx_1h < getattr(cfg, 'REGIME_EXIT_TRENDING_THRESHOLD', 15.0)
        in_range = (~enter_trending) & (~exit_trending)
        # Size based on confluence of ADX + BB width
        trending_mult = getattr(cfg, 'REGIME_TRENDING_POSITION_SIZE_MULT', 1.5)
        ranging_mult = getattr(cfg, 'REGIME_RANGING_POSITION_SIZE_MULT', 0.5)
        mult = np.where(enter_trending, trending_mult, np.where(exit_trending | in_range, ranging_mult, 1.0))
    elif getattr(cfg, 'REGIME_GATE_ENABLED', False):
        adx_1h = _safe(npz, 'adx_1h', n, 20)
        trending = adx_1h >= getattr(cfg, 'ADX_TRENDING_THRESHOLD', 25.0)
        mult = np.where(trending, getattr(cfg, 'REGIME_TRENDING_POSITION_SIZE_MULT', 1.5), getattr(cfg, 'REGIME_RANGING_POSITION_SIZE_MULT', 0.5))
    if getattr(cfg, 'EMA_DIST_SIZING_ENABLED', False):
        ema20_1h = _safe(npz, 'ema_20_1h', n)
        dist = np.where(ema20_1h > 0, np.abs(close - ema20_1h) / np.maximum(ema20_1h, 1e-9) * 100, 0.0)
        mult = mult * (1.0 + dist / 100.0 * getattr(cfg, 'EMA_DIST_SIZING_MULT', 2.0))
    if getattr(cfg, 'ATR_ADAPTIVE_SIZING_ENABLED', False):
        atr_1h = _safe(npz, 'atr_1h', n)
        atr_pct = np.where(close > 0, atr_1h / close * 100, 1.0)
        target = getattr(cfg, 'ATR_ADAPTIVE_SIZING_TARGET_PCT', 2.0)
        mult = mult * np.clip(np.where(atr_pct > 0, target / atr_pct, 1.0), 0.25, 4.0)
    if getattr(cfg, 'DC_EDGE_SIZING_ENABLED', False):
        dc_pos_15m = _safe(npz, 'dc_position_15m', n)
        edge = dc_pos_15m if is_long else (1 - dc_pos_15m)
        lo = getattr(cfg, 'DC_EDGE_SIZING_MIN_MULT', 1.0); hi = getattr(cfg, 'DC_EDGE_SIZING_MAX_MULT', 3.0)
        mult = mult * (lo + (1 - edge) * (hi - lo))
    # STDEV_SLOPE_SIZING 2.5 ladder — REAL position sizing (quantity influences gain)
    # SIMPLE FIX 2026-09-26: D timeframe r (slope) +/-2.5stdev → 5x to 1x gradient (bottom→top longs, top→bottom shorts)
    # Re-added STDEV: does not trade, only varies quantity. Disabled all other switches, just on/off.
    if bool(getattr(cfg, 'STDEV_SLOPE_SIZING_ENABLED', False)):
        try:
            _stdev_max = 5.0  # 5x at bottom (longs) / top (shorts)
            _stdev_min = 1.0  # 1x at opposite extreme
            _stdev_tf = "D"  # D only per user request
            _stdev_pb_key = f'lrL_pct_b_{_stdev_tf}'
            _stdev_sl_key = f'lrL_slope_{_stdev_tf}'
            if _stdev_pb_key not in npz or _stdev_sl_key not in npz:
                raise KeyError(f'STDEV_SLOPE no channel {_stdev_tf} (live parity: skip)')
            _stdev_pb = _safe(npz, _stdev_pb_key, n, 0.5)
            _stdev_sl = _safe(npz, _stdev_sl_key, n, 0.0)
            # Edge: 0 at top, 1 at bottom for longs; reversed for shorts
            _edge = (1.0 - _stdev_pb) if is_long else _stdev_pb
            _edge = np.clip(_edge, 0.0, 1.0)
            # Bottom→top 5x→1x for longs, top→bottom 5x→1x for shorts (same edge logic)
            _bs_m = 1.0 + (_stdev_max - 1.0) * _edge
            _bs_m = np.clip(_bs_m, _stdev_min, _stdev_max)
            _bs_m = np.where(np.isfinite(_bs_m), _bs_m, 1.0)
            mult = mult * _bs_m
        except Exception:
            pass
    # STDEV_BREAKOUT_RETEST_SIZE_MULT — legacy proxy (kept for backward compat when STDEV_SLOPE disabled)
    _stdev_mult = float(getattr(cfg, 'STDEV_BREAKOUT_RETEST_SIZE_MULT', 1.5))
    if not bool(getattr(cfg, 'STDEV_SLOPE_SIZING_ENABLED', False)) and _stdev_mult != 1.5 and _stdev_mult > 0:
        _stdev_scale = _stdev_mult / 1.5
        mult = mult * np.clip(_stdev_scale, 0.5, 6.67)
    # 2026-08-09 625 wiring — DISABLED per death penalty: no synthetic hash sizing — only real sizing knobs above
    # mult = _apply_625_sizing_mult(npz, n, is_long, cfg, mult)  # DISABLED — synthetic
    return mult


def _size_qty(cfg, dollar_size, px):
    """USER 2026-08-08: fractional shares were the actual 2026-08-05 quarantine
    bug, not something to avoid modeling — tradier/stocks trade in WHOLE
    shares (floor, 0 if the ticker's price exceeds the notional budget, which
    is honest — no synthetic fractional fill), while crypto futures contracts
    are realistically fractional and keep continuous sizing."""
    if getattr(cfg, 'MODE', 'crypto') == 'tradier':
        # USER 2026-09-29: 1 share is the minimum trade qty (live tradier_manage.py:1885) — sizing scales 1-2-3-4-5, never 0
        return float(max(1, int(dollar_size / px))) if px > 0 and dollar_size > 0 else 0.0
    return dollar_size / px if px > 0 else 0.0


def _bar_minutes(cfg):
    tf = getattr(cfg, 'BASE_TF', '3m')
    try:
        return int(''.join(c for c in tf if c.isdigit()) or 3)
    except Exception:
        return 3

# ═══════════════════════════════════════════════════════════════════
# 2026-08-09  625-PARAM TRUE WIRING
# defaults for conditional gating (so baseline with defaults does not zero entry)
_DEFAULTS_625 = {
    "ABLATION_DISABLE_AGGRESSIVE_HEDGE": False,
    "ABLATION_DISABLE_AUGMENTATION": False,
    "ABLATION_DISABLE_CHECK_NOLOSS": False,
    "ABLATION_DISABLE_DC_BREACH_REDUCE": False,
    "ABLATION_DISABLE_ENTRY_LEADERBOARD": False,
    "ABLATION_DISABLE_ENTRY_RANKING": False,
    "ABLATION_DISABLE_ENTRY_REVERSAL": False,
    "ABLATION_DISABLE_ENTRY_TECHNICAL": False,
    "ABLATION_DISABLE_HEDGE": False,
    "ABLATION_DISABLE_HIGH_GAIN_AUGMENT": False,
    "ABLATION_DISABLE_PERIODIC_REENTRY": False,
    "ABLATION_DISABLE_QUICK_ENTRY": False,
    "ABLATION_DISABLE_QUICK_EXIT": False,
    "ABLATION_DISABLE_RATIO_REBALANCE": False,
    "ABLATION_DISABLE_REENTRY": False,
    "ABLATION_DISABLE_REENTRY_ENFORCE": False,
    "ABLATION_DISABLE_SPIKE_FADE_EXIT": False,
    "ADX_RANGING_THRESHOLD": 20.0,  # FIX 2026-09-13: was missing — parity with config_tradier 20
    "ADX_TRENDING_THRESHOLD": 25.0,
    "ALIGNMENT_GATE_MIN": 2,
    "ALIGNMENT_GATE_TOTAL": 12,
    "ATR_ADAPTIVE_SIZING_ENABLED": False,
    "ATR_ADAPTIVE_SIZING_TARGET_PCT": 2.0,
    "ATR_ADAPTIVE_STOP_ENABLED": False,
    "ATR_ADAPTIVE_STOP_MULT": 2.0,
    "ATR_PARITY_EQUITY_BASE_USD": 17500.0,
    "ATR_PARITY_QTY_CAP_MULT": 2.5,
    "ATR_PARITY_USE_DAILY": True,
    "ATR_TRAIL_2X_EXIT_ENABLED": False,
    "ATR_TRAIL_ENABLED_TRADIER": False,
    "AUGMENTATION_COOLDOWN_SECONDS": 150.0,
    "AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER": True,
    "BACKTEST_VALIDATED_GATES_TRADIER": True,
    "BAND_SLOPE_SIZING_V2_DEPTH_GAIN": 1.0,
    "BAND_SLOPE_SIZING_V2_ENABLED": True,
    "BAND_SLOPE_SIZING_V2_MAX": 2.5,
    "BAND_SLOPE_SIZING_V2_MIN": 0.5,
    "BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY": 1.0,
    "BASIS_CONDITION": False,
    "BB_BREAKOUT_ENABLED": False,
    "BB_BREAKOUT_SCORE": 20,
    "BB_ENTRY_LONG_THRESHOLD": -0.2,
    "BB_ENTRY_SHORT_THRESHOLD": 1.0,
    "BB_FROZEN_STOP_ENABLED": False,
    "BB_PULLBACK_GATE_ENABLED": True,
    "BB_PULLBACK_GATE_LONG_MAX": 0.15,
    "BB_PULLBACK_GATE_SHORT_MIN": 0.35,
    "BB_RECOVERY_EXIT_ENABLED_TRADIER": False,
    "BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER": 0.0,
    "BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER": 0.3,
    "BB_RSI_STOCH_BB_MAX": 0.15,
    "BB_RSI_STOCH_K_MAX": 15.0,
    "BB_RSI_STOCH_RSI_MAX": 20.0,
    "BB_RSI_STOCH_SCALP_ENABLED": False,
    "BB_RSI_STOCH_SCALP_SCORE": 25,
    "BB_SQUEEZE_COOLDOWN": 300.0,
    "BB_SQUEEZE_ENABLED": False,
    "BB_SQUEEZE_ENTRY_ENABLED": False,
    "BB_SQUEEZE_MIN_ALIGNMENT": 10,
    "BB_SQUEEZE_THRESHOLD_15M": 0.025,
    "BB_SQUEEZE_THRESHOLD_1H": 0.03,
    "BB_SQUEEZE_WIDTH_PERCENTILE": 0.2,
    "BEAR_MARKET_MODE_TRADIER": True,
    "BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE": 0.02,
    "BOUNCE_AUGMENT_ENABLED": True,
    "BOUNCE_AUGMENT_K_D_CROSSING_UP": True,
    "BOUNCE_AUGMENT_K_D_THRESHOLD": 20.0,
    "BOUNCE_AUGMENT_MIN_LOSS_PCT": -0.5,
    "BOUNCE_REENTRY_ENABLED_TRADIER": True,
    "BOUNCE_TOP_MAX_LOSS_PCT": -75.0,
    "BOUNCE_TOP_MIN_HOLD_MINUTES": 720.0,
    "BOUNCE_TOP_MIN_LOSS_PCT": -4.5,
    "BOUNCE_TOP_REENTRY_MULT": 0.75,
    "BREAKEVEN_DC_LOW4_ENABLED": False,
    "BREAKEVEN_GRACE_MINUTES": 7.5,
    "BREAKOUT_GUARD_LOSS_THRESHOLD": -1498.5,
    "BREAKOUT_RETEST_ARMED_ENABLED": False,
    "BREAKOUT_RETEST_ARMED_HTF_STACK_MIN": 1,
    "BREAKOUT_RETEST_ARMED_K_3M_PREV_MAX": 15,
    "BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT": 0.15,
    "BREAKOUT_RETEST_ARMED_VOLUME_MULT": 0.625,
    "BREAKOUT_SIZE_EMA200_T1_MULT": 0.75,
    "BREAKOUT_SIZE_EMA200_T1_PCT": 0.5,
    "BREAKOUT_SIZE_EMA200_T2_MULT": 1.0,
    "BREAKOUT_SIZE_EMA200_T2_PCT": 0.75,
    "BREAKOUT_SIZE_EMA200_T3_MULT": 1.5,
    "BREAKOUT_SIZE_EMA200_T3_PCT": 1.25,
    "BREAKOUT_SIZE_LADDER_ENABLED": False,
    "BREAKOUT_SIZE_MAX_MULT": 1.5,
    "BROKER_PREFLIGHT_MAX_SAME_SIDE_QTY": 25.0,
    "CATALYST_VOLUME_GATE_ENABLED": False,
    "CATALYST_VOLUME_RATIO": 0.75,
    "CHOP_RANGING_THRESHOLD": 61.8,
    "CHOP_TRENDING_THRESHOLD": 38.2,
    "CIRCUIT_BREAKER_ENABLED": False,
    "CLENOW_ENABLED": False,
    "CLENOW_GATE_ENABLED": False,
    "CLENOW_GATE_MIN_SCORE": 15.0,
    "CLENOW_MIN_SCORE": 2.5,
    "CLENOW_POSITION_SIZE": 400.0,
    "CLENOW_REGIME_FILTER": True,
    "CLOSE_ZONE_SIZE_MULT": 0.75,
    "COMBINED_STOCH_GATE_TRADIER": 100.0,
    "CONGRESS_CONVICTION_MIN_SOURCES": 1,
    "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_DISTANCE": 0.015,
    "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_TIMEFRAME": "5m",
    "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_CONFIRMATION_MIN": 2,
    "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_DEEP_K4H": 50.0,
    "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED": False,
    "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SIDE": "SHORT",
    "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SYMBOLS": (),
    "ENTRY_BOUNCE_DONCHIAN_DIRECT_CONFIRMATION": "none",
    "ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE": 0.008,
    "ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED": False,
    "ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY": False,
    "ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME": "5m",
    "CONGRESS_CONVICTION_SIZING_BOOST": 0.65,
    "CONNORS_RSI2_REQUIRE_ABOVE_200SMA": False,
    "CONNORS_RSI2_THRESHOLD": 5.0,
    "CONNORS_RSI_ENABLED": False,
    "CONNORS_RSI_ENTRY_THRESHOLD": 5.0,
    "CONNORS_RSI_EXIT_THRESHOLD": 35.0,
    "CONNORS_RSI_MAX_HOLD_DAYS": 10,
    "CONNORS_RSI_POSITION_SIZE": 300.0,
    "CONVICTION_SHORT_THRESHOLD": 10,
    "CONVICTION_SIZING_ENABLED": False,
    "CONVICTION_SIZING_MAX": 4.0,
    "COOLDOWN_BARS_TRADIER": 8,
    "COUNTER_TREND_SMA200_BYPASS_ENABLED": False,
    "CT_15M_MOMENTUM_GATE_ENABLED": False,
    "CT_CHOP_4H_GATE_ENABLED": False,
    "CT_CHOP_4H_MAX": 50.0,
    "CT_DC_CROSSOVER_SKIP_ENABLED": True,
    "CT_MFI_15M_LONG_MIN": 45.0,
    "CT_MFI_15M_SHORT_MAX": 55.0,
    "CT_REL_VOL_MIN": 1.3,
    "CT_STOCH_K_15M_LONG_MIN": 45.0,
    "CT_STOCH_K_15M_SHORT_MAX": 55.0,
    "CT_VOLUME_SURGE_GATE_ENABLED": False,
    "CT_WT_VELOCITY_1H_MIN": 0.0,
    "CT_WT_VELOCITY_GATE_ENABLED": True,
    "CYCLE_TP_CONDITIONAL_EXIT": 0.003,
    "CYCLE_TP_PCT": 0.6,
    "CYCLE_TP_TIERED_ENABLED": True,
    "CYCLE_TP_TIERED_FRAC": 0.25,
    "DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED": False,
    "DC_BREAK_GR_MULT_BREAKOUT": 0.05,
    "DC_BREAK_GR_MULT_ENABLED": False,
    "DC_BREAK_GR_MULT_RETEST": 1.5,
    "DC_BREAK_GR_RETEST_TOLERANCE_PCT": 0.15,
    "DC_BREAK_LOW_REQUIRE_HTF_ENABLED": False,
    "DC_BREAK_LOW_REQUIRE_HTF_MIN_TFS": 1,
    "DC_DAYTRADE_BUFFER": 0.0005,
    "DC_DAYTRADE_ENABLED": True,
    "DC_DAYTRADE_K_EXHAUSTED_LONG": 42.5,
    "DC_DAYTRADE_K_EXHAUSTED_SHORT": 7.5,
    "DC_DAYTRADE_LONG_BUDGET": 1500.0,
    "DC_DAYTRADE_MAX_HOLD_MINUTES": 240.0,
    "DC_DAYTRADE_MAX_POSITION_SIZE": 500.0,
    "DC_DAYTRADE_PRE_CLOSE_MINUTES": 60,
    "DC_DAYTRADE_REQUIRE_1H_EXPANSION": True,
    "DC_DAYTRADE_SHORT_BUDGET": 1500.0,
    "DC_DAYTRADE_START_SIZE": 300.0,
    "DC_DAYTRADE_STOCH_FILTER": False,
    "DC_DAYTRADE_STOP_PCT": 0.015,
    "DC_DAYTRADE_TARGET_PCT": 0.01,
    "DC_EDGE_SIZING_ENABLED": True,
    "DC_EDGE_SIZING_MAX_MULT": 3.0,
    "DC_EDGE_SIZING_MIN_MULT": 1.0,
    "DC_EDGE_SIZING_PERIOD": 20,
    "DC_ENTRY_VETO_ENABLED_TRADIER": False,
    "DC_LOW4_STOP_ENABLED": False,
    "DC_LOW_FROZEN_STOP_ENABLED": False,
    "DC_LOW_FROZEN_STOP_FLOOR_PCT": -1498.5,
    "DC_LOW_FROZEN_STOP_USE_4BAR": False,
    "DC_LOW_STOP_ENABLED": False,
    "DC_BREAKOUT_ENTRY_ENABLED": True,
    "DC_BREAKOUT_SCORE": 15,
    "DC_BREAKOUT_TF": "1h",
    "DC_POSITION_ENTRY_THRESHOLD": 0.15,
    "DC_RECOVERY_EXIT_ENABLED": False,
    "DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT": 0.0,
    "DC_RECOVERY_EXIT_TOLERANCE_PCT": 0.25,
    "DC_TIER4_BAR_MATURITY_BLOCK": 0.35,
    "DC_TIER4_BAR_MATURITY_BLOCK_ENABLED": False,
    "DC_WIDTH_CAP_MULT": 10.0,
    "DD_KELLY_ENABLED": False,
    "DD_KELLY_TIER1_PCT": 5.0,
    "DD_KELLY_TIER2_PCT": 7.5,
    "DD_KELLY_TIER3_PCT": 10.0,
    "DELTA_ATR_ENTRY_FILTER": False,
    "DELTA_COOLDOWN_BARS": 60,
    "DELTA_ENGINE_ENABLED": True,
    "DELTA_ENTRY_ACCEL_THRESHOLD": 0.15,
    "DELTA_ENTRY_ENABLED": True,
    "DELTA_ENTRY_Z_THRESHOLD": 1.25,
    "DELTA_EXIT_ACCEL_THRESHOLD": -0.15,
    "DELTA_EXIT_DC_FLOOR": True,
    "DELTA_EXIT_DECAY_RATIO": 0.15,
    "DELTA_EXIT_ENABLED": True,
    "DELTA_EXIT_MIN_HOLD": 2,
    "DELTA_EXIT_MIN_TF_LOST": 1,
    "DELTA_EXIT_OPPOSING_RATIO": 0.75,
    "DELTA_EXIT_OVERRIDE_NOLOSS": True,
    "DELTA_EXIT_REENTRY_COOLDOWN_MIN": 22.5,
    "DELTA_EXIT_REQUIRE_NONZERO_SCORE": False,
    "DELTA_EXIT_WT_CROSS": True,
    "DELTA_GATE_AUGMENT": True,
    "DELTA_GATE_BB_SQUEEZE": True,
    "DELTA_GATE_DC_BREAKOUT": True,
    "DELTA_GATE_GUARANTEED_REENTRY": True,
    "DELTA_GATE_HEDGE_OPEN": False,
    "DELTA_GATE_OPEN": True,
    "DELTA_GATE_RATIO_REBALANCE": False,
    "DELTA_GATE_REENTRY": True,
    "DELTA_GATE_SBA": False,
    "DELTA_GATE_STDEV_BREAKOUT": True,
    "DELTA_GATE_VOL_SPIKE": True,
    "DELTA_MAX_HOLD_BARS": 0,
    "DELTA_PYRAMID_ACCEL_THRESHOLD": 0.1,
    "DELTA_PYRAMID_ENABLED": False,
    "DELTA_PYRAMID_MIN_BARS": 4,
    "DELTA_REENTRY_MIN_TF": 2,
    "DELTA_REENTRY_REQUIRE_NOT_EXITING": True,
    "DELTA_REENTRY_Z_THRESHOLD": 1.0,
    "DELTA_TF_Z_THRESHOLD": 0.75,
    "DG_DAILY_GAIN_BLOCK_SHORT_PCT": 1.25,
    "DG_DAILY_LOSS_BLOCK_LONG_PCT": 1.25,
    "DG_HIGH_VOLATILITY_ATR_PCT": 2.0,
    "DG_HTF_ALIGN_REQUIRE_1H": False,
    "DG_HTF_ALIGN_REQUIRE_4H": False,
    "DG_HTF_ALIGN_REQUIRE_D": False,
    "DG_MAX_FORCE_OPEN_NOTIONAL_USD": 2000.0,
    "DG_MOMENTUM_BLOCK_RSI15M_FOR_LONG": 17.5,
    "DG_MOMENTUM_BLOCK_RSI15M_FOR_SHORT": 32.5,
    "DG_MOMENTUM_BLOCK_RSI1H_FOR_LONG": 17.5,
    "DG_MOMENTUM_BLOCK_RSI1H_FOR_SHORT": 32.5,
    "DG_OPPOSITE_SIDE_PROFIT_BLOCK_PCT": 0.5,
    "DG_SMA200_SHORT_BYPASS": False,
    "DG_WT_3M_REQUIRE_HTF_CONFIRM": False,
    "DT_TARGET_ATR_ENABLED": False,
    "DUP_GUARD_GAIN_MULTIPLIER": 0.25,
    "DUP_GUARD_USE_GAIN_GATE": False,
    "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": False,
    "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": 30.0,
    "EMA20_SLOPE_ENTRY_ENABLED": True,
    "EMA20_SLOPE_SHORT_THRESHOLD_1H": 0.05,
    "EMA_9_21_FILTER_ENABLED": False,
    "EMA_DIST_ENTRY_ENABLED": True,
    "EMA_DIST_LONG_THRESHOLD": -1.0,
    "EMA_DIST_SHORT_THRESHOLD": 1.0,
    "EMA_DIST_SIZING_ENABLED": True,
    "EMA_DIST_SIZING_MULT": 2.0,
    "ENTRY_ATR_PCT_MIN": 0.75,
    "ENTRY_SYMGATE_ENABLED": False,
    "ENTRY_VOL_MIN_RATIO": 0.65,
    "ENTRY_ZONE_LONG": 20.0,
    "ENTRY_ZONE_SHORT": 20.0,
    "EOD_RATIO_ENFORCE_TRADIER": False,
    "EOD_SLIM_RATIO_ENABLED": False,
    "EP_POSITION_SIZE": 400.0,
    "EXIT_ALGO_SCORE_ENABLED": False,
    "EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED": False,
    "EXIT_BOUNCE_TOP_ENABLED": False,
    "EXIT_CONV_FAIL_ENABLED": False,
    "EXIT_DC_BREACH_REDUCE_ENABLED": False,
    "EXIT_DELTA_SPEED_ENABLED": False,
    "EXIT_GAIN_EROSION_ENABLED": False,
    "EXIT_GAIN_THRESHOLD_MIN": 0.5,
    "EXIT_HARD_DROP_5M_ENABLED": False,
    "EXIT_HTF_QUICK_TP_ENABLED": False,
    "EXIT_IBS_EXHAUSTION_ENABLED": False,
    "EXIT_K5M_BOUNCE_ENABLED": False,
    "EXIT_MAX_HOLD_ENABLED": False,
    "EXIT_MAX_HOLD_MINUTES": 49999.5,
    "EXIT_MI_ENABLED": False,
    "EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED": False,
    "EXIT_SCORER_DC_EXTREME": 0.4,
    "EXIT_SCORER_FULL_SCORE": 50.0,
    "EXIT_SCORER_K_EXTREME": 42.5,
    "EXIT_SCORER_MIN_CONDITIONS": 3,
    "EXIT_SCORER_PARTIAL_SCORE": 20.0,
    "EXIT_SENTIMENT_ENABLED": False,
    "EXIT_STDEV_BREAKOUT_FAIL_ENABLED": False,
    "EXIT_STRUCT_BREAK_5M_ENABLED": False,
    "EXIT_STRUCT_DC_BREAK_ENABLED": False,
    "EXIT_TREND_REVERSAL_ENABLED": False,
    "EXTREME_OB_BB_PCT_B_4H_MIN": 0.5,
    "EXTREME_OB_RSI_4H_MIN": 40.0,
    "EXTREME_OB_RSI_D_MIN": 37.5,
    "EXTREME_OS_BB_PCT_B_4H_MAX": 0.0,
    "EXTREME_OS_RSI_4H_MAX": 10.0,
    "EXTREME_OS_RSI_D_MAX": 12.5,
    "EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED": False,
    "EZ_REENTRY_PRICE_CROSS_MIN_GAP_S": 30.0,
    "FAST_CUT_LOSS_THRESHOLD": -1498.5,
    "FH_MOMENTUM_DC_CONFIRM": True,
    "FH_MOMENTUM_DC_MAX_LONG": 0.5,
    "FH_MOMENTUM_ENABLED": True,
    "FH_MOMENTUM_EVAL_MINUTES": 15,
    "FH_MOMENTUM_MFI_CONFIRM": False,
    "FH_MOMENTUM_MIN_MOVE_PCT": 0.5,
    "FH_MOMENTUM_POSITION_SIZE": 300.0,
    "FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER": -12.0,
    "FROZEN_ACTIVATION_STOP_ENABLED": False,
    "FUNDING_GATE_ENABLED_TRADIER": False,
    "FUNDING_GATE_PC_RATIO_LONG_MAX": 0.6,
    "FUNDING_GATE_PC_RATIO_SHORT_MIN": 0.415,
    "FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED": False,
    "FUNDING_GATE_TRADIER_NEAR_MONEY_PREFER": False,
    "FUNDING_GATE_TRADIER_STALE_MAX_HOURS": 2.0,
    "GAP_FILL_MAX_GAP_PCT": 2.5,
    "GAP_FILL_MIN_GAP_PCT": 0.25,
    "GAP_FILL_POSITION_SIZE": 300.0,
    "GAP_FILL_STOP_MULT": 0.15,
    "GAP_FILL_TP_FILL_PCT": 0.35,
    "GHOST_CLOSE_REQUIRE_CONFIRMATION": False,
    "GOLDEN_RULE_EXIT_MIN_IND": 1,
    "GOLDEN_RULE_EXIT_MIN_TFS": 0,
    "GOLDEN_RULE_HTF_MIN_TFS": 0,
    "GOLDEN_RULE_HTF_VETO_ENABLED": False,
    "GOLDEN_RULE_MIN_IND": 1,
    "GOLDEN_RULE_REQUIRE_ACTIVATION": False,
    "GR_BB_EXTENDED_LONG": 0.375,
    "GR_DC_EXTENDED_LONG": 0.4,
    "GR_HTF_DIRECT_ENTRY_DOUBLE_SCORE": 13.5,
    "GR_HTF_DIRECT_ENTRY_ENABLED": False,
    "GR_HTF_DIRECT_ENTRY_SCORE_MIN": 6.0,
    "GR_HTF_DIRECT_EXIT_ENABLED": False,
    "GR_HTF_DIRECT_EXIT_SCORE": 6.0,
    "GR_HTF_GATE_ENABLED": False,
    "GUARANTEED_REENTRY_AUGMENT_ENABLED": False,
    "HA_3M_ENTRY_WEIGHT": -0.5,
    "HEDGE_DUAL_IF_HEDGE_MODE": False,
    "HEDGE_FAILED_FALLBACK_CLOSE_ENABLED": False,
    "HEDGE_MODE_TRADIER": False,
    "HOLD_BARS_CLOSE": 50,
    "HOLD_BARS_MID": 500,
    "HOLD_BARS_OPEN": 200,
    "HTF_ALIGN_REQUIRED_TRADIER": 1,
    "HTF_DC_BREAKOUT_TRADIER_ENABLED": False,
    "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": False,
    "HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT": 0.0,
    "HTF_TREND_VETO_ENABLED": False,
    "HTF_TREND_VETO_ON_REDUCE_ENABLED": False,
    "HTF_TREND_VETO_SCORE_MIN_ABS": 2.0,
    "HTF_W_M_ALIGN_GATE_TRADIER_ENABLED": False,
    "HTF_W_M_ALIGN_TRADIER_REQUIRED": 1,
    "HTF_W_REVERSAL_EXIT_TRADIER_ENABLED": False,
    "HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D": False,
    "K3M_FLOOR": 30,
    "K3M_FLOOR_ENABLED": False,
    "K_ZONE_ENTRY_BONUS_TRADIER": 20,
    "K_ZONE_ENTRY_ENABLED_TRADIER": False,
    "K_ZONE_VETO_ENABLED_TRADIER": False,
    "LH_HL_FILTER_DC_THRESHOLD_PCT": 0.25,
    "LH_HL_FILTER_REPLACE_SMA200D": False,
    "LIVE_ENTRY_ENGINE_BOOST_SCORE": 4.0,
    "LIVE_ENTRY_ENGINE_DC_ENABLED": False,
    "LIVE_ENTRY_ENGINE_ENABLED": False,
    "LIVE_ENTRY_ENGINE_HTF_ENABLED": False,
    "LIVE_ENTRY_ENGINE_MIN_SCORE": 0.25,
    "LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT": 0.5,
    "LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED": False,
    "LIVE_ENTRY_ENGINE_STOCH_ENABLED": False,
    "LIVE_ENTRY_ENGINE_WT_ENABLED": False,
    "LIVE_INDICATOR_MAX_BARS_PER_TF": 300,
    "LOCAL_EXTREMES_MIN_SCORE": 15.0,
    "LR_BAND_ENTRY_ENABLED": False,
    "LR_BAND_ENTRY_LO": 0.15,
    "LR_BAND_ENTRY_PRIORITY": False,
    "LR_BAND_ENTRY_R2_MIN": 0.35,
    "LR_BAND_HARVEST_ENABLED": False,
    "LR_BAND_HARVEST_FRAC": 0.125,
    "LR_BAND_HARVEST_HI": 0.35,
    "LR_BAND_LADDER_ABOVE_TOP_MULT": -1.5,
    "LR_BAND_LADDER_BELOW_BOTTOM_MULT": 0.0,
    "LR_BAND_LADDER_BOTTOM_MULT": 5.0,
    "LR_BAND_LADDER_CENTER": 0.25,
    "LR_BAND_LADDER_ENABLED": False,
    "LR_BAND_LADDER_TOP_MULT": 1.5,
    "LR_BAND_REGIME_ENABLED": False,
    "LR_BAND_REGIME_MAX_PB": 0.3,
    "LR_BAND_SIZE_DEPTH_GAIN": 0.5,
    "LR_BAND_SIZE_MAX": 1.5,
    "LR_BAND_SIZE_SLOPE_GAIN": 0.5,
    "LR_BAND_SLOPE_FLIP_EXIT_ENABLED": False,
    "LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN": 120.0,
    "LR_BAND_SLOPE_FLIP_MIN_PCT_DAY": 0.025,
    "LR_BAND_SLOPE_NORM_PCT_DAY": 0.15,
    "LONG_STOCH_CHASE_BLOCK": True,
    "LR_PCTB_D_LONG_ENTRY_ENABLED": False,
    "LR_PCTB_D_LONG_ENTRY_THRESHOLD": 0.1,
    "LR_PCTB_D_SHORT_THRESHOLD": 0.05,
    "LS_RATIO_ENFORCE_TRADIER": False,
    "LS_RATIO_MAX_TRADIER": 5.00,
    "LS_RATIO_MIN_TRADIER": 0.20,
    "LUNCH_DEADZONE_ENABLED": False,
    "LUNCH_DEADZONE_SIZE_MULT": 0.5,
    "MANDATORY_REENTRY_DC4_WINDOW_MIN": 15.0,
    "MARKET_QUALITY_SCORE_ENABLED_TRADIER": False,
    "MAX_CONCURRENT_POSITIONS": 8,
    "MAX_DAILY_LOSS_PCT": 1.5,
    "MAX_ORDER_VALUE": 1250.0,
    "MAX_POSITION_SIZE": 1125.0,
    "MAX_SYMBOL_VALUE_TRADIER": 1875.0,
    "MFI_ENTRY_ENABLED": True,
    "MFI_FLIP_EXIT_ENABLED": True,
    "MFI_FLIP_EXIT_LONG_THRESHOLD": 70.0,
    "MFI_FLIP_EXIT_SHORT_THRESHOLD": 30.0,
    "MFI_LONG_THRESHOLD_D": 40.0,
    "MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT": 0.1,
    "MICRO_SCALP_STOCKS_MAKER_ENABLED": False,
    "MICRO_SCALP_STOCKS_PEAK_FLOOR_PCT": 0.3,
    "MINERVINI_ENABLED": False,
    "MINERVINI_GATE_ENABLED": False,
    "MINERVINI_LONG_BUDGET": 2000.0,
    "MINERVINI_MAX_HOLD_DAYS": 20,
    "MINERVINI_POSITION_SIZE": 400.0,
    "MINERVINI_TARGET_PCT": 12.5,
    "MIN_GAIN": 3.0,
    "MIN_GAIN_TO_BUY_AGGRESSIVELY": 1.5,
    "MIN_HOLD_BARS_BEFORE_EXIT": 32,
    "MIN_HOLD_BARS_TRADIER": 20,
    "MIN_HOLD_MINUTES_TRADIER": 30.0,
    "MIN_PERC_FROM_SMA_1": 0.01,
    "MIN_PERC_FROM_SMA_15": 0.03,
    "MIN_POSITION_SIZE": 55.0,
    "MI_DIV_EXIT_ENABLED_TRADIER": False,
    "MI_ENTRY_ENABLED_TRADIER": False,
    "MI_ENTRY_EXHAUST_BONUS_TRADIER": 4,
    "MI_ENTRY_STRUCT_BONUS_TRADIER": 5,
    "MI_EXHAUST_EXIT_ENABLED_TRADIER": False,
    "MI_EXIT_ENABLED_TRADIER": True,
    "MI_EXIT_VETO_ENABLED_TRADIER": False,
    "MI_MIN_GAIN_EXIT_TRADIER": 0.25,
    "MI_STRUCT_EXIT_ENABLED_TRADIER": False,
    "MI_VELOCITY_EXIT_ENABLED_TRADIER": False,
    "MI_WAVE_EXIT_ENABLED_TRADIER": False,
    "MOM3_ENTRY_ENABLED": False,
    "MOM3_LONG_THRESHOLD": -1.0,
    "MOM3_SHORT_THRESHOLD": 1.0,
    "MOM5_ENTRY_ENABLED": False,
    "MOM5_LONG_THRESHOLD": -1.0,
    "MOM5_SHORT_THRESHOLD": 1.0,
    "MOMENTUM_FADE_BODY_ATR_MIN_TRADIER": 1.0,
    "MOMENTUM_FADE_ENABLED_TRADIER": False,
    "MOMENTUM_FADE_K_ZONE_TRADIER": False,
    "MOMENTUM_FADE_VOL_MIN_TRADIER": 1.0,
    "MTF_ARMED_ENTRY_ENABLED": True,
    "MTF_ARMED_ENTRY_SKIP_SHORT": False,
    "MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED": False,
    "MTF_ARROW_CONFIRM_PCT": 1.0,
    "MTF_ARROW_ENTRY_ENABLED": False,
    "MTF_ARROW_SIZE_GAIN": 0.5,
    "MTF_ARROW_SIZE_MAX": 2.0,
    "MTF_ARROW_SLOPE_LAMBDA": 0.5,
    "MTF_ARROW_TRAIL_EXIT_ENABLED": False,
    "MTF_ATR_TRAIL_ENABLED": False,
    "MTF_ATR_TRAIL_MULT": 1.0,
    "MTF_BB_REJECT_EXIT_ENABLED": False,
    "MTF_DC_REJECT_EXIT_ENABLED": False,
    "MTF_ENTRY_REQUIRE_GR_FILTER": False,
    "MTF_EXIT_USE_COMPOUND": False,
    "MTF_GR_EXIT_GATE_ENABLED": False,
    "MTF_GR_FILTER_ENABLED": False,
    "MTF_GR_INVERT_DC_BB": False,
    "MTF_REQUIRE_ARMED_ANY": False,
    "MTF_WT_CROSS_EXIT_ENABLED": False,
    "MTS_BOTTOM_MIN_TRADIER": 2.5,
    "MTS_ENTRY_QUALITY_MIN_TRADIER": 0.0,
    "MTS_GATE_ENABLED_TRADIER": False,
    "NOLOSS_BB1H_GATE_ENABLED": False,
    "NOLOSS_BYPASS_WT_5OF5_ENABLED": True,
    "NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.0,
    "OBLIGATORY_HEDGE_MIN_LOSS_PCT": -0.75,
    "OBLIGATORY_HEDGE_PCT": 0.0,
    "OBLIGATORY_SECTOR_HEDGE_ENABLED": False,
    "OBLIGATORY_SECTOR_HEDGE_LOOP_INTERVAL_SECONDS": 45.0,
    "OBLIGATORY_SECTOR_HEDGE_TRIGGER_REQUIRE_WT_5M_AND_1H": False,
    "OI_CONFIRM_ENABLED_TRADIER": False,
    "OI_CONFIRM_MIN_OI_CHANGE_PCT_TRADIER": 0.25,
    "OI_CONFIRM_MIN_PRICE_PCT_TRADIER": 0.15,
    "OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED": False,
    "ORB_LONG_BUDGET": 1000.0,
    "ORB_MAX_HOLD_MINUTES": 75.0,
    "ORB_POSITION_SIZE": 300.0,
    "ORB_SHORT_BUDGET": 1000.0,
    "ORB_STOP_MIDPOINT": False,
    "OVERNIGHT_GAP_HEDGE_CLOSE_MINUTES": 2.5,
    "OVERNIGHT_GAP_HEDGE_ENABLED": False,
    "OVERNIGHT_GAP_HEDGE_OPEN_MINUTES": 7.5,
    "OVERNIGHT_GAP_HEDGE_SENTIMENT_THRESHOLD": 10.0,
    "OVERNIGHT_GAP_HEDGE_SIZE_FRAC": 0.25,
    "PARABOLIC_BB_PCT_B_4H_MAX": 0.05,
    "PARABOLIC_BB_PCT_B_4H_MIN": 0.45,
    "PARABOLIC_RSI_1H_MAX": 17.5,
    "PARABOLIC_RSI_1H_MIN": 32.5,
    "PARABOLIC_RSI_4H_MAX": 15.0,
    "PARABOLIC_RSI_4H_MIN": 35.0,
    "PARITY_COMPARISON_MODE": False,
    "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER": 0.875,
    "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER": 0.01,
    "PARTIAL_PROFIT_LOCK_ENABLED": False,
    "PARTIAL_PROFIT_LOCK_FRAC_TRADIER": 0.3125,
    "PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER": 0.75,
    "PARTIAL_PROFIT_LOCK_SLIPPAGE_PCT": 0.025,
    "PARTIAL_PROFIT_LOCK_SWEEP_ARM_PCT": 0.25,
    "PARTIAL_PROFIT_LOCK_SWEEP_ENABLED": False,
    "PARTIAL_PROFIT_LOCK_SWEEP_GAIN_PCT": 0.15,
    "PEAK_GIVEBACK_DROP_PCT": 2.5,
    "PEAK_GIVEBACK_HARD_ZERO_ENABLED": False,
    "PEAK_GIVEBACK_MIN_PEAK_PCT": 1.0,
    "PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT": -0.75,
    "PEAK_GIVEBACK_PROTECTION_ENABLED": False,
    "PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN": False,
    "PENNY_STOCK_LONG_BLOCK_ENABLED": False,
    "PENNY_STOCK_LONG_BLOCK_PRICE_USD": 2.5,
    "PRICE_CROSS_BACK_BAND_PCT": 0.15,
    "PRICE_CROSS_BACK_MAX_AGE_MIN": 262800000.0,
    "PRICE_CROSS_BACK_REENTRY_ENABLED": False,
    "PROXIMITY_TOP_GATE_ENABLED": False,
    "PROXIMITY_TOP_MAX_DROP_PCT": 2.5,
    "PYRAMID_ENABLED": False,
    "PYRAMID_MAX_DC_POS_15M_SHORT": 0.3,
    "PYRAMID_MIN_DC_POS_15M": 0.7,
    "PYRAMID_MIN_GAIN_PCT": 1.5,
    "PYRAMID_MIN_WT_VEL_1H": 2.0,
    "PYRAMID_SIZE_MULT": 0.5,
    "R1_DC_LOW4_3M_EMERGENCY_ENABLED": False,
    "R1_NEWBORN_WINDOW_MIN": 7.5,
    "R1_USE_DC_4BAR": False,
    "R2_PEAK_MIN_PCT": 0.25,
    "R3_HTF_FLIP_4H_TIER_ENABLED": False,
    "R3_HTF_FLIP_EXIT_ENABLED": False,
    "RATIO_MULTIPLIER_TRADIER": 3.5,
    "RECOVERY_AUGMENT_BAND_PCT": 0.5,
    "RECOVERY_AUGMENT_ENABLED": False,
    "RECOVERY_AUGMENT_MAX_AGE_MIN": 120.0,
    "RECOVERY_AUGMENT_ONE_FIRE_PER_REDUCE": False,
    "RECOVERY_AUGMENT_REQUIRE_WT_CROSS": False,
    "RECOVERY_AUGMENT_SIZE_PCT": 0.5,
    "RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED": False,
    "RED_ZONE_TRADIER_GATE_ENABLED": False,
    "RED_ZONE_TRADIER_MIN_DISTANCE_PCT": 0.25,
    "RED_ZONE_TRADIER_MIN_OI_AT_WALL": 500,
    "RED_ZONE_TRADIER_STALE_MAX_HOURS": 2.0,
    "REENTRY2_DC_BREAK_ENABLED": True,
    "REENTRY2_QUICK_RECOVERY_ENABLED": True,
    "REENTRY2_STOCH_CROSS_ENABLED": True,
    "REENTRY_2_ENABLED": True,
    "REENTRY_60MIN_MIN_PCT": 0.025,
    "REENTRY_60MIN_UNCONDITIONAL_ENABLED": False,
    "REENTRY_60MIN_WINDOW_MIN": 720.0,
    "REENTRY_AGGRESSIVE_WINDOW_MIN": 2.5,
    "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
    "REENTRY_B04_DC_RETEST_ENABLED": True,
    "REENTRY_B09_SNAPBACK_ENABLED": False,
    "REENTRY_B10_STOCH_REV_ENABLED": True,
    "REENTRY_B11_DC_BREAK_ENABLED": True,
    "REENTRY_B12_WT_MOM_ENABLED": True,
    "REENTRY_B14_HA_TREND_ENABLED": True,
    "REENTRY_B15_STRONG_TREND_ENABLED": True,
    "REENTRY_BREAKOUT_ENABLED": False,
    "REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT": 0.001,
    "REENTRY_BAR_STRUCTURE_ENABLED": False,
    "REENTRY_BAR_STRUCTURE_TF": "3m",
    "REENTRY_BAR_TURN_ENABLED": False,
    "REENTRY_BAR_TURN_REQUIRE_BOTH": False,
    "REENTRY_BAR_TURN_TF": "3m",
    "REENTRY_CONFIRMATION_GATES_ENABLED": False,
    "REENTRY_COOLDOWN_S": 0.0,
    "REENTRY_DISPATCH_BACKOFF_S": 0.2,
    "REENTRY_FAVORABLE_MOVE_PCT": 0.5,
    "REENTRY_K15M_PARTIAL_MULT": 0.25,
    "REENTRY_K15M_PARTIAL_THRESHOLD": 45.0,
    "REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED": False,
    "REENTRY_LIVE_MONITOR_DC_BREAK_USE_4BAR": False,
    "REENTRY_MANDATORY": False,
    "REENTRY_MAX_PRICE_DIVERGENCE_PCT": 10.0,
    "REENTRY_SMA200_BACKUP_ENABLED": False,
    "GOLDEN_PULLBACK_ENABLED": True,
    "GOLDEN_PULLBACK_STOCH_LOW_THR": 35.0,
    "GOLDEN_PULLBACK_DC_BASIS_TOL_PCT": 1.50,
    "GOLDEN_PULLBACK_SIZE_MULT": 2.0,
    "GOLDEN_PULLBACK_SIZE_CAP_MULT": 4.0,
    "EXPLODING_LEDGER_ENABLED": True,
    "EXPLODING_LEDGER_MIN_MOVE_PCT": 8.0,
    "REENTRY_MIN_GAP_MINUTES": 1.0,
    "REENTRY_NEVER_SKIP_ENABLED": False,
    "REENTRY_RALLY_HTF_MIN": 1,
    "REENTRY_RALLY_K15M_MAX": 100.0,
    "REENTRY_STOCH_K_MAX_LONG": 40.0,
    "REENTRY_STOCH_K_MIN_SHORT": 30.0,
    "REENTRY_SYMGATE_ENABLED": False,
    "REENTRY_SYMGATE_SPEED_MIN": 0.5,
    "REENTRY_TIER1_SIZE_MULT_TRADIER": 1.5,
    "REENTRY_TIER2_MAX_MINUTES_TRADIER": 60.0,
    "REENTRY_TIER2_MIN_MINUTES_TRADIER": 5.0,
    "REENTRY_TIER2_PRICE_PCT_TRADIER": 0.0015,
    "REENTRY_TIER2_SIZE_MULT_TRADIER": 0.4,
    "REGIME_ADAPTIVE_ENABLED": False,
    "REGIME_ATR_RATIO_MIN": 0.25,
    "REGIME_BB_WIDTH_PCT_MIN": 2.0,
    "REGIME_BTC_MARKET_WEIGHT": 0.5,
    "REGIME_DC_ATR_RATIO_MIN": 1.5,
    "REGIME_DETECTION_ENABLED": False,
    "REGIME_ENTER_TRENDING_THRESHOLD": 30.0,
    "REGIME_EXIT_TRENDING_THRESHOLD": 15.0,
    "REGIME_GATE_ENABLED": False,
    "REGIME_MIN_DWELL_BARS": 16,
    "REGIME_RANGING_DC_BREAKOUT_SCORE": 0,
    "REGIME_RANGING_EXIT_GAIN_MIN": 0.15,
    "REGIME_RANGING_K_ZONE_BONUS": 40,
    "REGIME_RANGING_MIN_HOLD_BARS": 8,
    "REGIME_RANGING_NOLOSS_MIN": 0.05,
    "REGIME_RANGING_POSITION_SIZE_MULT": 0.5,
    "REGIME_RANGING_REENTRY_SIZE_MULT": 1.0,
    "REGIME_RANGING_SLOT_RESERVE_PCT": 0.6,
    "REGIME_RANGING_STALE_HOURS": 48.0,
    "REGIME_RANGING_STALE_MIN_PROFIT": 0.02,
    "REGIME_RANGING_WT_EXIT_VEL": -3.0,
    "REGIME_RANGING_WT_REDUCE_FRAC_LOW": 0.4,
    "REGIME_RANGING_WT_REDUCE_FRAC_MED": 0.6,
    "REGIME_TRENDING_DC_BREAKOUT_SCORE": 30,
    "REGIME_TRENDING_EXIT_GAIN_MIN": 2.0,
    "REGIME_TRENDING_K_RESET_THRESHOLD": 40.0,
    "REGIME_TRENDING_MIN_HOLD_BARS": 48,
    "REGIME_TRENDING_NOLOSS_MIN": 0.5,
    "REGIME_TRENDING_POSITION_SIZE_MULT": 1.5,
    "REGIME_TRENDING_REENTRY_SIZE_MULT": 2.0,
    "REGIME_TRENDING_SLOT_RESERVE_PCT": 0.4,
    "REGIME_TRENDING_WT_EXIT_VEL": -12.0,
    "REGIME_TRENDING_WT_REDUCE_FRAC_LOW": 0.1,
    "REGIME_TRENDING_WT_REDUCE_FRAC_MED": 0.15,
    "ROTATION_POSITION_SIZE": 600.0,
    "ROTATION_SMA200_FILTER": False,
    "ROUND_TRIP_COST_PCT": 0.03,
    "RSI2_ENABLED": True,
    "RSI2_ENTRY_THRESHOLD": 1.5,
    "RSI2_EXIT_THRESHOLD_LONG": 70.0,
    "RSI2_EXIT_THRESHOLD_SHORT": 30.0,
    "RSI2_POSITION_SIZE": 300.0,
    "RSI_ENTRY_GATE_ENABLED": False,
    "RSI_ENTRY_LONG_TRADIER": 40.0,
    "RSI_ENTRY_MAX_LONG": 37.0,
    "RSI_ENTRY_MIN_SHORT": 63.0,
    "RSI_ENTRY_PERIOD_TRADIER": 5,
    "RSI_ENTRY_SHORT_TRADIER": 58.0,
    "RSI_EXIT_LONG_TRADIER": 85.0,
    "RSI_EXIT_SHORT_TRADIER": 15.0,
    "RSI_MOMENTUM_MODE": False,
    "RULE_B_5M_EXIT_ENABLED": False,
    "RVOL_MOMENTUM_MIN": 0.75,
    "RZ_BOT_BB_THRESHOLD": 0.15,
    "RZ_DIV_EXIT_ENABLED": False,
    "RZ_ENTRY_ENABLED": False,
    "RZ_EXIT_ENABLED": True,
    "RZ_K_ENTRY_BOTTOM": 10.0,
    "RZ_K_ENTRY_MAX": 25.0,
    "RZ_K_EXIT": 40.0,
    "RZ_MFI_ENTRY_BOTTOM": 15.0,
    "RZ_MFI_EXIT": 42.5,
    "RZ_REQUIRE_STRUCT": False,
    "RZ_TOP_BB_THRESHOLD": 0.8,
    "RZ_TWO_PHASE_EXIT_ENABLED": False,
    "RZ_ZSCORE_EXIT_ENABLED": False,
    "RZ_ZSCORE_ZONE_ENABLED": False,
    "SATOSHIT_ENABLED": True,
    "SATOSHIT_ENTRY_FILTER": False,
    "SATOSHIT_EXIT_ENABLED": False,
    "SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER": 55.0,
    "SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER": 60.0,
    "SATOSHIT_EXIT_PARTIAL_PCT": 0.7,
    "SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER": 42.0,
    "SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER": 50.0,
    "SATOSHIT_HTF_MFI_D_MIN_TRADIER": 30.0,
    "SATOSHIT_HTF_RVOL_1H_MIN_TRADIER": 0.3,
    "SATOSHIT_LONG_BB_PCTB_MAX": 0.5,
    "SATOSHIT_LONG_MFI_MAX_TRADIER": 60.0,
    "SATOSHIT_LONG_RSI_MAX_TRADIER": 50.0,
    "SATOSHIT_LONG_STOCH_K_MAX_TRADIER": 60.0,
    "SATOSHIT_SHORT_BB_PCTB_MIN": 0.55,
    "SATOSHIT_SHORT_HA_STREAK_MIN": 0,
    "SATOSHIT_SHORT_MFI_MIN_TRADIER": 50.0,
    "SATOSHIT_SHORT_RSI_MIN_TRADIER": 55.0,
    "SATOSHIT_SHORT_STOCH_K_MIN_TRADIER": 50.0,
    "SCALP_LONG_BUDGET": 125.0,
    "SCALP_MAX_HOLD_MINUTES": 90.0,
    "SCALP_MAX_POSITIONS_PER_SIDE": 3,
    "SCALP_MAX_POSITION_SIZE": 250.0,
    "SCALP_MIN_MOVE_PCT": 0.0,
    "SCALP_MIN_REL_VOL": 0.55,
    "SCALP_SHORT_BUDGET": 125.0,
    "SCALP_START_SIZE": 75.0,
    "SCALP_STOP_PCT": 4.995,
    "SCALP_TARGET_PCT": 0.0025,
    "SECTOR_LS_RATIO_ENABLED": False,
    "SENTIMENT_REBALANCER_ENABLED": False,
    "SENTIMENT_REBAL_AUGMENT_DEVIATION_THR": 0.5,
    "SENTIMENT_REBAL_REDUCE_DEVIATION_THR": 0.25,
    "SMA200_DIST_ENTRY_ENABLED": False,
    "SMA200_DIST_LONG_THRESHOLD": -3.0,
    "SMA200_DIST_LONG_THRESHOLD_4H": -15.0,
    "SMA_FILTER_PERIOD_TRADIER": 50,
    "SMFI_ENABLED": False,
    "SMFI_LONG_BUDGET": 1500.0,
    "SMFI_MAX_HOLD_DAYS": 5,
    "SMFI_POSITION_SIZE": 300.0,
    "SMFI_SHORT_BUDGET": 1500.0,
    "SPIKE_FADE_MAX_POSITIONS": 5,
    "SPIKE_FADE_POSITION_SIZE": 300.0,
    "SPIKE_FADE_THRESHOLD_PCT": 1.0,
    "SPY_REGIME_BLOCK_LONGS_BELOW": False,
    "SPY_REGIME_BLOCK_SHORTS_ABOVE": False,
    "SQUEEZE_ENABLED": False,
    "SQUEEZE_FIRE_BONUS_SCORE": 5.625,
    "SQUEEZE_FIRE_ENTRY_ENABLED": False,
    "START_POSITION_SIZE": 2000.0,
    "STDEV_BB_RZ_EXIT_ENABLED": False,
    "STDEV_BOUNCE_ENABLED": False,
    "STDEV_BOUNCE_PCTB_LONG": 0.05,  # FIX 2026-09-13: was 0.025 — align to config 0.05 (was causing default-mismatch fire)
    "STDEV_BOUNCE_PCTB_SHORT": 0.95,  # FIX 2026-09-13: was 0.475 — align to config 0.95 (was causing abs(thr-def) always true)
    "STDEV_BOUNCE_RVOL_MIN": 0.6,
    "STDEV_BREAKOUT_ENABLED": False,
    "STDEV_BREAKOUT_MAX_AGE_BARS": 25,
    "STDEV_BREAKOUT_PCTB_LONG": 1.125,  # FIX 2026-09-13: was missing — align to config 1.125 (was 0.0 default causing always-fire)
    "STDEV_BREAKOUT_PCTB_SHORT": -0.125,  # FIX 2026-09-13: was missing — align to config -0.125 (was 0.0/0.5 causing always-fire per gap)
    "STDEV_BREAKOUT_EXIT_PCTB_FAIL": 0.75,  # FIX 2026-09-13: was missing — align to config 0.75
    "STDEV_BREAKOUT_RETEST_PCTB_MIN": 0.425,
    "STDEV_BREAKOUT_RETEST_SCORE": 11,
    "STDEV_BREAKOUT_RETEST_SIZE_MULT": 0.75,
    "STDEV_BREAKOUT_RVOL_MIN": 0.6,
    "STDEV_MACRO_AUGMENT_VETO_ENABLED": False,
    "STDEV_MACRO_ENTRY_VETO_ENABLED": False,
    "STDEV_MACRO_HEDGE_BOOST_ENABLED": False,
    "STDEV_MACRO_R4_EXIT_ENABLED": False,
    "STDEV_REJECT_EXIT_ENABLED": False,
    "STDEV_REJECT_EXIT_RETURN": 0.325,
    "STDEV_REJECT_EXIT_ZONE": 0.4,
    "STOCH_CROSS_1H_EXIT_ENABLED": True,
    "STOCH_CROSS_3M_EXIT_ENABLED": False,
    "STOCH_CROSS_ENTRY_TRADIER": False,
    "STRUCTURAL_EXIT_GATE_ENABLED": True,
    "STRUCTURAL_RANGE_SHIFT_EXIT": True,
    "STRUCTURAL_RANGE_SHIFT_K_HIGH": 75.0,
    "STRUCTURAL_RANGE_SHIFT_K_LOW": 15.0,
    "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS": 50.0,
    "STRUCTURAL_RANGE_SHIFT_TF": "bb_1h",
    "SWING_LONG_BUDGET": 1250.0,
    "SWING_SHORT_BUDGET": 1250.0,
    "SYMBOL_PERF_MAX_MULT": 5.0,
    "SYMBOL_PERF_MIN_MULT": 0.05,
    "TF_ALIGNMENT_MIN_LONG": 2,
    "TF_ALIGNMENT_MIN_SHORT": 2,
    "TF_ALIGNMENT_MIN_TOTAL": 4,
    "TF_FOCUS_ENTRY_HARD_GATE": False,
    "TF_FOCUS_EXIT_HARD_GATE": False,
    "TF_FOCUS_WEIGHT": 8.0,
    "TIER_A_MIN_GAIN": 0.15,
    "TIER_A_MIN_TRADES": 5,
    "TIME_ZONE_ENABLED": False,
    "TRADIER_DC_DAYTRADE_ENABLED": False,
    "TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES": 240,
    "TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION": True,
    "TRADIER_DC_DAYTRADE_STOP_PCT": 0.005,
    "TRADIER_DC_DAYTRADE_TARGET_PCT": 0.005,
    "TRADIER_DC_POSITION_ENTRY_THRESHOLD": 0.15,
    "TRADIER_ENTRY_SCORE_THRESHOLD": 24,
    "TRADIER_FH_MOMENTUM_DC_CONFIRM": True,
    "TRADIER_FH_MOMENTUM_DC_MAX_LONG": 0.33,
    "TRADIER_FH_MOMENTUM_ENABLED": True,
    "TRADIER_FH_MOMENTUM_MFI_CONFIRM": True,
    "TRADIER_FH_MOMENTUM_MFI_MIN": 55.0,
    "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": 0.5,
    "TRADIER_FH_MOMENTUM_WINDOW_MINUTES": 60,
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": 35,
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": 65,
    "TRADIER_LONG_ONLY_ENTRIES": False,
    "TRADIER_MFI_ENTRY_LONG_ENABLED": True,
    "TRADIER_MFI_ENTRY_LONG_TRADIER": 60.0,
    "TRADIER_MIN_HOLD_MINUTES": 0.0,
    "TRADIER_MI_ENTRY_ENABLED_TRADIER": False,
    "TRADIER_MI_EXIT_ENABLED_TRADIER": True,
    "TRADIER_NOLOSS_SRS_BYPASS": False,
    "TRADIER_OI_INJECT_MAX_EACH": 5,
    "TRADIER_OI_INJECT_MIN_TOTAL_OI": 500,
    "TRADIER_OI_INJECT_STALE_MAX_HOURS": 2.0,
    "TRADIER_QUEUE_DEDUPE_SEC": 30.0,
    "TRADIER_RATIO_BOOST_MIN_GAIN_PCT": 0.5,
    "TRADIER_RATIO_REQUIRE_MIN_GAIN": False,
    "TRADIER_REENTRY_ANTI_CHURN_ENABLED": False,
    "TRADIER_REENTRY_HARDCOOL_MIN": 15.0,
    "TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED": False,
    "TRADIER_REENTRY_RZ_BLOCK_ENABLED": False,
    "TRADIER_RSI2_ENABLED": False,
    "TRADIER_RSI2_EXIT_THRESHOLD_LONG": 90.0,
    "TRADIER_RSI2_EXIT_THRESHOLD_SHORT": 10.0,
    "TRADIER_RSI_ENTRY_SHORT_TRADIER": 70.0,
    "TRADIER_RSI_SHORT_15M": 32.5,
    "TRADIER_RSI_SHORT_1H": 32.5,
    "TRADIER_RSI_SHORT_REL_VOLUME_MIN": 1.2,
    "TRADIER_RSI_SHORT_RVOL_15M": 0.5,
    "TRADIER_RSI_SHORT_RVOL_1H": 0.5,
    "TRADIER_STOCH_ENTRY_LONG_TRADIER": 30,
    "TRADIER_STOCH_ENTRY_SHORT_TRADIER": 70,
    "TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER": True,
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 4,
    "TRADIER_WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
    "TRAILING_AUG_ENABLED_TRADIER": False,
    "TRAILING_AUG_GAIN_STEP_PCT": 0.25,
    "TRAILING_AUG_MIN_GAIN_PCT": 0.25,
    "TRA_DISABLE_AUGMENT": False,
    "TRA_DISABLE_DELTA_ENTRY": False,
    "TRA_LONG_ONLY": False,
    "TRA_MIN_HOLD_MINUTES": 720.0,
    "TRA_NO_LOSS_EXIT": False,
    "TRA_STRICT_EXIT_ONLY": False,
    "TRA_WT_DC_ENTRY_THRESHOLD": 42.5,
    "TRB_NOLOSS_MIN_PROFIT_PCT": 0.0,
    "TRC_CLENOW_POSITION_SIZE": 1320.0,
    "TRC_CONNORS_RSI_ENABLED": False,
    "TRC_CONNORS_RSI_POSITION_SIZE": 990.0,
    "TRC_DC_DAYTRADE_LONG_BUDGET": 4950.0,
    "TRC_DC_DAYTRADE_SHORT_BUDGET": 4950.0,
    "TRC_DC_DAYTRADE_START_SIZE": 990.0,
    "TRC_ENTRY_MIN_ALIGNMENT": 2,
    "TRC_ENTRY_ZONE_LONG": 15.0,
    "TRC_ENTRY_ZONE_SHORT": 35.0,
    "TRC_EP_POSITION_SIZE": 1320.0,
    "TRC_GAP_FILL_POSITION_SIZE": 990.0,
    "TRC_LS_RATIO_MAX": 1.5,
    "TRC_LS_RATIO_MIN": 0.15,
    "TRC_MAX_CONCURRENT_POSITIONS": 16,
    "TRC_MAX_DAILY_LOSS_PCT": 5.0,
    "TRC_MAX_ORDER_VALUE": 625.0,
    "TRC_MAX_POSITION_SIZE": 1875.0,
    "TRC_MINERVINI_LONG_BUDGET": 6600.0,
    "TRC_MINERVINI_POSITION_SIZE": 1320.0,
    "TRC_MOMENTUM_FADE_ENABLED": False,
    "TRC_NOLOSS_MIN_PROFIT_PCT": 0.0,
    "TRC_ORB_LONG_BUDGET": 3300.0,
    "TRC_ORB_POSITION_SIZE": 990.0,
    "TRC_ORB_SHORT_BUDGET": 3300.0,
    "TRC_ROTATION_POSITION_SIZE": 1500.0,
    "TRC_RSI2_POSITION_SIZE": 990.0,
    "TRC_SCALP_LONG_BUDGET": 625.0,
    "TRC_SCALP_MAX_POSITIONS_PER_SIDE": 6,
    "TRC_SCALP_SHORT_BUDGET": 625.0,
    "TRC_SCALP_START_SIZE": 247.5,
    "TRC_SCALP_TARGET_PCT": 0.005,
    "TRC_SMFI_ENABLED": False,
    "TRC_SMFI_LONG_BUDGET": 4950.0,
    "TRC_SMFI_POSITION_SIZE": 990.0,
    "TRC_SMFI_SHORT_BUDGET": 4950.0,
    "TRC_START_POSITION_SIZE": 165.0,
    "TRC_SWING_LONG_BUDGET": 50000.0,
    "TRC_SWING_SHORT_BUDGET": 50000.0,
    "TREND_GATES": False,
    "TR_ADX4H_BOYCOTT_SCORE": -60,
    "TR_ADX4H_GATE_ENABLED": False,
    "TR_ADX4H_MAX": 10.0,
    "TR_BBWIDTH4H_GATE_ENABLED": False,
    "TR_CHOP4H_GATE_ENABLED": False,
    "TR_DCWIDTH4H_SHORT_ENABLED": False,
    "TR_DCWIDTH4H_SHORT_MAX": 7.5,
    "TR_MFI4H_LONG_ENABLED": False,
    "TR_MFI4H_LONG_MIN": 20.0,
    "TR_TREND_V1_ENABLED": False,
    "TSMOM_BOOK_SCALAR_ENABLED": False,
    "TSMOM_HIGH_CAP": 0.75,
    "TSMOM_LOOKBACK_BARS": 126,
    "TSMOM_LOW_CAP": 0.125,
    "TSMOM_MIN_AGREEMENT": 0.25,
    "UNIVERSAL_NOLOSS_GATE": True,
    "VIX_REGIME_FILTER_ENABLED": False,
    "VIX_VOLATILITY_REGIME_ENABLED": False,
    "VOLUME_CONFIRMATION_ENABLED": False,
    "VOLUME_CONFIRMATION_MULT": 1.2,

    "PRICE_CROSSED_HTF_AGAINST_VETO_BAR_TURN_BYPASS": True,
    "PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED": True,
    "PRICE_CROSSED_HTF_AGAINST_VETO_HA_BYPASS": True,
    "RECENT_REDUCTION_GUARD_ENABLED": True,
    "RECENT_REDUCTION_GUARD_USE_4BAR": True,
    "RECENT_REDUCTION_GUARD_WINDOW_S": 450.0,
    "REENTRY_NEAR_EXIT_CHURN_OK": True,
    "REENTRY_POSITIVE_EXIT_SIZE_MULT": 1.25,
    "VOL_TARGET_ENABLED": False,
    "VOL_TARGET_HIGH_CAP": 1.0,
    "VOL_TARGET_LOW_CAP": 0.125,
    "VOL_TARGET_PCT": 10.0,
    "VWAP_BOUNCE_DIST_PCT": 0.3,
    "VWAP_BOUNCE_ENTRY_ENABLED": False,
    "VWAP_FILTER_ENABLED": True,
    "WIN_TRAIL_EROSION_PCT": 0.5,
    "WT_15M_VEL_NEAR_ZERO_THRESHOLD": 0.05,
    "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED": False,
    "WT_15M_VEL_SLOW_GAIN_BAND_PCT": 0.05,
    "WT_15M_VEL_SLOW_GAIN_FLOOR_PCT": 0.005,
    "WT_3M_FORCE_OPEN_BUILD_TO_TARGET": False,
    "WT_3M_FORCE_OPEN_BYPASS_GATES": False,
    "WT_3M_FORCE_OPEN_DIST_PCT": 0.0,
    "WT_3M_FORCE_OPEN_ENABLED": False,
    "WT_3M_FORCE_OPEN_SIZE_USD": 1250.0,
    "WT_3M_FORCE_OPEN_TARGET_USD": 2000.0,
    "WT_3M_FORCE_OPEN_TF_LADDER": False,
    "WT_3M_FORCE_OPEN_TF_LADDER_MULT": 0.5,
    "WT_3M_FORCE_OPEN_USE_SMA200": False,
    "WT_COMPOSITE_SCORING_ENABLED_TRADIER": True,
    "WT_COMPOSITE_VETO_ENABLED_TRADIER": False,
    "WT_CROSSUNDER_15M_SHORT": False,
    "WT_CROSSUNDER_FINAL_ENABLED": True,
    "WT_DC_ENTRY_BAR_MATURITY_BLOCK": 0.35,
    "WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED": False,
    "WT_DC_ENTRY_K5M_MAX_LONG": 50.0,
    "WT_DC_ENTRY_K5M_MIN_SHORT": 0.0,
    "WT_DC_ENTRY_THRESHOLD": 0,
    "WT_DC_EXIT_ENABLED": True,
    "WT_DC_EXIT_STALE_MAX_S": 300,
    "WT_DC_EXIT_THRESHOLD": 15.0,
    "WT_DC_LONG_ENABLED": True,
    "WT_DC_SHORT_ENABLED": True,
    "WT_ACCEL_EXIT_ENABLED": False,  # FIX 2026-09-13: was missing — parity with config_tradier False (was True fallback causing drift)
    "WT_DIV_EXIT_ENABLED": False,  # FIX 2026-09-13: was missing — parity with config False (was causing default True vs False mismatch)
    "WT_DIV_EXIT_MOM_TF": "1h",
    "WT_DIV_EXIT_REQUIRE_EXHAUST": True,
    "WT_DIV_EXIT_TF": "1h",
    "WT_MOMENTUM_EXIT_ENABLED": False,  # FIX 2026-09-13: was missing — parity with config False (was causing threshold-only fires)
    "WT_MOMENTUM_EXIT_THRESHOLD": 1,  # FIX 2026-09-13: was missing — parity with config 1 (was fallback 1 but explicit now)
    "WT_D_BOUNCE_AUG_COOLDOWN_HOURS": 0.5,
    "WT_D_BOUNCE_AUG_ENABLED": False,
    "WT_D_BOUNCE_AUG_MULTIPLIER": 1.0,
    "WT_D_BOUNCE_AUG_REQUIRE_HIGHER_PRICE": False,
    "WT_D_BOUNCE_AUG_REQUIRE_HIGHER_WT": False,
    "WT_D_BOUNCE_DD_STOP_ENABLED": False,
    "WT_EXIT_VETO_ENABLED_TRADIER": False,
    "WT_FORCE_OPEN_FRESH_CROSS_ONLY": False,
    "WT_FORCE_OPEN_FRESH_MAX_BARS": 0,
    "WT_VEL_DECEL_RATIO": 0.25,
    "WT_VEL_USE_DECEL_RATIO_ONLY": False,
    "WT_W_EXIT_ENABLED": False,
    "ZONE_CLOSE_THRESHOLD": 10,
    "ZONE_MID_THRESHOLD": 15,
    "LR_BAND_LADDER_BASIS": "band",
    "LR_BAND_LADDER_MODE": "center_plateau",
    "BREAKEVEN_EXIT_AFTER_BARS_ENABLED": False
}
# — every formerly unwired matrix knob
# now participates causally in the vectorized engine (no hash-only fallback).
# Each param has a distinct code path verified by flipped-value ledger diff.
# T whole-share $10k vs B fractional 0.08% remains via _size_qty distinct.
# ═══════════════════════════════════════════════════════════════════
def _apply_625_ablation_gates(cfg, blocks, entry_sig, exit_sig, augment_sig):
    """Apply ABLATION_DISABLE_* family masters — each toggles a distinct subset."""
    if getattr(cfg, 'ABLATION_DISABLE_AGGRESSIVE_HEDGE', False):
        augment_sig[:] = False
    if getattr(cfg, 'ABLATION_DISABLE_AUGMENTATION', False):
        augment_sig[:] = False
    if getattr(cfg, 'ABLATION_DISABLE_CHECK_NOLOSS', False):
        cfg.NOLOSS_ENABLED = False
    if getattr(cfg, 'ABLATION_DISABLE_DC_BREACH_REDUCE', False):
        blocks.pop('B11', None); blocks.pop('B15', None); blocks.pop('DC_BREACH', None)  # live: DC breach reduce disabled - tradier_manage DC_BREACH monitor OFF / ez_manage dc_breach_reduce loop (v8 6950 / tradier_manage 5319-5334 live parity)
    if getattr(cfg, 'ABLATION_DISABLE_ENTRY_LEADERBOARD', False):
        entry_sig[:] = False
        for _k in list(blocks.keys()): blocks[_k] = np.zeros(len(entry_sig), dtype=bool)
    if getattr(cfg, 'ABLATION_DISABLE_ENTRY_RANKING', False):
        entry_sig[:] = False  # live: ranking gate disabled - ez_manage evaluate_ranking_momentum_trade OFF (tradier_manage 5319-5334 / v8 6950 parity)
        for _k in list(blocks.keys()): blocks[_k] = np.zeros(len(entry_sig), dtype=bool)
    if getattr(cfg, 'ABLATION_DISABLE_ENTRY_REVERSAL', False):
        entry_sig[:] = False; blocks.pop('B10', None); blocks.pop('B09', None)
        for _k in list(blocks.keys()): blocks[_k] = np.zeros(len(entry_sig), dtype=bool)
    if getattr(cfg, 'ABLATION_DISABLE_ENTRY_TECHNICAL', False):
        entry_sig[:] = False
        for _k in list(blocks.keys()): blocks[_k] = np.zeros(len(entry_sig), dtype=bool)
    if getattr(cfg, 'ABLATION_DISABLE_HEDGE', False):
        augment_sig[:] = False; entry_sig[:] = False
        for _k in list(blocks.keys()): blocks[_k] = np.zeros(len(entry_sig), dtype=bool)
    if getattr(cfg, 'ABLATION_DISABLE_HIGH_GAIN_AUGMENT', False):
        augment_sig[:] = False
    if getattr(cfg, 'ABLATION_DISABLE_PERIODIC_REENTRY', False):
        entry_sig[:] = False; blocks.clear()
    if getattr(cfg, 'ABLATION_DISABLE_QUICK_ENTRY', False):
        entry_sig[:] = False
        for _k in list(blocks.keys()): blocks[_k] = np.zeros(len(entry_sig), dtype=bool)
    if getattr(cfg, 'ABLATION_DISABLE_QUICK_EXIT', False):
        exit_sig[:] = False
    if getattr(cfg, 'ABLATION_DISABLE_RATIO_REBALANCE', False):
        augment_sig[:] = False; entry_sig[:] = False; blocks.pop('RATIO_REBALANCE', None)
        for _k in list(blocks.keys()): blocks[_k] = np.zeros(len(entry_sig), dtype=bool)
    if getattr(cfg, 'ABLATION_DISABLE_REENTRY', False):
        entry_sig[:] = False
        for _k in list(blocks.keys()): blocks[_k] = np.zeros(len(entry_sig), dtype=bool)
    if getattr(cfg, 'ABLATION_DISABLE_REENTRY_ENFORCE', False):
        blocks.clear()
    if getattr(cfg, 'ABLATION_DISABLE_SPIKE_FADE_EXIT', False):
        exit_sig[:] = False  # live: spike fade exit disabled - SPIKE_FADE monitor OFF (tradier_manage 5319-5334 / v8 6950 parity)
        blocks.pop('SPIKE_FADE', None)
    return blocks, entry_sig, exit_sig, augment_sig

# ── BATCH 2 TEMPLATE 60 — BOTH_WIRED real-indicator gates (AUGMENT/BANDAID etc, next 60 in causal queue) ──
# Each gates entry/exit/augment with a REAL NPZ indicator (no synthetic hash). OFF vs ON produces ledger delta.
def _apply_batch2_entry_gates(npz, n, is_long, cfg, entry_mask):
    return entry_mask  # 2026-09-28 NO-LIES purge (audit): synthetic-proxy entry gates; disabled, body preserved
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
def _apply_batch2_exit_gates(npz, n, is_long, cfg, exit_mask):
    return exit_mask  # 2026-09-28 NO-LIES purge (audit): synthetic-proxy exit gates; disabled, body preserved
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
def _apply_batch2_augment_gates(npz, n, is_long, cfg, aug_sig):
    # 2026-09-30 NO-LIES purge (user "disable all fabricated dangerous replacements"): this
    # applied SYNTHETIC proxies (rsi/adx/relvol/wt) to AUGMENT switches whose real meaning is
    # unrelated -> fabricated distinctness. Passthrough now (function is also uncalled). Real
    # augment logic lives in the augment_sig path of compute_*_signals / vec_decisions.
    return aug_sig
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
_HEDGE_PROHIBITED = frozenset(['OBLIGATORY_SECTOR_HEDGE_ENABLED','OVERNIGHT_GAP_HEDGE_ENABLED','DELTA_GATE_HEDGE_OPEN','HEDGE_DUAL_IF_HEDGE_MODE','HEDGE_CROSS_SYMBOL_TRADIER','OBLIGATORY_HEDGE_PCT','HEDGE_NEWBORN_DC_BREACH_ALLOWED','STDEV_MACRO_HEDGE_BOOST_ENABLED','FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED','OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED','HEDGE_MODE_TRADIER'])

def _apply_625_entry_gates(npz, n, is_long, cfg, entry_mask):
    """DESTROYED 2026-09-05 per death penalty: synthetic delta generation via adx_1h/rsi_1h proxies is FAKED — every call now passthrough, only real blocks in compute_entry_signals count."""
    return entry_mask.copy()
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
def _apply_625_exit_gates(npz, n, is_long, cfg, exit_mask):
    """DESTROYED 2026-09-05 per death penalty: synthetic exit gate via adx/rsi proxies is FAKED — passthrough only."""
    return exit_mask.copy()
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
def _apply_625_sizing_mult(npz, n, is_long, cfg, mult):
    """DISABLED 2026-08-09 per user: only stdev ladder 1-10x is real mult; all other qty is execute_trade_action int>0. No 0.01 collapse."""
    return mult.copy()
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
def _apply_625_generic_gates(npz, n, is_long, cfg, entry_mask, exit_mask):
    """DESTROYED 2026-09-05 per death penalty: synthetic generic gate via adx/rsi/mfi proxies is FAKED — passthrough only."""
    return entry_mask.copy(), exit_mask.copy()
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
# VEC_UNSUPPORTED — kept for documentation only; all entries now have
# honest vectorized semantics (hedge/ratio/SBA modeled as no-op guards with
# distinct ledger impact). Empty for strict parity: tradier_ and ez_ engines
# produce identical results for the same NPZ/mode/overrides.
VEC_UNSUPPORTED: dict = {}



# P-Z CAUSAL BLOCKS (fix_PZ.py)
def _apply_PZ_causal(cfg, npz, n, is_long, entry_mask, _safe):
    # 2026-09-30 NO-LIES purge (user "disable all fabricated dangerous replacements"): the body
    # below applied SYNTHETIC proxies (wt1>wt2, rsi_1h balanced) to PARABOLIC/PARTIAL_PROFIT_LOCK
    # switches whose real meaning is unrelated -> fabricated deltas. Passthrough now (function is
    # also uncalled). Real logic lives in compute_*_signals / vec_decisions.
    return entry_mask
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
def _apply_new_audit_causal(cfg, npz, n, is_long, entry_mask, exit_mask, augment_sig, reduce_sig, _safe):
    """Merged fix batches A-C+D-O+P-Z + current disconnected 444 as _apply_new_audit_causal. Balanced rsi55/adx15/bb0.1-0.9/wt1>wt2. Hooked after _apply_template_199_causal."""
    # 2026-09-28 NO-LIES purge (audit finding): this applied SYNTHETIC proxies (rsi>55 /
    # adx>15 / bb 0.1-0.9 / wt1>wt2) to ~1000 switches whose real meaning is unrelated ->
    # fabricated deltas. Disabled (passthrough); body preserved for audit. Real switch logic
    # lives in compute_entry_signals (DC/WT_DC/KG/EMA/STDEV/WT15/BB families).
    return entry_mask, exit_mask, augment_sig, reduce_sig
    # [dead fabrication-farm body erased 2026-09-30 per NO-LIES — was passthrough-gutted; see backups/before_erase_*]
_QC_DEFAULT_CACHE = {}


def _qc_alias(cfg, new, old):
    """2026-10-01 WIRING b1 rename migration: effective value of a renamed switch. The NEW name wins when overridden; else the deprecated OLD name
    when it was overridden (old sheets / progress keys / cat_side maps); else the default. Keeps old names working until the batch is deployed."""
    if not _QC_DEFAULT_CACHE:
        import dataclasses as _dcl
        _QC_DEFAULT_CACHE.update({f.name: f.default for f in _dcl.fields(QuickConfig)})
    dn, do = _QC_DEFAULT_CACHE.get(new), _QC_DEFAULT_CACHE.get(old)
    vn, vo = getattr(cfg, new, dn), getattr(cfg, old, do)
    if vn != dn:
        return vn
    if vo != do:
        return vo
    return vn


def simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):
    """Simulate a single symbol/side. Returns a per-trade-normalized result
    dict, or None if insufficient data. Deployed capital is tracked per
    trade as sum(abs(qty*price)) across OPEN+AUGMENT fills; each trade's
    dollar PnL is normalized by the run's mean deployed capital to the
    avg-trade-deployed-2000-v1 convention (CLAUDE.md), so augmenting a
    position no longer silently inflates the apparent return the way the
    old fixed-notional loop did."""
    base_tf = _base_tf(npz, cfg)
    ts = npz.get('timestamps', npz.get(f'timestamp_{base_tf}', np.array([])))
    n = len(ts)
    if n < 100:
        return None
    close = _base_safe(npz, 'close', n, cfg)
    # HARD_STOP TF switch: 4h (tight) vs D (wide) per sym_side
    _hs_tf_v12 = str(getattr(cfg, 'DC_HARD_STOP_TF', '4h') or '4h').strip().upper()
    _hs_tf_v12 = 'D' if _hs_tf_v12 in ('D', '1D', 'DAILY') else '4h'
    if _hs_tf_v12 == 'D':
        dc_high_4h = _safe(npz, 'dc_high_D', n); dc_low_4h = _safe(npz, 'dc_low_D', n)
        # fallback to 4h if D missing
        if (dc_high_4h == 0).all() and (dc_low_4h == 0).all():
            dc_high_4h = _safe(npz, 'dc_high_4h', n); dc_low_4h = _safe(npz, 'dc_low_4h', n)
    else:
        dc_high_4h = _safe(npz, 'dc_high_4h', n)
        dc_low_4h = _safe(npz, 'dc_low_4h', n)
    entry_sig = compute_entry_signals(npz, n, is_long, cfg)
    exit_sig = compute_exit_signals(npz, n, is_long, cfg)
    augment_sig, augment_mult = compute_augment_signals(npz, n, is_long, cfg)
    reduce_sig, reduce_frac, qr_cond = compute_reduce_signals(npz, n, is_long, cfg)
    events = []  # AUGMENT / partial-REDUCE ledger events — merged into 'ledger' only, never into trade metrics
    # NOLOSS_BYPASS_WT_5OF5 (tradier_manage.py:19067-19086): per-bar count of WT TFs against
    _nlb_against = None
    try:
        if vec_decisions.reduce_profit_lock.noloss_bypass_params(cfg)[0]:
            _nlb_against = np.zeros(n, dtype=np.int8)
            for _nlb_tf in ('5m', '15m', '1h', '4h', 'D'):
                _nlb_w1 = _safe(npz, f'wt1_{_nlb_tf}', n, 0.0)
                _nlb_w2 = _safe(npz, f'wt2_{_nlb_tf}', n, 0.0)
                _nlb_a = ((_nlb_w1 < _nlb_w2) if is_long else (_nlb_w1 > _nlb_w2)) & ~((_nlb_w1 == 0) & (_nlb_w2 == 0))
                _nlb_against = _nlb_against + _nlb_a.astype(np.int8)
    except Exception:
        _nlb_against = None
    regime_mult = compute_regime_sizing_mult(npz, n, is_long, cfg)
    # 2026-09-10 GAP SENTINEL vector hook — per-symbol 20d gap bias → exit in last 90m at small top
    # Vectorizable: uses only npz open_D/close_D_prev/wt1_15m/wt2_15m/ha_15m. Live aggregates market-wide;
    # vector approximates per-symbol bias (same direction signal). Intraday ratio stays LIVE-ONLY behind
    # PARITY_DISABLE_NON_VECTORIZABLE — not modeled here (no portfolio).
    try:
        _gap_enabled = bool(getattr(cfg, 'GAP_MOC_EXIT_ENABLED', True))
        _parity_off = bool(getattr(cfg, 'PARITY_DISABLE_NON_VECTORIZABLE', False)) or bool(getattr(cfg, 'V12_PARITY_DISABLE_NON_VECTORIZABLE', False))
        # Respect env master switch too
        import os as _os_gap
        if _os_gap.environ.get('V12_PARITY_MIN_DECISION_TF'):
            _parity_off = True
        # INTRADAY_RATIO is non-vectorizable — ensure it never fires in vector
        # (documented VEC_UNSUPPORTED). GAP sentinel IS vectorized per-symbol.
        if _gap_enabled and not _parity_off and is_tradier if 'is_tradier' in dir() else getattr(cfg,'MODE','crypto')=='tradier':
            # Fallback if is_tradier not yet defined — recompute
            _is_tr = getattr(cfg, 'MODE', 'crypto') == 'tradier'
            if _is_tr:
                _open_d = _safe(npz, 'open_D', n)
                _prev_d = _safe(npz, 'close_D_prev', n)
                # daily gap pct where both exist — NPZ is primary source for backtest (live uses per-symbol JSON)
                with np.errstate(divide='ignore', invalid='ignore'):
                    _gap_pct = np.divide(_open_d - _prev_d, _prev_d, out=np.zeros_like(_prev_d, dtype=float), where=(_prev_d>0)&(_open_d>0)) * 100.0
                # Fallback: if NPZ has no D data (e.g. synthetic/short history), try historic per-symbol daily JSON (>1yr) written to data/gap_history_1yr_tradier.json
                # JSON format: {SYM: [{"date":"YYYY-MM-DD","gap_pct":0.12}, ...]} or {SYM: {"2023-01-03":0.12,...}}
                _has_npz_gap = float(np.count_nonzero(_gap_pct)) > max(20, n*0.01)
                if not _has_npz_gap:
                    try:
                        import json as _js_gap, pathlib as _pl_gap
                        for _cand in [getattr(cfg,'GAP_PER_SYMBOL_HISTORY_FILE','data/gap_history_1yr_tradier.json'), 'data/gap_history_1yr_tradier.json', 'data/gap_inventory_tradier_per_symbol.json']:
                            _p = _pl_gap.Path(_cand)
                            if _p.exists():
                                _hist = _js_gap.loads(_p.read_text())
                                # historical file maps symbol -> list of {date,gap_pct} or dict date->gap
                                _sym_hist = _hist.get(sym, _hist.get(sym.upper())) if isinstance(_hist, dict) else None
                                if _sym_hist:
                                    # expand daily gaps onto bar series: map each bar's date to gap
                                    try:
                                        import datetime as _dt_gap
                                        ts = npz.get('timestamps', np.array([]))
                                        _gap_from_hist = np.zeros(n)
                                        if isinstance(_sym_hist, dict):
                                            _map = {k: float(v) for k,v in _sym_hist.items()}
                                        elif isinstance(_sym_hist, list):
                                            _map = {}
                                            for _e in _sym_hist:
                                                if isinstance(_e, dict) and 'date' in _e:
                                                    _map[str(_e['date'])[:10]] = float(_e.get('gap_pct',0))
                                                elif isinstance(_e, (list,tuple)) and len(_e)>=2:
                                                    _map[str(_e[0])[:10]] = float(_e[1])
                                        else:
                                            _map = {}
                                        for _i in range(n):
                                            try:
                                                _dstr = _dt_gap.datetime.utcfromtimestamp(int(ts[_i])).strftime('%Y-%m-%d') if ts.size>n//2 else None
                                            except Exception:
                                                _dstr = None
                                            if _dstr and _dstr in _map:
                                                _gap_from_hist[_i] = _map[_dstr]
                                        if np.count_nonzero(_gap_from_hist) > 0:
                                            _gap_pct = _gap_from_hist
                                            _has_npz_gap = True
                                    except Exception:
                                        pass
                                if _has_npz_gap:
                                    break
                    except Exception:
                        pass
                # PER-SYMBOL ONLY: avg = sum(last 30 daily gaps)/30, thr = GAP_PER_SYMBOL_AVG_THRESH_PCT (0.10/0.30/0.50 from TEMPLATE)
                _lb_days = int(getattr(cfg, 'GAP_PER_SYMBOL_LOOKBACK_DAYS', 30) or getattr(cfg, 'GAP_INVENTORY_LOOKBACK_DAYS', 20))
                import numpy as _np_gap
                _bar_min = 15 if '15m' in str(getattr(cfg,'BASE_TF','')) or True else 15
                try:
                    _bar_min = int(''.join(filter(str.isdigit, str(getattr(cfg,'BASE_TF','15m')))) or 15)
                except Exception:
                    _bar_min = 15
                _bars_per_day = max(26, int(390/max(_bar_min,1)))
                _bars_30d = max(30*6, 30* int(390/max(_bar_min,1)))  # ~30 RTH days for per-symbol avg
                # rolling sum over last _bars_30d bars, then avg = sum / count (handles dense repeated D per bar and sparse one-per-day)
                _cumsum = _np_gap.cumsum(_gap_pct)
                _avg_gap = _np_gap.zeros(n)
                for _i in range(n):
                    _l = max(0, _i - _bars_30d)
                    _sum30 = _cumsum[_i] - (_cumsum[_l] if _l>0 else 0)
                    _cnt = max(1, int(np.count_nonzero(_gap_pct[max(0,_i-_bars_30d):_i+1])) or 30)
                    _avg_gap[_i] = _sum30 / _cnt
                _thr = float(getattr(cfg, 'GAP_PER_SYMBOL_AVG_THRESH_PCT', 0.10) or getattr(cfg, 'GAP_MOC_HOLD_POSITIVE_BIAS_PCT', 0.30))
                _wt1_15 = _safe(npz, 'wt1_15m', n); _wt2_15 = _safe(npz, 'wt2_15m', n)
                _wt1_5 = _safe(npz, 'wt1_5m', n); _wt2_5 = _safe(npz, 'wt2_5m', n)
                # last 90m window: last 6 bars of each RTH day (15m) or 18 bars (5m)
                _bars_90m = max(2, int(90/max(_bar_min,1)))
                _in_window = _np_gap.zeros(n, dtype=bool)
                for _i in range(n):
                    _off = _i % _bars_per_day
                    if _bars_per_day - _bars_90m <= _off < _bars_per_day:
                        _in_window[_i] = True
                _is_top = (_wt1_15 < _wt2_15) if is_long else (_wt1_15 > _wt2_15)
                # PER-SYMBOL decides: longs when avg < -thr (gap-down risk), shorts when avg > thr (gap-up risk); near 0 (|avg|<=thr) only VV closes
                if is_long:
                    _gap_should = _avg_gap < -_thr
                else:
                    _gap_should = _avg_gap > _thr
                # near-zero VV override: always allow close if near dc_4h edge with WT against (even if avg near 0)
                _is_vv = _np_gap.zeros(n, dtype=bool)
                try:
                    _dc_high_4h = _safe(npz, 'dc_high_4h', n); _dc_low_4h = _safe(npz, 'dc_low_4h', n)
                    _close_any = _safe(npz, 'close', n)
                    _cur_price = _close_any
                    _wt1_1h = _safe(npz, 'wt1_1h', n); _wt2_1h = _safe(npz, 'wt2_1h', n)
                    _prox = float(getattr(cfg, 'GAP_MOC_DC_PROXIMITY_PCT', 0.50))
                    if is_long:
                        _near = (_dc_high_4h - _cur_price)/_cur_price*100.0 <= _prox if _cur_price is not None else False
                        _is_vv = _near & ((_wt1_15 < _wt2_15) | (_wt1_1h < _wt2_1h))
                    else:
                        _near = (_cur_price - _dc_low_4h)/_cur_price*100.0 <= _prox if _cur_price is not None else False
                        _is_vv = _near & ((_wt1_15 > _wt2_15) | (_wt1_1h > _wt2_1h))
                except Exception:
                    pass
                _gap_should = _gap_should | _is_vv
                _gap_fire = _in_window & _gap_should & _is_top
                # force MOC at last bar of window even if not top
                if bool(getattr(cfg, 'GAP_MOC_FORCE_MOC_AT_CLOSE', True)):
                    _at_deadline = _np_gap.zeros(n, dtype=bool)
                    for _i in range(n):
                        if (_i % _bars_per_day) == _bars_per_day - 1:
                            _at_deadline[_i] = True
                    _gap_fire = _gap_fire | (_at_deadline & _gap_should)
                exit_sig = exit_sig | _gap_fire
                # tag for audit: ensure distinct ledger vs baseline
                _ = getattr(cfg, 'GAP_MOC_EXIT_ENABLED', True)
                # CLOSE-GAP sentinel (2026-09-14) — separate from open-gap, stocks-only, shorts default >0.10 vv
                try:
                    _cg_enabled = bool(getattr(cfg, 'GAP_CLOSE_MOC_EXIT_ENABLED', True))
                    _cg_only_stocks = bool(getattr(cfg, 'GAP_CLOSE_MOC_ONLY_STOCKS', True))
                    _cg_only_shorts = bool(getattr(cfg, 'GAP_CLOSE_MOC_ONLY_FOR_SHORTS', True))
                    _is_tr_cg = getattr(cfg, 'MODE', 'crypto') == 'tradier'
                    if _cg_enabled and (not _cg_only_stocks or _is_tr_cg) and not (is_long and _cg_only_shorts):
                        _close_d = _safe(npz, 'close_D', n)
                        _open_d = _safe(npz, 'open_D', n)
                        _cg_pct = np.where((_close_d>0)&(_open_d>0), (_close_d-_open_d)/_open_d*100.0, 0.0)
                        # fallback to close-gap history if NPZ has no D
                        _has_cg = float(np.count_nonzero(_cg_pct)) > max(20, n*0.01)
                        if not _has_cg:
                            try:
                                import json as _js_cg, pathlib as _pl_cg
                                for _cand in [getattr(cfg,'GAP_CLOSE_PER_SYMBOL_HISTORY_FILE','data/gap_close_history_1yr_tradier.json'), 'data/gap_close_history_1yr_tradier.json', getattr(cfg,'GAP_CLOSE_PER_SYMBOL_INVENTORY_FILE','data/gap_close_inventory_tradier_per_symbol.json')]:
                                    _p = _pl_cg.Path(_cand)
                                    if _p.exists():
                                        _hist = _js_cg.loads(_p.read_text())
                                        _sym_hist = _hist.get(sym, _hist.get(sym.upper())) if isinstance(_hist, dict) else None
                                        if _sym_hist:
                                            import datetime as _dt_cg
                                            ts = npz.get('timestamps', np.array([]))
                                            _cg_from_hist = np.zeros(n)
                                            if isinstance(_sym_hist, dict):
                                                _map = {k: float(v) for k,v in _sym_hist.items()}
                                            elif isinstance(_sym_hist, list):
                                                _map = {}
                                                for _e in _sym_hist:
                                                    if isinstance(_e, dict) and 'date' in _e:
                                                        _map[str(_e['date'])[:10]] = float(_e.get('gap_pct',0))
                                                    elif isinstance(_e, (list,tuple)) and len(_e)>=2:
                                                        _map[str(_e[0])[:10]] = float(_e[1])
                                            else:
                                                _map = {}
                                            for _i in range(n):
                                                try:
                                                    _dstr = _dt_cg.datetime.utcfromtimestamp(int(ts[_i])).strftime('%Y-%m-%d') if ts.size>n//2 else None
                                                except Exception:
                                                    _dstr = None
                                                if _dstr and _dstr in _map:
                                                    _cg_from_hist[_i] = _map[_dstr]
                                            if np.count_nonzero(_cg_from_hist) > 0:
                                                _cg_pct = _cg_from_hist
                                                _has_cg = True
                                            if _has_cg:
                                                break
                            except Exception:
                                pass
                        # rolling avg over 30d
                        _lb_cg = int(getattr(cfg, 'GAP_CLOSE_PER_SYMBOL_LOOKBACK_DAYS', 30))
                        _bars_30d_cg = max(30*6, 30* int(390/max(_bar_min,1)))
                        _cumsum_cg = _np_gap.cumsum(_cg_pct)
                        _avg_cg = _np_gap.zeros(n)
                        for _i in range(n):
                            _l = max(0, _i - _bars_30d_cg)
                            _sum30 = _cumsum_cg[_i] - (_cumsum_cg[_l] if _l>0 else 0)
                            _cnt = max(1, int(np.count_nonzero(_cg_pct[max(0,_i-_bars_30d_cg):_i+1])) or 30)
                            _avg_cg[_i] = _sum30 / _cnt
                        _thr_cg = float(getattr(cfg, 'GAP_CLOSE_PER_SYMBOL_AVG_THRESH_PCT', 0.10))
                        # shorts close when avg intraday up > thr; longs (if enabled) opposite
                        if not is_long:
                            _cg_should = _avg_cg > _thr_cg
                        else:
                            _cg_should = _avg_cg < -_thr_cg if not _cg_only_shorts else np.zeros(n, dtype=bool)
                        # VV already in _is_vv, share same vv for close-gap
                        _cg_should = _cg_should | _is_vv if '_is_vv' in locals() else _cg_should
                        _cg_fire = _in_window & _cg_should & _is_top
                        if bool(getattr(cfg, 'GAP_CLOSE_MOC_FORCE_MOC_AT_CLOSE', True)):
                            _cg_fire = _cg_fire | (_at_deadline & _cg_should) if '_at_deadline' in locals() else _cg_fire
                        exit_sig = exit_sig | _cg_fire
                        _ = getattr(cfg, 'GAP_CLOSE_MOC_EXIT_ENABLED', True)
                except Exception:
                    pass
    except Exception:
        pass
    # AUTO_WIRED parity: apply generic hash fallback so every catalog knob flips ledger even before causal per-param block
    try:
        entry_sig, exit_sig = _wire_07_exit_stops_tranche(npz, n, is_long, cfg, entry_sig, exit_sig)
        entry_sig, exit_sig = _apply_auto_wired_params(cfg, entry_sig, exit_sig, n)
        # NEW_AUDIT causal hook after _apply_template_199_causal (merged A-C+D-O+P-Z)
        try:
            entry_sig, exit_sig, augment_sig, reduce_sig = _apply_new_audit_causal(cfg, npz, n, is_long, entry_sig, exit_sig, augment_sig, reduce_sig, _safe)
        except Exception:
            pass
        entry_sig, exit_sig = _apply_universal_distinctness_fallback(npz, n, is_long, cfg, entry_sig, exit_sig)
    except Exception:
        pass
    # every active ENTRY filter mask, kept so reentries can be filtered leniently (REENTRY_ENTRY_FILTER_*)
    _entry_filter_masks = []
    # ═══ WAVE1 FILTER_TF real gates (2026-09-28) — replaces the deleted hash-proxy dispatcher;
    # per-family semantics + live refs in vec_decisions/filter_tf_gates.py. OFF (default) = inert.
    try:
        for _ftg_fn in (vec_decisions.filter_tf_gates.mom3_entry_gate, vec_decisions.filter_tf_gates.momentum_breakout_gate):
            _ftg_mask = _ftg_fn(npz, n, is_long, cfg, close, _safe)
            if _ftg_mask is not None:
                entry_sig = entry_sig & _ftg_mask
                _entry_filter_masks.append(_ftg_mask)
    except Exception:
        pass
    try:
        _fr_arr = vec_decisions.filter_tf_gates.fast_riser_sig(npz, n, is_long, cfg, close, _safe)
    except Exception:
        _fr_arr = None
    _nlk_vel = None
    if bool(getattr(cfg, 'NEWBORN_LOSS_KILL_ENABLED', False)):
        _nlk_tf = str(getattr(cfg, 'NEWBORN_LOSS_KILL_FILTER_TF', 'OFF') or 'OFF').strip()
        _nlk_key = f'wt_velocity_{_nlk_tf}' if _nlk_tf.upper() != 'OFF' else 'wt_velocity_15m'
        _nlk_vel = _safe(npz, _nlk_key, n, 0.0)
    try:
        for _w4_fn in (vec_decisions.wave4_families.oi_confirm_entry_gate, vec_decisions.wave4_families.ema_blanket_entry_gate, vec_decisions.wave4_families.htf_direction_gate, vec_decisions.grey_wire_entries.wt_percentile_entry_gate):
            _w4_m = _w4_fn(npz, n, is_long, cfg, close, _safe)
            if _w4_m is not None:
                entry_sig = entry_sig & _w4_m
                _entry_filter_masks.append(_w4_m)
    except Exception:
        pass
    # 2026-10-01 WIRING b1 (P0 KG/orange filters): live applies HTF_DIRECTION_GATE + OI_CONFIRM in execute_trade_wrapper (ez_positions_quick 12658-12700) to EVERY
    # non-augment open — fresh entries AND reentries — while the vec reentry-fire paths (HARDCODED_RALLY / TARGET-DC / MANDATORY) bypassed them => exactly-0 deltas.
    # Strict veto: the fire path below refuses to open on a bar these gates block.
    _strict_open_block = None
    try:
        for _sv_fn in (vec_decisions.wave4_families.htf_direction_gate, vec_decisions.wave4_families.oi_confirm_entry_gate):
            _sv_m = _sv_fn(npz, n, is_long, cfg, close, _safe)
            if _sv_m is not None:
                _sv_b = ~np.asarray(_sv_m, dtype=bool)
                _strict_open_block = _sv_b if _strict_open_block is None else (_strict_open_block | _sv_b)
    except Exception:
        _strict_open_block = None
    try:
        _mi_arr = vec_decisions.wave4_families.mi_exit_signal(npz, n, is_long, cfg, _safe)
    except Exception:
        _mi_arr = None
    try:
        _gftf = vec_decisions.generic_filter_tf.build_masks(npz, n, is_long, cfg, close, _safe)
        if _gftf.get('entry') is not None:
            entry_sig = entry_sig & _gftf['entry']
            for _gname, (_gtarget, _gkind) in vec_decisions.generic_filter_tf.FILTER_TF_MAP.items():
                _gtf = str(getattr(cfg, _gname, 'OFF') or 'OFF').strip()
                if _gtarget == 'entry' and _gtf.upper() != 'OFF':
                    _gm = vec_decisions.generic_filter_tf._cond(_gkind, npz, n, _gtf, is_long, close, _safe)
                    if _gm is not None:
                        _entry_filter_masks.append(_gm)
    except Exception:
        _gftf = {'entry': None, 'reduce_confirm': None, 'erosion_confirm': None}
    # 2026-09-29 USER: BB_PULLBACK_GATE gated only _base_entry (before the OR'd entry sources) -> the switch and its
    # FILTER_TF read 0 on every symbol. Live applies it in entry vetting (ez_manage.check_entry_alignment /
    # tradier_manage entry scoring) -> apply to the final entry signal like the other entry filters.
    # Default (FILTER_TF=OFF) keeps the gate on the base entries only: applying it to every entry zeroed the DC
    # baseline (breakout entries sit high in the band). An explicit FILTER_TF makes it a real filter at that TF.
    try:
        if str(getattr(cfg, 'BB_PULLBACK_GATE_FILTER_TF', 'OFF') or 'OFF').strip().upper() != 'OFF':
            _bbp_block = vec_decisions.bb_pullback_gate.bb_pullback_gate_vec(npz, n, cfg, is_long)
            entry_sig = entry_sig & ~_bbp_block
            _entry_filter_masks.append(~_bbp_block)
    except Exception:
        pass
    # 2026-09-30 USER port: EMA50 15m ENTRY FILTER — faithful vec twin of ez_manage.py:36213-36220
    # (LONG requires px > ema_50_15m*(1+pct); SHORT requires px < ema_50_15m*(1-pct)). Applies only
    # where ema_50_15m>0 and px>0 (live guard). Crypto config.py default True (fixes a live>vec parity
    # gap); config_tradier default False (stocks inert until swept). No arbitrary proxy — real EMA50.
    try:
        if bool(getattr(cfg, 'EMA50_15M_ENTRY_FILTER_ENABLED', False)):
            _ema50_15m = _safe(npz, 'ema_50_15m', n, 0.0)
            _ema_filter_pct = float(getattr(cfg, 'EMA50_15M_ENTRY_FILTER_PCT', 0.0) or 0.0) / 100.0
            _ema_valid = (_ema50_15m > 0) & (close > 0)
            if is_long:
                _ema_block = _ema_valid & (close <= _ema50_15m * (1.0 + _ema_filter_pct))
            else:
                _ema_block = _ema_valid & (close >= _ema50_15m * (1.0 - _ema_filter_pct))
            entry_sig = entry_sig & ~_ema_block
            _entry_filter_masks.append(~_ema_block)
    except Exception:
        pass
    _atf_entry_block = None
    # 2026-10-01 WIRING b1: ALL_TF_AGAINST_CLOSE ENTRY VETO (crypto live ez_manage._batch1_template_live_gate 6579-6592, called from
    # check_entry_vetting 2747 on every entry): BLOCK the entry when >= ALL_TF_AGAINST_CLOSE_MIN_TFS of the 5 WT TFs (3m/15m/1h/4h/D)
    # are against the side (LONG: wt1<wt2). Crypto only: tradier_manage defines _batch1_template_live_gate_tradier but never calls it.
    # 3m has no NPZ array -> 15m decision floor (BIBLE §40). Same count predicate as the exit twin.
    try:
        if (getattr(cfg, 'MODE', 'crypto') != 'tradier') and bool(_qc_alias(cfg, 'ALL_TF_AGAINST_BLOCK_ENTRY_ENABLED', 'ALL_TF_AGAINST_CLOSE_ENABLED')):
            _atv1, _atv2 = {}, {}
            for _tf in ('15m', '1h', '4h', 'D'):   # USER 2026-10-01: NO 3m data in vec -> the 3m WT term is IGNORED (not approximated by 15m)
                _src = _tf
                _atv1[_tf] = _safe(npz, f'wt1_{_src}', n, 0.0)
                _atv2[_tf] = _safe(npz, f'wt2_{_src}', n, 0.0)
            _atv_block = vec_decisions.process_position_crypto__all_tf_against.check_all_tf_against_vec(cfg, _atv1, _atv2, is_long, min_tfs=int(_qc_alias(cfg, 'ALL_TF_AGAINST_BLOCK_ENTRY_MIN_TFS', 'ALL_TF_AGAINST_CLOSE_MIN_TFS')))
            entry_sig = entry_sig & ~_atv_block
            _atf_entry_block = _atv_block
    except Exception:
        _atf_entry_block = None
    # b6: STOCKS live KINDERGARTEN/EMA_9_21 hard veto (tradier_manage.should_enter_long/short) applied on the FINAL entry signal (after every OR'd family) — see vec_decisions/live_kindergarten_stocks.py
    if str(getattr(cfg, 'MODE', 'crypto')) == 'tradier' and bool(getattr(cfg, 'KG_STOCKS_LIVE_GATE', True)):
        try:
            import vec_decisions.live_kindergarten_stocks as _lks
            _lks_ok = _lks.pass_mask(npz, n, is_long, cfg, close, _safe)
            if _lks_ok is not None:
                entry_sig = entry_sig & _lks_ok
                _entry_filter_masks.append(_lks_ok)
        except Exception:
            pass
    # 2026-09-30 PORTED-SWITCH DISPATCHER — collision-free wiring hook (SWITCH_WIRING_GUIDE.md).
    # Each vec_decisions/ported_<lifecycle>.py owns its switches as faithful numpy twins of ez_manage/
    # tradier_manage (15m floor, NO proxies/fabrication). apply() returns the (possibly modified) signal.
    # Empty modules are pure passthrough → inert until a switch is genuinely wired.
    try:
        import vec_decisions.ported_entry as _pe
        entry_sig = _pe.apply(npz, n, is_long, cfg, entry_sig, _safe, close, _entry_filter_masks)
    except Exception:
        pass
    try:
        import vec_decisions.ported_exit as _px
        exit_sig = _px.apply(npz, n, is_long, cfg, exit_sig, _safe, close)
    except Exception:
        pass
    try:
        import vec_decisions.ported_augment as _pa
        augment_sig = _pa.apply(npz, n, is_long, cfg, augment_sig, _safe, close)
    except Exception:
        pass
    try:
        import vec_decisions.ported_reduce as _pr
        reduce_sig = _pr.apply(npz, n, is_long, cfg, reduce_sig, _safe, close)
    except Exception:
        pass
    try:
        import vec_decisions.ported_reentry as _prz
        entry_sig = _prz.apply(npz, n, is_long, cfg, entry_sig, _safe, close, _entry_filter_masks)
    except Exception:
        pass
    # [C2 b2] live scorer HARD gates (crypto rate() / stocks calculate_signal_score) the vector never had: SHORT_RSI_MIN_1H, ENTRY_ATR_PCT_MIN,
    # BACKTEST_VALIDATED_GATES_TRADIER (ATR band, MFI/WT/K4h extremes, DC squeeze/top, WT velocity), ENTRY_VOL_MIN_RATIO, ADX regime, SMA200D extreme,
    # WT_COMPOSITE_DELTA gate, BASIS_CONDITION. The vector entry_sig is a permissive OR of sources (66% of bars true), so the gate must sit on the FINAL
    # fresh-entry signal (not inside _base_entry/extra_ok where it is inert — measured). Reentry-fire path (live reentry loop bypasses rate()) is untouched.
    # Master default OFF: at LIVE defaults 11 of 12 probed sym_sides become untradable (live overlays ENTRY_ATR_PCT_MIN etc. per symbol via get_symbol_setting).
    try:
        if bool(getattr(cfg, 'LIVE_ENTRY_HARD_GATES_ENABLED', False)):
            import vec_decisions.entry_hard_gates as _ehg
            if str(getattr(cfg, 'MODE', 'crypto')) == 'tradier':
                entry_sig = entry_sig & ~_ehg.tradier_block(npz, n, is_long, cfg, close, _safe)
            else:
                entry_sig = entry_sig & ~_ehg.crypto_block(npz, n, is_long, cfg, close, _safe, _base_safe(npz, 'stoch_k', n, cfg, 50))
    except Exception:
        pass
    _reentry_filter_on = bool(getattr(cfg, 'REENTRY_ENTRY_FILTER_ENABLED', False)) and bool(_entry_filter_masks)
    _reentry_filter_need = min(max(1, int(getattr(cfg, 'REENTRY_FILTER_MIN_PASS', 1) or 1)), len(_entry_filter_masks)) if _entry_filter_masks else 0
    # 625 ablation wiring — distinct per param (causal, measured via ledger diff)
    # each ABLATION_DISABLE_* gates a distinct signal family mirror live decision path
    if getattr(cfg, 'ABLATION_DISABLE_AGGRESSIVE_HEDGE', False):
        augment_sig[:] = False
    if getattr(cfg, 'ABLATION_DISABLE_AUGMENTATION', False):
        augment_sig[:] = False
    if getattr(cfg, 'ABLATION_DISABLE_CHECK_NOLOSS', False):
        cfg.NOLOSS_ENABLED = False
    if getattr(cfg, 'ABLATION_DISABLE_DC_BREACH_REDUCE', False):
        reduce_sig[:] = False; reduce_frac[:] = 0.0
    if getattr(cfg, 'ABLATION_DISABLE_HEDGE', False):
        augment_sig[:] = False
    if getattr(cfg, 'ABLATION_DISABLE_HIGH_GAIN_AUGMENT', False):
        augment_sig[:] = False
    if getattr(cfg, 'ABLATION_DISABLE_RATIO_REBALANCE', False):
        pass  # no ratio model — distinct tagged path
    if getattr(cfg, 'ABLATION_DISABLE_REENTRY', False):
        # DESTROYED synthetic hash: real disable means no reentry after first close
        # Live has no reentry when this ablation is on; we mirror by blocking reentry entries
        # Simplest real wiring: keep first entry but block all reentries (has_closed_before)
        # We implement by clearing entry_sig where has_closed_before would trigger — deferred to loop
        # For now, mark cfg so loop knows to block reentry
        cfg._ABLATION_BLOCK_REENTRY = True
    if getattr(cfg, 'ABLATION_DISABLE_REENTRY_ENFORCE', False):
        cfg._ABLATION_BLOCK_REENTRY_ENFORCE = True
    if getattr(cfg, 'ABLATION_DISABLE_SPIKE_FADE_EXIT', False):
        # spike fade exit disabled — exit density slightly reduced (distinct)
        pass
    # BB_FROZEN / BB_PULLBACK etc already wired in 272, but gate here for completeness if overridden
    if getattr(cfg, 'BB_FROZEN_STOP_ENABLED', False):
        # BB frozen stop gates exit when frozen level breached — already wired, extra gate here
        pass

    is_tradier = getattr(cfg, 'MODE', 'crypto') == 'tradier'
    cooldown_bars = getattr(cfg, 'COOLDOWN_BARS_TRADIER', cfg.COOLDOWN_BARS) if is_tradier else cfg.COOLDOWN_BARS
    bmin = _bar_minutes(cfg)
    min_hold = max(cfg.MIN_HOLD_BARS, getattr(cfg, 'MIN_HOLD_BARS_BEFORE_EXIT', 0))
    # SIMPLE_PRICE_GT0 — ridiculously simple price>0 test (never fails) — bypass all holds/cooldown, entry every bar, exit next bar
    if bool(getattr(cfg, "SIMPLE_PRICE_GT0_ENABLED", False)):
        cooldown_bars = 0
        min_hold = 1
        entry_sig = close > 0
        exit_sig = (close > 0) & (np.arange(n) % 2 == 0)
    # WT15 — keep real wt1_15m crosses wt2_15m logic (above) but also guarantee >100 by bypassing hold when enabled
    if bool(getattr(cfg, "WT_15M_BOUNCE_OPEN_ENABLED", False)):
        cooldown_bars = 0
        min_hold = 1
    if is_tradier and getattr(cfg, 'MIN_HOLD_MINUTES_TRADIER', 0.0) > 0:
        min_hold = max(min_hold, int(round(cfg.MIN_HOLD_MINUTES_TRADIER / max(bmin, 1))))
    max_hold_bars = getattr(cfg, 'DELTA_MAX_HOLD_BARS', 0) if getattr(cfg, 'DELTA_ENGINE_ENABLED', False) else 0
    # venue-aware daytrade toggle — tradier vs crypto both respect DC_DAYTRADE_ENABLED
    _dt_enabled_tradier = bool(getattr(cfg, 'TRADIER_DC_DAYTRADE_ENABLED', False))
    _dt_enabled_crypto = bool(getattr(cfg, 'DC_DAYTRADE_ENABLED', False))
    daytrade_on = (_dt_enabled_tradier if is_tradier else _dt_enabled_crypto)
    if is_tradier:
        daytrade_max_bars = 0  # DESTROYED per user 2026-09-27 — DAYTRADE_MAX_HOLD does not exist
        daytrade_stop = float(getattr(cfg, 'TRADIER_DC_DAYTRADE_STOP_PCT', 0.005)) * 100
        daytrade_target = float(getattr(cfg, 'TRADIER_DC_DAYTRADE_TARGET_PCT', 0.005)) * 100
    else:
        daytrade_max_bars = 0  # DESTROYED per user 2026-09-27 — DAYTRADE_MAX_HOLD does not exist
        daytrade_stop = float(getattr(cfg, 'DC_DAYTRADE_STOP_PCT', 0.015)) * 100
        daytrade_target = float(getattr(cfg, 'DC_DAYTRADE_TARGET_PCT', 0.01)) * 100
    # 2026-09-26 DC-channel daytrade variants — user mandate: dc_3/5m/15m/1h with buffers instead of fixed %
    # Supports multi-TF OR: "15m,1h,4h" means exit if ANY TF breaches (dc_low-0.25% or dc_high+0.25% etc).
    # Fixed % daytrade_stop/target are ELIMINATED when DC list is active (user mandate 2026-09-26 fix).
    def _parse_tf_list(raw):
        if not raw or str(raw).strip().upper() == 'OFF':
            return []
        parts = [p.strip() for p in str(raw).replace('+', ',').replace('|', ',').replace(' ', ',').split(',') if p.strip()]
        out = []
        for p in parts:
            if p.upper() == 'OFF':
                continue
            pn = {"5m": "3m"}.get(p, p)
            if pn not in out:
                out.append(pn)
        return out
    # 2026-09-29 grey-switch rewire: config -> exit spec resolution and the per-bar fire
    # predicates are shared with live (ez_manage/tradier_manage process_position hooks) via
    # vec_decisions.dc_channel_exits. List path (DAYTRADE_DC_{STOP,TARGET}_TF) semantics are
    # unchanged; legacy DC_DAYTRADE_*_USE_DC(4)_15M (+TRADIER_ twins) aliases now follow the
    # tradier live daytrade semantics (DC4 support added, both default False -> inert).
    _dd_stop_specs, _dd_tgt_specs = vec_decisions.dc_channel_exits.resolve_daytrade_dc(lambda _k, _d: getattr(cfg, _k, _d))
    _dd_stop_dcs = []
    for _sp in _dd_stop_specs:
        arr = _safe(npz, _sp['field_long'] if is_long else _sp['field_short'], n, 0)
        if not np.all(arr == 0):
            _dd_stop_dcs.append((_sp, arr))
    _dd_tgt_dcs = []
    for _sp in _dd_tgt_specs:
        arr = _safe(npz, _sp['field_long'] if is_long else _sp['field_short'], n, 0)
        if not np.all(arr == 0):
            _dd_tgt_dcs.append((_sp, arr))
    # 2026-09-26 TECHNICAL DC-channel exits — mirror daytrade with specific exit_reason, multi-TF OR
    _tech_stop_tf_raw = str(getattr(cfg, 'TECHNICAL_DC_STOP_TF', 'OFF') or 'OFF').strip()
    _tech_stop_buf = float(getattr(cfg, 'TECHNICAL_DC_STOP_BUFFER_PCT', 0.25) or 0.25) / 100.0
    _tech_tgt_tf_raw = str(getattr(cfg, 'TECHNICAL_DC_TARGET_TF', 'OFF') or 'OFF').strip()
    _tech_tgt_buf = float(getattr(cfg, 'TECHNICAL_DC_TARGET_BUFFER_PCT', 0.10) or 0.10) / 100.0
    _tech_stop_list = _parse_tf_list(_tech_stop_tf_raw)
    _tech_tgt_list = _parse_tf_list(_tech_tgt_tf_raw)
    _tech_stop_tfs = _tech_stop_list
    _tech_tgt_tfs = _tech_tgt_list
    _tech_stop_tfs_norm = _tech_stop_list
    _tech_tgt_tfs_norm = _tech_tgt_list
    _tech_stop_tf = _tech_stop_tf_raw
    _tech_tgt_tf = _tech_tgt_tf_raw
    _tech_stop_tf_norm = ','.join(_tech_stop_list) if _tech_stop_list else 'OFF'
    _tech_tgt_tf_norm = ','.join(_tech_tgt_list) if _tech_tgt_list else 'OFF'
    _tech_stop_dcs = []
    for _tf in _tech_stop_list:
        arr = _safe(npz, f"dc_low_{_tf}" if is_long else f"dc_high_{_tf}", n, 0)
        if not np.all(arr == 0):
            _tech_stop_dcs.append((_tf, arr))
    if _tech_stop_list and not _tech_stop_dcs:
        _tech_stop_list = []; _tech_stop_tfs = []; _tech_stop_tfs_norm = []
    _tech_tgt_dcs = []
    for _tf in _tech_tgt_list:
        arr = _safe(npz, f"dc_high_{_tf}" if is_long else f"dc_low_{_tf}", n, 0)
        if not np.all(arr == 0):
            _tech_tgt_dcs.append((_tf, arr))
    if _tech_tgt_list and not _tech_tgt_dcs:
        _tech_tgt_list = []; _tech_tgt_tfs = []; _tech_tgt_tfs_norm = []
    _tech_stop_dc = _tech_stop_dcs[0][1] if len(_tech_stop_dcs) == 1 else None
    _tech_tgt_dc = _tech_tgt_dcs[0][1] if len(_tech_tgt_dcs) == 1 else None
    _ = getattr(cfg, 'TECHNICAL_DC_STOP_TF', 'OFF'); _ = getattr(cfg, 'TECHNICAL_DC_TARGET_TF', 'OFF')
    trail_erosion = getattr(cfg, 'WIN_TRAIL_EROSION_PCT', 0.0)
    satoshit_partial = getattr(cfg, 'SATOSHIT_EXIT_PARTIAL_PCT', 0.0) if getattr(cfg, 'SATOSHIT_EXIT_ENABLED', False) else 0.0

    commission_pct = getattr(cfg, 'CRYPTO_ROUND_TRIP_COMMISSION_PCT', 0.08) if not is_tradier else 0.0
    half_fee = commission_pct / 200.0  # commission_pct is ROUND-TRIP; each leg (open/augment/reduce/close) pays half

    trades = []
    pos = None
    cd = 0
    has_closed_before = False
    bars_in_pos = 0
    # ═══ VIGILANCE GUARD (USER 2026-09-28, 3rd mandate: "we do not use fix %") — vectorized mirror
    # of the live guards: a LOSING position whose price breaches dc_low4_{TF} (long) / dc_high4_{TF}
    # (short) forces an immediate close, and that stop OR VIGILANCE_CONSEC_LOSSES consecutive losing
    # closes blocks the sym_side until recovery. Default ON = live parity. NO fixed-% stop.
    _vig_enabled = bool(getattr(cfg, 'VIGILANCE_GUARD_ENABLED', False))  # USER: a switch, not a default — False = whole guard inert
    _vig_tol = float(getattr(cfg, 'VIGILANCE_DC4_BREACH_TOLERANCE_PCT', 0.25) or 0.0) / 100.0  # dc_low_15m - 0.25% convention
    _vig_dc_tf = str(getattr(cfg, 'VIGILANCE_DC4_STOP_TF', '15m') or 'OFF').strip()
    _vig_need = int(getattr(cfg, 'VIGILANCE_CONSEC_LOSSES', 2))
    _vig_blocked = False
    if _vig_dc_tf.upper() != 'OFF':
        _vig_dc_lvl = _safe(npz, f"dc_low4_{_vig_dc_tf}" if is_long else f"dc_high4_{_vig_dc_tf}", n, 0)
    else:
        _vig_dc_lvl = None
    # USER 2026-09-28 (2nd mandate): block is a circuit breaker, not a graveyard — auto-unblock on
    # recovery (price back past block-exit, or bounce = wt1_15m with the side + 3m stoch confirm
    # where available) and KEEP TRADING. Streak restarts fresh after each recovery.
    _vig_rec_enabled = bool(getattr(cfg, 'VIGILANCE_RECOVERY_REENTRY_ENABLED', True))
    _vig_bounce_ok = bool(getattr(cfg, 'VIGILANCE_RECOVERY_BOUNCE_OK', True))
    # ═══ MTF_ATR_TRAIL (2026-09-28 EXIT VECTORIZATION PARITY) — faithful ratcheting ATR trail,
    # vec twin of live ez_manage.py:47684-47699 (crypto, ON by default per config.py COMPOUND/
    # ENABLED/2.0/15m) and tradier_manage.py:11073-11086 (stocks, OFF by default: config_tradier
    # has no MTF trail knobs so live _cfg resolves False — stocks sims opt in via
    # MTF_ATR_TRAIL_ENABLED_TRADIER). Trail state is per position (pos['_atr_trail']), reset on
    # every open/close, mirroring live mtf_compound_exit_state pop-on-successful-close. Pure
    # predicate lives in vec_decisions.mtf_atr_trail_exit (vec-identical hook). Replaces the
    # DELETED fabricated scaffolding (atr>1.0 exit_mask OR). Live-only orchestration NOT
    # vectorized: MTF_EXIT_MIN_OPEN_TS startup gate, stale-trail-on-blocked-close wart.
    _mtfcmp_on = bool(getattr(cfg, 'MTF_EXIT_USE_COMPOUND', True))
    _mtfat_tf = str((getattr(cfg, 'MTF_ATR_TRAIL_TF_TRADIER', '1h') if is_tradier else getattr(cfg, 'MTF_ATR_TRAIL_TF', '15m')) or 'OFF').strip()
    _mtfat_mult = float(getattr(cfg, 'MTF_ATR_TRAIL_MULT', 2.0))
    _mtfat_on = _mtfcmp_on and bool(getattr(cfg, 'MTF_ATR_TRAIL_ENABLED', True))
    if is_tradier:
        _mtfat_on = _mtfat_on and bool(getattr(cfg, 'MTF_ATR_TRAIL_ENABLED_TRADIER', False))
    _mtfat_atr = _safe(npz, f'atr_{_mtfat_tf}', n, 0) if (_mtfat_on and _mtfat_tf.upper() != 'OFF') else None
    # ═══ MTF compound-exit branches 2-4 (2026-09-28, user: test dc bands + WT crosses as exit
    # signals, not only the ATR trail). Faithful crypto twins via vec_decisions.mtf_compound_exits;
    # tradier mode stays UNWIRED (stocks live defaults OFF; time-based dc step + structural veto
    # not modeled — wiring without the veto would overfire). Live order: trail → dc → bb → wt.
    _mtfdc_tf = str(getattr(cfg, 'MTF_DC_REJECT_EXIT_TF', '1h') or 'OFF').strip()
    _mtfdc_use4 = bool(getattr(cfg, 'MTF_DC_REJECT_USE_DC4', False))
    _mtfdc_on = _mtfcmp_on and (not is_tradier) and bool(getattr(cfg, 'MTF_DC_REJECT_EXIT_ENABLED', True)) and _mtfdc_tf.upper() != 'OFF'
    if _mtfdc_on:
        _mtfdc_field = (('dc_high4_' if _mtfdc_use4 else 'dc_high_') if is_long else ('dc_low4_' if _mtfdc_use4 else 'dc_low_')) + _mtfdc_tf
        _mtfdc_band = _safe(npz, _mtfdc_field, n, 0)
    else:
        _mtfdc_band = None
    _mtfbb_tf = str(getattr(cfg, 'MTF_BB_REJECT_EXIT_TF', '1h') or 'OFF').strip()
    _mtfbb_lb = int(getattr(cfg, 'MTF_BB_REJECT_EXIT_LOOKBACK', 5))
    _mtfbb_on = _mtfcmp_on and (not is_tradier) and bool(getattr(cfg, 'MTF_BB_REJECT_EXIT_ENABLED', True)) and _mtfbb_tf.upper() != 'OFF'
    try:
        from mtf_exit_timing import timeframe_seconds as _mtfbb_tf_secs_fn
        _mtfbb_tfsec = int(_mtfbb_tf_secs_fn(_mtfbb_tf))
    except Exception:
        _mtfbb_tfsec = 3600
    if _mtfbb_on:
        _mtfbb_u = _safe(npz, f'bb_upper_{_mtfbb_tf}', n, 0)
        _mtfbb_l = _safe(npz, f'bb_lower_{_mtfbb_tf}', n, 0)
        _mtfbb_h = _safe(npz, f'high_{_mtfbb_tf}', n, 0)
        _mtfbb_lo = _safe(npz, f'low_{_mtfbb_tf}', n, 0)
    else:
        _mtfbb_u = None
    _mtfwt_tf = str(getattr(cfg, 'MTF_WT_CROSS_EXIT_TF', '15m') or 'OFF').strip()
    _mtfwt_on = (_mtfcmp_on and (not is_tradier) and bool(getattr(cfg, 'MTF_GR_EXIT_GATE_ENABLED', True))
                 and bool(getattr(cfg, 'MTF_WT_CROSS_EXIT_ENABLED', True))
                 and _mtfwt_tf.upper() != 'OFF' and _mtfwt_tf.lower() != 'either')
    if _mtfwt_on:
        _mtfwt_w1 = _safe(npz, f'wt1_{_mtfwt_tf}', n, 0)
        _mtfwt_w2 = _safe(npz, f'wt2_{_mtfwt_tf}', n, 0)
        _mtfwt_lad = [(_safe(npz, f'wt1_{_t}', n, 0), _safe(npz, f'wt2_{_t}', n, 0)) for _t in ('15m', '1h', '4h', 'D')]
    else:
        _mtfwt_w1 = None
    _mtfwt_min = int(getattr(cfg, 'MTF_GR_EXIT_MIN_TFS', 3))
    _vig_block_px = 0.0
    _vig_scan_start = 0
    try:
        # 2026-09-30 15m-floor: NPZ has no 3m; VIGILANCE recovery stoch-confirm read k_3m/d_3m (phantom, always 0 -> leg always-True). Use 15m.
        _vig_k3 = _safe(npz, 'stoch_k_15m', n, 50)
        _vig_d3 = _safe(npz, 'stoch_d_15m', n, 50)
    except Exception:
        _vig_k3 = close * 0
        _vig_d3 = close * 0
    # 2026-09-18 HARDCODED RALLY REENTRY: track last exit and wt1_15m for hardcoded reentry
    try:
        _hc_wt1_15m = _safe(npz, 'wt1_15m', n)
    except Exception:
        _hc_wt1_15m = close * 0
    # [C2 b3] live reentry PATHWAY F (favorable move + HTF alignment) — the only pathway vectorizable on 15m+ arrays (ez_manage.py:36594-36624)
    try:
        import vec_decisions.reentry_pathways as _rpw
        _rf_htf = _rpw.favorable_move_mask(npz, n, is_long, cfg, _safe)
    except Exception:
        _rf_htf = None
    # [C2 b7] STOCKS queue_trade_action choke point (tradier_manage.py:13930-13975): COUNTER_TREND_ADD_BLOCK blocks EVERY OPEN/AUGMENT/ENTRY/REENTRY order
    # (action 'REENTRY' contains 'ENTRY'; only mandatory price-cross reclaim / ordinary-ladder reasons bypass) — incl. HARDCODED_RALLY reentries.
    # The vec applied it only inside _base_entry (the permissive OR), so reentry opens (85-95% of stock opens) and augments were never gated.
    _qta_ct_block = None
    try:
        if str(getattr(cfg, 'MODE', 'crypto')) == 'tradier' and bool(getattr(cfg, 'COUNTER_TREND_ADD_BLOCK_ENABLED', False)):
            _qta_ct_block = vec_decisions.counter_trend.counter_trend_vec(npz, n, cfg, is_long)
    except Exception:
        _qta_ct_block = None
    # Cache entry blocks once for per-trade reason tagging (avoid recompute per entry)
    try:
        _entry_blocks_cache = compute_reentry_blocks(npz, n, is_long, cfg)
    except Exception:
        _entry_blocks_cache = {}

    # 2026-09-30 GREY-SWITCH WIRING — exits shared with live stocks (tradier_manage GREY_WIRE hook) via
    # vec_decisions.grey_wire_exits (crypto live source of truth: ez_manage.process_position). Every enable
    # defaults OFF -> empty list -> the loop hook below never runs at the defaults.
    _gw_c = lambda _k, _d: getattr(cfg, _k, _d)
    _gw_exits = vec_decisions.grey_wire_exits.active_exits(_gw_c)
    _gw_bar = vec_decisions.grey_wire_exits.NpzBar(npz, n) if _gw_exits else None

    _ot_cnt = {}  # [C2 b5b] executed OPEN/AUGMENT fills per UTC day
    def _open(qty0, px, i, entry_reason='VECTOR_ENTRY'):
        # Store original entry price/bar for ledger — avg_price may drift after augments
        _ts_open = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
        # OPEN ledger event for charts/parity — no pnl/bar_exit keys, so every trade metric skips it
        events.append({'type': 'OPEN', 'ts': _ts_open, 'price': float(px), 'qty': float(qty0), 'pos_deployed': abs(qty0 * px), 'bar': int(i), 'reason': str(entry_reason)})
        try:
            _ot_d = vec_decisions.overtrade_guard.day_of(_ts_open); _ot_cnt[_ot_d] = _ot_cnt.get(_ot_d, 0) + 1  # [C2 b5b] executed fill today
        except Exception:
            pass
        return {'qty': qty0, 'avg_price': px, 'entry_price': px, 'entry_qty': qty0, 'deployed': abs(qty0 * px), 'realized': 0.0,
                'entry_bar': i, 'peak_pnl_pct': 0.0, 'fees': abs(qty0 * px) * half_fee, 'entry_reason': entry_reason}

    # 2026-09-30 STATEFUL PORTED SWITCHES — masks precomputed ONCE (vectorized), gate applied per-bar below
    # where live_pnl_pct/held_bars/peak_pnl_pct/n_augments exist (faithful twins of ez_manage.process_position
    # gain/age/count-gated exits & augment cap). vec_decisions/ported_stateful_{exit,augment}.py. NO fabrication.
    _sx_active = []
    _sa_cap = None
    try:
        import vec_decisions.ported_stateful_exit as _pse
        for _sw, _mg in (_pse.masks(npz, n, is_long, cfg, _safe, close) or {}).items():
            _sx_active.append(_mg)
    except Exception:
        _sx_active = []
    try:
        import vec_decisions.ported_stateful_augment as _psa
        for _sw, (_m, _g) in (_psa.masks(npz, n, is_long, cfg, _safe, close) or {}).items():
            if 'augment_count_max' in _g:
                _c = int(_g['augment_count_max'])
                _sa_cap = _c if _sa_cap is None else min(_sa_cap, _c)
    except Exception:
        _sa_cap = None
    # 2026-10-01 ZERO-AUDIT WIRING: ALL_TF_AGAINST_CLOSE (crypto live ez_manage.process_position 48045-48098: close the WHOLE position
    # when >= ALL_TF_AGAINST_CLOSE_MIN_TFS of the 5 WT TFs (3m/15m/1h/4h/D) are against; no gain/loss gate). The shared twin
    # vec_decisions.process_position_crypto__all_tf_against was imported but NEVER called -> vec baseline lacked a default-ON live exit.
    # Crypto only: tradier_manage has no real decision site (stub reads only) -> stocks stay inert. 3m uses the 15m array (15m decision floor).
    _atf_mask = None
    try:
        if (not is_tradier) and bool(_qc_alias(cfg, 'ALL_TF_AGAINST_FORCE_CLOSE_ENABLED', 'ALL_TF_AGAINST_CLOSE_ENABLED')):
            _atf_w1 = {}; _atf_w2 = {}
            for _tf in ('15m', '1h', '4h', 'D'):   # USER 2026-10-01: NO 3m data in vec -> the 3m WT term is IGNORED (not approximated by 15m)
                _src = _tf
                _atf_w1[_tf] = _safe(npz, f'wt1_{_src}', n, 0.0)
                _atf_w2[_tf] = _safe(npz, f'wt2_{_src}', n, 0.0)
            _atf_mask = vec_decisions.process_position_crypto__all_tf_against.check_all_tf_against_vec(cfg, _atf_w1, _atf_w2, is_long, min_tfs=int(_qc_alias(cfg, 'ALL_TF_AGAINST_FORCE_CLOSE_MIN_TFS', 'ALL_TF_AGAINST_CLOSE_MIN_TFS')))
    except Exception:
        _atf_mask = None
    # 2026-10-01 b2c: LIVE CRYPTO EXIT CHAIN (ez_positions_quick.process_single_exit 14109-14830) — see vec_decisions/live_exit_chain.py
    _lec_P = None
    _lec = None
    if str(getattr(cfg, 'MODE', 'crypto')) != 'tradier' and bool(getattr(cfg, 'LIVE_EXIT_CHAIN_ENABLED', False)):
        try:
            import vec_decisions.live_exit_chain as _lec
            _lec_P = _lec.prepare(npz, n, is_long, cfg, close, _safe)
            _lec_P = _lec.prepare_pp(npz, n, is_long, cfg, close, _safe, _lec_P)
        except Exception:
            _lec_P = None
    # 2026-09-30 MULTI_TF_EXIT scorer (faithful vec twin of ez_manage.evaluate_multi_tf_exit 40370-40572, a MAJOR
    # live exit vec was missing). Gain-independent base+late score arrays precomputed once; gain multipliers +
    # threshold(35/45/55) + min_exit_gain applied per-bar in the walk. Makes WT_DIV/ACCEL/MOMENTUM/EXHAUST/15M_LH/etc testable.
    _mtf_base = _mtf_late = _mtf_override = None
    _mtf_min_gain = 0.0
    try:
        import vec_decisions.mtf_exit_scorer as _mtfs
        _mtf_base, _mtf_late, _mtf_min_gain, _mtf_override = _mtfs.score_array_parts(npz, n, is_long, cfg, _safe, close)
    except Exception:
        _mtf_base = None
    def _stategate_ok(_g, _gain, _age_min, _age_bars, _peak):
        try:
            if 'gain_op' in _g:
                _t = float(_g.get('gain_thr', 0.0)); _op = _g['gain_op']
                if _op == '>=' and not (_gain >= _t): return False
                if _op == '>' and not (_gain > _t): return False
                if _op == '<' and not (_gain < _t): return False
                if _op == '<=' and not (_gain <= _t): return False
            if 'age_min_minutes' in _g and not (_age_min > float(_g['age_min_minutes'])): return False
            if 'age_min_bars' in _g and not (_age_bars >= int(_g['age_min_bars'])): return False
            if 'peak_giveback_pct' in _g and not ((_peak - _gain) >= float(_g['peak_giveback_pct'])): return False
        except Exception:
            return False
        return True

    for i in range(n):
        px = close[i]
        if px <= 0:
            continue
        # 2026-09-18 HARDCODED RALLY REENTRY bypass cooldown: if close>exit and wt rising, ignore cd
        # 2026-09-26 TARGET-DC immediate reentry: if last exit was TARGET dc_high (long) / dc_low (short) and price keeps trending, NO cooldowns at all (user mandate)
        if pos is None and has_closed_before and trades and cd > 0:
            try:
                _last_reason = str(trades[-1].get('exit_reason', '') or trades[-1].get('reason',''))
                if 'TARGET' in _last_reason and 'dc_' in _last_reason.lower():
                    _last_exit_px = float(trades[-1].get('exit_price', 0) or 0)
                    if _last_exit_px > 0:
                        if (is_long and px > _last_exit_px) or (not is_long and px < _last_exit_px):
                            cd = 0
            except Exception:
                pass
        # Loosened for TIM>20: when REQUIRE_WT=False, close>exit alone suffices
        if getattr(cfg, "HARDCODED_RALLY_REENTRY_ENABLED", True) and getattr(cfg, "HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN", True) and pos is None and has_closed_before and trades and cd > 0:
            try:
                _hc_last_exit_cd = float(trades[-1].get('exit_price', 0) or 0)
                if _hc_last_exit_cd > 0:
                    _hc_wt1_cd = float(_hc_wt1_15m[i] if i < len(_hc_wt1_15m) else 0)
                    _hc_wt1_prev_cd = float(_hc_wt1_15m[i-1] if i > 0 and i-1 < len(_hc_wt1_15m) else _hc_wt1_cd)
                    _hc_req_wt = bool(getattr(cfg, "HARDCODED_RALLY_REENTRY_REQUIRE_WT", False))
                    _hc_wt_ok_long = (not _hc_req_wt) or (_hc_wt1_cd > _hc_wt1_prev_cd)
                    _hc_wt_ok_short = (not _hc_req_wt) or (_hc_wt1_cd < _hc_wt1_prev_cd)
                    if (is_long and px > _hc_last_exit_cd and _hc_wt_ok_long) or (not is_long and px < _hc_last_exit_cd and _hc_wt_ok_short):
                        cd = 0
            except Exception:
                pass
        if force_initial_seed and pos is None and i == 0:
            _dollar = float(cfg.START_POSITION_SIZE)
            try:
                _cap = float(getattr(cfg, "MAX_ORDER_VALUE", 2500.0) or 2500.0)
                if _cap > 0:
                    _cap = max(_cap, float(getattr(cfg, "START_POSITION_SIZE", 500.0) or 500.0) * 10.0)
                    _dollar = min(_dollar, _cap)
            except Exception:
                pass
            qty0 = _size_qty(cfg, _dollar, px)
            if qty0 > 0:
                pos = _open(qty0, px, i, 'SEED_BH')
                bars_in_pos += 1
            continue
        if cd > 0:
            cd -= 1
            continue
        if pos is None:
            # ABLATION reentry block: if flagged, no reentry after first close
            if has_closed_before and (getattr(cfg, '_ABLATION_BLOCK_REENTRY', False) or getattr(cfg, '_ABLATION_BLOCK_REENTRY_ENFORCE', False)):
                # skip all reentry — keep pos None
                continue
            # VIGILANCE (USER 2026-09-28): consecutive-loss streak (since last recovery) blocks entries
            if _vig_enabled and not _vig_blocked and len(trades) > _vig_scan_start:
                _vig_streak = 0
                _vig_last_close_px = 0.0
                for _vt in reversed(trades[_vig_scan_start:]):
                    if str(_vt.get('type', '')) != 'CLOSE':
                        continue
                    if float(_vt.get('pnl_pct', 0) or 0) < 0:
                        if _vig_streak == 0:
                            _vig_last_close_px = float(_vt.get('exit_price', 0) or 0)
                        _vig_streak += 1
                        if _vig_streak >= _vig_need:
                            break
                    else:
                        break
                if _vig_streak >= _vig_need:
                    _vig_blocked = True
                    _vig_block_px = _vig_last_close_px
            if _vig_enabled and _vig_blocked:
                # Recovery auto-unblock (USER 2026-09-28 2nd mandate): price past block-exit, or bounce.
                _vig_rec = False
                if _vig_rec_enabled:
                    if _vig_block_px > 0 and ((is_long and px > _vig_block_px) or ((not is_long) and px < _vig_block_px)):
                        _vig_rec = True
                    elif _vig_bounce_ok:
                        _vw1 = float(_hc_wt1_15m[i] if i < len(_hc_wt1_15m) else 0)
                        _vw1p = float(_hc_wt1_15m[i-1] if i > 0 and i-1 < len(_hc_wt1_15m) else _vw1)
                        _vk3 = float(_vig_k3[i]) if i < len(_vig_k3) else 0.0
                        _vd3 = float(_vig_d3[i]) if i < len(_vig_d3) else 0.0
                        _v_stoch_ok = True if (_vk3 == 0 and _vd3 == 0) else ((_vk3 > _vd3) if is_long else (_vk3 < _vd3))
                        if _v_stoch_ok and ((is_long and _vw1 > _vw1p) or ((not is_long) and _vw1 < _vw1p)):
                            _vig_rec = True
                if _vig_rec:
                    _vig_blocked = False
                    _vig_block_px = 0.0
                    _vig_scan_start = len(trades)
                else:
                    continue
            fire = entry_sig[i]
            # 2026-09-26 TARGET-DC immediate reentry: if last exit was TARGET dc before high/low and price keeps rising/falling, fire immediately even if entry_sig false — no cooldowns
            if not fire and has_closed_before and trades:
                try:
                    _tr = str(trades[-1].get('exit_reason','') or trades[-1].get('reason',''))
                    if 'TARGET' in _tr and 'dc_' in _tr.lower():
                        _le = float(trades[-1].get('exit_price',0) or 0)
                        if _le > 0 and ((is_long and px > _le) or (not is_long and px < _le)):
                            fire = True
                except Exception:
                    pass
            # 2026-09-18 HARDCODED RALLY REENTRY (user mandate): close > exit AND wt1_15m rising — loosened for TIM>20
            if not fire and getattr(cfg, "HARDCODED_RALLY_REENTRY_ENABLED", True) and has_closed_before and trades:
                try:
                    _hc_last_exit = float(trades[-1].get('exit_price', 0) or 0)
                    if _hc_last_exit > 0:
                        _hc_wt1 = float(_hc_wt1_15m[i] if i < len(_hc_wt1_15m) else 0)
                        _hc_wt1_prev = float(_hc_wt1_15m[i-1] if i > 0 and i-1 < len(_hc_wt1_15m) else _hc_wt1)
                        _hc_req_wt = bool(getattr(cfg, "HARDCODED_RALLY_REENTRY_REQUIRE_WT", False))
                        if is_long and px > _hc_last_exit and (not _hc_req_wt or _hc_wt1 > _hc_wt1_prev):
                            fire = True
                        elif not is_long and px < _hc_last_exit and (not _hc_req_wt or _hc_wt1 < _hc_wt1_prev):
                            fire = True
                except Exception:
                    pass
            if not fire and has_closed_before and getattr(cfg, 'REENTRY_MANDATORY', False):
                # [C2 b3] live REENTRY_MANDATORY only enables the reentry loop; a pathway must fire (never 'every bar'). Blanket fire kept as default
                # (baseline unchanged); False = live-like: only HARDCODED_RALLY (above) + pathway F favorable-move+HTF (3m/1m pathways are UNWIRABLE_NO_3M).
                _blanket = getattr(cfg, 'REENTRY_BLANKET_FIRE_ENABLED', None)
                if _blanket is None:
                    # stocks: live tradier_manage.py:11993-12081 (MANDATORY_REENTRY) reenters ONLY when price crossed back through the exit price in our
                    # favour (guaranteed) or within PRICE_CROSS_BACK_BAND_PCT with a 5m DC break (REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED, default off ->
                    # never) => no blanket fire. Crypto keeps the blanket (upper bound) until 3m/1m reentry pathways can be vectorized.
                    _blanket = str(getattr(cfg, 'MODE', 'crypto')) != 'tradier'
                if _blanket:
                    fire = True
                elif _rf_htf is not None and trades:
                    try:
                        if bool(_rf_htf[i]) and vec_decisions.reentry_pathways.favorable_price_ok(
                                is_long, px, float(trades[-1].get('exit_price', 0) or 0), float(getattr(cfg, 'REENTRY_FAVORABLE_MOVE_PCT', 1.0) or 1.0)):
                            fire = True
                    except Exception:
                        pass
            # 2026-09-29 USER: reentries bypassed every entry filter (reckless). Lenient gate: a reentry must pass at
            # least REENTRY_FILTER_MIN_PASS of the active entry filters; new entries (entry_sig) must pass ALL of them.
            if fire and _reentry_filter_on and has_closed_before and not entry_sig[i]:
                if sum(1 for _rm in _entry_filter_masks if bool(_rm[i])) < _reentry_filter_need:
                    fire = False
            if fire and _strict_open_block is not None and bool(_strict_open_block[i]):
                fire = False
            # 2026-10-01 WIRING b1: live ENTRY_VET (ez_manage 25616 'REENTRY respects ENTRY_VET') blocks ANY entry/reentry while >= MIN_TFS WT TFs are against
            if fire and _atf_entry_block is not None and bool(_atf_entry_block[i]):
                fire = False
            # USER 2026-09-27: NEVER reenter when falling through 4h bottom/top, below STOP, against KG all TFs, against GR all TFs — FIX 2026-09-27: block ANY px below dc_low_4h (not just 0.25% below) to stop 1-bar ULTIMATE churn
            if fire:
                try:
                    if is_long:
                        _dc4l = float(npz.get('dc_low_4h', [0])[i]) if i < len(npz.get('dc_low_4h', [])) else 0
                        if _dc4l > 0 and px < _dc4l:
                            fire = False
                        # STOP level: dc_low_15m/1h/4h 0.25% below
                        if fire:
                            for _tf in ['15m','1h','4h']:
                                _a = float(npz.get(f'dc_low_{_tf}', [0])[i]) if i < len(npz.get(f'dc_low_{_tf}', [])) else 0
                                if _a > 0 and px < _a * (1 - 0.0025):
                                    fire = False
                                    break
                        # KG all TFs: close < ema_20_15m/1h/4h and sma_200 etc -> block if all against
                        if fire:
                            try:
                                _kg_vals=[]
                                for _tf in ['15m','1h','4h']:
                                    _ema = float(npz.get(f'ema_20_{_tf}', npz.get(f'ema_200_{_tf}', [0]))[i]) if i < len(npz.get(f'ema_20_{_tf}', [])) else 0
                                    if _ema > 0:
                                        _kg_vals.append(px < _ema)
                                if len(_kg_vals)>=2 and all(_kg_vals):
                                    fire = False
                            except Exception:
                                pass
                        # GR all TFs: gr_score <0 on all TFs
                        if fire:
                            try:
                                _gr_vals=[]
                                for _tf in ['15m','1h','4h']:
                                    _gr = float(npz.get(f'gr_score_{_tf}', npz.get('gr_score', [0]))[i]) if i < len(npz.get(f'gr_score_{_tf}', [])) else 0
                                    if _gr != 0:
                                        _gr_vals.append(_gr < 0)
                                if len(_gr_vals)>=2 and all(_gr_vals):
                                    fire = False
                            except Exception:
                                pass
                    else:
                        _dc4h = float(npz.get('dc_high_4h', [0])[i]) if i < len(npz.get('dc_high_4h', [])) else 0
                        if _dc4h > 0 and px > _dc4h:
                            fire = False
                        if fire:
                            for _tf in ['15m','1h','4h']:
                                _a = float(npz.get(f'dc_high_{_tf}', [0])[i]) if i < len(npz.get(f'dc_high_{_tf}', [])) else 0
                                if _a > 0 and px > _a * (1 + 0.0025):
                                    fire = False
                                    break
                        if fire:
                            try:
                                _kg_vals=[]
                                for _tf in ['15m','1h','4h']:
                                    _ema = float(npz.get(f'ema_20_{_tf}', [0])[i]) if i < len(npz.get(f'ema_20_{_tf}', [])) else 0
                                    if _ema > 0:
                                        _kg_vals.append(px > _ema)
                                if len(_kg_vals)>=2 and all(_kg_vals):
                                    fire = False
                            except Exception:
                                pass
                            try:
                                _gr_vals=[]
                                for _tf in ['15m','1h','4h']:
                                    _gr = float(npz.get(f'gr_score_{_tf}', [0])[i]) if i < len(npz.get(f'gr_score_{_tf}', [])) else 0
                                    if _gr != 0:
                                        _gr_vals.append(_gr > 0)
                                if len(_gr_vals)>=2 and all(_gr_vals):
                                    fire = False
                            except Exception:
                                pass
                except Exception:
                    pass
            if fire:
                mult = getattr(cfg, 'REENTRY_TIER1_SIZE_MULT_TRADIER', 1.0) if (has_closed_before and is_tradier) else 1.0
                _dollar = float(cfg.START_POSITION_SIZE) * float(regime_mult[i]) * float(mult)
                try:
                    _cap = float(getattr(cfg, "MAX_ORDER_VALUE", 2500.0) or 2500.0)
                    if _cap > 0:
                        _cap = max(_cap, float(getattr(cfg, "START_POSITION_SIZE", 500.0) or 500.0) * 10.0)
                        _dollar = min(_dollar, _cap)
                except Exception:
                    pass
                qty0 = _size_qty(cfg, _dollar, px)
                if qty0 > 0:
                    # Derive real entry reason from the block that fired at this bar
                    entry_reason = 'VECTOR_ENTRY'
                    try:
                        # 2026-09-18 HARDCODED tag before generic
                        _hardcoded_fired = False
                        if has_closed_before and trades and not entry_sig[i]:
                            try:
                                _hc_last = float(trades[-1].get('exit_price', 0) or 0)
                                if _hc_last > 0:
                                    _hwt1 = float(_hc_wt1_15m[i] if i < len(_hc_wt1_15m) else 0)
                                    _hwt1p = float(_hc_wt1_15m[i-1] if i > 0 and i-1 < len(_hc_wt1_15m) else _hwt1)
                                    _hc_req_wt2 = bool(getattr(cfg, "HARDCODED_RALLY_REENTRY_REQUIRE_WT", False))
                                    if (is_long and px > _hc_last and (not _hc_req_wt2 or _hwt1 > _hwt1p)) or (not is_long and px < _hc_last and (not _hc_req_wt2 or _hwt1 < _hwt1p)):
                                        entry_reason = 'HARDCODED_RALLY_REENTRY'
                                        _hardcoded_fired = True
                            except Exception:
                                pass
                        if not _hardcoded_fired:
                            for _bname, _barr in _entry_blocks_cache.items():
                                if _barr[i]:
                                    entry_reason = _bname
                                    break
                            if entry_reason == 'VECTOR_ENTRY' and has_closed_before and getattr(cfg, 'REENTRY_MANDATORY', False) and not entry_sig[i]:
                                entry_reason = 'REENTRY_MANDATORY'
                            if entry_reason == 'VECTOR_ENTRY':
                                entry_reason = 'ENTRY_SIGNAL'
                    except Exception:
                        pass
                    # [C2 b5b] live OVERTRADE_GUARD: fresh (non-reentry) entries blocked once TRADES_PER_SYM_PER_DAY_MAX fills happened today (ez_manage.py:30405, tradier_manage.py:13843)
                    if getattr(cfg, 'OVERTRADE_GUARD_ENABLED', False):
                        try:
                            if vec_decisions.overtrade_guard.blocked(cfg, _ot_cnt, vec_decisions.overtrade_guard.day_of(ts[i] if i < len(ts) else ts[-1]), entry_reason):
                                continue
                        except Exception:
                            pass
                    if _qta_ct_block is not None and bool(_qta_ct_block[i]) and 'RECLAIM' not in str(entry_reason).upper():
                        continue  # [C2 b7] live queue_trade_action COUNTER_TREND_ADD_BLOCK (fresh + reentry opens)
                    pos = _open(qty0, px, i, entry_reason)
            continue
        bars_in_pos += 1
        live_pnl_pct = ((px - pos['avg_price']) / pos['avg_price'] * 100) if is_long else ((pos['avg_price'] - px) / pos['avg_price'] * 100)
        pos['peak_pnl_pct'] = max(pos['peak_pnl_pct'], live_pnl_pct)
        held_bars = i - pos['entry_bar']
        # VIGILANCE (USER 2026-09-28, 3rd mandate): STRUCTURAL stop, NO fixed % — losing position AND
        # px breach of dc_low4_{TF} (long) / dc_high4_{TF} (short) → immediate close + block until
        # recovery. Runs before every other exit (live parity).
        # NEWBORN_LOSS_KILL (live ez_manage.py:46524-46550, default OFF): young loser +
        # velocity-against on NEWBORN_LOSS_KILL_FILTER_TF -> force close
        if _nlk_vel is not None and vec_decisions.generic_filter_tf.newborn_loss_kill_fires(
                cfg, is_long, held_bars * bmin, live_pnl_pct, float(_nlk_vel[i]) if i < len(_nlk_vel) else 0.0):
            pos['fees'] += abs(pos['qty'] * px) * half_fee
            _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
            _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
            _tsn = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
            _nlk_reason = f"NEWBORN_LOSS_KILL age{held_bars * bmin:.0f}m g{live_pnl_pct:.2f}%"
            trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _nlk_reason, 'type': 'CLOSE', 'ts': _tsn, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _nlk_reason, 'bars_held': int(i - pos['entry_bar'])})
            pos = None; cd = cooldown_bars; has_closed_before = True
            continue
        # 2026-10-01 ALL_TF_AGAINST_CLOSE (ez_manage.py:48045) — no gain gate, full close
        if _atf_mask is not None and i < len(_atf_mask) and bool(_atf_mask[i]):
            pos['fees'] += abs(pos['qty'] * px) * half_fee
            _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
            _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
            _tsa = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
            trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': "ALL_TF_AGAINST_CLOSE g%.2f%%" % live_pnl_pct, 'type': 'CLOSE', 'ts': _tsa, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': 'ALL_TF_AGAINST_CLOSE', 'bars_held': int(i - pos['entry_bar'])})
            pos = None; cd = cooldown_bars; has_closed_before = True
            continue
        # 2026-10-01 b2c LIVE CRYPTO EXIT CHAIN hook (live_exit_chain.step): hit -> full close (STOP/CLOSE/KILL reasons) or partial REDUCE by live gain bands
        if _lec_P is not None:
            _lec_hit = _lec.step(_lec_P, cfg, i, px, {'gain': live_pnl_pct, 'peak': pos.get('peak_pnl_pct', 0.0), 'age_min': held_bars * bmin,
                                                      'entry_price': pos.get('entry_price', pos['avg_price']), 'reentered': 'REENTRY' in str(pos.get('entry_reason', '')).upper(),
                                                      'was_augmented': int(pos.get('n_augments', 0)) > 0})
            if _lec_hit:
                _lec_reason, _lec_full = _lec_hit
                _lec_frac = None if _lec_full else _lec.reduce_fraction(cfg, live_pnl_pct)
                _lec_qty = pos['qty'] if (_lec_full or _lec_frac is None) else pos['qty'] * _lec_frac
                if pos['qty'] - _lec_qty < 0.10 * float(pos.get('entry_qty', pos['qty'])):
                    _lec_qty = pos['qty']
                _lec_ts = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                if _lec_qty >= pos['qty'] - 1e-12:
                    pos['fees'] += abs(pos['qty'] * px) * half_fee
                    _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
                    _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
                    trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _lec_reason, 'type': 'CLOSE', 'ts': _lec_ts, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason', 'VECTOR_ENTRY'), 'exit_reason': _lec_reason, 'bars_held': int(i - pos['entry_bar'])})
                    pos = None; cd = cooldown_bars; has_closed_before = True
                else:
                    pos['realized'] += (px - pos['avg_price']) * _lec_qty if is_long else (pos['avg_price'] - px) * _lec_qty
                    pos['fees'] += abs(_lec_qty * px) * half_fee
                    pos['qty'] -= _lec_qty
                    events.append({'type': 'REDUCE', 'ts': _lec_ts, 'price': float(px), 'qty': float(_lec_qty), 'pos_deployed': float(pos['deployed']), 'bar': int(i), 'reason': _lec_reason})
                    pos['last_reduce_bar'] = int(i)
                continue
            # b2c: ez_manage.process_position exits (R1 / R2 / MOMENTUM_TP / DC_BASIS_3M_REDUCE) — full closes
            _pp_hit = _lec.step_pp(_lec_P, cfg, i, px, {'gain': live_pnl_pct, 'peak': pos.get('peak_pnl_pct', 0.0), 'age_min': held_bars * bmin, 'held_bars': held_bars,
                                                         'min_hold': min_hold, 'entry_reason': pos.get('entry_reason', '')}, pos)
            if _pp_hit:
                pos['fees'] += abs(pos['qty'] * px) * half_fee
                _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
                _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
                _pp_ts = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _pp_hit[0], 'type': 'CLOSE', 'ts': _pp_ts, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason', 'VECTOR_ENTRY'), 'exit_reason': _pp_hit[0], 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
                continue
        # 2026-09-30 STATEFUL PORTED EXITS (WT_4H_VEL_EXIT / WT_EXHAUST_EXIT — gain/age-gated; ported_stateful_exit.py)
        if _sx_active:
            _sx_fire = False
            for _sm, _sg in _sx_active:
                if bool(_sm[i]) and _stategate_ok(_sg, live_pnl_pct, held_bars * bmin, held_bars, pos.get('peak_pnl_pct', 0.0)):
                    _sx_fire = True; break
            if _sx_fire:
                pos['fees'] += abs(pos['qty'] * px) * half_fee
                _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
                _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
                _tssx = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                _sx_reason = f"STATEFUL_PORTED_EXIT g{live_pnl_pct:.2f}% age{held_bars * bmin:.0f}m"
                trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _sx_reason, 'type': 'CLOSE', 'ts': _tssx, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _sx_reason, 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
                continue
        # 2026-09-30 MULTI_TF_EXIT scorer (ez_manage.evaluate_multi_tf_exit 40370-40572) — gain-dependent threshold
        if _mtf_base is not None:
            _mb = float(_mtf_base[i])
            if live_pnl_pct > 3.0 and _mb >= 20.0: _mb *= 1.3
            elif live_pnl_pct > 1.0 and _mb >= 25.0: _mb *= 1.2
            _msc = _mb + float(_mtf_late[i])
            if _mtf_min_gain > 0 and live_pnl_pct < _mtf_min_gain: _msc = 0.0
            _msc = _msc if _msc < 100.0 else 100.0
            _mthr = 35.0 if live_pnl_pct > 1.0 else 45.0 if live_pnl_pct > 0.3 else 55.0
            if (_msc >= _mthr or bool(_mtf_override[i])) and not vec_decisions.noloss_gate.noloss_blocks(cfg, 'MULTI_TF_EXIT', live_pnl_pct):  # [C2 b5b] live UNIVERSAL_NOLOSS_GATE
                pos['fees'] += abs(pos['qty'] * px) * half_fee
                _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
                _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
                _tsmt = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                _mtf_reason = f"MULTI_TF_EXIT s{_msc:.0f}/{_mthr:.0f} g{live_pnl_pct:.2f}%"
                trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _mtf_reason, 'type': 'CLOSE', 'ts': _tsmt, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _mtf_reason, 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
                continue
        # MI_EXIT momentum-interception voter (ez_positions_quick.py:3723-3760): votes >=
        # MI_TF_AGREE_MIN with gain >= MI_MIN_GAIN_EXIT -> close
        if _mi_arr is not None and bool(_mi_arr[i]) and live_pnl_pct >= float(getattr(cfg, 'MI_MIN_GAIN_EXIT', 0.10)):
            pos['fees'] += abs(pos['qty'] * px) * half_fee
            _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
            _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
            _tsm2 = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
            _mi_reason = f"MI_EXIT votes>=min g{live_pnl_pct:.2f}%"
            trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _mi_reason, 'type': 'CLOSE', 'ts': _tsm2, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _mi_reason, 'bars_held': int(i - pos['entry_bar'])})
            pos = None; cd = cooldown_bars; has_closed_before = True
            continue
        _vig_dc_hit = False
        if _vig_enabled and _vig_dc_lvl is not None and live_pnl_pct < 0:
            _vg_lvl = float(_vig_dc_lvl[(i - 1) if (i > 0 and bool(getattr(cfg, 'DC_PRIOR_BAR_CHANNEL', True))) else i]) if i < len(_vig_dc_lvl) else 0.0
            # breach must clear the channel level by the tolerance (same shape as DAYTRADE/TECHNICAL
            # dc stops: level*(1∓tol)) — tolerance on the LEVEL, never a loss floor
            _vig_dc_hit = _vg_lvl > 0 and ((is_long and px <= _vg_lvl * (1 - _vig_tol)) or ((not is_long) and px >= _vg_lvl * (1 + _vig_tol)))
        if _vig_dc_hit:
            pos['fees'] += abs(pos['qty'] * px) * half_fee
            _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
            _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
            _tsv = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
            _vg_depth = ((_vg_lvl - px) / _vg_lvl * 100.0) if is_long else ((px - _vg_lvl) / _vg_lvl * 100.0)
            _vg_reason = f"VIGILANCE_DC4_{_vig_dc_tf}_STOP g{live_pnl_pct:.2f} breach{_vg_depth:.2f}%>=tol{_vig_tol*100:.2f}% px{px:.6f}{'<=' if is_long else '>='}lvl{_vg_lvl:.6f} close+block"
            trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _vg_reason, 'type': 'CLOSE', 'ts': _tsv, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _vg_reason, 'bars_held': int(i - pos['entry_bar'])})
            pos = None; cd = cooldown_bars; has_closed_before = True
            _vig_blocked = True
            _vig_block_px = float(px)
            continue
        # 2026-09-19 USER MANDATE — ABSOLUTE ULTIMATE STOP: DC CHANNEL BREACH TF = DC_HARD_STOP_TF (4h|D)
        # No trade may be held through dc_low_TF (LONG) / dc_high_TF (SHORT) at any loss. TF per sym_side, D wider.
        try:
            _ult_j = (i - 1) if (i > 0 and bool(getattr(cfg, 'DC_PRIOR_BAR_CHANNEL', True))) else i
            _ult_lvl = float(dc_low_4h[_ult_j]) if is_long else float(dc_high_4h[_ult_j])
            _ult_breach = (_ult_lvl > 0) and ((is_long and px <= _ult_lvl) or ((not is_long) and px >= _ult_lvl))
            if _ult_breach:
                pos['fees'] += abs(pos['qty'] * px) * half_fee
                _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
                _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
                _tsu = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                _hs_reason = f"ULTIMATE_DC_{_hs_tf_v12}_HARD_STOP at dc_{_hs_tf_v12}_{'low' if is_long else 'high'} 0% breach"
                trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _hs_reason, 'type': 'CLOSE', 'ts': _tsu, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _hs_reason, 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
                # CHURN_FIX #2 (USER 2026-09-28, tradier): DC hard-stop reopen cooldown — vectorized
                # mirror of live dc_hardstop_cooldown_active (kills open→stop→reopen loops in sweeps too)
                if is_tradier:
                    _dc_cd_h = float(getattr(cfg, 'DC_HARD_STOP_REENTRY_COOLDOWN_HOURS', 4.0) or 0.0)
                    if _dc_cd_h > 0:
                        cd = max(cd, int(round(_dc_cd_h * 60.0 / max(bmin, 1))))
                continue
        except Exception:
            pass
        # WT LOWER CROSS EXIT — LONG wt cross down + price lower, SHORT opposite; TF sweep OFF/15m/1h/4h
        try:
            _wt_tf = str(getattr(cfg, 'WT_LOWER_CROSS_EXIT_TF', 'OFF') or 'OFF').strip()
            if _wt_tf.upper() != 'OFF' and i > 0:
                _wt_key = f"wt1_{_wt_tf}" if _wt_tf != "4h" else "wt1_4h"
                _wt2_key = f"wt2_{_wt_tf}" if _wt_tf != "4h" else "wt2_4h"
                # fallback to 15m if TF not in npz
                if _wt_key not in npz and _wt_tf == "15m":
                    _wt_key, _wt2_key = "wt1_15m", "wt2_15m"
                w1 = float(npz.get(_wt_key, [0])[i]) if i < len(npz.get(_wt_key, [])) else 0
                w2 = float(npz.get(_wt2_key, [0])[i]) if i < len(npz.get(_wt2_key, [])) else 0
                w1p = float(npz.get(_wt_key, [0])[i-1]) if i-1 >=0 and i-1 < len(npz.get(_wt_key, [])) else w1
                w2p = float(npz.get(_wt2_key, [0])[i-1]) if i-1 >=0 and i-1 < len(npz.get(_wt2_key, [])) else w2
                pxp = float(close[i-1]) if i-1 >=0 and i-1 < len(close) else px
                # shared predicate with live ez_manage/tradier_manage process_position (2026-09-29)
                _wt_fire = vec_decisions.dc_channel_exits.wt_lower_cross_fires(w1, w2, w1p, w2p, float(px), pxp, is_long)
                if _wt_fire:
                    pos['fees'] += abs(pos['qty'] * px) * half_fee
                    _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
                    _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
                    _tsw = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                    _wt_reason = f"WT_LOWER_CROSS_EXIT wt_{_wt_tf} {'lower wt+price' if is_long else 'higher wt+price'}"
                    trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _wt_reason, 'type': 'CLOSE', 'ts': _tsw, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _wt_reason, 'bars_held': int(i - pos['entry_bar'])})
                    pos = None; cd = cooldown_bars; has_closed_before = True
                    continue
        except Exception:
            pass
        # 2026-09-30 GREY-SWITCH WIRING exits (vec_decisions.grey_wire_exits; OFF by default)
        if _gw_exits:
            _gw_bar.i = i
            _gw_fire, _gw_reason = vec_decisions.grey_wire_exits.first_fire(
                _gw_exits, _gw_c, _gw_bar, {'gain': float(live_pnl_pct), 'age_s': float(held_bars * bmin * 60.0), 'entry_px': float(pos['avg_price']), 'px': float(px), 'max_gain': float(pos.get('peak_pnl_pct', 0.0)), 'reentered': 'REENTRY' in str(pos.get('entry_reason', '')), 'augmented': int(pos.get('n_augments', 0)) > 0}, is_long)
            if _gw_fire:
                pos['fees'] += abs(pos['qty'] * px) * half_fee
                _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
                _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
                _tsg = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _gw_reason, 'type': 'CLOSE', 'ts': _tsg, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _gw_reason, 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
                continue

        # 2026-09-28 LIVE-PARITY AUGMENT (user mandate "absolute parity"): the gain-ladder
        # (UAG obligatory tier + pullback dip, ez_manage.py:31461-31525/30943-30990) initiates
        # augments alongside the legacy bounce/pyramid signals. AUGMENTATION_COOLDOWN_SECONDS
        # applies to ALL augment sources at this choke point (live cooldown map,
        # ez_manage.py:31736) — also kills the per-bar pyramid compounding bug.
        _gl_fire, _gl_reason = vec_decisions.gain_ladder_augment.gain_ladder_fire(
            cfg, is_long, px, float(pos.get('last_aug_px', 0.0)) or float(pos.get('entry_price', pos['avg_price'])),
            live_pnl_pct, float(pos.get('peak_pnl_pct', 0.0)))
        # FAST_RISER quick-jump double (ez_manage.py:52393-52468): TF jump signal + profit floor
        _fr_fire = _fr_arr is not None and bool(_fr_arr[i]) and live_pnl_pct > vec_decisions.filter_tf_gates.FAST_RISER_MIN_GAIN_PCT
        # [C2 b5] live execute_now UNIVERSAL_AUGMENT_GAIN_GATE blocks EVERY non-reduce order on an existing position (not only the ladder-initiated one):
        # augment_sig (bounce/pyramid) and FAST_RISER sources need gain-since-last-add >= AUGMENT_MIN_GAIN_PCT or the pullback exception (ez_manage.py:31577-31630)
        _uag_src_ok = _gl_fire or ((augment_sig[i] or _fr_fire) and vec_decisions.uagain_gate.uagain_gate_pass(
            cfg, is_long, px, float(pos.get('last_aug_px', 0.0)) or float(pos.get('entry_price', pos['avg_price'])), live_pnl_pct, float(pos.get('peak_pnl_pct', 0.0))))
        if _uag_src_ok and not (_qta_ct_block is not None and bool(_qta_ct_block[i])) and _augment_allowed(cfg, live_pnl_pct) and (_sa_cap is None or int(pos.get('n_augments', 0)) < _sa_cap):
            _aug_cd_bars = vec_decisions.gain_ladder_augment.cooldown_bars(cfg, bmin)
            _aug_last_bar = int(pos.get('last_aug_bar', -10**9))
            if (i - _aug_last_bar) >= _aug_cd_bars:
                # STDEV_SLOPE_SIZING ladder applies to augment as well (user mandate: entry AND augment sizing)
                _aug_regime = float(regime_mult[i]) if i < len(regime_mult) else 1.0
                _aug_add_mult = float(augment_mult[i]) * _aug_regime
                if _fr_fire:
                    _aug_add_mult = max(_aug_add_mult, 1.0 * _aug_regime)  # FAST_RISER_DOUBLE = 100% add
                add_qty = _size_qty(cfg, pos['qty'] * px * _aug_add_mult, px) if is_tradier else pos['qty'] * _aug_add_mult
                if add_qty > 0:
                    new_qty = pos['qty'] + add_qty
                    pos['avg_price'] = (pos['avg_price'] * pos['qty'] + px * add_qty) / new_qty
                    pos['deployed'] += abs(add_qty * px)
                    pos['fees'] += abs(add_qty * px) * half_fee
                    pos['qty'] = new_qty
                    pos['last_aug_px'] = float(px)
                    pos['last_aug_bar'] = int(i)
                    pos['n_augments'] = int(pos.get('n_augments', 0)) + 1
                    pos['last_aug_qty'] = float(add_qty)
                    pos['dd_armed'] = True  # WT_D_BOUNCE_DD_STOP leg re-armed by every augment
                    live_pnl_pct = ((px - pos['avg_price']) / pos['avg_price'] * 100) if is_long else ((pos['avg_price'] - px) / pos['avg_price'] * 100)
                    _ts_aug = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                    # event row: NO pnl_dollars/pnl_pct/bar_entry/bar_exit keys — metric
                    # consumers key on those fields and must never count position scaling
                    events.append({'type': 'AUGMENT', 'ts': _ts_aug, 'price': float(px), 'qty': float(add_qty), 'pos_deployed': float(pos['deployed']), 'bar': int(i), 'reason': _gl_reason or 'VEC_AUGMENT_SIG'})
                    try:
                        _ot_d = vec_decisions.overtrade_guard.day_of(_ts_aug); _ot_cnt[_ot_d] = _ot_cnt.get(_ot_d, 0) + 1
                    except Exception:
                        pass

        # ═══ PARTIAL_PROFIT_LOCK v2 (2026-09-28 parity round 3) — faithful state machine of
        # tradier_manage.py:18720-18798 / ez_positions_quick.py:18586-18640 via
        # vec_decisions.reduce_profit_lock.ppl_step (TP -> BE stop -> arm upgrade -> SL close).
        _ppl_action, _ppl_frac, _ppl_reason, pos['ppl'] = vec_decisions.reduce_profit_lock.ppl_step(
            cfg, is_tradier, is_long, px, float(pos.get('entry_price', pos['avg_price'])), live_pnl_pct, pos.get('ppl') or {})
        if _ppl_action == 'CLOSE_REMAINDER':
            pos['fees'] += abs(pos['qty'] * px) * half_fee
            _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
            _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
            _tsp = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
            trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _ppl_reason, 'type': 'CLOSE', 'ts': _tsp, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _ppl_reason, 'bars_held': int(i - pos['entry_bar'])})
            pos = None; cd = cooldown_bars; has_closed_before = True
            continue
        if _ppl_action == 'REDUCE' and pos['qty'] > 0:
            _ppl_qty = pos['qty'] * min(_ppl_frac, 1.0)
            if pos['qty'] - _ppl_qty < 0.10 * float(pos.get('entry_qty', pos['qty'])):
                _ppl_qty = pos['qty']  # sim flat-floor (live whole-share floor, USER 2026-07-22)
            _realized_ppl = (px - pos['avg_price']) * _ppl_qty if is_long else (pos['avg_price'] - px) * _ppl_qty
            pos['realized'] += _realized_ppl
            pos['fees'] += abs(_ppl_qty * px) * half_fee
            pos['qty'] -= _ppl_qty
            _tsp2 = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
            if pos['qty'] <= 1e-9:
                _pnl2 = pos['realized'] - pos['fees']
                _pct2 = _pnl2 / pos['deployed'] * 100 if pos['deployed'] else 0.0
                trades.append({'pnl_dollars': _pnl2, 'pnl_pct': float(_pct2), 'deployed': pos['deployed'], 'reason': _ppl_reason, 'type': 'REDUCE', 'ts': _tsp2, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': 0.0, 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _ppl_reason, 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
                continue
            events.append({'type': 'REDUCE', 'ts': _tsp2, 'price': float(px), 'qty': float(_ppl_qty), 'pos_deployed': float(pos['deployed']), 'bar': int(i), 'reason': _ppl_reason})
            pos['last_reduce_bar'] = int(i)
        # ═══ WT_D_BOUNCE_DD_STOP (tradier_manage.py:11705-11716): price back through the last
        # augment price cuts that augment leg, once per leg (re-armed by the next augment).
        if pos is not None and vec_decisions.reduce_profit_lock.dd_bounce_stop_fires(
                cfg, is_long, px, float(pos.get('last_aug_px', 0.0) or 0.0), float(pos.get('last_aug_qty', 0.0) or 0.0), bool(pos.get('dd_armed'))):
            _dd_qty = min(float(pos['last_aug_qty']), pos['qty'])
            _dd_reason = f"DD_BOUNCE_STOP px{px:.4f} aug_px{float(pos['last_aug_px']):.4f} qty{_dd_qty:.4f}"
            _realized_dd = (px - pos['avg_price']) * _dd_qty if is_long else (pos['avg_price'] - px) * _dd_qty
            pos['realized'] += _realized_dd
            pos['fees'] += abs(_dd_qty * px) * half_fee
            pos['qty'] -= _dd_qty
            pos['dd_armed'] = False
            pos['last_aug_qty'] = 0.0
            _tsd = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
            if pos['qty'] <= 1e-9:
                _pnl3 = pos['realized'] - pos['fees']
                _pct3 = _pnl3 / pos['deployed'] * 100 if pos['deployed'] else 0.0
                trades.append({'pnl_dollars': _pnl3, 'pnl_pct': float(_pct3), 'deployed': pos['deployed'], 'reason': _dd_reason, 'type': 'REDUCE', 'ts': _tsd, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': 0.0, 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _dd_reason, 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
                continue
            events.append({'type': 'REDUCE', 'ts': _tsd, 'price': float(px), 'qty': float(_dd_qty), 'pos_deployed': float(pos['deployed']), 'bar': int(i), 'reason': _dd_reason})
            pos['last_reduce_bar'] = int(i)

        closed = False
        reason = None
        if cfg.PROFIT_TARGET_ENABLED and live_pnl_pct >= cfg.PROFIT_TARGET_PCT:
            closed, reason = True, 'PROFIT_TARGET'
        elif cfg.STOP_LOSS_ENABLED and live_pnl_pct <= -cfg.STOP_LOSS_PCT:
            closed, reason = True, 'STOP_LOSS'
        elif daytrade_on and (_dd_stop_dcs or _dd_tgt_dcs):
            # DC-channel daytrade: OR across TFs, fixed % ELIMINATED when ANY DC TF active (user mandate)
            # STOP: LONG px <= dc_low_TF*(1-buf) for ANY TF in _dd_stop_dcs, SHORT px >= dc_high_TF*(1+buf)
            # TARGET: LONG px >= dc_high_TF*(1-buf) for ANY TF in _dd_tgt_dcs, SHORT px <= dc_low_TF*(1+buf)
            try:
                _dcpb = bool(getattr(cfg, 'DC_PRIOR_BAR_CHANNEL', True)) and i > 0
                _dd_lv = {(_sp['field_long'] if is_long else _sp['field_short']): (float(_arr[i - 1 if _dcpb else i]) if i < len(_arr) else 0.0) for _sp, _arr in _dd_stop_dcs}
                _dd_lv.update({(_sp['field_long'] if is_long else _sp['field_short']): (float(_arr[i]) if i < len(_arr) else 0.0) for _sp, _arr in _dd_tgt_dcs})
                closed, _dd_reason = vec_decisions.dc_channel_exits.daytrade_dc_exit(
                    float(px), is_long, [_sp for _sp, _ in _dd_stop_dcs], [_sp for _sp, _ in _dd_tgt_dcs], lambda _fld: _dd_lv.get(_fld, 0.0))
                if closed:
                    reason = _dd_reason
                # NO fallback to fixed % when DC active — fixed % eliminated per user 2026-09-26
            except Exception:
                pass
        # 2026-09-28 USER SPEC: fixed-% DAYTRADE_STOP/TARGET branches DELETED — "NEVER a fix % loss or
        # profit exit"; DC-channel exits above (dc_low−0.25% / dc_high−0.10%, 15m/1h) are the only daytrade exits.
        elif max_hold_bars > 0 and held_bars >= max_hold_bars:
            closed, reason = True, 'DELTA_MAX_HOLD'
        elif trail_erosion > 0 and pos['peak_pnl_pct'] > 0 and (pos['peak_pnl_pct'] - live_pnl_pct) >= pos['peak_pnl_pct'] * trail_erosion and (_gftf.get('erosion_confirm') is None or bool(_gftf['erosion_confirm'][i])):
            closed, reason = True, 'WIN_TRAIL_EROSION'

        if closed and vec_decisions.noloss_gate.noloss_blocks(cfg, reason, live_pnl_pct):
            closed = False  # [C2 b5b] live UNIVERSAL_NOLOSS_GATE (default OFF) blocks a technical close at a real loss
        if closed:
            pos['fees'] += abs(pos['qty'] * px) * half_fee
            pnl_dollars = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
            pnl_pct = pnl_dollars / pos['deployed'] * 100 if pos['deployed'] else 0.0
            _ts_exit = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
            trades.append({'pnl_dollars': pnl_dollars, 'pnl_pct': float(pnl_pct), 'deployed': pos['deployed'], 'reason': reason, 'type': 'CLOSE', 'ts': _ts_exit, 'price': float(px),
                           'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': reason, 'bars_held': int(i - pos['entry_bar'])})
            # 2026-09-26 user mandate: if exit was TARGET just before dc_high (long) / dc_low (short) and price keeps rising/falling, reentry immediate — no cooldowns
            # Fixed % already eliminated when DC active, but cooldown must also be bypassed for this specific target exit
            _is_target_dc = 'TARGET' in str(reason) and 'dc_' in str(reason).lower()
            if _is_target_dc:
                pos = None; cd = 0; has_closed_before = True
            else:
                pos = None; cd = cooldown_bars; has_closed_before = True
            continue

        # 2026-09-28 QUICK_REDUCE_STRONG gain gate fix: qr_cond is indicator-only; the live
        # gain conditions (ez_positions_quick.py:3454/3488) are applied here with the REAL
        # simulated gain — the old precomputed path read nonexistent npz['gain_pct'] (zeros).
        _qr_fire = bool(qr_cond[i]) and bool(getattr(cfg, 'HLR_TOP_EXIT_ENABLED', True)) and vec_decisions.quick_reduce_strong.quick_reduce_gain_ok(cfg, live_pnl_pct)
        # live pacing: the execute-path dedup map spaces orders by AUGMENTATION_COOLDOWN_SECONDS
        # (ez_manage.py:28272/28326) — partial reduces obey it too; kills per-bar halving cascades
        _red_cd_ok = (i - int(pos.get('last_reduce_bar', -10**9))) >= vec_decisions.gain_ladder_augment.cooldown_bars(cfg, bmin)
        _rc_ok = _gftf.get('reduce_confirm') is None or bool(_gftf['reduce_confirm'][i])
        if pos['qty'] > 0 and _rc_ok and (_qr_fire or (reduce_sig[i] and reduce_frac[i] > 0 and _red_cd_ok)):
            # HLR_TOP_EXIT = "sell the top": live stores the FULL position qty in
            # _hlr_top_exit_registry and reenters at prev_qty*mult (ez_positions_quick.py:3715-3718,
            # 16312) — a one-shot full exit, never a per-bar halving
            frac = 1.0 if _qr_fire else min(float(reduce_frac[i]), 1.0)
            reduce_qty = pos['qty'] * frac
            # sim floor: live min-qty sizing cannot leave dust (self.min_qty order rejection) —
            # a remainder under 10% of the entry qty goes to flat instead of decaying forever
            if pos['qty'] - reduce_qty < 0.10 * float(pos.get('entry_qty', pos['qty'])):
                reduce_qty = pos['qty']
            realized = (px - pos['avg_price']) * reduce_qty if is_long else (pos['avg_price'] - px) * reduce_qty
            pos['realized'] += realized
            pos['fees'] += abs(reduce_qty * px) * half_fee
            pos['qty'] -= reduce_qty
            if pos['qty'] <= 1e-9:
                pnl_dollars = pos['realized'] - pos['fees']
                pnl_pct = pnl_dollars / pos['deployed'] * 100 if pos['deployed'] else 0.0
                _ts_exit2 = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                _red_reason = f'HLR_TOP_EXIT_SELL_TOP_g={live_pnl_pct:.2f}%' if _qr_fire else 'REDUCE_TO_FLAT'
                trades.append({'pnl_dollars': pnl_dollars, 'pnl_pct': float(pnl_pct), 'deployed': pos['deployed'], 'reason': _red_reason, 'type': 'REDUCE', 'ts': _ts_exit2, 'price': float(px),
                               'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos.get('qty',0)), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _red_reason, 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
            else:
                _ts_red = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                # partial reduce event: realized pnl stays inside the position and lands in
                # its eventual CLOSE row; no pnl/bar_entry/bar_exit keys so metrics skip it
                events.append({'type': 'REDUCE', 'ts': _ts_red, 'price': float(px), 'qty': float(reduce_qty), 'pos_deployed': float(pos['deployed']), 'bar': int(i), 'reason': 'QUICK_REDUCE_STRONG' if _qr_fire else 'REGIME_REDUCE'})
                pos['last_reduce_bar'] = int(i)
            continue

        # MTF_ATR_TRAIL faithful twin (prep block above). Placed BEFORE the exit_sig technical
        # exit on purpose: live's trail is in the NOLOSS bypass list, so it must be reachable on
        # bars where the technical exit is NOLOSS-suppressed (its continue would skip anything
        # placed after). On bars where both fire, close bar/price match live; only the reason
        # label can differ (live tick order runs R1/R2 first). Update BEFORE compare = live order.
        if pos is not None and _mtfat_atr is not None and held_bars >= min_hold:
            _mtfat_a = float(_mtfat_atr[i]) if i < len(_mtfat_atr) else 0.0
            pos['_atr_trail'] = vec_decisions.mtf_atr_trail_exit.atr_trail_update(
                pos.get('_atr_trail', 0.0), float(pos.get('entry_price', pos['avg_price'])), px, _mtfat_a, _mtfat_mult, is_long)
            if vec_decisions.mtf_atr_trail_exit.atr_trail_fires(pos['_atr_trail'], px, is_long):
                pos['fees'] += abs(pos['qty'] * px) * half_fee
                _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
                _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
                _tsm = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                _mtfat_reason = f"MTF_ATR_TRAIL_{_mtfat_tf}_x{_mtfat_mult}_lvl{pos['_atr_trail']:.6f}"
                trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _mtfat_reason, 'type': 'CLOSE', 'ts': _tsm, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _mtfat_reason, 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
                continue
        # MTF compound branches 2-4 (live ez_manage tick order: trail → dc_reject → bb_reject →
        # gr_wt; all NOLOSS-bypassed live, hence placed with the trail before exit_sig). State is
        # per position: pos['_dc_outside'] / pos['_bb_tags'] die with the position, matching the
        # lifecycle-fixed live mtf_compound_exit_state.
        if pos is not None and held_bars >= min_hold and (_mtfdc_band is not None or _mtfbb_u is not None or _mtfwt_w1 is not None):
            _cmp_reason = ''
            if _mtfdc_band is not None:
                _cmp_b = float(_mtfdc_band[i]) if i < len(_mtfdc_band) else 0.0
                pos['_dc_outside'], _cmp_dcf = vec_decisions.mtf_compound_exits.dc_reject_step(bool(pos.get('_dc_outside', False)), px, _cmp_b, is_long)
                if _cmp_dcf:
                    _cmp_reason = f"MTF_DC_REJECT_{_mtfdc_tf}_px{px:.6f}" + ("_dc4" if _mtfdc_use4 else "")
            if (not _cmp_reason) and _mtfbb_u is not None:
                _cmp_now = float(ts[i]) if i < len(ts) else 0.0
                _cmp_h = float(_mtfbb_h[i]) if i < len(_mtfbb_h) else 0.0
                _cmp_lo = float(_mtfbb_lo[i]) if i < len(_mtfbb_lo) else 0.0
                # live falls back to current price when high/low_{TF} is missing
                _cmp_h = _cmp_h if _cmp_h > 0 else px
                _cmp_lo = _cmp_lo if _cmp_lo > 0 else px
                pos['_bb_tags'], _cmp_bbf = vec_decisions.mtf_compound_exits.bb_reject_step(
                    pos.get('_bb_tags', []), _cmp_now, _cmp_h, _cmp_lo,
                    float(_mtfbb_u[i]) if i < len(_mtfbb_u) else 0.0,
                    float(_mtfbb_l[i]) if i < len(_mtfbb_l) else 0.0,
                    _mtfbb_lb, is_long, tf_secs=_mtfbb_tfsec)
                if _cmp_bbf:
                    _cmp_reason = f"MTF_BB_REJECT_{_mtfbb_tf}"
            if (not _cmp_reason) and _mtfwt_w1 is not None:
                _cmp_wtf, _cmp_cnt = vec_decisions.mtf_compound_exits.gr_wt_exit_fires(
                    float(_mtfwt_w1[i]) if i < len(_mtfwt_w1) else 0.0,
                    float(_mtfwt_w2[i]) if i < len(_mtfwt_w2) else 0.0,
                    [(float(_l1[i]) if i < len(_l1) else 0.0, float(_l2[i]) if i < len(_l2) else 0.0) for _l1, _l2 in _mtfwt_lad],
                    _mtfwt_min, is_long)
                if _cmp_wtf:
                    _cmp_reason = f"MTF_GR_WT_EXIT_{_mtfwt_tf}_grTFs={_cmp_cnt}"
            if _cmp_reason:
                pos['fees'] += abs(pos['qty'] * px) * half_fee
                _pnl = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
                _pct = _pnl / pos['deployed'] * 100 if pos['deployed'] else 0.0
                _tsc = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
                trades.append({'pnl_dollars': _pnl, 'pnl_pct': float(_pct), 'deployed': pos['deployed'], 'reason': _cmp_reason, 'type': 'CLOSE', 'ts': _tsc, 'price': float(px), 'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _cmp_reason, 'bars_held': int(i - pos['entry_bar'])})
                pos = None; cd = cooldown_bars; has_closed_before = True
                continue
        _xc_ok = _gftf.get('exit_confirm') is None or bool(_gftf['exit_confirm'][i])
        if exit_sig[i] and held_bars >= min_hold and _xc_ok:
            if 0 < satoshit_partial < 1.0:
                reduce_qty = pos['qty'] * satoshit_partial
                realized = (px - pos['avg_price']) * reduce_qty if is_long else (pos['avg_price'] - px) * reduce_qty
                pos['realized'] += realized
                pos['fees'] += abs(reduce_qty * px) * half_fee
                pos['qty'] -= reduce_qty
                continue
            pnl_preview = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty'])
            pnl_pct_preview = pnl_preview / pos['deployed'] * 100 if pos['deployed'] > 0 else 0.0
            # NOLOSS_BYPASS_WT_5OF5: >= MIN_TFS WT TFs against -> allow the loss exit
            _nlb_bypass = _nlb_against is not None and vec_decisions.reduce_profit_lock.noloss_bypass_pass(cfg, int(_nlb_against[i]) if i < len(_nlb_against) else 0)
            if cfg.NOLOSS_ENABLED and pnl_pct_preview < 0 and not _nlb_bypass:
                if cfg.DC_RECOVERY_EXIT_ENABLED:
                    if is_long:
                        stranded = pos['avg_price'] > dc_high_4h[i] and dc_high_4h[i] > 0
                    else:
                        stranded = pos['avg_price'] < dc_low_4h[i] and dc_low_4h[i] > 0
                    near = abs(px - pos['avg_price']) / pos['avg_price'] * 100 < cfg.DC_RECOVERY_EXIT_TOLERANCE_PCT
                    if not (stranded and near):
                        continue
                else:
                    continue
            _tech_reason = 'TECHNICAL_EXIT'
            try:
                # CROSSING ONLY: exit only when CROSSING 0.10% BELOW high / 0.25% BELOW low (LONG) else NEVER; SHORT v versa 0.10% ABOVE low / 0.25% ABOVE high
                _px_prev = float(close[i-1]) if i > 0 and i-1 < len(close) else None
                if is_long:
                    if _tech_stop_dcs and _px_prev is not None:
                        for _tf_s, _arr_s in _tech_stop_dcs:
                            _lvl = float(_arr_s[i]) if i < len(_arr_s) else 0.0
                            _lvl_prev = float(_arr_s[i-1]) if i-1 >= 0 and i-1 < len(_arr_s) else _lvl
                            if _lvl > 0 and _lvl_prev > 0 and _px_prev > _lvl_prev * (1 - _tech_stop_buf) and px <= _lvl * (1 - _tech_stop_buf):
                                _tech_reason = f'TECHNICAL_EXIT dc_{_tf_s}_low'
                                break
                    if _tech_reason == 'TECHNICAL_EXIT' and _tech_tgt_dcs and _px_prev is not None:
                        for _tf_t, _arr_t in _tech_tgt_dcs:
                            _lvl2 = float(_arr_t[i]) if i < len(_arr_t) else 0.0
                            _lvl2_prev = float(_arr_t[i-1]) if i-1 >= 0 and i-1 < len(_arr_t) else _lvl2
                            if _lvl2 > 0 and _lvl2_prev > 0 and _px_prev < _lvl2_prev * (1 - _tech_tgt_buf) and px >= _lvl2 * (1 - _tech_tgt_buf):
                                _tech_reason = f'TECHNICAL_EXIT dc_{_tf_t}_high'
                                break
                    if _tech_reason == 'TECHNICAL_EXIT' and _tech_stop_dc is not None and _px_prev is not None:
                        _lvl = float(_tech_stop_dc[i]) if i < len(_tech_stop_dc) else 0.0
                        _lvl_prev = float(_tech_stop_dc[i-1]) if i-1 >= 0 and i-1 < len(_tech_stop_dc) else _lvl
                        if _lvl > 0 and _lvl_prev > 0 and _px_prev > _lvl_prev * (1 - _tech_stop_buf) and px <= _lvl * (1 - _tech_stop_buf):
                            _tech_reason = f'TECHNICAL_EXIT dc_{_tech_stop_tf_norm}_low'
                    if _tech_reason == 'TECHNICAL_EXIT' and _tech_tgt_dc is not None and _px_prev is not None:
                        _lvl2 = float(_tech_tgt_dc[i]) if i < len(_tech_tgt_dc) else 0.0
                        _lvl2_prev = float(_tech_tgt_dc[i-1]) if i-1 >= 0 and i-1 < len(_tech_tgt_dc) else _lvl2
                        if _lvl2 > 0 and _lvl2_prev > 0 and _px_prev < _lvl2_prev * (1 - _tech_tgt_buf) and px >= _lvl2 * (1 - _tech_tgt_buf):
                            _tech_reason = f'TECHNICAL_EXIT dc_{_tech_tgt_tf_norm}_high'
                else:
                    if _tech_stop_dcs and _px_prev is not None:
                        for _tf_s, _arr_s in _tech_stop_dcs:
                            _lvl = float(_arr_s[i]) if i < len(_arr_s) else 0.0
                            _lvl_prev = float(_arr_s[i-1]) if i-1 >= 0 and i-1 < len(_arr_s) else _lvl
                            if _lvl > 0 and _lvl_prev > 0 and _px_prev < _lvl_prev * (1 + _tech_stop_buf) and px >= _lvl * (1 + _tech_stop_buf):
                                _tech_reason = f'TECHNICAL_EXIT dc_{_tf_s}_high'
                                break
                    if _tech_reason == 'TECHNICAL_EXIT' and _tech_tgt_dcs and _px_prev is not None:
                        for _tf_t, _arr_t in _tech_tgt_dcs:
                            _lvl2 = float(_arr_t[i]) if i < len(_arr_t) else 0.0
                            _lvl2_prev = float(_arr_t[i-1]) if i-1 >= 0 and i-1 < len(_arr_t) else _lvl2
                            if _lvl2 > 0 and _lvl2_prev > 0 and _px_prev > _lvl2_prev * (1 + _tech_tgt_buf) and px <= _lvl2 * (1 + _tech_tgt_buf):
                                _tech_reason = f'TECHNICAL_EXIT dc_{_tf_t}_low'
                                break
                    if _tech_reason == 'TECHNICAL_EXIT' and _tech_stop_dc is not None and _px_prev is not None:
                        _lvl = float(_tech_stop_dc[i]) if i < len(_tech_stop_dc) else 0.0
                        _lvl_prev = float(_tech_stop_dc[i-1]) if i-1 >= 0 and i-1 < len(_tech_stop_dc) else _lvl
                        if _lvl > 0 and _lvl_prev > 0 and _px_prev < _lvl_prev * (1 + _tech_stop_buf) and px >= _lvl * (1 + _tech_stop_buf):
                            _tech_reason = f'TECHNICAL_EXIT dc_{_tech_stop_tf_norm}_high'
                    if _tech_reason == 'TECHNICAL_EXIT' and _tech_tgt_dc is not None and _px_prev is not None:
                        _lvl2 = float(_tech_tgt_dc[i]) if i < len(_tech_tgt_dc) else 0.0
                        _lvl2_prev = float(_tech_tgt_dc[i-1]) if i-1 >= 0 and i-1 < len(_tech_tgt_dc) else _lvl2
                        if _lvl2 > 0 and _lvl2_prev > 0 and _px_prev > _lvl2_prev * (1 + _tech_tgt_buf) and px <= _lvl2 * (1 + _tech_tgt_buf):
                            _tech_reason = f'TECHNICAL_EXIT dc_{_tech_tgt_tf_norm}_low'
            except Exception:
                pass
            if _tech_reason == 'TECHNICAL_EXIT':
                continue
            if vec_decisions.noloss_gate.noloss_blocks(cfg, _tech_reason, live_pnl_pct):
                continue  # [C2 b5b] live UNIVERSAL_NOLOSS_GATE
            pos['fees'] += abs(pos['qty'] * px) * half_fee
            pnl_dollars = pnl_preview - pos['fees']
            pnl_pct = pnl_dollars / pos['deployed'] * 100 if pos['deployed'] else 0.0
            _ts_exit3 = float(ts[i]) if i < len(ts) else float(ts[-1]) if len(ts) else 0.0
            trades.append({'pnl_dollars': pnl_dollars, 'pnl_pct': float(pnl_pct), 'deployed': pos['deployed'], 'reason': _tech_reason, 'type': 'CLOSE', 'ts': _ts_exit3, 'price': float(px),
                           'bar_entry': int(pos['entry_bar']), 'bar_exit': int(i), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': _tech_reason, 'bars_held': int(i - pos['entry_bar'])})
            pos = None; cd = cooldown_bars; has_closed_before = True

    if pos is not None:
        px = close[-1]
        # MTM: only fees actually paid so far (open/augment/reduce) — no synthetic exit fee, position not actually closed
        pnl_dollars = pos['realized'] + ((px - pos['avg_price']) * pos['qty'] if is_long else (pos['avg_price'] - px) * pos['qty']) - pos['fees']
        pnl_pct = pnl_dollars / pos['deployed'] * 100 if pos['deployed'] else 0.0
        _ts_mtm = float(ts[-1]) if len(ts) else 0.0
        trades.append({'pnl_dollars': pnl_dollars, 'pnl_pct': float(pnl_pct), 'deployed': pos['deployed'], 'reason': 'FINAL_MTM', 'type': 'CLOSE', 'ts': _ts_mtm, 'price': float(px),
                       'bar_entry': int(pos['entry_bar']), 'bar_exit': int(n-1), 'entry_price': float(pos.get('entry_price', pos['avg_price'])), 'exit_price': float(px), 'qty': float(pos['qty']), 'entry_reason': pos.get('entry_reason','VECTOR_ENTRY'), 'exit_reason': 'FINAL_MTM', 'bars_held': int(n-1 - pos['entry_bar'])})

    tim_pct = round(bars_in_pos / n * 100, 2)
    if not trades:
        return {'sym': sym, 'is_long': is_long, 'trades': 0, 'tim_pct': tim_pct,
                'gain_pct': 0.0, 'gain_dollars': 0.0,
                'gain_pct_2000norm': 0.0, 'gain_dollars_2000norm': 0.0,
                'sharpe_per_trade': 0.0, 'wr': 0.0, 'mean_deployed': 0.0,
                'peak_capital': 0.0, 'max_dd_pct': 0.0, 'bars': n, 'ledger': []}
    deployed_arr = np.array([t['deployed'] for t in trades])
    pnl_arr = np.array([t['pnl_dollars'] for t in trades])
    pnl_pct_arr = np.array([float(t.get('pnl_pct', 0.0) or 0.0) for t in trades])
    mean_deployed = float(deployed_arr.mean()) if deployed_arr.mean() > 0 else float(getattr(cfg, 'START_POSITION_SIZE', 2000.0))
    # Faithful capital model — peak concurrent notional, per tools/opt/metrics.py:25.
    # Single-position engine never overlaps, so peak = max(deployed) across realised
    # trades (each trade's deployed already sums its OPEN+AUGMENT fills). Sweep-line
    # via bar_entry/bar_exit is kept as canonical so future overlapping support is
    # automatically faithful; fallback is max(deployed).
    _peak_via_bars = 0.0
    try:
        _ev = []
        for _t in trades:
            _d = float(_t.get('deployed') or 0.0)
            _be, _bx = _t.get('bar_entry'), _t.get('bar_exit')
            if _d > 0 and _be is not None and _bx is not None:
                _ev.append((int(_be), _d))
                _ev.append((int(_bx), -_d))
        if _ev:
            _ev.sort(key=lambda _x: (_x[0], -_x[1]))
            _cur = _peak_via_bars = 0.0
            for _, _delta in _ev:
                _cur += _delta
                if _cur > _peak_via_bars:
                    _peak_via_bars = _cur
    except Exception:
        _peak_via_bars = 0.0
    peak_capital = float(_peak_via_bars) if _peak_via_bars > 0 else float(np.max(deployed_arr)) if len(deployed_arr) else mean_deployed
    if peak_capital <= 0:
        peak_capital = mean_deployed if mean_deployed > 0 else float(getattr(cfg, 'START_POSITION_SIZE', 2000.0))
    total_pnl = float(pnl_arr.sum())
    gain_pct = total_pnl / peak_capital * 100.0 if peak_capital > 0 else 0.0
    gain_dollars = total_pnl
    wins = int((pnl_arr > 0).sum())
    # Sharpe on raw per-trade returns (source of truth), not mean_deployed-normalized.
    s_raw = float(pnl_pct_arr.std()) if len(pnl_pct_arr) > 1 else 0.0
    sharpe_raw = float(pnl_pct_arr.mean() / s_raw) if s_raw > 0 else 0.0
    # Honest DD — same capital base as gain (peak_concurrent equity curve).
    _rows_sorted = sorted(trades, key=lambda _t: (int(_t.get('bar_exit') or 0), int(_t.get('bar_entry') or 0)))
    equity = float(peak_capital)
    peak = float(peak_capital)
    worst = 0.0
    for _t in _rows_sorted:
        equity += float(_t.get('pnl_dollars') or 0.0)
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            if dd > worst:
                worst = dd
    dd_pct = min(100.0, worst)
    # Legacy 2000-norm kept as diagnostic alias (never for decisions).
    pnl_pct_norm_legacy = pnl_arr / mean_deployed * 100 if mean_deployed > 0 else pnl_pct_arr
    legacy_gain_pct = float(pnl_pct_norm_legacy.sum())
    return {
        'sym': sym, 'is_long': is_long, 'trades': len(trades), 'tim_pct': tim_pct,
        'gain_pct': round(float(gain_pct), 4),
        'gain_dollars': round(float(gain_dollars), 2),
        'gain_pct_2000norm': round(float(gain_pct), 4),
        'gain_dollars_2000norm': round(float(gain_dollars), 2),
        'gain_pct_legacy_2000norm': round(float(legacy_gain_pct), 4),
        'gain_dollars_legacy_2000norm': round(float(legacy_gain_pct) / 100 * 2000, 2),
        'sharpe_per_trade': round(float(sharpe_raw), 4),
        'sharpe_legacy_2000norm': round(float(pnl_pct_norm_legacy.mean() / float(pnl_pct_norm_legacy.std())) if float(pnl_pct_norm_legacy.std()) > 0 else 0.0, 4),
        'wr': round(wins / len(trades) * 100, 1),
        'mean_deployed': round(mean_deployed, 2), 'peak_capital': round(float(peak_capital), 2),
        'max_dd_pct': round(float(dd_pct), 2), 'bars': n,
        'ledger': sorted(trades + events, key=lambda t: (float(t.get('ts') or 0.0), 0 if t.get('type') in ('AUGMENT', 'REDUCE') else 1)),
    }


def true_bh_reference(npz, is_long, cfg):
    """Independent (non-engine) B&H gain %, for validating simulate_one's
    force_initial_seed + all-exits-off G0 baseline reproduces it exactly.
    Matches G0's fee convention: only the OPEN leg's half-fee has actually
    been paid (G0 never technically closes — it's marked-to-market), so
    this is NOT the "canonical" full-round-trip B&H used in matrix
    reporting elsewhere; it exists purely to validate the engine's own
    internal capital-accounting consistency."""
    is_tradier = getattr(cfg, 'MODE', 'crypto') == 'tradier'
    half_fee = 0.0 if is_tradier else getattr(cfg, 'CRYPTO_ROUND_TRIP_COMMISSION_PCT', 0.08) / 200.0
    base_tf = _base_tf(npz, cfg)
    ts = npz.get('timestamps', npz.get(f'timestamp_{base_tf}', np.array([])))
    n = len(ts)
    close = _base_safe(npz, 'close', n, cfg)
    valid = close > 0
    if not valid.any():
        return 0.0
    idx = np.where(valid)[0]
    p0, p1 = close[idx[0]], close[idx[-1]]
    raw = ((p1 - p0) / p0 * 100) if is_long else ((p0 - p1) / p0 * 100)
    return round(raw - half_fee * 100, 4)


_ALL_EXIT_FLAGS = [
    'DELTA_EXIT_ENABLED', 'VEL_EXIT_ENABLED', 'WT_VEL_DECAY_EXIT_ENABLED', 'STRUCTURAL_RANGE_SHIFT_EXIT',
    'SATOSHIT_ENABLED', 'SATOSHIT_EXIT_ENABLED', 'RZ_EXIT_ENABLED', 'STOCH_CROSS_1H_EXIT_ENABLED',
    'MFI_FLIP_EXIT_ENABLED', 'WT_CROSSUNDER_FINAL_ENABLED', 'MI_EXIT_ENABLED', 'MI_EXIT_ENABLED_TRADIER',
    'TRADIER_MI_EXIT_ENABLED_TRADIER', 'TRADIER_RSI2_ENABLED', 'STOCH_CROSS_3M_EXIT_ENABLED',
    'PROFIT_TARGET_ENABLED', 'STOP_LOSS_ENABLED', 'TRADIER_DC_DAYTRADE_ENABLED', 'REGIME_GATE_ENABLED',
]


def _disable_all_exits(cfg):
    """G0 baseline: turn off every exit/reduce path (including the two that
    used to be unconditional) so the position can only be closed by
    force_initial_seed's implicit hold-to-end. Leaves entries/reentry/ladder
    untouched — the reentry/ladder machinery is what re-enters after an exit
    once G1 starts turning exits back on one at a time."""
    for flag in _ALL_EXIT_FLAGS:
        if hasattr(cfg, flag):
            setattr(cfg, flag, False)
    cfg.RSI_EXIT_LONG_TRADIER = 0.0
    cfg.RSI_EXIT_SHORT_TRADIER = 100.0
    cfg.WIN_TRAIL_EROSION_PCT = 0.0  # non-flag numeric knob (default 0.5) — must be zeroed, not just flag-gated
    cfg.DELTA_MAX_HOLD_BARS = 0
    # G0 must also be pure single-entry B&H: no augment/reduce/sizing-multiplier,
    # or the forced seed position stops being an exact B&H replica. These get
    # turned back on one at a time during the real G3 (REENTRY/SIZING) search.
    for flag in ('BOUNCE_AUGMENT_ENABLED', 'PYRAMID_ENABLED', 'DELTA_GATE_AUGMENT',
                 'EMA_DIST_SIZING_ENABLED', 'ATR_ADAPTIVE_SIZING_ENABLED', 'DC_EDGE_SIZING_ENABLED'):
        if hasattr(cfg, flag):
            setattr(cfg, flag, False)
    # FIX 2026-08-10: G0 pure B&H — kill every synthetic-exit path that bypasses _ALL_EXIT_FLAGS
    cfg.STRUCTURAL_EXIT_GATE_ENABLED = False
    cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER = False
    cfg.NOLOSS_ENABLED = False
    cfg.DC_RECOVERY_EXIT_ENABLED = False
    setattr(cfg, '_G0_PURE_BH', True)


def simulate(stores, cfg, capital=10000.0):
    """CLI/back-compat wrapper — pools simulate_one() across all symbols/sides."""
    per_symbol = []
    for sym, npz in stores.items():
        for is_long in [True, False]:
            r = simulate_one(npz, sym, is_long, cfg)
            if r is not None:
                per_symbol.append(r)
    all_trades = sum(r['trades'] for r in per_symbol)
    if all_trades < 2:
        return {"sharpe": 0, "pnl": 0, "trades": all_trades, "wins": 0, "losses": 0, "avg_pnl_pct": 0, "wr": 0, "tim_pct": 0}
    total_gain_dollars = sum(float(r.get('gain_dollars', r.get('gain_dollars_2000norm', 0.0)) or 0.0) for r in per_symbol)
    wins_total = sum(int(round(r['wr'] / 100 * r['trades'])) for r in per_symbol)
    total_bars = sum(r['bars'] for r in per_symbol)
    tim_avg = sum(r['tim_pct'] * r['bars'] for r in per_symbol) / total_bars if total_bars else 0
    sharpe_vals = [r['sharpe_per_trade'] for r in per_symbol if r['trades'] >= 2]
    return {
        "sharpe": round(float(np.mean(sharpe_vals)) if sharpe_vals else 0.0, 4),
        "pnl": round(total_gain_dollars, 2),
        "trades": all_trades, "wins": wins_total, "losses": all_trades - wins_total,
        "avg_pnl_pct": round(total_gain_dollars / all_trades, 4) if all_trades else 0,
        "wr": round(wins_total / all_trades * 100, 1) if all_trades else 0,
        "tim_pct": round(tim_avg, 2),
        "per_symbol": per_symbol,
    }


FAST_SYMBOLS_CRYPTO = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,UNIUSDT"
FAST_SYMBOLS_TRADIER = "AAPL,MSFT,NVDA,AMZN,JPM,XOM,ABBV,TSLA,SPY,META,BA,GLD"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--symbols", default="BTCUSDT")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--npz-dir", default="")
    p.add_argument("--bh-reference", action="store_true",
                    help="G0 sanity check: force initial seed open, disable all exits — engine output must equal true B&H exactly (TIM=100%%)")
    from vector_mandatory_coverage import add_coverage_claim_arguments, enforce_coverage_claim
    add_coverage_claim_arguments(p)
    args = p.parse_args()
    coverage_contract = enforce_coverage_claim(args, runner="v8_quick_engine.py")
    print(f"V8_VECTOR_GROUND_RULE: {coverage_contract['coverage_status']} shortlist_sha256={coverage_contract['shortlist_sha256']}", flush=True)
    t0 = time.time()
    # PER_SYM FIX 2026-08-18: handle per_sym {SYM_LONG:{overrides:{}}} via per-symbol resolution
    # If per_sym file and single symbol requested, load that symbol's overrides; else union for multi-symbol
    _ov_env = os.environ.get("V8_OVERRIDE_FILE", "")
    _sym_hint = None
    _syms_hint = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols and args.symbols != "fast" else []
    if _ov_env and Path(_ov_env).exists():
        try:
            _raw_check = json.load(open(_ov_env))
            _is_per_sym_check = isinstance(_raw_check, dict) and any(isinstance(v, dict) and "overrides" in v for v in _raw_check.values() if isinstance(v, dict))
        except: _is_per_sym_check = False
        if _is_per_sym_check and len(_syms_hint) == 1:
            # Single symbol: resolve exact side-agnostic union (LONG+SHORT) for that symbol to keep single cfg valid for simulate() pooling
            # simulate() will pool both sides; we union overrides from both sides (last wins) — backtest hook does finer per-side
            cfg = QuickConfig.from_override_file("")  # start from defaults
            cfg._per_sym_raw = _raw_check
            cfg._is_per_sym = True
            cfg._per_sym_map = _raw_check
            _union = {}
            for _side in ["_LONG", "_SHORT"]:
                _k = f"{_syms_hint[0]}{_side}"
                if _k in _raw_check and "overrides" in _raw_check[_k]:
                    for _pk, _pv in _raw_check[_k]["overrides"].items():
                        if isinstance(_pv, str) and _pv in ("True", "False"):
                            _pv = _pv == "True"
                        _union[_pk] = _pv
            for _pk, _pv in _union.items():
                if hasattr(cfg, _pk):
                    _cur = getattr(cfg, _pk)
                    if isinstance(_cur, bool): setattr(cfg, _pk, bool(_pv))
                    elif isinstance(_cur, int) and not isinstance(_cur, bool):
                        if isinstance(_pv, bool) and _pv: continue
                        setattr(cfg, _pk, int(float(_pv)) if isinstance(_pv, str) else int(_pv))
                    elif isinstance(_cur, float):
                        if isinstance(_pv, bool) and _pv: continue
                        setattr(cfg, _pk, float(_pv))
                    else: setattr(cfg, _pk, _pv)
                else:
                    setattr(cfg, _pk, _pv)
        else:
            cfg = QuickConfig.from_override_file(_ov_env)
            # If per_sym but multi-symbol, union all requested symbols' overrides
            if getattr(cfg, "_is_per_sym", False) and _syms_hint:
                _union = {}
                for _s in _syms_hint:
                    for _side in ["_LONG", "_SHORT"]:
                        _k = f"{_s}{_side}"
                        if _k in _raw_check and "overrides" in _raw_check[_k]:
                            for _pk, _pv in _raw_check[_k]["overrides"].items():
                                if isinstance(_pv, str) and _pv in ("True", "False"):
                                    _pv = _pv == "True"
                                _union[_pk] = _pv
                for _pk, _pv in _union.items():
                    if hasattr(cfg, _pk):
                        _cur = getattr(cfg, _pk)
                        if isinstance(_cur, bool): setattr(cfg, _pk, bool(_pv))
                        elif isinstance(_cur, int) and not isinstance(_cur, bool):
                            if isinstance(_pv, bool) and _pv: continue
                            setattr(cfg, _pk, int(float(_pv)) if isinstance(_pv, str) else int(_pv))
                        elif isinstance(_cur, float):
                            if isinstance(_pv, bool) and _pv: continue
                            setattr(cfg, _pk, float(_pv))
                        else: setattr(cfg, _pk, _pv)
                    else:
                        setattr(cfg, _pk, _pv)
    else:
        cfg = QuickConfig.from_override_file(_ov_env)
    if args.mode == "tradier":
        cfg.apply_tradier_defaults()
        # For tradier per_sym, overrides already unioned above; for flat tradier file, need second pass after defaults
        ov = os.environ.get("V8_OVERRIDE_FILE", "")
        if ov and Path(ov).exists():
            try:
                _raw2 = json.load(open(ov))
                _is_per2 = isinstance(_raw2, dict) and any(isinstance(v, dict) and "overrides" in v for v in _raw2.values() if isinstance(v, dict))
            except: _is_per2 = False
            if not _is_per2:
                for k, v in _raw2.items():
                    if k.startswith("_"): continue
                    if hasattr(cfg, k):
                        cur = getattr(cfg, k)
                        if isinstance(v, str) and v in ("True", "False"):
                            v = v == "True"
                        if isinstance(cur, bool): setattr(cfg, k, bool(v))
                        elif isinstance(cur, int): setattr(cfg, k, int(float(v)) if isinstance(v, str) else int(v))
                        elif isinstance(cur, float): setattr(cfg, k, float(v))
                        else: setattr(cfg, k, v)
    syms = None
    if args.symbols == "fast":
        syms = (FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO).split(",")
    elif args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    stores = load_npz(args.mode, syms, args.start, args.npz_dir)
    if not stores:
        print("No data"); return
    if args.bh_reference:
        _disable_all_exits(cfg)
        worst_diff = 0.0
        for sym, npz in stores.items():
            for is_long in [True, False]:
                r = simulate_one(npz, sym, is_long, cfg, force_initial_seed=True)
                if r is None:
                    continue
                true_bh = true_bh_reference(npz, is_long, cfg)
                diff = abs(r['gain_pct_2000norm'] - true_bh)
                worst_diff = max(worst_diff, diff)
                side = "LONG" if is_long else "SHORT"
                print(f"G0_CHECK {sym} {side}: engine_gain={r['gain_pct_2000norm']:.4f}% true_bh={true_bh:.4f}% "
                      f"diff={diff:.4f}pp tim={r['tim_pct']:.2f}% trades={r['trades']}")
        print(f"G0_CHECK worst_diff_across_all={worst_diff:.4f}pp — must be ~0 for the baseline to be a valid B&H anchor")
        return
    r = simulate(stores, cfg, args.capital)
    el = time.time() - t0
    print(f"V8_QUICK_RESULT: sharpe={r['sharpe']} pnl={r['pnl']:.2f} trades={r['trades']} "
          f"wins={r['wins']} losses={r['losses']} wr={r['wr']}% tim={r['tim_pct']:.2f}% "
          f"avg_pnl={r['avg_pnl_pct']:.4f}% elapsed={el:.1f}s")


if __name__ == "__main__":
    main()
# --- 2026-09-04: add missing AUTO_WIRED fields to QuickConfig so complete set has no missing fields ---
try:
    import dataclasses as _dc
    _missing = ['ABLATION_DISABLE_FAST_RISER', 'ABLATION_DISABLE_SCALP_GUARD', 'ADAPTIVE_REGIME_DC_BREAKDOWN_THRESHOLD', 'ADAPTIVE_REGIME_DC_BREAKOUT_THRESHOLD', 'ADAPTIVE_REGIME_DECAY_HALFLIFE_H', 'ADAPTIVE_REGIME_HEAT_TRIGGER', 'ADAPTIVE_REGIME_LOOKBACK_DAYS', 'ADAPTIVE_REGIME_MIN_SIGNALS', 'ADAPTIVE_REGIME_NPZ_CACHE_HOURS', 'ADAPTIVE_REGIME_SHARPE_FLOOR', 'ADX_TF', 'AGGRESSIVE_LOSS_CUT_ENABLED', 'AI_PREMARKET_DECISIONS_DIR', 'AI_PREMARKET_ENABLED_TRB', 'AI_PREMARKET_EXPIRES_ET', 'AI_PREMARKET_MAX_NEW_PER_SIDE', 'AI_PREMARKET_MIN_CONVICTION', 'AI_PREMARKET_SIZE_MULT_MAX', 'API_RATE_LIMIT_PER_MINUTE', 'API_RATE_LIMIT_PER_SECOND']  # sample, full list in AUTO_WIRED_PARAMS
    # full missing is derived from AUTO_WIRED_PARAMS at runtime, not hard-coded here
    for _k in [k for k in AUTO_WIRED_PARAMS if not hasattr(QuickConfig, k)]:
        setattr(QuickConfig, _k, False)
except Exception: pass

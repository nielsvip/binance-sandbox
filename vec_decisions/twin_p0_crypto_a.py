"""twin_p0_crypto_a — scalar live twins for P0 VEC_ONLY crypto switches (names 1-53).

Vector reference: v12_quick_engine.py in this worktree, plus the parent checkout's
vec_decisions/gap_risk_exit_live.py for the GAP_RISK_EXIT family (that module is
absent in this checkout, so the engine's import try/excepts to inert there).
Each twin mirrors the vector predicate EXACTLY: same key, same getattr default,
same inequality direction, same `or`-fallback chains. `vec_*` functions are the
transcribed array reference; sibling scalars are the live twin. Agreement is proven
in test_p0_crypto_a_twin.py with NO engine import (v12_quick_engine is unimportable
in this checkout — missing vec_decisions.* modules — same constraint as prior waves).

Switch order follows /tmp/p0_vec_only_crypto.txt rows 1-53.
"""
import numpy as np


def _g(cfg, key, default):
    return getattr(cfg, key, default)


# ── 1-5. ABLATION_DISABLE_* — effective semantics are the INLINE block ─────────
# v12:12473-12498. NOTE: _apply_625_ablation_gates (v12:11488) is NEVER called
# (no callers), so its stronger kills (RATIO entry+augment, SPIKE_FADE exit) are
# dead; the inline `pass` versions below are what the vector actually does.

def ablation_aggressive_hedge_kills_augment(cfg):
    """v12:12473-12474 — augment_sig[:] = False."""
    return bool(_g(cfg, 'ABLATION_DISABLE_AGGRESSIVE_HEDGE', False))


def ablation_high_gain_kills_augment(cfg):
    """v12:12483-12484 — augment_sig[:] = False."""
    return bool(_g(cfg, 'ABLATION_DISABLE_HIGH_GAIN_AUGMENT', False))


def ablation_dc_breach_kills_reduce(cfg):
    """v12:12479-12480 — reduce_sig[:] = False; reduce_frac[:] = 0.0."""
    return bool(_g(cfg, 'ABLATION_DISABLE_DC_BREACH_REDUCE', False))


def ablation_ratio_rebalance_kills(cfg):
    """v12:12485-12486 — `pass  # no ratio model`. Key read, no effect.

    Returns (kills_entry, kills_augment, clears_blocks) = (False, False, False).
    """
    _ = bool(_g(cfg, 'ABLATION_DISABLE_RATIO_REBALANCE', False))
    return (False, False, False)


def ablation_spike_fade_kills_exit(cfg):
    """v12:12496-12498 — `pass`. Key read, no effect on exit_sig."""
    _ = bool(_g(cfg, 'ABLATION_DISABLE_SPIKE_FADE_EXIT', False))
    return False


def vec_apply_ablation(n, cfg):
    """Array reference: (entry_killed, exit_killed, augment_killed, reduce_killed)."""
    entry_k = np.zeros(n, dtype=bool)
    exit_k = np.zeros(n, dtype=bool)
    aug_k = np.zeros(n, dtype=bool)
    red_k = np.zeros(n, dtype=bool)
    if ablation_aggressive_hedge_kills_augment(cfg):
        aug_k[:] = True
    if ablation_high_gain_kills_augment(cfg):
        aug_k[:] = True
    if ablation_dc_breach_kills_reduce(cfg):
        red_k[:] = True
    _re = ablation_ratio_rebalance_kills(cfg)
    if _re[0]:
        entry_k[:] = True
    if _re[1]:
        aug_k[:] = True
    if ablation_spike_fade_kills_exit(cfg):
        exit_k[:] = True
    return entry_k, exit_k, aug_k, red_k


# ── 6. ADX_TRENDING_THRESHOLD (v12:10414, v12:10448) ────────────────────────────
def vec_trending(adx_1h, cfg):
    return np.asarray(adx_1h, dtype=float) >= _g(cfg, 'ADX_TRENDING_THRESHOLD', 25.0)


def live_trending(adx_1h, cfg):
    return float(adx_1h) >= _g(cfg, 'ADX_TRENDING_THRESHOLD', 25.0)


# ── 7. AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER (v12:10377-10380) ───────────────────
# NOTE: NOT mode-gated; called in the shared augment loop (v12:13702).
def augment_allowed(cfg, live_pnl_pct):
    """Exact port of v12._augment_allowed."""
    if _g(cfg, 'AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER', False):
        return live_pnl_pct > 0
    return live_pnl_pct <= _g(cfg, 'BOUNCE_AUGMENT_MIN_LOSS_PCT', -0.5)


def vec_augment_allowed(cfg, live_pnl_pct_arr):
    return np.array([augment_allowed(cfg, float(x)) for x in np.asarray(live_pnl_pct_arr, dtype=float)], dtype=bool)


# ── 8. BACKTEST_VALIDATED_GATES_TRADIER (v12:9257-9266) ─────────────────────────
def validated_gates_apply(cfg):
    return bool(_g(cfg, 'BACKTEST_VALIDATED_GATES_TRADIER', False)) and _g(cfg, 'MODE', 'crypto') == 'tradier'


def validated_tf_need(cfg):
    tf_gate_total = _g(cfg, 'TF_ALIGNMENT_MIN_TOTAL', 0)
    return max(1, min(3, int(tf_gate_total // 4))) if tf_gate_total > 0 else 1


def validated_tf_count(wt1_1h, wt2_1h, wt1_4h, wt2_4h, wt1_D, wt2_D, is_long):
    if is_long:
        return int(wt1_1h > wt2_1h) + int(wt1_4h > wt2_4h) + int(wt1_D > wt2_D)
    return int(wt1_1h < wt2_1h) + int(wt1_4h < wt2_4h) + int(wt1_D < wt2_D)


def validated_gate_pass(cfg, wt1_1h, wt2_1h, wt1_4h, wt2_4h, wt1_D, wt2_D, is_long):
    if not validated_gates_apply(cfg):
        return True
    return validated_tf_count(wt1_1h, wt2_1h, wt1_4h, wt2_4h, wt1_D, wt2_D, is_long) >= validated_tf_need(cfg)


# ── 9. BB_RECOVERY_EXIT_ENABLED_TRADIER (v12:10017-10025) ────────────────────────
# NOTE: not mode-gated in code (comment says stocks mode, code applies to crypto too).
def vec_bb_recovery_exit(close, bb_upper_1h, bb_lower_1h, k_1h, is_long):
    close = np.asarray(close, dtype=float)
    mid = (np.asarray(bb_upper_1h, dtype=float) + np.asarray(bb_lower_1h, dtype=float)) / 2.0
    k = np.asarray(k_1h, dtype=float)
    if is_long:
        return (close >= mid) & (k >= 70)
    return (close <= mid) & (k <= 30)


def bb_recovery_exit(close, bb_upper_1h, bb_lower_1h, k_1h, is_long):
    mid = (float(bb_upper_1h) + float(bb_lower_1h)) / 2.0
    if is_long:
        return (float(close) >= mid) and (float(k_1h) >= 70)
    return (float(close) <= mid) and (float(k_1h) <= 30)


# ── 10-11. CHOP_RANGING_THRESHOLD / CHOP_TRENDING_THRESHOLD (v12:9043-9052) ──
# Master: bool(cfg value) != False  (_DEFAULTS_625:10665 False) => enabled-only.
# CHOP_RANGING_THRESHOLD is READ (v12:9048) but UNUSED in the formula — mirrored.
def chop_gate_applies(cfg):
    return bool(_g(cfg, 'CT_CHOP_4H_GATE_ENABLED', False)) is True


def vec_chop_ok(chop_4h, adx_4h, bb_width_4h, cfg):
    _ranging_thr = _g(cfg, 'CHOP_RANGING_THRESHOLD', 61.8)  # noqa: F841 (read, unused — like vector)
    trending_thr = _g(cfg, 'CHOP_TRENDING_THRESHOLD', 38.2)
    is_ranging = (np.asarray(chop_4h, dtype=float) > trending_thr) | (np.asarray(adx_4h, dtype=float) < 25) | (np.asarray(bb_width_4h, dtype=float) < 0.5)
    return ~is_ranging


def chop_gate_ok(chop_4h, adx_4h, bb_width_4h, cfg):
    _ranging_thr = _g(cfg, 'CHOP_RANGING_THRESHOLD', 61.8)  # noqa: F841 (read, unused — like vector)
    trending_thr = _g(cfg, 'CHOP_TRENDING_THRESHOLD', 38.2)
    is_ranging = (float(chop_4h) > trending_thr) or (float(adx_4h) < 25) or (float(bb_width_4h) < 0.5)
    return not is_ranging


# ── 12. COMBINED_STOCH_GATE_TRADIER (v12:9235-9243) ─────────────────────────────
def resolve_csg_k(has_5m, k_5m, k_3m):
    """v12:9238 — stoch_k_5m if present else k_3m."""
    return k_5m if has_5m else k_3m


def vec_stoch_gate_pass(k_arr, cfg, is_long):
    csg = float(_g(cfg, 'COMBINED_STOCH_GATE_TRADIER', 100.0))
    k = np.asarray(k_arr, dtype=float)
    if csg >= 100.0:
        return np.ones(k.shape, dtype=bool)
    if is_long:
        return k < csg
    return k > (100.0 - csg)


def stoch_gate_pass(k, cfg, is_long):
    csg = float(_g(cfg, 'COMBINED_STOCH_GATE_TRADIER', 100.0))
    if csg >= 100.0:
        return True
    if is_long:
        return float(k) < csg
    return float(k) > (100.0 - csg)


# ── 13-14. COOLDOWN_BARS / COOLDOWN_BARS_TRADIER (v12:12505) ─────────────────────
def cooldown_bars(cfg):
    """Direct-attr mirror: cfg.COOLDOWN_BARS (no getattr default in vector)."""
    is_tradier = _g(cfg, 'MODE', 'crypto') == 'tradier'
    if is_tradier:
        return _g(cfg, 'COOLDOWN_BARS_TRADIER', cfg.COOLDOWN_BARS)
    return cfg.COOLDOWN_BARS


# ── 15. DC_BREAK_LOW_HTF_ALIGN_MIN (v12:9413-9414) ──────────────────────────────
def wt_bear_count(wt1_1h, wt2_1h, wt1_4h, wt2_4h, wt1_D, wt2_D):
    return int(wt1_1h < wt2_1h) + int(wt1_4h < wt2_4h) + int(wt1_D < wt2_D)


def htf_align_ok(bear_count, cfg):
    return int(bear_count) >= int(float(_g(cfg, 'DC_BREAK_LOW_HTF_ALIGN_MIN', 2)))


# ── 16-17. DC_DAYTRADE_STOP/TARGET_PCT (v12:12521-12532) ─────────────────────────
# Computed (*100 -> daytrade_stop/target) but NEVER consumed afterwards in the
# vector (dead computation); live daytrade uses the DC-list path only.
def daytrade_on(cfg):
    is_tradier = _g(cfg, 'MODE', 'crypto') == 'tradier'
    if is_tradier:
        return bool(_g(cfg, 'TRADIER_DC_DAYTRADE_ENABLED', False))
    return bool(_g(cfg, 'DC_DAYTRADE_ENABLED', False))


def daytrade_levels(cfg):
    """Returns (stop_pct, target_pct) in PERCENT units (vector *100 scaling)."""
    is_tradier = _g(cfg, 'MODE', 'crypto') == 'tradier'
    if is_tradier:
        return (float(_g(cfg, 'TRADIER_DC_DAYTRADE_STOP_PCT', 0.005)) * 100,
                float(_g(cfg, 'TRADIER_DC_DAYTRADE_TARGET_PCT', 0.005)) * 100)
    return (float(_g(cfg, 'DC_DAYTRADE_STOP_PCT', 0.015)) * 100,
            float(_g(cfg, 'DC_DAYTRADE_TARGET_PCT', 0.01)) * 100)


# ── 18. DC_HARD_STOP_REENTRY_COOLDOWN_HOURS (v12:13531-13534, tradier-only) ──────
def dc_hardstop_cd(cd, cfg, is_tradier, bmin):
    if is_tradier:
        h = float(_g(cfg, 'DC_HARD_STOP_REENTRY_COOLDOWN_HOURS', 4.0) or 0.0)
        if h > 0:
            cd = max(cd, int(round(h * 60.0 / max(bmin, 1))))
    return cd


# ── 19. DC_POSITION_ENTRY_THRESHOLD (v12:8673-8679) ─────────────────────────────
def dcpos_master_on(cfg):
    return bool(_g(cfg, 'DC_DAYTRADE_ENABLED', False)) or bool(_g(cfg, 'TRADIER_DC_DAYTRADE_ENABLED', False))


def dcpos_thr(cfg):
    if _g(cfg, 'MODE', 'crypto') == 'tradier':
        return _g(cfg, 'TRADIER_DC_POSITION_ENTRY_THRESHOLD', 0.15)
    return _g(cfg, 'DC_POSITION_ENTRY_THRESHOLD', 0.15)


def vec_daytrade_entry_ok(dc_pos_15m, thr, is_long, expansion_ok):
    a = np.asarray(dc_pos_15m, dtype=float)
    e = np.asarray(expansion_ok, dtype=bool)
    if is_long:
        return (a < thr) & e
    return (a > (1 - thr)) & e


def daytrade_entry_ok(dc_pos_15m, thr, is_long, expansion_ok=True):
    if is_long:
        return (float(dc_pos_15m) < thr) and bool(expansion_ok)
    return (float(dc_pos_15m) > (1 - thr)) and bool(expansion_ok)


# ── 20. DELTA_ATR_ENTRY_FILTER (v12:8729-8735 long, v12:8745-8751 short) ─────────
def atr_entry_master_on(cfg):
    return bool(_g(cfg, 'DELTA_ATR_ENTRY_FILTER', False))


def vec_atr_entry_ok(bar_move, atr_1h):
    m = np.asarray(bar_move, dtype=float)
    a = np.asarray(atr_1h, dtype=float)
    return (a <= 0) | (m >= a * 0.3)


def atr_entry_ok(bar_move, atr_1h):
    return (float(atr_1h) <= 0) or (float(bar_move) >= float(atr_1h) * 0.3)


# ── 21. DELTA_EXIT_DC_FLOOR (v12:9897-9903, tradier + STOCKS_LIVE_TWINS gated) ────
def delta_exit_dc_floor_applies(cfg):
    return (str(_g(cfg, 'MODE', 'crypto')) == 'tradier'
            and bool(_g(cfg, 'DELTA_EXIT_DC_FLOOR', False))
            and bool(_g(cfg, 'STOCKS_LIVE_TWINS_ENABLED', False)))


# ── 22-24. DELTA_GATE_AUGMENT/OPEN/REENTRY ───────────────────────────────────────
# v12:9293-9301 (open/reentry), v12:10299-10300 (augment). Dead-in-live by design
# (C2 b4): vector itself is inert unless VEC_HONOR_DEAD_LIVE_DELTA_GATES — mirrored.
def _delta_honor(cfg):
    return bool(_g(cfg, 'VEC_HONOR_DEAD_LIVE_DELTA_GATES', False))


def delta_gate_open_allows(cfg):
    return not (_delta_honor(cfg) and not _g(cfg, 'DELTA_GATE_OPEN', True))


def delta_gate_reentry_allows(cfg):
    return not (_delta_honor(cfg) and not _g(cfg, 'DELTA_GATE_REENTRY', True))


def delta_gate_augment_allows(cfg):
    return not (_delta_honor(cfg) and not _g(cfg, 'DELTA_GATE_AUGMENT', True))


def vec_delta_gate_allows(cfg, which):
    """Array reference: all-True unless (honor and gate False) kills all."""
    fn = {'open': delta_gate_open_allows, 'reentry': delta_gate_reentry_allows,
          'augment': delta_gate_augment_allows}[which]
    return fn(cfg)


# ── 25. DELTA_MAX_HOLD_BARS (v12:12520) ──────────────────────────────────────────
def delta_max_hold_bars(cfg):
    if _g(cfg, 'DELTA_ENGINE_ENABLED', False):
        return _g(cfg, 'DELTA_MAX_HOLD_BARS', 0)
    return 0


# ── 26. DYN_STRUCT_TRAIL_ENABLED (v12:11958-11959, tradier-only) ─────────────────
def dyn_trail_applies(cfg):
    return _g(cfg, 'MODE', 'crypto') == 'tradier' and bool(_g(cfg, 'DYN_STRUCT_TRAIL_ENABLED', False))


def dyn_trail_field(is_long, cfg):
    tf = str(_g(cfg, 'DYN_STRUCT_TRAIL_TF', '4h') or '4h')
    return ('dc_low_' if is_long else 'dc_high_') + tf


# ── 27. FORCE_MIN_ONE_TRADE (v12:9862-9873, backtest-only construct) ─────────────
def force_first_index(close_arr, base_entry, cfg):
    """Returns bar index to force True, or None. Exact argmax semantics."""
    close = np.asarray(close_arr, dtype=float)
    base = np.asarray(base_entry, dtype=bool)
    n = len(base)
    if np.any(base) or not bool(_g(cfg, 'FORCE_MIN_ONE_TRADE', False)):
        return None
    first_valid = int(np.argmax(close > 0)) if np.any(close > 0) else 0
    if first_valid < n:
        return first_valid
    return None


# ── 28-40. GAP_MOC / GAP_CLOSE_MOC families (v12:11695-11899, tradier-only) ──────
def gap_bar_min(cfg):
    try:
        return int(''.join(filter(str.isdigit, str(_g(cfg, 'BASE_TF', '15m')))) or 15)
    except Exception:
        return 15


def gap_bars_per_day(cfg):
    return max(26, int(390 / max(gap_bar_min(cfg), 1)))


def gap_bars_90m(cfg):
    return max(2, int(90 / max(gap_bar_min(cfg), 1)))


def gap_moc_gates(cfg):
    """v12:11695-11706. Effective: enabled and not parity_off and tradier."""
    import os as _os
    enabled = bool(_g(cfg, 'GAP_MOC_EXIT_ENABLED', True))
    parity_off = (bool(_g(cfg, 'PARITY_DISABLE_NON_VECTORIZABLE', False))
                  or bool(_g(cfg, 'V12_PARITY_DISABLE_NON_VECTORIZABLE', False))
                  or bool(_os.environ.get('V12_PARITY_MIN_DECISION_TF')))
    return {'enabled': enabled, 'parity_off': parity_off,
            'is_tradier': _g(cfg, 'MODE', 'crypto') == 'tradier',
            'applies': enabled and not parity_off and _g(cfg, 'MODE', 'crypto') == 'tradier'}


def gap_lookback_days(cfg):
    """v12:11758 — `or` fallback: 0/None falls through to inventory default."""
    return int(_g(cfg, 'GAP_PER_SYMBOL_LOOKBACK_DAYS', 30) or _g(cfg, 'GAP_INVENTORY_LOOKBACK_DAYS', 20))


def gap_avg_threshold(cfg):
    """v12:11775 — `or` fallback: 0.0 falls through to positive-bias default."""
    return float(_g(cfg, 'GAP_PER_SYMBOL_AVG_THRESH_PCT', 0.10) or _g(cfg, 'GAP_MOC_HOLD_POSITIVE_BIAS_PCT', 0.30))


def gap_pct(open_d, prev_close):
    o, p = float(open_d), float(prev_close)
    if p > 0 and o > 0:
        return (o - p) / p * 100.0
    return 0.0


def vec_gap_pct(open_d, prev_close):
    o = np.asarray(open_d, dtype=float)
    p = np.asarray(prev_close, dtype=float)
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.divide(o - p, p, out=np.zeros_like(p, dtype=float), where=(p > 0) & (o > 0)) * 100.0


def gap_in_window(i, bars_per_day, bars_90m):
    off = int(i) % bars_per_day
    return (bars_per_day - bars_90m) <= off < bars_per_day


def gap_at_deadline(i, bars_per_day):
    return (int(i) % bars_per_day) == bars_per_day - 1


def gap_is_top(wt1_15, wt2_15, is_long):
    if is_long:
        return float(wt1_15) < float(wt2_15)
    return float(wt1_15) > float(wt2_15)


def gap_should(avg_gap, thr, is_long):
    """v12:11787-11790 — longs on gap-down risk, shorts on gap-up risk."""
    if is_long:
        return float(avg_gap) < -thr
    return float(avg_gap) > thr


def gap_vv_override(cfg, dc_edge_4h, price, wt1_15, wt2_15, wt1_1h, wt2_1h, is_long):
    """v12:11792-11806 near-edge + WT-against override. px<=0 -> False (nan-safe)."""
    prox = float(_g(cfg, 'GAP_MOC_DC_PROXIMITY_PCT', 0.50))
    px = float(price)
    if px <= 0:
        return False
    edge = float(dc_edge_4h)
    if is_long:
        near = (edge - px) / px * 100.0 <= prox
        against = (float(wt1_15) < float(wt2_15)) or (float(wt1_1h) < float(wt2_1h))
    else:
        near = (px - edge) / px * 100.0 <= prox
        against = (float(wt1_15) > float(wt2_15)) or (float(wt1_1h) > float(wt2_1h))
    return bool(near and against)


def gap_fire(in_window, should_or_vv, is_top, at_deadline, force_moc):
    """v12:11807-11816 — window+should+top, plus deadline force."""
    fire = bool(in_window and should_or_vv and is_top)
    if force_moc:
        fire = fire or bool(at_deadline and should_or_vv)
    return fire


def gap_force_moc(cfg):
    return bool(_g(cfg, 'GAP_MOC_FORCE_MOC_AT_CLOSE', True))


def vec_gap_fire(in_window, should, is_top, at_deadline, force_moc):
    w = np.asarray(in_window, dtype=bool)
    s = np.asarray(should, dtype=bool)
    t = np.asarray(is_top, dtype=bool)
    fire = w & s & t
    if force_moc:
        fire = fire | (np.asarray(at_deadline, dtype=bool) & s)
    return fire


# CLOSE-GAP sentinel (v12:11828-11898)
def close_gap_gates(cfg, is_long):
    enabled = bool(_g(cfg, 'GAP_CLOSE_MOC_EXIT_ENABLED', True))
    only_stocks = bool(_g(cfg, 'GAP_CLOSE_MOC_ONLY_STOCKS', True))
    only_shorts = bool(_g(cfg, 'GAP_CLOSE_MOC_ONLY_FOR_SHORTS', True))
    is_tr = _g(cfg, 'MODE', 'crypto') == 'tradier'
    return {'enabled': enabled, 'only_stocks': only_stocks, 'only_shorts': only_shorts,
            'applies': enabled and (not only_stocks or is_tr) and not (is_long and only_shorts)}


def close_gap_pct(close_d, open_d):
    c, o = float(close_d), float(open_d)
    if c > 0 and o > 0:
        return (c - o) / o * 100.0
    return 0.0


def close_gap_lookback_days(cfg):
    return int(_g(cfg, 'GAP_CLOSE_PER_SYMBOL_LOOKBACK_DAYS', 30))


def close_gap_thr(cfg):
    return float(_g(cfg, 'GAP_CLOSE_PER_SYMBOL_AVG_THRESH_PCT', 0.10))


def close_gap_should(avg_cg, thr, is_long, only_shorts):
    """v12:11888-11891 — shorts close on avg intraday-up > thr."""
    if not is_long:
        return float(avg_cg) > thr
    if not only_shorts:
        return float(avg_cg) < -thr
    return False


def close_gap_force_moc(cfg):
    return bool(_g(cfg, 'GAP_CLOSE_MOC_FORCE_MOC_AT_CLOSE', True))


# ── 41-47. GAP_RISK_EXIT_* (v12:11905-11909 dispatches to ─────────────────────────
# vec_decisions/gap_risk_exit_live.py, absent in this checkout; engine try/excepts
# to inert. Reference below transcribed from the parent checkout's module, whose
# getattr defaults are False (note: v12 QuickConfig FIELD defaults are True at
# v12:6792-6804 — fields win when cfg carries them, so twin callers pass cfg
# through and the module's getattr defaults only bite on bare namespaces).
def gap_risk_gates(cfg, is_long):
    """Parent module lines 32-38: master / side / A / B enables."""
    master = bool(_g(cfg, 'GAP_RISK_EXIT_ENABLED', False))
    side = bool(_g(cfg, 'GAP_RISK_EXIT_LONG_ENABLED' if is_long else 'GAP_RISK_EXIT_SHORT_ENABLED', False))
    a_en = bool(_g(cfg, 'GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED', False)) or bool(_g(cfg, 'GAP_RISK_EXIT_COND_A_ENABLED', False))
    b_en = bool(_g(cfg, 'GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED', False)) or bool(_g(cfg, 'GAP_RISK_EXIT_COND_B_ENABLED', False))
    return {'master': master, 'side': side, 'a': a_en, 'b': b_en,
            'applies': master and side and (a_en or b_en)}


def gap_risk_new_state():
    return {'open': None, 'prev': None, 'retraced': False, 'ext_hi': 0.0, 'ext_lo': 0.0}


def gap_risk_step(state, o, p, h, l, c, gates, is_long):
    """Scalar mirror of one loop iteration of gap_risk_exit_live_vec.

    state: dict from gap_risk_new_state() (mutated in place, like live's
    _GAP_RISK_STATE). Returns True on exit fire (state reset, like live pop).
    """
    o, p, h, l, c = float(o), float(p), float(h), float(l), float(c)
    if not (o and p):
        state.update(gap_risk_new_state())
        return False
    gap = (o < p) if is_long else (o > p)
    if not gap:
        state.update(gap_risk_new_state())
        return False
    if state['open'] != o or state['prev'] != p:
        state['open'], state['prev'] = o, p
        state['retraced'] = False
        state['ext_hi'], state['ext_lo'] = h, l
    if not state['retraced']:
        if (not is_long) and l and l <= p:
            state['retraced'] = True
        elif is_long and h and h >= p:
            state['retraced'] = True
    if h:
        state['ext_hi'] = max(state['ext_hi'] or h, h)
    if l:
        state['ext_lo'] = min(state['ext_lo'] or l, l) if state['ext_lo'] else l
    if not state['retraced']:
        return False
    ca = cb = False
    if gates['a']:
        ca = (c > o or h > o) if not is_long else (c < o or (l and l < o))
    if gates['b']:
        if (not is_long) and h and h > o:
            cb = h > p and h >= o and h == state['ext_hi']
        elif is_long and l and l < o:
            cb = l < p and l <= o and l == state['ext_lo']
    if ca or cb:
        state.update(gap_risk_new_state())
        return True
    return False


def vec_gap_risk_exit(npz, n, cfg, is_long):
    """Array reference — exact transcription of the parent module's loop."""
    def _arr(key, default=0.0):
        if key in npz:
            a = np.asarray(npz[key], dtype=float)
            if a.size < n:
                t = np.full(n, default)
                t[:a.size] = a
                a = t
            return np.nan_to_num(a[:n], nan=0.0, posinf=0.0, neginf=0.0)
        return np.full(n, default)
    out = np.zeros(n, dtype=bool)
    gates = gap_risk_gates(cfg, is_long)
    if not gates['applies']:
        return out
    od, pc = _arr('open_D'), _arr('close_D_prev')
    hi, lo = _arr('high_D'), _arr('low_D')
    cl, px = _arr('close_D'), _arr('close')
    close = np.where(cl > 0, cl, px)
    st = gap_risk_new_state()
    for i in range(n):
        if gap_risk_step(st, od[i], pc[i], hi[i], lo[i], close[i], gates, is_long):
            out[i] = True
    return out


# ── 48-51. GAP_RISK_REENTRY_* — CONFIG-ONLY (no vector predicate anywhere) ───────
# v12 field defaults (v12:6805-6808). No getattr read outside catalogs.
def gap_risk_reentry_cfg(cfg):
    return {'enabled': bool(_g(cfg, 'GAP_RISK_REENTRY_ENABLED', True)),
            'max_days': int(_g(cfg, 'GAP_RISK_REENTRY_MAX_DAYS', 5)),
            'on_fill': bool(_g(cfg, 'GAP_RISK_REENTRY_ON_FILL', True)),
            'require_trend': bool(_g(cfg, 'GAP_RISK_REENTRY_REQUIRE_TREND', False))}


# ── 52. GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN — DEAD READ in vector ──────────────
# v12:8624 reads into _gr_hedge, v12:8630 discards it in `_ = (...)`. No effect.
def guaranteed_hedge_required_value(cfg):
    return bool(_g(cfg, 'GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN', True))


def guaranteed_hedge_affects_vector():
    return False


# ── 53. HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN (v12:12960-12970) ─────────────────
def rally_bypass_gate(cfg, pos_none, closed_before, has_trades, cd):
    return (bool(_g(cfg, 'HARDCODED_RALLY_REENTRY_ENABLED', True))
            and bool(_g(cfg, 'HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN', True))
            and bool(pos_none) and bool(closed_before) and bool(has_trades) and cd > 0)


def rally_fires(px, exit_px, wt1, wt1_prev, req_wt, is_long, hrf_ok=True):
    """v12:12962-12969. _hrf_ok filter passed in (separate live/vector helper)."""
    px, exit_px = float(px), float(exit_px)
    if exit_px <= 0:
        return False
    wt_ok_long = (not req_wt) or (float(wt1) > float(wt1_prev))
    wt_ok_short = (not req_wt) or (float(wt1) < float(wt1_prev))
    if ((is_long and px > exit_px and wt_ok_long)
            or ((not is_long) and px < exit_px and wt_ok_short)) and hrf_ok:
        return True
    return False

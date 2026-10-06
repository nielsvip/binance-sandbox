"""Stocks live twins of the vec entry FILTER_TF masks (PARITY LANE C, 2026-10-06).

Scalar, per-tick mirrors of the exact vec predicates applied to ``entry_sig`` in
``v12_quick_engine.simulate_one``:
  - vec_decisions.filter_tf_gates.mom3_entry_gate / momentum_breakout_gate
  - vec_decisions.bb_pullback_gate.bb_pullback_gate_vec (when BB_PULLBACK_GATE_FILTER_TF != OFF)
  - vec_decisions.generic_filter_tf._cond kinds: bb_reclaim, dc_break, wt_cross_side,
    bar_pattern_side, dc_retest_hold, bb_bounce
Each function returns a veto string (entry blocked) or None (entry allowed).
Missing live keys fail OPEN exactly like the vec ``safe`` defaults do.
Pure stdlib, no tradier_manage import (unit-testable in isolation).
"""
from __future__ import annotations

import math

BAR_PATTERN_BULL = frozenset({"morning_star", "three_white_soldiers", "bull_engulfing", "tweezer_bottom", "hammer", "bull_harami", "pin_bar_bull", "three_bar_bull"})
BAR_PATTERN_BEAR = frozenset({"evening_star", "three_black_crows", "bear_engulfing", "tweezer_top", "shooting_star", "bear_harami", "pin_bar_bear", "three_bar_bear"})
BAR_PATTERN_CODES = {"none": 0, "morning_star": 1, "evening_star": 2, "three_white_soldiers": 3, "three_black_crows": 4, "bull_engulfing": 5, "bear_engulfing": 6, "tweezer_bottom": 7, "tweezer_top": 8, "hammer": 9, "shooting_star": 10, "bull_harami": 11, "bear_harami": 12, "multi_inside": 13, "inside_bar": 14, "outside_bar": 15, "pin_bar_bull": 16, "pin_bar_bear": 17, "three_bar_bull": 18, "three_bar_bear": 19, "doji": 20}
_BULL_CODES = frozenset({1, 3, 5, 7, 9, 11, 16, 18})
_BEAR_CODES = frozenset({2, 4, 6, 8, 10, 12, 17, 19})


def _num(get_ind, key):
    try:
        v = get_ind(key)
    except Exception:
        return None
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _fnum(v, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if math.isnan(f) else f


def resolve_tf(raw):
    t = str(raw if raw is not None else "OFF").strip()
    return None if (not t or t.upper() == "OFF") else t


def mom3_block(get_ind, price, is_long, tf, thr_long, thr_short):
    if tf is None or not price or price <= 0:
        return None
    c3 = _num(get_ind, f"close_3bar_{tf}")
    if c3 is None or c3 <= 0:
        return None
    m = (price - c3) / c3 * 100.0
    ok = (m < _fnum(thr_long, -1.0)) if is_long else (m > _fnum(thr_short, 1.0))
    return None if ok else f"MOM3_FILTER_TF_{tf}_BLOCK({m:.2f}%)"


def momentum_breakout_block(get_ind, price, is_long, tf):
    if tf is None or not price or price <= 0:
        return None
    c3 = _num(get_ind, f"close_3bar_{tf}")
    if c3 is None or c3 <= 0:
        return None
    m = (price - c3) / c3 * 100.0
    ok = (m > 0.0) if is_long else (m < 0.0)
    return None if ok else f"MOMENTUM_BREAKOUT_TF_{tf}_BLOCK({m:.2f}%)"


def bb_pullback_filter_block(get_ind, is_long, tf, enabled, long_max, short_min):
    """vec: entry_sig &= ~bb_pullback_gate_vec at TF=FILTER_TF (master + LONG_MAX/SHORT_MIN)."""
    if tf is None or not truthy(enabled):
        return None
    b = _num(get_ind, f"bb_pct_b_{tf}")
    if b is None:
        return None
    bound = _fnum(long_max, 0.30) if is_long else _fnum(short_min, 0.70)
    blocked = (b > bound) if is_long else (b < bound)
    return f"BB_PULLBACK_TF_{tf}_BLOCK(pctB={b:.2f})" if blocked else None


def bb_reclaim_block(get_ind, is_long, tf, label):
    if tf is None:
        return None
    b = _num(get_ind, f"bb_pct_b_{tf}")
    if b is None:
        return None
    ok = (b > 0.5) if is_long else (b < 0.5)
    return None if ok else f"{label}_{tf}_BLOCK(pctB={b:.2f})"


def dc_break_block(get_ind, price, is_long, tf):
    if tf is None or not price or price <= 0:
        return None
    lvl = _num(get_ind, f"dc_high_{tf}_prev" if is_long else f"dc_low_{tf}_prev")
    if lvl is None or lvl <= 0:
        return None
    ok = (price > lvl) if is_long else (price < lvl)
    return None if ok else f"DC_BREAK_TF_{tf}_BLOCK(px{price:.4f}_vs_{lvl:.4f})"


def wt_cross_side_block(get_ind, is_long, tf, label):
    if tf is None:
        return None
    w1 = _num(get_ind, f"wt1_{tf}")
    w2 = _num(get_ind, f"wt2_{tf}")
    if w1 is None or w2 is None or (w1 == 0 and w2 == 0):
        return None
    ok = (w1 > w2) if is_long else (w1 < w2)
    return None if ok else f"{label}_{tf}_BLOCK(wt1={w1:.1f}_wt2={w2:.1f})"


def _pattern_code(raw):
    if raw is None:
        return None
    if isinstance(raw, str):
        return BAR_PATTERN_CODES.get(raw.strip().lower(), 0)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def bar_pattern_side_block(get_ind, is_long, tf):
    """vec bar_pattern_side: bullish code set for LONG / bearish for SHORT; missing key -> pass."""
    if tf is None:
        return None
    try:
        raw = get_ind(f"bar_pattern_{tf}")
    except Exception:
        raw = None
    code = _pattern_code(raw)
    if code is None or code < 0:
        return None
    ok = (code in _BULL_CODES) if is_long else (code in _BEAR_CODES)
    return None if ok else f"BAR_PATTERNS_TF_{tf}_BLOCK(code={code})"


def dc_retest_hold_block(get_ind, price, is_long, tf, master_enabled):
    """vec dc_retest_hold, master-gated by BREAKOUT_RETEST_ARMED_ENABLED (v12 [JSN2] forces OFF otherwise)."""
    if tf is None or not truthy(master_enabled) or not price or price <= 0:
        return None
    lvl = _num(get_ind, f"dc_high_{tf}_prev" if is_long else f"dc_low_{tf}_prev")
    if lvl is None or lvl <= 0:
        return None
    wick = _num(get_ind, f"low_{tf}" if is_long else f"high_{tf}") or 0.0
    if is_long:
        ok = wick > 0 and wick <= lvl and price > lvl
    else:
        ok = wick > 0 and wick >= lvl and price < lvl
    return None if ok else f"BREAKOUT_RETEST_TF_{tf}_BLOCK(px{price:.4f}_wick{wick:.4f}_lvl{lvl:.4f})"


def bb_bounce_filter_block(get_ind, price, is_long, tf):
    """vec FILTER_TF_MAP BB_BOUNCE_ENTRY_TF ('entry','bb_bounce'): long needs low<=bb_lower<close; band missing -> pass."""
    if tf is None:
        return None
    band = _num(get_ind, f"bb_lower_{tf}" if is_long else f"bb_upper_{tf}")
    if band is None or band <= 0:
        return None
    px = float(price or 0.0)
    if is_long:
        lo = _num(get_ind, f"low_{tf}") or 0.0
        ok = lo > 0 and lo <= band and px > band
    else:
        hi = _num(get_ind, f"high_{tf}") or 0.0
        ok = hi > 0 and hi >= band and px < band
    return None if ok else f"BB_BOUNCE_FILTER_TF_{tf}_BLOCK(px{px:.4f}_band{band:.4f})"

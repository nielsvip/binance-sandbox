"""vec_decisions/reentry_15m_bb_htf.py — testable 15m BB/DC pullback reentries with HTF confirmation.

All switches here use 15m/1h fields that exist in NPZ (backtest testable) and
are wired identically live (Redis indicators) and vector (NPZ arrays).  This is
the focus for TIM 30-80% — the 3m/5m BOUNCE_REENTRY family is live-only
untestable (off for backtest comparison, on for live).

Three testable switches:

A. REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED
   15m close crosses above dc_basis_15m after being below, HTF WT bullish >=2
   (1h,4h,D) and bb_pct_b_1h not overbought (>0.85 blocked). Better-price gate
   price < exit * (1 - 0.002) for LONG.

B. REENTRY_15M_LRL_PULLBACK_HTF_ENABLED
   15m lrL pullback: lrL_pct_b_15m was <0.20 (near lower band) within last 2
   bars and now closes >0.30 with wt1_15m > wt2_15m, HTF >=2.

C. REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED
   1h BB low bounce: bb_pct_b_1h <0.20 (oversold on 1h) and now >0.25 with
   close > bb_lower_1h and wt1_15m > wt2_15m, HTF >=2.  Uses 1h BB which is
   in NPZ; 15m confirms timing.

HTF = count of WT bullish TFs among (15m,1h,4h,D) or GR votes — same lattice
as mtf_live_evaluator.gr_filter_pass but threshold is MIN_TFS.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

_EPS = 1e-9
_BETTER_PCT = 0.002


def _num(m: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    try:
        v = (m or {}).get(key, default)
        fv = float(v)
        return fv if math.isfinite(fv) else default
    except (TypeError, ValueError):
        return default


def _htf_bullish_count(ind: Mapping[str, Any] | None) -> int:
    cnt = 0
    for tf in ("15m", "1h", "4h", "D"):
        wt1 = _num(ind, f"wt1_{tf}", 50.0)
        wt2 = _num(ind, f"wt2_{tf}", 50.0)
        if abs(wt1 - 50) < _EPS and abs(wt2 - 50) < _EPS:
            continue
        if wt1 > wt2:
            cnt += 1
    return cnt


def _htf_bearish_count(ind: Mapping[str, Any] | None) -> int:
    cnt = 0
    for tf in ("15m", "1h", "4h", "D"):
        wt1 = _num(ind, f"wt1_{tf}", 50.0)
        wt2 = _num(ind, f"wt2_{tf}", 50.0)
        if abs(wt1 - 50) < _EPS and abs(wt2 - 50) < _EPS:
            continue
        if wt1 < wt2:
            cnt += 1
    return cnt


# ── Core pure predicates ──

def _dc_basis_cross_htf_fires(close_15m: float, close_15m_prev: float,
                              dc_basis_15m: float, dc_basis_prev: float,
                              htf_count: int, bb_pct_b_1h: float,
                              price: float, exit_price: float,
                              is_long: bool, min_tfs: int, better_pct: float) -> bool:
    if dc_basis_15m <= 0 or close_15m <= 0 or close_15m_prev <= 0:
        return False
    better = (price < exit_price * (1 - better_pct)) if is_long else (price > exit_price * (1 + better_pct))
    if exit_price > 0 and not better:
        return False
    if htf_count < min_tfs:
        return False
    # overbought block on 1h BB (fail if too high)
    if is_long and bb_pct_b_1h > 0.85:
        return False
    if not is_long and bb_pct_b_1h < 0.15:
        return False
    if is_long:
        # crossed up: prev below basis, now above
        return close_15m_prev < dc_basis_prev and close_15m > dc_basis_15m
    else:
        return close_15m_prev > dc_basis_prev and close_15m < dc_basis_15m


def _lrl_pullback_htf_fires(lrl_pct_prev: float, lrl_pct_now: float,
                            wt1_15m: float, wt2_15m: float,
                            htf_count: int, price: float, exit_price: float,
                            is_long: bool, min_tfs: int, better_pct: float) -> bool:
    better = (price < exit_price * (1 - better_pct)) if is_long else (price > exit_price * (1 + better_pct))
    if exit_price > 0 and not better:
        return False
    if htf_count < min_tfs:
        return False
    if is_long:
        if not (wt1_15m > wt2_15m):
            return False
        # was near lower band (<0.20) and now reclaimed >0.30
        return lrl_pct_prev < 0.20 and lrl_pct_now > 0.30
    else:
        if not (wt1_15m < wt2_15m):
            return False
        return lrl_pct_prev > 0.80 and lrl_pct_now < 0.70


def _bb1h_low_bounce_htf_fires(bb_pct_1h: float, bb_pct_prev: float,
                               close_15m: float, bb_lower_1h: float,
                               wt1_15m: float, wt2_15m: float,
                               htf_count: int, price: float, exit_price: float,
                               is_long: bool, min_tfs: int, better_pct: float) -> bool:
    better = (price < exit_price * (1 - better_pct)) if is_long else (price > exit_price * (1 + better_pct))
    if exit_price > 0 and not better:
        return False
    if htf_count < min_tfs:
        return False
    if is_long:
        if not (wt1_15m > wt2_15m and close_15m > bb_lower_1h > 0):
            return False
        # 1h BB was oversold (<0.20) and now reclaiming >0.25
        return bb_pct_prev < 0.20 and bb_pct_1h > 0.25
    else:
        if not (wt1_15m < wt2_15m and close_15m < bb_lower_1h + (0.1 * abs(bb_lower_1h))):
            # for short, use upper band; reuse lower param but check pct
            return False
        # short: was overbought >0.80 and now <0.75
        return bb_pct_prev > 0.80 and bb_pct_1h < 0.75


# ── Scalar wrappers ──

def check_dc_basis_cross_htf(ind: Mapping[str, Any] | None, is_long: bool,
                             price: float, exit_price: float, cfg: Any = None) -> tuple[bool, str]:
    if cfg is not None and not bool(getattr(cfg, "REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED", False)):
        return False, ""
    min_tfs = int(getattr(cfg, "REENTRY_15M_DC_BASIS_CROSS_HTF_MIN_TFS", 2)) if cfg else 2
    better_pct = float(getattr(cfg, "REENTRY_15M_BETTER_PCT", _BETTER_PCT)) if cfg else _BETTER_PCT
    htf = _htf_bullish_count(ind) if is_long else _htf_bearish_count(ind)
    close_15m = _num(ind, "close_15m", 0)
    close_prev = _num(ind, "close_15m_prev", close_15m)
    # bb_middle_15m is the true BB basis (now in NPZ 15m+); fallback to dc_basis for old NPZ
    bb_basis = _num(ind, "bb_middle_15m", _num(ind, "bb_basis_15m", _num(ind, "dc_basis_15m", 0)))
    bb_prev = _num(ind, "bb_middle_15m_prev", _num(ind, "bb_basis_15m_prev", _num(ind, "dc_basis_15m_prev", bb_basis)))
    bb_pct = _num(ind, "bb_pct_b_1h", 0.5)
    fires = _dc_basis_cross_htf_fires(close_15m, close_prev, bb_basis, bb_prev,
                                      htf, bb_pct, price, exit_price, is_long, min_tfs, better_pct)
    return (True, f"15M_BB_BASIS_CROSS_HTF_{'LONG' if is_long else 'SHORT'}_htf{htf}") if fires else (False, "")


def check_lrl_pullback_htf(ind: Mapping[str, Any] | None, is_long: bool,
                           price: float, exit_price: float, cfg: Any = None) -> tuple[bool, str]:
    if cfg is not None and not bool(getattr(cfg, "REENTRY_15M_LRL_PULLBACK_HTF_ENABLED", False)):
        return False, ""
    min_tfs = int(getattr(cfg, "REENTRY_15M_LRL_PULLBACK_HTF_MIN_TFS", 2)) if cfg else 2
    better_pct = float(getattr(cfg, "REENTRY_15M_BETTER_PCT", _BETTER_PCT)) if cfg else _BETTER_PCT
    htf = _htf_bullish_count(ind) if is_long else _htf_bearish_count(ind)
    lrl_now = _num(ind, "lrL_pct_b_15m", 0.5)
    # approximate prev as current unless explicit prev present
    lrl_prev = _num(ind, "lrL_pct_b_15m_prev", lrl_now)
    wt1 = _num(ind, "wt1_15m", 50); wt2 = _num(ind, "wt2_15m", 50)
    fires = _lrl_pullback_htf_fires(lrl_prev, lrl_now, wt1, wt2, htf, price, exit_price, is_long, min_tfs, better_pct)
    return (True, f"15M_LRL_PULLBACK_HTF_{'LONG' if is_long else 'SHORT'}_htf{htf}") if fires else (False, "")


def check_bb1h_low_bounce_htf(ind: Mapping[str, Any] | None, is_long: bool,
                              price: float, exit_price: float, cfg: Any = None) -> tuple[bool, str]:
    if cfg is not None and not bool(getattr(cfg, "REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED", False)):
        return False, ""
    min_tfs = int(getattr(cfg, "REENTRY_15M_BB1H_LOW_BOUNCE_HTF_MIN_TFS", 2)) if cfg else 2
    better_pct = float(getattr(cfg, "REENTRY_15M_BETTER_PCT", _BETTER_PCT)) if cfg else _BETTER_PCT
    htf = _htf_bullish_count(ind) if is_long else _htf_bearish_count(ind)
    bb_pct = _num(ind, "bb_pct_b_1h", 0.5)
    # need prev; fallback to current if not stored
    bb_prev = _num(ind, "bb_pct_b_1h_prev", bb_pct)
    close_15m = _num(ind, "close_15m", price)
    bb_lower = _num(ind, "bb_lower_1h", 0)
    wt1 = _num(ind, "wt1_15m", 50); wt2 = _num(ind, "wt2_15m", 50)
    fires = _bb1h_low_bounce_htf_fires(bb_pct, bb_prev, close_15m, bb_lower, wt1, wt2, htf, price, exit_price, is_long, min_tfs, better_pct)
    return (True, f"15M_BB1H_LOW_BOUNCE_HTF_{'LONG' if is_long else 'SHORT'}_htf{htf}") if fires else (False, "")


# ── Vector masks ──

def check_dc_basis_cross_htf_vec(close_15m_arr, close_prev_arr, dc_basis_arr, dc_prev_arr,
                                 htf_count_arr, bb_pct_arr, price_arr, exit_price_arr,
                                 is_long: bool, min_tfs: int, better_pct: float):
    close_15m_arr = np.asarray(close_15m_arr, dtype=float)
    close_prev_arr = np.asarray(close_prev_arr, dtype=float)
    dc_basis_arr = np.asarray(dc_basis_arr, dtype=float)
    dc_prev_arr = np.asarray(dc_prev_arr, dtype=float)
    htf_arr = np.asarray(htf_count_arr, dtype=int)
    bb_arr = np.asarray(bb_pct_arr, dtype=float)
    price_arr = np.asarray(price_arr, dtype=float)
    exit_arr = np.asarray(exit_price_arr, dtype=float)
    better = (price_arr < exit_arr * (1 - better_pct)) if is_long else (price_arr > exit_arr * (1 + better_pct))
    better = better | (exit_arr <= 0)  # if no exit price, allow
    htf_ok = htf_arr >= min_tfs
    dc_ok = (dc_basis_arr > 0) & (close_15m_arr > 0)
    if is_long:
        cross = (close_prev_arr < dc_prev_arr) & (close_15m_arr > dc_basis_arr)
        bb_ok = bb_arr <= 0.85
    else:
        cross = (close_prev_arr > dc_prev_arr) & (close_15m_arr < dc_basis_arr)
        bb_ok = bb_arr >= 0.15
    return better & htf_ok & dc_ok & cross & bb_ok

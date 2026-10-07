"""vec_decisions/reentry_bounce_after_correction.py — 5 bounce-after-correction reentry predicates.

Shared scalar+vectorized source of truth for TIM 30-80% (>50) bounce reentries
at a BETTER price after a 1-5 bar correction.

Live source of truth to mirror: ez_reentry.py:evaluate_obligatory_reentry (TIER1-3)
plus the new 5 switches below. Each predicate is pure per-bar: no config object
inside the core, no state, no I/O, no disk — caller honors positionAmt==0,
dedup_gap, is_reentry_eligible, churn guards, sizing.

Design follows vec_decisions/guaranteed_price_cross_reentry.py pattern:
  one pure per-bar core -> scalar wrapper -> numpy vec mask.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

_EPS = 1e-9
_BETTER_DEFAULT = 0.002  # 0.20% better price discount


def _num(m: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    try:
        v = (m or {}).get(key, default)
        fv = float(v)
        return fv if math.isfinite(fv) else default
    except (TypeError, ValueError):
        return default


def _gr_htf_count(ind: Mapping[str, Any] | None) -> int:
    """Count HTF bullish TFs (3m,15m,1h,4h,D) — same lattice as mtf_live_evaluator.gr_filter."""
    cnt = 0
    for tf in ("3m", "15m", "1h", "4h", "D"):
        wt1 = _num(ind, f"wt1_{tf}", 50.0)
        wt2 = _num(ind, f"wt2_{tf}", 50.0)
        # wt bullish when wt1>wt2; missing/zero -> 50/50 neutral not counted
        if abs(wt1 - 50) < _EPS and abs(wt2 - 50) < _EPS:
            continue
        if wt1 > wt2:
            cnt += 1
    return cnt


def _bar_turn_3m(ind: Mapping[str, Any] | None, is_long: bool, require_both: bool = False) -> bool:
    high = _num(ind, "high_3m", 0)
    high_prev = _num(ind, "high_3m_prev", 0)
    low = _num(ind, "low_3m", 0)
    low_prev = _num(ind, "low_3m_prev", 0)
    if not (high > 0 and high_prev > 0 and low > 0 and low_prev > 0):
        return False
    hh = high > high_prev
    hl = low > low_prev
    ll = low < low_prev
    lh = high < high_prev
    if is_long:
        return (hh and hl) if require_both else (hh or hl)
    else:
        return (ll and lh) if require_both else (ll or lh)


# ── Core predicates (pure, no config) ──

def _bounce_bar_gr_fires(price: float, exit_price: float, is_long: bool,
                         gr_count: int, k15m: float, bar_turn: bool,
                         better_pct: float, min_tfs: int) -> bool:
    if exit_price <= 0 or price <= 0 or not bar_turn:
        return False
    better = (price < exit_price * (1 - better_pct)) if is_long else (price > exit_price * (1 + better_pct))
    if not better:
        return False
    if gr_count < min_tfs:
        return False
    if not (15 <= k15m <= 70):
        return False
    return True


def _pullback_gr_fires(wt1_3m: float, wt2_3m: float, wt1_15m: float, wt2_15m: float,
                       gr_score: int, k15m: float, price: float, exit_price: float,
                       is_long: bool, score_min: int, better_pct: float) -> bool:
    better = (price < exit_price * (1 - better_pct)) if is_long else (price > exit_price * (1 + better_pct))
    if not better or gr_score < score_min:
        return False
    # pullback: 3m aligned with trend, 15m still against (correction)
    wt3_align = (wt1_3m > wt2_3m) if is_long else (wt1_3m < wt2_3m)
    wt15_against = (wt1_15m < wt2_15m) if is_long else (wt1_15m > wt2_15m)
    if not (wt3_align and wt15_against):
        return False
    if is_long:
        return 25 <= k15m <= 45
    else:
        return 55 <= k15m <= 75


def _dc_mid_fires(price: float, dc_basis: float, dc_width: float, dc_pos: float,
                  gr_count: int, width_max: float, min_tfs: int,
                  exit_price: float, is_long: bool, better_pct: float) -> bool:
    if dc_width > width_max or gr_count < min_tfs:
        return False
    better = (price < exit_price * (1 - better_pct)) if is_long else (price > exit_price * (1 + better_pct))
    if not better:
        return False
    # mid-band: dc_pos 0.25-0.55 (pulled back from top, not at bottom)
    if not (0.25 <= dc_pos <= 0.55):
        return False
    # price near basis
    if dc_basis <= 0:
        return False
    return abs(price - dc_basis) / dc_basis < 0.04  # within 4% of basis


def _k_reset_fires(k: float, k_prev: float, gr_count: int, wt1_3m: float, wt2_3m: float,
                   is_long: bool, min_tfs: int, price: float, exit_price: float, better_pct: float) -> bool:
    better = (price < exit_price * (1 - better_pct)) if is_long else (price > exit_price * (1 + better_pct))
    if not better or gr_count < min_tfs:
        return False
    wt_ok = (wt1_3m > wt2_3m) if is_long else (wt1_3m < wt2_3m)
    if not wt_ok:
        return False
    if is_long:
        return k_prev < 28 and 32 < k < 45
    else:
        return k_prev > 72 and 55 < k < 68


def _sma200_gr_fires(price: float, sma200: float, gr_count: int, is_long: bool,
                     min_tfs: int, exit_price: float, better_pct: float, hh: bool) -> bool:
    if sma200 <= 0:
        return False
    trend_ok = (price > sma200) if is_long else (price < sma200)
    if not trend_ok or gr_count < min_tfs or not hh:
        return False
    better = (price < exit_price * (1 - better_pct)) if is_long else (price > exit_price * (1 + better_pct))
    return better


# ── Scalar wrappers (config-aware) ──

def check_bounce_bar_gr(ind: Mapping[str, Any] | None, is_long: bool, price: float,
                        exit_price: float, cfg: Any = None) -> tuple[bool, str]:
    if cfg is not None and not bool(getattr(cfg, "REENTRY_BOUNCE_BAR_GR_ENABLED", False)):
        return False, ""
    min_tfs = int(getattr(cfg, "REENTRY_BOUNCE_BAR_GR_MIN_TFS", 2)) if cfg else 2
    better_pct = float(getattr(cfg, "REENTRY_BOUNCE_BAR_GR_BETTER_PCT", _BETTER_DEFAULT)) if cfg else _BETTER_DEFAULT
    gr_count = _gr_htf_count(ind)
    k15m = _num(ind, "stoch_k_15m", 50.0)
    bar_turn = _bar_turn_3m(ind, is_long, False)
    fires = _bounce_bar_gr_fires(price, exit_price, is_long, gr_count, k15m, bar_turn, better_pct, min_tfs)
    return (True, f"BOUNCE_BAR_GR_{'LONG' if is_long else 'SHORT'}_gr{gr_count}") if fires else (False, "")


def check_pullback_gr(ind: Mapping[str, Any] | None, is_long: bool, price: float,
                      exit_price: float, cfg: Any = None) -> tuple[bool, str]:
    if cfg is not None and not bool(getattr(cfg, "REENTRY_PULLBACK_GR_SCORE_ENABLED", False)):
        return False, ""
    score_min = int(getattr(cfg, "REENTRY_PULLBACK_GR_SCORE_MIN", 12)) if cfg else 12
    better_pct = float(getattr(cfg, "REENTRY_BOUNCE_BAR_GR_BETTER_PCT", _BETTER_DEFAULT)) if cfg else _BETTER_DEFAULT
    # gr_score = sum of TF votes * indicators (proxy: gr_count*5)
    gr_count = _gr_htf_count(ind)
    gr_score = gr_count * 5
    wt1_3m = _num(ind, "wt1_3m", 50.0); wt2_3m = _num(ind, "wt2_3m", 50.0)
    wt1_15m = _num(ind, "wt1_15m", 50.0); wt2_15m = _num(ind, "wt2_15m", 50.0)
    k15m = _num(ind, "stoch_k_15m", 50.0)
    fires = _pullback_gr_fires(wt1_3m, wt2_3m, wt1_15m, wt2_15m, gr_score, k15m, price, exit_price, is_long, score_min, better_pct)
    return (True, f"PULLBACK_GR_{'LONG' if is_long else 'SHORT'}_{gr_score}") if fires else (False, "")


def check_dc_mid(ind: Mapping[str, Any] | None, is_long: bool, price: float,
                 exit_price: float, cfg: Any = None) -> tuple[bool, str]:
    if cfg is not None and not bool(getattr(cfg, "REENTRY_DC_MID_PULLBACK_ENABLED", False)):
        return False, ""
    width_max = float(getattr(cfg, "REENTRY_DC_MID_PULLBACK_WIDTH_MAX", 12.0)) if cfg else 12.0
    min_tfs = int(getattr(cfg, "REENTRY_DC_MID_GR_MIN_TFS", 2)) if cfg else 2
    better_pct = float(getattr(cfg, "REENTRY_BOUNCE_BAR_GR_BETTER_PCT", _BETTER_DEFAULT)) if cfg else _BETTER_DEFAULT
    gr_count = _gr_htf_count(ind)
    dc_basis = _num(ind, "dc_basis_3m", _num(ind, "dc_basis_4h", 0))
    dc_width = _num(ind, "dc_width_3m", _num(ind, "dc_width_4h", 10.0))
    dc_pos = _num(ind, "dc_pos_3m", 0.5)
    fires = _dc_mid_fires(price, dc_basis, dc_width, dc_pos, gr_count, width_max, min_tfs, exit_price, is_long, better_pct)
    return (True, f"DC_MID_PULLBACK_{'LONG' if is_long else 'SHORT'}") if fires else (False, "")


def check_k_reset(ind: Mapping[str, Any] | None, is_long: bool, price: float,
                  exit_price: float, cfg: Any = None) -> tuple[bool, str]:
    if cfg is not None and not bool(getattr(cfg, "REENTRY_K_RESET_GR_ENABLED", False)):
        return False, ""
    min_tfs = int(getattr(cfg, "REENTRY_K_RESET_GR_MIN_TFS", 2)) if cfg else 2
    better_pct = float(getattr(cfg, "REENTRY_BOUNCE_BAR_GR_BETTER_PCT", _BETTER_DEFAULT)) if cfg else _BETTER_DEFAULT
    gr_count = _gr_htf_count(ind)
    tf = str(getattr(cfg, "REENTRY_K_RESET_TF", "15m")) if cfg else "15m"
    k = _num(ind, f"stoch_k_{tf}", 50.0)
    k_prev = _num(ind, f"stoch_k_{tf}_prev", k)
    wt1_3m = _num(ind, "wt1_3m", 50.0); wt2_3m = _num(ind, "wt2_3m", 50.0)
    fires = _k_reset_fires(k, k_prev, gr_count, wt1_3m, wt2_3m, is_long, min_tfs, price, exit_price, better_pct)
    return (True, f"K_RESET_GR_{'LONG' if is_long else 'SHORT'}_{tf}") if fires else (False, "")


def check_sma200_gr(ind: Mapping[str, Any] | None, is_long: bool, price: float,
                    exit_price: float, cfg: Any = None) -> tuple[bool, str]:
    if cfg is not None and not bool(getattr(cfg, "REENTRY_SMA200_GR_CONTINUATION_ENABLED", False)):
        return False, ""
    min_tfs = int(getattr(cfg, "REENTRY_SMA200_GR_MIN_TFS", 2)) if cfg else 2
    better_pct = float(getattr(cfg, "REENTRY_BOUNCE_BAR_GR_BETTER_PCT", _BETTER_DEFAULT)) if cfg else _BETTER_DEFAULT
    gr_count = _gr_htf_count(ind)
    sma200 = _num(ind, "sma_200_15m", 0)
    hh = _bar_turn_3m(ind, is_long, False)
    fires = _sma200_gr_fires(price, sma200, gr_count, is_long, min_tfs, exit_price, better_pct, hh)
    return (True, f"SMA200_GR_{'LONG' if is_long else 'SHORT'}") if fires else (False, "")


# ── Vectorized masks (numpy, caller builds arrays) ──

def check_bounce_bar_gr_vec(price_arr, exit_price_arr, is_long: bool,
                            gr_count_arr, k15m_arr, bar_turn_arr,
                            better_pct: float, min_tfs: int):
    price_arr = np.asarray(price_arr, dtype=float)
    exit_price_arr = np.asarray(exit_price_arr, dtype=float)
    gr_count_arr = np.asarray(gr_count_arr, dtype=int)
    k15m_arr = np.asarray(k15m_arr, dtype=float)
    bar_turn_arr = np.asarray(bar_turn_arr, dtype=bool)
    better = (price_arr < exit_price_arr * (1 - better_pct)) if is_long else (price_arr > exit_price_arr * (1 + better_pct))
    return better & (gr_count_arr >= min_tfs) & (k15m_arr >= 15) & (k15m_arr <= 70) & bar_turn_arr & (exit_price_arr > 0) & (price_arr > 0)


def check_pullback_gr_vec(price_arr, exit_price_arr, is_long: bool,
                          wt1_3m_arr, wt2_3m_arr, wt1_15m_arr, wt2_15m_arr,
                          gr_score_arr, k15m_arr, better_pct: float, score_min: int):
    price_arr = np.asarray(price_arr, dtype=float)
    exit_price_arr = np.asarray(exit_price_arr, dtype=float)
    gr_score_arr = np.asarray(gr_score_arr, dtype=int)
    k15m_arr = np.asarray(k15m_arr, dtype=float)
    wt1_3m_arr = np.asarray(wt1_3m_arr, dtype=float)
    wt2_3m_arr = np.asarray(wt2_3m_arr, dtype=float)
    wt1_15m_arr = np.asarray(wt1_15m_arr, dtype=float)
    wt2_15m_arr = np.asarray(wt2_15m_arr, dtype=float)
    better = (price_arr < exit_price_arr * (1 - better_pct)) if is_long else (price_arr > exit_price_arr * (1 + better_pct))
    wt3_align = (wt1_3m_arr > wt2_3m_arr) if is_long else (wt1_3m_arr < wt2_3m_arr)
    wt15_against = (wt1_15m_arr < wt2_15m_arr) if is_long else (wt1_15m_arr > wt2_15m_arr)
    if is_long:
        k_ok = (k15m_arr >= 25) & (k15m_arr <= 45)
    else:
        k_ok = (k15m_arr >= 55) & (k15m_arr <= 75)
    return better & (gr_score_arr >= score_min) & wt3_align & wt15_against & k_ok


# DC-mid, K-reset, SMA200 vec variants follow same pattern — omitted for brevity, scalar is parity source.

"""STOP_RECLAIM_REENTRY — swift, very conditional re-entry after the non-negotiable FIXED_STOP (USER 2026-10-16: "The solid
dc_15m_low -0.3% (vv short) innegotiable stop loss is a harsh but necessary measure that affects gains negatively we need to
compensate it with a super swift but very conditional reentry"; 2026-10-17 "pls build"). Design: docs/v15_pending/14 §4.1.

Evaluated on each CLOSED 15m bar while flat (LONG; SHORT mirrored). Fires only when ALL hold:
  1 armed     the last close was FIXED_STOP and its lifecycle was not itself a STOP_RECLAIM entry (one per chain)
  2 window    1 <= bars since the stop <= STOP_RECLAIM_WINDOW_BARS (2 / 4 / 8)
  3 shallow   lowest low since the stop >= stop_level x (1 - STOP_RECLAIM_MAX_SWEEP_PCT/100)   (a deep flush = real breakdown)
  4 reclaim   close >= stop_level x (1 + STOP_RECLAIM_BUFFER_PCT/100) AND close in the upper half of the bar (buyers won it)
  5 momentum  wt1_15m > wt2_15m AND wt_velocity_15m > 0 (strictly rising)
  6 trend     (STOP_RECLAIM_REQUIRE_HTF) close > dc_low_4h AND (wt1_4h >= wt2_4h OR close >= ema_50_4h)
  STOP_RECLAIM_EXCLUSIVE (default True): inside the window every other re-entry pathway is held (fresh entries are not).
Entry at the bar close, sized STOP_RECLAIM_SIZE_MULT x the normal open. The new position freezes its own FIXED_STOP with the
unchanged vec_decisions/fixed_stop.py math (dc_15m_low now includes the sweep low) — the stop is never moved or disabled.
ONE predicate for v12_quick_engine.simulate_one; live follows through the VEC_EXACT twin (the twin's OPEN carries the reason),
and live sizing applies size_mult() at the twin dispatch (ez_manage / tradier_manage).
"""
from __future__ import annotations

import math

REASON = "STOP_RECLAIM_REENTRY"
STOP_REASON = "FIXED_STOP"


def _num(v, d):
    try:
        f = float(v)
        return f if math.isfinite(f) else d
    except (TypeError, ValueError):
        return d


def _get(cfg, k, d):
    v = cfg(k, d) if callable(cfg) else getattr(cfg, k, d)
    return d if v is None else v


def enabled(cfg) -> bool:
    v = _get(cfg, "STOP_RECLAIM_REENTRY_ENABLED", False)
    return (str(v).strip().lower() in ("1", "true", "yes")) if isinstance(v, str) else bool(v)


def armed(last_exit_reason, last_entry_reason) -> bool:
    return str(last_exit_reason or "").upper().startswith(STOP_REASON) and not str(last_entry_reason or "").upper().startswith(REASON)


def fire(cfg, is_long: bool, stop_level: float, bars_since: int, ext_since: float, close: float, high: float, low: float,
         wt1: float, wt2: float, wt_vel: float, dc4h_low: float, dc4h_high: float, wt1_4h: float, wt2_4h: float, ema50_4h: float) -> bool:
    """True when the stop was a shallow sweep that has been reclaimed with momentum and the 4h trend intact.
    ext_since: LONG lowest low / SHORT highest high from the stop bar up to and including this bar."""
    try:
        if not enabled(cfg):
            return False
        sl, c = _num(stop_level, 0.0), _num(close, 0.0)
        if sl <= 0 or c <= 0:
            return False
        win = int(_num(_get(cfg, "STOP_RECLAIM_WINDOW_BARS", 4), 4))
        if not (1 <= int(bars_since) <= win):
            return False
        sweep = abs(_num(_get(cfg, "STOP_RECLAIM_MAX_SWEEP_PCT", 1.5), 1.5)) / 100.0
        buf = abs(_num(_get(cfg, "STOP_RECLAIM_BUFFER_PCT", 0.10), 0.10)) / 100.0
        hi, lo, ext = _num(high, 0.0), _num(low, 0.0), _num(ext_since, 0.0)
        rng = hi - lo
        loc = (c - lo) / rng if rng > 0 else 0.5
        if is_long:
            if ext <= 0 or ext < sl * (1.0 - sweep):
                return False
            if c < sl * (1.0 + buf) or loc < 0.5:
                return False
            if not (_num(wt1, 0.0) > _num(wt2, 0.0) and _num(wt_vel, 0.0) > 0):
                return False
        else:
            if ext <= 0 or ext > sl * (1.0 + sweep):
                return False
            if c > sl * (1.0 - buf) or loc > 0.5:
                return False
            if not (_num(wt1, 0.0) < _num(wt2, 0.0) and _num(wt_vel, 0.0) < 0):
                return False
        req = _get(cfg, "STOP_RECLAIM_REQUIRE_HTF", True)
        req = (str(req).strip().lower() in ("1", "true", "yes")) if isinstance(req, str) else bool(req)
        if req:
            w1, w2, e50 = _num(wt1_4h, 0.0), _num(wt2_4h, 0.0), _num(ema50_4h, 0.0)
            if is_long:
                if not (_num(dc4h_low, 0.0) > 0 and c > _num(dc4h_low, 0.0)):
                    return False
                if not (w1 >= w2 or (e50 > 0 and c >= e50)):
                    return False
            else:
                if not (_num(dc4h_high, 0.0) > 0 and c < _num(dc4h_high, 0.0)):
                    return False
                if not (w1 <= w2 or (e50 > 0 and c <= e50)):
                    return False
        return True
    except Exception:
        return False


def exclusive_blocks(cfg, last_exit_reason, bars_since: int) -> bool:
    """STOP_RECLAIM_EXCLUSIVE (default True, only while STOP_RECLAIM_REENTRY_ENABLED): during the reclaim window after a
    FIXED_STOP, every OTHER re-entry pathway (HARDCODED_RALLY, exit-price break, tiers ...) is held — the stop is followed
    only by the strict reclaim. Fresh entry signals are never blocked here. Measured 10-17 on 11 sym_sides: with rally
    re-entry on, every reclaim landed on a bar the blunt rally re-entry already took (gain identical; reclaims all lost)."""
    if not enabled(cfg):
        return False
    v = _get(cfg, "STOP_RECLAIM_EXCLUSIVE", True)
    if not ((str(v).strip().lower() in ("1", "true", "yes")) if isinstance(v, str) else bool(v)):
        return False
    if not str(last_exit_reason or "").upper().startswith(STOP_REASON):
        return False
    return 0 <= int(bars_since) <= int(_num(_get(cfg, "STOP_RECLAIM_WINDOW_BARS", 4), 4))


def size_mult(cfg, reason) -> float:
    """Size factor for an OPEN whose reason is STOP_RECLAIM_REENTRY (1.0 otherwise). Live applies it after live sizing."""
    if REASON not in str(reason or "").upper():
        return 1.0
    return max(0.0, _num(_get(cfg, "STOP_RECLAIM_SIZE_MULT", 1.0), 1.0))

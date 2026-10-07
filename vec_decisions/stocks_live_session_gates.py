"""Stocks live session / hold gates — vec twins (lane D parity, 2026-10-06).

All three are LIVE behaviours at tradier defaults that the vector engine did not model:

1. RTH-only trading. Live tradier_manage acts only Mon-Fri 09:30-16:00 ET
   (execute_now returns MARKET_CLOSED outside, closes dropped outside RTH). The stock
   NPZs carry 04:00-20:00 extended-hours 15m bars (64/day), so the vec traded on ~55-60%
   of bars live never touches. Twin: a bar is actionable iff its 15m label time (bar open,
   ET) is in [09:30, 16:00) on a weekday. Switch STOCKS_RTH_ONLY_ENABLED (default True = live).

2. OPENING_BUFFER_NO_CLOSE_MINUTES (live tradier_manage.in_opening_buffer :8823, config_tradier 30):
   0 <= minutes_since_09:30 < N blocks every evaluate_stop exit, reentry and augment.
   Twin: buffer mask on the bar decision time (= bar label + bar minutes, i.e. bar close).

3. HTF_TREND_VETO_ON_REDUCE (live tradier_manage.execute_now :27754-27776, config_tradier True):
   any REDUCE/CLOSE whose reason lacks a bypass token is REFUSED while the Daily WT still
   supports the side (long wt1_D > wt2_D, short wt1_D < wt2_D); fail-open when either is 0.
"""
from __future__ import annotations

import datetime as _dt

import numpy as np

try:
    from zoneinfo import ZoneInfo as _ZI
    _NY = _ZI("America/New_York")
except Exception:  # pragma: no cover
    _NY = None

# live tradier_manage.py:27758 verbatim
HTF_VETO_BYPASS_TOKENS = ('R1_', 'HEDGE', 'PARTIAL_PROFIT_LOCK', 'EOD_FORCE_FLAT', 'EMERGENCY', 'LIQUIDATION', 'PARABOLIC_EXIT', 'GAIN_EROSION', 'R3_HTF_FLIP', 'R4_STDEV_MACRO', 'STRUCTURAL_RANGE_SHIFT', 'DD_BOUNCE_STOP', 'OVERNIGHT_GAP_HEDGE_REMOVE', 'TAKE_PROFIT', 'HEDGE_FAILED')
# vec reason prefixes that are the SAME live action as a bypass-token reason (vec label differs from the live label)
VEC_REASON_ALIASES = {'PPL_': 'PARTIAL_PROFIT_LOCK'}


def _et_minutes(ts_arr):
    """(weekday[n], minutes_since_midnight_ET[n]) for epoch-second (or ms) timestamps."""
    ts = np.asarray(ts_arr, dtype=float)
    n = len(ts)
    wd = np.full(n, 7, dtype=int)
    mins = np.full(n, -1.0)
    if _NY is None or n == 0:
        return wd, mins
    scale = 1000.0 if np.nanmax(np.abs(ts)) > 1e11 else 1.0
    cache = {}
    for i in range(n):
        t = ts[i]
        if not np.isfinite(t) or t <= 0:
            continue
        k = int(t)
        if k not in cache:
            d = _dt.datetime.fromtimestamp(t / scale, _NY)
            cache[k] = (d.weekday(), d.hour * 60 + d.minute + d.second / 60.0)
        wd[i], mins[i] = cache[k]
    return wd, mins


def rth_mask(ts_label, bar_minutes=15.0):
    """True where the bar (label = bar open time) lies fully inside Mon-Fri 09:30-16:00 ET."""
    wd, m = _et_minutes(ts_label)
    return (wd < 5) & (m >= 570.0) & (m + float(bar_minutes) <= 960.0)


def live_in_rth(weekday, minute_of_day):
    """Scalar live rule (decision instant): Mon-Fri, 09:30 <= t < 16:00 ET."""
    return weekday < 5 and 570.0 <= minute_of_day < 960.0


def opening_buffer_mask(ts_label, buffer_minutes, bar_minutes=15.0):
    """True where the bar's decision instant (label + bar_minutes) is within N min after 09:30 ET."""
    if buffer_minutes is None or float(buffer_minutes) <= 0:
        return np.zeros(len(np.asarray(ts_label)), dtype=bool)
    wd, m = _et_minutes(ts_label)
    since = (m + float(bar_minutes)) - 570.0
    return (wd < 5) & (since >= 0.0) & (since < float(buffer_minutes))


def live_in_opening_buffer(weekday, minute_of_day, buffer_minutes):
    """Scalar replica of tradier_manage.in_opening_buffer (decision instant)."""
    if buffer_minutes <= 0 or weekday >= 5:
        return False
    since = minute_of_day - 570.0
    return 0.0 <= since < float(buffer_minutes)


def daily_wt_supports_mask(wt1_D, wt2_D, is_long):
    """Live: data_ok (both non-zero) and Daily WT on the position's side."""
    w1 = np.asarray(wt1_D, dtype=float)
    w2 = np.asarray(wt2_D, dtype=float)
    ok = (np.abs(w1) > 1e-9) & (np.abs(w2) > 1e-9) & np.isfinite(w1) & np.isfinite(w2)
    return ok & ((w1 > w2) if is_long else (w1 < w2))


def htf_veto_bypassed(reason):
    r = str(reason or '').upper()
    if any(t in r for t in HTF_VETO_BYPASS_TOKENS):
        return True
    return any(r.startswith(p) for p in VEC_REASON_ALIASES)


def htf_veto_blocks(reason, supports):
    """True = the close/reduce is refused (live BLOCKED_HTF_TREND_VETO_ON_REDUCE)."""
    return bool(supports) and not htf_veto_bypassed(reason)


def live_htf_veto_blocks(reason, wt1_D, wt2_D, is_long):
    """Scalar replica of tradier_manage.execute_now :27754-27776 (enabled, is_reduce, not hedge)."""
    up = (reason or '').upper()
    if any(s in up for s in HTF_VETO_BYPASS_TOKENS):
        return False
    w1 = float(wt1_D or 0)
    w2 = float(wt2_D or 0)
    if not (abs(w1) > 1e-9 and abs(w2) > 1e-9):
        return False
    return (is_long and w1 > w2) or ((not is_long) and w1 < w2)


def moc_window_masks(ts_label, bar_minutes=15.0, window_minutes=90.0):
    """GAP_MOC session window from timestamps (replaces `i % 26`, wrong on 64-bar/day extended-hours NPZs).
    in_window: weekday bar whose decision instant (label + bar) lies in (16:00 - window, 16:00] ET.
    at_deadline: the bar whose decision instant is exactly 16:00 ET (last RTH bar)."""
    wd, m = _et_minutes(ts_label)
    t_end = m + float(bar_minutes)
    wk = (wd < 5) & (m >= 0)
    in_window = wk & (t_end > 960.0 - float(window_minutes)) & (t_end <= 960.0)
    at_deadline = wk & (np.abs(t_end - 960.0) < 1e-6)
    return in_window, at_deadline


def trading_day_lookback_start(ts_label, days):
    """Per bar: index of the first bar of the trading date `days-1` distinct ET dates back (window = last `days` dates)."""
    ts = np.asarray(ts_label, dtype=float)
    n = len(ts)
    out = np.zeros(n, dtype=int)
    if _NY is None or n == 0:
        return out
    scale = 1000.0 if np.nanmax(np.abs(ts)) > 1e11 else 1.0
    first_idx = []
    last_date = None
    k = -1
    days = max(1, int(days))
    for i in range(n):
        t = ts[i]
        d = _dt.datetime.fromtimestamp(t / scale, _NY).date() if np.isfinite(t) and t > 0 else last_date
        if d != last_date:
            first_idx.append(i)
            last_date = d
            k += 1
        out[i] = first_idx[max(0, k - days + 1)]
    return out

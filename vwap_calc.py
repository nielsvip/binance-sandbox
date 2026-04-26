"""
vwap_calc.py — Pure-function VWAP calculators for crypto scalping.

Two anchor styles, both crypto-applicable:

1. compute_dc_break_vwap(bars_3m, dc_period_bars)
   Anchored VWAP from the most-recent Donchian channel break. Works without any
   session assumption — the anchor is wherever the market last broke its N-bar
   range. Direction encoded:
     * vwap_dc_long_anchor  = VWAP from last DC-HIGH break (bullish anchor)
     * vwap_dc_short_anchor = VWAP from last DC-LOW break  (bearish anchor)

2. compute_us_rth_vwap(bars_3m, now_ts_utc)
   Anchored at the most-recent US RTH open (13:30 UTC). Crypto+equity correlation
   means the bulk of trading volume happens during US RTH; this VWAP is the
   intraday mean for that volume regime. Falls back to "yesterday's 13:30 UTC"
   if current time is before 13:30 UTC today.

Both are pure: no I/O, no async, no logging. Input = list[dict{timestamp,open,high,low,close,volume}],
output = float (or None if not enough data).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple


def _parse_ts(raw) -> Optional[float]:
    """Returns epoch seconds for an ISO string OR pass-through float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        s = str(raw)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _typical_price(bar: dict) -> float:
    return (float(bar.get("high", 0)) + float(bar.get("low", 0)) + float(bar.get("close", 0))) / 3.0


def _vwap_from(bars: List[dict], start_idx: int) -> Optional[float]:
    if start_idx < 0 or start_idx >= len(bars):
        return None
    sum_pv = 0.0
    sum_v = 0.0
    for b in bars[start_idx:]:
        v = float(b.get("volume", 0) or 0)
        if v <= 0:
            continue
        sum_pv += _typical_price(b) * v
        sum_v += v
    return (sum_pv / sum_v) if sum_v > 0 else None


def compute_dc_break_vwap(bars_3m: List[dict], dc_period_bars: int = 20) -> Tuple[Optional[float], Optional[float], Optional[int], Optional[int]]:
    """Find the most-recent DC-high break (LONG anchor) and DC-low break (SHORT anchor).

    A "break" = current bar's high > prior N-bar high (bullish), or current bar's low < prior N-bar low (bearish).
    Returns (vwap_long_anchor, vwap_short_anchor, long_anchor_bar_idx, short_anchor_bar_idx).
    Either side may be None if no break was found in the bar history.
    """
    n = len(bars_3m)
    if n <= dc_period_bars + 1:
        return (None, None, None, None)
    long_anchor_idx = None
    short_anchor_idx = None
    for i in range(n - 1, dc_period_bars, -1):
        b = bars_3m[i]
        prior = bars_3m[i - dc_period_bars: i]
        if not prior:
            continue
        prior_high = max(float(p.get("high", 0)) for p in prior)
        prior_low = min(float(p.get("low", 0)) for p in prior)
        bh = float(b.get("high", 0))
        bl = float(b.get("low", 0))
        if long_anchor_idx is None and bh > prior_high:
            long_anchor_idx = i
        if short_anchor_idx is None and bl < prior_low:
            short_anchor_idx = i
        if long_anchor_idx is not None and short_anchor_idx is not None:
            break
    vwap_long = _vwap_from(bars_3m, long_anchor_idx) if long_anchor_idx is not None else None
    vwap_short = _vwap_from(bars_3m, short_anchor_idx) if short_anchor_idx is not None else None
    return (vwap_long, vwap_short, long_anchor_idx, short_anchor_idx)


def _last_us_rth_anchor_ts(now_ts_utc: float) -> float:
    """Return epoch seconds of the most-recent 13:30 UTC.
    If now is before today's 13:30 UTC, return yesterday's 13:30 UTC.
    """
    now_dt = datetime.fromtimestamp(now_ts_utc, tz=timezone.utc)
    today_open = now_dt.replace(hour=13, minute=30, second=0, microsecond=0)
    if now_dt < today_open:
        # Use yesterday's open
        from datetime import timedelta
        today_open = today_open - timedelta(days=1)
    return today_open.timestamp()


def compute_us_rth_vwap(bars_3m: List[dict], now_ts_utc: Optional[float] = None) -> Tuple[Optional[float], Optional[int]]:
    """VWAP from the most-recent 13:30 UTC anchor (US equity-market open) to now.
    Returns (vwap, anchor_bar_idx). Either may be None if no bars exist past the anchor.
    """
    if not bars_3m:
        return (None, None)
    if now_ts_utc is None:
        now_ts_utc = datetime.now(timezone.utc).timestamp()
    anchor_ts = _last_us_rth_anchor_ts(now_ts_utc)
    # Find first bar with timestamp >= anchor_ts
    anchor_idx = None
    for i, b in enumerate(bars_3m):
        bt = _parse_ts(b.get("timestamp"))
        if bt is not None and bt >= anchor_ts:
            anchor_idx = i
            break
    if anchor_idx is None:
        return (None, None)
    return (_vwap_from(bars_3m, anchor_idx), anchor_idx)


def compute_atr_3m_percentile(bars_3m: List[dict], lookback: int = 100) -> Optional[float]:
    """Returns the percentile rank (0-100) of the most-recent bar's true range vs the prior `lookback` bars.
    Used to filter dead-vol chop. Returns None if not enough data.
    """
    n = len(bars_3m)
    if n < lookback + 2:
        return None
    trs = []
    for i in range(n - lookback - 1, n):
        cur = bars_3m[i]; prev = bars_3m[i - 1] if i > 0 else cur
        h = float(cur.get("high", 0)); l = float(cur.get("low", 0))
        pc = float(prev.get("close", 0))
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if not trs:
        return None
    last = trs[-1]
    history = sorted(trs[:-1])
    if not history:
        return None
    n_below = sum(1 for x in history if x <= last)
    return 100.0 * n_below / len(history)


def add_vwap_to_indicators(indicators: dict, bars_3m: List[dict], now_ts_utc: Optional[float] = None,
                           dc_period_bars: int = 20) -> dict:
    """Mutates and returns the indicators dict with VWAP + ATR-percentile fields added."""
    vl, vs, li, si = compute_dc_break_vwap(bars_3m, dc_period_bars=dc_period_bars)
    vwap_us, ui = compute_us_rth_vwap(bars_3m, now_ts_utc=now_ts_utc)
    atr_pctl = compute_atr_3m_percentile(bars_3m, lookback=100)
    indicators["vwap_dc_long"] = vl
    indicators["vwap_dc_short"] = vs
    indicators["vwap_dc_long_idx"] = li
    indicators["vwap_dc_short_idx"] = si
    indicators["vwap_us_rth"] = vwap_us
    indicators["vwap_us_rth_idx"] = ui
    indicators["atr_3m_pctl_100"] = atr_pctl
    return indicators

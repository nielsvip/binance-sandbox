#!/usr/bin/env python3
"""npz_cutoffs — venue bar-clock helpers with ZERO import side effects (no sys.path mutation).

Single source of truth for "latest closed bar" cutoffs, shared by the refresh daemon
(tools/npz_bar_refresh, which re-exports these) and the test freshness gate
(tools/opt/v12_pilot.npz_freshness). Split out 2026-10-12: importing npz_bar_refresh
anywhere in an eval process SHADOWED ./cat_side_defaults.py with the stale
./tools/cat_side_defaults.py copy (its sys.path.insert(0, ROOT/"tools")) and broke
every evaluation with "no attribute 'cat_side_of'". This module must NEVER gain
sys.path hacks or heavy imports (stdlib only).
"""
import datetime as dt
import time
from zoneinfo import ZoneInfo

BAR = 900  # 15m
ET = ZoneInfo("America/New_York")
STOCK_CLOSE_GRACE = 30  # s after a 15m bar closes before trusting the Tradier bar (API propagation)

NYSE_HOLIDAYS = frozenset({
    "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-04-02", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    "2028-01-03",
})  # extend yearly; the freshness gate treats these as non-sessions (no bars expected)


def is_crypto_symbol(symbol):
    s = str(symbol).upper()
    return s.endswith("USDT") or s.endswith("USDC")


def last_closed_bar(now=None):
    now = int(now if now is not None else time.time())
    return (now // BAR) * BAR - BAR


def last_closed_stock_bar(now=None):
    """latest 15m bar open (UTC grid — the stock grid is ts%900==0) that is CLOSED incl. Tradier propagation grace."""
    now = int(now if now is not None else time.time())
    return ((now - STOCK_CLOSE_GRACE) // BAR) * BAR - BAR


def last_session_final_bar(now=None):
    """most recent fully-closed weekday 19:45 ET bar (ext close), epoch; 0 when none in range (clock sanity).
    Out-of-gate catch-up uses this (not an hour threshold): any stock older than the last closed session gets fetched."""
    now = now if now is not None else dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    ts_now = now.timestamp()
    et_now = now.astimezone(ET)
    for back in range(10):
        d = (et_now - dt.timedelta(days=back)).date()
        if d.weekday() >= 5:
            continue
        cand = int(dt.datetime(d.year, d.month, d.day, 19, 45, tzinfo=ET).timestamp())
        if cand + BAR + STOCK_CLOSE_GRACE <= ts_now:
            return cand
    return 0


# USER LAW 2026-10-20 "all new calculations calculate up to the latest kline bar, max 15m old in the npz": ONE number for every layer.
# npz_age_min = minutes between the latest CLOSED bar's open and the NPZ's own last bar open (0 = current, 15 = one bar behind).
# Hard limit 20 min = one bar behind is tolerated (the closing bar needs minutes to be fetched, pushed and installed), two bars is not.
# Same definition as v15_pilot.npz_age_stamp and its V15_NPZ_MAX_AGE_MIN. Was 2 bars crypto (30 min) / 3 bars in v12_pilot.npz_freshness.
import os as _os
try:
    MAX_NPZ_AGE_MIN = float(_os.environ.get("V15_NPZ_MAX_AGE_MIN", "20") or 20)
except ValueError:
    MAX_NPZ_AGE_MIN = 20.0
if MAX_NPZ_AGE_MIN <= 0:
    MAX_NPZ_AGE_MIN = 20.0
TAIL_GRACE_BARS_CRYPTO = int(MAX_NPZ_AGE_MIN * 60 // BAR)  # 20 min -> 1 bar
TAIL_GRACE_BARS_STOCKS = int(MAX_NPZ_AGE_MIN * 60 // BAR)  # 20 min -> 1 bar


def npz_age_min(last_ts, venue, now=None):
    """minutes the NPZ's last bar (epoch s) is behind the latest closed bar (crypto) / last RTH close (stocks); None when no cutoff."""
    now_ts, now_dt = _now_split(now)
    cut = last_closed_bar(now_ts) if str(venue).lower() == "crypto" else last_rth_close_bar(now_dt)
    if not cut:
        return None
    return round(max(0.0, (float(cut) - float(last_ts)) / 60.0), 1)


def _now_split(now):
    """(epoch_int, aware_utc_datetime) from None/epoch/datetime."""
    if now is None:
        now_dt = dt.datetime.now(dt.timezone.utc)
        return int(now_dt.timestamp()), now_dt
    if isinstance(now, dt.datetime):
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=dt.timezone.utc)
        return int(now_dt.timestamp()), now_dt
    now_dt = dt.datetime.fromtimestamp(float(now), tz=dt.timezone.utc)
    return int(float(now)), now_dt


def tail_currency(tail_ts, venue, now=None):
    """USER 2026-10-12 dispatch gate: is this NPZ tail complete to the latest bar? Pure (unit-tested).
    tail_ts = last NPZ timestamp in epoch SECONDS; venue = 'crypto'|'stocks'. Returns (ok, detail).
    crypto: tail within TAIL_GRACE_BARS_CRYPTO of the last closed 15m bar. stocks: tail within
    TAIL_GRACE_BARS_STOCKS of last_rth_close_bar (RTH-session aware: weekends/holidays expect no new bars)."""
    try:
        tail = float(tail_ts)
    except Exception:
        return (False, "bad-tail-ts")
    now_ts, now_dt = _now_split(now)
    if str(venue).lower() == "crypto":
        cutoff = last_closed_bar(now_ts)
        if not cutoff:
            return (False, "no-cutoff")
        lag = (cutoff - tail) / BAR
        if lag <= TAIL_GRACE_BARS_CRYPTO:
            return (True, f"lag{lag:.1f}bars")
        return (False, f"tail {lag:.1f} bars behind latest closed 15m")
    cutoff = last_rth_close_bar(now_dt)
    if not cutoff:
        return (False, "no-cutoff")
    if tail >= cutoff - TAIL_GRACE_BARS_STOCKS * BAR:
        ahead_min = (tail - cutoff) / 60.0
        return (True, f"{ahead_min:+.0f}min vs RTH close")
    behind_min = (cutoff - tail) / 60.0
    return (False, f"tail {behind_min:.0f}min behind RTH close")


def last_rth_close_bar(now=None):
    """most recent RTH-session 15m-bar close, epoch; 0 when none in range (clock sanity). RTH = 9:30-16:00 ET
    on non-holiday weekdays. Weekend/pre-open/holiday -> previous session's 16:00 ET close; live RTH -> latest
    closed 15m bar; post-close -> today's 16:00 ET. THE freshness-gate cutoff for stocks (v12_pilot.npz_freshness):
    rebuilt NPZs carry RTH bars, so the ext 19:45 close is the wrong bar to demand."""
    now = now if now is not None else dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    ts_now = now.timestamp()
    et_now = now.astimezone(ET)
    mm = et_now.hour * 60 + et_now.minute
    if et_now.weekday() < 5 and et_now.date().isoformat() not in NYSE_HOLIDAYS and 570 <= mm < 960:
        return (int(ts_now) // BAR) * BAR - BAR
    for back in range(12):
        d = (et_now - dt.timedelta(days=back)).date()
        if d.weekday() >= 5 or d.isoformat() in NYSE_HOLIDAYS:
            continue
        cand = int(dt.datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET).timestamp())
        if cand + BAR <= ts_now:
            return cand
    return 0

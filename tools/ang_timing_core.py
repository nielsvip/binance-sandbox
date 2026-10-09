"""ANG entry-timing core — breakout/rebound/top states shared by rankings + tests.

User 2026-10-09: winners must not only buy the tops — buy breakouts with
tight stops, insist through the pullback, full entry on the rebound.
Rankings computes these states per sym_side from 15m bars and publishes
data/ranking_entry_timing.json; ez_manage holds extended-top entries and
sizes rebound entries full. Ranking LISTS are untouched (respected).
"""

import numpy as np

FRESH = "BREAKOUT_FRESH"
REBOUND = "REBOUND_GO"
EXTENDED = "EXTENDED_TOP"
FAILED = "FAILED"
NONE = "NONE"


def _atr14(highs, lows, closes):
    n = len(closes)
    start = max(1, n - 14)
    trs = []
    for j in range(start, n):
        trs.append(
            max(
                highs[j] - lows[j],
                abs(highs[j] - closes[j - 1]),
                abs(lows[j] - closes[j - 1]),
            )
        )
    return float(sum(trs) / len(trs)) if trs else 0.0


def long_state(
    closes,
    highs,
    lows,
    lookback=20,
    fresh_bars=3,
    tight_atr=0.5,
    hold_bars=12,
    hold_tol_atr=0.25,
):
    """Classify LONG timing from oldest->newest 15m bars. Returns (state, level, ext_atr, bars_since)."""
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    n = len(closes)
    if n < lookback + hold_bars + 2:
        return NONE, 0.0, 0.0, -1
    atr = _atr14(highs, lows, closes)
    if not atr > 0:
        return NONE, 0.0, 0.0, -1
    px = float(closes[-1])
    prev = float(closes[-2])
    broke_idx = -1
    for i in range(n - hold_bars - 1, n):
        lvl = float(np.max(highs[max(0, i - lookback) : i]))
        if float(closes[i]) > lvl:
            broke_idx = i
            break
    if broke_idx < 0:
        return NONE, 0.0, 0.0, -1
    level = float(np.max(highs[max(0, broke_idx - lookback) : broke_idx]))
    if level == 0.0 or not np.isfinite(level):
        return NONE, 0.0, 0.0, -1
    bars_since = int(n - 1 - broke_idx)
    ext = (px - level) / atr
    if px < level - hold_tol_atr * atr:
        return FAILED, level, ext, bars_since
    if bars_since <= fresh_bars and ext <= tight_atr and px > level:
        return FRESH, level, ext, bars_since
    seg_low = float(np.min(lows[broke_idx:]))
    seg_close_min = float(np.min(closes[broke_idx:]))
    held = seg_low >= level - hold_tol_atr * atr
    pulled_back = seg_close_min <= level + tight_atr * atr
    resuming = px >= level and px > prev
    if held and pulled_back and resuming and bars_since > fresh_bars:
        return REBOUND, level, ext, bars_since
    if ext > tight_atr and px > level:
        return EXTENDED, level, ext, bars_since
    if px > level:
        return FRESH, level, ext, bars_since
    return NONE, level, ext, bars_since


def short_state(closes, highs, lows, **kw):
    """Mirror of long_state for SHORT (invert the tape)."""
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    st, lvl, ext, bars = long_state(-closes, -lows, -highs, **kw)
    return st, (-lvl if lvl else 0.0), ext, bars


def should_timing_hold(state):
    """Entry-hold contract: ONLY FAILED holds (never average into a lost
    level). EXTENDED breakouts are the biggest gains — they ENTER with tight
    stops + mandatory reentries, they are never held."""
    return state == FAILED


def should_timing_trim(state, gain_pct, ext_atr, min_gain_pct=1.0, min_ext_atr=2.0):
    """Exit-timing decision: trim ONLY at a profit. EXTENDED needs high
    extension; FAILED (level lost) trims on profit alone before it erodes.
    NEVER True at a loss — loss exits stay with technicals/hedges."""
    try:
        gain = float(gain_pct)
        ext = float(ext_atr)
    except (TypeError, ValueError):
        return False
    if not gain >= float(min_gain_pct):
        return False
    if state == EXTENDED:
        return ext >= float(min_ext_atr)
    if state == FAILED:
        return True
    return False

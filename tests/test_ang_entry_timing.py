"""ANG entry timing — fresh breakouts enter, extended tops wait, rebounds go full."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import ang_timing_core as T


def _bars(closes, spread=0.5):
    closes = list(closes)
    highs = [c + spread / 2 for c in closes]
    lows = [c - spread / 2 for c in closes]
    return closes, highs, lows


def test_fresh_breakout_enters():
    closes = [100.0] * 12 + [100.4, 100.35]
    c, h, l = _bars(closes)
    st, level, ext, bars = T.long_state(
        c, h, l, lookback=5, fresh_bars=2, tight_atr=0.5, hold_bars=6, hold_tol_atr=0.25
    )
    assert st == T.FRESH, (st, level, ext, bars)
    assert bars <= 2 and ext <= 0.5


def test_extended_top_waits():
    closes = [100.0] * 5 + [101.2, 102, 103, 104, 105, 106, 107, 108, 109]
    c, h, l = _bars(closes, spread=0.6)
    st, level, ext, bars = T.long_state(
        c, h, l, lookback=5, fresh_bars=2, tight_atr=0.5, hold_bars=6, hold_tol_atr=0.25
    )
    assert st == T.EXTENDED, (st, level, ext, bars)


def test_rebound_goes_full():
    closes = [100.0] * 10 + [101.2, 100.8, 100.6, 100.7, 100.9, 101.0, 101.3, 101.4, 101.8, 102.0, 102.2, 102.5]
    c, h, l = _bars(closes, spread=0.5)
    st, level, ext, bars = T.long_state(
        c, h, l, lookback=10, fresh_bars=2, tight_atr=0.5, hold_bars=8, hold_tol_atr=0.25
    )
    assert st == T.REBOUND, (st, level, ext, bars)


def test_failed_breakout_holds():
    closes = [100.0] * 7 + [101.2, 100.8, 100.4, 100.0, 99.6, 99.2, 98.8, 98.4, 98.0]
    c, h, l = _bars(closes)
    st, level, ext, bars = T.long_state(
        c, h, l, lookback=5, fresh_bars=2, tight_atr=0.5, hold_bars=8, hold_tol_atr=0.25
    )
    assert st == T.FAILED, (st, level, ext, bars)


def test_chop_is_none():
    closes = [100.0] * 16
    c, h, l = _bars(closes)
    st, _, _, _ = T.long_state(
        c, h, l, lookback=5, fresh_bars=2, tight_atr=0.5, hold_bars=6, hold_tol_atr=0.25
    )
    assert st == T.NONE


def test_short_mirrors_long():
    closes = [100.0] * 12 + [99.6, 99.65]
    c, h, l = _bars(closes)
    st, level, ext, bars = T.short_state(
        c, h, l, lookback=5, fresh_bars=2, tight_atr=0.5, hold_bars=6, hold_tol_atr=0.25
    )
    assert st == T.FRESH, (st, level, ext, bars)
    assert level < 0


def test_too_few_bars_is_none():
    c, h, l = _bars([100.0] * 10)
    st, _, _, _ = T.long_state(c, h, l)
    assert st == T.NONE

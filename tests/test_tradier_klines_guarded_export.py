"""tradier_klines.export_atomic must never erase an S1 kline (USER 2026-10-16).

`refresh_daily` fetches Tradier daily history for a window, filters `volume > 0`, and hands the frame to
`export_atomic(..., replace_from=<window start>)`. That branch DROPS every existing row at or after the window start
and keeps only what the response returned — so a reply with fewer bars in the window deletes history outright.
Measured on s1 at 21:16Z 2026-10-15: `MSTR_D.json` 5560 -> 5552 rows and `IBIT_D.json` 1852 -> 1844, spans unchanged
(3699 d / 1008 d), i.e. 8 days each; 29 such events since 10-13, ~1500 file-shrinks in total. `klines_guard
--tripwire` restored every one within the minute, which is why nothing was permanently lost and also why
re-downloading the same history never catches up. klines_guard's docstring requires every canonical-cache writer to
go through `guarded_write`; this writer never did.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tradier_klines as TK  # noqa: E402


def rows(n, start="2016-01-04", vol=100.0):
    """n daily bars as the cache stores them."""
    idx = pd.bdate_range(start, periods=n, tz="UTC")
    return [{"timestamp": t.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0, "volume": vol} for t in idx]


def frame_from(recs):
    df = pd.DataFrame(recs)
    df.index = pd.DatetimeIndex(pd.to_datetime(df["timestamp"], format="mixed", utc=True))
    return df[["open", "high", "low", "close", "volume"]]


def test_shrinking_window_replace_is_refused_and_the_file_is_untouched(tmp_path, monkeypatch):
    monkeypatch.delenv("TRADIER_KLINES_ALLOW_SHRINK", raising=False)
    p = tmp_path / "MSTR_D.json"
    existing = rows(2600)                      # ~10 years -> well over the guard's 400-day floor
    p.write_text(json.dumps(existing))
    window_start = existing[-9]["timestamp"]   # the fresh reply covers the last 9 bars ...
    fresh = frame_from(existing[-9:-8])        # ... but only returns 1 of them (the `volume > 0` filter dropped 8)
    assert TK.export_atomic(fresh, p, replace_from=window_start) is False
    assert json.loads(p.read_text()) == existing, "a refused write must leave every byte in place"


def test_correcting_the_same_bars_still_writes(tmp_path, monkeypatch):
    """The window replace exists to CORRECT mislabelled bars — same count, new values, must still land."""
    monkeypatch.delenv("TRADIER_KLINES_ALLOW_SHRINK", raising=False)
    p = tmp_path / "MSTR_D.json"
    existing = rows(2600)
    p.write_text(json.dumps(existing))
    corrected = [dict(r, close=42.0) for r in existing[-9:]]
    assert TK.export_atomic(frame_from(corrected), p, replace_from=existing[-9]["timestamp"]) is True
    out = json.loads(p.read_text())
    assert len(out) == len(existing)
    assert out[-1]["close"] == 42.0 and out[0] == existing[0]


def test_appending_new_bars_still_writes(tmp_path, monkeypatch):
    monkeypatch.delenv("TRADIER_KLINES_ALLOW_SHRINK", raising=False)
    p = tmp_path / "AAA_15m.json"
    existing = rows(500)
    p.write_text(json.dumps(existing))
    more = rows(503)[-3:]
    assert TK.export_atomic(frame_from(more), p) is True
    assert len(json.loads(p.read_text())) == 503


def test_escape_hatch_restores_the_old_unguarded_write(tmp_path, monkeypatch):
    """A deliberate repair pass must still be possible — explicitly, never by default."""
    monkeypatch.setenv("TRADIER_KLINES_ALLOW_SHRINK", "1")
    p = tmp_path / "MSTR_D.json"
    existing = rows(2600)
    p.write_text(json.dumps(existing))
    fresh = frame_from(existing[-9:-8])
    assert TK.export_atomic(fresh, p, replace_from=existing[-9]["timestamp"]) is True
    assert len(json.loads(p.read_text())) == 2592


def test_unreadable_large_cache_is_still_never_replaced(tmp_path):
    p = tmp_path / "BBB_D.json"
    p.write_text("x" * 2000)
    assert TK.export_atomic(frame_from(rows(5)), p) is False
    assert p.read_text() == "x" * 2000

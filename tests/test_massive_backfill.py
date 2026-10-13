"""Massive stock backfill — session grid, row format, old-only merge."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import massive_backfill_15m as M


def test_grid_31_slots():
    assert len(M.GRID) == 31
    assert M.GRID[0] == "12:30" and M.GRID[-1] == "20:00"


def test_in_session():
    assert M.in_session("2026-07-13T13:30:00.000000Z") is True  # Mon RTH
    assert M.in_session("2026-07-13T11:00:00.000000Z") is False  # pre-market
    assert M.in_session("2026-07-13T23:30:00.000000Z") is False  # after-hours
    assert M.in_session("2026-07-12T15:00:00.000000Z") is False  # Sunday
    assert M.in_session("2026-07-13T12:30:00.000000Z") is True  # grid edge
    assert M.in_session("2026-07-13T20:00:00.000000Z") is True  # grid edge


def test_to_row():
    r = M.to_row({"t": 1783940400000, "o": 17.42, "h": 17.5, "l": 17.4, "c": 17.42, "v": 300})
    assert r["timestamp"] == "2026-07-13T11:00:00.000000Z"
    assert r["close"] == 17.42 and r["volume"] == 300.0


def test_backfill_old_only(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "ROOT", tmp_path)
    d = tmp_path / "klines_cache_backtest" / "tradier"
    d.mkdir(parents=True)
    old_ts = "2026-07-13T12:30:00.000000Z"
    (d / "T_15m.json").write_text(json.dumps([{"timestamp": old_ts, "open": 1, "high": 1, "low": 1, "close": 10.0, "volume": 5}]))
    import datetime
    def fake_fetch(sym, frm, to):
        assert frm == "2025-08-01"
        return [{"t": int(datetime.datetime(2026, 7, 10, 15, 30, tzinfo=datetime.timezone.utc).timestamp() * 1000), "o": 9, "h": 9, "l": 9, "c": 9.0, "v": 1},
                {"t": int(datetime.datetime(2026, 7, 13, 12, 30, tzinfo=datetime.timezone.utc).timestamp() * 1000), "o": 1, "h": 1, "l": 1, "c": 999.0, "v": 1}]
    monkeypatch.setattr(M, "fetch", fake_fetch)
    r = M.backfill("T", "2025-08-01", True)
    assert r["action"] == "written" and r["added"] == 1, r
    rows = json.loads((d / "T_15m.json").read_text())
    assert len(rows) == 2 and rows[0]["timestamp"] < old_ts
    assert rows[1]["close"] == 10.0  # existing wins on overlap
    r = M.backfill("T", "2025-08-01", False)
    assert r["action"] == "dry" and r["would_add"] == 0

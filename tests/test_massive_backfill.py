"""Massive stock backfill — session grid, row format, old-only merge."""
import json
import sys

import pytest
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


def _mk(tmp_path, monkeypatch, head_day="2026-07-13"):
    monkeypatch.setattr(M, "ROOT", tmp_path)
    d = tmp_path / "klines_cache_backtest" / "tradier"
    d.mkdir(parents=True, exist_ok=True)
    (d / "T_15m.json").write_text(json.dumps([{"timestamp": f"{head_day}T12:30:00.000000Z", "open": 1, "high": 1, "low": 1, "close": 10.0, "volume": 5}]))
    return d


def _bars(first_day, n_days):
    """One 13:30Z bar per weekday, n_days of them, starting first_day."""
    import datetime
    out, day = [], datetime.date.fromisoformat(first_day)
    while len(out) < n_days:
        if day.weekday() < 5:
            t = datetime.datetime(day.year, day.month, day.day, 13, 30, tzinfo=datetime.timezone.utc)
            out.append({"t": int(t.timestamp() * 1000), "o": 1, "h": 1, "l": 1, "c": 1.0, "v": 1})
        day += datetime.timedelta(days=1)
    return out


def test_pagination_walks_forward_to_the_cache_head(tmp_path, monkeypatch):
    """USER 2026-10-16: one request is ONE page, and it is not the window asked for — measured for KGC,
    from=2023-01-01/to=2026-07-13 replied 2024-10-15..2025-04-01. A single page leaves a 15-month hole AND returns
    the same bars on every later run, so the symbol is re-downloaded daily and never advances."""
    d = _mk(tmp_path, monkeypatch)
    calls = []

    def fake_fetch(sym, frm, to):
        calls.append(frm)
        if frm <= "2026-01-01":
            return _bars("2026-01-05", 20)          # page 1 (provider clamps to its own start)
        if frm <= "2026-02-03":
            return _bars("2026-02-03", 20)          # page 2, continuing
        return []                                    # exhausted

    monkeypatch.setattr(M, "fetch", fake_fetch)
    monkeypatch.setattr(M.time, "sleep", lambda *_: None)
    r = M.backfill("T", "2024-01-01", True, max_pages=6)
    assert r["action"] == "written" and r["pages"] >= 2, r
    assert r["added"] == 40, r
    assert len(calls) >= 2 and calls[0] == "2024-01-01" and calls[1] > calls[0], calls
    rows = json.loads((d / "T_15m.json").read_text())
    assert rows[0]["timestamp"][:10] == "2026-01-05" and len(rows) == 41


def test_remaining_hole_is_reported_not_hidden(tmp_path, monkeypatch):
    """A gap between the backfilled block and the cache head makes the stock NPZ gap guard refuse the symbol, so the
    seam must be stated rather than silently created."""
    _mk(tmp_path, monkeypatch)
    monkeypatch.setattr(M, "fetch", lambda sym, frm, to: _bars("2025-01-06", 10) if frm <= "2025-01-06" else [])
    monkeypatch.setattr(M.time, "sleep", lambda *_: None)
    r = M.backfill("T", "2024-01-01", False, max_pages=4)
    assert r["action"] == "dry" and r["would_add"] == 10
    assert r["hole_days"] and r["hole_days"] > 500, r


def test_a_provider_that_does_not_advance_stops_instead_of_spinning(tmp_path, monkeypatch):
    _mk(tmp_path, monkeypatch)
    monkeypatch.setattr(M, "fetch", lambda sym, frm, to: _bars("2025-01-06", 5))  # always the same page
    monkeypatch.setattr(M.time, "sleep", lambda *_: None)
    r = M.backfill("T", "2024-01-01", False, max_pages=50)
    assert r["pages"] <= 2, r


def test_symbol_already_at_the_provider_start_costs_no_requests(tmp_path, monkeypatch, capsys):
    """The daily schedule must go quiet once a symbol is caught up — not re-fetch every page to learn nothing."""
    import massive_backfill_15m as MM
    d = _mk(tmp_path, monkeypatch, head_day="2024-10-15")
    called = []
    monkeypatch.setattr(MM, "fetch", lambda *a, **k: called.append(a) or [])
    monkeypatch.setattr(sys, "argv", ["massive_backfill_15m.py", "--symbols", "T"])
    MM.main()
    out = capsys.readouterr().out
    assert '"action": "at-provider-start"' in out, out
    assert called == [], "a caught-up symbol must not hit the API at all"


def test_transient_403_is_retried_like_429(monkeypatch):
    """Measured 2026-10-16: 13 symbols took HTTPError 403 in one batch and a plain retry of ADP/CME returned
    1256/1157 bars. Raising on the first 403 dropped them for the night."""
    import urllib.error
    import massive_backfill_15m as MM
    calls = {"n": 0}

    class _Resp:
        def read(self): return json.dumps({"results": [{"t": 1783940400000, "o": 1, "h": 1, "l": 1, "c": 1.0, "v": 1}]}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def flaky(req, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        return _Resp()

    monkeypatch.setattr(MM, "api_key", lambda: "k")
    monkeypatch.setattr(MM.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(MM.time, "sleep", lambda *_: None)
    assert len(MM.fetch("ADP", "2025-01-01", "2025-03-01")) == 1
    assert calls["n"] == 2


def test_permanent_403_still_raises(monkeypatch):
    import urllib.error
    import massive_backfill_15m as MM
    monkeypatch.setattr(MM, "api_key", lambda: "k")
    monkeypatch.setattr(MM.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(urllib.error.HTTPError("u", 403, "Forbidden", {}, None)))
    monkeypatch.setattr(MM.time, "sleep", lambda *_: None)
    with pytest.raises(urllib.error.HTTPError):
        MM.fetch("NOPE", "2025-01-01", "2025-03-01")


def _et_rows(day, slots):
    """rows for one ET session at the given ET HH:MM slots."""
    import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    out = []
    for s in slots:
        loc = datetime.datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), int(s[:2]), int(s[3:]), tzinfo=et)
        u = loc.astimezone(datetime.timezone.utc)
        out.append({"timestamp": u.strftime("%Y-%m-%dT%H:%M:%S.000000Z"), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    return out


def _full_core():
    return [f"{h:02d}:{m:02d}" for h in range(9, 17) for m in (0, 15, 30, 45) if (h, m) >= (9, 30) and (h, m) <= (16, 0)]


def test_winter_session_is_measured_in_eastern_time():
    """USER 2026-10-16: in_session matched the EDT grid against the raw UTC clock, so from November to March it kept
    07:30-15:00 ET and threw away 15:15-16:00 ET — the close — on every single session. Measured on U and KGC."""
    assert M.in_session("2025-01-15T20:45:00.000000Z") is True    # 15:45 ET, regular hours
    assert M.in_session("2025-01-15T21:00:00.000000Z") is True    # 16:00 ET close print
    assert M.in_session("2025-01-15T12:30:00.000000Z") is False   # 07:30 ET, before the cache's own 08:30 convention
    assert M.in_session("2025-07-16T19:45:00.000000Z") is True    # 15:45 ET in summer
    assert M.in_session("2025-07-16T20:45:00.000000Z") is False   # 16:45 ET in summer, after the close


def test_incomplete_sessions_finds_the_short_winter_tail():
    rows = _et_rows("2025-01-15", ["07:30", "07:45"] + [s for s in _full_core() if s <= "15:00"])
    rows += _et_rows("2025-01-16", _full_core())
    bad, early = M.incomplete_sessions(rows)
    assert [d for d, _ in bad] == ["2025-01-15"] and early == []
    assert dict(bad)["2025-01-15"] == 4, bad


def test_a_half_day_is_not_chased_forever():
    """~3 early closes a year genuinely end at 13:00 ET; reporting them as holes would make every file unfixable."""
    rows = _et_rows("2025-11-28", [s for s in _full_core() if s <= "13:00"])
    bad, early = M.incomplete_sessions(rows)
    assert bad == [] and early == ["2025-11-28"]


def test_fill_holes_inserts_only_the_missing_bars(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "ROOT", tmp_path)
    d = tmp_path / "klines_cache_backtest" / "tradier"
    d.mkdir(parents=True)
    short = _et_rows("2025-01-15", [s for s in _full_core() if s <= "15:00"])
    full = _et_rows("2025-01-15", _full_core())
    (d / "T_15m.json").write_text(json.dumps(short))
    calls = []

    def fake_fetch(sym, frm, to):
        calls.append((frm, to))
        return [{"t": int(__import__("datetime").datetime.strptime(r["timestamp"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000), "o": 2, "h": 2, "l": 2, "c": 2.0, "v": 2} for r in full]

    monkeypatch.setattr(M, "fetch", fake_fetch)
    monkeypatch.setattr(M.time, "sleep", lambda *_: None)
    r = M.fill_holes("T", True)
    assert r["action"] == "filled" and r["added"] == 4, r
    assert r["short_sessions_before"] == 1 and r["short_sessions_after"] == 0
    rows = json.loads((d / "T_15m.json").read_text())
    assert len(rows) == len(short) + 4
    assert [x["close"] for x in rows if x["timestamp"] <= short[-1]["timestamp"]].count(1.0) == len(short), "existing bars must not be overwritten"
    assert len(calls) == 1, "one contiguous block must cost one request"


def test_fill_holes_is_a_noop_on_a_complete_file(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "ROOT", tmp_path)
    d = tmp_path / "klines_cache_backtest" / "tradier"
    d.mkdir(parents=True)
    (d / "T_15m.json").write_text(json.dumps(_et_rows("2025-01-15", _full_core())))
    monkeypatch.setattr(M, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    assert M.fill_holes("T", True)["action"] == "complete"


def test_fill_holes_groups_blocks_into_few_windows():
    days = ["2025-01-02", "2025-01-03", "2025-01-06", "2026-01-05", "2026-01-06"]
    assert M.windows(days) == [("2025-01-02", "2025-01-06"), ("2026-01-05", "2026-01-06")]


def test_one_bad_window_does_not_lose_the_symbol(tmp_path, monkeypatch):
    """AAPL 2026-10-16: the first window took a 403 and the symbol was abandoned with 12 windows unasked — the
    never-catch-up loop one level down. A failed window is skipped, the rest still land."""
    import urllib.error
    monkeypatch.setattr(M, "ROOT", tmp_path)
    d = tmp_path / "klines_cache_backtest" / "tradier"
    d.mkdir(parents=True)
    full_2025 = _et_rows("2025-01-15", _full_core())
    full_2026 = _et_rows("2026-01-14", _full_core())
    short = [r for r in full_2025 if r["timestamp"] <= full_2025[-5]["timestamp"]] + [r for r in full_2026 if r["timestamp"] <= full_2026[-5]["timestamp"]]
    (d / "T_15m.json").write_text(json.dumps(short))
    seen = []

    def flaky(sym, frm, to):
        seen.append(frm)
        if frm.startswith("2025"):
            raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        import datetime
        return [{"t": int(datetime.datetime.strptime(r["timestamp"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000), "o": 2, "h": 2, "l": 2, "c": 2.0, "v": 2} for r in full_2026]

    monkeypatch.setattr(M, "fetch", flaky)
    monkeypatch.setattr(M.time, "sleep", lambda *_: None)
    r = M.fill_holes("T", True)
    assert len(seen) == 2, seen
    assert r["action"] == "filled" and r["added"] == 4 and r["window_errors"] == 1, r


def test_a_session_the_provider_cannot_fill_goes_quiet(tmp_path, monkeypatch):
    """BMNR 2026-10-16: 7 clean fetches, ZERO new bars — an illiquid name that never printed in those 15m windows.
    Re-asking nightly forever is the same waste as re-downloading the same page every day."""
    monkeypatch.setattr(M, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    d = tmp_path / "klines_cache_backtest" / "tradier"
    d.mkdir(parents=True)
    short = [r for r in _et_rows("2025-01-15", _full_core()) if r["timestamp"] <= _et_rows("2025-01-15", _full_core())[-5]["timestamp"]]
    (d / "T_15m.json").write_text(json.dumps(short))
    calls = []
    monkeypatch.setattr(M, "fetch", lambda sym, frm, to: calls.append(frm) or [])
    monkeypatch.setattr(M.time, "sleep", lambda *_: None)
    r = M.fill_holes("T", True)
    assert r["action"] == "nothing-returned" and len(calls) == 1, r
    st = json.loads((tmp_path / "data" / "massive_fill_exhausted.json").read_text())
    assert st["T"]["sessions"] == ["2025-01-15"], st
    r2 = M.fill_holes("T", True)
    assert r2["action"] == "complete" and r2["short_sessions_the_provider_has_nothing_for"] == 1, r2
    assert len(calls) == 1, "a session already proven empty must cost no further requests"


def test_an_exhausted_session_is_retried_after_the_ttl(tmp_path, monkeypatch):
    import datetime
    monkeypatch.setattr(M, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=M.EXHAUST_TTL_DAYS + 1)).isoformat()
    (tmp_path / "data" / "massive_fill_exhausted.json").write_text(json.dumps({"T": {"sessions": ["2025-01-15"], "ts": old}}))
    assert M.load_exhausted("T") == set(), "a stale verdict must re-arm — the provider backfills its own history too"

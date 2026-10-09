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
    closes = [100.0] * 10 + [
        101.2,
        100.8,
        100.6,
        100.7,
        100.9,
        101.0,
        101.3,
        101.4,
        101.8,
        102.0,
        102.2,
        102.5,
    ]
    c, h, l = _bars(closes, spread=0.5)
    st, level, ext, bars = T.long_state(
        c,
        h,
        l,
        lookback=10,
        fresh_bars=2,
        tight_atr=0.5,
        hold_bars=8,
        hold_tol_atr=0.25,
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
    assert 99.0 < level < 100.0, level


def test_too_few_bars_is_none():
    c, h, l = _bars([100.0] * 10)
    st, _, _, _ = T.long_state(c, h, l)
    assert st == T.NONE


def test_hold_only_failed_never_extended():
    assert T.should_timing_hold(T.FAILED) is True
    assert T.should_timing_hold(T.EXTENDED) is False
    assert T.should_timing_hold(T.FRESH) is False
    assert T.should_timing_hold(T.REBOUND) is False
    assert T.should_timing_hold(T.NONE) is False


def test_trim_never_touches_sacred_breakouts():
    assert T.should_timing_trim(T.EXTENDED, 1.5, 2.5) is False
    assert T.should_timing_trim(T.EXTENDED, 9.9, 9.9) is False
    assert T.should_timing_trim(T.FRESH, 9.9, 0.1) is False
    assert T.should_timing_trim(T.REBOUND, 9.9, 0.8) is False


def test_trim_fires_on_failed_profit():
    assert T.should_timing_trim(T.FAILED, 1.2, -0.5) is True
    assert T.should_timing_trim(T.FAILED, -0.5, -1.0) is False


def test_trim_never_on_winners_riding_or_loss():
    assert T.should_timing_trim(T.NONE, 5.0, 0.0) is False
    assert T.should_timing_trim(T.FAILED, -2.0, -1.0) is False
    assert T.should_timing_trim(T.FAILED, "x", None) is False


def _stage_timing(tmp_path, states, updated_at):
    import json
    from datetime import datetime, timezone

    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    upd = updated_at or datetime.now(timezone.utc).isoformat()
    (d / "ranking_entry_timing.json").write_text(
        json.dumps({"updated_at": upd, "states": states})
    )


def test_reader_returns_states(tmp_path, monkeypatch):
    import ez_manage as ez

    _stage_timing(
        tmp_path,
        {
            "AAA_LONG": {"state": "BREAKOUT_FRESH"},
            "BBB_SHORT": {"state": "EXTENDED_TOP"},
        },
        None,
    )
    monkeypatch.setattr(ez.config, "BASE_PATH", str(tmp_path))
    monkeypatch.setattr(
        ez, "_ANG_TIMING_CACHE", {"mtime": 0.0, "updated_at": 0.0, "states": {}}
    )
    assert ez.ang_timing_state_for("AAA", "LONG") == "BREAKOUT_FRESH"
    assert ez.ang_timing_state_for("BBB", "SHORT") == "EXTENDED_TOP"
    assert ez.ang_timing_state_for("ZZZ", "LONG") == "NONE"


def test_reader_entry_passthrough(tmp_path, monkeypatch):
    import ez_manage as ez

    _stage_timing(
        tmp_path,
        {
            "AAA_LONG": {
                "state": "EXTENDED_TOP",
                "level": 1.5,
                "extension_atr": 2.5,
                "bars_since_break": 6,
            }
        },
        None,
    )
    monkeypatch.setattr(ez.config, "BASE_PATH", str(tmp_path))
    monkeypatch.setattr(
        ez, "_ANG_TIMING_CACHE", {"mtime": 0.0, "updated_at": 0.0, "states": {}}
    )
    entry = ez.ang_timing_entry_for("AAA", "LONG")
    assert entry["state"] == "EXTENDED_TOP" and entry["extension_atr"] == 2.5
    assert ez.ang_timing_entry_for("ZZZ", "SHORT") == {}


def test_reader_fail_open(tmp_path, monkeypatch):
    import ez_manage as ez
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(ez.config, "BASE_PATH", str(tmp_path))
    monkeypatch.setattr(
        ez, "_ANG_TIMING_CACHE", {"mtime": 0.0, "updated_at": 0.0, "states": {}}
    )
    assert ez.ang_timing_state_for("AAA", "LONG") == "NONE"
    stale = (datetime.now(timezone.utc) - timedelta(seconds=3600)).isoformat()
    _stage_timing(tmp_path, {"AAA_LONG": {"state": "EXTENDED_TOP"}}, stale)
    monkeypatch.setattr(
        ez, "_ANG_TIMING_CACHE", {"mtime": 0.0, "updated_at": 0.0, "states": {}}
    )
    assert ez.ang_timing_state_for("AAA", "LONG") == "NONE"
    (tmp_path / "data" / "ranking_entry_timing.json").write_text("{corrupt")
    monkeypatch.setattr(
        ez, "_ANG_TIMING_CACHE", {"mtime": 0.0, "updated_at": 0.0, "states": {}}
    )
    assert ez.ang_timing_state_for("AAA", "LONG") == "NONE"

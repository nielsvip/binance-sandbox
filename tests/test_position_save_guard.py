"""test_position_save_guard — newest-wins save guard + realtime warm-standby arbitration.

Service is primary, realtime is warm backup; only PROVEN-stale saves are refused.
Run from repo root: /opt/anaconda3/envs/binance_env/bin/python -m pytest tests/test_position_save_guard.py -q
"""
import asyncio
import datetime as dt
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ez_positions
import ez_positions_realtime as epr
import ez_positions_service as eps


def _pos(ts):
    return SimpleNamespace(last_updated=dt.datetime.fromtimestamp(ts, dt.timezone.utc))


def _svc(tmp_path, positions, mtime_long=None, mtime_short=None):
    acct = "tst"
    d = tmp_path / acct
    d.mkdir(exist_ok=True)
    paths = {}
    for side, m in (("long", mtime_long), ("short", mtime_short)):
        p = d / f"{side}_positions.json"
        if m is None:
            continue
        p.write_text("{}")
        os.utime(p, (m, m))
        paths[side] = p
    for side in ("long", "short"):
        paths.setdefault(side, d / f"{side}_positions.json")
    svc = SimpleNamespace(positions_by_account={acct: positions}, get_position_file=lambda a, s: paths[s])
    return svc, acct


def _fake_save(monkeypatch):
    calls = []

    async def fake(service, account_key, force=False):
        calls.append((account_key, force))

    monkeypatch.setattr(ez_positions, "atomic_save_positions", fake)
    return calls


def test_guard_saves_fresh(tmp_path, monkeypatch):
    now = time.time()
    svc, acct = _svc(tmp_path, {"k": _pos(now - 5)}, now - 60, now - 60)
    calls = _fake_save(monkeypatch)
    assert asyncio.run(eps.save_positions_guarded(svc, acct)) is True
    assert calls == [(acct, False)]


def test_guard_force_passthrough(tmp_path, monkeypatch):
    now = time.time()
    svc, acct = _svc(tmp_path, {"k": _pos(now - 5)}, now - 60, None)
    calls = _fake_save(monkeypatch)
    assert asyncio.run(eps.save_positions_guarded(svc, acct, force=True)) is True
    assert calls == [(acct, True)]


def test_guard_refuses_older_over_newer(tmp_path, monkeypatch):
    now = time.time()
    svc, acct = _svc(tmp_path, {"k": _pos(now - 60)}, now - 5, now - 5)
    calls = _fake_save(monkeypatch)
    assert asyncio.run(eps.save_positions_guarded(svc, acct, force=True)) is False
    assert calls == []


def test_guard_refuses_ancient_memory(tmp_path, monkeypatch):
    now = time.time()
    svc, acct = _svc(tmp_path, {"k": _pos(now - 300)}, now - 400, now - 400)
    calls = _fake_save(monkeypatch)
    assert asyncio.run(eps.save_positions_guarded(svc, acct, force=True)) is False
    assert calls == []


def test_guard_same_tick_saves(tmp_path, monkeypatch):
    now = time.time()
    svc, acct = _svc(tmp_path, {"k": _pos(now - 5)}, now - 5.5, None)
    calls = _fake_save(monkeypatch)
    assert asyncio.run(eps.save_positions_guarded(svc, acct)) is True
    assert len(calls) == 1


def test_guard_fail_open_missing_stamps(tmp_path, monkeypatch):
    now = time.time()
    svc, acct = _svc(tmp_path, {"k": SimpleNamespace()}, now - 5, now - 5)
    calls = _fake_save(monkeypatch)
    assert asyncio.run(eps.save_positions_guarded(svc, acct)) is True
    assert len(calls) == 1


def test_guard_fail_open_on_error(tmp_path, monkeypatch):
    now = time.time()
    svc, acct = _svc(tmp_path, {"k": _pos(now - 5)}, now - 5, now - 5)
    svc.get_position_file = lambda a, s: (_ for _ in ()).throw(OSError("boom"))
    calls = _fake_save(monkeypatch)
    assert asyncio.run(eps.save_positions_guarded(svc, acct)) is True
    assert len(calls) == 1


def test_memory_newest_parses_forms():
    now = time.time()
    iso = dt.datetime.fromtimestamp(now - 10, dt.timezone.utc).isoformat()
    d = {"a": SimpleNamespace(last_updated=iso), "b": SimpleNamespace(last_updated=now - 50)}
    assert abs(eps.memory_positions_newest(d) - (now - 10)) < 2
    assert eps.memory_positions_newest({"x": SimpleNamespace()}) is None
    assert eps.memory_positions_newest({}) is None


def test_should_standby_table():
    now = 1000000.0
    assert epr.should_standby(0.0, 0.0, now) is False
    assert epr.should_standby(now - 40, 0.0, now) is False
    assert epr.should_standby(now - 5, 0.0, now) is True
    assert epr.should_standby(now - 5, now - 5, now) is False
    assert epr.should_standby(now - 5, now - 60, now) is True

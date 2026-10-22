"""Service PAU bound end-to-end (USER 2026-10-22 positions-reliability: the service must never hang).

Fin died 17:15Z with 9 unverified positions: entry-bar klines rebuilds are 15 s-timeouted
and SERIAL inside process_account_update (9x15 > 120 s tripwire). The fix caps rebuilds at
3 per fetch cycle and wraps the stop cycle in wait_for(45).

These tests drive the REAL methods on an isolated PositionService (object.__new__ — no
accounts, no broker, no loop state) through the PUBLIC process_account_update path, followed
by the writer loop's save_positions_guarded, with a STALLED klines leg (2 s/call, then valid
channel data). Five held positions need entry-bar verification (> 3, so the cap must bite).
No network, no live dirs (BASE_PATH under tmp_path). Honest costs: the cap test burns ~6 s
of staged klines stall; the wedge test burns the real 45 s cycle timeout.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ez_positions as ezp  # noqa: E402
import ez_positions_service as eps  # noqa: E402

ACCT = "tst"
SYMS = [("TST1USDT", "LONG", 1.5, 100.0, 101.0), ("TST2USDT", "LONG", 2.5, 50.0, 50.5), ("TST3USDT", "LONG", 0.8, 200.0, 202.0), ("TST4USDT", "SHORT", 3.0, 10.0, 9.9), ("TST5USDT", "SHORT", 1.2, 75.0, 74.0)]


class _RedisStub:
    def __init__(self):
        self.sets = []
        self.pubs = []

    async def set(self, key, value, ex=None):
        self.sets.append((key, value))
        return True

    async def publish(self, channel, message):
        self.pubs.append((channel, message))
        return 1


def _klines_rows(base):
    return [[i, str(base), str(base + 2), str(base - 1), str(base + 1), "0", "0", "0", "0", "0", "0", "0"] for i in range(20)]


def _mem_row(symbol, side, amt, ep, mp):
    now = datetime.now(timezone.utc)
    return {"symbol": symbol, "position_side": side, "entry_price": ep, "mark_price": mp, "positionAmt": amt, "initial_quantity": amt, "max_quantity": amt, "opened_at": now, "last_updated": now}


def _api_row(symbol, side, amt, ep, mp):
    return {"symbol": symbol, "positionSide": side, "positionAmt": str(amt), "entryPrice": str(ep), "markPrice": str(mp), "unrealizedProfit": "0.0"}


async def _make_svc(tmp_path, klines_fn):
    svc = object.__new__(eps.PositionService)
    svc.config = SimpleNamespace(BASE_PATH=tmp_path)
    svc.accounts = {}
    by_acct = {}
    for symbol, side, amt, ep, mp in SYMS:
        key = f"{ACCT}:{symbol}_{side}"
        by_acct[key] = eps.Position.from_dict(_mem_row(symbol, side, amt, ep, mp))
    svc.positions_by_account = {ACCT: by_acct}
    svc.positions = dict(by_acct)
    svc._position_update_timestamps = {}
    svc._last_api_seen = {}
    svc._positions_lock = asyncio.Lock()
    svc._position_flush_event = None
    svc._positions_dirty = False
    svc.positions_live = False
    svc.positions_source = None
    svc.positions_last_sync = None
    svc._loading_complete_event = asyncio.Event()
    svc._loading_complete_event.set()
    svc._last_full_redis_sync = {}
    svc.redis_manager = _RedisStub()
    svc.trade_manager = None
    svc._ensure_trade_manager = None
    svc.min_qty = {}
    svc.reentry_data = {}
    svc._restore_negative_cache = {}
    svc._get_expected_symbol_count = lambda: 0
    calls = []

    def _klines(**kw):
        calls.append(kw)
        return klines_fn(kw)

    svc.get_client = lambda ak: SimpleNamespace(futures_klines=_klines)

    async def _noop(ak):
        return None

    svc.monitor_reductions_priority = _noop
    api_rows = [_api_row(s, d, a, e, m) for s, d, a, e, m in SYMS]
    return svc, api_rows, calls


def _run(coro):
    return asyncio.run(coro)


def _skip_backups():
    now = time.time()
    ezp._last_backup_time[f"{ACCT}_LONG"] = now
    ezp._last_backup_time[f"{ACCT}_SHORT"] = now


def test_entry_bar_rebuilds_capped_at_three_per_cycle(tmp_path, monkeypatch):
    """5 unverified positions x staged 2 s klines stall: exactly 3 rebuilds attempted, PAU
    returns all 5 keys promptly, redis + both JSON files fresh."""
    monkeypatch.setattr(eps, "_svc_psym_get", lambda symbol, side, knob, default: default)

    def _slow_klines(kw):
        time.sleep(2.0)
        return _klines_rows(100.0)

    async def _main():
        svc, api_rows, calls = await _make_svc(tmp_path, _slow_klines)
        _skip_backups()
        t0 = time.time()
        updated = await svc.process_account_update(ACCT, api_rows, single=False, skip_broadcast_save=False)
        dt = time.time() - t0
        saved = await eps.save_positions_guarded(svc, ACCT)
        return svc, updated, dt, saved, calls

    svc, updated, dt, saved, calls = _run(_main())
    assert len(calls) == 3, f"cap breached: {len(calls)} klines rebuilds in one cycle"
    assert len(updated) == 5
    assert dt < 60.0, f"cycle took {dt:.1f}s despite the cap"
    assert saved is True
    assert len(svc.__dict__.get("_fx_entry_verified", set())) == 3, "the 3 attempted lifecycles verify, the other 2 defer"
    assert any(k == f"positions:{ACCT}" for k, _ in svc.redis_manager.sets), "no full redis snapshot written"
    for side, expect in (("long", 3), ("short", 2)):
        p = tmp_path / ACCT / f"{side}_positions.json"
        rows = json.loads(p.read_text())
        assert len(rows) == expect, f"{side}: {len(rows)} rows"
        assert time.time() - p.stat().st_mtime < 60.0, f"{side} file not fresh"


def test_wedged_stop_cycle_costs_one_cycle_not_the_tripwire(tmp_path, monkeypatch):
    """The stop cycle wedged at 120 s: PAU still returns in ~45 s with redis + JSON fresh.
    (The wedge replaces the bound method, so this pins the call-site wiring; the cap test
    above pins the method itself.)"""
    monkeypatch.setattr(eps, "_svc_psym_get", lambda symbol, side, knob, default: default)

    async def _main():
        svc, api_rows, _ = await _make_svc(tmp_path, lambda kw: _klines_rows(100.0))

        async def _wedge(*a, **k):
            await asyncio.sleep(120.0)

        svc._fixed_stop_fetch_cycle = _wedge
        _skip_backups()
        t0 = time.time()
        updated = await svc.process_account_update(ACCT, api_rows, single=False, skip_broadcast_save=False)
        dt = time.time() - t0
        saved = await eps.save_positions_guarded(svc, ACCT)
        return svc, updated, dt, saved

    svc, updated, dt, saved = _run(_main())
    assert dt < 75.0, f"wedged stop cycle held PAU {dt:.1f}s"
    assert len(updated) == 5
    assert saved is True
    assert any(k == f"positions:{ACCT}" for k, _ in svc.redis_manager.sets)
    for side in ("long", "short"):
        p = tmp_path / ACCT / f"{side}_positions.json"
        assert p.exists() and time.time() - p.stat().st_mtime < 120.0

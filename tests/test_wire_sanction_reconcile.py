"""Wire-sanction reconcile (USER 2026-10-11 fix-as-discovered): a stale OPEN sanction wedged inf:XTZUSDT_LONG 5h (refuse-before-verify deadlock — every new act dies at _sanction_execution before any verify/clear path can run). On sanction-refuse the key now spawns a cancel-then-classify reconcile that retires broker-confirmed terminal orders so the NEXT act can place. Current act stays refused (fail-safe). Fresh sanctions (<180s, live maker window) are never touched; at most one reconcile per key per 60s.
Run with EZ_LOG_DIR=/tmp (repo pattern: log dir is not writable in sandbox).
"""
import asyncio
import time
import ez_manage


KEY = "tst:XTZUSDT_LONG_RECONCILE_TEST"
OID = "13020190717"


def _seed(open_ts):
    ez_manage._EXEC_WIRE_SANCTIONS[KEY] = {"seq": 1, "action": "OPEN", "side": "BUY", "orders": {OID: {"state": ez_manage._WIRE_STATE_OPEN, "site": "test", "ts": open_ts}}}
    ez_manage._WIRE_RECONCILE_LAST.pop(KEY, None)


def _cleanup():
    ez_manage._EXEC_WIRE_SANCTIONS.pop(KEY, None)
    ez_manage._WIRE_RECONCILE_LAST.pop(KEY, None)


def test_reconcile_retires_terminal_and_heals(monkeypatch):
    _seed(time.time() - 3600.0)
    calls = []
    async def fake_verify(client, symbol, oid):
        calls.append(oid)
        return True, "ABSENT"
    monkeypatch.setattr(ez_manage, "_verify_order_terminal", fake_verify)
    assert ez_manage._sanction_execution(KEY, "OPEN", "BUY", 6.0, "test") is None
    healed = asyncio.run(ez_manage._reconcile_wire_sanction(object(), KEY, "XTZUSDT"))
    assert healed is True and calls == [OID]
    assert ez_manage._sanction_execution(KEY, "OPEN", "BUY", 6.0, "test") is not None
    _cleanup()


def test_reconcile_keeps_live_order(monkeypatch):
    _seed(time.time() - 3600.0)
    async def fake_verify(client, symbol, oid):
        return False, "LIVE"
    monkeypatch.setattr(ez_manage, "_verify_order_terminal", fake_verify)
    healed = asyncio.run(ez_manage._reconcile_wire_sanction(object(), KEY, "XTZUSDT"))
    assert healed is False
    assert ez_manage._sanction_execution(KEY, "OPEN", "BUY", 6.0, "test") is None
    _cleanup()


def test_reconcile_skips_fresh_sanction(monkeypatch):
    _seed(time.time() - 5.0)
    calls = []
    async def fake_verify(client, symbol, oid):
        calls.append(oid)
        return True, "CANCELED"
    monkeypatch.setattr(ez_manage, "_verify_order_terminal", fake_verify)
    healed = asyncio.run(ez_manage._reconcile_wire_sanction(object(), KEY, "XTZUSDT"))
    assert healed is False and calls == []
    _cleanup()


def test_reconcile_throttled_to_one_per_minute(monkeypatch):
    _seed(time.time() - 3600.0)
    calls = []
    async def fake_verify(client, symbol, oid):
        calls.append(oid)
        return False, "UNKNOWN"
    monkeypatch.setattr(ez_manage, "_verify_order_terminal", fake_verify)
    asyncio.run(ez_manage._reconcile_wire_sanction(object(), KEY, "XTZUSDT"))
    asyncio.run(ez_manage._reconcile_wire_sanction(object(), KEY, "XTZUSDT"))
    assert calls == [OID]
    _cleanup()


def test_spawn_schedules_reconcile_without_blocking(monkeypatch):
    _seed(time.time() - 3600.0)
    calls = []
    async def fake_verify(client, symbol, oid):
        calls.append(oid)
        return True, "CANCELED"
    monkeypatch.setattr(ez_manage, "_verify_order_terminal", fake_verify)
    async def main():
        ez_manage._spawn_wire_reconcile(object(), KEY, "XTZUSDT")
        await asyncio.sleep(0.2)
    asyncio.run(main())
    assert calls == [OID]
    _cleanup()

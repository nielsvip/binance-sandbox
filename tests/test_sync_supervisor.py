"""The broker-truth sync loop can never die: the supervisor relaunches it on
ANY exit (exception, cancellation, loop return) while the manager is running,
and stops quietly on shutdown. (NVDA trb 2026-10-13: sync died silently and
order paths acted on stale memory for hours.)"""
import asyncio

import pytest

import tradier_manage as tm_mod
import tradier_positions as tp_mod


def _mgr():
    m = object.__new__(tm_mod.TradierTradeManager)
    m.running = True
    m.reader = object()
    m._sync_calls = 0
    return m


@pytest.mark.asyncio
async def test_supervisor_relaunches_after_exception():
    m = _mgr()

    async def flaky():
        m._sync_calls += 1
        if m._sync_calls < 3:
            raise RuntimeError("redis exploded")
        m.running = False  # third incarnation ends the manager -> supervisor exits

    m.sync_positions_loop = flaky
    await asyncio.wait_for(m._sync_supervisor(), timeout=10)
    assert m._sync_calls == 3


@pytest.mark.asyncio
async def test_supervisor_relaunches_after_cancellation():
    m = _mgr()

    async def cancelling():
        m._sync_calls += 1
        if m._sync_calls < 2:
            raise asyncio.CancelledError()
        m.running = False

    m.sync_positions_loop = cancelling
    await asyncio.wait_for(m._sync_supervisor(), timeout=10)
    assert m._sync_calls == 2


@pytest.mark.asyncio
async def test_supervisor_relaunches_after_plain_return():
    m = _mgr()

    async def returning():
        m._sync_calls += 1
        if m._sync_calls >= 2:
            m.running = False

    m.sync_positions_loop = returning
    await asyncio.wait_for(m._sync_supervisor(), timeout=10)
    assert m._sync_calls == 2


@pytest.mark.asyncio
async def test_supervisor_stops_when_not_running():
    m = _mgr()
    m.running = False
    m.sync_positions_loop = lambda: (_ for _ in ()).throw(AssertionError("must not start"))
    await asyncio.wait_for(m._sync_supervisor(), timeout=5)
    assert m._sync_calls == 0


@pytest.mark.asyncio
async def test_positions_publisher_supervisor_relaunches():
    m = object.__new__(tp_mod.TradierPositionManager)
    m.running = True
    m.account_key = "trb"
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("api exploded")
        m.running = False

    await asyncio.wait_for(m._supervise_loop(flaky, "API_SYNC"), timeout=10)
    assert calls["n"] == 3

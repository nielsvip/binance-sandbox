"""Tracked VEC_EXACT dispatch — execution survives sweep cancellation."""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import vx_tracked_exec as T


def test_normal_completion():
    async def go():
        return await T.dispatch_tracked("k1", "CLOSE t", lambda: _ok(), 5.0)

    async def _ok():
        await asyncio.sleep(0.01)
        return "SUCCESS"

    assert asyncio.run(go()) == "SUCCESS"
    assert "k1" not in T.INFLIGHT


def test_outer_cancel_inner_survives():
    done = {}

    async def slow():
        await asyncio.sleep(0.3)
        done["v"] = "FILLED"
        return "FILLED"

    async def outer():
        return await T.dispatch_tracked("k2", "CLOSE t", slow, 5.0)

    async def go():
        t = asyncio.create_task(outer())
        await asyncio.sleep(0.05)
        t.cancel()
        try:
            await t
            return "NO_RAISE"
        except asyncio.CancelledError:
            pass
        for _ in range(100):
            if "k2" not in T.INFLIGHT:
                break
            await asyncio.sleep(0.02)
        return done.get("v")

    assert asyncio.run(go()) == "FILLED"  # outer cancelled, execution completed
    assert "k2" not in T.INFLIGHT


def test_dup_skipped_single_flight():
    started = []

    async def slow():
        started.append(1)
        await asyncio.sleep(0.2)
        return "SUCCESS"

    async def go():
        t1 = asyncio.create_task(T.dispatch_tracked("k3", "CLOSE t", slow, 5.0))
        await asyncio.sleep(0.05)
        r2 = await T.dispatch_tracked("k3", "CLOSE t", slow, 5.0)
        r1 = await t1
        return r1, r2

    r1, r2 = asyncio.run(go())
    assert (r1, r2) == ("SUCCESS", "SKIPPED_VX_INFLIGHT")
    assert started == [1]


def test_inner_timeout_returns_blocked():
    async def hang():
        await asyncio.sleep(5.0)
        return "never"

    async def go():
        return await T.dispatch_tracked("k4", "CLOSE t", hang, 0.1)

    assert asyncio.run(go()) == "BLOCKED_DISPATCH_TIMEOUT"
    assert "k4" not in T.INFLIGHT


def test_stale_entry_allows_redispatch():
    async def hang():
        await asyncio.sleep(30.0)

    async def go():
        t = asyncio.create_task(T.dispatch_tracked("k5", "CLOSE t", hang, 600.0))
        await asyncio.sleep(0.05)
        T.INFLIGHT["k5"] = (T.INFLIGHT["k5"][0], time.time() - 700.0, 600.0)
        r2 = await T.dispatch_tracked("k5", "CLOSE t2", lambda: _ok(), 5.0)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        return r2

    async def _ok():
        return "SUCCESS2"

    assert asyncio.run(go()) == "SUCCESS2"

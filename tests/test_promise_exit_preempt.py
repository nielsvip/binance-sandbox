"""An exit may never wait behind a non-exit (USER 2026-10-18 "trade what you promised nothing more nothing less").

Measured failure this pins: the single-flight guard in tools/vx_tracked_exec is keyed on the position only, so a vec
full-close queued behind a slower non-exit execution on the same key and every retry answered SKIPPED_VX_INFLIGHT
(fin:RLCUSDT_SHORT 2026-10-18 04:26:22 -> 04:29:32), until the VEC_STALE_FLATTEN backstop closed it hours later.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ["PROMISE_LEDGER"] = "0"

import promise_ledger as PL  # noqa: E402
import vx_tracked_exec as T  # noqa: E402


def test_exit_label_classification():
    assert T._is_exit("CLOSE VEC_EXIT_OWED")
    assert T._is_exit("REDUCE EXIT_VELOCITY_WT against-long")
    assert T._is_exit("CLOSE VEC_STALE_FLATTEN paper-flat-live-holds")
    assert not T._is_exit("OPEN B_KZONE")
    assert not T._is_exit("AUGMENT GOLDEN_RULE_ENTRY")
    assert PL.is_exit_label("CLOSE x") and not PL.is_exit_label("AUGMENT x")


def test_exit_preempts_non_exit_inflight():
    """The promised exit dispatches while an OPEN/AUGMENT chase is still running, and wins the INFLIGHT slot."""

    async def hang():
        await asyncio.sleep(30.0)

    async def go():
        t = asyncio.create_task(T.dispatch_tracked("p1", "AUGMENT GOLDEN_RULE_ENTRY", hang, 600.0))
        await asyncio.sleep(0.05)
        assert T.INFLIGHT["p1"][3] == "OTHER"
        r = await T.dispatch_tracked("p1", "CLOSE VEC_EXIT_OWED", lambda: _ok(), 5.0)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        return r

    async def _ok():
        await asyncio.sleep(0.01)
        return "SUCCESS"

    assert asyncio.run(go()) == "SUCCESS"


def test_preempted_non_exit_does_not_clear_the_exit_slot():
    """The preempted task's done-callback must not free the slot the exit now owns (that would allow a duplicate exit)."""

    async def go():
        t = asyncio.create_task(T.dispatch_tracked("p2", "AUGMENT B12", lambda: _slow(), 600.0))
        await asyncio.sleep(0.05)
        asyncio.ensure_future(T.dispatch_tracked("p2", "CLOSE VEC_EXIT_OWED", lambda: _slow_exit(), 600.0))
        await asyncio.sleep(0.25)  # the non-exit has finished by now, the exit is still running
        held = T.INFLIGHT.get("p2")
        r2 = await T.dispatch_tracked("p2", "CLOSE VEC_EXIT_OWED", lambda: _slow_exit(), 600.0)
        t.cancel()
        return held, r2

    async def _slow():
        await asyncio.sleep(0.1)
        return "SUCCESS_AUG"

    async def _slow_exit():
        await asyncio.sleep(2.0)
        return "SUCCESS_EXIT"

    held, r2 = asyncio.run(go())
    assert held is not None and held[3] == "EXIT"
    assert r2 == "SKIPPED_VX_INFLIGHT"


def test_exit_does_not_preempt_another_exit_but_escalates_when_wedged():
    """Two exits never double-dispatch; a wedged one escalates to the FORCE market leg instead of being skipped forever."""
    fired = []

    async def go():
        t = asyncio.create_task(T.dispatch_tracked("p3", "CLOSE VEC_EXIT_OWED", lambda: _hang(), 600.0))
        await asyncio.sleep(0.05)
        r_fresh = await T.dispatch_tracked("p3", "CLOSE VEC_EXIT_OWED", lambda: _hang(), 600.0, on_timeout=_force)
        ent = T.INFLIGHT["p3"]
        T.INFLIGHT["p3"] = (ent[0], time.time() - 200.0, ent[2], ent[3])
        r_wedged = await T.dispatch_tracked("p3", "CLOSE VEC_EXIT_OWED", lambda: _hang(), 600.0, on_timeout=_force)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        return r_fresh, r_wedged

    async def _hang():
        await asyncio.sleep(30.0)

    async def _force():
        fired.append(1)
        return "TIMEOUT_EXIT_FORCE ok"

    T._FORCED.pop("p3", None)
    r_fresh, r_wedged = asyncio.run(go())
    assert r_fresh == "SKIPPED_VX_INFLIGHT"  # a fresh exit is still in flight: no duplicate order
    assert r_wedged.startswith("EXIT_FORCE_ESCALATION") and fired == [1]


def test_kill_switch_restores_the_old_behaviour():
    os.environ["VX_EXIT_PREEMPT"] = "0"
    try:

        async def go():
            t = asyncio.create_task(T.dispatch_tracked("p4", "AUGMENT B12", lambda: _hang(), 600.0))
            await asyncio.sleep(0.05)
            r = await T.dispatch_tracked("p4", "CLOSE VEC_EXIT_OWED", lambda: _hang(), 600.0)
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
            return r

        async def _hang():
            await asyncio.sleep(30.0)

        assert asyncio.run(go()) == "SKIPPED_VX_INFLIGHT"
    finally:
        os.environ.pop("VX_EXIT_PREEMPT", None)


def test_legacy_three_tuple_does_not_crash_the_dispatcher():
    async def go():
        t = asyncio.create_task(T.dispatch_tracked("p5", "CLOSE t", lambda: _hang(), 600.0))
        await asyncio.sleep(0.05)
        ent = T.INFLIGHT["p5"]
        T.INFLIGHT["p5"] = (ent[0], ent[1], ent[2])
        r = await T.dispatch_tracked("p5", "AUGMENT t2", lambda: _hang(), 600.0)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        return r

    async def _hang():
        await asyncio.sleep(30.0)

    assert asyncio.run(go()) == "SKIPPED_VX_INFLIGHT"

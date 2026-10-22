"""Broker-truth refresher (USER 2026-10-22 BROKER_UNREACHABLE lie): the event loop can
starve 9s+ under CPU load while the broker answers positionRisk in 0.75s, so the old
loop-timed single-flight timeouts misfired and every open was refused as "unreachable".
The refresher thread polls broker truth OFF-loop; snapshot() serves the slot with no
loop-timed waiting. A stale slot is GENUINE unreachability (failover signal), never timing.
"""
import asyncio
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import positions_truth as pt  # noqa: E402

CFG = SimpleNamespace(
    POSITIONS_BROKER_CACHE_S=1.0,
    POSITIONS_BROKER_SLOT_STALE_S=20.0,
    POSITIONS_BROKER_REFRESH_S=0.05,
    POSITIONS_BROKER_TIMEOUT_S=8.0,
)

ROWS = [
    {"symbol": "MANAUSDT", "positionSide": "LONG", "positionAmt": "297", "entryPrice": "0.09397"}
]


def _bp(sync=None, last_age=None):
    async def _never(_=None):
        raise AssertionError("async fetch must not run on the refresher path")

    bp = pt.BrokerPositions("binance", "ang", _never, config=CFG, sync_fetch=sync)
    if last_age is not None:
        bp.last = pt.BrokerSnapshot("binance", "ang", time.time() - last_age, {"MANAUSDT": 1.0}, {})
    return bp


def test_slot_serves_fresh_despite_held_asyncio_lock():
    """The lie regression: even with the single-flight lock HELD (old code would abandon
    after 9 s and refuse as unreachable), a fresh slot returns immediately."""
    bp = _bp(sync=lambda: ROWS, last_age=2.0)

    async def main():
        bp._alock()
        await bp._lock.acquire()
        t0 = time.time()
        try:
            got = await bp.snapshot(reason="pre ang:X OPEN")
        finally:
            bp._lock.release()
        return got, time.time() - t0

    got, dt = asyncio.new_event_loop().run_until_complete(main())
    assert got is not None and dt < 2.0, dt


def test_stale_slot_returns_none_and_signals_genuine_once(caplog):
    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("broker down")

    bp = _bp(sync=_boom, last_age=3600.0)
    logger = logging.getLogger("pt_genuine_test")
    bp.log = logger
    with caplog.at_level(logging.CRITICAL, logger="pt_genuine_test"):

        async def go():
            assert await bp.snapshot(reason="pre ang:X OPEN") is None
            assert await bp.snapshot(reason="pre ang:X OPEN") is None

        asyncio.new_event_loop().run_until_complete(go())
    hits = [r for r in caplog.records if "GENUINE_SLOT_STALE" in r.getMessage()]
    assert len(hits) == 1  # edge-triggered, not per attempt
    assert "POSITIONS_BROKER_FETCH_FAILED" in hits[0].getMessage()  # feeds existing counters


def test_refresher_recovers_slot_without_loop():
    seen = {"n": 0}

    def _rows():
        seen["n"] += 1
        return ROWS

    bp = _bp(sync=_rows)
    bp.ensure_refresher()
    t0 = time.time()
    got = None
    while time.time() - t0 < 5.0:
        got = asyncio.new_event_loop().run_until_complete(bp.snapshot(reason="x"))
        if got is not None:
            break
        time.sleep(0.05)
    assert got is not None and got.amount("MANAUSDT", "LONG") == 297.0
    assert seen["n"] >= 1


def test_force_still_uses_async_path():
    async def _async_fetch():
        return ROWS

    bp = pt.BrokerPositions("binance", "ang", _async_fetch, config=CFG, sync_fetch=lambda: (_ for _ in ()).throw(RuntimeError("sync must not run")))
    got = asyncio.new_event_loop().run_until_complete(bp.snapshot(force=True, reason="confirm"))
    assert got is not None and got.amount("MANAUSDT", "LONG") == 297.0


def test_no_sync_fetch_keeps_old_path():
    async def _async_fetch():
        return ROWS

    bp = pt.BrokerPositions("binance", "ang", _async_fetch, config=CFG)
    got = asyncio.new_event_loop().run_until_complete(bp.snapshot(reason="pre ang:X OPEN"))
    assert got is not None and got.amount("MANAUSDT", "LONG") == 297.0

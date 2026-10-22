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


def test_truth_slot_age_inf_when_absent():
    assert pt.truth_slot_age_s("binance", "no_such_acct_xyz") == float("inf")


def test_ez_broker_sync_fetch_reads_service_store_no_io():
    """USER 2026-10-22 refresher v2: the sync fetch reads the WS-fed service
    store (no socket, no REST weight, cannot hang). A sync REST client whose
    pool wedges under load hung the v1 refresher forever (S1 20:23-20:43Z:
    slot never filled, zero REFRESH_FAIL, ban 0.0)."""
    pos = SimpleNamespace(symbol="MANAUSDT", positionSide="LONG", positionAmt="297", entryPrice="0.09397")
    svc = SimpleNamespace(positions={"ang_store1:MANAUSDT_LONG": pos})
    mgr = SimpleNamespace(positions_service=svc, accounts={})
    bp = pt.ez_broker(mgr, "ang_store1", config=CFG)
    got, dt = None, 0.0
    t0 = time.time()
    while time.time() - t0 < 5.0:
        got = asyncio.new_event_loop().run_until_complete(bp.snapshot(reason="pre ang:X OPEN"))
        if got is not None:
            dt = time.time() - t0
            break
        time.sleep(0.05)
    assert got is not None and got.amount("MANAUSDT", "LONG") == 297.0
    assert pt.truth_slot_age_s("binance", "ang_store1") < 5.0


def test_ez_broker_sync_fetch_dict_positions_and_empty_store():
    """Dict-shaped store rows work; an empty store seeds a fresh flat snapshot
    (empty is truth, not failure)."""
    svc = SimpleNamespace(
        positions={"ang_store2:FOO_SHORT": {"symbol": "FOO", "positionSide": "SHORT", "positionAmt": "-3", "entryPrice": "1"}}
    )
    mgr = SimpleNamespace(positions_service=svc, accounts={})
    bp = pt.ez_broker(mgr, "ang_store2", config=CFG)
    got = None
    t0 = time.time()
    while time.time() - t0 < 5.0:
        got = asyncio.new_event_loop().run_until_complete(bp.snapshot(reason="pre ang:X OPEN"))
        if got is not None:
            break
        time.sleep(0.05)
    assert got is not None and got.amount("FOO", "SHORT") == 3.0  # normalize_binance abs (broker semantics)


def test_truth_slot_age_tracks_seeded_snapshot():
    assert pt.seed_snapshot("binance", "age_probe_acct", ROWS) is True
    age = pt.truth_slot_age_s("binance", "age_probe_acct")
    assert 0.0 <= age < 5.0, age
    assert pt.truth_slot_age_s("tradier", "age_probe_acct") == float("inf")

"""2026-10-14 live-vs-board parity: a broker read slower than 3 s made every concurrent caller return None
(POSITIONS_SNAPSHOT_LOCK_BUSY -> BLOCKED_POSITIONS_BROKER_UNREACHABLE on vec OPENs). Waiters must share the in-flight read."""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import positions_truth as pt  # noqa: E402

CFG = SimpleNamespace(POSITIONS_BROKER_CACHE_S=1.0, POSITIONS_BROKER_TIMEOUT_S=8.0)


class SlowBinance:
    def __init__(self, delay):
        self.delay = delay
        self.calls = 0

    def futures_position_information(self):
        self.calls += 1
        time.sleep(self.delay)
        return [{"symbol": "MANAUSDT", "positionSide": "LONG", "positionAmt": "297", "entryPrice": "0.09397"}]


def test_waiter_shares_slow_inflight_read():
    fb = SlowBinance(3.5)
    bp = pt.BrokerPositions("binance", "ang", pt.binance_fetcher(fb), config=CFG)
    bp.last = pt.BrokerSnapshot("binance", "ang", time.time() - 3600.0, {}, {})

    async def go():
        first = asyncio.ensure_future(bp.snapshot(force=True, reason="holder"))
        await asyncio.sleep(0.1)
        second = await bp.snapshot(reason="pre_order ang:MANAUSDT_LONG OPEN")
        return await first, second

    first, second = asyncio.new_event_loop().run_until_complete(go())
    assert second is not None and second is first
    assert second.amount("MANAUSDT", "LONG") == 297.0
    assert fb.calls == 1


def test_forced_waiter_accepts_read_started_after_request_only():
    fb = SlowBinance(0.2)
    bp = pt.BrokerPositions("binance", "ang", pt.binance_fetcher(fb), config=CFG)
    bp.last = pt.BrokerSnapshot("binance", "ang", time.time() - 3600.0, {}, {})

    async def go():
        a = asyncio.ensure_future(bp.snapshot(force=True, reason="a"))
        await asyncio.sleep(0.05)
        b = await bp.snapshot(force=True, reason="b")
        return await a, b

    a, b = asyncio.new_event_loop().run_until_complete(go())
    assert fb.calls == 2  # b's request came after a's read started -> b needs its own read
    assert b.fetched_at >= a.fetched_at

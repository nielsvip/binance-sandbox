"""Tracked VEC_EXACT dispatch — execution survives sweep cancellation."""

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
os.environ["PROMISE_LEDGER"] = "0"  # a test dispatch must never write into the live promise ledger

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


class _Pos:
    def __init__(self, amt):
        self.positionAmt = amt


class _Client:
    def __init__(self, open_orders):
        self.open_orders = open_orders
        self.calls = []

    def futures_get_open_orders(self, symbol=None):
        self.calls.append(("get_open", symbol))
        return list(self.open_orders)

    def futures_cancel_order(self, symbol=None, orderId=None):
        self.calls.append(("cancel", symbol, orderId))
        self.open_orders = [o for o in self.open_orders if o.get("orderId") != orderId]
        return {"status": "CANCELED"}

    def futures_create_order(self, symbol=None, side=None, positionSide=None, quantity=None, type=None):
        self.calls.append(("market", symbol, side, positionSide, quantity, type))
        return {"orderId": 1, "status": "FILLED"}


class _TM:
    def __init__(self, amt, open_orders, step=0.1):
        self.positions = {"a:S_LONG": _Pos(amt)}
        self._client = _Client(open_orders)
        self._step = step

    def get_client(self, acct):
        return self._client

    def get_symbol_config(self, sym):
        return {"step_size": self._step}


def test_timeout_market_cancels_then_fires():
    tm = _TM(10.0, [{"orderId": 7, "side": "SELL", "status": "NEW"},
                     {"orderId": 8, "side": "BUY", "status": "NEW"}])

    async def go():
        return await T.fire_exit_market(tm, "a:S_LONG", "S", "SELL", "LONG")

    assert asyncio.run(go()) == "TIMEOUT_EXIT_MARKET 10.0"
    kinds = [c[0] for c in tm._client.calls]
    assert kinds == ["get_open", "cancel", "market"]  # same-side only, cancel before market
    assert tm._client.calls[1][2] == 7
    assert tm._client.calls[2][1:] == ("S", "SELL", "LONG", "10.0", "MARKET")


def test_timeout_market_flat_no_wire():
    tm = _TM(0.0, [{"orderId": 7, "side": "SELL", "status": "NEW"}])

    async def go():
        return await T.fire_exit_market(tm, "a:S_LONG", "S", "SELL", "LONG")

    assert asyncio.run(go()) == "TIMEOUT_ALREADY_FLAT"
    assert tm._client.calls == []


def test_timeout_market_no_client():
    class _NoTM(_TM):
        def get_client(self, acct):
            return None

    async def go():
        return await T.fire_exit_market(_NoTM(5.0, []), "a:S_LONG", "S", "SELL", "LONG")

    assert asyncio.run(go()) == "TIMEOUT_MARKET_NO_CLIENT"


def test_dispatch_timeout_runs_market_callback():
    tm = _TM(10.0, [])

    async def hang():
        await asyncio.sleep(30.0)

    async def go():
        return await T.dispatch_tracked("k6", "CLOSE t", hang, 0.1,
                                        on_timeout=lambda: T.fire_exit_market(tm, "a:S_LONG", "S", "SELL", "LONG"))

    res = asyncio.run(go())
    assert res.startswith("BLOCKED_DISPATCH_TIMEOUT->TIMEOUT_EXIT_MARKET")
    assert [c[0] for c in tm._client.calls] == ["get_open", "market"]


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

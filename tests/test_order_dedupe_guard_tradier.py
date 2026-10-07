"""ORDER_DEDUPE_GUARD — Tradier (tradier_manage) path. Mocked broker only; no network, no live orders."""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import order_dedupe_guard as odg  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_DEDUPE_ONLY = SimpleNamespace(WIRE_EXPOSURE_GATE_ENABLED=False, WIRE_REQUIRE_EXECUTE_NOW_ALL=False, WIRE_FILE_REFLECTION_ENABLED=False)  # these tests isolate the dedupe layer; exposure gate: tests/test_positions_revamp_incident.py


class Clock:
    def __init__(self, t=None):
        self.t = t or datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.t


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeTradier:
    """TradierAPIClient double: _request (raw), place_order, cancel_order, get_order_status."""

    def __init__(self, clock):
        self.clock = clock
        self._current_id = "VA123"
        self.orders = []
        self.placed = []
        self.list_fail = False
        self.place_mode = "ok"  # ok | gateway_error | raise | retried_twice | reject
        self.next_id = 1000

    def _new(self, symbol, side, qty, typ, status="open"):
        o = {"id": self.next_id, "symbol": symbol, "side": side, "type": typ, "status": status, "quantity": float(qty), "exec_quantity": 0.0, "create_date": _iso(self.clock())}
        self.next_id += 1
        self.orders.append(o)
        return o

    async def _request(self, method, endpoint, params=None, data=None, use_data_context=False, headers=None, retry_count=0):
        assert method == "GET"
        if self.list_fail:
            return {}
        if endpoint.endswith("/orders"):
            return {"orders": "null"} if not self.orders else {"orders": {"order": [dict(o) for o in self.orders]}}
        oid = int(endpoint.rsplit("/", 1)[1])
        for o in self.orders:
            if o["id"] == oid:
                return {"order": dict(o)}
        return {}

    async def place_order(self, account_key, symbol, side, quantity, order_type="market", price=None, stop=None, duration="day"):
        self.placed.append((symbol, side, quantity, order_type))
        if self.place_mode == "raise":
            raise asyncio.TimeoutError()
        if self.place_mode == "gateway_error":
            self._new(symbol, side, quantity, order_type)  # accepted at broker, response lost
            return {"status": "error", "reason": "Gateway Rejected / Bad Request"}
        if self.place_mode == "retried_twice":
            self._new(symbol, side, quantity, order_type)
            self._new(symbol, side, quantity, order_type)  # tradier_api._request re-POSTed after a timeout
            return {"status": "error", "reason": "Gateway Rejected / Bad Request"}
        if self.place_mode == "reject":
            return {"errors": {"error": ["Backoffice rejected override"]}}
        o = self._new(symbol, side, quantity, order_type)
        return {"order": {"id": o["id"], "status": "ok"}}

    async def cancel_order(self, account_key, order_id):
        for o in self.orders:
            if o["id"] == order_id and o["status"] in ("open", "partially_filled", "pending"):
                o["status"] = "canceled"
        return {"order": {"id": order_id, "status": "ok"}}

    async def get_order_status(self, account_key, order_id):
        for o in self.orders:
            if o["id"] == order_id:
                return dict(o)
        return {}

    def set(self, oid, **kw):
        for o in self.orders:
            if o["id"] == oid:
                o.update(kw)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    broker = FakeTradier(clock)
    guard = odg.TradierDedupeGuard("trb", ledger_dir=tmp_path, clock=clock, cfg=_DEDUPE_ONLY)
    client = odg.GuardedTradierClient(broker, guard)
    return broker, guard, client, clock, tmp_path


def run(coro):
    return asyncio.run(coro)


def place(client, side="buy", qty=10, typ="market", symbol="AAPL"):
    return run(client.place_order(account_key="trb", symbol=symbol, side=side, quantity=qty, order_type=typ))


def test_allowed_when_everything_final(env):
    broker, guard, client, clock, _ = env
    r = place(client)
    oid = r["order"]["id"]
    assert len(broker.placed) == 1
    broker.set(oid, status="filled", exec_quantity=10.0)
    clock.t += 31
    place(client, side="sell")
    assert len(broker.placed) == 2
    first = guard.ledger.orders("AAPL")[0]
    assert first["final"] and first["status"] == "filled" and first["executed_qty"] == 10.0


def test_refused_when_open_order_exists(env):
    broker, guard, client, clock, tmp = env
    broker._new("AAPL", "buy", 5, "limit", status="partially_filled")
    r = place(client)
    assert "ORDER_DEDUPE_BLOCK:OPEN_ORDER_EXISTS" in r["errors"]["error"][0]
    assert broker.placed == []
    ev = json.loads((tmp / "order_dedupe_blocks.jsonl").read_text().splitlines()[-1])
    assert ev["evidence"]["open_orders"][0]["status"] == "partially_filled"


def test_other_symbol_open_order_does_not_block(env):
    broker, guard, client, clock, _ = env
    broker._new("MSFT", "buy", 5, "limit", status="open")
    place(client)
    assert len(broker.placed) == 1


def test_emergency_close_also_refused(env):
    broker, guard, client, clock, _ = env
    broker._new("AAPL", "buy", 5, "limit", status="pending")
    r = place(client, side="sell")
    assert r["order_dedupe_block"]["code"] == "OPEN_ORDER_EXISTS"
    assert broker.placed == []


def test_refused_when_broker_query_fails(env):
    broker, guard, client, clock, _ = env
    broker.list_fail = True  # TradierAPIClient.get_orders would have returned [] here (fail-open in old check)
    r = place(client)
    assert r["order_dedupe_block"]["code"] == "BROKER_UNREACHABLE_ORDERS"
    assert broker.placed == []


def test_refused_when_last_order_non_final(env):
    broker, guard, client, clock, _ = env
    r = place(client, typ="market")
    oid = r["order"]["id"]
    broker.orders = []  # day listing rolled / missing it — guard must still query the id
    broker.orders_hidden = True
    clock.t += 3
    # by-id lookup also misses -> unknown -> refuse
    r2 = place(client, side="sell")
    assert r2["order_dedupe_block"]["code"] == "LAST_ORDER_STATUS_UNKNOWN"
    assert len(broker.placed) == 1
    _ = oid


def test_last_order_open_status_refused(env):
    broker, guard, client, clock, _ = env
    r = place(client, typ="limit")
    oid = r["order"]["id"]
    broker.set(oid, status="pending")
    r2 = place(client, side="sell")
    assert r2["order_dedupe_block"]["code"] == "OPEN_ORDER_EXISTS"
    assert len(broker.placed) == 1


def test_gateway_error_after_accept_freezes_key_and_adopts(env):
    broker, guard, client, clock, tmp = env
    broker.place_mode = "gateway_error"
    place(client)
    assert guard.ledger.orders("AAPL")[-1]["state"] == "UNKNOWN"
    assert odg.ledger_has_nonfinal("tradier", "trb", "AAPL", ledger_dir=tmp) is True
    broker.place_mode = "ok"
    clock.t += 3
    r = place(client)
    assert r["order_dedupe_block"]["code"] == "OPEN_ORDER_EXISTS"  # the "lost" order is working at the broker
    oid = broker.orders[0]["id"]
    broker.set(oid, status="filled", exec_quantity=10.0)
    clock.t += 3
    r2 = place(client)  # same side, broker-confirmed fill of the full intent -> duplicate refused
    assert r2["order_dedupe_block"]["code"] == "INTENT_ALREADY_FILLED"
    row = guard.ledger.orders("AAPL")[0]
    assert row["order_id"] == oid and row["adopted"] and row["final"]
    assert len(broker.placed) == 1


def test_retried_post_double_order_detected(env):
    broker, guard, client, clock, _ = env
    broker.place_mode = "retried_twice"
    place(client)
    for o in broker.orders:
        o.update(status="filled", exec_quantity=10.0)
    clock.t += 3
    r = place(client)
    assert r["order_dedupe_block"]["code"] == "INTENT_ALREADY_FILLED"
    row = guard.ledger.orders("AAPL")[0]
    assert row["adopted_extra"]  # second broker order recorded as evidence of the transport re-POST


def test_place_exception_freezes_key_across_restart(env):
    broker, guard, client, clock, tmp = env
    broker.place_mode = "raise"
    with pytest.raises(asyncio.TimeoutError):
        place(client)
    assert odg.ledger_has_nonfinal("tradier", "trb", "AAPL", ledger_dir=tmp) is True
    guard2 = odg.TradierDedupeGuard("trb", ledger_dir=tmp, clock=clock, cfg=_DEDUPE_ONLY)
    client2 = odg.GuardedTradierClient(broker, guard2)
    broker.place_mode = "ok"
    clock.t += 5
    r = place(client2)
    assert r["order_dedupe_block"]["code"] == "LAST_ORDER_STATUS_UNKNOWN"
    clock.t += 61  # broker listing confirms nothing was created
    place(client2, side="sell")
    assert len(broker.placed) == 2
    assert guard2.ledger.orders("AAPL")[0]["status"] == "not_found"


def test_definitive_reject_is_final(env):
    broker, guard, client, clock, _ = env
    broker.place_mode = "reject"
    place(client)
    assert guard.ledger.orders("AAPL")[-1]["status"] == "rejected_by_broker"
    broker.place_mode = "ok"
    place(client)
    assert len(broker.placed) == 2


def test_chase_replace_waits_for_cancel_and_is_clamped(env, monkeypatch):
    broker, guard, client, clock, _ = env
    r1 = place(client, typ="limit", qty=10)
    oid = r1["order"]["id"]
    st = run(client.get_order_status("trb", oid))
    assert st["status"] == "open"
    broker.set(oid, exec_quantity=4.0, status="partially_filled")  # fills between status read and cancel
    run(client.cancel_order("trb", oid))
    clock.t += 1
    place(client, typ="limit", qty=10)  # chase's stale remainder (10 - 0 read before the partial)
    assert broker.placed[-1][2] == 6.0


def test_chase_replace_refused_when_cancel_not_confirmed(env, monkeypatch):
    broker, guard, client, clock, _ = env
    r1 = place(client, typ="limit", qty=10)
    oid = r1["order"]["id"]

    async def no_cancel(account_key, order_id):  # broker never confirms the cancel
        return {}

    monkeypatch.setattr(broker, "cancel_order", no_cancel)

    async def fast_sleep(s):
        clock.t += s

    monkeypatch.setattr(odg.asyncio, "sleep", fast_sleep)
    run(client.cancel_order("trb", oid))
    r2 = place(client, typ="market", qty=10)
    assert r2["order_dedupe_block"]["code"] == "OPEN_ORDER_EXISTS"
    assert len(broker.placed) == 1


def test_execute_now_preflight_helper(env):
    broker, guard, client, clock, _ = env
    d = run(odg.tradier_execute_now_preflight(client, "trb", "AAPL", origin="VEC_EXACT_OPEN"))
    assert d.allowed
    broker._new("AAPL", "sell", 3, "market", status="open")
    d2 = run(odg.tradier_execute_now_preflight(client, "trb", "AAPL", origin="VEC_DRIVEN_CLOSE"))
    assert not d2.allowed and d2.code == "OPEN_ORDER_EXISTS"


def test_options_not_guarded_are_refused(env):
    broker, guard, client, clock, _ = env
    r = run(client.place_option_order("trb", "AAPL", "AAPL261016C00200000", "buy_to_open", 1))
    assert "ORDER_DEDUPE_BLOCK" in r["errors"]["error"][0]


def test_proxy_is_transparent(env):
    broker, guard, client, clock, _ = env
    assert client._current_id == "VA123"
    assert odg.wrap_tradier_client(client, "trb") is client


def test_own_stale_order_cancel_then_confirm_next_cycle(env):
    broker, guard, client, clock, _ = env
    r1 = place(client, typ="limit")
    clock.t += 121
    r2 = place(client, side="sell")
    assert r2["order_dedupe_block"]["evidence"]["auto_cancel"]["id"] == r1["order"]["id"]
    assert len(broker.placed) == 1
    clock.t += 2.5
    place(client, side="sell")
    assert len(broker.placed) == 2


def test_foreign_open_order_never_auto_cancelled(env):
    broker, guard, client, clock, _ = env
    o = broker._new("AAPL", "buy", 5, "limit", status="open")
    clock.t += 10_000
    r = place(client)
    assert r["order_dedupe_block"]["code"] == "OPEN_ORDER_EXISTS"
    assert o["status"] == "open"

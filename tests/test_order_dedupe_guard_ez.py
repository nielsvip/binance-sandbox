"""ORDER_DEDUPE_GUARD — Binance futures (ez_manage) path. Mocked broker only; no network, no live orders."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import order_dedupe_guard as odg  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_DEDUPE_ONLY = SimpleNamespace(WIRE_EXPOSURE_GATE_ENABLED=False, WIRE_REQUIRE_EXECUTE_NOW_ALL=False, WIRE_FILE_REFLECTION_ENABLED=False)  # these tests isolate the dedupe layer; exposure gate: tests/test_positions_revamp_incident.py
from binance.exceptions import BinanceAPIException  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _api_exc(code, status=400, msg="x"):
    return BinanceAPIException(None, status, json.dumps({"code": code, "msg": msg}))


class FakeBinance:
    """Minimal python-binance Client double. Records every create call."""

    def __init__(self):
        self.open_orders = []
        self.orders = {}  # orderId -> dict
        self.by_cid = {}
        self.created = []
        self.next_id = 100
        self.open_orders_exc = None
        self.get_order_exc = None
        self.create_exc = None
        self.create_status = "NEW"
        self.session = "SESSION"

    def futures_get_open_orders(self, symbol=None):
        if self.open_orders_exc:
            raise self.open_orders_exc
        return [o for o in self.open_orders if symbol is None or o["symbol"] == symbol]

    def futures_get_order(self, symbol=None, orderId=None, origClientOrderId=None):
        if self.get_order_exc:
            raise self.get_order_exc
        o = self.orders.get(orderId) if orderId is not None else self.by_cid.get(origClientOrderId)
        if o is None:
            raise _api_exc(-2013, 400, "Order does not exist.")
        return dict(o)

    def futures_create_order(self, **p):
        self.created.append(dict(p))
        if self.create_exc:
            exc, self.create_exc = self.create_exc, None
            raise exc
        oid = self.next_id
        self.next_id += 1
        o = {"orderId": oid, "clientOrderId": p.get("newClientOrderId"), "symbol": p["symbol"], "side": p["side"], "positionSide": p.get("positionSide"), "type": p.get("type"), "status": self.create_status, "origQty": p.get("quantity"), "executedQty": "0"}
        if self.create_status == "FILLED":
            o["executedQty"] = p.get("quantity")
        self.orders[oid] = o
        self.by_cid[o["clientOrderId"]] = o
        if self.create_status in ("NEW", "PARTIALLY_FILLED"):
            self.open_orders.append(o)
        return dict(o)

    def futures_cancel_order(self, symbol=None, orderId=None, origClientOrderId=None):
        o = self.orders[orderId]
        o["status"] = "CANCELED"
        self.open_orders = [x for x in self.open_orders if x["orderId"] != orderId]
        return dict(o)

    def fill(self, oid, qty=None):
        o = self.orders[oid]
        o["status"] = "FILLED"
        o["executedQty"] = qty if qty is not None else o["origQty"]
        self.open_orders = [x for x in self.open_orders if x["orderId"] != oid]


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    broker = FakeBinance()
    guard = odg.BinanceDedupeGuard("men", ledger_dir=tmp_path, clock=clock, cfg=_DEDUPE_ONLY)
    client = odg.GuardedBinanceClient(broker, guard)
    return broker, guard, client, clock, tmp_path


def _create(client, side="BUY", ps="LONG", qty="0.010", typ="MARKET"):
    return client.futures_create_order(symbol="BTCUSDT", side=side, positionSide=ps, type=typ, quantity=qty)


def test_allowed_when_everything_final(env):
    broker, guard, client, clock, _ = env
    resp = _create(client)
    assert len(broker.created) == 1
    assert broker.created[0]["newClientOrderId"].startswith("odg")
    rows = guard.ledger.orders("BTCUSDT")
    assert rows[-1]["order_id"] == resp["orderId"] and rows[-1]["state"] == "SUBMITTED" and not rows[-1]["final"]
    broker.fill(resp["orderId"])
    clock.t += 31  # outside intent window: a new, independent decision
    _create(client, side="SELL")
    assert len(broker.created) == 2
    assert guard.ledger.orders("BTCUSDT")[0]["final"] and guard.ledger.orders("BTCUSDT")[0]["status"] == "FILLED"


def test_refused_when_open_order_exists(env):
    broker, guard, client, clock, tmp = env
    broker.open_orders.append({"orderId": 9, "symbol": "BTCUSDT", "side": "BUY", "positionSide": "LONG", "status": "NEW", "type": "LIMIT"})
    with pytest.raises(odg.OrderDedupeBlocked) as ei:
        _create(client)
    assert isinstance(ei.value, BinanceAPIException) and ei.value.code == odg.DEDUPE_BLOCK_BINANCE_CODE
    assert ei.value.decision.code == "OPEN_ORDER_EXISTS"
    assert broker.created == []
    blocks = (tmp / "order_dedupe_blocks.jsonl").read_text().strip().splitlines()
    assert json.loads(blocks[-1])["evidence"]["open_orders"][0]["orderId"] == 9


def test_open_order_on_other_position_side_blocks_in_symbol_scope(env):
    broker, guard, client, clock, _ = env
    broker.open_orders.append({"orderId": 9, "symbol": "BTCUSDT", "side": "SELL", "positionSide": "SHORT", "status": "PARTIALLY_FILLED", "type": "LIMIT"})
    with pytest.raises(odg.OrderDedupeBlocked):
        _create(client, side="SELL", ps="LONG")  # emergency close of LONG while a SHORT order is working
    assert broker.created == []


def test_emergency_close_also_refused(env):
    broker, guard, client, clock, _ = env
    broker.open_orders.append({"orderId": 9, "symbol": "BTCUSDT", "side": "SELL", "positionSide": "LONG", "status": "NEW", "type": "LIMIT"})
    with pytest.raises(odg.OrderDedupeBlocked):
        client.futures_create_order(symbol="BTCUSDT", side="SELL", positionSide="LONG", type="MARKET", quantity="0.010", newClientOrderId="EMERGENCY_LIQ_CLOSE")
    assert broker.created == []


def test_refused_when_last_order_non_final(env):
    broker, guard, client, clock, _ = env
    resp = _create(client)
    broker.open_orders.clear()  # not visible in openOrders (e.g. lagging), but its own status is still NEW
    with pytest.raises(odg.OrderDedupeBlocked) as ei:
        _create(client, side="SELL")
    assert ei.value.decision.code == "LAST_ORDER_NOT_FINAL"
    assert ei.value.decision.evidence["broker"]["status"] == "NEW"
    assert len(broker.created) == 1
    _ = resp


def test_refused_when_open_orders_query_fails(env):
    broker, guard, client, clock, _ = env
    broker.open_orders_exc = ConnectionError("network down")
    with pytest.raises(odg.OrderDedupeBlocked) as ei:
        _create(client)
    assert ei.value.decision.code == "BROKER_UNREACHABLE_OPEN_ORDERS"
    assert broker.created == []


def test_refused_when_status_query_fails(env):
    broker, guard, client, clock, _ = env
    _create(client)
    broker.open_orders.clear()
    broker.get_order_exc = TimeoutError("read timeout")
    clock.t += 5
    with pytest.raises(odg.OrderDedupeBlocked) as ei:
        _create(client, side="SELL")
    assert ei.value.decision.code == "LAST_ORDER_STATUS_UNKNOWN"
    assert len(broker.created) == 1


def test_submit_exception_freezes_key_across_restart(env):
    broker, guard, client, clock, tmp = env
    broker.create_exc = TimeoutError("ReadTimeout after request sent")
    with pytest.raises(TimeoutError):
        _create(client)
    rows = guard.ledger.orders("BTCUSDT")
    assert rows[-1]["state"] == "UNKNOWN" and not rows[-1]["final"]
    assert odg.ledger_has_nonfinal("binance", "men", "BTCUSDT", ledger_dir=tmp) is True
    # the order DID reach the exchange (classic timeout-after-accept); visible only by clientOrderId
    cid = broker.created[0]["newClientOrderId"]
    broker.by_cid[cid] = {"orderId": 555, "clientOrderId": cid, "symbol": "BTCUSDT", "status": "NEW", "executedQty": "0"}
    broker.orders[555] = broker.by_cid[cid]
    # "restart": brand-new guard + client over the same persisted ledger
    guard2 = odg.BinanceDedupeGuard("men", ledger_dir=tmp, clock=clock, cfg=_DEDUPE_ONLY)
    client2 = odg.GuardedBinanceClient(broker, guard2)
    clock.t += 5
    with pytest.raises(odg.OrderDedupeBlocked) as ei:
        _create(client2)
    assert ei.value.decision.code == "LAST_ORDER_NOT_FINAL"
    assert len(broker.created) == 1
    broker.by_cid[cid]["status"] = "FILLED"
    broker.by_cid[cid]["executedQty"] = "0.010"
    clock.t += 2.5  # past block backoff
    with pytest.raises(odg.OrderDedupeBlocked) as ei2:
        _create(client2)  # same side within 30s of a confirmed full fill = duplicate
    assert ei2.value.decision.code in ("INTENT_ALREADY_FILLED",)
    clock.t += 40
    _create(client2)
    assert len(broker.created) == 2
    assert odg.ledger_has_nonfinal("binance", "men", "BTCUSDT", ledger_dir=tmp) is True  # the new order is live again


def test_not_found_only_final_after_grace(env):
    broker, guard, client, clock, tmp = env
    broker.create_exc = ConnectionError("connection reset before send")
    with pytest.raises(ConnectionError):
        _create(client)
    broker.created.clear()
    clock.t += 10
    with pytest.raises(odg.OrderDedupeBlocked) as ei:
        _create(client)
    assert ei.value.decision.code == "LAST_ORDER_STATUS_UNKNOWN"
    clock.t += 61
    _create(client, side="SELL")
    assert len(broker.created) == 1
    first = guard.ledger.orders("BTCUSDT")[0]
    assert first["status"] == "NOT_FOUND" and first["final"]


def test_definitive_broker_reject_is_final(env):
    broker, guard, client, clock, _ = env
    broker.create_exc = _api_exc(-2019, 400, "Margin is insufficient.")
    with pytest.raises(BinanceAPIException):
        _create(client)
    assert guard.ledger.orders("BTCUSDT")[-1]["status"] == "REJECTED_BY_BROKER"
    _create(client)
    assert len(broker.created) == 2


def test_unknown_code_is_not_a_reject(env):
    broker, guard, client, clock, _ = env
    broker.create_exc = _api_exc(-1007, 408, "Timeout waiting for response from backend server. Send status unknown")
    with pytest.raises(BinanceAPIException):
        _create(client)
    assert guard.ledger.orders("BTCUSDT")[-1]["state"] == "UNKNOWN"
    with pytest.raises(odg.OrderDedupeBlocked):
        _create(client)


def test_chase_cancel_replace_is_clamped_to_broker_confirmed_remainder(env):
    broker, guard, client, clock, _ = env
    r1 = _create(client, typ="LIMIT", qty="0.010")
    broker.orders[r1["orderId"]]["executedQty"] = "0.004"  # partial fill races the cancel
    client.futures_cancel_order(symbol="BTCUSDT", orderId=r1["orderId"])
    clock.t += 1
    _create(client, typ="LIMIT", qty="0.010")  # caller's stale remainder (missed the partial)
    assert broker.created[-1]["quantity"] == "0.006"


def test_runaway_market_after_unseen_fill_is_refused(env):
    broker, guard, client, clock, _ = env
    r1 = _create(client, typ="LIMIT", qty="0.010")
    broker.fill(r1["orderId"])  # filled; caller's cancel would fail with -2011 and it fires MARKET anyway
    clock.t += 1
    with pytest.raises(odg.OrderDedupeBlocked) as ei:
        _create(client, typ="MARKET", qty="0.010")
    assert ei.value.decision.code == "INTENT_ALREADY_FILLED"
    assert len(broker.created) == 1


def test_own_cancel_confirm_wait_then_allowed(env, monkeypatch):
    broker, guard, client, clock, _ = env
    r1 = _create(client, typ="LIMIT")
    calls = {"n": 0}
    real = broker.futures_get_open_orders

    def lagging(symbol=None):  # openOrders still shows OUR cancelled order once
        calls["n"] += 1
        if calls["n"] == 1:
            return [dict(broker.orders[r1["orderId"]], status="NEW")]
        return real(symbol)

    client.futures_cancel_order(symbol="BTCUSDT", orderId=r1["orderId"])
    monkeypatch.setattr(broker, "futures_get_open_orders", lagging)
    monkeypatch.setattr(odg.time, "sleep", lambda s: None)
    clock.t += 0.1
    _create(client, side="SELL", typ="LIMIT")
    assert len(broker.created) == 2 and calls["n"] >= 2


def test_foreign_open_order_never_waited_on(env, monkeypatch):
    broker, guard, client, clock, _ = env
    broker.open_orders.append({"orderId": 77, "symbol": "BTCUSDT", "side": "BUY", "positionSide": "LONG", "status": "NEW", "type": "LIMIT"})
    slept = []
    monkeypatch.setattr(odg.time, "sleep", lambda s: slept.append(s))
    with pytest.raises(odg.OrderDedupeBlocked):
        _create(client)
    assert slept == []


def test_block_backoff_avoids_rest_hammering(env):
    broker, guard, client, clock, _ = env
    broker.open_orders_exc = ConnectionError("down")
    n = {"c": 0}
    orig = broker.futures_get_open_orders

    def counting(symbol=None):
        n["c"] += 1
        return orig(symbol)

    broker.futures_get_open_orders = counting
    for _ in range(5):
        with pytest.raises(odg.OrderDedupeBlocked):
            _create(client)
    assert n["c"] == 1


def test_corrupt_ledger_fails_closed(env):
    broker, guard, client, clock, tmp = env
    guard.ledger.path.write_text("{not json")
    with pytest.raises(odg.OrderDedupeBlocked) as ei:
        _create(client)
    assert ei.value.decision.code == "GUARD_INTERNAL_ERROR"
    assert odg.ledger_has_nonfinal("binance", "men", "BTCUSDT", ledger_dir=tmp) is True


def test_proxy_is_transparent(env):
    broker, guard, client, clock, _ = env
    assert client.session == "SESSION"
    client.session = "NEW"
    assert broker.session == "NEW"
    assert odg.wrap_binance_client(client, "men") is client


def test_execute_now_preflight_helper(env):
    broker, guard, client, clock, _ = env
    d = asyncio.run(odg.binance_execute_now_preflight(client, "men", "BTCUSDT", "LONG", origin="VEC_EXACT_OPEN"))
    assert d.allowed
    broker.open_orders.append({"orderId": 9, "symbol": "BTCUSDT", "side": "BUY", "positionSide": "LONG", "status": "NEW", "type": "LIMIT"})
    d2 = asyncio.run(odg.binance_execute_now_preflight(client, "men", "BTCUSDT", "LONG", origin="VEC_DRIVEN_OPEN"))
    assert not d2.allowed and d2.code == "OPEN_ORDER_EXISTS"


def test_no_client_fails_closed(tmp_path):
    g = odg.BinanceDedupeGuard("men", ledger_dir=tmp_path)
    assert not g.preflight(None, "BTCUSDT").allowed


def test_batch_orders_refused(env):
    broker, guard, client, clock, _ = env
    with pytest.raises(odg.OrderDedupeBlocked):
        client.futures_place_batch_order(symbol="BTCUSDT", batchOrders=[])


def test_own_stale_order_cancel_then_confirm_next_cycle(env):
    broker, guard, client, clock, _ = env
    r1 = _create(client, typ="LIMIT")  # e.g. untracked after a submit timeout; stays resting
    clock.t += 121
    with pytest.raises(odg.OrderDedupeBlocked) as ei:
        _create(client, side="SELL")  # emergency close: cancel is SENT but this call still refuses to place
    assert ei.value.decision.evidence["auto_cancel"]["broker_status"] == "CANCELED"
    assert len(broker.created) == 1
    clock.t += 2.5
    _create(client, side="SELL")  # next cycle: broker confirms CANCELED (qty known) -> allowed
    assert len(broker.created) == 2
    assert guard.ledger.orders("BTCUSDT")[0]["status"] == "CANCELED"
    _ = r1


def test_foreign_open_order_never_auto_cancelled(env):
    broker, guard, client, clock, _ = env
    broker.open_orders.append({"orderId": 77, "clientOrderId": "web_manual", "symbol": "BTCUSDT", "side": "BUY", "positionSide": "LONG", "status": "NEW", "type": "LIMIT"})
    broker.orders[77] = broker.open_orders[0]
    clock.t += 10_000
    with pytest.raises(odg.OrderDedupeBlocked):
        _create(client)
    assert broker.orders[77]["status"] == "NEW"

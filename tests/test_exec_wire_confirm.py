"""test_exec_wire_confirm — broker-confirmation wire guard (ez_manage).

USER 2026-10-07: broker confirmation is the ONLY order criterion (no clock). Pins:
sanction/refuse lifecycle, post-wire verdicts (unconfirmed->None, unsanctioned->BYPASS),
retire-on-terminal, and verify-then-clear (never erase blind).
"""
import asyncio
import os
import sys
from collections import defaultdict
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ez_manage as E  # noqa: E402

PK = "TEST_WIRE:BTCUSDT_LONG"


def _reset():
    E._EXEC_WIRE_SANCTIONS.pop(PK, None)


def test_sanction_issue_refuse_reissue_cycle():
    _reset()
    s1 = E._sanction_execution(PK, "OPEN", "SELL", 1.0, "t")
    assert isinstance(s1, int) and s1 > 0
    out = E._confirm_wire_result("test", PK, {"orderId": "111", "status": "NEW"}, s1, "OPEN", "t")
    assert out == "111"
    assert E._pending_unconfirmed(PK) == ["111"]
    assert E._sanction_execution(PK, "OPEN", "SELL", 1.0, "t") is None
    E._retire_wire_order(PK, "111", "EXECUTED")
    assert E._pending_unconfirmed(PK) == []
    s2 = E._sanction_execution(PK, "OPEN", "SELL", 1.0, "t")
    assert isinstance(s2, int) and s2 > s1
    _reset()


def test_confirm_unconfirmed_never_verdict():
    _reset()
    s = E._sanction_execution(PK, "OPEN", "SELL", 1.0, "t")
    assert E._confirm_wire_result("test", PK, {}, s) is None
    assert E._confirm_wire_result("test", PK, None, s) is None
    assert E._confirm_wire_result("test", PK, {"orderId": "?"}, s) is None
    assert E._confirm_wire_result("test", PK, {"status": "NEW"}, s) is None
    assert E._pending_unconfirmed(PK) == []
    _reset()


def test_confirm_unsanctioned_trips_bypass():
    _reset()
    assert E._confirm_wire_result("rogue", PK, {"orderId": "222", "status": "NEW"}, 999999) == "BYPASS"
    assert E._pending_unconfirmed(PK) == ["222"]
    _reset()


def test_confirm_seq_mismatch_trips():
    _reset()
    s = E._sanction_execution(PK, "OPEN", "SELL", 1.0, "t")
    assert E._confirm_wire_result("test", PK, {"orderId": "333", "status": "NEW"}, s + 100) == "BYPASS"
    _reset()


def test_confirm_terminal_at_response_binds_nothing():
    _reset()
    s = E._sanction_execution(PK, "OPEN", "SELL", 1.0, "t")
    assert E._confirm_wire_result("test", PK, {"orderId": "444", "status": "FILLED"}, s) == "444"
    assert E._pending_unconfirmed(PK) == []
    s = E._sanction_execution(PK, "OPEN", "SELL", 1.0, "t")
    assert E._confirm_wire_result("test", PK, {"orderId": "445", "status": "CANCELED"}, s) == "445"
    assert E._pending_unconfirmed(PK) == []
    s = E._sanction_execution(PK, "OPEN", "SELL", 1.0, "t")
    assert E._confirm_wire_result("test", PK, {"orderId": "446", "status": "REJECTED"}, s) == "446"
    assert E._pending_unconfirmed(PK) == []
    _reset()


def test_retire_unknown_safe():
    _reset()
    E._retire_wire_order(PK, "nope", "CANCELED")
    E._retire_wire_order(None, None, "x")
    assert E._pending_unconfirmed(PK) == []


def test_guard_disabled_fail_open(monkeypatch):
    _reset()
    monkeypatch.setattr(E, "_wire_guard_enabled", lambda: False)
    assert E._sanction_execution(PK, "OPEN", "SELL", 1.0, "t") == 0
    assert E._confirm_wire_result("test", PK, {"orderId": "555", "status": "NEW"}, 1) is None
    assert E._pending_unconfirmed(PK) == []
    _reset()


def _fake_manager(cancel_resp="CANCELED", cancel_exc=None, query_exc=None):
    async def _noop_lock(*a, **k):
        return None

    class FakeClient:
        def futures_cancel_order(self, symbol, orderId):
            if cancel_exc is not None:
                raise cancel_exc
            return {"status": cancel_resp, "orderId": orderId} if isinstance(cancel_resp, str) else cancel_resp

        def futures_get_order(self, symbol, orderId):
            if query_exc is not None:
                raise query_exc
            return {"status": "CANCELED", "orderId": orderId}

    ns = SimpleNamespace(
        dedupe_lock=asyncio.Lock(),
        active_maker_orders={PK: {"order_id": 777, "start_time": 0.0, "status": "active"}},
        managed_maker_order_registry={"777": {"position_key": PK}},
        maker_price_retry_count={},
        price_band_tracked_order_ids=defaultdict(set),
        accounts={"TEST_WIRE": SimpleNamespace(client=FakeClient())},
        _track_price_band_order=lambda *a: None,
    )
    ns._untrack_price_band_order = E.MultiAccountTradeManager._untrack_price_band_order.__get__(ns)
    return ns


def test_clear_verified_cancel_pops_and_retires():
    _reset()
    E._EXEC_WIRE_SANCTIONS[PK] = {"seq": 1, "action": "OPEN", "side": "SELL", "orders": {"777": {"state": "OPEN", "site": "t"}}}
    ns = _fake_manager(cancel_resp="CANCELED")
    asyncio.run(E.MultiAccountTradeManager.clear_active_maker_order(ns, PK))
    assert PK not in ns.active_maker_orders
    assert E._pending_unconfirmed(PK) == []
    _reset()


def test_clear_ambiguous_keeps_pending():
    _reset()
    E._EXEC_WIRE_SANCTIONS[PK] = {"seq": 1, "action": "OPEN", "side": "SELL", "orders": {"777": {"state": "OPEN", "site": "t"}}}
    ns = _fake_manager(cancel_exc=TimeoutError("t"), query_exc=TimeoutError("t"))
    asyncio.run(E.MultiAccountTradeManager.clear_active_maker_order(ns, PK))
    assert PK in ns.active_maker_orders
    assert E._pending_unconfirmed(PK) == ["777"]
    _reset()


def test_clear_confirmed_how_skips_broker():
    _reset()
    E._EXEC_WIRE_SANCTIONS[PK] = {"seq": 1, "action": "OPEN", "side": "SELL", "orders": {"777": {"state": "OPEN", "site": "t"}}}

    class ExplodingClient:
        def futures_cancel_order(self, *a, **k):
            raise AssertionError("broker must not be touched")

        def futures_get_order(self, *a, **k):
            raise AssertionError("broker must not be touched")

    ns = _fake_manager()
    ns.accounts["TEST_WIRE"].client = ExplodingClient()
    asyncio.run(E.MultiAccountTradeManager.clear_active_maker_order(ns, PK, confirmed_how="EXECUTED"))
    assert PK not in ns.active_maker_orders
    assert E._pending_unconfirmed(PK) == []
    _reset()


def test_cancel_and_clear_ambiguous_returns_false_and_keeps():
    _reset()
    E._EXEC_WIRE_SANCTIONS[PK] = {"seq": 1, "action": "OPEN", "side": "SELL", "orders": {"777": {"state": "OPEN", "site": "t"}}}
    ns = _fake_manager(cancel_exc=TimeoutError("t"), query_exc=TimeoutError("t"))
    out = asyncio.run(E.MultiAccountTradeManager.cancel_and_clear_active_maker_order(ns, PK))
    assert out is False
    assert PK in ns.active_maker_orders
    _reset()

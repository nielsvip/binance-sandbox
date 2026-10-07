"""Durable regression for Finandy dead-hand: foothold deleted, entire order only via maker→MARKET.

Covers 2026-09-10 behavior change:
- send_foothold_webhook deleted (no small / reason slice)
- place_maker_order sends entire qty_abs in one maker order (no foothold subtraction, no FOOTHOLD_ONLY early return)
- place_maker_order TIMEOUT is 30.0 (≥30s before fallback)
- send_webhook replaced by direct Binance MARKET (quantized, ORDER_TYPE_MARKET) with no Finandy webhook POST
- execute_now always calls place_maker_order first (reduce + augment)
"""
from __future__ import annotations
import asyncio
import inspect
import pathlib
from decimal import Decimal
from unittest import mock

import ez_manage
from ez_manage import MultiAccountTradeManager


def test_foothold_webhook_deleted():
    assert not hasattr(MultiAccountTradeManager, "send_foothold_webhook"), "send_foothold_webhook must be deleted"
    # also ensure no reference remains in place_maker_order
    src = inspect.getsource(MultiAccountTradeManager.place_maker_order)
    assert "send_foothold_webhook" not in src
    assert "foothold_qty" not in src
    assert "FOOTHOLD DELETED" in src


def test_place_maker_sends_entire_order_no_split():
    src = inspect.getsource(MultiAccountTradeManager.place_maker_order)
    assert "TIMEOUT = 30.0" in src
    assert "qty_abs = qty_abs - foothold_qty" not in src
    assert "FOOTHOLD_ONLY" not in src
    assert "FOOTHOLD_NEARLY_ONLY" not in src
    # entire order path still quantizes full qty_abs
    assert 'tick = Decimal(str(symbol_conf["tick_size"]))' in src


def test_send_webhook_is_direct_market_quantized():
    src = inspect.getsource(MultiAccountTradeManager.send_webhook)
    assert "direct_market:send_webhook" in src
    assert "ORDER_TYPE_MARKET" in src
    assert "futures_create_order" in src
    # old Finandy POST must be commented only
    live_finandy = [l for l in src.splitlines() if "finandy_webhook" in l and not l.strip().startswith("#")]
    assert live_finandy == []


def test_execute_now_always_maker_before_market():
    src = inspect.getsource(MultiAccountTradeManager.execute_now)
    assert "Maker ALWAYS first" in src
    live_force = [l for l in src.splitlines() if "_force_webhook_reduces = True" in l and not l.strip().startswith("#")]
    assert live_force == []
    assert src.count("await self.place_maker_order") >= 2


def test_send_webhook_market_quantization_via_mock():
    mgr = object.__new__(MultiAccountTradeManager)
    mgr.accounts = {}
    mgr.positions_by_account = {}
    mgr.get_symbol_config = lambda sym: {"step_size": "0.001", "tick_size": "0.01"}
    async def dummy_load(): return None
    mgr.load_symbol_configs = dummy_load
    mgr.is_same_direction = lambda s, ps: True
    mgr.hedge_engine = None
    mgr.positions_service = None
    mgr._recent_order_reasons = {}
    mgr._webhook_semaphore = asyncio.Semaphore(5)
    mgr.get_cached_open_orders = mock.AsyncMock(return_value=[])

    orig_sandbox = ez_manage.is_sandbox_account
    orig_safe = ez_manage.safe_check_server_heartbeat
    orig_parse = ez_manage.parse_position_key
    orig_load = ez_manage.load_accounts
    orig_verify = ez_manage.verify_trade_via_websocket
    orig_env = ez_manage.current_env.copy()
    try:
        ez_manage.is_sandbox_account = lambda cfg, ak: False
        async def fake_safe(ak): return False
        ez_manage.safe_check_server_heartbeat = fake_safe
        ez_manage.parse_position_key = lambda pk: ("test", "BTCUSDC", "LONG")
        ez_manage.load_accounts = lambda cfg: {}
        async def fake_verify(*a, **kw): return False
        ez_manage.verify_trade_via_websocket = fake_verify
        ez_manage.current_env = {"env": "server"}

        class MockClient:
            def __init__(self): self.orders = []
            def futures_create_order(self, **kw):
                self.orders.append(kw)
                assert kw["type"] == "MARKET"
                q = Decimal(kw["quantity"])
                assert q == (q // Decimal("0.001")) * Decimal("0.001")
                return {"orderId": 1, "status": "FILLED", "avgPrice": "1"}
            def futures_get_open_orders(self, **kw): return []
            def futures_cancel_order(self, **kw): return {"status": "CANCELED"}

        mc = MockClient()
        class FakeAcc:
            def __init__(self, c): self.client = c
        mgr.accounts["test"] = FakeAcc(mc)

        async def run():
            ok = await mgr.send_webhook("test:BTCUSDC_LONG", "test", "BTCUSDC", 1.0, 0.5678, 50000, "SELL", "LONG", "uid", False, "REDUCE_TEST")
            assert ok is True
            assert mc.orders[0]["quantity"] == "0.567"
            assert mc.orders[0]["type"] == "MARKET"

        asyncio.run(run())
    finally:
        ez_manage.is_sandbox_account = orig_sandbox
        ez_manage.safe_check_server_heartbeat = orig_safe
        ez_manage.parse_position_key = orig_parse
        ez_manage.load_accounts = orig_load
        ez_manage.verify_trade_via_websocket = orig_verify
        ez_manage.current_env = orig_env

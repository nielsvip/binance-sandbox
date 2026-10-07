"""tests/test_order_queue_stall_skip.py — queue-consumer stall regression.

2026-10-06 (USER immediate-repair 2026-10-05): Oct 5 trb/trc consumers wedged
inside ONE unbounded execute_trade_action await (ORDER_DEBUG with zero
ORDER_RESULT all Monday) while the event loop stayed alive. handle_order now
bounds every execution with ORDER_EXEC_TIMEOUT_S and skip-continues on timeout.

Proves: a hung downstream call (sleep >> timeout) is loud-skipped and the
consumer proceeds to the next order (queue.join() completes; second order
reaches ORDER_RESULT). Hermetic: stub trade manager, tmp cwd, no network.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tradier_manage as tm

EXEC_TIMEOUT = 0.2  # patched ORDER_EXEC_TIMEOUT_S
HANG_S = 30.0  # downstream hang >> timeout (must be cancelled on skip)


def _order(symbol, action="OPEN", side="SELL", qty=48,
           reason="GAP_FILL_SELL gap=4.76%"):
    return {
        "account_key": "trb",
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "action": action,
        "reason": reason,
        "current_price": 35.0,
        "position_side": "SHORT",
        "unique_id": f"test:{symbol}:{action}",
        "override_qty": qty,
        "order_id": "None",
    }


def _stub_manager(hang_symbols, calls):
    async def _execute(**kw):
        calls.append(kw["symbol"])
        if kw["symbol"] in hang_symbols:
            try:
                await asyncio.sleep(HANG_S)
            except asyncio.CancelledError:
                calls.append(kw["symbol"] + ":cancelled")
                raise
            return "SUCCESS"  # unreachable — cancelled on stall-skip
        return "SUCCESS"

    pm = types.SimpleNamespace(positions={}, get_position=lambda pk: None)
    return types.SimpleNamespace(
        redis_manager=None,
        position_manager=pm,
        get_indicators=lambda s: {},
        get_current_price=None,
        dedupe_lock=asyncio.Lock(),
        order_deduplication={},
        execute_trade_action=_execute,
    )


def _run_consumer(oq, orders):
    async def _drive():
        for o in orders:
            ok, msg = await oq.add_order(o)
            assert (ok, msg) == (True, "SUCCESS")
        consumer = asyncio.create_task(oq.process_orders())
        try:
            # Completes only if EVERY order is task_done'd — i.e. the consumer
            # was never wedged. Raises TimeoutError on regression.
            await asyncio.wait_for(oq._orders.join(), timeout=10.0)
        finally:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass
        await asyncio.sleep(0.1)  # let orphan cancellation settle

    asyncio.run(_drive())


def test_hung_order_skipped_consumer_proceeds(tmp_path, monkeypatch, caplog):
    """Oct 5 replay: AR hangs downstream → loud skip → ACN still executes."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tm, "is_regular_trading_hours", lambda: True)
    monkeypatch.setattr(tm, "_cfg_auto",
                        lambda p, d=None: EXEC_TIMEOUT if p == "ORDER_EXEC_TIMEOUT_S" else d)
    calls = []
    oq = tm.OrderQueue(_stub_manager({"AR"}, calls))
    with caplog.at_level(logging.INFO):
        _run_consumer(oq, [_order("AR"), _order("ACN", side="BUY")])
    assert calls[0] == "AR"
    assert "ACN" in calls, f"consumer never reached 2nd order: {calls}"
    assert "AR:cancelled" in calls, f"orphaned execute not cancelled: {calls}"
    assert "[ORDER_STALL_SKIP]" in caplog.text, "stall was not logged loudly"
    assert "trb:AR_SHORT" in caplog.text
    assert "[ORDER_RESULT]" in caplog.text, "2nd order never completed"


def test_fast_orders_unaffected(tmp_path, monkeypatch, caplog):
    """No-hang path: both orders succeed, no stall-skip fires."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tm, "is_regular_trading_hours", lambda: True)
    monkeypatch.setattr(tm, "_cfg_auto",
                        lambda p, d=None: EXEC_TIMEOUT if p == "ORDER_EXEC_TIMEOUT_S" else d)
    calls = []
    oq = tm.OrderQueue(_stub_manager(set(), calls))
    with caplog.at_level(logging.INFO):
        _run_consumer(oq, [_order("AR"), _order("ACN", side="BUY")])
    assert calls == ["AR", "ACN"], calls
    assert "[ORDER_STALL_SKIP]" not in caplog.text
    assert caplog.text.count("[ORDER_RESULT]") == 2

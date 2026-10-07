"""Queue-check probes must not poison order_deduplication (2026-10-05 queue-jump).

Root cause of 538x BLOCKED_BROKER_SYNC_PENDING_CONFIRMATION with ~0 trades:
queue_trade_action calls is_duplicate_order(unique_id="queue_check") seconds
before execute_now runs the BROKER_SYNC gate; the probe STAMPED
order_deduplication[position_key], which the gate misreads as an unconfirmed
real order and refuses the same attempt. Probes are check-only now.
"""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _bare_manager():
    import ez_manage as ez
    mgr = ez.MultiAccountTradeManager.__new__(ez.MultiAccountTradeManager)
    mgr.order_deduplication = {}
    mgr.dedupe_lock = asyncio.Lock()
    return mgr


def test_queue_check_probe_does_not_stamp():
    import ez_manage as ez  # noqa: F401
    mgr = _bare_manager()
    res = asyncio.run(mgr.is_duplicate_order("fin:BTCUSDC_LONG", 0.0, "BUY", "queue_check"))
    assert res is False
    assert mgr.order_deduplication == {}, f"probe stamped dedup: {mgr.order_deduplication}"


def test_real_order_still_stamps_and_blocks():
    mgr = _bare_manager()
    first = asyncio.run(mgr.is_duplicate_order("fin:BTCUSDC_LONG", 1.0, "BUY", "real-1"))
    assert first is False
    assert "fin:BTCUSDC_LONG" in mgr.order_deduplication
    second = asyncio.run(mgr.is_duplicate_order("fin:BTCUSDC_LONG", 1.0, "BUY", "real-2"))
    assert second is True


def test_queue_check_still_reports_genuine_recent_stamp():
    mgr = _bare_manager()
    mgr.order_deduplication["fin:BTCUSDC_LONG"] = {"time": time.time(), "executed": True}
    res = asyncio.run(mgr.is_duplicate_order("fin:BTCUSDC_LONG", 0.0, "BUY", "queue_check"))
    assert res is True

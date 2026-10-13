"""Dedupe guard bombardment controls (USER 2026-10-13): concurrent verifies
must share one in-flight listing (single-flight), and per-pass follow-up
order GETs are capped so a blackout backlog can't burn the queue budget."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import order_dedupe_guard as odg  # noqa: E402


class SlowClient:
    def __init__(self):
        self._current_id = "VA123"
        self.calls = 0
        self.fail = False

    async def _request(self, method, endpoint, **kw):
        self.calls += 1
        await asyncio.sleep(0.3)
        if self.fail:
            raise TimeoutError()
        if endpoint.endswith("/orders"):
            return {"orders": {"order": []}}
        return {"order": {"id": 1, "status": "filled", "exec_quantity": 1.0}}


def _guard(tmp_path):
    from order_dedupe_guard import OrderLedger
    ledger = OrderLedger("tradier", "trb", tmp_path, 40)
    return odg.TradierDedupeGuard("trb", ledger=ledger, cfg=SimpleNamespace(), clock=lambda: 0.0)


def _run(coro):
    return asyncio.run(coro)


def test_concurrent_lists_share_one_fetch(tmp_path):
    g = _guard(tmp_path)
    c = SlowClient()

    async def _burst():
        return await asyncio.gather(*[g._list_orders(c) for _ in range(5)])

    res = _run(_burst())
    assert c.calls == 1, c.calls
    assert all(r == [] for r in res)


def test_shared_failure_raises_for_all_then_recovers(tmp_path):
    g = _guard(tmp_path)
    c = SlowClient()
    c.fail = True

    async def _burst():
        return await asyncio.gather(*[g._list_orders(c) for _ in range(3)],
                                    return_exceptions=True)

    res = _run(_burst())
    assert all(isinstance(r, TimeoutError) for r in res), res
    assert c.calls == 1, c.calls
    c.fail = False
    assert _run(g._list_orders(c)) == []


def test_verify_gets_capped_per_pass(tmp_path):
    g = _guard(tmp_path)
    c = SlowClient()
    now = 0.0
    g.clock = lambda: now
    rows = []
    for i in range(5):
        rows.append({"order_id": str(900 + i), "local_id": f"l{i}", "state": "SUBMITTED",
                     "submitted_at": now - 10.0, "side": "sell", "type": "market",
                     "final": False})
    g.ledger.mutate("AAPL", lambda orders: orders.extend(rows))
    dec, _ = _run(g._check_once(c, "AAPL"))
    assert dec.allowed is False
    assert dec.code == "LAST_ORDER_STATUS_UNKNOWN", dec.code
    assert c.calls <= 4, c.calls  # 1 shared list + cap(3) follow-ups

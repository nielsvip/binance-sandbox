"""Sync-adopt parity (USER 2026-10-14, REAL MONEY).

Broker is truth: a broker holding above the adopt caps (AXON 8, CVX 16, XOM 20
all vetoed 2026-10-13/14) must be mirrored at EXACT qty. The veto may only add
telemetry (reason tag + log) — it must never clamp qty, never write REJECTED
graffiti, and never destroy the peak. Never writes into the live data/ dir."""
import asyncio
from datetime import datetime, timedelta, timezone

from utils import construct_position_key
from tradier_positions import TradierPosition, TradierPositionManager


def _mgr():
    m = TradierPositionManager("trb")
    m._loading_complete_event.set()

    async def _noprices():
        return {}

    m.fetch_prices_from_external_cache = _noprices
    m.history = []

    async def _rec(pk, tt, qty, px, ctx=None):
        m.history.append((pk, tt, qty, px, ctx))

    m.append_to_position_history_file = _rec
    return m


def _stub(symbol, side, amt, mark):
    now = datetime.now(timezone.utc)
    return TradierPosition(
        symbol=symbol, position_side=side, positionAmt=amt, entry_price=0.0,
        mark_price=mark, opened_at=now - timedelta(days=2),
        last_updated=now, last_update=now.isoformat(), entry_time="",
        augment_reason="", max_gain=3.0, gain=0.0, prev_gain=0.0,
    )


def _seed(m, pos):
    pk = construct_position_key("trb", pos.symbol, pos.position_side)
    m.positions[pk] = pos
    m.positions_by_account["trb"][pk] = pos
    return pk


def test_flat_stub_adopts_broker_qty_exact_above_cap():
    m = _mgr()
    pk = _seed(m, _stub("AXON", "SHORT", 0.0, 410.0))
    broker = [{"symbol": "AXON", "quantity": -8.0, "cost_basis": -3231.44}]
    asyncio.run(m.process_account_update(data=broker, skip_broadcast_save=True))
    pos = m.positions[pk]
    assert abs(pos.positionAmt) == 8.0
    assert "REJECTED" not in (pos.augment_reason or "")
    assert "ADOPTED_EXACT" in (pos.augment_reason or "")
    assert pos.max_gain >= 3.0
    assert "OPEN" in [h[1] for h in m.history]


def test_augment_advisory_adopts_exact_and_logs_augment():
    m = _mgr()
    pk = _seed(m, _stub("CVX", "LONG", 2.0, 222.0))
    broker = [{"symbol": "CVX", "quantity": 16.0, "cost_basis": 3547.36}]
    asyncio.run(m.process_account_update(data=broker, skip_broadcast_save=True))
    pos = m.positions[pk]
    assert abs(pos.positionAmt) == 16.0
    assert "REJECTED" not in (pos.augment_reason or "")
    kinds = [h[1] for h in m.history]
    assert "AUGMENT" in kinds
    assert not [h for h in m.history if h[1] == "OPEN" and "REJECTED" in str(h[4])]

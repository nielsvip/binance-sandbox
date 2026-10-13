"""FIXED_STOP backstop must never place orders when out of sync with
tradier_positions/exchange, must never fake a placed order, and must back off
after failures (NVDA trb 2026-10-13: 501 rejected sell/buy-to-cover stops for a
non-existent position, placed ~3/sec with no registry record)."""
import time
from types import SimpleNamespace

import pytest

import tradier_manage as tm_mod

PK = "trb:NVDA_LONG"


class FakePos:
    positionAmt = 14.0
    opened_at = "2026-10-01T14:34:16+00:00"
    entry_price = 235.615


class FakeClient:
    def __init__(self, *a, **k):
        pass

    mode = "noid"
    calls = 0
    broker_positions = []

    async def get_account_positions(self, *a, **k):
        if FakeClient.mode == "verify_raise":
            raise RuntimeError("positions endpoint down")
        return list(FakeClient.broker_positions)

    async def place_order(self, *a, **k):
        FakeClient.calls += 1
        if FakeClient.mode == "raise":
            raise RuntimeError("broker rejected: no position")
        if FakeClient.mode == "ok":
            return {"order": {"id": 123456}}
        return {}  # broker response without an order id (rejection body)

    async def cancel_order(self, *a, **k):
        return {}


def _manager(sync_age_s=0.0):
    m = object.__new__(tm_mod.TradierTradeManager)
    m._fx_reg = {}
    m._fixed_stop_registry_load = lambda: m._fx_reg
    m._fixed_stop_registry_save = lambda: None
    m.position_manager = SimpleNamespace(_last_sync_ok_ts=time.time() - sync_age_s)
    return m


async def _ensure(m):
    import os
    os.environ.pop("PYTEST_CURRENT_TEST", None)  # pytest re-sets it after fixtures; the SUT checks it at call time
    return await m.ensure_fixed_stop_backstop_tradier(
        account_key="trb", symbol="NVDA", position_key=PK, position_side="LONG",
        position=FakePos(), soft_level=190.0, current_price=225.0)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(tm_mod, "TradierAPIClient", FakeClient)
    FakeClient.calls = 0
    FakeClient.mode = "noid"
    FakeClient.broker_positions = []


@pytest.mark.asyncio
async def test_no_order_id_is_failure_not_success():
    m = _manager()
    r = await _ensure(m)
    assert r.startswith("FAILED"), r
    assert PK not in m._fx_reg, "registry must not record a stop that was never placed"


@pytest.mark.asyncio
async def test_rejected_place_backs_off_no_spam():
    FakeClient.mode = "raise"
    m = _manager()
    r1 = await _ensure(m)
    r2 = await _ensure(m)
    assert r1.startswith("FAILED"), r1
    assert r2 == "SKIPPED_STOP_BACKOFF", r2
    assert FakeClient.calls == 1, "second ensure within retry pace must not re-place"


@pytest.mark.asyncio
async def test_in_sync_failed_order_replaced_never_killed():
    FakeClient.mode = "raise"
    m = _manager()
    for i in range(1, 8):
        r = await _ensure(m)
        assert r.startswith("FAILED"), r
        assert FakeClient.calls == i
        assert m._fx_stop_attempts[PK] == i, "obligation must persist and count, never be killed"
        m._fx_stop_retry_ts[PK] -= 10.0  # simulate retry pace elapsing
    # after 500 failures the obligation still re-places
    m._fx_stop_attempts[PK] = 500
    r = await _ensure(m)
    assert r.startswith("FAILED"), r
    assert m._fx_stop_attempts[PK] == 501


@pytest.mark.asyncio
async def test_stale_sync_broker_flat_places_nothing():
    FakeClient.broker_positions = []
    m = _manager(sync_age_s=3600.0)
    r = await _ensure(m)
    assert r == "SKIPPED_STOP_BROKER_FLAT", r
    assert FakeClient.calls == 0


@pytest.mark.asyncio
async def test_stale_sync_broker_held_still_protects():
    FakeClient.mode = "ok"
    FakeClient.broker_positions = [{"symbol": "NVDA", "quantity": 14.0}]
    m = _manager(sync_age_s=3600.0)
    r = await _ensure(m)
    assert r == "SUCCESS_STOP_PLACED_123456", r
    assert FakeClient.calls == 1


@pytest.mark.asyncio
async def test_stale_sync_unverifiable_still_protects():
    FakeClient.mode = "verify_raise"
    m = _manager(sync_age_s=3600.0)
    # place_order also raises in this mode family? No: place raises only in
    # mode == "raise"; verify_raise falls through to no-id place -> FAILED,
    # proving placement was attempted (position protection attempted).
    r = await _ensure(m)
    assert FakeClient.calls == 1, r


@pytest.mark.asyncio
async def test_success_registers_real_id_and_dedupes():
    FakeClient.mode = "ok"
    m = _manager()
    r = await _ensure(m)
    assert r == "SUCCESS_STOP_PLACED_123456", r
    assert m._fx_reg[PK]["orderId"] == "123456"
    r2 = await _ensure(m)
    assert r2 == "OK_ALREADY_PLACED", r2
    assert FakeClient.calls == 1

"""Broker-verify throttle (USER 2026-10-13): tradier_manage bombarded Tradier
with unclosed clients + full listings per verify until throttled, then exits
failed. HELD=True cached per TTL; FLAT/failed never cached; clients closed."""
import asyncio
import types

import tradier_manage as tm


class FakeClient:
    built = 0
    closed = 0
    mode = "held_long"

    def __init__(self, *a, **k):
        type(self).built += 1

    async def get_account_positions(self, account_key):
        if type(self).mode == "boom":
            raise TimeoutError()
        if type(self).mode == "held_long":
            return [{"symbol": "AAPL", "quantity": 5}]
        return [{"symbol": "AAPL", "quantity": 0}]

    async def close(self):
        type(self).closed += 1


def _mgr():
    return tm.TradierTradeManager._broker_holds_position.__get__(types.SimpleNamespace())


def _run(coro):
    # pytest (re)sets PYTEST_CURRENT_TEST at call phase, after setup-phase
    # fixtures run — pop here so the unit under test takes the live path.
    import os
    os.environ.pop("PYTEST_CURRENT_TEST", None)
    return asyncio.run(coro)


import pytest


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    # pytest sets PYTEST_CURRENT_TEST during the call phase; the unit under
    # test short-circuits on it, so remove it for these tests (the last test
    # re-adds it explicitly).
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    FakeClient.built = 0
    FakeClient.closed = 0
    FakeClient.mode = "held_long"
    tm._BROKER_HOLDS_CACHE.clear()


def test_held_cached_and_client_closed(monkeypatch):
    monkeypatch.setattr(tm, "TradierAPIClient", FakeClient)
    monkeypatch.setattr(tm.config, "TRADIER_BROKER_VERIFY_TTL_SEC", 60.0, raising=False)
    m = _mgr()
    assert _run(m("trb", "AAPL", "LONG")) is True
    assert _run(m("trb", "AAPL", "LONG")) is True
    assert FakeClient.built == 1
    assert FakeClient.closed == 1


def test_flat_never_cached(monkeypatch):
    monkeypatch.setattr(tm, "TradierAPIClient", FakeClient)
    monkeypatch.setattr(tm.config, "TRADIER_BROKER_VERIFY_TTL_SEC", 60.0, raising=False)
    FakeClient.mode = "flat"
    m = _mgr()
    assert _run(m("trb", "AAPL", "LONG")) is False
    assert _run(m("trb", "AAPL", "LONG")) is False
    assert FakeClient.built == 2
    assert FakeClient.closed == 2


def test_failure_returns_none_and_closes(monkeypatch):
    monkeypatch.setattr(tm, "TradierAPIClient", FakeClient)
    monkeypatch.setattr(tm.config, "TRADIER_BROKER_VERIFY_TTL_SEC", 60.0, raising=False)
    FakeClient.mode = "boom"
    m = _mgr()
    assert _run(m("trb", "AAPL", "LONG")) is None
    assert FakeClient.closed == 1


def test_pytest_env_short_circuits(monkeypatch):
    import asyncio
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    m = _mgr()
    assert asyncio.run(m("trb", "AAPL", "LONG")) is None

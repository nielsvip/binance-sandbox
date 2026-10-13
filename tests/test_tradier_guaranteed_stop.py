"""GUARANTEED STOP (stocks, USER 2026-10-13): a broker stop exists for every open stock position, the wire guard lets it through
(execute_now token) and never lets it block later orders, and nothing removes it on a stale local "flat". Mocked broker only."""
import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import order_dedupe_guard as odg  # noqa: E402
import tradier_manage as tm_mod  # noqa: E402
from tests.test_order_dedupe_guard_tradier import Clock, FakeTradier, _DEDUPE_ONLY  # noqa: E402

PK = "trb:MSFT_LONG"


def run(coro):
    return asyncio.run(coro)


def _guard_env(tmp_path):
    clock = Clock()
    broker = FakeTradier(clock)
    guard = odg.TradierDedupeGuard("trb", ledger_dir=tmp_path, clock=clock, cfg=_DEDUPE_ONLY)
    return broker, guard, odg.GuardedTradierClient(broker, guard), clock


def test_protective_stop_truth_table():
    assert odg.tradier_protective_stop("stop", "sell")
    assert odg.tradier_protective_stop("stop_limit", "buy_to_cover")
    assert not odg.tradier_protective_stop("stop", "buy")
    assert not odg.tradier_protective_stop("stop", "sell_short")
    assert not odg.tradier_protective_stop("limit", "sell")
    assert not odg.tradier_protective_stop("market", "buy_to_cover")


def test_resting_backstop_never_blocks_exit(tmp_path):
    broker, guard, client, clock = _guard_env(tmp_path)
    broker._new("MSFT", "sell", 6, "stop", status="open")
    r = run(client.place_order(account_key="trb", symbol="MSFT", side="sell", quantity=6, order_type="market"))
    assert "errors" not in r and len(broker.placed) == 1


def test_non_protective_open_order_still_blocks(tmp_path):
    broker, guard, client, clock = _guard_env(tmp_path)
    broker._new("MSFT", "buy", 6, "stop", status="open")  # stop-entry (increasing) is NOT a backstop
    r = run(client.place_order(account_key="trb", symbol="MSFT", side="sell", quantity=6, order_type="market"))
    assert "ORDER_DEDUPE_BLOCK:OPEN_ORDER_EXISTS" in r["errors"]["error"][0]


def test_own_backstop_ledger_row_is_not_last_order_not_final(tmp_path):
    broker, guard, client, clock = _guard_env(tmp_path)
    r1 = run(client.place_order(account_key="trb", symbol="MSFT", side="sell", quantity=6, order_type="stop", stop=530.0, duration="gtc"))
    assert r1["order"]["id"]
    clock.t += 31
    r2 = run(client.place_order(account_key="trb", symbol="MSFT", side="sell", quantity=6, order_type="market"))
    assert "errors" not in r2 and len(broker.placed) == 2


def test_stop_placement_skips_intent_chain(tmp_path):
    broker, guard, client, clock = _guard_env(tmp_path)
    r = run(client.place_order(account_key="trb", symbol="MSFT", side="sell", quantity=4, order_type="market"))
    broker.set(r["order"]["id"], status="filled", exec_quantity=4.0)
    clock.t += 1
    r2 = run(client.place_order(account_key="trb", symbol="MSFT", side="sell", quantity=2, order_type="stop", stop=530.0, duration="gtc"))
    assert "errors" not in r2, r2


def test_binance_intent_rows_unchanged(tmp_path):
    g = odg.BinanceDedupeGuard("men", ledger_dir=tmp_path)
    assert g._intent_rows("XRPUSDC") == g.ledger.orders("XRPUSDC")


class _Pos:
    positionAmt = 6.0
    opened_at = "2026-10-13 15:48:12.277428+00:00"
    entry_price = 545.25


class _WireClient:
    tokens = []
    placed = []
    cancelled = []
    positions = None

    def __init__(self, *a, **k):
        self._current_id = None

    async def place_order(self, *a, **k):
        _WireClient.tokens.append(dict(odg.current_token() or {}))
        _WireClient.placed.append((a, k))
        return {"order": {"id": 777}}

    async def cancel_order(self, *a, **k):
        _WireClient.cancelled.append(a)
        return {"order": {"id": a[-1], "status": "ok"}}

    async def get_account_positions(self, *a, **k):
        return _WireClient.positions

    async def close(self):
        pass


def _manager(monkeypatch, reg=None):
    monkeypatch.setattr(tm_mod, "TradierAPIClient", _WireClient)
    _WireClient.tokens, _WireClient.placed, _WireClient.cancelled = [], [], []
    m = object.__new__(tm_mod.TradierTradeManager)
    m._fx_reg = dict(reg or {})
    m._fixed_stop_registry_load = lambda: m._fx_reg
    m._fixed_stop_registry_save = lambda: None
    m._fixed_stop_levels = {}
    m.position_manager = SimpleNamespace(_last_sync_ok_ts=time.time())
    return m


def _live():
    os.environ.pop("PYTEST_CURRENT_TEST", None)  # the SUT checks it at call time


def test_wire_place_runs_inside_execute_now_token(monkeypatch):
    m = _manager(monkeypatch)
    _live()
    r = run(m._fixed_stop_order_action_tradier(position_key=PK, account_key="trb", symbol="MSFT", position_side="LONG", action="PLACE_STOP", stop_price=530.0, quantity=6, current_price=545.0))
    assert r == "SUCCESS_STOP_PLACED_777"
    tok = _WireClient.tokens[-1]
    assert tok.get("symbol") == "MSFT" and tok.get("action") == "PLACE_STOP" and tok.get("venue") == "tradier"
    assert _WireClient.placed[-1][1].get("order_type") == "stop" and _WireClient.placed[-1][1].get("duration") == "gtc"
    assert odg.current_token() is None


def test_missing_level_falls_back_to_entry(monkeypatch):
    m = _manager(monkeypatch)
    _live()
    r = run(m.ensure_fixed_stop_backstop_tradier(account_key="trb", symbol="MSFT", position_key=PK, position_side="LONG", position=_Pos(), soft_level=0.0, current_price=545.0))
    assert r == "SUCCESS_STOP_PLACED_777"
    want = round(545.25 * 0.9975 * 0.9995 - 0.005, 2)
    assert abs(m._fx_reg[PK]["stopPrice"] - want) <= 0.011 and m._fx_reg[PK]["qty"] == 6


def test_local_flat_unverified_keeps_stop(monkeypatch):
    m = _manager(monkeypatch, {PK: {"orderId": "555", "entry_sig": "x", "symbol": "MSFT", "side": "LONG"}})
    _live()
    _WireClient.positions = None  # broker query failed -> UNVERIFIED, never "flat"
    flat = SimpleNamespace(positionAmt=0.0, opened_at="", entry_price=0.0)
    r = run(m.ensure_fixed_stop_backstop_tradier(account_key="trb", symbol="MSFT", position_key=PK, position_side="LONG", position=flat, soft_level=530.0, current_price=545.0))
    assert r == "KEPT_STOP_BROKER_NOT_FLAT" and _WireClient.cancelled == [] and PK in m._fx_reg


def test_local_flat_broker_held_keeps_stop(monkeypatch):
    m = _manager(monkeypatch, {PK: {"orderId": "555", "entry_sig": "x", "symbol": "MSFT", "side": "LONG"}})
    _live()
    _WireClient.positions = [{"symbol": "MSFT", "quantity": 6.0}]
    tm_mod._BROKER_HOLDS_CACHE.clear()
    flat = SimpleNamespace(positionAmt=0.0, opened_at="", entry_price=0.0)
    r = run(m.ensure_fixed_stop_backstop_tradier(account_key="trb", symbol="MSFT", position_key=PK, position_side="LONG", position=flat, soft_level=530.0, current_price=545.0))
    assert r == "KEPT_STOP_BROKER_NOT_FLAT" and _WireClient.cancelled == []


def test_broker_confirmed_flat_cancels_stop(monkeypatch):
    m = _manager(monkeypatch, {PK: {"orderId": "555", "entry_sig": "x", "symbol": "MSFT", "side": "LONG"}})
    _live()
    _WireClient.positions = []
    tm_mod._BROKER_HOLDS_CACHE.clear()
    flat = SimpleNamespace(positionAmt=0.0, opened_at="", entry_price=0.0)
    r = run(m.ensure_fixed_stop_backstop_tradier(account_key="trb", symbol="MSFT", position_key=PK, position_side="LONG", position=flat, soft_level=530.0, current_price=545.0))
    assert r.startswith("SUCCESS_STOP_CANCELLED") and PK not in m._fx_reg


def test_sweep_keeps_stop_when_broker_not_flat(monkeypatch):
    m = _manager(monkeypatch, {PK: {"orderId": "555", "entry_sig": "x", "symbol": "MSFT", "side": "LONG"}})
    m.position_manager = SimpleNamespace(_last_sync_ok_ts=time.time(), get_position=lambda pk: None)
    _live()
    _WireClient.positions = None
    r = run(m.sweep_fixed_stops_tradier("trb"))
    assert "KEPT_BROKER_NOT_FLAT" in str(r) and _WireClient.cancelled == [] and PK in m._fx_reg


def test_qty_change_replaces_stop(monkeypatch):
    m = _manager(monkeypatch, {PK: {"orderId": "555", "entry_sig": _Pos.opened_at, "symbol": "MSFT", "side": "LONG", "qty": 3}})
    _live()
    r = run(m.ensure_fixed_stop_backstop_tradier(account_key="trb", symbol="MSFT", position_key=PK, position_side="LONG", position=_Pos(), soft_level=532.96, current_price=545.0))
    assert ("trb", "555") in _WireClient.cancelled and r == "SUCCESS_STOP_PLACED_777" and m._fx_reg[PK]["qty"] == 6


def test_exit_handoff_holds_replacement(monkeypatch):
    m = _manager(monkeypatch)
    m._fx_stop_handoff_ts = {PK: time.time()}
    _live()
    r = run(m.ensure_fixed_stop_backstop_tradier(account_key="trb", symbol="MSFT", position_key=PK, position_side="LONG", position=_Pos(), soft_level=532.96, current_price=545.0))
    assert r == "SKIPPED_STOP_EXIT_HANDOFF" and _WireClient.placed == []


def test_broker_holds_none_is_unverified(monkeypatch):
    m = _manager(monkeypatch)
    _live()
    _WireClient.positions = None
    tm_mod._BROKER_HOLDS_CACHE.clear()
    assert run(m._broker_holds_position("trb", "MSFT", "LONG")) is None


def test_registry_save_merges_other_accounts(tmp_path, monkeypatch):
    import json
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"orders": {"tra:META_LONG": {"orderId": "9"}}, "levels": {"tra:META_LONG": {"level": 600.0, "sig": "s"}, "trb:OLD_LONG": {"level": 1.0, "sig": "z"}}}))
    monkeypatch.setattr(tm_mod, "_own_accounts", ["trb"])
    m = object.__new__(tm_mod.TradierTradeManager)
    m._fixed_stop_registry_path = lambda: str(p)
    m._fixed_stop_orders = {"trb:MSFT_LONG": {"orderId": "1"}}
    m._fixed_stop_levels = {"trb:MSFT_LONG": {"level": 532.0, "sig": "a"}}
    m._fixed_stop_registry_save()
    d = json.loads(p.read_text())
    assert d["orders"] == {"tra:META_LONG": {"orderId": "9"}, "trb:MSFT_LONG": {"orderId": "1"}}
    assert set(d["levels"]) == {"tra:META_LONG", "trb:MSFT_LONG"}  # own stale trb row dropped, other account kept

import json
import time
from types import SimpleNamespace

from tools import v15_forward_state as forward
from tools import vector_direct_trader as trader
from tools import mac_positions_push_executors as push


def test_multiple_orders_refresh_actual_broker_quantity(tmp_path):
    obj = trader.Trader.__new__(trader.Trader)
    obj.venue = "crypto"
    obj.out = tmp_path
    obj.a = SimpleNamespace(dry_run=False)
    obj.done, obj.done_ts, obj.quiet = set(), {}, set()
    obj.filled_open_anchors = set()
    obj._entry_banned = lambda acct: False
    rows, held_seen = [], []
    obj._ledger = rows.append
    obj.stops = SimpleNamespace(after_fill=lambda *args: "ok")
    def size(order, held, price):
        held_seen.append(held)
        return min(order["qty"], held), "ok"
    obj.broker = SimpleNamespace(positions=lambda acct: {"TESTUSDT_LONG": {"qty": 7, "price": 10}}, size=size, send=lambda *args: {"status": "FILLED"})
    orders = [{"ss": "TESTUSDT_LONG", "act": "REDUCE", "qty": 3, "key": str(i), "why": "test"} for i in range(2)]
    obj.send_orders("ang", orders, {"TESTUSDT_LONG": {"qty": 10, "price": 10}}, False)
    assert held_seen == [10, 7]
    assert [row["held"] for row in rows] == [10, 7]


def test_terminal_reduction_uses_exit_bar_and_stays_flat_afterwards():
    ledger = [{"type": "OPEN", "bar": 0, "qty": 100, "price": 10}, {"type": "REDUCE", "bar": 1, "qty": 50, "price": 11}, {"type": "REDUCE", "bar_entry": 0, "bar_exit": 2, "qty": 50, "price": 12, "pnl_is_lifecycle": True}]
    position, actions = forward._vector_acts(ledger, 2, [100, 1000, 1900])
    assert position["engine_qty"] == 0
    assert len(actions) == 1
    assert actions[0]["qty_before"] == 50
    assert actions[0]["qty_frac"] == 1
    position, actions = forward._vector_acts(ledger, 3, [100, 1000, 1900, 2800])
    assert position["engine_qty"] == 0 and actions == []


def test_terminal_reduction_follows_same_bar_augment_in_engine_sorted_ledger():
    ledger = [{"type": "OPEN", "bar": 0, "qty": 100}, {"type": "REDUCE", "bar_entry": 0, "bar_exit": 2, "qty": 120, "pnl_is_lifecycle": True}, {"type": "AUGMENT", "bar": 2, "qty": 20}]
    position, actions = forward._vector_acts(ledger, 2, [100, 1000, 1900])
    assert position["engine_qty"] == 0
    assert [action["type"] for action in actions] == ["AUGMENT", "REDUCE"]
    assert actions[-1]["qty_before"] == 120


def test_terminal_reduction_does_not_erase_a_later_entry():
    ledger = [{"type": "OPEN", "bar": 0, "qty": 100}, {"type": "REDUCE", "bar_entry": 0, "bar_exit": 1, "qty": 100, "pnl_is_lifecycle": True}, {"type": "OPEN", "bar": 2, "qty": 40, "price": 12}]
    position, actions = forward._vector_acts(ledger, 2, [100, 1000, 1900])
    assert position["engine_qty"] == 40 and position["engine_open_bar_ts"] == 1900
    assert [action["type"] for action in actions] == ["OPEN"]


def snapshot(tmp_path, row):
    obj = trader.Trader.__new__(trader.Trader)
    obj.venue, obj.out = "crypto", tmp_path
    alerts = []
    obj.alert = lambda kind, **fields: alerts.append(kind)
    (tmp_path / "mac_positions_crypto.json").write_text(json.dumps({"epoch": time.time(), "accounts": {"ang": {"TESTUSDT_LONG": row}}}))
    return obj, alerts


def test_recent_transport_does_not_make_an_old_position_fresh(tmp_path):
    obj, alerts = snapshot(tmp_path, {"qty": 1, "entry_price": 10, "source_file_epoch": time.time() - 3600})
    positions = {"TESTUSDT_LONG": {"qty": 1}}
    obj.annotate_with_mac("ang", positions)
    assert "entry" not in positions["TESTUSDT_LONG"]
    assert alerts == ["VD_MAC_POSITION_SOURCE_STALE"]


def test_recent_file_does_not_make_an_old_record_fresh(tmp_path):
    obj, alerts = snapshot(tmp_path, {"qty": 1, "entry_price": 10, "source_file_epoch": time.time(), "last_updated": "2020-01-01T00:00:00Z"})
    positions = {"TESTUSDT_LONG": {"qty": 1}}
    obj.annotate_with_mac("ang", positions)
    assert "entry" not in positions["TESTUSDT_LONG"]
    assert alerts == ["VD_MAC_POSITION_SOURCE_STALE"]


def test_quantity_disagreement_cannot_attach_another_lifecycle(tmp_path):
    obj, alerts = snapshot(tmp_path, {"qty": 2, "entry_price": 10, "max_gain": 50, "source_file_epoch": time.time()})
    positions = {"TESTUSDT_LONG": {"qty": 1}}
    obj.annotate_with_mac("ang", positions)
    assert positions["TESTUSDT_LONG"]["qty"] == 1
    assert "max_gain" not in positions["TESTUSDT_LONG"]
    assert alerts == ["VD_MAC_POSITION_QTY_DISAGREES"]


def test_push_preserves_actual_source_time(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "ROOT", tmp_path)
    (tmp_path / "ang").mkdir()
    path = tmp_path / "ang/long_positions.json"
    path.write_text(json.dumps({"ang:TESTUSDT_LONG": {"symbol": "TESTUSDT", "positionAmt": 1, "last_updated": "2020-01-01T00:00:00Z"}}))
    accounts, meta = push.collect(["ang"])
    row = accounts["ang"]["TESTUSDT_LONG"]
    assert row["source_file_epoch"] == path.stat().st_mtime
    assert row["last_updated"] == "2020-01-01T00:00:00Z"


def test_crypto_augment_cannot_exceed_market_mode_position_cap(monkeypatch):
    monkeypatch.setattr(trader, "MAX_OPEN_NOTIONAL", 80.0)
    monkeypatch.setattr(trader, "ENTRY_NOTIONAL_CEILING_USD", 20.0)
    broker = trader.Binance.__new__(trader.Binance)
    broker.exchange_info = lambda: {"QNTUSDT": {"step": 0.1, "min_qty": 0.1, "min_notional": 5.0}}
    quantity, why = broker.size({"ss": "QNTUSDT_LONG", "act": "AUGMENT", "qty": 0.04}, 0.4, 199.0)
    assert quantity == 0 and "market-mode cap" in why
    assert broker.size({"ss": "QNTUSDT_LONG", "act": "AUGMENT", "qty": 0.04}, 0.3, 199.0) == (0.1, "")
    assert broker.size({"ss": "QNTUSDT_LONG", "act": "CLOSE"}, 1.1, 199.0) == (1.1, "")


def test_crypto_open_rounding_still_respects_position_cap(monkeypatch):
    monkeypatch.setattr(trader, "MAX_OPEN_NOTIONAL", 80.0)
    monkeypatch.setattr(trader, "ENTRY_NOTIONAL_CEILING_USD", 100.0)
    broker = trader.Binance.__new__(trader.Binance)
    broker.exchange_info = lambda: {"TESTUSDT": {"step": 1.0, "min_qty": 1.0, "min_notional": 80.0}}
    quantity, why = broker.size({"ss": "TESTUSDT_LONG", "act": "OPEN", "notional": 80.0}, 0, 21.0)
    assert quantity == 0


def test_crypto_order_ceiling_comes_from_venue_config(monkeypatch):
    import config
    monkeypatch.setattr(trader, "ENTRY_NOTIONAL_CEILING_USD", 3200.0)
    trader.apply_venue_sizing("crypto")
    assert trader.ENTRY_NOTIONAL_CEILING_USD == config.Config().MAX_ORDER_VALUE


def test_consumer_honors_recorded_terminal_reduction_even_with_old_producer(tmp_path):
    ledger = [{"type": "OPEN", "bar": 1, "qty": 100}, {"type": "REDUCE", "bar_entry": 1, "bar_exit": 3, "qty": 100, "pnl_is_lifecycle": True}]
    state = {"ok": True, "open": True, "engine_qty": 0, "ledger": ledger, "npz_tail": 10000}
    (tmp_path / "TESTUSDT_LONG.json").write_text(json.dumps(state))
    obj = trader.Trader.__new__(trader.Trader)
    obj.state_dir = tmp_path
    loaded = obj.load_states(["TESTUSDT_LONG"])["TESTUSDT_LONG"]
    assert loaded["open"] is False and loaded["consumer_state_repair"]
    assert state["open"] is True
    orders, notes = trader.plan_account("ang", "crypto", ["TESTUSDT_LONG"], {"TESTUSDT_LONG": loaded}, {"TESTUSDT_LONG": {"qty": 3}}, 10000, set(), lambda *args: True)
    assert [(order["act"], order["qty"]) for order in orders] == [("CLOSE", 3)]
    assert trader.plan_account("ang", "crypto", ["TESTUSDT_LONG"], {"TESTUSDT_LONG": loaded}, {}, 10000, set(), lambda *args: True)[0] == []
    state["ledger"].append({"type": "OPEN", "bar": 4, "qty": 20})
    assert trader.reconcile_terminal_lifecycle(state)["open"] is True


def test_legacy_empty_newborn_timeframe_matches_actual_engine_fallback():
    from tools.switch_types import coerce
    assert coerce("NEWBORN_LOSS_KILL_VEL_TF", "", "crypto")[:2] == (True, "15m")
    assert coerce("NEWBORN_LOSS_KILL_VEL_TF", "", "stocks")[:2] == (True, "15m")
    assert coerce("NEWBORN_LOSS_KILL_VEL_TF", "nonsense", "crypto")[0] is False
    assert coerce("BB_BOUNCE_ENTRY_TF", "", "crypto")[0] is False

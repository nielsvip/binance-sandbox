import json
import time
from types import SimpleNamespace

from tools import v15_forward_state as forward
from tools import vector_direct_trader as trader
from tools import mac_positions_push_executors as push


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

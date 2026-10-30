"""The Mac books annotate the executor's broker positions and never override them (USER 2026-10-30)."""
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vector_direct_mac_positions_test", ROOT / "tools" / "vector_direct_trader.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def trader(tmp_path, book, epoch=None, venue="stocks"):
    alerts, logs = [], []
    obj = module.Trader.__new__(module.Trader)
    obj.venue = venue
    obj.out = tmp_path
    obj.alert = lambda kind, **fields: alerts.append((kind, fields))
    doc = {"source": "mac", "venue": venue, "epoch": epoch if epoch is not None else time.time(),
           "written_at": "2026-10-30T20:20:25Z", "accounts": book}
    (tmp_path / f"mac_positions_{venue}.json").write_text(json.dumps(doc))
    module.log = logs.append
    return obj, alerts, logs


MAC = {"trb": {"LMT_LONG": {"qty": 1.0, "entry_price": 647.14, "opened_at": "2026-10-29T17:15:34Z", "max_gain": 2.81, "gain": -1.53, "last_signal": "OPEN"}}}


def test_lifecycle_is_attached_and_quantity_is_left_alone(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC)
    pos = {"LMT_LONG": {"qty": 1.0, "price": 0.0}}
    obj.annotate_with_mac("trb", pos)
    assert pos["LMT_LONG"]["qty"] == 1.0
    assert pos["LMT_LONG"]["entry"] == 647.14
    assert pos["LMT_LONG"]["opened_at"] == "2026-10-29T17:15:34Z"
    assert pos["LMT_LONG"]["max_gain"] == 2.81
    assert pos["LMT_LONG"]["price"] == 0.0, "the current mark is the broker's, never the Mac's entry"
    assert alerts == []


def test_a_mac_quantity_that_disagrees_rings_but_the_broker_stands(tmp_path):
    obj, alerts, _ = trader(tmp_path, {"trb": {"LMT_LONG": {"qty": 4.0, "entry_price": 647.14}}})
    pos = {"LMT_LONG": {"qty": 1.0}}
    obj.annotate_with_mac("trb", pos)
    assert pos["LMT_LONG"]["qty"] == 1.0 and pos["LMT_LONG"]["mac_qty"] == 4.0
    assert [k for k, _ in alerts] == ["VD_MAC_POSITION_QTY_DISAGREES"]


def test_a_side_only_the_mac_holds_creates_no_position(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC)
    pos = {}
    obj.annotate_with_mac("trb", pos)
    assert pos == {}, "the Mac book must never add a position the broker does not show"
    assert [k for k, _ in alerts] == ["VD_MAC_HOLDS_WHAT_BROKER_DOES_NOT"]


def test_a_broker_side_absent_from_the_mac_book_rings_once(tmp_path):
    obj, alerts, _ = trader(tmp_path, {"trb": {}})
    pos = {"GILD_LONG": {"qty": 1.0}}
    obj.annotate_with_mac("trb", pos)
    obj.annotate_with_mac("trb", pos)
    assert [k for k, _ in alerts] == ["VD_MAC_POSITION_MISSING"]


def test_a_stale_book_is_not_used(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC, epoch=time.time() - 10 * 3600)
    pos = {"LMT_LONG": {"qty": 1.0}}
    obj.annotate_with_mac("trb", pos)
    assert "entry" not in pos["LMT_LONG"] and "mac" not in pos["LMT_LONG"]
    assert [k for k, _ in alerts] == ["VD_MAC_POSITIONS_STALE"]


def test_no_book_is_not_an_error(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC)
    (tmp_path / "mac_positions_stocks.json").unlink()
    pos = {"LMT_LONG": {"qty": 1.0}}
    assert obj.annotate_with_mac("trb", pos) is pos and alerts == []


def test_a_held_position_without_a_resting_stop_rings_critical(tmp_path):
    obj, alerts, logs = trader(tmp_path, MAC)
    obj._ledger = lambda row: None
    obj.stops = SimpleNamespace(resting_level=lambda acct, symbol, side: None)
    obj.audit_protection("trb", {"LMT_LONG": {"qty": 1.0}})
    assert [k for k, _ in alerts] == ["VD_HELD_POSITION_UNPROTECTED"]
    assert alerts[0][1]["severity"] == "CRITICAL"
    obj.protection_audited["trb"] = 0.0
    obj.audit_protection("trb", {"LMT_LONG": {"qty": 1.0}})
    assert len(alerts) == 1, "one bell per episode, not one per pass"


def test_a_protected_position_rings_nothing_and_rearms_the_bell(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC)
    obj._ledger = lambda row: None
    obj.stops = SimpleNamespace(resting_level=lambda acct, symbol, side: 625.64)
    obj.audit_protection("trb", {"LMT_LONG": {"qty": 1.0}})
    assert alerts == []
    obj.protection_audited["trb"] = 0.0
    obj.stops = SimpleNamespace(resting_level=lambda acct, symbol, side: None)
    obj.audit_protection("trb", {"LMT_LONG": {"qty": 1.0}})
    assert [k for k, _ in alerts] == ["VD_HELD_POSITION_UNPROTECTED"]

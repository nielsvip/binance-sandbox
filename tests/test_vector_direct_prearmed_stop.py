import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vector_direct_prearm_test", ROOT / "tools" / "vector_direct_trader.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def trader(tmp_path, stop_failure=None, verify_failure=None, resting_missing=False):
    events = []
    obj = module.Trader.__new__(module.Trader)
    obj.venue = "crypto"
    obj.out = tmp_path
    obj.root = tmp_path
    obj.a = SimpleNamespace(dry_run=False)
    obj.quiet = set()
    obj.done = set()
    obj.done_ts = {}
    obj.attempts = {}
    obj.filled_open_anchors = set()
    obj._entry_banned = lambda account: False
    obj._ledger = lambda row: events.append(("ledger", row.get("status")))
    obj.alert = lambda *args, **kwargs: events.append(("alert", args[0]))
    class Broker:
        def price(self, symbol):
            return 1.0
        def size(self, order, held, price):
            return 10.0, ""
        def send(self, account, order, quantity):
            events.append(("send", order["act"]))
            return {"status": "FILLED", "executedQty": str(quantity)}
        def positions(self, account):
            return {"XUSDT_LONG": {"qty": 10.0}}
    obj.broker = Broker()
    class Stops:
        def prearm_entry(self, account, order, quantity, held, price):
            events.append(("prearm", order["act"]))
            if stop_failure:
                raise RuntimeError(stop_failure)
            return 0.9
        def verify_entry(self, account, order, quantity, held, level):
            events.append(("verify", order["act"]))
            if verify_failure:
                raise RuntimeError(verify_failure)
        def after_fill(self, account, order, quantity, held, price):
            events.append(("after_fill", order["act"]))
            return "stop placed"
        def resting_level(self, account, symbol, side):
            events.append(("resting", symbol))
            return None if resting_missing else 0.9
    obj.stops = Stops()
    return obj, events


def order():
    return {"ss": "XUSDT_LONG", "act": "OPEN", "key": "entry-1", "why": "test", "notional": 10.0}


HELD = {"XUSDT_LONG": {"qty": 10.0, "price": 1.0}}


def test_no_entry_is_sent_when_stop_cannot_be_verified(tmp_path):
    """An AUGMENT onto an OPEN position can be pre-protected, so a failure there must refuse the entry."""
    obj, events = trader(tmp_path, stop_failure="not resting")
    obj.send_orders("ang", [dict(order(), act="AUGMENT")], HELD, False)
    assert ("prearm", "AUGMENT") in events
    assert not any(event[0] == "send" for event in events)
    assert ("ledger", "REFUSED_UNPROTECTED_ENTRY") in events


def test_stop_precedes_entry_and_is_verified_after_fill(tmp_path):
    obj, events = trader(tmp_path)
    obj.send_orders("ang", [dict(order(), act="AUGMENT")], HELD, False)
    assert [e for e in events if e[0] in ("prearm", "send", "verify")] == [("prearm", "AUGMENT"), ("send", "AUGMENT"), ("verify", "AUGMENT")]


def test_stock_entry_submits_one_contingent_order_without_flat_position_stop(tmp_path):
    obj, events = trader(tmp_path)
    obj.venue = "stocks"
    obj.rebal_phase = lambda: (True, "")
    obj.broker.pending_protected = lambda account, symbol, side: None
    obj.broker.send_protected = lambda account, order, quantity, level: events.append(("oto", order["act"])) or {"id": 7, "limit": 11.05, "stop": level}
    obj.stops.entry_level = lambda order, price: 0.9
    obj.send_orders("trb", [order()], {}, False)
    assert ("oto", "OPEN") in events
    assert not any(event[0] in ("prearm", "send") for event in events)
    assert ("ledger", "PROTECTED_OTO_SUBMITTED") in events
    assert order()["key"] not in obj.done


def test_tradier_contingent_order_has_entry_and_stop_legs(tmp_path):
    broker = module.Tradier({}, tmp_path)
    broker.account = lambda account: ("test-account", "test-key", "test-url")
    posted = []
    def request(method, account, path, params=None, data=None):
        if method == "GET":
            return {"quotes": {"quote": {"ask": 10.0, "bid": 9.98}}}
        posted.append(data)
        return {"order": {"id": 17, "status": "ok"}}
    broker._req = request
    response = broker.send_protected("trb", {"ss": "XYZ_LONG", "key": "one"}, 3, 9.5)
    assert response["id"] == 17 and response["class"] == "oto"
    assert posted == [{"class": "oto", "duration[0]": "day", "duration[1]": "gtc", "symbol[0]": "XYZ", "quantity[0]": "3", "type[0]": "limit", "side[0]": "buy", "price[0]": "10.03", "symbol[1]": "XYZ", "quantity[1]": "3", "type[1]": "stop", "side[1]": "sell", "stop[1]": "9.50", "tag": posted[0]["tag"]}]


def test_stale_contingent_entry_is_cancelled_but_current_entry_is_kept(tmp_path):
    broker = module.Tradier({}, tmp_path)
    broker.account = lambda account: ("test-account", "test-key", "test-url")
    broker._protected_ids = {("trb", "XYZ_LONG"): {"17"}, ("trb", "ABC_SHORT"): {"18"}}
    deleted = []
    def request(method, account, path, params=None, data=None):
        if method == "GET":
            return {"order": {"id": 18, "status": "open", "class": "oto", "leg": [{"side": "sell_short", "exec_quantity": 0}]}}
        deleted.append(path)
        return {"order": {"status": "ok"}}
    broker._req = request
    assert len(broker.cancel_stale_protected("trb", {"XYZ_LONG"})) == 1
    assert deleted == ["/accounts/test-account/orders/18"]


def test_filled_oto_parent_is_not_cancelled_with_its_live_stop(tmp_path):
    broker = module.Tradier({}, tmp_path)
    broker.account = lambda account: ("test-account", "test-key", "test-url")
    broker._protected_ids = {("trb", "XYZ_LONG"): {"17"}}
    broker._req = lambda method, account, path, params=None, data=None: {"order": {"id": 17, "status": "open", "class": "oto", "leg": [{"side": "buy", "quantity": 2, "exec_quantity": 2}, {"side": "sell", "status": "held"}]}} if method == "GET" else (_ for _ in ()).throw(AssertionError("filled parent must remain"))
    assert broker.cancel_stale_protected("trb", set()) == []
    assert broker.pending_protected("trb", "XYZ", "LONG") is None


def test_lost_stop_closes_filled_position(tmp_path):
    obj, events = trader(tmp_path, verify_failure="gone")
    obj.send_orders("ang", [dict(order(), act="AUGMENT")], HELD, False)
    assert [e for e in events if e[0] == "send"] == [("send", "AUGMENT"), ("send", "CLOSE")]


def test_an_open_from_flat_is_not_prearmed_because_the_exchange_refuses_it(tmp_path):
    """Binance -4509 / a bare Tradier stop: a closing stop needs a position. The stop follows the fill instead."""
    obj, events = trader(tmp_path)
    obj.send_orders("ang", [order()], {}, False)
    assert not any(e[0] == "prearm" for e in events), "prearming a flat book is impossible, not optional"
    assert [e[0] for e in events if e[0] in ("send", "after_fill", "resting")] == ["send", "after_fill", "resting"]


def test_an_open_from_flat_whose_stop_never_rests_is_closed_again(tmp_path):
    obj, events = trader(tmp_path, resting_missing=True)
    obj.send_orders("ang", [order()], {}, False)
    assert [e for e in events if e[0] == "send"] == [("send", "OPEN"), ("send", "CLOSE")]
    assert ("alert", "STOP_MISSING_AFTER_ENTRY") in events

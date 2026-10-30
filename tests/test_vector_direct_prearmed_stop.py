import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vector_direct_prearm_test", ROOT / "tools" / "vector_direct_trader.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def trader(tmp_path, stop_failure=None, verify_failure=None):
    events = []
    obj = module.Trader.__new__(module.Trader)
    obj.venue = "crypto"
    obj.out = tmp_path
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
    obj.stops = Stops()
    return obj, events


def order():
    return {"ss": "XUSDT_LONG", "act": "OPEN", "key": "entry-1", "why": "test", "notional": 10.0}


def test_no_entry_is_sent_when_stop_cannot_be_verified(tmp_path):
    obj, events = trader(tmp_path, stop_failure="not resting")
    obj.send_orders("ang", [order()], {}, False)
    assert ("prearm", "OPEN") in events
    assert not any(event[0] == "send" for event in events)
    assert ("ledger", "REFUSED_UNPROTECTED_ENTRY") in events


def test_stop_precedes_entry_and_is_verified_after_fill(tmp_path):
    obj, events = trader(tmp_path)
    obj.send_orders("ang", [order()], {}, False)
    assert [event for event in events if event[0] in ("prearm", "send", "verify")] == [("prearm", "OPEN"), ("send", "OPEN"), ("verify", "OPEN")]


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


def test_lost_stop_closes_filled_position(tmp_path):
    obj, events = trader(tmp_path, verify_failure="gone")
    obj.send_orders("ang", [order()], {}, False)
    assert [event for event in events if event[0] == "send"] == [("send", "OPEN"), ("send", "CLOSE")]

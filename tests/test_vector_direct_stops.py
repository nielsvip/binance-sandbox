import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import vector_direct_stops as S  # noqa: E402


def test_levels_and_rounding_never_shrink_the_buffer():
    assert abs(S.stop_level("LONG", 100.0, 110.0) - 99.6) < 1e-9
    assert abs(S.stop_level("SHORT", 100.0, 110.0) - 110.44) < 1e-9
    assert S.round_to_tick(99.6049, 0.01, "LONG") == 99.6
    assert S.round_to_tick(110.4401, 0.01, "SHORT") == 110.45
    assert S.stop_level("LONG", 0, 1) is None


class FakeBinance:
    def __init__(self, out, resting=()):
        self.out, self.calls, self.resting = out, [], list(resting)

    def is_dual(self, acct):
        return True

    def _req(self, method, path, acct=None, params=None):
        self.calls.append((method, path, dict(params or {})))
        if path == "/fapi/v1/exchangeInfo":
            return {"symbols": [{"symbol": "XUSDT", "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.001"}]}]}
        if path == "/fapi/v1/openAlgoOrders":
            return self.resting
        if path == "/fapi/v1/algoOrder" and method == "POST":
            return {"algoId": 7, "algoStatus": "NEW"}
        return {}


def _trader(tmp_path, venue, broker):
    t = types.SimpleNamespace(venue=venue, broker=broker, state_dir=tmp_path / "s" / "data" / "forward_state", a=types.SimpleNamespace(npz_dir=str(tmp_path / "npz")))
    rows, alerts = [], []
    t._ledger = rows.append
    t.alert = lambda kind, **f: alerts.append((kind, f))
    (tmp_path / "npz" / ".tail").mkdir(parents=True)
    (tmp_path / "npz" / ".tail" / "XUSDT.jsonl").write_text(json.dumps({"timestamps": 1, "dc_low_15m": 10.0, "dc_high_15m": 12.0}) + "\n")
    return t, rows, alerts


def test_open_long_cancels_old_side_stop_and_places_closeposition_stop(tmp_path):
    b = FakeBinance(tmp_path, resting=[{"algoId": 1, "positionSide": "LONG", "orderType": "STOP_MARKET"}, {"algoId": 2, "positionSide": "SHORT", "orderType": "STOP_MARKET"}])
    t, rows, alerts = _trader(tmp_path, "crypto", b)
    msg = S.Stops(t).after_fill("ang", {"ss": "XUSDT_LONG", "act": "OPEN", "key": "k"}, 5.0, 0.0, 11.0)
    assert "placed" in msg and not alerts
    dels = [c for c in b.calls if c[0] == "DELETE"]
    assert [c[2]["algoId"] for c in dels] == [1]
    post = [c for c in b.calls if c[0] == "POST"][0][2]
    assert post["type"] == "STOP_MARKET" and post["closePosition"] == "true" and post["side"] == "SELL" and post["positionSide"] == "LONG"
    assert float(post["triggerPrice"]) == 9.96 and post["workingType"] == "MARK_PRICE"


def test_short_level_above_dc_high_and_wrong_side_refused(tmp_path):
    b = FakeBinance(tmp_path)
    t, rows, alerts = _trader(tmp_path, "crypto", b)
    msg = S.Stops(t).after_fill("ang", {"ss": "XUSDT_SHORT", "act": "AUGMENT", "key": "k"}, 1.0, 1.0, 11.0)
    post = [c for c in b.calls if c[0] == "POST"][0][2]
    assert float(post["triggerPrice"]) == 12.048 and post["side"] == "BUY"
    b2 = FakeBinance(tmp_path)
    t2, rows2, alerts2 = t, [], []
    t.broker, t.alert = b2, lambda kind, **f: alerts2.append(kind)
    msg = S.Stops(t).after_fill("ang", {"ss": "XUSDT_SHORT", "act": "OPEN", "key": "k"}, 1.0, 0.0, 13.0)
    assert "wrong side" in msg and alerts2 == ["VD_STOP_WRONG_SIDE"] and not [c for c in b2.calls if c[0] == "POST"]


def test_close_cancels_and_crypto_reduce_places_nothing(tmp_path):
    b = FakeBinance(tmp_path, resting=[{"algoId": 3, "positionSide": "LONG", "orderType": "STOP_MARKET"}])
    t, rows, alerts = _trader(tmp_path, "crypto", b)
    assert "cancelled 1" in S.Stops(t).after_fill("ang", {"ss": "XUSDT_LONG", "act": "CLOSE", "key": "k"}, 5.0, 5.0, 11.0)
    b.calls.clear()
    S.Stops(t).after_fill("ang", {"ss": "XUSDT_LONG", "act": "REDUCE", "key": "k"}, 1.0, 5.0, 11.0)
    assert b.calls == []


class FakeTradier:
    """Tradier answers a submission with status "ok" and REJECTS a closing stop that has no position under it."""

    def __init__(self, out, held=None, resting=()):
        self.out, self.calls, self.resting = out, [], list(resting)
        self.held = dict(held or {})
        self.placed = []

    def account(self, acct):
        return "VA1", "key", "https://sandbox.tradier.com/v1"

    def positions(self, acct):
        return {ss: {"qty": q} for ss, q in self.held.items() if q}

    def _req(self, method, acct, path, params=None, data=None):
        self.calls.append((method, path, dict(data or {})))
        if method == "GET" and path.endswith("/orders"):
            return {"orders": {"order": list(self.resting)}}
        if method == "POST":
            self.placed.append(dict(data or {}))
            return {"order": {"id": 101, "status": "ok"}}
        return {"status": "cancelled"}


def test_tradier_submission_ack_ok_is_accepted_and_the_entry_is_not_refused(tmp_path):
    b = FakeTradier(tmp_path, held={"XUSDT_LONG": 5.0})
    t, rows, alerts = _trader(tmp_path, "stocks", b)
    b.resting = [{"id": 101, "side": "sell", "quantity": 5, "status": "open", "type": "stop", "symbol": "XUSDT", "stop_price": 9.96}]
    level = S.Stops(t).prearm_entry("trb", {"ss": "XUSDT_LONG", "act": "AUGMENT", "key": "k"}, 0.0, 5.0, 11.0)
    assert level == 9.96 and not alerts
    assert [r.get("status") for r in rows] == ["PREARM_VERIFIED"]


def test_a_closing_stop_is_never_sent_to_tradier_without_a_position(tmp_path):
    b = FakeTradier(tmp_path, held={})
    t, rows, alerts = _trader(tmp_path, "stocks", b)
    try:
        S.Stops(t).prearm_entry("trb", {"ss": "XUSDT_LONG", "act": "OPEN", "key": "k"}, 5.0, 0.0, 11.0)
        raise AssertionError("a naked stop must be refused")
    except RuntimeError as exc:
        assert "STOP_WOULD_BE_NAKED" in str(exc)
    assert b.placed == []


def test_a_refused_entry_withdraws_the_stop_it_placed(tmp_path):
    b = FakeTradier(tmp_path, held={"XUSDT_LONG": 5.0})
    t, rows, alerts = _trader(tmp_path, "stocks", b)
    b.resting = [{"id": 9, "side": "sell", "quantity": 5, "status": "open", "type": "stop", "symbol": "XUSDT", "stop_price": 0.0}]
    try:
        S.Stops(t).prearm_entry("trb", {"ss": "XUSDT_LONG", "act": "AUGMENT", "key": "k"}, 0.0, 5.0, 11.0)
        raise AssertionError("an unverifiable stop must refuse the entry")
    except RuntimeError as exc:
        assert "STOP_NOT_RESTING" in str(exc) and "withdrew 1 stop" in str(exc)
    assert [r.get("status") for r in rows] == ["WITHDRAWN"]
    assert [c[0] for c in b.calls if c[0] == "DELETE"] == ["DELETE"]


def test_stop_above_the_held_share_count_is_refused(tmp_path):
    b = FakeTradier(tmp_path, held={"XUSDT_LONG": 2.0})
    t, rows, alerts = _trader(tmp_path, "stocks", b)
    try:
        S.Stops(t).place("trb", "XUSDT", "LONG", 9.96, 5.0, "k")
        raise AssertionError("a stop larger than the position must be refused")
    except RuntimeError as exc:
        assert "STOP_QTY_ABOVE_HELD" in str(exc)
    assert b.placed == []


def test_stock_close_ack_keeps_stop_until_broker_is_flat(tmp_path):
    stop = {"id": 17, "side": "buy_to_cover", "quantity": 38, "status": "open", "type": "stop", "symbol": "XUSDT", "stop_price": 21.49}
    b = FakeTradier(tmp_path, held={"XUSDT_SHORT": 38.0}, resting=[stop])
    t, rows, alerts = _trader(tmp_path, "stocks", b)
    o = {"ss": "XUSDT_SHORT", "act": "CLOSE", "key": "trb|XUSDT_SHORT|1793303100|CLOSE"}
    msg = S.Stops(t).after_fill("trb", o, 38.0, 38.0, 21.4)
    assert "protective stop retained" in msg
    assert not [call for call in b.calls if call[0] == "DELETE"]
    assert rows[-1]["status"] == "STOP_RETAINED_POSITION_HELD"
    assert alerts[-1][0] == "VD_CLOSE_NOT_FLAT_STOP_RETAINED"
    b.held.clear()
    msg = S.Stops(t).after_fill("trb", o, 38.0, 38.0, 21.4)
    assert "stops cancelled 1" in msg
    assert [call for call in b.calls if call[0] == "DELETE"]

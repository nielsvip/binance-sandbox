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

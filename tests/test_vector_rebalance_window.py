import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vdt", ROOT / "tools" / "vector_direct_trader.py")
vdt = importlib.util.module_from_spec(spec)
sys.modules["vdt"] = vdt
spec.loader.exec_module(vdt)
NY = ZoneInfo("America/New_York")


def ts(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=NY).timestamp()


class FakeBroker:
    def __init__(self, prices):
        self.prices = prices

    def price(self, sym):
        return self.prices[sym]


def make(prices, npz):
    t = vdt.Trader.__new__(vdt.Trader)
    t.broker = FakeBroker(prices)
    t.a = type("A", (), {"npz_dir": str(npz)})()
    t.state_dir = Path(npz).parent
    return t


def bar(npz, sym, high, low, close, prev, dch, dcl):
    d = Path(npz) / ".tail"
    d.mkdir(parents=True, exist_ok=True)
    row = {"high_15m": high, "low_15m": low, "close_15m": close, "close_15m_prev": prev, "dc_high_15m": dch, "dc_low_15m": dcl}
    (d / f"{sym}.jsonl").write_text(json.dumps(row) + "\n")


def test_phases_new_york_time():
    t = make({}, "/tmp/unused")
    assert t.rebal_phase(ts(2026, 10, 30, 8, 44)) == (False, False, False)
    assert t.rebal_phase(ts(2026, 10, 30, 9, 30)) == (True, False, False)
    assert t.rebal_phase(ts(2026, 10, 30, 14, 29)) == (True, False, False)
    assert t.rebal_phase(ts(2026, 10, 30, 14, 30)) == (True, True, False)
    assert t.rebal_phase(ts(2026, 10, 30, 15, 45)) == (True, True, True)
    assert t.rebal_phase(ts(2026, 10, 31, 10, 0)) == (False, False, False)  # Saturday
    assert t.rebal_phase(ts(2026, 10, 29, 22, 52)) == (False, False, False)  # weekday evening: no forced trim
    assert t.rebal_phase(ts(2026, 10, 30, 16, 0)) == (False, False, False)


def test_local_top_and_bottom(tmp_path):
    t = make({}, tmp_path)
    bar(tmp_path, "ZCSH", high=10.0, low=9.0, close=9.8, prev=9.9, dch=10.0, dcl=8.5)
    assert t.at_local_extreme("ZCSH", "top") is True
    assert t.at_local_extreme("ZCSH", "bottom") is False
    bar(tmp_path, "ZCSH", high=9.5, low=8.5, close=9.0, prev=8.9, dch=10.0, dcl=8.5)
    assert t.at_local_extreme("ZCSH", "top") is False
    assert t.at_local_extreme("ZCSH", "bottom") is True
    assert t.at_local_extreme("NOBAR", "top") is False


def test_rebalance_pro_rata_floor_and_top_gate(tmp_path):
    prices = {"AAA": 100.0, "BBB": 50.0}
    t = make(prices, tmp_path)
    pos = {"AAA_LONG": {"qty": 10.0, "price": 0.0, "cost": 0.0}, "BBB_LONG": {"qty": 4.0, "price": 0.0, "cost": 0.0}}
    # book = 1000 + 200 = 1200 <= 2000 cap -> nothing
    assert t.rebalance("trb", pos, "b1", False) == []
    prices["AAA"] = 300.0  # book = 3000 + 200 = 3200, f = 2000/3200
    bar(tmp_path, "AAA", 10, 9, 9.8, 9.9, 10, 8)  # AAA at a local top
    bar(tmp_path, "BBB", 10, 9, 9.5, 9.0, 12, 8)  # BBB not at a top
    out = t.rebalance("trb", pos, "b1", False)
    assert [o["ss"] for o in out] == ["AAA_LONG"]
    assert out[0]["act"] == "REDUCE" and out[0]["qty"] == 10 - 6  # floor(10*0.625)=6
    forced = t.rebalance("trb", pos, "b1", True)
    assert {o["ss"] for o in forced} == {"AAA_LONG", "BBB_LONG"}
    bbb = [o for o in forced if o["ss"] == "BBB_LONG"][0]
    assert bbb["qty"] == 4 - 2  # floor(4*0.625)=2 is the target, so cut 2

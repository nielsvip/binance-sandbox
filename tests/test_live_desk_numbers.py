"""live_desk numbers must be byte-identical to engine files (USER 2026-10-22).

The desk never recomputes P&L and never touches orders. These tests pin that:
positions snapshots carry file values through untouched, realized windows follow
average-cost math with unknown (never fabricated) basis, and intent validation
rejects bad shapes before anything is staged.
"""
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_server():
    spec = importlib.util.spec_from_file_location("live_desk_server", str(ROOT / "tools" / "live_desk" / "server.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_desk_server"] = mod
    spec.loader.exec_module(mod)
    return mod


srv = _load_server()

ROW = {"symbol": "TSTUSDT", "position_side": "LONG", "positionAmt": 2.5, "entry_price": 100.0, "mark_price": 110.0, "gain": 10.0, "max_gain": 12.0, "unrealized_pnl_USD": 25.0, "realized_pnl": 0.0, "opened_at": "2026-10-22T10:00:00Z", "last_updated": "2026-10-22T11:00:00Z"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    acct = tmp_path / "fin"
    acct.mkdir()
    (acct / "long_positions.json").write_text(json.dumps({"fin:TSTUSDT_LONG": dict(ROW), "fin:FLATUSDT_LONG": dict(ROW, symbol="FLATUSDT", positionAmt=0.0, unrealized_pnl_USD=0.0, gain=0.0)}))
    (acct / "short_positions.json").write_text(json.dumps({"fin:SHOUSDT_SHORT": dict(ROW, symbol="SHOUSDT", position_side="SHORT", positionAmt=1.0, unrealized_pnl_USD=-5.0)}))
    monkeypatch.setattr(srv, "ROOT", tmp_path)
    monkeypatch.setattr(srv, "_cache", {})
    return tmp_path


def test_positions_passthrough_byte_identical(repo):
    snap = srv.read_positions("fin")
    assert set(snap["sides"]) == {"long", "short"}, "both sides must survive (no side-collapse)"
    assert snap["totals"]["count"] == 2
    assert snap["totals"]["flat"] == 1
    row = snap["sides"]["long"][0]
    for k in ("symbol", "side", "qty", "entry", "mark", "gain", "max_gain", "upnl", "realized", "opened_at", "updated"):
        assert row[k] == {"symbol": "TSTUSDT", "side": "LONG", "qty": 2.5, "entry": 100.0, "mark": 110.0, "gain": 10.0, "max_gain": 12.0, "upnl": 25.0, "realized": 0.0, "opened_at": "2026-10-22T10:00:00Z", "updated": "2026-10-22T11:00:00Z"}[k]
    assert snap["totals"]["upnl"] == 20.0
    assert snap["sides"]["short"][0]["side"] == "SHORT"


def _ev(ts, sym, side, act, qty, price):
    return {"ts": ts, "symbol": sym, "side": side, "action": act, "qty": qty, "price": price}


def test_realized_windows_and_unknown_basis():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()

    from datetime import timedelta
    evts = [
        _ev(iso(now - timedelta(hours=1)), "A", "LONG", "OPEN", 10.0, 100.0),
        _ev(iso(now - timedelta(minutes=30)), "A", "LONG", "REDUCE", 4.0, 110.0),  # +40 in every window
        _ev(iso(now - timedelta(days=10)), "B", "SHORT", "OPEN", 5.0, 50.0),
        _ev(iso(now - timedelta(days=9)), "B", "SHORT", "CLOSE", 5.0, 40.0),  # +50 in 30d only
        _ev(iso(now - timedelta(minutes=5)), "C", "LONG", "CLOSE", 2.0, 9.0),  # no basis: unknown
    ]
    out, s = srv.realized_walk(evts)
    assert s["pnl_1d"] == 40.0
    assert s["pnl_7d"] == 40.0
    assert s["pnl_30d"] == 90.0
    assert s["pnl_total"] == 90.0
    assert s["unknown"] == 1
    by_sym = {e["symbol"]: e for e in out if e["action"] in ("REDUCE", "CLOSE")}
    assert by_sym["A"]["rpnl"] == 40.0
    assert by_sym["B"]["rpnl"] == 50.0
    assert "rpnl" not in by_sym["C"]


def test_intent_validation():
    ok, err = srv.validate_intent({"account": "fin", "symbol": "dogeusdt", "side": "long", "qty": 5})
    assert err is None and ok == {"acct": "fin", "symbol": "DOGEUSDT", "side": "LONG", "qty": 5.0}
    for bad in ({"account": "zzz", "symbol": "DOGEUSDT", "side": "LONG", "qty": 5}, {"account": "fin", "symbol": "../x", "side": "LONG", "qty": 5}, {"account": "fin", "symbol": "DOGEUSDT", "side": "HOLD", "qty": 5}, {"account": "fin", "symbol": "DOGEUSDT", "side": "LONG", "qty": 0}, {"account": "fin", "symbol": "DOGEUSDT", "side": "LONG", "qty": -3}, []):
        _, err = srv.validate_intent(bad)
        assert err, bad


def test_write_intent_ttl_and_audit(repo):
    before = time.time()
    intent = srv.write_intent("fin", "DOGEUSDT", "LONG", 5.0)
    assert intent["status"] == "STAGED"
    assert intent["expires_at"] - intent["armed_at"] == 300
    assert intent["armed_at"] >= before
    p = repo / "data" / "manual_orders" / "fin" / f"{intent['uuid']}.json"
    assert json.loads(p.read_text())["symbol"] == "DOGEUSDT"
    audit = (repo / "data" / "manual_orders" / "audit.jsonl").read_text()
    assert intent["uuid"] in audit and "STAGED" in audit

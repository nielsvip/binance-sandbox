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


def _expected_day_sums(out, now):
    """Per-day realized sums straight from priced events, independent of the walk's bucketing."""
    from datetime import date, timedelta
    per_day = {}
    for e in out:
        rp = e.get("rpnl")
        if rp is None:
            continue
        ts = datetime.fromisoformat(e["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > now:
            continue
        d = ts.strftime("%Y-%m-%d")
        per_day[d] = per_day.get(d, 0.0) + rp
    today, yest = now.date(), now.date() - timedelta(days=1)

    def in_trail(n):
        return round(sum(v for d, v in per_day.items() if (today - date.fromisoformat(d)).days <= n), 2)

    return {"today": round(per_day.get(today.strftime("%Y-%m-%d"), 0.0), 2), "yest": round(per_day.get(yest.strftime("%Y-%m-%d"), 0.0), 2), "d7": in_trail(6), "d30": in_trail(29)}


def test_realized_windows_and_unknown_basis():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()

    from datetime import timedelta
    evts = [
        _ev(iso(now - timedelta(hours=2)), "A", "LONG", "OPEN", 10.0, 100.0),
        _ev(iso(now - timedelta(hours=1)), "A", "LONG", "REDUCE", 4.0, 110.0),  # +40
        _ev(iso(now - timedelta(hours=26)), "D", "LONG", "OPEN", 1.0, 100.0),
        _ev(iso(now - timedelta(hours=25)), "D", "LONG", "REDUCE", 1.0, 120.0),  # +20 prior day
        _ev(iso(now - timedelta(days=10)), "B", "SHORT", "OPEN", 5.0, 50.0),
        _ev(iso(now - timedelta(days=9)), "B", "SHORT", "CLOSE", 5.0, 40.0),  # +50 in 30d only
        _ev(iso(now - timedelta(minutes=5)), "C", "LONG", "CLOSE", 2.0, 9.0),  # no basis: unknown
    ]
    out, s = srv.realized_walk(evts)
    exp = _expected_day_sums(out, now)
    assert s["pnl_total"] == 110.0
    assert s["pnl_today"] == exp["today"]
    assert s["pnl_yest"] == exp["yest"]
    assert s["pnl_7d"] == exp["d7"]
    assert s["pnl_30d"] == exp["d30"]
    assert s["d_today"] == now.strftime("%Y-%m-%d")
    assert s["d_yest"] == (now - timedelta(days=1)).strftime("%Y-%m-%d")
    assert s["unknown"] == 1
    assert s["unknown_phantom"] == 1
    assert s["unknown_partial"] == 0
    by_sym = {e["symbol"]: e for e in out if e["action"] in ("REDUCE", "CLOSE")}
    assert by_sym["A"]["rpnl"] == 40.0
    assert by_sym["B"]["rpnl"] == 50.0
    assert "rpnl" not in by_sym["C"]


def test_unknown_partial_vs_phantom():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()
    from datetime import timedelta
    evts = [
        _ev(iso(now - timedelta(hours=2)), "P", "LONG", "OPEN", 10.0, 100.0),
        _ev(iso(now - timedelta(hours=1)), "P", "LONG", "REDUCE", 14.0, 110.0),  # 10 known (+100) + 4 partial
    ]
    out, s = srv.realized_walk(evts)
    exp = _expected_day_sums(out, now)
    assert s["known"] == 1
    assert s["unknown"] == 1
    assert s["unknown_partial"] == 1
    assert s["unknown_phantom"] == 0
    assert s["pnl_total"] == 100.0
    assert s["pnl_today"] == exp["today"]
    assert s["pnl_7d"] == exp["d7"]
    assert s["pnl_30d"] == exp["d30"]
    by_sym = {e["symbol"]: e for e in out if e["action"] in ("REDUCE", "CLOSE")}
    assert by_sym["P"]["rpnl"] == 100.0


def test_broker_fills_counted_once_and_unattributed_hidden():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()
    from datetime import timedelta
    o = _ev(iso(now - timedelta(hours=4)), "LRCX", "SHORT", "OPEN", 13.0, 263.12)
    r1 = _ev(iso(now - timedelta(hours=3)), "LRCX", "SHORT", "REDUCE", 13.0, 235.70)
    r1["reason"] = "Broker sync"
    p = _ev(iso(now - timedelta(hours=62, minutes=0)), "E", "LONG", "OPEN", 43.4, 0.6433)
    f1 = _ev(iso(now - timedelta(hours=1, minutes=1)), "E", "LONG", "REDUCE", 30.7, 0.6432)
    f1["reason"] = "BROKER_RECONCILED order=1"
    f2 = _ev(iso(now - timedelta(hours=1, minutes=1)), "E", "LONG", "REDUCE", 8.8, 0.6431)
    f2["reason"] = "BROKER_RECONCILED order=1"
    f3 = _ev(iso(now - timedelta(hours=1, minutes=1)), "E", "LONG", "REDUCE", 3.9, 0.6431)
    f3["reason"] = "BROKER_RECONCILED order=1"
    dup = _ev(iso(now - timedelta(hours=1)), "E", "LONG", "REDUCE", 43.4, 0.6435)
    dup["reason"] = "REAL_STRATEGY_CLOSE"
    q0 = _ev(iso(now - timedelta(minutes=30)), "R", "LONG", "OPEN", 10.0, 100.0)
    c0 = _ev(iso(now - timedelta(minutes=29)), "R", "LONG", "REDUCE", 10.0, 120.0)
    c0["reason"] = "REAL_STRATEGY_CLOSE"
    d0 = _ev(iso(now - timedelta(minutes=28)), "R", "LONG", "REDUCE", 10.0, 120.0)
    d0["reason"] = "BROKER_RECONCILED order=2"
    out, s = srv.realized_walk([o, r1, p, f1, f2, f3, dup, q0, c0, d0])
    lrcx = [e for e in out if e.get("symbol") == "LRCX" and e["action"] == "REDUCE"]
    assert len(lrcx) == 1 and all("rpnl" not in e for e in lrcx)
    fills = [e for e in out if (e.get("reason") or "").startswith("BROKER_RECONCILED order=1")]
    assert len(fills) == 3 and all("rpnl" in e for e in fills)
    strat = [e for e in out if e.get("reason") == "REAL_STRATEGY_CLOSE" and e.get("symbol") == "E"][0]
    assert "rpnl" not in strat and strat.get("sync_hidden") is True
    assert [e for e in out if e.get("reason") == "BROKER_RECONCILED order=2"][0].get("sync_hidden") is True
    assert s["sync_hidden"] == 3
    assert s["known"] == 4
    assert s["unknown"] == 0
    assert s["pnl_total"] == round(30.7 * (0.6432 - 0.6433) + 8.8 * (0.6431 - 0.6433) + 3.9 * (0.6431 - 0.6433) + 200.0, 2)
    assert srv._is_broker_reconstruction("BROKER_SYNC_CLOSE") is True
    assert srv._is_broker_reconstruction("Broker sync") is True
    assert srv._is_broker_reconstruction("BROKER_RECONCILED order=1") is False
    assert srv._is_broker_fill("BROKER_RECONCILED order=1") is True
    assert srv._is_broker_fill("Broker sync") is False
    assert srv._is_broker_reconstruction("VEC_DRIVEN_CLOSE_RECONCILE_") is False
    assert srv._is_broker_reconstruction("FIXED_STOP_SHORT_px1_g-0.3") is False
    assert srv._is_broker_reconstruction(None) is False


def test_intent_validation():
    ok, err = srv.validate_intent({"account": "fin", "symbol": "dogeusdt", "side": "long", "qty": 5})
    assert err is None and ok == {"acct": "fin", "symbol": "DOGEUSDT", "side": "LONG", "qty": 5.0}
    for bad in ({"account": "zzz", "symbol": "DOGEUSDT", "side": "LONG", "qty": 5}, {"account": "fin", "symbol": "../x", "side": "LONG", "qty": 5}, {"account": "fin", "symbol": "DOGEUSDT", "side": "HOLD", "qty": 5}, {"account": "fin", "symbol": "DOGEUSDT", "side": "LONG", "qty": 0}, {"account": "fin", "symbol": "DOGEUSDT", "side": "LONG", "qty": -3}, []):
        _, err = srv.validate_intent(bad)
        assert err, bad


def test_low_funds_boundary():
    assert srv.low_funds(10000.0, 400.0) is True
    assert srv.low_funds(10000.0, 500.0) is False
    assert srv.low_funds(10000.0, 9000.0) is False
    assert srv.low_funds(None, 10.0) is False
    assert srv.low_funds(100.0, None) is False
    assert srv.low_funds(0.0, 0.0) is False


def test_sum_windows_buckets_and_future_clamp():
    import time
    now = time.time()
    noon = now - (now % 86400) + 12 * 3600
    if noon > now:
        noon -= 86400
    now_s = noon + 3600
    w, daily = srv.sum_windows([(noon - 100, 10.0), (noon - 86400 + 50, 3.0), (noon - 2 * 86400, -4.0), (noon - 10 * 86400, 7.0), (noon - 40 * 86400, 100.0), (noon + 12 * 3600, 5.0)], now_s)
    assert w["b_today"] == 15.0
    assert w["b_yest"] == 3.0
    assert w["b_7d"] == 14.0
    assert w["b_30d"] == 21.0
    assert sum(daily.values()) == 21.0
    assert w["d_today"] == time.strftime("%Y-%m-%d", time.gmtime(now_s))
    assert w["d_yest"] == time.strftime("%Y-%m-%d", time.gmtime(now_s - 86400))
    assert srv.sum_windows([("bad", 1.0)], now_s)[0]["b_today"] == 0.0
    assert srv.sum_windows([("bad", 1.0)], now_s)[1] == {}


def test_binance_sign_shape():
    import re
    s1 = srv._binance_sign("secretA", "timestamp=123&recvWindow=10000")
    s2 = srv._binance_sign("secretA", "timestamp=123&recvWindow=10000")
    s3 = srv._binance_sign("secretB", "timestamp=123&recvWindow=10000")
    assert re.fullmatch(r"[0-9a-f]{64}", s1)
    assert s1 == s2
    assert s1 != s3


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

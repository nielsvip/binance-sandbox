import datetime
import json
import re

from tools.v15_daily_charts import active_set_matches, catchup_rows, discrepancy_overlay


def _epoch(label):
    return int(datetime.datetime.strptime(label, "%Y-%m-%d %H:%M").replace(tzinfo=datetime.timezone.utc).timestamp())


def _page(labels, trades=None, events=None):
    return "\n".join((
        "const LABELS = " + json.dumps(labels) + ";",
        "const TRADES = " + json.dumps(trades or []) + ";",
        "const EVENTS = " + json.dumps(events or []) + ";",
        "const DATASETS=[]; new Chart(c,{",
        "<div id='wrap'>",
    ))


def _constant(page, name):
    match = re.search(r"const " + name + r" = ([^;]+);", page)
    assert match, name
    return json.loads(match.group(1))


def _board(symside, published):
    return {"symside": symside, "published_utc": published}


def test_confirmed_stock_protected_oto_is_included_and_marked_at_fill_bar():
    labels = ["2026-10-30 20:00", "2026-10-30 20:15"]
    decision_bar, fill_bar = map(_epoch, labels)
    rows = [{
        "event": "ORDER",
        "status": "PROTECTED_OTO_SUBMITTED",
        "ss": "AAPL_LONG",
        "acct": "tra",
        "act": "OPEN",
        "ts": decision_bar,
        "key": f"tra|AAPL_LONG|{decision_bar}|OPEN",
        "qty": 2,
        "resp": {"id": "stock-order-1", "status": "accepted"},
        "broker_fill": {"found": True, "status": "filled", "exec_qty": 2, "avg_price": 100, "fill_ms": (fill_bar + 300) * 1000, "order_id": "stock-order-1"},
    }]
    page, result = discrepancy_overlay(_page(labels), _board("AAPL_LONG", "2026-10-30T19:00:00Z"), rows)
    fills = _constant(page, "BROKER_FILLS")
    discrepancies = _constant(page, "DISCREPANCIES")
    assert result["confirmed_broker_fills"] == 1
    assert fills[0]["action"] == "OPEN"
    assert fills[0]["i"] == 1
    assert discrepancies["blue"] == [{"i": 0, "action": "OPEN", "accounts": ["tra"]}]
    assert result["comparison_scope"] == "ACTION_BAR_DIAGNOSTIC_ACCOUNT_AND_QUANTITY_IGNORED"
    assert "does NOT certify exact live parity" in page


def test_confirmed_protective_stop_is_counted_as_live_close_at_fill_bar():
    labels = ["2026-10-30 20:00", "2026-10-30 20:15"]
    decision_bar, fill_bar = map(_epoch, labels)
    rows = [{"_coverage_start": decision_bar - 1800}, {
        "event": "STOP",
        "status": "PLACED",
        "ss": "BTCUSDC_LONG",
        "acct": "men",
        "ts": decision_bar,
        "level": 99,
        "resp": {"orderId": "stop-order-1"},
        "broker_fill": {"found": True, "status": "filled", "exec_qty": 1, "avg_price": 98, "fill_ms": (fill_bar + 300) * 1000, "order_id": "stop-order-1"},
    }]
    vector_trade = {"bar_entry": 0, "bar_exit": 1, "unrealised": False}
    page, result = discrepancy_overlay(_page(labels, trades=[vector_trade]), _board("BTCUSDC_LONG", "2026-10-30T19:00:00Z"), rows)
    fills = _constant(page, "BROKER_FILLS")
    discrepancies = _constant(page, "DISCREPANCIES")
    assert result["confirmed_broker_fills"] == 1
    assert fills[0]["action"] == "CLOSE (protective stop)"
    assert fills[0]["i"] == 1
    assert not any(row["action"] == "CLOSE" for row in discrepancies["orange"])


def test_catchup_orders_negative_gain_and_skips_superseded_boards(monkeypatch):
    monkeypatch.setattr("tools.v15_daily_charts.latest_publish", lambda ss: "2026-10-31T00:00:00Z" if ss == "OLD_LONG" else "2026-10-30T00:00:00Z")
    entries = []
    for ss, delta in (("SMALL_LONG", -4.0), ("LARGE_LONG", -22.0), ("OLD_LONG", -50.0), ("UP_LONG", 3.0)):
        entries.append({"symside": ss, "status": "OK", "deltas": {"gain_pct": delta}, "calculated_utc": "2026-10-30T20:00:00Z", "set_md5": "abc", "board_published_utc": "2026-10-29T00:00:00Z"})
    rows = catchup_rows({"entries": entries})
    assert [(r["symside"], r["drop_pp"]) for r in rows] == [("LARGE_LONG", 22.0), ("SMALL_LONG", 4.0)]


def test_chart_halt_requires_exact_active_set(monkeypatch, tmp_path):
    import hashlib
    import sqlite3
    from tools import v15_daily_charts as C
    monkeypatch.setattr(C, "ROOT", tmp_path)
    db_dir = tmp_path / "data" / "hourly_reconfig"
    db_dir.mkdir(parents=True)
    con = sqlite3.connect(db_dir / "per_sym_store.db")
    con.execute("CREATE TABLE per_sym_active(sym_side TEXT, overrides_json TEXT)")
    con.execute("INSERT INTO per_sym_active VALUES (?,?)", ("GOOD_LONG", json.dumps({"switch": True})))
    con.execute("INSERT INTO per_sym_active VALUES (?,?)", ("OLD_LONG", json.dumps({"switch": False})))
    con.commit(); con.close()
    md5 = hashlib.md5(json.dumps({"switch": True}, sort_keys=True, default=str).encode()).hexdigest()
    rows = [{"symside": "GOOD_LONG", "set_md5": md5}, {"symside": "OLD_LONG", "set_md5": md5}]
    assert active_set_matches(rows) == {"GOOD_LONG"}

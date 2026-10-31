import datetime

from tools.daily_fill_reconciliation import reconcile_fill_markers


START = 1_800_000_000.0
END = START + 900.0


def _iso(timestamp):
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat()


def _scope(account, symside):
    return {"account": account, "sym_side": symside}


def _coverage():
    return {("tra", "AAPL_LONG"): {"complete": True, "start_ts": START, "end_ts": END}}


def _history(account="tra", symside="AAPL_LONG", order_id="id-1", action="OPEN", qty=2, price=100, timestamp=START + 15):
    return {"account": account, "sym_side": symside, "order_id": order_id, "type": action, "qty": qty, "price": price,
            "ts": _iso(timestamp), "prov": {"src": "S5_VECTOR_DIRECT"}}


def _chart(account="tra", symside="AAPL_LONG", order_id="id-1", action="OPEN", qty=2, price=100, timestamp=START + 15):
    return {"account": account, "sym_side": symside, "order_id": order_id, "action": action, "qty": qty, "price": price, "time": _iso(timestamp)}


def test_explicit_zero_fill_scope_is_verified_when_history_coverage_is_complete():
    result = reconcile_fill_markers([_scope("tra", "AAPL_LONG")], [], [], _coverage(), START, END)
    assert result["status"] == "MATCH_100_FILL_CAPTURE"
    assert result["scopes"][0]["history_fills"] == 0
    assert result["scopes"][0]["chart_fills"] == 0


def test_composite_identity_keeps_same_order_id_separate_per_account():
    scopes = [_scope("tra", "AAPL_LONG"), _scope("trb", "AAPL_LONG")]
    coverage = {
        ("tra", "AAPL_LONG"): {"complete": True, "start_ts": START, "end_ts": END},
        ("trb", "AAPL_LONG"): {"complete": True, "start_ts": START, "end_ts": END},
    }
    history = [_history(account="tra"), _history(account="trb")]
    chart = [_chart(account="tra"), _chart(account="trb")]
    result = reconcile_fill_markers(scopes, history, chart, coverage, START, END)
    assert result["status"] == "MATCH_100_FILL_CAPTURE"
    assert result["history_fills"] == 2
    assert all(row["matched_fills"] == 1 for row in result["scopes"])


def test_missing_or_extra_chart_marker_fails_account_scope_exactness():
    scope = [_scope("tra", "AAPL_LONG")]
    missing = reconcile_fill_markers(scope, [_history()], [], _coverage(), START, END)
    extra = reconcile_fill_markers(scope, [], [_chart()], _coverage(), START, END)
    assert missing["status"] == "FAIL"
    assert missing["scopes"][0]["missing_chart_order_ids"] == ["id-1"]
    assert extra["status"] == "FAIL"
    assert extra["scopes"][0]["extra_chart_order_ids"] == ["id-1"]


def test_fill_payload_mismatch_and_duplicate_ids_fail_closed():
    scope = [_scope("tra", "AAPL_LONG")]
    payload = reconcile_fill_markers(scope, [_history()], [_chart(qty=3)], _coverage(), START, END)
    duplicate = reconcile_fill_markers(scope, [_history(), _history()], [_chart()], _coverage(), START, END)
    assert payload["status"] == "FAIL"
    assert payload["scopes"][0]["changed_fills"] == [{"order_id": "id-1", "fields": ["qty"]}]
    assert duplicate["status"] == "FAIL"
    assert duplicate["scopes"][0]["duplicate_history_order_ids"] == ["id-1"]


def test_missing_coverage_or_unknown_history_provenance_is_unverified():
    scope = [_scope("tra", "AAPL_LONG")]
    no_coverage = reconcile_fill_markers(scope, [_history()], [_chart()], {}, START, END)
    unknown_src = _history()
    unknown_src["prov"] = {"src": "UNKNOWN"}
    no_provenance = reconcile_fill_markers(scope, [unknown_src], [_chart()], _coverage(), START, END)
    assert no_coverage["status"] == "UNVERIFIED"
    assert no_provenance["status"] == "UNVERIFIED"


def test_undeclared_account_or_missing_order_id_is_unverified():
    scope = [_scope("tra", "AAPL_LONG")]
    undeclared = reconcile_fill_markers(scope, [_history(account="trb")], [], _coverage(), START, END)
    no_order_id = _history()
    no_order_id.pop("order_id")
    incomplete_id = reconcile_fill_markers(scope, [no_order_id], [], _coverage(), START, END)
    assert undeclared["status"] == "UNVERIFIED"
    assert "undeclared scope trb/AAPL_LONG" in undeclared["issues"][0]
    assert incomplete_id["status"] == "UNVERIFIED"
    assert "no broker order_id" in incomplete_id["scopes"][0]["issues"][0]

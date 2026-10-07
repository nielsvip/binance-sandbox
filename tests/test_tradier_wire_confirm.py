"""test_tradier_wire_confirm — broker-confirmation wire guard, stocks side (tradier_manage).

USER 2026-10-07: broker confirmation is the ONLY order criterion (no clock). Mirrors
test_exec_wire_confirm with Tradier shapes (nested-or-flat {id,status}, lowercase statuses).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tradier_manage as T  # noqa: E402

PK = "TEST_TRB:AAPL_LONG"


def _reset():
    T._EXEC_WIRE_SANCTIONS.pop(PK, None)


def test_id_status_nested_and_flat():
    assert T._trad_id_status({"order": {"id": 77, "status": "open"}}) == ("77", "open")
    assert T._trad_id_status({"id": 78, "status": "FILLED"}) == ("78", "filled")
    assert T._trad_id_status({"errors": {"error": ["x"]}}) == ("", "")
    assert T._trad_id_status({}) == ("", "")
    assert T._trad_id_status(None) == ("", "")


def test_pk_for():
    assert T._pk_for("trb", "aapl", "LONG") == "trb:AAPL_LONG"
    assert T._pk_for("trb", "aapl", None) == "trb:AAPL"


def test_sanction_issue_refuse_reissue_cycle():
    _reset()
    s1 = T._sanction_execution(PK, "OPEN", "BUY", 10.0, "t")
    assert isinstance(s1, int) and s1 > 0
    out = T._confirm_wire_result("test", PK, {"order": {"id": 111, "status": "open"}}, s1, "OPEN", "t")
    assert out == "111"
    assert T._pending_unconfirmed(PK) == ["111"]
    assert T._sanction_execution(PK, "OPEN", "BUY", 10.0, "t") is None
    T._retire_wire_order(PK, 111, "EXECUTED")
    assert T._pending_unconfirmed(PK) == []
    s2 = T._sanction_execution(PK, "OPEN", "BUY", 10.0, "t")
    assert isinstance(s2, int) and s2 > s1
    _reset()


def test_confirm_unconfirmed_never_verdict():
    _reset()
    s = T._sanction_execution(PK, "OPEN", "BUY", 10.0, "t")
    assert T._confirm_wire_result("test", PK, {}, s) is None
    assert T._confirm_wire_result("test", PK, None, s) is None
    assert T._confirm_wire_result("test", PK, {"errors": {"error": ["nope"]}}, s) is None
    assert T._confirm_wire_result("test", PK, {"status": "error", "reason": "Gateway Rejected"}, s) is None
    assert T._pending_unconfirmed(PK) == []
    _reset()


def test_confirm_unsanctioned_trips_bypass():
    _reset()
    assert T._confirm_wire_result("rogue", PK, {"order": {"id": 222, "status": "open"}}, 999999) == "BYPASS"
    assert T._pending_unconfirmed(PK) == ["222"]
    _reset()


def test_confirm_terminal_at_response_binds_nothing():
    _reset()
    for oid, st in ((444, "filled"), (445, "canceled"), (446, "expired"), (447, "rejected")):
        s = T._sanction_execution(PK, "OPEN", "BUY", 10.0, "t")
        assert T._confirm_wire_result("test", PK, {"id": oid, "status": st}, s) == str(oid)
        assert T._pending_unconfirmed(PK) == []
    _reset()


def test_close_order_terminal_matrix():
    for st in ("filled", "canceled", "cancelled", "expired", "rejected", "FILLED", "Canceled"):
        assert T._close_order_terminal(st) == "TERMINAL"
    for st in ("open", "pending", "submitted", "queued", "partially_filled", "calculated", "accepted_for_bidding"):
        assert T._close_order_terminal(st) == "LIVE"
    assert T._close_order_terminal("") == "UNKNOWN"
    assert T._close_order_terminal(None) == "UNKNOWN"


def test_guard_disabled_fail_open(monkeypatch):
    _reset()
    monkeypatch.setattr(T, "_wire_guard_enabled", lambda: False)
    assert T._sanction_execution(PK, "OPEN", "BUY", 10.0, "t") == 0
    assert T._confirm_wire_result("test", PK, {"order": {"id": 555, "status": "open"}}, 1) is None
    assert T._pending_unconfirmed(PK) == []
    _reset()

from copy import deepcopy
from tools.trade_order_parity import audit_replay_logs, compare_orders

def ledger():
    return [
        {"account": "ang", "sym_side": "TEST_LONG", "bar_ts": 1800, "action": "OPEN", "qty": 10, "position_after": 10},
        {"account": "ang", "sym_side": "TEST_LONG", "bar_ts": 2700, "action": "REDUCE", "qty": 4, "position_after": 6},
        {"account": "ang", "sym_side": "TEST_LONG", "bar_ts": 3600, "action": "CLOSE", "qty": 6, "position_after": 0},
    ]

def test_exact_partial_order_sequence_passes():
    assert compare_orders(ledger(), ledger())["verdict"] == "PASS"

def test_same_action_bars_with_different_partial_quantities_pass_user_contract():
    actual = ledger()
    actual[1].update(qty=3, position_after=7)
    actual[2].update(qty=7)
    report = compare_orders(ledger(), actual)
    assert report["verdict"] == "PASS"
    assert len(report["quantity_differences_diagnostic"]) == 2
    assert compare_orders(ledger(), actual, require_quantity_parity=True)["verdict"] == "FAIL"

def test_one_bar_late_is_not_matched_by_tolerance():
    actual = ledger()
    actual[1]["bar_ts"] += 900
    assert compare_orders(ledger(), actual)["verdict"] == "FAIL"

def test_duplicate_reduction_changes_position_and_fails():
    actual = ledger()
    duplicate = deepcopy(actual[1])
    duplicate.update(qty=2, position_after=4)
    actual.insert(2, duplicate)
    actual[3].update(qty=4)
    assert compare_orders(ledger(), actual)["verdict"] == "FAIL"

def test_close_more_than_open_is_invalid_even_if_both_lanes_do_it():
    actual = ledger()
    actual[-1]["qty"] = 12
    report = compare_orders(actual, actual)
    assert report["verdict"] == "INVALID"
    assert "conservation" in report["native_errors"][0]["error"]

def test_empty_ledgers_are_not_parity_proof():
    assert compare_orders([], [])["verdict"] == "NO_EVENTS"

def test_different_accounts_are_not_silently_matched():
    actual = ledger()
    for row in actual:
        row["account"] = "inf"
    assert compare_orders(ledger(), actual)["verdict"] == "FAIL"

def test_errors_and_vector_messages_remain_visible_and_prevent_clean_log_verdict(tmp_path):
    log = tmp_path / "native.log"
    content = "INFO normal\nERROR failed native action\nWARNING [VEC_MISMATCH] original text\n"
    log.write_text(content)
    report = audit_replay_logs([log])
    assert report["verdict"] == "UNREVIEWED"
    assert report["counts"]["RUNTIME_ERROR"] == 1
    assert report["counts"]["VECTOR_DIAGNOSTIC_REQUIRES_REVIEW"] == 1
    assert log.read_text() == content

def test_absent_logs_cannot_prove_error_free_execution(tmp_path):
    assert audit_replay_logs([])["verdict"] == "NOT_CHECKED"
    assert audit_replay_logs([tmp_path / "absent.log"])["verdict"] == "UNREVIEWED"


def test_empty_file_is_not_an_error_free_run_log(tmp_path):
    log = tmp_path / "empty.log"
    log.touch()
    assert audit_replay_logs([log])["verdict"] == "UNREVIEWED"


def test_all_four_action_bars_match_despite_different_sizes():
    vector = ledger()
    vector.insert(1, {"account": "ang", "sym_side": "TEST_LONG", "bar_ts": 1800, "action": "AUGMENT", "qty": 2, "position_after": 12})
    vector[2].update(position_after=8)
    vector[3].update(qty=8)
    native = deepcopy(vector)
    for row in native:
        row["qty"] *= 100
        row["position_after"] *= 100
    assert compare_orders(vector, native)["verdict"] == "PASS"
    native[1]["bar_ts"] = 2700
    assert compare_orders(vector, native)["verdict"] == "FAIL"


def test_existing_positions_can_have_distinct_initial_quantities():
    vector, native = ledger()[1:], ledger()[1:]
    native[0].update(qty=40, position_after=60)
    native[1].update(qty=60)
    key = ("ang", "TEST_LONG")
    result = compare_orders(vector, native, vector_initial_positions={key: 10}, native_initial_positions={key: 100})
    assert result["verdict"] == "PASS"

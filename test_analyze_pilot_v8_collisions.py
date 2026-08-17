from tools.analyze_pilot_v8_collisions import analyze


def _row(key, metric, fingerprint, param="P", value="1"):
    return {
        "key": key,
        "delta_gain_mo_vs_bh": metric,
        "trades_fingerprint": fingerprint,
        "param": param,
        "value_json": value,
        "contract_fingerprint": "contract",
    }


def test_separates_identical_schedule_from_metric_precision_collision():
    payload = analyze(
        [
            _row("MU_LONG", 1.2345, "fp-a", value="1"),
            _row("MU_LONG", 1.2345, "fp-a", value="2"),
            _row("MU_LONG", 1.2345, "fp-b", value="3"),
            _row("VT_LONG", 2.0, None, value="4"),
            _row("VT_LONG", 2.0, None, value="5"),
        ]
    )

    assert payload["totals"]["duplicate_numeric_rows"] == 3
    assert payload["totals"]["exact_schedule_duplicate_rows"] == 1
    assert payload["totals"]["different_schedule_metric_collisions"] == 1
    assert payload["totals"]["legacy_missing_fingerprint_rows"] == 2
    assert len(payload["full_precision_rerun_families"]) == 1
    assert payload["rows_sharing_metric_with_another_setting"] == 5
    assert payload["exclusive_row_classification_counts"] == {
        "DISTINCT_ACTIONS_EQUAL_STORED_METRIC": 3,
        "UNRESOLVED_MISSING_ACTION_FINGERPRINT": 2,
        "ZERO_NONFINITE_OR_MISSING_RESULT": 0,
    }
    assert payload["logical_identity_audit"][
        "duplicate_logical_identity_groups"
    ] == 0
    assert (
        payload["full_precision_rerun_families"][0][
            "minimum_cells_recoverable_by_full_precision_rerun"
        ]
        == 1
    )


def test_true_equal_actions_are_classified_as_legitimate_not_jittered():
    payload = analyze([
        _row("MU_LONG", 2.0, "same-actions", param="A", value="1"),
        _row("MU_LONG", 2.0, "same-actions", param="B", value="2"),
        _row("MU_LONG", 3.0, "unique-actions", param="C", value="3"),
    ])
    assert payload["exclusive_row_classification_counts"] == {
        "LEGITIMATE_IDENTICAL_BACKTEST_OUTCOME": 2,
        "UNIQUE_NONZERO_RESULT_ROW": 1,
        "UNRESOLVED_MISSING_ACTION_FINGERPRINT": 0,
        "ZERO_NONFINITE_OR_MISSING_RESULT": 0,
    }
    assert payload["synthetic_uniqueness_allowed"] is False

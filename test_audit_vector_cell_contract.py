from tools.audit_vector_cell_contract import _receipt_sha256, audit_rows


def row(metric, fingerprint, *, status="MOVED", trades=3):
    return {
        "status": status,
        "delta_gain_mo_vs_bh_approx": metric,
        "behavior_fingerprint": fingerprint,
        "trades": trades,
    }


def test_bounded_pair_plateau_is_allowed_when_other_three_values_are_unique():
    source = {
        ("MU_LONG", "THRESHOLD", "0"): row(1.0, "fp-0"),
        ("MU_LONG", "THRESHOLD", "20"): row(1.0, "fp-0"),
        ("MU_LONG", "THRESHOLD", "40"): row(2.0, "fp-40"),
        ("MU_LONG", "THRESHOLD", "60"): row(3.0, "fp-60"),
        ("MU_LONG", "THRESHOLD", "80"): row(4.0, "fp-80"),
    }
    rows, report = audit_rows(source, family_by_param={"THRESHOLD": "TEST"})
    assert rows[("MU_LONG", "THRESHOLD", "0")]["result_uniqueness_status"] == "PASS"
    assert rows[("MU_LONG", "THRESHOLD", "20")]["result_uniqueness_status"] == "PASS"
    assert report["allowed_isolated_metric_plateau_groups"] == 1
    assert report["allowed_isolated_fingerprint_plateau_groups"] == 1
    assert report["campaign_must_stop"] is False


def test_pair_plateau_is_rejected_when_axis_has_only_four_values():
    source = {
        ("MU_LONG", "THRESHOLD", "0"): row(1.0, "fp-0"),
        ("MU_LONG", "THRESHOLD", "20"): row(1.0, "fp-0"),
        ("MU_LONG", "THRESHOLD", "40"): row(2.0, "fp-40"),
        ("MU_LONG", "THRESHOLD", "60"): row(3.0, "fp-60"),
    }
    rows, report = audit_rows(source)
    assert rows[("MU_LONG", "THRESHOLD", "0")]["result_uniqueness_status"] == "QUARANTINED"
    assert report["allowed_isolated_metric_plateau_groups"] == 0


def test_two_plateaus_in_one_axis_are_a_broadcast_not_an_exception():
    source = {
        ("MU_LONG", "THRESHOLD", "0"): row(1.0, "fp-a"),
        ("MU_LONG", "THRESHOLD", "20"): row(1.0, "fp-a"),
        ("MU_LONG", "THRESHOLD", "40"): row(2.0, "fp-b"),
        ("MU_LONG", "THRESHOLD", "60"): row(2.0, "fp-b"),
        ("MU_LONG", "THRESHOLD", "80"): row(3.0, "fp-c"),
    }
    rows, report = audit_rows(source)
    assert all(
        rows[("MU_LONG", "THRESHOLD", value)]["result_uniqueness_status"]
        == "QUARANTINED"
        for value in ("0", "20", "40", "60")
    )
    assert rows[("MU_LONG", "THRESHOLD", "80")]["result_uniqueness_status"] == "PASS"
    assert report["campaign_must_stop"] is True
    assert report["next_causal_recalculation_queue"]
    assert report["per_family_duplicate_groups"]["UNMAPPED"]["METRIC_GROUPS"] == 2


def test_cross_parameter_repeat_is_broken_even_when_each_param_has_values():
    source = {
        ("MSTR_SHORT", "A", "0"): row(1.0, "fp-a0"),
        ("MSTR_SHORT", "A", "20"): row(2.0, "fp-a20"),
        ("MSTR_SHORT", "B", "0"): row(1.0, "fp-b0"),
        ("MSTR_SHORT", "B", "20"): row(3.0, "fp-b20"),
    }
    rows, report = audit_rows(source)
    assert rows[("MSTR_SHORT", "A", "0")]["result_uniqueness_status"] == "QUARANTINED"
    assert rows[("MSTR_SHORT", "B", "0")]["result_uniqueness_status"] == "QUARANTINED"
    assert report["campaign_must_stop"] is True


def test_repeated_behavior_stops_even_when_metric_numbers_differ():
    source = {
        ("MSTR_SHORT", "A", "0"): row(1.0, "same-actions"),
        ("MSTR_SHORT", "A", "20"): row(2.0, "same-actions"),
    }
    rows, report = audit_rows(source)
    assert all(item["result_uniqueness_status"] == "QUARANTINED" for item in rows.values())
    assert report["reason_counts"]["DUPLICATE_ACTION_FINGERPRINT"] == 2


def test_zero_and_non_moved_are_never_valid_cells():
    source = {
        ("MSTR_SHORT", "A", "0"): row(0.0, "a"),
        ("MSTR_SHORT", "B", "0"): row(1.0, "b", status="INERT"),
    }
    rows, _report = audit_rows(source)
    assert "ZERO_RESULT" in rows[("MSTR_SHORT", "A", "0")]["result_uniqueness_reasons"]
    assert "STATUS_INERT" in rows[("MSTR_SHORT", "B", "0")]["result_uniqueness_reasons"]


def test_receipt_hash_is_order_independent_and_binds_content():
    first = {"queue": [{"key": "MU_LONG"}], "source": {"a": "hash"}}
    reordered = {"source": {"a": "hash"}, "queue": [{"key": "MU_LONG"}]}
    changed = {"queue": [{"key": "VT_LONG"}], "source": {"a": "hash"}}
    assert _receipt_sha256(first) == _receipt_sha256(reordered)
    assert _receipt_sha256(first) != _receipt_sha256(changed)

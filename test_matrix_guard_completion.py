from tools import current_matrix_reporting
from tools import matrix_guard
from tools import audit_switch_matrix_uniqueness


def _fixture():
    header = [
        "main_switch",
        "sub_setting",
        "value",
        "description",
        "status",
        "n_tested",
        "n_inert",
        "mean_delta",
        "best_key",
        "best_delta",
        "worst_key",
        "worst_delta",
        "MU_LONG",
    ]
    rows = [
        [
            "TEST_ENABLED",
            "",
            "true",
            "",
            "OK",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "9.9",
        ]
    ]
    records = [
        {
            "canonical_param": "TEST_ENABLED",
            "static_contract": "READY_EXACT_ONLY",
            "side_applicability": "BOTH_OR_RUNTIME_DEPENDENT",
            "grid_membership": "CURRENT_MANIFEST_GRID",
            "dynamic_state": "NO_CURRENT_EXACT_EVIDENCE",
            "matrix_red_triage": "NOT_CURRENTLY_RED",
            "matrix_status": "OK",
            "intentional_control": False,
        }
    ]
    return header, rows, records


def test_receipt_completion_replaces_stale_csv_numeric_cell():
    header, rows, records = _fixture()

    progress = matrix_guard.classified_progress(
        header,
        rows,
        records,
        ["MU_LONG"],
        filled_logical_cells=set(),
    )

    assert progress["pilot"]["MU_LONG"]["filled"] == 0
    assert progress["pilot"]["MU_LONG"]["empty"] == 1


def test_valid_historical_logical_cell_fills_scalar_completion():
    header, rows, records = _fixture()

    progress = matrix_guard.classified_progress(
        header,
        rows,
        records,
        ["MU_LONG"],
        filled_logical_cells={
            ("TEST_ENABLED", "true", "MU_LONG"),
        },
    )

    assert progress["pilot"]["MU_LONG"]["filled"] == 1
    assert progress["pilot"]["MU_LONG"]["empty"] == 0


def test_receipt_loader_excludes_grouped_and_helper_cells(monkeypatch):
    monkeypatch.setattr(
        current_matrix_reporting,
        "merged_rows",
        lambda _root: [
            {
                "param": "TEST_ENABLED",
                "value_json": "true",
                "key": "MU_LONG",
            },
            {
                "param": "GROUP_COMBO",
                "value_json": '{"recipe_id":"r1"}',
                "key": "MU_LONG",
            },
            {
                "param": "STOP_PACK",
                "value_json": "all",
                "key": "MU_LONG",
            },
        ],
    )

    assert matrix_guard.receipt_validated_scalar_cells() == {
        ("TEST_ENABLED", "true", "MU_LONG")
    }


def test_uniqueness_allows_only_quarantined_missing_path_rows(monkeypatch):
    payload = {
        "scope": {"campaign": matrix_guard.CURRENT_CAMPAIGN},
        "summary": {"authority_error_count": 1},
        "authority_errors": ["ALIAS_PARAM: missing path row"],
    }
    monkeypatch.setattr(
        audit_switch_matrix_uniqueness,
        "audit",
        lambda **_kwargs: payload,
    )

    result = matrix_guard.load_uniqueness_contract()

    assert result is payload
    assert result["quarantined_missing_path_rows"] == 1


def test_uniqueness_still_fails_other_authority_errors(monkeypatch):
    payload = {
        "scope": {"campaign": matrix_guard.CURRENT_CAMPAIGN},
        "summary": {"authority_error_count": 1},
        "authority_errors": ["PARAM: manifest collision"],
    }
    monkeypatch.setattr(
        audit_switch_matrix_uniqueness,
        "audit",
        lambda **_kwargs: payload,
    )

    assert matrix_guard.load_uniqueness_contract() is None


def test_uniqueness_quarantines_explicit_non_scalar_authority_gaps(monkeypatch):
    payload = {
        "scope": {"campaign": matrix_guard.CURRENT_CAMPAIGN},
        "summary": {"authority_error_count": 3},
        "authority_errors": [
            "GROUP_COMBO: missing manifest row",
            "GROUP_COMBO: missing path row",
            "GROUP_COMBO: missing registry row",
        ],
    }
    monkeypatch.setattr(
        audit_switch_matrix_uniqueness,
        "audit",
        lambda **_kwargs: payload,
    )

    result = matrix_guard.load_uniqueness_contract()

    assert result is payload
    assert result["quarantined_authority_gaps"] == 3
    assert result["quarantined_missing_path_rows"] == 1


def test_strict_overlay_counts_only_uniqueness_pass(monkeypatch):
    from tools import export_switch_matrix_xls as exporter

    raw = {
        ("P", "1", "MU_LONG"): {"status": "MOVED"},
        ("P", "2", "MU_LONG"): {"status": "INERT"},
        ("P", "3", "VT_LONG"): {"status": "DATA_UNAVAILABLE"},
    }
    audited = {
        ("P", "1", "MU_LONG"): {"result_uniqueness_status": "PASS"},
        ("P", "2", "MU_LONG"): {
            "result_uniqueness_status": "QUARANTINED"
        },
        ("P", "3", "VT_LONG"): {
            "result_uniqueness_status": "NOT_APPLICABLE_DATA_UNAVAILABLE"
        },
    }
    report = {
        "passed_numeric_overlays": 1,
        "quarantined_numeric_overlays": 1,
        "data_unavailable_rows": 1,
    }
    monkeypatch.setattr(
        exporter,
        "load_active_vector_cell_audit_overlay",
        lambda _root: (
            raw,
            {"available": True, "report": "strict-union.json", "passed": 1},
        ),
    )
    monkeypatch.setattr(
        exporter,
        "load_full_matrix_vector_overlay",
        lambda _root: (_ for _ in ()).throw(
            AssertionError("retired full-blank broadcast must not be read")
        ),
    )
    monkeypatch.setattr(
        exporter,
        "audit_vector_overlay_uniqueness",
        lambda _raw: (audited, report),
    )

    result = matrix_guard.strict_provisional_vector_overlay()

    assert result["available"] is True
    assert result["keys"]["MU_LONG"] == {
        "amber": 1,
        "quarantined": 1,
        "unavailable": 0,
    }
    assert result["keys"]["VT_LONG"]["amber"] == 0
    assert result["keys"]["VT_LONG"]["unavailable"] == 1
    assert result["report"]["old_full_blank_broadcast_display_excluded"] is True

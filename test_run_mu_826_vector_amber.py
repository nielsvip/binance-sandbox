import gzip
import hashlib
import json

import pytest

from tools import audit_switch_matrix_uniqueness as uniqueness
from tools import matrix_guard
from tools import run_mu_826_vector_amber as runner
from tools import vector_approx_entry_augment as entry_adapter


def test_surface_lock_rejects_a_concurrent_materializer(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "SURFACE_LOCK", tmp_path / "matrix.lock")
    first = runner.acquire_surface_lock()
    try:
        with pytest.raises(RuntimeError, match="SWITCH_MATRIX_SURFACE_BUSY"):
            runner.acquire_surface_lock()
    finally:
        first.close()


def test_mu_discovery_binds_explicit_matrix_and_db(monkeypatch, tmp_path):
    matrix = tmp_path / "SWITCH_MATRIX_TRB.csv.gz"
    with gzip.open(matrix, "wt") as handle:
        handle.write("# test canonical\n")
    db = tmp_path / "param_results_stocks.db"
    db.touch()
    rows = [["", "", f"v{i}", ""] + [""] * 9 for i in range(826)]
    records = [
        {"canonical_param": f"P{i}", "category": "ACTIONABLE_EXACT_ONLY"}
        for i in range(826)
    ]
    seen = {}
    header = ["a", "b", "value", "description"] + [f"h{i}" for i in range(8)] + ["MU_LONG"]
    monkeypatch.setattr(matrix_guard, "load", lambda: (header, rows))
    monkeypatch.setattr(
        uniqueness,
        "audit",
        lambda *, matrix_path, db_path: (
            seen.update(matrix=matrix_path, db=db_path)
            or {
                "scope": {"campaign": matrix_guard.CURRENT_CAMPAIGN},
                "tim_policy": {"keys": ["MU_LONG"]},
                "rows": records,
            }
        ),
    )
    monkeypatch.setattr(
        matrix_guard,
        "cell_contract_category",
        lambda record, key, active: record["category"],
    )

    cells, occupied, target, source = runner.discover_cells(matrix, db)

    assert len(cells) == 826
    assert occupied == 0
    assert target == 826
    assert seen == {"matrix": matrix.resolve(), "db": db.resolve()}
    assert source["canonical_matrix_path"] == str(matrix.resolve())
    assert source["canonical_db_path"] == str(db.resolve())
    assert source["canonical_matrix_sha256"]
    assert source["canonical_contract_sha256"]
    assert source["canonical_category_counts"] == {"ACTIONABLE_EXACT_ONLY": 826}
    assert source["canonical_nonblank_actionable"] == 0


def test_logical_contract_hash_ignores_volatile_observations():
    base = {
        "scope": {"campaign": "stocks_repaired_20260730_c5"},
        "tim_policy": {"keys": ["MU_LONG"]},
        "authority_errors": [],
        "rows": [{"canonical_param": "P", "value": "1", "static_contract": "x"}],
        "generated_at": "2026-08-01T00:00:00Z",
        "dynamic_state_counts": {"PASS": 1},
    }
    changed = {**base, "generated_at": "2026-08-01T00:01:00Z", "dynamic_state_counts": {"PASS": 2}}
    assert runner._logical_contract_sha256(base) == runner._logical_contract_sha256(changed)


def test_materializer_counts_only_unique_pass_or_explicit_unavailable():
    rows = [
        {
            "key": "MU_LONG", "param": "A", "value_json": "1",
            "status": "MOVED", "delta_gain_mo_vs_bh_approx": 4.2,
            "behavior_fingerprint": "actions-a", "trades": 3,
        },
        {
            "key": "MU_LONG", "param": "B", "value_json": "1",
            "status": "MOVED", "delta_gain_mo_vs_bh_approx": 4.2,
            "behavior_fingerprint": "actions-b", "trades": 3,
        },
        {
            "key": "MU_LONG", "param": "C", "value_json": "1",
            "status": "MOVED", "delta_gain_mo_vs_bh_approx": 5.1,
            "behavior_fingerprint": "actions-c", "trades": 3,
        },
        {
            "key": "MU_LONG", "param": "D", "value_json": "1",
            "status": "DATA_UNAVAILABLE", "limitations": "no adapter",
        },
    ]
    original = json.loads(json.dumps(rows))

    audited, report = runner.audit_materialized_rows(rows)

    assert rows == original
    assert report["passed_numeric_rows"] == 1
    assert report["quarantined_numeric_rows"] == 2
    assert report["explicit_data_unavailable_rows"] == 1
    assert report["campaign_must_stop"] is True
    by_param = {row["param"]: row for row in audited}
    assert by_param["C"]["result_uniqueness_status"] == "PASS"
    assert by_param["D"]["result_uniqueness_status"] == "NOT_APPLICABLE_DATA_UNAVAILABLE"


def test_inert_adapter_rows_become_non_numeric_unavailable_evidence():
    rows = [
        {
            "key": "MU_LONG", "param": "INERT_A", "value_json": "1",
            "status": "INERT", "delta_gain_mo_vs_bh_approx": -45.5,
            "behavior_fingerprint": "base-actions", "trades": 3,
        },
        {
            "key": "MU_LONG", "param": "INERT_B", "value_json": "2",
            "status": "INERT", "delta_gain_mo_vs_bh_approx": -45.5,
            "behavior_fingerprint": "base-actions", "trades": 3,
        },
        {
            "key": "MU_LONG", "param": "SATURATED", "value_json": "1",
            "status": "MOVED", "delta_gain_mo_vs_bh_approx": -34.4,
            "behavior_fingerprint": "sat-actions", "trades": 3,
        },
        {
            "key": "MU_LONG", "param": "SATURATED", "value_json": "2",
            "status": "MOVED", "delta_gain_mo_vs_bh_approx": -34.4,
            "behavior_fingerprint": "sat-actions", "trades": 3,
        },
        {
            "key": "MU_LONG", "param": "PATH_A", "value_json": "1",
            "status": "MOVED", "delta_gain_mo_vs_bh_approx": -45.1,
            "behavior_fingerprint": "shared-path", "trades": 3,
        },
        {
            "key": "MU_LONG", "param": "PATH_B", "value_json": "1",
            "status": "MOVED", "delta_gain_mo_vs_bh_approx": -45.1,
            "behavior_fingerprint": "shared-path", "trades": 3,
        },
        {
            "key": "MU_LONG", "param": "NO_ACTIVITY", "value_json": "1",
            "status": "MOVED", "delta_gain_mo_vs_bh_approx": -22.0,
            "behavior_fingerprint": "empty-actions", "trades": 0,
        },
    ]

    audited, report = runner.audit_materialized_rows(rows)

    assert report["quarantined_adapter_group_count"] == 2
    classifications = {
        group["classification"] for group in report["quarantined_adapter_groups"]
    }
    assert classifications == {
        "DATA_UNAVAILABLE_SATURATED_SAME_PARAMETER_RANGE",
        "DATA_UNAVAILABLE_CROSS_PARAMETER_RUNTIME_COLLISION",
    }
    assert report["passed_numeric_rows"] == 0
    assert report["quarantined_numeric_rows"] == 4
    assert report["explicit_data_unavailable_rows"] == 3
    assert report["inert_numeric_reclassified_data_unavailable_rows"] == 2
    assert report["no_activity_numeric_reclassified_data_unavailable_rows"] == 1
    assert report["campaign_must_stop"] is True
    inert = [row for row in audited if row.get("raw_measurement_status") == "INERT"]
    assert len(inert) == 2
    assert all(row["status"] == "DATA_UNAVAILABLE" for row in inert)
    assert all("delta_gain_mo_vs_bh_approx" not in row for row in inert)
    assert all("behavior_fingerprint" not in row for row in inert)
    assert all(row["raw_evidence_sha256"] for row in inert)
    no_activity = [
        row
        for row in audited
        if row.get("causal_adapter_classification")
        == "DATA_UNAVAILABLE_DEGENERATE_NO_ACTIVITY"
    ]
    assert len(no_activity) == 1
    assert "delta_gain_mo_vs_bh_approx" not in no_activity[0]
    quarantined = [
        row
        for row in audited
        if row.get("result_uniqueness_status")
        != "NOT_APPLICABLE_DATA_UNAVAILABLE"
    ]
    assert all(row["matrix_fill_allowed"] is False for row in quarantined)
    assert all(
        row["required_next_stage"] == "UNTESTED_NEEDS_EXACT_CAUSAL_ADAPTER"
        for row in inert + quarantined
    )


def test_unsupported_adapter_projection_strips_numeric_action_evidence():
    raw = {
        "key": "MU_LONG",
        "param": "STDEV_BREAKOUT_RETEST_PCTB_MIN",
        "value_json": "0.85",
        "status": "UNSUPPORTED_CAUSAL_ADAPTER",
        "trades": 7,
        "delta_gain_mo_vs_bh_approx": 12.3,
        "gain_per_mo_approx": 13.4,
        "capture_vs_bh_approx": 1.2,
        "behavior_fingerprint": "prior-actions",
    }
    expected_hash = hashlib.sha256(
        json.dumps(
            raw, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()

    audited, report = runner.audit_materialized_rows([raw])

    assert report["unsupported_adapter_reclassified_data_unavailable_rows"] == 1
    assert report["quarantined_numeric_rows"] == 0
    row = audited[0]
    assert row["status"] == "DATA_UNAVAILABLE"
    assert row["raw_measurement_status"] == "UNSUPPORTED_CAUSAL_ADAPTER"
    assert row["raw_evidence_sha256"] == expected_hash
    assert row["causal_adapter_classification"] == (
        "DATA_UNAVAILABLE_UNSUPPORTED_CAUSAL_ADAPTER"
    )
    assert row["required_next_stage"] == "UNTESTED_NEEDS_EXACT_CAUSAL_ADAPTER"
    for forbidden in (
        "trades",
        "delta_gain_mo_vs_bh_approx",
        "gain_per_mo_approx",
        "capture_vs_bh_approx",
        "behavior_fingerprint",
    ):
        assert forbidden not in row


def test_prior_raw_loader_is_hash_bound(monkeypatch, tmp_path):
    relative = "prior/MU_LONG.jsonl"
    path = tmp_path / relative
    path.parent.mkdir()
    raw = {
        "key": "MU_LONG",
        "param": "STDEV_BREAKOUT_RETEST_PCTB_MIN",
        "value_json": "0.85",
        "status": "MOVED",
    }
    path.write_text(json.dumps(raw, sort_keys=True) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setitem(
        runner.PRIOR_RESIDUAL_SOURCE, "raw_repo_relative_path", relative
    )
    monkeypatch.setitem(runner.PRIOR_RESIDUAL_SOURCE, "raw_sha256", digest)

    evidence = runner._load_prior_residual_raw_evidence(tmp_path)

    assert evidence[("STDEV_BREAKOUT_RETEST_PCTB_MIN", "0.85")] == raw


def test_mu_discovery_treats_db_exact_owner_as_occupied(monkeypatch, tmp_path):
    matrix = tmp_path / "SWITCH_MATRIX_TRB.csv.gz"
    with gzip.open(matrix, "wt") as handle:
        handle.write("# test canonical\n")
    db = tmp_path / "param_results_stocks.db"
    db.touch()
    rows = [["", "", f"v{i}", ""] + [""] * 9 for i in range(826)]
    records = [
        {
            "canonical_param": f"P{i}",
            "category": "ACTIONABLE_EXACT_ONLY",
            "current_exact_keys_tested": ["MU_LONG"] if i == 0 else [],
        }
        for i in range(826)
    ]
    header = ["a", "b", "value", "description"] + [f"h{i}" for i in range(8)] + ["MU_LONG"]
    monkeypatch.setattr(matrix_guard, "load", lambda: (header, rows))
    monkeypatch.setattr(
        uniqueness,
        "audit",
        lambda *, matrix_path, db_path: {
            "scope": {"campaign": matrix_guard.CURRENT_CAMPAIGN},
            "tim_policy": {"keys": ["MU_LONG"]},
            "rows": records,
        },
    )
    monkeypatch.setattr(
        matrix_guard,
        "cell_contract_category",
        lambda record, key, active: record["category"],
    )

    cells, occupied, target, source = runner.discover_cells(matrix, db)

    assert occupied == 1
    assert len(cells) == 825
    assert target == 826
    assert source["canonical_occupied_by_category"] == {"ACTIONABLE_EXACT_ONLY": 1}


def test_cross_parameter_proxy_fan_in_fails_before_simulation():
    plans = {
        ("SOURCE_A", "1"): ({"ENTRY_SCORE_THRESHOLD": 3.0}, {}, "OTHER"),
        ("SOURCE_B", "2"): ({"ENTRY_SCORE_THRESHOLD": 3.0}, {}, "OTHER"),
        # A same-parameter pair is left for the measured plateau audit, which
        # alone can apply the Bible's narrow five-value exception.
        ("SOURCE_C", "1"): ({"OTHER_FIELD": True}, {}, "ENTRY"),
        ("SOURCE_C", "2"): ({"OTHER_FIELD": True}, {}, "ENTRY"),
    }

    collisions = runner._cross_parameter_proxy_collisions(plans)

    assert set(collisions) == {("SOURCE_A", "1"), ("SOURCE_B", "2")}
    assert all("CROSS_PARAMETER_PROXY_RECIPE_COLLISION" in reason for reason in collisions.values())


def test_remaining_collision_adapters_use_direct_paths_or_stay_unavailable():
    registry = entry_adapter.build_registry(runner.ROOT)

    dc_overrides, dc_meta = entry_adapter.adapt(
        "DC_POSITION_ENTRY_THRESHOLD", 0.3125, "LONG", registry
    )
    assert dc_overrides == {
        "DC_POSITION_ENTRY_THRESHOLD": 0.3125,
        "DC_ENTRY_VETO_ENABLED_TRADIER": True,
    }
    assert dc_meta["approximation_confidence"] == "HIGH"
    assert dc_meta["approximation_mismatch_class"].startswith("DIRECT_VECTOR_PORT")

    lr_overrides, lr_meta = entry_adapter.adapt(
        "LR_BAND_ENTRY_ENABLED", True, "LONG", registry
    )
    assert lr_overrides == {"LR_BAND_ENTRY_ENABLED": True}
    assert lr_meta["approximation_confidence"] == "HIGH"

    assert "STDEV_BREAKOUT_RETEST_PCTB_MIN" not in registry


def test_prior_89_residual_lineage_is_a_disjoint_conserved_union():
    audited = []
    blanks = set()
    for record in runner._prior_residual_records():
        row = {
            "key": "MU_LONG",
            "param": record["param"],
            "value_json": record["value_norm"],
        }
        blanks.add(("MU_LONG", record["param"], record["value_norm"]))
        if record["param"] == "LR_BAND_ENTRY_ENABLED":
            row.update(status="MOVED", result_uniqueness_status="PASS")
        elif record["param"] == "DC_POSITION_ENTRY_THRESHOLD":
            row.update(
                status="DATA_UNAVAILABLE",
                result_uniqueness_status="NOT_APPLICABLE_DATA_UNAVAILABLE",
                causal_adapter_classification=(
                    "DATA_UNAVAILABLE_DEGENERATE_NO_ACTIVITY"
                ),
            )
        elif record["param"] == "STDEV_BREAKOUT_RETEST_PCTB_MIN":
            row.update(
                status="DATA_UNAVAILABLE",
                result_uniqueness_status="NOT_APPLICABLE_DATA_UNAVAILABLE",
                causal_adapter_classification=(
                    "DATA_UNAVAILABLE_UNSUPPORTED_CAUSAL_ADAPTER"
                ),
            )
        else:
            row.update(
                status="DATA_UNAVAILABLE",
                result_uniqueness_status="NOT_APPLICABLE_DATA_UNAVAILABLE",
                causal_adapter_classification=(
                    "DATA_UNAVAILABLE_INERT_CAUSAL_ADAPTER"
                ),
            )
        audited.append(row)

    transition = runner._prior_residual_transition(audited, blanks)

    assert transition["conservation_ok"] is True
    assert transition["bucket_counts"] == {
        "repaired_to_pass": 1,
        "reclassified_inert_unavailable": 81,
        "reclassified_other_unavailable": 7,
        "remaining_quarantined": 0,
        "protected_exact": 0,
        "unaccounted": 0,
    }
    bucket_ids = [
        logical_id
        for name in (
            "repaired_to_pass",
            "reclassified_inert_unavailable",
            "reclassified_other_unavailable",
            "remaining_quarantined",
            "protected_exact",
        )
        for logical_id in transition[name]
    ]
    assert len(bucket_ids) == len(set(bucket_ids)) == 89

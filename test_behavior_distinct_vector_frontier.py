import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path
import pytest

from tools.export_switch_matrix_xls import load_behavior_distinct_vector_overlay
from tools.fixed_axis_pipeline_preflight import verify as verify_fixed_axis_pipeline
from tools.run_behavior_distinct_vector_frontier import (
    action_fingerprint,
    build_candidates,
    build_fixed_axis_candidates,
    build_interaction_candidates,
    candidate_identity,
    condition_fixed_axis_candidates,
    fixed_axis_vector_support,
    norm_value,
    select_fixed_axis_context,
)


def test_action_fingerprint_is_order_and_quantity_sensitive():
    one = {"ts": 1.0, "type": "OPEN", "qty": 1.0, "price": 10.0}
    two = {"ts": 2.0, "type": "CLOSE", "qty": 1.0, "price": 11.0}
    assert action_fingerprint([one, two]) != action_fingerprint([two, one])
    assert action_fingerprint([one, two]) != action_fingerprint(
        [one, {**two, "qty": 0.5}]
    )


def test_candidate_identity_is_stable_and_companion_bound():
    first = candidate_identity("MU_LONG", "P", 1.0, {"P_ENABLED": True})
    assert first == candidate_identity(
        "MU_LONG", "P", 1, {"P_ENABLED": True}
    )
    assert first != candidate_identity(
        "MU_LONG", "P", 1, {"P_ENABLED": False}
    )


def test_norm_value_canonicalizes_numeric_and_boolean_aliases():
    assert norm_value("1.0") == norm_value(1) == "1"
    assert norm_value("true") == norm_value(True) == "true"


def test_mu_plan_is_source_connected_and_nonrepetitive():
    candidates, receipt = build_candidates(
        __import__("pathlib").Path(__file__).resolve().parent,
        "MU_LONG",
        points=12,
    )
    assert receipt["selected_source_connected_parameters"] > 0
    logical = {(row["param"], norm_value(row["value"])) for row in candidates}
    assert len(logical) == len(candidates)
    assert len({row["candidate_id"] for row in candidates}) == len(candidates)
    assert all(row["binding_evidence"] == "DIRECT_LIVE_CONFIG_READ" for row in candidates)
    assert all(row["key"] == "MU_LONG" for row in candidates)
    assert all(json.loads(row["value_json"]) == row["value"] for row in candidates)


def test_fixed_axis_support_adds_only_direct_live_vector_readers():
    root = Path(__file__).resolve().parent
    support, receipt = fixed_axis_vector_support(root)
    added = set(receipt["post_inventory_supported_parameter_names"])
    assert added == {
        "COUNTER_TREND_SMA200_BYPASS_ENABLED",
        "DC_ENTRY_VETO_ENABLED_TRADIER",
        "DC_POSITION_ENTRY_THRESHOLD",
        "EXIT_MAX_HOLD_ENABLED",
        "EXIT_MAX_HOLD_MINUTES",
        "GR_HTF_DIRECT_EXIT_ENABLED",
        "GR_HTF_DIRECT_EXIT_SCORE",
        "LR_BAND_ENTRY_ENABLED",
        "LR_BAND_ENTRY_LO",
        "LR_BAND_ENTRY_R2_MIN",
        "LR_BAND_REGIME_ENABLED",
        "LR_BAND_REGIME_MAX_PB",
        "LR_BAND_HARVEST_ENABLED",
        "LR_BAND_HARVEST_FRAC",
        "LR_BAND_HARVEST_HI",
        "LR_BAND_SLOPE_FLIP_EXIT_ENABLED",
        "LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN",
        "LR_BAND_SLOPE_FLIP_MIN_PCT_DAY",
        "WT_DC_LONG_ENABLED",
        "WT_DC_SHORT_ENABLED",
        "WT_DC_EXIT_ENABLED",
        "WT_DC_EXIT_STALE_MAX_S",
        "WT_DC_EXIT_THRESHOLD",
    }
    assert all(
        support[name]["proof"] == "POST_INVENTORY_DIRECT_LIVE_AND_VECTOR_READ"
        for name in added
    )
    # This is a declared/dynamically applicable knob but has no vector decision
    # reader.  It must not gain coverage merely because setattr accepts it.
    assert "LR_BAND_ENTRY_PRIORITY" not in support


def test_fixed_axis_runner_fails_closed_to_supported_semantics_only():
    root = Path(__file__).resolve().parent
    workbook = (
        root
        / "data/sync/current_matrix_surface_history"
        / "1785697632_80e4d4cdc1a09dd6/SWITCH_MATRIX_TRB.xlsx"
    )
    candidates, plan = build_fixed_axis_candidates(
        root,
        "MU_LONG",
        points=0,
        fixed_workbook=workbook,
    )
    assert plan["authoritative_fixed_axes"] == 3618
    assert plan["planned_candidates"] <= 370
    assert plan["rejected_reason_counts"]["UNSUPPORTED_FIXED_AXIS_SEMANTICS"] > 3000
    assert all(
        row["fixed_axis_priority_class"]
        in {"SOURCE_CONNECTED_FIXED_AXIS", "POST_INVENTORY_PROVEN_FIXED_AXIS"}
        for row in candidates
    )
    assert all(row["vector_support_proof"] for row in candidates)


def test_fixed_axis_pipeline_preflight_hash_binds_vector_engine():
    root = Path(__file__).resolve().parent
    workbook = (
        root / "data/sync/current_matrix_surface_history"
        / "1785697632_80e4d4cdc1a09dd6/SWITCH_MATRIX_TRB.xlsx"
    )
    result = verify_fixed_axis_pipeline(
        root,
        root / "data/reports/FIXED_AXIS_VECTOR_SUPPORT_CURRENT.json",
        workbook,
    )
    assert result["status"] == "PASS"
    assert result["failures"] == []


def test_fixed_axis_context_is_train_only_hash_bound_and_immutable(tmp_path):
    root = Path(__file__).resolve().parent
    output = tmp_path / "NVDA_LONG_CONTEXT.json"
    receipt = select_fixed_axis_context(root, "NVDA_LONG", output)
    assert receipt["status"] == "PASS"
    assert receipt["selection_split"] == "TRAIN_ONLY"
    assert receipt["holdout_used_for_selection"] is False
    assert receipt["untouched_final_used_for_selection"] is False
    assert receipt["nested_validation_schema"] == "behavior-frontier-nested-validation-v2-capital"
    assert receipt["capital_accounting_version"] == "avg-trade-deployed-2000-v1-vector-event-ledger"
    assert receipt["selected_train_metrics"]["alpha_vs_bh_pp"] > 0
    assert receipt["selected_train_metrics"]["real_closes"] > 10
    assert receipt["overrides"]
    assert select_fixed_axis_context(root, "NVDA_LONG", output) == receipt


def test_fixed_axis_context_fails_closed_without_train_qualifier(tmp_path):
    root = Path(__file__).resolve().parent
    with pytest.raises(SystemExit, match="FIXED_CONTEXT_GUARD_FAILED"):
        select_fixed_axis_context(root, "MU_LONG", tmp_path / "MU_CONTEXT.json")


def test_fixed_axis_same_knob_probe_is_retained_and_context_bound():
    candidate = {
        "candidate_id": "axis-id",
        "overrides": {"WT_DC_ENTRY_THRESHOLD": 2},
    }
    context = {
        "candidate_id": "context-id",
        "context_sha256": "c" * 64,
        "override_sha256": "o" * 64,
        "overrides": {"WT_DC_ENTRY_THRESHOLD": 5, "WT_DC_LONG_ENABLED": True},
    }
    conditioned = condition_fixed_axis_candidates([candidate], context)
    assert len(conditioned) == 1
    assert conditioned[0]["unconditioned_candidate_id"] == "axis-id"
    assert conditioned[0]["axis_supersedes_context_same_knob"] is True
    assert conditioned[0]["overrides"]["WT_DC_ENTRY_THRESHOLD"] == 2
    assert conditioned[0]["context_overrides"]["WT_DC_ENTRY_THRESHOLD"] == 5


def test_finalized_frontier_loader_is_hash_bound_and_diagnostic_only(tmp_path):
    reports = tmp_path / "data/reports"
    key_dir = reports / "behavior_distinct_vector_frontier/MU_LONG"
    raw_dir = key_dir / "raw/contract"
    raw_dir.mkdir(parents=True)
    protected = reports / "PILOT_V8_CELLS_IMMUTABLE.jsonl"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("\n")
    raw = raw_dir / "shard_00.jsonl"
    raw.write_text('{"candidate_id":"one"}\n')
    accepted = key_dir / "accepted_index.jsonl"
    accepted_row = {
        "key": "MU_LONG",
        "param": "P",
        "value_json": "1.25",
        "status": "MOVED",
        "real_closes": 2,
        "delta_gain_mo_vs_bh_diagnostic": 0.123456789,
        "action_fingerprint": "action-one",
        "frontier_acceptance_status": "ACCEPTED_UNIQUE_MEASURED",
        "vector_evidence_class": "BEHAVIOR_DISTINCT_VECTOR_FRONTIER",
        "exact_completion_credit": False,
        "engine_ranking_allowed": False,
        "promotion_allowed": False,
        "live_config_write_allowed": False,
        "db_engine_write_allowed": False,
    }
    accepted.write_text(json.dumps(accepted_row) + "\n")

    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = {
        "schema": "behavior-distinct-vector-frontier-receipt-v1",
        "status": "PASS",
        "key": "MU_LONG",
        "accepted_unique_measured_cells": 1,
        "accepted_index_sha256": digest(accepted),
        "raw_shards": [{"path": "/s1/raw/contract/shard_00.jsonl", "sha256": digest(raw)}],
        "protected_evidence": {
            "sources": [
                {"path": "/s1/PILOT_V8_CELLS_IMMUTABLE.jsonl", "sha256": digest(protected)}
            ]
        },
        "integrity_contract": {
            name: False
            for name in (
                "zero_allowed",
                "nonfinite_allowed",
                "duplicate_metric_within_key_allowed",
                "duplicate_action_fingerprint_within_key_allowed",
                "synthetic_jitter_allowed",
                "copied_result_allowed",
                "preserved_v8_overwrite_allowed",
            )
        },
        "preserved_v8_cells_written": False,
        "database_written": False,
        "workbook_written": False,
        "live_config_written": False,
    }
    receipt_path = key_dir / "FRONTIER_RECEIPT.json"
    receipt_path.write_text(json.dumps(receipt))

    overlay, audit = load_behavior_distinct_vector_overlay(tmp_path)
    assert audit["accepted_rows"] == 1
    item = overlay[("P", "1.25", "MU_LONG")]
    assert item["vector_evidence_class"] == "VEC_APPROX"
    assert item["frontier_vector_evidence_class"] == "BEHAVIOR_DISTINCT_VECTOR_FRONTIER"
    assert item["exact_completion_credit"] is False

    raw.write_text("tampered\n")
    overlay, audit = load_behavior_distinct_vector_overlay(tmp_path)
    assert overlay == {}
    assert "raw shard hash mismatch" in audit["rejected_keys"][0]["reason"]


def test_collision_watchdog_fails_closed_before_runner_when_frontier_incomplete(tmp_path):
    script = Path(__file__).resolve().parent / "tools/s1_full_precision_collision_watchdog.sh"
    env = {
        **os.environ,
        "S1_COLLISION_ROOT": str(tmp_path),
        "S1_COLLISION_PYTHON": sys.executable,
        "S1_COLLISION_LOG_DIR": str(tmp_path / "logs"),
        "S1_COLLISION_LOCK": str(tmp_path / "logs/collision.lock"),
    }
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0
    assert not (
        tmp_path
        / "data/reports/vector_capacity_runtime_20260802/full_precision_collision/runner.pid"
    ).exists()


def test_collision_watchdog_requires_nested_all_fold_strict_admission():
    script = (
        Path(__file__).resolve().parent
        / "tools/s1_full_precision_collision_watchdog.sh"
    ).read_text()
    assert "behavior_frontier_nested_validation" in script
    assert "behavior_frontier_nested_validation_v2" in script
    assert "behavior-frontier-nested-validation-v2-capital" in script
    assert "avg-trade-deployed-2000-v1-vector-event-ledger" in script
    assert 'payload.get("validator_source_sha256") == source_sha' in script
    assert 'payload.get("status") == "PASS"' in script
    assert 'int(payload.get("all_fold_strict_survivors") or 0) > 0' in script
    assert '[[ "$validation_status" == PASS ]] || exit 0' in script


def test_acn_frontiers_reserve_headroom_with_four_concurrent_shards():
    root = Path(__file__).resolve().parent
    for name in (
        "tools/s1_behavior_distinct_vector_watchdog.sh",
        "tools/s1_behavior_interaction_watchdog.sh",
    ):
        script = (root / name).read_text()
        assert 'if [[ "$key" == ACN_SHORT && "$key_launch_limit" -gt 4 ]]' in script
        assert "key_launch_limit=4" in script


def test_nested_watchdog_archives_stale_receipt_before_remeasurement():
    script = (
        Path(__file__).resolve().parent
        / "tools/s1_behavior_interaction_watchdog.sh"
    ).read_text()
    assert "behavior_frontier_nested_validation_history" in script
    assert 'cp -a "$VALIDATION_OUT/$key/." "$history/"' in script
    assert 'chmod -R a-w "$history"' in script


def test_frontier_runner_has_nonblocking_per_contract_shard_lock():
    source = (
        Path(__file__).resolve().parent
        / "tools/run_behavior_distinct_vector_frontier.py"
    ).read_text()
    assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in source
    assert "SHARD_ALREADY_RUNNING" in source
    assert "run_contract_sha[:20]" in source


def test_c6_interaction_plan_uses_unique_c5_action_boundary_bundles():
    root = Path(__file__).resolve().parent
    candidates, plan = build_interaction_candidates(
        root, "MU_LONG", points=76, max_candidates=250
    )
    assert len(candidates) == 250
    assert plan["c5_accepted_seed_rows"] == 438
    assert plan["candidate_generation"].startswith("deterministic 2-4 member")
    assert len({row["candidate_id"] for row in candidates}) == len(candidates)
    assert len({row["value"]["override_sha256"] for row in candidates}) == len(candidates)
    assert all(2 <= row["bundle_member_count"] <= 4 for row in candidates)
    assert all(
        row["binding_evidence"] == "C5_ACCEPTED_UNIQUE_ACTION_BOUNDARY"
        and len(row["c5_seed_action_fingerprints"]) == row["bundle_member_count"]
        for row in candidates
    )

import json

from tools.run_full_precision_collision_reruns import (
    execution_identity,
    load_result_state,
    logical_run_identity,
    retryable_logical,
    result_identity,
    select_representatives,
)
from collections import defaultdict


def _row(key, metric, fp, contract, ts, param, value):
    return {
        "key": key,
        "delta_gain_mo_vs_bh": metric,
        "trades_fingerprint": fp,
        "contract_fingerprint": contract,
        "ts": ts,
        "param": param,
        "value_json": json.dumps(value),
        "overrides_json": json.dumps({param: value}),
    }


def test_selects_one_representative_per_distinct_schedule_and_prefers_c5():
    rows = [
        _row("VT_LONG", 1.0, "fp-a", "c2", "2026-01-02", "P", 1),
        _row(
            "VT_LONG", 1.0, "fp-a", "tradier-matrix-exec-c5:x",
            "2026-01-01", "P", 2,
        ),
        _row("VT_LONG", 1.0, "fp-b", "c2", "2026-01-03", "Q", 3),
        _row("VT_LONG", 2.0, "fp-only", "c2", "2026-01-04", "R", 4),
        _row("NOT_A_PILOT_LONG", 1.0, "fp-c", "c2", "2026-01-05", "S", 5),
    ]
    selected = select_representatives(rows)

    assert len(selected) == 2
    assert {row["trades_fingerprint"] for row in selected} == {"fp-a", "fp-b"}
    chosen_a = next(row for row in selected if row["trades_fingerprint"] == "fp-a")
    assert chosen_a["contract_fingerprint"].startswith("tradier-matrix-exec-c5")


def test_missing_fingerprint_collision_rows_are_opt_in_replay_candidates():
    known = _row("MU_LONG", 1.0, "fp-a", "c5", "2026", "P", 1)
    missing = _row("MU_LONG", 1.0, None, "legacy", "2025", "Q", 2)

    assert select_representatives([known, missing], 10) == []
    selected = select_representatives(
        [known, missing], 10, include_missing_fingerprints=True
    )

    assert len(selected) == 2
    assert {
        row["collision_replay_reason"] for row in selected
    } == {
        "DISTINCT_ACTIONS_EQUAL_STORED_METRIC",
        "MISSING_ACTION_FINGERPRINT_IN_NUMERIC_COLLISION",
    }


def test_logical_identity_is_stable_across_deployment_and_changes_with_setting():
    row = _row("MU_LONG", 1.0, "fp-a", "c5", "2026", "P", 1)
    row["collision_metric"] = 1.0
    first = result_identity(row, "deploy-a")
    second = result_identity(row, "deploy-b")
    row["overrides_json"] = json.dumps({"P": 2})
    third = result_identity(row, "deploy-a")

    assert first == second
    assert first != third


def test_logical_identity_changes_with_real_engine_contract():
    row = _row("MU_LONG", 1.0, "fp-a", "c5", "2026", "P", 1)
    row["target_contract_fingerprint"] = "engine-a"
    first = logical_run_identity(row)
    row["target_contract_fingerprint"] = "engine-b"
    assert first != logical_run_identity(row)


def test_execution_identity_distinguishes_deployment_and_attempt():
    row = _row("MU_LONG", 1.0, "fp-a", "c5", "2026", "P", 1)
    assert execution_identity(row, "a", 1) != execution_identity(row, "b", 1)
    assert execution_identity(row, "a", 1) != execution_identity(row, "a", 2)


def test_result_state_counts_unique_logical_passes_across_deployments(tmp_path):
    row = _row("MU_LONG", 1.0, "fp-a", "c5", "2026", "P", 1)
    logical = logical_run_identity(row)
    override_sha = __import__("hashlib").sha256(
        json.dumps({"P": 1}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps(
                {
                    "key": "MU_LONG",
                    "override_sha256": override_sha,
                    "deployment_sha256": deployment,
                    "run_identity": old_execution_id,
                    "status": status,
                }
            )
            for deployment, old_execution_id, status in (
                ("old", "old-id", "PASS"),
                ("new", "new-id", "FAIL_RUN_EXCEPTION"),
            )
        )
        + "\n"
    )

    state = load_result_state(ledger)
    assert state["ledger_rows"] == 2
    assert state["attempts"][logical] == 2
    assert state["passed"] == {logical}


def test_legacy_exception_without_override_hash_maps_to_selected_cell(tmp_path):
    row = _row("VT_LONG", 1.0, "fp-a", "c5", "2026", "P", 1)
    logical = logical_run_identity(row)
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "key": "VT_LONG",
                "param": "P",
                "value_json": "1",
                "status": "FAIL_RUN_EXCEPTION",
            }
        )
        + "\n"
    )

    state = load_result_state(ledger, {("VT_LONG", "P", "1"): logical})
    assert state["ledger_rows"] == 1
    assert state["unindexed_ledger_rows"] == 0
    assert state["attempts"][logical] == 1


def test_engine_no_result_is_not_retried_for_same_logical_setting():
    state = {
        "attempts": defaultdict(int, {"logical": 1}),
        "latest_status": {"logical": "FAIL_ENGINE_NO_RESULT"},
    }
    assert not retryable_logical(state, "logical", 3)


def test_transient_exception_is_bounded_and_retryable():
    state = {
        "attempts": defaultdict(int, {"logical": 1}),
        "latest_status": {"logical": "FAIL_RUN_EXCEPTION"},
    }
    assert retryable_logical(state, "logical", 3)
    state["attempts"]["logical"] = 3
    assert not retryable_logical(state, "logical", 3)

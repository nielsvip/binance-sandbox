import hashlib
import json
from pathlib import Path

from tools import build_matrix_symbol_side_achievement as achievement


def test_s1_absolute_tools_launch_can_import_tools_package(tmp_path):
    import subprocess
    import sys

    script = Path(__file__).resolve().parent / "tools/build_matrix_symbol_side_achievement.py"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import runpy; runpy.run_path({str(script)!r}, run_name='achievement_import_probe')",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _artifact(symbol="ABC", side="LONG", family="ENTRY_STOCH_HHHL"):
    fold_metrics = {
        "capital_return_pct": 12.0,
        "bh_capital_return_pct": 4.0,
        "entry_capacity_breach": False,
        "bars_flat_beyond_reclaim": 0,
    }
    return {
        "manifest": {
            "tier": "VEC_RESEARCH",
            "symbol": symbol,
            "side": side,
            "family": family,
            "real_entry_gate_mask": {
                "schema": "tradier-direct-entry-gate-mask-v1",
                "family": "ENTRY_STOCH_HHHL",
                "applied_to_entry_signal": True,
                "accepted_rows": 8,
            },
        },
        "outer_folds": [
            {
                "beats_bh": True,
                "beats_control": True,
                "exposure_policy_pass": True,
                "validation_metrics": fold_metrics,
            },
            {
                "beats_bh": True,
                "beats_control": True,
                "exposure_policy_pass": True,
                "validation_metrics": dict(fold_metrics),
            },
        ],
        "aggregate": {
            "vector_survivor": True,
            "all_folds_beat_bh": True,
            "all_folds_beat_control": True,
            "all_mandatory_reclaim": True,
            "all_capacity_safe": True,
            "all_folds_exposure_policy_pass": True,
            "future_htf_count": 0,
            "exposure_policy": {"pass": True},
        },
    }


def _candidate(path: Path, digest: str, *, closes=11):
    return {
        "key": "ABC_LONG",
        "hotlist_eligible": True,
        "lifecycle_all_folds_strict_gate": True,
        "entry_family": "ENTRY_STOCH_HHHL",
        "strategy_gain_per_month_pct": 8.0,
        "bh_gain_per_month_pct": 3.0,
        "ledger_backed_vector_close_fills": closes,
        "source_receipt": "data/reports/vec_research/campaign_receipt.json",
        "source_receipt_sha256": "b" * 64,
        "complete_recipe": {
            "schema": "complete-vector-lifecycle-recipe-v1",
            "ENTRY": {
                "family": "ENTRY_STOCH_HHHL",
                "artifact": str(path.parent),
                "artifact_result_sha256": digest,
                "params": {"enabled_tfs": ["1h"], "min_confirming_tfs": 1, "stoch_threshold": 20},
            },
            "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {}},
        },
    }


def test_strict_complete_ordinary_frontier_candidate_enters_ordinary_pool():
    recipe = {
        "schema": "complete-vector-lifecycle-recipe-v1",
        "ENTRY": {"family": "ENTRY_LADDER_GREEN"},
        "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"},
    }
    recipe_sha = hashlib.sha256(
        json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    candidate = {
        "key": "ABC_LONG",
        "entry_family": "ENTRY_LADDER_GREEN",
        "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        "hotlist_eligible": True,
        "lifecycle_all_folds_strict_gate": True,
        "complete_recipe": recipe,
        "shared_route_vector_parity_pass": True,
        "shared_route_vector_parity": {
            "schema": "shared-route-frozen-vector-parity-v1",
            "key": "ABC_LONG",
            "complete_recipe_sha256": recipe_sha,
            "entry_family": "ENTRY_LADDER_GREEN",
            "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
            "shared_entry_route_fired": True,
            "shared_exit_route_fired": True,
            "real_close_trades": 11,
            "strategy_return_pct": 12.0,
            "bh_return_pct": 4.0,
        },
    }

    assert achievement.ordinary_frontier_candidate(candidate) is True

    direct_entry = {
        **candidate,
        "entry_family": "ENTRY_STOCH_HHHL",
        "complete_recipe": {
            **candidate["complete_recipe"],
            "ENTRY": {"family": "ENTRY_STOCH_HHHL"},
        },
    }
    assert achievement.ordinary_frontier_candidate(direct_entry) is False

    non_strict = {**candidate, "lifecycle_all_folds_strict_gate": False}
    assert achievement.ordinary_frontier_candidate(non_strict) is False

    missing_frozen_shared_route_proof = {
        key: value
        for key, value in candidate.items()
        if not key.startswith("shared_route_vector_parity")
    }
    assert achievement.ordinary_frontier_candidate(missing_frozen_shared_route_proof) is False


def _write_artifact(root: Path):
    path = root / "data/reports/vec_research/v3/entry/result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_artifact(), sort_keys=True))
    return path


def test_hash_bound_v3_gate_aware_survivor_is_admitted(tmp_path):
    result_path = _write_artifact(tmp_path)
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()

    evidence = achievement.gate_aware_vector_evidence(
        tmp_path, "ABC_LONG", _candidate(result_path, digest)
    )

    assert evidence["gate_aware_vector_pass"] is True
    assert evidence["artifact_sha256"] == digest
    assert evidence["real_close_trades"] == 11
    assert evidence["alpha_vs_bh_per_month_pp"] == 5.0
    assert evidence["fold_count"] == 2


def test_xle_and_dino_counter_trend_canaries_quarantine_only_the_same_recipe(tmp_path):
    result_path = _write_artifact(tmp_path)
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    for key, family in (
        ("XLE_LONG", "ENTRY_4H_DEEP_VALUE"),
        ("DINO_LONG", "ENTRY_BB_RECOVERY"),
    ):
        candidate = _candidate(result_path, digest)
        candidate["key"] = key
        candidate["entry_family"] = family
        candidate["complete_recipe"]["ENTRY"]["family"] = family
        batch_row = {
            "key": key,
            "run_id": f"canary_{key}",
            "exact_v8_status": "BLOCKED_SHARED_DIRECT_ROUTE_CANARY",
            "complete_recipe": candidate["complete_recipe"],
            "shared_direct_route_canary": {
                "pass": False,
                "entry_family": family,
                "blockers": ["CANARY_QUEUE_GATE_BLOCK:COUNTER_TREND_ADD_BLOCK"],
                "attempts": [{"queue_gate_blocks": {"COUNTER_TREND_ADD_BLOCK": 1}}],
            },
        }
        rejection = achievement.direct_canary_rejection(candidate, [batch_row])
        assert rejection == {
            "blocker": "CANARY_QUEUE_GATE_BLOCK:COUNTER_TREND_ADD_BLOCK",
            "run_id": f"canary_{key}",
            "queue_gate_blocks": {"COUNTER_TREND_ADD_BLOCK": 1},
        }

        altered = {**candidate, "complete_recipe": {**candidate["complete_recipe"]}}
        altered["complete_recipe"]["ENTRY"] = {
            **candidate["complete_recipe"]["ENTRY"], "params": {"changed": True}
        }
        assert achievement.direct_canary_rejection(altered, [batch_row]) is None


def test_weekly_activity_ranking_cannot_veto_over_10_strict_recipe(tmp_path):
    result_path = _write_artifact(tmp_path)
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    candidate = _candidate(result_path, digest, closes=17)
    candidate["hotlist_eligible"] = False
    candidate["weekly_activity_gate"] = False
    candidate["tim_pct"] = 78.29

    evidence = achievement.gate_aware_vector_evidence(
        tmp_path, "ABC_LONG", candidate
    )

    assert evidence["gate_aware_vector_pass"] is True
    assert evidence["real_close_trades"] == 17


def test_tampered_result_cannot_reuse_receipt_hash(tmp_path):
    result_path = _write_artifact(tmp_path)
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    result_path.write_text(json.dumps(_artifact(family="ENTRY_BB_RECOVERY")))

    evidence = achievement.gate_aware_vector_evidence(
        tmp_path, "ABC_LONG", _candidate(result_path, digest)
    )

    assert evidence == {
        "gate_aware_vector_pass": False,
        "blocker": "ENTRY_ARTIFACT_HASH_MISMATCH",
        "artifact": "data/reports/vec_research/v3/entry/result.json",
        "expected_sha256": digest,
        "actual_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
    }


def test_missing_mask_or_insufficient_close_ledger_is_rejected(tmp_path):
    result_path = _write_artifact(tmp_path)
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    payload = _artifact()
    del payload["manifest"]["real_entry_gate_mask"]
    result_path.write_text(json.dumps(payload, sort_keys=True))
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()

    missing_mask = achievement.gate_aware_vector_evidence(
        tmp_path, "ABC_LONG", _candidate(result_path, digest)
    )
    assert missing_mask["blocker"] == "DIRECT_ENTRY_GATE_MASK_NOT_PROVEN"

    result_path.write_text(json.dumps(_artifact(), sort_keys=True))
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    closes = achievement.gate_aware_vector_evidence(
        tmp_path, "ABC_LONG", _candidate(result_path, digest, closes=10)
    )
    assert closes == {
        "gate_aware_vector_pass": False,
        "blocker": "REAL_CLOSES_NOT_OVER_10",
    }


def test_negative_strategy_cannot_enter_exact_even_when_bh_is_more_negative(tmp_path):
    result_path = _write_artifact(tmp_path)
    payload = _artifact()
    for fold in payload["outer_folds"]:
        fold["validation_metrics"]["capital_return_pct"] = -2.0
        fold["validation_metrics"]["bh_capital_return_pct"] = -8.0
    result_path.write_text(json.dumps(payload, sort_keys=True))
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    candidate = _candidate(result_path, digest)
    candidate["strategy_gain_per_month_pct"] = -2.0
    candidate["bh_gain_per_month_pct"] = -8.0

    evidence = achievement.gate_aware_vector_evidence(
        tmp_path, "ABC_LONG", candidate
    )

    assert evidence == {
        "gate_aware_vector_pass": False,
        "blocker": "V3_PER_FOLD_CONTRACT_FAILED",
    }


def test_complete_classic_formation_combo_is_hash_bound_and_admitted(tmp_path):
    campaign_request_id = "classic-formations-test-request-v5"
    request_path = tmp_path / "data/sync/CLASSIC_FORMATION_S1_REQUEST.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text(
        json.dumps({"request_id": campaign_request_id}, sort_keys=True)
    )
    (tmp_path / "classic_formations.py").write_text("shared-selector")
    (tmp_path / "v8_vec_sweep.py").write_text("parent-id-aware-loader")
    npz = tmp_path / "data/frozen/ABC.npz"
    npz.parent.mkdir(parents=True)
    npz.write_bytes(b"frozen-bars")
    artifact_dir = tmp_path / "data/reports/vec_research/formations/ABC_LONG/entry"
    artifact_dir.mkdir(parents=True)
    recipe = {
        "schema": "complete-vector-lifecycle-recipe-v1",
        "KEY": "ABC_LONG",
        "ENTRY": {
            "family": "ENTRY_CLASSIC_FORMATION_TREND_STRUCTURE",
            "params": {
                "timeframe": "ALL",
                "min_score": 0.65,
                "position_size_mult": 1.0,
            },
            "artifact": "data/reports/vec_research/formations/ABC_LONG/entry",
        },
        "EXIT": {
            "family": "EXIT_CLASSIC_FORMATION_TREND_STRUCTURE",
            "params": {
                "timeframe": "ALL",
                "min_score": 0.65,
                "exit_min_gain_pct": 0.0,
            },
        },
        "REENTER": {"family": "MANDATORY_ZERO_BUFFER_RECLAIM"},
    }
    semantic_sha = hashlib.sha256(
        json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    ledger = [
        {"ts": 1, "type": "OPEN", "reason": "CLASSIC_FORMATION_ENTRY_TREND_STRUCTURE_score0.8"},
        {"ts": 2, "type": "CLOSE", "reason": "CLASSIC_FORMATION_EXIT_TREND_STRUCTURE_score0.8"},
    ]
    ledger_sha = hashlib.sha256(
        json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    metrics = {
        "return_pct": 12.0,
        "bh_return_pct": 4.0,
        "alpha_vs_bh_pp": 8.0,
        "trades": 11,
        "formation_entry_action_count": 2,
        "formation_exit_action_count": 2,
    }
    artifact = {
        "schema": "classic-formation-shared-vector-artifact-v1",
        "manifest": {
            "schema": "classic-formation-shared-vector-artifact-v1",
            "key": "ABC_LONG",
            "family": recipe["ENTRY"]["family"],
            "exit_family": recipe["EXIT"]["family"],
            "npz": "data/frozen/ABC.npz",
            "npz_sha256": hashlib.sha256(npz.read_bytes()).hexdigest(),
            "classic_formations_sha256": hashlib.sha256(
                (tmp_path / "classic_formations.py").read_bytes()
            ).hexdigest(),
            "v8_vec_sweep_sha256": hashlib.sha256(
                (tmp_path / "v8_vec_sweep.py").read_bytes()
            ).hexdigest(),
            "campaign_request_id": campaign_request_id,
            "shared_selector": "classic_formations.py:select_latest_formation",
            "complete_recipe_semantic_sha256": semantic_sha,
        },
        "outer_folds": [
            {
                "name": name,
                "metrics": metrics,
                "baseline_return_pct": 5.0,
                "event_ledger": ledger,
                "event_ledger_sha256": ledger_sha,
                "event_path_audit": {
                    "schema": "classic-formation-complete-recipe-event-path-audit-v1",
                    "pass": True,
                },
                "event_path_audit_sha256": hashlib.sha256(
                    json.dumps(
                        {
                            "schema": "classic-formation-complete-recipe-event-path-audit-v1",
                            "pass": True,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "passes": True,
            }
            for name in ("train", "holdout")
        ],
        "aggregate": {
            "train_pass": True,
            "holdout_pass": True,
            "all_windows_positive_return": True,
            "all_windows_beat_bh": True,
            "all_windows_improve_baseline": True,
            "both_formation_routes_fired": True,
            "over_10_real_closes_each_window": True,
        },
    }
    artifact_path = artifact_dir / "result.json"
    artifact_path.write_text(json.dumps(artifact, sort_keys=True))
    recipe["ENTRY"]["artifact_result_sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()
    candidate = {
        "key": "ABC_LONG",
        "entry_family": recipe["ENTRY"]["family"],
        "exit_family": recipe["EXIT"]["family"],
        "classic_formation_vector_pass": True,
        "exact_v8_candidate_authorized": True,
        "classic_formations_sha256": artifact["manifest"]["classic_formations_sha256"],
        "v8_vec_sweep_sha256": artifact["manifest"]["v8_vec_sweep_sha256"],
        "campaign_request_id": campaign_request_id,
        "hotlist_eligible": True,
        "lifecycle_all_folds_strict_gate": True,
        "strategy_return_pct": 12.0,
        "bh_return_pct": 4.0,
        "ledger_backed_vector_close_fills": 11,
        "complete_recipe": recipe,
    }

    evidence = achievement.classic_formation_vector_evidence(
        tmp_path, "ABC_LONG", candidate
    )
    assert evidence["classic_formation_vector_pass"] is True
    assert evidence["exact_v8_candidate_authorized"] is True
    assert evidence["alpha_vs_bh_pp"] == 8.0

    (tmp_path / "v8_vec_sweep.py").write_text("changed-loader")
    stale_loader = achievement.classic_formation_vector_evidence(
        tmp_path, "ABC_LONG", candidate
    )
    assert stale_loader["blocker"] == "CLASSIC_FORMATION_ARTIFACT_SCHEMA_INVALID"
    (tmp_path / "v8_vec_sweep.py").write_text("parent-id-aware-loader")

    mixed_ledger = ledger + [
        {"ts": 3, "type": "OPEN", "reason": "GOLDEN_RULE_ENTRY"},
        {"ts": 4, "type": "REDUCE", "reason": "PPL_TP_1"},
    ]
    mixed_ledger_sha = hashlib.sha256(
        json.dumps(mixed_ledger, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    for fold in artifact["outer_folds"]:
        fold["event_ledger"] = mixed_ledger
        fold["event_ledger_sha256"] = mixed_ledger_sha
    artifact_path.write_text(json.dumps(artifact, sort_keys=True))
    candidate["complete_recipe"]["ENTRY"]["artifact_result_sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()
    mixed = achievement.classic_formation_vector_evidence(
        tmp_path, "ABC_LONG", candidate
    )
    assert mixed["classic_formation_vector_pass"] is False
    assert mixed["blocker"] == "CLASSIC_FORMATION_UNDECLARED_EVENT_PATH_MISMATCH"
    assert mixed["evidence_class"] == "GROUP_VECTOR_RESEARCH"
    assert mixed["exact_admission_authorized"] is False
    assert mixed["event_path_audits"]["holdout"]["undeclared_event_count"] == 2

    artifact_path.write_text(json.dumps({**artifact, "aggregate": {}}, sort_keys=True))
    rejected = achievement.classic_formation_vector_evidence(
        tmp_path, "ABC_LONG", candidate
    )
    assert rejected["blocker"] == "CLASSIC_FORMATION_ARTIFACT_HASH_MISMATCH"

def test_mask_audit_that_was_not_applied_cannot_admit_recipe(tmp_path):
    result_path = _write_artifact(tmp_path)
    payload = _artifact()
    payload["manifest"]["real_entry_gate_mask"]["applied_to_entry_signal"] = False
    result_path.write_text(json.dumps(payload, sort_keys=True))
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()

    evidence = achievement.gate_aware_vector_evidence(
        tmp_path, "ABC_LONG", _candidate(result_path, digest)
    )

    assert evidence == {
        "gate_aware_vector_pass": False,
        "blocker": "DIRECT_ENTRY_GATE_MASK_NOT_APPLIED",
    }


def test_per_fold_bh_contract_is_not_inferred_from_aggregate(tmp_path):
    result_path = _write_artifact(tmp_path)
    payload = _artifact()
    payload["outer_folds"][1]["beats_bh"] = False
    result_path.write_text(json.dumps(payload, sort_keys=True))
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()

    evidence = achievement.gate_aware_vector_evidence(
        tmp_path, "ABC_LONG", _candidate(result_path, digest)
    )

    assert evidence == {
        "gate_aware_vector_pass": False,
        "blocker": "V3_PER_FOLD_CONTRACT_FAILED",
    }

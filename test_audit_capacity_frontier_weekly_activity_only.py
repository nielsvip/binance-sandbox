import hashlib
import json
from pathlib import Path

from tools import audit_capacity_frontier_weekly_activity_only as audit


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def _artifact() -> dict:
    metrics = {
        "capital_return_pct": 12.0,
        "bh_capital_return_pct": 4.0,
        "entry_capacity_breach": False,
        "bars_flat_beyond_reclaim": 0,
    }
    return {
        "manifest": {
            "symbol": "ABC",
            "side": "LONG",
            "family": "ENTRY_STOCH_HHHL",
            "real_entry_gate_mask": {
                "schema": "tradier-direct-entry-gate-mask-v1",
                "family": "ENTRY_STOCH_HHHL",
                "applied_to_entry_signal": True,
            },
        },
        "outer_folds": [
            {
                "beats_bh": True,
                "beats_control": True,
                "exposure_policy_pass": True,
                "validation_metrics": dict(metrics),
            }
            for _ in range(2)
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


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    campaign = (
        tmp_path
        / "data/reports/vec_research/capacity_frontier_test_gate_aware"
    )
    _write(campaign / "campaign_manifest.json", {"cohort_keys": ["ABC_LONG"]})
    artifact = campaign / "keys/ABC_LONG/entries/ENTRY_STOCH_HHHL/run/result.json"
    _write(artifact, _artifact())
    artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    candidate = {
        "key": "ABC_LONG",
        "hotlist_eligible": False,
        "weekly_activity_gate": False,
        "lifecycle_all_folds_strict_gate": True,
        "tim_gate": True,
        "causal_capacity_gate": True,
        "strictly_later_reentry_gate": True,
        "complete_recipe_gate": True,
        "beats_bh_gate": True,
        "strategy_return_pct": 14.0,
        "bh_return_pct": 4.0,
        "alpha_vs_bh_pp": 10.0,
        "strategy_gain_per_month_pct": 7.0,
        "bh_gain_per_month_pct": 2.0,
        "ledger_backed_vector_close_fills": 17,
        "tim_pct": 73.5,
        "entry_family": "ENTRY_STOCH_HHHL",
        "entry_params": {"min_confirming_tfs": 1},
        "exit_family": "EXIT_MTF_ATR_TRAIL",
        "exit_params": {"atr_mult": 1.5},
        "complete_recipe": {
            "schema": "complete-vector-lifecycle-recipe-v1",
            "ENTRY": {
                "family": "ENTRY_STOCH_HHHL",
                "artifact": str(artifact.parent.relative_to(tmp_path)),
                "artifact_result_sha256": artifact_sha,
                "params": {"min_confirming_tfs": 1},
            },
            "EXIT": {
                "family": "EXIT_MTF_ATR_TRAIL",
                "params": {"atr_mult": 1.5},
            },
        },
    }
    receipt = campaign / "keys/ABC_LONG/key_result.json"
    _write(
        receipt,
        {
            "key": "ABC_LONG",
            "status": "UNRESOLVED_USE_BH",
            "champion": candidate,
            "top_candidates": [candidate],
        },
    )
    return receipt, artifact


def test_weekly_only_recipe_passes_new_over_10_contract(tmp_path):
    receipt, artifact = _fixture(tmp_path)
    payload = audit.audit(tmp_path)
    assert payload["qualifying_candidate_count"] == 1
    assert payload["qualifying_symbol_side_count"] == 1
    row = payload["rows"][0]
    assert row["key"] == "ABC_LONG"
    assert row["strategy_return_pct"] == 14.0
    assert row["side_aware_bh_return_pct"] == 4.0
    assert row["real_close_trades"] == 17
    assert row["time_in_market_pct"] == 73.5
    assert row["weekly_activity_gate_is_exact_blocker"] is False
    assert row["new_over_10_close_vector_contract_pass"] is True
    assert row["exact_admission_blockers"] == [
        "FULL_RECIPE_EXACT_REPLAY_NOT_RUN"
    ]
    assert row["receipt_sha256"] == hashlib.sha256(receipt.read_bytes()).hexdigest()
    assert row["direct_entry_artifact_actual_sha256"] == hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()


def test_tampered_direct_entry_artifact_is_rejected(tmp_path):
    _, artifact = _fixture(tmp_path)
    _write(artifact, {**_artifact(), "aggregate": {}})
    payload = audit.audit(tmp_path)
    assert payload["qualifying_candidate_count"] == 0
    assert payload["artifact_contract_rejection_count"] == 1
    assert (
        payload["artifact_contract_rejections"][0]["blocker"]
        == "ENTRY_ARTIFACT_HASH_MISMATCH"
    )


def test_weekly_audit_is_in_compact_sync_pipeline():
    script = Path("tools/pull_vector_capacity_compact_results.sh").read_text()
    assert "audit_capacity_frontier_weekly_activity_only.py" in script
    assert "CAPACITY_FRONTIER_WEEKLY_ACTIVITY_ONLY_AUDIT_CURRENT.json" in script
    assert "CAPACITY_FRONTIER_WEEKLY_ACTIVITY_ONLY_AUDIT_CURRENT.csv" in script

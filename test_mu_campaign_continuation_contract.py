import json
from pathlib import Path

from tools import mu_campaign_continuation_contract as contract
from tools.build_mu_exact_handoff import evaluate_candidate


def _attempt(tmp_path: Path) -> Path:
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "result.json").write_text(json.dumps({"plateau_complete": False}))
    (attempt / "campaign_manifest.json").write_text(json.dumps({
        "source_npz": {"sha256": "npz"},
        "source_code": {"sha256": "code"},
    }))
    rows = []
    for side in ("SHORT", "LONG"):
        for round_no in (0, 1):
            for value, gain in ((5, 10.0), (20, 30.0), (80, 20.0)):
                rows.append({
                    "side": side, "stage": "JOINT_COARSE", "fold": "DISCOVERY",
                    "round": round_no, "gain_pct_capacity": gain + round_no,
                    "return_multiple_vs_bh_or_cash": 5.0 + gain / 100,
                    "trade_sharpe": 1.0, "close_actions": 35,
                    "entry_tf": "5m", "exit_tf": "1h", "reentry_tf": "15m",
                    "lookback": value, "bounce_bps": value,
                    "reclaim_bps": value, "exit_value_delta": value / 10,
                    "reentry_value_delta": value / 20,
                    "exit_price_confirm_bps": value,
                    "reentry_price_confirm_bps": value,
                })
        rows.append({"side": side, "stage": "SIZE_AND_BAR_AUDIT", "fold": "HOLDOUT",
                     "round": 99, "gain_pct_capacity": 999999.0})
    (attempt / "all_combinations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    return attempt


def test_plan_uses_only_terminal_discovery_and_is_deterministic(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    first = contract.build_plan(lane="combined", prior_attempt=attempt, generation=1)
    second = contract.build_plan(lane="combined", prior_attempt=attempt, generation=1)
    assert first == second
    assert first["terminal_rounds"] == {"SHORT": 1, "LONG": 1}
    assert first["terminal_rows"] == 6
    assert first["terminal_best"]["gain_pct_capacity"] < 1000
    assert first["selection_uses_validation_or_holdout"] is False
    assert first["quantity_units"] == list(range(1, 11))
    assert first["source_npz_sha256"] == "npz"
    assert first["source_code_sha256"] == "code"
    assert first["grid_sha256"] == second["grid_sha256"]


def test_completed_grid_hash_produces_explicit_exhaustion(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    first = contract.build_plan(lane="dc_fast", prior_attempt=attempt, generation=2)
    exhausted = contract.build_plan(
        lane="dc_fast", prior_attempt=attempt, generation=2,
        prior_plan_hashes={first["grid_sha256"]},
    )
    assert exhausted["exhausted_domain"] is True
    assert exhausted["exhaustion_reason"] == "DETERMINISTIC_GRID_ALREADY_EVALUATED"


def test_prior_hashes_include_original_completed_attempt_grid(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    attempt = root / "attempts" / "a1"
    attempt.mkdir(parents=True)
    grid = {
        "entry_grid": {"lookback": [5], "bounce_bps": [10], "reclaim_bps": [0]},
        "wt_grid": {"value_delta": [3.0], "price_confirm_bps": [10]},
        "tf_order": list(contract.TFS), "quantity_units": list(range(1, 11)),
    }
    (attempt / "campaign_manifest.json").write_text(json.dumps({
        "entry_grid": grid["entry_grid"], "wt_grid": grid["wt_grid"],
    }))
    assert contract.canonical_sha256(grid) in contract.prior_grid_hashes(root)


def test_exact_handoff_requires_real_v8_receipt(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    spec_dir = artifact / "exact_queue" / "c1"
    spec_dir.mkdir(parents=True)
    spec = {
        "parameters": {"lane": "combined"},
        "expected_metrics": {
            "fold": "HOLDOUT", "eligible_for_exact": True,
            "return_multiple_vs_bh_or_cash": 10.0,
            "close_actions": 30, "max_drawdown_pct": 20.0,
            "trade_sharpe": 1.0, "daily_sharpe": 0.8,
            "multiple_denominator": "POSITIVE_SIDE_AWARE_BH_RETURN",
        },
    }
    (spec_dir / "replay_spec.json").write_text(json.dumps(spec))
    queue = {"candidate_id": "c1", "side": "LONG",
             "spec": "exact_queue/c1/replay_spec.json", "spec_sha256": "s",
             "vector_ledger_sha256": "l"}
    blocked = evaluate_candidate(artifact, queue)
    assert blocked["matrix_proposal_eligible"] is False
    assert "EXACT_RUN_MISSING" in blocked["blocking_reasons"]
    assert "RETURN_MULTIPLE_NOT_GT_10X" in blocked["blocking_reasons"]

    run_dir = artifact / "exact_v8" / "c1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_summary.json").write_text(json.dumps({
        "status": "PASS", "spec_sha256": "s", "vector_ledger_sha256": "l",
        "audit": {"status": "PASS", "schedule": {
            "status": "PASS", "signal_parity": True,
            "capacity": {"status": "PASS"}}, "accounting": {"status": "PASS"}},
    }))
    # The exact receipt alone cannot waive the strict >10x vector gate.
    boundary = evaluate_candidate(artifact, queue)
    assert boundary["matrix_proposal_eligible"] is False
    assert "RETURN_MULTIPLE_NOT_GT_10X" in boundary["blocking_reasons"]
    assert "SUB_10X_FALLBACK_NOT_AUTHORIZED" in boundary["blocking_reasons"]

    # Ten-times is the automatic target, not a discard floor. A documented
    # exhaustive-search plateau authorizes the best lower frontier for V8.
    queue["fallback_authorization"] = {
        "status": "AUTHORIZED_AFTER_PLATEAU",
        "full_scope_complete": True,
        "no_gt_10x_candidate": True,
        "plateau_receipt_sha256": "plateau-sha",
    }
    fallback = evaluate_candidate(artifact, queue)
    assert fallback["matrix_proposal_eligible"] is True
    assert fallback["automatic_gt_10x_escalation"] is False
    assert fallback["sub_10x_fallback_authorized"] is True
    assert fallback["sub_10x_retention_policy"] == "RETAIN_AND_RANK_NEVER_DISCARD"

    queue.pop("fallback_authorization")
    spec["expected_metrics"]["return_multiple_vs_bh_or_cash"] = 10.1
    (spec_dir / "replay_spec.json").write_text(json.dumps(spec))
    ready = evaluate_candidate(artifact, queue)
    assert ready["matrix_proposal_eligible"] is True
    assert ready["automatic_gt_10x_escalation"] is True
    assert ready["live_promotion_eligible"] is False

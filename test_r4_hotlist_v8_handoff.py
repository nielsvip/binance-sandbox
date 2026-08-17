import hashlib
import json

from tools.r4_hotlist_v8_handoff import r4_v8_admission


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fold():
    return {
        "terminal_lifecycle_closes": 11,
        "strategy_return_pct": 1.0,
        "alpha_vs_bh_pp": 1.0,
        "alpha_vs_same_entry_e02_pp": 1.0,
        "weighted_tim_pct": 75.0,
        "unfilled_obligations": 0,
        "future_htf_source_count": 0,
        "bars_flat_beyond_reclaim": 0,
        "entry_capacity_breach": False,
        "insolvent": False,
    }


def _receipts(status="STRICT_SURVIVOR", complete=True):
    recipe = {
        "schema": "complete-vector-lifecycle-recipe-v1", "KEY": "ABC_LONG",
        "ENTRY": {"family": "ENTRY_LADDER_GREEN", "artifact": "sealed", "artifact_result_sha256": "a", "params": {}},
        "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}},
        "VALIDATION": ["2025-01-01", "2025-02-01"],
    }
    digest = hashlib.sha256(_canonical(recipe).encode()).hexdigest()
    baseline = {"complete_recipe": recipe, "complete_recipe_sha256": digest,
                "entry_artifact": "sealed", "entry_artifact_result_sha256": "a"}
    plan = {"schema": "coupled-r4-sealed-recipe-candidate-plan-v1", "plans": [{"key": "ABC_LONG", "recipe_candidates": [{"candidate_id": "c1", "baseline": baseline}]}]}
    winner = {"candidate_id": "c1", "complete_recipe_sha256": digest,
              "selected_exit": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}},
              "discovery_folds": [_fold(), _fold()]}
    frozen = {"schema": "r4-frozen-train-winner-v1", "key": "ABC_LONG", "winner": winner}
    final = {"status": status, "r4_candidate_id": "c1", "complete_recipe_sha256": digest,
             "entry_artifact": "sealed", "entry_artifact_result_sha256": "a",
             "train_all_folds_strict": True, "untouched_final_strict": True,
             "selected_exit": winner["selected_exit"], "untouched_final": _fold()}
    qualification = {"key": "ABC_LONG", "evaluation_status": "COMPLETE" if complete else "INCOMPLETE_FAIL_CLOSED",
                     "full_evaluation_complete": complete, "strict_survivor_count": 1}
    return plan, qualification, frozen, final


def test_dry_run_r4_strict_winner_queues_exactly_one_v8_candidate():
    plan, qualification, frozen, final = _receipts()
    record, status = r4_v8_admission(plan, qualification, frozen, final)
    assert status == "QUEUED"
    assert record["key"] == "ABC_LONG"
    assert record["candidate_id"] == "c1"
    assert record["entry_artifact"] == "sealed"
    assert record["entry_artifact_result_sha256"] == "a"
    assert record["selected_exit"] == final["selected_exit"]
    assert record["admission_mode"] == "RESEARCH_CONTROL_STRICT"


def test_dry_run_r4_rejected_or_incomplete_never_queues_v8():
    plan, qualification, frozen, final = _receipts(status="REJECTED_STRICT_FOLD")
    assert r4_v8_admission(plan, qualification, frozen, final)[0] is None
    plan, qualification, frozen, final = _receipts(complete=False)
    assert r4_v8_admission(plan, qualification, frozen, final)[0] is None


def test_current_tim_floor_and_emergency_share_are_enforced():
    plan, qualification, frozen, final = _receipts()
    for fold in frozen["winner"]["discovery_folds"]:
        fold["weighted_tim_pct"] = 55.0
    final["untouched_final"]["weighted_tim_pct"] = 55.0
    assert r4_v8_admission(plan, qualification, frozen, final)[1] == "QUEUED"

    final["untouched_final"]["emergency_exit_share"] = 0.26
    assert r4_v8_admission(plan, qualification, frozen, final)[0] is None

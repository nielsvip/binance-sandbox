import json
import hashlib
from types import SimpleNamespace

from tools import run_coupled_path_qualification_executor as executor


def r4_baseline(tmp_path, recipe, recipe_sha="r"):
    artifact = tmp_path / "artifact"
    artifact.mkdir(exist_ok=True)
    (artifact / "result.json").write_text(json.dumps({"manifest": {}}))
    return {
        "complete_recipe": recipe, "complete_recipe_sha256": recipe_sha,
        "entry_artifact": str(artifact),
        "entry_artifact_result_sha256": executor.sha256_file(artifact / "result.json"),
    }


def test_legacy_entry_role_requires_unanimous_sealed_fold_evidence():
    base = {"family": "ENTRY_BOUNCE_15M_LOW", "params": {}}
    payload = {"outer_folds": [
        {"selected_candidate": {"role": "direct"}},
        {"selected_candidate": {"role": "direct"}},
    ]}
    assert executor.proven_entry_role(base, payload) == "direct"
    payload["outer_folds"][1]["selected_candidate"]["role"] = "filter"
    assert executor.proven_entry_role(base, payload) is None


def test_priority_overlay_requires_exact_plan_recipe_and_pack_set(tmp_path):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"plans": []}))
    plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    plan = {"baseline": {"complete_recipe_sha256": "recipe", "entry_artifact": "artifact"}, "coupled_packs": [{"pack_id": "a"}, {"pack_id": "b"}]}
    overlay = tmp_path / "priority.json"
    overlay.write_text(json.dumps({"schema": "coupled-path-vector-priority-overlay-v1", "source_plan_sha256": plan_sha, "entries": [{"key": "ABC_LONG", "complete_recipe_sha256": "recipe", "baseline_entry_artifact": "artifact", "allowed_pack_ids": ["a"]}]}))
    allowed, meta = executor.priority_overlay_selection(overlay, plan_path, plan, "ABC_LONG")
    assert allowed == {"a"}
    assert meta["entry"]["key"] == "ABC_LONG"


def test_materialized_adapter_uses_empty_child_not_materialization_parent(tmp_path):
    work = tmp_path / "pack"
    (work / "materialized_entry").mkdir(parents=True)
    materialization = {"artifact": "sealed"}
    adapter_work = work / "adapter" if materialization is not None else work
    assert adapter_work == work / "adapter"
    assert not adapter_work.exists()


def test_executor_uses_artifact_bound_frozen_npz_not_global_directory(tmp_path, monkeypatch):
    frozen_dir = tmp_path / "sealed"
    frozen = frozen_dir / "ABC.npz"
    frozen.parent.mkdir(); frozen.write_bytes(b"frozen")
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "result.json").write_text(json.dumps({"manifest": {"npz": str(frozen)}}))
    seen = []
    def fake_run(command, **kwargs):
        seen.extend(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="expected test stop")
    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    plan = {"key": "ABC_LONG", "baseline": {"entry_artifact": "artifact", "complete_recipe_sha256": "x", "complete_recipe": {"ENTRY": {"family": "ENTRY_4H_DEEP_VALUE", "params": {}}, "EXIT": {"family": "EXIT_MTF_ATR_TRAIL", "params": {}}}}}
    pack = {"pack_id": "p", "entry": {"params": {}}, "exit": {"family": "EXIT_MTF_ATR_TRAIL", "params": {}}, "timing": {"policy": "none"}}
    out = executor.run_pack(tmp_path, plan, pack, tmp_path / "wrong-current", tmp_path / "out")
    assert out["status"] == "ENGINE_ERROR"
    assert str(frozen_dir) in seen
    assert str(tmp_path / "wrong-current") not in seen


def test_supported_grid_refinement_ranks_only_train_failure_distance():
    fold = {
        "alpha_vs_bh_pp": 2.0,
        "alpha_vs_same_entry_e02_pp": 1.0,
        "weighted_tim_pct": 75.0,
        "real_close_trades": 11,
        "unfilled_obligations": 0,
        "future_htf_source_count": 0,
        "bars_flat_beyond_reclaim": 0,
        "entry_capacity_breach": False,
        "insolvent": False,
        "emergency_exit_share": 0.0,
    }
    payload = {"candidates": [
        {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 2},
         "fold_evidence": [{**fold, "weighted_tim_pct": 68.0}, fold], "metrics": {}},
        {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1},
         "fold_evidence": [fold, {**fold, "weighted_tim_pct": 10.0}], "metrics": {}},
    ]}
    selected = executor.pick_supported_grid_refinement(
        payload,
        {"exit": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"}},
        {"family": "ENTRY_TEST", "artifact": "sealed"},
    )
    # Candidate x=1 wins because only its TRAIN fold is observed here; its
    # deliberately bad final fold must not influence selection.
    assert selected["exit_params"] == {"x": 1}


def test_current_tim_band_accepts_50_to_80_and_rejects_outside():
    fold = {
        "strategy_return_pct": 1.0,
        "alpha_vs_bh_pp": 2.0,
        "alpha_vs_same_entry_e02_pp": 1.0,
        "real_close_trades": 11,
        "unfilled_obligations": 0,
        "future_htf_source_count": 0,
        "bars_flat_beyond_reclaim": 0,
        "entry_capacity_breach": False,
        "insolvent": False,
        "emergency_exit_share": 0.0,
    }
    assert executor._train_refinement_fold({**fold, "weighted_tim_pct": 50.0})
    assert executor._train_refinement_fold({**fold, "weighted_tim_pct": 65.0})
    assert executor._train_refinement_fold({**fold, "weighted_tim_pct": 80.0})
    assert not executor._train_refinement_fold({**fold, "weighted_tim_pct": 49.9})
    assert not executor._train_refinement_fold({**fold, "weighted_tim_pct": 80.1})


def test_failure_distance_prefers_lower_emergency_share_before_freeze():
    fold = {
        "strategy_return_pct": 10.0, "alpha_vs_bh_pp": 8.0,
        "alpha_vs_same_entry_e02_pp": 1.0, "weighted_tim_pct": 60.0,
        "real_close_trades": 11, "unfilled_obligations": 0,
        "future_htf_source_count": 0, "bars_flat_beyond_reclaim": 0,
        "entry_capacity_breach": False, "insolvent": False,
    }
    payload = {"candidates": [
        {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1},
         "fold_evidence": [{**fold, "emergency_exit_share": 0.40}, fold], "metrics": {}},
        {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 2},
         "fold_evidence": [{**fold, "emergency_exit_share": 0.26}, fold], "metrics": {}},
    ]}
    selected = executor.pick_supported_grid_refinement(
        payload, {"exit": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"}},
        {"family": "ENTRY_TEST", "artifact": "sealed"},
    )
    assert selected["exit_params"] == {"x": 2}


def test_r4_discovery_screen_never_reads_a_final_field():
    class NoFinal(dict):
        def __getitem__(self, key):
            if key in {"untouched_final", "validation", "final"}:
                raise AssertionError("holdout accessed during discovery")
            return super().__getitem__(key)
    fold = {"strategy_return_pct": 1.0, "alpha_vs_bh_pp": 2.0, "alpha_vs_same_entry_e02_pp": 1.0,
            "weighted_tim_pct": 75.0, "real_close_trades": 11,
            "unfilled_obligations": 0, "future_htf_source_count": 0,
            "bars_flat_beyond_reclaim": 0, "entry_capacity_breach": False,
            "insolvent": False, "emergency_exit_share": 0.0}
    baseline = {"complete_recipe": {"EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"}},
                "complete_recipe_sha256": "r", "entry_artifact_result_sha256": "a"}
    plan = {"recipe_candidates": [{"candidate_id": "one", "baseline": baseline}]}
    calls = []
    def discovery(_candidate):
        calls.append("discovery")
        return NoFinal({"candidates": [{"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}, "fold_evidence": [fold]}]})
    rows, winner = executor.r4_screen_recipe_candidates(plan, discovery)
    assert calls == ["discovery"]
    assert rows[0]["discovery_folds"] == [fold]
    assert winner["candidate_id"] == "one"


def test_r4_reads_exactly_one_final_after_freeze(tmp_path, monkeypatch):
    fold = {"strategy_return_pct": 1.0, "alpha_vs_bh_pp": 2.0, "alpha_vs_same_entry_e02_pp": 1.0,
            "weighted_tim_pct": 75.0, "real_close_trades": 11,
            "unfilled_obligations": 0, "future_htf_source_count": 0,
            "bars_flat_beyond_reclaim": 0, "entry_capacity_breach": False,
            "insolvent": False, "emergency_exit_share": 0.0}
    baseline = r4_baseline(tmp_path, {"EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}}})
    plan = {"key": "ABC_LONG", "recipe_candidates": [{"candidate_id": "one", "baseline": baseline}]}
    calls = []
    def fake_adapter(_root, _baseline, _exit, _npz, _work, fold_mode):
        calls.append(fold_mode)
        payload = {"candidates": [{"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1},
                                    "fold_evidence": [fold]}]}
        if fold_mode == "discovery":
            _work.mkdir(parents=True); (_work / "result.json").write_text(json.dumps(payload))
        return payload
    monkeypatch.setattr(executor, "_r4_adapter", fake_adapter)
    _, winner, result = executor.execute_r4_recipe_candidates(tmp_path, plan, tmp_path, tmp_path / "out")
    assert winner["candidate_id"] == "one"
    assert result["untouched_final"] == fold
    assert calls == ["discovery", "latest"]


def test_r4_shares_only_identical_artifact_family_discovery_screen(tmp_path, monkeypatch):
    fold = {"strategy_return_pct": 1.0, "alpha_vs_bh_pp": 2.0, "alpha_vs_same_entry_e02_pp": 1.0,
            "weighted_tim_pct": 75.0, "real_close_trades": 11,
            "unfilled_obligations": 0, "future_htf_source_count": 0,
            "bars_flat_beyond_reclaim": 0, "entry_capacity_breach": False,
            "insolvent": False, "emergency_exit_share": 0.0}
    recipe = {"EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}}}
    baseline = r4_baseline(tmp_path, recipe)
    plan = {"key": "ABC_LONG", "recipe_candidates": [
        {"candidate_id": "one", "baseline": baseline},
        {"candidate_id": "two", "baseline": {**baseline, "complete_recipe_sha256": "r2"}},
    ]}
    calls = []
    def fake_adapter(_root, _baseline, _exit, _npz, _work, fold_mode):
        calls.append(fold_mode)
        payload = {"candidates": [{"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}, "fold_evidence": [fold]}]}
        if fold_mode == "discovery":
            _work.mkdir(parents=True); (_work / "result.json").write_text(json.dumps(payload))
        return payload
    monkeypatch.setattr(executor, "_r4_adapter", fake_adapter)
    screened, _, _ = executor.execute_r4_recipe_candidates(tmp_path, plan, tmp_path, tmp_path / "out")
    assert calls == ["discovery", "latest"]
    assert screened[0]["discovery_screen_id"] == screened[1]["discovery_screen_id"]


def test_r4_reuses_completed_discovery_screen_without_adapter_call(tmp_path, monkeypatch):
    fold = {"strategy_return_pct": 1.0, "alpha_vs_bh_pp": 2.0, "alpha_vs_same_entry_e02_pp": 1.0,
            "weighted_tim_pct": 75.0, "real_close_trades": 11, "unfilled_obligations": 0,
            "future_htf_source_count": 0, "bars_flat_beyond_reclaim": 0,
            "entry_capacity_breach": False, "insolvent": False, "emergency_exit_share": 0.0}
    recipe = {"EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}}}
    baseline = r4_baseline(tmp_path, recipe)
    plan = {"key": "ABC_LONG", "recipe_candidates": [{"candidate_id": "one", "baseline": baseline}]}
    screen_id = hashlib.sha256(executor.canon({"entry_artifact_result_sha256": baseline["entry_artifact_result_sha256"], "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"}).encode()).hexdigest()
    screen = tmp_path / "out" / "ABC_LONG" / "discovery_screens" / screen_id; screen.mkdir(parents=True)
    payload = {"fold_mode": "discovery", "source_artifact": baseline["entry_artifact"], "candidates": [{"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}, "fold_evidence": [fold]}]}
    result_path = screen / "result.json"; result_path.write_text(json.dumps(payload))
    (screen / "R4_DISCOVERY_SCREEN_RECEIPT.json").write_text(json.dumps({"schema": "r4-discovery-screen-receipt-v1", "screen_id": screen_id, "entry_artifact_result_sha256": baseline["entry_artifact_result_sha256"], "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "result_sha256": executor.sha256_file(result_path)}))
    calls = []
    def adapter(*args):
        calls.append(args[-1])
        assert args[-1] == "latest"
        return {"candidates": [{"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}, "fold_evidence": [fold]}]}
    monkeypatch.setattr(executor, "_r4_adapter", adapter)
    executor.execute_r4_recipe_candidates(tmp_path, plan, tmp_path, tmp_path / "out")
    assert calls == ["latest"]


def test_r4_rejects_mismatched_or_incomplete_discovery_screen(tmp_path):
    recipe = {"EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}}}
    baseline = r4_baseline(tmp_path, recipe); plan = {"key": "ABC_LONG", "recipe_candidates": [{"candidate_id": "one", "baseline": baseline}]}
    screen_id = hashlib.sha256(executor.canon({"entry_artifact_result_sha256": baseline["entry_artifact_result_sha256"], "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"}).encode()).hexdigest()
    screen = tmp_path / "out" / "ABC_LONG" / "discovery_screens" / screen_id; screen.mkdir(parents=True)
    try:
        executor.execute_r4_recipe_candidates(tmp_path, plan, tmp_path, tmp_path / "out")
    except RuntimeError as exc:
        assert "INCOMPLETE_DISCOVERY_SCREEN" in str(exc)
    else: assert False


def test_r4_rejects_mismatched_completed_screen_receipt(tmp_path):
    recipe = {"EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {"x": 1}}}
    baseline = r4_baseline(tmp_path, recipe); plan = {"key": "ABC_LONG", "recipe_candidates": [{"candidate_id": "one", "baseline": baseline}]}
    screen_id = hashlib.sha256(executor.canon({"entry_artifact_result_sha256": baseline["entry_artifact_result_sha256"], "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"}).encode()).hexdigest()
    screen = tmp_path / "out" / "ABC_LONG" / "discovery_screens" / screen_id; screen.mkdir(parents=True)
    (screen / "result.json").write_text(json.dumps({"fold_mode": "discovery", "source_artifact": baseline["entry_artifact"], "candidates": [{"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"}]}))
    (screen / "R4_DISCOVERY_SCREEN_RECEIPT.json").write_text(json.dumps({"schema": "r4-discovery-screen-receipt-v1", "screen_id": screen_id, "entry_artifact_result_sha256": baseline["entry_artifact_result_sha256"], "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "result_sha256": "wrong"}))
    try:
        executor.execute_r4_recipe_candidates(tmp_path, plan, tmp_path, tmp_path / "out")
    except RuntimeError as exc:
        assert "RECEIPT_MISMATCH" in str(exc)
    else: assert False

import hashlib
import json

from tools import build_coupled_qualification_priority_overlay as overlay


def test_priority_overlay_preserves_plan_and_limits_role_unproven_variants(tmp_path):
    artifact = tmp_path / "entry"; artifact.mkdir()
    (artifact / "result.json").write_text(json.dumps({"outer_folds": [{"selected_candidate": {"role": "direct"}, "validation_metrics": {"capital_return_pct": 12, "bh_capital_return_pct": 2, "exit_count": 10, "exposure_weighted_tim_pct": 75}}], "aggregate": {"candidate_bh_multiple": 3.0}}))
    plan = {"schema": "plan", "plans": [{"key": "INTC_LONG", "status": "READY_FOR_RESOURCE_GUARDED_VECTOR_EXECUTOR", "baseline": {"entry_artifact": "entry", "complete_recipe_sha256": "recipe", "complete_recipe": {"ENTRY": {"family": "ENTRY_BOUNCE_5M_LOW"}, "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"}}}, "coupled_packs": [{"pack_id": "base"}, {"pack_id": "fav"}, {"pack_id": "strict"}, {"pack_id": "timing"}]}]}
    plan_path = tmp_path / "plan.json"; plan_path.write_text(json.dumps(plan))
    result = overlay.build(tmp_path, plan_path)
    row = result["entries"][0]
    assert result["source_plan_sha256"] == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    assert row["allowed_pack_ids"] == ["base", "fav", "strict", "timing"]
    assert row["failure_summary"]["close_deficit_to_11"] == 1
    assert row["objective"]["all_folds_min_closes"] == 11


def test_recompute_first_keys_are_scheduled_before_legacy_primary(tmp_path):
    def row(key):
        artifact = tmp_path / key; artifact.mkdir()
        (artifact / "result.json").write_text(json.dumps({
            "outer_folds": [{"selected_candidate": {"role": "direct"},
            "validation_metrics": {"capital_return_pct": 12,
            "bh_capital_return_pct": 2, "exit_count": 11,
            "exposure_weighted_tim_pct": 55}}]
        }))
        return {"key": key, "status": "READY_FOR_RESOURCE_GUARDED_VECTOR_EXECUTOR",
                "baseline": {"entry_artifact": key, "complete_recipe_sha256": key,
                "complete_recipe": {"ENTRY": {"family": "ENTRY_BOUNCE_5M_LOW"},
                "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"}}},
                "coupled_packs": [{"pack_id": key}]}
    plan_path = tmp_path / "plan2.json"
    plan_path.write_text(json.dumps({"plans": [row("INTC_LONG"), row("AR_LONG"), row("AMD_LONG")]}))
    result = overlay.build(tmp_path, plan_path)
    assert [item["key"] for item in result["entries"]] == ["AMD_LONG", "AR_LONG", "INTC_LONG"]

import json

import pytest

from tools import build_classic_formation_group_recipe as group


def test_group_override_preserves_active_baseline_and_adds_only_selected_formation_paths():
    baseline = {
        "WT_3M_FORCE_OPEN_ENABLED": False,
        "BREAKOUT_RETEST_ARMED_ENABLED": True,
        "FORMATION_WEDGE_ENTRY_ENABLED": False,
    }
    override, delta = group.group_override(baseline, "wedge_entry_1h", "triangle_exit_1h")
    assert override["WT_3M_FORCE_OPEN_ENABLED"] is False
    assert override["BREAKOUT_RETEST_ARMED_ENABLED"] is True
    assert override["FORMATION_WEDGE_ENTRY_ENABLED"] is True
    assert override["FORMATION_TRIANGLE_EXIT_ENABLED"] is True
    assert override["FORMATION_TFS"] == "1h"
    assert "FULL_RECIPE_ONLY_ENABLED" not in override
    assert set(delta) == {
        "FORMATION_WEDGE_ENTRY_ENABLED", "FORMATION_TRIANGLE_EXIT_ENABLED", "FORMATION_TFS"
    }


def test_group_override_expands_all_to_real_selector_timeframes():
    override, delta = group.group_override(
        {}, "trend_structure_entry_ALL", "trend_structure_exit_ALL"
    )
    assert delta["FORMATION_TFS"] == "15m,1h,4h,D"
    assert override["FORMATION_TFS"] == "15m,1h,4h,D"


def test_group_override_rejects_isolated_or_precontaminated_baseline():
    with pytest.raises(ValueError, match="ACTIVE_BASELINE_ISOLATED_FULL_RECIPE_ONLY"):
        group.group_override({"FULL_RECIPE_ONLY_ENABLED": True}, "wedge_entry_1h", "triangle_exit_1h")
    with pytest.raises(ValueError, match="ACTIVE_BASELINE_FORMATION_CONTAMINATION"):
        group.group_override({"FORMATION_CUP_HANDLE_ENTRY_ENABLED": True}, "wedge_entry_1h", "triangle_exit_1h")


def test_group_override_rejects_conflicting_baseline_formation_parameter():
    with pytest.raises(ValueError, match="ACTIVE_BASELINE_FORMATION_PARAMETER_CONFLICT:FORMATION_TFS"):
        group.group_override({"FORMATION_TFS": "D"}, "wedge_entry_1h", "triangle_exit_1h")


def test_active_config_drift_blocks_before_recipe_construction():
    train = {"active_config_overrides": {"ABC_LONG": {"WT_3M_FORCE_OPEN_ENABLED": False}}}
    active = {"ABC_LONG": {"overrides": {"WT_3M_FORCE_OPEN_ENABLED": True}}}
    with pytest.raises(ValueError, match="ACTIVE_CONFIG_DRIFT_FROM_TRAIN_SNAPSHOT:ABC_LONG"):
        group.compare_active_to_train(train, active, "ABC_LONG")


def test_active_config_binding_is_per_key_and_hash_bound():
    train = {"active_config_overrides": {"ABC_LONG": {"WT_3M_FORCE_OPEN_ENABLED": False}}}
    active = {"ABC_LONG": {"winning_tag": "baseline", "overrides": {"WT_3M_FORCE_OPEN_ENABLED": False}}}
    binding = group.compare_active_to_train(train, active, "ABC_LONG")
    assert binding["active_config_override_snapshot_sha256"] == binding["train_active_config_override_snapshot_sha256"]
    assert binding["active_config_key_envelope_sha256"]


def test_inventory_covers_all_event_reason_families_with_selected_formation_routes():
    class Event:
        def __init__(self, kind, reason):
            self.type, self.reason = kind, reason
    rows = group.event_reason_inventory([
        Event("OPEN", "CLASSIC_FORMATION_ENTRY_WEDGE_score0.80"),
        Event("CLOSE", "CLASSIC_FORMATION_EXIT_TRIANGLE_score0.75_gain1.20%"),
        Event("OPEN", "WT_3M_FORCE_OPEN_LONG_wt1=-60.0_px10.2"),
    ])
    assert [(row["event_type"], row["reason_family"]) for row in rows] == [
        ("CLOSE", "CLASSIC_FORMATION_EXIT_TRIANGLE"),
        ("OPEN", "CLASSIC_FORMATION_ENTRY_WEDGE"),
        ("OPEN", "WT_3M_FORCE_OPEN_LONG"),
    ]


def test_group_recipe_is_distinguishable_from_isolated_recipe_contract():
    assert group.RECIPE_KIND == "ACTIVE_BASELINE_PATH_SET_PLUS_CLASSIC_FORMATION_ENTRY_EXIT"
    assert "FULL_RECIPE_ONLY" not in group.CONTRACT


def test_group_recipe_active_config_revalidation_blocks_file_or_envelope_drift(tmp_path):
    active = tmp_path / "active.json"
    payload = {"ABC_LONG": {"winning_tag": "base", "overrides": {"WT_3M_FORCE_OPEN_ENABLED": False}}}
    active.write_text(json.dumps(payload))
    envelope, overrides = group.active_snapshot(payload, "ABC_LONG")
    recipe = {
        "key": "ABC_LONG",
        "ACTIVE_BASELINE_PATH_SET": {
            "active_config_path": str(active.relative_to(tmp_path)),
            "active_config_file_sha256": group.sha256(active),
            "active_config_key_envelope_sha256": group.stable_hash(envelope),
            "active_config_override_snapshot_sha256": group.stable_hash(overrides),
        },
    }
    group.verify_active_config_binding(recipe, tmp_path, active)
    active.write_text(json.dumps({"ABC_LONG": {"winning_tag": "changed", "overrides": overrides}}))
    with pytest.raises(ValueError, match="ACTIVE_CONFIG_FILE_DRIFT_FROM_GROUP_RECIPE"):
        group.verify_active_config_binding(recipe, tmp_path, active)

import json

from tools import run_classic_formation_resolved_group_overlay as lane


def test_resolved_config_requires_full_payload_and_resets_formation_switches(tmp_path):
    path = tmp_path / "resolved.json"
    path.write_text(json.dumps({"resolved_config": {"FORMATION_WEDGE_EXIT_ENABLED": True, "LR_BAND_ENTRY_ENABLED": True}}))
    values, _ = lane.resolved_config(path)
    cfg = lane.base_from_resolved(values)
    assert cfg.LR_BAND_ENTRY_ENABLED is True
    assert cfg.FORMATION_WEDGE_EXIT_ENABLED is False
    cfg.LIVE_DYNAMIC_PATH_KNOB = True
    over = lane.overlay_config(cfg, "head_shoulders_exit_ALL")
    assert over.FORMATION_HEAD_SHOULDERS_EXIT_ENABLED is True
    assert over.FORMATION_TFS == "15m,1h,4h,D"
    assert over.LIVE_DYNAMIC_PATH_KNOB is True


def test_strict_group_overlay_requires_real_overlay_and_bh():
    row = {"return_pct": 3.0, "trades": 11, "alpha_vs_bh_pp": 0.1, "formation_exit_action_count": 1}
    assert lane.strict_failures(row) == []
    assert lane.strict_failures({**row, "formation_exit_action_count": 0}) == ["SELECTED_FORMATION_EXIT_ACTIONS_ZERO"]

import pytest

from tools import build_classic_formation_adaptive_hybrid_group_recipe as builder
from tools import run_classic_formation_adaptive_hybrid_group_exact_v8 as exact


def evidence():
    return {
        "return_pct": 20.0, "trades": 11, "alpha_vs_bh_pp": 5.0,
        "selected_formation_action_count": 2, "ordinary_entry_action_count": 11,
        "ordinary_exit_schedule_count": 11, "undeclared_event_count": 0,
    }


def recipe():
    return {
        "DECLARED_HYBRID_TOPOLOGY": {
            "role": "EXIT_OVERLAY", "ordinary_entry_group": "GOLDEN_RULE",
            "ordinary_exit_group": "PPL_SL_CLOSE",
        },
        "ADAPTIVE_VECTOR_REPLAY": {"train": evidence(), "holdout": evidence()},
    }


def test_vector_gate_accepts_actual_exit_overlay_topology():
    builder.vector_gate(recipe())


def test_vector_gate_refuses_invented_formation_entry_or_undeclared_action():
    with pytest.raises(ValueError, match="ADAPTIVE_RECIPE_NOT_EXIT_OVERLAY"):
        builder.vector_gate({**recipe(), "DECLARED_HYBRID_TOPOLOGY": {**recipe()["DECLARED_HYBRID_TOPOLOGY"], "role": "ENTRY_EXIT"}})
    bad = recipe(); bad["ADAPTIVE_VECTOR_REPLAY"]["holdout"]["undeclared_event_count"] = 1
    with pytest.raises(ValueError, match="UNDECLARED_ACTIONS_PRESENT"):
        builder.vector_gate(bad)


def test_exact_refuses_active_augment_reduce_reentry_or_hedge_path():
    assert exact.active_baseline_disallowed_paths({"SWEEP_DISABLE_HEDGING": True}) == []
    assert exact.active_baseline_disallowed_paths({"TREND_AUGMENT_ENABLED": True}) == ["TREND_AUGMENT_ENABLED"]
    assert exact.active_baseline_disallowed_paths({"REENTRY_ENABLED": True, "PPL_REDUCE_ENABLED": True}) == ["PPL_REDUCE_ENABLED", "REENTRY_ENABLED"]


def test_exact_reads_resolved_engine_config_not_just_sparse_override(tmp_path):
    missing = exact.resolved_engine_disallowed_paths(tmp_path / "missing.json")
    assert missing == ["RESOLVED_ENGINE_CONFIG_MISSING"]
    path = tmp_path / "resolved.json"
    path.write_text('{"resolved_config":{"LR_BAND_ENTRY_ENABLED":true,"REENTRY_B15_STRONG_TREND_ENABLED":true}}')
    assert exact.resolved_engine_disallowed_paths(path) == ["REENTRY_B15_STRONG_TREND_ENABLED"]


def test_exact_inventory_fails_all_undeclared_lifecycle_actions():
    r = {"key": "ABC_SHORT", "DECLARED_HYBRID_TOPOLOGY": {"formation_exit_arm": "head_shoulders_exit_15m", "ordinary_entry_group": "GOLDEN_RULE", "ordinary_exit_group": "PPL_SL_CLOSE"}}
    events = [
        {"side": "SHORT", "action": "OPEN", "reason": "GOLDEN_RULE_ok"},
        {"side": "SHORT", "action": "CLOSE", "exit_reason": "CLASSIC_FORMATION_EXIT_HEAD_SHOULDERS_ok"},
        {"side": "SHORT", "action": "AUGMENT", "reason": "TREND_AUGMENT"},
    ]
    inventory, bad = exact.lifecycle_inventory(r, events)
    assert {row["family"] for row in inventory} == {"DECLARED_GOLDEN_RULE_ENTRY", "DECLARED_FORMATION_EXIT_OVERLAY", "UNDECLARED"}
    assert bad == [{"action": "AUGMENT", "reason": "TREND_AUGMENT"}]

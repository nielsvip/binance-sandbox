from tools.materialize_classic_formation_combo_candidates import (
    event_path_audit,
    require_complete_recipe_ledger,
    route,
    strictly_later_reentry,
)
import pytest


def test_routes_and_reentry_are_explicit_and_strictly_later():
    assert route("trend_structure_entry_ALL", "ENTRY", 0.65) == {
        "family": "ENTRY_CLASSIC_FORMATION_TREND_STRUCTURE",
        "params": {
            "timeframe": "ALL",
            "min_score": 0.65,
            "position_size_mult": 1.0,
        },
        "status": "SHARED_CAUSAL_VECTOR_MEASURED",
    }
    evidence = strictly_later_reentry(
        [
            {"ts": 10, "type": "OPEN"},
            {"ts": 20, "type": "CLOSE"},
            {"ts": 20, "type": "OPEN"},
            {"ts": 30, "type": "OPEN"},
            {"ts": 40, "type": "CLOSE"},
        ]
    )
    assert evidence["reentry_pairs"] == [{"exit_ts": 20, "reentry_ts": 30}]
    assert evidence["strictly_later_reentry_proven"] is True
    assert evidence["terminal_right_censored"] is True


def _formation_routes():
    return (
        route("trend_structure_entry_ALL", "ENTRY", 0.65),
        route("trend_structure_exit_ALL", "EXIT", 0.65),
    )


def test_event_path_audit_rejects_mixed_baseline_open_exit_augment_and_reduce():
    entry, exit_ = _formation_routes()
    audit = event_path_audit([
        {"ts": 1, "type": "OPEN", "reason": "CLASSIC_FORMATION_ENTRY_TREND_STRUCTURE_score0.80"},
        {"ts": 2, "type": "AUGMENT", "reason": "REENTRY_TREND_g3.1_blk_B11"},
        {"ts": 3, "type": "REDUCE", "reason": "PPL_TP_gain1.6_62pct"},
        {"ts": 4, "type": "CLOSE", "reason": "HYBRID_STRUCT_EXIT_4h"},
        {"ts": 5, "type": "OPEN", "reason": "GOLDEN_RULE_SHORT_mult3.0_INTERVENTION"},
        {"ts": 6, "type": "CLOSE", "reason": "CLASSIC_FORMATION_EXIT_TREND_STRUCTURE_score0.70"},
    ], entry, exit_)
    assert audit["pass"] is False
    assert audit["declared_action_counts"] == {"ENTRY": 1, "EXIT": 1}
    assert audit["undeclared_paths"] == [
        {"type": "AUGMENT", "reason_group": "REENTRY", "count": 1},
        {"type": "CLOSE", "reason_group": "HYBRID_STRUCT_EXIT", "count": 1},
        {"type": "OPEN", "reason_group": "GOLDEN_RULE", "count": 1},
        {"type": "REDUCE", "reason_group": "PPL_TP", "count": 1},
    ]
    with pytest.raises(ValueError, match="MATERIALIZATION_BLOCKED_UNDECLARED_VECTOR_EVENT_PATHS"):
        require_complete_recipe_ledger({"holdout": audit}, "AXON_SHORT")


def test_event_path_audit_accepts_only_the_exact_selected_formation_paths():
    entry, exit_ = _formation_routes()
    audit = event_path_audit([
        {"ts": 1, "type": "OPEN", "reason": "CLASSIC_FORMATION_ENTRY_TREND_STRUCTURE_score0.80"},
        {"ts": 2, "type": "CLOSE", "reason": "CLASSIC_FORMATION_EXIT_TREND_STRUCTURE_score0.70"},
    ], entry, exit_)
    assert audit["pass"] is True
    assert audit["undeclared_paths"] == []


def test_materializer_preserves_but_blocks_a_mixed_path_ledger():
    source = open("tools/materialize_classic_formation_combo_candidates.py").read()
    assert "BLOCKED_UNDECLARED_VECTOR_EVENT_PATHS" in source
    assert "ACTIVE_BASELINE_PATH_SET_REQUIRED_FOR_MIXED_VECTOR_LEDGER" in source
    assert '"exact_v8_candidate_authorized": False' in source
    assert '"active_vector_config_sha256"' in source

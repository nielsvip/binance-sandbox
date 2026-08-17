from tools.hotlist_v8_full_recipe_routes import (
    CLASSIC_FORMATION_ENTRY_FAMILIES,
    CLASSIC_FORMATION_EXIT_FAMILIES,
    full_recipe_blockers,
    full_recipe_route_audit,
    is_dispatchable_row,
)
from tools.run_hotlist_v8_full_recipe import entry_overrides


def test_ladder_bottom_a_remains_a_shared_pair():
    audit = full_recipe_route_audit(
        "ENTRY_LADDER_GREEN", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"
    )
    assert audit["shared_route"] is True
    assert audit["blockers"] == []
    assert "_ordinary_ladder_target" in audit["entry_contract"]["shared_live_decision_site"]


def test_bounce_5m_is_dispatchable_only_with_exact_direct_route_params():
    blockers = full_recipe_blockers(
        "ENTRY_BOUNCE_5M_LOW", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        {
            "timeframe": "5m", "distance": .025,
            "recovery_only": False, "confirmation": "none",
        },
    )
    assert blockers == []
    assert any("INVALID" in item or "MISSING" in item for item in full_recipe_blockers(
        "ENTRY_BOUNCE_5M_LOW", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"
    ))


def test_bounce_15m_valid_exact_params_dispatch_but_missing_params_fail_closed():
    row = (
        {
            "entry_family": "ENTRY_BOUNCE_15M_LOW",
            "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
            "entry_params": {
                "timeframe": "15m", "distance": .015,
                "recovery_only": False, "confirmation": "stoch15",
            },
            "beats_bh_and_over_10_real_trades": True,
        }
    )
    assert is_dispatchable_row(row)
    row.pop("entry_params")
    assert not is_dispatchable_row(row)


def test_bb_recovery_is_a_shared_completed_parent_route_when_frozen():
    params = {"timeframe": "1h", "recovery_bars": 4, "min_excursion_atr": .5}
    audit = full_recipe_route_audit(
        "ENTRY_BB_RECOVERY", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", params
    )
    assert audit["shared_route"] is True
    assert audit["entry_contract"]["shared_contract"] == (
        "bb_recovery_contract.py:BBRecoveryEntry"
    )
    assert audit["entry_contract"]["shared_v8_reason_prefix"] == "BB_RECOVERY_DIRECT"


def test_research_gate_is_required_even_for_shared_pair():
    assert not is_dispatchable_row(
        {
            "entry_family": "ENTRY_LADDER_GREEN",
            "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
            "beats_bh_and_over_10_real_trades": False,
        }
    )


def test_dc_break_reconstruction_is_explicitly_not_dispatchable():
    row = {
        "entry_family": "ENTRY_DC_BREAK_ENTRY_ENABLED",
        "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        "entry_params": {
            "timeframe": "15m",
            "buffer_fraction": .0005,
            "require_1h_expansion": False,
            "confirmation": "not-exhausted",
        },
        "beats_bh_and_over_10_real_trades": True,
    }
    audit = full_recipe_route_audit(
        row["entry_family"], row["exit_family"], row["entry_params"]
    )
    assert audit["shared_route"] is False
    assert audit["entry_contract"]["route_status"] == (
        "RESEARCH_RECONSTRUCTION_NOT_LIVE_PROMOTABLE"
    )
    assert {
        "DC_BREAK_REGISTRY_SWITCH_DISCONNECTED",
        "DC_BREAK_VECTOR_MIXES_DAYTRADE_AND_SWING_SEMANTICS",
    }.issubset(audit["blockers"])
    assert not is_dispatchable_row(row)


def test_all_seven_classic_formation_entries_are_exact_routes_only_when_frozen():
    base = {
        "timeframe": "15m",
        "min_score": .65,
        "position_size_mult": 1.0,
    }
    for family in CLASSIC_FORMATION_ENTRY_FAMILIES:
        audit = full_recipe_route_audit(
            family, "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", base
        )
        assert audit["shared_route"], (family, audit["blockers"])
        assert audit["entry_contract"]["shared_v8_reason_prefix"] == (
            "CLASSIC_FORMATION_ENTRY_"
        )
        assert full_recipe_blockers(
            family, "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", {"timeframe": "15m"}
        )


def test_all_seven_classic_formation_exits_are_exact_routes_only_when_frozen():
    base = {"timeframe": "ALL", "min_score": .65, "exit_min_gain_pct": 0.0}
    for family in CLASSIC_FORMATION_EXIT_FAMILIES:
        audit = full_recipe_route_audit("ENTRY_LADDER_GREEN", family, {}, base)
        assert audit["shared_route"], (family, audit["blockers"])
        assert audit["exit_contract"]["shared_v8_reason_prefix"] == (
            "CLASSIC_FORMATION_EXIT_"
        )
        assert full_recipe_blockers("ENTRY_LADDER_GREEN", family, {}, {"timeframe": "ALL"})


def test_classic_formation_override_enables_only_selected_family_and_expands_all():
    family = "ENTRY_CLASSIC_FORMATION_WEDGE"
    recipe = {
        "ENTRY": {
            "family": family,
            "params": {
                "timeframe": "ALL", "min_score": .7,
                "position_size_mult": 1.5,
            },
        },
        "EXIT": {
            "family": "EXIT_CLASSIC_FORMATION_TRIANGLE",
            "params": {
                "timeframe": "ALL", "min_score": .7,
                "exit_min_gain_pct": 1.0,
            },
        },
    }
    overrides = entry_overrides(family, None, recipe)
    assert overrides["FORMATION_WEDGE_ENTRY_ENABLED"] is True
    assert overrides["FORMATION_TRIANGLE_EXIT_ENABLED"] is True
    assert overrides["FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED"] is False
    assert overrides["FORMATION_TFS"] == "15m,1h,4h,D"
    assert overrides["FORMATION_MIN_SCORE"] == .7
    assert overrides["FORMATION_POSITION_SIZE_MULT"] == 1.5
    assert overrides["FORMATION_EXIT_MIN_GAIN_PCT"] == 1.0
    assert overrides["LS_RATIO_ENFORCE_TRADIER"] is False


def test_formation_entry_exit_selector_conflict_fails_closed():
    blockers = full_recipe_blockers(
        "ENTRY_CLASSIC_FORMATION_WEDGE",
        "EXIT_CLASSIC_FORMATION_TRIANGLE",
        {"timeframe": "15m", "min_score": .65, "position_size_mult": 1.0},
        {"timeframe": "1h", "min_score": .65, "exit_min_gain_pct": 0.0},
    )
    assert "CLASSIC_FORMATION_ENTRY_EXIT_SHARED_SELECTOR_CONFLICT" in blockers

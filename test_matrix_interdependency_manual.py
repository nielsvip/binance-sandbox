import re

from tools import build_matrix_interdependency_manual as manual


def _rows():
    payload = manual.build_payload()
    return payload, {row["param"]: row for row in payload["paths"]}


def test_inventory_is_complete_and_descriptions_are_nonempty():
    payload, rows = _rows()
    assert payload["path_count"] == 894
    assert len(rows) == 894
    assert all(row["description"].strip() for row in rows.values())
    assert all(
        "direction is not safely inferable" not in row["description"]
        for row in rows.values()
    )
    assert payload["audit_summary"]["verified_descriptions"] == 12
    assert payload["audit_summary"]["generated_conservative_descriptions"] == 882


def test_activation_edges_do_not_use_truncated_family_guesses():
    payload, rows = _rows()
    assert rows["WT_DC_ENTRY_THRESHOLD"]["activation_dependencies"] == []
    assert rows["WT_DC_ENTRY_THRESHOLD"]["master_resolution"] == (
        "STANDALONE_OR_UNRESOLVED"
    )
    assert rows["WT_DC_ENTRY_BAR_MATURITY_BLOCK"][
        "activation_dependencies"
    ] == ["WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED"]
    assert rows["MTF_DC_REJECT_EXIT_ENABLED"]["activation_dependencies"] == [
        "MTF_EXIT_USE_COMPOUND"
    ]
    for name in (
        "STRUCTURAL_RANGE_SHIFT_TF",
        "STRUCTURAL_RANGE_SHIFT_K_HIGH",
        "STRUCTURAL_RANGE_SHIFT_K_LOW",
        "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS",
    ):
        assert rows[name]["activation_dependencies"] == [
            "STRUCTURAL_RANGE_SHIFT_EXIT"
        ]

    explicit = manual.VERIFIED_ACTIVATION_DEPENDENCIES
    for row in rows.values():
        for master in row["activation_dependencies"]:
            if master in explicit.get(row["param"], []):
                continue
            stem = manual._master_stem(master)
            assert row["param"] == stem or row["param"].startswith(stem + "_")
    assert payload["audit_summary"]["activation_edges"] == 266


def test_cross_cutting_guards_precede_actions_and_invalidate_bundle():
    payload, rows = _rows()
    assert manual.LAYERS["CROSS_CUTTING_GUARD"] == 15
    assert "OTHER_GUARD" not in manual.LAYERS
    assert rows["DELTA_COOLDOWN_BARS"]["precedence_layer"] == (
        "CROSS_CUTTING_GUARD"
    )
    assert manual.RETEST_RULES["REENTRY_CYCLE"]["invalidates"][:2] == [
        "EXIT_SOURCE",
        "EXIT_FILTER",
    ]
    assert set(manual.RETEST_RULES["CAPITAL_METRIC_REPLAY"]["invalidates"]) >= {
        "ENTRY_SOURCE",
        "EXIT_SOURCE",
        "REENTRY",
        "SIZING",
    }
    assert payload["audit_summary"]["registry_bool_master_demotions"] == 35


def test_repaired_grids_are_executable_and_runtime_control_is_not_alpha():
    payload, rows = _rows()
    assert payload["audit_summary"]["grid_repair_required"] == 0
    assert rows["WT_DC_ENTRY_THRESHOLD"]["grid_repair_reasons"] == []
    assert rows["REENTRY_RALLY_HTF_MIN"]["test_values"] == [0, 1, 2, 3]
    assert all(
        type(value) is int
        for value in rows["REENTRY_RALLY_HTF_MIN"]["test_values"]
    )
    assert "MTF_EXIT_MIN_OPEN_TS" not in rows


def test_deployment_and_binding_limitations_are_explicit():
    payload, rows = _rows()
    summary = payload["audit_summary"]
    assert summary["no_live_decision_read"] == 253
    assert summary["global_only_live"] == 596
    assert summary["per_symbol_live"] == 45
    assert summary["vector_screenable"] == 160
    assert summary["exact_only_no_vector"] == 734
    assert summary["token_only_live_references"] == 88
    assert rows["GOLDEN_RULE_REQUIRE_ACTIVATION"]["deployment_scope"] == (
        "NONDEPLOYABLE_NO_LIVE_READ"
    )
    assert rows["ATR_TRAIL_2X_EXIT_ENABLED"]["differential_readiness"] == (
        "BINDING_PROBE_REQUIRED"
    )
    assert re.search(
        r"exact differential fingerprint",
        rows["ATR_TRAIL_2X_EXIT_ENABLED"]["description"],
    )

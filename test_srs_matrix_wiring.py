from tools import param_matrix_daemon as daemon
from tools import exact_wiring_gate


def test_each_srs_subsetting_enables_only_its_legacy_family_master() -> None:
    for name, value in (
        ("STRUCTURAL_RANGE_SHIFT_TF", "bb_1h"),
        ("STRUCTURAL_RANGE_SHIFT_K_HIGH", 85.0),
        ("STRUCTURAL_RANGE_SHIFT_K_LOW", 15.0),
        ("STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS", 100.0),
    ):
        overrides = daemon.matrix_cell_overrides(name, {name: value})
        assert overrides["STRUCTURAL_RANGE_SHIFT_EXIT"] is True
        assert overrides[name] == value
        assert overrides["WT_CROSSUNDER_FINAL_ENABLED"] is False
        assert overrides["MTF_DC_REJECT_EXIT_ENABLED"] is False
        assert overrides["DYNAMIC_SCORE_COUNTER_EXIT_ENABLED"] is False


def test_srs_off_control_remains_off_on_the_ladder_floor() -> None:
    overrides = daemon.matrix_cell_overrides(
        "STRUCTURAL_RANGE_SHIFT_EXIT",
        {"STRUCTURAL_RANGE_SHIFT_EXIT": False},
    )
    assert overrides["STRUCTURAL_RANGE_SHIFT_EXIT"] is False


def test_disabled_live_master_does_not_block_isolated_subsetting_recipe() -> None:
    assert (
        exact_wiring_gate.master_state("STDEV_REJECT_EXIT_ZONE")["enabled"]
        is False
    )
    assert (
        daemon.matrix_recipe_master_enabled_override(
            "STDEV_REJECT_EXIT_ZONE", "MU", "LONG"
        )
        is True
    )

    verdict = exact_wiring_gate.classify_param(
        "STDEV_REJECT_EXIT_ZONE",
        [],
        "baseline",
        None,
        symbol="MU",
        side="LONG",
        master_enabled_override=True,
    )

    assert verdict["verdict"] == "NEEDS_TWO_VALUE_SMOKE"
    assert verdict["master"]["enabled"] is True


def test_default_subsetting_is_not_mistaken_for_baseline_alias() -> None:
    # The numeric value is the config default, but the isolated recipe also
    # enables STDEV_REJECT_EXIT_ENABLED, so this must receive an exact run.
    assert (
        daemon.wiring_gate.TradierConfig.STDEV_REJECT_EXIT_ZONE == 0.8
    )
    assert not daemon.matrix_cell_is_baseline_alias(
        "STDEV_REJECT_EXIT_ZONE",
        {"STDEV_REJECT_EXIT_ZONE": 0.8},
    )


def test_disabled_master_control_is_a_true_baseline_alias() -> None:
    assert daemon.matrix_cell_is_baseline_alias(
        "STDEV_REJECT_EXIT_ENABLED",
        {"STDEV_REJECT_EXIT_ENABLED": False},
    )


def test_disabled_mtf_master_is_alias_even_when_companions_are_restored() -> None:
    effective = daemon.matrix_cell_overrides(
        "MTF_DC_REJECT_EXIT_ENABLED",
        {"MTF_DC_REJECT_EXIT_ENABLED": False},
    )
    assert effective["MTF_DC_REJECT_EXIT_ENABLED"] is False
    assert effective["MTF_DC_REJECT_EXIT_TF"] == "1h"
    assert effective["MTF_DC_REJECT_EXIT_LOOKBACK"] == 5
    assert daemon.matrix_cell_is_baseline_alias(
        "MTF_DC_REJECT_EXIT_ENABLED",
        {"MTF_DC_REJECT_EXIT_ENABLED": False},
    )

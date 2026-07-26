from tools import export_switch_matrix_xls as matrix


def valid_meta(delta, gain=1.0):
    return {
        "bh_delta": delta,
        "gain_per_mo": gain,
        "trades": 3,
        "same_value_fingerprint": False,
        "validation_status": "PASS",
        "real_closes": 2,
        "reentry_violations": 0,
    }


def test_dc_low4_descriptions_are_failed_entry_diagnostics():
    for knob in (
        "DC_LOW4_STOP_ENABLED",
        "R1_DC_LOW4_3M_EMERGENCY_ENABLED",
    ):
        description = matrix.describe_knob(knob)
        assert "ENTRY-QUALITY FAILURE DIAGNOSTIC" in description
        assert "NOT" in description
        assert "profit" in description


def test_dc_low4_below_bh_stays_gray_even_if_nominal_gain_is_positive():
    assert matrix.matrix_cell_state(
        "DC_LOW4_STOP_ENABLED",
        valid_meta(-0.5, gain=2.0),
    ) == "gray"
    assert matrix.matrix_cell_state(
        "UNRELATED_EXIT_ENABLED",
        valid_meta(-0.5, gain=2.0),
    ) == "white"


def test_structural_description_separates_current_path_from_unmeasured_proposal():
    description = matrix.describe_knob("LONG_STRUCT_EXIT_TF")
    assert "CURRENT DIRECT EXIT" in description
    assert "PROPOSED/UNTESTED" in description
    assert "reentry obligation must remain latched" in description

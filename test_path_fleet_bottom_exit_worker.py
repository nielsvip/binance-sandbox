from tools.path_fleet_bottom_exit_worker import FAMILIES


def test_bottom_worker_binds_all_three_registered_families():
    assert FAMILIES == {
        "BOTTOM_A_PROTECTIVE_TRAIL": ("BOTTOM_A", 220),
        "BOTTOM_B_DELAYED_LOWER_TOP": ("BOTTOM_B", 864),
        "BOTTOM_C_DELAYED_EMERGENCY": ("BOTTOM_C", 324),
    }

from tools import path_fleet_control_worker as worker


def test_worker_module_points_at_control_runner():
    assert worker.ROOT.joinpath("tools", "vec_band_ladder_walkforward.py").name == (
        "vec_band_ladder_walkforward.py"
    )

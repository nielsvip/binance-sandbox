from tools.path_fleet_wt_mtf_worker import PATH_ID


def test_worker_is_bound_to_wt_exit_family():
    assert PATH_ID == "EXIT_WT_MTF"

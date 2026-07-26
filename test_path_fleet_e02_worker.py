from tools.path_fleet_e02_worker import (
    _bh_validation,
    _same_control_validation,
)


def _candidate():
    return {
        "nested": {
            "validation": {"capital_return_pct_sum": 150.0},
            "validation_alpha_vs_bh_pp": 50.0,
            "validation_alpha_vs_same_entry_e02_pp": 20.0,
        }
    }


def test_validation_benchmarks_are_reconstructed_without_side_pooling():
    candidate = _candidate()
    assert _bh_validation(candidate) == 100.0
    assert _same_control_validation(candidate) == 130.0

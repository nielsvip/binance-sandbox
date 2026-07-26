import numpy as np
import pytest

from tools import regime_conditioned_exposure_grid as grid


def _view():
    return {
        "lrL_pct_b_4h": np.array([0.2, 0.5, 0.8]),
        "lrL_pct_b_D": np.array([0.2, 0.5, 0.8]),
        "stoch_k_1h": np.array([20.0, 50.0, 80.0]),
        "stoch_d_1h": np.array([30.0, 50.0, 70.0]),
        "adx_4h": np.array([25.0, 10.0, 25.0]),
        "relative_volume_4h": np.ones(3),
    }


def test_classifier_is_exact_side_mirror_and_missing_never_supportive():
    long = grid.classify_completed_regime(_view(), "LONG")
    short = grid.classify_completed_regime(_view(), "SHORT")
    assert long.tolist() == [
        grid.ADVERSE, grid.NEUTRAL, grid.SUPPORTIVE
    ]
    assert short.tolist() == [
        grid.SUPPORTIVE, grid.NEUTRAL, grid.ADVERSE
    ]
    missing = _view()
    missing["adx_4h"] = np.array([np.nan, np.nan, np.nan])
    assert grid.classify_completed_regime(missing, "LONG").tolist() == [
        grid.NEUTRAL, grid.NEUTRAL, grid.NEUTRAL
    ]


def test_policy_grid_is_monotonic_and_hard_capped():
    for policy in grid.policy_grid():
        policy.validate()
        assert max(policy.regime_max_mult) <= 8.0
        assert policy.multiplier_scale == tuple(
            sorted(policy.multiplier_scale)
        )
        assert policy.min_gap_completed_1h_bars == tuple(
            sorted(policy.min_gap_completed_1h_bars, reverse=True)
        )


def test_apply_policy_thins_by_completed_slot_and_never_exceeds_8x():
    policy = next(
        row for row in grid.policy_grid() if row.name == "ternary_balanced"
    )
    entry = np.array([8.0, 8.0, 8.0, 8.0, 8.0])
    regime = np.array(
        [grid.ADVERSE, grid.ADVERSE, grid.NEUTRAL, grid.SUPPORTIVE, grid.SUPPORTIVE]
    )
    slots = np.array([1, 2, 4, 4, 5])
    result, audit = grid.apply_policy(entry, regime, slots, policy)
    assert result.tolist() == [4.0, 0.0, 6.0, 0.0, 8.0]
    assert audit["max_output_mult"] == 8.0
    assert audit["requests"] == 5
    assert audit["accepted"] == 3


def test_non_monotonic_or_over_capacity_policy_is_rejected():
    with pytest.raises(ValueError):
        grid.Policy("bad", 3, (1.0, 0.5, 2.0), (2, 1, 0), (4, 6, 8)).validate()
    with pytest.raises(ValueError):
        grid.Policy("bad", 3, (0.5, 1.0, 2.0), (2, 1, 0), (4, 6, 9)).validate()

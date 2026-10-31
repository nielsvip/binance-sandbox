import numpy as np
import pytest

from vec_decisions.tradier_missing_stdev_parent import (
    completed_parent_snapshot_values,
    previous_completed_parent_values,
)


def test_previous_value_uses_final_observation_of_prior_daily_parent():
    current = [0.2, 0.3, 0.4, 0.7, 0.8, 0.9, 0.5]
    tokens = [10, 10, 10, 20, 20, 20, 30]
    previous = previous_completed_parent_values(current, tokens)
    assert np.isnan(previous[:3]).all()
    np.testing.assert_allclose(previous[3:], [0.4, 0.4, 0.4, 0.9])


def test_parent_array_rejects_mismatched_shapes():
    with pytest.raises(ValueError, match="matching 1D arrays"):
        previous_completed_parent_values([0.2, 0.3], [10])


def test_missing_parent_token_resets_without_fabricating_predecessor():
    current = [0.2, 0.3, 0.4, 0.7]
    tokens = [10, 10, None, 20]
    previous = previous_completed_parent_values(current, tokens)
    assert np.isnan(previous).all()


def test_fixed_daily_bb_previous_matches_live_previous_row_contract():
    import pandas as pd

    from tradier_indicators import bb_features
    from vec_decisions.live_indicator_arrays import bb_fixed_arrays

    closes = np.asarray([80.0 + i * 0.37 + (i % 4) * 0.8 for i in range(32)])
    vector_current = bb_fixed_arrays(closes, "D")["bb_pct_b_D"]
    live_previous = np.asarray(
        [
            np.nan
            if index < 20
            else bb_features(pd.Series(closes[:index]), length=20, std_mult=2.0)[2]
            for index in range(len(closes))
        ],
        dtype=float,
    )
    np.testing.assert_allclose(vector_current[19:-1], live_previous[20:], equal_nan=True)


def test_completed_parent_pair_advances_only_at_new_available_token():
    current = [0.8, 0.9, 0.7, 0.6, 0.5, 0.4]
    tokens = [10, 10, 20, 20, 30, 30]
    completed, previous = completed_parent_snapshot_values(current, tokens)
    assert np.isnan(completed[:2]).all()
    np.testing.assert_allclose(completed[2:], [0.9, 0.9, 0.6, 0.6])
    assert np.isnan(previous[:4]).all()
    np.testing.assert_allclose(previous[4:], [0.9, 0.9])


def test_completed_parent_values_do_not_depend_on_future_npz_rows():
    values = [0.8, 0.9, 0.7, 0.6, 0.5, 0.4]
    tokens = [10, 10, 20, 20, 30, 30]
    baseline = completed_parent_snapshot_values(values, tokens)
    changed_future = completed_parent_snapshot_values(
        values[:4] + [0.99, 0.01], tokens
    )
    np.testing.assert_allclose(baseline[0][:4], changed_future[0][:4], equal_nan=True)
    np.testing.assert_allclose(baseline[1][:4], changed_future[1][:4], equal_nan=True)

import numpy as np
import pytest

from vec_decisions.tradier_missing_stdev_parent import (
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

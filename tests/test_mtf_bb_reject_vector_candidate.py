import numpy as np

from vec_decisions.mtf_bb_reject_vector_candidate import bb_reject_close_mask
from vec_decisions.mtf_compound_exits import bb_reject_step


def _scalar_mask(ts, high, low, upper, lower, lookback, is_long, tf_secs):
    tags = []
    fired = []
    for i in range(len(ts)):
        tags, fire = bb_reject_step(
            tags, ts[i], high[i], low[i], upper[i], lower[i],
            lookback, is_long, tf_secs=tf_secs,
        )
        fired.append(fire)
    return np.asarray(fired, dtype=bool)


def test_short_one_hour_tag_then_failure_matches_live_scalar():
    ts = np.arange(7, dtype=float) * 3600.0
    lower = np.full(7, 100.0)
    upper = np.full(7, 120.0)
    low = np.array([99.9, 100.4, 100.5, 100.2, 99.8, 100.4, 100.5])
    high = np.full(7, 121.0)
    got = bb_reject_close_mask(ts, high, low, upper, lower, 5, False, 3600)
    expected = _scalar_mask(ts, high, low, upper, lower, 5, False, 3600)
    np.testing.assert_array_equal(got, expected)
    np.testing.assert_array_equal(got, [False, True, True, True, False, True, True])


def test_long_tag_then_failure_matches_live_scalar():
    ts = np.arange(4, dtype=float) * 900.0
    upper = np.full(4, 110.0)
    lower = np.full(4, 90.0)
    high = np.array([110.1, 109.5, 109.4, 110.2])
    low = np.full(4, 89.0)
    got = bb_reject_close_mask(ts, high, low, upper, lower, 5, True, 900)
    expected = _scalar_mask(ts, high, low, upper, lower, 5, True, 900)
    np.testing.assert_array_equal(got, expected)
    np.testing.assert_array_equal(got, [False, True, True, False])


def test_missing_or_nonfinite_inputs_return_none():
    assert bb_reject_close_mask([0, 1], [1, 1], [1, 1], [2], [0], 5, False, 3600) is None
    assert bb_reject_close_mask([0, np.nan], [1, 1], [1, 1], [2, 2], [0, 0], 5, False, 3600) is None

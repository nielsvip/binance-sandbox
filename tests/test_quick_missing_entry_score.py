import numpy as np

from vec_decisions.quick_missing_entry_score import bb_breakout_score_scalar, bb_breakout_score_vec


class Config:
    BB_BREAKOUT_ENABLED = True
    BB_BREAKOUT_TF = "1h"
    BB_BREAKOUT_SCORE = 20


def _fixture(is_long):
    if is_long:
        return {
            "close": np.array([110.0, 110.0, 90.0, 110.0, 110.0]),
            "bb_pct_b_1h": np.array([1.01, 1.0, 1.2, 1.2, 1.2]),
            "sma_200_1h": np.array([100.0, 100.0, 100.0, 100.0, 0.0]),
            "adx_1h": np.array([25.01, 30.0, 30.0, 25.0, 30.0]),
        }
    return {
        "close": np.array([90.0, 90.0, 110.0, 90.0, 90.0]),
        "bb_pct_b_1h": np.array([-0.01, 0.0, -0.2, -0.2, -0.2]),
        "sma_200_1h": np.array([100.0, 100.0, 100.0, 100.0, 0.0]),
        "adx_1h": np.array([25.01, 30.0, 30.0, 25.0, 30.0]),
    }


def test_bb_breakout_score_matches_scalar_source_both_sides():
    for is_long in (True, False):
        npz = _fixture(is_long)
        base_score = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        vec_score, vec_mask = bb_breakout_score_vec(npz, 5, is_long, Config(), base_score)
        scalar_results = [
            bb_breakout_score_scalar(
                {key: value[index] for key, value in npz.items()},
                float(npz["close"][index]),
                is_long,
                Config(),
                float(base_score[index]),
            )
            for index in range(5)
        ]
        np.testing.assert_array_equal(vec_mask, [item[1] for item in scalar_results])
        np.testing.assert_array_equal(vec_score, [item[0] for item in scalar_results])
        assert vec_mask.sum() == 1


def test_bb_breakout_score_default_off_is_zero_delta():
    class OffConfig(Config):
        BB_BREAKOUT_ENABLED = False

    npz = _fixture(True)
    base = np.arange(5, dtype=float)
    score, mask = bb_breakout_score_vec(npz, 5, True, OffConfig(), base)
    np.testing.assert_array_equal(score, base)
    assert not mask.any()

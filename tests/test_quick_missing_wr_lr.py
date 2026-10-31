import numpy as np

from vec_decisions.check_entry_candidates_crypto__wr_lr_pullback import check_wr_lr_pullback
from vec_decisions.quick_missing_wr_lr import wr_lr_good_score, wr_lr_pullback_15m_mask


class Config:
    USE_1M_3M_SIGNALS_ENABLED = False


def test_long_mask_matches_live_15m_fallback_and_good_score_floor():
    ha = np.array(["green", "red", "neutral", "neutral"])
    k1h = np.array([30.0, 30.0, 45.0, 30.0])
    k15 = np.array([25.0, 25.0, 25.0, 35.0])
    k15p = np.array([20.0, 20.0, 20.0, 30.0])
    cross = np.array(["", "BULL", "BULL", "BULL"])
    listed = np.array([True, True, True, False])
    mask = wr_lr_pullback_15m_mask(ha, k1h, k15, k15p, cross, listed, True)
    expected = []
    for i in range(len(mask)):
        fires, _ = check_wr_lr_pullback(
            Config(),
            {"ha_4h": ha[i], "stoch_k_1h": k1h[i], "stoch_k_15m": k15[i], "stoch_k_15m_prev": k15p[i], "wt_cross_15m": cross[i]},
            {},
            bool(listed[i]),
            True,
        )
        expected.append(fires)
    np.testing.assert_array_equal(mask, expected)
    np.testing.assert_array_equal(wr_lr_good_score(np.array([10, 22, 30, 15]), mask), [22, 22, 30, 15])


def test_short_mask_matches_live_15m_fallback_and_respects_membership():
    ha = np.array(["red", "green", "neutral", "neutral"])
    k1h = np.array([70.0, 70.0, 55.0, 70.0])
    k15 = np.array([75.0, 75.0, 75.0, 65.0])
    k15p = np.array([80.0, 80.0, 80.0, 70.0])
    cross = np.array(["", "BEAR", "BEAR", "BEAR"])
    listed = np.array([True, True, True, False])
    mask = wr_lr_pullback_15m_mask(ha, k1h, k15, k15p, cross, listed, False)
    expected = []
    for i in range(len(mask)):
        fires, _ = check_wr_lr_pullback(
            Config(),
            {"ha_4h": ha[i], "stoch_k_1h": k1h[i], "stoch_k_15m": k15[i], "stoch_k_15m_prev": k15p[i], "wt_cross_15m": cross[i]},
            {},
            bool(listed[i]),
            False,
        )
        expected.append(fires)
    np.testing.assert_array_equal(mask, expected)


def test_score_floor_is_conditional_and_shape_checked():
    score = np.array([5.0, 22.0, 31.0])
    mask = np.array([True, False, True])
    np.testing.assert_array_equal(wr_lr_good_score(score, mask), [22.0, 22.0, 31.0])

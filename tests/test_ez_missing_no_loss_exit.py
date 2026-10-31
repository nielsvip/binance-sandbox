import numpy as np

from vec_decisions.ez_missing_no_loss_exit import (
    no_loss_critical_exit_live_adapter,
    no_loss_critical_exit_mask,
)


class Config:
    NO_LOSS_EXIT_CRITICAL_ENABLED = True


def _live_indicators(is_long):
    if is_long:
        return {
            "dc_low_15m": 100.0,
            "high_15m": 9.0,
            "high_15m_prev": 10.0,
            "k_15m": 10.0,
            "d_15m": 20.0,
        }, 99.0
    return {
        "dc_high_15m": 100.0,
        "low_15m": 11.0,
        "low_15m_prev": 10.0,
        "k_15m": 90.0,
        "d_15m": 80.0,
    }, 101.0


def test_no_loss_vector_predicate_matches_live_helper_under_ltf_off_mapping():
    from ez_manage import check_no_loss_exit

    cfg = Config()
    for is_long in (True, False):
        indicators, price = _live_indicators(is_long)
        source_inputs = {
            **indicators,
            "k_1m": indicators["k_15m"],
            "d_1m": indicators["d_15m"],
            "k_3m": indicators["k_15m"],
            "d_3m": indicators["d_15m"],
            "current_price": price,
        }
        expected, expected_reason = check_no_loss_exit(source_inputs, price, 100.0, is_long, 0.02, 0.01)
        actual, actual_reason = no_loss_critical_exit_live_adapter(indicators, price, is_long, 0.02)
        assert actual == expected
        assert actual_reason == expected_reason
        npz = {
            "close": np.array([price]),
            "dc_low_15m" if is_long else "dc_high_15m": np.array([100.0]),
            "k_15m": np.array([indicators["k_15m"]]),
            "d_15m": np.array([indicators["d_15m"]]),
            "high_15m" if is_long else "low_15m": np.array([9.0 if is_long else 11.0]),
            "high_15m_prev" if is_long else "low_15m_prev": np.array([10.0]),
        }
        assert no_loss_critical_exit_mask(npz, 1, is_long, cfg, np.array([0.02])).tolist() == [True]
    cfg.NO_LOSS_EXIT_CRITICAL_ENABLED = False
    assert not no_loss_critical_exit_mask(npz, 1, False, cfg, np.array([0.02])).any()


def test_no_loss_critical_exit_respects_gain_boundaries_and_missing_inputs():
    cfg = Config()
    npz = {
        "close": np.array([99.0, 99.0, 99.0]),
        "dc_low_15m": np.array([100.0, 100.0, 100.0]),
        "k_15m": np.array([10.0, 10.0, 10.0]),
        "d_15m": np.array([20.0, 20.0, 20.0]),
        "high_15m": np.array([9.0, 9.0, 9.0]),
        "high_15m_prev": np.array([10.0, 10.0, 10.0]),
    }
    assert no_loss_critical_exit_mask(npz, 3, True, cfg, np.array([-0.01, 0.03, 0.02])).tolist() == [False, False, True]
    del npz["high_15m_prev"]
    assert not no_loss_critical_exit_mask(npz, 3, True, cfg, np.array([0.02, 0.02, 0.02])).any()

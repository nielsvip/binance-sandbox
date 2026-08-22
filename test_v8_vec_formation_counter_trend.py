import numpy as np

from v12_wide_engine import (
    SweepConfig,
    _tradier_counter_trend_entry_allowed_vec,
)


def test_short_counter_trend_is_blocked_but_structural_bear_bypass_is_allowed():
    arrays = {
        "wt1_1h": np.array([10.0, 10.0, 0.0], dtype=np.float32),
        "wt2_1h": np.array([5.0, 5.0, 0.0], dtype=np.float32),
        "close": np.array([110.0, 90.0, 110.0], dtype=np.float32),
        "sma_200_15m": np.array([100.0, 100.0, 100.0], dtype=np.float32),
        "low_1h": np.array([95.0, 90.0, 90.0], dtype=np.float32),
        "low_1h_prev": np.array([90.0, 95.0, 90.0], dtype=np.float32),
    }
    allowed = _tradier_counter_trend_entry_allowed_vec(
        arrays, is_long=False, config=SweepConfig(), n=3
    )
    assert allowed.tolist() == [False, True, True]


def test_long_counter_trend_gate_can_be_disabled_explicitly():
    config = SweepConfig()
    config.COUNTER_TREND_ADD_BLOCK_ENABLED = False
    arrays = {
        "wt1_1h": np.array([1.0, 1.0], dtype=np.float32),
        "wt2_1h": np.array([2.0, 2.0], dtype=np.float32),
    }
    allowed = _tradier_counter_trend_entry_allowed_vec(
        arrays, is_long=True, config=config, n=2
    )
    assert allowed.tolist() == [True, True]

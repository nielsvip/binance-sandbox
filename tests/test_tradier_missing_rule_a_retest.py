from types import SimpleNamespace

import numpy as np

from vec_decisions.tradier_missing_rule_a_retest import rule_a_retest_mask


def _safe(npz, key, n, default=0.0):
    value = npz.get(key)
    if value is None or len(value) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(value, dtype=float)


def _fixture(is_long):
    if is_long:
        return {
            "close": np.array([101.0, 101.0, 101.0]),
            "wt1_D": np.array([2.0, 2.0, 2.0]),
            "wt2_D": np.array([1.0, 1.0, 1.0]),
            "wt1_W": np.array([2.0, 2.0, 2.0]),
            "wt2_W": np.array([1.0, 1.0, 1.0]),
            "dc_basis_D": np.array([100.0, 100.0, 100.0]),
            "atr_D": np.array([5.0, 5.0, 5.0]),
            "wt1_15m": np.array([2.0, 2.0, 2.0]),
            "wt2_15m": np.array([1.0, 1.0, 1.0]),
            "wt1_1h": np.array([2.0, 2.0, 2.0]),
            "wt2_1h": np.array([1.0, 1.0, 1.0]),
            "stoch_k_15m": np.array([20.0, 40.0, 30.0]),
            "stoch_d_15m": np.array([10.0, 35.0, 32.0]),
        }
    return {
        "close": np.array([99.0, 99.0, 99.0]),
        "wt1_D": np.array([1.0, 1.0, 1.0]),
        "wt2_D": np.array([2.0, 2.0, 2.0]),
        "wt1_W": np.array([1.0, 1.0, 1.0]),
        "wt2_W": np.array([2.0, 2.0, 2.0]),
        "dc_basis_D": np.array([100.0, 100.0, 100.0]),
        "atr_D": np.array([5.0, 5.0, 5.0]),
        "wt1_15m": np.array([1.0, 1.0, 1.0]),
        "wt2_15m": np.array([2.0, 2.0, 2.0]),
        "wt1_1h": np.array([1.0, 1.0, 1.0]),
        "wt2_1h": np.array([2.0, 2.0, 2.0]),
        "stoch_k_15m": np.array([80.0, 60.0, 70.0]),
        "stoch_d_15m": np.array([90.0, 65.0, 68.0]),
    }


def test_disabled_master_is_zero():
    cfg = SimpleNamespace(BREAKOUT_RETEST_ARMED_ENABLED=False)
    result = rule_a_retest_mask(_fixture(True), 3, True, cfg, _safe)
    assert result.tolist() == [False, False, False]


def test_long_and_short_fire_on_15m_stochastic_turn_only():
    cfg = SimpleNamespace(
        BREAKOUT_RETEST_ARMED_ENABLED=True,
        BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT=0.30,
    )
    assert rule_a_retest_mask(_fixture(True), 3, True, cfg, _safe).tolist() == [False, True, False]
    assert rule_a_retest_mask(_fixture(False), 3, False, cfg, _safe).tolist() == [False, True, False]


def test_data_and_strict_retest_band_fail_closed():
    cfg = SimpleNamespace(
        BREAKOUT_RETEST_ARMED_ENABLED=True,
        BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT=0.20,
    )
    data = _fixture(True)
    assert rule_a_retest_mask(data, 3, True, cfg, _safe).tolist() == [False, False, False]
    data["atr_D"] = np.zeros(3)
    assert rule_a_retest_mask(data, 3, True, cfg, _safe).tolist() == [False, False, False]

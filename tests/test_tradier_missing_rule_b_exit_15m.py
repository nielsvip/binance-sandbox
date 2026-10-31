from types import SimpleNamespace

import numpy as np

from vec_decisions.tradier_missing_rule_b_exit_15m import (
    rule_b_exit_15m_fires,
    rule_b_exit_15m_mask,
)


def _safe(npz, key, n, default=0.0):
    value = npz.get(key)
    if value is None or len(value) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(value, dtype=float)


def _long_data():
    return {
        "low_15m": np.array([8.0, 7.0, 7.0]),
        "low_15m_prev": np.array([8.0, 8.0, 7.0]),
        "high_15m": np.array([10.0, 9.0, 9.0]),
        "high_15m_prev": np.array([10.0, 11.0, 9.0]),
    }


def test_long_rule_b_mask_matches_live_scalar_and_gain_floor():
    data = _long_data()
    cfg = SimpleNamespace(RULE_B_5M_EXIT_ENABLED=True, NOLOSS_MIN_PROFIT_PCT_TRADIER=3.0)
    gains = np.array([3.0, 3.0, 3.0])
    mask = rule_b_exit_15m_mask(data, 3, True, cfg, gains, _safe)
    assert mask.tolist() == [False, True, False]
    get = lambda key, default=None: getattr(cfg, key, default)
    live, reason = rule_b_exit_15m_fires(
        get,
        True,
        {key: values[1] for key, values in data.items()},
        gains[1],
    )
    assert live is True
    assert reason == "RULE_B_15M_LL_LH_gain:3.00%"
    assert not rule_b_exit_15m_mask(data, 3, True, cfg, np.array([2.99] * 3), _safe).any()


def test_short_rule_b_mirror_and_off_switch_are_inert():
    data = {
        "low_15m": np.array([8.0, 9.0, 9.0]),
        "low_15m_prev": np.array([8.0, 8.0, 9.0]),
        "high_15m": np.array([10.0, 11.0, 11.0]),
        "high_15m_prev": np.array([10.0, 10.0, 11.0]),
    }
    on = SimpleNamespace(RULE_B_5M_EXIT_ENABLED=True, NOLOSS_MIN_PROFIT_PCT_TRADIER=3.0)
    off = SimpleNamespace(RULE_B_5M_EXIT_ENABLED=False, NOLOSS_MIN_PROFIT_PCT_TRADIER=3.0)
    assert rule_b_exit_15m_mask(data, 3, False, on, np.full(3, 3.0), _safe).tolist() == [False, True, False]
    assert not rule_b_exit_15m_mask(data, 3, False, off, np.full(3, 3.0), _safe).any()


def test_missing_price_fields_fail_closed():
    cfg = SimpleNamespace(RULE_B_5M_EXIT_ENABLED=True, NOLOSS_MIN_PROFIT_PCT_TRADIER=3.0)
    assert not rule_b_exit_15m_mask({}, 3, True, cfg, np.full(3, 4.0), _safe).any()

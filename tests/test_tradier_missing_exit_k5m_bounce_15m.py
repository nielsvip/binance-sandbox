from types import SimpleNamespace

import numpy as np

from vec_decisions.tradier_missing_exit_k5m_bounce_15m import (
    exit_k5m_bounce_live,
    exit_k5m_bounce_mask,
)


def _safe(npz, key, n, default=0.0):
    value = npz.get(key)
    if value is None or len(value) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(value, dtype=float)


def test_long_and_short_mask_match_native_predicate_on_15m_inputs():
    cfg = SimpleNamespace(
        EXIT_K5M_BOUNCE_ENABLED=True,
        NOLOSS_MIN_PROFIT_PCT_TRADIER=3.0,
    )
    data = {
        "close": np.array([98.0, 97.0, 102.0, 102.0]),
        "dc_low_15m": np.array([99.0, 99.0, 99.0, 99.0]),
        "dc_high_15m": np.array([101.0, 101.0, 101.0, 101.0]),
        "stoch_k_15m": np.array([20.0, 20.0, 80.0, 80.0]),
    }
    gain = np.array([3.0, 2.99, 3.0, 2.99])
    assert exit_k5m_bounce_mask(data, 4, True, gain, cfg, _safe).tolist() == [True, False, False, False]
    assert exit_k5m_bounce_mask(data, 4, False, gain, cfg, _safe).tolist() == [False, False, True, False]


def test_off_missing_band_and_boundary_values_fail_closed():
    cfg = SimpleNamespace(
        EXIT_K5M_BOUNCE_ENABLED=False,
        NOLOSS_MIN_PROFIT_PCT_TRADIER=3.0,
    )
    data = {
        "close": np.array([98.0, 98.0]),
        "dc_low_15m": np.array([99.0, 0.0]),
        "stoch_k_15m": np.array([30.0, 20.0]),
    }
    assert exit_k5m_bounce_mask(data, 2, True, np.array([4.0, 4.0]), cfg, _safe).tolist() == [False, False]
    cfg.EXIT_K5M_BOUNCE_ENABLED = True
    assert exit_k5m_bounce_mask(data, 2, True, np.array([4.0, 4.0]), cfg, _safe).tolist() == [False, False]
    assert exit_k5m_bounce_mask(data, 2, True, np.array([4.0]), cfg, _safe).tolist() == [False, False]


def test_live_scalar_adapter_matches_long_and_short_masks():
    cfg = SimpleNamespace(
        EXIT_K5M_BOUNCE_ENABLED=True,
        NOLOSS_MIN_PROFIT_PCT_TRADIER=3.0,
    )
    assert exit_k5m_bounce_live(cfg, {"dc_low_15m": 99.0, "stoch_k_15m": 29.0}, 98.0, 3.0, True)
    assert exit_k5m_bounce_live(cfg, {"dc_high_15m": 101.0, "stoch_k_15m": 71.0}, 102.0, 3.0, False)
    assert not exit_k5m_bounce_live(cfg, {"dc_low_15m": 99.0, "stoch_k_15m": 29.0}, 98.0, 2.99, True)

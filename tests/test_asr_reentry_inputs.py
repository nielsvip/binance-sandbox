from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.asr_lifecycle_state import build_lifecycle_arrays
from vec_decisions.asr_reentry_inputs import bind_asr_reentry_inputs


def _npz(n=4):
    values = {key: np.full(n, 50.0) for key in (
        "k_1m", "d_1m", "k_1m_prev", "k_3m", "d_3m", "k_3m_prev",
        "k_15m", "d_15m", "k_15m_prev", "k_1h", "d_1h", "k_4h", "d_4h", "k_D", "d_D",
    )}
    values.update({key: np.full(n, 100.0) for key in (
        "dc_high_3m", "dc_high_15m", "sma_200_15m", "ema_50_15m",
        "dc_basis_1h", "dc_basis_4h", "sma_200_1h",
    )})
    values.update({key: np.full(n, 90.0) for key in ("dc_low_3m", "dc_low_15m")})
    values.update({key: np.full(n, "neutral", dtype=object) for key in ("ha_1h", "ha_4h", "ha_D")})
    values.update({key: np.full(n, False) for key in ("wt_bullish_3m", "wt_bullish_15m", "wt_bullish_1h", "wt_bullish_4h", "wt_bullish_D")})
    values.update({key: np.full(n, 10.0) for key in ("high_1h", "high_1h_prev", "low_1h", "low_1h_prev", "high_4h", "high_4h_prev", "low_4h", "low_4h_prev")})
    values["relative_volume_3m"] = np.array([0.5, np.nan, 1.5, 0.4])
    values["relative_volume_15m"] = np.array([1.2, 0.2, 0.3, 0.5])
    return values


def test_binds_causal_lifecycle_and_native_volume_defaults():
    n = 4
    ts = 1000.0 + np.arange(n) * 900
    px = np.array([10.0, 10.2, 10.3, 10.4])
    lifecycle = build_lifecycle_arrays(
        [{"type": "OPEN", "bar": 0, "ts": ts[0], "price": 10.0, "qty": 2.0},
         {"type": "REDUCE", "bar": 1, "ts": ts[1], "price": 10.2, "qty": 1.0}],
        [], ts, px, True, {"position_exists": False},
    )
    cfg = SimpleNamespace(USE_1M_3M_SIGNALS_ENABLED=False, ENTRY_VOL_MIN_RATIO=1.0)
    bound = bind_asr_reentry_inputs(_npz(n), cfg, lifecycle, n, True, px,
                                    np.full(n, 4.0), np.full(n, 9.9), False)
    assert bound["context"]["position_amt"].tolist() == [0.0, 2.0, 1.0, 1.0]
    assert bound["context"]["last_reduction_price"].tolist() == [0.0, 0.0, 10.2, 10.2]
    assert bound["context"]["vol_ok"].tolist() == [True, False, False, False]
    strict_cfg = {"USE_1M_3M_SIGNALS_ENABLED": False, "ENTRY_VOL_MIN_RATIO": 1.3}
    strict = bind_asr_reentry_inputs(_npz(n), strict_cfg, lifecycle, n, True, px,
                                     np.full(n, 4.0), np.full(n, 9.9), False)
    assert strict["context"]["vol_ok"].tolist() == [False, False, False, False]
    assert bound["context"]["vol_strong"].tolist() == [False, False, False, False]
    ltf_cfg = SimpleNamespace(USE_1M_3M_SIGNALS_ENABLED=True, ENTRY_VOL_MIN_RATIO=1.0)
    ltf_bound = bind_asr_reentry_inputs(_npz(n), ltf_cfg, lifecycle, n, True, px,
                                       np.full(n, 4.0), np.full(n, 9.9), False)
    assert ltf_bound["context"]["vol_ok"].tolist() == [True, True, True, False]
    assert ltf_bound["context"]["vol_strong"].tolist() == [False, False, True, False]
    assert bound["context"]["minutes_since_reduction"][2] == 15.0
    assert bound["context"]["true_lag"].tolist() == [4.0] * n
    assert bound["context"]["bounce_reentry_k_reset"] is False


def test_external_causal_fields_are_required_and_per_bar():
    n = 2
    ts = np.array([1000.0, 1900.0])
    lifecycle = build_lifecycle_arrays([], [], ts, np.array([10.0, 10.0]), True, {"position_exists": False})
    cfg = SimpleNamespace(USE_1M_3M_SIGNALS_ENABLED=False)
    with pytest.raises(ValueError, match="true_lag"):
        bind_asr_reentry_inputs(_npz(n), cfg, lifecycle, n, True, np.ones(n), None, np.ones(n), False)
    with pytest.raises(ValueError, match="prev_cross_price"):
        bind_asr_reentry_inputs(_npz(n), cfg, lifecycle, n, True, np.ones(n), np.ones(n), None, False)
    with pytest.raises(ValueError, match="bounce_reentry_k_reset"):
        bind_asr_reentry_inputs(_npz(n), cfg, lifecycle, n, True, np.ones(n), np.ones(n), np.ones(n), None)

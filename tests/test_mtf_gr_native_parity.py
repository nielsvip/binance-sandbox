from types import SimpleNamespace

import numpy as np
import pytest

from mtf_live_evaluator import _tf_score, gr_filter_pass
from vec_decisions.mtf_gr_filter import gr_filter_pass_vec, tf_score

@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("invert", [True, False])
def test_timeframe_votes_match_actual_native_with_explicit_dc_and_nonfinite(is_long, invert):
    rng = np.random.default_rng(423)
    n = 300
    data = {}
    for field in ("wt1", "wt2", "rsi", "mfi", "dc_high", "dc_low", "dc_pct", "bb_pct_b", "relative_volume", "k", "d", "stoch_k", "stoch_d", "adx", "macd_hist", "ha_color"):
        values = rng.uniform(-10., 100., n)
        values[:9] = [0., np.nan, np.inf, -np.inf, 0.25, 0.35, 0.65, 0.75, 50.]
        data[f"{field}_15m"] = values
    close = np.full(n, 50.)
    expected = [_tf_score({**{key: value[index] for key, value in data.items()}, "current_price": close[index]}, "15m", is_long, invert) for index in range(n)]
    np.testing.assert_array_equal(tf_score(data, n, "15m", is_long, invert, close), expected)

@pytest.mark.parametrize("mode", ["crypto", "tradier"])
@pytest.mark.parametrize("use_ltf", [True, False])
@pytest.mark.parametrize("is_long", [True, False])
def test_full_gate_matches_actual_native_policy_and_missing_micro_is_not_a_free_vote(mode, use_ltf, is_long):
    rng = np.random.default_rng(24)
    n = 80
    data = {}
    for timeframe in ("3m", "5m", "15m", "1h", "4h", "D", "W"):
        for field in ("wt1", "wt2", "rsi", "mfi", "dc_high", "dc_low", "dc_pct", "bb_pct_b", "relative_volume", "k", "d", "adx", "macd_hist", "ha_color"):
            data[f"{field}_{timeframe}"] = rng.uniform(-3., 100., n)
    close = np.full(n, 50.)
    cfg = SimpleNamespace(MODE=mode, USE_1M_3M_SIGNALS_ENABLED=use_ltf, LTF_SUB15M_TRADING_ENABLED=use_ltf)
    expected = [gr_filter_pass({**{key: value[index] for key, value in data.items()}, "current_price": close[index]}, "LONG" if is_long else "SHORT", mode, cfg) for index in range(n)]
    np.testing.assert_array_equal(gr_filter_pass_vec(data, n, is_long, cfg, close), expected)
    cfg.MTF_GR_MIN_TFS = 1
    cfg.MTF_GR_MIN_IND = 1
    assert not gr_filter_pass_vec({}, 1, is_long, cfg, np.array([50.])).any()

def test_zero_thresholds_are_not_replaced_with_nonzero_defaults():
    cfg = SimpleNamespace(MTF_GR_MIN_TFS=0, MTF_GR_MIN_IND=0)
    assert gr_filter_pass({}, "LONG", "crypto", cfg)
    assert gr_filter_pass_vec({}, 1, True, cfg, np.array([50.])).all()

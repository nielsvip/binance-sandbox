from types import SimpleNamespace

import numpy as np
import pytest

from mtf_live_evaluator import _tf_score, gr_filter_pass
from vec_paths.gr_filter_vec import _tf_score_vec, build_gr_filter_mask

@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("invert", [True, False])
def test_votes_use_only_this_bar_and_match_native(is_long, invert):
    data = {
        "current_price": np.array([50., 0., 60., 70.]),
        "close": np.array([40., 50., 40., 40.]),
        "dc_pct_15m": np.array([0., 0., .8, np.nan]),
        "dc_low_15m": np.zeros(4),
        "dc_high_15m": np.full(4, 100.),
        "bb_pct_b_15m": np.array([0., 0., .8, np.nan]),
        "wt1_15m": np.array([10., -10., 0., np.nan]),
        "wt2_15m": np.array([0., 0., 10., 0.]),
        "k_15m": np.array([0., 60., 0., np.nan]),
        "stoch_k_15m": np.array([20., 10., 30., 40.]),
        "d_15m": np.array([0., 40., 0., 0.]),
        "stoch_d_15m": np.array([10., 80., 60., 20.]),
    }
    expected = [_tf_score({k: v[i] for k, v in data.items()}, "15m", is_long, invert) for i in range(4)]
    actual = _tf_score_vec(data, "15m", 4, is_long, invert)
    np.testing.assert_array_equal(actual, expected)
    for end in range(1, 5):
        np.testing.assert_array_equal(_tf_score_vec({k: v[:end] for k, v in data.items()}, "15m", end, is_long, invert), actual[:end])

@pytest.mark.parametrize("mode", ["crypto", "tradier"])
@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("use_ltf", [True, False])
@pytest.mark.parametrize("invert", [True, False])
def test_actual_entry_mask_matches_native_each_bar(mode, is_long, use_ltf, invert):
    rng = np.random.default_rng(812)
    n = 75
    data = {"close": np.linspace(90., 110., n), "sma_200_15m": np.full(n, 100.)}
    for tf in ("3m", "5m", "15m", "1h", "4h", "D", "W"):
        for field in ("wt1", "wt2", "rsi", "mfi", "k", "d", "stoch_k", "stoch_d", "adx", "macd_hist", "ha_color"):
            data[f"{field}_{tf}"] = rng.choice([0., 10., 30., 60., 90., np.nan], n)
        for field in ("dc_pct", "bb_pct_b", "relative_volume"):
            data[f"{field}_{tf}"] = rng.choice([0., .25, .35, .65, .75, 1.2, np.nan], n)
        data[f"dc_low_{tf}"] = np.full(n, 80.)
        data[f"dc_high_{tf}"] = np.full(n, 120.)
    cfg = SimpleNamespace(MTF_GR_MIN_TFS=3, MTF_GR_MIN_IND=7, MTF_GR_INVERT_DC_BB=invert, USE_1M_3M_SIGNALS_ENABLED=use_ltf, LTF_SUB15M_TRADING_ENABLED=use_ltf)
    expected = []
    for i in range(n):
        row = {key: values[i] for key, values in data.items()}
        breakout = row["close"] > 105. if is_long else row["close"] < 95.
        expected.append(gr_filter_pass(row, "LONG" if is_long else "SHORT", mode, cfg, min_ind=4 if breakout else 7))
    np.testing.assert_array_equal(build_gr_filter_mask(data, n, is_long, mode, cfg), expected)
    for end in (1, 20, 50):
        np.testing.assert_array_equal(build_gr_filter_mask({k: v[:end] for k, v in data.items()}, end, is_long, mode, cfg), expected[:end])

def test_disabled_filter_and_explicit_zero_threshold():
    assert build_gr_filter_mask({}, 3, True, "crypto", SimpleNamespace(MTF_GR_FILTER_ENABLED=False)).all()
    assert build_gr_filter_mask({}, 3, True, "crypto", SimpleNamespace(MTF_GR_MIN_IND=0, MTF_GR_MIN_TFS=0)).all()

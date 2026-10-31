from types import SimpleNamespace

import numpy as np

from golden_rule_htf import _ind_score
from vec_decisions.wt_force_open_stock import force_open_mask


def _cfg(**overrides):
    values = {
        "MODE": "tradier",
        "WT_3M_FORCE_OPEN_ENABLED": True,
        "WT_3M_FORCE_OPEN_SMA_PCT": 1.0,
        "WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS": True,
        "WT_FORCE_OPEN_FRESH_CROSS_ONLY": False,
        "WT_3M_FORCE_OPEN_GR_GATE_ENABLED": False,
        "FORCE_OPEN_REQUIRE_MTF_GR": True,
        "WT_3M_FORCE_OPEN_GR_VOTE_MIN": 15,
        "WT_3M_FORCE_OPEN_GR_MIN_TFS": 0,
        "WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_native_stock_5m_precedence_anchor_fallback_and_hh_zero_quirk():
    n = 5
    close = np.asarray([102.0, 102.0, 102.0, 102.0, 102.0])
    data = {
        "ema_200_15m": np.asarray([0.0, 100.0, 100.0, 100.0, 100.0]),
        "sma_200_15m": np.full(n, 100.0),
        "wt1_5m": np.asarray([2.0, 0.0, 2.0, 2.0, 2.0]),
        "wt2_5m": np.asarray([1.0, 0.0, 1.0, 1.0, 1.0]),
        "wt1_3m": np.full(n, -9.0),
        "wt2_3m": np.full(n, 9.0),
        "wt_crossover_value_5m": np.asarray([2.0, 0.0, 2.0, 2.0, 2.0]),
        "wt_crossover_value_5m_prev": np.asarray([1.0, 0.0, 3.0, 1.0, 1.0]),
    }
    actual = force_open_mask(data, n, True, _cfg(), close)
    # Bar 0 uses the SMA fallback; bar 1 has present 5m zeros (no 3m fallback);
    # bar 2 is allowed by the native zero-cross-value exception; bar 3 fails HH;
    # bar 4 passes every indicator gate.
    np.testing.assert_array_equal(actual, [True, False, False, True, True])


def test_native_stock_short_trend_veto_and_switch_off():
    n = 3
    close = np.asarray([98.0, 98.0, 98.0])
    data = {
        "ema_200_15m": np.full(n, 100.0),
        "wt1_5m": np.asarray([-1.0, -1.0, 1.0]),
        "wt2_5m": np.zeros(n),
        "wt_crossover_value_5m": np.asarray([1.0, 1.0, 0.0]),
        "wt_crossover_value_5m_prev": np.asarray([2.0, 2.0, 0.0]),
        "d_5m": np.asarray([50.0, 90.0, 50.0]),
        "rsi_5m": np.asarray([50.0, 80.0, 50.0]),
    }
    np.testing.assert_array_equal(force_open_mask(data, n, False, _cfg(), close), [True, True, False])
    np.testing.assert_array_equal(
        force_open_mask(data, n, False, _cfg(WT_3M_FORCE_OPEN_ENABLED=False), close),
        np.zeros(n, dtype=bool),
    )


def test_five_tf_numpy_agreement_scores_match_native_scalar():
    from vec_decisions.golden_loop import _tf_ind_matrix

    rng = np.random.default_rng(8317)
    data = {}
    n = 200
    close = rng.uniform(20.0, 220.0, n)
    fields = ("wt1", "wt2", "rsi", "mfi", "dc_position", "dc_high", "dc_low", "bb_pct_b", "bb_upper", "bb_lower", "relative_volume", "stoch_k", "stoch_d", "adx", "macd_hist", "ha_color")
    for tf in ("5m", "15m", "1h", "4h", "D"):
        for field in fields:
            if field in ("dc_high", "bb_upper"):
                value = close + rng.uniform(1, 20, n)
            elif field in ("dc_low", "bb_lower"):
                value = np.maximum(1, close - rng.uniform(1, 20, n))
            else:
                value = rng.normal(50, 30, n) if field in ("rsi", "mfi", "stoch_k", "stoch_d", "adx", "relative_volume", "dc_position", "bb_pct_b") else rng.normal(0, 5, n)
            data[f"{field}_{tf}"] = value
    for is_long in (True, False):
        for tf in ("5m", "15m", "1h", "4h", "D"):
            vector = _tf_ind_matrix(data, tf, n, is_long, close, False, 0.0, 0.0)
            scalar = np.asarray([_ind_score({key: value[i] for key, value in data.items() if key.endswith("_" + tf)}, tf, is_long, close[i])[0] for i in range(n)])
            np.testing.assert_array_equal(vector, scalar)

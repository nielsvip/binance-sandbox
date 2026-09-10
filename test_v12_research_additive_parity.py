import dataclasses

import numpy as np

import v12_quick_engine as v12


def _npz():
    n = 6
    return {
        "close_5m": np.array([100.0, 106.0, 94.0, 110.0, 90.0, 100.0]),
        "macd_1h": np.array([-1.0, -1.0, 1.0, -1.0, 1.0, 0.0]),
        "macd_crossover_1h": np.array([0, 1, 0, 1, 0, 0], dtype=np.int8),
        "macd_crossunder_1h": np.array([0, 0, 1, 0, 1, 0], dtype=np.int8),
        "sma_200_1h": np.full(n, 100.0),
        "rsi_2_1h": np.array([50.0, 10.0, 90.0, 20.0, 80.0, 50.0]),
        "ha_streak_1h": np.array([0.0, 3.0, -3.0, 2.0, -4.0, 0.0]),
        "rsi_1h": np.array([50.0, 25.0, 75.0, 20.0, 80.0, 50.0]),
        "stoch_k_1h": np.array([50.0, 15.0, 85.0, 25.0, 90.0, 50.0]),
        "ema_9_1h": np.full(n, 100.0),
    }


def _cfg(**overrides):
    cfg = v12.QuickConfig()
    cfg.BASE_TF = "5m"
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return cfg


def _block(name, is_long, **overrides):
    return v12.compute_reentry_blocks(
        _npz(), 6, is_long, _cfg(**overrides)
    )[name]


def test_research_family_typed_defaults_are_authoritative():
    fields = {field.name: field for field in dataclasses.fields(v12.QuickConfig)}
    expected = {
        "MACD_ZERO_CROSS_SCORE": 15,
        "RSI2_THRESHOLD_LONG": 15.0,
        "RSI2_THRESHOLD_SHORT": 85.0,
        "RSI_MACD_EMA_TF": "1h",
        "TRIPLE_CONF_SCORE": 30,
        "HA_WICK_QUALITY_TF": "1h",
    }
    assert {name: fields[name].default for name in expected} == expected


def test_macd_zero_cross_exact_side_mirror():
    long = _block("B_MACD_ZERO_CROSS", True, MACD_ZERO_CROSS_ENABLED=True)
    short = _block("B_MACD_ZERO_CROSS", False, MACD_ZERO_CROSS_ENABLED=True)
    assert long.tolist() == [False, True, False, True, False, False]
    assert short.tolist() == [False, False, True, False, True, False]


def test_rsi2_and_ha_quality_exact_side_mirrors():
    rsi_long = _block("B_RSI2_MEAN_REVERSION", True, RSI2_MEAN_REVERSION_ENABLED=True)
    rsi_short = _block("B_RSI2_MEAN_REVERSION", False, RSI2_MEAN_REVERSION_ENABLED=True)
    ha_long = _block("B_HA_WICK_QUALITY", True, HA_WICK_QUALITY_ENABLED=True)
    ha_short = _block("B_HA_WICK_QUALITY", False, HA_WICK_QUALITY_ENABLED=True)
    assert rsi_long.tolist() == [False, True, False, False, False, False]
    assert rsi_short.tolist() == [False, False, True, False, False, False]
    assert ha_long.tolist() == [False, True, False, False, False, False]
    assert ha_short.tolist() == [False, False, True, False, True, False]


def test_triple_confirmation_and_rsi_macd_ema_exact():
    triple_long = _block("B_TRIPLE_CONF", True, TRIPLE_CONF_ENABLED=True)
    triple_short = _block("B_TRIPLE_CONF", False, TRIPLE_CONF_ENABLED=True)
    rme_long = _block("B_RSI_MACD_EMA", True, RSI_MACD_EMA_ENABLED=True)
    rme_short = _block("B_RSI_MACD_EMA", False, RSI_MACD_EMA_ENABLED=True)
    assert triple_long.tolist() == [False, True, False, False, False, False]
    assert triple_short.tolist() == [False, False, True, False, True, False]
    assert rme_long.tolist() == [False, True, False, True, False, False]
    assert rme_short.tolist() == [False, False, True, False, True, False]


def test_missing_selected_parent_data_fails_closed():
    npz = _npz()
    npz.pop("macd_crossover_1h")
    cfg = _cfg(MACD_ZERO_CROSS_ENABLED=True, RSI_MACD_EMA_ENABLED=True)
    blocks = v12.compute_reentry_blocks(npz, 6, True, cfg)
    assert not blocks["B_MACD_ZERO_CROSS"].any()
    assert not blocks["B_RSI_MACD_EMA"].any()


def test_research_scores_are_added_without_name_derived_effects():
    blocks = {
        "B_MACD_ZERO_CROSS": np.array([True, False, False]),
        "B_RSI2_MEAN_REVERSION": np.array([False, True, False]),
        "B_HA_WICK_QUALITY": np.array([False, False, True]),
        "B_TRIPLE_CONF": np.array([True, True, False]),
        "B_RSI_MACD_EMA": np.array([False, True, True]),
    }
    cfg = _cfg(ENTRY_SCORE_THRESHOLD=18)
    admitted, score = v12._combine_entry_blocks(blocks, cfg, 3)
    assert score.tolist() == [45.0, 75.0, 40.0]
    assert admitted.tolist() == [True, True, True]

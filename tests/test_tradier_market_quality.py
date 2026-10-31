import numpy as np

from vec_decisions.tradier_market_quality import market_quality_score_scalar, market_quality_score_vec


def _column(npz, key, n, default):
    return np.asarray(npz.get(key, np.full(n, default)), dtype=float)


def _cfg(enabled=True):
    return {
        "MODE": "tradier",
        "MARKET_QUALITY_SCORE_ENABLED": False,
        "MARKET_QUALITY_SCORE_ENABLED_TRADIER": enabled,
        "TRADIER_RSI_SHORT_RVOL_1H": 1.0,
        "TRADIER_RSI_SHORT_15M": 65.0,
        "TRADIER_RSI_SHORT_1H": 65.0,
        "TRADIER_RSI_SHORT_RVOL_15M": 1.0,
    }


def _bars():
    return {
        "close": np.full(3, 100.0),
        "bb_width_1h": np.array([6.0, 10.0, 10.0]),
        "bb_width_4h": np.array([9.0, 10.0, 10.0]),
        "adx_1h": np.array([18.0, 24.0, 28.0]),
        "adx_4h": np.array([24.0, 28.0, 28.0]),
        "mfi_1h": np.array([55.0, 50.0, 50.0]),
        "mfi_4h": np.array([50.0, 50.0, 50.0]),
        "mfi_15m": np.array([20.0, 50.0, 50.0]),
        "rsi_1h": np.array([50.0, 70.0, 50.0]),
        "rsi_4h": np.array([50.0, 55.0, 50.0]),
        "rsi_15m": np.array([50.0, 70.0, 50.0]),
        "relative_volume_1h": np.array([0.9, 1.1, 0.9]),
        "relative_volume_15m": np.array([0.9, 1.2, 0.9]),
    }


def test_tradier_long_scalar_numpy_and_score_adapters_match():
    npz = _bars()
    cfg = _cfg()
    bonuses, fires = market_quality_score_vec(npz, 3, True, cfg, _column)
    scalar = []
    for i in range(3):
        indicators = {key: values[i] for key, values in npz.items() if key != "close"}
        scalar.append(market_quality_score_scalar(indicators, True, cfg.get)[0])
    assert bonuses.tolist() == scalar == [4.0, 0.0, 0.0]
    assert fires.tolist() == [True, False, False]
def test_tradier_short_thresholds_are_shared_and_stock_switch_is_distinct():
    npz = _bars()
    cfg = _cfg()
    bonuses, fires = market_quality_score_vec(npz, 3, False, cfg, _column)
    scalar = []
    for i in range(3):
        indicators = {key: values[i] for key, values in npz.items() if key != "close"}
        scalar.append(market_quality_score_scalar(indicators, False, cfg.get)[0])
    assert bonuses.tolist() == scalar == [2.0, 2.0, 0.0]
    assert fires.tolist() == [True, True, False]
    cfg["MARKET_QUALITY_SCORE_ENABLED_TRADIER"] = False
    cfg["MARKET_QUALITY_SCORE_ENABLED"] = True
    bonuses, fires = market_quality_score_vec(npz, 3, False, cfg, _column)
    assert bonuses.tolist() == [0.0, 0.0, 0.0]
    assert fires.tolist() == [False, False, False]

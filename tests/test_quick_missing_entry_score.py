import numpy as np

from vec_decisions.quick_missing_entry_score import bb_breakout_score_scalar, bb_breakout_score_vec


class Config:
    BB_BREAKOUT_ENABLED = True
    BB_BREAKOUT_TF = "1h"
    BB_BREAKOUT_SCORE = 20


def _fixture(is_long):
    if is_long:
        return {
            "close": np.array([110.0, 110.0, 90.0, 110.0, 110.0]),
            "bb_pct_b_1h": np.array([1.01, 1.0, 1.2, 1.2, 1.2]),
            "sma_200_1h": np.array([100.0, 100.0, 100.0, 100.0, 0.0]),
            "adx_1h": np.array([25.01, 30.0, 30.0, 25.0, 30.0]),
        }
    return {
        "close": np.array([90.0, 90.0, 110.0, 90.0, 90.0]),
        "bb_pct_b_1h": np.array([-0.01, 0.0, -0.2, -0.2, -0.2]),
        "sma_200_1h": np.array([100.0, 100.0, 100.0, 100.0, 0.0]),
        "adx_1h": np.array([25.01, 30.0, 30.0, 25.0, 30.0]),
    }


def test_bb_breakout_score_matches_scalar_source_both_sides():
    for is_long in (True, False):
        npz = _fixture(is_long)
        base_score = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        vec_score, vec_mask = bb_breakout_score_vec(npz, 5, is_long, Config(), base_score)
        scalar_results = [
            bb_breakout_score_scalar(
                {key: value[index] for key, value in npz.items()},
                float(npz["close"][index]),
                is_long,
                Config(),
                float(base_score[index]),
            )
            for index in range(5)
        ]
        np.testing.assert_array_equal(vec_mask, [item[1] for item in scalar_results])
        np.testing.assert_array_equal(vec_score, [item[0] for item in scalar_results])
        assert vec_mask.sum() == 1


def test_bb_breakout_score_default_off_is_zero_delta():
    class OffConfig(Config):
        BB_BREAKOUT_ENABLED = False

    npz = _fixture(True)
    base = np.arange(5, dtype=float)
    score, mask = bb_breakout_score_vec(npz, 5, True, OffConfig(), base)
    np.testing.assert_array_equal(score, base)
    assert not mask.any()


def test_research_score_bonus_masks_cover_live_non_scalp_branches():
    from vec_decisions.quick_missing_entry_score import research_score_masks_vec

    class ResearchConfig:
        EMA_PULLBACK_ENABLED = True
        EMA_PULLBACK_TF = "15m"
        EMA_PULLBACK_SCORE_BONUS = 35
        RSI2_MEAN_REVERSION_ENABLED = True
        RSI2_THRESHOLD_LONG = 15.0
        RSI2_THRESHOLD_SHORT = 85.0
        RSI2_SCORE_BONUS = 20
        TRIPLE_CONF_ENABLED = True
        TRIPLE_CONF_TF = "1h"
        TRIPLE_CONF_SCORE = 30
        EMA200_STOCHRSI_ENABLED = True
        EMA200_STOCHRSI_TF = "1h"
        EMA200_STOCHRSI_K_LONG = 20.0
        EMA200_STOCHRSI_K_SHORT = 80.0
        EMA200_STOCHRSI_BODY_MULT = 1.05
        EMA200_STOCHRSI_SCORE = 25
        RSI_MACD_EMA_ENABLED = True
        RSI_MACD_EMA_TF = "1h"
        RSI_MACD_EMA_RSI_LONG = 35.0
        RSI_MACD_EMA_RSI_SHORT = 65.0
        RSI_MACD_EMA_SCORE = 25

    npz = {
        "close": np.array([105., 95., 105., 95., 105.]),
        "ema_9_15m": np.array([110., 90., 110., 90., 110.]),
        "ema_14_15m": np.array([110., 90., 110., 90., 110.]),
        "ema_20_15m": np.array([100., 100., 100., 100., 100.]),
        "k_15m": np.array([19., 81., 50., 50., 50.]),
        "rsi_2_1h": np.array([10., 90., 50., 50., 50.]),
        "rsi_1h": np.array([20., 80., 20., 80., 50.]),
        "k_1h": np.array([10., 90., 10., 90., 50.]),
        "macd_crossover_1h": np.array([True, False, True, False, False]),
        "macd_crossunder_1h": np.array([False, True, False, True, False]),
        "sma_200_1h": np.array([100., 100., 100., 100., 100.]),
        "stoch_crossover_1h": np.array([True, False, True, False, False]),
        "stoch_crossunder_1h": np.array([False, True, False, True, False]),
        "candle_body_ratio_1h": np.array([1.1, 1.1, 1.1, 1.1, 1.1]),
        "ema_9_1h": np.array([100., 100., 100., 100., 100.]),
    }
    score, masks = research_score_masks_vec(npz, 5, True, ResearchConfig(), is_exit=False)
    np.testing.assert_array_equal(masks["EMA_PULLBACK"], [True, False, False, False, False])
    np.testing.assert_array_equal(masks["RSI2_MEAN_REVERSION"], [True, False, False, False, False])
    np.testing.assert_array_equal(masks["TRIPLE_CONF"], [True, False, True, False, False])
    np.testing.assert_array_equal(masks["EMA200_STOCHRSI"], [True, False, True, False, False])
    np.testing.assert_array_equal(masks["RSI_MACD_EMA"], [True, False, True, False, False])
    assert score.tolist() == [135., 0., 80., 0., 0.]


def test_research_bonus_is_exit_gated_only_where_live_source_gates_it():
    from vec_decisions.quick_missing_entry_score import research_score_masks_vec

    class Enabled:
        EMA_PULLBACK_ENABLED = True
        EMA_PULLBACK_SCORE_BONUS = 35
        RSI2_MEAN_REVERSION_ENABLED = True
        RSI2_SCORE_BONUS = 20
        TRIPLE_CONF_ENABLED = True
        TRIPLE_CONF_SCORE = 30
        EMA200_STOCHRSI_ENABLED = True
        EMA200_STOCHRSI_SCORE = 25
        RSI_MACD_EMA_ENABLED = True
        RSI_MACD_EMA_SCORE = 25

    npz = {
        "close": np.array([105.]), "ema_9_15m": np.array([110.]),
        "ema_14_15m": np.array([110.]), "ema_20_15m": np.array([100.]),
        "k_15m": np.array([10.]), "rsi_2_1h": np.array([10.]),
    }
    score, masks = research_score_masks_vec(npz, 1, True, Enabled(), is_exit=True)
    assert score.tolist() == [55.]
    assert masks["EMA_PULLBACK"].tolist() == [True]
    assert masks["RSI2_MEAN_REVERSION"].tolist() == [True]
    assert not masks["TRIPLE_CONF"].any()
    assert not masks["EMA200_STOCHRSI"].any()
    assert not masks["RSI_MACD_EMA"].any()


def test_research_score_vector_matches_scalar_adapter_both_sides():
    from vec_decisions.quick_missing_entry_score import research_score_masks_scalar, research_score_masks_vec

    class AllOn:
        EMA_PULLBACK_ENABLED = True
        EMA_PULLBACK_TF = "15m"
        EMA_PULLBACK_SCORE_BONUS = 35
        RSI2_MEAN_REVERSION_ENABLED = True
        RSI2_THRESHOLD_LONG = 15.0
        RSI2_THRESHOLD_SHORT = 85.0
        RSI2_SCORE_BONUS = 20
        TRIPLE_CONF_ENABLED = True
        TRIPLE_CONF_TF = "1h"
        TRIPLE_CONF_SCORE = 30
        EMA200_STOCHRSI_ENABLED = True
        EMA200_STOCHRSI_TF = "1h"
        EMA200_STOCHRSI_K_LONG = 20.0
        EMA200_STOCHRSI_K_SHORT = 80.0
        EMA200_STOCHRSI_BODY_MULT = 1.05
        EMA200_STOCHRSI_SCORE = 25
        RSI_MACD_EMA_ENABLED = True
        RSI_MACD_EMA_TF = "1h"
        RSI_MACD_EMA_RSI_LONG = 35.0
        RSI_MACD_EMA_RSI_SHORT = 65.0
        RSI_MACD_EMA_SCORE = 25

    npz = {
        "close": np.array([105., 95., 105., 95.]),
        "ema_9_15m": np.array([110., 90., 100., 100.]),
        "ema_14_15m": np.array([110., 90., 100., 100.]),
        "ema_20_15m": np.array([100., 100., 100., 100.]),
        "k_15m": np.array([19., 81., 50., 50.]),
        "rsi_2_1h": np.array([10., 90., 50., 50.]),
        "rsi_1h": np.array([20., 80., 20., 80.]),
        "k_1h": np.array([10., 90., 10., 90.]),
        "macd_crossover_1h": np.array([True, False, True, False]),
        "macd_crossunder_1h": np.array([False, True, False, True]),
        "sma_200_1h": np.array([100., 100., 100., 100.]),
        "stoch_crossover_1h": np.array([True, False, True, False]),
        "stoch_crossunder_1h": np.array([False, True, False, True]),
        "candle_body_ratio_1h": np.array([1.1, 1.1, 1.1, 1.1]),
        "ema_9_1h": np.array([100., 100., 100., 100.]),
    }
    for side in (True, False):
        for is_exit in (True, False):
            base = np.array([1., 2., 3., 4.])
            vec_score, vec_masks = research_score_masks_vec(npz, 4, side, AllOn(), is_exit=is_exit, score=base)
            scalar = [research_score_masks_scalar({k: v[i] for k, v in npz.items()}, npz["close"][i], side, AllOn(), is_exit=is_exit, score=base[i]) for i in range(4)]
            np.testing.assert_array_equal(vec_score, [x[0] for x in scalar])
            for name in vec_masks:
                np.testing.assert_array_equal(vec_masks[name], [x[1][name] for x in scalar])


def test_market_quality_bonus_vector_matches_shared_live_scalar_parts_without_sba_veto():
    from vec_decisions.quick_missing_entry_score import research_score_masks_scalar, research_score_masks_vec

    class Enabled:
        MARKET_QUALITY_SCORE_ENABLED = True

    npz = {
        "close": np.array([100., 100., 100., 100.]),
        "bb_width_1h": np.array([5., 8., 20., 20.]),
        "bb_width_4h": np.array([9., 12., 20., 20.]),
        "dc_width_1h": np.array([6., 6., 10., 10.]),
        "dc_width_4h": np.array([11., 11., 20., 20.]),
        "adx_1h": np.array([15., 22., 40., 40.]),
        "adx_4h": np.array([20., 30., 40., 40.]),
        "sma_200_1h": np.array([100., 100., 100., 100.]),
        "relative_volume_15m": np.array([1., 1., 1., 1.]),
        "relative_volume_1h": np.array([0.8, 1.0, 1.5, 1.5]),
        "mfi_1h": np.array([60., 60., 50., 50.]),
        "mfi_4h": np.array([60., 60., 50., 50.]),
        "mfi_15m": np.array([50., 50., 50., 50.]),
        "k_3m": np.array([20., 50., 50., 50.]),
        "k_15m": np.array([20., 50., 50., 50.]),
        "k_1h": np.array([30., 50., 50., 50.]),
        "stoch_crossover_15m": np.array([True, False, False, False]),
        "wt_signal_15m": np.array(["BUY", "", "", ""]),
        "wt_signal_1h": np.array(["BUY", "", "", ""]),
        "wt_divergence_4h": np.array([1, 0, 0, 0]),
        "wt_momentum_state_1h": np.array([-2, 0, 0, 0]),
        "bar_pattern_15m": np.array(["hammer", "", "", ""]),
        "rsi_2_1h": np.array([10., 50., 50., 50.]),
    }
    for side in (True, False):
        scores, masks = research_score_masks_vec(npz, 4, side, Enabled())
        scalar = [research_score_masks_scalar({k: v[i] for k, v in npz.items()}, npz["close"][i], side, Enabled()) for i in range(4)]
        np.testing.assert_array_equal(masks["MARKET_QUALITY_SCORE"], [x[1]["MARKET_QUALITY_SCORE"] for x in scalar])
        np.testing.assert_array_equal(scores, [x[0] for x in scalar])
        assert masks["MARKET_QUALITY_SCORE"].any()

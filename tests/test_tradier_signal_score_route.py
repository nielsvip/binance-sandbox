import sys

import numpy as np

sys.path.insert(0, "tests")

import v12_quick_engine as V
from test_tradier_reviewed_exit_ledgers import _base_npz, _cfg
from vec_decisions.tradier_signal_score import _Tradeability, entry_pre_gate_score_vec, entry_score_arrays, entry_tail_score_vec, mts_score_vec, native_entry_setup_masks, paired_lower_tf_indicators, possible_entry_setup_mask


def test_paired_lower_tf_stock_score_inputs_use_15m_only_when_ltf_is_off():
    source = {"k_5m": 80.0, "k_15m": 20.0, "dc_low_5m_prev": 101.0, "dc_low_15m_prev": 99.0}
    assert paired_lower_tf_indicators(source, False) == {
        "k_5m": 20.0,
        "k_15m": 20.0,
        "dc_low_5m_prev": 99.0,
        "dc_low_15m_prev": 99.0,
    }
    assert paired_lower_tf_indicators(source, True) == source


def test_paired_lower_tf_inputs_hydrate_absent_5m_aliases_from_15m():
    source = {
        "k_15m": 21.0,
        "d_15m": 19.0,
        "k_15m_prev": 17.0,
        "dc_low_15m": 98.0,
        "dc_high_15m": 102.0,
        "relative_volume_15m": 1.4,
        "rel_vol_15m": 1.3,
        "k_1m": 25.0,
    }
    paired = paired_lower_tf_indicators(source, False)
    for source_key, target_key in (
        ("k_15m", "k_5m"), ("d_15m", "d_5m"),
        ("k_15m_prev", "k_5m_prev"), ("dc_low_15m", "dc_low_5m"),
        ("dc_high_15m", "dc_high_5m"),
        ("relative_volume_15m", "relative_volume_5m"),
        ("rel_vol_15m", "rel_vol_5m"),
    ):
        assert paired[target_key] == paired[source_key]
    assert paired["k_1m"] == 25.0


def test_native_setup_prefilter_preserves_nonfinite_indicator_comparisons():
    npz = _base_npz()
    n = len(npz["close"])
    for key in ("k_15m", "d_15m", "k_5m", "d_5m", "k_5m_prev", "k_4h", "d_4h", "mfi_4h"):
        npz[key] = np.full(n, 50.0)
    npz["k_15m"][3] = np.inf
    npz["k_15m"][7] = np.nan
    npz["d_15m"][3] = 40.0
    cfg = _cfg()
    cfg.MODE = "tradier"
    cfg.LTF_SUB15M_TRADING_ENABLED = False
    cfg.K_ZONE_ENTRY_ENABLED_TRADIER = False
    cfg.BB_BREAKOUT_ENTRY_TF = "OFF"
    cfg.ENTRY_SCORE_THRESHOLD = 12.0
    cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
    full = entry_score_arrays(npz, n, "SYNTH", True, cfg, np.ones(n, dtype=bool))
    filtered = entry_score_arrays(npz, n, "SYNTH", True, cfg)
    assert np.array_equal(full[0], filtered[0])
    assert np.array_equal(full[1], filtered[1])
    assert full[2] == filtered[2]


def test_native_setup_prefilter_has_no_false_negatives_at_threshold_boundaries():
    npz = _base_npz()
    n = 40
    npz = {key: np.asarray(value)[:n].copy() for key, value in npz.items()}
    boundary = np.array([14.9, 15.0, 15.1, 29.9, 30.0, 30.1, 34.9, 35.0, 35.1, 49.9, 50.0, 50.1, 64.9, 65.0, 65.1, 69.9, 70.0, 70.1, 84.9, 85.0] * 2)
    for key, values in {
        "k_15m": boundary,
        "d_15m": np.roll(boundary, 3),
        "k_5m": 100.0 - boundary,
        "d_5m": np.roll(100.0 - boundary, 2),
        "k_5m_prev": np.roll(100.0 - boundary, 1),
        "k_4h": boundary,
        "d_4h": np.roll(boundary, 1),
        "mfi_4h": boundary,
        "mfi_15m": 100.0 - boundary,
        "bb_pct_b_1h": np.where(boundary < 50.0, 0.95, 0.05),
        "bb_upper_1h": np.full(n, 101.0),
        "bb_lower_1h": np.full(n, 99.0),
        "timestamp_1m": np.asarray(npz["timestamps"], dtype=float) - 30.0,
    }.items():
        npz[key] = np.asarray(values, dtype=float)
    score_configs = ((0.0, "OFF", False), (12.0, "1h", True), (18.0, "4h", False), (30.0, "D", True))
    for is_long in (True, False):
        for ltf_enabled in (False, True):
            for mq_enabled in (False, True):
                for threshold, bb_tf, k_zone_enabled in score_configs:
                    cfg = _cfg()
                    cfg.LTF_SUB15M_TRADING_ENABLED = ltf_enabled
                    cfg.MARKET_QUALITY_SCORE_ENABLED_TRADIER = mq_enabled
                    cfg.BB_BREAKOUT_ENTRY_TF = bb_tf
                    cfg.K_ZONE_ENTRY_ENABLED_TRADIER = k_zone_enabled
                    cfg.ENTRY_SCORE_THRESHOLD = threshold
                    cfg.WT_COMPOSITE_SCORING_ENABLED_TRADIER = False
                    cfg.WT_COMPOSITE_SCORING_ENABLED = False
                    cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
                    full = entry_score_arrays(npz, n, "SYNTH", is_long, cfg, np.ones(n, dtype=bool))
                    filtered = entry_score_arrays(npz, n, "SYNTH", is_long, cfg)
                    assert np.array_equal(full[0], filtered[0])
                    assert np.array_equal(full[1], filtered[1])
                    assert full[2] == filtered[2]


def test_native_setup_prefilter_matches_missing_and_future_m1_timestamp_semantics():
    timestamp_modes = (("missing", None, True), ("future", "future", True), ("positive_inf", np.inf, True), ("negative_inf", -np.inf, False), ("nan", np.nan, False))
    for timestamp_mode, timestamp_value, expected_fresh in timestamp_modes:
        npz = _base_npz()
        n = len(npz["close"])
        for key in ("k_15m", "d_15m", "k_5m", "d_5m", "k_5m_prev", "k_4h", "d_4h", "mfi_4h"):
            npz[key] = np.full(n, 50.0)
        npz["k_1m"] = np.full(n, 20.0)
        npz["d_1m"] = np.full(n, 15.0)
        npz["k_1m_prev"] = np.full(n, 10.0)
        if timestamp_mode == "future":
            npz["timestamp_1m"] = np.asarray(npz["timestamps"], dtype=float) + 60.0
        elif timestamp_mode == "missing":
            npz.pop("timestamp_1m", None)
        else:
            npz["timestamp_1m"] = np.full(n, timestamp_value, dtype=float)
        cfg = _cfg()
        cfg.MODE = "tradier"
        cfg.LTF_SUB15M_TRADING_ENABLED = False
        cfg.K_ZONE_ENTRY_ENABLED_TRADIER = False
        cfg.BB_BREAKOUT_ENTRY_TF = "OFF"
        cfg.ENTRY_SCORE_THRESHOLD = 12.0
        cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
        candidates = possible_entry_setup_mask(npz, n, True, cfg)
        assert bool(candidates[0]) is expected_fresh
        full = entry_score_arrays(npz, n, "SYNTH", True, cfg, np.ones(n, dtype=bool))
        filtered = entry_score_arrays(npz, n, "SYNTH", True, cfg)
        assert np.array_equal(full[0], filtered[0])
        assert np.array_equal(full[1], filtered[1])
        assert full[2] == filtered[2]


def _mq_crossing_fixture(enabled):
    npz = _base_npz()
    n = len(npz["close"])
    fire_bar = 201
    values = {
        "k_15m": 50.0,
        "d_15m": 50.0,
        "k_5m": 20.0,
        "d_5m": 30.0,
        "k_4h": 50.0,
        "d_4h": 45.0,
        "mfi_4h": 41.0,
        "mfi_1h": 55.0,
        "mfi_15m": 20.0,
        "bb_width_1h": 6.0,
        "bb_width_4h": 9.0,
        "adx_1h": 18.0,
        "adx_4h": 24.0,
        "relative_volume_1h": 0.9,
        "rsi_1h": 55.0,
        "rsi_4h": 55.0,
    }
    for key, value in values.items():
        column = np.full(n, 50.0)
        column[fire_bar] = value
        npz[key] = column
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"stoch_k_{tf}"] = np.full(n, 60.0)
        npz[f"stoch_d_{tf}"] = np.full(n, 50.0)
    cfg = _cfg()
    cfg.LTF_SUB15M_TRADING_ENABLED = False
    cfg.MARKET_QUALITY_SCORE_ENABLED_TRADIER = enabled
    cfg.ENTRY_SCORE_THRESHOLD = 8.0
    cfg.K_ZONE_ENTRY_ENABLED_TRADIER = False
    cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
    cfg.RSI_ENTRY_LONG_TRADIER = 42.0
    cfg.WT_COMPOSITE_SCORING_ENABLED_TRADIER = False
    cfg.WT_COMPOSITE_SCORING_ENABLED = False
    cfg.BEAR_MARKET_MODE_TRADIER = False
    cfg.STRUCTURAL_RANGE_SHIFT_EXIT = False
    for key in (
        "TR_ADX4H_GATE_ENABLED",
        "TR_BBWIDTH4H_GATE_ENABLED",
        "TR_CHOP4H_GATE_ENABLED",
        "TR_MFI4H_LONG_ENABLED",
        "TR_DCWIDTH4H_SHORT_ENABLED",
        "WT_15M_BOUNCE_OPEN_ENABLED",
        "WT_SIMPLE_GUARANTEE_ENABLED",
    ):
        setattr(cfg, key, False)
    return npz, cfg, fire_bar


def test_vector_wt_composite_matches_native_scalar_oracle_both_sides():
    import tradier_manage as tm

    for is_long in (True, False):
        npz = _base_npz()
        n = len(npz["close"])
        bar = 201
        for key in ("k_15m", "d_15m", "k_5m", "d_5m", "k_5m_prev", "k_4h", "d_4h"):
            npz[key] = np.full(n, 50.0)
        npz["k_15m"][bar] = 20.0 if is_long else 80.0
        npz["d_15m"][bar] = 30.0 if is_long else 85.0
        npz["mfi_4h"] = np.full(n, 20.0 if is_long else 80.0)
        comp_key = "wt_composite_long" if is_long else "wt_composite_short"
        align_key = "wt_bull_alignment" if is_long else "wt_bear_alignment"
        npz[comp_key] = np.full(n, 0.0)
        npz[align_key] = np.full(n, 4.0)
        npz[comp_key][bar] = 55.0
        npz["wt_oversold_tf_count"] = np.full(n, 0, dtype=int)
        npz["wt_overbought_tf_count"] = np.full(n, 0, dtype=int)
        npz["wt_oversold_tf_count"][bar] = 2
        npz["wt_overbought_tf_count"][bar] = 2
        for tf in ("5m", "15m", "1h", "4h"):
            npz[f"wt_divergence_{tf}"] = np.full(n, "NONE", dtype=object)
        if is_long:
            for tf in ("5m", "15m", "1h"):
                npz[f"wt_divergence_{tf}"][bar] = "BULL"
            npz["wt_divergence_4h"][bar] = "BEAR"
            npz["wt_strongest_bull_div_tf"] = np.full(n, "4h", dtype=object)
        else:
            for tf in ("5m", "15m", "1h"):
                npz[f"wt_divergence_{tf}"][bar] = "BEAR"
            npz["wt_divergence_4h"][bar] = "BULL"
            npz["wt_strongest_bear_div_tf"] = np.full(n, "4h", dtype=object)
        cfg = _cfg()
        cfg.MODE = "tradier"
        cfg.LTF_SUB15M_TRADING_ENABLED = False
        cfg.MARKET_QUALITY_SCORE_ENABLED_TRADIER = False
        cfg.K_ZONE_ENTRY_ENABLED_TRADIER = False
        cfg.BB_BREAKOUT_ENTRY_TF = "OFF"
        cfg.ENTRY_SCORE_THRESHOLD = 0.0
        cfg.WT_COMPOSITE_SCORING_ENABLED_TRADIER = True
        cfg.WT_COMPOSITE_SCORING_ENABLED = False
        cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
        vector_scores, _, vector_reasons = entry_score_arrays(npz, n, "SYNTH", is_long, cfg)
        strategy = tm.StockStrategy.__new__(tm.StockStrategy)
        strategy.config = cfg
        strategy.trade_manager = _Tradeability("SYNTH", is_long)
        indicators = {key: (np.asarray(value)[bar].item() if hasattr(np.asarray(value)[bar], "item") else np.asarray(value)[bar]) for key, value in npz.items() if np.asarray(value).ndim == 1 and len(np.asarray(value)) == n}
        cfg_token = tm._TR_SCORE_CFG_CTX.set(cfg)
        ts_token = tm._TR_SCORE_TS_CTX.set(float(npz["timestamps"][bar]))
        try:
            scalar_score, _, scalar_reason = strategy.calculate_signal_score(
                "v12", "SYNTH", is_long, float(indicators["close"]), indicators, None, is_exit=False
            )
        finally:
            tm._TR_SCORE_TS_CTX.reset(ts_token)
            tm._TR_SCORE_CFG_CTX.reset(cfg_token)
        assert vector_scores[bar] == scalar_score
        assert scalar_reason in vector_reasons[bar]
        assert "WT_STRONG" in scalar_reason
        assert "DIV_" in scalar_reason


def test_vector_trigger_score_matches_native_scalar_oracle_with_momentum_fade():
    import tradier_manage as tm

    for is_long in (True, False):
        npz = _base_npz()
        n = len(npz["close"])
        bar = 201
        values = {
            "k_15m": 20.0 if is_long else 80.0,
            "d_15m": 30.0 if is_long else 85.0,
            "k_1m": 20.0 if is_long else 80.0,
            "d_1m": 15.0 if is_long else 85.0,
            "k_1m_prev": 10.0 if is_long else 90.0,
            "wt1_15m": 10.0 if is_long else -10.0,
            "wt2_15m": 0.0,
            "rel_vol_15m": 2.5,
            "atr_15m": 1.0,
            "high_15m": 105.0,
            "low_15m": 100.0,
            "mfi_4h": 20.0 if is_long else 80.0,
        }
        for key, value in values.items():
            column = np.full(n, value)
            if key in ("k_15m", "d_15m", "k_1m", "d_1m", "k_1m_prev", "wt1_15m", "wt2_15m", "rel_vol_15m", "atr_15m", "high_15m", "low_15m", "mfi_4h"):
                npz[key] = column
        npz["timestamp_1m"] = np.asarray(npz["timestamps"], dtype=float) - 30.0
        npz["bar_direction_1h"] = np.zeros(n)
        npz["bar_strength_1h"] = np.zeros(n)
        npz["bar_vol_confirm_1h"] = np.zeros(n, dtype=bool)
        npz["bar_pattern_1h"] = np.full(n, "none", dtype=object)
        npz["bar_swing_bull_1h"] = np.zeros(n, dtype=bool)
        npz["bar_swing_bear_1h"] = np.zeros(n, dtype=bool)
        npz["bar_streak_1h"] = np.zeros(n, dtype=int)
        npz["bar_compression_1h"] = np.zeros(n, dtype=bool)
        npz["bar_vol_spike_1h"] = np.zeros(n, dtype=bool)
        npz["bar_direction_1h"][bar] = 1.0 if is_long else -1.0
        npz["bar_strength_1h"][bar] = 0.7
        npz["bar_vol_confirm_1h"][bar] = True
        npz["bar_pattern_1h"][bar] = "BullishPattern" if is_long else "BearishPattern"
        npz["bar_swing_bull_1h"][bar] = is_long
        npz["bar_swing_bear_1h"][bar] = not is_long
        npz["bar_streak_1h"][bar] = 3 if is_long else -3
        npz["bar_compression_1h"][bar] = True
        npz["bar_vol_spike_1h"][bar] = True
        cfg = _cfg()
        cfg.MODE = "tradier"
        cfg.LTF_SUB15M_TRADING_ENABLED = False
        cfg.MARKET_QUALITY_SCORE_ENABLED_TRADIER = False
        cfg.K_ZONE_ENTRY_ENABLED_TRADIER = False
        cfg.BB_BREAKOUT_ENTRY_TF = "OFF"
        cfg.ENTRY_SCORE_THRESHOLD = 0.0
        cfg.WT_COMPOSITE_SCORING_ENABLED_TRADIER = False
        cfg.WT_COMPOSITE_SCORING_ENABLED = False
        cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
        cfg.MOMENTUM_FADE_ENABLED_TRADIER = True
        cfg.MOMENTUM_FADE_BODY_ATR_MIN_TRADIER = 2.0
        cfg.MOMENTUM_FADE_VOL_MIN_TRADIER = 2.0
        cfg.MOMENTUM_FADE_K_ZONE_TRADIER = True
        cfg.MOMENTUM_FADE_SCORE_BONUS_TRADIER = 5
        vector_score, _, vector_reasons = entry_score_arrays(npz, n, "SYNTH", is_long, cfg)
        strategy = tm.StockStrategy.__new__(tm.StockStrategy)
        strategy.config = cfg
        strategy.trade_manager = _Tradeability("SYNTH", is_long)
        indicators = {key: (np.asarray(value)[bar].item() if hasattr(np.asarray(value)[bar], "item") else np.asarray(value)[bar]) for key, value in npz.items() if np.asarray(value).ndim == 1 and len(np.asarray(value)) == n}
        cfg_token = tm._TR_SCORE_CFG_CTX.set(cfg)
        ts_token = tm._TR_SCORE_TS_CTX.set(float(npz["timestamps"][bar]))
        try:
            scalar_score, _, scalar_reason = strategy.calculate_signal_score(
                "v12", "SYNTH", is_long, float(indicators["close"]), indicators, None, is_exit=False
            )
        finally:
            tm._TR_SCORE_TS_CTX.reset(ts_token)
            tm._TR_SCORE_CFG_CTX.reset(cfg_token)
        assert vector_score[bar] == scalar_score
        assert "MOM_FADE" in scalar_reason
        assert "MOM_FADE" in vector_reasons[bar]
        assert vector_reasons[bar].endswith(scalar_reason)


def test_vector_golden_angles_score_matches_native_scalar_oracle_both_sides():
    import tradier_manage as tm

    for is_long in (True, False):
        npz = _base_npz()
        n = len(npz["close"])
        fire_bar = 201
        for key in ("k_15m", "d_15m", "k_5m", "d_5m", "k_5m_prev", "k_4h", "d_4h", "mfi_4h"):
            npz[key] = np.full(n, 50.0)
        if is_long:
            npz["k_15m"][fire_bar] = 20.0
            npz["d_15m"][fire_bar] = 30.0
            npz["mfi_4h"][fire_bar] = 20.0
            npz.update({key: np.full(n, value) for key, value in (("wt1_4h", 0.0), ("wt2_4h", 0.0), ("wt1_15m", 0.0), ("wt2_15m", 0.0), ("dc_position_1h", 0.5), ("wt_velocity_1h", 0.0))})
            npz["wt1_4h"][fire_bar] = 8.0
            npz["dc_position_1h"][fire_bar] = 0.2
            npz["wt_velocity_1h"][fire_bar] = 4.0
            npz["wt_cross_rising_1h"] = np.zeros(n, dtype=bool)
            npz["wt_cross_rising_1h"][fire_bar] = True
            npz["k_4h"][fire_bar] = 20.0
            npz["d_4h"][fire_bar] = 10.0
            npz["dc_position_1h"][fire_bar] = 0.2
            npz["dc_position_4h"] = np.full(n, 0.2)
            npz["dc_width_1h"] = np.full(n, 10.0)
            npz["dc_width_1h_prev"] = np.full(n, 5.0)
            for tf in ("5m", "15m", "1h", "4h", "D"):
                for field in ("dc_high", "dc_high_ant", "dc_low", "dc_low_ant"):
                    key = f"dc_high_{tf}_ant" if field == "dc_high_ant" else f"dc_low_{tf}_ant" if field == "dc_low_ant" else f"{field}_{tf}"
                    npz[key] = np.zeros(n)
            for tf in ("15m", "1h"):
                npz[f"dc_high_{tf}"][fire_bar] = 101.0
                npz[f"dc_high_{tf}_ant"][fire_bar] = 99.0
                npz[f"dc_low_{tf}"][fire_bar] = 97.0
                npz[f"dc_low_{tf}_ant"][fire_bar] = 98.0
        else:
            npz["k_15m"][fire_bar] = 80.0
            npz["d_15m"][fire_bar] = 85.0
            npz["mfi_4h"][fire_bar] = 80.0
            npz.update({key: np.full(n, value) for key, value in (("wt1_4h", 0.0), ("wt2_4h", 0.0), ("wt1_15m", 0.0), ("wt2_15m", 0.0), ("dc_position_1h", 0.5), ("wt_velocity_1h", 0.0))})
            npz["wt1_15m"][fire_bar] = -8.0
            npz["dc_position_1h"][fire_bar] = 0.8
            npz["wt_velocity_1h"][fire_bar] = -4.0
            npz["wt_cross_rising_1h"] = np.ones(n, dtype=bool)
            npz["wt_cross_rising_1h"][fire_bar] = False
            npz["k_4h"][fire_bar] = 80.0
            npz["d_4h"][fire_bar] = 90.0
            npz["dc_position_1h"][fire_bar] = 0.8
            npz["dc_position_4h"] = np.full(n, 0.8)
            npz["dc_width_1h"] = np.full(n, 10.0)
            npz["dc_width_1h_prev"] = np.full(n, 5.0)
            for tf in ("5m", "15m", "1h", "4h", "D"):
                for field in ("dc_high", "dc_high_ant", "dc_low", "dc_low_ant"):
                    key = f"dc_high_{tf}_ant" if field == "dc_high_ant" else f"dc_low_{tf}_ant" if field == "dc_low_ant" else f"{field}_{tf}"
                    npz[key] = np.zeros(n)
            for tf in ("15m", "1h"):
                npz[f"dc_high_{tf}"][fire_bar] = 103.0
                npz[f"dc_high_{tf}_ant"][fire_bar] = 102.0
                npz[f"dc_low_{tf}"][fire_bar] = 99.0
                npz[f"dc_low_{tf}_ant"][fire_bar] = 101.0
        cfg = _cfg()
        cfg.MODE = "tradier"
        cfg.LTF_SUB15M_TRADING_ENABLED = False
        cfg.MARKET_QUALITY_SCORE_ENABLED_TRADIER = False
        cfg.K_ZONE_ENTRY_ENABLED_TRADIER = True
        cfg.K_ZONE_LONG_THRESHOLD_TRADIER = 35.0
        cfg.K_ZONE_SHORT_THRESHOLD_TRADIER = 65.0
        cfg.K_ZONE_ENTRY_BONUS_TRADIER = 17
        cfg.BB_BREAKOUT_ENTRY_TF = "OFF"
        cfg.ENTRY_SCORE_THRESHOLD = 0.0
        cfg.WT_COMPOSITE_SCORING_ENABLED_TRADIER = False
        cfg.WT_COMPOSITE_SCORING_ENABLED = False
        cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
        vector_score, _, vector_reasons = entry_score_arrays(npz, n, "SYNTH", is_long, cfg)
        strategy = tm.StockStrategy.__new__(tm.StockStrategy)
        strategy.config = cfg
        strategy.trade_manager = _Tradeability("SYNTH", is_long)
        indicators = {key: np.asarray(value)[fire_bar].item() for key, value in npz.items() if np.asarray(value).ndim == 1 and len(np.asarray(value)) == n}
        cfg_token = tm._TR_SCORE_CFG_CTX.set(cfg)
        ts_token = tm._TR_SCORE_TS_CTX.set(float(npz["timestamps"][fire_bar]))
        try:
            scalar_score, _, scalar_reason = strategy.calculate_signal_score(
                "v12", "SYNTH", is_long, float(indicators["close"]), indicators, None, is_exit=False
            )
        finally:
            tm._TR_SCORE_TS_CTX.reset(ts_token)
            tm._TR_SCORE_CFG_CTX.reset(cfg_token)
        assert vector_score[fire_bar] == scalar_score
        assert "GA_" in scalar_reason
        assert "GA_" in vector_reasons[fire_bar]
        assert "KZone_4h(+17)" in scalar_reason
        assert "KZone_4h(+17)" in vector_reasons[fire_bar]
        assert "DC_POS_BONUS" in scalar_reason
        assert "DC_POS_BONUS" in vector_reasons[fire_bar]
        assert "DC_RETEST_" in scalar_reason
        assert "DC_RETEST_" in vector_reasons[fire_bar]
        assert scalar_reason in vector_reasons[fire_bar]


def test_vector_market_quality_injection_matches_native_scalar_score():
    npz, cfg, fire_bar = _mq_crossing_fixture(True)
    vector_score = entry_score_arrays(npz, len(npz["close"]), "SYNTH", True, cfg)[0][fire_bar]
    import tradier_manage as tm

    strategy = tm.StockStrategy.__new__(tm.StockStrategy)
    strategy.config = cfg
    strategy.trade_manager = _Tradeability("SYNTH", True)
    indicators = {key: np.asarray(value)[fire_bar].item() for key, value in npz.items() if np.asarray(value).ndim == 1 and len(np.asarray(value)) == len(npz["close"])}
    cfg_token = tm._TR_SCORE_CFG_CTX.set(cfg)
    ts_token = tm._TR_SCORE_TS_CTX.set(float(npz["timestamps"][fire_bar]))
    try:
        scalar_score, _, scalar_reason = strategy.calculate_signal_score(
            "v12", "SYNTH", True, float(indicators["close"]), indicators, None, is_exit=False
        )
    finally:
        tm._TR_SCORE_TS_CTX.reset(ts_token)
        tm._TR_SCORE_CFG_CTX.reset(cfg_token)
    assert vector_score == scalar_score
    assert "MQ_GOOD" in scalar_reason


def _native_stock_score_at(npz, cfg, bar, is_long=True):
    import tradier_manage as tm

    strategy = tm.StockStrategy.__new__(tm.StockStrategy)
    strategy.config = cfg
    strategy.trade_manager = _Tradeability("SYNTH", is_long)
    indicators = {key: np.asarray(value)[bar].item() for key, value in npz.items() if np.asarray(value).ndim == 1 and len(np.asarray(value)) == len(npz["close"])}
    cfg_token = tm._TR_SCORE_CFG_CTX.set(cfg)
    ts_token = tm._TR_SCORE_TS_CTX.set(float(npz["timestamps"][bar]))
    try:
        return strategy.calculate_signal_score("v12", "SYNTH", is_long, float(indicators["close"]), indicators, None, is_exit=False)
    finally:
        tm._TR_SCORE_TS_CTX.reset(ts_token)
        tm._TR_SCORE_CFG_CTX.reset(cfg_token)


def test_vector_stock_entry_pregate_block_reasons_match_native_scalar():
    import tradier_manage as tm

    for gate, expected_prefix in (("edc", "ENTRY_DC_TF(15m)"), ("backtest", "BV_ATR_HIGH_4h(4.00%)")):
        npz = _base_npz()
        n = len(npz["close"])
        bar = 201
        cfg = _cfg()
        cfg.ENTRY_SCORE_THRESHOLD = 10000.0
        cfg.BACKTEST_VALIDATED_GATES_TRADIER = True
        cfg.ENTRY_DC_TF = "15m" if gate == "edc" else "OFF"
        cfg.ENTRY_DC_BUFFER_PCT = 0.10
        if gate == "edc":
            npz["dc_low_15m"] = np.full(n, 102.0)
            npz["dc_high_15m"] = np.full(n, 103.0)
        else:
            npz["atr_4h"] = np.full(n, 4.0)
        _pre_adjustment, _pre_reasons, blocked = entry_pre_gate_score_vec(npz, n, True, cfg, False)
        assert blocked[bar] == expected_prefix
        scores, fires, _ = entry_score_arrays(npz, n, "SYNTH", True, cfg, np.ones(n, dtype=bool))
        native = _native_stock_score_at(npz, cfg, bar, True)
        assert scores[bar] == native[0] == 0.0
        assert not fires[bar]
        token = tm._TR_SCORE_PRE_CTX.set((0.0, [], blocked[bar]))
        try:
            injected = _native_stock_score_at(npz, cfg, bar, True)
        finally:
            tm._TR_SCORE_PRE_CTX.reset(token)
        assert injected[2] == native[2] == expected_prefix


def test_vector_stock_entry_pregate_penalty_order_matches_native_scalar():
    npz = _base_npz()
    n = len(npz["close"])
    bar = 201
    npz["adx_4h"] = np.full(n, 30.0)
    npz["bb_upper_4h"] = np.full(n, 106.0)
    npz["bb_lower_4h"] = np.full(n, 94.0)
    npz["choppiness_4h"] = np.full(n, 55.0)
    npz["atr_4h"] = np.full(n, 1.0)
    npz["atr_1h"] = np.full(n, 0.5)
    npz["mfi_4h"] = np.full(n, 50.0)
    npz["wt1_1h"] = np.zeros(n)
    npz["wt1_4h"] = np.zeros(n)
    npz["k_4h"] = np.full(n, 50.0)
    npz["dc_width_1h"] = np.full(n, 5.0)
    npz["dc_width_4h"] = np.full(n, 5.0)
    npz["dc_position_4h"] = np.full(n, 0.5)
    npz["wt_velocity_1h"] = np.zeros(n)
    cfg = _cfg()
    cfg.ENTRY_SCORE_THRESHOLD = -10000.0
    cfg.BACKTEST_VALIDATED_GATES_TRADIER = True
    cfg.ENTRY_DC_TF = "OFF"
    cfg.CT_WT_VELOCITY_GATE_ENABLED = False
    cfg.ENTRY_ATR_PCT_MIN = 0.0
    cfg.ENTRY_VOL_MIN_RATIO = 0.0
    cfg.TR_ADX4H_GATE_ENABLED = True
    cfg.TR_ADX4H_MAX = 20.0
    cfg.TR_ADX4H_BOYCOTT_SCORE = -40.0
    cfg.TR_BBWIDTH4H_GATE_ENABLED = True
    cfg.TR_BBWIDTH4H_MAX = 10.0
    cfg.TR_BBWIDTH4H_BOYCOTT_SCORE = -35.0
    cfg.TR_CHOP4H_GATE_ENABLED = True
    cfg.TR_CHOP4H_MIN = 50.0
    cfg.TR_CHOP4H_BONUS = 15.0
    cfg.TR_CHOP4H_TREND_MAX = 38.0
    cfg.TR_CHOP4H_PENALTY = -20.0
    cfg.TR_MFI4H_LONG_ENABLED = False
    cfg.TR_DCWIDTH4H_SHORT_ENABLED = False
    vector_scores, _, _ = entry_score_arrays(npz, n, "SYNTH", True, cfg, np.ones(n, dtype=bool))
    native_score, _, _ = _native_stock_score_at(npz, cfg, bar, True)
    adjustment, pre_reasons, blocked = entry_pre_gate_score_vec(npz, n, True, cfg, False)
    assert vector_scores[bar] == native_score
    assert adjustment[bar] == -60.0
    assert blocked[bar] is None
    assert pre_reasons[bar] == [
        "TR_ADX4H_TREND(30>20,-40)",
        "TR_BBW4H_WIDE(12.0%>10.0,-35)",
        "TR_CHOP4H_OK(55,+15)",
    ]



def test_vector_mts_scores_match_native_analyzer_both_sides():
    from tradier_indicators import analyze_multi_tf_state_tradier

    npz = _base_npz()
    n = len(npz["close"])
    rng = np.random.default_rng(194)
    cfg = _cfg()
    cfg.MODE = "tradier"
    for tf in ("5m", "15m", "1h", "4h", "D"):
        for key, values in {
            f"k_{tf}": rng.uniform(0.0, 100.0, n),
            f"d_{tf}": rng.uniform(0.0, 100.0, n),
            f"k_{tf}_prev": rng.uniform(0.0, 100.0, n),
            f"wt1_{tf}": rng.uniform(-80.0, 80.0, n),
            f"wt2_{tf}": rng.uniform(-80.0, 80.0, n),
            f"wt_velocity_{tf}": rng.uniform(-8.0, 8.0, n),
            f"wt_cross_value_{tf}": rng.uniform(-60.0, 60.0, n),
            f"wt_cross_prev_value_{tf}": rng.uniform(-60.0, 60.0, n),
            f"wt_cross_bars_ago_{tf}": rng.integers(0, 20, n),
            f"dc_position_{tf}": rng.uniform(0.0, 1.0, n),
            f"dc_width_{tf}": rng.uniform(0.5, 8.0, n),
        }.items():
            npz[key] = values
        npz[f"wt_cross_{tf}"] = rng.choice(["BULL", "BEAR", ""], n)
        npz[f"wt_cross_rising_{tf}"] = rng.choice([True, False], n)
        npz[f"wt_divergence_{tf}"] = rng.choice(["BULL", "HIDDEN_BULL", "BEAR", "HIDDEN_BEAR", ""], n)
        npz[f"wt_momentum_state_{tf}"] = rng.choice(["EXHAUST_UP", "EXHAUST_DOWN", ""], n)
    for is_long in (True, False):
        adjustments, reasons, blocked, bottoms, entry_quality = mts_score_vec(npz, n, is_long, cfg)
        assert all(item is None for item in blocked)
        for index in range(n):
            indicators = {key: np.asarray(value)[index].item() for key, value in npz.items() if np.asarray(value).ndim == 1 and len(np.asarray(value)) == n}
            indicators = paired_lower_tf_indicators(indicators, bool(cfg.LTF_SUB15M_TRADING_ENABLED))
            native = analyze_multi_tf_state_tradier(indicators, is_long, float(indicators["close"]), cfg)
            assert bottoms[index] == native["bottom_score"]
            assert entry_quality[index] == native["entry_quality"]
            expected_adjustment = (8.0 if bottoms[index] > 40 else 4.0 if bottoms[index] > 25 else 0.0) + (5.0 if entry_quality[index] > 40 else 2.0 if entry_quality[index] > 25 else 0.0)
            assert adjustments[index] == expected_adjustment
            expected_reason = []
            if bottoms[index] > 40: expected_reason.append(f"MTS_STRONG({bottoms[index]:.0f},+8)")
            elif bottoms[index] > 25: expected_reason.append(f"MTS_BOTTOM({bottoms[index]:.0f},+4)")
            if entry_quality[index] > 40: expected_reason.append(f"MTS_EQ({entry_quality[index]:.0f},+5)")
            elif entry_quality[index] > 25: expected_reason.append(f"MTS_EQ_OK({entry_quality[index]:.0f},+2)")
            assert reasons[index] == expected_reason


def test_vector_mts_gate_block_matches_native_scalar():
    import tradier_manage as tm

    npz = _base_npz()
    n = len(npz["close"])
    bar = 201
    cfg = _cfg()
    cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
    cfg.MTS_GATE_ENABLED_TRADIER = True
    cfg.MTS_BOTTOM_MIN_TRADIER = 100.0
    cfg.MTS_ENTRY_QUALITY_MIN_TRADIER = 0.0
    cfg.ENTRY_SCORE_THRESHOLD = 10000.0
    adjustments, reasons, blocked, bottoms, entry_quality = mts_score_vec(npz, n, True, cfg)
    assert blocked[bar] is not None
    token_cfg = tm._TR_SCORE_CFG_CTX.set(cfg)
    token_ts = tm._TR_SCORE_TS_CTX.set(float(npz["timestamps"][bar]))
    token_mts = tm._TR_SCORE_MTS_CTX.set((float(adjustments[bar]), reasons[bar], blocked[bar], float(bottoms[bar]), float(entry_quality[bar])))
    try:
        strategy = tm.StockStrategy.__new__(tm.StockStrategy)
        strategy.config = cfg
        strategy.trade_manager = _Tradeability("SYNTH", True)
        indicators = {key: np.asarray(value)[bar].item() for key, value in npz.items() if np.asarray(value).ndim == 1 and len(np.asarray(value)) == n}
        injected = strategy.calculate_signal_score("v12", "SYNTH", True, float(indicators["close"]), indicators, None, is_exit=False)
    finally:
        tm._TR_SCORE_MTS_CTX.reset(token_mts)
        tm._TR_SCORE_TS_CTX.reset(token_ts)
        tm._TR_SCORE_CFG_CTX.reset(token_cfg)
    assert injected == (0.0, "WAIT", blocked[bar])



def test_vector_stock_good_setup_mask_matches_native_setup_return(monkeypatch):
    import tradier_manage as tm

    monkeypatch.setattr(tm, "is_regular_trading_hours", lambda: True)
    npz = _base_npz()
    n = len(npz["close"])
    rng = np.random.default_rng(87)
    for key, values in {
        "k_1m": rng.uniform(0.0, 100.0, n),
        "d_1m": rng.uniform(0.0, 100.0, n),
        "k_1m_prev": rng.uniform(0.0, 100.0, n),
        "k_15m": rng.uniform(0.0, 100.0, n),
        "d_15m": rng.uniform(0.0, 100.0, n),
        "k_15m_prev": rng.uniform(0.0, 100.0, n),
        "k_5m": rng.uniform(0.0, 100.0, n),
        "d_5m": rng.uniform(0.0, 100.0, n),
        "k_5m_prev": rng.uniform(0.0, 100.0, n),
        "k_4h": rng.uniform(0.0, 100.0, n),
        "d_4h": rng.uniform(0.0, 100.0, n),
        "mfi_4h": rng.uniform(0.0, 100.0, n),
        "rsi_1h": rng.uniform(0.0, 100.0, n),
        "bb_pct_b_15m": rng.uniform(0.0, 1.0, n),
        "bb_upper_15m": np.full(n, 102.0),
        "bb_lower_15m": np.full(n, 98.0),
        "timestamp_1m": np.asarray(npz["timestamps"], dtype=float) - rng.uniform(-10.0, 120.0, n),
    }.items():
        npz[key] = values
    for is_long in (True, False):
        cfg = _cfg()
        cfg.MODE = "tradier"
        cfg.LTF_SUB15M_TRADING_ENABLED = False
        cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
        cfg.ENTRY_DC_TF = "OFF"
        cfg.WT_COMPOSITE_SCORING_ENABLED_TRADIER = False
        cfg.WT_COMPOSITE_SCORING_ENABLED = False
        cfg.BB_BREAKOUT_ENTRY_TF = "15m"
        cfg.MTS_GATE_ENABLED_TRADIER = False
        cfg.EOD_RATIO_ENFORCE_TRADIER = False
        cfg.FG_FEAR_THRESHOLD = 35.0
        cfg.FG_GREED_THRESHOLD = 70.0
        masks, _, _, _, _ = native_entry_setup_masks(npz, n, is_long, cfg)
        strategy = tm.StockStrategy.__new__(tm.StockStrategy)
        strategy.config = cfg
        strategy.trade_manager = _Tradeability("SYNTH", is_long)
        cfg_token = tm._TR_SCORE_CFG_CTX.set(cfg)
        try:
            for index in range(n):
                indicators = {key: np.asarray(value)[index].item() for key, value in npz.items() if np.asarray(value).ndim == 1 and len(np.asarray(value)) == n}
                _, _, reason = strategy.calculate_signal_score("v12", "SYNTH", is_long, float(indicators["close"]), indicators, None, is_exit=False)
                assert bool(masks[index]) == (reason != f"NO_{'LONG' if is_long else 'SHORT'}_SETUP")
        finally:
            tm._TR_SCORE_CFG_CTX.reset(cfg_token)

def test_vector_entry_tail_matches_bear_and_eod_thresholds():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    npz = _base_npz()
    n = len(npz["close"])
    et = ZoneInfo("America/New_York")
    moments = [datetime(2026, 10, 29, 15, minute, tzinfo=et).timestamp() for minute in (30, 45, 56)]
    npz["timestamps"][:3] = moments
    cfg = _cfg()
    cfg.BEAR_MARKET_MODE_TRADIER = True
    cfg.EOD_RATIO_ENFORCE_TRADIER = True
    long_adjustments, long_reasons, long_blocked = entry_tail_score_vec(npz, n, True, cfg)
    assert long_adjustments[:3].tolist() == [-20.0, -25.0, -28.0]
    assert long_reasons[0] == ["BEAR_PENALTY", "EOD_FADE(30min)"]
    assert long_reasons[1] == ["BEAR_PENALTY", "EOD_FADE(15min)"]
    assert long_blocked[0] is None and long_blocked[1] is None
    assert long_blocked[2] == "EOD_BLOCK(4min_to_close)"
    short_adjustments, short_reasons, _ = entry_tail_score_vec(npz, n, False, cfg)
    assert short_adjustments[:2].tolist() == [15.0, 10.0]
    assert short_reasons[0] == ["BEAR_BONUS", "EOD_FADE(30min)"]
    assert short_reasons[1] == ["BEAR_BONUS", "EOD_FADE(15min)"]


def test_vector_entry_eod_block_reaches_native_entry_score_context():
    import tradier_manage as tm
    from datetime import datetime
    from zoneinfo import ZoneInfo

    npz = _base_npz()
    n = len(npz["close"])
    bar = 201
    npz["timestamps"][bar] = datetime(2026, 10, 29, 15, 56, tzinfo=ZoneInfo("America/New_York")).timestamp()
    npz["k_15m"] = np.full(n, 10.0)
    npz["d_15m"] = np.full(n, 30.0)
    npz["k_5m"] = np.full(n, 10.0)
    npz["d_5m"] = np.full(n, 30.0)
    cfg = _cfg()
    cfg.BACKTEST_VALIDATED_GATES_TRADIER = False
    cfg.WT_COMPOSITE_SCORING_ENABLED_TRADIER = False
    cfg.WT_COMPOSITE_SCORING_ENABLED = False
    cfg.EOD_RATIO_ENFORCE_TRADIER = True
    cfg.ENTRY_SCORE_THRESHOLD = 10000.0
    adjustment, reasons, blocked = entry_tail_score_vec(npz, n, True, cfg)
    assert blocked[bar] == "EOD_BLOCK(4min_to_close)"
    token_cfg = tm._TR_SCORE_CFG_CTX.set(cfg)
    token_ts = tm._TR_SCORE_TS_CTX.set(float(npz["timestamps"][bar]))
    token_tail = tm._TR_SCORE_TAIL_CTX.set((float(adjustment[bar]), reasons[bar], blocked[bar]))
    try:
        strategy = tm.StockStrategy.__new__(tm.StockStrategy)
        strategy.config = cfg
        strategy.trade_manager = _Tradeability("SYNTH", True)
        indicators = {key: np.asarray(value)[bar].item() for key, value in npz.items() if np.asarray(value).ndim == 1 and len(np.asarray(value)) == n}
        injected = strategy.calculate_signal_score("v12", "SYNTH", True, float(indicators["close"]), indicators, None, is_exit=False)
    finally:
        tm._TR_SCORE_TAIL_CTX.reset(token_tail)
        tm._TR_SCORE_TS_CTX.reset(token_ts)
        tm._TR_SCORE_CFG_CTX.reset(token_cfg)
    assert injected == (0.0, "WAIT", blocked[bar])

def test_native_score_fallback_changes_simulate_one_open_bar_with_market_quality(monkeypatch):
    monkeypatch.setenv("V8_RATE_GUARD_DISABLED", "1")
    off_npz, off_cfg, fire_bar = _mq_crossing_fixture(False)
    on_npz, on_cfg, _ = _mq_crossing_fixture(True)
    off = V.simulate_one(off_npz, "SYNTH", True, off_cfg)
    on = V.simulate_one(on_npz, "SYNTH", True, on_cfg)
    off_opens = [row for row in off["ledger"] if row.get("type") == "OPEN"]
    on_opens = [row for row in on["ledger"] if row.get("type") == "OPEN"]
    assert off_opens == []
    assert len(on_opens) == 1
    assert on_opens[0]["bar"] == fire_bar
    assert "MQ_GOOD" in on_opens[0]["reason"]

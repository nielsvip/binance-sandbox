import sys

import numpy as np

sys.path.insert(0, "tests")

import v12_quick_engine as V
from test_tradier_reviewed_exit_ledgers import _base_npz, _cfg
from vec_decisions.tradier_signal_score import _Tradeability, entry_score_arrays, paired_lower_tf_indicators, possible_entry_setup_mask


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

from types import SimpleNamespace

import numpy as np

from golden_rule_htf import _ind_score
from vec_decisions.wt_force_open_stock import force_open_mask


def _cfg(**overrides):
    values = {
        "MODE": "tradier",
        "LTF_SUB15M_TRADING_ENABLED": True,
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



def test_sub15m_master_off_blocks_stock_force_open():
    n = 1
    data = {
        "ema_200_15m": np.asarray([90.0]),
        "wt1_5m": np.asarray([2.0]),
        "wt2_5m": np.asarray([1.0]),
    }
    cfg = _cfg(LTF_SUB15M_TRADING_ENABLED=False, WT_3M_FORCE_OPEN_ENABLED=True)
    assert not force_open_mask(data, n, True, cfg, np.asarray([102.0])).any()


def test_tradier_entry_masks_calls_stock_force_open_provider():
    from vec_decisions.tradeable_mandatory_entry import entry_masks

    data = {
        "ema_200_15m": np.asarray([90.0, 200.0]),
        "wt1_5m": np.asarray([2.0, 2.0]),
        "wt2_5m": np.asarray([1.0, 1.0]),
    }
    cfg = _cfg(WT_3M_FORCE_OPEN_GR_GATE_ENABLED=False, WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS=False)
    _, force = entry_masks(data, 2, True, cfg, np.asarray([102.0, 102.0]))
    np.testing.assert_array_equal(force, [True, False])


def test_native_force_open_stateful_config_zero_and_identity_semantics():
    from vec_decisions.wt_force_open_stock import stateful_fire

    data = {
        "timestamps": np.asarray([1699972200.0]),
        "wt1_15m": np.asarray([1.0]),
        "wt2_15m": np.asarray([0.0]),
        "wt1_1h": np.asarray([1.0]),
        "wt2_1h": np.asarray([0.0]),
        "wt1_4h": np.asarray([0.0]),
        "wt2_4h": np.asarray([0.0]),
        "wt1_D": np.asarray([0.0]),
        "wt2_D": np.asarray([0.0]),
    }
    state = {"last_ts": None, "day": None, "count": 0}
    assert stateful_fire(
        data, 0, True, _cfg(WT_3M_FORCE_OPEN_BUILD_TO_TARGET=True, WT_3M_FORCE_OPEN_TARGET_USD=0.0),
        100.0, 0.0, state, float(data["timestamps"][0]),
    ) is None
    open_without_build_target = stateful_fire(
        data, 0, True, _cfg(
            WT_3M_FORCE_OPEN_BUILD_TO_TARGET=False,
            WT_3M_FORCE_OPEN_TARGET_USD=0.0,
            WT_3M_FORCE_OPEN_SIZE_USD=10.0,
            WT_3M_FORCE_OPEN_TF_LADDER=False,
        ),
        100.0, 0.0, state, float(data["timestamps"][0]),
    )
    assert open_without_build_target["size_usd"] == 10.0
    open_zero_target_without_build = stateful_fire(
        data, 0, True, _cfg(
            WT_3M_FORCE_OPEN_BUILD_TO_TARGET=False,
            WT_3M_FORCE_OPEN_TARGET_USD=0.0,
            WT_3M_FORCE_OPEN_SIZE_USD=10.0,
            WT_3M_FORCE_OPEN_TF_LADDER=False,
        ),
        100.0, 0.0, state, float(data["timestamps"][0]),
    )
    assert open_zero_target_without_build["size_usd"] == 10.0
    open_negative_target_without_build = stateful_fire(
        data, 0, True, _cfg(
            WT_3M_FORCE_OPEN_BUILD_TO_TARGET=False,
            WT_3M_FORCE_OPEN_TARGET_USD=-5.0,
            WT_3M_FORCE_OPEN_SIZE_USD=10.0,
            WT_3M_FORCE_OPEN_TF_LADDER=False,
        ),
        100.0, 0.0, state, float(data["timestamps"][0]),
    )
    assert open_negative_target_without_build["size_usd"] == 10.0
    assert stateful_fire(
        data, 0, True, _cfg(
            WT_3M_FORCE_OPEN_BUILD_TO_TARGET=False,
            WT_3M_FORCE_OPEN_TARGET_USD=1000.0,
            WT_3M_FORCE_OPEN_SIZE_USD=10.0,
            WT_3M_FORCE_OPEN_TF_LADDER=False,
        ),
        100.0, 1.0, state, float(data["timestamps"][0]),
    ) is None
    assert stateful_fire(
        data, 0, True, _cfg(
            WT_3M_FORCE_OPEN_BUILD_TO_TARGET=False,
            WT_3M_FORCE_OPEN_SIZE_USD=0.0,
            WT_3M_FORCE_OPEN_TF_LADDER=False,
        ),
        100.0, 0.0, state, float(data["timestamps"][0]),
    ) is None
    zero_ladder_mult = stateful_fire(
        data, 0, True, _cfg(
            WT_3M_FORCE_OPEN_BUILD_TO_TARGET=False,
            WT_3M_FORCE_OPEN_SIZE_USD=10.0,
            WT_3M_FORCE_OPEN_TF_LADDER=True,
            WT_3M_FORCE_OPEN_TF_LADDER_MULT=0.0,
        ),
        100.0, 0.0, state, float(data["timestamps"][0]),
    )
    assert zero_ladder_mult["size_usd"] == 10.0
    data["wt_cross_bars_ago_5m"] = np.asarray([1.0])
    data["wt_cross_rising_5m"] = np.asarray([np.bool_(True)], dtype=object)
    data["wt_cross_bull_5m"] = np.asarray([1.0])
    data["ema_200_15m"] = np.asarray([90.0])
    data["wt1_5m"] = np.asarray([2.0])
    data["wt2_5m"] = np.asarray([1.0])
    assert not force_open_mask(
        data,
        1,
        True,
        _cfg(
            WT_FORCE_OPEN_FRESH_CROSS_ONLY=True,
            WT_FORCE_OPEN_FRESH_MAX_BARS=2,
            WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS=False,
            WT_3M_FORCE_OPEN_TF_LADDER=False,
        ),
        np.asarray([100.0]),
    )[0]


def test_stock_force_open_simulate_one_open_matches_selected_bar():
    import v12_quick_engine as engine
    from tests.test_quick_srs_paired_15m import _cfg as quick_cfg, _npz
    from vec_decisions.generic_filter_tf import FILTER_TF_MAP

    data = _npz()
    n = len(data["timestamps"])
    data["timestamps"] = 1699972200.0 + np.arange(n) * 900.0
    cfg = quick_cfg()
    cfg.MODE = "tradier"
    cfg.LTF_SUB15M_TRADING_ENABLED = True
    cfg.WT_3M_FORCE_OPEN_ENABLED = True
    cfg.WT_3M_FORCE_OPEN_GR_GATE_ENABLED = False
    cfg.WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS = False
    cfg.WT_3M_FORCE_OPEN_BUILD_TO_TARGET = False
    cfg.WT_3M_FORCE_OPEN_BYPASS_GATES = True
    cfg.GR_FILTER_ALL_ENTRIES = False
    # Keep the native generic GR consensus gate off in this route fixture;
    # the producer's own optional force-open GR gate is disabled above.
    cfg.GOLDEN_RULE_HTF_MIN_TFS = 0
    cfg.STOCKS_LIVE_ENTRY_STACK_ENABLED = False
    cfg.ABLATION_DISABLE_QUICK_ENTRY = True
    cfg.STRUCTURAL_RANGE_SHIFT_EXIT = False
    cfg.OPTIMAL_HOLD_BARS_EXIT_ENABLED = False
    cfg.STDEV_REJECT_EXIT_ENABLED = False
    cfg.RULE_B_5M_EXIT_ENABLED = False
    cfg.EMA50_15M_ENTRY_FILTER_ENABLED = False
    for name, (target, _) in FILTER_TF_MAP.items():
        if target == "entry":
            setattr(cfg, name, "OFF")
    data["ema_200_15m"] = np.full(n, 200.0)
    data["ema_200_15m"][2] = 90.0
    data["dc_high_15m"][2] = 99.0
    data["wt1_5m"] = np.full(n, 2.0)
    data["wt2_5m"] = np.zeros(n)
    result = engine.simulate_one(data, "WT_STOCK_FORCE_TEST", True, cfg)
    opens = [row for row in result["ledger"] if row.get("type") == "OPEN"]
    assert opens and opens[0]["bar"] == 2
    assert "WT_3M_FORCE_OPEN" in opens[0]["reason"]


def test_stock_force_open_simulate_one_builds_position_with_paced_augment():
    import v12_quick_engine as engine
    from tests.test_quick_srs_paired_15m import _cfg as quick_cfg, _npz
    from vec_decisions.generic_filter_tf import FILTER_TF_MAP

    data = _npz()
    n = len(data["timestamps"])
    data["timestamps"] = 1699972200.0 + np.arange(n) * 900.0
    cfg = quick_cfg()
    cfg.MODE = "tradier"
    cfg.LTF_SUB15M_TRADING_ENABLED = True
    cfg.WT_3M_FORCE_OPEN_ENABLED = True
    cfg.WT_3M_FORCE_OPEN_GR_GATE_ENABLED = False
    cfg.WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS = False
    cfg.WT_3M_FORCE_OPEN_BUILD_TO_TARGET = True
    cfg.WT_3M_FORCE_OPEN_BYPASS_GATES = True
    cfg.WT_3M_FORCE_OPEN_SIZE_USD = 100.0
    cfg.WT_3M_FORCE_OPEN_TARGET_USD = 1000.0
    cfg.DG_MAX_FORCE_OPEN_NOTIONAL_USD = 2000.0
    cfg.GR_FILTER_ALL_ENTRIES = False
    cfg.GOLDEN_RULE_HTF_MIN_TFS = 0
    cfg.STOCKS_LIVE_ENTRY_STACK_ENABLED = False
    cfg.ABLATION_DISABLE_QUICK_ENTRY = True
    cfg.STRUCTURAL_RANGE_SHIFT_EXIT = False
    cfg.OPTIMAL_HOLD_BARS_EXIT_ENABLED = False
    cfg.STDEV_REJECT_EXIT_ENABLED = False
    cfg.RULE_B_5M_EXIT_ENABLED = False
    cfg.EMA50_15M_ENTRY_FILTER_ENABLED = False
    for name, (target, _) in FILTER_TF_MAP.items():
        if target == "entry":
            setattr(cfg, name, "OFF")
    data["ema_200_15m"] = np.full(n, 200.0)
    data["ema_200_15m"][2:] = 90.0
    data["wt1_5m"] = np.full(n, 2.0)
    data["wt2_5m"] = np.zeros(n)
    result = engine.simulate_one(data, "WT_STOCK_FORCE_TEST", True, cfg)
    actions = [
        row for row in result["ledger"]
        if row.get("type") in ("OPEN", "AUGMENT")
        and "WT_3M_FORCE_OPEN" in str(row.get("reason", ""))
    ]
    assert [(row["type"], row["bar"]) for row in actions[:2]] == [
        ("OPEN", 2),
        ("AUGMENT", 3),
    ]
    cfg.WT_3M_FORCE_OPEN_MAX_TRADES_PER_DAY = 1
    capped = engine.simulate_one(data, "WT_STOCK_FORCE_TEST", True, cfg)
    capped_actions = [
        row for row in capped["ledger"]
        if row.get("type") in ("OPEN", "AUGMENT")
        and "WT_3M_FORCE_OPEN" in str(row.get("reason", ""))
    ]
    assert [(row["type"], row["bar"]) for row in capped_actions] == [
        ("OPEN", 2),
        ("AUGMENT", 97),
    ]


def test_force_open_pacing_records_queue_acceptance_even_if_fill_fails_later():
    from vec_decisions.wt_force_open_stock import commit_state, stateful_fire

    timestamps = np.asarray([1699972200.0, 1699972201.0])
    data = {"timestamps": timestamps}
    cfg = _cfg(
        WT_3M_FORCE_OPEN_BUILD_TO_TARGET=False,
        WT_3M_FORCE_OPEN_SIZE_USD=10.0,
        WT_3M_FORCE_OPEN_TF_LADDER=False,
        WT_3M_FORCE_OPEN_COOLDOWN_SEC=900.0,
    )
    state = {"last_ts": None, "day": None, "count": 0}
    decision = stateful_fire(data, 0, True, cfg, 100.0, 0.0, state, timestamps[0])
    assert decision is not None

    # queue_trade_action returned SUCCESS, so native pacing is committed before
    # downstream broker execution; a later rejected/unfilled order does not undo it.
    queue_success = True
    execution_filled = False
    if queue_success:
        commit_state(state, decision, timestamps[0])
    assert not execution_filled
    assert state == {"last_ts": timestamps[0], "day": int(timestamps[0] // 86400), "count": 1}
    assert stateful_fire(data, 1, True, cfg, 100.0, 0.0, state, timestamps[1]) is None

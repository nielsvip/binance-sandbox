from types import SimpleNamespace

import numpy as np

from vec_paths.v12_exit_reduce_gap_batch9 import consume_lifecycle_actions, evaluate_gap_batch9


def _data(n=5, tf="1h"):
    data = {
        f"open_{tf}": np.array([100, 100, 101, 102, 102.0]),
        f"high_{tf}_prev": np.array([10, 9, 9, 8, 7.0]),
        f"low_{tf}_prev": np.array([8, 7, 7, 6, 5.0]),
        "bb_pct_b_D": np.array([1.1, .9, 1.1, .9, .8]),
        "bb_pct_b_D_prev": np.array([.9, 1.1, .9, 1.1, .9]),
        "wt_velocity_1h": np.array([-1, -1, 1, 1, -1.0]),
    }
    for tf_name in ("5m", "15m", "1h", "4h", "D"):
        data[f"wt1_{tf_name}"] = np.zeros(n)
        data[f"wt2_{tf_name}"] = np.ones(n)
    return data


def _cfg(**updates):
    values = dict(
        OPENING_BUFFER_NO_CLOSE_MINUTES=30.0,
        MARKET_CLOSE_HOUR=16,
        MARKET_CLOSE_MINUTE=0,
        DC_DAYTRADE_PRE_CLOSE_MINUTES=120,
        EXIT_STRUCT_TF="None",
        LONG_STRUCT_EXIT_TF="1h",
        SHORT_STRUCT_EXIT_TF="1h",
        MTF_EXIT_MIN_OPEN_TS=100.0,
        MTF_EXIT_USE_COMPOUND=False,
        NOLOSS_MIN_PROFIT_PCT_TRADIER=3.0,
        NOLOSS_BYPASS_WT_5OF5_ENABLED=False,
        NOLOSS_BYPASS_WT_5OF5_MIN_TFS=5,
        STDEV_BB_RZ_EXIT_ENABLED=False,
        STDEV_BB_RZ_EXIT_TF="D",
        TRADIER_POST_CLOSE_COOLDOWN_MIN=15.0,
        DC_RECOVERY_EXIT_DISABLED_ACCOUNTS=["inf"],
    )
    values.update(updates)
    return SimpleNamespace(**values)


def _run(cfg=None, data=None, side="LONG", **state):
    return evaluate_gap_batch9(data or _data(), cfg or _cfg(), position_side=side, **state)


def test_opening_buffer_weekday_boundaries_and_e02_exit_exception():
    result = _run(
        weekday_et=np.array([0, 4, 5, 1, 1]),
        minutes_since_open_et=np.array([0, 29.999, 10, -1, 30]),
        opening_buffer_exit_bypass=np.array([0, 1, 0, 0, 0]),
    )
    assert result.masks["opening_buffer_block"].tolist() == [True, True, False, False, False]
    assert result.masks["opening_buffer_exit_block"].tolist() == [True, False, False, False, False]
    actions = consume_lifecycle_actions(result).masks
    assert actions["ENTRY_FILTER_BLOCK"].tolist()[:2] == [True, True]
    assert actions["EXIT_FILTER_BLOCK"].tolist()[:2] == [True, False]


def test_market_close_fields_own_exact_preclose_window_not_portfolio_selection():
    result = _run(
        current_et_minutes=np.array([839, 840, 900, 959, 960]),
        regular_trading_session=True,
        daytrade_preclose_selected=np.array([1, 1, 0, 1, 1]),
    )
    assert result.values["minutes_to_market_close"].tolist() == [121, 120, 60, 1, 0]
    assert result.masks["daytrade_preclose_window"].tolist() == [False, True, True, True, False]
    assert result.masks["daytrade_preclose_exit"].tolist() == [False, True, False, True, False]
    shifted = _run(_cfg(MARKET_CLOSE_HOUR=15, MARKET_CLOSE_MINUTE=30), current_et_minutes=900, daytrade_preclose_selected=True)
    assert shifted.values["minutes_to_market_close"].tolist() == [30] * 5


def test_side_structural_fallback_is_stateful_and_common_tf_takes_precedence():
    result = _run()
    assert result.masks["side_structural_exit"].tolist() == [False, False, True, True, False]
    common = _run(_cfg(EXIT_STRUCT_TF="4h"))
    assert not common.masks["side_structural_exit"].any()
    short_data = _data()
    short_data["high_1h_prev"] = np.array([10, 11, 11, 12, 12.0])
    short_data["low_1h_prev"] = np.array([8, 9, 9, 10, 10.0])
    short = _run(data=short_data, side="SHORT")
    assert short.masks["side_structural_exit"].tolist() == [False, False, True, True, False]


def test_selected_structural_tf_missing_native_arrays_fails_closed():
    data = _data()
    data.pop("low_1h_prev")
    result = _run(data=data)
    assert not result.masks["side_structural_exit"].any()
    assert result.missing_arrays["side_structural_exit"] == ("low_1h_prev",)


def test_mtf_cutoff_uses_config_or_startup_and_shared_strict_eligibility():
    result = _run(
        _cfg(MTF_EXIT_USE_COMPOUND=True, MTF_EXIT_MIN_OPEN_TS=100),
        position_open_ts=np.array([0, 99, 100, 101, 200]),
    )
    assert result.masks["mtf_compound_position_eligible"].tolist() == [False, False, True, True, True]
    startup = _run(
        _cfg(MTF_EXIT_USE_COMPOUND=True, MTF_EXIT_MIN_OPEN_TS=0),
        position_open_ts=np.array([100, 101, 102, 103, 104]),
        mtf_startup_ts=np.array([101, 101, 103, 103, 105]),
    )
    assert startup.masks["mtf_compound_position_eligible"].tolist() == [False, True, False, True, False]


def test_noloss_min_tf_count_and_short_mirror_are_exact():
    cfg = _cfg(NOLOSS_BYPASS_WT_5OF5_ENABLED=True, NOLOSS_BYPASS_WT_5OF5_MIN_TFS=4)
    data = _data()
    data["wt1_D"][:] = 2
    data["wt2_D"][:] = 1
    result = _run(cfg, data, gain_pct=0, strict_no_loss_account=True)
    assert result.values["noloss_adverse_wt_tf_count"].tolist() == [4] * 5
    assert result.masks["noloss_bypass_wt_5of5"].all()
    assert not result.masks["noloss_exit_filter_block"].any()
    for tf in ("5m", "15m", "1h", "4h"):
        data[f"wt1_{tf}"][:] = 2
        data[f"wt2_{tf}"][:] = 1
    data["wt1_D"][:] = 0
    data["wt2_D"][:] = 1
    short = _run(cfg, data, side="SHORT", gain_pct=0, strict_no_loss_account=True)
    assert short.values["noloss_adverse_wt_tf_count"].tolist() == [4] * 5
    assert short.masks["noloss_bypass_wt_5of5"].all()


def test_noloss_missing_native_timeframe_fails_bypass_closed_and_reports():
    data = _data()
    data.pop("wt2_4h")
    result = _run(_cfg(NOLOSS_BYPASS_WT_5OF5_ENABLED=True), data, gain_pct=-1, strict_no_loss_account=True)
    assert not result.masks["noloss_bypass_wt_5of5"].any()
    assert result.masks["noloss_exit_filter_block"].all()
    assert result.missing_arrays["noloss_bypass_wt_5of5"] == ("wt2_4h",)


def test_stdev_selected_tf_uses_explicit_previous_and_completed_parent_tokens():
    cfg = _cfg(STDEV_BB_RZ_EXIT_ENABLED=True)
    explicit = _run(cfg)
    assert explicit.masks["stdev_bb_rz_exit"].tolist() == [False, True, False, True, False]
    data = _data()
    data.pop("bb_pct_b_D_prev")
    data["_completed_source_ts_D"] = np.array([10, 10, 20, 30, 30])
    data["bb_pct_b_D"] = np.array([1.1, 1.1, .9, 1.1, .9])
    tokenized = _run(cfg, data)
    assert tokenized.masks["stdev_bb_rz_exit"].tolist() == [False, False, True, False, False]


def test_stdev_selected_tf_missing_previous_and_token_fails_closed():
    data = _data()
    data.pop("bb_pct_b_D_prev")
    result = _run(_cfg(STDEV_BB_RZ_EXIT_ENABLED=True), data)
    assert not result.masks["stdev_bb_rz_exit"].any()
    assert "bb_pct_b_D_prev|_completed_source_ts_D|timestamp_D" in result.missing_arrays["stdev_bb_rz_exit"]


def test_post_close_cooldown_bypasses_and_dc_account_exclusion_map_to_consumers():
    result = _run(
        elapsed_since_last_reduction_minutes=np.array([0, 14.9, 15, 1, 1]),
        mandatory_reentry=np.array([0, 0, 0, 1, 0]),
        ordinary_parity_action=np.array([0, 0, 0, 0, 1]),
        account_key="inf",
        dc_recovery_exit_candidate=np.array([1, 0, 1, 0, 1]),
    )
    assert result.masks["post_close_entry_block"].tolist() == [True, True, False, False, False]
    assert result.masks["dc_recovery_exit_filter_block"].tolist() == [True, False, True, False, True]
    assert not result.masks["dc_recovery_exit"].any()
    actions = consume_lifecycle_actions(result).masks
    assert actions["HEDGE_OPEN_FILTER_BLOCK"].tolist() == [True, True, False, False, False]
    allowed = _run(account_key="trb", dc_recovery_exit_candidate=True)
    assert allowed.masks["dc_recovery_exit"].all()


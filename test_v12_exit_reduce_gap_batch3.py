from types import SimpleNamespace

import numpy as np

from vec_paths.v12_exit_reduce_gap_batch3 import (
    consume_lifecycle_actions,
    evaluate_gap_batch3,
)


def _data(n=4):
    data = {}
    for tf in ("1m", "3m", "15m", "1h", "4h", "D"):
        data[f"wt1_{tf}"] = np.full(n, -2.0)
        data[f"wt2_{tf}"] = np.zeros(n)
        data[f"wt_velocity_{tf}"] = np.full(n, -1.0)
    for tf in ("3m", "15m", "1h"):
        data[f"stoch_k_{tf}"] = np.full(n, 20.0)
        data[f"stoch_d_{tf}"] = np.full(n, 50.0)
    data.update(
        close_3m=np.array([10.0, 9.0, 8.0, 7.0]),
        rsi_4h=np.full(n, 50.0),
        rsi_1h=np.full(n, 50.0),
        bb_pct_b_4h=np.full(n, 0.5),
    )
    return data


def _cfg(**updates):
    values = dict(
        UNIVERSAL_NOLOSS_GATE=False,
        COMMISSION_BUFFER_PCT=0.10,
        UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL=True,
        UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS=("R1_", "STRUCTURAL_RANGE_SHIFT"),
        OBLIGATORY_HEDGE_ENABLED=False,
        OBLIGATORY_HEDGE_MIN_LOSS_PCT=-0.25,
        OBLIGATORY_HEDGE_WT_USE_1M=False,
        OBLIGATORY_HEDGE_WT_USE_3M=True,
        OBLIGATORY_HEDGE_WT_USE_15M=False,
        OBLIGATORY_HEDGE_WT_USE_1H=True,
        OBLIGATORY_HEDGE_WT_TFS_REQUIRED=0,
        HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H=True,
        HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H=False,
        HEDGE_TRIGGER_USE_WT_3M_ALONE=False,
        HEDGE_FAILED_FALLBACK_CLOSE_ENABLED=True,
        NEWBORN_LOSS_KILL_ENABLED=False,
        NEWBORN_LOSS_KILL_WINDOW_MIN=30.0,
        NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT=0.0,
        NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST=True,
        NEWBORN_LOSS_KILL_VEL_TF="",
        DD_BOUNCE_ENABLED=False,
        DD_BOUNCE_DD_STOP_ENABLED=True,
        UNDERWATER_HEDGE_OR_CLOSE_ENABLED=False,
        UNDERWATER_HEDGE_OR_CLOSE_COOLDOWN_SEC=60.0,
        UNDERWATER_HEDGE_OR_CLOSE_HTF_CLOSE_REQUIRED=2,
        MANDATORY_HEDGE_GAIN_THRESHOLD_PCT=-0.5,
        MICRO_SCALP_USDC_MAKER_ENABLED=False,
        UNDERWATER_HOC_USDC_MAKER_BYPASS=True,
        WRONG_SIDE_ABS_KILL_ENABLED=False,
        WRONG_SIDE_MIN_AGE_MIN=30.0,
        WRONG_SIDE_WT_TFS_REQUIRED=4,
        WRONG_SIDE_K_TFS_REQUIRED=0,
        WT15M_AGAINST_FORCE_HEDGE_ENABLED=False,
        WT15M_AGAINST_FORCE_HEDGE_COOLDOWN_SEC=30.0,
        PARABOLIC_PROTECTION_ENABLED=True,
        PARABOLIC_RSI_4H_MIN=70.0,
        PARABOLIC_RSI_1H_MIN=65.0,
        PARABOLIC_BB_PCT_B_4H_MIN=0.70,
        PARABOLIC_RSI_4H_MAX=30.0,
        PARABOLIC_RSI_1H_MAX=35.0,
        PARABOLIC_BB_PCT_B_4H_MAX=0.10,
    )
    values.update(updates)
    return SimpleNamespace(**values)


def _run(config=None, side="LONG", data=None, **state):
    args = dict(gain_pct=np.zeros(4), position_age_minutes=np.full(4, 30.0))
    args.update(state)
    return evaluate_gap_batch3(
        data or _data(), config or _cfg(), position_side=side, **args
    )


def test_newborn_window_threshold_velocity_and_missing_are_exact():
    cfg = _cfg(NEWBORN_LOSS_KILL_ENABLED=True)
    r = _run(
        cfg,
        gain_pct=np.array([-0.1, 0.0, -0.1, -0.1]),
        position_age_minutes=np.array([0.0, 30.0, 31.0, 15.0]),
    )
    assert r.masks["newborn_loss_kill"].tolist() == [True, True, False, True]
    data = _data()
    data["wt_velocity_3m"][-1] = 1.0
    assert _run(cfg, data=data, gain_pct=-0.1).masks["newborn_loss_kill"].tolist() == [True, True, True, False]
    del data["wt_velocity_3m"]
    missing = _run(cfg, data=data, gain_pct=-0.1)
    assert not missing.masks["newborn_loss_kill"].any()
    assert missing.missing_arrays["newborn_loss_kill"] == ("wt_velocity_3m",)
    no_velocity = _run(
        _cfg(NEWBORN_LOSS_KILL_ENABLED=True, NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST=False),
        data=data,
        gain_pct=-0.1,
    )
    assert no_velocity.masks["newborn_loss_kill"].all()


def test_noloss_obligatory_hedge_bypass_and_failure_contract():
    cfg = _cfg(UNIVERSAL_NOLOSS_GATE=True, OBLIGATORY_HEDGE_ENABLED=True)
    success = _run(cfg, gain_pct=-1.0)
    assert success.masks["obligatory_hedge_fire"].all()
    assert success.obligatory_wt_against_count.tolist() == [2] * 4
    assert success.masks["noloss_hold"].all()
    failed = _run(cfg, gain_pct=-1.0, hedge_attempt_succeeded=False)
    assert failed.masks["obligatory_hedge_failed_fallback_close"].all()
    assert not failed.masks["noloss_hold"].any()
    bypass = _run(cfg, gain_pct=-1.0, hard_exit_reason="R1_DC_BREAK")
    assert bypass.masks["noloss_allow_reduce"].all()
    assert not bypass.masks["obligatory_hedge_fire"].any()
    disabled = _run(_cfg(UNIVERSAL_NOLOSS_GATE=False), gain_pct=-1.0)
    assert disabled.masks["noloss_allow_reduce"].all()


def test_obligatory_dynamic_arrays_and_trigger_precedence_fail_closed():
    data = _data()
    data["wt1_1h"][:] = 2.0
    data["wt1_15m"][:] = 2.0
    cfg = _cfg(
        UNIVERSAL_NOLOSS_GATE=True,
        OBLIGATORY_HEDGE_ENABLED=True,
        OBLIGATORY_HEDGE_WT_TFS_REQUIRED=2,
    )
    assert not _run(cfg, data=data, gain_pct=-1.0).masks["obligatory_hedge_fire"].any()
    alone = _cfg(
        UNIVERSAL_NOLOSS_GATE=True,
        OBLIGATORY_HEDGE_ENABLED=True,
        OBLIGATORY_HEDGE_WT_TFS_REQUIRED=2,
        HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H=False,
        HEDGE_TRIGGER_USE_WT_3M_ALONE=True,
    )
    assert _run(alone, data=data, gain_pct=-1.0).masks["obligatory_hedge_fire"].all()
    del data["wt1_15m"]
    missing = _run(alone, data=data, gain_pct=-1.0)
    assert not missing.masks["obligatory_hedge_fire"].any()
    assert missing.missing_arrays["obligatory_hedge"] == ("wt1_15m",)


def test_dd_bounce_stop_uses_strict_adverse_price_breach():
    r = _run(
        _cfg(DD_BOUNCE_ENABLED=True),
        dd_bounce_qty_above_min=True,
        dd_last_augment_price=9.0,
    )
    assert r.masks["dd_bounce_stop"].tolist() == [False, False, True, True]


def test_underwater_threshold_failure_fallback_and_active_close():
    cfg = _cfg(UNDERWATER_HEDGE_OR_CLOSE_ENABLED=True)
    r = _run(
        cfg,
        gain_pct=np.array([-0.4, -0.6, -0.6, -0.6]),
        seconds_since_underwater_action=60.0,
        hedge_already_active=np.array([False, False, False, True]),
        hedge_engine_available=np.array([True, True, False, True]),
        hedge_attempt_succeeded=np.array([True, False, True, True]),
    )
    assert r.masks["underwater_hedge_fire"].tolist() == [False, True, False, False]
    assert r.masks["underwater_force_close"].tolist() == [False, True, True, True]
    assert r.underwater_htf_against_count.tolist() == [4] * 4


def test_underwater_usdc_bypass_is_composed_from_both_switches():
    data = _data()
    for tf in ("1h", "4h", "D"):
        data[f"wt1_{tf}"][:] = 2.0
    base = dict(
        data=data,
        gain_pct=-0.1,
        seconds_since_underwater_action=60.0,
        hedge_already_active=True,
        symbol_is_usdc=True,
    )
    assert not _run(_cfg(UNDERWATER_HEDGE_OR_CLOSE_ENABLED=True), **base).masks["underwater_force_close"].any()
    enabled = _cfg(
        UNDERWATER_HEDGE_OR_CLOSE_ENABLED=True,
        MICRO_SCALP_USDC_MAKER_ENABLED=True,
        UNDERWATER_HOC_USDC_MAKER_BYPASS=True,
    )
    assert _run(enabled, **base).masks["underwater_force_close"].all()


def test_wrong_side_thresholds_are_configurable_and_k_zero_needs_no_k_data():
    data = _data()
    for tf in ("1m",):
        data.pop(f"wt1_{tf}")
        data.pop(f"wt2_{tf}")
    for tf in ("3m", "15m", "1h"):
        data.pop(f"stoch_k_{tf}")
        data.pop(f"stoch_d_{tf}")
    cfg = _cfg(WRONG_SIDE_ABS_KILL_ENABLED=True)
    r = _run(cfg, data=data)
    assert r.wrong_side_wt_count.tolist() == [5] * 4
    assert r.wrong_side_stoch_count.tolist() == [0] * 4
    assert r.masks["wrong_side_abs_kill"].all()
    strict = _run(
        _cfg(
            WRONG_SIDE_ABS_KILL_ENABLED=True,
            WRONG_SIDE_WT_TFS_REQUIRED=5,
            WRONG_SIDE_K_TFS_REQUIRED=3,
        ),
        data=data,
    )
    assert not strict.masks["wrong_side_abs_kill"].any()
    assert "stoch_k_3m" in strict.missing_arrays["wrong_side_abs_kill"]


def test_wt15_hedges_uncovered_and_closes_only_covered_nonparabolic():
    cfg = _cfg(WT15M_AGAINST_FORCE_HEDGE_ENABLED=True)
    data = _data()
    data["rsi_4h"][-1] = 75.0
    data["rsi_1h"][-1] = 70.0
    data["bb_pct_b_4h"][-1] = 0.8
    r = _run(
        cfg,
        data=data,
        seconds_since_wt15_action=np.array([29.0, 30.0, 31.0, 31.0]),
        hedge_already_active=np.array([False, False, True, True]),
    )
    assert r.masks["wt15m_force_hedge"].tolist() == [False, True, False, False]
    assert r.masks["wt15m_force_close"].tolist() == [False, False, True, False]
    no_engine = _run(
        cfg, seconds_since_wt15_action=31.0, hedge_engine_available=False
    )
    assert not no_engine.masks["wt15m_force_hedge"].any()
    assert not no_engine.masks["wt15m_force_close"].any()


def test_missing_wt15_parabolic_inputs_fail_path_closed():
    data = _data()
    del data["rsi_4h"]
    r = _run(
        _cfg(WT15M_AGAINST_FORCE_HEDGE_ENABLED=True),
        data=data,
        seconds_since_wt15_action=31.0,
    )
    assert not r.masks["wt15m_force_hedge"].any()
    assert r.missing_arrays["wt15m_against_force_hedge"] == ("rsi_4h",)


def test_lifecycle_consumer_applies_shared_filters_and_preserves_nonconsumers():
    result = _run(
        _cfg(UNIVERSAL_NOLOSS_GATE=True),
        gain_pct=np.array([-1.0, 1.0, -1.0, 1.0]),
    )
    candidates = {
        "ENTRY": [True, False, True, False],
        "REENTRY": [False, True, False, True],
        "AUGMENT": [True, True, False, False],
        "REDUCE": True,
        "EXIT": True,
        "STOP": False,
    }
    consumed = consume_lifecycle_actions(result, candidates).masks
    assert consumed["ENTRY"].tolist() == candidates["ENTRY"]
    assert consumed["REENTRY"].tolist() == candidates["REENTRY"]
    assert consumed["AUGMENT"].tolist() == candidates["AUGMENT"]
    assert consumed["REDUCE"].tolist() == [False, True, False, True]
    assert consumed["EXIT"].tolist() == [False, True, False, True]
    assert consumed["HOLD"].tolist() == [True, False, True, False]


def test_no_hash_or_synthetic_decision_path():
    import inspect
    import vec_paths.v12_exit_reduce_gap_batch3 as module

    source = inspect.getsource(module).lower()
    assert "hashlib" not in source
    assert "_full_coverage_hash" not in source
    assert "synthetic" not in source

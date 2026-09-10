from types import SimpleNamespace

import numpy as np

from vec_paths.v12_exit_reduce_gap_batch2 import evaluate_gap_batch2


def _data(n=4):
    d = {}
    for tf in ("3m", "15m", "1h", "4h", "D", "W"):
        d[f"wt1_{tf}"] = np.full(n, -2.0)
        d[f"wt2_{tf}"] = np.zeros(n)
        d[f"wt_velocity_{tf}"] = np.full(n, -2.0)
        d[f"wt_momentum_state_{tf}"] = np.ones(n)
        d[f"wt_divergence_{tf}"] = -np.ones(n)
        d[f"wt_peak_structure_{tf}"] = -np.ones(n)
        d[f"wt_trough_structure_{tf}"] = np.ones(n)
        d[f"wt_wave_phase_{tf}"] = -np.ones(n)
        d[f"bb_pct_b_{tf}"] = np.array([0.9, 0.6, 0.9, 0.6])
        d[f"timestamp_{tf}"] = np.arange(n, dtype=float)
    d.update(
        close_3m=np.array([10.0, 9.0, 8.0, 7.0]),
        high_3m=np.array([11.0, 10.0, 9.0, 8.0]),
        low_3m=np.array([9.0, 8.0, 7.0, 6.0]),
        dc_low_3m=np.full(n, 8.5),
        dc_high_3m=np.full(n, 11.5),
        dc_low4_3m=np.full(n, 9.5),
        dc_high4_3m=np.full(n, 10.5),
        htf_trend_score=np.array([-1.0, 1.0, -1.0, 1.0]),
        macd_crossunder_15m=np.array([1, 0, 1, 0]),
        macd_crossover_15m=np.array([0, 1, 0, 1]),
    )
    return d


def _cfg(**kw):
    base = dict(
        ALL_TF_AGAINST_CLOSE_ENABLED=True,
        ALL_TF_AGAINST_CLOSE_MIN_TFS=4,
        ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC=30.0,
        HTF_AGAINST_FORCE_CLOSE_ENABLED=True,
        WT_CROSS_EXIT_REQUIRE_15M_CONFIRM=True,
        HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H=False,
        HTF_AGAINST_FORCE_CLOSE_CONFIRM_3M=True,
        HTF_AGAINST_FORCE_CLOSE_CONFIRM_D=True,
        HTF_EXIT_VETO_ENABLED=True,
        HTF_EXIT_VETO_MAX_LOSS_PCT=2.0,
        HTF_EXIT_VETO_MIN_ALIGNED=2,
        TREND_MIN_GAIN_EXIT=0.1,
        TREND_EXIT_SCORE_FLIP=0,
        BREAKEVEN_GAIN_EROSION_ENABLED=True,
        BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT=True,
        BREAKEVEN_GAIN_EROSION_MIN_GAIN=0.1,
        BREAKEVEN_GRACE_MINUTES=15,
        HARD_BREAKEVEN_FLOOR_ENABLED=True,
        HARD_BREAKEVEN_MIN_PEAK_PCT=0.5,
        BREAKEVEN_DC_LOW4_ENABLED=True,
        BREAKEVEN_DC_FIELD_MODE="DC4",
        PEAK_GIVEBACK_PROTECTION_ENABLED=True,
        PEAK_GIVEBACK_MIN_PEAK_PCT=0.5,
        PEAK_GIVEBACK_HARD_ZERO_ENABLED=True,
        PEAK_GIVEBACK_DROP_TRIGGER_ENABLED=False,
        PEAK_GIVEBACK_DROP_PCT=1.0,
        LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED=False,
        LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK=True,
        SIMPLE_TP_EXIT_ENABLED=True,
        SIMPLE_TP_PCT=0.5,
        MACD_EXIT_ENABLED=True,
        MACD_EXIT_MIN_GAIN=0.3,
        MACD_EXIT_TF="15m",
        MI_EXIT_ENABLED=True,
        MI_MIN_GAIN_EXIT=0.1,
        MI_STRUCT_EXIT_ENABLED=True,
        MI_EXHAUST_EXIT_ENABLED=True,
        MI_DIV_EXIT_ENABLED=True,
        MI_VELOCITY_EXIT_ENABLED=True,
        MI_WAVE_EXIT_ENABLED=True,
        MI_TF_AGREE_MIN=3,
        RULE_B_3M_EXIT_ENABLED=True,
        QUICK_REDUCE_TECHNICAL_ONLY=True,
        STDEV_BREAKOUT_ENABLED=True,
        STDEV_BREAKOUT_EXIT_PCTB_FAIL=0.75,
        STDEV_BREAKOUT_EXIT_WT_ENABLED=False,
        STDEV_REJECT_EXIT_ENABLED=True,
        STDEV_REJECT_EXIT_TF="D",
        STDEV_REJECT_EXIT_ZONE=0.8,
        STDEV_REJECT_EXIT_RETURN=0.65,
        HLR_TOP_EXIT_ENABLED=True,
        HLR_TOP_MIN_GAIN_PCT=1.5,
        HLR_TOP_MIN_TFS=2,
        NOLOSS_MIN_PROFIT_PCT=0.5,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _run(cfg=None, side="LONG", **state):
    defaults = dict(
        gain_pct=np.full(4, 0.6),
        position_age_minutes=np.full(4, 20.0),
        max_gain_pct=np.full(4, 1.0),
        is_trend_account=True,
    )
    defaults.update(state)
    return evaluate_gap_batch2(_data(), cfg or _cfg(), position_side=side, **defaults)


def test_all_tf_force_close_and_cooldown_are_exact():
    r = _run(seconds_since_all_tf_close=np.array([29, 30, 31, 100]))
    assert r.all_tf_against_count.tolist() == [5] * 4
    assert r.masks["all_tf_against_close"].tolist() == [False, True, True, True]
    assert r.masks["htf_against_force_close"].all()
    assert not _run(cfg=_cfg(HTF_AGAINST_FORCE_CLOSE_ENABLED=False)).masks["htf_against_force_close"].any()


def test_htf_veto_and_hard_breakeven_composition():
    r = _run(gain_pct=0.2, max_gain_pct=1.0)
    # Data are adverse to LONG, so no favorable HTF veto.
    assert not r.masks["htf_exit_veto"].any()
    assert r.masks["breakeven_gain_erosion_exit"].all()
    d = _data()
    for tf in ("1h", "4h", "D"):
        d[f"wt1_{tf}"][:] = 2.0
    blocked = evaluate_gap_batch2(
        d, _cfg(), position_side="LONG", gain_pct=0.2,
        position_age_minutes=20, max_gain_pct=0.1,
    )
    assert blocked.masks["htf_exit_veto"].all()
    assert not blocked.masks["breakeven_gain_erosion_exit"].any()
    override = evaluate_gap_batch2(
        d, _cfg(), position_side="LONG", gain_pct=0.2,
        position_age_minutes=20, max_gain_pct=1.0,
    )
    assert override.masks["breakeven_gain_erosion_exit"].all()


def test_dc_mode_peak_and_stale_price_gates():
    r = _run(hard_exit_pending=False)
    assert r.masks["breakeven_dc_structural_exit"].tolist() == [False, True, True, True]
    r = _run(cfg=_cfg(BREAKEVEN_DC_FIELD_MODE="DC"))
    assert r.masks["breakeven_dc_structural_exit"].tolist() == [False, False, True, True]
    r = _run(gain_pct=0.5, max_gain_pct=2.0, cfg=_cfg(PEAK_GIVEBACK_HARD_ZERO_ENABLED=False, PEAK_GIVEBACK_DROP_TRIGGER_ENABLED=True))
    assert r.masks["peak_giveback_exit"].all()
    r = _run(hard_exit_reason="OTHER_EXIT", fresh_gain_pct=-0.2, max_gain_pct=1.0)
    assert r.masks["stale_price_abort"].all()
    allowed = _run(cfg=_cfg(LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED=True), hard_exit_reason="OTHER_EXIT", fresh_gain_pct=-0.2, max_gain_pct=1.0)
    assert allowed.masks["stale_price_allow_near_breakeven"].all()
    technical = _run(hard_exit_reason="WT_CROSS_EXIT", fresh_gain_pct=-2.0)
    assert not technical.masks["stale_price_abort"].any()


def test_simple_macd_mi_and_hlr_paths():
    r = _run(gain_pct=2.0)
    assert r.masks["simple_tp_exit"].all()
    assert r.masks["macd_exit"].tolist() == [True, False, True, False]
    assert r.mi_signal_count.tolist() == [8] * 4
    assert r.masks["mi_exit"].all()
    assert r.hlr_confirm_count.tolist() == [4] * 4
    assert r.masks["hlr_top_exit"].all()
    off = _run(cfg=_cfg(MI_STRUCT_EXIT_ENABLED=False, MI_EXHAUST_EXIT_ENABLED=False, MI_DIV_EXIT_ENABLED=False, MI_VELOCITY_EXIT_ENABLED=False, MI_WAVE_EXIT_ENABLED=False), gain_pct=2.0)
    assert not off.masks["mi_exit"].any()


def test_rule_b_quick_reduce_and_stdev_paths():
    r = _run(rate_recommendation="HOLD", stdev_breakout_active=True, gain_pct=2.0)
    assert r.masks["rule_b_3m_exit"].tolist() == [False, True, True, True]
    assert r.masks["stdev_breakout_exit"].tolist() == [False, True, False, True]
    # D pctB rolls 0.9 -> 0.6 with negative 1h velocity.
    assert r.masks["stdev_reject_exit"].tolist() == [False, True, False, True]
    trapped = _run(rate_recommendation="STRONG_REDUCE", rate_reason="STOCH_ONLY")
    assert trapped.masks["quick_reduce_suppressed"].all()
    named = _run(rate_recommendation="STRONG_REDUCE", rate_reason="RULE_B_3M")
    assert named.masks["quick_reduce_allowed"].all()


def test_missing_arrays_fail_only_affected_path_closed():
    d = _data()
    del d["wt_velocity_W"]
    r = evaluate_gap_batch2(
        d, _cfg(), position_side="LONG", gain_pct=2.0, position_age_minutes=20
    )
    assert r.masks["all_tf_against_close"].all()  # W is not required here
    assert not r.masks["hlr_top_exit"].any()
    assert r.missing_arrays["hlr_top_exit"] == ("wt_velocity_W",)


def test_stdev_reject_uses_prior_distinct_native_tf_not_prior_base_row():
    d = _data()
    d["bb_pct_b_D"] = np.array([0.9, 0.9, 0.6, 0.6])
    d["timestamp_D"] = np.array([1.0, 1.0, 2.0, 2.0])
    r = evaluate_gap_batch2(
        d, _cfg(), position_side="LONG", gain_pct=1.0, position_age_minutes=20
    )
    assert r.masks["stdev_reject_exit"].tolist() == [False, False, True, True]


def test_no_synthetic_or_hash_decision_path():
    import inspect
    import vec_paths.v12_exit_reduce_gap_batch2 as module
    source = inspect.getsource(module).lower()
    assert "hashlib" not in source
    assert "_full_coverage_hash" not in source
    assert "synthetic" not in source

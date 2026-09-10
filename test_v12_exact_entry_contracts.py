from dataclasses import fields, replace

import numpy as np

import v12_quick_engine as q


PROMOTED = {
    "WT_15M_BOUNCE_OPEN_ENABLED": (bool, False),
    "WT_15M_BOUNCE_MAX_BARS_AGO": (int, 2),
    "WT_15M_BOUNCE_BB_MIN": (float, 0.05),
    "WT_15M_BOUNCE_BB_MAX": (float, 0.95),
    "WT_15M_BOUNCE_REQUIRE_BOTH_HTF": (bool, False),
    "WT_15M_CROSS_ENTRY_ENABLED": (bool, False),
    "WT_15M_CROSS_ENTRY_MODE": (str, "value_lower"),
    "RZ_BREAKOUT_ENTRY_ENABLED": (bool, False),
    "RZ_BREAKOUT_BAND": (float, 0.05),
    "WT_PERCENTILE_ENTRY_GATE_ENABLED": (bool, False),
    "WT_PERCENTILE_ENTRY_OB_D": (float, 90.0),
    "WT_PERCENTILE_ENTRY_OS_D": (float, 10.0),
    "MI_ENTRY_ENABLED": (bool, False),
    "MI_ENTRY_EXHAUST_BONUS": (int, 8),
    "MI_ENTRY_STRUCT_BONUS": (int, 10),
    "HAIKU_ENTRY_GATE_ENABLED": (bool, False),
    "HAIKU_ENTRY_GATE_LONG_MAX_K": (float, 85.0),
    "HAIKU_ENTRY_GATE_SHORT_MIN_K": (float, 15.0),
    "BB_PULLBACK_GATE_TF": (str, "15m"),
    "HTF_DIRECTION_GATE_ENABLED": (bool, False),
    "HTF_GATE_APPLY_TO_OPEN": (bool, True),
    "HTF_GATE_BYPASS_RZ": (bool, True),
    "HTF_GATE_D_MANDATORY": (bool, False),
    "HTF_GATE_MIN_CONFIRMATIONS": (int, 2),
    "HTF_GATE_SIGNALS_SMA200D": (bool, True),
    "WT_MTF_VEL_GATE_ENABLED": (bool, True),
    "WT_MTF_VEL_MIN": (int, 2),
    "WT_CHOP_GATE_ENABLED": (bool, False),
    "WT_CHOP_MAX": (int, 8),
    "WT_COMPOSITE_DELTA_GATE_ENABLED": (bool, True),
    "WT_COMPOSITE_DELTA_LONG_MIN": (float, -100.0),
    "WT_COMPOSITE_DELTA_SHORT_MAX": (float, 100.0),
    "WT_COMPOSITE_DELTA_SCORE_ENABLED": (bool, True),
    "WT_COMPOSITE_DELTA_SCORE_THRESHOLD": (float, 50.0),
    "WT_COMPOSITE_DELTA_SCORE_BONUS": (float, 3.0),
    "WT_EXHAUST_ENTRY_GATE_ENABLED": (bool, False),
    "WT_DIV_ENTRY_GATE_ENABLED": (bool, True),
    "R_G10_HTF_DIV_GATE_ENABLED": (bool, False),
    "R_G10_HTF_DIV_TFS": (str, "4h,D"),
    "R_S6_WT_MSTATE_GATE_MODE": (int, 0),
    "WT_COMPOSITE_ENTRY_BLOCK": (float, -20.0),
    "WT_COMPOSITE_ENTRY_GOOD": (float, 30.0),
    "WT_COMPOSITE_ENTRY_OK": (float, 10.0),
    "WT_COMPOSITE_ENTRY_STRONG": (float, 50.0),
    "WT_COMPOSITE_HTF_GATE": (bool, False),
    "R_S1_WT_COMPOSITE_DELTA_USE_ENABLED": (bool, False),
    "R_S1_WT_COMPOSITE_DELTA_THR": (float, 50.0),
    "R_S2_WT_ADAPTIVE_OS_ENABLED": (bool, False),
    "R_S2_WT_PCT_OS_LONG": (float, 10.0),
    "R_S2_WT_PCT_OB_SHORT": (float, 90.0),
}


def test_exact_entry_contract_fields_are_typed_and_defaulted():
    declared = {field.name: field for field in fields(q.QuickConfig)}
    cfg = q.QuickConfig()
    for name, (expected_type, expected_default) in PROMOTED.items():
        assert name in declared
        assert declared[name].type is expected_type
        assert getattr(cfg, name) == expected_default


def test_wt_15m_bounce_reads_every_bound_and_confirmation():
    npz = {
        "wt_cross_bars_ago_15m": np.array([999, 1, 2, 3.0]),
        "wt_cross_rising_15m": np.array([0, 1, 1, 1], dtype=np.int8),
        "wt_cross_rising_1h": np.array([0, 1, 1, 1], dtype=np.int8),
        "wt_cross_rising_4h": np.array([0, 0, 1, 1], dtype=np.int8),
        "bb_pct_b_15m": np.array([0.5, 0.1, 0.9, 0.5]),
    }
    cfg = replace(q.QuickConfig(), WT_15M_BOUNCE_OPEN_ENABLED=True)
    direct, _, _, _ = q._exact_entry_contract_masks(npz, 4, True, cfg)
    assert direct.tolist() == [False, True, True, False]

    cfg = replace(cfg, WT_15M_BOUNCE_REQUIRE_BOTH_HTF=True)
    direct, _, _, _ = q._exact_entry_contract_masks(npz, 4, True, cfg)
    assert direct.tolist() == [False, False, True, False]

    cfg = replace(cfg, WT_15M_BOUNCE_BB_MAX=0.5, WT_15M_BOUNCE_MAX_BARS_AGO=1)
    direct, _, _, _ = q._exact_entry_contract_masks(npz, 4, True, cfg)
    assert not direct.any()

    missing = dict(npz)
    missing.pop("wt_cross_rising_4h")
    direct, _, _, _ = q._exact_entry_contract_masks(missing, 4, True, cfg)
    assert not direct.any()


def test_wt_15m_cross_modes_and_side_are_causal():
    npz = {
        "wt1_15m": np.array([2.0, 1.0, 2.0, 1.0]),
        "wt2_15m": np.array([3.0, 0.0, 1.0, 2.0]),
    }
    cfg = replace(q.QuickConfig(), WT_15M_CROSS_ENTRY_ENABLED=True)
    long, _, _, _ = q._exact_entry_contract_masks(npz, 4, True, cfg)
    short, _, _, _ = q._exact_entry_contract_masks(npz, 4, False, cfg)
    assert long.tolist() == [False, True, False, False]
    assert short.tolist() == [False, False, False, True]

    cfg = replace(cfg, WT_15M_CROSS_ENTRY_MODE="any_cross")
    any_side, _, _, _ = q._exact_entry_contract_masks(npz, 4, True, cfg)
    assert any_side.tolist() == [False, True, False, True]

    # Wide-engine parity: GR is bypassed when its parent filter is disabled.
    cfg = replace(
        cfg,
        WT_15M_CROSS_ENTRY_MODE="any_cross_gr",
        MTF_ENTRY_REQUIRE_GR_FILTER=False,
    )
    gr_disabled, _, _, _ = q._exact_entry_contract_masks(npz, 4, True, cfg)
    assert np.array_equal(gr_disabled, any_side)


def test_rz_breakout_is_an_early_bypass_and_missing_is_no_event():
    cfg = replace(
        q.QuickConfig(),
        MODE="crypto",
        RZ_BREAKOUT_ENTRY_ENABLED=True,
        RZ_TOP_BB_THRESHOLD=0.85,
        RZ_BOT_BB_THRESHOLD=0.15,
    )
    npz = {"bb_pct_b_1h": np.array([0.14, 0.15, 0.20, 0.21, 0.80, 0.85, 0.86])}
    _, long_bypass, _, _ = q._exact_entry_contract_masks(npz, 7, True, cfg)
    _, short_bypass, _, _ = q._exact_entry_contract_masks(npz, 7, False, cfg)
    assert long_bypass.tolist() == [False, True, True, False, False, False, False]
    assert short_bypass.tolist() == [False, False, False, False, True, True, False]
    assert not q._exact_entry_contract_masks({}, 7, True, cfg)[1].any()


def test_percentile_haiku_and_bb_selected_tf_gates_match_missing_policy():
    cfg = replace(
        q.QuickConfig(),
        MODE="tradier",
        WT_PERCENTILE_ENTRY_GATE_ENABLED=True,
        HAIKU_ENTRY_GATE_ENABLED=True,
        BB_PULLBACK_GATE_ENABLED=True,
        BB_PULLBACK_GATE_TF="1h",
        BB_PULLBACK_GATE_LONG_MAX=0.30,
    )
    npz = {
        "wt_percentile_D": np.array([89.0, 91.0, 50.0]),
        "stoch_k_15m": np.array([84.0, 70.0, 86.0]),
        "bb_pct_b_1h": np.array([0.29, 0.20, 0.10]),
        "bb_pct_b_15m": np.array([0.90, 0.90, 0.90]),
    }
    _, _, gate, _ = q._exact_entry_contract_masks(npz, 3, True, cfg)
    assert gate.tolist() == [True, False, False]

    # All three scalar sources fail open when their snapshot value is absent.
    _, _, missing_gate, _ = q._exact_entry_contract_masks({}, 3, True, cfg)
    assert missing_gate.all()

    # BB pullback belongs to the stock manager; the crypto path is N/A.
    _, _, crypto_gate, _ = q._exact_entry_contract_masks(
        {"bb_pct_b_1h": np.ones(3)}, 3, True, replace(cfg, MODE="crypto")
    )
    assert crypto_gate.all()


def test_mi_score_sums_all_five_live_subsignals_for_each_side():
    cfg = replace(q.QuickConfig(), MI_ENTRY_ENABLED=True)
    long_npz = {
        "wt_trough_structure_1h": np.array([1, 0, 1], dtype=np.int8),
        "wt_trough_structure_4h": np.array([1, 0, 0], dtype=np.int8),
        "wt_momentum_state_1h": np.array([-2, 0, -2], dtype=np.int8),
        "wt_momentum_state_4h": np.array([-2, 0, 0], dtype=np.int8),
        "wt_divergence_1h": np.array([1, 0, 0], dtype=np.int8),
    }
    score = q._exact_entry_contract_masks(long_npz, 3, True, cfg)[3]
    assert score.tolist() == [44.0, 0.0, 18.0]

    short_npz = {
        "wt_peak_structure_1h": np.array([-1, 0], dtype=np.int8),
        "wt_peak_structure_4h": np.array([-1, 0], dtype=np.int8),
        "wt_momentum_state_1h": np.array([2, 0], dtype=np.int8),
        "wt_momentum_state_4h": np.array([2, 0], dtype=np.int8),
        "wt_divergence_1h": np.array([-1, 0], dtype=np.int8),
    }
    score = q._exact_entry_contract_masks(short_npz, 2, False, cfg)[3]
    assert score.tolist() == [44.0, 0.0]


def test_rz_bypass_reaches_compute_entry_even_without_legacy_blocks(monkeypatch):
    monkeypatch.setattr(q, "compute_reentry_blocks", lambda *_args, **_kwargs: {})
    cfg = replace(
        q.QuickConfig(),
        MODE="crypto",
        BASE_TF="3m",
        RZ_BREAKOUT_ENTRY_ENABLED=True,
        RZ_TOP_BB_THRESHOLD=0.85,
        RZ_BOT_BB_THRESHOLD=0.15,
    )
    n = 4
    npz = {
        "close": np.full(n, 100.0),
        "bb_pct_b_1h": np.array([0.10, 0.15, 0.20, 0.30]),
    }
    out = q.compute_entry_signals(npz, n, True, cfg)
    assert out.tolist() == [False, True, True, False]


def test_crypto_htf_direction_gate_counts_optional_sma_and_daily_requirement():
    npz = {
        "close": np.array([110.0, 110.0, 110.0]),
        "sma_200_D": np.full(3, 100.0),
        "wt1_D": np.array([2.0, 1.0, 2.0]),
        "wt2_D": np.array([1.0, 2.0, 1.0]),
        "wt1_4h": np.array([2.0, 2.0, 1.0]),
        "wt2_4h": np.array([1.0, 1.0, 2.0]),
        "wt1_1h": np.array([1.0, 2.0, 1.0]),
        "wt2_1h": np.array([2.0, 1.0, 2.0]),
    }
    cfg = replace(q.QuickConfig(), MODE="crypto", HTF_DIRECTION_GATE_ENABLED=True)
    gate, _, htf = q._exact_crypto_wt_entry_contract(npz, 3, True, cfg)
    assert gate.all() and htf.all()

    cfg = replace(cfg, HTF_GATE_D_MANDATORY=True)
    gate, _, _ = q._exact_crypto_wt_entry_contract(npz, 3, True, cfg)
    assert gate.tolist() == [True, False, True]

    cfg = replace(cfg, HTF_GATE_D_MANDATORY=False, HTF_GATE_SIGNALS_SMA200D=False)
    gate, _, _ = q._exact_crypto_wt_entry_contract(npz, 3, True, cfg)
    assert gate.tolist() == [True, True, False]


def test_crypto_velocity_and_chop_gates_use_exact_scalar_missing_semantics():
    cfg = replace(q.QuickConfig(), MODE="crypto", WT_CHOP_GATE_ENABLED=True)
    npz = {
        "wt_velocity_up_count": np.array([0, 1, 2], dtype=np.int8),
        "wt_cross_count_bull_3m": np.array([8, 8, 0], dtype=np.int16),
        "wt_cross_count_bull_15m": np.array([8, 0, 0], dtype=np.int16),
    }
    gate, _, _ = q._exact_crypto_wt_entry_contract(npz, 3, True, cfg)
    assert gate.tolist() == [False, False, True]

    relaxed = replace(cfg, WT_MTF_VEL_GATE_ENABLED=False, WT_CHOP_MAX=9)
    gate, _, _ = q._exact_crypto_wt_entry_contract(npz, 3, True, relaxed)
    assert gate.all()

    # Missing velocity/count arrays are unavailable, not invented vetoes.
    gate, _, _ = q._exact_crypto_wt_entry_contract({}, 3, True, cfg)
    assert gate.all()


def test_crypto_composite_delta_gate_and_score_use_strict_live_boundaries():
    cfg = replace(q.QuickConfig(), MODE="crypto", WT_COMPOSITE_SCORING_ENABLED=False)
    npz = {"wt_composite_delta": np.array([-101.0, -100.0, 50.0, 51.0])}
    gate, score, _ = q._exact_crypto_wt_entry_contract(npz, 4, True, cfg)
    assert gate.tolist() == [False, True, True, True]
    assert score.tolist() == [0.0, 0.0, 0.0, 3.0]

    short_cfg = replace(cfg, WT_COMPOSITE_DELTA_SHORT_MAX=75.0)
    gate, score, _ = q._exact_crypto_wt_entry_contract(
        {"wt_composite_delta": np.array([76.0, 75.0, -51.0])}, 3, False, short_cfg
    )
    assert gate.tolist() == [False, True, True]
    assert score.tolist() == [0.0, 0.0, 3.0]


def test_crypto_exhaust_divergence_and_mstate_filters_are_side_exact():
    cfg = replace(
        q.QuickConfig(),
        MODE="crypto",
        WT_EXHAUST_ENTRY_GATE_ENABLED=True,
        R_S6_WT_MSTATE_GATE_MODE=1,
    )
    npz = {
        "wt_momentum_state_3m": np.array([2, 0, 2, 0], dtype=np.int8),
        "wt_momentum_state_15m": np.array([2, 0, 0, 0], dtype=np.int8),
        "wt_any_bear_div": np.array([0, 1, 0, 0], dtype=np.int8),
    }
    gate, _, _ = q._exact_crypto_wt_entry_contract(npz, 4, True, cfg)
    assert gate.tolist() == [False, False, False, True]

    cfg = replace(
        cfg,
        WT_EXHAUST_ENTRY_GATE_ENABLED=False,
        WT_DIV_ENTRY_GATE_ENABLED=False,
        R_S6_WT_MSTATE_GATE_MODE=0,
        R_G10_HTF_DIV_GATE_ENABLED=True,
    )
    npz = {
        "wt_divergence_4h": np.array([-1, 0, 0], dtype=np.int8),
        "wt_divergence_D": np.array([0, -1, 0], dtype=np.int8),
    }
    gate, _, _ = q._exact_crypto_wt_entry_contract(npz, 3, True, cfg)
    assert gate.tolist() == [False, False, True]


def test_crypto_composite_score_thresholds_and_optional_exact_bonuses():
    cfg = replace(q.QuickConfig(), MODE="crypto", WT_COMPOSITE_DELTA_SCORE_ENABLED=False)
    npz = {"wt_composite_long": np.array([9.0, 10.0, 30.0, 50.0])}
    _, score, _ = q._exact_crypto_wt_entry_contract(npz, 4, True, cfg)
    assert score.tolist() == [0.0, 2.0, 5.0, 8.0]

    cfg = replace(
        cfg,
        WT_COMPOSITE_DELTA_SCORE_ENABLED=True,
        R_S1_WT_COMPOSITE_DELTA_USE_ENABLED=True,
        R_S2_WT_ADAPTIVE_OS_ENABLED=True,
    )
    npz = {
        "wt_composite_long": np.array([0.0]),
        "wt_oversold_tf_count": np.array([2]),
        "wt_hl_count": np.array([2]),
        "wt_any_bull_div": np.array([1]),
        "wt_bull_cross_count": np.array([2]),
        "wt_composite_delta": np.array([60.0]),
        "wt_percentile_15m": np.array([5.0]),
    }
    _, score, _ = q._exact_crypto_wt_entry_contract(npz, 1, True, cfg)
    assert score.tolist() == [39.0]

    cfg = replace(cfg, WT_COMPOSITE_HTF_GATE=True)
    npz = {
        "wt_composite_long": np.array([-10.0, -21.0, 10.0]),
        "wt_bull_alignment": np.array([2.0, 3.0, 3.0]),
    }
    gate, _, _ = q._exact_crypto_wt_entry_contract(npz, 3, True, cfg)
    assert gate.tolist() == [False, False, True]

    short_cfg = replace(
        q.QuickConfig(),
        MODE="crypto",
        R_S2_WT_ADAPTIVE_OS_ENABLED=True,
        R_S2_WT_PCT_OB_SHORT=80.0,
    )
    _, short_score, _ = q._exact_crypto_wt_entry_contract(
        {"wt_composite_short": np.zeros(2), "wt_percentile_15m": np.array([79.0, 81.0])},
        2,
        False,
        short_cfg,
    )
    assert short_score.tolist() == [0.0, 7.0]


def test_rz_htf_bypass_switch_controls_only_the_post_signal_htf_wrapper(monkeypatch):
    monkeypatch.setattr(q, "compute_reentry_blocks", lambda *_args, **_kwargs: {})
    cfg = replace(
        q.QuickConfig(),
        MODE="crypto",
        BASE_TF="3m",
        RZ_BREAKOUT_ENTRY_ENABLED=True,
        RZ_TOP_BB_THRESHOLD=0.85,
        RZ_BOT_BB_THRESHOLD=0.15,
        HTF_DIRECTION_GATE_ENABLED=True,
        HTF_GATE_MIN_CONFIRMATIONS=4,
        HTF_GATE_BYPASS_RZ=False,
    )
    npz = {
        "close": np.full(3, 100.0),
        "bb_pct_b_1h": np.array([0.10, 0.15, 0.20]),
    }
    assert not q.compute_entry_signals(npz, 3, True, cfg).any()
    assert q.compute_entry_signals(
        npz, 3, True, replace(cfg, HTF_GATE_BYPASS_RZ=True)
    ).tolist() == [False, True, True]


def test_crypto_wt_family_is_honest_na_for_stock_mode():
    cfg = replace(
        q.QuickConfig(),
        MODE="tradier",
        HTF_DIRECTION_GATE_ENABLED=True,
        WT_CHOP_GATE_ENABLED=True,
        WT_EXHAUST_ENTRY_GATE_ENABLED=True,
        R_G10_HTF_DIV_GATE_ENABLED=True,
    )
    gate, score, htf = q._exact_crypto_wt_entry_contract({}, 3, True, cfg)
    assert gate.all() and htf.all() and not score.any()

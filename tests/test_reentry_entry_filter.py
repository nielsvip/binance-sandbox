from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.generic_filter_tf import FILTER_TF_MAP, _cond
from vec_decisions.entry_dc_gate import pass_mask as entry_dc_pass_mask
from vec_decisions.reentry_entry_filter import (
    compare_mask_coverage,
    generic_filter_tf_live_masks,
    live_15m_masks,
    live_reentry_min_pass,
    min_pass_allowed,
    min_pass_result,
    min_pass_required,
    shared_simple_live_masks,
    vector_entry_mask_snapshot,
    wave4_live_masks,
)
import vec_decisions.twin_yellow_filters as yellow_filters


def test_min_pass_counts_only_available_masks_and_clamps_minimum():
    assert min_pass_result({"a": True, "b": False, "missing": None}, 2) == (
        False,
        1,
        2,
        2,
    )
    assert min_pass_result({"a": True}, 9) == (True, 1, 1, 1)
    assert min_pass_result({"a": None}, 1) == (True, 0, 0, 0)


def test_coverage_comparison_reports_partial_provider_without_claiming_parity():
    result = compare_mask_coverage(
        ["MOM3_FILTER_TF", "HTF_DIRECTION_GATE_ENABLED"],
        {"MOM3_FILTER_TF": True, "NATIVE_EXTRA": False},
    )
    assert result == {
        "vector_active_count": 2,
        "native_active_count": 2,
        "counts_equal": True,
        "producer_sets_equal": False,
        "missing_native": ["HTF_DIRECTION_GATE_ENABLED"],
        "native_only": ["NATIVE_EXTRA"],
    }


def test_live_15m_masks_match_native_long_contract_and_key_omission():
    masks = live_15m_masks(
        {"wt1_15m": 2, "wt2_15m": 1, "k_15m": 80, "rsi_15m": 70}, True
    )
    assert masks == {"wt_cross_15m": True, "stoch_k_15m": True, "rsi_15m": True}
    assert live_15m_masks({"wt1_15m": 2, "k_15m": 50}, True) == {
        "stoch_k_15m": True
    }


def test_live_15m_masks_match_native_short_contract_and_falsey_fallbacks():
    masks = live_15m_masks(
        {"wt1_15m": 1, "wt2_15m": 2, "k_15m": 0, "rsi_15m": 0}, False
    )
    assert masks == {"wt_cross_15m": True, "stoch_k_15m": True, "rsi_15m": True}


def test_empty_live_masks_do_not_block():
    assert min_pass_result(live_15m_masks({}, True), 1) == (True, 0, 0, 0)
    assert min_pass_required(0, 2) == 0
    assert min_pass_required(3, 2) == 2
    assert min_pass_allowed(2, 3, 2)
    assert not min_pass_allowed(1, 3, 2)


def test_native_generic_filter_provider_uses_same_vector_predicate():
    indicators = {
        "bb_pct_b_15m": 0.1,
        "wt1_1h": 2.0,
        "wt2_1h": 1.0,
        "dc_high_15m_prev": 99.0,
        "low_15m": 98.0,
        "high_15m": 101.0,
    }
    cfg = SimpleNamespace(
        BB_BOUNCE_ENTRY_TF="OFF",
        EMA_BLANKET_FILTER_FILTER_TF="OFF",
        BAR_PATTERNS_FILTER_TF="OFF",
        BREAKOUT_RETEST_FILTER_TF="15m",
        BREAKOUT_RETEST_ARMED_ENABLED=True,
        BB_RECOVERY_ENTRY_FILTER_TF="OFF",
        BB_RECOVERY_FILTER_TF="OFF",
        DC_BREAK_FILTER_TF="15m",
        DELTA_ENGINE_FILTER_TF="1h",
        DC_MOMENTUM_BOTA_SCORER_FILTER_TF="OFF",
        CIRCUIT_SHARPE_GATES_FILTER_TF="OFF",
        BT_WT_CROSS_LADDER_FILTER_TF="OFF",
    )
    actual = generic_filter_tf_live_masks(indicators, True, cfg, 100.0)
    assert actual == {
        "BREAKOUT_RETEST_FILTER_TF": True,
        "DC_BREAK_FILTER_TF": True,
        "DELTA_ENGINE_FILTER_TF": True,
    }
    npz = {key: np.asarray([value]) for key, value in indicators.items()}

    def safe(data, key, n, default=0.0):
        return data[key] if key in data else np.full(n, default)

    for name, passed in actual.items():
        kind = FILTER_TF_MAP[name][1]
        tf = getattr(cfg, name)
        reference = _cond(kind, npz, 1, tf, True, np.asarray([100.0]), safe)
        assert bool(reference[0]) is passed


def test_generic_filter_provider_respects_vector_master_switch():
    cfg = SimpleNamespace(
        MODE="crypto",
        BREAKOUT_RETEST_FILTER_TF="15m",
        BREAKOUT_RETEST_ARMED_ENABLED=False,
    )
    assert generic_filter_tf_live_masks(
        {"dc_high_15m_prev": 99.0, "low_15m": 98.0}, True, cfg, 100.0
    ) == {}


def test_yellow_filter_names_stay_aligned_with_appended_masks(monkeypatch):
    monkeypatch.setattr(yellow_filters, "mom3_entry_gate", lambda *args: np.asarray([True]))
    monkeypatch.setattr(yellow_filters, "momentum_breakout_gate", lambda *args: None)
    monkeypatch.setattr(yellow_filters, "dc_break_entry_gate", lambda *args: np.asarray([False]))
    monkeypatch.setattr(yellow_filters, "ema_blanket_pass_vec", lambda *args: None)
    masks, names = [], []
    output = yellow_filters.apply_yellow_entry_masks(
        {}, 1, True, object(), np.asarray([1.0]), np.asarray([True]), masks,
        safe=lambda *args: np.asarray([0.0]), include_kg=False,
        entry_filter_mask_names=names,
    )
    assert len(masks) == len(names) == 2
    assert names == ["YELLOW_MOM3_FILTER_TF", "YELLOW_DC_BREAK_FILTER_TF"]
    assert output.tolist() == [False]


def test_shared_simple_native_provider_matches_shared_vector_gates():
    cfg = SimpleNamespace(
        OI_CONFIRM_ENABLED=False,
        EMA_BLANKET_FILTER_ENABLED=False,
        HTF_DIRECTION_GATE_ENABLED=False,
        WT_PERCENTILE_ENTRY_GATE_ENABLED=False,
        BB_PULLBACK_GATE_FILTER_TF="15m",
        BB_PULLBACK_GATE_ENABLED=True,
        BB_PULLBACK_GATE_TF="15m",
        BB_PULLBACK_GATE_LONG_MAX=0.3,
        BB_PULLBACK_GATE_SHORT_MIN=0.7,
        EMA50_15M_ENTRY_FILTER_ENABLED=True,
        EMA50_15M_ENTRY_FILTER_PCT=2.0,
    )
    indicators = {"bb_pct_b_15m": 0.4, "ema_50_15m": 100.0}
    assert shared_simple_live_masks(indicators, True, cfg, 103.0) == {
        "BB_PULLBACK_GATE_FILTER_TF": False,
        "EMA50_15M_ENTRY_FILTER_ENABLED": True,
    }
    assert shared_simple_live_masks(indicators, False, cfg, 97.9) == {
        "BB_PULLBACK_GATE_FILTER_TF": False,
        "EMA50_15M_ENTRY_FILTER_ENABLED": True,
    }


def test_wave4_native_provider_reports_only_active_shared_gates():
    cfg = SimpleNamespace(
        OI_CONFIRM_ENABLED=True,
        HTF_DIRECTION_GATE_ENABLED=True,
        HTF_GATE_APPLY_TO_OPEN=True,
        HTF_GATE_SIGNALS_SMA200D=False,
        HTF_GATE_MIN_CONFIRMATIONS=3,
        HTF_GATE_D_MANDATORY=True,
        OI_CONFIRM_MIN_CHANGE_PCT=0.5,
        OI_CONFIRM_MIN_PRICE_PCT=0.3,
        EMA_BLANKET_FILTER_ENABLED=False,
        WT_PERCENTILE_ENTRY_GATE_ENABLED=True,
        WT_PERCENTILE_ENTRY_OB_D=90.0,
        WT_PERCENTILE_ENTRY_OS_D=10.0,
    )
    indicators = {
        "oi_change_1h_pct": 1.0,
        "close_1h_prev": 100.0,
        "wt1_D": 2.0,
        "wt2_D": 1.0,
        "wt1_4h": 2.0,
        "wt2_4h": 1.0,
        "wt1_1h": 2.0,
        "wt2_1h": 1.0,
        "wt_percentile_D": 50.0,
    }
    assert wave4_live_masks(indicators, True, cfg, 101.0) == {
        "OI_CONFIRM_ENABLED": True,
        "HTF_DIRECTION_GATE_ENABLED": True,
        "WT_PERCENTILE_ENTRY_GATE_ENABLED": True,
    }
    unavailable_bar = wave4_live_masks(
        {"oi_change_1h_pct": np.nan, "close_1h_prev": 0.0}, True, cfg, 101.0
    )
    assert unavailable_bar["OI_CONFIRM_ENABLED"] is True


def test_oi_mask_is_causal_and_enabled_missing_values_pass_consistently():
    from vec_decisions.wave4_families import oi_confirm_entry_gate

    cfg = SimpleNamespace(
        MODE="crypto",
        BASE_TF="15m",
        ENTRY_CHOKE_GATES_ON_FINAL=False,
        ENTRY_VET_RELAX_MODE=3,
        FUNDING_GATE_ENABLED=False,
        WT_DIV_ENTRY_GATE_ENABLED=False,
        OI_CONFIRM_ENABLED=True,
        HTF_DIRECTION_GATE_ENABLED=True,
        OI_CONFIRM_MIN_CHANGE_PCT=0.5,
        OI_CONFIRM_MIN_PRICE_PCT=0.3,
        EMA_BLANKET_FILTER_ENABLED=False,
        WT_PERCENTILE_ENTRY_GATE_ENABLED=False,
        DC_BREAK_FILTER_TF="OFF",
        ENTRY_DC_TF="OFF",
    )

    def safe(data, key, n, default=0.0):
        return data[key] if key in data else np.full(n, default)

    all_zero = {
        "oi_change_1h_pct": np.zeros(2),
        "close_1h_prev": np.zeros(2),
    }
    assert oi_confirm_entry_gate(all_zero, 2, True, cfg, np.ones(2), safe).tolist() == [True, True]
    missing_fields = oi_confirm_entry_gate({}, 2, True, cfg, np.ones(2), safe)
    assert missing_fields.tolist() == [True, True]
    all_zero_snapshot = vector_entry_mask_snapshot(
        {"oi_change_1h_pct": 0.0, "close_1h_prev": 0.0},
        True,
        cfg,
        1.0,
    )
    assert all_zero_snapshot["OI_CONFIRM_ENABLED"] is True

    late_valid = {
        "oi_change_1h_pct": np.asarray([0.0, 1.0]),
        "close_1h_prev": np.asarray([0.0, 100.0]),
    }
    vector_masks = oi_confirm_entry_gate(
        late_valid, 2, True, cfg, np.asarray([1.0, 101.0]), safe
    )
    assert vector_masks.tolist() == [True, True]
    late_snapshot = vector_entry_mask_snapshot(
        {"oi_change_1h_pct": 0.0, "close_1h_prev": 0.0},
        True,
        cfg,
        1.0,
    )
    assert late_snapshot["OI_CONFIRM_ENABLED"] is True

    no_oi = vector_entry_mask_snapshot({}, True, cfg, 1.0)
    assert no_oi["OI_CONFIRM_ENABLED"] is True

    prefix = {
        "oi_change_1h_pct": np.asarray([np.nan, -1.0]),
        "close_1h_prev": np.asarray([0.0, 100.0]),
    }
    prefix_close = np.asarray([1.0, 99.0])
    prefix_only = oi_confirm_entry_gate(prefix, 2, True, cfg, prefix_close, safe)
    with_future = {
        "oi_change_1h_pct": np.asarray([np.nan, -1.0, 2.0]),
        "close_1h_prev": np.asarray([0.0, 100.0, 100.0]),
    }
    extended = oi_confirm_entry_gate(
        with_future, 3, True, cfg, np.asarray([1.0, 99.0, 102.0]), safe
    )
    assert extended[:2].tolist() == prefix_only.tolist()


def test_entry_dc_filter_mask_is_active_and_causal_when_enabled():
    cfg = SimpleNamespace(ENTRY_DC_TF="15m", ENTRY_DC_BUFFER_PCT=0.1)

    def safe(data, key, n, default=0.0):
        return np.asarray(data[key], dtype=float) if key in data else np.full(n, default)

    all_zero = entry_dc_pass_mask(
        {"dc_low_15m": np.zeros(2), "dc_high_15m": np.zeros(2)},
        2,
        True,
        cfg,
        np.asarray([10.0, 10.0]),
        safe,
    )
    assert all_zero.tolist() == [True, True]
    assert entry_dc_pass_mask({}, 2, True, cfg, np.asarray([10.0, 10.0]), safe).tolist() == [True, True]

    full = {
        "dc_low_15m": np.asarray([0.0, 8.0, 8.0]),
        "dc_high_15m": np.asarray([0.0, 12.0, 12.0]),
    }
    close = np.asarray([10.0, 7.0, 13.0])
    with_suffix = entry_dc_pass_mask(full, 3, True, cfg, close, safe)
    prefix = entry_dc_pass_mask(
        {key: value[:1] for key, value in full.items()},
        1,
        True,
        cfg,
        close[:1],
        safe,
    )
    assert with_suffix.tolist() == [True, False, True]
    assert with_suffix[:1].tolist() == prefix.tolist()


def test_live_reentry_min_pass_enabled_count_boundaries_and_disabled_lane():
    cfg = SimpleNamespace(
        MODE="crypto",
        BASE_TF="15m",
        ENTRY_CHOKE_GATES_ON_FINAL=False,
        ENTRY_VET_RELAX_MODE=3,
        FUNDING_GATE_ENABLED=False,
        WT_DIV_ENTRY_GATE_ENABLED=False,
        OI_CONFIRM_ENABLED=False,
        HTF_DIRECTION_GATE_ENABLED=False,
        EMA_BLANKET_FILTER_ENABLED=False,
        WT_PERCENTILE_ENTRY_GATE_ENABLED=False,
        MOM3_FILTER_TF="15m",
        DC_BREAK_FILTER_TF="15m",
        ENTRY_DC_TF="15m",
        ENTRY_DC_BUFFER_PCT=0.1,
    )
    indicators = {
        "close_3bar_15m": 11.0,
        "dc_high_15m_prev": 11.0,
        "dc_low_15m": 9.0,
        "dc_high_15m": 9.0,
    }
    one, passed1, required1, active1, masks = live_reentry_min_pass(
        indicators, True, cfg, 10.0, min_pass=1, enabled=True
    )
    two, passed2, required2, active2, _ = live_reentry_min_pass(
        indicators, True, cfg, 10.0, min_pass=2, enabled=True
    )
    all_required, passed_all, required_all, active_all, _ = live_reentry_min_pass(
        indicators, True, cfg, 10.0, min_pass=5, enabled=True
    )
    assert set(masks) == {
        "MOM3_FILTER_TF",
        "YELLOW_MOM3_FILTER_TF",
        "DC_BREAK_FILTER_TF",
        "YELLOW_DC_BREAK_FILTER_TF",
        "ENTRY_DC_GATE",
    }
    assert (one, passed1, required1, active1) == (True, 3, 1, 5)
    assert (two, passed2, required2, active2) == (True, 3, 2, 5)
    assert (all_required, passed_all, required_all, active_all) == (False, 3, 5, 5)
    assert live_reentry_min_pass(
        {"wt1_15m": "invalid"}, True, cfg, 10.0, min_pass=1, enabled=False
    ) == (True, 0, 0, 0, {})


def test_native_epq_min_pass_is_reentry_only_and_fail_closed_on_invalid_masks():
    source = (Path(__file__).resolve().parents[1] / "ez_positions_quick.py").read_text()
    assert "if curr_amt == 0 and _vec_reentry_fire and _reentry_ftf_enabled:" in source
    assert "_live_reentry_min_pass(indicators, is_long, _reentry_cfg_view" in source
    assert "[REENTRY_MIN_PASS_INVALID]" in source
    assert "[WORKER_FTF_VETO]" in source


def test_full_provider_matches_active_vector_names_for_single_generic_gate():
    cfg = SimpleNamespace(
        MODE="crypto",
        BASE_TF="15m",
        ENTRY_CHOKE_GATES_ON_FINAL=False,
        ENTRY_VET_RELAX_MODE=3,
        ENTRY_VET_NO_STRUCT_OR_BREAKOUT_REQUIRED=False,
        FUNDING_GATE_ENABLED=False,
        WT_DIV_ENTRY_GATE_ENABLED=False,
        HTF_DIRECTION_GATE_ENABLED=False,
        OI_CONFIRM_ENABLED=False,
        EMA_BLANKET_FILTER_ENABLED=False,
        WT_PERCENTILE_ENTRY_GATE_ENABLED=False,
        DC_BREAK_FILTER_TF="15m",
        ENTRY_DC_TF="OFF",
    )
    indicators = {
        "dc_high_15m_prev": 99.0,
        "wt1_15m": 1.0,
        "wt2_15m": 0.0,
    }
    native = vector_entry_mask_snapshot(indicators, True, cfg, 100.0)
    vector_names = ["DC_BREAK_FILTER_TF", "YELLOW_DC_BREAK_FILTER_TF"]
    coverage = compare_mask_coverage(vector_names, native)
    assert native == {name: True for name in vector_names}
    assert coverage["vector_active_count"] == coverage["native_active_count"] == 2
    assert coverage["producer_sets_equal"]


def test_snapshot_provider_ignores_unrelated_text_metadata_but_rejects_bad_indicator_values():
    cfg = SimpleNamespace(
        MODE="crypto",
        BASE_TF="15m",
        ENTRY_CHOKE_GATES_ON_FINAL=False,
        ENTRY_VET_RELAX_MODE=3,
        FUNDING_GATE_ENABLED=False,
        WT_DIV_ENTRY_GATE_ENABLED=False,
        HTF_DIRECTION_GATE_ENABLED=False,
        OI_CONFIRM_ENABLED=False,
        EMA_BLANKET_FILTER_ENABLED=False,
        WT_PERCENTILE_ENTRY_GATE_ENABLED=False,
        DC_BREAK_FILTER_TF="OFF",
        ENTRY_DC_TF="OFF",
    )
    assert vector_entry_mask_snapshot(
        {"symbol": "1000PEPEUSDC", "side": "LONG", "reason": "metadata"},
        True,
        cfg,
        1.0,
    ) == {}
    with pytest.raises(ValueError, match="wt1_15m"):
        vector_entry_mask_snapshot({"wt1_15m": "unknown"}, True, cfg, 1.0)

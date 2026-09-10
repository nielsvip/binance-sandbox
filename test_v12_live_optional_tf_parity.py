from types import SimpleNamespace

import tradier_manage as tm
from tools import audit_v12_live_vector_parity as parity_audit


def _resolver(enabled):
    return lambda name, default=None, *_args: enabled.get(name, default)


def test_ema_5m_live_route_matches_vector_transition(monkeypatch):
    monkeypatch.setattr(tm, "_cfg", _resolver({"EMA_TF_5M_ENABLED": True}))
    manager = SimpleNamespace()
    common = (manager, "trb:MU_LONG")
    first = {"timestamp_5m": 100, "ema_9_above_21_5m": False}
    cross = {"timestamp_5m": 200, "ema_9_above_21_5m": True}
    assert tm._v12_optional_tf_entry_claim(*common, first, "trb", "MU", "LONG", allow_claim=True) is None
    claim = tm._v12_optional_tf_entry_claim(*common, cross, "trb", "MU", "LONG", allow_claim=True)
    assert claim["switch"] == "EMA_TF_5M_ENABLED"
    assert claim["family"] == "V12_EMA_5m"
    assert tm._v12_optional_tf_entry_claim(*common, cross, "trb", "MU", "LONG", allow_claim=True) is None


def test_ema_short_uses_true_to_false_transition(monkeypatch):
    monkeypatch.setattr(tm, "_cfg", _resolver({"EMA_TF_15M_ENABLED": True}))
    manager = SimpleNamespace()
    args = (manager, "trb:MU_SHORT")
    assert tm._v12_optional_tf_entry_claim(
        *args, {"timestamp_15m": 100, "ema_9_above_21_15m": True},
        "trb", "MU", "SHORT", allow_claim=True,
    ) is None
    claim = tm._v12_optional_tf_entry_claim(
        *args, {"timestamp_15m": 200, "ema_9_above_21_15m": False},
        "trb", "MU", "SHORT", allow_claim=True,
    )
    assert claim["switch"] == "EMA_TF_15M_ENABLED"


def test_wt_route_consumes_completed_event_once(monkeypatch):
    monkeypatch.setattr(tm, "_cfg", _resolver({"WT_TF_5M_ENABLED": True}))
    manager = SimpleNamespace()
    args = (manager, "trb:MU_LONG")
    row = {"timestamp_5m": 300, "wt_cross_bull_5m": True}
    claim = tm._v12_optional_tf_entry_claim(*args, row, "trb", "MU", "LONG", allow_claim=True)
    assert claim["switch"] == "WT_TF_5M_ENABLED"
    assert tm._v12_optional_tf_entry_claim(*args, row, "trb", "MU", "LONG", allow_claim=True) is None


def test_native_timeframe_missing_data_fails_closed(monkeypatch):
    monkeypatch.setattr(tm, "_cfg", _resolver({"EMA_TF_3M_ENABLED": True, "WT_TF_3M_ENABLED": True}))
    manager = SimpleNamespace()
    claim = tm._v12_optional_tf_entry_claim(
        manager, "trb:MU_LONG", {"timestamp_5m": 100},
        "trb", "MU", "LONG", allow_claim=True,
    )
    assert claim is None


def test_synthetic_research_dispatch_is_fail_open(monkeypatch):
    monkeypatch.setattr(tm, "_cfg", _resolver({"CT_DC_CROSSOVER_SKIP_ENABLED": True}))
    assert tm._apply_research_only_live_gates(
        "trb", "MU", "LONG", {"wt1_1h": -99}, True
    ) == (False, "SYNTHETIC_RESEARCH_DISPATCH_DISABLED")


def test_full_coverage_hash_census_is_noop():
    tm._FULL_COVERAGE_HASH.clear()
    assert tm._full_coverage_read("trb", "MU", "LONG") is None
    assert tm._FULL_COVERAGE_HASH == {}


def test_no_reachable_hash_based_decision_path():
    result = parity_audit.synthetic_hash_sites(
        (parity_audit.ADAPTER, *parity_audit.LIVE_DECISION_FILES)
    )
    assert result["reachable"] == []
    assert result["disabled_at_boundary"]


def test_gain_lifecycle_uses_strict_vector_thresholds_and_side_cross():
    enabled = {
        "AUGMENT_GAIN_GT_3PCT_ENABLED": True,
        "AUGMENT_RALLY_MIN_GAIN_PCT": 2.0,
        "AUGMENT_WT15_CROSS_GAIN_GT_2_ENABLED": True,
        "AUGMENT_WT15_MIN_GAIN_PCT": 3.0,
        "REDUCE_GAIN_FALLBACK_LT_1PCT_ENABLED": True,
        "REDUCE_FALLBACK_PEAK_PCT": 2.0,
        "REDUCE_FALLBACK_LIVE_PCT": 1.0,
    }
    values = {
        "wt1_15m_prev": -2.0,
        "wt2_15m_prev": -1.0,
        "wt1_15m": 1.0,
        "wt2_15m": 0.0,
    }
    at_threshold = tm._v12_gain_lifecycle_decision(
        values, is_long=True, gain=2.0, peak_gain=2.0,
        resolver=_resolver(enabled),
    )
    assert at_threshold == {"augment": False, "wt_cross": True, "reduce": False}
    above = tm._v12_gain_lifecycle_decision(
        values, is_long=True, gain=2.01, peak_gain=2.01,
        resolver=_resolver(enabled),
    )
    assert above["augment"] is True
    fallback = tm._v12_gain_lifecycle_decision(
        {}, is_long=True, gain=0.99, peak_gain=2.01,
        resolver=_resolver(enabled),
    )
    assert fallback["reduce"] is True


def test_wt_exit_bundle_uses_selected_native_timeframes_and_thresholds():
    accel = {
        "WT_DIV_EXIT_ENABLED": False,
        "WT_ACCEL_EXIT_ENABLED": True,
        "WT_ACCEL_EXIT_TFS": "5m,1h",
        "WT_ACCEL_EXIT_MIN_TFS": 2,
        "WT_ACCEL_EXIT_LONG_THR": -0.5,
        "WT_MOMENTUM_EXIT_ENABLED": False,
        "WT_EXHAUST_EXIT_ENABLED": False,
        "DELTA_EXIT_ENABLED": False,
    }
    values = {"wt_acceleration_5m": -0.6, "wt_acceleration_1h": -0.7}
    assert tm._v12_wt_exit_reason(
        values, is_long=True, current_price=100.0,
        resolver=_resolver(accel),
    ) == "V12_WT_ACCEL_EXIT_2TF"
    values["wt_acceleration_1h"] = -0.5
    assert tm._v12_wt_exit_reason(
        values, is_long=True, current_price=100.0,
        resolver=_resolver(accel),
    ) == ""

    divergence = {
        "WT_DIV_EXIT_ENABLED": True,
        "WT_DIV_EXIT_TF": "4h",
        "WT_DIV_EXIT_REQUIRE_EXHAUST": True,
        "WT_DIV_EXIT_MOM_TF": "1h",
        "WT_ACCEL_EXIT_ENABLED": False,
        "WT_MOMENTUM_EXIT_ENABLED": False,
        "WT_EXHAUST_EXIT_ENABLED": False,
        "DELTA_EXIT_ENABLED": False,
    }
    assert tm._v12_wt_exit_reason(
        {"wt_divergence_4h": -1, "wt_momentum_state_1h": 1},
        is_long=True, current_price=100.0, resolver=_resolver(divergence),
    ).startswith("V12_WT_DIV_EXIT_4h")


def test_wt_default_exhaust_and_delta_edge_match_vector_votes():
    exhaust = {
        "WT_DIV_EXIT_ENABLED": False,
        "WT_ACCEL_EXIT_ENABLED": False,
        "WT_MOMENTUM_EXIT_ENABLED": False,
        "WT_EXHAUST_EXIT_ENABLED": True,
        "WT_EXHAUST_EXIT_MIN_TFS": 0,
        "DELTA_EXIT_ENABLED": False,
    }
    assert tm._v12_wt_exit_reason(
        {"wt_momentum_state_4h": 1, "wt_momentum_state_15m": 1},
        is_long=True, current_price=100.0, resolver=_resolver(exhaust),
    ).startswith("V12_WT_EXHAUST_EXIT_")

    delta = {
        "WT_DIV_EXIT_ENABLED": False,
        "WT_ACCEL_EXIT_ENABLED": False,
        "WT_MOMENTUM_EXIT_ENABLED": False,
        "WT_EXHAUST_EXIT_ENABLED": False,
        "DELTA_EXIT_ENABLED": True,
        "BASE_TF": "5m",
        "WT_EXIT_MIN_TFS": 3,
        "WT_AGAINST_FILTER_ENABLED": False,
    }
    values = {
        "wt1_5m": -1, "wt2_5m": 1,
        "wt1_15m": -1, "wt2_15m": 1,
        "wt1_1h": -1, "wt2_1h": 1,
        "dc_high_1h": 100,
        "bb_pct_b_1h": 0.5,
    }
    assert tm._v12_wt_exit_reason(
        values, is_long=True, current_price=100.0, resolver=_resolver(delta),
    ) == "V12_WT_DELTA_EXIT_3TF"


def test_b11_long_and_short_optional_filters_match_vector_predicates():
    long_cfg = {
        "REENTRY_B11_MFI_UP_ENABLED": True,
        "REENTRY_B11_LOWER_THAN_EXIT_ENABLED": True,
        "WT_AGAINST_FILTER_ENABLED": True,
    }
    long_values = {
        "dc_low_1h": 100, "dc_high_1h": 120, "dc_high_15m": 118,
        "mfi_1h": 51, "mfi_1h_prev": 50,
        "wt1_15m": 1, "wt2_15m": 0,
        "wt1_1h": 1, "wt2_1h": 0,
        "wt1_4h": 1, "wt2_4h": 0,
    }
    assert tm._v12_b11_reentry_allowed(
        long_values, is_long=True, current_price=100.0,
        resolver=_resolver(long_cfg),
    )
    long_values["mfi_1h"] = 49
    assert not tm._v12_b11_reentry_allowed(
        long_values, is_long=True, current_price=100.0,
        resolver=_resolver(long_cfg),
    )

    short_cfg = {"REENTRY_B11_SHORT_RSI_RVOL_ENABLED": True}
    short_values = {
        "dc_high_1h": 100, "rsi_1h": 56, "rsi_1h_prev": 55,
        "relative_volume_1h": 1.2,
    }
    assert tm._v12_b11_reentry_allowed(
        short_values, is_long=False, current_price=100.0,
        resolver=_resolver(short_cfg),
    )


def test_kindergarten_modes_and_causal_ema_sma_rebuild():
    direct = {
        "KINDERGARTEN_EMA_GATE_ENABLED": True,
        "KINDERGARTEN_CROSS_TYPE": "ema9_21",
        "EMA_9_21_FILTER_ENABLED": True,
        "EMA_9_21_TIMEFRAME": "15m",
        "BASE_TF": "5m",
    }
    manager = SimpleNamespace()
    assert tm._v12_kindergarten_entry_allowed(
        manager, "trb:MU_LONG", {"ema_9_above_21_15m": True},
        is_long=True, current_price=100.0, resolver=_resolver(direct),
    )
    assert not tm._v12_kindergarten_entry_allowed(
        manager, "trb:MU_SHORT", {"ema_9_above_21_15m": True},
        is_long=False, current_price=100.0, resolver=_resolver(direct),
    )

    rebuilt = {
        "KINDERGARTEN_EMA_GATE_ENABLED": True,
        "KINDERGARTEN_CROSS_TYPE": "ema_sma",
        "KINDERGARTEN_TF": "1h",
        "KINDERGARTEN_EMA_PERIOD": 2,
        "KINDERGARTEN_SMA_PERIOD": 2,
        "BASE_TF": "5m",
    }
    manager = SimpleNamespace()
    assert not tm._v12_kindergarten_entry_allowed(
        manager, "trb:MU_LONG", {"close_1h": 100},
        is_long=True, current_price=100.0, resolver=_resolver(rebuilt),
    )
    assert tm._v12_kindergarten_entry_allowed(
        manager, "trb:MU_LONG", {"close_1h": 110},
        is_long=True, current_price=110.0, resolver=_resolver(rebuilt),
    )
    assert len(manager._v12_kindergarten_state[("trb:MU_LONG", "1h", 2, 2)]["samples"]) == 2


def test_new_causal_families_are_machine_inventory_exact():
    payload = parity_audit.build()
    rows = {row["switch"]: row for row in payload["rows"]}
    expected = (
        parity_audit.GAIN_LIFECYCLE_FAMILY
        | parity_audit.WT_EXIT_FAMILY
        | parity_audit.KINDERGARTEN_FAMILY
        | parity_audit.B11_REENTRY_FAMILY
    )
    assert all(rows[name]["exact_semantics"] for name in expected)
    assert payload["claims"]["full_parity_complete"] is False


def test_additive_entry_scores_compose_before_threshold():
    cfg = {
        "BASE_TF": "5m",
        "ENTRY_SCORE_THRESHOLD": 18.0,
        "EMA_PULLBACK_ENABLED": True,
        "EMA_PULLBACK_TF": "15m",
        "EMA_PULLBACK_SCORE_BONUS": 10.0,
        "EMA200_STOCHRSI_ENABLED": False,
        "BB_RSI_STOCH_SCALP_ENABLED": True,
        "BB_RSI_STOCH_SCALP_TF": "15m",
        "BB_RSI_STOCH_BB_MAX": 0.2,
        "BB_RSI_STOCH_RSI_MAX": 30.0,
        "BB_RSI_STOCH_K_MAX": 20.0,
        "BB_RSI_STOCH_SCALP_SCORE": 10.0,
    }
    values = {
        "ts": 100,
        "close_5m": 95,
        "ema_9_15m": 100,
        "ema_14_15m": 98,
        "ema_20_15m": 90,
        "stoch_k_15m": 10,
        "bb_pct_b_15m": 0.1,
        "rsi_15m": 20,
    }
    claim = tm._v12_additive_entry_claim(
        SimpleNamespace(), "trb:MU_LONG", values,
        is_long=True, current_price=95, resolver=_resolver(cfg),
        allow_claim=True,
    )
    assert claim["family"] == "V12_ADDITIVE_ENTRY_SCORE"
    assert "20.0000" in claim["reason"]


def test_additive_entry_uses_selected_tf_and_exact_body_ratio():
    cfg = {
        "BASE_TF": "5m",
        "ENTRY_SCORE_THRESHOLD": 20.0,
        "EMA_PULLBACK_ENABLED": False,
        "EMA200_STOCHRSI_ENABLED": True,
        "EMA200_STOCHRSI_TF": "1h",
        "EMA200_STOCHRSI_K_LONG": 25.0,
        "EMA200_STOCHRSI_BODY_MULT": 1.5,
        "EMA200_STOCHRSI_SCORE": 20.0,
        "BB_RSI_STOCH_SCALP_ENABLED": False,
    }
    values = {
        "ts": 200,
        "close_5m": 110,
        "sma_200_1h": 100,
        "stoch_k_1h": 20,
        "stoch_crossover_1h": True,
        "open_1h": 106,
        "close_1h": 110,
        "open_1h_prev": 100,
        "close_1h_prev": 102,
    }
    assert tm._v12_additive_entry_claim(
        SimpleNamespace(), "trb:MU_LONG", values,
        is_long=True, current_price=110, resolver=_resolver(cfg),
        allow_claim=True,
    ) is not None
    values["open_1h"] = 108
    assert tm._v12_additive_entry_claim(
        SimpleNamespace(), "trb:MU_LONG", values,
        is_long=True, current_price=110, resolver=_resolver(cfg),
        allow_claim=True,
    ) is None


def test_mi_entry_uses_prior_aligned_row_and_clips_vote_minimum():
    cfg = {
        "MODE": "tradier",
        "BASE_TF": "5m",
        "MI_ENTRY_ENABLED_TRADIER": True,
        "TRADIER_MI_ENTRY_ENABLED_TRADIER": False,
        "TRADIER_MI_SUBSIGNAL_MIN_COUNT": 9,
    }
    manager = SimpleNamespace()
    first = {"ts": 100, "mfi_1h": 29, "stoch_k_5m": 10, "rsi_1h": 31}
    second = {"ts": 200, "mfi_1h": 31, "stoch_k_5m": 11, "rsi_1h": 31}
    assert tm._v12_mi_entry_claim(
        manager, "trb:MU_LONG", first, is_long=True,
        resolver=_resolver(cfg), allow_claim=True,
    ) is None
    claim = tm._v12_mi_entry_claim(
        manager, "trb:MU_LONG", second, is_long=True,
        resolver=_resolver(cfg), allow_claim=True,
    )
    assert claim["reason"] == "V12_MI_ENTRY_3of3"


def test_vector_lifecycle_context_and_completed_bar_cooldown():
    cfg = {
        "MODE": "tradier",
        "BASE_TF": "5m",
        "COOLDOWN_BARS": 99,
        "COOLDOWN_BARS_TRADIER": 2,
        "MIN_HOLD_BARS": 3,
        "MIN_HOLD_BARS_BEFORE_EXIT": 4,
        "MIN_HOLD_MINUTES_TRADIER": 30.0,
    }
    resolver = _resolver(cfg)
    context = tm._v12_lifecycle_context(resolver)
    assert context == {
        "mode": "tradier", "cooldown_bars": 2,
        "min_hold_bars": 6, "base_tf": "5m",
    }
    manager = SimpleNamespace()
    tm._v12_stamp_close(manager, "trb:MU_LONG", {"ts": 100}, resolver)
    assert not tm._v12_cooldown_allows(manager, "trb:MU_LONG", {"ts": 200}, resolver)
    assert not tm._v12_cooldown_allows(manager, "trb:MU_LONG", {"ts": 300}, resolver)
    assert tm._v12_cooldown_allows(manager, "trb:MU_LONG", {"ts": 400}, resolver)


def test_inventory_rejects_synthetic_vector_only_wiring():
    payload = parity_audit.build()
    rows = {row["switch"]: row for row in payload["rows"]}
    rejected = {
        "EXIT_SCORER_FULL_SCORE",
        "EXIT_SCORER_PARTIAL_SCORE",
        "LIVE_INDICATOR_MAX_BARS_PER_TF",
        "LR_BAND_LADDER_BOTTOM_MULT",
        "LR_BAND_LADDER_TOP_MULT",
        "SYMBOL_PERF_MAX_MULT",
        "SYMBOL_PERF_MIN_MULT",
        "TIER_A_MIN_GAIN",
        "TIER_A_MIN_TRADES",
        "TRADIER_OI_INJECT_MAX_EACH",
        "TRADIER_OI_INJECT_MIN_TOTAL_OI",
        "TRADIER_OI_INJECT_STALE_MAX_HOURS",
        "WT_3M_FORCE_OPEN_USE_SMA200",
    }
    assert all(rows[name]["status"] == "VECTOR_SYNTHETIC_ONLY_REJECTED" for name in rejected)
    assert payload["entry_score_and_mi_family"] == {
        "requested": 19, "exact_semantics": 19,
    }

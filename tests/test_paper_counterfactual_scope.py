from tools.paper_counterfactual_scope import classify_path


def test_verified_wt_alias_overrides_stale_metadata_and_keeps_veto_separate():
    got = classify_path(
        "QUICK_WT_CROSS_EXIT_1H_BEAR",
        metadata={"timeframe": "3m", "vec_twin": False, "canonical_switch": "OLD"},
        venue="crypto",
    )
    assert got["route"] == "vector_backtest"
    assert got["canonical_switch"] == "WT_CROSS_EXIT_ENABLED"
    assert got["timeframe"] == "1h"
    assert got["optional_native_veto"] == {
        "canonical_switch": "LIVE_5m_trading_ENABLED",
        "timeframe": "3m",
    }


def test_wt_reason_alias_and_delta_shared_alias():
    wt = classify_path("QUICK_WT_CROSS_EXIT_1h_bear_wt1=60", venue="crypto")
    dc = classify_path("DELTA_EXIT_DC15M_FLOOR_BREAK_g-1.2", venue="stocks")
    assert (wt["route"], wt["canonical_switch"], wt["timeframe"]) == (
        "vector_backtest",
        "WT_CROSS_EXIT_ENABLED",
        "1h",
    )
    assert (dc["route"], dc["canonical_switch"], dc["timeframe"]) == (
        "vector_backtest",
        "DC15M_FORCE_EXIT_ENABLED",
        "15m",
    )


def test_metadata_low_timeframe_is_native_but_15m_twin_is_vector():
    low = classify_path("SOME_EXIT", {"tf": "5m", "vec_twin": True})
    main = classify_path("SOME_EXIT", {"tf": "15m", "vec_twin": True})
    assert (low["route"], low["timeframe"]) == ("native_counterfactual", "5m")
    assert (main["route"], main["timeframe"]) == ("vector_backtest", "15m")


def test_metadata_minutes_below_15_and_registry_gate_are_preserved():
    low = classify_path("UNKNOWN", {"tf": "1m", "vec_twin": True, "gate_knob": "LOW_TF_ENABLED"})
    registry = classify_path("WT_LOWER_CROSS_EXIT")
    assert (low["route"], low["canonical_switch"]) == ("native_counterfactual", "LOW_TF_ENABLED")
    assert registry["canonical_switch"] == "WT_LOWER_CROSS_EXIT_TF"


def test_verified_reconciliation_bounce_and_augmented_dc_aliases():
    reconciliation = classify_path("VEC_FWD_MISSING_FLAT fwd=CLOSE", {"tf": "15m", "vec_twin": True})
    bounce = classify_path("WT_15M_BOUNCE_OPEN_S_wt1=-61.2_wt2=-60.8", {"tf": "3m", "vec_twin": False})
    augmented = classify_path("QUICK_AUGMENTED_DC_BREAK_REDUCE_TO_MIN_-0.4", {"tf": "3m", "vec_twin": False})
    assert reconciliation["route"] == "operational_reconciliation"
    assert bounce["route"] == "vector_backtest"
    assert bounce["canonical_switch"] == "WT_15M_BOUNCE_OPEN_ENABLED"
    assert bounce["timeframe"] == "15m"
    assert augmented["route"] == "vector_backtest"
    assert augmented["canonical_switch"] == "AUGMENTED_DC_BREAK_ENABLED"
    assert augmented["timeframe"] == "15m"
    assert augmented["optional_native_variant"]["timeframe"] == "3m"


def test_unverified_named_gaps_are_not_forced_to_vector():
    for path in ("TRADIER_DC_BREAKOUT", "MFI2", "ANG_TIMING_TRIM", "SATOSHIT"):
        assert classify_path(path)["route"] != "vector_backtest"


def test_nonvector_inputs_and_uncertain_paths_remain_distinct():
    native = classify_path("FAST", {"timeframe": "3m", "vec_twin": False})
    nonvec_tf = classify_path("HOOK", {"timeframe": "webhook", "vec_twin": True})
    nonvec_twin = classify_path("NO_TWIN", {"timeframe": "15m", "vec_twin": False})
    uncertain = classify_path("OPAQUE_PATH", {"timeframe": "15m"})
    missing = classify_path("OPAQUE_PATH")
    assert native["route"] == "native_counterfactual"
    assert nonvec_tf["route"] == "nonvector_counterfactual"
    assert nonvec_twin["route"] == "nonvector_counterfactual"
    assert uncertain["route"] == "needs_wiring_audit"
    assert missing["route"] == "needs_wiring_audit"

from sweep_value_semantics import executable_values, validate_test_values


def test_oscillator_grid_rejects_impossible_values_without_silent_clamp():
    result = validate_test_values(
        "CONNORS_RSI_EXIT_THRESHOLD",
        70.0,
        [35.0, 52.5, 70.0, 87.5, 105.0],
    )
    assert result["status"] == "REPAIRED_EXPLICIT_REJECTIONS"
    assert executable_values(result) == [35.0, 52.5, 70.0, 87.5]
    assert result["rejected_values"] == [
        {"value": 105.0, "reason": "above semantic maximum 100.0"}
    ]


def test_stoch_k_grid_rejects_127_point_5():
    result = validate_test_values(
        "DC_DAYTRADE_K_EXHAUSTED_LONG",
        85.0,
        [42.5, 63.75, 85.0, 106.25, 127.5],
    )
    assert executable_values(result) == [42.5, 63.75, 85.0]
    assert [row["value"] for row in result["rejected_values"]] == [
        106.25,
        127.5,
    ]


def test_boolean_grid_refuses_numeric_bool_imposters():
    result = validate_test_values("SOME_PATH_ENABLED", True, [True, 1, False, 0])
    assert executable_values(result) == [True, False]
    assert len(result["rejected_values"]) == 2


def test_counts_are_nonnegative_integers():
    result = validate_test_values("MIN_TFS", 2, [-1, 0, 1.5, 2, 3])
    assert executable_values(result) == [0, 2, 3]
    assert len(result["rejected_values"]) == 2


def test_ambiguous_pctb_units_are_quarantined_not_clamped():
    result = validate_test_values("ENTRY_PCTB_THRESHOLD", 20.0, [10, 20, 30])
    assert result["status"] == "QUARANTINED_AMBIGUOUS_UNITS"
    assert executable_values(result) is None


def test_explicit_timeframe_categories_remain_executable():
    result = validate_test_values(
        "EXIT_STRUCT_TF", "None", ["None", "15m", "1h", "4h", "D"]
    )
    assert result["status"] == "VALID"
    assert executable_values(result) == ["None", "15m", "1h", "4h", "D"]


def test_indicator_token_does_not_make_string_tf_a_numeric_domain():
    result = validate_test_values(
        "STOCH_EXIT_TF", "1h", ["5m", "1h", "4h"]
    )
    assert result["status"] == "VALID"
    assert result["valid_values"] == ["5m", "1h", "4h"]


def test_oscillator_disabled_sentinel_keeps_active_control():
    result = validate_test_values(
        "SATOSHIT_LONG_MFI_MAX_TRADIER", 120.0, [60.0, 90.0, 120.0]
    )
    assert result["status"] == "VALID"
    assert result["valid_values"] == [60.0, 90.0, 120.0]

from vec_decisions.reentry_entry_filter import live_15m_masks, min_pass_result


def test_min_pass_counts_only_available_masks_and_clamps_minimum():
    assert min_pass_result({"a": True, "b": False, "missing": None}, 2) == (
        False,
        1,
        2,
        2,
    )
    assert min_pass_result({"a": True}, 9) == (True, 1, 1, 1)
    assert min_pass_result({"a": None}, 1) == (True, 0, 0, 0)


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

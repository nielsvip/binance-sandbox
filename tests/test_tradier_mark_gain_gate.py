from vec_decisions.net_exit_gate import vector_exit_event_allowed


def test_tradier_exit_gate_uses_native_entry_mark_after_partial_reduction():
    from tradier_positions import calculate_gain

    before = {"entry_price": 100.0, "avg_price": 100.0, "qty": 1.0, "realized": 10.0}
    red_mark_but_positive_lifecycle = {
        "type": "CLOSE",
        "reason": "MULTI_TF_EXIT",
        "entry_price": 100.0,
        "exit_price": 99.0,
        "pnl_dollars": 8.0,
    }
    assert vector_exit_event_allowed(red_mark_but_positive_lifecycle, before, before)
    assert not vector_exit_event_allowed(
        red_mark_but_positive_lifecycle,
        before,
        before,
        require_positive_mark_gain=True,
    )
    assert calculate_gain("LONG", 99.0, 100.0) < 0.0


def test_tradier_exit_gate_allows_positive_mark_even_if_lifecycle_pnl_is_negative():
    from tradier_positions import calculate_gain

    before = {"entry_price": 100.0, "avg_price": 100.0, "qty": 1.0, "realized": -10.0}
    positive_mark_but_negative_lifecycle = {
        "type": "REDUCE",
        "reason": "REDUCE_SIGNAL",
        "price": 101.0,
        "pnl_dollars": -8.0,
    }
    assert not vector_exit_event_allowed(positive_mark_but_negative_lifecycle, before, before)
    assert vector_exit_event_allowed(
        positive_mark_but_negative_lifecycle,
        before,
        before,
        require_positive_mark_gain=True,
        is_long=True,
    )
    assert calculate_gain("LONG", 101.0, 100.0) > 0.0


def test_tradier_mark_gain_gate_is_side_aware_and_preserves_mandatory_red_stops():
    before = {"entry_price": 100.0, "avg_price": 100.0}
    short_positive = {"type": "CLOSE", "reason": "EXIT", "price": 99.0}
    short_negative = {"type": "CLOSE", "reason": "EXIT", "price": 101.0}
    assert vector_exit_event_allowed(short_positive, before, before, is_long=False, require_positive_mark_gain=True)
    assert not vector_exit_event_allowed(short_negative, before, before, is_long=False, require_positive_mark_gain=True)
    mandatory_red = {
        "type": "CLOSE",
        "reason": "ULTIMATE_DC_4H_HARD_STOP_LONG_px99_lvl100",
        "price": 99.0,
    }
    assert vector_exit_event_allowed(mandatory_red, before, before, require_positive_mark_gain=True)


def test_tradier_mark_gain_gate_uses_weighted_entry_after_native_augment():
    from tradier_positions import calculate_gain

    long_before = {"entry_price": 100.0, "avg_price": 100.0, "qty": 2.0}
    long_after = {"entry_price": 100.0, "avg_price": 105.0, "qty": 4.0}
    long_close = {
        "type": "CLOSE",
        "reason": "EXIT",
        "entry_price": 100.0,
        "exit_price": 102.0,
        "pnl_dollars": 1.0,
    }
    assert calculate_gain("LONG", 102.0, 105.0) < 0.0
    assert not vector_exit_event_allowed(
        long_close,
        long_after,
        long_before,
        is_long=True,
        require_positive_mark_gain=True,
    )

    short_before = {"entry_price": 100.0, "avg_price": 100.0, "qty": 2.0}
    short_after = {"entry_price": 100.0, "avg_price": 95.0, "qty": 4.0}
    short_close = {"type": "CLOSE", "reason": "EXIT", "entry_price": 100.0, "exit_price": 98.0, "pnl_dollars": 1.0}
    assert calculate_gain("SHORT", 98.0, 95.0) < 0.0
    assert not vector_exit_event_allowed(
        short_close,
        short_after,
        short_before,
        is_long=False,
        require_positive_mark_gain=True,
    )


def test_tradier_mark_gain_gate_fails_closed_when_mark_inputs_are_missing_or_nonpositive():
    assert not vector_exit_event_allowed(
        {"type": "CLOSE", "reason": "EXIT", "price": 101.0},
        {},
        {},
        require_positive_mark_gain=True,
    )
    assert not vector_exit_event_allowed(
        {"type": "CLOSE", "reason": "EXIT", "entry_price": 0.0, "price": 101.0},
        {"avg_price": 100.0},
        {"avg_price": 100.0},
        require_positive_mark_gain=True,
    )

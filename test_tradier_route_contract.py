from tradier_route_contract import flat_entry_data_error


def test_flat_candidate_with_synthetic_50_50_is_rejected():
    assert flat_entry_data_error(
        {"k_1m": 50.0, "k_5m": 50.0},
        has_position=False,
    )


def test_held_position_with_synthetic_50_50_reaches_exit_monitor():
    assert not flat_entry_data_error(
        {"k_1m": 50.0, "k_5m": 50.0},
        has_position=True,
    )


def test_non_neutral_flat_candidate_reaches_entry_monitor():
    assert not flat_entry_data_error(
        {"k_1m": 50.0, "k_5m": 49.9},
        has_position=False,
    )


def test_neutral_flat_key_with_reclaim_or_ladder_state_reaches_monitor():
    assert not flat_entry_data_error(
        {"k_1m": 50.0, "k_5m": 50.0},
        has_position=False,
        stateful_route_required=True,
    )

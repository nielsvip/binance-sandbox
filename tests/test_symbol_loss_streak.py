from vec_decisions.symbol_loss_streak import is_blocked, record_close


def test_symbol_loss_streak_trips_at_threshold_and_expires():
    losses = 0
    until = 0.0
    for close_ts in (100.0, 200.0):
        losses, until = record_close(losses, until, -1.0, close_ts, 3, 1800.0)
        assert until == 0.0
    losses, until = record_close(losses, until, -0.1, 300.0, 3, 1800.0)
    assert (losses, until) == (0, 2100.0)
    assert is_blocked(2099.0, until)
    assert not is_blocked(2100.0, until)


def test_symbol_loss_streak_winning_close_resets_count():
    losses, until = record_close(2, 0.0, 0.01, 500.0, 3, 1800.0)
    assert (losses, until) == (0, 0.0)
    losses, until = record_close(losses, until, -1.0, 600.0, 3, 1800.0)
    assert (losses, until) == (1, 0.0)


def test_symbol_loss_streak_invalid_outcome_does_not_change_state():
    assert record_close(2, 1000.0, None, 500.0, 3, 1800.0) == (2, 1000.0)

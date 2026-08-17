import pytest

from bb_recovery_contract import BBRecoveryEntry, BBRecoveryParams, CompletedBBBar


def bar(ts, close, *, tf="1h", upper=110., lower=90., atr=4., observed=None):
    return CompletedBBBar(tf, ts, ts if observed is None else observed, close, upper, lower, atr)


def test_completed_long_failed_break_requires_later_recovery_within_selected_window():
    route = BBRecoveryEntry(BBRecoveryParams("1h", 1, .5), side="LONG")
    assert not route.step(bar(100, 87)).entry_event  # arms below 90 - .5*4
    event = route.step(bar(200, 90))
    assert event.entry_event
    # Same completed parent cannot duplicate an event.
    assert not route.step(bar(200, 90)).entry_event


def test_short_mirror_and_expired_or_invalid_parent_fail_closed():
    route = BBRecoveryEntry(BBRecoveryParams("15m", 1, .25), side="SHORT")
    assert not route.step(bar(100, 111, tf="15m")).entry_event
    assert route.step(bar(200, 110, tf="15m")).entry_event
    assert "FUTURE_COMPLETED_BAR" in route.step(bar(300, 90, tf="15m", observed=299)).blockers
    assert "MUTATED_COMPLETED_BAR" in route.step(bar(200, 109, tf="15m")).blockers


def test_non_vector_parameter_values_are_rejected():
    with pytest.raises(ValueError):
        BBRecoveryParams("1h", 3, .5).validate()

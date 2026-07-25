"""Regression contract for DeltaTracker's delayed failed-retest exit state."""

import pytest

from wt_dc_delta import (
    DeltaTracker,
    delayed_retest_exit_step,
    structural_exit_permitted,
)


class _NoRedzoneTracker(DeltaTracker):
    """Keep these tests isolated from independent red-zone exit signals."""

    def _run_redzone(self, *args, **kwargs):
        return None


_CFG = {
    "tf_weights": {},
    "structural_exit_gate_enabled": True,
    "rz_two_phase_exit_enabled": True,
    "rz_div_exit_enabled": False,
    "rz_zscore_exit_enabled": False,
    "exit_zero_speed_threshold": -1,
    "exit_speed_decay_pct": 101,
    "exit_min_tf_lost": 0,
    "exit_require_htf_slowdown": False,
    "entry_enabled": False,
}


def _phase_two_bar(side):
    if side == "LONG":
        return {
            "_tick_ts": 1,
            "current_price": 105,
            "open_3m": 100,
            "close_3m": 104,
            "high_3m": 106,
            "low_3m": 99,
            "high_3m_prev": 103,
            "low_3m_prev": 98,
            "high_1h": 110,
            "high_1h_prev": 109,
            "low_1h": 100,
            "low_1h_prev": 99,
            "high_4h": 120,
            "high_4h_prev": 119,
            "low_4h": 90,
            "low_4h_prev": 89,
        }
    return {
        "_tick_ts": 1,
        "current_price": 95,
        "open_3m": 100,
        "close_3m": 96,
        "high_3m": 101,
        "low_3m": 94,
        "high_3m_prev": 102,
        "low_3m_prev": 97,
        "high_1h": 109,
        "high_1h_prev": 110,
        "low_1h": 99,
        "low_1h_prev": 100,
        "high_4h": 119,
        "high_4h_prev": 120,
        "low_4h": 89,
        "low_4h_prev": 90,
    }


@pytest.mark.parametrize(
    ("side", "pending_key", "signal_attr"),
    [
        ("LONG", "_exit_pending_long", "exit_pending_long"),
        ("SHORT", "_exit_pending_short", "exit_pending_short"),
    ],
)
def test_structural_veto_preserves_two_phase_pending(side, pending_key, signal_attr):
    tracker = _NoRedzoneTracker(_CFG)
    tracker._prev["TEST"][pending_key] = True

    signal = tracker.update(
        "TEST", _phase_two_bar(side), {"side": side, "n_entries": 1}
    )

    assert getattr(signal, signal_attr) is True
    assert tracker._prev["TEST"][pending_key] is True


@pytest.mark.parametrize(
    ("is_long", "bar", "expected_reason"),
    [
        (
            True,
            (100.0, 103.0, 99.5, 102.0, 101.0, 99.0),
            "RETEST_SEEN",
        ),
        (
            False,
            (100.0, 100.5, 97.0, 98.0, 101.0, 99.0),
            "RETEST_SEEN",
        ),
    ],
)
def test_rebound_starts_retest_but_never_exits(is_long, bar, expected_reason):
    execute, pending, seen, reason = delayed_retest_exit_step(
        is_long, True, False, *bar
    )
    assert execute is False
    assert pending is True
    assert seen is True
    assert reason == expected_reason


@pytest.mark.parametrize(
    ("is_long", "bar", "expected_reason"),
    [
        (
            True,
            (101.0, 101.5, 97.5, 98.0, 102.0, 99.0),
            "LONG_FAILED_RETEST_LH_LL",
        ),
        (
            False,
            (99.0, 102.5, 98.5, 102.0, 101.0, 98.0),
            "SHORT_FAILED_RETEST_HH_HL",
        ),
    ],
)
def test_exit_requires_rollover_after_retest(is_long, bar, expected_reason):
    execute, pending, seen, reason = delayed_retest_exit_step(
        is_long, True, True, *bar
    )
    assert execute is True
    assert pending is False
    assert seen is True
    assert reason == expected_reason


def test_missing_bar_keeps_pending_obligation():
    execute, pending, seen, reason = delayed_retest_exit_step(
        True, True, True, None, None, None, None, None, None
    )
    assert execute is False
    assert pending is True
    assert seen is True
    assert reason == "WAIT_MISSING_BAR"


def test_structural_gate_long_requires_confirmed_down_structure():
    permitted, _ = structural_exit_permitted(
        {
            "current_price": 97,
            "open_3m": 100,
            "close_3m": 97,
            "high_3m": 101,
            "high_3m_prev": 103,
            "low_3m": 96,
            "low_3m_prev": 98,
        },
        True,
    )
    assert permitted is True


def test_structural_gate_short_requires_confirmed_up_structure():
    permitted, _ = structural_exit_permitted(
        {
            "current_price": 103,
            "open_3m": 100,
            "close_3m": 103,
            "high_3m": 104,
            "high_3m_prev": 102,
            "low_3m": 99,
            "low_3m_prev": 97,
        },
        False,
    )
    assert permitted is True

from __future__ import annotations

import pytest

from wt_dc_delta import (
    DeltaTracker,
    causal_z_speed_acceleration,
    exit_acceleration_below,
    exit_min_hold_satisfied,
    normalize_delta_cfg,
    opposing_pressure_exceeds,
)


def test_fractional_decay_ratio_is_normalized_to_percent():
    cfg = normalize_delta_cfg({"exit_speed_decay_ratio": 0.15})
    assert cfg["exit_speed_decay_pct"] == pytest.approx(15.0)
    assert DeltaTracker(
        {"exit_speed_decay_ratio": 0.45}
    ).cfg["exit_speed_decay_pct"] == pytest.approx(45.0)


def test_explicit_percent_wins_over_ratio_alias():
    cfg = normalize_delta_cfg(
        {
            "exit_speed_decay_ratio": 0.15,
            "exit_speed_decay_pct": 62.0,
        }
    )
    assert cfg["exit_speed_decay_pct"] == pytest.approx(62.0)


def test_legacy_percent_shaped_ratio_is_not_multiplied_again():
    cfg = normalize_delta_cfg({"exit_speed_decay_ratio": 35.0})
    assert cfg["exit_speed_decay_pct"] == pytest.approx(35.0)


def test_opposing_ratio_moves_the_decision_boundary():
    # Counter-speed 1.5 exceeds current-speed 1.0 at 0.75x, but not 2.25x.
    assert opposing_pressure_exceeds(1.5, 1.0, 0.75)
    assert not opposing_pressure_exceeds(1.5, 1.0, 2.25)


def test_opposing_ratio_default_preserves_old_one_to_one_behavior():
    assert opposing_pressure_exceeds(1.01, 1.0)
    assert not opposing_pressure_exceeds(1.0, 1.0)


def _acceleration_series(values, direction, *, lookback=2):
    state = {}
    return [
        causal_z_speed_acceleration(
            state,
            direction,
            value,
            append=True,
            lookback=lookback,
            window=20,
        )
        for value in values
    ]


def test_causal_z_acceleration_is_long_short_symmetric():
    values = [0.0, 1.0, 2.0, 1.0, 0.25]

    bull = _acceleration_series(values, "bull")
    bear = _acceleration_series(values, "bear")

    assert bull == bear
    assert bull[-1] < 0.0


def test_exit_acceleration_threshold_changes_the_gate():
    acceleration = _acceleration_series(
        [0.0, 1.0, 2.0, 1.0, 0.25],
        "bull",
    )[-1]

    assert exit_acceleration_below(acceleration, -0.05) is True
    assert exit_acceleration_below(
        acceleration, acceleration - 0.01
    ) is False


def test_repeated_same_bar_does_not_advance_causal_history():
    state = {}
    for value in (0.0, 1.0, 2.0):
        causal_z_speed_acceleration(
            state, "bull", value, append=True, lookback=2, window=20
        )
    before = tuple(state["_exit_bull_speed_z"])

    repeated = causal_z_speed_acceleration(
        state, "bull", 99.0, append=False, lookback=2, window=20
    )

    assert tuple(state["_exit_bull_speed_z"]) == before
    assert repeated == before[-1] - before[-3]


def test_exit_min_hold_uses_authoritative_fifteen_minute_bars():
    assert exit_min_hold_satisfied(6.0, 6)
    assert not exit_min_hold_satisfied(5.99, 6)
    assert exit_min_hold_satisfied(None, 6)

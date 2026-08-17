from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import run_ttd_short_hysteretic_episode as study


def test_lower_high_break_requires_sequence_and_close_through():
    high = np.array([15, 14, 13, 12, 13, 12, 11, 12], dtype=float)
    close = np.array([14, 13, 12, 11, 13.5, 11, 10, 12.5])
    event = study._lower_high_break(high, close, 2)
    assert event.tolist() == [0, 0, 0, 0, 1, 0, 0, 1]


def test_episode_latches_reassertion_until_minimum_dwell():
    rebound = np.array([.01, .09, .10, .08, .07, .06, .04, .03])
    structural_break = np.array([0, 1, 0, 0, 0, 0, 0, 0])
    # Reassertion arrives before dwell and must remain pending.
    reassert = np.array([0, 0, 0, 1, 0, 0, 0, 0])
    state, audit = study._episode_state(
        rebound, structural_break, reassert, .08, 4, .5
    )
    assert state.tolist() == [0, 1, 1, 1, 1, 0, 0, 0]
    assert audit["episode_entries"] == 1
    assert audit["episode_exits"] == 1


def test_episode_suppresses_rearm_until_absolute_reset():
    rebound = np.array([.09, .08, .09, .09, .03, .09])
    structural_break = np.ones(6, dtype=np.uint8)
    reassert = np.array([0, 1, 0, 0, 0, 0])
    state, audit = study._episode_state(
        rebound, structural_break, reassert, .08, 1, .5
    )
    assert state.tolist() == [1, 0, 0, 0, 0, 1]
    assert audit["episode_entries"] == 2
    assert audit["suppressed_rearms"] == 2


def test_daily_reassert_maps_only_at_or_after_daily_availability():
    data = SimpleNamespace(ts=np.arange(100, 140, dtype=np.int64))
    h4 = SimpleNamespace(
        event_index=np.array([2, 6, 10, 14, 18, 22, 26, 30, 34, 38]),
        close=np.ones(10),
    )
    daily = SimpleNamespace(
        event_index=np.array([1, 7, 13, 19, 25, 31]),
        source_ts=np.array([101, 107, 113, 119, 125, 131]),
        low=np.array([10, 9, 8, 7, 6, 5], dtype=float),
        high=np.array([12, 11, 10, 9, 8, 7], dtype=float),
        close=np.array([11, 10, 8.5, 7.5, 6.5, 5.5], dtype=float),
    )
    mapped, audit = study._daily_reassertion(
        data, h4, daily, lookback=2
    )
    assert audit["daily_parent_future_count"] == 0
    assert audit["mapped_reassertion_count"] == 4
    assert np.flatnonzero(mapped).tolist() == [3, 5, 6, 8]
    assert h4.event_index[3] >= daily.event_index[2]


def test_purged_slices_advance_by_completed_4h_events():
    data = SimpleNamespace(ts=np.arange(1000, dtype=np.int64))
    h4 = SimpleNamespace(event_index=np.arange(0, 1000, 10))
    slices = study._purged_inner_slices(
        data, h4, 0, 800, purge_4h=3
    )
    raw = study.ladder._inner_slices(0, 800)
    assert len(slices) == 3
    assert all(left >= raw_left + 30 for (left, _), (raw_left, _) in zip(slices, raw))


def test_policy_family_is_bounded_and_whole_book_only():
    assert study.ROOT == Path(__file__).resolve().parent
    assert len(study.EPISODE_POLICIES) == 9
    assert {policy.high_risk_book for policy in study.EPISODE_POLICIES} == {
        study.CONTROL_LABEL
    }
    assert {policy.low_risk_book for policy in study.EPISODE_POLICIES} == {
        study.STRICT_LABEL
    }
    assert max(
        policy.minimum_dwell_4h for policy in study.EPISODE_POLICIES
    ) == 18

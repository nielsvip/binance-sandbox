import time

import numpy as np

from tools.replay_forward_vector import (
    _ReplayClock,
    _actions_at,
    _expected_closed_bar,
    row_prefix,
)


def test_prefix_slices_every_time_aligned_array_without_future_rows():
    arrays = {
        "timestamps": np.arange(10, 18),
        "close": np.arange(100, 108),
        "2d": np.arange(16).reshape(8, 2),
        "scalar": np.asarray(5),
        "metadata": "fixed",
    }
    prefix = row_prefix(arrays, 3, 8)
    assert prefix["timestamps"].tolist() == [10, 11, 12, 13]
    assert prefix["close"].tolist() == [100, 101, 102, 103]
    assert prefix["2d"].tolist() == [[0, 1], [2, 3], [4, 5], [6, 7]]
    assert int(prefix["scalar"]) == 5
    assert prefix["metadata"] == "fixed"


def test_bar_open_timestamp_maps_to_next_close_decision_clock():
    bar_open = 1_800_000_000
    decision_clock = bar_open + 900
    assert _expected_closed_bar(decision_clock) == bar_open
    replay_clock = _ReplayClock(decision_clock, time)
    assert replay_clock.time() == decision_clock
    assert replay_clock.gmtime() == time.gmtime()


def test_actions_are_emitted_only_for_current_npz_bar():
    events = [
        {"type": "OPEN", "ts": 1_800_000_000, "qty": 2},
        {"type": "AUGMENT", "ts": 1_800_000_000, "qty": 1},
        {"type": "REDUCE", "ts": 1_799_999_100, "qty": 0.5},
        {"type": "CLOSE", "ts": 1_800_000_900, "qty": 3},
        {"type": "NOTE", "ts": 1_800_000_000},
    ]
    current = _actions_at(events, 1_800_000_000)
    assert [row["type"] for row in current] == ["OPEN", "AUGMENT"]

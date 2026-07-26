import math
from types import SimpleNamespace

import numpy as np

from tools.vec_same_entry_exit_adapter import (
    StaticExitBook,
    WtMtfParams,
    _entry_schedule_hash,
    build_wt_mtf_book,
)


class FakeZ:
    def __init__(self, rows):
        self.rows = rows
        self.files = list(rows)

    def __getitem__(self, key):
        return self.rows[key]


def _fake_data(n=12):
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    return SimpleNamespace(
        symbol="TEST",
        ts=ts,
        high=np.linspace(10, 12, n),
        low=np.linspace(9, 11, n),
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ(
            {
                "wt1_1h": np.array([0, 10, 30, 70, 60, 40, 20, 10, 0, 0, 0, 0], float),
                "wt2_1h": np.array([0, 5, 20, 60, 62, 50, 30, 20, 10, 0, 0, 0], float),
            }
        ),
    )


def test_wt_mtf_is_edge_triggered_and_causal():
    data = _fake_data()
    htf = SimpleNamespace(
        event_index=np.arange(12, dtype=np.int64),
        source_ts=data.ts.copy(),
        high=data.high.copy(),
        low=data.low.copy(),
        close=(data.high + data.low) / 2,
    )
    book = build_wt_mtf_book(
        data,
        {"1h": htf},
        WtMtfParams(
            timeframes=("1h",),
            min_against_tfs=1,
            extreme=65,
            velocity=0,
            recent_extreme_bars=4,
        ),
        side="LONG",
    )
    rows = np.flatnonzero(book.events)
    assert rows.tolist() == [4]
    decision = book.update(4, active=True)
    assert decision is not None
    assert decision.source_timestamps["1h"] <= data.ts[4]
    assert book.update(5, active=True) is None


def test_static_book_never_crosses_side_or_fires_flat():
    events = np.array([0, 1, 0], dtype=np.uint8)
    refs = np.array([math.nan, 11.0, math.nan])
    book = StaticExitBook("TEST_EXIT", events, refs, {1: {"1h": 600}})
    assert book.update(1, active=False) is None
    assert book.update(1, active=True).reason == "TEST_EXIT"


def test_entry_schedule_hash_changes_only_when_entry_schedule_changes():
    data = SimpleNamespace(ts=np.array([1, 2, 3], dtype=np.int64))
    curve = SimpleNamespace()
    # dataclasses.asdict needs an actual ladder curve.
    from tools.vec_band_ladder_walkforward import Curve, SignalData

    curve = Curve("x", "linear", "green", "target", 30, 8, 4, 6, 3, 3, 1)
    first = SignalData(
        entry_mult=np.array([0.0, 2.0, 0.0]),
        event_tf=np.zeros((3, 3), dtype=np.uint8),
        exit_event=np.zeros(3, dtype=np.uint8),
        exit_ref=np.full(3, np.nan),
        causality={},
    )
    second = SignalData(
        entry_mult=np.array([0.0, 3.0, 0.0]),
        event_tf=first.event_tf,
        exit_event=first.exit_event,
        exit_ref=first.exit_ref,
        causality={},
    )
    assert _entry_schedule_hash(data, first, curve, 0, 3) == _entry_schedule_hash(
        data, first, curve, 0, 3
    )
    assert _entry_schedule_hash(data, first, curve, 0, 3) != _entry_schedule_hash(
        data, second, curve, 0, 3
    )

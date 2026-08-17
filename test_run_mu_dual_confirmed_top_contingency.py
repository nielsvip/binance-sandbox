from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from tools import run_mu_dual_confirmed_top_contingency as target
from tools import run_top_exit_reclaim_phase3 as phase3
from tools import vec_same_entry_exit_adapter as shared


def _registered(label, events, references, sources):
    return phase3.RegisteredBook(
        "TEST",
        label,
        {},
        shared.StaticExitBook(
            label,
            np.asarray(events, dtype=np.uint8),
            np.asarray(references, dtype=np.float64),
            sources,
        ),
        0.5,
    )


def test_dual_book_requires_second_family_inside_elapsed_window():
    data = SimpleNamespace(
        ts=np.asarray([0, 3600, 7200, 10800, 200000], dtype=np.int64),
        high=np.asarray([10, 11, 12, 13, 14], dtype=np.float64),
    )
    left = _registered(
        "left", [0, 1, 0, 0, 0], [np.nan, 12, np.nan, np.nan, np.nan],
        {1: {"4h": 3000}},
    )
    right = _registered(
        "right", [0, 0, 0, 1, 1], [np.nan, np.nan, np.nan, 13, 14],
        {3: {"1h": 10000}, 4: {"1h": 199000}},
    )
    result = target.dual_confirm_book(data, left, right, 24)
    assert result.book.events.tolist() == [0, 0, 0, 1, 0]
    assert result.book.references[3] == 13
    assert result.book.source_by_row[3] == {"4h": 3000, "1h": 10000}


def test_dual_book_is_symmetric_in_arrival_order():
    data = SimpleNamespace(
        ts=np.asarray([0, 3600, 7200], dtype=np.int64),
        high=np.asarray([10, 11, 12], dtype=np.float64),
    )
    left = _registered(
        "left", [0, 0, 1], [np.nan, np.nan, 12], {2: {"4h": 7000}}
    )
    right = _registered(
        "right", [0, 1, 0], [np.nan, 11, np.nan], {1: {"D": 3000}}
    )
    result = target.dual_confirm_book(data, left, right, 24)
    assert result.book.events.tolist() == [0, 0, 1]
    assert result.book.source_by_row[2] == {"D": 3000, "4h": 7000}

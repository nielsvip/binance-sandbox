"""Coarse sweep: prior-ranked per-row yellow cap (10-min worst_first)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import v15_pilot as P


def test_trim_keeps_top_by_evidence():
    kept = ["A=1", "B=2", "C=3", "D=4"]
    ev = {"A=1": (0, 5), "B=2": (4, 10), "C=3": (4, 3), "D=4": (1, 1)}
    trimmed, deferred = P._coarse_trim(kept, ev, 2)
    assert trimmed == ["B=2", "C=3"]
    assert deferred == ["D=4", "A=1"]


def test_trim_stable_ties_and_passthrough():
    kept = ["A=1", "B=2", "C=3"]
    ev = {"A=1": (2, 2), "B=2": (2, 2), "C=3": (2, 2)}
    trimmed, deferred = P._coarse_trim(kept, ev, 2)
    assert trimmed == ["A=1", "B=2"]
    assert deferred == ["C=3"]
    t2, d2 = P._coarse_trim(kept, ev, 5)
    assert t2 == kept and d2 == []
    t3, d3 = P._coarse_trim(kept, ev, 0)
    assert t3 == kept and d3 == []


def test_trim_missing_evidence_sorts_last():
    kept = ["A=1", "B=2"]
    trimmed, deferred = P._coarse_trim(kept, {"B=2": (3, 3)}, 1)
    assert trimmed == ["B=2"]
    assert deferred == ["A=1"]

"""select_latest completeness exemption (USER 2026-10-09): a converged complete sweep (done_n >= 2000) is exempt from the real-cell floor — its zeros are verdicts, not gaps. Without this, run29's verified boards (real 35-88) lost to stale exploratory files (real ~291)."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
from v15_vector_delta_rebuild import (
    DONE_COMPLETE_N,
    FIX_CUTOFF_EPOCH,
    scan_file,
    select_latest,
)

T0 = FIX_CUTOFF_EPOCH + 1000000


def _row(real, mtime, done_n, host="s1"):
    return ("A_LONG", real, 1, 0, T0 + mtime, f"/p/{host}/A_LONG.json", host, done_n)


def test_converged_new_beats_rich_old():
    old = _row(291, 0, 3038, "s2")
    new = _row(72, 500000, 3521, "s1")
    assert DONE_COMPLETE_N == 2000
    assert select_latest([old, new])["A_LONG"][6] == "s1"


def test_sparse_new_still_falls_back():
    old = _row(291, 0, 3038, "s2")
    new = _row(3, 500000, 50, "s1")
    assert select_latest([old, new])["A_LONG"][6] == "s2"


def test_floor_still_applies_below_completeness():
    a = _row(100, 0, 500, "s2")
    b = _row(10, 500000, 500, "s1")
    assert select_latest([a, b])["A_LONG"][6] == "s2"


def test_legacy_7tuples_still_work():
    old = ("A_LONG", 291, 1, 0, T0, "/p/s2/A.json", "s2")
    new = ("A_LONG", 72, 1, 0, T0 + 500000, "/p/s1/A.json", "s1")
    assert select_latest([old, new])["A_LONG"][6] == "s2"


def test_scan_file_reports_done_n(tmp_path):
    p = tmp_path / "X_LONG_v14_progress.json"
    done = {f"c{i}": {"delta": 0.0 if i % 2 else 0.5} for i in range(10)}
    p.write_text(json.dumps({"initial_baseline_gain": 1.0, "done": done}))
    ss, real, is56, fg, done_n = scan_file(str(p))
    assert (ss, real, is56, done_n) == ("X_LONG", 5, 1, 10)

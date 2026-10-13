"""V16 no-wipe REDO: promoted rows with only uncalculated yellow cells are settled (chain_passed), never wiped."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import v15_pilot as P
from tools.v15_row_guards import row_needs_recalc


def _done():
    return {
        "TAB!3:A=1": {"promoted": True, "delta": 0.5, "complete": True, "sampled_out_filters": ["F=1", "G=2"]},
        "TAB!4:B=1": {"promoted": True, "delta": 0.2, "complete": False, "missing_yellows": ["H=3"]},
        "TAB!5:C=1": {"promoted": False, "delta": -0.1, "complete": True, "sampled_out_filters": ["F=1"]},
        "TAB!6:D=1": {"promoted": True, "delta": None, "complete": False},
        "TAB!7:E=1": {"promoted": True, "delta": 0.3, "complete": True, "tab_level_excluded": ["X=1"]},
        "TAB!8:F=1": {"promoted": True, "delta": 0.1, "complete": False, "missing_yellows": ["Q=1"], "reason": "RAMFP-MISMATCH: x"},
    }


def _reasons(done):
    return {k: row_needs_recalc(v, key=k) for k, v in done.items() if row_needs_recalc(v, key=k)}


def test_promoted_measured_rows_settle_and_stop_being_hollow():
    done = _done()
    rs = _reasons(done)
    settled = P._settle_promoted_holes(done, list(rs), rs)
    assert sorted(settled) == ["TAB!3:A=1", "TAB!4:B=1"]
    assert done["TAB!3:A=1"]["chain_passed_filters"] == ["F=1", "G=2"]
    assert done["TAB!4:B=1"]["chain_passed_filters"] == ["H=3"]
    for k in settled:
        assert row_needs_recalc(done[k], key=k) is None
        assert done[k]["complete"] is True
        assert done[k]["delta"] is not None  # the measured G is kept, never rewritten


def test_unmeasured_nonpromoted_tablevel_ramfp_untouched():
    done = _done()
    rs = _reasons(done)
    P._settle_promoted_holes(done, list(rs), rs)
    assert "chain_passed_filters" not in done["TAB!5:C=1"]  # not promoted: spliced/refilled normally
    assert "chain_passed_filters" not in done["TAB!6:D=1"]  # no measured delta: still a real hole
    assert "chain_passed_filters" not in done["TAB!7:E=1"]  # tab-level era: untouched
    assert "chain_passed_filters" not in done["TAB!8:F=1"]  # RAMFP-stale: revalidates on resume
    assert row_needs_recalc(done["TAB!6:D=1"], key="TAB!6:D=1") is not None


def test_idempotent_and_safe_on_garbage():
    done = _done()
    rs = _reasons(done)
    P._settle_promoted_holes(done, list(rs), rs)
    rs2 = _reasons(done)  # callers always re-scan: settled rows are no longer holes
    assert P._settle_promoted_holes(done, list(rs2), rs2) == []
    before = {k: dict(v) for k, v in done.items()}
    P._settle_promoted_holes(done, list(rs), rs)  # even a stale scan changes nothing further
    assert {k: dict(v) for k, v in done.items()} == before
    assert P._settle_promoted_holes(None, ["x"], {}) == []
    assert P._settle_promoted_holes({"k": "corrupt"}, ["k"], {"k": "corrupt-record"}) == []

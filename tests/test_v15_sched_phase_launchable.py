"""USER 2026-10-15 (06.3): _venue_phase counts ONLY really-launchable stock sides.

Regression: pinned/redo stock sides counted as launchable -> crypto blocked in ~11%
of ticks. _phase_launchable skips exhausted (incl. the operator-pin cap) and
soft-skipped (quarantined terminal verdicts the pilot refuses) sides.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_fleet_scheduler as S


def _st(attempts=None, slices=None):
    return {"attempts": dict(attempts or {}), "slices": dict(slices or {})}


def test_fresh_side_launchable():
    assert S._phase_launchable("AAPL_LONG", _st(), set(), set()) is True


def test_attempt_exhausted_not_launchable():
    st = _st(attempts={"AAPL_LONG|30D": 3})
    assert S._phase_launchable("AAPL_LONG", st, set(), set()) is False


def test_slice_exhausted_started_side_not_launchable():
    st = _st(slices={"AAPL_LONG|30D": 20})
    assert S._phase_launchable("AAPL_LONG", st, {"AAPL_LONG"}, set()) is False


def test_redo_side_always_launchable():
    st = _st(attempts={"AAPL_LONG|30D": 99})
    assert S._phase_launchable("AAPL_LONG", st, set(), {"AAPL_LONG"}) is True


def test_pinned_redo_over_pin_cap_not_launchable():
    st = _st(attempts={"AAPL_LONG|30D": 39}, slices={"AAPL_LONG|30D": 1})
    assert S._phase_launchable("AAPL_LONG", st, set(), {"AAPL_LONG"}, pinned={"AAPL_LONG"}) is False


def test_pinned_side_under_cap_launchable():
    st = _st(attempts={"AAPL_LONG|30D": 3})
    assert S._phase_launchable("AAPL_LONG", st, set(), set(), pinned={"AAPL_LONG"}) is True


def test_quarantined_softskip_never_holds_phase():
    st = _st()
    assert S._phase_launchable("AAPL_LONG", st, set(), set(), quar={"AAPL_LONG"}) is False
    assert S._phase_launchable("AAPL_LONG", st, set(), {"AAPL_LONG"}, quar={"AAPL_LONG"}, pinned={"AAPL_LONG"}) is False


def test_legacy_mode_restores_old_callback():
    old = os.environ.get("V15_SCHED_PHASE_STRICT")
    os.environ["V15_SCHED_PHASE_STRICT"] = "0"
    try:
        st = _st(attempts={"AAPL_LONG|30D": 39}, slices={"AAPL_LONG|30D": 1})
        assert S._phase_launchable("AAPL_LONG", st, set(), {"AAPL_LONG"}, quar={"AAPL_LONG"}, pinned={"AAPL_LONG"}) is True
    finally:
        if old is None:
            os.environ.pop("V15_SCHED_PHASE_STRICT", None)
        else:
            os.environ["V15_SCHED_PHASE_STRICT"] = old


def test_phase_end_to_end_with_predicate():
    cs = {"AAPL": {"LONG": ("need30", 1), "SHORT": ("terminal_ok", None)}}
    st = _st(slices={"AAPL_LONG|30D": 40})
    phase, pend = S._venue_phase(cs, mode="auto", launchable=lambda s, sd: S._phase_launchable(f"{s}_{sd}", st, set(), {"AAPL_LONG"}, set(), {"AAPL_LONG"}))
    assert phase == "any" and pend == []

"""USER 2026-10-12: sliced-board resume budget — 40-min pilot slices draw 20 slice
resumes, not the 3 fresh-attempt budget (BG_SHORT stalled 23+ min at 3/3 with
1769/3383 rows done: slices are resumes, not fresh attempts)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import v15_fleet_scheduler as S


def test_fresh_side_attempts_cap_unchanged():
    st = {"attempts": {"A|30D": 3}, "slices": {}}
    assert S._launch_exhausted("A|30D", "30D", "A", st, set(), set()) is True
    st = {"attempts": {"A|30D": 2}, "slices": {}}
    assert S._launch_exhausted("A|30D", "30D", "A", st, set(), set()) is False


def test_started_side_draws_slices_not_attempts():
    st = {"attempts": {"BG_SHORT|30D": 3}, "slices": {}}
    assert S._launch_exhausted("BG_SHORT|30D", "30D", "BG_SHORT", st, {"BG_SHORT"}, set()) is False
    st = {"attempts": {"BG_SHORT|30D": 3}, "slices": {"BG_SHORT|30D": 20}}
    assert S._launch_exhausted("BG_SHORT|30D", "30D", "BG_SHORT", st, {"BG_SHORT"}, set()) is True
    st = {"attempts": {"BG_SHORT|30D": 9}, "slices": {"BG_SHORT|30D": 5}}
    assert S._launch_exhausted("BG_SHORT|30D", "30D", "BG_SHORT", st, {"BG_SHORT"}, set()) is False


def test_redo_and_other_windows_untouched():
    st = {"attempts": {"R|30D": 99}, "slices": {"R|30D": 99}}
    assert S._launch_exhausted("R|30D", "30D", "R", st, {"R"}, {"R"}) is False
    st = {"attempts": {"C|365D": 2}, "slices": {}}
    assert S._launch_exhausted("C|365D", "365D", "C", st, {"C"}, set()) is True
    st = {"attempts": {"C|365D": 1}, "slices": {}}
    assert S._launch_exhausted("C|365D", "365D", "C", st, {"C"}, set()) is False


def test_burn_routes_to_right_budget():
    st = {"attempts": {}, "slices": {}}
    S._burn_launch("N|30D", "30D", "N", st, set())
    assert st == {"attempts": {"N|30D": 1}, "slices": {}}
    S._burn_launch("BG_SHORT|30D", "30D", "BG_SHORT", st, {"BG_SHORT"})
    assert st["slices"] == {"BG_SHORT|30D": 1}
    assert st["attempts"] == {"N|30D": 1}
    S._burn_launch("C|365D", "365D", "C", st, {"C"})
    assert st["attempts"]["C|365D"] == 1

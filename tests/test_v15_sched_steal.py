"""Scheduler pass-2 stealing (USER 2026-10-08): pure-helper contracts."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from v15_fleet_scheduler import _fetch_specs, _held_host, _repair_sticky_ok


class HeldHostTest(unittest.TestCase):
    def test_running_wins_over_owner(self):
        running = {"AAA_LONG": {"host": "s5"}}
        self.assertEqual(_held_host("AAA", {"AAA": "s1"}, running), "s5")

    def test_owner_when_idle(self):
        self.assertEqual(_held_host("AAA", {"AAA": "s1"}, {}), "s1")

    def test_none_when_unknown(self):
        self.assertIsNone(_held_host("AAA", {}, {}))


class FetchSpecsTest(unittest.TestCase):
    def test_365d_and_a1_fetch_progress(self):
        acts = [{"sym": "AAA", "side": "LONG", "window": "365D", "attempt": 1},
                {"sym": "AAA", "side": "SHORT", "window": "REPAIR", "attempt": 1}]
        out = _fetch_specs("AAA", acts, "/thief/pdir", {"AAA_LONG": "s1", "AAA_SHORT": "s1"}, {"s1": "/owner/pdir"})
        self.assertEqual(out, [("s1", "/owner/pdir/AAA_LONG_v14_progress.json", "/thief/pdir/AAA_LONG_v14_progress.json"),
                               ("s1", "/owner/pdir/AAA_SHORT_v14_progress.json", "/thief/pdir/AAA_SHORT_v14_progress.json")])

    def test_a2_and_gs_never_fetch(self):
        acts = [{"sym": "AAA", "side": "LONG", "window": "REPAIR", "attempt": 2},
                {"sym": "AAA", "side": "SHORT", "window": "GS", "attempt": 1}]
        self.assertEqual(_fetch_specs("AAA", acts, "/thief/pdir", {"AAA_LONG": "s1"}, {"s1": "/p"}), [])

    def test_missing_source_fails_closed(self):
        acts = [{"sym": "AAA", "side": "LONG", "window": "365D", "attempt": 1}]
        self.assertIsNone(_fetch_specs("AAA", acts, "/thief/pdir", {}, {"s1": "/p"}))
        self.assertIsNone(_fetch_specs("AAA", acts, "/thief/pdir", {"AAA_LONG": "s1"}, {}))


class RepairStickyTest(unittest.TestCase):
    def test_a1_free_a2_sticky(self):
        self.assertTrue(_repair_sticky_ok({"sym": "A", "side": "L", "window": "REPAIR", "attempt": 1}, {}, "s5"))
        self.assertTrue(_repair_sticky_ok({"sym": "A", "side": "L", "window": "REPAIR", "attempt": 2}, {("A_L", 1): "s5"}, "s5"))
        self.assertFalse(_repair_sticky_ok({"sym": "A", "side": "L", "window": "REPAIR", "attempt": 2}, {("A_L", 1): "s1"}, "s5"))

    def test_non_repair_free(self):
        self.assertTrue(_repair_sticky_ok({"sym": "A", "side": "L", "window": "365D", "attempt": 1}, {}, "s5"))


if __name__ == "__main__":
    unittest.main()

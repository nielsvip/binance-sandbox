"""Preferred cat_side per server, with both-side overflow while underloaded."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import v15_fleet_scheduler as _SCHED


class HostSidesTest(unittest.TestCase):
    def test_pinned_long(self):
        self.assertEqual(_SCHED._host_sides({"name": "s1", "sides": ["LONG"]}), {"LONG"})

    def test_pinned_short(self):
        self.assertEqual(_SCHED._host_sides({"name": "s6", "sides": ["SHORT"]}), {"SHORT"})

    def test_absent_means_both(self):
        self.assertEqual(_SCHED._host_sides({"name": "s1"}), {"LONG", "SHORT"})

    def test_empty_means_both(self):
        self.assertEqual(_SCHED._host_sides({"name": "s1", "sides": []}), {"LONG", "SHORT"})

    def test_name_lookup(self):
        hosts = [{"name": "s5", "sides": ["LONG"]}, {"name": "s6", "sides": ["SHORT"]}]
        self.assertEqual(_SCHED._host_sides("s5", hosts), {"LONG"})
        self.assertEqual(_SCHED._host_sides("s6", hosts), {"SHORT"})
        self.assertEqual(_SCHED._host_sides("s9", hosts), {"LONG", "SHORT"})

    def test_junk_fail_open(self):
        self.assertEqual(_SCHED._host_sides({"name": "s1", "sides": ["SIDEWAYS"]}), {"LONG", "SHORT"})
        self.assertEqual(_SCHED._host_sides({"name": "s1", "sides": "LONG"}), {"LONG", "SHORT"})
        self.assertEqual(_SCHED._host_sides(None), {"LONG", "SHORT"})

    def test_case_normalized(self):
        self.assertEqual(_SCHED._host_sides({"name": "s1", "sides": ["long"]}), {"LONG"})

    def test_underloaded_host_opens_both_sides_and_other_venue(self):
        host = {"name": "s5", "venues": ["stocks"], "sides": ["LONG"],
                "_runtime_overflow": True, "_effective_sides": ["LONG", "SHORT"]}
        self.assertEqual(_SCHED._host_sides(host), {"LONG", "SHORT"})
        self.assertIn("crypto", _SCHED._host_overflow_venues(host))


if __name__ == "__main__":
    unittest.main()

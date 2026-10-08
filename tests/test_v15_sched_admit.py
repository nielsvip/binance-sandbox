"""Scheduler admission honesty (USER 2026-10-08): no more OOM murder loops."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from v15_fleet_scheduler import _est_pair_mb, _progress_done_rows, _rank_oom_victim, _reap_attempt_keys, _stall_tick, _swap_admit_ok


class EstPairTest(unittest.TestCase):
    def test_floor_rules_when_unmeasured(self):
        self.assertEqual(_est_pair_mb([], 12000), 12000)
        self.assertEqual(_est_pair_mb(None, 12000), 12000)
        self.assertEqual(_est_pair_mb([3000, 4000], 12000), 12000)

    def test_max_times_growth_when_big(self):
        self.assertEqual(_est_pair_mb([8000, 13000], 12000), 13000 * 1.25)
        self.assertEqual(_est_pair_mb([13000], 4000), 13000 * 1.25)

    def test_junk_safe(self):
        self.assertEqual(_est_pair_mb(["x", None], 12000), 12000)
        self.assertEqual(_est_pair_mb([5000], "bogus"), 12000)


class SwapAdmitTest(unittest.TestCase):
    def test_no_swap_ok(self):
        self.assertTrue(_swap_admit_ok({"swap_total_mb": 0, "swap_used_mb": 0}))
        self.assertTrue(_swap_admit_ok({}))

    def test_threshold(self):
        self.assertTrue(_swap_admit_ok({"swap_total_mb": 8000, "swap_used_mb": 3000}))
        self.assertFalse(_swap_admit_ok({"swap_total_mb": 8000, "swap_used_mb": 5000}))

    def test_junk_safe(self):
        self.assertTrue(_swap_admit_ok(None))
        self.assertTrue(_swap_admit_ok({"swap_total_mb": "x"}))


class VictimRankTest(unittest.TestCase):
    def test_least_progress_wins(self):
        c = [("A_LONG", 3249, 1200), ("A_SHORT", 711, 1200), ("B_LONG", 50, 300)]
        self.assertEqual(_rank_oom_victim(c), "B_LONG")

    def test_unknown_young_counts_zero(self):
        c = [("A_LONG", 3249, 1200), ("NEW_SIDE", None, 200)]
        self.assertEqual(_rank_oom_victim(c), "NEW_SIDE")

    def test_unknown_old_sorts_last(self):
        c = [("A_LONG", 3249, 1200), ("OLD365", None, 7200)]
        self.assertEqual(_rank_oom_victim(c), "A_LONG")

    def test_tie_breaks_youngest(self):
        c = [("A_LONG", 100, 1200), ("B_LONG", 100, 300)]
        self.assertEqual(_rank_oom_victim(c), "B_LONG")

    def test_empty_and_junk(self):
        self.assertIsNone(_rank_oom_victim([]))
        self.assertIsNone(_rank_oom_victim(None))
        self.assertIsNone(_rank_oom_victim([("X", "bogus", "bogus"), None, 42]))


class DoneRowsTest(unittest.TestCase):
    def test_count_and_missing(self):
        p = Path("/tmp/test_sched_admit_prog.json")
        p.write_text(json.dumps({"done": {"a": 1, "b": 2}}))
        self.assertEqual(_progress_done_rows(str(p)), 2)
        self.assertIsNone(_progress_done_rows("/tmp/test_sched_admit_nope.json"))
        p.write_text("not json{{{")
        self.assertIsNone(_progress_done_rows(str(p)))
        p.unlink()


class ReapKeysTest(unittest.TestCase):
    def test_nonorphan_consumes_last_act(self):
        acts = [{"ss": "KSMUSDT_SHORT", "why": "wedged: blah (TERM)", "pids": [1]},
                {"ss": "X_LONG", "why": "orphaned workers (parent gone)", "pids": [2]}]
        self.assertEqual(_reap_attempt_keys(acts, {"KSMUSDT_SHORT": "KSMUSDT_SHORT|30D", "X_LONG": "X_LONG|30D"}), ["KSMUSDT_SHORT|30D"])

    def test_missing_last_act_fails_open(self):
        acts = [{"ss": "KSMUSDT_SHORT", "why": "wedged (TERM)", "pids": [1]}]
        self.assertEqual(_reap_attempt_keys(acts, {}), [])
        self.assertEqual(_reap_attempt_keys([], {"A": "B"}), [])


class StallTickTest(unittest.TestCase):
    def test_counts_and_warns(self):
        self.assertEqual(_stall_tick(188, 59, 188), (60, True))
        self.assertEqual(_stall_tick(188, 10, 188), (11, False))

    def test_resets_on_change(self):
        self.assertEqual(_stall_tick(188, 59, 187), (0, False))
        self.assertEqual(_stall_tick(None, None, 188), (0, False))


if __name__ == "__main__":
    unittest.main()

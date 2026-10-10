"""Scheduler admission honesty (USER 2026-10-08): no more OOM murder loops."""

import datetime
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from v15_fleet_scheduler import _disk_admit_ok, _est_pair_mb, _growth_debt_mb, _progress_done_rows, _rank_oom_victim, _reap_burn_weight, _reap_burns, _stall_tick, _swap_admit_ok, tick


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


class GrowthDebtTest(unittest.TestCase):
    def test_young_pairs_count(self):
        self.assertEqual(_growth_debt_mb({"a": 3000, "b": 4000}, 12000), 17000)
        self.assertEqual(_growth_debt_mb([2000], 12000), 10000)

    def test_mature_pairs_exempt(self):
        self.assertEqual(_growth_debt_mb({"a": 6000, "b": 12000}, 12000), 0)
        self.assertEqual(_growth_debt_mb({}, 12000), 0)

    def test_junk_safe(self):
        self.assertEqual(_growth_debt_mb(None, 12000), 0)
        self.assertEqual(_growth_debt_mb({"a": "x", "b": -5}, 12000), 0)
        self.assertEqual(_growth_debt_mb({"a": 3000}, "bogus"), 0)


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


class DiskAdmitTest(unittest.TestCase):
    def test_missing_fails_open(self):
        self.assertTrue(_disk_admit_ok({}))
        self.assertTrue(_disk_admit_ok(None))
        self.assertTrue(_disk_admit_ok({"disk_avail_mb": None}))

    def test_threshold(self):
        self.assertTrue(_disk_admit_ok({"disk_avail_mb": 54000}))
        self.assertTrue(_disk_admit_ok({"disk_avail_mb": 5000}))
        self.assertFalse(_disk_admit_ok({"disk_avail_mb": 4999}))
        self.assertFalse(_disk_admit_ok({"disk_avail_mb": 0}))
        self.assertFalse(_disk_admit_ok({"disk_avail_mb": 797}))

    def test_junk_safe(self):
        self.assertTrue(_disk_admit_ok({"disk_avail_mb": "x"}))
        self.assertTrue(_disk_admit_ok(42))


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


class ReapBurnTest(unittest.TestCase):
    def test_stall_wedge_hardcap_burn_3_on_30d(self):
        self.assertEqual(_reap_burn_weight("wedged: blah (TERM)", "KSMUSDT_SHORT|30D"), 3)
        self.assertEqual(_reap_burn_weight("stuck: no progress (TERM)", "A_LONG|30D"), 3)
        self.assertEqual(_reap_burn_weight("hardcap 480min (TERM)", "A_LONG|30D"), 3)

    def test_oom_and_chains_burn_1(self):
        self.assertEqual(_reap_burn_weight("OOM guard: blah, least-progress victim (TERM)", "A_LONG|30D"), 1)
        self.assertEqual(_reap_burn_weight("wedged: blah (TERM)", "A_LONG|365D"), 1)
        self.assertEqual(_reap_burn_weight("stuck: blah (TERM)", "A_LONG|REPAIR1"), 1)

    def test_orphan_burns_0_and_junk_safe(self):
        self.assertEqual(_reap_burn_weight("orphaned workers (parent gone)", "X_LONG|30D"), 0)
        self.assertEqual(_reap_burn_weight(None, None), 1)
        self.assertEqual(_reap_burn_weight(42, 42), 1)


class ReapBurnsTest(unittest.TestCase):
    def test_dedupes_per_pid_entries(self):
        acts = [{"ss": "NMRUSDT_LONG", "why": "OOM guard: x (TERM)", "pids": [1]} for _ in range(7)]
        self.assertEqual(_reap_burns(acts, {"NMRUSDT_LONG": "NMRUSDT_LONG|30D"}), {"NMRUSDT_LONG|30D": 1})

    def test_two_sides_burn_separately(self):
        acts = [{"ss": "A_LONG", "why": "wedged: x (TERM)", "pids": [1]},
                {"ss": "A_LONG", "why": "wedged: x (TERM)", "pids": [2]},
                {"ss": "B_LONG", "why": "OOM guard: x (TERM)", "pids": [3]}]
        la = {"A_LONG": "A_LONG|30D", "B_LONG": "B_LONG|30D"}
        self.assertEqual(_reap_burns(acts, la), {"A_LONG|30D": 3, "B_LONG|30D": 1})

    def test_missing_last_act_skipped(self):
        acts = [{"ss": "A_LONG", "why": "wedged: x (TERM)", "pids": [1]}]
        self.assertEqual(_reap_burns(acts, {}), {})
        self.assertEqual(_reap_burns([], {"A_LONG": "A_LONG|30D"}), {})
        self.assertEqual(_reap_burns(None, None), {})


class StallTickTest(unittest.TestCase):
    def test_counts_and_warns(self):
        self.assertEqual(_stall_tick(188, 59, 188), (60, True))
        self.assertEqual(_stall_tick(188, 10, 188), (11, False))

    def test_resets_on_change(self):
        self.assertEqual(_stall_tick(188, 59, 187), (0, False))
        self.assertEqual(_stall_tick(None, None, 188), (0, False))


class DiskGuardWiringTest(unittest.TestCase):
    """USER 2026-10-10: the disk guard must hold at EVERY launch site (main loop,
    chains, backfill, pass-2 steal) — a full disk idles the host, never relaunches."""

    def _run_tick(self, disk):
        uni = json.loads((ROOT / "data" / "daily_universe" / "20261010.json").read_text())
        syms = uni["crypto"][:20] + uni["stocks"][:6]
        stats = {"nproc": 8, "load1": 1.0, "busy_pct": 10.0, "busy_nonnice_pct": 10.0, "mem_avail_mb": 60000, "pdir": "/home/niels/v15_run30_20261009/progress", "swap_total_mb": 0, "swap_used_mb": 0, "running": {}, "started": [], "done": [], "quarantined": [], "redo": [], "v365": {}, "repair": {}, "gs": {}, "gains": {}}
        if disk != "MISSING":
            stats["disk_avail_mb"] = disk
        ready = {s: {"ok": True, "size_mb": 0} for s in syms}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sim_p = str(Path(tmp.name) / "sim.json")
        json.dump({"hosts": {"gatetest": stats}, "ready": {"gatetest": ready}}, open(sim_p, "w"))
        args = types.SimpleNamespace(simulate=sim_p, dry_run=False, no_reap=True, max_launch=2, extend_universe=False)
        cfg = {"cpu_target_pct": 90, "pair_floor_mb": 8000, "hosts": [{"name": "gatetest", "ssh": ["127.0.0.1"], "root": "~/binance-sandbox", "venues": ["stocks", "crypto"], "mem_reserve_mb": 6000, "max_pairs": 2, "workers_per_side": 4, "oom_mb": 2000}]}
        now = datetime.datetime(2026, 10, 10, 8, 30, tzinfo=datetime.timezone.utc)
        return tick(args, cfg, now)

    def test_healthy_disk_not_blocked(self):
        log = self._run_tick(54000)
        self.assertNotEqual(log["hosts"]["gatetest"].get("idle_reason"), "disk guard")

    def test_low_disk_blocks_all_launch_paths(self):
        log = self._run_tick(797)
        h = log["hosts"]["gatetest"]
        self.assertEqual(h.get("launched_pairs"), 0)
        self.assertEqual(h.get("idle_reason"), "disk guard")
        self.assertEqual([t for t in log.get("launched", []) if t.startswith("gatetest:") or t.startswith("BACKFILL gatetest:")], [])

    def test_missing_disk_fails_open(self):
        log = self._run_tick("MISSING")
        self.assertNotEqual(log["hosts"]["gatetest"].get("idle_reason"), "disk guard")


if __name__ == "__main__":
    unittest.main()

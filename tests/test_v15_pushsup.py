"""Supervisor orchestrator-grade guards (USER 2026-10-10): disk gate, stuck reap, log rotation."""
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import v15_pusher_supervisor as S


class DiskAdmitTest(unittest.TestCase):
    def test_missing_path_fails_open(self):
        self.assertTrue(S._disk_admit_ok("/nonexistent-pushsup-xyz", 5000))

    def test_tmpfs_admits(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(S._disk_admit_ok(d, 1))
            self.assertFalse(S._disk_admit_ok(d, 10 ** 12))


class StuckUnitsTest(unittest.TestCase):
    def test_fresh_unit_not_stuck(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            (r / "u1.json").write_text("{}")
            self.assertEqual(S._stuck_units(r, time.time()), [])

    def test_old_unit_without_pid_flagged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            f = r / "u1.json"
            f.write_text("{}")
            old = time.time() - 46 * 60
            os.utime(f, (old, old))
            got = S._stuck_units(r, time.time())
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0][1], 0)

    def test_tmp_files_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            f = r / "tmp_x"
            f.write_text("{}")
            old = time.time() - 99 * 60
            os.utime(f, (old, old))
            self.assertEqual(S._stuck_units(r, time.time()), [])


class LogsPruneTest(unittest.TestCase):
    def test_under_keep_prunes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for i in range(3):
                (Path(d) / f"worker_20261010_0{i}.log").write_text("x")
            self.assertEqual(S._logs_to_prune(Path(d), keep=200), [])

    def test_over_keep_prunes_oldest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            for i in range(5):
                f = r / f"worker_20261010_0{i}.log"
                f.write_text("x")
                os.utime(f, (1000 + i, 1000 + i))
            got = S._logs_to_prune(r, keep=2)
            self.assertEqual([p.name for p in got], ["worker_20261010_00.log", "worker_20261010_01.log", "worker_20261010_02.log"])


if __name__ == "__main__":
    unittest.main()

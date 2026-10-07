"""Collect-dedupe winner selection (run24 collect died on synced-copy dupes). Fast, stdlib-only."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.v15_autopilot as AP


def _hosts(*names):
    return [{"name": n, "ssh": [n], "root": "~/binance-sandbox"} for n in names]


class DedupeTest(unittest.TestCase):
    def _run(self, stat_out, names=("s1", "s2", "s5"), rc=0):
        old = AP.rsh
        AP.rsh = lambda h, cmd, timeout=60: (rc, stat_out.get(h["name"], ""))
        try:
            hpdirs = [(h, "/home/niels/v15_run25_20261002/progress") for h in _hosts(*names)]
            return AP._collect_select_winners(hpdirs)
        finally:
            AP.rsh = old

    def test_size_wins_over_mtime(self):
        out, dropped = self._run({
            "s1": "1249528 1790991120 /home/niels/v15_run25_20261002/progress/PEPE_LONG_v14_progress.json\n",
            "s2": "13941233 1790990820 /home/niels/v15_run25_20261002/progress/PEPE_LONG_v14_progress.json\n",
            "s5": "",
        }, names=("s1", "s2"))
        self.assertEqual(len(out["s2"]), 1)
        self.assertEqual(out["s1"], [])
        self.assertEqual(dropped, [("PEPE_LONG", "s2", "s1")])

    def test_mtime_breaks_size_tie(self):
        out, dropped = self._run({
            "s1": "9054700 1790993068 /home/niels/v15_run25_20261002/progress/NOC_LONG_v14_progress.json\n",
            "s2": "9054700 1790993070 /home/niels/v15_run25_20261002/progress/NOC_LONG_v14_progress.json\n",
        }, names=("s1", "s2"))
        self.assertEqual(len(out["s2"]), 1)
        self.assertEqual(out["s1"], [])

    def test_host_order_breaks_full_tie(self):
        line = "9054700 1790993068 /home/niels/v15_run25_20261002/progress/NOC_LONG_v14_progress.json\n"
        out, dropped = self._run({"s1": line, "s2": line, "s5": line})
        self.assertEqual(len(out["s1"]), 1)
        self.assertEqual(out["s2"], [])
        self.assertEqual(out["s5"], [])
        self.assertEqual(sorted(d[2] for d in dropped), ["s2", "s5"])

    def test_unique_syms_pass_through(self):
        out, dropped = self._run({
            "s1": "100 1790993000 /p/A_LONG_v14_progress.json\n",
            "s2": "200 1790993000 /p/B_SHORT_v14_progress.json\n",
        }, names=("s1", "s2"))
        self.assertEqual(len(out["s1"]), 1)
        self.assertEqual(len(out["s2"]), 1)
        self.assertEqual(dropped, [])

    def test_rsh_failure_fails_open(self):
        out, dropped = self._run({"s1": ""}, names=("s1",), rc=1)
        self.assertIsNone(out)
        self.assertEqual(dropped, [])

    def test_malformed_lines_skipped(self):
        out, dropped = self._run({
            "s1": "garbage line here\n100 1790993000 /p/A_LONG_v14_progress.json\n",
        }, names=("s1",))
        self.assertEqual(len(out["s1"]), 1)


if __name__ == "__main__":
    unittest.main()

"""Parity-repair prune logic (fakes; real engines run on fleet)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v15_parity_repair import family_of, rank_removals, repair


class TestFamilyOf(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(family_of("DAYTRADE_DC_TARGET_TF"), "DAYTRADE")
        self.assertEqual(family_of("HARDCODED_RALLY_REENTRY_ENABLED"), "HARDCODED_RALLY_REENTRY")
        self.assertEqual(family_of("REENTRY_MANDATORY"), "REENTRY")
        self.assertEqual(family_of("REENTRY2_DC_BREAK_ENABLED"), "REENTRY2")
        self.assertEqual(family_of("WT_DC_ENTRY_58"), "WT_DC")
        self.assertEqual(family_of("XYZ"), "MISC")


class TestRankRemovals(unittest.TestCase):
    def test_ranks_and_drops(self):
        ov = {"DAYTRADE_A": 1, "REENTRY_B": 2, "WT_DC_C": 3}

        def vec(sub):
            if "WT_DC_C" not in sub:
                return {"valid": False, "gain_pct": -1.0, "trades": 5}
            g = 10.0 - len(sub)
            return {"valid": True, "gain_pct": g, "trades": 50}

        r = rank_removals(ov, vec)
        self.assertEqual([f for f, _, _, _ in r], ["DAYTRADE", "REENTRY"])
        self.assertNotIn("WT_DC", [f for f, _, _, _ in r])

    def test_exception_skipped(self):
        def vec(sub):
            raise RuntimeError("boom")

        self.assertEqual(rank_removals({"A_B": 1}, vec), [])


class TestRepair(unittest.TestCase):
    def test_first_pass_wins(self):
        ov = {"DAYTRADE_A": 1, "REENTRY_B": 2}
        vec = lambda sub: {"valid": True, "gain_pct": 5.0, "trades": 40}
        calls = []

        def scalar(ss, sub):
            calls.append(set(sub))
            return (len(calls) == 2, "ok2" if len(calls) == 2 else "no1")

        sub, ev = repair("S_LONG", ov, vec, scalar, max_scalar_runs=5)
        self.assertEqual(sub, {"DAYTRADE_A": 1})
        self.assertEqual(len(calls), 2)
        self.assertTrue(ev[-1]["pass"])

    def test_exhaust_residual(self):
        ov = {"DAYTRADE_A": 1}
        vec = lambda sub: {"valid": True, "gain_pct": 5.0, "trades": 40}
        sub, ev = repair("S_LONG", ov, vec, lambda ss, s: (False, "still bad"), max_scalar_runs=5)
        self.assertIsNone(sub)
        self.assertEqual(ev[0]["keys"], 1)


if __name__ == "__main__":
    unittest.main()

"""NONE priority + pos_sym sampling (USER 2026-10-07): unevidenced yellow cells are
must-calculate priority inside computed rows; rows with pos_sym None sample 1/20."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import v15_pilot as PILOT
from tools.v15_cat_avg_matrix import cell_state


class CellStateTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(cell_state(None), "NONE")
        self.assertEqual(cell_state([0, 0, 0]), "NONE")
        self.assertEqual(cell_state([5, 2, 1]), "POS")
        self.assertEqual(cell_state([5, 0, 3]), "NEG0")
        self.assertEqual(cell_state([5, 0, 0]), "ALLZERO")
        self.assertEqual(cell_state([1, 0, 0]), "ALLZERO")


class PossymNoneTest(unittest.TestCase):
    def test_none_samples_1_in_20(self):
        go, bucket, p, u = PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", None, None)
        self.assertEqual(bucket, "pos=None")
        self.assertAlmostEqual(p, 1.0 / 20)
        self.assertIsInstance(go, bool)
        self.assertIsInstance(u, float)

    def test_none_deterministic(self):
        a = PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", None, None)
        b = PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", None, None)
        self.assertEqual(a, b)

    def test_none_rate(self):
        n = 4000
        hits = sum(PILOT._possym_decide(f"S{i}_LONG", "TAB", "SW=V", "run99", None, None)[0] for i in range(n))
        self.assertGreater(hits, 100)
        self.assertLess(hits, 300)

    def test_known_probs_unchanged(self):
        # USER 2026-10-10 refinement: pos>=1 always calculates; only pos=0/None sample.
        _, b0, p0, _ = PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", 0, 30)
        c1, b1, p1, _ = PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", 1, 30)
        c2, b2, p2, _ = PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", 2, 30)
        c3, b3, p3, _ = PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", 3, 30)
        self.assertEqual((b0, b1, b2, b3), ("pos=0", "pos>=1", "pos>=1", "pos>=1"))
        self.assertAlmostEqual(p0, 1.0 / 20)
        self.assertTrue(c1 and c2 and c3)
        self.assertIsNone(p1)
        self.assertIsNone(p2)
        self.assertIsNone(p3)

    def test_always_compute(self):
        self.assertTrue(PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", 4, 30)[0])
        self.assertTrue(PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", 9, 30)[0])
        self.assertTrue(PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", None, None, new=True)[0])
        self.assertTrue(PILOT._possym_decide("A_LONG", "TAB", "SW=V", "run99", 0, 2)[0])


class NonePriorityTest(unittest.TestCase):
    def test_force_decision(self):
        self.assertFalse(PILOT._is_none_priority({}, "TAB!SW=V@H=O"))
        self.assertFalse(PILOT._is_none_priority({"rows": {}, "cells": {}}, "TAB!SW=V@H=O"))
        self.assertFalse(PILOT._is_none_priority(None, "TAB!SW=V@H=O"))
        prio = {"rows": {"TAB!SW=V": [3, 1, 0]}, "cells": {"TAB!SW=V@H=O": [3, 1, 0]}}
        self.assertFalse(PILOT._is_none_priority(prio, "TAB!SW=V@H=O"))
        self.assertTrue(PILOT._is_none_priority(prio, "TAB!SW=V@H2=O2"))
        self.assertTrue(PILOT._is_none_priority(prio, "TAB2!SW2=V2@H=O"))

    def test_loader_fail_open(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("V15_PRIORITY_DIR")
            os.environ["V15_PRIORITY_DIR"] = td
            try:
                PILOT._NONE_PRIO_CACHE.clear()
                self.assertEqual(PILOT.none_priority_evidence("CRYPTO_SHORT"), {"rows": {}, "cells": {}})
            finally:
                PILOT._NONE_PRIO_CACHE.clear()
                if old is None:
                    os.environ.pop("V15_PRIORITY_DIR", None)
                else:
                    os.environ["V15_PRIORITY_DIR"] = old

    def test_loader_reads_real_file(self):
        PILOT._NONE_PRIO_CACHE.clear()
        try:
            prio = PILOT.none_priority_evidence("CRYPTO_SHORT")
            self.assertGreater(len(prio["rows"]), 1000)
            self.assertGreater(len(prio["cells"]), 10000)
            self.assertIn("ENTRY_REVERSAL_BOUNCE!FAST_RISER_FILTER_TF=4h", prio["rows"])
        finally:
            PILOT._NONE_PRIO_CACHE.clear()


if __name__ == "__main__":
    unittest.main()

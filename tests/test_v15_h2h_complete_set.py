"""A per_sym set may gain keys, never lose them.

2026-10-16 18:24Z: tools/v15_golive_headtohead applied `per_sym_history.overrides_json` AS the full set. That
column is a DIFF on many rows, so 24 live crypto sym_sides dropped from ~3572 keys to 41-856 (ETHFIUSDC_LONG 56,
TRBUSDT_LONG 41, ZECUSDC_LONG 76) and would have resolved ~3500 switches from cat_side/global defaults instead of
their proven values. They were restored from the pre-apply export. These tests pin the two guards added after.
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _mod():
    path = os.path.join(ROOT, "tools", "v15_golive_headtohead.py")
    spec = importlib.util.spec_from_file_location("v15_h2h_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestCandidateFull(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _mod()

    def test_diff_is_merged_over_the_incumbent(self):
        live = {f"K{i}": i for i in range(100)}
        cand = {"K0": 999, "NEW": 1}
        full = self.m._candidate_full(live, cand)
        self.assertEqual(len(full), 101)
        self.assertEqual(full["K0"], 999)
        self.assertEqual(full["NEW"], 1)
        self.assertEqual(full["K50"], 50)

    def test_no_live_key_is_ever_dropped(self):
        live = {f"K{i}": i for i in range(3572)}
        full = self.m._candidate_full(live, {"K7": "x"})
        self.assertEqual(set(live) - set(full), set())
        self.assertGreaterEqual(len(full), len(live))

    def test_the_regression_shape_cannot_shrink(self):
        """The exact shape that caused the incident: a 41-key publish row against a 3572-key live set."""
        live = {f"K{i}": i for i in range(3572)}
        publish_row = {f"K{i}": -i for i in range(41)}
        full = self.m._candidate_full(live, publish_row)
        self.assertEqual(len(full), 3572)
        self.assertEqual(full["K0"], 0)
        self.assertEqual(full["K40"], -40)
        self.assertEqual(full["K41"], 41)

    def test_empty_inputs_are_safe(self):
        self.assertEqual(self.m._candidate_full(None, None), {})
        self.assertEqual(self.m._candidate_full({}, {"A": 1}), {"A": 1})
        self.assertEqual(self.m._candidate_full({"A": 1}, {}), {"A": 1})

    def test_candidate_is_what_gets_measured(self):
        """The measured set and the written set must be the same object shape — the merge, not the raw diff."""
        src = open(os.path.join(ROOT, "tools", "v15_golive_headtohead.py")).read()
        self.assertIn("_gain_and_trades(ss, cand_full, a.window_days)", src)
        self.assertNotIn('d["overrides"] = cand\n', src)
        self.assertIn("refusing to shrink the live set", src)
        self.assertIn("refusing to shrink a live set", src)


if __name__ == "__main__":
    unittest.main()

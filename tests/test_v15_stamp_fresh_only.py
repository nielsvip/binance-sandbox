"""USER 2026-10-24: result_done_utc may only wear a new date when the launch
evaluated >=1 fresh cell. A resume/refusal that computed nothing keeps its
prior date (INTC_LONG wore 18:24 with 6-day-old cells fleet-wide)."""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import v15_pilot as P  # noqa: E402


class StampAllowedTest(unittest.TestCase):
    def test_fresh_evals_allow(self):
        self.assertTrue(P._stamp_allowed({"last_run_fresh_evals": 1}))
        self.assertTrue(P._stamp_allowed({"last_run_fresh_evals": 3718}))

    def test_zero_missing_or_junk_deny(self):
        self.assertFalse(P._stamp_allowed({"last_run_fresh_evals": 0}))
        self.assertFalse(P._stamp_allowed({}))
        self.assertFalse(P._stamp_allowed(None))
        self.assertFalse(P._stamp_allowed({"last_run_fresh_evals": "bogus"}))
        self.assertFalse(P._stamp_allowed({"last_run_fresh_evals": -3}))


if __name__ == "__main__":
    unittest.main()

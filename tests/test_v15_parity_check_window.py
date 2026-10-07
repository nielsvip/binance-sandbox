"""WINDOW span-stamp: detect scalar/vec window drift (42d-vs-30d class) from ledger ts."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v15_parity_check import _ledger_span


class TestLedgerSpan(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_ledger_span(None), (0, None))
        self.assertEqual(_ledger_span([]), (0, None))

    def test_single(self):
        self.assertEqual(_ledger_span([{"ts": 1700000000.0}]), (1, 0.0))

    def test_thirty_days(self):
        led = [{"ts": 1700000000.0}, {"ts": 1700000000.0 + 30 * 86400}]
        n, span = _ledger_span(led)
        self.assertEqual(n, 2)
        self.assertAlmostEqual(span, 30.0, places=6)

    def test_ms_normalized(self):
        led = [{"ts": 1700000000000.0}, {"ts": 1700000000000.0 + 42 * 86400000}]
        n, span = _ledger_span(led)
        self.assertEqual(n, 2)
        self.assertAlmostEqual(span, 42.0, places=6)

    def test_skips_junk(self):
        led = [None, {"noleg": 1}, {"ts": 0}, {"ts": 1700000000.0}, {"ts": 1700000000.0 + 86400}]
        n, span = _ledger_span(led)
        self.assertEqual(n, 2)
        self.assertAlmostEqual(span, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()

"""Venue-aware parity timeout: crypto scalar needs ~2400s, stocks ~1100s (2026-10-04)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v15_final_phase import parity_timeout_for


class TestParityTimeoutFor(unittest.TestCase):
    def test_crypto_gets_3600(self):
        for ss in ("ALGOUSDT_LONG", "BTCUSDC_SHORT", "ETHUSD1_LONG", "1000PEPEUSDC_SHORT"):
            self.assertEqual(parity_timeout_for(ss), 3600, ss)

    def test_stocks_get_1500(self):
        for ss in ("CRWD_LONG", "SMCI_LONG", "AAPL_SHORT", "GDX_LONG"):
            self.assertEqual(parity_timeout_for(ss), 1500, ss)

    def test_case_and_type_safe(self):
        self.assertEqual(parity_timeout_for("btcusdc_long"), 3600)
        self.assertEqual(parity_timeout_for("X"), 1500)


if __name__ == "__main__":
    unittest.main()

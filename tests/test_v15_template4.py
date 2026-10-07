"""4 independent templates (USER 2026-10-07): exact cat_side file, never generic, no fallback."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import v15_pilot as PILOT


class Template4Test(unittest.TestCase):
    def test_constants_point_at_final_norm(self):
        for p in (PILOT.TEMPLATE_CRYPTO_LONG, PILOT.TEMPLATE_CRYPTO_SHORT, PILOT.TEMPLATE_STOCKS_LONG, PILOT.TEMPLATE_STOCKS_SHORT):
            self.assertIn("TEMPLATE_FINAL_NORM", str(p))
            self.assertTrue(Path(p).exists(), f"missing fleet template {p}")

    def test_resolution_per_cat_side(self):
        self.assertEqual(PILOT.get_template_for_symside("BTCUSDC_LONG"), PILOT.TEMPLATE_CRYPTO_LONG)
        self.assertEqual(PILOT.get_template_for_symside("ETHUSDC_SHORT"), PILOT.TEMPLATE_CRYPTO_SHORT)
        self.assertEqual(PILOT.get_template_for_symside("AAPL_LONG"), PILOT.TEMPLATE_STOCKS_LONG)
        self.assertEqual(PILOT.get_template_for_symside("MSFT_SHORT"), PILOT.TEMPLATE_STOCKS_SHORT)
        four = {PILOT.get_template_for_symside(s) for s in ("BTCUSDC_LONG", "ETHUSDC_SHORT", "AAPL_LONG", "MSFT_SHORT")}
        self.assertEqual(len(four), 4)

    def test_missing_file_fails_closed_no_fallback(self):
        old = PILOT.TEMPLATE_CRYPTO_LONG
        try:
            PILOT.TEMPLATE_CRYPTO_LONG = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "NOPE.xlsx"
            with self.assertRaises(SystemExit):
                PILOT.get_template_for_symside("BTCUSDC_LONG")
        finally:
            PILOT.TEMPLATE_CRYPTO_LONG = old


if __name__ == "__main__":
    unittest.main()

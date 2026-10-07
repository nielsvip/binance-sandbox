"""Final matrix filename contract: int-percent gain + trade count (USER 2026-10-03)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v15_final_naming import chart_name_for, final_matrix_name, fmt_gain_int, parse_final_matrix_name


class TestBuild(unittest.TestCase):
    def test_user_example(self):
        self.assertEqual(final_matrix_name("FANG_LONG", 1.58, 6.67, 66), "FANG_LONG_bh1p58_gain6p_t66_30d_matrix.xlsx")

    def test_truncation_edges(self):
        self.assertEqual(fmt_gain_int(6.67), "6p")
        self.assertEqual(fmt_gain_int(0.0), "0p")
        self.assertEqual(fmt_gain_int(0.99), "0p")
        self.assertEqual(fmt_gain_int(-11.29), "m11p")
        self.assertEqual(fmt_gain_int(-0.36), "m0p")

    def test_refuses_garbage(self):
        with self.assertRaises(ValueError):
            final_matrix_name("X_LONG", 1.0, float("nan"), 5)
        with self.assertRaises(ValueError):
            final_matrix_name("X_LONG", 1.0, 2.0, -1)

    def test_chart_name(self):
        self.assertEqual(chart_name_for("FANG_LONG_bh1p58_gain6p_t66_30d_matrix.xlsx"), "FANG_LONG_bh1p58_gain6p_t66_30d_matrix.html")


class TestParse(unittest.TestCase):
    def test_round_trip_new(self):
        n = final_matrix_name("ENSUSDT_SHORT", -23.74, 12.10, 66)
        p = parse_final_matrix_name(n)
        self.assertEqual(p["symside"], "ENSUSDT_SHORT")
        self.assertAlmostEqual(p["bh"], -23.74)
        self.assertAlmostEqual(p["gain"], 12.0)
        self.assertEqual(p["trades"], 66)
        self.assertEqual(p["fmt"], "new")

    def test_old_format_back_compat(self):
        p = parse_final_matrix_name("ENSUSDT_SHORT_bhm23p74_gain12p10_30d_matrix.xlsx")
        self.assertEqual(p["symside"], "ENSUSDT_SHORT")
        self.assertAlmostEqual(p["gain"], 12.10)
        self.assertIsNone(p["trades"])
        self.assertEqual(p["fmt"], "old")

    def test_malformed_none(self):
        for bad in ("ENSUSDT_SHORT_30d_matrix.xlsx", "X_bh1p2_gain3p_t4_30d_matrix.xlsx.superseded", "random.xlsx", ""):
            self.assertIsNone(parse_final_matrix_name(bad), bad)

    def test_herd_gate_values(self):
        p = parse_final_matrix_name("FANG_LONG_bh1p58_gain6p_t66_30d_matrix.xlsx")
        self.assertTrue(p["gain"] >= 0 and p["gain"] >= 1.0)


if __name__ == "__main__":
    unittest.main()

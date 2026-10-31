import unittest

from tools import fixed_stop_variation_lab as lab


class FixedStopVariationLabTests(unittest.TestCase):
    def test_selects_all_sides_at_losing_stop_fill_floor(self):
        inventory = {"per_symbol": {"AAA": {"position_sides": {
            "LONG": {"fixed_stop_exit_fill_loss_count": 3},
            "SHORT": {"fixed_stop_exit_fill_loss_count": 2},
        }}, "BBB": {"position_sides": {"SHORT": {"fixed_stop_exit_fill_loss_count": 7}}}}}
        self.assertEqual(lab.fixed_stop_loser_counts(inventory, 3), {"AAA_LONG": 3, "BBB_SHORT": 7})

    def test_variants_are_template_values_and_keep_stop_enabled(self):
        base = {"FIXED_STOP_REF_FIELD": "dc_low", "FIXED_STOP_REF_TF": "15m", "FIXED_STOP_BUFFER_PCT": 0.25,
                "FIXED_STOP_TIGHTEN_MODE": "off", "FIXED_STOP_TIGHTEN_TRIGGER_PCT": 1.0}
        choices = {
            "FIXED_STOP_REF_FIELD": ["dc_low", "dc_basis"], "FIXED_STOP_REF_TF": ["15m", "1h"],
            "FIXED_STOP_BUFFER_PCT": [0.25, 0.35], "FIXED_STOP_TIGHTEN_MODE": ["off", "trail_ref"],
            "FIXED_STOP_TIGHTEN_TRIGGER_PCT": [1.0, 0.5],
        }
        variants = lab.make_variants(base, choices)
        self.assertTrue(variants)
        self.assertTrue(any(v["overrides"].get("FIXED_STOP_REF_TF") == "1h" for v in variants))
        self.assertTrue(any(v["overrides"].get("FIXED_STOP_TIGHTEN_MODE") == "trail_ref" for v in variants))
        self.assertTrue(any(v["overrides"].get("FIXED_STOP_TIGHTEN_TRIGGER_PCT") == 0.5 for v in variants))
        for v in variants:
            self.assertNotIn("FIXED_STOP_ENABLED", v["overrides"])

    def test_summary_reports_fees_turnover_entries_and_trade_net(self):
        result = {"valid": True, "data_days": 365.0, "max_dd_pct": 12.0, "execution_ledger": [
            {"type": "OPEN", "bar": 1, "qty": 10, "price": 100, "reason": "ENTRY"},
            {"type": "CLOSE", "bar": 3, "qty": 10, "price": 99, "reason": "FIXED_STOP"},
            {"type": "CLOSE", "bar_entry": 1, "bar_exit": 3, "entry_price": 100, "exit_price": 99,
             "qty": 10, "pnl_pct": -1.08, "pnl_dollars": -1.08, "entry_reason": "ENTRY",
             "exit_reason": "FIXED_STOP", "bars_held": 2},
        ]}
        out = lab.summarize_run(result, 0.04, 365)
        self.assertEqual(out["entry_count"], 1)
        self.assertEqual(out["reentry_count"], 0)
        self.assertEqual(out["closed_fixed_stop_count"], 1)
        self.assertAlmostEqual(out["turnover_estimate"], 1990)
        self.assertAlmostEqual(out["simulated_fee_estimate"], 0.796)
        self.assertEqual(out["net_pnl_engine"], -1.08)
        self.assertAlmostEqual(out["trade_rows"][0]["gross_pnl_estimate"], -0.284)
        self.assertEqual(out["status"], "DIAGNOSTIC_ONLY")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import persym_baseline_campaign as psc


class RepairedMatrixContractTests(unittest.TestCase):
    def test_capital_accounting_uses_dollars_and_two_thousand_benchmark(self):
        trades = [
            {
                "pnl_pct": 10.0,
                "pnl_usd": 100.0,
                "round_trip_cost_pct": 0.06,
            },
            {
                # Same percentage, four times the position: percentage summation would lie.
                "pnl_pct": 10.0,
                "pnl_usd": 400.0,
                "round_trip_cost_pct": 0.06,
            },
        ]
        metrics = psc.capital_key_metrics(
            trades,
            years=1.0,
            bh_long_price_pct=100.0,
            side="LONG",
            result={"time_in_mkt_long_pct": 75.0},
        )
        self.assertEqual(metrics["acc_gain_pct"], 5.0)
        self.assertEqual(metrics["bh_pct"], 19.988)
        self.assertEqual(metrics["time_in_mkt_pct"], 75.0)

    def test_short_benchmark_is_side_aware(self):
        metrics = psc.capital_key_metrics(
            [{"pnl_pct": 5, "pnl_usd": 50, "round_trip_cost_pct": 0.06}],
            years=1.0,
            bh_long_price_pct=25.0,
            side="SHORT",
            result={"time_in_mkt_short_pct": 40.0},
        )
        self.assertEqual(metrics["bh_pct"], -5.012)

    def test_missing_real_close_is_diagnostic_not_a_promotable_pass(self):
        fake_data = type(
            "Contract",
            (),
            {
                "valid": True,
                "errors": (),
                "warnings": (),
                "stats": {},
            },
        )()
        sizing = {"valid": True}
        result = {
            "real_closes": 0,
            "reentry_pending": 0,
            "reentry_violations": 0,
        }
        trades = [{"entry_reason": "V8_LADDER_INITIAL_BH_SEED"}]
        with patch("backtest_data_contract.audit_npz", return_value=fake_data), patch(
            "backtest_data_contract.audit_ladder_result", return_value=sizing
        ), patch.object(psc, "matrix_contract_fingerprint", return_value="fp"):
            audit = psc.matrix_run_audit("MU", "LONG", trades, result)
        self.assertEqual(audit["status"], "INCOMPLETE_NO_REAL_CLOSE")


if __name__ == "__main__":
    unittest.main()

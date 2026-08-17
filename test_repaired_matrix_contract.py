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
                "round_trip_cost_pct": 0.05,
                "entry_price": 100.0,
                "executed_open_qty": 10.0,
            },
            {
                # Same percentage, four times the position: percentage summation would lie.
                "pnl_pct": 10.0,
                "pnl_usd": 400.0,
                "round_trip_cost_pct": 0.05,
                "entry_price": 100.0,
                "executed_open_qty": 40.0,
            },
        ]
        metrics = psc.capital_key_metrics(
            trades,
            years=1.0,
            bh_long_price_pct=100.0,
            side="LONG",
            result={"time_in_mkt_long_pct": 75.0},
        )
        self.assertEqual(metrics["acc_gain_pct"], 20.0)
        self.assertEqual(metrics["bh_pct"], 99.95)
        self.assertEqual(metrics["time_in_mkt_pct"], 75.0)
        self.assertEqual(metrics["average_deployed_usd"], 2500.0)
        self.assertEqual(metrics["benchmark_deployed_usd"], 2000.0)
        self.assertEqual(metrics["capital_normalization_factor"], 0.8)
        self.assertEqual(metrics["normalized_pnl_usd"], 400.0)

    def test_short_benchmark_is_side_aware(self):
        metrics = psc.capital_key_metrics(
            [
                {
                    "pnl_pct": 5,
                    "pnl_usd": 50,
                    "round_trip_cost_pct": 0.05,
                    "entry_price": 100,
                    "executed_open_qty": 10,
                }
            ],
            years=1.0,
            bh_long_price_pct=25.0,
            side="SHORT",
            result={"time_in_mkt_short_pct": 40.0},
        )
        self.assertEqual(metrics["bh_pct"], -25.05)

    def test_action_event_cash_flow_is_authoritative_deployed_notional(self):
        metrics = psc.capital_key_metrics(
            [
                {
                    "pnl_pct": 10,
                    "pnl_usd": 200,
                    "entry_price": 10,
                    "executed_open_qty": 999,
                    "round_trip_cost_pct": 0.05,
                    "action_events": [
                        {
                            "action": "OPEN",
                            "executed_qty": 10,
                            "price": 100,
                            "cash_flow": -999999,
                        },
                        {"action": "AUGMENT", "executed_qty": 5, "price": 100},
                        {"action": "REENTER", "executed_qty": 5, "price": 100},
                        {"action": "CLOSE", "cash_flow": 2200},
                    ],
                }
            ],
            years=1.0,
            bh_long_price_pct=10.0,
            side="LONG",
        )
        self.assertEqual(metrics["average_deployed_usd"], 2000.0)
        self.assertEqual(metrics["acc_gain_pct"], 10.0)

    def test_missing_executed_entry_notional_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "positive executed entry notional"):
            psc.capital_key_metrics(
                [{"pnl_pct": 0, "pnl_usd": 20}],
                years=1.0,
                bh_long_price_pct=10.0,
                side="LONG",
            )

    def test_legacy_exact_dollar_percent_pair_reconstructs_deployed_notional(self):
        metrics = psc.capital_key_metrics(
            [{"pnl_pct": 5.0, "pnl_usd": 100.0}],
            years=1.0,
            bh_long_price_pct=10.0,
            side="LONG",
        )
        self.assertEqual(metrics["average_deployed_usd"], 2000.0)

    def test_risk_metrics_use_ordered_capital_normalized_dollar_returns(self):
        trades = [
            {
                "pnl_pct": 10,
                "pnl_usd": 100,
                "entry_price": 100,
                "executed_open_qty": 10,
            },
            {
                "pnl_pct": -10,
                "pnl_usd": -400,
                "entry_price": 100,
                "executed_open_qty": 40,
            },
        ]
        metrics = psc.capital_key_metrics(
            trades,
            years=1.0,
            bh_long_price_pct=10.0,
            side="LONG",
        )
        normalized_returns = [4.0, -16.0]
        self.assertEqual(metrics["acc_gain_pct"], -12.0)
        self.assertEqual(metrics["max_dd_pct"], 16.0)
        self.assertEqual(
            metrics["pool_sharpe"],
            psc.mg.pool_sharpe(normalized_returns),
        )

    def test_c5_metrics_preserve_sub_four_decimal_result_differences(self):
        common = {
            "pnl_pct": 1.0,
            "entry_price": 100.0,
            "executed_open_qty": 20.0,
            "round_trip_cost_pct": 0.05,
        }
        first = psc.capital_key_metrics(
            [{**common, "pnl_usd": 20.00001}],
            years=1.0,
            bh_long_price_pct=10.0,
            side="LONG",
        )
        second = psc.capital_key_metrics(
            [{**common, "pnl_usd": 20.00002}],
            years=1.0,
            bh_long_price_pct=10.0,
            side="LONG",
        )

        # Both values round to the same four-decimal gain/month.  The evidence
        # store must retain the real computed difference so the two genuine
        # schedules are not manufactured into a collision.
        self.assertEqual(
            round(first["gain_per_mo"], 4),
            round(second["gain_per_mo"], 4),
        )
        self.assertNotEqual(first["gain_per_mo"], second["gain_per_mo"])
        self.assertNotEqual(
            first["delta_gain_mo_vs_bh"],
            second["delta_gain_mo_vs_bh"],
        )

    def test_c5_missing_real_close_fails_closed(self):
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
            "sized_open_events": 1,
            "max_requested_mult": 1,
            "requested_fill_ratio": 1,
            "size_clamp_count": 0,
            "strategy_capacity_usd": 16000,
            "max_open_notional": 2000,
        }
        trades = [{"entry_reason": "V8_LADDER_INITIAL_BH_SEED"}]
        with patch("backtest_data_contract.audit_npz", return_value=fake_data), patch(
            "backtest_data_contract.audit_ladder_result", return_value=sizing
        ), patch.object(psc, "matrix_contract_fingerprint", return_value="fp"):
            audit = psc.matrix_run_audit("MU", "LONG", trades, result)
        self.assertEqual(audit["status"], "FAIL")
        self.assertIn(
            "no real close: exit/re-entry lifecycle was not exercised",
            audit["reasons"],
        )

    def test_c5_ladder_control_may_have_no_close_but_is_not_candidate_pass(self):
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
            "reclaim_pending": 0,
            "reentry_violations": 0,
            "sized_open_events": 1,
            "max_requested_mult": 1,
            "requested_fill_ratio": 1,
            "size_clamp_count": 0,
            "strategy_capacity_usd": 16000,
            "max_open_notional": 2000,
        }
        trades = [{"entry_reason": "V8_LADDER_INITIAL_BH_SEED"}]
        with patch("backtest_data_contract.audit_npz", return_value=fake_data), patch(
            "backtest_data_contract.audit_ladder_result", return_value=sizing
        ), patch.object(psc, "matrix_contract_fingerprint", return_value="fp"):
            audit = psc.matrix_run_audit(
                "MU",
                "LONG",
                trades,
                result,
                allow_no_real_close_control=True,
            )
        self.assertEqual(audit["status"], "PASS_CONTROL_NO_CLOSE")
        self.assertTrue(audit["allow_no_real_close_control"])

    def test_c5_capacity_clamps_fail_closed(self):
        fake_data = type(
            "Contract",
            (),
            {"valid": True, "errors": (), "warnings": (), "stats": {}},
        )()
        structural = {"valid": True}
        full_sizing = {"valid": False}
        result = {
            "real_closes": 10,
            "reentry_pending": 0,
            "reentry_violations": 0,
            "sized_open_events": 10,
            "max_requested_mult": 8,
            "requested_fill_ratio": 0.5,
            "size_clamp_count": 5,
            "strategy_capacity_usd": 16000,
            "max_open_notional": 15000,
        }
        trades = [{"entry_reason": "V8_LADDER_INITIAL_BH_SEED"}]
        with patch("backtest_data_contract.audit_npz", return_value=fake_data), patch(
            "backtest_data_contract.audit_ladder_result",
            side_effect=[full_sizing, structural],
        ), patch.object(psc, "matrix_contract_fingerprint", return_value="fp"):
            audit = psc.matrix_run_audit("MU", "LONG", trades, result)
        self.assertEqual(audit["status"], "FAIL")
        self.assertIn(
            "capacity clamps or sub-90% requested/fill ratio observed",
            audit["reasons"],
        )
        self.assertTrue(audit["capacity_respected"])

    def test_c5_terminal_pending_is_right_censored_when_no_overshoot(self):
        fake_data = type(
            "Contract",
            (),
            {"valid": True, "errors": (), "warnings": (), "stats": {}},
        )()
        sizing = {"valid": True}
        result = {
            "real_closes": 3,
            "reentry_pending": 1,
            "reclaim_pending": 1,
            "reentry_violations": 0,
            "sized_open_events": 4,
            "max_requested_mult": 2,
            "requested_fill_ratio": 1,
            "size_clamp_count": 0,
            "strategy_capacity_usd": 16000,
            "max_open_notional": 4000,
        }
        trades = [{"entry_reason": "V8_LADDER_INITIAL_BH_SEED"}]
        with patch("backtest_data_contract.audit_npz", return_value=fake_data), patch(
            "backtest_data_contract.audit_ladder_result", return_value=sizing
        ), patch.object(psc, "matrix_contract_fingerprint", return_value="fp"):
            audit = psc.matrix_run_audit("VT", "LONG", trades, result)
        self.assertEqual(audit["status"], "PASS")
        self.assertTrue(audit["terminal_right_censored"])
        self.assertTrue(audit["c5_safety"]["terminal_right_censored"])

    def test_cached_exact_result_requires_identical_effective_override(self):
        with tempfile.TemporaryDirectory() as directory:
            cell = Path(directory)
            (cell / "override__MU.json").write_text(
                '{"MTF_DC_REJECT_EXIT_ENABLED": true, "HOLD": 240}'
            )
            self.assertTrue(
                psc.cached_override_matches(
                    cell,
                    "MU",
                    {"HOLD": 240, "MTF_DC_REJECT_EXIT_ENABLED": True},
                )
            )
            self.assertFalse(
                psc.cached_override_matches(
                    cell,
                    "MU",
                    {"HOLD": 4320, "MTF_DC_REJECT_EXIT_ENABLED": True},
                )
            )

    def test_missing_or_malformed_cached_override_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            cell = Path(directory)
            self.assertFalse(psc.cached_override_matches(cell, "MU", {}))
            (cell / "override__MU.json").write_text("{not-json")
            self.assertFalse(psc.cached_override_matches(cell, "MU", {}))


if __name__ == "__main__":
    unittest.main()

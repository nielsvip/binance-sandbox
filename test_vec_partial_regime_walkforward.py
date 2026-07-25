import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import vec_partial_regime_walkforward as campaign
from backtest_v8_harness import accumulate_partial_close


class PartialRegimeWalkForwardTests(unittest.TestCase):
    def test_cost_math_matches_faithful_partial_accumulator_long(self):
        gross, net = campaign._engine_cost_reference(500.0, 100.0, 110.0, 1, 0.001)
        state = {"qty": 5.0, "entry_price": 100.0}
        summary = accumulate_partial_close(state, 5.0, 110.0, 0.10, True)
        self.assertAlmostEqual(gross, summary["pnl_dollars_gross"])
        self.assertAlmostEqual(net, summary["pnl_dollars"])

    def test_cost_math_matches_faithful_partial_accumulator_short(self):
        gross, net = campaign._engine_cost_reference(500.0, 100.0, 90.0, -1, 0.001)
        state = {"qty": 5.0, "entry_price": 100.0}
        summary = accumulate_partial_close(state, 5.0, 90.0, 0.10, False)
        self.assertAlmostEqual(gross, summary["pnl_dollars_gross"])
        self.assertAlmostEqual(net, summary["pnl_dollars"])

    def test_regime_uses_only_already_completed_daily_values(self):
        h = type("HTF", (), {})()
        h.close = np.linspace(100.0, 200.0, 300)
        h.event_index = np.arange(10, 310)
        h.source_ts = np.arange(300)
        regime = campaign._regime_array(320, h, 1, 20, 20, 50)
        # Before the first completed daily event no regime can be visible.
        self.assertTrue(np.all(regime[:10] == 0))
        # A long monotonic series eventually becomes an aligned trend.
        self.assertTrue(np.any(regime[100:] == 1))

    def test_scanner_compiles(self):
        lib = campaign._compile_scanner()
        self.assertTrue(hasattr(lib, "vec_partial_regime_scan"))

    def test_compiled_scanner_exact_latency_and_partial_fixture(self):
        audit = campaign._synthetic_scanner_check()
        self.assertEqual(audit["status"], "PASS", audit)


if __name__ == "__main__":
    unittest.main()

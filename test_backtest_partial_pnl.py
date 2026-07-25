import unittest

from backtest_v8_harness import accumulate_partial_close


class PartialPnlTests(unittest.TestCase):
    def test_two_partial_exits_both_contribute_to_round_pnl(self):
        state = {"qty": 10.0, "entry_price": 100.0}
        self.assertIsNone(accumulate_partial_close(state, 4, 110, 0.0, True))
        summary = accumulate_partial_close(state, 6, 90, 0.0, True)
        # +$40 on first partial, -$60 on second = -$20, not just final -10%.
        self.assertAlmostEqual(summary["pnl_dollars"], -20.0)
        self.assertAlmostEqual(summary["pnl_pct"], -2.0)
        self.assertAlmostEqual(summary["exit_price"], 98.0)

    def test_cost_is_allocated_across_every_partial(self):
        state = {"qty": 10.0, "entry_price": 100.0}
        accumulate_partial_close(state, 5, 101, 0.10, True)
        summary = accumulate_partial_close(state, 5, 101, 0.10, True)
        self.assertAlmostEqual(summary["pnl_dollars_gross"], 10.0)
        self.assertAlmostEqual(summary["pnl_dollars"], 9.0)


if __name__ == "__main__":
    unittest.main()

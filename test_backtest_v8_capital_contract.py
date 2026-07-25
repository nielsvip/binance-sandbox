import unittest
from types import SimpleNamespace

from backtest_v8_harness import apply_tradier_backtest_capital_contract


class CapitalContractTests(unittest.TestCase):
    def test_ten_thousand_capital_means_two_thousand_bh_and_sixteen_thousand_capacity(self):
        cfg = SimpleNamespace(
            START_POSITION_SIZE=1,
            MAX_ORDER_VALUE=2,
            MAX_POSITION_SIZE=3,
            SWING_LONG_BUDGET=4,
            SWING_SHORT_BUDGET=5,
        )
        result = apply_tradier_backtest_capital_contract(cfg, 10_000)
        self.assertEqual(cfg.START_POSITION_SIZE, 2_000)
        self.assertEqual(cfg.MAX_ORDER_VALUE, 16_000)
        self.assertEqual(cfg.MAX_POSITION_SIZE, 16_000)
        self.assertEqual(cfg.SWING_LONG_BUDGET, 16_000)
        self.assertEqual(cfg.SWING_SHORT_BUDGET, 16_000)
        self.assertEqual(result["max_ladder_mult"], 8)


if __name__ == "__main__":
    unittest.main()

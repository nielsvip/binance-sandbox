import unittest

from reentry_contract import (
    get_exit_value,
    reentry_opposition,
    set_exit_state,
    update_reentry_trace,
)


class Manager:
    def __init__(self):
        self.last_exit_times = {}
        self.last_exit_prices = {}


class ReentryContractTests(unittest.TestCase):
    def test_long_and_short_exit_state_are_isolated(self):
        manager = Manager()
        set_exit_state(manager, "trb:MU_LONG", 10, 100.0)
        set_exit_state(manager, "trb:MU_SHORT", 20, 90.0)
        self.assertEqual(get_exit_value(manager.last_exit_prices, "trb:MU_LONG", "MU"), 100.0)
        self.assertEqual(get_exit_value(manager.last_exit_prices, "trb:MU_SHORT", "MU"), 90.0)
        self.assertNotIn("MU", manager.last_exit_prices)

    def test_legacy_symbol_state_is_read_but_not_required(self):
        self.assertEqual(get_exit_value({"MU": 88.0}, "trb:MU_LONG", "MU"), 88.0)

    def test_reentry_waits_only_when_more_than_one_tf_opposes(self):
        indicators = {}
        for tf in ("15m", "1h", "4h", "D"):
            indicators.update({
                f"wt1_{tf}": 2, f"wt2_{tf}": 1,
                f"stoch_k_{tf}": 60, f"stoch_d_{tf}": 40,
            })
        indicators["wt1_1h"] = 0
        indicators["wt1_4h"] = 0
        opposed, _ = reentry_opposition(indicators, True)
        self.assertEqual(opposed, ["1h", "4h"])
        self.assertGreater(len(opposed), 1)
        indicators["wt1_4h"] = 2
        opposed, _ = reentry_opposition(indicators, True)
        self.assertEqual(opposed, ["1h"])

    def test_pending_trace_never_forgets_and_records_overshoot(self):
        trace = {}
        first = update_reentry_trace(trace, "trb:MU_LONG", True, 100, 101, 1, ["1h", "4h"])
        second = update_reentry_trace(trace, "trb:MU_LONG", True, 100, 105, 2, ["1h"])
        self.assertIs(first, second)
        self.assertEqual(second["flat_bars"], 2)
        self.assertTrue(second["pending"])
        self.assertAlmostEqual(second["max_overshoot_pct"], 5.0)


if __name__ == "__main__":
    unittest.main()

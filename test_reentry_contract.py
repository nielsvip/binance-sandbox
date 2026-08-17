import unittest

from reentry_contract import (
    confirm_reentry_fill,
    force_reentry_after_temporary_opposition,
    active_reentry_violation,
    get_exit_value,
    reentry_opposition,
    resting_reclaim_fill,
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

    def test_resolved_trace_starts_a_clean_next_cycle(self):
        trace = {}
        old = update_reentry_trace(
            trace, "trb:MU_LONG", True, 100, 105, 1, []
        )
        old["pending"] = False
        new = update_reentry_trace(
            trace, "trb:MU_LONG", True, 110, 110.1, 2, ["1h", "4h"]
        )
        self.assertEqual(new["flat_bars"], 1)
        self.assertAlmostEqual(
            new["max_overshoot_pct"], 0.1 / 110 * 100
        )

    def test_any_real_flat_to_open_fill_resolves_the_obligation(self):
        trace = {
            "trb:MU_LONG": {
                "pending": True,
                "flat_bars": 7,
                "max_overshoot_pct": 0.2,
            }
        }
        self.assertTrue(
            confirm_reentry_fill(
                trace, "trb:MU_LONG", timestamp=123, price=101.25
            )
        )
        self.assertFalse(trace["trb:MU_LONG"]["pending"])
        self.assertEqual(trace["trb:MU_LONG"]["filled_ts"], 123.0)
        self.assertEqual(trace["trb:MU_LONG"]["filled_price"], 101.25)

    def test_violation_requires_an_active_unopposed_overshoot(self):
        row = {
            "pending": True,
            "max_overshoot_pct": 1.0,
            "last_opposed_tfs": ["1h", "4h"],
        }
        self.assertFalse(active_reentry_violation(row, 0.3))
        row["last_opposed_tfs"] = ["1h"]
        self.assertTrue(active_reentry_violation(row, 0.3))
        row["pending"] = False
        self.assertFalse(active_reentry_violation(row, 0.3))

    def test_multi_tf_opposition_is_bounded_not_permanent(self):
        row = {
            "pending": True,
            "flat_bars": 11,
            "max_overshoot_pct": 12.25,
            "last_opposed_tfs": ["1h", "4h"],
        }
        self.assertFalse(
            force_reentry_after_temporary_opposition(
                row,
                favorable=True,
                material_pct=0.5,
                max_opposed_bars=12,
            )
        )
        row["flat_bars"] = 12
        self.assertTrue(
            force_reentry_after_temporary_opposition(
                row,
                favorable=True,
                material_pct=0.5,
                max_opposed_bars=12,
            )
        )

    def test_unopposed_favorable_reentry_is_immediate(self):
        row = {
            "pending": True,
            "flat_bars": 1,
            "max_overshoot_pct": 0.01,
            "last_opposed_tfs": ["1h"],
        }
        self.assertTrue(
            force_reentry_after_temporary_opposition(
                row,
                favorable=True,
                material_pct=0.5,
                max_opposed_bars=12,
            )
        )

    def test_resting_reclaim_touch_and_adverse_gap_fill_are_side_mirrors(self):
        # Touch fills at the stored level, with one-way adverse slippage.
        self.assertAlmostEqual(
            resting_reclaim_fill(
                is_long=True,
                reclaim_level=100,
                bar_open=99,
                bar_high=101,
                bar_low=98,
                slippage_bps=2,
            ),
            100.02,
        )
        self.assertAlmostEqual(
            resting_reclaim_fill(
                is_long=False,
                reclaim_level=100,
                bar_open=101,
                bar_high=102,
                bar_low=99,
                slippage_bps=2,
            ),
            99.98,
        )
        # A gap beyond a stop cannot receive the stale stop price.
        self.assertAlmostEqual(
            resting_reclaim_fill(
                is_long=True,
                reclaim_level=100,
                bar_open=110,
                bar_high=112,
                bar_low=109,
                slippage_bps=2,
            ),
            110.022,
        )
        self.assertAlmostEqual(
            resting_reclaim_fill(
                is_long=False,
                reclaim_level=100,
                bar_open=90,
                bar_high=91,
                bar_low=88,
                slippage_bps=2,
            ),
            89.982,
        )

    def test_resting_reclaim_does_not_fill_without_touch(self):
        self.assertIsNone(
            resting_reclaim_fill(
                is_long=True,
                reclaim_level=100,
                bar_open=98,
                bar_high=99.9,
                bar_low=97,
            )
        )
        self.assertIsNone(
            resting_reclaim_fill(
                is_long=False,
                reclaim_level=100,
                bar_open=102,
                bar_high=103,
                bar_low=100.1,
            )
        )


if __name__ == "__main__":
    unittest.main()

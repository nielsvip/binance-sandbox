import unittest

from backtest_v8_harness import open_sizing_telemetry


class SizingTelemetryTests(unittest.TestCase):
    def test_requested_ladder_size_and_clamped_fill_are_visible(self):
        events = [{
            "action": "OPEN",
            "position_side": "LONG",
            "price": 100.0,
            "quantity": 25.0,
            "reason": "LR_BAND_LADDER_L_pb=0.1_x10.00_D",
        }]
        result = open_sizing_telemetry(events, 2000.0)
        self.assertEqual(result["max_requested_mult"], "10.0000")
        self.assertEqual(result["max_filled_start_mult"], "1.2500")
        self.assertEqual(result["requested_fill_ratio"], "0.1250")
        self.assertEqual(result["size_clamp_count"], 1)

    def test_opposite_side_close_is_not_counted_as_open(self):
        events = [{
            "action": "SELL",
            "position_side": "LONG",
            "price": 100.0,
            "quantity": 25.0,
            "reason": "exit_x10",
        }]
        result = open_sizing_telemetry(events, 2000.0)
        self.assertEqual(result["max_open_notional"], "0.0000")

    def test_ladder_one_through_eight_produces_distinct_filled_notional(self):
        fills = []
        for mult in range(1, 9):
            result = open_sizing_telemetry([{
                "action": "OPEN",
                "position_side": "LONG",
                "price": 100.0,
                "quantity": 20.0 * mult,
                "reason": f"LR_BAND_LADDER_L_x{mult:.2f}_D",
            }], 2000.0)
            self.assertEqual(result["requested_fill_ratio"], "1.0000")
            fills.append(result["max_open_notional"])
        self.assertEqual(len(set(fills)), 8)

    def test_stage0_bh_seed_reports_one_requested_and_filled_unit(self):
        result = open_sizing_telemetry([{
            "action": "OPEN",
            "position_side": "LONG",
            "price": 125.0,
            "quantity": 16.0,
            "reason": "V8_LADDER_INITIAL_BH_SEED",
        }], 2000.0)
        self.assertEqual(result["max_requested_mult"], "1.0000")
        self.assertEqual(result["max_filled_start_mult"], "1.0000")
        self.assertEqual(result["requested_fill_ratio"], "1.0000")
        self.assertEqual(result["sized_open_events"], 1)
        self.assertEqual(result["size_clamp_count"], 0)

    def test_live_base_and_existing_position_define_incremental_request(self):
        result = open_sizing_telemetry([{
            "action": "AUGMENT",
            "position_side": "LONG",
            "price": 100.0,
            "quantity": 88.5,
            "reason": "WT_FORCE_x4_posval2950of2950_px100_qty88.5",
        }], 2000.0)
        # Target=4*$2950; held=$2950; incremental request/fill=$8850.
        self.assertEqual(result["requested_fill_ratio"], "1.0000")
        self.assertEqual(result["size_clamp_count"], 0)


if __name__ == "__main__":
    unittest.main()

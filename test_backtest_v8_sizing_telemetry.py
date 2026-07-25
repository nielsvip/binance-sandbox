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


if __name__ == "__main__":
    unittest.main()

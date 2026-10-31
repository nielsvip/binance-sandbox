import unittest

from tools.fixed_stop_path_audit import audit_inventory, audit_lifecycle


def event(ts, kind, qty, avg, price, stop=False, realized=0.0, trade_id=None):
    return {"timestamp_ms": ts, "timestamp": None, "event_type": kind, "qty": qty, "price": price,
            "current_qty_after": qty, "weighted_average_entry_price_after": avg,
            "realized_pnl_raw": realized, "realized_pnl_asset": "USDT", "fixed_stop_order_detected": stop,
            "trade_id": trade_id}


def lifecycle(events, side="LONG", carried=False):
    return {"account_id": "acct", "symbol": "ABCUSDT", "position_side": side,
            "lifecycle_index": 1, "status": "completed", "carried_in": carried, "events": events}


class FixedStopPathAuditTests(unittest.TestCase):
    def test_closed_bar_profit_is_separate_from_stop_bar_and_commission(self):
        life = lifecycle([event(0, "open", 2, 10, 10), event(3600000, "close", 0, 10, 8, True, -4, 2)])
        bars = {"bars": [(0, 11, 12, 9), (900000, 10.5, 11, 10), (1800000, 10, 12, 9),
                          (2700000, 8, 12, 7)], "timeframe": "15m", "source_kind": "test", "source_path": "fixture"}
        result = audit_lifecycle(life, bars, 4000000)
        self.assertEqual(result["classification"], "CLOSED_BAR_GROSS_PROFIT_OPPORTUNITY")
        self.assertEqual(result["eligible_bar_count"], 3)
        self.assertEqual(result["first_confirmed_closed_bar_opportunity"]["bar_start_ms"], 0)
        self.assertEqual(result["first_confirmed_closed_bar_opportunity"]["gross_pnl_at_close_before_commission"], 2)
        self.assertIn("commissions and slippage", result["limitations"][0])

    def test_favorable_wick_without_profitable_close_is_only_possible(self):
        life = lifecycle([event(0, "open", 1, 10, 10), event(1800000, "close", 0, 10, 8, True, -2, 2)])
        bars = {"bars": [(0, 9, 12, 8), (900000, 9, 11, 8)], "timeframe": "15m", "source_kind": "test"}
        result = audit_lifecycle(life, bars, 1800000)
        self.assertEqual(result["classification"], "POSSIBLE_INTRABAR_ONLY_OHLC_BOUND")
        self.assertIsNone(result["first_confirmed_closed_bar_opportunity"])
        self.assertIsNotNone(result["first_possible_intrabar_bound"])

    def test_weighted_average_after_augment_is_used_for_later_bars(self):
        life = lifecycle([event(0, "open", 1, 10, 10), event(900000, "augment", 2, 15, 20),
                          event(2700000, "close", 0, 15, 14, True, -2, 3)])
        bars = {"bars": [(900000, 16, 17, 14), (1800000, 16, 18, 15)], "timeframe": "15m", "source_kind": "test"}
        result = audit_lifecycle(life, bars, 3000000)
        evidence = result["first_confirmed_closed_bar_opportunity"]
        self.assertEqual(result["classification"], "CLOSED_BAR_GROSS_PROFIT_OPPORTUNITY")
        self.assertEqual(evidence["weighted_average_entry_price"], 15)
        self.assertEqual(evidence["qty"], 2)

    def test_short_uses_low_as_favorable_ohlc_extreme(self):
        life = lifecycle([event(0, "open", 1, 10, 10), event(1800000, "close", 0, 10, 12, True, -2, 2)], "SHORT")
        bars = {"bars": [(0, 11, 12, 8), (900000, 11, 12, 9)], "timeframe": "15m", "source_kind": "test"}
        result = audit_lifecycle(life, bars, 1800000)
        self.assertEqual(result["classification"], "POSSIBLE_INTRABAR_ONLY_OHLC_BOUND")
        self.assertEqual(result["first_possible_intrabar_bound"]["low_price"], 8)

    def test_cutoff_excludes_unclosed_future_candles(self):
        life = lifecycle([event(0, "open", 1, 10, 10), event(2700000, "close", 0, 10, 8, True, -2, 2)])
        bars = {"bars": [(0, 11, 12, 9), (900000, 11, 12, 9), (1800000, 11, 12, 9)],
                "timeframe": "15m", "source_kind": "test"}
        result = audit_lifecycle(life, bars, 1700000)
        self.assertEqual(result["eligible_bar_count"], 1)
        self.assertEqual(result["future_or_post_cutoff_excluded_bar_count"], 1)

    def test_mixed_realized_assets_do_not_claim_combined_profitability(self):
        life = lifecycle([event(0, "open", 2, 10, 10), event(900000, "reduce", 1, 10, 11, False, 1, 2),
                          {**event(1800000, "augment", 2, 10, 9, False, -3, 3), "realized_pnl_asset": "USDC"},
                          event(3600000, "close", 0, 10, 8, True, -4, 4)])
        bars = {"bars": [(1800000, 12, 13, 11), (2700000, 12, 13, 11)], "timeframe": "15m", "source_kind": "test"}
        result = audit_lifecycle(life, bars, 3600000)
        self.assertEqual(result["classification"], "NO_CLOSED_BAR_GROSS_PROFIT_EVIDENCE")

    def test_inventory_only_audits_recorded_reduction_or_close_stops(self):
        doc = {"schema_version": 1, "source": {"sha256": "abc", "window_end_ms": 2000000}, "lifecycles": [
            lifecycle([event(0, "open", 1, 10, 10), event(900000, "close", 0, 10, 9, True, -1, 2)]),
            lifecycle([event(0, "open", 1, 10, 10)])]}
        result = audit_inventory(doc, None)
        self.assertEqual(result["fixed_stop_lifecycle_count"], 1)
        self.assertEqual(result["counts_by_classification"], {"UNVERIFIED_NO_VALID_BAR_SOURCE": 1})


if __name__ == "__main__":
    unittest.main()

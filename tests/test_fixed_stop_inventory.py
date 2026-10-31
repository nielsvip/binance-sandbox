import unittest

from tools.fixed_stop_inventory import build_neural_builder_handoff, reconstruct_account


def fill(trade_id, order_id, side, qty, price, realized=0, commission=0, commission_asset="BNB", timestamp=1000):
    return {"id": trade_id, "orderId": order_id, "side": side, "positionSide": "LONG", "qty": str(qty),
            "price": str(price), "realizedPnl": str(realized), "commission": str(commission),
            "commissionAsset": commission_asset, "marginAsset": "USDT", "time": timestamp}


def account_for(fills, orders=None, ending_qty=0):
    return {"trades": {"ABCUSDT": fills}, "orders": {"ABCUSDT": orders or []},
            "positions": [{"symbol": "ABCUSDT", "positionSide": "LONG", "positionAmt": str(ending_qty)}]}


class FixedStopInventoryTests(unittest.TestCase):
    def test_partial_fills_augment_and_partial_reductions_preserve_weighted_cost(self):
        fills = [fill(1, 10, "BUY", 2, 10), fill(2, 10, "BUY", 3, 20, timestamp=2000),
                 fill(3, 11, "BUY", 5, 30, timestamp=3000), fill(4, 12, "SELL", 4, 25, 20, timestamp=4000),
                 fill(5, 13, "SELL", 6, 30, 40, timestamp=5000)]
        result = reconstruct_account("acct", account_for(fills), 0, 6000)
        life = result["lifecycles"][0]
        self.assertEqual(life["status"], "completed")
        self.assertEqual(life["opening_qty_actual_fills"], 10)
        self.assertAlmostEqual(life["events"][2]["weighted_average_entry_price_after"], 23)
        self.assertAlmostEqual(life["events"][3]["weighted_average_entry_price_after"], 23)
        self.assertEqual([e["event_type"] for e in life["events"]], ["open", "augment", "augment", "reduce", "close"])
        self.assertAlmostEqual(sum(e["realized_pnl_raw"] for e in life["events"]), 60)

    def test_carried_in_inventory_reconciles_from_ending_position_and_window_flow(self):
        fills = [fill(1, 20, "SELL", 2, 15, 7, timestamp=2000)]
        result = reconstruct_account("acct", account_for(fills, ending_qty=3), 1000, 3000)
        life = result["lifecycles"][0]
        self.assertTrue(life["carried_in"])
        self.assertEqual(life["opening_qty_estimate"], 5)
        self.assertEqual(life["opening_qty_method"], "ending_position_minus_signed_window_flows")
        self.assertEqual(life["ending_qty_inferred"], 3)
        self.assertEqual(life["ending_qty_observed"], 3)
        self.assertEqual(life["status"], "open")
        self.assertIsNone(life["events"][0]["price"])

    def test_repeated_entry_after_flat_close_starts_a_new_lifecycle(self):
        fills = [fill(1, 1, "BUY", 1, 10), fill(2, 2, "SELL", 1, 11, 1, timestamp=2000),
                 fill(3, 3, "BUY", 2, 12, timestamp=3000), fill(4, 4, "SELL", 2, 10, -4, timestamp=4000)]
        result = reconstruct_account("acct", account_for(fills), 0, 5000)
        self.assertEqual(len(result["lifecycles"]), 2)
        self.assertEqual([life["status"] for life in result["lifecycles"]], ["completed", "completed"])
        self.assertEqual([life["lifecycle_index"] for life in result["lifecycles"]], [1, 2])

    def test_raw_realized_pnl_is_conserved_without_fee_conversion(self):
        fills = [fill(1, 1, "BUY", 2, 10, commission=0.1),
                 fill(2, 2, "SELL", 2, 9, -2.5, commission=0.2, timestamp=2000)]
        result = reconstruct_account("acct", account_for(fills), 0, 3000)
        side = result["per_symbol"]["ABCUSDT"]["position_sides"]["LONG"]
        self.assertAlmostEqual(side["raw_realized_pnl"], -2.5)
        self.assertAlmostEqual(side["lifecycle_realized_pnl"], -2.5)
        self.assertAlmostEqual(side["realized_pnl_conservation_delta"], 0)
        self.assertAlmostEqual(result["account_commission_by_asset"]["BNB"], 0.3)
        self.assertNotIn("net_after_fees", result)

    def test_stop_order_detection_uses_recorded_raw_order_type_or_prefix_only(self):
        fills = [fill(1, 77, "BUY", 1, 10), fill(2, 88, "SELL", 1, 9, -1, timestamp=2000),
                 fill(3, 99, "BUY", 1, 8, timestamp=3000), fill(4, 100, "SELL", 1, 7, -1, timestamp=4000)]
        orders = [{"orderId": 77, "type": "LIMIT", "origType": "LIMIT", "clientOrderId": "ordinary-entry"},
                  {"orderId": 88, "type": "LIMIT", "origType": "STOP_MARKET", "clientOrderId": "ordinary-stop"},
                  {"orderId": 99, "type": "LIMIT", "origType": "LIMIT", "clientOrderId": "ordinary-entry-2"},
                  {"orderId": 100, "type": "STOP_MARKET", "origType": "LIMIT", "clientOrderId": "ordinary-stop-2"}]
        result = reconstruct_account("acct", account_for(fills, orders), 0, 3000)
        ev = result["lifecycles"][0]["events"] + result["lifecycles"][1]["events"]
        self.assertFalse(ev[0]["fixed_stop_order_detected"])
        self.assertTrue(ev[1]["fixed_stop_order_detected"])
        self.assertEqual(ev[1]["fixed_stop_detection_basis"], "order_type:STOP_MARKET")
        self.assertTrue(ev[3]["fixed_stop_order_detected"])
        self.assertEqual(ev[3]["fixed_stop_detection_basis"], "order_type:STOP_MARKET")
        self.assertEqual(ev[3]["order_orig_type"], "LIMIT")
        self.assertEqual(ev[3]["order_type"], "STOP_MARKET")

    def test_explicit_entry_reason_only_and_open_position_reported(self):
        fills = [fill(1, 90, "BUY", 2, 10)]
        orders = [{"orderId": 90, "type": "LIMIT", "clientOrderId": "x", "decisionReason": "RECORDED_SWITCH_A"}]
        result = reconstruct_account("acct", account_for(fills, orders, ending_qty=2), 0, 3000)
        life = result["lifecycles"][0]
        self.assertEqual(life["status"], "open")
        self.assertEqual(life["entry_switch_attribution"], "RECORDED_SWITCH_A")
        self.assertEqual(result["lifecycle_counts"]["open"], 1)

    def test_exact_generic_history_match_is_not_promoted_to_switch(self):
        fills = [fill(1, 91, "BUY", 1, 10)]
        linked = {"91": [{"source": "history", "order_id_relationship": "order_id", "recorded_type": "OPEN",
                           "recorded_reason": "board holds since bar", "recorded_provenance": {"src": "S1_VECTOR_DIRECT", "switch": "VECTOR_DIRECT"}}]}
        result = reconstruct_account("acct", account_for(fills, ending_qty=1), 0, 3000, linked)
        life = result["lifecycles"][0]
        self.assertEqual(life["entry_switch_attribution"], "unknown")
        self.assertEqual(len(life["events"][0]["order_id_linked_history_records"]), 1)
        self.assertEqual(result["entry_provenance_coverage"]["entry_fill_events_with_verified_switch"], 0)

    def test_exact_vec_entry_history_switch_can_be_attributed(self):
        fills = [fill(1, 92, "BUY", 1, 10)]
        linked = {"92": [{"source": "history", "source_path": "data/history/acct/ABCUSDT_LONG.jsonl",
                           "order_id_relationship": "order_id", "recorded_type": "OPEN", "recorded_ts": "2026-10-31T00:00:00Z",
                           "recorded_reason": "REENTRY_LOWER_HIGH", "recorded_provenance": {"src": "VEC_EXACT", "switch": "REENTRY_ON_HIGHER_LOW_ENABLED"}}]}
        result = reconstruct_account("acct", account_for(fills, ending_qty=1), 0, 3000, linked)
        life = result["lifecycles"][0]
        self.assertEqual(life["entry_switch_attribution"], "REENTRY_ON_HIGHER_LOW_ENABLED")
        self.assertEqual(result["entry_provenance_coverage"]["entry_fill_events_with_verified_switch"], 1)

    def test_neural_builder_handoff_preserves_per_symbol_unknown_coverage(self):
        fills = [fill(1, 93, "BUY", 1, 10)]
        linked = {"93": [{"source": "history", "order_id_relationship": "order_id", "recorded_type": "OPEN",
                           "recorded_provenance": {"src": "S1_VECTOR_DIRECT", "switch": "VECTOR_DIRECT"}}]}
        inventory = reconstruct_account("acct", account_for(fills, ending_qty=1), 0, 3000, linked)
        handoff = build_neural_builder_handoff({"acct": inventory}, "synthetic.json")
        self.assertEqual(handoff["coverage"]["entry_fill_events"], 1)
        self.assertEqual(handoff["coverage"]["entry_fill_events_with_verified_switch"], 0)
        self.assertEqual(handoff["cross_symbol"]["acct:ABCUSDT_LONG"]["entry_fill_events_with_order_id_history_match"], 1)


if __name__ == "__main__":
    unittest.main()

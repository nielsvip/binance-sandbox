import json
import unittest
import tempfile
from pathlib import Path

import numpy as np
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
        self.assertEqual(result["first_entry_fill_timestamp_ms"], 0)
        self.assertEqual(result["first_stop_fill_timestamp_ms"], 3600000)
        self.assertEqual(result["entry_to_first_stop_seconds"], 3600)
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

    def test_source_census_excludes_cutoff_crossing_npz_rows(self):
        doc = {"schema_version": 1, "source": {"sha256": "abc", "window_end_ms": 1800000}, "lifecycles": [
            lifecycle([event(0, "open", 1, 10, 10), event(2700000, "close", 0, 10, 9, True, -1, 2)])]}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ABCUSDT.npz"
            np.savez_compressed(path, timestamps=np.array([0, 900, 1800]), close_15m=np.array([11, 11, 11]),
                                high_15m=np.array([12, 12, 12]), low_15m=np.array([9, 9, 9]))
            result = audit_inventory(doc, temp_dir)
        self.assertEqual(result["source_inventory"]["sha256"], "abc")
        self.assertNotIn("bars", result["source_inventory"])
        self.assertEqual(result["post_cutoff_or_cutoff_crossing_source_rows_excluded_count"], 1)
        self.assertEqual(result["bar_sources_by_symbol"]["ABCUSDT"]["rows_usable_by_cutoff_count"], 2)
        evidence = result["lifecycles"][0]
        self.assertEqual(evidence["eligible_bar_count"], 2)

    def test_one_minute_refinement_can_resolve_a_trade_shorter_than_one_npz_bar(self):
        doc = {"schema_version": 1, "source": {"sha256": "abc", "window_end_ms": 200000}, "lifecycles": [
            lifecycle([event(0, "open", 1, 10, 10), event(120000, "close", 0, 10, 9, True, -1, 2)])]}
        with tempfile.TemporaryDirectory() as temp_dir:
            fallback_dir = Path(temp_dir) / "fallback"
            fallback_dir.mkdir()
            np.savez_compressed(Path(temp_dir) / "ABCUSDT.npz", timestamps=np.array([0]),
                                close_15m=np.array([11]), high_15m=np.array([12]), low_15m=np.array([9]))
            (fallback_dir / "ABCUSDT_1m.json").write_text(json.dumps([
                {"timestamp": "1970-01-01T00:00:00.000Z", "close": 11, "high": 12, "low": 9},
                {"timestamp": "1970-01-01T00:01:00.000Z", "close": 10, "high": 11, "low": 9}]))
            result = audit_inventory(doc, temp_dir, fallback_dir)
        row = result["lifecycles"][0]
        self.assertEqual(row["primary_15m_classification"], "UNVERIFIED_NO_FULL_BAR_WITHIN_LIFECYCLE")
        self.assertEqual(row["one_minute_refinement_classification"], "CLOSED_BAR_GROSS_PROFIT_OPPORTUNITY")
        self.assertEqual(row["classification"], "CLOSED_BAR_GROSS_PROFIT_OPPORTUNITY")
        self.assertEqual(row["bar_timeframe"], "1m")

    def test_intent_join_labels_emitted_actions_without_claiming_candidate_coverage(self):
        doc = {"schema_version": 1, "source": {"sha256": "abc", "window_end_ms": 2700000}, "lifecycles": [
            lifecycle([event(0, "open", 1, 10, 10), event(1800000, "close", 0, 10, 9, True, -1, 2)])]}
        with tempfile.TemporaryDirectory() as temp_dir:
            np.savez_compressed(Path(temp_dir) / "ABCUSDT.npz", timestamps=np.array([0, 900]),
                                close_15m=np.array([11, 10]), high_15m=np.array([12, 11]), low_15m=np.array([9, 9]))
            ledger = Path(temp_dir) / "intent_ledger.jsonl"
            ledger.write_text(json.dumps({"account": "acct", "ss": "ABCUSDT_LONG", "bar_ts": 0,
                                          "engine_type": "REDUCE", "reason": "TEST_REDUCE"}) + "\n")
            result = audit_inventory(doc, temp_dir, intent_ledger_path=ledger)
        evidence = result["lifecycles"][0]["forward_intent_evidence"]
        self.assertEqual(evidence["status"], "ACTION_ROWS_AT_PROFITABLE_BAR")
        self.assertEqual(evidence["exact_profitable_bar_intent_count"], 1)
        self.assertIn("does not prove no exit candidate", evidence["source_semantics"])
        self.assertEqual(result["forward_intent_ledger_source"]["parsed_action_rows"], 1)


if __name__ == "__main__":
    unittest.main()

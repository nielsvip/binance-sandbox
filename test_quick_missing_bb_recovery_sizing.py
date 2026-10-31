"""Behavior checks for the shared BB_RECOVERY native REDUCE sizing helper."""

import unittest
from dataclasses import replace

import numpy as np

import v12_quick_engine as v12

from vec_decisions.quick_missing_bb_recovery_sizing import (
    bb_recovery_reduce_qty,
    bb_recovery_reduce_qty_scalar,
    bb_recovery_qty_limits,
)


class BBRecoveryReduceSizingTests(unittest.TestCase):
    def setUp(self):
        self.qty = 10.0
        self.min_qty = 0.1
        self.args = (self.qty, self.min_qty, 0.7, 0.5, 0.15, 0.25, 0.5)

    def test_native_gain_bands_match_scalar_source(self):
        self.assertAlmostEqual(bb_recovery_reduce_qty_scalar(*self.args), 2.5)
        self.assertAlmostEqual(
            bb_recovery_reduce_qty_scalar(10.0, 0.1, 0.3, 0.2, 0.15, 0.25, 0.5),
            1.5,
        )
        self.assertAlmostEqual(
            bb_recovery_reduce_qty_scalar(10.0, 0.1, 2.0, 0.5, 0.15, 0.25, 0.5),
            5.0,
        )
        self.assertAlmostEqual(
            bb_recovery_reduce_qty_scalar(10.0, 0.1, 3.0, 0.5, 0.15, 0.25, 0.5),
            9.91,
        )

    def test_vector_mask_sizes_each_live_gain_band(self):
        got = bb_recovery_reduce_qty(
            np.array([10.0, 10.0, 10.0, 10.0]),
            0.1,
            np.array([0.3, 0.7, 2.0, 3.0]),
            0.5,
            0.15,
            0.25,
            0.5,
        )
        np.testing.assert_allclose(got, [9.91, 2.5, 5.0, 9.91])

    def test_partial_ledger_reduction_retains_position_and_records_reduce_event(self):
        position = {"qty": 10.0, "avg_price": 100.0, "realized": 0.0, "fees": 0.0}
        px = 101.0
        reduce_qty = bb_recovery_reduce_qty_scalar(10.0, 0.1, 0.7, 0.5, 0.15, 0.25, 0.5)
        realized = (px - position["avg_price"]) * reduce_qty
        position["realized"] += realized
        position["fees"] += abs(reduce_qty * px) * 0.0005
        position["qty"] -= reduce_qty
        event = {"type": "REDUCE", "qty": reduce_qty, "price": px, "reason": "BB_RECOVERY_EXIT_TRADIER_LONG"}
        self.assertEqual(event["type"], "REDUCE")
        self.assertAlmostEqual(event["qty"], 2.5)
        self.assertAlmostEqual(position["qty"], 7.5)
        self.assertAlmostEqual(position["realized"], 2.5)
        self.assertGreater(position["fees"], 0.0)

    def test_symbol_limits_use_frozen_contract_min_qty_and_order_floor(self):
        pos_min_qty, order_min_qty = bb_recovery_qty_limits(
            "1000PEPEUSDC", 100.0, replace(v12.QuickConfig(), MIN_POSITION_SIZE=1.0)
        )
        self.assertAlmostEqual(pos_min_qty, 1.0)
        self.assertAlmostEqual(order_min_qty, 1.2)

    def test_simulate_one_books_bb_recovery_as_partial_reduce_and_keeps_dust_open(self):
        n = 120
        close = np.full(n, 100.0)
        close[1:] = 103.0
        bb_mask = np.zeros(n, dtype=bool)
        bb_mask[1] = True
        arrays = {
            "timestamps": np.arange(n, dtype=np.int64) * 180,
            "close_3m": close,
            "bb_upper_1h": np.full(n, 104.0),
            "bb_lower_1h": np.full(n, 99.0),
            "stoch_k_1h": np.full(n, 80.0),
        }

        def entry_signals(_npz, _n, _is_long, _cfg):
            return np.zeros(_n, dtype=bool)

        def exit_signals(_npz, _n, _is_long, cfg, _entry_sig=None):
            cfg.BB_RECOVERY_EXIT_INFO = {"mask": bb_mask.copy()}
            return bb_mask.copy()

        cfg = replace(
            v12.QuickConfig(),
            MODE="crypto",
            BASE_TF="3m",
            START_POSITION_SIZE=220.0,
            MIN_POSITION_SIZE=1.0,
            MIN_HOLD_BARS=0,
            COOLDOWN_BARS=0,
            BB_RECOVERY_EXIT_ENABLED_TRADIER=True,
            COMMISSION_BUFFER_PCT=0.08,
            VEC_EXIT_SIG_IS_TRIGGER=True,
            CYCLE_TP_TIERED_ENABLED=False,
        )
        cfg.apply_cat_side_defaults("CRYPTO_LONG")
        cfg.MIN_HOLD_BARS = 0
        cfg.CYCLE_TP_TIERED_ENABLED = False
        cfg.PARTIAL_PROFIT_LOCK_ENABLED = False
        original_entry = v12.compute_entry_signals
        original_exit = v12.compute_exit_signals
        try:
            v12.compute_entry_signals = entry_signals
            v12.compute_exit_signals = exit_signals
            result = v12.simulate_one(arrays, "1000PEPEUSDC", True, cfg, force_initial_seed=True)
        finally:
            v12.compute_entry_signals = original_entry
            v12.compute_exit_signals = original_exit
        ledger = result["ledger"]
        reductions = [row for row in ledger if row.get("reason") == "BB_RECOVERY_EXIT_TRADIER_LONG"]
        self.assertEqual(len(reductions), 1)
        self.assertEqual(reductions[0]["type"], "REDUCE")
        self.assertAlmostEqual(reductions[0]["qty"], 1.3, places=7)
        self.assertLess(2.2 - reductions[0]["qty"], 1.0)
        self.assertFalse(any(row.get("exit_reason") == "BB_RECOVERY_EXIT_TRADIER_LONG" for row in ledger))
        final_marks = [row for row in ledger if row.get("reason") == "FINAL_MTM"]
        self.assertEqual(len(final_marks), 1)
        self.assertTrue(final_marks[0].get("unrealised"))
        self.assertAlmostEqual(final_marks[0]["qty"], 0.9, places=7)

    def test_simulate_one_honors_native_net_gain_veto_before_reduce(self):
        n = 120
        close = np.full(n, 100.0)
        close[1:] = 100.05
        bb_mask = np.zeros(n, dtype=bool)
        bb_mask[1] = True
        arrays = {
            "timestamps": np.arange(n, dtype=np.int64) * 180,
            "close_3m": close,
            "bb_upper_1h": np.full(n, 101.0),
            "bb_lower_1h": np.full(n, 99.0),
            "stoch_k_1h": np.full(n, 80.0),
        }

        def entry_signals(_npz, _n, _is_long, _cfg):
            return np.zeros(_n, dtype=bool)

        def exit_signals(_npz, _n, _is_long, cfg, _entry_sig=None):
            cfg.BB_RECOVERY_EXIT_INFO = {"mask": bb_mask.copy()}
            return bb_mask.copy()

        cfg = replace(
            v12.QuickConfig(),
            MODE="crypto",
            BASE_TF="3m",
            START_POSITION_SIZE=220.0,
            MIN_POSITION_SIZE=1.0,
            MIN_HOLD_BARS=0,
            COOLDOWN_BARS=0,
            BB_RECOVERY_EXIT_ENABLED_TRADIER=True,
            COMMISSION_BUFFER_PCT=0.08,
            VEC_EXIT_SIG_IS_TRIGGER=True,
        )
        cfg._template_layer = "CRYPTO_LONG"
        cfg.CYCLE_TP_TIERED_ENABLED = False
        cfg.PARTIAL_PROFIT_LOCK_ENABLED = False
        original_entry = v12.compute_entry_signals
        original_exit = v12.compute_exit_signals
        try:
            v12.compute_entry_signals = entry_signals
            v12.compute_exit_signals = exit_signals
            result = v12.simulate_one(arrays, "1000PEPEUSDC", True, cfg, force_initial_seed=True)
        finally:
            v12.compute_entry_signals = original_entry
            v12.compute_exit_signals = original_exit
        ledger = result["ledger"]
        self.assertFalse(any(row.get("reason") == "BB_RECOVERY_EXIT_TRADIER_LONG" for row in ledger))
        final_marks = [row for row in ledger if row.get("reason") == "FINAL_MTM"]
        self.assertEqual(len(final_marks), 1)
        self.assertAlmostEqual(final_marks[0]["qty"], 2.2, places=7)

    def test_nonfinite_or_negative_position_fails_closed(self):
        got = bb_recovery_reduce_qty(
            np.array([-1.0, np.nan]), 0.1, np.array([1.0, 1.0]), 0.5, 0.15, 0.25, 0.5
        )
        np.testing.assert_allclose(got, [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()

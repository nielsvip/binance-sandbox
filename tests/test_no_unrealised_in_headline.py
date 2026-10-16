"""USER 2026-10-16: "make sure no more exaggeration of technicals and gains can ever take place and the chart
html files represent the trades with the exact number that are shown in the headers".

Measured that day over the 439 latest published charts: 138 counted a FINAL_MTM row — an OPEN position marked to
market at the window edge, never sold — inside their gain, trade count and WR. These tests pin the contract that
makes that impossible: a mark-to-market row is reported, never summed.
"""
import importlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import metrics_guard as MG


def _ledger():
    return [
        {"type": "CLOSE", "qty": 1.0, "entry_price": 100.0, "exit_price": 110.0, "deployed": 100.0, "pnl_dollars": 10.0, "pnl_pct": 10.0, "exit_reason": "WT_CROSS_EXIT"},
        {"type": "REDUCE", "qty": 0.0, "entry_price": 100.0, "exit_price": 105.0, "deployed": 100.0, "pnl_dollars": 5.0, "pnl_pct": 5.0, "exit_reason": "PPL_TP"},
        {"type": "CLOSE", "qty": 1.0, "entry_price": 100.0, "exit_price": 140.0, "deployed": 100.0, "pnl_dollars": 40.0, "pnl_pct": 40.0, "exit_reason": "FINAL_MTM"},
        {"type": "OPEN", "qty": 1.0, "entry_price": 100.0, "pnl_dollars": None},
        {"type": "CLOSE", "qty": 1.0, "entry_price": 1.0, "exit_price": 2.0, "pnl_dollars": 3.0, "pnl_pct": 3.0, "seeded_prewindow": True, "exit_reason": "SEED"},
    ]


class TestUnrealisedNeverCounted(unittest.TestCase):
    def setUp(self):
        # Exclusion mode is opt-in since USER 2026-10-16 (marks count by default); these tests pin that mode.
        os.environ["METRICS_COUNT_UNREALISED_MTM"] = "0"
        importlib.reload(MG)

    def tearDown(self):
        os.environ.pop("METRICS_COUNT_UNREALISED_MTM", None)
        importlib.reload(MG)

    def test_realised_closes_drops_the_mark(self):
        real = MG.realised_closes(_ledger())
        self.assertEqual([t["exit_reason"] for t in real], ["WT_CROSS_EXIT", "PPL_TP"])
        self.assertEqual(sum(t["pnl_dollars"] for t in real), 15.0)

    def test_mark_is_reported_not_hidden(self):
        self.assertEqual(len(MG.unrealised_marks(_ledger())), 1)
        self.assertEqual(MG.unrealised_dollars(_ledger()), 40.0)

    def test_trade_count_excludes_the_mark(self):
        self.assertEqual(len(MG.realised_closes(_ledger())), 2)

    def test_pool_sharpe_excludes_the_mark(self):
        led = _ledger()
        with_mark = MG.pool_sharpe([10.0, 5.0, 40.0])
        got = MG.pool_sharpe_from_ledger(led)
        self.assertAlmostEqual(got, MG.pool_sharpe([10.0, 5.0]), places=9)
        self.assertNotAlmostEqual(got, with_mark, places=6)

    def test_detection_covers_the_variants_and_nothing_else(self):
        for reason in ("FINAL_MTM", "final_mtm_long", "WINDOW_END_MTM", "MTM_FINAL", "OPEN_AT_WINDOW_END"):
            self.assertTrue(MG.is_unrealised_mark({"exit_reason": reason}), reason)
        for reason in ("WT_CROSS_EXIT", "DC_HARD_STOP_15M", "PPL_TP", "REDUCE_TO_FLAT", "", None):
            self.assertFalse(MG.is_unrealised_mark({"exit_reason": reason}), reason)
        self.assertTrue(MG.is_unrealised_mark({"unrealised": True}))
        self.assertFalse(MG.is_unrealised_mark("not a dict"))
        self.assertFalse(MG.is_unrealised_mark({}))

    def test_kill_switch_restores_the_old_number(self):
        os.environ["METRICS_COUNT_UNREALISED_MTM"] = "1"
        importlib.reload(MG)
        real = MG.realised_closes(_ledger())
        self.assertEqual(sum(t["pnl_dollars"] for t in real), 55.0)
        self.assertEqual(len(real), 3)

    def test_empty_and_malformed_ledgers_are_safe(self):
        for led in ([], None, [None, 3, "x"], [{}]):
            self.assertEqual(MG.realised_closes(led), [] if led != [{}] else [])
            self.assertEqual(MG.unrealised_dollars(led), 0.0)


class TestChartSplitTagsTheMark(unittest.TestCase):
    def setUp(self):
        os.environ["METRICS_COUNT_UNREALISED_MTM"] = "0"  # exclusion mode (opt-in since USER 2026-10-16)
        importlib.reload(MG)

    def tearDown(self):
        os.environ.pop("METRICS_COUNT_UNREALISED_MTM", None)
        importlib.reload(MG)

    def test_mark_moves_to_events_tagged_unrealised(self):
        from tools.opt.hires_chart import _split_ledger
        trades, events = _split_ledger(_ledger(), [0.0, 1.0, 2.0])
        self.assertEqual(len(trades), 2)
        marks = [e for e in events if e.get("unrealised")]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["type"], "UNREALISED_MARK")
        self.assertEqual(marks[0]["pnl_dollars"], 40.0)

    def test_the_mark_is_not_an_undrawn_trade(self):
        """The fail-closed `_undrawn` guard must not trip on a tagged mark (it would block every chart)."""
        from tools.opt.hires_chart import _split_ledger
        _, events = _split_ledger(_ledger(), [0.0, 1.0, 2.0])
        undrawn = [e for e in events if e.get("pnl_dollars") is not None and not e.get("seeded_prewindow") and not e.get("unrealised")]
        self.assertEqual(undrawn, [])


if __name__ == "__main__":
    unittest.main()


class TestMarkCountsByDefault(unittest.TestCase):
    """USER 2026-10-16: an open position marked at the window edge counts — an open LOSER must never vanish from a headline."""
    def setUp(self):
        os.environ.pop("METRICS_COUNT_UNREALISED_MTM", None)
        importlib.reload(MG)

    def tearDown(self):
        os.environ.pop("METRICS_COUNT_UNREALISED_MTM", None)
        importlib.reload(MG)

    def test_default_counts_the_mark(self):
        real = MG.realised_closes(_ledger())
        self.assertEqual(len(real), 3)
        self.assertEqual(sum(t["pnl_dollars"] for t in real), 55.0)

    def test_open_loser_is_counted(self):
        led = [{"type": "CLOSE", "pnl_dollars": 10.0, "pnl_pct": 10.0, "exit_reason": "WT_CROSS_EXIT"}, {"type": "CLOSE", "pnl_dollars": -30.0, "pnl_pct": -30.0, "exit_reason": "FINAL_MTM", "unrealised": True}]
        self.assertEqual(sum(t["pnl_dollars"] for t in MG.realised_closes(led)), -20.0)


class TestChartDrawsCountedMark(unittest.TestCase):
    def setUp(self):
        os.environ.pop("METRICS_COUNT_UNREALISED_MTM", None)
        importlib.reload(MG)

    def test_default_chart_draws_the_mark_as_a_counted_trade(self):
        from tools.opt.hires_chart import _split_ledger
        trades, events = _split_ledger(_ledger(), [0.0, 1.0, 2.0])
        self.assertEqual(len(trades), 3)
        self.assertTrue(any(str(t.get("exit_reason")) == "FINAL_MTM" for t in trades))

"""USER 2026-10-16 ("unlock and fix"): the three v12_quick_engine defects that let a headline exaggerate itself
and let a chart draw fewer trades than its header counted.

1. FINAL_MTM — the OPEN position marked to the last close was appended with `pnl_dollars` and therefore entered
   gain, trades, WR, mean_deployed, peak capital and drawdown. 149 of the 439 latest published charts carried one.
2. Four reduce-to-flat sites wrote `qty: 0.0` while the closed size (`_hq_rq`, `_ppl_qty`, `_dd_qty`, `_w2_tpq`)
   sat in scope, so `qty*(exit-entry)` contradicted the row's own P&L sign on 70 charts.
3. SATOSHIT_EXIT_PARTIAL_PCT (default 0.7) shrank the position on every qualifying bar and wrote NOTHING to
   trades or events — invisible legs whose dollars surfaced inside a later lifecycle row.

These are source-level contracts: they hold without an NPZ, so they run anywhere and cannot silently rot.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "v12_quick_engine.py")).read()


class TestFinalMtmNotCounted(unittest.TestCase):
    def test_mtm_row_is_tagged_unrealised(self):
        i = SRC.index('"exit_reason": "FINAL_MTM"')
        self.assertIn('"unrealised": True', SRC[i:i + 1200])

    def test_metrics_population_excludes_unrealised(self):
        self.assertIn('_mtrades = (', SRC)
        blk = SRC[SRC.index("_mtrades = ("):SRC.index("_mtrades = (") + 400]
        self.assertIn('not t.get("unrealised")', blk)
        self.assertIn("_count_mtm", blk)

    def test_the_kill_switch_exists_and_matches_metrics_guard(self):
        self.assertIn('os.environ.get("METRICS_COUNT_UNREALISED_MTM", "1") == "1"', SRC)
        mg = open(os.path.join(ROOT, "metrics_guard.py")).read()
        self.assertIn('os.environ.get("METRICS_COUNT_UNREALISED_MTM", "1") == "1"', mg)

    def test_mtm_row_still_reaches_the_ledger(self):
        """Excluded from METRICS, kept in the ledger — the chart must still be able to show it."""
        i = SRC.index('"exit_reason": "FINAL_MTM"')
        self.assertIn("trades.append(", SRC[max(0, i - 1500):i])


class TestClosedSizeIsStated(unittest.TestCase):
    def test_no_zero_qty_ledger_row_remains(self):
        self.assertEqual(SRC.count('"qty": 0.0'), 0)

    def test_every_reduce_to_flat_row_states_its_closed_size(self):
        for var in ("_hq_rq", "_ppl_qty", "_dd_qty", "_w2_tpq"):
            self.assertIn(f'"closed_qty": float({var})', SRC, var)
            self.assertIn(f'"qty": float({var})', SRC, var)

    def test_lifecycle_pnl_is_flagged(self):
        """pnl_dollars on these rows is pos['realized']-fees for the WHOLE lifecycle; readers must be told."""
        self.assertGreaterEqual(SRC.count('"pnl_is_lifecycle": True'), 4)


class TestSatoshitPartialIsRecorded(unittest.TestCase):
    def _branch(self):
        i = SRC.index("if 0 < satoshit_partial < 1.0:")
        return SRC[i:i + 3600]

    def test_the_partial_leg_writes_a_reduce_event(self):
        b = self._branch()
        self.assertIn("events.append(", b)
        self.assertIn('"qty": float(reduce_qty)', b)
        self.assertIn("SATOSHIT_EXIT_PARTIAL", b)

    def test_a_dust_flat_lifecycle_closes_instead_of_becoming_a_mark(self):
        b = self._branch()
        self.assertIn('if pos["qty"] <= 1e-9:', b)
        self.assertIn("SATOSHIT_EXIT_PARTIAL_FLAT", b)
        self.assertIn("trades.append(", b)
        self.assertIn("has_closed_before = True", b)

    def test_the_sizing_and_exit_decision_are_untouched(self):
        b = self._branch()
        self.assertIn('reduce_qty = pos["qty"] * satoshit_partial', b)
        self.assertIn('pos["qty"] -= reduce_qty', b)
        self.assertIn("pos[\"realized\"] += realized", b)
        # still a `continue` branch: the exit is deferred, exactly as before
        self.assertTrue(re.search(r"\n\s+continue\n", b))


if __name__ == "__main__":
    unittest.main()

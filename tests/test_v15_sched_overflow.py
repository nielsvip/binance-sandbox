"""Overflow venues (USER 2026-10-25): idle stocks workers take crypto 30D.

s5/s6 carry "overflow_venues": ["crypto"]; own work always sorts first and the
take gate fires only when the host launched nothing and runs nothing own-venue.
Hosts without overflow_venues keep legacy behaviour byte-identical.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import v15_fleet_scheduler as _SCHED

_S6 = {"name": "s6", "venues": ["stocks"], "sides": ["SHORT"], "overflow_venues": ["crypto"]}
_S5 = {"name": "s5", "venues": ["stocks"], "sides": ["LONG"], "overflow_venues": ["crypto"]}
_S2 = {"name": "s2", "venues": ["crypto"], "sides": ["LONG"]}


class OverflowVenuesTest(unittest.TestCase):
    def test_configured(self):
        self.assertEqual(_SCHED._host_overflow_venues(_S6), ["crypto"])

    def test_absent_fail_closed(self):
        self.assertEqual(_SCHED._host_overflow_venues(_S2), [])
        self.assertEqual(_SCHED._host_overflow_venues({}), [])
        self.assertEqual(_SCHED._host_overflow_venues(None), [])

    def test_junk_fail_closed(self):
        self.assertEqual(_SCHED._host_overflow_venues({"venues": ["stocks"], "overflow_venues": ["mars"]}), [])
        self.assertEqual(_SCHED._host_overflow_venues({"venues": ["stocks"], "overflow_venues": ["stocks"]}), [])


class OverflowTakeTest(unittest.TestCase):
    def test_idle_takes_overflow(self):
        self.assertTrue(_SCHED._overflow_take_ok(_S6, "XRPUSDC", 0, 0))

    def test_under_90_takes(self):
        self.assertTrue(_SCHED._overflow_take_ok(_S6, "XRPUSDC", 0, 89.9))

    def test_at_90_blocks(self):
        self.assertFalse(_SCHED._overflow_take_ok(_S6, "XRPUSDC", 0, 90))

    def test_over_90_blocks(self):
        self.assertFalse(_SCHED._overflow_take_ok(_S6, "XRPUSDC", 0, 150))

    def test_own_venue_never_overflow(self):
        self.assertFalse(_SCHED._overflow_take_ok(_S6, "NVDA", 0, 0))

    def test_launch_this_tick_blocks(self):
        self.assertFalse(_SCHED._overflow_take_ok(_S6, "XRPUSDC", 1, 0))

    def test_no_overflow_host(self):
        self.assertFalse(_SCHED._overflow_take_ok(_S2, "NVDA", 0, 0))


class OverflowReadinessTest(unittest.TestCase):
    def test_overflow_venues_probed(self):
        self.assertEqual(_SCHED._readiness_cand_venues(_S6), ["stocks", "crypto"])

    def test_no_overflow_unchanged(self):
        self.assertEqual(_SCHED._readiness_cand_venues(_S2), ["crypto"])
        self.assertEqual(_SCHED._readiness_cand_venues({}), [])

    def test_one_free_slot_can_borrow_crypto_from_busy_owner(self):
        need = ["BTCUSDC"]
        eligible = [s for s in need if _SCHED.venue_of(s) in _SCHED._readiness_cand_venues(_S5)]
        self.assertEqual(_SCHED._steal30_pick("s5", 1, False, {"BTCUSDC": "s2"}, eligible,
                                               need, min_free=1), ("BTCUSDC", "s2"))


class OverflowOrderTest(unittest.TestCase):
    def test_legacy_order_untouched_without_venue_first(self):
        new = ["XRPUSDC", "NVDA", "SOLUSDC", "AAPL"]
        a = _SCHED._place_order([], [], list(new), {}, attempts={}, launched_at={})
        b = _SCHED._place_order([], [], list(new), {}, attempts={}, launched_at={}, venue_first=None)
        self.assertEqual([s for s, _ in a], [s for s, _ in b])

    def test_own_venue_first_dominates_tier(self):
        tiers = {"XRPUSDC_LONG": {"tier": "W"}, "XRPUSDC_SHORT": {"tier": "W"},
                 "NVDA_LONG": {"tier": "L"}, "NVDA_SHORT": {"tier": "L"}}
        o = _SCHED._place_order([], [], ["XRPUSDC", "NVDA"], tiers, attempts={}, launched_at={}, venue_first={"stocks"})
        self.assertEqual([s for s, _ in o], ["NVDA", "XRPUSDC"])


if __name__ == "__main__":
    unittest.main()

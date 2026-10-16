import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    path = os.path.join(ROOT, "tools", "v15_fleet_scheduler.py")
    spec = importlib.util.spec_from_file_location("v15_fleet_scheduler_dead", path)
    mod = importlib.util.module_from_spec(spec)
    sys.argv = [path]
    spec.loader.exec_module(mod)
    return mod


class TestNpzDeadSyms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_structural_defects_are_dead(self):
        rb = {"s2": {
            "CDW": {"ok": False, "reason": "load fail could not convert string to float: '2026-10-13T17:30:00.000000Z'"},
            "ADP": {"ok": False, "reason": "too few bars 1"},
            "AEM": {"ok": False, "reason": "no timestamps/timestamp_15m"},
            "AXTI": {"ok": False, "reason": "_compact_to_15m would raise (events=1, lag_min=0)"},
            "MU": {"ok": False, "reason": "len(timestamp_15m)=3 != len(timestamps)=9"},
        }}
        self.assertEqual(self.mod._npz_dead_syms(rb), {"CDW", "ADP", "AEM", "AXTI", "MU"})

    def test_staleness_is_not_dead(self):
        rb = {"s2": {
            "AAPL": {"ok": False, "reason": "tail-stale: tail 420min behind RTH close"},
            "MSFT": {"ok": False, "reason": "stale: last bar 300.0h old"},
            "NVDA": {"ok": False, "reason": "npz missing"},
            "TSLA": {"ok": False, "reason": "history 12.0d < 30d"},
            "AMD": {"ok": False, "reason": "gap 7.5h inside last 30d"},
        }}
        self.assertEqual(self.mod._npz_dead_syms(rb), set())

    def test_ok_on_any_host_is_not_dead(self):
        rb = {"s1": {"CDW": {"ok": False, "reason": "load fail bad zip"}},
              "s2": {"CDW": {"ok": True, "reason": ""}}}
        self.assertEqual(self.mod._npz_dead_syms(rb), set())

    def test_dead_side_cannot_hold_the_venue_phase(self):
        chain_state = {"CDW": {"LONG": ("need30", 1), "SHORT": ("terminal_fresh", None)},
                       "ETHFIUSDC": {"LONG": ("need30", 1), "SHORT": ("need30", 1)}}
        dead = {"CDW"}
        phase, pending = self.mod._venue_phase(chain_state, launchable=lambda s, sd: s not in dead, mode="auto", market_open=True)
        self.assertEqual(list(pending), [])
        self.assertEqual(phase, "crypto")

    def test_live_stock_still_holds_the_phase(self):
        chain_state = {"CDW": {"LONG": ("need30", 1), "SHORT": ("terminal_fresh", None)},
                       "ETHFIUSDC": {"LONG": ("need30", 1), "SHORT": ("need30", 1)}}
        phase, pending = self.mod._venue_phase(chain_state, launchable=lambda s, sd: True, mode="auto", market_open=True)
        self.assertEqual(list(pending), ["CDW_LONG"])
        self.assertEqual(phase, "stocks")

    def test_empty_readiness_is_safe(self):
        self.assertEqual(self.mod._npz_dead_syms({}), set())
        self.assertEqual(self.mod._npz_dead_syms(None), set())


if __name__ == "__main__":
    unittest.main()

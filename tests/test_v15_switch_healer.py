"""Healer daemon: classify/plan pure logic (USER 2026-10-03). No fleet, no writes."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v15_switch_healer import classify_switch


def _cen(zero=0, nonzero=0, pos=0, syms=0):
    return {"tested": zero + nonzero, "zero": zero, "nonzero": nonzero, "pos_sym": pos, "syms_tested": syms, "reasons": {}}


class TestClassify(unittest.TestCase):
    def test_never_mover_no_vec_read_adds(self):
        idx = {"SW": {"vec_func": [], "vec_stub": False, "vec_mod": True, "live": False}}
        v, act, _ = classify_switch("SW", _cen(zero=60, nonzero=0), idx, set())
        self.assertEqual((v, act), ("LEDGER_ADD", "add"))

    def test_live_functional_never_mover_backlogs(self):
        idx = {"SW": {"vec_func": [], "vec_stub": False, "vec_mod": False, "live": True}}
        v, act, _ = classify_switch("SW", _cen(zero=60, nonzero=0), idx, set())
        self.assertEqual((v, act), ("BACKLOG", "ledger_add"))

    def test_vec_read_never_mover_plateaus(self):
        idx = {"SW": {"vec_func": ["simulate_one"], "vec_stub": False, "vec_mod": False, "live": True}}
        v, act, _ = classify_switch("SW", _cen(zero=200, nonzero=0), idx, set())
        self.assertEqual((v, act), ("PLATEAU", "none"))

    def test_ledgered_with_vec_read_rescues(self):
        idx = {"SW": {"vec_func": ["blocked"], "vec_stub": False, "vec_mod": False, "live": True}}
        v, act, _ = classify_switch("SW", _cen(zero=60, nonzero=0), idx, {"SW"})
        self.assertEqual((v, act), ("LEDGER_RESCUE", "remove"))

    def test_ledgered_dead_stays(self):
        idx = {"SW": {"vec_func": [], "vec_stub": True, "vec_mod": False, "live": False}}
        v, act, _ = classify_switch("SW", _cen(zero=60, nonzero=0), idx, {"SW"})
        self.assertEqual((v, act), ("OK", "none"))

    def test_pos_sym_zero_thin_evidence_watches(self):
        idx = {"SW": {"vec_func": ["simulate_one"], "vec_stub": False, "vec_mod": False, "live": True}}
        v, act, _ = classify_switch("SW", _cen(zero=20, nonzero=0, pos=0, syms=12), idx, set())
        self.assertEqual((v, act), ("WATCH", "none"))

    def test_mover_ok(self):
        idx = {"SW": {"vec_func": ["simulate_one"], "vec_stub": False, "vec_mod": False, "live": True}}
        v, act, _ = classify_switch("SW", _cen(zero=50, nonzero=5, pos=2, syms=12), idx, set())
        self.assertEqual((v, act), ("OK", "none"))


if __name__ == "__main__":
    unittest.main()

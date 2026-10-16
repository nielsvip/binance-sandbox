"""Pair-halt (USER 2026-10-15): 3 trailing net losers since apply halts new
exposure for that (account, sym_side) only. Exits never touched."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v15_sym_halt import (
    trailing_losers,
    compute_halts,
    sym_halt_active,
    NET,
)


def _trades(nets, start_ts=1_760_400_000):
    return [
        {"ts": start_ts + i * 60, "pnl_pct": n, "fees_pct": 0.0} for i, n in enumerate(nets)
    ]


class TestTrailing(unittest.TestCase):
    def test_three_losses(self):
        self.assertEqual(trailing_losers(_trades([1.0, -1.0, -2.0, -0.5])), 3)

    def test_winner_resets(self):
        self.assertEqual(trailing_losers(_trades([-1.0, -2.0, 0.5, -0.5])), 1)

    def test_anchor_excludes_older(self):
        tr = _trades([-1.0, -2.0, -3.0, -4.0])
        self.assertEqual(trailing_losers(tr, since_ts=1_760_400_000 + 2 * 60), 1)

    def test_fees_count(self):
        tr = [{"ts": 1, "pnl_pct": 0.01, "fees_pct": 0.05}]
        self.assertEqual(NET(tr[0]) < 0, True)
        self.assertEqual(trailing_losers(tr), 1)


class TestComputeHalts(unittest.TestCase):
    def test_halt_and_release_on_new_apply(self):
        ledgers = {"ang": {"AAVEUSDC_LONG": _trades([-1.0, -1.0, -1.0])}}
        h1 = compute_halts(ledgers, {"AAVEUSDC_LONG": 1_760_399_000}, {}, {}, n=3, now=1_760_500_000)
        self.assertIn("AAVEUSDC_LONG", h1["pairs"]["ang"])
        h2 = compute_halts(ledgers, {"AAVEUSDC_LONG": 1_760_600_000}, h1["pairs"], {}, n=3, now=1_760_600_001)
        self.assertNotIn("AAVEUSDC_LONG", h2["pairs"].get("ang", {}))

    def test_expiry_reanchors(self):
        ledgers = {"ang": {"X_LONG": _trades([-1.0, -1.0, -1.0])}}
        h1 = compute_halts(ledgers, {"X_LONG": 1_760_399_000}, {}, {}, n=3, now=1_760_400_500)
        self.assertIn("X_LONG", h1["pairs"]["ang"])
        t_exp = 1_760_400_500 + 25 * 3600
        h2 = compute_halts(ledgers, {"X_LONG": 1_760_399_000}, h1["pairs"], {}, n=3, now=t_exp)
        self.assertEqual(h2["reanchored"], [["ang", "X_LONG"]])
        self.assertNotIn("X_LONG", h2["pairs"].get("ang", {}))
        self.assertEqual(h2["pairs"]["_baselines"]["ang"]["X_LONG"], t_exp)
        # baseline persists: same old trades never re-halt; one fresh loss streak re-halts
        h3 = compute_halts(ledgers, {"X_LONG": 1_760_399_000}, h2["pairs"], {}, n=3, now=t_exp + 3600)
        self.assertNotIn("X_LONG", h3["pairs"].get("ang", {}))
        self.assertEqual(h3["reanchored"], [])
        new_ledgers = {"ang": {"X_LONG": _trades([-1.0, -1.0, -1.0], start_ts=t_exp + 7200)}}
        h4 = compute_halts(new_ledgers, {"X_LONG": 1_760_399_000}, h3["pairs"], {}, n=3, now=t_exp + 10800)
        self.assertEqual(h4["pairs"]["ang"]["X_LONG"]["streak"], 3)

    def test_365d_pass_releases(self):
        ledgers = {"ang": {"X_LONG": _trades([-1.0, -1.0, -1.0])}}
        h1 = compute_halts(ledgers, {"X_LONG": 1_760_399_000}, {}, {}, n=3, now=1_760_400_500)
        conf = {"X_LONG": {"confirmed_at": "2026-10-15T01:00:00+00:00", "gain_365d": 2.0}}
        h2 = compute_halts(ledgers, {"X_LONG": 1_760_399_000}, h1["pairs"], conf, n=3, now=1_760_400_600)
        self.assertNotIn("X_LONG", h2["pairs"].get("ang", {}))


class TestPredicate(unittest.TestCase):
    def test_fail_open_missing(self):
        with tempfile.TemporaryDirectory() as d:
            hit, _ = sym_halt_active("ang", "X_LONG", flag_path=os.path.join(d, "no.json"))
            self.assertFalse(hit)

    def test_fail_open_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.json")
            Path(p).write_text("{nope")
            hit, _ = sym_halt_active("ang", "X_LONG", flag_path=p)
            self.assertFalse(hit)

    def test_hit_and_kill_switch(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.json")
            Path(p).write_text(json.dumps({"pairs": {"ang": {"X_LONG": {"streak": 3}}}}))
            hit, why = sym_halt_active("ang", "X_LONG", flag_path=p)
            self.assertTrue(hit)
            self.assertIn("streak 3", why)
            os.environ["V15_SYM_HALT_ENABLED"] = "0"
            try:
                hit2, _ = sym_halt_active("ang", "X_LONG", flag_path=p)
                self.assertFalse(hit2)
            finally:
                del os.environ["V15_SYM_HALT_ENABLED"]

    def test_other_pair_unaffected(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.json")
            Path(p).write_text(json.dumps({"pairs": {"ang": {"X_LONG": {"streak": 3}}}}))
            hit, _ = sym_halt_active("ang", "Y_SHORT", flag_path=p)
            self.assertFalse(hit)
            hit2, _ = sym_halt_active("fin", "X_LONG", flag_path=p)
            self.assertFalse(hit2)


if __name__ == "__main__":
    unittest.main()

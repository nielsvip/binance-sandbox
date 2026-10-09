"""Gain-proportional size tiers (USER 2026-10-09): bigger gain = bigger size."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from v15_persym_size_tiers import _ranked_mult, _venue_of


class RankedMultTest(unittest.TestCase):
    def test_magnitude_proportional(self):
        self.assertEqual(_ranked_mult(20.0, 5.0), 3.0)
        self.assertEqual(_ranked_mult(40.0, 5.0), 3.0)
        self.assertEqual(_ranked_mult(10.0, 5.0), 2.0)
        self.assertEqual(_ranked_mult(2.0, 5.0), 1.2)
        self.assertEqual(_ranked_mult(0.5, 5.0), 1.05)

    def test_neg365_capped_at_one(self):
        self.assertEqual(_ranked_mult(50.0, -3.0), 1.0)
        self.assertEqual(_ranked_mult(50.0, 0.0), 1.0)
        self.assertEqual(_ranked_mult(50.0, None), 3.0)

    def test_bad_inputs_fail_neutral(self):
        self.assertEqual(_ranked_mult("bogus", 5.0), 1.0)
        self.assertEqual(_ranked_mult(10.0, 5.0, gain_cap=0), 1.0)

    def test_venue_split(self):
        self.assertEqual(_venue_of("BTCUSDC_LONG"), "crypto")
        self.assertEqual(_venue_of("AAPL_LONG"), "stocks")


class TiersEndToEndTest(unittest.TestCase):
    def test_rules_on_synthetic_report(self):
        rep = {"sym_sides": {
            "AAA_LONG": {"evidence": {"gain_pct": 30.0, "trades": 50, "tim_pct": 40.0}, "final_365d": {"gain_pct": 12.0}},
            "BBB_SHORT": {"evidence": {"gain_pct": 2.0, "trades": 40, "tim_pct": 30.0}, "final_365d": {"gain_pct": 1.0}},
            "CCC_LONG": {"evidence": {"gain_pct": 25.0, "trades": 60, "tim_pct": 50.0}, "final_365d": {"gain_pct": -4.0}},
            "DDD_SHORT": {"evidence": {"gain_pct": -5.0, "trades": 30, "tim_pct": 20.0}, "final_365d": {"gain_pct": -2.0}},
        }}
        tmp = Path("/tmp/test_size_tiers_rep.json")
        out = Path("/tmp/test_size_tiers_out.json")
        tmp.write_text(json.dumps(rep))
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "v15_persym_size_tiers.py"), "--report", str(tmp), "--out", str(out)],
                           capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        d = json.loads(out.read_text())["tiers"]
        self.assertEqual(d["AAA_LONG"]["mult"], 3.0)
        self.assertEqual(d["AAA_LONG"]["tier"], "RANKED")
        self.assertEqual(d["BBB_SHORT"]["mult"], 1.2)
        self.assertEqual(d["CCC_LONG"]["mult"], 1.0)
        self.assertEqual(d["CCC_LONG"]["tier"], "RANKED_NEG365_CAP1")
        self.assertEqual(d["DDD_SHORT"]["mult"], 0.25)
        self.assertEqual(d["DDD_SHORT"]["tier"], "MIN")


if __name__ == "__main__":
    unittest.main()

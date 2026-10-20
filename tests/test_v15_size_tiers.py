"""Gain-proportional size tiers (USER 2026-10-09): bigger gain = bigger size."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from v15_persym_size_tiers import _min_mult_for_venue, _ranked_mult, _size_ranked, _venue_of


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
        # current rules (2026-10-11/14, tool docstring): max-mult 6.0 / gain-cap 20, 365D-negative or unconfirmed gainers trade small at 0.5
        # (RANKED_NEG365_SMALL), stocks MIN = 0.05. The synthetic sym_sides have no per_sym_store row, so USER 2026-10-09 NOSTORE_MIN demotes every
        # above-min row to the venue minimum and keeps the rule outcome in was_tier - the rule tier is read from there.
        rule = {s: d[s].get("was_tier", d[s]["tier"]) for s in d}
        self.assertEqual(rule["AAA_LONG"], "RANKED")
        self.assertEqual(rule["BBB_SHORT"], "RANKED")
        self.assertEqual(rule["CCC_LONG"], "RANKED_NEG365_SMALL")
        self.assertEqual(rule["DDD_SHORT"], "MIN")
        self.assertEqual(_size_ranked({"g30": 30.0, "g365": 12.0}, 6.0, 20.0, 0.5), (6.0, "RANKED"))
        self.assertEqual(_size_ranked({"g30": 2.0, "g365": 1.0}, 6.0, 20.0, 0.5), (1.5, "RANKED"))
        self.assertEqual(_size_ranked({"g30": 25.0, "g365": -4.0}, 6.0, 20.0, 0.5), (0.5, "RANKED_NEG365_SMALL"))
        self.assertEqual(_size_ranked({"g30": 25.0, "g365": None}, 6.0, 20.0, 0.5), (0.5, "RANKED_NO365_SMALL"))
        stocks_min = _min_mult_for_venue("stocks", 0.25, 0.05)
        self.assertEqual(stocks_min, 0.05)
        for s in ("AAA_LONG", "BBB_SHORT", "CCC_LONG"):
            self.assertEqual(d[s]["tier"], "NOSTORE_MIN")
            self.assertEqual(d[s]["mult"], stocks_min)
        self.assertEqual(d["DDD_SHORT"]["mult"], stocks_min)
        self.assertEqual(d["DDD_SHORT"]["tier"], "MIN")


if __name__ == "__main__":
    unittest.main()

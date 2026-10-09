"""Gain-tier scheduling (USER 2026-10-09): winners-first ranks, loser deferral, launch caps."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from v15_fleet_scheduler import _GAIN_TIER_DEFER_DAYS, _LAUNCH_CAPS, _gain_tiers, _side_deferred, _side_tier, _sym_quota_hit, _sym_tier_rank

NOW = 1791500000.0


def _g(gain, age_days=1.0):
    return {"gain": gain, "mtime": NOW - age_days * 86400.0}


class LaunchCapsTest(unittest.TestCase):
    def test_single_source_of_truth(self):
        self.assertEqual(_LAUNCH_CAPS, {"30D": 3, "365D": 2, "REPAIR": 1, "GS": 1})


class GainTiersTest(unittest.TestCase):
    def test_relative_split_top40_bottom_third(self):
        gains = {f"S{i:02d}_LONG": _g(float(10 - i)) for i in range(10)}
        tiers = _gain_tiers(gains)
        got = sorted(ss for ss, e in tiers.items() if e["tier"] == "W")
        self.assertEqual(got, ["S00_LONG", "S01_LONG", "S02_LONG", "S03_LONG"])
        got_l = sorted(ss for ss, e in tiers.items() if e["tier"] == "L")
        self.assertEqual(got_l, ["S07_LONG", "S08_LONG", "S09_LONG"])
        self.assertEqual(sorted(ss for ss, e in tiers.items() if e["tier"] == "M"), ["S04_LONG", "S05_LONG", "S06_LONG"])

    def test_bad_gain_skipped_empty_ok(self):
        self.assertEqual(_gain_tiers({}), {})
        self.assertEqual(_gain_tiers({"A_LONG": {"gain": "nan-x", "mtime": NOW}}), {})
        self.assertEqual(_gain_tiers(None), {})

    def test_unknown_side_defaults_m(self):
        tiers = _gain_tiers({"A_LONG": _g(5.0)})
        self.assertEqual(_side_tier("ZZZ_SHORT", tiers), "M")

    def test_sym_rank_uses_best_side(self):
        tiers = _gain_tiers({"A_LONG": _g(50.0), "A_SHORT": _g(-90.0), "B_LONG": _g(1.0), "B_SHORT": _g(0.5)})
        self.assertEqual(_sym_tier_rank("A", tiers), 0)
        self.assertEqual(_sym_tier_rank("ZZZ", tiers), 1)


class DeferralTest(unittest.TestCase):
    def _tiers(self):
        gains = {f"S{i:02d}_LONG": _g(float(10 - i)) for i in range(10)}
        return _gain_tiers(gains)

    def test_young_loser_deferred(self):
        self.assertTrue(_side_deferred("S09_LONG", self._tiers(), NOW))

    def test_stale_loser_readmitted(self):
        gains = {f"S{i:02d}_LONG": _g(float(10 - i), age_days=8.0) for i in range(10)}
        self.assertFalse(_side_deferred("S09_LONG", _gain_tiers(gains), NOW))

    def test_mid_window_three_days(self):
        tiers = self._tiers()
        self.assertTrue(_side_deferred("S05_LONG", tiers, NOW))
        gains = {f"S{i:02d}_LONG": _g(float(10 - i), age_days=4.0) for i in range(10)}
        self.assertFalse(_side_deferred("S05_LONG", _gain_tiers(gains), NOW))

    def test_winners_new_never_deferred(self):
        tiers = self._tiers()
        self.assertFalse(_side_deferred("S00_LONG", tiers, NOW))
        self.assertFalse(_side_deferred("NEW_SHORT", tiers, NOW))
        self.assertFalse(_side_deferred("S09_LONG", {}, NOW))

    def test_bad_mtime_fails_open(self):
        tiers = {"X_LONG": {"tier": "L", "gain": -5.0, "mtime": "bogus"}}
        self.assertFalse(_side_deferred("X_LONG", tiers, NOW))

    def test_defer_windows_mid_weekly_loser(self):
        self.assertEqual(_GAIN_TIER_DEFER_DAYS, {"M": 3.0, "L": 7.0})


class QuotaTest(unittest.TestCase):
    def _tiers(self):
        gains = {f"S{i:02d}_LONG": _g(float(10 - i)) for i in range(10)}
        return _gain_tiers(gains)

    def test_measured_mid_loser_hit_quota(self):
        tiers = self._tiers()
        self.assertTrue(_sym_quota_hit("S05", tiers))
        self.assertTrue(_sym_quota_hit("S09", tiers))

    def test_winner_pair_exempt(self):
        tiers = self._tiers()
        self.assertFalse(_sym_quota_hit("S00", tiers))

    def test_unknown_pair_is_discovery_exempt(self):
        self.assertFalse(_sym_quota_hit("ZZZ", self._tiers()))


if __name__ == "__main__":
    unittest.main()

"""Gain-tier scheduling (USER 2026-10-09): winners-first ranks, loser deferral, launch caps."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from v15_fleet_scheduler import _GAIN_TIER_DEFER_DAYS, _LAUNCH_CAPS, _defer_windows, _gain_tiers, _gs_allowed_for_tier, _place_order, _repair_allowed_for_tier, _side_deferred, _side_tier, _sym_quota_hit, _sym_tier_rank

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

    def test_pools_independent_per_venue_side(self):
        gains = {"AAA_LONG": _g(1.0), "BBB_LONG": _g(2.0), "CCCUSDT_LONG": _g(90.0), "DDDUSDT_LONG": _g(80.0)}
        tiers = _gain_tiers(gains)
        self.assertEqual(tiers["BBB_LONG"]["tier"], "W")
        self.assertEqual(tiers["CCCUSDT_LONG"]["tier"], "W")
        self.assertEqual(tiers["AAA_LONG"]["tier"], "L")
        self.assertEqual(tiers["DDDUSDT_LONG"]["tier"], "L")

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

    def test_defer_windows_env_override(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"V15_TIER_DEFER_M_D": "1", "V15_TIER_DEFER_L_D": "2"}):
            self.assertEqual(_defer_windows(), {"M": 1.0, "L": 2.0})
        with patch.dict(os.environ, {"V15_TIER_DEFER_M_D": "bogus"}, clear=False):
            if "V15_TIER_DEFER_L_D" in os.environ:
                del os.environ["V15_TIER_DEFER_L_D"]
            self.assertEqual(_defer_windows(), {"M": 3.0, "L": 7.0})


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


class PlaceOrderTest(unittest.TestCase):
    def test_winners_before_all_mid_loser(self):
        tiers = {"W1_LONG": {"tier": "W", "gain": 9, "mtime": 1}, "M1_LONG": {"tier": "M", "gain": 5, "mtime": 1}, "L1_LONG": {"tier": "L", "gain": -9, "mtime": 1}, "L1_SHORT": {"tier": "L", "gain": -8, "mtime": 1}}
        got = _place_order(["M1", "L1"], ["L1"], ["W1", "M1"], tiers)
        self.assertEqual([s for s, _ in got], ["W1", "M1", "M1", "L1", "L1"])

    def test_chain_first_within_tier(self):
        tiers = {"A_LONG": {"tier": "W", "gain": 9, "mtime": 1}, "B_LONG": {"tier": "W", "gain": 8, "mtime": 1}}
        got = _place_order(["A"], [], ["B"], tiers)
        self.assertEqual(got, [("A", False), ("B", True)])


class TierChainGatesTest(unittest.TestCase):
    def test_gs_winners_mid_discovery_only(self):
        self.assertTrue(_gs_allowed_for_tier("W", True))
        self.assertTrue(_gs_allowed_for_tier("M", True))
        self.assertFalse(_gs_allowed_for_tier("L", True))
        self.assertTrue(_gs_allowed_for_tier("M", False))

    def test_repair_winners_discovery_only(self):
        self.assertTrue(_repair_allowed_for_tier("W", True))
        self.assertFalse(_repair_allowed_for_tier("M", True))
        self.assertFalse(_repair_allowed_for_tier("L", True))
        self.assertTrue(_repair_allowed_for_tier("M", False))


if __name__ == "__main__":
    unittest.main()

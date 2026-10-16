"""Gain-tier scheduling (USER 2026-10-09): winners-first ranks, loser deferral, launch caps."""

import os
import sys
import unittest
from pathlib import Path

os.environ["V15_RANKING_PRIORITY"] = "0"  # USER 2026-10-14: hermetic — these pins predate ranking priority (covered in test_v15_ranking_priority.py)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from v15_fleet_scheduler import _GAIN_TIER_DEFER_DAYS, _LAUNCH_CAPS, _defer_windows, _fresh_daily_state, _gain_tiers, _gs_allowed_for_tier, _place_order, _quota_blocks, _repair_allowed_for_tier, _side_deferred, _side_tier, _sym_quota_hit, _sym_tier_rank

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
        # USER 2026-10-13: the window is 1 day for every tier, so "young" means measured less than a day ago
        gains = {f"S{i:02d}_LONG": _g(float(10 - i), age_days=0.5) for i in range(10)}
        self.assertTrue(_side_deferred("S09_LONG", _gain_tiers(gains), NOW))

    def test_stale_loser_readmitted(self):
        # USER 2026-10-13: every tier is a 1-day window — a side measured >1d ago is eligible again
        gains = {f"S{i:02d}_LONG": _g(float(10 - i), age_days=0.5) for i in range(10)}
        self.assertTrue(_side_deferred("S09_LONG", _gain_tiers(gains), NOW))
        gains = {f"S{i:02d}_LONG": _g(float(10 - i), age_days=1.5) for i in range(10)}
        self.assertFalse(_side_deferred("S09_LONG", _gain_tiers(gains), NOW))

    def test_mid_window_three_days(self):
        gains0 = {f"S{i:02d}_LONG": _g(float(10 - i), age_days=0.5) for i in range(10)}
        self.assertTrue(_side_deferred("S05_LONG", _gain_tiers(gains0), NOW))  # inside the 1-day window
        gains = {f"S{i:02d}_LONG": _g(float(10 - i), age_days=1.5) for i in range(10)}
        self.assertFalse(_side_deferred("S05_LONG", _gain_tiers(gains), NOW))  # a day later it recalculates

    def test_winners_new_never_deferred(self):
        tiers = self._tiers()
        self.assertFalse(_side_deferred("S00_LONG", tiers, NOW))
        self.assertFalse(_side_deferred("NEW_SHORT", tiers, NOW))
        self.assertFalse(_side_deferred("S09_LONG", {}, NOW))

    def test_bad_mtime_fails_open(self):
        tiers = {"X_LONG": {"tier": "L", "gain": -5.0, "mtime": "bogus"}}
        self.assertFalse(_side_deferred("X_LONG", tiers, NOW))

    def test_defer_windows_mid_weekly_loser(self):
        self.assertEqual(_GAIN_TIER_DEFER_DAYS, {"M": 1.0, "L": 1.0})  # USER 2026-10-13: ALL symbols EVERY day (s1+s2)

    def test_defer_windows_env_override(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"V15_TIER_DEFER_M_D": "1", "V15_TIER_DEFER_L_D": "2"}):
            self.assertEqual(_defer_windows(), {"M": 1.0, "L": 2.0})
        with patch.dict(os.environ, {"V15_TIER_DEFER_M_D": "bogus"}, clear=False):
            if "V15_TIER_DEFER_L_D" in os.environ:
                del os.environ["V15_TIER_DEFER_L_D"]
            self.assertEqual(_defer_windows(), {"M": 1.0, "L": 1.0})


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


class QuotaSiblingTest(unittest.TestCase):
    """USER 2026-10-14: a terminal-failed side never blocks its sibling's fresh board (both directions);
    each side stands on its own evidence, both can be productive at once."""

    def test_failed_short_frees_fresh_long(self):
        tiers = {"X_SHORT": {"tier": "L", "gain": -5, "mtime": 1}}
        chain = {"X": {"LONG": ("need30", 1), "SHORT": ("terminal_failing", None)}}
        self.assertFalse(_quota_blocks("X", tiers, chain))

    def test_failed_long_frees_fresh_short(self):
        tiers = {"X_LONG": {"tier": "L", "gain": -5, "mtime": 1}}
        chain = {"X": {"LONG": ("terminal_failing", None), "SHORT": ("need30", 1)}}
        self.assertFalse(_quota_blocks("X", tiers, chain))

    def test_unfailed_loser_still_blocks(self):
        tiers = {"X_SHORT": {"tier": "L", "gain": -5, "mtime": 1}}
        chain = {"X": {"LONG": ("need30", 1), "SHORT": ("need30", 1)}}
        self.assertTrue(_quota_blocks("X", tiers, chain))

    def test_measured_mediocre_sibling_still_throttled(self):
        tiers = {"X_LONG": {"tier": "M", "gain": 1, "mtime": 1}, "X_SHORT": {"tier": "L", "gain": -5, "mtime": 1}}
        chain = {"X": {"LONG": ("need30", 1), "SHORT": ("terminal_failing", None)}}
        self.assertTrue(_quota_blocks("X", tiers, chain))

    def test_failed_side_ignored_in_hit(self):
        tiers = {"X_SHORT": {"tier": "L", "gain": -5, "mtime": 1}}
        self.assertTrue(_sym_quota_hit("X", tiers))
        self.assertFalse(_sym_quota_hit("X", tiers, {"SHORT"}))
        self.assertTrue(_sym_quota_hit("X", tiers, {"LONG"}))


class PlaceOrderTest(unittest.TestCase):
    def test_winners_before_all_mid_loser(self):
        tiers = {"W1_LONG": {"tier": "W", "gain": 9, "mtime": 1}, "M1_LONG": {"tier": "M", "gain": 5, "mtime": 1}, "L1_LONG": {"tier": "L", "gain": -9, "mtime": 1}, "L1_SHORT": {"tier": "L", "gain": -8, "mtime": 1}}
        got = _place_order(["M1", "L1"], ["L1"], ["W1", "M1"], tiers)
        self.assertEqual([s for s, _ in got], ["W1", "M1", "M1", "L1", "L1"])

    def test_chain_first_within_tier(self):
        tiers = {"A_LONG": {"tier": "W", "gain": 9, "mtime": 1}, "B_LONG": {"tier": "W", "gain": 8, "mtime": 1}}
        got = _place_order(["A"], [], ["B"], tiers)
        self.assertEqual(got, [("A", False), ("B", True)])

    def test_redo_new_before_winners(self):
        tiers = {"W1_LONG": {"tier": "W", "gain": 9, "mtime": 1}, "R1_LONG": {"tier": "L", "gain": -9, "mtime": 1}, "R1_SHORT": {"tier": "L", "gain": -8, "mtime": 1}}
        got = _place_order([], [], ["W1", "R1"], tiers, redo={"R1_LONG"})
        self.assertEqual([s for s, _ in got], ["R1", "W1"])

    def test_no_native_penalty_after_parity_fix(self):
        tiers = {"MSTR_LONG": {"tier": "M", "gain": 0, "mtime": 1}, "MSTR_SHORT": {"tier": "M", "gain": 0, "mtime": 1}, "AAVEUSDC_LONG": {"tier": "M", "gain": 0, "mtime": 1}}
        got = _place_order([], [], ["MSTR", "AAVEUSDC"], tiers)
        self.assertEqual([s for s, _ in got], ["MSTR", "AAVEUSDC"])


class TierChainGatesTest(unittest.TestCase):
    def test_gs_winners_mid_discovery_only(self):
        self.assertTrue(_gs_allowed_for_tier("W", True))
        self.assertTrue(_gs_allowed_for_tier("M", True))
        self.assertFalse(_gs_allowed_for_tier("L", True))
        self.assertTrue(_gs_allowed_for_tier("M", False))

    def test_midnight_keeps_tier_memory(self):
        st = {"date": "20261009", "attempts": {"A": 3}, "gain_seen": {"X_LONG": {"gain": 5.0, "mtime": 1.0}}, "launched_at": {"K": 123.0}}
        out = _fresh_daily_state(st, "20261010")
        self.assertEqual(out["date"], "20261010")
        self.assertEqual(out["attempts"], {})
        self.assertEqual(out["gain_seen"], {"X_LONG": {"gain": 5.0, "mtime": 1.0}})
        self.assertEqual(out["launched_at"], {"K": 123.0})

    def test_same_day_untouched(self):
        st = {"date": "20261010", "attempts": {"A": 3}}
        self.assertIs(_fresh_daily_state(st, "20261010"), st)

    def test_repair_winners_discovery_only(self):
        self.assertTrue(_repair_allowed_for_tier("W", True))
        self.assertFalse(_repair_allowed_for_tier("M", True))
        self.assertFalse(_repair_allowed_for_tier("L", True))
        self.assertTrue(_repair_allowed_for_tier("M", False))


if __name__ == "__main__":
    unittest.main()

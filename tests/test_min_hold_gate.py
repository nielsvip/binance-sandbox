"""MIN_HOLD_BARS must be ONE rule, identical in vec and live.

USER 2026-10-27: "refuse a discretionary CLOSE/REDUCE when age < MIN_HOLD_BARS
<- this has to be IDENTICAL in vec and live and TESTED with 0-1-2-4-8 on EVERY
crypto; I REPEAT SCALP IS PROHIBITED"

These pin the three things that were measured wrong:
  * live turned the bar count into seconds at *180 (3-minute bars) while the
    vector held the same bars on the 15m grid -> 30 min vs 150 min, a 5x split;
  * the vector composed max(MIN_HOLD_BARS, MIN_HOLD_BARS_BEFORE_EXIT), so every
    swept option <= 8 was masked by BEFORE_EXIT=10 and the sweep could only
    report structural zeros;
  * a SCALP reason must never buy an exemption from the hold.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vec_decisions import min_hold_gate as g  # noqa: E402

OPTIONS = (0, 1, 2, 4, 8)


def cfg(min_hold_bars=2, before_exit=0, base_tf="15m", sole=False, scalp=False):
    return type("C", (), {
        "BASE_TF": base_tf,
        "MIN_HOLD_BARS": min_hold_bars,
        "MIN_HOLD_BARS_BEFORE_EXIT": before_exit,
        "MIN_HOLD_SOLE_SWITCH_ENABLED": sole,
        "SCALP_MODE": scalp,
        "SCALP_REDUCE_ENABLED": scalp,
    })


class IdentityTest(unittest.TestCase):
    def test_seconds_and_bars_paths_never_disagree(self):
        """The live (seconds) and vec (bars) entry points are one decision."""
        for opt in OPTIONS:
            c = cfg(opt)
            for held in range(0, 13):
                age = held * g.bar_minutes(c) * 60
                a, _ = g.refuse(age, c, "CLOSE", "WT_CROSS_EXIT_1h")
                b, _ = g.refuse_bars(held, c, "CLOSE", "WT_CROSS_EXIT_1h")
                self.assertEqual(a, b, f"option={opt} held={held}")

    def test_threshold_is_on_the_base_tf_grid_not_180s(self):
        """Regression on the 5x bug: 10 bars of 15m is 150 min, never 30."""
        c = cfg(min_hold_bars=10, base_tf="15m")
        self.assertEqual(g.bar_minutes(c), 15)
        self.assertEqual(g.threshold_seconds(c), 10 * 15 * 60.0)
        self.assertNotEqual(g.threshold_seconds(c), 10 * 180.0)

    def test_bar_minutes_follows_base_tf(self):
        self.assertEqual(g.bar_minutes(cfg(base_tf="3m")), 3)
        self.assertEqual(g.bar_minutes(cfg(base_tf="1h")), 60)
        self.assertEqual(g.bar_minutes(cfg(base_tf="nonsense")), g.DEFAULT_BAR_MINUTES)


class CompositionTest(unittest.TestCase):
    def test_before_exit_masks_every_swept_option(self):
        """Why the ordered sweep reports structural zeros as shipped."""
        for opt in OPTIONS:
            self.assertEqual(g.threshold_bars(cfg(opt, before_exit=10)), 10)

    def test_sole_switch_makes_each_option_bind(self):
        for opt in OPTIONS:
            self.assertEqual(g.threshold_bars(cfg(opt, before_exit=10, sole=True)), opt)

    def test_zero_disables_the_gate(self):
        c = cfg(0, before_exit=0)
        refused, detail = g.refuse(0, c, "CLOSE", "WT_CROSS_EXIT_1h")
        self.assertFalse(refused)
        self.assertEqual(detail, "MIN_HOLD_OFF")


class ExemptionTest(unittest.TestCase):
    def test_price_protection_is_never_held(self):
        c = cfg(8)
        for reason in ("FIXED_STOP_HIT", "HARD_STOP_LOSS", "STOP_LOSS_TRIGGERED",
                       "LIQUIDATION_PROTECT", "EMERGENCY_MARGIN_KILL",
                       "VIGILANCE_DC4_EXIT", "BALANCE_FLOOR_FORCE_REDUCE"):
            refused, _ = g.refuse(0, c, "CLOSE", reason)
            self.assertFalse(refused, f"{reason} must never be held")

    def test_board_dictates_and_bookkeeping_pass(self):
        c = cfg(8)
        for reason in ("VEC_DRIVEN_CLOSE_RECONCILE_", "BOARD_CONVERGE_CLOSE",
                       "VEC_FWD_MISSING_FLAT fwd=x", "WT_CROSS_EXIT |VEC_EXACT",
                       "BROKER_SYNC_FLAT", "NON_TRADEABLE_EXIT", "OVERSIZE_TRIM_REDUCE"):
            refused, _ = g.refuse(0, c, "CLOSE", reason)
            self.assertFalse(refused, f"{reason} must pass")

    def test_entries_are_untouched(self):
        c = cfg(8)
        for action in ("OPEN", "AUGMENT", "REENTRY", "PLACE_STOP", "CANCEL_STOP"):
            refused, detail = g.refuse(0, c, action, "ANY_REASON")
            self.assertFalse(refused)
            self.assertEqual(detail, "NOT_AN_EXIT")

    def test_unknown_age_never_strands_an_exit(self):
        c = cfg(8)
        for age in (None, "", -1, "bogus"):
            refused, _ = g.refuse(age, c, "CLOSE", "WT_CROSS_EXIT_1h")
            self.assertFalse(refused)


class ScalpProhibitedTest(unittest.TestCase):
    def test_scalp_reason_is_not_an_exemption(self):
        """SCALP IS PROHIBITED: a scalp label must still be held."""
        c = cfg(2)
        for reason in ("SCALP_REDUCE_NOW", "SCALP_EXIT_FAST", "QUICK_SCALP_CLOSE"):
            refused, _ = g.refuse(0, c, "REDUCE", reason)
            self.assertTrue(refused, f"{reason} must be refused inside the hold")

    def test_scalp_tokens_absent_from_every_exempt_set(self):
        """Guards against a future edit quietly re-opening sub-hold churn."""
        for token in g.SCALP_TOKENS:
            self.assertNotIn(token, g.STOP_TOKENS)
            self.assertNotIn(token, g.BOOKKEEPING_TOKENS)
            self.assertNotIn(token, g.BOARD_TOKENS)

    def test_prohibition_is_asserted_not_assumed(self):
        ok, _ = g.is_scalp_prohibited(cfg(2, scalp=False))
        self.assertTrue(ok)
        bad, detail = g.is_scalp_prohibited(cfg(2, scalp=True))
        self.assertFalse(bad)
        self.assertIn("SCALP PROHIBITED", detail)

    def test_shipped_configs_have_scalp_off(self):
        import config
        import config_tradier
        for mod in (config.Config, config_tradier.TradierConfig):
            ok, detail = g.is_scalp_prohibited(mod)
            self.assertTrue(ok, detail)


class VecLiveWiringTest(unittest.TestCase):
    def test_both_engines_resolve_the_same_threshold(self):
        """config.Config (live) and QuickConfig (vec) must agree exactly."""
        import config
        import v12_quick_engine as V
        self.assertEqual(
            g.threshold_seconds(config.Config),
            g.threshold_seconds(V.QuickConfig()),
        )

    def test_vector_sources_min_hold_from_this_module(self):
        """The engine must not compose its own min hold any more."""
        src = (ROOT / "v12_quick_engine.py").read_text()
        self.assertIn("_min_hold_gate.threshold_bars(cfg)", src)
        self.assertNotIn('min_hold = max(cfg.MIN_HOLD_BARS, getattr(cfg, "MIN_HOLD_BARS_BEFORE_EXIT", 0))', src)

    def test_live_no_longer_multiplies_bars_by_180(self):
        src = (ROOT / "ez_manage.py").read_text()
        self.assertIn("_mhg.threshold_seconds(", src)
        self.assertNotIn("_min_hold_sec_for_exit = _min_hold_bars_for_exit * 180.0", src)

    def test_sweep_options_are_the_ordered_five(self):
        self.assertEqual(tuple(g.SWEEP_OPTIONS), OPTIONS)


if __name__ == "__main__":
    unittest.main()

"""Best-not-newest publish + NO_TRADES skip + BEST_EFFORT verdicts (USER 2026-10-03).

Regression cover for the empty-rows/flat-gains diagnosis: publish keeps the
best set (never newest-wins), dead sheets skip the fill, near-misses publish
flagged instead of quarantining. Fast, stdlib-only + pilot import.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import v15_pilot as PILOT
import tools.v15_fleet_scheduler as SCH


def _mkfile(d: Path, name: str, cls: str | None = None, trades: int = 66):
    (d / name).write_text("dummy-xlsx")
    if cls is not None:
        man = {"publish_class": cls, "metrics": {"trades": trades, "gain_pct": 0.0}}
        (d / name.replace(".xlsx", "_manifest.json")).write_text(json.dumps(man))


class NearMissTest(unittest.TestCase):
    def test_tim_miss_is_nearmiss(self):
        ok, r = PILOT._nearmiss_30d({"valid": True, "trades": 40, "tim_pct": 12.0, "gain_pct": 8.0}, 5.0)
        self.assertTrue(ok)
        self.assertTrue(any("TIM" in x for x in r))

    def test_bh_miss_is_nearmiss(self):
        ok, r = PILOT._nearmiss_30d({"valid": True, "trades": 40, "tim_pct": 30.0, "gain_pct": 8.0}, 26.0)
        self.assertTrue(ok)
        self.assertTrue(any("BH" in x for x in r))

    def test_qualified_is_not_nearmiss(self):
        ok, _ = PILOT._nearmiss_30d({"valid": True, "trades": 40, "tim_pct": 30.0, "gain_pct": 8.0}, 5.0)
        self.assertFalse(ok)

    def test_invalid_is_not_nearmiss(self):
        ok, _ = PILOT._nearmiss_30d({"valid": False, "invalid_reason": "DD 79.9% >30% (vomit)", "trades": 40, "tim_pct": 30.0, "gain_pct": 8.0}, 5.0)
        self.assertFalse(ok)

    def test_negative_gain_is_not_nearmiss(self):
        ok, _ = PILOT._nearmiss_30d({"valid": True, "trades": 40, "tim_pct": 30.0, "gain_pct": -2.0}, 5.0)
        self.assertFalse(ok)

    def test_zero_trades_is_not_nearmiss(self):
        ok, _ = PILOT._nearmiss_30d({"valid": False, "trades": 0, "tim_pct": 0.0, "gain_pct": 0.0}, -27.0)
        self.assertFalse(ok)


class StampStaleTest(unittest.TestCase):
    def test_newer_npz_is_fresh(self):
        self.assertFalse(PILOT._stamp_is_stale(100, 200))

    def test_same_npz_is_stale(self):
        self.assertTrue(PILOT._stamp_is_stale(200, 200))

    def test_missing_stamp_fails_open(self):
        self.assertFalse(PILOT._stamp_is_stale(None, 200))

    def test_missing_npz_fails_open(self):
        self.assertFalse(PILOT._stamp_is_stale(200, None))


class RetryableHolesTest(unittest.TestCase):
    def test_missing_yellows_retryable(self):
        done = {"SHEET!3:X=1": {"delta": None, "missing_yellows": ["H1"], "reason": ""}}
        self.assertTrue(PILOT._board_has_retryable_holes(done))

    def test_settled_nulls_not_retryable(self):
        done = {"SHEET!3:X=1": {"delta": None, "missing_yellows": [], "reason": "ZERO_TRADES"}, "SHEET!4:Y=2": {"delta": 1.5, "missing_yellows": [], "reason": ""}}
        self.assertFalse(PILOT._board_has_retryable_holes(done))

    def test_empty_board_not_retryable(self):
        self.assertFalse(PILOT._board_has_retryable_holes({}))


class BestPublishTest(unittest.TestCase):
    def test_no_actives_wins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            win, best, _, _ = PILOT._rank_final_gain(d, "MOG_SHORT", "MOG_SHORT_bh0p00_gain1p_t10_30d_matrix.xlsx", 1.32)
            self.assertTrue(win)
            self.assertIsNone(best)

    def test_beats_worse_active(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _mkfile(d, "MOG_SHORT_bhm9p75_gainm0p13_30d_matrix.xlsx")
            win, best, gain, _ = PILOT._rank_final_gain(d, "MOG_SHORT", "MOG_SHORT_bhm9p75_gain1p_t10_30d_matrix.xlsx", 1.32)
            self.assertTrue(win)
            self.assertIsNone(best)

    def test_loses_to_better_active(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _mkfile(d, "MOG_SHORT_bhm9p75_gain6p56_30d_matrix.xlsx")
            win, best, gain, _ = PILOT._rank_final_gain(d, "MOG_SHORT", "MOG_SHORT_bhm9p75_gain1p_t10_30d_matrix.xlsx", 1.32)
            self.assertFalse(win)
            self.assertEqual(best, "MOG_SHORT_bhm9p75_gain6p56_30d_matrix.xlsx")
            self.assertAlmostEqual(gain, 6.56)

    def test_tie_keeps_existing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _mkfile(d, "MOG_SHORT_bhm9p75_gain1p32_30d_matrix.xlsx")
            win, best, _, _ = PILOT._rank_final_gain(d, "MOG_SHORT", "MOG_SHORT_bhm9p75_gain1p_t9_30d_matrix.xlsx", 1.32)
            self.assertFalse(win)
            self.assertEqual(best, "MOG_SHORT_bhm9p75_gain1p32_30d_matrix.xlsx")

    def test_qualified_beats_flagged_regardless_of_gain(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _mkfile(d, "MOG_SHORT_bhm9p75_gain1p32_30d_matrix.xlsx", cls="QUALIFIED")
            win, best, _, _ = PILOT._rank_final_gain(d, "MOG_SHORT", "MOG_SHORT_bhm9p75_gain80p_t10_30d_matrix.xlsx", 80.0, cand_class="BEST_EFFORT")
            self.assertFalse(win)
            self.assertEqual(best, "MOG_SHORT_bhm9p75_gain1p32_30d_matrix.xlsx")

    def test_flagged_beats_flagged_on_gain(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _mkfile(d, "MOG_SHORT_bhm9p75_gain1p32_30d_matrix.xlsx", cls="BEST_EFFORT")
            win, best, _, _ = PILOT._rank_final_gain(d, "MOG_SHORT", "MOG_SHORT_bhm9p75_gain2p_t10_30d_matrix.xlsx", 2.0, cand_class="BEST_EFFORT")
            self.assertTrue(win)
            self.assertIsNone(best)

    def test_apply_collapses_multi_active(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _mkfile(d, "AR_LONG_bh89p39_gain47p39_30d_matrix.xlsx")
            _mkfile(d, "AR_LONG_bh89p39_gain80p79_30d_matrix.xlsx")
            src = d / "working.xlsx"
            import openpyxl as _oxl
            _wb = _oxl.Workbook()
            for _i in range(4):
                _ws = _wb.create_sheet(f"S{_i}")
                _ws["A1"] = "v"
                _ws["B2"] = 1.5
            _wb.save(src)
            active, won = PILOT._apply_best_publish(d, "AR_LONG", "AR_LONG_bh89p39_gain55p_t10_30d_matrix.xlsx", 55.0, "QUALIFIED", src)
            self.assertFalse(won)
            self.assertEqual(active, "AR_LONG_bh89p39_gain80p79_30d_matrix.xlsx")
            self.assertTrue((d / "AR_LONG_bh89p39_gain47p39_30d_matrix.xlsx.superseded").exists())
            self.assertTrue((d / "AR_LONG_bh89p39_gain55p_t10_30d_matrix.xlsx.superseded").exists())
            self.assertTrue((d / "AR_LONG_bh89p39_gain80p79_30d_matrix.xlsx").exists())


class ParseBoolTest(unittest.TestCase):
    def test_zero_strings_are_false(self):
        self.assertIs(PILOT._parse_opt_value("0", False), False)
        self.assertIs(PILOT._parse_opt_value("0.0", False), False)

    def test_one_strings_are_true(self):
        self.assertIs(PILOT._parse_opt_value("1", False), True)
        self.assertIs(PILOT._parse_opt_value("1.0", False), True)

    def test_garbage_stays_for_gate(self):
        self.assertEqual(PILOT._parse_opt_value("2.0", False), "2.0")
        self.assertEqual(PILOT._parse_opt_value("0.5", False), "0.5")

    def test_gate_rejects_garbage_for_bool(self):
        ok, why = PILOT._cand_compatible("WT_EXHAUST_EXIT_REQUIRE_GAIN", "2.0", {"WT_EXHAUST_EXIT_REQUIRE_GAIN": False})
        self.assertFalse(ok)
        self.assertIn("TYPE_MISMATCH", why)

    def test_gate_uses_config_type_without_template_default(self):
        ok, _ = PILOT._cand_compatible("WT_EXHAUST_EXIT_REQUIRE_GAIN", "2.0", {})
        self.assertFalse(ok)
        ok2, _ = PILOT._cand_compatible("WT_EXHAUST_EXIT_REQUIRE_GAIN", True, {})
        self.assertTrue(ok2)

    def test_gate_passes_true_false(self):
        self.assertTrue(PILOT._cand_compatible("WT_EXHAUST_EXIT_REQUIRE_GAIN", True, {"WT_EXHAUST_EXIT_REQUIRE_GAIN": False})[0])
        self.assertTrue(PILOT._cand_compatible("WT_EXHAUST_EXIT_REQUIRE_GAIN", False, {"WT_EXHAUST_EXIT_REQUIRE_GAIN": False})[0])


class SamplingDefaultTest(unittest.TestCase):
    def test_default_off_without_env_or_flag(self):
        import os
        old = os.environ.get("V15_POSSYM_SAMPLING")
        try:
            os.environ.pop("V15_POSSYM_SAMPLING", None)
            self.assertFalse(PILOT._possym_enabled("run25"))
        finally:
            if old is not None:
                os.environ["V15_POSSYM_SAMPLING"] = old

    def test_env_forces_on_and_off(self):
        import os
        old = os.environ.get("V15_POSSYM_SAMPLING")
        try:
            os.environ["V15_POSSYM_SAMPLING"] = "1"
            self.assertTrue(PILOT._possym_enabled("run25"))
            os.environ["V15_POSSYM_SAMPLING"] = "0"
            self.assertFalse(PILOT._possym_enabled("run25"))
        finally:
            if old is not None:
                os.environ["V15_POSSYM_SAMPLING"] = old
            else:
                os.environ.pop("V15_POSSYM_SAMPLING", None)


class BoardCalcNTest(unittest.TestCase):
    def test_excludes_identity_counts_real(self):
        done = {
            "A": {"delta": 0.0, "reason": "RUNNING_IDENTITY: x"},
            "B": {"delta": 0.0, "reason": ""},
            "C": {"delta": 1.5, "reason": ""},
            "D": {"delta": None, "reason": "ZERO_TRADES"},
        }
        self.assertEqual(PILOT._board_calc_n(done), 2)


class SchedulerSoftTerminalTest(unittest.TestCase):
    def test_soft_verdicts_match_terminal_regex(self):
        for v in ("IMPOSSIBLE", "NO_TRADES", "BEST_EFFORT"):
            self.assertTrue(SCH._VERDICT_TERMINAL_RE.search('{"verdict": "%s"}' % v), v)
            self.assertTrue(SCH._quar_terminal("S_LONG", {"S_LONG"}, True)[0].startswith("terminal"))

    def test_no_verdict_not_terminal(self):
        self.assertFalse(SCH._VERDICT_TERMINAL_RE.search('{"verdict": null, "final_gain": 7.09}'))
        self.assertFalse(SCH._VERDICT_TERMINAL_RE.search('{"final_gain": 7.09}'))


if __name__ == "__main__":
    unittest.main()

"""Obligate-calc guard contract (s5 experiment). Fast, stdlib-only."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v15_obligate import code_stamp, delta_vs_obligate, far_value, is_error_reason, stamp_mismatch


def old_delta_vs(res, cum_before):
    """Pre-fix pilot _delta_vs logic (v15_pilot.py): error-zeros were computable."""
    if not res or res.get("gain_pct") is None:
        return None, False, str((res or {}).get("invalid_reason") or "no result")[:40]
    if int(res.get("trades") or 0) == 0:
        return None, False, "ZERO_TRADES"
    d = float(res.get("gain_pct")) - cum_before
    d = 0.0 if abs(d) < 1e-9 else d
    if not res.get("valid"):
        return d, False, str(res.get("invalid_reason") or "invalid")[:40]
    return d, True, ""


class TestErrorZeroRefused(unittest.TestCase):
    def test_future_err_with_stale_gain_refused(self):
        res = {"gain_pct": 3.0, "trades": 12, "valid": True}
        d, ok, why = delta_vs_obligate(res, "TIMEOUT 10s", 3.0)
        self.assertIsNone(d)
        self.assertFalse(ok)

    def test_eval_layer_error_shapes_refused(self):
        for reason in ("ERR boom", "prepare failed (no npz)", "v12_pilot stall timeout 8s — woke to next cell", "Traceback (most recent call)", "KILLED oom", "no result"):
            res = {"gain_pct": 0.0, "trades": 5, "valid": False, "invalid_reason": reason}
            d, ok, _ = delta_vs_obligate(res, "", 1.5)
            self.assertIsNone(d, reason)
            self.assertFalse(ok, reason)

    def test_old_logic_accepted_what_obligate_refuses(self):
        res = {"gain_pct": 0.0, "trades": 5, "valid": False, "invalid_reason": "ERR boom"}
        d_old, _, _ = old_delta_vs(res, 1.5)
        self.assertIsNotNone(d_old)
        d_new, _, _ = delta_vs_obligate(res, "", 1.5)
        self.assertIsNone(d_new)


class TestHonestPathsPreserved(unittest.TestCase):
    def test_exact_zero_passes(self):
        d, ok, why = delta_vs_obligate({"gain_pct": 4.25, "trades": 30, "valid": True}, "", 4.25)
        self.assertEqual(d, 0.0)
        self.assertTrue(ok)

    def test_epsilon_zero_passes(self):
        d, ok, _ = delta_vs_obligate({"gain_pct": 4.25 + 5e-10, "trades": 30, "valid": True}, "", 4.25)
        self.assertEqual(d, 0.0)

    def test_positive_and_negative_pass(self):
        d, ok, _ = delta_vs_obligate({"gain_pct": 6.0, "trades": 30, "valid": True}, "", 4.25)
        self.assertAlmostEqual(d, 1.75)
        self.assertTrue(ok)
        d, ok, _ = delta_vs_obligate({"gain_pct": 1.0, "trades": 30, "valid": True}, "", 4.25)
        self.assertAlmostEqual(d, -3.25)
        self.assertTrue(ok)

    def test_invalid_real_value_kept_grey(self):
        d, ok, why = delta_vs_obligate({"gain_pct": 5.0, "trades": 12, "valid": False, "invalid_reason": "TIM 85.2 > 80"}, "", 3.0)
        self.assertAlmostEqual(d, 2.0)
        self.assertFalse(ok)
        self.assertIn("TIM", why)

    def test_none_and_zero_trades(self):
        d, _, _ = delta_vs_obligate(None, "", 1.0)
        self.assertIsNone(d)
        d, _, why = delta_vs_obligate({"gain_pct": 0.0, "trades": 0, "valid": False}, "", 1.0)
        self.assertIsNone(d)
        self.assertEqual(why, "ZERO_TRADES")

    def test_stats_bumped(self):
        st = {}
        delta_vs_obligate({"gain_pct": 1.0, "trades": 5, "valid": True}, "", 1.0, stats=st)
        delta_vs_obligate({"gain_pct": 0.0, "trades": 5, "valid": False, "invalid_reason": "ERR x"}, "", 1.0, stats=st)
        self.assertEqual(st.get("proven_zero"), 1)
        self.assertEqual(st.get("error_refused"), 1)


class TestStamp(unittest.TestCase):
    def test_mismatch_lists_drifted_keys(self):
        a = {"pilot_md5": "aa", "engine_md5": "bb", "eval_md5": "cc", "template_md5": "dd", "defaults_round": "r1"}
        b = dict(a)
        self.assertEqual(stamp_mismatch(a, b), [])
        b["engine_md5"] = "zz"
        self.assertEqual(stamp_mismatch(a, b), ["engine_md5"])
        self.assertEqual(stamp_mismatch(None, b), sorted(b.keys()))

    def test_code_stamp_shape(self):
        s = code_stamp(ROOT, ROOT / "v15_pilot.py", None, "r1")
        self.assertEqual(s["template_md5"], "no-template")
        self.assertEqual(len(s["pilot_md5"]), 32)


class TestFarValue(unittest.TestCase):
    def test_derivations(self):
        self.assertIs(far_value("X", True, True), False)
        self.assertEqual(far_value("X", 5, 5), 51)
        self.assertAlmostEqual(far_value("X", 0.5, 0.5), 5.5)
        self.assertEqual(far_value("X", "15m", "15m"), "1h")
        self.assertEqual(far_value("X", "OFF", "OFF"), "ON")
        self.assertIs(far_value("X", "true", True), False)
        self.assertIs(far_value("X", None, True), True)
        self.assertIsNone(far_value("X", {"a": 1}, {}))


if __name__ == "__main__":
    unittest.main()

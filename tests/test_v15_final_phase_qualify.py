"""Strict qualify: QUALIFIED requires parity PASS (PAR/001 2026-10-04 user-ordered fix)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v15_final_phase import qualify

W30 = {"gain_pct": 5.0, "trades": 66, "valid": True, "tim_pct": 40.0, "max_dd_pct": 8.0}
W365 = {"gain_pct": 12.0, "trades": 790, "valid": True, "tim_pct": 60.0, "max_dd_pct": 15.0}
OV = {"A": 1}
CONF = {"BTCDOMUSDT_LONG": {"valid": True, "gain_365d": 9.0, "trades": 200}}
PAR = {"BTCDOMUSDT_LONG": {"status": "PASS", "reason": "parity ok", "live_gain": "5.1", "live_trades": "60"}}
V = {"both_ok": True, "unverifiable": False, "attempt": 0, "final_progress": "/x", "overrides": OV, "span_365_days": 365.0, "w30": W30, "w365": W365}
CS = "BTCDOMUSDT_LONG"


class TestQualifyStrict(unittest.TestCase):
    def q(self, ss, vd, par, conf):
        return qualify(ss, vd, par, conf)[0]

    def test_stock_qualified(self):
        par = {"CRWD_LONG": {"status": "PASS", "reason": "parity ok", "live_gain": "4.4", "live_trades": "55"}}
        self.assertEqual(self.q("CRWD_LONG", V, par, {}), "QUALIFIED")

    def test_crypto_qualified(self):
        self.assertEqual(self.q(CS, V, PAR, CONF), "QUALIFIED")

    def test_no_verdict(self):
        self.assertEqual(self.q(CS, None, PAR, CONF), "UNVERIFIED")

    def test_unverifiable(self):
        self.assertEqual(self.q(CS, dict(V, unverifiable=True), PAR, CONF), "UNVERIFIED")

    def test_not_both_ok(self):
        self.assertEqual(self.q(CS, dict(V, both_ok=False), PAR, CONF), "NEGATIVE")

    def test_no_overrides(self):
        self.assertEqual(self.q(CS, dict(V, overrides={}), PAR, CONF), "UNVERIFIED")

    def test_bad_30d(self):
        self.assertEqual(self.q(CS, dict(V, w30=dict(W30, gain_pct=-1.0)), PAR, CONF), "NEGATIVE")

    def test_span_short(self):
        self.assertEqual(self.q(CS, dict(V, span_365_days=100.0), PAR, CONF), "NEGATIVE")

    def test_crypto_no_confirm(self):
        self.assertEqual(self.q(CS, V, PAR, {}), "UNVERIFIED")

    def test_parity_neg_blocks(self):
        par = {CS: {"status": "FAIL", "reason": "x", "live_gain": "-3.2"}}
        self.assertEqual(self.q(CS, V, par, CONF), "NEGATIVE")

    def test_parity_unavailable_blocks(self):
        par = {CS: {"status": "UNAVAILABLE", "reason": "no PARITY line"}}
        self.assertEqual(self.q(CS, V, par, CONF), "UNVERIFIED")

    def test_parity_missing_blocks(self):
        self.assertEqual(self.q(CS, V, {}, CONF), "UNVERIFIED")

    def test_parity_fail_positive_blocks(self):
        par = {CS: {"status": "FAIL", "reason": "ratio 2.86 out of range", "live_gain": "2.1"}}
        self.assertEqual(self.q(CS, V, par, CONF), "NEGATIVE")

    def test_parity_running_blocks(self):
        par = {CS: {"status": "RUNNING", "reason": "running"}}
        self.assertEqual(self.q(CS, V, par, CONF), "UNVERIFIED")

    def test_parity_fail_no_gain_blocks(self):
        par = {CS: {"status": "FAIL", "reason": "live invalid: trades 29 < 30"}}
        self.assertEqual(self.q(CS, V, par, CONF), "NEGATIVE")


if __name__ == "__main__":
    unittest.main()

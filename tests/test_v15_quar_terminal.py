"""Scheduler quarantine-terminal rule (attempt-burn stall: quarantined syms relaunched forever). Fast, stdlib-only."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.v15_fleet_scheduler as SCH


class QuarTerminalTest(unittest.TestCase):
    def test_rule_no_chain(self):
        self.assertEqual(SCH._quar_terminal("ZENUSDT_SHORT", {"ZENUSDT_SHORT"}, True), ("terminal_ok", None))

    def test_rule_chain_mode(self):
        self.assertEqual(SCH._quar_terminal("ZENUSDT_SHORT", {"ZENUSDT_SHORT"}, False), ("terminal_failing", None))

    def test_rule_not_quarantined(self):
        self.assertIsNone(SCH._quar_terminal("NOC_LONG", {"ZENUSDT_SHORT"}, True))
        self.assertIsNone(SCH._quar_terminal("NOC_LONG", set(), True))

    def test_verdict_regex(self):
        pat = SCH._VERDICT_TERMINAL_RE
        self.assertTrue(pat.search('{"verdict": "IMPOSSIBLE", "final_gain": null}'))
        self.assertTrue(pat.search('{"verdict":"IMPOSSIBLE"}'))
        self.assertTrue(pat.search('{"verdict": "NO_TRADES"}'))
        self.assertTrue(pat.search('{"verdict":"BEST_EFFORT"}'))
        self.assertFalse(pat.search('{"verdict": null, "final_gain": 7.09}'))
        self.assertFalse(pat.search('{"done": {}}'))

    def test_done_regex_still_excludes_quarantine(self):
        pat = re.compile(r'"final_gain": [-0-9]')
        self.assertFalse(pat.search('{"verdict": "IMPOSSIBLE", "final_gain": null}'))
        self.assertTrue(pat.search('{"final_gain": 7.09}'))
        self.assertTrue(pat.search('{"final_gain": -2.5}'))

    def test_pair_gate_fresh_pair(self):
        cs = {"AAA": {"LONG": ("need30", 1), "SHORT": ("need30", 1)}}
        acts = [{"side": "LONG"}, {"side": "SHORT"}]
        self.assertTrue(SCH._pair_gate_ok("AAA", acts, {}, cs, {}))

    def test_pair_gate_half_terminal_fresh(self):
        cs = {"AAA": {"LONG": ("terminal_failing", None), "SHORT": ("need30", 1)}}
        self.assertTrue(SCH._pair_gate_ok("AAA", [{"side": "SHORT"}], {}, cs, {}))
        self.assertFalse(SCH._pair_gate_ok("AAA", [], {}, cs, {}))

    def test_pair_gate_capped_side_excluded(self):
        cs = {"AAA": {"LONG": ("need30", 1), "SHORT": ("need30", 1)}}
        self.assertTrue(SCH._pair_gate_ok("AAA", [{"side": "SHORT"}], {}, cs, {"AAA_LONG|30D": 3}))
        self.assertFalse(SCH._pair_gate_ok("AAA", [{"side": "SHORT"}], {}, cs, {}))

    def test_pair_gate_owned_always_passes(self):
        cs = {"AAA": {"LONG": ("need30", 1), "SHORT": ("need30", 1)}}
        self.assertTrue(SCH._pair_gate_ok("AAA", [{"side": "SHORT"}], {"AAA": "s1"}, cs, {}))

    def test_pair_gate_both_terminal_blocks(self):
        cs = {"AAA": {"LONG": ("terminal_ok", None), "SHORT": ("terminal_failing", None)}}
        self.assertFalse(SCH._pair_gate_ok("AAA", [], {}, cs, {}))

    def test_shipped_probe_has_detection(self):
        self.assertIn('"quarantined": []', SCH.HOST_PY)
        self.assertIn('IMPOSSIBLE', SCH.HOST_PY)
        self.assertIn('NO_TRADES', SCH.HOST_PY)
        self.assertIn('BEST_EFFORT', SCH.HOST_PY)
        self.assertIn('_VERDICT_TERMINAL_RE.search(_txt)', SCH.HOST_PY)
        compile(SCH.HOST_PY, "HOST_PY", "exec")


if __name__ == "__main__":
    unittest.main()

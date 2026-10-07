"""Pack runner: launch/poll/summarize with fakes (no sleeps)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.parity_pack import run_pack


class TestPack(unittest.TestCase):
    def test_all_terminal_first_poll(self):
        launched = []
        par = {"A_LONG": {"status": "PASS"}, "B_SHORT": {"status": "FAIL"}, "C_LONG": {"status": "UNAVAILABLE"}}
        with patch("tools.parity_pack.time.sleep", return_value=None):
            s = run_pack(["A_LONG", "B_SHORT", "C_LONG"], "/p", "/f",
                         lambda ss, fd, pr: launched.append(ss),
                         lambda fd: {"parity": par})
        self.assertEqual(launched, ["A_LONG", "B_SHORT", "C_LONG"])
        self.assertEqual(s["PASS"], ["A_LONG"])
        self.assertEqual(s["FAIL"], ["B_SHORT"])
        self.assertEqual(s["UNAVAILABLE"], ["C_LONG"])

    def test_running_then_pass(self):
        states = [{"X_LONG": {"status": "RUNNING"}}, {"X_LONG": {"status": "PASS"}}]
        with patch("tools.parity_pack.time.sleep", return_value=None):
            s = run_pack(["X_LONG"], "/p", "/f", lambda ss, fd, pr: None, lambda fd: {"parity": states.pop(0) if len(states) > 1 else states[0]})
        self.assertEqual(s["PASS"], ["X_LONG"])

    def test_launch_error(self):
        def launch(ss, fd, pr):
            raise RuntimeError("boom")

        with patch("tools.parity_pack.time.sleep", return_value=None):
            s = run_pack(["Z_LONG"], "/p", "/f", launch, lambda fd: {"parity": {}})
        self.assertEqual(s["ERROR"], {"Z_LONG": "boom"})

    def test_launch_arg_order_matches_host_parity(self):
        import inspect
        from tools import v15_final_phase as FP
        params = list(inspect.signature(FP.host_parity).parameters)
        self.assertEqual(params, ["ss", "final_dir", "progress"])
        calls = []
        with patch("tools.parity_pack.time.sleep", return_value=None):
            run_pack(["A_LONG"], "/p", "/f", lambda ss, fd, pr: calls.append((ss, fd, pr)), lambda fd: {"parity": {"A_LONG": {"status": "PASS"}}})
        self.assertEqual(calls, [("A_LONG", "/f", "/p/A_LONG_v14_progress.json")])

    def test_all_error_returns_without_polling(self):
        def launch(ss, fd, pr):
            raise RuntimeError("boom")

        polls = []
        with patch("tools.parity_pack.time.sleep", return_value=None):
            s = run_pack(["Z_LONG"], "/p", "/f", launch, lambda fd: polls.append(fd) or {"parity": {}})
        self.assertEqual(s["ERROR"], {"Z_LONG": "boom"})
        self.assertEqual(polls, [])


if __name__ == "__main__":
    unittest.main()

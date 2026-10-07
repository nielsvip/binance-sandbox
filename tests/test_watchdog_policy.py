"""USER 2026-10-06 watchdog policy: exit-reason capture, failure backoff, churn alert,
pressure gate, parent attribution. Dry-runs run_with_watchdog.sh against dummy
children in /tmp (WATCHDOG_TEST_* hooks) — never touches live logs or procs."""
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WD = str(ROOT / "run_with_watchdog.sh")


def _run_dummy(child_src, wait_s):
    tmp = Path(tempfile.mkdtemp(prefix="wdtest_"))
    logdir = tmp / "logs"
    logdir.mkdir()
    (tmp / "wdtest_dummy.py").write_text(child_src)
    env = dict(os.environ, WATCHDOG_TEST_LOGDIR=str(logdir), WATCHDOG_TEST_WORKDIR=str(tmp))
    p = subprocess.Popen(["bash", WD, "wdtest_dummy.py"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        time.sleep(wait_s)
    finally:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        p.wait(timeout=10)
    logs = " ".join(f.read_text() for f in logdir.glob("*watchdog.log"))
    return logs


class TestWatchdogPolicy(unittest.TestCase):
    def test_exit_reason_captured(self):
        logs = _run_dummy("import sys\nprint('FATAL-BOOM-42', file=sys.stderr)\nraise SystemExit(1)\n", 12)
        self.assertIn("code: 1", logs)
        self.assertIn("[EXIT_REASON]", logs)
        self.assertIn("FATAL-BOOM-42", logs)
        self.assertIn("Parent: pid=", logs)

    def test_failure_backoff_grows(self):
        logs = _run_dummy("raise SystemExit(1)\n", 30)
        self.assertIn("[RESTART_BACKOFF] consecutive failure #1 (code 1) — sleeping 5s", logs)
        self.assertIn("[RESTART_BACKOFF] consecutive failure #2 (code 1) — sleeping 10s", logs)

    def test_clean_exit_no_backoff(self):
        logs = _run_dummy("raise SystemExit(0)\n", 14)
        self.assertIn("Restarting in 5 seconds", logs)
        self.assertNotIn("[RESTART_BACKOFF]", logs)

    def test_policy_markers_present(self):
        src = (ROOT / "run_with_watchdog.sh").read_text()
        for marker in ("PRESSURE_GATE", ".restart_alert_", "EXIT_REASON", "RESTART_BACKOFF"):
            self.assertIn(marker, src)


if __name__ == "__main__":
    unittest.main()

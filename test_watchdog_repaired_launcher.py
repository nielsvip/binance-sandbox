#!/usr/bin/env python3
"""Linux regression for the repaired exact-matrix watchdog launcher.

Run directly on S1 (no pytest dependency required):
    python3 tests/test_watchdog_repaired_launcher.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parent
WATCHDOG = ROOT / "watchdog_lab_matrix.sh"


@unittest.skipUnless(
    sys.platform.startswith("linux") and shutil.which("setsid") and Path("/proc").exists(),
    "setsid/process-parent integration is Linux-only",
)
class RepairedLauncherIntegrationTest(unittest.TestCase):
    def test_workers_detach_and_fingerprint_exit_is_relaunched_next_cycle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="matrix-watchdog-") as raw:
            tmp = Path(raw)
            sbx = tmp / "sandbox"
            tools = sbx / "tools"
            logs = tmp / "logs"
            fake_bin = tmp / "bin"
            tools.mkdir(parents=True)
            logs.mkdir()
            fake_bin.mkdir()
            (sbx / "data").mkdir()
            (sbx / "data" / "MATRIX_REPAIRED_ENABLE").touch()
            (sbx / "data" / "matrix_worker_manifest.json").write_text(
                json.dumps(
                    {
                        "campaign": "stocks_repaired_20260730_c5",
                        "matrix_contract_version": (
                            "tradier-matrix-exec-c5-20260730"
                        ),
                        "no_live_promotion": True,
                        "workers": [
                            {
                                "tag": tag,
                                "symbol": symbol,
                                "side": side,
                                "expected_contract_fingerprint": f"c5:{tag}",
                                "priority_roots": ["EXIT_ROOT_ENABLED"],
                            }
                            for tag, symbol, side in (
                                ("wm_exit", "MU", "LONG"),
                                ("wv_exit", "VT", "LONG"),
                                ("wt_exit", "TTD", "SHORT"),
                                ("wa_exit", "ACN", "SHORT"),
                            )
                        ],
                    }
                ),
                encoding="utf-8",
            )
            reports = sbx / "data" / "reports"
            reports.mkdir()
            (reports / "VECTOR_OVERLAY_UNIQUENESS_AUDIT.json").write_text(
                json.dumps(
                    {
                        "contract": (
                            "STRICT_PER_KEY_UNIQUE_METRIC_AND_ACTION_NO_DUPLICATES"
                        ),
                        "current_engine_campaign": (
                            "stocks_repaired_20260730_c5"
                        ),
                        "matrix_numeric_fill_requires_pass": True,
                        "input_rows": 4,
                        "passed_numeric_overlays": 4,
                        "data_unavailable_rows": 0,
                        "quarantined_numeric_overlays": 0,
                        "metric_collision_groups": 0,
                        "fingerprint_collision_groups": 0,
                    }
                ),
                encoding="utf-8",
            )
            shutil.copy2(ROOT / "tools" / "matrix_resume_gate.py", tools)
            events = tmp / "worker-events.jsonl"

            worker = tools / "param_matrix_daemon.py"
            worker.write_text(
                """#!/usr/bin/env python3
import json, os, sys, time
with open(os.environ["FAKE_MATRIX_EVENTS"], "a", encoding="utf-8") as f:
    f.write(json.dumps({
        "argv": sys.argv[1:],
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "sid": os.getsid(0),
        "exit_reason": "contract_fingerprint_changed",
    }) + "\\n")
    f.flush()
time.sleep(float(os.environ.get("FAKE_MATRIX_LIFETIME", "0.4")))
raise SystemExit(42)
""",
                encoding="utf-8",
            )
            for report in ("export_switch_matrix_xls.py", "switch_matrix_digest.py"):
                (tools / report).write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            # Keep this integration test invisible to the real repaired fleet if it is run on
            # S1: the watchdog's pgrep sees only PIDs recorded by this private fake worker.
            pgrep = fake_bin / "pgrep"
            pgrep.write_text(
                """#!/usr/bin/env python3
import json, os, pathlib, re, sys
pattern = sys.argv[-1]
event_path = pathlib.Path(os.environ["FAKE_MATRIX_EVENTS"])
events = [] if not event_path.exists() else [
    json.loads(line) for line in event_path.read_text().splitlines() if line
]
for event in events:
    cmdline = pathlib.Path(f"/proc/{event['pid']}/cmdline")
    if not cmdline.exists():
        continue
    try:
        command = cmdline.read_bytes().replace(b"\\0", b" ").decode(errors="replace")
    except OSError:
        continue
    if re.search(pattern, command):
        print(event["pid"])
        raise SystemExit(0)
raise SystemExit(1)
""",
                encoding="utf-8",
            )
            pgrep.chmod(0o755)

            env = {
                **os.environ,
                "MATRIX_SBX": str(sbx),
                "MATRIX_PY": sys.executable,
                "MATRIX_LOGDIR": str(logs),
                "FAKE_MATRIX_EVENTS": str(events),
                "FAKE_MATRIX_LIFETIME": "0.4",
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
            }

            started = time.monotonic()
            first = subprocess.run(
                ["bash", str(WATCHDOG)],
                env=env,
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertLess(
                time.monotonic() - started,
                2.0,
                "watchdog waited for a repaired worker instead of independently detaching it",
            )

            first_events = self._wait_for_events(events, 4)
            self.assertEqual(
                self._tags(first_events),
                {"wm_exit", "wv_exit", "wt_exit", "wa_exit"},
            )
            for event in first_events:
                self.assertEqual(event["ppid"], 1, event)
                self.assertEqual(event["sid"], event["pid"], event)
                self.assertEqual(event["exit_reason"], "contract_fingerprint_changed")

            self._wait_until_dead(first_events)
            second = subprocess.run(
                ["bash", str(WATCHDOG)],
                env=env,
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            all_events = self._wait_for_events(events, 8)
            self.assertEqual(len(all_events), 8)
            self.assertEqual(
                self._tags(all_events[4:]),
                {"wm_exit", "wv_exit", "wt_exit", "wa_exit"},
            )
            self.assertTrue(
                set(event["pid"] for event in first_events).isdisjoint(
                    event["pid"] for event in all_events[4:]
                )
            )
            self._wait_until_dead(all_events[4:])

    @staticmethod
    def _read_events(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def _wait_for_events(self, path: Path, count: int) -> list[dict]:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            events = self._read_events(path)
            if len(events) >= count:
                return events
            time.sleep(0.02)
        self.fail(f"expected {count} worker starts, got {len(self._read_events(path))}")

    def _wait_until_dead(self, events: list[dict]) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if all(not Path(f"/proc/{event['pid']}").exists() for event in events):
                return
            time.sleep(0.02)
        alive = [event["pid"] for event in events if Path(f"/proc/{event['pid']}").exists()]
        self.fail(f"fake fingerprint-exit workers did not terminate: {alive}")

    @staticmethod
    def _tags(events: list[dict]) -> set[str]:
        tags = set()
        for event in events:
            argv = event["argv"]
            tags.add(argv[argv.index("--tag") + 1])
        return tags


if __name__ == "__main__":
    unittest.main()

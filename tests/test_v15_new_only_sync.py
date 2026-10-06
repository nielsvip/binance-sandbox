"""New-only S1->Mac sync: ancient files must never re-sync (USER 2026-10-06).

Every S1->Mac SPREADSHEETS bulk pull must (1) carry -u/--update so a Mac file
newer than S1's copy is never overwritten by the older S1 copy, and (2) carry
a recency gate (remote find -mmin feeding rsync --files-from) so ancient files
missing on the Mac (quarantined/deleted) are never resurrected and presented
as new arrivals. Tiny state anchors (lifecycle_pilot JSON) are update-only and
exempt from the recency gate: resume needs old anchors, and they are KB, not
the GB xlsx/html flood.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PULL_SCRIPTS = (
    "tools/sync_s1_to_mac.sh",
    "tools/mac_pull.sh",
    "tools/mac_pull_sheets_charts.sh",
    "tools/monitor_mega_sweep.sh",
)

REMOTE_SRC = re.compile(r":~/|:binance-sandbox|:/home/|\$S1:|\$MEGA:|\$s1host:|\$host:|\$SRC")
UPDATE_FLAG = re.compile(r"--update\b|(?<!-)-(?!-)[A-Za-z]*u")


def _active_rsync_lines(path):
    lines = []
    for raw in Path(path).read_text().splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "rsync" not in s:
            continue
        lines.append(s)
    return lines


def _pull_lines(rel):
    return [s for s in _active_rsync_lines(ROOT / rel) if REMOTE_SRC.search(s)]


class TestNewOnlySync(unittest.TestCase):
    def test_pulls_found(self):
        for rel in PULL_SCRIPTS:
            self.assertGreaterEqual(len(_pull_lines(rel)), 3, f"{rel}: no pull lines found, test is vacuous")

    def test_pulls_never_overwrite_newer(self):
        for rel in PULL_SCRIPTS:
            for line in _pull_lines(rel):
                self.assertIsNotNone(UPDATE_FLAG.search(line), f"{rel}: pull without -u/--update can overwrite a newer Mac file: {line[:160]}")

    def test_bulk_pulls_gated_to_new_only(self):
        for rel in PULL_SCRIPTS:
            for line in _pull_lines(rel):
                if "lifecycle_pilot" in line:
                    continue
                self.assertIn("--files-from", line, f"{rel}: bulk pull without recency gate resurrects ancient files: {line[:160]}")

    def test_recency_gate_defined(self):
        for rel in PULL_SCRIPTS:
            text = (ROOT / rel).read_text()
            self.assertIn("V15_SYNC_MAX_AGE_MIN", text, f"{rel} missing V15_SYNC_MAX_AGE_MIN")
            self.assertIn("-mmin", text, f"{rel} missing remote find -mmin recency list")
            self.assertIn("--files-from", text, f"{rel} missing --files-from")


if __name__ == "__main__":
    unittest.main()

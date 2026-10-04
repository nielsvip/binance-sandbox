#!/usr/bin/env python3
"""Regression test: resurrected result files (old bytes, new date) are caught.

Provenance: 2026-10-04 HYPEUSDT_SHORT — Oct-2 sheet bytes re-placed on the
Mac with a fresh mtime, masquerading as the latest calculation.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GUARD = ROOT / "tools" / "v15_resurrect_guard.py"


def make_xlsx(path: Path, internal_ts: float):
    iso = datetime.fromtimestamp(internal_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = (f'<?xml version="1.0"?><cp:coreProperties xmlns:cp="x" xmlns:dcterms="y">'
            f"<dcterms:modified>{iso}</dcterms:modified></cp:coreProperties>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("docProps/core.xml", core)
        z.writestr("xl/workbook.xml", "<workbook/>")


def run_guard(*args):
    r = subprocess.run([sys.executable, str(GUARD), *args], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-500:]
    return r.stdout


class TestResurrectGuard(unittest.TestCase):
    def test_quarantine_old_bytes_new_date(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as t:
            live = Path(t) / "live"
            q = Path(t) / "q"
            live.mkdir()
            fresh = live / "AAA_LONG_bh1p00_gain2p_t10_30d_matrix.xlsx"
            make_xlsx(fresh, now - 60)
            os.utime(fresh, (now, now))
            fake = live / "BBB_SHORT_bhm1p00_gain3p_t11_30d_matrix.xlsx"
            make_xlsx(fake, now - 50 * 3600)
            os.utime(fake, (now, now))
            out = run_guard("--dirs", str(live), "--quarantine-dir", str(q),
                            "--max-gap-hours", "24", "--skip-json", "--audit")
            self.assertIn("WOULD_QUARANTINE", out)
            self.assertIn("BBB_SHORT", out)
            self.assertTrue(fake.exists(), "audit must not move")
            out = run_guard("--dirs", str(live), "--quarantine-dir", str(q),
                            "--max-gap-hours", "24", "--skip-json")
            self.assertIn("QUARANTINED", out)
            self.assertFalse(fake.exists(), "fake must leave the live dir")
            self.assertTrue(fresh.exists(), "fresh file must stay")
            moved = list(q.glob("BBB_SHORT*.RESURRECTED_*.xlsx"))
            self.assertEqual(len(moved), 1, "bytes preserved in quarantine")
            log = (q / "quarantine.log").read_text()
            self.assertIn("QUARANTINED", log)

    def test_skips_tmp_and_garbage(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as t:
            live = Path(t) / "live"
            q = Path(t) / "q"
            live.mkdir()
            tmp = live / "CCC_LONG_30d_matrix.fz.tmp.xlsx"
            tmp.write_bytes(b"partial-write")
            os.utime(tmp, (now, now))
            out = run_guard("--dirs", str(live), "--quarantine-dir", str(q),
                            "--max-gap-hours", "24", "--skip-json")
            self.assertNotIn("QUARANTINED", out)
            self.assertTrue(tmp.exists())

    def test_json_mtouch_logged(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as t:
            jd = Path(t) / "j"
            jd.mkdir()
            reg = Path(t) / "seen.json"
            q = Path(t) / "q"
            p = jd / "DDD_LONG_v14_progress.json"
            p.write_text(json.dumps({"done": {"a": 1}}))
            out = run_guard("--dirs", str(Path(t) / "empty"), "--quarantine-dir", str(q),
                            "--json-dirs", str(jd), "--registry", str(reg))
            self.assertNotIn("MTOUCH", out)
            os.utime(p, (now + 7200, now + 7200))
            out = run_guard("--dirs", str(Path(t) / "empty"), "--quarantine-dir", str(q),
                            "--json-dirs", str(jd), "--registry", str(reg))
            self.assertIn("MTOUCH", out)
            self.assertTrue(p.exists(), "json is log-only, never moved")


if __name__ == "__main__":
    unittest.main()

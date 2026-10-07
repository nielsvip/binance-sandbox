"""v15_switch_add — the template one-script rule's single exception (USER 2026-10-06).

Proves refusal paths + a real add on throwaway copies (never live templates).
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = str(ROOT / "tools" / "v15_switch_add.py")


def run(*args):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, cwd=str(ROOT))


class RefusalTest(unittest.TestCase):
    def test_unknown_tab(self):
        r = run("--switch", "X", "--tab", "NOPE", "--candidates", "a,b", "--default", "a")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown tab", r.stdout + r.stderr)

    def test_single_candidate(self):
        r = run("--switch", "X", "--tab", "GLOBAL_RISK_GATES", "--candidates", "a", "--default", "a")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("at least 2", r.stdout + r.stderr)

    def test_unwired_switch(self):
        r = run("--switch", "DEFINITELY_NOT_A_SWITCH_XYZ", "--tab", "GLOBAL_RISK_GATES", "--candidates", "True,False", "--default", "True")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a field of config.py", r.stdout + r.stderr)


class AddOnCopiesTest(unittest.TestCase):
    def test_wired_switch_adds_rows_then_dup_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            shutil.copy(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx", Path(td) / "TEMPLATE_CRYPTO_LONG.xlsx")
            base = ["--switch", "CHANNEL_REENTRY_STOP_TF", "--tab", "REENTRY_ADAPTIVE", "--candidates", "1h,OFF,15m", "--default", "1h", "--venues", "crypto", "--sides", "long", "--templates-dir", td]
            dry = run(*base)
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertIn("insert 3 rows", dry.stdout)
            wet = run(*base, "--apply")
            self.assertEqual(wet.returncode, 0, wet.stdout + wet.stderr)
            self.assertIn("installed 3 rows", wet.stdout)
            import openpyxl
            wb = openpyxl.load_workbook(str(Path(td) / "TEMPLATE_CRYPTO_LONG.xlsx"), read_only=True)
            ws = wb["REENTRY_ADAPTIVE"]
            hit = [(ws.cell(r, 2).value, ws.cell(r, 2).font.bold) for r in range(3, ws.max_row + 1) if ws.cell(r, 1).value == "CHANNEL_REENTRY_STOP_TF"]
            wb.close()
            self.assertEqual(hit, [("1h", True), ("OFF", False), ("15m", False)])
            dup = run(*base)
            self.assertNotEqual(dup.returncode, 0)
            self.assertIn("already has rows", dup.stdout + dup.stderr)


if __name__ == "__main__":
    unittest.main()

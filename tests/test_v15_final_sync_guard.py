"""Final-sync guard: bh/gain finals are immutable in sync (USER 2026-10-05).

A finished matrix (old cents OR new int+trades format) is pulled/pushed ONCE
and never again — same-name + new-mtime recycling hid real progress. Its
same-stem chart + manifest travel with it, also immutable. Live in-progress
sheets keep updating.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v15_final_sync_guard import FINAL_GLOBS, audit, chart_name_for_xlsx, is_final, is_final_chart, is_final_manifest, is_final_xlsx, manifest_name_for_xlsx, missing_charts

OLD_FINAL = "1MBABYDOGEUSDT_SHORT_bhm14p94_gain0p39_30d_matrix.xlsx"
NEW_FINAL = "FANG_LONG_bh1p58_gain6p_t66_30d_matrix.xlsx"
LIVE_SHEET = "MU_LONG_30d_matrix.xlsx"


class TestClassifier(unittest.TestCase):
    def test_old_and_new_finals(self):
        self.assertTrue(is_final_xlsx(OLD_FINAL))
        self.assertTrue(is_final_xlsx(NEW_FINAL))
        self.assertTrue(is_final(OLD_FINAL))
        self.assertTrue(is_final(NEW_FINAL))

    def test_live_sheet_not_final(self):
        self.assertFalse(is_final_xlsx(LIVE_SHEET))
        self.assertFalse(is_final(LIVE_SHEET))

    def test_history_files_not_final(self):
        for bad in ("FANG_LONG_bh1p58_gain6p_t66_30d_matrix.xlsx.superseded", "MU_LONG_30d_matrix_20261005.xlsx", "MU_LONG_pilot_30d_matrix.xlsx", "random.xlsx", ""):
            self.assertFalse(is_final(bad), bad)

    def test_chart_sidecar(self):
        self.assertTrue(is_final_chart(chart_name_for_xlsx(NEW_FINAL)))
        self.assertTrue(is_final(chart_name_for_xlsx(OLD_FINAL)))
        self.assertFalse(is_final_chart("MU_LONG_30d_matrix.html"))
        self.assertFalse(is_final("MU_LONG_COMPLETE_chart.html"))

    def test_manifest_sidecar(self):
        self.assertTrue(is_final_manifest(manifest_name_for_xlsx(NEW_FINAL)))
        self.assertTrue(is_final(manifest_name_for_xlsx(OLD_FINAL)))
        self.assertFalse(is_final_manifest("MU_LONG_30d_matrix_manifest.json"))
        self.assertFalse(is_final_manifest("something_manifest.json"))


class TestAudit(unittest.TestCase):
    def test_missing_charts_and_counts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / OLD_FINAL).write_text("x")
            (d / NEW_FINAL).write_text("x")
            (d / chart_name_for_xlsx(NEW_FINAL)).write_text("<html>")
            (d / manifest_name_for_xlsx(NEW_FINAL)).write_text("{}")
            (d / LIVE_SHEET).write_text("live")
            self.assertEqual(missing_charts(d), [OLD_FINAL])
            a = audit(d)
            self.assertEqual((a["finals"], a["charts"], a["manifests"], a["missing_charts"]), (2, 1, 1, 1))


def _rsync_lines(path):
    lines = []
    for raw in Path(path).read_text().splitlines():
        s = raw.strip()
        if s.startswith("#") or "rsync" not in s:
            continue
        lines.append(s)
    return lines


class TestShellGuard(unittest.TestCase):
    PULL_PUSH = ("tools/sync_s1_to_mac.sh", "tools/mac_pull.sh", "tools/npz_sync.sh", "tools/s1_pull_from_s2s3s5.sh")

    def test_cell_rsync_lines_guarded(self):
        for rel in self.PULL_PUSH:
            for line in _rsync_lines(ROOT / rel):
                if "V15_V16_CELL_BY_CELL" not in line:
                    continue
                guarded = "--ignore-existing" in line or "--exclude='*_bh*_gain*" in line or '--exclude="*_bh*_gain*' in line
                self.assertTrue(guarded, f"{rel}: unguarded CELL rsync: {line[:160]}")

    def test_final_globs_present_in_shell(self):
        for rel in self.PULL_PUSH:
            text = (ROOT / rel).read_text()
            for g in FINAL_GLOBS:
                self.assertIn(g, text, f"{rel} missing pattern {g}")

    def test_pulls_carry_charts(self):
        for rel in ("tools/sync_s1_to_mac.sh", "tools/mac_pull.sh", "tools/s1_pull_from_s2s3s5.sh"):
            text = (ROOT / rel).read_text()
            self.assertIn("--include='*_bh*_gain*_30d_matrix.html'", text, f"{rel} must pull final charts")


class TestHerdGuard(unittest.TestCase):
    def test_push_to_s1_finals_once(self):
        text = (ROOT / "tools/v15_local_herd.py").read_text()
        seg = text.split("def push_to_s1")[1].split("\ndef ")[0]
        self.assertIn("--ignore-existing", seg)
        for g in FINAL_GLOBS:
            self.assertIn(g, seg, f"push_to_s1 missing pattern {g}")

    def test_periodic_sync_finals_once(self):
        text = (ROOT / "tools/v15_local_herd.py").read_text()
        self.assertIn("periodic rsync", text)
        seg = text.split("periodic push to S1")[1].split("if args.once")[0]
        self.assertIn("--ignore-existing", seg)
        self.assertIn("--exclude='*_bh*_gain*_30d_matrix.xlsx'", seg)


if __name__ == "__main__":
    unittest.main()

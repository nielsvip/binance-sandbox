"""BIBLE §96 — the ONE typed-value validator (tools/switch_types.py) and its fail-closed writers.

USER 2026-10-13: "'0.0' on a true/false switch — that is TERRIBLE but an easy fix, just make sure it can never happen again."
Unit cases run on a throwaway fixture root (tiny config.py / config_tradier.py / v12_quick_engine.py), never live templates.
"""
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import switch_types as ST  # noqa: E402

CONFIG = '''
from dataclasses import dataclass, field
@dataclass
class Config:
    FOO_ENABLED: bool = False
    BAR_COUNT: int = 4
    BAZ_THR: float = 0.75
    QUX_TF: str = "OFF"
    MODE_WORD: str = "AND"
    HTF_GATE_D_MANDATORY: bool = False
    THROTTLE: dict = field(default_factory=lambda: {"gateway": 200})
    SMA_FIELD: str = "ema_50"
'''
TRADIER = CONFIG.replace("class Config", "class TradierConfig").replace("HTF_GATE_D_MANDATORY: bool = False", "HTF_GATE_D_MANDATORY: float = 0.0")
QUICK = '''
from dataclasses import dataclass, field
@dataclass
class QuickConfig:
    FOO_ENABLED: float = 0.0
    BAR_COUNT: float = 4
    BAZ_THR: float = 0.75
    QUX_TF: str = "OFF"
    MODE_WORD: str = "AND"
    HTF_GATE_D_MANDATORY: float = 0.0
    THROTTLE: float = 0.0
    SMA_FIELD: str = "ema_50"
    FILTER_EXEMPTIONS: dict = field(default_factory=dict)
    QUICK_ONLY_RATE: float = 0.0
'''
CSD = '''
def defaults(cs):
    return {"FOO_ENABLED": False, "BAR_COUNT": 4, "BAZ_THR": 0.75, "QUX_TF": "OFF", "MODE_WORD": "AND", "SMA_FIELD": "ema_50"}
'''


def make_root(td):
    r = Path(td)
    (r / "config.py").write_text(CONFIG)
    (r / "config_tradier.py").write_text(TRADIER)
    (r / "v12_quick_engine.py").write_text(QUICK)
    (r / "cat_side_defaults.py").write_text(CSD)
    (r / "backups").mkdir()
    (r / "data").mkdir()
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = make_root(self._td.name)
        ST._CACHE.pop(str(self.root), None)

    def tearDown(self):
        self._td.cleanup()

    def c(self, f, v, venue="CRYPTO", **kw):
        return ST.coerce(f, v, venue, root=self.root, **kw)


class BoolCases(Base):
    def test_regression_0p0_string_on_bool(self):
        ok, v, _ = self.c("FOO_ENABLED", "0.0")
        self.assertTrue(ok)
        self.assertIs(v, False)
        self.assertFalse(ST.canonical("FOO_ENABLED", "0.0", "CRYPTO", root=self.root), "'0.0' text on a bool must be flagged non-canonical")

    def test_unambiguous_bool_coercions(self):
        for raw, want in (("0", False), (0, False), (0.0, False), ("1", True), ("1.0", True), (1, True), ("True", True), ("False", False), (True, True)):
            ok, v, _ = self.c("FOO_ENABLED", raw)
            self.assertTrue(ok, raw)
            self.assertIs(v, want, raw)

    def test_ambiguous_bool_refused(self):
        for raw in ("2.0", "0.5", 2, 30, 1.125, "OFF", "0.0_ALT", "", "maybe"):
            ok, _, why = self.c("FOO_ENABLED", raw)
            self.assertFalse(ok, raw)
            self.assertIn("FOO_ENABLED", why)

    def test_bool01_cross_venue(self):
        self.assertEqual(self.c("HTF_GATE_D_MANDATORY", "1.0", "STOCKS")[:2], (True, 1.0))
        self.assertEqual(self.c("HTF_GATE_D_MANDATORY", "1.0", "CRYPTO")[:2], (True, True))
        self.assertFalse(self.c("HTF_GATE_D_MANDATORY", "0.5", "STOCKS")[0], "0.5 on a bool-encoded float is ambiguous")


class NumericCases(Base):
    def test_int(self):
        self.assertEqual(self.c("BAR_COUNT", "2.0")[:2], (True, 2))
        self.assertEqual(self.c("BAR_COUNT", 3)[:2], (True, 3))
        self.assertFalse(self.c("BAR_COUNT", "0.5")[0], "non-integral on int must never be rounded")
        self.assertFalse(self.c("BAR_COUNT", False)[0])
        self.assertEqual(self.c("BAR_COUNT", 0.5, allow_quick_float=True)[:2], (True, 0.5), "registrar float-kept path (QuickConfig float)")

    def test_float(self):
        self.assertEqual(self.c("BAZ_THR", "0.30")[:2], (True, 0.3))
        self.assertEqual(self.c("BAZ_THR", 25)[:2], (True, 25.0))
        for raw in (False, True, "abc", "nan"):
            self.assertFalse(self.c("BAZ_THR", raw)[0], raw)

    def test_to_cell_keeps_integer_text(self):
        self.assertEqual(ST.to_cell("BAZ_THR", "25", "CRYPTO", root=self.root)[:2], (True, 25))
        self.assertEqual(ST.to_cell("BAZ_THR", "0.30", "CRYPTO", root=self.root)[:2], (True, 0.3))


class TextCases(Base):
    def test_tf(self):
        self.assertEqual(self.c("QUX_TF", False)[:2], (True, "OFF"))
        self.assertEqual(self.c("QUX_TF", "4h")[:2], (True, "4h"))
        self.assertEqual(self.c("QUX_TF", "1h,4h,D")[:2], (True, "1h,4h,D"))
        self.assertEqual(self.c("QUX_TF", "BOTH")[:2], (True, "BOTH"))
        self.assertEqual(self.c("QUX_TF", "D=D")[:2], (True, "D"), "'X=X' dup typo collapses")
        for raw in (True, 20, 0.1, "AND", "K=V"):
            self.assertFalse(self.c("QUX_TF", raw)[0], raw)

    def test_enum(self):
        self.assertTrue(self.c("SMA_FIELD", "sma_200")[0])
        for raw in ("0.5", "1.0", 0, "True", False):
            self.assertFalse(self.c("SMA_FIELD", raw)[0], raw)
        self.assertEqual(ST.field_spec("MODE_WORD", "CRYPTO", root=self.root)["kind"], "str", "'OFF'-default without _TF name is an enum")

    def test_dict_and_grey(self):
        self.assertEqual(self.c("FILTER_EXEMPTIONS", '{"entry": "A, B, A"}')[:2], (True, {"entry": ["A", "B"]}))
        self.assertFalse(self.c("FILTER_EXEMPTIONS", 1)[0])
        self.assertEqual(ST.field_spec("THROTTLE", "CRYPTO", root=self.root)["kind"], "grey")
        self.assertTrue(self.c("THROTTLE", "0.0")[0], "grey field: QuickConfig default is the only valid value")
        self.assertFalse(self.c("THROTTLE", "0.5")[0])

    def test_unknown(self):
        self.assertTrue(self.c("NOT_A_FIELD", "x")[0])
        self.assertFalse(self.c("NOT_A_FIELD", "x", strict_unknown=True)[0])
        self.assertEqual(self.c("QUICK_ONLY_RATE", "0.5")[:2], (True, 0.5))


class FixTypesWriter(Base):
    def _wb(self, path, rows):
        import openpyxl
        from openpyxl.styles import Font
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "GLOBAL_RISK_GATES"
        ws.cell(1, 1).value = "title"
        hdr = ["Switch", "default", "override", "Family", "BASELINE", "HUSTLE_DELTA", "VECTOR_DELTA", "LIVE_DELTA", "LIVE_SHARPE", "REAL_COMPLETE", "PER_ROW_FILTERS", "is_default"]
        for c, h in enumerate(hdr, 1):
            ws.cell(2, c).value = h
        for i, (sw, v, yes) in enumerate(rows, start=3):
            ws.cell(i, 1).value = sw
            ws.cell(i, 2).value = v
            ws.cell(i, 2).font = Font(bold=yes)
            ws.cell(i, 12).value = "YES" if yes else "NO"
        for i in range(10):
            wb.create_sheet(f"PAD{i}")
        wb.save(str(path))

    def test_fix_types_coerces_drops_realigns(self):
        import openpyxl
        import v15_switch_add as A
        td = self.root / "tpl"
        td.mkdir()
        p = td / "TEMPLATE_CRYPTO_LONG.xlsx"
        self._wb(p, [("FOO_ENABLED", True, True), ("FOO_ENABLED", "0.0", False), ("FOO_ENABLED", "1.0", False), ("FOO_ENABLED", "2.0", False),
                     ("BAR_COUNT", "D", True), ("BAR_COUNT", "0.5", False), ("BAR_COUNT", "2.0", False), ("QUX_TF", "OFF", True), ("QUX_TF", False, False), ("QUX_TF", "4h", False)])
        dry = A.fix_types([str(td)], apply=False, root=self.root, ts="T0")
        self.assertEqual(dry["files"][str(p)]["unresolved"], [])
        self.assertEqual(openpyxl.load_workbook(str(p))["GLOBAL_RISK_GATES"].cell(4, 2).value, "0.0", "dry-run never writes")
        A.fix_types([str(td)], apply=True, root=self.root, ts="T1")
        ws = openpyxl.load_workbook(str(p))["GLOBAL_RISK_GATES"]
        got = [(ws.cell(r, 1).value, ws.cell(r, 2).value, ws.cell(r, 12).value) for r in range(3, ws.max_row + 1) if ws.cell(r, 1).value]
        self.assertEqual(got, [("FOO_ENABLED", True, "YES"), ("FOO_ENABLED", False, "NO"), ("BAR_COUNT", 4, "YES"), ("BAR_COUNT", 2, "NO"), ("QUX_TF", "OFF", "YES"), ("QUX_TF", "4h", "NO")])
        self.assertTrue((self.root / "backups" / "before_fix_types_T1_tpl_TEMPLATE_CRYPTO_LONG.xlsx").exists())
        self.assertEqual(ST.scan_workbook(p, root=self.root), [], "a repaired template scans clean")


class WriterRefusals(Base):
    def test_switch_add_refuses_mistyped_candidate(self):
        import v15_switch_add as A
        (self.root / "data" / "SWITCH_BIBLE.json").write_text(json.dumps({"switches": {"BAR_COUNT": {"status": "WIRED", "vec_reads": ["x"], "live_read_count": {"ez": 1}}, "SMA_FIELD": {"status": "WIRED", "vec_reads": ["x"], "live_read_count": {"ez": 1}}}}))
        with self.assertRaises(A.Refused) as e:
            A.validate_switch("BAR_COUNT", "GLOBAL_RISK_GATES", ["4", "0.5"], "4", ["CRYPTO"], ["LONG"], root=self.root)
        self.assertIn("integral", str(e.exception))
        with self.assertRaises(A.Refused):
            A.validate_switch("SMA_FIELD", "GLOBAL_RISK_GATES", ["ema_50", "0.5"], "ema_50", ["CRYPTO"], ["LONG"], root=self.root)
        opts, dflt, _, _ = A.validate_switch("BAR_COUNT", "GLOBAL_RISK_GATES", ["4", "2"], "4", ["CRYPTO"], ["LONG"], root=self.root)
        self.assertEqual((opts, dflt), ([4, 2], 4))

    def test_intake_refuses_mistyped_entry(self):
        import v15_template_row_intake as I
        q = {"entries": []}
        add, ign = I.merge_entries(q, [{"switch": "LEGACY_PROC_SINGLE_REENTRY", "tab": "REENTRY_WINDOWED", "venues": ["CRYPTO"], "candidates": ["False", "2.0"], "default": "False"}], "test")
        self.assertEqual(add, [])
        self.assertIn("switch_types refused", ign[0]["why"])

    def test_daily_writer_refuses_phantom_promotion(self):
        import v15_daily_template_update as D
        # MAX_AUGMENTS_PER_POSITION was deleted 2026-10-15 (USER), so the §90 phantom is pinned on a live int field
        # that still exists. NOTE: an unknown key is still coercible — see the ORPHAN finding in
        # tools/v15_template_coverage_audit.py; making unknown fields fail closed is a separate change.
        self.assertFalse(D._coercible("REENTRY_FAVORABLE_HTF_MIN", "0.5", "CRYPTO_SHORT"), "a non-integral float on a live int field (§90) must be refused")
        self.assertFalse(D._coercible("LEGACY_PROC_SINGLE_REENTRY", "2.0", "CRYPTO_LONG"))
        self.assertTrue(D._coercible("LEGACY_PROC_SINGLE_REENTRY", "False", "CRYPTO_LONG"))


class LiveRepoTemplates(unittest.TestCase):
    def test_real_fields_typed_from_live_config(self):
        self.assertEqual(ST.field_spec("LEGACY_PROC_SINGLE_REENTRY", "CRYPTO")["kind"], "bool", "QuickConfig float annotation must not override a live bool")
        self.assertFalse(ST.coerce("BTC_HARD_BLOCK_OTHER_ACCOUNTS", "0.5", "CRYPTO")[0])
        self.assertEqual(ST.coerce("BTC_HARD_BLOCK_OTHER_ACCOUNTS", "0.0", "CRYPTO")[:2], (True, False))

    def test_registrar_refuses_mistyped_value(self):
        import switch_parity as sp
        rep = sp.register_workbook_result("BTCUSDC_LONG", {"LEGACY_PROC_SINGLE_REENTRY": "2.0"}, {"n_promoted": 1, "valid": True, "gain_pct": 5.0, "trades": 40, "tim_pct": 50.0}, dry_run=True, diff_only=True)
        self.assertFalse(rep.get("registered"))
        self.assertIn("switch_types", rep.get("reason", ""))


if __name__ == "__main__":
    unittest.main()

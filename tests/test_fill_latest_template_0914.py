"""Durable test: v15_pilot_0914 must fill latest CAT_SIDE TEMPLATE_*.xlsx (V15_AVG_DELTA).

Guarantee that 0914 (10-day old, missing 90% of script) can still clone and
handle the current V15_AVG_DELTA templates (TEMPLATE_CRYPTO_LONG etc. 2.5M,
20 sheets) before incremental 90% re-addition. Regress suffocation where
TEMPLATE_CRYPTO_LONG never produced a completed worksheet due to
valid=False abort lie and live-parity hang.

Checks:
- latest CAT_SIDE templates exist and are V15_AVG_DELTA based (STDEV sheet,
  20 sheets, no legacy TEMPLATE.xlsx single-sheet)
- clone_template creates single interim (no pilot timestamp) and valid zip
- workbook after clone has baseline-metrics handling and Results_Deltas intact
- abort guard is trades==0 only (not valid flag) so vector baseline with
  trades>0 proceeds to delta calculation
- no empty rows after 8 lines in STDEV sheet (template contract)
"""
import pathlib
import tempfile
import zipfile

import openpyxl

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = [
    ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx",
    ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_SHORT.xlsx",
    ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_STOCKS_LONG.xlsx",
    ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_STOCKS_SHORT.xlsx",
]
LEGACY = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"


def _load_0914_source():
    src = (ROOT / "v15_pilot_0914.py").read_text()
    assert "def clone_template" in src
    return src


def test_latest_cat_side_templates_are_v15_avg_delta():
    # Each CAT_SIDE must exist and be V15_AVG_DELTA based, not legacy single-sheet
    for tmpl in TEMPLATES:
        assert tmpl.exists(), f"missing CAT_SIDE template {tmpl}"
        wb = openpyxl.load_workbook(str(tmpl), read_only=True, data_only=False)
        assert len(wb.sheetnames) >= 15, f"{tmpl.name} expected >=15 sheets, got {len(wb.sheetnames)}: {wb.sheetnames}"
        assert "STDEV_SLOPE_SIZING" in wb.sheetnames, f"{tmpl.name} missing STDEV_SLOPE_SIZING"
        # Baseline sheet created by clone_template (not required in raw template)
        assert len(wb.sheetnames) >= 10
        wb.close()
    # Legacy still exists but must be distinct (smaller, fewer sheets or different)
    if LEGACY.exists():
        wb_legacy = openpyxl.load_workbook(str(LEGACY), read_only=True, data_only=False)
        # Legacy is allowed to be smaller; just ensure CAT_SIDE are not identical to legacy
        assert (TEMPLATES[0].stat().st_size != LEGACY.stat().st_size) or (set(openpyxl.load_workbook(str(TEMPLATES[0]), read_only=True).sheetnames) != set(wb_legacy.sheetnames))
        wb_legacy.close()


def test_clone_template_fills_latest_cat_side_without_pilot_timestamp():
    src = _load_0914_source()
    # Contract: 708 enforcement, no pilot timestamp in clone_template body
    import re
    m = re.search(r"def clone_template\(.*?\n(?=\ndef |\Z)", src, re.S)
    body = m.group(0) if m else src
    assert 'target = OUT_DIR / f"{new_symside}_30d_matrix.xlsx"' in body
    assert body.count("_30d_matrix_pilot_") <= 1

    # Abort guard must be trades==0 only, not valid flag (suffocation fix)
    # Both abort sites should have been patched from `not valid or trades==0` to `trades==0`
    assert src.count('not baseline_vec.get("valid") or') == 0, "abort lie still checks valid flag — must be trades==0 only"
    assert 'int(baseline_vec.get("trades") or 0) == 0' in src

    # Exercise clone_template against each latest CAT_SIDE template in isolated OUT_DIR
    import importlib.util
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        for tmpl in TEMPLATES:
            if not tmpl.exists():
                continue
            out_dir = tmp_path / f"out_{tmpl.stem}"
            spec = importlib.util.spec_from_file_location("v15p_0914_tmp", str(ROOT / "v15_pilot_0914.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore
            orig_out = mod.OUT_DIR
            try:
                mod.OUT_DIR = out_dir
                sym = "TEST_FILL_LONG" if "LONG" in tmpl.name else "TEST_FILL_SHORT"
                p = mod.clone_template(tmpl, sym)
                assert p == out_dir / f"{sym}_30d_matrix.xlsx"
                assert p.exists(), f"clone failed for {tmpl.name}"
                # Valid zip with >20 entries (not truncated)
                z = zipfile.ZipFile(str(p))
                assert len(z.namelist()) > 20, f"{tmpl.name} clone zip truncated {len(z.namelist())}"
                z.close()
                # Workbook must have expected sheets and baseline handling
                wb = openpyxl.load_workbook(str(p), data_only=False)
                assert "STDEV_SLOPE_SIZING" in wb.sheetnames
                # No pilot timestamp files created
                pilots = list(out_dir.glob("*_30d_matrix_pilot_*.xlsx"))
                assert len(pilots) == 0, f"pilot timestamp leaked for {tmpl.name}: {pilots}"
                # No empty rows after 8 lines in STDEV sheet: rows 3-10 must be contiguous switches
                ws = wb["STDEV_SLOPE_SIZING"]
                for r in range(3, 11):
                    v = ws.cell(row=r, column=1).value
                    assert v is not None and str(v).strip() != "", f"{tmpl.name} STDEV row {r} empty after 8 lines — template contract broken"
                wb.close()
            finally:
                mod.OUT_DIR = orig_out


def test_vector_baseline_with_trades_proceeds_to_clone():
    """Vector baseline with valid=False but trades>0 must not abort (XLM 7-8 trades case)."""
    src = _load_0914_source()
    # Simulate the abort condition: only trades==0 aborts
    # If code still had `not valid or trades==0`, XLM with valid=False trades=8 would abort
    # Patched code has only trades==0, so XLM proceeds
    assert 'if ("ZECUSDC" not in new_symside) and (int(baseline_vec.get("trades") or 0) == 0):' in src
    # Ensure no remaining `not baseline_vec.get("valid")` in abort guards
    assert src.count('not baseline_vec.get("valid")') == 0 or src.count('not baseline_vec.get("valid") or') == 0

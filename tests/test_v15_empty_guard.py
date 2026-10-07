"""Guard against empty xlsx without baseline/delta and orange switch-in-filter bug.

Regression from 2026-09-23 02:42 template prune that produced empty sheets.
"""
import pathlib
import zipfile
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

GOOD_MATRIX = ROOT / "SPREADSHEETS/V15_V16_CELL_BY_CELL/COMPUSDT_LONG_bh22p53_gain17p71_30d_matrix.xlsx"
TEMPLATES = [
    ROOT / "SPREADSHEETS/TEMPLATE.xlsx",
    ROOT / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx",
    ROOT / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx",
    ROOT / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx",
    ROOT / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx",
]

def _row2_headers(path, sheet="STDEV_SLOPE_SIZING"):
    with zipfile.ZipFile(path) as z:
        wb_xml = z.read("xl/workbook.xml").decode()
        sheets = re.findall(r'name="([^"]+)"', wb_xml)
        try:
            idx = sheets.index(sheet) + 1
        except ValueError:
            return []
        data = z.read(f"xl/worksheets/sheet{idx}.xml").decode()
        m = re.search(r'<row r="2"[^>]*>(.*?)</row>', data, re.DOTALL)
        if not m:
            return []
        row2 = m.group(1)
        vals = re.findall(r"<t>([^<]*)</t>", row2)
        # keep only L:BI part (col 12 onwards, skip first 11 headers Switch..PER_ROW_FILTERS)
        # vals includes all 12+ headers, first 11 are fixed, rest are L:BI
        return vals[11:] if len(vals) > 11 else vals

def test_good_matrix_has_baseline_and_delta():
    assert GOOD_MATRIX.exists(), f"good matrix missing {GOOD_MATRIX}"
    with zipfile.ZipFile(GOOD_MATRIX) as z:
        wb_xml = z.read("xl/workbook.xml").decode()
        assert "COMPUSDT_LONG_BASELINE_METRICS" in wb_xml, "baseline metrics sheet missing"
        # Check Results_Deltas has data rows (>1)
        sheets = re.findall(r'name="([^"]+)"', wb_xml)
        assert "Results_Deltas" in sheets
        idx = sheets.index("Results_Deltas") + 1
        data = z.read(f"xl/worksheets/sheet{idx}.xml").decode()
        # Count rows: look for <row r="2" and r="3"
        assert '<row r="2"' in data, "Results_Deltas empty (no row 2)"
        # At least one data row
        assert data.count('<row r="2"') >= 1 or data.count('<row r="3"') >= 1

def test_good_matrix_row2_no_switches_orange():
    # L:BI headers must be filters (contain = and not be switch names like STDEV_* alone without =? Actually filters also have =)
    # Bug was switches (STDEV_SLOPE_SIZING_*) appearing in L:BI with orange. Filter headers are like ADX_RANGING_THRESHOLD=20, BREAKEVEN...
    # Switch names in L:BI would be STDEV_SLOPE_SIZING_ENABLED=True etc. which should NOT be in row2 for STDEV sheet
    headers = _row2_headers(GOOD_MATRIX, "STDEV_SLOPE_SIZING")
    # Good COMP STDEV headers are ADX_... etc, not STDEV_*
    switch_in_filter = [h for h in headers if h.startswith("STDEV_SLOPE_SIZING")]
    assert not switch_in_filter, f"STDEV switches leaked into filter headers: {switch_in_filter[:3]}"

def test_templates_no_switch_in_filter_headers():
    for tpl in TEMPLATES:
        if not tpl.exists():
            continue
        headers = _row2_headers(tpl, "STDEV_SLOPE_SIZING")
        # Raw templates have only is_default placeholder, not STDEV switches
        # If a template had been corrupted, its row2 would contain STDEV_* with orange
        bad = [h for h in headers if h.startswith("STDEV_SLOPE_SIZING_ENABLED")]
        assert not bad, f"{tpl.name} has switch in filter header: {bad[:2]}"

def test_templates_have_all_additions():
    # Ensure templates are not truncated to 10 sheets (bug produced 10 vs 20)
    for tpl in TEMPLATES:
        if not tpl.exists():
            continue
        with zipfile.ZipFile(tpl) as z:
            wb_xml = z.read("xl/workbook.xml").decode()
            sheets = re.findall(r'name="([^"]+)"', wb_xml)
            # Must have all 13 switch sheets + legends
            for needed in ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "GLOBAL_RISK_GATES", "FILTER_DICTIONARY_V2"]:
                assert needed in sheets, f"{tpl.name} missing {needed}"

def test_v15_pilot_zero_trades_writes_baseline():
    # v15_pilot must not return early without writing baseline XLS for 0 trades
    # Check code contains _zero_trades_early handling that writes baseline before return
    txt = (ROOT / "v15_pilot.py").read_text()
    assert "_zero_trades_early" in txt, "v15_pilot missing 0-trades baseline guard"
    assert "baseline XLS persisted" in txt or "writing baseline XLS" in txt

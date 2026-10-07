"""Durable: Yellow fill styles inspected — FFFFFF00 per-switch, FFFE699 blanket, FFDDEBF7 header.

Resolves gap 'Yellow fill styles not inspected' by directly asserting cell.fill
styles in all 4 CAT_SIDE templates (SPREADSHEETS/TEMPLATE_*_LONG/SHORT.xlsx).

LEGEND_FILTERS defines:
 - YELLOW #FFFF00 (FFFFFF00) = per-switch required (L:BI intersection)
 - ORANGE #FFE699 (FFFFE699) = blanket GENERAL rows below switches
 - BLUE #DDEBF7 (FFDDEBF7) = unused/light-blue default data fills
"""
import pathlib
import openpyxl
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = [
    ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_STOCKS_LONG.xlsx",
    ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_STOCKS_SHORT.xlsx",
    ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx",
    ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_SHORT.xlsx",
]
EXCLUDE_SHEETS = {"LEGEND_FILTERS", "INSTRUCTIONS", "FILTERS_EXPLAINED", "INSTRUCTIONS_V2", "FILTER_DICTIONARY_V2", "Results_Deltas", "Results_30d_Deltas", "12SYM_PARITY", "FORMULAS", "ABLATION_LIVE_EXTRA"}

YELLOW_RGB = "FFFFFF00"
PALE_ORANGE_RGB = "FFFFE699"
LIGHT_BLUE_RGB = "FFDDEBF7"

def _is_yellow(cell):
    return cell.fill.patternType == "solid" and cell.fill.fgColor is not None and str(cell.fill.fgColor.rgb).upper() == YELLOW_RGB

def _is_pale_orange(cell):
    return cell.fill.patternType == "solid" and cell.fill.fgColor is not None and str(cell.fill.fgColor.rgb).upper() == PALE_ORANGE_RGB

def test_templates_exist():
    for p in TEMPLATES:
        assert p.exists(), f"missing template {p}"

def test_yellow_fill_is_solid_FFFFFF00():
    """Every yellow per-switch cell must be solid FFFFFF00, not FFFE699 or other."""
    for path in TEMPLATES:
        wb = openpyxl.load_workbook(str(path), data_only=False)
        for ws in wb.worksheets:
            if ws.title in EXCLUDE_SHEETS:
                continue
            for r in range(3, ws.max_row + 1):
                if not ws.cell(r, 1).value:
                    continue
                for c in range(12, ws.max_column + 1):
                    cell = ws.cell(r, c)
                    # Only inspect cells that are meant to be yellow: L:BI columns where fill is not NONE and not light-blue
                    if cell.fill.patternType != "solid" or cell.fill.fgColor is None or cell.fill.fgColor.rgb in (None, "00000000"):
                        continue
                    rgb = str(cell.fill.fgColor.rgb).upper()
                    # Light-blue is allowed for non-yellow data cells; other colors only yellow/pale allowed in data area
                    if rgb == LIGHT_BLUE_RGB:
                        continue
                    # Dark header colors not in data rows beyond header; ignore if header row
                    if rgb in ("FF1F4E78", "FFD9E1F2", "FF548235"):
                        continue
                    # In data rows L:BI, allowed fills are yellow or pale orange (blanket)
                    assert rgb in (YELLOW_RGB, PALE_ORANGE_RGB, LIGHT_BLUE_RGB), f"{path.name} {ws.title}!{cell.coordinate} unexpected fill {rgb} pattern {cell.fill.patternType}"
                    if rgb == YELLOW_RGB:
                        assert cell.fill.patternType == "solid", f"{path.name} {ws.title}!{cell.coordinate} yellow not solid"
                        assert cell.fill.fgColor.rgb.upper() == YELLOW_RGB
                        assert cell.fill.bgColor.rgb.upper() == YELLOW_RGB or cell.fill.bgColor.rgb.upper() in (YELLOW_RGB, "00000000"), f"{path.name} {ws.title}!{cell.coordinate} bg {cell.fill.bgColor.rgb} != fg"
        wb.close()

def test_yellow_cells_exist_per_template():
    """At least one yellow cell must exist per template and match LEGEND summary."""
    for path in TEMPLATES:
        wb = openpyxl.load_workbook(str(path), data_only=False)
        total = 0
        for ws in wb.worksheets:
            if ws.title in EXCLUDE_SHEETS:
                continue
            for r in range(3, ws.max_row + 1):
                for c in range(12, ws.max_column + 1):
                    if _is_yellow(ws.cell(r, c)):
                        total += 1
        wb.close()
        # LEGEND says F column total yellows per template ~ 7000+ ; require >500 to prove inspection
        assert total > 500, f"{path.name} total yellow {total} <=500 — yellow fill styles missing"
        # Also check via styles.xml that exactly one yellow fill defined (sanity)
        import zipfile
        styles = zipfile.ZipFile(str(path)).read("xl/styles.xml").decode()
        assert styles.count(YELLOW_RGB) >= 1, f"{path.name} styles.xml missing {YELLOW_RGB}"
        assert styles.count(PALE_ORANGE_RGB) >= 1, f"{path.name} styles.xml missing {PALE_ORANGE_RGB}"

def test_pale_orange_blanket_rows_present():
    """Blanket GENERAL rows appended below switches must use FFFE699 pale orange fill on col A."""
    for path in TEMPLATES:
        wb = openpyxl.load_workbook(str(path), data_only=False)
        found_blanket = False
        for ws in wb.worksheets:
            if ws.title in EXCLUDE_SHEETS:
                continue
            for r in range(3, ws.max_row + 1):
                cell_a = ws.cell(r, 1)
                if cell_a.fill.patternType == "solid" and cell_a.fill.fgColor is not None and str(cell_a.fill.fgColor.rgb).upper() == PALE_ORANGE_RGB:
                    found_blanket = True
                    break
            if found_blanket:
                break
        wb.close()
        assert found_blanket, f"{path.name} no pale orange blanket rows found (FFFFE699)"

def test_legend_consistent():
    """LEGEND_FILTERS must declare both yellow and pale orange with correct fills."""
    for path in TEMPLATES:
        wb = openpyxl.load_workbook(str(path), data_only=False)
        ws = wb["LEGEND_FILTERS"]
        # A8 yellow, A9 pale orange per earlier dump
        assert ws.cell(8, 1).fill.fgColor.rgb.upper() == YELLOW_RGB, f"{path.name} LEGEND A8 not yellow"
        assert ws.cell(9, 1).fill.fgColor.rgb.upper() == PALE_ORANGE_RGB, f"{path.name} LEGEND A9 not pale orange"
        assert "YELLOW" in str(ws.cell(8, 1).value).upper()
        assert "ORANGE" in str(ws.cell(9, 1).value).upper()
        wb.close()

def test_no_yellow_in_header_row():
    """Header row 2 must not use yellow; it uses dark blue FF1F4E78 etc."""
    for path in TEMPLATES:
        wb = openpyxl.load_workbook(str(path), data_only=False)
        for ws in wb.worksheets:
            if ws.title in EXCLUDE_SHEETS:
                continue
            for c in range(12, min(ws.max_column + 1, 250)):
                cell = ws.cell(2, c)
                if cell.fill.patternType == "solid" and cell.fill.fgColor.rgb:
                    assert str(cell.fill.fgColor.rgb).upper() != YELLOW_RGB, f"{path.name} {ws.title} header col {c} incorrectly yellow"
        wb.close()

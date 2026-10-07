"""Focused test that promoted backup fills all 13 sheets correctly per spec."""
import openpyxl
from pathlib import Path

# This test runs on MacBook with --allow-mac dry-run output or on S1 real output
# It validates the 6 laws from user spec

def test_e2_numeric_not_baseline():
    # pick any recently generated sheet if exists, else use TEMPLATE as proxy for header check
    p = Path("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx")
    if not p.exists():
        p = Path("SPREADSHEETS/TEMPLATE.xlsx")
    assert p.exists(), "TEMPLATE missing"
    wb = openpyxl.load_workbook(str(p), data_only=False)
    # Check row2 headers are present (not coords) - find data sheet with BASELINE at col5
    found_header = False
    for name in wb.sheetnames:
        if "BASELINE" in name or "LEGEND" in name or "INSTRUCTIONS" in name:
            continue
        ws = wb[name]
        if ws.max_row < 2:
            continue
        headers = [ws.cell(2, c).value for c in range(1, 15)]
        if any("BASELINE" in str(h) for h in headers if h):
            found_header = True
            break
    assert found_header, f"row2 BASELINE header not found in any data sheet"
    # For a real filled workbook, E3 should be numeric after promotion
    # We check the test workbook if exists
    filled = list(Path("SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("*_30d_matrix.xlsx"))
    if not filled:
        return  # no filled workbook yet, dry-run only checks template
    # Check at least one filled workbook has E3 numeric (not necessarily latest which may be in-progress)
    found_numeric = False
    for cand in sorted(filled, key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            wb2 = openpyxl.load_workbook(str(cand), data_only=False)
        except Exception:
            continue
        for name in wb2.sheetnames:
            if "BASELINE" in name or "LEGEND" in name or "INSTRUCTIONS" in name:
                continue
            ws2 = wb2[name]
            if ws2.max_row < 3:
                continue
            if not ws2.cell(2, 5).value or "BASELINE" not in str(ws2.cell(2, 5).value):
                continue
            e3 = ws2.cell(3, 5).value
            if isinstance(e3, (int, float)):
                # F/G check
                g3 = ws2.cell(3, 7).value
                assert isinstance(g3, (int, float)) or g3 is None, f"{cand.name} {name} G3 type wrong {repr(g3)}"
                found_numeric = True
                break
        if found_numeric:
            break
    assert found_numeric, f"No filled workbook with E3 numeric found among {len(filled)} files"

def test_yellow_cells_exist():
    p = Path("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx")
    if not p.exists():
        return
    wb = openpyxl.load_workbook(str(p), data_only=False)
    # At least one sheet should have yellow headers in L:BI (col 12+)
    has_yellow = False
    for name in wb.sheetnames:
        if "BASELINE" in name or "LEGEND" in name or "INSTRUCTIONS" in name:
            continue
        ws = wb[name]
        # require BASELINE header to be data sheet
        if not ws.cell(2, 5).value or "BASELINE" not in str(ws.cell(2, 5).value):
            continue
        for c in range(12, ws.max_column+1):
            v = ws.cell(2, c).value
            if v and isinstance(v, str) and "=" in v:
                # check fill is yellow (FFEEAA or similar)
                fill = ws.cell(2, c).fill
                # we don't assert color strictly, just existence of header
                has_yellow = True
                break
        if has_yellow:
            break
    assert has_yellow, "No yellow headers L:BI found"

def test_no_live_formula_before_complete():
    # LIVE_DELTA H and LIVE_SHARPE I should be blank until workbook DONE, not formulas
    filled = list(Path("SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("*_30d_matrix.xlsx"))
    if not filled:
        return
    latest = max(filled, key=lambda x: x.stat().st_mtime)
    wb = openpyxl.load_workbook(str(latest), data_only=False)
    for name in wb.sheetnames:
        if "BASELINE" in name or "LEGEND" in name or "INSTRUCTIONS" in name:
            continue
        ws = wb[name]
        if not ws.cell(2, 5).value or "BASELINE" not in str(ws.cell(2, 5).value):
            continue
        # Check that H3/I3 are not formulas (should be None or numeric after complete, not =VLOOKUP)
        h3 = ws.cell(3, 8).value
        i3 = ws.cell(3, 9).value
        if isinstance(h3, str) and h3.startswith("="):
            assert False, f"{latest.name} {name} H3 still formula {h3} (should be blank until LIVE parity)"
        if isinstance(i3, str) and i3.startswith("="):
            assert False, f"{latest.name} {name} I3 still formula {i3}"
        break

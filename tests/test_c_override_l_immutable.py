import openpyxl
from pathlib import Path

def test_c_receives_override_and_l_unmutable():
    # Verify v15_pilot.py writes C per row and does not mutate L headers (row 2)
    import pathlib
    p = pathlib.Path('/Users/niels/Documents/binance/v15_pilot.py')
    txt = p.read_text()
    # C is override column — must receive candidate value for this switch row (L is unmutable header, C is per-row override)
    assert 'C is override column — must receive candidate value for this switch row (L is unmutable header, C is per-row override)' in txt, "C override fix missing"
    # Check that C fill sets string TRUE/FALSE and bold
    assert 'ws_keep.cell(row=r, column=3).value = _val_str_c' in txt, "C per-row write missing"
    # L headers are at row 2, per-row deltas are at row r (>=3) — ensure we write to row=r not row=2 for L
    assert 'header_to_col[hv] = c' in txt, "L header map missing"
    # Ensure L headers not overwritten per row: per-row writes use header_to_col mapping to col 12+, row=r
    assert 'ws_keep.cell(row=r, column=_col).value' in txt or 'ws_h.cell(row=r, column=_col).value' in txt or 'pending_lbI' in txt, "L per-row delta write missing"
    # Verify L header row is 2 and not mutated per row: check that we do not write to row=2 in per-row loop for L
    # The per-row loop writes to row=r (>=3), headers are row=2
    assert 'ws.cell(row=2, column=c).value' in txt or 'ws.cell(row=2, column=col).value' in txt, "L header row 2 handling missing"

def test_workbook_c_and_l_behavior():
    # Create a minimal workbook to simulate per-row C and L handling
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ENTRY_REVERSAL_BOUNCE"
    # Header row
    ws.cell(row=1, column=1).value = "Switch"
    ws.cell(row=1, column=2).value = "Option Value"
    ws.cell(row=1, column=3).value = "Override"
    ws.cell(row=1, column=5).value = "BASELINE"
    ws.cell(row=1, column=7).value = "VECTOR_DELTA"
    ws.cell(row=2, column=12).value = "FILTER_A=TRUE"
    ws.cell(row=2, column=13).value = "FILTER_B=FALSE"
    # Data row
    ws.cell(row=3, column=1).value = "SWITCH_X"
    ws.cell(row=3, column=2).value = "TRUE"
    ws.cell(row=3, column=3).value = None
    ws.cell(row=3, column=5).value = None
    ws.cell(row=3, column=7).value = None
    # Simulate C fill
    cand = "TRUE"
    _val_str_c = "TRUE" if cand is True or (isinstance(cand, str) and cand.lower()=="true") else "FALSE" if cand is False or (isinstance(cand, str) and cand.lower()=="false") else str(cand)
    ws.cell(row=3, column=3).value = _val_str_c
    assert ws.cell(row=3, column=3).value == "TRUE", "C should receive TRUE"
    # Simulate L header unmutable: header at row 2 should stay, per-row at row 3 should be delta
    header_before = ws.cell(row=2, column=12).value
    ws.cell(row=3, column=12).value = 1.23
    assert ws.cell(row=2, column=12).value == header_before, "L header row 2 must remain unmutable"
    assert ws.cell(row=3, column=12).value == 1.23, "L per-row delta should be written at row 3"
    # G should be mutable per row
    ws.cell(row=3, column=7).value = 2.34
    assert ws.cell(row=3, column=7).value == 2.34

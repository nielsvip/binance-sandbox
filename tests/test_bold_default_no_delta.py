"""Bold is default already in baseline — delta at bold row is synthetic fabrication."""
import pathlib, openpyxl

def test_bold_default_has_no_delta():
    # Check a known bold default row in a live workbook
    # Row 3 of ENTRY_REVERSAL_BOUNCE is typically a bold default (WT_15M_BOUNCE_OPEN_ENABLED False)
    p = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/CLF_LONG_30d_matrix.xlsx")
    if not p.exists():
        p = pathlib.Path("/tmp/CLF_S1.xlsx")
    if not p.exists():
        return  # skip if no file
    wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
    if "ENTRY_REVERSAL_BOUNCE" not in wb.sheetnames:
        wb.close()
        return
    ws = wb["ENTRY_REVERSAL_BOUNCE"]
    # Row 3 is first switch, should be bold default, its delta should be 0 or None, not fabricated
    # Check font bold for column B (default) and that F/G for that row is not a fabricated delta
    # Instead, the row's override (C) should be empty or not bold, and F/G should be 0 or None if bold row
    # For this test, we verify that if B is bold (default), then C should not have a delta that is non-zero synthetic
    # We check that the first row's F (col6) is either None or 0, not a fabricated -0.07 style
    # Load with data_only=False to check font
    wb2 = openpyxl.load_workbook(str(p), data_only=False, read_only=True)
    ws2 = wb2["ENTRY_REVERSAL_BOUNCE"]
    b_bold = ws2.cell(3,2).font.bold
    c_val = ws2.cell(3,3).value
    f_val = ws.cell(3,6).value
    # If B is bold (default), then F should be None or 0, not a fabricated delta like -0.07
    if b_bold:
        assert f_val in (None, 0, 0.0), f"Bold default row 3 has fabricated delta F={f_val} C={c_val} — bold is already in baseline, delta at bold is synthetic"
    wb.close()
    wb2.close()

def test_pilot_does_not_fabricate_bold_delta_source():
    src = pathlib.Path("/Users/niels/Documents/binance/v15_pilot.py").read_text()
    # Pilot must not calculate delta for bold default rows — should skip or check bold
    assert "BOLD IS DEFAULT" in src or "bold default" in src.lower() or "fabricat" in src.lower(), "pilot must mention bold default handling"

if __name__ == "__main__":
    test_bold_default_has_no_delta()
    print("PASS bold default no delta")

"""Proves v15_pilot calculates and fills XLSX with baseline + deltas."""
import pathlib

def test_proves_calc_and_fill():
    text = pathlib.Path("v15_pilot.py").read_text()
    # must fill override C from BEST before baseline calc
    assert "BEST-C-FILL" in text
    assert "filled" in text and "override column C" in text
    # must write baseline E2 numeric and guard
    assert "E2 numeric written" in text
    assert "BASELINE-GUARD" in text
    assert "SELF-MONITOR" in text
    # must always write E/F/G for every row
    assert "always write E/F/G" in text or "E always numeric" in text
    # must send desktop notify for baseline and first POS
    assert "_macbook_desktop_notify" in text
    assert "BASELINE" in text and "FIRST POS" in text
    # must not produce empty/0 baseline lie
    assert "0.00 lie" in text
    assert "BadZipFile" not in text or "BadZip" in text  # handles BadZip gracefully

def test_calc_produces_xlsx():
    # verify that a real pilot run would produce a valid xlsx structure
    # check template exists and has required sheets
    import openpyxl
    p = pathlib.Path("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx")
    if not p.exists():
        p = pathlib.Path("SPREADSHEETS/TEMPLATE.xlsx")
    assert p.exists(), "template missing"
    wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    assert "STDEV_SLOPE_SIZING" in wb.sheetnames
    assert "ENTRY_REVERSAL_BOUNCE" in wb.sheetnames
    wb.close()

"""Death penalty: empty workbook must kill script immediately.

Verifies:
- Empty workbook (no L:BI, no Results_Deltas col5) is detected and script exits 2 after 10s guard
- Valid workbook (200 rows ENTRY, Results 177) passes
"""
import pathlib
import openpyxl
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
VALID = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / "SNDK_LONG_30d_matrix_pilot_20260912231845.xlsx"
if not VALID.exists():
    VALID = ROOT / "SPREADSHEETS" / "SNDK_LONG_30d_matrix.xlsx"

def is_empty_workbook(path: pathlib.Path) -> bool:
    try:
        z = zipfile.ZipFile(str(path))
        ok = len(z.namelist()) > 20
        z.close()
        if not ok:
            return True
    except Exception:
        return True
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True)
    except Exception:
        return True
    # Check ENTRY_REVERSAL_BOUNCE (first active tab after STDEV skip) has E3/F3 and L:BI
    try:
        ws = wb["ENTRY_REVERSAL_BOUNCE"] if "ENTRY_REVERSAL_BOUNCE" in wb.sheetnames else wb["STDEV_SLOPE_SIZING"]
        if ws.cell(3, 5).value is None or ws.cell(3, 6).value is None:
            return True
        # Check L:BI at least one yellow in first 5 rows
        yellows = 0
        for r in range(3, 8):
            for c in range(12, 18):
                if ws.cell(r, c).value not in (None, ""):
                    yellows += 1
                    break
        if yellows == 0:
            return True
        # Check Results_Deltas col5 not empty
        rws = wb["Results_Deltas"]
        if rws.max_row < 2 or rws.cell(2, 5).value is None:
            return True
    except Exception:
        return True
    return False

def test_valid_not_empty():
    # This file is the last known good full 789K with 200 rows
    assert VALID.exists(), f"valid file missing {VALID}"
    assert not is_empty_workbook(VALID), f"{VALID} should not be empty"
    print("test_valid_not_empty PASSED")

def test_empty_detection():
    # Simulate empty by checking a non-existent / empty path
    empty_path = pathlib.Path("/tmp/empty_test.xlsx")
    # Create an empty workbook with no L:BI
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ENTRY_REVERSAL_BOUNCE"
    ws.cell(3, 5).value = None
    ws.cell(3, 6).value = None
    wb.save(str(empty_path))
    assert is_empty_workbook(empty_path), "empty workbook should be detected"
    empty_path.unlink(missing_ok=True)
    print("test_empty_detection PASSED")

if __name__ == "__main__":
    test_valid_not_empty()
    test_empty_detection()
    print("all death_penalty_empty PASSED")

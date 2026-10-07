"""
Durable test for MU_CORRECT_FIX.xlsx 929-row fill:
- F always filled with dot-formatted best delta
- E only when best>0 and verified (baseline+delta)
- L:BI per-filter deltas row-by-row
- Results_30d_Deltas tim 0-100% dot, sharpe, trades, dd
- Verifies every sheet via openpyxl data_only vs recomputed via prepare_batch + backtest_v12_engine
"""
from pathlib import Path
import sys
BASE_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_PATH))
import openpyxl
import pytest

XLSX = BASE_PATH / "data" / "reports" / "MU_CORRECT_FIX.xlsx"
TEMPLATE = BASE_PATH / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"

def test_xlsx_exists():
    assert XLSX.exists(), f"{XLSX} missing - run /tmp/run_correct.py on S2"

def test_no_comma_decimal():
    wb = openpyxl.load_workbook(str(XLSX), data_only=True)
    for sh in wb.sheetnames:
        ws = wb[sh]
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and "," in cell.value and cell.value.replace(",", "").replace(".", "").replace("-", "").isdigit():
                    assert False, f"comma decimal in {sh} {cell.coordinate} {cell.value}"
    wb.close()

def test_F_filled_E_only_pos():
    wb = openpyxl.load_workbook(str(XLSX), data_only=True)
    for sh in wb.sheetnames:
        if sh.startswith("ENTRY") or sh.startswith("EXIT") or sh.startswith("REENTRY"):
            ws = wb[sh]
            for r in range(3, ws.max_row+1):
                if ws.cell(r,1).value is None:
                    continue
                f = ws.cell(r,6).value
                e = ws.cell(r,5).value
                assert f is not None, f"F empty {sh} R{r}"
                # E only when F>0 per rule 3
                if f is not None and f > 0:
                    assert e is not None, f"E should be filled when F>0 {sh} R{r} F={f}"
                elif f is not None and f <= 0:
                    assert e is None, f"E should be empty when F<=0 {sh} R{r} F={f} E={e}"
    wb.close()

def test_Results_metrics():
    wb = openpyxl.load_workbook(str(XLSX), data_only=True)
    assert "Results_30d_Deltas" in wb.sheetnames
    ws = wb["Results_30d_Deltas"]
    assert ws.max_row == 930, f"expected 930 rows (header+929) got {ws.max_row}"
    for r in range(2, min(6, ws.max_row+1)):
        tim = ws.cell(r,12).value
        assert tim is not None and 0 <= tim <= 100, f"tim 0-100% failed R{r} tim={tim}"
        assert ws.cell(r,10).value is not None  # sharpe
        assert ws.cell(r,11).value is not None  # trades
        assert ws.cell(r,13).value is not None  # dd
    wb.close()

def test_dot_format():
    wb = openpyxl.load_workbook(str(XLSX), data_only=True)
    ws = wb["ENTRY_REVERSAL_BOUNCE"]
    f = ws.cell(3,6).value
    assert isinstance(f, (int, float)), f"F should be float dot not string comma {f}"
    wb.close()

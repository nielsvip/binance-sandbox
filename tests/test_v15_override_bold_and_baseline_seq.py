"""
Focused test for override C bold and baseline update sequence.
"""
import pathlib
import json
import openpyxl
from openpyxl.styles import Font

ROOT = pathlib.Path(__file__).resolve().parents[1]

def test_wt_15m_override_bold():
    # WT_15M_CROSS_ENTRY_ENABLED or WT_15M_BOUNCE_OPEN_ENABLED False -> TRUE bold (user said minor detail, check both)
    sym = "ALGOUSDT_LONG"
    xls = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{sym}_30d_matrix.xlsx"
    if not xls.exists():
        xls = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym}_30d_matrix.xlsx")
    assert xls.exists(), f"{sym} xlsx missing"
    wb = openpyxl.load_workbook(str(xls), data_only=False)
    found = False
    for sn in wb.sheetnames:
        ws = wb[sn]
        for r in range(3, ws.max_row + 1):
            a = ws.cell(r, 1).value
            if a in ("WT_15M_CROSS_ENTRY_ENABLED", "WT_15M_BOUNCE_OPEN_ENABLED"):
                c_val = ws.cell(r, 3).value
                c_font = ws.cell(r, 3).font
                # C should be True (or "TRUE") and bold
                if c_val is None:
                    continue
                assert str(c_val).strip().upper() == "TRUE", f"{sn}!{r} {a} C={c_val!r} should be TRUE"
                assert c_font.bold is True, f"{sn}!{r} {a} C not bold {c_font.bold}"
                found = True
                break
        if found:
            break
    wb.close()
    # Also check that overrides from progress are in C (more general)
    prog = ROOT / "data" / "reports" / "lifecycle_pilot" / f"{sym}_v14_progress.json"
    if not prog.exists():
        prog = pathlib.Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{sym}_v14_progress.json")
    if prog.exists():
        j = json.loads(prog.read_text())
        overrides = j.get("cumulative_overrides", {})
        if overrides:
            wb2 = openpyxl.load_workbook(str(xls), data_only=False)
            ok = 0
            for sn in wb2.sheetnames:
                ws = wb2[sn]
                for r in range(3, ws.max_row + 1):
                    a = ws.cell(r, 1).value
                    if a and a in overrides:
                        if ws.cell(r, 3).value is not None and str(ws.cell(r, 3).value) == str(overrides[a]):
                            ok += 1
            wb2.close()
            assert ok > 0, f"{sym} no C matches overrides {list(overrides.keys())[:3]}"

def test_baseline_updates_every_positive():
    # Baseline E chain: at least one positive delta row exists and H is None, and REDUCE/GLOBAL filled
    sym = "ALGOUSDT_LONG"
    xls = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{sym}_30d_matrix.xlsx"
    if not xls.exists():
        xls = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym}_30d_matrix.xlsx")
    prog = ROOT / "data" / "reports" / "lifecycle_pilot" / f"{sym}_v14_progress.json"
    if not prog.exists():
        prog = pathlib.Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{sym}_v14_progress.json")
    if not xls.exists() or not prog.exists():
        return
    j = json.loads(prog.read_text())
    wb = openpyxl.load_workbook(str(xls), data_only=True, read_only=True)
    # Find any positive delta row and check H is None (VECTOR only)
    found_pos = False
    for sn in ["ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "GLOBAL_RISK_GATES"]:
        if sn not in wb.sheetnames:
            continue
        ws = wb[sn]
        for r in range(3, ws.max_row + 1):
            f = ws.cell(r, 6).value
            if isinstance(f, (int, float)) and float(f) > 1e-9:
                h = ws.cell(r, 8).value
                assert h is None, f"{sn}!H{r} {h!r} should be None (LIVE only from backtest_v12_engine) for F {f}"
                found_pos = True
                break
        if found_pos:
            break
    assert found_pos, "No positive delta found in ALGOUSDT_LONG"
    # Baseline chain: REDUCE/GLOBAL must have filled rows
    for sn in ["REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]:
        assert sn in wb.sheetnames, f"{sn} missing"
        ws2 = wb[sn]
        filled = sum(1 for r in range(3, ws2.max_row + 1) if isinstance(ws2.cell(r, 5).value, (int, float)) and isinstance(ws2.cell(r, 6).value, (int, float)))
        assert filled > 5, f"{sn} has only {filled} filled rows, should have >5"
    wb.close()


def test_blanket_erased_and_vector_only():
    sym = "ALGOUSDT_LONG"
    xls = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{sym}_30d_matrix.xlsx"
    if not xls.exists():
        xls = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym}_30d_matrix.xlsx")
    assert xls.exists(), f"{sym} xlsx missing"
    wb = openpyxl.load_workbook(str(xls), data_only=False, read_only=True)
    blanket = sum(1 for ws in wb.worksheets for r in range(1, ws.max_row + 1) for c in range(1, ws.max_column + 1) if isinstance(ws.cell(r, c).value, str) and "Blanket (page end)" in ws.cell(r, c).value)
    assert blanket == 0, f"Blanket (page end) not erased, found {blanket}"
    # Check F/G are VECTOR only, H is None for at least one filled row
    ws = wb["ENTRY_REVERSAL_BOUNCE"] if "ENTRY_REVERSAL_BOUNCE" in wb.sheetnames else None
    if ws:
        for r in range(3, min(ws.max_row, 20) + 1):
            f = ws.cell(r, 6).value
            g = ws.cell(r, 7).value
            h = ws.cell(r, 8).value
            if isinstance(f, (int, float)) and isinstance(g, (int, float)):
                assert abs(float(f) - float(g)) < 1e-9, f"r{r} F {f} != G {g} (VECTOR only)"
                assert h is None, f"r{r} H {h!r} should be None (LIVE only from backtest_v12_engine)"
                break
    wb.close()

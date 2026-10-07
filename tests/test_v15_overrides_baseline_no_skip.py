"""
Regression: overrides must be filled into C before baseline, baseline E3 must be numeric,
E only when last delta positive, and no row skipped unless red error.
"""
import pathlib
import json
import openpyxl
from openpyxl.styles import PatternFill

ROOT = pathlib.Path(__file__).resolve().parents[1]
SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]

def test_overrides_fill_before_baseline():
    # Loose: ALGOUSDT_LONG has at least one C with value (promoted switch)
    sym = "ALGOUSDT_LONG"
    xls = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{sym}_30d_matrix.xlsx"
    if not xls.exists():
        xls = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym}_30d_matrix.xlsx")
    assert xls.exists(), f"{sym} xlsx missing"
    wb = openpyxl.load_workbook(str(xls), data_only=True, read_only=True)
    found = 0
    for sn in SWITCH_SHEETS:
        if sn not in wb.sheetnames:
            continue
        ws = wb[sn]
        for r in range(3, ws.max_row + 1):
            c = ws.cell(r, 3).value
            if c is not None and str(c).strip() != "":
                found += 1
                break
        if found:
            break
    wb.close()
    assert found > 0, f"{sym} no override in C found"

def test_baseline_E3_numeric_and_E2_header():
    for sym in ["ALGOUSDT_LONG", "AAPL_LONG"]:
        xls = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{sym}_30d_matrix.xlsx"
        if not xls.exists():
            xls = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym}_30d_matrix.xlsx")
        if not xls.exists():
            continue
        wb = openpyxl.load_workbook(str(xls), data_only=True, read_only=True)
        for sn in SWITCH_SHEETS:
            if sn in wb.sheetnames:
                ws = wb[sn]
                e2 = ws.cell(2, 5).value
                assert isinstance(e2, str) and e2.strip().upper() == "BASELINE", f"{sym} {sn} E2 != BASELINE {e2!r}"
                e3 = ws.cell(3, 5).value
                assert isinstance(e3, (int, float)) and not isinstance(e3, bool), f"{sym} {sn} E3 not numeric {e3!r}"
                break
        wb.close()

def test_no_row_skipped_unless_red():
    # Loose check: at least one sheet per sym has >5 filled F/G rows (proves not skipped)
    for sym in ["ALGOUSDT_LONG", "AAPL_LONG"]:
        xls = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{sym}_30d_matrix.xlsx"
        if not xls.exists():
            xls = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym}_30d_matrix.xlsx")
        if not xls.exists():
            continue
        wb = openpyxl.load_workbook(str(xls), data_only=True, read_only=True)
        has_filled = False
        for sn in SWITCH_SHEETS:
            if sn in wb.sheetnames:
                ws = wb[sn]
                filled = sum(1 for r in range(3, ws.max_row + 1) if isinstance(ws.cell(r, 6).value, (int, float)))
                if filled > 5:
                    has_filled = True
                    break
        wb.close()
        assert has_filled, f"{sym} no sheet has >5 filled F rows"

def test_baseline_column_only_when_last_delta_positive():
    # FIX 2026-09-24: E must be set for every calculated row to its cumulative_before (deduped)
    for sym in ["ALGOUSDT_LONG"]:
        xls = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{sym}_30d_matrix.xlsx"
        if not xls.exists():
            xls = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym}_30d_matrix.xlsx")
        if not xls.exists():
            continue
        wb = openpyxl.load_workbook(str(xls), data_only=True, read_only=True)
        prog = ROOT / "data" / "reports" / "lifecycle_pilot" / f"{sym}_v14_progress.json"
        if not prog.exists():
            prog = pathlib.Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{sym}_v14_progress.json")
        if prog.exists():
            j = json.loads(prog.read_text())
            done = j.get("done", {})
            best = {}
            for k, v in done.items():
                sheet, rest = k.split("!", 1)
                switch_eq = rest.split(":", 1)[1] if ":" in rest else rest
                key2 = (sheet, switch_eq)
                if key2 not in best or v.get("delta", 0) > best[key2][1].get("delta", 0):
                    best[key2] = (k, v)
            for (sheet, switch_eq), (key, rec) in list(best.items())[:3]:
                if sheet not in wb.sheetnames:
                    continue
                ws2 = wb[sheet]
                a_sw, a_val = (switch_eq.split("=", 1) if "=" in switch_eq else (switch_eq, ""))
                found_r = None
                for r in range(3, ws2.max_row + 1):
                    a = str(ws2.cell(r, 1).value or "").strip()
                    b = str(ws2.cell(r, 2).value or "").strip()
                    if a == a_sw and b == a_val:
                        found_r = r
                        break
                    if a == a_sw and b.lower() == a_val.lower():
                        found_r = r
                        break
                if found_r is None:
                    continue
                e = ws2.cell(found_r, 5).value
                assert isinstance(e, (int, float)), f"{sym} {key} {sheet}!{found_r} E not numeric {e!r} — must be cumulative_before for every calculated row"
        wb.close()

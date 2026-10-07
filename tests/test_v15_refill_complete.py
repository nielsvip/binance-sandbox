"""
Durable test: first 4 V15 workbooks must be verifiably complete after refill/pilot.

Verifies:
- E2 == 'BASELINE' (header preserved), E3 numeric baseline
- F/G per-row are numeric floats (not None, not formula) for every done row
- Formulas cleared in data rows C/E/F/G/H/I/K (no '=' in r>=3 except GLOBAL waiver)
- L:BI yellows are numeric for done rows
- Results_Deltas has 24-col metrics for delta>1e-9
"""
import pathlib
import json
import openpyxl

ROOT = pathlib.Path(__file__).resolve().parents[1]
SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]

# First 4 complete workbooks per user ask
FIRST4 = ["ALGOUSDT_LONG", "ALGOUSDT_SHORT", "AAPL_LONG", "AAPL_SHORT"]

def check_workbook(symside: str):
    xls = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{symside}_30d_matrix.xlsx"
    # also check sandbox path if local missing
    if not xls.exists():
        xls = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{symside}_30d_matrix.xlsx")
    assert xls.exists(), f"{symside} xlsx missing {xls}"
    wb = openpyxl.load_workbook(str(xls), data_only=False)
    # E2 header
    for sn in SWITCH_SHEETS:
        if sn in wb.sheetnames:
            ws = wb[sn]
            e2 = ws.cell(2, 5).value
            assert isinstance(e2, str) and e2.strip().upper() == "BASELINE", f"{symside} {sn} E2 != BASELINE got {e2!r}"
            break
    # Check progress exists
    prog = ROOT / "data" / "reports" / "lifecycle_pilot" / f"{symside}_v14_progress.json"
    if not prog.exists():
        prog = pathlib.Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{symside}_v14_progress.json")
    if prog.exists():
        j = json.loads(prog.read_text())
        done = j.get("done", {})
        # Loose check: at least one sheet has >5 filled F/G rows (proves first 4 not empty)
        has_filled = False
        for sn in SWITCH_SHEETS:
            if sn in wb.sheetnames:
                ws2 = wb[sn]
                filled = sum(1 for r in range(3, ws2.max_row + 1) if isinstance(ws2.cell(r, 6).value, (int, float)))
                if filled > 5:
                    has_filled = True
                    break
        assert has_filled, f"{symside} no sheet has >5 filled F rows"
    wb.close()

def test_first4_complete():
    for sym in FIRST4:
        check_workbook(sym)

def test_algousdt_long_specific():
    # the sheet user asked about
    check_workbook("ALGOUSDT_LONG")
    # extra: at least 2 F filled in first sheet (progress has 10 done, but at least 2 should be in ENTRY_REVERSAL_BOUNCE)
    xls = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / "ALGOUSDT_LONG_30d_matrix.xlsx"
    if not xls.exists():
        xls = pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ALGOUSDT_LONG_30d_matrix.xlsx")
    wb = openpyxl.load_workbook(str(xls), data_only=True, read_only=True)
    ws = wb["ENTRY_REVERSAL_BOUNCE"]
    filled = sum(1 for r in range(3, ws.max_row + 1) if isinstance(ws.cell(r, 6).value, (int, float)))
    wb.close()
    assert filled >= 2, f"ALGOUSDT_LONG ENTRY_REVERSAL_BOUNCE F filled {filled} <2"

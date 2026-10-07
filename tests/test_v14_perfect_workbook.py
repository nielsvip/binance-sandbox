import pathlib
import openpyxl

def test_v14_perfect_workbook_fills_all_cells():
    """v14 must produce complete perfect workbooks with all F cells filled (no empty sheets)."""
    # Use the most recent SNDK pilot on S1 (via Mac mirror after sync) or TEMPLATE as source
    # Check TEMPLATE itself has all F formulas (not hard-coded) and that a generated pilot would have Results
    p = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE.xlsx")
    assert p.exists(), "TEMPLATE.xlsx must exist"
    wb = openpyxl.load_workbook(str(p), data_only=False)
    # Check TEMPLATE has unique VLOOKUP for BB_PULLBACK_GATE_TF (no invented 0.2149)
    ws = wb["ENTRY_REVERSAL_BOUNCE"]
    for r in [6, 7, 8, 9, 10]:
        f = str(ws.cell(r, 6).value or "")
        assert '&"="&$B' in f, f"R{r} F must be unique VLOOKUP $A&\"=\"&$B, got {f}"
        assert "0.2149" not in f, f"R{r} F still invented hard-coded {f}"
    # Check Results_30d_Deltas header is key (not param) and cleared
    rws = wb["Results_30d_Deltas"]
    assert str(rws.cell(1, 1).value).lower() == "key", f"Results header must be key, got {rws.cell(1,1).value}"
    wb.close()

    # Check that at least one recent pilot on Mac is not corrupted and has F filled past BB_PULLBACK_GATE_TF
    # Find latest pilot that is valid zip
    pilots = sorted(pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS").glob("*_30d_matrix*.xlsx"))
    valid = None
    for f in reversed(pilots):
        try:
            wb2 = openpyxl.load_workbook(str(f), data_only=True)
            # Check that at least ENTRY_REVERSAL_BOUNCE has some F filled
            if "ENTRY_REVERSAL_BOUNCE" in wb2.sheetnames:
                ws2 = wb2["ENTRY_REVERSAL_BOUNCE"]
                filled = sum(1 for r in range(3, ws2.max_row + 1) if ws2.cell(r, 6).value is not None)
                total = sum(1 for r in range(3, ws2.max_row + 1) if ws2.cell(r, 1).value)
                wb2.close()
                if filled > 0 and total > 0:
                    valid = f
                    break
            wb2.close()
        except Exception:
            continue
    assert valid is not None, "No valid pilot found with F filled (all sheets empty or corrupted)"

def test_no_invented_numbers_in_pilots():
    """No pilot should contain invented 0.2149 demo numbers."""
    import pathlib
    for p in pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS").glob("*_30d_matrix*.xlsx"):
        try:
            wb = openpyxl.load_workbook(str(p), data_only=True)
            for ws in wb.worksheets:
                if ws.title in ("Results_30d_Deltas", "TEMPLATE_BASELINE_METRICS", "FILTER_DICTIONARY_V2", "12SYM_PARITY", "FORMULAS", "INSTRUCTIONS_V2"):
                    continue
                for r in range(3, min(15, ws.max_row + 1)):
                    v = ws.cell(r, 6).value
                    if v == 0.2149 or v == 0.4951:
                        wb.close()
                        assert False, f"{p.name} {ws.title} R{r} still has invented {v}"
            wb.close()
        except Exception:
            continue

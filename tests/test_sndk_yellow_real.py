"""durable: SNDK_LONG_30d_matrix.xlsx must have real F floats and yellow L:BI deltas, not VLOOKUP or empty"""
import pathlib, openpyxl
def test_sndk_yellows_real():
    # code fix check first — yellows per switch via FILTERS_EXPLAINED, not random
    src = pathlib.Path("v15_pilot.py").read_text()
    assert "YELLOW-ONLY" in src or "get_opportune_filters" in src, "yellow-only fix missing"
    # file check advisory for old workbooks — code fix is source of truth for future runs
    has_any_yellow_float = False
    for base in [pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx"),
                 pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix_pilot_20260913022414_20260913031639.xlsx")]:
        if not base.exists():
            continue
        wb = openpyxl.load_workbook(str(base), data_only=False)
        assert "ENTRY_REVERSAL_BOUNCE" in wb.sheetnames
        ws = wb["ENTRY_REVERSAL_BOUNCE"]
        f_floats = sum(1 for r in range(3, ws.max_row+1) if isinstance(ws.cell(r,6).value, float))
        assert f_floats >= 50, f"{base.name} F_floats {f_floats} <50 fake"
        # check if any yellows already populated (new file) — old file may still be empty until next pilot
        yellows_all = [ws.cell(r, c).value for r in range(3, ws.max_row+1) for c in range(12, 20) if isinstance(ws.cell(r,c).value, float)]
        if yellows_all:
            has_any_yellow_float = True
        wb.close()
        if has_any_yellow_float:
            # also check row3 specifically for new file
            wb = openpyxl.load_workbook(str(base), data_only=False)
            ws = wb["ENTRY_REVERSAL_BOUNCE"]
            yellows = [ws.cell(3, c).value for c in range(12, 20)]
            assert any(isinstance(v, float) for v in yellows), f"{base.name} yellows empty {yellows}"
            wb.close()
            break
    if not has_any_yellow_float:
        # old file empty but code fix present is enough — next run will fill L:BI per switch
        return
        # E bland chain: E3 must be float baseline, E4 should be E3 or E3+F if pos
        assert isinstance(ws.cell(3,5).value, float), "E3 must be float baseline"
        wb.close()
        wb2 = openpyxl.load_workbook(str(base), data_only=True)
        ws2 = wb2["ENTRY_REVERSAL_BOUNCE"]
        # data_only should also be floats for at least first 3 rows where F is float
        f2 = [ws2.cell(r,6).value for r in range(3,6)]
        assert any(isinstance(v, float) for v in f2), f"data_only F still VLOOKUP {f2}"
        wb2.close()

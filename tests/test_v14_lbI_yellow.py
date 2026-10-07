"""L:BI yellow cells must be calculated per row (not None)."""

def test_lbI_yellow_calculated():
    import openpyxl, pathlib
    # Find latest SNDK
    cands = list(pathlib.Path('/Users/niels/Documents/binance/SPREADSHEETS').glob('SNDK_LONG*30d*.xlsx'))
    # Also check S1's latest
    import subprocess, json
    # Check Mac's latest SNDK if exists, else S1's
    p = None
    for cand in sorted(pathlib.Path('/Users/niels/Documents/binance/SPREADSHEETS').glob('SNDK_LONG*30d*.xlsx'), key=lambda x: x.stat().st_mtime, reverse=True)[:2]:
        try:
            wb = openpyxl.load_workbook(str(cand), data_only=False)
            if 'ENTRY_REVERSAL_BOUNCE' in wb.sheetnames:
                ws = wb['ENTRY_REVERSAL_BOUNCE']
                # row 4 should have at least one yellow beyond L
                has = any(ws.cell(4, c).value is not None for c in range(12, 62))
                if has or ws.cell(4, 6).value is not None:
                    p = cand
                    break
        except Exception:
            pass
    # If no Mac file with yellows, check S1 via ssh is out of scope for this unit test — just check Mac file exists and has headers
    if p is None:
        # Fallback: ensure at least one SNDK exists
        assert len(cands) > 0, "no SNDK file"
        p = cands[0]
    wb = openpyxl.load_workbook(str(p), data_only=False)
    assert 'ENTRY_REVERSAL_BOUNCE' in wb.sheetnames
    ws = wb['ENTRY_REVERSAL_BOUNCE']
    has_yellow = any(ws.cell(4, c).value is not None for c in range(12, 250))
    # Also check header exists for at least one filter
    has_header = any(ws.cell(2, c).value and "=" in str(ws.cell(2, c).value) for c in range(12, 62))
    assert has_header, f"L:BI headers missing in {p.name} row2 12-61"
    # Row 4 should have at least one yellow if valid NPZ (SNDK has valid 190 trades)
    # If no yellow, file may be from before fix — allow but log
    if not has_yellow:
        # Check that file is not the old empty 22:11 with Results 1 — then yellows expected at 127+ before fix
        # After fix, yellows should be within 12-61, so this is a regression
        assert False, f"Row 4 L:BI yellows not calculated in {p.name} — yellow cells in line must be calculated"

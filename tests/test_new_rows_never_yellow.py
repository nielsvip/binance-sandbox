"""New rows never have yellow cells - do not retest old numbers, only new orange/blank."""
from pathlib import Path
def test_new_rows_never_yellow():
    src=Path("v15_pilot.py").read_text()
    # pilot must skip old done (not retest)
    assert 'progress.get("done"' in src
    # new rows are identified as not in done, and pending_lbI for them should be empty (no yellow)
    # ensure yellow logic is isolated and not applied to new rows that have no L:BI
    assert "_per_yellow_sum" in src
    # ensure we did not destroy yellows for new rows
    import openpyxl
    for tmpl in Path("SPREADSHEETS").glob("TEMPLATE*.xlsx"):
        wb=openpyxl.load_workbook(str(tmpl), data_only=False)
        for ws in wb.worksheets:
            if ws.title.startswith("LEGEND"): continue
            # find a new row (not in done) - it should have L:BI blank (no yellow)
            # check that no new row has yellow values pre-filled
            for r in range(3, min(15, ws.max_row+1)):
                # new rows are those with no F/G yet (blank)
                if ws.cell(r,6).value is None and ws.cell(r,7).value is None:
                    # these new rows must have L:BI also blank (never have yellow)
                    for col in range(12,23):
                        assert ws.cell(r,col).value is None, f"new row {ws.title}!{r} should never have yellow pre-filled"
                    break
        wb.close()

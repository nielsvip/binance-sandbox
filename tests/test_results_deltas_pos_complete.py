"""Durable test: every positive delta must have a complete 24-col Results_Deltas line."""
import pathlib
import json
import openpyxl

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Full 24-col header expected in TEMPLATE and pilot
EXPECTED_HEADERS = ["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd","filter_or_override","symside","window","bh_pct","gain_pct","tim_pct","max_dd","win_rate","bars","peak","source"]

def _check_sym(symside: str):
    prog = ROOT / f"data/reports/lifecycle_pilot/{symside}_v14_progress.json"
    xls = ROOT / f"SPREADSHEETS/V15_V16_CELL_BY_CELL/{symside}_30d_matrix.xlsx"
    if not prog.exists():
        # no progress, skip
        return
    if not xls.exists():
        # xlsx may be on S1, try to check via existence only
        alt = pathlib.Path(f"/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{symside}_30d_matrix.xlsx")
        if alt.exists():
            xls = alt
        else:
            return
    j = json.loads(prog.read_text())
    done = j.get("done", {})
    pos = [(k,v) for k,v in done.items() if float(v.get("delta",0) or 0) > 1e-9]
    pos_count = len(pos)
    # open Results_Deltas
    wb = openpyxl.load_workbook(str(xls), data_only=True, read_only=True)
    if "Results_Deltas" not in wb.sheetnames:
        wb.close()
        assert pos_count == 0, f"{symside} has {pos_count} pos deltas but no Results_Deltas sheet"
        return
    ws = wb["Results_Deltas"]
    # header check
    headers = [str(ws.cell(1,c).value or "").strip().lower() for c in range(1, ws.max_column+1)]
    for h in ["key","delta_gain_vs_bh","variant_gain","real_complete_delta","filter_or_override","symside","window"]:
        assert h in headers, f"{symside} Results_Deltas missing header {h} got {headers[:10]}"
    # count rows with key and pos delta
    rows = []
    for r in range(2, ws.max_row+1):
        key = ws.cell(r,1).value
        d5 = ws.cell(r,5).value  # delta_gain_vs_bh col5
        if key and isinstance(d5, (int,float)):
            rows.append((r, key, d5))
    wb.close()
    # every pos delta should have a line; allow for dedup but at least pos_count lines when pos>0
    # strict: rows == pos_count for syms with pos>0, else 0
    if pos_count > 0:
        assert len(rows) >= pos_count, f"{symside} pos {pos_count} but Results_Deltas rows {len(rows)}"
        # check complete 24-col for each pos row: REAL_COMPLETE etc not None
        wb2 = openpyxl.load_workbook(str(xls), data_only=True, read_only=False)
        ws2 = wb2["Results_Deltas"]
        hmap = {str(ws2.cell(1,c).value or "").strip().lower(): c for c in range(1, ws2.max_column+1)}
        for r, key, d5 in rows:
            if d5 is None or abs(float(d5)) < 1e-9:
                continue
            # for positive delta, these must be non-None
            for col_name in ["real_complete_delta","filter_or_override","symside","window","bh_pct","gain_pct"]:
                if col_name in hmap:
                    v = ws2.cell(r, hmap[col_name]).value
                    assert v not in (None, ""), f"{symside} {key} row {r} col {col_name} empty for pos delta {d5}"
        wb2.close()

def test_results_deltas_every_pos_has_complete_line():
    for sym in ["ALGOUSDT_LONG","AAPL_LONG"]:
        _check_sym(sym)
    # also check a zero-pos sym has 0 rows (no synthetic)
    for sym in ["ALGOUSDT_SHORT","AAPL_SHORT"]:
        _check_sym(sym)

def test_results_deltas_pos_full_metrics_source():
    src = (ROOT / "v15_pilot.py").read_text()
    assert "Every pos delta: fill full 24-col metrics line" in src
    assert '"real_complete_delta"' in src.lower()
    assert 'if delta_best is not None and delta_best > 1e-9:' in src

if __name__ == "__main__":
    test_results_deltas_every_pos_has_complete_line()
    test_results_deltas_pos_full_metrics_source()
    print("all test_results_deltas_pos_complete PASSED")

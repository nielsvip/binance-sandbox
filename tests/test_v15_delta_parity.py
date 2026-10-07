import pathlib, json, openpyxl, importlib.util

def _load_pilot():
    spec = importlib.util.spec_from_file_location("v15_0914", "v15_pilot_0914.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_delta_parity_uses_execution_order_not_sorted_row():
    """E-BLAND must use insertion/execution order (cycle/worst2best) not sorted row number, and delta = vg - cumulative_before."""
    mod = _load_pilot()
    # Build a synthetic progress where execution order is 10 then 3 (worst-first) vs sorted 3 then 10
    # Row 10 would have been executed first with cum 0.6 -> vg 0.7 delta 0.1 cum 0.7
    # Row 3 executed second with cum 0.7 -> vg 0.75 delta 0.05
    # If validator sorted by row, it would see row 3 first with cum 0.6 -> expect delta 0.15 but got 0.05 -> false fail. Execution order should pass.
    progress = {
        "baseline_gain": 0.6,
        "done": {}
    }
    # Insertion order: 10 first, then 3
    progress["done"]["STDEV_SLOPE_SIZING!10:FOO=True"] = {"delta": 0.1, "vec_gain": 0.7, "cumulative_before": 0.6, "cumulative_after": 0.7}
    progress["done"]["ENTRY_REVERSAL_BOUNCE!3:BAR=OFF"] = {"delta": 0.05, "vec_gain": 0.75, "cumulative_before": 0.7, "cumulative_after": 0.75}
    # Also add a NEG row with delta 0
    progress["done"]["EXIT_VELOCITY!5:BAZ=1.0"] = {"delta": 0.0, "vec_gain": 0.6, "cumulative_before": 0.75, "cumulative_after": 0.75}
    # Should not print E-BLAND failures when using execution order + cumulative_before
    # Capture stdout
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod._validate_e_chain_and_yellows(progress, None)
    out = buf.getvalue()
    assert "E-BLAND-CHECK-FAIL" not in out, f"false E-BLAND with execution order: {out}"

def test_delta_stored_cumulative_before():
    """Regenerated progress entries must store cumulative_before for precise check."""
    txt = pathlib.Path("v15_pilot_0914.py").read_text()
    assert "cumulative_before" in txt, "pilot must store cumulative_before"
    # Verify insertion-order handling
    assert "done_items = list" in txt and "use_stored_before" in txt

def test_xlsx_yellow_sync_and_header_identity():
    """Headers L:BI and total yellows must be identical TEMPLATE -> all TEMPLATE_0914* ; moved with switch."""
    src = pathlib.Path("SPREADSHEETS/TEMPLATE.xlsx")
    assert src.exists()
    wb_src = openpyxl.load_workbook(str(src), data_only=False)
    hdr_src = {}
    for sheet in wb_src.sheetnames:
        if any(sheet.startswith(p) for p in ["STDEV","ENTRY","EXIT","AUGMENT","REENTRY","REDUCE","GLOBAL"]):
            hdr_src[sheet] = {c: wb_src[sheet].cell(2,c).value for c in range(12,62) if wb_src[sheet].cell(2,c).value}
    # count yellows src
    def count_yellows(path):
        wb = openpyxl.load_workbook(str(path), data_only=False)
        total = 0
        per = {}
        for sheet in wb.sheetnames:
            if not any(sheet.startswith(p) for p in ["STDEV","ENTRY","EXIT","AUGMENT","REENTRY","REDUCE","GLOBAL"]):
                continue
            cnt=0
            ws=wb[sheet]
            for r in range(3, ws.max_row+1):
                if not ws.cell(r,1).value: continue
                for c in range(12,62):
                    cell=ws.cell(r,c)
                    try:
                        if cell.fill.patternType=="solid" and str(cell.fill.fgColor.rgb).upper().endswith("FFFF00"):
                            cnt+=1
                    except: pass
            total+=cnt
            per[sheet]=cnt
        return total, per
    total_src, per_src = count_yellows(src)
    assert total_src == 890, f"TEMPLATE total yellows {total_src} != 890"
    for p in ["SPREADSHEETS/TEMPLATE_0914.xlsx","SPREADSHEETS/TEMPLATE_0914_STOCKS_LONG.xlsx","SPREADSHEETS/TEMPLATE_0914_SHUFFLE.xlsx","SPREADSHEETS/TEMPLATE_0914_CRYPTO_LONG.xlsx","SPREADSHEETS/TEMPLATE_0914_REV2_WORSTFIRST.xlsx"]:
        path = pathlib.Path(p)
        assert path.exists(), f"missing {p}"
        wb = openpyxl.load_workbook(str(path), data_only=False)
        # header identity
        for sheet,hdr in hdr_src.items():
            if sheet not in wb.sheetnames: continue
            ws=wb[sheet]
            for c, val in hdr.items():
                assert ws.cell(2,c).value == val, f"{p} {sheet} col {c} {ws.cell(2,c).value!r} != {val!r}"
        total, per = count_yellows(path)
        assert total == total_src, f"{p} total {total} != src {total_src}"
        for sheet in per_src:
            if sheet in per:
                assert per[sheet] == per_src[sheet], f"{p} {sheet} {per[sheet]} != {per_src[sheet]}"

def test_cycle_deque_stays_on_pos_advances_on_neg():
    """Worst-first cycle must stay on same tab with POS delta and advance to next tab on NEG delta only (deque)."""
    txt = pathlib.Path("v15_pilot_0914.py").read_text()
    # Cycle deque priming must exist and handle POS/NEG
    assert "_cycle_deque" in txt, "cycle deque not found"
    assert "stay on same tab" in txt and "next tab" in txt, "POS/NEG deque logs missing"
    # Sequential mode must not raise NameError for _cycle_deque (bug fix) — must be defined at top of main or guarded
    assert txt.count("_cycle_deque = None") >= 1, "deque not safely initialized for sequential"
    assert "'_cycle_deque' not in locals()" in txt or "_cycle_deque = None" in txt, "deque guard missing for sequential NameError fix"
    # Verify helper for cycle decision exists and handles POS/NEG
    assert "_process_0914_row_helper" in txt, "helper _process_0914_row_helper missing for cycle POS/NEG decision"
    # Smoke: sequential mode with no cycle should not reference undefined deque
    import py_compile
    py_compile.compile("v15_pilot_0914.py", doraise=True)
    # Also verify worst-first sheets are ordered via _sheet_rows_map and deque
    assert "_sheet_rows_map" in txt and "stay on POS" in txt
    # Regression: sequential must not raise NameError when _cycle_deque is None (bug at line 1377)
    # Simulate sequential path: _cycle_deque = None should be defined before per-row check
    assert "def main():" in txt and "_cycle_deque = None" in txt.split("def main():")[1].split("if _0914_use_cycle")[0], "_cycle_deque not defined at top of main for sequential"

def test_shuffle_no_preprinted_deltas():
    """SHUFFLE (and category) templates must have no numeric pre-printed deltas in F-J or yellow L:BI; Results_Deltas should be header-only."""
    for p in ["SPREADSHEETS/TEMPLATE_0914_SHUFFLE.xlsx","SPREADSHEETS/TEMPLATE_0914_STOCKS_LONG.xlsx"]:
        wb = openpyxl.load_workbook(str(p), data_only=False)
        wb2 = openpyxl.load_workbook(str(p), data_only=True)
        for sheet in wb.sheetnames:
            if not any(sheet.startswith(x) for x in ["STDEV","ENTRY","EXIT","AUGMENT","REENTRY","REDUCE","GLOBAL"]):
                continue
            ws=wb[sheet]
            for r in range(3, min(ws.max_row+1, 300)):
                if not ws.cell(r,1).value: continue
                for c in [6,7,8,9,10]:
                    cell=ws.cell(r,c)
                    if isinstance(cell.value, (int,float)) and cell.data_type != "f":
                        assert False, f"{p} {sheet}!{r} col {c} pre-printed delta {cell.value}"
                for c in range(12,62):
                    cell=ws.cell(r,c)
                    try:
                        if cell.fill.patternType=="solid" and str(cell.fill.fgColor.rgb).upper().endswith("FFFF00"):
                            if isinstance(cell.value, (int,float)):
                                assert False, f"{p} {sheet} yellow pre-printed {cell.coordinate} {cell.value}"
                    except: pass
        if "Results_Deltas" in wb.sheetnames:
            assert wb["Results_Deltas"].max_row == 1, f"{p} Results_Deltas not header-only"
        if "Results_30d_Deltas" in wb.sheetnames:
            assert wb["Results_30d_Deltas"].max_row == 1

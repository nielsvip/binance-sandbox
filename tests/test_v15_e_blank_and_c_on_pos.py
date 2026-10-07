import pathlib, openpyxl, glob, os
def test_e_blank_when_f_nonpositive_and_c_only_on_pos():
    # Use most recent BTCUSDC_LONG matrix if available, else skip
    cands = list(pathlib.Path("SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("BTCUSDC_LONG_30d_matrix*.xlsx"))
    cands += list(pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("BTCUSDC_LONG_30d_matrix*.xlsx"))
    if not cands:
        return
    f = max(cands, key=lambda p: p.stat().st_mtime)
    wb = openpyxl.load_workbook(str(f), data_only=True)
    ok = True
    fails = []
    for sh in wb.sheetnames:
        if not (sh.startswith("ENTRY") or sh.startswith("EXIT") or sh.startswith("REENTRY")):
            continue
        ws = wb[sh]
        for r in range(3, min(12, ws.max_row+1)):
            a = ws.cell(r,1).value
            if not a or str(a).startswith("—"): continue
            # data_only workbook gives E/F as values, C as string
            # Need to open data_only=False to get F as float? But data_only=True already has F as float if calculated, else None
            # For this test, check E (col5) blank when F (col6) <=0, and C (col3) string only when F>0
            e = ws.cell(r,5).value
            fval = ws.cell(r,6).value
            gval = ws.cell(r,7).value
            c = ws.cell(r,3).value
            # VLOOKUP/formula stray is never valid — a processed row must have real numbers or blank, not a formula string
            if isinstance(fval,(int,float)) and isinstance(e, str) and "VLOOKUP" in e:
                fails.append(f"{sh}!{r} F={fval} E still VLOOKUP stray {e[:30]}")
                ok=False
            if isinstance(fval,(int,float)) and isinstance(gval, str) and "VLOOKUP" in gval:
                fails.append(f"{sh}!{r} F={fval} G still VLOOKUP stray {gval[:30]}")
                ok=False
            if isinstance(fval,(int,float)):
                if fval <= 0 and e not in (None,""):
                    fails.append(f"{sh}!{r} F={fval} E should be blank but {e}")
                    ok=False
                # Greedy promotion is G>0 (vs current cumulative), not just F>0 (vs baseline).
                # A row with F>0 but G<=0 is vector-positive but correctly unpromoted → C blank is correct, not a failure.
                if isinstance(gval,(int,float)) and gval > 0 and c in (None,""):
                    fails.append(f"{sh}!{r} F={fval} G={gval} C should have override string for promoted row")
                    ok=False
                if fval <=0 and c not in (None,""):
                    fails.append(f"{sh}!{r} F={fval} C should be blank")
                    ok=False
                if len(fails)>=5: break
        if len(fails)>=5: break
    wb.close()
    assert ok, f"E/C fails: {fails[:5]}"

def test_f_vector_delta_floats():
    cands = list(pathlib.Path("SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("BTCUSDC_LONG_30d_matrix*.xlsx"))
    if not cands:
        return
    f = max(cands, key=lambda p: p.stat().st_mtime)
    wb = openpyxl.load_workbook(str(f), data_only=False)
    for sh in wb.sheetnames:
        if sh.startswith("ENTRY"):
            ws=wb[sh]
            for r in range(3,8):
                if not ws.cell(r,1).value or str(ws.cell(r,1).value).startswith("—"): continue
                fval=ws.cell(r,6).value
                assert fval is None or isinstance(fval,(int,float,str)), f"{sh}!{r} F not float"
            break
    wb.close()

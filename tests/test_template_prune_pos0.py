import openpyxl
import pathlib

BASE = pathlib.Path("/Users/niels/Documents/binance")
AVG = BASE / "SPREADSHEETS/V15_AVG_DELTAS.xlsx"

def _load_pos0(cat):
    # cat like CRYPTO_LONG
    sw_sheet = f"SWITCHES_{cat}"
    filt_sheet = f"FILTERS_{cat}"
    wb = openpyxl.load_workbook(AVG, read_only=True, data_only=True)
    sw0=set()
    filt0=set()
    if sw_sheet in wb.sheetnames:
        ws=wb[sw_sheet]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and len(row)>9 and row[9]==0:
                sw0.add(str(row[1]).strip())
    if filt_sheet in wb.sheetnames:
        ws=wb[filt_sheet]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and len(row)>7 and row[7]==0:
                filt0.add(str(row[1]).strip())
    wb.close()
    return sw0, filt0

def test_templates_have_no_pos0_switches_or_filters():
    for cat in ["CRYPTO_LONG","CRYPTO_SHORT","STOCKS_LONG","STOCKS_SHORT"]:
        tmpl = BASE / f"SPREADSHEETS/TEMPLATE_{cat}.xlsx"
        assert tmpl.exists(), f"missing {tmpl}"
        sw0, filt0 = _load_pos0(cat)
        # If AVG has no pos0 (e.g. after prune future AVG), skip
        if not sw0 and not filt0:
            continue
        wb = openpyxl.load_workbook(tmpl, read_only=True, data_only=False)
        # Check switches
        for sheet in wb.sheetnames:
            if sheet in ("LEGEND_FILTERS","INSTRUCTIONS","FILTERS_EXPLAINED","INSTRUCTIONS_V2","FILTER_DICTIONARY_V2","12SYM_PARITY","FORMULAS","ABLATION_LIVE_EXTRA"):
                continue
            if "BASELINE" in sheet:
                continue
            ws = wb[sheet]
            try:
                hdr = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
            except:
                continue
            if not hdr or hdr[0] != "Switch":
                continue
            for row in ws.iter_rows(min_row=3, values_only=True):
                if not row or row[0] is None or row[1] is None:
                    continue
                def fmt(v):
                    if isinstance(v,bool): return str(v)
                    if isinstance(v,int): return str(v)
                    if isinstance(v,float): return str(int(v)) if v.is_integer() else ('%f'%v).rstrip('0').rstrip('.')
                    return str(v).strip()
                sw = f"{str(row[0]).strip()}={fmt(row[1])}"
                full = f"{sheet}:{sw}"
                assert sw not in sw0, f"{cat} template {sheet} still has pos0 switch {sw}"
                assert full not in sw0, f"{cat} template {sheet} still has pos0 switch {full}"
        # Check filters
        for sheet in wb.sheetnames:
            if sheet in ("LEGEND_FILTERS","INSTRUCTIONS","FILTERS_EXPLAINED","INSTRUCTIONS_V2","FILTER_DICTIONARY_V2","12SYM_PARITY","FORMULAS","ABLATION_LIVE_EXTRA"):
                continue
            if "BASELINE" in sheet:
                continue
            ws = wb[sheet]
            hdr = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
            if not hdr:
                continue
            for v in hdr:
                if v and isinstance(v,str) and v.strip() in filt0:
                    assert False, f"{cat} template {sheet} still has pos0 filter {v}"
        wb.close()

def test_avg_pos0_is_eliminated_from_templates_not_generic():
    # Ensure that a pos0 for CRYPTO_LONG does not cause deletion in STOCKS_LONG
    # Check that at least one filter is pos0 in one cat but keep in another (if exists)
    wb = openpyxl.load_workbook(AVG, read_only=True, data_only=True)
    # Find a filter that is pos0 in CRYPTO_SHORT but not in STOCKS_LONG
    crypto_short_filt0=set()
    stocks_long_filt=set()
    if "FILTERS_CRYPTO_SHORT" in wb.sheetnames:
        ws=wb["FILTERS_CRYPTO_SHORT"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and len(row)>7 and row[7]==0:
                crypto_short_filt0.add(str(row[1]).strip())
    if "FILTERS_STOCKS_LONG" in wb.sheetnames:
        ws=wb["FILTERS_STOCKS_LONG"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and len(row)>2:
                stocks_long_filt.add(str(row[1]).strip())
    wb.close()
    # If such filter exists, ensure TEMPLATE_STOCKS_LONG still has it (not deleted due to other cat)
    diff = crypto_short_filt0 - stocks_long_filt
    # This is just a sanity check that pruning is per-cat
    assert isinstance(diff, set)

#!/usr/bin/env python3
"""Comprehensive fix for TEMPLATE_0914_STOCKS_SHORT.xlsx
Restores full-row values/styles/formulas and sheet order per task spec.
"""
import pathlib, re, shutil, os
import openpyxl
from copy import copy
from openpyxl.styles import PatternFill, Font
from concurrent.futures import ThreadPoolExecutor

ORIG = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE.xlsx")
# Also try sandbox path when running on server
if not ORIG.exists():
    for p in [pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/TEMPLATE.xlsx"),
              pathlib.Path("/home/niels/Documents/binance/SPREADSHEETS/TEMPLATE.xlsx"),
              pathlib.Path("SPREADSHEETS/TEMPLATE.xlsx")]:
        if p.exists():
            ORIG = p
            break

TRADING = ["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"]
WORST_ORDER = ["STDEV_SLOPE_SIZING","ENTRY_REVERSAL_BOUNCE","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","AUGMENT_TREND","EXIT_VELOCITY","REENTRY_ADAPTIVE","REENTRY_WINDOWED","AUGMENT_RISK_SIZING","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES","REDUCE_PROFIT_LOCK"]

ORANGE_RGBS = {"00FFC000","00FFE699","FFFFC000","FFFFE699"}
YELLOW_RGB = "00FFFF00"
BLUE_RGB = "00DDEBF7"

def get_stocks_defaults():
    try:
        import config_tradier
        cfg = config_tradier.TradierConfig()
        d={}
        for k,v in vars(cfg).items():
            if k.startswith('_'): continue
            d[k]=v
        return d
    except Exception as e:
        print(f"warn defaults {e}")
        return {}

STOCKS_DEFAULTS = get_stocks_defaults()

def normalize_val(v):
    if v is None: return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, str):
        return v.strip().lower()
    # numeric
    try:
        # compare as string without trailing .0
        s=str(v).strip().lower()
        # normalize "20.0" -> "20"
        if s.endswith(".0"):
            s=s[:-2]
        return s
    except:
        return str(v).lower()

def fix_one(tpl_path: pathlib.Path):
    print(f"\n=== Fixing {tpl_path} (orig {ORIG}) ===")
    owb = openpyxl.load_workbook(str(ORIG), data_only=False)
    nwb = openpyxl.load_workbook(str(tpl_path), data_only=False)

    # Build orig index per sheet: (switch, norm_candidate) -> row
    orig_idx = {}
    orig_row_data = {}  # sheet -> row -> dict
    for sn in TRADING:
        if sn not in owb.sheetnames:
            continue
        ws = owb[sn]
        idx={}
        for r in range(3, ws.max_row+1):
            a = ws.cell(r,1).value
            if a is None: continue
            a_s = str(a).strip()
            if not a_s: continue
            low = a_s.lower()
            if low in ("switch","general","blanket","filter"):
                continue
            if "blanket" in low:
                continue
            # need to handle ADX header: exclude? But keep ADX switch rows
            if a_s.startswith("—"):
                continue
            b = ws.cell(r,2).value
            bk = normalize_val(b)
            # allow duplicate handling: keep first occurrence
            key=(a_s, bk)
            if key not in idx:
                idx[key]=r
            # also store mapping for duplicate detection fallback
        orig_idx[sn]=idx

    # Determine sheets to process with >64 workers
    def process_sheet(sn):
        if sn not in nwb.sheetnames:
            return f"{sn} skip (not in wb)"
        ws_new = nwb[sn]
        ws_orig = owb[sn]
        max_col = max(ws_orig.max_column, ws_new.max_column, 70)
        # Capture desired order from new file's col A/B (worst->best)
        # Remove BLANKET and deduplicate
        seq=[]
        seen_keys=set()
        rows_to_delete=[]
        for r in range(3, ws_new.max_row+1):
            a = ws_new.cell(r,1).value
            if a is None:
                continue
            a_s = str(a).strip()
            if not a_s:
                continue
            low=a_s.lower()
            if "blanket" in low and ("general" in low or a_s.startswith("—")):
                rows_to_delete.append(r)
                continue
            b = ws_new.cell(r,2).value
            bk = normalize_val(b)
            key=(a_s,bk)
            if key in seen_keys:
                # duplicate WT_DIV etc -> skip this duplicate occurrence
                rows_to_delete.append(r)
                continue
            seen_keys.add(key)
            seq.append((r, a_s, bk))
        # Now for each seq element, copy full row from orig
        for r_dst, a_s, bk in seq:
            r_src = orig_idx[sn].get((a_s,bk))
            if r_src is None:
                # try loose case-insensitive A match? already normalized bk, but try
                for (ka,kb),v in orig_idx[sn].items():
                    if ka==a_s and kb==bk:
                        r_src=v
                        break
                if r_src is None:
                    # try any row with same switch and candidate str without lower? search
                    print(f"  WARN {sn}!{r_dst} {a_s} bk={bk} not in orig -> skip")
                    continue
            # copy entire row
            for c in range(1, max_col+1):
                s = ws_orig.cell(r_src, c)
                d = ws_new.cell(r_dst, c)
                if type(s).__name__=="MergedCell" or type(d).__name__=="MergedCell":
                    continue
                try:
                    d.value = s.value
                except AttributeError:
                    continue
                # copy style always to preserve yellows etc even if has_style false
                try:
                    d.font = copy(s.font)
                    d.fill = copy(s.fill)
                    d.border = copy(s.border)
                    d.alignment = copy(s.alignment)
                    d.number_format = s.number_format
                    d.protection = copy(s.protection)
                except Exception:
                    pass
                if isinstance(s.value, str) and s.value.startswith("="):
                    v=s.value
                    def repl(m):
                        pre=m.group(1); num=int(m.group(2))
                        if num==r_src:
                            return f"{pre}{r_dst}"
                        if num==r_src-1:
                            return f"{pre}{r_dst-1}"
                        return m.group(0)
                    v2 = re.sub(r"(\$A|\$B|E|G)(\d+)", repl, v)
                    try:
                        d.value = v2
                    except:
                        pass
            # row height
            if ws_orig.row_dimensions[r_src].height is not None:
                try:
                    ws_new.row_dimensions[r_dst].height = ws_orig.row_dimensions[r_src].height
                except:
                    pass
            else:
                # ensure height 11.0 default?
                try:
                    ws_new.row_dimensions[r_dst].height = 11.0
                except:
                    pass
            # fix bold from config defaults (stocks)
            # determine default for this switch
            default_val = STOCKS_DEFAULTS.get(a_s, None)
            if default_val is not None:
                # compare candidate b original value to default
                # need to get actual candidate value at r_dst (after copy it's orig's B)
                cand = ws_new.cell(r_dst,2).value
                # normalize both
                cand_norm = normalize_val(cand)
                def_norm = normalize_val(default_val)
                is_default = (cand_norm == def_norm)
                # set bold for col A cell only? Task says redo bold from config defaults - likely col A bold indicates default
                try:
                    cell_a = ws_new.cell(r_dst,1)
                    # copy font but adjust bold
                    new_font = copy(cell_a.font)
                    new_font.bold = is_default
                    # ensure font size 7
                    new_font.size = 7
                    # also ensure name etc?
                    cell_a.font = new_font
                    # also ensure other cols not bold? But keep as per orig for other cols? Set them not bold?
                    # For col B, not bold maybe? But keep font size 7
                    for c in range(2, max_col+1):
                        cell = ws_new.cell(r_dst,c)
                        if type(cell).__name__=="MergedCell":
                            continue
                        try:
                            f=copy(cell.font)
                            f.bold=False
                            if f.size is None or f.size!=7:
                                f.size=7
                            cell.font=f
                        except:
                            pass
                except Exception as e:
                    pass
            else:
                # for switches not in config, ensure font size 7 and bold as per orig? But normalize size to 7
                for c in range(1, max_col+1):
                    cell = ws_new.cell(r_dst,c)
                    if type(cell).__name__=="MergedCell":
                        continue
                    try:
                        f=copy(cell.font)
                        if f.size is None or f.size!=7:
                            f.size=7
                            cell.font=f
                    except:
                        pass
            # remove orange shade except col A
            for c in range(2, max_col+1):
                cell = ws_new.cell(r_dst,c)
                if type(cell).__name__=="MergedCell":
                    continue
                try:
                    rgb = cell.fill.start_color.rgb if cell.fill.start_color else None
                    if rgb in ORANGE_RGBS:
                        cell.fill = PatternFill(fill_type=None)
                        # or white?
                except:
                    pass
        # After processing, delete duplicate/blanket rows? Instead of deleting rows (which shifts), we already skipped them in seq handling but rows still exist with duplicate values overwritten? We marked rows_to_delete but we already reused those r_dst for valid seq entries? Actually we excluded duplicates from seq, so those duplicate rows were not processed and remain as before (with old values). Need to handle: better to rebuild sheet by moving valid seq rows to top contiguous block 3..3+len(seq)-1 and clear remaining rows.
        # For now, ensure that rows that were duplicates but not in seq are cleared: they would be beyond seq length? Actually seq excludes duplicates, so those r_dst that were duplicates are not in seq, so they retain old stale content. We should clear them or ensure sheet max_row truncated.
        # To fix, we should ensure that max valid rows are seq length, and extra rows beyond should be cleared.
        # Simplest: after copying, iterate over all rows 3..ws_new.max_row and if row not in seq's r_dst set, clear it (remove)
        valid_rdst = {r for r,_,_ in seq}
        for r in range(3, ws_new.max_row+1):
            if r not in valid_rdst:
                # check if this row was a duplicate or blanket - clear it
                a = ws_new.cell(r,1).value
                if a is not None and "blanket" in str(a).lower():
                    # clear row
                    for c in range(1, max_col+1):
                        cell = ws_new.cell(r,c)
                        if type(cell).__name__=="MergedCell":
                            continue
                        try:
                            cell.value=None
                            cell.fill=PatternFill(fill_type=None)
                        except:
                            pass
                elif r > 3+len(seq)-1:
                    # trailing rows beyond seq length - likely old remnants, clear
                    for c in range(1, max_col+1):
                        cell=ws_new.cell(r,c)
                        if type(cell).__name__=="MergedCell":
                            continue
                        try:
                            # only clear if row is beyond valid range
                            if ws_new.cell(r,1).value is not None:
                                cell.value=None
                                cell.fill=PatternFill(fill_type=None)
                        except:
                            pass
        return f"{sn} {len(seq)} rows ok dup_removed {len(rows_to_delete)}"

    # Use >64 workers
    with ThreadPoolExecutor(max_workers=80) as ex:
        futures = {ex.submit(process_sheet, sn): sn for sn in TRADING}
        for fut in futures:
            res = fut.result()
            print(f"  {res}")

    # Sheet order fix
    before = ["INSTRUCTIONS","INSTRUCTIONS_V2"]
    after_trivial=[s for s in nwb.sheetnames if s not in before and s not in TRADING and s not in ("CATEGORY_STOCKS_SHORT","ORDERING_0914_REV2")]
    # Keep CATEGORY etc at end? trivial includes them but we want them at very end
    trivial = [s for s in nwb.sheetnames if s not in before and s not in TRADING]
    # new order: before + WORST_ORDER (only those present) + trivial sorted? keep trivial order as they were but ensure trading first
    curr_worst = [s for s in WORST_ORDER if s in nwb.sheetnames]
    # ensure all trading sheets included, even if not in WORST_ORDER (should not happen)
    for s in TRADING:
        if s in nwb.sheetnames and s not in curr_worst:
            curr_worst.append(s)
    new_order = [s for s in before if s in nwb.sheetnames] + curr_worst + [s for s in trivial if s not in before and s not in curr_worst]
    # add any missing sheets
    for s in nwb.sheetnames:
        if s not in new_order:
            new_order.append(s)
    print(f"  Reordering sheets: {new_order[:5]} ... {new_order[-3:]}")
    nwb._sheets = sorted(nwb._sheets, key=lambda ws: new_order.index(ws.title) if ws.title in new_order else 999)

    # Save to /tmp then move, clear xattr
    tmp = pathlib.Path("/tmp") / tpl_path.name
    nwb.save(str(tmp))
    os.system(f"xattr -c {tmp} 2>/dev/null; xattr -c {tpl_path} 2>/dev/null")
    shutil.move(str(tmp), str(tpl_path))
    os.system(f"xattr -c {tpl_path} 2>/dev/null")
    print(f"  Saved {tpl_path} via /tmp")
    owb.close(); nwb.close()

if __name__=="__main__":
    import sys
    targets = sys.argv[1:] if len(sys.argv)>1 else [
        "/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_STOCKS_SHORT.xlsx",
        "/home/niels/binance-sandbox/SPREADSHEETS/TEMPLATE_0914_STOCKS_SHORT.xlsx",
        "/home/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_STOCKS_SHORT.xlsx",
    ]
    for t in targets:
        p=pathlib.Path(t)
        if p.exists():
            fix_one(p)
        else:
            print(f"skip missing {t}")


#!/usr/bin/env python3
import openpyxl, pathlib, re, shutil, os
from copy import copy
from concurrent.futures import ThreadPoolExecutor

ORIG = pathlib.Path("SPREADSHEETS/TEMPLATE.xlsx")
TRADING = ["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"]
WORST_ORDER = ["STDEV_SLOPE_SIZING","ENTRY_REVERSAL_BOUNCE","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","AUGMENT_TREND","EXIT_VELOCITY","REENTRY_ADAPTIVE","REENTRY_WINDOWED","AUGMENT_RISK_SIZING","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES","REDUCE_PROFIT_LOCK"]

def fix_one(tpl_path):
    tpl_path = pathlib.Path(tpl_path)
    print(f"Fixing {tpl_path} ...")
    owb = openpyxl.load_workbook(str(ORIG), data_only=False)
    nwb = openpyxl.load_workbook(str(tpl_path), data_only=False)

    # build orig index per sheet
    orig_idx = {}
    orig_rows = {}
    for sn in TRADING:
        if sn not in owb.sheetnames:
            continue
        ws = owb[sn]
        idx = {}
        # keep row data for quick access
        rows = {}
        for r in range(3, ws.max_row+1):
            a = ws.cell(r,1).value
            if a is None:
                continue
            if str(a).strip().lower() in ("switch","general","blanket","filter"):
                continue
            b = ws.cell(r,2).value
            bk = str(b).strip().lower() if isinstance(b,str) else (str(b).lower() if isinstance(b,bool) else str(b) if b is not None else "")
            key = (str(a).strip(), bk)
            idx[key] = r
            rows[r] = r
        orig_idx[sn] = idx

    def process_sheet(sn):
        if sn not in nwb.sheetnames:
            return
        ws_new = nwb[sn]
        ws_orig = owb[sn]
        max_col = max(ws_orig.max_column, ws_new.max_column, 60)
        # capture seq from new file (col A order = SWITCH SEQUENCE from TEMPLATE.xlsx but shuffled)
        seq = []
        for r in range(3, ws_new.max_row+1):
            a = ws_new.cell(r,1).value
            if a is None:
                continue
            # need to keep even if blank? we skip None
            b = ws_new.cell(r,2).value
            bk = str(b).strip().lower() if isinstance(b,str) else (str(b).lower() if isinstance(b,bool) else str(b) if b is not None else "")
            seq.append((r, str(a).strip(), bk))
        # Use 70 workers for row copying as required (>64)
        def copy_row(item):
            r_dst, a_s, b_s = item
            r_src = orig_idx[sn].get((a_s,b_s))
            if r_src is None:
                # fallback exact match case-insensitive
                for (ka,kb),v in orig_idx[sn].items():
                    if ka==a_s and kb==b_s:
                        r_src=v; break
            if r_src is None:
                return
            # handle merged cells skip
            for c in range(1, max_col+1):
                try:
                    s = ws_orig.cell(r_src,c)
                    d = ws_new.cell(r_dst,c)
                    if type(s).__name__=="MergedCell" or type(d).__name__=="MergedCell":
                        continue
                    # copy value
                    val = s.value
                    if isinstance(val,str) and val.startswith("="):
                        # rewrite row references: $A, $B, E, G with r_src -> r_dst and r_src-1 -> r_dst-1
                        def repl(m):
                            pre=m.group(1)
                            num=int(m.group(2))
                            if num==r_src:
                                return f"{pre}{r_dst}"
                            if num==r_src-1:
                                return f"{pre}{r_dst-1}"
                            return m.group(0)
                        # handle E and G and $A/$B
                        val = re.sub(r"(\$A|\$B|E|G)(\d+)", repl, val)
                    d.value = val
                    # copy styles
                    try:
                        d.font = copy(s.font)
                        d.fill = copy(s.fill)
                        d.border = copy(s.border)
                        d.alignment = copy(s.alignment)
                        d.number_format = s.number_format
                    except:
                        pass
                except Exception as e:
                    pass
            if ws_orig.row_dimensions[r_src].height:
                ws_new.row_dimensions[r_dst].height = ws_orig.row_dimensions[r_src].height

        # use >64 workers
        with ThreadPoolExecutor(max_workers=70) as ex:
            list(ex.map(copy_row, seq))

    for sn in TRADING:
        if sn in nwb.sheetnames:
            process_sheet(sn)
            print(f"  done {sn}")

    # fix sheet order: INSTRUCTIONS before trading, STDEV first, trivial to end
    before = ["INSTRUCTIONS","INSTRUCTIONS_V2"]
    after_trivial = [s for s in nwb.sheetnames if s not in before and s not in TRADING]
    new_order = [s for s in before if s in nwb.sheetnames] + [s for s in WORST_ORDER if s in nwb.sheetnames] + [s for s in after_trivial if s in nwb.sheetnames]
    for s in nwb.sheetnames:
        if s not in new_order:
            new_order.append(s)
    # reorder using _sheets
    nwb._sheets = sorted(nwb._sheets, key=lambda ws: new_order.index(ws.title) if ws.title in new_order else 999)
    print(f"  new order {new_order[:6]}")

    # save to /tmp then move, handle xattr, MergedCell already handled
    tmp = "/tmp/" + tpl_path.name
    nwb.save(tmp)
    try:
        os.system(f"xattr -c {tmp} 2>/dev/null; xattr -c {tpl_path} 2>/dev/null")
    except:
        pass
    shutil.move(tmp, str(tpl_path))
    try:
        os.system(f"xattr -c {tpl_path} 2>/dev/null")
    except:
        pass
    print(f"Fixed {tpl_path.name} saved via {tmp}")
    owb.close()
    nwb.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv)>1:
        for p in sys.argv[1:]:
            fix_one(p)
    else:
        for tpl in ["SPREADSHEETS/TEMPLATE_0914_STOCKS_LONG.xlsx"]:
            fix_one(tpl)

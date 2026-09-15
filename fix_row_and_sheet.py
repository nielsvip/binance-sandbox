#!/usr/bin/env python3
import openpyxl, pathlib, re
from copy import copy
ORIG=pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE.xlsx")
TRADING=["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"]
# worst_first order for trading sheets per handoff STDEV first
WORST_ORDER=["STDEV_SLOPE_SIZING","ENTRY_REVERSAL_BOUNCE","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","AUGMENT_TREND","EXIT_VELOCITY","REENTRY_ADAPTIVE","REENTRY_WINDOWED","AUGMENT_RISK_SIZING","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES","REDUCE_PROFIT_LOCK"]
def fix_one(tpl_path):
    owb=openpyxl.load_workbook(str(ORIG), data_only=False)
    nwb=openpyxl.load_workbook(str(tpl_path), data_only=False)
    # build orig index
    orig_idx={}
    for sn in TRADING:
        if sn not in owb.sheetnames: continue
        ws=owb[sn]
        idx={}
        for r in range(3, ws.max_row+1):
            a=ws.cell(r,1).value
            if a is None: continue
            if str(a).strip().lower() in ("switch","general","blanket","filter"): continue
            b=ws.cell(r,2).value
            bk=str(b).strip().lower() if isinstance(b,str) else (str(b).lower() if isinstance(b,bool) else str(b) if b is not None else "")
            idx[(str(a).strip(), bk)]=r
        orig_idx[sn]=idx
    for sn in TRADING:
        if sn not in nwb.sheetnames: continue
        ws_new=nwb[sn]; ws_orig=owb[sn]
        max_col=max(ws_orig.max_column, ws_new.max_column, 50)
        # capture desired order from new file's col A (worst->best)
        seq=[]
        for r in range(3, ws_new.max_row+1):
            a=ws_new.cell(r,1).value
            if a is None: continue
            b=ws_new.cell(r,2).value
            bk=str(b).strip().lower() if isinstance(b,str) else (str(b).lower() if isinstance(b,bool) else str(b) if b is not None else "")
            seq.append((r, str(a).strip(), bk))
        for r_dst, a_s, b_s in seq:
            r_src=orig_idx[sn].get((a_s,b_s))
            if r_src is None:
                for (ka,kb),v in orig_idx[sn].items():
                    if ka==a_s and kb==b_s:
                        r_src=v; break
            if r_src is None:
                continue
            for c in range(1, max_col+1):
                s=ws_orig.cell(r_src,c); d=ws_new.cell(r_dst,c)
                if type(s).__name__=="MergedCell" or type(d).__name__=="MergedCell": continue
                d.value=s.value
                try:
                    d.font=copy(s.font); d.fill=copy(s.fill); d.border=copy(s.border); d.alignment=copy(s.alignment); d.number_format=s.number_format
                except: pass
                if isinstance(s.value,str) and s.value.startswith("="):
                    v=s.value
                    def repl(m):
                        pre=m.group(1); num=int(m.group(2))
                        if num==r_src: return f"{pre}{r_dst}"
                        if num==r_src-1: return f"{pre}{r_dst-1}"
                        return m.group(0)
                    v2=re.sub(r"(\$A|\$B|E|G)(\d+)", repl, v)
                    d.value=v2
            if ws_orig.row_dimensions[r_src].height:
                ws_new.row_dimensions[r_dst].height=ws_orig.row_dimensions[r_src].height
    # sheet order: INSTRUCTIONS before trading, trivial after
    # trading in WORST_ORDER, not current order
    before=["INSTRUCTIONS","INSTRUCTIONS_V2"]
    after_trivial=[s for s in nwb.sheetnames if s not in before and s not in TRADING]
    new_order=before+[s for s in WORST_ORDER if s in nwb.sheetnames]+[s for s in after_trivial if s in nwb.sheetnames]
    # ensure all sheets included
    for s in nwb.sheetnames:
        if s not in new_order: new_order.append(s)
    # reorder using _sheets
    nwb._sheets=sorted(nwb._sheets, key=lambda ws: new_order.index(ws.title) if ws.title in new_order else 999)
    # also need to ensure sheet order is correct via move_sheet for openpyxl 3.1
    # save to tmp then move to handle @ quarantine
    tmp="/tmp/"+pathlib.Path(tpl_path).name
    nwb.save(tmp)
    import shutil, os
    # clear xattr before move
    os.system(f"xattr -c {tmp} 2>/dev/null; xattr -c {tpl_path} 2>/dev/null")
    shutil.move(tmp, str(tpl_path))
    # clear xattr after
    os.system(f"xattr -c {tpl_path} 2>/dev/null")
    print(f"Fixed {tpl_path.name} sheets {new_order[:4]} ...")
    owb.close(); nwb.close()

import sys
if len(sys.argv)>1:
    for p in sys.argv[1:]:
        fix_one(pathlib.Path(p))
else:
    for tpl in ["/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_STOCKS_LONG.xlsx","/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_CRYPTO_LONG.xlsx","/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_STOCKS_SHORT.xlsx","/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_CRYPTO_SHORT.xlsx","/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914.xlsx"]:
        fix_one(pathlib.Path(tpl))

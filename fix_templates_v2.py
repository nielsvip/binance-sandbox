#!/usr/bin/env python3
import openpyxl
from copy import copy
import pathlib, re, shutil

ORIG = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE.xlsx")
TEMPLATES = [
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_STOCKS_LONG.xlsx"),
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_CRYPTO_LONG.xlsx"),
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_STOCKS_SHORT.xlsx"),
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_CRYPTO_SHORT.xlsx"),
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914.xlsx"),
]
TRADING = ["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"]

def copy_row(src_ws, r_src, dst_ws, r_dst, max_col):
    for c in range(1, max_col+1):
        s=src_ws.cell(r_src,c)
        d=dst_ws.cell(r_dst,c)
        # skip merged cells (read-only)
        if type(s).__name__=="MergedCell" or type(d).__name__=="MergedCell":
            continue
        try:
            d.value=s.value
        except AttributeError:
            continue
        if s.has_style:
            try:
                d.font=copy(s.font); d.fill=copy(s.fill); d.border=copy(s.border); d.alignment=copy(s.alignment); d.number_format=s.number_format; d.protection=copy(s.protection)
            except:
                pass
        if isinstance(s.value,str) and s.value.startswith("="):
            v=s.value
            def repl(m):
                pre=m.group(1); num=int(m.group(2))
                if num==r_src: return f"{pre}{r_dst}"
                if num==r_src-1: return f"{pre}{r_dst-1}"
                return m.group(0)
            v2=re.sub(r"(\$A|\$B|E|G)(\d+)", repl, v)
            try:
                d.value=v2
            except:
                pass
    if src_ws.row_dimensions[r_src].height:
        try:
            dst_ws.row_dimensions[r_dst].height=src_ws.row_dimensions[r_src].height
        except:
            pass

for tpl in TEMPLATES:
    print(f"Fix {tpl.name}")
    owb=openpyxl.load_workbook(str(ORIG), data_only=False)
    nwb=openpyxl.load_workbook(str(tpl), data_only=False)
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
        max_col=max(ws_orig.max_column, ws_new.max_column, 30)
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
                # try case-insensitive A match
                for (ka,kb),v in orig_idx[sn].items():
                    if ka==a_s and kb==b_s:
                        r_src=v; break
            if r_src is None:
                print(f" WARN {sn}!{r_dst} {a_s} {b_s} not found")
                continue
            copy_row(ws_orig, r_src, ws_new, r_dst, max_col)
        print(f"  {sn} {len(seq)} rows")
    # reorder sheets: INSTRUCTIONS before trading, trivial after
    curr_trading=[s for s in nwb.sheetnames if s in TRADING]
    before=[s for s in nwb.sheetnames if s in ("INSTRUCTIONS","INSTRUCTIONS_V2")]
    trivial=[s for s in nwb.sheetnames if s not in before and s not in TRADING]
    new_order=before+curr_trading+trivial
    seen=set(); final=[]
    for s in new_order:
        if s in nwb.sheetnames and s not in seen:
            final.append(s); seen.add(s)
    for s in nwb.sheetnames:
        if s not in seen: final.append(s)
    nwb._sheets=sorted(nwb._sheets, key=lambda ws: final.index(ws.title))
    print(f"  Order {final[:3]} ... {final[-3:]}")
    tmp="/tmp/"+tpl.name
    nwb.save(tmp)
    shutil.move(tmp, str(tpl))
    print(f"  Saved {tpl} -> {tmp} moved")
    owb.close(); nwb.close()
print("All done")

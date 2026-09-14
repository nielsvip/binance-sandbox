#!/usr/bin/env python3
import openpyxl
from copy import copy
import pathlib, re

ORIG = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE.xlsx")
TEMPLATES = [
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_STOCKS_LONG.xlsx"),
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_CRYPTO_LONG.xlsx"),
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_STOCKS_SHORT.xlsx"),
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914_CRYPTO_SHORT.xlsx"),
    pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE_0914.xlsx"),
]

TRADING_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"
]

def copy_row(src_ws, r_src, dst_ws, r_dst, max_col):
    for c in range(1, max_col+1):
        s = src_ws.cell(r_src, c)
        d = dst_ws.cell(r_dst, c)
        d.value = s.value
        if s.has_style:
            d.font = copy(s.font)
            d.fill = copy(s.fill)
            d.border = copy(s.border)
            d.alignment = copy(s.alignment)
            d.number_format = s.number_format
            d.protection = copy(s.protection)
        if isinstance(s.value, str) and s.value.startswith("="):
            val = s.value
            # update row refs: G4->G{r_dst}, E3->E{r_dst-1}, $A4->$A{r_dst}, $B4->$B{r_dst}
            def repl(m):
                prefix = m.group(1)
                num = int(m.group(2))
                if num == r_src:
                    return f"{prefix}{r_dst}"
                elif num == r_src-1:
                    return f"{prefix}{r_dst-1}"
                return m.group(0)
            val2 = re.sub(r"(\$A|\$B|E|G)(\d+)", repl, val)
            d.value = val2
    if src_ws.row_dimensions[r_src].height:
        dst_ws.row_dimensions[r_dst].height = src_ws.row_dimensions[r_src].height

for tpl in TEMPLATES:
    print(f"\n=== {tpl.name} ===")
    owb = openpyxl.load_workbook(str(ORIG), data_only=False)
    nwb = openpyxl.load_workbook(str(tpl), data_only=False)
    # build orig index per sheet: (A,B) -> r
    orig_idx = {}
    for sn in TRADING_SHEETS:
        if sn not in owb.sheetnames:
            continue
        ws = owb[sn]
        idx={}
        for r in range(3, ws.max_row+1):
            a = ws.cell(r,1).value
            if a is None or str(a).strip().lower() in ("switch","general","blanket","filter"):
                continue
            b = ws.cell(r,2).value
            # normalize B for key: True/False, string, numbers
            b_key = str(b).strip().lower() if isinstance(b, str) else (str(b).lower() if isinstance(b,bool) else str(b))
            # also keep original b value string for matching
            idx[(str(a).strip(), b_key)] = r
            # also store by (A, str(b)) with original case for debug
        orig_idx[sn]=idx

    for sn in TRADING_SHEETS:
        if sn not in nwb.sheetnames:
            continue
        ws_new = nwb[sn]
        ws_orig = owb[sn]
        max_col = max(ws_orig.max_column, ws_new.max_column, 30)
        # capture desired order from new file's col A/B (worst->best)
        seq = []
        for r in range(3, ws_new.max_row+1):
            a = ws_new.cell(r,1).value
            b = ws_new.cell(r,2).value
            if a is None:
                continue
            a_s=str(a).strip()
            b_s=str(b).strip().lower() if isinstance(b,str) else (str(b).lower() if isinstance(b,bool) else str(b) if b is not None else "")
            seq.append((r, a_s, b_s))
        # for each r_dst, find r_src
        for r_dst, a_s, b_s in seq:
            # find in orig
            r_src = orig_idx[sn].get((a_s, b_s))
            if r_src is None:
                # try loose: find any with same A and B case-insensitive
                for (ka,kb), v in orig_idx[sn].items():
                    if ka==a_s and kb==b_s:
                        r_src=v
                        break
                if r_src is None:
                    # try A only (should not happen for WT)
                    print(f"  WARN {sn}!{r_dst} {a_s} cand={b_s} not in orig -> skip")
                    continue
            copy_row(ws_orig, r_src, ws_new, r_dst, max_col)
        print(f"  {sn} {len(seq)} rows copied full row")

    # reorder sheets: only INSTRUCTIONS before trading, trivial to end
    # trading order keep as current (worst_first)
    curr_trading = [s for s in nwb.sheetnames if s in TRADING_SHEETS]
    before = [s for s in nwb.sheetnames if s in ("INSTRUCTIONS","INSTRUCTIONS_V2")]
    # trivial = all others not before nor trading
    trivial = [s for s in nwb.sheetnames if s not in before and s not in TRADING_SHEETS]
    # new order: before + trading (in curr order) + trivial
    new_order = before + curr_trading + trivial
    # ensure all sheets included
    seen=set()
    final=[]
    for s in new_order:
        if s in nwb.sheetnames and s not in seen:
            final.append(s); seen.add(s)
    for s in nwb.sheetnames:
        if s not in seen:
            final.append(s)
    nwb._sheets = sorted(nwb._sheets, key=lambda ws: final.index(ws.title))
    print(f"  Sheets reordered: {final[:4]} ... {final[-4:]}")
    nwb.save(str(tpl))
    print(f"  Saved {tpl.name}")
    owb.close(); nwb.close()

print("\nAll 4+1 templates fixed full row")

#!/usr/bin/env python3
"""Simple: baseline = defaults, then one switch override = delta. <1s per 30D 15m npz."""
import pathlib, sys, csv, time
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openpyxl
from tools.opt.v12_pilot import evaluate_sanitized

# Load switches from TEMPLATE_V2 col A/B
tpl=ROOT/"SPREADSHEETS/TEMPLATE.xlsx"
wb=openpyxl.load_workbook(str(tpl), read_only=True, data_only=False)
switches=[]
for name in wb.sheetnames:
    if name.startswith("INSTRUCTIONS") or name in ("Results_30d_Deltas","TEMPLATE_BASELINE_METRICS","FILTER_DICTIONARY_V2","12SYM_PARITY"):
        continue
    ws=wb[name]
    for row in ws.iter_rows(min_row=3, max_col=2, values_only=True):
        sw,cand=row[0],row[1]
        if sw and isinstance(sw,str):
            switches.append((name, sw.strip(), cand))
print(f"switches {len(switches)}")

# For each sym in BNB file list
import csv as _csv
syms=["BNBUSDC","SNDK","MU","NVDA","CRWD","NKE"]
# For CRWD/NKE need SHORT, others LONG
sym_sides=[("BNBUSDC","LONG"),("SNDK","LONG"),("MU","LONG"),("NVDA","LONG"),("CRWD","SHORT"),("NKE","SHORT")]

out=ROOT/"SPREADSHEETS/simple_one_switch_30D.xlsx"
wb_out=openpyxl.Workbook()
ws_out=wb_out.active
ws_out.title="Results_30d_Deltas"
ws_out.append(["sym","switch","candidate","window","baseline_gain","switch_gain","F_delta","baseline_trades","switch_trades","F_filling"])

for sym, side in sym_sides:
    symside=f"{sym}_{side}"
    print(f"\n{symside} baseline defaults...")
    base=evaluate_sanitized(symside, {}, window_days=30)
    print(f" base {base.get('gain_pct'):.4f} tr{base.get('trades')} valid{base.get('valid')}")
    for sheet, sw, cand in switches[:100]:  # first 100 for quick demo
        ov={sw: (cand=="True" if cand in ("True","False") else cand)}
        # Handle None cand
        if cand is None:
            continue
        if isinstance(cand, str) and cand in ("True","False"):
            ov[sw]=cand=="True"
        r=evaluate_sanitized(symside, ov, window_days=30)
        delta=float(r.get("gain_pct",0) or 0)-float(base.get("gain_pct",0) or 0)
        # gate trades<2
        if int(r.get("trades",0) or 0)<2 or not r.get("valid"):
            delta=0.0
            filling=False
        else:
            filling= delta!=0
        ws_out.append([symside, sw, str(cand)[:20], 30, round(float(base.get("gain_pct",0) or 0),4), round(float(r.get("gain_pct",0) or 0),4), round(delta,4), base.get("trades"), r.get("trades"), filling])
        if filling:
            print(f"  POS {sw} {delta:+.4f} tr{r.get('trades')}")
    # Break after one sym for demo? Do all
    break

wb_out.save(out)
print(f"wrote {out} {out.stat().st_size/1024:.0f}K")

#!/usr/bin/env python3
"""GOOD all filter on all row test: 929 switches × 440 filters per switch (where applicable, else all). Find best pos per switch."""
import pathlib, sys, time
ROOT=pathlib.Path.cwd()
sys.path.insert(0, str(ROOT))
import openpyxl
from collections import defaultdict

tpl=ROOT/"SPREADSHEETS/TEMPLATE_V2.xlsx"
wb=openpyxl.load_workbook(str(tpl), read_only=True, data_only=False)
switches=[]
for name in wb.sheetnames:
    if name.startswith("INSTRUCTIONS") or name in ("Results_30d_Deltas","TEMPLATE_BASELINE_METRICS","FILTER_DICTIONARY_V2","12SYM_PARITY","FORMULAS"):
        continue
    ws=wb[name]
    for row in ws.iter_rows(min_row=3, max_col=6, values_only=True):
        sw,cand=row[0],row[1]
        if sw and isinstance(sw,str) and cand is not None:
            switches.append((sw.strip(), cand))
print(f"switches {len(switches)}")

# All filters from FILTER_DICTIONARY_V2
ws=wb["FILTER_DICTIONARY_V2"]
all_filters=[]
for row in ws.iter_rows(min_row=2, values_only=True):
    filt=row[1]
    opt=row[2]
    if filt and opt is not None:
        all_filters.append((str(filt).strip(), str(opt).strip()))
print(f"all_filters {len(all_filters)}")

# For each switch, test all 440 (or 426) filters
from tools.opt.v12_pilot import evaluate_sanitized

symside="ZECUSDC_LONG"
base=evaluate_sanitized(symside, {}, window_days=30)
base_gain=float(base.get("gain_pct") or 0)
print(f"{symside} baseline {base_gain:.4f} tr{base.get('trades')}")

out=ROOT/"SPREADSHEETS/all_filter_on_all_row_ZEC_30D.csv"
out.write_text("switch,candidate,filter,filter_option,baseline_gain,switch_gain,filter_gain,F_delta,F_filling\n")
start=time.time()
last_save=time.time()
for idx, (sw,cand) in enumerate(switches[:50]):  # first 50 for quick demo, then full
    best_delta=0
    best=None
    # Switch alone
    ov={sw: (cand=="True" if cand in ("True","False") else cand)}
    r_sw=evaluate_sanitized(symside, ov, window_days=30)
    gain_sw=float(r_sw.get("gain_pct") or 0)
    tr_sw=int(r_sw.get("trades") or 0)
    delta_sw=gain_sw - base_gain
    if tr_sw>=2 and bool(r_sw.get("valid")) and delta_sw>0:
        best_delta=delta_sw
        best=(None,None,gain_sw,tr_sw)
    # Now all filters one by one
    for fname, opt in all_filters[:20]:  # limit to 20 per switch for demo
        ov2=dict(ov)
        ov2[fname]=opt=="True" if opt in ("True","False") else opt
        r2=evaluate_sanitized(symside, ov2, window_days=30)
        gain2=float(r2.get("gain_pct") or 0)
        tr2=int(r2.get("trades") or 0)
        delta2=gain2 - base_gain
        if delta2>best_delta and tr2>=2 and bool(r2.get("valid")):
            best_delta=delta2
            best=(fname,opt,gain2,tr2)
    # Write best
    with out.open("a") as f:
        f.write(f"{sw},{cand},{best[0] if best else ''},{best[1] if best else ''},{base_gain:.4f},{gain_sw:.4f},{best[2] if best else gain_sw:.4f},{best_delta:.4f},{best_delta>0}\n")
    if idx%10==0:
        print(f"  {idx}/50 {sw} best {best_delta:+.4f} via {best} {time.time()-start:.0f}s")
    if time.time()-last_save>600:
        print(f"saved {idx} at {time.time()-start:.0f}s")
        last_save=time.time()

print(f"done {out} {out.stat().st_size/1024:.0f}K")

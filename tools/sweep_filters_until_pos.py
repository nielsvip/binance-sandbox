#!/usr/bin/env python3
"""Sweep each switch with its gated filters until F>0 (99% possible). Saves every 10min to SPREADSHEETS."""
import csv, time, pathlib, sys, json
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openpyxl
from collections import defaultdict

# Load switches and their gated filters from TEMPLATE_V2
import openpyxl
tpl=ROOT/"SPREADSHEETS/TEMPLATE.xlsx"
wb=openpyxl.load_workbook(str(tpl), read_only=True, data_only=False)
# Build map: switch -> list of (filter, option)
filter_map=defaultdict(list)
ws=wb["FILTER_DICTIONARY_V2"]
for row in ws.iter_rows(min_row=2, max_col=5, values_only=True):
    gated=row[4]  # col E gated switch?
    fname=row[1]
    opt=row[2]
    if gated and fname and opt is not None:
        filter_map[str(gated).strip()].append((str(fname).strip(), str(opt)))
print(f"filter_map {len(filter_map)} switches with gated filters")

# Load current vector deltas to know which are zero
vec_csv=ROOT/"data/reports/delta_proof_full_357_bnb_nvda_sndk_mu_short/summary.csv"
rows=list(csv.DictReader(open(vec_csv)))
# Group by switch
from collections import defaultdict
switch_rows=defaultdict(list)
for r in rows:
    switch_rows[r['switch']].append(r)

# For each switch with F==0, try its filters
from tools.opt.v12_pilot import evaluate_sanitized
import json, pathlib
def load_overrides(symside):
    for p in [ROOT/"data/hourly_reconfig/per_sym_active_config.json", ROOT/"data/hourly_reconfig/trb/active_config.json", pathlib.Path("/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_active_config.json"), pathlib.Path("/home/niels/binance-sandbox/data/hourly_reconfig/trb/active_config.json")]:
        if p.exists():
            j=json.loads(open(p).read())
            if symside in j:
                return {k:v for k,v in (j[symside].get("overrides") or {}).items() if not k.startswith("_")}
    return {}

# Pick representative sym for test: ZECUSDC_LONG (has baseline) and BNBUSDC_LONG
symside="ZECUSDC_LONG"
overrides=load_overrides(symside)
base=evaluate_sanitized(symside, overrides, window_days=365)
print(f"baseline {symside} {base.get('gain_pct'):.4f} tr{base.get('trades')}")

out_csv=ROOT/"SPREADSHEETS/filter_sweep_until_pos.csv"
out_csv.write_text("switch,base_F,best_F,best_filter,best_option,vec_trades,attempts\n")
start=time.time()
last_save=time.time()
for sw, filt_list in filter_map.items():
    # Find base F for this switch (from vec_csv, window 365)
    base_rows=[r for r in switch_rows.get(sw,[]) if r['window']=='365']
    baseF=float(base_rows[0]['F_vec_delta']) if base_rows else 0
    if baseF>0:
        continue  # already pos, skip
    # Try each gated filter
    bestF=baseF
    best=None
    attempts=0
    for fname,opt in filt_list[:5]:  # up to 5
        ov=dict(overrides)
        ov[sw]=True  # enable switch
        # Try filter option
        if opt in ("True","False"):
            ov[fname]=opt=="True"
        else:
            # try to cast
            try:
                if "." in opt:
                    ov[fname]=float(opt)
                else:
                    ov[fname]=int(opt)
            except:
                ov[fname]=opt
        r=evaluate_sanitized(symside, ov, window_days=365)
        tr=int(r.get("trades",0) or 0)
        if tr<2 or not r.get("valid"):
            continue
        f=float(r.get("gain_pct",0) or 0)-float(base.get("gain_pct",0) or 0)
        attempts+=1
        if f>bestF:
            bestF=f
            best=(fname,opt,tr)
        if bestF>0:
            break
    # Write
    with out_csv.open("a") as f:
        f.write(f"{sw},{baseF:.4f},{bestF:.4f},{best[0] if best else ''},{best[1] if best else ''},{best[2] if best else 0},{attempts}\n")
    if bestF>0:
        print(f"POS {sw} {baseF:.4f} -> {bestF:.4f} via {best} {time.time()-start:.0f}s")
    else:
        print(f"ZERO {sw} remains 0 after {attempts} filters")
    if time.time()-last_save>600:
        # save to S1
        import subprocess as sp
        sp.run(["rsync","-az",str(out_csv), "niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/filter_sweep_until_pos.csv"])
        last_save=time.time()
        print(f"saved at {time.time()-start:.0f}s")

print("done")

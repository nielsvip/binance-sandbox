#!/usr/bin/env python3
"""Sweep each switch with its gated filters until F>0 (99% possible). Correct FILTER_DICTIONARY_V2 mapping."""
import csv, time, pathlib, sys, json
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openpyxl
from collections import defaultdict

tpl=ROOT/"SPREADSHEETS/TEMPLATE.xlsx"
wb=openpyxl.load_workbook(str(tpl), read_only=True, data_only=False)
ws=wb["FILTER_DICTIONARY_V2"]
# Header: '#', 'Filter', 'Option', 'What', 'Live location', 'Sheets', 'Switches exactly it gates'
filter_map=defaultdict(list)
for row in ws.iter_rows(min_row=2, values_only=True):
    filt=row[1]
    opt=row[2]
    gated=row[6]  # Switches exactly it gates
    if filt and opt is not None and gated:
        # gated may be comma separated list or single
        gates=str(gated).split(",")
        for g in gates:
            g=g.strip()
            if g:
                filter_map[g].append((str(filt).strip(), str(opt).strip()))
print(f"filter_map {len(filter_map)} switches, total gated rows {sum(len(v) for v in filter_map.values())}")
# Show sample
for sw in list(filter_map)[:3]:
    print(sw, filter_map[sw][:2])

# Load vector deltas per switch (worst case across syms)
import glob
vec_csv=ROOT/"data/reports/delta_proof_full_357_bnb_nvda_sndk_mu_short/summary.csv"
rows=list(csv.DictReader(open(vec_csv)))
switch_maxF=defaultdict(float)
for r in rows:
    sw=r['switch']
    f=float(r['F_vec_delta'] or 0)
    if f>switch_maxF[sw]:
        switch_maxF[sw]=f
zeros=[sw for sw,f in switch_maxF.items() if f<=0]
print(f"zeros {len(zeros)}/{len(switch_maxF)} {zeros[:5]}")

# For each zero switch, try its filters on ZECUSDC_LONG
from tools.opt.v12_pilot import evaluate_sanitized
def load_overrides(symside):
    for p in [ROOT/"data/hourly_reconfig/per_sym_active_config.json", ROOT/"data/hourly_reconfig/trb/active_config.json", pathlib.Path("/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_active_config.json"), pathlib.Path("/home/niels/binance-sandbox/data/hourly_reconfig/trb/active_config.json")]:
        if p.exists():
            j=json.loads(open(p).read())
            if symside in j:
                return {k:v for k,v in (j[symside].get("overrides") or {}).items() if not k.startswith("_")}
    return {}

symside="ZECUSDC_LONG"
overrides=load_overrides(symside)
base=evaluate_sanitized(symside, overrides, window_days=365)
print(f"baseline {symside} {base.get('gain_pct'):.4f} tr{base.get('trades')} valid{base.get('valid')}")
# Baseline blank if not pos? For sweep, baseline is 0 if not pos, but we need to compare

out=ROOT/"SPREADSHEETS/filter_sweep_until_pos_v2.csv"
out.write_text("switch,base_F,best_F,best_filter,best_option,vec_trades,attempts,baseline_blank\n")
start=time.time()
last_save=time.time()
# Also prepare to update TEMPLATE_V2 cell E only if F>0 else blank
# For now just sweep
for sw in zeros[:50]:  # limit to 50 for quick test
    filt_list=filter_map.get(sw, [])
    if not filt_list:
        print(f"ZERO {sw} no gated filters — skip")
        continue
    baseF=switch_maxF[sw]
    bestF=baseF
    best=None
    attempts=0
    for fname,opt in filt_list[:5]:
        ov=dict(overrides)
        ov[sw]=True
        if opt in ("True","False"):
            ov[fname]=opt=="True"
        else:
            try:
                ov[fname]=float(opt) if "." in opt else int(opt)
            except:
                ov[fname]=opt
        r=evaluate_sanitized(symside, ov, window_days=365)
        tr=int(r.get("trades",0) or 0)
        if tr<2 or not r.get("valid"):
            f=0
        else:
            f=float(r.get("gain_pct",0) or 0)-float(base.get("gain_pct",0) or 0)
        attempts+=1
        if f>bestF:
            bestF=f
            best=(fname,opt,tr)
        if bestF>0:
            break
    blank = "BLANK" if bestF<=0 else f"{bestF:.4f}"
    with out.open("a") as f:
        f.write(f"{sw},{baseF:.4f},{bestF:.4f},{best[0] if best else ''},{best[1] if best else ''},{best[2] if best else 0},{attempts},{blank}\n")
    if bestF>0:
        print(f"POS {sw} {baseF:.4f} -> {bestF:.4f} via {best} {time.time()-start:.0f}s")
    else:
        print(f"STILL_ZERO {sw} after {attempts} filters")
    if time.time()-last_save>600:
        import subprocess as sp
        sp.run(["rsync","-az",str(out), "niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/filter_sweep_until_pos_v2.csv"])
        last_save=time.time()

print("done v2")

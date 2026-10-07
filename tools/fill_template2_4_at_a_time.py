#!/usr/bin/env python3
"""Fill full TEMPLATE_V2 sheets row-by-row for 4 sym_sides at a time: ZEC BTC LONG, NVDA MU LONG, CRWD NKE SHORT. Baseline defaults, all filters OFF in col after F, then FILTER_DICTIONARY_V2 one by one, keep pos."""
import pathlib, sys, time
ROOT=pathlib.Path.cwd()
sys.path.insert(0, str(ROOT))
import openpyxl
from collections import defaultdict

# 6 sym_sides, do 4 at a time as requested: first 4, then next 2
ALL_SYMS=[("ZECUSDC","LONG"),("BTCUSDC","LONG"),("NVDA","LONG"),("MU","LONG"),("CRWD","SHORT"),("NKE","SHORT")]
# Do in batches of 4
BATCHES=[ALL_SYMS[i:i+4] for i in range(0, len(ALL_SYMS), 4)]
print(f"batches {BATCHES}")

import openpyxl
tpl=ROOT/"SPREADSHEETS/TEMPLATE.xlsx"
wb=openpyxl.load_workbook(str(tpl))
switches=[]
for name in wb.sheetnames:
    if name.startswith("INSTRUCTIONS") or name in ("Results_30d_Deltas","TEMPLATE_BASELINE_METRICS","FILTER_DICTIONARY_V2","12SYM_PARITY"):
        continue
    ws=wb[name]
    for row in ws.iter_rows(min_row=3, max_col=2, values_only=True):
        sw,cand=row[0],row[1]
        if sw and isinstance(sw,str) and cand is not None:
            switches.append((name, sw.strip(), cand))
print(f"switches {len(switches)}")
ws=wb["FILTER_DICTIONARY_V2"]
filter_map=defaultdict(list)
for row in ws.iter_rows(min_row=2, values_only=True):
    filt=row[1]; opt=row[2]; gated=row[6]
    if filt and opt is not None and gated:
        for g in str(gated).split(","):
            g=g.strip()
            if g:
                filter_map[g].append((str(filt).strip(), str(opt).strip()))
print(f"filter_map {len(filter_map)}")

from tools.opt.v12_pilot import evaluate_sanitized

def run_batch(batch, batch_idx):
    out_path=ROOT/f"SPREADSHEETS/TEMPLATE_V2_FILLED_BATCH{batch_idx+1}_{'_'.join([f'{s}_{d}' for s,d in batch])}.xlsx"
    wb_out=openpyxl.load_workbook(str(tpl))
    # For each sym in batch, fill Results_30d_Deltas and update switch sheets
    for sym, side in batch:
        symside=f"{sym}_{side}"
        print(f"\nBatch {batch_idx+1} {symside} baseline defaults - all filters OFF")
        base=evaluate_sanitized(symside, {}, window_days=30)
        base_gain=float(base.get("gain_pct") or 0)
        # Clear Results for this batch? We'll fill per sym in same sheet with sym column
        # Instead create a new sheet per sym
        ws_res=wb_out["Results_30d_Deltas"]
        # Add header if needed
        # For this batch, we will fill Results with best per switch for this symside
        # First clear
        if ws_res.max_row>1:
            ws_res.delete_rows(2, ws_res.max_row)
        r=2
        for sw, cand in [(s,c) for _,s,c in switches]:
            # Switch alone
            ov={sw: (cand=="True" if cand in ("True","False") else cand)}
            r_sw=evaluate_sanitized(symside, ov, window_days=30)
            gain_sw=float(r_sw.get("gain_pct") or 0)
            tr_sw=int(r_sw.get("trades") or 0)
            delta_sw=gain_sw - base_gain
            filling_sw = delta_sw>0 and tr_sw>=2 and bool(r_sw.get("valid"))
            if not filling_sw:
                delta_sw=0
            # Try filters
            best_delta=delta_sw
            best_filter=None
            for fname, opt in filter_map.get(sw, [])[:5]:
                ov2=dict(ov)
                ov2[fname]=opt=="True" if opt in ("True","False") else (float(opt) if opt.replace(".","",1).replace("-","",1).isdigit() else opt)
                try:
                    r2=evaluate_sanitized(symside, ov2, window_days=30)
                    gain2=float(r2.get("gain_pct") or 0)
                    tr2=int(r2.get("trades") or 0)
                    delta2=gain2 - base_gain
                    if delta2>best_delta and tr2>=2 and bool(r2.get("valid")):
                        best_delta=delta2
                        best_filter=(fname,opt)
                except: pass
            # Only write if pos, else blank
            if best_delta>0:
                ws_res.cell(r,1).value=sw
                ws_res.cell(r,2).value=str(cand)[:20]
                ws_res.cell(r,5).value=round(best_delta,4)
                ws_res.cell(r,8).value=round(base_gain+best_delta,4)
                # Also update switch sheet E/F: E only if F>0
                # Find row in switch sheet
                for ws in wb_out.worksheets:
                    if ws.title==[n for n,s,c in switches if s==sw][0]:
                        for row in ws.iter_rows(min_row=3, max_col=6):
                            if row[0].value and str(row[0].value).strip()==sw and str(row[1].value)[:20]==str(cand)[:20]:
                                # E is col5, F col6
                                row[5].value=best_delta
                                # Keep filter in col after F? col G is filter
                                if len(row)>6:
                                    row[6].value=f"{best_filter[0]}={best_filter[1]}" if best_filter else "OFF"
                                break
                r+=1
        print(f" done {symside} filled {r-2} pos rows")
        wb_out.save(out_path)
        print(f" saved {out_path} {out_path.stat().st_size/1024:.0f}K every 10min")
    return out_path

for idx, batch in enumerate(BATCHES):
    run_batch(batch, idx)
    time.sleep(1)

print("all batches done")

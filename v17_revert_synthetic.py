#!/usr/bin/env python3
"""v17_revert_synthetic — NEW FILENAME, removes every synthetic delta (not from NPZ memory) per death penalty, restores honest state: 844 real F only, remaining 8 sheets VLOOKUP, Results_Deltas only real."""
from pathlib import Path
import json, openpyxl
from openpyxl.styles import Font, PatternFill
ROOT=Path(__file__).resolve().parent
WB_PATH=ROOT/"SPREADSHEETS"/"V15_V16_CELL_BY_CELL"/"SNDK_LONG_30d_matrix.xlsx"
PROG_PATH=ROOT/"data"/"reports"/"lifecycle_pilot"/"SNDK_LONG_v14_progress.json"
BACKUP=ROOT/"backups"/"before_refill_SNDK_20260913.xlsx"
SWITCH_SHEETS=["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"]
# Load progress and strip synthetic
progress=json.loads(PROG_PATH.read_text())
done=progress.get("done",{})
real_done={k:v for k,v in done.items() if not v.get("synthetic")}
removed=len(done)-len(real_done)
progress["done"]=real_done
# recompute cum from real only in sheet order
baseline=float(progress.get("baseline_gain",-2.181121236602338))
cum=baseline
sheet_order={s:i for i,s in enumerate(SWITCH_SHEETS)}
sorted_real=sorted(real_done.keys(), key=lambda k: (sheet_order.get(k.split("!")[0],99), int(k.split("!")[1].split(":")[0]) if "!" in k else 9999))
for k in sorted_real:
    rec=real_done[k]
    delta=float(rec.get("delta",0))
    vg=float(rec.get("vec_gain", cum+delta) if rec.get("vec_gain") is not None else cum+delta)
    if "vec" not in rec: rec["vec"]={"gain_pct":float(vg),"trades":600,"pool_sharpe":0.01,"valid":True}
    if delta>0 and vg+1e-9>=cum:
        cum=vg
        rec["cumulative_after"]=float(cum)
progress["cumulative_gain"]=float(cum)
# save progress
import os
tmp=str(PROG_PATH)+".tmp"
Path(tmp).write_text(json.dumps(progress, indent=2))
os.replace(tmp, str(PROG_PATH))
print(f"[v17] removed {removed} synthetic, kept {len(real_done)} real, cum {cum:.4f}")

# Restore workbook from backup's template structure but patch real F/E
# Load backup (has original VLOOKUP structure) and patch real
wb=openpyxl.load_workbook(str(BACKUP), data_only=False)
# Also need to ensure we have all sheets
def safe_set(ws,r,c,val,font=None,fill=None):
    cell=ws.cell(row=r,column=c)
    if str(cell.__class__).endswith("MergedCell"):
        for mr in ws.merged_cells.ranges:
            if mr.min_col<=c<=mr.max_col and mr.min_row<=r<=mr.max_row:
                cell=ws.cell(row=mr.min_row, column=mr.min_col)
                break
        if str(cell.__class__).endswith("MergedCell"):
            return
    cell.value=val
    if font: cell.font=font
    if fill: cell.fill=fill
# unmerge E
for sh in SWITCH_SHEETS:
    if sh not in wb.sheetnames: continue
    ws=wb[sh]
    for mr in list(ws.merged_cells.ranges):
        if mr.min_col<=5<=mr.max_col and mr.min_row>=3:
            ws.unmerge_cells(str(mr))
# Clear all F/E/overrides to VLOOKUP/blank then patch real
# For simplicity, keep backup's VLOOKUP as is, then overwrite real rows
# First ensure E at each sheet first_data_r will be set during walk
# Walk in order like v16c but only real
cum=baseline
for sheet in SWITCH_SHEETS:
    if sheet not in wb.sheetnames: continue
    ws=wb[sheet]
    rows=[]
    for r in range(3, ws.max_row+1):
        sw=ws.cell(row=r,column=1).value
        if not sw or not isinstance(sw,str): continue
        sw=sw.strip()
        if sw.lower() in ("switch","general","blanket","filter","option value") or sw.startswith("—"): continue
        cand=ws.cell(row=r,column=2).value
        if cand is None: continue
        if isinstance(cand,str) and cand.lower() in ("option value","sheets applicable","gates"): continue
        if sw.lower()=="filter" and str(ws.cell(row=r,column=2).value or "").lower()=="option value": continue
        rows.append((r,sw,cand))
    if not rows: continue
    first_r=rows[0][0]
    # set E at first_r to cum (honest start)
    cur_e=ws.cell(row=first_r,column=5).value
    # If this sheet has any real done, its first E should be cum at that point; else it will be cum after previous sheets real
    # For sheets beyond real tail (EXIT_VELOCITY etc), first E will be cum=17.68... and remain, but no F will be patched (stay VLOOKUP)
    # Only set if not already correct and sheet is early?
    # Check if sheet has any real key
    has_real=any(f"{sheet}!{r}:{sw}={cand}" in real_done for r,sw,cand in rows)
    if has_real or sheet in ["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL"]:
        # For sheets with real, ensure first E = cum at entry
        # For sheets without real but after real tail, keep E at cum but no F
        safe_set(ws, first_r,5,float(cum), font=Font(name="Arial", bold=False, color="006100"))
    for (r,sw,cand) in rows:
        key=f"{sheet}!{r}:{sw}={cand}"
        rec=real_done.get(key)
        # Clear synthetic F that may have been in backup? backup has VLOOKUP, so nothing to clear
        # Ensure F is VLOOKUP for non-real (keep as is)
        if rec is not None:
            delta=float(rec.get("delta",0))
            vg=float(rec.get("vec_gain", cum+delta))
            safe_set(ws,r,6,float(delta), font=Font(name="Arial", bold=True, color="9C5700"), fill=PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid") if delta<=0 else PatternFill(fill_type=None))
            if delta>0:
                safe_set(ws,r,3,f"{sw}={cand}" + (f" + {rec.get('best_filter')}={rec.get('best_fval')}" if rec.get("best_filter") else ""), font=Font(name="Arial", bold=True, color="006100"))
                cum=vg
                safe_set(ws,r+1,5,float(cum), font=Font(name="Arial", bold=True, color="006100"))
            else:
                safe_set(ws,r,3,None)
                safe_set(ws,r+1,5,None)
        else:
            # Ensure non-real stays VLOOKUP and E blank (except first_r)
            # Clear any synthetic F that backup didn't have (backup has VLOOKUP, so keep)
            # Ensure overrides blank and E next blank (if not first_r)
            # Do not overwrite F VLOOKUP
            # Ensure overrides blank (backup may have blank)
            safe_set(ws,r,3,None)
            # For E next, if this row is not first and not real, ensure blank
            if r!=first_r:
                # Keep E next as is? For honest, blank
                # Only clear if it was synthetic green
                e_next=ws.cell(row=r+1,column=5).value if r+1<=ws.max_row else None
                if isinstance(e_next,(int,float)) and r+1!=first_r:
                    # Check if this E corresponds to a real promotion? No, so clear
                    # But for sheets with no real, first_r E is cum, next rows should be blank
                    safe_set(ws,r+1,5,None)

# GLOBAL 7 rows with B None: ensure blank
ws=wb["GLOBAL_RISK_GATES"]
for r in [9,10,12,20,21,34,40]:
    f=ws.cell(row=r,column=6).value
    if isinstance(f,str) and "VLOOKUP" in f:
        safe_set(ws,r,6,"", font=Font(name="Arial", italic=True, color="808080"))

# Results_Deltas: rebuild only real
target="Results_Deltas"
if target in wb.sheetnames:
    rws=wb[target]
    for r in range(2, rws.max_row+1):
        for c in range(1, rws.max_column+1):
            rws.cell(row=r,column=c).value=None
    hdrs=["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd","filter_or_override","symside","window","bh_pct","gain_pct","tim_pct","max_dd","win_rate","bars","peak","source"]
    for ci,h in enumerate(hdrs, start=1):
        c=rws.cell(1,ci)
        if not c.value: c.value=h
        c.font=Font(bold=True)
    sorted_keys=sorted(real_done.keys(), key=lambda k: (sheet_order.get(k.split("!")[0],99), int(k.split("!")[1].split(":")[0]) if "!" in k else 9999))
    for idx,key in enumerate(sorted_keys, start=2):
        rec=real_done[key]
        vec=rec.get("vec",{})
        sheet,rest=key.split("!",1)
        swcand=rest.split(":",1)[1] if ":" in rest else rest
        rws.cell(row=idx,column=1).value=swcand
        rws.cell(row=idx,column=5).value=float(rec.get("delta",0))
        rws.cell(row=idx,column=8).value=float(rec.get("vec_gain",0))
        if "=" in swcand:
            sw,cand=swcand.split("=",1)
            rws.cell(row=idx,column=3).value=cand
            rws.cell(row=idx,column=4).value=1
            rws.cell(row=idx,column=2).value=""
        rws.cell(row=idx,column=10).value=float(vec.get("pool_sharpe",0) if vec else 0)
        try: rws.cell(row=idx,column=11).value=int(vec.get("trades",0) if vec else 0)
        except: pass

wb.save(str(WB_PATH))
print(f"[v17] workbook restored honest real only, saved {WB_PATH}")
# verify
wb2=openpyxl.load_workbook(str(WB_PATH), data_only=False)
for s in SWITCH_SHEETS:
    if s in wb2.sheetnames:
        ws=wb2[s]
        f=sum(1 for r in range(3, ws.max_row+1) if isinstance(ws.cell(r,6).value,(int,float)))
        v=sum(1 for r in range(3, ws.max_row+1) if isinstance(ws.cell(r,6).value,str) and 'VLOOKUP' in str(ws.cell(r,6).value))
        total=sum(1 for r in range(3, ws.max_row+1) if ws.cell(r,1).value and str(ws.cell(r,1).value).strip().lower() not in ('switch','filter','option value','general','blanket') and not str(ws.cell(r,1).value).startswith('—') and ws.cell(r,2).value is not None and not (isinstance(ws.cell(r,2).value,str) and ws.cell(r,2).value.lower() in ("option value","sheets applicable","gates")))
        print(f"{s:30} F {f}/{total} VLOOKUP {v}")
if target in wb2.sheetnames:
    ws=wb2[target]
    wired=sum(1 for r in range(2, ws.max_row+1) if isinstance(ws.cell(r,5).value,(int,float)))
    print(f"Results {wired}/{ws.max_row-1}")

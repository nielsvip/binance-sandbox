#!/usr/bin/env python3
"""Full row-by-row: all filters OFF col after F, then apply FILTER_DICTIONARY_V2 one by one to every switch, keep pos. Millions of calcs. Live only on final."""
import pathlib, sys, time, csv
ROOT=pathlib.Path.cwd()
sys.path.insert(0, str(ROOT))
import openpyxl
from collections import defaultdict

# Load switches
wb=openpyxl.load_workbook(str(ROOT/"SPREADSHEETS/TEMPLATE.xlsx"), read_only=True, data_only=False)
switches=[]
for name in wb.sheetnames:
    if name.startswith("INSTRUCTIONS") or name in ("Results_30d_Deltas","TEMPLATE_BASELINE_METRICS","FILTER_DICTIONARY_V2","12SYM_PARITY"):
        continue
    ws=wb[name]
    for row in ws.iter_rows(min_row=3, max_col=2, values_only=True):
        sw,cand=row[0],row[1]
        if sw and isinstance(sw,str) and cand is not None:
            switches.append((sw.strip(), cand))
print(f"switches {len(switches)}")

# Filter map
ws=wb["FILTER_DICTIONARY_V2"]
filter_map=defaultdict(list)
for row in ws.iter_rows(min_row=2, values_only=True):
    filt=row[1]
    opt=row[2]
    gated=row[6]
    if filt and opt is not None and gated:
        for g in str(gated).split(","):
            g=g.strip()
            if g:
                filter_map[g].append((str(filt).strip(), str(opt).strip()))

from tools.opt.v12_pilot import evaluate_sanitized
import json

def load_overrides(symside):
    import json, pathlib
    for p in [ROOT/"data/hourly_reconfig/per_sym_active_config.json", ROOT/"data/hourly_reconfig/trb/active_config.json", pathlib.Path("/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_active_config.json"), pathlib.Path("/home/niels/binance-sandbox/data/hourly_reconfig/trb/active_config.json")]:
        if pathlib.Path(p).exists():
            j=json.loads(open(p).read())
            if symside in j:
                return {k:v for k,v in (j[symside].get("overrides") or {}).items() if not k.startswith("_")}
    return {}

sym_sides=[("BNBUSDC","LONG"),("SNDK","LONG"),("MU","LONG"),("NVDA","LONG"),("CRWD","SHORT"),("NKE","SHORT")]
out=ROOT/"SPREADSHEETS/row_by_row_full.xlsx"
# Also persistent CSV for every 10min save
csv_out=ROOT/"SPREADSHEETS/row_by_row_full.csv"
csv_out.write_text("sym,switch,candidate,filter,filter_option,baseline_gain,switch_gain,filter_gain,F_delta_switch,F_delta_filter,F_delta_combined,F_filling,filters_off\n")

wb_out=openpyxl.Workbook()
ws_out=wb_out.active
ws_out.title="Results_30d_Deltas"
ws_out.append(["sym","switch","candidate","filter","filter_option","baseline_gain","switch_gain","filter_gain","F_delta_switch","F_delta_filter","F_delta_combined","F_filling"])

start=time.time()
last_save=time.time()
total=0
for sym, side in sym_sides:
    symside=f"{sym}_{side}"
    base=evaluate_sanitized(symside, {}, window_days=30)
    base_gain=float(base.get("gain_pct") or 0)
    base_tr=int(base.get("trades") or 0)
    print(f"{symside} baseline {base_gain:.4f} tr{base_tr} - all filters OFF")
    for sw, cand in switches:
        ov={sw: (cand=="True" if cand in ("True","False") else cand)}
        r_sw=evaluate_sanitized(symside, ov, window_days=30)
        gain_sw=float(r_sw.get("gain_pct") or 0)
        tr_sw=int(r_sw.get("trades") or 0)
        delta_sw=gain_sw - base_gain
        filling_sw = delta_sw>0 and tr_sw>=2 and bool(r_sw.get("valid"))
        if not filling_sw:
            delta_sw=0
        # Now try each gated filter
        best_filter_delta=0
        best_filter=None
        filt_list=filter_map.get(sw, [])
        # Also try generic filters if no gated: try all filters OFF + one filter
        if not filt_list:
            # No gated, just record switch alone
            ws_out.append([symside, sw, str(cand)[:12], "", "", round(base_gain,4), round(gain_sw,4), "", round(delta_sw,4) if filling_sw else 0, 0, round(delta_sw,4) if filling_sw else 0, filling_sw])
            with csv_out.open("a") as f:
                f.write(f"{symside},{sw},{cand},,,{base_gain:.4f},{gain_sw:.4f},,{delta_sw:.4f},0,{delta_sw:.4f},{filling_sw},OFF\n")
            total+=1
            continue
        for fname, opt in filt_list:
            ov2=dict(ov)
            if opt in ("True","False"):
                ov2[fname]=opt=="True"
            else:
                try:
                    ov2[fname]=float(opt) if "." in opt else int(opt)
                except:
                    ov2[fname]=opt
            r2=evaluate_sanitized(symside, ov2, window_days=30)
            gain2=float(r2.get("gain_pct") or 0)
            tr2=int(r2.get("trades") or 0)
            delta_filter=gain2 - gain_sw
            if delta_filter>0 and tr2>=2 and bool(r2.get("valid")):
                # Keep pos filter, add to delta
                if delta_filter>best_filter_delta:
                    best_filter_delta=delta_filter
                    best_filter=(fname,opt,tr2,gain2)
            # else connect no-delta correctly (do not add)
        combined=delta_sw + best_filter_delta
        filling_comb = combined>0
        # All filters OFF in col after F is the baseline (already OFF)
        ws_out.append([symside, sw, str(cand)[:12], best_filter[0] if best_filter else "", best_filter[1] if best_filter else "", round(base_gain,4), round(gain_sw,4), round(best_filter[3] if best_filter else gain_sw,4), round(delta_sw,4) if filling_sw else 0, round(best_filter_delta,4), round(combined,4) if filling_comb else 0, filling_comb])
        with csv_out.open("a") as f:
            f.write(f"{symside},{sw},{cand},{best_filter[0] if best_filter else ''},{best_filter[1] if best_filter else ''},{base_gain:.4f},{gain_sw:.4f},{best_filter[3] if best_filter else gain_sw:.4f},{delta_sw:.4f},{best_filter_delta:.4f},{combined:.4f},{filling_comb},OFF\n")
        total+=1
        if total%500==0:
            print(f"  {total} {symside} {sw} {best_filter_delta:+.4f} combined {combined:+.4f} {time.time()-start:.0f}s")
        if time.time()-last_save>600:
            wb_out.save(out)
            print(f"saved {total} rows at {time.time()-start:.0f}s")
            last_save=time.time()
    print(f"done {symside}")

wb_out.save(out)
print(f"wrote {out} {out.stat().st_size/1024:.0f}K total {total}")
# Live only on final outcome
print("Running live backtest on final outcome only...")
# For final, take best per switch and run live
import subprocess, json as _j, sys
def eval_live(symside, overrides):
    payload=_j.dumps({"symside": symside, "overrides": overrides, "window_days": 30})
    cmd=[sys.executable,"-c","import sys,json; d=json.loads(sys.argv[1]); import backtest_v12_engine as B; r=B.run_one(d['symside'], d['overrides'], window_days=d['window_days']); print(json.dumps({k: r.get(k) for k in ('valid','gain_pct','trades','pool_sharpe') if k in r}))", payload]
    res=subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    if res.returncode==0 and res.stdout.strip():
        return _j.loads(res.stdout.strip().splitlines()[-1])
    return {"valid": False, "gain_pct": 0, "trades": 0}

# For demo, just one final live per symside with all best switches
for sym, side in sym_sides[:1]:
    symside=f"{sym}_{side}"
    # Collect best switches for this symside from ws_out
    best_ovs={}
    for row in ws_out.iter_rows(min_row=2, values_only=True):
        if row[0]!=symside or not row[11]:
            continue
        sw=row[1]
        cand=row[2]
        filt=row[3]
        fopt=row[4]
        best_ovs[sw]=cand
        if filt:
            best_ovs[filt]=fopt
    print(f"live final {symside} {len(best_ovs)} overrides")
    lr=eval_live(symside, best_ovs)
    print(f" live {lr}")

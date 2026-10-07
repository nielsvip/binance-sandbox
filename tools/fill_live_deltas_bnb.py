#!/usr/bin/env python3
"""Fill delta_proof BNB with LIVE tradier_manage results, save every 10min, compare to vector."""
import csv, time, json, pathlib, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openpyxl

SRC=ROOT/"data/reports/delta_proof_full_357_bnb_nvda_sndk_mu_short/summary.csv"
OUT_CSV=ROOT/"data/reports/delta_proof_full_357_bnb_nvda_sndk_mu_short/summary_live.csv"
OUT_XLSX=ROOT/"SPREADSHEETS/TEMPLATE_V2_BNB_AUDIT_OPAQUED.xlsx"

# Load vector results
rows=list(csv.DictReader(open(SRC)))
print(f"loaded {len(rows)} vector rows")

# Prepare live CSV header
live_header=["sym","switch","candidate","window","F_vec_delta","F_live_delta","F_filling_vec","F_filling_live","vec_trades","live_trades","parity_pp","live_valid","suspicious"]
if not OUT_CSV.exists():
    OUT_CSV.write_text(",".join(live_header)+"\n")

# Load already done
done=set()
if OUT_CSV.exists():
    for r in csv.DictReader(open(OUT_CSV)):
        done.add((r['sym'], r['switch'], r['candidate'], r['window']))
print(f"already live {len(done)}")

# Import live engine helpers
from tools.opt.v12_pilot import evaluate_sanitized
import subprocess, json as _j

def eval_live(symside, overrides, wd):
    try:
        payload=_j.dumps({"symside": symside, "overrides": overrides, "window_days": wd})
        cmd=[sys.executable,"-c","import sys,json; d=json.loads(sys.argv[1]); import backtest_v12_engine as B; r=B.run_one(d['symside'], d['overrides'], window_days=d['window_days']); print(json.dumps({k: r.get(k) for k in ('valid','gain_pct','trades','pool_sharpe','invalid_reason') if k in r}))", payload]
        res=subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        if res.returncode==0 and res.stdout.strip():
            j=_j.loads(res.stdout.strip().splitlines()[-1])
            return {"valid": j.get("valid",True), "gain_pct": j.get("gain_pct",0), "trades": j.get("trades",0), "pool_sharpe": j.get("pool_sharpe",0)}
        return {"valid": False, "gain_pct": 0, "trades": 0}
    except Exception as e:
        return {"valid": False, "gain_pct": 0, "trades": 0}

# Load overrides cache
import json as js
def load_overrides(symside):
    for p in [ROOT/"data/hourly_reconfig/per_sym_active_config.json", ROOT/"data/hourly_reconfig/trb/active_config.json", Path("/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_active_config.json"), Path("/home/niels/binance-sandbox/data/hourly_reconfig/trb/active_config.json")]:
        if p.exists():
            j=js.loads(open(p).read())
            if symside in j:
                ov=j[symside].get("overrides") or {}
                return {k:v for k,v in ov.items() if not k.startswith("_")}
    return {}

start=time.time()
last_save=time.time()
count=0
for r in rows:
    key=(r['sym'], r['switch'], r['candidate'], r['window'])
    if key in done:
        continue
    sym=r['sym']
    # sym is like BNBUSDC, but need side? For BNB file, sym includes side already? In BNB file, sym is BNBUSDC etc without side, but CRWD_SHORT includes side.
    # Detect side from summary.csv sym field: it is like BNBUSDC (without side) or CRWD_SHORT (with side)
    # Our prove stored symside_full as key, but csv sym is that full.
    symside=r['sym']
    if symside.endswith("_LONG") or symside.endswith("_SHORT"):
        sym_base, side = symside.rsplit("_",1)
    else:
        sym_base, side = symside, "LONG"
        symside=symside+"_LONG"
    overrides=load_overrides(symside)
    cand=r['candidate']
    if cand in ("True","False"):
        cand = cand=="True"
    ov=dict(overrides)
    ov[r['switch']]=cand
    wd=int(r['window'])
    # vector already known, but recompute for parity? Use stored vec
    # For live, evaluate
    live=eval_live(symside, ov, wd)
    # baseline live
    base_live=eval_live(symside, overrides, wd)
    # compute live delta with trades guard
    live_tr=int(live.get("trades",0) or 0)
    base_tr=int(base_live.get("trades",0) or 0)
    live_delta=float(live.get("gain_pct",0) or 0)-float(base_live.get("gain_pct",0) or 0)
    if live_tr<2 or not live.get("valid"):
        live_delta=0.0
        live_filling=False
    else:
        live_filling= live_delta!=0
    # append
    out_row=[r['sym'], r['switch'], r['candidate'], r['window'], r['F_vec_delta'], f"{live_delta:.4f}", r['F_filling'], str(live_filling), r['vec_trades'], str(live_tr), f"{abs(float(r['F_vec_delta'] or 0)-live_delta):.4f}", str(live.get("valid")), "SUSP" if r['switch'] in ["DELTA_GATE_BB_SQUEEZE"] else ""]
    with OUT_CSV.open("a") as f:
        f.write(",".join(out_row)+"\n")
    count+=1
    done.add(key)
    # progress print
    if count%10==0:
        print(f"[{count}/{len(rows)}] {symside} {r['switch']} {wd}d vec {r['F_vec_delta']} live {live_delta:.4f} tr{live_tr} {'F' if live_filling else '.'} {time.time()-start:.0f}s")
    # save every 10min
    if time.time()-last_save>600:
        # also update xlsx
        try:
            wb=openpyxl.load_workbook(str(OUT_XLSX))
            if "LIVE_VS_VECTOR_BNB" in wb.sheetnames:
                ws=wb["LIVE_VS_VECTOR_BNB"]
                # append last 10 rows
                # find max row
                for rr in list(csv.DictReader(open(OUT_CSV)))[-10:]:
                    pass
                wb.save(OUT_XLSX)
                print(f"saved xlsx at {time.time()-start:.0f}s")
        except Exception as e:
            print(f"xlsx save failed {e}")
        last_save=time.time()
        # also sync to S1
        import subprocess as sp
        sp.run(["rsync","-az",str(OUT_CSV), "niels@157.180.125.52:~/binance-sandbox/data/reports/delta_proof_full_357_bnb_nvda_sndk_mu_short/summary_live.csv"], capture_output=True)
        sp.run(["rsync","-az",str(OUT_XLSX), "niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/TEMPLATE_V2_BNB_AUDIT_OPAQUED.xlsx"], capture_output=True)

print(f"done {count} live, total {len(rows)}")

import json, os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_WIRED={"TECHNICAL_DC_STOP_TF","TECHNICAL_DC_TARGET_TF","TECHNICAL_DC_STOP_BUFFER_PCT","TECHNICAL_DC_TARGET_BUFFER_PCT","WT_LOWER_CROSS_EXIT_TF","ENTRY_DC_TF","ENTRY_DC_BUFFER_PCT","EMA_9_21_FILTER_ENABLED","EMA_9_21_FILTER_FILTER_TF"}
VEC_ONLY={"WT_DC_ENABLED","WT_DC_DETAILED_SCORER_ENABLED","WT_DC_TF_ENTRY","WT_DC_DC_TF","BB_SQUEEZE_ENTRY_ENABLED","BB_SQUEEZE_ENTRY_TF","BB_SQUEEZE_EXIT_ENABLED"}
def load(p):
    out=[]
    if p.endswith(".jsonl") and os.path.exists(p):
        for l in open(p):
            try: out.append(json.loads(l))
            except: pass
    elif os.path.exists(p):
        out=json.load(open(p)).get("results",[])
    return out
res=load(os.path.join(ROOT,"data/reports/dc64_walkforward_crypto.jsonl"))+load(os.path.join(ROOT,"data/reports/dc64_walkforward_stocks.json"))
promos=[]
for r in res:
    if not r.get("robust"): continue
    # pick switch set from longest-IS generalizing scenario (most stable)
    sw=None
    for sc in ("90/30","60/30","120/60","30/30","7/7"):
        s=r.get("scenarios",{}).get(sc,{})
        if s.get("generalizes") and s.get("switches"): sw=s["switches"]; break
    if not sw:
        for sc in r.get("scenarios",{}).values():
            if sc.get("switches"): sw=sc["switches"]; break
    if not sw: continue
    pure = all(k in LIVE_WIRED for k in sw)
    promos.append({"sym_side":r["sym_side"],"venue":r.get("venue"),"pass":pure,
        "applied":sw,"live_safe":pure,"needs_wiring":[k for k in sw if k in VEC_ONLY],
        "d365":r.get("compass_365d",{}).get("delta"),"oos_mean":r.get("oos_mean_delta"),
        "base_gain30":0,"final_gain30":0,"live_gain30":r.get("compass_365d",{}).get("delta")})
json.dump({"n_pass":len(promos),"n_total":len(promos),"results":promos},open(os.path.join(ROOT,"data/reports/dc64_promotions.json"),"w"),indent=1)
ls=[p for p in promos if p["live_safe"]]; nw=[p for p in promos if not p["live_safe"]]
print(f"ROBUST promotions: {len(promos)} total | {len(ls)} live-safe (exit switches only) | {len(nw)} need live-wiring (WT_DC/BB)")
print("--- LIVE-SAFE (directly promotable) ---")
for p in sorted(ls,key=lambda x:-(x['d365'] or 0)): print(f"  {p['sym_side']:20s} [{p['venue']}] 365D_Δ {p['d365']:+} switches={list(p['applied'].keys())}")
print("--- NEED WIRING (WT_DC/BB vec-only, hold) ---")
for p in nw: print(f"  {p['sym_side']:20s} needs={p['needs_wiring']}")

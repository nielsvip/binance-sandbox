"""reentry trace: published set + frozen NPZ through a TRACE copy of the deployed engine (scratch, never deployed)."""
import json,os,sys,importlib.util,collections
R=os.path.expanduser("~/binance-sandbox"); sys.path.insert(0,R); os.chdir(R)
ss=sys.argv[1]; prog_path=sys.argv[2]; trace_engine=sys.argv[3]; extra=json.loads(sys.argv[4]) if len(sys.argv)>4 else {}
spec=importlib.util.spec_from_file_location("v12_quick_engine", trace_engine); V=importlib.util.module_from_spec(spec); sys.modules["v12_quick_engine"]=V; spec.loader.exec_module(V)
import tools.augment_family_probe as P
from pathlib import Path
prog=json.load(open(os.path.expanduser(prog_path)))
ov=dict(prog["cumulative_overrides"]); ov.update(extra)
sym=ss.rsplit("_",1)[0]
P._install_frozen_npz(Path(os.path.expanduser(f"~/aug_probe_20261020/npz/{ss}")), sym, prog["npz_id"]["md5"])
from tools.opt.lifecycle_pilot import evaluate_month
V._RT.clear()
r=evaluate_month(ss, ov, include_ledger=True, window_days=30)
led=[t for t in (r.get("execution_ledger") or []) if t.get("pnl_dollars") is not None]
RT=list(V._RT)
n_re=sum(1 for t in led if "REENTR" in str(t.get("entry_reason","")).upper())
post=sum(1 for k,t in enumerate(led) if k>0)
print("RESULT "+json.dumps({"ss":ss,"extra":extra,"engine":os.path.basename(trace_engine),"gain":round(r.get("gain_pct") or 0,3),"net_usd":round(sum(float(t.get("pnl_dollars") or 0) for t in led),2),"trades":len(led),"reentries":n_re,"tim":r.get("tim_pct"),"dd":round(float(r.get("max_dd_pct") or 0),2),"wr":round(float(r.get("wr_pct") or 0),1),"valid":r.get("valid")}))
src=open(trace_engine).read().split("\n")
def lbl(L):
    # nearest comment/context for a veto line
    s=src[L-1].strip()
    for k in range(L-1, max(0,L-12), -1):
        c=src[k-1]
        if "#" in c and ("fire" in c or "block" in c.lower() or "gate" in c.lower() or "veto" in c.lower() or "twin" in c.lower()):
            return f"L{L} {s[:60]} | {c.strip()[:110]}"
    return f"L{L} {s[:120]}"
# window per exit: bars between bar_exit and next bar_entry
# simulate_one bar index i is in the sim window; ledger bar_exit same index
summary=[]
for k,t in enumerate(led):
    x=int(t["bar_exit"]); nxt=int(led[k+1]["bar_entry"]) if k+1<len(led) else 10**9
    ev=[e for e in RT if x<e[0]<=min(nxt,x+64)]
    c=collections.Counter()
    for e in ev:
        if e[1]=="pre": c["pre_fire" if e[2] else "pre_nofire"]+=1; 
        if e[1]=="pre" and e[2]: c["src:"+str(e[3])]+=1
        if e[1]=="veto": c["veto:"+lbl(e[2])]+=1
        if e[1]=="skip": c["skip:"+lbl(e[2])]+=1
        if e[1] in ("cd","vig_block"): c[e[1]]+=1
    summary.append({"exit_bar":x,"exit_reason":t.get("exit_reason","")[:50],"exit_px":t.get("exit_price"),"next_entry_bar":(nxt if nxt<10**9 else None),"next_entry_reason":(led[k+1].get("entry_reason") if k+1<len(led) else None),"bars_scanned":min(nxt,x+64)-x,"events":dict(c.most_common(14))})
json.dump({"engine":os.path.basename(trace_engine),"ss":ss,"extra":extra,"result":{"gain":r.get("gain_pct"),"trades":len(led)},"per_exit":summary},open(os.path.expanduser(f"~/aug_probe_20261020/reentry_trace_{ss}_{os.path.basename(trace_engine)[:-3]}.json"),"w"),indent=1,default=str)


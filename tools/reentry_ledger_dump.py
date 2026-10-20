import json,os,sys,importlib.util
R=os.path.expanduser("~/binance-sandbox"); sys.path.insert(0,R); os.chdir(R)
ss=sys.argv[1]; eng=sys.argv[2]; extra=json.loads(sys.argv[3]) if len(sys.argv)>3 else {}
spec=importlib.util.spec_from_file_location("v12_quick_engine", eng); V=importlib.util.module_from_spec(spec); sys.modules["v12_quick_engine"]=V; spec.loader.exec_module(V)
import tools.augment_family_probe as P
from pathlib import Path
prog=json.load(open(os.path.expanduser(f"~/v15_run33_20261019/progress/{ss}_v14_progress.json")))
ov=dict(prog["cumulative_overrides"]); ov.update(extra)
P._install_frozen_npz(Path(os.path.expanduser(f"~/aug_probe_20261020/npz/{ss}")), ss.rsplit("_",1)[0], prog["npz_id"]["md5"])
from tools.opt.lifecycle_pilot import evaluate_month
r=evaluate_month(ss, ov, include_ledger=True, window_days=30)
led=[t for t in r["execution_ledger"] if t.get("pnl_dollars") is not None]
prev=None
for t in led:
    xb=""
    if prev:
        ex=float(prev["exit_price"]); en=float(t["entry_price"])
        xb=f"prev_exit {ex:.2f} entry {en:.2f} ({(en/ex-1)*100:+.2f}%)"
    print(f"{t['bar_entry']:4d}-{t['bar_exit']:4d} {str(t.get('entry_reason'))[:28]:28s} pnl% {float(t['pnl_pct']):+6.2f} ${float(t['pnl_dollars']):+8.2f}  {xb}  exit:{str(t.get('exit_reason'))[:30]}")
    prev=t
print("gain",r["gain_pct"],"trades",len(led))

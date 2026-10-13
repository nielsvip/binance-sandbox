#!/usr/bin/env python3
"""Boards finished per hour on this host (reads ~/v15_run3*/progress result_done_utc). Usage: python3 tools/v15_rate.py"""
import json,glob,os,datetime as dt,sys
now=dt.datetime.now(dt.timezone.utc)
rows=[]
for p in glob.glob(os.path.expanduser("~/v15_run3*/progress/*_v14_progress.json")):
    try: d=json.load(open(p))
    except Exception: continue
    t=d.get("result_done_utc")
    if not t: continue
    try: T=dt.datetime.fromisoformat(t.replace("Z","+00:00"))
    except Exception: continue
    if T.tzinfo is None: T=T.replace(tzinfo=dt.timezone.utc)
    s=d.get("result_start_utc")
    try: S=dt.datetime.fromisoformat(s.replace("Z","+00:00")); S=S if S.tzinfo else S.replace(tzinfo=dt.timezone.utc)
    except Exception: S=None
    rows.append((T,S,os.path.basename(p)[:-17],d.get("final_gain"),d.get("bh")))
for h in (1,2,3,6):
    n=[r for r in rows if (now-r[0]).total_seconds()<h*3600]
    print(f"last {h}h: {len(n)} finished ({len(n)/h:.1f}/h)")
rec=sorted([r for r in rows if (now-r[0]).total_seconds()<3*3600])
for T,S,ss,g,bh in rec: print(T.strftime("%H:%M"), ss, "wall_min", round((T-S).total_seconds()/60,1) if S else None, "gain",g if g is None else round(g,2),"bh",bh if bh is None else round(bh,2))

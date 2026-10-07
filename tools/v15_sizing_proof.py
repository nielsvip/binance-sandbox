import json, glob, os, datetime as dt, statistics, collections
R="/Users/niels/Documents/binance"
now=dt.datetime.now(dt.timezone.utc); cut=now-dt.timedelta(days=7)
CRYPTO=("flz","men","ang","inf","fin"); STOCK=("tra","trb","trc")
tc=json.load(open(f"{R}/data/daily_chain/20261006_tradeable_check_DRYRUN.json"))["rows"]
gl=json.load(open(f"{R}/data/daily_chain/persym_golive_20261006.json"))["sym_sides"]
conf=json.load(open(f"{R}/data/confirmed_365d.json"))
bookc=json.load(open(f"{R}/data/hourly_reconfig/per_sym_active_config.json"))
books=json.load(open(f"{R}/data/hourly_reconfig/per_sym_active_config_stocks.json"))
fb=json.load(open(f"{R}/data/persym_final_book.json")).get("tradeable",{})
rows=[]
for acct in CRYPTO+STOCK:
    for f in glob.glob(f"{R}/data/history/{acct}/*.jsonl"):
        ss=os.path.basename(f)[:-6]
        opens=[]
        for l in open(f):
            try: e=json.loads(l); t=dt.datetime.fromisoformat(str(e["ts"]).replace("Z","+00:00"))
            except Exception: continue
            if t>=cut and str(e.get("type","")).upper() in ("OPEN","REENTRY","QUICK_OPEN"):
                opens.append(float(e.get("value") or 0))
        if not opens: continue
        g30=None
        if ss in tc and tc[ss].get("gain_pct") is not None: g30=tc[ss]["gain_pct"]
        elif ss in gl and (gl[ss].get("evidence") or {}).get("gain_pct") is not None: g30=gl[ss]["evidence"]["gain_pct"]
        g365=(conf.get(ss) or {}).get("gain_365d")
        book=bookc if acct in CRYPTO else books
        ov=(book.get(ss) or {}).get("overrides") or {}
        base=ov.get("START_POSITION_SIZE_OVERRIDE_USD") or ov.get("START_POSITION_SIZE") or (28.0 if acct in CRYPTO else (330.0 if acct=="trc" else 500.0))
        sm=(fb.get(ss) or {}).get("size_mult")
        conv=max(1.0,min(float(sm),8.0)) if sm is not None else 1.0
        rows.append((acct,ss,g30,g365,(book.get(ss) or {}).get("acc_gain_pct"),round(float(base)*conv,1),conv,len(opens),round(statistics.median(opens),1)))
rows.sort(key=lambda r:(r[0] in CRYPTO, -(r[2] if r[2] is not None else -999)))
print("acct sym_side g30_fresh g365 book_acc_gain configured_size conv_mult n_opens_7d median_open_notional")
for r in rows: print(*[("%.2f"%x if isinstance(x,float) else x) for x in r])
neg=[r for r in rows if r[2] is not None and r[2]<=0]; pos=[r for r in rows if r[2] is not None and r[2]>0]
def med(x): return round(statistics.median(x),1) if x else None
for venue,accts in (("stocks",STOCK),("crypto",CRYPTO)):
    n=[r[8] for r in neg if r[0] in accts]; p=[r[8] for r in pos if r[0] in accts]
    top=sorted([r for r in pos if r[0] in accts],key=lambda r:-r[2])[:5]
    print(venue,"neg30 n=",len(n),"median notional",med(n),"| pos30 n=",len(p),"median",med(p),"| top5 median",med([r[8] for r in top]))

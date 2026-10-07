#!/usr/bin/env python3
"""Throttled USDC 15m until 300d — runs alongside USDT clone. Switch when USDC everywhere."""
import json, time, requests, os, subprocess
from pathlib import Path
import datetime as dt
# Support both S1 (~/binance-sandbox) and Mac (~/Documents/binance)
for p in [Path.home()/"binance-sandbox", Path.home()/"Documents/binance", Path("/Users/niels/Documents/binance")]:
    if (p/"klines_cache").exists():
        ROOT=p
        break
else:
    ROOT=Path.home()/"binance-sandbox"
USDC_SYMS=[p.stem.replace("_15m","") for p in sorted((ROOT/"klines_cache").glob("*USDC_15m.json"))]
if not USDC_SYMS:
    USDC_SYMS=[p.stem.replace("_15m","") for p in sorted((ROOT/"klines_cache"/"tradier").glob("*USDC_15m.json"))]
PRIORITY=["BTCUSDC","ETHUSDC","SOLUSDC","BNBUSDC","XRPUSDC","ADAUSDC","AVAXUSDC","DOGEUSDC","LINKUSDC","UNIUSDC","LTCUSDC","BCHUSDC","HBARUSDC","SUIUSDC","TIAUSDC","ARBUSDC","CRVUSDC","ENSUSDC","ORDIUSDC","WIFUSDC","ZECUSDC","AAVEUSDC"]
ordered=[s for s in PRIORITY if s in USDC_SYMS] + [s for s in USDC_SYMS if s not in PRIORITY]
print(f"[{dt.datetime.now(dt.timezone.utc).isoformat()}] USDC ordered {len(ordered)} {ordered[:5]} ROOT={ROOT}")
for sym in ordered:
    klines_path=ROOT/"klines_cache"/f"{sym}_15m.json"
    # Also check tradier subdir for USDC? No, USDC are crypto top-level
    if not klines_path.exists():
        klines_path=ROOT/"klines_cache"/"tradier"/f"{sym}_15m.json"
    existing=json.load(open(klines_path)) if klines_path.exists() else []
    if existing:
        t0=dt.datetime.fromisoformat(existing[0]["timestamp"].replace("Z","+00:00"))
        t1=dt.datetime.fromisoformat(existing[-1]["timestamp"].replace("Z","+00:00"))
        span=(t1-t0).total_seconds()/86400
        if span>=300:
            print(f"  {sym} span {span:.1f}d READY — skip")
            continue
        print(f"  {sym} span {span:.1f}d collecting — throttled 1 req/2s")
    else:
        print(f"  {sym} no existing — collecting")
    url="https://fapi.binance.com/fapi/v1/klines"
    params={"symbol": sym, "interval": "15m", "limit": 500}
    start=time.time()
    try:
        r=requests.get(url, params=params, timeout=10)
        elapsed=time.time()-start
        print(f"    {sym} status {r.status_code} weight {r.headers.get('X-MBX-USED-WEIGHT-1M')} elapsed {elapsed:.2f}s")
        if r.status_code==200:
            data=r.json()
            new_rows=[{"timestamp": dt.datetime.fromtimestamp(x[0]/1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"), "open": float(x[1]), "high": float(x[2]), "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])} for x in data]
            by_ts={x["timestamp"]: x for x in existing}
            for row in new_rows:
                by_ts[row["timestamp"]]=row
            merged=sorted(by_ts.values(), key=lambda x: x["timestamp"])
            t0=dt.datetime.fromisoformat(merged[0]["timestamp"].replace("Z","+00:00"))
            t1=dt.datetime.fromisoformat(merged[-1]["timestamp"].replace("Z","+00:00"))
            span=(t1-t0).total_seconds()/86400
            # Atomic merge without truncation
            if len(merged)>=len(existing):
                tmp=klines_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(merged))
                os.replace(tmp, klines_path)
                print(f"    {sym} merged {len(existing)}->{len(merged)} span {span:.1f}d {'READY' if span>=300 else 'collecting'} atomic OK")
                # rsync to gateway/S5/Macbook
                for dst in ["niels@10.0.0.3:~/binance-sandbox/klines_cache/", "niels@10.0.0.5:~/binance-sandbox/klines_cache/", "niels@10.0.0.3:~/binance-sandbox/klines_cache/tradier/"]:
                    try:
                        subprocess.run(["rsync","-az",str(klines_path), dst], timeout=10, capture_output=True)
                        print(f"      rsync {dst} OK")
                    except Exception as e:
                        print(f"      rsync {dst} fail {e}")
            else:
                print(f"    {sym} would truncate {len(existing)}->{len(merged)} — BLOCKED")
        elif r.status_code in (418,429,403):
            wait=int(r.headers.get("Retry-After","60"))
            print(f"    {sym} banned {r.status_code} wait {wait}s")
            time.sleep(wait)
        else:
            print(f"    {sym} error {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"    {sym} exc {e}")
    # Throttle 1 req/2s
    sleep_left=2 - (time.time()-start)
    if sleep_left>0:
        time.sleep(sleep_left)
    print(f"    {sym} throttled {time.time()-start:.2f}s total")
print(f"[{dt.datetime.now(dt.timezone.utc).isoformat()}] throttled batch done — both collections running")

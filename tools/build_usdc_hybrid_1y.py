#!/usr/bin/env python3
"""Build 1y hybrid USDC klines: USDC where available (recent 18d), USDT adjusted for older 347d."""
import json, pathlib
from pathlib import Path
import datetime as dt

ROOT_CANDIDATES=[Path.home()/"binance-sandbox", Path.home()/"Documents/binance", Path("/Users/niels/Documents/binance")]
for p in ROOT_CANDIDATES:
    if (p/"klines_cache").exists() and list((p/"klines_cache").glob("*USDC_15m.json")):
        ROOT=p
        break
else:
    ROOT=Path.home()/"binance-sandbox"

USDC_FILES=list((ROOT/"klines_cache").glob("*USDC_15m.json"))
USDC_SYMS=[p.stem.replace("_15m","") for p in USDC_FILES]
print(f"USDC syms {len(USDC_SYMS)} ROOT={ROOT}")

for sym_usdc in sorted(USDC_SYMS):
    sym_usdt=sym_usdc.replace("USDC","USDT")
    usdc_path=ROOT/"klines_cache"/f"{sym_usdc}_15m.json"
    usdt_path=ROOT/"klines_cache"/f"{sym_usdt}_15m.json"
    if not usdt_path.exists():
        print(f"{sym_usdc} no USDT counterpart {sym_usdt} skip")
        continue
    usdc=json.load(open(usdc_path))
    usdt=json.load(open(usdt_path))
    if not usdc or not usdt:
        continue
    # Find first USDC timestamp
    first_usdc_ts=usdc[0]["timestamp"]
    first_usdc_close=usdc[0]["close"]
    # Find USDT close at same timestamp or nearest before
    usdt_map={x["timestamp"]: x for x in usdt}
    # Try exact match, else find closest before
    if first_usdc_ts in usdt_map:
        usdt_at_first=usdt_map[first_usdc_ts]["close"]
    else:
        # Find USDT with max timestamp < first_usdc_ts
        candidates=[x for x in usdt if x["timestamp"] < first_usdc_ts]
        if candidates:
            usdt_at_first=candidates[-1]["close"]
        else:
            usdt_at_first=usdt[0]["close"]
    ratio=first_usdc_close / usdt_at_first if usdt_at_first else 1.0
    # Clamp ratio to avoid extreme jumps (0.995-1.005 is typical, allow 0.99-1.01)
    if not (0.98 < ratio < 1.02):
        print(f"{sym_usdc} ratio {ratio:.4f} extreme, capping to 1.0")
        ratio=1.0
    # Build hybrid: USDC wins on overlap, USDT (adjusted) for older
    by_ts={}
    # Add adjusted USDT for all before first USDC
    for x in usdt:
        if x["timestamp"] < first_usdc_ts:
            # Adjust OHLC by ratio
            adj={k: v for k,v in x.items()}
            for k in ("open","high","low","close"):
                adj[k]=float(x[k])*ratio
            by_ts[x["timestamp"]]=adj
        elif x["timestamp"] not in {y["timestamp"] for y in usdc}:
            # USDT after first USDC but not in USDC - keep USDT (should be rare, but USDC wins)
            # Only add if not overlapping with USDC
            if x["timestamp"] not in {y["timestamp"] for y in usdc}:
                by_ts[x["timestamp"]]=x
    # Add all USDC (wins)
    for x in usdc:
        by_ts[x["timestamp"]]=x
    merged=sorted(by_ts.values(), key=lambda x: x["timestamp"])
    t0=dt.datetime.fromisoformat(merged[0]["timestamp"].replace("Z","+00:00"))
    t1=dt.datetime.fromisoformat(merged[-1]["timestamp"].replace("Z","+00:00"))
    span=(t1-t0).total_seconds()/86400
    # Only write if we achieve >=300d and not truncating recent USDC
    if span>=300 and len(merged) >= len(usdc):
        # Backup original USDC
        backup=usdc_path.with_suffix(".usdc_pure.json")
        if not backup.exists():
            backup.write_text(json.dumps(usdc))
        # Write hybrid as USDC file (called USDC but hybrid)
        tmp=usdc_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged))
        import os
        os.replace(tmp, usdc_path)
        print(f"{sym_usdc} hybrid {len(usdc)}+{len(usdt)}->{len(merged)} span {span:.1f}d ratio {ratio:.4f} {'READY' if span>=365 else 'collecting'}")
    else:
        print(f"{sym_usdc} skip span {span:.1f}d len {len(merged)}")

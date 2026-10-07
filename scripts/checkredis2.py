import asyncio
import os
import json
import time
from redis.asyncio import Redis

async def check():
    r = Redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
    keys = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC"]
    
    print(f"{'SYMBOL':<10} | {'PRICE':<10} | {'TS (Epoch)':<15} | {'1m':<8} | {'3m':<8} | {'STATUS':<10}")
    print("-" * 80)
    
    for sym in keys:
        raw = await r.get(f"hot_metrics:{sym}")
        if raw:
            try:
                data = json.loads(raw)
                ts = float(data.get('_tick_ts', 0) or data.get('ts', 0))
                price = data.get('price', 0)
                
                k1 = int(data.get('k1', 0))
                d1 = int(data.get('d1', 0))
                k3 = int(data.get('k3', 0))
                d3 = int(data.get('d3', 0))
                
                lag = time.time() - ts
                status = "✅ OK" if lag < 2.0 else f"❌ STALE ({lag:.1f}s)"
                
                print(f"{sym:<10} | {price:<10.4f} | {ts:<15.1f} | {k1}/{d1:<5} | {k3}/{d3:<5} | {status}")
            except Exception as e:
                print(f"{sym:<10} | ERROR: {e}")
        else:
            print(f"{sym:<10} | NO DATA (Check ez_market_data.py)")

    await r.aclose()

if __name__ == "__main__":
    asyncio.run(check())
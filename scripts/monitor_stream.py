import asyncio
import os
import sys
import time
import json
from datetime import datetime
from redis.asyncio import Redis

# --- CONFIG ---
REDIS_URL = "redis://127.0.0.1:6379/0" # Change IP if running remotely!
# If running locally on Mac connecting to remote Redis, put server IP here:
# REDIS_URL = "redis://157.180.125.52:6379/0" 

WATCH_LIST = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC"]

async def monitor():
    r = Redis.from_url(REDIS_URL, decode_responses=True)
    
    print(f"Connecting to Redis...")
    try:
        await r.ping()
        print("✅ Connected.")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return

    # --- CLOCK SYNC (The Magic Fix) ---
    print("⏱️  Synchronizing clocks with server data...")
    offsets = []
    for _ in range(5):
        raw = await r.get("hot_metrics:BTCUSDC")
        if raw:
            data = json.loads(raw)
            ts_server = float(data.get('ts', 0) or data.get('_tick_ts', 0))
            if ts_server > 0:
                # Difference between My Time and Server Time
                offsets.append(time.time() - ts_server)
        await asyncio.sleep(0.5)
    
    if not offsets:
        print("❌ Could not sync clocks (No BTC data).")
        avg_offset = 0
    else:
        # We assume the server data is 'fresh' relative to the server.
        # The average difference is mostly Clock Skew + Network Latency.
        avg_offset = sum(offsets) / len(offsets)
        print(f"✅ Clock Offset detected: {avg_offset:.3f}s (Adjusting display)")

    print("\n" * 2)

    try:
        while True:
            sys.stdout.write(f"\033[{len(WATCH_LIST)+6}A") 
            
            print(f"{'SYMBOL':<10} | {'PRICE':<10} | {'TRUE LAG':<10} | {'1m K/D':<12} | {'3m K/D':<12} | {'STATUS':<10}")
            print("-" * 80)

            for sym in WATCH_LIST:
                key = f"hot_metrics:{sym}"
                raw_data = await r.get(key)
                
                if not raw_data:
                    print(f"{sym:<10} | {'WAITING...':<65}")
                    continue

                try:
                    data = json.loads(raw_data)
                    
                    price = float(data.get('price', 0))
                    k1 = float(data.get('k1', 0))
                    d1 = float(data.get('d1', 0))
                    k3 = float(data.get('k3', 0))
                    d3 = float(data.get('d3', 0))
                    ts_server = float(data.get('ts', 0) or data.get('_tick_ts', 0))
                    
                    # --- CALCULATE TRUE LAG ---
                    # (Local Time - Server TS) - Clock Offset
                    raw_diff = time.time() - ts_server
                    true_lag_ms = (raw_diff - avg_offset) * 1000
                    
                    # Clamp negative lag (caused by jitter) to 0
                    true_lag_ms = max(0.0, true_lag_ms)
                    
                    k1_str = f"{k1:.0f}/{d1:.0f}"
                    k3_str = f"{k3:.0f}/{d3:.0f}"
                    
                    status = "⚡ LIVE"
                    color = "\033[92m" # Green
                    
                    # Thresholds (Tightened for "True Lag")
                    if true_lag_ms > 2500:
                        status = "⚠️ SLOW"
                        color = "\033[93m" 
                    if true_lag_ms > 4000:
                        status = "❌ STALE"
                        color = "\033[91m" 

                    reset = "\033[0m"

                    print(f"{sym:<10} | {price:<10.4f} | {color}{true_lag_ms:>6.0f} ms{reset} | {k1_str:<12} | {k3_str:<12} | {color}{status:<10}{reset}")

                except Exception as e:
                    print(f"{sym:<10} | ERROR: {e}")

            print(f"-" * 80)
            print(f"Offset: {avg_offset:.2f}s | Local Time: {datetime.now().strftime('%H:%M:%S')}")
            
            await asyncio.sleep(0.1)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        await r.close()

if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')
    asyncio.run(monitor())
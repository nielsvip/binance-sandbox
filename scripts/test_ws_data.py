import asyncio
import aiohttp
import orjson
from datetime import datetime

async def debug_mark_price():
    # Use a simpler single-stream URL to avoid overload
    url = "wss://fstream.binance.com/ws/btcusdt@markPrice"
    print(f"🔌 Connecting to {url}...")
    
    async with aiohttp.ClientSession() as session:
        # ssl=False fixes Mac hang, timeout prevents infinite wait
        async with session.ws_connect(url, ssl=False, timeout=10) as ws:
            print("✅ Connected. Waiting for data...")
            
            count = 0
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = orjson.loads(msg.data)
                    print(f"\n📩 Message {count+1}:")
                    print(f"   Symbol: {data.get('s')}")
                    print(f"   Price:  {data.get('p')}")
                    print(f"   Event Time (E): {data.get('E')} (Type: {type(data.get('E'))})")
                    
                    # Manual Verify
                    ts = data.get('E') / 1000.0
                    print(f"   Converted Time: {datetime.fromtimestamp(ts)}")
                    
                    count += 1
                    if count >= 3:
                        print("\n🛑 Test Complete.")
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    print(f"❌ Connection Closed/Error: {msg.type}")
                    break

if __name__ == "__main__":
    try:
        asyncio.run(debug_mark_price())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
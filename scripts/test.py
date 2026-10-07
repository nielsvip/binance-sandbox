import asyncio
from redis.asyncio import Redis

async def test_redis():
    print("--- Testing Redis Connection ---")
    try:
        # Try connecting to localhost default
        r = Redis(host='localhost', port=6379, db=0, decode_responses=True)
        
        # 1. Ping
        latency = await r.ping()
        print(f"✅ Ping successful: {latency}")

        # 2. Write
        await r.set("hot_metrics", "{\"price\": 100, \"ts\": 12345}", ex=60)
        print("✅ Wrote key 'hot_metrics:TEST'")

        # 3. Read
        val = await r.get("hot_metrics:TEST")
        print(f"✅ Read back: {val}")

        # 4. Check Key List
        keys = await r.keys("hot_metrics:*")
        print(f"✅ Found keys matching pattern: {keys}")

        await r.aclose()
    except Exception as e:
        print(f"❌ Redis Failure: {e}")
        print("Is Redis running? Try: brew services start redis (on Mac) or sudo systemctl start redis (on Linux)")

if __name__ == "__main__":
    asyncio.run(test_redis())
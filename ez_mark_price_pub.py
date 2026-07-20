"""Standalone mark price publisher for S1 — writes mark_price:{symbol} to Redis."""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
import aiohttp
import redis.asyncio as redis_lib

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("mark_price_pub")

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 0
TTL = 300
URL = "wss://fstream.binance.com/market/stream?streams=!markPrice@arr@1000ms"


async def main():
    redis_client = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
    backoff = 5
    published = 0
    last_log = time.time()
    while True:
        try:
            session = aiohttp.ClientSession()
            try:
                async with session.ws_connect(URL, heartbeat=30, timeout=60.0) as ws:
                    logger.info("Connected to !markPrice@arr stream")
                    backoff = 5
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                payload = json.loads(msg.data)
                                items = payload.get("data", payload)
                                if not isinstance(items, list):
                                    items = [items]
                                ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                                pipe = redis_client.pipeline()
                                for item in items:
                                    sym = item.get("s", "")
                                    price = item.get("p") or item.get("mp")
                                    if sym and price:
                                        val = json.dumps({"price": float(price), "timestamp": ts, "symbol": sym})
                                        pipe.set(f"mark_price:{sym}", val, ex=TTL)
                                        published += 1
                                await pipe.execute()
                                if time.time() - last_log > 60:
                                    logger.info(f"Published {published} mark prices (cumulative)")
                                    published = 0
                                    last_log = time.time()
                            except Exception as e:
                                logger.debug(f"msg parse error: {e}")
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            logger.warning("WS closed/error, reconnecting")
                            break
            finally:
                await session.close()
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Stream error: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

if __name__ == "__main__":
    asyncio.run(main())

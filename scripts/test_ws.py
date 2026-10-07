import asyncio
import time

import aiohttp
import orjson


async def test_ws():
    url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"
    print(f"Connecting to {url}")
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            print("Connected!")
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = orjson.loads(msg.data)
                    for p in data:
                        if p['s'] == 'BTCUSDC':
                            event_ts = p['E'] / 1000.0
                            now = time.time()
                            print(f"BTCUSDC Price: {p['p']} at {event_ts:.2f} (Lag: {now - event_ts:.2f}s)")
                            return
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print("Error")
                    break

if __name__ == '__main__':
    asyncio.run(test_ws())

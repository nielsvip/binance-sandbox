import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import redis.asyncio as redis

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("poly_scanner")

class PolyScanner:
    def __init__(self):
        self.gamma_url = "https://gamma-api.polymarket.com/markets"
        self.redis_client = None
        self.data_dir = Path("./data/poly")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Thresholds for "Safe" and "Fast" markets
        self.MIN_LIQUIDITY = 5000   # At least $5k in the order book
        self.MIN_VOLUME_24H = 20000 # At least $20k traded in last 24h
        self.MAX_MARKETS = 100      # Limit how many symbols we track

    async def connect_redis(self):
        try:
            self.redis_client = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
            await self.redis_client.ping()
        except Exception as e:
            logger.error(f"Redis Connection Failed: {e}")

    async def fetch_active_markets(self):
        """Fetch markets from Gamma API sorted by 24h volume"""
        params = {
            "active": "true",
            "closed": "false"
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(self.gamma_url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"Gamma API Error: {resp.status}")
                        return []
            except Exception as e:
                logger.error(f"Scanner Request Failed: {e}")
                return []
    async def run_scan(self):
        markets = await self.fetch_active_markets()
        poly_symbols = {}

        for m in markets:
            token_ids = json.loads(m.get('clobTokenIds', '[]'))
            if len(token_ids) < 2: continue
            
            # Mapping for the Manager
            # We use the Ticker or Question as the 'Symbol'
            symbol = m.get('ticker', f"PM:{m['id']}")
            
            poly_symbols[symbol] = {
                "market_id": m['id'],
                "question": m['question'],
                "yes_token": token_ids[0], # The "Long" asset
                "no_token": token_ids[1],  # The "Short" asset
                "min_increment": m.get('minimum_order_size', 1),
                "end_date_iso": m.get('endDateIso'),
                "liquidity_num": m.get('liquidityNum', 0)
            }

        await self.redis_client.set("poly_symbols_map", json.dumps(poly_symbols))
        # Create a flat list for the Price Fetcher
        all_tokens = []
        for s in poly_symbols.values():
            all_tokens.extend([s['yes_token'], s['no_token']])
        await self.redis_client.set("poly_monitored_tokens", json.dumps(all_tokens))

    async def main_loop(self):
        await self.connect_redis()
        while True:
            await self.run_scan()
            # Scan every 10 minutes (Discovery doesn't need to be 1s)
            await asyncio.sleep(600)

if __name__ == "__main__":
    scanner = PolyScanner()
    asyncio.run(scanner.main_loop())
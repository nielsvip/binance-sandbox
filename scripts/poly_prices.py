import asyncio
import json
import logging
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import aiofiles
import redis.asyncio as redis
# Polymarket SDK
from py_clob_client.client import ClobClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg  # Your existing GPG helper

load_environment_from_gpg(None)

# Setup Logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("poly_prices")

class PolyPriceFetcher:
    def __init__(self):
        # 1. Decrypt and Load Environment
        load_environment_from_gpg(None)
        self.pk = os.getenv("POLY_API_WALLET_PK")
        self.funder = os.getenv("POLY_API_WALLET_ADDRESS")
        
        # 2. Config
        self.data_dir = Path("./data/poly")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.latest_file = self.data_dir / "poly_prices_latest.json"
        
        # 3. State
        self.redis_client = None
        self.clob_client = None
        self.running = False
        self.update_interval = 5.0 # Polymarket quota is generous, 5s is safe

    async def init_services(self):
        """Initialize Redis and CLOB Client"""
        try:
            # Connect Redis
            self.redis_client = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
            await self.redis_client.ping()
            
            # Initialize CLOB Client (Authenticated for higher rate limits)
            self.clob_client = ClobClient(
                host="https://clob.polymarket.com",
                key=self.pk,
                funder=self.funder,
                chain_id=137
            )
            logger.info("🔌 Poly CLOB Client & Redis initialized.")
        except Exception as e:
            logger.error(f"Initialization Failed: {e}")
            raise

    async def fetch_token_midpoint(self, token_id: str) -> Optional[Dict]:
        """Fetches Order Book and calculates Midpoint price"""
        try:
            # Get Orderbook from CLOB
            book = await asyncio.to_thread(self.clob_client.get_order_book, token_id)
            
            bid = float(book.bids[0].price) if book.bids else None
            ask = float(book.asks[0].price) if book.asks else None
            
            if bid and ask:
                midpoint = (bid + ask) / 2
                return {
                    "price": round(midpoint, 4),
                    "bid": bid,
                    "ask": ask,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "token_id": token_id
                }
            elif bid or ask:
                # Fallback to whichever side is available if book is thin
                price = bid if bid else ask
                return {
                    "price": price,
                    "bid": bid or 0,
                    "ask": ask or 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "token_id": token_id
                }
        except Exception as e:
            logger.debug(f"Error fetching book for {token_id}: {e}")
        return None

    async def broadcast_prices(self, price_map: Dict):
        """Saves prices to Redis and Disk JSON atomically"""
        try:
            # 1. Update Redis
            await self.redis_client.set("poly_prices_latest", json.dumps(price_map))
            
            # 2. Atomic Save to Disk (Fallback for Indicator script)
            random_suffix = uuid.uuid4().hex
            temp_file = self.latest_file.with_name(f".{self.latest_file.name}.{random_suffix}.tmp")
            
            async with aiofiles.open(temp_file, mode='w') as f:
                await f.write(json.dumps(price_map, indent=2))
                await f.flush()
                os.fsync(f.fileno())
            
            shutil.move(str(temp_file), str(self.latest_file))
        except Exception as e:
            logger.error(f"Broadcast Failed: {e}")

    async def run_loop(self):
        await self.init_services()
        self.running = True
        logger.info(f"🚀 Poly Price Fetcher active (Interval: {self.update_interval}s)")

        while self.running:
            start_time = time.time()
            try:
                # 1. Get tokens discovered by poly_scanner.py
                raw_tokens = await self.redis_client.get("poly_monitored_tokens")
                if not raw_tokens:
                    logger.warning("No tokens found in poly_monitored_tokens. Waiting for scanner...")
                    await asyncio.sleep(10)
                    continue
                
                token_ids = json.loads(raw_tokens)
                
                # 2. Fetch prices concurrently
                tasks = [self.fetch_token_midpoint(tid) for tid in token_ids]
                results = await asyncio.gather(*tasks)
                
                # 3. Map results
                price_map = {}
                for res in results:
                    if res:
                        price_map[res['token_id']] = res
                
                # 4. Save and Log
                if price_map:
                    await self.broadcast_prices(price_map)
                    elapsed = time.time() - start_time
                    logger.info(f"Updated {len(price_map)} Poly prices in {elapsed:.2f}s")

                # 5. Dynamic Sleep to maintain interval
                sleep_time = max(0, self.update_interval - (time.time() - start_time))
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Main Loop Error: {e}")
                await asyncio.sleep(10)

if __name__ == "__main__":
    fetcher = PolyPriceFetcher()
    try:
        asyncio.run(fetcher.run_loop())
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
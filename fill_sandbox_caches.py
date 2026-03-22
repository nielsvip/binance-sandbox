import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
import aiohttp

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("sandbox_filler")

BASE_PATH = Path("/home/niels/binance-sandbox")
CACHE_DIR = BASE_PATH / "klines_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BASE_PATH))

try:
    from tradier_api import TradierAPIClient
    from config_tradier import TradierConfig
    from tradier_indicators import TradierBarManager, TradierPriceCacheManager
except ImportError as e:
    logger.error(f"Failed to import tradier modules: {e}")

from collections import deque
_throttle_state = {'per_second': deque(), 'per_minute': deque()}
API_MAX_PER_SECOND = 2
API_MAX_PER_MINUTE = 50

async def throttle_api_request():
    per_second = _throttle_state['per_second']
    per_minute = _throttle_state['per_minute']
    while True:
        now = time.monotonic()
        while per_second and now - per_second[0] >= 1: per_second.popleft()
        while per_minute and now - per_minute[0] >= 60: per_minute.popleft()
        if len(per_second) < API_MAX_PER_SECOND and len(per_minute) < API_MAX_PER_MINUTE:
            per_second.append(now)
            per_minute.append(now)
            return
        await asyncio.sleep(0.1)

async def fetch_binance_history(symbol: str, interval: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore):
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {'symbol': symbol, 'interval': interval, 'limit': 1500} # Max limit for fapi as in ez_klines
    
    await throttle_api_request()
    async with semaphore:
        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    klines_data = []
                    for kline in data:
                        try:
                            timestamp = datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                            klines_data.append({
                                'timestamp': timestamp, 'open': float(kline[1]), 'high': float(kline[2]),
                                'low': float(kline[3]), 'close': float(kline[4]), 'volume': float(kline[5])
                            })
                        except (ValueError, TypeError, IndexError):
                            continue
                    file_path = CACHE_DIR / f"{symbol}_{interval}.json"
                    with open(file_path, 'w') as f:
                        json.dump(klines_data, f)
                    logger.info(f"Binance: Saved {len(klines_data)} bars for {symbol} {interval}")
                else:
                    logger.error(f"Binance API Error for {symbol} {interval}: {response.status}")
        except Exception as e:
            logger.error(f"Error fetching {symbol} {interval}: {e}")

async def process_binance():
    symbols_file = BASE_PATH / "symbols.json"
    if not symbols_file.exists():
        logger.error("symbols.json not found")
        return
    with open(symbols_file, 'r') as f:
        symbols = json.load(f)
    logger.info(f"Loaded {len(symbols)} Binance symbols")
    
    semaphore = asyncio.Semaphore(2)
    async with aiohttp.ClientSession() as session:
        tasks = []
        for symbol in symbols:
            for interval in ['1m', '3m']:
                tasks.append(fetch_binance_history(symbol, interval, session, semaphore))
        await asyncio.gather(*tasks)

async def process_tradier():
    tradier_symbols_file = BASE_PATH / "symbols_tradier.json"
    if not tradier_symbols_file.exists():
        logger.error("symbols_tradier.json not found")
        return
    with open(tradier_symbols_file, 'r') as f:
        tradier_symbols = json.load(f)
    logger.info(f"Loaded {len(tradier_symbols)} Tradier symbols")
    
    config = TradierConfig()
    config.KLINES_CACHE_DIR = CACHE_DIR
    api_client = TradierAPIClient(config=config, account_key="tra")
    price_cacheman = TradierPriceCacheManager(config.DATA_DIR)
    bar_manager = TradierBarManager(api_client, CACHE_DIR, price_cacheman)
    
    await api_client.connect()
    try:
        for i, symbol in enumerate(tradier_symbols):
            logger.info(f"[{i+1}/{len(tradier_symbols)}] Fetching Tradier data for {symbol}...")
            try:
                bundle = await bar_manager.get_bundle(symbol, force_api=True)
                if bundle:
                    counts = {tf: len(df) for tf, df in bundle.items() if tf in ['1m', '5m']}
                    logger.info(f"   Success {symbol}: {counts}")
            except Exception as e:
                logger.error(f"   Error fetching {symbol}: {e}")
            await asyncio.sleep(0.1)
    finally:
        await api_client.close()

async def main():
    logger.info("Starting Sandbox Klines Filler")
    await process_binance()
    await process_tradier()
    logger.info("Finished Sandbox Klines Filler")

if __name__ == "__main__":
    asyncio.run(main())

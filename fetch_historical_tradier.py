import asyncio
import json
import logging
import os
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Import Tradier components
from tradier_api import TradierAPIClient
from config_tradier import TradierConfig
from tradier_indicators import TradierBarManager, TradierPriceCacheManager, safe_json_loads

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fetch_hist")

async def fetch_all_historical():
    config = TradierConfig()
    api_client = TradierAPIClient(config=config, account_key="tra")
    price_cacheman = TradierPriceCacheManager(config.DATA_DIR)
    bar_manager = TradierBarManager(api_client, config.KLINES_CACHE_DIR, price_cacheman)
    
    await api_client.connect()
    
    try:
        # 1. GET OPEN POSITIONS
        logger.info("Fetching open positions...")
        positions = await api_client.get_account_positions()
        pos_symbols = [p['symbol'] for p in positions if 'symbol' in p]
        logger.info(f"Found {len(pos_symbols)} symbols with open positions: {pos_symbols}")
        
        # 2. LOAD LEADERBOARDS (trb)
        def load_json_list(path: Path):
            if path.exists():
                try:
                    with open(path, 'r') as f:
                        data = json.load(f)
                        return data if isinstance(data, list) else []
                except Exception: return []
            return []

        trb_long = load_json_list(config.BASE_PATH / "symbols_trb_long.json")
        trb_short = load_json_list(config.BASE_PATH / "symbols_trb_short.json")
        leaderboard_symbols = list(set(trb_long + trb_short))
        logger.info(f"Found {len(leaderboard_symbols)} leaderboard symbols.")
        
        # 3. LOAD ALL TRADIER SYMBOLS
        all_tradier = load_json_list(config.BASE_PATH / "symbols_tradier.json")
        logger.info(f"Found {len(all_tradier)} symbols in symbols_tradier.json")
        
        # 4. BUILD PRIORITY LIST
        seen = set()
        priority_list = []
        
        for s in pos_symbols:
            if s not in seen:
                priority_list.append(s)
                seen.add(s)
        
        for s in leaderboard_symbols:
            if s not in seen:
                priority_list.append(s)
                seen.add(s)
                
        for s in all_tradier:
            if s not in seen:
                priority_list.append(s)
                seen.add(s)
        
        logger.info(f"Total unique symbols to fetch: {len(priority_list)}")
        
        # 5. FETCH DATA
        for i, symbol in enumerate(priority_list):
            logger.info(f"[{i+1}/{len(priority_list)}] Deep fetching historical data for {symbol}...")
            
            try:
                bundle = await bar_manager.get_bundle(symbol, force_api=True)
                if bundle:
                    counts = {tf: len(df) for tf, df in bundle.items()}
                    logger.info(f"   Success {symbol}: {counts}")
                else:
                    logger.warning(f"   Failed to fetch bundle for {symbol}")
            except Exception as e:
                logger.error(f"   Error fetching {symbol}: {e}")
            
            await asyncio.sleep(0.1)

    finally:
        await api_client.close()

if __name__ == "__main__":
    asyncio.run(fetch_all_historical())

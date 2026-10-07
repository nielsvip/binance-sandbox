import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import redis.asyncio as redis

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ez_indicators import (atr_values, donchian, ema_pair, heikin_ashi,
                           hull_trend_indicators, linreg_features,
                           relative_volume, rsi_value, stoch_result, wavetrend)
from utils import load_environment_from_gpg

# Constants required by your ez_indicators functions
STOCH_LEN = 14
STOCH_K = 3
STOCH_D = 3
WT_N1 = 10
WT_N2 = 21
WT_SMOOTH = 3

from py_clob_client.client import ClobClient

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("poly_indicators")

class PolyBarManager:
    def __init__(self, clob_client, base_path: Path):
        self.client = clob_client
        self.base_dir = base_path / "klines_poly"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.MAX_BARS = 400
        # Waterfall definition: (Timeframe, Pandas Rule)
        self.tf_hierarchy = [
            ("1m", "1min"),
            ("3m", "3min"),
            ("15m", "15min"),
            ("1h", "1h"),
            ("4h", "4h")
        ]

    def get_token_dir(self, token_id: str) -> Path:
        t_dir = self.base_dir / token_id
        t_dir.mkdir(parents=True, exist_ok=True)
        return t_dir

    async def sync_1m_base(self, token_id: str):
        """Fetch latest prices and update the 1m base file"""
        t_dir = self.get_token_dir(token_id)
        file_1m = t_dir / "1m.json"
        
        existing_data = []
        last_ts = 0
        if file_1m.exists():
            try:
                with open(file_1m, 'r') as f:
                    existing_data = json.load(f)
                if existing_data:
                    last_ts = existing_data[-1]['t']
            except: pass

        try:
            # Fetch 1m history
            import aiohttp
            async with aiohttp.ClientSession() as session:
                url = f"https://clob.polymarket.com/prices-history?market={token_id}&interval=1d"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                    else:
                        res = {}
            new_points = res.get('history', []) if res else []
            
            # Filter and Merge
            filtered_new = [p for p in new_points if p['t'] > last_ts]
            if filtered_new:
                combined = (existing_data + filtered_new)[-1000:] # Keep buffer for resampling
                with open(file_1m, 'w') as f:
                    json.dump(combined, f)
                return combined
            return existing_data
        except Exception as e:
            logger.error(f"Sync error for {token_id}: {e}")
            return existing_data

    async def update_all_timeframes(self, token_id: str, live_price: float = None):
        """Waterfall Resampling: 1m -> 3m -> 15m -> 1h -> 4h"""
        data_1m = await self.sync_1m_base(token_id)
        if not data_1m: return

        # Load into DataFrame
        df = pd.DataFrame(data_1m)
        df['timestamp_dt'] = pd.to_datetime(df['t'], unit='s', utc=True)
        df.set_index('timestamp_dt', inplace=True)
        df['p'] = df['p'].astype(float)

        t_dir = self.get_token_dir(token_id)
        
        # Step through the hierarchy
        for tf_name, rule in self.tf_hierarchy:
            # Resample from base p (price)
            ohlc = df['p'].resample(rule).ohlc()
            
            # Apply 400 bar limit
            ohlc = ohlc.tail(self.MAX_BARS)
            
            # Inject Live Price into the very last TF (the active bar)
            if live_price is not None:
                idx = ohlc.index[-1]
                ohlc.at[idx, 'close'] = live_price
                ohlc.at[idx, 'high'] = max(ohlc.at[idx, 'high'], live_price)
                ohlc.at[idx, 'low'] = min(ohlc.at[idx, 'low'], live_price)

            # Save TF to disk for indicator script persistence
            ohlc_dict = ohlc.reset_index().to_dict(orient='records')
            tf_file = t_dir / f"{tf_name}.json"
            with open(tf_file, 'w') as f:
                json.dump(ohlc_dict, f, default=str)

    def purge_ended_markets(self, active_token_ids: list):
        """Delete directories for tokens that are no longer in our active list"""
        try:
            existing_dirs = [d.name for d in self.base_dir.iterdir() if d.is_dir()]
            for folder in existing_dirs:
                if folder not in active_token_ids:
                    logger.info(f"🧹 Purging ended market data: {folder}")
                    shutil.rmtree(self.base_dir / folder)
        except Exception as e:
            logger.error(f"Purge error: {e}")

    def get_df(self, token_id: str, timeframe: str) -> pd.DataFrame:
        """Load a specific timeframe DF for ez_indicators"""
        file_path = self.base_dir / token_id / f"{timeframe}.json"
        if not file_path.exists(): return pd.DataFrame()
        
        try:
            df = pd.read_json(file_path)
            if not df.empty:
                df['timestamp_dt'] = pd.to_datetime(df['timestamp_dt'])
                df.set_index('timestamp_dt', inplace=True)
            return df
        except: return pd.DataFrame()

class PolyIndicatorOrchestrator:
    def __init__(self):
        self.data_dir = Path("./data/poly")
        self.klines_dir = self.data_dir / "klines_poly"
        self.redis_client = None
        self.clob_client = None
        self.timeframes = ["1m", "5m", "15m", "1h", "4h", "D"]
        self.indicators_data = {}
        self.bar_manager = None

    async def init(self):
        load_environment_from_gpg(None)
        pk = os.getenv("POLY_API_WALLET_PK")
        funder = os.getenv("POLY_API_WALLET_ADDRESS")
        self.redis_client = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
        self.clob_client = ClobClient(host="https://clob.polymarket.com", key=pk, funder=funder, chain_id=137)
        self.bar_manager = PolyBarManager(self.clob_client, self.klines_dir)

    async def compute_all(self, symbol: str, token_id: str):
        raw_prices = await self.redis_client.get("poly_prices_latest")
        current_price = None
        if raw_prices:
            p_map = json.loads(raw_prices)
            current_price = float(p_map.get(token_id, {}).get('price', 0.5))

        results = {"symbol": symbol, "timestamp": datetime.now(timezone.utc).isoformat(), "current_price": current_price}

        # 2. Process each timeframe using the BarManager's local storage
        for tf in ["1m", "5m", "15m", "1h", "D"]:
            # This handles file loading, syncing new data, and resampling
            df = self.bar_manager.get_df(token_id, tf)
            
            if df.empty or len(df) < 50: # Need enough data for indicators
                continue

            # --- MATH: Connect to your ez_indicators functions ---
            close_s = df['close']
            high_s = df['high']
            low_s = df['low']

            # Donchian (20 periods)
            dh, dl, db = donchian(high_s, low_s, 20)
            results[f"dc_high_{tf}"] = float(dh)
            results[f"dc_low_{tf}"] = float(dl)
            results[f"dc_basis_{tf}"] = float(db)

            # Stoch Result
            sr = stoch_result(close_s) # Returns k_curr, d_curr, etc.
            results[f"stoch_k_{tf}"] = float(sr[0])
            results[f"stoch_d_{tf}"] = float(sr[1])
            results[f"stoch_crossup_{tf}"] = bool(sr[4])

            # Hull Trend (Your THMA based logic)
            ht = hull_trend_indicators(close_s)
            results[f"t_up_{tf}"] = bool(ht[0]) # t_up
            results[f"hull_buy_{tf}"] = bool(ht[1]) # tco

            # Wavetrend
            wt = wavetrend(df)
            if wt[0] is not None:
                results[f"wt1_{tf}"] = float(wt[0].iloc[-1])
                results[f"wt2_{tf}"] = float(wt[1].iloc[-1])

        # 3. Save to Redis for poly_manage.py to see
        await self.redis_client.set(f"poly_indicators:{symbol}", json.dumps(results))

    # async def compute_indicators(self, symbol: str, token_id: str):
    #     """The core calculator for a single symbol"""
    #     results = {"symbol": symbol, "token_id": token_id, "timestamp": datetime.now(timezone.utc).isoformat()}
        
    #     # 1. Get Live Price from Redis (updated by poly_prices.py)
    #     raw_prices = await self.redis_client.get("poly_prices_latest")
    #     current_price = 0.5 # Default
    #     if raw_prices:
    #         prices = json.loads(raw_prices)
    #         current_price = float(prices.get(token_id, {}).get('price', 0.5))

    #     # 2. Process each timeframe
    #     for tf in self.timeframes:
    #         df = await self.bar_manager.get_history(token_id, tf)
    #         if df.empty: continue

    #         # Append current live price as the latest close
    #         df.loc[df.index[-1], 'close'] = current_price
            
    #         close_ser = df['close']
    #         high_ser = df['high']
    #         low_ser = df['low']

    #         # --- CALCULATIONS ---
    #         # Donchian Channels (Vital for Poly Stop Losses)
    #         dc_h, dc_l, dc_b = donchian(high_ser, low_ser, 20)
    #         results[f"dc_high_{tf}"] = dc_h
    #         results[f"dc_low_{tf}"] = dc_l
    #         results[f"dc_basis_{tf}"] = dc_b

    #         # Stoch RSI (The main Entry/Exit signal)
    #         stoch = stoch_rsi(close_ser, length=14, k=3, d=3)
    #         if stoch is not None:
    #             results[f"stoch_k_{tf}"] = float(stoch['k'].iloc[-1])
    #             results[f"stoch_d_{tf}"] = float(stoch['d'].iloc[-1])

    #         # Trend (1h Hull or EMA)
    #         if tf == "1h":
    #             ema_20 = close_ser.ewm(span=20).mean().iloc[-1]
    #             results["t_up_1h"] = current_price > ema_20

    #         # Heikin Ashi (Color of the move)
    #         curr_ha, _ = heikin_ashi(df)
    #         results[f"ha_{tf}"] = curr_ha

    #     # 3. Store results in Redis
    #     await self.redis_client.set(f"poly_indicators:{symbol}", json.dumps(results))
    #     return results

    async def run_loop(self):
        await self.init()
        logger.info("🚀 Poly Indicators Orchestrator Started")
        
        while True:
            try:
                # 1. Get current active tokens from Redis (Scanner output)
                raw_tokens = await self.redis_client.get("poly_monitored_tokens")
                raw_map = await self.redis_client.get("poly_symbols_map")
                if not raw_tokens or not raw_map:
                    await asyncio.sleep(10)
                    continue
                
                active_tokens = json.loads(raw_tokens)
                symbols_map = json.loads(raw_map)
                
                # 2. Purge data for markets that have ended/vanished
                self.bar_manager.purge_ended_markets(active_tokens)
                
                # 3. Get Live prices for "Mid-Bar" injection
                raw_prices = await self.redis_client.get("poly_prices_latest")
                price_map = json.loads(raw_prices) if raw_prices else {}

                # 4. Sync and Calculate
                for symbol, m_data in symbols_map.items():
                    token_id = m_data['yes_token']
                    live_p = price_map.get(token_id, {}).get('price')
                    
                    # This triggers the Waterfall Resampling and 400-bar pruning
                    await self.bar_manager.update_all_timeframes(token_id, live_price=live_p)
                    
                    # Now calculate indicators using the fresh local files
                    await self.compute_all(symbol, token_id)

                await asyncio.sleep(20)
                
            except Exception as e:
                logger.error(f"Orchestrator Loop Error: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    orchestrator = PolyIndicatorOrchestrator()
    asyncio.run(orchestrator.run_loop())
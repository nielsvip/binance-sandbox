import asyncio
import glob
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
import warnings
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiofiles
import aiohttp
import numpy as np
import orjson
import pandas as pd
import pandas_ta as ta
import psutil
from ta.momentum import RSIIndicator, StochasticOscillator

warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="pandas_ta")
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from cachetools import LRUCache

from config import Config
from utils import (REDIS_CHANNELS, _resolve_klines_directories, force_usdc_in_list, get_current_environment, get_current_price, get_klines_from_redis, get_simple_redis_manager, logger, orjson_default, standardize_kline_data_for_json)

# =========================
# =========================
config = Config()
BASE_PATH                 = config.BASE_PATH
logger.info(f"[ez_crosses] Configured BASE_PATH: {BASE_PATH}")
RANKINGS_DIR              = config.RANKINGS_DIR
SYMBOLS_FILE              = config.SYMBOLS_FILE
logger.info(f"[ez_crosses] Configured SYMBOLS_FILE: {SYMBOLS_FILE}")
PLOTS_DIR                 = config.PLOTS_DIR
DATA_DIR                  = config.DATA_DIR
KLINES_CACHE_DIR          = config.KLINES_CACHE_DIR
RANKING_RESULTS_FILE      = config.RANKING_RESULTS_FILE
WINNERS_20_FILE           = config.WINNERS_20_FILE
LOSERS_20_FILE            = config.LOSERS_20_FILE
WINNERS_15M_FILE          = config.WINNERS_15M_FILE
LOSERS_15M_FILE           = config.LOSERS_15M_FILE
MIN_QTY_FILE              = config.MIN_QTY_FILE
MULT_FILE                 = config.MULT_FILE
SYMBOLS_ACTIVE_FILE       = config.SYMBOLS_ACTIVE_FILE
LAST_EVENTS_FILE          = config.LAST_EVENTS_FILE
SYMBOLS_ACTIVE            = config.SYMBOLS_ACTIVE    
REDIS_CHANNEL_SIGNALS: str = "signals_channel"
MAX_MEMORY_GB = 6
KLINE_DATA_CACHE_SIZE = 100
TIMEFRAMES = ['3m', '15m', '1h', '4h','D']
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_CHANNEL = 'signals_channel'
SLEEP_INTERVAL = 20  # Seconds between iterations
SIGNAL_PUBLISH_SLEEP = 0.4  # Delay between Redis publishes
# STREAMING APPROACH: No more in-memory caching
# recent_signals = deque(maxlen=1000)  # REMOVED - no duplicate tracking needed
# kline_lru_cache = LRUCache(maxsize=KLINE_DATA_CACHE_SIZE)  # REMOVED - stream from Redis
# Initialize last_events dictionary
last_events = {}
symbols_active=[]
required_columns_global = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
last_timeframe_process = {}  # Track last processed time for each timeframe boundary 

# Redis clients for dual-source reading
# redis_client = None  # Local Redis for writing signals
# redis_client_read = None  # Gateway Redis for reading klines/mark prices
redis_manager = None
local_redis = None 
def setup_logging():
    """Get logger from utils.py with proper rotation and formatting."""
    from utils import orjson_default, setup_ez_script_logger
    return setup_ez_script_logger('ez_crosses')
logger = setup_logging()
local_redis = None
gateway_redis = None

async def initialize_redis():
    """Initializes all Redis connections and assigns them to the global clients."""
    global redis_manager, local_redis, gateway_redis
    logger.info("Initializing Redis connections for ez_crosses...")
    try:
        redis_manager = await get_simple_redis_manager()
        if redis_manager:
            local_redis = redis_manager.connections.get("local")
            gateway_redis = redis_manager.connections.get("gateway")
        logger.info(f"Redis connections established: Local={'✅' if local_redis else '❌'}, Gateway={'✅' if gateway_redis else '❌'}")
    except Exception as e:
        logger.warning(f"Redis initialization failed - continuing with file operations only: {e}")
# def get_redis_client():
#     """
#     Lazily initialize and return a module-level Redis client for write operations (local).
#     Returns None if Redis is unavailable.
#     """
#     global redis_client
#     if redis_client is not None:
#         return redis_client
#     try:
#         client = redis.Redis(
#             host=REDIS_HOST,
#             port=REDIS_PORT,
#             db=0,
#             socket_timeout=1.0,
#             decode_responses=True,
#         )
#         client.ping()
#         redis_client = client
#         logger.info("Initialized local Redis client for writes (signals).")
#         return redis_client
#     except Exception as e:
#         logger.error(f"Failed to initialize local Redis client for writes: {e}")
#         redis_client = None
#         return None

# def get_redis_client_read():
#     """
#     Lazily initialize and return a module-level Redis client for read operations (gateway).
#     Returns None if Redis is unavailable.
#     """
#     global redis_client_read
#     if redis_client_read is not None:
#         return redis_client_read
#     try:
#         client = redis.Redis(
#             host=GATEWAY_REDIS_HOST,
#             port=GATEWAY_REDIS_PORT,
#             db=0,
#             socket_connect_timeout=2.0,
#             socket_timeout=2.0,
#             decode_responses=True,
#         )
#         client.ping()
#         redis_client_read = client
#         logger.info(f"Initialized gateway Redis client for reads at {GATEWAY_REDIS_HOST}:{GATEWAY_REDIS_PORT} (klines/mark prices).")
#         return redis_client_read
#     except Exception as e:
#         logger.error(f"Failed to initialize gateway Redis client for reads: {e}")
#         redis_client_read = None
#         return None
# ---------------------------------------------------------
# UPDATE: ez_crosses.py -> recalc_crosses
# ---------------------------------------------------------
async def recalc_crosses(symbol: str, timeframe: str):
    """Recalculate crosses for a specific symbol and timeframe using DATA timestamps."""
    global last_events
    try:
        df = await get_ohlcv_df(symbol, timeframe)
        if df.empty:
            logger.debug(f"No data for {symbol} on {timeframe}. Skipping recalc.")
            return
            
        # --- FIX START: Extract True Data Timestamp ---
        last_row = df.iloc[-1]
        last_ts = last_row['timestamp']
        
        # Ensure it's a datetime object and UTC
        if not isinstance(last_ts, datetime):
            last_ts = pd.to_datetime(last_ts, utc=True).to_pydatetime()
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
            
        # This is the TRUE timestamp of the data
        data_timestamp_str = last_ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        # --- FIX END ---

        indicators = calculate_indicators(symbol, df, timeframe)
        if 'stoch_rsi' not in indicators or indicators['stoch_rsi'] is None:
            logger.debug(f"Stoch RSI not calculated for {symbol} on {timeframe}. Skipping recalc.")
            return
            
        if symbol not in last_events: last_events[symbol] = {}
        if timeframe not in last_events[symbol]: last_events[symbol][timeframe] = {}

        if indicators.get('stoch_k_series') is not None and not indicators['stoch_k_series'].empty:
            k_series = indicators['stoch_k_series']
            d_series = indicators['stoch_d_series']
            k_val = float(k_series.iloc[-1])
            d_val = float(d_series.iloc[-1])
            k_prev = float(k_series.iloc[-2]) if len(k_series) > 1 else k_val
            d_prev = float(d_series.iloc[-2]) if len(d_series) > 1 else d_val

            last_events[symbol][timeframe]['stoch_k'] = max(0, min(100, k_val))
            last_events[symbol][timeframe]['stoch_d'] = max(0, min(100, d_val))
            last_events[symbol][timeframe]['stoch_k_prev'] = max(0, min(100, k_prev))
            last_events[symbol][timeframe]['stoch_d_prev'] = max(0, min(100, d_prev))
            
            # Use the DATA timestamp, not calculation time
            last_events[symbol][timeframe]['stoch_timestamp'] = data_timestamp_str

        events = detect_stoch_crossovers(symbol, df, indicators['stoch_rsi'])
        if events:
            for event in events:
                # Events already extract timestamp from the DF row in detect_stoch_crossovers
                update_last_events(symbol, timeframe, event, last_events)
                
        save_last_events(last_events)
    except Exception as e:
        logger.error(f"Error in recalc_crosses for {symbol}_{timeframe}: {e}")
        
async def schedule_cross_recalcs(symbols, timeframes):
    while True:
        now = datetime.now(timezone.utc)
        second = now.second
        minute = now.minute
        hour = now.hour

        for tf in timeframes:
            if should_recalc(tf, now):
                for symbol in symbols:
                    await recalc_crosses(symbol, tf)
        await asyncio.sleep(1)

def should_recalc(tf, now):
    m, s, h = now.minute, now.second, now.hour
    if s != 2:  # always trigger at :02s only
        return False
    if tf == "1m": return True
    if tf == "3m": return m % 3 == 0
    if tf == "5m": return m % 5 == 0
    if tf == "15m": return m % 15 == 0
    if tf == "1h": return m == 0
    if tf == "4h": return m == 0 and h % 4 == 0
    if tf == "D": return m == 0 and h == 0
    return False

def should_process_timeframe(timeframe: str, now: datetime) -> bool:
    """Check if a timeframe should be processed based on current time boundaries"""
    global last_timeframe_process
    minute, hour = now.minute, now.hour
    if timeframe == "3m":
        return True
    elif timeframe == "15m":
        if minute % 15 != 0:
            return False
        boundary_key = f"{timeframe}_{hour:02d}:{minute:02d}"
        if last_timeframe_process.get(boundary_key) == now.strftime("%Y-%m-%d %H:%M"):
            return False
        last_timeframe_process[boundary_key] = now.strftime("%Y-%m-%d %H:%M")
        return True
    elif timeframe == "1h":
        if minute != 0:
            return False
        boundary_key = f"{timeframe}_{hour:02d}:00"
        if last_timeframe_process.get(boundary_key) == now.strftime("%Y-%m-%d %H:%M"):
            return False
        last_timeframe_process[boundary_key] = now.strftime("%Y-%m-%d %H:%M")
        return True
    elif timeframe == "4h":
        if minute != 0 or hour % 4 != 0:
            return False
        boundary_key = f"{timeframe}_{hour:02d}:00"
        if last_timeframe_process.get(boundary_key) == now.strftime("%Y-%m-%d %H:%M"):
            return False
        last_timeframe_process[boundary_key] = now.strftime("%Y-%m-%d %H:%M")
        return True
    elif timeframe == "D":
        if minute != 0 or hour != 0:
            return False
        date_key = now.strftime("%Y-%m-%d")
        boundary_key = f"{timeframe}_{date_key}"
        if last_timeframe_process.get(boundary_key) == date_key:
            return False
        last_timeframe_process[boundary_key] = date_key
        return True
    return False

# def get_mark_price_from_redis(symbol: str):
#     """
#     Best-effort fetch of latest mark price for a symbol from both Redis sources.
#     Tries hash 'mark_prices' first, then key 'mark_price:{symbol}'.
#     Returns float or None.
#     """
#     # Try both Redis sources
#     redis_sources = []
    
#     # Add local Redis if available
#     local_client = get_redis_client()
#     if local_client:
#         redis_sources.append(('local', local_client))
    
#     # Add gateway Redis if available
#     gateway_client = get_redis_client_read()
#     if gateway_client:
#         redis_sources.append(('gateway', gateway_client))
    
#     if not redis_sources:
#         return None
#     # Try each Redis source
#     for source_name, client in redis_sources:
#         try:
#             # Try hash (preferred)
#             data = client.hget("mark_prices", symbol)
#             if data:
#                 try:
#                     obj = json.loads(data)
#                     if isinstance(obj, dict):
#                         # Common fields seen in other modules
#                         if 'p' in obj:
#                             price = float(obj['p'])
#                             logger.debug(f"Got mark price from {source_name} Redis hash: {price}")
#                             return price
#                         if 'price' in obj:
#                             price = float(obj['price'])
#                             logger.debug(f"Got mark price from {source_name} Redis hash: {price}")
#                             return price
#                     # If it's a plain number string
#                     price = float(data)
#                     logger.debug(f"Got mark price from {source_name} Redis hash: {price}")
#                     return price
#                 except Exception:
#                     try:
#                         price = float(data)
#                         logger.debug(f"Got mark price from {source_name} Redis hash: {price}")
#                         return price
#                     except Exception:
#                         pass

#             # Try simple key as fallback
#             key = f"mark_price:{symbol}"
#             data2 = client.get(key)
#             if data2:
#                 try:
#                     obj2 = json.loads(data2)
#                     if isinstance(obj2, dict):
#                         if 'p' in obj2:
#                             price = float(obj2['p'])
#                             logger.debug(f"Got mark price from {source_name} Redis key: {price}")
#                             return price
#                         if 'price' in obj2:
#                             price = float(obj2['price'])
#                             logger.debug(f"Got mark price from {source_name} Redis key: {price}")
#                             return price
#                     price = float(data2)
#                     logger.debug(f"Got mark price from {source_name} Redis key: {price}")
#                     return price
#                 except Exception:
#                     try:
#                         price = float(data2)
#                         logger.debug(f"Got mark price from {source_name} Redis key: {price}")
#                         return price
#                     except Exception:
#                         pass
#         except Exception as e:
#             logger.debug(f"Error reading mark price from {source_name} Redis for {symbol}: {e}")
#     return None
# =========================
# Helper Functions
# =========================
async def monitor_memory():
    process = psutil.Process(os.getpid())
    while True:
        mem_bytes = process.memory_info().rss
        mem_gb = mem_bytes / (1024 ** 3)
        if mem_gb > MAX_MEMORY_GB:
            print(f"Memory usage exceeded: {mem_gb:.2f} GB. Restarting...")
            os.execv(sys.executable, ['python'] + sys.argv)  # Restart script
        await asyncio.sleep(60) 

async def get_ohlcv_df(symbol: str, timeframe: str) -> pd.DataFrame:
    global logger, required_columns_global, local_redis, gateway_redis
    log_prefix = f"[GET_OHLCV][{symbol}_{timeframe}]"
    redis_df = await get_klines_from_redis(local_redis, gateway_redis, symbol, timeframe)
    try:
        disk_df = await asyncio.to_thread(_load_klines_from_file, symbol, timeframe)
    except Exception as e:
        logger.error(f"{log_prefix} Error loading from disk backup: {e}")
        disk_df = pd.DataFrame()
    # Define cleaning function for reuse
    def clean_dataframe(df):
        """Clean DataFrame by removing duplicate columns and ensuring unique column names"""
        if df is None or df.empty: return pd.DataFrame()
        df = df.loc[:, ~df.columns.duplicated()].copy()
        if 'timestamp_dt' in df.columns:
            if 'timestamp' in df.columns: df = df.drop(columns=['timestamp'])
            df = df.rename(columns={'timestamp_dt': 'timestamp'})
        if 'timestamp' not in df.columns: return df
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
            df = df.dropna(subset=['timestamp'])
            df = df[(df['timestamp'].dt.year >= 2000) & (df['timestamp'].dt.year <= 2100)]
            df = df.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp').reset_index(drop=True)
        except Exception as e:
            logger.warning(f"{log_prefix} Error in clean_dataframe: {e}")
            df = df.loc[:, ~df.columns.duplicated()]
            if 'timestamp' in df.columns: df = df.drop_duplicates(subset=['timestamp'], keep='last').reset_index(drop=True)
        return df
    redis_df = clean_dataframe(redis_df)
    disk_df = clean_dataframe(disk_df)
    if not redis_df.empty and not disk_df.empty:
        try:
            combined_df = pd.concat([disk_df, redis_df], ignore_index=True)
            combined_df = clean_dataframe(combined_df)
            logger.debug(f"{log_prefix} Merged Redis ({len(redis_df)}) and Disk ({len(disk_df)}) -> {len(combined_df)} bars.")
            return combined_df
        except Exception as e:
            logger.warning(f"{log_prefix} Merge failed: {e}")
            return redis_df if not redis_df.empty else disk_df
    elif not redis_df.empty:
        logger.debug(f"{log_prefix} Using Redis data only ({len(redis_df)} bars).")
        return redis_df
    elif not disk_df.empty:
        logger.debug(f"{log_prefix} Using Disk data only ({len(disk_df)} bars).")
        return disk_df
    return pd.DataFrame()

def _load_klines_from_file(symbol: str, interval: str) -> pd.DataFrame:
    min_bars = {'3m': 220, '15m': 200, '1h': 200, '4h': 200, 'D': 50}.get(interval, 80)
    best_df = pd.DataFrame()
    best_ts = pd.Timestamp(0, tz="UTC")
    best_len = 0
    klines_dirs = _resolve_klines_directories()
    regular_kline_dir = next((d for d in klines_dirs if 'klines_cache' in str(d) and not any(x in str(d) for x in ['gateway', 'macbook', 'server'])), None)
    sorted_dirs = [d for d in klines_dirs if d == regular_kline_dir] + [d for d in klines_dirs if d != regular_kline_dir]
    for klines_dir in sorted_dirs:
        fpath = klines_dir / f"{symbol}_{interval}.json"
        if not fpath.exists():
            continue
        candidate_df = None
        for strategy in ['orjson', 'json', 'line_by_line']:
            f = None
            try:
                data=None
                if strategy == 'orjson':
                    f = open(fpath, "rb")
                    file_data = f.read()
                    f.close()
                    f = None
                    data = orjson.loads(file_data)  # pylint: disable=no-member
                elif strategy == 'json':
                    f = open(fpath, "r")
                    file_data = f.read()
                    f.close()
                    f = None
                    data = json.loads(file_data)
                elif strategy == 'line_by_line':
                    data = _repair_json_file(fpath)
                if not data:
                    continue
                df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
                if df.empty or 'timestamp' not in df.columns:
                    continue
                df = df.loc[:, ~df.columns.duplicated()]
                timestamp_col = df["timestamp"]
                if hasattr(timestamp_col, 'dtype') and pd.api.types.is_numeric_dtype(timestamp_col):
                    df.loc[:, "timestamp"] = pd.to_datetime(df["timestamp"], unit='ms', errors='coerce', utc=True)
                else:
                    df.loc[:, "timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True)
                df = df.dropna(subset=['timestamp']).reset_index(drop=True)
                if not df.empty:
                    # Filter crazy years
                    df = df[(df['timestamp'].dt.year >= 2000) & (df['timestamp'].dt.year <= 2100)]
                if df.empty:
                    continue
                for col in ["open", "high", "low", "close", "volume"]:
                    df.loc[:, col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=['open', 'high', 'low', 'close']).reset_index(drop=True)
                if df.empty:
                    continue
                df = df.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp').reset_index(drop=True)
                latest_ts = df['timestamp'].iloc[-1]
                if not pd.isna(latest_ts):
                    candidate_df = df
                    if strategy in ['json', 'line_by_line'] and len(df) > 0:
                        _backup_repaired_file(fpath, data)
                    break
            except Exception:
                continue
            finally:
                if f is not None:
                    try:
                        f.close()
                    except Exception:
                        pass
        if candidate_df is not None and not candidate_df.empty:
            latest_ts = candidate_df['timestamp'].iloc[-1]
            if not pd.isna(latest_ts):
                candidate_meets_min = len(candidate_df) >= min_bars
                best_meets_min = len(best_df) >= min_bars if not best_df.empty else False
                if klines_dir == regular_kline_dir and candidate_meets_min:
                    return candidate_df
                if candidate_meets_min and not best_meets_min:
                    best_df = candidate_df
                    best_ts = latest_ts
                    best_len = len(candidate_df)
                elif (candidate_meets_min == best_meets_min) and (latest_ts > best_ts or (latest_ts == best_ts and len(candidate_df) > best_len)):
                    best_df = candidate_df
                    best_ts = latest_ts
                    best_len = len(candidate_df)
    return best_df if not best_df.empty else pd.DataFrame()

def _repair_json_file(fpath):
    """Repair corrupted JSON by parsing line by line"""
    try:
        with open(fpath, "r") as f:
            lines = f.readlines()
        data = []
        for line in lines:
            line = line.strip()
            if line and (line.startswith('[') or line.startswith(']')):
                continue
            if line.endswith(','):
                line = line[:-1]
            try:
                item = json.loads(line)
                if isinstance(item, list) and len(item) >= 6:
                    data.append(item)
            except Exception:
                continue
        return data
    except Exception:
        return []

def _backup_repaired_file(fpath, data):
    """Create backup of repaired data"""
    try:
        backup_path = fpath.with_suffix('.json.repaired')
        with open(backup_path, "wb") as f:
            f.write(orjson.dumps(data))  # pylint: disable=no-member
    except Exception:
        pass

# You will also need to rename your existing _load_klines_from_file to avoid confusion
# and ensure any functions that call get_ohlcv_df are now `async` and use `await`.

# def get_ohlcv_df(symbol: str, timeframe: str) -> pd.DataFrame:
#     global logger, config, required_columns_global
#     global local_redis, gateway_redis

#     cache_key = f"{symbol}_{timeframe}"
#     log_prefix = f"[GET_OHLCV][{cache_key}]"
    
#     # STREAMING APPROACH: No more LRU caching - go directly to Redis/disk
#     # try:
#     #     cached_df = kline_lru_cache[cache_key]
#     #     return cached_df.copy() 
#     # except KeyError:
#     #     pass # Continue to disk load
#     # except Exception as e_cache:
#     #     logger.error(f"{log_prefix} Error accessing LRU cache: {e_cache}. Proceeding to disk load.", exc_info=True)

#     # Try Redis first for klines from both sources (mirrors ez_indicators' Redis-first approach)
#     redis_sources = []
#     if local_redis: redis_sources.append(('local', local_redis))
#     if gateway_redis: redis_sources.append(('gateway', gateway_redis))

#     # # Add local Redis if available
#     # local_client = get_redis_client()
#     # if local_client:
#     #     redis_sources.append(('local', local_client))
#     #     logger.debug(f"[GET_OHLCV] Added local Redis source")
    
#     # # Add gateway Redis if available
#     # gateway_client = get_redis_client_read()
#     # if gateway_client:
#     #     redis_sources.append(('gateway', gateway_client))
#     #     logger.debug(f"[GET_OHLCV] Added gateway Redis source at {GATEWAY_REDIS_HOST}:{GATEWAY_REDIS_PORT}")
#     # else:
#     #     logger.warning(f"[GET_OHLCV] Gateway Redis client not available")
    
#    # logger.info(f"[GET_OHLCV] Trying {len(redis_sources)} Redis sources for {symbol}_{timeframe}")
#     for source_name, client in redis_sources:
#         try:
#             redis_key = f"klines:{symbol}:{timeframe}"
#             logger.debug(f"[GET_OHLCV] Trying {source_name} Redis key: {redis_key}")
#             json_data = client.get(redis_key)
#             if json_data:
#                 logger.debug(f"[GET_OHLCV] Got data from {source_name} Redis: {len(json_data)} chars")
#                 payload = json.loads(json_data)
#                 klines_list = payload.get('klines') if isinstance(payload, dict) else payload
#                 if isinstance(klines_list, list) and klines_list:
#                     logger.debug(f"[GET_OHLCV] Parsed {len(klines_list)} klines from {source_name} Redis")
#                     df_from_redis = pd.DataFrame(klines_list)
#                     for col in required_columns_global:
#                         if col not in df_from_redis.columns:
#                             df_from_redis[col] = np.nan
#                     if 'timestamp' in df_from_redis.columns:

#                         ts_sample = None
#                         try:
#                             if isinstance(klines_list, list) and klines_list:
#                                 ts_sample = klines_list[-1].get('timestamp') if isinstance(klines_list[-1], dict) else None
#                         except Exception:
#                             ts_sample = None
#                         is_raw_api = isinstance(ts_sample, (int, float))

#                         # Use clean_kline_data for robust timestamp handling
#                         from utils import clean_kline_data, orjson_default
#                         cleaned_df = clean_kline_data(df_from_redis, is_raw_api_data=is_raw_api)

#                         df_from_redis['timestamp'] = pd.to_datetime(
#                             df_from_redis['timestamp'], errors='coerce', utc=True
#                         )
#                         df_from_redis.dropna(subset=['timestamp'], inplace=True)
#                     else:
#                         logger.error(f"{log_prefix} No 'timestamp' in Redis payload for {symbol}_{timeframe}.")
#                         df_from_redis = pd.DataFrame(columns=required_columns_global)
#                     if not df_from_redis.empty:
#                         for col in ['open', 'high', 'low', 'close', 'volume']:
#                             if col in df_from_redis.columns:
#                                 df_from_redis[col] = pd.to_numeric(
#                                     df_from_redis[col], errors='coerce' )
#                         df_from_redis = df_from_redis[required_columns_global]
#                         df_from_redis.sort_values('timestamp', inplace=True)
#                         df_from_redis.reset_index(drop=True, inplace=True)
#                         # STREAMING APPROACH: No more caching
#                         # kline_lru_cache[cache_key] = df_from_redis.copy()
#                         logger.debug(f"{log_prefix} Got klines from {source_name} Redis: {len(df_from_redis)} rows")
#                         return df_from_redis.copy()
#                 else:
#                     logger.debug(f"{log_prefix} Redis key present but no klines list for {redis_key} from {source_name}.")
#         except Exception as e:
#             logger.error(f"{log_prefix} Error reading from {source_name} Redis key {symbol}_{timeframe}: {e}", exc_info=True)
            
#     BACKUP_DIR = Path(KLINES_CACHE_DIR) / "backups"
#     df_from_disk = pd.DataFrame(columns=required_columns_global)

#     try:
#         # 1. Look for the latest backup for the symbol
#         backup_folders = glob.glob(str(BACKUP_DIR / f"{symbol}_backup_*"))
#         if backup_folders:
#             latest_backup_folder = max(backup_folders, key=os.path.getmtime)
#             backup_file_path = Path(latest_backup_folder) / f"{symbol}_{timeframe}.json"
            
#             if backup_file_path.exists():
#                 logger.debug(f"{log_prefix} Found backup file: {backup_file_path}")
#                 # Use the same file reading logic as your original fallback
#                 with open(backup_file_path, 'r', encoding='utf-8') as f:
#                     content = f.read()
#                 if content.strip():
#                     data = json.loads(content)
#                     if isinstance(data, list) and data:
#                         df_from_disk = pd.DataFrame(data)
#                         # ... (add your column validation and type conversion logic here) ...
#                         logger.info(f"{log_prefix} Successfully loaded {len(df_from_disk)} bars from backup.")
#                         return df_from_disk.copy() # Return immediately if backup is successful
#             else:
#                 logger.debug(f"{log_prefix} No {timeframe} file in latest backup folder.")

#     except Exception as e:
#         logger.error(f"{log_prefix} Error accessing backup directory: {e}")

#     # 2. If backup fails or doesn't exist, fall back to the live klines_cache file
#     logger.debug(f"{log_prefix} No suitable backup found. Falling back to live file.")
#     file_path = Path(KLINES_CACHE_DIR) / f"{symbol}_{timeframe}.json"
    


#     # Fallback to disk
#     file_path = Path(KLINES_CACHE_DIR) / f"{symbol}_{timeframe}.json"
#     df_from_disk = pd.DataFrame(columns=required_columns_global)
    
#     logger.debug(f"[GET_OHLCV] Falling back to disk: {file_path}")
    
#     try:
#         if not file_path.exists():
#             logger.warning(f"{log_prefix} File not found: {file_path}")
#         else:
#             with open(file_path, 'r', encoding='utf-8') as f: # Synchronous read
#                 content = f.read()
#             if not content.strip():
#                 logger.warning(f"{log_prefix} File is empty: {file_path}")
#             else:
#                 data = json.loads(content) # Assuming no repair logic here for simplicity for now
#                 if isinstance(data, list) and data:
#                     df_from_disk = pd.DataFrame(data)
#                     for col in required_columns_global:
#                         if col not in df_from_disk.columns: df_from_disk[col] = np.nan
#                     if 'timestamp' in df_from_disk.columns:
#                         df_from_disk['timestamp'] = pd.to_datetime(df_from_disk['timestamp'], errors='coerce', utc=True)
#                         df_from_disk.dropna(subset=['timestamp'], inplace=True)
#                     else: # Should not happen if files are consistent
#                         logger.error(f"{log_prefix} No 'timestamp' in {file_path}. Returning empty.")
#                         df_from_disk = pd.DataFrame(columns=required_columns_global)

#                     if not df_from_disk.empty:
#                         for col in ['open', 'high', 'low', 'close', 'volume']:
#                             if col in df_from_disk.columns: df_from_disk[col] = pd.to_numeric(df_from_disk[col], errors='coerce')
#                         df_from_disk = df_from_disk[required_columns_global] # Ensure schema
#                         df_from_disk.sort_values('timestamp', inplace=True)
#                         df_from_disk.reset_index(drop=True, inplace=True)
#                 else: # Not a list or empty list
#                     logger.warning(f"{log_prefix} Data in {file_path} not a valid kline list.")
#     except Exception as e:
#         logger.error(f"{log_prefix} Error reading/processing {file_path}: {e}", exc_info=True)
#         df_from_disk = pd.DataFrame(columns=required_columns_global) # Ensure it's a DF on error
#                             # STREAMING APPROACH: No more caching
#                         # logger.debug(f"{log_prefix} Caching DF of shape {df_from_disk.shape}. LRU Size before: {len(kline_lru_cache)}")
#                         # kline_lru_cache[cache_key] = df_from_disk.copy() # Store a copy
#                         # logger.debug(f"{log_prefix} Cached. LRU Size after: {len(kline_lru_cache)}")
#     return df_from_disk.copy()

def read_ohlcv(file_path):
    """
    Reads OHLCV data from a JSON file into a pandas DataFrame.
    """
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        df = pd.DataFrame(data)
        required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required_cols):
            logger.warning(f"Missing columns in {file_path}. Required columns: {required_cols}")
            return pd.DataFrame()
        
        # Try multiple datetime formats to avoid parsing warnings
        try:
            df = df.copy()
            df.loc[:, 'timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
            df = df.dropna(subset=['timestamp'])
            df = df[(df['timestamp'].dt.year >= 2000) & (df['timestamp'].dt.year <= 2100)]
        except (ValueError, TypeError):
            try:
                df = df.copy()
                df.loc[:, 'timestamp'] = pd.to_datetime(df['timestamp'], format='ISO8601', utc=True, errors='coerce')
                df = df.dropna(subset=['timestamp'])
                df = df[(df['timestamp'].dt.year >= 2000) & (df['timestamp'].dt.year <= 2100)]
            except Exception:
                return pd.DataFrame()

        df = df.sort_values('timestamp')
        tf = file_path.split('_')[-1].replace('.json', '') if '_' in file_path else 'unknown'
        expected_diff =  pd.Timedelta(minutes=3) if tf == '3m' else pd.Timedelta(minutes=15) if tf == '15m' else pd.Timedelta(hours=1) if tf == '1h' else pd.Timedelta(hours=4)
        time_diffs = df['timestamp'].diff().dropna()
        missing_intervals = time_diffs[time_diffs != expected_diff]
        if not missing_intervals.empty:
            logger.debug(f"Missing intervals detected in {file_path}. Number of gaps: {len(missing_intervals)}")
        return df
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return pd.DataFrame()


def calculate_stoch_rsi(symbol, df, rsi_window=14, stoch_window=14, smooth_window=3):
    try:
        rsi = RSIIndicator(close=df['close'], window=rsi_window).rsi()
        if rsi is None or rsi.empty:
            return None
        stoch_period = min(stoch_window, len(rsi) - 1) if len(rsi) > 1 else 1
        k_period = min(smooth_window, len(rsi) - stoch_period) if len(rsi) > stoch_period else 1
        d_period = min(smooth_window, len(rsi) - stoch_period - k_period) if len(rsi) > stoch_period + k_period else 1
        if stoch_period > 0:
            lowest_rsi = rsi.rolling(stoch_period, min_periods=1).min()
            highest_rsi = rsi.rolling(stoch_period, min_periods=1).max()
            stoch_rsi = 100 * (rsi - lowest_rsi) / (highest_rsi - lowest_rsi).replace(0, 1)
            stoch_k = stoch_rsi.rolling(k_period, min_periods=1).mean() if k_period > 1 else stoch_rsi
            stoch_d = stoch_k.rolling(d_period, min_periods=1).mean() if d_period > 1 else stoch_k
            return {'k': stoch_k, 'd': stoch_d}
        return None
    except Exception as e:
        logger.error(f"Error calculating Stoch RSI for {symbol}: {e}")
        return None

def calculate_indicators(symbol, df, timeframe):
    indicators = {}
    if df.empty:
        return indicators
    try:
        # Step 1: Calculate RSI
        rsi = RSIIndicator(close=df['close'], window=14).rsi()
        if rsi is not None and not rsi.empty:
            indicators['rsi'] = rsi.values  # numpy array
            logger.debug(f"RSI calculated for {symbol} on timeframe {timeframe}. Mean: {rsi.mean()}, Std: {rsi.std()}")
        else:
            indicators['rsi'] = None
            logger.warning(f"RSI calculation returned None or empty for {symbol} on timeframe {timeframe}")
        
        # Step 2: Calculate Stoch RSI
        stoch_rsi = calculate_stoch_rsi(symbol, df)
        if stoch_rsi is not None and stoch_rsi.get('k') is not None and not stoch_rsi['k'].empty:
            indicators['stoch_rsi'] = stoch_rsi['k'].values
            indicators['stoch_k_series'] = stoch_rsi['k']
            indicators['stoch_d_series'] = stoch_rsi['d']
            logger.debug(f"Stoch RSI calculated for {symbol} on timeframe {timeframe}")
        else:
            indicators['stoch_rsi'] = None
            indicators['stoch_k_series'] = None
            indicators['stoch_d_series'] = None
            logger.warning(f"Stoch RSI calculation returned None or empty for {symbol} on timeframe {timeframe}")
        
        # Step 3: Calculate ATR (only for '3m' timeframe)
        if timeframe == '3m':
            if len(df) < 14:
                logger.debug(f"ATR skipped for {symbol} on {timeframe}: df too short ({len(df)} bars)")
                indicators['atr'] = None
            else:
                atr = ta.atr(high=df['high'], low=df['low'], close=df['close'], length=14)
                if atr is not None and not atr.empty:
                    indicators['atr'] = atr.values
                    logger.debug(f"ATR calculated for {symbol} on {timeframe}. Mean: {atr.mean():.4f}")
                else:
                    indicators['atr'] = None
                    logger.warning(f"ATR returned None for {symbol} on {timeframe}. DF length: {len(df)}")
        else:
            indicators['atr'] = None
        df = df.drop(columns=['stoch_k', 'stoch_d'], errors='ignore')
        return indicators
    except Exception as e:
        logger.error(f"Error calculating indicators for {symbol} on timeframe {timeframe}: {e}")
        return indicators


def detect_stoch_crossovers(symbol, df, stoch_rsi_values, next_tf_stoch_values=None):
    events = []
    if stoch_rsi_values is None or len(stoch_rsi_values) < 2:
        return events  # Not enough data to detect crossovers/crossunders
    try:
        # For simplicity, assume we want to check the most recent value of the next timeframe.
        next_tf_value = None
        if next_tf_stoch_values is not None and len(next_tf_stoch_values) > 0:
            next_tf_value = next_tf_stoch_values[-1]

        # HISTORICAL DETECTION (close-based): iterate full series, price = bar close
        for i in range(1, len(df)):
            prev_stoch = stoch_rsi_values[i-1]
            curr_stoch = stoch_rsi_values[i]
            curr = df.iloc[i]

            price_to_use = float(curr['close'])

            # stoch_crossover: current value crosses above 20.
            # Only pass if the next timeframe is in oversold territory (e.g. < 30).
            if prev_stoch < 20 and curr_stoch > 20:
                if next_tf_value is None or next_tf_value < 30:
                    # Handle timestamp - it might be a string or datetime object
                    if hasattr(curr['timestamp'], 'strftime'):
                        timestamp_str = curr['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    else:
                        timestamp_str = str(curr['timestamp'])

                    events.append({
                        'type': 'stoch_crossover',
                        'price': price_to_use,
                        'timestamp': timestamp_str
                    })
                    logger.debug(f"stoch_crossover detected for {symbol} at {curr['timestamp']} with price {price_to_use} (close)")
            # stoch_crossunder: current value crosses below 80.
            # Only pass if the next timeframe is in overbought territory (e.g. > 70).
            elif prev_stoch > 80 and curr_stoch < 80:
                if next_tf_value is None or next_tf_value > 70:
                    # Handle timestamp - it might be a string or datetime object
                    if hasattr(curr['timestamp'], 'strftime'):
                        timestamp_str = curr['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    else:
                        timestamp_str = str(curr['timestamp'])

                    events.append({
                        'type': 'stoch_crossunder',
                        'price': price_to_use,
                        'timestamp': timestamp_str
                    })
                    logger.debug(f"stoch_crossunder detected for {symbol} at {curr['timestamp']} with price {price_to_use} (close)")
    except Exception as e:
        logger.error(f"Error detecting stoch crossovers for {symbol}: {e}")
    return events

async def detect_stoch_crossovers_live(symbol, df, stoch_rsi_values, next_tf_stoch_values=None):
    """Live detection: evaluate only the most recent bar, use mark price for price field."""
    events = []
    if stoch_rsi_values is None or len(stoch_rsi_values) < 2 or len(df) < 2:
        return events
    try:
        last_index = len(df) - 1
        prev_stoch = stoch_rsi_values[last_index - 1]
        curr_stoch = stoch_rsi_values[last_index]
        curr = df.iloc[last_index]

        current_mark_price,_ = await get_current_price(symbol)#, local_redis, gateway_redis)

        if current_mark_price is None:
            return events

        next_tf_value = None
        if next_tf_stoch_values is not None and len(next_tf_stoch_values) > 0:
            next_tf_value = next_tf_stoch_values[-1]

        if prev_stoch < 20 and curr_stoch > 20:
            if next_tf_value is None or next_tf_value < 30:
                events.append({
                    'type': 'stoch_crossover',
                    'price': float(current_mark_price),
                    'timestamp': curr['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ') if hasattr(curr['timestamp'], 'strftime') else str(curr['timestamp'])
                })
                logger.debug(f"stoch_crossover detected (live) for {symbol} at {curr['timestamp']} with mark price {current_mark_price}")
        elif prev_stoch > 80 and curr_stoch < 80:
            if next_tf_value is None or next_tf_value > 70:
                events.append({
                    'type': 'stoch_crossunder',
                    'price': float(current_mark_price),
                    'timestamp': curr['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ') if hasattr(curr['timestamp'], 'strftime') else str(curr['timestamp'])
                })
                logger.debug(f"stoch_crossunder detected (live) for {symbol} at {curr['timestamp']} with mark price {current_mark_price}")
    except Exception as e:
        logger.error(f"Error detecting live stoch crossovers for {symbol}: {e}")
    return events

async def initialize_previous_data(symbols: list, symbol_timeframes: dict, folder_path_str: str) -> dict:
    global logger # Assuming global logger
    log_prefix = "[INIT_PREV_DATA]"
    initial_events = {}
    for symbol in symbols:
        initial_events[symbol] = {}
        for tf in ['3m', '15m', '1h', '4h','D']:
            initial_events[symbol][tf] = {
                'stoch_crossover': {'latest': None, 'previous': None},
                'stoch_crossunder': {'latest': None, 'previous': None}
            }            
            df = await get_ohlcv_df(symbol, tf) # <<<< MODIFIED HERE
            if df.empty:
                logger.info(f"{log_prefix} No data from get_ohlcv_df for {symbol}_{tf}.")
                continue
            indicators = calculate_indicators(symbol, df, tf) # This needs to be defined
            if 'stoch_rsi' not in indicators or indicators['stoch_rsi'] is None or len(indicators['stoch_rsi']) < 2 :
                logger.info(f"{log_prefix} Stoch RSI not calculated or insufficient for {symbol} {tf}.")
                continue            
            stoch_events = detect_stoch_crossovers(symbol, df, indicators['stoch_rsi']) # historical (close-based)
            if not stoch_events:
                continue
            crossover_events = [e for e in stoch_events if e['type'] == 'stoch_crossover']
            crossunder_events = [e for e in stoch_events if e['type'] == 'stoch_crossunder']
            logger.debug(f"Found {len(crossover_events)} stoch_crossover events and {len(crossunder_events)} stoch_crossunder events for {symbol} on timeframe {tf}.")
            if len(crossover_events) >= 2:
                initial_events[symbol][tf]['stoch_crossover']['latest'] = {
                    'price': crossover_events[-1]['price'],
                    'timestamp': crossover_events[-1]['timestamp']
                }
                initial_events[symbol][tf]['stoch_crossover']['previous'] = {
                    'price': crossover_events[-2]['price'],
                    'timestamp': crossover_events[-2]['timestamp']
                }
            elif len(crossover_events) == 1:
                initial_events[symbol][tf]['stoch_crossover']['latest'] = {
                    'price': crossover_events[0]['price'],
                    'timestamp': crossover_events[0]['timestamp']
                }
            if len(crossunder_events) >= 2:
                initial_events[symbol][tf]['stoch_crossunder']['latest'] = {
                    'price': crossunder_events[-1]['price'],
                    'timestamp': crossunder_events[-1]['timestamp']
                }
                initial_events[symbol][tf]['stoch_crossunder']['previous'] = {
                    'price': crossunder_events[-2]['price'],
                    'timestamp': crossunder_events[-2]['timestamp']
                }
            elif len(crossunder_events) == 1:
                initial_events[symbol][tf]['stoch_crossunder']['latest'] = {
                    'price': crossunder_events[0]['price'],
                    'timestamp': crossunder_events[0]['timestamp']
                }
    return initial_events
            
def ensure_last_events_structure(events, symbols, symbol_timeframes):
    """
    Ensures the last_events dictionary has the correct structure.
    Prevents overwriting existing values with None.
    """
    def ensure_nested_keys(data, keys, default_value=None):
        """
        Ensures nested keys exist in a dictionary, creating them if missing.
        """
        for key in keys[:-1]:
            if key not in data:
                data[key] = {}
            data = data[key]
        if keys[-1] not in data:
            data[keys[-1]] = default_value

    for symbol in symbols:
        if symbol not in events:
            events[symbol] = {}
        for tf in ['3m', '15m', '1h', '4h','D']:
            ensure_nested_keys(events[symbol], [tf, 'stoch_crossover', 'latest'], {})
            ensure_nested_keys(events[symbol], [tf, 'stoch_crossover', 'previous'], {})
            ensure_nested_keys(events[symbol], [tf, 'stoch_crossunder', 'latest'], {})
            ensure_nested_keys(events[symbol], [tf, 'stoch_crossunder', 'previous'], {})
            ensure_nested_keys(events[symbol], [tf, 'stoch_k'], None)
            ensure_nested_keys(events[symbol], [tf, 'stoch_d'], None)
            ensure_nested_keys(events[symbol], [tf, 'stoch_k_prev'], None)
            ensure_nested_keys(events[symbol], [tf, 'stoch_d_prev'], None)
            ensure_nested_keys(events[symbol], [tf, 'stoch_timestamp'], None)
    return events

def validate_last_events(last_events, symbol_timeframes):
    """
    Validates the structure of last_events based on available symbol_timeframes.

    Args:
        last_events (dict): The last_events dictionary to validate.
        symbol_timeframes (dict): Dictionary mapping symbols to their available timeframes.

    Returns:
        bool: True if valid, False otherwise.
    """
    is_valid = True
    for symbol, tfs in symbol_timeframes.items():
        if symbol not in last_events:
            logger.error(f"Symbol '{symbol}' missing in last_events.")
            is_valid = False
            continue
        for tf in tfs:
            if tf not in last_events[symbol]:
                logger.error(f"Timeframe '{tf}' missing for symbol '{symbol}' in last_events.")
                is_valid = False
                continue
            for event_type in ['stoch_crossover', 'stoch_crossunder']:
                if event_type not in last_events[symbol][tf]:
                    logger.error(f"Event type '{event_type}' missing for symbol '{symbol}' on timeframe '{tf}'.")
                    is_valid = False
                    continue
                # Ensure 'latest' and 'previous' keys exist
                if 'latest' not in last_events[symbol][tf][event_type]:
                    logger.warning(f"'latest' missing in '{event_type}' for symbol '{symbol}' on timeframe '{tf}'. Initializing as None.")
                    last_events[symbol][tf][event_type]['latest'] = None
                if 'previous' not in last_events[symbol][tf][event_type]:
                    logger.warning(f"'previous' missing in '{event_type}' for symbol '{symbol}' on timeframe '{tf}'. Initializing as None.")
                    last_events[symbol][tf][event_type]['previous'] = None
    return is_valid

def clean_json_file(file_path: Path) -> dict:
    """Clean common JSON syntax issues in file."""
    try:
        with open(file_path, "r") as f:
            raw = f.read()
        raw = raw.replace(",\n}", "\n}").replace(",\n]", "\n]")
        raw = "\n".join(line for line in raw.splitlines() if line.strip() != "")
        data = json.loads(raw)
        tmp_file = file_path.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            json.dump(data, f, indent=4)
        shutil.move(tmp_file, file_path)
        logger.info(f"[clean_json_file] Cleaned and recovered {file_path}")
        return data
    except Exception as e:
        logger.error(f"[clean_json_file] Could not clean {file_path}: {e}")
        return {}

def load_last_events():
    if os.path.exists(LAST_EVENTS_FILE):
        try:
            with open(LAST_EVENTS_FILE, 'r') as f:
                events = json.load(f)
                if not isinstance(events, dict):
                    logger.error("Invalid format in last_events file. Expected a dictionary.")
                    return {}
                logger.info("Successfully loaded last_events from file.")
                return events
        except Exception as e:
            logger.error(f"Error loading last_events from file: {e}")
            return clean_json_file(LAST_EVENTS_FILE)
    logger.warning("last_events file does not exist. Initializing empty structure.")
    return {}

# def save_last_events(events):
#     try:
#         with open(LAST_EVENTS_FILE, 'w') as f:
#             json.dump(events, f, indent=4)
#         logger.info("Successfully saved last_events to file.")
#     except Exception as e:
#         logger.error(f"Error saving last_events to file: {e}")

def save_last_events(events):
    try:
        file_path=LAST_EVENTS_FILE
        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    existing = json.load(f)
            except Exception:
                existing = clean_json_file(file_path)
        # Merge existing with new events deeply per symbol/timeframe
        for symbol, tfs in events.items():
            if symbol not in existing:
                existing[symbol] = {}
            for tf, tf_events in tfs.items():
                if tf not in existing[symbol]:
                    existing[symbol][tf] = {}
                for ev_type, payload in tf_events.items():
                    # Skip non-dictionary values (like stoch_k, stoch_d, stoch_timestamp which are floats/strings)
                    if not isinstance(payload, dict):
                        existing[symbol][tf][ev_type] = payload
                        continue
                    if ev_type not in existing[symbol][tf]:
                        existing[symbol][tf][ev_type] = {}
                    # Overwrite latest/previous entries if provided
                    if 'latest' in payload and payload['latest'] is not None:
                        existing[symbol][tf][ev_type]['latest'] = payload['latest']
                    if 'previous' in payload and payload['previous'] is not None:
                        existing[symbol][tf][ev_type]['previous'] = payload['previous']
        tmp_path = file_path.with_suffix(".tmp")
        with tempfile.NamedTemporaryFile("w", dir=str(file_path.parent), delete=False) as tmp:
            json.dump(existing, tmp, indent=4)
            tmp_path = Path(tmp.name)
        tmp_path.replace(file_path)
       # logger.info(f"[save_last_events] Saved events to {file_path}")
    except Exception as e:
        logger.error(f"[save_last_events] Failed to save events: {e}")

# def send_redis_signal(redis_client, signal_data):
#     """
#     STREAMING APPROACH: Publish signal directly to Redis - no local storage
#     """
#     try:
#         # Publish to signals channel
#         redis_client.publish(REDIS_CHANNEL, json.dumps(signal_data))
        
#         # Store in symbol-specific list for ez_manage consumption
#         symbol = signal_data.get('symbol')
#         if symbol:
#             signal_key = f"recent_signals:{symbol}"
#             signal_json = json.dumps(signal_data)
            
#             # Add to list and trim to keep only last 10
#             redis_client.lpush(signal_key, signal_json)
#             redis_client.ltrim(signal_key, 0, 9)
            
#             # Set expiry to prevent memory buildup
#             redis_client.expire(signal_key, 300)  # 5 minutes
        
#         logger.debug(f"Published signal to Redis: {signal_data.get('event_type')} for {symbol}")
        
#     except Exception as e:
#         logger.error(f"Error publishing to Redis: {e}")

def generate_signal_id(signal):
    """
    Generates a unique identifier for a signal.
    """
    symbol = signal.get('symbol', 'UNKNOWN_SYMBOL')
    event_type = signal.get('event_type', 'UNKNOWN_EVENT')
    timestamp = signal.get('timestamp', 'UNKNOWN_TIMESTAMP')
    if symbol == 'UNKNOWN_SYMBOL' or event_type == 'UNKNOWN_EVENT' or timestamp == 'UNKNOWN_TIMESTAMP':
        logger.warning(f"Signal missing keys: {signal}")
    return f"{symbol}_{event_type}_{timestamp}"

# STREAMING APPROACH: No duplicate checking needed - Redis handles deduplication
def is_signal_sent_recently(signal):
    """
    STREAMING APPROACH: No duplicate checking - always return False to allow all signals
    """
    return False  # Allow all signals in streaming approach

def add_signal_to_cache(signal):
    """
    STREAMING APPROACH: No local caching needed - signals go directly to Redis
    """
    pass  # No local caching in streaming approach

def is_signal_valid(signal):
    required_keys = ['symbol', 'event_type', 'timestamp']
    missing_keys = [key for key in required_keys if key not in signal]
    if missing_keys:
        #logger.error(f"Signal is missing keys: {missing_keys}. Signal: {signal}")
        return False
    return True

def graceful_shutdown(signum, frame):
    """
    Handles graceful shutdown on receiving termination signals.
    """
    logger.info("Gracefully shutting down...")
    print("Gracefully shutting down...")
    save_last_events(last_events)
    sys.exit(0)

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)

def simplify_signals(last_events, output_file):
    """
    Simplifies the signals from last_events into a flat, easy-to-read structure.

    Args:
        last_events (dict): The input dictionary containing events for symbols.
        output_file (str|Path): Path to save the simplified signals as a JSON file.
    """
    # Ensure output_file is a Path object
    output_file = Path(output_file)
    simplified_signals = {}

    for symbol, timeframes_data in last_events.items():
        if not isinstance(timeframes_data, dict):
            logger.error(f"timeframes_data for symbol '{symbol}' is not a dict. Found: {timeframes_data}")
            continue  # Skip this symbol

        simplified_signals[symbol] = {}

        for timeframe, events in timeframes_data.items():
            if not isinstance(events, dict):
                logger.error(f"Events for symbol '{symbol}' on timeframe '{timeframe}' are not dicts. Found: {events}")
                continue  # Skip this timeframe

            # Ensure timeframe is one of the expected ones
            if timeframe not in ["3m", "15m", "1h", "4h", "D"]:
                logger.debug(f"Unexpected timeframe '{timeframe}' for symbol '{symbol}'. Skipping.")
                continue

            # Safely extract crossover and crossunder
            crossover = events.get("stoch_crossover")
            if not isinstance(crossover, dict):
                logger.warning(f"'stoch_crossover' for symbol '{symbol}' on timeframe '{timeframe}' is missing or not a dict.")
                crossover = {}
            crossunder = events.get("stoch_crossunder")
            if not isinstance(crossunder, dict):
                logger.warning(f"'stoch_crossunder' for symbol '{symbol}' on timeframe '{timeframe}' is missing or not a dict.")
                crossunder = {}

            # Extract prices and flags safely
            latest_crossover = crossover.get("latest") or {}
            previous_crossover = crossover.get("previous") or {}
            crossover_flag = False
            if latest_crossover and previous_crossover:
                crossover_flag = latest_crossover.get("price", 0) > previous_crossover.get("price", 0)

            latest_crossunder = crossunder.get("latest") or {}
            previous_crossunder = crossunder.get("previous") or {}
            crossunder_flag = False
            if latest_crossunder and previous_crossunder:
                crossunder_flag = latest_crossunder.get("price", float('inf')) < previous_crossunder.get("price", float('inf'))

            latest_crossover_price = latest_crossover.get("price")
            previous_crossover_price = previous_crossover.get("price")
            latest_crossunder_price = latest_crossunder.get("price")
            previous_crossunder_price = previous_crossunder.get("price")

            logger.debug(f"Simplifying signals for {symbol} on {timeframe}:")
            logger.debug(f"  Crossover - Latest Price: {latest_crossover_price}, Previous Price: {previous_crossover_price}, Flag: {crossover_flag}")
            logger.debug(f"  Crossunder - Latest Price: {latest_crossunder_price}, Previous Price: {previous_crossunder_price}, Flag: {crossunder_flag}")

            # Update simplified_signals
            simplified_signals[symbol].update({
                f"latest_crossover_price_{timeframe}": latest_crossover_price,
                f"previous_crossover_price_{timeframe}": previous_crossover_price,
                f"crossover_{timeframe}": crossover_flag,
                f"latest_crossunder_price_{timeframe}": latest_crossunder_price,
                f"previous_crossunder_price_{timeframe}": previous_crossunder_price,
                f"crossunder_{timeframe}": crossunder_flag,
            })
    try:
        # Ensure parent directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(simplified_signals, f, indent=4)
        logger.info(f"Simplified signals saved to {output_file}")
    except Exception as e:
        logger.error(f"Error saving simplified signals: {e}")

def update_last_events(symbol, tf, event, last_events):
    try:
        event_type = event.get('type')
        event_ts_str = event.get('timestamp')
        if event_type not in ['stoch_crossover', 'stoch_crossunder']:
            return
        if symbol not in last_events: last_events[symbol] = {}
        if tf not in last_events[symbol]: last_events[symbol][tf] = {}
        if event_type not in last_events[symbol][tf]: last_events[symbol][tf][event_type] = {}
        current_latest = last_events[symbol][tf][event_type].get('latest', {}) or {}
        if current_latest and current_latest.get('timestamp'):
            try:
                curr_ts = pd.to_datetime(current_latest['timestamp'], utc=True)
                new_ts = pd.to_datetime(event_ts_str, utc=True)
                if new_ts < curr_ts:
                    return
            except Exception: pass
        last_events[symbol][tf][event_type]['previous'] = current_latest.copy()
        last_events[symbol][tf][event_type]['latest'] = {
            'price': event.get('price'),
            'timestamp': event_ts_str }
    except Exception as e:
        logger.error(f"Error updating last_events for {symbol}: {e}")

def load_symbols_active():
    global symbols_active
    try:
        with open(SYMBOLS_ACTIVE, 'r') as file:
            content = file.read()
            if not content.strip():
                raise ValueError(f"{SYMBOLS_ACTIVE} is empty.")
            symbols_active = list(set(json.loads(content)))
    except Exception as e:
        logger.error(f"Error loading SYMBOLS_ACTIVE: {e}")
        symbols_active = []

def start_symbols_loader():
    """Periodically refreshes all symbol files every 8 minutes."""
    while True:
        load_symbols_active()  # Refresh symbols_active
        #print(f"Updated symbols_active: {symbols_active}")  # Debugging print
        time.sleep(120)  # 8 minutes

# async def gateway_redis_monitor():
#     """
#     Advanced gateway Redis monitoring with failure counting and automatic reconnection.
#     """
#     global redis_client_read, _gateway_redis_failures
    
#     while True:
#         try:
#             await asyncio.sleep(30)  # Check every 30 seconds
            
#             if redis_client_read:
#                 try:
#                     redis_client_read.ping()
#                     # Reset failure counter on successful ping
#                     if hasattr(globals(), '_gateway_redis_failures'):
#                         _gateway_redis_failures = 0
#                     logger.debug(" Gateway Redis connection healthy")
#                 except Exception as e:
#                     logger.warning(f" Gateway Redis ping failed: {e}")
                    
#                     if not hasattr(globals(), '_gateway_redis_failures'):
#                         _gateway_redis_failures = 0
#                     _gateway_redis_failures += 1
                    
#                     if _gateway_redis_failures >= 3:  # Allow 3 failures before removing
#                         logger.warning(" Gateway Redis failed 3 times, removing from sources")
#                         redis_client_read = None
#                         _gateway_redis_failures = 0
#                     else:
#                         logger.debug(f" Gateway Redis failure {_gateway_redis_failures}/3, keeping connection")
#             else:
#                 # Try to reconnect to gateway Redis
#                 try:
#                     import os
#                     gw_host = os.environ.get("GATEWAY_REDIS_HOST", "localhost")
#                     gw_port = int(os.environ.get("GATEWAY_REDIS_PORT", "6379"))
                    
#                     new_redis_client = redis.Redis(
#                         host=gw_host,
#                         port=gw_port,
#                         db=0,
#                         decode_responses=True,
#                         socket_connect_timeout=10,
#                         socket_timeout=30,
#                         health_check_interval=30,
#                         max_connections=100,
#                     )
#                     new_redis_client.ping()
#                     redis_client_read = new_redis_client
#                     _gateway_redis_failures = 0
#                     logger.info(f" Gateway Redis connection restored at {gw_host}:{gw_port}")
#                 except Exception as e:
#                     logger.debug(f"Could not restore gateway Redis connection to {gw_host}:{gw_port}: {e}")
                    
#         except Exception as e:
#             logger.error(f"Error in gateway Redis monitor: {e}")
#             await asyncio.sleep(10)


# async def cleanup():
#     """Clean up resources before shutdown."""
#     global redis_client, redis_client_read
    
#     try:
#         if redis_client:
#             redis_client.close()
#             logger.info("Local Redis client closed")
#     except Exception as e:
#         logger.error(f"Error closing local Redis client: {e}")
    
#     try:
#         if redis_client_read:
#             redis_client_read.close()
#             logger.info("Gateway Redis client closed")
#     except Exception as e:
#         logger.error(f"Error closing gateway Redis client: {e}")


async def main():
    global symbols_active, redis_manager, local_redis, gateway_redis
    try:
        redis_manager = await get_simple_redis_manager()
        if not redis_manager:
            logger.warning("⚠️ Redis not available - continuing with file operations only")
        else:
            connections = redis_manager.connections
            local_redis = connections.get("local")
            gateway_redis = connections.get("gateway")
            logger.info(f"Redis connections established: Local={'✅' if local_redis else '❌'}, Gateway={'✅' if gateway_redis else '❌'}")
    except Exception as e:
        logger.warning(f"Redis initialization failed - continuing with file operations only: {e}")
    load_symbols_active()
    loader_thread = threading.Thread(target=start_symbols_loader, daemon=True)
    loader_thread.start()
    symbols_from_file = []
    try:
        # Load symbols from symbols.json - ONLY work with these symbols
        logger.info(f"Attempting to load symbols from: {SYMBOLS_FILE}")
        with open(SYMBOLS_FILE, 'r') as f:
            symbols = json.load(f)
        logger.info(f"Loaded {len(symbols)} symbols from symbols.json")
        
        # Create symbol_timeframes dictionary mapping each symbol to available timeframes
        symbol_timeframes = {}
        for symbol in symbols:
            symbol_timeframes[symbol] = TIMEFRAMES.copy()
        
        logger.info(f"Created symbol_timeframes for {len(symbols)} symbols")
            
    except Exception as e:
        logger.warning(f"Could not load symbols from {SYMBOLS_FILE}: {e}")
        logger.info("Falling back to loading symbols from cache directory")
        symbols = symbols_from_file
        
        # Create symbol_timeframes for fallback symbols
        symbol_timeframes = {}
        for symbol in symbols:
            symbol_timeframes[symbol] = TIMEFRAMES.copy()
        logger.info(f"Created symbol_timeframes for {len(symbols)} fallback symbols")

    logger.info(f"Final symbols with timeframes: {len(symbols)} symbols")
    #logger.info(f"Symbols found: {symbols}")

    loaded_events = load_last_events()
    if loaded_events:
        last_events = loaded_events
        last_events = ensure_last_events_structure(last_events, symbols, symbol_timeframes)
    else:
        last_events = await initialize_previous_data(symbols, symbol_timeframes, KLINES_CACHE_DIR)
        last_events = ensure_last_events_structure(last_events, symbols, symbol_timeframes)
        save_last_events(last_events)
        logger.info("Initialized last_events and saved to file.")

    # Validate last_events structure
    if not validate_last_events(last_events, symbol_timeframes):
        logger.error("last_events structure is invalid. Re-initializing.")
        last_events = await initialize_previous_data(symbols, symbol_timeframes, KLINES_CACHE_DIR)
        last_events = ensure_last_events_structure(last_events, symbols, symbol_timeframes)
        if validate_last_events(last_events, symbol_timeframes):
            save_last_events(last_events)
            logger.info("Re-initialized last_events and saved to file.")
        else:
            logger.critical("Failed to validate last_events after re-initialization. Exiting.")
            sys.exit(1)
    logger.debug(f"Initial last_events structure: {json.dumps(last_events, indent=4)}")
    asyncio.create_task(schedule_cross_recalcs(symbols, TIMEFRAMES))
    logger.info("✅ Started background task for cross recalculations.")

    while True:
        all_signals = {}
        all_signals_for_json_save = {} 
        current_iteration = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
     #   print(f"Starting new iteration at {current_iteration} UTC")
      #  logger.info(f"Starting new iteration at {current_iteration} UTC")

        # Schedule cross recalculations for all symbols and timeframes
      #  await schedule_cross_recalcs(symbols, TIMEFRAMES)

        now = datetime.now(timezone.utc)
        timeframe_should_process = {}
        for timeframe in TIMEFRAMES:
            timeframe_should_process[timeframe] = should_process_timeframe(timeframe, now)
        for symbol in symbols:
            for timeframe in TIMEFRAMES:
                if not timeframe_should_process.get(timeframe, False):
                    continue
                if timeframe not in symbol_timeframes[symbol]:
                    logger.debug(f"{symbol} does not have data for timeframe {timeframe}. Skipping.")
                    continue
                if symbol not in all_signals:
                    all_signals[symbol] = {}
                if timeframe not in all_signals[symbol]:
                    all_signals[symbol][timeframe] = {
                        'stoch_crossover': {'latest': None, 'previous': None},
                        'stoch_crossunder': {'latest': None, 'previous': None}, }
                last_crossover = last_events.get(symbol, {}).get(timeframe, {}).get('stoch_crossover', {})
                last_crossunder = last_events.get(symbol, {}).get(timeframe, {}).get('stoch_crossunder', {})
                all_signals[symbol][timeframe]['stoch_crossover']['latest'] = last_crossover.get('latest', None)
                all_signals[symbol][timeframe]['stoch_crossover']['previous'] = last_crossover.get('previous', None)
                all_signals[symbol][timeframe]['stoch_crossunder']['latest'] = last_crossunder.get('latest', None)
                all_signals[symbol][timeframe]['stoch_crossunder']['previous'] = last_crossunder.get('previous', None)
                
                
                file_path = os.path.join(KLINES_CACHE_DIR, f"{symbol}_{timeframe}.json")
                #df = read_ohlcv(file_path)
                df = await get_ohlcv_df(symbol, timeframe)
                if df.empty:
                    logger.debug(f"No data available for {symbol} on {timeframe}. Skipping.")
                    continue
                last_candle_ts = df['timestamp'].iloc[-1]
                if hasattr(last_candle_ts, 'iloc'): last_candle_ts = last_candle_ts.iloc[0]
                if pd.isna(last_candle_ts): continue
                if last_candle_ts.tzinfo is None: last_candle_ts = last_candle_ts.replace(tzinfo=timezone.utc)
                indicators = calculate_indicators(symbol, df, timeframe)
                if 'stoch_rsi' not in indicators or indicators['stoch_rsi'] is None:
                    logger.warning(f"Stoch RSI not calculated for {symbol} on timeframe {timeframe}. Skipping.")
                    continue
                if symbol not in last_events:
                    last_events[symbol] = {}
                if timeframe not in last_events[symbol]:
                    last_events[symbol][timeframe] = {}
                if indicators.get('stoch_k_series') is not None and not indicators['stoch_k_series'].empty:
                    k_series = indicators['stoch_k_series']
                    d_series = indicators['stoch_d_series']
                    k_val = float(k_series.iloc[-1]) if hasattr(k_series, 'iloc') else float(k_series.values[-1])
                    d_val = float(d_series.iloc[-1]) if hasattr(d_series, 'iloc') else float(d_series.values[-1])
                    k_prev = float(k_series.iloc[-2]) if len(k_series) > 1 and hasattr(k_series, 'iloc') else (float(k_series.values[-2]) if len(k_series.values) > 1 else k_val)
                    d_prev = float(d_series.iloc[-2]) if len(d_series) > 1 and hasattr(d_series, 'iloc') else (float(d_series.values[-2]) if len(d_series.values) > 1 else d_val)
                    stoch_ts_str = last_candle_ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    last_events[symbol][timeframe]['stoch_k'] = max(0, min(100, k_val))
                    last_events[symbol][timeframe]['stoch_d'] = max(0, min(100, d_val))
                    last_events[symbol][timeframe]['stoch_k_prev'] = max(0, min(100, k_prev))
                    last_events[symbol][timeframe]['stoch_d_prev'] = max(0, min(100, d_prev))
                    last_events[symbol][timeframe]['stoch_timestamp'] = stoch_ts_str
                # Use historical detection for daily timeframe, live detection for others
                if timeframe == 'D':
                    events = detect_stoch_crossovers(symbol, df, indicators['stoch_rsi'])
                else:
                    events = await detect_stoch_crossovers_live(symbol, df, indicators['stoch_rsi'])

                if not events:
                    logger.debug(f"No events detected for {symbol} on timeframe {timeframe}. Skipping.")
                    continue

                latest_event = events[-1]  # Get the latest event
                event_type = latest_event['type']
                event_price = latest_event['price']
                event_timestamp = latest_event['timestamp']

                last_latest_event = last_events.get(symbol, {}).get(timeframe, {}).get(event_type, {}).get('latest', {})

                try:
                    if last_latest_event and pd.to_datetime(event_timestamp, utc=True) <= pd.to_datetime(last_latest_event.get('timestamp', ""), utc=True):
                        logger.debug(f"Event at {event_timestamp} already processed for {symbol} on {timeframe}. Skipping.")
                        continue  # Skip processing this event
                except Exception as e:
                    logger.warning(f"Error comparing event timestamps for {symbol} on {timeframe}: {e}")
                    # If we can't compare, assume it's not a duplicate and continue processing
                    pass

                if not event_type or not event_price or not event_timestamp:
                    logger.debug(f"Incomplete event data for {symbol} on {timeframe}. Skipping.")
                    continue

                # Update all_signals with latest and previous events
                all_signals[symbol][timeframe][event_type]['latest'] = latest_event
                if len(events) > 1:
                    all_signals[symbol][timeframe][event_type]['previous'] = events[-2]

                # Update last_events
                update_last_events(symbol, timeframe, latest_event, last_events)

                # COMMENTED OUT: Handle signals for '3m' timeframe - stoch 3m signals now come from ez_positions_quick
                # if timeframe == '3m':
                #     atr_values = indicators.get('atr', [])
                #     if len(atr_values) == 0:
                #         logger.warning(f"ATR not calculated for {symbol} on {timeframe}. Skipping ATR-based condition.")
                #         continue
                #     # Use the last ATR value
                #     current_atr = atr_values[-1]
                #     send_signal = False
                #     signal = {}
                #     try:
                #         event_time = pd.to_datetime(event_timestamp, utc=True).to_pydatetime()
                #         time_diff = datetime.now(timezone.utc) - event_time
                #     except Exception as e:
                #         logger.error(f"Error parsing event_timestamp '{event_timestamp}' for {symbol} on {timeframe}: {e}")
                #         continue
                #     # Implement 120 seconds condition
                #     if time_diff > timedelta(seconds=120):
                #         logger.debug(f"Signal for {symbol} is too old ({time_diff.seconds} seconds). Saving and suppressing.")
                #         continue
                #     if event_type == 'stoch_crossover':
                #         previous_crossover = last_events.get(symbol, {}).get(timeframe, {}).get('stoch_crossover', {}).get('previous', {})
                #         if previous_crossover:
                #             price_change = abs(event_price - previous_crossover.get('price', 0))
                #             if price_change >= 2 * current_atr:
                #                 send_signal = True
                #                 signal = {'symbol': symbol, 'event_type': event_type, 'action': 'BUY', 'price': event_price, 'current_stoch_crossover_price_3m': event_price, 'previous_stoch_crossover_price_3m': previous_crossover.get('price'), 'timestamp': event_timestamp}
                #     elif event_type == 'stoch_crossunder':
                #         previous_crossunder = last_events.get(symbol, {}).get(timeframe, {}).get('stoch_crossunder', {}).get('previous', {})
                #         if previous_crossunder:
                #             price_change = abs(event_price - previous_crossunder.get('price', 0))
                #             if price_change >= 2 * current_atr:
                #                 send_signal = True
                #                 signal = {'symbol': symbol, 'event_type': event_type, 'action': 'SELL', 'price': event_price, 'current_stoch_crossunder_price_3m': event_price, 'previous_stoch_crossunder_price_3m': previous_crossunder.get('price'), 'timestamp': event_timestamp}
                #     if send_signal and is_signal_valid(signal) and not is_signal_sent_recently(signal) and symbol in symbols_active:
                #         try:
                #             if redis_manager:
                #                 success_count = await redis_manager.publish( channel=REDIS_CHANNELS["signals_data"],  message=signal, expiry_seconds=60, data_type="trade_signal")
                #             else:
                #                 success_count = 0
                #             logger.info(f"Broadcasted signal to {success_count} Redis instances.")
                #             add_signal_to_cache(signal)
                #             time.sleep(SIGNAL_PUBLISH_SLEEP)  # Prevent overwhelming Redis
                #         except Exception as e:
                #             logger.error(f"Error publishing signal to Redis for {symbol} on {timeframe}: {e}")
                #     else:
                #         if not is_signal_valid(signal):
                #             logger.debug(f"Invalid signal detected for {symbol} on {timeframe}: {signal}")

        simplify_signals(last_events, BASE_PATH / "data" / "simplified_signals.json")

        # # Save last_events to file
        save_last_events(last_events)


                    # Sleep before the next iteration
        #logger.info(f"Iteration complete. Sleeping for {SLEEP_INTERVAL} seconds.")
        #print(f"Iteration complete. Sleeping for {SLEEP_INTERVAL} seconds.\n")
        await asyncio.sleep(SLEEP_INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script terminated by user.")
        # Note: cleanup will be handled by the main function
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
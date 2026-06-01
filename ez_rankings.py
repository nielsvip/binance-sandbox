# pylint: disable=W,C,R,I
#!/home/niels/.conda/envs/binance_env/bin/python3
import asyncio
import glob
import json
import logging
import math
import os
import random
import re
import shutil
import signal
import ssl
import sys
import tempfile
import time
import warnings
from logging.handlers import RotatingFileHandler

import aiohttp
import certifi
import numpy as np
import orjson
import websockets

warnings.filterwarnings(  "ignore", category=UserWarning, message=".*pkg_resources is deprecated.*")
import subprocess
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import aiofiles
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import pandas as pd
import pandas_ta as ta
import plotly.graph_objs as go
import psutil
import redis.asyncio as redis
from asyncio_throttle import Throttler
from dateutil.relativedelta import relativedelta
from dotenv import dotenv_values
from matplotlib.dates import DateFormatter
from numpy import nan as npNaN
from pandas_ta import atr
from scipy.signal import find_peaks
from scipy.stats import linregress
from ta.momentum import StochRSIIndicator

from binance.client import Client
from binance.exceptions import BinanceAPIException
from config import Config
from utils import (REDIS_CHANNELS, _resolve_klines_directories,
                   force_usdc_if_needed, force_usdc_in_list,
                   get_comprehensive_klines, get_current_environment,
                   get_current_price, get_simple_redis_manager,
                   load_environment_from_gpg, orjson_default,
                   parse_position_key, safe_fetch_float,
                   set_global_redis_manager, setup_logger,
                   standardize_kline_data_for_json)

config = Config()

# BACKTEST_CHANGE_49: Top backtest performers get ranking bonus (weight 0.2, capped at 5.0)
BACKTEST_SHARPE = {"CELOUSDT": 26858, "DYDXUSDT": 2974, "GTCUSDT": 5015, "GALAUSDT": 9426, "TIAUSDC": 13041, "XTZUSDT": 4028, "SKLUSDT": 3910, "CELRUSDT": 3498, "TLMUSDT": 2026, "RVNUSDT": 1905, "ARBUSDC": 1883, "RSRUSDT": 1195, "SNXUSDT": 1075, "OMUSDT": 1036, "VANAUSDT": 998, "FETUSDT": 971, "CHRUSDT": 969, "PIXELUSDT": 925, "XAGUSDT": 900, "RIVERUSDT": 867}

# ===== VOLUME FILTERING CONFIGURATION =====
ENABLE_VOLUME_FILTERING = True  # Set to False to disable volume filtering entirely
VOLUME_THRESHOLD_USDT = 80000.0  # Conservative threshold: 80k USDT (only removes truly low-volume tokens)
logger = setup_logger('ez_rankings', str(config.LOG_FILE_EZ_RANKINGS), logging.INFO)
for handler in logger.handlers:
    if hasattr(handler, 'baseFilename'):
        handler.close()
        handler.baseFilename = str(config.LOG_FILE_EZ_RANKINGS)
load_environment_from_gpg(logger)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
redis_manager = None
redis = None  # Direct Redis client for resilient connections

API_KEY = os.getenv('ang_API_KEY')
API_SECRET = os.getenv('ang_API_SECRET')

WS_URL = config.WS_URL
BINANCE_API_BASE = config.BINANCE_API_BASE

BASE_PATH = config.BASE_PATH
# LOG_FILE is now handled by utils.py unified logging
RANKINGS_DIR = config.RANKINGS_DIR
SYMBOLS_FILE = config.SYMBOLS_FILE
DATA_DIR = config.DATA_DIR
RANKINGS_FILE = DATA_DIR / "rankings.json"
PLOTS_DIR = config.PLOTS_DIR
KLINES_CACHE_DIR = config.KLINES_CACHE_DIR
PRICE_CACHE_FILE = config.PRICE_CACHE_FILE
PRICE_CACHE_FILE_2 = config.PRICE_CACHE_FILE_2
PRICE_CACHE_FILE_3 = config.PRICE_CACHE_FILE_3
RANKING_RESULTS_FILE = config.RANKING_RESULTS_FILE
WINNERS_30_FILE = config.WINNERS_20_FILE # Alias for consistency
LOSERS_30_FILE = config.LOSERS_20_FILE # Alias for consistency
WINNERS_15M_FILE = config.WINNERS_15M_FILE
LOSERS_15M_FILE = config.LOSERS_15M_FILE
WINNERS_30R_FILE = DATA_DIR / "winners_30r" # Assuming this path from original
LOSERS_30R_FILE = DATA_DIR / "losers_30r"   # Assuming this path from original
REDIS_CHANNEL_SIGNALS = config.REDIS_CHANNEL_SIGNALS
_news_sentiment_cache: Dict[str, float] = {}

async def _load_news_sentiment(rm=None):
    global _news_sentiment_cache
    try:
        if rm and hasattr(rm, 'connections'):
            for name, conn in rm.connections.items():
                if conn is None: continue
                try:
                    # Prefer crypto-only key (cleaner, no stock symbol bleed)
                    raw = await conn.get('news_sentiment_crypto') or await conn.get('news_sentiment_bulk')
                    if raw:
                        _news_sentiment_cache = {k: float(v) for k, v in json.loads(raw).items()}
                        return
                except Exception: pass
        fallback = Path(config.BASE_PATH) / 'data' / 'news_sentiment.json'
        if fallback.exists():
            with open(fallback, 'r') as f: _news_sentiment_cache = {k: float(v) for k, v in json.load(f).items()}
    except Exception as e:
        logger.debug(f"News sentiment load: {e}")
SIGNALS_FILE = config.SIGNALS_FILE
CROSSES_FILE = config.CROSSES_FILE
BAND_FILE = config.BAND_FILE
FINAL_SCORE_FILE = config.FINAL_SCORE_FILE
FINAL_SCORE_R_FILE = DATA_DIR / "final_score_recent.json" # Assuming this path
PROX_FILE = config.PROX_FILE
INDICATORS_FILE = DATA_DIR / "latest_market_data.json" # Assuming this path
SCORE_RANGES_FILE = DATA_DIR / "trend_val_ranges.json" # Assuming this path
MAX_MEMORY_GB = config.MAX_MEMORY_GB # For reading klines data from gateway
live_usdc_pairs = set()  # Global variable for USDC pairs
MAX_FILES = config.MAX_FILES
DAYS_PLOT = config.DAYS_PLOT
ssl_context = config.ssl_context
# Get current environment for rate limiting
current_env = get_current_environment()['env']
throttler = Throttler(rate_limit=config.EZ_RANKINGS_THROTTLER_RATE.get(current_env, 500))
crossover_prices = {'current': None, 'previous': None}
crossunder_prices = {'current': None, 'previous': None}
# STREAMING APPROACH: No more in-memory caching
# kline_cache = defaultdict(list)  # REMOVED
kline_data_lock = asyncio.Lock()
all_symbols = []
PREV_WINNERS_15M = []
PREV_LOSERS_15M = []
PREV_WINNERS_30 = []
PREV_LOSERS_30 = []
PREV_ALL_GREEN_ARROWS = []  # Track symbols with all green arrows for reversal detection
PREV_ALL_RED_ARROWS = []  # Track symbols with all red arrows for reversal detection
ARROW_SIGNAL_STATE = {}  # Track last signal state per symbol: "up" or "down"
RANKING_DATA = []
RANKING_INFO = {}
LAST_RANKING_TIME = None
LAST_CLIP_OLD_SIGNALS_TIME = None

market_index_history = []
last_market_mode = Config._CURRENT_MARKET_MODE
_cached_market_index = None
_cached_market_index_timestamp = 0
_cached_indicators_hash = None
top_15_15m=[]
bottom_15_15m=[]
items_15m=[]
returns_15m=[]
to_save_top15_15m = []
to_save_bottom15_15m = []
top_100_long_term = []
to_save_top20 = []
to_save_bottom20 = []
to_save_top30 = []
to_save_bottom30 = []
to_save_top30_r = []
to_save_bottom30_r = []
to_save_top15_r = []
to_save_bottom15_r = []
to_save_top50 = []
to_save_bottom50 = []
combined_winners = []
combined_losers=[]
latest_winner_15_file = []
latest_loser_15_file = []
symbols_ang_long_list= []
symbols_inf_long_list= []
symbols_inf_short_list= []
symbols_ang_short_list= []
symbols_active=[]
MIN_TIME_BETWEEN_SAME_SIGNAL = 600 
last_sent = {} 
crosses_data={}
price_cache={}
previous_signals = {}
signals_data = {}
last_kline_updates = {}
indicators_data = {}
_subscribers_started = False
bs={}
rp={}
rpg={}
rpn={}
fs={}
fsr={}
prox={}
mark_price_cache = {}  # In-memory mark price cache
now = datetime.now(timezone.utc)
file_last_modified = 0 # For load_indicators_data
indicators_last_loaded = 0 # For load_indicators_data
signals_file_lock = asyncio.Lock()

#####################################################
# Utility for converting np scalars
#####################################################
async def monitor_memory():
    process = psutil.Process(os.getpid())
    while True:
        mem_bytes = process.memory_info().rss
        mem_gb = mem_bytes / (1024 ** 3)
        if mem_gb > MAX_MEMORY_GB:
            print(f"Memory usage exceeded: {mem_gb:.2f} GB. Restarting...")
            os.execv(sys.executable, ['python'] + sys.argv)  # Restart script
        await asyncio.sleep(config.MEMORY_MONITOR_SLEEP_SECONDS) 
def recursively_convert_np(obj):
    """Converts numpy types, PosixPath, and datetime to serializable types."""
    if isinstance(obj, dict):
        return {k: recursively_convert_np(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [recursively_convert_np(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(recursively_convert_np(v) for v in obj)
    elif isinstance(obj, Path):  #  Convert PosixPath to string
        return str(obj)
    elif isinstance(obj, set):  #  Convert sets to lists (Redis doesn't support sets)
        return list(obj)
    elif isinstance(obj, datetime):  # Convert datetime to ISO string
        return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ') if obj.tzinfo else obj.replace(tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    elif isinstance(obj, (np.integer)):
        return int(obj)
    elif isinstance(obj, (np.floating)):
        return float(obj)
    elif isinstance(obj, (np.bool_)):
        return bool(obj)
    return obj
def to_float(obj):
    if isinstance(obj, dict):
        return {k: to_float(v) for k,v in obj.items()}
    elif isinstance(obj, list):
        return [to_float(x) for x in obj]
    elif isinstance(obj, (np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, (np.int64, np.int32)):
        return int(obj)
    return obj
def convert_np_scalars(obj):
    if isinstance(obj, dict):
        return {k: convert_np_scalars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_np_scalars(x) for x in obj]
    elif isinstance(obj, (np.float32, np.float64)):
        return float(obj)
    elif isinstance(obj, (np.int32, np.int64)):
        return int(obj)
    return obj

######################################################################
# Redis initialization
######################################################################
def json_loads(s):
    return orjson.loads(s)  # type: ignore # pylint: disable=no-member,c-extension-no-member
def json_dumps(obj):
    return orjson.dumps(obj).decode("utf-8")  # type: ignore # pylint: disable=no-member,c-extension-no-member

async def _init_redis_direct():
    """Initialize direct Redis client using EXACT same pattern as ez_prices_ws.py"""
    global redis
    try:
        import redis.asyncio as redis
        from redis import exceptions as redis_exceptions

        from config import Config
        from utils import get_current_environment, orjson_default
        
        config = Config()
        env_info = get_current_environment()
        host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
        # LONG timeout - Redis can take time to appear
        redis = redis.Redis(host=host, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True, 
                          socket_connect_timeout=30, socket_timeout=60, health_check_interval=30, 
                          max_connections=3000, retry_on_timeout=True,
                          retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError])
        # NON-BLOCKING: No ping wait, connects in background
        logger.info(f"⏳ Direct Redis client created ({host}:{config.REDIS_PORT}) - connecting in background")
        return redis
    except Exception as e:
        logger.warning(f"⚠️ Could not create direct Redis client: {e}")
        return None

async def initialize_redis_manager():
    """Initialize Redis manager with proper error handling"""
    global redis_manager, redis
    
    # Initialize direct Redis client - matching ez_prices_ws.py pattern
    if redis is None:
        redis = await _init_redis_direct()
    
    try:
        redis_manager = await get_simple_redis_manager()
        logger.info("✅ Redis manager initialized successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to initialize Redis manager: {e}")
        return False
        
async def start_redis_subscribers():
    """Start Redis subscribers for real-time data updates"""
    global redis_manager, _subscribers_started
    if _subscribers_started or not redis_manager:
        if _subscribers_started:
            logger.debug("Redis subscribers already started, skipping")
        return
    try:
        if redis_manager:
            await redis_manager.subscribe(
                channel=REDIS_CHANNELS["klines_updates"],
                callback=handle_klines_update,
                data_type="klines" )
        if redis_manager:
            await redis_manager.subscribe(
                channel=REDIS_CHANNELS["market_data"],
                callback=handle_market_data_update,
                data_type="market_data" )
        _subscribers_started = True
        logger.info("✅ Started Redis subscribers for real-time updates")
        
    except Exception as e:
        logger.error(f"❌ Failed to start Redis subscribers: {e}")

async def handle_klines_update(data):
    """Handle incoming klines updates from Redis"""
    try:
        symbol = data.get('symbol')
        interval = data.get('interval')
        logger.debug(f"📊 Received klines update for {symbol}-{interval}")
        
        # # Trigger reprocessing if this symbol is in our active list
        # if symbol in symbols_active_list:
        #     # You can add logic here to refresh analysis for this symbol
        #     pass
            
    except Exception as e:
        logger.error(f"Error handling klines update: {e}")

async def handle_market_data_update(data):
    """Handle incoming market data updates from Redis"""
    try:
        logger.debug("📈 Received market data update")
        # Reload indicators when market data is updated
        await load_indicators_data()
    except Exception as e:
        logger.error(f"Error handling market data update: {e}")
def _load_indicators_from_file():
    """
    Original function to load indicators from the JSON file. (This is your old load_indicators_data)
    """
    global indicators_data, file_last_modified
    try:
        if INDICATORS_FILE.exists():
            current_mtime = INDICATORS_FILE.stat().st_mtime
            if current_mtime == file_last_modified:
                logger.debug("Indicators file on disk has not changed, skipping reload.")
                return # No change in file, no need to reload from disk
            file_last_modified = current_mtime

            with open(INDICATORS_FILE, 'rb') as f:
                content = f.read()
            if not content.strip():
                indicators_data = {}
            else:
                indicators_data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
            logger.debug(f"Loaded indicators data from disk: {INDICATORS_FILE}.")
        else:
            logger.warning(f"Indicators file {INDICATORS_FILE} not found on disk.")
            indicators_data = {}
    except Exception as e:
        logger.error(f"Unexpected error loading indicators from file: {e}")
        indicators_data = {}
        
# async def load_indicators_data():
#     """Load indicators data using unified Redis manager"""
#     global indicators_data, redis_manager
#     if redis_manager:
#         for client in redis_manager.get_broadcast_clients():
#             try:
#                 json_data_str = await client.get("latest_market_data")
#                 if json_data_str:
#                     loaded_data = orjson.loads(json_data_str)  # type: ignore # pylint: disable=no-member,c-extension-no-member
#                     if isinstance(loaded_data, dict) and loaded_data:
#                         indicators_data = loaded_data
#                         logger.info(f"✅ Loaded {len(indicators_data)} symbols' indicators from Redis")
#                         return
#             except Exception as e:
#                 logger.debug(f"Failed to load indicators from Redis: {e}")
#                 continue
#     logger.info("Executing file fallback for indicators data...")
#     def try_repair_json(filepath):
#         try:
#             with open(filepath, "r", encoding="utf-8") as f:
#                 raw = f.read().strip()
#             last_brace = raw.rfind('}')
#             last_bracket = raw.rfind(']')
#             end_pos = max(last_brace, last_bracket)
#             if end_pos == -1:
#                 return None
#             for i in range(end_pos + 1, 0, -1):
#                 chunk = raw[:i]
#                 try:
#                     data = json.loads(chunk)
#                     if isinstance(data, (dict, list)):
#                         backup_path = str(filepath) + ".bak"
#                         if os.path.exists(backup_path):
#                             backup_path += f".{int(time.time())}"
#                         os.rename(filepath, backup_path)
#                         with open(filepath, "w", encoding="utf-8") as f_repaired:
#                             json.dump(data, f_repaired, indent=2)
#                         logger.info(f"Repaired corrupted file: {filepath.name}")
#                         return filepath
#                 except json.JSONDecodeError:
#                     continue
#         except Exception as e:
#             logger.warning(f"Could not repair {filepath.name}: {e}")
#         return None
#     try:
#         data_dir = Path(config.DATA_DIR)
#         market_data_files = list(data_dir.glob("market_data_*.json"))
#         if not market_data_files:
#             default_file = data_dir / "latest_market_data.json"
#             if default_file.exists():
#                 try:
#                     with open(default_file, 'rb') as f:
#                         content = f.read()
#                     if content.strip():
#                         indicators_data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
#                         logger.info(f"✅ Loaded {len(indicators_data)} symbols from fallback file: {default_file.name}")
#                         return
#                 except orjson.JSONDecodeError:  # type: ignore # pylint: disable=no-member,c-extension-no-member
#                     try_repair_json(default_file)
#                     try:
#                         with open(default_file, 'rb') as f:
#                             content = f.read()
#                         if content.strip():
#                             indicators_data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
#                             logger.info(f"✅ Loaded {len(indicators_data)} symbols from repaired file: {default_file.name}")
#                             return
#                     except Exception:
#                         pass
#         else:
#             files_sorted = sorted(market_data_files, key=lambda f: f.stat().st_mtime, reverse=True)
#             for file_path in files_sorted:
#                 try:
#                     with open(file_path, 'rb') as f:
#                         content = f.read()
#                     if content.strip():
#                         indicators_data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
#                         logger.info(f"✅ Loaded {len(indicators_data)} symbols from fallback file: {file_path.name}")
#                         return
#                 except orjson.JSONDecodeError:  # type: ignore # pylint: disable=no-member,c-extension-no-member
#                     logger.warning(f"Broken JSON in {file_path.name}, attempting repair...")
#                     repaired = try_repair_json(file_path)
#                     if repaired:
#                         try:
#                             with open(repaired, 'rb') as f:
#                                 content = f.read()
#                             if content.strip():
#                                 indicators_data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
#                                 logger.info(f"✅ Loaded {len(indicators_data)} symbols from repaired file: {file_path.name}")
#                                 return
#                         except Exception as e:
#                             logger.warning(f"Repaired file still unreadable: {e}")
#                             continue
#                     else:
#                         logger.warning(f"Could not repair {file_path.name}, trying next file...")
#                         continue
#                 except Exception as e:
#                     logger.warning(f"Error loading {file_path.name}: {e}, trying next file...")
#                     continue
#         logger.error("❌ CRITICAL: All fallback files failed. No indicator data available.")
#         indicators_data = {}
#     except Exception as e:
#         logger.error(f"❌ CRITICAL: Error during file fallback for indicators: {e}", exc_info=True)
#         indicators_data = {}

async def load_indicators_data():
    """Load indicators data using unified Redis manager with robust file fallback"""
    global indicators_data, redis_manager
    
    # 1. Try Redis First
    if redis_manager:
        for client in redis_manager.get_broadcast_clients():
            try:
                json_data_str = await client.get("latest_market_data")
                if json_data_str:
                    loaded_data = orjson.loads(json_data_str)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                    if isinstance(loaded_data, dict) and loaded_data:
                        indicators_data = loaded_data
                        logger.info(f"✅ Loaded {len(indicators_data)} symbols' indicators from Redis")
                        return
            except Exception as e:
                logger.debug(f"Failed to load indicators from Redis: {e}")
                continue

    # 2. File Fallback
    logger.info("Executing file fallback for indicators data...")
    
    def try_repair_json(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            last_brace = raw.rfind('}')
            last_bracket = raw.rfind(']')
            end_pos = max(last_brace, last_bracket)
            if end_pos == -1: return None
            
            # Simple truncation attempt
            chunk = raw[:end_pos+1]
            try:
                data = json.loads(chunk)
                return data
            except Exception:
                return None
        except Exception:
            return None

    try:
        data_dir = Path(config.DATA_DIR)
        # Get all potential files
        market_data_files = list(data_dir.glob("market_data_*.json"))
        
        if not market_data_files:
            # Try default file
            default_file = data_dir / "latest_market_data.json"
            if default_file.exists():
                try:
                    with open(default_file, 'rb') as f:
                        indicators_data = orjson.loads(f.read())  # type: ignore # pylint: disable=no-member,c-extension-no-member
                    logger.info(f"✅ Loaded {len(indicators_data)} symbols from default file")
                    return
                except Exception:
                    pass
        else:
            # FIX: Filter files that actually exist before sorting to prevent Race Condition
            valid_files = []
            for f in market_data_files:
                try:
                    if f.exists(): valid_files.append(f)
                except OSError: continue
            
            # Now sort safely
            files_sorted = sorted(valid_files, key=lambda f: f.stat().st_mtime, reverse=True)
            
            for file_path in files_sorted:
                try:
                    with open(file_path, 'rb') as f:
                        content = f.read()
                    if content.strip():
                        indicators_data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                        logger.info(f"✅ Loaded {len(indicators_data)} symbols from: {file_path.name}")
                        return
                except orjson.JSONDecodeError:  # type: ignore # pylint: disable=no-member,c-extension-no-member
                    logger.warning(f"⚠️ Corrupted JSON in {file_path.name}, attempting repair...")
                    repaired_data = try_repair_json(file_path)
                    if repaired_data:
                        indicators_data = repaired_data
                        logger.info(f"✅ Loaded {len(indicators_data)} symbols from repaired file")
                        return
                    # If repair fails, delete bad file to prevent future errors
                    try:
                        os.remove(file_path)
                        logger.info(f"🗑️ Deleted corrupted file: {file_path.name}")
                    except Exception: pass
                    continue
                except Exception as e:
                    logger.warning(f"Error loading {file_path.name}: {e}")
                    continue

        logger.warning("⚠️ No valid indicator data found on disk.")
        indicators_data = {}
        
    except Exception as e:
        logger.error(f"❌ CRITICAL: Error during file fallback for indicators: {e}", exc_info=True)
        indicators_data = {}

def save_scores(score_dict: dict, file_path: Path):
    """Save scores sorted from 100 to -100."""
    sorted_scores = dict(sorted(score_dict.items(), key=lambda x: x[1], reverse=True))
    if file_path.exists():
        try:
            with open(file_path, "r") as f:
                existing = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load existing scores from {file_path}: {e}")
            existing = {}
    else:
        existing = {}
    existing.update(sorted_scores)
    existing = dict(sorted(existing.items(), key=lambda x: x[1], reverse=True))
    existing = recursively_convert_np(existing)
    with open(file_path, "w") as f:
        json.dump(existing, f, indent=4)
    logger.info(f"Saved {len(sorted_scores)} scores to {file_path}. 253")
    
async def save_rankings_json(ranking_data_scalars: List[Dict[str, Any]]):
    """
    Save comprehensive rankings.json with multipliers for order sizing.
    
    Multiplier Calculation:
    - Range: 0.3 to 2.5 (30% to 250% of base order size)
    - Components:
      * Rank multiplier (35%): Based on percentile position (top rankers get higher multipliers)
      * Score multiplier (25%): Based on final_score_norm (-100 to +100)
      * Recent score multiplier (15%): Based on final_score_recent_norm (short-term momentum)
      * Trend multiplier (15%): Based on trend strength (stronger trends = higher multiplier)
      * Volume multiplier (10%): Based on relative volume (higher volume = slightly higher multiplier)
    
    Usage: order_size = base_order_size * get_ranking_multiplier(symbol)
    """
    if not ranking_data_scalars: return
    try:
        sorted_lt = sorted(ranking_data_scalars, key=lambda x: x.get("final_score_norm", 0.0), reverse=True)
        sorted_st = sorted(ranking_data_scalars, key=lambda x: x.get("final_score_recent_norm", 0.0), reverse=True)
        total_symbols = len(ranking_data_scalars)
        rankings_dict = {}
        for idx, entry in enumerate(sorted_lt):
            symbol = entry.get("symbol", "")
            if not symbol: continue
            lt_rank = idx + 1
            st_rank = next((i + 1 for i, e in enumerate(sorted_st) if e.get("symbol") == symbol), total_symbols)
            final_score_norm = entry.get("final_score_norm", 0.0)
            final_score_recent_norm = entry.get("final_score_recent_norm", 0.0)
            lt_percentile = (total_symbols - lt_rank + 1) / total_symbols
            st_percentile = (total_symbols - st_rank + 1) / total_symbols
            combined_percentile = (lt_percentile * 0.6 + st_percentile * 0.4)
            rank_multiplier = 0.4 + (combined_percentile * 1.6)
            score_multiplier = 0.5 + ((final_score_norm + 100) / 200) * 1.5
            recent_score_multiplier = 0.5 + ((final_score_recent_norm + 100) / 200) * 1.5
            trend_multiplier = 0.7 + (abs(entry.get("trend_val_norm_lt", 0.0)) / 100) * 0.6
            vol_multiplier = min(1.3, max(0.7, entry.get("rel_vol_raw", 1.0)))
            final_multiplier = (rank_multiplier * 0.35 + score_multiplier * 0.25 + recent_score_multiplier * 0.15 + trend_multiplier * 0.15 + vol_multiplier * 0.10)
            final_multiplier = max(0.3, min(2.5, final_multiplier))
            _sym_tier = 'B'
            _tier_mult = 1.0
            if config.TIER_ENABLED:
                try:
                    from utils import get_symbol_tier, get_tier_multiplier
                    _sym_tier = get_symbol_tier(symbol, default='B')
                    _tier_mult = get_tier_multiplier(symbol)
                    final_multiplier = max(0.3, min(2.5, final_multiplier * _tier_mult))
                except Exception:
                    pass
            rankings_dict[symbol] = {
                "lt_rank": lt_rank,
                "st_rank": st_rank,
                "final_score_norm": float(final_score_norm),
                "final_score_recent_norm": float(final_score_recent_norm),
                "lt_percentile": float(lt_percentile),
                "st_percentile": float(st_percentile),
                "combined_percentile": float(combined_percentile),
                "order_multiplier": float(final_multiplier),
                "tier": _sym_tier,
                "trend_val_norm_lt": float(entry.get("trend_val_norm_lt", 0.0)),
                "trend_val_norm_st": float(entry.get("trend_val_norm_st", 0.0)),
                "proximity_score_norm": float(entry.get("proximity_score_norm", 0.0)),
                "band_score": float(entry.get("band_score", 0.0)),
                "rel_vol_raw": float(entry.get("rel_vol_raw", 1.0)),
                "weighted_gains_lt": float(entry.get("weighted_gains_lt", 0.0)),
                "weighted_gains_st": float(entry.get("weighted_gains_st", 0.0))
            }
        # === DC MOMENT + QTY: cross-TF analysis + cross-symbol ranking ===
        _dc = {}
        _tfs_all = ['3m', '15m', '1h', '4h', 'D']
        for _sym in rankings_dict:
            _ind = indicators_data.get(_sym, {})
            if not _ind: continue
            _w, _p = {}, {}
            _cp = float(_ind.get('current_price') or 0)
            if _cp <= 0: continue
            for _tf in _tfs_all:
                _dw = float(_ind.get(f'dc_width_{_tf}') or 0)
                _dp = float(_ind.get(f'dc_position_{_tf}') or 0)
                if _dw <= 0:
                    _dch = float(_ind.get(f'dc_high_{_tf}') or 0)
                    _dcl = float(_ind.get(f'dc_low_{_tf}') or 0)
                    if _dch > 0 and _dcl > 0 and _dch > _dcl:
                        _dw = ((_dch - _dcl) / _dcl) * 100
                        _dp = max(0.0, min(1.0, (_cp - _dcl) / (_dch - _dcl)))
                if _dw > 0: _w[_tf] = _dw
                if 0 <= _dp <= 1.0: _p[_tf] = _dp
            if len(_w) < 2: continue
            _wc = _w.get('D', 0) * 0.50 + _w.get('4h', 0) * 0.30 + _w.get('1h', 0) * 0.20
            _ltf_w = sum(_w.get(t, 0) for t in ['3m', '15m']) / max(1, sum(1 for t in ['3m', '15m'] if t in _w))
            _htf_w = sum(_w.get(t, 0) for t in ['D', '4h', '1h']) / max(1, sum(1 for t in ['D', '4h', '1h'] if t in _w))
            _exp = _ltf_w / _htf_w if _htf_w > 0 else 0.0
            _hp = _p.get('D', 0.5) * 0.5 + _p.get('4h', 0.5) * 0.3 + _p.get('1h', 0.5) * 0.2
            _lp = _p.get('3m', 0.5) * 0.5 + _p.get('15m', 0.5) * 0.5
            _trend = (_hp - 0.5) * 2.0
            if _trend > 0:
                _pbd = max(0.0, _hp - _lp) / max(_hp, 0.01)
            else:
                _pbd = max(0.0, _lp - _hp) / max(1.0 - _hp, 0.01)
            _pbd = min(1.0, _pbd)
            _eb = min(1.3, max(1.0, _exp * 0.65 + 0.35)) if _exp > 1.0 else max(0.7, _exp)
            _moment = max(-100.0, min(100.0, _trend * _pbd * _eb * 100.0))
            _dc[_sym] = {'wc': _wc, 'exp': round(_exp, 3), 'hp': round(_hp, 3), 'lp': round(_lp, 3), 'moment': round(_moment, 1), 'w': _w, 'p': _p}
        if _dc:
            _sorted = sorted(_dc.items(), key=lambda x: x[1]['wc'], reverse=True)
            _n = len(_sorted)
            for _ri, (_s, _d) in enumerate(_sorted):
                _wr = (_n - _ri) / _n
                _d['wr'] = round(_wr, 4)
                _d['qty'] = round(_d['moment'] * _wr, 1)
            for _s, _d in _dc.items():
                if _s not in rankings_dict: continue
                rankings_dict[_s]['dc_moment'] = _d['moment']
                rankings_dict[_s]['dc_qty'] = _d.get('qty', 0)
                rankings_dict[_s]['dc_width_rank'] = _d.get('wr', 0)
                rankings_dict[_s]['dc_width_composite'] = round(_d['wc'], 2)
                rankings_dict[_s]['dc_expansion'] = _d['exp']
                rankings_dict[_s]['dc_htf_pos'] = _d['hp']
                rankings_dict[_s]['dc_ltf_pos'] = _d['lp']
                for _tf in _tfs_all:
                    if _tf in _d['w']: rankings_dict[_s][f'dc_width_{_tf}'] = round(_d['w'][_tf], 2)
                    if _tf in _d['p']: rankings_dict[_s][f'dc_pos_{_tf}'] = round(_d['p'][_tf], 4)
            _best_l = [(_s, _d['moment'], _d.get('qty',0)) for _s, _d in _sorted if _d['moment'] > 10][:3]
            _best_s = [(_s, _d['moment'], _d.get('qty',0)) for _s, _d in reversed(_sorted) if _d['moment'] < -10][:3]
            logger.info(f"[DC_INDEX] {_n} syms | L: {_best_l} | S: {_best_s}")
        # === DC MOMENT + QTY: cross-TF analysis + cross-symbol ranking ===
        _dc = {}
        _tfs_all = ['3m', '15m', '1h', '4h', 'D']
        for _sym in rankings_dict:
            _ind = indicators_data.get(_sym, {})
            if not _ind: continue
            _w, _p = {}, {}
            _cp = float(_ind.get('current_price') or 0)
            if _cp <= 0: continue
            for _tf in _tfs_all:
                _dw = float(_ind.get(f'dc_width_{_tf}') or 0)
                _dp = float(_ind.get(f'dc_position_{_tf}') or 0)
                if _dw <= 0:
                    _dch = float(_ind.get(f'dc_high_{_tf}') or 0)
                    _dcl = float(_ind.get(f'dc_low_{_tf}') or 0)
                    if _dch > 0 and _dcl > 0 and _dch > _dcl:
                        _dw = ((_dch - _dcl) / _dcl) * 100
                        _dp = max(0.0, min(1.0, (_cp - _dcl) / (_dch - _dcl)))
                if _dw > 0: _w[_tf] = _dw
                if 0 <= _dp <= 1.0: _p[_tf] = _dp
            if len(_w) < 2: continue
            _wc = _w.get('D', 0) * 0.50 + _w.get('4h', 0) * 0.30 + _w.get('1h', 0) * 0.20
            _ltf_w = sum(_w.get(t, 0) for t in ['3m', '15m']) / max(1, sum(1 for t in ['3m', '15m'] if t in _w))
            _htf_w = sum(_w.get(t, 0) for t in ['D', '4h', '1h']) / max(1, sum(1 for t in ['D', '4h', '1h'] if t in _w))
            _exp = _ltf_w / _htf_w if _htf_w > 0 else 0.0
            _hp = _p.get('D', 0.5) * 0.5 + _p.get('4h', 0.5) * 0.3 + _p.get('1h', 0.5) * 0.2
            _lp = _p.get('3m', 0.5) * 0.5 + _p.get('15m', 0.5) * 0.5
            _trend = (_hp - 0.5) * 2.0
            if _trend > 0:
                _pbd = max(0.0, _hp - _lp) / max(_hp, 0.01)
            else:
                _pbd = max(0.0, _lp - _hp) / max(1.0 - _hp, 0.01)
            _pbd = min(1.0, _pbd)
            _eb = min(1.3, max(1.0, _exp * 0.65 + 0.35)) if _exp > 1.0 else max(0.7, _exp)
            _moment = max(-100.0, min(100.0, _trend * _pbd * _eb * 100.0))
            _dc[_sym] = {'wc': _wc, 'exp': round(_exp, 3), 'hp': round(_hp, 3), 'lp': round(_lp, 3), 'moment': round(_moment, 1), 'w': _w, 'p': _p}
        if _dc:
            _sorted = sorted(_dc.items(), key=lambda x: x[1]['wc'], reverse=True)
            _n = len(_sorted)
            for _ri, (_s, _d) in enumerate(_sorted):
                _wr = (_n - _ri) / _n
                _d['wr'] = round(_wr, 4)
                _d['qty'] = round(_d['moment'] * _wr, 1)
            for _s, _d in _dc.items():
                if _s not in rankings_dict: continue
                rankings_dict[_s]['dc_moment'] = _d['moment']
                rankings_dict[_s]['dc_qty'] = _d.get('qty', 0)
                rankings_dict[_s]['dc_width_rank'] = _d.get('wr', 0)
                rankings_dict[_s]['dc_width_composite'] = round(_d['wc'], 2)
                rankings_dict[_s]['dc_expansion'] = _d['exp']
                rankings_dict[_s]['dc_htf_pos'] = _d['hp']
                rankings_dict[_s]['dc_ltf_pos'] = _d['lp']
                for _tf in _tfs_all:
                    if _tf in _d['w']: rankings_dict[_s][f'dc_width_{_tf}'] = round(_d['w'][_tf], 2)
                    if _tf in _d['p']: rankings_dict[_s][f'dc_pos_{_tf}'] = round(_d['p'][_tf], 4)
            _best_l = [(_s, _d['moment'], _d.get('qty',0)) for _s, _d in _sorted if _d['moment'] > 10][:3]
            _best_s = [(_s, _d['moment'], _d.get('qty',0)) for _s, _d in reversed(_sorted) if _d['moment'] < -10][:3]
            logger.info(f"[DC_INDEX] {_n} syms | L: {_best_l} | S: {_best_s}")
        rankings_dict = recursively_convert_np(rankings_dict)
        async with aiofiles.open(RANKINGS_FILE, "w") as f:
            await f.write(json.dumps(rankings_dict, indent=2))
        logger.info(f"✅ Saved rankings.json: {len(rankings_dict)} symbols with multipliers")
    except Exception as e:
        logger.error(f"Error saving rankings.json: {e}", exc_info=True)
def get_ranking_multiplier(symbol: str, default: float = 1.0) -> float:
    """Get order multiplier for a symbol from rankings.json"""
    try:
        if RANKINGS_FILE.exists():
            with open(RANKINGS_FILE, "r") as f:
                rankings = json.load(f)
                if symbol in rankings:
                    return float(rankings[symbol].get("order_multiplier", default))
    except Exception as e:
        logger.debug(f"Error reading ranking multiplier for {symbol}: {e}")
    return default
def get_ranking_data(symbol: str) -> Optional[Dict[str, Any]]:
    """Get full ranking data for a symbol from rankings.json"""
    try:
        if RANKINGS_FILE.exists():
            with open(RANKINGS_FILE, "r") as f:
                rankings = json.load(f)
                return rankings.get(symbol)
    except Exception as e:
        logger.debug(f"Error reading ranking data for {symbol}: {e}")
    return None


def clean_value(value):
    """
    Cleans values before saving to JSON to ensure they are serializable.
    """
    if isinstance(value, np.float64) or isinstance(value, np.float32):
        return float(value)  # Convert NumPy float to standard Python float
    if isinstance(value, np.int64) or isinstance(value, np.int32):
        return int(value)  # Convert NumPy int to standard Python int
    if isinstance(value, pd.Timestamp):
        return value.strftime('%Y-%m-%dT%H:%M:%S.%fZ')  # Convert timestamp to string
    return value  # Leave other types unchanged

def clean_timestamps_recursively(data):
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, pd.Timestamp):
                data[key] = val.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            elif isinstance(val, (dict, list)):
                clean_timestamps_recursively(val)
    elif isinstance(data, list):
        for i in range(len(data)):
            if isinstance(data[i], pd.Timestamp):
                data[i] = data[i].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            elif isinstance(data[i], (dict, list)):
                clean_timestamps_recursively(data[i])

def load_signals():
    global signals_data # ensure we are modifying the global
    if not os.path.exists(SIGNALS_FILE):
        signals_data = {}
        return {}
    try:
        with open(SIGNALS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            logger.debug(f" signals.json is a list, not a dict. Renaming or skipping it...")
            os.rename(SIGNALS_FILE, str(SIGNALS_FILE) + ".old_list_bak")
            signals_data = {}
            return {}
        for symbol, tf_data in data.items():
            if not isinstance(tf_data, dict):
                continue
            for timeframe, events_dict in tf_data.items():
                if not isinstance(events_dict, dict):
                    continue
                for etype, records in events_dict.items():
                    if isinstance(records, list):
                        for rec in records:
                            if "timestamp" in rec and isinstance(rec["timestamp"], str):
                                try:
                                    rec["timestamp"] = pd.to_datetime(rec["timestamp"])
                                except Exception as e:
                                    logger.warning(f"Could not parse timestamp {rec['timestamp']} for {symbol} {timeframe} {etype}: {e}")
        signals_data = data # Assign to global
        return data
    except Exception as e:
        logger.debug(f" Error loading signals: {e}")
        signals_data = {}
        return {}
async def process_historical_stochastic_events(symbols_list: List[str]) -> None:
    """
    Process historical stochastic crossover/crossunder events and WT signals for plotting.
    This function scans historical data for each timeframe and STORES them in signals_data
    so the plot_loop can visualize them.
    """
    global signals_data
    
    logger.info(f"[process_historical_stochastic_events] Processing historical technical events for {len(symbols_list)} symbols")
    
    # Define timeframes and their respective lookback periods
    timeframe_configs = {
        "3m": {"days_back": 2},      
        "15m": {"days_back": 14},   
        "1h": {"days_back": 60},  
        "4h": {"days_back": 180}  
    }
    
    processed_count = 0
    
    for i, symbol in enumerate(symbols_list):
        # Yield control every few symbols to prevent blocking the event loop
        if i % 10 == 0:
            await asyncio.sleep(0.1)

        try:
            for timeframe, config in timeframe_configs.items():
                cutoff_date = pd.Timestamp.now(tz='UTC') - pd.DateOffset(days=config["days_back"])
                
                # Load data for this timeframe
                df_tf = await convert_cached_klines_to_analysis_df(symbol, timeframe)
                if df_tf.empty:
                    continue
                
                # 1. Calculate WT indicators if not present
                if 'wt1' not in df_tf.columns or 'wt2' not in df_tf.columns:
                    wt1, wt2 = calculate_wavetrend(df_tf)
                    if wt1 is not None and wt2 is not None:
                        df_tf['wt1'] = wt1
                        df_tf['wt2'] = wt2
                
                # 2. Add stochastic RSI zones
                df_tf_rsi = add_stochrsi_zones(df_tf.copy())
                
                # 3. Detect stochastic events
                stoch_evs = detect_stoch_crossovers(df_tf_rsi)
                
                # 4. Detect WT events
                wt_evs = detect_wt_signals(df_tf)
                
                # 5. STORE EVENTS IN SIGNALS_DATA (Crucial Step)
                # We use record_signal (which saves to memory/disk) NOT send_signal (which broadcasts)
                
                # Store Stochastic Events
                for ev in stoch_evs:
                    # Filter out very old events based on config to keep memory light
                    ts = pd.to_datetime(ev['timestamp'])
                    if ts < cutoff_date:
                        continue
                        
                    await record_signal(
                        signals_data,
                        symbol,
                        timeframe,
                        ev['event_type'],
                        ev['price'],
                        ts,
                        reason=f"Historical {ev['event_type']}",
                        action=ev['action']
                    )
                    processed_count += 1

                # Store WT Events
                for ev in wt_evs:
                    ts = pd.to_datetime(ev['timestamp'])
                    if ts < cutoff_date:
                        continue

                    await record_signal(
                        signals_data,
                        symbol,
                        timeframe,
                        ev['event_type'],
                        ev['price'],
                        ts,
                        reason=f"Historical {ev['event_type']}",
                        action=ev['action']
                    )
                    processed_count += 1
                
        except Exception as e:
            logger.debug(f"[process_historical_stochastic_events] Error processing {symbol}: {e}")
            continue
    
    logger.info(f"[process_historical_stochastic_events] Completed: stored {processed_count} historical events for plotting")
    
def clean_ranking_signals(signals_dict: dict) -> None:
    """
    Remove irrelevant ranking signals (winners_up, losers_down, etc.) from signals.json
    to keep only meaningful technical indicator signals.
    """
    if not signals_dict:
        return
    
    # Define patterns for ranking signals to remove
    ranking_patterns = [  'winners_up', 'winners_down', 'losers_up', 'losers_down',
        'short_term_winners', 'short_term_losers', 'long_term_winners', 'long_term_losers', 'rank' ]
    
    total_removed = 0
    for sym, tf_dict in signals_dict.items():
        if not isinstance(tf_dict, dict):
            continue
        for tf, etypes in tf_dict.items():
            if not isinstance(etypes, dict):
                continue
            
            # Find ranking signal types to remove
            signals_to_remove = []
            for etype in etypes.keys():
                if any(pattern in etype.lower() for pattern in ranking_patterns):
                    signals_to_remove.append(etype)
            
            # Remove the ranking signals
            for etype in signals_to_remove:
                removed_count = len(etypes[etype])
                del etypes[etype]
                total_removed += removed_count
                logger.debug(f"[clean_ranking_signals] Removed {removed_count} {etype} signals from {sym}-{tf}")
    
    if total_removed > 0:
        logger.info(f"[clean_ranking_signals] Cleaned up {total_removed} ranking signals from signals.json")
    else:
        logger.debug("[clean_ranking_signals] No ranking signals found to clean up")

def clip_old_signals(signals_dict: dict, months_to_keep: int = 6) -> None:
    """Clip old signals from the signals dictionary. Only runs once per week to avoid overhead."""
    """
    Remove signals older than specified months to keep signals.json manageable.
    """
    if not signals_dict:
        return
    
    cutoff_date = pd.Timestamp.now(tz='UTC') - pd.DateOffset(months=months_to_keep)
    logger.info(f"[clip_old_signals] Removing signals older than {cutoff_date.strftime('%Y-%m-%d')}")
    
    total_removed = 0
    for sym, tf_dict in signals_dict.items():
        if not isinstance(tf_dict, dict):
            continue
        for tf, etypes in tf_dict.items():
            if not isinstance(etypes, dict):
                continue
            for etype, records in etypes.items():
                if not isinstance(records, list):
                    continue
                
                original_count = len(records)
                filtered_records = []
                
                for record in records:
                    try:
                        # Parse timestamp - handle both string and pd.Timestamp formats
                        ts_str = record.get("timestamp")
                        if not ts_str:
                            continue
                            
                        if isinstance(ts_str, str):
                            ts = pd.to_datetime(ts_str)
                        else:
                            ts = ts_str
                            
                        if ts.tzinfo is None:
                            ts = ts.tz_localize('UTC')
                        else:
                            ts = ts.tz_convert('UTC')
                            
                        # Keep only recent signals
                        if ts >= cutoff_date:
                            filtered_records.append(record)
                            
                    except Exception as e:
                        logger.debug(f"[clip_old_signals] Error parsing timestamp {ts_str}: {e}")
                        continue
                
                removed_count = original_count - len(filtered_records)
                if removed_count > 0:
                    total_removed += removed_count
                    etypes[etype] = filtered_records
                    logger.debug(f"[clip_old_signals] {sym}-{tf}-{etype}: removed {removed_count} old signals")


async def save_signals():
    global signals_data
    path = SIGNALS_FILE

    # Use an async lock to prevent race conditions
    async with signals_file_lock:
        try:
            # --- The rest of your existing save logic goes inside the lock ---
            
            # 1. Create a serializable copy of the in-memory data
            serializable_signals = {}
            for sym, tf_dict_val in signals_data.items():
                serializable_signals[sym] = {}
                for tf, etypes_val in tf_dict_val.items():
                    serializable_signals[sym][tf] = {}
                    for etype, events_val in etypes_val.items():
                        serializable_signals[sym][tf][etype] = []
                        for ev in events_val:
                            ev_copy = ev.copy()
                            if isinstance(ev_copy.get("timestamp"), pd.Timestamp):
                                ev_copy["timestamp"] = ev_copy["timestamp"].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                            serializable_signals[sym][tf][etype].append(ev_copy)

            # 2. Load existing data from file (if any)
            existing_from_file = {}
            if path.exists():
                try:
                    # Use aiofiles for async read
                    async with aiofiles.open(path, "r", encoding="utf-8") as f:
                        content = await f.read()
                        if content:
                           existing_from_file = json.loads(content)
                except Exception as e:
                    logger.warning(f"Could not load existing signals from {path} for merging: {e}")
                    existing_from_file = {}

            # 3. Merge in-memory data into the data loaded from the file
            for sym, tf_dict_val in serializable_signals.items():
                if sym not in existing_from_file:
                    existing_from_file[sym] = {}
                for tf, etypes_val in tf_dict_val.items():
                    if tf not in existing_from_file[sym]:
                        existing_from_file[sym][tf] = {}
                    for etype, events_val in etypes_val.items():
                        if etype not in existing_from_file[sym][tf]:
                            existing_from_file[sym][tf][etype] = []
                        existing_from_file[sym][tf][etype].extend(events_val) 

            # 4. Clean up ranking signals (remove winners_up, losers_down, etc.)
            clean_ranking_signals(existing_from_file)
            
            # 5. Deduplicate the combined data
            deduplicate_signals(existing_from_file)
            
            # # 5. Clip old signals to keep only last 6 months (only run once per week)
            # global LAST_CLIP_OLD_SIGNALS_TIME
            # current_time = datetime.now(timezone.utc)
            
            # # Only run clip_old_signals if it hasn't been run in the last week
            # if (LAST_CLIP_OLD_SIGNALS_TIME is None or 
            #     (current_time - LAST_CLIP_OLD_SIGNALS_TIME).total_seconds() > 7 * 24 * 3600):  # 7 days
            #     logger.info("[save_signals] Running weekly clip_old_signals cleanup...")
            #     clip_old_signals(existing_from_file, months_to_keep=6)
            #     LAST_CLIP_OLD_SIGNALS_TIME = current_time
            # else:
            #     logger.debug("[save_signals] Skipping clip_old_signals (run within last week)")

            # 6. Trim data to size limits and write atomically
            # --- Size trimming helpers ---
            def _apply_per_list_limit(signals_dict: dict, per_list_limit: int) -> None:
                for _sym, tf_dict in signals_dict.items():
                    if not isinstance(tf_dict, dict):
                        continue
                    for _tf, etypes in tf_dict.items():
                        if not isinstance(etypes, dict):
                            continue
                        for _etype, records in etypes.items():
                            if isinstance(records, list) and len(records) > per_list_limit:
                                # Keep most recent N assuming appends are newest
                                etypes[_etype] = records[-per_list_limit:]

            def _estimate_size_bytes(obj: dict) -> int:
                try:
                    return len(orjson.dumps(obj, option=orjson.OPT_INDENT_2))  # type: ignore # pylint: disable=no-member,c-extension-no-member
                except Exception:
                    # Fallback to std json if orjson fails for any reason
                    return len(json.dumps(obj, indent=2).encode("utf-8"))

            # Dedup already done; now enforce per-list and total size limits
            MAX_MB = int(os.environ.get("MAX_SIGNALS_FILE_SIZE_MB", "80"))
            MAX_BYTES = MAX_MB * 1024 * 1024
            per_list_limit = int(os.environ.get("SIGNALS_PER_LIST_LIMIT", "80000"))
            min_list_limit = int(os.environ.get("SIGNALS_MIN_PER_LIST", "100"))

            # First pass limit
            _apply_per_list_limit(existing_from_file, per_list_limit)
            total_size = _estimate_size_bytes(existing_from_file)

            # Iteratively tighten per-list limit until under cap or hitting minimum
            tightened = False
            while total_size > MAX_BYTES and per_list_limit > min_list_limit:
                new_limit = max(min_list_limit, per_list_limit // 2)
                if new_limit == per_list_limit:
                    break
                per_list_limit = new_limit
                _apply_per_list_limit(existing_from_file, per_list_limit)
                total_size = _estimate_size_bytes(existing_from_file)
                tightened = True

            if total_size > MAX_BYTES:
                logger.warning(
                    f"signals.json still exceeds {MAX_MB}MB after per-list trimming (size ~{total_size/1024/1024:.1f}MB). Proceeding with write; consider lowering SIGNALS_PER_LIST_LIMIT."
                )
            else:
                if tightened:
                    logger.info(
                        f"Trimmed signals per-list to {per_list_limit} to fit under {MAX_MB}MB (size ~{total_size/1024/1024:.1f}MB)."
                    )

            # Write atomically with a temp file in the same directory
            os.makedirs(path.parent, exist_ok=True)
            tmp_file_path = None
            try:
                content_bytes = orjson.dumps(existing_from_file, option=orjson.OPT_INDENT_2)  # type: ignore # pylint: disable=no-member,c-extension-no-member
            except Exception:
                # Fallback to standard json with indentation
                content_bytes = json.dumps(existing_from_file, indent=2).encode("utf-8")

            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=str(path.parent), delete=False, prefix="signals_", suffix=".tmp"
                ) as tmp_f:
                    tmp_file_path = tmp_f.name
                    tmp_f.write(content_bytes)
                    tmp_f.flush()
                    os.fsync(tmp_f.fileno())
                os.replace(tmp_file_path, path)
            finally:
                # Clean up stray temp file on failure
                if tmp_file_path and os.path.exists(tmp_file_path):
                    try:
                        os.remove(tmp_file_path)
                    except Exception:
                        pass
            # logger.info(f"Saved signals (merged + deduped) to {path}")

        except Exception as e:
            logger.error(f"CRITICAL ERROR during save_signals: {e}", exc_info=True)

def deduplicate_signals(signals_data_local): # Renamed arg to avoid confusion with global
    for sym, tf_dict in signals_data_local.items():
        if not isinstance(tf_dict, dict):
            continue
        for tf, etypes in tf_dict.items():
            if not isinstance(etypes, dict):
                continue
            for etype, records in etypes.items():
                if not isinstance(records, list):
                    continue
                unique_set = set()
                new_list = []
                for rec in records:
                    # Ensure timestamp is consistently handled, preferably as string for uniqueness key
                    ts_key = rec.get("timestamp")
                    if isinstance(ts_key, pd.Timestamp): # Should be string if coming from save prep
                        ts_key = ts_key.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    
                    price = rec.get("price")
                    reason = rec.get("reason", "")
                    action = rec.get("action", "")
                    # Add other relevant fields from **kwargs to the key if they define uniqueness
                    # For example, if 'usd_size' or 'confidence' are part of what makes a signal unique.
                    # For now, assuming timestamp, price, reason, action are sufficient.
                    key_tuple_list = []
                    key_tuple_list.append(ts_key)
                    key_tuple_list.append(price)
                    key_tuple_list.append(reason)
                    key_tuple_list.append(action)
                    
                    # Add other identifying kwargs to the key
                    # This needs to be deterministic, so sort kwargs by key
                    other_kwargs = {k:v for k,v in rec.items() if k not in ["timestamp", "price", "reason", "action"]}
                    for k_kwarg in sorted(other_kwargs.keys()):
                        key_tuple_list.append(other_kwargs[k_kwarg])
                    
                    key = tuple(key_tuple_list)

                    if key not in unique_set:
                        unique_set.add(key)
                        new_list.append(rec)
                etypes[etype] = new_list

async def record_signal(signals_data_local, symbol, timeframe, etype, price, timestamp, reason="", action="", **kwargs):
    if price is None:
        logger.debug(f" Skipping signal for {symbol} {timeframe} {etype} due to missing price.")
        return
    if not isinstance(timestamp, pd.Timestamp): # Ensure timestamp is pd.Timestamp in memory
        try:
            timestamp = pd.to_datetime(timestamp)
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize('UTC')
        except Exception as e:
            logger.error(f"Failed to parse timestamp {timestamp} in record_signal: {e}")
            return # Cannot record without a valid timestamp

    if symbol not in signals_data_local:
        signals_data_local[symbol] = {}
    if timeframe not in signals_data_local[symbol]:
        signals_data_local[symbol][timeframe] = {}
    if etype not in signals_data_local[symbol][timeframe]:
        signals_data_local[symbol][timeframe][etype] = []
    
    event = {
        "timestamp": timestamp, # Store as pd.Timestamp in memory
        "price": price,
        "reason": reason,
        "action": action 
    }
    event.update(kwargs)
    signals_data_local[symbol][timeframe][etype].append(event)
    logger.debug(f" Recorded signal in memory for {symbol} {timeframe} {etype}: price={price} @ {timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}")
    # Don't save on every individual signal - let signals_loop handle batch saves
    # This prevents timeout issues and improves performance
    logger.debug(f"[record_signal] Signal recorded in memory for {symbol} {timeframe} {etype} - will be saved in batch")

async def send_signal(sig_event, signals_data_ref):
    """Send signal through unified Redis manager"""
    global redis_manager
    symbol = sig_event["symbol"]; event_type = sig_event.get("event_type", "unknown"); action = sig_event.get("action", "BUY" if "buy" in sig_event.get("event_type", "").lower() or "up" in sig_event.get("event_type", "").lower() else "SELL"); ranking_points_val = sig_event.get("ranking_points", sig_event.get("final_score_norm", 0))
    logger.info(f"🔵 [ez_rankings] SEND_SIGNAL: {symbol} | {event_type} | action={action} | score={ranking_points_val:.1f}")
    timeframe = sig_event.get("timeframe", "3m")
    reason = sig_event.get("reason", sig_event.get("event_type", "unknown_event"))
    price = sig_event.get("price")
    timestamp_signal = pd.to_datetime(sig_event.get("timestamp_signal", sig_event.get("timestamp")))
    
    # Record signal in local storage
    other_details = {k:v for k,v in sig_event.items() if k not in ["symbol", "timeframe", "event_type", "price", "timestamp", "reason", "action", "timestamp_signal"]}
    await record_signal(signals_data_ref, symbol, timeframe, sig_event["event_type"], price, timestamp_signal, reason=reason, action=action, **other_details)
    
    # Broadcast via Redis if manager is available
    ranking_points_val = sig_event.get("ranking_points", sig_event.get("final_score_norm", 0))
    event_type = sig_event.get("event_type", "")
    
    # Determine signal priority - these are the most important signals
    # ALL arrow-related signals should be broadcast
    is_arrow_signal = (
        "all_green" in event_type or 
        "all_red" in event_type or
        "winners_all_green" in event_type or
        "losers_all_red" in event_type or
        event_type.startswith("winners_") or
        event_type.startswith("losers_")
    )
    is_priority_signal = (
        is_arrow_signal or
        "winners_" in event_type or
        "losers_" in event_type or
        "fast mover" in reason.lower() or 
        abs(ranking_points_val) > 70 or
        "breakthrough" in event_type.lower()
    )
    
    # Special handling for arrow signals - always log and broadcast
    if is_arrow_signal:
        logger.info(f"🎯 [ARROW SIGNAL] {symbol} {event_type} - This is a high priority arrow alignment signal!")
    is_all_green_red = "all_green" in event_type or "all_red" in event_type or "winners_all_green" in event_type or "losers_all_red" in event_type
    if is_all_green_red:
        is_priority_signal = True
        logger.warning(f"🚀 [PRIORITY ALL_GREEN/RED] {symbol} {event_type} - Immediate broadcast!")
    success_count=0
    if redis_manager and is_priority_signal:
        try:
            broadcast_signal = {
                "symbol": symbol,
                "timeframe": timeframe,
                "event_type": event_type,
                "action": action,
                "price": price,
                "reason": reason,
                "ranking_points": ranking_points_val,
                "timestamp": timestamp_signal.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "timestamp_signal": timestamp_signal.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "source": "ez_rankings",
                "priority": "HIGH" if (is_arrow_signal or is_all_green_red or abs(ranking_points_val) > 70) else "MEDIUM"
            }
            
            broadcast_signal = {k: v for k, v in broadcast_signal.items() if v is not None}
            
            if redis_manager:
                success_count = await redis_manager.publish(
                    channel=REDIS_CHANNELS["signals_data"],
                    message=broadcast_signal, expiry_seconds=60,
                    data_type="trade_signal"
                )
            
            if success_count > 0:
                prefix = "🎯 [ARROW]" if is_arrow_signal else "📡"
                logger.info(f"{prefix} Broadcasted signal => {symbol} {event_type} (score: {ranking_points_val}) to {success_count} Redis instances")
            else:
                logger.warning(f"⚠️ Signal not broadcasted - no Redis connections available")
                
        except Exception as e:
            logger.error(f"❌ Failed to publish signal to Redis: {e}")
    else:
        logger.debug(f"📝 Signal recorded locally for {symbol} {event_type} (score: {ranking_points_val}) - not meeting broadcast criteria")
        

def _get_timeframe_seconds(timeframe: str, current_time: Optional[datetime] = None) -> int:
    """Get cooldown seconds until next bar close based on timeframe. 
    Calculates time remaining until the next bar closes (e.g., if 15m signal fires at :23, 
    cooldown is 7min until :30, not full 15min)."""
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    if timeframe == "3m":
        seconds_into_bar = (current_time.minute % 3) * 60 + current_time.second + (current_time.microsecond / 1000000.0)
        seconds_until_close = 180 - seconds_into_bar
    elif timeframe == "15m":
        seconds_into_bar = (current_time.minute % 15) * 60 + current_time.second + (current_time.microsecond / 1000000.0)
        seconds_until_close = 900 - seconds_into_bar
    elif timeframe == "1h":
        seconds_into_bar = current_time.minute * 60 + current_time.second + (current_time.microsecond / 1000000.0)
        seconds_until_close = 3600 - seconds_into_bar
    elif timeframe == "4h":
        hours_into_bar = current_time.hour % 4
        seconds_into_bar = hours_into_bar * 3600 + current_time.minute * 60 + current_time.second + (current_time.microsecond / 1000000.0)
        seconds_until_close = 14400 - seconds_into_bar
    elif timeframe == "D":
        seconds_into_day = current_time.hour * 3600 + current_time.minute * 60 + current_time.second + (current_time.microsecond / 1000000.0)
        seconds_until_close = 86400 - seconds_into_day
    else:
        # Default to 3m
        seconds_into_bar = (current_time.minute % 3) * 60 + current_time.second + (current_time.microsecond / 1000000.0)
        seconds_until_close = 180 - seconds_into_bar
    # Ensure minimum cooldown (at least 1 minute for safety)
    min_cooldown = 60
    return max(int(seconds_until_close), min_cooldown)

def should_send_signal(event_details, signals_data_ref, symbol, timeframe, current_atr=0.0, min_time_diff=None, atr_multiplier=2): # Renamed args
    etype = event_details.get("event_type")
    if not etype:
        logger.warning("should_send_signal: event_type missing in event_details.")
        return False # Cannot determine without event_type
    is_all_green_red = "all_green" in etype or "all_red" in etype or "winners_all_green" in etype or "losers_all_red" in etype
    if is_all_green_red:
        return True
    if min_time_diff is None:
        # Use signal timestamp to calculate cooldown until next bar close
        signal_ts_str = event_details.get("timestamp_signal", event_details.get("timestamp"))
        signal_time = None
        if signal_ts_str:
            try:
                signal_time = pd.to_datetime(signal_ts_str)
                if signal_time.tzinfo is None:
                    signal_time = signal_time.tz_localize('UTC')
                signal_time = signal_time.to_pydatetime()
            except Exception:
                pass
        min_time_diff = _get_timeframe_seconds(timeframe, signal_time)
    # Ensure path exists in signals_data_ref
    if symbol not in signals_data_ref:
        signals_data_ref[symbol] = {}
    if timeframe not in signals_data_ref[symbol]:
        signals_data_ref[symbol][timeframe] = {}
    if etype not in signals_data_ref[symbol][timeframe]:
        signals_data_ref[symbol][timeframe][etype] = []

    prior_signals = signals_data_ref[symbol][timeframe][etype]
    if not prior_signals:
        return True # No prior signals of this type, so send

    last_signal = prior_signals[-1] # Most recent recorded signal of this type

    new_ts_str = event_details.get("timestamp_signal", event_details.get("timestamp"))
    old_ts_obj = last_signal.get("timestamp") # This should be pd.Timestamp from record_signal

    if not new_ts_str or not isinstance(old_ts_obj, pd.Timestamp):
        logger.warning("should_send_signal: Missing or invalid timestamps for comparison.")
        return True # Cannot compare, allow sending

    try:
        new_dt = pd.to_datetime(new_ts_str)
        if new_dt.tzinfo is None:
            new_dt = new_dt.tz_localize('UTC')
    except Exception as e:
        logger.error(f"should_send_signal: Failed to parse new_dt from '{new_ts_str}': {e}")
        return True # Error parsing, allow sending

    if pd.isnull(new_dt) or pd.isnull(old_ts_obj):
        logger.warning("should_send_signal: Parsed timestamps resulted in NaT.")
        return True

    dtsec = (new_dt - old_ts_obj).total_seconds()
    if dtsec < min_time_diff:
        logger.debug(f"Skipping signal for {symbol} {etype} (too soon), only {dtsec:.1f}s since last (cooldown: {min_time_diff}s).")
        return False

    old_price = last_signal.get("price")
    new_price = event_details.get("price")

    if old_price is None or new_price is None: # Price comparison not possible
        return True 

    if current_atr > 0 and abs(new_price - old_price) < atr_multiplier * current_atr:
        logger.debug(f"Skipping signal for {symbol} {etype} (price too close) => old={old_price}, new={new_price}, ATR={current_atr}")
        return False
        
    return True

def prune_old_signals(signals_data_local, retention_days=55): # Renamed arg
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for symbol in list(signals_data_local.keys()):
        for timeframe in list(signals_data_local[symbol].keys()):
            for event_type in list(signals_data_local[symbol][timeframe].keys()):
                signals_list = signals_data_local[symbol][timeframe][event_type]
                
                # Ensure timestamps in signals_list are pd.Timestamp before comparison
                valid_signals_after_pruning = []
                for signal in signals_list:
                    ts = signal.get("timestamp")
                    if isinstance(ts, str): # Convert if string
                        try:
                            ts = pd.to_datetime(ts)
                            if ts.tzinfo is None: ts = ts.tz_localize('UTC')
                        except Exception: # If conversion fails, keep original or discard based on policy
                            logger.warning(f"Could not parse timestamp {ts} during pruning for {symbol}")
                            continue # Or keep if policy allows
                    
                    if isinstance(ts, pd.Timestamp) and ts >= cutoff_time:
                         valid_signals_after_pruning.append(signal)
                
                signals_data_local[symbol][timeframe][event_type] = valid_signals_after_pruning
                
                if not signals_data_local[symbol][timeframe][event_type]:
                    del signals_data_local[symbol][timeframe][event_type]
            if not signals_data_local[symbol][timeframe]:
                del signals_data_local[symbol][timeframe]
        if not signals_data_local[symbol]:
            del signals_data_local[symbol]

def detect_trading_signals():
    global RANKING_INFO, rp, rpg, rpn
    trading_signals = []
    
    # Attempt to get files
    latest_files = get_latest_files(DATA_DIR, num_required=2)
    
    # FIX: Check if latest_files is None OR has fewer than 2 items
    if not latest_files or len(latest_files) < 2:
        # Don't log error every time to avoid spamming logs, just debug
        # logger.debug("Not enough working market data files for detect_trading_signals.")
        return trading_signals 
        
    latest_path = latest_files[0]
    prev_path = latest_files[1]
    
    try:
        data_latest = load_market_json(latest_path)
        data_prev = load_market_json(prev_path)
    except Exception as e:
        logger.error(f"Error loading market json files in detect_trading_signals: {e}")
        return trading_signals

    try:
        fallback_timestamp = datetime.fromtimestamp(os.path.getmtime(latest_path), tz=timezone.utc)
    except Exception as e:
        # logger.warning(f"Could not get file timestamp fallback: {e}")
        fallback_timestamp = datetime.now(timezone.utc)

    for symbol, curr in data_latest.items():
        if not isinstance(curr, dict):
            continue          
        ts_str = curr.get("timestamp", None)
        last_timestamp = fallback_timestamp # Default
        if ts_str:
            try:
                parsed_ts = pd.to_datetime(ts_str)
                if parsed_ts.tzinfo is None:
                    last_timestamp = parsed_ts.tz_localize('UTC')
                else:
                    last_timestamp = parsed_ts.tz_convert('UTC')
            except Exception as e:
                logger.warning(f"{symbol} => Could not parse timestamp '{ts_str}': {e}. Using file timestamp.")
        else:
            logger.warning(f"{symbol} => Missing 'timestamp'. Using file timestamp.")

        if symbol not in data_prev or not isinstance(data_prev.get(symbol), dict):
            continue
        prev = data_prev[symbol]
        
        # Ensure all required keys exist in curr and prev to avoid KeyErrors
        required_keys_stoch = ["stoch_k_3m", "stoch_d_3m", "stoch_k_15m", "stoch_k_1h", "stoch_d_1h"]
        required_keys_trend = ["lr_trend_3m", "lr_trend_15m"]
        required_keys_dc = ["dc_low_3m_ant", "dc_high_3m_ant", "current_price"]
        required_keys_ranking = ["0ranking_points"]
        required_keys_wt = [f"wt_strong_alert_{tf}" for tf in ["3m", "15m", "1h", "4h"]]


        all_curr_keys = required_keys_stoch + required_keys_trend + required_keys_dc + required_keys_ranking + required_keys_wt
        all_prev_keys = ["stoch_k_3m", "stoch_d_3m"] # Example for prev, adjust as needed

        if not all(k in curr for k in all_curr_keys) or \
           not all(k in prev for k in all_prev_keys):
            continue
            
        if symbol not in RANKING_INFO: RANKING_INFO[symbol] = {}
        RANKING_INFO[symbol]["ranking_points"] = curr.get("0ranking_points", 0)
        RANKING_INFO[symbol]["ranking_points_global"] = curr.get("0ranking_points_global", 0)
        RANKING_INFO[symbol]["ranking_points_normal"] = curr.get("0ranking_points_normal", 0)
        RANKING_INFO[symbol]["stoch_k_3m"] = curr.get("stoch_k_3m", None)
        RANKING_INFO[symbol]["stoch_d_3m"] = curr.get("stoch_d_3m", None)
        RANKING_INFO[symbol]["stoch_k_1h"] = curr.get("stoch_k_1h", None)
        RANKING_INFO[symbol]["stoch_d_1h"] = curr.get("stoch_d_1h", None)
        RANKING_INFO[symbol]["lr_trend_3m"] = curr.get("lr_trend_3m", None)
        RANKING_INFO[symbol]["lr_trend_15m"] = curr.get("lr_trend_15m", None)
        RANKING_INFO[symbol]["dc_low_3m_ant"] = curr.get("dc_low_3m_ant", None)
        RANKING_INFO[symbol]["dc_high_3m_ant"] = curr.get("dc_high_3m_ant", None)
        RANKING_INFO[symbol]["wt_alert_3m"] = curr.get("wt_alert_3m", None)
        RANKING_INFO[symbol]["wt_signal_3m"] = curr.get("wt_signal_3m", None)
        RANKING_INFO[symbol]["wt_alert_15m"] = curr.get("wt_alert_15m", None)
        RANKING_INFO[symbol]["wt_signal_15m"] = curr.get("wt_signal_15m", None)
        RANKING_INFO[symbol]["wt_alert_1h"] = curr.get("wt_alert_1h", None)
        RANKING_INFO[symbol]["wt_signal_1h"] = curr.get("wt_signal_1h", None)
        RANKING_INFO[symbol]["wt_alert_4h"] = curr.get("wt_alert_4h", None)
        RANKING_INFO[symbol]["wt_signal_4h"] = curr.get("wt_signal_4h", None)
        RANKING_INFO[symbol]["wt1_3m"] = curr.get("wt1_3m", None)
        RANKING_INFO[symbol]["wt2_3m"] = curr.get("wt2_3m", None)
        RANKING_INFO[symbol]["wt1_15m"] = curr.get("wt1_15m", None)
        RANKING_INFO[symbol]["wt2_15m"] = curr.get("wt2_15m", None)
        RANKING_INFO[symbol]["wt1_1h"] = curr.get("wt1_1h", None)
        RANKING_INFO[symbol]["wt2_1h"] = curr.get("wt2_1h", None)
        RANKING_INFO[symbol]["wt1_4h"] = curr.get("wt1_4h", None)
        RANKING_INFO[symbol]["wt2_4h"] = curr.get("wt2_4h", None)
        RANKING_INFO[symbol]["wt1_D"] = curr.get("wt1_D", None)
        RANKING_INFO[symbol]["wt2_D"] = curr.get("wt2_D", None)
        RANKING_INFO[symbol]["current_price"] = curr.get("current_price", None)

        current_price_val = curr.get("current_price") # Use this for signals
        rp[symbol]=curr.get("0ranking_points", 0)
        rpg[symbol]=curr.get("0ranking_points_global", 0)
        rpn[symbol]=curr.get("0ranking_points_normal", 0)
        # logger.info(f"{symbol} rp {rp.get(symbol)} rpg {rpg.get(symbol)} rpn {rpn.get(symbol)}. 509")

        # Signal detection logic (ensure values are not None before comparison)
        # Example for the first signal:
        if all(curr.get(k) is not None for k in ["stoch_k_3m", "stoch_d_3m", "stoch_k_15m", "stoch_k_1h", "stoch_d_1h", "lr_trend_3m", "lr_trend_15m", "current_price", "dc_low_3m_ant", "0ranking_points"]) and \
           all(prev.get(k) is not None for k in ["stoch_k_3m", "stoch_d_3m"]):
            if (
                prev["stoch_k_3m"] < prev["stoch_d_3m"] and curr["stoch_k_3m"] >= curr["stoch_d_3m"] and
                curr["stoch_k_15m"] < 30 and curr["stoch_k_1h"] > curr["stoch_d_1h"] and
                curr["lr_trend_3m"] == "uptrend" and curr["lr_trend_15m"] == "uptrend" and
                curr["current_price"] <= curr["dc_low_3m_ant"] and
                curr["0ranking_points"] > 70 # Use the direct key from data_latest
            ):
                trading_signals.append({
                    'symbol': symbol, 'action': "BUY", 'price': current_price_val,
                    'event_type': "Perfect Stochastic + Trend + Support Entry", 'timestamp': last_timestamp })

        # Apply similar checks for other signal conditions...
        if all(curr.get(k) is not None for k in ["stoch_k_3m", "stoch_d_3m", "stoch_k_15m", "lr_trend_3m", "lr_trend_15m", "current_price", "dc_high_3m_ant", "0ranking_points"]) and \
           all(prev.get(k) is not None for k in ["stoch_k_3m", "stoch_d_3m"]):
            if (
                prev["stoch_k_3m"] > prev["stoch_d_3m"] and curr["stoch_k_3m"] <= curr["stoch_d_3m"] and
                curr["stoch_k_15m"] > 70 and
                curr["lr_trend_3m"] == "downtrend" and curr["lr_trend_15m"] == "downtrend" and
                curr["current_price"] >= curr["dc_high_3m_ant"] and
                curr["0ranking_points"] < -70
            ):
                trading_signals.append({
                    'symbol': symbol, 'action': "SELL", 'price': current_price_val,
                    'event_type': "Perfect Stochastic + Trend + Resistance Exit", 'timestamp': last_timestamp })

        current_rp = rp.get(symbol) # Use the value from the updated global dict
        if current_rp is not None:
            if current_rp > 50: # Use current_rp
                for tf_wt in ["3m", "15m", "1h", "4h"]:
                    if curr.get(f"wt_strong_alert_{tf_wt}"): # wt_strong_alert might be boolean true
                        trading_signals.append({
                            'symbol': symbol, 'action': "BUY", 'price': current_price_val,
                            'event_type': f"WT Strong Alert on {tf_wt} (ranking_points {current_rp})", 'timestamp': last_timestamp })
                        break 
            elif current_rp < -50: # Use current_rp
                for tf_wt in ["3m", "15m", "1h", "4h"]:
                    if curr.get(f"wt_strong_alert_{tf_wt}"):
                        trading_signals.append({
                            'symbol': symbol, 'action': "SELL", 'price': current_price_val,
                            'event_type': f"WT Strong Alert on {tf_wt} (ranking_points {current_rp})", 'timestamp': last_timestamp })
                        break
    return trading_signals

def get_latest_files(directory, num_required=2, max_attempts=1000):
    def try_repair_json(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            last_brace = raw.rfind('}')
            last_bracket = raw.rfind(']')
            end_pos = max(last_brace, last_bracket)
            
            if end_pos == -1: 
                return None
            chunk = raw[:end_pos+1]
            try:
                json.loads(chunk) 
                with open(filepath, "w", encoding="utf-8") as f_out:
                    f_out.write(chunk)
                logger.info(f"[get_latest_files] Repaired truncated file: {filepath}")
                return filepath
            except Exception:
                return None
        except Exception:
            return None

    # --- Helper: Load and Validate a single file ---
    def load_and_validate(path_to_load):
        try:
            with open(path_to_load, "rb") as f_load:
                return orjson.loads(f_load.read())  # type: ignore # pylint: disable=no-member,c-extension-no-member
        except orjson.JSONDecodeError:  # type: ignore # pylint: disable=no-member,c-extension-no-member
            logger.warning(f"[get_latest_files] Broken JSON in {path_to_load}, attempting repair...")
            repaired_path = try_repair_json(path_to_load)
            if repaired_path:
                try:
                    with open(repaired_path, "rb") as f_repaired:
                        return orjson.loads(f_repaired.read())  # type: ignore # pylint: disable=no-member,c-extension-no-member
                except Exception:
                    pass # Repair failed effectively
            
            # If we reach here, file is bad and unrepairable
            try:
                os.remove(path_to_load)
                logger.warning(f"[get_latest_files] Deleted unrecoverable file: {path_to_load}")
            except OSError:
                pass
            return None
        except Exception as e:
            # Generic error (permissions, file disappeared, etc.)
            logger.debug(f"[get_latest_files] Error reading {path_to_load}: {e}")
            return None

    # --- Main Logic ---
    try:
        # Ensure directory is a string for os.path operations
        dir_str = str(directory)
        if not os.path.exists(dir_str):
            return []

        # 1. List all candidate files
        candidate_files = [
            os.path.join(dir_str, f) 
            for f in os.listdir(dir_str) 
            if f.startswith("market_data_") and f.endswith(".json")
        ]
        
        # 2. Sort by modification time (Newest First)
        # We check exists() inside the key lambda to prevent race conditions if a file is deleted mid-sort
        files_sorted = sorted(
            [f for f in candidate_files if os.path.exists(f)], 
            key=lambda f_path: os.path.getmtime(f_path), 
            reverse=True
        )

    except Exception as e:
        logger.error(f"[get_latest_files] Error listing/sorting files in {directory}: {e}")
        return []

    # 3. Select Valid Files
    selected_files = []
    attempts = 0
    
    for path_loop in files_sorted:
        if len(selected_files) >= num_required:
            break
        if attempts >= max_attempts:
            break
        
        # Validate that this file actually contains usable JSON
        data = load_and_validate(path_loop)
        if data:
            selected_files.append(path_loop)
        
        attempts += 1
        
    if len(selected_files) < num_required:
        logger.debug(f"[get_latest_files] Only found {len(selected_files)}/{num_required} usable files.")
        
    return selected_files

#####################################################
# KLINE LOADERS
#####################################################
def _load_klines_from_file(symbol: str, interval: str) -> pd.DataFrame:
    all_dfs = []
    all_dirs = []
    
    # Resolve directories
    for klines_dir in _resolve_klines_directories():
        all_dirs.append(klines_dir)
        consolidated_dir = klines_dir / "consolidated_klines"
        if consolidated_dir.exists() and consolidated_dir.is_dir():
            all_dirs.append(consolidated_dir)
            
    for klines_dir in all_dirs:
        fpath = klines_dir / f"{symbol}_{interval}.json"
        if not fpath.exists():
            continue
            
        try:
            with open(fpath, "rb") as f:
                file_data = f.read()
            
            if not file_data:
                continue
                
            data = orjson.loads(file_data)  # type: ignore # pylint: disable=no-member,c-extension-no-member
            df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
            
            # Basic validation
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors='coerce', utc=True)
                df.dropna(subset=['timestamp'], inplace=True)
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                all_dfs.append(df)
                
        except (orjson.JSONDecodeError, ValueError) as e:  # type: ignore # pylint: disable=no-member,c-extension-no-member
            # FIX: Catch corruption errors specifically
            logger.error(f"❌ Corrupted kline file detected: {fpath} ({e}) - DELETING")
            try:
                os.remove(fpath)
            except OSError:
                pass
            continue
        except Exception as e:
            logger.error(f"Error loading {symbol}/{interval} from {fpath}: {e}")
            continue

    if not all_dfs:
        return pd.DataFrame()
        
    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.sort_values("timestamp").drop_duplicates(subset="timestamp", keep="last").reset_index(drop=True)
    
    if len(combined) > 800 and not hasattr(_load_klines_from_file, '_historical_mode'):
        combined = combined.iloc[-800:]
        
    return combined


async def get_klines_redis_first(symbol: str, interval: str) -> pd.DataFrame:
    global redis_manager
    if not redis_manager:
        return await asyncio.to_thread(_load_klines_from_file, symbol, interval)
    try:
        result = await get_klines_for_ranking(symbol, interval)
        if not result.empty:
            return result
    except Exception as e:
        logger.debug(f"[get_klines_redis_first] Redis failed for {symbol}-{interval}: {e}")
    return await asyncio.to_thread(_load_klines_from_file, symbol, interval)

async def get_klines_for_ranking(symbol: str, interval: str) -> pd.DataFrame:
    global redis_manager
    if not redis_manager:
        return await asyncio.to_thread(_load_klines_from_file, symbol, interval)
    local_client = redis_manager.connections.get("local")
    gateway_client = redis_manager.connections.get("gateway")
    available_sources = []
    if local_client: available_sources.append("local")
    if gateway_client: available_sources.append("gateway")
    logger.debug(f"[get_klines_for_ranking] Fetching {symbol}-{interval} from sources: {available_sources}")
    return await get_comprehensive_klines(local_client, gateway_client, symbol, interval, str(config.KLINES_CACHE_DIR))
    
async def load_cached_klines(symbol: str, interval: str) -> pd.DataFrame:
    return await get_klines_redis_first(symbol, interval)
async def convert_cached_klines_to_analysis_df(symbol: str, interval: str) -> pd.DataFrame:
    cols = ["timestamp", "close_time", "open", "high", "low", "close", "volume"]
    df = await asyncio.to_thread(_load_klines_from_file, symbol, interval)
    if df is None or df.empty:
        try:
            df = await load_cached_klines(symbol, interval)
        except Exception as exc:
            logger.debug(f"[convert_cached_klines_to_analysis_df] Redis load failed for {symbol}-{interval}: {exc}")
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
    df = df.copy()
    if "timestamp" not in df.columns:
        return pd.DataFrame(columns=cols)
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    elif getattr(df["timestamp"], "dt", None) is not None and df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    df.dropna(subset=["timestamp"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df = df.loc[~df["timestamp"].duplicated(keep="last")]
    interval_map = {"3m": 3, "15m": 15, "1h": 60, "4h": 240, "D": 1440}
    delta_minutes = interval_map.get(interval)
    if delta_minutes is not None:
        df["close_time"] = df["timestamp"] + pd.Timedelta(minutes=delta_minutes)
    else:
        df["close_time"] = pd.to_datetime(df.get("close_time", df["timestamp"]), errors="coerce", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["close_time"], inplace=True)
    return df[cols].copy()

#####################################################
# PRICES
#####################################################
def parse_timestamp(ts_str: Union[str, int, float, datetime, pd.Timestamp]) -> datetime:
    """Parses a timestamp string, numeric Unix timestamp, or datetime/pd.Timestamp into a timezone-aware datetime (UTC)."""
    if isinstance(ts_str, pd.Timestamp):
        if ts_str.tzinfo is None:
            return ts_str.tz_localize('UTC').to_pydatetime()
        return ts_str.tz_convert('UTC').to_pydatetime()
    if isinstance(ts_str, datetime):
        if ts_str.tzinfo is None:
            return ts_str.replace(tzinfo=timezone.utc)
        return ts_str.astimezone(timezone.utc)
    try:
        if isinstance(ts_str, (int, float)):
            return datetime.fromtimestamp(ts_str, tz=timezone.utc)
        dt_obj = pd.to_datetime(ts_str) # pd.to_datetime is quite versatile
        if dt_obj.tzinfo is None:
            return dt_obj.tz_localize('UTC').to_pydatetime()
        return dt_obj.tz_convert('UTC').to_pydatetime()
    except Exception as e:
        return datetime.min.replace(tzinfo=timezone.utc)

async def save_cache(file_path, cache_data):
    """Save cache data to file"""
    try:
        serializable_data = recursively_convert_np(cache_data)
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(orjson.dumps(serializable_data, option=orjson.OPT_INDENT_2))  # type: ignore # pylint: disable=no-member,c-extension-no-member
        logger.debug(f"Cache saved to {file_path}")
    except Exception as e:
        logger.error(f"Error saving cache to {file_path}: {e}")

async def load_cache(file_path):
    if os.path.exists(file_path):
        try:
            async with aiofiles.open(file_path, "rb") as f: # Read as bytes for orjson
                return orjson.loads(await f.read())  # type: ignore # pylint: disable=no-member,c-extension-no-member
        except Exception as e:
            logger.error(f"Error loading cache file {file_path}: {e}")
    return {}

async def initialize_caches():
    global price_cache
    # Only load from main price cache file
    price_cache = await load_cache(PRICE_CACHE_FILE_2)
    logger.info(f"Loaded price cache: {len(price_cache)} symbols from {PRICE_CACHE_FILE_2.name}")

def check_cache(cache_data: dict) -> Optional[tuple]:
    if cache_data and "price" in cache_data and "timestamp" in cache_data:
        try:
            cached_price = float(cache_data["price"])
            ts = parse_timestamp(cache_data["timestamp"]) # parse_timestamp handles various formats
            if cached_price > 0 and ts > datetime.min.replace(tzinfo=timezone.utc): # Ensure valid price and parsed timestamp
                return cached_price, ts
        except ValueError: # Handles if price is not a valid float
            logger.debug(f"Invalid price format in cache_data: {cache_data['price']}")
        except Exception as e_parse: # Catch other parsing errors from parse_timestamp if any
            logger.debug(f"Error parsing timestamp in cache_data: {e_parse}")
    return None


async def get_fresh_close_price(symbol: str, df_3m: pd.DataFrame) -> Optional[float]:
    """
    Simplified version that uses get_current_price from utils
    """
    try:
        # Use the existing get_current_price function from utils
        price, timestamp = await get_current_price(symbol)
        
        if price is not None:
            return price
        
        # Fallback to DataFrame if Redis price is not available
        if not df_3m.empty:
            return float(df_3m['close'].iloc[-1])
            
        logger.warning(f"No fresh price available for {symbol}, using fallback")
        return None
        
    except Exception as e:
        logger.error(f"Error getting fresh close price for {symbol}: {e}")
        # Fallback to DataFrame
        if not df_3m.empty:
            return float(df_3m['close'].iloc[-1])
        return None

# async def _updat
#####################################################
# Stoch RSI + Zones
#####################################################
def add_stochrsi_zones(df: pd.DataFrame) -> pd.DataFrame:
    df_copy = df.copy() # Work on a copy
    if df_copy.empty or 'close' not in df_copy.columns or df_copy['close'].isnull().all():
        df_copy["stoch_rsi"] = np.nan
        df_copy["zone"] = "neutral"
        return df_copy
    
    try:
        # Ensure close is numeric and has enough non-NaN values for StochRSI calculation
        close_numeric = pd.to_numeric(df_copy['close'], errors='coerce')
        if close_numeric.isnull().all() or len(close_numeric.dropna()) < 14: # 14 is default StochRSI window
            df_copy["stoch_rsi"] = np.nan
        else:
            stoch_k = StochRSIIndicator(close=close_numeric, window=14, smooth1=3, smooth2=3, fillna=True).stochrsi() * 100.0
            df_copy["stoch_rsi"] = stoch_k.clip(0, 100)

        def label_zone(x):
            if pd.isna(x): return "neutral"
            if x < 20: return "green"
            if x > 80: return "red"
            return "neutral"
        df_copy["zone"] = df_copy["stoch_rsi"].apply(label_zone)
    except Exception as e:
        logger.error(f"[add_stochrsi_zones] => Error calculating StochRSI for DataFrame (length {len(df_copy)}): {e}")
        df_copy["stoch_rsi"]= np.nan
        df_copy["zone"]="neutral"
    return df_copy

#####################################################
# WT
#####################################################
def calculate_wavetrend(df, n1=10, n2=21, wt_smoothing=4):
    # Ensure df has HLC and they are numeric
    if not all(col in df.columns for col in ['high', 'low', 'close']):
        # logger.warning("calculate_wavetrend: DataFrame missing HLC columns.")
        return pd.Series(dtype=float), pd.Series(dtype=float) # Return empty Series
    
    df_wt = df.copy()
    for col in ['high', 'low', 'close']:
        df_wt[col] = pd.to_numeric(df_wt[col], errors='coerce')
    df_wt.dropna(subset=['high', 'low', 'close'], inplace=True)
    if df_wt.empty:
        # logger.warning("calculate_wavetrend: DataFrame empty after HLC NA drop.")
        return pd.Series(dtype=float), pd.Series(dtype=float)

    hlc3 = (df_wt['high'] + df_wt['low'] + df_wt['close']) / 3
    
    # Check if enough data for EWMA
    if len(hlc3) < n1 or len(hlc3) < n2 : # Or a more suitable minimum length
        # logger.warning(f"calculate_wavetrend: Not enough data for EWMA (len {len(hlc3)})")
        return pd.Series(index=df.index, dtype=float), pd.Series(index=df.index, dtype=float)


    esa = hlc3.ewm(span=n1, adjust=False, min_periods=n1).mean()
    d_abs = abs(hlc3 - esa)
    d = d_abs.ewm(span=n1, adjust=False, min_periods=n1).mean()
    
    # Handle potential division by zero if d is zero or very small
    ci_denominator = 0.015 * d
    # Replace zeros or very small numbers in denominator to avoid inf/NaN
    ci_denominator = ci_denominator.where(ci_denominator.abs() > 1e-9, np.nan) 

    ci = (hlc3 - esa) / ci_denominator
    
    wt1 = ci.ewm(span=n2, adjust=False, min_periods=n2).mean()
    
    # Ensure enough data for rolling mean
    if len(wt1) < wt_smoothing:
        wt2 = pd.Series(index=df.index, dtype=float) # Fill with NaNs
    else:
        wt2 = wt1.rolling(window=wt_smoothing, min_periods=wt_smoothing).mean()
        
    # Reindex to original DataFrame's index to ensure results align, fill missing with NaN
    return wt1.reindex(df.index), wt2.reindex(df.index)


def detect_wt_signal( wt1: pd.Series, wt2: pd.Series):
    if len(wt1) < 2 or len(wt2) < 2 or wt1.isnull().all() or wt2.isnull().all():
        return None # Not enough data or all NaNs
    
    # Get last two non-NaN values if possible
    wt1_valid = wt1.dropna()
    wt2_valid = wt2.dropna()

    if len(wt1_valid) < 2 or len(wt2_valid) < 2:
        return None

    prev_wt1, curr_wt1 = wt1_valid.iloc[-2], wt1_valid.iloc[-1]
    prev_wt2, curr_wt2 = wt2_valid.iloc[-2], wt2_valid.iloc[-1]
    
    if pd.isna(prev_wt1) or pd.isna(curr_wt1) or pd.isna(prev_wt2) or pd.isna(curr_wt2):
        return None # If any of the critical values are NaN after dropna (should not happen with check above)

    if prev_wt1 < prev_wt2 and curr_wt1 > curr_wt2:
        return "bullish"
    elif prev_wt1 > prev_wt2 and curr_wt1 < curr_wt2:
        return "bearish"
    return None

def calc_wt_alert(df: pd.DataFrame):
    wt1, wt2 = calculate_wavetrend(df.copy()) # Pass a copy to avoid modifying original df
    signal_type = detect_wt_signal(wt1, wt2)
    
    # Return Series aligned with df's index, not just single values
    # For alert_bool: True if the last signal is not None
    # For signal_str: The last signal_type detected
    # For wt1, wt2: The full series
    
    # This function seems to be used in detect_signals_from_raw_lines for historical assignment
    # Note: initial_fetch_and_ranking does NOT use market_data files - it uses klines data directly
    # For historical assignment (df[f'wt_alert_{tf}']), we need series.
    # For current state, we need latest values.

    # Let's assume this calc_wt_alert is for adding columns to a historical DF
    # It should return series.
    
    # To generate a series of alerts:
    alert_series = pd.Series(False, index=df.index)
    signal_series = pd.Series(None, index=df.index, dtype=object)

    # Iterate to find all historical crossovers for series generation
    if not wt1.empty and not wt2.empty:
        for i in range(1, len(df)):
            # Check if enough preceding data for iloc[-2] equivalent access for this point i
            if i > 0 and i < len(wt1) and i < len(wt2):
                # Use .iloc[i-1] and .iloc[i] on the wt1/wt2 series which are aligned with df.index
                prev_wt1_val, curr_wt1_val = wt1.iloc[i-1], wt1.iloc[i]
                prev_wt2_val, curr_wt2_val = wt2.iloc[i-1], wt2.iloc[i]

                if pd.notna(prev_wt1_val) and pd.notna(curr_wt1_val) and \
                   pd.notna(prev_wt2_val) and pd.notna(curr_wt2_val):
                    if prev_wt1_val < prev_wt2_val and curr_wt1_val > curr_wt2_val:
                        alert_series.iloc[i] = True
                        signal_series.iloc[i] = "bullish"
                    elif prev_wt1_val > prev_wt2_val and curr_wt1_val < curr_wt2_val:
                        alert_series.iloc[i] = True
                        signal_series.iloc[i] = "bearish"
    
    # For compatibility with how it might be used for current signal (`signal is not None`):
    # The `detect_trading_signals` function seems to look for `curr.get(f"wt_strong_alert_{tf}")`
    # which implies that `wt_strong_alert` is a boolean field on the *current* candle data.
    # The historical `calc_wt_alert` as used in `detect_signas_from_raw_klines` should return the series.

    # If this function is solely for `detect_signas_from_raw_klines`:
    return alert_series, signal_series, wt1, wt2 # Return full series


#####################################################
# CROSSOVER DETECTIONS
#####################################################
def calculate_atr(df: pd.DataFrame, period=14) -> float: # Added type hint for return
    # logger.debug(f"[calculate_atr] => period={period}")
    if df.empty or len(df) < period : # Check df.empty
        # logger.debug("[calculate_atr] => Not enough rows or empty DataFrame, returning 0.0")
        return 0.0
    
    df_atr = df.copy()
    # Ensure HLC are numeric and present
    if not all(c in df_atr.columns for c in ['high', 'low', 'close']): return 0.0
    for col in ['high', 'low', 'close']:
        df_atr[col] = pd.to_numeric(df_atr[col], errors='coerce')
    df_atr.dropna(subset=['high', 'low', 'close'], inplace=True)
    if len(df_atr) < period: return 0.0

    # Using pandas_ta.atr for robustness
    atr_series = ta.atr(high=df_atr['high'], low=df_atr['low'], close=df_atr['close'], length=period)
    
    if atr_series is None or atr_series.empty or pd.isna(atr_series.iloc[-1]):
        return 0.0
    return float(atr_series.iloc[-1])


def detect_stoch_crossovers(df: pd.DataFrame) -> List[dict]: # Added return type hint
    logger.debug("[detect_stoch_crossovers] => Checking for crossovers/crossunders.")
    events = []
    if df.empty or "stoch_rsi" not in df.columns or "timestamp" not in df.columns or "close" not in df.columns:
        logger.debug(f"[detect_stoch_crossovers] => Missing required columns. Has stoch_rsi: {'stoch_rsi' in df.columns}, timestamp: {'timestamp' in df.columns}, close: {'close' in df.columns}")
        return events
    
    # Only process recent data (last 24 hours) to improve performance
    cutoff_time = pd.Timestamp.now(tz='UTC') - pd.DateOffset(days=5)
    df_recent = df[df['timestamp'] >= cutoff_time].copy()
    if df_recent.empty:
        logger.debug("[detect_stoch_crossovers] => No recent data (last 24h), skipping")
        return events
    
    logger.debug(f"[detect_stoch_crossovers] => Processing {len(df_recent)} recent rows (last 24h) out of {len(df)} total rows")
    
    # Ensure stoch_rsi is numeric, others are present (use recent data)
    df_stoch = df_recent.copy()
    df_stoch["stoch_rsi"] = pd.to_numeric(df_stoch["stoch_rsi"], errors='coerce')
    # Timestamps should be pd.Timestamp objects
    if not pd.api.types.is_datetime64_any_dtype(df_stoch['timestamp']):
         df_stoch['timestamp'] = pd.to_datetime(df_stoch['timestamp'], errors='coerce').dt.tz_localize('UTC')
    
    df_stoch.dropna(subset=["stoch_rsi", "timestamp", "close"], inplace=True)
    if df_stoch.empty: 
        logger.debug("[detect_stoch_crossovers] => No valid data after dropping NaN values")
        return events

    st = df_stoch["stoch_rsi"].values
    cl = pd.to_numeric(df_stoch["close"].values, errors='coerce') # Ensure close is numeric array
    ts = df_stoch["timestamp"].values # Array of pd.Timestamp
    
    logger.debug(f"[detect_stoch_crossovers] => Processing {len(df_stoch)} recent rows, stoch_rsi range: {st.min():.3f}-{st.max():.3f}")

    for i in range(1, len(df_stoch)): # Start from 1 to access [i-1]
        prev_s = st[i - 1]
        curr_s = st[i]
        
        if pd.isna(prev_s) or pd.isna(curr_s) or pd.isna(cl[i]): continue

        current_ts = ts[i]
        # Convert numpy datetime64 to pd.Timestamp if needed
        if not isinstance(current_ts, pd.Timestamp):
            try:
                current_ts = pd.Timestamp(current_ts)
            except Exception:
                continue

        # FILTERED: Only detect crossovers outside the 20-80 range (extreme levels)
        # Crossover: from below 20 to above 20 (oversold recovery)
        if prev_s <= 20 and curr_s > 20:
            events.append({
                "idx": df_stoch.index[i], # Use original DataFrame index if available and meaningful
                "action":"BUY", "price": float(cl[i]), "event_type": "stoch_crossover",
                "timestamp": current_ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ') })
            logger.debug(f"[detect_stoch_crossovers] => Found stoch_crossover at {current_ts}, price={cl[i]:.6f}, prev_s={prev_s:.3f}, curr_s={curr_s:.3f}")
        # Crossunder: from above 80 to below 80 (overbought decline)
        elif prev_s >= 80 and curr_s < 80:
            events.append({
                "idx": df_stoch.index[i],
                "action":"SELL", "price": float(cl[i]), "event_type": "stoch_crossunder",
                "timestamp": current_ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ') })
            logger.debug(f"[detect_stoch_crossovers] => Found stoch_crossunder at {current_ts}, price={cl[i]:.6f}, prev_s={prev_s:.3f}, curr_s={curr_s:.3f}")
    logger.debug(f"[detect_stoch_crossovers] => found {len(events)} events (filtered to extreme levels only)")
    return events

def detect_wt_signals(df: pd.DataFrame) -> List[dict]:
    """Detect WaveTrend (WT) signal crossovers and crossunders."""
    logger.debug("[detect_wt_signals] => Checking for WT signal crossovers/crossunders.")
    events = []
    
    # Check for required columns
    required_cols = ["wt1", "wt2", "timestamp", "close"]
    if df.empty or not all(col in df.columns for col in required_cols):
        logger.debug(f"[detect_wt_signals] => Missing required columns. Available: {list(df.columns)}")
        return events
    
    # Only process recent data (last 24 hours) to improve performance
    cutoff_time = pd.Timestamp.now(tz='UTC') - pd.DateOffset(days=5)
    df_recent = df[df['timestamp'] >= cutoff_time].copy()
    if df_recent.empty:
        logger.debug("[detect_wt_signals] => No recent data (last 24h), skipping")
        return events
    
    logger.debug(f"[detect_wt_signals] => Processing {len(df_recent)} recent rows (last 24h) out of {len(df)} total rows")
    
    # Ensure WT values are numeric
    df_wt = df_recent.copy()
    df_wt["wt1"] = pd.to_numeric(df_wt["wt1"], errors='coerce')
    df_wt["wt2"] = pd.to_numeric(df_wt["wt2"], errors='coerce')
    
    # Timestamps should be pd.Timestamp objects
    if not pd.api.types.is_datetime64_any_dtype(df_wt['timestamp']):
         df_wt['timestamp'] = pd.to_datetime(df_wt['timestamp'], errors='coerce').dt.tz_localize('UTC')
    
    df_wt.dropna(subset=["wt1", "wt2", "timestamp", "close"], inplace=True)
    if df_wt.empty: 
        logger.debug("[detect_wt_signals] => No valid data after dropping NaN values")
        return events

    wt1 = pd.to_numeric(df_wt["wt1"], errors="coerce").values
    wt2 = pd.to_numeric(df_wt["wt2"], errors="coerce").values
    cl = pd.to_numeric(df_wt["close"].values, errors='coerce')
    ts = df_wt["timestamp"].values
    
    logger.debug(f"[detect_wt_signals] => Processing {len(df_wt)} recent rows, wt1 range: {wt1.min():.3f}-{wt1.max():.3f}, wt2 range: {wt2.min():.3f}-{wt2.max():.3f}")

    for i in range(1, len(df_wt)): # Start from 1 to access [i-1]
        prev_wt1 = wt1[i - 1]
        curr_wt1 = wt1[i]
        prev_wt2 = wt2[i - 1]
        curr_wt2 = wt2[i]
        price_i = cl[i]

        if (pd.isna(prev_wt1) or pd.isna(curr_wt1) or pd.isna(prev_wt2) or pd.isna(curr_wt2)
                or pd.isna(price_i)):
            continue

        current_ts = ts[i]
        # Convert numpy datetime64 to pd.Timestamp if needed
        if not isinstance(current_ts, pd.Timestamp):
            try:
                current_ts = pd.Timestamp(current_ts)
            except Exception:
                continue

        # Convert to floats and ensure they are finite before saving to events
        try:
            curr_price = float(price_i)
            curr_wt1_f = float(curr_wt1)
            curr_wt2_f = float(curr_wt2)
        except (TypeError, ValueError):
            continue

        if not (np.isfinite(curr_price) and np.isfinite(curr_wt1_f) and np.isfinite(curr_wt2_f)):
            continue

        # WT Crossover: WT1 crosses above WT2 (bullish)
        if prev_wt1 <= prev_wt2 and curr_wt1 > curr_wt2:
            events.append({
                "idx": df_wt.index[i],
                "action": "BUY", 
                "price": curr_price, 
                "event_type": "wt_crossover",
                "timestamp": current_ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "wt1_value": curr_wt1_f if np.isfinite(curr_wt1_f) else 0.0,
                "wt2_value": curr_wt2_f if np.isfinite(curr_wt2_f) else 0.0
            })
            logger.debug(f"[detect_wt_signals] => Found wt_crossover at {current_ts}, price={cl[i]:.6f}, wt1={curr_wt1:.3f}, wt2={curr_wt2:.3f}")

        # WT Crossunder: WT1 crosses below WT2 (bearish)
        elif prev_wt1 >= prev_wt2 and curr_wt1 < curr_wt2:
            events.append({
                "idx": df_wt.index[i],
                "action": "SELL", 
                "price": curr_price, 
                "event_type": "wt_crossunder",
                "timestamp": current_ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "wt1_value": curr_wt1_f if np.isfinite(curr_wt1_f) else 0.0,
                "wt2_value": curr_wt2_f if np.isfinite(curr_wt2_f) else 0.0
            })
            logger.debug(f"[detect_wt_signals] => Found wt_crossunder at {current_ts}, price={cl[i]:.6f}, wt1={curr_wt1:.3f}, wt2={curr_wt2:.3f}")
    
    logger.debug(f"[detect_wt_signals] => found {len(events)} WT events")
    return events

async def load_crosses():
    global crosses_data # Ensure global is modified
    crosses_file = CROSSES_FILE
    # logger.debug(f"Attempting to load crosses data from: {crosses_file}")
    if not crosses_file.exists(): # Use Path object's method
        logger.error(f"Crosses file does not exist: {crosses_file}")
        crosses_data = {} # Initialize to empty dict
        return
    try:
        async with aiofiles.open(crosses_file, "rb") as f: # Read as bytes
            content = await f.read()
        data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
        if not isinstance(data, dict):
            logger.error(f"Crosses file invalid. Expected dict, got {type(data)}.")
            crosses_data = {}
            return
        crosses_data = data
    except orjson.JSONDecodeError as e:  # type: ignore # pylint: disable=no-member,c-extension-no-member
        logger.error(f"Error decoding crosses file {crosses_file} with orjson: {e}")
        crosses_data = {}
    except Exception as e:
        logger.error(f"Unexpected error loading crosses file {crosses_file}: {e}")
        crosses_data = {}

def load_market_json(path):
    try:
        with open(path, 'rb') as f: # Read as bytes for orjson
            data = orjson.loads(f.read())  # type: ignore # pylint: disable=no-member,c-extension-no-member
        if isinstance(data, dict):
            # Filter out entries that are not dicts or don't have a timestamp
            # This helps clean malformed entries if any
            return {k: v for k, v in data.items() 
                    if isinstance(v, dict) and "timestamp" in v and v["timestamp"] is not None}
        # If data is not a dict (e.g. a list, or malformed), log and return empty
        logger.warning(f"load_market_json: Expected dict from {path}, got {type(data)}. Returning empty dict.")
        return {}
    except orjson.JSONDecodeError as e:  # type: ignore # pylint: disable=no-member,c-extension-no-member
        logger.error(f"Failed to decode JSON from {path} with orjson: {e}")
        return {} # Return empty dict on error
    except Exception as e: # Catch other potential errors like file not found if not pre-checked
        logger.error(f"Failed to load market data from {path}: {e}")
        return {}

def json_dumps_safe(data): # Uses standard json for compatibility, orjson can be used if preferred
    def convert_nan_inf(obj):
        if isinstance(obj, float):
            if np.isnan(obj): return None
            if np.isinf(obj): return str(obj) # Or None, or a large number, depending on policy
        if isinstance(obj, dict):
            return {k: convert_nan_inf(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [convert_nan_inf(v) for v in obj]
        # Add numpy type conversions if not handled by recursively_convert_np earlier
        if isinstance(obj, (np.integer)): return int(obj)
        if isinstance(obj, (np.floating)): return None if np.isnan(obj) else (str(obj) if np.isinf(obj) else float(obj))
        if isinstance(obj, (np.bool_)): return bool(obj)
        if isinstance(obj, pd.Timestamp): return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        return obj
    
    cleaned_data = convert_nan_inf(data)
    # Use orjson for dumping if consistency is desired
    try:
        return orjson.dumps(cleaned_data, option=orjson.OPT_INDENT_2).decode('utf-8')  # type: ignore # pylint: disable=no-member,c-extension-no-member
    except Exception: # Fallback or re-raise
        return json.dumps(cleaned_data, indent=2, default=str) # default=str for other unhandled types

def json_loads_safe(content: bytes): # Assumes content is bytes
    try:
        text = content.decode('utf-8') # Orjson prefers bytes directly
    except UnicodeDecodeError:
        logger.error("json_loads_safe: Failed to decode content as UTF-8.")
        raise # Or return a default like {}
    
    try:
        obj = orjson.loads(content) # Pass bytes directly to orjson  # type: ignore # pylint: disable=no-member,c-extension-no-member
    except orjson.JSONDecodeError as e:  # type: ignore # pylint: disable=no-member,c-extension-no-member
        logger.error(f"json_loads_safe: orjson decoding failed: {e}")
        # Optionally, try standard json as a fallback if orjson fails
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e_std:
            logger.error(f"json_loads_safe: standard json decoding also failed: {e_std}")
            raise # Re-raise if both fail
            
    # orjson handles NaN/inf according to standards (None for NaN, specific string/error for Inf depending on options)
    # The fix_nan function might be redundant if orjson is configured correctly or if NaNs are not expected
    def fix_nan_values_recursively(data_obj): # Renamed to be more specific
        if isinstance(data_obj, dict):
            return {k: fix_nan_values_recursively(v) for k, v in data_obj.items()}
        elif isinstance(data_obj, list):
            return [fix_nan_values_recursively(x) for x in data_obj]
        # orjson by default might convert JSON null to None.
        # If you have actual float NaN values after loading (e.g. from non-standard JSON), this is relevant.
        # Standard JSON does not support NaN/Infinity, so they'd typically be null or strings.
        # This check is more for in-memory Python NaNs if they somehow get into the object post-load.
        elif isinstance(data_obj, float) and (np.isnan(data_obj) or data_obj is pd.NA): # Check for pd.NA too
            return None 
        return data_obj
    return fix_nan_values_recursively(obj)

#####################################################
# Detect Tops/Bottoms
#####################################################
def detect_tops_bottoms_1(df: pd.DataFrame, window=20) -> pd.DataFrame:
    df_copy = df.copy()
    if df_copy.empty or 'close' not in df_copy.columns:
        df_copy["top"]= np.nan
        df_copy["bottom"]= np.nan
        return df_copy
    
    df_copy['close'] = pd.to_numeric(df_copy['close'], errors='coerce')
    # Rolling max/min requires enough points and non-NaNs
    # center=True means the window is [i-window/2, i+window/2]
    # Ensure min_periods to avoid all-NaN results if not enough data in window
    min_p = window // 2 + 1 # Heuristic for min_periods with center=True

    rolling_max = df_copy["close"].rolling(window, center=True, min_periods=min_p).max()
    rolling_min = df_copy["close"].rolling(window, center=True, min_periods=min_p).min()

    df_copy["top"] = np.where(df_copy["close"] == rolling_max, df_copy["close"], np.nan)
    df_copy["bottom"] = np.where(df_copy["close"] == rolling_min, df_copy["close"], np.nan)
    return df_copy

def detect_tops_bottoms( df: pd.DataFrame,  distance: int = 5, prominence: float = 1e-8) -> pd.DataFrame:
    df_copy = df.copy()
    if df_copy.empty or "close" not in df_copy.columns:
        df_copy["top"] = np.nan
        df_copy["bottom"] = np.nan
        return df_copy

    df_copy['close'] = pd.to_numeric(df_copy['close'], errors='coerce')
    close_values = df_copy["close"].dropna() # Use dropna version for find_peaks
    
    if close_values.empty or len(close_values) <= distance: # Check length against distance
        df_copy["top"] = np.nan
        df_copy["bottom"] = np.nan
        return df_copy

    # find_peaks works on numpy arrays
    peaks_indices, _ = find_peaks(close_values.values, distance=distance, prominence=prominence)
    troughs_indices, _ = find_peaks(-close_values.values, distance=distance, prominence=prominence) # For minima

    df_copy["top"] = np.nan
    df_copy["bottom"] = np.nan

    # Map indices from close_values (which had NaNs dropped) back to df_copy's original index
    # This ensures tops/bottoms are assigned to the correct rows in the original DataFrame structure
    if len(peaks_indices) > 0:
        df_copy.loc[close_values.iloc[peaks_indices].index, "top"] = close_values.iloc[peaks_indices].values
    if len(troughs_indices) > 0:
        df_copy.loc[close_values.iloc[troughs_indices].index, "bottom"] = close_values.iloc[troughs_indices].values
        
    return df_copy

def calculate_tops_bottoms_score(current_price: float, tops_df: pd.DataFrame) -> float:
    """
    Calculate a score based on current price position relative to detected tops and bottoms.
    
    Args:
        current_price: Current price to evaluate
        tops_df: DataFrame with 'top' and 'bottom' columns from detect_tops_bottoms
        
    Returns:
        Score from -100 to +100 where:
        - +100: Price at recent bottom (bullish)
        - -100: Price at recent top (bearish)
        - 0: Price between tops and bottoms (neutral)
    """
    if tops_df.empty or current_price is None or pd.isna(current_price):
        return 0.0
    
    # Get recent tops and bottoms (last 50 bars for relevance)
    recent_tops = tops_df.dropna(subset=['top']).tail(50)
    recent_bottoms = tops_df.dropna(subset=['bottom']).tail(50)
    
    if recent_tops.empty and recent_bottoms.empty:
        return 0.0
    
    scores = []
    
    # Score based on proximity to tops (bearish)
    if not recent_tops.empty:
        for _, row in recent_tops.iterrows():
            top_price = row['top']
            if pd.notna(top_price):
                # Calculate distance to top
                distance_to_top = abs(current_price - top_price) / top_price
                # Closer to top = more bearish (negative score)
                # Use exponential decay for distance weighting
                top_score = -100 * np.exp(-distance_to_top * 10)  # 10x multiplier for sensitivity
                scores.append(top_score)
    
    # Score based on proximity to bottoms (bullish)
    if not recent_bottoms.empty:
        for _, row in recent_bottoms.iterrows():
            bottom_price = row['bottom']
            if pd.notna(bottom_price):
                # Calculate distance to bottom
                distance_to_bottom = abs(current_price - bottom_price) / bottom_price
                # Closer to bottom = more bullish (positive score)
                # Use exponential decay for distance weighting
                bottom_score = 100 * np.exp(-distance_to_bottom * 10)  # 10x multiplier for sensitivity
                scores.append(bottom_score)
    
    if not scores:
        return 0.0
    
    # Return weighted average of all scores
    # More recent tops/bottoms get higher weight
    weighted_score = np.mean(scores)
    
    # Clamp to [-100, 100] range
    return np.clip(weighted_score, -100, 100)

#####################################################
# Slope & R-value
#####################################################
def calculate_slope_and_rvalue(df: pd.DataFrame) -> tuple[float, float]: # Added type hint
    if df.empty or "top" not in df.columns or "bottom" not in df.columns:
        return 0.0, 0.0
    
    # Ensure 'timestamp' and 'close' are present and valid for sorting and regression
    if 'timestamp' not in df.columns or 'close' not in df.columns:
        return 0.0, 0.0
    
    df_calc = df.copy()
    try:
        df_calc['timestamp'] = pd.to_datetime(df_calc['timestamp'], utc=True, errors='coerce')
    except (ValueError, TypeError):
        df_calc['timestamp'] = pd.to_datetime(df_calc['timestamp'], format="mixed", utc=True, errors='coerce')
    df_calc['close'] = pd.to_numeric(df_calc['close'], errors='coerce')
    df_calc.dropna(subset=['timestamp', 'close', 'top', 'bottom'], how='all', inplace=True) # More specific drop

    tops = df_calc.dropna(subset=["top"])
    bots = df_calc.dropna(subset=["bottom"])
    
    # Ensure combined has 'timestamp' and 'close'
    if tops.empty and bots.empty: return 0.0, 0.0 # No points for regression
    
    combined = pd.concat([tops,bots]).sort_values("timestamp")
    combined.dropna(subset=['close', 'timestamp'], inplace=True) # Ensure regression inputs are valid

    if len(combined)<2: return 0.0,0.0
    
    # Use numeric representation of time for x-axis if timestamps are irregular
    # Here, simple sequential index is used, assuming combined points are somewhat evenly spaced in sequence
    x = np.arange(len(combined)) 
    y = combined["close"].values
    
    if np.isnan(y).any() or len(y) < 2: return 0.0, 0.0

    avgp = y.mean()
    if avgp is None or pd.isna(avgp) or avgp<=0: return 0.0,0.0
    
    y_norm = y / avgp
    
    try:
        slp, icpt, r_val, p_val, std_err = linregress(x, y_norm)
    except ValueError: # linregress can fail if x or y are problematic (e.g. all same value)
        return 0.0, 0.0
        
    slp_pct = slp*100.0 # Represent slope as percentage change per point index step
    return float(slp_pct), float(r_val) if pd.notna(r_val) else 0.0


#####################################################
# DC Score, top movers, etc. 
#####################################################
def calculate_donchian_channels(df: pd.DataFrame, window=20) -> pd.DataFrame:
    df_dc = df.copy()
    if df_dc.empty or not all(c in df_dc.columns for c in ['high', 'low']):
        df_dc["dc_high"] = np.nan
        df_dc["dc_low"] = np.nan
        return df_dc

    for col in ['high', 'low']:
        df_dc[col] = pd.to_numeric(df_dc[col], errors='coerce')
    
    # min_periods should be at least 1, typically window for Donchian
    df_dc["dc_high"] = df_dc["high"].rolling(window, min_periods=window // 2 +1).max() # Adjusted min_periods
    df_dc["dc_low"]  = df_dc["low"].rolling(window, min_periods=window // 2 +1).min()
    return df_dc

def dc_score_for_tf(df: pd.DataFrame, tf:str)-> float:
    wmap = {"4h":100,"1h":60,"15m":40,"3m":25}
    if df.empty or not all(c in df.columns for c in ["dc_high", "dc_low", "close"]) \
       or df[["dc_high", "dc_low", "close"]].iloc[-1].isnull().any(): # Check last row for NaNs
        return 0.0
    
    last_row = df.iloc[-1]
    cl= pd.to_numeric(last_row["close"], errors='coerce')
    dc_h= pd.to_numeric(last_row["dc_high"], errors='coerce')
    dc_l= pd.to_numeric(last_row["dc_low"], errors='coerce')
    
    if pd.isna(cl) or pd.isna(dc_h) or pd.isna(dc_l): return 0.0

    w= wmap.get(tf,0)
    if cl<= dc_l: return -w
    elif cl>= dc_h: return +w
    else: return 0.0
    
def dc_score(dfs: dict)-> float:
    total=0.0
    for tf_loop in ["4h","1h","15m","3m"]: # Renamed tf to tf_loop to avoid conflict
        df_current = dfs.get(tf_loop) # df_current can be None or DataFrame
        if df_current is None or df_current.empty:
            continue
        
        # Calculate Donchian if not present
        if "dc_high" not in df_current.columns or "dc_low" not in df_current.columns:
            df_current = calculate_donchian_channels(df_current, window=20)
            dfs[tf_loop] = df_current # Update the dict with the DataFrame having DC channels

        total+= dc_score_for_tf(df_current, tf_loop)
    return total

async def calculate_15min_returns_for_symbols(all_symbols_dfs: dict)-> dict:
    global mark_price_cache
    async def process_symbol_15m(sym, dfs_sym):
        df_3m= dfs_sym.get("3m") # Can be None or DataFrame
        if df_3m is None or df_3m.empty or 'close_time' not in df_3m.columns or 'close' not in df_3m.columns:
            return sym, 0.0
        df_3m['close_time'] = pd.to_datetime(df_3m['close_time'], errors='coerce')
        df_3m['close'] = pd.to_numeric(df_3m['close'], errors='coerce')
        df_3m.dropna(subset=['close_time', 'close'], inplace=True)
        df_3m.sort_values('close_time', inplace=True)
        if df_3m.empty or len(df_3m) < 2:
            return sym, 0.0
        last_ts= df_3m["close_time"].iloc[-1]
        cutoff= last_ts - pd.Timedelta(minutes=15)
        prevdf= df_3m[df_3m["close_time"] <= cutoff]
        if prevdf.empty:
            return sym, 0.0
        close_15m_ago= prevdf["close"].iloc[-5]
        
        # Use cached mark price if available, otherwise fall back to get_current_price
        last_close = None
        if sym in mark_price_cache:
            last_close = mark_price_cache[sym]["price"]
        else:
            price_result, timestamp = await get_current_price(sym)
            last_close = price_result if price_result else None
        
        if last_close is None or pd.isna(close_15m_ago) or pd.isna(last_close) or close_15m_ago<=0:
            return sym, 0.0
        pct= (last_close- close_15m_ago)/ close_15m_ago * 100.0
        return sym, float(pct)
    
    results = await asyncio.gather(*[process_symbol_15m(sym, dfs_sym) for sym, dfs_sym in all_symbols_dfs.items()], return_exceptions=True)
    ret_15m = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        sym, val = result
        ret_15m[sym] = val
    return ret_15m

async def calculate_3min_returns_for_symbols(all_symbols_dfs: dict) -> dict:
    global mark_price_cache
    async def process_symbol_3m(sym, dfs_sym):
        df_3m = dfs_sym.get("3m")
        if df_3m is None or df_3m.empty or 'close_time' not in df_3m.columns or 'close' not in df_3m.columns:
            return sym, 0.0

        df_3m['close_time'] = pd.to_datetime(df_3m['close_time'], errors='coerce')
        df_3m['close'] = pd.to_numeric(df_3m['close'], errors='coerce')
        df_3m.dropna(subset=['close_time', 'close'], inplace=True)
        df_3m.sort_values('close_time', inplace=True)
        if df_3m.empty or len(df_3m) < 2:
            return sym, 0.0
        
        if len(df_3m) >= 2:
            close_3m_ago = df_3m["close"].iloc[-2]
            
            # Use cached mark price if available, otherwise fall back to get_current_price
            last_close = None
            if sym in mark_price_cache:
                last_close = mark_price_cache[sym]["price"]
            else:
                price_result, _ = await get_current_price(sym)
                last_close = price_result if price_result else None
            
            if last_close is None or pd.isna(close_3m_ago) or pd.isna(last_close) or close_3m_ago <= 0:
                return sym, 0.0
            pct = (last_close - close_3m_ago) / close_3m_ago * 100.0
            return sym, float(pct)
        else:
            return sym, 0.0
    
    results = await asyncio.gather(*[process_symbol_3m(sym, dfs_sym) for sym, dfs_sym in all_symbols_dfs.items()], return_exceptions=True)
    ret_3m = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        sym, val = result
        ret_3m[sym] = val
    return ret_3m

def clean_kline_df(df: pd.DataFrame) -> pd.DataFrame:
    # This function seems to be a placeholder. 
    # Actual cleaning (type conversion, NA handling) is done in load_cached_klines and other places.
    # If specific cleaning unique to this call point is needed, implement here.
    # For now, it's an identity function.
    if df.empty: return df
    
    # Example cleaning: ensure essential columns exist, numeric types, sort by time
    required_cols = ["timestamp", "open", "high", "low", "close", "volume"]
    if not all(col in df.columns for col in required_cols):
        # logger.warning("clean_kline_df: DataFrame missing one or more required columns.")
        # Return empty or df as is, depending on strictness
        return pd.DataFrame() # Stricter: return empty if malformed

    df_cleaned = df.copy()
    df_cleaned['timestamp'] = pd.to_datetime(df_cleaned['timestamp'], errors='coerce').dt.tz_localize('UTC')
    for col in ["open", "high", "low", "close", "volume"]:
        df_cleaned[col] = pd.to_numeric(df_cleaned[col], errors='coerce')
    
    df_cleaned.dropna(subset=required_cols, inplace=True) # Drop rows if any essential data is NA
    df_cleaned.sort_values("timestamp", inplace=True)
    return df_cleaned.reset_index(drop=True)

def top_movers_bonus_map(returns_15m: dict)-> dict:
    if not returns_15m: return {} # Handle empty input

    # Filter out non-float or NaN values before sorting
    valid_returns = {sym: val for sym, val in returns_15m.items() if isinstance(val, (int, float)) and pd.notna(val)}
    if not valid_returns: return {sym: 0 for sym in returns_15m}


    items= sorted(valid_returns.items(), key=lambda x:x[1], reverse=True)
    
    num_items = len(items)
    top_n = min(10, num_items) # Ensure we don't try to get more items than available
    bottom_n = min(10, num_items)

    top10_symbols= [sym for sym, val in items[:top_n]]
    # For bottom, sort ascending then take top_n, or take last_n from descending sort
    # items is sorted descending. items[-bottom_n:] gives the N smallest.
    bot10_symbols= [sym for sym, val in items[-bottom_n:]] 
    
    bonus_map={}
    # Iterate over original keys to ensure all symbols get a bonus value (even if 0)
    for sym in returns_15m.keys():
        if sym in top10_symbols:
            bonus_map[sym]=2
        elif sym in bot10_symbols:
            bonus_map[sym]=-2
        else:
            bonus_map[sym]=0
    return bonus_map

#####################################################
# Relative Gains
#####################################################
def calculate_relative_gains(df: pd.DataFrame) -> float:
    if df.empty or 'close' not in df.columns or len(df) < 2: # Need at least 2 points for regression
        return 0.0
    
    df_gains = df.copy()
    df_gains['close'] = pd.to_numeric(df_gains['close'], errors='coerce')
    df_gains.dropna(subset=['close'], inplace=True)
    if len(df_gains) < 2: return 0.0

    slope_pct, r_value, yhat_abs = calculate_regression_slope_line(df_gains) # Use the existing regression function
    
    if yhat_abs is None or len(yhat_abs) < 2: # yhat_abs could be None or empty if regression fails
        return 0.0
        
    start_val = yhat_abs[0]
    end_val = yhat_abs[-1]
    
    if pd.isna(start_val) or pd.isna(end_val) or start_val <= 0:
        return 0.0
        
    return float((end_val - start_val) / start_val * 100.0)

def calculate_regression_slope_line(df: pd.DataFrame) -> tuple[float, float, np.ndarray]:
    if df.empty or 'close' not in df.columns or len(df.dropna(subset=['close'])) < 2: # Min 2 points for linregress
        return 0.0, 0.0, np.array([]) # Return empty array for yhat_abs

    df_reg = df.copy()
    df_reg['close'] = pd.to_numeric(df_reg['close'], errors='coerce')
    yvals = df_reg["close"].dropna() # Use only non-NaN close values for regression
    
    if len(yvals) < 2: return 0.0, 0.0, np.array([])

    avgp = yvals.mean()
    if pd.isna(avgp) or avgp <= 0: return 0.0, 0.0, np.array([])
        
    xvals = np.arange(len(yvals)) # X-axis based on count of valid y-points
    ynorm = yvals.values / avgp # .values to ensure numpy array for linregress
    
    try:
        slp, icpt, rvv, p_val, std_err = linregress(xvals, ynorm)
    except ValueError: # Handles cases like all yvals being the same
        return 0.0, 0.0, np.array([])

    slope_pct = slp * 100.0
    # Calculate yhat_abs based on original yvals length and indices if needed for plotting alignment
    # For calculate_relative_gains, yhat_abs on the `yvals` (dropna'd) sequence is fine.
    yhat_norm_on_valid_x = icpt + slp * xvals
    yhat_abs_on_valid_x = yhat_norm_on_valid_x * avgp
    
    # To return a yhat_abs series aligned with the original df's index (with NaNs):
    yhat_abs_aligned = pd.Series(np.nan, index=df.index) # Create NaN series with original index
    yhat_abs_aligned.loc[yvals.index] = yhat_abs_on_valid_x 
    return float(slope_pct), float(rvv) if pd.notna(rvv) else 0.0, yhat_abs_on_valid_x



#####################################################
# Proximity logic
#####################################################

def calculate_relative_volume(df: pd.DataFrame, window: int = 50) -> float:
    if df.empty or 'volume' not in df.columns: return 1.0 # Default if no data
    
    df_vol = df.copy()
    df_vol['volume'] = pd.to_numeric(df_vol['volume'], errors='coerce')
    df_vol.dropna(subset=['volume'], inplace=True)
    
    if len(df_vol) < window // 2 : return 1.0 # Heuristic: if very few candles, assume normal volume

    # Ensure enough data for recent_vol calculation
    if len(df_vol) < window:
        recent_vol = df_vol["volume"].mean() # Use all available if less than window
    else:
        recent_vol = df_vol["volume"].iloc[-window:].mean()

    # For past_vol, consider data before the recent window
    if len(df_vol) <= window: # If data is less than or equal to one window
        # Not much "past" data, so either use overall mean or return 1.0 to indicate no strong signal
        past_vol = df_vol["volume"].mean() # Or, could be recent_vol itself if no distinct past
        if past_vol == recent_vol: return 1.0 # Avoid issues if they are identical due to short data
    else: # len(df_vol) > window
        past_vol = df_vol["volume"].iloc[:-window].mean()
    
    if pd.isna(recent_vol) or pd.isna(past_vol): return 1.0
    if past_vol == 0: # Avoid division by zero
        return float('inf') if recent_vol > 0 else 1.0 # If recent is also 0, then 1.0
    
    rel_vol = recent_vol / past_vol
    return float(rel_vol) if pd.notna(rel_vol) else 1.0

def calculate_average_volume(df: pd.DataFrame, window: int = 50) -> float:
    """Calculate average volume over a specified window period in USDT equivalent"""
    if df.empty or 'volume' not in df.columns or 'close' not in df.columns: return 0.0
    
    df_vol = df.copy()
    df_vol['volume'] = pd.to_numeric(df_vol['volume'], errors='coerce')
    df_vol['close'] = pd.to_numeric(df_vol['close'], errors='coerce')
    df_vol.dropna(subset=['volume', 'close'], inplace=True)
    
    if len(df_vol) < window // 2: return 0.0
    
    # Convert volume from base currency to USDT equivalent
    df_vol['volume_usdt'] = df_vol['volume'] * df_vol['close']
    
    if len(df_vol) < window:
        avg_vol_usdt = df_vol["volume_usdt"].mean()
    else:
        avg_vol_usdt = df_vol["volume_usdt"].iloc[-window:].mean()
    
    return float(avg_vol_usdt) if pd.notna(avg_vol_usdt) else 0.0

def is_low_volume_token(dfs_dict: dict, volume_threshold: float = 80000.0) -> bool:
    """
    More robust volume filtering with multiple timeframe checks.
    """
    if not dfs_dict:
        return True
    
    # Check multiple timeframes
    volume_checks = []
    
    for tf, df in dfs_dict.items():
        if df is None or df.empty:
            continue
        
        # Calculate average volume in USDT
        avg_volume_usdt = calculate_average_volume(df, window=50)
        
        # Weight different timeframes differently
        weight = {"4h": 1.0, "1h": 0.8, "15m": 0.5, "3m": 0.3}.get(tf, 0.5)
        
        meets_threshold = avg_volume_usdt >= (volume_threshold * weight)
        volume_checks.append((meets_threshold, weight))
    
    if not volume_checks:
        return True
    total_weight = sum(weight for _, weight in volume_checks)
    passed_weight = sum(weight for passed, weight in volume_checks if passed)
    if not total_weight:
        return True
    return (passed_weight / total_weight) < 0.5

def is_low_volume_token_safe(dfs_dict: dict, volume_threshold: float = 80000.0) -> bool:
    """
    Determine if a token has low volume using a safe, conservative threshold.
    Returns True if the token should be disqualified from rankings.
    
    Args:
        dfs_dict: Dictionary of dataframes for different timeframes
        volume_threshold: Conservative volume threshold in USDT (default: 80k)
    """
    if not dfs_dict:
        return True  # Disqualify if no data
    
    # Calculate average volumes for each timeframe
    avg_volumes = {}
    for tf, df in dfs_dict.items():
        if df is not None and not df.empty:
            avg_volumes[tf] = calculate_average_volume(df, window=50)
        else:
            avg_volumes[tf] = 0.0
    
    # Use 1h timeframe as primary volume indicator, fallback to others
    primary_volume = avg_volumes.get('1h', 0.0)
    if primary_volume == 0.0:
        # Fallback to 15m or 3m if 1h is not available
        primary_volume = avg_volumes.get('15m', avg_volumes.get('3m', 0.0))
    
    # If still no volume data, disqualify
    if primary_volume == 0.0:
        return True
    
    # Use conservative threshold - only remove truly low volume tokens
    return primary_volume < volume_threshold

async def filter_symbols_by_volume_safe(symbols: list, timeframes: list = ["4h", "1h", "15m", "3m"], volume_threshold: float = 80000.0) -> tuple[list, dict]:
    """
    Filter symbols by volume using a safe, conservative approach.
    Returns (filtered_symbols_list, filtering_stats)
    """
    logger.info(f"🔍 Safe volume filtering {len(symbols)} symbols (threshold: {volume_threshold:,.0f} USDT)...")
    
    filtered_symbols = []
    disqualified_symbols = []
    no_data_symbols = []
    
    for sym in symbols:
        try:
            # Quick volume check with minimal data loading
            dfs_current_sym = {}
            for tf in timeframes:
                dftf = await convert_cached_klines_to_analysis_df(sym, tf)
                if dftf.empty: continue
                dfs_current_sym[tf] = dftf
            
            if not dfs_current_sym:
                no_data_symbols.append(sym)
                logger.debug(f"🚫 Disqualifying {sym} - no data")
                continue
            
            # Volume filtering: Skip low volume tokens to avoid spread issues
            if is_low_volume_token_safe(dfs_current_sym, volume_threshold):
                disqualified_symbols.append(sym)
                logger.debug(f"🚫 Disqualifying {sym} due to low volume")
                continue
            
            filtered_symbols.append(sym)
            
        except Exception as e:
            logger.debug(f"Error filtering {sym}: {e}")
            disqualified_symbols.append(sym)
            continue
    
    logger.info(f"✅ Safe volume filtering complete:")
    logger.info(f"   📊 Original symbols: {len(symbols)}")
    logger.info(f"   ✅ Filtered symbols: {len(filtered_symbols)}")
    logger.info(f"   🚫 Disqualified: {len(disqualified_symbols)}")
    logger.info(f"   ❌ No data: {len(no_data_symbols)}")
    logger.info(f"   📊 Volume threshold: {volume_threshold:,.0f} USDT")
    
    if disqualified_symbols:
        logger.debug(f"🚫 Disqualified tokens: {disqualified_symbols[:10]}{'...' if len(disqualified_symbols) > 10 else ''}")
    
    return filtered_symbols, {
        'original_count': len(symbols),
        'filtered_count': len(filtered_symbols),
        'disqualified_count': len(disqualified_symbols),
        'no_data_count': len(no_data_symbols),
        'volume_threshold': volume_threshold,
        'disqualified_symbols': disqualified_symbols,
        'no_data_symbols': no_data_symbols }
        

def calculate_simple_proximity_score(current_price: float, dfs_dict: Dict[str, pd.DataFrame]) -> float:
    """
    Simple proximity score without weights (for comparison).
    Uses average of all timeframe proximity scores.
    """
    if current_price is None or pd.isna(current_price):
        return 0.0
    timeframes = ['D', '4h', '1h', '15m', '3m']
    scores = []
    for timeframe in timeframes:
        df = dfs_dict.get(timeframe)
        if df is None or df.empty:
            continue
        
        band_df = calculate_regression_band(df)
        if band_df.empty or 'upperb' not in band_df.columns or 'lowerb' not in band_df.columns:
            continue
        
        upper_band = band_df['upperb'].iloc[-1]
        lower_band = band_df['lowerb'].iloc[-1]
        
        if pd.isna(upper_band) or pd.isna(lower_band) or upper_band == lower_band:
            continue
        
        span = upper_band - lower_band
        frac = (current_price - lower_band) / span
        score = (90.0 - (180.0 * frac)) * (100/90)
        scores.append(score)
    
    if not scores:
        return 0.0
    
    return float(np.mean(scores))

def calculate_proximity_score(current_price: float, dfs_dict: Dict[str, pd.DataFrame]) -> float:
    if current_price is None or pd.isna(current_price):
        return 0.0
    tf_weights = {
        'D': 4.0,
        '4h': 4.0,
        '1h': 2.0,
        '15m': 1.0,
        '3m': 0.5  }
    weighted_scores = []
    total_weight = 0.0
    for tf, weight in tf_weights.items():
        df = dfs_dict.get(tf)
        if df is None or df.empty or 'close' not in df.columns:
            continue
        band_df = calculate_regression_band(df)
        if band_df.empty or 'upperb' not in band_df.columns or 'lowerb' not in band_df.columns:
            continue
        upper_band = band_df['upperb'].iloc[-1]
        lower_band = band_df['lowerb'].iloc[-1]
        if pd.isna(upper_band) or pd.isna(lower_band) or upper_band == lower_band:
            continue
        span = upper_band - lower_band
        if span == 0:
            continue
        frac = (current_price - lower_band) / span
        frac = max(0.0, min(1.0, frac))  # Clamp to [0, 1]
        score = 100.0 - (frac * 200.0)
        weighted_scores.append(score * weight)
        total_weight += weight
    if not weighted_scores or total_weight == 0:
        return 0.0
    return sum(weighted_scores) / total_weight

def get_proximity_score(current_price: Optional[float], bigger_df: pd.DataFrame) -> float:
    if current_price is None or pd.isna(current_price): return 0.0
    if bigger_df.empty or not all(c in bigger_df.columns for c in ['high', 'low']): # Use H/L for range
        return 0.0

    df_prox = bigger_df.copy()
    df_prox['high'] = pd.to_numeric(df_prox['high'], errors='coerce')
    df_prox['low'] = pd.to_numeric(df_prox['low'], errors='coerce')
    
    # Consider the range of the entire 'bigger_df'
    # Using min of lows and max of highs over the period of bigger_df
    bot_price = df_prox["low"].min()
    top_price = df_prox["high"].max()

    if pd.isna(bot_price) or pd.isna(top_price): return 0.0
    if np.isclose(top_price, bot_price): return 0.0 # Avoid division by zero if range is tiny

    ratio = (current_price - bot_price) / (top_price - bot_price)
    
    # Clamp ratio to [0, 1] before calculating score
    # This handles cases where current_price is outside the historical min/max of bigger_df
    ratio = max(0.0, min(1.0, ratio)) 
    
    # Score: +100 at bottom (ratio=0), -100 at top (ratio=1)
    # Original was: score = 100 - (ratio * 200) -> (ratio=0 => 100, ratio=1 => -100)
    # If you want +50 at bottom, -50 at top, it would be: 50 - (ratio * 100)
    # Keeping original:
    score = 100.0 - (ratio * 200.0)
    return float(score)


def calculate_cross_score( row: pd.Series,df_15m: pd.DataFrame, df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> float:
    scores = []
    current_close_price = row.get("close")
    if current_close_price is None or pd.isna(current_close_price):
        return 0.0 # Cannot calculate score without a current price from the row

    for bigger_df in [df_15m, df_1h, df_4h]:
        if bigger_df is not None and not bigger_df.empty:
            score_tf = get_proximity_score(current_close_price, bigger_df)
            scores.append(score_tf)
            
    if not scores: return 0.0
    return float(np.mean(scores))


def assign_points_proximity_3m(
    df_3m: pd.DataFrame,
    df_15m: Optional[pd.DataFrame], # Allow None
    df_1h: Optional[pd.DataFrame],
    df_4h: Optional[pd.DataFrame]
) -> pd.DataFrame:
    df_3m_copy = df_3m.copy()
    if df_3m_copy.empty or "zone" not in df_3m_copy.columns or 'close' not in df_3m_copy.columns:
        df_3m_copy["proximity_score"] = 0.0
        return df_3m_copy
    
    # Ensure 'close' is numeric for calculations
    df_3m_copy['close'] = pd.to_numeric(df_3m_copy['close'], errors='coerce')

    # Pre-calculate relative volumes
    vol_mult_3m = calculate_relative_volume(df_3m_copy, window=20)
    vol_mult_15m = calculate_relative_volume(df_15m, window=20) if df_15m is not None and not df_15m.empty else 1.0
    
    # Apply clamping/scaling to volume multipliers
    vol_mult_3m_scaled = 1.2 if vol_mult_3m > 1.3 else (0.8 if vol_mult_3m < 1.0 else 1.0)
    vol_mult_15m_scaled = 1.2 if vol_mult_15m > 1.3 else (0.8 if vol_mult_15m < 1.0 else 1.0)
    
    # Average scaled volume multipliers
    avg_vol_mult = (vol_mult_3m_scaled + vol_mult_15m_scaled) / 2.0

    proximity_scores_list = []
    for i, row in df_3m_copy.iterrows():
        if pd.isna(row['close']): # Skip if no valid close price for the row
            proximity_scores_list.append(0.0) # Or np.nan
            continue

        base_pts = calculate_cross_score(row=row, df_15m=df_15m, df_1h=df_1h, df_4h=df_4h)
        
        zone = row.get("zone", "neutral") # Default to neutral if zone missing
        if zone == "green":
            base_pts += 10
        elif zone == "red":
            base_pts -= 10
            
        final_score_for_row = base_pts * avg_vol_mult
        proximity_scores_list.append(final_score_for_row)
    
    df_3m_copy["proximity_score"] = proximity_scores_list
    return df_3m_copy

def get_band_score_safe(current_price: float, dfs_dict: Dict[str, pd.DataFrame]) -> float:
    """
    Safe band score calculation that handles missing timeframes.
    """
    if current_price is None or pd.isna(current_price):
        return 0.0
    
    # Try the full weighted calculation first
    try:
        band_data = calculate_weighted_band_score(current_price, dfs_dict)
        if band_data and "score" in band_data:
            return band_data["score"]
    except Exception as e:
        logger.debug(f"Full band score calculation failed for {list(dfs_dict.keys())}: {e}")
    
    # Fallback: calculate from available timeframes
    scores = []
    for tf, df in dfs_dict.items():
        if df is None or df.empty:
            continue
        
        band_df = calculate_regression_band(df)
        if band_df.empty:
            continue
        
        upper_band = band_df['upperb'].iloc[-1]
        lower_band = band_df['lowerb'].iloc[-1]
        
        if pd.isna(upper_band) or pd.isna(lower_band) or upper_band == lower_band:
            continue
        
        span = upper_band - lower_band
        if span == 0:
            continue
        
        frac = (current_price - lower_band) / span
        frac = max(0.0, min(1.0, frac))
        score = 100.0 - (frac * 200.0)
        scores.append(score)
    
    if not scores:
        return 0.0
    return sum(scores) / len(scores)

def calculate_weighted_band_score(current_price: float, dfs_dict: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    """
    Calculate weighted band score using regression bands across ALL timeframes.
    
    Weights (same as proximity for consistency):
    - D (Daily): 4x
    - 4h: 4x
    - 1h: 2x
    - 15m: 1x
    - 3m: 0.5x
    
    Returns:
        Dictionary with 'score', 'upper_band', 'lower_band', and breakdown by timeframe
    """
    if current_price is None or pd.isna(current_price):
        return {"score": 0.0, "upper_band": None, "lower_band": None, "breakdown": {}}
    
    # Weights for each timeframe
    weights = {
        'D': 4.0,
        '4h': 4.0,
        '1h': 2.0,
        '15m': 1.0,
        '3m': 0.5
    }
    
    total_weighted_score = 0.0
    total_weight = 0.0
    breakdown = {}
    
    # Use 4h band as primary for upper/lower band reference
    primary_upper_band = None
    primary_lower_band = None
    
    # Store references to dataframes for later use
    df_4h_ref = dfs_dict.get('4h')
    df_1h_ref = dfs_dict.get('1h')
    
    for timeframe, weight in weights.items():
        df = dfs_dict.get(timeframe)
        if df is None or df.empty:
            continue
        
        # Calculate regression band for this timeframe
        band_df = calculate_regression_band(df)
        if band_df.empty or 'upperb' not in band_df.columns or 'lowerb' not in band_df.columns:
            continue
        
        # Get the latest band values
        upper_band = band_df['upperb'].iloc[-1]
        lower_band = band_df['lowerb'].iloc[-1]
        
        if pd.isna(upper_band) or pd.isna(lower_band) or upper_band == lower_band:
            continue
        
        # Store 4h band as primary reference
        if timeframe == '4h':
            primary_upper_band = upper_band
            primary_lower_band = lower_band
        
        # Calculate band position score
        span = upper_band - lower_band
        if span == 0:
            continue
        
        frac = (current_price - lower_band) / span
        frac = max(0.0, min(1.0, frac))  # Clamp to [0, 1]
        
        # Score from -100 (upper band) to +100 (lower band)
        score = 100.0 - (frac * 200.0)
        
        # Apply weight
        weighted_score = score * weight
        total_weighted_score += weighted_score
        total_weight += weight
        
        # Store breakdown
        breakdown[timeframe] = {
            'score': score,
            'weighted_score': weighted_score,
            'weight': weight,
            'upper_band': upper_band,
            'lower_band': lower_band,
            'distance_pct': ((current_price - lower_band) / lower_band * 100) if lower_band > 0 else 0.0
        }
    
    if total_weight == 0:
        return {"score": 0.0, "upper_band": None, "lower_band": None, "breakdown": {}}
    
    # Calculate weighted average
    final_score = total_weighted_score / total_weight
    
    # Apply improvements to the final_score (not individual scores)
    
    # 1. Band Width Factor
    if primary_upper_band and primary_lower_band and current_price > 0:
        band_width_pct = ((primary_upper_band - primary_lower_band) / current_price * 100)
        if band_width_pct < 5.0:
            final_score *= 1.2  # 20% boost for narrow bands
        elif band_width_pct > 20.0:
            final_score *= 0.8  # 20% reduction for very wide bands
    
    # 2. Band Slope Alignment
    df_slope = df_4h_ref if df_4h_ref is not None and not df_4h_ref.empty else df_1h_ref
    if df_slope is not None and not df_slope.empty and len(df_slope) >= 20:
        # Calculate short-term slope (last 10 bars)
        recent_prices = df_slope['close'].tail(10).values
        if len(recent_prices) >= 2:
            x = np.arange(len(recent_prices))
            slope, _, _, _, _ = linregress(x, recent_prices)
            
            # If price moving toward lower band and slope is negative, more bullish
            if final_score > 50 and slope < 0:
                final_score *= 1.1  # Moving down toward support
            
            # If price moving toward upper band and slope is positive, more bearish
            if final_score < -50 and slope > 0:
                final_score *= 1.1  # Moving up toward resistance
    
    # 3. Volume Confirmation
    if df_4h_ref is not None and not df_4h_ref.empty and 'volume' in df_4h_ref.columns:
        recent_volume = df_4h_ref['volume'].tail(5).mean()
        avg_volume = df_4h_ref['volume'].mean()
        
        # High volume near bands = stronger signal
        if recent_volume > avg_volume * 1.5:
            if abs(final_score) > 70:  # Near extreme
                final_score *= 1.15  # Boost with volume confirmation
    
    # Ensure score stays within bounds
    final_score = max(-100.0, min(100.0, final_score))
    
    # For breakout detection, also calculate how far price is from nearest band
    breakout_potential = 0.0
    if primary_upper_band and primary_lower_band:
        # Calculate percentage distance to nearest band
        distance_to_upper = abs(current_price - primary_upper_band) / primary_upper_band if primary_upper_band > 0 else 0
        distance_to_lower = abs(current_price - primary_lower_band) / primary_lower_band if primary_lower_band > 0 else 0
        nearest_distance = min(distance_to_upper, distance_to_lower)
        
        # Breakout potential: 0 (at band) to 100 (far from band)
        breakout_potential = (1.0 - min(1.0, nearest_distance * 10)) * 100
    
    return {
        "score": final_score,
        "upper_band": primary_upper_band,
        "lower_band": primary_lower_band,
        "breakout_potential": breakout_potential,
        "breakdown": breakdown
    }

def save_band_scores(score_dict: dict, file_path: Path):
    """Save band scores sorted by score."""
    sorted_scores = dict(sorted(score_dict.items(), key=lambda x: x[1], reverse=True))
    if file_path.exists():
        try:
            with open(file_path, "r") as f:
                existing = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load existing band scores from {file_path}: {e}")
            existing = {}
    else:
        existing = {}
    
    existing.update(sorted_scores)
    existing = dict(sorted(existing.items(), key=lambda x: x[1], reverse=True))
    existing = recursively_convert_np(existing)
    
    with open(file_path, "w") as f:
        json.dump(existing, f, indent=4)
    logger.info(f"Saved {len(sorted_scores)} band scores to {file_path}")

def calculate_multi_timeframe_band_score(
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame, 
    df_D: pd.DataFrame,
    current_price: float
) -> dict: # <-- Now returns a dictionary
    """
    Enhanced band score that also returns the key boundaries for breakthrough calculations.
    """
    scores = []
    final_upper_band = None
    final_lower_band = None
    
    # 1. 4h Regression Band (main component)
    if not df_4h.empty and len(df_4h) >= 200:
        band_4h = calculate_regression_band(df_4h)
        if not band_4h.empty:
            # Store the latest band boundaries
            final_upper_band = band_4h['upperb'].iloc[-1]
            final_lower_band = band_4h['lowerb'].iloc[-1]
            score_4h = band_position_score(current_price, final_lower_band, final_upper_band)
            scores.append(score_4h * 0.5)

    # 2. 1h Tops/Bottoms Context
    if not df_1h.empty and len(df_1h) >= 50:
        tops_1h = detect_tops_bottoms(df_1h, distance=5)
        if not tops_1h.empty:
            score_1h = calculate_tops_bottoms_score(current_price, tops_1h)
            scores.append(score_1h * 0.3)

    # 3. Daily Tops/Bottoms Context  
    if not df_D.empty and len(df_D) >= 100:
        tops_D = detect_tops_bottoms(df_D, distance=10)
        if not tops_D.empty:
            score_D = calculate_tops_bottoms_score(current_price, tops_D)
            scores.append(score_D * 0.2)
    
    # Return a dictionary with score and boundaries
    return {
        "score": sum(scores) if scores else 0.0,
        "upper_band": final_upper_band,
        "lower_band": final_lower_band
    }

def get_proximity_range(bigger_df: pd.DataFrame) -> dict:
    """Calculates and returns the top and bottom of a dataframe's price range."""
    if bigger_df.empty or not all(c in bigger_df.columns for c in ['high', 'low']):
        return {"top": None, "bottom": None}

    df_prox = bigger_df.copy()
    df_prox['high'] = pd.to_numeric(df_prox['high'], errors='coerce')
    df_prox['low'] = pd.to_numeric(df_prox['low'], errors='coerce')
    
    bot_price = df_prox["low"].min()
    top_price = df_prox["high"].max()

    return {"top": top_price, "bottom": bot_price}


def calculate_regression_band(df, atr_period=200, atr_multiplier=6): # atr_multiplier not used with stdev
    if df.empty or 'close' not in df.columns or 'close_time' not in df.columns:
        return pd.DataFrame(columns=["time", "upperb", "lowerb"]) # Ensure columns exist even if empty
        
    df_band = df.copy()
    df_band['close'] = pd.to_numeric(df_band['close'], errors='coerce')
    df_band['close_time'] = pd.to_datetime(df_band['close_time'], errors='coerce')
    df_band.dropna(subset=['close', 'close_time'], inplace=True) # Critical columns for regression and output
    
    if len(df_band) < 2: # Linregress needs at least 2 points
         return pd.DataFrame(columns=["time", "upperb", "lowerb"])

    avgp = df_band["close"].mean()
    if pd.isna(avgp) or avgp <= 0:
        return pd.DataFrame(columns=["time", "upperb", "lowerb"])

    xvals = np.arange(len(df_band))
    yvals = df_band["close"].values
    ynorm = yvals / avgp
    
    try:
        slp, icpt, r_val, p_val, std_err = linregress(xvals, ynorm)
    except ValueError:
        return pd.DataFrame(columns=["time", "upperb", "lowerb"])

    yhat_abs = (icpt + slp * xvals) * avgp
    resid = yvals - yhat_abs
    stdev = np.std(resid) # Standard deviation of residuals

    # Using stdev of residuals for bands
    upperb_series = yhat_abs + 2.5 * stdev
    lowerb_series = yhat_abs - 2.5 * stdev
    
    out_df = pd.DataFrame({
        "time": df_band["close_time"], # Use original timestamps for alignment
        "upperb": upperb_series,
        "lowerb": lowerb_series
    })
    return out_df

def merge_htf_band_into_ltf(df_ltf: pd.DataFrame, band_htf: pd.DataFrame) -> pd.DataFrame: # Added type hints
    if df_ltf.empty:
        # If ltf is empty, still define columns for consistency if band_htf is also empty
        return df_ltf.assign(upperb=np.nan, lowerb=np.nan, time=pd.NaT) # time from band_htf
        
    if band_htf.empty or not all(c in band_htf.columns for c in ['time', 'upperb', 'lowerb']):
        # If band_htf is empty or malformed, return ltf with NaN band columns
        return df_ltf.assign(upperb=np.nan, lowerb=np.nan) 

    # Ensure datetime columns are actual datetimes and sorted
    df_ltf_sorted = df_ltf.copy()
    df_ltf_sorted['close_time'] = pd.to_datetime(df_ltf_sorted['close_time'], errors='coerce')
    df_ltf_sorted.sort_values("close_time", inplace=True)
    
    band_htf_sorted = band_htf.copy()
    band_htf_sorted['time'] = pd.to_datetime(band_htf_sorted['time'], errors='coerce')
    band_htf_sorted.sort_values("time", inplace=True)

    # Drop rows with NaT in time columns after conversion, as merge_asof might behave unexpectedly
    df_ltf_sorted.dropna(subset=['close_time'], inplace=True)
    band_htf_sorted.dropna(subset=['time', 'upperb', 'lowerb'], inplace=True) # Ensure band data is valid

    if df_ltf_sorted.empty or band_htf_sorted.empty: # Check again after dropna
        return df_ltf.assign(upperb=np.nan, lowerb=np.nan) # Or df_ltf_sorted if preferred

    merged = pd.merge_asof(
        df_ltf_sorted,
        band_htf_sorted,
        left_on="close_time",
        right_on="time", # 'time' is from band_htf
        direction="backward" # Use last known band values from HTF
    )
    # `time` column from band_htf will be present in merged. Remove if not needed.
    # merged.drop(columns=['time'], inplace=True, errors='ignore')
    return merged


def calculate_weighted_gains(df, num_bars, decay_factor=0.98):
    """
    Calculate weighted gains/losses with exponential decay favoring recent bars.
    
    Args:
        df: DataFrame with 'close' column
        num_bars: Number of bars to look back
        decay_factor: Exponential decay factor (0.98 = 2% decay per bar)
    
    Returns:
        Weighted percentage gain/loss
    """
    if df is None or df.empty or 'close' not in df.columns:
        return 0.0
    
    # Get the last num_bars bars
    recent_data = df.tail(num_bars + 1)  # +1 to calculate returns
    if len(recent_data) < 2:
        return 0.0
    
    # Calculate returns for each bar
    returns = recent_data['close'].pct_change().iloc[1:]  # Skip first NaN
    
    if returns.empty:
        return 0.0
    
    # Create exponential weights (most recent bar gets weight 1)
    weights = np.array([decay_factor ** (len(returns) - i - 1) for i in range(len(returns))])
    weights = weights / weights.sum()  # Normalize weights to sum to 1
    
    # Calculate weighted return
    weighted_return = (returns.values * weights).sum()
    
    # Convert to percentage
    return weighted_return * 100

def band_position_score(price, lowerb, upperb):
    if pd.isna(price) or pd.isna(lowerb) or pd.isna(upperb):
        # FALLBACK: If bands are missing (insufficient data), use neutral score
        # This prevents new symbols from getting 0.0 scores due to missing data
        if pd.notna(price):
            return 0.0  # Neutral score for insufficient data scenarios
        return np.nan # Only return NaN if price itself is invalid
    
    span = upperb - lowerb
    if pd.isna(span) or np.isclose(span, 0): # span can be NaN if upperb/lowerb were NaN
        # If span is zero (upperb == lowerb):
        # Score could be 0 if price is on the line, or depends on convention for outside
        if pd.notna(price) and np.isclose(price, upperb): return 0.0 
        return np.nan # Undefined if span is zero and price is not on the line, or bands are NaN

    frac = (price - lowerb) / span
    

    score = 90.0 - 180.0 * frac
    return float(score)


def apply_band_scores(df):
    df_scored = df.copy()
    if not all(c in df_scored.columns for c in ['close', 'lowerb', 'upperb']):
        df_scored["band_score"] = np.nan # Ensure column exists even if inputs are missing
        return df_scored

    df_scored["band_score"] = df_scored.apply(
        lambda row: band_position_score(row.get("close"), row.get("lowerb"), row.get("upperb")), axis=1
    )
    return df_scored


def apply_event_modifier(df):
    df_modified = df.copy()
    if "band_score" not in df_modified.columns:
        # Calculate band_scores if not present. Requires 'close', 'lowerb', 'upperb'.
        if all(c in df_modified.columns for c in ['close', 'lowerb', 'upperb']):
            df_modified = apply_band_scores(df_modified)
        else: # Cannot calculate, ensure column exists with NaN
            df_modified["band_score"] = np.nan
            # No event modification possible if band_score cannot be determined
            return df_modified 
            
    # Ensure event_type column exists, default to None or empty string
    if "event_type" not in df_modified.columns:
        df_modified["event_type"] = None # Or ""
    
    def row_logic(row):
        base_score = row.get("band_score") # Get potentially NaN score
        if pd.isna(base_score):
            return np.nan # Keep NaN if base score is NaN

        etype = row.get("event_type")
        if etype is None or not isinstance(etype, str): # Handle None or non-string etype
            return base_score # No modification if event_type is not a recognized string

        # Case-insensitive check for event types
        etype_lower = etype.lower()
        if "crossunder" in etype_lower: # More robust check
            return base_score - 10.0
        elif "crossover" in etype_lower:
            return base_score + 10.0
        return base_score 
        
    df_modified["band_score"] = df_modified.apply(row_logic, axis=1)
    return df_modified

def ensure_symbol_timeframes(ranking_info_dict, sym, tfs=None): # Renamed arg
    if tfs is None:
        tfs = ["4h", "1h", "15m", "3m"]
    if sym not in ranking_info_dict:
        ranking_info_dict[sym] = {} # defaultdict would handle this, but explicit is fine
    for tf_loop in tfs: # Renamed tf
        if tf_loop not in ranking_info_dict[sym]:
            ranking_info_dict[sym][tf_loop] = {}


def normalize_signed(val: float, raw_min: float, raw_max: float) -> float:
    if pd.isna(val): return 0.0 # Handle NaN input
    if raw_max == raw_min: # Avoid division by zero if range is zero
        return 0.0 if val == raw_min else (100.0 if val > raw_min else -100.0) # Or other appropriate logic for zero range

    if val >= 0:
        # Normalize positive values to [0, 100]
        # If raw_max is 0 or negative, all positive values effectively become outliers beyond the "positive range"
        # A common approach is to scale based on the magnitude of raw_max.
        # If raw_max is 0, and val > 0, it's "infinitely" above max.
        return (val / raw_max) * 100.0 if raw_max > 0 else (100.0 if val > 0 else 0.0) # Cap if raw_max invalid
    else: # val < 0
        # Normalize negative values to [-100, 0]
        # raw_min should be negative. If raw_min is 0 or positive, all negative vals are outliers.
        # -(val / abs(raw_min)) * 100
        # If val = raw_min (e.g. -50), then -(-50 / 50)*100 = -(-1)*100 = 100. This is wrong.
        # It should be (val - raw_min) / (0 - raw_min) * 100 - 100 for [-100, 0]
        # Or, if mapping to [-100, 0], then for val = raw_min, score = -100. For val = 0, score = 0.
        # (val / abs(raw_min)) * 100 can work if raw_min is the most negative.
        # e.g. raw_min = -50. val = -50 => (-50/50)*100 = -100. val = -25 => (-25/50)*100 = -50. val = 0 => 0.
        return (val / abs(raw_min)) * 100.0 if raw_min < 0 else (-100.0 if val < 0 else 0.0) # Cap if raw_min invalid


def normalize_log_signed(val: float, raw_min: float, raw_max: float) -> float:
    if pd.isna(val):
        return 0.0
    
    # Handle edge cases
    if val == 0:
        return 0.0
    
    if val > 0:
        if raw_max <= 0:
            return 100.0  # Positive value but no positive range
        if raw_max <= 1e-10:  # Very small positive range
            return 100.0 if val > 0 else 0.0
        
        # Ensure raw_max is valid for log1p
        raw_max = max(1e-10, raw_max)
        log_raw_max = math.log1p(raw_max)
        
        if log_raw_max == 0:
            return 100.0 if val > 0 else 0.0
        
        return (math.log1p(val) / log_raw_max) * 100.0
    
    else:  # val < 0
        if raw_min >= 0:
            return -100.0  # Negative value but no negative range
        
        # Make raw_min positive for log1p
        abs_raw_min = abs(raw_min)
        if abs_raw_min <= 1e-10:
            return -100.0 if val < 0 else 0.0
        
        log_abs_raw_min = math.log1p(abs_raw_min)
        
        if log_abs_raw_min == 0:
            return -100.0 if val < 0 else 0.0
        
        return -(math.log1p(abs(val)) / log_abs_raw_min) * 100.0


def calculate_min_max(vals: List[float]): # Added type hint
    # Filter out NaNs before calculating min/max
    valid_vals = [v for v in vals if pd.notna(v)]
    if not valid_vals:
        return -1.0, 1.0 # Default if no valid values

    # Corrected min/max for negative and positive parts
    # Min of negative values (e.g., if vals are [-10, -5, 1, 5], global_min_neg = -10)
    # Max of positive values (e.g., global_max_pos = 5)
    global_min_neg = min((v for v in valid_vals if v < 0), default=0.0) # Default to 0 if no negatives
    global_max_pos = max((v for v in valid_vals if v > 0), default=0.0) # Default to 0 if no positives

    # If all values are positive, global_min_neg will be 0. Need a fallback for scaling negative numbers.
    # If all values are negative, global_max_pos will be 0. Need a fallback for scaling positive numbers.
    # The normalization functions handle raw_min/raw_max being zero appropriately.
    # So, if global_min_neg is 0, it means the "negative range" is effectively [0,0] or non-existent.
    # If global_max_pos is 0, "positive range" is [0,0] or non-existent.

    # If only positive values, use a token negative min for normalization logic (e.g. -1.0)
    # If only negative values, use a token positive max for normalization logic (e.g. 1.0)
    # This ensures that normalize_log_signed and normalize_signed have non-zero ranges if one side is empty.

    final_min = global_min_neg
    if global_min_neg == 0.0 and any(v < 0 for v in valid_vals): # Should not happen if default is 0.0 unless all negatives are 0
        pass # Keep 0 if smallest negative is 0
    elif global_min_neg == 0.0 and not any(v < 0 for v in valid_vals): # No negative numbers
        final_min = -1.0 # Symbolic small negative if no actual negatives

    final_max = global_max_pos
    if global_max_pos == 0.0 and any(v > 0 for v in valid_vals):
        pass
    elif global_max_pos == 0.0 and not any(v > 0 for v in valid_vals): # No positive numbers
        final_max = 1.0 # Symbolic small positive

    return final_min, final_max

def rank_symbols_with_linearity_priority(symbols_data: List[Dict]) -> List[Dict]:
    for symbol_data in symbols_data:
        # Base calculation
        base_score = symbol_data.get("final_score_raw_lt", 0)
        linearity = symbol_data.get("avg_linearity", 0)
        
        # Apply 4x linearity boost
        linearity_boost = 1.0 + (linearity * 3.0)  # 4x total boost for perfect linearity
        boosted_score = base_score * linearity_boost
        
        symbol_data["linearity_boosted_score"] = boosted_score
        symbol_data["linearity_boost_factor"] = linearity_boost
    
    # Sort by linearity-boosted score
    sorted_symbols = sorted(
        symbols_data,
        key=lambda x: x["linearity_boosted_score"],
        reverse=True )
    high_linearity = [s for s in sorted_symbols if s.get("avg_linearity", 0) > 0.6]
    medium_linearity = [s for s in sorted_symbols if 0.4 <= s.get("avg_linearity", 0) <= 0.6]
    low_linearity = [s for s in sorted_symbols if s.get("avg_linearity", 0) < 0.4]
    
    logger.info(f"📈 Linearity-based ranking: {len(high_linearity)} high, {len(medium_linearity)} medium, {len(low_linearity)} low linearity")
    return sorted_symbols

def calculate_smart_score(trend_val: float,linearity: float, volume_score: float,band_score: float,gains: float) -> float:
    linearity_weight = 4.0
    volume_weight = 2.0
    trend_weight = 2.5
    band_weight = 1.0
    gains_weight = 1.5
    linearity_component = linearity * 100 * linearity_weight
    volume_support = 1.0 if volume_score > 0.5 else 0.5
    volume_component = volume_score * 100 * volume_weight * volume_support
    score = (
        (trend_val * trend_weight) +
        linearity_component +
        volume_component +
        (band_score * band_weight) +
        (gains * gains_weight) )
    if linearity > 0.6 and volume_score > 0.4:
        score *= 1.3  
    return score


def compute_wt_composite_for_ranking(sym: str) -> float:
    """Compute multi-TF WaveTrend composite score from RANKING_INFO (Redis data). Backtest: 53.6% WR at 4h, PF 1.45. Dominant predictor for 1D-1M (LT) and 15m-4h (ST)."""
    ri = RANKING_INFO.get(sym, {})
    if not ri:
        return 0.0
    tf_keys = [("wt1_3m", "wt2_3m", 0.5), ("wt1_15m", "wt2_15m", 1.0), ("wt1_1h", "wt2_1h", 2.0), ("wt1_4h", "wt2_4h", 3.0), ("wt1_D", "wt2_D", 2.0)]
    wt_score = 0.0
    for wt1_key, wt2_key, weight in tf_keys:
        wt1_val = ri.get(wt1_key)
        wt2_val = ri.get(wt2_key)
        if wt1_val is not None and wt2_val is not None:
            try:
                wt_score += (float(wt1_val) - float(wt2_val)) * weight
            except (TypeError, ValueError):
                pass
    return wt_score


def calculate_final_scores(trend_val: float, linearity_multiplier: float, price_vs_sma_score: float, band_score: float, breakthrough_bonus: float, weighted_gains: float, proximity_score: float, is_long_term: bool = True, wt_composite: float = 0.0) -> float:
    if is_long_term:
        # BACKTEST_CHANGE_154: LT weights optimized for 1D-1M forward returns
        # Winner: WT+BAND (55% WR at 1D, 61% shorts at 1M) — WT composite dominant
        weights = {
            'trend': 1.0,          # Reduced from 2.5 — trend alone has poor LT predictive value
            'linearity': 2.0,      # Reduced from 4.0 — less dominant
            'price_vs_sma': 0.3,   # Reduced from 0.8
            'band': 1.5,           # RAISED from 0.6 — band position is key for LT mean reversion
            'breakthrough': 0.3,   # Reduced from 0.5
            'gains': 0.5,          # Reduced from 1.0
            'proximity': 1.0,      # RAISED from 0.3 — proximity matters for LT
            'wt_composite': 2.5  } # NEW — WT composite is #1 LT predictor (backtest validated)
        trend_component = trend_val * linearity_multiplier * weights['trend']
        linearity_value = (linearity_multiplier - 1) / 3.2
        linearity_component = linearity_value * 100 * weights['linearity']
        score = (
            trend_component +
            linearity_component +
            price_vs_sma_score * weights['price_vs_sma'] +
            band_score * weights['band'] +
            breakthrough_bonus * weights['breakthrough'] +
            weighted_gains * weights['gains'] +
            proximity_score * weights['proximity'] +
            wt_composite * weights['wt_composite']  )
    else:
        # BACKTEST_CHANGE_155: ST weights optimized for 15m-4h forward returns
        # Winner: WT_DOMINANT (55-58% WR at 1h, 61% shorts at 4h) — WT 3x + trend 1x + band 0.4x + gains 0.6x
        weights = {
            'trend': 1.0,          # Reduced from 1.5
            'linearity': 2.0,      # Reduced from 4.0
            'price_vs_sma': 0.3,   # Reduced from 0.6
            'band': 0.4,           # Reduced from 0.8 — band less useful for ST
            'breakthrough': 0.3,   # Reduced from 0.7
            'gains': 0.6,          # Reduced from 1.2
            'proximity': 0.3,      # Reduced from 0.6
            'wt_composite': 3.0  } # NEW — WT composite is #1 ST predictor (backtest validated)
        trend_component = trend_val * linearity_multiplier * weights['trend']
        linearity_value = (linearity_multiplier - 1) / 4.0
        linearity_component = linearity_value * 100 * weights['linearity']
        score = (
            trend_component +
            linearity_component +
            price_vs_sma_score * weights['price_vs_sma'] +
            band_score * weights['band'] +
            breakthrough_bonus * weights['breakthrough'] +
            weighted_gains * weights['gains'] +
            proximity_score * weights['proximity'] +
            wt_composite * weights['wt_composite']  )
    return score
   
def filter_by_linearity(symbols_data: List[Dict], min_linearity: float = 0.4) -> List[Dict]:
    filtered = []
    for item in symbols_data:
        r_values = item.get("r_values_raw", {})
        if not r_values:
            continue
        avg_linearity = sum(abs(r) for r in r_values.values() if pd.notna(r)) / len(r_values)
        if avg_linearity >= min_linearity:
            linearity_boost = 1.0 + (avg_linearity * 3.0)  # Up to 4x boost
            item["linearity_boost"] = linearity_boost
            item["avg_linearity"] = avg_linearity
            filtered.append(item)
    filtered.sort(
        key=lambda x: x.get("final_score_raw_lt", 0) * x.get("linearity_boost", 1.0),
        reverse=True )
    logger.info(f"📊 Linearity filtering: {len(filtered)}/{len(symbols_data)} symbols with linearity ≥ {min_linearity}")
    return filtered
#########################################

async def initial_fetch_and_ranking(symbols, timeframes=["4h","1h","15m","3m"]):
    """Calculate rankings from klines data only with 4x linearity importance."""
    global RANKING_INFO, to_save_top15_15m, to_save_bottom15_15m, to_save_top20, to_save_bottom20
    global to_save_top30, to_save_bottom30, to_save_top30_r, to_save_bottom30_r, top_100_long_term
    global fs, fsr, prox, bs, symbols_ang_long_list, symbols_ang_short_list, symbols_active
    global symbols_inf_long_list, symbols_inf_short_list, mark_price_cache
    try:
        redis_manager = await get_simple_redis_manager()
        logger.info("✅ Redis manager available for rankings")
    except Exception as e:
        logger.warning(f"⚠️ Redis not available for rankings: {e}")
        redis_manager = None
    await _load_news_sentiment(redis_manager)
    # Clear all global data structures
    RANKING_INFO.clear(); top_100_long_term.clear()
    to_save_top15_15m.clear(); to_save_bottom15_15m.clear()
    to_save_top20.clear(); to_save_bottom20.clear()
    to_save_top30.clear(); to_save_bottom30.clear()
    to_save_top30_r.clear(); to_save_bottom30_r.clear()
    fs.clear(); fsr.clear(); prox.clear(); bs.clear()

    def calculate_trimmed_slope(df, tf):
        if df.empty or "top" not in df.columns or "bottom" not in df.columns or 'timestamp' not in df.columns:
            return 0.0, 0.0, ""
        
        df_calc = df.copy()
        df_calc["timestamp"] = pd.to_datetime(df_calc["timestamp"], errors='coerce')
        df_calc["timestamp"] = df_calc["timestamp"].dt.tz_localize('UTC') if df_calc["timestamp"].dt.tz is None else df_calc["timestamp"].dt.tz_convert('UTC')
        df_calc.dropna(subset=['timestamp'], inplace=True)
        
        if df_calc.empty:
            return 0.0, 0.0, ""
        
        days_visible = {"4h": 66, "1h": 14, "15m": 5, "3m": 1}
        ndays = days_visible.get(tf, 7)
        
        if df_calc.empty or df_calc["timestamp"].max() is pd.NaT:
            return 0.0, 0.0, ""
        
        cutoff = df_calc["timestamp"].max() - pd.Timedelta(days=ndays)
        df_filtered = df_calc[df_calc["timestamp"] >= cutoff].copy()
        
        if df_filtered.empty:
            return 0.0, 0.0, ""
        
        tops = df_filtered.dropna(subset=["top"])
        bots = df_filtered.dropna(subset=["bottom"])
        combined = pd.concat([tops, bots]).sort_values("timestamp")
        combined.dropna(subset=['close', 'timestamp'], inplace=True)
        
        if len(combined) < 2:
            return 0.0, 0.0, ""
        
        combined['timestamp_numeric'] = pd.to_numeric(combined['timestamp'])
        x_time = combined['timestamp_numeric'].values
        y_price = pd.to_numeric(combined["close"].values, errors='coerce')
        
        if np.isnan(y_price).all() or len(y_price) < 2:
            return 0.0, 0.0, ""
        
        avgp = np.nanmean(y_price)
        y_norm = y_price / avgp
        x_min, x_max = x_time.min(), x_time.max()
        
        if x_max == x_min:
            return 0.0, 0.0, ""
        
        x_normalized = (x_time - x_min) / (x_max - x_min)
        
        try:
            slp, icpt, r_val, p_val, std_err = linregress(x_normalized, y_norm)
            return float(slp * 100), float(r_val) if pd.notna(r_val) else 0.0, f"{tf} slp={slp*100:.2f}%"
        except ValueError:
            return 0.0, 0.0, ""

    # Process all symbols
    intermediate_symbol_data = []
    all_dfs_for_returns_calc = {}
    processed_symbols = []
    
    start_time = time.time()
    logger.info(f"🔍 Processing {len(symbols)} symbols with 4x linearity importance...")
    
    # Process symbols in parallel batches
    async def process_single_symbol(sym):
        """Process a single symbol and return its data with 4x linearity emphasis"""
        try:
            symbol_start = time.time()
            dfs_current_sym = {}
            
            for tf in timeframes:
                dftf = await convert_cached_klines_to_analysis_df(sym, tf)
                if dftf.empty:
                    continue
                dftf = detect_tops_bottoms(dftf, distance=5, prominence=1e-4)
                dftf = add_stochrsi_zones(dftf)
                dfs_current_sym[tf] = dftf
            
            if not dfs_current_sym:
                return None
            
            slopes_raw = {}
            r_values_raw = {}
            for tf, dfv in dfs_current_sym.items():
                slp, rvv, _ = calculate_trimmed_slope(dfv, tf)
                slopes_raw[tf] = slp
                r_values_raw[tf] = rvv

            # Calculate initial trend values with 4x linearity consideration
            tf_weights_lt = {"D": 60, "4h": 120, "1h": 200, "15m": 400, "3m": 80}
            tf_weights_st = {"D": 4, "4h": 6, "1h": 25, "15m": 100, "3m": 200}
            
            trend_val_raw_lt = sum(slopes_raw.get(tf, 0.0) * tf_weights_lt.get(tf, 0.0) for tf in slopes_raw.keys())
            trend_val_raw_st = sum(slopes_raw.get(tf, 0.0) * tf_weights_st.get(tf, 0.0) for tf in slopes_raw.keys())
                        
            symbol_time = time.time() - symbol_start
            if symbol_time > 1.0: 
                logger.debug(f"⏱️ {sym} took {symbol_time:.2f}s")
            
            return {
                "symbol": sym,
                "dfs_for_calc": dfs_current_sym,
                "slopes_raw": slopes_raw,
                "r_values_raw": r_values_raw,
                "trend_val_raw_lt": trend_val_raw_lt,
                "trend_val_raw_st": trend_val_raw_st
            }
            
        except Exception as e:
            logger.debug(f"Error processing {sym}: {e}")
            return None
    
    # Process in batches
    batch_size = 31
    logger.info(f"🔄 Processing symbols in batches of {batch_size}...")
    
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        batch_start = time.time()
        
        results = await asyncio.gather(*[process_single_symbol(sym) for sym in batch], return_exceptions=True)
        
        # Collect valid results
        for result in results:
            if result is not None and not isinstance(result, Exception):
                intermediate_symbol_data.append(result)
                processed_symbols.append(result["symbol"])
                all_dfs_for_returns_calc[result["symbol"]] = result["dfs_for_calc"]
        
        batch_time = time.time() - batch_start
        batch_num = (i // batch_size) + 1
        total_batches = (len(symbols) + batch_size - 1) // batch_size
        logger.info(f"✅ Batch {batch_num}/{total_batches} completed in {batch_time:.2f}s ({len(batch)} symbols)")
    
    total_time = time.time() - start_time
    logger.info(f"✅ Processed {len(processed_symbols)} symbols successfully in {total_time:.2f}s ({total_time/len(processed_symbols):.3f}s per symbol)")

    if not intermediate_symbol_data:
        return [], {}
    
    # Calculate returns
    logger.info(f"[ranking] Processing returns for {len(intermediate_symbol_data)} symbols...")
    returns_15m = await calculate_15min_returns_for_symbols(all_dfs_for_returns_calc)
    returns_3m = await calculate_3min_returns_for_symbols(all_dfs_for_returns_calc)
    del all_dfs_for_returns_calc
    
    logger.info(f"[ranking] Returns calculated: 15m={len(returns_15m)}, 3m={len(returns_3m)}")
    
    # Cache prices
    symbols_needing_price = []
    for item in intermediate_symbol_data:
        sym = item["symbol"]
        if sym not in mark_price_cache:
            df_3m = item.get("dfs_for_calc", {}).get("3m")
            if df_3m is not None and not df_3m.empty and "close" in df_3m.columns:
                latest_close = df_3m["close"].iloc[-1]
                mark_price_cache[sym] = {"price": latest_close, "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
            else:
                symbols_needing_price.append(sym)
    
    if symbols_needing_price:
        logger.debug(f"[ranking] Pre-fetching {len(symbols_needing_price)} missing prices...")
        results = await asyncio.gather(*[get_current_price(sym) for sym in symbols_needing_price], return_exceptions=True)
        for sym, result in zip(symbols_needing_price, results):
            if isinstance(result, Exception) or result[0] is None:
                continue
            mark_price_cache[sym] = {"price": result[0], "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}

    # Normalize trends
    all_trend_raw_lt_vals = [item["trend_val_raw_lt"] for item in intermediate_symbol_data if pd.notna(item["trend_val_raw_lt"])]
    all_trend_raw_st_vals = [item["trend_val_raw_st"] for item in intermediate_symbol_data if pd.notna(item["trend_val_raw_st"])]
    
    global_min_trend_lt, global_max_trend_lt = calculate_min_max(all_trend_raw_lt_vals)
    global_min_trend_st, global_max_trend_st = calculate_min_max(all_trend_raw_st_vals)
    
    for item in intermediate_symbol_data:
        item["trend_val_norm_lt"] = normalize_log_signed(item["trend_val_raw_lt"], global_min_trend_lt, global_max_trend_lt)
        item["trend_val_norm_st"] = normalize_log_signed(item["trend_val_raw_st"], global_min_trend_st, global_max_trend_st)
    
    # ------------------------------------------------------------------
    # NEW: Calculate scores with 4x linearity importance
    # ------------------------------------------------------------------
    final_ranking_data_scalars = []
    
    for item in intermediate_symbol_data:
        sym = item["symbol"]
        dfs_calc = item["dfs_for_calc"]
        
        # Get dataframes
        df_3m = dfs_calc.get("3m", pd.DataFrame())
        df_15m = dfs_calc.get("15m", pd.DataFrame())
        df_1h = dfs_calc.get("1h", pd.DataFrame())
        df_4h = dfs_calc.get("4h", pd.DataFrame())
        df_D = dfs_calc.get("D", pd.DataFrame())
        
        # Calculate linearity (R-values)
        valid_r_vals = [r for r in item["r_values_raw"].values() if pd.notna(r)]
        if valid_r_vals:
            lin_val_raw = sum(valid_r_vals) / len(valid_r_vals)
            abs_lin_val_raw = sum(abs(r) for r in valid_r_vals) / len(valid_r_vals)
        else:
            lin_val_raw = 0.0
            abs_lin_val_raw = 0.0
        
        # Calculate relative volume with new smart calculation
        rel_vol_h1 = calculate_relative_volume(df_1h, 50) if df_1h is not None else 1.0
        rel_vol_m15 = calculate_relative_volume(df_15m, 50) if df_15m is not None else 1.0
        rel_vol_m3 = calculate_relative_volume(df_3m, 50) if df_3m is not None else 1.0
        
        # NEW: Smart volume adjustment with gradual scaling
        rel_vol_tot_raw = (rel_vol_h1 * 2 + rel_vol_m15 * 8 + rel_vol_m3 * 14) / 24.0
        rel_vol_tot_raw_r = (rel_vol_h1 * 2 + rel_vol_m15 * 12 + rel_vol_m3 * 24) / 38.0
        
        # Gradual adjustment instead of step function
        rel_vol_tot_norm_factor = 1.0 + (rel_vol_tot_raw - 1.0) * 0.2
        rel_vol_tot_norm_factor = max(0.5, min(2.0, rel_vol_tot_norm_factor))
        
        rel_vol_tot_norm_factor_r = 1.0 + (rel_vol_tot_raw_r - 1.0) * 0.2
        rel_vol_tot_norm_factor_r = max(0.5, min(2.0, rel_vol_tot_norm_factor_r))
        
        # Get current price
        current_price = mark_price_cache.get(sym, {}).get("price") if sym in mark_price_cache else None
        
        # Calculate price vs SMA with new weighting
        price_vs_sma_score = 0.0
        if current_price and df_1h is not None and not df_1h.empty and len(df_1h) >= 200:
            sma_1h = df_1h['close'].rolling(200).mean().iloc[-1] if 'close' in df_1h.columns else None
            if sma_1h and sma_1h > 0:
                price_vs_sma_score = ((current_price - sma_1h) / sma_1h) * 100 * 15
        
        # NEW: Safe band score calculation
        valid_dfs = {tf: df for tf, df in dfs_calc.items() if df is not None and not df.empty}
        band_score = get_band_score_safe(current_price, valid_dfs)
        
        # NEW: Proper proximity score calculation
        weighted_prox_raw = calculate_proximity_score(current_price, valid_dfs)
        
        # Calculate breakthrough bonus
        breakthrough_bonus = 0.0
        if current_price and df_1h is not None and not df_1h.empty:
            atr_1h = calculate_atr(df_1h, period=14)
            
            # Get band boundaries for breakthrough detection
            band_data = calculate_weighted_band_score(current_price, valid_dfs)
            upper_band = band_data.get("upper_band")
            lower_band = band_data.get("lower_band")
            
            # Get proximity range for additional context
            prox_range_df = pd.concat([df_1h, df_4h]) if df_4h is not None else df_1h
            prox_range = get_proximity_range(prox_range_df)
            
            # Bullish breakthrough
            if upper_band and prox_range.get("top") and atr_1h > 0 and current_price > upper_band and current_price > prox_range.get("top"):
                distance_above = current_price - max(upper_band, prox_range.get("top"))
                if distance_above > (atr_1h * 0.75):
                    breakthrough_bonus = (distance_above / atr_1h) * 75
            
            # Bearish breakthrough
            elif lower_band and prox_range.get("bottom") and atr_1h > 0 and current_price < lower_band and current_price < prox_range.get("bottom"):
                distance_below = min(lower_band, prox_range.get("bottom")) - current_price
                if distance_below > (atr_1h * 0.75):
                    breakthrough_bonus = -((distance_below / atr_1h) * 75)
        
        # Calculate weighted gains with new decay factors
        weighted_gains_lt = calculate_weighted_gains(df_4h, 270, decay_factor=0.995) if df_4h is not None else 0.0
        weighted_gains_st = calculate_weighted_gains(df_3m, 480, decay_factor=0.98) if df_3m is not None else 0.0
        
        # Calculate final trend values for scoring (different weights than initial)
        tf_weights_lt_final = {"4h": 80, "1h": 160, "15m": 100, "3m": 60}
        tf_weights_st_final = {"4h": 60, "1h": 120, "15m": 140, "3m": 160}
        
        trend_val_lt = sum(item["slopes_raw"].get(tf, 0.0) * tf_weights_lt_final.get(tf, 0.0) for tf in item["slopes_raw"].keys())
        trend_val_st = sum(item["slopes_raw"].get(tf, 0.0) * tf_weights_st_final.get(tf, 0.0) for tf in item["slopes_raw"].keys())
        
        # ------------------------------------------------------------------
        # 4x LINEARITY IMPORTANCE APPLIED HERE!
        # ------------------------------------------------------------------
        linearity_multiplier_lt = 1 + (3.2 * abs_lin_val_raw)  # 320% boost - 4x more important
        linearity_multiplier_st = 1 + (4.0 * abs_lin_val_raw)  # 400% boost - 4x more important
        
        # BACKTEST_CHANGE_154/155: Compute WT composite from RANKING_INFO (Redis indicators)
        wt_composite_score = compute_wt_composite_for_ranking(sym)
        # Calculate final scores with WT composite (backtest-optimized weights)
        long_term_score_raw = calculate_final_scores(
            trend_val=trend_val_lt,
            linearity_multiplier=linearity_multiplier_lt,
            price_vs_sma_score=price_vs_sma_score,
            band_score=band_score,
            breakthrough_bonus=breakthrough_bonus,
            weighted_gains=weighted_gains_lt,
            proximity_score=weighted_prox_raw,
            is_long_term=True,
            wt_composite=wt_composite_score
        )
        short_term_score_raw = calculate_final_scores(
            trend_val=trend_val_st,
            linearity_multiplier=linearity_multiplier_st,
            price_vs_sma_score=price_vs_sma_score,
            band_score=band_score,
            breakthrough_bonus=breakthrough_bonus,
            weighted_gains=weighted_gains_st,
            proximity_score=weighted_prox_raw,
            is_long_term=False,
            wt_composite=wt_composite_score
        )
        
        # Apply volume adjustment
        final_score_raw_lt = long_term_score_raw * rel_vol_tot_norm_factor
        final_score_raw_st = short_term_score_raw * rel_vol_tot_norm_factor_r
        if config.NEWS_SENTIMENT_ENABLED and _news_sentiment_cache:
            _ns = _news_sentiment_cache.get(sym, 0.0)
            if _ns != 0.0:
                _ns_mult = 1.0 + (_ns * config.NEWS_SENTIMENT_WEIGHT)
                final_score_raw_lt *= _ns_mult
                final_score_raw_st *= _ns_mult
        final_score_raw_lt += 0.2 * min(BACKTEST_SHARPE.get(sym, 0) / 1000, 5.0)  # BACKTEST_CHANGE_49: backtest Sharpe bonus
        final_score_raw_st += 0.2 * min(BACKTEST_SHARPE.get(sym, 0) / 1000, 5.0)  # BACKTEST_CHANGE_49: backtest Sharpe bonus
        # === SCALP_V3 ULTRA-SHORT BOOST (2026-04-22) — undoable by SCALP_V3_BOOST_ENABLED=False ===
        # Boosts final_score_raw_st on last N × 3m bars of outperformance + volume spike.
        # Signed: positive return → pushes into top_winners_st → symbols_inf_long_list;
        #         negative return → pushes into top_losers_st → symbols_inf_short_list.
        # These lists are already saved to symbols_inf_long/short.json and picked up by
        # ez_positions_service which flows them into tradeable_keys automatically.
        # 2026-04-23 evening: also compute _ret_15m unconditionally so the post-loop
        # outlier detector can re-boost for "RECENT WINNERS / LOSERS vs market median"
        # — this is what routes THETA/ZEC/COMP-style divergers into symbols_inf_long
        # regardless of volume spike.
        _v3_ret_15m = 0.0
        if df_3m is not None and not df_3m.empty and len(df_3m) >= 6 and 'close' in df_3m.columns:
            try:
                _closes_v3 = df_3m['close'].values
                _n_v3 = 5   # 5 bars × 3m = 15 min
                if len(_closes_v3) > _n_v3 and _closes_v3[-_n_v3 - 1] > 0:
                    _v3_ret_15m = (_closes_v3[-1] / _closes_v3[-_n_v3 - 1] - 1.0) * 100.0
            except Exception:
                pass
        if getattr(config, 'SCALP_V3_BOOST_ENABLED', False) and df_3m is not None and not df_3m.empty:
            try:
                _n = int(getattr(config, 'SCALP_V3_BOOST_LOOKBACK_BARS_3M', 5))
                _wt = float(getattr(config, 'SCALP_V3_BOOST_WEIGHT', 0.0))
                _vol_z_min = float(getattr(config, 'SCALP_V3_BOOST_VOL_Z_MIN', 1.5))
                if len(df_3m) >= 20 and _wt != 0.0 and 'close' in df_3m.columns and 'volume' in df_3m.columns:
                    _vols = df_3m['volume'].values
                    _vol_recent = _vols[-_n:].mean() if _n > 0 else 0.0
                    _vol_baseline = _vols[-20:-_n].mean() if (_n < 20 and _n > 0) else _vols[-20:].mean()
                    _vol_ratio = _vol_recent / _vol_baseline if _vol_baseline > 0 else 1.0
                    if _vol_ratio >= _vol_z_min and abs(_v3_ret_15m) > 0.5:
                        final_score_raw_st += _wt * _v3_ret_15m
            except Exception:
                pass
        # === END SCALP_V3 BOOST ===
        # Add linearity metadata for filtering/ranking
        avg_linearity = abs_lin_val_raw
        linearity_category = (
            "HIGH" if avg_linearity > 0.7 else
            "MEDIUM" if avg_linearity > 0.5 else
            "LOW"
        )
        
        # Store in final ranking data
        final_ranking_data_scalars.append({
            "symbol": sym,
            "slopes_raw": item['slopes_raw'],
            "r_values_raw": item['r_values_raw'],
            "trend_val_norm_lt": item["trend_val_norm_lt"],
            "trend_val_norm_st": item["trend_val_norm_st"],
            "linearity_raw": lin_val_raw,
            "abs_linearity_raw": abs_lin_val_raw,
            "avg_linearity": avg_linearity,
            "linearity_category": linearity_category,
            "rel_vol_raw": rel_vol_tot_raw,
            "rel_vol_norm_factor": rel_vol_tot_norm_factor,
            "final_score_raw_lt": final_score_raw_lt,
            "final_score_raw_st": final_score_raw_st,
            "weighted_proximity_score_raw": weighted_prox_raw,
            "band_score": band_score,
            "weighted_gains_lt": weighted_gains_lt,
            "weighted_gains_st": weighted_gains_st,
            "_v3_ret_15m": _v3_ret_15m,
            "dfs_for_calc": dfs_calc
        })

    if not final_ranking_data_scalars:
        return [], {}

    # === SCALP_V3 OUTLIER SCORING (2026-04-23 evening) =============================
    # User directive: "add outliers vs avg index at the end" — DON'T change the
    # regular calculate_final_scores calc. Compute per-symbol z-score on 15-min
    # price return vs market median and ATTACH it to the entry. The injection into
    # symbols_inf_long/short_list happens AT THE END (after lists are built).
    try:
        if getattr(config, 'SCALP_V3_OUTLIER_ENABLED', True):
            import statistics as _stats
            _rets = [e.get("_v3_ret_15m", 0.0) for e in final_ranking_data_scalars if e.get("_v3_ret_15m") is not None]
            if len(_rets) >= 10:
                _median_ret = _stats.median(_rets)
                _abs_devs = sorted(abs(r - _median_ret) for r in _rets)
                _mad = _abs_devs[len(_abs_devs) // 2] if _abs_devs else 0.0
                _mad = max(_mad, 0.05)
                for _e in final_ranking_data_scalars:
                    _ret = _e.get("_v3_ret_15m", 0.0) or 0.0
                    _e["_v3_outlier_z"] = (_ret - _median_ret) / _mad
    except Exception as _v3ol_err:
        logger.warning(f"[SCALP_V3_OUTLIER] scoring error: {_v3ol_err}")
    # === END SCALP_V3 OUTLIER SCORING ==============================================

    # Normalize final scores
    all_scores_combined = [entry["final_score_raw_lt"] for entry in final_ranking_data_scalars] + [entry["final_score_raw_st"] for entry in final_ranking_data_scalars]
    global_min_score, global_max_score = calculate_min_max(all_scores_combined)
    
    for entry in final_ranking_data_scalars:
        sym = entry["symbol"]
        entry["final_score_norm"] = normalize_log_signed(entry["final_score_raw_lt"], global_min_score, global_max_score)
        entry["final_score_recent_norm"] = normalize_log_signed(entry["final_score_raw_st"], global_min_score, global_max_score)
        entry["proximity_score_norm"] = normalize_log_signed(entry["weighted_proximity_score_raw"], -100, 100) if entry["weighted_proximity_score_raw"] else 0.0
        
        # Store in global dictionaries
        fs[sym] = entry["final_score_norm"]
        fsr[sym] = entry["final_score_recent_norm"]
        prox[sym] = entry["proximity_score_norm"]
        bs[sym] = entry["band_score"]

    # ------------------------------------------------------------------
    # Sort and create ranked lists with 4x linearity emphasis
    # ------------------------------------------------------------------
    logger.info("📊 Sorting symbols with 4x linearity emphasis...")
    
    # First, filter by linearity to prioritize predictable trends
    filtered_by_linearity = filter_by_linearity(final_ranking_data_scalars, min_linearity=0.4)
    
    # Then rank with linearity priority
    ranked_symbols = rank_symbols_with_linearity_priority(filtered_by_linearity)
    
    # ═══ HTF TREND FILTER: winners must be in uptrend, losers in downtrend ═══
    # A symbol below SMA200_D CANNOT be a "winner" (LONG candidate)
    # A symbol above SMA200_D CANNOT be a "loser" (SHORT candidate)
    try:
        # Use in-memory indicators_data (loaded from Redis) — NOT the stale JSON file
        if indicators_data and isinstance(indicators_data, dict):
            _lmd = indicators_data
            _bearish_syms = set()
            _bullish_syms = set()
            for _sym, _sd in _lmd.items():
                _p = float(_sd.get('current_price', 0) or 0)
                _sma = float(_sd.get('sma_200_D', 0) or 0)
                if _p > 0 and _sma > 0:
                    if _p < _sma * 0.99:
                        _bearish_syms.add(_sym)
                    elif _p > _sma * 1.01:
                        _bullish_syms.add(_sym)
            # SMA200_D filter REMOVED — was blocking mean-reversion entries at bottoms
            # Alignment gate in execute_now() is the ONLY entry filter now
            _winner_candidates = ranked_symbols
            _loser_candidates = ranked_symbols
            logger.info(f"[HTF_RANK_FILTER] DISABLED — alignment gate handles entry filtering")
        else:
            _winner_candidates = ranked_symbols
            _loser_candidates = ranked_symbols
    except Exception as _e:
        logger.debug(f"[HTF_RANK_FILTER] Error: {_e}")
        _winner_candidates = ranked_symbols
        _loser_candidates = ranked_symbols

    # Extract top winners and losers (with HTF filter applied)
    top_winners_lt = sorted(_winner_candidates, key=lambda x: x["linearity_boosted_score"], reverse=True)
    top_losers_lt = sorted(_loser_candidates, key=lambda x: x["linearity_boosted_score"])
    
    # Short-term ranking (using recent scores with linearity boost)
    top_winners_st = sorted(ranked_symbols, key=lambda x: x.get("final_score_recent_norm", 0) * x.get("linearity_boost_factor", 1), reverse=True)
    top_losers_st = sorted(ranked_symbols, key=lambda x: x.get("final_score_recent_norm", 0) * x.get("linearity_boost_factor", 1))
    
    # Save top/bottom 30 (long term)
    to_save_top30[:] = [{"symbol": e["symbol"], "score": e["final_score_norm"], "linearity": e.get("avg_linearity", 0)} for e in top_winners_lt[:30]]
    to_save_bottom30[:] = [{"symbol": e["symbol"], "score": e["final_score_norm"], "linearity": e.get("avg_linearity", 0)} for e in top_losers_lt[:30]]
    
    # Save top/bottom 30 recent
    to_save_top30_r[:] = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"], "linearity": e.get("avg_linearity", 0)} for e in top_winners_st[:20]]
    to_save_bottom30_r[:] = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"], "linearity": e.get("avg_linearity", 0)} for e in top_losers_st[:20]]
    
    # Save top/bottom 20 (long term)
    to_save_top20[:] = to_save_top30[:20]
    to_save_bottom20[:] = [{"symbol": e["symbol"], "score": e["final_score_norm"], "linearity": e.get("avg_linearity", 0)} for e in top_losers_lt[:20]]
    
    # Build top 100 long-term list
    # 2026-04-16: cap size via config.LT_TOP_SIZE, log every refresh for verification + downstream consumers.
    try:
        _lt_cap = int(getattr(Config(), 'LT_TOP_SIZE', 100))
        _st_lt_enabled = bool(getattr(Config(), 'ST_LT_SPLIT_ENABLED', True))
    except Exception:
        _lt_cap = 100
        _st_lt_enabled = True
    top_100_long_term.extend([item["symbol"] for item in top_winners_lt[:_lt_cap]])
    if _st_lt_enabled:
        try:
            logger.info(f"[ST_LT_SPLIT] top_100_long_term populated size={len(top_100_long_term)} cap={_lt_cap} first10={top_100_long_term[:10]}")
        except Exception: pass
    
    # Merge returns data
    def merge_filter(ret15_dict, ret3_dict, top_100_set, cond15_func, cond3_func):
        merged = {}
        for sym_candidate in top_100_set:
            val_15 = ret15_dict.get(sym_candidate)
            val_3 = ret3_dict.get(sym_candidate)
            meets_cond15 = pd.notna(val_15) and cond15_func(val_15)
            meets_cond3 = pd.notna(val_3) and cond3_func(val_3)
            if meets_cond15 or meets_cond3:
                merged[sym_candidate] = {"returns_15m": val_15, "returns_3m": val_3}
        return merged
        
    # 2026-04-27 USER BUG FIX (v2): top_15_15m must find ACTUAL recent gainers across ALL symbols.
    # Two issues fixed:
    #   (1) Old code pre-filtered by LT-top-100 → strong-15m setups with poor LT score (e.g. WIFUSDC LT=-85, wt_percentile_15m=93.5) were excluded.
    #   (2) Single-bar 15m return penalizes mid-rally pullback bars — a symbol that rallied +3% over 1h but the latest 15m bar went -0.4% gets sorted to the bottom.
    # New scoring: gain_score = max(returns_15m, 4*returns_3m) blended with wt_score_15m as a momentum proxy.
    # This catches both fresh 15m breakouts AND ongoing rallies regardless of latest single-bar noise.
    _ranking_by_sym = {e["symbol"]: e for e in final_ranking_data_scalars}
    _all_15m_syms = set(returns_15m.keys()) | set(returns_3m.keys())
    def _gain_score(sym):
        v15 = returns_15m.get(sym)
        v3 = returns_3m.get(sym)
        v15_f = float(v15) if v15 is not None and pd.notna(v15) else 0.0
        v3_f = float(v3) if v3 is not None and pd.notna(v3) else 0.0
        # 4× 3m return ≈ 1h estimate (4 bars). Take MAX of the two — captures whichever timeframe shows momentum.
        ret_max = max(v15_f, v3_f * 4.0)
        # wt_score_15m component: positive = bullish momentum, negative = bearish. Scale ~0.1 to balance with %-returns.
        wt_score = 0.0
        e = _ranking_by_sym.get(sym, {})
        try:
            wt_score = float(e.get("indicators_data", {}).get("wt_score_15m", 0)) * 0.1
        except Exception: pass
        return ret_max + wt_score
    _scored = [(s, _gain_score(s), returns_15m.get(s), returns_3m.get(s)) for s in _all_15m_syms]
    _scored.sort(key=lambda x: -x[1])
    _sort_top = _scored[:30]
    _sort_bot = sorted(_scored, key=lambda x: x[1])[:30]
    to_save_top15_15m[:] = [{"symbol": s, "returns_15m": r15, "returns_3m": r3, "gain_score": round(sc, 3)} for s, sc, r15, r3 in _sort_top]
    to_save_bottom15_15m[:] = [{"symbol": s, "returns_15m": r15, "returns_3m": r3, "gain_score": round(sc, 3)} for s, sc, r15, r3 in _sort_bot]
    try:
        _wif_t = next((i+1 for i,(s,_,_,_) in enumerate(_sort_top) if s=='WIFUSDC'), None)
        _wif_b = next((i+1 for i,(s,_,_,_) in enumerate(_sort_bot) if s=='WIFUSDC'), None)
        logger.info(f"🎯 [TOP15_15M_FIX] universe={len(_all_15m_syms)} top5={[(s,round(sc,3)) for s,sc,_,_ in _sort_top[:5]]} bot5={[(s,round(sc,3)) for s,sc,_,_ in _sort_bot[:5]]} WIFUSDC_top={_wif_t} WIFUSDC_bot={_wif_b}")
    except Exception as _e:
        logger.warning(f"[TOP15_15M_FIX] log error: {_e}")

    # ------------------------------------------------------------------
    # Build symbol lists with ranking_points.json enhancement
    # ------------------------------------------------------------------
    symbols_ang_long_list = [item["symbol"] for item in to_save_top30]
    symbols_ang_short_list = [item["symbol"] for item in to_save_bottom30]
    
    # Add ranking_points.json data if available — WITH HTF TREND FILTER
    extra_long = []
    extra_short = []
    try:
        with open("data/ranking_points.json", "r") as f: 
            ranking_data = json.load(f)
        sorted_ranking = sorted(ranking_data.items(), key=lambda x: x[1], reverse=True)
        # FILTER: only allow longs if NOT in daily bearish set, shorts if NOT in daily bullish set
        extra_long = [{"symbol": symbol, "score": score} for symbol, score in sorted_ranking[:40] if symbol not in _bearish_syms][:20]
        extra_short = [{"symbol": symbol, "score": score} for symbol, score in sorted_ranking[-40:] if symbol not in _bullish_syms][:20]
        logger.info(f"[HTF_RANK_FILTER] ranking_points.json: {len(extra_long)} longs passed HTF, {len(extra_short)} shorts passed HTF")
    except Exception as e: 
        logger.warning(f"Failed to load ranking_points.json: {e}")
    
    def extend_unique(base_list, new_items):
        existing_syms = {i["symbol"] for i in base_list}
        for item in new_items:
            if item["symbol"] not in existing_syms:
                base_list.append(item)
                existing_syms.add(item["symbol"])
        return base_list
    
    to_save_top30 = extend_unique(to_save_top30, extra_long)
    to_save_bottom30 = extend_unique(to_save_bottom30, extra_short)

    symbols_ang_long_list = [item["symbol"] for item in to_save_top30]
    symbols_ang_short_list = [item["symbol"] for item in to_save_bottom30]

    # Trim to desired length
    symbols_ang_long_list = symbols_ang_long_list[:20]
    symbols_ang_short_list = symbols_ang_short_list[:20]

    # Add ranking_points.json to final scores — WITH HTF TREND FILTER
    try:
        with open("data/ranking_points.json", "r") as f:
            ranking_data = json.load(f)
        sorted_ranking = sorted(ranking_data.items(), key=lambda x: x[1], reverse=True)
        symbols_ang_long_list.extend([symbol for symbol, score in sorted_ranking[:40] if symbol not in _bearish_syms][:20])
        symbols_ang_short_list.extend([symbol for symbol, score in sorted_ranking[-40:] if symbol not in _bullish_syms][:20])
    except Exception as e:
        logger.warning(f"Failed to load ranking_points.json: {e}")
    
    # Remove duplicates and ensure 20 symbols each
    symbols_ang_long_list = list(dict.fromkeys(symbols_ang_long_list))[:20]
    symbols_ang_short_list = list(dict.fromkeys(symbols_ang_short_list))[:20]
    # Tier-based priority: A-tier symbols get sorted to front, C-tier to back
    if config.TIER_ENABLED:
        try:
            from utils import get_symbol_tier
            _tier_order = {'A': 0, 'B': 1, 'C': 2}
            symbols_ang_long_list.sort(key=lambda s: _tier_order.get(get_symbol_tier(s, 'B'), 1))
            symbols_ang_short_list.sort(key=lambda s: _tier_order.get(get_symbol_tier(s, 'B'), 1))
        except Exception:
            pass
    
    if len(symbols_ang_long_list) < 20:
        symbols_ang_long_list.extend([item["symbol"] for item in to_save_top30[len(symbols_ang_long_list):]])
    if len(symbols_ang_short_list) < 20:
        symbols_ang_short_list.extend([item["symbol"] for item in to_save_bottom30[len(symbols_ang_short_list):]]) 
    
    # Build short-term/inflection lists — WITH HTF TREND FILTER
    symbols_inf_long_list = [item["symbol"] for item in to_save_top15_15m if item["symbol"] not in _bearish_syms] + \
                            [item["symbol"] for item in to_save_top30_r if item["symbol"] not in _bearish_syms]
    symbols_inf_short_list = [item["symbol"] for item in to_save_bottom15_15m if item["symbol"] not in _bullish_syms] + \
                             [item["symbol"] for item in to_save_bottom30_r if item["symbol"] not in _bullish_syms]

    # 2026-05-08 USER MANDATE: 24h-gain-leader injection.
    # Pipeline above is myopic — only catches LAST-15-MINUTE bursts (e.g. AIA -91 fsr in via gain_score)
    # while symbols pumping +5-10% over 24h but flat in last 15min are EXCLUDED (e.g. ETC fsr=84 omitted).
    # Inject top-20/bottom-20 by `weighted_gains_st` (24h decay-weighted return) so sustained pumps qualify.
    try:
        _wgs_sorted = sorted(final_ranking_data_scalars, key=lambda e: float(e.get("weighted_gains_st", 0) or 0), reverse=True)
        _wgs_top20 = _wgs_sorted[:20]
        _wgs_bot20 = _wgs_sorted[-20:]
        for _e in _wgs_top20:
            _s = _e.get("symbol")
            if _s and _s not in symbols_inf_long_list and _s not in _bearish_syms:
                symbols_inf_long_list.append(_s)
        for _e in _wgs_bot20:
            _s = _e.get("symbol")
            if _s and _s not in symbols_inf_short_list and _s not in _bullish_syms:
                symbols_inf_short_list.append(_s)
        # Same for ang lists — they also miss 24h gainers
        for _e in _wgs_top20:
            _s = _e.get("symbol")
            if _s and _s not in symbols_ang_long_list and _s not in _bearish_syms:
                symbols_ang_long_list.append(_s)
        for _e in _wgs_bot20:
            _s = _e.get("symbol")
            if _s and _s not in symbols_ang_short_list and _s not in _bullish_syms:
                symbols_ang_short_list.append(_s)
        logger.info(f"[WGS_24H_INJECT] top20={[e.get('symbol') for e in _wgs_top20[:8]]}... bot20={[e.get('symbol') for e in _wgs_bot20[:8]]}...")
    except Exception as _wgs_e:
        logger.warning(f"[WGS_24H_INJECT] failed: {_wgs_e}")

    # === SCALP_V3 OUTLIER INJECTION (2026-04-23 evening) ===========================
    # User directive: inject RECENT winners/losers (vs market-wide 15-min median return)
    # into symbols_inf_long/short_list so ez_positions_service auto-adds them to
    # tradeable_keys. This captures chart-visible outliers (THETA, ZEC, COMP, DOT, CRV
    # style) regardless of whether the regular ranking picks them up. Does NOT modify
    # the existing calculate_final_scores path — pure append.
    try:
        if getattr(config, 'SCALP_V3_OUTLIER_ENABLED', True):
            _min_z_long = float(getattr(config, 'SCALP_V3_OUTLIER_MIN_Z_LONG', 1.5))
            _min_z_short = float(getattr(config, 'SCALP_V3_OUTLIER_MIN_Z_SHORT', -1.5))
            _max_inject = int(getattr(config, 'SCALP_V3_OUTLIER_MAX_INJECT', 15))
            _long_outliers = sorted(
                [e for e in final_ranking_data_scalars if e.get("_v3_outlier_z", 0) >= _min_z_long],
                key=lambda x: x.get("_v3_outlier_z", 0), reverse=True
            )[:_max_inject]
            _short_outliers = sorted(
                [e for e in final_ranking_data_scalars if e.get("_v3_outlier_z", 0) <= _min_z_short],
                key=lambda x: x.get("_v3_outlier_z", 0)
            )[:_max_inject]
            _lo_syms = [e["symbol"] for e in _long_outliers if e["symbol"] not in _bearish_syms]
            _so_syms = [e["symbol"] for e in _short_outliers if e["symbol"] not in _bullish_syms]
            for _s in _lo_syms:
                if _s not in symbols_inf_long_list:
                    symbols_inf_long_list.append(_s)
            for _s in _so_syms:
                if _s not in symbols_inf_short_list:
                    symbols_inf_short_list.append(_s)
            if _lo_syms or _so_syms:
                logger.info(f"🎯 [SCALP_V3_OUTLIER_INJECT] +LONG {len(_lo_syms)}: {_lo_syms[:8]} | +SHORT {len(_so_syms)}: {_so_syms[:8]}")
    except Exception as _v3inj_err:
        logger.warning(f"[SCALP_V3_OUTLIER_INJECT] error: {_v3inj_err}")
    # === END SCALP_V3 OUTLIER INJECTION ============================================

    # === SCALP_V3 REENTRY STICKY INJECT (2026-04-26) ================================
    # Fix for V3 reentry pipeline gap (0/345 same-key reentries observed):
    # ez_positions_quick writes Redis key v3_recent:{SYM}_{SIDE} with TTL on every V3
    # entry/exit. Here we read those keys back and re-inject the symbols so they stay
    # in the inf universe for the sticky window even if outlier z-score has decayed.
    try:
        if getattr(config, 'SCALP_V3_REENTRY_STICKY_ENABLED', True):
            _v3rs_long = []; _v3rs_short = []
            try:
                if redis is not None:
                    _v3rs_keys = await redis.keys("v3_recent:*")
                    for _k in _v3rs_keys or []:
                        _kp = _k.split(":", 1)[1] if ":" in _k else _k
                        if _kp.endswith("_LONG"):
                            _v3rs_long.append(_kp[:-5])
                        elif _kp.endswith("_SHORT"):
                            _v3rs_short.append(_kp[:-6])
            except Exception as _rdx_e:
                logger.debug(f"[SCALP_V3_REENTRY_STICKY] redis keys scan failed: {_rdx_e}")
            for _s in _v3rs_long:
                if _s not in symbols_inf_long_list:
                    symbols_inf_long_list.append(_s)
            for _s in _v3rs_short:
                if _s not in symbols_inf_short_list:
                    symbols_inf_short_list.append(_s)
            if _v3rs_long or _v3rs_short:
                logger.info(f"🔁 [SCALP_V3_REENTRY_STICKY_INJECT] +LONG {len(_v3rs_long)}: {_v3rs_long[:8]} | +SHORT {len(_v3rs_short)}: {_v3rs_short[:8]}")
    except Exception as _v3rs_err:
        logger.warning(f"[SCALP_V3_REENTRY_STICKY] error: {_v3rs_err}")
    # === END SCALP_V3 REENTRY STICKY INJECT =========================================

    # === FUNDING + OI EXTREME-OUTLIER INJECTION (2026-04-27 user directive) ============
    # Reads funding_rate + oi_change_1h_pct + price_change_1h from Redis hot_metrics:{sym}
    # (populated by ez_market_data funding_rate_loop / open_interest_loop) and injects
    # symbols with extreme/conviction readings into the inf long/short universe.
    #
    # FUNDING: stretched funding fades the overcrowded side
    #   funding > +FUNDING_INJECT_LONG_MIN  → SHORT injection (longs overcrowded, will revert)
    #   funding < -FUNDING_INJECT_SHORT_MIN → LONG injection (shorts overcrowded)
    # OI×PRICE 4-quadrant (Schabacker classic):
    #   price↑ + OI↑ = new long conviction → LONG injection
    #   price↓ + OI↑ = new short conviction → SHORT injection
    #   (squeeze/liquidation quadrants intentionally not injected — they're fades, not entries)
    try:
        if getattr(config, 'FUNDING_OI_INJECT_ENABLED', True) and redis is not None:
            _fr_long_min = float(getattr(config, 'FUNDING_INJECT_SHORT_OVERCROWDED_BELOW', -0.0008))  # funding ≤ -0.08%
            _fr_short_min = float(getattr(config, 'FUNDING_INJECT_LONG_OVERCROWDED_ABOVE', 0.0008))  # funding ≥ +0.08%
            _oi_min_pct = float(getattr(config, 'FUNDING_OI_INJECT_OI_MIN_PCT', 1.0))  # |oi_chg| ≥ 1.0%
            _px_min_pct = float(getattr(config, 'FUNDING_OI_INJECT_PRICE_MIN_PCT', 0.5))  # |price_chg| ≥ 0.5%
            _max_each = int(getattr(config, 'FUNDING_OI_INJECT_MAX_EACH', 10))
            _foi_long = []  # (sym, reason)
            _foi_short = []
            _candidate_syms = list({e["symbol"] for e in final_ranking_data_scalars})
            for _sym in _candidate_syms:
                try:
                    _hm = await redis.get(f"hot_metrics:{_sym}")
                    if not _hm: continue
                    _hm_d = json_loads(_hm)
                    if not isinstance(_hm_d, dict): continue
                    _fr = _hm_d.get('funding_rate')
                    _oi_chg = _hm_d.get('oi_change_1h_pct')
                    _px_now = _hm_d.get('current_price') or _hm_d.get('close')
                    _px_1h = _hm_d.get('close_1h_prev') or _hm_d.get('close_1h')
                    _px_chg = ((float(_px_now) - float(_px_1h)) / float(_px_1h) * 100.0) if (_px_now and _px_1h and float(_px_1h) > 0) else None
                    _fr = float(_fr) if _fr is not None else None
                    _oi_chg = float(_oi_chg) if _oi_chg is not None else None
                    # Funding-extreme injection (fade the overcrowded side)
                    if _fr is not None:
                        if _fr <= _fr_long_min and _sym not in _bullish_syms:
                            _foi_long.append((_sym, f"funding={_fr*100:.3f}%_shorts_overcrowded"))
                        elif _fr >= _fr_short_min and _sym not in _bearish_syms:
                            _foi_short.append((_sym, f"funding={_fr*100:.3f}%_longs_overcrowded"))
                    # OI×price 4-quadrant injection (CONVICTION quadrants only)
                    if _oi_chg is not None and _px_chg is not None and abs(_oi_chg) >= _oi_min_pct and abs(_px_chg) >= _px_min_pct:
                        _px_up = _px_chg > 0; _oi_up = _oi_chg > 0
                        if _px_up and _oi_up and _sym not in _bearish_syms:
                            _foi_long.append((_sym, f"OI↑{_oi_chg:+.2f}%×px↑{_px_chg:+.2f}%_new_longs"))
                        elif (not _px_up) and _oi_up and _sym not in _bullish_syms:
                            _foi_short.append((_sym, f"OI↑{_oi_chg:+.2f}%×px↓{_px_chg:+.2f}%_new_shorts"))
                except Exception:
                    continue
            # Dedup by symbol (keep first reason if both funding+OI hit), then cap
            _seen_l = set(); _foi_long_d = []
            for _s, _r in _foi_long:
                if _s not in _seen_l:
                    _seen_l.add(_s); _foi_long_d.append((_s, _r))
            _seen_s = set(); _foi_short_d = []
            for _s, _r in _foi_short:
                if _s not in _seen_s:
                    _seen_s.add(_s); _foi_short_d.append((_s, _r))
            _foi_long_d = _foi_long_d[:_max_each]
            _foi_short_d = _foi_short_d[:_max_each]
            for _s, _ in _foi_long_d:
                if _s not in symbols_inf_long_list: symbols_inf_long_list.append(_s)
            for _s, _ in _foi_short_d:
                if _s not in symbols_inf_short_list: symbols_inf_short_list.append(_s)
            if _foi_long_d or _foi_short_d:
                logger.info(f"💰 [FUNDING_OI_INJECT] +LONG {len(_foi_long_d)}: {[(s,r) for s,r in _foi_long_d[:5]]} | +SHORT {len(_foi_short_d)}: {[(s,r) for s,r in _foi_short_d[:5]]}")
    except Exception as _foi_err:
        logger.warning(f"[FUNDING_OI_INJECT] error: {_foi_err}")
    # === END FUNDING + OI INJECTION ====================================================

    # === TRENDER + BREAKOUT INJECTION (2026-05-09 user mandate) ========================
    # Two new injectors that capture symbols missed by velocity-only funnels:
    #   TRENDER_INJECT  — sustained + linear + size-credible grinders (clean +5% over 24h on a $100M coin)
    #   BREAKOUT_INJECT — symbols whose 4h return outliers from cross-sectional cluster
    #                     (the ZEC/TON-style band-breakers the user actually trades)
    # Both default to SHADOW_LOG: candidates are written to data/inject_shadow_log.jsonl
    # WITHOUT modifying symbols_inf_long/short_list. Flip *_LIVE=True after validation.
    # NO top-N cap — a quiet market may inject 0; a moving market may inject 30+.
    try:
        import math as _math
        import statistics as _stats2
        from utils import get_symbol_tier as _gst
        _ts_now = int(time.time())
        _shadow_path = BASE_PATH / "data" / "inject_shadow_log.jsonl"
        _shadow_rows = []
        _tier_mult = {'A': 1.5, 'B': 1.2, 'C': 1.0, 'D': 0.7}
        # Pre-compute per-symbol horizon returns + 24h DD + 24h quote-volume USD from dfs_for_calc
        # so both injectors can share the same numbers.
        _horizons = {}  # sym -> {ret_1h, ret_4h, ret_24h, dd_24h_pct, qv_24h_usd}
        for _e in final_ranking_data_scalars:
            _sym = _e.get("symbol")
            _dfs = _e.get("dfs_for_calc") or {}
            _df15 = _dfs.get("15m"); _df1h = _dfs.get("1h")
            if _sym is None or _df15 is None or _df1h is None: continue
            try:
                if _df15.empty or 'close' not in _df15.columns: continue
                if _df1h.empty or 'close' not in _df1h.columns: continue
                _c15 = _df15['close'].astype(float)
                _c1h = _df1h['close'].astype(float)
                if len(_c15) < 17 or len(_c1h) < 25: continue
                _last = float(_c15.iloc[-1])
                _ret_1h = (_last / float(_c15.iloc[-5]) - 1.0) * 100.0   # 4×15m back
                _ret_4h = (_last / float(_c15.iloc[-17]) - 1.0) * 100.0  # 16×15m back
                _ret_24h = (_last / float(_c1h.iloc[-25]) - 1.0) * 100.0
                _peak_24h = float(_c1h.iloc[-25:].max())
                _trough_24h = float(_c1h.iloc[-25:].min())
                _dd_long_pct = ((_peak_24h - _last) / _peak_24h * 100.0) if _peak_24h > 0 else 0.0   # peak→now drawdown (LONG side)
                _dd_short_pct = ((_last - _trough_24h) / _trough_24h * 100.0) if _trough_24h > 0 else 0.0  # trough→now rally (SHORT side)
                _qv_usd = float((_df1h['volume'].astype(float).iloc[-24:] * _c1h.iloc[-24:]).sum()) if 'volume' in _df1h.columns else 0.0
                _horizons[_sym] = {"ret_1h": _ret_1h, "ret_4h": _ret_4h, "ret_24h": _ret_24h, "dd_long_pct": _dd_long_pct, "dd_short_pct": _dd_short_pct, "qv_24h_usd": _qv_usd}
            except Exception:
                continue

        # ---- TRENDER ----
        if getattr(config, 'TRENDER_INJECT_SHADOW_LOG', True) or getattr(config, 'TRENDER_INJECT_LIVE', False):
            _lin_min = float(getattr(config, 'TRENDER_LIN_MIN', 0.55))
            _ret24_min = float(getattr(config, 'TRENDER_RET24_MIN_PCT', 1.5))
            _qv_floor = float(getattr(config, 'TRENDER_QV_FLOOR_USD', 25_000_000))
            _qv_anchor = float(getattr(config, 'TRENDER_QV_ANCHOR_USD', 50_000_000))
            _qv_max_boost = float(getattr(config, 'TRENDER_QV_MAX_BOOST', 1.5))
            _dd_ratio_max = float(getattr(config, 'TRENDER_DD_RATIO_MAX', 0.40))
            _trender_long = []; _trender_short = []
            for _e in final_ranking_data_scalars:
                _sym = _e.get("symbol")
                _h = _horizons.get(_sym)
                if not _sym or not _h: continue
                _r = _e.get("r_values_raw") or {}
                _vals = [abs(v) for v in _r.values() if pd.notna(v)]
                _lin = (sum(_vals) / len(_vals)) if _vals else float(_e.get("avg_linearity", 0.0) or 0.0)
                if _lin < _lin_min: continue
                if _h["qv_24h_usd"] < _qv_floor: continue
                _qv_boost = max(0.0, min(_qv_max_boost, _math.log10(max(_h["qv_24h_usd"], 1.0) / _qv_anchor)))
                try:
                    _tier = _gst(_sym, default='B') or 'B'
                except Exception:
                    _tier = 'B'
                _tm = _tier_mult.get(_tier, 1.0)
                # LONG side: all three windows up + cumulative ≥ floor + drawdown contained
                if (_h["ret_24h"] >= _ret24_min and _h["ret_4h"] >= 0 and _h["ret_1h"] >= 0):
                    _ddr = _h["dd_long_pct"] / max(_h["ret_24h"], 0.01)
                    if _ddr <= _dd_ratio_max:
                        _stab = max(0.0, 1.0 - _ddr)
                        _score = _h["ret_24h"] * (_lin ** 2) * _qv_boost * _tm * _stab
                        _trender_long.append({"symbol": _sym, "score": round(_score, 4), "ret_24h": round(_h["ret_24h"], 2), "ret_4h": round(_h["ret_4h"], 2), "ret_1h": round(_h["ret_1h"], 2), "lin": round(_lin, 3), "qv_M": round(_h["qv_24h_usd"]/1e6, 1), "tier": _tier, "dd_ratio": round(_ddr, 3)})
                # SHORT side
                if (_h["ret_24h"] <= -_ret24_min and _h["ret_4h"] <= 0 and _h["ret_1h"] <= 0):
                    _ddr = _h["dd_short_pct"] / max(abs(_h["ret_24h"]), 0.01)
                    if _ddr <= _dd_ratio_max:
                        _stab = max(0.0, 1.0 - _ddr)
                        _score = abs(_h["ret_24h"]) * (_lin ** 2) * _qv_boost * _tm * _stab
                        _trender_short.append({"symbol": _sym, "score": round(_score, 4), "ret_24h": round(_h["ret_24h"], 2), "ret_4h": round(_h["ret_4h"], 2), "ret_1h": round(_h["ret_1h"], 2), "lin": round(_lin, 3), "qv_M": round(_h["qv_24h_usd"]/1e6, 1), "tier": _tier, "dd_ratio": round(_ddr, 3)})
            _trender_long.sort(key=lambda x: x["score"], reverse=True)
            _trender_short.sort(key=lambda x: x["score"], reverse=True)
            _live_t = bool(getattr(config, 'TRENDER_INJECT_LIVE', False))
            for _c in _trender_long:
                _shadow_rows.append({"ts": _ts_now, "injector": "TRENDER", "side": "LONG", "would_inject": _live_t, **_c})
                if _live_t and _c["symbol"] not in symbols_inf_long_list and _c["symbol"] not in _bearish_syms:
                    symbols_inf_long_list.append(_c["symbol"])
            for _c in _trender_short:
                _shadow_rows.append({"ts": _ts_now, "injector": "TRENDER", "side": "SHORT", "would_inject": _live_t, **_c})
                if _live_t and _c["symbol"] not in symbols_inf_short_list and _c["symbol"] not in _bullish_syms:
                    symbols_inf_short_list.append(_c["symbol"])
            logger.info(f"📈 [TRENDER_INJECT] mode={'LIVE' if _live_t else 'SHADOW'} long={len(_trender_long)} short={len(_trender_short)} | top_long={[c['symbol'] for c in _trender_long[:5]]} top_short={[c['symbol'] for c in _trender_short[:5]]}")

        # ---- BREAKOUT ----
        if getattr(config, 'BREAKOUT_INJECT_SHADOW_LOG', True) or getattr(config, 'BREAKOUT_INJECT_LIVE', False):
            _bk_mad_mult = float(getattr(config, 'BREAKOUT_MAD_MULTIPLIER', 2.0))
            _bk_min_pct = float(getattr(config, 'BREAKOUT_MIN_RET_PCT', 3.0))
            _bk_max_dd_ratio = float(getattr(config, 'BREAKOUT_MAX_DD_RATIO', 0.5))  # allow more DD than TRENDER (these spike)
            _all_4h = [_h["ret_4h"] for _h in _horizons.values()]
            if len(_all_4h) >= 30:
                _med4 = _stats2.median(_all_4h)
                _abs_dev_sorted = sorted(abs(x - _med4) for x in _all_4h)
                _mad4 = _abs_dev_sorted[len(_abs_dev_sorted) // 2] if _abs_dev_sorted else 0.5
                _mad4 = max(_mad4, 0.3)  # floor so micro-MAD doesn't admit noise
                _upper = _med4 + _bk_mad_mult * _mad4
                _lower = _med4 - _bk_mad_mult * _mad4
                _bk_long = []; _bk_short = []
                for _sym, _h in _horizons.items():
                    _r4 = _h["ret_4h"]
                    if _r4 >= _upper and _r4 >= _bk_min_pct:
                        _ddr = _h["dd_long_pct"] / max(_h["ret_24h"], 0.01) if _h["ret_24h"] > 0 else 1.0
                        if _ddr <= _bk_max_dd_ratio:
                            _bk_long.append({"symbol": _sym, "ret_4h": round(_r4, 2), "ret_1h": round(_h["ret_1h"], 2), "ret_24h": round(_h["ret_24h"], 2), "z_above": round((_r4 - _med4) / _mad4, 2), "qv_M": round(_h["qv_24h_usd"]/1e6, 1), "dd_ratio": round(_ddr, 3)})
                    elif _r4 <= _lower and _r4 <= -_bk_min_pct:
                        _ddr = _h["dd_short_pct"] / max(abs(_h["ret_24h"]), 0.01) if _h["ret_24h"] < 0 else 1.0
                        if _ddr <= _bk_max_dd_ratio:
                            _bk_short.append({"symbol": _sym, "ret_4h": round(_r4, 2), "ret_1h": round(_h["ret_1h"], 2), "ret_24h": round(_h["ret_24h"], 2), "z_below": round((_med4 - _r4) / _mad4, 2), "qv_M": round(_h["qv_24h_usd"]/1e6, 1), "dd_ratio": round(_ddr, 3)})
                _bk_long.sort(key=lambda x: -x["ret_4h"])
                _bk_short.sort(key=lambda x: x["ret_4h"])
                _live_b = bool(getattr(config, 'BREAKOUT_INJECT_LIVE', False))
                for _c in _bk_long:
                    _shadow_rows.append({"ts": _ts_now, "injector": "BREAKOUT", "side": "LONG", "would_inject": _live_b, "median_4h": round(_med4, 2), "mad_4h": round(_mad4, 2), "upper_band": round(_upper, 2), **_c})
                    if _live_b and _c["symbol"] not in symbols_inf_long_list and _c["symbol"] not in _bearish_syms:
                        symbols_inf_long_list.append(_c["symbol"])
                for _c in _bk_short:
                    _shadow_rows.append({"ts": _ts_now, "injector": "BREAKOUT", "side": "SHORT", "would_inject": _live_b, "median_4h": round(_med4, 2), "mad_4h": round(_mad4, 2), "lower_band": round(_lower, 2), **_c})
                    if _live_b and _c["symbol"] not in symbols_inf_short_list and _c["symbol"] not in _bullish_syms:
                        symbols_inf_short_list.append(_c["symbol"])
                logger.info(f"💥 [BREAKOUT_INJECT] mode={'LIVE' if _live_b else 'SHADOW'} median_4h={_med4:.2f}% mad={_mad4:.2f}% upper={_upper:.2f}% lower={_lower:.2f}% | long={len(_bk_long)} short={len(_bk_short)} | top_long={[c['symbol'] for c in _bk_long[:5]]} top_short={[c['symbol'] for c in _bk_short[:5]]}")
            else:
                logger.info(f"💥 [BREAKOUT_INJECT] insufficient sample (n={len(_all_4h)}) — need ≥30 syms with full 1h+15m history")

        # ---- Persist shadow log (jsonl, one row per candidate per cycle) ----
        if _shadow_rows:
            try:
                _shadow_path.parent.mkdir(parents=True, exist_ok=True)
                with open(_shadow_path, "ab") as _slf:
                    for _row in _shadow_rows:
                        _slf.write(orjson.dumps(_row) + b"\n")
            except Exception as _slog_err:
                logger.warning(f"[INJECT_SHADOW_LOG] write failed: {_slog_err}")
    except Exception as _trbk_err:
        logger.warning(f"[TRENDER+BREAKOUT_INJECT] error: {_trbk_err}")
    # === END TRENDER + BREAKOUT INJECTION ==============================================

    # Save scores to files
    save_scores(fs, FINAL_SCORE_FILE)
    save_scores(fsr, FINAL_SCORE_R_FILE)
    save_scores(prox, PROX_FILE)
    save_scores(bs, BAND_FILE)
    
    # Save market data and rankings
    await save_market_data()
    await save_rankings_json(final_ranking_data_scalars)
    
    logger.info(f"✅ [initial_fetch_and_ranking] => Completed with 4x linearity emphasis. Processed {len(final_ranking_data_scalars)} symbols.")
    logger.info(f"📊 High linearity: {len([s for s in final_ranking_data_scalars if s.get('linearity_category') == 'HIGH'])} symbols")
    logger.info(f"📊 Medium linearity: {len([s for s in final_ranking_data_scalars if s.get('linearity_category') == 'MEDIUM'])} symbols")
    logger.info(f"📊 Low linearity: {len([s for s in final_ranking_data_scalars if s.get('linearity_category') == 'LOW'])} symbols")
    
    return final_ranking_data_scalars, {}
# async def initial_fetch_and_ranking(symbols, timeframes=["4h","1h","15m","3m"]):
#     """Calculate rankings from klines data only. Does NOT use market_data_*.json files - uses klines cache/Redis directly."""
#     global RANKING_INFO, to_save_top15_15m, to_save_bottom15_15m, to_save_top20, to_save_bottom20, to_save_top30, to_save_bottom30, to_save_top30_r, to_save_bottom30_r, top_100_long_term
#     global fs, fsr, prox, bs, symbols_ang_long_list, symbols_ang_short_list, symbols_inf_long_list, symbols_inf_short_list, mark_price_cache
#     try:
#         redis_manager = await get_simple_redis_manager()
#         logger.info("✅ Redis manager available for rankings")
#     except Exception as e:
#         logger.warning(f"⚠️ Redis not available for rankings: {e}")
#         redis_manager = None
#     RANKING_INFO.clear(); top_100_long_term.clear(); to_save_top15_15m.clear(); to_save_bottom15_15m.clear(); to_save_top20.clear(); to_save_bottom20.clear(); to_save_top30.clear(); to_save_bottom30.clear(); to_save_top30_r.clear(); to_save_bottom30_r.clear(); fs.clear(); fsr.clear(); prox.clear(); bs.clear()

#     def calculate_trimmed_slope(df, tf):
#         if df.empty or "top" not in df.columns or "bottom" not in df.columns or 'timestamp' not in df.columns: return 0.0, 0.0, ""
#         df_calc = df.copy(); df_calc["timestamp"] = pd.to_datetime(df_calc["timestamp"], errors='coerce'); df_calc["timestamp"] = df_calc["timestamp"].dt.tz_localize('UTC') if df_calc["timestamp"].dt.tz is None else df_calc["timestamp"].dt.tz_convert('UTC'); df_calc.dropna(subset=['timestamp'], inplace=True)
#         if df_calc.empty: return 0.0,0.0,""
#         days_visible = {"4h": 66, "1h": 14, "15m": 5, "3m": 1}; ndays = days_visible.get(tf, 7)
#         if df_calc.empty or df_calc["timestamp"].max() is pd.NaT: return 0.0, 0.0, ""
#         cutoff = df_calc["timestamp"].max() - pd.Timedelta(days=ndays); df_filtered = df_calc[df_calc["timestamp"] >= cutoff].copy()
#         if df_filtered.empty: return 0.0,0.0,""
#         tops = df_filtered.dropna(subset=["top"]); bots = df_filtered.dropna(subset=["bottom"]); combined = pd.concat([tops, bots]).sort_values("timestamp"); combined.dropna(subset=['close', 'timestamp'], inplace=True)
#         if len(combined) < 2: return 0.0, 0.0, ""
#         combined['timestamp_numeric'] = pd.to_numeric(combined['timestamp']); x_time = combined['timestamp_numeric'].values; y_price = pd.to_numeric(combined["close"].values, errors='coerce')
#         if np.isnan(y_price).all() or len(y_price) < 2: return 0.0, 0.0, ""
#         avgp = np.nanmean(y_price); y_norm = y_price / avgp; x_min, x_max = x_time.min(), x_time.max()
#         if x_max == x_min: return 0.0, 0.0, ""
#         x_normalized = (x_time - x_min) / (x_max - x_min)
#         try: slp, icpt, r_val, p_val, std_err = linregress(x_normalized, y_norm); return float(slp * 100), float(r_val) if pd.notna(r_val) else 0.0, f"{tf} slp={slp*100:.2f}%"
#         except ValueError: return 0.0,0.0,""

#     # Process all symbols
#     intermediate_symbol_data = []; all_dfs_for_returns_calc = {}
#     processed_symbols = []
    
#     start_time = time.time()
#     logger.info(f"🔍 Processing {len(symbols)} symbols...")
    
#     # Process symbols in parallel batches
#     async def process_single_symbol(sym):
#         """Process a single symbol and return its data"""
#         try:
#             symbol_start = time.time()
#             dfs_current_sym = {}
#             for tf in timeframes:
#                 dftf = await convert_cached_klines_to_analysis_df(sym, tf)
#                 if dftf.empty: continue
#                 dftf = detect_tops_bottoms(dftf, distance=5, prominence=1e-4)
#                 dftf = add_stochrsi_zones(dftf)
#                 dfs_current_sym[tf] = dftf
            
#             if not dfs_current_sym:
#                 return None
            
#             slopes_raw = {}
#             r_values_raw = {}
#             for tf, dfv in dfs_current_sym.items():
#                 slp, rvv, _ = calculate_trimmed_slope(dfv, tf)
#                 slopes_raw[tf] = slp
#                 r_values_raw[tf] = rvv

#             tf_weights_lt = {"D": 60, "4h": 120, "1h": 200, "15m": 400, "3m": 80}
#             tf_weights_st = {"D": 4, "4h": 6, "1h": 25, "15m": 100, "3m": 200}
#             trend_val_raw_lt = sum(slopes_raw.get(tf, 0.0) * tf_weights_lt.get(tf, 0.0) for tf in slopes_raw.keys())
#             trend_val_raw_st = sum(slopes_raw.get(tf, 0.0) * tf_weights_st.get(tf, 0.0) for tf in slopes_raw.keys())
                        
#             symbol_time = time.time() - symbol_start
#             if symbol_time > 1.0: 
#                 logger.debug(f"⏱️ {sym} took {symbol_time:.2f}s")
            
#             return {
#                 "symbol": sym,
#                 "dfs_for_calc": dfs_current_sym,
#                 "slopes_raw": slopes_raw,
#                 "r_values_raw": r_values_raw,
#                 "trend_val_raw_lt": trend_val_raw_lt,
#                 "trend_val_raw_st": trend_val_raw_st
#             }
            
#         except Exception as e:
#             logger.debug(f"Error processing {sym}: {e}")
#             return None
#     batch_size = 31
#     logger.info(f"🔄 Processing symbols in batches of {batch_size}...")
    
#     for i in range(0, len(symbols), batch_size):
#         batch = symbols[i:i+batch_size]
#         batch_start = time.time()
        
#         results = await asyncio.gather(*[process_single_symbol(sym) for sym in batch], return_exceptions=True)
        
#         # Collect valid results
#         for result in results:
#             if result is not None and not isinstance(result, Exception):
#                 intermediate_symbol_data.append(result)
#                 processed_symbols.append(result["symbol"])
#                 all_dfs_for_returns_calc[result["symbol"]] = result["dfs_for_calc"]
        
#         batch_time = time.time() - batch_start
#         batch_num = (i // batch_size) + 1
#         total_batches = (len(symbols) + batch_size - 1) // batch_size
#         logger.info(f"✅ Batch {batch_num}/{total_batches} completed in {batch_time:.2f}s ({len(batch)} symbols)")
    
#     total_time = time.time() - start_time
#     logger.info(f"✅ Processed {len(processed_symbols)} symbols successfully in {total_time:.2f}s ({total_time/len(processed_symbols):.3f}s per symbol)")

#     if not intermediate_symbol_data: return [], {}
#     logger.info(f"[ranking] Processing returns for {len(intermediate_symbol_data)} symbols...")
#     returns_15m = await calculate_15min_returns_for_symbols(all_dfs_for_returns_calc); returns_3m = await calculate_3min_returns_for_symbols(all_dfs_for_returns_calc); del all_dfs_for_returns_calc
#     logger.info(f"[ranking] Returns calculated: 15m={len(returns_15m)}, 3m={len(returns_3m)}")
#     symbols_needing_price = []
#     for item in intermediate_symbol_data:
#         sym = item["symbol"]
#         if sym not in mark_price_cache:
#             df_3m = item.get("dfs_for_calc", {}).get("3m")
#             if df_3m is not None and not df_3m.empty and "close" in df_3m.columns:
#                 latest_close = df_3m["close"].iloc[-1]
#                 mark_price_cache[sym] = {"price": latest_close, "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
#             else:
#                 symbols_needing_price.append(sym)
#     if symbols_needing_price:
#         logger.debug(f"[ranking] Pre-fetching {len(symbols_needing_price)} missing prices...")
#         results = await asyncio.gather(*[get_current_price(sym) for sym in symbols_needing_price], return_exceptions=True)
#         for sym, result in zip(symbols_needing_price, results):
#             if isinstance(result, Exception) or result[0] is None: continue
#             mark_price_cache[sym] = {"price": result[0], "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}

#     all_trend_raw_lt_vals = [item["trend_val_raw_lt"] for item in intermediate_symbol_data if pd.notna(item["trend_val_raw_lt"])]
#     all_trend_raw_st_vals = [item["trend_val_raw_st"] for item in intermediate_symbol_data if pd.notna(item["trend_val_raw_st"])]
    
#     global_min_trend_lt, global_max_trend_lt = calculate_min_max(all_trend_raw_lt_vals)
#     global_min_trend_st, global_max_trend_st = calculate_min_max(all_trend_raw_st_vals)
    
#     for item in intermediate_symbol_data:
#         item["trend_val_norm_lt"] = normalize_log_signed(item["trend_val_raw_lt"], global_min_trend_lt, global_max_trend_lt)
#         item["trend_val_norm_st"] = normalize_log_signed(item["trend_val_raw_st"], global_min_trend_st, global_max_trend_st)
    
#     final_ranking_data_scalars = []
    
#     for item in intermediate_symbol_data:
#         sym = item["symbol"]
#         dfs_calc = item["dfs_for_calc"]
        
#         # Get dataframes
#         df_3m = dfs_calc.get("3m", pd.DataFrame())
#         df_15m = dfs_calc.get("15m", pd.DataFrame())
#         df_1h = dfs_calc.get("1h", pd.DataFrame())
#         df_4h = dfs_calc.get("4h", pd.DataFrame())
#         df_D = dfs_calc.get("D", pd.DataFrame())
        
#         # Calculate linearity
#         valid_r_vals = [r for r in item["r_values_raw"].values() if pd.notna(r)]
#         if valid_r_vals:
#             lin_val_raw = sum(valid_r_vals) / len(valid_r_vals)
#             abs_lin_val_raw = sum(abs(r) for r in valid_r_vals) / len(valid_r_vals)
#         else:
#             lin_val_raw = 0.0
#             abs_lin_val_raw = 0.0
        
#         # Calculate relative volume
#         rel_vol_h1 = calculate_relative_volume(df_1h, 50) if df_1h is not None else 1.0
#         rel_vol_m15 = calculate_relative_volume(df_15m, 50) if df_15m is not None else 1.0
#         rel_vol_m3 = calculate_relative_volume(df_3m, 50) if df_3m is not None else 1.0
        
#         # FIXED: More gradual volume adjustment
#         rel_vol_tot_raw = (rel_vol_h1 * 2 + rel_vol_m15 * 8 + rel_vol_m3 * 14) / 24.0
#         rel_vol_tot_raw_r = (rel_vol_h1 * 2 + rel_vol_m15 * 12 + rel_vol_m3 * 24) / 38.0
        
#         # Gradual adjustment instead of step function
#         rel_vol_tot_norm_factor = 1.0 + (rel_vol_tot_raw - 1.0) * 0.2
#         rel_vol_tot_norm_factor = max(0.5, min(2.0, rel_vol_tot_norm_factor))
        
#         rel_vol_tot_norm_factor_r = 1.0 + (rel_vol_tot_raw_r - 1.0) * 0.2
#         rel_vol_tot_norm_factor_r = max(0.5, min(2.0, rel_vol_tot_norm_factor_r))
        
#         # Get current price
#         current_price = mark_price_cache.get(sym, {}).get("price") if sym in mark_price_cache else None
        
#         # Calculate price vs SMA
#         price_vs_sma_score = 0.0
#         if current_price and df_1h is not None and not df_1h.empty and len(df_1h) >= 200:
#             sma_1h = df_1h['close'].rolling(200).mean().iloc[-1] if 'close' in df_1h.columns else None
#             if sma_1h and sma_1h > 0:
#                 price_vs_sma_score = ((current_price - sma_1h) / sma_1h) * 100 * 15
        
#         # FIXED: Safe band score calculation
#         valid_dfs = {tf: df for tf, df in dfs_calc.items() if df is not None and not df.empty}
#         band_score = get_band_score_safe(current_price, valid_dfs)
        
#         # FIXED: Proper proximity score calculation
#         weighted_prox_raw = calculate_proximity_score(current_price, valid_dfs)
        
#         # Calculate breakthrough bonus
#         breakthrough_bonus = 0.0
#         if current_price and df_1h is not None and not df_1h.empty:
#             atr_1h = calculate_atr(df_1h, period=14)
#             # ... breakthrough logic ...
        
#         # Calculate weighted gains
#         weighted_gains_lt = calculate_weighted_gains(df_4h, 270, decay_factor=0.995) if df_4h is not None else 0.0
#         weighted_gains_st = calculate_weighted_gains(df_3m, 480, decay_factor=0.98) if df_3m is not None else 0.0
        
#         # FIXED: Proper trend values for final calculation
#         tf_weights_lt_final = {"4h": 80, "1h": 160, "15m": 100, "3m": 60}
#         tf_weights_st_final = {"4h": 60, "1h": 120, "15m": 140, "3m": 160}
        
#         trend_val_lt = sum(item["slopes_raw"].get(tf, 0.0) * tf_weights_lt_final.get(tf, 0.0) for tf in item["slopes_raw"].keys())
#         trend_val_st = sum(item["slopes_raw"].get(tf, 0.0) * tf_weights_st_final.get(tf, 0.0) for tf in item["slopes_raw"].keys())
        
#         linearity_multiplier_lt = 1 + (3.2 * abs_lin_val_raw)  # 320% boost - 4x more important
#         linearity_multiplier_st = 1 + (4.0 * abs_lin_val_raw)  # 400% boost - 4x more important
#         long_term_score_raw = calculate_smart_score( trend_val_lt,  abs_lin_val_raw, min(rel_vol_tot_raw / 3.0, 1.0),  band_score, weighted_gains_lt )
        
#         short_term_score_raw = calculate_smart_score( trend_val_st, abs_lin_val_raw, min(rel_vol_tot_raw_r / 3.0, 1.0), band_score, weighted_gains_st  ) * 1.2  # Slightly more weight to short-term
        
#         # Add linearity metadata for filtering/ranking
#         item["avg_linearity"] = abs_lin_val_raw
#         item["linearity_category"] = (
#             "HIGH" if abs_lin_val_raw > 0.7 else
#             "MEDIUM" if abs_lin_val_raw > 0.5 else
#             "LOW"
#         )          
#         # FIXED: Balanced final score calculation
#         long_term_score_raw = calculate_final_scores(
#             trend_val_lt, linearity_multiplier_lt, price_vs_sma_score,
#             band_score, breakthrough_bonus, weighted_gains_lt, weighted_prox_raw, True
#         )
        
#         short_term_score_raw = calculate_final_scores(
#             trend_val_st, linearity_multiplier_st, price_vs_sma_score,
#             band_score, breakthrough_bonus, weighted_gains_st, weighted_prox_raw, False
#         )
        
#         # Apply volume adjustment
#         final_score_raw_lt = long_term_score_raw * rel_vol_tot_norm_factor
#         final_score_raw_st = short_term_score_raw * rel_vol_tot_norm_factor_r
        
#         final_ranking_data_scalars.append({
#             "symbol": sym,
#             "slopes_raw": item['slopes_raw'],
#             "r_values_raw": item['r_values_raw'],
#             "trend_val_norm_lt": item["trend_val_norm_lt"],
#             "trend_val_norm_st": item["trend_val_norm_st"],
#             "linearity_raw": lin_val_raw,
#             "abs_linearity_raw": abs_lin_val_raw,
#             "rel_vol_raw": rel_vol_tot_raw,
#             "rel_vol_norm_factor": rel_vol_tot_norm_factor,
#             "final_score_raw_lt": final_score_raw_lt,
#             "final_score_raw_st": final_score_raw_st,
#             "weighted_proximity_score_raw": weighted_prox_raw,
#             "band_score": band_score,
#             "weighted_gains_lt": weighted_gains_lt,
#             "weighted_gains_st": weighted_gains_st,
#             "dfs_for_calc": dfs_calc
#         })



#     # all_trend_raw_lt_vals = [item["trend_val_raw_lt"] for item in intermediate_symbol_data if pd.notna(item["trend_val_raw_lt"])]
#     # all_trend_raw_st_vals = [item["trend_val_raw_st"] for item in intermediate_symbol_data if pd.notna(item["trend_val_raw_st"])]
#     # global_min_trend_lt, global_max_trend_lt = calculate_min_max(all_trend_raw_lt_vals)
#     # global_min_trend_st, global_max_trend_st = calculate_min_max(all_trend_raw_st_vals)
#     # for item in intermediate_symbol_data:
#     #     item["trend_val_norm_lt"] = normalize_log_signed(item["trend_val_raw_lt"], global_min_trend_lt, global_max_trend_lt)
#     #     item["trend_val_norm_st"] = normalize_log_signed(item["trend_val_raw_st"], global_min_trend_st, global_max_trend_st)
#     # final_ranking_data_scalars = []; all_raw_proximity_scores_3m_for_norm = []
#     # for item in intermediate_symbol_data:
#     #     sym = item["symbol"]; dfs_calc = item["dfs_for_calc"]
#     #     df_3m = dfs_calc.get("3m", pd.DataFrame()); df_15m = dfs_calc.get("15m", pd.DataFrame()); df_1h = dfs_calc.get("1h", pd.DataFrame()); df_4h = dfs_calc.get("4h", pd.DataFrame()); df_D = dfs_calc.get("D", pd.DataFrame())
#     #     lin_val_raw = sum(r for r in item["r_values_raw"].values() if pd.notna(r)) / len([r for r in item["r_values_raw"].values() if pd.notna(r)]) if item["r_values_raw"] and any(pd.notna(r) for r in item["r_values_raw"].values()) else 0.0
#     #     abs_lin_val_raw = sum(abs(r) for r in item["r_values_raw"].values() if pd.notna(r)) / len([r for r in item["r_values_raw"].values() if pd.notna(r)]) if item["r_values_raw"] and any(pd.notna(r) for r in item["r_values_raw"].values()) else 0.0
#     #     rel_vol_h1 = calculate_relative_volume(df_1h, 50); rel_vol_m15 = calculate_relative_volume(df_15m, 50); rel_vol_m3 = calculate_relative_volume(df_3m, 50)
#     #     rel_vol_tot_raw = (rel_vol_h1 * 2 + rel_vol_m15 * 8 + rel_vol_m3 * 14) / 24.0
#     #     rel_vol_tot_raw_r = (rel_vol_h1 * 2 + rel_vol_m15 * 12 + rel_vol_m3 * 24) / 38.0        
#     #     rel_vol_tot_norm_factor = 1.4 if rel_vol_tot_raw > 1.5 else (1.1 if rel_vol_tot_raw > 1.1 else (0.8 if rel_vol_tot_raw < 0.85 else 1.0))
#     #     rel_vol_tot_norm_factor_r = 1.4 if rel_vol_tot_raw_r > 1.5 else (1.1 if rel_vol_tot_raw_r > 1.1 else (0.8 if rel_vol_tot_raw_r < 0.85 else 1.0))
#     #     current_price = mark_price_cache.get(sym, {}).get("price") if sym in mark_price_cache else None
#     #     price_vs_sma_score = 0.0
#     #     if current_price and df_1h is not None and not df_1h.empty and len(df_1h) >= 200:
#     #         sma_1h = df_1h['close'].rolling(200).mean().iloc[-1] if 'close' in df_1h.columns else None
#     #         if sma_1h and sma_1h > 0: price_vs_sma_score = ((current_price - sma_1h) / sma_1h) * 100 * 15
#     #     band_data = {}
#     #     band_score = 0.0
#     #     if current_price is not None:
#     #         dfs_dict = {
#     #             'D': df_D if 'D' in dfs_calc else None,
#     #             '4h': df_4h,
#     #             '1h': df_1h,
#     #             '15m': df_15m,
#     #             '3m': df_3m }
#     #         valid_dfs = {tf: df for tf, df in dfs_dict.items() if df is not None and not df.empty}
            
#     #         if valid_dfs:
#     #             band_data = calculate_weighted_band_score(current_price, valid_dfs)
#     #             band_score = band_data.get("score", 0.0)
                
#     #             # Log breakdown for debugging
#     #             if sym in ["BTCUSDC", "ETHUSDC"]:
#     #                 breakdown = band_data.get("breakdown", {})
#     #                 logger.debug(f"[{sym}] Band score breakdown: " + 
#     #                             ", ".join([f"{tf}:{data['score']:.1f}"   for tf, data in breakdown.items()]) + f" → Weighted: {band_score:.1f}")
#     #     # weighted_prox_score_raw = 0.0
#     #     # if df_3m is not None and not df_3m.empty:
#     #     #     df_3m_with_prox = assign_points_proximity_3m(df_3m.copy(), df_15m, df_1h, df_4h)
#     #     #     if "proximity_score" in df_3m_with_prox.columns: 
#     #     #         valid_prox_scores = df_3m_with_prox["proximity_score"].dropna()
#     #     #         if not valid_prox_scores.empty: weighted_prox_score_raw = valid_prox_scores.mean(); all_raw_proximity_scores_3m_for_norm.extend(valid_prox_scores.tolist())
#     #     weighted_prox_raw = 0.0
#     #     all_prox_scores = []  #
#     #     if current_price is not None:
#     #         tf_weights = {
#     #             'D': 4.0,
#     #             '4h': 4.0,
#     #             '1h': 2.0,
#     #             '15m': 1.0,
#     #             '3m': 0.5    }
#     #         for tf, weight in tf_weights.items():
#     #             df_tf = dfs_calc.get(tf)
#     #             if df_tf is None or df_tf.empty:
#     #                 continue
#     #             band_df = calculate_regression_band(df_tf)
#     #             if band_df.empty or 'upperb' not in band_df.columns or 'lowerb' not in band_df.columns:
#     #                 continue
#     #             upper_band = band_df['upperb'].iloc[-1]
#     #             lower_band = band_df['lowerb'].iloc[-1]
                
#     #             if pd.isna(upper_band) or pd.isna(lower_band) or upper_band == lower_band:
#     #                 continue
                
#     #             current_price_tf = current_price
#     #             span = upper_band - lower_band
#     #             if span == 0:
#     #                 continue
#     #             frac = (current_price_tf - lower_band) / span
#     #             frac = max(0.0, min(1.0, frac)) 
#     #             score = (90.0 - (180.0 * frac)) * (100/90)
#     #             weighted_score = score * weight
#     #             all_prox_scores.append((tf, score, weighted_score))
#     #         if all_prox_scores:
#     #             total_weight = sum(tf_weights.get(tf, 0) for tf, _, _ in all_prox_scores if tf in tf_weights)
#     #             total_weighted_score = sum(weighted_score for _, _, weighted_score in all_prox_scores)
#     #             if total_weight > 0:
#     #                 weighted_prox_raw = total_weighted_score / total_weight
#     #             else:
#     #                 weighted_prox_raw = sum(score for _, score, _ in all_prox_scores) / len(all_prox_scores)
#     #             mean_prox_score_raw_3m = weighted_prox_raw
#     #             all_raw_proximity_scores_3m_for_norm.append(weighted_prox_raw)
#     #             if sym in ["BTCUSDC", "ETHUSDC"]:
#     #                 logger.debug(f"[{sym}] Proximity scores: {all_prox_scores}, Weighted: {weighted_prox_raw:.2f}")
                
#     #     breakthrough_bonus = 0.0
#     #     if current_price and df_1h is not None and not df_1h.empty:
#     #         atr_1h = calculate_atr(df_1h, period=14); upper_band = band_data.get("upper_band"); lower_band = band_data.get("lower_band")
#     #         prox_range_df = pd.concat([df_1h, df_4h]) if df_4h is not None else df_1h; prox_range = get_proximity_range(prox_range_df)
#     #         if upper_band and prox_range.get("top") and atr_1h > 0 and current_price > upper_band and current_price > prox_range.get("top"):
#     #             distance_above = current_price - max(upper_band, prox_range.get("top"))
#     #             if distance_above > (atr_1h * 0.75): breakthrough_bonus = (distance_above / atr_1h) * 75
#     #         elif lower_band and prox_range.get("bottom") and atr_1h > 0 and current_price < lower_band and current_price < prox_range.get("bottom"):
#     #             distance_below = min(lower_band, prox_range.get("bottom")) - current_price
#     #             if distance_below > (atr_1h * 0.75): breakthrough_bonus = -((distance_below / atr_1h) * 75)
        
#     #     # Calculate weighted gains/losses
#     #     # Long-term: 45 days of 4h data (270 bars), decay factor 0.995 for slower decay
#     #     weighted_gains_lt = calculate_weighted_gains(df_4h, 270, decay_factor=0.995) if df_4h is not None else 0.0
        
#     #     # Short-term: 1 day of 3m data (480 bars), decay factor 0.98 for faster decay
#     #     weighted_gains_st = calculate_weighted_gains(df_3m, 480, decay_factor=0.98) if df_3m is not None else 0.0
#     #     trend_val_lt = sum(item["slopes_raw"].get(tf, 0.0) * w for tf, w in {"4h": 80, "1h": 160, "15m": 100, "3m": 60}.items())
#     #     trend_val_st = sum(item["slopes_raw"].get(tf, 0.0) * w for tf, w in {"4h": 60, "1h": 120, "15m": 140, "3m": 160}.items())
#     #     linearity_multiplier_lt = 1 + (4 * abs_lin_val_raw)  # Amplifies trend by up to 80%
#     #     linearity_multiplier_st = 1 + (4 * abs_lin_val_raw)  
#     #     tf_weights_lt_final = {"4h": 80, "1h": 160, "15m": 100, "3m": 60}
#     #     tf_weights_st_final = {"4h": 60, "1h": 120, "15m": 140, "3m": 160}
#     #     trend_val_lt = sum(slopes_raw.get(tf, 0.0) * tf_weights_lt_final.get(tf, 0.0) for tf in slopes_raw.keys())
#     #     trend_val_st = sum(slopes_raw.get(tf, 0.0) * tf_weights_st_final.get(tf, 0.0) for tf in slopes_raw.keys())



#     #     long_term_score_raw = calculate_final_scores(   trend_val_lt, linearity_multiplier_lt, price_vs_sma_score, band_score, breakthrough_bonus, weighted_gains_lt, weighted_prox_raw, True )

#     #     short_term_score_raw = calculate_final_scores( trend_val_st, linearity_multiplier_st, price_vs_sma_score,   band_score, breakthrough_bonus, weighted_gains_st, weighted_prox_raw, False  )
#     #     final_score_raw_lt = long_term_score_raw * rel_vol_tot_norm_factor
#     #     final_score_raw_st = short_term_score_raw * rel_vol_tot_norm_factor_r

#     #     final_ranking_data_scalars.append({
#     #         "symbol": sym, "slopes_raw": item['slopes_raw'], "r_values_raw": item['r_values_raw'],
#     #         "trend_val_norm_lt": item["trend_val_norm_lt"], "trend_val_norm_st": item["trend_val_norm_st"],
#     #         "linearity_raw": lin_val_raw, "abs_linearity_raw": abs_lin_val_raw, "rel_vol_raw": rel_vol_tot_raw, "rel_vol_norm_factor": rel_vol_tot_norm_factor,
#     #         "final_score_raw_lt": final_score_raw_lt, "final_score_raw_st": final_score_raw_st,
#     #         "weighted_proximity_score_raw": weighted_prox_raw, "band_score": band_score, 
#     #         "weighted_gains_lt": weighted_gains_lt, "weighted_gains_st": weighted_gains_st,
#     #         "dfs_for_calc": dfs_calc })

#     if not final_ranking_data_scalars: return [], {}

#     all_scores_combined = [entry["final_score_raw_lt"] for entry in final_ranking_data_scalars] + [entry["final_score_raw_st"] for entry in final_ranking_data_scalars]
#     global_min_score, global_max_score = calculate_min_max(all_scores_combined)
    
#     for entry in final_ranking_data_scalars:
#         sym = entry["symbol"]
#         entry["final_score_norm"] = normalize_log_signed(entry["final_score_raw_lt"], global_min_score, global_max_score)
#         entry["final_score_recent_norm"] = normalize_log_signed(entry["final_score_raw_st"], global_min_score, global_max_score)
#         entry["proximity_score_norm"] = normalize_log_signed(entry["weighted_proximity_score_raw"], -100, 100) if entry["weighted_proximity_score_raw"] else 0.0
        
#         fs[sym] = entry["final_score_norm"]; fsr[sym] = entry["final_score_recent_norm"]
#         prox[sym] = entry["proximity_score_norm"]; bs[sym] = entry["band_score"]

#     # CORRECT SORTING: Biggest winner = #1, Biggest loser = #1
#     top_winners_lt = sorted(final_ranking_data_scalars, key=lambda x: x["final_score_norm"], reverse=True)  # Highest first
#     top_losers_lt = sorted(final_ranking_data_scalars, key=lambda x: x["final_score_norm"])  # Lowest first (most negative)
#     top_winners_st = sorted(final_ranking_data_scalars, key=lambda x: x["final_score_recent_norm"], reverse=True)  # Highest first  
#     top_losers_st = sorted(final_ranking_data_scalars, key=lambda x: x["final_score_recent_norm"])  # Lowest first (most negative)
    
#     # Save top/bottom 30 (long term)
#     to_save_top30[:] = [{"symbol": e["symbol"], "score": e["final_score_norm"]} for e in top_winners_lt[:30]]
#     to_save_bottom30[:] = [{"symbol": e["symbol"], "score": e["final_score_norm"]} for e in top_losers_lt[:30]]
    
#     # Save top/bottom 30 recent
#     to_save_top30_r[:] = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"]} for e in top_winners_st[:20]]
#     to_save_bottom30_r[:] = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"]} for e in top_losers_st[:20]]
    
#     # Save top/bottom 15 recent
#     to_save_top15_r = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"]} for e in top_winners_st[:20]]
#     to_save_bottom15_r = [{"symbol": e["symbol"], "score": e["final_score_recent_norm"]} for e in top_losers_st[:20]]
    
#     to_save_top20[:] = to_save_top30[:20]; 
#     to_save_bottom20[:] = [{"symbol": e["symbol"], "score": e["final_score_norm"]} for e in top_losers_lt[:20]]
    
#     top_100_long_term.extend([item["symbol"] for item in top_winners_lt[:90]])
    
#     def merge_filter(ret15_dict, ret3_dict, top_100_set, cond15_func, cond3_func):
#         merged = {}
#         for sym_candidate in top_100_set:
#             val_15 = ret15_dict.get(sym_candidate); val_3 = ret3_dict.get(sym_candidate)
#             meets_cond15 = pd.notna(val_15) and cond15_func(val_15); meets_cond3 = pd.notna(val_3) and cond3_func(val_3)
#             if meets_cond15 or meets_cond3: merged[sym_candidate] = {"returns_15m": val_15, "returns_3m": val_3}
#         return merged
        
#     top_100_set = set(top_100_long_term)
#     final_merged_top = merge_filter(returns_15m, returns_3m, top_100_set, lambda x: x > 1.0, lambda x: x > 0.9)
#     final_merged_bottom = merge_filter(returns_15m, returns_3m, top_100_set, lambda x: x < -0.9, lambda x: x < -0.75)
#     to_save_top15_15m[:] = [{"symbol": k, **v} for k, v in final_merged_top.items()]
#     to_save_bottom15_15m[:] = [{"symbol": k, **v} for k, v in final_merged_bottom.items()]

#     symbols_ang_long_list = [item["symbol"] for item in to_save_top30]
#     symbols_ang_short_list = [item["symbol"] for item in to_save_bottom30]
#     extra_long = []
#     extra_short = []
#     try:
#         with open("data/ranking_points.json", "r") as f: 
#             ranking_data = json.load(f)
#         sorted_ranking = sorted(ranking_data.items(), key=lambda x: x[1], reverse=True)
#         extra_long = [{"symbol": symbol, "score": score} for symbol, score in sorted_ranking[:20]]
#         extra_short = [{"symbol": symbol, "score": score} for symbol, score in sorted_ranking[-20:]]
#     except Exception as e: 
#         logger.warning(f"Failed to load ranking_points.json: {e}")
#     def extend_unique(base_list, new_items):
#         existing_syms = {i["symbol"] for i in base_list}
#         for item in new_items:
#             if item["symbol"] not in existing_syms:
#                 base_list.append(item)
#                 existing_syms.add(item["symbol"])
#         return base_list
#     to_save_top30 = extend_unique(to_save_top30, extra_long)
#     to_save_bottom30 = extend_unique(to_save_bottom30, extra_short)

#     symbols_ang_long_list = [item["symbol"] for item in to_save_top30]
#     symbols_ang_short_list = [item["symbol"] for item in to_save_bottom30]

#     # 5. Trim to 40 (or desired length)
#     symbols_ang_long_list = symbols_ang_long_list[:20]
#     symbols_ang_short_list = symbols_ang_short_list[:20]

# # ASSIGN SYMBOL LISTS AS REQUESTED - Add ranking_points.json to final scores, ensure 40 each
#     symbols_ang_long_list = [item["symbol"] for item in to_save_top30]
#     symbols_ang_short_list = [item["symbol"] for item in to_save_bottom30]
#     try:
#         with open("data/ranking_points.json", "r") as f: ranking_data = json.load(f)
#         sorted_ranking = sorted(ranking_data.items(), key=lambda x: x[1], reverse=True)
#         symbols_ang_long_list.extend([symbol for symbol, score in sorted_ranking[:20]])
#         symbols_ang_short_list.extend([symbol for symbol, score in sorted_ranking[-20:]])
#     except Exception as e: logger.warning(f"Failed to load ranking_points.json: {e}")
#     # Remove duplicates and ensure 40 symbols each
#     symbols_ang_long_list = list(dict.fromkeys(symbols_ang_long_list))[:20]
#     symbols_ang_short_list = list(dict.fromkeys(symbols_ang_short_list))[:20]
#     if len(symbols_ang_long_list) < 20: symbols_ang_long_list.extend([item["symbol"] for item in to_save_top30[len(symbols_ang_long_list):]])
#     if len(symbols_ang_short_list) < 20: symbols_ang_short_list.extend([item["symbol"] for item in to_save_bottom30[len(symbols_ang_short_list):]]) 
#     symbols_inf_long_list = [item["symbol"] for item in to_save_top15_15m] + [item["symbol"] for item in to_save_top15_r]  # 15m winners + recent winners
#     symbols_inf_short_list = [item["symbol"] for item in to_save_bottom15_15m] + [item["symbol"] for item in to_save_bottom15_r]  # 15m losers + recent losers

#     save_scores(fs, FINAL_SCORE_FILE); save_scores(fsr, FINAL_SCORE_R_FILE); save_scores(prox, PROX_FILE); save_scores(bs, BAND_FILE)
#     await save_market_data()
#     await save_rankings_json(final_ranking_data_scalars)
#     logger.info(f"[initial_fetch_and_ranking] => Completed. Processed {len(final_ranking_data_scalars)} symbols.")
#     return final_ranking_data_scalars, {}

async def send_market_mode_signal(mode: str, market_index: float, mode_data: dict):
    """
    Send market mode signal through the normal signals pipeline
    """
    try:
        symbol = "MARKET"
        timeframe = "GLOBAL"
        event_type = f"market_mode_{mode.lower()}"
        
        # Create signal event
        signal_event = {
            "symbol": symbol,
            "timeframe": timeframe,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc),
            "timestamp_signal": datetime.now(timezone.utc),
            "market_index": market_index,
            "mode": mode,
            "reason": f"Market movement index: {market_index:.1f}",
            "action": "MONITOR",
            "priority": "HIGH" if mode == "EXTREME_MODE" else "MEDIUM",
            "source": "market_movement_monitor"
        }
        
        # Use your existing send_signal function
        await send_signal(signal_event, signals_data)
        
        logger.info(f"📡 Sent market mode signal: {mode} (Index: {market_index:.1f})")
        
    except Exception as e:
        logger.error(f"Error sending market mode signal: {e}")

async def get_current_market_mode() -> dict:
    """
    Get current market mode and index
    """
    try:
        mode_file = DATA_DIR / "market_mode.json"
        if mode_file.exists():
            async with aiofiles.open(mode_file, "r") as f:
                content = await f.read()
                return json.loads(content)
        else:
            # Return default if file doesn't exist yet
            return {
                "market_index": 50.0,
                "mode": "NORMAL_MODE",
                "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            }
    except Exception as e:
        logger.error(f"Error reading market mode: {e}")
        return {"mode": "NORMAL_MODE", "market_index": 50.0}

def build_ranked_list(primary_entries=None, score_key=None, extras=None, deduplicate=True):
    seen = set()
    output = []
    # Ensure primary_entries and extras are not None
    if primary_entries is None: primary_entries = []
    if extras is None: extras = {}

    # Process extras first if they should have some priority or be distinct
    for sym, data in extras.items():
        if deduplicate and sym in seen:
            continue
        # Ensure data is a dict, otherwise skip
        if not isinstance(data, dict):
            logger.warning(f"build_ranked_list: Extra item for {sym} is not a dict: {data}")
            continue

        seen.add(sym)
        r15 = data.get("returns_15m", 0.0)
        # Key was 'returns_5m' in merge_filter, but 'returns_3m' in actual data calculation. Standardize.
        # Assuming 'returns_3m' from merge_filter's potential output structure.
        r3_key_options = ["returns_3m", "returns_5m"] 
        r3 = next((data.get(k, 0.0) for k in r3_key_options if k in data), 0.0)

        # Determine score: use the one with larger absolute magnitude, or based on some priority
        score_val = r15 if abs(r15) >= abs(r3) else r3 # Example: larger magnitude dominates
        output.append({
            "symbol": sym, "rank": len(output) + 1, "score": score_val, "source": "returns"
        })

    # Process primary_entries
    for entry in primary_entries:
        # Ensure entry is a dict and has 'symbol'
        if not isinstance(entry, dict) or "symbol" not in entry:
            logger.warning(f"build_ranked_list: Primary entry is invalid: {entry}")
            continue
        sym = entry["symbol"]
        if deduplicate and sym in seen:
            continue
        seen.add(sym)
        output.append({
            "symbol": sym, "rank": len(output) + 1, 
            "score": entry.get(score_key, 0.0), # Get score using score_key
            "source": "ranking"
        })
    # Sort final list by rank (which is based on processing order) or by score if needed
    # Current logic implies order of processing = rank. If sorting by score is desired:
    # output.sort(key=lambda x: x.get("score", 0.0), reverse=True) # Example: descending score
    return output

def inject_return_based_scores(score_dict, merged_dict, prefer="max"):
    for sym, d_val in merged_dict.items(): # Renamed d to d_val
        if sym in score_dict: # Skip if symbol already has a score from primary source
            continue
        if not isinstance(d_val, dict): continue # Skip if data is not a dict

        r15 = d_val.get("returns_15m", 0.0)
        # Handle potential key name variations for 3m/5m returns
        r3_key_options = ["returns_3m", "returns_5m"]
        r_other = next((d_val.get(k, 0.0) for k in r3_key_options if k in d_val), 0.0)
        
        # Ensure values are numeric before comparison
        r15 = r15 if pd.notna(r15) else 0.0
        r_other = r_other if pd.notna(r_other) else 0.0

        if prefer == "max":
            score_dict[sym] = max(r15, r_other)
        elif prefer == "min": # Assuming this implies for negative scores, more negative is "better"
            score_dict[sym] = min(r15, r_other)
        else: # Default or other logic
            score_dict[sym] = r15 # Fallback to 15m return

def rotate_files(file_to_rotate_base_path_str: str, max_versions: int = MAX_FILES):
    """Rotates files, keeping the last `max_versions`.
    Assumes timestamped files like `basename_timestamp.json`.
    The `file_to_rotate_base_path_str` is the NEWEST file just saved, e.g., `winners_15m_1699999999.json`.
    This function will then manage older `winners_15m_*.json` files.
    """
    try:
        base_dir = os.path.dirname(file_to_rotate_base_path_str)
        base_filename_parts = os.path.basename(file_to_rotate_base_path_str).split('_')
        
        # Infer prefix: everything before the last underscore (timestamp)
        if len(base_filename_parts) < 2 or not base_filename_parts[-1].replace('.json','').isdigit():
            logger.warning(f"rotate_files: Cannot infer prefix from {file_to_rotate_base_path_str}. Skipping rotation.")
            return
        
        prefix = "_".join(base_filename_parts[:-1]) + "_" # e.g., "winners_15m_"

        # List all files matching the prefix in the directory
        existing_files = [
            f for f in os.listdir(base_dir) 
            if f.startswith(prefix) and f.endswith(".json") and os.path.isfile(os.path.join(base_dir, f))
        ]

        # Sort by timestamp (assumed to be part of filename before .json)
        # from oldest to newest to make deletion of oldest easy
        def get_timestamp_from_filename(fname_str):
            try:
                return int(fname_str.replace(prefix, "").replace(".json", ""))
            except ValueError:
                return 0 # Fallback for unparseable names
        
        existing_files.sort(key=get_timestamp_from_filename)

        # Prune if too many files
        while len(existing_files) > max_versions:
            oldest_file_to_remove = existing_files.pop(0) # Remove from start of list (oldest)
            try:
                os.remove(os.path.join(base_dir, oldest_file_to_remove))
                logger.debug(f"Rotated (removed) oldest file: {oldest_file_to_remove}")
            except OSError as e_remove:
                logger.error(f"Error removing rotated file {oldest_file_to_remove}: {e_remove}")
                # Break if removal fails to prevent potential infinite loop if perms are bad
                break 
                
    except Exception as e_rotate:
        logger.error(f"Error in rotate_files for {file_to_rotate_base_path_str}: {e_rotate}")

async def save_market_data():    
    global symbols_ang_long_list, symbols_ang_short_list, symbols_inf_long_list, symbols_inf_short_list
    global to_save_top15_15m, to_save_bottom15_15m, to_save_top30_r, to_save_bottom30_r, to_save_top30, to_save_bottom30
    global live_usdc_pairs
    
    try:
        timestamp = int(time.time())
        winners_15m_ts_path  = WINNERS_15M_FILE.parent / f"{WINNERS_15M_FILE.name}_{timestamp}.json"
        losers_15m_ts_path   = LOSERS_15M_FILE.parent / f"{LOSERS_15M_FILE.name}_{timestamp}.json"
        winners_30r_ts_path = WINNERS_30R_FILE.parent / f"{WINNERS_30R_FILE.name}_{timestamp}.json"
        losers_30r_ts_path  = LOSERS_30R_FILE.parent / f"{LOSERS_30R_FILE.name}_{timestamp}.json"
        winners_30_ts_path   = WINNERS_30_FILE.parent / f"{WINNERS_30_FILE.name}_{timestamp}.json"
        losers_30_ts_path    = LOSERS_30_FILE.parent / f"{LOSERS_30_FILE.name}_{timestamp}.json"
        def get_usdc_corrected_list(dict_list, usdc_pairs):
            updated_list = []
            seen_symbols = set()
            for item in dict_list:
                if "symbol" in item:
                    new_sym = force_usdc_if_needed(item["symbol"], usdc_pairs)
                    # Create a copy to avoid mutating original if needed, 
                    # but here we want the saved data to be USDC
                    new_item = item.copy()
                    new_item["symbol"] = new_sym
                    if new_sym not in seen_symbols:
                        updated_list.append(new_item)
                        seen_symbols.add(new_sym)
            return updated_list

        # 1. Prepare data for saving (Use local vars for the corrected data)
        #    This prevents messing up the global references for the next cycle
        save_top15_15m = get_usdc_corrected_list(to_save_top15_15m, live_usdc_pairs)
        save_bot15_15m = get_usdc_corrected_list(to_save_bottom15_15m, live_usdc_pairs)
        save_top30_r   = get_usdc_corrected_list(to_save_top30_r, live_usdc_pairs)
        save_bot30_r   = get_usdc_corrected_list(to_save_bottom30_r, live_usdc_pairs)
        save_top30     = get_usdc_corrected_list(to_save_top30, live_usdc_pairs)
        save_bot30     = get_usdc_corrected_list(to_save_bottom30, live_usdc_pairs)

        # DEBUG: Log sizes
        logger.info(f"💾 Saving Market Data to {WINNERS_15M_FILE.parent}:")
        logger.info(f"   - Winners 15m: {len(save_top15_15m)} items")
        logger.info(f"   - Winners 30 : {len(save_top30)} items")
        
        if len(save_top30) == 0:
            logger.warning("⚠️ WARNING: save_top30 is empty! Winners files will be empty.")

        # 2. Sync String Lists (The "Symbol" files) - Update Globals In-Place
        #    Using [:] ensures we keep the same object reference
        symbols_inf_long_list[:] = list(set(force_usdc_in_list(symbols_inf_long_list, live_usdc_pairs)))
        symbols_inf_short_list[:] = list(set(force_usdc_in_list(symbols_inf_short_list, live_usdc_pairs)))
        symbols_ang_short_list[:] = list(set(force_usdc_in_list(symbols_ang_short_list, live_usdc_pairs)))
        symbols_ang_long_list[:] = list(set(force_usdc_in_list(symbols_ang_long_list, live_usdc_pairs)))
        all_active_symbols = set()
        all_active_symbols.update(symbols_inf_long_list)
        all_active_symbols.update(symbols_inf_short_list)
        all_active_symbols.update(symbols_ang_long_list)
        all_active_symbols.update(symbols_ang_short_list)

        # Always include symbols with open positions so they get fresh indicators
        # even if they've fallen off the ranked lists
        try:
            _mdata_path = BASE_PATH / "data" / "latest_market_data.json"
            if _mdata_path.exists():
                import orjson as _orjson
                _md = _orjson.loads(_mdata_path.read_bytes())
                for _sym, _sdata in _md.items():
                    if abs(float(_sdata.get('positionAmt', 0) or 0)) > 0:
                        all_active_symbols.add(_sym)
        except Exception as _e:
            logger.warning(f"[symbols_active] Could not add open-position symbols: {_e}")

        # Convert to sorted list for consistent saving
        symbols_active_list = sorted(list(all_active_symbols))
        # 3. Helper to save JSON
        async def _save_json_async(file_p: Path, data_to_save: Any):
            try:
                # Ensure parent dir exists
                file_p.parent.mkdir(parents=True, exist_ok=True)
                
                serializable_data = recursively_convert_np(data_to_save)
                async with aiofiles.open(file_p, "wb") as af:
                    await af.write(orjson.dumps(serializable_data, option=orjson.OPT_INDENT_2))  # type: ignore # pylint: disable=no-member,c-extension-no-member
                # logger.debug(f"   Saved {file_p.name}")
            except Exception as e_save_json:
                logger.error(f"Failed to save JSON to {file_p}: {e_save_json}")

        # 4. Save timestamped files
        await _save_json_async(winners_15m_ts_path, save_top15_15m)
        await _save_json_async(losers_15m_ts_path, save_bot15_15m)
        await _save_json_async(winners_30r_ts_path, save_top30_r)
        await _save_json_async(losers_30r_ts_path, save_bot30_r)
        await _save_json_async(winners_30_ts_path, save_top30)
        await _save_json_async(losers_30_ts_path, save_bot30)
        
        # 5. Prune old backups (Keep 5 most recent)
        # Pass the prefix *without* the underscore if the file name is just "winners_15m.json"
        # The backups are "winners_15m.json_TIMESTAMP.json"
        await prune_old_backups(WINNERS_15M_FILE.parent, WINNERS_15M_FILE.name + "_", 5)
        await prune_old_backups(LOSERS_15M_FILE.parent, LOSERS_15M_FILE.name + "_", 5)
        await prune_old_backups(WINNERS_30_FILE.parent, WINNERS_30_FILE.name + "_", 5)
        await prune_old_backups(LOSERS_30_FILE.parent, LOSERS_30_FILE.name + "_", 5)
        await prune_old_backups(WINNERS_30R_FILE.parent, WINNERS_30R_FILE.name + "_", 5)
        await prune_old_backups(LOSERS_30R_FILE.parent, LOSERS_30R_FILE.name + "_", 5)

        # 6. Copy to latest files ATOMICALLY
        files_to_copy_map = [
            (winners_15m_ts_path, WINNERS_15M_FILE), (losers_15m_ts_path, LOSERS_15M_FILE),
            (winners_30r_ts_path, WINNERS_30R_FILE), (losers_30r_ts_path, LOSERS_30R_FILE),
            (winners_30_ts_path, WINNERS_30_FILE), (losers_30_ts_path, LOSERS_30_FILE),
        ]
        
        for src_p, dest_p in files_to_copy_map:
            if src_p.exists():
                try:
                    await asyncio.to_thread(cleanup_and_copy, str(src_p), str(dest_p))
                    # logger.debug(f"   Updated latest: {dest_p.name}")
                except Exception as e_copy_latest:
                    logger.error(f"Failed to copy {src_p} to {dest_p}: {e_copy_latest}")
            else:
                logger.error(f"Source file missing for copy: {src_p}")

        # Save symbol lists
        await _save_json_async(BASE_PATH / "symbols_inf_long.json", symbols_inf_long_list)
        await _save_json_async(BASE_PATH / "symbols_inf_short.json", symbols_inf_short_list)
        await _save_json_async(BASE_PATH / "symbols_ang_short.json", symbols_ang_short_list)
        await _save_json_async(BASE_PATH / "symbols_ang_long.json", symbols_ang_long_list)
        await _save_json_async(BASE_PATH / "symbols_active.json", symbols_active_list)

        logger.info(f"✅ [save_market_data] All files saved successfully.")

    except Exception as e:
        logger.error(f"Error saving market data: {e}", exc_info=True)
        
def copy_latest_file(src: str, dest: str): # Type hints for clarity
    try:
        if os.path.exists(src):
            if os.path.lexists(dest): # lexists checks symlinks without following
                os.remove(dest)
            shutil.copyfile(src, dest)
            logger.info(f"Copied {src} to {dest}")
        else:
            logger.warning(f"Source file {src} does not exist, skipping copy.")
    except Exception as e:
        logger.critical(f"Failed to copy {src} to {dest}: {e}")

# recursively_convert_np updated earlier to handle more types
# to_builtin_num is fine

def to_builtin_num(val):
    """
    Convert np.float64 / np.int64 to a built-in float or int.
    If val is None or not a numeric type, just return val itself.
    """
    if isinstance(val, np.floating):
        return float(val)
    elif isinstance(val, np.integer):
        return int(val)
    return val

def get_rank_fields(ranking_info_ref: dict, symbol: str, tf: str) -> dict: # Renamed arg
    # Default values for all fields
    out = {
        "band_score": None, "final_score": None, "final_score_norm": None,
        "final_score_recent_norm": None, # Added from review of build_ranking_info
        "proximity_score": None, "proximity_score_norm": None,
        "rel_vol": None, "rel_gains_norm": None,
        "slopes": None, "r_values": None,
        "trend_val_norm": None, "trend_val_recent_norm": None, # Added
        "linearity_norm":None,
    }
    
    sym_info = ranking_info_ref.get(symbol, {}) # Default to empty dict if symbol not found
    
    # Symbol-level scores (like band_score if it's stored at symbol level)
    out["band_score"] = to_builtin_num(sym_info.get("band_score"))

    # TF-specific scores (most scores are under '3m' or the specific tf)
    # It's common for many summary scores to be nested under a primary TF like '3m'
    # Adjust based on where build_ranking_info places them.
    
    # Get scores primarily from the '3m' sub-dictionary as per build_ranking_info structure
    # If tf passed is different, some scores might not apply or need different fetching.
    # For now, assume most relevant summary scores are in '3m' from build_ranking_info
    data_source_dict = sym_info.get("3m", {}) # Use '3m' as primary source for these scores
    
    out["final_score"]           = to_builtin_num(data_source_dict.get("final_score"))
    out["final_score_norm"]      = to_builtin_num(data_source_dict.get("final_score_norm"))
    out["final_score_recent_norm"]= to_builtin_num(data_source_dict.get("final_score_recent_norm"))
    out["proximity_score"]       = to_builtin_num(data_source_dict.get("proximity_score"))
    out["proximity_score_norm"]  = to_builtin_num(data_source_dict.get("proximity_score_norm"))
    out["rel_vol"]               = to_builtin_num(data_source_dict.get("rel_vol"))
    out["rel_gains_norm"]        = to_builtin_num(data_source_dict.get("rel_gains_norm"))
    out["trend_val_norm"]        = to_builtin_num(data_source_dict.get("trend_val")) # Key is 'trend_val' in build_ranking_info
    out["trend_val_recent_norm"] = to_builtin_num(data_source_dict.get("trend_val_recent"))
    out["linearity_norm"]        = to_builtin_num(data_source_dict.get("linearity_norm"))

    # Slopes and r_values are dicts by TF, stored under '3m' as 'slopes'/'r_values'
    # which themselves contain e.g. slopes['4h'], slopes['1h'] etc.
    out["slopes"]                = data_source_dict.get("slopes") # This will be the full dict {'4h': val, ...}
    out["r_values"]              = data_source_dict.get("r_values")
    
    # If a score is truly specific to the passed 'tf' (and not a summary in '3m'):
    # tf_specific_data = sym_info.get(tf, {})
    # out["some_tf_specific_score"] = to_builtin_num(tf_specific_data.get("some_tf_specific_score"))
    
    return out


async def prune_old_backups(backup_folder_path: Path, prefix: str, max_backups: int = 5):
    """Tiered retention: keep 1 file per bucket (0-15m, 15m-1h, 1h-4h, 4h-1d, 1d-1W), delete the rest."""
    try:
        if not isinstance(backup_folder_path, Path):
            backup_folder_path = Path(backup_folder_path)
        if not backup_folder_path.is_dir():
            return
        all_backups = [p for p in backup_folder_path.iterdir() if p.is_file() and p.name.startswith(prefix) and p.name.endswith(".json")]
        if len(all_backups) <= 5:
            return
        now = time.time()
        buckets = [(0, 900), (900, 3600), (3600, 14400), (14400, 86400), (86400, 604800)]
        def extract_ts(p):
            try:
                return int(p.name.replace(prefix, "").replace(".json", ""))
            except ValueError:
                return 0
        all_backups.sort(key=extract_ts, reverse=True)
        keep = set()
        for bucket_start, bucket_end in buckets:
            for p in all_backups:
                age = now - extract_ts(p)
                if bucket_start <= age < bucket_end:
                    keep.add(p)
                    break
        if all_backups:
            keep.add(all_backups[0])
        deleted = 0
        for p in all_backups:
            if p not in keep:
                try:
                    p.unlink()
                    deleted += 1
                except Exception as ex_unlink:
                    logger.warning(f"Could not remove {p}: {ex_unlink}")
                    break
        if deleted > 0:
            logger.info(f"[prune] {prefix}*: deleted {deleted}, kept {len(keep)}")
    except Exception as e:
        logger.error(f"Error in prune_old_backups for {backup_folder_path}/{prefix}: {e}")

def cleanup_and_copy(src: str, dest: str):
    """
    Atomically copy src to dest. 
    Copies to dest.tmp first, then renames to dest.
    This prevents race conditions where the file reads as empty/missing.
    """
    temp_dest = dest + ".tmp"
    try:
        shutil.copyfile(src, temp_dest)
        # os.replace is atomic on POSIX (Linux/Mac) and effectively atomic for this purpose on modern Windows
        os.replace(temp_dest, dest) 
        # logger.debug(f"Atomically updated {dest}")
    except Exception as e:
        logger.error(f"Failed to atomically copy {src} to {dest}: {e}")
        # Clean up temp file if it was left behind
        if os.path.exists(temp_dest):
            try:
                os.remove(temp_dest)
            except Exception:
                pass

def get_all_legend_handles_labels(axes_list: List[plt.Axes]) -> Tuple[List[Any], List[str]]: # Renamed arg
    handles, labels = [], []
    # Ensure axes_list is iterable, even if it's a single Axes object
    if not isinstance(axes_list, (list, np.ndarray)): # Or however Matplotlib returns multiple axes
        axes_list_iterable = [axes_list]
    else:
        axes_list_iterable = axes_list

    for ax_item in axes_list_iterable: # Renamed ax to ax_item
        if hasattr(ax_item, 'get_legend_handles_labels'):
            h, l = ax_item.get_legend_handles_labels()
            handles.extend(h)
            labels.extend(l)
    return handles, labels


def get_legend_handles_labels(axes_list: List[plt.Axes]) -> Tuple[List[Any], List[str]]: # Renamed arg
    all_handles, all_labels = [], []
    if not isinstance(axes_list, (list, np.ndarray)):
        axes_list_iterable = [axes_list]
    else:
        axes_list_iterable = axes_list

    for ax_item in axes_list_iterable: # Renamed ax
        if hasattr(ax_item, 'get_legend_handles_labels'):
            h, l = ax_item.get_legend_handles_labels()
            for handle, label in zip(h, l):
                if label not in all_labels: # Avoid duplicate labels in the combined legend
                    all_handles.append(handle)
                    all_labels.append(label)
    return all_handles, all_labels


def add_gradient(
    ax: plt.Axes, # Type hint for Axes
    x_left: Union[pd.Timestamp, float, np.datetime64], 
    x_right: Union[pd.Timestamp, float, np.datetime64],
    y_min: float, y_max: float,
    gradient_vals: np.ndarray, # Expect numpy array
    slices: int=350, alpha: float=0.04, zorder: int=-10, is_vertical: bool=True
):
    if gradient_vals is None or len(gradient_vals) < 2: return
    
    # Convert x_left, x_right to numeric if they are timestamps for calculations
    # Matplotlib handles plotting with datetime objects, but for interpolation span, numeric might be easier.
    # Or, use Timedelta arithmetic directly if x_left/right are pd.Timestamp.
    
    is_timestamp_x = isinstance(x_left, (pd.Timestamp, np.datetime64, datetime))

    if is_timestamp_x:
        # Ensure they are pd.Timestamp for timedelta arithmetic
        x_left_ts = pd.Timestamp(x_left)
        x_right_ts = pd.Timestamp(x_right)
        if x_left_ts is pd.NaT or x_right_ts is pd.NaT: return

        total_span_delta = x_right_ts - x_left_ts
        if total_span_delta.total_seconds() <= 0: return
    else: # Assume numeric (e.g. mdates.date2num)
        total_span_numeric = float(x_right) - float(x_left)
        if total_span_numeric <= 0: return

    # Normalize gradient_vals to [0, 1] for color mapping, if not already.
    # Assuming gradient_vals are e.g. proximity_score_norm which might be [-100, 100].
    # Map this range to [0,1] for (R,G,B) = (c, 1-c, 0)
    # If gradient_vals are already [0,1], this step is simpler.
    # Let's assume gradient_vals is proximity_score_norm from df_tf, normalized from -100 to 100.
    # We need to map this to a [0,1] range for the (c, 1-c, 0) color scheme.
    # E.g., map -100 (red) to c=1, +100 (green) to c=0. Mid (0) to c=0.5.
    # c = (100 - score) / 200
    
    # Interpolate gradient_vals across the x-axis range
    # x_coords_for_interp are normalized [0,1] positions across the plot width
    # gradient_x_coords are normalized [0,1] positions of the provided gradient_vals
    gradient_x_coords = np.linspace(0, 1, len(gradient_vals))


    for i in range(slices):
        fracA = i / slices
        fracB = (i+1) / slices

        if is_timestamp_x:
            xA_ts = x_left_ts + fracA * total_span_delta
            xB_ts = x_left_ts + fracB * total_span_delta
            # For fill_betweenx, matplotlib can handle datetime objects directly
            xA_plot, xB_plot = xA_ts, xB_ts
        else: # Numeric x-axis
            xA_plot = float(x_left) + fracA * total_span_numeric
            xB_plot = float(x_left) + fracB * total_span_numeric

        # Interpolate the original gradient value at the midpoint of the slice
        slice_mid_frac = (fracA + fracB) / 2.0
        g_val_interpolated = np.interp(slice_mid_frac, gradient_x_coords, gradient_vals)
        
        # Map g_val_interpolated (e.g., -100 to 100) to a color factor c (0 to 1)
        # For (Red, Green, Blue) = (c, 1-c, 0):
        # score = +100 (strong positive prox, green) => c = 0  => (0,1,0) Green
        # score = -100 (strong negative prox, red)   => c = 1  => (1,0,0) Red
        # score = 0    (neutral prox, yellow-ish) => c = 0.5 => (0.5,0.5,0) Yellow
        color_factor_c = (100.0 - g_val_interpolated) / 200.0
        color_factor_c = max(0.0, min(1.0, color_factor_c)) # Clamp to [0,1]
        
        plot_color = (color_factor_c, 1.0 - color_factor_c, 0.0)

        if is_vertical:
            ax.fill_betweenx([y_min, y_max], xA_plot, xB_plot, color=plot_color, alpha=alpha, zorder=zorder,linewidth=0.0) # No edge
        else: # Horizontal gradient (fill_between)
            # This would require yA, yB based on fracA/B and x_min, x_max for the fill
            # The current function seems designed for vertical slices along x-axis.
            pass # Not implemented for horizontal


async def test_plot_stoch_crossovers(symbol: str, dfs: dict, output_dir: str = "./plots"):
    return
    """
    Simple test function to plot just price line and stochastic crossovers.
    This helps debug why stochastic crossovers aren't visible in the main plots.
    """
    import os
    from pathlib import Path

    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    
    tfs = ["3m", "15m"]  # Focus on the most relevant timeframes
    valid_tfs_with_data = [tf_loop for tf_loop in tfs if tf_loop in dfs and isinstance(dfs[tf_loop], pd.DataFrame) and not dfs[tf_loop].empty]
    
    if not valid_tfs_with_data:
        logger.warning(f"[test_plot_stoch_crossovers] => No valid TF data for {symbol}, skipping.")
        return
    
    fig, axes = plt.subplots(len(valid_tfs_with_data), 1, figsize=(14, 6 * len(valid_tfs_with_data)), dpi=150, sharex=False)
    if len(valid_tfs_with_data) == 1:
        axes = [axes]
    
    # Dark theme
    fig.patch.set_facecolor("#272d30")
    for ax_plot in axes:
        ax_plot.set_facecolor("#272d30")
        for spine in ax_plot.spines.values(): 
            spine.set_color("#d0d0d0")
        ax_plot.tick_params(axis="x", colors="#d0d0d0")
        ax_plot.tick_params(axis="y", colors="#d0d0d0")
        ax_plot.xaxis.label.set_color("#d0d0d0")
        ax_plot.yaxis.label.set_color("#d0d0d0")
        ax_plot.title.set_color("#9c864e")
        ax_plot.grid(color="#4a4a4a", alpha=0.6, linestyle=':')
    
    for i, tf_plot_loop in enumerate(valid_tfs_with_data):
        ax_plot = axes[i]
        df_tf = dfs[tf_plot_loop].copy()
        
        # Limit to last 24 hours for 3m, 7 days for 15m
        ndays = 1 if tf_plot_loop == "3m" else 7
        if not df_tf.empty and pd.notna(df_tf["close_time"].max()):
            cutoff_ts = pd.to_datetime(df_tf["close_time"].max()) - pd.Timedelta(days=ndays)
            df_tf = df_tf[df_tf["close_time"] >= cutoff_ts].copy()
        
        if len(df_tf) < 2:
            ax_plot.set_title(f"{symbol} {tf_plot_loop}: Insufficient data to plot.")
            continue
        
        # Ensure required columns exist
        for col_check in ["high", "low", "close"]:
            if col_check not in df_tf.columns:
                if 'close' in df_tf.columns: 
                    df_tf[col_check] = df_tf['close']
                else: 
                    ax_plot.set_title(f"{symbol} {tf_plot_loop}: Missing price data.")
                    continue
        
        df_tf['close_time'] = pd.to_datetime(df_tf['close_time'], errors='coerce')
        df_tf.dropna(subset=['close_time'], inplace=True)
        df_tf.sort_values("close_time", inplace=True)
        
        # Calculate stochastic indicators using EXACT same method as ez_crosses.py
        try:
            from ta.momentum import RSIIndicator, StochasticOscillator

            # Step 1: Calculate RSI (same as ez_crosses.py)
            rsi = RSIIndicator(close=df_tf['close'], window=14).rsi()
            
            if rsi is not None and not rsi.empty:
                # Step 2: Calculate Stochastic RSI (EXACT same as ez_crosses.py)
                stoch_rsi = StochasticOscillator(
                    high=rsi,
                    low=rsi,
                    close=rsi,
                    window=14,
                    smooth_window=3
                ).stoch()
                
                if stoch_rsi is not None and not stoch_rsi.empty:
                    # Add to dataframe (ez_crosses.py returns numpy array, we need series)
                    df_tf['stoch_rsi'] = stoch_rsi
                    
                    # Use EXACT same crossover detection logic as ez_crosses.py
                    # Crossover: prev_stoch < 20 and curr_stoch > 20
                    # Crossunder: prev_stoch > 80 and curr_stoch < 80
                    prev_stoch = stoch_rsi.shift(1)
                    curr_stoch = stoch_rsi
                    
                    stoch_crossover = (prev_stoch < 20) & (curr_stoch > 20)
                    stoch_crossunder = (prev_stoch > 80) & (curr_stoch < 80)
                    
                    df_tf['stoch_crossover'] = stoch_crossover
                    df_tf['stoch_crossunder'] = stoch_crossunder
                    
                    logger.info(f"[test_plot_stoch_crossovers] ez_crosses.py method calculated for {symbol} {tf_plot_loop}: StochRSI={stoch_rsi.iloc[-1]:.2f}")
                else:
                    logger.warning(f"[test_plot_stoch_crossovers] StochasticOscillator returned None/empty for {symbol} {tf_plot_loop}")
                    # Fallback to dummy values
                    df_tf['stoch_rsi'] = np.nan
                    df_tf['stoch_crossover'] = False
                    df_tf['stoch_crossunder'] = False
            else:
                logger.warning(f"[test_plot_stoch_crossovers] RSI calculation returned None/empty for {symbol} {tf_plot_loop}")
                # Fallback to dummy values
                df_tf['stoch_rsi'] = np.nan
                df_tf['stoch_crossover'] = False
                df_tf['stoch_crossunder'] = False
            
        except Exception as e:
            logger.error(f"Error calculating stochastic using ez_crosses.py method for {symbol} {tf_plot_loop}: {e}")
            # Fallback to dummy values
            df_tf['stoch_rsi'] = np.nan
            df_tf['stoch_crossover'] = False
            df_tf['stoch_crossunder'] = False
        
        # Set up plot limits
        x_left_ts = df_tf["close_time"].iloc[0]
        x_right_ts = df_tf["close_time"].iloc[-1]
        ax_plot.set_xlim(x_left_ts, x_right_ts)
        
        ymin_data = pd.to_numeric(df_tf["low"], errors='coerce').min()
        ymax_data = pd.to_numeric(df_tf["high"], errors='coerce').max()
        
        if pd.isna(ymin_data) or pd.isna(ymax_data) or np.isclose(ymin_data, ymax_data):
            current_close_val = pd.to_numeric(df_tf["close"], errors='coerce').iloc[-1] if not df_tf.empty and 'close' in df_tf.columns else 1.0
            if pd.isna(current_close_val):
                current_close_val = 1.0
            ymin_data = current_close_val * 0.98
            ymax_data = current_close_val * 1.02
        
        range_span = ymax_data - ymin_data
        margin_abs_val = (0.012 * range_span) if range_span > 1e-9 else 0.02
        ax_plot.set_ylim(ymin_data - margin_abs_val, ymax_data + margin_abs_val)
        
        # Plot price line
        ax_plot.plot(df_tf["close_time"], df_tf["close"], color="#c0c0c0", lw=1.5, label="Close Price")
        
        # Plot stochastic crossovers (more opaque) - only if proper data exists
        if 'stoch_crossover' in df_tf.columns and 'stoch_crossunder' in df_tf.columns:
            crossover_points = df_tf[df_tf['stoch_crossover'] == True]
            crossunder_points = df_tf[df_tf['stoch_crossunder'] == True]
            
            if not crossover_points.empty:
                ax_plot.scatter(crossover_points["close_time"], crossover_points["close"], 
                              color="#00bcd4", marker="o", s=18, zorder=10, label="StochRSI Crossover", alpha=0.9)
                logger.info(f"[test_plot_stoch_crossovers] Found {len(crossover_points)} StochRSI crossover points in {symbol} {tf_plot_loop}")
            
            if not crossunder_points.empty:
                ax_plot.scatter(crossunder_points["close_time"], crossunder_points["close"], 
                              color="#ff4dff", marker="o", s=18, zorder=10, label="StochRSI Crossunder", alpha=0.9)
                logger.info(f"[test_plot_stoch_crossovers] Found {len(crossunder_points)} StochRSI crossunder points in {symbol} {tf_plot_loop}")
        
        # Add WT signal detection
        try:
            # Calculate WT indicators if not present
            if 'wt1' not in df_tf.columns or 'wt2' not in df_tf.columns:
                wt1, wt2 = calculate_wavetrend(df_tf)
                if wt1 is not None and wt2 is not None:
                    df_tf['wt1'] = wt1
                    df_tf['wt2'] = wt2
            
            # Detect WT crossovers
            wt_crossover = (df_tf['wt1'].shift(1) <= df_tf['wt2'].shift(1)) & (df_tf['wt1'] > df_tf['wt2'])
            wt_crossunder = (df_tf['wt1'].shift(1) >= df_tf['wt2'].shift(1)) & (df_tf['wt1'] < df_tf['wt2'])
            
            # Plot WT crossovers
            wt_crossover_points = df_tf[wt_crossover]
            wt_crossunder_points = df_tf[wt_crossunder]
            
            if not wt_crossover_points.empty:
                ax_plot.scatter(wt_crossover_points["close_time"], wt_crossover_points["close"], 
                              color="#3fa7ff", marker="^", s=22, zorder=10, label="WT Crossover", alpha=0.88, linewidths=0.6)
                logger.info(f"[test_plot_stoch_crossovers] Found {len(wt_crossover_points)} WT crossover points in {symbol} {tf_plot_loop}")
            
            if not wt_crossunder_points.empty:
                ax_plot.scatter(wt_crossunder_points["close_time"], wt_crossunder_points["close"], 
                              color="#ff6ec7", marker="v", s=22, zorder=10, label="WT Crossunder", alpha=0.88, linewidths=0.6)
                logger.info(f"[test_plot_stoch_crossovers] Found {len(wt_crossunder_points)} WT crossunder points in {symbol} {tf_plot_loop}")
                
        except Exception as e:
            logger.error(f"Error calculating WT for {symbol} {tf_plot_loop}: {e}")
        
        df_tb = detect_tops_bottoms(df_tf.copy(), distance=10 if tf_plot_loop == "3m" else 20, prominence=1e-5)
        tops = df_tb.dropna(subset=["top"])
        bottoms = df_tb.dropna(subset=["bottom"])

        if not bottoms.empty:
            ax_plot.scatter(bottoms["close_time"], bottoms["bottom"], color="#2ecc71", marker="^", s=24, zorder=9, label="Green Arrow", alpha=1.0, edgecolors="white", linewidths=0.6)
        if not tops.empty:
            ax_plot.scatter(tops["close_time"], tops["top"], color="#ff6b6b", marker="v", s=24, zorder=9, label="Red Arrow", alpha=1.0, edgecolors="white", linewidths=0.6)

        # Add dual reversal arrows from detect_arrow_reversals
        try:
            # Use sensitive parameters for reversal detection
            distance = 2 if tf_plot_loop == "3m" else 4
            prominence = 1e-7
            
            # Check for very recent tops (red reversal arrows)
            df_recent = df_tf.tail(30)  # Last 30 candles
            df_tops_bottoms_recent = detect_tops_bottoms(df_recent, distance=distance, prominence=prominence)
            recent_tops = df_tops_bottoms_recent.dropna(subset=["top"])
            
            if not recent_tops.empty:
                # Check if top is in the last 3 candles (very recent)
                last_top_idx = recent_tops.index[-1]
                df_last_idx = df_tops_bottoms_recent.index[-1]
                candles_since_top = df_last_idx - last_top_idx
                
                if candles_since_top <= 2:  # Very recent top
                    recent_top = recent_tops.iloc[-1]
                    
                    # 1. Historical arrow at the actual top (in hindsight) - more opaque
                    ax_plot.scatter(recent_top["close_time"], recent_top["top"], 
                                  color="#ff0000", marker="v", s=35, zorder=12, 
                                  label="Historical Red Arrow", alpha=0.98, edgecolors="white", linewidth=1.5)
                    
                    # 2. Real-time arrow at detection point (current candle or detection candle)
                    detection_time = df_tf["close_time"].iloc[-1]  # Current candle
                    detection_price = df_tf["close"].iloc[-1]  # Current price
                    ax_plot.scatter(detection_time, detection_price, 
                                  color="#ff4444", marker="v", s=25, zorder=11, 
                                  label="Real-time Red Arrow", alpha=0.85, edgecolors="yellow", linewidth=1)
            
            # Check for very recent bottoms (green reversal arrows)
            recent_bottoms = df_tops_bottoms_recent.dropna(subset=["bottom"])
            
            if not recent_bottoms.empty:
                # Check if bottom is in the last 3 candles (very recent)
                last_bottom_idx = recent_bottoms.index[-1]
                df_last_idx = df_tops_bottoms_recent.index[-1]
                candles_since_bottom = df_last_idx - last_bottom_idx
                
                if candles_since_bottom <= 2:  # Very recent bottom
                    recent_bottom = recent_bottoms.iloc[-1]
                    
                    # 1. Historical arrow at the actual bottom (in hindsight) - more opaque
                    ax_plot.scatter(recent_bottom["close_time"], recent_bottom["bottom"], 
                                  color="#00ff00", marker="^", s=35, zorder=12, 
                                  label="Historical Green Arrow", alpha=0.98, edgecolors="white", linewidth=1.5)
                    
                    # 2. Real-time arrow at detection point (current candle or detection candle)
                    detection_time = df_tf["close_time"].iloc[-1]  # Current candle
                    detection_price = df_tf["close"].iloc[-1]  # Current price
                    ax_plot.scatter(detection_time, detection_price, 
                                  color="#44ff44", marker="^", s=25, zorder=11, 
                                  label="Real-time Green Arrow", alpha=0.85, edgecolors="yellow", linewidth=1)
                                  
        except Exception as e:
            logger.debug(f"Error adding dual reversal arrows for {symbol} {tf_plot_loop}: {e}")

        # Add stochastic lines as subplot
        ax2 = ax_plot.twinx()
        ax2.set_facecolor("#272d30")
        ax2.tick_params(axis="y", colors="#d0d0d0")
        ax2.yaxis.label.set_color("#d0d0d0")
        ax2.set_ylim(0, 100)
        ax2.set_ylabel("Stochastic %")
        
        # Add horizontal lines for overbought/oversold
        ax2.axhline(y=80, color="red", linestyle="--", alpha=0.2)
        ax2.axhline(y=20, color="green", linestyle="--", alpha=0.2)
        
        # Set title
        title_str = f"{symbol} {tf_plot_loop} - Price & Stochastic Crossovers"
        ax_plot.set_title(title_str, fontsize=12)
        
        # Format x-axis
        ax_plot.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M %d-%b'))
        ax_plot.tick_params(axis='x', rotation=15, labelsize=8)
        ax_plot.tick_params(axis='y', labelsize=8)
    
    # Add legend
    handles1, labels1 = axes[0].get_legend_handles_labels()
    handles2, labels2 = axes[0].right_ax.get_legend_handles_labels() if hasattr(axes[0], 'right_ax') else ([], [])
    all_handles = handles1 + handles2
    all_labels = labels1 + labels2
    
    if all_handles:
        fig.legend(all_handles, all_labels, loc='upper center', 
                   bbox_to_anchor=(0.5, 0.95),
                   ncol=min(len(all_labels), 6),
                   fontsize=8, frameon=False, labelcolor='#d0d0d0')
    
    fig.tight_layout(rect=[0, 0.03, 1, 0.95])
    
    # Save plot
    os.makedirs(output_dir, exist_ok=True)
    fpath = Path(output_dir) / f"test_stoch_{symbol}.png"
    try:
        fig.savefig(fpath)
        logger.info(f"[test_plot_stoch_crossovers] Saved test plot to {fpath}")
    except Exception as e_save:
        logger.error(f"Failed to save test plot {fpath}: {e_save}")
    finally:
        plt.close(fig)


async def fetch_crypto_trades_from_log(symbol: str, log_path: str = "~/logs/actions.log"):
    """
    Parses actions.log for crypto trades.
    Regex handles 'augmented' (Buy) and 'reduced' (Sell) for Crypto symbols.
    """
    trades = []
    expanded_path = os.path.expanduser(log_path)
    if not os.path.exists(expanded_path):
        return trades

    # Matches: 2026-02-06 20:56:09 ... trb:BTCUSDC_LONG: ... augmented. ... Augment by: 150.25
    pattern = re.compile(
        r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*trb:(?P<sym>[A-Z0-9]+)_.*"
        r"Position was (?P<action>augmented|reduced).*"
        r"(?:Augment|Reduced) by: (?P<amount>[\d.]+)"
    )

    try:
        with open(expanded_path, 'r') as f:
            for line in f:
                # Basic check for performance before running regex
                if symbol in line and "trb:" in line:
                    match = pattern.search(line)
                    if match:
                        # Extract data
                        t_ts = pd.to_datetime(match.group('ts')).replace(tzinfo=timezone.utc)
                        action = match.group('action') 
                        amount = float(match.group('amount'))
                        
                        trades.append({
                            'timestamp': t_ts,
                            'type': 'buy' if action == 'augmented' else 'sell',
                            'value': amount
                        })
    except Exception as e:
        logger.error(f"Error reading crypto logs: {e}")
    return trades

async def plot_dfs_subplots(
    rank_label: str, symbol: str, dfs: dict, final_score: float, 
    final_score_norm_arg: float, final_score_recent_arg: float,
    prox_norm_arg: float, band_score_arg: float, trend_val_arg: float, rp_global_arg: float,
    proximity_score_norm: float = 0.0,
    rel_volume: float = 0.0, slope_str: str = "", highlight_heatmap: bool = True,
    linear_val: float = 0.0, rel_gains: float = 0.0,
    output_dir: str = "./plots", RANKING_INFO_ARG: dict = None
):
    tfs = ["4h", "1h", "15m", "3m"]
    valid_tfs_with_data = [tf_loop for tf_loop in tfs if tf_loop in dfs and isinstance(dfs[tf_loop], pd.DataFrame) and not dfs[tf_loop].empty]
    price_result, _ = await get_current_price(symbol)
    current_price = price_result if price_result else None
    if not valid_tfs_with_data:
        logger.warning(f"[plot_dfs_subplots] => No valid TF data for {rank_label} {symbol}, skipping.")
        return
    crypto_trades = await fetch_crypto_trades_from_log(symbol)

    fig, axes = plt.subplots(len(valid_tfs_with_data), 1, figsize=(14, 4 * len(valid_tfs_with_data)), dpi=110, sharex=False)
    if len(valid_tfs_with_data) == 1:
        axes = [axes]
    
    fig.patch.set_facecolor("#272d30")
    for ax_plot in axes:
        ax_plot.set_facecolor("#272d30")
        for spine in ax_plot.spines.values(): spine.set_color("#d0d0d0")
        ax_plot.tick_params(axis="x", colors="#d0d0d0"); ax_plot.tick_params(axis="y", colors="#d0d0d0")
        ax_plot.xaxis.label.set_color("#d0d0d0"); ax_plot.yaxis.label.set_color("#d0d0d0")
        ax_plot.title.set_color("#9c864e")
        ax_plot.grid(color="#4a4a4a", alpha=0.6, linestyle=':')

    def fmt_f(val_fmt):
        try: return f"{float(val_fmt):.2f}"
        except (TypeError, ValueError): return "NaN"

    global signals_data

    # timeframe_days = {"4h": 66, "1h": 14, "15m": 5, "3m": 1}
    plot_signals = True

    for i, tf_plot_loop in enumerate(valid_tfs_with_data):
        ax_plot = axes[i]
        df_tf = dfs[tf_plot_loop].copy()

        # Plot all available data - no filtering to avoid gaps
        # Natural gaps (weekends, holidays) will show but won't be artificially created

        if len(df_tf) < 2:
            ax_plot.set_title(f"{symbol} {tf_plot_loop}: Insufficient data to plot.")
            continue # Skip to the next subplot

        # The rest of the function will now only execute if there is enough data
        for col_check in ["high", "low"]:
            if col_check not in df_tf.columns:
                if 'close' in df_tf.columns: df_tf[col_check] = df_tf['close']
                else: 
                    ax_plot.set_title(f"{symbol} {tf_plot_loop}: Missing price data."); continue 
        
        df_tf['close_time'] = pd.to_datetime(df_tf['close_time'], errors='coerce')
        df_tf.dropna(subset=['close_time'], inplace=True)
        df_tf.sort_values("close_time", inplace=True)

        x_left_ts = df_tf["close_time"].iloc[0]
        x_right_ts = df_tf["close_time"].iloc[-1]
        ax_plot.set_xlim(x_left_ts, x_right_ts) # This line will no longer cause a warning

        ymin_data = pd.to_numeric(df_tf["low"], errors='coerce').min()
        ymax_data = pd.to_numeric(df_tf["high"], errors='coerce').max()
        
        if pd.isna(ymin_data) or pd.isna(ymax_data) or np.isclose(ymin_data, ymax_data):
            current_close_val = pd.to_numeric(df_tf["close"], errors='coerce').iloc[-1] if not df_tf.empty and 'close' in df_tf.columns else 1.0
            if pd.isna(current_close_val):
                current_close_val = 1.0
            ymin_data = current_close_val * 0.98
            ymax_data = current_close_val * 1.02
            if np.isclose(ymin_data, ymax_data):
                ymin_data = 0.9
                ymax_data = 1.1

        range_span = ymax_data - ymin_data
        margin_abs_val = (0.012 * range_span) if range_span > 1e-9 else 0.02
        ax_plot.set_ylim(ymin_data - margin_abs_val, ymax_data + margin_abs_val)
        main_close_line = ax_plot.plot(df_tf["close_time"], df_tf["close"], color="#c0c0c0", lw=1.2, label=f"Close")
        if crypto_trades:
            for trade in crypto_trades:
                t_ts = trade['timestamp']
                
                # Check if trade is within the time bounds of this specific subplot
                if x_left_ts <= t_ts <= x_right_ts:
                    # Color: Blue for Buy, Red for Sell
                    l_color = '#00bfff' if trade['type'] == 'buy' else '#ff4444'
                    
                    # Thickness: Value / 100 (Min 0.8px, Max 6px)
                    # Example: $500 trade = 5.0px thickness
                    l_width = min(max(trade['value'] / 100, 0.8), 6.0)
                    
                    ax_plot.axvline(
                        x=t_ts, 
                        color=l_color, 
                        linewidth=l_width, 
                        alpha=0.6, 
                        zorder=2   )

        if symbol in signals_data and tf_plot_loop in signals_data[symbol]:
            logger.info(
                f"[plot_dfs_subplots] Plotting signals for {symbol}-{tf_plot_loop}: {list(signals_data[symbol][tf_plot_loop].keys())}"
            )
            for etype, ev_list in signals_data[symbol][tf_plot_loop].items():
                for event in ev_list:
                    ts_event = event.get("timestamp")
                    if not isinstance(ts_event, pd.Timestamp):
                        try: ts_event = pd.to_datetime(ts_event); 
                        except Exception: continue
                    if ts_event.tzinfo is None: ts_event = ts_event.tz_localize('UTC')
                    
                    if ts_event < x_left_ts or ts_event > x_right_ts: continue 

                    price_event = event.get("price")
                    reason_event = event.get("reason", etype).lower()
                    action_event = event.get("action", "").upper()
                    
                    marker_style = None; color_style = 'orange'; line_style = 'dotted'; marker_size = 6
                    is_vline = True

                    if "stoch_crossover" in reason_event: 
                        color_style = "cyan"
                        marker_style = 'o'; is_vline = False; marker_size = 4
                    elif "stoch_crossunder" in reason_event: 
                        color_style = "magenta"
                        marker_style = 'o'; is_vline = False; marker_size = 4
                    elif "wt_crossover" in reason_event or "wt crossover" in reason_event:
                        color_style = "lime"
                        marker_style = '^'; is_vline = False; marker_size = 8
                    elif "wt_crossunder" in reason_event or "wt crossunder" in reason_event:
                        color_style = "red"
                        marker_style = 'v'; is_vline = False; marker_size = 8
                    elif "wt strong alert" in reason_event:
                        color_style = "lime" if action_event == "BUY" else "red"
                    elif "winners_up" in reason_event or "losers_down" in reason_event:
                        is_vline = False; marker_style = '^'; color_style = 'lime'
                    elif "winners_down" in reason_event or "losers_up" in reason_event:
                        is_vline = False; marker_style = 'v'; color_style = 'red'
                    
                    if is_vline:
                        ax_plot.axvline(x=ts_event, color=color_style, linestyle=line_style, lw=1, zorder=5)
                    elif marker_style and pd.notna(price_event):
                        y_pos = price_event
                        try:
                            candle_row = df_tf[df_tf['close_time'] >= ts_event].iloc[0]
                            if marker_style == '^':
                                y_pos = candle_row['low'] - margin_abs_val * 0.5
                            elif marker_style == 'v':
                                y_pos = candle_row['high'] + margin_abs_val * 0.5
                        except IndexError:
                            pass
                        ax_plot.plot(ts_event, y_pos, marker=marker_style, color=color_style, ms=marker_size, linestyle='None', zorder=7)

        if len(df_tf) > 2 and 'close' in df_tf.columns:
            try:
                slope_pct_local, rvv_local, yhat_abs_local = calculate_regression_slope_line(df_tf[['close_time','close']].copy())
                
                if len(yhat_abs_local) == len(df_tf):
                    slope_str_local_title = f"Slope={fmt_f(slope_pct_local)}%, R={fmt_f(rvv_local)}"
                    ax_plot.plot(df_tf["close_time"], yhat_abs_local, color="blue", linestyle="--", lw=1, label=f"Reg ({slope_str_local_title})")
                    
                    band_df_local = calculate_regression_band(df_tf.copy())
                    if not band_df_local.empty:
                        ax_plot.fill_between(
                            band_df_local["time"], band_df_local["upperb"], ymax_data + margin_abs_val,
                            color="grey", alpha=0.25, where=(band_df_local["upperb"] < (ymax_data + margin_abs_val)), zorder=-5, linewidth=0.0)
                        ax_plot.fill_between(
                            band_df_local["time"], ymin_data - margin_abs_val, band_df_local["lowerb"],
                            color="grey", alpha=0.25, where=(band_df_local["lowerb"] > (ymin_data - margin_abs_val)), zorder=-5, linewidth=0.0)

            except Exception as e_slope:
                logger.debug(f"[plo t_dfs_subplots] => Slope/Band line error /{tf_plot_loop}: {e_slope}")#{symbol_plot}

        if "stoch_rsi" in df_tf.columns and not df_tf["stoch_rsi"].isnull().all():
            try:
                # Normalize StochRSI to 0-1 range
                stoch_norm = df_tf["stoch_rsi"].fillna(50) / 100.0
                stoch_norm = np.clip(stoch_norm, 0, 1)
                
                # Create RGBA Image Array (Height=1, Width=N_candles)
                # Red=1 (Overbought), Green=0 (Oversold) -> (s, 1-s, 0)
                # This creates the Red-Yellow-Green gradient
                img_data = np.zeros((1, len(stoch_norm), 4))
                img_data[0, :, 0] = stoch_norm.values       # R
                img_data[0, :, 1] = 1.0 - stoch_norm.values # G
                img_data[0, :, 2] = 0.0                     # B
                img_data[0, :, 3] = 0.12                    # Alpha (Transparency)

                # Calculate extent [left, right, bottom, top]
                # Convert timestamps to matplotlib float format
                t0 = mdates.date2num(df_tf["close_time"].iloc[0])
                t1 = mdates.date2num(df_tf["close_time"].iloc[-1])
                
                # Render as a single image instead of 3000+ polygons
                ax_plot.imshow(
                    img_data, 
                    extent=[t0, t1, ymin_data - margin_abs_val, ymax_data + margin_abs_val], 
                    aspect='auto', 
                    origin='lower',
                    zorder=-15,
                    interpolation='bilinear' # Smooths the transition between candles
                )
            except Exception as e_grad:
                logger.debug(f"[pl ot_dfs_subplots] Gradient error: {e_grad}")
        # ----------------------------------------------------------

        df_tf_tops_bottoms = detect_tops_bottoms(df_tf.copy(), distance=10 if tf_plot_loop == "3m" else 20, prominence=1e-5)
        topdf = df_tf_tops_bottoms.dropna(subset=["top"])
        botdf = df_tf_tops_bottoms.dropna(subset=["bottom"])
        top_size = 15 if tf_plot_loop == "3m" else 20
        bottom_size = 15 if tf_plot_loop == "3m" else 20
        ax_plot.scatter(topdf["close_time"], topdf["top"], color="red", marker="v", s=top_size, zorder=3, label="Top")
        ax_plot.scatter(botdf["close_time"], botdf["bottom"], color="lime", marker="^", s=bottom_size, zorder=3, label="Bottom")

        if highlight_heatmap and "proximity_score_norm" in df_tf.columns and not df_tf["proximity_score_norm"].isnull().all():
            prox_gradient_vals = pd.to_numeric(df_tf["proximity_score_norm"], errors='coerce').fillna(0).values
            if len(prox_gradient_vals) > 1:
                 add_gradient(ax_plot, x_left_ts, x_right_ts,
                             ymin_data - margin_abs_val, ymax_data + margin_abs_val,
                             gradient_vals=prox_gradient_vals, slices=200, alpha=0.025,
                             zorder=-10, is_vertical=True)
        
        bigger_tfs_map = {"1h": ["4h"], "15m": ["1h", "4h"], "3m": ["15m", "1h", "4h"]}
        htfs_to_overlay = bigger_tfs_map.get(tf_plot_loop, [])

        for btf_overlay in htfs_to_overlay:
            if btf_overlay in dfs and isinstance(dfs[btf_overlay], pd.DataFrame) and not dfs[btf_overlay].empty:
                df_btf_data = dfs[btf_overlay].copy()
                
                if 'close_time' in df_btf_data.columns and 'close' in df_btf_data.columns:
                    band_htf_overlay = calculate_regression_band(df_btf_data)
                    if not band_htf_overlay.empty:
                        df_tf_merged_htf_band = merge_htf_band_into_ltf(df_tf.copy(), band_htf_overlay)
                        
                        ax_plot.plot(df_tf_merged_htf_band["close_time"], df_tf_merged_htf_band["upperb"], color="blue", linestyle=":", lw=0.8, alpha=0.5, label=f"{btf_overlay} UB")
                        ax_plot.plot(df_tf_merged_htf_band["close_time"], df_tf_merged_htf_band["lowerb"], color="blue", linestyle=":", lw=0.8, alpha=0.5, label=f"{btf_overlay} LB")
                        
                        ax_plot.fill_between(
                            df_tf_merged_htf_band["close_time"],
                            df_tf_merged_htf_band["upperb"], df_tf_merged_htf_band["lowerb"],
                            color="blue", alpha=0.05, zorder=-8, linewidth=0.0
                        )

        title_str = f"{symbol} {tf_plot_loop}"
        if tf_plot_loop == "3m":
            title_str = (f"{rank_label} {symbol} {tf_plot_loop} FS_N:{fmt_f(final_score_norm_arg)} FS_R:{fmt_f(final_score_recent_arg)} | "
                         f"Prox:{fmt_f(prox_norm_arg)} Band:{fmt_f(band_score_arg)} | "
                         f"Trend:{fmt_f(trend_val_arg)} RPg:{fmt_f(rp_global_arg)}")
        elif tf_plot_loop == "4h":
            title_str = (f"{rank_label} {symbol} {tf_plot_loop} FS_N:{fmt_f(final_score_norm_arg)} FS_R:{fmt_f(final_score_recent_arg)} | "
                         f"RawFS:{fmt_f(final_score)} Lin:{fmt_f(linear_val)} | Slopes(summ): {slope_str[:50]}")
        ax_plot.set_title(title_str, fontsize=14)
        ax_plot.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M %d-%b'))
        ax_plot.tick_params(axis='x', rotation=15, labelsize=8)
        ax_plot.tick_params(axis='y', labelsize=8)

    handles_combined, labels_combined = get_legend_handles_labels(axes)
    if labels_combined:
        fig.legend(handles_combined, labels_combined, loc='upper center', 
                   bbox_to_anchor=(0.5, 1.00),
                   ncol=min(len(labels_combined), 8),
                   fontsize=7, frameon=False, labelcolor='#d0d0d0')

    fig.tight_layout(rect=[0, 0.03, 1, 0.95])

    os.makedirs(output_dir, exist_ok=True)
    safe_rank_label = rank_label.replace("#","_").replace(":","_")
    fpath = Path(output_dir) / f"{safe_rank_label}_{symbol}.png"
    try:
        fig.savefig(fpath)
    except Exception as e_save:
        logger.error(f"Failed to save plot {fpath}: {e_save}")
    finally:
        plt.close(fig)



def cleanup_old_plots(output_dir_str: str, days_to_keep: int = DAYS_PLOT): # Renamed arg
    """Delete plot files older than 'days_to_keep' days from the output directory."""
    output_dir_path = Path(output_dir_str)
    if not output_dir_path.is_dir(): return

    cutoff_time = time.time() - (days_to_keep * 86400)
    for file_path_obj in output_dir_path.glob("*.png"): # Iterate over Path objects
        if file_path_obj.is_file() and file_path_obj.stat().st_mtime < cutoff_time:
            try:
                file_path_obj.unlink() # Use Path.unlink()
                logger.info(f"Deleted old plot: {file_path_obj}")
            except Exception as e_del_plot:
                logger.error(f"Failed to delete {file_path_obj}: {e_del_plot}")

def to_json_safe(val): # This seems like a simpler version of recursively_convert_np
    if isinstance(val, (np.integer)): return int(val)
    if isinstance(val, (np.floating)):
        if np.isnan(val): return None
        if np.isinf(val): return str(val) # Or handle as error/specific value
        return float(val)
    if isinstance(val, (np.bool_)): return bool(val)
    if isinstance(val, pd.Timestamp): return val.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    # For other types, attempt string conversion, but ideally they are handled by a more comprehensive serializer
    try:
        json.dumps(val) # Test if serializable by standard json
        return val
    except TypeError:
        return str(val) # Fallback to string

def build_ranking_info(ranking_data_scalars: List[Dict[str, Any]], timeframes: List[str]) -> Dict[str, Dict[str, Any]]:
    ranking_info_built = defaultdict(lambda: defaultdict(dict)) # Renamed arg
    for item_scalars in ranking_data_scalars:
        sym = item_scalars["symbol"]
        
        # Symbol-level scores (if any are stored directly under symbol)
        # 'band_score' is a good candidate for a symbol-level summary if it's consistent across TFs or a specific one is chosen as 'the' band_score
        ranking_info_built[sym]["band_score"] = to_json_safe(item_scalars.get("band_score", 0.0))
        ranking_info_built[sym]["mtf_band_score"] = to_json_safe(item_scalars.get("mtf_band_score", 0.0))
        # Add any other symbol-level general scores here if applicable from item_scalars
        # ranking_info_built[sym]["some_other_general_score"] = to_json_safe(item_scalars.get("some_other_general_score"))

        # Scores typically summarized under the '3m' timeframe key in RANKING_INFO for convenience.
        # These are often global or aggregated scores but associated with the most granular/relevant TF for easy access.
        scores_for_3m_summary = {
            "final_score": to_json_safe(item_scalars.get("final_score_raw", 0.0)),
            "final_score_norm": to_json_safe(item_scalars.get("final_score_norm", 0.0)),
            "final_score_recent_norm": to_json_safe(item_scalars.get("final_score_recent_norm", 0.0)),
            "proximity_score": to_json_safe(item_scalars.get("weighted_proximity_score_raw", 0.0)), # Raw mean prox for 3m
            "proximity_score_norm": to_json_safe(item_scalars.get("proximity_score_norm", 0.0)), # Normalized mean prox for 3m
            "trend_val": to_json_safe(item_scalars.get("trend_val_norm", 0.0)), # Normalized global trend
            "trend_val_recent": to_json_safe(item_scalars.get("trend_val_recent_norm", 0.0)), # Normalized recent trend
            "linearity_norm": to_json_safe(item_scalars.get("linearity_norm", 0.0)),
            "rel_vol": to_json_safe(item_scalars.get("rel_vol_norm_factor", 1.0)), # This is the vol multiplier/factor
            "rel_gains_norm": to_json_safe(item_scalars.get("rel_gains_norm", 0.0)),
            "slopes": item_scalars.get("slopes_raw", {}), # Dict of raw slopes {tf: val}, keep as dict
            "r_values": item_scalars.get("r_values_raw", {}), # Dict of raw r_values {tf: val}, keep as dict
            "band_score": to_json_safe(item_scalars.get("band_score", 0.0)), # Also include band_score in 3m for get_symbol_3m_data convenience
            "mtf_band_score": to_json_safe(item_scalars.get("mtf_band_score", 0.0)) # Multi-timeframe band score
        }
        ranking_info_built[sym]["3m"].update(scores_for_3m_summary)

        # For other timeframes, store their specific raw slope and r_value if needed for direct access.
        # The 'slopes_raw' and 'r_values_raw' in item_scalars already contain per-TF data.
        # Accessing via RANKING_INFO[sym]['3m']['slopes']['4h'] is one way.
        # If direct RANKING_INFO[sym]['4h']['slope_raw'] is also desired for some reason:
        for tf_loop in timeframes: # tf_loop is "4h", "1h", "15m", "3m"
            if tf_loop not in ranking_info_built[sym]: # Ensure TF dict exists (defaultdict handles this but explicit is fine)
                 ranking_info_built[sym][tf_loop] = {}
            # Store the specific TF's raw slope and r-value under its own TF key
            ranking_info_built[sym][tf_loop]["slope_raw"] = to_json_safe(item_scalars.get("slopes_raw", {}).get(tf_loop, 0.0))
            ranking_info_built[sym][tf_loop]["r_value_raw"] = to_json_safe(item_scalars.get("r_values_raw", {}).get(tf_loop, 0.0))
            
            # If you had other per-TF scores generated in `item_scalars` (e.g., `stoch_rsi_4h_last_value`),
            # you would add them here under `ranking_info_built[sym][tf_loop]`.
            # For example:
            # ranking_info_built[sym][tf_loop]["stoch_rsi_value"] = to_json_safe(item_scalars.get(f"stoch_rsi_{tf_loop}", None))
            
    return ranking_info_built

def find_rank_in_list(symbol_to_find: str, winners_list: List[dict], losers_list: List[dict]) -> Optional[int]:
    if not isinstance(symbol_to_find, str): return None # Basic type check

    # Winners are positive ranks (1st is best winner, rank 1)
    for i, item_dict in enumerate(winners_list):
        if isinstance(item_dict, dict) and item_dict.get("symbol") == symbol_to_find:
            return i + 1 # Rank is 1-based index
            
    # Losers are negative ranks (1st is "best" loser, i.e., most negative score, rank -1)
    for i, item_dict in enumerate(losers_list):
        if isinstance(item_dict, dict) and item_dict.get("symbol") == symbol_to_find:
            return -(i + 1) # Rank is 1-based index, made negative
            
    return None # Symbol not found in either list


def load_last_two_versions(prefix: str, folder_str: str) -> tuple[list, list]:
    folder_path = Path(folder_str)
    if not folder_path.is_dir():
        logger.warning(f"load_last_two_versions: Folder {folder_str} not found.")
        return [], []

    all_files_paths = [
        p for p in folder_path.iterdir()
        if p.is_file() and p.name.startswith(prefix) and p.name.endswith(".json")
    ]
    
    if not all_files_paths: return [], []

    all_files_paths.sort(key=lambda p: p.stat().st_mtime, reverse=True) # Most recent first
    
    curr_data = []
    prev_data = []

    def _load_json_from_path_internal(p_load: Path) -> Optional[list]: # Renamed to avoid conflict
        try:
            with open(p_load, "rb") as f_load: # Read as bytes for orjson
                file_content = f_load.read()
                if len(file_content) == 0:
                    logger.warning(f"load_last_two_versions: Empty file {p_load}, trying next version")
                    return None # Signal to caller to try next file
                loaded_content = orjson.loads(file_content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
            if isinstance(loaded_content, list):
                return [item for item in loaded_content if isinstance(item, dict)]
            elif isinstance(loaded_content, dict) and "symbol" in loaded_content:
                return [loaded_content]
            logger.warning(f"load_last_two_versions: Expected list from {p_load}, got {type(loaded_content)}. Returning empty list.")
            return []
        except Exception as e_load_json_internal:
            logger.error(f"load_last_two_versions: Failed to load/parse {p_load}: {e_load_json_internal}, trying next version")
            return None # Signal to caller to try next file

    if len(all_files_paths) >= 1:
        curr_data = _load_json_from_path_internal(all_files_paths[0])
        if curr_data is None:
            for alt_file in all_files_paths[1:]: # Try remaining files
                curr_data = _load_json_from_path_internal(alt_file)
                if curr_data is not None: break
    
    if len(all_files_paths) >= 2:
        prev_data = _load_json_from_path_internal(all_files_paths[1])
        if prev_data is None:
            for alt_file in all_files_paths[2:]: # Try remaining files
                prev_data = _load_json_from_path_internal(alt_file)
                if prev_data is not None: break
        
    return prev_data, curr_data


def calculate_trade_recommendation(
    event_type: str, 
    final_score_norm: float, 
    proximity_score_norm: float, 
    band_score: float, 
    max_usd: float = 600.0,
    min_usd: float = 10.0
) -> dict:
    final_score_norm = final_score_norm if pd.notna(final_score_norm) else 0.0
    proximity_score_norm = proximity_score_norm if pd.notna(proximity_score_norm) else 0.0
    band_score = band_score if pd.notna(band_score) else 0.0

    band_score_capped = np.clip(band_score, -90.0, 90.0) # Cap band_score for combination
    
    combined_confidence = (0.5 * final_score_norm) + \
                          (0.3 * proximity_score_norm) + \
                          (0.2 * band_score_capped)
    combined_confidence = np.clip(combined_confidence, -100.0, 100.0)

    action = "HOLD" 
    # Primary action based on event_type
    if "stoch_crossover" in event_type.lower() or "winners_up" in event_type.lower() or \
       "losers_up" in event_type.lower() or "wt strong alert" in event_type.lower() and combined_confidence > 0: # General BUY triggers
        action = "BUY"
    elif "stoch_crossunder" in event_type.lower() or "winners_down" in event_type.lower() or \
         "losers_down" in event_type.lower() or "wt strong alert" in event_type.lower() and combined_confidence < 0: # General SELL triggers
        action = "SELL"
    
    # Refine action based on combined_confidence magnitude and agreement with trigger
    # If confidence is very low, revert to HOLD
    if abs(combined_confidence) < 15: # Threshold for "too low confidence"
        action = "HOLD"
    # If event suggests BUY but confidence is strongly SELL, or vice-versa (conflict)
    elif action == "BUY" and combined_confidence < -30: # Strong conflict
        action = "HOLD" # Or reduce size drastically / smaller counter-trade based on strategy
    elif action == "SELL" and combined_confidence > 30: # Strong conflict
        action = "HOLD"
    
    frac_confidence = abs(combined_confidence) / 100.0
    usd_range = max_usd - min_usd
    usd_amount = min_usd + frac_confidence * usd_range

    if action == "HOLD":
        usd_amount = 0.0
        
    return {
        "action": action, 
        "usd_amount": round(usd_amount, 2),  
        "combined_confidence": round(combined_confidence, 2)
    }


def detect_fast_15_movers_up_down(new_list_15m: List[dict], old_list_15m: List[dict], top_n: int=5) -> Tuple[List[dict], List[dict]]:
    if not old_list_15m or not new_list_15m: return [], []

    old_rank_map = {item.get("symbol"): i for i, item in enumerate(old_list_15m) if isinstance(item, dict) and "symbol" in item}
    
    deltas = []
    for i, item_new in enumerate(new_list_15m):
        if not isinstance(item_new, dict) or "symbol" not in item_new: continue
        sym = item_new["symbol"]
        
        new_r_idx = i # 0-indexed
        old_r_idx = old_rank_map.get(sym) 
        
        if old_r_idx is None: old_r_idx = len(old_list_15m) 
            
        delta_rank_val = old_r_idx - new_r_idx # Positive: moved up (rank number decreased)
        if delta_rank_val != 0:
            deltas.append({
                "symbol": sym,
                "old_rank_15m": old_r_idx + 1, # 1-based for reporting
                "new_rank_15m": new_r_idx + 1, # 1-based
                "delta_15m": delta_rank_val,   
                "data": item_new 
            })
            
    up_sorted = sorted([d for d in deltas if d["delta_15m"] > 0], key=lambda x: x["delta_15m"], reverse=True)
    fast_up = up_sorted[:top_n]
    
    down_sorted = sorted([d for d in deltas if d["delta_15m"] < 0], key=lambda x: x["delta_15m"])
    fast_down = down_sorted[:top_n]
    
    return fast_up, fast_down


def detect_fast_20_movers_up_down(new_list_20: List[dict], old_list_20: List[dict], top_n: int=5) -> Tuple[List[dict], List[dict]]:
    if not old_list_20 or not new_list_20: return [], []

    old_rank_map = {item.get("symbol"): i for i, item in enumerate(old_list_20) if isinstance(item, dict) and "symbol" in item}
    
    deltas = []
    for i, item_new in enumerate(new_list_20):
        if not isinstance(item_new, dict) or "symbol" not in item_new: continue
        sym = item_new["symbol"]
        
        new_r_idx = i
        old_r_idx = old_rank_map.get(sym)
        
        if old_r_idx is None: old_r_idx = len(old_list_20)
            
        delta_rank_val = old_r_idx - new_r_idx
        if delta_rank_val != 0:
            deltas.append({
                "symbol": sym, "old_rank_20": old_r_idx + 1, "new_rank_20": new_r_idx + 1,
                "delta_20": delta_rank_val, "data": item_new
            })
            
    up_sorted = sorted([d for d in deltas if d["delta_20"] > 0], key=lambda x: x["delta_20"], reverse=True)
    fast_up = up_sorted[:top_n]
    
    down_sorted = sorted([d for d in deltas if d["delta_20"] < 0], key=lambda x: x["delta_20"])
    fast_down = down_sorted[:top_n]
    
    return fast_up, fast_down

def detect_fast_30_movers_up_down(new_list_30: List[dict], old_list_30: List[dict], top_n: int=5) -> Tuple[List[dict], List[dict]]:
    if not old_list_30 or not new_list_30: return [], []

    old_rank_map = {item.get("symbol"): i for i, item in enumerate(old_list_30) if isinstance(item, dict) and "symbol" in item}
    
    deltas = []
    for i, item_new in enumerate(new_list_30):
        if not isinstance(item_new, dict) or "symbol" not in item_new: continue
        sym = item_new["symbol"]
        
        new_r_idx = i
        old_r_idx = old_rank_map.get(sym)
        
        if old_r_idx is None: old_r_idx = len(old_list_30)
            
        delta_rank_val = old_r_idx - new_r_idx
        if delta_rank_val != 0:
            deltas.append({
                "symbol": sym, "old_rank_30": old_r_idx + 1, "new_rank_30": new_r_idx + 1,
                "delta_30": delta_rank_val, "data": item_new
            })
            
    up_sorted = sorted([d for d in deltas if d["delta_30"] > 0], key=lambda x: x["delta_30"], reverse=True)
    fast_up = up_sorted[:top_n]
    
    down_sorted = sorted([d for d in deltas if d["delta_30"] < 0], key=lambda x: x["delta_30"])
    fast_down = down_sorted[:top_n]
    
    return fast_up, fast_down

async def detect_all_arrows_aligned(ranking_data: List[dict], top_n: int = 20) -> Tuple[List[dict], List[dict]]:
    """
    Simple arrow detection: Check last 3 bars of each timeframe (3m, 15m, 1h, 4h).
    If all bars go the same direction (all up or all down), signal.
    If all have had a green up arrow or red down arrow in recent bars, stronger signal.
    Returns: (all_green_list, all_red_list)
    """
    if not ranking_data:
        return [], []
    timeframes_to_check = ["3m", "15m", "1h", "4h"]
    all_green = []
    all_red = []
    for item in ranking_data:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        dfs_for_calc = item.get("dfs_for_calc", {})
        if not symbol or not dfs_for_calc:
            continue
        bars_up = 0; bars_down = 0; arrows_green = 0; arrows_red = 0; valid_tfs = 0
        slopes_dict = item.get("slopes_raw", {})
        slopes_all_positive = True; slopes_all_negative = True; slopes_count = 0
        for tf in timeframes_to_check:
            df = dfs_for_calc.get(tf)
            if df is None or df.empty or len(df) < 3:
                continue
            try:
                last_3 = df.tail(3)
                if len(last_3) < 3 or "close" not in last_3.columns:
                    continue
                closes = last_3["close"].values
                all_up = closes[1] > closes[0] and closes[2] > closes[1]
                all_down = closes[1] < closes[0] and closes[2] < closes[1]
                if all_up: bars_up += 1
                elif all_down: bars_down += 1
                if "bottom" in last_3.columns and last_3["bottom"].notna().any(): arrows_green += 1
                if "top" in last_3.columns and last_3["top"].notna().any(): arrows_red += 1
                valid_tfs += 1
                # Check slope direction for this timeframe
                slope_val = slopes_dict.get(tf, 0.0)
                if slope_val is not None:
                    try:
                        slope_float = float(slope_val)
                        slopes_count += 1
                        if slope_float <= 0: slopes_all_positive = False
                        if slope_float >= 0: slopes_all_negative = False
                    except (TypeError, ValueError):
                        pass
            except Exception as e:
                logger.debug(f"Error checking {symbol} on {tf}: {e}")
                continue
        if valid_tfs < len(timeframes_to_check):
            continue
        # Check if all plots move in same direction (all slopes positive or all negative)
        all_plots_up = slopes_count >= 2 and slopes_all_positive
        all_plots_down = slopes_count >= 2 and slopes_all_negative
        # Emit signal if: (1) all bars/arrows aligned OR (2) all plots move in same direction
        if bars_up == valid_tfs or arrows_green == valid_tfs or all_plots_up:
            all_green.append({
                "symbol": symbol,
                "final_score_norm": item.get("final_score_norm", 0.0),
                "bars_up": bars_up, "arrows_green": arrows_green,
                "confidence": 2.0 if arrows_green == valid_tfs else (1.5 if all_plots_up else 1.0),
                "slopes": {tf: slopes_dict.get(tf, 0.0) for tf in timeframes_to_check},
                "data": item
            })
        elif bars_down == valid_tfs or arrows_red == valid_tfs or all_plots_down:
            all_red.append({
                "symbol": symbol,
                "final_score_norm": item.get("final_score_norm", 0.0),
                "bars_down": bars_down, "arrows_red": arrows_red,
                "confidence": 2.0 if arrows_red == valid_tfs else (1.5 if all_plots_down else 1.0),
                "slopes": {tf: slopes_dict.get(tf, 0.0) for tf in timeframes_to_check},
                "data": item
            })
    all_green_sorted = sorted(all_green, key=lambda x: (x.get("confidence", 0), x.get("final_score_norm", 0)), reverse=True)
    all_red_sorted = sorted(all_red, key=lambda x: (x.get("confidence", 0), -x.get("final_score_norm", 0)))
    return all_green_sorted[:top_n], all_red_sorted[:top_n]


def analyze_timeframe_signal(df_tops_bottoms: pd.DataFrame, price_change_pct: float, timeframe: str) -> dict:
    """Analyze signal for a single timeframe with price amplification."""
    
    # Find most recent signals
    tops = df_tops_bottoms.dropna(subset=["top"])
    bottoms = df_tops_bottoms.dropna(subset=["bottom"])
    
    last_top_idx = tops.index[-1] if not tops.empty else None
    last_bottom_idx = bottoms.index[-1] if not bottoms.empty else None
    
    # Determine signal strength and recency
    signal_strength = calculate_signal_strength(df_tops_bottoms, tops, bottoms)
    price_trend_up = price_change_pct > 0.001
    price_trend_down = price_change_pct < -0.001
    
    # Determine dominant signal with price amplification
    dominant_signal = "neutral"
    confidence = 0.5  # Base confidence
    
    if last_top_idx is None and last_bottom_idx is None:
        # No arrows - rely on price movement
        if price_trend_up:
            dominant_signal = "bottom_amplified"
            confidence = 0.6
        elif price_trend_down:
            dominant_signal = "top_amplified" 
            confidence = 0.6
        else:
            dominant_signal = "neutral"
            confidence = 0.3
    elif last_bottom_idx is None:
        # Only tops detected
        if price_trend_up:
            dominant_signal = "bottom_amplified"  # Price contradicts top signal
            confidence = 0.7
        else:
            dominant_signal = "top"
            confidence = 0.8 + signal_strength
    elif last_top_idx is None:
        # Only bottoms detected
        if price_trend_down:
            dominant_signal = "top_amplified"  # Price contradicts bottom signal
            confidence = 0.7
        else:
            dominant_signal = "bottom"
            confidence = 0.8 + signal_strength
    else:
        # Both signals detected - check recency
        if last_bottom_idx > last_top_idx:
            if price_trend_down:
                dominant_signal = "top_amplified"  # Price overrides recent bottom
                confidence = 0.7
            else:
                dominant_signal = "bottom"
                confidence = 0.8 + signal_strength
        else:
            if price_trend_up:
                dominant_signal = "bottom_amplified"  # Price overrides recent top
                confidence = 0.7
            else:
                dominant_signal = "top"
                confidence = 0.8 + signal_strength
    
    return {
        "dominant_signal": dominant_signal,
        "confidence": min(confidence, 1.0),  # Cap at 1.0
        "last_top_idx": last_top_idx,
        "last_bottom_idx": last_bottom_idx,
        "price_change_pct": price_change_pct,
        "signal_strength": signal_strength
    }


def calculate_signal_strength(df_tops_bottoms: pd.DataFrame, tops: pd.DataFrame, bottoms: pd.DataFrame) -> float:
    """Calculate signal strength based on prominence and recency."""
    if df_tops_bottoms.empty:
        return 0.0
    
    strength = 0.0
    
    # Check most recent signals
    recent_data = df_tops_bottoms.tail(20)
    
    # Add strength for recent signals
    recent_tops = recent_data.dropna(subset=["top"])
    recent_bottoms = recent_data.dropna(subset=["bottom"])
    
    if not recent_tops.empty:
        strength += 0.3
    if not recent_bottoms.empty:
        strength += 0.3
        
    # Add strength for multiple confirmations
    if len(tops) > 1 and len(bottoms) > 1:
        strength += 0.2
        
    return min(strength, 0.5)  # Cap signal strength contribution


def calculate_alignment_confidence(timeframe_signals: dict, signal_type: str) -> float:
    """Calculate overall confidence based on timeframe alignment."""
    confidences = [signal.get("confidence", 0) for signal in timeframe_signals.values()]
    
    if not confidences:
        return 0.0
    
    base_confidence = sum(confidences) / len(confidences)
    
    # Boost confidence if all timeframes have strong signals
    strong_signals = [c for c in confidences if c > 0.7]
    if len(strong_signals) == len(confidences):
        base_confidence *= 1.2  # 20% boost
    
    return min(base_confidence, 1.0)


async def detect_arrow_reversals(symbols_with_all_arrows: List[dict], reversal_type: str = "green_to_red") -> List[dict]:
    """
    Enhanced reversal detection with better signal validation.
    
    For symbols that have all green arrows, detect if ANY timeframe now shows a new top (red arrow).
    For symbols that have all red arrows, detect if ANY timeframe now shows a new bottom (green arrow).
    """
    if not symbols_with_all_arrows:
        return []
    
    timeframes_to_check = ["3m", "15m", "1h", "4h"]
    reversals = []
    
    for item in symbols_with_all_arrows:
        symbol = item.get("symbol")
        if not symbol:
            continue
        
        reversal_candidates = []
        
        for tf in timeframes_to_check:
            try:
                df = await convert_cached_klines_to_analysis_df(symbol, tf)
                if df.empty or len(df) < 10:
                    continue
                
                # Use sensitive parameters for early detection
                distance = 2 if tf == "3m" else 4
                prominence = 1e-7
                
                df_tops_bottoms = detect_tops_bottoms(df.tail(30), distance=distance, prominence=prominence)
                
                if reversal_type == "green_to_red":
                    # Look for recent tops in the last 2-3 candles
                    tops = df_tops_bottoms.dropna(subset=["top"])
                    if is_very_recent_signal(tops, df_tops_bottoms, max_candles=3):
                        reversal_candidates.append({
                            "timeframe": tf,
                            "signal_type": "top",
                            "recency": get_signal_recency(tops, df_tops_bottoms)
                        })
                        
                elif reversal_type == "red_to_green":
                    # Look for recent bottoms in the last 2-3 candles
                    bottoms = df_tops_bottoms.dropna(subset=["bottom"])
                    if is_very_recent_signal(bottoms, df_tops_bottoms, max_candles=3):
                        reversal_candidates.append({
                            "timeframe": tf,
                            "signal_type": "bottom", 
                            "recency": get_signal_recency(bottoms, df_tops_bottoms)
                        })
                        
            except Exception as e:
                logger.debug(f"Error checking reversal for {symbol} on {tf}: {e}")
                continue
        
        # Require at least one very recent reversal signal
        if reversal_candidates:
            reversals.append({
                "symbol": symbol,
                "reversal_candidates": reversal_candidates,
                "reversal_type": reversal_type,
                "confidence": calculate_reversal_confidence(reversal_candidates),
                "original_data": item
            })
            
    return reversals


def is_very_recent_signal(signals_df: pd.DataFrame, main_df: pd.DataFrame, max_candles: int = 3) -> bool:
    """Check if signal is very recent."""
    if signals_df.empty:
        return False
    last_signal_idx = signals_df.index[-1]
    current_idx = main_df.index[-1]
    return (current_idx - last_signal_idx) <= max_candles


def get_signal_recency(signals_df: pd.DataFrame, main_df: pd.DataFrame) -> int:
    """Get how many candles ago the signal occurred."""
    if signals_df.empty:
        return 999
    
    last_signal_idx = signals_df.index[-1]
    current_idx = main_df.index[-1]
    
    return current_idx - last_signal_idx


def calculate_reversal_confidence(reversal_candidates: List[dict]) -> float:
    """Calculate confidence for reversal signals."""
    if not reversal_candidates:
        return 0.0
    recency_scores = []
    for candidate in reversal_candidates:
        recency = candidate.get("recency", 999)
        if recency <= 1:
            recency_scores.append(0.9)
        elif recency <= 2:
            recency_scores.append(0.7)
        elif recency <= 3:
            recency_scores.append(0.5)
        else:
            recency_scores.append(0.3)
    base_confidence = sum(recency_scores) / len(recency_scores)
    if len(reversal_candidates) > 1:
        base_confidence *= 1.3
        
    return min(base_confidence, 1.0)

async def calculate_market_movement_index(use_cache=True, volume_weighted=False, min_volume_threshold=0.0):
    MARKET_INDEX_WINDOW = 15
    global indicators_data, market_index_history, _cached_market_index, _cached_market_index_timestamp, _cached_indicators_hash, redis_manager
    CACHE_TTL = 30
    try:
        await load_indicators_data()
        if not indicators_data:
            logger.warning("No indicator data available for market movement index")
            return 50.0
        current_time = time.time()
        data_hash = hash(str(sorted(indicators_data.keys())))
        if use_cache and _cached_market_index is not None and (current_time - _cached_market_index_timestamp) < CACHE_TTL and _cached_indicators_hash == data_hash:
            return _cached_market_index
        movement_components = []
        weights = []
        valid_symbols = 0
        for symbol, data in indicators_data.items():
            if not isinstance(data, dict): continue
            symbol_score = 0
            component_count = 0
            current_price = data.get('current_price')
            if current_price:
                atr_15m = data.get('atr_15m', 0)
                atr_3m = data.get('atr_3m', 0)
                if atr_15m and current_price > 0:
                    atr_pct_15m = (atr_15m / current_price) * 100
                    symbol_score += min(atr_pct_15m * 10, 50)
                    component_count += 1
                if atr_3m and current_price > 0:
                    atr_pct_3m = (atr_3m / current_price) * 100
                    symbol_score += min(atr_pct_3m * 10, 30)
                    component_count += 1
            rel_vol_15m = data.get('relative_volume_15m', 1.0)
            rel_vol_3m = data.get('relative_volume_3m', 1.0)
            if rel_vol_15m > 1.0:
                symbol_score += (rel_vol_15m - 1.0) * 20
                component_count += 1
            if rel_vol_3m > 1.0:
                symbol_score += (rel_vol_3m - 1.0) * 10
                component_count += 1
            rsi_15m = data.get('rsi_15m', 50)
            rsi_3m = data.get('rsi_3m', 50)
            rsi_momentum_15m = abs(rsi_15m - 50) / 50.0
            rsi_momentum_3m = abs(rsi_3m - 50) / 50.0
            symbol_score += rsi_momentum_15m * 10
            symbol_score += rsi_momentum_3m * 5
            component_count += 2
            stoch_k_15m = data.get('stoch_k_15m', 50)
            stoch_k_3m = data.get('stoch_k_3m', 50)
            stoch_momentum_15m = abs(stoch_k_15m - 50) / 50.0
            stoch_momentum_3m = abs(stoch_k_3m - 50) / 50.0
            symbol_score += stoch_momentum_15m * 5
            symbol_score += stoch_momentum_3m * 3
            component_count += 2
            if component_count > 0:
                normalized_score = symbol_score / component_count
                movement_components.append(min(normalized_score, 100))
                if volume_weighted:
                    volume_weight = max(rel_vol_15m, rel_vol_3m, 1.0)
                    if volume_weight >= min_volume_threshold: weights.append(volume_weight)
                    else: weights.append(0.0)
                else:
                    weights.append(1.0)
                valid_symbols += 1
        if not movement_components: return 50.0
        if volume_weighted and sum(weights) > 0:
            weights = np.array(weights)
            weights = weights / weights.sum()
            market_index = np.average(movement_components, weights=weights)
        else:
            market_index = np.mean(movement_components)
        scale_factor = 100.0 / max(50.0, np.percentile(movement_components, 95) if len(movement_components) > 0 else 50.0)
        market_index = max(0.0, min(100.0, market_index * scale_factor))
        market_index_history.append(market_index)
        if len(market_index_history) > MARKET_INDEX_WINDOW: market_index_history.pop(0)
        smoothed_index = max(0.0, min(100.0, np.mean(market_index_history) if len(market_index_history) >= 3 else market_index))
        _cached_market_index = smoothed_index
        _cached_market_index_timestamp = current_time
        _cached_indicators_hash = data_hash
        if redis_manager:
            try:
                ticker_data = {"symbol": "MARKET_INDEX", "price": float(smoothed_index), "timestamp": datetime.now(timezone.utc).isoformat(), "mode": "EXTREME_MODE" if smoothed_index >= 65 else "LIGHT_MODE" if smoothed_index <= 35 else "NORMAL_MODE"}
                await redis_manager.publish("market_index_ticker", orjson.dumps(ticker_data))  # type: ignore # pylint: disable=no-member,c-extension-no-member
            except Exception as e:
                logger.debug(f"Failed to publish market index ticker: {e}")
        logger.debug(f"Market movement calculation: {valid_symbols} symbols, raw: {market_index:.1f}, smoothed: {smoothed_index:.1f}")
        return smoothed_index
    except Exception as e:
        logger.error(f"Error calculating market movement index: {e}")
        return 50.0

async def check_market_index_alerts(market_index: float, previous_index: float = None) -> list:
    alerts = []
    TUMBLE_THRESHOLD = -5.0
    TAKEOFF_THRESHOLD = 5.0
    EXTREME_ALERT = 70.0
    CRASH_ALERT = 20.0
    if previous_index is not None:
        change = market_index - previous_index
        if change <= TUMBLE_THRESHOLD:
            alerts.append({"type": "TUMBLE", "severity": "HIGH", "index": market_index, "change": change, "message": f"Market tumbling: {market_index:.1f} (down {abs(change):.1f})"})
        elif change >= TAKEOFF_THRESHOLD:
            alerts.append({"type": "TAKEOFF", "severity": "HIGH", "index": market_index, "change": change, "message": f"Market taking off: {market_index:.1f} (up {change:.1f})"})
    if market_index >= EXTREME_ALERT:
        alerts.append({"type": "EXTREME_VOLATILITY", "severity": "CRITICAL", "index": market_index, "message": f"Extreme market volatility: {market_index:.1f}"})
    elif market_index <= CRASH_ALERT:
        alerts.append({"type": "MARKET_CRASH", "severity": "CRITICAL", "index": market_index, "message": f"Market crash conditions: {market_index:.1f}"})
    return alerts
async def trigger_market_mode(market_index: float) -> tuple:
    global last_market_mode, signals_data
    EXTREME_THRESHOLD = 65
    LIGHT_THRESHOLD = 35
    new_mode = "EXTREME_MODE" if market_index >= EXTREME_THRESHOLD else "LIGHT_MODE" if market_index <= LIGHT_THRESHOLD else "NORMAL_MODE"
    mode_changed = new_mode != last_market_mode
    last_market_mode = new_mode
    mode_data = {
        "market_index": market_index,
        "mode": new_mode,
        "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        "thresholds": {
            "extreme": EXTREME_THRESHOLD,
            "light": LIGHT_THRESHOLD
        },
        "mode_changed": mode_changed
    }
    mode_file = DATA_DIR / "market_mode.json"
    async with aiofiles.open(mode_file, "w") as f:
        await f.write(json.dumps(mode_data, indent=2))
    Config.set_market_mode(new_mode)
    if mode_changed:
        await record_market_mode_signal(new_mode, market_index)
        if new_mode == "EXTREME_MODE":
            logger.warning(f"🚨 MARKET MODE CHANGE: {new_mode} (Index: {market_index:.1f})")
        elif new_mode == "LIGHT_MODE":
            logger.info(f"💤 MARKET MODE CHANGE: {new_mode} (Index: {market_index:.1f})")
        else:
            logger.info(f"✅ MARKET MODE CHANGE: {new_mode} (Index: {market_index:.1f})")
    return new_mode, mode_changed, mode_data

async def record_market_mode_signal(mode: str, market_index: float):
    """
    Record market mode changes in signals_data
    """
    global signals_data
    
    try:
        symbol = "MARKET"
        timeframe = "GLOBAL"
        event_type = f"market_mode_{mode.lower()}"
        
        signal_details = {
            "timestamp": datetime.now(timezone.utc),
            "market_index": market_index,
            "mode": mode,
            "reason": f"Market movement index reached {market_index:.1f}",
            "action": "MONITOR",  # Not a trading action
            "priority": "HIGH" if mode == "EXTREME_MODE" else "MEDIUM" }
        
        await record_signal(signals_data, symbol, timeframe, event_type, 
                           price=None, timestamp=signal_details["timestamp"],
                           reason=signal_details["reason"], 
                           action=signal_details["action"],
                           market_index=market_index,
                           mode=mode,
                           priority=signal_details["priority"])
        logger.info(f"📝 Recorded market mode signal: {mode} in signals.json")
    except Exception as e:
        logger.error(f"Error recording market mode signal: {e}")

async def get_market_mode_analysis():
    market_index = await calculate_market_movement_index()
    current_mode, changed, mode_data = await trigger_market_mode(market_index)
    
    analysis = {
        "market_index": market_index,
        "current_mode": current_mode,
        "mode_changed": changed,
        "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        "recommendations": get_mode_recommendations(current_mode, market_index),
        "history_length": len(market_index_history)
    }
    
    return analysis

def get_mode_recommendations(mode: str, market_index: float) -> list:
    """
    Get trading recommendations based on market mode
    """
    if mode == "EXTREME_MODE":
        return [
            "Reduce position sizes",
            "Widen stop losses", 
            "Avoid new entries during spikes",
            "Focus on major support/resistance levels",
            "Monitor for reversal patterns"
        ]
    elif mode == "LIGHT_MODE":
        return [
            "Consider smaller, more frequent trades",
            "Tighter stop losses may work better",
            "Look for breakout opportunities",
            "Range-bound strategies may perform well",
            "Be patient for clear signals"
        ]
    else:  # NORMAL_MODE
        return [
            "Standard position sizing",
            "Normal risk management",
            "Follow established strategies",
            "Monitor for mode changes",
            "Diversify across timeframes"
        ]

async def market_index_realtime_monitor(update_interval=30):
    """Fast real-time monitor for market index ticker with alerts"""
    global _cached_market_index, redis_manager
    logger.info(f"🚀 Starting real-time market index monitor (update every {update_interval}s)")
    previous_index = None
    _last_alert_type = None
    _last_alert_index = None
    while True:
        try:
            market_index = await calculate_market_movement_index(use_cache=True, volume_weighted=False)
            alerts = await check_market_index_alerts(market_index, previous_index)
            for alert in alerts:
                atype = alert["type"]
                idx_changed = _last_alert_index is None or abs(market_index - _last_alert_index) >= 2.0
                type_changed = atype != _last_alert_type
                if not type_changed and not idx_changed:
                    continue
                _last_alert_type = atype
                _last_alert_index = market_index
                if alert["severity"] == "CRITICAL":
                    logger.critical(f"🚨 {alert['type']}: {alert['message']}")
                elif alert["severity"] == "HIGH":
                    logger.warning(f"⚠️ {alert['type']}: {alert['message']}")
            previous_index = market_index
            await asyncio.sleep(update_interval)
        except Exception as e:
            logger.error(f"Error in real-time market index monitor: {e}")
            await asyncio.sleep(10)
async def market_movement_monitor():
    """Continuous monitor for market movement - call this in your main loop"""
    logger.info("🔄 Starting market movement monitor")
    previous_index = None
    while True:
        try:
            analysis = await get_market_mode_analysis()
            market_index = analysis['market_index']
            alerts = await check_market_index_alerts(market_index, previous_index)
            for alert in alerts:
                if alert["severity"] == "CRITICAL":
                    logger.critical(f"🚨 {alert['type']}: {alert['message']}")
                elif alert["severity"] == "HIGH":
                    logger.warning(f"⚠️ {alert['type']}: {alert['message']}")
            if analysis["current_mode"] == "EXTREME_MODE":
                logger.info(f"📊 Market: EXTREME (Index: {analysis['market_index']:.1f})")
            elif analysis["current_mode"] == "LIGHT_MODE":
                logger.info(f"📊 Market: LIGHT (Index: {analysis['market_index']:.1f})")
            else:
                if analysis["market_index"] > 55 or analysis["market_index"] < 45:
                    logger.info(f"📊 Market: {analysis['current_mode']} (Index: {analysis['market_index']:.1f})")
            previous_index = market_index
            await asyncio.sleep(180)
        except Exception as e:
            logger.error(f"Error in market movement monitor: {e}")
            await asyncio.sleep(60)

async def get_market_index_ticker() -> dict:
    """Get current market index as a ticker for display"""
    global _cached_market_index
    try:
        market_index = await calculate_market_movement_index(use_cache=True)
        mode = "EXTREME_MODE" if market_index >= 65 else "LIGHT_MODE" if market_index <= 35 else "NORMAL_MODE"
        return {"symbol": "MARKET_INDEX", "price": float(market_index), "mode": mode, "timestamp": datetime.now(timezone.utc).isoformat(), "change_24h": None}
    except Exception as e:
        logger.error(f"Error getting market index ticker: {e}")
        return {"symbol": "MARKET_INDEX", "price": 50.0, "mode": "NORMAL_MODE", "timestamp": datetime.now(timezone.utc).isoformat(), "change_24h": None}

# # Integration with existing ranking system
# async def enhanced_ranking_with_market_context():
#     """
#     Enhanced ranking that considers market movement context
#     """
#     analysis = await get_market_mode_analysis()
#     current_mode = analysis["current_mode"]
#     market_index = analysis["market_index"]
    
#     logger.info(f"📈 Starting ranking with market context: {current_mode} (Index: {market_index:.1f})")
    
#     # Adjust behavior based on market mode
#     if current_mode == "EXTREME_MODE":
#         logger.info("🔄 EXTREME_MODE: Applying conservative filters and wider thresholds")
#         # Could adjust: score thresholds, minimum volume requirements, etc.
        
#     elif current_mode == "LIGHT_MODE":
#         logger.info("🔄 LIGHT_MODE: Applying aggressive filters and tighter thresholds")
#         # Could adjust: be more selective with entries, require stronger signals
    
#     # Proceed with normal ranking (market mode influences scoring internally via indicators)
#     return await initial_fetch_and_ranking(all_symbols)

# Quick access function for other scripts
async def ranking_loop(symbols_list_arg: List[str]):
    global RANKING_DATA, RANKING_INFO, LAST_RANKING_TIME, _subscribers_started, mark_price_cache
    redis_manager_instance = await get_simple_redis_manager()
    async def _handle_klines_update(data: dict):
        """Handle klines updates from coordinated Redis"""
        global last_kline_updates
        try:
            symbol = data.get('symbol')
            interval = data.get('interval')
            if symbol and interval:
                last_kline_updates.setdefault(symbol, {})[interval] = datetime.now(timezone.utc)
                logger.debug(f"[rankings] Klines update: {symbol} {interval}")
        except Exception as e:
            logger.warning(f"[rankings] Klines update handler error: {e}")

    async def _handle_mark_prices_update(data: dict):
        """Handle mark price updates and cache them"""
        global mark_price_cache
        try:
            symbol = data.get("symbol")
            price = data.get("price")
            if symbol and price:
                mark_price_cache[symbol] = {"price": float(price), "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
        except Exception as e:
            logger.debug(f"[rankings] Mark price update error: {e}")

    async def _handle_latest_market_data_update(data: dict):
        """Handle latest market data updates from coordinated Redis"""
        try:
            logger.debug("[rankings] Received latest market data update")
            # Reload indicators when latest market data is updated
            await load_indicators_data()
        except Exception as e:
            logger.error(f"[rankings] Error handling latest market data update: {e}")

    if redis_manager_instance and not _subscribers_started:
        try:
            await redis_manager_instance.subscribe(
                channel="klines_updates",
                callback=_handle_klines_update,
                data_type="klines"
            )
            await redis_manager_instance.subscribe(
                channel="mark_prices",
                callback=_handle_mark_prices_update,
                data_type="mark_prices"
            )
            await redis_manager_instance.subscribe(
                channel="latest_market_data",
                callback=_handle_latest_market_data_update,
                data_type="latest_market_data"
            )
            logger.info("[rankings] ✅ Subscribed to coordinated Redis channels")
            _subscribers_started = True
        except Exception as e:
            logger.error(f"[rankings] Failed to subscribe to coordinated Redis: {e}")
    elif _subscribers_started:
        logger.debug("[rankings] Redis subscribers already started, skipping subscription")

    last_cache_save = datetime.now(timezone.utc)
    
    while True:
        try:
            ranking_data_scalars_list_loop, metadata_current_run_loop = await initial_fetch_and_ranking(symbols_list_arg, ["4h", "1h", "15m", "3m", "D"])
            
            if ranking_data_scalars_list_loop:
                RANKING_DATA = ranking_data_scalars_list_loop 
                temp_ranking_info_loop = build_ranking_info(RANKING_DATA, ["4h","1h","15m","3m","D"])

                metadata_loop = metadata_current_run_loop.copy() if metadata_current_run_loop else {}
                metadata_loop.setdefault('generated_at', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'))
                metadata_loop.setdefault('symbol_count', len(RANKING_DATA))
                metadata_loop.setdefault('source', 'ranking_loop')
                temp_ranking_info_loop['metadata'] = metadata_loop
                RANKING_INFO = temp_ranking_info_loop 
                LAST_RANKING_TIME = datetime.now(timezone.utc)
                logger.info(f"[rankings] ✅ Ranking completed: {len(RANKING_DATA)} symbols in RANKING_DATA")
                if metadata_current_run_loop and metadata_current_run_loop.get('sources_used'):
                    sources_used = metadata_current_run_loop.get('sources_used', [])
                    logger.info(f"[rankings] Using sources: {sources_used}")
            else:
                logger.warning("[rankings] => No scalar ranking data generated")
            now = datetime.now(timezone.utc)
            if (now - last_cache_save).total_seconds() >= 60:
                try:
                    await save_cache(PRICE_CACHE_FILE, price_cache)
                    last_cache_save = now
                    logger.debug(f"[rankings] Periodic price cache save: {len(price_cache)} symbols")
                except Exception as e:
                    logger.debug(f"[rankings] Cache save failed: {e}")
            
            await asyncio.sleep(config.RANKING_LOOP_SLEEP_SECONDS)
        except Exception as e_rank_loop_exc:
            logger.error(f"Error in ranking_loop: {e_rank_loop_exc}", exc_info=True)
            await asyncio.sleep(config.ERROR_RECOVERY_SLEEP_SECONDS)

async def plot_loop():
    global RANKING_DATA, RANKING_INFO, fs, fsr, prox, bs, rpg, KLINES_CACHE_DIR, PLOTS_DIR, DAYS_PLOT, pd
    if not redis_manager:
        return None, None
    redis_client = redis_manager.connections.get("local")
    redis_client_read = redis_manager.connections.get("gateway")
    logger.info("[plot_loop] => Starting plot_loop...")
    iteration_count = 0
    logger.info(f"[plot_loop] 🎨 Starting plot cycle for {len(RANKING_DATA)} symbols...")
    while True: 
        try: 
            iteration_count += 1
            logger.debug(f"[plot_loop] Iteration {iteration_count} - Checking RANKING_DATA...")            
            if not RANKING_DATA:
                logger.info(f"[plot_loop] => RANKING_DATA is empty, sleeping for {config.PLOT_LOOP_INTERVAL_SECONDS} seconds")
                await asyncio.sleep(config.PLOT_LOOP_INTERVAL_SECONDS)
                continue
            if not RANKING_INFO:
                if RANKING_DATA:
                    logger.info("[plot_loop] => RANKING_INFO empty - rebuilding from current ranking data...")
                    try:
                        rebuilt_info = build_ranking_info(RANKING_DATA, ["4h","1h","15m","3m","D"])
                        RANKING_INFO = rebuilt_info or {}
                    except Exception as rebuild_exc:
                        logger.error(f"[plot_loop] => Failed to rebuild RANKING_INFO: {rebuild_exc}")
                        await asyncio.sleep(config.PLOT_LOOP_INTERVAL_SECONDS)
                        continue
                else:
                    logger.info(f"[plot_loop] => RANKING_INFO empty and no ranking data yet, sleeping for {config.PLOT_LOOP_INTERVAL_SECONDS} seconds")
                    await asyncio.sleep(config.PLOT_LOOP_INTERVAL_SECONDS)
                    continue

            if 'metadata' not in RANKING_INFO or not RANKING_INFO.get('metadata'):
                if not RANKING_DATA:
                    logger.info(f"[plot_loop] => RANKING_DATA unavailable for metadata fallback, sleeping for {config.PLOT_LOOP_INTERVAL_SECONDS} seconds")
                    await asyncio.sleep(config.PLOT_LOOP_INTERVAL_SECONDS)
                    continue

                logger.info("[plot_loop] => Injecting fallback metadata for plotting...")
                fallback_metadata = {
                    'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    'symbol_count': len(RANKING_DATA),
                    'source': 'plot_loop_fallback'
                }
                try:
                    RANKING_INFO['metadata'] = fallback_metadata
                except TypeError:
                    # RANKING_INFO might be a list; convert to dict structure
                    RANKING_INFO = {'metadata': fallback_metadata}
                logger.info("[plot_loop] => Fallback metadata ready, proceeding with plotting...")
            await asyncio.to_thread(load_signals)
            logger.debug(f"[plot_loop] Loaded signals data: {len(signals_data)} symbols with events")
            # Load fresh signals data for plotting
            logger.debug(f"[plot_loop] Loaded signals data: {len(signals_data)} symbols with events")

            # --- PLOT QUEUE GENERATION FROM FRESH IN-MEMORY DATA ---
            top_recent_plot = sorted(RANKING_DATA, key=lambda x: x.get("final_score_recent_norm", 0.0), reverse=True)[:20]
            bottom_recent_plot = sorted(RANKING_DATA, key=lambda x: x.get("final_score_recent_norm", 0.0))[:20]
            top_norm_plot = sorted(RANKING_DATA, key=lambda x: x.get("final_score_norm", 0.0), reverse=True)[:20]
            bottom_norm_plot = sorted(RANKING_DATA, key=lambda x: x.get("final_score_norm", 0.0))[:20]
            
            plot_queue_items_list = []

            def add_to_plot_queue_func(ranked_items, prefix_str):
                seen_prefix = set()
                for i, item in enumerate(ranked_items, 1):
                    symbol = item.get("symbol")
                    if symbol and symbol not in seen_prefix:
                        plot_queue_items_list.append((f"{prefix_str}{i:02d}", symbol))
                        seen_prefix.add(symbol)

            add_to_plot_queue_func(top_recent_plot, "WR")
            add_to_plot_queue_func(bottom_recent_plot, "LR")
            add_to_plot_queue_func(top_norm_plot, "W")
            add_to_plot_queue_func(bottom_norm_plot, "L")
            
            logger.info(f"[plot_loop] => Queued {len(plot_queue_items_list)} unique symbols for plotting from in-memory data.")

            for rank_label_plot, symbol_plot in plot_queue_items_list:
                try:
                    # --- THIS IS THE PUNCTUAL FIX ---
                    # The variable name is now consistent throughout the loop.
                    timeframes_for_plot = ["3m", "15m", "1h", "4h"]
                    # --- END OF PUNCTUAL FIX ---
                    
                    all_files_ready = True
                    for tf in timeframes_for_plot:
                        kline_file = KLINES_CACHE_DIR / f"{symbol_plot}_{tf}.json"
                        if not kline_file.exists() or kline_file.stat().st_size < 50:
                            logger.debug(f"[plot_loop] Skipping plot for {symbol_plot}: Kline data for '{tf}' not yet available.")
                            all_files_ready = False
                            break
                    
                    if not all_files_ready:
                        continue

                    scalar_item_data_plot = next((item for item in RANKING_DATA if item.get("symbol") == symbol_plot), None)
                    if not scalar_item_data_plot:
                        continue                
                    
                    df_tasks = [convert_cached_klines_to_analysis_df(symbol_plot, tf) for tf in timeframes_for_plot]
                    loaded_dfs = await asyncio.gather(*df_tasks)
                    # Inject mark price (from Redis) as most recent close for responsiveness, if available
                    try:
                        mp_val = 0.0
                        if redis_client:
                            raw_mp = await redis_client.get(f"mark_price:{symbol_plot}")
                            if raw_mp:
                                try:
                                    obj_mp = json.loads(raw_mp)
                                    mp_val = float(obj_mp.get("price", 0)) if isinstance(obj_mp, dict) else float(raw_mp)
                                except (json.JSONDecodeError, ValueError, TypeError):
                                    mp_val = 0.0
                        if mp_val > 0:
                            for i_tf in range(len(loaded_dfs)):
                                df_ld = loaded_dfs[i_tf]
                                if isinstance(df_ld, pd.DataFrame) and not df_ld.empty and "close" in df_ld.columns:
                                    df_ld = df_ld.copy(); df_ld.loc[df_ld.index[-1], "close"] = mp_val
                                    loaded_dfs[i_tf] = df_ld
                    except Exception:
                        pass

                    raw_dfs_plot_temp_dict = dict(zip(timeframes_for_plot, loaded_dfs))
                    # --- END OF PUNCTUAL FIX ---

                    if not any(not df.empty for df in raw_dfs_plot_temp_dict.values()):
                        logger.warning(f"[plot_loop] => Data vanished for {symbol_plot} between check and load.")
                        continue

                    dfs_current_plot_dict = {}
                    for tf_p_proc, df_p_raw in raw_dfs_plot_temp_dict.items():
                        if df_p_raw is None or df_p_raw.empty:
                            dfs_current_plot_dict[tf_p_proc] = pd.DataFrame()
                            continue
                        
                        df_with_inds = add_stochrsi_zones(df_p_raw.copy())
                        if tf_p_proc == "3m":
                            df_15m, df_1h, df_4h = (raw_dfs_plot_temp_dict.get(tf) for tf in ["15m", "1h", "4h"])
                            df_with_inds = assign_points_proximity_3m(df_with_inds, df_15m, df_1h, df_4h)
                            
                            metadata = RANKING_INFO.get('metadata', {})
                            gmax_prox = metadata.get('global_max_abs_individual_prox_score_3m', 1.0)
                            if "proximity_score" in df_with_inds.columns:
                                df_with_inds["proximity_score_norm"] = (df_with_inds["proximity_score"].fillna(0) / (gmax_prox if gmax_prox != 0 else 1.0) * 100.0)
                        dfs_current_plot_dict[tf_p_proc] = df_with_inds
                    
                    fs_norm_title = fs.get(symbol_plot, 0.0)
                    fs_recent_title = fsr.get(symbol_plot, 0.0)
                    prox_norm_title = prox.get(symbol_plot, 0.0)
                    band_score_title = bs.get(symbol_plot, 0.0)
                    rp_global_title = rpg.get(symbol_plot, 0.0)

                    await plot_dfs_subplots(
                        rank_label=rank_label_plot, symbol=symbol_plot, dfs=dfs_current_plot_dict, 
                        final_score=scalar_item_data_plot.get("final_score_raw", 0.0),
                        final_score_norm_arg=fs_norm_title, final_score_recent_arg=fs_recent_title,
                        prox_norm_arg=prox_norm_title, band_score_arg=band_score_title,
                        trend_val_arg=scalar_item_data_plot.get("trend_val_norm", 0.0), 
                        rp_global_arg=rp_global_title,
                        slope_str=",".join([f"{k}:{v:.1f}" for k, v in scalar_item_data_plot.get("slopes_raw", {}).items()]),
                        linear_val=scalar_item_data_plot.get("linearity_raw", 0.0),
                        rel_gains=scalar_item_data_plot.get("rel_gains_raw", 0.0),                    
                        output_dir=str(PLOTS_DIR), 
                        RANKING_INFO_ARG=RANKING_INFO
                    )
                    await test_plot_stoch_crossovers(symbol_plot, dfs_current_plot_dict, str(PLOTS_DIR))
                except Exception as e_plot_sym_exc:
                    logger.error(f"[plot_loop] => Failed to plot {symbol_plot} ({rank_label_plot}): {e_plot_sym_exc}", exc_info=True)
            
            await asyncio.to_thread(cleanup_old_plots, str(PLOTS_DIR), days_to_keep=DAYS_PLOT)
            logger.info(f"[plot_loop] => Plotting cycle complete.")
            await asyncio.sleep(config.PLOT_LOOP_INTERVAL_SECONDS)

        except Exception as e_plot_loop_outer_exc:
            logger.error(f"Outer error in plot_loop: {e_plot_loop_outer_exc}", exc_info=True)
            await asyncio.sleep(config.SIGNALS_LOOP_INTERVAL_SECONDS)


async def arrow_signals_loop():
    """Separate loop to check for all_green/all_red signals every 90 seconds"""
    global RANKING_DATA, ARROW_SIGNAL_STATE, signals_data
    global fs, bs, rp, prox
    while True:
        try:
            await asyncio.sleep(90)
            if not RANKING_DATA:
                continue
            try:
                winners_30_task = asyncio.to_thread(load_last_two_versions, WINNERS_30_FILE.name, str(WINNERS_30_FILE.parent))
                losers_30_task = asyncio.to_thread(load_last_two_versions, LOSERS_30_FILE.name, str(LOSERS_30_FILE.parent))
                results = await asyncio.wait_for(asyncio.gather(winners_30_task, losers_30_task), timeout=10.0)
            except asyncio.TimeoutError:
                continue
            _, current_winners_30 = results[0]
            _, current_losers_30 = results[1]
            winners_symbols = set(item["symbol"] for item in current_winners_30[:30])
            losers_symbols = set(item["symbol"] for item in current_losers_30[:30])
            all_green_arrows, all_red_arrows = await detect_all_arrows_aligned(RANKING_DATA, top_n=20)
            all_green_arrows = [item for item in all_green_arrows if item["symbol"] in winners_symbols]
            all_red_arrows = [item for item in all_red_arrows if item["symbol"] in losers_symbols]
            ranking_ctx = defaultdict(lambda: {"short_term": "none", "long_term": "none"})
            for item_ctx in current_winners_30: ranking_ctx[item_ctx["symbol"]]["long_term"] = "winner"
            for item_ctx in current_losers_30: ranking_ctx[item_ctx["symbol"]]["long_term"] = "loser"
            async def send_aligned_arrow_sig_fast(item_aligned, action_str, event_type_prefix, arrow_rank):
                sym_aligned = item_aligned["symbol"]
                price_result_aligned, _ = await get_current_price(sym_aligned)
                if price_result_aligned is None: return
                price_aligned = price_result_aligned
                if price_aligned is None: return
                rank_in_list = find_rank_in_list(sym_aligned, current_winners_30, []) if action_str == "BUY" else find_rank_in_list(sym_aligned, [], current_losers_30)
                slopes_info = item_aligned.get("slopes", {})
                slopes_str = ", ".join([f"{tf}:{slopes_info.get(tf, 0.0):.2f}" for tf in ["3m", "15m", "1h", "4h"]])
                sig_event_aligned = {"symbol": sym_aligned, "event_type": event_type_prefix, "action": action_str, "price": price_aligned, "rank": arrow_rank, "rank_20": rank_in_list, "final_score_norm": fs.get(sym_aligned, 0.0), "prox_score_norm": prox.get(sym_aligned, 0.0), "band_score": bs.get(sym_aligned, 0.0), "rp": rp.get(sym_aligned, 0.0), "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "timestamp_signal": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "long_term_group": ranking_ctx[sym_aligned]["long_term"], "short_term_group": ranking_ctx[sym_aligned].get("short_term", "none"), "reason": f"All arrows aligned: {slopes_str}", "slopes": slopes_str, "conviction": 75.0}
                await send_signal(sig_event_aligned, signals_data)
                logger.warning(f"[ARROW_LOOP] 🎯 {action_str} signal for {sym_aligned} - All arrows aligned! (rank={arrow_rank})")
            for idx, item_green in enumerate(all_green_arrows, 1):
                sym_green = item_green["symbol"]
                slopes_green = item_green.get("slopes", {})
                slope_3m = slopes_green.get("3m", 0.0)
                last_state = ARROW_SIGNAL_STATE.get(sym_green, None)
                if last_state != "down" and slope_3m > 0:
                    continue
                await send_aligned_arrow_sig_fast(item_green, "BUY", f"winners_all_green_rank{idx}", idx)
                ARROW_SIGNAL_STATE[sym_green] = "up"
            for idx, item_red in enumerate(all_red_arrows, 1):
                sym_red = item_red["symbol"]
                slopes_red = item_red.get("slopes", {})
                slope_3m = slopes_red.get("3m", 0.0)
                if slope_3m < 0:
                    await send_aligned_arrow_sig_fast(item_red, "SELL", f"losers_all_red_rank{idx}", idx)
                    ARROW_SIGNAL_STATE[sym_red] = "down"
        except Exception as e_arrow:
            logger.error(f"[arrow_signals_loop] Error: {e_arrow}", exc_info=True)
            await asyncio.sleep(90)

async def signals_loop():
    global PREV_WINNERS_15M, PREV_LOSERS_15M, PREV_WINNERS_30, PREV_LOSERS_30
    global PREV_ALL_GREEN_ARROWS, PREV_ALL_RED_ARROWS  # Add missing global declarations
    global RANKING_INFO, signals_data 
    global fs, bs, rp, rpg, rpn, prox 
    last_processed_mode = None
    if not signals_data: 
        await asyncio.to_thread(load_signals)
    while True:
        try:
            current_mode_data = await get_current_market_mode()
            current_mode = current_mode_data.get("mode", "NORMAL_MODE")
            market_index = current_mode_data.get("market_index", 50.0)
            if current_mode != last_processed_mode and last_processed_mode is not None:
                await send_market_mode_signal(current_mode, market_index, current_mode_data)
            last_processed_mode = current_mode

            try:
                current_raw_trading_signals_list = await asyncio.wait_for(
                    asyncio.to_thread(detect_trading_signals), 
                    timeout=30.0   )
            except asyncio.TimeoutError:
                logger.warning("[signals_loop] detect_trading_signals timed out after 30 seconds, skipping this cycle")
                await asyncio.sleep(config.SIGNALS_LOOP_INTERVAL_SECONDS)
                continue
            
            # Load all ranking files in parallel with timeout protection
            try:
                winners_15m_task = asyncio.to_thread(load_last_two_versions, WINNERS_15M_FILE.name, str(WINNERS_15M_FILE.parent))
                losers_15m_task = asyncio.to_thread(load_last_two_versions, LOSERS_15M_FILE.name, str(LOSERS_15M_FILE.parent))
                winners_30_task = asyncio.to_thread(load_last_two_versions, WINNERS_30_FILE.name, str(WINNERS_30_FILE.parent))
                losers_30_task = asyncio.to_thread(load_last_two_versions, LOSERS_30_FILE.name, str(LOSERS_30_FILE.parent))
                
                # Wait for all file loading operations to complete with timeout
                results = await asyncio.wait_for(
                    asyncio.gather(winners_15m_task, losers_15m_task, winners_30_task, losers_30_task),
                    timeout=20.0  # 20 second timeout for file loading
                )
            except asyncio.TimeoutError:
                logger.warning("[signals_loop] File loading timed out after 20 seconds, skipping this cycle")
                await asyncio.sleep(config.SIGNALS_LOOP_INTERVAL_SECONDS)
                continue
            _, current_winners_15m = results[0]
            _, current_losers_15m = results[1]
            _, current_winners_30 = results[2]
            _, current_losers_30 = results[3]
            
            # Debug logging for file loading
            logger.debug(f"[signals_loop] Loaded data: winners_15m={len(current_winners_15m)}, losers_15m={len(current_losers_15m)}, winners_30={len(current_winners_30)}, losers_30={len(current_losers_30)}")
            logger.debug(f"[signals_loop] Previous data: PREV_WINNERS_15M={len(PREV_WINNERS_15M)}, PREV_LOSERS_15M={len(PREV_LOSERS_15M)}, PREV_WINNERS_30={len(PREV_WINNERS_30)}, PREV_LOSERS_30={len(PREV_LOSERS_30)}")

            if not PREV_WINNERS_15M and current_winners_15m: PREV_WINNERS_15M = current_winners_15m
            if not PREV_LOSERS_15M and current_losers_15m: PREV_LOSERS_15M = current_losers_15m
            if not PREV_WINNERS_30 and current_winners_30: PREV_WINNERS_30 = current_winners_30
            if not PREV_LOSERS_30 and current_losers_30: PREV_LOSERS_30 = current_losers_30

            ranking_ctx = defaultdict(lambda: {"short_term": "none", "long_term": "none"}) # Renamed
            for item_ctx in current_winners_15m: ranking_ctx[item_ctx["symbol"]]["short_term"] = "winner"
            for item_ctx in current_losers_15m: ranking_ctx[item_ctx["symbol"]]["short_term"] = "loser"
            for item_ctx in current_winners_30: ranking_ctx[item_ctx["symbol"]]["long_term"] = "winner"
            for item_ctx in current_losers_30: ranking_ctx[item_ctx["symbol"]]["long_term"] = "loser"

            fast_up_15_w, fast_down_15_w = detect_fast_15_movers_up_down(current_winners_15m, PREV_WINNERS_15M)
            fast_up_15_l, fast_down_15_l = detect_fast_15_movers_up_down(current_losers_15m, PREV_LOSERS_15M)
            fast_up_30_w, fast_down_30_w = detect_fast_20_movers_up_down(current_winners_30, PREV_WINNERS_30)
            fast_up_30_l, fast_down_30_l = detect_fast_20_movers_up_down(current_losers_30, PREV_LOSERS_30)
            
            all_green_arrows, all_red_arrows = [], []
            if RANKING_DATA:
                all_green_arrows, all_red_arrows = await detect_all_arrows_aligned(RANKING_DATA, top_n=20)
                
                # Filter to only include top 20 winners and top 20 losers
                winners_symbols = set(item["symbol"] for item in current_winners_30[:30])
                losers_symbols = set(item["symbol"] for item in current_losers_30[:30])
                
                # Keep only winners with all green arrows
                all_green_arrows = [item for item in all_green_arrows if item["symbol"] in winners_symbols]
                # Keep only losers with all red arrows
                all_red_arrows = [item for item in all_red_arrows if item["symbol"] in losers_symbols]
                
                if all_green_arrows:
                    logger.info(f"[signals_loop] 🟢 Top 20 Winners with ALL GREEN arrows ({len(all_green_arrows)}): {[item['symbol'] for item in all_green_arrows]}")
                if all_red_arrows:
                    logger.info(f"[signals_loop] 🔴 Top 20 Losers with ALL RED arrows ({len(all_red_arrows)}): {[item['symbol'] for item in all_red_arrows]}")
                if not all_green_arrows and not all_red_arrows:
                    logger.debug(f"[signals_loop] No aligned arrows detected (green: 0, red: 0)")
            if fast_up_30_w or fast_down_30_w or fast_up_30_l or fast_down_30_l:
                logger.info(f"[signals_loop] 20m fast movers detected: up_w={len(fast_up_30_w)}, down_w={len(fast_down_30_w)}, up_l={len(fast_up_30_l)}, down_l={len(fast_down_30_l)}")
                if fast_up_30_w: logger.info(f"[signals_loop] Winners up: {[item['symbol'] for item in fast_up_30_w]}")
                if fast_down_30_w: logger.info(f"[signals_loop] Winners down: {[item['symbol'] for item in fast_down_30_w]}")
                if fast_up_30_l: logger.info(f"[signals_loop] Losers up: {[item['symbol'] for item in fast_up_30_l]}")
                if fast_down_30_l: logger.info(f"[signals_loop] Losers down: {[item['symbol'] for item in fast_down_30_l]}")
            else:
                logger.info(f"[signals_loop] No 20m fast movers detected. Current winners: {len(current_winners_30)}, Previous winners: {len(PREV_WINNERS_30)}")
                # Log some sample data to debug
                if current_winners_30 and PREV_WINNERS_30:
                    logger.info(f"[signals_loop] Sample current winner: {current_winners_30[0] if current_winners_30 else 'None'}")
                    logger.info(f"[signals_loop] Sample previous winner: {PREV_WINNERS_30[0] if PREV_WINNERS_30 else 'None'}")

            if RANKING_INFO:
                now_utc_sig = datetime.now(timezone.utc) # Renamed
                symbols_to_check = list(RANKING_INFO.keys() - {'metadata'})

                for sym_s in symbols_to_check: # Renamed symbol_stoch
                    price_result_s, _ = await get_current_price(sym_s);
                    if price_result_s is None: continue
                    price_s = price_result_s
                    if price_s is None: continue

                    final_score_s = fs.get(sym_s, 0.0); band_s = bs.get(sym_s, 0.0)
                    prox_s = prox.get(sym_s, 0.0); rp_s = rp.get(sym_s, 0.0) # rpg/rpn also available if needed
                    
                    df_3m_s = await convert_cached_klines_to_analysis_df(sym_s, "3m")

                    df_3m_s_rsi = add_stochrsi_zones(df_3m_s.copy())
                    
                    stoch_evs = detect_stoch_crossovers(df_3m_s_rsi)
                    logger.debug(f"[signals_loop] {sym_s}: detected {len(stoch_evs)} stochastic events")
                    if not stoch_evs: continue
                    fresh_stoch_evs = [ev_s for ev_s in stoch_evs if (now_utc_sig - pd.to_datetime(ev_s["timestamp"])).total_seconds() <= 190]
                    if not fresh_stoch_evs: continue
                    
                    atr_s = calculate_atr(df_3m_s, period=14)

                    for ev_s_item in fresh_stoch_evs: # Renamed ev_stoch
                        trade_r = calculate_trade_recommendation(ev_s_item["event_type"], final_score_s, prox_s, band_s)
                        st_g = ranking_ctx[sym_s]["short_term"]; lt_g = ranking_ctx[sym_s]["long_term"]
                        r15 = find_rank_in_list(sym_s, current_winners_15m, current_losers_15m)
                        r30 = find_rank_in_list(sym_s, current_winners_30, current_losers_30)

                        sig_ev_s = {
                            "symbol": sym_s, "event_type": ev_s_item["event_type"], "action": trade_r["action"], 
                            "price": price_s, "band_score": band_s, "atr": atr_s, "short_term_group": st_g, 
                            "long_term_group": lt_g, "final_score_norm": final_score_s, "prox_score_norm": prox_s,
                            "rank_15m": r15, "rank_20": r30, "usd_size": trade_r["usd_amount"], 
                            "confidence": trade_r["combined_confidence"], "timestamp": now_utc_sig.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            "timestamp_signal": ev_s_item["timestamp"], "rp": rp_s # Added rp
                        }
                        if should_send_signal(sig_ev_s, signals_data, sym_s, "3m", current_atr=atr_s):
                            await send_signal(sig_ev_s, signals_data)

            for raw_sig_item in current_raw_trading_signals_list: # Renamed raw_sig
                sym_raw = raw_sig_item["symbol"]
                final_score_raw_sig = fs.get(sym_raw, 0.0); band_raw_sig = bs.get(sym_raw, 0.0)
                prox_raw_sig = prox.get(sym_raw, 0.0); rp_raw_sig = rp.get(sym_raw, 0.0)

                trade_r_raw = calculate_trade_recommendation(raw_sig_item["event_type"], final_score_raw_sig, prox_raw_sig, band_raw_sig)
                st_g_raw = ranking_ctx[sym_raw]["short_term"]; lt_g_raw = ranking_ctx[sym_raw]["long_term"]
                
                # Prefer raw event price; fallback to latest mark price via get_current_price
                price_for_raw_sig, _ = await get_current_price(sym_raw)
                if price_for_raw_sig is None : continue


                sig_ev_raw_item = {
                    "symbol": sym_raw, "event_type": raw_sig_item["event_type"], "action": trade_r_raw["action"], 
                    "price": price_for_raw_sig, "band_score": band_raw_sig, "short_term_group": st_g_raw, 
                    "long_term_group": lt_g_raw, "final_score_norm": final_score_raw_sig, 
                    "prox_score_norm": prox_raw_sig, "usd_size": trade_r_raw["usd_amount"], 
                    "confidence": trade_r_raw["combined_confidence"], "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "timestamp_signal": raw_sig_item["timestamp"].strftime('%Y-%m-%dT%H:%M:%S.%fZ') if isinstance(raw_sig_item["timestamp"], (datetime, pd.Timestamp)) else raw_sig_item["timestamp"],
                    "rp": rp_raw_sig # Added rp
                }
                if should_send_signal(sig_ev_raw_item, signals_data, sym_raw, "3m"):
                     await send_signal(sig_ev_raw_item, signals_data)

            all_fm_data = {} # Renamed
            all_fm_symbols_set = set() # Renamed
            fm_lists_all = [fast_up_15_w, fast_down_15_w, fast_up_15_l, fast_down_15_l,
                            fast_up_30_w, fast_down_30_w, fast_up_30_l, fast_down_30_l]
            for fm_l in fm_lists_all: # Renamed fm_list
                for fm_i in fm_l: all_fm_symbols_set.add(fm_i["symbol"]) # Renamed fm_item

            for sym_fm_item in all_fm_symbols_set: # Renamed sym_mover
                all_fm_data[sym_fm_item] = {
                    "final_score_norm": fs.get(sym_fm_item, 0.0), "band_score": bs.get(sym_fm_item, 0.0),
                    "prox_score_norm": prox.get(sym_fm_item, 0.0), "rp": rp.get(sym_fm_item, 0.0),
                    "st_group": ranking_ctx[sym_fm_item]["short_term"], "lt_group": ranking_ctx[sym_fm_item]["long_term"],
                }

            async def send_fm_sig(item_fm_data, evt_prefix, rank_key, delta_key, act_str): # Renamed args
                sym_fm_send = item_fm_data["symbol"]
                fm_details_send = all_fm_data.get(sym_fm_send, {})
                price_result_fm, _ = await get_current_price(sym_fm_send)
                if price_result_fm is None: return
                price_fm_send = price_result_fm
                if price_fm_send is None: return

                # Determine rank number for event type (e.g. #1, #2)
                # This needs the index from the original fast mover list iteration
                # Passed item_fm_data should include its rank (new_rank_15m or new_rank_20)
                # Let's assume item_fm_data['new_rank_15m'] or item_fm_data['new_rank_20'] is the rank number for event_type.
                rank_num_for_event = item_fm_data.get(rank_key, 0) # e.g. new_rank_15m holds the 1-based rank
                # All ranking signals (short-term and long-term) are on 3m chart - use 3m cooldown
                # Only indicator signals can be for other timeframes
                tf_for_signal = "3m"

                sig_event_fm_send = {
                    "symbol": sym_fm_send, "event_type": f"{evt_prefix}{rank_num_for_event}", 
                    "action": act_str, "price": price_fm_send,
                    "old_rank": item_fm_data.get(f"old_rank_{rank_key.split('_')[-1]}"), 
                    "new_rank": rank_num_for_event, # new_rank from item_fm_data
                    "delta_rank": item_fm_data.get(delta_key),
                    "final_score_norm": fm_details_send.get("final_score_norm"),
                    "prox_score_norm": fm_details_send.get("prox_score_norm"),
                    "band_score": fm_details_send.get("band_score"), "rp": fm_details_send.get("rp"),
                    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "timestamp_signal": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "long_term_group":  fm_details_send.get("lt_group"),
                    "short_term_group": fm_details_send.get("st_group"),
                    "reason": f"Fast Mover: {evt_prefix.replace('_rank','')}"
                }
                # Check should_send_signal to prevent duplicate signals (throttling based on time and price movement)
                if should_send_signal(sig_event_fm_send, signals_data, sym_fm_send, tf_for_signal):
                    await send_signal(sig_event_fm_send, signals_data)

            # Send short-term ranking signals (15m winners/losers fast movers)
            for item_fm_loop_send in fast_up_15_w: 
                await send_fm_sig(item_fm_loop_send, f"short_term_winners_up_rank", "new_rank_15m", "delta_15m", "BUY")
            for item_fm_loop_send in fast_down_15_w: 
                await send_fm_sig(item_fm_loop_send, f"short_term_winners_down_rank", "new_rank_15m", "delta_15m", "SELL")
            for item_fm_loop_send in fast_up_15_l: 
                await send_fm_sig(item_fm_loop_send, f"short_term_losers_jump_rank", "new_rank_15m", "delta_15m", "BUY")  # Loser improved -> cover short / less bearish
            for item_fm_loop_send in fast_down_15_l: 
                await send_fm_sig(item_fm_loop_send, f"short_term_losers_drop_rank", "new_rank_15m", "delta_15m", "SELL")

            # Send long-term ranking signals (30m winners/losers fast movers)
            for item_fm_loop_send in fast_up_30_w: 
                logger.info(f"[signals_loop] Sending winners up signal for {item_fm_loop_send['symbol']}")
                await send_fm_sig(item_fm_loop_send, f"long_term_winners_up_rank", "new_rank_20", "delta_20", "BUY")
            for item_fm_loop_send in fast_down_30_w: 
                logger.info(f"[signals_loop] Sending winners down signal for {item_fm_loop_send['symbol']}")
                await send_fm_sig(item_fm_loop_send, f"long_term_winners_down_rank", "new_rank_20", "delta_20", "SELL")
            for item_fm_loop_send in fast_up_30_l: 
                logger.info(f"[signals_loop] Sending losers up signal for {item_fm_loop_send['symbol']}")
                await send_fm_sig(item_fm_loop_send, f"long_term_losers_jump_rank", "new_rank_20", "delta_20", "BUY")
            for item_fm_loop_send in fast_down_30_l: 
                logger.info(f"[signals_loop] Sending losers down signal for {item_fm_loop_send['symbol']}")
                await send_fm_sig(item_fm_loop_send, f"long_term_losers_drop_rank", "new_rank_20", "delta_20", "SELL")

            # Broadcast signals for aligned arrows (all green or all red)
            async def send_aligned_arrow_sig(item_aligned, action_str, event_type_prefix, arrow_rank):
                sym_aligned = item_aligned["symbol"]
                price_result_aligned, _ = await get_current_price(sym_aligned)
                if price_result_aligned is None:
                    return
                price_aligned = price_result_aligned
                if price_aligned is None:
                    return
                
                # Get ranking position
                rank_in_list = None
                if action_str == "BUY":  # Winners with all green arrows
                    rank_in_list = find_rank_in_list(sym_aligned, current_winners_30, [])
                else:  # Losers with all red arrows
                    rank_in_list = find_rank_in_list(sym_aligned, [], current_losers_30)
                
                slopes_info = item_aligned.get("slopes", {})
                slopes_str = ", ".join([f"{tf}:{slopes_info.get(tf, 0.0):.2f}" for tf in ["3m", "15m", "1h", "4h"]])
                
                sig_event_aligned = {
                    "symbol": sym_aligned,
                    "event_type": event_type_prefix,
                    "action": action_str,
                    "price": price_aligned,
                    "rank": arrow_rank,
                    "rank_20": rank_in_list,
                    "final_score_norm": fs.get(sym_aligned, 0.0),
                    "prox_score_norm": prox.get(sym_aligned, 0.0),
                    "band_score": bs.get(sym_aligned, 0.0),
                    "rp": rp.get(sym_aligned, 0.0),
                    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "timestamp_signal": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "long_term_group": ranking_ctx[sym_aligned]["long_term"],
                    "short_term_group": ranking_ctx[sym_aligned]["short_term"],
                    "reason": f"All arrows aligned: {slopes_str}",
                    "slopes": slopes_str
                }
                await send_signal(sig_event_aligned, signals_data)
                logger.info(f"[signals_loop] 🎯 [ARROW SIGNAL] Broadcast {action_str} signal for {sym_aligned} - All arrows aligned! (rank={arrow_rank}, confidence={item_aligned.get('confidence', 0.0)})")
            
            # DISABLED: Duplicate of fast arrow loop above (lines 7245-7260) — caused triple-order bug 2026-03-25
            # send_aligned_arrow_sig_fast already fires the same signals; this second loop doubled them
            
            # Check for reversals in symbols that previously had all green arrows
            if PREV_ALL_GREEN_ARROWS:
                logger.debug(f"[signals_loop] Checking reversals for {len(PREV_ALL_GREEN_ARROWS)} symbols with previous green arrows")
                reversals = await detect_arrow_reversals(PREV_ALL_GREEN_ARROWS, reversal_type="green_to_red")
                
                if reversals:
                    logger.info(f"[signals_loop] 🔴 Detected {len(reversals)} reversals (tops appeared): {[r['symbol'] for r in reversals]}")
                    
                    for reversal_item in reversals:
                        sym_rev = reversal_item["symbol"]
                        reversal_tfs = reversal_item.get("reversal_timeframes", [])
                        
                        # Find the original rank when it had all green arrows
                        original_rank = None
                        for idx, prev_green in enumerate(PREV_ALL_GREEN_ARROWS, 1):
                            if prev_green.get("symbol") == sym_rev:
                                original_rank = idx
                                break
                        
                        if original_rank is None:
                            continue

                        price_result_rev, _ = await get_current_price(sym_rev)
                        if price_result_rev is None:
                            continue
                        price_rev = price_result_rev
                        if price_rev is None:
                            continue
                        
                        rank_in_list_rev = find_rank_in_list(sym_rev, current_winners_30, [])
                        tfs_str = ", ".join(reversal_tfs)
                        
                        sig_event_reversal = {
                            "symbol": sym_rev,
                            "event_type": f"winners_all_green_rank{original_rank}",  # Same event type as BUY
                            "action": "SELL",  # But SELL action to close
                            "price": price_rev,
                            "rank_20": rank_in_list_rev,
                            "final_score_norm": fs.get(sym_rev, 0.0),
                            "prox_score_norm": prox.get(sym_rev, 0.0),
                            "band_score": bs.get(sym_rev, 0.0),
                            "rp": rp.get(sym_rev, 0.0),
                            "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            "timestamp_signal": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            "long_term_group": ranking_ctx[sym_rev]["long_term"],
                            "short_term_group": ranking_ctx[sym_rev]["short_term"],
                            "reason": f"Reversal detected: tops on {tfs_str}",
                            "reversal_timeframes": tfs_str }
                        
                        # Always send reversal signals (don't check should_send_signal for reversals)
                        await send_signal(sig_event_reversal, signals_data)
                        logger.info(f"[signals_loop] 📡 Broadcast SELL (reversal) for {sym_rev} - Tops detected on: {tfs_str}")

            # Check for reversals in symbols that previously had all red arrows (shorts closing)
            if PREV_ALL_RED_ARROWS:
                logger.debug(f"[signals_loop] Checking reversals for {len(PREV_ALL_RED_ARROWS)} symbols with previous red arrows")
                red_reversals = await detect_arrow_reversals(PREV_ALL_RED_ARROWS, reversal_type="red_to_green")
                
                if red_reversals:
                    logger.info(f"[signals_loop] 🟢 Detected {len(red_reversals)} red-to-green reversals (bottoms appeared): {[r['symbol'] for r in red_reversals]}")
                    
                    for reversal_item in red_reversals:
                        sym_rev = reversal_item["symbol"]
                        reversal_tfs = reversal_item.get("reversal_timeframes", [])
                        
                        # Find the original rank when it had all red arrows
                        original_rank = None
                        for idx, prev_red in enumerate(PREV_ALL_RED_ARROWS, 1):
                            if prev_red["symbol"] == sym_rev:
                                original_rank = idx
                                break
                        tfs_str = ", ".join(reversal_tfs)
                        price_result_rev_red, _ = await get_current_price(sym_rev)
                        if price_result_rev_red is None:
                            continue
                        price_rev_red = price_result_rev_red
                        if price_rev_red is None:
                            continue
                        rank_in_list_rev_red = find_rank_in_list(sym_rev, [], current_losers_30)
                        sig_event_reversal = {
                            "symbol": sym_rev,
                            "event_type": f"losers_all_red_rank{original_rank}",
                            "action": "BUY",
                            "price": price_rev_red,
                            "rank_20": rank_in_list_rev_red,
                            "final_score_norm": fs.get(sym_rev, 0.0),
                            "prox_score_norm": prox.get(sym_rev, 0.0),
                            "band_score": bs.get(sym_rev, 0.0),
                            "rp": rp.get(sym_rev, 0.0),
                            "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            "timestamp_signal": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            "long_term_group": ranking_ctx[sym_rev]["long_term"],
                            "short_term_group": ranking_ctx[sym_rev]["short_term"],
                            "reason": f"Reversal detected: bottoms on {tfs_str}",
                            "reversal_timeframes": tfs_str
                        }
                        
                        # Always send reversal signals (don't check should_send_signal for reversals)
                        await send_signal(sig_event_reversal, signals_data)
                        logger.info(f"[signals_loop] 📡 Broadcast BUY (red reversal) for {sym_rev} - Bottoms detected on: {tfs_str}")

            # Update tracking for next iteration
            PREV_ALL_GREEN_ARROWS = all_green_arrows  # Update with current all green arrows
            PREV_ALL_RED_ARROWS = all_red_arrows      # Update with current all red arrows
            PREV_WINNERS_15M = current_winners_15m; PREV_LOSERS_15M  = current_losers_15m
            PREV_WINNERS_30  = current_winners_30; PREV_LOSERS_30   = current_losers_30
            
            await asyncio.to_thread(prune_old_signals, signals_data)
            
            # Save signals with timeout protection
            try:
                await asyncio.wait_for(save_signals(), timeout=30.0)  # 30 second timeout for batch saves
            except asyncio.TimeoutError:
                logger.warning("[signals_loop] save_signals timed out after 30 seconds")

        except Exception as ex_sig_loop_exc: # Renamed
            logger.error(f"[signals_loop] => Unexpected error: {ex_sig_loop_exc}", exc_info=True)

        await asyncio.sleep(config.SIGNALS_LOOP_INTERVAL_SECONDS)


async def process_historical_stochastic_events_recent(symbols_list: List[str]) -> None:
    """
    Process recent historical stochastic events (last 7 days) for immediate plotting.
    Uses working functions from ez_crosses.py for reliable results.
    """
    global signals_data
    
    logger.info(f"[process_historical_stochastic_events_recent] Processing recent stochastic events for {len(symbols_list)} symbols (last 7 days)")
    
    # Import working functions from ez_crosses
    try:
        from ez_crosses import calculate_stoch_rsi, detect_stoch_crossovers
    except ImportError:
        logger.error("[process_historical_stochastic_events_recent] Could not import ez_crosses functions")
        return
    
    # Process only recent data (last 7 days)
    cutoff_date = pd.Timestamp.now(tz='UTC') - pd.DateOffset(days=7)
    processed_count = 0
    
    for symbol in symbols_list:
        try:
            # Process 3m timeframe only for recent data (most relevant for plotting)
            df_tf = await convert_cached_klines_to_analysis_df(symbol, "3m")
            if df_tf.empty:
                continue
                
            # Filter to recent data only
            df_recent = df_tf[df_tf['timestamp'] >= cutoff_date].copy()
            if df_recent.empty:
                continue
            
            # Use working stochastic RSI calculation
            stoch_rsi_values = calculate_stoch_rsi(symbol, df_recent)
            if stoch_rsi_values is None:
                continue
            
            # Detect stochastic events using working function
            stoch_evs = detect_stoch_crossovers(symbol, df_recent, stoch_rsi_values)
            
            # Store events
            for ev in stoch_evs:
                reason = f"Stoch RSI {'crossing above 20' if ev['type'] == 'stoch_crossover' else 'crossing below 80'}"
                action = "BUY" if ev['type'] == 'stoch_crossover' else "SELL"
                await record_signal(signals_data, symbol, "3m", ev["type"], 
                                 ev["price"], pd.to_datetime(ev["timestamp"]), 
                                 reason=reason, action=action)
                processed_count += 1
            
            if stoch_evs:
                logger.info(f"[process_historical_stochastic_events_recent] {symbol}-3m: stored {len(stoch_evs)} recent stochastic events")
                
        except Exception as e:
            logger.error(f"[process_historical_stochastic_events_recent] Error processing {symbol}: {e}")
            continue
    logger.info(f"[process_historical_stochastic_events_recent] Completed: stored {processed_count} recent stochastic events")

# ===== HELPER FUNCTIONS =====

async def wait_for_market_data_ready():
    data_ready_flag = BASE_PATH / "indicators_ready.flag"
    logger.info("🔍 Waiting for ez_prices to prepare market data...")
    
    wait_start = time.time()
    while not data_ready_flag.exists():
        elapsed = time.time() - wait_start
        if elapsed > config.DATA_READY_TIMEOUT_SECONDS:
            logger.info(f"ℹ️ Data ready flag not found after {config.DATA_READY_TIMEOUT_SECONDS}s - starting with available data")
            break
            
        if int(elapsed) % config.LOG_INTERVAL_SECONDS == 0:
            logger.info(f"⏳ Waiting for market data... ({elapsed:.0f}s)")
            
        await asyncio.sleep(1)
    
    if data_ready_flag.exists():
        logger.info("✅ Market data ready!")


async def load_symbols():
    """Load and process symbols list"""
    symbols_main_list = []

    # Load symbols from symbols.json - ONLY work with these symbols
    if SYMBOLS_FILE.exists():
        async with aiofiles.open(SYMBOLS_FILE, "r") as f_sym_main:
            content = await f_sym_main.read()
            loaded_symbols_main = json.loads(content)
        if isinstance(loaded_symbols_main, list):
            symbols_main_list = loaded_symbols_main
            logger.info(f"Loaded {len(symbols_main_list)} symbols from symbols.json")
    else:
        symbols_main_list = ["BTCUSDC","ETHUSDC","BNBUSDC"]
        logger.info("ℹ️ symbols.json not found, using default symbol list")

    if not symbols_main_list:
        symbols_main_list = ["BTCUSDC","ETHUSDC","BNBUSDC"]
        logger.info("ℹ️ Using default symbol list")

    return symbols_main_list


async def run_bootstrap_ranking(symbols_main_list):
    """Run initial ranking with timeout protection"""
    bootstrap_symbols = symbols_main_list[:min(50, len(symbols_main_list))]
    logger.info(f"Bootstrapping with {len(bootstrap_symbols)} symbols")
    
    try:
        ranking_data, metadata = await asyncio.wait_for(
            initial_fetch_and_ranking(bootstrap_symbols, ["4h", "1h", "15m", "3m", "D"]),
            timeout=300
        )
        return ranking_data, metadata
    except asyncio.TimeoutError:
        logger.info("ℹ️ Bootstrap timeout - using available data")
        return [], {}
    except Exception as e:
        logger.info(f"ℹ️ Bootstrap had issues: {e} - continuing with available data")
        return [], {}


async def start_background_tasks(symbols_main_list, shutdown_event):
    """Start all background tasks"""
    tasks = []
    task_configs = [
        (ranking_loop, [symbols_main_list], "ranking_loop"),
        (signals_loop, [], "signals_loop"),
        (arrow_signals_loop, [], "arrow_signals_loop"),
        (plot_loop, [], "plot_loop"),
        (market_movement_monitor, [], "market_movement_monitor"),
        (market_index_realtime_monitor, [30], "market_index_realtime_monitor"),
        (monitor_memory, [], "monitor_memory")
    ]
    for task_func, args, task_name in task_configs:
        def make_task():
            coro = task_func(*args) if args else task_func()
            return safe_task_wrapper(coro, task_name, shutdown_event)
        task = asyncio.create_task(make_task())
        tasks.append((task, task_name))
        logger.info(f"✅ Started {task_name}")
        await asyncio.sleep(config.TASK_STAGGER_SECONDS)
    return tasks


async def run_main_monitoring_loop(tasks, shutdown_event):
    """Main monitoring loop"""
    monitor_interval = 60
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=monitor_interval)
            break
        except asyncio.TimeoutError:
            active_symbols_count = len(signals_data) if signals_data else 0
            logger.info(f"📊 Status: {active_symbols_count} active symbols, {len(tasks)} tasks running")


async def safe_task_wrapper(coro, task_name, shutdown_event=None):
    """Wrapper for tasks to ensure they restart on failure"""
    while True:
        if shutdown_event and shutdown_event.is_set():
            logger.info(f"Task {task_name} stopping due to shutdown")
            break
        try:
            await coro
        except asyncio.CancelledError:
            logger.info(f"Task {task_name} was cancelled")
            break
        except Exception as e:
            if shutdown_event and shutdown_event.is_set():
                logger.info(f"Task {task_name} stopping due to shutdown")
                break
            logger.error(f"Task {task_name} failed: {e}. Restarting in 30 seconds...")
            try:
                await asyncio.wait_for(shutdown_event.wait() if shutdown_event else asyncio.sleep(30), timeout=30)
                if shutdown_event and shutdown_event.is_set():
                    break
            except asyncio.TimeoutError:
                pass
        else:
            if shutdown_event and shutdown_event.is_set():
                logger.info(f"Task {task_name} stopping due to shutdown")
                break
            logger.warning(f"Task {task_name} exited normally. Restarting in 30 seconds...")
            try:
                await asyncio.wait_for(shutdown_event.wait() if shutdown_event else asyncio.sleep(30), timeout=30)
                if shutdown_event and shutdown_event.is_set():
                    break
            except asyncio.TimeoutError:
                pass

async def cleanup():
    """Cleanup function to close resources properly"""
    logger.info("Starting cleanup procedure...")
    
    # Save any pending signals
    try:
        await save_signals()
        logger.info("Saved pending signals during cleanup")
    except Exception as e:
        logger.error(f"Error saving signals during cleanup: {e}")
    
    # Close Redis connections
    if redis_manager:
        logger.info("Closing all Redis connections via manager...")
        for target, client in redis_manager.connections.items():
            if client:
                try:
                    await client.aclose()
                    logger.info(f"Closed '{client}' Redis connection.")
                except Exception as e:
                    logger.error(f"Error closing '{client}' Redis connection: {e}")
    
    logger.info("Cleanup completed")

async def initialize_global_rankings(ranking_data: List[Dict], metadata: Dict):
    """
    Populates global variables with the initial bootstrap data so that
    signals_loop and plot_loop have data immediately.
    """
    global RANKING_DATA, RANKING_INFO
    global fs, fsr, prox, bs, rp, rpg, rpn

    if not ranking_data:
        logger.warning("⚠️ initialize_global_rankings called with empty data.")
        return

    logger.info(f"⚙️ Initializing global structures with {len(ranking_data)} symbols...")

    # 1. Set the main list
    RANKING_DATA = ranking_data

    # 2. Build the detailed info dictionary (used by plots)
    # We use the existing build_ranking_info helper
    RANKING_INFO = build_ranking_info(RANKING_DATA, ["4h", "1h", "15m", "3m", "D"])

    # 3. Attach metadata
    if metadata:
        if 'generated_at' not in metadata:
            metadata['generated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        RANKING_INFO['metadata'] = metadata

    # 4. Populate quick-lookup scalar dictionaries (used by signals_loop)
    # Clear existing to ensure freshness
    fs.clear()
    fsr.clear()
    prox.clear()
    bs.clear()
    rp.clear()
    
    for item in RANKING_DATA:
        sym = item.get("symbol")
        if not sym: continue

        # Extract values with safe defaults
        val_fs = float(item.get("final_score_norm", 0.0))
        val_fsr = float(item.get("final_score_recent_norm", 0.0))
        val_prox = float(item.get("proximity_score_norm", 0.0))
        val_bs = float(item.get("band_score", 0.0))
        
        # Populate globals
        fs[sym] = val_fs
        fsr[sym] = val_fsr
        prox[sym] = val_prox
        bs[sym] = val_bs
        rp[sym] = val_fs  # "Ranking Points" usually maps to the final score
        
        # Initialize others if present, otherwise 0
        rpg[sym] = float(item.get("0ranking_points_global", 0.0))
        rpn[sym] = float(item.get("0ranking_points_normal", 0.0))

    logger.info(f"✅ Global rankings initialized. Memory ready for signals/plotting.")

async def main():
    global redis_manager, RANKING_DATA, RANKING_INFO

    # Set up signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()
    main_tasks = []

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        shutdown_event.set()
        for task in main_tasks:
            if hasattr(task, 'done') and not task.done():
                task.cancel()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    try:
        logger.info("🚀 Starting ez_rankings initialization...")
        redis_initialized = await initialize_redis_manager()
        if redis_initialized:
            set_global_redis_manager(redis_manager)
            logger.info("✅ Redis initialized successfully")
        else:
            logger.info("ℹ️ Redis not available - continuing with JSON data only")

        # ===== PHASE 2: DATA LOADING =====
        logger.info("📦 Loading initial data...")
        if redis_initialized:
            await start_redis_subscribers()
        else:
            logger.info("ℹ️ Skipping Redis subscribers - using file-based data only")
        await load_indicators_data()
        await load_crosses()
        load_signals()

        await wait_for_market_data_ready()
        
        # Load symbols from symbols.json
        symbols_from_file = await load_symbols()
        if not symbols_from_file:
            logger.error("❌ No symbols loaded - cannot continue")
            return
        if ENABLE_VOLUME_FILTERING:
            logger.info("🔍 Applying safe volume filtering...")
            symbols_main_list, volume_stats = await filter_symbols_by_volume_safe(
                symbols_from_file, 
                volume_threshold=VOLUME_THRESHOLD_USDT
            )
            
            if not symbols_main_list:
                logger.error("❌ No symbols passed volume filtering - disabling volume filtering")
                symbols_main_list = symbols_from_file
            else:
                logger.info(f"✅ Volume filtering applied: {volume_stats['original_count']} → {volume_stats['filtered_count']} symbols")
                logger.info(f"📊 Removed {volume_stats['disqualified_count']} low-volume tokens (< {VOLUME_THRESHOLD_USDT:,.0f} USDT)")
        else:
            logger.info("📊 Volume filtering disabled - using all symbols")
            symbols_main_list = symbols_from_file

        # Initialize caches
        await initialize_caches()
        
        # ===== PHASE 3: BOOTSTRAP PROCESSING =====
        logger.info(f"🎯 Starting bootstrap processing with {len(symbols_main_list)} symbols...")
        
        ranking_data, metadata = await run_bootstrap_ranking(symbols_main_list)
        if ranking_data:
            await initialize_global_rankings(ranking_data, metadata) 
            bootstrap_info = build_ranking_info(ranking_data, ["4h","1h","15m","3m","D"])
            metadata_bootstrap = metadata.copy() if metadata else {}
            metadata_bootstrap.setdefault('generated_at', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'))
            metadata_bootstrap.setdefault('symbol_count', len(ranking_data))
            metadata_bootstrap.setdefault('source', 'bootstrap')
            bootstrap_info['metadata'] = metadata_bootstrap
            RANKING_DATA = ranking_data
            RANKING_INFO = bootstrap_info
            logger.info(f"✅ Bootstrap rankings primed: {len(RANKING_DATA)} symbols ready for signals")
        else:
            logger.warning("⚠️ Bootstrap produced no ranking data, waiting for ranking_loop refresh")
        
        if ranking_data:
            await initialize_global_rankings(ranking_data, metadata)
            logger.info(f"✅ Bootstrap complete - {len(RANKING_DATA)} symbols ranked")
        else:
            logger.info("ℹ️ Bootstrap completed with limited data - continuing anyway")
            RANKING_DATA = []
            RANKING_INFO = {}

        # Load signals data
        load_signals()
        logger.info(f"✅ Signals data loaded - {len(signals_data)} symbols with signals")
        
        # Process historical events for immediate plotting
        await process_historical_stochastic_events(symbols_main_list[:20])
        
        # ===== PHASE 4: START BACKGROUND TASKS =====
        logger.info("🔄 Starting background tasks...")
        asyncio.create_task(process_historical_stochastic_events(symbols_main_list[:30]))

        tasks = await start_background_tasks(symbols_main_list, shutdown_event)
        
        # ===== PHASE 5: MAIN MONITORING LOOP =====
        logger.info("✅ All systems go! Entering main monitoring loop...")
        task_objects = [task for task, _ in tasks]
        main_tasks.extend(task_objects)
        await run_main_monitoring_loop(tasks, shutdown_event)
        
    except asyncio.CancelledError:
        logger.info("Main loop cancelled - shutting down")
    except Exception as e:
        logger.critical(f"💥 Critical error in main execution: {e}", exc_info=True)
    finally:
        logger.info("🛑 Shutting down...")
        for task in main_tasks:
            if not task.done():
                task.cancel()
        if main_tasks:
            await asyncio.gather(*main_tasks, return_exceptions=True)
        await cleanup()


if __name__ == "__main__":
    try:
        logger.info("=== Script execution started directly (__main__) ===")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("=== Script execution interrupted by user (KeyboardInterrupt). ===")
    except Exception as e_global_exc:
        logger.exception(f"=== Unhandled global exception during script execution: {e_global_exc} ===")
        sys.exit(1)
    finally:
        logger.info("=== Script execution __main__ block finished. ===")
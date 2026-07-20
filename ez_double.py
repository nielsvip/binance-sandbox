import argparse
import asyncio
import json
import logging
import math
import os
import random
import statistics
import sys
import tempfile
import time
import traceback
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
#from influxdb import InfluxDBClient
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import aiofiles
import aiohttp
import dateutil.parser
import psutil
import questionary
import redis.asyncio as redis
from asyncio_throttle import Throttler
from dateutil.parser import isoparse
from dotenv import load_dotenv
from filelock import FileLock

from binance import AsyncClient, BinanceSocketManager
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import subprocess
from io import StringIO

from dotenv import dotenv_values

from utils import (AccountFilter, action_logger, current_account, log_augment_action, log_reduce_action, logger, orjson_default, setup_logger)

decrypt_command = "gpg --batch --yes --decrypt .env.gpg"
decrypted_env = subprocess.run(decrypt_command, shell=True, capture_output=True, text=True).stdout
decrypted_stream = StringIO(decrypted_env)
env_vars = dotenv_values(stream=decrypted_stream)
clean_env_vars = {key: value for key, value in env_vars.items() if value is not None}
os.environ.update(clean_env_vars)
now = datetime.now(timezone.utc)
@dataclass
class Config:
    # ---------------- GLOBAL CONFIG ----------------
    BASE_PATH: str =    Path.home() / "Documents" / "binance"
    LOG_FILE =          Path.home() / "logs" / "ez_double.log"
    MIN_POSITION_SIZE:  float = 8.0  # Minimum position size in USD
    MAX_POSITION_SIZE:   float = 2500.0
    MAX_POSITION_SIZE_MEN: float = 2500.0
    MAX_POSITION_SIZE_FIN: float = 2500.0
    MAX_POSITION_SIZE_BTC: float = 5000.0
    START_POSITION_SIZE: float = 30.0  # USD
    MAX_ORDER_VALUE   :  float = 1200.0  # USD
    INCLUDE_0: bool =    False
    MAX_PRICE_FETCH_ATTEMPTS=3
    PRICE_FETCH_RETRY_DELAY=4
    ACCOUNT_KEYS: List[str] = field(default_factory=lambda: ["ang", "inf", "men", "flz"])
    PLOTS_DIR               : Path = BASE_PATH / "plots"
    DATA_DIR                : Path = BASE_PATH / "data"
    KLINES_CACHE_DIR        : Path = BASE_PATH / "klines_cache"
    RANKINGS_DIR            : Path = BASE_PATH / "rankings"
    CROSSES_FILE            : Path = BASE_PATH / "data" / "last_events.json"
    MIN_QTY_FILE            : Path = BASE_PATH / "min_qty.json"
    SYMBOL_CONFIGS_FILE     : Path = BASE_PATH / "symbol_configs.json"  
    POSITIONS_FILE          : Path = BASE_PATH / "positions.json"  # Use a template
    KLINES_CACHE_DIR        : Path = BASE_PATH / "klines"
    SYMBOLS_FILE            : Path = BASE_PATH / "symbols.json"
    SYMBOLS_ANG             : Path = BASE_PATH / "symbols_ang.json"
    SYMBOLS_INF_LONG        : Path = BASE_PATH / "symbols_inf_long.json"
    SYMBOLS_INF_SHORT       : Path = BASE_PATH / "symbols_inf_short.json"
    SYMBOLS_MEN             : Path = BASE_PATH / "symbols_men.json"
    SYMBOLS_FLZ             : Path = BASE_PATH / "symbols_flz.json"  
    SYMBOLS_ACTIVE          : Path = BASE_PATH / "symbols_active.json"    
    PRICE_CACHE_FILE        : Path = BASE_PATH / "price_cache.json"
    PRICE_CACHE_FILE_2      : Path = BASE_PATH / "price_cache_2.json"
    PRICE_CACHE_FILE_3      : Path = BASE_PATH / "price_cache_3.json"
    PRICE_CACHE_FILE_TEMPLATE:Path  =BASE_PATH / 'price_cache_{account_key}.json'
    BACKUP_PRICE_CACHES_DIR : Path = BASE_PATH / "backup_price_caches/"
    indicators_filepath     : Path = BASE_PATH / "data" / "latest_market_data.json"
    USDC_SYMBOLS    =       [ "BTCUSDC", "ETHUSDC", "XRPUSDC", "LINKUSDC", "BCHUSDC", "LTCUSDC", "BNBUSDC", "NEOUSDC", "DOGEUSDC", "SOLUSDC", "AVAXUSDC", "FILUSDC", "HBARUSDC", "1000SHIBUSDC", "ARBUSDC", "SUIUSDC", "1000PEPEUSDC", "1000BONKUSDC", "CRVUSDC", "TRUMPUSDC", "WIFUSDC", "ORDIUSDC" "BOMEUSDC", "ETHFIUSDC", "ENAUSDC", "LTCUSDC", "NEOUSDC"]
    REDIS_CHANNEL_SIGNALS: str = "signals_channel"
    MAX_CONCURRENT_ORDERS: int = 10  # for maker
    MAX_WEBHOOK_ORDERS: int = 140    # for webhook
    MAKER_ORDER_CONFIG = { "MAX_ATTEMPTS": 5, "SLEEP_BASE": 0.01, "SLEEP_STEP": 0.01, "SLEEP_MAX": 0.05, "REPLACE_EVERY": 7, "CONCURRENCY_LIMIT": 25, "ORDER_WORKERS": 10, }
    GAIN_THRESHOLD: float = 0.2
    LOSS_THRESHOLD: float = 0.2
    BIG_MOVE_THRESHOLD_PCT: float = 3.0
    MONITOR_INTERVAL: int = 60
    PRICE_DEVIATION_THRESHOLD: float = 0.3
    MAX_PRICE_FETCH_ATTEMPTS: int = 3
    PRICE_FETCH_RETRY_DELAY: int = 2
    MAX_MEMORY_GB: int = 8
    positions: Dict[str, Dict[str, Dict]] = field(default_factory=dict)
    closed_positions: Dict[str, float] = field(default_factory=dict)
    def __getitem__(self, key):
        return getattr(self, key)

config = Config()

@dataclass

class AccountConfig:
    prefix: str
    api_key: str = field(init=False)
    api_secret: str = field(init=False)
    webhook_url: str = field(init=False)
    webhook_secret: str = field(init=False)
    client: Optional[AsyncClient] = field(default=None, init=False)
    _last_used_weight: int = field(default=0, init=False)  # NEW: to track last logged weight
    def __post_init__(self):
        self.api_key = os.getenv(f"{self.prefix.lower()}_API_KEY")
        self.api_secret = os.getenv(f"{self.prefix.lower()}_API_SECRET")
        self.webhook_url = os.getenv(f"{self.prefix.lower()}_WEBHOOK_URL")
        self.webhook_secret = os.getenv(f"{self.prefix.lower()}_WEBHOOK_SECRET")
        missing = []
        if not self.api_key:
            missing.append(f"{self.prefix.lower()}_API_KEY")
        if not self.api_secret:
            missing.append(f"{self.prefix.lower()}_API_SECRET")
        if not self.webhook_url:
            missing.append(f"{self.prefix.lower()}_WEBHOOK_URL")
        if not self.webhook_secret:
            missing.append(f"{self.prefix.lower()}_WEBHOOK_SECRET")
        if missing:
            raise ValueError(f"Missing environment variables for account '{self.prefix}': {', '.join(missing)}")
    async def initialize(self):
        """Initialize the Binance client."""
        if not self.client:
            try:
                self.client = await AsyncClient.create(api_key=self.api_key, api_secret=self.api_secret)
                self._patch_client_for_rate_limit_logging(self.client)
                logger.debug(f"Initialized Binance client for account '{self.prefix}'", extra={'color': 'green'})
            except Exception as e:
                logger.error(f"Failed to initialize Binance client for account '{self.prefix}': {e}", extra={'color': 'red'})
    async def close(self):
        """Close the Binance client."""
        if self.client:
            await self.client.close_connection()
            logger.debug(f"Closed Binance client for account '{self.prefix}'", extra={'color': 'green'})
    def _patch_client_for_rate_limit_logging(self, client):
        """Patch Binance client to log X-MBX-USED-WEIGHT-1M more smartly."""
        original_request = client._request
        async def patched_request(method, uri, signed, force_params=False, **kwargs):
            response = await original_request(method, uri, signed, force_params, **kwargs)
            try:
                headers = response.headers if isinstance(response, aiohttp.ClientResponse) else {}
                used_weight = headers.get("X-MBX-USED-WEIGHT-1M")
                if used_weight:
                    used_weight_int = int(used_weight)
                    # Log only if weight jumped significantly (+50 steps)
                    if used_weight_int - self._last_used_weight >= 50:
                        logger.info(f"[{self.prefix}] [RATE LIMIT] X-MBX-USED-WEIGHT-1M: {used_weight_int}")
                        self._last_used_weight = used_weight_int
                    # Always warn if dangerously high
                    if used_weight_int > 1000:
                        logger.warning(f"[{self.prefix}] [RATE LIMIT WARNING] High API usage: {used_weight_int}/1200")
            except Exception as e:
                logger.warning(f"[{self.prefix}] Failed to log rate limit info: {e}")
            return response
        client._request = patched_request

class ColoredFormatter(logging.Formatter):
    COLORS = { 'red': '\033[91m', 'green': '\033[92m', 'yellow': '\033[93m', 'reset': '\033[0m', 'cyan': '\033[96m', }
    def format(self, record):
        color = getattr(record, 'color', 'reset').lower()
        color_code = self.COLORS.get(color, self.COLORS['reset'])
        reset_code = self.COLORS['reset']
        record.msg = f"{color_code}{record.msg}{reset_code}"
        return super().format(record)

# Main logger

logger = logging.getLogger("TradeLogger")
logger.setLevel(logging.DEBUG)
file_handler = RotatingFileHandler(f"{config.BASE_PATH}ez_double.log", maxBytes=10*1024*1024, backupCount=5, encoding='utf-8', mode='a')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter( '%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_formatter = ColoredFormatter( '%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
stream_handler.setFormatter(stream_formatter)
logger.addHandler(stream_handler)
logger.propagate = False
action_logger = logging.getLogger("ActionLogger")
action_logger.setLevel(logging.DEBUG)
action_file_handler = RotatingFileHandler(f"{config.BASE_PATH}actions.log", maxBytes=10*1024*1024, backupCount=5, encoding='utf-8', mode='a')
action_file_handler.setLevel(logging.DEBUG)
action_file_formatter = logging.Formatter( '%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
action_file_handler.setFormatter(action_file_formatter)
action_logger.addHandler(action_file_handler)
action_logger.propagate = False
async def monitor_memory():
    process = psutil.Process(os.getpid())
    while True:
        mem_bytes = process.memory_info().rss
        mem_gb = mem_bytes / (1024 ** 3)
        if mem_gb > config.MAX_MEMORY_GB:
            print(f"Memory usage exceeded: {mem_gb:.2f} GB. Restarting...")
            os.execv(sys.executable, ['python'] + sys.argv)  # Restart script
        await asyncio.sleep(60) 

def safe_fetch_float(value, default=None):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def calculate_gain(position_side, current_price, entry_price):
    if entry_price <= 0:
        return 0.0
    if position_side == 'LONG':
        return ((current_price - entry_price) / entry_price) * 100
    else:
        return ((entry_price - current_price) / entry_price) * 100

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def force_usdc_if_needed(symbol: str) -> str:
    """Correct a single symbol if it has a USDC version."""
    if symbol.endswith("USDT"):
        usdc_symbol = symbol.replace("USDT", "USDC")
        if usdc_symbol in config.USDC_SYMBOLS:
            return usdc_symbol
    return symbol

def force_usdc_in_list(symbols: list[str]) -> list[str]:
    return [force_usdc_if_needed(symbol) for symbol in symbols]

def get_most_recent_timestamp(*timestamps: Optional[datetime]) -> datetime:
    now = datetime.now(timezone.utc)
    valid = [ts for ts in timestamps if isinstance(ts, datetime)]
    return max(valid) if valid else now - timedelta(minutes=1000)    

def get_max_position_size(symbol: str, account_key: str = None) -> float:
    if account_key == 'men':
        return config.MAX_POSITION_SIZE_MEN
    if account_key == 'fin':
        return config.MAX_POSITION_SIZE_FIN
    if symbol == "BTCUSDC":
        return config.MAX_POSITION_SIZE_BTC
    return config.MAX_POSITION_SIZE

@dataclass

class Position:
    symbol: str
    position_side: str
    entry_price: float
    mark_price: float
    positionAmt: float
    initial_quantity: float
    gain: float
    max_gain: float
    prev_gain: float
    max_quantity: float
    last_augmentation_amount: float
    last_augmentation_price: float
    last_augmentation_time: Optional[datetime]
    last_reduction_amount: float
    last_reduction_price: float
    last_reduction_time: Optional[datetime]
    last_signal: str
    max_positionSize: float
    opened_at: Optional[datetime]
    last_updated: Optional[datetime]
    def to_dict(self) -> dict:
        return { "symbol": self.symbol,    "position_side": self.position_side,    "entry_price": float(self.entry_price),    "mark_price": float(self.mark_price),    "positionAmt": float(self.positionAmt),    "initial_quantity": float(self.initial_quantity),    "gain": float(self.gain),    "max_gain": float(self.max_gain),    "prev_gain": float(self.prev_gain),    "max_quantity": float(self.max_quantity), "last_augmentation_amount": float(self.last_augmentation_amount),    "last_augmentation_price": float(self.last_augmentation_price),    "last_augmentation_time": self.last_augmentation_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ') if self.last_augmentation_time else None, "last_reduction_amount": float(self.last_reduction_amount),    "last_reduction_price": float(self.last_reduction_price),    "last_reduction_time": self.last_reduction_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ') if self.last_reduction_time else None, "last_signal": self.last_signal,    "max_positionSize": float(self.max_positionSize),    "opened_at": self.opened_at.strftime('%Y-%m-%dT%H:%M:%S.%fZ') if self.opened_at else None,    "last_updated": self.last_updated.strftime('%Y-%m-%dT%H:%M:%S.%fZ') if self.last_updated else None,}
    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        def safe_float(x, default=0.0):
            if x is None:
                return default
            try:
                return float(x)
            except Exception:
                return default
        def parse_dt(s):
            if not s:
                return None
            try:
                dt = datetime.fromisoformat(s)
                # Ensure offset-aware if missing
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                return None
        # Field mapping from API response format to Position class format
        field_mapping = { "positionSide": "position_side", "entryPrice": "entry_price", "markPrice": "mark_price", "positionAmt": "positionAmt", "unRealizedProfit": "gain",
            # Add other field mappings as needed
        }
        # Create a normalized dict with mapped field names
        normalized_d = {}
        for key, value in d.items():
            if key in field_mapping:
                normalized_d[field_mapping[key]] = value
            else:
                normalized_d[key] = value
        return cls( symbol=normalized_d.get("symbol", ""), position_side=normalized_d.get("position_side", "LONG"), entry_price=safe_float(normalized_d.get("entry_price")), mark_price=safe_float(normalized_d.get("mark_price")), positionAmt=safe_float(normalized_d.get("positionAmt")), initial_quantity=safe_float(normalized_d.get("initial_quantity")), gain=safe_float(normalized_d.get("gain")), max_gain=safe_float(normalized_d.get("max_gain")), prev_gain=safe_float(normalized_d.get("prev_gain")), max_quantity=safe_float(normalized_d.get("max_quantity")), last_augmentation_amount=safe_float(normalized_d.get("last_augmentation_amount")), last_augmentation_price=safe_float(normalized_d.get("last_augmentation_price")), last_augmentation_time=parse_dt(normalized_d.get("last_augmentation_time")), last_reduction_amount=safe_float(normalized_d.get("last_reduction_amount")), last_reduction_price=safe_float(normalized_d.get("last_reduction_price")), last_reduction_time=parse_dt(normalized_d.get("last_reduction_time")), last_signal=normalized_d.get("last_signal", ""), max_positionSize=safe_float(normalized_d.get("max_positionSize"), 999999.0), opened_at=parse_dt(normalized_d.get("opened_at")), last_updated=parse_dt(normalized_d.get("last_updated")), )

# -------------- MULTI-ACCOUNT TRADE MANAGER --------------

class MultiAccountTradeManager:
    """
    Manages multiple Binance accounts, fetching positions and handling orders.
    """
    def __init__(self, accounts: Dict[str, AccountConfig],symbols,influx_host="localhost",influx_port=8086,influx_user="",influx_password="",influx_db="trading_db",measurement="positions" ):
        self.accounts = accounts
        self.config = config  # Accessing the global Config instance
        self.symbols = symbols
        self.symbols_ang=[]
        self.symbols_inf=[]
        self.symbols_men=[]
        self.symbols_flz=[]
        self.symbols_active=[]
        self.price_cache: Dict[str, Tuple[float, datetime]] = {}
        self.price_cache_2: Dict[str, Tuple[float, datetime]] = {}
        self.price_cache_3: Dict[str, Tuple[float, datetime]] = {}
        self.backup_median_prices: Dict[str, float] = {}
        self.backup_median_prices_time= datetime.min.replace(tzinfo=timezone.utc)
        self.symbol_configs: Dict[str, Dict[str, Any]] = {}  # e.g., symbol_configs[account_key][symbol]
        self.webhook_sent: Dict[str, bool] = {}
        self.webhook_signal_time: Dict[str, datetime] = {}
        self.webhook_cooldown_period = 10  # seconds
        self.indicators_filepath = config.indicators_filepath
        self.indicators_refresh_interval = 60
        self.indicators_data = {}
        self.indicators_last_loaded = 0
        self.file_last_modified = 0
        self.positions: Dict[str, Position] = {}        #Position?
        self.positions_by_account = { 'ang': {},'inf': {}, 'men': {}, 'flz': {}  }
        self.positions_dict = {}
        self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
        self.last_signal_tracker = {} 
        self.measurement = measurement
        #self.influx_client = InfluxDBClient(host=influx_host, port=influx_port,username=influx_user, password=influx_password,database=influx_db, pool_size=100 )
        self.crosses_data = {}
        self.out_dict = {}
        self.min_qty={}
        self.orders_in_progress=set()
        self.orders_executing=set()
        self.allowed_symbols = set()
        self.fallback_used = set()
        self.reenter_level={}
        self.maker_semaphore = asyncio.Semaphore(config.MAKER_ORDER_CONFIG["CONCURRENCY_LIMIT"])
        self.failed_price_counts = {}
        self.augmented_positions = {}
        self.reduced_positions = {}
        self.reduction_cooldown_map = {}
        self.augmentation_cooldown_map = {}
        self.stop_level = {}
    async def save_reenter_level(self, position_key: str): pass
    async def _save_all_accounts(self):
        """Stub method for ez_double.py - persistence not needed here"""
        pass
    def ensure_position_floats(self, position: Position) -> Position:
        """Ensure all numeric values in a Position object are floats, not strings"""
        if position is None:
            return position
        # Create a copy of the position with ensured float values
        position_dict = position.to_dict()
        for key, value in position_dict.items():
            if key in ['symbol', 'position_side', 'last_signal', 'augment_reason', 'reduction_reason']:
                continue  # Skip non-numeric fields
            if isinstance(value, str) and value.replace('.', '').replace('-', '').isdigit():
                try:
                    position_dict[key] = safe_float(value)
                except Exception:
                    pass  # Keep original value if conversion fails
        return Position.from_dict(position_dict)
    def validate_positions_floats(self):
        """Validate and fix all positions in positions_by_account to ensure numeric values are floats"""
        fixed_count = 0
        for account_key, positions_dict in self.positions_by_account.items():
            for position_key, position in positions_dict.items():
                if position is None:
                    continue
                # Check if any numeric field might be a string
                position_dict = position.to_dict()
                needs_fix = False
                for key, value in position_dict.items():
                    if key in ['symbol', 'position_side', 'last_signal', 'augment_reason', 'reduction_reason']:
                        continue
                    if isinstance(value, str) and value.replace('.', '').replace('-', '').replace(',', '').isdigit():
                        needs_fix = True
                        break
                if needs_fix:
                    fixed_position = self.ensure_position_floats(position)
                    if fixed_position:
                        positions_dict[position_key] = fixed_position
                        fixed_count += 1
        if fixed_count > 0:
            logger.info(f"[validate_positions_floats] Fixed {fixed_count} positions with string numeric values")
        return fixed_count
    async def init_async(self):
        self.min_qty = await self.load_min_qty()
        self.symbol_configs= await self.load_symbol_configs()
    async def initialize_clients(self):
        """Initialize Binance clients asynchronously."""
        await asyncio.gather(*(account.initialize() for account in self.accounts.values()))
        logger.debug("All Binance clients initialized.")
    async def _reinitialize_binance_client(self, account_key: str):
        account_config = self.accounts.get(account_key)
        if not account_config:
            logger.error(f"Account '{account_key}' does not exist, cannot reinitialize.")
            return
        await account_config.close()
        account_config.client = None
        await account_config.initialize()
        logger.info(f"Re-initialized client for {account_key}")
    async def init_redis(self):
        """Initialize the Redis client."""
        try:
            self.redis_client = redis.Redis(host='localhost', port=6379, db=0, max_connections=100 )
            await self.redis_client.ping()
            logging.info("Connected to Redis")
        except Exception as e:
            logging.error(f"Error initializing Redis: {e}")
    async def close_redis(self):
        """Close the Redis connection."""
        try:
            if self.redis_client:
                await self.redis_client.aclose()
                logger.info("Redis connection closed.")
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}")
    async def start_symbols_loader(self):
        """Periodically refreshes all symbol files every 8 minutes."""
        while True:
            await self.load_symbols_active()  # Refresh active symbols
            await self.load_symbols_files()  # Refresh other symbol files
            #logger.debug(f"Updated symbols_active: {self.symbols_active}")  # Debugging print
            await asyncio.sleep(4 * 60)  # 8-minute interval
    async def load_symbols_active(self):
        try:
            async with aiofiles.open(self.config.SYMBOLS_ACTIVE, 'r') as file:
                content = await file.read()
                if not content.strip():
                    raise ValueError(f"{self.config.SYMBOLS_ACTIVE} is empty.")
                new_symbols = [s.upper() for s in json.loads(content)]
                self.symbols_active = new_symbols  
                #logger.debug(f"Loaded active symbols: {self.symbols_active}")
        except Exception as e:
            logger.error(f"Error loading SYMBOLS_ACTIVE: {e}", exc_info=True)
            if not self.symbols_active:
                self.symbols_active = self.symbols
    async def load_symbols_files(self):
        try:
            async with aiofiles.open(self.config.SYMBOLS_ANG, "r") as f:
                content = await f.read()
                self.symbols_ang = json.loads(content)
            # Load symbols_inf_long.json
            symbols_inf_combined = []
            if os.path.exists(self.config.SYMBOLS_INF_LONG):
                async with aiofiles.open(self.config.SYMBOLS_INF_LONG, "r") as f:
                    content = await f.read()
                    symbols_inf_long = json.loads(content)
                    symbols_inf_combined.extend(symbols_inf_long)
            # Load symbols_inf_short.json
            if os.path.exists(self.config.SYMBOLS_INF_SHORT):
                async with aiofiles.open(self.config.SYMBOLS_INF_SHORT, "r") as f:
                    content = await f.read()
                    symbols_inf_short = json.loads(content)
                    symbols_inf_combined.extend(symbols_inf_short)
            self.symbols_inf = symbols_inf_combined
            async with aiofiles.open(self.config.SYMBOLS_MEN, "r") as f:
                content = await f.read()
                self.symbols_men = json.loads(content)
            async with aiofiles.open(self.config.SYMBOLS_FLZ, "r") as f:
                content = await f.read()
                self.symbols_flz = json.loads(content)
            logger.debug("Successfully loaded symbols for ang, inf, men, and flz.")
            self.symbols_ang = self.clean_symbols(self.symbols_ang)
            self.symbols_inf = self.clean_symbols(self.symbols_inf)
            self.symbols_men = self.clean_symbols(self.symbols_men)
            self.symbols_flz = self.clean_symbols(self.symbols_flz)
        except FileNotFoundError as fnf_error:
            logger.error(f"File not found: {fnf_error}")
            # Decide how to handle missing files - perhaps default to empty lists
            self.symbols_ang = []
            self.symbols_inf = []
            self.symbols_men = []
            self.symbols_flz = []
        except json.JSONDecodeError as json_err:
            logger.error(f"JSON parse error: {json_err}")
            # Also default to empty or handle differently
            self.symbols_ang = []
            self.symbols_inf = []
            self.symbols_men = []
            self.symbols_flz = []
        except Exception as e:
            logger.error(f"Unexpected error loading symbol files: {e}", exc_info=True)
            # Fallback
            self.symbols_ang = []
            self.symbols_inf = []
            self.symbols_men = []
            self.symbols_flz = []
    def clean_symbols(self,symbol_list):
        """Ensure all symbols are consistently formatted and valid."""
        return [symbol.strip().upper() for symbol in symbol_list if isinstance(symbol, str) and symbol.strip()]
    async def load_indicators_data(self):
        try:
            if os.path.exists(self.indicators_filepath):
                current_mtime = os.path.getmtime(self.indicators_filepath)
                if current_mtime == self.file_last_modified:
                    logger.debug("Indicators data is already up-to-date.")
                    return  # Skip reloading if not modified
                self.file_last_modified = current_mtime  # Update last modified time
                async with aiofiles.open(self.indicators_filepath, 'r') as f:
                    content = await f.read()
                    if not content.strip():
                        logger.warning(f"File {self.indicators_filepath} is empty. Using default indicators data.")
                        self.indicators_data = {}
                    else:
                        self.indicators_data = json.loads(content)
                self.indicators_last_loaded = time.time()
                logger.debug(f"Loaded indicators data from {self.indicators_filepath}.")
            else:
                logger.warning(f"Indicators file {self.indicators_filepath} not found.")
                self.indicators_data = {}
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON in {self.indicators_filepath}: {e}")
            self.indicators_data = {}
        except Exception as e:
            logger.error(f"Unexpected error loading indicators data: {e}")
            self.indicators_data = {}
    async def load_crosses(self):
        crosses_file = self.config.CROSSES_FILE
        logger.debug(f"Attempting to load crosses data from: {crosses_file}")
        if not os.path.exists(crosses_file):
            logger.error(f"Crosses file does not exist: {crosses_file}")
            self.crosses_data = None
            return
        try:
            async with aiofiles.open(crosses_file, "r") as f:
                content = await f.read()
            data = json.loads(content)  # <-- Use json.loads
            logger.debug(f"Crosses data loaded. Type: {type(data)}")
            if not isinstance(data, dict):
                logger.error(f"Crosses file invalid. Expected dict, got {type(data)}.")
                self.crosses_data = None
                return
            logger.debug(f"Crosses data loaded successfully. Keys: {list(data.keys())[:300]}")
            self.crosses_data = data
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding crosses file: {e}")
            self.crosses_data = None
        except Exception as e:
            logger.error(f"Unexpected error loading crosses file: {e}")
            self.crosses_data = None
    def get_position_file(self, account_key: str, position_side: str) -> str:
        """Return the file path for a given position key."""
        assert position_side in ['LONG', 'SHORT'], f"Invalid position_side: {position_side}"
        base_dir = os.path.join(config.BASE_PATH, account_key)
        os.makedirs(base_dir, exist_ok=True)  # Create the directory if it doesn't exist
        filename = f"{position_side.lower()}_positions.json"
        file_path = os.path.join(base_dir, filename)
        try:
            if not os.path.exists(file_path):
                with open(file_path, 'w') as f:
                    json.dump({}, f)  # Create an empty JSON file
                    logger.info(f"Created empty positions file: {file_path}")
        except Exception as e:
            logger.error(f"Error ensuring file existence: {file_path}. Error: {e}")
            raise
        return file_path 
    async def load_all_positions(self):
        """Load positions for all accounts using API calls instead of backup files."""
        total_loaded = 0
        # Get positions for all accounts via API calls
        for account in self.accounts.values():
            account_key = account.prefix
            if account_key not in self.positions_by_account:
                self.positions_by_account[account_key] = {}
            logger.debug(f"Fetching positions for account '{account_key}' via API")
            positions_data = await self.fetch_positions(account_key)
            # Process the fetched positions
            if positions_data:
                for position_data in positions_data:
                    try:
                        # Convert API position data to our Position object
                        position_obj = Position.from_dict(position_data)
                        if position_obj.positionAmt > 0.0 or account_key == 'flz':
                            # Create position key in the expected format
                            position_side = "LONG" if position_obj.position_side == "LONG" else "SHORT"
                            position_key = f"{account_key}:{position_obj.symbol}_{position_side}"
                            self.positions[position_key] = position_obj
                            self.positions_by_account[account_key][position_key] = position_obj
                            logger.debug(f"Loaded position {position_key}")
                            total_loaded += 1
                    except Exception as e:
                        logger.error(f"Error parsing position data for {account_key}: {e}")
        logger.debug(f"Successfully loaded {total_loaded} positions from all accounts via API.")
        # Ensure all numeric values in positions are floats
        fixed_count = self.validate_positions_floats()
        if fixed_count > 0:
            logger.info(f"[load_positions] Fixed {fixed_count} positions with string numeric values")
        #logger.info(f'{account_key}: loaded positions: {self.positions_by_account}. 592')
    async def _load_and_merge_backups(self, main_file: str, position_side: str) -> Dict[str, dict]:
        """
        Merges backup files with the main file for a given position_side and returns the combined data.
        """
        base_dir = os.path.dirname(main_file)
        backup_dir = os.path.join(base_dir, "backups")
        merged_data: Dict[str, dict] = {}
        # Load backup files
        if os.path.isdir(backup_dir):
            backup_files = [ os.path.join(backup_dir, fn) for fn in os.listdir(backup_dir) if fn.startswith(f"{position_side.lower()}_positions.json_backup_") and fn.endswith(".json")]
            backup_files.sort()  # Sort backups in ascending order
            for backup_file in backup_files:
                try:
                    async with aiofiles.open(backup_file, "r") as f:
                        raw_content = await f.read()
                        if not raw_content.strip():
                            continue  # Skip empty files
                        # Remove any trailing commas (fix for malformed files)
                        raw_content = raw_content.rstrip(',\n')
                        backup_data = json.loads(raw_content)
                        for symbol, data in backup_data.items():
                            merged_data[symbol] = data  # Overwrite with newer backups
                        logger.debug(f"Merged backup from {backup_file}")
                except Exception as e:
                    logger.error(f"Error reading backup file {backup_file}: {e}")
        if os.path.exists(main_file):
            try:
                async with aiofiles.open(main_file, "r") as f:
                    raw_main = await f.read()
                    if raw_main.strip():
                        # Remove trailing commas here as well
                        raw_main = raw_main.rstrip(',\n')
                        main_data = json.loads(raw_main)
                        for symbol, data in main_data.items():
                            merged_data[symbol] = data  # Overwrite backups with main data
                logger.debug(f"Merged main data from {main_file}")
            except Exception as e:
                logger.error(f"Error reading main file {main_file}: {e}")
        else:
            logger.warning(f"Main file not found: {main_file}")
        return merged_data
    # async def load_positions_from_influx(self, account_key: str):
    #     query_str = f"""
    #         SELECT
    #           LAST(symbol)                  AS symbol,
    #           LAST(position_side)           AS position_side,
    #           LAST(entry_price)             AS entry_price,
    #           LAST(mark_price)              AS mark_price,
    #           LAST(quantity)                AS positionAmt,
    #           LAST(initial_quantity)        AS initial_quantity,
    #           LAST(gain)                    AS gain,
    #           LAST(max_gain)                AS max_gain,
    #           LAST(prev_gain)               AS prev_gain,
    #           LAST(max_quantity)            AS max_quantity,
    #           LAST(last_augmentation_amount) AS last_augmentation_amount,
    #           LAST(last_augmentation_price)  AS last_augmentation_price,
    #           LAST(last_augmentation_time)   AS last_augmentation_time,
    #           LAST(last_reduction_amount)    AS last_reduction_amount,
    #           LAST(last_reduction_price)     AS last_reduction_price,
    #           LAST(last_reduction_time)      AS last_reduction_time,
    #           LAST(last_signal)             AS last_signal,
    #           LAST(max_positionSize)        AS max_positionSize,
    #           LAST(opened_at)               AS opened_at,
    #           LAST(last_updated_field)      AS last_updated
    #         FROM "{self.measurement}"
    #         WHERE "account_key" = '{account_key}'
    #         GROUP BY "position_key"
    #     """
    #     def do_influx_query():
    #         try:
    #             result = self.influx_client.query(query_str)
    #             return result
    #         except Exception as e:
    #             logging.error(f"Error reading positions for '{account_key}' from Influx: {e}")
    #             return None
    #     raw_result = await asyncio.to_thread(do_influx_query)
    #     if not raw_result:
    #         logging.warning(f"No data returned from InfluxDB for account '{account_key}' or query error.")
    #         return
    #     self.positions = {}
    #     series_list = raw_result.raw.get("series", [])
    #     row_count = 0
    #     for series in series_list:
    #         columns = series.get("columns", [])
    #         values = series.get("values", [])
    #         tags = series.get("tags", {})
    #         position_key_tag = tags.get("position_key", "")
    #         for row in values:
    #             row_dict = dict(zip(columns, row))
    #             pos_obj = self.row_to_position(row_dict)
    #             local_key = position_key_tag or f"{account_key}:{pos_obj.symbol}_{pos_obj.position_side}"
    #             self.positions[local_key] = pos_obj
    #             row_count += 1
    #     if account_key not in self.positions_dict:
    #         self.positions_dict[account_key] = {}
    #     for pk, pos_obj in self.positions.items():
    #         if pk.startswith(f"{account_key}:") and pos_obj.positionAmt > 0:
    #             self.positions_dict[account_key][pk] = pos_obj
    #     #logger.info(f'positions loaded for {account_key}: {self.positions}')
    #     logging.info(
    #         "Loaded the latest row for %d position_keys from InfluxDB for account '%s'.",
    #         row_count, account_key)
    # def row_to_position(self, row_dict: dict):
    #     def parse_dt(s):
    #         if not s:
    #             return None
    #         try:
    #             dt = datetime.fromisoformat(s)
    #             if dt.tzinfo is None:
    #                 dt = dt.replace(tzinfo=timezone.utc)
    #             return dt
    #         except ValueError:
    #             return None
    #     return Position(
    #         symbol=row_dict.get("symbol", ""),
    #         position_side=row_dict.get("position_side", "LONG"),
    #         entry_price=float(row_dict.get("entry_price") or 0.0),
    #         mark_price=float(row_dict.get("mark_price") or 0.0),
    #         positionAmt=float(row_dict.get("positionAmt") or 0.0),
    #         initial_quantity=float(row_dict.get("initial_quantity") or 0.0),
    #         gain=float(row_dict.get("gain") or 0.0),
    #         max_gain=float(row_dict.get("max_gain") or 0.0),
    #         prev_gain=float(row_dict.get("prev_gain") or 0.0),
    #         max_quantity=float(row_dict.get("max_quantity") or 0.0),
    #         last_augmentation_amount=float(row_dict.get("last_augmentation_amount") or 0.0),
    #         last_augmentation_price=float(row_dict.get("last_augmentation_price") or 0.0),
    #         last_augmentation_time=parse_dt(row_dict.get("last_augmentation_time")),
    #         last_reduction_amount=float(row_dict.get("last_reduction_amount") or 0.0),
    #         last_reduction_price=float(row_dict.get("last_reduction_price") or 0.0),
    #         last_reduction_time=parse_dt(row_dict.get("last_reduction_time")),
    #         last_signal=row_dict.get("last_signal", ""),
    #         max_positionSize=float(row_dict.get("max_positionSize") or 0.0),
    #         opened_at=parse_dt(row_dict.get("opened_at")),
    #         last_updated=parse_dt(row_dict.get("last_updated"))
    #     )
    # async def fetch_positions_for_all_accounts(self):
    #     """Fetch positions for all accounts."""        
    #     await asyncio.gather(*(self.fetch_positions(account_key) for account_key in self.accounts.keys()))
    async def fetch_positions(self, account_key: str):
        logger.debug(f"Starting fetch for {account_key}")
        retries = 4  # Retry up to 4 times for fetching
        positions_data = None
        for attempt in range(retries):
            try:
                account = self.accounts.get(account_key)
                if not account or not account.client:
                    raise RuntimeError(f"No client found for account '{account_key}'")
                positions_data = await account.client.futures_position_information()
                if not positions_data:
                    logger.warning(f"FETCH: No positions found for account '{account_key}'.")
                    return {}
                logger.debug(f"Fetched positions for account '{account_key}': {positions_data}")
                break  # Successfully fetched, break out of the retry loop
            except TypeError as e:
                logger.error(f"TypeError occurred while fetching positions for '{account_key}': {e}")
                logger.debug("Full traceback:", exc_info=True)
                break  # Stop retrying on a TypeError
            except BinanceAPIException as e:
                logger.error(f"Binance API error for '{account_key}': {e}")
                if e.status_code == 429:  # Rate limit hit
                    delay = 2 ** attempt  # Exponential backoff
                    logger.warning(f"Rate limit hit. Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Unexpected error while fetching positions for '{account_key}': {e}")
                logger.debug(traceback.format_exc())
            if attempt < retries - 1:
                delay = 2 ** attempt
                logger.debug(f"Retrying fetch_positions for '{account_key}' ({attempt + 1}/{retries})...")
                await asyncio.sleep(delay)
            else:
                logger.debug(f"Failed to fetch positions for '{account_key}' after {retries} attempts.")
                return {}
        try:
            ok = await self.process_account_update(account_key, positions_data)
            if not ok:
                logger.warning(f"process_account_update signaled error for {account_key}.")
            else:
                await self.reset_unupdated_positions(account_key, positions_data, datetime.now(timezone.utc))
        except Exception as e:
            logger.error(f"Error processing positions for '{account_key}': {e}")
            logger.debug(traceback.format_exc())
        return positions_data
    async def process_account_update(self, account_key: str, data: Optional[dict] = None):
        """Process account updates with position data."""
        try:
            safe = lambda x: f"{x:.2f}" if isinstance(x, (int, float)) and x is not None else "None"
            now = datetime.now(timezone.utc)
            MAX_FAILURES = 5
            updated_keys = set()
            raw_positions_data = self.extract_positions_data(data)
            for pos in raw_positions_data:
                symbol = pos.get("s") or pos.get("symbol")
                if not symbol:
                    logger.warning(f"Empty symbol in position data: {pos}")
                    continue
                # if symbol.endswith("USDT"):
                #     candidate_usdc = symbol.replace("USDT", "USDC")
                #     if candidate_usdc in config.USDC_SYMBOLS:
                #         logger.info(f"[{account_key}] Converting {symbol} to {candidate_usdc} (force USDC mode)")
                #         symbol = candidate_usdc
                raw_pos_amt = pos.get("pa") or pos.get("positionAmt")
                raw_mark_price = pos.get("markPrice")
                pos_amt = safe_fetch_float(raw_pos_amt)
                raw_mark_price_float = safe_fetch_float(raw_mark_price)
                current_price_fallback = await self.get_current_price(symbol, account_key)
                mark_price = raw_mark_price_float or current_price_fallback
                # Skip processing if we can't get valid numeric values
                if pos_amt is None or mark_price is None:
                    logger.warning(f"[process_account_update] Invalid position data for {symbol}: pos_amt={raw_pos_amt}, mark_price={raw_mark_price}")
                    continue
                current_price = safe_float(mark_price)
                if not current_price:
                    await self.handle_missing_price(symbol, MAX_FAILURES)
                    continue
                self.failed_price_counts[symbol] = 0
                positionAmt = safe_float(abs(pos_amt))
                position_side = (pos.get("ps") or pos.get("positionSide") or ("LONG" if safe_float(pos_amt) >= 0 else "SHORT")).upper()
                position_key = self.construct_position_key(account_key, symbol, position_side)
                updated_keys.add(position_key)
                entry_price_raw = safe_fetch_float(pos.get("ep") or pos.get("entryPrice"))
                entry_price = safe_float(entry_price_raw or current_price)
                gain = safe_float(calculate_gain(position_side, current_price, entry_price))
                existing_position = self.positions_by_account.setdefault(account_key, {}).get(position_key)
                if not existing_position:
                    if positionAmt > 0:
                        self.positions_by_account[account_key][position_key] = await self.create_fetched_position( symbol, position_side, positionAmt, entry_price, current_price, gain)
                    continue
                await self.update_existing_position(account_key, existing_position, position_key, current_price, gain, positionAmt)
                augment_qty = safe_float(positionAmt - existing_position.positionAmt)
                reduce_qty = safe_float(existing_position.positionAmt - positionAmt)
                if positionAmt > existing_position.positionAmt:
                    await self.handle_augmentation(existing_position, position_key, positionAmt, augment_qty, current_price, entry_price)
                    if position_key in self.reenter_level:
                        del self.reenter_level[position_key]
                        await self.save_reenter_level(position_key) 
                elif positionAmt < existing_position.positionAmt:
                    await self.handle_reduction(account_key, existing_position, position_key, positionAmt, reduce_qty, current_price, entry_price)
                elif positionAmt == existing_position.positionAmt:
                    gain = calculate_gain(position_side, current_price, entry_price)
                    if gain != existing_position.gain:
                        existing_position.prev_gain = existing_position.gain 
                    existing_position.gain = safe_float(gain)
                    existing_position.max_gain = max(safe_float(existing_position.max_gain), safe_float(existing_position.gain))
                    existing_position.mark_price = safe_float(current_price)
                    existing_position.last_updated = now
                else:
                    logger.warning(f"[process_account] {position_key} WHAT??? NO MORE NO LESS NO THE SAME??? Reduce: {reduce_qty} Augment: {safe(augment_qty)} ")
                self.positions_by_account[account_key][position_key] = existing_position
                self.positions[position_key] = existing_position
            if updated_keys:
                await self._save_all_accounts()
                updated_keys = list(updated_keys)
                random.shuffle(updated_keys)                
            else:
                logger.info(f"[process_account_update] {account_key} no open positions to monitor. 2323")
            # Ensure all numeric values in positions are floats
            fixed_count = self.validate_positions_floats()
            if fixed_count > 0:
                logger.info(f"[process_account] Fixed {fixed_count} positions with string numeric values")
            return True
        except Exception as e:
            symbol_str = symbol if 'symbol' in locals() else 'unknown'
            logger.error(f"Error in process_account_update for {account_key} (symbol: {symbol_str}): {e}")
            logger.debug(traceback.format_exc())
            return False         
    def extract_positions_data(self, data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "a" in data and "P" in data["a"]:
            return data["a"]["P"]
        logger.warning(f"Unexpected data format: {data}")
        return []
    async def handle_missing_price(self, symbol, max_failures):
        self.failed_price_counts[symbol] = self.failed_price_counts.get(symbol, 0) + 1
        if self.failed_price_counts[symbol] >= max_failures:
            logger.error(f"Exceeded max failures for {symbol}. Closing position.")
    async def create_fetched_position(self, symbol, position_side, positionAmt, entry_price, current_price, gain):
        now = datetime.now(timezone.utc)
        position = Position( symbol=symbol, position_side=position_side, entry_price=safe_float(entry_price), mark_price=safe_float(current_price), positionAmt=safe_float(positionAmt), initial_quantity=safe_float(positionAmt), gain=safe_float(gain), max_gain=safe_float(gain), prev_gain=0.0, max_quantity=safe_float(positionAmt), last_augmentation_amount=safe_float(positionAmt), last_augmentation_price=safe_float(entry_price), last_augmentation_time=now - timedelta(minutes=100), last_reduction_amount=0.0, last_reduction_price=safe_float(entry_price), last_reduction_time=now - timedelta(minutes=241), last_signal="", max_positionSize=safe_float(get_max_position_size(symbol)), opened_at=now, last_updated=now )
        # All numeric values are already converted to floats via safe_float calls
        return position
    async def update_existing_position(self, account_key, position, position_key, current_price, gain, positionAmt):
        now = datetime.now(timezone.utc)
        position.mark_price = safe_float(current_price)
        if safe_float(gain) != position.gain: position.prev_gain = position.gain
        position.max_gain = max(safe_float(gain), safe_float(position.max_gain))
        position.gain = safe_float(gain)
        position.last_updated = now
       # await monitor_entries( order_queue=self.order_queue, trade_manager=self, account_key=account_key, position_keys=position_key )
    async def handle_augmentation(self, position, position_key, positionAmt, augment_qty, current_price, entry_price):
        now = datetime.now(timezone.utc) 
        position.last_augmentation_amount = safe_float(augment_qty)
        position.last_augmentation_price = safe_float(current_price)
        position.last_augmentation_time = now - timedelta(minutes=3)
        position.positionAmt = safe_float(positionAmt)
        position.entry_price = safe_float(entry_price)
        position.max_quantity = max(safe_float(position.max_quantity), safe_float(positionAmt))
        position.last_updated = now
        self.augmented_positions[position_key] = now
    async def handle_reduction(self, account_key, position, position_key,  positionAmt, reduce_qty, current_price, entry_price):
        now = datetime.now(timezone.utc) 
        is_tiny_position = reduce_qty * current_price <= self.config.MIN_POSITION_SIZE * 2
        position.last_reduction_time = now 
        if safe_float(reduce_qty) > position.last_reduction_amount:
            position.last_reduction_amount = safe_float(reduce_qty)
        position.last_reduction_price = safe_float(current_price)
        position.entry_price = safe_float(entry_price)
        position.positionAmt = safe_float(positionAmt)
        position.max_quantity = max(safe_float(position.max_quantity), safe_float(positionAmt))
        #position.prev_gain = position.gain
        if is_tiny_position:
            position.gain = safe_float(0.0 if position.prev_gain < 0 else calculate_gain(position.position_side, current_price, entry_price))
        else:
            position.gain = safe_float(calculate_gain(position.position_side, current_price, entry_price))
        position.last_updated = now
        self.reenter_level[position_key] = { "reenter_level": safe_float(current_price), "reenter_amount": safe_float(position.last_reduction_amount), "timestamp": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "reason": 'manual reduce position' }
        logger.debug(f"[process_account] {position_key} Reenter level set: {self.reenter_level[position_key]}. 2313")
        await self.save_reenter_level(position_key)
        if position_key in self.augmented_positions:
            del self.augmented_positions[position_key] 
            #await self.save_augmented_positions(account_key) 
        if position_key in self.stop_level:
            del self.stop_level[position_key]  
    async def reset_unupdated_positions(self, account_key: str, fetched_positions: list, now: datetime, order_queue= None):
        now = datetime.now(timezone.utc) 
        try:
            #logger.debug(f'[reset_unupdated_positions] fetched: {fetched_positions}')
            updated_keys = set()
            for pos in fetched_positions:
                symbol = pos.get("symbol")
                position_side = pos.get("positionSide", "LONG" if float(pos.get("positionAmt", 0)) >= 0 else "SHORT").upper()
                position_key = f"{account_key}:{symbol}_{position_side}"
                updated_keys.add(position_key)
            account_positions = self.positions_by_account.setdefault(account_key, {})
            all_keys = set(account_positions.keys())
            not_updated_keys = all_keys - updated_keys
            valid_not_updated_keys = []
            for pk in list(updated_keys): 
                try:
                    _ak, _s, _ps = self.parse_position_key(pk)
                    if is_symbol_allowed(_ak, _s, self): 
                        valid_not_updated_keys.append(pk)
                    else:
                        logger.debug(f"[{account_key}] [process_account_update] Filtering out disallowed key: {pk}")
                except ValueError as e: # From parse_position_key
                    logger.warning(f"[{account_key}] [process_account_update] Malformed key {pk}, cannot check allowance: {e}")
            if valid_not_updated_keys:
                random.shuffle(valid_not_updated_keys)
                #await monitor_entries( order_queue=self.order_queue, trade_manager=self, account_key=account_key, position_keys=valid_not_updated_keys  )
                logger.info(f"[process_account_update] {account_key} Sent {len(valid_not_updated_keys)} valid keys to monitor_entries (out of {len(updated_keys)} originally). {valid_not_updated_keys}. 2321")
            else:
                pass
               # logger.info(f"[{account_key}] [process_account_update] No valid updated keys to send to monitor_entries after filtering.")
            if symbol in ["NOTUSDT", "TAOUSDT", "PYTHUSDT"]:
                return
            for pk in not_updated_keys:
                position_obj = account_positions.get(pk)
                if not position_obj or not isinstance(position_obj, Position):
                    logger.warning(f"Invalid or unknown position type for '{pk}'. Skipping reset.")
                    continue
                leftover_symbol = position_obj.symbol
                mark_price = await self.get_current_price(leftover_symbol, account_key)
                if not mark_price:
                    mark_price = position_obj.mark_price
                if position_obj.positionAmt != 0.0:
                    # Reset the position
                    position_obj.prev_gain = position_obj.gain
                    position_obj.gain = safe_float(0.0)
                    position_obj.last_reduction_amount = safe_float(position_obj.positionAmt if position_obj.positionAmt > position_obj.last_reduction_amount else position_obj.last_reduction_amount)
                    position_obj.last_reduction_price = safe_float(position_obj.mark_price)
                    position_obj.last_reduction_time = now
                    position_obj.positionAmt = safe_float(0.0)
                    position_obj.last_updated = now
                    position_obj.mark_price = safe_float(mark_price)            
                    if pk not in self.reenter_level:
                        self.reenter_level[pk] = {"reenter_level": safe_float(mark_price),  "reenter_amount": safe_float(position_obj.last_reduction_amount), "timestamp": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "reason": 'reset unupdated position. 1385', }
                #>        logger.debug(f"[RESET_UNUPDATED] {position_key} Reenter level set for {position_key}: {self.reenter_level[position_key]}")
                        await self.save_reenter_level(position_key) 
                if position_obj.positionAmt == 0.0 and now - position_obj.last_reduction_time > timedelta(days=2):
                    position_obj.max_quantity = safe_float(0.0)
                    position_obj.mark_price = safe_float(mark_price)
                    position_obj.last_updated = now    
                    if pk in self.stop_level:
                        del self.stop_level[pk]  
                    if pk in self.augmented_positions:
                        del self.augmented_positions[pk] 
                    #    await self.save_augmented_positions(account_key)                 
                else:
                    position_obj.mark_price = safe_float(mark_price)
                    position_obj.last_updated = now
                    if pk in self.stop_level:
                        del self.stop_level[pk]  
                    if pk in self.augmented_positions:
                        del self.augmented_positions[pk] 
                    #    await self.save_augmented_positions(account_key)                                                 
                # Replace the updated object
                account_positions[pk] = position_obj
            valid_symbols = getattr(self, f"symbols_{account_key}")
            filtered_keys = { pk for pk in not_updated_keys if pk.split(":")[1].split("_")[0] in valid_symbols }
            for pk in filtered_keys:
                position_obj = account_positions.get(pk)
                if not position_obj or not isinstance(position_obj, Position):
                    logger.warning(f"Invalid or unknown position type for '{pk}'. Skipping reset.")
                    continue
           # await monitor_entries( order_queue=self.order_queue, trade_manager=self, account_key=account_key, position_keys=filtered_keys )
               # logger.info(f"{self.account_positions[pk]}")
             #   (self.save_position_to_influx(position_obj, account_key))
                #logger.debug(f"[PROCESS_ACCOUNT_UPDATE] {pk} Position dead: positionAmt set to 0.")
        except Exception as e:
            logger.error(f"Error resetting unupdated positions {not_updated_keys} for account '{account_key}': {e}")
            logger.debug(traceback.format_exc())
    def get_account_positions(self, account_key, side=None):
        subdict = self.positions_by_account.get(account_key, {})
        if side is None:
            return subdict
        return { k: v for k, v in subdict.copy().items() if v.position_side.upper() == side.upper()}
    async def load_symbol_configs(self) -> dict:
        file_path = self.config.SYMBOL_CONFIGS_FILE
        if not os.path.exists(file_path):
            logger.error(f"Symbol config file {file_path} does not exist.")
            return {}
        try:
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
            return json.loads(content)
        except FileNotFoundError:
            logger.error(f"Symbol config file {file_path} not found.")
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {file_path}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error loading symbol configs: {e}")
        return {}
    def get_symbol_config(self, symbol):
        return self.symbol_configs.get(symbol)
    def get_position_keys_for_symbol(self, account_key: str, symbol: str) -> List[str]:
        """Return all position keys (LONG/SHORT) for this account & symbol."""
        results = []
        for side in ("LONG", "SHORT"):
            pk = self.construct_position_key(account_key, symbol, side)
            if pk in self.positions:
                results.append(pk)
        return results
    async def read_json_file(self, filepath: str) -> dict:
        """Helper to safely read a JSON file. Logs if invalid/missing."""
        if not os.path.exists(filepath):
            return {}
        try:
            async with aiofiles.open(filepath, "r") as f:
                content = await f.read()
            return json.loads(content)
        except Exception as e:
            logger.debug(f"Could not read {filepath}: {e}")
            return {}
    async def load_min_qty(self) -> Dict[str, float]:
        file_path = self.config.MIN_QTY_FILE
        if not os.path.exists(file_path):
            logger.error(f"Minimum quantity file {file_path} not found.")
            return {}
        try:
            async with aiofiles.open(file_path, 'r') as f:
                content = await f.read()
            min_qty_data = json.loads(content)
            min_qty_flat = { symbol: float(details['minQty']) for symbol, details in min_qty_data.items() }
            logger.debug("Loaded minimum quantities from JSON.")
            return min_qty_flat
        except FileNotFoundError:
            logger.error(f"Minimum quantity file {file_path} not found.")
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {file_path}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error loading minimum quantities: {e}")
        return {}
    async def initialize_caches(self):
        """Initialize and load price caches during setup."""
        async def load_cache(file_path):
            if os.path.exists(file_path):
                try:
                    async with aiofiles.open(file_path, "r") as f:
                        return json.loads(await f.read())
                except Exception as e:
                    logger.error(f"Error loading cache file {file_path}: {e}")
            return {}
        self.price_cache = await load_cache(config.PRICE_CACHE_FILE)
        self.price_cache_2 = await load_cache(config.PRICE_CACHE_FILE_2)
        self.price_cache_3 = await load_cache(config.PRICE_CACHE_FILE_3)
    async def update_backup_median_prices(self):
        now = datetime.now(timezone.utc)
        if now - self.backup_median_prices_time < timedelta(seconds=60):
            return
        backup_folder = os.path.join(os.path.dirname(self.config.PRICE_CACHE_FILE), "backup_price_caches")
        if not os.path.isdir(backup_folder):
            logger.warning(f"[WARN] Backup folder {backup_folder} not found.")
            return
        self.backup_median_prices = await self.compute_backup_medians_from_multiple_backups( backup_folder, max_backups=3 )
        logger.debug(f"[INFO] Updated backup_median_prices with {len(self.backup_median_prices)} symbols.")
        self.backup_median_prices_time=now
    async def compute_backup_medians_from_multiple_backups( self, backup_folder: str, max_backups: int = 3 ) -> Dict[str, float]:
        now = datetime.now(timezone.utc)
        backup_files = [f for f in os.listdir(backup_folder) if f.endswith(".json")]        
        full_paths = []
        for f in backup_files:
            path = os.path.join(backup_folder, f)
            if os.path.exists(path):  # <- This prevents FileNotFoundError
                full_paths.append(path)
        try:
            full_paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except FileNotFoundError as e:
            logger.info(f"[WARN] Skipping file missing during getmtime: {e}")
        full_paths = full_paths[:max_backups]
        symbol_prices = {}
        for path in full_paths:
            try:
                async with aiofiles.open(path, "r") as f:
                    content = await f.read()
                data = json.loads(content)
                if not isinstance(data, dict):
                    continue
                for sym, info in data.items():
                    p = info.get("price")
                    if isinstance(p, (int, float)) and p > 0:
                        symbol_prices.setdefault(sym, []).append(p)
            except Exception as e:
                logger.info(f"[WARN] Could not read {path}: {e}")
        medians: Dict[str, float] = {}
        for sym, prices in symbol_prices.items():
            if prices:
                medians[sym] = statistics.median(prices)
        return medians
    def find_position_by_symbol(self,symbol: str):
        matching_positions = [ pos for key, pos in self.out_dict.items() if pos and pos.symbol == symbol  ]
        if not matching_positions:
            return None
        return max(matching_positions, key=lambda pos: pos.last_updated)
    async def get_current_price(self, symbol: str, max_cache_age_seconds: int = 600) -> Optional[float]:
        now = datetime.now(timezone.utc)
        candidate_prices = []  # (price, timestamp, source)
        reference_price = self.backup_median_prices.get(symbol)  # Start with median as a fallback
        def parse_timestamp(ts_str: str) -> datetime:
            """Parse ISO or epoch timestamp to datetime with timezone."""
            try:
                if isinstance(ts_str, (int, float)):
                    return datetime.fromtimestamp(ts_str, tz=timezone.utc)
                return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
            except Exception:
                return now
        def is_stale(ts: datetime) -> bool:
            """Check if a timestamp is stale based on the max cache age."""
            return (now - ts).total_seconds() > max_cache_age_seconds
        def is_outlier(p: float, ref: float, threshold=0.3) -> bool:
            """Determine if a price is an outlier compared to the reference."""
            p_float = safe_float(p)
            if not p_float or p_float <= 0:
                return True
            ref_float = safe_float(ref) if ref is not None else None
            if ref_float is None or ref_float <= 0:
                return False  # No reference price means no outlier judgment
            return abs(p_float - ref_float) / ref_float > threshold
        async def load_json_prices(file_path: str, price_key: str, ts_key: str, source: str):
            data = await self.read_json_file(file_path)
            if symbol in data:
                price = data[symbol].get(price_key)
                ts_str = data[symbol].get(ts_key)
                price_float = safe_fetch_float(price)
                if price_float:
                    candidate_prices.append((price_float, parse_timestamp(ts_str), source))
        if symbol in self.price_cache_2:
            try:
                cache_data = self.price_cache_2[symbol]
                if isinstance(cache_data, (list, tuple)) and len(cache_data) >= 3:
                    cached_price_raw, cached_ts, cached_source = cache_data[0], cache_data[1], cache_data[2]
                elif isinstance(cache_data, dict):
                    cached_price_raw, cached_ts, cached_source = cache_data.get("price"), cache_data.get("timestamp"), cache_data.get("source")
                else:
                    cached_price_raw, cached_ts, cached_source = cache_data
                cached_price = safe_fetch_float(cached_price_raw)
                if cached_price is not None and isinstance(cached_price, (int, float)) and cached_price > 0.0:
                    if isinstance(cached_ts, str):
                        cached_ts = parse_timestamp(cached_ts)
                    if not is_stale(cached_ts):
                        logger.debug(f"{symbol} => Using in-memory cached price {cached_price} from {cached_source}")
                        return float(cached_price)
            except (ValueError, TypeError) as e:
                logger.warning(f"{symbol} => Error unpacking cache data: {e}, reading from files instead")
            except Exception as e:
                logger.warning(f"{symbol} => Unexpected error reading cache: {e}, reading from files instead")
        if symbol in self.positions:
            pos_info = self.find_position_by_symbol(symbol)
            if pos_info:
                price = pos_info.get("mark_price")
                ts = parse_timestamp(pos_info.get("last_updated"))
                price_float = safe_fetch_float(price)
                if price_float:
                    candidate_prices.append((price_float, ts, "Positions Dict"))
        for folder in ["ang", "inf", "men", "flz"]:
            for pfile in ["long_positions.json", "short_positions.json"]:
                fpath = os.path.join(self.config.BASE_PATH, folder, pfile)
                await load_json_prices(fpath, "mark_price", "last_updated", f"{folder}/{pfile}")
        for cfile in ["price_cache.json", "price_cache_3.json"]:
            cpath = os.path.join(self.config.BASE_PATH, cfile)
            await load_json_prices(cpath, "price", "timestamp", cfile)
        klines_path = os.path.join(self.config.KLINES_CACHE_DIR, f"{symbol}_1m.json")
        try:
            kline_data = await self.read_json_file(klines_path)
            if kline_data:
                latest_kline = max(kline_data, key=lambda x: x["timestamp"])
                close_price = safe_fetch_float(latest_kline.get("close"))
                if close_price:
                    candidate_prices.append((close_price, parse_timestamp(latest_kline["timestamp"]), "Klines Cache"))
        except Exception as e:
            logger.warning(f"{symbol} => Error reading klines cache: {e}")
        candidate_prices.sort(key=lambda x: x[1], reverse=True)  # Sort by timestamp
        logger.debug(f'>>>CANDIDATES: {symbol}: {candidate_prices}')
        valid_prices = [safe_float(p) for p, _, _ in candidate_prices if safe_float(p) and safe_float(p) > 0]
        if valid_prices and reference_price is None:
            reference_price = statistics.median(valid_prices)
        for price, ts, source in candidate_prices:
            price_float = safe_float(price)
            if price_float and not is_outlier(price_float, reference_price, threshold=0.3) and not is_stale(ts):
                logger.debug(f"{symbol} => Using candidate price {price_float} from {source}")
                self.price_cache_2[symbol] = (price_float, ts, source)  # Update cache
                return price_float
        if reference_price:
            ref_price_float = safe_float(reference_price)
            if ref_price_float:
                logger.debug(f"{symbol} => Falling back to backup median price: {ref_price_float}")
                self.price_cache_2[symbol] = (ref_price_float, now, "Backup Median")
                return ref_price_float
        attempt = 0
        MAX_PRICE_FETCH_ATTEMPTS = config.MAX_PRICE_FETCH_ATTEMPTS
        while attempt < MAX_PRICE_FETCH_ATTEMPTS:
            try:
                # Iterate accounts to distribute calls
                for account_key in self.config.ACCOUNT_KEYS:
                    account = self.accounts.get(account_key)
                    if not account or not account.client:
                        continue
                    mark_price_data = await account.client.futures_mark_price(symbol=symbol)
                    fetched_price = float(mark_price_data.get("markPrice", 0))
                    if fetched_price > 0:
                        # Check outlier
                        if reference_price and is_outlier(fetched_price, reference_price, threshold=0.3):
                            logger.warning(f"{symbol} => Fetched price {fetched_price} from {account_key} outlier vs {reference_price}")
                            continue
                        # Accept
                        self.price_cache[symbol] = (fetched_price, now, f"API/{account_key}")
                        logger.debug(f"{symbol} => Fetched price {fetched_price} from account '{account_key}'")
                        return fetched_price
            except Exception as e:
                logger.error(f"{symbol} => Error fetching price from Binance API (attempt={attempt}): {e}", exc_info=True)
            attempt += 1
            backoff = config.PRICE_FETCH_RETRY_DELAY
            logger.info(f"{symbol} => Retry fetching price after {backoff:.2f} seconds (attempt={attempt}).")
            await asyncio.sleep(backoff)        
        logger.warning(f"{symbol} => No valid price found. Returning None.")
        return None
    def construct_position_key(self,account_key: str, symbol: str, position_side: str) -> str:
        if not account_key or not symbol or not position_side:
            raise ValueError(f"Invalid parameters for position_key: account_key={account_key}, symbol={symbol}, position_side={position_side}")
        return f"{account_key}:{symbol}_{position_side}"
    def parse_position_key(self, position_key: str):
        if not position_key or not isinstance(position_key, str):
            raise ValueError(f"Invalid position_key for parsing: {position_key}")
        try:
            parts = position_key.split(":", 1)
            if len(parts) != 2:
                raise ValueError(f"Position key must contain exactly one colon: '{position_key}'")
            account_key, symbol_side = parts
            symbol_parts = symbol_side.rsplit("_", 1)
            if len(symbol_parts) != 2:
                raise ValueError(f"Symbol side must contain underscore separator: '{symbol_side}'")
            symbol, position_side = symbol_parts
            return account_key, symbol, position_side
        except ValueError as e:
            logger.error(f"Invalid position_key format: '{position_key}'. Expected format 'account_key:SYMBOL_SIDE'.")
            raise ValueError(f"Invalid position_key format: '{position_key}'. Expected format 'account_key:SYMBOL_SIDE'.") from e
    def is_valid_stoch_event(self, latest_data: dict, max_valid_minutes: int = 2) -> bool:
        if not latest_data:
            return False
        event_timestamp_str = latest_data.get("timestamp")
        if not event_timestamp_str:
            return False
        event_timestamp = dateutil.parser.parse(event_timestamp_str)
        now = datetime.now(timezone.utc)
        time_difference = now - event_timestamp
        return time_difference <= timedelta(minutes=max_valid_minutes)
    def get_relevant_accounts_for_symbol(self, symbol: str) -> Set[str]:
        relevant = set()
        if symbol in self.symbols_inf:
            relevant.add("inf")
        if symbol in self.symbols_men:
            relevant.add("men")
        if symbol in self.symbols_flz:
            relevant.add("flz")
        if hasattr(self, "symbols_ang") and symbol in self.symbols_ang:
            relevant.add("ang")
        items_snapshot = list(self.positions.items())
        for position_key, position_obj in items_snapshot:
            acc_key, pos_symbol, side = self.parse_position_key(position_key)
            if pos_symbol == symbol:
                relevant.add(acc_key)
        return relevant
    async def cancel_order_on_binance(self, account_key, symbol, order_id):
        """Cancel an open order on Binance."""
        try:
            account_config = self.accounts.get(account_key)
            if not account_config or not account_config.client:
                logger.error(f"No Binance client available for account {account_key}.")
                return False
            client = account_config.client
            order_status = await asyncio.wait_for(client.futures_get_order(symbol=symbol, orderId=order_id),timeout=5)
            if order_status['status'] in ['NEW', 'PARTIALLY_FILLED']:
                logger.debug(f"Attempting to cancel order {order_id} for symbol {symbol}")
                result = await asyncio.wait_for(client.futures_cancel_order(symbol=symbol, orderId=order_id), timeout=10)
                logger.debug(f"[cancel_order] Order {order_id} for {symbol} on '{account_key}' . Attempting to cancel 1719")
                if result['status'] == 'CANCELED':
                    logger.debug(f"Successfully canceled order {order_id} for symbol {symbol}.")
                    return True
                else:
                    logger.error(f"Failed to cancel order {order_id} for symbol {symbol}, Status: {result['status']}")
                    return False
            else:
                logger.debug(f"Order {order_id} is already in {order_status['status']} state. No cancellation needed.")
                return False
        except asyncio.TimeoutError:
            logger.error(f"Timeout while trying to cancel order {order_id} for symbol {symbol}")
            return False
        except BinanceAPIException as e:
            logger.error(f"Binance API exception while canceling order {order_id} for symbol {symbol}: {e}")
            if "Session is closed" in str(e):
                logger.error(f"Detected 'Session is closed'  Restarting binance. 4411")
                await self._reinitialize_binance_client(account_key)
                #sys.exit(0)     
            return False
        except Exception as e:
            logger.error(f"Error canceling order {order_id} for symbol {symbol}: {e}")
            return False
    async def place_stop_orders(self, client, symbol, quantity, side, position_side, price):
        """
        Places stop loss and trailing stop loss orders.
        """
        try:
            price_precision = self.symbol_configs[symbol]['price_precision']
            tick_size = self.symbol_configs[symbol]['tick_size']
            indicators = self.indicators_data.get(symbol, {})
            dc_low_3m = float(indicators.get("dc_low_3m", 0.0) or 0.0)
            dc_high_3m = float(indicators.get("dc_high_3m", 0.0) or 0.0)
            if position_side.upper() == 'LONG':
                stop_loss_price = dc_low_3m if dc_low_3m > 0.0 else round(price * (1 - 0.02) - tick_size, price_precision)
                stop_loss_price = round(stop_loss_price, price_precision)
                stop_side = 'SELL'
            elif position_side.upper() == 'SHORT':
                stop_loss_price = dc_high_3m if dc_high_3m > 0.0 else round(price * (1 + 0.02) + tick_size, price_precision)
                stop_loss_price = round(stop_loss_price, price_precision)
                stop_side = 'BUY'
            else:
                logger.error(f"Invalid position_side '{position_side}' for {symbol}.")
                return False
            open_orders = await client.futures_get_open_orders(symbol=symbol)
            existing_stops = [o for o in open_orders if o.get('type') in ['STOP_MARKET', 'TRAILING_STOP_MARKET', 'STOP', 'TAKE_PROFIT_MARKET'] and o.get('positionSide') == position_side]
            for existing in existing_stops:
                try:
                    await client.futures_cancel_order(symbol=symbol, orderId=existing.get('orderId'))
                    logger.info(f"Cancelled existing stop order {existing.get('orderId')} for {symbol} before placing new one")
                except Exception as e:
                    logger.warning(f"Failed to cancel existing stop order for {symbol}: {e}")
            logger.info(f"Placing Stop Loss order for {symbol} at {stop_loss_price} | Side: {stop_side} (dc_low_3m={dc_low_3m:.6f}, dc_high_3m={dc_high_3m:.6f})")
            stop_loss_order = await client.futures_create_order(symbol=symbol, side=stop_side, type='STOP_MARKET', stopPrice=stop_loss_price, quantity=quantity, positionSide=position_side)
            logger.info(f"Stop Loss order placed: {stop_loss_order}")
            return True
        except Exception as e:
            logger.info(f"Error placing stop orders for {symbol}: {e}")
            return False
    def is_same_direction(self, side, position_side) -> bool:
        return (side == "BUY" and position_side == "LONG") or (side == "SELL" and position_side == "SHORT")
    def is_opposite_direction(self, side, position_side) -> bool:
        return (side == "SELL" and position_side == "LONG") or (side == "BUY" and position_side == "SHORT")
    async def safe_place_order(self, account_key, position_key, symbol, quantity, current_price, side, position_side, unique_id, use_webhook=False, is_full_close=False, webhook_url=None, webhook_secret=None, reason='' ):
        logger.debug(f"[safe_place_order] {position_key} {side} ${quantity * current_price} '{account_key}' Reason: {reason}")
        position_key = self.construct_position_key(account_key, symbol, position_side)
        position = self.positions_by_account.get(account_key, {}).get(position_key)
        positionAmt = position.positionAmt if position else 0
        gain = position.gain if position else 0.0
        if not unique_id:
            unique_id = generate_unique_id(position_key, side, reason)
        if unique_id in self.orders_in_progress:
            logger.debug(f"[safe_place_order] {reason} Already in progress {position_key}, skipping")
            return False
        self.orders_in_progress.add(unique_id)
        account = self.accounts.get(account_key)
        try:
            if use_webhook or not account or not account.client:
                logger.info(f"[safe_place_order] {reason} Webhook for {position_key}")
                await self.send_webhook_once(self.construct_position_key(account_key, symbol, position_side),  account_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close, webhook_url, webhook_secret, reason)
                return True
            success = await self.place_maker_order(account_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close, webhook_url, webhook_secret, reason )
            if success:
                logger.debug(f"[safe_place_order] {reason} Maker order success {position_key}")
                return True
            else:
                logger.warning(f"[safe_place_order] {reason} Maker failed for {position_key}, fallback webhook")
                await self.send_webhook_once(self.construct_position_key(account_key, symbol, position_side),  account_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close, webhook_url, webhook_secret, reason)
                return True
        except Exception as e:
            logger.error(f"[safe_place_order] {reason} Exception {position_key}: {e}", exc_info=True)
            await self.send_webhook_once(self.construct_position_key(account_key, symbol, position_side),  account_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close, webhook_url, webhook_secret, reason)
            return True
        finally:
            self.orders_in_progress.discard(unique_id)
    async def place_maker_order( self, account_key: str, symbol: str, quantity: float, current_price: float, side: str, position_side: str, unique_id, is_full_close: bool,webhook_url: str, webhook_secret: str, reason: str) -> bool:
        account = self.accounts.get(account_key)
        position_key = self.construct_position_key(account_key, symbol, position_side)
        if not account or not account.client:
            logger.warning(f"[PLACE_MAKER_ORDER] No client for {account_key}, fallback to webhook.")
            await self.send_webhook_once(position_key, account_key, symbol, quantity, current_price,side, position_side, unique_id, is_full_close,webhook_url, webhook_secret, reason)
            return True
        client = account.client
        position = self.positions_by_account.get(account_key, {}).get(position_key)
        if not position:
            logger.warning(f"[PLACE_MAKER_ORDER] No position found for {position_key}, fallback to webhook.")
            await self.send_webhook_once(position_key, account_key, symbol, quantity, current_price,side, position_side, unique_id, is_full_close,webhook_url, webhook_secret, reason)
            return True
        symbol_config = self.get_symbol_config(symbol)
        if not symbol_config:
            logger.warning(f"[PLACE_MAKER_ORDER] No symbol config for {symbol}, fallback to webhook.")
            await self.send_webhook_once(position_key, account_key, symbol, quantity, current_price,side, position_side, unique_id, is_full_close,webhook_url, webhook_secret, reason)
            return True
        price_precision = symbol_config.get("price_precision", 2)
        quantity_precision = symbol_config.get("quantity_precision", 3)
        tick_size = float(symbol_config.get("tick_size", 0.001))
        step_size = float(symbol_config.get("step_size", 0.001))
        
        adjusted_quantity = max(round(math.floor(quantity / step_size) * step_size, quantity_precision), step_size)
        if adjusted_quantity <= 0:
            logger.warning(f"[PLACE_MAKER_ORDER] {position_key} adjusted_quantity={adjusted_quantity} <=0, skipping.")
            return False
            
        reason_upper = str(reason).upper() if reason else ""
        is_quick_order = any(k in reason_upper for k in ['QUICK'])
        TIMEOUT = 5.0 if is_quick_order else 10.0
        POLL_INTERVAL = 0.1
        RETRY_DELAY = 0.05
        qty_str = f"{adjusted_quantity}"

        async with self.maker_semaphore:
            self.orders_executing.add(position_key)
            try:
                placement_start_time = time.time()
                active_order_id = None
                active_price = None
                executed_qty = 0.0
                filled = False

                while time.time() - placement_start_time < TIMEOUT:
                    try:
                        await asyncio.to_thread(client.futures_countdown_cancel_all, symbol=symbol, countdownTime=5000)
                        ticker = await asyncio.to_thread(client.futures_symbol_ticker, symbol=symbol)
                        lp = float(ticker['price'])
                        elapsed = time.time() - placement_start_time

                        if elapsed < 5.0:
                            book = await asyncio.to_thread(client.futures_order_book, symbol=symbol, limit=5)
                            bb, ba = float(book['bids'][0][0]), float(book['asks'][0][0])
                            if side == 'BUY':
                                target = bb + tick_size
                                target_price = target if target < ba else bb
                            else:
                                target = ba - tick_size
                                target_price = target if target > bb else ba
                        else:
                            target_price = lp - tick_size if side == 'BUY' else lp + tick_size
                            
                        target_price_str = f"{round(target_price, price_precision):.{price_precision}f}"

                        if active_order_id and active_price == target_price_str:
                            await asyncio.sleep(POLL_INTERVAL)
                            continue

                        if active_order_id:
                            try:
                                await asyncio.to_thread(client.futures_cancel_order, symbol=symbol, orderId=active_order_id)
                            except Exception: pass

                        new_order = await asyncio.to_thread(
                            client.futures_create_order,
                            symbol=symbol, side=side, positionSide=position_side,
                            quantity=qty_str, price=target_price_str,
                            type=ORDER_TYPE_LIMIT, timeInForce='GTX',
                            newOrderRespType='RESULT'
                        )

                        if new_order.get('status') == 'FILLED':
                            executed_qty = float(new_order.get('executedQty', 0.0))
                            filled = True; break
                        
                        active_order_id = new_order.get("orderId")
                        active_price = target_price_str

                    except BinanceAPIException as e:
                        if e.code == -5022:
                            active_price = None 
                            continue 
                        if e.code in [-1003, -2027]:
                            logger.error(f"[MAKER_STOP] Critical Error {e.code}")
                            break
                        await asyncio.sleep(RETRY_DELAY)
                    except Exception as e:
                        logger.error(f"[MAKER_LOOP_ERR] {e}")
                        await asyncio.sleep(RETRY_DELAY)

                try: await asyncio.to_thread(client.futures_countdown_cancel_all, symbol=symbol, countdownTime=0)
                except Exception: pass

                if filled or executed_qty >= (adjusted_quantity * 0.9):
                    logger.info(f"[MAKER_SUCCESS] {position_key} filled {executed_qty}")
                    return True
                
                remaining = adjusted_quantity - executed_qty
                if remaining > (adjusted_quantity * 0.1):
                    logger.warning(f"[MAKER_FALLBACK] {position_key} timeout. Sending remaining {remaining} to webhook.")
                    await self.send_webhook_once(position_key, account_key, symbol, remaining, float(lp), side, position_side, unique_id, is_full_close, webhook_url, webhook_secret, reason)
                
                return True

            except Exception as e:
                logger.error(f"[PLACE_MAKER_ORDER] {position_key} Unexpected error: {e}", exc_info=True)
                await self.send_webhook_once(position_key, account_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close, webhook_url, webhook_secret, reason)
                return True
            finally:
                self.orders_executing.discard(position_key)
    async def _handle_filled_maker(self, account_key, position_key, position, positionAmt, quantity, current_price, side, position_side, unique_id, reason):
        position_value = positionAmt * current_price if positionAmt and current_price else "0.0"
        position_value_str = f"{(position_value)}" 
        if position_key in self.orders_executing:
            self.orders_executing.discard(position_key)
        if await self.redis_client.exists(position_key):
            await self.redis_client.delete(position_key)
        if self.is_opposite_direction(side, position_side): 
            reduction_value_str = ( f"{quantity * current_price:.2f}" if current_price else "N/A" )
            log_reduce_action( position_key=position_key,  position_value_str=position_value_str, reduction_value_str=reduction_value_str,  gain=position.gain,  reason= reason )
            logger.info( f"[HANDLE_FILLED] {position_key} status FILLED Reduced total $ {position_value_str}  {position_key} " f"by reduction $ {reduction_value_str} at {current_price}, reason={reason}. 2335", extra={"color": "red"} )
            if quantity == 0:
                quantity = position.max_quantity
            if position_key in self.reenter_level:
                del self.reenter_level[position_key]
            self.reenter_level[position_key] = {"reenter_level": current_price,  "reenter_amount": quantity, "timestamp": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "reason": '_handle_filled_maker reduction. 2366' }
            logger.debug(f"[HANDLE_FILLED] {position_key} Reenter level set for {position_key}: {self.reenter_level[position_key]}. 2367")
            await self.save_reenter_level(position_key)                               
            position.positionAmt -= quantity
            position.last_reduction_amount = quantity
            position.last_reduction_price = current_price
            position.last_reduction_time = now
            position.last_augmentation_amount = 0
            position.last_updated = now
            self.reduced_positions[position_key] = True
            if position_key in self.augmented_positions:
                del self.augmented_positions[position_key]            
            self.reduction_cooldown_map[position_key] = now
            logger.debug(f"[HANDLE_FILLED] {position_key} reduced => new qty{position.positionAmt}. 2324")
        if self.is_same_direction(side, position_side):
            augment_value_str = f"{quantity * current_price:.2f}" if current_price else "N/A"
            log_augment_action(position_key=position_key,  position_value_str=position_value_str,  augment_value_str=augment_value_str,  gain=position.gain,  reason=reason or "Augment"   )
            logger.info( f"[[HANDLE_FILLED] {position_key} status FILLED qty {quantity} * {current_price} by $ {augment_value_str}, reason={reason}. 2349", extra={"color": "green"})
            position.positionAmt += quantity
            position.last_augmentation_amount = quantity
            position.last_augmentation_price = current_price
            position.last_augmentation_time = now
            # NEVER zero reduction fields — historical data must be preserved
            if position_key in self.reduced_positions:
                del self.reduced_positions[position_key]
            self.augmented_positions[position_key] = True
            self.augmentation_cooldown_map[position_key] = now
            if position_key in self.reenter_level:
                del self.reenter_level[position_key]
                await self.save_reenter_level(position_key) 
            logger.debug(f"[HANDLE_FILLED] {position_key} augmented => new qty{position.positionAmt}. 2339")
        await self._save_all_accounts() 
        self.fallback_used.discard(unique_id)
    async def send_webhook(self, account_key, symbol, amount, current_price, side, position_side, unique_id, is_full_close, webhook_url, webhook_secret, reason= None):
        position_key = self.construct_position_key(account_key, symbol, position_side)
        logger.debug(f"[SEND_WEBHOOK] {position_key}, Symbol='{symbol}', Side='{side}', Amount={amount}, current_price={current_price}, Close={is_full_close}. 2687")
        account_positions = self.positions_by_account[account_key]
        position = account_positions.get(position_key)
        positionAmt = position.positionAmt
        last_time = self.webhook_signal_time.get(symbol)
        if last_time:
            delta = (now - last_time).total_seconds()
            if delta < self.webhook_cooldown_period:
                logger.info(f"Webhook cooldown active for {symbol} (last sent {delta:.2f}s ago). Skipping. 2387")
               # return
        if position_key in self.orders_executing:
            self.orders_executing.discard(position_key)
            logger.debug(f"[send_webhook]: order:{reason} ---> removed from orders_executing after executing. 2884")
        augment_sum=0
        reduce_sum=0
        if self.is_same_direction(side,position_side):
            augment_sum = amount * current_price
            # if augment_sum > config.MAX_ORDER_VALUE:
            #     augment_sum = config.MAX_ORDER_VALUE
            # if account_key == 'inf' and position_side=='LONG' and position.positionAmt == 0.0:
            #     augment_sum = 10 * augment_sum
            # if account_key == 'inf' and position_side=='SHORT' and position.positionAmt == 0.0:
            #     augment_sum = 3 * augment_sum    
            # if account_key == 'men' and position_side=='LONG' and position.positionAmt == 0.0:
            #     augment_sum = 3 * augment_sum
            # if account_key == 'men' and position_side=='SHORT' and position.positionAmt == 0.0:
            #     augment_sum = 10 * augment_sum      
            # else:
            #     augment_sum = amount * current_price     
        if self.is_opposite_direction(side,position_side):
            reduce_sum = amount * current_price #positionAmt * current_price - config.MIN_POSITION_SIZE    
        if is_full_close:
            reduce_sum = positionAmt * current_price
            position_side = "FLAT"   
        logger.info(f"{position_key} ${current_price} order amount {amount} value augm {augment_sum} red {reduce_sum}. {reason} . 2406")
        payload = { "name":  f"ez_manage: {reason}", "secret": webhook_secret, "symbol": symbol, "side": side, "open": {  "amountType": "sumUsd", "amount":str(augment_sum)}, "dca": { "amountType": "sumUsd","amount": str(augment_sum)}, "positionSide": position_side,
           # "tp": { "update": False, "orders": [ { "ofs": "-2", "piece": "85"  }, { "ofs": "2.5", "piece": "20.0" }, { "ofs": "5.3", "piece": "20.0" }, { "ofs": "8", "piece": "20.0"  },  { "ofs": "99", "piece": "32.0"  } ]  },
            "close": { "decrease": {  "type": "sumUsd",  "amount": str(reduce_sum) } } }
        logger.debug(f"send_webhook: {position_key} {side} Order sent {payload} .2731")
        async def do_send():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(webhook_url, json=payload, timeout=10) as resp:
                        text = await resp.text()
                        if resp.status == 200:
                            logger.debug(f"Webhook sent successfully for {account_key} {side} {symbol}: Response='{text}. 2398'")
                            #await self._handle_filled_maker(account_key, position_key, position, positionAmt, amount, current_price, side, position_side, unique_id, reason)
                            # position_value_str = f"{positionAmt * current_price:.2f}" if positionAmt and current_price else "0.0"
                            # if self.is_opposite_direction(side, position_side): 
                            #     reduction_value_str = ( f"{amount * current_price:.2f}" if current_price else "N/A" )
                            #     log_reduce_action( position_key=position_key,  position_value_str=position_value_str, reduction_value_str=reduction_value_str,  gain=gain,  reason= reason )
                            #     logger.info( f"[[WEBHOOK] {position_key} webhook CONFIRMED Reduced total $ {position_value_str}  {position_key} " f"by reduction $ {reduction_value_str} at {current_price}, reason={reason}.", extra={"color": "red"} )
                            # if self.is_same_direction(side, position_side):
                            #     augment_value_str = f"{amount * current_price:.2f}" if current_price else "N/A"
                            #     log_augment_action(position_key=position_key,  position_value_str=position_value_str,  augment_value_str=augment_value_str,  gain=position.gain,  reason=reason or "Augment"   )
                            #     logger.info( f"[[WEBHOOK] {position_key} webhook CONFIRMED Augmented total $ {position_value_str}  {position_key} " f"by  $ {augment_value_str} at {current_price}, reason={reason}.", extra={"color": "green"} )
                            #     reduction_value_str = ( f"{amount * current_price:.2f}" if current_price else "N/A" )
                            #     log_reduce_action( position_key=position_key,  position_value_str=position_value_str, reduction_value_str=reduction_value_str,  gain=gain,  reason= reason )
                        else:
                            logger.error(f"Webhook failed for {account_key} {side} {symbol}: Status={resp.status}, Response='{text}'")
                            if await self.redis_client.exists(position_key):
                                await self.redis_client.delete(position_key)
            except asyncio.TimeoutError:
                logger.error(f"Webhook request timed out for {account_key} {side}  {symbol}.")
            except Exception as e:
                if await self.redis_client.exists(position_key):
                    await self.redis_client.delete(position_key)                
                logger.error(f"Error sending webhook for {account_key} {side} {symbol}: {e}", exc_info=True)
        asyncio.create_task(do_send())
        self.webhook_signal_time[symbol] = now
        self.fallback_used.add(unique_id)
    async def send_webhook_once(self, position_key, account_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close, webhook_url, webhook_secret, reason=None):
        logger.debug(f"[SEND_WEBHOOK_ONCE] {position_key} {side} Order received for ${quantity*current_price} on '{account_key}'.")
        if unique_id in self.fallback_used: 
            logger.debug(f"[send_webhook_once] {unique_id}: Already sent fallback, skipping.")
            return
        logger.debug(f"SEND_WEBHOOK_ONCE => Fallback to webhook for {position_key}")
        success = await self.send_webhook(account_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close, webhook_url, webhook_secret, reason)
        if not success:
            return False
        if success:
            return True

# -------------- ORDER QUEUE CLASS --------------

class OrderQueue:
    def __init__(self, trade_manager, max_concurrent_orders=85, max_webhook_orders=100):
        self.trade_manager = trade_manager
        # Separate queues
        self._maker_orders = asyncio.Queue()
        self._webhook_orders = asyncio.Queue()
        # Maker semaphore for concurrency limit
        self.maker_semaphore = asyncio.Semaphore(max_concurrent_orders)
        # Optional webhook semaphore (could remove if fully unlimited)
        self.webhook_semaphore = asyncio.Semaphore(max_webhook_orders)
        self.max_concurrent_orders = max_concurrent_orders
        self.max_webhook_orders = max_webhook_orders
    async def add_order(self, order: dict):
        """Dispatch order to appropriate queue."""
        if order.get("use_webhook", False):
            await self._webhook_orders.put(order)
            logger.debug(f"Webhook Order added to queue: {order}")
        else:
            await self._maker_orders.put(order)
            logger.debug(f"Maker Order added to queue: {order}")
    async def process_orders(self):
        """Start parallel processing of both queues."""
        logger.info(" Starting order queue processing loop...")
        asyncio.create_task(self._process_maker_orders())
        await asyncio.sleep(15)
        asyncio.create_task(self._process_webhook_orders())
    async def _process_maker_orders(self):
        while True:
            logger.info(f" Waiting for next MAKER order... Queue size: {self._maker_orders.qsize()}")
            order = await self._maker_orders.get()
            logger.info(f" Got MAKER order: {order}")
            asyncio.create_task(self._handle_maker_order(order))
    async def _process_webhook_orders(self):
        while True:
            logger.info(f" Waiting for next WEBHOOK order... Queue size: {self._webhook_orders.qsize()}")
            order = await self._webhook_orders.get()
            logger.info(f" Got WEBHOOK order: {order}")
            asyncio.create_task(self._handle_webhook_order(order))
    async def _handle_maker_order(self, order: dict):
        async with self.maker_semaphore:
            await self.handle_order(order)
    async def _handle_webhook_order(self, order: dict):
        # Optional: keep semaphore, or just directly:
        async with self.webhook_semaphore:
            await self.handle_order(order)
    async def handle_order(self, order: dict):
        """Execute the order using the TradeManager."""
        account_key = order['account_key']
        symbol = order['symbol']
        side = order['side']
        quantity = order['quantity']
        current_price = order['current_price']
        if not current_price or current_price <= 0:
            logger.error(f"[handle_order] Invalid current_price={current_price}. Skipping order.")
            return
        position_side = order['position_side']
        is_full_close = order['is_full_close']
        use_webhook = order.get('use_webhook', False)
        unique_id = order['unique_id']  
        reason = 'bulk' 
        position_key = self.trade_manager.construct_position_key(account_key, symbol, position_side)
        if not unique_id:
            unique_id = generate_unique_id(position_key, side, reason)    
        account_positions = self.trade_manager.positions_by_account[account_key]
        position = account_positions.get(position_key)
        logger.debug(f"[HANDLE_ORDER] {position_key} => Side='{side}', Quantity={quantity}, Full_Close={is_full_close}, Webhook={use_webhook}")
        if not position:
            return  
        positionAmt=position.positionAmt
        gain = position.gain 
        entry_price = position.entry_price
        account_config = self.trade_manager.accounts.get(account_key)
        if not account_config:
            logger.error(f"[handle_order] {position_key} No account configuration found for '{account_key}'. Skipping order.")
            return
        logger.debug(f"[HANDLE_ORDER] {position_key} PositionAmt {positionAmt} for {position_side} {symbol} on '{account_key}': {gain:.2f}%")
        webhook_url = account_config.webhook_url
        webhook_secret = account_config.webhook_secret
        symbol_min_qty = self.trade_manager.min_qty.get(symbol, 0.001)
        try:
            if unique_id in self.trade_manager.orders_in_progress:
                logger.debug(f"[HANDLE_ORDER] {position_key} is already being placed. Skipping. 2394")
                return
            if gain < 0.3 and current_price > 0 and self.trade_manager.is_same_direction(side, position_side):
                quantity = config.START_POSITION_SIZE / current_price
                logger.debug(f"[HANDLE_ORDER] {position_key} Reducing order for {symbol} on '{account_key}' due to gain threshold. 2399")
            pos_price = await self.trade_manager.get_current_price(symbol)
            if pos_price and abs(current_price - pos_price) / pos_price > 0.05 and self.trade_manager.is_same_direction(side, position_side):
                logger.warning(f"[HANDLE_ORDER] {position_key} Skipping order: current price {current_price} price difference with mark price: {pos_price} too high! skipping!")
                return
            order_value = quantity * pos_price
            max_order_value=config.MAX_ORDER_VALUE
            if self.trade_manager.is_same_direction(side, position_side):
                if order_value > max_order_value:
                    logger.warning(f"[handle_order] {position_key} Order value {order_value:.2f} exceeds {max_order_value}. Adjusting quantity.")
                    quantity = max_order_value / pos_price if pos_price else quantity
                if order_value < symbol_min_qty * current_price or order_value < 2 * config.MIN_POSITION_SIZE:
                    order_value = max(symbol_min_qty, 2 * config.MIN_POSITION_SIZE)
                    quantity = order_value / current_price
                    logger.warning(f"[HANDLE_ORDER] {position_key}Order value {order_value:.2f} augment exceeds {max_order_value}. Adjusting quantity.")
            if pos_price and self.trade_manager.is_opposite_direction(side, position_side):  
                max_opp_order_value = positionAmt * current_price - config.MIN_POSITION_SIZE
                quantity = min(quantity, max_opp_order_value / current_price)
                logger.debug(f"[HANDLE_ORDER] {position_key} Order value {order_value:.2f} reduce exceeds {max_opp_order_value}. Adjusting quantity to {quantity} order_val ${quantity*current_price}. 2233")
            if (pos_price and (positionAmt * pos_price < 1.2 * config.MIN_POSITION_SIZE) and self.trade_manager.is_opposite_direction(side, position_side)):  
                logger.debug(f"[HANDLE_ORDER] {position_key} Order value {order_value:.2f} size too small. PositionAmt: {positionAmt}, pos.price:{pos_price} current_price: {current_price}. 2236")
                return
            if (pos_price and quantity < 1.2 * (max(config.MIN_POSITION_SIZE / pos_price), symbol_min_qty) and self.trade_manager.is_same_direction(side, position_side)): 
                quantity =  max(config.START_POSITION_SIZE / pos_price, symbol_min_qty)  
            if positionAmt * current_price > 2 * config.START_POSITION_SIZE and gain < 0.6 and self.trade_manager.is_same_direction(side,position_side):
                logger.debug(f"[handle_order] {position_key} augmenting too fast.")
                quantity = config.START_POSITION_SIZE / current_price
            reason = f"{position_key} {side} ${current_price*quantity}={current_price}*{quantity} {reason}"
            logger.debug(f"Proceeding with maker order for {position_key}'")
            success = await self.trade_manager.safe_place_order( account_key=account_key, position_key=position_key, symbol=symbol, quantity=quantity, current_price=current_price, side=side, position_side=position_side, unique_id=unique_id, use_webhook=use_webhook, is_full_close=is_full_close, webhook_url=webhook_url, webhook_secret=webhook_secret, reason=reason )
            if success:
                logger.debug(f"[handle_order] {position_key} value${current_price*positionAmt} {side} order for ${current_price*quantity}: Order successfully placed for {symbol} on '{account_key}'.")
            else: 
                await self.trade_manager.send_webhook_once( position_key, account_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close, webhook_url, webhook_secret )
                logger.debug(f"[handle_order] {position_key} value${current_price*positionAmt} {side} order for ${current_price*quantity} Order failed'. Webhook sent. 2287")
        except Exception as e:
            logger.error(f"[handle_order] {position_key} value${current_price*positionAmt} {side} order for ${current_price*quantity} Unexpected error placing order: {e}. 2507", exc_info=True)

# -------------- MANAGE POSITIONS (Bulk) --------------

# -------------- MANAGE POSITIONS (Bulk) --------------

async def manage_positions_bulk(order_queue: OrderQueue, trade_manager: MultiAccountTradeManager):
    """
    Prompts the user to select accounts, filter criteria, and bulk actions, then enqueues orders concurrently.
    """
    logger.debug("Entered manage_positions_bulk function.")
    # 1) Choose account(s)
    choices = ["All Accounts"] + config.ACCOUNT_KEYS
    choice = await asyncio.to_thread( lambda: questionary.select( "Which account(s) do you want to manage?", choices=choices ).ask() )
    if not choice:
        logger.info("No account selected.")
        return
    selected_accounts = config.ACCOUNT_KEYS if choice == "All Accounts" else [choice]
    logger.debug(f"Selected accounts: {selected_accounts}")
    # 2) Choose filter
    filter_options = [ "All Positions", "All Long Positions", "All Short Positions", "All Positions in Gain", "All Positions in Loss" ]
    filter_choice = await asyncio.to_thread( lambda: questionary.select( "Select filter criteria for positions:", choices=filter_options ).ask() )
    if not filter_choice:
        logger.info("No filter selected.")
        return
    logger.debug(f"Selected filter: {filter_choice}")
    # 3) Choose action
    action_choices = [ "Half Selected Positions", "Double Selected Positions", "Close Selected Positions", "Cancel" ]
    action_choice = await asyncio.to_thread( lambda: questionary.select( "Select a bulk action to perform on the chosen positions:", choices=action_choices ).ask() )
    if not action_choice or action_choice == "Cancel":
        logger.info("No action selected.")
        return
    logger.debug(f"Selected action: {action_choice}")
    # Determine multiplier and side map
    multiplier, side_map = { "Half Selected Positions": (0.5, {"LONG": "SELL", "SHORT": "BUY"}), "Double Selected Positions": (2.0, {"LONG": "BUY", "SHORT": "SELL"}), "Close Selected Positions": ("close", {"LONG": "SELL", "SHORT": "BUY"}) }[action_choice]
    # 4) Confirm action
    confirm = await asyncio.to_thread( lambda: questionary.confirm( f"Are you sure you want to '{action_choice}' for the chosen filter '{filter_choice}' in {len(selected_accounts)} account(s)?" ).ask() )
    if not confirm:
        logger.info("User canceled the bulk action.")
        return
    logger.debug("User confirmed bulk action.")
    # 5) Ask for webhook usage
    use_webhook = False
    webhook_choice = await asyncio.to_thread( lambda: questionary.confirm( "Do you want to place orders via webhook for all selected accounts?" ).ask() )
    use_webhook = webhook_choice
    logger.debug(f"Use webhook: {use_webhook}")
    total_orders = 0
    global_tasks = []
    for acct in selected_accounts:
        acct_positions = trade_manager.positions_by_account.get(acct, {})
        if not acct_positions:
            logger.info(f"No positions for account '{acct}'.")
            continue
        # Filter positions based on choice
        selected_dict = { "All Positions": acct_positions, "All Long Positions": {s: p for s, p in acct_positions.items() if p.position_side == "LONG"}, "All Short Positions": {s: p for s, p in acct_positions.items() if p.position_side == "SHORT"}, "All Positions in Gain": {s: p for s, p in acct_positions.items() if p.gain > 0}, "All Positions in Loss": {s: p for s, p in acct_positions.items() if p.gain < 0} }.get(filter_choice, {})
        if not selected_dict:
            logger.info(f"No positions match the filter '{filter_choice}' in account '{acct}'.")
            continue
        logger.info(f"Eligible positions for {acct} ({filter_choice}):")
        for pk, pos in selected_dict.items():
            logger.info(f"  - {pk}: amt={pos.positionAmt}, gain={pos.gain}, side={pos.position_side}")
        for position_key, pos_obj in selected_dict.items():
            symbol = pos_obj.symbol
            side = pos_obj.position_side
            positionAmt = pos_obj.positionAmt
            current_price = await trade_manager.get_current_price(symbol)
            symbol_min_qty = trade_manager.min_qty.get(symbol, 0.001)
            # Determine action-specific settings
            if multiplier == "close":
                new_side = side_map[side]
                quantity = positionAmt
                is_full_close = True
            else:
                new_side = side_map[side]
                quantity = positionAmt * multiplier
                is_full_close = False
            # Skip tiny positions for halve
            if positionAmt < 1.5 * symbol_min_qty and "Half" in action_choice:
                logger.info(f"Skipping {position_key}: too small to halve (amt={positionAmt})")
                continue
            if (positionAmt < 1.2 * symbol_min_qty or positionAmt < 2 * config.MIN_POSITION_SIZE) and (positionAmt > 0 or config.INCLUDE_0) and "Double" in action_choice:
                logger.info(f"Using start size for {position_key} due to small amt (amt={positionAmt})")
                quantity = config.START_POSITION_SIZE / current_price if current_price else 0
            if not is_symbol_allowed(acct, symbol, trade_manager) and selected_accounts in ['ang','inf','men']:
                logger.info(f"Skipping {position_key}: symbol not allowed.")
                continue
            unique_id = generate_unique_id(position_key, side, 'manage bulk')
            position_key = trade_manager.construct_position_key(acct, symbol, side)
            order = { "account_key": acct, "symbol": symbol, "side": new_side, "quantity": quantity, "current_price": current_price, "position_side": side, "unique_id": unique_id, "is_full_close": is_full_close, "use_webhook": use_webhook, }
            global_tasks.append(asyncio.create_task(order_queue.add_order(order)))
            logger.info(f"Enqueued order => Account: {acct}, Symbol: {symbol}, Side: {new_side}, Qty: {quantity}, Close: {is_full_close}, Webhook: {use_webhook}")
            total_orders += 1
    # Execute all tasks concurrently!
    if global_tasks:
        await asyncio.gather(*global_tasks)
    logger.info(f"Total of {total_orders} orders enqueued across {len(selected_accounts)} account(s).")

async def automatic_individual_manager(order_queue: OrderQueue, trade_manager: MultiAccountTradeManager):
    """
    Periodically checks each open position individually.
      - If a position's gain% > 0.8, augment it by +20% *once*, and mark it 'augmented'.
      - If a position's gain% < 0.5, reduce it by 90% ONLY IF 'augmented' is True.
    """
    logger.debug(">>> automatic_individual_manager started!")
    UPPER_GAIN_THRESHOLD = 0.8     # e.g. +0.8% => augment
    LOWER_GAIN_THRESHOLD = 0.2     # e.g. +0.5% => reduce
    AUGMENT_FACTOR = 0.3           # +20% position
    REDUCTION_FACTOR = 0.9         # -90% position
    MIN_NOTIONAL = 25.0            # keep at least $20 if you wish
    COOLDOWN_SECONDS = 120
    CHECK_INTERVAL = 70
    augmented_positions = {}
    reduced_positions = {}
    cooldown_map = {}
    now = datetime.now(timezone.utc)
    while True:
        logger.debug("Entering automatic_individual_manager while-loop iteration.")
        for account_key in trade_manager.config.ACCOUNT_KEYS:
            trade_manager.positions_dict = { k: v for k, v in trade_manager.positions.items() if k.startswith(f"{account_key}:") and v.positionAmt > 0}
            positions_list = [(symbol, pos_info) for symbol, pos_info in trade_manager.positions_dict.items()  if pos_info.get("position_key", "").startswith(f"{account_key}:")]
            BATCH_SIZE = 50
            for i in range(0, len(positions_list), BATCH_SIZE):
                batch = positions_list[i : i + BATCH_SIZE]
                for symbol, pos_info in batch:
                    position_side = pos_info["position_side"]  # "LONG" or "SHORT"
                    gain = pos_info["gain"]  
                    prev_gain = pos_info["prev_gain"]          # gain in %
                    positionAmt  = pos_info["positionAmt"]
                    current_price = pos_info["mark_price"]
                    last_reduction_amount = pos_info.get("last_reduction_amount", 0.0)
                    last_reduction_price = pos_info.get("last_reduction_price", current_price)
                    last_augmentation_amount = pos_info.get("last_augmentation_amount", 0.0)
                    last_augmentation_price = pos_info.get("last_augmentation_price", current_price)
                    position_key =  trade_manager.construct_position_key(account_key,symbol,position_side)
                    if position_key not in augmented_positions:
                        augmented_positions[position_key] = False 
                    if position_key not in reduced_positions:
                        reduced_positions[position_key] = False                         
                    logger.debug(f"[IndividualManager] Checking {account_key} {position_side} {symbol} => " f" price={pos_info['mark_price']}, positionAmt={pos_info['positionAmt']:.4f}, gain={pos_info['gain']:.2f}%")
                    if not current_price or positionAmt <= 0:
                        continue
                    pos_key = trade_manager.construct_position_key(account_key, symbol, position_side)
                    now = datetime.now(timezone.utc)
                    last_action_time = cooldown_map.get(pos_key, None)
                    if last_action_time and (now - last_action_time).total_seconds() < COOLDOWN_SECONDS:
                        # still on cooldown for this position
                        continue
                    indicators = trade_manager.indicators_data.get(symbol)
                    if not isinstance(indicators, dict):
                        logger.debug(f"Indicators data for {symbol} is missing or invalid. Attempting to reload...")
                        await trade_manager.load_indicators_data()
                    symbol_data = trade_manager.crosses_data.get(symbol, {})
                    stoch_crossover_1m = stoch_crossunder_1m = stoch_crossover_15m = stoch_crossunder_15m = False; crossover_price_1m = crossunder_price_1m = crossover_price_15m = crossunder_price_15m = previous_crossover_price_1m = previous_crossunder_price_1m = previous_crossover_price_15m = previous_crossunder_price_15m = cross_data_1m = cross_data_15m = None  
                    if symbol_data:
                        try:
                            cross_data_1m = symbol_data.get("1m", {})
                            cross_data_15m = symbol_data.get("15m", {})
                            if cross_data_1m:
                                latest_stoch_crossover_1m = cross_data_1m.get("stoch_crossover", {}).get("latest", {})                        
                                stoch_crossover_1m = trade_manager.is_valid_stoch_event(latest_stoch_crossover_1m, max_valid_minutes=2)
                                crossover_price_1m = latest_stoch_crossover_1m.get("price")
                                previous_crossover_price_1m = cross_data_1m.get("stoch_crossover", {}).get("previous", {}).get("price")
                                latest_stoch_crossunder_1m = cross_data_1m.get("stoch_crossunder", {}).get("latest", {})
                                stoch_crossunder_1m = trade_manager.is_valid_stoch_event(latest_stoch_crossunder_1m, max_valid_minutes=2)            
                                crossunder_price_1m = cross_data_1m.get("stoch_crossunder", {}).get("latest", {}).get("price")
                                previous_crossunder_price_1m = cross_data_1m.get("stoch_crossunder", {}).get("previous", {}).get("price")                        
                            if cross_data_15m:
                                latest_stoch_crossover_15m = cross_data_15m.get("stoch_crossover", {}).get("latest", {})
                                stoch_crossover_15m = trade_manager.is_valid_stoch_event(latest_stoch_crossover_15m, max_valid_minutes=5)
                                crossover_price_15m = cross_data_15m.get("stoch_crossover", {}).get("latest", {}).get("price")
                                previous_crossover_price_15m = cross_data_15m.get("stoch_crossover", {}).get("previous", {}).get("price")
                                latest_stoch_crossunder_15m = cross_data_15m.get("stoch_crossunder", {}).get("latest", {})            
                                stoch_crossunder_15m = trade_manager.is_valid_stoch_event(latest_stoch_crossunder_15m, max_valid_minutes=5)
                                crossunder_price_15m = cross_data_15m.get("stoch_crossunder", {}).get("latest", {}).get("price")
                                previous_crossunder_price_15m = cross_data_15m.get("stoch_crossunder", {}).get("previous", {}).get("price")
                        except AttributeError as e:
                            logger.debug(f"Error reading cross data for {symbol}: {e}")
                    lr_trend_4h =indicators.get('lr_trend_4h')
                    lr_trend_1h =indicators.get('lr_trend_1h')
                    dc_high_1m =indicators.get('dc_high_1m')
                    dc_low_1m =indicators.get('dc_low_1m')
                    dc_basis_1m =indicators.get('dc_basis_1m')
                    dc_high_15m =indicators.get('dc_high_15m')
                    dc_low_15m =indicators.get('dc_low_15m')
                    dc_basis_15m =indicators.get('dc_basis_15m')
                    dc_high_1h =indicators.get('dc_high_1h')
                    dc_low_1h =indicators.get('dc_low_1h')
                    dc_basis_1h =indicators.get('dc_basis_1h')
                    dc_high_4h =indicators.get('dc_high_4h')
                    dc_low_4h =indicators.get('dc_low_4h')
                    dc_basis_4h =indicators.get('dc_basis_4h')
                    dc_high_D =indicators.get('dc_high_D')
                    dc_low_D =indicators.get('dc_low_D')
                    sma_200_1m_prev=indicators.get('sma_200_1m_prev')
                    sma_200_1m =indicators.get('sma_200_1m')
                    sma_200_15m =indicators.get('sma_200_15m')
                    k_1m =indicators.get('stoch_k_1m')  # Same as stoch_k_1m
                    d_1m =indicators.get('stoch_d_1m')  # Same as stoch_d_1m
                    k_prev_1m =indicators.get('stoch_k_prev_1m')  
                    d_prev_1m =indicators.get('stoch_d_prev_1m')  
                    k_15m =indicators.get('stoch_k_15m')
                    d_15m =indicators.get('stoch_d_15m')
                    k_1h =indicators.get('stoch_k_1h')
                    d_1h =indicators.get('stoch_d_1h')
                    k_4h =indicators.get('stoch_k_4h')
                    d_4h =indicators.get('stoch_d_4h')        
                    dc_basis_crossover_1m = indicators.get('dc_basis_crossover_1m')
                    dc_basis_crossunder_1m = indicators.get('dc_basis_crossunder_1m')
                    dc_basis_crossover_15m = indicators.get('dc_basis_crossover_15m')
                    dc_basis_crossunder_15m = indicators.get('dc_basis_crossunder_15m')
                    sma_crossover_1m = indicators.get('sma_crossover_1m')
                    sma_crossunder_1m = indicators.get('sma_crossunder_1m')     
                    sma_crossover_15m = indicators.get('sma_crossover_15m')
                    sma_crossunder_15m = indicators.get('sma_crossunder_15m')   
                    atr_1m = indicators.get('atr_1m')     
                    atr_15m = indicators.get('atr_15m')           
                    prev_price = indicators.get('prev_price')     
                    logger.debug( f"{symbol} | cp{current_price}:pp{prev_price:}-gain:{gain}-Prev{prev_gain} 15m Crossover Price: {crossover_price_15m}, " f"Previous Crossover: {previous_crossover_price_15m}, "  f"Crossunder Price: {crossunder_price_15m}, "  f"Previous Crossunder: {previous_crossunder_price_15m}, co_1{stoch_crossover_1m}, cu_1{stoch_crossunder_1m}, co_15{stoch_crossover_15m}, cu_15{stoch_crossunder_15m}, "  )
                    side = "BUY" if position_side == "LONG" else "SELL"
                    unique_id = generate_unique_id(position_key, side, 'aut ind manager')
                    # 1) Check if we want to AUGMENT (gain > 0.8%)
                    if (gain > UPPER_GAIN_THRESHOLD  or ((gain > 0.5 * UPPER_GAIN_THRESHOLD or gain==0.0) and (position_side=='LONG' and lr_trend_1h=='uptrend' and k_15m < 30 and k_15m > d_15m and k_1m > d_1m and current_price > sma_200_1m and current_price > dc_basis_15m) or (position_side=='SHORT' and lr_trend_1h=='downtrend' and k_15m > 70 and k_15m < d_15m and k_1m < d_1m and current_price < sma_200_1m and current_price < dc_basis_15m)) or ((position_side=='LONG' and current_price > last_reduction_price + atr_1m) and k_1m > d_1m and (current_price >= dc_high_1m or k_15m < 30 )) or ((position_side=='SHORT' and current_price < last_reduction_price - atr_1m) and k_1m < d_1m and (current_price <= dc_low_1m or k_15m > 70 )) ):
                        add_qty = max(positionAmt * AUGMENT_FACTOR, 60/current_price if current_price > 0 else 0, last_reduction_amount)
                        if last_reduction_amount > 0:
                            logger.info(f"[AUTO] Augmenting {symbol} by the previously reduced amount {last_reduction_amount:.4f}.")
                        logger.info(f"[AUTO] {symbol} in {account_key} has gain {gain:.2f}%, " f"augmenting by +{AUGMENT_FACTOR*100:.1f}%.")
                        augment_qty=max(add_qty,last_reduction_amount)
                        order = { "account_key": account_key, "symbol": symbol, "side": side, "quantity": augment_qty, "current_price": current_price, "position_side": position_side, "unique_id":unique_id, "is_full_close": False, "use_webhook": False }
                        await order_queue.add_order(order)
                        position_value_str = f"{positionAmt * current_price:.2f}" if positionAmt and current_price else "0.0"
                        augment_value_str = f"{augment_qty * current_price:.2f}" if current_price else "N/A"
                        log_augment_action(position_key=position_key, position_value_str=position_value_str,  augment_value_str=augment_value_str, gain=gain, reason='automatic_individual_manager augm')                
                        logger.info(f"[augment_position] Augmented {position_value_str} {position_key} by {augment_value_str} at {current_price}, reason='automatic_individual_manager augm'.", extra= {'color': 'green'} )
                        pos_info["last_augmentation_amount"] = augment_qty
                        pos_info["last_augmentation_price"] = current_price
                        pos_info["last_augmentation_time"] = now
                        augmented_positions[pos_key] = True
                        cooldown_map[pos_key] = now
                    if ((gain > 0.5 or pos_key in reduced_positions) and (position_side=='LONG' and k_15m > d_15m and k_1m > d_1m and current_price > sma_200_1m and (current_price > dc_basis_15m or current_price >= dc_high_1m )) or (position_side=='SHORT' and k_15m < d_15m and k_1m < d_1m and current_price < sma_200_1m and (current_price < dc_basis_15m or current_price <= dc_low_1m)) or ((position_side=='LONG' and current_price > last_reduction_price + atr_1m) and k_1m > d_1m and (current_price >= dc_high_1m or k_15m < 30 )) or ((position_side=='SHORT' and current_price < last_reduction_price - atr_1m) and k_1m < d_1m and (current_price <= dc_low_1m or k_15m > 70 )) ):
                        add_qty = 0.1 * positionAmt
                        side = "BUY" if position_side == "LONG" else "SELL"
                        if last_reduction_amount > 0:
                            logger.info(f"[AUTO] Augmenting {symbol} by the previously reduced amount {last_reduction_amount:.4f}.")
                        logger.info(f"[AUTO] {symbol} in {account_key} has gain {gain:.2f}%, " f"augmenting by +{AUGMENT_FACTOR*100:.1f}%.")
                        augment_qty=max(add_qty,last_reduction_amount)
                        order = { "account_key": account_key, "symbol": symbol, "side": side, "quantity": augment_qty, "current_price": current_price, "position_side": position_side, "unique_id":unique_id, "is_full_close": False, "use_webhook": False }
                        await order_queue.add_order(order)
                        position_value_str = f"{positionAmt * current_price:.2f}" if positionAmt and current_price else "0.0"
                        augment_value_str = f"{augment_qty * current_price:.2f}" if current_price else "N/A"
                        log_augment_action(position_key=position_key, position_value_str=position_value_str,  augment_value_str=augment_value_str, gain=gain, reason='automatic_individual_manager augm')                
                        logger.info(f"[augment_position] Augmented {position_value_str} {position_key} by {augment_value_str} at {current_price}, reason='automatic_individual_manager augm'.", extra= {'color': 'green'} )
                        pos_info["last_augmentation_amount"] = augment_qty
                        pos_info["last_augmentation_price"] = current_price
                        pos_info["last_augmentation_time"] = now
                        augmented_positions[pos_key] = True
                        cooldown_map[pos_key] = now
                    # 2) Check if we want to REDUCE (gain < -1%)
                    elif current_price * positionAmt > 30 and gain < -0.1 :
                        keep_amount = MIN_NOTIONAL / current_price
                        reduce_qty = positionAmt - keep_amount
                        if reduce_qty <= 0:
                            continue
                        side = "SELL" if position_side == "LONG" else "BUY"
                        logger.info(f"[AUTO] {symbol} in {account_key} has gain {gain:.2f}%, " f"reducing by ~{REDUCTION_FACTOR*100:.1f}% => reduce_qty={reduce_qty:.4f}.")
                        order = { "account_key": account_key, "symbol": symbol, "side": side, "quantity": reduce_qty, "current_price": current_price, "position_side": position_side, "unique_id":unique_id, "is_full_close": False,  "use_webhook": False }
                        await order_queue.add_order(order)
                        pos_info["last_reduction_amount"] = reduce_qty
                        pos_info["last_reduction_price"] = current_price
                        pos_info["last_reduction_time"] = now
                        position_value_str = f"{positionAmt * current_price:.2f}" if positionAmt and current_price else "0.0"
                        reduction_value_str = f"{reduce_qty * current_price:.2f}" if current_price else "N/A"
                        log_reduce_action(position_key=position_key, position_value_str=position_value_str,  reduction_value_str=reduction_value_str, gain=gain, reason='automatic_individual_manager red1')  
                        logger.info(f"[reduce_position] Reduced {position_value_str} {position_key} by {reduction_value_str} at {current_price}, reason='automatic_individual_manager red1'.", extra= {'color': 'red'} )
                        cooldown_map[pos_key] = now
                        reduced_positions[position_key] = True
                        augmented_positions[pos_key] = False
                    # 3) Check if we want to REDUCE (gain < 0.5%)
                    elif (current_price * positionAmt > 30 and pos_key in augmented_positions and (gain < LOWER_GAIN_THRESHOLD or  (position_side=='LONG' and (k_15m < d_15m or  current_price < sma_200_1m or current_price < dc_basis_15m or sma_crossunder_1m or dc_basis_crossunder_1m or dc_basis_crossunder_15m or stoch_crossunder_15m or (stoch_crossunder_1m and k_15m > 90))) or (position_side=='SHORT' and (k_15m > d_15m or  current_price > sma_200_1m or current_price > dc_basis_15m or sma_crossover_1m or dc_basis_crossover_1m or dc_basis_crossover_15m or stoch_crossover_15m or (stoch_crossover_1m and k_15m < 10))))) :
                        notional = positionAmt * current_price
                        keep_amount = MIN_NOTIONAL / current_price
                        reduce_qty = positionAmt - keep_amount
                        if reduce_qty <= 0:
                            continue
                        side = "SELL" if position_side == "LONG" else "BUY"
                        logger.info(f"[AUTO] {symbol} in {account_key} has gain {gain:.2f}%, " f"reducing by ~{REDUCTION_FACTOR*100:.1f}% => reduce_qty={reduce_qty:.4f}.")
                        order = { "account_key": account_key, "symbol": symbol, "side": side, "quantity": reduce_qty, "current_price": current_price, "position_side": position_side, "unique_id":unique_id, "is_full_close": False, "use_webhook": False }
                        await order_queue.add_order(order)
                        pos_info["last_reduction_amount"] = reduce_qty
                        pos_info["last_reduction_price"] = current_price
                        pos_info["last_reduction_time"] = now
                        position_value_str = f"{positionAmt * current_price:.2f}" if positionAmt and current_price else "0.0"
                        reduction_value_str = f"{reduce_qty * current_price:.2f}" if current_price else "N/A"
                        log_reduce_action(position_key=position_key, position_value_str=position_value_str,  reduction_value_str=reduction_value_str, gain=gain, reason='automatic_individual_manager red2')  
                        logger.info(f"[reduce_position] Reduced {position_value_str} {position_key} by {reduction_value_str} at {current_price}, reason='automatic_individual_manager red2'.", extra= {'color': 'red'} )
                        cooldown_map[pos_key] = now
                        reduced_positions[position_key] = True
                        augmented_positions[pos_key] = False
            await asyncio.sleep(CHECK_INTERVAL)

def generate_unique_id(position_key, side, reason=""):
    unique_id = uuid.uuid4().hex[:8]  # short random
    return f"{position_key}:{side}:{reason}:{unique_id}"

# -------------- USER INTERFACE LOOP --------------

async def user_interface_loop(order_queue: OrderQueue, trade_manager: MultiAccountTradeManager):
    while True:
        action = await asyncio.to_thread( lambda: questionary.select( "Main Menu - Choose an action:", choices=[ "Manage Positions", "View Queue Size", "Exit" ] ).ask() )
        if action == "Manage Positions":
            await manage_positions_bulk(order_queue, trade_manager)
        elif action == "View Queue Size":
            queue_size = order_queue._maker_orders.qsize()
            logger.info(f"Current queue size: {queue_size} orders pending.")
            print(f"Current queue size: {queue_size} orders pending.")
        elif action == "Exit":
            logger.info("User chose to exit.")
            await shutdown(order_queue, trade_manager)
            return 
        else:
            logger.warning(f"Unknown action selected: {action}")
        await asyncio.sleep(0.5)

def is_symbol_allowed(account_key, symbol, trade_manager):
    allowed = { 'ang': trade_manager.symbols_ang, 'inf': trade_manager.symbols_inf, 'men': trade_manager.symbols_men, 'flz': trade_manager.symbols_flz }
    if account_key in allowed:
        return symbol in allowed[account_key]
    return False

async def load_positions_scheduler(trade_manager=MultiAccountTradeManager):
    while True:
        try:
            await trade_manager.load_all_positions()
        except Exception as e:
            logger.error(f"Error in load_positions_scheduler: {e}")
            logger.debug(traceback.format_exc())
        await asyncio.sleep(15) 

# -------------- SHUTDOWN FUNCTION --------------

async def shutdown(order_queue: OrderQueue, trade_manager: MultiAccountTradeManager):
    logger.info("Shutting down, waiting for queue to empty...")
    await order_queue._maker_orders.join()
    logger.info("All orders processed. Closing Binance clients.")
    await asyncio.gather(*(account.close() for account in trade_manager.accounts.values()))
    logger.info("All Binance clients closed. Exiting.")

def load_accounts(config: Config) -> Dict[str, AccountConfig]:
    """Load account configurations from environment variables."""
    account_keys = config.ACCOUNT_KEYS
    accounts = {}
    for key in account_keys:
        try:
            accounts[key] = AccountConfig(prefix=key)
            logger.info(f"Loaded account '{key}'")
        except ValueError as e:
            logger.error(e)
    return accounts

# -------------- MAIN FUNCTION --------------

async def main():
    config = Config()
    #logger.debug(f"Configuration loaded: {asdict(config)}")
    accounts = load_accounts(config)
    if not accounts:
        logger.error("No accounts loaded. Exiting.")
        return
    try:
        logger.info("Initializing accounts...")
        await asyncio.gather(*(account.initialize() for account in accounts.values()))
        logger.info("All accounts initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize accounts: {e}")
        return
    # 2) Load symbols from disk
    symbols = []
    filepath = config.SYMBOLS_FILE
    try:
        async with aiofiles.open(filepath, 'r') as f:
            content = await f.read()
            if not content.strip():
                raise ValueError(f"{filepath} is empty.")
            symbols = json.loads(content)
        if not isinstance(symbols, list):
            raise ValueError(f"Symbols file does not contain a valid list: {filepath}")
        logger.info(f"Loaded {len(symbols)} symbols from {filepath}")
    except Exception as e:
        logger.error(f"Failed to load symbols from {filepath}: {e}")
        return
    if not symbols:
        logger.error("No symbols available to proceed. Exiting.")
        return
    # 3) Create the MultiAccountTradeManager
    trade_manager = MultiAccountTradeManager(accounts, symbols)  
    await trade_manager.load_indicators_data()
    await trade_manager.initialize_clients()
    await trade_manager.update_backup_median_prices()
    #await trade_manager.initialize_caches()
    await trade_manager.load_symbols_active()
    await trade_manager.load_symbols_files()    
    #await trade_manager.init_redis()
    await trade_manager.init_async()
    asyncio.create_task(load_positions_scheduler(trade_manager))
    asyncio.create_task(trade_manager.start_symbols_loader())
    order_queue = OrderQueue( max_concurrent_orders=config["MAX_CONCURRENT_ORDERS"], max_webhook_orders=config["MAX_WEBHOOK_ORDERS"], trade_manager=trade_manager )
    asyncio.create_task(order_queue.process_orders())
    asyncio.create_task(user_interface_loop(order_queue, trade_manager))
    asyncio.create_task(monitor_memory())
    # for account_key in config.ACCOUNT_KEYS:
    #     await trade_manager.fetch_positions(account_key)
    #     logger.info(f'fetchfetch : {trade_manager.positions_by_account}. 6616')
    # 6) Periodic tasks loop
    async def periodic_tasks():
        while True:
            # This runs every 60s. Potentially you can do 120s or 300s if you're dealing with big data
            await asyncio.sleep(120)
            # Only do these big fetch calls once in the periodic tasks, not from the manager
            await trade_manager.initialize_caches()
            await trade_manager.update_backup_median_prices()
            #await trade_manager.load_indicators_data()
            #await trade_manager.load_crosses()
            logger.debug("Periodic refresh completed.")
    # Start the periodic tasks
    asyncio.create_task(periodic_tasks())
    # 7) Keep script alive
    await asyncio.Event().wait()

# -------------- SCRIPT ENTRY POINT --------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application interrupted by user.")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

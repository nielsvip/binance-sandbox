# pylint: disable=W,C,R,I
import asyncio
import json
import logging
import logging.handlers
import os
import random
import resource
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import aiofiles
import aiofiles.os as aio_os
import aiohttp
import numpy as np
import pandas as pd
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
from dateutil.parser import isoparse
from requests.adapters import HTTPAdapter
from config import Config
from ez_manage import (MultiAccountTradeManager, OrderQueue, _last_events_cache_time, generate_unique_id, load_accounts, minutes_since, verify_trade_via_websocket)
from ez_positions_service import bootstrap_position_service
from ez_share_ind import get_shared_memory_client
from utils import (SimpleRedisManager, construct_position_key, force_usdc_in_list, get_current_environment, get_current_price, is_hedge_account, load_environment_from_gpg, parse_position_key, safe_datetime, safe_fetch_float)
try:
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 10000), hard))
except Exception: pass
try:
    from typing import Any, Dict, List, Optional, Union
    import orjson
    def default_json_serializer(obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        raise TypeError
    def json_dumps(obj: Any, **kwargs) -> bytes:
        option = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS # type: ignore # pylint: disable=no-member,c-extension-no-member
        return orjson.dumps(obj, default=default_json_serializer, option=option) # type: ignore # pylint: disable=no-member,c-extension-no-member
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if isinstance(s, str):
            s = s.encode('utf-8')
        return orjson.loads(s) # type: ignore # pylint: disable=no-member,c-extension-no-member
    JSONDecodeError = orjson.JSONDecodeError # type: ignore # pylint: disable=no-member,c-extension-no-member
except ImportError:
    import json
    from typing import Any, Dict, List, Optional, Union
    def default_json_serializer(obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        return str(obj)
    def json_dumps(obj: Any, **kwargs) -> str:
        if 'indent' not in kwargs:
            kwargs['indent'] = 2
        return json.dumps(obj, default=default_json_serializer, **kwargs)
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if isinstance(s, (bytes, bytearray, memoryview)):
            s = s.decode('utf-8')
        return json.loads(s, **kwargs)
    JSONDecodeError = json.JSONDecodeError
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass
config = Config()
base_path = config.BASE_PATH
current_env = get_current_environment()
current_account = ContextVar("current_account", default="unknown")
load_environment_from_gpg(None)
shared_indicators_proxy = None
service_logger = logging.getLogger("ez_positions_quick")
service_logger.setLevel(logging.INFO) # Corrected to uppercase INFO
service_logger.propagate = False
logger = logging.getLogger("ez_positions_quick")
if logger.hasHandlers():
    logger.handlers.clear()
logs_dir = Path.home() / "logs"
paper_logs_dir = base_path/'logs'
_last_events_cache=60
logs_dir.mkdir(parents=True, exist_ok=True)
current_log_suffix = "main"
if "--account" in sys.argv:
    try:
        idx = sys.argv.index("--account") + 1
        if idx < len(sys.argv):
            current_log_suffix = sys.argv[idx]
    except ValueError:
        pass
log_filename = str(logs_dir / f"ez_positions_quick_general_{current_log_suffix}.log")
file_handler = logging.handlers.RotatingFileHandler( log_filename, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8", mode="a", delay=True,)
file_formatter = logging.Formatter("[%(asctime)s] %(message)s")
datefmt='%d %H:%M:%S'
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(file_formatter)
account_loggers = {}
_last_events_cache = {}
DATE_FORMAT = '%m-%d %H:%M:%S'
LOG_FORMAT = '[%(asctime)s] %(message)s'

def setup_service_logger():
    """Sets up the general service logger for ez_positions_quick"""
    logger = logging.getLogger("ez_positions_quick")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.hasHandlers():
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
    # Resolve log path
    current_log_suffix = "main"
    if "--account" in sys.argv:
        try:
            idx = sys.argv.index("--account") + 1
            if idx < len(sys.argv):
                current_log_suffix = sys.argv[idx]
        except (ValueError, IndexError): pass
    log_filename = logs_dir / f"ez_positions_quick_general_{current_log_suffix}.log"
    # Rotating Handler with delay=True to allow rotation to happen
    file_handler = logging.handlers.RotatingFileHandler( str(log_filename), maxBytes=20*1024*1024, backupCount=5, encoding="utf-8", mode="a", delay=True )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(file_handler)
    # Console output
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(stream_handler)
    return logger
logger = setup_service_logger()

def get_account_logger(account_key: str) -> logging.Logger:
    """Get or create per-account logger. Strictly writes to FILE only."""
    if account_key not in account_loggers:
        acc_logger = logging.getLogger(f"ez_positions_quick_{account_key}")
        acc_logger.propagate = False
        acc_logger.setLevel(logging.INFO)
        if acc_logger.hasHandlers():
            for h in list(acc_logger.handlers):
                h.close()
                acc_logger.removeHandler(h)
        acc_log_filename = str(logs_dir / f"ez_positions_quick_{account_key}.log")
        # Fixed Date Format and Delay=True
        acc_file_handler = logging.handlers.RotatingFileHandler( acc_log_filename, maxBytes=20*1024*1024, backupCount=5, encoding='utf-8', mode='a', delay=True )
        acc_file_handler.setFormatter(logging.Formatter(f"[%(asctime)s] [{account_key}] %(message)s", datefmt=DATE_FORMAT))
        acc_logger.addHandler(acc_file_handler)
        account_loggers[account_key] = acc_logger
    return account_loggers[account_key]
wait_loggers = {}

def get_wait_logger(account_key: str) -> logging.Logger:
    """Get or create per-account wait logger."""
    if account_key not in wait_loggers:
        w_logger = logging.getLogger(f"ez_positions_quick_wait_{account_key}")
        w_logger.propagate = False
        w_logger.setLevel(logging.INFO)
        if w_logger.hasHandlers():
            for h in list(w_logger.handlers):
                h.close()
                w_logger.removeHandler(h)
        w_log_filename = str(logs_dir / f"ez_positions_quick_wait_{account_key}.log")
        w_file_handler = logging.handlers.RotatingFileHandler( w_log_filename, maxBytes=20*1024*1024, backupCount=5, encoding="utf-8", mode="a", delay=True )
        w_file_handler.setFormatter(logging.Formatter(f"[%(asctime)s] [{account_key}] %(message)s", datefmt=DATE_FORMAT))
        w_logger.addHandler(w_file_handler)
        wait_loggers[account_key] = w_logger
    return wait_loggers[account_key]
# def get_account_logger(account_key: str) -> logging.Logger:
# """Get or create per-account logger. Strictly writes to FILE only."""
# if account_key not in account_loggers:
# acc_logger = logging.getLogger(f"ez_positions_quick_{account_key}")
# acc_logger.propagate = False # CRITICAL: Do not print to General Log/Console
# acc_logger.setLevel(logging.INFO)
# if acc_logger.hasHandlers():
# acc_logger.handlers.clear()
# acc_log_filename = str(logs_dir / f"ez_positions_quick_{account_key}.log")
# try:
# acc_file_handler = logging.handlers.RotatingFileHandler( acc_log_filename, maxBytes=5*1024*1024, backupCount=12, encoding='utf-8', mode='a', delay=True )
# acc_file_handler.setLevel(logging.INFO)
# acc_file_handler.setFormatter(file_formatter)
# acc_logger.addHandler(acc_file_handler)
# except Exception as e:
# logger.error(f"CRITICAL: Failed to create log file for {account_key}: {e}")
# account_loggers[account_key] = acc_logger
# return account_loggers[account_key]
_log_throttle = {}
FILE_IO_SEMAPHORE = asyncio.Semaphore(100)
HEADER_WIDTH = 200
STOCH_FMT_HEAD = "{:<22} | {:<12} | {:<14} | {:<10} | {:<4} | {:<12} | {:<15} | {:<6} | {:<10} | {:<10} | {:<10} | {:<8}"
STOCH_FMT_ROW = "{:<22} | {:<12} | {:<14} | {:<10} | {:<4} | {:<12} | {:<15} | {:<6} | {:<10} | {:<10} | {:<10} | {:<8}"
REENTRY_EPS = 0.0005
REENTRY_MIN_MULT = 1.3 # can tune later
REENTRY_MAX_MULT = 1.8 # depends on signal_quality
MAX_REENTRY_MINUTES = 1200
_global_order_timestamps = []
_order_timestamps = {}
# class UnifiedTechIndicators:
# @staticmethod
# def calculate_stoch_rsi(series: pd.Series, period=14, k_window=3, d_window=3):
# if series is None or series.empty or len(series) < period:
# return pd.DataFrame({'k': [50.0]*len(series), 'd': [50.0]*len(series)}, index=series.index)
# delta = series.diff()
# gain = delta.where(delta > 0, 0.0)
# loss = -delta.where(delta < 0, 0.0)
# alpha = 1.0 / period
# avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
# avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()
# rs = avg_gain / avg_loss.replace(0.0, np.nan)
# rsi = 100.0 - (100.0 / (1.0 + rs))
# rsi = rsi.fillna(50.0)
# min_rsi = rsi.rolling(window=period).min()
# max_rsi = rsi.rolling(window=period).max()
# range_rsi = (max_rsi - min_rsi).replace(0.0, 1e-9)
# stoch = ((rsi - min_rsi) / range_rsi) * 100.0
# k = stoch.rolling(window=k_window).mean()
# d = k.rolling(window=d_window).mean()
# return pd.DataFrame({'k': k, 'd': d}).fillna(50.0)
@dataclass

class AccountConfig:
    prefix: str
    api_key: str = field(init=False)
    api_secret: str = field(init=False)
    webhook_url: str = field(init=False)
    webhook_secret: str = field(init=False)
    client: Optional[Client] = field(default=None, init=False)
    _last_used_weight: int = field(default=0, init=False)
    _connector_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    # IP rotation for API calls (separate from WebSocket IP rotation)
    _ip_cycle: Optional[deque] = field(default=None, init=False)
    _current_ip: Optional[str] = field(default=None, init=False)
    _banned_ips: dict = field(default_factory=dict, init=False)
    def __post_init__(self):
        self.time_offset=0.0
        self._last_time_sync=0.0
        self.api_key = os.getenv(f"{self.prefix.lower()}_API_KEY")
        self.api_secret = os.getenv(f"{self.prefix.lower()}_API_SECRET")
        self.webhook_url = os.getenv(f"{self.prefix.lower()}_WEBHOOK_URL")
        self.webhook_secret = os.getenv(f"{self.prefix.lower()}_WEBHOOK_SECRET")
        self.webhook_url_2 = os.getenv(f"{self.prefix.lower()}2_WEBHOOK_URL2")
        self.webhook_secret_2 = os.getenv(f"{self.prefix.lower()}_WEBHOOK_SECRET2")
        self.webhook_url_3 = os.getenv(f"{self.prefix.lower()}_WEBHOOK_URL3")
        self.webhook_secret_3 = os.getenv(f"{self.prefix.lower()}_WEBHOOK_SECRET3")
        missing = []
        if not self.api_key: missing.append(f"{self.prefix.lower()}_API_KEY")
        if not self.api_secret: missing.append(f"{self.prefix.lower()}_API_SECRET")
        if not self.webhook_url: missing.append(f"{self.prefix.lower()}_WEBHOOK_URL")
        if not self.webhook_secret: missing.append(f"{self.prefix.lower()}_WEBHOOK_SECRET")
        if missing:
            raise ValueError(f"Missing environment variables for account '{self.prefix}': {', '.join(missing)}")
        self._init_ip_cycle()
    def _init_ip_cycle(self):
        self._ip_cycle = None
        self._current_ip = None
        self._banned_ips = {}
        if self.prefix.lower() in {"ang", "inf", "men", "fin", "flz"} and sys.platform != "darwin" and os.environ.get("EZ_DISABLE_IP_BINDING") != "1":
            self._ip_cycle = deque(["5.75.211.216", "49.13.39.233", "157.180.125.52"])
            logger.info(f"[{self.prefix}] IP switching enabled with IPs: {list(self._ip_cycle)}")
        else:
            logger.info(f"[{self.prefix}] IP switching disabled - prefix: {self.prefix.lower()}, platform: {sys.platform}, env: {os.environ.get('EZ_DISABLE_IP_BINDING')}")
    def _get_next_ip(self) -> Optional[str]:
        if not self._ip_cycle: return None
        now = time.time()
        for _ in range(len(self._ip_cycle)):
            ip = self._ip_cycle[0]
            banned_until = self._banned_ips.get(ip, 0)
            if banned_until and banned_until > now:
                self._ip_cycle.rotate(-1)
                continue
            self._ip_cycle.rotate(-1)
            return ip
        return None
    def _mark_ip_banned(self, ip: str, cooldown_seconds: int = 900):
        if not ip: return
        self._banned_ips[ip] = time.time() + cooldown_seconds
    def _create_bound_adapter(self, ip: str) -> HTTPAdapter:
        if not ip: return HTTPAdapter()
        class SourceAddressAdapter(HTTPAdapter):
            def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
                pool_kwargs["source_address"] = (ip, 0)
                super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
            def proxy_manager_for(self, *args, **kwargs):
                kwargs.setdefault("pool_kwargs", {})["source_address"] = (ip, 0)
                return super().proxy_manager_for(*args, **kwargs)
        return SourceAddressAdapter(max_retries=3)
    def _apply_ip_binding(self, client: Client):
        if sys.platform == "darwin" or os.environ.get("EZ_DISABLE_IP_BINDING") == "1" or not self._ip_cycle:
            return
        timeout_seconds = 20
        tried_ips = set()
        working_ip = None
        while True:
            candidate_ip = self._get_next_ip()
            if not candidate_ip or candidate_ip in tried_ips: break
            try:
                import socket
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((candidate_ip, 0))
                probe.close()
                working_ip = candidate_ip
                break
            except OSError as e:
                logger.info(f"[{self.prefix}] REST IP {candidate_ip} binding failed: {e}")
                self._mark_ip_banned(candidate_ip)
                tried_ips.add(candidate_ip)
        if working_ip:
            adapter = self._create_bound_adapter(working_ip)
            client.session.mount("https://", adapter)
            client.session.mount("http://", adapter)
            logger.debug(f"[{self.prefix}] Bound client session to dedicated IP {working_ip}")
            self._current_ip = working_ip
        else:
            logger.info(f"[{self.prefix}] No dedicated REST IP available, using default routing.")
            self._current_ip = None
    def handle_api_ban(self, cooldown_seconds: int = 900):
        banned_ip = getattr(self, "_current_ip", None)
        logger.info(f"[{self.prefix}] 🚫 IP BAN DETECTED on {banned_ip}, switching IPs...")
        self._mark_ip_banned(banned_ip, cooldown_seconds)
        if self.client:
            self._apply_ip_binding(self.client) if hasattr(self, '_apply_ip_binding') else None
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running(): asyncio.create_task(delete_heartbeat_on_ban())
            else: loop.run_until_complete(delete_heartbeat_on_ban())
        except: pass
    async def initialize(self):
        if not self.client:
            try:
                logger.info(f"[{self.prefix}] Initializing Binance client...")
                self.client = Client(api_key=self.api_key, api_secret=self.api_secret)
                self._apply_ip_binding(self.client)
                self.sync_binance_time()
                logger.debug(f"Initialized Binance client for account '{self.prefix}'", extra={'color': "green"})
            except BinanceAPIException as e:
                logger.error(f"[{self.prefix}] Binance API error during initialization: {e}")
            except Exception as e:
                logger.error(f"Failed to initialize Binance client for account '{self.prefix}': {e}")
    def sync_binance_time(self):
        current_time = time.time()
        if hasattr(self, '_last_time_sync') and (current_time - self._last_time_sync) < 3600: return True
        try:
            server_time_response = self.client.futures_time()
            server_time = server_time_response['serverTime']
            local_time = int(time.time() * 1000)
            time_diff = server_time - local_time
            self.time_offset = time_diff
            self._last_time_sync = current_time
            return True
        except Exception as e:
            logger.error(f"[{self.prefix}] Failed to sync time with Binance: {e}")
            return False
    def get_binance_timestamp(self):
        if hasattr(self, 'time_offset'): return int(time.time() * 1000) + self.time_offset
        return int(time.time() * 1000)
    async def close(self):
        if self.client: logger.debug(f"Closed Binance client for account '{self.prefix}'", extra={'color': "green"})

def load_accounts(config_obj=None) -> Dict[str, AccountConfig]: # pylint: disable=function-redefined
    accounts = {}
    keys_to_load = []
    if config_obj and hasattr(config_obj, 'ACCOUNT_KEYS'):
        keys_to_load = config_obj.ACCOUNT_KEYS
    else:
        keys_to_load = ['ang', 'inf', 'men', 'flz', 'fin']
    for account_key in keys_to_load:
        try:
            acc_config = AccountConfig(account_key)
            accounts[account_key] = acc_config
        except Exception as e:
            logger.error(f"❌ Failed to load account {account_key}: {e}")
    return accounts
async def load_initial_market_data(data_manager, config):
    """Forces an immediate load of indicators from disk to warm up the cache."""
    logger.info("📥 [STARTUP] Pre-loading market data from disk...")
    try:
        data_file = config.DATA_DIR / "latest_market_data.json"
        if not data_file.exists():
            logger.warning(f"⚠️ [STARTUP] {data_file} not found. Indicators will be empty until Redis/WS updates.")
            return
        async with FILE_IO_SEMAPHORE:
            async with aiofiles.open(data_file, 'r') as f:
                content = await f.read()
                data = safe_json_loads(content)
        count = 0
        if isinstance(data, dict):
            for symbol, indicators in data.items():
                if isinstance(indicators, dict):
                    count += 1
            data_manager._cold_data = data
        logger.info(f"✅ [STARTUP] Successfully injected indicators for {count} symbols.")
    except Exception as e:
        logger.error(f"❌ [STARTUP] Failed to load initial market data: {e}")

def get_server_heartbeat_path(account_key: str = None) -> Path:
    if account_key: return config.DATA_DIR / f"ez_positions_quick_running_{account_key}"
    return config.DATA_DIR / "ez_positions_quick_running"
SERVER_HOST = "niels@157.180.125.52"
HEARTBEAT_STALE_THRESHOLD = 30
HEARTBEAT_UPDATE_INTERVAL = 30
_heartbeat_deleted_due_to_ban = False
async def check_server_heartbeat(account_key: str = None) -> bool:
    try:
        if current_env['env'] == 'server':
            accounts_to_check = [account_key] if account_key else config.ACCOUNT_KEYS
            server_path_local = Path("/home/niels/binance/data")
            for acc in accounts_to_check:
                heartbeat_path = server_path_local / f"ez_positions_quick_running_{acc}"
                if heartbeat_path.exists():
                    try:
                        mtime = heartbeat_path.stat().st_mtime
                        if (time.time() - mtime) < HEARTBEAT_STALE_THRESHOLD: return True
                    except: pass
            return False
        else: return True
    except Exception as e:
        logger.debug(f"[HEARTBEAT] Check failed: {e}")
        return True
async def safe_check_server_heartbeat(account_key: str = None) -> bool: return await check_server_heartbeat(account_key)
async def update_server_heartbeat(account_key: str = None):
    try:
        if current_env['env'] == 'server':
            server_path_local = Path("/home/niels/binance/data")
            is_on_server = server_path_local.exists()
            accounts_to_update = [account_key] if account_key else config.ACCOUNT_KEYS
            if is_on_server:
                for acc in accounts_to_update:
                    heartbeat_path = server_path_local / f"ez_positions_quick_running_{acc}"
                    try:
                        if not heartbeat_path.parent.exists():
                            heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
                            try: os.chmod(str(heartbeat_path.parent), 0o755)
                            except: pass
                        if heartbeat_path.exists(): os.utime(str(heartbeat_path), None)
                        else:
                            heartbeat_path.touch(mode=0o644)
                            try: os.chmod(str(heartbeat_path), 0o644)
                            except: pass
                    except Exception as e: logger.info(f"[HEARTBEAT] Local update failed for {acc}: {e}")
            else:
                if account_key:
                    server_path = f"/home/niels/binance/data/ez_manage_running_{account_key}"
                    try:
                        subprocess.run(["ssh", "-o", "ConnectTimeout=2", "-o", "StrictHostKeyChecking=no", SERVER_HOST, "mkdir", "-p", "/home/niels/binance/data"], timeout=3, capture_output=True)
                        subprocess.run(["ssh", "-o", "ConnectTimeout=2", "-o", "StrictHostKeyChecking=no", SERVER_HOST, "touch", server_path], timeout=3, capture_output=True)
                        subprocess.run(["ssh", "-o", "ConnectTimeout=2", "-o", "StrictHostKeyChecking=no", SERVER_HOST, "chmod", "644", server_path], timeout=3, capture_output=True)
                    except: pass
    except Exception as e: logger.info(f"[HEARTBEAT] General failure: {e}")

def sf(v): return float(v) if v is not None else 50.0
async def create_server_heartbeat():
    try: await update_server_heartbeat()
    except Exception as e: logger.info(f"[HEARTBEAT] Failed to create heartbeat (non-critical, continuing): {e}")
async def delete_server_heartbeat():
    if current_env['env'] == 'server':
        for account_key in config.ACCOUNT_KEYS:
            heartbeat_path = get_server_heartbeat_path(account_key)
            try:
                if heartbeat_path.exists(): heartbeat_path.unlink()
            except FileNotFoundError: pass
            except Exception as e: logger.debug(f"[HEARTBEAT] Failed to delete heartbeat for {account_key}: {e}")
    else:
        for account_key in config.ACCOUNT_KEYS:
            server_path = f"/home/niels/binance/data/ez_manage_running_{account_key}"
            try: subprocess.run(["ssh", "-o", "ConnectTimeout=2", "-o", "StrictHostKeyChecking=no", SERVER_HOST, "rm", "-f", server_path], timeout=3, capture_output=True)
            except: pass
async def delete_heartbeat_on_ban():
    global _heartbeat_deleted_due_to_ban
    if current_env['env'] == 'server':
        try:
            await delete_server_heartbeat()
            _heartbeat_deleted_due_to_ban = True
            logger.critical(f"[HEARTBEAT] Server banned - heartbeat deleted to allow failover")
        except Exception as e: logger.error(f"[HEARTBEAT] Failed to delete heartbeat on ban: {e}")
async def restore_heartbeat_on_ban_lifted():
    global _heartbeat_deleted_due_to_ban
    if current_env['env'] == 'server' and _heartbeat_deleted_due_to_ban:
        try:
            for account_key in config.ACCOUNT_KEYS: await update_server_heartbeat(account_key)
            _heartbeat_deleted_due_to_ban = False
            logger.critical(f"[HEARTBEAT] Ban lifted - heartbeats restored for all accounts, server resuming order execution")
        except Exception as e: logger.error(f"[HEARTBEAT] Failed to restore heartbeat after ban lifted: {e}")
async def periodic_heartbeat_update(active_account_keys: list):
    global _heartbeat_deleted_due_to_ban
    logger.info(f"[HEARTBEAT] Starting high-frequency loop for: {active_account_keys}")
    while True:
        try:
            if not _heartbeat_deleted_due_to_ban:
                for acc in active_account_keys: await update_server_heartbeat(acc)
            await asyncio.sleep(5)
        except asyncio.CancelledError: break
        except Exception as e:
            logger.error(f"[HEARTBEAT] Loop error: {e}")
            await asyncio.sleep(8)

def _sync_atomic_write_tracker(temp_path, target_path, data):
    try:
        with open(temp_path, "wb") as f:
            f.write(data)
            f.flush()
        os.replace(temp_path, target_path)
        try:
            os.chmod(target_path, 0o664)
        except Exception:
            pass
    except Exception as e:
        # Cleanup temp file on failure
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        raise e

def calculate_dynamic_quantity(symbol: str, current_price: float, score: int, config_obj, trade_manager, tracker_data: Dict[str, Any] = None, is_long: bool = True, indicators: Dict[str, Any] = None, metrics: Dict[str, Any] = None) -> float:
    if current_price <= 0: return 0.0
    base_usdc_size = safe_fetch_float(getattr(config_obj, 'START_POSITION_SIZE', 45.0), 45.0)
    # --- 1. FALLING KNIFE GUARD ---
    knife_penalty = 1.0
    k_1m = safe_fetch_float(metrics.get('stoch_k_1m', 50.0))
    d_1m = safe_fetch_float(metrics.get('stoch_d_1m', 50.0))
    k_15m = safe_fetch_float(indicators.get('stoch_k_15m', 50.0))
    d_15m = safe_fetch_float(indicators.get('stoch_d_15m', 50.0))
    ha_15m = indicators.get('ha_15m', 'neutral')
    now_ts = time.time()
    exact_tick_ts= metrics.get('_tick_ts')
    true_lag = now_ts - exact_tick_ts
    if true_lag > 10.0: should_scalp = False
    # Immediate Momentum Check
    if is_long:
        if (k_1m < d_1m and true_lag < 10.0)and k_15m < d_15m:
            knife_penalty *= 0.5
    else: # Short
        if (k_1m > d_1m and true_lag < 10.0) and k_15m > d_15m:
            knife_penalty *= 0.5
    sma_1h = safe_fetch_float(indicators.get('sma_200_1h'), 0.0)
    sma_4h = safe_fetch_float(indicators.get('sma_200_4h'), 0.0)
    # --- 2. EXTRACT CONTEXT & BANDS ---
    dc_high_3m = safe_fetch_float(indicators.get('dc_high_3m'), 0.0)
    dc_high_15m = safe_fetch_float(indicators.get('dc_high_15m'), 0.0)
    dc_high_1h = safe_fetch_float(indicators.get('dc_high_1h'), 0.0)
    dc_high_4h = safe_fetch_float(indicators.get('dc_high_4h'), 0.0)
    dc_high_D = safe_fetch_float(indicators.get('dc_high_D'), 0.0)
    dc_low_3m = safe_fetch_float(indicators.get('dc_low_3m'), 0.0)
    dc_low_15m = safe_fetch_float(indicators.get('dc_low_15m'), 0.0)
    dc_low_1h = safe_fetch_float(indicators.get('dc_low_1h'), 0.0)
    dc_low_4h = safe_fetch_float(indicators.get('dc_low_4h'), 0.0)
    dc_low_D = safe_fetch_float(indicators.get('dc_low_D'), 0.0)
    sma_15m = safe_fetch_float(indicators.get('sma_200_15m'), 0.0)
    ema_50_15m = safe_fetch_float(indicators.get('ema_50_15m', sma_15m), 0.0)
    tracker_reason = str(tracker_data.get('last_reason', '')) if tracker_data else ""
    # --- 3. TIERED DC BREAKOUT SCALPING ---
    dc_tier_mult = 0.0
    if is_long:
        if dc_high_D > 0 and current_price >= dc_high_D: dc_tier_mult = 5.0
        elif dc_high_4h > 0 and current_price >= dc_high_4h: dc_tier_mult = 4.0
        elif dc_high_1h > 0 and current_price >= dc_high_1h: dc_tier_mult = 3.0
        elif dc_high_15m > 0 and current_price >= dc_high_15m: dc_tier_mult = 2.0
        elif dc_high_3m > 0 and current_price >= dc_high_3m: dc_tier_mult = 1.0
    else:
        if dc_low_D > 0 and current_price <= dc_low_D: dc_tier_mult = 5.0
        elif dc_low_4h > 0 and current_price <= dc_low_4h: dc_tier_mult = 4.0
        elif dc_low_1h > 0 and current_price <= dc_low_1h: dc_tier_mult = 3.0
        elif dc_low_15m > 0 and current_price <= dc_low_15m: dc_tier_mult = 2.0
        elif dc_low_3m > 0 and current_price <= dc_low_3m: dc_tier_mult = 1.0
    if dc_tier_mult > 0.0 and ("DC_MOMENTUM_SCALP" in tracker_reason or score >= 20):
        # DIRECT OVERRIDE: We are in the scalp mode, force this size exactly
        target_notional = base_usdc_size * dc_tier_mult * knife_penalty
        raw_qty = target_notional / current_price
        min_qty_symbol = trade_manager.min_qty.get(symbol, 0.0)
        return max(raw_qty, min_qty_symbol)
    # --- 3. B.O.T.A. (BRING OUT THE ARMY) MULTIPLIER ---
    bota_mult = 1.0
    top_buyer_penalty = 1.0
    # Check if this was flagged as a massive pullback re-entry
    tracker_reason = str(tracker_data.get('last_reason', '')) if tracker_data else ""
    if "BOTA" in tracker_reason or score >= 28:
        bota_mult = 4.0 # 4x standard size for the golden setup!
    else:
        # If not BOTA, check if we are buying the absolute breakout top
        if is_long and dc_high_15m > 0 and current_price >= (dc_high_15m * 0.99):
            top_buyer_penalty = 0.4 # Buy the breakout, but keep it SMALL (test the waters)
        elif not is_long and dc_low_15m > 0 and current_price <= (dc_low_15m * 1.01):
            top_buyer_penalty = 0.4
    # --- 4. SCORING MULTIPLIERS ---
    score_mult = 0.2
    if score >= 18: score_mult = 3.0
    elif score >= 12: score_mult = 2.0
    elif score >= 8: score_mult = 1.5
    elif score >= 4: score_mult = 1.0
    dc_mult = 1.0
    if dc_high_15m > 0 and current_price > 0:
        width_pct_15 = ((dc_high_15m - dc_low_15m) / current_price) * 100
        if width_pct_15 < 1.0: dc_mult = 0.3
        elif width_pct_15 > 3.0: dc_mult = 1.5 # Capped from 2.0
        elif width_pct_15 > 6.0: dc_mult = 2.0 # Capped from 3.0
    value_mult = 1.0
    if sma_4h > 0:
        dist_pct = abs((current_price - sma_4h) / sma_4h) * 100
        if dist_pct < 1.0: value_mult = 1.3
    if sma_1h > 0:
        dist_pct = abs((current_price - sma_1h) / sma_1h) * 100
        if dist_pct < 1.0: value_mult *= 1.3
    # Sentiment Logic
    global_sentiment = safe_fetch_float(indicators.get('0market_sentiment_score'), 0.0)
    local_sentiment = safe_fetch_float(indicators.get('0market_sentiment_local'), 0.0)
    crash_mult = 1.0
    if not is_long and local_sentiment < -20: crash_mult = 2.5
    elif is_long and global_sentiment < -40: crash_mult = 1.5
    elif is_long and local_sentiment > 40: crash_mult = 2.0
    # Ratios
    ratio_mult = 1.0
    skew_factor = global_sentiment / 100.0
    if is_long: ratio_mult = 1.0 + skew_factor
    else: ratio_mult = 1.0 - skew_factor
    ratio_mult = max(0.4, min(1.8, ratio_mult))
    alpha_mult = 1.0
    alpha = local_sentiment - global_sentiment
    alpha_impact = alpha / 100.0
    if is_long: alpha_mult = 1.0 + alpha_impact
    else: alpha_mult = 1.0 - alpha_impact
    alpha_mult = max(0.5, min(1.5, alpha_mult))
    # Score Multiplier
    score_mult = 0.0
    if score >= 18: score_mult = 3.0
    elif score >= 12: score_mult = 2.0
    elif score >= 8: score_mult = 1.5
    elif score >= 4: score_mult = 1.0
    else: score_mult = 0.2
    # Modeistory
    current_mode = getattr(trade_manager, 'market_mode', "NORMAL_MODE")
    mode_mult = 1.5 if current_mode == "EXTREME_MODE" else (0.4 if current_mode == "LIGHT_MODE" else 1.0)
    history_mult = 1.0
    if tracker_data:
        win_rate = safe_fetch_float(tracker_data.get('win_rate_%', 0.0), 50.0)
        if win_rate > 60: history_mult = 1.3
        elif win_rate < 30: history_mult = 0.3
    # --- 3. FINAL CALCULATION ---
    # Apply Knife Penalty
    army_deployed = False
    if score >= 25 or (tracker_data and "BRING_OUT_THE_ARMY" in str(tracker_data.get('last_reason', ''))):
        dc_mult = 5.0 # 5x base size. THE ARMY.
        value_mult = 1.5
        score_mult = 2.0
        army_deployed = True
    elif tracker_data and "BREAKOUT_PLAY" in str(tracker_data.get('last_reason', '')):
        # Breakouts are high probability but high risk of fakeout. Use standard size, don't over-leverage.
        dc_mult = 1.0
        value_mult = 1.0
    # --- 3. FINAL CALCULATION ---
    target_notional = base_usdc_size * mode_mult * ratio_mult * alpha_mult * score_mult * history_mult * crash_mult * value_mult * dc_mult * knife_penalty
    # Increase the max cap if the army is deployed
    max_notional = base_usdc_size * (15.0 if army_deployed else 6.0)
    target_notional = min(target_notional, max_notional)
    target_notional = base_usdc_size * mode_mult * ratio_mult * alpha_mult * score_mult * history_mult * crash_mult * value_mult * dc_mult * knife_penalty
    max_notional = base_usdc_size * 6.0
    target_notional = min(target_notional, max_notional)
    raw_qty = target_notional / current_price
    min_qty_symbol = trade_manager.min_qty.get(symbol, 0.0)
    final_qty = max(raw_qty, min_qty_symbol)
    if (final_qty * current_price) < 5.5: final_qty = 6.0 / current_price
    return final_qty
# def calculate_dynamic_quantity(symbol: str, current_price: float, score: int, config_obj, trade_manager, tracker_data: Dict[str, Any] = None, is_long: bool = True, indicators: Dict[str, Any] = None) -> float:#SCALP
# if current_price <= 0: return 0.0
# base_usdc_size = safe_fetch_float(getattr(config_obj, 'START_POSITION_SIZE', 45.0), 45.0)
# dc_high_15m = safe_fetch_float(indicators.get('dc_high_15m'), 0.0)
# dc_dc_low_15m = safe_fetch_float(indicators.get('dc_low_15m'), 0.0)
# dc_high_1h = safe_fetch_float(indicators.get('dc_high_1h'), 0.0)
# dc_low_1h = safe_fetch_float(indicators.get('dc_low_1h'), 0.0)
# sma_1h = safe_fetch_float(indicators.get('sma_200_1h'), 0.0)
# sma_4h = safe_fetch_float(indicators.get('sma_200_4h'), 0.0)
# score_mult = 1.0
# dc_mult = 1.0
# if dc_high_15m > 0 and current_price > 0:
# width_pct_15 = ((dc_high_15m - dc_dc_low_15m) / current_price) * 100
# if width_pct_15 < 1.0: dc_mult = 0.3 # Squeeze = Go Bigger
# elif width_pct_15 > 3.0: dc_mult = 2 # High Vol = Go Smaller
# elif width_pct_15 > 6.0: dc_mult = 3
# if dc_high_1h > 0 and current_price > 0:
# width_pct_1h = ((dc_high_1h - dc_low_1h) / current_price) * 100
# if width_pct_1h < 5.0: dc_mult *= 0.3 # Squeeze = Go Bigger
# elif width_pct_1h > 20.0: dc_mult *= 2 # High Vol = Go Smaller
# elif width_pct_1h > 40.0: dc_mult *= 3
# value_mult = 1.0
# if sma_4h > 0:
# dist_pct = abs((current_price - sma_4h) / sma_4h) * 100
# if dist_pct < 1.0: value_mult = 1.3 # Pullback Value
# if sma_1h > 0:
# dist_pct = abs((current_price - sma_1h) / sma_1h) * 100
# if dist_pct < 1.0: value_mult *= 1.3 # Pullback Value
# global_sentiment = safe_fetch_float(indicators.get('0market_sentiment_score'), 0.0)
# local_sentiment = safe_fetch_float(indicators.get('0market_sentiment_local'), 0.0)
# crash_mult = 1.0
# if not is_long and local_sentiment < -20: crash_mult = 2.5
# elif is_long and global_sentiment < -40: crash_mult = 1.5
# elif is_long and local_sentiment > 40: crash_mult = 2.0
# ratio_mult = 1.0
# skew_factor = global_sentiment / 100.0
# if is_long: ratio_mult = 1.0 + skew_factor
# else: ratio_mult = 1.0 - skew_factor
# ratio_mult = max(0.4, min(1.8, ratio_mult))
# alpha_mult = 1.0
# alpha = local_sentiment - global_sentiment
# alpha_impact = alpha / 100.0
# if is_long: alpha_mult = 1.0 + alpha_impact
# else: alpha_mult = 1.0 - alpha_impact
# alpha_mult = max(0.5, min(1.5, alpha_mult))
# score_mult = 0.0
# if score >= 10: score_mult = 3.0
# elif score >= 8: score_mult = 2.0
# elif score >= 6: score_mult = 1.5
# elif score >= 4: score_mult = 1.0
# elif score >= 2: score_mult = 0.2
# else: return 0.0
# current_mode = getattr(trade_manager, 'market_mode', "NORMAL_MODE")
# mode_mult = 1.5 if current_mode == "EXTREME_MODE" else (0.4 if current_mode == "LIGHT_MODE" else 1.0)
# history_mult = 1.0
# if tracker_data:
# win_rate = safe_fetch_float(tracker_data.get('win_rate_%', 0.0), 50.0)
# if win_rate > 60: history_mult = 1.3
# elif win_rate < 30: history_mult = 0.3
# target_notional = base_usdc_size * mode_mult * ratio_mult * alpha_mult * score_mult * history_mult * crash_mult * value_mult * dc_mult
# max_notional = base_usdc_size * 6.0
# target_notional = min(target_notional, max_notional)
# raw_qty = target_notional / current_price
# min_qty_symbol = trade_manager.min_qty.get(symbol, 0.0)
# final_qty = max(raw_qty, min_qty_symbol)
# if (final_qty * current_price) < 5.5: final_qty = 6.0 / current_price
# return final_qty

def _refresh_last_events_cache():
    """Refresh the in-memory cache of last_events.json data."""
    global _last_events_cache, _last_events_cache_time
    try:
        last_events_path = os.path.join('data', 'last_events.json')
        if not os.path.exists(last_events_path):
            logger.warning(f"WARNING: last_events.json not found at {last_events_path}")
            _last_events_cache = {}
            _last_events_cache_time = time.time()
            return
        with open(last_events_path, "rb") as f:
            _last_events_cache = safe_json_loads(f.read())
        _last_events_cache_time = time.time()
        logger.debug(f"Refreshed last_events.json cache with {len(_last_events_cache)} symbols")
    except Exception as e:
        logger.warning(f"WARNING: Error refreshing last_events.json cache: {e}")
        _last_events_cache = {}
        _last_events_cache_time = time.time()
# class LightweightPositionClient:
# def __init__(self, redis_manager, accounts):
# self.redis_manager = redis_manager
# self.accounts = accounts
# self.positions_by_account = defaultdict(dict)
# self._running = True
# # Start background sync
# asyncio.create_task(self._sync_loop())
# def _normalize_payload(self, data):
# """Recursively fix double-encoded JSON strings."""
# try:
# if isinstance(data, (bytes, bytearray)):
# return self._normalize_payload(orjson.loads(data)) # type: ignore # pylint: disable=no-member,c-extension-no-member
# if isinstance(data, str):
# return self._normalize_payload(orjson.loads(data)) # type: ignore # pylint: disable=no-member,c-extension-no-member
# except: pass
# return data
# async def initial_sync(self):
# """
# BLOCKING load from disk to ensure we have state before main loop starts.
# Call this from main()!
# """
# logger.info("💾 [POS_CLIENT] Performing Initial Disk Sync...")
# for account_key in self.accounts:
# await self._load_from_disk(account_key)
# async def _sync_loop(self):
# logger.info("🔗 [POS_CLIENT] Starting Sync Loop (Redis + Disk Fallback)...")
# while self._running:
# try:
# # 1. Get Redis Client (Non-blocking)
# client = None
# if self.redis_manager:
# client = self.redis_manager.connections.get("local")
# for account_key in self.accounts:
# success_from_redis = False
# # 2. Try Redis (Priority)
# if client:
# try:
# raw = await client.get(f"positions:{account_key}")
# if raw:
# payload = self._normalize_payload(raw)
# # Handle Service "Wrapped" Format
# positions_data = payload
# if isinstance(payload, dict):
# if "positions" in payload: positions_data = payload["positions"]
# elif "data" in payload: positions_data = payload["data"]
# if positions_data:
# self._update_local_cache(account_key, positions_data)
# success_from_redis = True
# except Exception:
# pass # Redis failed, proceed to disk silently
# # 3. Try Disk (Fallback)
# # Run this if Redis failed OR returned empty
# if not success_from_redis:
# await self._load_from_disk(account_key)
# except Exception as e:
# logger.error(f"[POS_CLIENT] Loop Crash: {e}")
# await asyncio.sleep(1.0)
# async def _load_from_disk(self, account_key):
# """Loads positions from JSON files."""
# try:
# # Construct path (Adjust based on your config structure)
# base = getattr(config, 'BASE_PATH', Path.home()/'binance') / account_key
# disk_pos = {}
# files_found = False
# for side in ["long", "short"]:
# fpath = base / f"{side}_positions.json"
# if await aio_os.path.exists(fpath):
# files_found = True
# async with aiofiles.open(fpath, "rb") as f:
# content = await f.read()
# if content:
# chunk = self._normalize_payload(content)
# if isinstance(chunk, dict):
# disk_pos.update(chunk)
# elif isinstance(chunk, list):
# # Convert list to dict
# for p in chunk:
# if isinstance(p, dict):
# sym = p.get('symbol', '')
# ps = p.get('position_side', '')
# if sym and ps:
# k = f"{account_key}:{sym}_{ps}"
# disk_pos[k] = p
# if disk_pos:
# self._update_local_cache(account_key, disk_pos)
# # else:
# # # Optional: Warn only if files were expected but empty
# # if files_found: logger.debug(f"[POS_CLIENT] Disk files found but empty for {account_key}")
# except Exception as e:
# # logger.error(f"[POS_CLIENT] Disk Load Failed for {account_key}: {e}")
# pass
# def _update_local_cache(self, account_key, data_map):
# new_map = {}
# iterable = data_map.values() if isinstance(data_map, dict) else data_map
# for p_data in iterable:
# if not isinstance(p_data, dict): continue
# try:
# pos = Position.from_dict(p_data)
# key = f"{account_key}:{pos.symbol}_{pos.position_side}"
# new_map[key] = pos
# except: continue
# # Atomic Update
# if new_map:
# self.positions_by_account[account_key] = new_map
# def get_position(self, position_key):
# try:
# acc, _, _ = parse_position_key(position_key)
# return self.positions_by_account.get(acc, {}).get(position_key)
# except: return None
# # Dummy interfaces
# async def fetch_positions(self, *args): pass
# async def initialize(self): pass
# async def _ensure_hedge_engine_initialized(self): pass
# def get_position(self, position_key):
# try:
# acc, _, _ = parse_position_key(position_key)
# return self.positions_by_account.get(acc, {}).get(position_key)
# except: return None
# class LightweightPositionClient:
# def __init__(self, redis_manager, accounts):
# self.redis_manager = redis_manager
# self.accounts = accounts
# self.positions_by_account = defaultdict(dict)
# self.positions = defaultdict(dict)
# self._running = True
# asyncio.create_task(self._sync_loop())
# def _normalize_redis_payload(self, payload: Any) -> Any:
# if isinstance(payload, (bytes, bytearray)):
# try:
# return orjson.loads(payload) # type: ignore # pylint: disable=no-member,c-extension-no-member
# except Exception:
# try:
# payload = payload.decode("utf-8")
# except Exception:
# return {}
# if isinstance(payload, str):
# try:
# return safe_json_loads(payload)
# except Exception:
# return payload
# return payload
# # def _normalize_timestamp(self, ts):
# # if not ts: return None
# # if isinstance(ts, (int, float)):
# # try: ts = datetime.fromtimestamp(float(ts), tz=timezone.utc)
# # except Exception: return None
# # elif isinstance(ts, str):
# # try: ts = isoparse(ts)
# # except Exception: return None
# # if isinstance(ts, datetime) and ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
# # return ts if isinstance(ts, datetime) else None
# # def _extract_position_timestamp(self, position):
# # if not position: return None
# # for attr in ("last_updated", "prev_gain_last_updated", "opened_at","mark_price_last_updated"):
# # ts = getattr(position, attr, None)
# # normalized = self._normalize_timestamp(ts)
# # if normalized: return normalized
# # return None
# # async def _load_positions_quick_from_files(self, account_key: str) :
# # if account_key not in self._allowed_accounts:
# # logger.warning(f"[_load_positions_quick_from_files] BLOCKED: Account '{account_key}' not in allowed accounts {self._allowed_accounts}")
# # return {}
# # if account_key not in config.ACCOUNT_KEYS: return {}
# # bucket: Dict[str, Position] = {}
# # base_dir = Path(self.config.BASE_PATH)
# # candidates = [account_key, account_key.lower(), account_key.upper()]
# # selected_dir: Optional[Path] = None
# # for candidate in candidates:
# # candidate_dir = base_dir / candidate
# # if candidate_dir.exists():
# # selected_dir = candidate_dir
# # break
# # if not selected_dir:
# # return bucket
# # # First, try to load from update files and merge incrementally
# # for side in ("LONG", "SHORT"):
# # update_file = selected_dir / f"position_updates_{side.lower()}.json"
# # if await aio_os.path.exists(str(update_file)):
# # try:
# # update_data = await load_json_safe(update_file, account_key)
# # if isinstance(update_data, dict):
# # for position_key, pos_dict in update_data.items():
# # if position_key == "_last_update" or not isinstance(pos_dict, dict):
# # continue
# # if not isinstance(position_key, str) or not position_key.startswith(f"{account_key}:"):
# # continue
# # try:
# # pos_obj = Position.from_dict(pos_dict)
# # existing_pos = bucket.get(position_key)
# # existing_ts = self._extract_position_timestamp(existing_pos) if existing_pos else None
# # new_ts = self._extract_position_timestamp(pos_obj)
# # if existing_ts and new_ts and existing_ts > new_ts:
# # continue
# # bucket[position_key] = self.ensure_position_floats(pos_obj)
# # except Exception as e:
# # logger.debug(f"[load_positions:updates] Failed to parse position {position_key} from update file: {e}")
# # except Exception as e:
# # logger.debug(f"[load_positions:updates] Failed to load update file {update_file}: {e}")
# # for filename, side in (("long_positions.json", "LONG"), ("short_positions.json", "SHORT")):
# # file_path = selected_dir / filename
# # main_file_exists = await aio_os.path.exists(str(file_path))
# # main_file_fresh = False
# # main_file_age = 999999.0
# # if main_file_exists:
# # try:
# # main_file_mtime = file_path.stat().st_mtime
# # main_file_age = time.time() - main_file_mtime
# # main_file_fresh = main_file_age <= 10.0
# # except Exception:
# # pass
# # # Check alternative files if main file is missing or stale
# # #alt_files = [f"{side.lower()}_positions_changed.json", f"{side.lower()}_positions_updated.json"]
# # best_file = file_path if main_file_exists and main_file_fresh else None
# # best_age = main_file_age if main_file_exists and main_file_fresh else 999999.0
# # # for alt_filename in alt_files:
# # # alt_path = selected_dir / alt_filename
# # # if await aio_os.path.exists(str(alt_path)):
# # # try:
# # # alt_mtime = alt_path.stat().st_mtime
# # # alt_age = time.time() - alt_mtime
# # # if alt_age < best_age:
# # # best_file = alt_path
# # # best_age = alt_age
# # # except Exception:
# # # pass
# # if best_file:
# # if best_file != file_path:
# # logger.warning(f"[load_positions:quick][{account_key}:{side}] Main file {filename} is {'missing' if not main_file_exists else f'stale ({main_file_age:.1f}s old)'}, using {best_file.name} (age: {best_age:.1f}s)")
# # else:
# # logger.debug(f"[load_positions:quick][{account_key}:{side}] Using main file {filename} (age: {main_file_age:.1f}s)")
# # data = await load_json_safe(best_file, account_key)
# # if not isinstance(data, dict):
# # continue
# # for raw_key, payload in data.items():
# # if not isinstance(payload, dict):
# # continue
# # symbol = payload.get('symbol') or ''
# # if not symbol:
# # symbol = str(raw_key).split(':', 1)[-1] if ':' in str(raw_key) else str(raw_key)
# # symbol = symbol.replace(f"_{side}", "").replace("_LONG", "").replace("_SHORT", "")
# # symbol = symbol.replace(":LONG", "").replace(":SHORT", "")
# # symbol = str(symbol).strip().upper()
# # if not symbol:
# # continue
# # symbol = clean_and_repair_symbol(symbol) if symbol else ""
# # symbol = symbol.strip().upper()
# # if not symbol:
# # continue
# # try:
# # position_key = construct_position_key(account_key, symbol, side)
# # except Exception:
# # position_key = f"{account_key}:{symbol}_{side}"
# # payload_copy = dict(payload)
# # payload_copy["symbol"] = symbol
# # payload_copy["position_side"] = side
# # try:
# # pos_obj = Position.from_dict(payload_copy)
# # except Exception as exc:
# # logger.debug(f"[load_positions:quick] Parse failed for {position_key}: {exc}")
# # continue
# # existing_pos = bucket.get(position_key)
# # existing_ts = self._extract_position_timestamp(existing_pos) if existing_pos else None
# # new_ts = self._extract_position_timestamp(pos_obj)
# # if existing_ts and new_ts and existing_ts > new_ts:
# # continue
# # bucket[position_key] = self.ensure_position_floats(pos_obj)
# # return bucket
# async def _sync_loop(self):
# logger.info("🔗 [POS_CLIENT] Starting Robust Sync Loop (Redis -> Disk)...")
# while self._running:
# try:
# for account_key in self.accounts:
# # CRITICAL: Only load positions for allowed accounts
# if account_key not in self.accounts:
# logger.warning(f"[_load_positions_from_redis] BLOCKED: Account '{account_key}' not in allowed accounts {self.accounts}")
# return {}
# if account_key not in config.ACCOUNT_KEYS: return {}
# if not self.redis_manager or not hasattr(self.redis_manager, "get"):
# return {}
# async def fetch_redis():
# try:
# return await self.redis_manager.get(f"positions:{account_key}"), None
# except Exception as exc:
# logger.debug(f"[redis_position_fallback] Redis get failed for {account_key}: {exc}")
# return None, str(exc)
# # async def fetch_json():
# # try:
# # return await self._load_positions_quick_from_files(account_key), None
# # except Exception as exc:
# # return None, str(exc)
# start_time = time.time()
# redis_task = asyncio.create_task(fetch_redis())
# # json_task = asyncio.create_task(fetch_json())
# done, pending = await fetch_redis()#await asyncio.wait(redis_task)
# first_result, first_source, first_time = None, None, time.time() - start_time
# for task in done:
# result, err = await task
# if task == redis_task:first_result, first_source = result, "redis"
# else:first_result, first_source = result, "json"
# if pending:
# await asyncio.wait(pending, timeout=0.3)
# for task in pending:
# if task.done():
# result, err = await task
# if task == redis_task and result:first_result, first_source = result, "redis"
# elif result and not first_result:first_result, first_source = result, "json"
# else:
# try:
# result, err = await asyncio.wait_for(task, timeout=0.3)
# if task == redis_task and result:first_result, first_source = result, "redis"
# elif result and not first_result:first_result, first_source = result, "json"
# except:pass
# redis_result, redis_err = await redis_task
# # json_result, json_err = await json_task
# # if not redis_result and not json_result:
# # return {}
# raw_payload = None
# if redis_result:
# raw_payload = self._normalize_redis_payload(redis_result)
# # json_bucket = None
# # if json_result and isinstance(json_result, dict) and len(json_result) > 0:
# # json_bucket = json_result
# # if raw_payload:
# # meta_source = raw_payload if isinstance(raw_payload, dict) else {}
# # redis_ts = None
# # if isinstance(meta_source, dict):
# # timestamp_candidate = meta_source.get("timestamp")
# # if not timestamp_candidate:
# # meta_section = meta_source.get("meta")
# # if isinstance(meta_section, dict):
# # timestamp_candidate = meta_section.get("positions_last_sync") or meta_section.get("timestamp")
# # redis_ts = safe_datetime(timestamp_candidate)
# # base_dir = Path(self.config.BASE_PATH)
# # candidates = [account_key, account_key.lower(), account_key.upper()]
# # json_ts = None
# # for candidate in candidates:
# # candidate_dir = base_dir / candidate
# # if candidate_dir.exists():
# # for side in ("LONG", "SHORT"):
# # update_file = candidate_dir / f"position_updates_{side.lower()}.json"
# # if update_file.exists():
# # json_ts = datetime.fromtimestamp(update_file.stat().st_mtime, tz=timezone.utc)
# # break
# # if json_ts:break
# # if redis_ts and json_ts and json_ts > redis_ts:
# # return json_bucket
# # else:
# # return json_bucket
# meta_source = raw_payload if isinstance(raw_payload, dict) else {}
# raw_positions = raw_payload
# if isinstance(raw_payload, dict):
# if "positions_by_account" in raw_payload:
# per_account = raw_payload.get("positions_by_account") or {}
# raw_positions = per_account.get(account_key, {}) if isinstance(per_account, dict) else {}
# elif "positions" in raw_payload:
# raw_positions = raw_payload.get("positions") or {}
# elif "data" in raw_payload and isinstance(raw_payload.get("data"), dict):
# raw_positions = raw_payload["data"]
# raw_positions = self._normalize_redis_payload(raw_positions)
# if not isinstance(raw_positions, dict):
# return {}
# now = datetime.now(timezone.utc)
# timestamp_candidate = None
# if isinstance(meta_source, dict):
# timestamp_candidate = meta_source.get("timestamp")
# if not timestamp_candidate:
# meta_section = meta_source.get("meta")
# if isinstance(meta_section, dict):
# timestamp_candidate = meta_section.get("positions_last_sync") or meta_section.get("timestamp")
# ts_dt = safe_datetime(timestamp_candidate)
# age = (now - ts_dt).total_seconds() if isinstance(ts_dt, datetime) else None
# bucket: Dict[str, Position] = {}
# metadata_keys = {"positions", "meta", "timestamp", "data", "positions_by_account"}
# for position_key, payload in raw_positions.items():
# if not isinstance(payload, dict):
# continue
# if not isinstance(position_key, str):
# continue
# if position_key in metadata_keys:
# continue
# if not position_key.startswith(f"{account_key}:"):
# continue
# try:
# pos_obj = Position.from_dict(payload)
# logger.info(f'redis payload pos {payload}')
# except Exception as exc:
# logger.debug(f"[redis_position_fallback] parse error for {position_key}: {exc}")
# continue
# bucket[position_key] = self.ensure_position_floats(pos_obj)
# if not bucket:
# return {}
# if age is not None and age > 60:
# logger.debug(f"[redis_position_fallback] Redis data for {account_key} is stale ({age:.2f}s old), falling back to files")
# return {}
# logger.debug(f"[redis_position_fallback] Loaded {len(bucket)} positions for {account_key} from Redis")
# return bucket
# # if data: return bucket
# # # 2. Try Disk JSON (L2 - Fallback)
# # if not data:
# # try:
# # base = config.BASE_PATH / account_key
# # long_file = base / "long_positions.json"
# # short_file = base / "short_positions.json"
# # disk_data = []
# # # Helper to safely load file
# # async def load_safe(path):
# # if await aio_os.path.exists(path):
# # async with aiofiles.open(path, "rb") as f:
# # content = await f.read()
# # if content: return orjson.loads(content) # type: ignore # pylint: disable=no-member,c-extension-no-member
# # return []
# # longs = await load_safe(long_file)
# # shorts = await load_safe(short_file)
# # # Merge lists
# # if isinstance(longs, list): disk_data.extend(longs)
# # elif isinstance(longs, dict): disk_data.extend(longs.values())
# # if isinstance(shorts, list): disk_data.extend(shorts)
# # elif isinstance(shorts, dict): disk_data.extend(shorts.values())
# # if disk_data:
# # data = disk_data
# # source = "DISK"
# # except Exception as e:
# # logger.info(f"[POS_CLIENT] Disk load failed: {e}")
# # pass
# # combined: Dict[str, Any] = {}
# # latest_ts: Optional[datetime] = None
# # # 1. Load Master Symbol List
# # try:
# # symbols_file = Path(getattr(self.config, "SYMBOLS_FILE", Path(self.config.BASE_PATH) / "symbols.json"))
# # if symbols_file.exists():
# # sym_content = await self._load_json(symbols_file)
# # if isinstance(sym_content, dict) and "symbols" in sym_content:
# # master_symbols = set(str(s).strip().upper() for s in sym_content["symbols"])
# # elif isinstance(sym_content, list):
# # master_symbols = set(str(s).strip().upper() for s in sym_content)
# # else:
# # master_symbols = set()
# # else:
# # master_symbols = set()
# # except Exception as e:
# # logger.error(f"[{account_key}] Failed to load symbols list: {e}")
# # master_symbols = set()
# # for side in ('LONG', 'SHORT'):
# # # A. Load Main File
# # file_path = self.get_position_file(account_key, side)
# # snapshot = await self._load_json(file_path)
# # if isinstance(snapshot, dict):
# # expected_prefix = f"{account_key}:"
# # filtered_snapshot = {}
# # for k, v in snapshot.items():
# # if isinstance(k, str) and k.startswith(expected_prefix):
# # try:
# # parsed_account, _, _ = parse_position_key(k)
# # if parsed_account == account_key:
# # filtered_snapshot[k] = v
# # except:
# # filtered_snapshot[k] = v
# # combined.update(filtered_snapshot)
# # # Update generic timestamp
# # try:
# # if await aio_os.path.exists(str(file_path)):
# # stat_result = await aio_os.stat(str(file_path))
# # ts = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc)
# # if not latest_ts or ts > latest_ts:
# # latest_ts = ts
# # except: pass
# # self._update_local_cache(account_key, combined)
# return bucket
# except Exception as e:
# logger.error(f"[POS_CLIENT] Sync Critical Error: {e}")
# await asyncio.sleep(1.0) # Check every second
# def _update_local_cache(self, account_key, data_list):
# """Converts raw JSON dicts into Position objects"""
# new_map = {}
# for p_data in data_list:
# # Convert dict to Position object (using existing Position class)
# pos = Position.from_dict(p_data)
# key = f"{account_key}:{pos.symbol}_{pos.position_side}"
# new_map[key] = pos
# # Atomic swap
# self.positions_by_account[account_key] = new_map
# def get_position(self, position_key):
# # account_key:symbol_side
# try:
# acc, _, _ = parse_position_key(position_key)
# return self.positions_by_account.get(acc, {}).get(position_key)
# except: return None
# # Dummy methods to satisfy TradeManager interface
# async def fetch_positions(self, account_key): pass # No-op (handled by sync loop)
# async def initialize(self): pass
# async def _ensure_hedge_engine_initialized(self): pass
@dataclass(slots=True)

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
    max_positionSize: float
    opened_at: Optional[datetime]
    last_updated: Optional[datetime]
    last_signal: str
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    was_reentered: bool = False
    was_reduced: bool = False
    prev_gain_last_updated: Optional[datetime] = None
    augment_reason: str = ""
    reduction_reason: str = ""
    mark_price_last_updated: Optional[datetime] = None
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        if not isinstance(data, dict): raise TypeError(f"Position.from_dict() requires a dict, got {type(data).__name__}")
        data = _convert_timestamp_strings(data)
        def safe_float(x):
            if x is None: return 0.0
            if isinstance(x, (int, float, Decimal)): return float(x)
            if isinstance(x, str):
                s = x.strip()
                if s == "": return 0.0
                s = s.replace(",", "")
                try: return float(Decimal(s))
                except: return 0.0
            if isinstance(x, dict):
                for k in ("amount", "qty", "positionAmt", "value"):
                    if k in x: return safe_float(x[k])
            try: return float(x)
            except (TypeError, ValueError): return 0.0
        def safe_time(x):
            if isinstance(x, datetime): return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x
            if isinstance(x, str):
                try: return isoparse(x) if isinstance(isoparse(x), datetime) else pd.to_datetime(x, utc=True).to_pydatetime()
                except (ValueError, TypeError):
                    try: return pd.to_datetime(x, utc=True).to_pydatetime()
                    except (ValueError, TypeError): return None
            return None

        return cls(
            symbol=str(data.get("symbol", "")),
            position_side=str(data.get("position_side", "")),
            entry_price=safe_float(data.get("entry_price")),
            mark_price=safe_float(data.get("mark_price")),
            positionAmt=safe_float(data.get("positionAmt")),
            initial_quantity=safe_float(data.get("initial_quantity")),
            gain=safe_float(data.get("gain")),
            max_gain=safe_float(data.get("max_gain")),
            prev_gain=safe_float(data.get("prev_gain")),
            max_quantity=safe_float(data.get("max_quantity")),
            last_augmentation_amount=safe_float(data.get("last_augmentation_amount")),
            last_augmentation_price=safe_float(data.get("last_augmentation_price")),
            last_augmentation_time=safe_time(data.get("last_augmentation_time")),
            last_reduction_amount=safe_float(data.get("last_reduction_amount")),
            last_reduction_price=safe_float(data.get("last_reduction_price")),
            last_reduction_time=safe_time(data.get("last_reduction_time")),
            max_positionSize=safe_float(data.get("max_positionSize")),
            opened_at=safe_time(data.get("opened_at")),
            last_updated=safe_time(data.get("last_updated")),
            last_signal=str(data.get("last_signal", "")),
            realized_pnl=safe_float(data.get("realized_pnl")),
            unrealized_pnl=safe_float(data.get("unrealized_pnl")),
            was_reentered=data.get("was_reentered", False),
            was_reduced=data.get("was_reduced", False),
            prev_gain_last_updated=safe_time(data.get("prev_gain_last_updated")),
            augment_reason=str(data.get("augment_reason", data.get("reason", ""))),
            reduction_reason=str(data.get("reduction_reason", data.get("reason", ""))),
            mark_price_last_updated=safe_time(data.get("mark_price_last_updated")), )
    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        for key, value in result.items():
            if isinstance(value, datetime): result[key] = value.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            elif value is None: result[key] = None
        return result

def _convert_timestamp_strings(data: Any) -> Any:
    """Recursively convert all timestamp strings in dict/list to datetime objects - CRITICAL for all JSON loads"""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(value, str) and any(ts_key in key.lower() for ts_key in ['time', 'timestamp', 'updated', 'at', 'date']):
                try:
                    if 'T' in value and ('Z' in value or '+' in value or value.count('-') >= 2):
                        parsed = isoparse(value)
                        result[key] = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
                    else:
                        result[key] = value
                except (ValueError, TypeError):
                    try:
                        parsed = pd.to_datetime(value, utc=True).to_pydatetime()
                        result[key] = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
                    except (ValueError, TypeError):
                        result[key] = value
            elif isinstance(value, (dict, list)):
                result[key] = _convert_timestamp_strings(value)
            else:
                result[key] = value
        return result
    elif isinstance(data, list):
        return [_convert_timestamp_strings(item) for item in data]
    return data

class DummyLock:
    async def __aenter__(self):
        pass
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    async def acquire(self): pass
    def release(self): pass
    def locked(self): return False

class AdvancedSignalRater:#SCALP
    @staticmethod
    async def rate(account_key, symbol, is_long, current_price, metrics, ind, prev_cross_price, is_exit, is_allowed, avg_entry=0.0, last_exit_timestamp=None, last_reduction_price=0.0, scalping_mode=False, scalping_override=False, tracker_data=None, tracker_manager=None):
        scalp_accounts = getattr(config, 'SCALP_ACCOUNTS', [])
        if isinstance(scalp_accounts, tuple): scalp_accounts = list(scalp_accounts)
        should_scalp = scalping_mode and account_key in scalp_accounts
        score = 0.0;i=ind
        reasons = []
        now = datetime.now(timezone.utc)
        now_ts = time.time()
        position_side='LONG' if is_long else 'SHORT'
        position_key = construct_position_key(account_key,symbol,position_side)
        position = await tracker_manager.get_position(position_key)
        if not position: position = tracker_manager.positions_service.positions.get(position_key)
        if not position: position = tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(position_key)
        positionAmt = 0.0
        gain = 0.0
        prev_gain = 0.0
        if position:
            gain = getattr(position, 'gain', 0.0)
            prev_gain = getattr(position, 'prev_gain', 0.0)
            positionAmt = getattr(position, 'positionAmt', 0.0)
        is_tradeable = ( position_key in tracker_manager.tradeable_keys or position_key in tracker_manager.tradeable_keys_cache or position_key in tracker_manager.tradeable_position_keys.get(account_key, set()) )
        if not is_tradeable and (not position or positionAmt <= 0): return 0, "NOT A TRADEABLE KEY", f'{position_key} NOT TRADEABLE'
        if not last_reduction_price: last_reduction_price = position.last_reduction_price if position else current_price
        if not is_allowed and not is_exit: return 0, "WAIT", "Not Allowed"
        if current_price <= 0: return 0, "WAIT", "No current_price"
        timestamp_str = i.get('timestamp_3m', '')
    
        k_1m, k_1m_prev, d_1m, k_3m, d_3m, k_15m, d_15m, k_1h, d_1h, k_4h, d_4h, k_D, d_D, k_3m_prev, k_15m_prev, k_1h_prev, k_4h_prev, k_D_prev, wt1_3m, wt2_3m, wt1_15m, wt2_15m, wt1_1h, wt2_1h, wt1_4h, wt2_4h, wt1_D, wt2_D, sma_200_1m, sma_200_1m_prev, sma_200_15m, sma_200_15m_prev, sma_200_1h, sma_200_1h_prev, sma_200_4h, sma_200_4h_prev, sma_200_D, sma_200_D_prev, dc_basis_3m, dc_basis_15m, dc_basis_1h, dc_basis_4h, dc_basis_D, dc_high_3m, dc_low_3m, dc_high_15m, dc_low_15m, dc_high_1h, dc_low_1h, dc_high_4h, dc_low_4h, dc_high_D, dc_low_D, dc_high_3m_ant, dc_low_3m_ant, dc_high_15m_ant, dc_low_15m_ant, dc_high_1h_ant, dc_low_1h_ant, dc_high_4h_ant, dc_low_4h_ant, dc_high_D_ant, dc_low_D_ant, dc_high4_3m, dc_low4_3m, high_3m, low_3m, high_3m_prev, low_3m_prev, high_15m, low_15m, high_1h, low_1h, high_1h_prev, low_1h_prev, high_4h, low_4h, high_4h_prev, low_4h_prev, high_D, low_D, ha_3m, ha_15m, ha_1h, ha_4h, ha_D, dc_basis_crossover_3m, dc_basis_crossunder_3m, dc_basis_crossover_15m, dc_basis_crossunder_15m, dc_basis_crossover_1h, dc_basis_crossunder_1h, dc_basis_crossover_4h, dc_basis_crossunder_4h, dc_basis_crossover_D, dc_basis_crossunder_D, dc_high_crossover_3m, dc_high_crossunder_3m, dc_high_crossover_15m, dc_high_crossunder_15m, dc_high_crossover_1h, dc_high_crossunder_1h, dc_high_crossover_4h, dc_high_crossunder_4h, dc_high_crossover_D, dc_high_crossunder_D, dc_low_crossover_3m, dc_low_crossunder_3m, dc_low_crossover_15m, dc_low_crossunder_15m, dc_low_crossover_1h, dc_low_crossunder_1h, dc_low_crossover_4h, dc_low_crossunder_4h, dc_low_crossover_D, dc_low_crossunder_D, stoch_crossover_3m, stoch_crossunder_3m, stoch_crossover_15m, stoch_crossunder_15m, stoch_crossover_1h, stoch_crossunder_1h, stoch_crossover_4h, stoch_crossunder_4h, stoch_crossover_D, stoch_crossunder_D, wt_signal_3m, wt_signal_15m, wt_signal_1h, wt_signal_4h, wt_signal_D, atr_3m, atr_3m_prev, atr_15m, atr_15m_prev, atr_1h, atr_1h_prev, atr_4h, atr_4h_prev, atr_D, atr_D_prev, rsi_3m, rsi_15m, rsi_1h, rsi_4h, rsi_D, mfi_3m, mfi_15m, mfi_1h, mfi_4h, mfi_D, ema_20_3m, ema_50_3m, ema_20_15m, ema_50_15m, ema_20_1h, ema_50_1h, ema_20_4h, ema_50_4h, t_up_3m, t_up_15m, timestamp, dc_basis_15m_ant = i.get('stoch_k_1m',50), i.get('k_1m_prev', 50), i.get('stoch_d_1m', 50), i.get('stoch_k_3m', 50), i.get('stoch_d_3m', 50), i.get('stoch_k_15m', 50), i.get('stoch_d_15m', 0), i.get('stoch_k_1h', 50), i.get('stoch_d_1h', 50), i.get('stoch_k_4h', 50), i.get('stoch_d_4h', 50), i.get('stoch_k_D', 50), i.get('stoch_d_D', 50), i.get('k_3m_prev', 50), i.get('stoch_k_15m_prev', 50), i.get('stoch_k_1h_prev', 50), i.get('stoch_k_4h_prev', 50), i.get('stoch_k_D_prev', 50), i.get('wt1_3m', 0), i.get('wt2_3m', 0), i.get('wt1_15m', 0), i.get('wt2_15m', 0), i.get('wt1_1h', 0), i.get('wt2_1h', 0), i.get('wt1_4h', 0), i.get('wt2_4h', 0), i.get('wt1_D', 0), i.get('wt2_D', 0), i.get('sma_200_1m', 0), i.get('sma_200_1m_prev', 0), i.get('sma_200_15m', 0), i.get('sma_200_15m_prev', 0), i.get('sma_200_1h', 0), i.get('sma_200_1h_prev', 0), i.get('sma_200_4h', 0), i.get('sma_200_4h_prev', 0), i.get('sma_200_D', 0), i.get('sma_200_D_prev', 0), i.get('dc_basis_3m', 0), i.get('dc_basis_15m', 0), i.get('dc_basis_1h', 0), i.get('dc_basis_4h', 0), i.get('dc_basis_D', 0), i.get('dc_high_3m', 0), i.get('dc_low_3m', 0), i.get('dc_high_15m', 0), i.get('dc_low_15m', 0), i.get('dc_high_1h', 0), i.get('dc_low_1h', 0), i.get('dc_high_4h', 0), i.get('dc_low_4h', 0), i.get('dc_high_D', 0), i.get('dc_low_D', 0), i.get('dc_high_3m_ant', 0), i.get('dc_low_3m_ant', 0), i.get('dc_high_15m_ant', 0), i.get('dc_low_15m_ant', 0), i.get('dc_high_1h_ant', 0), i.get('dc_low_1h_ant', 0), i.get('dc_high_4h_ant', 0), i.get('dc_low_4h_ant', 0), i.get('dc_high_D_ant', 0), i.get('dc_low_D_ant', 0), i.get('dc_high4_3m', 0), i.get('dc_low4_3m', 0), i.get('high_3m', 0), i.get('low_3m', 0), i.get('high_3m_prev', 0), i.get('low_3m_prev', 0), i.get('high_15m', 0), i.get('low_15m', 0), i.get('high_1h', 0), i.get('low_1h', 0), i.get('high_1h_prev', 0), i.get('low_1h_prev', 0), i.get('high_4h', 0), i.get('low_4h', 0), i.get('high_4h_prev', 0), i.get('low_4h_prev', 0), i.get('high_D', 0), i.get('low_D', 0), i.get('ha_3m', 'neutral'), i.get('ha_15m', 'neutral'), i.get('ha_1h', 'neutral'), i.get('ha_4h', 'neutral'), i.get('ha_D', 'neutral'), i.get('dc_basis_crossover_3m', False), i.get('dc_basis_crossunder_3m', False), i.get('dc_basis_crossover_15m', False), i.get('dc_basis_crossunder_15m', False), i.get('dc_basis_crossover_1h', False), i.get('dc_basis_crossunder_1h', False), i.get('dc_basis_crossover_4h', False), i.get('dc_basis_crossunder_4h', False), i.get('dc_basis_crossover_D', False), i.get('dc_basis_crossunder_D', False), i.get('dc_high_crossover_3m', False), i.get('dc_high_crossunder_3m', False), i.get('dc_high_crossover_15m', False), i.get('dc_high_crossunder_15m', False), i.get('dc_high_crossover_1h', False), i.get('dc_high_crossunder_1h', False), i.get('dc_high_crossover_4h', False), i.get('dc_high_crossunder_4h', False), i.get('dc_high_crossover_D', False), i.get('dc_high_crossunder_D', False), i.get('dc_low_crossover_3m', False), i.get('dc_low_crossunder_3m', False), i.get('dc_low_crossover_15m', False), i.get('dc_low_crossunder_15m', False), i.get('dc_low_crossover_1h', False), i.get('dc_low_crossunder_1h', False), i.get('dc_low_crossover_4h', False), i.get('dc_low_crossunder_4h', False), i.get('dc_low_crossover_D', False), i.get('dc_low_crossunder_D', False), i.get('stoch_crossover_3m', False), i.get('stoch_crossunder_3m', False), i.get('stoch_crossover_15m', False), i.get('stoch_crossunder_15m', False), i.get('stoch_crossover_1h', False), i.get('stoch_crossunder_1h', False), i.get('stoch_crossover_4h', False), i.get('stoch_crossunder_4h', False), i.get('stoch_crossover_D', False), i.get('stoch_crossunder_D', False), i.get('wt_signal_3m', 'NEUTRAL'), i.get('wt_signal_15m', 'NEUTRAL'), i.get('wt_signal_1h', 'NEUTRAL'), i.get('wt_signal_4h', 'NEUTRAL'), i.get('wt_signal_D', 'NEUTRAL'), i.get('atr_3m', 0), i.get('atr_3m_prev', 0), i.get('atr_15m', 0), i.get('atr_15m_prev', 0), i.get('atr_1h', 0), i.get('atr_1h_prev', 0), i.get('atr_4h', 0), i.get('atr_4h_prev', 0), i.get('atr_D', 0), i.get('atr_D_prev', 0), i.get('rsi_3m', 50), i.get('rsi_15m', 50), i.get('rsi_1h', 50), i.get('rsi_4h', 50), i.get('rsi_D', 50), i.get('mfi_3m', 50), i.get('mfi_15m', 50), i.get('mfi_1h', 50), i.get('mfi_4h', 50), i.get('mfi_D', 50), i.get('ema_20_3m', 0), i.get('ema_50_3m', 0), i.get('ema_20_15m', 0), i.get('ema_50_15m', 0), i.get('ema_20_1h', 0), i.get('ema_50_1h', 0), i.get('ema_20_4h', 0), i.get('ema_50_4h', 0), i.get('t_up_3m', False), i.get('t_up_15m', True), i.get('timestamp',0), i.get('dc_basis_15m_ant', 0);timestamp = datetime.fromisoformat(str(timestamp_str).replace('Z', '+00:00')) if isinstance(timestamp_str, str) and timestamp_str else None
        dc_high4_1m = safe_fetch_float(ind.get('dc_high4_1m'), 0.0)
        dc_low4_1m = safe_fetch_float(ind.get('dc_low4_1m'), 0.0)
        wt_score_15m = safe_fetch_float(ind.get('wt_score_15m'), 0.0)
        wt_score_1h = safe_fetch_float(ind.get('wt_score_1h'), 0.0)
        rel_vol = safe_fetch_float(ind.get('relative_volume_1h'), 1.0)
        lr_slope = safe_fetch_float(ind.get('lr_trend_15m'), 0.0)
        sco1h = ind.get('stoch_crossover_1h', False); sco15m = ind.get('stoch_crossover_15m', False)
        scu1h = ind.get('stoch_crossunder_1h', False); scu15m = ind.get('stoch_crossunder_15m', False)
        top_sent = (ind.get('0is_top_sentiment'), False); bot_sent = (ind.get('0is_bottom_sentiment'), False)
        sentc = str(ind.get('0sentiment_classification') or "")
        local_sent = safe_fetch_float(ind.get('0market_sentiment_local'), 0.0)
        _le = _last_events_cache.get(symbol, {}) if _last_events_cache else {}
        _3m = _le.get('3m', {}).get('stoch_crossover', {}); _15m = _le.get('15m', {}).get('stoch_crossover', {}); _1h = _le.get('1h', {}).get('stoch_crossover', {}); _4h = _le.get('4h', {}).get('stoch_crossover', {}); _D = _le.get('D', {}).get('stoch_crossover', {})
        _3mu = _le.get('3m', {}).get('stoch_crossunder', {}); _15mu = _le.get('15m', {}).get('stoch_crossunder', {}); _1hu = _le.get('1h', {}).get('stoch_crossunder', {}); _4hu = _le.get('4h', {}).get('stoch_crossunder', {}); _Du = _le.get('D', {}).get('stoch_crossunder', {})
        crossover_price_3m, crossover_price_15m, crossover_price_1h, crossover_price_4h, crossover_price_D, crossunder_price_3m, crossunder_price_15m, crossunder_price_1h, crossunder_price_4h, crossunder_price_D, crossover_price_previous_3m, crossover_price_previous_15m, crossover_price_previous_1h, crossover_price_previous_4h, crossover_price_previous_D, crossunder_price_previous_3m, crossunder_price_previous_15m, crossunder_price_previous_1h, crossunder_price_previous_4h, crossunder_price_previous_D = float(_3m.get('latest', {}).get('price', 0.0) or 0.0), float(_15m.get('latest', {}).get('price', 0.0) or 0.0), float(_1h.get('latest', {}).get('price', 0.0) or 0.0), float(_4h.get('latest', {}).get('price', 0.0) or 0.0), float(_D.get('latest', {}).get('price', 0.0) or 0.0), float(_3mu.get('latest', {}).get('price', 0.0) or 0.0), float(_15mu.get('latest', {}).get('price', 0.0) or 0.0), float(_1hu.get('latest', {}).get('price', 0.0) or 0.0), float(_4hu.get('latest', {}).get('price', 0.0) or 0.0), float(_Du.get('latest', {}).get('price', 0.0) or 0.0), float(_3m.get('previous', {}).get('price', 0.0) or 0.0), float(_15m.get('previous', {}).get('price', 0.0) or 0.0), float(_1h.get('previous', {}).get('price', 0.0) or 0.0), float(_4h.get('previous', {}).get('price', 0.0) or 0.0), float(_D.get('previous', {}).get('price', 0.0) or 0.0), float(_3mu.get('previous', {}).get('price', 0.0) or 0.0), float(_15mu.get('previous', {}).get('price', 0.0) or 0.0), float(_1hu.get('previous', {}).get('price', 0.0) or 0.0), float(_4hu.get('previous', {}).get('price', 0.0) or 0.0), float(_Du.get('previous', {}).get('price', 0.0) or 0.0)
        exact_tick_ts= metrics.get('_tick_ts')
        true_lag = now_ts - exact_tick_ts
        if true_lag > 10.0: should_scalp = False
        k_1mco= (k_1m > d_1m and true_lag < 10.0)and true_lag < 10.0 and k_1m_prev <= d_1m and k_1m < 30
        k_1mcu = k_1m < d_1m and true_lag < 10.0 and k_1m_prev >= d_1m and k_1m > 70
        k_3mco= k_3m > d_3m and k_3m_prev <= d_3m and k_3m < 30
        k_3mcu= k_3m < d_3m and k_3m_prev >= d_3m and k_3m > 70
        k_15mco= k_15m > d_15m and k_15m_prev <= d_15m and k_15m < 30
        k_15mcu= k_15m < d_15m and k_15m_prev >= d_15m and k_15m > 70
        c1_long = k_1m > d_1m;c1_short = k_1m < d_1m; c3_long = k_3m > d_3m; c3_short = k_3m < d_3m
        dc_w1h=((dc_high_1h-dc_low_1h)/dc_low_1h)*100 if dc_low_1h>0 else 0;dc_w4h=((dc_high_4h-dc_low_4h)/dc_low_4h)*100 if dc_low_4h>0 else 0
        if is_long:
            if dc_w1h>5.0 and current_price<(dc_high_1h*0.97) and current_price>dc_basis_1h: score+=25; reasons.append("JUMP_PULLBACK")
            if k_15m<20: score+=10; reasons.append("LOW_K15")
            if k_1h<20: score+=15; reasons.append("LOW_K1H")
            if k_4h<25: score+=20; reasons.append("LOW_K4H")
            if k_D<30: score+=25; reasons.append("LOW_KD")
            if dc_basis_crossover_3m: score+=30; reasons.append("DCB_CO_3M")
            if dc_basis_crossover_15m: score+=25; reasons.append("DCB_CO_15M")
            if dc_basis_crossover_1h: score+=20; reasons.append("DCB_CO_1H")
            if dc_basis_crossover_4h: score+=15; reasons.append("DCB_CO_4H")
            if dc_basis_crossover_D: score+=10; reasons.append("DCB_CO_D")
            if dc_basis_crossunder_3m: score-=30; reasons.append("DCB_CU_3M_BAD")
            if dc_basis_crossunder_15m: score-=25; reasons.append("DCB_CU_15M_BAD")
            if dc_basis_crossunder_1h: score-=20; reasons.append("DCB_CU_1H_BAD")
            if current_price>ema_50_15m: score+=10; reasons.append("AB_E50_15M")
            else: score-=15; reasons.append("BE_E50_15M_BAD")
            if current_price>sma_200_15m: score+=10; reasons.append("AB_S200_15M")
            else: score-=15; reasons.append("BE_S200_15M_BAD")
            if current_price>sma_200_1h: score+=10; reasons.append("AB_S200_1H")
            else: score-=15; reasons.append("BE_S200_1H_BAD")
        else:
            if dc_w1h>5.0 and current_price>(dc_low_1h*1.03) and current_price<dc_basis_1h: score+=25; reasons.append("DROP_RECOVERY")
            if k_15m>80: score+=10; reasons.append("HIGH_K15")
            if k_1h>80: score+=15; reasons.append("HIGH_K1H")
            if k_4h>75: score+=20; reasons.append("HIGH_K4H")
            if k_D>70: score+=25; reasons.append("HIGH_KD")
            if dc_basis_crossunder_3m: score+=30; reasons.append("DCB_CU_3M")
            if dc_basis_crossunder_15m: score+=25; reasons.append("DCB_CU_15M")
            if dc_basis_crossunder_1h: score+=20; reasons.append("DCB_CU_1H")
            if dc_basis_crossunder_4h: score+=15; reasons.append("DCB_CU_4H")
            if dc_basis_crossunder_D: score+=10; reasons.append("DCB_CU_D")
            if dc_basis_crossover_3m: score-=30; reasons.append("DCB_CO_3M_BAD")
            if dc_basis_crossover_15m: score-=25; reasons.append("DCB_CO_15M_BAD")
            if dc_basis_crossover_1h: score-=20; reasons.append("DCB_CO_1H_BAD")
            if current_price<ema_50_15m: score+=10; reasons.append("BE_E50_15M")
            else: score-=15; reasons.append("AB_E50_15M_BAD")
            if current_price<sma_200_15m: score+=10; reasons.append("BE_S200_15M")
            else: score-=15; reasons.append("AB_S200_15M_BAD")
            if current_price<sma_200_1h: score+=10; reasons.append("BE_S200_1H")
            else: score-=15; reasons.append("AB_S200_1H_BAD")
        if (is_long and not is_exit and not c1_long and not c3_long) or (not is_long and not is_exit and not c1_short and not c3_short) or (is_long and is_exit and not c1_short) or (not is_long and is_exit and not c1_long) : return score, 'WAIT', 'SHIT IDEA'
        if (is_long and not is_exit and not c3_long) or (not is_long and not is_exit and not c3_short) : score -= 2
        if (is_long and is_exit and not c3_short) or (not is_long and is_exit and not c3_long) : score += 2
        if (is_long and not is_exit and c1_long and c3_long) or (not is_long and not is_exit and c1_short and c3_short) : score += 3
        elif (is_long and not is_exit and c3_long) or (not is_long and not is_exit and c3_short) : score += 1
        if not is_exit and ((k_3m==50 and d_3m==50) or (k_15m==50 and d_15m == 50)) :
            logger.error(f"[rate] {position_key}: ❌ BLOCKED - UNRELIABLE INDICATORS")
            return 0, "WAIT", "UNRELIABLE_INDICATORS"
        pos_last_aug_time = getattr(position, 'last_augmentation_time', None) if position else None
        if not pos_last_aug_time and tracker_data:
            pos_last_aug_time = tracker_data.get('last_entry_time')
        min_since_aug = minutes_since(pos_last_aug_time)
        pos_last_red_time = getattr(position, 'last_reduction_time', None) if position else None
        if not pos_last_red_time and tracker_data:
            pos_last_red_time = tracker_data.get('last_exit_timestamp') or tracker_data.get('last_reduction_time')
        min_since_red = minutes_since(pos_last_red_time)
        actual_last_red_price = getattr(position, 'last_reduction_price', 0.0) if position else 0.0
        if actual_last_red_price <= 0 and tracker_data:
            actual_last_red_price = safe_fetch_float(tracker_data.get('last_reduction_price', 0.0))
        if actual_last_red_price <= 0 and last_reduction_price > 0:
            actual_last_red_price = last_reduction_price
        global_sent = safe_fetch_float(ind.get('0market_sentiment_score'), 0.0)
        time_since_exit = None
        if last_exit_timestamp:
            exit_dt = safe_datetime(last_exit_timestamp)
            if exit_dt: time_since_exit = now - exit_dt
        pnl_pct = 0.0
        if avg_entry > 0:
            if is_long: pnl_pct = ((current_price - avg_entry) / avg_entry) * 100
            else: pnl_pct = ((avg_entry - current_price) / avg_entry) * 100
        if not prev_gain:
            prev_gain = 0.0
            if tracker_data:
                prev_gain = safe_fetch_float(tracker_data.get('prev_gain'), pnl_pct)
        if pnl_pct == 0.0: prev_gain=0.0
        aug_ts = safe_datetime(position.last_augmentation_time)
        open_min = 0.0
        if aug_ts:
            if aug_ts.tzinfo is None: aug_ts = aug_ts.replace(tzinfo=timezone.utc)
            # Use total_seconds() to get a float, then divide
            open_min = (now - aug_ts).total_seconds() / 60.0
        k_str = f"k_1m:{int(sf(k_1m))}/d_1m:{int(sf(d_1m))}/k_3m:{int(sf(k_3m))}/d_3m:{int(sf(d_3m))}/k_15m:{int(sf(k_15m))}/d_15m:{int(sf(d_15m))}/k_1h:{int(sf(k_1h))}/d_1h:{int(sf(d_1h))}/k_4h:{int(sf(k_4h))}/d_4h:{int(sf(d_4h))} sent {sentc}"
        is_data_blind = (k_3m == 50.0 and d_3m == 50.0) or (k_1m == 50.0 and d_1m == 50.0)
        if is_data_blind:
            if gain is not None and prev_gain is not None and gain < prev_gain: # pylint: disable=used-before-assignment,possibly-used-before-assignment
                pass # Allow falling through to scalp decay logic
            else:
                return 0, "WAIT", "DATA_BLIND_5050"
        # # -------------------------
        higher_high_15m = bool(i.get('high_15m', 0) and i.get('high_15m_prev', 0) and i.get('high_15m', 0) >= i.get('high_15m_prev', 0)) #TEMP OUT
        lower_low_15m = bool(i.get('low_15m', 0) and i.get('low_15m_prev', 0) and i.get('low_15m', 0) <= i.get('low_15m_prev', 0))
        if ((is_long and current_price > last_reduction_price) or (not is_long and current_price < last_reduction_price)) and min_since_red < 35: score += 8
        if (is_long and k_1mco) or (not is_long and k_1mcu) and true_lag < 10.0 :
            score += 1
            if (is_long and current_price > crossover_price_3m) or (not is_long and current_price < crossunder_price_3m):
                score += 2
                if (is_long and crossover_price_3m > crossover_price_previous_3m) or (not is_long and crossunder_price_3m < crossunder_price_previous_3m): score += 3
            if min_since_red < 25: score += 3
        if ((is_long and k_1mcu) or (not is_long and k_1mco)) and true_lag < 10.0 :
            score -= 1
            if (is_long and current_price < crossover_price_3m) or (not is_long and current_price > crossunder_price_3m):
                score -= 1
                if (is_long and crossover_price_3m < crossover_price_previous_3m) or (not is_long and crossunder_price_3m > crossunder_price_previous_3m): score -= 4
            if gain < 0.1: score -= 5
        if (is_long and k_3mco) or (not is_long and k_3mcu):
            score += 4
            if (is_long and current_price > crossover_price_3m) or (not is_long and current_price < crossunder_price_3m):
                score += 2
                if (is_long and crossover_price_3m > crossover_price_previous_3m) or (not is_long and crossunder_price_3m < crossunder_price_previous_3m): score += 2
            if gain < 0.1: score -= 8
            if min_since_red < 35: score += 3
        if (is_long and k_3mcu) or (not is_long and k_3mco):
            score -= 3
            if (is_long and current_price < crossover_price_3m) or (not is_long and current_price > crossunder_price_3m):
                score -= 2
                if (is_long and crossover_price_3m < crossover_price_previous_3m) or (not is_long and crossunder_price_3m > crossunder_price_previous_3m): score -= 3
            if gain < 0.1: score -= 7
        if (is_long and k_15mco) or (not is_long and k_15mcu):
            score += 7
            if (is_long and current_price > crossover_price_15m) or (not is_long and current_price < crossunder_price_15m):
                score += 5
                if (is_long and crossover_price_15m > crossover_price_previous_15m) or (not is_long and crossunder_price_15m < crossunder_price_previous_15m): score += 3
            if gain < 0.1: score -= 9
        if (is_long and k_15mcu) or (not is_long and k_15mco):
            score -= 9
            if (is_long and current_price < crossover_price_15m) or (not is_long and current_price > crossunder_price_15m):
                score -= 4
                if (is_long and crossover_price_15m < crossover_price_previous_15m) or (not is_long and crossunder_price_15m > crossunder_price_previous_15m): score -= 4
            if gain < 0.1: score -= 19
        trend_is_bullish = (k_15m > d_15m) or (k_1h > d_1h) or (current_price > ema_50_15m)
        trend_is_bearish = (k_15m < d_15m) or (k_1h < d_1h) or (current_price < ema_50_15m)
        force_reentry = False
        if not is_exit and actual_last_red_price > 0 and min_since_red < 360:
            if is_long and current_price > actual_last_red_price:
                if k_1m > k_1m_prev and k_3m > k_3m_prev:
                    force_reentry = True
                    reasons.append(f"RECLAIM_LEVEL_{actual_last_red_price}")
            elif not is_long and current_price < actual_last_red_price:
                if k_1m < k_1m_prev and k_3m < k_3m_prev:
                    force_reentry = True
                    reasons.append(f"RECLAIM_LEVEL_{actual_last_red_price}")
        # 2. ENTRY SCORING (More Picky)
        if not is_exit:
            if is_long:
                good_entry = k_3m < 20 or (((k_1m < 10 and k_1m > k_1m_prev) or k_1mco) and true_lag < 10.0) or k_3mco
                if not good_entry: return 0, "WAIT", "NOT_GOOD_ENTRY_MOMENT"
            else:
                good_entry = k_3m > 80 or (((k_1m > 90 and k_1m < k_1m_prev) or k_1mcu) and true_lag < 10.0) or k_3mcu
                if not good_entry: return 0, "WAIT", "NOT_GOOD_ENTRY_MOMENT"
            # LONG ENTRY
            if is_long:
                if not trend_is_bullish:
                    score -= 10 # Don't catch falling knives unless deep oversold
                # Primary Trigger: 1m cross up, BUT 3m must not be topped out
                if (k_1mco and k_3m < 80 and true_lag < 10.0) or k_3mco and k_15m > 80 :
                    score += 5
                    if k_15m < 50: score += 3 # Buying the dip in an uptrend
                # Continuation Trigger: 15m crossing up
                if k_15mco: score += 4
                dc_width_15m = ((dc_high_15m - dc_low_15m) / dc_low_15m) * 100 if dc_low_15m > 0 else 0
                near_sma = abs(current_price - sma_200_15m) / sma_200_15m < 0.015 if sma_200_15m > 0 else False
                k_15m_reset = k_15m < 30
                if dc_width_15m > 5.0 and near_sma and k_15m_reset and k_3mco:
                    score += 25 # Massive score override
                    reasons.append("BRING_OUT_THE_ARMY")
                # --- 2. FRESH BREAKOUT (Buy The Top) ---
                # Condition: Breaking DC High, HTF is strongly supporting, Stoch is high but pushing UP.
                elif current_price > dc_high_15m and k_1h > d_1h and k_15m > k_15m_prev:
                    score += 10
                    reasons.append("BREAKOUT_PLAY_TIGHT_STOP")
                elif k_15m > 90 and k_15m < k_15m_prev:
                    score -= 5
            # SHORT ENTRY
            else:
                if not trend_is_bearish:
                    score -= 10
                if (k_1mcu and k_3m > 20 and true_lag < 10.0) or k_3mcu and k_15m > 20 :
                    score += 5
                    if k_15m > 50: score += 3
                if k_15mcu: score += 4
                dc_width_15m = ((dc_high_15m - dc_low_15m) / dc_low_15m) * 100 if dc_low_15m > 0 else 0
                near_sma = abs(current_price - sma_200_15m) / sma_200_15m < 0.015 if sma_200_15m > 0 else False
                k_15m_reset = k_15m > 70
                if dc_width_15m > 5.0 and near_sma and k_15m_reset and k_3mcu:
                    score += 25 # Massive score override
                    reasons.append("BRING_OUT_THE_ARMY")
                # --- 2. FRESH BREAKOUT (Buy The Top) ---
                # Condition: Breaking DC High, HTF is strongly supporting, Stoch is high but pushing UP.
                elif current_price < dc_low_15m and k_1h < d_1h and k_15m < k_15m_prev:
                    score += 10
                    reasons.append("BREAKOUT_PLAY_TIGHT_STOP")
                if k_15m < 10 or current_price < dc_low_15m: score -= 5
# Calculate Volatility Expansion
        dc_expansion_pct = 0.0
        if dc_low_15m > 0:
            dc_expansion_pct = ((dc_high_15m - dc_low_15m) / dc_low_15m) * 100.0
        is_massive_expansion = dc_expansion_pct > 4.5
        # =====================================================================
        # 2. ENTRY SCORING
        # =====================================================================
        if not is_exit:
            # --- TIERED DC BREAKOUT SCALP (SNAP-REENTRY) ---
            in_dc_breakout_long = dc_high_3m > 0 and current_price >= dc_high_3m
            in_dc_breakout_short = dc_low_3m > 0 and current_price <= dc_low_3m
            if is_long and in_dc_breakout_long:
                if k_3m > k_3m_prev or (k_1m > d_1m and true_lag < 10.0):
                    score += 25.0
                    reasons.append("DC_MOMENTUM_SCALP_REENTRY_LONG")
            elif not is_long and in_dc_breakout_short:
                if k_3m < k_3m_prev or (k_1m < d_1m and true_lag < 10.0):
                    score += 25.0
                    reasons.append("DC_MOMENTUM_SCALP_REENTRY_SHORT")
            # --- B.O.T.A. (BRING OUT THE ARMY) DETECTOR ---
            if is_massive_expansion and "DC_MOMENTUM_SCALP" not in str(reasons):
                if is_long:
                    near_sma = current_price <= (ema_50_15m * 1.03) or current_price <= (sma_200_15m * 1.03)
                    stoch_15m_reset = k_15m < 35
                    micro_turn = k_1m > d_1m and k_1m > k_1m_prev and k_3m > d_3m
                    if near_sma and stoch_15m_reset and micro_turn:
                        score += 30.0
                        reasons.append("BOTA_PULLBACK_REENTRY_LONG")
                else:
                    near_sma = current_price >= (ema_50_15m * 0.97) or current_price >= (sma_200_15m * 0.97)
                    stoch_15m_reset = k_15m > 65
                    micro_turn = k_1m < d_1m and k_1m < k_1m_prev and k_3m < d_3m
                    if near_sma and stoch_15m_reset and micro_turn:
                        score += 30.0
                        reasons.append("BOTA_PULLBACK_REENTRY_SHORT")
            # --- DECENT GAIN GUARD (Augment at 3%, Protect at 2%) ---
            if 2.8 < pnl_pct < 3.5:
                # Bullish setup for 3% push
                is_bullish = (is_long and k_1m > d_1m and k_3m > d_3m) or (not is_long and k_1m < d_1m and k_3m < d_3m)
                if is_bullish:
                    score += 15.0
                    reasons.append("DECENT_GAIN_PUSH_3PCT")
            elif 1.8 < pnl_pct < 2.3:
                # Bearish setup for 2% protection
                is_reverting = (is_long and (k_1m < d_1m or k_3m < d_3m)) or (not is_long and (k_1m > d_1m or k_3m > d_3m))
                if is_reverting:
                    return -10.0, "SCALP_REDUCE", f"DECENT_GAIN_PROTECT_2PCT_{pnl_pct:.2f}%" # Standard Entry
            if "BOTA" not in str(reasons) and "DC_MOMENTUM_SCALP" not in str(reasons):
                if is_long:
                    good_entry = k_3m < 25 or (((k_1m < 15 and k_1m > k_1m_prev) or k_1mco) and true_lag < 10.0) or k_3mco
                    if not good_entry: return 0, "WAIT", "NOT_GOOD_ENTRY_MOMENT"
                    if not trend_is_bullish: score -= 10
                    if (k_1mco and k_3m < 80 and true_lag < 10.0) or (k_3mco and k_15m > 80):
                        score += 5
                        if k_15m < 50: score += 5
                    if k_15mco: score += 4
                else:
                    good_entry = k_3m > 75 or (((k_1m > 85 and k_1m < k_1m_prev) or k_1mcu) and true_lag < 10.0) or k_3mcu
                    if not good_entry: return 0, "WAIT", "NOT_GOOD_ENTRY_MOMENT"
                    if not trend_is_bearish: score -= 10
                    if (k_1mcu and k_3m > 20 and true_lag < 10.0) or (k_3mcu and k_15m < 20):
                        score += 5
                        if k_15m > 50: score += 5
                    if k_15mcu: score += 4
        # =====================================================================
        # 3. EXIT SCORING (OVERHAULED FOR POSITIVE CLOSURE & HEDGE COHERENCE)
        # =====================================================================
        else:
            time_in_trade = (now - position.opened_at).total_seconds() / 60.0 if position.opened_at else 999
            # --- HEDGE COHERENCE (MANDATORY) ---
            # If in HEDGE_MODE and losing more than 0.15%, we BLOCK the scalp exit
            # This allows the loss-management system to trigger a hedge instead of just stopping out.
            if is_hedge_account(config, account_key) and pnl_pct < -0.15:
                logger.info(f"🛡️ [SCALP_HEDGE_HANDOFF] {position_key}: Blocking Scalp Exit ({pnl_pct:.2f}%) to allow Hedge Engine.")
                return 0, "WAIT", "HEDGE_ENGINE_HANDOFF"
            # --- HIGH-PRIORITY PROFIT RESCUE ---
            # If we are in positive territory, we are much more aggressive about exiting on momentum exhaustion.
            in_profit = pnl_pct > 0.05
            # --- DC MOMENTUM SCALP CUT (Hair Trigger) ---
            if is_long and current_price >= dc_high_3m:
                if k_3m < k_3m_prev or (k_1m < d_1m and true_lag < 10.0):
                    score -= 30.0
                    reasons.append("DC_MOMENTUM_SCALP_CUT_LONG")
            elif not is_long and current_price <= dc_low_3m:
                if k_3m > k_3m_prev or (k_1m > d_1m and true_lag < 10.0):
                    score -= 30.0
                    reasons.append("DC_MOMENTUM_SCALP_CUT_SHORT")
            # --- MOMENTUM EXHAUSTION (PROFIT PROTECTOR) ---
            # If in profit, we exit at the first sign of 1m exhaustion or 15m trend-flip
            if in_profit:
                if is_long:
                    exhaustion = (k_1m < k_1m_prev and k_1m > 70) or (k_1m < d_1m and true_lag < 15.0) or (ha_3m == 'red')
                    trend_flip = (k_15m < k_15m_prev and k_15m > 80)
                    if exhaustion or trend_flip:
                        return -10.0, "SCALP_REDUCE", f"PROFIT_RESCUE_L_{pnl_pct:.2f}%"
                else:
                    exhaustion = (k_1m > k_1m_prev and k_1m < 30) or (k_1m > d_1m and true_lag < 15.0) or (ha_3m == 'green')
                    trend_flip = (k_15m > k_15m_prev and k_15m < 20)
                    if exhaustion or trend_flip:
                        return -10.0, "SCALP_REDUCE", f"PROFIT_RESCUE_S_{pnl_pct:.2f}%"
            # --- TIME-BASED TIGHT LEASH ---
            # If scalp is < 10 mins old and pnl is slightly positive, don't let it go red
            if time_in_trade < 10 and 0.01 < pnl_pct < 0.15:
                if is_long and (k_1m < k_1m_prev or current_price < dc_basis_3m):
                    return -10.0, "SCALP_REDUCE", "TIGHT_LEASH_PROFIT_SAVE"
                elif not is_long and (k_1m > k_1m_prev or current_price > dc_basis_3m):
                    return -10.0, "SCALP_REDUCE", "TIGHT_LEASH_PROFIT_SAVE"
            if is_long:
                if avg_entry > (dc_high_15m * 0.985):
                    if k_1m < d_1m and k_1m < k_1m_prev and k_3m < 75:
                        score -= 20.0
                        reasons.append("TIGHT_BREAKOUT_STOP_LONG")
                if k_15m < d_15m and k_1h < d_1h: score -= 5
                if (k_1m < d_1m and true_lag < 10.0) or k_3m < d_3m:
                    if pnl_pct > 0.3 or current_price >= dc_high_15m: score -= 3
                    elif pnl_pct < -0.5 and k_3m < d_3m: score -= 2
            else:
                if avg_entry < (dc_low_15m * 1.015):
                    if k_1m > d_1m and k_1m > k_1m_prev and k_3m > 25:
                        score -= 20.0
                        reasons.append("TIGHT_BREAKOUT_STOP_SHORT")
                if k_15m > d_15m and k_1h > d_1h: score -= 5
                if (k_1m > d_1m and true_lag < 10.0) or k_3m > d_3m:
                    if pnl_pct > 0.3 or current_price <= dc_low_15m: score -= 3
                    elif pnl_pct < -0.5 and k_3m > d_3m: score -= 2
            # CHURN PROTECTION
            if time_in_trade < 5 and -1.0 < pnl_pct < 0.1 and "TIGHT" not in str(reasons) and "SCALP_CUT" not in str(reasons):
                score += 10
                reasons.append("breathing_room")
        if should_scalp and is_exit:
            if is_long:
                scalp_condition_standard = k_1m < k_1m_prev and (k_1m > 85 or (k_1m < d_1m and k_1m_prev > d_1m))
                scalp_condition_momentum = k_1m < k_1m_prev
                scalp_condition_decay = (pnl_pct < prev_gain and pnl_pct < 0.12) or (pnl_pct < 0.05 and k_1m < k_1m_prev)
                if (scalp_condition_standard or scalp_condition_momentum or scalp_condition_decay or pnl_pct < prev_gain or k_15m < k_15m_prev or lower_low_15m) and (min_since_aug > 4 or current_price <= dc_low4_3m):
                    score -= 10.0 # Force Exit
                    if scalp_condition_decay: scalp_reason = f"SCALP_EXIT_DECAY_g{pnl_pct:.2f}%"
                    elif scalp_condition_momentum: scalp_reason = f"SCALP_EXIT_MOMENTUM_k{k_1m:.0f}<{k_1m_prev:.0f}"
                    else: scalp_reason = "SCALP_EXIT_CROSSOVER"
                    return score, "SCALP_REDUCE", scalp_reason
            else: # Short
                scalp_condition_standard = k_1m > k_1m_prev and (k_1m < 20 or (k_1m > d_1m and true_lag < 10.0 and k_1m_prev < d_1m))
                scalp_condition_momentum = k_1m > k_1m_prev
                scalp_condition_decay = (pnl_pct < prev_gain and pnl_pct < 0.12) or (pnl_pct < 0.05 and ((is_long and k_1m < k_1m_prev) or (not is_long and k_1m > k_1m_prev)))
                if (scalp_condition_standard or scalp_condition_momentum or scalp_condition_decay or pnl_pct < prev_gain or k_15m > k_15m_prev or higher_high_15m) and (min_since_aug > 4 or current_price >= dc_high4_3m):
                    score -= 10.0 # Force Exit
                    if scalp_condition_decay: scalp_reason = f"SCALP_EXIT_DECAY_g{pnl_pct:.2f}%"
                    elif scalp_condition_momentum: scalp_reason = f"SCALP_EXIT_MOMENTUM_k{k_1m:.0f}>{k_1m_prev:.0f}"
                    else: scalp_reason = "SCALP_EXIT_CROSSOVER"
                    return score, "SCALP_REDUCE", scalp_reason
        # --- 3. SCALPING ENTRY LOGIC (YOUR ORIGINAL - KEPT) ---
        scalp_entry_detected = False
        if should_scalp and not is_exit:
            price_breakout = False
            if is_long:
                scalp_entry_condition_1 = ((k_1m and k_1m > d_1m) or (not k_1m and k_3m > d_3m)) and (k_15m < 30 or k_15m > d_15m or higher_high_15m) and current_price > dc_basis_1h and k_15m > k_15m_prev
                has_flipped = k_1m_prev < d_1m if d_1m > 0 else True
                scalp_entry_condition_2 = k_1m > k_1m_prev and (k_1m < 20 or current_price >= dc_high_3m) and (k_15m > d_15m or higher_high_15m)
                scalp_entry_condition_3 = time_since_exit is not None and time_since_exit.total_seconds() < 3600 and ((k_1m > d_1m and true_lag < 10.0)or k_3m > d_3m)
                if (scalp_entry_condition_1 and has_flipped) or scalp_entry_condition_2 or scalp_entry_condition_3 or current_price > last_reduction_price:
                    scalp_entry_detected = True
                    score += 7.0
                    scalp_reason = "SCALP_REENTRY_CROSSOVER" if scalp_entry_condition_1 else "SCALP_REENTRY_OVERSOLD1"
                    reasons.append(scalp_reason)
            else: # Short
                scalp_entry_condition_1 = ((k_1m and k_1m < d_1m) or (not k_1m and k_3m < d_3m)) and ((k_15m < d_15m or lower_low_15m) or k_15m > 70 or lower_low_15m) and current_price < dc_basis_1h and k_15m < k_15m_prev
                has_flipped = k_1m_prev > d_1m if d_1m > 0 else True
                scalp_entry_condition_2 = k_1m < k_1m_prev and (k_1m > 80 or current_price <= dc_low_3m) and (k_15m < d_15m or lower_low_15m)
                scalp_entry_condition_3 = time_since_exit is not None and time_since_exit.total_seconds() < 3600 and (k_1m < d_1m or k_3m < d_3m)
                if (scalp_entry_condition_1 and has_flipped) or scalp_entry_condition_2 or scalp_entry_condition_3 or current_price < last_reduction_price:
                    scalp_entry_detected = True
                    score += 7.0
                    scalp_reason = "SCALP_REENTRY_CROSSUNDER" if scalp_entry_condition_1 else "SCALP_REENTRY_OVERBOUGHT1"
                    reasons.append(scalp_reason)
            if last_reduction_price > 0:
                if is_long:
                    if current_price > last_reduction_price * 1.0002 and k_1m > k_1m_prev and k_3m > d_3m:
                        price_breakout = True
                else: # Short
                    if current_price < last_reduction_price * 0.9998 and k_1m< k_1m_prev and k_3m < d_3m:
                        price_breakout = True
            if price_breakout:
                scalp_entry_detected = True
                score += 8.0
                reasons.append("SCALP_BREAKOUT_REENTRY")
        if is_exit : #and (time_since_exit is None or time_since_exit > 120)
            if min_since_aug < 12:
                reasons.append("just_opened")
            if pnl_pct < -0.65 and min_since_aug > 6:
                    # Long: Stoch 1m hooking down OR price dropping below recent low
                    if is_long and (k_1m < k_1m_prev or current_price < low_3m):
                        return -10, "STRONG_REDUCE", f"FAST_CUT_LOSS_{pnl_pct:.2f}%"
                    # Short: Stoch 1m hooking up OR price breaking recent high
                    if not is_long and (k_1m > k_1m_prev or current_price > high_3m):
                        return -10, "STRONG_REDUCE", f"FAST_CUT_LOSS_{pnl_pct:.2f}%"
            is_flip = (is_long and ((k_1m < d_1m and true_lag < 10.0) or k_3m < d_3m or k_15m < d_15m or lower_low_15m )) or (not is_long and ((k_1m > d_1m and true_lag < 10.0) or k_3m > d_3m or k_15m > d_15m or higher_high_15m))
            if not is_flip: reasons.append("no_flip") #return 0, "HOLD", "-"
            should_close = False
            if is_flip:
                if pnl_pct < 1 and ((is_long and k_3m < d_3m) or (not is_long and k_3m > d_3m)): should_close = True; reasons.append("Stagnant_Structure_Break")
                if (is_long and (k_15m > 80 or (k_15m < d_15m or lower_low_15m) ) and (k_3m < k_3m_prev or k_1m < d_1m)) or (not is_long and (k_15m < 20 or (k_15m > d_15m or higher_high_15m) ) and (k_3m > k_3m_prev or k_1m < d_1m)): reasons.append( "Overbought_safety")
            if pnl_pct > 0.35: reasons.append("in_scalp_gain")
            if pnl_pct < 0.13 and (gain is None or gain < prev_gain) and open_min > 4: reasons.append("almost_losing")
            # --- FIX 2: Strict Momentum Exit Rule (1m & 3m both against) ---
            momentum_against = (is_long and (k_1m < d_1m and true_lag < 10.0) and k_3m < d_3m) or (not is_long and (k_1m > d_1m and true_lag < 10.0) and k_3m > d_3m) and min_since_aug > 12
            if momentum_against:
                 return -8.0, "NOW_REDUCE", "1m_3m_BOTH_AGAINST"
            # --- FIX 3: Typo correction (k_3m < k_3m -> k_3m < d_3m) ---
            is_1m_flip_against = (is_long and ((k_1m < d_1m and true_lag < 10.0)or k_3m < d_3m or current_price <= dc_low_3m)) or (not is_long and ((k_1m > d_1m and true_lag < 10.0)or k_3m > d_3m or current_price >= dc_high_3m)) and min_since_aug > 12
            if pnl_pct < 0.17 and is_1m_flip_against:
                structure_is_safe = (is_long and (k_15m > d_15m or higher_high_15m) and k_1h > d_1h and k_15m < 90) or (not is_long and (k_15m < d_15m or lower_low_15m) and k_1h < d_1h and k_15m > 10)
                if not structure_is_safe: return -8.0, "NOW_REDUCE", "AGGRESSIVE_LOSS_CUT_1m_FLIP"
                else:
                    should_close=True
                    score -= 6
            if (is_long and k_3m > 80 and (low_3m < low_3m_prev or current_price < low_3m_prev)) or (not is_long and k_3m < 20 and (high_3m > high_3m_prev or current_price < high_3m_prev )) and min_since_aug > 12:
                should_close = True; reasons.append("k_3m_flip")
            if gain is not None and gain < 0.17 and gain < prev_gain and open_min > 4:
                should_close = True; reasons.append("gain < 0.17 ")
            if should_scalp and pnl_pct > 0.4:
                if (is_long and ((k_1m and k_1m < d_1m) or (not k_1m and k_3m < d_3m)) ) or (not is_long and ((k_1m and k_1m > d_1m) or (not k_1m and k_3m > d_3m)) ): return -6.0, "SCALP_REDUCE", "Quick_Profit"
            if pnl_pct < 0.2 and (is_long and (k_3m < d_3m or k_15m<d_15m or bot_sent) and k_1m < k_1m_prev) or (not is_long and (k_3m > d_3m or (k_15m > d_15m or higher_high_15m) or top_sent) and k_1m > k_1m_prev) :
                score -= 8.0
                return score, "NO_PROFIT", "Get Out"
            if should_scalp:
                if (is_long and (k_15m > 80 or (k_15m < d_15m or lower_low_15m) or bot_sent) and (k_3m < k_3m_prev or k_1m < d_1m)) or (not is_long and (k_15m < 20 or (k_15m > d_15m or higher_high_15m) or top_sent) and (k_3m > k_3m_prev or k_1m < d_1m)) :
                    score -= 4.0
                    return score, "SCALP_PROFIT", "Fast_Profit_Take"
            if (is_long and current_price <= dc_low_3m) or (not is_long and current_price >= dc_high_3m):
                should_close = True
                reasons.append("dc_broken")
            if ((is_long and current_price <= dc_low4_3m) or (not is_long and current_price >= dc_high4_3m)) and min_since_aug < 12:
                should_close = True
                reasons.append("dc4_broken")
            if is_long and k_15m > 90 and k_1m < k_1m_prev and k_3m > 90: should_close = True; reasons.append("Overbought_Safety")
            elif not is_long and k_15m < 90 and k_1m > k_1m_prev and k_3m < 10: should_close = True; reasons.append("Oversold_Safety")
            if is_long:
                structure_bad = (dc_high_3m < dc_high_3m_ant)
                lower_high = (prev_cross_price > 0 and current_price < prev_cross_price)
                stagnant = (pnl_pct < 0.2)
                if structure_bad: reasons.append("DC_Lowering")
                if lower_high: reasons.append("Lower_High")
                if stagnant: reasons.append("Stagnant")
                if structure_bad or lower_high or stagnant: should_close = True
            else:
                structure_bad = (dc_low_3m > dc_low_3m_ant)
                higher_low = (prev_cross_price > 0 and current_price > prev_cross_price)
                stagnant = (pnl_pct < 0.17)
                if structure_bad: reasons.append("DC_Rising")
                if higher_low: reasons.append("Higher_Low")
                if stagnant: reasons.append("Stagnant")
                if structure_bad or higher_low or stagnant: should_close = True
            if should_close:
                score -= 4.0
                if "no_flip" in reasons: score +=3
                if "Stagnant" in reasons: score -= 1
                if "in_scalp_gain" in reasons: score -= 1
                if "almost_losing" in reasons: score -= 2
                if "gain < 0.17" in reasons: score -= 3
                if "k_3m_flip" in reasons: score -= 3
                if "dc_broken" in reasons: score -= 10
                if "dc4_broken" in reasons: score -= 14
                if "just_opened" in reasons: score += 7
                if "Overbought_Safety" in reasons or "Oversold_Safety" in reasons: score -= 2
                rec = "STRONG_REDUCE" if score <= -7 else "WEAK_REDUCE"
                return score, rec, ",".join(reasons)
            else:
                return 0, "HOLD", "Trend_Strong"
        logger.info(f" in rate0 {position_key} {score} {k_str}")
        if (k_3m==50 and d_3m==50) or (k_15m==50 and d_15m == 50) or (k_1h==50 and d_1h==50) :
            logger.error(f"[rate] {position_key}: ❌ BLOCKED - UNRELIABLE INDICATORS")
            return 0, "WAIT", "UNRELIABLE_INDICATORS"
        if not is_exit:
            trend_ok = False
            if is_long:
                if k_3m > d_3m: trend_ok = True
                elif k_3m < 20 and k_3m > k_3m_prev: trend_ok = True
            else:
                if k_3m < d_3m: trend_ok = True
                elif k_3m > 80 and k_3m < k_3m_prev: trend_ok = True
            special_reentry = False
            force_reentry = False
            if last_reduction_price > 0 and min_since_red < 60:
                if is_long and current_price > last_reduction_price:
                    if k_1m > k_1m_prev and k_3m > k_3m_prev:
                        force_reentry = True
                        reasons.append(f"RECLAIM_LEVEL_{last_reduction_price}")
                elif not is_long and current_price < last_reduction_price:
                    if k_1m < k_1m_prev and k_3m < k_3m_prev:
                        force_reentry = True
                        reasons.append(f"RECLAIM_LEVEL_{last_reduction_price}")
            if force_reentry:
                score += 15
                k_15m_is_safe = True
            if is_long:
                condition_15m_low = k_15m < 30 or ((k_15m < d_15m or lower_low_15m) and k_15m < 50)
                condition_1h_strong = k_1h > d_1h or k_1h > 50
                trigger_1m = k_1m > k_1m_prev
                if condition_15m_low and condition_1h_strong and trigger_1m:
                    special_reentry = True
                    reasons.append("15mLow_1hHigh_ReEntry")
            else:
                condition_15m_high = k_15m > 70 or ((k_15m > d_15m or higher_high_15m) and k_15m > 50)
                condition_1h_weak = k_1h < d_1h or k_1h < 50
                trigger_1m = k_1m < k_1m_prev
                if condition_15m_high and condition_1h_weak and trigger_1m:
                    special_reentry = True
                    reasons.append("15mHigh_1hLow_ReEntry")
            c1_long = k_1m > d_1m
            c1_short = (k_1m < d_1m and true_lag < 10.0)
            if not c1_long and is_long and not special_reentry: score = 0.3 * score
            if not c1_short and not is_long and not special_reentry: score = 0.3 * score
            pr_confirm = False
            recent_exit = False
            valid_setup = False
            # logger.info(f" in rate1 {position_key} {score}")
            if last_exit_timestamp:
                try:
                    if isinstance(last_exit_timestamp, str): exit_dt = datetime.fromisoformat(last_exit_timestamp.replace('Z', '+00:00'))
                    elif isinstance(last_exit_timestamp, datetime): exit_dt = last_exit_timestamp
                    else: exit_dt = None
                    if exit_dt:
                        if exit_dt.tzinfo is None: exit_dt = exit_dt.replace(tzinfo=timezone.utc)
                        time_since_exit = (datetime.now(timezone.utc) - exit_dt).total_seconds()
                        if time_since_exit < 7200:
                            if is_long and current_price > dc_basis_15m: pr_confirm = True
                            elif not is_long and current_price < dc_basis_15m: pr_confirm = True
                        if (datetime.now(timezone.utc) - exit_dt).total_seconds() < 3600:
                            recent_exit = True
                except: pass
            if is_long:
                if dc_high_4h > dc_high_4h_ant:
                    score += 2.0
                    reasons.append("4h_Exp_Up")
            else:
                if dc_low_4h < dc_low_4h_ant:
                    score += 2.0
                    reasons.append("4h_Exp_Down")
            if is_long:
                if k_4h < 50:
                    score += 3.0
                elif k_4h > 85:
                    score -= 3.0 # Penalty for chasing top
            else:
                if k_4h > 50:
                    score += 3.0
                elif k_4h < 15:
                    score -= 3.0
            trend_ok = False
            if recent_exit:
                set_a = is_long and k_15m < 50 and (k_1m > d_1m and true_lag < 10.0)and k_3m > d_3m and k_1m > 50 and k_3m < 50 and (k_15m > d_15m or higher_high_15m)
                if set_a:
                    valid_setup = True
                    score+=3
                    reasons.append("Trend_Continuation_ReEntry")
                set_b = not is_long and k_15m > 50 and (k_1m < d_1m and true_lag < 10.0)and k_3m < d_3m and k_1m > 50 and k_3m > 50 and (k_15m < d_15m or lower_low_15m)
                if set_b:
                    valid_setup = True
                    score +=3
                    reasons.append("Trend_Continuation_ReEntry")
                set_c = is_long and current_price > position.last_reduction_price and ((k_1m > d_1m and true_lag < 10.0)and k_1m < 50 and k_3m < 50 and (k_15m > d_15m or higher_high_15m))
                if set_c:
                    valid_setup = True
                    score+=5
                    reasons.append("Continuing up")
                set_d = not is_long and current_price < position.last_reduction_price and ((k_1m < d_1m and true_lag < 10.0)and k_1m > 50 and k_3m > 50 and (k_15m < d_15m or lower_low_15m))
                if set_d:
                    valid_setup = True
                    score +=5
                    reasons.append("Continuing down")
                set_e = (is_long and (current_price > dc_basis_15m or current_price > dc_basis_1h * 1.03) and k_15m > d_15m and k_15m < 70) or (not is_long and (current_price < dc_basis_15m or current_price < dc_basis_1h * 0.97) and k_15m < d_15m and k_15m > 30)
                if set_e:
                    score +=2
                if (set_a or set_b or set_c or set_d) and set_e:
                    return 12, "QUICK_REENTRY", f'a:{set_a} b:{set_b} c:{set_c} d:{set_d} and price >< basis'
            # Momentum Setup (Sniper)
            if is_long and k_1m_prev < 20 and k_1m > k_1m_prev:
                valid_setup = True
                score += 1.0
                reasons.append("Sniper_Long")
            elif not is_long and k_1m_prev > 80 and k_1m < k_1m_prev:
                valid_setup = True
                score += 1.0
                reasons.append("Sniper_Short")
            # 1. Safely fetch position attributes (handle None or missing attrs)
            pos_last_red = safe_fetch_float(getattr(position, 'last_reduction_price', 0.0)) if position else 0.0
            pos_last_aug = safe_fetch_float(getattr(position, 'last_augmentation_price', 0.0)) if position else 0.0
            if is_long and k_3mco and current_price < pos_last_red and current_price > pos_last_aug:
                valid_setup = True
                score += 6.0
                reasons.append("higher low")
            elif is_long and k_3mco and current_price > pos_last_aug:
                valid_setup = True
                score += 2.0
                reasons.append("price rising")
            elif not is_long and k_3mcu and current_price > pos_last_red and current_price < pos_last_aug:
                valid_setup = True
                score += 6.0
                reasons.append("lower high")
            elif not is_long and k_3mcu and current_price < pos_last_aug:
                valid_setup = True
                score += 2.0
                reasons.append("price falling")
            # 2. Safely fetch previous indicators (Default to current if missing to prevent NoneType error)
            dc_high_4h_ant = safe_fetch_float(i.get('dc_high_4h_ant'), dc_high_4h)
            dc_low_4h_ant = safe_fetch_float(i.get('dc_low_4h_ant'), dc_low_4h)
            dc_high_1h_ant = safe_fetch_float(i.get('dc_high_1h_ant'), dc_high_1h)
            dc_low_1h_ant = safe_fetch_float(i.get('dc_low_1h_ant'), dc_low_1h)
            dc_high_15m_ant = safe_fetch_float(i.get('dc_high_15m_ant'), dc_high_15m)
            dc_low_15m_ant = safe_fetch_float(i.get('dc_low_15m_ant'), dc_low_15m)
            if is_long:
                if ((dc_low_3m > dc_low_3m_ant or dc_high_3m > dc_high_3m_ant) and (dc_low_15m > dc_low_15m_ant or dc_high_15m > dc_high_15m_ant) and (dc_low_1h > dc_low_1h_ant or dc_high_1h > dc_high_1h_ant) and (dc_low_4h > dc_low_4h_ant or dc_high_4h > dc_high_4h_ant)):
                    valid_setup = True
                    score += 4.0
                    reasons.append("dc 3-15-1h-4h up")
                if ((dc_low_3m > dc_low_3m_ant or dc_high_3m > dc_high_3m_ant) and (dc_low_15m > dc_low_15m_ant or dc_high_15m > dc_high_15m_ant) and ((dc_low_1h > dc_low_1h_ant or dc_high_1h > dc_high_1h_ant) or (dc_low_4h > dc_low_4h_ant or dc_high_4h > dc_high_4h_ant))):
                    valid_setup = True
                    score += 3.0
                    reasons.append("dc 3-15-1h or 4h up")
                if ((dc_low_3m > dc_low_3m_ant or dc_high_3m > dc_high_3m_ant) or ((dc_low_15m > dc_low_15m_ant or dc_high_15m > dc_high_15m_ant) or ((dc_low_1h > dc_low_1h_ant or dc_high_1h > dc_high_1h_ant) or  (dc_low_4h > dc_low_4h_ant or dc_high_4h > dc_high_4h_ant)))):
                    valid_setup = True
                    score += 1.0
                    reasons.append("dc 3-15 or 1h or 4h up")
                if (dc_low_3m < dc_low_3m_ant and dc_high_3m < dc_high_3m_ant) or (dc_low_15m < dc_low_15m_ant and dc_high_15m < dc_high_15m_ant) :
                    score -= 4.0
                    reasons.append("dc 3 and 15 down")
                if (dc_low_3m < dc_low_3m_ant or dc_high_3m < dc_high_3m_ant) and (dc_low_15m < dc_low_15m_ant or dc_high_15m < dc_high_15m_ant) :
                    score -= 3.0
                    reasons.append("dc 3 or 15 down")
                if ((dc_low_1h < dc_low_1h_ant and dc_high_1h < dc_high_1h_ant) or (dc_low_4h < dc_low_4h_ant and dc_high_4h < dc_high_4h_ant)):
                    score -= 2.0
                    reasons.append("dc 1h or 4h down")
                if current_price >= dc_high_4h and (k_1m > d_1m and true_lag < 10.0)and (k_15m > d_15m or higher_high_15m):
                    score += 5
                    reasons.append(">dc4hhigh")
                if current_price >= dc_high_1h and (k_1m > d_1m and true_lag < 10.0)and (k_15m > d_15m or higher_high_15m):
                    score += 4
                    reasons.append(">dc1hhigh")
                if current_price >= dc_high_15m and (k_1m > d_1m and true_lag < 10.0)and (k_15m > d_15m or higher_high_15m):
                    score += 3
                    reasons.append(">dc15mhigh")
                elif current_price >= dc_high_3m and (k_1m > d_1m and true_lag < 10.0)and (k_15m > d_15m or higher_high_15m) and (k_3m < 40 or k_15m < 20):
                    score += 6
                    reasons.append(">dc3mhigh")
                elif current_price >= dc_high_3m and (k_1m > d_1m and true_lag < 10.0)and (k_15m > d_15m or higher_high_15m):
                    score += 2
                    reasons.append(">dc3mhigh")
                logger.info(f" in rate1 {position_key} {score} {k_str}")
            elif not is_long:
                if (dc_low_3m < dc_low_3m_ant or dc_high_3m < dc_high_3m_ant) and (dc_low_15m < dc_low_15m_ant or dc_high_15m < dc_high_15m_ant) and (dc_low_1h < dc_low_1h_ant or dc_high_1h < dc_high_1h_ant) and (dc_low_4h < dc_low_4h_ant or dc_high_4h < dc_high_4h_ant):
                    valid_setup = True
                    score += 4.0
                    reasons.append("dc 3-15-1h-4h down")
                if (dc_low_3m < dc_low_3m_ant or dc_high_3m < dc_high_3m_ant) and (dc_low_15m < dc_low_15m_ant or dc_high_15m < dc_high_15m_ant) and  ((dc_low_1h < dc_low_1h_ant or dc_high_1h < dc_high_1h_ant) or (dc_low_4h < dc_low_4h_ant or dc_high_4h < dc_high_4h_ant)):
                    valid_setup = True
                    score += 3.0
                    reasons.append("dc 3-15-1h or 4h down")
                if (dc_low_3m < dc_low_3m_ant or dc_high_3m < dc_high_3m_ant) or  ((dc_low_15m < dc_low_15m_ant or dc_high_15m < dc_high_15m_ant) or ((dc_low_1h < dc_low_1h_ant or dc_high_1h < dc_high_1h_ant) or (dc_low_4h < dc_low_4h_ant or dc_high_4h < dc_high_4h_ant))):
                    valid_setup = True
                    score += 1.0
                    reasons.append("dc 3-15 or 1h or 4h down")
                if (dc_low_3m > dc_low_3m_ant and dc_high_3m > dc_high_3m_ant) or (dc_low_15m > dc_low_15m_ant and dc_high_15m > dc_high_15m_ant) :
                    score -= 4.0
                    reasons.append("dc 3 and 15 up")
                if (dc_low_3m > dc_low_3m_ant or dc_high_3m > dc_high_3m_ant) and (dc_low_15m > dc_low_15m_ant or dc_high_15m > dc_high_15m_ant) :
                    score -= 3.0
                    reasons.append("dc 3 or 15 up")
                if ((dc_low_1h > dc_low_1h_ant and dc_high_1h > dc_high_1h_ant) or (dc_low_4h > dc_low_4h_ant and dc_high_4h > dc_high_4h_ant)):
                    score -= 2.0
                    reasons.append("dc 1h or 4h up")
                if current_price <= dc_low_4h and (k_1m < d_1m and true_lag < 10.0)and (k_15m < d_15m or lower_low_15m):
                    score += 5
                    reasons.append("<dc4hlow")
                if current_price <= dc_low_1h and k_3m < d_3m and (k_15m < d_15m or lower_low_15m):
                    score += 4
                    reasons.append("<dc1hlow")
                if current_price <= dc_low_15m and (k_1m < d_1m and true_lag < 10.0)and (k_15m < d_15m or lower_low_15m) and (k_3m > 60 or k_15m > 80):
                    score += 6
                    reasons.append("<dc15mlow")
                elif current_price <= dc_low_3m and k_3m < d_3m and (k_15m < d_15m or lower_low_15m):
                    score += 2
                    reasons.append("<dc3mlow")
            if (is_long and k_1m > k_1m_prev+ 50.0 or k_3m > k_3m_prev + 40.0) or (not is_long and k_1m < k_1m_prev- 50.0 or k_3m < k_3m_prev - 40.0) :
                score +=7
            elif (is_long and k_1m > k_1m_prev + 40.0 or k_3m > k_3m_prev + 30.0) or (not is_long and k_1m < k_1m_prev- 40.0 or k_3m < k_3m_prev - 30.0) :
                score +=3
            elif (is_long and k_1m > k_1m_prev + 20.0 or k_3m > k_3m_prev + 20.0) or (not is_long and k_1m < k_1m_prev- 20.0 or k_3m < k_3m_prev - 20.0) :
                score +=1
            pa_confirm = False
            if is_long:
                if prev_cross_price != 0 and current_price >= prev_cross_price:
                    pa_confirm = True
                    score += 2
                elif lr_slope > 0:
                    pa_confirm = True
                    score += 1
            else:
                if prev_cross_price != 0 and current_price <= prev_cross_price:
                    pa_confirm = True
                    score += 2
                elif lr_slope < 0:
                    pa_confirm = True
                    score += 1
            logger.info(f" in rate2 {position_key} {score} {k_str} tts1:{true_lag} ts3:{timestamp_str}")
            if is_long and k_1h > d_1h: score += 1.0
            if not is_long and k_1h < d_1h: score += 1.0
            if is_long and current_price > dc_basis_15m or not is_long and current_price < dc_basis_15m: score += 1.0
            if is_long and current_price < dc_basis_15m or not is_long and current_price > dc_basis_15m: score -= 2.0
            if is_long and current_price > dc_basis_1h or not is_long and current_price < dc_basis_1h: score += 1.0
            if is_long and current_price < dc_basis_1h or not is_long and current_price > dc_basis_1h: score -= 2.0
            if is_long and current_price > dc_basis_4h or not is_long and current_price < dc_basis_4h: score += 1.0
            if is_long and current_price < dc_basis_4h or not is_long and current_price > dc_basis_4h: score -= 2.0
            if is_long and current_price > sma_200_1m or not is_long and current_price < sma_200_1m: score += 1.0
            if is_long and current_price < sma_200_1m or not is_long and current_price > sma_200_1m: score -= 2.0
            if is_long and current_price > sma_200_15m or not is_long and current_price < sma_200_15m: score += 1.0
            if is_long and current_price < sma_200_15m or not is_long and current_price > sma_200_15m: score -= 2.0
            if is_long and current_price > sma_200_1h or not is_long and current_price < sma_200_1h: score += 1.0
            if is_long and current_price < sma_200_1h or not is_long and current_price > sma_200_1h: score -= 2.0
            if is_long:
                if ha_15m == 'green': score += 1.5
                if ha_1h == 'green': score += 1.5
                if ha_15m == 'red': score -= 2.0
            else:
                if ha_15m == 'red': score += 1.5
                if ha_1h == 'red': score += 1.5
                if ha_15m == 'green': score -= 2.0
            if is_long:
                if wt_score_15m > 0 and wt_score_1h > -20: score += 1.0
                if wt_score_15m < -50: score += 1.5
            else:
                if wt_score_15m < 0 and wt_score_1h < 20: score += 1.0
                if wt_score_15m > 50: score += 1.5
            if rel_vol > 1.5: score += 1.0; reasons.append("HighVol")
            elif rel_vol < 0.5: score -= 1.5; reasons.append("LowVol")
            z_conviction = safe_fetch_float(ind.get(f'zconviction_augment_{"long" if is_long else "short"}'), 0.0)
            if z_conviction > 20: score += 2.0; reasons.append(f"Z_Conviction({int(z_conviction)})")
            if is_long:
                if current_price > dc_high_1h * 0.995 and rel_vol < 2.0: score -= 2.0; reasons.append("Near_1H_Res")
            else:
                if current_price < dc_low_1h * 1.005 and rel_vol < 2.0: score -= 2.0; reasons.append("Near_1H_Sup")
            if trend_ok and pa_confirm: valid_setup = True; reasons.append("Trend+PA")
            elif pr_confirm: valid_setup = True; reasons.append("ReEntry_Struct")
            elif is_long and k_1m_prev < 10 and k_3m < 25 and k_1m > k_1m_prev: valid_setup = True; reasons.append("Sniper_Long")
            elif not is_long and k_1m_prev > 90 and k_3m > 75 and k_1m < k_1m_prev: valid_setup = True; reasons.append("Sniper_Short")
            logger.info(f" in rate3 {position_key} {score} ")
            history_score_mod = 0.0
            if tracker_data and not is_exit:
                win_rate = safe_fetch_float(tracker_data.get('win_rate_%', 0), 0)
                total_trades = int(safe_fetch_float(tracker_data.get('total_trades', 0), 0))
                if total_trades >= 3:
                    if win_rate >= 70: history_score_mod += 2.0; reasons.append(f"HighWinRate_{win_rate:.0f}%")
                    elif win_rate <= 30: history_score_mod -= 3.0; reasons.append(f"LowWinRate_{win_rate:.0f}%")
                total_pnl = safe_fetch_float(tracker_data.get('total_realized_pnl_$', 0), 0)
                if total_pnl < -50.0: history_score_mod -= 2.0; reasons.append("Historical_Loser")
                elif total_pnl > 50.0: history_score_mod += 1.0; reasons.append("Historical_Winner")
            if (is_long and k_3m > d_3m) or (not is_long and k_3m < d_3m): score += 2.0
            if (is_long and k_15m < 20) or (not is_long and k_15m > 80): score += 2.0
            if (is_long and k_15m > 80) or (not is_long and k_15m < 20): score -= 3.0
            if (is_long and k_3m < 20) or (not is_long and k_3m > 80): score += 2.0
            if (is_long and k_3m > 80) or (not is_long and k_3m < 20): score -= 4.0
            if (is_long and k_1h < d_1h) or (not is_long and k_1h > d_1h): score -= 2.0
            if (is_long and k_4h < d_4h) or (not is_long and k_4h > d_4h): score -= 1.0
            if (is_long and k_1h < 30 and k_1h > d_1h) or (not is_long and k_1h > 70 and k_1h < d_1h): score += 2.0
            if (is_long and current_price > dc_basis_1h * 1.04 and current_price > dc_basis_15m) or (not is_long and current_price < dc_basis_15m * 0.96 and current_price < dc_basis_15m): score += 3.0
            elif (is_long and current_price > dc_basis_1h * 1.03) or (not is_long and current_price < dc_basis_1h * 0.93): score += 1.0
            if (is_long and sco1h and current_price > dc_basis_1h ) or (not is_long and scu1h and current_price < dc_basis_1h * 0.93): score += 1.0
            if (is_long and sco15m and current_price > dc_basis_15m ) or (not is_long and scu15m and current_price < dc_basis_15m): score += 2.0
            if (is_long and current_price > dc_basis_1h) or (not is_long and current_price < dc_basis_1h): score += 1.0
            if (is_long and current_price < dc_basis_4h) or (not is_long and current_price > dc_basis_4h): score -= 2.0
            if (is_long and current_price >= dc_high_3m) or (not is_long and current_price <= dc_low_3m): score += 1.0
            if (is_long and current_price >= dc_high_15m) or (not is_long and current_price <= dc_low_15m): score += 1.5
            if (is_long and current_price >= dc_high_1h) or (not is_long and current_price <= dc_low_1h): score += 2
            if (is_long and current_price >= dc_high_4h) or (not is_long and current_price <= dc_low_4h): score += 2
            final_ai_score = safe_fetch_float(ind.get('0final_score_norm'), 0.0)
            if is_long:
                if final_ai_score > 90: score += 2.0
                elif final_ai_score > 80: score += 1.0
            else:
                if final_ai_score < -90: score += 2.0
                elif final_ai_score < -80: score += 1.0
            if is_long:
                if top_sent: score += 2.0
                elif bot_sent: score -= 5.0
            else:
                if bot_sent: score += 2.0
                elif top_sent: score -= 5.0
            rank_points = safe_fetch_float(ind.get('0ranking_points'), 0.0)
            if rank_points > 80: score += 1.5
            elif rank_points > 50: score += 0.5
            score += history_score_mod
            if ind:
                alpha = local_sent - global_sent
                if is_long:
                    if sentc == 'EXTREME_BULLISH': score += 2
                    if sentc == 'NEUTRAL': score -= 1
                    if 'BEARISH' in sentc : score -= 2
                    if local_sent > 50: score += 1.5
                    elif local_sent > 25: score += 0.5
                    if global_sent < -20: score -= 3
                    elif global_sent > 50: score +=3
                    elif global_sent > 30: score +=1
                else:
                    if sentc == 'EXTREME_BEARISH': score +=2
                    if sentc == 'NEUTRAL': score -= 1
                    if 'BULLISH' in sentc : score -= 2
                    if local_sent < -50: score += 1.5
                    elif local_sent < -25: score += 0.5
                    if global_sent > 20: score -= 3
                    elif global_sent < -50: score +=3
                    elif global_sent < -30: score +=1
                if is_long:
                    if alpha > 40: score += 3.5
                    elif alpha > 20: score += 1.5
                    elif alpha < -20: score -= 2.0
                else:
                    if alpha < -40: score += 3.5
                    if alpha < -20: score += 1.5
                    elif alpha > 20: score -= 2.0
            logger.info(f" in rate4 {symbol} {score} ")
            c1_long = k_1m > d_1m
            c1_short = (k_1m < d_1m and true_lag < 10.0)
            if not c1_long and is_long : score = 0.3 * score
            if is_long and k_1m < 20 or k_3m < 20: score = 1.5 * score
            if not is_long and k_1m > 80 or k_3m > 80: score = 1.5 * score
            if not c1_short and not is_long : score = 0.3 * score
            if not scalp_entry_detected:
                if (is_long and (k_15m < d_15m or lower_low_15m)) or (not is_long and (k_15m > d_15m or higher_high_15m)): score -=5
                if (is_long and k_3m < d_3m) or (not is_long and k_3m > d_3m): score -=5
            if score >= 5:
                if gain > 3 * config.MIN_GAIN_TO_BUY_AGGRESSIVELY: score += 4
                elif gain > 2 * config.MIN_GAIN_TO_BUY_AGGRESSIVELY: score += 3
                elif gain > config.MIN_GAIN_TO_BUY_AGGRESSIVELY: score += 1.5
            if (is_long and k_1m > 85 or k_3m > 85) or (not is_long and k_1m < 15 or k_3m < 15):
                score=0.2 * score
            if (is_long and (k_1m < d_1m and true_lag < 10.0)and k_3m < d_3m) or (not is_long and k_1m > d_3m and k_3m > d_3m):
                score=0.1 * score
            elif (is_long and ((k_1m < d_1m and true_lag < 10.0)or k_3m < d_3m)) or (not is_long and (k_1m > d_3m or k_3m > d_3m)):
                score=0.4 * score
            if (is_long and k_1m > k_1m_prev + 40.0 or k_3m > k_3m_prev + 30.0 or k_15m > k_15m_prev + 25.0) or (not is_long and k_1m < k_1m_prev - 40.0 or k_3m < k_3m_prev - 30.0 or k_15m < k_15m_prev - 25.0):
                score += 6
            final_score = max(0, min(30, int(round(score))))
            if "Trend_Continuation_ReEntry" in reasons:
                score = max(0.8*score, 10.0)
            if score > 6.0:
                logger.info(f" in rate {position_key} {score} {final_score} {k_str} ")
            if dc_low_3m and dc_low_15m and dc_low_1h:
                dc_suff = ((dc_high_3m - dc_low_3m) / dc_low_3m) > 0.3 * (dc_high_1h- dc_low_1h) / dc_low_1h or (dc_high_3m- dc_low_3m) / dc_low_3m > 0.5 * (dc_high_15m- dc_low_15m) / dc_low_15m
                if not dc_suff: score *= 0.35
            rec = "WAIT"
            if not is_exit and ((k_3m==50 and d_3m==50) or (k_15m==50 and d_15m == 50)) :
                logger.error(f"[rate] {position_key}: ❌ BLOCKED - UNRELIABLE INDICATORS")
                return 0, "WAIT", "UNRELIABLE_INDICATORS"
            position = await tracker_manager.get_position(position_key)
            if minutes_since(position.last_augmentation_time) < 24 and gain < config.MIN_GAIN_TO_BUY_AGGRESSIVELY: return 0, 'NO_GAIN_WAIT_HAS BEEN AUGMENTED ALREADY', 'NO_GAIN_WAIT_NO DOUBLE AUG'
            knife_penalty = 0.0
            if is_long:
                if k_3m < d_3m and k_3m < 20 and k_3m < k_3m_prev:
                    knife_penalty = 6.0
                    reasons.append("Falling_Knife_3m")
            else:
                if k_3m > d_3m and k_3m > 80 and k_3m > k_3m_prev:
                    knife_penalty = 6.0
                    reasons.append("Rocket_Ship_3m")
            score -= knife_penalty
            if score > 10 and gain > 3: score += 12
            if score > 8 and gain > 1.5: score += 8
            final_score = max(0, min(60, int(round(score))))
            if final_score >= 29: rec = "STRONG_BUY" if is_long else "STRONG_SELL"
            elif final_score >= 24: rec = "GOOD_BUY" if is_long else "GOOD_SELL"
            elif final_score >= 18: rec = "WEAK_BUY" if is_long else "WEAK_SELL"
            return final_score, rec, ",".join(reasons)

class RatingRegistry:
    def __init__(self, trade_manager, tracker_manager, data_manager, config):
        self.trade_manager = trade_manager
        self.tracker_manager = tracker_manager
        self.data_manager = data_manager
        self.config = config
        self.cache = {}
        self.top_longs = []
        self.top_shorts = []
        self._lock = asyncio.Lock()
        self.hedge_usage = defaultdict(int)
        self.market_panic = False
        self.market_euphoria = False
        self.last_update = 0
        self.proactive_interval = 25 # Tunable injection rate
        self.state_file = self.config.BASE_PATH / "rating_registry.json"
        self._load_state_sync()
    def _load_state_sync(self):
        """Loads previous state from disk on startup to persist rankings and usage data."""
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)
            self.top_longs = data.get('top_longs', [])
            self.top_shorts = data.get('top_shorts', [])
            self.market_panic = data.get('market_state', {}).get('panic', False)
            self.market_euphoria = data.get('market_state', {}).get('euphoria', False)
            # Load Hedge Usage (Convert back to defaultdict)
            usage_raw = data.get('hedge_usage', {})
            for k, v in usage_raw.items():
                self.hedge_usage[k] = v
            # Load Cache (Optional, but good for immediate context)
            if 'cache' in data:
                self.cache = data['cache']
            logger.info(f"📋 [REGISTRY] Loaded state from disk. Top Long: {self.top_longs[0][0] if self.top_longs else 'None'}")
        except Exception as e:
            logger.error(f"📋 [REGISTRY] Load Error: {e}")
    async def _save_state(self):
        """Saves current state to JSON for monitoring and persistence."""
        try:
            # Snapshot state inside lock
            async with self._lock:
                state = { "timestamp": datetime.now(timezone.utc).isoformat(), "market_state": { "panic": self.market_panic, "euphoria": self.market_euphoria, "total_ranked": len(self.cache) }, "top_longs": self.top_longs[:50], "top_shorts": self.top_shorts[:50], "hedge_usage": dict(self.hedge_usage), "cache": self.cache }
            # Atomic Write
            temp_file = self.state_file.with_suffix(".tmp")
            json_bytes = await asyncio.to_thread( json.dumps, state, indent=2, default=str )
            async with aiofiles.open(temp_file, "w") as f:
                await f.write(json_bytes)
                await f.flush()
                await asyncio.to_thread(os.fsync, f.fileno())
            await asyncio.to_thread(os.replace, temp_file, self.state_file)
        except Exception as e:
            logger.error(f"📋 [REGISTRY] Save Error: {e}")
    async def run_loop(self):
        """Main Loop"""
        logger.info("📋 [REGISTRY] Loop Started")
        await asyncio.sleep(10)
        last_match_time = time.time()
        while True:
            try:
                await self.refresh_rankings()
                await self._save_state()
                now = time.time()
                if now - last_match_time > self.proactive_interval:
                    await self._match_and_inject_opportunities()
                    last_match_time = now
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[REGISTRY] Loop Error: {e}", exc_info=True)
                await asyncio.sleep(10)
    async def update(self, symbol, side, rating_tuple):
        """Manual update hook for external callers."""
        async with self._lock:
            if symbol not in self.cache: self.cache[symbol] = {}
            self.cache[symbol][side] = rating_tuple
    def get_rating(self, symbol, side):
        """Retrieve latest rating tuple for a specific symbol/side."""
        return self.cache.get(symbol, {}).get(side, (0, "WAIT", "NO_CACHE", 0))
    async def refresh_rankings(self):
        """
        Scans ALL symbols in DataManager, rates them, updates cache/top lists, and determines market state.
        """
        snapshot = self.data_manager._cold_data
        if not snapshot: return
        temp_longs = []
        temp_shorts = []
        temp_cache = {}
        # Proxy account for generic rating
        proxy_account = 'flz'
        processed_count = 0
        for symbol, data in snapshot.items():
            if not isinstance(data, dict): continue
            # Filters
            price = safe_fetch_float(data.get('current_price', 0) or data.get('close', 0))
            if price <= 0: continue
            if "USD" in symbol and ("USDT" in symbol or "DAI" in symbol):
                 if abs(price - 1.0) < 0.05: continue
            # --- RATE LONG ---
            score_l, rec_l, reason_l = await AdvancedSignalRater.rate( proxy_account, symbol, True, price, data, data, 0.0, is_exit=False, is_allowed=True, tracker_manager=self.tracker_manager )
            # --- RATE SHORT ---
            score_s, rec_s, reason_s = await AdvancedSignalRater.rate( proxy_account, symbol, False, price, data, data, 0.0, is_exit=False, is_allowed=True, tracker_manager=self.tracker_manager )
            # Store in Temp Cache
            temp_cache[symbol] = { "LONG": (score_l, rec_l, reason_l, price), "SHORT": (score_s, rec_s, reason_s, price) }
            # Add to Lists if Good
            if score_l >= 12:
                temp_longs.append((symbol, score_l, price))
            if score_s >= 12:
                    temp_shorts.append((symbol, score_s, price))
            processed_count += 1
            if processed_count % 30 == 0: await asyncio.sleep(0)
        # Sort Lists
        temp_longs.sort(key=lambda x: x[1], reverse=True)
        temp_shorts.sort(key=lambda x: x[1], reverse=True)
        async with self._lock:
            self.cache.update(temp_cache)
            self.top_longs = temp_longs[:100]
            self.top_shorts = temp_shorts[:100]
            # Market State Logic
            if len(self.top_shorts) >= 5:
                avg_short = sum(x[1] for x in self.top_shorts[:10]) / min(len(self.top_shorts), 10)
                self.market_panic = avg_short > 22
            else: self.market_panic = False
            if len(self.top_longs) >= 5:
                avg_long = sum(x[1] for x in self.top_longs[:10]) / min(len(self.top_longs), 10)
                self.market_euphoria = avg_long > 22
            else: self.market_euphoria = False
            self.last_update = time.time()
    async def _inject_opportunities(self):
        """Feeds top opportunities to specific accounts based on their allowed keys."""
        for acc in self.trade_manager.target_accounts:
            universe = self.tracker_manager.get_tradeable_position_keys_for(acc)
            allowed_longs = {k.split(':')[1].replace('_LONG','') for k in universe if 'LONG' in k}
            allowed_shorts = {k.split(':')[1].replace('_SHORT','') for k in universe if 'SHORT' in k}
            picks = []
            # Top 3 Valid Longs
            count = 0
            for sym, score, _ in self.top_longs:
                if sym in allowed_longs:
                    key = construct_position_key(acc, sym, 'LONG')
                    picks.append(key)
                    count += 1
                    if count >= 3: break
            # Top 3 Valid Shorts
            count = 0
            for sym, score, _ in self.top_shorts:
                if sym in allowed_shorts:
                    key = construct_position_key(acc, sym, 'SHORT')
                    picks.append(key)
                    count += 1
                    if count >= 3: break
            if picks:
                for pos_key in picks:
                    try:
                        sym = pos_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
                        current_price, _ = await self.data_manager.get_fresh_price(sym)
                        if current_price > 0:
                            await execute_trade_wrapper( trade_manager=self.trade_manager, tracker_manager=self.tracker_manager, hedge_engine=getattr(self.trade_manager, 'hedge_engine', None), account_key=acc, position_key=pos_key, positionAmt=0.0, action='OPEN', current_price=current_price, qty=0.0, reason="REGISTRY_INJECT", already_locked=False, is_hedge=False, data_manager=self.data_manager )
                    except Exception as e:
                        logger.error(f"[REGISTRY] Failed to inject {pos_key}: {e}")
    def get_hottest_hedge(self, account_key, target_side, exclude_symbols=None):
        """Finds best hedge candidate (High Score + Allowed + Not Crowded)"""
        candidates = self.top_longs if target_side == "LONG" else self.top_shorts
        if not candidates: return None, 0
        allowed_symbols = set()
        universe = self.tracker_manager.get_tradeable_position_keys_for(account_key)
        for k in universe:
            try:
                _, p_sym, p_side = parse_position_key(k)
                if p_side == target_side: allowed_symbols.add(p_sym)
            except: pass
        sym_to_key_map = {}
        for k in universe:
            _, sym, side = parse_position_key(k)
            if side == target_side: sym_to_key_map[sym] = k
        for sym, score, price in candidates:
            if exclude_symbols and sym in exclude_symbols: continue
            if allowed_symbols and sym not in allowed_symbols: continue
            usage_key = f"{account_key}:{sym}"
            if self.hedge_usage[usage_key] >= 2: continue
            # Existing Position Check (Must be winning)
            pos_key = sym_to_key_map.get(sym) or construct_position_key(account_key, sym, target_side)
            pos = self.trade_manager.position_manager.get_position(pos_key)
            if pos and abs(safe_fetch_float(pos.positionAmt, 0)) > 0:
                gain = getattr(pos, 'gain', 0.0)
                if gain < 0.25: continue
                if gain > 2.0: score += 5
            if score >= 10:
                self.hedge_usage[usage_key] += 1
                return sym, score
        return None, 0
    def release_hedge_slot(self, account_key, symbol):
        usage_key = f"{account_key}:{symbol}"
        if self.hedge_usage[usage_key] > 0:
            self.hedge_usage[usage_key] -= 1
    async def _match_and_inject_opportunities(self):
        for account_key in self.trade_manager.accounts:
            # Skip if account not allowed on this instance
            if hasattr(self.trade_manager, '_check_account_allowed'):
                if not self.trade_manager._check_account_allowed(account_key): continue
            # 1. Get Allowed Keys for Account
            universe = self.tracker_manager.get_tradeable_position_keys_for(account_key)
            if not universe: continue
            # Map Symbol -> Full Key(s)
            # e.g. 'BTC': {'ang:BTCUSDC_LONG', 'ang:BTCUSDC_SHORT'}
            sym_map = defaultdict(set)
            for k in universe:
                try:
                    _, sym, side = parse_position_key(k)
                    sym_map[sym].add(k)
                except: pass
            # 2. Find Candidates (Intersection)
            entry_batch = []
            exit_batch = []
            # A. Process Top Longs
            for sym, score, price in self.top_longs:
                if sym in sym_map:
                    # We have keys for this symbol.
                    # Check if we should Check Entry (Long Key) or Exit (Short Key? No, usually Longs imply Long Key)
                    for key in sym_map[sym]:
                        if 'LONG' in key:
                            await self._classify_and_queue(key, entry_batch, exit_batch)
            # B. Process Top Shorts
            for sym, score, price in self.top_shorts:
                if sym in sym_map:
                    for key in sym_map[sym]:
                        if 'SHORT' in key:
                            await self._classify_and_queue(key, entry_batch, exit_batch)
            # 3. Dispatch Batches
            if entry_batch:
                # logger.info(f"📋 [REGISTRY][{account_key}] Injecting {len(entry_batch)} Entry Candidates (Top Rated)")
                asyncio.create_task(check_entry_candidates_for_account( self.trade_manager, account_key, self.trade_manager.redis_manager, self.tracker_manager, self.trade_manager.order_queue, self.data_manager, self.trade_manager.hedge_engine, position_keys=entry_batch ))
            if exit_batch:
                # logger.info(f"📋 [REGISTRY][{account_key}] Injecting {len(exit_batch)} Exit Checks (Top Rated)")
                asyncio.create_task(check_exit_candidates_for_account( self.trade_manager, account_key, self.trade_manager.redis_manager, self.tracker_manager, self.trade_manager.order_queue, self.data_manager, self.trade_manager.hedge_engine, position_keys=exit_batch ))
            await asyncio.sleep(0.5) # Yield between accounts
    async def _classify_and_queue(self, position_key, entry_batch, exit_batch):
        """Helper to decide if a key goes to Entry Check or Exit Check"""
        # If it is in exit_candidates (Active), send to Exit Check
        # If it is in entry_candidates (Waiting), send to Entry Check
        # Check Active (Fastest check)
        is_active = False
        async with self.tracker_manager._exit_candidates_lock:
             if position_key in self.tracker_manager.exit_candidates:
                 is_active = True
        if is_active:
            exit_batch.append(position_key)
        else:
            # Only queue for entry if not cooling down
            if not await self.tracker_manager.is_trade_cooldown_active(position_key):
                entry_batch.append(position_key)

class HedgeEngine:
    def __init__(self, trade_manager, tracker_manager, data_manager, config, redis_manager=None, positions_service=None, registry = None):
        self.trade_manager = trade_manager
        self.tracker_manager = tracker_manager
        self.data_manager = data_manager
        self.registry = registry
        self.config = config
        self.redis_manager = redis_manager
        self.positions_service = positions_service or getattr(tracker_manager, "positions_service", None)
        self._account_locks: Dict[str, DummyLock] = {}
        self.default_hedge_candidates = ["BTCUSDC", "BTCDOMUSDT", "ETHUSDC", "SKYUSDT", "LINKUSDC", "TONUSDT", "AAVEUSDC", "ZENUSDT", "ZECUSDC", "AVAXUSDC", "SUIUSDC", "TRUMPUSDC", "DASHUSDT", "1INCHUSDT", "SOLUSDC", "BNBUSDC", "XAGUSDT"]
        self.hedge_multiplier = getattr(config, 'HEDGE_MULTIPLIER', 1.0)
        self.min_loss_for_hedge = getattr(config, 'MIN_LOSS_FOR_HEDGE', -0.1)
        self.max_loss_for_hedge = getattr(config, 'MAX_LOSS_FOR_HEDGE', -3.0)
        self.min_gain_for_pyramid = 0.5 * getattr(config, 'MIN_GAIN_TO_BUY_AGGRESSIVELY', 1.5)
        self.pyramid_size_ratio = getattr(config, 'PYRAMID_SIZE_RATIO', 0.4)
        self.max_hedge_notional = getattr(config, 'MAX_HEDGE_NOTIONAL', 5000.0)
        self.elected_symbol_ratio = 0.5
        self.actual_symbol_ratio = 0.5
        self._hedge_cooldowns: Dict[str, float] = {} # symbol -> timestamp of last hedge use
        self.HEDGE_COOLDOWN_SECONDS = 420 # 7 minutes between using same symbol as hedge
    def _get_account_lock(self, account_key: str) -> DummyLock:
        if account_key not in self._account_locks: self._account_locks[account_key] = DummyLock()
        return self._account_locks[account_key]
    def _validate_hedge_safety(self, losing_symbol: str, losing_side: str, hedge_symbol: str, hedge_side: str) -> Tuple[bool, str]:
        """Block same-direction hedges. Only BTCDOM is allowed as same-direction hedge."""
        if losing_side == hedge_side:
            if 'BTCDOM' not in hedge_symbol and 'BTCDOM' not in losing_symbol:
                return False, f"BLOCKED: Same-direction hedge {losing_symbol}_{losing_side} -> {hedge_symbol}_{hedge_side}"
            logger.info(f"[HEDGE_SAFETY] Allowing same-direction hedge via BTCDOM: {hedge_symbol}_{hedge_side}")
        return True, "Safe"
    async def persist_hedge_record(self, account_key: str, hedge_record: Dict[str, Any]) -> None:
        # 1. Update Memory IMMEDIATELY (Critical for stopping the loop)
        async with self.tracker_manager._hedges_lock:
            # Remove old if exists (update)
            self.tracker_manager.active_hedges = [ h for h in self.tracker_manager.active_hedges if h.get('id') != hedge_record.get('id') ]
            self.tracker_manager.active_hedges.append(hedge_record)
        # 2. Update Exit Candidate metadata
        position_key = hedge_record.get('position_key')
        if position_key:
            async with self.tracker_manager._exit_candidates_lock:
                if position_key not in self.tracker_manager.exit_candidates:
                    # If new hedge position, create template
                    self.tracker_manager.exit_candidates[position_key] = self.tracker_manager._exit_template()
                existing = self.tracker_manager.exit_candidates[position_key]
                existing.update({ 'is_hedge': True, 'hedge_for': hedge_record.get('losing_position_key'), 'hedge_id': hedge_record.get('id'), 'positionAmt': hedge_record.get('quantity', 0.0), 'entry_price': hedge_record.get('price', 0.0), 'status': 'active' })
                self.tracker_manager.exit_candidates[position_key] = existing
                self.tracker_manager._exit_candidates_dirty[account_key] = True
        # 3. Save to Disk
        await self.tracker_manager.save_tracker(account_key, force=True)
    async def should_close_original_position(self, account_key: str, position_key: str, current_price: float) -> bool:
        async with self.tracker_manager._hedges_lock:
            active_hedge = None
            for hedge in self.tracker_manager.active_hedges:
                if (hedge.get('account') == account_key and hedge.get('hedge_for') == position_key and hedge.get('is_hedge', False)):
                    active_hedge = hedge
                    break
            if not active_hedge: return True
            position_data = await self.tracker_manager.get_exit_candidate(position_key)
            if not position_data: return True
            entry_price = safe_fetch_float(position_data.get('average_entry_price', 0.0), 0.0)
            if entry_price <= 0: return True
            is_long = position_key.endswith('_LONG')
            if is_long: gain = ((current_price - entry_price) / entry_price) * 100.0
            else: gain = ((entry_price - current_price) / entry_price) * 100.0
            return gain > 0.5
    async def load_hedge_records(self, account_key: str) -> List[Dict[str, Any]]:
        if hasattr(self, 'trade_manager') and self.trade_manager and hasattr(self.trade_manager, '_check_account_allowed'):
            if not self.trade_manager._check_account_allowed(account_key):
                logger.info(f"[load_hedge_records] BLOCKED: Account '{account_key}' not in allowed accounts")
                return []
        records = []
        async with self.tracker_manager._hedges_lock:
            account_hedges = [ h for h in self.tracker_manager.active_hedges if h.get('account') == account_key ]
            records.extend(account_hedges)
        try:
            tracker_file = self.tracker_manager.get_tracker_file(account_key)
            if await aio_os.path.exists(tracker_file):
                async with FILE_IO_SEMAPHORE:
                    async with aiofiles.open(tracker_file, 'r') as f:
                        data = json.loads(await f.read())
                        if 'active_hedges' in data and isinstance(data['active_hedges'], list):
                            file_hedges = [ h for h in data['active_hedges'] if h.get('account') == account_key ]
                            for hedge in file_hedges:
                                if not any(h.get('id') == hedge.get('id') for h in records):
                                    records.append(hedge)
        except Exception as e:
            logger.info(f"[ ] Failed to load hedges from tracker file: {e}")
        unique_records = {}
        for record in records:
            record_id = record.get('id')
            if record_id: unique_records[record_id] = record
        return list(unique_records.values())
    async def scan_and_hedge_losers(self, account_key: str):
        if not is_hedge_account(self.config, account_key):
            return
        positions = self.positions_service.positions_by_account.get(account_key, {})
        for position_key, pos in positions.items():
            if not position_key.startswith(account_key): continue
            position = await self.tracker_manager.get_position(position_key)
            if not pos: pos=position
            async with self.tracker_manager._exit_candidates_lock:
                tracker_data = self.tracker_manager.exit_candidates.get(position_key)
            if tracker_data and (tracker_data.get('is_hedge', False) or tracker_data.get('hedge_for')):
                continue
            qty = abs(safe_fetch_float(getattr(pos, 'positionAmt', 0)))
            entry_price = safe_fetch_float(getattr(pos, 'entry_price', 0))
            mark_price = safe_fetch_float(getattr(pos, 'mark_price', 0))
            if qty * mark_price < 10.0: continue
            is_long = position_key.endswith('_LONG')
            if is_long: pnl_pct = ((mark_price - entry_price) / entry_price) * 100
            else: pnl_pct = ((entry_price - mark_price) / entry_price) * 100
            if pnl_pct < -1.0:
                await self._manage_hedge_for_position(account_key, position_key, pos, qty, mark_price, pnl_pct, tracker_data)
    async def _manage_hedge_for_position(self, account_key, losing_key, losing_pos, losing_qty, current_price, pnl_pct, tracker_data):
        symbol = losing_key.split(':')[1].replace('_LONG','').replace('_SHORT','')
        losing_side = 'LONG' if losing_key.endswith('_LONG') else 'SHORT'
        hedge_side = 'SHORT' if losing_side == 'LONG' else 'LONG'
        hedge_key = f"{account_key}:{symbol}_{hedge_side}"
        positions = self.positions_service.positions_by_account.get(account_key, {})
        hedge_pos = positions.get(hedge_key)
        losing_position = positions.get(losing_key)
        real_hedge_qty = abs(safe_fetch_float(getattr(hedge_pos, 'positionAmt', 0))) if hedge_pos else 0.0
        losing_value = losing_qty * current_price
        existing_hedge_value = real_hedge_qty * current_price
        history_loss = safe_fetch_float(tracker_data.get('total_realized_pnl_$', 0.0)) if tracker_data else 0.0
        target_ratio = 0.0
        if pnl_pct < -2.0 or history_loss < -30: target_ratio = 1.0
        elif pnl_pct < -1.0 or history_loss < -10: target_ratio = 0.7
        elif pnl_pct < -0.6: target_ratio = 0.4
        if target_ratio == 0: return
        if real_hedge_qty > 0 and existing_hedge_value >= (losing_value * 0.5):
             logger.debug(f"[HEDGE_GUARD] {losing_key} already has hedge {hedge_key} with value ${existing_hedge_value:.2f} (TargetRatio:{target_ratio}). Skipping.")
             return
        target_hedge_value = losing_value * target_ratio
        if existing_hedge_value >= (target_hedge_value * 0.95):
            if tracker_data and not tracker_data.get('has_hedge_lock'):
                 async with self.tracker_manager._exit_candidates_lock:
                     if losing_key in self.tracker_manager.exit_candidates:
                         self.tracker_manager.exit_candidates[losing_key]['has_hedge_lock'] = True
            return
        shortfall_value = target_hedge_value - existing_hedge_value
        qty_to_add = shortfall_value / current_price
        if (qty_to_add * current_price) < 6.0: return
        action = 'OPEN' if real_hedge_qty == 0 else 'AUGMENT'
        logger.info(f"🛡️ [HEDGE_CALC] {losing_key} ($-{losing_value:.2f}) vs {hedge_key} ($-{existing_hedge_value:.2f}). Target Ratio: {target_ratio}. Adding: ${shortfall_value:.2f}")
        await self.execute_dual_hedge(account_key, losing_key, symbol, losing_side, losing_value, False)
    async def monitor_hedge_health_loop(self, stop_event: asyncio.Event):
        any_hedge_enabled = any(is_hedge_account(self.config, ak) for ak in self.config.ACCOUNT_KEYS)
        if not any_hedge_enabled:
            return
        """
        Smart Hedge Monitor:
        1. Identifies specific Hedge <-> Origin pairs via Tracker.
        2. Adjusts Target Ratio dynamically based on Market Sentiment (ported from service).
        3. Checks Technical Indicators (Stoch/D) before executing size changes.
        """
        logger.info("[HedgeHealth] Started SMART monitoring loop (Sentiment + Tech checks).")
        # Configuration
        TOLERANCE_PCT = 0.17 # 15% deviation tolerance
        MIN_ADJUST_USD = 20.0
        CHECK_INTERVAL = 10
        # --- Ported Helper Functions ---
        def can_adjust_long(indicators: Dict[str, Any], position = None) -> bool:
            """Safe to ADD to LONG hedge or REDUCE SHORT hedge?"""
            try:
                k_3m = float(indicators.get('stoch_k_3m', 50)); d_3m = float(indicators.get('stoch_d_3m', 50))
                k_15m = float(indicators.get('stoch_k_15m', 50)); d_15m = float(indicators.get('stoch_d_15m', 50))
                k_1h = float(indicators.get('stoch_k_1h', 50)); d_1h = float(indicators.get('stoch_d_1h', 50))
                momentum_ok = (k_3m > d_3m) or (k_15m > d_15m) or (k_1h > d_1h)
                if not momentum_ok: return False
                if position:
                    cg = getattr(position, 'gain', 0.0); pg = getattr(position, 'prev_gain', cg)
                    if cg < pg - 0.05: return False # Gain deteriorating
                return True
            except: return True
        def can_adjust_short(indicators: Dict[str, Any], position = None) -> bool:
            """Safe to ADD to SHORT hedge or REDUCE LONG hedge?"""
            try:
                k_3m = float(indicators.get('stoch_k_3m', 50)); d_3m = float(indicators.get('stoch_d_3m', 50))
                k_15m = float(indicators.get('stoch_k_15m', 50)); d_15m = float(indicators.get('stoch_d_15m', 50))
                k_1h = float(indicators.get('stoch_k_1h', 50)); d_1h = float(indicators.get('stoch_d_1h', 50))
                momentum_ok = (k_3m < d_3m) or (k_15m < d_15m) or (k_1h < d_1h)
                if not momentum_ok: return False
                if position:
                    cg = getattr(position, 'gain', 0.0); pg = getattr(position, 'prev_gain', cg)
                    if cg < pg - 0.05: return False # Gain deteriorating
                return True
            except: return True
        while not stop_event.is_set():
            try:
                await asyncio.sleep(CHECK_INTERVAL)
                # Snapshot active hedges
                async with self.tracker_manager._hedges_lock:
                    active_hedges_snapshot = list(self.tracker_manager.active_hedges)
                # Group by account to minimize context switching
                hedges_by_account = defaultdict(list)
                for h in active_hedges_snapshot:
                    hedges_by_account[h.get('account')].append(h)
                for account_key, hedges in hedges_by_account.items():
                    if self.trade_manager and hasattr(self.trade_manager, '_check_account_allowed'):
                        if not self.trade_manager._check_account_allowed(account_key): continue
                    positions = self.positions_service.positions_by_account.get(account_key, {})
                    for record in hedges:
                        try:
                            # --- A. Validate Existence ---
                            hedge_key = record.get('position_key')
                            losing_key = record.get('losing_position_key')
                            hedge_pos = positions.get(hedge_key)
                            losing_pos = positions.get(losing_key)
                            if not hedge_pos: continue # Hedge closed, tracker will clean up
                            if not losing_pos:
                                # Origin closed -> Hedge should assume full closing logic (handled by main monitor)
                                continue
                            # --- B. Get Data ---
                            hedge_sym = hedge_pos.symbol
                            losing_sym = losing_pos.symbol
                            # Prices
                            h_price, _ = await self.data_manager.get_fresh_price(hedge_sym)
                            if not h_price or h_price <= 0: h_price, _ = await get_current_price(hedge_sym)
                            l_price, _ = await self.data_manager.get_fresh_price(losing_sym)
                            if not l_price or l_price <= 0: l_price, _ = await get_current_price(losing_sym)
                            if h_price <= 0 or l_price <= 0: continue
                            metrics, h_ind, k_1m, d_1m, k_3m, d_3m, is_data_fresh = await self.data_manager.get_hot_state(hedge_sym)
                            if not h_ind: h_ind = {}
                            # Values
                            hedge_amt = abs(safe_fetch_float(getattr(hedge_pos, 'positionAmt', 0)))
                            losing_amt = abs(safe_fetch_float(getattr(losing_pos, 'positionAmt', 0)))
                            hedge_value = hedge_amt * h_price
                            losing_value = losing_amt * l_price
                            # --- HEDGE PnL GUARD (NEW) ---
                            # A hedge should NEVER be a major liability. If the hedge itself is losing, # it means the market has reversed in favor of the original position.
                            hedge_gain = getattr(hedge_pos, 'gain', 0.0)
                            if hedge_gain < -0.15:
                                logger.critical(f"🚨 [HEDGE_LIABILITY_KILL] Hedge {hedge_key} is losing {hedge_gain:.2f}%. Market reversal detected? KILLING HEDGE.")
                                # Set cooldown on the ORIGINAL position so we don't immediately re-hedge
                                self.tracker_manager.hedge_liability_cooldowns[losing_key] = time.time()
                                await execute_trade_wrapper( trade_manager=self.trade_manager, tracker_manager=self.tracker_manager, hedge_engine=self, account_key=account_key, position_key=hedge_key, positionAmt=hedge_amt, action='CLOSE', current_price=h_price, qty=hedge_amt, reason=f"HEDGE_LIABILITY_KILL_{hedge_gain:.2f}%", is_hedge=True, hedge_for=losing_key, data_manager=self.data_manager )
                                continue
                            # --- C. Calculate Dynamic Target Ratio (The "Brain" from Service) ---
                            sentiment = float(h_ind.get('0market_sentiment_score', 0.0))
                            losing_pnl = ((l_price - losing_pos.entry_price) / losing_pos.entry_price * 100.0) if losing_pos.position_side == 'LONG' else ((losing_pos.entry_price - l_price) / losing_pos.entry_price * 100.0)
                            # --- RECOVERY TRIM / PROFIT GUARD (NEW) ---
                            # If the original position has recovered significantly (almost break-even), # we aggressively kill the hedge to prevent it from becoming a liability.
                            if losing_pnl > -0.05:
                                logger.info(f"⚖️ [HEDGE_RECOVERY_TRIM] {losing_key} recovered to {losing_pnl:.2f}%. Killing hedge {hedge_key} early.")
                                target_ratio = 0.0
                            else:
                                # Default base ratio from the record (usually 1.0 or 0.7)
                                base_ratio = safe_fetch_float(record.get('ratio', 1.0))
                                target_ratio = base_ratio
                                is_hedge_long = (hedge_pos.position_side == 'LONG')
                                if is_hedge_long:
                                    # We are hedging a SHORT with a LONG.
                                    # If Sentiment is Bullish -> We need MORE hedge (market going against short)
                                    if sentiment > 50: target_ratio *= 1.3
                                    elif sentiment > 25: target_ratio *= 1.15
                                    # If Sentiment is Bearish -> We need LESS hedge (market helping short)
                                    elif sentiment < -25: target_ratio *= 0.8
                                else:
                                    # We are hedging a LONG with a SHORT.
                                    # If Sentiment is Bearish -> We need MORE hedge
                                    if sentiment < -50: target_ratio *= 1.3
                                    elif sentiment < -25: target_ratio *= 1.15
                                    # If Sentiment is Bullish -> We need LESS hedge
                                    elif sentiment > 25: target_ratio *= 0.8
                            target_hedge_value = losing_value * target_ratio
                            # --- D. Determine Action ---
                            diff_usd = target_hedge_value - hedge_value
                            diff_pct = diff_usd / hedge_value if hedge_value > 0 else 1.0
                            action = None
                            qty_to_trade = 0.0
                            # Only act if deviation is significant
                            if abs(diff_pct) > TOLERANCE_PCT and abs(diff_usd) > MIN_ADJUST_USD:
                                if diff_usd > 0:
                                    # Need to ADD to hedge
                                    # CHECK TECHS: Can we add to this side?
                                    allowed = can_adjust_long(h_ind, hedge_pos) if is_hedge_long else can_adjust_short(h_ind, hedge_pos)
                                    if allowed:
                                        action = "AUGMENT"
                                        qty_to_trade = abs(diff_usd) / h_price
                                    else:
                                        logger.debug(f"[HEDGE_SKIP] {hedge_key} wants AUGMENT but indicators forbid.")
                                elif diff_usd < 0:
                                    # Need to REDUCE hedge (Over-hedged)
                                    if abs(diff_pct) > 0.25:
                                        # If target_ratio is 0, we are KILLING the hedge (Recovery Trim)
                                        # In this case, we bypass indicator checks.
                                        is_kill = (target_ratio == 0.0)
                                        allowed = True if is_kill else (can_adjust_short(h_ind, hedge_pos) if is_hedge_long else can_adjust_long(h_ind, hedge_pos))
                                        if allowed:
                                            action = "CLOSE" if is_kill else "REDUCE"
                                            qty_to_trade = hedge_amt if is_kill else abs(diff_usd) / h_price
                                        else:
                                            logger.debug(f"[HEDGE_SKIP] {hedge_key} wants REDUCE but indicators forbid.")
                            # --- FORTIFICATION LOGIC (NEW) ---
                            # If we have both LONG and SHORT for the same symbol, and one is losing, # ensure the opposite (winning) side is fortified if its qty is smaller.
                            # Fortification continues until Stoch RSI turns or gain deteriorates.
                            other_side = 'SHORT' if losing_pos.position_side == 'LONG' else 'LONG'
                            other_key = construct_position_key(account_key, losing_sym, other_side)
                            other_pos = positions.get(other_key)
                            if other_pos and losing_pos:
                                other_amt = abs(safe_fetch_float(other_pos.positionAmt, 0.0))
                                losing_amt = abs(safe_fetch_float(losing_pos.positionAmt, 0.0))
                                if other_amt < losing_amt:
                                    # Candidate for fortification
                                    # Must be winning or neutral
                                    other_pnl = ((h_price - other_pos.entry_price) / other_pos.entry_price * 100.0) if other_side == 'LONG' else ((other_pos.entry_price - h_price) / other_pos.entry_price * 100.0)
                                    if other_pnl > -0.05:
                                        # Check momentum and deterioration
                                        fortify_allowed = can_adjust_long(h_ind, other_pos) if other_side == 'LONG' else can_adjust_short(h_ind, other_pos)
                                        if fortify_allowed:
                                            # We want other_amt to at least match losing_amt
                                            fortify_usd = (losing_amt - other_amt) * h_price
                                            if fortify_usd > MIN_ADJUST_USD:
                                                # Add to standard action if not already augmenting
                                                if not action or action == "AUGMENT":
                                                    action = "AUGMENT"
                                                    qty_to_trade = max(qty_to_trade, (losing_amt - other_amt))
                                                    logger.info(f"🏰 [FORTIFY] {other_key} needs to match losing {losing_key}. Adding {qty_to_trade:.6f}")
                            # --- E. Execute ---
                            if action and qty_to_trade > 0:
                                logger.info(f"⚖️ [HEDGE_BALANCER] {hedge_key} ({action}) to match {losing_key}. " f"Sent:{sentiment:.0f} TargetRatio:{target_ratio:.2f} Diff:${diff_usd:.2f}")
                                success, trade_result = await execute_trade_wrapper( trade_manager=self.trade_manager, tracker_manager=self.tracker_manager, hedge_engine=self, account_key=account_key, position_key=hedge_key, positionAmt=hedge_amt, action=action, current_price=h_price, qty=qty_to_trade, reason=f"HEDGE_BAL_{action}_SENT{sentiment:.0f}_RATIO{target_ratio:.2f}", already_locked=False, is_hedge=True, hedge_for=losing_key, data_manager=self.data_manager )
                                # --- EMERGENCY MITIGATION ---
                                # If we failed to AUGMENT a hedge that was seriously short (diff_usd > 0)
                                # and the original position is in meaningful loss, we must exit the original.
                                if not success and action == "AUGMENT" and diff_usd > 50.0:
                                    losing_pnl = ((l_price - losing_pos.entry_price) / losing_pos.entry_price * 100.0) if losing_pos.position_side == 'LONG' else ((losing_pos.entry_price - l_price) / losing_pos.entry_price * 100.0)
                                    if losing_pnl < -0.2:
                                        logger.critical(f"🚨 [HEDGE_BAL_FAILURE_KILL] Failed to augment hedge {hedge_key} for {losing_key} ({losing_pnl:.2f}% loss). KILLING ORIGINAL.")
                                        side_kill = 'SELL' if losing_pos.position_side == 'LONG' else 'BUY'
                                        await self.trade_manager.execute_now( losing_key, account_key, losing_sym, losing_amt, side_kill, losing_pos.position_side, losing_amt, l_price, f"HEDGE_BAL_FAIL_KILL_{int(time.time())}", f"HEDGE_BAL_FAILURE_MITIGATION_{trade_result}", True, 'CLOSE' )
                        except Exception as e:
                            logger.error(f"[HEDGE_BALANCER] Error on {record.get('position_key')}: {e}")
                            continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[HEDGE_BALANCER] Loop Crash: {e}", exc_info=True)
                await asyncio.sleep(60)
    async def _calculate_hedge_candidate_score(self, account_key, candidate_symbol: str,target_side: str, indicators: Dict[str, Any], current_price: float, losing_symbol: str,metrics: Dict[str, float] ) -> float:
        k_1m = safe_fetch_float(metrics.get('k_1m', 50.0), 50.0)
        d_1m = safe_fetch_float(metrics.get('d_1m', 50.0), 50.0)
        k_1m_prev = safe_fetch_float(metrics.get('k_1m_prev', 50.0), 50.0)
        k_3m = safe_fetch_float(metrics.get('k_3m', 50.0), 50.0)
        d_3m = safe_fetch_float(metrics.get('d_3m', 50.0), 50.0)
        k_3m_prev = safe_fetch_float(metrics.get('k_3m_prev', 50.0), 50.0)
        k_15m = safe_fetch_float(indicators.get('stoch_k_15m', 50.0), 50.0)
        d_15m = safe_fetch_float(indicators.get('stoch_d_15m', 50.0), 50.0)
        k_1h = safe_fetch_float(indicators.get('stoch_k_1h', 50.0), 50.0)
        d_1h = safe_fetch_float(indicators.get('stoch_d_1h', 50.0), 50.0)
        k_4h = safe_fetch_float(indicators.get('stoch_k_4h', 50.0), 50.0)
        d_4h = safe_fetch_float(indicators.get('stoch_d_4h', 50.0), 50.0)
        if (target_side == 'LONG' and k_3m < d_3m) or (target_side == 'SHORT' and k_3m > d_3m) : return 0.0
        score_components = {}
        total_weight = 0.0
        final_score = 0.0
        relative_volume_1h = safe_fetch_float(indicators.get('relative_volume_1h', 0.0), 0.0)
        liquidity_score = min(1.0, relative_volume_1h / 2.0)
        score_components['liquidity'] = (liquidity_score, 0.2)
        market_sentiment_local = safe_fetch_float(indicators.get('0market_sentiment_local', 0.0), 0.0)
        ranking_points = safe_fetch_float(indicators.get('0ranking_points', 0.0), 0.0)
        ranking_points_global = safe_fetch_float(indicators.get('0ranking_points_global', 0.0), 0.0)
        is_top_sentiment = indicators.get('0is_top_sentiment', False)
        sentiment_classification = str(indicators.get('0sentiment_classification') or '')
        if target_side == 'LONG':
            local_sentiment_score = max(0.0, min(1.0, (market_sentiment_local + 100) / 200))
            ranking_score = max(0.0, min(1.0, ranking_points / 100.0))
            global_ranking_score = max(0.0, min(1.0, ranking_points_global / 100.0))
            top_sentiment_bonus = 0.2 if is_top_sentiment else 0.0
            bullish_bonus = 0.17 if 'BULLISH' in sentiment_classification.upper() else 0.0
            trend_score = (local_sentiment_score * 0.4 + ranking_score * 0.3 + global_ranking_score * 0.1 + top_sentiment_bonus + bullish_bonus)
            trend_score = min(1.0, trend_score)
        else: # SHORT
            local_sentiment_score = max(0.0, min(1.0, (100 - market_sentiment_local) / 200))
            ranking_score = max(0.0, min(1.0, (100 - ranking_points) / 100.0))
            global_ranking_score = max(0.0, min(1.0, (100 - ranking_points_global) / 100.0))
            bearish_bonus = 0.17 if 'BEARISH' in sentiment_classification.upper() else 0.0
            trend_score = (local_sentiment_score * 0.4 + ranking_score * 0.3 + global_ranking_score * 0.1 + bearish_bonus)
            trend_score = min(1.0, trend_score)
        score_components['trend'] = (trend_score, 0.5)
        atr_1h = safe_fetch_float(indicators.get('atr_1h', 0.0), 0.0)
        volatility_pct = (atr_1h / current_price) if current_price > 0 else 0.02
        volatility_score = max(0.0, min(1.0, 1.0 - (volatility_pct / 0.05)))
        score_components['volatility'] = (volatility_score, 0.17)
        correlation_penalty = 0.0
        if losing_symbol.startswith('BTC') and candidate_symbol.startswith('BTC'):
            correlation_penalty = 0.5
        correlation_score = 1.0 - correlation_penalty
        score_components['correlation'] = (correlation_score, 0.17)
        candidate_position_key = construct_position_key(account_key,candidate_symbol,target_side)
        candidate_position = await self.tracker_manager.get_position(candidate_position_key)
        for component, (value, weight) in score_components.items():
            final_score += value * weight
            total_weight += weight
        if total_weight > 0:
            final_score /= total_weight
        if target_side=='LONG':
            if k_15m < d_15m: final_score *= 0.2 # 15m still dropping = too early for LONG
            if k_1h < d_1h: final_score += 0.2 # 1h oversold = ripe for bounce (mean-reversion)
            if k_4h < d_4h: final_score += 0.1 # 4h oversold = stronger bounce setup
            if k_15m > 80: final_score += 0.4 # Strong upward momentum already
        else: # SHORT
            if k_15m > d_15m: final_score *= 0.2 # 15m still rising = too early for SHORT
            if k_1h > d_1h: final_score += 0.2 # 1h overbought = ripe for drop (mean-reversion)
            if k_4h > d_4h: final_score += 0.1 # 4h overbought = stronger reversal setup
            if k_15m < 20: final_score += 0.4 # Strong downward momentum already
        # Rebound detection for SHORT candidates (biggest losers that have had a bounce)
        if target_side == 'SHORT':
            # Updated Rebound Logic:
            # 1. Coming down (k < d) on short timeframe (1m or 3m)
            # 2. AFTER k1 and k3 have been over 80
            # 3. k_15m < k_15m_prev
            k_15m_prev = safe_fetch_float(indicators.get('k_15m_prev', 50.0), 50.0)
            # Check if k1 and k3 have been over 80 recently (we use current k as a proxy for "have been over 80" if it's still high or just crossed down)
            # Or more accurately, if they ARE/WERE over 80 and now k < d.
            over_80 = (k_1m > 80 or k_1m_prev > 80) and (k_3m > 80 or k_3m_prev > 80)
            coming_down = (k_1m < d_1m) or (k_3m < d_3m)
            k15m_dropping = k_15m < k_15m_prev
            is_rebound = over_80 and coming_down and k15m_dropping
            if is_rebound:
                final_score += 0.3 # Bonus for having a rebound so it can go down quickly
            else:
                # If it doesn't meet the rebound criteria, it might just be going up.
                # We penalize it to avoid catching a falling knife that's actually a rocket.
                final_score *= 0.5
        if candidate_position and candidate_position.positionAmt != 0:
            pos_value = abs(candidate_position.positionAmt) * (candidate_position.mark_price if candidate_position.mark_price > 0 else current_price)
            size_ratio = pos_value / config.START_POSITION_SIZE if config.START_POSITION_SIZE > 0 else 0
            if size_ratio > 3.0:
                final_score *= 0.3
            elif size_ratio > 1.5:
                final_score *= 0.6
            # Graduated gain penalty instead of hard exclusion
            if candidate_position.gain < -2.0:
                final_score *= 0.3
            elif candidate_position.gain < 0:
                final_score *= 0.7
            # Small gains (0-0.3%) get no penalty
        if final_score <= 0: final_score = 0.00000001
        return final_score
    async def compute_hedge_size(self, account_key: str, losing_value_usd: float, target_symbol: str, hedge_multiplier: float = 1.0, ratio: float = 1.0, losing_side: str = "") -> float:
        # OVERHAUL: We always want 100% of the losing value covered.
        base_notional = losing_value_usd * 1.0
        metrics, indicators, k_3m, d_3m, _, _, is_data_fresh = await self.data_manager.get_hot_state(target_symbol)
        current_price, _ = await self.data_manager.get_fresh_price(target_symbol)
        # We don't apply volatility or sentiment reductions if we want "entire value" coverage
        # But we keep them as 1.0 multipliers for code structure
        volatility_adjustment = 1.0
        market_adjustment = 1.0
        adjusted_notional = base_notional * volatility_adjustment * market_adjustment
        if is_hedge_account(self.config, account_key) or self.config.REV_MODE:
            # Minimum is the original losing value
            adjusted_notional = max(adjusted_notional, losing_value_usd)
        adjusted_notional = min(adjusted_notional, self.max_hedge_notional)
        return adjusted_notional
    async def cleanup_infinite_hedges(self, account_key: str):
            """Removes hedge records that reference other hedges"""
            async with self.tracker_manager._hedges_lock:
                clean_list = []
                for h in self.tracker_manager.active_hedges:
                    if h.get('account') != account_key:
                        clean_list.append(h)
                        continue
                    ts_str = str(h.get('timestamp', ''))
                    try:
                        if 'T' in ts_str: ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00')).timestamp()
                        else: ts = float(ts_str)
                        if time.time() - ts > 86400: continue
                    except: pass
                    clean_list.append(h)
                self.tracker_manager.active_hedges = clean_list
    async def promote_hedge_to_standard_position(self, account_key: str, position_key: str):
        """
        Converts a Hedge position into a Standard Active Position.
        Removes 'is_hedge', 'hedge_for', and 'hedge_id' flags.
        """
        async with self.tracker_manager._exit_candidates_lock:
            if position_key in self.tracker_manager.exit_candidates:
                data = self.tracker_manager.exit_candidates[position_key]
                # Remove Hedge Flags
                data.pop('is_hedge', None)
                data.pop('hedge_for', None)
                data.pop('hedge_id', None)
                data.pop('losing_position_key', None)
                # Ensure status is active
                data['status'] = 'active'
                data['promotion_time'] = datetime.now(timezone.utc).isoformat()
                data['last_reason'] = "PROMOTED_FROM_HEDGE_TO_WINNER"
                self.tracker_manager.exit_candidates[position_key] = data
                self.tracker_manager._exit_candidates_dirty[account_key] = True
        # Remove from Active Hedges List
        async with self.tracker_manager._hedges_lock:
            self.tracker_manager.active_hedges = [ h for h in self.tracker_manager.active_hedges if h.get('position_key') != position_key ]
        # Force Save
        await self.tracker_manager.save_tracker(account_key, force=True)
        logger.info(f"🎓 [PROMOTION] {position_key} graduated from HEDGE to STANDARD POSITION.")
    async def monitor_and_manage_hedges(self, account_key: str) -> List[Dict[str, Any]]:
        actions_taken = []
        if not self.config.HEDGE_MODE and not self.config.REV_MODE: return actions_taken
        # 1. Snapshot active hedges to avoid lock contention
        async with self.tracker_manager._hedges_lock:
            active_hedges = [h.copy() for h in self.tracker_manager.active_hedges if h.get('account') == account_key]
        positions = self.positions_service.positions_by_account.get(account_key, {})
        for record in active_hedges:
            try:
                # --- A. Validate Existence ---
                hedge_key = record.get('position_key')
                original_key = record.get('losing_position_key')
                if not hedge_key: continue
                hedge_pos = positions.get(hedge_key)
                # Cleanup: If hedge position physically gone/empty, remove record & release slot
                if not hedge_pos or abs(safe_fetch_float(hedge_pos.positionAmt, 0)) < 0.0001:
                    async with self.tracker_manager._hedges_lock:
                        self.tracker_manager.active_hedges = [ h for h in self.tracker_manager.active_hedges if h.get('id') != record.get('id') ]
                    # Release registry slot if applicable
                    if hasattr(self, 'registry') and self.registry:
                        self.registry.release_hedge_slot(account_key, record.get('symbol'))
                    continue
                # --- B. Get Data ---
                symbol = hedge_pos.symbol
                # CRITICAL FIX: Properly fetch and unpack data
                # We use get_hot_state to get metrics, but we also need the full indicators dict
                metrics, indicators, k_3m, d_3m, _, _, is_fresh = await self.data_manager.get_hot_state(symbol)
                if not indicators:
                    # Try explicit fetch if hot state incomplete
                    indicators, _ = await self.data_manager.get_fresh_indicators(symbol)
                if not indicators: continue # Skip if no data
                # Parse specific indicators needed for logic
                k_1m = safe_fetch_float(metrics.get('stoch_k_1m', 50.0))
                d_1m = safe_fetch_float(metrics.get('stoch_d_1m', 50.0))
                k_15m = safe_fetch_float(indicators.get('stoch_k_15m', 50.0))
                d_15m = safe_fetch_float(indicators.get('stoch_d_15m', 50.0))
                k_1h = safe_fetch_float(indicators.get('stoch_k_1h', 50.0))
                d_1h = safe_fetch_float(indicators.get('stoch_d_1h', 50.0))
                k_4h = safe_fetch_float(indicators.get('stoch_k_4h', 50.0))
                ha_1h = indicators.get('ha_1h', 'neutral')
                hedge_amt = abs(safe_fetch_float(hedge_pos.positionAmt, 0))
                current_price = safe_fetch_float(hedge_pos.mark_price, 0)
                if current_price <= 0:
                    current_price = safe_fetch_float(indicators.get('current_price', 0))
                if current_price <= 0: continue
                hedge_gain = safe_fetch_float(getattr(hedge_pos, 'gain', 0.0), 0.0)
                hedge_prev_gain = safe_fetch_float(getattr(hedge_pos, 'prev_gain', 0.0), 0.0)
                is_hedge_long = (hedge_pos.position_side == 'LONG')
                # --- C. HEDGE SAFETY KILL (Stop Loss) ---
                gain_dropping_fast = (hedge_prev_gain > 0.5 and hedge_gain < hedge_prev_gain * 0.5)
                if hedge_gain < -0.15 or gain_dropping_fast:
                    reason_detail = f"gain={hedge_gain:.2f}%" if hedge_gain < -0.15 else f"decay={hedge_prev_gain:.2f}%->{hedge_gain:.2f}%"
                    logger.warning(f"💀 [HEDGE_KILL] {hedge_key} failing ({reason_detail}). Executing KILL.")
                    success, _ = await execute_trade_wrapper( self.trade_manager, self.tracker_manager, self, account_key, hedge_key, hedge_amt, 'QUICK_CLOSE', current_price, hedge_amt, f"HEDGE_KILL_FAILING_{reason_detail}", is_hedge=True, data_manager=self.data_manager )
                    if success:
                        if hasattr(self, 'registry') and self.registry:
                            self.registry.release_hedge_slot(account_key, symbol)
                        async with self.tracker_manager._hedges_lock:
                            self.tracker_manager.active_hedges = [h for h in self.tracker_manager.active_hedges if h.get('id') != record.get('id')]
                        await self.tracker_manager.nuke_hedge_key(account_key, hedge_key)
                        actions_taken.append({'action': 'kill_hedge', 'key': hedge_key})
                        continue
                # --- D. ORPHAN CHECK ---
                is_orphan = False
                cand = await self.tracker_manager.get_exit_candidate(hedge_key)
                if cand and cand.get('hedge_state') == 'ORPHANED':
                    is_orphan = True
                else:
                    # Double check if parent is physically dead
                    if original_key:
                        original_pos = positions.get(original_key)
                        if not original_pos or abs(safe_fetch_float(original_pos.positionAmt, 0)) == 0:
                            is_orphan = True
                            original_pos = None
                    else:
                        original_pos = None
                # --- VALUE MATCH CHECK (EMERGENCY EXIT) ---
                if not is_orphan and original_pos:
                    orig_val = abs(safe_fetch_float(original_pos.positionAmt, 0)) * safe_fetch_float(original_pos.mark_price, 0)
                    hedge_val = hedge_amt * current_price
                    if orig_val > 10.0: # Only check if original has meaningful value
                        coverage_ratio = hedge_val / orig_val
                        if coverage_ratio < 0.80 or coverage_ratio > 1.30:
                            logger.critical(f"🚨 [HEDGE_VALUE_MISMATCH] {hedge_key} covers {coverage_ratio:.1%} of {original_key}. Emergency Exit triggered.")
                            should_close_hedge = True
                            close_reason_full = f"VALUE_MISMATCH_{coverage_ratio:.2f}"
                # --- E. LOGIC EVALUATION ---
                should_close_hedge = False
                trigger_recovery_augment = False
                close_reason_full = ""
                now_ts = time.time()
                exact_tick_ts = metrics.get('_tick_ts', 0)
                true_lag = now_ts - exact_tick_ts
                # 1. ORPHAN CLEANUP LOGIC (Graceful Exit on Technicals)
                if is_orphan:
                    # Long Hedge: Close if Momentum Turns Down
                    if is_hedge_long:
                        if (k_1m < d_1m and true_lag < 15.0) or (k_3m < d_3m):
                            should_close_hedge = True
                            close_reason_full = f"ORPHAN_CLEANUP_L_k1:{k_1m:.0f}<{d_1m:.0f}"
                    # Short Hedge: Close if Momentum Turns Up
                    else:
                        if (k_1m > d_1m and true_lag < 15.0) or (k_3m > d_3m):
                            should_close_hedge = True
                            close_reason_full = f"ORPHAN_CLEANUP_S_k1:{k_1m:.0f}>{d_1m:.0f}"
                # 2. STANDARD MANAGEMENT (Parent is Alive)
                elif original_pos:
                    is_original_long = (original_pos.position_side == 'LONG')
                    o_entry = safe_fetch_float(getattr(original_pos, 'entry_price', 0.0))
                    o_mark = safe_fetch_float(getattr(original_pos, 'mark_price', 0.0))
                    o_pnl = -99.9
                    if o_entry > 0:
                        o_pnl = ((o_mark - o_entry) / o_entry * 100) if is_original_long else ((o_entry - o_mark) / o_entry * 100)
                    o_pnl = -99.9
                    if o_entry > 0:
                        o_pnl = ((o_mark - o_entry) / o_entry * 100) if is_original_long else ((o_entry - o_mark) / o_entry * 100)

                    # a) Parent Recovered? -> Close Original & Hedge
                    if o_pnl > 0.35: # Gain of 0.35% is enough to exit both safely
                        logger.info(f"⚖️ [HEDGE_MGMT] {original_key} recovered ({o_pnl:.2f}%). Closing original.")
                        orig_amt = abs(safe_fetch_float(original_pos.positionAmt, 0))
                        asyncio.create_task(execute_trade_wrapper(self.trade_manager, self.tracker_manager, self, account_key, original_key, orig_amt, 'CLOSE', o_mark, 0.0, f"HEDGE_ENGINE_ORIGINAL_RECOVERY_{o_pnl:.2f}", is_hedge=False, data_manager=self.data_manager))
                        continue # Cleanup will handle the hedge
                    
                    # b) Hedge Profit Taking (Stalling)? -> Close Hedge
                    elif hedge_gain > 15.0:
                        stalling = False
                        if is_hedge_long and k_3m < d_3m: stalling = True
                        if not is_hedge_long and k_3m > d_3m: stalling = True
                        
                        if stalling:
                            should_close_hedge = True
                            close_reason_full = f"HEDGE_PROFIT_TAKE_g{hedge_gain:.1f}%"
                    
                    # c) Momentum Reversal (The "Swing") -> Close Hedge AND Augment Parent
                    else:
                        st_momentum = False
                        
                        if is_original_long:
                            # Parent Long, Hedge Short. Close Short if Market Turns UP.
                            # 1m Up (Fresh) AND 3m Up AND (15m Up or Oversold)
                            st_momentum = (k_1m > d_1m and true_lag < 15.0) and k_3m > d_3m and (k_15m > d_15m or k_15m < 20)
                            
                            # HTF Permission
                            c1 = k_1h > d_1h 
                            c2 = k_1h < 20 
                            c3 = ha_1h == 'green'
                            c4 = not (k_4h > 85 and k_4h < safe_fetch_float(indicators.get('stoch_k_4h_prev', 0)))
                            htf_permission = (c1 or c2 or c3) and c4
                            
                            if st_momentum and htf_permission:
                                should_close_hedge = True
                                trigger_recovery_augment = True
                                close_reason_full = "MOMENTUM_FLIP_LONG"
                        else:
                            # Parent Short, Hedge Long. Close Long if Market Turns DOWN.
                            st_momentum = (k_1m < d_1m and true_lag < 15.0) and k_3m < d_3m and (k_15m < d_15m or k_15m > 80)
                            
                            c1 = k_1h < d_1h
                            c2 = k_1h > 80
                            c3 = ha_1h == 'red'
                            c4 = not (k_4h < 15 and k_4h > safe_fetch_float(indicators.get('stoch_k_4h_prev', 0)))
                            htf_permission = (c1 or c2 or c3) and c4

                            if st_momentum and htf_permission:
                                should_close_hedge = True
                                trigger_recovery_augment = True
                                close_reason_full = "MOMENTUM_FLIP_SHORT"

                # --- F. EXECUTION ---
                if should_close_hedge and current_price > 0:
                    logger.info(f"⚖️ [HEDGE_MGMT] Closing {hedge_key}: {close_reason_full}")
                    
                    success, _ = await execute_trade_wrapper(
                        self.trade_manager, self.tracker_manager, self,
                        account_key, hedge_key, hedge_amt, 'QUICK_CLOSE', current_price, hedge_amt,
                        f"HEDGE_EXIT_{close_reason_full}", 
                        is_hedge=True, hedge_for=original_key,
                        override_qty=hedge_amt, data_manager=self.data_manager
                    )
                    
                    if success:
                        if hasattr(self, 'registry') and self.registry:
                            self.registry.release_hedge_slot(account_key, symbol)

                        # Remove from active list
                        async with self.tracker_manager._hedges_lock:
                            self.tracker_manager.active_hedges = [
                                h for h in self.tracker_manager.active_hedges 
                                if h.get('id') != record.get('id')
                            ]
                        await self.tracker_manager.nuke_hedge_key(account_key, hedge_key)
                        actions_taken.append({'action': 'close_hedge', 'key': hedge_key})
                        
                        # Trigger Recovery Augment on Parent
                        if trigger_recovery_augment and original_pos:
                            orig_amt = abs(safe_fetch_float(original_pos.positionAmt, 0))
                            recovery_qty = min(hedge_amt * 1.5, orig_amt)
                            
                            max_pos_size = self.config.MAX_POSITION_SIZE / current_price 
                            if (orig_amt + recovery_qty) > max_pos_size:
                                recovery_qty = max(0, max_pos_size - orig_amt)
                            
                            if recovery_qty > 0:
                                logger.info(f"🚀 [RECOVERY_TRIGGER] {original_key}: Firing AUGMENT for {recovery_qty:.6f}")
                                await execute_trade_wrapper(
                                    self.trade_manager, self.tracker_manager, self,
                                    account_key, original_key, orig_amt, 'AUGMENT', current_price, recovery_qty,
                                    f"RECOVERY_AUGMENT_{close_reason_full}", 
                                    already_locked=False, is_hedge=False,
                                    data_manager=self.data_manager
                                )

            except Exception as e:
                logger.error(f"[HEDGE_MON] Error managing hedge {record.get('id')}: {e}", exc_info=True)
                continue        
        
        return actions_taken

    async def find_hedge_candidates(self, account_key: str, losing_symbol: str, losing_side: str) -> List[Tuple[str, float, str]]:
        if not self.registry:
            logger.error(f"❌ [HEDGE_ERROR] Registry not initialized!")
            return []
        now = time.time()
        self._hedge_cooldowns = {s: t for s, t in self._hedge_cooldowns.items() if (now - t) < getattr(self, 'HEDGE_COOLDOWN_SECONDS', 420)}
        cooldown_symbols = list(self._hedge_cooldowns.keys())
        if cooldown_symbols:
            logger.info(f"[HedgeEngine] Skipping {len(cooldown_symbols)} symbols on cooldown")
        target_side = 'SHORT' if losing_side == 'LONG' else 'LONG'
        exclude_list = [losing_symbol] + cooldown_symbols
        hottest_sym, score = self.registry.get_hottest_hedge(account_key, target_side, exclude_symbols=exclude_list)
        candidates = []
        if hottest_sym:
            logger.info(f"🛡️ [HEDGE_MATCH] Selected {hottest_sym} (Score: {score:.1f})")
            candidates.append((hottest_sym, score, target_side))
        fallback_symbols = ["BTCDOMUSDT", "PAXGUSDT", "XAGUSDT"]
        for fs in fallback_symbols:
            if fs == losing_symbol: continue
            if any(c[0] == fs for c in candidates): continue
            rating_opp = self.registry.get_rating(fs, target_side)
            score_opp = rating_opp[0] if rating_opp and rating_opp[0] > 0 else 5.0
            rating_same = self.registry.get_rating(fs, losing_side)
            score_same = rating_same[0] if rating_same and rating_same[0] > 0 else 5.0
            if score_same > score_opp + 5.0:
                 candidates.append((fs, score_same, losing_side))
            else:
                 candidates.append((fs, score_opp, target_side))
        return candidates
    
    async def execute_dual_hedge(self, account_key: str, losing_position_key: str, losing_symbol: str, losing_side: str, losing_value_usd: float, dry_run: bool = False) -> Dict[str, Any]:
        async with self._get_account_lock(account_key):            
            results = { 'elected_symbol': {'status': 'pending'}, 'actual_symbol': {'status': 'pending'}, 'overall_status': 'pending' }
            candidates = await self.find_hedge_candidates(account_key=account_key, losing_symbol=losing_symbol, losing_side=losing_side)
            
            target_symbol = None
            candidate_score = 0
            elected_success = False
            
            if not candidates:
                logger.info(f"[HedgeEngine] No viable candidates found for {losing_symbol}")
                results['elected_symbol'] = {'status': 'failed', 'reason': 'NO_CANDIDATES'}
            else:
                for sym, score, side in candidates:
                    logger.info(f"🛡️ [HEDGE_ATTEMPT] Trying candidate: {sym} (Score: {score:.2f}, Side: {side})")
                    
                    target_symbol = sym
                    candidate_score = score
                    position_side = side
                    
                    hedge_notional = await self.compute_hedge_size(account_key, losing_value_usd, target_symbol, ratio=self.elected_symbol_ratio, losing_side=losing_side)
                    if hedge_notional <= 0: continue

                    target_price, _ = await self.data_manager.get_fresh_price(target_symbol)
                    if target_price <= 0: continue

                    raw_quantity = hedge_notional / target_price
                    quantity = await self.trade_manager.round_quantity_to_lot(target_symbol, raw_quantity)
                    
                    min_val = 5.5
                    if (quantity * target_price) < min_val:
                        quantity = min_val / target_price

                    if quantity > 0:
                        position_key = construct_position_key(account_key, target_symbol, position_side)
                        
                        position = await self.tracker_manager.get_position(position_key)
                        if not position: 
                            position = self.tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(position_key)
                        
                        positionAmt_abs = position.positionAmt if position else 0.0
                        action_type = 'OPEN' if positionAmt_abs == 0 else 'AUGMENT'
                        
                        success, failure_reason = await execute_trade_wrapper(
                            trade_manager=self.trade_manager, 
                            tracker_manager=self.tracker_manager,
                            hedge_engine=self, 
                            account_key=account_key, 
                            position_key=position_key, 
                            positionAmt=positionAmt_abs, 
                            action=action_type, 
                            current_price=target_price, 
                            qty=quantity, 
                            reason=f'HEDGE_ELECTED_{losing_symbol}_{losing_side}', 
                            override_qty=quantity, 
                            already_locked=False, 
                            is_hedge=True,  
                            hedge_for=losing_position_key,  
                            data_manager=self.data_manager
                        )

                        if success:
                            hedge_record = {
                                'type': 'HEDGE_ELECTED_OPEN',
                                'status': 'executed',
                                'account': account_key,
                                'position_key': position_key, 
                                'quantity': quantity,
                                'initial_quantity': quantity, 
                                'price': target_price,
                                'entry_price': target_price, 
                                'target_symbol': target_symbol,
                                'symbol': target_symbol, 
                                'losing_symbol': losing_symbol,
                                'losing_position_key': losing_position_key,
                                'hedge_for': losing_position_key, 
                                'losing_side': losing_side,
                                'position_side': position_side,
                                'notional_usd': hedge_notional,
                                'candidate_score': candidate_score,
                                'ratio': self.elected_symbol_ratio,
                                'timestamp': time.time(),
                                'is_hedge': True,
                                'hedge_id': f"hedge_{int(time.time() * 1000)}",
                                'reason': f'HEDGE_ELECTED_{losing_symbol}_{losing_side}'
                            }
                            await self.persist_hedge_record(account_key, hedge_record)
                            results['elected_symbol'] = {'status': 'success', 'record': hedge_record}
                            elected_success = True
                            logger.info(f"✅ [HEDGE_SUCCESS] Covered with {target_symbol}")
                            break 
                        else:
                            logger.warning(f"⚠️ [HEDGE_NEXT] {target_symbol} failed: {failure_reason}. Trying next...")
                            await self.tracker_manager.set_trade_cooldown(position_key)
            
            if not elected_success:
                results['elected_symbol']['status'] = 'failed'

            # --- 2. ACTUAL SYMBOL HEDGE (The Safety Net) ---
            # Logic: If Elected failed (for ANY reason), FORCE a Self-Hedge at 1.0 Ratio.
            
            force_self_hedge = not elected_success
            effective_ratio = self.actual_symbol_ratio
            
            if force_self_hedge:
                logger.warning(f"🛡️ [HEDGE_FALLBACK] Elected hedge failed/missing. Forcing SAME-SYMBOL hedge for {losing_symbol} (Ratio 1.0)")
                effective_ratio = 1.0
            
            if effective_ratio > 0.0: 
                current_price, _ = await self.data_manager.get_fresh_price(losing_symbol)
                if current_price > 0:
                    # Calculate Self Hedge Size
                    hedge_notional_actual = await self.compute_hedge_size(
                        account_key, losing_value_usd, losing_symbol, ratio=effective_ratio
                    )
                    
                    if hedge_notional_actual > 0:
                        raw_quantity = hedge_notional_actual / current_price
                        quantity = await self.trade_manager.round_quantity_to_lot(losing_symbol, raw_quantity)
                        
                        # Min Size Check
                        min_val = 5.5
                        if (quantity * current_price) < min_val:
                            quantity = min_val / current_price

                        if quantity > 0:
                            hedge_side = 'SHORT' if losing_side == 'LONG' else 'LONG'
                            hedge_position_key = construct_position_key(account_key, losing_symbol, hedge_side)
                            
                            # Check if we already have the hedge open (don't double dip)
                            position = await self.tracker_manager.get_position(hedge_position_key)
                            if not position: 
                                position = self.tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(hedge_position_key)
                            
                            positionAmt_abs = position.positionAmt if position else 0.0
                            
                            # If we are forcing a 1.0 hedge, but we already have 0.5 open, we only add the difference?
                            # For simplicity and safety in a panic: 
                            # If we already have a significant hedge (>50% of target), maybe skip or add small?
                            # Here we rely on execute_trade_wrapper to just execute the order.
                            
                            if dry_run:
                                results['actual_symbol'] = {'status': 'dry_run', 'symbol': losing_symbol}
                            else:
                                success, failure_reason = await execute_trade_wrapper( 
                                    trade_manager=self.trade_manager,
                                    tracker_manager=self.tracker_manager,
                                    hedge_engine=self,
                                    account_key=account_key,
                                    position_key=hedge_position_key,
                                    positionAmt=positionAmt_abs,
                                    action='OPEN', # Or Augment
                                    current_price=current_price,
                                    qty=quantity,
                                    reason=f"HEDGE_ACTUAL_OPEN_{losing_symbol}_{hedge_side}",
                                    override_qty=quantity, 
                                    already_locked=False,
                                    is_hedge=True,
                                    hedge_for=losing_position_key,
                                    data_manager=self.data_manager
                                )
                                
                                if success:
                                    hedge_record = {
                                        'type': 'HEDGE_SAME_SYMBOL_OPEN',
                                        'status': 'executed',
                                        'account': account_key,
                                        'position_key': hedge_position_key, 
                                        'target_symbol': losing_symbol,
                                        'symbol': losing_symbol,
                                        'losing_symbol': losing_symbol,
                                        'losing_position_key': losing_position_key,
                                        'losing_side': losing_side,
                                        'position_side': hedge_side,
                                        'quantity': quantity,
                                        'price': current_price,
                                        'notional_usd': hedge_notional_actual,
                                        'candidate_score': 0,
                                        'ratio': effective_ratio,
                                        'timestamp': time.time(),
                                        'is_hedge': True,  
                                        'hedge_id': f"hedge_act_{int(time.time() * 1000)}" 
                                    }
                                    await self.persist_hedge_record(account_key, hedge_record)
                                    results['actual_symbol'] = {'status': 'success', 'record': hedge_record}
                                else:
                                    results['actual_symbol'] = {'status': 'failed', 'reason': failure_reason}
            else:
                results['actual_symbol'] = {'status': 'skipped', 'reason': 'ratio_zero'}

            # --- 3. FINAL EVALUATION ---
            actual_status = results['actual_symbol'].get('status')
            
            # If EITHER worked, we are good.
            if elected_success or actual_status == 'success':
                results['overall_status'] = 'success'
                logger.info(f"[HedgeEngine] Hedge operation successful (Status: {results['overall_status']})")
                return results

            else:
                # --- LAST RESORT: REDUCTION ---
                # If elected failed AND self-hedge failed, we MUST reduce the loser.
                results['overall_status'] = 'failed'
                logger.warning(f"🛡️ [HEDGE_CRITICAL] ALL Hedges failed. Executing FALLBACK REDUCTION on {losing_symbol}.")
                
                losing_pos = await self.tracker_manager.get_position(losing_position_key)
                if not losing_pos: 
                    losing_pos = self.tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(losing_position_key)
                
                if losing_pos:
                    current_amt = abs(safe_fetch_float(getattr(losing_pos, 'positionAmt', 0.0), 0.0))
                    current_price, _ = await self.data_manager.get_fresh_price(losing_symbol)
                    
                    # Reduce by 35%
                    target_reduce = current_amt * 0.35 
                    if current_amt > 0:
                        success, reason = await execute_trade_wrapper(
                            trade_manager=self.trade_manager, 
                            tracker_manager=self.tracker_manager,
                            hedge_engine=self,
                            account_key=account_key, 
                            position_key=losing_position_key, 
                            positionAmt=current_amt, 
                            action='REDUCE', 
                            current_price=current_price, 
                            qty=target_reduce, 
                            reason=f"HEDGE_FAILED_FALLBACK_REDUCE",
                            already_locked=False, 
                            is_hedge=False,
                            override_qty=target_reduce,
                            data_manager=self.data_manager  
                        )
                        results['reduction_fallback'] = {'status': 'success' if success else 'failed', 'reason': reason}
                
                return results



    # async def execute_hedge_with_fallback(self, account_key, losing_position_key, losing_symbol, losing_side, losing_value_usd, losing_position=None, dry_run=False):
    #     async with self._get_account_lock(account_key):
    #         logger.info(f"[HHHHedgeEn gine] Starting hedge with fallback for {losing_position_key} (${losing_value_usd:.2f})")
    #         dual_result = await self.execute_dual_hedge( account_key=account_key,losing_position_key=losing_position_key, losing_symbol=losing_symbol, losing_side=losing_side,losing_value_usd=losing_value_usd,dry_run=dry_run)
    #         if dual_result.get('overall_status') in ['success', 'partial']:
    #             logger.info(f"[HHHHedge Engine] ✅ Dual hedge succeeded for {losing_position_key}")
    #             return {'method': 'dual_hedge', 'result': dual_result, 'status': 'success'}
    #         logger.info(f"[HHHHedg eEngine] Dual hedge failed, trying same-symbol hedge for {losing_position_key}")
    #         if losing_position is None:
    #             positions = self.positions_service.positions_by_account.get(account_key, {}) or self.trade_manager.positions_by_account.get(account_key, {})
    #             losing_position = positions.get(losing_position_key)
    #         # if losing_position:TEMP OUT
    #         #     positionAmt_abs = abs(safe_fetch_float(getattr(losing_position, 'positionAmt', 0.0), 0.0))
    #         #     if positionAmt_abs > 0:
    #         #         current_price, _ = await self.data_manager.get_fresh_price(losing_symbol)
    #         #         if current_price > 0:
    #         #             same_symbol_success = await self.execute_same_symbol_hedge(account_key=account_key, origin_position=losing_position, symbol=losing_symbol, origin_side=losing_side, qty=positionAmt_abs, current_price=current_price  )
    #         #             if same_symbol_success:
    #         #                 logger.info(f"[HHHHedgeEn gine] ✅ Same-symbol hedge succeeded for {losing_position_key}")
    #         #                 return {'method': 'same_symbol_hedge', 'status': 'success'}
    #         # logger.info(f"[HHHHedgeEn gine] Same-symbol hedge failed, reducing position for {losing_position_key}")
    #         if losing_position:
    #             positionAmt_abs = abs(safe_fetch_float(getattr(losing_position, 'positionAmt', 0.0), 0.0))
    #             if positionAmt_abs > 0:
    #                 current_price, _ = await self.data_manager.get_fresh_price(losing_symbol)
    #                 if current_price > 0:
    #                     reduction_qty = positionAmt_abs * 0.5
    #                     reduction_qty = await self.trade_manager.round_quantity_to_lot(losing_symbol, reduction_qty)
    #                     if reduction_qty > 0:
    #                         logger.info(f"[ ] 🔻 Reducing position by 50% as final fallback: {reduction_qty:.6f} of {losing_symbol}")
    #                         success, failure_reason = await execute_trade_wrapper(trade_manager=self.trade_manager,tracker_manager=self.tracker_manager,hedge_engine=self,account_key=account_key,position_key=losing_position_key,positionAmt=positionAmt_abs,action='REDUCE',current_price=current_price,qty=reduction_qty,reason=f"HEDGE_FALLBACK_REDUCE_{losing_symbol}_{losing_side}",already_locked=False,is_hedge=False,override_qty=reduction_qty,data_manager=self.data_manager )
    #                         if success:
    #                             logger.info(f"[HedgeE ngine] ✅ Position reduction succeeded for {losing_position_key}")
    #                             return {'method': 'position_reduction', 'status': 'success', 'reduction_qty': reduction_qty}
    #                         else:
    #                             logger.error(f"[Hedge Engine] ❌ Position reduction failed, reducing to MIN_QTY as last resort: {failure_reason}")
    #                             base_min = self.trade_manager.min_qty.get(losing_symbol, 0.0)
    #                             cost_min_qty = getattr(config, 'MIN_POSITION_SIZE', 5.50) / current_price
    #                             effective_min_qty = max(base_min, cost_min_qty)
    #                             min_qty_reduction = positionAmt_abs - effective_min_qty
    #                            # min_qty_reduction = await self.trade_manager.round_quantity_to_lot(losing_symbol, min_qty_reduction)
                                
    #                             if min_qty_reduction > 0 and min_qty_reduction < positionAmt_abs:
    #                                 min_reduce_success, min_reduce_reason = await execute_trade_wrapper(trade_manager=self.trade_manager,tracker_manager=self.tracker_manager,hedge_engine=self,account_key=account_key,position_key=losing_position_key,positionAmt=positionAmt_abs,action='REDUCE',current_price=current_price,qty=min_qty_reduction,reason=f"HEDGE_FALLBACK_MIN_QTY_{losing_symbol}_{losing_side}",already_locked=False,is_hedge=False,override_qty=min_qty_reduction,data_manager=self.data_manager)
    #                                 if min_reduce_success:
    #                                     logger.info(f"[Hedg eEngine] ✅ Reduced to MIN_QTY ({effective_min_qty:.6f}) as last resort for {losing_position_key}")
    #                                     return {'method': 'min_qty_reduction', 'status': 'success', 'reduction_qty': min_qty_reduction, 'remaining_qty': effective_min_qty}
    #                                 else:
    #                                     logger.error(f"[HedgeE ngine] 🚨 CRITICAL: All hedge methods including MIN_QTY reduction failed for {losing_position_key}: {min_reduce_reason}")
    #                                     return {'method': 'all_failed', 'status': 'failed', 'reduction_reason': failure_reason, 'min_qty_reason': min_reduce_reason, 'warning': 'POSITION_STILL_AT_RISK'}
    #                             else:
    #                                 logger.error(f"[HedgeEngi ne] 🚨 CRITICAL: Cannot reduce to MIN_QTY (min_qty={effective_min_qty:.6f}, current={positionAmt_abs:.6f}) for {losing_position_key}")
    #                                 return {'method': 'all_failed', 'status': 'failed', 'reduction_reason': failure_reason, 'warning': 'CANNOT_REDUCE_TO_MIN_QTY'}
    #         logger.error(f"[HedgeE ngine] ❌ All hedge methods failed for {losing_position_key} - Position not found or zero")
    #         return {'method': 'all_failed', 'status': 'failed', 'dual_result': dual_result, 'warning': 'POSITION_NOT_FOUND_OR_ZERO'}

    async def execute_same_symbol_hedge(self, account_key, origin_position, symbol, origin_side, qty, current_price):
        hedge_side = 'SHORT' if origin_side == 'LONG' else 'LONG'
        hedge_key = f"{account_key}:{symbol}_{hedge_side}"
        existing_hedge = await self.tracker_manager.get_position(hedge_key)
        if not existing_hedge: existing_hedge = self.tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(hedge_key)
        positionAmt = abs(safe_fetch_float(getattr(existing_hedge, 'positionAmt', 0.0), 0.0))
        qty = qty - positionAmt
        if qty * current_price < 5.0: return True # Already covered
        logger.info(f"🛡️ [HEDGE_SAME] Opening {hedge_side} {symbol} ({qty:.6f}) to cover {origin_side}")
        success, failure_reason = await execute_trade_wrapper( trade_manager=self.trade_manager, tracker_manager=self.tracker_manager, hedge_engine=self,   account_key=account_key,  position_key=hedge_key,  positionAmt=positionAmt, action='OPEN',    current_price=current_price,  qty=qty,    reason=f"HEDGE_PROTECT_{origin_side}_LOSS",  already_locked=False,   is_hedge=True, hedge_for=f"{account_key}:{symbol}_{origin_side}",  override_qty=qty,   data_manager=self.data_manager  )
        if success:
            hedge_record = {
                'type': 'HEDGE_SAME_SYMBOL',
                'status': 'executed',
                'account': account_key,
                'position_key': hedge_key,
                'target_symbol': symbol,
                'symbol': symbol, # Added
                'losing_symbol': symbol,
                'losing_position_key': f"{account_key}:{symbol}_{origin_side}",
                'hedge_for': f"{account_key}:{symbol}_{origin_side}", # Added
                'losing_side': origin_side,
                'position_side': hedge_side,
                'quantity': qty,
                'initial_quantity': qty, # Added
                'price': current_price,
                'entry_price': current_price, # Added
                'notional_usd': qty * current_price,
                'timestamp': time.time(),
                'is_hedge': True,
                'hedge_id': f"hedge_{int(time.time() * 1000)}",
                'reason': f"HEDGE_PROTECT_{origin_side}_LOSS"
            }
            await self.persist_hedge_record(account_key, hedge_record)
            self._hedge_cooldowns[symbol] = time.time()
            return True
        else: return False

    async def should_hedge_position(self,account_key: str,position_key: str,entry_price: float,current_price: float,is_long: bool ) -> Tuple[bool, float, str]:
        if entry_price <= 0 or current_price <= 0: return False, 0.0, "Invalid prices"
        if is_long: gain = ((current_price - entry_price) / entry_price) * 100.0
        else: gain = ((entry_price - current_price) / entry_price) * 100.0
        lower_bound = min(self.min_loss_for_hedge, self.max_loss_for_hedge)
        upper_bound = max(self.min_loss_for_hedge, self.max_loss_for_hedge)
        should_hedge = (gain <= upper_bound)
        reason = ""
        if should_hedge: reason = f"PnL {gain:.2f}% within hedge range [{self.min_loss_for_hedge}%, {self.max_loss_for_hedge}%]"
        else:
            if gain > self.max_loss_for_hedge: reason = f"PnL {gain:.2f}% above max hedge threshold"
            elif gain < self.min_loss_for_hedge: reason = f"PnL {gain:.2f}% below min hedge threshold"
        return should_hedge, gain, reason

    async def try_aggressive_pyramid( self, account_key: str, position_key: str, current_price: float  ) -> Optional[Dict[str, Any]]:
        async with self.tracker_manager._exit_candidates_lock:
            position_data = self.tracker_manager.exit_candidates.get(position_key, {})
        ak, symbol, position_side = parse_position_key(position_key)
        if not position_data: return None
        current_price, ts = await self.data_manager.get_fresh_price(symbol)
        if not current_price or current_price <= 0:  current_price, ts = await get_current_price(symbol)
        current_quantity = safe_fetch_float(position_data.get('positionAmt', 0.0), 0.0)
        average_entry_price = safe_fetch_float(position_data.get('average_entry_price', 0.0), 0.0)
        if current_quantity <= 0 or average_entry_price <= 0: return None
        metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_data_fresh = await self.data_manager.get_hot_state(symbol)
        if not isinstance(indicators, dict): indicators = {}

        k_3m = safe_fetch_float(metrics.get('k_3m', 50.0), 50.0)
        d_3m = safe_fetch_float(metrics.get('d_3m', 50.0), 50.0)
        k_15m = safe_fetch_float(indicators.get('stoch_k_15m', 50.0), 50.0)
        d_15m = safe_fetch_float(indicators.get('stoch_d_15m', 50.0), 50.0)
        k_1h = safe_fetch_float(indicators.get('stoch_k_1h', 50.0), 50.0)
        d_1h = safe_fetch_float(indicators.get('stoch_d_1h', 50.0), 50.0)
        k_4h = safe_fetch_float(indicators.get('stoch_k_4h', 50.0), 50.0)
        d_4h = safe_fetch_float(indicators.get('stoch_d_4h', 50.0), 50.0)
        
        # ERROR WAS HERE: 'i' was undefined, so it grabbed 'i' from global scope (sys.argv index)
        higher_high_15m = bool(indicators.get('high_15m', 0) and indicators.get('high_15m_prev', 0) and indicators.get('high_15m', 0) >= indicators.get('high_15m_prev', 0))
        lower_low_15m = bool(indicators.get('low_15m', 0) and indicators.get('low_15m_prev', 0) and indicators.get('low_15m', 0) <= indicators.get('low_15m_prev', 0))
        # --- FIX ENDS HERE ---

        is_long = position_key.endswith('_LONG')
        if is_long: current_gain_percent = ((current_price - average_entry_price) / average_entry_price) * 100.0
        else: current_gain_percent = ((average_entry_price - current_price) / average_entry_price) * 100.0
        
        if current_gain_percent < self.min_gain_for_pyramid: return None
        
        rsi_1h = safe_fetch_float(indicators.get('rsi_1h', 50.0), 50.0)
        if is_long and (rsi_1h < 30 or (k_15m < d_15m or lower_low_15m) or k_3m < d_3m or k_1h < d_1h): return None
        elif not is_long and (rsi_1h > 70 or k_15m > d_15m or k_3m > d_3m or k_1h > d_1h): return None
        
        start_position_size_usd = safe_fetch_float(getattr(self.config, 'START_POSITION_SIZE', 45.0), 45.0)
        min_quantity_by_cost = start_position_size_usd / current_price
        max_quantity_by_ratio = current_quantity * self.pyramid_size_ratio
        pyramid_quantity = max(min_quantity_by_cost, max_quantity_by_ratio)
        max_usd_addition = start_position_size_usd * 12.0
        if pyramid_quantity * current_price > max_usd_addition: pyramid_quantity = max_usd_addition / current_price
        if pyramid_quantity <= 0:
            logger.info(f"[Pyramid] Invalid pyramid quantity for {symbol}: {pyramid_quantity}")
            return None

        pyramid_suggestion = {
            'symbol': symbol,
            'position_key': position_key,
            'side': 'BUY' if is_long else 'SELL',
            'quantity': pyramid_quantity,
            'current_price': current_price,
            'current_gain_percent': current_gain_percent,
            'current_quantity': current_quantity,
            'average_entry_price': average_entry_price,
            'rsi_1h': rsi_1h,
            'reason': f"PYRAMID_GAIN_{current_gain_percent:.1f}%_RSI{int(rsi_1h)}"  
        }
        logger.info(f"[Pyramid] Suggesting add to {position_key}: " +
                   f"gain={current_gain_percent:.2f}%,  " +
                   f"add {pyramid_quantity:.6f} @ ${current_price:.4f}")
        return pyramid_suggestion

    async def close_hedge( self,  account_key: str,  hedge_record: Dict[str, Any],  dry_run: bool = False  ) -> Dict[str, Any]:
        target_symbol = hedge_record.get('target_symbol')
        hedge_quantity = hedge_record.get('quantity', 0)
        position_side = hedge_record.get('position_side')
        position_key = hedge_record.get('position_key')
        positionAmt = hedge_record.get('positionAmt')
        if not target_symbol or hedge_quantity <= 0 or not position_key:
            return {
                'status': 'failed',
                'reason': 'Invalid hedge record', 
                'timestamp': time.time()  }
        close_side = 'BUY' if position_side == 'SHORT' else 'SELL'
        current_price, _ = await self.data_manager.get_fresh_price(target_symbol)
        
        if dry_run:
            logger.info(f"[ ][DRY_RUN] Would close hedge: {close_side} {target_symbol} " +
                       f"x {hedge_quantity:.6f} @ ${current_price:.4f}")
            close_record = {
                'type': 'HEDGE_CLOSE',
                'status': 'dry_run',
                'original_hedge_id': hedge_record.get('id'),
                'target_symbol': target_symbol,
                'close_side': close_side,
                'quantity': hedge_quantity,
                'price': current_price,
                'original_position_side': position_side }
            await self.persist_hedge_record(account_key, close_record)
            return { 
                'status': 'dry_run', 
                'record': close_record,  
                'timestamp': time.time()  }
        
        logger.info(f"[ ] Closing hedge: {close_side} {target_symbol} " +  f"x {hedge_quantity:.6f} @ ${current_price:.4f}")
        success, failure_reason = await execute_trade_wrapper( trade_manager=self.trade_manager,tracker_manager=self.tracker_manager, hedge_engine = self,account_key=account_key,position_key=position_key,positionAmt=positionAmt,action='CLOSE',current_price=current_price,qty=hedge_quantity,reason=f"HEDGE_CLOSE_{hedge_record.get('id')}",already_locked=False,is_hedge=True,hedge_for=hedge_record.get('losing_position_key'),override_qty=hedge_quantity,data_manager=self.data_manager )
        
        if success:# Remove from active hedges
            self.registry.release_hedge_slot(account_key, target_symbol)
            async with self.tracker_manager._hedges_lock:
                self.tracker_manager.active_hedges = [
                    h for h in self.tracker_manager.active_hedges 
                    if h.get('id') != hedge_record.get('id') ]
            close_record = {
                'type': 'HEDGE_CLOSE',
                'status': 'executed',
                'original_hedge_id': hedge_record.get('id'),
                'target_symbol': target_symbol,
                'close_side': close_side,
                'quantity': hedge_quantity,
                'price': current_price,
                'original_position_side': position_side,
                'timestamp': time.time()   }
            await self.persist_hedge_record(account_key, close_record)
            logger.info(f"[Hedge Engine] Hedge closed successfully: {hedge_record.get('id')}")
            return { 
                'status': 'success', 
                'record': close_record, 
                'timestamp': time.time()    }
        else:
            logger.error(f"[HedgeEngine] Failed to close hedge: {failure_reason}")
            return {
                'status': 'failed',
                'reason': failure_reason,
                'timestamp': time.time() }

class SentimentMomentumStrategy:
    def __init__(self, trade_manager, tracker_manager, data_manager, config, registry):
        self.trade_manager = trade_manager
        self.tracker_manager = tracker_manager
        self.data_manager = data_manager
        self.config = config
        self.registry = registry
        
        self.account_rules = {
            'flz': {'check_keys': False, 'max_pos': 20},
            'ang': {'check_keys': True,  'max_pos': 20}
        }
        
        self.strategy_tag = "SENT_STRAT"
        self.base_target_gain = 3.0
        self.stop_loss_pct = -0.2
        self.ratio_sensitivity = 0.015 
        
        # HISTORY TRACKING (Symbol -> Deque of (timestamp, local_score, global_score))
        # Stores last ~5 minutes of data points
        self.history = defaultdict(lambda: deque(maxlen=30)) 
        
        self._iteration_lock = asyncio.Lock()
        self.log_dir = Path.home() / "logs" / "sentiment_strategy"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def run_loop(self, stop_event: asyncio.Event):
        logger.info(f"🧠 [SENT_STRAT] Started. History Window: ~3m. Targets: {list(self.account_rules.keys())}")
        await asyncio.sleep(15)
        
        while not stop_event.is_set():
            try:
                async with self._iteration_lock:
                    snapshot = self.data_manager._cold_data or {}
                    btc_data = snapshot.get('BTCUSDC', {})
                    
                    global_score = safe_fetch_float(btc_data.get('0market_sentiment_score', 0.0))
                    global_ema = safe_fetch_float(btc_data.get('0market_sentiment_score_ema', global_score))
                    
                    # Update History for all symbols
                    self._update_history(snapshot, global_score)

                    # Dynamic Long Ratio
                    diff = global_score - global_ema
                    target_long_ratio = 0.5 + (diff * self.ratio_sensitivity)
                    target_long_ratio = max(0.2, min(0.8, target_long_ratio))
                    
                    for account, rules in self.account_rules.items():
                        if hasattr(self.trade_manager, '_check_account_allowed'):
                            if not self.trade_manager._check_account_allowed(account): continue
                        
                        await self._manage_active_positions(account, target_long_ratio)
                        await self._scan_for_entries(account, rules, target_long_ratio, snapshot, global_score)
                
                await asyncio.sleep(10) 
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"🧠 [SENT_STRAT] Loop Error: {e}", exc_info=True)
                await asyncio.sleep(30)

    def _update_history(self, snapshot, global_score):
        """Updates internal deque with (ts, local, global) for velocity calc"""
        now = time.time()
        for symbol, data in snapshot.items():
            if not isinstance(data, dict): continue
            local = safe_fetch_float(data.get('0market_sentiment_local', 0.0))
            self.history[symbol].append((now, local, global_score))

    def _get_3m_deltas(self, symbol):
        """
        Returns (local_delta, divergence_delta) over approx 3 minutes.
        """
        dq = self.history.get(symbol)
        if not dq or len(dq) < 2: return 0.0, 0.0
        
        current = dq[-1] # (ts, local, global)
        cur_ts, cur_loc, cur_glob = current
        
        # Find data point closest to 3 minutes (180s) ago
        target_ts = cur_ts - 180
        past = dq[0]
        
        for item in dq:
            if item[0] >= target_ts:
                past = item
                break
        
        past_ts, past_loc, past_glob = past
        
        # If history is too short (< 60s), return 0 to avoid noise on startup
        if (cur_ts - past_ts) < 60: return 0.0, 0.0

        # Delta Calculation
        local_delta = cur_loc - past_loc
        
        # Divergence = Local - Global
        cur_div = cur_loc - cur_glob
        past_div = past_loc - past_glob
        div_delta = cur_div - past_div
        
        return local_delta, div_delta

    async def _scan_for_entries(self, account_key: str, rules: dict, target_long_ratio: float, snapshot: dict, global_score: float):
        max_pos = rules.get('max_pos', 20)
        check_keys = rules.get('check_keys', False)

        # 1. Count Active
        current_longs = 0
        current_shorts = 0
        positions = self.tracker_manager.positions_service.positions_by_account.get(account_key, {})
        for k, v in positions.items():
            cand = await self.tracker_manager.get_exit_candidate(k)
            # Count if tagged OR if it's in our managed list (fallback)
            if cand and self.strategy_tag in cand.get('last_reason', ''):
                if 'LONG' in k: current_longs += 1
                else: current_shorts += 1
        
        total_active = current_longs + current_shorts
        slots_available = max_pos - total_active
        
        max_long_count = int(max_pos * target_long_ratio)
        max_short_count = max_pos - max_long_count
        
        allow_long = (current_longs < max_long_count) and (slots_available > 0)
        allow_short = (current_shorts < max_short_count) and (slots_available > 0)

        if not allow_long and not allow_short: return

        # 2. Key filtering
        allowed_symbols = set()
        if check_keys:
            universe = self.tracker_manager.get_tradeable_position_keys_for(account_key)
            for k in universe:
                try: allowed_symbols.add(parse_position_key(k)[1])
                except: pass
            if not allowed_symbols:
                await self.tracker_manager.sync_universe(account_key)
                return 

        candidates = []
        if not snapshot: return

        for symbol, data in snapshot.items():
            if not isinstance(data, dict): continue
            if check_keys and symbol not in allowed_symbols: continue
            
            # Skip open positions
            if f"{account_key}:{symbol}_LONG" in positions: continue
            if f"{account_key}:{symbol}_SHORT" in positions: continue

            # --- METRICS ---
            local_score = safe_fetch_float(data.get('0market_sentiment_local', 0.0))
            current_divergence = local_score - global_score
            
            # GET 3M DELTA (The "Taking Off" Metric)
            local_delta_3m, div_delta_3m = self._get_3m_deltas(symbol)
            
            current_price = safe_fetch_float(data.get('current_price', 0.0))
            if current_price <= 0: continue

            # --- ENTRY CRITERIA (Adjusted) ---
            
            # LONG:
            # 1. Divergence > 8 (POPCAT was 9.2, so this catches it)
            # 2. 3m Delta > 5 (Moving up relative to market by 5 points in 3 mins)
            if allow_long and current_divergence > 8 and div_delta_3m > 5:
                k_15m = safe_fetch_float(data.get('stoch_k_15m', 50))
                if k_15m < 85: 
                    candidates.append({
                        'symbol': symbol, 'side': 'LONG', 
                        'score': current_divergence + div_delta_3m, 
                        'price': current_price,
                        'reason': f"DIV:{current_divergence:.1f}_VEL3m:{div_delta_3m:.1f}"
                    })

            # SHORT:
            # 1. Divergence < -8
            # 2. 3m Delta < -5 (Dropping relative to market)
            elif allow_short and current_divergence < -8 and div_delta_3m < -5:
                k_15m = safe_fetch_float(data.get('stoch_k_15m', 50))
                if k_15m > 15:
                    candidates.append({
                        'symbol': symbol, 'side': 'SHORT', 
                        'score': abs(current_divergence + div_delta_3m), 
                        'price': current_price,
                        'reason': f"DIV:{current_divergence:.1f}_VEL3m:{div_delta_3m:.1f}"
                    })

        # Sort and Execute
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        for cand in candidates:
            if cand['side'] == 'LONG':
                if current_longs >= max_long_count: continue
                current_longs += 1
            else:
                if current_shorts >= max_short_count: continue
                current_shorts += 1
                
            await self._execute_entry(account_key, cand)
            await asyncio.sleep(1) 
            
            if (current_longs + current_shorts) >= max_pos: break

    async def _execute_entry(self, account_key: str, cand: dict):
        symbol = cand['symbol']
        side = cand['side'] 
        price = cand['price']
        reason_detail = cand['reason']
        
        base_usd = safe_fetch_float(getattr(self.config, 'START_POSITION_SIZE', 50.0), 50.0)
        mult = 1.5 if cand['score'] > 40 else 1.0
        
        target_usd = base_usd * mult
        qty = target_usd / price
        # qty = await self.trade_manager.round_quantity_to_lot(symbol, qty)
        
        if qty <= 0: return

        position_key = f"{account_key}:{symbol}_{side}"
        
        logger.info(f"🧠 [SENT_STRAT][{account_key}] Opening {position_key} (Score: {cand['score']:.1f} | {reason_detail})")
        
        success, msg = await execute_trade_wrapper(
            self.trade_manager, self.tracker_manager, None,
            account_key, position_key, 0.0, 'OPEN', price, qty,
            f"{self.strategy_tag}_{reason_detail}", 
            already_locked=False, is_hedge=False
        )
        
        if success:
            async with self.tracker_manager._exit_candidates_lock:
                if position_key in self.tracker_manager.exit_candidates:
                    self.tracker_manager.exit_candidates[position_key]['last_reason'] = f"{self.strategy_tag}_ENTRY"
                    self.tracker_manager.exit_candidates[position_key]['status'] = 'active'
            await self.tracker_manager.save_tracker(account_key, force=True)

    async def _manage_active_positions(self, account_key: str, target_long_ratio: float):
        """Standard management with adaptive profit targets"""
        positions = self.tracker_manager.positions_service.positions_by_account.get(account_key, {})
        my_positions = []
        for k in positions:
            cand = await self.tracker_manager.get_exit_candidate(k)
            if cand and self.strategy_tag in cand.get('last_reason', ''):
                my_positions.append(k)

        for pos_key in my_positions:
            try:
                pos = positions.get(pos_key)
                if not pos: continue
                
                symbol = pos.symbol
                current_price, _ = await self.data_manager.get_fresh_price(symbol)
                if current_price <= 0: continue

                amt = abs(safe_fetch_float(pos.positionAmt, 0.0))
                entry = safe_fetch_float(pos.entry_price, 0.0)
                is_long = pos.position_side == 'LONG'
                side_str = pos.position_side
                
                if entry <= 0: continue
                
                gain_pct = ((current_price - entry) / entry) * 100 if is_long else ((entry - current_price) / entry) * 100
                
                # 1. STOP LOSS
                if gain_pct < self.stop_loss_pct:
                    reason = f"{self.strategy_tag}_STOP_LOSS"
                    logger.warning(f"🧠 [SENT_STRAT][{account_key}] 🔪 CUT {pos_key} at {gain_pct:.2f}%")
                    success, msg = await execute_trade_wrapper(
                        self.trade_manager, self.tracker_manager, None,
                        account_key, pos_key, amt, 'CLOSE', current_price, amt,
                        reason, already_locked=False, is_hedge=False
                    )
                    if success:
                        await self._log_performance(account_key, symbol, side_str, "CLOSE_LOSS", amt, current_price, entry, reason)
                    continue

                # 2. PROFIT TARGET
                dynamic_target = self.base_target_gain
                
                # Adaptive: Exit faster if market regime turns against us
                if is_long and target_long_ratio < 0.4: dynamic_target = 1.0 
                elif not is_long and target_long_ratio > 0.6: dynamic_target = 1.0

                if gain_pct > dynamic_target:
                    reduce_qty = amt * 0.25
                    min_qty = self.trade_manager.min_qty.get(symbol, 0.0)
                    action = 'REDUCE'
                    qty_to_trade = reduce_qty
                    
                    if (amt - reduce_qty) < min_qty or (amt * current_price) < 10.0:
                        action = 'CLOSE'
                        qty_to_trade = amt
                    
                    reason = f"{self.strategy_tag}_TRIM_g{gain_pct:.1f}_t{dynamic_target:.1f}"
                    logger.info(f"🧠 [SENT_STRAT][{account_key}] 💰 {action} {pos_key} at {gain_pct:.2f}%")
                    
                    success, msg = await execute_trade_wrapper(
                        self.trade_manager, self.tracker_manager, None,
                        account_key, pos_key, amt, action, current_price, qty_to_trade,
                        reason, already_locked=False, is_hedge=False )
                    if success:
                        await self._log_performance(account_key, symbol, side_str, f"{action}_PROFIT", qty_to_trade, current_price, entry, reason)
            except Exception as e:
                logger.error(f"[SENT_STRAT] Manage error {pos_key}: {e}")

    async def _log_performance(self, account, symbol, side, action, qty, price, entry_price, reason):
        try:
            pnl_usd = 0.0
            pnl_pct = 0.0
            if entry_price > 0 and qty > 0:
                is_long = (side == 'LONG')
                if is_long:
                    pnl_usd = (price - entry_price) * qty
                    pnl_pct = ((price - entry_price) / entry_price) * 100
                else:
                    pnl_usd = (entry_price - price) * qty
                    pnl_pct = ((entry_price - price) / entry_price) * 100
            file_path = self.log_dir / f"{account}_pnl.csv"
            file_exists = file_path.exists()
            row = [datetime.now(timezone.utc).isoformat(), symbol, side, action, f"{qty:.6f}", f"{price:.6f}", f"{entry_price:.6f}", f"{pnl_usd:.4f}", f"{pnl_pct:.2f}", reason]
            async with aiofiles.open(file_path, "a") as f:
                if not file_exists:
                    await f.write("timestamp,symbol,side,action,qty,exit_price,entry_price,pnl_usd,pnl_pct,reason\n")
                await f.write(",".join(row) + "\n")
            logger.info(f"💰 [SENT_STRAT_PNL][{account}] {symbol} {action}: ${pnl_usd:.2f} ({pnl_pct:.2f}%)")
        except: pass
# class FastDataManager:
#     def __init__(self, redis_manager, data_dir: Path, trade_manager=None, tracker_manager=None, ws_manager=None, positions_service=None):
#         self.redis_manager = redis_manager
#         self.data_dir = data_dir
#         self.shared_proxy = None
#         self._local_cache = {} 
#         self._cold_data = {} 
#         self._last_loaded_file = None
#         asyncio.create_task(self._maintain_shared_memory_connection())
#         asyncio.create_task(self._maintain_cold_data_sync())

#     def _resolve_symbol(self, input_symbol: str) -> str:
#         s = str(input_symbol).upper()
#         if ':' in s: s = s.split(':')[-1]
#         return s.replace('_LONG', '').replace('_SHORT', '').strip()

#     def _safe_float(self, val, default=50.0):
#         try:
#             if val is None: return default
#             return float(val)
#         except: return default

#     def _normalize_to_unix(self, val) -> float:
#         """
#         The only function that matters for time.
#         Recognizes ISO Z-Strings, Datetimes, and Scales Unix MS/SEC to Float SEC.
#         """
#         if not val: return 0.0
#         try:
#             # 1. Handle ISO Strings (Z-Time)
#             if isinstance(val, str):
#                 # Use isoparse for high performance on ISO 8601
#                 dt = isoparse(val.replace('Z', '+00:00'))
#                 return dt.timestamp()
            
#             # 2. Handle Datetime Objects
#             if isinstance(val, datetime):
#                 return val.timestamp()
            
#             # 3. Handle Numbers (Unix)
#             if isinstance(val, (int, float, Decimal)):
#                 f_val = float(val)
#                 # If it's Binance Milliseconds (13+ digits), scale to seconds
#                 if f_val > 1000000000000: return f_val / 1000.0
#                 return f_val
#         except: pass
#         return 0.0

#     async def get_hot_state(self, symbol: str):
#         """Calculates indicators. Strictly YOUNGEST source wins."""
#         clean_sym = self._resolve_symbol(symbol)
#         now = time.time()
        
#         # A. Start with Cold Data (Disk)
#         combined_data = self._cold_data.get(clean_sym, {}).copy()
#         ts_cold = self._normalize_to_unix(combined_data.get('timestamp_1m') or combined_data.get('timestamp'))

#         # B. Check Shared Memory (L1)
#         hot_data = None
#         if self.shared_proxy:
#             try:
#                 raw = self.shared_proxy.get_symbol(clean_sym)
#                 if raw and isinstance(raw, dict): hot_data = dict(raw)
#             except: self.shared_proxy = None 

#         # C. Check Redis (L2)
#         if not hot_data and self.redis_manager:
#             try:
#                 client = self.redis_manager.connections.get("local")
#                 if client:
#                     raw_json = await client.get(f"hot_metrics:{clean_sym}")
#                     if raw_json: hot_data = orjson.loads(raw_json)
#             except: pass

#         # D. The Comparison Logic
#         ts_hot = 0.0
#         if hot_data:
#             ts_hot = self._normalize_to_unix(hot_data.get('_tick_ts') or hot_data.get('ts') or hot_data.get('timestamp'))

#         # ONLY update if hot data is strictly younger than what was on disk
#         if hot_data and ts_hot > ts_cold:
#             combined_data.update(hot_data)
#             mappings = {
#                 'k_1m': 'stoch_k_1m', 'd_1m': 'stoch_d_1m',
#                 'k_3m': 'stoch_k_3m', 'd_3m': 'stoch_d_3m',
#                 'price': 'current_price', 'current_price': 'price'
#             }
#             for s_key, l_key in mappings.items():
#                 if s_key in hot_data:
#                     val = hot_data[s_key]
#                     combined_data[l_key] = val
#                     combined_data[s_key] = val
        
#         # E. Final Polish
#         k_1m = self._safe_float(combined_data.get('stoch_k_1m', combined_data.get('k_1m')))
#         d_1m = self._safe_float(combined_data.get('stoch_d_1m', combined_data.get('d_1m')))
#         k_3m = self._safe_float(combined_data.get('stoch_k_3m', combined_data.get('k_3m')))
#         d_3m = self._safe_float(combined_data.get('stoch_d_3m', combined_data.get('d_3m')))
        
#         final_ts = max(ts_hot, ts_cold)
        
#         metrics = {
#             'k_1m': k_1m, 'd_1m': d_1m, 'k_3m': k_3m, 'd_3m': d_3m,
#             'k_1m_prev': self._safe_float(combined_data.get('k_1m_prev'), k_1m),
#             'k_3m_prev': self._safe_float(combined_data.get('k_3m_prev'), k_3m),
#             '_tick_ts': final_ts # This is used by log_stoch_snapshot for the 'Age' column
#         }
        
#         is_fresh = (final_ts > 0) and (abs(now - final_ts) < 60.0)
#         return metrics, combined_data, k_1m, d_1m, k_3m, d_3m, is_fresh

#     async def get_fresh_price(self, symbol: str) -> tuple[float, float]:
#         clean_sym = self._resolve_symbol(symbol)
#         best_p, best_ts = 0.0, 0.0

#         # Check Redis mark_price (Fastest update, usually 1s)
#         if self.redis_manager:
#             try:
#                 client = self.redis_manager.connections.get("local")
#                 raw = await client.get(f"mark_price:{clean_sym}")
#                 if raw:
#                     d = orjson.loads(raw)
#                     best_p = self._safe_float(d.get('price') or d.get('p'), 0.0)
#                     best_ts = self._normalize_to_unix(d.get('timestamp') or d.get('E'))
#             except: pass

#         # Check Indicator State
#         m, i, _, _, _, _, _ = await self.get_hot_state(clean_sym)
#         p_hot = self._safe_float(i.get('current_price') or i.get('price'), 0.0)
#         ts_hot = m.get('_tick_ts', 0.0)
#         if ts_hot > best_ts:
#             best_p, best_ts = p_hot, ts_hot

#         # Check Disk Caches (N=8)
#         dp, dts = self._get_price_from_disk_caches(clean_sym)
#         if dts > best_ts:
#             best_p, best_ts = dp, dts

#         return best_p, best_ts

#     def get_fresh_price_sync(self, symbol: str) -> tuple[float, float]:
#         clean = self._resolve_symbol(symbol)
#         # Check high-speed disk caches first
#         best_p, best_ts = self._get_price_from_disk_caches(clean)

#         # Fallback to cold disk dictionary
#         cold = self._cold_data.get(clean, {})
#         cts = self._normalize_to_unix(cold.get('timestamp'))
#         if cts > best_ts:
#             best_p = self._safe_float(cold.get('current_price') or cold.get('close'), 0.0)
#             best_ts = cts

#         return best_p, best_ts

#     def _get_price_from_disk_caches(self, symbol: str) -> tuple[float, float]:
#         best_p, best_ts = 0.0, 0.0
#         for i in range(1, 9):
#             try:
#                 path = config.BASE_PATH / f"price_cache_{i}.json"
#                 if not path.exists(): continue
#                 with open(path, 'rb') as f:
#                     data = orjson.loads(f.read())
#                     entry = data.get(symbol)
#                     if entry:
#                         if isinstance(entry, dict):
#                             p = self._safe_float(entry.get('price') or entry.get('p'), 0.0)
#                             t = self._normalize_to_unix(entry.get('timestamp') or entry.get('ts') or entry.get('E'))
#                         else:
#                             p = self._safe_float(entry, 0.0)
#                             t = path.stat().st_mtime
#                         if t > best_ts: best_p, best_ts = p, t
#             except: continue
#         return best_p, best_ts

#     async def _maintain_shared_memory_connection(self):
#         while True:
#             # Only try to connect if we don't have a proxy
#             if self.shared_proxy is None:
#                 try:
#                     from ez_share_ind import get_shared_memory_client
#                     client = get_shared_memory_client()
#                     if client:
#                         self.shared_proxy = client.get_store()  # pylint: disable=no-member
#                         # Immediate test
#                         self.shared_proxy.get_stats() 
#                         print("[SharedMem] Connected successfully.")
#                 except Exception:
#                     self.shared_proxy = None
            
#             # If we DO have a proxy, test it. If test fails, wipe it.
#             elif self.shared_proxy is not None:
#                 try:
#                     # Lightweight ping
#                     self.shared_proxy.health_check()
#                 except Exception:
#                     print("[SharedMem] Connection lost. Resetting.")
#                     self.shared_proxy = None
                    
#             await asyncio.sleep(2) # Check every 2 seconds

#     async def _maintain_cold_data_sync(self):
#         while True:
#             try:
#                 candidates = list(self.data_dir.glob("market_data_*.json"))
#                 if candidates:
#                     candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
#                     target_file = candidates[0]
#                     if target_file != self._last_loaded_file:
#                         async with aiofiles.open(target_file, "rb") as f:
#                             content = await f.read()
#                             self._cold_data = orjson.loads(content)
#                             self._last_loaded_file = target_file
#             except: pass
#             await asyncio.sleep(10)

# class FastDataManager:
#     def __init__(self, redis_manager, data_dir: Path, trade_manager=None, tracker_manager=None, ws_manager=None, positions_service=None):
#         self.redis_manager = redis_manager
#         self.data_dir = data_dir
#         self.shared_proxy = None
#         self._local_cache = {} 
#         self._cold_data = {} 
#         self._last_loaded_file = None
#         self.using_shared_memory = True
#         asyncio.create_task(self._maintain_shared_memory_connection())
#         asyncio.create_task(self._maintain_cold_data_sync())

#     def register_shared_memory(self, proxy):
#         self.shared_indicators_proxy = proxy
#         self.using_shared_memory = True

#     async def _maintain_shared_memory_connection(self):
#         while True:
#             # Only try to connect if we don't have a proxy
#             if self.shared_proxy is None:
#                 try:
#                     from ez_share_ind import get_shared_memory_client
#                     client = get_shared_memory_client()
#                     if client:
#                         self.shared_proxy = client.get_store()  # pylint: disable=no-member
#                         # Immediate test
#                         self.shared_proxy.get_stats() 
#                         print("[SharedMem] Connected successfully.")
#                 except Exception:
#                     self.shared_proxy = None
            
#             # If we DO have a proxy, test it. If test fails, wipe it.
#             elif self.shared_proxy is not None:
#                 try:
#                     # Lightweight ping
#                     self.shared_proxy.health_check()
#                 except Exception:
#                     print("[SharedMem] Connection lost. Resetting.")
#                     self.shared_proxy = None
                    
#             await asyncio.sleep(2) # Check every 2 seconds


#     def _resolve_symbol(self, input_symbol: str) -> str:
#         s = str(input_symbol)
#         if ':' in s: s = s.split(':')[-1]
#         s = s.replace('_LONG', '').replace('_SHORT', '')
#         return s.strip().upper()

#     def _safe_float(self, val, default=50.0):
#         """CRITICAL FIX: Safely convert anything to float, handling None."""
#         try:
#             if val is None: return default
#             return float(val)
#         except: return default
        
    # def _normalize_to_unix(self, val) -> float:
    #     """
    #     The only function that matters for time.
    #     Recognizes ISO Z-Strings, Datetimes, and Scales Unix MS/SEC to Float SEC.
    #     """
    #     if not val: return 0.0
    #     try:
    #         # 1. Handle ISO Strings (Z-Time)
    #         if isinstance(val, str):
    #             # Use isoparse for high performance on ISO 8601
    #             dt = isoparse(val.replace('Z', '+00:00'))
    #             return dt.timestamp()
            
    #         # 2. Handle Datetime Objects
    #         if isinstance(val, datetime):
    #             return val.timestamp()
            
    #         # 3. Handle Numbers (Unix)
    #         if isinstance(val, (int, float, Decimal)):
    #             f_val = float(val)
    #             # If it's Binance Milliseconds (13+ digits), scale to seconds
    #             if f_val > 1000000000000: return f_val / 1000.0
    #             return f_val
    #     except: pass
    #     return 0.0

    # async def get_hot_state(self, symbol: str):
    #     """Calculates indicators. Strictly YOUNGEST source wins."""
    #     clean_sym = self._resolve_symbol(symbol)
    #     now = time.time()
        
    #     # A. Start with Cold Data (Disk)
    #     combined_data = self._cold_data.get(clean_sym, {}).copy()
    #     ts_cold = self._normalize_to_unix(combined_data.get('timestamp_1m') or combined_data.get('timestamp'))

    #     # B. Check Shared Memory (L1)
    #     hot_data = None
    #     if self.shared_proxy:
    #         try:
    #             raw = self.shared_proxy.get_symbol(clean_sym)
    #             if raw and isinstance(raw, dict): hot_data = dict(raw)
    #         except: self.shared_proxy = None 

    #     # C. Check Redis (L2)
    #     if not hot_data and self.redis_manager:
    #         try:
    #             client = self.redis_manager.connections.get("local")
    #             if client:
    #                 raw_json = await client.get(f"hot_metrics:{clean_sym}")
    #                 if raw_json: hot_data = orjson.loads(raw_json)
    #         except: pass

    #     # D. The Comparison Logic
    #     ts_hot = 0.0
    #     if hot_data:
    #         ts_hot = self._normalize_to_unix(hot_data.get('_tick_ts') or hot_data.get('ts') or hot_data.get('timestamp'))

    #     # ONLY update if hot data is strictly younger than what was on disk
    #     if hot_data and ts_hot > ts_cold:
    #         combined_data.update(hot_data)
    #         mappings = {
    #             'k_1m': 'stoch_k_1m', 'd_1m': 'stoch_d_1m',
    #             'k_3m': 'stoch_k_3m', 'd_3m': 'stoch_d_3m',
    #             'price': 'current_price', 'current_price': 'price'
    #         }
    #         for s_key, l_key in mappings.items():
    #             if s_key in hot_data:
    #                 val = hot_data[s_key]
    #                 combined_data[l_key] = val
    #                 combined_data[s_key] = val
        
    #     # E. Final Polish
    #     k_1m = self._safe_float(combined_data.get('stoch_k_1m', combined_data.get('k_1m')))
    #     d_1m = self._safe_float(combined_data.get('stoch_d_1m', combined_data.get('d_1m')))
    #     k_3m = self._safe_float(combined_data.get('stoch_k_3m', combined_data.get('k_3m')))
    #     d_3m = self._safe_float(combined_data.get('stoch_d_3m', combined_data.get('d_3m')))
        
    #     final_ts = max(ts_hot, ts_cold)
        
    #     metrics = {
    #         'k_1m': k_1m, 'd_1m': d_1m, 'k_3m': k_3m, 'd_3m': d_3m,
    #         'k_1m_prev': self._safe_float(combined_data.get('k_1m_prev'), k_1m),
    #         'k_3m_prev': self._safe_float(combined_data.get('k_3m_prev'), k_3m),
    #         '_tick_ts': final_ts # This is used by log_stoch_snapshot for the 'Age' column
    #     }
        
    #     is_fresh = (final_ts > 0) and (abs(now - final_ts) < 60.0)
    #     return metrics, combined_data, k_1m, d_1m, k_3m, d_3m, is_fresh

    # async def get_fresh_price(self, symbol: str) -> tuple[float, float]:
    #     clean_sym = self._resolve_symbol(symbol)
    #     best_p, best_ts = 0.0, 0.0

    #     # Check Redis mark_price (Fastest update, usually 1s)
    #     if self.redis_manager:
    #         try:
    #             client = self.redis_manager.connections.get("local")
    #             raw = await client.get(f"mark_price:{clean_sym}")
    #             if raw:
    #                 d = orjson.loads(raw)
    #                 best_p = self._safe_float(d.get('price') or d.get('p'), 0.0)
    #                 best_ts = self._normalize_to_unix(d.get('timestamp') or d.get('E'))
    #         except: pass

    #     # Check Indicator State
    #     m, i, _, _, _, _, _ = await self.get_hot_state(clean_sym)
    #     p_hot = self._safe_float(i.get('current_price') or i.get('price'), 0.0)
    #     ts_hot = m.get('_tick_ts', 0.0)
    #     if ts_hot > best_ts:
    #         best_p, best_ts = p_hot, ts_hot

    #     # Check Disk Caches (N=8)
    #     dp, dts = self._get_price_from_disk_caches(clean_sym)
    #     if dts > best_ts:
    #         best_p, best_ts = dp, dts

    #     return best_p, best_ts

    # def get_fresh_price_sync(self, symbol: str) -> tuple[float, float]:
    #     clean = self._resolve_symbol(symbol)
    #     # Check high-speed disk caches first
    #     best_p, best_ts = self._get_price_from_disk_caches(clean)

    #     # Fallback to cold disk dictionary
    #     cold = self._cold_data.get(clean, {})
    #     cts = self._normalize_to_unix(cold.get('timestamp'))
    #     if cts > best_ts:
    #         best_p = self._safe_float(cold.get('current_price') or cold.get('close'), 0.0)
    #         best_ts = cts

    #     return best_p, best_ts

    # def _get_price_from_disk_caches(self, symbol: str) -> tuple[float, float]:
    #     best_p, best_ts = 0.0, 0.0
    #     for i in range(1, 9):
    #         try:
    #             path = config.BASE_PATH / f"price_cache_{i}.json"
    #             if not path.exists(): continue
    #             with open(path, 'rb') as f:
    #                 data = orjson.loads(f.read())
    #                 entry = data.get(symbol)
    #                 if entry:
    #                     if isinstance(entry, dict):
    #                         p = self._safe_float(entry.get('price') or entry.get('p'), 0.0)
    #                         t = self._normalize_to_unix(entry.get('timestamp') or entry.get('ts') or entry.get('E'))
    #                     else:
    #                         p = self._safe_float(entry, 0.0)
    #                         t = path.stat().st_mtime
    #                     if t > best_ts: best_p, best_ts = p, t
    #         except: continue
    #     return best_p, best_ts


#     async def _maintain_cold_data_sync(self):
#         """Scans for the newest JSON snapshot."""
#         while True:
#             try:
#                 candidates = list(self.data_dir.glob("market_data_*.json"))
#                 target_file = None
                
#                 if candidates:
#                     candidates.sort(key=lambda p: p.name, reverse=True)
#                     target_file = candidates[0]
#                 else:
#                     static = self.data_dir / "latest_market_data.json"
#                     if static.exists(): target_file = static

#                 if target_file and target_file != self._last_loaded_file:
#                     async with aiofiles.open(target_file, "rb") as f:
#                         content = await f.read()
#                         if content:
#                             self._cold_data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
#                             self._last_loaded_file = target_file
#                             logger.info(f"COLD_DATA btcusdc ts {self._cold_data.get['BTCUSDC'].get['timestamp']}")
#             except Exception:
#                 pass
            
#             await asyncio.sleep(5)

#     def _resolve_symbol(self, input_symbol: str) -> str:
#         s = str(input_symbol)
#         if ':' in s: s = s.split(':')[-1]
#         s = s.replace('_LONG', '').replace('_SHORT', '')
#         return s.strip().upper()

#     def _safe_float(self, val, default=50.0):
#         """CRITICAL FIX: Safely convert anything to float, handling None."""
#         try:
#             if val is None: return default
#             return float(val)
#         except: return default
        
    # async def get_hot_state(self, symbol: str):
    #     lookup_symbol = self._resolve_symbol(symbol)
    #     now = time.time()
    #     combined_data = self._cold_data.get(lookup_symbol, {}).copy()
    #     hot_data = None
    #     if self.shared_proxy:
    #         try:
    #             raw = self.shared_proxy.get_symbol(lookup_symbol)
    #             if raw:
    #                 temp = dict(raw)
    #                 hot_data = dict(raw)
    #                 ts = self._safe_float(temp.get('_tick_ts') or temp.get('ts'), 0.0)
    #                 if (now - ts) < 3.0:
    #                     hot_data = temp
    #         except: 
    #             self.shared_proxy = None 
    #             pass

    #     if not hot_data and self.redis_manager:
    #         try:
    #             client = self.redis_manager.connections.get("local")
    #             if client:
    #                 raw_json = await client.get(f"hot_metrics:{lookup_symbol}")
    #                 if raw_json: hot_data = orjson.loads(raw_json)  # type: ignore # pylint: disable=no-member,c-extension-no-member
    #         except: pass
    #     ts_hot = self._safe_float(hot_data.get('_tick_ts') or hot_data.get('ts'), 0.0) if hot_data else 0.0
    #     ts_cold = 0.0
        
    #     c_ts = combined_data.get('timestamp_1m') or combined_data.get('timestamp')
    #     if c_ts:
    #         try: 
    #             if isinstance(c_ts, (int, float)): ts_cold = float(c_ts)
    #             else: ts_cold = datetime.fromisoformat(str(c_ts).replace('Z', '+00:00')).timestamp()
    #         except: pass
    #     if hot_data and (ts_hot >= (ts_cold - 1.0) or not combined_data):
    #         combined_data.update(hot_data)
    #         mappings = {
    #             'k_1m': 'stoch_k_1m', 'd_1m': 'stoch_d_1m',
    #             'k_3m': 'stoch_k_3m', 'd_3m': 'stoch_d_3m',
    #             'k_1m_prev': 'k_1m_prev', 'd_1m_prev': 'd_1m_prev',
    #             'k_3m_prev': 'k_3m_prev', 'd_3m_prev': 'd_3m_prev',
    #             'price': 'current_price', 'current_price': 'price'  }
            
    #         for s_key, l_key in mappings.items():
    #             if s_key in hot_data:
    #                 val = hot_data[s_key]
    #                 combined_data[l_key] = val
    #                 combined_data[s_key] = val
    #         if ts_hot > 0:
    #             iso_ts = datetime.fromtimestamp(ts_hot, tz=timezone.utc).isoformat()
    #             combined_data['timestamp'] = iso_ts
    #             combined_data['timestamp_1m'] = iso_ts
    #     k_1m = self._safe_float(combined_data.get('stoch_k_1m', combined_data.get('k_1m')))
    #     d_1m = self._safe_float(combined_data.get('stoch_d_1m', combined_data.get('d_1m')))
    #     k_3m = self._safe_float(combined_data.get('stoch_k_3m', combined_data.get('k_3m')))
    #     d_3m = self._safe_float(combined_data.get('stoch_d_3m', combined_data.get('d_3m')))
    #     k_1m_prev = self._safe_float(combined_data.get('k_1m_prev', combined_data.get('k_1m_prev')), k_1m)
    #     d_1m_prev = self._safe_float(combined_data.get('stoch_d_1m_prev', combined_data.get('d_1m_prev')), d_1m)
    #     k_3m_prev = self._safe_float(combined_data.get('k_3m_prev', combined_data.get('k_3m_prev')), k_3m)
    #     d_3m_prev = self._safe_float(combined_data.get('stoch_d_3m_prev', combined_data.get('d_3m_prev')), d_3m)

    #     metrics = {
    #         'k_1m': k_1m, 'd_1m': d_1m, 'k_3m': k_3m, 'd_3m': d_3m,
    #         'k_1m_prev': k_1m_prev, 'd_1m_prev': d_1m_prev, 'k_3m_prev': k_3m_prev, 'd_3m_prev': d_3m_prev,
    #         '_tick_ts': max(ts_hot, ts_cold)}
    #     is_fresh = (now - metrics['_tick_ts']) < 60.0
    #     return metrics, combined_data, k_1m, d_1m, k_3m, d_3m, is_fresh

    # # Wrappers
    # async def get_fresh_price(self, symbol):
    #     m, i, _, _, _, _, _ = await self.get_hot_state(symbol)
    #     p = self._safe_float(i.get('current_price') or i.get('close') or i.get('price'), 0.0)
    #     return p, 0

    # def get_fresh_price_sync(self, symbol: str) -> tuple[float, float]:
    #     clean = self._resolve_symbol(symbol)
    #     if clean in self._local_cache: return self._local_cache[clean]
    #     cold = self._cold_data.get(clean, {})
    #     p = self._safe_float(cold.get('current_price') or cold.get('close'), 0.0)
    #     return p, 0

    # async def get_fresh_indicators(self, symbol):
    #     _, indicators, _, _, _, _, is_fresh = await self.get_hot_state(symbol)
    #     return indicators, not is_fresh

    # # Dead stubs
    # def update_price_direct(self, *args): pass
    # async def inject_data(self, *args): pass
    # async def update_buffers(self): pass
    # async def start_redis_listener(self): pass


class FastDataManager:
    def __init__(self, redis_manager, data_dir: Path, trade_manager=None, tracker_manager=None, ws_manager=None, positions_service=None):
        self.redis_manager = redis_manager
        self.data_dir = data_dir
        self.shared_proxy = None
        self._local_cache = {} 
        self._cold_data = {} 
        self._last_loaded_file = None
        self.using_shared_memory = True
        asyncio.create_task(self._maintain_shared_memory_connection())
        asyncio.create_task(self._maintain_cold_data_sync())

    def _resolve_symbol(self, input_symbol: str) -> str:
        s = str(input_symbol).upper()
        if ':' in s: s = s.split(':')[-1]
        return s.replace('_LONG', '').replace('_SHORT', '').strip()

    def _safe_float(self, val, default=50.0):
        try:
            if val is None: return default
            return float(val)
        except: return default

    def register_shared_memory(self, proxy):
        self.shared_indicators_proxy = proxy
        self.using_shared_memory = True

    async def _maintain_shared_memory_connection(self):
        while True:
            # Only try to connect if we don't have a proxy
            if self.shared_proxy is None:
                try:
                    from ez_share_ind import get_shared_memory_client
                    client = get_shared_memory_client()
                    if client:
                        self.shared_proxy = client.get_store()  # pylint: disable=no-member
                        # Immediate test
                        self.shared_proxy.get_stats() 
                        print("[SharedMem] Connected successfully.")
                except Exception:
                    self.shared_proxy = None
            
            # If we DO have a proxy, test it. If test fails, wipe it.
            elif self.shared_proxy is not None:
                try:
                    # Lightweight ping
                    self.shared_proxy.health_check()
                except Exception:
                    print("[SharedMem] Connection lost. Resetting.")
                    self.shared_proxy = None
                    
            await asyncio.sleep(2) # Check every 2 seconds


    def _resolve_symbol(self, input_symbol: str) -> str:
        s = str(input_symbol)
        if ':' in s: s = s.split(':')[-1]
        s = s.replace('_LONG', '').replace('_SHORT', '')
        return s.strip().upper()

    def _safe_float(self, val, default=50.0):
        """CRITICAL FIX: Safely convert anything to float, handling None."""
        try:
            if val is None: return default
            return float(val)
        except: return default

    async def _maintain_cold_data_sync(self):
        last_mtime = 0.0
        while True:
            try:
                candidates = list(self.data_dir.glob("market_data_*.json"))
                target_file = None
                
                if candidates:
                    candidates.sort(key=lambda p: p.name, reverse=True)
                    target_file = candidates[0]
                else:
                    static = self.data_dir / "latest_market_data.json"
                    if static.exists(): target_file = static

                if target_file and target_file != self._last_loaded_file:
                    async with aiofiles.open(target_file, "rb") as f:
                        content = await f.read()
                        if content:
                            self._cold_data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                            self._last_loaded_file = target_file
                            logger.info(f"COLD_DATA btcusdc ts {self._cold_data.get['BTCUSDC'].get['timestamp']}")            
            except Exception:
                pass
            await asyncio.sleep(5)


    def _normalize_to_unix(self, val) -> float:
        """
        The only function that matters for time.
        Recognizes ISO Z-Strings, Datetimes, and Scales Unix MS/SEC to Float SEC.
        """
        if not val: return 0.0
        try:
            # 1. Handle ISO Strings (Z-Time)
            if isinstance(val, str):
                # Use isoparse for high performance on ISO 8601
                dt = isoparse(val.replace('Z', '+00:00'))
                return dt.timestamp()
            
            # 2. Handle Datetime Objects
            if isinstance(val, datetime):
                return val.timestamp()
            
            # 3. Handle Numbers (Unix)
            if isinstance(val, (int, float, Decimal)):
                f_val = float(val)
                # If it's Binance Milliseconds (13+ digits), scale to seconds
                if f_val > 1000000000000: return f_val / 1000.0
                return f_val
        except: pass
        return 0.0

    async def get_hot_state(self, symbol: str):
        """Calculates indicators. Combines cold disk state with fresh bridge updates."""
        clean_sym = self._resolve_symbol(symbol)
        now = time.time()
        
        # A. Start with Cold Data (Disk)
        combined_data = self._cold_data.get(clean_sym, {}).copy()
        ts_cold = self._normalize_to_unix(combined_data.get('timestamp_1m') or combined_data.get('timestamp'))

        # B. Check Shared Memory (L1)
        hot_data = None
        if self.shared_proxy:
            try:
                raw = self.shared_proxy.get_symbol(clean_sym)
                if raw: hot_data = dict(raw) # safely converts multiprocessing DictProxy to dict
            except: self.shared_proxy = None 

        # C. Check Redis (L2)
        if not hot_data and self.redis_manager:
            try:
                client = self.redis_manager.connections.get("local")
                if client:
                    raw_json = await client.get(f"hot_metrics:{clean_sym}")
                    if raw_json: hot_data = orjson.loads(raw_json)
            except: pass

        # D. The Merging Logic
        ts_hot = 0.0
        if hot_data:
            ts_hot = self._normalize_to_unix(hot_data.get('_tick_ts') or hot_data.get('ts') or hot_data.get('timestamp'))

            # ALWAYS map over critical high-frequency streaming indicators regardless of timestamps
            for key in ['k_1m', 'd_1m', 'k_3m', 'd_3m', 'price', '_tick_ts', 'k_1m_prev', 'k_3m_prev', 'current_price']:
                if key in hot_data:
                    val = hot_data[key]
                    combined_data[key] = val
                    # Map to legacy names used by SignalRater and other components
                    if key == 'k_1m': combined_data['stoch_k_1m'] = val
                    if key == 'd_1m': combined_data['stoch_d_1m'] = val
                    if key == 'k_3m': combined_data['stoch_k_3m'] = val
                    if key == 'd_3m': combined_data['stoch_d_3m'] = val
                    if key == 'k_1m_prev': combined_data['stoch_k_1m_prev'] = val
                    if key == 'k_3m_prev': combined_data['stoch_k_3m_prev'] = val
                    if key == 'price': combined_data['current_price'] = val

            # Fully update everything else ONLY if hot data is strictly younger than what was on disk
            if ts_hot > ts_cold:
                combined_data.update(hot_data)
        
        # E. Final Polish
        k_1m = self._safe_float(combined_data.get('stoch_k_1m', combined_data.get('k_1m')))
        d_1m = self._safe_float(combined_data.get('stoch_d_1m', combined_data.get('d_1m')))
        k_3m = self._safe_float(combined_data.get('stoch_k_3m', combined_data.get('k_3m')))
        d_3m = self._safe_float(combined_data.get('stoch_d_3m', combined_data.get('d_3m')))
        k_1m_prev = self._safe_float(combined_data.get('k_1m_prev'), k_1m)
        k_3m_prev = self._safe_float(combined_data.get('k_3m_prev'), k_3m)
        
        final_ts = max(ts_hot, ts_cold)
        
        metrics = {
            'k_1m': k_1m, 'd_1m': d_1m, 'k_3m': k_3m, 'd_3m': d_3m,
            'k_1m_prev': k_1m_prev,
            'k_3m_prev': k_3m_prev,
            'timestamp_3m': combined_data.get('timestamp_3m'),
            '_tick_ts': final_ts # This is used by log_stoch_snapshot for the 'Age' column
        }
        
        is_fresh = (final_ts > 0) and (abs(now - final_ts) < 60.0)
        return metrics, combined_data, k_1m, d_1m, k_3m, d_3m, is_fresh


    # async def get_hot_state(self, symbol: str):
    #     """Calculates indicators. Strictly YOUNGEST source wins."""
    #     clean_sym = self._resolve_symbol(symbol)
    #     now = time.time()
        
    #     # A. Start with Cold Data (Disk)
    #     combined_data = self._cold_data.get(clean_sym, {}).copy()
    #     ts_cold = self._normalize_to_unix(combined_data.get('timestamp_1m') or combined_data.get('timestamp'))

    #     # B. Check Shared Memory (L1)
    #     hot_data = None
    #     if self.shared_proxy:
    #         try:
    #             raw = self.shared_proxy.get_symbol(clean_sym)
    #             if raw and isinstance(raw, dict): hot_data = dict(raw)
    #         except: self.shared_proxy = None 

    #     # C. Check Redis (L2)
    #     if not hot_data and self.redis_manager:
    #         try:
    #             client = self.redis_manager.connections.get("local")
    #             if client:
    #                 raw_json = await client.get(f"hot_metrics:{clean_sym}")
    #                 if raw_json: hot_data = orjson.loads(raw_json)
    #         except: pass

    #     # D. The Comparison Logic
    #     ts_hot = 0.0
    #     if hot_data:
    #         ts_hot = self._normalize_to_unix(hot_data.get('_tick_ts') or hot_data.get('ts') or hot_data.get('timestamp'))

    #     # ONLY update if hot data is strictly younger than what was on disk
    #     if hot_data and ts_hot > ts_cold:
    #         combined_data.update(hot_data)
    #         mappings = {
    #             'k_1m': 'stoch_k_1m', 'd_1m': 'stoch_d_1m',
    #             'k_3m': 'stoch_k_3m', 'd_3m': 'stoch_d_3m',
    #             'price': 'current_price', 'current_price': 'price'
    #         }
    #         for s_key, l_key in mappings.items():
    #             if s_key in hot_data:
    #                 val = hot_data[s_key]
    #                 combined_data[l_key] = val
    #                 combined_data[s_key] = val
        
    #     # E. Final Polish
    #     k_1m = self._safe_float(combined_data.get('stoch_k_1m', combined_data.get('k_1m')))
    #     d_1m = self._safe_float(combined_data.get('stoch_d_1m', combined_data.get('d_1m')))
    #     k_3m = self._safe_float(combined_data.get('stoch_k_3m', combined_data.get('k_3m')))
    #     d_3m = self._safe_float(combined_data.get('stoch_d_3m', combined_data.get('d_3m')))
        
    #     final_ts = max(ts_hot, ts_cold)
        
    #     metrics = {
    #         'k_1m': k_1m, 'd_1m': d_1m, 'k_3m': k_3m, 'd_3m': d_3m,
    #         'k_1m_prev': self._safe_float(combined_data.get('k_1m_prev'), k_1m),
    #         'k_3m_prev': self._safe_float(combined_data.get('k_3m_prev'), k_3m),
    #         '_tick_ts': final_ts # This is used by log_stoch_snapshot for the 'Age' column
    #     }
        
    #     is_fresh = (final_ts > 0) and (abs(now - final_ts) < 60.0)
    #     return metrics, combined_data, k_1m, d_1m, k_3m, d_3m, is_fresh




    # async def get_hot_state(self, symbol: str):
    #     clean_sym = self._resolve_symbol(symbol)
    #     now = time.time()
        
    #     # 1. TIER 1/2: HOT DATA (Proxy or Redis hot_metrics)
    #     hot_data = None
    #     if self.shared_proxy:
    #         try: hot_data = self.shared_proxy.get_symbol(clean_sym)
    #         except: self.shared_proxy = None 
    #     if not hot_data and self.redis_manager:
    #         try:
    #             client = self.redis_manager.connections.get("local")
    #             raw = await client.get(f"hot_metrics:{clean_sym}")
    #             if raw: hot_data = orjson.loads(raw)
    #         except: pass

    #     # 2. TIER 2/3: INDICATORS (Redis Blob or JSON fallback)
    #     blob_data = None
    #     if self.redis_manager:
    #         try:
    #             client = self.redis_manager.connections.get("local")
    #             raw = await client.get("latest_market_data")
    #             if raw:
    #                 full_blob = orjson.loads(raw)
    #                 blob_data = full_blob.get(clean_sym)
    #         except: pass
    #     if not blob_data: 
    #         blob_data = self._cold_data.get(clean_sym)

    #     # 3. ARBITRATION
    #     combined_data = blob_data.copy() if blob_data else {}
        
    #     # 1M: ALWAYS from Hot Source
    #     ts_1m = 0.0
    #     if hot_data:
    #         ts_1m = self._normalize_to_unix(hot_data.get('timestamp_1m') or hot_data.get('_tick_ts'))
    #         combined_data['stoch_k_1m'] = hot_data.get('k_1m', 50.0)
    #         combined_data['stoch_d_1m'] = hot_data.get('d_1m', 50.0)
    #         combined_data['k_1m_prev'] = hot_data.get('k_1m_prev', 50.0)

    #     # 3M: COMPARE TIMESTAMP_3M (Freshest source wins)
    #     ts_3m_hot = self._normalize_to_unix(hot_data.get('timestamp_3m')) if hot_data else 0.0
    #     ts_3m_blob = self._normalize_to_unix(blob_data.get('timestamp_3m')) if blob_data else 0.0
        
    #     if ts_3m_hot >= ts_3m_blob and hot_data:
    #         combined_data['stoch_k_3m'] = hot_data.get('k_3m', 50.0)
    #         combined_data['stoch_d_3m'] = hot_data.get('d_3m', 50.0)
    #         combined_data['k_3m_prev'] = hot_data.get('k_3m_prev', 50.0)
    #         ts_3m_final = ts_3m_hot
    #     else:
    #         ts_3m_final = ts_3m_blob # HA/SMA/DC stay from blob

    #     # FRESHNESS (No Concessions)
    #     final_tick_ts = ts_1m if ts_1m > 0 else ts_3m_final
    #     metrics = {
    #         'k_1m': combined_data.get('stoch_k_1m', 50.0),
    #         'd_1m': combined_data.get('stoch_d_1m', 50.0),
    #         'k_1m_prev': combined_data.get('k_1m_prev', 50.0),
    #         'k_3m': combined_data.get('stoch_k_3m', 50.0),
    #         'd_3m': combined_data.get('stoch_d_3m', 50.0),
    #         'k_3m_prev': combined_data.get('k_3m_prev', 50.0),
    #         '_tick_ts': final_tick_ts
    #     }
        
    #     is_fresh = (now - final_tick_ts) < 15.0 if final_tick_ts > 0 else False
    #     return metrics, combined_data, metrics['k_1m'], metrics['d_1m'], metrics['k_3m'], metrics['d_3m'], is_fresh
    # async def get_hot_state(self, symbol: str):
    #     """Calculates indicators. Strictly YOUNGEST source wins."""
    #     clean_sym = self._resolve_symbol(symbol)
    #     now = time.time()
        
    #     # A. Start with Cold Data (Disk)
    #     combined_data = self._cold_data.get(clean_sym, {}).copy()
    #     ts_cold = self._normalize_to_unix(combined_data.get('timestamp_1m') or combined_data.get('timestamp'))

    #     # B. Check Shared Memory (L1)
    #     hot_data = None
    #     if self.shared_proxy:
    #         try:
    #             raw = self.shared_proxy.get_symbol(clean_sym)
    #             if raw and isinstance(raw, dict): hot_data = dict(raw)
    #         except: self.shared_proxy = None 

    #     # C. Check Redis (L2)
    #     if not hot_data and self.redis_manager:
    #         try:
    #             client = self.redis_manager.connections.get("local")
    #             if client:
    #                 raw_json = await client.get(f"hot_metrics:{clean_sym}")
    #                 if raw_json: hot_data = orjson.loads(raw_json)
    #         except: pass

    #     # D. The Comparison Logic
    #     ts_hot = 0.0
    #     if hot_data:
    #         ts_hot = self._normalize_to_unix(hot_data.get('_tick_ts') or hot_data.get('ts') or hot_data.get('timestamp'))

    #     if hot_data:
    #         # ALWAYS override these specific keys from hot_data if it exists
    #         for key in ['k_1m', 'd_1m', 'k_3m', 'd_3m', 'price', '_tick_ts']:
    #             if key in hot_data:
    #                 combined_data[key] = hot_data[key]
    #                 # Map to legacy names used by SignalRater
    #                 if 'k_1m' in key: combined_data['stoch_k_1m'] = hot_data[key]
    #                 if 'k_3m' in key: combined_data['stoch_k_3m'] = hot_data[key]
    #     # if hot_data and ts_hot > ts_cold:
    #     #     combined_data.update(hot_data)
    #     #     mappings = {
    #     #         'k_1m': 'stoch_k_1m', 'd_1m': 'stoch_d_1m',
    #     #         'k_3m': 'stoch_k_3m', 'd_3m': 'stoch_d_3m',
    #     #         'price': 'current_price', 'current_price': 'price'
    #     #     }
    #     #     for s_key, l_key in mappings.items():
    #     #         if s_key in hot_data:
    #     #             val = hot_data[s_key]
    #     #             combined_data[l_key] = val
    #     #             combined_data[s_key] = val
        
    #     # E. Final Polish
    #     k_1m = self._safe_float(combined_data.get('stoch_k_1m', combined_data.get('k_1m')))
    #     d_1m = self._safe_float(combined_data.get('stoch_d_1m', combined_data.get('d_1m')))
    #     k_3m = self._safe_float(combined_data.get('stoch_k_3m', combined_data.get('k_3m')))
    #     d_3m = self._safe_float(combined_data.get('stoch_d_3m', combined_data.get('d_3m')))
        
    #     final_ts = max(ts_hot, ts_cold)
        
    #     metrics = {
    #         'k_1m': k_1m, 'd_1m': d_1m, 'k_3m': k_3m, 'd_3m': d_3m,
    #         'k_1m_prev': self._safe_float(combined_data.get('k_1m_prev'), k_1m),
    #         'k_3m_prev': self._safe_float(combined_data.get('k_3m_prev'), k_3m),
    #         '_tick_ts': final_ts # This is used by log_stoch_snapshot for the 'Age' column
    #     }
        
    #     is_fresh = (final_ts > 0) and (abs(now - final_ts) < 60.0)
    #     return metrics, combined_data, k_1m, d_1m, k_3m, d_3m, is_fresh

    async def get_fresh_price(self, symbol: str) -> tuple[float, float]:
        clean_sym = self._resolve_symbol(symbol)
        best_p, best_ts = 0.0, 0.0

        # Check Redis mark_price (Fastest update, usually 1s)
        if self.redis_manager:
            try:
                client = self.redis_manager.connections.get("local")
                raw = await client.get(f"mark_price:{clean_sym}")
                if raw:
                    d = orjson.loads(raw)
                    best_p = self._safe_float(d.get('price') or d.get('p'), 0.0)
                    best_ts = self._normalize_to_unix(d.get('timestamp') or d.get('E'))
            except: pass

        # Check Indicator State
        m, i, _, _, _, _, _ = await self.get_hot_state(clean_sym)
        p_hot = self._safe_float(i.get('current_price') or i.get('price'), 0.0)
        ts_hot = m.get('_tick_ts', 0.0)
        if ts_hot > best_ts:
            best_p, best_ts = p_hot, ts_hot

        # Check Disk Caches (N=8)
        dp, dts = self._get_price_from_disk_caches(clean_sym)
        if dts > best_ts:
            best_p, best_ts = dp, dts

        return best_p, best_ts

    def get_fresh_price_sync(self, symbol: str) -> tuple[float, float]:
        clean = self._resolve_symbol(symbol)
        # Check high-speed disk caches first
        best_p, best_ts = self._get_price_from_disk_caches(clean)

        # Fallback to cold disk dictionary
        cold = self._cold_data.get(clean, {})
        cts = self._normalize_to_unix(cold.get('timestamp'))
        if cts > best_ts:
            best_p = self._safe_float(cold.get('current_price') or cold.get('close'), 0.0)
            best_ts = cts

        return best_p, best_ts

    def _get_price_from_disk_caches(self, symbol: str) -> tuple[float, float]:
        best_p, best_ts = 0.0, 0.0
        for i in range(1, 9):
            try:
                path = config.BASE_PATH / f"price_cache_{i}.json"
                if not path.exists(): continue
                with open(path, 'rb') as f:
                    data = orjson.loads(f.read())
                    entry = data.get(symbol)
                    if entry:
                        if isinstance(entry, dict):
                            p = self._safe_float(entry.get('price') or entry.get('p'), 0.0)
                            t = self._normalize_to_unix(entry.get('timestamp') or entry.get('ts') or entry.get('E'))
                        else:
                            p = self._safe_float(entry, 0.0)
                            t = path.stat().st_mtime
                        if t > best_ts: best_p, best_ts = p, t
            except: continue
        return best_p, best_ts

    def update_price_direct(self, *args): pass
    async def inject_data(self, *args): pass
    async def update_buffers(self): pass
    async def start_redis_listener(self): pass
    
    # async def get_fresh_price(self, symbol):
    #     m, i, _, _, _, _, _ = await self.get_hot_state(symbol)
    #     p = self._safe_float(i.get('current_price') or i.get('close') or i.get('price'), 0.0)
    #     return p, 0

    # def get_fresh_price_sync(self, symbol: str) -> tuple[float, float]:
    #     clean = self._resolve_symbol(symbol)
    #     if clean in self._local_cache: return self._local_cache[clean]
    #     cold = self._cold_data.get(clean, {})
    #     p = self._safe_float(cold.get('current_price') or cold.get('close'), 0.0)
    #     return p, 0

    # async def get_fresh_indicators(self, symbol):
    #     _, indicators, _, _, _, _, is_fresh = await self.get_hot_state(symbol)
    #     return indicators, not is_fresh

    # Dead stubs 
# class FastDataManager:
#     def __init__(self, redis_manager, data_dir: Path, trade_manager=None, tracker_manager=None, ws_manager=None, positions_service=None):
#         self.redis = redis_manager
#         self.shared_proxy = None
#         self._local_cache = {} 
#         # Connect to L1
#         asyncio.create_task(self._maintain_shared_memory_connection())

#     async def _maintain_shared_memory_connection(self):
#         while True:
#             if self.shared_proxy is None:
#                 try:
#                     from ez_share_ind import get_shared_memory_client
#                     client = get_shared_memory_client()
#                     if client:
#                         self.shared_proxy = client.get_store()  # pylint: disable=no-member
#                         # Simple health check
#                         self.shared_proxy.get_symbol("BTCUSDC")
#                         # logger.info("✅ [FAST_DATA] Connected to Shared Memory")
#                 except: pass
#             await asyncio.sleep(5)

#     def _resolve_symbol(self, input_symbol: str) -> str:
#         """
#         CRITICAL FIX: Converts 'men:BTCUSDC_LONG' -> 'BTCUSDC'
#         The DB only knows 'BTCUSDC'.
#         """
#         s = input_symbol
#         # 1. Strip Account Prefix (everything before first :)
#         if ':' in s:
#             s = s.split(':')[-1]
        
#         # 2. Strip Strategy Suffixes
#         s = s.replace('_LONG', '').replace('_SHORT', '')
        
#         return s.strip().upper()

#     async def get_hot_state(self, symbol: str):
#         """
#         Single Source of Truth.
#         Returns tuple: (metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_data_fresh)
#         """
#         # 1. CLEAN THE SYMBOL
#         # If we ask for 'men:BTC_LONG', we must look up 'BTC'
#         lookup_symbol = self._resolve_symbol(symbol)
        
#         data = None
        
#         # 2. L1: Shared Memory
#         if self.shared_proxy:
#             try:
#                 raw = self.shared_proxy.get_symbol(lookup_symbol)
#                 if raw: data = dict(raw)
#             except: 
#                 self.shared_proxy = None 

#         # 3. L2: Redis
#         if not data and self.redis:
#             try:
#                 client = self.redis.connections.get("local")
#                 if client:
#                     # Look up the CLEAN symbol
#                     raw_json = await client.get(f"hot_metrics:{lookup_symbol}")
#                     if raw_json: data = orjson.loads(raw_json)  # type: ignore # pylint: disable=no-member,c-extension-no-member
#             except: pass

#         # 4. Defaults (If still no data)
#         if not data:
#             # Return defaults but with False freshness flag
#             return {}, {}, 50.0, 50.0, 50.0, 50.0, False

#         # 5. Extract Data
#         tick_ts = float(data.get('_tick_ts', 0) or data.get('ts', 0))
#         price = float(data.get('price', 0) or data.get('current_price', 0))
        
#         # Update local sync cache for legacy calls (store under the COMPLEX key too if needed)
#         if price > 0: 
#             self._local_cache[symbol] = (price, tick_ts) # Store for caller's key
#             self._local_cache[lookup_symbol] = (price, tick_ts) # Store for clean key

#         k_1m = float(data.get('k_1m', 50)); d_1m = float(data.get('d_1m', 50))
#         k_3m = float(data.get('k_3m', 50)); d_3m = float(data.get('d_3m', 50))

#         # 6. Build Metrics (Legacy format)
#         metrics = {
#             'k_1m': k_1m, 'd_1m': d_1m, 'k_3m': k_3m, 'd_3m': d_3m,
#             'k_1m_prev': k_1m, 'k_3m_prev': k_3m,
#             'ts1': data.get('ts1'),
#             'ts3': data.get('ts3'),
#             '_tick_ts': tick_ts
#         }
        
#         # 7. Build Indicators (Legacy format)
#         indicators = data.copy()
#         indicators.update({
#             'stoch_k_1m': k_1m, 'stoch_d_1m': d_1m,
#             'stoch_k_3m': k_3m, 'stoch_d_3m': d_3m,
#             'stoch_k_15m': k_1m, 'stoch_d_15m': d_1m,
#             'current_price': price,
#             # Inject timestamps so checks pass
#             'timestamp': datetime.fromtimestamp(tick_ts, tz=timezone.utc).isoformat() if tick_ts > 0 else None
#         })

#         # Freshness Check (15s tolerance)
#         is_fresh = (time.time() - tick_ts) < 15.0
        
#         return metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_fresh

#     # --- COMPATIBILITY WRAPPERS ---
#     async def get_fresh_price(self, symbol):
#         metrics, indicators, _, _, _, _, _ = await self.get_hot_state(symbol)
#         p = indicators.get('current_price', 0.0)
#         ts = metrics.get('_tick_ts', 0.0)
#         return p, ts

#     def get_fresh_price_sync(self, symbol: str) -> tuple[float, float]:
#         return self._local_cache.get(symbol, (0.0, 0.0))

#     async def get_fresh_indicators(self, symbol):
#         _, indicators, _, _, _, _, is_fresh = await self.get_hot_state(symbol)
#         if not indicators: return {}, True # Return True to avoid "Stale" crash if we just lack data
#         return indicators, not is_fresh

#     # Dead methods stubs
#     def update_price_direct(self, *args): pass
#     async def inject_data(self, *args): pass
#     async def update_buffers(self): pass
#     async def start_redis_listener(self): pass

# class FastDataManager:
#     def __init__(self, redis_manager, data_dir: Path, trade_manager=None, tracker_manager=None, ws_manager=None, positions_service=None):
#         self.redis = redis_manager
#         self.shared_proxy = None
#         # Local cache for legacy sync calls
#         self._local_cache = {} 
#         # Connect to L1
#         asyncio.create_task(self._maintain_shared_memory_connection())

#     async def _maintain_shared_memory_connection(self):
#         while True:
#             if self.shared_proxy is None:
#                 try:
#                     from ez_share_ind import get_shared_memory_client
#                     client = get_shared_memory_client()
#                     if client:
#                         self.shared_proxy = client.get_store()  # pylint: disable=no-member
#                         self.shared_proxy.get_symbol("BTCUSDC") # Health check
#                 except: pass
#             await asyncio.sleep(5)

#     async def get_hot_state(self, symbol: str):
#         """
#         Single Source of Truth.
#         Returns tuple: (metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_data_fresh)
#         """
#         symbol = symbol.upper()
#         data = None
        
#         # 1. L1: Shared Memory
#         if self.shared_proxy:
#             try:
#                 raw = self.shared_proxy.get_symbol(symbol)
#                 if raw: data = dict(raw)
#             except: self.shared_proxy = None 

#         # 2. L2: Redis
#         if not data and self.redis:
#             try:
#                 client = self.redis.connections.get("local")
#                 if client:
#                     raw_json = await client.get(f"hot_metrics:{symbol}")
#                     if raw_json: data = orjson.loads(raw_json)  # type: ignore # pylint: disable=no-member,c-extension-no-member
#             except: pass

#         # 3. Defaults
#         if not data:
#             return {}, {}, 50.0, 50.0, 50.0, 50.0, False

#         # 4. Extract
#         tick_ts = float(data.get('_tick_ts', 0) or data.get('ts', 0))
#         price = float(data.get('price', 0) or data.get('current_price', 0))
        
#         # Update local sync cache for legacy calls
#         if price > 0: self._local_cache[symbol] = (price, tick_ts)

#         k_1m = float(data.get('k_1m', 50)); d_1m = float(data.get('d_1m', 50))
#         k_3m = float(data.get('k_3m', 50)); d_3m = float(data.get('d_3m', 50))

#         # 5. Build Metrics (Legacy format)
#         metrics = {
#             'k_1m': k_1m, 'd_1m': d_1m, 'k_3m': k_3m, 'd_3m': d_3m,
#             'k_1m_prev': k_1m, 'k_3m_prev': k_3m, # Proxies
#             'ts1': data.get('ts1'),
#             'ts3': data.get('ts3'),
#             '_tick_ts': tick_ts
#         }
        
#         # 6. Build Indicators (Legacy format)
#         indicators = data.copy()
#         indicators.update({
#             'stoch_k_1m': k_1m, 'stoch_d_1m': d_1m,
#             'stoch_k_3m': k_3m, 'stoch_d_3m': d_3m,
#             'stoch_k_15m': k_1m, 'stoch_d_15m': d_1m,
#             'current_price': price })
#         is_fresh = (time.time() - tick_ts) < 15.0
#         return metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_fresh

#     # --- COMPATIBILITY WRAPPERS ---
#     async def get_fresh_price(self, symbol):
#         # Trigger fetch to update cache
#         metrics, indicators, _, _, _, _, _ = await self.get_hot_state(symbol)
#         p = indicators.get('current_price', 0.0)
#         ts = metrics.get('_tick_ts', 0.0)
#         return p, ts

#     def get_fresh_price_sync(self, symbol: str) -> tuple[float, float]:
#         return self._local_cache.get(symbol, (0.0, 0.0))

#     async def get_fresh_indicators(self, symbol):
#         _, indicators, _, _, _, _, is_fresh = await self.get_hot_state(symbol)
#         if not indicators: return {}, True
#         return indicators, not is_fresh

#     # Dead methods stubs
#     def update_price_direct(self, *args): pass
#     async def inject_data(self, *args): pass
#     async def update_buffers(self): pass
#     async def start_redis_listener(self): pass
        
class WebSocketManager:
    def __init__(self, account_keys, api_key, api_secret, config, tracker_manager, trade_manager, data_manager=None, positions_service=None, mode='FULL'):
        self.account_keys = account_keys
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = Client(api_key=api_key, api_secret=api_secret)
        self.session = None
        self._running = True
        self.tracker_manager = tracker_manager
        self._verification_callbacks={}
        self.config = config
        self.trade_manager = trade_manager
        self.data_manager = data_manager
        self.positions_service = positions_service
        self.mode = mode
        self.interested_symbols = set()
        self._last_account_update = None
        self._mark_price_session = None

    async def start(self):
        self.session = aiohttp.ClientSession()
        for acc in self.account_keys:
            asyncio.create_task(self._maintain_user_stream(acc))
        logger.info(f"⚡ [WS_MANAGER] User Streams Started for {self.account_keys}")

    async def _maintain_user_stream(self, account_key):
        while self._running:
            try:
                listen_key = await asyncio.to_thread(self.client.futures_stream_get_listen_key)
                url = f"wss://fstream.binance.com/ws/{listen_key}"
                asyncio.create_task(self._keepalive(listen_key))
                async with self.session.ws_connect(url, heartbeat=30) as ws:
                    logger.info(f"[{account_key}] User Stream Connected")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = orjson.loads(msg.data)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                            if data.get('e') == 'ACCOUNT_UPDATE':
                                await self._handle_account_update(data, account_key)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR): 
                            break
            except Exception as e:
                logger.error(f"[{account_key}] WS Error: {e}")
                await asyncio.sleep(5)

    async def _handle_account_update(self, data, account_key):
        try:
            update = data.get('a', {})
            self._last_account_update = { 'data': data,  'timestamp': time.time(), 'account': account_key }
            for cb in list(self._verification_callbacks.values()):
                try:
                    await cb(data)
                except:
                    pass
            for p in update.get('P', []):
                sym = p['s']
                amt = float(p['pa'])
                entry_price = float(p['ep'])
                # Only sync if we have a position
                if abs(amt) > 0:
                    pos_side = 'LONG' if amt > 0 else 'SHORT'
                    key = f"{account_key}:{sym}_{pos_side}"
                    await self.tracker_manager.sync_from_ws_event(account_key, key, abs(amt), entry_price)
        except Exception as e: 
            logger.error(f"WS Account Update Error: {e}")

    def register_verification_callback(self, callback_id: str, callback):
        self._verification_callbacks[callback_id] = callback
    
    def unregister_verification_callback(self, callback_id: str):
        self._verification_callbacks.pop(callback_id, None)

    async def _keepalive(self, lk):
        while self._running:
            await asyncio.sleep(1500)
            try: await asyncio.to_thread(self.client.futures_stream_keepalive, lk)
            except: break

    async def _update_interests_from_tracker(self):
        """Pull latest active keys from Tracker and add to WS interest set"""
        if not self.tracker_manager: return
        added_count = 0
        
        # 1. Check Entry Candidates
        async with self.tracker_manager._entry_candidates_lock:
            for k in self.tracker_manager.entry_candidates.keys():
                if ':' in k:
                    sym = k.split(':')[1].replace('_LONG','').replace('_SHORT','')
                    if sym not in self.interested_symbols:
                        self.interested_symbols.add(sym)
                        added_count += 1

        # 2. Check Exit Candidates
        async with self.tracker_manager._exit_candidates_lock:
            for k in self.tracker_manager.exit_candidates.keys():
                if ':' in k:
                    sym = k.split(':')[1].replace('_LONG','').replace('_SHORT','')
                    if sym not in self.interested_symbols:
                        self.interested_symbols.add(sym)
                        added_count += 1
                        
        keys = await self.tracker_manager._get_tradeable_keys_cached()
        for acc in self.account_keys:
            keys = {k for k in keys if k.startswith(f"{acc}:")}

            for k in keys:
                if ':' in k:
                    sym = k.split(':')[1].replace('_LONG','').replace('_SHORT','')
                    if sym not in self.interested_symbols:
                        self.interested_symbols.add(sym)
                        added_count += 1

        if added_count > 0:
            logger.info(f"📡 [WS_MANAGER] Auto-expanded interest list by {added_count} symbols. Total: {len(self.interested_symbols)}")
    
    async def _mark_price_loop(self):
        url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"

        while self._running:
            # 1. Create FRESH session for isolation (Vital for long-running connections)
            # 4MB limit for large JSON payloads, 30s read timeout
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=30)

            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    # 2. Connect
                    logger.info(f"📡 [mark_price] Connecting to Firehose...")

                    async with session.ws_connect(
                            url,
                            heartbeat=15,  # Auto-ping every 15s
                            max_msg_size=8 * 1024 * 1024,  # 8MB limit (Fixes truncation errors)
                            autoping=True
                    ) as ws:
                        logger.info(f"✅ [mark_price] Connected.")

                        # 3. Fast Loop
                        async for msg in ws:
                            if not self._running: break

                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    # Fast Parse
                                    raw_data = orjson.loads(msg.data)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                                    # Handle both raw list and {"data": ...} formats
                                    payloads = raw_data.get('data', []) if isinstance(raw_data, dict) else raw_data

                                    now_sys = time.time()

                                    # Fast Update Loop
                                    for p in payloads:
                                        sym = p['s']

                                        current_price = float(p['p'])
                                        event_ms = p.get('E')
                                        packet_ts = event_ms / 1000.0 if event_ms else now_sys
                                        
                                        # Update Internal State
                                        # self.tracker_manager.update_local_tohlcv(sym, current_price, packet_ts)
                                        
                                        if hasattr(self.trade_manager, 'price_cache'):
                                            self.trade_manager.price_cache[sym] = {'price': current_price, 'timestamp': packet_ts}

                                        # Update Position Objects (Live PnL) if needed
                                        if self.positions_service:
                                            # Update positions directly in the dict for speed
                                            for acc_key in self.account_keys:
                                                positions = self.positions_service.positions_by_account.get(acc_key, {})
                                                # Check Long
                                                l_key = f"{acc_key}:{sym}_LONG"
                                                if l_key in positions:
                                                    positions[l_key].mark_price = current_price
                                                # Check Short
                                                s_key = f"{acc_key}:{sym}_SHORT"
                                                if s_key in positions:
                                                    positions[s_key].mark_price = current_price
                                    
                                    # Yield to event loop to allow other tasks to run
                                    await asyncio.sleep(0)

                                except Exception as parse_e:
                                    continue

                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning(f"⚠️ [mark_price] WebSocket closed unexpectedly.")
                                break

            except Exception as e:
                logger.error(f"❌ [mark_price] Connection failed: {e}")
                await asyncio.sleep(5)  # Wait before retry
    
    async def stop(self):
        self._running = False
        if self.session: await self.session.close()
        if self._mark_price_session: await self._mark_price_session.close()


def _sync_atomic_write_tracker(tmp_path: str, final_path: str, content: bytes):
    try:
        with open(tmp_path, 'wb') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, final_path)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise e

class TrackerManager:
    def __init__(self, base_path: Path,  trade_manager=None, positions_service=None, redis_manager=None, target_account: str = None, data_manager=None, registry=None, hedge_engine=None):
        self.base_path = base_path
        self.target_account = target_account 
        self.registry = registry
        self.hedge_engine = hedge_engine         
        self.exit_candidates: Dict[str, Dict[str, Any]] = {}      
        self.data_manager = data_manager
        self.entry_candidates: Dict[str, Dict[str, Any]] = {}
        self.trade_manager = trade_manager
        self.positions_service = positions_service or getattr(trade_manager, "positions_service", None)
        self.redis_manager = redis_manager or getattr(trade_manager, "redis_manager", None)
        # self.stream_ohlc = StreamOHLCManager()
        # self._local_tohlcv: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1800))
        self.positions: Dict[str, Position] = {}
        self.positions_by_account: Dict[str, Dict[str, Position]] = defaultdict(dict)
        self._position_memory: Dict[str, Position] = {} 
        self._exit_candidates_lock = DummyLock()  
        self._entry_candidates_lock = DummyLock()
        self._processing_lock = DummyLock()
        self._trade_cooldown_lock = DummyLock()
        self._trade_cooldown = {}
        self._cache_lock = DummyLock()  
        self._timing_lock = DummyLock()
        self._file_lock = DummyLock()
        self._exit_candidates_dirty: Dict[str, bool] = {}
        self._entry_candidates_dirty: Dict[str, bool] = {}
        self._last_tracker_save_time: Dict[str, float] = {}
        self.active_hedges: List[Dict[str, Any]] = []
        self.hedge_liability_cooldowns: Dict[str, float] = {} # losing_position_key -> timestamp
        self._hedges_lock = asyncio.Lock() 
        self.tradeable_keys_cache = None
        self._tradeable_keys_mtime = 0
        self._tradeable_keys_path = self.base_path / "tradeable_keys.json"
        self.TRADE_COOLDOWN_SECONDS = 60.0 
        cache_dir = getattr(Config, 'KLINES_CACHE_DIR', Path.home() / 'binance' / 'klines_cache')
        # self.klines_manager = klines_manager or QuickKLinesManager(cache_dir, tracker_manager=self)
        if self.target_account: self._last_tracker_save_time[self.target_account] = 0.0
        else:
            allowed_accounts = getattr(trade_manager, '_allowed_accounts', getattr(trade_manager, 'accounts', {}).keys())
            for account_key in allowed_accounts: self._last_tracker_save_time[account_key] = 0.0
        self._last_field_calculation: Dict[str, float] = {}
        self._calculation_interval = 60.0  # Recalculate every 60 seconds
        self._data_retention_hours = 72  # Keep only last 3 days of data
        self._min_entries_for_stats = 5  # Minimum trades for meaningful stats
        self._price_cache: Dict[str, Tuple[float, float]] = {}  # symbol: (price, timestamp)
        self.restored_positions: Set[str] = set()        
        self._cache_ttl = 30.0  
        self._watched_symbols: Set[str] = set()
        self.always_watch = {'BTCUSDC', 'ETHUSDC', 'BNBUSDC', 'SOLUSDC', 'XRPUSDC', 'DOGEUSDC', 'ADAUSDC', 'TRUMPUSDC'}
        self._watched_symbols.update(self.always_watch)
        self.last_check_times: Dict[str, float] = {} 
        self.active_tasks: Dict[str, asyncio.Task] = {} 
        self.key_persistence: Dict[str, float] = {} # {position_key: last_seen_valid_timestamp}
        self._account_lag_status: Dict[str, bool] = defaultdict(bool)
        self.is_optimistic = False
        self.optimistic_ts = {}
        self.THROTTLE_NORMAL = 20.0     
        self.THROTTLE_CRITICAL = 120.0 
        self._last_keys_check_ts = 0
        self._last_sync_pos_keys_ts = {}
        self._last_sync_universe_ts = {}
        self._last_file_read = float(time.time()) - 240
        self.config = config
        self.accounts = {}
        self._processing_orders ={}
        self.tradeable_position_keys= {}

    def set_lag_status(self, account_key: str, is_lagging: bool):
        self._account_lag_status[account_key] = is_lagging

    def is_system_lagging(self, account_key: str) -> bool:
        return self._account_lag_status.get(account_key, False)

    async def register_check(self, position_key: str):
        async with self._timing_lock:
            self.last_check_times[position_key] = time.time()
    def get_last_check_time(self, position_key: str) -> float:
        return self.last_check_times.get(position_key, 0.0)
        
    def check_can_proceed(self, account_key: str, current_key: str) -> bool:
        now = time.time()
        if current_key not in self.last_check_times:
            self.last_check_times[current_key] = 0.0
        universe = self.tradeable_position_keys.copy()
        if self.positions_service:
            real_positions = self.positions_service.positions_by_account.get(account_key, {})
            for k, pos in real_positions.items():
                if abs(safe_fetch_float(getattr(pos, 'positionAmt', 0.0), 0.0)) > 0:
                    universe.add(k)
        critical_laggards = []
        for k in universe:
            last_ts = self.last_check_times.get(k, 0.0)
            lag = now - last_ts
            if lag > self.THROTTLE_CRITICAL:
                critical_laggards.append(k)
        if critical_laggards:
            if current_key in critical_laggards:
                return True
            else:
                if random.random() < 0.01:
                    logger.warning(f"⛔ [THROTTLE] Blocking {current_key}. Waiting for {len(critical_laggards)} laggards (e.g., {critical_laggards[0]})")
                return False
        last_check = self.last_check_times.get(current_key, 0.0)
        if (now - last_check) < self.THROTTLE_NORMAL:
            return False 
        return True

    async def identify_active_scalps(self, account_key: str) -> List[str]:
        """Identifies positions that are tagged as scalps or breakout plays."""
        scalp_keys = []
        async with self._exit_candidates_lock:
            for k, v in self.exit_candidates.items():
                if k.startswith(account_key):
                    reason = str(v.get('last_reason', '')).upper()
                    if 'SCALP' in reason or 'BREAKOUT' in reason:
                        scalp_keys.append(k)
        return scalp_keys

    async def get_position(self, position_key: str):
        live_pos = None
        if self.positions_service:
            # Check flat dict
            if hasattr(self.positions_service, 'positions'):
                live_pos = self.positions_service.positions.get(position_key)
            
            # Check nested dict (Account-based)
            if not live_pos and hasattr(self.positions_service, 'positions_by_account'):
                try:
                    acc = position_key.split(':')[0]
                    bucket = self.positions_service.positions_by_account.get(acc, {})
                    live_pos = bucket.get(position_key)
                except Exception: pass

        # 2. Try Redis (L2)
        if not live_pos and self.redis_manager:
            try:
                acc = position_key.split(':')[0]
                raw_data = await self.redis_manager.get(f"positions:{acc}")
                if raw_data:
                    data = safe_json_loads(raw_data)
                    positions_dict = data.get("positions", data)
                    pos_data = positions_dict.get(position_key)
                    if pos_data:
                        live_pos = Position.from_dict(pos_data)
            except Exception: pass

        # --- FIX START: Memory Logic ---
        
        # A. If we found a LIVE position, update our Sticky Memory and return it
        if live_pos:
            self._position_memory[position_key] = live_pos
            return live_pos

        # B. If Live failed, check Sticky Memory (Glitch Protection)
        if position_key in self._position_memory:
            # OPTIONAL: Check age if you track timestamps, but for now, 
            # trust memory over "it vanished".
            return self._position_memory[position_key]
            
        # C. Last Resort: Disk (Existing logic)
        try:
            acc, sym, side = parse_position_key(position_key)
            file_path = self.base_path / acc / f"{side.lower()}_positions.json"
            if file_path.exists():
                async with aiofiles.open(file_path, "rb") as f:
                    content = await f.read()
                    data = safe_json_loads(content)
                    positions_dict = data.get("positions", data)
                    if position_key in positions_dict:
                        disk_pos = Position.from_dict(positions_dict[position_key])
                        self._position_memory[position_key] = disk_pos # Cache this too
                        return disk_pos
        except Exception:
            pass            
        return None
    
    async def _get_tradeable_keys_cached(self) -> set:
        current_time = time.time()
        # Cache hit
        if self.tradeable_keys_cache and (current_time - self._tradeable_keys_mtime < 180):
            return self.tradeable_keys_cache

        keys_loaded = False
        new_keys = set()

        # 1. REDIS ATTEMPT
        if self.redis_manager:
            try:
                client = self.redis_manager.connections.get('local')
                if client:
                    raw = await asyncio.wait_for(client.get("tradeable_keys"), timeout=0.2)
                    if raw:
                        if isinstance(raw, bytes): raw = raw.decode('utf-8')
                        data = orjson.loads(raw)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                        if isinstance(data, list) and len(data) > 0:
                            new_keys = set(data)
                            keys_loaded = True
            except Exception: pass

        # 2. FILE ATTEMPT (If Redis failed)
        should_read_file = (not keys_loaded) or (current_time - self._last_file_read > 180)
        
        if should_read_file:
            main_path = Path(config.BASE_PATH) / "tradeable_keys.json"
            backup_path = Path(config.BASE_PATH) / "tradeable_keys.bak.json"
            
            # Helper to try loading a specific file
            async def try_load_file(path_obj):
                if not await aio_os.path.exists(path_obj): return None
                try:
                    async with aiofiles.open(path_obj, "rb") as f:
                        content = await f.read()
                        if not content: return None
                        # Use robust decode (handles corruption)
                        try: return orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                        except: return json.loads(content.decode('utf-8', errors='ignore'))
                except Exception as e:
                    logger.warning(f"[KEYS] Error reading {path_obj.name}: {e}")
                    return None

            # Try Main File
            data = await try_load_file(main_path)
            
            # If Main failed or empty, Try Backup
            if not data or not isinstance(data, list) or len(data) == 0:
                logger.warning(f"[KEYS] Main file failed/empty. Trying backup: {backup_path}")
                data = await try_load_file(backup_path)

            if data and isinstance(data, list):
                file_keys = set(data)
                if keys_loaded: 
                    new_keys.update(file_keys)
                else: 
                    new_keys = file_keys
                keys_loaded = True
                self._last_file_read = current_time
            else:
                if not keys_loaded:
                    logger.error("[KEYS] CRITICAL: Could not load keys from Redis, Main File, or Backup!")
        if keys_loaded:
            self.tradeable_keys = {} # Clear old dict if needed
            self.tradeable_keys_cache = new_keys
            self.tradeable_keys = new_keys # Sync attribute
            self._tradeable_keys_mtime = current_time
            return new_keys
        return self.tradeable_keys_cache if self.tradeable_keys_cache else set()

    def get_tradeable_position_keys_for(self, account_key: str):
        if not self.tradeable_keys: return set()
        return {k for k in self.tradeable_keys if k.startswith(f"{account_key}:")}

    def mark_task_start(self, key: str, task: asyncio.Task):
        self.active_tasks[key] = task
        task.add_done_callback(lambda t: self.active_tasks.pop(key, None))

    def is_task_running(self, key: str) -> bool:
        return key in self.active_tasks
        
    async def get_sorted_by_staleness(self, keys: list) -> list:
        """Returns keys sorted by how long ago they were checked (Oldest First)"""
        async with self._timing_lock:
            return sorted(keys, key=lambda k: self.last_check_times.get(k, 0.0))

    async def get_lag_stats(self, keys: list, threshold: float) -> tuple[int, float]:
        """Returns count of keys lagging behind threshold and max lag"""
        now = time.time()
        lagging_count = 0
        max_lag = 0.0
        async with self._timing_lock:
            for k in keys:
                last_ts = self.last_check_times.get(k, 0.0)
                lag = now - last_ts
                if lag > threshold:
                    lagging_count += 1
                    if lag > max_lag: max_lag = lag
        return lagging_count, max_lag

    async def refresh_watched_symbols(self):
            """Rebuilds the set of symbols we want to buffer high-frequency data for."""
            new_set = set(self.always_watch)
            async with self._entry_candidates_lock:
                for k in self.entry_candidates.keys():
                    if ':' in k:
                        try:
                            new_set.add(k.split(':')[1].replace('_LONG','').replace('_SHORT',''))
                        except: pass
            async with self._exit_candidates_lock:
                for k in self.exit_candidates.keys():
                    if ':' in k:
                        try:
                            new_set.add(k.split(':')[1].replace('_LONG','').replace('_SHORT',''))
                        except: pass
            self._watched_symbols = new_set

    def _clean_old_data(self, candidate_data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove data older than 3 days from lists and logs."""
        if not candidate_data:
            return candidate_data
        
        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(hours=self._data_retention_hours)
        
        # Clean trade_log
        if 'trade_log' in candidate_data and isinstance(candidate_data['trade_log'], list):
            cleaned_log = []
            for log_entry in candidate_data['trade_log']:
                if isinstance(log_entry, dict) and 'ts' in log_entry:
                    try:
                        log_time = datetime.fromisoformat(
                            log_entry['ts'].replace('Z', '+00:00')
                        )
                        if log_time >= cutoff_time:
                            cleaned_log.append(log_entry)
                    except:
                        cleaned_log.append(log_entry)
            candidate_data['trade_log'] = cleaned_log[-200:]  # Keep max 200 recent entries
        
        # Clean gain lists (keeping only recent data, maintaining totals)
        list_fields = ['gain_list', 'gain_dollar_list']#, 'paper_gain_list', 'paper_gain_dollar_list']
        for field in list_fields:
            if field in candidate_data and isinstance(candidate_data[field], list):
                # We don't have timestamps in gain lists, so we keep last 50 entries
                # This ensures we don't accumulate data indefinitely
                candidate_data[field] = candidate_data[field][-50:]
        return candidate_data

    async def send_webhook(self, position_key: str, positionAmt:float, side: str, current_price: float, quantity: float, is_full_close: bool, reason: str) -> bool:
        try:
            account_key, symbol, position_side = parse_position_key(position_key)
            account_key = account_key.lower()
            if account_key not in self.accounts:
                self.accounts = load_accounts(self.config)
                if account_key not in self.accounts:
                    logger.critical(f"🛑 [{position_key}] Cannot execute webhook. Credentials for '{account_key}' missing.")
                    return False
            account = self.accounts.get(account_key)
            webhook_url = getattr(account, 'webhook_url', None)
            webhook_secret = getattr(account, 'webhook_secret', None)
            if not webhook_url or not webhook_secret:
                logger.error(f"🛑 [WEBHOOK_FAIL] Missing URL/Secret for {account_key}.")
                return False
            is_long = (position_side.upper() == "LONG")
            is_augmentation = (side.upper() == "BUY" and is_long) or (side.upper() == "SELL" and not is_long)
            usd_value = abs(quantity * current_price)
            payload = {"name": f"{reason}", "secret": webhook_secret, "symbol": symbol, "side": side, "positionSide": position_side}
            if is_augmentation:
                block = {"amountType": "sumUsd", "amount": f"{usd_value:.6f}"}
                payload["open"] = block
                payload["dca"] = block
                payload.pop("close", None)
            else:
                payload["close"] = {"decrease": {"type": "sumUsd", "amount": f"{usd_value:.6f}"}, "action": "close" if is_full_close else "decrease"}
                payload.pop("open", None)
                payload.pop("dca", None)
            logger.info(f"🌊  Sending quick webhook {position_key} {side}| $: {usd_value} | {reason}")
 
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=15, limit_per_host=15, force_close=False)) as session:
                try:
                    async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10, connect=5)) as resp:
                        await resp.text()
                    logger.debug(f"[{position_key}] {reason} WEBHOOK_SENT")
                except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, OSError) as e:
                    if "closing transport" in str(e).lower() or "cannot write" in str(e).lower():
                        logger.debug(f"[{position_key}] {reason} WEBH OK_ERROR: Connection closing during send: {e}")
                    else:
                        try:
                            async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=30, connect=10)) as resp:
                                await resp.text()
                                logger.debug(f"[{position_key}] {reason} WEBHOOK_SENT")
                        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, OSError) as e:
                            try:
                                async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=50, connect=30)) as resp:
                                    await resp.text()
                                    logger.debug(f"[{position_key}] {reason} WEBHOOK_SENT")
                            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, OSError) as e:
                                if "closing transport" in str(e).lower() or "cannot write" in str(e).lower():
                                    logger.debug(f"[{position_key}] {reason} WEBH OK_ERROR: Connection closing during send: {e}")
                                    logger.error(f"[{position_key}] {reason} WEBH OOK_ERROR: {e}")
                                return 'SUCCESS'
                            return False

                if hasattr(self, 'positions_service') and self.positions_service:
                    asyncio.create_task(self.positions_service.fetch_positions(account_key))

                position = await self.get_position(position_key)
                if position and position.positionAmt != positionAmt:
                    return 'SUCCESS'

                return False
        except Exception as e:
            logger.error(f"🛑 [WEBHOOK_ERROR] {e}")
            return False


    async def sync_universe(self, account_key: str) -> None:
        if not hasattr(self, '_last_sync_universe_ts'): 
            self._last_sync_universe_ts = {}
        
        now_ts = time.time()
        last_run = self._last_sync_universe_ts.get(account_key, 0)
        
        # 60 second cooldown per account
        if now_ts - last_run < 60: return
        
        self._last_sync_universe_ts[account_key] = now_ts

        if self.target_account and account_key != self.target_account: return
        
        try:
            # --- 1. LOAD CONFIGURATION ---
            # Get GLOBAL list of keys from tradeable_keys.json
            file_keys = await self._get_tradeable_keys_cached()
            if file_keys is None: file_keys = set()
            
            # STRICT FILTER: Only keep keys belonging to THIS account
            prefix = f"{account_key}:"
            account_file_keys = {k for k in file_keys if k.startswith(prefix)}
            
            # Initialize Entry Candidates
            async with self._entry_candidates_lock:
                async with self._exit_candidates_lock:
                    for k in account_file_keys:
                        if k not in self.exit_candidates and k not in self.entry_candidates:
                            try:
                                symbol_clean = k.split(':')[1].replace('_LONG','').replace('_SHORT','')
                                self.entry_candidates[k] = {
                                    "symbol": symbol_clean,
                                    "status": "scanning",
                                    "generated_by": "persistence_sync",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "last_updated": datetime.now(timezone.utc).isoformat()
                                }
                                self._entry_candidates_dirty[account_key] = True
                            except IndexError: pass

            # --- 2. GET LIVE POSITIONS ---
            if not self.positions_service: return

            positions = self.positions_service.positions_by_account.get(account_key, {})
            
            if len(positions) == 0 and hasattr(self.positions_service, 'fetch_positions'):
                await self.positions_service.fetch_positions(account_key)
                positions = self.positions_service.positions_by_account.get(account_key, {})

            # --- 3. RECONCILE POSITIONS -> TRACKER ---
            active_ghosts = set()
            min_usd = safe_fetch_float(getattr(config, 'MIN_POSITION_SIZE', 5.0), 5.0)

            for position_key, position in positions.items():
                if not position_key.startswith(prefix): continue
                
                current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
                
                # Price Fallbacks
                if current_price <= 0:
                    try:
                        _, symbol, _ = parse_position_key(position_key)
                        if self.data_manager:
                            dm_price, _ = self.data_manager.get_fresh_price_sync(symbol)
                            if dm_price > 0: current_price = dm_price
                        if current_price <= 0:
                            current_price, _ = await get_current_price(symbol)
                    except: pass

                amt = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0), 0.0))
                val = amt * current_price
                
                # Logic: Is this position substantial enough to track as an EXIT?
                # or is it a ghost (dust) we still need to know about?
                is_active_exit = val > 0 #TEMP min_usd
                
                # Add to ghosts so it remains in our universe even if removed from config
                if val > 0:
                    active_ghosts.add(position_key)

                # === CASE 1: ACTIVE POSITION ===
                if is_active_exit:                    
                    history_data = {}
                    needs_promotion = False
                    
                    if position_key not in self.exit_candidates:
                        needs_promotion = True
                        async with self._entry_candidates_lock:
                            if position_key in self.entry_candidates:
                                history_data = self.entry_candidates.pop(position_key)
                                self._entry_candidates_dirty[account_key] = True
                    
                    if needs_promotion or position_key in self.exit_candidates:
                        if not needs_promotion:
                            history_data = self.exit_candidates.get(position_key, {})

                        active_data = self._smart_merge(history_data, template_type='exit')
                        active_data = self._populate_from_position(active_data, position)
                        active_data['status'] = 'active'
                        active_data['last_updated'] = datetime.now(timezone.utc).isoformat()
                        
                        # Preserve Hedge Flags
                        is_hedge = False
                        async with self._hedges_lock:
                            for hedge in self.active_hedges:
                                if hedge.get('position_key') == position_key:
                                    is_hedge = True #TEMP OUT; break
                        if is_hedge: 
                            active_data['is_hedge'] = True
                            active_ghosts.add(position_key) #TEMP IN


                        async with self._exit_candidates_lock:
                            self.exit_candidates[position_key] = active_data
                            self._exit_candidates_dirty[account_key] = True

                # === CASE 2: POSITION IS CLOSED/DUST ===
                else:
                    is_active_in_tracker = False
                    async with self._exit_candidates_lock:
                        if position_key in self.exit_candidates:
                            is_active_in_tracker = True
                    
                    if is_active_in_tracker:
                        async with self._exit_candidates_lock:
                            history_data = self.exit_candidates.pop(position_key, {})
                            self._exit_candidates_dirty[account_key] = True
                        
                        # Only recycle if it's still allowed by config
                        if position_key in account_file_keys:
                            entry_data = self._smart_merge(history_data, template_type='entry')
                            entry_data['last_exit_timestamp'] = datetime.now(timezone.utc).isoformat()
                            
                            async with self._entry_candidates_lock:
                                self.entry_candidates[position_key] = entry_data
                                self._entry_candidates_dirty[account_key] = True

            # --- 4. FINALIZE UNIVERSE ---
            # Correctly merge filtered file keys with active positions
            final_keys = account_file_keys | active_ghosts
            self.tradeable_position_keys[account_key] = final_keys
            
            # --- 5. CLEANUP INVALID ENTRIES ---
            async with self._entry_candidates_lock:
                current_entries = list(self.entry_candidates.keys())
                for k in current_entries:
                    if k.startswith(prefix):
                        if k not in final_keys:
                            del self.entry_candidates[k]
                            self._entry_candidates_dirty[account_key] = True

        except Exception as e:
            logger.error(f"[SYNC_ERROR] {account_key}: {e}", exc_info=True)

    # async def sync_universe(self, account_key: str) -> None:
    #     if not hasattr(self, '_last_sync_universe_ts'): 
    #         self._last_sync_universe_ts = {}
        
    #     now_ts = time.time()
    #     last_run = self._last_sync_universe_ts.get(account_key, 0)
        
    #     # 60 second cooldown per account
    #     if now_ts - last_run < 60:
    #         return
        
    #     # Update timestamp immediately
    #     self._last_sync_universe_ts[account_key] = now_ts
    #     # --- COOLDOWN CHECK END ---

    #     if self.target_account and account_key != self.target_account: return
    #     try:
    #         log_counter=0
    #         # --- 1. LOAD CONFIGURATION ---
    #         file_keys = await self._get_tradeable_keys_cached()
    #         if file_keys is None: file_keys = set()
            
    #         # Keys allowed by config for this account
    #         account_file_keys = {k for k in file_keys if k.startswith(f"{account_key}:")}
    #         async with self._entry_candidates_lock:
    #             async with self._exit_candidates_lock:
    #                 for k in account_file_keys:
    #                     # If we are not tracking it as an exit or an entry...
    #                     if k not in self.exit_candidates and k not in self.entry_candidates:
    #                         # ...Initialize it as a valid Entry Candidate
    #                         self.entry_candidates[k] = {
    #                             "symbol": k.split(':')[1].replace('_LONG','').replace('_SHORT',''),
    #                             "status": "scanning",
    #                             "generated_by": "persistence_sync",
    #                             "timestamp": datetime.now(timezone.utc).isoformat(),
    #                             "last_updated": datetime.now(timezone.utc).isoformat()
    #                         }
    #                         self._entry_candidates_dirty[account_key] = True

    #         # --- 2. GET LIVE POSITIONS ---
    #         if not self.positions_service:
    #             logger.warning(f"⚠️ [SYNC] No positions service for {account_key}")
    #             return

    #         positions = self.positions_service.positions_by_account.get(account_key, {})
            
    #         # Auto-fetch if empty (Startup safety)
    #         if len(positions) == 0 and hasattr(self.positions_service, 'fetch_positions'):
    #             await self.positions_service.fetch_positions(account_key)
    #             positions = self.positions_service.positions_by_account.get(account_key, {})

    #         # --- 3. RECONCILE POSITIONS -> TRACKER ---
    #         active_ghosts = set()
    #         min_usd = safe_fetch_float(getattr(config, 'MIN_POSITION_SIZE', 5.0), 5.0)

    #         for position_key, position in positions.items():
    #             if not position_key.startswith(account_key): continue
                
    #             # A. Get Price safely (Fixes AttributeError)
    #             current_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
                
    #             # Try Data Manager if available, otherwise fallback to position mark price
    #             if self.data_manager:
    #                 try:
    #                     _, symbol, _ = parse_position_key(position_key)
    #                     dm_price, _ = self.data_manager.get_fresh_price_sync(symbol)
    #                     if dm_price > 0: current_price = dm_price
    #                 except: pass
                
    #             # Final fallback
    #             if current_price <= 0:
    #                 try: 
    #                     _, symbol, _ = parse_position_key(position_key)
    #                     current_price, _ = await get_current_price(symbol)
    #                 except: pass

    #             amt = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0), 0.0))
    #             val = amt * current_price
    #             start_size_usd = safe_fetch_float(getattr(config, 'MIN_POSITION_SIZE', 45.0), 45.0)
    #             is_active_exit = val > start_size_usd
                
    #             # This check ensures we track it even if it's not in the file anymore
    #             is_tradeable = position_key in account_file_keys # (Simplified for clarity)
                
    #             # If it's open, add it to ghosts so it gets into final_keys
    #             if val > 0:
    #                 active_ghosts.add(position_key)

    #             if is_active_exit:                    
    #                 history_data = {}

    #             is_tradeable = position_key in self.tradeable_keys# or position_key in self.tradeable_position_keys.get(account_key, set())
    #             is_active_exit = val >= 0.0
    #             # if amt > 0 and not is_tradeable:
    #             #     side='SELL' if position.position_side=='LONG' else 'BUY'
    #             #     await self.send_webhook(position_key, position.positionAmt, side, current_price, position.positionAmt, True, "NOT IN TRADEABLE_KEYS_ANYMORE")
    #             if not is_active_exit and val > 0 and not is_tradeable:
    #                 is_active_exit = True
    #             if is_active_exit:                    
    #                 history_data = {}
    #                 needs_promotion = False
    #                 active_ghosts.add(position_key)
    #                 if position_key not in self.exit_candidates:
    #                     needs_promotion = True
    #                     async with self._entry_candidates_lock:
    #                         if position_key in self.entry_candidates:
    #                             history_data = self.entry_candidates.pop(position_key)
    #                             self._entry_candidates_dirty[account_key] = True
    #                 if needs_promotion or position_key in self.exit_candidates:
    #                     if not needs_promotion:
    #                         history_data = self.exit_candidates.get(position_key, {})

    #                     active_data = self._smart_merge(history_data, template_type='exit')
    #                     active_data = self._populate_from_position(active_data, position)
    #                     active_data['status'] = 'active'
    #                     active_data['last_updated'] = datetime.now(timezone.utc).isoformat()
                        
    #                     # Preserve Hedge Flags
    #                     is_hedge = False
    #                     async with self._hedges_lock:
    #                         for hedge in self.active_hedges:
    #                             if hedge.get('position_key') == position_key:
    #                                 is_hedge = True
    #                                 break
    #                     if is_hedge: active_data['is_hedge'] = True

    #                     async with self._exit_candidates_lock:
    #                         self.exit_candidates[position_key] = active_data
    #                         self._exit_candidates_dirty[account_key] = True

    #             # === CASE 2: POSITION IS CLOSED/DUST ===
    #             else:
    #                 # Check if it's currently marked Active in tracker -> Demote it
    #                 is_active_in_tracker = False
    #                 async with self._exit_candidates_lock:
    #                     if position_key in self.exit_candidates:
    #                         is_active_in_tracker = True
                    
    #                 if is_active_in_tracker:
    #                     # Demote Logic
    #                     async with self._exit_candidates_lock:
    #                         history_data = self.exit_candidates.pop(position_key, {})
    #                         self._exit_candidates_dirty[account_key] = True
                        
    #                     # Only move to entry if it's actually in our allowed file keys
    #                     # (Otherwise we just drop it)
    #                     if position_key in account_file_keys:
    #                         entry_data = self._smart_merge(history_data, template_type='entry')
    #                         entry_data['last_exit_timestamp'] = datetime.now(timezone.utc).isoformat()
                            
    #                         async with self._entry_candidates_lock:
    #                             self.entry_candidates[position_key] = entry_data
    #                             self._entry_candidates_dirty[account_key] = True

    #         # --- 4. FINALIZE UNIVERSE ---
    #         # The Universe = Configured Keys + Any Active Ghosts
    #         final_keys = account_file_keys | active_ghosts
            
    #         # Update the defaultdict correctly
    #         self.tradeable_position_keys[account_key] = final_keys
            
    #         # --- 5. CLEANUP INVALID ENTRIES ---
    #         # Remove keys from entry_candidates if they are NEITHER in config NOR active
    #         async with self._entry_candidates_lock:
    #             # Use list() to avoid runtime error while modifying dict keys
    #             current_entries = list(self.entry_candidates.keys())
    #             for k in current_entries:
    #                 if k.startswith(f"{account_key}:"):
    #                     if k not in final_keys:
    #                         del self.entry_candidates[k]
    #                         self._entry_candidates_dirty[account_key] = True

    #         # Optional: Log if ghosts found
    #         if len(active_ghosts) > 0:
    #             ghosts_only = active_ghosts - account_file_keys
    #             log_counter += 1
    #             if log_counter % 10 == 0:
    #                 if ghosts_only:
    #                     logger.info(f"👻 [SYNC] {account_key}: Tracking {len(final_keys)} {len(ghosts_only)} ghost positions and {len(account_file_keys)} keys.")

    #     except Exception as e:
    #         logger.error(f"[SYNC_ERROR] {account_key}: {e}", exc_info=True)

    async def sync_position_keys(self, account_key: str) -> None:
        if not hasattr(self, '_last_sync_pos_keys_ts'): 
            self._last_sync_pos_keys_ts = {}
            
        now_ts = time.time()
        last_run = self._last_sync_pos_keys_ts.get(account_key, 0)
        
        # 60 second cooldown per account
        if now_ts - last_run < 60:
            return
            
        self._last_sync_pos_keys_ts[account_key] = now_ts
        # --- COOLDOWN CHECK END ---

        try:
            if not self.positions_service: return
            positions = self.positions_service.positions_by_account.get(account_key, {})
            if len(positions) == 0:
                # Trigger fetch if empty
                if hasattr(self.positions_service, 'fetch_positions'):
                    await self.positions_service.fetch_positions(account_key)
                    positions = self.positions_service.positions_by_account.get(account_key, {})
            
            min_usd = safe_fetch_float(getattr(config, 'MIN_POSITION_SIZE', 5.0), 5.0)
            
            for k, pos in positions.items():
                if not k.startswith(account_key): continue
                
                amt = abs(safe_fetch_float(getattr(pos, 'positionAmt', 0), 0))
                price = safe_fetch_float(getattr(pos, 'mark_price', 0), 0)
                
                if price <= 0:
                    ak, sym, ps = parse_position_key(k)
                    price,_ = await self.data_manager.get_fresh_price(sym)
                    
                val = amt * price
                
                # --- POSITION IS ACTIVE ---
                if val > 0.0:
                    # Hedge positions must NOT enter tradeable_position_keys
                    is_hedge = False
                    async with self._exit_candidates_lock:
                        ec = self.exit_candidates.get(k, {})
                        is_hedge = ec.get('is_hedge', False) or ec.get('hedge_for')
                    if not is_hedge:
                        self.tradeable_position_keys[account_key].add(k)
                    # else:
                    #     self.tradeable_position_keys[account_key] = {k}

                    # Promote to Active
                    if k not in self.exit_candidates:
                        
                        logger.info(f"👻 [GHOST] Found real position {k}. Promoting to Active.")
                        
                        history = {}
                        async with self._entry_candidates_lock:
                            if k in self.entry_candidates: 
                                history = self.entry_candidates.pop(k)
                                self._entry_candidates_dirty[account_key] = True

                        new_data = self._smart_merge(history, template_type='exit')
                        new_data = self._populate_from_position(new_data, pos)
                        new_data['status'] = 'active'
                        new_data['last_updated'] = datetime.now(timezone.utc).isoformat()
                        
                        self.exit_candidates[k] = new_data
                        self._exit_candidates_dirty[account_key] = True

                # --- POSITION IS EMPTY ---
                else:
                    # Demote to Entry
                    async with self._exit_candidates_lock:
                        if k in self.exit_candidates:
                            cand = self.exit_candidates[k]
                            if cand.get('status') == 'PENDING_OPEN':
                                ts = cand.get('timestamp')
                                if isinstance(ts, datetime) and (datetime.now(timezone.utc) - ts).total_seconds() < 30: continue
                            
                            history = self.exit_candidates.pop(k)
                            self._exit_candidates_dirty[account_key] = True
                            
                            entry_data = self._smart_merge(history, template_type='entry')
                            entry_data['last_exit_timestamp'] = datetime.now(timezone.utc).isoformat()
                            
                            async with self._entry_candidates_lock:
                                self.entry_candidates[k] = entry_data
                                self._entry_candidates_dirty[account_key] = True
                                
            await self.save_tracker(account_key, force=True)
        except Exception as e:
            logger.error(f"[POS_SYNC] {account_key}: {e}", exc_info=True)

    def _merge_with_defaults(self, existing_data: Dict[str, Any], template_type: str = 'entry') -> Dict[str, Any]:
        merged = existing_data.copy() if existing_data else {}
        defaults = self._entry_template()
        if template_type == 'exit':
            defaults.update({'status': 'active', 'positionAmt': 0.0, 'entry_price': 0.0, 'average_entry_price': 0.0})
        for k, v in defaults.items():
            if k not in merged:
                if isinstance(v, datetime): merged[k] = datetime.now(timezone.utc)
                elif isinstance(v, (list, dict)): merged[k] = v.copy() if hasattr(v, 'copy') else v
                else: merged[k] = v
        return merged
        
    async def load_tracker(self, account_key: str) -> None:
        if self.target_account and account_key != self.target_account: return
        file_path = self.get_tracker_file(account_key)
        if not await aio_os.path.exists(file_path): return
        try:
            async with FILE_IO_SEMAPHORE: 
                async with aiofiles.open(file_path, 'rb') as f:
                    content = await f.read()
                    if not content.strip(): return
                    data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
            async with self._exit_candidates_lock:
                for k, v in data.get('exit_candidates', {}).items():
                    if k.startswith(f"{account_key}:"):
                        v = self._merge_with_defaults(v, 'exit')
                        v['status'] = 'active'
                        self.exit_candidates[k] = v
                for k, v in data.get('hedges', {}).items():
                    if k.startswith(f"{account_key}:"):
                        v = self._merge_with_defaults(v, 'exit')
                        v['status'] = 'active'; v['is_hedge'] = True
                        self.exit_candidates[k] = v
            async with self._entry_candidates_lock:
                for k, v in data.get('entry_candidates', {}).items():
                    if k.startswith(f"{account_key}:"):
                        v = self._merge_with_defaults(v, 'entry')
                        v['status'] = 'entry_candidate'
                        self.entry_candidates[k] = v
            if 'key_persistence' in data: self.key_persistence.update(data['key_persistence'])
            logger.info(f"[LOAD] {account_key}: Tracker loaded successfully.")
        except Exception as e:
            logger.error(f"[LOAD_ERROR] {account_key}: {e}")

    # async def save_tracker(self, account_key: str, force: bool = False, min_interval: int = 30, trade_manager=None) -> None:
    #     if not account_key: return
    #     now_ts = time.time()
    #     dirty = (self._exit_candidates_dirty.get(account_key, False) or self._entry_candidates_dirty.get(account_key, False))
    #     last_save = self._last_tracker_save_time.get(account_key, 0)
        
    #     if not force and not dirty and (now_ts - last_save) < 300: return
    #     if not force and (now_ts - last_save) < min_interval: return
        
    #     file_path = self.get_tracker_file(account_key)
    #     tmp_path = None # Initialize to avoid UnboundLocalError in finally/except blocks
        
    #     try:
    #         def prepare_node(data_in, c_type='entry'):
    #             data = data_in.copy()
    #             # Compress lists to save space
    #             for field in ['gain_list', 'gain_dollar_list', 'pos_values_list']:
    #                 if field in data and isinstance(data[field], list):
    #                     data[field] = self._compress_list(data[field])            
                
    #             # Truncate logs
    #             if 'trade_log' in data and isinstance(data['trade_log'], list):
    #                 data['trade_log'] = data['trade_log'][-100:] 
                
    #             # Serialize dates
    #             data = self._serialize_datetime_fields(data)
    #             return self.reorder_candidate_fields(data, candidate_type=c_type)

    #         # 1. Ensure Universe is Fresh
    #         await self.sync_universe(account_key)

    #         # 2. Gather Data
    #         async with self._exit_candidates_lock:
    #             raw_exits = {k: v for k, v in self.exit_candidates.items() if k.startswith(f"{account_key}:")}
    #         async with self._entry_candidates_lock:
    #             raw_entries = {k: v for k, v in self.entry_candidates.items() if k.startswith(f"{account_key}:")}
    #         async with self._hedges_lock:
    #             current_hedges = [h.copy() for h in self.active_hedges if h.get('account') == account_key]
            
    #         keys_set = self.tradeable_position_keys.get(account_key, set())
    #         current_tradeable_keys = sorted(list(keys_set))

    #         output = {
    #             "tradeable_position_keys": current_tradeable_keys, 
    #             "exit_candidates": {}, 
    #             "entry_candidates": {},  
    #             "key_persistence": self.key_persistence,
    #             "hedges": {},               
    #             "scalps": {},               
    #             "reentry_candidates": {},   
    #             "metadata": {
    #                 "last_saved": datetime.now(timezone.utc).isoformat(),
    #                 "account": account_key,
    #                 "key_count": len(current_tradeable_keys)
    #             }  
    #         }
            
    #         handled_keys = set()
            
    #         # Hedges
    #         for h in current_hedges:
    #             pk = h.get('position_key')
    #             if pk:
    #                 rec = h.copy()
    #                 if pk in raw_exits: rec.update(raw_exits[pk])
    #                 rec['is_hedge'] = True
    #                 output['hedges'][pk] = prepare_node(rec, 'exit')
    #                 handled_keys.add(pk)
            
    #         # Exits
    #         for k, v in raw_exits.items():
    #             if k in handled_keys: continue
    #             cleaned = prepare_node(v, 'exit')
                
    #             if v.get('is_hedge', False): output['hedges'][k] = cleaned
    #             elif 'SCALP' in str(v.get('last_reason', '')).upper(): output['scalps'][k] = cleaned
    #             else: output['exit_candidates'][k] = cleaned
                
    #             handled_keys.add(k)
            
    #         # Entries
    #         for k, v in raw_entries.items():
    #             if k in handled_keys: continue
    #             cleaned = prepare_node(v, 'entry')
                
    #             if v.get('last_exit_reentry_ready', False): output['reentry_candidates'][k] = cleaned
    #             elif 'SCALP' in str(v.get('last_reason', '')).upper(): output['scalps'][k] = cleaned
    #             else: output['entry_candidates'][k] = cleaned

    #         json_bytes = orjson.dumps(output, option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_NUMPY)  # type: ignore # pylint: disable=no-member,c-extension-no-member
    #         tmp_path = file_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
            
    #         # CRITICAL FIX: Use sync IO in a thread to prevent 'Bad file descriptor'
    #         async with FILE_IO_SEMAPHORE:
    #             await asyncio.to_thread(
    #                 _sync_atomic_write_tracker, 
    #                 str(tmp_path), 
    #                 str(file_path), 
    #                 json_bytes )

    #         self._last_tracker_save_time[account_key] = now_ts
    #         self._exit_candidates_dirty[account_key] = False
    #         self._entry_candidates_dirty[account_key] = False

    #     except Exception as e:
    #         logger.error(f"[SAVE_ERROR] {account_key}: {e}", exc_info=True)
    #         # Safe cleanup
    #         try:
    #             if tmp_path and await aio_os.path.exists(tmp_path): 
    #                 await aio_os.remove(tmp_path)
    #         except: pass
    

    async def save_tracker(self, account_key: str, force: bool = False, min_interval: int = 30, trade_manager=None) -> None:
        if not account_key: return
        now_ts = time.time()
        dirty = (self._exit_candidates_dirty.get(account_key, False) or self._entry_candidates_dirty.get(account_key, False))
        last_save = self._last_tracker_save_time.get(account_key, 0)
        
        if not force and not dirty and (now_ts - last_save) < 300: return
        if not force and (now_ts - last_save) < min_interval: return
        
        file_path = self.get_tracker_file(account_key)
        tmp_path = None 
        
        try:
            def prepare_node(data_in, c_type='entry'):
                data = data_in.copy()
                for field in ['gain_list', 'gain_dollar_list', 'pos_values_list']:
                    if field in data and isinstance(data[field], list):
                        data[field] = self._compress_list(data[field])            
                if 'trade_log' in data and isinstance(data['trade_log'], list):
                    data['trade_log'] = data['trade_log'][-100:] 
                data = self._serialize_datetime_fields(data)
                return self.reorder_candidate_fields(data, candidate_type=c_type)

            # 1. Sync First
            await self.sync_universe(account_key)

            # 2. Gather Data
            prefix = f"{account_key}:"
            
            async with self._exit_candidates_lock:
                raw_exits = {k: v for k, v in self.exit_candidates.items() if k.startswith(prefix)}
            async with self._entry_candidates_lock:
                raw_entries = {k: v for k, v in self.entry_candidates.items() if k.startswith(prefix)}
            async with self._hedges_lock:
                current_hedges = [h.copy() for h in self.active_hedges if h.get('account') == account_key]
            
            # CRITICAL FIX: Force filter the universe keys to this account only
            # This prevents "universe leakage" if the global set is dirty
            raw_keys_set = self.tradeable_position_keys.get(account_key, set())
            current_tradeable_keys = sorted([k for k in raw_keys_set if k.startswith(prefix)])

            output = {
                "tradeable_position_keys": current_tradeable_keys, 
                "exit_candidates": {}, 
                "entry_candidates": {},  
                "key_persistence": self.key_persistence,
                "hedges": {},               
                "scalps": {},               
                "reentry_candidates": {},   
                "metadata": {
                    "last_saved": datetime.now(timezone.utc).isoformat(),
                    "account": account_key,
                    "key_count": len(current_tradeable_keys)
                }  
            }
            
            handled_keys = set()
            
            # Hedges
            for h in current_hedges:
                pk = h.get('position_key')
                if pk:
                    rec = h.copy()
                    if pk in raw_exits: rec.update(raw_exits[pk])
                    rec['is_hedge'] = True
                    output['hedges'][pk] = prepare_node(rec, 'exit')
                    handled_keys.add(pk)
            
            # Exits
            for k, v in raw_exits.items():
                if k in handled_keys: continue
                cleaned = prepare_node(v, 'exit')
                
                if v.get('is_hedge', False): output['hedges'][k] = cleaned
                elif 'SCALP' in str(v.get('last_reason', '')).upper(): output['scalps'][k] = cleaned
                else: output['exit_candidates'][k] = cleaned
                handled_keys.add(k)
            
            # Entries
            for k, v in raw_entries.items():
                if k in handled_keys: continue
                cleaned = prepare_node(v, 'entry')
                
                if v.get('last_exit_reentry_ready', False): output['reentry_candidates'][k] = cleaned
                elif 'SCALP' in str(v.get('last_reason', '')).upper(): output['scalps'][k] = cleaned
                else: output['entry_candidates'][k] = cleaned

            # Use Orjson with Pretty Print
            # OPT_INDENT_2 makes it readable (requires orjson >= 3.6.0)
            json_bytes = orjson.dumps(  # type: ignore # pylint: disable=no-member,c-extension-no-member
                output, 
                option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_NUMPY  # type: ignore # pylint: disable=no-member,c-extension-no-member
            )
            
            tmp_path = file_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
            
            async with FILE_IO_SEMAPHORE:
                await asyncio.to_thread(
                    _sync_atomic_write_tracker, 
                    str(tmp_path), 
                    str(file_path), 
                    json_bytes )

            self._last_tracker_save_time[account_key] = now_ts
            self._exit_candidates_dirty[account_key] = False
            self._entry_candidates_dirty[account_key] = False

        except Exception as e:
            logger.error(f"[SAVE_ERROR] {account_key}: {e}", exc_info=True)
            try:
                if tmp_path and await aio_os.path.exists(tmp_path): 
                    await aio_os.remove(tmp_path)
            except: pass



    # def _merge_with_defaults(self, existing_data: Dict[str, Any], template_type: str = 'entry') -> Dict[str, Any]:
    #     merged = existing_data.copy() if existing_data else {}
    #     defaults = self._entry_template()
    #     if template_type == 'exit':
    #         defaults.update({'status': 'active', 'positionAmt': 0.0, 'entry_price': 0.0, 'average_entry_price': 0.0})
    #     for k, v in defaults.items():
    #         if k not in merged:
    #             if isinstance(v, datetime): merged[k] = datetime.now(timezone.utc)
    #             elif isinstance(v, (list, dict)): merged[k] = v.copy() if hasattr(v, 'copy') else v
    #             else: merged[k] = v
    #     return merged



    def _smart_merge(self, existing_data: Dict[str, Any], template_type: str = 'entry') -> Dict[str, Any]:
        """
        Merges existing data into a template WITHOUT overwriting accumulated stats 
        with default zeros.
        """
        if template_type == 'exit':
            base = self._exit_template()
        else:
            base = self._entry_template()
            
        if not existing_data:
            return base

        # List of fields that represent history/accumulation and should NEVER be overwritten by 0/Empty
        history_fields = [
            'total_realized_pnl_$', 'total_trades_count', 'winning_trades', 
            'losing_trades', 'total_trades', 'win_rate_%', 'consecutive_wins', 
            'consecutive_losses', 'trade_log', 'max_gain', 'buy_hold_gain_$',
            'buy_hold_gain_%', 'total_gain_%', 'total_gain_$' ]
        
        # 1. Start with the template (contains all required keys with default values)
        merged = base.copy()
        
        # 2. Update with existing data
        for k, v in existing_data.items():
            # If the key is a history field, ALWAYS prefer the existing value if it's non-zero/non-empty
            if k in history_fields:
                if v: # If existing value is not None/0/Empty, keep it
                    merged[k] = v
                # If existing is 0 but merged (template) is 0, it stays 0
            else:
                # For non-history fields (like current price), use existing data
                merged[k] = v        
        return merged

    async def nuke_hedge_key(self, account_key: str, position_key: str):
        """
        Aggressively removes a failed hedge key from ALL lists to prevent re-entry.
        """
        logger.warning(f"☢️ [NUKE_KEY] Permanently banishing failed hedge: {position_key}")
        
        # 1. Remove from Tradeable Universe (The Allow List) - per-account dict of sets
        account_keys_set = self.tradeable_position_keys.get(account_key, set())
        if position_key in account_keys_set:
            account_keys_set.discard(position_key)
            logger.info(f"   {position_key} - Removed from tradeable_position_keys[{account_key}]")

        # 2. Remove from Entry Candidates (The Logic Queue)
        async with self._entry_candidates_lock:
            if position_key in self.entry_candidates:
                del self.entry_candidates[position_key]
                self._entry_candidates_dirty[account_key] = True
                logger.info(f"   - Removed from entry_candidates")

        # 3. Remove from Exit Candidates (Just in case)
        async with self._exit_candidates_lock:
            if position_key in self.exit_candidates:
                del self.exit_candidates[position_key]
                self._exit_candidates_dirty[account_key] = True

        # 4. Remove from Re-entry Data (The Persistence Layer)
        if self.trade_manager and hasattr(self.trade_manager, 'reentry_data'):
            if position_key in self.trade_manager.reentry_data:
                self.trade_manager.reentry_data.pop(position_key, None)
                logger.info(f"   - Removed from reentry_data")
        
        # 5. Remove from Direct High Gain (The Augmenter)
        if self.trade_manager and hasattr(self.trade_manager, 'direct_high_gain'):
            if position_key in self.trade_manager.direct_high_gain:
                self.trade_manager.direct_high_gain.pop(position_key, None)

        # 6. Force Save to persist the deletion
        await self.save_tracker(account_key, force=True)


    def _compress_list(self, data_list: List[float], tolerance: float = 1e-6) -> List[float]:
            """
            Compresses a history list by removing consecutive identical values.
            Example: [-5.0, -5.0, -5.0, -4.0] -> [-5.0, -4.0]
            """
            if not data_list:
                return []
            
            # Start with the first item
            compressed = [data_list[0]]
            
            for value in data_list[1:]:
                # Only add if the value has changed significantly from the last recorded value
                if abs(value - compressed[-1]) > tolerance:
                    compressed.append(value)
            
            # Limit to last 50 data points to prevent file bloat
            return compressed[-5:]

    def recalculate_candidate_stats(self, data: Dict[str, Any]) -> Dict[str, Any]:
        trade_log = data.get('trade_log', [])
        if not isinstance(trade_log, list):
            trade_log = []
            data['trade_log'] = trade_log
        if not trade_log:
            return data
        log_wins = 0
        log_losses = 0
        log_total = 0
        sorted_log = sorted(trade_log, key=lambda x: x.get('ts', ''))
        curr_win_streak = 0
        curr_loss_streak = 0
        
        for trade in sorted_log:
            action = trade.get('action', '')
            if action in ['CLOSE', 'REDUCE', 'PROFIT_TAKE', 'SEMI-STOP']:
                log_total += 1
                pnl = safe_fetch_float(trade.get('pnl_usd', 0), 0.0)
                if pnl > 0:
                    log_wins += 1
                    curr_win_streak += 1
                    curr_loss_streak = 0
                elif pnl < 0:
                    log_losses += 1
                    curr_loss_streak += 1
                    curr_win_streak = 0
        if log_total > 0:
            stored_total = int(data.get('total_trades', 0))
            if log_total >= stored_total:
                data['total_trades_count'] = log_total
                data['total_trades'] = log_total
                data['winning_trades'] = log_wins
                data['losing_trades'] = log_losses
                data['consecutive_wins'] = curr_win_streak
                data['consecutive_losses'] = curr_loss_streak
                if log_total > 0:
                    data['win_rate_%'] = (log_wins / log_total) * 100.0
        return data

    def ensure_all_fields(self, candidate_data: Dict[str, Any], candidate_type: str = 'entry') -> Dict[str, Any]:
        """Ensure all required fields exist with proper defaults."""
        template = self._exit_template() if candidate_type == 'exit' else self._entry_template()
        for key, default_value in template.items():
            if key not in candidate_data:
                if isinstance(default_value, datetime):
                    candidate_data[key] = datetime.now(timezone.utc)
                else:
                    candidate_data[key] = default_value
        calc_fields = ['current_pos_value', 'average_pos_value', 'unrealized_pnl_%', 'unrealized_pnl_$','total_pnl_$', 'current_gain_%', 'current_gain_$', 'buy_hold_gain_%', 'buy_hold_gain_$'] # 'paper_current_value', 'paper_avg_value', 'paper_unrealized_pnl_$', 'paper_unrealized_pnl_%' ]
        for field in calc_fields:
            if field not in candidate_data:
                candidate_data[field] = 0.0
        list_fields = ['gain_list', 'gain_dollar_list', 'trade_log']# 'paper_gain_list', 'paper_gain_dollar_list',
        for field in list_fields:
            if field not in candidate_data or not isinstance(candidate_data[field], list):
                candidate_data[field] = []
        return candidate_data
        
    async def calculate_candidate_fields(self, account_key: str, candidate_type: str, position_key: str,  candidate_data: Dict[str, Any], trade_manager, data_manager: FastDataManager = None) -> Dict[str, Any]:
        if hasattr(trade_manager, '_check_account_allowed'):
            if not trade_manager._check_account_allowed(account_key):
                logger.info(f"[calculate_candidate_fields] BLOCKED: Account '{account_key}' not in allowed accounts")
                return candidate_data
        candidate_data = self.ensure_all_fields(candidate_data, candidate_type)
        now_ts = time.time()
        candidate_data = self._clean_old_data(candidate_data)
        last_calc = self._last_field_calculation.get(position_key, 0)
        time_since_last = now_ts - last_calc
        if candidate_type == 'exit':
            needs_recalc = time_since_last >= self._calculation_interval
        else:
            positionAmt = safe_fetch_float(candidate_data.get('positionAmt', 0.0), 0.0)
            is_active = positionAmt > 0
            needs_recalc = (is_active and time_since_last >= self._calculation_interval) or \
                          (not is_active and time_since_last >= 300)  
        if not needs_recalc and candidate_data.get('_fields_fresh', False):
            return candidate_data
        symbol = position_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
        is_exit_candidate = candidate_type == 'exit'
        is_entry_candidate = candidate_type == 'entry'
        current_price = 0.0
        if data_manager:
            current_price, _ = data_manager.get_fresh_price_sync(symbol)
        if current_price <= 0:
            current_price = candidate_data.get('mark_price', 0.0)
        if not current_price or current_price <= 0: current_price, _ = await get_current_price(symbol)
        if current_price > 0:
            candidate_data['mark_price'] = current_price
            candidate_data['mark_price_last_updated'] = datetime.now(timezone.utc).isoformat()
        if is_exit_candidate:
            positionAmt = safe_fetch_float(candidate_data.get('positionAmt', 0.0), 0.0)
            entry_price = safe_fetch_float(candidate_data.get('entry_price', 0.0), 0.0)
            avg_entry_price = safe_fetch_float(candidate_data.get('average_entry_price', entry_price), entry_price)
            first_entry_price = safe_fetch_float(candidate_data.get('first_entry_price', entry_price), entry_price)
            is_long = position_key.endswith('_LONG')
            current_pos_value = positionAmt * current_price if current_price > 0 else 0.0
            candidate_data['current_pos_value'] = current_pos_value
            avg_pos_value = positionAmt * avg_entry_price if avg_entry_price > 0 else 0.0
            candidate_data['average_pos_value'] = avg_pos_value
            total_realized_pnl = safe_fetch_float(candidate_data.get('total_realized_pnl_$', 0.0), 0.0)
            
            if avg_entry_price > 0 and current_price > 0 and positionAmt > 0:
                # Unrealized PnL
                if is_long:
                    unrealized_gain = ((current_price - avg_entry_price) / avg_entry_price) * 100.0
                    unrealized_pnl_usd = (current_price - avg_entry_price) * positionAmt
                else:
                    unrealized_gain = ((avg_entry_price - current_price) / avg_entry_price) * 100.0
                    unrealized_pnl_usd = (avg_entry_price - current_price) * positionAmt
                
                candidate_data['unrealized_pnl_%'] = unrealized_gain
                candidate_data['unrealized_pnl_$'] = unrealized_pnl_usd
                
                # Total PnL
                total_pnl_usd = total_realized_pnl + unrealized_pnl_usd
                candidate_data['total_pnl_$'] = total_pnl_usd
                
                # Current gain percentage
                current_gain_percent = unrealized_gain
                candidate_data['current_gain_%'] = current_gain_percent
                
                # Update max gain if needed
                max_gain = safe_fetch_float(candidate_data.get('max_gain', 0.0), 0.0)
                if current_gain_percent > max_gain:
                    candidate_data['max_gain'] = current_gain_percent
                
                # Update gain list (only if significant change)
                gain_list = candidate_data.get('gain_list', [])
                if not isinstance(gain_list, list):
                    gain_list = []
                should_add_gain = False
                if len(gain_list) == 0:
                    should_add_gain = True
                elif time_since_last >= 60:  # At least 1 minute between entries
                    should_add_gain = True
                elif len(gain_list) > 0 and abs(gain_list[-1] - current_gain_percent) > 0.1:
                    should_add_gain = True
                
                if should_add_gain:
                    gain_list.append(current_gain_percent)
                    candidate_data['gain_list'] = gain_list[-1000:]  # Keep last 1000 entries
                
                # Calculate total gain percentage (from recent trades)
                recent_gain_list = gain_list[-50:]  # Last 50 entries (~last hour if 1 minute updates)
                total_gain_percent = sum(recent_gain_list)
                candidate_data['total_gain_%'] = total_gain_percent
                
                # Gain in dollars
                gain_dollar_list = candidate_data.get('gain_dollar_list', [])
                if not isinstance(gain_dollar_list, list):
                    gain_dollar_list = []
                
                if should_add_gain:
                    gain_dollar_list.append(unrealized_pnl_usd)
                    candidate_data['gain_dollar_list'] = gain_dollar_list[-1000:]
                
                recent_dollar_list = gain_dollar_list[-50:]
                total_gain_usd = sum(recent_dollar_list)
                candidate_data['total_gain_$'] = total_gain_usd
                
                if 'pos_values_list' not in candidate_data: candidate_data['pos_values_list'] = []
                
                # Only append if value changed significantly or it's been a while (to save space)
                current_val = candidate_data.get('current_pos_value', 0.0)
                if current_val > 0:
                    if not candidate_data['pos_values_list']:
                        candidate_data['pos_values_list'].append(current_val)
                    else:
                        last_val = candidate_data['pos_values_list'][-1]
                        # Append if changed by > 1% to create a graphable history
                        if abs(current_val - last_val) > (last_val * 0.01):
                            candidate_data['pos_values_list'].append(current_val)
                            # Keep last 100 points
                            if len(candidate_data['pos_values_list']) > 100:
                                candidate_data['pos_values_list'] = candidate_data['pos_values_list'][-100:]


                # Buy & Hold comparison
                if first_entry_price > 0:
                    if is_long:
                        buy_hold_pct = ((current_price - first_entry_price) / first_entry_price) * 100.0
                        buy_hold_usd = (current_price - first_entry_price) * positionAmt
                    else:
                        buy_hold_pct = ((first_entry_price - current_price) / first_entry_price) * 100.0
                        buy_hold_usd = (first_entry_price - current_price) * positionAmt
                    
                    candidate_data['buy_hold_gain_%'] = buy_hold_pct
                    candidate_data['buy_hold_gain_$'] = buy_hold_usd
            trade_log = candidate_data.get('trade_log', [])
            if isinstance(trade_log, list):
                cutoff_time = datetime.now(timezone.utc) - timedelta(hours=self._data_retention_hours)
                recent_trades = []
                
                for trade in trade_log:
                    try:
                        if isinstance(trade, dict) and 'ts' in trade:
                            trade_time = datetime.fromisoformat(trade['ts'].replace('Z', '+00:00'))
                            if trade_time >= cutoff_time:
                                recent_trades.append(trade)
                    except: continue
                
                winning_trades = 0
                losing_trades = 0
                total_trades = len(recent_trades)
                
                for trade in recent_trades:
                    pnl_pct = safe_fetch_float(trade.get('pnl_pct', 0), 0)
                    if pnl_pct > 0: winning_trades += 1
                    elif pnl_pct < 0: losing_trades += 1
                
                candidate_data['winning_trades'] = winning_trades
                candidate_data['losing_trades'] = losing_trades
                candidate_data['total_trades'] = total_trades
                
                if total_trades >= self._min_entries_for_stats:
                    win_rate = (winning_trades / total_trades) * 100.0
                    candidate_data['win_rate_%'] = win_rate
                else:
                    candidate_data['win_rate_%'] = 0.0
                
                consecutive_wins = 0
                consecutive_losses = 0
                last_trades = recent_trades[-10:]
                for trade in reversed(last_trades):
                    pnl_pct = safe_fetch_float(trade.get('pnl_pct', 0), 0)
                    if pnl_pct > 0:
                        consecutive_wins += 1
                        consecutive_losses = 0
                    elif pnl_pct < 0:
                        consecutive_losses += 1
                        consecutive_wins = 0
                    else: break
                
                candidate_data['consecutive_wins'] = consecutive_wins
                candidate_data['consecutive_losses'] = consecutive_losses
        
        # ===== ENTRY CANDIDATES: Light Recalculation =====
        elif is_entry_candidate:
            positionAmt = safe_fetch_float(candidate_data.get('positionAmt', 0.0), 0.0)
            
            if positionAmt > 0:
                entry_price = safe_fetch_float(candidate_data.get('entry_price', 0.0), 0.0)
                avg_entry_price = safe_fetch_float(candidate_data.get('average_entry_price', entry_price), entry_price)
                
                current_pos_value = positionAmt * current_price if current_price > 0 else 0.0
                candidate_data['current_pos_value'] = current_pos_value
                
                if avg_entry_price > 0 and current_price > 0:
                    is_long = position_key.endswith('_LONG')
                    
                    if is_long:
                        current_gain_percent = ((current_price - avg_entry_price) / avg_entry_price) * 100.0
                        current_gain_usd = (current_price - avg_entry_price) * positionAmt
                    else:
                        current_gain_percent = ((avg_entry_price - current_price) / avg_entry_price) * 100.0
                        current_gain_usd = (avg_entry_price - current_price) * positionAmt
                    
                    candidate_data['current_gain_%'] = current_gain_percent
                    candidate_data['current_gain_$'] = current_gain_usd
                    
                    max_gain = safe_fetch_float(candidate_data.get('max_gain', 0.0), 0.0)
                    if current_gain_percent > max_gain:
                        candidate_data['max_gain'] = current_gain_percent
            
            last_exit_ts = candidate_data.get('last_exit_timestamp')
            if last_exit_ts:
                try:
                    if isinstance(last_exit_ts, str):
                        last_exit_dt = datetime.fromisoformat(last_exit_ts.replace('Z', '+00:00'))
                        hours_since_exit = (datetime.now(timezone.utc) - last_exit_dt).total_seconds() / 3600
                        if hours_since_exit > self._data_retention_hours and positionAmt <= 0:
                            candidate_data['_needs_cleanup'] = True
                except: pass
        candidate_data['last_calculated'] = datetime.now(timezone.utc).isoformat()
        candidate_data['_fields_fresh'] = True
        candidate_data['calculation_count'] = candidate_data.get('calculation_count', 0) + 1
        self._last_field_calculation[position_key] = now_ts
        return candidate_data

    async def calculate_trading_signals(self, position_key: str, candidate_data: Dict[str, Any],current_price: float, is_long: bool, data_manager: FastDataManager = None) -> Dict[str, Any]:
        symbol = position_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
        signals = {}
        gain_list = candidate_data.get('gain_list', [])
        if isinstance(gain_list, list) and len(gain_list) >= 3:
            recent_gains = gain_list[-3:]
            momentum = sum(recent_gains) / len(recent_gains)
            signals['momentum'] = momentum
            if len(gain_list) >= 4:
                prev_momentum = sum(gain_list[-4:-1]) / 3
                acceleration = momentum - prev_momentum
                signals['momentum_acceleration'] = acceleration
        if data_manager:
            metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_data_fresh = await data_manager.get_hot_state(symbol)
            if indicators:
                current_vol = safe_fetch_float(indicators.get('volatility_1h_%', indicators.get('0sentiment_strength', 0)), 0)
                avg_vol = safe_fetch_float(indicators.get('avg_volatility_1h_%', 50.0), 50.0)
                if avg_vol > 0:
                    vol_ratio = current_vol / avg_vol
                    signals['volatility_ratio'] = vol_ratio
                    signals['high_volatility_warning'] = vol_ratio > 1.5
        positionAmt = safe_fetch_float(candidate_data.get('positionAmt', 0.0), 0.0)
        avg_entry_price = safe_fetch_float(candidate_data.get('average_entry_price', 0.0), 0.0)
        current_gain = safe_fetch_float(candidate_data.get('current_gain_%', 0.0), 0.0)
        
        if positionAmt > 0 and avg_entry_price > 0:
            max_gain = safe_fetch_float(candidate_data.get('max_gain', 0.0), current_gain)
            drawdown = max_gain - current_gain
            signals['drawdown_from_max_%'] = drawdown
            risk_level = 1
            if drawdown > 2: risk_level = 5
            if drawdown > 5: risk_level = 8
            if drawdown > 10: risk_level = 10
            signals['risk_level'] = risk_level
        if candidate_data.get('status') == 'entry_candidate':
            last_reduction_price = safe_fetch_float(candidate_data.get('last_reduction_price', 0.0), 0.0)
            if last_reduction_price > 0 and current_price > 0:
                if is_long:
                    distance_to_exit_pct = ((current_price - last_reduction_price) / last_reduction_price) * 100
                else:
                    distance_to_exit_pct = ((last_reduction_price - current_price) / last_reduction_price) * 100
                
                signals['distance_to_last_exit_%'] = distance_to_exit_pct
                if is_long:
                    signal_strength = max(0, min(100, 50 + distance_to_exit_pct * 2))
                else:
                    signal_strength = max(0, min(100, 50 - distance_to_exit_pct * 2))
                
                signals['reentry_signal_strength'] = signal_strength
        
        # 5. COMPOSITE SIGNAL SCORE (0-100)
        composite_score = 50  # Neutral
        
        # Adjust based on momentum
        if 'momentum' in signals:
            composite_score += signals['momentum'] * 0.5
        
        # Adjust based on risk
        if 'risk_level' in signals:
            risk_adjustment = (10 - signals['risk_level']) * 2  # Lower risk = higher score
            composite_score += risk_adjustment
        
        # Adjust based on volatility
        if 'high_volatility_warning' in signals and signals['high_volatility_warning']:
            composite_score -= 10
        
        # Clamp to 0-100
        composite_score = max(0, min(100, composite_score))
        signals['composite_signal_score'] = composite_score
        
        # Action recommendation
        if composite_score >= 80:
            signals['action'] = 'STRONG_BUY' if is_long else 'STRONG_SELL'
        elif composite_score >= 60:
            signals['action'] = 'BUY' if is_long else 'SELL'
        elif composite_score >= 40:
            signals['action'] = 'HOLD'
        elif composite_score >= 20:
            signals['action'] = 'CONSIDER_REDUCE'
        else:
            signals['action'] = 'STRONG_REDUCE'
        candidate_data['trading_signals'] = signals
        candidate_data['signals_calculated'] = datetime.now(timezone.utc).isoformat()        
        return candidate_data


    async def realtime_monitoring_task(self, stop_event: asyncio.Event, all_accounts: list = None, trade_manager=None, data_manager: FastDataManager = None):
        """Real-time monitoring task that: 1. Updates all field calculations 2. Generates trading signals 3. Updates account summaries 4. Logs important events"""
        logger.info("[REALTIME_MONITOR] Starting real-time monitoring task")
        last_full_update = {account: 0 for account in (all_accounts or [])}
        last_summary_update = {account: 0 for account in (all_accounts or [])}
        while not stop_event.is_set():
            try:
                current_time = time.time()
                for account_key in (all_accounts or []):
                    try:
                        if current_time - last_full_update.get(account_key, 0) >= 60 and trade_manager._check_account_allowed(account_key):
                            await self.update_all_fields_for_account(account_key, trade_manager, data_manager)
                            last_full_update[account_key] = current_time
                        if current_time - last_full_update.get(account_key, 0) >= 30 and trade_manager._check_account_allowed(account_key):
                            await self.update_trading_signals_for_account(account_key, trade_manager, data_manager)
                        if current_time - last_summary_update.get(account_key, 0) >= 300 and trade_manager._check_account_allowed(account_key):
                            summary = await self.generate_account_summary(account_key, trade_manager, data_manager)
                            await self.log_summary_changes(account_key, summary)
                            last_summary_update[account_key] = current_time
                    except Exception as e:
                        logger.error(f"[REALTIME_MONITOR] Error for {account_key}: {e}")
                await asyncio.sleep(10)
            except asyncio.CancelledError: break
            except Exception as e:
                logger.error(f"[REALTIME_MONITOR] Task error: {e}")
                await asyncio.sleep(30)

    async def update_all_fields_for_account(self, account_key: str, trade_manager, data_manager: FastDataManager = None):
        async with self._exit_candidates_lock:
            for position_key, candidate in list(self.exit_candidates.items()):
                if not position_key.startswith(f"{account_key}:"): continue
                try:
                    updated = await self.calculate_candidate_fields(account_key, 'exit', position_key, candidate, trade_manager, data_manager)
                    self.exit_candidates[position_key] = updated
                    self._exit_candidates_dirty[account_key] = True
                except Exception as e:
                    logger.error(f"[FIELD_UPDATE] Error updating {position_key}: {e}")
        async with self._entry_candidates_lock:
            for position_key, candidate in list(self.entry_candidates.items()):
                if not position_key.startswith(f"{account_key}:"): continue
                positionAmt = safe_fetch_float(candidate.get('positionAmt', 0.0), 0.0)
                if positionAmt > 0:
                    try:
                        updated = await self.calculate_candidate_fields(account_key, 'entry', position_key, candidate, trade_manager, data_manager)
                        self.entry_candidates[position_key] = updated
                        self._entry_candidates_dirty[account_key] = True
                    except Exception as e:
                        logger.error(f"[FIELD_UPDATE] Error updating {position_key}: {e}")

    async def update_trading_signals_for_account(self, account_key: str, trade_manager, data_manager: FastDataManager = None):
        """Update trading signals for all candidates."""
        if hasattr(trade_manager, '_check_account_allowed'):
            if not trade_manager._check_account_allowed(account_key): return
        price_cache = {}
        async with self._exit_candidates_lock:
            for position_key, candidate in list(self.exit_candidates.items()):
                if not position_key.startswith(f"{account_key}:"): continue
                symbol = position_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
                is_long = position_key.endswith('_LONG')
                if symbol not in price_cache:
                    price = 0.0
                    if data_manager:
                        price, _ = data_manager.get_fresh_price_sync(symbol)
                    price_cache[symbol] = price
                current_price = price_cache[symbol]
                if current_price <= 0: continue
                try:
                    updated = await self.calculate_trading_signals(position_key, candidate, current_price, is_long, data_manager)
                    self.exit_candidates[position_key] = updated
                    self._exit_candidates_dirty[account_key] = True
                except Exception as e:
                    logger.error(f"[SIGNAL_UPDATE] Error updating signals for {position_key}: {e}")

    async def log_summary_changes(self, account_key: str, summary: Dict[str, Any]):
        """Log important changes in account summary."""
        if not hasattr(self, '_last_summaries'):
            self._last_summaries = {}
        last_summary = self._last_summaries.get(account_key)
        self._last_summaries[account_key] = summary
        if not last_summary: return
        significant_changes = []
        current_pnl = summary['performance']['total_pnl']
        last_pnl = last_summary['performance']['total_pnl']
        if abs(current_pnl - last_pnl) > 100:
            significant_changes.append(f"PnL change: ${last_pnl:.2f} → ${current_pnl:.2f}")
        current_winrate = summary['performance'].get('win_rate', 0)
        last_winrate = last_summary['performance'].get('win_rate', 0)
        if abs(current_winrate - last_winrate) > 10:
            significant_changes.append(f"Win rat e: {last_winrate:.1f}% → {current_winrate:.1f}%")
        current_health = summary['account_health_status']
        last_health = last_summary['account_health_status']
        if current_health != last_health:
            significant_changes.append(f"Health: {last_health} → {current_health}")
        if significant_changes:
            logger.info(f"[SUMMARY_CHANGE][{account_key}] " + ", ".join(significant_changes))

    async def generate_account_summary(self, account_key: str, trade_manager=None, data_manager: FastDataManager = None) -> Dict[str, Any]:
        """Generate comprehensive account summary for monitoring and dashboards."""
        if trade_manager and hasattr(trade_manager, '_check_account_allowed'):
            if not trade_manager._check_account_allowed(account_key):
                logger.info(f"[generate_account_summary] BLOCKED: Account '{account_key}' not in allowed accounts")
                return {}
        summary = {'account': account_key, 'timestamp': datetime.now(timezone.utc).isoformat(), 'position_counts': {}, 'performance': {}, 'risk_metrics': {}, 'trading_activity': {}}
        async with self._exit_candidates_lock:
            exit_count = len([k for k in self.exit_candidates.keys() if k.startswith(f"{account_key}:")])
        async with self._entry_candidates_lock:
            entry_count = len([k for k in self.entry_candidates.keys() if k.startswith(f"{account_key}:")])
        summary['position_counts']['active'] = exit_count
        summary['position_counts']['watching'] = entry_count
        total_realized_pnl = 0.0
        total_unrealized_pnl = 0.0
        total_positions_value = 0.0
        winning_positions = 0
        total_positions = 0
        async with self._exit_candidates_lock:
            for position_key, candidate in self.exit_candidates.items():
                if not position_key.startswith(f"{account_key}:"): continue
                total_positions += 1
                total_realized_pnl += safe_fetch_float(candidate.get('total_realized_pnl_$', 0), 0)
                total_unrealized_pnl += safe_fetch_float(candidate.get('unrealized_pnl_$', 0), 0)
                total_positions_value += safe_fetch_float(candidate.get('current_pos_value', 0), 0)
                current_gain = safe_fetch_float(candidate.get('current_gain_%', 0), 0)
                if current_gain > 0: winning_positions += 1
        summary['performance']['total_realized_pnl'] = total_realized_pnl
        summary['performance']['total_unrealized_pnl'] = total_unrealized_pnl
        summary['performance']['total_pnl'] = total_realized_pnl + total_unrealized_pnl
        summary['performance']['total_positions_value'] = total_positions_value
        if total_positions > 0:
            summary['performance']['win_rate'] = (winning_positions / total_positions) * 100
            summary['performance']['avg_position_gain'] = total_unrealized_pnl / total_positions_value * 100 if total_positions_value > 0 else 0
        if total_positions_value > 0:
            summary['risk_metrics']['exposure_ratio'] = total_positions_value / (total_realized_pnl + total_unrealized_pnl + 1000)
            summary['risk_metrics']['concentration_risk'] = min(100, total_positions * 10)
        all_trades = []
        cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        async with self._exit_candidates_lock:
            for candidate in self.exit_candidates.values():
                trade_log = candidate.get('trade_log', [])
                if isinstance(trade_log, list): all_trades.extend(trade_log)
        async with self._entry_candidates_lock:
            for candidate in self.entry_candidates.values():
                trade_log = candidate.get('trade_log', [])
                if isinstance(trade_log, list): all_trades.extend(trade_log)
        recent_trades = []
        for trade in all_trades:
            try:
                if isinstance(trade, dict) and 'ts' in trade:
                    trade_time = datetime.fromisoformat(trade['ts'].replace('Z', '+00:00'))
                    if trade_time >= cutoff_24h: recent_trades.append(trade)
            except: continue
        summary['trading_activity']['trades_24h'] = len(recent_trades)
        if recent_trades:
            daily_pnl = sum(safe_fetch_float(t.get('pnl_usd', 0), 0) for t in recent_trades)
            summary['trading_activity']['daily_pnl'] = daily_pnl
            daily_wins = sum(1 for t in recent_trades if safe_fetch_float(t.get('pnl_pct', 0), 0) > 0)
            summary['trading_activity']['daily_win_rate'] = (daily_wins / len(recent_trades)) * 100 if recent_trades else 0
        total_paper_pnl = 0.0
        total_paper_trades = 0
        all_candidates = list(self.exit_candidates.values()) + list(self.entry_candidates.values())
        #for candidate in all_candidates:
        #     if candidate.get('paper_qty', 0) > 0:
        #         total_paper_pnl += safe_fetch_float(candidate.get('paper_total_gain_$', 0), 0)
        #     trade_log = candidate.get('trade_log', [])
        #     if isinstance(trade_log, list):
        #         paper_trades = [t for t in trade_log if t.get('paper', False)]
        #         total_paper_trades += len(paper_trades)
        # summary['paper_trading'] = {'total_paper_pnl': total_paper_pnl, 'total_paper_trades': total_paper_trades, 'paper_trading_active': total_paper_trades > 0}
        health_score = 50
        if summary['performance']['total_pnl'] > 0: health_score += 10
        if summary['performance'].get('win_rate', 0) > 60: health_score += 15
        if summary['trading_activity']['trades_24h'] > 0: health_score += 10
        if summary['position_counts']['active'] > 10: health_score -= 10
        health_score = max(0, min(100, health_score))
        summary['account_health_score'] = health_score
        if health_score >= 80: summary['account_health_status'] = 'EXCELLENT'
        elif health_score >= 60: summary['account_health_status'] = 'GOOD'
        elif health_score >= 40: summary['account_health_status'] = 'FAIR'
        elif health_score >= 20: summary['account_health_status'] = 'POOR'
        else: summary['account_health_status'] = 'CRITICAL'
        return summary
        
    def _merge_history(self, target_dict: Dict[str, Any], source_dict: Dict[str, Any]) -> Dict[str, Any]:
            if not source_dict: return target_dict
            preserve_lists = [
                'gain_list', 'gain_dollar_list', 'trade_log',  'pos_values_list', 'exit_prices_list', 'entry_prices_list'  ]
            for key in preserve_lists:
                if key in source_dict and isinstance(source_dict[key], list):
                    if key not in target_dict: target_dict[key] = []
                    if not target_dict[key]:
                        target_dict[key] = source_dict[key][-200:] 
            preserve_stats = ['total_realized_pnl_$', 'total_trades_count', 'winning_trades', 
                'losing_trades', 'total_trades', 'consecutive_wins', 'consecutive_losses',
                'win_rate_%', 'buy_hold_gain_%', 'buy_hold_gain_$' ]
            for key in preserve_stats:
                if key in source_dict:
                    target_dict[key] = source_dict[key]
                    
            return target_dict
            
    def reorder_candidate_fields(self, candidate_data: Dict[str, Any], candidate_type: str = 'exit') -> Dict[str, Any]:
        if not candidate_data:
            return {}
        
        position_fields = [
            'positionAmt', 'mark_price', 'entry_price', 'exit_price',
            'average_entry_price', 'average_exit_price', 'average_pos_value', 
            'current_pos_value', 'total_entry_qty'
        ]
        status_fields = ['status', 'timestamp', 'last_calculated']

        entry_exit_detail_fields = [
            'first_entry_price', 'last_entry_price', 'last_entry_qty', 'last_entry_time',
            'last_reduction_price', 'last_reduction_amount', 'last_exit_time', 'last_exit_timestamp',
            'pos_values_list', 'exit_prices_list', 'dc_low_3m', 'dc_high_3m',
            'last_exit_reentry_ready', 'last_trade_gain'
        ]
        
        gain_fields = [
            'gain_list', 'gain_dollar_list', 'current_gain_%', 'max_gain',
            'total_gain_%', 'total_gain_$', 'buy_hold_gain_%', 'buy_hold_gain_$'
        ]
        
        pnl_fields = [
            'unrealized_pnl_%', 'unrealized_pnl_$',
            'total_realized_pnl_$', 'total_pnl_$'
        ]
        
        trade_stats_fields = [
            'total_trades_count', 'winning_trades', 'losing_trades', 'total_trades',
            'win_rate_%', 'consecutive_wins', 'consecutive_losses'
        ]
        
        # Paper fields (to be placed last)
        paper_prefix = 'paper_'
        
        # Create ordered dictionary
        ordered_data = {}
        
        # Add fields in order
        for field_group in [status_fields, position_fields, entry_exit_detail_fields, 
                          gain_fields, pnl_fields, trade_stats_fields]:
            for field in field_group:
                if field in candidate_data:
                    ordered_data[field] = candidate_data[field]
        
        # Add all other non-paper fields alphabetically
        other_fields = []
        for field in candidate_data:
            if (field not in ordered_data and 
                not field.startswith(paper_prefix) and 
                not field.startswith('_') and 
                field not in ['trade_log', 'reentry_attempts', 'last_reentry_check']):
                other_fields.append(field)
        for field in sorted(other_fields):
            ordered_data[field] = candidate_data[field]
        paper_fields = []
        for field in candidate_data:
            if field.startswith(paper_prefix):
                paper_fields.append(field)
        for field in sorted(paper_fields):
            ordered_data[field] = candidate_data[field]
        if 'trade_log' in candidate_data:
            ordered_data['trade_log'] = candidate_data['trade_log']
        reentry_fields = ['reentry_attempts', 'last_reentry_check']
        for field in reentry_fields:
            if field in candidate_data:
                ordered_data[field] = candidate_data[field]
        return ordered_data

    def _serialize_datetime_fields(self, data: Any) -> Any:
        """Recursively serialize datetime fields to ISO format strings."""
        if isinstance(data, dict):
            return {k: self._serialize_datetime_fields(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._serialize_datetime_fields(item) for item in data]
        elif isinstance(data, datetime):
            return data.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        else:
            return data

    async def calculate_detailed_metrics(self, account_key: str, position_key: str,   candidate_data: Dict[str, Any], trade_manager, data_manager: FastDataManager = None) -> Dict[str, Any]:
        symbol = position_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
        is_long = position_key.endswith('_LONG')
        candidate_type = 'exit' if candidate_data.get('status') == 'active' else 'entry'
        current_price = 0.0
        if data_manager:
            current_price, _ = data_manager.get_fresh_price_sync(symbol)
        if current_price <= 0:
            current_price = candidate_data.get('mark_price', 0.0)
        metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_data_fresh = await data_manager.get_hot_state(symbol)
        atr_1h = safe_fetch_float(indicators.get('atr_1h', 0.0), 0.0)
        atr_4h = safe_fetch_float(indicators.get('atr_4h', 0.0), 0.0)
        
        candidate_data['atr_1h'] = atr_1h
        candidate_data['atr_4h'] = atr_4h
        
        # Volatility percentage
        if current_price > 0:
            volatility_1h_pct = (atr_1h / current_price) * 100 if atr_1h > 0 else 0.0
            volatility_4h_pct = (atr_4h / current_price) * 100 if atr_4h > 0 else 0.0
            
            candidate_data['volatility_1h_%'] = volatility_1h_pct
            candidate_data['volatility_4h_%'] = volatility_4h_pct

        # 2. TREND STRENGTH METRICS
        # Calculate from recent gain list
        gain_list = candidate_data.get('gain_list', [])
        if isinstance(gain_list, list) and len(gain_list) >= 10:
            recent_gains = gain_list[-10:]  # Last 10 updates
            
            # Trend direction (positive = uptrend, negative = downtrend)
            if len(recent_gains) >= 2:
                trend_direction = 1 if recent_gains[-1] > recent_gains[0] else -1
                candidate_data['trend_direction'] = trend_direction
            
            # Trend strength (standard deviation of recent gains)
            if len(recent_gains) >= 3:
                try:
                    gain_series = pd.Series(recent_gains)
                    trend_strength = gain_series.std()
                    candidate_data['trend_strength'] = float(trend_strength)
                except:
                    candidate_data['trend_strength'] = 0.0
        
        # 3. RISK METRICS
        positionAmt = safe_fetch_float(candidate_data.get('positionAmt', 0.0), 0.0)
        avg_entry_price = safe_fetch_float(candidate_data.get('average_entry_price', 0.0), 0.0)
        
        if positionAmt > 0 and avg_entry_price > 0 and current_price > 0:
            # Position risk (distance to break-even)
            if is_long:
                risk_to_breakeven_pct = ((avg_entry_price - current_price) / avg_entry_price) * 100
            else:
                risk_to_breakeven_pct = ((current_price - avg_entry_price) / avg_entry_price) * 100
            
            candidate_data['risk_to_breakeven_%'] = risk_to_breakeven_pct
            
            # Risk-reward ratio (simplified)
            max_gain = safe_fetch_float(candidate_data.get('max_gain', 0.0), 0.0)
            current_gain = safe_fetch_float(candidate_data.get('current_gain_%', 0.0), 0.0)
            
            if abs(risk_to_breakeven_pct) > 0:
                risk_reward_ratio = abs(current_gain / risk_to_breakeven_pct)
                candidate_data['risk_reward_ratio'] = risk_reward_ratio
        
        # 4. PERFORMANCE METRICS (last 24 hours)
        trade_log = candidate_data.get('trade_log', [])
        if isinstance(trade_log, list):
            # Filter trades from last 24 hours
            cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
            recent_trades_24h = []
            
            for trade in trade_log:
                try:
                    if isinstance(trade, dict) and 'ts' in trade:
                        trade_time = datetime.fromisoformat(
                            trade['ts'].replace('Z', '+00:00')
                        )
                        if trade_time >= cutoff_24h:
                            recent_trades_24h.append(trade)
                except:
                    continue
            
            if recent_trades_24h:
                # Calculate daily PnL
                daily_pnl = sum(safe_fetch_float(t.get('pnl_usd', 0), 0) for t in recent_trades_24h)
                candidate_data['daily_pnl_$'] = daily_pnl
                
                # Daily win rate
                daily_wins = sum(1 for t in recent_trades_24h if safe_fetch_float(t.get('pnl_pct', 0), 0) > 0)
                daily_total = len(recent_trades_24h)
                
                if daily_total > 0:
                    daily_win_rate = (daily_wins / daily_total) * 100
                    candidate_data['daily_win_rate_%'] = daily_win_rate
        
        # 5. TIME-BASED METRICS
        opened_at = candidate_data.get('opened_at')
        if opened_at:
            try:
                if isinstance(opened_at, str):
                    opened_dt = datetime.fromisoformat(opened_at.replace('Z', '+00:00'))
                elif isinstance(opened_at, datetime):
                    opened_dt = opened_at
                else:
                    opened_dt = None
                
                if opened_dt:
                    hours_open = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600
                    candidate_data['hours_open'] = hours_open
                    
                    # Age-based performance
                    if hours_open > 0:
                        total_pnl = safe_fetch_float(candidate_data.get('total_pnl_$', 0), 0)
                        pnl_per_hour = total_pnl / hours_open
                        candidate_data['pnl_per_hour_$'] = pnl_per_hour
            except:
                pass
        
        # 6. RE-ENTRY SPECIFIC METRICS
        if candidate_type == 'entry':
            last_exit_ts = candidate_data.get('last_exit_timestamp')
            if last_exit_ts:
                try:
                    if isinstance(last_exit_ts, str):
                        last_exit_dt = datetime.fromisoformat(last_exit_ts.replace('Z', '+00:00'))
                        hours_since_exit = (datetime.now(timezone.utc) - last_exit_dt).total_seconds() / 3600
                        candidate_data['hours_since_exit'] = hours_since_exit
                        
                        # Re-entry probability (based on time since exit)
                        # More time = higher probability we should consider re-entry
                        if hours_since_exit < 1:
                            reentry_probability = 0.1
                        elif hours_since_exit < 6:
                            reentry_probability = 0.3
                        elif hours_since_exit < 12:
                            reentry_probability = 0.5
                        elif hours_since_exit < 24:
                            reentry_probability = 0.7
                        else:
                            reentry_probability = 0.9
                        
                        candidate_data['reentry_probability'] = reentry_probability
                except:
                    pass
        candidate_data['detailed_metrics_calculated'] = datetime.now(timezone.utc).isoformat()        
        return candidate_data


    async def is_processing(self, key: str) -> bool:
        async with self._processing_lock:
            ts = self._processing_orders.get(key)
            if ts:
                if (time.time() - ts) < 45.0: return True
                else: del self._processing_orders[key]
            return False
            
    async def set_processing(self, key: str):
        async with self._processing_lock: self._processing_orders[key] = time.time()

    async def clear_processing(self, key: str):
        async with self._processing_lock:
            if key in self._processing_orders: del self._processing_orders[key]

    async def is_trade_cooldown_active(self, position_key: str) -> bool:
        async with self._trade_cooldown_lock:
            cooldown_ts = self._trade_cooldown.get(position_key)
            if cooldown_ts:
                if (time.time() - cooldown_ts) < self.TRADE_COOLDOWN_SECONDS: return True
                else: del self._trade_cooldown[position_key]
            return False
            
    async def set_trade_cooldown(self, position_key: str):
        async with self._trade_cooldown_lock: self._trade_cooldown[position_key] = time.time()

    def _apply_entry_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        defaults = self._entry_template()
        for k, v in defaults.items():
            data.setdefault(k, v if not isinstance(v, datetime) else datetime.now(timezone.utc))
        if data.get('status') not in ('active', 'entry_candidate'): data['status'] = 'entry_candidate'
        if data.get('timestamp') and isinstance(data['timestamp'], str):
            try: data['timestamp'] = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
            except: data['timestamp'] = datetime.now(timezone.utc)
        return data
        
    def _entry_template(self) -> Dict[str, Any]:
        """
        Merged Template: Contains both legacy list fields and new analytical fields.
        """
        now = datetime.now(timezone.utc)
        return {
            'status': 'entry_candidate',
            'timestamp': now,
            
            # -- Legacy / Basic Fields --
            'entry_price': 0.0,
            'exit_price': 0.0,
            'average_entry_price': 0.0,
            'average_exit_price': 0.0,
            'average_pos_value': 0.0,
            'current_pos_value': 0.0,
            'total_entry_qty': 0.0,
            'pos_values_list': [],
            'exit_prices_list': [],
            'positionAmt': 0.0,
            'first_entry_price': 0.0,

            # -- Performance Tracking (Legacy Lists) --
            'gain_list': [],
            'gain_dollar_list': [],
            'total_gain_%': 0.0,
            'total_gain_$': 0.0,
            'buy_hold_gain_%': 0.0,
            'buy_hold_gain_$': 0.0,
            
            # -- Performance Tracking (New Stats) --
            'total_realized_pnl_$': 0.0, # Equivalent to total_gain_$ but explicit
            'total_trades_count': 0,     # Equivalent to total_trades
            'winning_trades': 0,
            'losing_trades': 0,
            'total_trades': 0,
            'win_rate_%': 0.0,
            'consecutive_wins': 0,
            'consecutive_losses': 0,
            'last_reduction_price': 0.0,
            'last_reduction_amount': 0.0,
            'last_trade_gain': 0.0,
            'last_exit_time': None,
            'last_exit_reentry_ready': False,
            'last_exit_timestamp': None,
            'trade_log': [] 
        }

    def _exit_template(self, entry_price: float = 0.0, qty: float = 0.0) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        tpl = self._entry_template()
        tpl.update({
            'status': 'entry_candidate',
            'timestamp': now,
            'entry_price': 0.0,
            'exit_price': 0.0,
            'average_entry_price': 0.0,
            'average_exit_price': 0.0,
            'average_pos_value': 0.0,
            'current_pos_value': 0.0,
            'total_entry_qty': 0.0,
            'pos_values_list': [],
            'exit_prices_list': [],
            'positionAmt': 0.0,
            'first_entry_price': 0.0,
            'gain_list': [],
            'gain_dollar_list': [],
            'total_gain_%': 0.0,
            'total_gain_$': 0.0,
            'buy_hold_gain_%': 0.0,
            'buy_hold_gain_$': 0.0,
            'total_realized_pnl_$': 0.0, # Equivalent to total_gain_$ but explicit
            'total_trades_count': 0,     # Equivalent to total_trades
            'winning_trades': 0,
            'losing_trades': 0,
            'total_trades': 0,
            'win_rate_%': 0.0,
            'consecutive_wins': 0,
            'consecutive_losses': 0,
            'last_reduction_price': 0.0,
            'last_reduction_amount': 0.0,
            'last_trade_gain': 0.0,
            'last_exit_time': None,
            'last_exit_reentry_ready': False,
            'last_exit_timestamp': None,
            'trade_log': [] 
        })
        return tpl

    def _populate_from_position(self, entry_data: Dict[str, Any], position) -> Dict[str, Any]:
        if not position: return entry_data
        
        p_amt = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0), 0.0))
        # API Entry Price (often just the last trade price, not avg)
        p_entry = safe_fetch_float(getattr(position, 'entry_price', 0.0), 0.0)
        p_mark = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
        
        entry_data['positionAmt'] = p_amt
        entry_data['mark_price'] = p_mark
        
        # --- CRITICAL FIX: Preserve Average Entry Price ---
        # If API gives us a valid entry, use it.
        # But if API gives 0 (sometimes happens on partial updates), KEEP EXISTING.
        if p_entry > 0:
            entry_data['entry_price'] = p_entry
            # If average is 0, initialize it. If it exists, KEEP IT (don't overwrite with last entry)
            if safe_fetch_float(entry_data.get('average_entry_price', 0.0)) == 0.0:
                entry_data['average_entry_price'] = p_entry
                entry_data['first_entry_price'] = p_entry
        else:
            # If API entry is 0, ensure we don't wipe existing data
            if entry_data.get('average_entry_price') is None:
                entry_data['average_entry_price'] = 0.0

        entry_data['total_entry_qty'] = max(entry_data.get('total_entry_qty', 0.0), p_amt)

        # --- 2. VALUE & PNL ---
        if p_mark > 0:
            entry_data['current_pos_value'] = p_amt * p_mark
        
        # Use stored average for value calc if possible
        avg_price = safe_fetch_float(entry_data.get('average_entry_price', 0.0))
        if avg_price > 0:
            entry_data['average_pos_value'] = p_amt * avg_price
            
        # Sync Gain/PnL from Position Object (Source of Truth)
        if hasattr(position, 'unrealized_pnl'):
            entry_data['unrealized_pnl_$'] = safe_fetch_float(position.unrealized_pnl, 0.0)
        
        if hasattr(position, 'gain'):
            g = safe_fetch_float(position.gain, 0.0)
            entry_data['current_gain_%'] = g
            entry_data['unrealized_pnl_%'] = g
            
        if hasattr(position, 'realized_pnl'):
            # This is "Realized PnL of the CURRENT session", not total history.
            # We add this to our historical total later.
            pass 

        if hasattr(position, 'max_gain'):
            entry_data['max_gain'] = max(entry_data.get('max_gain', -999.0), safe_fetch_float(position.max_gain, 0.0))

        # --- 3. TIMESTAMPS ---
        def _fmt_time(t):
            if not t: return None
            if isinstance(t, str): return t
            if isinstance(t, datetime): 
                if t.tzinfo is None: t = t.replace(tzinfo=timezone.utc)
                return t.isoformat()
            return None

        if hasattr(position, 'opened_at') and position.opened_at:
            entry_data['opened_at'] = _fmt_time(position.opened_at)
            
        if hasattr(position, 'last_updated') and position.last_updated:
            entry_data['last_updated'] = _fmt_time(position.last_updated)

        # --- 4. EXITS / RE-ENTRY CONTEXT ---
        if hasattr(position, 'last_reduction_price') and safe_fetch_float(getattr(position, 'last_reduction_price', 0)) > 0:
            entry_data['last_reduction_price'] = safe_fetch_float(position.last_reduction_price)
            
        if hasattr(position, 'last_reduction_amount') and safe_fetch_float(getattr(position, 'last_reduction_amount', 0)) > 0:
            entry_data['last_reduction_amount'] = safe_fetch_float(position.last_reduction_amount)
            
        if hasattr(position, 'last_reduction_time') and position.last_reduction_time:
            ts_str = _fmt_time(position.last_reduction_time)
            entry_data['last_exit_timestamp'] = ts_str
            entry_data['last_exit_reentry_ready'] = True

        return entry_data

    async def sync_from_ws_event(self, account_key: str, position_key: str, positionAmt: float, entry_price: float) -> None:
        if hasattr(self, 'trade_manager') and self.trade_manager and hasattr(self.trade_manager, '_check_account_allowed'):
            if not self.trade_manager._check_account_allowed(account_key): return
            
        try:
            ws_data = {
                'positionAmt': positionAmt, 
                'entry_price': entry_price, 
                'timestamp': datetime.now(timezone.utc), 
                'account_key': account_key
            }
            
            if not hasattr(self, '_ws_position_buffer'): self._ws_position_buffer = {}
            self._ws_position_buffer[position_key] = ws_data
            
            # 1. Get Context
            service = self.positions_service
            data_manager = self.data_manager
            # Get the FULL position object from service
            position = service.positions_by_account.get(account_key, {}).get(position_key)
            
            ak, symbol, position_side = parse_position_key(position_key)
            
            current_price, ts = data_manager.get_fresh_price_sync(symbol)
            if not current_price or current_price == 0: 
                current_price, ts = await data_manager.get_fresh_price(symbol)
            if not current_price or current_price == 0:
                current_price, ts = await get_current_price(symbol)            
                
            pos_min_qty = max(2 * config.MIN_POSITION_SIZE / current_price, self.trade_manager.min_qty.get(symbol, 0.0))

            if positionAmt > pos_min_qty:
                # 2. Retrieve History
                history_data = {}
                
                # Check Exit first
                if position_key in self.exit_candidates:
                    history_data = self.exit_candidates[position_key]
                else:
                    # Check Entry (CRITICAL for history preservation)
                    async with self._entry_candidates_lock:
                        if position_key in self.entry_candidates:
                            history_data = self.entry_candidates.pop(position_key)
                            self._entry_candidates_dirty[account_key] = True
                active_data = self._smart_merge(history_data, template_type='exit')
                active_data = self._populate_from_position(active_data, position)
                             
                active_data['positionAmt'] = positionAmt
                active_data['entry_price'] = entry_price
                start_size_usd = safe_fetch_float(getattr(config, 'MIN_POSITION_SIZE', 45.0), 45.0)
                current_val = positionAmt * active_data.get('mark_price', 0.0)
                if current_val >= start_size_usd:
                    active_data['status'] = 'active'
                active_data['timestamp'] = datetime.now(timezone.utc)
                active_data['last_updated'] = datetime.now(timezone.utc).isoformat()
                is_hedge = False
                async with self._hedges_lock:
                    for hedge in self.active_hedges:
                        if hedge.get('position_key') == position_key and hedge.get('account') == account_key:
                            is_hedge = True
                            break
                if is_hedge: active_data['is_hedge'] = True

                # 6. Save
                async with self._exit_candidates_lock:
                    self.exit_candidates[position_key] = active_data
                    self._exit_candidates_dirty[account_key] = True
                    
            else:
                # Position Closed Logic
                async with self._entry_candidates_lock:
                    if position_key not in self.entry_candidates:
                        history_data = {}
                        async with self._exit_candidates_lock:
                            if position_key in self.exit_candidates:
                                history_data = self.exit_candidates.pop(position_key)
                                self._exit_candidates_dirty[account_key] = True
                        
                        entry_data = self._smart_merge(history_data, template_type='entry')
                        entry_data['last_exit_timestamp'] = datetime.now(timezone.utc).isoformat()
                        
                        self.entry_candidates[position_key] = entry_data
                        self._entry_candidates_dirty[account_key] = True
                    else:
                        if not self.entry_candidates[position_key].get('last_exit_timestamp'):
                            self.entry_candidates[position_key]['last_exit_timestamp'] = datetime.now(timezone.utc).isoformat()
                            self._entry_candidates_dirty[account_key] = True
                            
                    async with self._exit_candidates_lock:
                        if position_key in self.exit_candidates:
                            del self.exit_candidates[position_key]
                            self._exit_candidates_dirty[account_key] = True
                            
        except Exception as e: 
            logger.error(f"[sync_from_ws_event] {account_key}:{position_key}: Error: {e}", exc_info=True)


    async def transition_to_exit(self, account_key: str, position_key: str, current_price: float, qty: float, status: str = "active", is_hedge: bool = False, hedge_for: str = None) -> None:
        data = None
        
        # 1. Try to find existing data (Priority: Active -> Entry)
        async with self._exit_candidates_lock:
            if position_key in self.exit_candidates:
                data = self.exit_candidates[position_key]
        
        # Check Entry (Promotion) - POP it to avoid duplication
        if not data:
            async with self._entry_candidates_lock:
                if position_key in self.entry_candidates:
                    data = self.entry_candidates.pop(position_key)
                    self._entry_candidates_dirty[account_key] = True

        # 2. Safe Merge using Smart Merge
        new_data = self._smart_merge(data, template_type='exit')
        
        # 3. Update CORE Position Fields
        prev_qty = safe_fetch_float(new_data.get('positionAmt', 0.0))
        prev_entry = safe_fetch_float(new_data.get('average_entry_price', current_price))
        
        # Handle Augment (Weighted Average Entry)
        if prev_qty > 0 and qty > prev_qty:
            added_qty = qty - prev_qty
            if added_qty > 0:
                avg_price = ((prev_qty * prev_entry) + (added_qty * current_price)) / qty
                new_data['average_entry_price'] = avg_price
                
                # Update Max Gain for new average
                is_long = position_key.endswith('_LONG')
                if avg_price > 0:
                    if is_long: new_gain = ((current_price - avg_price) / avg_price) * 100.0
                    else: new_gain = ((avg_price - current_price) / avg_price) * 100.0
                    new_data['max_gain'] = max(new_data.get('max_gain', -999), new_gain)
                    
        elif prev_qty == 0:
             # Fresh Open
             new_data['average_entry_price'] = current_price
             new_data['first_entry_price'] = current_price
             new_data['opened_at'] = datetime.now(timezone.utc).isoformat()

        new_data['status'] = status
        new_data['positionAmt'] = qty
        new_data['total_entry_qty'] = max(new_data.get('total_entry_qty', 0), qty)
        new_data['mark_price'] = current_price
        new_data['last_updated'] = datetime.now(timezone.utc).isoformat()
        
        if is_hedge:
            new_data['is_hedge'] = True
            new_data['hedge_for'] = hedge_for
            
        async with self._exit_candidates_lock:
            self.exit_candidates[position_key] = new_data
            self._exit_candidates_dirty[account_key] = True
            
        await self.save_tracker(account_key, force=False)

    async def transition_to_entry(self, account_key: str, position_key: str, current_price: float, qty: float, is_long: bool, is_paper: bool = False, from_websocket: bool = False, status:str=None) -> None:
        data = None
        async with self._exit_candidates_lock:
            if position_key in self.exit_candidates:
                data = self.exit_candidates.pop(position_key)
                self._exit_candidates_dirty[account_key] = True
        
        if not data:
            async with self._entry_candidates_lock:
                data = self.entry_candidates.get(position_key)
                
        if not data:
            # Fallback: create fresh if absolutely nothing exists (unlikely for a closure)
            data = self._entry_template()

        # 2. CALCULATE PNL (Before resetting anything)
        # Try Average first, then Entry, then First
        entry_price = safe_fetch_float(data.get('average_entry_price', 0.0))
        if entry_price <= 0: entry_price = safe_fetch_float(data.get('entry_price', 0.0))
        if entry_price <= 0: entry_price = safe_fetch_float(data.get('first_entry_price', 0.0))

        pnl_usd = 0.0
        gain = 0.0
        
        # Use passed quantity (amount closed)
        close_qty = qty
        if close_qty <= 0:
            close_qty = safe_fetch_float(data.get('positionAmt', 0.0))

        if entry_price > 0 and current_price > 0 and close_qty > 0:
            if is_long:
                gain = ((current_price - entry_price) / entry_price) * 100.0
                pnl_usd = (current_price - entry_price) * close_qty
            else:
                gain = ((entry_price - current_price) / entry_price) * 100.0
                pnl_usd = (entry_price - current_price) * close_qty
        else:
            # DEBUG LOG only if PnL is 0 but we expected a trade
            if close_qty > 0:
                logger.warning(f"[PNL_FAIL] {position_key}: Entry={entry_price}, Curr={current_price}, Qty={close_qty}")

        # 3. UPDATE CUMULATIVE STATS (History Preservation)
        current_total_realized = safe_fetch_float(data.get('total_realized_pnl_$', 0))
        data['total_realized_pnl_$'] = current_total_realized + pnl_usd
        
        data['total_trades'] = int(data.get('total_trades', 0)) + 1
        data['total_trades_count'] = data['total_trades'] # Sync legacy field
        
        if pnl_usd > 0:
            data['winning_trades'] = int(data.get('winning_trades', 0)) + 1
            data['consecutive_wins'] = int(data.get('consecutive_wins', 0)) + 1
            data['consecutive_losses'] = 0
        elif pnl_usd < 0:
            data['losing_trades'] = int(data.get('losing_trades', 0)) + 1
            data['consecutive_losses'] = int(data.get('consecutive_losses', 0)) + 1
            data['consecutive_wins'] = 0
            
        wins = data.get('winning_trades', 0)
        total = data.get('total_trades', 0)
        if total > 0: data['win_rate_%'] = (wins / total) * 100.0

        # 4. LOG TRADE
        trade_record = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'action': 'CLOSE',
            'pnl_usd': round(pnl_usd, 4),
            'pnl_pct': round(gain, 4),
            'price': current_price,
            'qty': close_qty,
            'entry': entry_price, # Store entry used for debug
            'reason': 'Position closed'
        }
        data.setdefault('trade_log', []).append(trade_record)
        data['trade_log'] = data['trade_log'][-200:]

        # 5. SET RE-ENTRY CONTEXT (Preserve levels)
        data['last_reduction_price'] = current_price
        data['last_reduction_amount'] = close_qty
        data['last_exit_timestamp'] = datetime.now(timezone.utc).isoformat()
        data['last_exit_reentry_ready'] = True
        
        # 6. RESET ACTIVE FIELDS (But keep history fields intact)
        # We manually reset specific fields instead of using a template merge
        # to guarantee we don't accidentally wipe 'total_realized_pnl_$' etc.
        current_amt = safe_fetch_float(data.get('positionAmt', 0.0))
        remaining_amt = max(0.0, current_amt - close_qty)
        
        if remaining_amt > 0.0001:
            data['status'] = 'active'
            data['positionAmt'] = remaining_amt
            # Update unrealized based on remaining
            data['current_pos_value'] = remaining_amt * current_price
            if entry_price > 0:
                u_gain = ((current_price - entry_price) / entry_price) * 100.0 if is_long else ((entry_price - current_price) / entry_price) * 100.0
                data['unrealized_pnl_%'] = u_gain
                data['unrealized_pnl_$'] = (current_price - entry_price) * remaining_amt if is_long else (entry_price - current_price) * remaining_amt
            
            # Put back in exit candidates
            async with self._exit_candidates_lock:
                self.exit_candidates[position_key] = data
                self._exit_candidates_dirty[account_key] = True
            logger.info(f"[TRACKER] {position_key} Partially REDUCED. Remaining: {remaining_amt:.6f}")
        else:
            data['status'] = status if status else 'entry_candidate'
            data['positionAmt'] = 0.0
            data['unrealized_pnl_$'] = 0.0
            data['unrealized_pnl_%'] = 0.0
            data['current_gain_%'] = 0.0
            data['current_pos_value'] = 0.0
            
            # Put in entry candidates
            async with self._entry_candidates_lock:
                self.entry_candidates[position_key] = data
                self._entry_candidates_dirty[account_key] = True
            logger.info(f"[TRACKER] {position_key} Fully CLOSED. History Saved.")

        # --- REDUCED POSITIONS TRACKING ---
        now_dt = datetime.now(timezone.utc)
        if self.positions_service:
            self.positions_service.reduced_positions[position_key] = now_dt
            asyncio.create_task(self.positions_service.save_reduced_positions(account_key, min_interval=0))
        
        if self.trade_manager:
            self.trade_manager.reentry_data[position_key] = {
                "reentry_level": current_price,
                "reentry_amount": close_qty,
                "timestamp": now_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "reason": f"TRACKER_REDUCTION_{status}"
            }

        # 7. SAVE
        await self.save_tracker(account_key, force=True)


    async def get_entry_candidate(self, position_key: str) -> Optional[Dict[str, Any]]:
        async with self._entry_candidates_lock:
            data = self.entry_candidates.get(position_key)
            return dict(data) if isinstance(data, dict) else None

    async def get_exit_candidate(self, position_key: str) -> Optional[Dict[str, Any]]:
        async with self._exit_candidates_lock:
            data = self.exit_candidates.get(position_key)
            return dict(data) if isinstance(data, dict) else None

    async def mark_reentry_consumed(self, account_key: str, position_key: str) -> None:
        async with self._entry_candidates_lock:
            data = self.entry_candidates.get(position_key)
            if data:
                data['last_reduction_amount'] = 0.0
                data['last_exit_reentry_ready'] = False
                self._entry_candidates_dirty[account_key] = True

    def get_tracker_file(self, account_key: str) -> Path:
        """Get the tracker file path for an account"""
        return self.base_path / account_key / "tracker.json"

    async def periodic_field_refresh(self, stop_event: asyncio.Event, all_accounts: list = None):
        """Background task to refresh field calculations periodically."""
        logger.info("[FIELD_REFRESH] Starting periodic field refresh task")
        while not stop_event.is_set():
            try:
                for account_key in (all_accounts or []):
                    try:
                        async with self._exit_candidates_lock:
                            for position_key in list(self.exit_candidates.keys()):
                                if position_key.startswith(f"{account_key}:"):
                                    self.exit_candidates[position_key]['_fields_fresh'] = False
                                    self._exit_candidates_dirty[account_key] = True
                        async with self._entry_candidates_lock:
                            for position_key in list(self.entry_candidates.keys()):
                                if position_key.startswith(f"{account_key}:"):
                                    self.entry_candidates[position_key]['_fields_fresh'] = False
                                    self._entry_candidates_dirty[account_key] = True
                    except Exception as e:
                        logger.error(f"[FIELD_REFRESH] Error refreshing {account_key}: {e}")
                await asyncio.sleep(self._calculation_interval)
            except asyncio.CancelledError: break
            except Exception as e:
                logger.error(f"[FIELD_REFRESH] Task error: {e}")
                await asyncio.sleep(10)

    async def cleanup_stale_temp_files(self):
        """
        Aggressively cleans up temp files to prevent inode exhaustion.
        Targets: *.tmp.*, .atom, and tracker* (excluding the main tracker.json).
        Threshold: 15 minutes.
        """
        try:
            # Run in a separate thread to avoid blocking the event loop with I/O
            await asyncio.to_thread(self._blocking_cleanup)
        except Exception as e:
            logger.debug(f"Error in async cleanup dispatch: {e}")

    def _blocking_cleanup(self):
        # 15 minutes = 900 seconds
        AGE_THRESHOLD = 900 
        now = time.time()
        deleted_count = 0
        
        # Iterate through all known accounts
        allowed_accounts = list(self._last_tracker_save_time.keys())
        # Also check directories physically present if we can
        try:
            physical_dirs = [d.name for d in self.base_path.iterdir() if d.is_dir()]
            for d in physical_dirs:
                if d not in allowed_accounts:
                    allowed_accounts.append(d)
        except: pass

        for account_key in set(allowed_accounts):
            account_dir = self.base_path / account_key
            if not account_dir.exists(): continue

            try:
                # Use os.scandir for performance (faster than pathlib on large directories)
                with os.scandir(account_dir) as entries:
                    for entry in entries:
                        if not entry.is_file(): continue
                        
                        name = entry.name
                        
                        # --- SAFETY CHECK ---
                        # NEVER delete the active tracker or position files
                        if name in ['tracker.json', 'long_positions.json', 'short_positions.json']:
                            continue
                        
                        # --- MATCH GARBAGE PATTERNS ---
                        # 1. Standard Temp files (*.tmp.*)
                        # 2. Atomic writes (.atom)
                        # 3. Tracker backups/temps (tracker* but not tracker.json)
                        is_garbage = (
                            '.tmp.' in name or 
                            name.endswith('.tmp') or
                            name.endswith('.atom') or
                            (name.startswith('tracker') and name != 'tracker.json')
                        )
                        
                        if is_garbage:
                            try:
                                stat = entry.stat()
                                if (now - stat.st_mtime) > AGE_THRESHOLD:
                                    os.unlink(entry.path)
                                    deleted_count += 1
                            except FileNotFoundError:
                                pass # Already gone
                            except Exception:
                                pass # Permission error etc.
                                
            except Exception as e:
                logger.debug(f"[CLEANUP] Error scanning {account_key}: {e}")

        if deleted_count > 0:
            logger.info(f"🧹 [SYSTEM_CLEANUP] Removed {deleted_count} stale temp files (>15m old).")

    async def revert_optimistic_exit(self, account_key: str, position_key: str):
        async with self._exit_candidates_lock:
            if position_key in self.exit_candidates:
                data = self.exit_candidates.pop(position_key)
                self._exit_candidates_dirty[account_key] = True
                async with self._entry_candidates_lock:
                    data['status'] = 'entry_candidate'
                    data['total_entry_qty'] = 0.0 
                    data['positionAmt'] = 0.0
                    self.entry_candidates[position_key] = data
                    self._entry_candidates_dirty[account_key] = True
                logger.info(f"[TRACKER] Reverted {position_key} from Exit -> Entry due to failed OPEN")
                await self.save_tracker(account_key, force=False)

    async def partial_reduce_exit(self, account_key: str, position_key: str, current_price: float, reduce_qty: float, is_long: bool) -> None:
        data = None
        source_list = None
        async with self._exit_candidates_lock:
            if position_key in self.exit_candidates:
                data = self.exit_candidates[position_key]
                source_list = 'exit'
        if not data:
            logger.error(f"[TRACKER] {position_key}: Cannot reduce phantom position.")
            return
        current_qty = safe_fetch_float(data.get('positionAmt', 0.0))
        new_qty = max(0.0, current_qty - reduce_qty)
        avg_entry_price = safe_fetch_float(data.get('average_entry_price', current_price))
        if avg_entry_price <= 0: avg_entry_price = safe_fetch_float(data.get('entry_price', current_price))
        pnl_usd = 0.0
        gain = 0.0
        if avg_entry_price > 0:
            if is_long:
                gain = ((current_price - avg_entry_price) / avg_entry_price) * 100.0
                pnl_usd = (current_price - avg_entry_price) * reduce_qty
            else:
                gain = ((avg_entry_price - current_price) / avg_entry_price) * 100.0
                pnl_usd = (avg_entry_price - current_price) * reduce_qty
        current_realized = safe_fetch_float(data.get('total_realized_pnl_$', 0.0))
        data['total_realized_pnl_$'] = current_realized + pnl_usd
        trade_record = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'action': 'SEMI-STOP',
            'pnl_usd': round(pnl_usd, 4),
            'pnl_pct': round(gain, 4),
            'price': current_price,
            'qty': reduce_qty,
            'remaining_qty': new_qty,
            'reason': 'Partial Reduction'  }
        if 'trade_log' not in data: data['trade_log'] = []
        data['trade_log'].append(trade_record)
        data['trade_log'] = data['trade_log'][-200:]
        data['positionAmt'] = new_qty
        data['total_entry_qty'] = new_qty
        data['timestamp'] = datetime.now(timezone.utc)
        data['last_updated'] = datetime.now(timezone.utc).isoformat()
        pilot_size_usd = safe_fetch_float(getattr(self.trade_manager.config, 'START_POSITION_SIZE', 45.0), 45.0) * 0.25
        remaining_value = new_qty * current_price
        is_now_pilot = remaining_value <= (pilot_size_usd * 1.1)
        if is_now_pilot:
            data['status'] = 'entry_candidate'
            async with self._entry_candidates_lock:
                self.entry_candidates[position_key] = data
                self._entry_candidates_dirty[account_key] = True
            async with self._exit_candidates_lock:
                if position_key in self.exit_candidates:
                    del self.exit_candidates[position_key]
                    self._exit_candidates_dirty[account_key] = True
            logger.info(f"[TRACKER] {position_key}: Demoted to Entry Candidate (Value ${remaining_value:.2f} < Pilot). PnL: ${pnl_usd:.2f}")
        else:
            data['status'] = 'active'
            async with self._exit_candidates_lock:
                self.exit_candidates[position_key] = data
                self._exit_candidates_dirty[account_key] = True
            logger.info(f"[TRACKER] {position_key}: Reduced but Active (Value ${remaining_value:.2f}). PnL: ${pnl_usd:.2f}")
        await self.save_tracker(account_key, force=False)

    def _is_duplicate_event(self, data: Dict[str, Any], action: str, price: float, qty: float) -> bool:
        """Check if this trade event was already recorded recently"""
        logs = data.get('trade_log', [])
        if not logs: return False
            
        last_log = logs[-1]
        if last_log.get('action') != action: return False
        
        last_price = safe_fetch_float(last_log.get('price'), 0.0)
        last_qty = safe_fetch_float(last_log.get('qty'), 0.0)
        
        # Match if price is within 0.01% and qty is within 0.01%
        price_match = abs(last_price - price) < (price * 0.0001)
        qty_match = abs(last_qty - qty) < (qty * 0.0001)
        
        # Check time (within 30 seconds)
        last_ts_str = last_log.get('ts')
        is_recent = False
        if last_ts_str:
            try:
                last_dt = isoparse(last_ts_str).replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                is_recent = (now - last_dt).total_seconds() < 30.0
            except: pass
        return price_match and qty_match and is_recent

async def load_symbols_quick(trade_manager) -> None:
    config = trade_manager.config
    base_path = Path(config.BASE_PATH)
    def _norm(s: str) -> str:
        s = str(s).strip()
        if ':' in s:
            parts = s.split(':', 1)
            return f"{parts[0].lower()}:{parts[1].upper()}"
        return s.upper()

    async def _read_set(filename: str) -> Set[str]:
        if not filename: return set()
        path = base_path / filename
        try:
            if await aio_os.path.exists(path):
                async with aiofiles.open(path, "rb") as f:
                    content = await f.read()
                    if not content: return set()
                    data = orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                    
                    if isinstance(data, list):
                        return {_norm(s) for s in data}
                    
                    if isinstance(data, dict):
                        # Support both {"symbols": [...]} and raw dict
                        if 'symbols' in data: return {_norm(s) for s in data['symbols']}
                        if 'pairs' in data: return {_norm(s) for s in data['pairs']}
                        return {_norm(s) for s in data.keys()}
        except Exception: 
            pass
        return set()

    try:
        fin_file = getattr(config, 'SYMBOLS_FIN', 'symbols_fin.json')

        results = await asyncio.gather(
            _read_set(getattr(config, 'SYMBOLS_ANG_LONG', 'symbols_ang_long.json')),
            _read_set(getattr(config, 'SYMBOLS_ANG_SHORT', 'symbols_ang_short.json')),
            _read_set(getattr(config, 'SYMBOLS_INF_LONG', 'symbols_inf_long.json')),
            _read_set(getattr(config, 'SYMBOLS_INF_SHORT', 'symbols_inf_short.json')),
            _read_set(getattr(config, 'SYMBOLS_FLZ', 'symbols_flz.json')),
            _read_set(getattr(config, 'SYMBOLS_MEN', 'symbols_men.json')),
            _read_set(getattr(config, 'SYMBOLS_FIN', 'symbols_fin.json')),
            _read_set(getattr(config, 'TRADEABLE_KEYS', 'tradeable_keys.json')),
            _read_set(getattr(config, 'SYMBOLS_FILE', 'symbols.json')),
            _read_set(getattr(config, 'SYMBOLS_ACTIVE_FILE', 'symbols_active.json')))

        trade_manager.symbols_ang_long = results[0]
        trade_manager.symbols_ang_short = results[1]
        trade_manager.symbols_inf_long = results[2]
        trade_manager.symbols_inf_short = results[3]
        trade_manager.symbols_flz = results[4]
        trade_manager.symbols_men = results[5]
        trade_manager.symbols_fin = results[6] 
        tradeable_keys_from_file = results[7]
        trade_manager.symbols = results[8]
        trade_manager.symbols_active = results[9]
        final_keys = tradeable_keys_from_file 
        sorted_keys = sorted(list(final_keys))
        if sorted_keys:
            trade_manager.tradeable_keys = set(sorted_keys)
            fin_count = len([k for k in sorted_keys if k.startswith('fin:')])
            men_count = len([k for k in sorted_keys if k.startswith('men:')])
            
            logger.info(f"[SYMBOLS] ✅ Loaded {len(sorted_keys)} keys. Breakdown: FIN={fin_count}, MEN={men_count}")
        else:
            logger.warning("[SYMBOLS] ❌ Universe is empty.")
    except Exception as e:
        logger.error(f"[SYMBOLS] Critical Fail: {e}", exc_info=True)

async def manual_load_leaderboards(trade_manager):
    try:
        import aiofiles
        import orjson
        async def _read_json(path):
            if not path: return {}
            p = Path(path) if isinstance(path, str) else path
            if not p.exists(): return {}
            try:
                async with aiofiles.open(p, "rb") as f:
                    content = await f.read()
                    return orjson.loads(content) if content else {}  # type: ignore # pylint: disable=no-member,c-extension-no-member
            except Exception: return {}

        w20_data = await _read_json(config.WINNERS_20_FILE)
        l20_data = await _read_json(config.LOSERS_20_FILE)
        
        w15_path = config.WINNERS_15M_FILE if os.path.exists(config.WINNERS_15M_FILE) else (config.DATA_DIR / "winners_30r")
        l15_path = config.LOSERS_15M_FILE if os.path.exists(config.LOSERS_15M_FILE) else (config.DATA_DIR / "losers_30r")
        
        w15_data = await _read_json(w15_path)
        l15_data = await _read_json(l15_path)

        def to_lookup_dict(data):
            if isinstance(data, dict): return data
            if isinstance(data, list):
                return {item['symbol'].strip().upper(): item for item in data if isinstance(item, dict) and 'symbol' in item}
            return {}
        trade_manager.winners_20 = to_lookup_dict(w20_data)
        trade_manager.losers_20 = to_lookup_dict(l20_data)
        trade_manager.winners_15m = to_lookup_dict(w15_data)
        trade_manager.losers_15m = to_lookup_dict(l15_data)
    except Exception as e:
        logger.error(f"[Leaderboard Load] Failed: {e}")


# async def periodic_metadata_refresh(trade_manager, tracker_manager, order_queue):
#     logger.info("🔄 [METADATA] Starting COMPREHENSIVE refresh loop (Symbols, Rankings, Account States)")
#     while True:
#         try:
#             await load_symbols_quick(trade_manager)
#             allowed_accounts = getattr(trade_manager, '_allowed_accounts', set())
#             if not allowed_accounts: 
#                 allowed_accounts = set(trade_manager.accounts.keys())
#             for account_key in allowed_accounts:
#                 try:
#                     await trade_manager.load_augmented_positions(account_key)
#                     await trade_manager.load_reduced_positions(account_key)
#                     await trade_manager.load_reversed_positions(account_key)
#                     await trade_manager.load_direct_high_gain(account_key)
#                     await trade_manager.load_stop_levels(account_key)
#                     await trade_manager.load_reentry_data(account_key)
#                     await trade_manager.load_ladder_levels(account_key)
#                     await tracker_manager.load_tracker(account_key)
#                 except Exception as acc_err:
#                     logger.error(f"❌ [METADATA] Failed to refresh account {account_key}: {acc_err}")
#             if random.random() < 0.05: 
#                 logger.info(f"🔄 [METADATA] Refresh Alive. Loaded states for {len(allowed_accounts)} accounts.")

#         except Exception as e:
#             logger.error(f"❌ [METADATA] Global Refresh Failed: {e}")
#         await asyncio.sleep(60)

def _get_prev_cross_price(symbol: str, is_long: bool) -> float:
    try:
        global _last_events_cache, _last_events_cache_time
        if _last_events_cache is None or (time.time() - _last_events_cache_time) > 60: _refresh_last_events_cache()
        node = _last_events_cache.get(symbol, {}).get('3m', {}) if _last_events_cache else {}
        key = 'stoch_crossover' if is_long else 'stoch_crossunder'
        prev = node.get(key, {}).get('previous', {})
        return safe_fetch_float(prev.get('price'), 0.0)
    except: return 0.0

async def record_trade_event(tracker_manager: TrackerManager, account_key: str, position_key: str, symbol: str, action: str, side: str, current_price: float, qty: float, reason: str, is_paper: bool, is_hedge: bool = False, hedge_for: Optional[str] = None):
    # Only process REAL trades
    if is_paper: return 

    ts_now = datetime.now(timezone.utc).isoformat()
    
    event = {
        "ts": ts_now,
        "account": account_key,
        "position_key": position_key,
        "symbol": symbol,
        "action": action,
        "side": side,
        "price": current_price,
        "qty": qty,
        "reason": reason,
        "is_hedge": is_hedge,
        "hedge_for": hedge_for,
        "pnl_usd": 0.0,
        "pnl_pct": 0.0 # Initialize pnl_pct
    }

    # Locate Data in Tracker (Exit or Entry list)
    candidate_data = None
    lock = None
    
    async with tracker_manager._exit_candidates_lock:
        if position_key in tracker_manager.exit_candidates:
            candidate_data = tracker_manager.exit_candidates[position_key]
            
    if not candidate_data:
        async with tracker_manager._entry_candidates_lock:
            if position_key in tracker_manager.entry_candidates:
                candidate_data = tracker_manager.entry_candidates[position_key]

    if candidate_data:
        # Calculate PnL for closing events for the log
        if action in ['CLOSE', 'REDUCE', 'SEMI-STOP', 'PROFIT_TAKE']:
            avg_entry = safe_fetch_float(candidate_data.get('average_entry_price', 0.0))
            # Fallback to entry_price if average is 0
            if avg_entry <= 0: avg_entry = safe_fetch_float(candidate_data.get('entry_price', 0.0))

            if avg_entry > 0:
                is_long = position_key.endswith('_LONG')
                if is_long:
                    pnl_usd = (current_price - avg_entry) * qty
                    pnl_pct = ((current_price - avg_entry) / avg_entry) * 100.0
                else:
                    pnl_usd = (avg_entry - current_price) * qty
                    pnl_pct = ((avg_entry - current_price) / avg_entry) * 100.0
                
                event['pnl_usd'] = pnl_usd
                event['pnl_pct'] = pnl_pct

                # --- CRITICAL FIX: Increment HARD COUNTERS immediately ---
                # This ensures stats persist even if trade_log is truncated later
                candidate_data['total_trades_count'] = int(candidate_data.get('total_trades_count', 0)) + 1
                candidate_data['total_trades'] = int(candidate_data.get('total_trades', 0)) + 1
                
                if pnl_usd > 0:
                    candidate_data['winning_trades'] = int(candidate_data.get('winning_trades', 0)) + 1
                    # Reset consecutive counters
                    candidate_data['consecutive_wins'] = int(candidate_data.get('consecutive_wins', 0)) + 1
                    candidate_data['consecutive_losses'] = 0
                elif pnl_usd < 0:
                    candidate_data['losing_trades'] = int(candidate_data.get('losing_trades', 0)) + 1
                    # Reset consecutive counters
                    candidate_data['consecutive_losses'] = int(candidate_data.get('consecutive_losses', 0)) + 1
                    candidate_data['consecutive_wins'] = 0
                
                # Update Win Rate immediately
                total = candidate_data['total_trades']
                wins = candidate_data['winning_trades']
                if total > 0:
                    candidate_data['win_rate_%'] = (wins / total) * 100.0
        if 'trade_log' not in candidate_data: candidate_data['trade_log'] = []
        candidate_data['trade_log'].append(event)
        if len(candidate_data['trade_log']) > 500:
            candidate_data['trade_log'] = candidate_data['trade_log'][-500:]
        tracker_manager._exit_candidates_dirty[account_key] = True
        tracker_manager._entry_candidates_dirty[account_key] = True
        

async def log_account_summary(account_key: str, trade_manager, tracker_manager: TrackerManager):
    """Log per-account summary showing entry/exit candidates, positions, and trade blockers"""
    try:
        acc_logger = get_account_logger(account_key) 
        async with tracker_manager._entry_candidates_lock:
            entry_keys = [k for k in tracker_manager.entry_candidates.keys() if k.startswith(f"{account_key}:")]
        async with tracker_manager._exit_candidates_lock:
            exit_keys = [k for k in tracker_manager.exit_candidates.keys() if k.startswith(f"{account_key}:")]
        positions = tracker_manager.positions_service.positions_by_account.get(account_key, {})
        # position = positions.get(position_key)
        # pos_min_qty = max(2 * config.MIN_POSITION_SIZE / position.mark_price, trade_manager.min_qty.get(symbol, 0.0))
        active_positions = {k: v for k, v in positions.items() if k.startswith(f"{account_key}:") and abs(safe_fetch_float(getattr(v, 'positionAmt', 0.0), 0.0)) > 0.0}
        entry_reasons = {}
        for key in entry_keys:
            async with tracker_manager._entry_candidates_lock:
                data = tracker_manager.entry_candidates.get(key, {})
                reason = data.get('last_reason', 'UNKNOWN')
                entry_reasons[reason] = entry_reasons.get(reason, 0) + 1
        
        logger.info(f"[ACCOUNT_SUMMARY][{account_key}] Entry: {len(entry_keys)} | Exit: {len(exit_keys)} | Active Positions: {len(active_positions)}")
        if entry_reasons:
            reason_str = ", ".join([f"{k}: {v}" for k, v in sorted(entry_reasons.items(), key=lambda x: x[1], reverse=True)])
            logger.info(f"[ACCOUNT_SUMMARY][{account_key}] Entry Blockers: {reason_str}")
        if len(entry_keys) > 0:
            high_score_count = 0
            for key in entry_keys:
                async with tracker_manager._entry_candidates_lock:
                    data = tracker_manager.entry_candidates.get(key, {})
                    score = safe_fetch_float(data.get('last_score', 0.0), 0.0)
                    if score >= 4: high_score_count += 1
            logger.info(f"[ACCOUNT_SUMMARY][{account_key}] Entry Candidates with Score >= 4: {high_score_count}/{len(entry_keys)}")
    except Exception as e:
        logger.error(f"[log_account_summary][{account_key}] Error: {e}")

async def log_tradeable_symbols_status(account_key: str, trade_manager, tracker_manager: TrackerManager, data_manager: FastDataManager) -> None:
    try:
        tradeable_keys = await tracker_manager._get_tradeable_keys_cached()
        tradeable_position_keys = {k for k in tradeable_keys if k.startswith(f"{account_key}:")}
        if not tradeable_position_keys: return
        async with tracker_manager._entry_candidates_lock:
            entry_candidates = {k: v for k, v in tracker_manager.entry_candidates.items() if k.startswith(f"{account_key}:")}
        status_list = []
        for position_key in sorted(tradeable_position_keys):
            try:
                account, symbol, side = parse_position_key(position_key)
                entry_data = entry_candidates.get(position_key, {})
                reason = entry_data.get('last_reason', 'UNKNOWN')
                if not reason or reason == 'UNKNOWN':
                    if await tracker_manager.is_processing(position_key): reason = 'PROCESSING'
                    elif await tracker_manager.is_trade_cooldown_active(position_key): reason = 'COOLDOWN'
                    elif not entry_data: reason = 'NO_DATA'
                    else: reason = 'WAITING_FOR_SIGNAL'
                status_list.append(f"{symbol}_{side}: {reason}")
            except: continue
        if status_list:
            logger.info(f"[TRADEABLE_STATUS][{account_key}] {len(status_list)} symbols: {', '.join(status_list[:20])}" + (f" ... (+{len(status_list)-20} more)" if len(status_list) > 20 else ""))
    except Exception as e:
        logger.error(f"[log_tradeable_symbols_status][{account_key}] Error: {e}")


async def unified_monitoring_loop(trade_manager, account_key: str, stop_event: asyncio.Event, tracker_manager: TrackerManager, data_manager: FastDataManager, hedge_engine: HedgeEngine):
    logger.info(f"🚀 [UNIFIED_LOOP][{account_key}] STARTED - High Frequency"  )
    loop_tick = 0
    while not stop_event.is_set():
        try:
            await tracker_manager._get_tradeable_keys_cached() 
            loop_tick += 1
            universe = list(tracker_manager.get_tradeable_position_keys_for(account_key))
            active_account_keys = set()
            waiting_keys = set()
            async with tracker_manager._exit_candidates_lock:
                active_account_keys = {k for k in tracker_manager.exit_candidates.keys() if k.startswith(account_key)}     
            waiting_keys = {k for k in universe if k not in active_account_keys}
            queue_items = []
            async with tracker_manager._exit_candidates_lock:
                for k in active_account_keys:
                    data = tracker_manager.exit_candidates.get(k, {})
                    last = data.get('last_logic_check', 0)
                    queue_items.append({'key': k, 'type': 'EXIT', 'last_check': last, 'priority': 0})
            async with tracker_manager._entry_candidates_lock:
                for k in waiting_keys:
                    if k not in tracker_manager.entry_candidates:
                        tracker_manager.entry_candidates[k] = tracker_manager._entry_template()
                    data = tracker_manager.entry_candidates.get(k, {})
                    last = data.get('last_logic_check', 0)
                    queue_items.append({'key': k, 'type': 'ENTRY', 'last_check': last, 'priority': 1})
            if not queue_items:
                if loop_tick % 50 == 0:
                    logger.warning(f"⚠️ [UNIFIED][{account_key}] No keys to monitor. Check config/symbols.")
                await asyncio.sleep(2.0)
                continue
            queue_items.sort(key=lambda x: x['last_check'])
            BATCH_SIZE = 100 
            batch = queue_items[:BATCH_SIZE]
            async def process_item(item):
                key = item['key']
                key_type = item['type']
                try:
                    if key_type == 'EXIT':
                        await check_exit_candidates_for_account(trade_manager, account_key, None, tracker_manager,  None, data_manager, hedge_engine, position_keys=[key]  )
                    else:
                        if key in tracker_manager.tradeable_keys:
                            await check_entry_candidates_for_account( trade_manager, account_key, None, tracker_manager,  None, data_manager, hedge_engine, position_keys=[key] )
                except Exception as e:
                    logger.error(f"[LoopItemError] {key}: {e}")
            async with asyncio.timeout(25.0):
                await asyncio.gather(*(process_item(item) for item in batch))
            await asyncio.sleep(0.01)
        except Exception as e:
            logger.error(f"[UNIFIED_LOOP][{account_key}] Critical: {e}", exc_info=True)
            await asyncio.sleep(5)

class TradeVerifier:
    def __init__(self, position_callback_manager=None, positions_by_account=None, active_maker_orders=None, managed_maker_order_registry=None, accounts=None, positions_service=None):
        self.position_callback_manager = position_callback_manager
        self.positions_by_account = positions_by_account or {}
        self.active_maker_orders = active_maker_orders or {}
        self.managed_maker_order_registry = managed_maker_order_registry or {}
        self.accounts = accounts or {}
        self.positions_service = positions_service

    async def verify_trade(self, account_key: str, position_key: str, expected_qty: float, is_long: bool, timeout_seconds: int = 10, positionAmt: float = 0.0, action: str = '') -> bool:
        if not self.positions_by_account:
            logger.info(f"[TradeVerifier][{position_key}] positions_by_account is None, cannot verify trade")
            return False
        symbol = position_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
        start_time = time.time()
        if action in ['CLOSE', 'REDUCE'] and positionAmt <= 0:
            return False
        has_ws_manager = self.position_callback_manager is not None
        # 1. FAST CHECK: WebSocket Buffer Loop
        # We loop quickly to catch the update as soon as it arrives
        for attempt in range(int(timeout_seconds * 2)): # Check every 0.5s
            try:
                await asyncio.sleep(0.5)
                # Stop if total time exceeded
                if time.time() - start_time >= timeout_seconds:
                    break
                current_amt = None
                # Check WebSocket Manager Buffer
                if has_ws_manager and self.position_callback_manager:
                    try:
                        ws_latest = await self.position_callback_manager.get_latest_position(position_key)
                        if ws_latest:
                            # Check freshness (ignore old buffered data)
                            ts_val = ws_latest.get('timestamp')
                            if isinstance(ts_val, str): 
                                from dateutil.parser import isoparse
                                ts_val = isoparse(ts_val)
                            if isinstance(ts_val, datetime):
                                if ts_val.tzinfo is None: ts_val = ts_val.replace(tzinfo=timezone.utc)
                                # Only accept data received AFTER we started checking
                                if ts_val.timestamp() > start_time - 1.0: 
                                    current_amt = abs(safe_fetch_float(ws_latest.get('positionAmt', 0.0), 0.0))
                    except: pass
                # Check In-Memory Dictionary (in case updated by other process)
                if current_amt is None:
                    positions = self.positions_service.positions_by_account.get(account_key, {})
                    current_pos = positions.get(position_key)
                    if current_pos:
                        # Check freshness
                        last_upd = getattr(current_pos, 'last_updated', None)
                        if last_upd:
                            if last_upd.tzinfo is None: last_upd = last_upd.replace(tzinfo=timezone.utc)
                            if last_upd.timestamp() > start_time - 1.0:
                                current_amt = abs(safe_fetch_float(getattr(current_pos, 'positionAmt', 0.0), 0.0))
                # Validate the data if we found any
                if current_amt is not None:
                    qty_diff = float(current_amt) - float(positionAmt)
                    expected_qty_float = float(expected_qty)
                    expected_diff = expected_qty_float if is_long else -expected_qty_float
                    if action in ['CLOSE', 'REDUCE']: 
                        expected_diff = -expected_qty_float if is_long else expected_qty_float
                    if abs(qty_diff) >= abs(expected_diff) * 0.1:
                        # Direction check
                        if (qty_diff > 0 and expected_diff > 0) or (qty_diff < 0 and expected_diff < 0):
                            logger.info(f"[TradeVerifier] ✅ Verified via WS/Mem: {current_amt:.6f} (diff={qty_diff:.6f})")
                            return True
            except Exception:
                await asyncio.sleep(0.5)
        # 2. SLOW CHECK: Force API Fetch and Compare
        try:
            if self.positions_service:
                await self.positions_service.fetch_positions(account_key)

            positions = self.positions_service.positions_by_account.get(account_key, {})
            current_pos = positions.get(position_key)
            final_amt = abs(safe_fetch_float(getattr(current_pos, 'positionAmt', 0.0), 0.0)) if current_pos else 0.0
            qty_diff = float(final_amt) - float(positionAmt)
            expected_qty_float = float(expected_qty)
            expected_diff = expected_qty_float if is_long else -expected_qty_float
            if action in ['CLOSE', 'REDUCE']: 
                expected_diff = -expected_qty_float if is_long else expected_qty_float
            # Check if position changed in expected direction (even partially)
            if abs(qty_diff) >= abs(expected_diff) * 0.1:
                if (qty_diff > 0 and expected_diff > 0) or (qty_diff < 0 and expected_diff < 0):
                    logger.info(f"[TradeVerifier] ✅ Executed: {final_amt:.6f} (diff={qty_diff:.6f})")
                    return True
            # Special case: OPEN when initial is 0 and final > 0
            if abs(positionAmt) < 1e-6 and action not in ['CLOSE', 'REDUCE'] and final_amt > 1e-6:
                logger.info(f"[TradeVerifier] ✅ Executed (OPEN): {final_amt:.6f}")
                return True
            # Special case: CLOSE when position vanished
            if action in ['CLOSE', 'REDUCE'] and final_amt < 1e-6 and abs(positionAmt) >= abs(expected_qty) * 0.9:
                logger.info(f"[TradeVerifier] ✅ Executed (CLOSED): position gone")
                return True
        except Exception as e:
            logger.error(f"[TradeVerifier] API fetch failed: {e}")
        # 3. No execution detected - cancel pending orders
        order_ids_to_cancel = []
        if position_key in self.active_maker_orders:
            order_info = self.active_maker_orders[position_key]
            order_id = order_info.get('order_id')
            if order_id and str(order_id).isdigit():
                order_ids_to_cancel.append(int(order_id))
        for order_id_str, order_info in list(self.managed_maker_order_registry.items()):
            if order_info.get('position_key') == position_key:
                try:
                    oid = int(order_id_str)
                    if oid not in order_ids_to_cancel: order_ids_to_cancel.append(oid)
                except: pass
        if order_ids_to_cancel and account_key in self.accounts:
            account = self.accounts[account_key]
            if account and hasattr(account, 'client') and account.client:
                client = account.client
                for order_id in order_ids_to_cancel:
                    try:
                        asyncio.create_task(asyncio.to_thread(client.futures_cancel_order, symbol=symbol, orderId=order_id))
                    except Exception: pass
        return False

class SentimentExposureManager:
    def __init__(self, trade_manager, tracker_manager, data_manager, config, registry):
        self.trade_manager = trade_manager
        self.tracker_manager = tracker_manager
        self.data_manager = data_manager
        self.config = config
        self.registry = registry
        self.last_run_time = 0
        self.run_interval = 20  
        self.reduced_positions_registry = {} 

    async def run_loop(self, stop_event: asyncio.Event):
        logger.info("⚖️ [SENTIMENT_MGR] Starting Exposure & Rebalance Loop")
        while not stop_event.is_set():
            try:
                await self.process_portfolio()
                await asyncio.sleep(self.run_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[SENTIMENT_MGR] Loop Error: {e}")
                await asyncio.sleep(30)

    async def process_portfolio(self):
        metrics, btc_ind, k_1m, d_1m, k_3m, d_3m, is_data_fresh = await self.data_manager.get_hot_state("BTCUSDC")
        if not btc_ind: 
            return
        sentiment_score = safe_fetch_float(btc_ind.get('0market_sentiment_score', 0))
        sentiment_ema = safe_fetch_float(btc_ind.get('0market_sentiment_score_ema', 0))
        
        # Determine Regime
        # Bullish Bias: Score > EMA (Target Longs 2x Shorts)
        # Bearish Bias: Score < EMA (Target Shorts 2x Longs)
        is_bullish_regime = sentiment_score > sentiment_ema
        
        # 2. ANALYZE CURRENT PORTFOLIO
        long_value = 0.0
        short_value = 0.0
        active_longs = []
        active_shorts = []
        
        async with self.tracker_manager._exit_candidates_lock:
            for k, v in self.tracker_manager.exit_candidates.items():
                if v.get('status') != 'active': continue
                
                # Get current value
                
                amt = safe_fetch_float(v.get('positionAmt', 0))
                ak,sym,ps=parse_position_key(k)
                # price,ts = self.data_manager.get_fresh_price(sym)
                # val = amt * price
                
                # if k.endswith('_LONG'):
                #     long_value += val
                #     active_longs.append(k)
                # elif k.endswith('_SHORT'):
                #     short_value += val
                if ps=='LONG' and amt > 0: 
                    active_shorts.append(k)
                if ps=='SHORT' and amt > 0: 
                    active_shorts.append(k)
        all_positions = active_longs + active_shorts
        
        for pos_key in all_positions:
            await self._manage_individual_position(pos_key, is_bullish_regime)

        # 4. ENFORCE GLOBAL RATIO (Gentle Nudging)
        # We only block new entries or force reductions if ratio is wildly off.
        # We rely on the individual logic above to do the heavy lifting, 
        # but we apply a bias here if things get extreme.
        
        total_value = long_value + short_value
        if total_value > 1000: # Only balance if portfolio has size
            ratio = long_value / short_value if short_value > 0 else 999.0
            
            if is_bullish_regime:
                # Target: Longs ~ 2x Shorts (Ratio ~ 2.0)
                if ratio < 1.0: 
                    logger.info(f"⚖️ [SENTIMENT] Bullish Regime (Score {sentiment_score:.0f} > EMA {sentiment_ema:.0f}) but Ratio {ratio:.2f}. Bias: FAVOR LONGS.")
                    # Logic: We might be more aggressive adding to longs in _mana ge_individual_position
            else:
                # Target: Shorts ~ 2x Longs (Ratio ~ 0.5)
                if ratio > 1.0:
                    logger.info(f"⚖️ [SENTIMENT] Bearish Regime (Score {sentiment_score:.0f} < EMA {sentiment_ema:.0f}) but Ratio {ratio:.2f}. Bias: FAVOR SHORTS.")

    async def _manage_individual_position(self, position_key: str, is_bullish_regime: bool):
        await self.tracker_manager._get_tradeable_keys_cached() 
        account_key, symbol, side = parse_position_key(position_key)
        is_long = (side == 'LONG')
        if not self.trade_manager._check_account_allowed(account_key): return
        candidate = await self.tracker_manager.get_exit_candidate(position_key)
        if not candidate: return
        current_price, ts = await self.data_manager.get_fresh_price(symbol)
        if current_price <= 0: return
        position = await self.tracker_manager.get_position(position_key)
        metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_data_fresh = await self.data_manager.get_hot_state(symbol)
        pnl_pct = safe_fetch_float(candidate.get('current_gain_%', 0))
        if position: pos_amt = position.positionAmt
        else: pos_amt = safe_fetch_float(candidate.get('positionAmt', 0))
        min_usd = getattr(self.config, 'MIN_POSITION_SIZE', 5.0)
        min_qty = max(min_usd / current_price, self.trade_manager.min_qty.get(symbol, 0)) * 1.05
        k_1m = float(metrics.get('stoch_k_1m', 50))
        k_3m = float(metrics.get('stoch_k_3m', 50));d_3m = float(metrics.get('stoch_d_3m', 50))
        k_15m = float(indicators.get('stoch_k_15m', 50))
        d_15m = float(indicators.get('stoch_k_dm', 50))
        d_15m = float(indicators.get('stoch_d_1m', 50))
        if (k_3m==50 and k_3m==50) or (k_15m==50 and d_15m==50):return
        aligned_with_sentiment = (is_long and is_bullish_regime) or (not is_long and not is_bullish_regime)
        # --- LOGIC A: PYRAMID (Augment) ---
        is_pullback = (is_long and k_15m < 30) or (not is_long and k_15m > 70)
        if pnl_pct > 1.0 and is_pullback and aligned_with_sentiment and position_key in self.tracker_manager.tradeable_keys:
            if await self.tracker_manager.is_trade_cooldown_active(position_key): return
            base_usd = getattr(self.config, 'START_POSITION_SIZE', 50)
            add_qty = (base_usd / current_price) * 0.5
            logger.info(f"📈 [SENTIMENT_PYRAMID] {position_key}: Gain {pnl_pct:.2f}% > 2% & k_15m={k_15m:.0f}. Augmenting by {add_qty:.6f}")
            success,msg=await execute_trade_wrapper( self.trade_manager, self.tracker_manager, self.trade_manager.hedge_engine, account_key, position_key, pos_amt, 'AUGMENT', current_price, add_qty,  f"SENTIMENT_PYRAMID_GAIN{pnl_pct:.1f}_k3:{k_3m}/d3:{k_3m}_k_15m{k_15m:.0f}", is_hedge=False  )
            return

        is_large_position = pos_amt > (config.START_POSITION_SIZE / current_price * 3)
        if pnl_pct < 1.5 and is_large_position:
            threshold = 1.0 if aligned_with_sentiment else 1.5
            
            if pnl_pct < threshold:
                is_flat = (-2.5 < pnl_pct < 0.2)
                is_huge = (pos_amt > min_qty * 10)
                
                if is_flat and not is_huge:
                    return

                if await self.tracker_manager.is_trade_cooldown_active(position_key): return                
                
                reduce_qty = pos_amt - min_qty
                if reduce_qty > 0:
                    logger.info(f"📉 [SENTIMENT_REDUCE] {position_key}: Gain {pnl_pct:.2f}% < {threshold}%. Cutting {pos_amt} - {reduce_qty:.6f} to MIN.")
                    success,msg=await execute_trade_wrapper( self.trade_manager, self.tracker_manager, self.trade_manager.hedge_engine,   account_key, position_key, pos_amt, 'REDUCE', current_price, reduce_qty,   f"SENTIMENT_CUT_GAIN{pnl_pct:.1f}", is_hedge=False )
                    self.registry.release_hedge_slot(account_key, symbol)
                    #     await self.clear_processing(position_key)
                    #     await self.set_trade_cooldown(position_key)
                    #     position_side = "SHORT" if is_long else "LONG",
                    #     await self.transition_to_entry(account_key, position_key, current_price, 0.0,position_side, status="REDUCED->ENTRY_CANDIDATE")
                    #     await lo g_stoch_snapshot(account_key, 'REDUCE', position_key, current_price, metrics, indicators, self, '-7', rec_exit, reason_exit, dummy_state, dummy_lock, acc_logger)                    
                    #     await self.register_check(position_key)
                    #     await record_trade_event(self, account_key, position_key, symbol, 'REDUCE', position_side, current_price, positionAmt, reason_exit, False, False)
                    #     await self.save_tracker(account_key, force=True)
                            
                    return

        # --- LOGIC C: RE-ENTRY (Restore to Max) ---
        if position_key in self.reduced_positions_registry:
            reg_data = self.reduced_positions_registry[position_key]

            if pos_amt > (min_qty * 1.5):
                del self.reduced_positions_registry[position_key]
                return
            higher_high_15m = bool(i.get('high_15m', 0) and i.get('high_15m_prev', 0) and i.get('high_15m', 0) >= i.get('high_15m_prev', 0))
            lower_low_15m = bool(i.get('low_15m', 0) and i.get('low_15m_prev', 0) and i.get('low_15m', 0) <= i.get('low_15m_prev', 0))
            k_3m_up = (k_3m > d_3m) and (k_3m > 5)
            if not k_3m_up: return
            reduced_price = reg_data['reduced_at_price']
            structure_confirmed = False
            if is_long:
                if current_price > reduced_price and (k_15m > float(indicators.get('stoch_d_15m', 100)) or higher_high_15m):
                    structure_confirmed = True
            else:
                if current_price < reduced_price and (k_15m < float(indicators.get('stoch_d_15m', 0)) or lower_low_15m):
                    structure_confirmed = True
            if structure_confirmed and aligned_with_sentiment:
                if await self.tracker_manager.is_trade_cooldown_active(position_key): return
                
                base_target = getattr(self.config, 'START_POSITION_SIZE', 50) * 2.5
                target_size = max(base_target, reg_data['original_size'])
                qty_to_buy = (target_size / current_price) - pos_amt
                if qty_to_buy > 0:
                    logger.info(f"🚀 [SENTIMENT_RE_ENTRY] {position_key}: Momentum Returned. Adding {qty_to_buy:.6f} to restore size.")
                    await execute_trade_wrapper( self.trade_manager, self.tracker_manager, self.trade_manager.hedge_engine,   account_key, position_key, pos_amt, 'AUGMENT', current_price, qty_to_buy, "SENTIMENT_RE_ENTRY_MAX", is_hedge=False  )

async def execute_trade_wrapper(trade_manager, tracker_manager: TrackerManager, hedge_engine: HedgeEngine, account_key: str, position_key: str, positionAmt:float, action: str, current_price: float, qty: float, reason: str, already_locked: bool = False, is_hedge: bool = False, hedge_for: Optional[str] = None, override_qty: Optional[float] = None, verify_via_websocket: bool = True, data_manager: Optional[FastDataManager] = None) -> tuple[bool, str]:
    if hasattr(trade_manager, '_allowed_accounts') and account_key not in trade_manager._allowed_accounts:
        logger.critical(f"🛑 [EXECUTE_BLOCKED] Attempted trade for {account_key} but instance is restricted to {trade_manager._allowed_accounts}")
        return False, "ACCOUNT_MISMATCH"
    if account_key not in trade_manager.accounts:
        return False, f"IGNORED: Account {account_key} not loaded in TradeManager."
    if 'OPEN' in action or 'OPEN' in reason.upper() and positionAmt > 0:
        logger.info:(f"BLOCK_YOUFUCKINGPIECEOFSHIT_OPEN IS FOR ZERO YOU FUCKING DISGRACEFUL MOTHER FUCKER SICK MOTHER FUCKING BITCH FUCK")
    if 'WAIT' in reason:
        # if 'QUICK' not in action and 'QUICK' not in reason and 'REENTRY' not in reason:
        logger.warning(f"🛡️[WAPPIE] {position_key} {reason}: WAIT MEANS WAIT") #NEEDS FUCKING WORK
        return False, f'{position_key} WAIT MEANS WAIT'
    
    # --- HEDGE_MODE_BLOCK ---
    if is_hedge_account(config, account_key) and ('CLOSE' in action or 'REDUCE' in action) and not is_hedge:
        # Check if this position has an active hedge record
        async with tracker_manager._hedges_lock:
            has_active_hedge = any(h.get('losing_position_key') == position_key for h in tracker_manager.active_hedges)
        if has_active_hedge and "HEDGE" not in reason.upper() and "ENGINE" not in reason.upper():
             logger.warning(f"🛡️ [HEDGE_MODE_BLOCK] {position_key}: Blocking {action} ({reason}) - Must be managed by HedgeEngine.")
             return False, "BLOCKED_BY_HEDGE_MODE"

    service = tracker_manager.positions_service
    fresh_position = await tracker_manager.get_position(position_key)
    ak, symbol, position_side = parse_position_key(position_key)
    is_long = (position_side == 'LONG')
    pos_min_qty = max(config.MIN_POSITION_SIZE / current_price, trade_manager.min_qty.get(symbol, 0.0) * 1.2)
    success=False
    if fresh_position:
        real_amt = abs(safe_fetch_float(getattr(fresh_position, 'positionAmt', 0.0), 0.0))
        real_gain = safe_fetch_float(getattr(fresh_position, 'gain', 0.0), 0.0)
        last_aug = getattr(fresh_position, 'last_augmentation_time', None)
    else:
        real_amt = 0.0
        real_gain = 0.0
        last_aug = None    
    if action == 'OPEN' and real_amt > 1.3  * pos_min_qty and 'HEDGE' not in reason.upper() and fresh_position.gain < 0.7:
        logger.warning(f"🛑 [EXECUTE_BLOCKED] {position_key}: Strategy tried OPEN, but Real Amt is {real_amt:.6f}. Force Syncing.")
        await tracker_manager.transition_to_exit(account_key, position_key, current_price, real_amt, status='active')
        if "BREAKOUT_PLAY" in reason or "MOMENTUM_SCALP" in reason:
            if not hasattr(trade_manager, 'strict_close_positions'):
                trade_manager.strict_close_positions = {}
            trade_manager.strict_close_positions[position_key] = {
                'augmented_at': datetime.now(timezone.utc),
                'augment_price': current_price,
                'strict_loss_threshold': -0.4,   # micro-stop: Cut at -0.4% loss
                'strict_gain_threshold': 1.0,    # Take quick profit if it stalls
                'strict_reduction_trigger': True }
            logger.info(f"🔒 [TIGHT_LEASH_APPLIED] {position_key}: Breakout Scalp locked into Strict Close Monitor.")
        return False, "BLOCKED_ALREADY_OPEN_STATE_MISMATCH"
    if action == 'AUGMENT':
        MIN_AUGMENT_GAIN = 0.5 # 0.4% minimum gain to add more
        if real_gain < MIN_AUGMENT_GAIN and positionAmt*current_price > config.START_POSITION_SIZE:
            logger.info(f"🛑 [EXECUTE_BLOCKED] {position_key}: AUGMENT rejected. Gain {real_gain:.2f}% < {MIN_AUGMENT_GAIN}%")
            return False, f"BLOCKED_GAIN_TOO_LOW_FOR_AUGMENT ({real_gain:.2f}%)"
        if last_aug:
            if isinstance(last_aug, str):
                try: last_aug = isoparse(last_aug)
                except: pass
            if isinstance(last_aug, datetime):
                if last_aug.tzinfo is None: last_aug = last_aug.replace(tzinfo=timezone.utc)
                seconds_since = (datetime.now(timezone.utc) - last_aug).total_seconds()
                if seconds_since < 120: # 2 minutes cooldown
                    logger.info(f"🛑 [EXECUTE_BLOCKED] {position_key}: AUGMENT rejected. Last add was {seconds_since:.0f}s ago.")
                    return False, "BLOCKED_AUGMENT_TOO_SOON"
    logger.info(f"🔄 [EXECUTE] {position_key}: {action} | ReqQty={qty:.6f} | RealAmt={real_amt:.6f} | Gain={real_gain:.2f}%")
    if action == 'OPEN':
        qty = max(config.START_POSITION_SIZE / current_price, pos_min_qty, qty)
    unique_id = generate_unique_id(position_key, reason)
    if 'CLOSE' in action or 'REDUCE' in action:
        side = 'SELL' if is_long else 'BUY'
        reduce_qty=fresh_position.positionAmt - pos_min_qty if 'REDUCE' in action else fresh_position.positionAmt
        # if 'STRONG' in reason.upper() or 'NOW' in reason.upper() and fresh_position.positionAmt * current_price > 2 * config.START_POSITION_SIZE:
        result = await trade_manager.execute_now(position_key, account_key, symbol, real_amt, side, position_side, reduce_qty, current_price, unique_id, f"QUICK_{reason}_REDUCE", False, action, is_hedge=is_hedge, hedge_for=hedge_for)
        if 'SUCCESS' not in result and 'BLOCKED' not in result and account_key != 'ang':
            success = await tracker_manager.send_webhook(position_key, real_amt, side, current_price, reduce_qty, True, reason)
        if success:
            tracker_manager.registry.release_hedge_slot(account_key, symbol)

        if override_qty is None or not override_qty:
            override_qty = real_amt if 'CLOSE' in action else real_amt - pos_min_qty
        tracker_manager.restored_positions.discard(position_key)
    else:
        side = 'BUY' if is_long else 'SELL'
    if not already_locked: await tracker_manager.set_processing(position_key)

    try:
        reason = f'QUICK_{reason}'
        action = f'QUICK_{action}'
        result = None
        if 'WAIT' in reason:
            return f'{position_key} BLOCKED_WAIT MEANS WAIT 6717'
        # EXECUTE
        if override_qty is not None and override_qty > 0 and (position_key in tracker_manager.tradeable_position_keys.get(account_key, set()) or 'HEDGE' in reason):
            qty = override_qty
            result = await trade_manager.execute_now(position_key, account_key, symbol, real_amt, side, position_side, override_qty, current_price, unique_id, reason, False, action, is_hedge=is_hedge, hedge_for=hedge_for)

        elif 'REDUCE' in reason or 'REDUCE' in action or 'CLOSE' in action or 'EMEERGENCY' in reason:
            result = await trade_manager.execute_now(position_key, account_key, symbol, real_amt, side, position_side, max(override_qty,qty) , current_price, unique_id, reason, False, action, is_hedge=is_hedge, hedge_for=hedge_for)
        else:
            if position_key in tracker_manager.tradeable_position_keys.get(account_key, set()) or account_key=='flz' or 'HEDGE' in reason.upper():
                result = await trade_manager.execute_trade_action(account_key, position_key, symbol, qty, current_price, side, position_side, unique_id, False, action, reason, override_qty=None, is_hedge=is_hedge, hedge_for=hedge_for)

        success = 'SUCCESS' in str(result).upper()

        if result and 'BLOCK' in result: 
            return False, result    
        if not success:
            success = await verify_trade_via_websocket(trade_manager, account_key, position_key, qty, is_long, timeout_seconds=9, action=action)
            if result and not success and 'BLOCK' not in result:
                success = await tracker_manager.send_webhook(position_key, real_amt, side, current_price, qty, False, reason)


        if success:
            if 'AUGMENT' in action or 'OPEN' in action:
                new_total = real_amt + qty
                await tracker_manager.transition_to_exit(account_key, position_key, current_price, new_total, status='active', is_hedge=is_hedge, hedge_for=hedge_for)

                # --- THE BREAKOUT TIGHT STOP INJECTION ---
                if "BREAKOUT_PLAY" in reason or "MOMENTUM_SCALP" in reason:
                    if not hasattr(trade_manager, 'strict_close_positions'):
                        trade_manager.strict_close_positions = {}
                    trade_manager.strict_close_positions[position_key] = {
                        'augmented_at': datetime.now(timezone.utc),
                        'augment_price': current_price,
                        'strict_loss_threshold': -0.4,   # micro-stop: Cut at -0.4% loss
                        'strict_gain_threshold': 1.0,    # Take quick profit if it stalls
                        'strict_reduction_trigger': True }
                    
            elif action in ['REDUCE', 'PROFIT_TAKE', 'CLOSE']:
                tracker_manager.registry.release_hedge_slot(account_key, symbol)
                await tracker_manager.transition_to_entry(account_key, position_key, current_price, qty, is_long, status='WAS_CLOSED')
                if is_hedge:
                    async with tracker_manager._hedges_lock:
                        tracker_manager.active_hedges = [h for h in tracker_manager.active_hedges if h.get('position_key') != position_key]
                else:
                    # Clean up associated hedges when original closes
                    async with tracker_manager._hedges_lock:
                        hedges_to_close = [h.copy() for h in tracker_manager.active_hedges if h.get('losing_position_key') == position_key]
                    for h_record in hedges_to_close:
                        h_key = h_record.get('position_key')
                        h_qty = h_record.get('quantity', 0.0)
                        logger.info(f"🛡️ [HEDGE_CLEANUP] Parent {position_key} closed. Triggering close of hedge {h_key}")
                        asyncio.create_task(execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, h_key, h_qty, 'CLOSE', current_price, 0.0, f"HEDGE_CLEANUP_FOR_{position_key}", is_hedge=True, data_manager=data_manager))

            # Record Events & Save
            await record_trade_event(tracker_manager, account_key, position_key, symbol, action, position_side, current_price, qty, reason, False, is_hedge=is_hedge, hedge_for=hedge_for)
            await tracker_manager.save_tracker(account_key, force=True)
            async with tracker_manager._timing_lock:
                tracker_manager.last_check_times[position_key] = time.time()
            return True, "SUCCESS"

            
        else:
            if result and 'SUCCESS' not in result:
                logger.warning(f'⚠️ [EX ECUTE_FAILED] {position_key} {action} {result} - {reason}')

            if 'OPEN' in action and not is_hedge:
                await tracker_manager.revert_optimistic_exit(account_key, position_key)
            return False, f"EXECUTION_FAILED: {result}"

    except Exception as e:
        logger.error(f"⚠️[EXECUTE_WRAPPER] {position_key} CRITICAL ERROR: {e}", exc_info=True)
        if action == 'OPEN' and not is_hedge:
            await tracker_manager.revert_optimistic_exit(account_key, position_key)
        return False, str(e)
    finally:
        if not already_locked:
            await tracker_manager.clear_processing(position_key)

async def track_scalping_performance(account_key: str, position_key: str, action: str, price: float, quantity: float, reason: str):
    if 'SCALP' not in reason.upper():
        return
    symbol = position_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
    log_dir = Path.home() / "logs" / "scalping"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"scalping_{account_key}.csv"
    if not log_file.exists():
        with open(log_file, 'w') as f:
            f.write("timestamp,symbol,action,price,quantity,reason\n")
    with open(log_file, 'a') as f:
        timestamp = datetime.now(timezone.utc).isoformat()
        f.write(f"{timestamp},{symbol},{action},{price:.6f},{quantity:.6f},{reason}\n")
    logger.debug(f"[SCALP_TRACK] Recorded {action} for {symbol} at {price:.4f}, qty: {quantity:.6f}")      

async def _get_tracker_snapshot(tracker_manager: TrackerManager, position_key: str):
    async with tracker_manager._exit_candidates_lock:
        if position_key in tracker_manager.exit_candidates:
            return 'EXIT', dict(tracker_manager.exit_candidates[position_key])
    async with tracker_manager._entry_candidates_lock:
        if position_key in tracker_manager.entry_candidates:
            return 'ENT', dict(tracker_manager.entry_candidates[position_key])
    return 'ENT', {}

async def log_stoch_snapshot(account_key: str, trade_manager, context_label: str, position_key: str, current_price: float, metrics: Dict[str, float], indicators: Optional[Dict[str, Any]], tracker_manager, score: float, rec: str, reason: str, header_state: Dict[str, bool], header_lock, acc_logger: Optional[logging.Logger] = None):
    try:
        # 1. Setup & Throttling
        if score is None: score = 0.0
        now_ts = time.time()
        is_long = "LONG" in position_key
        
        # Action Logic
        action_kw = ["BUY", "SELL", "CLOSE", "OPEN", "AUGMENT", "REDUCE", "BLOCKED", "REENTRY"]
        is_action = any(x in rec for x in action_kw)

        # 2. Position Data Extraction
        status, data = await _get_tracker_snapshot(tracker_manager, position_key)
        if not data: data = {}
        positionAmt = safe_fetch_float(data.get("positionAmt"), 0.0)
        
        # Throttle logic
        throttle_key = f"{account_key}:{position_key}:{context_label}"
        last_log_time = _log_throttle.get(throttle_key, 0.0)
        last_check_ts_temp = tracker_manager.get_last_check_time(position_key)
        is_zombie_temp = (now_ts - last_check_ts_temp) > 300 if last_check_ts_temp > 0 else False
        
        if not is_action and not is_zombie_temp and (now_ts - last_log_time < 60.0): 
            if last_check_ts_temp > 0 and (now_ts - last_check_ts_temp) >= 5.0:
                 tracker_manager.register_check(position_key)
            return
        _log_throttle[throttle_key] = now_ts

        # 4. Extract Data safely
        if not indicators: indicators = {}
        if not metrics: metrics = {}
        
        def _g(src, k, d=50.0): return safe_fetch_float(src.get(k, d), d)

        k_1m = _g(metrics, "k_1m"); k_1m_prev = _g(metrics, "k_1m_prev"); d_1m = _g(metrics, "d_1m")
        k_3m = _g(metrics, "k_3m"); k_3m_prev = _g(metrics, "k_3m_prev"); d_3m = _g(metrics, "d_3m")
        k_15m = _g(indicators, "stoch_k_15m"); d_15m = _g(indicators, "stoch_d_15m")
        k_1h = _g(indicators, "stoch_k_1h"); d_1h = _g(indicators, "stoch_d_1h")
        k_4h = _g(indicators, "stoch_k_4h"); d_4h = _g(indicators, "stoch_d_4h")
        k_D = _g(indicators, "stoch_k_D"); d_D = _g(indicators, "stoch_d_D")

        def get_weather(curr, prev=None, d=None):
            if prev is not None: return "☀️" if curr > prev else "☁️" if curr < prev else "-"
            if d is not None: return "☀️" if curr > d else "☁️" if curr < d else "-"
            return "•"
            
        w_str = f"[{get_weather(k_1m, prev=k_1m_prev)}{get_weather(k_3m, prev=k_3m_prev)}{get_weather(k_15m, d=d_15m)}{get_weather(k_1h, d=d_1h)}{get_weather(k_4h, d=d_4h)}{get_weather(k_D, d=d_D)}]"

        exact_tick_ts = metrics.get("_tick_ts", 0)
        ts_raw = metrics.get("timestamp_3m") or indicators.get("timestamp_3m") 
        lag_disp1 = "UNK"
        lag_disp = "UNK"
        if exact_tick_ts:
            m_obj = safe_datetime(exact_tick_ts)
            if m_obj:
                true_lag1 = now_ts - m_obj.timestamp()
                if true_lag1 < 2.0: lag_disp1 = f"{true_lag1:.1f}s"
                elif true_lag1 < 20.0: lag_disp1 = f"{true_lag1:.0f}s"
                else: lag_disp1 = f"❌{true_lag1:.0f}s"      
        if ts_raw:
            dt_obj = safe_datetime(ts_raw)
            if dt_obj:
                true_lag = now_ts - dt_obj.timestamp()
                if true_lag < 60.0: lag_disp = f"{true_lag:.1f}s"
                elif true_lag < 180.0: lag_disp = f"{true_lag:.0f}s"
                else: lag_disp = f"❌{true_lag:.0f}s"

        last_check_ts = tracker_manager.get_last_check_time(position_key)
        gap_disp = "INIT"
        is_zombie = False
        if last_check_ts > 0:
            gap_sec = now_ts - last_check_ts
            is_zombie = gap_sec > 300
            if gap_sec < 5.0 and not is_action: return
            if gap_sec < 60.0: gap_disp = f"{gap_sec:.1f}s"
            elif gap_sec < 3600: gap_disp = f"{gap_sec/60:.1f}m"
            else: gap_disp = f"{gap_sec/3600:.1f}h"
            if gap_sec > 120: gap_disp = f"💀{gap_disp}"
        tracker_manager.register_check(position_key)

        avg_entry = safe_fetch_float(data.get("average_entry_price", data.get("entry_price", 0.0)), 0.0)
        upnl_disp = "        "
        if avg_entry > 0 and current_price > 0:
            upnl_pct = ((current_price - avg_entry) / avg_entry) * 100.0 if is_long else ((avg_entry - current_price) / avg_entry) * 100.0
            if abs(upnl_pct) > 0.01: upnl_disp = f"{upnl_pct:>+7.2f}%"

        def _fmt_s(k, d): return f"{int(k):>2}/{int(d):<2}"
        i1 = _fmt_s(k_1m, d_1m); i3 = _fmt_s(k_3m, d_3m); i15 = _fmt_s(k_15m, d_15m)
        sc_val = int(score)
        
        try: sn_val = int(indicators.get("0market_sentiment_local", 0))
        except: sn_val = 0
        sn_disp = f"Sn:{sn_val:<3}"
        posAmt_str = f"${int(positionAmt * current_price):<8}" if positionAmt != 0 else "     "

        rec_clean = rec.replace(position_key, "").strip()
        for char in ["🚀", "🟢", "🔴", "💥", "❌", "⏳", "▫️"]:
            rec_clean = rec_clean.replace(char, "")
        rec_clean = rec_clean.strip()[:13]

        icon = "⏳"
        if "WEAK" in rec or "WEAK" in reason: icon = "▫️ "
        elif (("BUY" in rec and is_long) or ("SELL" in rec and not is_long)) or "OPEN" in rec or "AUGMENT" in rec: 
            icon = "🚀" if sc_val >= 28 else "🟢"
        elif "CLOSE" in rec or "REDUCE" in rec or "EMERGENCY" in rec: 
            icon = "💥" if sc_val <= -7 else "🔴"
        elif "BLOCKED" in rec: icon = "✋"

        prefix_icon = "💀" if is_zombie else ("🔵" if positionAmt == 0 else "🔴")
        reason_str = (f"Sc:{sc_val} {reason}" if reason else f"Sc:{sc_val}")[:50]

        log_line = (
            f"{icon}  {rec_clean:<14} {prefix_icon} {position_key:<19} {w_str} | "
            f"{current_price:<8.4f} | "
            f"1m:{i1:<6} 3m:{i3:<6} 15m:{i15:<6} | "
            f"{sn_disp} | "
            f"{upnl_disp} | Age1m:{lag_disp1:<7} | Age3m:{lag_disp:<7} | Gap:{gap_disp:<6} | {reason_str:<25} | {posAmt_str} "
        )
        
        is_wait = "WAIT" in rec or "⏳" in icon
        if is_wait:
            get_wait_logger(account_key).info(log_line)
        else:
            get_account_logger(account_key).info(log_line)
    except Exception as e:
        print(f"Error logging snapshot: {e}")

# async def log_stoch_snapshot(account_key: str, trade_manager: MultiAccountTradeManager, context_label: str, position_key: str, current_price: float,  metrics: Dict[str, float], indicators: Optional[Dict[str, Any]],  tracker_manager: TrackerManager, score: float, rec: str, reason: str,  header_state: Dict[str, bool], header_lock: DummyLock,  acc_logger: Optional[logging.Logger] = None):
#     is_action = "BUY" in rec or "SELL" in rec or "CLOSE" in rec or "OPEN" in rec or "AUGMENT" in rec or "REDUCE" in rec
#     is_long = "LONG" in position_key
#     if score is None: score = 0
#     now_ts = time.time()
#     status, data = await _get_tracker_snapshot(tracker_manager, position_key)
#     positionAmt=safe_fetch_float(data.get('positionAmt'), 0.0)
#     last_check_ts = tracker_manager.get_last_check_time(position_key)
#     if last_check_ts > 0:
#         gap_sec = now_ts - last_check_ts
#         if gap_sec < 5.0: return #gap_disp = f"{gap_sec*1000:.0f}ms"
#         elif gap_sec < 60.0: gap_disp = f"{gap_sec:.1f}s"
#         elif gap_sec < 3600: gap_disp = f"{gap_sec/60:.1f}m"
#         else: gap_disp = f"{gap_sec/3600:.1f}h"
#         if gap_sec > 120: gap_disp = f"💀{gap_disp}" # Zombie warning
#     else:
#         gap_disp = "INIT"
#     is_zombie = (last_check_ts > 0 and (now_ts - last_check_ts) > 300)
#     result=None
    
#     # Throttling (Skip if boring)
#     throttle_key = f"{account_key}:{position_key}:{context_label}"
#     last_log_time = _log_throttle.get(throttle_key, 0.0)
#     if not is_action and not is_zombie and (now_ts - last_log_time < 120.0): return
#     _log_throttle[throttle_key] = now_ts
    
#     # 4. PnL
#     avg_entry = safe_fetch_float(data.get('average_entry_price', data.get('entry_price', 0.0)), 0.0)
#     upnl_pct = 0.0
#     if status == 'EXIT' and avg_entry > 0:
#         if position_key.endswith('_LONG'): 
#             upnl_pct = ((current_price - avg_entry) / avg_entry) * 100.0
#         else: 
#             upnl_pct = ((avg_entry - current_price) / avg_entry) * 100.0
            
#     if abs(upnl_pct) < 0.01: upnl_disp = "        "
#     else: upnl_disp = f"{upnl_pct:>+7.2f}%"

#     # 5. Extract Values
#     if not indicators: indicators = {}
#     k_1m = safe_fetch_float(metrics.get('k_1m', 50.0), 50.0); k_1m_prev = safe_fetch_float(metrics.get('k_1m_prev', 50.0), 50.0);  d_1m = safe_fetch_float(metrics.get('d_1m', 50.0), 50.0)
#     k_3m = safe_fetch_float(metrics.get('k_3m', 50.0), 50.0); k_3m_prev = safe_fetch_float(metrics.get('k_3m_prev', 50.0), 50.0); d_3m = safe_fetch_float(metrics.get('d_3m', 50.0), 50.0)
#     k_15m = safe_fetch_float(indicators.get('stoch_k_15m'), 50.0); d_15m = safe_fetch_float(indicators.get('stoch_d_15m'), 50.0)
#     k_1h = safe_fetch_float(indicators.get('stoch_k_1h'), 50.0); d_1h = safe_fetch_float(indicators.get('stoch_d_1h'), 50.0)
#     k_4h = safe_fetch_float(indicators.get('stoch_k_4h'), 50.0); d_4h = safe_fetch_float(indicators.get('stoch_d_4h'), 50.0)
#     k_D = safe_fetch_float(indicators.get('stoch_k_D'), 50.0); d_D = safe_fetch_float(indicators.get('stoch_d_D'), 50.0)
#     def get_weather(curr, prev =None, d=None):
#         if prev is not None: return "☀️" if curr > prev else "☁️" if curr < prev else '-'
#         if d is not None: return "☀️" if curr > d else "☁️" if curr < d else '-'
#         return "•"
#     w1 = get_weather(k_1m, prev =k_1m_prev)
#     w3 = get_weather(k_3m, prev =k_3m_prev)
#     w15 = get_weather(k_15m, d=d_15m)
#     w1h = get_weather(k_1h, d=d_1h)
#     w4h = get_weather(k_4h, d=d_4h)
#     wD = get_weather(k_D, d=d_D)
#     weather_str = f"[{w1}{w3}{w15}{w1h}{w4h}{wD}]"

#     exact_tick_ts = metrics.get('_tick_ts', 0)
#     if exact_tick_ts > 0:
#         true_lag = now_ts - exact_tick_ts
#         if true_lag < 1.0: lag_disp = f"⚡{true_lag*1000:.0f}ms"
#         elif true_lag < 5.0: lag_disp = f"{true_lag:.1f}s"
#         else: lag_disp = f"❌{true_lag:.0f}s"
#     else:
#         lag_disp = "UNK"

#     # 8. Formatting
#     def _fmt_s(k, d): 
#         if int(k)==50 and int(d)==50: return "     "
#         return f"{int(k)}/{int(d)}"
        
#     i1 = _fmt_s(k_1m, d_1m)
#     i3 = _fmt_s(k_3m, d_3m)
#     i15 = _fmt_s(k_15m, d_15m)
#     score=safe_fetch_float(score)
#     sc_val = int(score)
#     sc_disp = f"Sc:{sc_val:<2}" if sc_val != 0 else "     "
#     sn_val = i.get('market_sentiment_local', 0.0)
#     sn_disp = f"Sc:{sn_val:<2}" if sn_val != 0 else "     "
#     icon = ""
#     if (("BUY" in rec and is_long) or ("SELL" in rec and not is_long)) or "OPEN" in rec or "AUGMENT" in rec: 
#         icon = "🚀" if score >= 25 else "🟢"
#     elif "CLOSE" in rec or "REDUCE" in rec: 
#         icon = "❌" if score <= -7 else "🔴" 
#     if "WEAK" in rec: icon = "▫️"
#         # if position.positionAmt > 0:  await check_exit_candidates_for_account(tracker_manager.trade_manager, account_key, None, tracker_manager,  None, tracker_manager.data_manager, tracker_manager.hedge_engine, position_keys=[position_key]  )
#     rec_disp = f"{icon}{rec}"[:13]
    
#     reason_str = reason or '-'

#     prefix_icon = "💀" if is_zombie else ("🔵" if positionAmt == 0 else "🔴")

#     # Columnar Layout
#     # Icon Key [W] Price | 1m | 3m | 15m | Score | Rec | PnL | Age | Gap | Reason
#     # log_line = (
#     #     f"{prefix_icon} {position_key:<24} {weather_str} | {current_price:<8.4f} | "
#     #     f"1m:{i1:<7} 3m:{i3:<7} 15m:{i15:<7} | {sc_disp} | {rec_disp:<13} | "
#     #     f"{upnl_disp} | Age:{lag_disp:<7} | Gap:{gap_disp:<6} | {reason_str} | {score}")
#     log_line=""
#     if config.VERBOSE2 and ('⏳' in icon or  'WAIT'  in rec_disp): 
#         log_line = (f"{rec_disp:<13} {prefix_icon} {position_key:<24} {weather_str} | {current_price:<8.4f} | "
#             f"1m:{i1:<7} 3m:{i3:<7} 15m:{i15:<7} | sc:{sc_disp} | sn:{sn_disp} | "
#             f"{upnl_disp} | Age:{lag_disp:<7} | Gap:{gap_disp:<6} | {reason_str} | {positionAmt}")
#     elif '⏳' not in icon and 'WAIT' not in rec_disp:
#         log_line = ( f"{rec_disp:<13} {prefix_icon} {position_key:<24} {weather_str} | {current_price:<8.4f} | "
#             f"1m:{i1:<7} 3m:{i3:<7} 15m:{i15:<7} | sc:{sc_disp} | sn:{sn_disp} | "
#             f"{upnl_disp} | Age:{lag_disp:<7} | Gap:{gap_disp:<6} | {reason_str} | {positionAmt}")
#     if log_line: print(log_line)
#     if acc_logger and log_line: acc_logger.info(log_line)

# async def log_stoc h_snapshot(account_key: str, context_label: str, position_key: str, current_price: float,  metrics: Dict[str, float], indicators: Optional[Dict[str, Any]],  tracker_manager: TrackerManager, score: float, rec: str, reason: str,  header_state: Dict[str, bool], header_lock: DummyLock,  acc_logger: Optional[logging.Logger] = None):
#     is_action = "BUY" in rec or "SELL" in rec or "CLOSE" in rec or "OPEN" in rec or "AUGMENT" in rec or "REDUCE" in rec
#     if score is None: score = 0
#     now_ts = time.time()
#     status, data = await _get_tracker_snapshot(tracker_manager, position_key)
#     last_check_ts = tracker_manager.get_last_check_time(position_key)
    
#     if last_check_ts > 0:
#         visit_gap = now_ts - last_check_ts
#         if visit_gap > 3600: last_disp = f"{visit_gap/3600:.1f}h"
#         elif visit_gap > 60: last_disp = f"{visit_gap/60:.1f}m"
#         else: last_disp = f"{visit_gap:.1f}s"
#         if visit_gap > 180: last_disp = f"💀{last_disp}"
#     else:
#         last_disp = "?"
    
#     is_zombie = (last_check_ts > 0 and (now_ts - last_check_ts) > 300)
    
#     throttle_key = f"{account_key}:{position_key}:{context_label}"
#     last_log_time = _log_throttle.get(throttle_key, 0.0)

#     # Throttling
#     if not is_action and not is_zombie and (now_ts - last_log_time < 60.0): return
#     _log_throttle[throttle_key] = now_ts
    
#     avg_entry = safe_fetch_float(data.get('average_entry_price', data.get('entry_price', 0.0)), 0.0)
#     upnl_pct = 0.0
#     if status == 'EXIT' and avg_entry > 0:
#         if position_key.endswith('_LONG'): 
#             upnl_pct = ((current_price - avg_entry) / avg_entry) * 100.0
#         else: 
#             upnl_pct = ((avg_entry - current_price) / avg_entry) * 100.0

#     if not indicators: indicators = {}

#     k_1m = safe_fetch_float(metrics.get('k_1m', 50.0), 50.0)
#     k_1m_prev = safe_fetch_float(metrics.get('k_1m_prev', k_1m), k_1m)
#     d_1m = safe_fetch_float(metrics.get('d_1m', 50.0), 50.0)
    
#     k_3m = safe_fetch_float(metrics.get('k_3m', 50.0), 50.0)
#     k_3m_prev = safe_fetch_float(metrics.get('k_3m_prev', k_3m), k_3m)
#     d_3m = safe_fetch_float(metrics.get('d_3m', 50.0), 50.0) 

#     k_15m = safe_fetch_float(indicators.get('stoch_k_15m'), 50.0)
#     d_15m = safe_fetch_float(indicators.get('stoch_d_15m'), 50.0)
    
#     k_1h = safe_fetch_float(indicators.get('stoch_k_1h'), 50.0)
#     d_1h = safe_fetch_float(indicators.get('stoch_d_1h'), 50.0)

#     k_4h = safe_fetch_float(indicators.get('stoch_k_4h'), 50.0)
#     d_4h = safe_fetch_float(indicators.get('stoch_d_4h'), 50.0)

#     # --- WEATHER LOGIC ---
#     def get_weather(curr, prev =None, d=None):
#         if prev is not None: return "☀️" if curr > prev else "☁️" if curr < prev else '-'
#         if d is not None: return "☀️" if curr > d else "☁️" if curr < d else '-'
#         return "•"
#     w1 = get_weather(k_1m, prev =k_1m_prev)
#     w3 = get_weather(k_3m, prev =k_3m_prev) 
#     w15 = get_weather(k_15m, d=d_15m)
#     w1h = get_weather(k_1h, d=d_1h)
#     w4h = get_weather(k_4h, d=d_4h)
   
#     weather_str = f"[{w1}{w3}{w15}{w1h}{w4h}]"
#     exact_tick_ts = metrics.get('_tick_ts', 0)
#     stream_warning = "" 
    
#     if exact_tick_ts > 0:
#         true_lag = now_ts - exact_tick_ts
#         if true_lag < 1.0: lag_disp = f"⚡{true_lag*1000:.0f}ms"
#         elif true_lag < 5.0: lag_disp = f"{true_lag:.1f}s"
#         elif true_lag < 15.0: lag_disp = f"⚠️{true_lag:.1f}s"
#         else: lag_disp = f"❌{true_lag:.0f}s"; stream_warning = "⚠️NO_STREAM"
#     else:
#         lag_disp = "UNK"; stream_warning = "⚠️NO_DATA"

#     htf_warnings = []
#     now_dt_check = datetime.now(timezone.utc)
#     def _check_lag(key, seconds_limit, tag):
#         val = indicators.get(key)
#         if val:
#             try:
#                 if isinstance(val, str): ts = isoparse(val)
#                 elif isinstance(val, (int, float)): ts = datetime.fromtimestamp(val, tz=timezone.utc)
#                 else: return
#                 if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
#                 delta = (now_dt_check - ts).total_seconds()
#                 if delta > seconds_limit: htf_warnings.append(f"{tag}({int(delta/60)}m)")
#             except: pass
#     _check_lag('timestamp_15m', 1800, '15m')
#     _check_lag('timestamp_1h', 5400, '1h') 
#     _check_lag('timestamp_4h', 18000, '4h')

#     ts3 = metrics.get('ts3', 0.0)
#     if ts3 > 0:
#         age_3m = max(0, now_ts - ts3)
#         if age_3m < 240: time_disp = f"{age_3m:.0f}s"
#         elif age_3m < 480: time_disp = f"⚠️{age_3m:.0f}s"
#         else: time_disp = f"❌{age_3m:.0f}s"
#     else: time_disp = "N/A"

#     # --- SUPPRESSION FORMATTING ---
#     def _fmt_s(k, d):
#         ki, di = int(k), int(d)
#         # Suppress 50/50 (neutral) or 0/0 (init) to spaces
#         if (ki == 50 and di == 50) or (ki == 0 and di == 0):
#             return "       " 
#         return f"{ki}/{di}"

#     i1 = _fmt_s(k_1m, d_1m)
#     i15 = _fmt_s(k_15m, d_15m)

#     # Score Suppression
#     sc_val = int(score)
#     if sc_val == 0:
#         sc_disp = "     " # Blank if 0
#     else:
#         sc_disp = f"Sc:{sc_val:<2}"

#     # PnL Suppression
#     if abs(upnl_pct) < 0.01:
#         upnl_disp = "        " # Blank if 0.00%
#     else:
#         upnl_disp = f"{upnl_pct:>+7.2f}%"

#     # Rec Display
#     icon = ""
#     if "BUY" in rec or "OPEN" in rec or "AUGMENT" in rec:
#         icon = "🚀" if score >= 8 else "🟢"
#     elif "SELL" in rec or "CLOSE" in rec or "REDUCE" in rec:
#         icon = "" if score >= 8 else "🔴"
#     if stream_warning: icon = "🔌"
#     rec_disp = f"{icon}{rec}"[:13]
    
#     # Reason
#     reason_str = reason or '-'
#     if htf_warnings: reason_str = f"⚠️STALE:[{','.join(htf_warnings)}] " + reason_str

#     if is_zombie: prefix_icon = "💀"
#     elif context_label == "ENTRY": prefix_icon = "🔵" 
#     else: prefix_icon = "🔴"

#     # Construct Line
#     log_line = (
#         f"{prefix_icon} {position_key:<24} {weather_str} | {current_price:<8.4f} | "
#         f"1m:{i1:<7} 15m:{i15:<7} | {sc_disp} | {rec_disp:<13} | "
#         f"{upnl_disp} | Age1:{lag_disp:<5} | Age3:{time_disp:<5} | Gap:{last_disp:<6} | {reason_str}" )

#     print(log_line)
#     log_target = acc_logger if acc_logger else logger
#     log_target.info(log_line)

async def check_exit_candidates_for_account(trade_manager, account_key: str, redis_manager, tracker_manager: TrackerManager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine=None, position_keys: List[str] = None, force: bool = False) -> None:
    if not position_keys: return
    sem = asyncio.Semaphore(250)    
    async def process_single_exit(position_key):
        async with sem:
            try:
                # 1. THROTTLING
                # if not force:
                if trade_manager._allowed_accounts and account_key not in trade_manager._allowed_accounts: return                
                is_lagging = tracker_manager.is_system_lagging(account_key)
                throttle_threshold = 15.0 if is_lagging else 5.0
                last_check = tracker_manager.get_last_check_time(position_key)
                if last_check > 0 and (time.time() - last_check) < throttle_threshold: return
                if await tracker_manager.is_processing(position_key):
                    if (time.time() - tracker_manager._processing_orders.get(position_key, 0)) > 30:
                        await tracker_manager.clear_processing(position_key)
                    else: return

                # 2. DATA
                ak, symbol, position_side = parse_position_key(position_key)
                metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_data_fresh = await data_manager.get_hot_state(symbol)
                now_ts = time.time()
                exact_tick_ts= metrics.get('_tick_ts')
                true_lag = now_ts - exact_tick_ts
                acc_logger = get_account_logger(account_key)
                dummy_state = {}; dummy_lock = DummyLock()
                position = await tracker_manager.get_position(position_key)
                if not position: position = tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(position_key)
                is_long = (position_side == 'LONG')
                k_1m=safe_fetch_float(metrics.get('stoch_k_1m', 50.0)); k_1m_prev =safe_fetch_float(metrics.get('k_1m_prev', 50.0)); d_1m=safe_fetch_float(metrics.get('stoch_d_1m', 50.0));k_3m=safe_fetch_float(metrics.get('stoch_k_3m', 50.0)); k_3m_prev =safe_fetch_float(metrics.get('k_3m_prev', 50.0)); d_3m=safe_fetch_float(metrics.get('stoch_d_3m', 50.0));k_15m=safe_fetch_float(indicators.get('stoch_k_15m', 50.0));  d_15m=safe_fetch_float(indicators.get('stoch_d_15m', 50.0))
                k_1h=safe_fetch_float(indicators.get('stoch_k_1h', 50.0)); k_1h_prev =safe_fetch_float(indicators.get('k_1h_prev', 50.0)); d_1h=safe_fetch_float(indicators.get('stoch_d_1h', 50.0))
                dc_high_3m = safe_fetch_float(indicators.get('dc_high_3m'), 0.0)
                dc_high_15m = safe_fetch_float(indicators.get('dc_high_15m'), 0.0)
                dc_high_1h = safe_fetch_float(indicators.get('dc_high_1h'), 0.0)
                dc_high_4h = safe_fetch_float(indicators.get('dc_high_4h'), 0.0)
                dc_high_D = safe_fetch_float(indicators.get('dc_high_D'), 0.0)

                dc_low_3m = safe_fetch_float(indicators.get('dc_low_3m'), 0.0)
                dc_low_15m = safe_fetch_float(indicators.get('dc_low_15m'), 0.0)
                dc_low_1h = safe_fetch_float(indicators.get('dc_low_1h'), 0.0)
                
                dc_low_4h = safe_fetch_float(indicators.get('dc_low_4h'), 0.0)
                dc_low_D = safe_fetch_float(indicators.get('dc_low_D'), 0.0)

                ha_3m=safe_fetch_float(indicators.get('ha_3m', 50.0)); ha_15m =safe_fetch_float(indicators.get('ha_15m', 50.0)); 
                hard_exit_reason = None
                is_blind = (k_3m == 50.0 and d_3m == 50.0) or (k_1m == 50.0 and d_1m == 50.0)
                if is_blind:

                    if position:
                        curr_g = safe_fetch_float(getattr(position, 'gain', 0.0))
                        prev_g = safe_fetch_float(getattr(position, 'prev_gain', curr_g))
                        if not (curr_g < prev_g):
                            return
                current_price = safe_fetch_float(indicators.get('current_price', 0.0))
                if not is_data_fresh or current_price <= 0:
                    current_price, _ = await data_manager.get_fresh_price(symbol)
                    indicators['current_price'] = current_price
                if current_price <= 0: current_price, _ = await get_current_price(symbol)
                pos_min = 2 * config.MIN_POSITION_SIZE / current_price
                pos_min_qty = max(pos_min, trade_manager.min_qty.get(symbol, 0.0))
                exit_cand = tracker_manager.exit_candidates.get(position_key, {})
                hedge_for = exit_cand.get('losing_position_key', None) if isinstance(exit_cand, dict) else None
                if position.positionAmt <=  0: # TEMP 0
                    await tracker_manager.clear_processing(position_key)
                    await tracker_manager.transition_to_entry(account_key, position_key, current_price, 0.0, is_long, status="REDUCED->ENTRY_CANDIDATE")
                    await tracker_manager.register_check(position_key)
                    await tracker_manager.save_tracker(account_key, force=True)
                    await check_entry_candidates_for_account(trade_manager, account_key, redis_manager, tracker_manager, order_queue, data_manager, hedge_engine, position_keys=[position_key])
                    return

                await tracker_manager.register_check(position_key)

                # if position_key=='ang:ROSEUSDT_LONG': logger.warning(f'ROSE2 {position.positionAmt} 1:{k_1m}/{d_1m} 3:{k_3m}/{d_3m} 15:{k_15m}/{d_15m}')

                if not position: position = await tracker_manager.get_position(position_key)
                if not position: 
                    logger.warning(f'{position_key} position LOST WTF')
                    
                    return # Cannot process missing position
                positionAmt = position.positionAmt
                start_size_usd = safe_fetch_float(getattr(config, 'MIN_POSITION_SIZE', 45.0), 45.0)
                position_value = position.positionAmt * current_price
                is_tradeable = position_key in tracker_manager.tradeable_keys
                if position_value < start_size_usd and is_tradeable:
                    await tracker_manager.clear_processing(position_key)
                    await tracker_manager.transition_to_entry(account_key, position_key, current_price, 0.0, is_long, status="SMALL_TRADEABLE->ENTRY_CANDIDATE")
                    await tracker_manager.register_check(position_key)
                    await tracker_manager.save_tracker(account_key, force=True)
                    await check_entry_candidates_for_account(trade_manager, account_key, redis_manager, tracker_manager, order_queue, data_manager, hedge_engine, position_keys=[position_key])
                    return
                
                await tracker_manager.register_check(position_key)


                # 4. DUST CHECK (Moved here, simplified)
                # pos_min_qty = max(2 * config.MIN_POSITION_SIZE / current_price, trade_manager.min_qty.get(symbol, 0.0) * 1.2)
                # positionAmt = abs(safe_fetch_float(getattr(position, "positionAmt", 0.0), 0.0))
                
                # if positionAmt < 1.2 * pos_min_qty:
                #     async with tracker_manager._exit_candidates_lock:
                #         cand = tracker_manager.exit_candidates.get(position_key, {})
                #         if cand.get("status") == "PENDING_OPEN":
                #             ts_raw = cand.get("timestamp")
                #             ts_dt = datetime.now(timezone.utc) 
                #             if isinstance(ts_raw, str):
                #                 try:
                #                     from dateutil.parser import isoparse
                #                     ts_dt = isoparse(ts_raw)
                #                 except: pass
                #             elif isinstance(ts_raw, datetime): ts_dt = ts_raw
                #             if ts_dt.tzinfo is None:  ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                #             if (datetime.now(timezone.utc) - ts_dt).total_seconds() < 20:  return
                #     await tracker_manager.transition_to_entry(account_key, position_key, current_price, 0.0, is_long, status="TOO_SMALL->ENTRY_CANDIDATE")
                #     return
                score_exit, rec_exit, reason_exit=0,None,None
                # 6. SCALPING CONFIG
                scalping_mode = getattr(config, "SCALP_MODE", False)
                scalping_accounts = getattr(config, "SCALP_ACCOUNTS", [])
                should_scalp = scalping_mode and account_key in scalping_accounts
                if should_scalp and not is_data_fresh: should_scalp = False
                # --- 6. EXIT LOGIC OVERHAUL (Harmonized with Service) ---
                current_gain = safe_fetch_float(getattr(position, 'gain', 0.0))
                prev_gain = safe_fetch_float(getattr(position, 'prev_gain', current_gain))
                max_gain = safe_fetch_float(getattr(position, 'max_gain', current_gain))
                if current_gain > max_gain:
                    position.max_gain = current_gain
                    max_gain = current_gain
                
                # a) Hedge Liability Guard
                is_hedge = False
                async with tracker_manager._hedges_lock:
                    for h in tracker_manager.active_hedges:
                        if h.get('position_key') == position_key:
                            is_hedge = True
                            break
                if not is_hedge:
                    cand = await tracker_manager.get_exit_candidate(position_key)
                    if cand and cand.get('is_hedge', False): is_hedge = True

                if is_hedge:
                    if current_gain < -0.1: # Tighter liability kill
                         hard_exit_reason = f"HEDGE_LIABILITY_KILL_{current_gain:.2f}%"
                         if hedge_for:
                             tracker_manager.hedge_liability_cooldowns[hedge_for] = time.time()
                    elif current_gain < prev_gain and current_gain < 0.3:
                         # Roll over in small profit? Get out.
                         hard_exit_reason = f"HEDGE_ROLLOVER_PROFIT_PROTECT_{current_gain:.2f}%"

                # b) Aggressive Profit Protection (Non-Hedges)
                if not hard_exit_reason and current_gain > 0.01:
                    erosion = max_gain - current_gain
                    mom_exhausted = (is_long and (k_1m < d_1m or ha_3m == 'red')) or (not is_long and (k_1m > d_1m or ha_3m == 'green'))
                    
                    if (max_gain > 0.4 and erosion > 0.15):
                        hard_exit_reason = f"PROFIT_EROSION_MAJOR_{erosion:.2f}%"
                    elif (current_gain < 0.1 and max_gain > 0.2):
                        hard_exit_reason = f"PROFIT_EROSION_MIN_RECLAIM_{current_gain:.2f}%"
                    elif mom_exhausted and current_gain < 0.12:
                        hard_exit_reason = f"MOM_FLIP_SMALL_PROFIT_EXIT_{current_gain:.2f}%"

                # c) Trailing Stop & Technical Exits
                if not hard_exit_reason:
                    # Logic: 2.5% peak -> 2% reclaim. If hit 3% but no recent augmentation -> Sell.
                    last_aug_age = minutes_since(position.last_augmentation_time)
                    if max_gain >= 2.5 and current_gain < 2.0:
                        is_unaugmented_3pct = (max_gain >= 3.0 and last_aug_age > 10)
                        hard_exit_reason = f"GAIN_PROTECTION_2.5PCT_RECLAIM_{current_gain:.2f}%"
                        if is_unaugmented_3pct: hard_exit_reason += "_NO_AUG_SELL"
                    elif (max_gain > 2.0 and (max_gain - current_gain) > 1.0):
                        hard_exit_reason = f"TRAILING_STOP_peak{max_gain:.1f}%"
                    elif k_1m:
                        if ((is_long and k_1m < d_1m) or (not is_long and k_1m > d_1m) ) and should_scalp:
                            hard_exit_reason = f"k1_scalp_HIT_{current_gain:.2f}%"
                    elif current_gain < 0.1 and (datetime.now(timezone.utc) - position.last_augmentation_time).total_seconds() > 300:
                        # 5 mins no progress? Get out if momentum is neutral/bad
                        is_stagnant = (is_long and k_3m < 55) or (not is_long and k_3m > 45)
                        if is_stagnant:
                            hard_exit_reason = f"STAGNATION_EXIT_{current_gain:.2f}%"

                is_h_acc = is_hedge_account(config, account_key)
                already_hedged = False
                if is_h_acc:
                    async with tracker_manager._hedges_lock:
                        for hedge in tracker_manager.active_hedges:
                            if hedge.get('losing_position_key') == position_key:
                                already_hedged = True
                                break
                                
                min_hedge_val = safe_fetch_float(getattr(config, 'START_POSITION_SIZE', 50.0), 50.0)

                # --- 1. NEW: PROACTIVE HEDGING (Hedge the Top, not the Bottom) ---
                if is_h_acc and not already_hedged and position_value >= min_hedge_val:
                    # --- COOLDOWN CHECK ---
                    last_kill = tracker_manager.hedge_liability_cooldowns.get(position_key, 0.0)
                    if (time.time() - last_kill) < 300.0:
                        proactive_hedge = False
                    else:
                        proactive_hedge = False
                        if is_long:
                            # Hedge when 15m is HIGH (good time to short) and 1m/3m hook DOWN
                            if k_15m > 70 and (k_1m < d_1m or k_3m < d_3m) and k_3m < k_3m_prev:
                                logger.info(f"🛡️ [PROACTIVE_HEDGE] {position_key}: Momentum turning DOWN from top (k_15m={k_15m:.1f}, k_3m={k_3m:.1f}). Hedging BEFORE huge loss.")
                                proactive_hedge = True
                        else:
                            # Hedge when 15m is LOW (good time to long) and 1m/3m hook UP
                            if k_15m < 30 and (k_1m > d_1m or k_3m > d_3m) and k_3m > k_3m_prev:
                                logger.info(f"🛡️ [PROACTIVE_HEDGE] {position_key}: Momentum turning UP from bottom (k_15m={k_15m:.1f}, k_3m={k_3m:.1f}). Hedging BEFORE huge loss.")
                                proactive_hedge = True
                    
                    if proactive_hedge:
                        hedge_result = await hedge_engine.execute_dual_hedge(account_key=account_key,losing_position_key=position_key,losing_symbol=symbol,losing_side='LONG' if is_long else 'SHORT',losing_value_usd=position_value, dry_run=False )
                        if hedge_result and hedge_result.get('overall_status') in ['success', 'partial']:
                            await tracker_manager.clear_processing(position_key)
                            return "PROACTIVE_HEDGED"
                        else:
                            # --- EMERGENCY MITIGATION ---
                            logger.critical(f"🚨 [PROACTIVE_HEDGE_FAILURE] Dual hedge failed for {position_key}: {hedge_result.get('error')}. KILLING ORIGINAL POSITION.")
                            side_kill = 'SELL' if is_long else 'BUY'
                            await trade_manager.execute_now(
                                position_key, account_key, symbol, position.positionAmt, 
                                side_kill, position_side, position.positionAmt, current_price, 
                                f"HEDGE_FAIL_PROACTIVE_KILL_{int(time.time())}", 
                                f"PROACTIVE_HEDGE_FAIL_MITIGATION_{hedge_result.get('error')}", True, 'CLOSE'
                            )
                            await tracker_manager.clear_processing(position_key)
                            return "PROACTIVE_HEDGE_FAILURE_MITIGATION"

                # --- 2. HARD EXIT / REACTIVE HEDGE ---
                if hard_exit_reason and (current_gain < prev_gain or not (k_3m==50 and d_3m == 50)) :
                    logger.warning(f"🛑 [STRONG_EXIT] {symbol} {hard_exit_reason}. Executing REDUCE.")
                    side='SELL' if is_long else 'BUY'
                    await tracker_manager.set_processing(position_key)
                    reduce_q = position.positionAmt
                    score_exit, rec_exit, reason_exit= -48, f'QUICK_EM_REDUCE_HARD_EXIT k1{k_1m}/{d_1m}', hard_exit_reason
                    if 'WAIT' in reason_exit:
                        return f'{position_key} WAIT MEANS WAIT7205'
                    
                    if is_h_acc and not already_hedged:
                        # --- COOLDOWN CHECK ---
                        last_kill = tracker_manager.hedge_liability_cooldowns.get(position_key, 0.0)
                        if (time.time() - last_kill) < 300.0: # 5 min cooldown
                            logger.info(f"🛡️ [HEDGE_COOLDOWN] Skipping hedge for {position_key} (Last liability kill < 5m ago).")
                        elif position_value < min_hedge_val:
                            logger.info(f"🛡️ [HEDGE_SKIP] {position_key} value ${position_value:.1f} too small to hedge.")
                        else:
                            hedge_result = await hedge_engine.execute_dual_hedge(account_key=account_key,losing_position_key=position_key,losing_symbol=symbol,losing_side='LONG' if is_long else 'SHORT',losing_value_usd=position_value, dry_run=False )
                            if hedge_result and hedge_result.get('overall_status') in ['success', 'partial']:
                                logger.info(f"🛡️ [HEDGE_DEPLOYED] Successfully hedged {position_key}. Aborting original reduction.")
                                await tracker_manager.clear_processing(position_key)
                                return "HEDGED_INSTEAD_OF_REDUCING"
                            else:
                                # --- EMERGENCY MITIGATION ---
                                logger.critical(f"🚨 [REACTIVE_HEDGE_FAILURE] Dual hedge failed for {position_key}: {hedge_result.get('error')}. KILLING ORIGINAL POSITION.")
                                await trade_manager.execute_now(
                                    position_key, account_key, symbol, position.positionAmt, 
                                    side, position_side, position.positionAmt, current_price, 
                                    f"HEDGE_FAIL_REACTIVE_KILL_{int(time.time())}", 
                                    f"REACTIVE_HEDGE_FAIL_MITIGATION_{hedge_result.get('error')}", True, 'CLOSE'
                                )
                                await tracker_manager.clear_processing(position_key)
                                return "REACTIVE_HEDGE_FAILURE_MITIGATION"

                    result = await trade_manager.execute_now(position_key, account_key, symbol,  position.positionAmt, side, position_side, reduce_q, current_price, f"QUICK_{reason_exit}_REDUCE", f"QUICK_{reason_exit}_REDUCE", False, rec_exit, is_hedge)
                    if result and 'SUCCESS' not in result and 'BLOCKED' not in result and account_key != 'ang':
                        result = await tracker_manager.send_webhook(position_key, position.positionAmt, side, current_price, reduce_q, True, reason_exit)
                    if result and 'SUCCESS' in result :
                        tracker_manager.registry.release_hedge_slot(account_key, symbol)
                        await tracker_manager.clear_processing(position_key)
                        await tracker_manager.set_trade_cooldown(position_key)
                        await tracker_manager.transition_to_entry(account_key, position_key, current_price, 0.0, is_long, status="REDUCED->ENTRY_CANDIDATE")
                        await tracker_manager.register_check(position_key)
                        await record_trade_event(tracker_manager, account_key, position_key, symbol, 'REDUCE', position_side, current_price, position.positionAmt, hard_exit_reason, False, False)
                        await tracker_manager.save_tracker(account_key, force=True)
                        return



                # if hard_exit_reason and (current_gain < prev_gain or not (k_3m==50 and d_3m == 50)) :
                #     logger.warning(f"🛑 [STRONG_EXIT] {symbol} {hard_exit_reason}. Executing REDUCE.")
                #     side='SELL' if is_long else 'BUY'
                #     await tracker_manager.set_processing(position_key)
                #     reduce_q = position.positionAmt#(positionAmt - pos_min_qty)
                #     # success, _ = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, positionAmt, 'REDUCE', current_price, reduce_q, f"HARD_EXIT_{hard_exit_reason}", already_locked=True, data_manager=data_manager)
                #     score_exit, rec_exit, reason_exit= -48, f'QUICK_EM_REDUCE_HARD_EXIT k1{k_1m}/{d_1m}', hard_exit_reason
                #     if 'WAIT' in reason_exit:
                #         return f'{position_key} WAIT MEANS WAIT7205'
                #     if config.HEDGE_MODE:
                #         async with tracker_manager._hedges_lock:
                #             for hedge in tracker_manager.active_hedges:
                #                 if hedge.get('losing_position_key') == position_key:
                #                     already_hedged = True
                #                     break
                #         min_hedge_val = safe_fetch_float(getattr(config, 'START_POSITION_SIZE', 50.0), 50.0)
                #         if not already_hedged:
                #             if position_value < min_hedge_val:
                #                 logger.info(f"🛡️ [HEDGE_SKIP] {position_key} value ${position_value:.1f} too small to hedge.")
                #             else:
                #                 await hedge_engine.execute_dual_hedge(account_key=account_key,losing_position_key=position_key,losing_symbol=symbol,losing_side='LONG' if is_long else 'SHORT',losing_value_usd=position_value, dry_run=False )

                #     result = await trade_manager.execute_now(position_key, account_key, symbol,  position.positionAmt, side, position_side, reduce_q, current_price, f"QUICK_{reason_exit}_REDUCE", f"QUICK_{reason_exit}_REDUCE", False, rec_exit, is_hedge)
                #     if result and 'SUCCESS' not in result and 'BLOCKED' not in result and account_key != 'ang':
                #         result = await tracker_manager.send_webhook(position_key, position.positionAmt, side, current_price, reduce_q, True, reason_exit)
                #     if result and 'SUCCESS' in result :
                #         tracker_manager.registry.release_hedge_slot(account_key, symbol)
                #         await tracker_manager.clear_processing(position_key)
                #         await tracker_manager.set_trade_cooldown(position_key)
                #         await tracker_manager.transition_to_entry(account_key, position_key, current_price, 0.0, is_long, status="REDUCED->ENTRY_CANDIDATE")
                #         # await log_stoch_snapshot(account_key, trade_manager, doit, position_key, current_price, metrics, indicators, tracker_manager, score_exit, rec_exit, reason_exit, dummy_state, dummy_lock, acc_logger)                    
                #         await tracker_manager.register_check(position_key)
                #         await record_trade_event(tracker_manager, account_key, position_key, symbol, 'REDUCE', position_side, current_price, position.positionAmt, hard_exit_reason, False, False)
                #         await tracker_manager.save_tracker(account_key, force=True)
                #         # if position_key=='ang:ROSEUSDT_LONG': logger.warning(f'ROSE4 {hard_exit_reason} {rec_exit} {position.positionAmt} 1:{k_1m}/{d_1m} 3:{k_3m}/{d_3m} 15:{k_15m}/{d_15m} ')
                #         return
                # # if position_key=='ang:ROSEUSDT_LONG': logger.warning(f'ROSE5 {hard_exit_reason} {position.positionAmt} 1:{k_1m}/{d_1m} 3:{k_3m}/{d_3m} 15:{k_15m}/{d_15m} ')
   
                k_str = f"k_1m:{int(sf(k_1m))}/d_1m:{int(sf(d_1m))}/k_3m:{int(sf(k_3m))}/d_3m:{int(sf(d_3m))}"
                # 7. STRATEGY RATING
                rec_exit = ""
                rec_entry = ""
                reason_exit = ""
                score_exit = 0
                score_entry = 0.0

                score_exit, rec_exit, reason_exit, ts  = tracker_manager.registry.get_rating(symbol, position_side)
                if (time.time() - ts) > 15:
                    prev_cross = _get_prev_cross_price(symbol, is_long)
                    score_exit, rec_exit, reason_exit = await AdvancedSignalRater.rate(account_key, symbol, is_long, current_price, metrics, indicators, prev_cross, is_exit=True, is_allowed=True, scalping_mode=should_scalp, tracker_manager=tracker_manager)
                if is_long and tracker_manager.registry.market_panic:
                    if current_gain < 0.2: 
                        score_exit -= 10
                        reason_exit += "_PANIC_TIGHTEN"
                        
                if not is_long and tracker_manager.registry.market_euphoria:
                    if current_gain < 0.2:
                        score_exit -= 10
                        reason_exit += "_EUPHORIA_TIGHTEN"

                should_close = (hard_exit_reason is not None) or ("CLOSE" in rec_exit or "PROFIT" in rec_exit or "REDUCE" in rec_exit or "EXIT" in rec_exit or "DECAY" in rec_exit or score_exit < -4 or "LOSS" in reason_exit)
                if hard_exit_reason:
                    reason_exit += f"_{hard_exit_reason}"
                    if not rec_exit or "WAIT" in rec_exit: rec_exit = "REDUCE"
                stagnation_exit = False
                now_dt = datetime.now(timezone.utc)
                opened_at = getattr(position, "opened_at", None)
                if isinstance(opened_at, str):
                    try: opened_at = isoparse(opened_at)
                    except: opened_at = None
                if isinstance(opened_at, datetime) and not (k_3m == 50 and d_3m == 50):
                    if opened_at.tzinfo is None: opened_at = opened_at.replace(tzinfo=timezone.utc)
                    if (now_dt - opened_at).total_seconds() > 900 and getattr(position, "gain", 0) < 0.2:
                        should_close = True
                        stagnation_exit = True
                        rec_exit = "REDUCE"
                        reason_exit += "STAGNATION"
                if should_close:
                    if is_hedge_account(config, account_key):
                        async with tracker_manager._hedges_lock:
                            for hedge in tracker_manager.active_hedges:
                                if hedge.get('losing_position_key') == position_key:
                                    already_hedged = True
                                    break
                        min_hedge_val = safe_fetch_float(getattr(config, 'START_POSITION_SIZE', 50.0), 50.0)
                        if not already_hedged:
                            if position_value < min_hedge_val:
                                logger.info(f"🛡️ [HEDGE_SKIP] {position_key} value ${position_value:.1f} too small to hedge.")
                            else:
                                logger.info(f"🛡️ [HEDGE_SCANNER] {position_key} losing -> Triggering hedge")
                                await hedge_engine.execute_dual_hedge(account_key=account_key,losing_position_key=position_key,losing_symbol=symbol,losing_side='LONG' if is_long else 'SHORT',losing_value_usd=position_value, dry_run=False )
                                return
                    await tracker_manager.set_processing(position_key)
                    action_type = "REDUCE"
                    reduction_qty = position.positionAmt if action_type == "CLOSE" or stagnation_exit else (positionAmt - pos_min_qty)
                    
                    full_reason = f"{action_type}_{rec_exit}_{k_str}_{reason_exit}"
                    if 'WAIT' in full_reason:
                        return f'{position_key} WAIT MEANS WAIT7264'
                    side='SELL' if is_long else 'BUY'
                    if 'WAIT' in full_reason:
                        return f'{position_key} WAIT MEANS WAIT'
                    result = await trade_manager.execute_now(position_key, account_key, symbol,  position.positionAmt, side, position_side, reduction_qty, current_price, f"QUICK_{full_reason}_REDUCE", f"QUICK_{full_reason}_REDUCE", False, rec_exit, is_hedge=is_hedge, hedge_for=hedge_for)
                    if result and 'SUCCESS' not in result and 'BLOCKED' not in result and account_key != 'ang':                    
                        result = await tracker_manager.send_webhook(position_key, positionAmt, side, current_price, reduction_qty, True, full_reason)

                    # logger.info(f'{position_key} position CLOSED8111')
                    if result and 'SUCCESS' in result:
                        tracker_manager.registry.release_hedge_slot(account_key, symbol)

                        await tracker_manager.clear_processing(position_key)
                        await tracker_manager.set_trade_cooldown(position_key)
                        await tracker_manager.transition_to_entry(account_key, position_key, current_price, 0.0, is_long, status="REDUCED->ENTRY_CANDIDATE")
                        # await log_stoch_snapshot(account_key, trade_manager, doit, position_key, current_price, metrics, indicators, tracker_manager, score_exit, rec_exit, reason_exit, dummy_state, dummy_lock, acc_logger)                    
                        await tracker_manager.register_check(position_key)
                        await record_trade_event(tracker_manager, account_key, position_key, symbol, 'REDUCE', position_side, current_price, position.positionAmt, reason_exit, False, False)
                        await tracker_manager.save_tracker(account_key, force=True)
                    # success, _ = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, positionAmt, 'REDUCE', current_price, reduction_qty, full_reason, already_locked=True, data_manager=data_manager)
                    # if not success: await tracker_manager.clear_processing(position_key)
                
                elif not should_close and k_1m is not None and minutes_since(position.last_augmentation_time) > 15:
                    if position_key  not in tracker_manager.tradeable_position_keys.get(account_key, set()):return
                    if position.gain > 0.6 * config.MIN_GAIN_TO_BUY_AGGRESSIVELY:
                        prev_cross = _get_prev_cross_price(symbol, is_long)
                        score_entry, rec_entry, reason_entry = await AdvancedSignalRater.rate(account_key, symbol, is_long, current_price, metrics, indicators, prev_cross, is_exit=False, is_allowed=True, scalping_mode=should_scalp, tracker_manager=tracker_manager)
                        if 'WAIT' in rec_entry:  return #or 'WAIT' in reason_entry:
                        is_buy_signal = (("BUY" in rec_entry and is_long) or ("SELL" in rec_entry and not is_long) or (score_entry >= 12))
                        if position.gain >= 3.0:
                            is_buy_signal = True; reason_entry += "_FORCE_AUGMENT_3PCT"
                        elif not is_buy_signal and position.gain > 2.0:
                            if (is_long and k_15m > d_15m and k_15m < 80) or (not is_long and k_15m < d_15m and k_15m > 20):
                                is_buy_signal = True; reason_entry += "_HIGH_GAIN_MOMENTUM"
                        
                        if is_buy_signal and position_key in tracker_manager.tradeable_position_keys.get(account_key, set()) and position.gain > config.MIN_GAIN_TO_BUY_AGGRESSIVELY:
                            if not await tracker_manager.is_trade_cooldown_active(position_key):

                                # Refined 3% High-Gain Augmentation Logic
                                k_3m_vel = k_3m - k_3m_prev
                                aug_ratio = 0.002 # 0.2% default
                                if k_3m_vel > 0: aug_ratio = 0.004 # 0.4% on positive momentum
                                
                                caution_tag = ""
                                if is_long:
                                    if k_15m > 90 or k_1h > 90: aug_ratio *= 0.5; caution_tag += "_K90"
                                    if dc_high_15m > 0 and current_price >= dc_high_15m * 0.998: aug_ratio *= 0.5; caution_tag += "_NEAR_DC15"
                                    if dc_high_1h > 0 and current_price >= i.get(dc_high_1h) * 0.998: aug_ratio *= 0.5; caution_tag += "_NEAR_DC1H"
                                else:
                                    if k_15m < 10 or k_1h < 10: aug_ratio *= 0.5; caution_tag += "_K10"
                                    if dc_low_15m > 0 and current_price <= dc_low_15m * 1.002: aug_ratio *= 0.5; caution_tag += "_NEAR_DC15"
                                    if dc_low_1h > 0 and current_price <= dc_low_1h * 1.002: aug_ratio *= 0.5; caution_tag += "_NEAR_DC1H"

                                augment_qty = abs(position.positionAmt) * aug_ratio
                                # Ensure minimum order size requirements while respecting the requested scale
                                base_qty = float(config.START_POSITION_SIZE) / current_price
                                min_qty_symbol = trade_manager.min_qty.get(symbol, 0.0)
                                augment_qty = max(augment_qty, min_qty_symbol, base_qty * 0.1)
                                
                                reason_entry += f"_DYN_{aug_ratio*1000:.1f}bp{caution_tag}"
                                side='BUY' if is_long else 'SELL'
                                if (augment_qty * current_price) >= 5.0: # Protect against invalid Binance dust orders
                                    augment_reason = f"AUGMENT_WINNING_g{position.gain:.2f}%_{k_str}_{rec_entry}_{reason_entry}"

                                if 'WAIT' in augment_reason:
                                    return f'{position_key} WAIT MEANS WAIT7301'
                                await tracker_manager.set_processing(position_key)
                                success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, position.positionAmt, 'AUGMENT', current_price, augment_qty, augment_reason, already_locked=True, data_manager=data_manager)
                                # if not success: 
                                #     await tracker_manager.send_webhook(position_key, positionAmt, side, current_price, augment_qty, False, augment_reason)
                                if success:
                                    async with tracker_manager._entry_candidates_lock:
                                        if position_key in tracker_manager.entry_candidates:
                                            del tracker_manager.entry_candidates[position_key]
                                            tracker_manager._entry_candidates_dirty[account_key] = True
                                    await tracker_manager.transition_to_exit(account_key, position_key, current_price, position.positionAmt, status='active')
                                    await record_trade_event(tracker_manager, account_key, position_key, symbol, 'AUGMENT', position_side, current_price, augment_qty, augment_reason, False, False )
                                    await tracker_manager.save_tracker(account_key, force=True)
                                    # if "BREAKOUT_PLAY" in reason or "MOMENTUM_SCALP" in reason:
                                    #     if not hasattr(trade_manager, 'strict_close_positions'):
                                    #         trade_manager.strict_close_positions = {}
                                    #     trade_manager.strict_close_positions[position_key] = {
                                    #         'augmented_at': datetime.now(timezone.utc),
                                    #         'augment_price': current_price,
                                    #         'strict_loss_threshold': -0.4,   # micro-stop: Cut at -0.4% loss
                                    #         'strict_gain_threshold': 1.0,    # Take quick profit if it stalls
                                    #         'strict_reduction_trigger': True }

                if 'score_entry' not in locals(): score_entry = 2
                if 'rec_entry' not in locals(): rec_entry = "WAIT"
                if 'rec_exit' not in locals(): rec_exit = "HOLsdfwretwrtwretwretretwD"
                if 'reason_exit' not in locals(): reason_exit = ""
                if 'score_exit' not in locals(): score_exit = 0
                if 'should_close' not in locals(): should_close = False

                final_score = score_entry if not should_close else score_exit
                final_rec = rec_entry if not should_close else rec_exit
                doit=final_rec
                
                await log_stoch_snapshot(account_key, trade_manager, doit, position_key, current_price, metrics, indicators, tracker_manager, final_score, final_rec, rec_exit, dummy_state, dummy_lock, acc_logger)
                await tracker_manager.register_check(position_key)

            except Exception as e:
                logger.error(f"[EXIT_CHECK_FAIL] {position_key}: {e}", exc_info=True)
                try: await tracker_manager.clear_processing(position_key)
                except: pass
    await asyncio.gather(*(process_single_exit(k) for k in position_keys))
 
async def check_entry_candidates_for_account(trade_manager, account_key: str, redis_manager, tracker_manager: TrackerManager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine=None, position_keys: List[str] = None, force: bool = False) -> None: 
    if not position_keys: 
        logger.info(f"🔍 {position_keys} no pos keys sent")
        return
    sem = asyncio.Semaphore(50)
    async def worker(position_key):
        success = False
        msg = "NO_TRADE"
        score = 0
        rec = "WAIT"
        reason = "SCANNING"
        # -----------------------------------------------------------------------
        try:
            async with sem:
                now = time.time()
                scalping_mode = getattr(config, "SCALP_MODE", False)
                scalping_accounts = getattr(config, "SCALP_ACCOUNTS", [])
                should_scalp = scalping_mode and account_key in scalping_accounts
                
                if trade_manager._allowed_accounts and account_key not in trade_manager._allowed_accounts: 
                    return

                if position_key not in tracker_manager.tradeable_keys: 
                    await tracker_manager._get_tradeable_keys_cached()
                    if position_key not in tracker_manager.tradeable_keys: 
                        if position_key in tracker_manager.tradeable_position_keys[account_key]:
                            tracker_manager.tradeable_position_keys[account_key].discard(position_key)
                        if position_key in tracker_manager.tradeable_keys:
                            tracker_manager.tradeable_keys_cache.discard(position_key)
                            await tracker_manager.save_tracker(account_key, force=True)
                            await tracker_manager.sync_universe(account_key)
                        return

                acc_logger = get_account_logger(account_key)
                dummy_state = {}; dummy_lock = DummyLock()
                is_lagging = tracker_manager.is_system_lagging(account_key)
                throttle_threshold = 180.0 if is_lagging else 45.0
                last_check = tracker_manager.get_last_check_time(position_key)
                
                if last_check > 0 and (now - last_check) < throttle_threshold:
                    return
                
                if await tracker_manager.is_processing(position_key):
                    if (now - tracker_manager._processing_orders.get(position_key, 0)) > 60:
                        logger.warning(f"🔓 [AUTO_UNLOCK] {position_key} was locked too long.")
                        await tracker_manager.clear_processing(position_key)
                    else:
                        return

                ak, symbol, position_side = parse_position_key(position_key)
                
                # 2. Data Fetch
                metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_data_fresh = await data_manager.get_hot_state(symbol)
                if not isinstance(metrics, dict): 
                    logger.info(f'FUCKED METRICS {metrics}')
                    metrics = {}
                if not isinstance(indicators, dict): indicators = {}
                if should_scalp and not is_data_fresh: should_scalp = False
                
                k_1m_prev = safe_fetch_float(metrics.get('k_1m_prev', 50.0))
                k_3m_prev = safe_fetch_float(metrics.get('k_3m_prev', 50.0))
                dc_high_3m = safe_fetch_float(indicators.get('dc_high_3m'), 0.0)
                dc_low_3m = safe_fetch_float(indicators.get('dc_low_3m'), 0.0)
                
                if random.random() < 0.01:
                   logger.info(f"🔍 {symbol} Fresh={is_data_fresh} k_1m={k_1m} Price={indicators.get('current_price')}")
                
                if not is_data_fresh:
                    if random.random() < 0.01: 
                        tick_ts = metrics.get('_tick_ts', 0)
                        age = time.time() - tick_ts
                        logger.warning(f"⏳ [ENTRY_SKIP] {symbol} Data Stale. Age: {age:.1f}s")
                    
                current_price = None
                hot_price = safe_fetch_float(indicators.get('current_price', 0.0))
                hot_ts = metrics.get('_tick_ts', 0)
                
                if hot_price > 0 and (now - hot_ts) < 5.0:
                    current_price = hot_price
                else:
                    cached = trade_manager.price_cache.get(symbol, {})
                    if isinstance(cached, dict):
                         ws_price = cached.get('price', 0)
                         ws_ts = cached.get('timestamp', 0)
                    else:
                         ws_price = cached; ws_ts = 0
                    if ws_price > 0 and (now - ws_ts) < 5.0:
                        current_price = ws_price
                        
                if not current_price: current_price, ts = await data_manager.get_fresh_price(symbol)
                if not current_price: current_price, ts = await get_current_price(symbol)
                
                position = await tracker_manager.get_position(position_key)
                if not position: 
                    acc_positions = tracker_manager.positions_service.positions_by_account.get(account_key, {})
                    if isinstance(acc_positions, dict):
                        position = acc_positions.get(position_key)

                pos_amt = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0), 0.0))
                current_gain = safe_fetch_float(getattr(position, 'gain', 0.0), 0.0)
                pos_val = pos_amt * current_price
                start_size = safe_fetch_float(getattr(config, 'START_POSITION_SIZE', 55.0), 55.0)
                
                # --- SMALL POSITION PULLBACK BYPASS ---
                # Allow small positions to "average down" if in technical pullback.
                is_small = pos_val < (1.5 * start_size)
                allow_neg_augment = False
                if pos_amt > 0 and current_gain <= 0 and is_small:
                    # Pullback zone: Long oversold (<35) or Short overbought (>65)
                    is_long = (position_side == 'LONG')
                    is_pullback = (is_long and k_3m < 35) or (not is_long and k_3m > 65)
                    if is_pullback:
                        logger.info(f"🛡️ [PULLBACK_AUGMENT_QUICK] {position_key}: Small position ({pos_val:.1f}$) in pullback (k3={k_3m:.1f}). Allowing augment despite negative gain ({current_gain:.2f}%).")
                        allow_neg_augment = True

                # Check for existing positions that shouldn't be augmented yet
                if pos_amt > 0 and not allow_neg_augment and minutes_since(position.last_augmentation_time) < 15 and current_gain < 1: 
                    return
                
                is_long = (position_side == 'LONG')
                prev_cross = _get_prev_cross_price(symbol, is_long)
                success=False
                
                # # 3. DUST EXIT LOGIC (Small position flip)
                # if pos_amt > 0:
                #     score_exit, rec_exit, reason_exit = await AdvancedSignalRater.rate(account_key, symbol, is_long, current_price, metrics, indicators, prev_cross, is_exit=True, is_allowed=True, scalping_mode=should_scalp, tracker_manager=tracker_manager)
                #     should_close = ("CLOSE" in rec_exit or "PROFIT" in rec_exit or "REDUCE" in rec_exit or "EXIT" in rec_exit or "DECAY" in rec_exit or score_exit < -4 or "LOSS" in reason_exit)
                #     if should_close:
                #         logger.info(f"📉 [DUST_EXIT] {position_key}: Small position signal flip ({rec_exit}). Closing.")
                #         await tracker_manager.set_processing(position_key)
                #         full_reason = f"DUST_EXIT_{rec_exit}_{reason_exit}"
                #         if isinstance(full_reason, str) and 'WAIT' in full_reason: return
                        
                #         success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, pos_amt, 'CLOSE', current_price, pos_amt, full_reason, already_locked=True, data_manager=data_manager)
                #         if success:
                #             tracker_manager.registry.release_hedge_slot(account_key, symbol)
                #             return 
                #         if not success and account_key != 'ang': 
                #             await tracker_manager.send_webhook(position_key, pos_amt, 'SELL' if is_long else 'BUY', current_price, pos_amt, False, full_reason)
                #         await tracker_manager.clear_processing(position_key)
                #         return

                # 4. ENTRY LOGIC
                entry_meta = await tracker_manager.get_entry_candidate(position_key)
                if not entry_meta: 
                    entry_meta = tracker_manager._entry_template()
                    tracker_manager.entry_candidates[position_key] = entry_meta
                
                # --- CACHE LOOKUP ---
                score, rec, reason, ts_cache = tracker_manager.registry.get_rating(symbol, position_side)
                
                # Fallback if cache is stale
                if (now - ts_cache) > 15:
                    score, rec, reason = await AdvancedSignalRater.rate(account_key, symbol, is_long, current_price, metrics, indicators, prev_cross, is_exit=False, is_allowed=True, last_exit_timestamp=entry_meta.get('last_exit_timestamp'), scalping_mode=should_scalp, tracker_data=entry_meta, tracker_manager=tracker_manager)
                
                should_trade = False
                pyramid_data = None
                if (score >= 4 or "BUY" in rec or "SELL" in rec) and 'WAIT' not in str(rec):
                    should_trade = True
                
                # --- SAFEGUARD: FORCE OPEN ON DC BREAKOUT ---
                if not should_trade and position_key in tracker_manager.tradeable_position_keys.get(account_key, set()):
                    if is_long and k_1m > d_1m and current_price >= dc_high_3m and dc_high_3m > 0:
                        should_trade = True; score = max(score, 15.0); reason += "_FORCE_DC_HIGH_BREAKOUT"
                    elif not is_long and k_1m < d_1m and current_price <= dc_low_3m and dc_low_3m > 0:
                        should_trade = True; score = max(score, 15.0); reason += "_FORCE_DC_LOW_BREAKOUT"
                
                if not should_trade: 
                    pyramid_data = await hedge_engine.try_aggressive_pyramid(account_key, position_key, current_price)
                    if pyramid_data: should_trade = True
                    
                if should_trade and position_key in tracker_manager.tradeable_position_keys.get(account_key, set()):
                    if not pyramid_data and isinstance(reason, str) and 'WAIT' in reason:
                        return f'{position_key} WAIT MEANS WAIT7205'
                    
                    curr_amt = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0), 0.0)) if position else 0.0
                    
                    # Unpack Pyramid Data OR use dynamic calc
                    if pyramid_data:
                        qty = pyramid_data['quantity']
                        reason = pyramid_data['reason']
                        rec = "PYRAMID_BOTA"
                    else:
                        qty = calculate_dynamic_quantity(symbol, current_price, score, trade_manager.config, trade_manager, entry_meta, is_long, indicators, metrics)
                        max_entry = float(config.START_POSITION_SIZE) * 12.0 / current_price # Allow BOTA expansion
                        qty = min(qty, max_entry)
                        
                    await tracker_manager.set_processing(position_key)
                    await tracker_manager.transition_to_exit(account_key, position_key, current_price, qty, status='PENDING_OPEN')
                    if "BREAKOUT_PLAY" in reason or "MOMENTUM_SCALP" in reason:
                        if not hasattr(trade_manager, 'strict_close_positions'):
                            trade_manager.strict_close_positions = {}
                        trade_manager.strict_close_positions[position_key] = {
                            'augmented_at': datetime.now(timezone.utc),
                            'augment_price': current_price,
                            'strict_loss_threshold': -0.4,   # micro-stop: Cut at -0.4% loss
                            'strict_gain_threshold': 1.0,    # Take quick profit if it stalls
                            'strict_reduction_trigger': True }
                    k_str = f"k1:{int(k_1m or 50)}/d1{int(d_1m or k_1m_prev)}/k3:{(k_3m or 0)}/d3:{(d_3m or k_3m_prev)}"
                    full_reason = f"{'OPEN' if curr_amt == 0 else 'AUGMENT'}_{rec}_{k_str}_@{current_price:.4f}_{reason}"
                    action_type = 'OPEN' if curr_amt == 0 else 'AUGMENT'                

                    success, msg = await execute_trade_wrapper(trade_manager, tracker_manager, hedge_engine, account_key, position_key, pos_amt, action_type, current_price, qty, full_reason, already_locked=True, data_manager=data_manager)
                    
                    if not success and 'BLOCK' not in str(msg) and account_key != 'ang':
                        await tracker_manager.send_webhook(position_key, pos_amt, 'BUY' if is_long else 'SELL', current_price, qty, False, full_reason)

                    if success: 
                        logger.info(f"🚀 {position_key} {action_type} SUCCESS {msg}")
                        async with tracker_manager._entry_candidates_lock:
                            if position_key in tracker_manager.entry_candidates:
                                del tracker_manager.entry_candidates[position_key]
                                tracker_manager._entry_candidates_dirty[account_key] = True
                        await tracker_manager.transition_to_exit(account_key, position_key, current_price, pos_amt + qty, status='active')
                        if "BREAKOUT_PLAY" in reason or "MOMENTUM_SCALP" in reason:
                            if not hasattr(trade_manager, 'strict_close_positions'):
                                trade_manager.strict_close_positions = {}
                            trade_manager.strict_close_positions[position_key] = {
                                'augmented_at': datetime.now(timezone.utc),
                                'augment_price': current_price,
                                'strict_loss_threshold': -0.4,   # micro-stop: Cut at -0.4% loss
                                'strict_gain_threshold': 1.0,    # Take quick profit if it stalls
                                'strict_reduction_trigger': True }
                        await record_trade_event(tracker_manager, account_key, position_key, symbol, action_type, position_side, current_price, qty, reason, False, False)
                        await tracker_manager.save_tracker(account_key, force=True)
                    else:
                        # Clean up slot if hedge attempt failed
                        if 'HEDGE' in str(full_reason).upper():
                            tracker_manager.registry.release_hedge_slot(account_key, symbol)
                        if action_type == 'OPEN':
                            await tracker_manager.revert_optimistic_exit(account_key, position_key)

                # 7. LOGGING
                await log_stoch_snapshot(account_key, trade_manager, "ENTRY", position_key, current_price, metrics, indicators, tracker_manager, score, rec, reason, dummy_state, dummy_lock, acc_logger)
                await tracker_manager.register_check(position_key)

        except Exception as e:
            logger.error(f"[ENTRY_CHECK_FAIL] {position_key}: {e}", exc_info=True)
            await tracker_manager.clear_processing(position_key)

    await asyncio.gather(*(worker(k) for k in position_keys))

# async def check_entry_candidates_for_account(trade_manager, account_key: str, redis_manager, tracker_manager: TrackerManager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine=None, position_keys: List[str] = None, force: bool = False) -> None: 
#     if not position_keys: 
#         logger.info(f"🔍 {position_keys} no pos keys sent")
#         return
#     sem = asyncio.Semaphore(50)
#     async def worker(position_key):
#         try:
#             async with sem:
#                 now = time.time()
#                 # logger.info(f"🔍 {position_key} starting entry eval")
#                 # if not force:
#                 scalping_mode = getattr(config, "SCALP_MODE", False)
#                 scalping_accounts = getattr(config, "SCALP_ACCOUNTS", [])
#                 should_scalp = scalping_mode and account_key in scalping_accounts
#                 if trade_manager._allowed_accounts and account_key not in trade_manager._allowed_accounts: 
#                     logger.info(f"🔍 {position_key} not in {trade_manager._allowed_accounts}")
#                     return
#                 if position_key not in tracker_manager.tradeable_keys: 
#                     await tracker_manager._get_tradeable_keys_cached()
#                     if position_key not in tracker_manager.tradeable_keys: 
#                         logger.info(f"🔍 {position_key} not in1 {len(tracker_manager.tradeable_keys)} tradeable")
#                         if position_key in tracker_manager.tradeable_position_keys[account_key]:
#                             tracker_manager.tradeable_position_keys[account_key].discard(position_key)
#                         if position_key in tracker_manager.tradeable_keys:
#                             tracker_manager.tradeable_keys_cache.discard(position_key)
#                             await tracker_manager.save_tracker(account_key, force=True)
#                             await tracker_manager.sync_universe(account_key)

#                             return
#                 acc_logger = get_account_logger(account_key)
#                 dummy_state = {}; dummy_lock = DummyLock()
#                 is_lagging = tracker_manager.is_system_lagging(account_key)
#                 throttle_threshold = 180.0 if is_lagging else 45.0
#                 last_check = tracker_manager.get_last_check_time(position_key)
#                 if last_check > 0 and (time.time() - last_check) < throttle_threshold:
#                     return
#                 if await tracker_manager.is_processing(position_key):
#                     if (time.time() - tracker_manager._processing_orders.get(position_key, 0)) > 60:
#                         logger.warning(f"🔓 [AUTO_UNLOCK] {position_key} was locked too long.")
#                         await tracker_manager.clear_processing(position_key)
#                     else:
#                         return
#                 # if position_key in tracker_manager.exit_candidates: 
#                 #     logger.info(f"🔍 {position_key} is exit cand")
#                     # return
#                 ak, symbol, position_side = parse_position_key(position_key)
#                 if position_key not in tracker_manager.tradeable_position_keys.get(account_key, set()) and position_key not in tracker_manager.tradeable_keys and position_key not in tracker_manager.tradeable_keys_cache:
#                     tracker_manager.tradeable_position_keys[account_key].discard(position_key)
#                     await tracker_manager.sync_universe(account_key)
#                     if position_key not in tracker_manager.tradeable_position_keys.get(account_key, set())  :
#                         logger.warning(f"  {position_key} NOT TRADEABLE ")
#                         return
#                 # 2. Data Fetch
#                 metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_data_fresh = await data_manager.get_hot_state(symbol)
#                 if not isinstance(metrics, dict): metrics = {}
#                 if not isinstance(indicators, dict): indicators = {}
#                 if should_scalp and not is_data_fresh: should_scalp = False
#                 k_1m_prev =safe_fetch_float(metrics.get('k_1m_prev', 50.0));k_3m_prev =safe_fetch_float(metrics.get('k_3m_prev', 50.0))
#                 if random.random() < 0.01:
#                    logger.info(f"🔍 {symbol} Fresh={is_data_fresh} k_1m={k_1m} Price={indicators.get('current_price')}")
#                 if not is_data_fresh:
#                     if random.random() < 0.01: 
#                         tick_ts = metrics.get('_tick_ts', 0)
#                         age = time.time() - tick_ts
#                         logger.warning(f"⏳ [ENTRY_SKIP] {symbol} Data Stale. Age: {age:.1f}s")
                    
#                 current_price=None
#                 hot_price = safe_fetch_float(indicators.get('current_price', 0.0))
#                 hot_ts = metrics.get('_tick_ts', 0)
                
#                 if hot_price > 0 and (now - hot_ts) < 5.0:
#                     current_price = hot_price
#                 else:
#                     cached = trade_manager.price_cache.get(symbol, {})
#                     if isinstance(cached, dict):
#                          ws_price = cached.get('price', 0)
#                          ws_ts = cached.get('timestamp', 0)
#                     else:
#                          ws_price = cached; ws_ts = 0
#                     if ws_price > 0 and (now - ws_ts) < 5.0:
#                         current_price = ws_price
#                 if not current_price: current_price, ts= await data_manager.get_fresh_price(symbol)
#                 if not current_price: current_price, ts= await get_current_price(symbol)
#                 position = await tracker_manager.get_position(position_key)
#                 if not position: 
#                     acc_positions = tracker_manager.positions_service.positions_by_account.get(account_key, {})
#                     if isinstance(acc_positions, dict):
#                         position = acc_positions.get(position_key)
#                     else:
#                         position = None
#                 pos_amt = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0), 0.0))
#                 if pos_amt > 0 and minutes_since(position.last_augmentation_time) < 15 and position.gain < 1: 
#                     return
#                 is_long = (position.position_side == 'LONG')
#                 prev_cross = _get_prev_cross_price(symbol, is_long)
#                 positionAmt = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0), 0.0))
#                 if positionAmt > 0:
#                     score_exit, rec_exit, reason_exit = await AdvancedSignalRater.rate( account_key=account_key,  symbol=symbol,  is_long=is_long,   current_price=current_price,   metrics=metrics,   ind=indicators,   prev_cross_price=prev_cross,  is_exit=True,   is_allowed=True,  scalping_mode=should_scalp,  tracker_manager=tracker_manager  )
#                     should_close = ("CLOSE" in rec_exit or "PROFIT" in rec_exit or "REDUCE" in rec_exit or "EXIT" in rec_exit or "DECAY" in rec_exit or score_exit < -4 or "LOSS" in reason_exit)
#                     if should_close:
#                         logger.info(f"📉 [DUST_EXIT] {position_key}: Small position signal flip ({rec_exit}). Closing.")
#                         await tracker_manager.set_processing(position_key)
#                         full_reason = f"DUST_EXIT_{rec_exit}_{reason_exit}"
#                         if 'WAIT' in full_reason:
#                             return f'{position_key} WAIT MEANS WAIT'
#                         side = 'SELL' if position.position_side=='LONG' else 'BUY'
#                         success, msg = await execute_trade_wrapper( trade_manager, tracker_manager, hedge_engine,  account_key, position_key, positionAmt, 'CLOSE',  current_price, positionAmt, full_reason,  already_locked=True, data_manager=data_manager  )
#                         if success:
#                             tracker_manager.registry.release_hedge_slot(account_key, symbol)
#                             return 
#                         if not success and account_key != 'ang': 
#                             await tracker_manager.send_webhook(position_key, positionAmt, side, current_price, positionAmt, False, full_reason)
#                         await tracker_manager.clear_processing(position_key)
#                         return
#                 entry_meta = await tracker_manager.get_entry_candidate(position_key)
#                 if not isinstance(entry_meta, dict): 
#                     entry_meta = None 
#                 if not entry_meta: 
#                     entry_meta = tracker_manager._entry_template()
#                     tracker_manager.entry_candidates[position_key] = entry_meta
                    
#                 score, rec, reason, ts = tracker_manager.registry.get_rating(symbol, position_side)
#                 if (time.time() - ts) > 15:
#                     score, rec, reason = await AdvancedSignalRater.rate( account_key=account_key, symbol=symbol, is_long=is_long, current_price=current_price, metrics=metrics, ind=indicators,  prev_cross_price=prev_cross, is_exit=False, is_allowed=True, last_exit_timestamp=entry_meta.get('last_exit_timestamp'), scalping_mode=should_scalp,  tracker_data=entry_meta, tracker_manager=tracker_manager)
#                 logger.debug(f" before calc qty {position_key} score {score} k_1m:{k_1m} d_1m:{d_1m} k_3m:{k_3m} d_3m:{d_3m} k_15m:{indicators.get('stoch_k_15m')} d_15m:{indicators.get('stoch_d_15m')} - after logger")
#                 should_trade = False
#                 if (score and score >= 4 or "BUY" in rec or "SELL" in rec or 'AUGMENT' in rec) and 'WAIT' not in rec:
#                     should_trade = True
#                 if not should_trade: should_trade=await hedge_engine.try_aggressive_pyramid(account_key,position_key,current_price)
#                 if should_trade and position_key in tracker_manager.tradeable_position_keys.get(account_key, set()):
#                     # Ensure reason is a string before checking content
#                     if isinstance(reason, str) and 'WAIT' in reason:
#                         return f'{position_key} WAIT MEANS WAIT7205'
#                     curr_amt = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0), 0.0)) if position else 0.0
#                     qty = calculate_dynamic_quantity(symbol, current_price, score, trade_manager.config, trade_manager, entry_meta, is_long, indicators, metrics)
#                     max_entry = float(config.START_POSITION_SIZE) * 5.0 / current_price
#                     qty = min(qty, max_entry, config.START_POSITION_SIZE/current_price)
#                     await tracker_manager.set_processing(position_key)
#                     await tracker_manager.transition_to_exit(account_key, position_key, current_price, qty, status='PENDING_OPEN')
                    
#                     k_str = f"k1:{int(k_1m or 50)}/d1{int(d_1m or k_1m_prev)}/k3:{(k_3m or 0)}/d3:{(d_3m or k_3m_prev)}"
#                     full_reason = f"OPEN_{rec}_{k_str}_@{current_price:.4f}_{reason}"
#                     action_type = 'OPEN' if curr_amt == 0 else 'AUGMENT'

#                     success, msg = await execute_trade_wrapper( trade_manager, tracker_manager, hedge_engine, account_key,  position_key, positionAmt, action_type, current_price, qty, full_reason,  already_locked=True, data_manager=data_manager)
#                     if not success and 'BLOCK' not in msg and account_key != 'ang': #TEMP OUT
#                         success = await tracker_manager.send_webhook(position_key, positionAmt, 'BUY' if is_long else 'SELL', current_price, qty, False, full_reason)
#                         # success= 'SUCCESS' in result
#                     # await log_stoch_snapshot(account_key, trade_manager, "ENTRY", position_key, current_price, metrics, indicators, tracker_manager, score, rec, reason, dummy_state, dummy_lock, acc_logger)

#                     if success: 
#                         logger.info(f"🚀 {position_key} {action_type} SUCCESS {msg}")
#                         tracker_manager.registry.release_hedge_slot(account_key, symbol)
#                         async with tracker_manager._entry_candidates_lock:
#                             if position_key in tracker_manager.entry_candidates:
#                                 del tracker_manager.entry_candidates[position_key]
#                                 tracker_manager._entry_candidates_dirty[account_key] = True
#                         await tracker_manager.transition_to_exit(account_key, position_key, current_price, positionAmt, status='active')
#                         await record_trade_event(tracker_manager, account_key, position_key, symbol, action_type, position_side, current_price, qty, reason, False, False )
#                         await tracker_manager.save_tracker(account_key, force=True)
                        
#                 # logger.info(f" {position_key} before logger")
#                 # 7. LOGGING (Guaranteed to run now)

                
#                 # We update the throttle key to allow logs every 2 mins even if no action
#                 log_key = f"{account_key}:{position_key}:ENTRY"
                
#                 # Check k_1m valid
#                 if k_1m is None: 
#                     k_1m, d_1m = 0, 0
#                     reason += " [NO_DATA]"
#                 await log_stoch_snapshot(account_key, trade_manager, "ENTRY", position_key, current_price, metrics, indicators, tracker_manager, score, rec, reason, dummy_state, dummy_lock, acc_logger)

#                 # Update Check Time
#                 await tracker_manager.register_check(position_key)
#                 logger.debug(f" {position_key} k_1m:{k_1m} d_1m:{d_1m} k_3m:{k_3m} d_3m:{d_3m} k_15m:{indicators.get('stoch_k_15m')} d_15m:{indicators.get('stoch_d_15m')} - after logger")

#         except Exception as e:
#             logger.error(f"[ENTRY_CHECK_FAIL] {position_key}: {e}")
#             try: await tracker_manager.clear_processing(position_key)
#             except: pass
#     try:
#         async with asyncio.timeout(40.0):
#             await asyncio.gather(*(worker(k) for k in position_keys))
#     except asyncio.TimeoutError:
#         pass

async def cleanup_invalid_tracker_entries(tracker_manager: TrackerManager, account_key: str):
    """Remove invalid entries from tracker collections"""
    cleaned_count = 0
    
    # Clean entry_candidates
    async with tracker_manager._entry_candidates_lock:
        invalid_keys = []
        for key in list(tracker_manager.entry_candidates.keys()):
            if not key or ':' not in key:
                invalid_keys.append(key)
                cleaned_count += 1
        
        for key in invalid_keys:
            if key in tracker_manager.entry_candidates:
                del tracker_manager.entry_candidates[key]
                tracker_manager._entry_candidates_dirty[account_key] = True
    
    # Clean exit_candidates
    async with tracker_manager._exit_candidates_lock:
        invalid_keys = []
        for key in list(tracker_manager.exit_candidates.keys()):
            if not key or ':' not in key:
                invalid_keys.append(key)
                cleaned_count += 1
        
        for key in invalid_keys:
            if key in tracker_manager.exit_candidates:
                del tracker_manager.exit_candidates[key]
                tracker_manager._exit_candidates_dirty[account_key] = True
    
    if cleaned_count > 0:
        logger.info(f"[CLEANUP][{account_key}] Removed {cleaned_count} invalid entries")
        await tracker_manager.save_tracker(account_key, force=True)

async def log_scalping_action(account_key: str, position_key: str, action: str, reason: str, metrics: Dict[str, float]):
    """Log scalping-specific actions for monitoring"""
    k_1m = safe_fetch_float(metrics.get('k_1m', 50.0), 50.0)
    k_1m_prev = safe_fetch_float(metrics.get('k_1m_prev', k_1m), k_1m)
    d_1m = safe_fetch_float(metrics.get('d_1m', 50.0), 50.0)
    
    logger.info(f"⚡ [SCALPING][{account_key}] {action} {position_key} | "
                f"k_1m={k_1m:.1f}, k_1m_prev={k_1m_prev:.1f}, d_1m={d_1m:.1f} | "
                f"Reason: {reason}")


async def monitor_market_mode(config: Config):
    """Monitor market_mode.json and update config when ez_rankings changes the mode"""
    mode_file = config.DATA_DIR / "market_mode.json"
    last_mode = config.MARKET_MODE
    startup_time = time.time()
    try:
        last_seen_mtime = mode_file.stat().st_mtime if mode_file.exists() else 0.0
    except OSError:
        last_seen_mtime = 0.0
    logger.debug(f"📊 [ez_positions_quick] Market mode monitor initialized with mode from config.py: {last_mode}")
    while True:
        try:
            if mode_file.exists():
                try:
                    file_mtime = mode_file.stat().st_mtime
                except OSError:
                    file_mtime = 0.0
                async with FILE_IO_SEMAPHORE: 
                    async with aiofiles.open(mode_file, 'r') as f:
                        content = await f.read()
                        mode_data = json.loads(content)
                        new_mode = mode_data.get("mode", "NORMAL_MODE")
                        market_index = mode_data.get("market_index", 0.0)
                        update_ready = file_mtime > max(last_seen_mtime, startup_time)
                        if update_ready:
                            if new_mode != last_mode:
                                Config.set_market_mode(new_mode)
                                logger.info(f"📊 [ez_positions_quick] Market mode updated by ez_rankings: {last_mode} -> {new_mode} (Index: {market_index:.1f})")
                                logger.info(f"📊 [ez_positions_quick] Config.EXTREME_MODE={config.EXTREME_MODE}, Config.LIGHT_MODE={config.LIGHT_MODE}, Config.MARKET_MODE={config.MARKET_MODE}")
                                last_mode = new_mode
                            last_seen_mtime = file_mtime
                        elif new_mode != last_mode:
                            logger.debug(f"📊 [ez_positions_quick] Market mode manual override active: ignoring ez_rankings recommendation {new_mode}, retaining config mode {last_mode}")
        except Exception as e:
            logger.debug(f"[ez_positions_quick] Error checking market mode: {e}")
        await asyncio.sleep(90)


async def aggressive_hedge_scanner(trade_manager, account_key: str, tracker_manager: TrackerManager, hedge_engine: HedgeEngine):
    try:
        if not config.HEDGE_MODE:
            return
        active_positions = {}
        async with tracker_manager._exit_candidates_lock:
            for k, v in tracker_manager.exit_candidates.items(): 
                if k.startswith(f"{account_key}:") and v.get('status') == 'active': 
                    active_positions[k] = v.copy()
        for position_key, data in active_positions.items():
            try:
                if data.get('is_hedge', False) or data.get('hedge_for'):
                    continue
                symbol = position_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
                current_price, _ = await trade_manager.data_manager.get_fresh_price(symbol)
                if not current_price or current_price <= 0: current_price, _ = await get_current_price(symbol)
                if current_price <= 0: continue
                avg_entry = safe_fetch_float(data.get('average_entry_price', 0), 0)
                if avg_entry <= 0: continue
                is_long = position_key.endswith('_LONG')
                if is_long:
                    pnl_pct = ((current_price - avg_entry) / avg_entry) * 100
                else:
                    pnl_pct = ((avg_entry - current_price) / avg_entry) * 100
                if pnl_pct < -0.25:
                    already_hedged = False
                    async with tracker_manager._hedges_lock:
                        for hedge in tracker_manager.active_hedges:
                            if hedge.get('losing_position_key') == position_key:
                                already_hedged = True
                                break
                    if not already_hedged:
                        logger.info(f"🛡️ [HEDGE_SCANNER] {position_key} losing {pnl_pct:.2f}% -> Triggering hedge")
                        position_value = safe_fetch_float(data.get('current_pos_value', 0), 0)
                        if position_value <= 0:
                            qty = safe_fetch_float(data.get('positionAmt', 0), 0)
                            position_value = avg_entry * qty
                        await hedge_engine.execute_dual_hedge(account_key=account_key,losing_position_key=position_key,losing_symbol=symbol,losing_side='LONG' if is_long else 'SHORT',losing_value_usd=position_value, dry_run=False )
            except Exception as e:
                logger.error(f"[HedgeScanner] Error for {position_key}: {e}")
    except Exception as e:
        logger.error(f"[AggressiveHedgeScanner] Error: {e}")

async def force_emergency_sync(tracker_manager: TrackerManager, account_key: str, trade_manager):
    try:
        service = tracker_manager.positions_service or getattr(trade_manager, 'positions_service', None)
        if not service:
            return        
        positions = service.positions_by_account.get(account_key, {})
        real_active_keys = set()
        for position_key, pos in positions.items():
            if not position_key.startswith(account_key):
                continue
            position = await tracker_manager.get_position(position_key)
            if not position:  position = tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(position_key)
            if pos: tracker_manager._position_memory[position_key] = pos
            if not pos: pos = position
            positionAmt = abs(safe_fetch_float(getattr(pos, 'positionAmt', 0.0), 0.0))
            if positionAmt > 0:
                real_active_keys.add(position_key)
                async with tracker_manager._exit_candidates_lock:
                    if position_key not in tracker_manager.exit_candidates:
                        exit_data = tracker_manager._exit_template(
                            getattr(pos, 'entry_price', 0.0), 
                            positionAmt)
                        exit_data = tracker_manager._populate_from_position(exit_data, pos)
                        exit_data['status'] = 'active'
                        exit_data['timestamp'] = datetime.now(timezone.utc)
                        tracker_manager.exit_candidates[position_key] = exit_data
                        tracker_manager._exit_candidates_dirty[account_key] = True
                    else:
                        # UPDATE EXISTING with REAL data
                        exit_data = tracker_manager.exit_candidates[position_key]
                        exit_data = tracker_manager._populate_from_position(exit_data, pos)
                        exit_data['positionAmt'] = positionAmt
                        exit_data['status'] = 'active'
                        exit_data['timestamp'] = datetime.now(timezone.utc)
                        entry_price = safe_fetch_float(exit_data.get('average_entry_price', 0.0), 0.0)
                        mark_price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
                        is_long = position_key.endswith('_LONG')
                        
                        if entry_price > 0 and mark_price > 0:
                            if is_long:
                                current_gain = ((mark_price - entry_price) / entry_price) * 100.0
                                current_gain_usd = (mark_price - entry_price) * positionAmt
                            else:
                                current_gain = ((entry_price - mark_price) / entry_price) * 100.0
                                current_gain_usd = (entry_price - mark_price) * positionAmt
                            
                            exit_data['current_gain_%'] = current_gain
                            exit_data['current_gain_$'] = current_gain_usd
                            exit_data['mark_price'] = mark_price
                            exit_data['mark_price_last_updated'] = datetime.now(timezone.utc)
                        
                        tracker_manager.exit_candidates[position_key] = exit_data
                        tracker_manager._exit_candidates_dirty[account_key] = True
                
                # REMOVE from entry candidates (if it's there)
                async with tracker_manager._entry_candidates_lock:
                    if position_key in tracker_manager.entry_candidates:
                        del tracker_manager.entry_candidates[position_key]
                        tracker_manager._entry_candidates_dirty[account_key] = True
        
        # 3. CLEAN UP STALE exit candidates (not in real positions)
        async with tracker_manager._exit_candidates_lock:
            exit_keys = list(tracker_manager.exit_candidates.keys())
            for position_key in exit_keys:
                if not position_key.startswith(account_key):
                    continue
                
                candidate = tracker_manager.exit_candidates[position_key]
                
                if candidate.get('status') == 'PENDING_OPEN':
                    continue
                
                # Rule 2: If updated less than 10 seconds ago, trust local memory over API lag
                last_update = candidate.get('timestamp')
                if isinstance(last_update, datetime):
                    if (datetime.now(timezone.utc) - last_update).total_seconds() < 10.0:
                        continue
                # ----------------------------------------------------

                if position_key not in real_active_keys:
                    # Move to entry candidates with EXIT DATA
                    exit_data = tracker_manager.exit_candidates.pop(position_key, {})
                    tracker_manager._exit_candidates_dirty[account_key] = True
                    
                    # Save exit context for re-entry
                    if exit_data:
                        exit_data['status'] = 'entry_candidate'
                        exit_data['last_exit_timestamp'] = datetime.now(timezone.utc).isoformat()
                        exit_data['last_exit_reentry_ready'] = True
                        
                        # Try to get last price
                        last_price = exit_data.get('mark_price') or exit_data.get('last_reduction_price', 0.0)
                        if last_price <= 0:
                            # Try to get from position object
                            pos = positions.get(position_key)
                            if pos:
                                last_price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
                        
                        exit_data['last_reduction_price'] = last_price
                        exit_data['last_reduction_amount'] = exit_data.get('positionAmt', 0.0)
                        exit_data['positionAmt'] = 0.0
                        
                        async with tracker_manager._entry_candidates_lock:

                            existing_entry = tracker_manager.entry_candidates.get(position_key, {})
                            if existing_entry:
                                exit_data = tracker_manager._merge_history(exit_data, existing_entry)
                                
                            tracker_manager.entry_candidates[position_key] = exit_data
                            tracker_manager._entry_candidates_dirty[account_key] = True
        
        # 4. RECALCULATE ALL FIELDS for dirty candidates
        dirty_exits = []
        async with tracker_manager._exit_candidates_lock:
            for position_key in tracker_manager.exit_candidates:
                if position_key.startswith(account_key):
                    if tracker_manager.exit_candidates[position_key].get('_fields_fresh', False) == False:
                        dirty_exits.append(position_key)
        
        for position_key in dirty_exits:
            try:
                await tracker_manager.calculate_candidate_fields(
                    account_key, 'exit', position_key,
                    tracker_manager.exit_candidates[position_key],
                    trade_manager, tracker_manager.data_manager
                )
            except:
                pass
        
        # 5. IMMEDIATE SAVE
        await tracker_manager.save_tracker(account_key, force=True)
        
    except Exception as e:
        logger.error(f"[EM_SYNC][{account_key}] Failed: {e}", exc_info=True)


# ==============================================================================
# 1. PRIORITY EXIT LOOP (Fast - Checks active keys constantly)
# ==============================================================================
async def priority_exit_scan_loop(trade_manager, account_key: str, stop_event: asyncio.Event, redis_manager, tracker_manager: TrackerManager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine):
    logger.info(f"🛡️ [EXIT_PRIORITY][{account_key}] STARTED")
    last_summary = time.time()
    count = 0
    
    while not stop_event.is_set():
        try:
            # 1. Get ALL Active Keys
            async with tracker_manager._exit_candidates_lock:
                active_keys = list(k for k in tracker_manager.exit_candidates.keys() if k.startswith(account_key))
                if tracker_manager.positions_service:
                    positions = tracker_manager.positions_service.positions_by_account.get(account_key, {})
       
                    for k, pos in positions.items():
                        if not k.startswith(account_key): continue
                        amt = abs(safe_fetch_float(getattr(pos, 'positionAmt', 0.0), 0.0))
                        if amt > 0.0:
                            mark_price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
                            val = amt * mark_price
                            start_size = safe_fetch_float(getattr(trade_manager.config, 'START_POSITION_SIZE', 45.0), 45.0)
                            is_tradeable = k in tracker_manager.tradeable_keys
                            if val >= start_size or not is_tradeable:
                                active_keys.append(k)
            if not active_keys:
                if time.time() - last_summary > 60:
                    logger.info(f"🛡️ [EXIT_PRIORITY][{account_key}] Idle (0 active positions).")
                    last_summary = time.time()
                await asyncio.sleep(3.0)
                continue
            import random
            random.shuffle(active_keys)
            BATCH_SIZE = 10  # Smaller batch to allow interleaving
            
            for i in range(0, len(active_keys), BATCH_SIZE):
                batch = active_keys[i:i+BATCH_SIZE]
                tasks = []
                
                for key in batch:
                    # Logic: Only check if not currently processing
                    if await tracker_manager.is_processing(key): continue
                    
                    tasks.append(check_exit_candidates_for_account(
                        trade_manager, account_key, redis_manager, tracker_manager, 
                        order_queue, data_manager, hedge_engine, position_keys=[key]
                    ))
                    count += 1
                
                if tasks:
                    await asyncio.gather(*tasks)
                
                # Critical Yield to let other loops (Entry/Watchdog) run
                await asyncio.sleep(0.2)

            if time.time() - last_summary > 20:
                logger.info(f"🛡️ [EXIT_PRIORITY][{account_key}] Cycle OK. Checked {count} keys. Active: {len(active_keys)}")
                count = 0
                last_summary = time.time()

            # Pause between full cycles
            await asyncio.sleep(1.0)

        except Exception as e:
            logger.error(f"❌ [EXIT_PRIO] Error: {e}")
            await asyncio.sleep(5.0)

# ==============================================================================
# 2. MAINTENANCE EXIT LOOP (Thorough - Syncs with API)
# ==============================================================================
async def quick_exit_monitor_loop(trade_manager, account_key: str, stop_event: asyncio.Event, redis_manager, tracker_manager: TrackerManager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine):
    logger.info(f"🛡️ [EXIT_MAINT][{account_key}] STARTED")
    while not stop_event.is_set():
        try:
            # 1. Sync with API (The Truth)
            await tracker_manager._get_tradeable_keys_cached()
            await tracker_manager.sync_position_keys(account_key)
            # if key not in tracker_manager.tradeable_keys: 
            #     logger.info(f"🔍 {key} not in4 {tracker_manager.tradeable_keys}")
            #     return
            async with tracker_manager._exit_candidates_lock:
                all_exits = list(k for k in tracker_manager.exit_candidates.keys() if k.startswith(account_key))
            
            # Sort by Oldest Check Time
            all_exits.sort(key=lambda k: tracker_manager.last_check_times.get(k, 0.0))
            
            # Process One-by-One
            for key in all_exits:
                if await tracker_manager.is_processing(key): continue
                
                # Run Check
                await check_exit_candidates_for_account(
                    trade_manager, account_key, redis_manager, tracker_manager, 
                    order_queue, data_manager, hedge_engine, position_keys=[key]
                )
                await asyncio.sleep(0.05) 

            await asyncio.sleep(6.0)
        except Exception as e:
            logger.error(f"❌ [EXIT_MAINT] Error: {e}")
            await asyncio.sleep(5.0)

# ==============================================================================
# 3. STREAM ENTRY LOOP (Fast - Random Sampling)
# ==============================================================================
async def quick_entry_monitor_loop(trade_manager, account_key: str, stop_event: asyncio.Event, redis_manager, tracker_manager: TrackerManager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine):
    logger.info(f"🔭 [ENTRY_STREAM][{account_key}] STARTED")
    sem = asyncio.Semaphore(30)
    last_summary = time.time()
    processed_count = 0
    async def safe_check(key):
        async with sem:
            if key not in tracker_manager.tradeable_keys: 
                # logger.info(f"🔍 {key} not in2 {tracker_manager.tradeable_keys}")
                return
            else: await check_entry_candidates_for_account(trade_manager, account_key, redis_manager, tracker_manager, order_queue, data_manager, hedge_engine, position_keys=[key])

    while not stop_event.is_set():
        try:
            universe = list(tracker_manager.get_tradeable_position_keys_for(account_key))
            universe.sort()
            async with tracker_manager._exit_candidates_lock:
                active = set(tracker_manager.exit_candidates.keys())
            candidates = [k for k in universe if k not in active and k.startswith(account_key)]
            valid_candidates = []
            start_size = safe_fetch_float(getattr(trade_manager.config, 'START_POSITION_SIZE', 45.0), 45.0)
            for k in candidates:

                pos = tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(k)
                if pos:
                    amt = abs(safe_fetch_float(getattr(pos, 'positionAmt', 0.0), 0.0))
                    price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
                    if (amt * price) >= start_size:
                        continue 
                valid_candidates.append(k)
            if not candidates: 
                await asyncio.sleep(2.0); continue

            import random
            random.shuffle(candidates)
            
            # Process batches
            BATCH_SIZE = 50
            for i in range(0, len(candidates), BATCH_SIZE):
                batch = candidates[i:i+BATCH_SIZE]
                tasks = [asyncio.create_task(safe_check(k)) for k in batch]
                await asyncio.gather(*tasks)
                processed_count += len(batch)
                await asyncio.sleep(30)

            # Heartbeat Log
            if time.time() - last_summary > 60:
                logger.info(f"🔭 [ENTRY_STREAM][{account_key}] Running. Checked {processed_count} candidates in last 60s.")
                processed_count = 0
                last_summary = time.time()

        except Exception as e:
            logger.error(f"❌ [ENTRY_STREAM] Error: {e}")
            await asyncio.sleep(5.0)

# ==============================================================================
# 4. BULK ENTRY LOOP (Slow - Linear Scan)
# ==============================================================================
async def bulk_entry_scan_loop(trade_manager, account_key: str, stop_event: asyncio.Event, redis_manager, tracker_manager: TrackerManager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine):
    logger.info(f"🚜 [ENTRY_BULK][{account_key}] STARTED")
    while not stop_event.is_set():
        try:
            await tracker_manager.sync_universe(account_key)
            await tracker_manager.sync_position_keys(account_key)
            universe = list(tracker_manager.get_tradeable_position_keys_for(account_key))
            universe.sort() # Deterministic order

            count = 0
            for key in universe:
                # Skip if active
                async with tracker_manager._exit_candidates_lock:
                    if key in tracker_manager.exit_candidates: continue
                
                # Skip if recently checked (e.g. by stream loop)
                last_check = tracker_manager.get_last_check_time(key)
                if (time.time() - last_check) < 30.0: continue
                if key not in tracker_manager.tradeable_keys: 
                    # logger.info(f"🔍 {key} not in3 {tracker_manager.tradeable_keys}")
                    continue
                else:
                    await check_entry_candidates_for_account(
                        trade_manager, account_key, redis_manager, tracker_manager, 
                        order_queue, data_manager, hedge_engine, position_keys=[key]
                    )
                    
                count += 1
                await asyncio.sleep(0.1) # Gentle pace

            if count > 0:
                logger.info(f"🚜 [ENTRY_BULK][{account_key}] Sweep complete. Checked {count} keys.")
            
            await asyncio.sleep(40.0)

        except Exception as e:
            logger.error(f"❌ [ENTRY_BULK] Error: {e}", exc_info=True)
            await asyncio.sleep(10.0)

# ==============================================================================
# 1.5 SCALP MONITOR LOOP (Fast 10s Loop for Scalps only)
# ==============================================================================
async def quick_scalp_monitor_loop(trade_manager, account_key: str, stop_event: asyncio.Event, redis_manager, tracker_manager: TrackerManager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine):
    logger.info(f"🏎️ [SCALP_MONITOR][{account_key}] STARTED (10s Interval)")
    
    while not stop_event.is_set():
        try:
            # Only run if SCALP_MODE is global and account is in SCALP_ACCOUNTS
            scalp_mode = getattr(trade_manager.config, "SCALP_MODE", False)
            scalp_accounts = getattr(trade_manager.config, "SCALP_ACCOUNTS", [])
            
            if scalp_mode and account_key in scalp_accounts:
                # 1. Identify Scalp Keys
                scalp_keys = await tracker_manager.identify_active_scalps(account_key)
                
                if scalp_keys:
                    # logger.info(f"🏎️ [SCALP_MONITOR][{account_key}] Checking {len(scalp_keys)} active scalps...")
                    # Process Scalps Immediately (high priority)
                    await check_exit_candidates_for_account(
                        trade_manager, account_key, redis_manager, tracker_manager, 
                        order_queue, data_manager, hedge_engine, position_keys=scalp_keys, force=True
                    )
            
            await asyncio.sleep(10.0)
        except Exception as e:
            logger.error(f"❌ [SCALP_MONITOR] Error: {e}")
            await asyncio.sleep(5.0)

async def sla_miss_enforcer_loop(trade_manager, account_key: str, stop_event: asyncio.Event, redis_manager, tracker_manager: TrackerManager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine):
    """
    active_keys: Finds keys that haven't been checked in > 10s (Active) or > 300s (Entry).
    Forces a 'Ghost' log if they are found.
    """
    logger.info(f"👮 [SLA_ENFORCER][{account_key}] STARTED.")
    # await tracker_manager._get_tradeable_keys_cached()
    while not stop_event.is_set():
        try:
            now = time.time()
            async with tracker_manager._exit_candidates_lock:
                active_account_keys = list(k for k in tracker_manager.exit_candidates.keys() if k.startswith(account_key))
            filtered_active = []
            start_size = safe_fetch_float(getattr(trade_manager.config, 'START_POSITION_SIZE', 45.0), 45.0)
            for k in active_account_keys:
                pos_mem = tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(k)
                if pos_mem:
                    amt = abs(safe_fetch_float(getattr(pos_mem, 'positionAmt', 0.0), 0.0))
                    price = safe_fetch_float(getattr(pos_mem, 'mark_price', 0.0), 0.0)
                    is_tradeable = k in tracker_manager.tradeable_keys
                    if (amt * price) < start_size and is_tradeable:
                        continue
                filtered_active.append(k)
            active_account_keys = filtered_active
            
            for k in active_account_keys:
                last_time = tracker_manager.get_last_check_time(k)
                gap = now - last_time
                if gap > 40.0:
                    asyncio.create_task( check_exit_candidates_for_account( trade_manager, account_key, redis_manager, tracker_manager,  order_queue, data_manager, hedge_engine, position_keys=[k]  )  )
                else: pass

            # 2. CHECK ENTRIES (Waiting) - 5 Minutes (300s)
            if time.time() % 10 < 1.0:
                universe = list(tracker_manager.get_tradeable_position_keys_for(account_key))
                universe.sort()

                for k in universe:
                    # if k in active_account_keys: continue
                    last_time = tracker_manager.get_last_check_time(k)
                    gap = now - last_time
                    if k not in tracker_manager.tradeable_keys: continue
                    if gap > 180.0:
                        # if k not in tracker_manager.tradeable_keys: return
                        # else:

                        asyncio.create_task(
                            check_entry_candidates_for_account( trade_manager, account_key, redis_manager, tracker_manager, 
                                order_queue, data_manager, hedge_engine, position_keys=[k]   )  )
            await asyncio.sleep(12.0)

        except Exception as e:
            logger.error(f"❌ [SLA_LOOP][{account_key}] Error: {e}")
            await asyncio.sleep(22.0)

async def position_watchdog_loop(tracker_manager, trade_manager, account_keys: list, stop_event: asyncio.Event, redis_manager, order_queue, data_manager, hedge_engine):
    logger.info("👮 [WATCHDOG] Started - Includes Rescue File Listener & Direct Fetcher")
    await asyncio.sleep(10)
    last_direct_fetch = {k: 0.0 for k in account_keys}
    
    while not stop_event.is_set():
        try:
            for account_key in account_keys:
                # --- DIRECT EXCHANGE FETCH FOR ORPHANS ---
                now = time.time()
                if (now - last_direct_fetch.get(account_key, 0)) > 180.0:  # Every 3 minutes
                    last_direct_fetch[account_key] = now
                    try:
                        account = trade_manager.accounts.get(account_key)
                        if account and account.client:
                            logger.info(f"🕵️ [WATCHDOG_FETCH] {account_key}: Executing direct Binance fetch to find untracked positions...")
                            positions_data = await asyncio.wait_for(asyncio.to_thread(account.client.futures_position_information), timeout=15.0)
                            
                            exchange_open_keys = []
                            for p in positions_data:
                                amt = abs(safe_fetch_float(p.get('positionAmt', 0)))
                                if amt > 0:
                                    sym = p.get('symbol')
                                    pside = p.get('positionSide', 'LONG')
                                    key = f"{account_key}:{sym}_{pside}"
                                    exchange_open_keys.append((key, amt))
                            
                            # Check against tracker and inject immediately if missing
                            async with tracker_manager._exit_candidates_lock:
                                injected = []
                                for key, amt in exchange_open_keys:
                                    if key not in tracker_manager.exit_candidates:
                                        tracker_manager.exit_candidates[key] = tracker_manager._exit_template()
                                        tracker_manager.exit_candidates[key]['status'] = 'active'
                                        tracker_manager.exit_candidates[key]['last_reason'] = 'WATCHDOG_DIRECT_FETCH_RESCUE'
                                        tracker_manager.exit_candidates[key]['positionAmt'] = amt
                                        tracker_manager._exit_candidates_dirty[account_key] = True
                                        injected.append(key)
                                
                                if injected:
                                    logger.warning(f"🚨🚨 [CRITICAL_WATCHDOG] Found {len(injected)} UNTRACKED OPEN POSITIONS on exchange for {account_key}: {injected}. INJECTED IMMEDIATELY.")
                                    # Force service to update them too
                                    if tracker_manager.positions_service:
                                        asyncio.create_task(tracker_manager.positions_service.fetch_positions(account_key, priority='critical'))
                                        
                    except Exception as e:
                        logger.error(f"[WATCHDOG_FETCH] Error directly fetching positions for {account_key}: {e}")

                # --- RESCUE FILE ---
                rescue_file = config.DATA_DIR / f"rescue_keys_{account_key}.json"
                if await aio_os.path.exists(rescue_file):
                    try:
                        logger.info(f"🚑 [WATCHDOG] Found rescue package: {rescue_file}")
                        async with aiofiles.open(rescue_file, 'r') as f:
                            content = await f.read()
                            rescue_keys = json.loads(content)
                        
                        if rescue_keys:
                            logger.warning(f"🚑 [WATCHDOG] INJECTING {len(rescue_keys)} NEGLECTED KEYS: {rescue_keys}")
                            async with tracker_manager._exit_candidates_lock:
                                for key in rescue_keys:
                                    if key not in tracker_manager.exit_candidates:
                                        tracker_manager.exit_candidates[key] = tracker_manager._exit_template()
                                        tracker_manager.exit_candidates[key]['status'] = 'active'
                                        tracker_manager.exit_candidates[key]['last_reason'] = 'PA_PY_INJECTION'
                                        tracker_manager._exit_candidates_dirty[account_key] = True
                            asyncio.create_task(  check_exit_candidates_for_account(  trade_manager, account_key, redis_manager, tracker_manager,  order_queue, data_manager, hedge_engine, position_keys=rescue_keys, force=True  )  )
                        await aio_os.remove(rescue_file)
                    except Exception as e:
                        logger.error(f"[WATCHDOG] Rescue failed: {e}")
                now = time.time()
                stuck_keys = []
                async with tracker_manager._processing_lock:
                    for k, ts in list(tracker_manager._processing_orders.items()):
                        if k.startswith(account_key) and (now - ts) > 60.0:
                            stuck_keys.append(k)
                            del tracker_manager._processing_orders[k]
                if stuck_keys:
                    logger.warning(f"🔓 [WATCHDOG][{account_key}] Unlocked {len(stuck_keys)} stuck keys.")
                real_neglected_keys = []
                if tracker_manager.positions_service:
                    positions = tracker_manager.positions_service.positions_by_account.get(account_key, {})
                    for k, pos in positions.items():
                        if not k.startswith(account_key): continue
                        amt = abs(safe_fetch_float(getattr(pos, 'positionAmt', 0.0), 0.0))
                        mark_price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
                        if (amt * mark_price) < 5.0: continue
                        start_size = safe_fetch_float(getattr(trade_manager.config, 'START_POSITION_SIZE', 45.0), 45.0)
                        val = amt * mark_price
                        is_tradeable = k in tracker_manager.tradeable_keys
                        if val < start_size and is_tradeable:
                            continue
                        last_check = tracker_manager.get_last_check_time(k)
                        gap = now - last_check
                        if gap > 60.0:
                            real_neglected_keys.append(k)
                            
                    if real_neglected_keys:
                        logger.warning(f"👮 [WATCHDOG][{account_key}] Found {len(real_neglected_keys)} NEGLECTED real positions (Gap > 90s). Rescue initiated.")
                        
                        # Force inject
                        async with tracker_manager._exit_candidates_lock:
                            for nk in real_neglected_keys:
                                if nk not in tracker_manager.exit_candidates:
                                    logger.info(f"➕ [WATCHDOG] Injecting {nk} into tracker.")
                                    tracker_manager.exit_candidates[nk] = tracker_manager._exit_template()
                                    tracker_manager.exit_candidates[nk]['status'] = 'active'
                                    tracker_manager.exit_candidates[nk]['last_reason'] = 'WATCHDOG_RESCUE'
                                    tracker_manager._exit_candidates_dirty[account_key] = True

                        # Force Check
                        asyncio.create_task(
                            check_exit_candidates_for_account(
                                trade_manager, account_key, redis_manager, tracker_manager,
                                order_queue, data_manager, hedge_engine, position_keys=real_neglected_keys, force=True
                            )
                        )

                # 3. STANDARD SYNC
                await tracker_manager.sync_universe(account_key)
                await tracker_manager.sync_position_keys(account_key)

                
                # 4. HANDLE STALE ENTRY CANDIDATES
                async with tracker_manager._entry_candidates_lock:
                    waiting_keys = [k for k in tracker_manager.entry_candidates.keys() if k.startswith(account_key)]
                    for k in waiting_keys:
                        if k not in tracker_manager.tradeable_position_keys.get(account_key, set()): continue

                stale_entries = []
                for k in waiting_keys:
                    last_check = tracker_manager.get_last_check_time(k)
                    if (now - last_check) > 300.0: 
                        stale_entries.append(k)
                
                if stale_entries:
                    batch = stale_entries[:50]
                    asyncio.create_task(
                        check_entry_candidates_for_account(
                            trade_manager, account_key, redis_manager, tracker_manager,
                            order_queue, data_manager, hedge_engine, position_keys=batch, force=True
                        )
                    )

            await asyncio.sleep(15.0)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[WATCHDOG] Critical Error: {e}", exc_info=True)
            await asyncio.sleep(10.0)

async def global_system_watchdog(tracker_manager, stop_event):
    logger.info("👮 [SYSTEM_WATCHDOG] Started. Monitoring Event Loop latency.")
    while not stop_event.is_set():
        try:
            start = time.time()
            await asyncio.sleep(10)
            end = time.time()
            lag = (end - start) - 10.0
            if lag > 15.0:
                logger.critical(f"💀 [SYSTEM_WATCHDOG] EVENT LOOP BLOCKED (Lag: {lag:.2f}s). SUICIDE RESTART.")
                import os
                import sys
                sys.stdout.flush()
                os._exit(1)
        except Exception as e:
            logger.error(f"[SYSTEM_WATCHDOG] Error: {e}")
            await asyncio.sleep(20)

async def global_data_watchdog(tracker_manager, stop_event):
    """Monitors the Data Stream. If BTC stops ticking, the WS is dead."""
    logger.info("🐶 [DATA_WATCHDOG] Started. Monitoring BTCUSDC heartbeat.")
    await asyncio.sleep(60) # Allow startup time
    
    while not stop_event.is_set():
        try:
            # Check BTC Freshness from StreamOHLC
            # (StreamOHLC is updated by the WebSocket loop)
            btc_ts = tracker_manager.stream_ohlc.get_last_tick_time("BTCUSDC")
            now = time.time()
            lag = now - btc_ts
            
            # If BTC hasn't updated in 45s, the WebSocket is a Zombie.
            # We exit with code 1 so Docker/Supervisor restarts us fresh.
            if btc_ts > 0 and lag > 45.0:
                logger.critical(f"💀 [DATA_WATCHDOG] SYSTEM FROZEN (BTC Lag: {lag:.1f}s). SUICIDE RESTART.")
                import os
                import sys
                sys.stdout.flush()
                os._exit(1) # Hard Kill
            
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"[DATA_WATCHDOG] Error: {e}")
            await asyncio.sleep(10)
            
async def shared_memory_watchdog(data_manager, stop_event):
    logger.info("🧠 [SHARdfdfED_MEM] Watchdog Started.")
    while not stop_event.is_set():
        if data_manager.shared_proxy is None:
            try:
                client = get_shared_memory_client()
                if client:
                    data_manager.register_shared_memory(client.get_store())  # pylint: disable=no-member
                    # Test read
                    data_manager.shared_proxy.get_symbol("BTCUSDC") 
                    logger.info("🧠 [SHARsdfsaED_MEMx] Re-connected successfully.")
            except Exception:
                pass
        await asyncio.sleep(5)
        
async def periodic_leaderboard_refresh(trade_manager):
    """Keeps the local trade_manager instance updated with latest rankings file data"""
    while True:
        await asyncio.sleep(60) # Refresh every minute
        try:
            _refresh_last_events_cache()
            await manual_load_leaderboards(trade_manager)
        except Exception as e:
            logger.error(f"Leaderboard refresh failed: {e}")

async def direct_position_injection(tracker_manager: TrackerManager, account_key: str, trade_manager):
    try:
        service = tracker_manager.positions_service
        if not service:
            return
        await tracker_manager._get_tradeable_keys_cached()
        positions = service.positions_by_account.get(account_key, {})
        for position_key, pos in positions.items():
            if not position_key.startswith(account_key):
                continue
            positionAmt = abs(safe_fetch_float(getattr(pos, 'positionAmt', 0.0), 0.0))
            if positionAmt > 0:
                async with tracker_manager._exit_candidates_lock:
                    exit_data = tracker_manager._exit_template(getattr(pos, 'entry_price', 0.0),  positionAmt  )
                    exit_data = tracker_manager._populate_from_position(exit_data, pos)
                    exit_data['status'] = 'active'
                    exit_data['timestamp'] = datetime.now(timezone.utc)
                    tracker_manager.exit_candidates[position_key] = exit_data
                    tracker_manager._exit_candidates_dirty[account_key] = True
                    symbol = position_key.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
                    mark_price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
                    logger.info(f"[DIRECT_INJECT][{account_key}] {symbol}: posAmt={positionAmt:.6f}, mark={mark_price:.4f}")           
            else:
                async with tracker_manager._entry_candidates_lock:
                    if position_key not in tracker_manager.entry_candidates:
                        entry_data = tracker_manager._entry_template()
                        entry_data['status'] = 'entry_candidate'
                        entry_data['last_exit_timestamp'] = datetime.now(timezone.utc).isoformat()
                        entry_data['last_exit_reentry_ready'] = True
                        tracker_manager.entry_candidates[position_key] = entry_data
                        tracker_manager._entry_candidates_dirty[account_key] = True
                        updates_made = True
        await tracker_manager.save_tracker(account_key, force=True)
    except Exception as e:
        logger.error(f"[DIRECT_INJECT][{account_key}] Failed: {e}")
_metric_watchdog = {}

async def run_initial_unified_batch(trade_manager, active_account_keys: list, tracker_manager: TrackerManager, redis_manager, order_queue, data_manager: FastDataManager, hedge_engine: HedgeEngine):
    logger.info(f"\n{'='*60}\n🚜 [STARTUP] Running INITIAL UNIFIED BATCH for {len(active_account_keys)} accounts...\n{'='*60}")
    keys = await tracker_manager._get_tradeable_keys_cached()
    logger.info(f"🔑 [UNIVERSE] Loaded {len(keys)} total tradeable keys.")
    await asyncio.sleep(2.0) 
    if not tracker_manager.tradeable_keys:
        logger.info("forcing tradeable keys load...")
        await tracker_manager._get_tradeable_keys_cached()
    for account_key in active_account_keys:
        try:
            logger.info(f"   >>> [BATCH] Processing Account: {account_key}")
            universe = list(tracker_manager.get_tradeable_position_keys_for(account_key))
            await tracker_manager.sync_universe(account_key)
            await tracker_manager.sync_position_keys(account_key)
            async with tracker_manager._exit_candidates_lock:
                active_keys = {k for k in tracker_manager.exit_candidates.keys() if k.startswith(account_key)}
            waiting_keys = {k for k in universe if k not in active_keys}
            start_size = safe_fetch_float(getattr(trade_manager.config, 'START_POSITION_SIZE', 45.0), 45.0)
            final_waiting = set()
            for k in waiting_keys:
                pos = tracker_manager.positions_service.positions_by_account.get(account_key, {}).get(k)
                if pos:
                    amt = abs(safe_fetch_float(getattr(pos, 'positionAmt', 0.0), 0.0))
                    price = safe_fetch_float(getattr(pos, 'mark_price', 0.0), 0.0)
                    if (amt * price) >= start_size:
                        active_keys.add(k)
                final_waiting.add(k)
            waiting_keys = final_waiting
            async with tracker_manager._exit_candidates_lock:
                active_keys = {k for k in tracker_manager.exit_candidates.keys() if k.startswith(account_key)}
            queue_items = []
            for k in active_keys:
                queue_items.append({'key': k, 'type': 'EXIT'})
            for k in waiting_keys:
                queue_items.append({'key': k, 'type': 'ENTRY'})
            total_items = len(queue_items)
            logger.info(f"   >>> [BATCH] Found {total_items} items ({len(active_keys)} Active, {len(waiting_keys)} Waiting)")
            if total_items == 0: continue
            BATCH_SIZE = 60 
            
            for i in range(0, total_items, BATCH_SIZE):
                batch = queue_items[i:i + BATCH_SIZE]
                logger.info(f"       ... Batch {i//BATCH_SIZE + 1}/{(total_items//BATCH_SIZE)+1} ({len(batch)} items)")

                async def process_item(item):
                    key = item['key']
                    try:
                        await check_exit_candidates_for_account( trade_manager, account_key, redis_manager, tracker_manager,   order_queue, data_manager, hedge_engine, position_keys=[key] )
                        if k not in tracker_manager.tradeable_keys and k not in tracker_manager.tradeable_keys_cache and k not in tracker_manager.tradeable_position_keys.get(account_key, set()): return
                        await check_entry_candidates_for_account(trade_manager, account_key, redis_manager, tracker_manager,  order_queue, data_manager, hedge_engine, position_keys=[key] )
                    except Exception as e:
                        logger.error(f"[InitBatchError] {key}: {e}")
                await asyncio.gather(*(process_item(item) for item in batch))
                await asyncio.sleep(10)
            logger.info(f"   ✅ [BATCH] Completed {account_key}")
        except Exception as e:
            logger.error(f"❌ [BATCH] Failed for {account_key}: {e}")
    logger.info(f"{'='*60}\n✅ [STARTUP] INITIAL BATCH COMPLETE. Releasing High-Frequency Monitors.\n{'='*60}")

async def global_ranker_loop(trade_manager, tracker_manager, data_manager, registry, stop_event):
    logger.info("🔥 [GLOBAL_RANKER] Started - Symmetrical monitoring enabled.")
    while not stop_event.is_set():
        try:
            symbols = list(trade_manager.symbols_active)
            if not symbols:
                await asyncio.sleep(5); continue

            for symbol in symbols:
                metrics, indicators, k_1m, d_1m, k_3m, d_3m, is_fresh = await data_manager.get_hot_state(symbol)
                if not is_fresh: continue
                current_price = safe_fetch_float(indicators.get('current_price', 0.0))
                if current_price <= 0: continue
                for side in ["LONG", "SHORT"]:
                    is_long = (side == "LONG")
                    prev_cross = _get_prev_cross_price(symbol, is_long)
                    res = await AdvancedSignalRater.rate(
                        "global", symbol, is_long, current_price, metrics, indicators, 
                        prev_cross, is_exit=False, is_allowed=True, tracker_manager=tracker_manager )
                    await registry.update(symbol, side, res + (time.time(),))

            await registry.refresh_rankings()
            await asyncio.sleep(0.5) 

        except Exception as e:
            logger.error(f"❌ [GLOBAL_RANKER] Crash: {e}")
            await asyncio.sleep(5)

async def main(account_key_filter: Optional[str] = None) -> None:
    try:
        global shared_indicators_proxy
        config = Config()
        mode = "PAPER" if getattr(config, 'PAPER_TRADING_QUICK', False) else "LIVE"
        if account_key_filter:
            if account_key_filter not in ["ang", "inf", "flz", "men", "fin"]:
                logger.error(f"❌ Invalid account filter: {account_key_filter}")
                return
            logger.info(f"🔒 [STARTUP] STRICT MODE: Locked to {account_key_filter}")
            config.ACCOUNT_KEYS = [account_key_filter]
            allowed_accounts = frozenset([account_key_filter])
            target_account = account_key_filter
            get_account_logger(account_key_filter)
        else:
            logger.info(f"🌍 [STARTUP] GLOBAL MODE: All Accounts")
            allowed_accounts = frozenset(["ang", "inf", "flz", "men", "fin"])
            config.ACCOUNT_KEYS = list(allowed_accounts)
            target_account = None
        logger.info(f"\n{'='*60}\n[ez_positions_quick] MODE: {mode} | PID: {os.getpid()} | ACCOUNTS: {list(allowed_accounts)}\n{'='*60}")
        all_accs = await asyncio.wait_for(asyncio.to_thread(load_accounts, config), timeout=30.0)
        
        accounts = {k: v for k, v in all_accs.items() if k in allowed_accounts}
        
        if not accounts:
            logger.critical("❌ No valid accounts loaded. Exiting.")
            return

        # 3. LOAD SYMBOLS
        try:
            async with aiofiles.open(config.SYMBOLS_FILE, 'r') as f:
                content = await f.read()
                symbols_from_file = json.loads(content)
        except Exception as e:
            logger.critical(f"CRITICAL: Could not read symbols.json: {e}")
            raise e 
        if not isinstance(symbols_from_file, list): symbols_from_file = []
        symbols = force_usdc_in_list(symbols_from_file, set())

        redis_manager = SimpleRedisManager()
        await redis_manager.initialize()

        logger.info("[epq] Bootstrapping Position Service (Robust Mode)...")
        positions_service = None
        
        # Retry loop for service bootstrap
        for attempt in range(1, 6):
            try:
                positions_service = await asyncio.wait_for(
                    bootstrap_position_service(logger=logger, accounts=accounts, enable_auto_fetch=False, start_maintenance=False, load_priority='redis'), 
                    timeout=300.0  )
                if positions_service:
                    break
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"⚠️ [STARTUP] Bootstrap attempt {attempt}/5 failed: {e}. Retrying in 5s...")
                await asyncio.sleep(5)
        
        # if not positions_service:
        #     logger.critical("❌ Failed to bootstrap positions_service after 5 attempts. Continuing in DEGRADED mode (Direct API Fallback).")
        #     # Create a dummy service container so the bot doesn't crash on None access
        #     class DummyService:
        #         def __init__(self): self.positions_by_account = {}
        #         def positions(self): return {}
        #     positions_service = DummyService()
        
        # Filter flattened positions
        sanitized_positions = {}
        for pos_key, pos_obj in positions_service.positions.items():
            acc_prefix = pos_key.split(':')[0]
            if acc_prefix in allowed_accounts:
                sanitized_positions[pos_key] = pos_obj
        positions_service.positions = sanitized_positions
        logger.info(f"🧹 [SANITIZATION] Retained data for: {list(positions_service.positions_by_account.keys())}")
        # ------------------------------------------------------

        trade_manager = MultiAccountTradeManager(accounts, symbols, use_dummy_lock=True, positions_service=positions_service)
        trade_manager._allowed_accounts = allowed_accounts 
        
        order_queue = OrderQueue(trade_manager, max_concurrent_orders=config.MAX_CONCURRENT_ORDERS)
        trade_manager.set_order_queue(order_queue)
        positions_service.trade_manager = trade_manager
        await asyncio.wait_for(trade_manager.init_async(), timeout=30.0)
        
        base_path = Path(config.BASE_PATH) if hasattr(config, 'BASE_PATH') else Path.home() / 'binance'
        data_path = Path(config.DATA_DIR) if hasattr(config, 'DATA_DIR') else Path.home() / 'binance' / 'data'
        
        tracker_manager = TrackerManager(base_path, trade_manager=trade_manager, positions_service=positions_service, target_account=target_account, redis_manager=redis_manager, registry=None, data_manager=None)
        tracker_manager.accounts = accounts
        tracker_manager._allowed_accounts = allowed_accounts
        trade_manager.tracker_manager = tracker_manager
        
        data_manager = FastDataManager(redis_manager, data_path, trade_manager=trade_manager, ws_manager=None, positions_service=positions_service)
        
        trade_manager.data_manager = data_manager
        tracker_manager.data_manager = data_manager

        registry = RatingRegistry(trade_manager, tracker_manager, data_manager, config)
        tracker_manager.registry = registry
        await load_initial_market_data(data_manager, config)
        await asyncio.wait_for(trade_manager.initialize_indicators_bridge(), timeout=30.0)
        

        logger.info("[epq] 🧠 Linking Data Manager to Shared Memory...")
        connected = False
        for _ in range(150):
            try:
                from ez_share_ind import get_shared_memory_client
                client = get_shared_memory_client()
                if client:
                    data_manager.register_shared_memory(client.get_store())  # pylint: disable=no-member
                    # Test Read
                    test_read = data_manager.shared_proxy.get_symbol("BTCUSDC")
                    if test_read:
                        ts = test_read.get('_tick_ts', 0)
                        age = time.time() - ts
                        logger.info(f"✅ [erweeewrreM] Connected! BTC Age: {age:.1f}s")
                        connected = True
                        break
            except Exception as e:
                logger.warning(f"⚠️ [asfsadfaM] Connection attempt failed: {e}")
            await asyncio.sleep(1)
            
        if not connected:
            logger.critical("🚨 [DATA_FAILURE] Could not connect to Shared Memory. Bot will be BLIND.")

        hedge_engine = HedgeEngine(trade_manager, tracker_manager, data_manager, config, redis_manager, positions_service=positions_service, registry=registry)
        hedge_engine.registry = registry
        trade_manager.hedge_engine = hedge_engine
        tracker_manager.hedge_engine = hedge_engine
        sentiment_strategy = SentimentMomentumStrategy(trade_manager, tracker_manager, data_manager, config, registry )
        sentiment_manager = SentimentExposureManager(trade_manager, tracker_manager, data_manager, config, registry)                  
        if hasattr(positions_service, '_ensure_hedge_engine_initialized'):
            await positions_service._ensure_hedge_engine_initialized()
            
        await tracker_manager._get_tradeable_keys_cached()

        logger.info("[epq] Linking Data Manager...")
        if trade_manager.indicators_bridge and trade_manager.indicators_bridge.shared_proxy:
            shared_proxy = trade_manager.indicators_bridge.shared_proxy
            data_manager.register_shared_memory(shared_proxy)
            logger.info("✅ Data Manager linked to Trade Manager's Shared Memory Bridge")
        else:
            logger.warning("⚠️ Manual connection for Data Manager...")
            for _ in range(5):
                try:
                    from ez_share_ind import get_shared_memory_client
                    client = get_shared_memory_client()
                    if client:
                        data_manager.register_shared_memory(client.get_store())  # pylint: disable=no-member
                        break
                except Exception:
                    await asyncio.sleep(1)

        # 6. INITIALIZE ACCOUNT STATES (One by One)
        for acc in allowed_accounts:
            logger.info(f"[STARTUP] >> Init {acc}...")
            async def init_acc():
                await tracker_manager.load_tracker(acc)
                logger.info(f"... [{acc}] Injecting Positions")
                await direct_position_injection(tracker_manager, acc, trade_manager)
                logger.info(f"... [{acc}] Cleaning Hedges")
                await hedge_engine.cleanup_infinite_hedges(acc)
                logger.info(f"... [{acc}] Loading Records")
                await trade_manager.hedge_engine.load_hedge_records(acc)

            try: 
                await asyncio.wait_for(init_acc(), timeout=40.0)
            except asyncio.TimeoutError:
                logger.error(f"[STARTUP]  TIMEOUT on {acc} - proceeding anyway")
            except Exception as e: 
                logger.error(f"[STARTUP] ❌ Failed {acc}: {e}")
                
        try: #TEMP OUT
            logger.info("[STARTUP] Loading Symbols (Quick Mode)...")
            await asyncio.wait_for(load_symbols_quick(trade_manager), timeout=10.0)
        except Exception as e: logger.error(f"⚠️ [STARTUP] Symbol load failed: {e}")

        # 7. PREPARE BACKGROUND TASKS
        # Define stop_event explicitly HERE before creating tasks
        stop_event = asyncio.Event()
        
        async def periodic_lock_cleanup_loop():
            logger.info("🧹 [CLEANUP_TASK] Started")
            while not stop_event.is_set():
                try:
                    await asyncio.sleep(300.0)
                    await trade_manager.cleanup_stale_execution_locks()
                    if tracker_manager: await tracker_manager.cleanup_stale_temp_files()
                except asyncio.CancelledError: break
                except Exception: await asyncio.sleep(30)

        async def hedge_monitoring_loop():
            while not stop_event.is_set():
                try:
                    await asyncio.sleep(10)
                    for ak in allowed_accounts:
                        await aggressive_hedge_scanner(trade_manager, ak, tracker_manager, hedge_engine)
                except asyncio.CancelledError: break
                except Exception: await asyncio.sleep(10)

        all_background_tasks = {}
        
        # Global Tasks
        all_background_tasks['heartbeat'] = asyncio.create_task(periodic_heartbeat_update(list(allowed_accounts)))
        all_background_tasks['monitor_market_mode'] = asyncio.create_task(monitor_market_mode(config))
        all_background_tasks['periodic_lock_cleanup'] = asyncio.create_task(periodic_lock_cleanup_loop())
        all_background_tasks['leaderboard_refresh'] = asyncio.create_task(periodic_leaderboard_refresh(trade_manager))
        # Tracker Task
        all_background_tasks['periodic_field_refresh'] = asyncio.create_task(tracker_manager.periodic_field_refresh(stop_event, list(allowed_accounts)))
        all_background_tasks['realtime_monitoring'] = asyncio.create_task(tracker_manager.realtime_monitoring_task(stop_event, list(allowed_accounts), trade_manager, data_manager))
        all_background_tasks['global_ranker'] =  asyncio.create_task(global_ranker_loop(trade_manager, tracker_manager, data_manager, registry, stop_event))

        # Hedge Tasks
        all_background_tasks['hedge_monitoring'] = asyncio.create_task(hedge_monitoring_loop())
        all_background_tasks['hedge_balancing'] = asyncio.create_task(hedge_engine.monitor_hedge_health_loop(stop_event))
        all_background_tasks['sentiment_manager'] = asyncio.create_task(sentiment_manager.run_loop(stop_event)) #TEMP OUT
        active_targets = []
        for acc in sentiment_strategy.account_rules.keys():
            if acc in allowed_accounts:
                active_targets.append(acc)
                
        if active_targets:
            all_background_tasks['sentiment_strategy'] = asyncio.create_task(
                sentiment_strategy.run_loop(stop_event)
            )

        if 'flz' in allowed_accounts:
            all_background_tasks['sentiment_strategy'] = asyncio.create_task(
                sentiment_strategy.run_loop(stop_event)  )
        # 8. START WEBSOCKETS
        websocket_managers = {}
        for account_key in allowed_accounts:
            account = accounts.get(account_key)
            if account and getattr(account, 'api_key', None):
                ws = WebSocketManager([account_key], account.api_key, account.api_secret, config, tracker_manager, trade_manager, data_manager, positions_service)
                websocket_managers[account_key] = ws
                all_background_tasks[f'websocket_{account_key}'] = asyncio.create_task(ws.start())

        # 9. SYNC UNIVERSE & RUN INITIAL BATCH
        if len(tracker_manager.tradeable_keys) < 10:
            await tracker_manager._get_tradeable_keys_cached()
            asyncio.sleep(20)
        if len(tracker_manager.tradeable_keys) > 10:
            for acc in allowed_accounts:
                keys = tracker_manager.get_tradeable_position_keys_for(acc)
                await tracker_manager.sync_universe(acc)
                logger.info(f"[epq] Account {acc} has {len(keys)} tradeable keys.")
                await run_initial_unified_batch(trade_manager, [acc], tracker_manager, trade_manager.redis_manager, order_queue, data_manager, hedge_engine)

        monitor_tasks={}
        logger.info(f"[epq] Startup complete. Entering supervisory loop for {list(allowed_accounts)}.")

        # 10. START PER-ACCOUNT MONITORING LOOPS
        for account_key in allowed_accounts:
            if f"exit_prio_{account_key}" not in monitor_tasks:
                monitor_tasks[f"exit_prio_{account_key}"] = asyncio.create_task(priority_exit_scan_loop(trade_manager, account_key, stop_event, trade_manager.redis_manager, tracker_manager, order_queue, data_manager, hedge_engine))
                logger.info(f"✅ [LOOP] exit_prio_{account_key}")

            if f"exit_maint_{account_key}" not in monitor_tasks:
                monitor_tasks[f"exit_maint_{account_key}"] = asyncio.create_task(quick_exit_monitor_loop(trade_manager, account_key, stop_event, redis_manager, tracker_manager, order_queue, data_manager, hedge_engine))
                logger.info(f"✅ [LOOP] exit_maint_{account_key}")
                
            if f"entry_bulk_{account_key}" not in monitor_tasks:
                 monitor_tasks[f"entry_bulk_{account_key}"] = asyncio.create_task(bulk_entry_scan_loop(trade_manager, account_key, stop_event, redis_manager, tracker_manager, order_queue, data_manager, hedge_engine))
                 logger.info(f"✅ [LOOP] entry_bulk_{account_key}")        
            
            if f"entry_stream_{account_key}" not in monitor_tasks:
                 monitor_tasks[f"entry_stream_{account_key}"] = asyncio.create_task(quick_entry_monitor_loop(trade_manager, account_key, stop_event, redis_manager, tracker_manager, order_queue, data_manager, hedge_engine))
                 logger.info(f"✅ [LOOP] entry_stream_{account_key}")

            if f"watchdog_{account_key}" not in monitor_tasks:
                 monitor_tasks[f"watchdog_{account_key}"] = asyncio.create_task(position_watchdog_loop(tracker_manager, trade_manager, [account_key], stop_event, redis_manager, order_queue, data_manager, hedge_engine))
                 logger.info(f"✅ [LOOP] watchdog_{account_key}")

            if f"sla_{account_key}" not in monitor_tasks:
                monitor_tasks[f"sla_{account_key}"] = asyncio.create_task(sla_miss_enforcer_loop(trade_manager, account_key, stop_event, redis_manager, tracker_manager, order_queue, data_manager, hedge_engine))
                monitor_tasks[f"scalp_{account_key}"] = asyncio.create_task(quick_scalp_monitor_loop(trade_manager, account_key, stop_event, redis_manager, tracker_manager, order_queue, data_manager, hedge_engine))
                logger.info(f"✅ [LOOP] sla_{account_key}")

        logger.info("✅ [SYSTEM] All loops started. Holding main process open.")
        await stop_event.wait()
        
    except asyncio.CancelledError:
        logger.info("[ez_positions_quick] Main task cancelled.")

    finally:
        if 'stop_event' in locals():
            stop_event.set()
        
        if 'websocket_managers' in locals():
            for ws in websocket_managers.values():
                await ws.stop()   

if __name__ == "__main__":
    import sys
    import traceback
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    account_key_arg = None
    if len(sys.argv) > 1:
        for i, arg in enumerate(sys.argv):
            if arg == "--account" and i + 1 < len(sys.argv): 
                account_key_arg = sys.argv[i + 1]
                break
    restart_delay = 5
    while True:
        try:
            asyncio.run(main(account_key_filter=account_key_arg))            
            logger.info(f"[ez_positions_quick] Service exited cleanly. Restarting in {restart_delay}s...")
            time.sleep(restart_delay)
        except KeyboardInterrupt:
            logger.info("[ez_positions_quick] Keyboard interrupt - exiting immediately")
            os._exit(0)
        except Exception as exc:
            print(f"\n{'!'*60}")
            print(f"🚨 CRITICAL CRASH DETECTED")
            print(f"{'!'*60}")
            traceback.print_exc()
            print(f"{'!'*60}\n")
            time.sleep(restart_delay)

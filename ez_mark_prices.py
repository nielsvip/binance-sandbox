import asyncio
import websockets
import json
import orjson
import contextvars
from contextvars import ContextVar
import ssl
import sys
import time
import signal
import certifi
import redis.asyncio as redis
from redis import exceptions as redis_exceptions
import logging
from pathlib import Path
import aiohttp
import pandas as pd
from io import StringIO
import tempfile
import os, socket
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from collections import deque
from contextlib import suppress
import uuid
import aiofiles
from dateutil.parser import isoparse
# from utils import get_gateway_redis_config #setup_gateway_redis_ssh_tunnel,, orjson_default
try:
    from typing import Any, Dict, List, Optional, Union
    import orjson
    def default_json_serializer(obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        raise TypeError
    def json_dumps(obj: Any, **kwargs) -> bytes:
        option = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS  # pylint: disable=no-member
        return orjson.dumps(obj, default=default_json_serializer, option=option)  # pylint: disable=no-member
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if isinstance(s, str):
            s = s.encode('utf-8')
        return orjson.loads(s)  # pylint: disable=no-member
    JSONDecodeError = orjson.JSONDecodeError  # pylint: disable=no-member
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
    import netifaces
    NETIFACES_AVAILABLE = True
except ImportError:
    NETIFACES_AVAILABLE = False
from utils import logger, current_account, get_live_usdc_pairs, force_usdc_in_list, get_current_environment,setup_logger_with_rotation, _resolve_klines_directories, orjson_default
from config import Config
config = Config()
logger = setup_logger_with_rotation("ez_mark_prices", "ez_mark_prices.log")

ENVIRONMENT = get_current_environment()['env']
redis_local_client: Optional[redis.Redis] = None
redis_gateway_client: Optional[redis.Redis] = None
_gateway_redis_failures = 0

_redis_reconnect_attempts = {}
_last_redis_warning_time = {}
_last_redis_reconnect_attempt = {}
MAX_REDIS_WARNING_INTERVAL = 60
REDIS_RECONNECT_COOLDOWN = 10  # seconds between reconnection attempts
# --- START RESILIENT I/O HELPERS ---

def _convert_timestamp_strings(data: Any) -> Any:
    """Recursively convert all timestamp strings in dict/list to datetime objects."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(value, str) and any(ts_key in key.lower() for ts_key in ['time', 'timestamp', 'updated', 'at', 'date']):
                try:
                    # Quick check for ISO-like format before expensive parse
                    if len(value) > 10 and ('-' in value or 'T' in value):
                        parsed = isoparse(value)
                        result[key] = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
                    else:
                        result[key] = value
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

async def atomic_write_json(file_path: Path, data: Any):
    """Atomically write JSON file with unique temp name and fsync."""
    random_suffix = uuid.uuid4().hex
    temp_file = file_path.with_name(f".{file_path.name}.{random_suffix}.tmp")
    
    try:
        # Create parent directory if needed
        if not file_path.parent.exists():
            await asyncio.to_thread(file_path.parent.mkdir, parents=True, exist_ok=True)
        
        # Serialize
        try:
            # Use the global json_dumps defined in ez_prices (handles numpy/orjson)
            content = json_dumps(data)
            if isinstance(content, str):
                content = content.encode('utf-8')
        except Exception:
            # Fallback
            content = json.dumps(data, indent=2, default=str).encode('utf-8')

        # Write binary
        async with aiofiles.open(temp_file, 'wb') as f:
            await f.write(content)
            await f.flush()
            # Force write to disk
            await asyncio.to_thread(os.fsync, f.fileno())
            
        # Atomic rename
        await asyncio.to_thread(os.replace, str(temp_file), str(file_path))
        
    except Exception as e:
        if await asyncio.to_thread(os.path.exists, temp_file):
            try:
                await asyncio.to_thread(os.remove, temp_file)
            except Exception: pass
        logger.error(f"Atomic write failed for {file_path}: {e}")
        raise e

async def load_json_resilient(file_path: Path) -> Any:
    """
    Load JSON file safely with SELF-HEALING on corruption.
    Fixes 'unexpected content after document' by trimming garbage.
    """
    if not await asyncio.to_thread(file_path.exists):
        return {}

    content = b""
    try:
        async with aiofiles.open(file_path, "rb") as f:
            content = await f.read()
        
        if not content.strip():
            return {}
            
        return safe_json_loads(content)
        
    except Exception:
        # --- RECOVERY MODE ---
        logger.warning(f"⚠️ JSON Corruption detected in {file_path}. Attempting self-healing...")
        data = {}
        recovery_success = False
        
        try:
            txt = content.decode('utf-8', errors='ignore').strip()
            end_idx = txt.rfind('}') # Look for last valid closing brace of root object
            if end_idx == -1:
                end_idx = txt.rfind(']') # Or root list
                
            if end_idx != -1:
                fixed_txt = txt[:end_idx+1]
                data = json.loads(fixed_txt)
                
                await atomic_write_json(file_path, data)
                recovery_success = True
                logger.info(f"✅ Self-healing successful for {file_path}")
        except Exception as e:
            logger.error(f"Self-healing strategy A failed: {e}")

        # Strategy B: Delete if unrecoverable (Prevents read loops)
        if not recovery_success:
            logger.error(f"❌ File {file_path} is unrecoverable. Deleting to reset.")
            try:
                await asyncio.to_thread(os.remove, file_path)
            except Exception: pass
            return {}
    return _convert_timestamp_strings(data) if isinstance(data, (dict, list)) else data

# --- END RESILIENT I/O HELPERS ---
async def ensure_redis_connections():
    global redis_local_client, redis_gateway_client, _gateway_redis_failures, _redis_reconnect_attempts, _last_redis_warning_time, _last_redis_reconnect_attempt
    current_time = time.time()
    
    # Check and maintain local Redis connection
    if redis_local_client is not None:
        try:
            await redis_local_client.ping()
        except Exception:
            redis_local_client = None
    # Check cooldown before attempting to reconnect
    last_attempt = _last_redis_reconnect_attempt.get('local', 0)
    if redis_local_client is None and current_time - last_attempt < REDIS_RECONNECT_COOLDOWN:
        # Skip reconnection attempt, still in cooldown
        pass
    elif redis_local_client is None:
        try:
            # Use 127.0.0.1 explicitly to avoid IPv6 issues
            host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
            # LONG timeout - Redis can take time to appear
            redis_local_client = redis.Redis(host=host, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True, 
                                              socket_connect_timeout=30, socket_timeout=60, health_check_interval=30, 
                                              max_connections=3000, retry_on_timeout=True,
                                              retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError])
            # NON-BLOCKING: No ping wait, connects in background
            logger.debug(f"⏳ Local Redis client created ({host}:{config.REDIS_PORT}) - connecting in background.")
            _redis_reconnect_attempts['local'] = 0
            _last_redis_reconnect_attempt['local'] = current_time
        except Exception as exc:
            _redis_reconnect_attempts['local'] = _redis_reconnect_attempts.get('local', 0) + 1
            _last_redis_reconnect_attempt['local'] = current_time
            if current_time - _last_redis_warning_time.get('local', 0) > MAX_REDIS_WARNING_INTERVAL:
                logger.warning(f"⚠️ Local Redis connection failed {host}:{config.REDIS_PORT} -> {exc}")
                _last_redis_warning_time['local'] = current_time
            redis_local_client = None
    
    # Check and maintain gateway Redis connection
    if redis_gateway_client is not None:
        try:
            await redis_gateway_client.ping()
        except Exception:
            redis_gateway_client = None
    
    # Check cooldown before attempting to reconnect
    last_attempt = _last_redis_reconnect_attempt.get('gateway', 0)
    if redis_gateway_client is None and current_time - last_attempt < REDIS_RECONNECT_COOLDOWN:
        # Skip reconnection attempt, still in cooldown
        pass
    elif redis_gateway_client is None:
        try:
            env_info = get_current_environment()
            gw_host, gw_port = env_info['redis_connections'].get('gateway', ('localhost', 6379))
            # Use 127.0.0.1 for localhost to avoid IPv6 issues
            if gw_host in ['localhost', '::1']:
                gw_host = '127.0.0.1'
            # MUCH longer timeout for gateway - SSH tunnels can be slow
            redis_gateway_client = redis.Redis(host=gw_host, port=gw_port, db=config.REDIS_DB, decode_responses=True, 
                                                socket_connect_timeout=60, socket_timeout=300, max_connections=3000, 
                                                health_check_interval=30, retry_on_timeout=True,
                                                retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError])
            # NON-BLOCKING: No ping wait, connects in background
            logger.debug(f"⏳ Gateway Redis client created ({gw_host}:{gw_port}) - connecting in background.")
            _gateway_redis_failures = 0
            _redis_reconnect_attempts['gateway'] = 0
            _last_redis_reconnect_attempt['gateway'] = current_time
        except Exception as exc:
            _gateway_redis_failures += 1
            _redis_reconnect_attempts['gateway'] = _redis_reconnect_attempts.get('gateway', 0) + 1
            _last_redis_reconnect_attempt['gateway'] = current_time
            if current_time - _last_redis_warning_time.get('gateway', 0) > MAX_REDIS_WARNING_INTERVAL:
                logger.warning(f"⚠️ Redis gateway connection failed {gw_host}:{gw_port} -> {type(exc).__name__}()")
                _last_redis_warning_time['gateway'] = current_time
            redis_gateway_client = None

async def get_ready_redis_clients() -> List[redis.Redis]:
    await ensure_redis_connections()
    clients: List[redis.Redis] = []
    if redis_local_client:
        clients.append(redis_local_client)
    if redis_gateway_client:
        clients.append(redis_gateway_client)
    return clients

async def publish_json(channel: str, payload: Dict[str, Any], expiry_seconds: Optional[int] = None) -> int:
    clients = await get_ready_redis_clients()
    if not clients:
        return 0
    body = json.dumps(payload, default=str)
    success = 0
    for client in clients:
        try:
            await client.publish(channel, body)
            if expiry_seconds:
                await client.setex(channel, expiry_seconds, body)
            success += 1
        except Exception as exc:
            logger.debug(f"Redis publish failed {channel}: {exc}")
    return success
# Get current environment for rate limiting
current_env = get_current_environment()['env']
API_SEMAPHORE = asyncio.Semaphore(config.EZ_MARK_PRICES_API_SEMAPHORE.get(current_env, 35))
STARTUP_API_SEMAPHORE = asyncio.Semaphore(config.EZ_MARK_PRICES_STARTUP_SEMAPHORE.get(current_env, 30))  # Even less restrictive for startup
STARTUP_BATCH_SIZE = 75  # Process symbols in even larger batches during startup
STARTUP_BATCH_DELAY = 0.2  
class AdaptiveThrottler:
    def __init__(self):
        self.error_count = 0
        self.last_error_time = 0
        self.performance_warnings = 0
        self.startup_start_time = time.time()

    def record_error(self):
        """Record an error for adaptive throttling."""
        self.error_count += 1
        self.last_error_time = time.time()

    def record_performance_warning(self):
        """Record a performance warning."""
        self.performance_warnings += 1

    def should_increase_throttling(self) -> bool:
        """Check if we should increase throttling due to errors."""
        # Be extremely lenient - only throttle if we have CRITICAL errors
        if time.time() - self.last_error_time < 30 and self.error_count > 25:
            return True
        # Only throttle if we have critical performance warnings
        if self.performance_warnings > 50:
            return True
        return False

    def get_dynamic_batch_size(self) -> int:
        """Get adaptive batch size based on performance."""
        if self.should_increase_throttling():
            return max(10, STARTUP_BATCH_SIZE - 10)  # Only reduce by 10, minimum 10
        return STARTUP_BATCH_SIZE

    def get_dynamic_delay(self) -> float:
        """Get adaptive delay based on performance."""
        if self.should_increase_throttling():
            return STARTUP_BATCH_DELAY * 1.5  # Only increase by 50%
        return STARTUP_BATCH_DELAY

    def reset_on_success(self):
        """Reset counters on successful operations."""
        # Reset error count if no errors for 60 seconds
        if time.time() - self.last_error_time > 60:
            self.error_count = max(0, self.error_count - 1)
        # Reset performance warnings if no warnings for 120 seconds
        if time.time() - getattr(self, 'last_warning_time', 0) > 120:
            self.performance_warnings = max(0, self.performance_warnings - 1)

# Global adaptive throttler instance
adaptive_throttler = AdaptiveThrottler()

# Rate limiting state tracking
class RateLimitTracker:
    def __init__(self):
        self.consecutive_errors = 0
        self.last_error_time = 0
        self.backoff_until = 0
        self.max_backoff = 300  # 5 minutes max

    def record_error(self):
        """Record an API error for backoff calculation"""
        now = time.time()
        self.consecutive_errors += 1
        self.last_error_time = now

        # Exponential backoff based on consecutive errors
        if self.consecutive_errors <= 3:
            backoff = min(30 * (2 ** self.consecutive_errors), self.max_backoff)
        else:
            backoff = self.max_backoff

        self.backoff_until = now + backoff
        logger.warning(f"🔥 API error #{self.consecutive_errors}. Backing off for {backoff}s until {datetime.fromtimestamp(self.backoff_until)}")

    def record_success(self):
        """Record a successful API call"""
        self.consecutive_errors = 0

    def should_backoff(self) -> bool:
        """Check if we should back off due to rate limiting"""
        return time.time() < self.backoff_until

    def get_backoff_remaining(self) -> float:
        """Get remaining backoff time in seconds"""
        remaining = self.backoff_until - time.time()
        return max(0, remaining)

# Global rate limit tracker
rate_limit_tracker = RateLimitTracker()

def _resolve_local_bind_kwargs():
    # Skip IP binding - let OS choose the interface automatically
    return {}

LOCAL_BIND_KW = _resolve_local_bind_kwargs()
# =========================
# Configuration
# =========================

LOG_FILE = Path.home() / "logs" / "ez_mark_prices.log"
M_READY_FLAG_FILE = config.DATA_READY_FLAG_FILE
WS_BATCH_SIZE = 70
MAX_BARS_3m = 2400  # Increased from 2400 to preserve more historical data
TARGET_BARS_3m = 1800  # Increased from 1500 to preserve more historical data  
TOP_UP_BARS = 1500
SEMAPHORE = 15
BACKFILL_CHUNK_SIZE = 1500
BACKFILL_SLEEP_SECONDS = 2
REDIS_3m_TAIL = 1500  # Keep a small tail in Redis for low-latency consumers

# Mark prices cache file path
MARK_PRICES_CACHE_FILE = config.BASE_PATH / "price_cache_3.json"

# Remove RedisPublisher class - using UnifiedRedisManager instead
async def save_mark_prices_to_cache(mark_prices: Dict[str, float]):
    """Save mark prices using robust atomic write."""
    # Throttle
    current_time = datetime.now(timezone.utc)
    if hasattr(save_mark_prices_to_cache, '_last_update_time'):
        time_since = (current_time - save_mark_prices_to_cache._last_update_time).total_seconds()
        if time_since < 15: return
    
    save_mark_prices_to_cache._last_update_time = current_time
    
    try:
        # Load existing using resilient loader
        existing_cache = await load_json_resilient(MARK_PRICES_CACHE_FILE)
        if not isinstance(existing_cache, dict): existing_cache = {}
        
        # Helper to get symbols list (cached if possible)
        complete_symbols = set()
        try:
            # Use resilient load for symbols too
            symbols_data = await load_json_resilient(config.SYMBOLS_FILE)
            if isinstance(symbols_data, list):
                complete_symbols = set(symbols_data)
        except Exception:
            complete_symbols = set(mark_prices.keys())
        
        updated_cache = {}
        
        # Preserve existing valid entries
        for symbol, data in existing_cache.items():
            if isinstance(data, dict) and 'price' in data:
                updated_cache[symbol] = data
        
        # Update new
        ts_str = current_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        for symbol, price in mark_prices.items():
            updated_cache[symbol] = {"price": price, "timestamp": ts_str}
        
        # Ensure all symbols present
        for symbol in complete_symbols:
            if symbol not in updated_cache:
                updated_cache[symbol] = {"price": 0.0, "timestamp": ts_str}
        
        # Use robust atomic write
        await atomic_write_json(MARK_PRICES_CACHE_FILE, updated_cache)
        
    except Exception as e:
        logger.error(f"Error saving mark prices cache: {e}")

async def load_mark_prices_from_cache() -> Dict[str, float]:
    """Load existing mark prices from price_cache_3.json.
    Only loads prices that are recent (within 60 seconds) as older prices are useless for trading."""
    try:
        if MARK_PRICES_CACHE_FILE.exists():
            with open(MARK_PRICES_CACHE_FILE, 'r') as f:
                cache_data = json.load(f)
            
            # Extract only recent prices from the cache
            current_time = datetime.now(timezone.utc)
            recent_prices = {}
            
            for symbol, data in cache_data.items():
                if isinstance(data, dict) and 'price' in data and 'timestamp' in data:
                    try:
                        # Check if the price is recent (within 60 seconds)
                        timestamp = pd.to_datetime(data['timestamp'], utc=True).to_pydatetime()
                        time_diff = (current_time - timestamp).total_seconds()
                        
                        if time_diff <= 60:  # Only load recent prices
                            recent_prices[symbol] = float(data['price'])
                        else:
                            logger.debug(f"⏰ Skipping stale mark price for {symbol} (age: {time_diff:.1f}s)")
                    except (ValueError, TypeError):
                        # If timestamp parsing fails, skip this entry
                        logger.debug(f"⚠️ Skipping mark price for {symbol} due to invalid timestamp")
                        continue
            
            if recent_prices:
                logger.info(f"✅ Loaded {len(recent_prices)} recent mark prices from cache file")
            else:
                logger.info("ℹ️ No recent mark prices found in cache file")
            return recent_prices
        else:
            logger.info("ℹ️ No mark prices cache file found, starting fresh")
            return {}
            
    except Exception as e:
        logger.error(f"Error loading mark prices from cache: {e}")
        return {}

async def cleanup_old_mark_price_cache():
    """Clean up old mark prices from the cache. 
    Only keeps prices from the last 60 seconds as older prices are useless for trading."""
    try:
        if not MARK_PRICES_CACHE_FILE.exists():
            return
        
        with open(MARK_PRICES_CACHE_FILE, 'r') as f:
            cache_data = json.load(f)
        
        # Keep only very recent prices (within 60 seconds)
        cleaned_cache = {}
        current_time = datetime.now(timezone.utc)
        
        for symbol, data in cache_data.items():
            if isinstance(data, dict) and 'price' in data and 'timestamp' in data:
                try:
                    # Parse timestamp and check if it's very recent (within 60 seconds)
                    timestamp = pd.to_datetime(data['timestamp'], utc=True).to_pydatetime()
                    time_diff = (current_time - timestamp).total_seconds()
                    
                    if time_diff <= 60:  # Only keep prices from last 60 seconds
                        cleaned_cache[symbol] = data
                    else:
                        logger.debug(f"🧹 Removing stale mark price for {symbol} (age: {time_diff:.1f}s)")
                except (ValueError, TypeError):
                    # If timestamp parsing fails, remove the entry
                    logger.debug(f"🧹 Removing mark price for {symbol} due to invalid timestamp")
                    continue
        
        # Only rewrite if we actually cleaned something
        if len(cleaned_cache) < len(cache_data):
            # Write cleaned cache atomically
            temp_file = MARK_PRICES_CACHE_FILE.with_suffix('.tmp')
            try:
                if atomic_write_json(MARK_PRICES_CACHE_FILE, cleaned_cache):
                    removed_count = len(cache_data) - len(cleaned_cache)
                    logger.info(f"🧹 Cleaned mark prices cache: {len(cache_data)} → {len(cleaned_cache)} symbols (removed {removed_count} stale entries)")
                else:
                    logger.error(f"Failed to write cleaned mark prices cache")
            except Exception as e:
                logger.error(f"Failed to write cleaned mark prices cache: {e}")
        else:
            logger.debug(f"🧹 Mark prices cache is already clean ({len(cache_data)} recent entries)")
                    
    except Exception as e:
        logger.error(f"Error cleaning up mark prices cache: {e}")

_last_broadcast_warning_time = {}
async def broadcast_mark_prices_to_redis(mark_prices: Dict[str, float]):
    """Broadcast mark prices to Redis hash for simple_redis consumption."""
    global _last_broadcast_warning_time
    current_time = time.time()
    await ensure_redis_connections()
    clients = await get_ready_redis_clients()
    if not clients:
        if current_time - _last_broadcast_warning_time.get('no_clients', 0) > MAX_REDIS_WARNING_INTERVAL:
            logger.warning(f"⚠️ No Redis clients available for broadcast_mark_prices_to_redis")
            _last_broadcast_warning_time['no_clients'] = current_time
        return 0
    
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    success = 0
    
    for symbol, price in mark_prices.items():
        if price == 0.0:
            continue
            
        hash_payload = {"symbol": symbol, "price": price, "timestamp": timestamp, "source": ENVIRONMENT}
        
        for client in clients:
            try:
                result = await client.hset("mark_prices", symbol, json.dumps(hash_payload))
                success += 1
                logger.debug(f"✅ Stored mark price for {symbol} = {price} in Redis (result: {result})")
            except Exception as exc:
                if current_time - _last_broadcast_warning_time.get(symbol, 0) > MAX_REDIS_WARNING_INTERVAL:
                    logger.error(f"❌ Redis HSET failed for {symbol}: {exc}")
                    _last_broadcast_warning_time[symbol] = current_time
    if mark_prices: await save_mark_prices_to_cache(mark_prices)
    return success

async def broadcast_klines_to_redis(symbol: str, klines_df):
    if klines_df.empty:
        return 0
    klines_list = klines_df.tail(1800).to_dict('records')
    for kline in klines_list:
        if 'timestamp' in kline:
            ts = kline['timestamp']
            if hasattr(ts, 'strftime'):
                kline['timestamp'] = ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            elif isinstance(ts, (int, float)):
                kline['timestamp'] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    payload = {"symbol": symbol, "interval": "3m", "published_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "klines": klines_list, "count": len(klines_list)}
    return await publish_json(f"klines:{symbol}:3m", payload, expiry_seconds=config.REDIS_EXPIRY_SECONDS * 6)

async def broadcast_latest_kline_to_redis(symbol: str, latest_kline: Dict[str, Any]):
    kline_for_redis = latest_kline.copy()
    if 'timestamp' in kline_for_redis and hasattr(kline_for_redis['timestamp'], 'strftime'):
        kline_for_redis['timestamp'] = kline_for_redis['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    payload = {**kline_for_redis, "published_at": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "source": ENVIRONMENT}
    success = await publish_json(f"latest_kline:{symbol}", payload, expiry_seconds=300)
    if success > 0:
        notification = {'symbol': symbol, 'published_at_utc': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'action': 'latest_kline_updated', 'source': ENVIRONMENT}
        await publish_json("latest_kline_updates", notification, expiry_seconds=300)
    return success > 0

class CoordinatedWebSocketManager:
    """Manages coordinated WebSocket connections with ez_prices_ws.py"""
    def __init__(self):
        self.role = "primary"
        self.symbols = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "ADAUSDC", "DOTUSDT"]
        self.subscriptions = ["markPrice@1s", "kline_3m", "kline_3m"]
        self.ping_interval = 20
        self.ping_timeout = 30  # Increased from 10 to 30 seconds to prevent timeouts
        self.reconnect_delay = 3
        self.max_reconnect_attempts = 10
        self.websocket = None
        self.is_connected = False
        self.last_ping = time.time()
        self.connection_attempts = 0
        
    async def start_coordinated_websocket(self):
        """Start the coordinated WebSocket connection."""
        logger.info(f"🔌 Starting coordinated WebSocket as {self.role}")
        logger.info(f"📊 Symbols: {', '.join(self.symbols)}")
        logger.info(f"📡 Subscriptions: {', '.join(self.subscriptions)}")
        
        while True:
            try:
                await self.connect_websocket()
                await self.monitor_connection()
            except Exception as e:
                logger.error(f"❌ WebSocket error: {e}")
                await self.handle_reconnection()
    
    async def connect_websocket(self):
        """Establish WebSocket connection."""
        try:
            # Create SSL context
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Connect to Binance WebSocket
            websocket_url = "wss://fstream.binance.com/ws"
            self.websocket = await websockets.connect(
                websocket_url,
                ssl=ssl_context,
                ping_interval=None,  # Disable automatic ping - Binance handles this
                ping_timeout=None,   # Disable ping timeout checks
                close_timeout=15,  # Increased for graceful shutdowns
                max_size=2**20
            )
            
            # Subscribe to streams
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": [
                    f"{symbol.lower()}@{sub}" 
                    for symbol in self.symbols 
                    for sub in self.subscriptions
                ],
                "id": self.connection_attempts
            }
            
            await self.websocket.send(json.dumps(subscribe_msg))
            self.is_connected = True
            self.connection_attempts += 1
            
            # Update health status in Redis
            await self.update_health_status("healthy")
            
            logger.info(f"✅ Connected to WebSocket (attempt {self.connection_attempts})")
            
        except Exception as e:
            logger.error(f"❌ WebSocket connection failed: {e}")
            self.is_connected = False
            raise
    
    async def monitor_connection(self):
        """Monitor the WebSocket connection and handle messages."""
        try:
            while self.is_connected:
                try:
                    # Send periodic ping
                    if time.time() - self.last_ping >= self.ping_interval:
                        await self.send_ping()
                    
                    # Receive message with timeout
                    message = await asyncio.wait_for(self.websocket.recv(), timeout=5.0)
                    await self.process_message(message)
                    
                except asyncio.TimeoutError:
                    # Check connection health
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"🔌 Connection closed: {e}")
                    break
                except Exception as e:
                    logger.error(f"❌ Message processing error: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Connection monitoring error: {e}")
        finally:
            self.is_connected = False
    
    async def send_ping(self):
        """Send ping to keep connection alive."""
        try:
            ping_msg = {"method": "ping"}
            await self.websocket.send(json.dumps(ping_msg))
            self.last_ping = time.time()
            logger.debug("🏓 Ping sent")
        except Exception as e:
            logger.warning(f"🏓 Ping failed: {e}")
    
    async def process_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)
            
            # Handle mark price updates
            if 'data' in data and 'markPrice' in data['data']:
                await self.handle_mark_price(data['data'])
            
            # Handle kline updates
            elif 'data' in data and 'k' in data['data']:
                await self.handle_kline_update(data['data'])
                
        except Exception as e:
            logger.error(f"❌ Message processing error: {e}")
    
    async def handle_mark_price(self, data: Dict):
        """Handle mark price updates."""
        try:
            symbol = data['s']
            mark_price = float(data['markPrice'])
            timestamp = datetime.fromtimestamp(data['E'] / 1000, tz=timezone.utc)
            
            await broadcast_mark_prices_to_redis({symbol: mark_price})
            
            logger.debug(f"📊 Mark price: {symbol} = {mark_price}")
            
        except Exception as e:
            logger.error(f"❌ Mark price handling error: {e}")
    
    async def handle_kline_update(self, data: Dict):
        """Handle kline updates."""
        try:
            kline = data['k']
            symbol = kline['s']
            timestamp = datetime.fromtimestamp(kline['T'] / 1000, tz=timezone.utc)
            
            kline_data = {
                'symbol': symbol,
                'timestamp': timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'open': float(kline['o']),
                'high': float(kline['h']),
                'low': float(kline['l']),
                'close': float(kline['c']),
                'volume': float(kline['v'])
            }
            await publish_json(f"kline:{symbol}:3m", kline_data, expiry_seconds=config.REDIS_EXPIRY_SECONDS)
            logger.debug(f"📊 Kline: {symbol} 3m at {timestamp}")
            
        except Exception as e:
            logger.error(f"❌ Kline handling error: {e}")
    
    async def update_health_status(self, status: str):
        """Update health status using direct Redis clients."""
        try:
            payload = {
                'status': status,
                'last_seen': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'symbols': self.symbols,
                'role': self.role,
                'connection_attempts': self.connection_attempts
            }
            await publish_json("websocket:ez_mark_prices:health", payload, expiry_seconds=300)
        except Exception as e:
            logger.error(f"❌ Health status update failed: {e}")
    
    async def handle_reconnection(self):
        """Handle WebSocket reconnection."""
        if self.connection_attempts >= self.max_reconnect_attempts:
            logger.error(f"❌ Max reconnection attempts ({self.max_reconnect_attempts}) reached")
            await self.update_health_status("failed")
            return
        
        logger.info(f"🔄 Reconnecting in {self.reconnect_delay}s... (attempt {self.connection_attempts + 1})")
        await self.update_health_status("reconnecting")
        await asyncio.sleep(self.reconnect_delay)
    
    async def stop(self):
        """Stop the WebSocket connection."""
        self.is_connected = False
        if self.websocket:
            await self.websocket.close()
        await self.update_health_status("stopped")
        logger.info("🔌 Coordinated WebSocket stopped")

class MarkPriceStreamer:
    def __init__(self):
        self.logger = logger
        self.symbols_to_run = []
        self.api_symbol_to_original_map = {}
        self.session = None
        self._cleanup_task_started = False
        self._file_write_locks = {}
        self.last_websocket_message_time = None
        self.websocket_data_freshness_threshold = 30
        self.shutdown_event = None
        self.redis_manager = None
        self.redis_local = None
        self.redis_gateway = None
        # Redis clients - following ez_prices_ws.py pattern
        self.redis: Optional[redis.Redis] = None
        self.redis_gateway_ws: Optional[redis.Redis] = None
        self._gateway_redis_failures_ws = 0
        self._top_up_task: Optional[asyncio.Task] = None
    
    async def _init_redis_ws(self) -> None:
        """Initialize Redis connections using EXACT same pattern as ez_prices_ws.py"""
        # Use 127.0.0.1 explicitly to avoid IPv6 issues
        host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
        # LONG timeout - Redis can take time to appear
        self.redis = redis.Redis(host=host, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True, 
                                  socket_connect_timeout=30, socket_timeout=60, health_check_interval=30, 
                                  max_connections=3000, retry_on_timeout=True,
                                  retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError])
        # NON-BLOCKING: No ping wait, connects in background
        self.logger.debug(f"⏳ Local Redis client created ({host}:{config.REDIS_PORT}) - connecting in background.")
        try:
            env_info = get_current_environment()
            gw_host, gw_port = env_info['redis_connections'].get('gateway', ('localhost', 6379))
            # Fix IPv6 issue - use 127.0.0.1 for localhost
            if gw_host in ['localhost', '::1']:
                gw_host = '127.0.0.1'
            # MUCH longer timeout for gateway - SSH tunnels can be slow
            self.redis_gateway_ws = redis.Redis(host=gw_host, port=gw_port, db=config.REDIS_DB, decode_responses=True, 
                                              socket_connect_timeout=60, socket_timeout=300, max_connections=3000, 
                                              health_check_interval=30, retry_on_timeout=True,
                                              retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError])
            # NON-BLOCKING: No ping wait, connects in background
            self.logger.debug(f"⏳ Gateway Redis client created ({gw_host}:{gw_port}) - connecting in background.")
            self._gateway_redis_failures_ws = 0
            self.redis_gateway_ws = None  # Don't use yet
        except Exception as e:
            self.logger.warning(f"⚠️ Could not connect to gateway Redis: {e}. Will only use local Redis.")
            self.redis_gateway_ws = None
    
    async def _publish_to_redis_direct(self, key: str, payload: Dict[str, Any], expiry_seconds: Optional[int] = None) -> int:
        """Publish to Redis using self.redis directly - matching ez_prices_ws.py pattern"""
        if not self.redis:
            return 0
        try:
            body = json.dumps(payload, default=str)
            await self.redis.setex(key, expiry_seconds or config.REDIS_EXPIRY_SECONDS, body)
            await self.redis.publish(key, body)
            return 1
        except Exception as e:
            self.logger.debug(f"Redis publish failed {key}: {e}")
            return 0
    
    async def _broadcast_mark_prices_direct(self, mark_prices: Dict[str, float]) -> int:
        """Broadcast mark prices using self.redis directly - matching ez_prices_ws.py pattern"""
        if not self.redis or not mark_prices:
            return 0
        try:
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            success = 0
            for symbol, price in mark_prices.items():
                try:
                    redis_key = f"mark_price:{symbol}"
                    # Store as JSON with timestamp
                    value = json.dumps({"price": price, "timestamp": timestamp, "symbol": symbol})
                    await self.redis.set(redis_key, value, ex=300)  # 5 minute expiry
                    success += 1
                    self.logger.debug(f"✅ Stored mark price for {symbol} = {price}")
                except Exception as exc:
                    self.logger.debug(f"❌ Redis SET failed for {symbol}: {exc}")
            if mark_prices: await save_mark_prices_to_cache(mark_prices)
            return success
        except Exception as e:
            self.logger.debug(f"Redis broadcast failed: {e}")
            return 0
    def _should_stop(self) -> bool:
        return bool(self.shutdown_event and self.shutdown_event.is_set())

    def _blocking_read_helper(self, fp: Path) -> str:
        with open(fp, "r", encoding="utf-8") as f: return f.read()
    
    async def _read_file_unlocked(self, fp: Path) -> pd.DataFrame:
        """Enhanced read using load_json_resilient."""
        if not await asyncio.to_thread(fp.exists) or (await asyncio.to_thread(fp.stat)).st_size < 10: 
            return pd.DataFrame()
            
        try:
            # Use resilient loader
            data = await load_json_resilient(fp)
            if not isinstance(data, list):
                return pd.DataFrame()
                
            df = pd.DataFrame(data)
            
            if 'timestamp' in df.columns:
                # Timestamps might already be datetime objects due to load_json_resilient
                # but we ensure they are floored correctly
                if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
                    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
                
                # Apply flooring
                df['timestamp'] = df['timestamp'].dt.floor('3min')
                df = df.dropna(subset=['timestamp'])
                df = df.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
                
            return df
        except Exception: 
            return pd.DataFrame()
    
    async def _write_file_unlocked(self, df: pd.DataFrame, fp: Path):
        tmp_file_path = None
        try:
            if df.empty:
                logger.warning(f"[{fp.stem}] Attempting to write empty DataFrame, skipping...")
                return False
            
            # ... (Your dataframe cleanup logic is fine) ...
            df_clean = df.dropna(subset=['timestamp']).copy()
            if len(df_clean) == 0: return False
            
            if len(df_clean) > MAX_BARS_3m:
                df_safe = df_clean.tail(TARGET_BARS_3m).copy()
            else:
                df_safe = df_clean.copy()
            
            df_safe['timestamp'] = df_safe['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            
            if len(df_safe) == 0: return False

            # FIX 1: Heavy CPU work (Pandas to JSON) must be in a thread
            # Using 'records' is standard, orjson is faster but pandas.to_json is safer for DateFrames
            json_str = await asyncio.to_thread(df_safe.to_json, orient='records', indent=2)

            # FIX 2: Use .atom extension (consistency with atomic_write_json)
            # FIX 3: Use aiofiles for the write to stay async friendly
            random_suffix = uuid.uuid4().hex
            tmp_path = fp.with_name(f".{fp.name}.{random_suffix}.atom")
            
            async with aiofiles.open(tmp_path, 'w', encoding='utf-8') as f:
                await f.write(json_str)
                await f.flush()
                await asyncio.to_thread(os.fsync, f.fileno())
            
            # Atomic replace
            await asyncio.to_thread(os.replace, str(tmp_path), str(fp))

            # Verify
            if fp.exists() and fp.stat().st_size > 0:
                logger.debug(f"✅ Successfully wrote {len(df_safe)} bars to {fp.name}")
                return True
            else:
                logger.error(f"❌ File write verification failed for {fp.name}")
                return False

        except Exception as e:
            logger.error(f"❌ Error writing file {fp.name}: {e}")
            # Cleanup logic
            try:
                if 'tmp_path' in locals() and tmp_path.exists():
                    os.remove(tmp_path)
            except Exception: pass
            return False
    # async def _write_file_unlocked(self, df: pd.DataFrame, fp: Path):
    #     """
    #     ENHANCED: Atomically writes DataFrame using atomic_write_json.
    #     """
    #     try:
    #         if df.empty: return False
            
    #         df_clean = df.dropna(subset=['timestamp']).copy()
    #         if len(df_clean) == 0: return False
            
    #         # Clip
    #         if len(df_clean) > MAX_BARS_3m:
    #             df_safe = df_clean.tail(TARGET_BARS_3m).copy()
    #             logger.info(f"[{fp.stem}] Clipping file: {len(df_clean)} -> {len(df_safe)}")
    #         else:
    #             df_safe = df_clean.copy()
            
    #         # Format timestamps for JSON
    #         if pd.api.types.is_datetime64_any_dtype(df_safe['timestamp']):
    #             df_safe['timestamp'] = df_safe['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            
    #         # Convert to list of dicts
    #         data_to_write = df_safe.to_dict(orient='records')
            
    #         # Use robust atomic write
    #         await atomic_write_json(fp, data_to_write)
            
    #         return True
                
    #     except Exception as e:
    #         logger.error(f"❌ Error writing file {fp.name}: {e}")
    #         return False
    async def _read_file_from_best_directory(self, symbol: str, interval: str = "3m") -> pd.DataFrame:
        """Read klines file from ALL THREE directories, return the one with freshest data"""
        best_df = pd.DataFrame()
        best_ts = pd.Timestamp(0, tz='UTC')
        klines_dirs = _resolve_klines_directories()
        for klines_dir in klines_dirs:
            if not klines_dir or not klines_dir.exists():
                continue
            file_path = klines_dir / f"{symbol}_{interval}.json"
            if not file_path.exists():
                continue
            df = await self._read_file_unlocked(file_path)
            if df.empty or 'timestamp' not in df.columns:
                continue
            df_timestamps = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
            latest_ts = df_timestamps.max()
            if pd.notna(latest_ts) and latest_ts > best_ts:
                best_df = df
                best_ts = latest_ts
        if not best_df.empty:
            self.logger.debug(f"📂 {symbol}_{interval} from directory scan: {len(best_df)} bars, latest {best_ts}")
        return best_df

    def _normalize_and_floor_timestamp(self, ts_column: pd.Series) -> pd.Series:
        ts_column = pd.to_datetime(ts_column, utc=True, errors='coerce')
        return ts_column.dt.floor('3min')
        
    async def fetch_klines(self, symbol: str, limit: int, end_dt: datetime = None) -> pd.DataFrame:
        """Fetch klines with improved rate limiting and error handling"""
        api_symbol = symbol.replace("USDC", "USDT") if symbol.endswith("USDC") else symbol
        actual_limit = min(limit, 1500)
        params = {"symbol": api_symbol, "interval": "3m", "limit": actual_limit}
        if end_dt: params['endTime'] = int(end_dt.timestamp() * 1000)

        # Check if we should back off due to rate limiting
        if rate_limit_tracker.should_backoff():
            remaining = rate_limit_tracker.get_backoff_remaining()
            logger.warning(f"⏸️ [{api_symbol}] Rate limiting active. Backing off for {remaining:.1f}s")
            await asyncio.sleep(min(remaining, 30))  # Sleep up to 30s or remaining time
            if rate_limit_tracker.should_backoff():
                logger.warning(f"⏸️ [{api_symbol}] Still rate limited after sleep. Skipping request.")
                return pd.DataFrame()

        logger.debug(f"[{api_symbol}] Fetching {actual_limit} klines (requested: {limit})")

        async with aiohttp.ClientSession() as session:
            async with API_SEMAPHORE:  # Use global semaphore for API throttling
                try:
                    async with session.get(f"{config.FAPI_BASE_URL}/klines", params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if not data:
                                logger.warning(f"[{api_symbol}] No data returned from API")
                                rate_limit_tracker.record_success()
                                return pd.DataFrame()

                            df = pd.DataFrame(data).iloc[:,:6]
                            df.columns = config.KLINE_COLUMNS
                            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
                            df['timestamp'] = self._normalize_and_floor_timestamp(df['timestamp'])
                            df = df.dropna(subset=['timestamp'])

                            logger.debug(f"[{api_symbol}] Successfully fetched and normalized {len(df)} klines")
                            rate_limit_tracker.record_success()
                            return df

                        elif resp.status == 429:  # Rate limit exceeded
                            logger.warning(f"🚫 [{api_symbol}] Rate limit exceeded (429). Backing off...")
                            rate_limit_tracker.record_error()
                            return pd.DataFrame()

                        elif resp.status >= 400 and resp.status < 500:  # Client errors (likely rate limiting or auth issues)
                            logger.warning(f"🚫 [{api_symbol}] Client error {resp.status}. Recording error for backoff.")
                            rate_limit_tracker.record_error()
                            return pd.DataFrame()

                        else:
                            logger.warning(f"[{api_symbol}] API Error {resp.status} during fetch.")
                            rate_limit_tracker.record_success()  # Don't penalize for server errors
                            return pd.DataFrame()

                except asyncio.TimeoutError:
                    logger.error(f"⏰ [{api_symbol}] Request timeout during fetch")
                    rate_limit_tracker.record_error()
                    return pd.DataFrame()

                except Exception as e:
                    logger.error(f"[{api_symbol}] Network error during fetch: {e}")
                    rate_limit_tracker.record_error()
                    return pd.DataFrame()

    async def run_startup_sequence(self, shutdown_event: Optional[asyncio.Event] = None):
        if shutdown_event: self.shutdown_event = shutdown_event
        logger.info("--- ⚔️ STARTING GUARDIAN STARTUP SEQUENCE ⚔️ ---")
        
        # Initialize aiohttp session for API calls
        if self.session is None:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=10, ttl_dns_cache=300, force_close=False, enable_cleanup_closed=True)
            timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
            self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            logger.info("✅ Initialized aiohttp session for API requests")
        
        # Initialize Redis using EXACT same pattern as ez_prices_ws.py
        await self._init_redis_ws()
        
        # Load symbols from file immediately (fast operation)
        with open(config.SYMBOLS_FILE, 'r') as f:
            initial_symbols = sorted(list(set(json.load(f))))
        logger.info(f"✅ Loaded {len(initial_symbols)} symbols from config")
        
        # Start websockets IMMEDIATELY with basic symbol mapping
        logger.info("--- 🚀 Starting websockets IMMEDIATELY ---")
        
        # Create symbol mapping (convert USDC to USDT for API calls)
        with open(config.LIVE_USDC_PAIRS_FILE, 'r') as f:
            live_usdc_pairs = set(json.load(f))
        self.symbols_to_run = sorted(force_usdc_in_list(initial_symbols, live_usdc_pairs))
        
        for original_symbol in self.symbols_to_run:
            api_symbol = original_symbol.replace("USDC", "USDT") if original_symbol.endswith("USDC") else original_symbol
            self.api_symbol_to_original_map[api_symbol] = original_symbol
        
        # Start websocket listener IMMEDIATELY
        websocket_task = asyncio.create_task(self.listen_to_websockets())
        logger.info("✅  WebSocket listener started - broadcasting data NOW")
        
        # Mark ready
        M_READY_FLAG_FILE.touch()
        logger.info(f"✅ Data Ready Flag Set: {M_READY_FLAG_FILE}")
        
        # Run setup in background (don't block)
        asyncio.create_task(self.setup_and_rename())
        
        # Start top-up in background - DON'T WAIT for it to complete
        self._top_up_task = asyncio.create_task(self._run_fast_top_up_background())
        logger.info("🚀 Background tasks started")
        
        # Return the websocket task so it can be monitored
        return websocket_task
    
    async def _run_fast_top_up_background(self):
        """Run fast top-up in background without blocking startup"""
        try:
            # Check if we should stop before starting
            if self._should_stop():
                self.logger.info("⚠️ Shutdown detected, skipping background top-up")
                return
            
            await self.run_fast_top_up()
            self.logger.info("✅ Background top-up completed")
        except asyncio.CancelledError:
            self.logger.info("🛑 Background top-up cancelled")
            raise
        except Exception as e:
            self.logger.error(f"Background top-up failed: {e}")
    
    async def get_fallback_mark_prices_from_redis(self) -> Optional[Dict[str, float]]:
        try:
            clients = await get_ready_redis_clients()
            if not clients:
                return None
            fallback_prices: Dict[str, float] = {}
            for client in clients:
                try:
                    hash_data = await client.hgetall("mark_prices")
                    if not hash_data:
                        continue
                    for symbol, price_data in hash_data.items():
                        try:
                            decoded = json.loads(price_data) if isinstance(price_data, str) else price_data
                            if isinstance(decoded, dict):
                                value = decoded.get('price') or decoded.get('p')
                                if value is not None:
                                    fallback_prices[symbol] = float(value)
                            else:
                                fallback_prices[symbol] = float(decoded)
                        except (ValueError, TypeError, json.JSONDecodeError):
                            continue
                    if fallback_prices:
                        break
                except Exception:
                    continue
            if fallback_prices:
                self.logger.info(f"📡 Got {len(fallback_prices)} fallback mark prices from Redis")
                return fallback_prices
            self.logger.debug("No fallback mark prices available")
            return None
        except Exception as e:
            self.logger.debug(f"Error getting fallback mark prices: {e}")
            return None
    
    async def get_fallback_klines_from_redis(self, symbol: str, interval: str = '3m') -> Optional[pd.DataFrame]:
        try:
            clients = await get_ready_redis_clients()
            if not clients:
                return None
            redis_key = f"klines:{symbol}:{interval}"
            for client in clients:
                try:
                    json_data = await client.get(redis_key)
                    if not json_data:
                        continue
                    payload = json.loads(json_data)
                    klines_list = payload.get('klines') if isinstance(payload, dict) else payload
                    if not isinstance(klines_list, list) or not klines_list:
                        continue
                    df = pd.DataFrame(klines_list)
                    required_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
                    for col in required_columns:
                        if col not in df.columns:
                            df[col] = 0.0
                    if 'timestamp' in df.columns:
                        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce', utc=True)
                        df.dropna(subset=['timestamp'], inplace=True)
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    if df.empty:
                        continue
                    df = df[required_columns]
                    df.sort_values('timestamp', inplace=True)
                    df.reset_index(drop=True, inplace=True)
                    self.logger.debug(f"📡 Got {len(df)} fallback {interval} klines from Redis for {symbol}")
                    return df
                except Exception:
                    continue
            self.logger.debug(f"No fallback {interval} klines available for {symbol}")
            return None
        except Exception as e:
            self.logger.debug(f"Error getting fallback {interval} klines: {e}")
            return None
    
    async def setup_and_rename(self):
        self.logger.info("--- Phase 1: Reading config, fetching live pairs, and creating master symbol list ---")
        
        # Initialize the Redis manager (if available)
        with open(config.SYMBOLS_FILE, 'r') as f:
            initial_symbols = sorted(list(set(json.load(f))))
        
        live_usdc_pairs = await get_live_usdc_pairs(self.session)
        if live_usdc_pairs is None:
            raise RuntimeError("CRITICAL: Cannot fetch live USDC pairs from API. Aborting startup.")
        
        # --- FIX 1: Only create the file if it doesn't exist ---
        if not config.LIVE_USDC_PAIRS_FILE.exists():
            try:
                with open(config.LIVE_USDC_PAIRS_FILE, 'w') as f:
                    json.dump(sorted(list(live_usdc_pairs)), f, indent=2)
                self.logger.info(f"✅ Master USDC list created with {len(live_usdc_pairs)} pairs.")
            except Exception as e:
                self.logger.error(f"Failed to save live USDC pairs file: {e}")
                raise
        else:
            self.logger.info(f"✅ Using existing USDC list with {len(live_usdc_pairs)} pairs.")

        # --- FIX 2: Create and save the FINAL, authoritative list of symbols to trade ---
        # Use utils function for consistent USDC conversion
        self.symbols_to_run = sorted(force_usdc_in_list(initial_symbols, live_usdc_pairs))
        
        self.logger.info(f"✅ Final symbols determined: {len(self.symbols_to_run)} symbols after USDC conversion.")
        
        # --- FIX 3: Ensure price cache contains all symbols on startup ---
        await self._ensure_price_cache_complete_on_startup()
            
    async def _ensure_price_cache_complete_on_startup(self):    
        """Ensure the price cache contains all symbols on startup using resilient I/O."""
        try:
            self.logger.info("🔄 Ensuring price cache contains all symbols on startup...")
            
            # Use resilient load
            current_cache = await load_json_resilient(MARK_PRICES_CACHE_FILE)
            if not isinstance(current_cache, dict): current_cache = {}
            
            complete_symbols = set(self.symbols_to_run)
            missing_symbols = complete_symbols - set(current_cache.keys())
            
            if missing_symbols:
                self.logger.info(f"🔄 Adding {len(missing_symbols)} missing symbols...")
                ts_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                
                for symbol in missing_symbols:
                    current_cache[symbol] = {"price": 0.0, "timestamp": ts_str}
                
                # Use robust atomic write
                await atomic_write_json(MARK_PRICES_CACHE_FILE, current_cache)
                self.logger.info(f"✅ Startup: Updated price cache")
            else:
                self.logger.info("✅ Startup: Price cache is complete")
            
        except Exception as e:
            self.logger.error(f"Error ensuring price cache completion: {e}")

    async def run_fast_top_up(self):
        self.logger.info(f"--- Phase 2: Running FAST Additive {TOP_UP_BARS}-bar top-up ---")
        semaphore = API_SEMAPHORE
        tasks = [asyncio.create_task(self.top_up_one_file(s, semaphore)) for s in self.symbols_to_run]
        try:
            if self._should_stop():
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                return
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_continuous_operations(self, websocket_task=None):
        self.logger.info("--- ✅ Startup complete. Starting continuous real-time operations. ---")
        if websocket_task:
            self.logger.info("✅ WebSocket listener already running from startup")
        else:
            self.logger.warning("⚠️ No websocket task provided - starting new one")
            websocket_task = asyncio.create_task(self.listen_to_websockets())
        await self._publish_existing_3m_data()
        background_tasks = [
            asyncio.create_task(self.continuous_backfill_task()),
            asyncio.create_task(self.websocket_health_check_task()),
            asyncio.create_task(self.redis_fallback_monitor_task()),
            asyncio.create_task(self.periodic_tmp_cleanup_task()),
            asyncio.create_task(self.periodic_gap_status_report_task()),
            asyncio.create_task(self.periodic_3m_redis_refresh_task()),
            asyncio.create_task(self.periodic_price_cache_completion_task()),
            asyncio.create_task(self.periodic_rate_limit_status_task())
        ]
        
        # Add top-up task to tracked tasks if it exists
        if self._top_up_task:
            background_tasks.append(self._top_up_task)
        
        async def wait_for_shutdown():
            if self.shutdown_event:
                await self.shutdown_event.wait()
            else:
                await asyncio.Future()
        shutdown_task = asyncio.create_task(wait_for_shutdown())
        all_tasks = background_tasks + [shutdown_task]
        try:
            done, pending = await asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)
            if shutdown_task in done:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if websocket_task:
                    websocket_task.cancel()
                    with suppress(asyncio.CancelledError): await websocket_task
                return
        finally:
            for task in all_tasks:
                task.cancel()
            for task in all_tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if websocket_task:
                websocket_task.cancel()
                with suppress(asyncio.CancelledError): await websocket_task

    async def periodic_tmp_cleanup_task(self, interval_seconds: int = 900, min_age_seconds: int = 300):
        """Periodically remove leftover temp files in the klines cache directory."""
        if self._cleanup_task_started:
            return
        self._cleanup_task_started = True
        await asyncio.sleep(60)
        patterns = (".tmp_ez_", ".tmp_ez3m_", ".tmp")
        while True:
            if self._should_stop(): break
            try:
                now = time.time()
                removed = 0
                for entry in config.KLINES_CACHE_DIR.iterdir():
                    try:
                        if not entry.is_file():
                            continue
                        name = entry.name
                        if (name.endswith('.tmp') or name.startswith(patterns)):
                            mtime = entry.stat().st_mtime
                            if now - mtime > min_age_seconds:
                                entry.unlink(missing_ok=True)
                                removed += 1
                    except Exception:
                        continue
                if removed:
                    self.logger.info(f"🧹 Temp cleanup: removed {removed} stale temp files from klines_cache")
            except Exception as e:
                self.logger.warning(f"Temp cleanup error: {e}")
            await asyncio.sleep(interval_seconds)

    async def periodic_gap_status_report_task(self, interval_seconds: int = 300):
        """Periodically report gap status for all symbols to help with monitoring."""
        if hasattr(self, '_gap_report_task_started') and self._gap_report_task_started:
            return
        self._gap_report_task_started = True

        await asyncio.sleep(120)  # Wait 2 minutes after startup before first report

        while True:
            if self._should_stop(): break
            try:
                # Check rate limiting status before starting report
                if rate_limit_tracker.should_backoff():
                    remaining = rate_limit_tracker.get_backoff_remaining()
                    logger.warning(f"⏸️ [Gap Report] Rate limiting active. Skipping gap report (backoff: {remaining:.1f}s)")
                    await asyncio.sleep(interval_seconds)
                    continue

                logger.info("📊 === STARTING PERIODIC GAP STATUS REPORT ===")

                # Report status for a subset of symbols each time to avoid overwhelming logs
                symbols_to_check = list(self.symbols_to_run)[:20]  # Check first 20 symbols each cycle

                for symbol in symbols_to_check:
                    try:
                        # Report both 3m and 3m status
                        await self._report_gap_status(symbol, '3min')
                        await asyncio.sleep(0.1)  # Small delay to avoid blocking
                    except Exception as e:
                        logger.debug(f"Error reporting gap status for {symbol}: {e}")

                logger.info("📊 === COMPLETED PERIODIC GAP STATUS REPORT ===")

            except Exception as e:
                logger.error(f"Error in periodic gap status report: {e}")

            await asyncio.sleep(interval_seconds)

    async def periodic_rate_limit_status_task(self, interval_seconds: int = 60):
        """Periodically log the current rate limiting status."""
        if hasattr(self, '_rate_limit_status_task_started') and self._rate_limit_status_task_started:
            return
        self._rate_limit_status_task_started = True

        await asyncio.sleep(300)  # Wait 5 minutes after startup before first status check

        while True:
            if self._should_stop(): break
            try:
                if rate_limit_tracker.should_backoff():
                    remaining = rate_limit_tracker.get_backoff_remaining()
                    logger.warning(f"🚫 RATE LIMIT STATUS: Active backoff for {remaining:.1f}s (errors: {rate_limit_tracker.consecutive_errors})")
                else:
                    logger.debug(f"✅ RATE LIMIT STATUS: Normal operation (errors: {rate_limit_tracker.consecutive_errors})")

                await asyncio.sleep(interval_seconds)

            except Exception as e:
                logger.error(f"Error in rate limit status task: {e}")
                await asyncio.sleep(interval_seconds)

    async def periodic_3m_redis_refresh_task(self, interval_seconds: int = 600):
        """Periodically refresh 3m data in Redis to ensure data availability."""
        if hasattr(self, '_3m_refresh_task_started') and self._3m_refresh_task_started:
            return
        self._3m_refresh_task_started = True
        
        await asyncio.sleep(300)  # Wait 5 minutes after startup before first refresh
        
        while True:
            if self._should_stop(): break
            try:
                # Check rate limiting status before starting refresh
                if rate_limit_tracker.should_backoff():
                    remaining = rate_limit_tracker.get_backoff_remaining()
                    logger.warning(f"⏸️ [Redis Refresh] Rate limiting active. Skipping Redis refresh (backoff: {remaining:.1f}s)")
                    await asyncio.sleep(interval_seconds)
                    continue

                logger.info("🔄 === STARTING PERIODIC 3M REDIS REFRESH ===")

                # Refresh 3m data for a subset of symbols each cycle
                symbols_to_refresh = list(self.symbols_to_run)[:30]
                refreshed_count = 0
                for symbol in symbols_to_refresh:
                    try:
                        fp = config.KLINES_CACHE_DIR / f"{symbol}_3m.json"
                        if not fp.exists():
                            continue
                        df = await self._read_file_unlocked(fp)
                        if df.empty or len(df) <= 10:
                            continue
                        recent_df = df.tail(TARGET_BARS_3m)
                        redis_klines = []
                        for _, row in recent_df.iterrows():
                            redis_klines.append({
                                'timestamp': (row['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ') if hasattr(row['timestamp'], 'strftime') else str(row['timestamp'])),
                                'open': float(row['open']),
                                'high': float(row['high']),
                                'low': float(row['low']),
                                'close': float(row['close']),
                                'volume': float(row['volume'])
                            })
                        if not redis_klines:
                            continue
                        payload = {
                            'symbol': symbol,
                            'interval': '3m',
                            'published_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            'klines': redis_klines,
                            'count': len(redis_klines)
                        }
                        success = await publish_json(f"klines:{symbol}:3m", payload, expiry_seconds=config.REDIS_EXPIRY_SECONDS * 6)
                        if success > 0:
                            refreshed_count += 1
                            logger.debug(f"🔄 Refreshed {len(redis_klines)} 3m bars in Redis for {symbol}")
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        logger.debug(f"Error refreshing 3m Redis data for {symbol}: {e}")
                
                logger.info(f"🔄 === COMPLETED PERIODIC 3M REDIS REFRESH: {refreshed_count} symbols refreshed ===")
                
            except Exception as e:
                logger.error(f"Error in periodic 3m Redis refresh: {e}")
            
            await asyncio.sleep(interval_seconds)

    async def periodic_price_cache_completion_task(self, interval_seconds: int = 300):
        """Periodically ensure the price cache contains all symbols."""
        if hasattr(self, '_price_cache_completion_task_started') and self._price_cache_completion_task_started:
            return
        self._price_cache_completion_task_started = True
        
        await asyncio.sleep(180)  # Wait 3 minutes after startup before first check
        
        while True:
            if self._should_stop(): break
            try:
                logger.info("🔄 === STARTING PERIODIC PRICE CACHE COMPLETION ===")
                
                # Read the current price cache
                current_cache = {}
                if MARK_PRICES_CACHE_FILE.exists():
                    try:
                        with open(MARK_PRICES_CACHE_FILE, 'r') as f:
                            current_cache = json.load(f)
                    except Exception as e:
                        logger.warning(f"Failed to read current price cache: {e}")
                        current_cache = {}
                
                # Get the complete symbol list
                complete_symbols = set()
                try:
                    with open(config.SYMBOLS_FILE, 'r') as f:
                        symbols_data = json.load(f)
                        complete_symbols = set(symbols_data)
                except Exception as e:
                    logger.warning(f"Failed to read symbols file: {e}")
                    continue
                
                # Check which symbols are missing
                missing_symbols = complete_symbols - set(current_cache.keys())
                
                if missing_symbols:
                    logger.info(f"🔄 Adding {len(missing_symbols)} missing symbols to price cache")
                    
                    # Add placeholders for missing symbols
                    current_time = datetime.now(timezone.utc)
                    for symbol in missing_symbols:
                        current_cache[symbol] = {
                            "price": 0.0,  # Placeholder price
                            "timestamp": current_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        }
                    
                    # Save updated cache
                    temp_file = MARK_PRICES_CACHE_FILE.with_suffix('.tmp')
                    try:
                        with open(temp_file, 'w') as f:
                            json.dump(current_cache, f, indent=2)
                        temp_file.replace(MARK_PRICES_CACHE_FILE)
                        logger.info(f"✅ Updated price cache with {len(missing_symbols)} missing symbols")
                    except Exception as e:
                        logger.error(f"Failed to update price cache: {e}")
                        if temp_file.exists():
                            temp_file.unlink()
                else:
                    logger.info("✅ Price cache is complete - all symbols present")
                
                logger.info(f"🔄 === COMPLETED PRICE CACHE COMPLETION: {len(current_cache)} total symbols ===")
                
            except Exception as e:
                logger.error(f"Error in periodic price cache completion: {e}")
            
            await asyncio.sleep(interval_seconds)

    async def listen_to_websockets(self):
        logger.info("Preparing to connect to real-time streams...")
        api_symbols_to_sub = list(self.api_symbol_to_original_map.keys())
        
        # Combine all necessary streams
        kline_3m_streams = [f"{api_sym.lower()}@kline_3m" for api_sym in api_symbols_to_sub]
        mark_price_streams = [f"{api_sym.lower()}@markPrice@1s" for api_sym in api_symbols_to_sub]
        all_streams = kline_3m_streams + mark_price_streams
        
        # Use batched connections for stability
        MAX_STREAMS_PER_CONNECTION = 100
        stream_batches = [all_streams[i:i + MAX_STREAMS_PER_CONNECTION] for i in range(0, len(all_streams), MAX_STREAMS_PER_CONNECTION)]
        
        tasks = [self._websocket_connection_handler(batch, i) for i, batch in enumerate(stream_batches)]
        await asyncio.gather(*tasks)

    async def _websocket_connection_handler(self, stream_batch: List[str], batch_id: int):
        """(NEW) Handles a single, resilient WebSocket connection for a batch of streams."""
        log_prefix = f"[WebSocket-Batch-{batch_id}]"
        reconnect_delay = 5
        
        while True:
            try:
                stream_url = f"{config.FSTREAM_WS_URL_BASE}?streams={'/'.join(stream_batch)}"
                logger.info(f"{log_prefix} Attempting to connect...")
                
                # --- DEFENSE 1: More lenient Ping/Pong settings for stability ---
                async with websockets.connect(
                    stream_url,
                    ssl=config.ssl_context,
                    ping_interval=None,  # Disable automatic ping - Binance handles this
                    ping_timeout=None,   # Disable ping timeout checks
                    close_timeout=10
                ) as ws:
                    logger.info(f"{log_prefix} ✅ Connection successful.")
                    reconnect_delay = 5  # Reset delay on success
                    last_message_time = time.time()

                    while True:
                        try:
                            # --- DEFENSE 2: Timeout on Receive ---
                            # Don't wait forever. If no message (not even a ping) is received in 60s,
                            # something is wrong.
                            msg = await asyncio.wait_for(ws.recv(), timeout=60.0)
                            last_message_time = time.time()
                            # Update instance variable for fallback monitoring
                            self.last_websocket_message_time = last_message_time
                            
                            # --- Process the message ---
                            payload = json.loads(msg)
                            data = payload.get('data')
                            if not data: continue

                            event_type = data.get('e')
                            if event_type == 'markPriceUpdate':
                                original_symbol = self.api_symbol_to_original_map.get(data['s'])
                                if original_symbol:
                                    # Use direct Redis publishing - matching ez_prices_ws.py pattern
                                    await self._broadcast_mark_prices_direct({original_symbol: float(data['p'])})
                            elif event_type == 'kline' and data.get('k', {}).get('x'): # 'x' means kline is closed
                                    kline = data['k']
                                    original_symbol = self.api_symbol_to_original_map.get(kline['s'])
                                    if original_symbol and kline['i'] == '3m':
                                        try:
                                            # --- APPLY THE GOLDEN RULE ---
                                            close_time = pd.to_datetime(kline['T'], unit='ms', utc=True) # Use T for close time
                                            normalized_ts = self._normalize_and_floor_timestamp(pd.Series([close_time])).iloc[0]

                                            latest_kline = {
                                                'timestamp': normalized_ts, # Use the clean, canonical timestamp
                                                'open': float(kline['o']),
                                                'high': float(kline['h']),
                                                'low': float(kline['l']),
                                                'close': float(kline['c']),
                                                'volume': float(kline['v'])
                                            }
        
                                            await self._broadcast_3m_to_redis(original_symbol, latest_kline)
                                            await self._write_3m_to_disk(original_symbol, latest_kline)
                                            await broadcast_latest_kline_to_redis(original_symbol, latest_kline)

                                        except (ValueError, TypeError) as e:
                                            logger.error(f"[{original_symbol}] Could not process kline due to invalid data: {e}. Data: {kline}")
                                        except Exception:
                                            # Catch other potential errors, like timestamp conversion
                                            pass         
                                        
                        except asyncio.TimeoutError:
                            logger.warning(f"{log_prefix} ⚠️ No message received for 60 seconds (receive timeout). Reconnecting...")
                            break # Break inner loop to force reconnection
                        
                        except Exception as e:
                            logger.error(f"{log_prefix} Error processing message: {e}")
                            # Continue trying to process messages unless it's a connection error
                            try:
                                if ws.closed:
                                    break
                            except AttributeError:
                                # For older websockets versions, just break on any error
                                break

            except Exception as e:
                logger.error(f"{log_prefix} ❌ WebSocket connection failed: {e}. Retrying in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 300) # Exponential backoff

    async def _broadcast_3m_to_redis(self, symbol: str, latest_kline: Dict):
        try:
            klines_list = [latest_kline]
            payload = {
                "symbol": symbol,
                "interval": "3m",
                "published_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "klines": klines_list,
                "count": len(klines_list)
            }
            # Use direct Redis publish - matching ez_prices_ws.py pattern
            success = await self._publish_to_redis_direct(f"klines:{symbol}:3m", payload, expiry_seconds=config.REDIS_EXPIRY_SECONDS * 6)
            return success > 0
        except Exception as e:
            self.logger.error(f"Error broadcasting 3m data to Redis for {symbol}: {e}")
            return False
        
    async def _write_file_unlocked(self, df: pd.DataFrame, fp: Path):

        tmp_file_path = None
        try:
            # Ensure we have valid data
            if df.empty:
                logger.warning(f"[{fp.stem}] Attempting to write empty DataFrame, skipping...")
                return False
            
            # Remove any rows with invalid timestamps
            df_clean = df.dropna(subset=['timestamp']).copy()
            if len(df_clean) == 0:
                logger.error(f"[{fp.stem}] No valid timestamps found in DataFrame")
                return False
            
            # Clip the DataFrame to the target size before saving
            if len(df_clean) > MAX_BARS_3m:
                df_safe = df_clean.tail(TARGET_BARS_3m).copy()
                logger.info(f"[{fp.stem}] Clipping file: {len(df_clean)} -> {len(df_safe)} bars (Max: {MAX_BARS_3m}, Target: {TARGET_BARS_3m}).")
            else:
                df_safe = df_clean.copy()
            
            # Ensure timestamps are in correct format
            df_safe['timestamp'] = df_safe['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            
            # Validate the data before writing
            if len(df_safe) == 0:
                logger.error(f"[{fp.stem}] DataFrame is empty after processing, aborting write")
                return False
            
            # Create temporary file
            with tempfile.NamedTemporaryFile('w', dir=fp.parent, delete=False, suffix='.tmp', encoding='utf-8') as tmp_file:
                json_str = df_safe.to_json(orient='records', indent=2)
                await asyncio.to_thread(tmp_file.write, json_str)
                await asyncio.to_thread(tmp_file.flush)
                await asyncio.to_thread(os.fsync, tmp_file.fileno())
                tmp_file_path = tmp_file.name
            
            # Atomic replace
            if tmp_file_path and os.path.exists(tmp_file_path):
                await asyncio.to_thread(os.replace, tmp_file_path, fp)
                
                # Verify the file was written correctly
                if fp.exists() and fp.stat().st_size > 0:
                    logger.debug(f"✅ Successfully wrote {len(df_safe)} bars to {fp.name}")
                    return True
                else:
                    logger.error(f"❌ File write verification failed for {fp.name}")
                    return False
            else:
                logger.error(f"❌ Temporary file creation failed for {fp.name}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error writing file {fp.name}: {e}")
            return False
        finally:
            # Clean up temporary file
            if tmp_file_path and os.path.exists(tmp_file_path):
                try:
                    os.remove(tmp_file_path)
                except OSError as e:
                    logger.error(f"Error removing temp file {tmp_file_path}: {e}")

    async def top_up_one_file(self, symbol: str, semaphore: asyncio.Semaphore):
            """
            (FIXED) Tops up a file on startup, ensuring it only appends new data and never overwrites with empty data.
            """
            async with semaphore:
                fp = config.KLINES_CACHE_DIR / f"{symbol}_3m.json"
                existing_df = await self._read_file_unlocked(fp)
                existing_count = len(existing_df)

                # --- FIX: Fetch top-up data from the API first ---
                top_up_data = await self.fetch_klines(symbol, TOP_UP_BARS)

                # --- CRITICAL FIX: If API fetch failed or returned no data, ABORT and leave the original file alone. ---
                if top_up_data.empty:
                    if existing_count > 0:
                        logger.warning(f"[{symbol}] API fetch for top-up returned no data. Existing {existing_count} bars are safe and remain untouched.")
                    else:
                        logger.error(f"[{symbol}] API fetch for initial data failed. File will be created later by backfill.")
                    return # Exit the function immediately

                # If we have no existing data, the top_up_data is our new file.
                if existing_count == 0:
                    final_df = top_up_data
                    logger.info(f"[{symbol}] New file created with {len(final_df)} bars from API.")
                else:
                    # --- FIX: This is the core logic to ensure we only APPEND ---
                    # 1. Get the timestamp of the very latest bar we have on disk.
                    latest_existing_time = existing_df['timestamp'].max()

                    # 2. Filter the newly fetched data to ONLY include bars that are strictly newer.
                    new_data_only = top_up_data[top_up_data['timestamp'] > latest_existing_time]

                    if not new_data_only.empty:
                        # 3. Concatenate the old data with the *new bars only*.
                        final_df = pd.concat([existing_df, new_data_only], ignore_index=True).sort_values('timestamp')
                        added_bars = len(new_data_only)
                        logger.info(f"[{symbol}] ADDITIVE top-up: Kept {existing_count} existing + added {added_bars} new bars = {len(final_df)} total.")
                    else:
                        # If there are no new bars, there's nothing to do. The existing data is up-to-date.
                        logger.info(f"[{symbol}] Data is already up-to-date. No new bars to add.")
                        final_df = existing_df

                # The clipping of oversized files will be handled by the robust _write_file_unlocked function.
                await self._write_file_unlocked(final_df, fp)

    async def backfill_symbol_data(self, symbol: str, session: aiohttp.ClientSession) -> int:
        """
        (REVISED) Fetches and prepends historical data, now respecting the MAX_BARS_3m limit.
        """
        fp = config.KLINES_CACHE_DIR / f"{symbol}_3m.json"
        existing_df = await self._read_file_unlocked(fp)

        if existing_df.empty:
            return 0
            
        # Do not backfill if we already have enough data.
        if len(existing_df) >= MAX_BARS_3m:
            return 0

        oldest_ts = existing_df['timestamp'].min()
        
        fetched_df = await self.fetch_klines(symbol, BACKFILL_CHUNK_SIZE, end_dt=oldest_ts)

        if not fetched_df.empty:
            combined = pd.concat([fetched_df, existing_df])
            final_df = combined.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
            
            # The clipping to TARGET_BAR_COUNT happens inside _write_file_unlocked
            await self._write_file_unlocked(final_df, fp)
            return len(final_df) - len(existing_df)
        
        return 0
    
    async def get_priority_backfill_symbol(self) -> Optional[str]:
        """Finds the symbol with the fewest bars."""
        counts = []
        for symbol in self.symbols_to_run:
            try:
                fp = config.KLINES_CACHE_DIR / f"{symbol}_3m.json"
                df = await self._read_file_unlocked(fp)
                if len(df) < TARGET_BARS_3m:
                    counts.append((symbol, len(df)))
            except Exception as e:
                logger.error(f"Error checking file size for {symbol}: {e}")
                counts.append((symbol, 0))
        if not counts:
            return None
        return min(counts, key=lambda x: x[1])[0]

    async def get_oldest_bar_date(self, symbol: str) -> str:
        """Gets the date of the oldest bar for a symbol."""
        fp = config.KLINES_CACHE_DIR / f"{symbol}_3m.json"
        df = await self._read_file_unlocked(fp)
        if not df.empty:
            return df['timestamp'].min().strftime('%Y-%m-%d %H:%M:%S')
        return "N/A"

    async def continuous_backfill_task(self):
            """
            (FIXED) Continuously and gently backfills data for the symbol with the fewest bars,
            with improved rate limiting to prevent API bans.
            """
            logger.info("[Maintainer] Starting continuous backfill task...")
            await asyncio.sleep(60)

            while True:
                if self._should_stop(): break
                try:
                    priority_symbol = await self.get_priority_backfill_symbol()

                    if priority_symbol:
                        current_bars = len(await self._read_file_unlocked(config.KLINES_CACHE_DIR / f"{priority_symbol}_3m.json"))
                        target_bars = TARGET_BARS_3m

                        # Check if we should back off due to rate limiting
                        if rate_limit_tracker.should_backoff():
                            remaining = rate_limit_tracker.get_backoff_remaining()
                            logger.warning(f"⏸️ [Maintainer] Rate limiting active for backfill. Waiting {remaining:.1f}s")
                            await asyncio.sleep(min(remaining, 60))  # Sleep up to 1 minute or remaining time
                            continue  # Skip this iteration

                        # If we are significantly behind, backfill more aggressively.
                        if current_bars < target_bars:
                            logger.info(f"[Maintainer] Priority: '{priority_symbol}' has {current_bars}/{target_bars} bars. Fetching older data...")
                            async with aiohttp.ClientSession() as session:
                                added_bars = await self.backfill_symbol_data(priority_symbol, session)
                                if added_bars > 0:
                                    new_total = current_bars + added_bars
                                    completion_pct = (new_total / target_bars) * 100
                                    oldest_bar = await self.get_oldest_bar_date(priority_symbol)
                                    logger.info(f"[Maintainer] ✅ '{priority_symbol}' +{added_bars} bars -> {new_total}/{target_bars} ({completion_pct:.1f}%). Oldest: {oldest_bar}")
                                else:
                                    logger.warning(f"[Maintainer] ⚠️ Backfill for '{priority_symbol}' returned no new bars. The API might be out of data or rate-limiting.")

                        else:
                            # If the priority symbol is already full, it means all symbols are full.
                            logger.info(f"[Maintainer] All symbols have at least {target_bars} bars. Backfill is idle.")
                    else:
                        logger.info("[Maintainer] All symbols are fully backfilled. Task is idle.")

                    # --- RATE LIMITING FIX ---
                    # Determine sleep time. Sleep longer if data is mostly complete.
                    if priority_symbol and current_bars < (target_bars * 0.9):
                        # If we are less than 90% complete, be more aggressive.
                        sleep_time = BACKFILL_SLEEP_SECONDS
                        logger.debug(f"[Maintainer] Sleeping for {sleep_time}s (aggressive backfill).")
                    else:
                        # If we are nearly or fully complete, slow down significantly.
                        sleep_time = 300 # 5 minutes
                        logger.debug(f"[Maintainer] Data is nearly complete. Sleeping for {sleep_time}s to conserve API quota.")

                    await asyncio.sleep(sleep_time)

                except Exception as e:
                    logger.error(f"[Maintainer] Unhandled error in backfill task: {e}", exc_info=True)
                    # Wait for a longer period after a major error.
                    await asyncio.sleep(60)
                    
    async def websocket_health_check_task(self):
        """Periodically check WebSocket connection health and log status."""
        await asyncio.sleep(60)  # Wait for initial setup
        
        while True:
            if self._should_stop(): break
            try:
                # Log WebSocket connection status
                current_time = time.time()
                if hasattr(self, 'last_websocket_message_time'):
                    time_since_last_message = current_time - self.last_websocket_message_time
                    if time_since_last_message > 300:  # 5 minutes
                        logger.warning(f"⚠️ No WebSocket messages received for {time_since_last_message:.0f} seconds")
                    elif time_since_last_message > 120:  # 2 minutes
                        logger.info(f"ℹ️ WebSocket quiet for {time_since_last_message:.0f} seconds")
                    else:
                        logger.debug(f"✅ WebSocket active, last message {time_since_last_message:.0f}s ago")
                else:
                    logger.info("ℹ️ WebSocket health check initialized")
                
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"WebSocket health check error: {e}")
                await asyncio.sleep(60)

    async def redis_fallback_monitor_task(self):
        """Monitor WebSocket data freshness and use Redis fallback when needed."""
        await asyncio.sleep(120)  # Wait for initial setup
        
        while True:
            if self._should_stop(): break
            try:
                current_time = time.time()
                
                # Check if WebSocket data is stale
                if hasattr(self, 'last_websocket_message_time') and self.last_websocket_message_time:
                    time_since_last_message = current_time - self.last_websocket_message_time
                    
                    # If WebSocket data is older than threshold, try Redis fallback
                    if time_since_last_message > self.websocket_data_freshness_threshold:
                        logger.warning(f"⚠️ WebSocket data is stale ({time_since_last_message:.0f}s old). Trying Redis fallback...")
                        
                        # Get fallback mark prices from Redis
                        fallback_prices = await self.get_fallback_mark_prices_from_redis()
                        if fallback_prices:
                            # Publish fallback prices using unified manager
                            for symbol, price in fallback_prices.items():
                                try:
                                    # Convert to original symbol format if needed
                                    original_symbol = symbol.replace("USDT", "USDC") if symbol.endswith("USDT") else symbol
                                    
                                    # Publish using unified manager
                                    await broadcast_mark_prices_to_redis({original_symbol: price})
                                    logger.debug(f"📡 Published fallback mark price for {original_symbol}: {price}")
                                    
                                except Exception as e:
                                    logger.debug(f"Error publishing fallback price for {symbol}: {e}")
                            
                            logger.info(f"✅ Published {len(fallback_prices)} fallback mark prices from Redis")
                        else:
                            logger.warning("⚠️ No fallback data available from Redis")
                
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Redis fallback monitor error: {e}")
                await asyncio.sleep(30)

    async def _publish_existing_3m_data(self):
        """Publish existing 3m data from disk to Redis on startup."""
        logger.info("🚀 Publishing existing 3m data to Redis on startup...")
        
        try:
            # Process symbols in batches
            batch_size = 30
            published_count = 0
            
            for i in range(0, len(self.symbols_to_run), batch_size):
                batch = self.symbols_to_run[i:i + batch_size]
                
                # Process batch concurrently
                tasks = []
                for symbol in batch:
                    tasks.append(self._publish_existing_3m_for_symbol(symbol))
                
                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    published_count += sum(1 for r in results if r is True)
                
                # Small delay between batches
                await asyncio.sleep(0.5)
            
            logger.info(f"✅ Startup: Published 3m data for {published_count} symbols to Redis")
            
        except Exception as e:
            logger.error(f"Startup 3m publishing error: {e}")

    async def _publish_existing_3m_for_symbol(self, symbol: str) -> bool:
        """Publish existing 3m data for a single symbol from disk to Redis."""
        # --- REMOVED: 3m handling moved to ez_prices_ws.py which gets data directly from Binance ---
        # This function is no longer needed since ez_prices_ws.py handles 3m data directly
        return True

    async def _write_3m_to_disk(self, symbol: str, latest_kline: Dict):
        """Write 3m kline data to disk JSON file."""
        try:
            fp = config.KLINES_CACHE_DIR / f"{symbol}_3m.json"
            lock = self._file_write_locks.setdefault(str(fp), asyncio.Lock())
            async with lock:
                # Read existing 3m data
                existing_df = await self._read_file_unlocked(fp)
                
                # Convert the new 3m bar to DataFrame format
                new_bar_df = pd.DataFrame([latest_kline])
                if not pd.api.types.is_datetime64_any_dtype(new_bar_df['timestamp']):
                    new_bar_df['timestamp'] = pd.to_datetime(new_bar_df['timestamp'], utc=True)
                
                # Merge with existing data
                if not existing_df.empty:
                    if existing_df['timestamp'].dtype == 'object':
                        existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'], utc=True)
                    combined_df = pd.concat([existing_df, new_bar_df], ignore_index=True)
                    combined_df = combined_df.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
                    if len(combined_df) > MAX_BARS_3m:
                        combined_df = combined_df.tail(TARGET_BARS_3m)
                        logger.debug(f"[{symbol}] Clipped 3m file to {TARGET_BARS_3m} bars")
                else:
                    combined_df = new_bar_df
                
                # Write to disk
                await self._write_file_unlocked(combined_df, fp)
                
                # Post-write continuity self-check on the last 2 000 bars
                written = await self._read_file_unlocked(fp)
                if not written.empty:
                    tail_df = written.tail(1500).copy()
                    if self._has_time_gaps(tail_df, freq='3min'):
                        logger.warning(f"🔍 [{symbol}] Detected 3m gaps after write. Attempting repair...")
                        await self._repair_3m_gaps(symbol, tail_df)
                    else:
                        logger.debug(f"✅ [{symbol}] 3m data continuity verified - no gaps detected")
                logger.debug(f"✅ Wrote 3m data to disk for {symbol}: {latest_kline['timestamp']}")
            
        except Exception as e:
            logger.error(f"Failed to write 3m data to disk for {symbol}: {e}")

    def _has_time_gaps(self, df: pd.DataFrame, freq: str = '3min') -> bool:
        """Enhanced gap detection that handles both 3m and 3m data more robustly."""
        try:
            if df.empty or len(df) < 2:
                return False
            
            s = pd.to_datetime(df['timestamp'], utc=True)
            s = s.sort_values().dropna()
            
            if len(s) < 2:
                return False
            
            # Calculate expected interval based on frequency
            if freq == '3min':
                expected_interval = pd.Timedelta(minutes=3)
            else:
                expected_interval = pd.Timedelta(minutes=1)  # Default to 1m
            
            # Check for gaps by looking at consecutive timestamps
            gaps_found = False
            gap_details = []
            for i in range(1, len(s)):
                time_diff = s.iloc[i] - s.iloc[i-1]
                # Allow some tolerance (1.5x expected interval) and ignore single kline gaps
                if time_diff > expected_interval * 1.5:
                    # Calculate how many klines are missing
                    expected_klines = int(time_diff.total_seconds() / expected_interval.total_seconds())
                    if expected_klines > 1:  # Only consider gaps of 2+ klines
                        gaps_found = True
                        gap_details.append({
                            'gap_start': s.iloc[i-1],
                            'gap_end': s.iloc[i],
                            'gap_duration': time_diff,
                            'expected_interval': expected_interval,
                            'missing_klines': expected_klines
                        })
            
            # Log gap information intelligently to reduce spam
            if gaps_found:
                # Log summary instead of individual gaps to reduce spam
                total_duration = sum(gap['gap_duration'] for gap in gap_details)
                max_gap = max(gap['gap_duration'] for gap in gap_details)
                logger.warning(f"🔍 GAP DETECTED in {freq} data: {len(gap_details)} gaps found, total duration: {total_duration}, max gap: {max_gap}")
                
                # Only log individual gap details for significant gaps or when debugging
                significant_gaps = [gap for gap in gap_details if gap['gap_duration'] > expected_interval * 5]  # 5x expected interval
                if significant_gaps:
                    logger.warning(f"⚠️ Significant gaps ({len(significant_gaps)}):")
                    for i, gap in enumerate(significant_gaps):
                        logger.warning(f"  Gap {i+1}: {gap['gap_start']} → {gap['gap_end']} (Duration: {gap['gap_duration']}, Expected: {gap['expected_interval']})")
                
                # Log first few gaps for context, then summarize the rest
                if len(gap_details) > 3:
                    logger.warning(f"📊 First 3 gaps shown above, {len(gap_details) - 3} additional gaps (use DEBUG level for full details)")
            else:
                logger.debug(f"✅ No gaps detected in {freq} data (checked {len(s)} timestamps)")
            
            return gaps_found
            
        except Exception as e:
            logger.debug(f"Error in gap detection: {e}")
            return False

    def _validate_data_continuity(self, df: pd.DataFrame, freq: str = '3min') -> bool:
        """Validate that data is continuous before writing to prevent gaps."""
        try:
            if df.empty or len(df) < 2:
                return True  # Empty or single row is considered "continuous"
            
            s = pd.to_datetime(df['timestamp'], utc=True)
            s = s.sort_values().dropna()
            
            if len(s) < 2:
                return True
            
            # Calculate expected interval based on frequency
            if freq == '3min':
                expected_interval = pd.Timedelta(minutes=3)
            else:
                expected_interval = pd.Timedelta(minutes=1)  # Default to 1m
            
            # Check for any gaps in the data
            for i in range(1, len(s)):
                time_diff = s.iloc[i] - s.iloc[i-1]
                # Allow some tolerance (1.5x expected interval)
                if time_diff > expected_interval * 1.5:
                    logger.warning(f"Data continuity validation failed: gap detected between {s.iloc[i-1]} and {s.iloc[i]} (diff: {time_diff})")
                    return False
            
            return True
            
        except Exception as e:
            logger.debug(f"Error in data continuity validation: {e}")
            return True  # Default to allowing the write if validation fails

    async def _repair_3m_gaps(self, symbol: str, df_window: pd.DataFrame):
        """Repair 3m gaps - IGNORES single kline gaps to reduce noise"""
        try:
            logger.info(f"🔧 Starting 3m gap repair for {symbol}...")
            
            s = pd.to_datetime(df_window['timestamp'], utc=True).sort_values()
            gaps = []
            prev = None
            for ts in s:
                if prev is not None:
                    time_diff = ts - prev
                    # Only consider gaps of 2+ minutes (ignore single kline gaps)
                    if time_diff > pd.Timedelta(minutes=2):
                        gaps.append((prev, ts))
                prev = ts
            
            if not gaps:
                logger.info(f"✅ No significant 3m gaps found for {symbol} - no repair needed")
                return
            
            logger.warning(f"🔍 Found {len(gaps)} significant 3m gaps in {symbol}, attempting repair...")
            
            # Log gap summary instead of individual gaps to reduce spam
            total_duration = sum((end - start) for start, end in gaps)
            max_gap = max((end - start) for start, end in gaps)
            logger.warning(f"📊 Gap summary: total duration: {total_duration}, max gap: {max_gap}")
            
            # Only log individual gaps for significant ones or when debugging
            significant_gaps = [(start, end) for start, end in gaps if (end - start) > pd.Timedelta(minutes=5)]
            if significant_gaps:
                logger.warning(f"⚠️ Significant 3m gaps ({len(significant_gaps)}):")
                for i, (start, end) in enumerate(significant_gaps):
                    gap_duration = end - start
                    logger.warning(f"  Gap {i+1}: {start} → {end} (Duration: {gap_duration})")
            
            # Check if we should back off due to rate limiting before gap repair
            if rate_limit_tracker.should_backoff():
                remaining = rate_limit_tracker.get_backoff_remaining()
                logger.warning(f"⏸️ [Gap Repair] Rate limiting active. Skipping gap repair for {symbol} (backoff: {remaining:.1f}s)")
                return

            # Attempt a focused fetch around the last gap
            start, end = gaps[-1]
            minutes_missing = int((end - start).total_seconds() // 60) - 1
            limit = 1500#max(10, min(1500, minutes_missing + 5))

            logger.info(f"🔧 Fetching {limit} bars to repair gap from {start} to {end} (estimated {minutes_missing} minutes missing)")

            fetched = await self.fetch_klines(symbol, limit=limit, end_dt=end)
            if not fetched.empty:
                logger.info(f"📥 Successfully fetched {len(fetched)} bars for gap repair")
                
                fp = config.KLINES_CACHE_DIR / f"{symbol}_3m.json"
                existing = await self._read_file_unlocked(fp)
                
                # Log data before repair
                logger.info(f"📊 Before repair: {len(existing)} existing bars, {len(fetched)} new bars")
                
                combined = pd.concat([existing, fetched]).drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
                if len(combined) > MAX_BARS_3m:
                    combined = combined.tail(TARGET_BARS_3m)
                    logger.info(f"✂️ Clipped combined data to {len(combined)} bars (max allowed)")
                
                await self._write_file_unlocked(combined, fp)
                
                # Verify repair success
                if self._has_time_gaps(combined, freq='3min'):
                    logger.warning(f"⚠️ Gap repair for {symbol} may not have been fully successful - gaps still detected")
                else:
                    logger.info(f"✅ Gap repair for {symbol} successful! Data is now continuous")
                
                logger.info(f"🔧 Repaired 3m gaps by fetching {len(fetched)} bars (limit={limit}) for {symbol}")
            else:
                logger.error(f"❌ Failed to fetch data for gap repair in {symbol}")
                
        except Exception as e:
            logger.error(f"❌ Repair 3m gaps failed for {symbol}: {e}")
            logger.exception(f"Full error details for {symbol} gap repair:")

    async def _report_gap_status(self, symbol: str, freq: str = '3min'):
        """Report detailed gap status for a symbol to help with monitoring."""
        try:
            fp = config.KLINES_CACHE_DIR / f"{symbol}_{freq.replace('min', 'm')}.json"
            if not fp.exists():
                logger.debug(f"📊 {symbol} {freq}: No data file found")
                return
            
            df = await self._read_file_unlocked(fp)
            if df.empty:
                logger.debug(f"📊 {symbol} {freq}: Data file is empty")
                return
            
            # Convert timestamps and sort
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
            df = df.sort_values('timestamp')
            
            # Calculate data quality metrics
            total_bars = len(df)
            time_span = df['timestamp'].max() - df['timestamp'].min()
            expected_interval = pd.Timedelta(minutes=3 if freq == '3min' else 1)
            expected_bars = int(time_span.total_seconds() / expected_interval.total_seconds()) + 1
            days_span = time_span.days + time_span.seconds / 86400
            
            # Check for gaps
            gaps_found = self._has_time_gaps(df, freq)
            
            # Log comprehensive status
            if gaps_found:
                logger.warning(f"📊 {symbol} {freq}: GAPS DETECTED - {total_bars} bars over {days_span:.2f} days / {time_span} (expected ~{expected_bars})")
            else:
                logger.info(f"📊 {symbol} {freq}: ✅ CONTINUOUS - {total_bars} bars over {days_span:.2f} days / {time_span} (expected ~{expected_bars})")
                
            # Log data freshness
            latest_ts = df['timestamp'].max()
            age = pd.Timestamp.now(tz='UTC') - latest_ts
            if age > pd.Timedelta(minutes=5):
                logger.warning(f"📊 {symbol} {freq}: Data is {age} old (latest: {latest_ts})")
            else:
                logger.debug(f"📊 {symbol} {freq}: Data is fresh ({age} old)")
                
        except Exception as e:
            logger.debug(f"Error reporting gap status for {symbol} {freq}: {e}")

    async def get_gap_status_summary(self):
        """Get a comprehensive summary of gap status for all symbols."""
        try:
            logger.info("📊 === COMPREHENSIVE GAP STATUS SUMMARY ===")
            
            gap_summary = {
                '3min': {'continuous': [], 'gaps_detected': [], 'no_data': []}
            }
            
            for symbol in self.symbols_to_run:
                for freq in ['3min']:
                    try:
                        fp = config.KLINES_CACHE_DIR / f"{symbol}_{freq.replace('min', 'm')}.json"
                        if not fp.exists():
                            gap_summary[freq]['no_data'].append(symbol)
                            continue
                        
                        df = await self._read_file_unlocked(fp)
                        if df.empty:
                            gap_summary[freq]['no_data'].append(symbol)
                            continue
                        
                        # Check for gaps
                        if self._has_time_gaps(df, freq):
                            gap_summary[freq]['gaps_detected'].append(symbol)
                        else:
                            gap_summary[freq]['continuous'].append(symbol)
                            
                    except Exception as e:
                        logger.debug(f"Error checking {symbol} {freq}: {e}")
                        gap_summary[freq]['no_data'].append(symbol)
            
            # Log summary
            for freq in ['3min']:
                logger.info(f"📊 {freq} Data Summary:")
                logger.info(f"  ✅ Continuous: {len(gap_summary[freq]['continuous'])} symbols")
                logger.info(f"  🔍 Gaps Detected: {len(gap_summary[freq]['gaps_detected'])} symbols")
                logger.info(f"  ❌ No Data: {len(gap_summary[freq]['no_data'])} symbols")
                
                if gap_summary[freq]['gaps_detected']:
                    logger.warning(f"  🔍 Symbols with {freq} gaps: {', '.join(gap_summary[freq]['gaps_detected'][:10])}")
                    if len(gap_summary[freq]['gaps_detected']) > 10:
                        logger.warning(f"  ... and {len(gap_summary[freq]['gaps_detected']) - 10} more")
            
            logger.info("📊 === END GAP STATUS SUMMARY ===")
            
        except Exception as e:
            logger.error(f"Error generating gap status summary: {e}")


async def main():
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        shutdown_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    await ensure_redis_connections()

    try:
        cached_prices = await load_mark_prices_from_cache()
        if cached_prices:
            await broadcast_mark_prices_to_redis(cached_prices)
            logger.info(f"✅ Restored {len(cached_prices)} recent mark prices from cache to Redis")
        else:
            logger.info("ℹ️ No recent mark prices found in cache - starting fresh")
    except Exception as e:
        logger.warning(f"⚠️ Failed to restore mark prices from cache: {e}")

    try:
        test_prices = {"TEST_SYMBOL": 100.0}
        success = await broadcast_mark_prices_to_redis(test_prices)
        if success:
            logger.info("✅ Redis publishing test successful")
        else:
            logger.warning("⚠️ Redis publishing test failed")
    except Exception as e:
        logger.error(f"❌ Redis publishing test failed: {e}")

    continuous_task = None
    shutdown_task = None
    try:
        streamer = MarkPriceStreamer()
        try:
            websocket_task = await streamer.run_startup_sequence(shutdown_event)
        except Exception as startup_error:
            logger.error(f"❌ Startup sequence failed: {startup_error}")
            logger.exception("Full startup error details:")
            raise
        continuous_task = asyncio.create_task(streamer.run_continuous_operations(websocket_task))
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        done, pending = await asyncio.wait({continuous_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except KeyboardInterrupt:
        logger.info("🛑 Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"❌ Fatal error in main: {e}")
        logger.exception("Full error details:")
    finally:
        for task in (continuous_task, shutdown_task):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("✅ Cleanup completed")


if __name__ == "__main__":
    try:
        if M_READY_FLAG_FILE.exists(): M_READY_FLAG_FILE.unlink()
        asyncio.run(main())
    except KeyboardInterrupt: 
        print("\nMark price engine stopping.")
    finally:
        if M_READY_FLAG_FILE.exists():
            try: M_READY_FLAG_FILE.unlink()
            except Exception: pass
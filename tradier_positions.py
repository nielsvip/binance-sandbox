
import asyncio
import glob
import json
import logging
import math
import os
import pickle
import re
import shutil
import signal
import sys
import tempfile
import time
import traceback
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import aiofiles
import pandas as pd
import pytz
import redis.asyncio as redis
from dateutil.parser import isoparse


from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from utils import (
    clean_position_key,
    construct_position_key,
    get_simple_redis_manager,
    load_environment_from_gpg,
    orjson_default,
    parse_position_key,
    record_decision_context,
    safe_fetch_float,
    safe_parse_ts,
    tradier_action_logger,
)

load_environment_from_gpg(None)
config = TradierConfig()
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
            if not s: return {}
            if isinstance(s, str):
                if not s.strip(): return {}
                s = s.encode('utf-8')
            elif isinstance(s, (bytes, bytearray, memoryview)):
                if not s.strip(): return {}
            return orjson.loads(s)  # In the except block, use json.loads(s, **kwargs)
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
            if not s: return {}
            if isinstance(s, (bytes, bytearray, memoryview)):
                s = s.decode('utf-8', errors='replace')
            if not isinstance(s, str) or not s.strip(): return {}
            return json.loads(s, **kwargs)
    JSONDecodeError = json.JSONDecodeError
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("tradier_positions")
os.makedirs(config.LOG_DIR, exist_ok=True)

from logging.handlers import RotatingFileHandler

file_handler = RotatingFileHandler(config.LOG_FILE_TRADIER_POSITIONS, maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT, encoding='utf-8', mode='a')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)
logger.setLevel(logging.DEBUG)
_global_file_write_semaphore = asyncio.Semaphore(400)  # MacBook: 5 (prevent "too many open files"), Server/Gateway: 1000 (speed)

# -----------------------------------------------------------------------------
# 3. HELPER FUNCTIONS
# -----------------------------------------------------------------------------
def ensure_tz(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def safe_float(x, default=0.0):
    """Convert value to float, ensuring we never return None for Position creation"""
    try:
        if x is None:
            return default
        if isinstance(x, (int, float, Decimal)):
            return float(x)
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return default
            s = s.replace(",", "")
            return float(Decimal(s))
        if isinstance(x, dict):
            for k in ("amount", "qty", "positionAmt", "value"):
                if k in x:
                    return safe_float(x[k], default)
        return float(x)
    except (InvalidOperation, ValueError, TypeError) as e:
        try:
            logger.debug(f"[safe_float] failed to parse {repr(x)} -> {e}")
        except Exception:
            pass
        return default

_OPT_RE = re.compile(r'^[A-Z]{1,6}\d{6}[CP]\d{4,}$')
def is_option(symbol):
    return bool(_OPT_RE.match((symbol or "").upper()))

def calculate_gain(position_side, current_price, entry_price):
    if entry_price <= 0: return 0.0
    if position_side == 'LONG': 
        return ((current_price - entry_price) / entry_price) * 100
    else: 
        return ((entry_price - current_price) / entry_price) * 100

def _convert_timestamp_strings(data: Any) -> Any:
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
                except Exception:
                    result[key] = value
            elif isinstance(value, (dict, list)):
                result[key] = _convert_timestamp_strings(value)
            else:
                result[key] = value
        return result
    elif isinstance(data, list):
        return [_convert_timestamp_strings(item) for item in data]
    return data


async def atomic_write_json(file_path: Path, data: Dict):
    random_suffix = uuid.uuid4().hex
    temp_file = file_path.with_name(f".{file_path.name}.{random_suffix}.tmp")
    
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            json_bytes = json_dumps(data)
            if isinstance(json_bytes, str):
                json_bytes = json_bytes.encode('utf-8')
        except Exception:
            json_bytes = json.dumps(data, indent=2, default=str).encode('utf-8')
        with open(temp_file, 'wb') as f:
            f.write(json_bytes)
            f.flush()
            os.fsync(f.fileno())
        shutil.move(str(temp_file), str(file_path))
    except Exception as e:
        if temp_file.exists():
            try: temp_file.unlink()
            except Exception: pass
        raise e

async def load_json_safe(file_path: str | Path, account_key: str = None) -> dict:
    file_path = Path(file_path)
    async with _global_file_write_semaphore:
        content = b""
        try:
            if not file_path.exists():
                return {}
            async with aiofiles.open(file_path, "rb") as f:
                content = await f.read()
            if not content.strip():
                return {}
            data = safe_json_loads(content)
        except Exception:
            data = {}
            recovery_success = False
            try:
                txt = content.decode('utf-8', errors='ignore').strip()
                end_idx = txt.rfind('}')
                if end_idx != -1:
                    fixed_txt = txt[:end_idx+1]
                    data = json.loads(fixed_txt)
                    await atomic_write_json(file_path, data)
                    recovery_success = True
            except Exception:
                pass
            if not recovery_success:
                try:
                    if file_path.exists():
                        os.remove(file_path)
                except Exception:
                    pass
                return {}
        if not isinstance(data, dict):
            return {}
        data = _convert_timestamp_strings(data) 
        return data


def log_reduce_action(position_key, position_value_str, reduction_value_str, gain, reason, conviction=None, origin: str = "UNKNOWN", timestamp_15m=None, k_15m=None, k_15m_prv=None):
    safe = lambda x: f"{x:.2f}" if isinstance(x, (int, float)) and x is not None else "None"
    account_key = position_key.split(':')[0] if ':' in position_key else "unknown"
    conviction_str = f", Conviction: {conviction:.1f}" if conviction is not None else ""
    ts_str = f", ts_15m={timestamp_15m}" if timestamp_15m else ""
    k_str = f", k_15m={k_15m:.1f}" if k_15m is not None else ""
    k_prv_str = f", k_15m_prv={k_15m_prv:.1f}" if k_15m_prv is not None else ""
    tradier_action_logger.info(f"{position_key}: M Position was reduced. Position Value before: {position_value_str}, Reduce by: {reduction_value_str}, Gain: {safe(gain)}{conviction_str}{ts_str}{k_str}{k_prv_str} . Reason: {reason} | origin={origin}",
        extra={ 'ticker': position_key, 'action': 'REDUCE', 'position_value_str': position_value_str, 'details': f"Reduce by {reduction_value_str}", 'conviction': conviction, 'origin': origin, 'timestamp_15m': timestamp_15m, 'k_15m': k_15m, 'k_15m_prv': k_15m_prv } )
def log_augment_action(position_key, position_value_str, augment_value_str, gain, reason, conviction=None, origin: str = "UNKNOWN", timestamp_15m=None, k_15m=None, k_15m_prv=None):
    safe = lambda x: f"{x:.2f}" if isinstance(x, (int, float)) and x is not None else "None"
    account_key = position_key.split(':')[0] if ':' in position_key else "unknown"
    conviction_str = f", Conviction: {conviction:.1f}" if conviction is not None else ""
    ts_str = f", ts_15m={timestamp_15m}" if timestamp_15m else ""
    k_str = f", k_15={k_15m:.1f}" if k_15m is not None else ""
    k_prv_str = f", k_15m_prv={k_15m_prv:.1f}" if k_15m_prv is not None else ""
    tradier_action_logger.info( f"{position_key}: M Position was augmented. Position Value before: {position_value_str}, Augment by: {augment_value_str}, Gain: {safe(gain)}{conviction_str}{ts_str}{k_str}{k_prv_str} . Reason: {reason} | origin={origin}",
        extra={'ticker': position_key, 'action': 'AUGMENT', 'position_value_str': position_value_str, 'details': f"Augment by {augment_value_str}", 'conviction': conviction, 'origin': origin, 'timestamp_15m': timestamp_15m, 'k_15m': k_15m, 'k_15m_prv': k_15m_prv } )

def minutes_since(timestamp_obj, now=None):
    if not timestamp_obj: return 999999
    if now is None: now = datetime.now(timezone.utc)
    try:
        if isinstance(timestamp_obj, str): dt = isoparse(timestamp_obj)
        elif isinstance(timestamp_obj, datetime): dt = timestamp_obj
        else: return 999999
        if not isinstance(dt, datetime): return 999999
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() / 60.0
    except Exception: return 999999

def safe_datetime(ts, fallback: Optional[datetime] = None) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            pass
    if isinstance(ts, str):
        candidate = ts.strip()
        if candidate:
            numeric = None
            try:
                numeric = float(candidate[:-1] if candidate.endswith(('Z', 'z')) else candidate)
            except (TypeError, ValueError):
                numeric = None
            if numeric is not None and not math.isnan(numeric) and not math.isinf(numeric):
                try:
                    return datetime.fromtimestamp(numeric / (1000.0 if numeric > 1e12 else 1.0), tz=timezone.utc)
                except (OSError, OverflowError, ValueError):
                    numeric = None
            try:
                dt = pd.to_datetime(candidate, utc=True).to_pydatetime()
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.debug(f"[safe_datetime] Failed to parse timestamp string: {candidate} ({e})")
    if ts is not None:
        logger.debug(f"[safe_datetime] Unexpected timestamp type: {type(ts)} -> {ts}")
    return fallback

@dataclass
class TradierPosition:
    symbol: str
    position_side: str  # "LONG" or "SHORT"
    positionAmt: float
    entry_price: float
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    entry_time: str = ""
    last_update: str = ""
    gain: float = 0.0
    max_gain: float = 0.0
    prev_gain: float = 0.0
    # 2026-04-29 USER RULE: separate per-cycle peak gain for EXIT decisions only.
    # max_gain decays over time and is used by REENTRY logic — must NOT be used as exit anchor.
    # cycle_peak_gain tracks peak since last open, resets to 0 when positionAmt → 0, used by PEAK_GIVEBACK.
    cycle_peak_gain: float = 0.0
    max_quantity: float = 0.0
    last_augmentation_amount: float = 0.0
    last_augmentation_price: float = 0.0
    last_augmentation_time: Optional[datetime] = None
    last_reduction_amount: float = 0.0
    last_reduction_price: float = 0.0
    last_reduction_time: Optional[datetime] = None
    max_positionSize: float = 0.0
    opened_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    last_signal: str = ""
    was_reentered: bool = False
    was_reduced: bool = False
    prev_gain_last_updated: Optional[datetime] = None
    augment_reason: str = ""
    reduction_reason: str = ""
    mark_price_last_updated: Optional[datetime] = None    
    price_fetch_count: int = 0
    trade_wing: str = "swing"           # "swing" or "scalp"
    r1_stop_price: float = 0.0
    # positionAmt: float = field(init=False)
    # entry_price: float = field(init=False)
    # current_price: float = field(init=False)
    
    def __post_init__(self):
        self._convert_dates()
        if self.stop_loss is None: self.stop_loss = 0.0
        if self.take_profit is None: self.take_profit = 0.0
        # if not self.entry_time: self.entry_time = datetime.now(timezone.utc).isoformat()
        if not self.last_update: self.last_update = datetime.now(timezone.utc).isoformat()
        if self.max_positionSize == 0.0: self.max_positionSize = config.MAX_POSITION_SIZE
        if not self.last_updated: self.last_updated = datetime.now(timezone.utc)
        # if not self.opened_at: self.opened_at = datetime.now(timezone.utc)

    def _convert_dates(self):
        date_fields = [
            ('entry_time', 'opened_at'),
            ('last_update', 'last_updated'),
            ('last_augmentation_time', 'last_augmentation_time'),
            ('last_reduction_time', 'last_reduction_time'),
            ('prev_gain_last_updated', 'prev_gain_last_updated'),
            ('mark_price_last_updated', 'mark_price_last_updated')
        ]
        for str_field, dt_field in date_fields:
            value = getattr(self, str_field) if hasattr(self, str_field) else None
            if value and isinstance(value, str):
                try:
                    parsed = isoparse(value)
                    setattr(self, dt_field, parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
                except Exception:
                    setattr(self, dt_field, None)
            # elif value is None:
            #     if dt_field in ['opened_at', 'last_updated']:
            #         setattr(self, dt_field, datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with all fields present"""
        payload = asdict(self)
        keys_to_remove = ['side', 'current_price']
        for k in keys_to_remove:
            payload.pop(k, None)
        required_fields = {
            'stop_loss': 0.0, 'take_profit': 0.0, 'gain': 0.0, 'max_gain': 0.0, 'prev_gain': 0.0,
            'max_quantity': 0.0, 'last_augmentation_amount': 0.0, 'last_augmentation_price': 0.0,
            'last_reduction_amount': 0.0, 'last_reduction_price': 0.0, 
            'max_positionSize': config.MAX_POSITION_SIZE, 'last_signal': "", 
            'was_reentered': False, 'was_reduced': False, 'augment_reason': "", 
            'reduction_reason': "", 'price_fetch_count': 0
        }
        for field_name, default_value in required_fields.items():
            if field_name not in payload or payload[field_name] is None:
                payload[field_name] = default_value

        # Convert datetime objects to ISO format strings
        for key, value in list(payload.items()):
            if isinstance(value, datetime):
                payload[key] = value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            elif value is None:
                # Convert None to appropriate default
                if 'time' in key.lower() or 'date' in key.lower():
                    payload[key] = datetime.now(timezone.utc).isoformat()
                elif key in ['stop_loss', 'take_profit']:
                    payload[key] = 0.0
                elif 'amount' in key.lower() or 'price' in key.lower() or 'gain' in key.lower() or 'pnl' in key.lower():
                    payload[key] = 0.0
                elif key in ['last_signal', 'augment_reason', 'reduction_reason']:
                    payload[key] = ""
                elif key in ['was_reentered', 'was_reduced']:
                    payload[key] = False

        return payload

class RedisPositionManager:
    def __init__(self, config):
        self.config = config
        self.redis_client = None
        
    async def connect(self):
        try:
            self.redis_client = redis.Redis(
                host=getattr(self.config, 'REDIS_HOST', 'localhost'),
                port=getattr(self.config, 'REDIS_PORT', 6379),
                decode_responses=False, 
                socket_connect_timeout=5 )
            await self.redis_client.ping()
            return True
        except Exception:
            self.redis_client = None
            return False

    # --- ADDED WRAPPER METHODS HERE ---
    async def set(self, key, value):
        if self.redis_client:
            return await self.redis_client.set(key, value)
        return False

    async def get(self, key):
        if self.redis_client:
            return await self.redis_client.get(key)
        return None

    async def delete(self, key):
        if self.redis_client:
            return await self.redis_client.delete(key)
        return False

    async def publish(self, channel, message):
        if self.redis_client:
            return await self.redis_client.publish(channel, message)
        return 0
    # ----------------------------------

    async def close(self):
        if self.redis_client: await self.redis_client.aclose()
        
    async def save_positions(self, positions: dict, account_key: str) -> bool:
        """Save positions to a NAMESPACED Redis key (e.g. tradier:positions:trc)"""
        try:
            if not self.redis_client: return False 
            redis_key = f"tradier:positions:{account_key}"
            
            positions_dict = {
                k: (v.to_dict() if hasattr(v, 'to_dict') else v) 
                for k, v in positions.items()
                if k.startswith(f"{account_key}:")   }
            
            data_wrapper = {'timestamp': time.time(), 'account': account_key, 'positions': positions_dict}
            await self.redis_client.set(redis_key, pickle.dumps(data_wrapper))
            
            channel = self.config.REDIS_CHANNEL_POSITIONS
            ui_payload = {"LONG": {}, "SHORT": {}}
            for pk, pdata in positions_dict.items():
                _, symbol, side = parse_position_key(pk)
                if side in ui_payload: ui_payload[side][symbol] = pdata
            
            await self.redis_client.publish(channel, json.dumps({account_key: ui_payload}, default=str))
            
            return True
        except Exception as e:
            logger.error(f"[Redis] Save failed for {account_key}: {e}")
            return False




def _is_trading_hours() -> bool:
    """True only during regular US market hours: 9:30–16:00 ET, weekdays."""
    from datetime import time as _dt_time
    try:
        now_et = datetime.now(pytz.timezone("America/New_York"))
    except Exception:
        now_et = datetime.now(timezone.utc)
    if now_et.weekday() >= 5:
        return False
    return _dt_time(9, 30) <= now_et.time() <= _dt_time(16, 0)


class TradierPositionManager:
    def __init__(self, account_key: str):
        self.config = TradierConfig()
        if account_key not in self.config.ACCOUNTS:
            raise ValueError(f"Account '{account_key}' not defined in config.")      
        self.account_key = account_key
        acc_conf = self.config.get_account_config(self.account_key)
        self.account_dir = acc_conf['account_dir']
        self.long_file = self.account_dir / "long_positions.json"
        self.short_file = self.account_dir / "short_positions.json"
        self.state_file = self.account_dir / "state.json"
        self.data_dir = acc_conf['data_dir']
        self.api_key = acc_conf['api_key']
        self.base_url = acc_conf['api_url']
        self.is_sandbox = acc_conf['is_sandbox']
        self.api_client = TradierAPIClient(self.config, account_key=self.account_key)
        self.positions: Dict[str, TradierPosition] = {}
        self.positions_by_account = defaultdict(dict)       
        self.symbols: List[str] = []
        self.global_symbols_file = self.config.TRADIER_SYMBOLS_FILE
        self.load_all_symbols() 
        self.running = False
        self._positions_lock = asyncio.Lock()
        self.redis_manager = None
        self.order_queue = None
        self.position_manager = None
        self.symbols_long_trb = []
        self.symbols_short_trb = []
        self.symbols_long_trc = []
        self.symbols_short_trc = []
        
        # Internal Maps
        self.min_qty: Dict[str, float] = {}
        self.price_cache: Dict[str, Any] = {}
        self._price_cache = self.price_cache
        self._price_cache_time = {}
        self._loading_complete_event = asyncio.Event()
        
        # Tracking Dictionaries
        self.reenter_data: Dict[str, Any] = {}
        self.invalidation_levels: Dict[str, Any] = {}
        self.augmented_positions: Dict[str, datetime] = {}
        self.reduced_positions: Dict[str, datetime] = {}
        self.reversed_positions: Dict[str, datetime] = {}
        self.augmentation_cooldown_map: Dict[str, Dict[str, Any]] = {}
        self.reduction_cooldown_map: Dict[str, datetime] = {}
        self._position_update_timestamps: Dict[str, datetime] = {}
        self.zero_report_tracker: Dict[str, Dict[str, Any]] = {}
        self.ladder_levels: Dict[str, Dict[str, Any]] = {}
        self.orders_in_limbo: Dict[str, Dict[str, float]] = {}        
        self.position_reasons: Dict[str, Dict[str, Any]] = {}
        self._last_api_seen: Dict[str, Set[str]] = defaultdict(set)
        self.failed_price_counts: Dict[str, int] = defaultdict(int)
        self.positions_last_sync: Optional[datetime] = None
        self.positions_live = False
        self._positions_dirty = False
        self._last_full_save_time = time.time()
        self._full_save_interval = 3600.0
        self._last_price_update = time.time()
        self._price_update_interval = 5.0
        self._position_flush_event = asyncio.Event()
        for s in self.symbols:
            self.min_qty[s] = 1.0     

    def load_all_symbols(self):
        """Load symbols from global file AND account-specific files."""
        self.symbols = []
        if self.global_symbols_file.exists():
            try:
                with open(self.global_symbols_file, 'r') as f:
                    self.symbols.extend([s for s in json.load(f) ])
            except Exception: pass
        # for side in ['long', 'short']:
        #     f = self.config.BASE_PATH / f"symbols_{self.account_key}_{side}.json"
        #     if f.exists():
        #         try:
        #             with open(f, 'r') as fp:
        #                 content = json.load(fp)
        #                 if isinstance(content, list):
        #                     self.symbols.extend([s for s in content])
        #                 elif isinstance(content, dict): # Handle dict if necessary
        #                     self.symbols.extend(content.keys())
        #         except Exception as e: 
        #             logger.warning(f"[{self.account_key}] Failed to load symbols from {f.name}: {e}")
                
        self.symbols = list(set(self.symbols)) # Dedupe
        logger.info(f"[{self.account_key}] Loaded {len(self.symbols)} total monitored symbols")

    def get_ladder_file(self, position_side: str) -> Path:
        # /binance/tra/ladder_levels_long.json
        return self.account_dir / f"ladder_levels_{position_side.lower()}.json"

    def get_reenter_file(self, position_side: str) -> Path:
        # /binance/tra/reenter_data_long.json
        return self.account_dir / f"reenter_data_{position_side.lower()}.json"
        
    def get_invalidation_file(self) -> Path:
        # /binance/tra/invalidation_levels.json
        return self.account_dir / "invalidation_levels.json"


    def get_position(self, position_key: str):
            return self.positions.get(position_key)

    def _get_pricing_client(self):
        """Alias for api_client to satisfy update_all_prices_loop"""
        return self.api_client

    async def save_state_all(self):
        """Save runtime state (cooldowns, augmentations) split by account."""
        
        # 1. Bucket by Account
        acc_states = defaultdict(lambda: {
            "augmented_positions": {},
            "reduced_positions": {},
            "reversed_positions": {},
            "reduction_cooldown_map": {},
            "augmentation_cooldown_map": {},
            "position_reasons": {}
        })

        # Helper to route data to the right bucket
        def bucket_simple(source_dict, target_field, is_date=True):
            for key, val in source_dict.items():
                try:
                    acc = key.split(":")[0]
                    val_to_store = val.isoformat() if is_date and isinstance(val, datetime) else val
                    acc_states[acc][target_field][key] = val_to_store
                except Exception: pass

        bucket_simple(self.augmented_positions, "augmented_positions")
        bucket_simple(self.reduced_positions, "reduced_positions")
        bucket_simple(self.reversed_positions, "reversed_positions")
        bucket_simple(self.reduction_cooldown_map, "reduction_cooldown_map")
        bucket_simple(self.position_reasons, "position_reasons", is_date=False)

        # Complex handling for augmentation_cooldown_map
        for key, val in self.augmentation_cooldown_map.items():
            try:
                acc = key.split(":")[0]
                if isinstance(val, dict):
                    ts = val.get('time')
                    acc_states[acc]["augmentation_cooldown_map"][key] = {
                        'time': ts.isoformat() if isinstance(ts, datetime) else str(ts),
                        'usd': val.get('usd', 0.0)
                    }
            except Exception: pass

        # 2. Write Files
        for acc, state_data in acc_states.items():
            acc_dir = self.account_dir
            if acc_dir:
                await atomic_write_json(acc_dir / "state.json", state_data)

    async def load_state_all(self):
            """Load state from THIS account directory"""
            state_file = self.account_dir / "state.json"
            if not state_file.exists(): return
            
            try:
                with open(state_file, 'r') as f:
                    data = json.load(f)
                
                def parse(ts):
                    try: return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
                    except Exception: return None

                # Merge into global dicts
                for k, v in data.get("augmented_positions", {}).items():
                    if k.startswith(f"{self.account_key}:"):
                        dt = parse(v)
                        if dt: self.augmented_positions[k] = dt
                
                for k, v in data.get("reduced_positions", {}).items():
                    if k.startswith(f"{self.account_key}:"):
                        dt = parse(v)
                        if dt: self.reduced_positions[k] = dt
                
                for k, v in data.get("reduction_cooldown_map", {}).items():
                    if k.startswith(f"{self.account_key}:"):
                        dt = parse(v)
                        if dt: self.reduction_cooldown_map[k] = dt
                
                for k, v in data.get("position_reasons", {}).items():
                    if k.startswith(f"{self.account_key}:"):
                        self.position_reasons[k] = v
                
                for k, v in data.get("augmentation_cooldown_map", {}).items():
                    if k.startswith(f"{self.account_key}:") and isinstance(v, dict):
                        dt = parse(v.get('time'))
                        if dt:
                            self.augmentation_cooldown_map[k] = {'time': dt, 'usd': v.get('usd', 0.0)}
                            
                logger.info(f"[{self.account_key}] Loaded State")
            except Exception as e:
                logger.error(f"[{self.account_key}] Failed to load state.json: {e}")

    def _get_market_data_client(self):
        """Return the API client for this instance."""
        return self.api_client

    async def get_indicators_for_symbol(self, symbol: str, force_refresh: bool = False) -> Dict[str, Any]:
            symbol = symbol.strip().upper()
            if getattr(self, 'redis_manager', None):
                try:
                    redis_key = getattr(self.config, 'REDIS_KEY_MARKET_DATA', "tradier_indicators_latest")
                    raw_data = await self.redis_manager.get(redis_key)
                    
                    if raw_data:
                        # Safely handle if Redis returns bytes/strings instead of a dict
                        data = json.loads(raw_data) if isinstance(raw_data, (str, bytes, bytearray)) else raw_data
                        if isinstance(data, dict) and symbol in data:
                            return data[symbol]
                except Exception as e:
                    logger.debug(f"[{symbol}] Redis indicator fetch failed: {e}")

            # --- 2. DISK CACHE (Fallback) ---
            try:
                latest_file = self.config.DATA_DIR / "tradier_indicators_latest.json"
                if latest_file.exists():
                    async with aiofiles.open(latest_file, 'r') as f:
                        content = await f.read()
                        if content.strip():
                            data = json.loads(content)
                            if isinstance(data, dict) and symbol in data:
                                return data[symbol]
            except Exception as e:
                logger.debug(f"[{symbol}] Disk indicator fetch failed: {e}")

            return {}

    # Alias to catch any stray calls expecting 'get_indicators'
    async def get_indicators(self, symbol: str, use_cache: bool = True) -> Dict[str, Any]:
        return await self.get_indicators_for_symbol(symbol, force_refresh=not use_cache)

    async def fetch_current_prices(self, symbols: List[str]) -> Dict[str, float]:
        """
        Fetches prices for a list of symbols. 
        Hierarchy: Local Cache -> Disk JSON -> API (Emergency Only).
        """
        prices_out = {}
        now = datetime.now(timezone.utc)
        if not symbols: 
            return {}

        def extract_and_validate(entry):
            """Helper to check if a specific symbol entry is fresh (< 15s)"""
            if not isinstance(entry, dict): return None
            p = float(entry.get('price') or entry.get('last', 0))
            t_raw = entry.get('timestamp') or entry.get('date')
            
            if p <= 0 or not t_raw: return None
            
            try:
                if isinstance(t_raw, (int, float)):
                    val = t_raw / 1000.0 if t_raw > 1e11 else t_raw
                    t_obj = datetime.fromtimestamp(val, tz=timezone.utc)
                else:
                    t_obj = pd.to_datetime(t_raw)
                    if t_obj.tzinfo is None: t_obj = t_obj.replace(tzinfo=timezone.utc)
                
                age = (now - t_obj).total_seconds()
                if age < 15:
                    return p
            except Exception: pass
            return None

        # --- LAYER 1: DISK JSON (The New Format) ---
        try:
            latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
            if latest_file.exists() and (time.time() - latest_file.stat().st_mtime) < 20:
                async with aiofiles.open(latest_file, 'r') as f:
                    content = await f.read()
                    payload = json.loads(content)
                    
                    # Handle new format: {"_metadata": ..., "data": {...}}
                    symbol_data = payload.get("data", payload) 
                    
                    for s in symbols:
                        if s in symbol_data:
                            valid_p = extract_and_validate(symbol_data[s])
                            if valid_p:
                                prices_out[s] = valid_p
                                # Update internal cache for consistency
                                self._price_cache[s] = valid_p
                                self._price_cache_time[s] = now
            
            # If we found all prices on disk, return now
            if len(prices_out) == len(symbols):
                return prices_out
                
        except Exception as e:
            logger.debug(f"Disk read error in fetch_current_prices: {e}")

        # --- LAYER 2: API (EMERGENCY ONLY) ---
        # Only run this if we are missing symbols and NOT currently banned
        remaining_symbols = [s for s in symbols if s not in prices_out]
        client = self._get_market_data_client()
        
        if remaining_symbols and client and time.time() > getattr(client, '_global_ban_expires', 0):
            try:
                batch_size = 50
                for i in range(0, len(remaining_symbols), batch_size):
                    batch = remaining_symbols[i:i + batch_size]
                    quotes = await client.get_quotes(batch)
                    
                    for s, quote in quotes.items():
                        price = float(quote.get('last', 0))
                        if price > 0:
                            prices_out[s] = price
                            self._price_cache[s] = price
                            self._price_cache_time[s] = now
                    
                    await asyncio.sleep(0.2) # Avoid burst penalty
            except Exception as e:
                logger.error(f"API Emergency Fetch Error: {e}")

        return prices_out

    async def fetch_prices_from_external_cache(self) -> Dict[str, float]:
        prices = {}
        now = datetime.now(timezone.utc)
        MAX_AGE = 15.0

        # Helper to parse time safely
        def safe_age(ts_str):
            try:
                if not ts_str: return 999999
                dt = pd.to_datetime(ts_str)
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                return (now - dt).total_seconds()
            except Exception: return 999999

        # --- STEP 1: REDIS ---
        if self.redis_manager:
            try:
                # SimpleRedisManager.get already does json.loads()
                payload = await self.redis_manager.get("tradier_prices_latest")
                
                if isinstance(payload, dict):
                    updated_at = payload.get("_metadata", {}).get("updated_at")
                    age = safe_age(updated_at)
                    
                    if age < MAX_AGE:
                        symbol_data = payload.get("data", {})
                        for sym, info in symbol_data.items():
                            p = info.get('price') or info.get('last')
                            if p: prices[sym] = float(p)
                        return prices # RETURN REAL DATA
                    else:
                        logger.debug(f"Redis data is STALE ({age:.1f}s old)")
            except Exception as e:
                logger.debug(f"Redis fetch failed: {e}")

        # --- STEP 2: DISK ---
        try:
            json_path = self.config.DATA_DIR / "tradier_prices_latest.json"
            if json_path.exists():
                # Check file age first
                if (time.time() - json_path.stat().st_mtime) < MAX_AGE:
                    async with aiofiles.open(json_path, mode='r') as f:
                        payload = json.loads(await f.read())
                        symbol_data = payload.get("data", payload)
                        for sym, info in symbol_data.items():
                            if isinstance(info, dict):
                                p = info.get('price') or info.get('last')
                                if p: prices[sym] = float(p)
                    return prices
        except Exception: pass

        # CRITICAL: Always return a DICT, never None
        return prices


    # async def fetch_prices_from_external_cache(self) -> Dict[str, float]:
    #     prices = {}
        
    #     # 1. Try Redis First (Fastest)
    #     if self.redis_manager:
    #         try:
    #             # Use the manager's get method which handles multiple sources and json decoding
    #             data_dict = await self.redis_manager.get(self.config.REDIS_KEY_MARKET_DATA)
    #             if data_dict and isinstance(data_dict, dict):
    #                 for sym, info in data_dict.items():
    #                     if not isinstance(info, dict): continue
    #                     # Support various price keys
    #                     price = float(info.get('price', info.get('last', 0.0)))
    #                     if price > 0: 
    #                         prices[sym] = price
    #                 # logger.debug(f"Loaded {len(prices)} prices from Redis")
    #                 return prices
    #         except Exception: 
    #             pass

    #     # 2. Try JSON File (Persistence Layer)
    #     try:
    #         json_path = self.config.DATA_DIR / "tradier_prices_latest.json"
    #         if json_path.exists():
    #             mtime = json_path.stat().st_mtime
    #             age = time.time() - mtime
                
    #             # Only use if fresh (< 30 seconds)
    #             if age < 30:
    #                 async with aiofiles.open(json_path, mode='r') as f:
    #                     content = await f.read()
    #                     data = json.loads(content)
    #                     for sym, info in data.items():
    #                         price = float(info.get('price', info.get('last', 0.0)))
    #                         if price > 0: prices[sym] = price
    #                 # logger.debug(f"Loaded {len(prices)} prices from JSON (age={age:.1f}s)")
    #                 return prices
    #     except Exception: 
    #         pass
    #     return prices

    async def refresh_active_symbols(self):
        longs = set()
        shorts = set()
        
        # 2. Define Paths (BASE_PATH as requested)
        f_long = self.config.BASE_PATH / f"symbols_{self.account_key}_long.json"
        f_short = self.config.BASE_PATH / f"symbols_{self.account_key}_short.json"

        # 3. Load Existing Lists from Disk
        try:
            if f_long.exists():
                content = f_long.read_text().strip()
                if content: longs.update(json.loads(content))
            
            if f_short.exists():
                content = f_short.read_text().strip()
                if content: shorts.update(json.loads(content))
        except Exception as e:
            logger.error(f"[{self.account_key}] Error reading active symbol lists: {e}")

        # 4. Add Active Holdings from Memory
        async with self._positions_lock:
            for k, pos in self.positions.items():
                if abs(pos.positionAmt) > 0:
                    try:
                        # Ensure we only process this account's positions
                        if k.startswith(f"{self.account_key}:"):
                            if pos.position_side == "LONG":
                                longs.add(pos.symbol)
                            elif pos.position_side == "SHORT":
                                shorts.add(pos.symbol)
                    except Exception: pass

        # 5. Save Updates to Disk
        try:
            await atomic_write_json(f_long, list(longs))
            await atomic_write_json(f_short, list(shorts))
        except Exception as e:
            logger.error(f"[{self.account_key}] Error saving active symbol lists: {e}")

        # 6. Update Internal Monitor List
        all_monitored = longs.union(shorts)
        self.symbols = list(all_monitored)
        
        for s in self.symbols:
            if s not in self.min_qty: 
                self.min_qty[s] = 1.0
            
    async def update_all_prices_loop(self):
        last_symbol_refresh = 0
        SYMBOL_REFRESH_INTERVAL = 300
        while self.running:
            try:
                now_ts = time.time()
                if now_ts - last_symbol_refresh > SYMBOL_REFRESH_INTERVAL:
                    await self.refresh_active_symbols()
                    last_symbol_refresh = now_ts

                # 1. Fetch Prices (Dictionary of symbol -> price_float)
                # Note: This function calls fetch_current_prices which updates _price_cache_time internally
                prices = await self.fetch_prices_from_external_cache()
                # if not prices:
                #      # This populates _price_cache AND _price_cache_time
                #     prices = await self.fetch_current_prices(self.symbols)

                # 2. Fetch option quotes for positions missing from price cache
                option_symbols = [pos.symbol for pos in self.positions.values() if pos.symbol not in prices and abs(float(pos.positionAmt or 0)) > 0 and is_option(pos.symbol)]
                if option_symbols:
                    client = self._get_market_data_client()
                    if client and time.time() > getattr(client, '_global_ban_expires', 0):
                        try:
                            quotes = await client.get_quotes(option_symbols)
                            for sym, quote in quotes.items():
                                p = float(quote.get('last', 0))
                                if p > 0:
                                    prices[sym] = p
                                    self._price_cache[sym] = p
                                    self._price_cache_time[sym] = datetime.now(timezone.utc)
                        except Exception as e:
                            logger.debug(f"Option quote fetch failed: {e}")
                # 3. Update Memory Positions
                if prices:
                    async with self._positions_lock:
                        for pos in self.positions.values():
                            if pos.symbol in prices:
                                new_p = prices[pos.symbol]
                                price_ts = self._price_cache_time.get(pos.symbol)
                                is_opt = is_option(pos.symbol)
                                if new_p > 0:
                                    pos.mark_price = new_p
                                    if price_ts:
                                        pos.mark_price_last_updated = price_ts
                                    if pos.entry_price > 0:
                                        if is_opt:
                                            pos.gain = ((new_p * 100 - pos.entry_price) / pos.entry_price) * 100 if pos.position_side == "LONG" else ((pos.entry_price - new_p * 100) / pos.entry_price) * 100
                                            pos.unrealized_pnl = (new_p * 100 - pos.entry_price) * pos.positionAmt if pos.position_side == "LONG" else (pos.entry_price - new_p * 100) * pos.positionAmt
                                        else:
                                            pos.gain = calculate_gain(pos.position_side, new_p, pos.entry_price)
                                            diff = (new_p - pos.entry_price) if pos.position_side == "LONG" else (pos.entry_price - new_p)
                                            pos.unrealized_pnl = diff * pos.positionAmt
                                        pos.max_gain = max(pos.gain, pos.max_gain)
                await asyncio.sleep(4)
            except Exception as e:
                logger.error(f"Price Loop Error: {e}")
                await asyncio.sleep(5)

    # async def get_current_price(self, symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
    #     try:
    #         cached_price = self._price_cache.get(symbol)
    #         cache_time = self._price_cache_time.get(symbol)
    #         if cached_price and cache_time:
    #             if (datetime.now(timezone.utc) - cache_time).total_seconds() < 60:
    #                 return cached_price, cache_time
    #         if not self.api_client:
    #             return None, None
    #         quote = await self.api_client.get_quote(symbol)
    #         if quote:
    #             price = quote.get('last', 0)
    #             if price and price > 0:
    #                 price_float = float(price)
    #                 now = datetime.now(timezone.utc)
    #                 self._price_cache[symbol] = price_float
    #                 self._price_cache_time[symbol] = now
    #                 return price_float, now
    #         return None, None
    #     except Exception as e:
    #         logger.debug(f"Error getting current price for {symbol}: {e}")
    #         return None, None

    async def ensure_permanent_positions(self):
        """USER 2026-05-11: EVERY symbol in symbols_tradier.json (the master universe) has BOTH
        a _LONG and a _SHORT position object in memory for every account. positionAmt may be
        0 (flat) — that is still a valid object with all metadata fields. 'Orphan' is not a
        state we tolerate. After load: recover from backups first; for anything still missing,
        create a flat stub so the API-sync loop (every 5s) can update it. The per-side
        symbols_<acct>_long/short.json files are signal-discovery scratch lists — NOT used to
        decide whether the position object exists; the master universe decides that."""
        # Load the master universe (single source of truth: symbols_tradier.json)
        master_file = self.config.BASE_PATH / "symbols_tradier.json"
        universe = set()
        try:
            if master_file.exists():
                with open(master_file, 'r') as _mf:
                    universe = {str(s).upper() for s in (json.load(_mf) or []) if s}
        except Exception as _e:
            logger.error(f"[{self.account_key}] reading {master_file}: {_e}")
        if not universe:
            logger.error(f"[{self.account_key}] symbols_tradier.json is empty/missing — cannot ensure permanent positions")
            return
        missing = []
        for symbol in sorted(universe):
            for side in ("LONG", "SHORT"):
                pk = construct_position_key(self.account_key, symbol, side)
                if pk not in self.positions:
                    missing.append((pk, symbol, side))
        if not missing:
            return
        recovered = 0
        stubbed = 0
        for pk, symbol, side in missing:
            pos = await self._restore_position_from_anywhere(symbol, side)
            if pos is None:
                # User mandate: NEVER leave a (symbol, side) without an object. Create a flat stub.
                # The API-sync loop running every 5s will overwrite entry_price / positionAmt
                # when the broker actually shows the position.
                now = datetime.now(timezone.utc)
                pos = TradierPosition(
                    symbol=symbol,
                    position_side=side,
                    positionAmt=0.0,
                    entry_price=0.0,
                    mark_price=0.0,
                    opened_at=None,
                    last_updated=now,
                    last_update=now.isoformat(),
                    entry_time="",
                    augment_reason="FLAT_STUB_PERMANENT_OBJECT",
                )
                stubbed += 1
            else:
                recovered += 1
            self.positions[pk] = pos
            try:
                self.positions_by_account[self.account_key][pk] = pos
            except Exception:
                pass
        logger.info(f"[{self.account_key}] ensure_permanent_positions: universe={len(universe)} syms × 2 sides = {len(universe)*2} objects required, recovered={recovered} stubbed_flat={stubbed} ensured_this_pass={len(missing)}")
        self._mark_positions_dirty()
        try:
            await self.save_all_positions()
        except Exception as _se:
            logger.warning(f"[{self.account_key}] save_all_positions after ensure failed: {_se}")

    async def _restore_position_from_anywhere(self, symbol: str, position_side: str) -> Optional[TradierPosition]:
        """Search: own backups → own main file → ALL other accounts. Never returns None without exhausting everything."""
        side_str = position_side.lower()
        pk = construct_position_key(self.account_key, symbol, position_side)
        all_tradier_accounts = ["tra", "trb", "trc"]
        search_order = [self.account_key] + [a for a in all_tradier_accounts if a != self.account_key]
        for acct in search_order:
            acct_pk = f"{acct}:{symbol}_{position_side}"
            backup_dir = self.config.BASE_PATH / acct / "backups"
            if backup_dir.exists():
                patterns = [f"{side_str}_positions_backup_*.json", f"{side_str}_positions.json_backup_*.json"]
                backup_files = []
                for pat in patterns:
                    backup_files.extend(glob.glob(str(backup_dir / pat)))
                backup_files.sort(reverse=True)
                for bf in backup_files:
                    try:
                        with open(bf, "r") as f:
                            data = json.load(f)
                        if isinstance(data, dict) and "positions" in data:
                            data = data["positions"]
                        pos_data = data.get(acct_pk)
                        if pos_data and isinstance(pos_data, dict):
                            pos_data["symbol"] = symbol
                            pos_data["position_side"] = position_side
                            if acct != self.account_key:
                                pos_data["positionAmt"] = 0.0
                            restored = TradierPosition(**pos_data)
                            source = f"{acct} backup {os.path.basename(bf)}" if acct != self.account_key else f"own backup {os.path.basename(bf)}"
                            logger.critical(f"[RESTORE] {pk}: Recovered from {source}")
                            return restored
                    except Exception:
                        continue
            main_file = self.config.BASE_PATH / acct / f"{side_str}_positions.json"
            if main_file.exists():
                try:
                    with open(main_file) as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "positions" in data:
                        data = data["positions"]
                    pos_data = data.get(acct_pk)
                    if pos_data and isinstance(pos_data, dict):
                        pos_data["symbol"] = symbol
                        pos_data["position_side"] = position_side
                        if acct != self.account_key:
                            pos_data["positionAmt"] = 0.0
                        restored = TradierPosition(**pos_data)
                        logger.critical(f"[RESTORE] {pk}: Recovered from {acct} main file")
                        return restored
                except Exception:
                    pass
        # 2026-04-26: downgraded CRITICAL→DEBUG. The vast majority of "missing" positions are tradeable_keys
        # symbols that simply have never been opened (~510 per account = the full symbols_tradier.json). These
        # are not data loss — they're flat positions awaiting first entry. Real data-loss has different signature
        # (positionAmt > 0 in API but missing locally) and is caught by ORPHAN_EXCHANGE_POSITION elsewhere.
        logger.debug(f"[RESTORE] {pk} not in any backup — likely never-opened tradeable_key (expected). Will create flat record on first signal.")
        return None

    # ------------------------------------------------------------------
    # 2. LOADING (With Strict Isolation)
    # ------------------------------------------------------------------
    def load_positions(self, position_side: str) -> Dict[str, TradierPosition]:
        """Load from disk, purging foreign keys but keeping ALL local empty keys."""
        positions = {}
        pos_file = self.long_file if position_side == "LONG" else self.short_file
        
        if pos_file.exists():
            try:
                with open(pos_file, 'r') as f:
                    data = json.load(f)
                
                for key, pos_dict in data.items():
                    # 1. FILTER: Drop keys from other accounts (e.g. 'tra' keys in 'trc' manager)
                    if ":" in key:
                        parts = key.split(":")
                        if parts[0] != self.account_key:
                            continue # Silent drop

                    symbol = pos_dict.get('symbol', '').upper()
                    if not symbol and ":" in key:
                        parts = key.split(":")
                        if "_" in parts[1]: symbol = parts[1].split("_")[0]
                    
                    if not symbol: continue
                    clean_key = construct_position_key(self.account_key, symbol, position_side)
                    
                    self._ensure_all_fields_dict(pos_dict, symbol, position_side)
                    positions[clean_key] = TradierPosition(**pos_dict)
            except Exception as e:
                logger.error(f"[{self.account_key}] Error loading {pos_file}: {e}")
        return positions

    # async def load_account_positions_from_files(self):
    #     try:
    #         async with self._positions_lock:
    #             for side in ["LONG", "SHORT"]:
    #                 loaded = self.load_positions(side)
    #                 for k, v in loaded.items():
    #                     self.positions[k] = v
    #                     self.positions_by_account[self.account_key][k] = v
    #         await self.ensure_permanent_positions()
    #         self._loading_complete_event.set()
    #     except Exception as e:
    #         logger.error(f"[{self.account_key}] Load failed: {e}")

    async def save_all_positions(self):
        long_data = {}
        short_data = {}
        
        for key, pos in self.positions.items():
            # 1. Isolation Check
            if not key.startswith(f"{self.account_key}:"): continue

            # 2. Prepare Data
            if hasattr(pos, 'to_dict'): data = pos.to_dict()
            else: continue

            # 3. Bucket
            if pos.position_side == 'LONG': long_data[key] = data
            else: short_data[key] = data

        # 4. Sort (Highest Value first, then alphabetical)
        # Empty positions will naturally fall to the bottom, BUT THEY ARE INCLUDED.
        def get_sort_key(item):
            key, data = item
            amt = abs(float(data.get('positionAmt', 0)))
            price = float(data.get('mark_price', 0) or 0)
            return (- (amt * price), key)

        longs_sorted = dict(sorted(long_data.items(), key=get_sort_key))
        shorts_sorted = dict(sorted(short_data.items(), key=get_sort_key))

        try:
            # Atomic Writes
            for fpath, data in [(self.long_file, longs_sorted), (self.short_file, shorts_sorted)]:
                temp = fpath.with_suffix('.tmp')
                with open(temp, 'w') as f:
                    json.dump(data, f, indent=2, default=str)
                shutil.move(str(temp), str(fpath))
        except Exception as e:
            logger.error(f"[{self.account_key}] Save failed: {e}")

    def _ensure_all_fields_dict(self, pos_dict, symbol, position_side):
        """Helper to backfill missing fields in dict before init"""
        defaults = {
            'symbol': symbol, 'position_side': position_side,
            'positionAmt': 0.0, 'entry_price': 0.0, 'mark_price': 0.0,
            'unrealized_pnl': 0.0, 'realized_pnl': 0.0,
            'max_positionSize': self.config.MAX_POSITION_SIZE,
            'entry_time': datetime.now(timezone.utc).isoformat(),
            'last_update': datetime.now(timezone.utc).isoformat()
        }
        for k, v in defaults.items():
            if k not in pos_dict: pos_dict[k] = v

    def get_position_file(self, position_side: str) -> Path:
        return self.long_file if position_side == "LONG" else self.short_file

    def _mark_positions_dirty(self):
        self._positions_dirty = True
    
    async def _periodic_permanence_check(self):
        """DISABLED — positions are never fabricated. If missing, the load is broken."""
        return

    async def fetch_positions_from_api(self, account_key=None) -> Optional[List[Dict]]:
        """
        Fetch positions. Returns:
        - List[Dict]: Valid positions.
        - []: Empty account (valid).
        - None: API Error/Failure (Do not touch local state).
        """
        try:
            # Always use the singular client for this instance
            raw = await self.api_client.get_account_positions()
            
            # 1. Handle List Response (Standard)
            if isinstance(raw, list):
                valid_items = [x for x in raw if isinstance(x, dict)]
                if len(valid_items) < len(raw):
                    logger.warning(f"[{self.account_key}] Filtered {len(raw) - len(valid_items)} non-dict items from API response")
                return valid_items
            
            # 2. Handle Dictionary Response (Wrapped)
            elif isinstance(raw, dict):
                # Check for 'positions' -> 'position' wrapper
                if 'positions' in raw:
                    inner = raw['positions']
                    if inner == 'null' or inner is None: 
                        return []
                    if isinstance(inner, dict) and 'position' in inner:
                        pos = inner['position']
                        if isinstance(pos, list):
                            return [x for x in pos if isinstance(x, dict)]
                        if isinstance(pos, dict):
                            return [pos]
                    if isinstance(inner, list):
                        return [x for x in inner if isinstance(x, dict)]
                if 'symbol' in raw and 'quantity' in raw:
                    return [raw]
            if raw is None:
                logger.warning(f"[{self.account_key}] API returned None (Possible connectivity issue).")
                return None 
            logger.debug(f"[{self.account_key}] API returned non-iterable type: {type(raw)}")
            return None #
        except Exception as e:
            logger.error(f"[{self.account_key}] API Fetch Error: {e}")
            return None # CRITICAL: Return None on error so we don't wipe local state

    async def fetch_positions_loop(self):
        print(f"[{self.account_key}] Starting API sync loop...")
        while True:
            try:
                if not _is_trading_hours():
                    await asyncio.sleep(30)
                    continue
                await self.fetch_positions_from_api()
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[{self.account_key}] Sync Loop Error: {e}")
                await asyncio.sleep(15)

    async def broadcast_to_redis(self, positions: Dict[str, TradierPosition], should_log: bool = True):
        """Broadcast positions to Redis - STRICTLY ISOLATED KEY"""
        try:
            if not self.redis_manager:
                self.redis_manager = await get_simple_redis_manager()
            
            # 1. Flatten Data (Just keys and dicts)
            flat_payload = {}
            for k, v in positions.items():
                if hasattr(v, 'to_dict'):
                    flat_payload[k] = v.to_dict()
                else:
                    flat_payload[k] = v

            # 2. Save to Redis Key (Pickle)
            redis_key = f"tradier:positions:{self.account_key}"
            
            wrapper = {
                'timestamp': time.time(), 
                'account': self.account_key, 
                'positions': flat_payload  }
            await self.redis_manager.set(redis_key, pickle.dumps(wrapper))
            channel_payload = {self.account_key: flat_payload}
            channel = self.config.REDIS_CHANNEL_POSITIONS
            
            await self.redis_manager.publish(channel, json.dumps(channel_payload, default=str))
            
            if should_log:
                logger.debug(f"[{self.account_key}] Broadcasted positions to Redis Key: {redis_key}")
        except Exception as e:
            logger.debug(f"[{self.account_key}] Redis broadcast error: {e}")
    
    async def prune_old_backups(self, backup_folder: str, prefix: str):
        """Prune old backups"""
        backup_path = Path(backup_folder)
        if not backup_path.is_dir():
            return
        try:
            now = datetime.now(timezone.utc)
            backup_files_with_time = []
            for file_path in backup_path.iterdir():
                if file_path.is_file() and file_path.name.endswith(".json"):
                    backup_patterns = [f"{prefix}_backup_", f"{prefix}backup_", f"{prefix}_backup", f"*_backup_"]
                    if any(file_path.name.startswith(p.replace("*", "")) or (p.startswith("*") and "_backup_" in file_path.name) for p in backup_patterns):
                        try:
                            mtime = file_path.stat().st_mtime
                            file_time = datetime.fromtimestamp(mtime, timezone.utc)
                            age = now - file_time
                            backup_files_with_time.append((file_time, age, file_path))
                        except OSError:
                            continue
            if not backup_files_with_time:
                return
            backup_files_with_time.sort(key=lambda x: x[0], reverse=True)
            files_to_keep = set()
            files_to_delete = []
            last_3m = now - timedelta(minutes=3)
            last_15m = now - timedelta(minutes=15)
            last_12h = now - timedelta(hours=12)
            last_2d = now - timedelta(days=2)
            five_min_slots = {}
            hourly_slots = {}
            four_hourly_slots = {}
            daily_slots = {}
            for file_time, age, file_path in backup_files_with_time:
                if file_time >= last_3m:
                    files_to_keep.add(file_path)
                elif file_time >= last_15m:
                    five_min_key = file_time.replace(second=0, microsecond=0)
                    five_min_key = five_min_key.replace(minute=(five_min_key.minute // 5) * 5)
                    if five_min_key not in five_min_slots:
                        five_min_slots[five_min_key] = file_path
                        files_to_keep.add(file_path)
                    else:
                        files_to_delete.append(file_path)
                elif file_time >= last_12h:
                    hour_key = file_time.replace(minute=0, second=0, microsecond=0)
                    if hour_key not in hourly_slots:
                        hourly_slots[hour_key] = file_path
                        files_to_keep.add(file_path)
                    else:
                        files_to_delete.append(file_path)
                elif file_time >= last_2d:
                    four_hour_key = file_time.replace(minute=0, second=0, microsecond=0)
                    four_hour_key = four_hour_key.replace(hour=(four_hour_key.hour // 4) * 4)
                    if four_hour_key not in four_hourly_slots:
                        four_hourly_slots[four_hour_key] = file_path
                        files_to_keep.add(file_path)
                    else:
                        files_to_delete.append(file_path)
                else:
                    day_key = file_time.replace(hour=0, minute=0, second=0, microsecond=0)
                    if day_key not in daily_slots:
                        daily_slots[day_key] = file_path
                        files_to_keep.add(file_path)
                    else:
                        files_to_delete.append(file_path)
            for file_path in files_to_delete:
                try:
                    if not file_path.exists():
                        continue
                    if not os.access(file_path, os.W_OK):
                        logger.warning(f"[PRUNE] No write permission for {file_path.name}, skipping")
                        continue
                    file_path.unlink()
                    logger.debug(f"[PRUNE] Removed old backup: {file_path.name}")
                except FileNotFoundError:
                    pass
                except PermissionError as pe:
                    logger.warning(f"[PRUNE] Permission denied removing {file_path.name}: {pe}")
                except Exception as ex:
                    if file_path.exists():
                        logger.warning(f"[PRUNE] Could not remove {file_path.name}: {ex}")
        except Exception as e:
            logger.error(f"[PRUNE] Failed to prune backups: {e}")

    async def process_account_update(self, data: Optional[list] = None, single: bool = False, skip_broadcast_save: bool = False) -> Set[str]:            
        if not self._loading_complete_event.is_set():
            try: await asyncio.wait_for(self._loading_complete_event.wait(), timeout=5.0)
            except Exception: pass

        raw_positions_data = data
        if raw_positions_data is None:
            raw_positions_data = await self.fetch_positions_from_api(self.account_key)
        if raw_positions_data is None:
            logger.warning(f"[{self.account_key}] Update skipped: API failure.")
            return set()

        fresh_prices = await self.fetch_prices_from_external_cache()
        if fresh_prices is None:
            logger.error(f"[{self.account_key}] fetch_prices returned None! Fixing to empty dict.")
            fresh_prices = {}

        now = datetime.now(timezone.utc)
        updated_keys_in_api: Set[str] = set()
        skipped_count = 0
        account_positions = self.positions
        
        if isinstance(raw_positions_data, list):
            for pos_api_data in raw_positions_data:
                symbol = pos_api_data.get("symbol", "").upper()
                if not symbol: continue
                # Skip option OCC symbols — managed by options agent, not tradier_manage
                if len(symbol) > 10:
                    continue
                # 3. GET THE "HOT" PRICE FOR THIS SYMBOL
                # If Redis/Disk doesn't have it, we fall back to the last known mark_price
                current_market_price = fresh_prices.get(symbol)
                
                raw_qty = float(pos_api_data.get("quantity", 0) or 0)
                amt_abs = abs(raw_qty)
                position_side = "LONG" if raw_qty >= 0 else "SHORT"
                position_key = construct_position_key(self.account_key, symbol, position_side)
                updated_keys_in_api.add(position_key)
                
                # API Entry Data
                cost_basis = float(pos_api_data.get("cost_basis", 0) or 0)
                entry_price = abs(cost_basis / raw_qty) if raw_qty != 0 else 0.0

                existing_position = account_positions.get(position_key)
                if not existing_position:
                    # SHOULD NEVER HAPPEN. Every tradeable symbol has a permanent position object
                    # created at startup by ensure_permanent_positions. If we get here it means
                    # ensure_permanent_positions is broken or was never called — a critical system
                    # failure. Kill trading immediately; do NOT fabricate or skip.
                    logger.critical(f"💀 [MISSING_POSITION_KILL] {position_key}: permanent position object missing — ensure_permanent_positions failed. HALTING all trading for {self.account_key}.")
                    try:
                        self._trading_halted = True
                    except Exception:
                        pass
                    return set()
                # UPDATE THE MARK PRICE BEFORE HANDLING LOGIC
                if current_market_price:
                    existing_position.mark_price = current_market_price
                    existing_position.mark_price_last_updated = now
                current_price = existing_position.mark_price or entry_price
                prev_amt = float(existing_position.positionAmt)
                diff = amt_abs - prev_amt
                existing_position.entry_price = entry_price
                try:
                    if abs(diff) < 0.0001:
                        await self.handle_unchanged_position(existing_position, position_key, amt_abs, current_price)
                    elif diff > 0:
                        await self.handle_augmentation(existing_position, position_key, prev_amt, amt_abs, diff, current_price, entry_price)
                    elif diff < 0:
                        await self.handle_reduction(existing_position, position_key, prev_amt, amt_abs, abs(diff), current_price, entry_price, reduction_source="api_sync")
                except Exception as _upd_err:
                    logger.error(f"[POSITION_UPDATE_ERR] {position_key}: handle_* raised: {_upd_err}")
                finally:
                    # UNCONDITIONAL: API quantity is the ground truth for positionAmt — always.
                    existing_position.positionAmt = amt_abs
                    existing_position.last_updated = now

        # Handle Missing
        await self._handle_missing_positions(self.account_key, updated_keys_in_api, account_positions, now)
        
        # NOTE: I am removing _update_prices_for_positions from the end because 
        # we now do it at the START of the loop for every symbol.
        
        if updated_keys_in_api:
            self._mark_positions_dirty()
            if not skip_broadcast_save:
                await self.broadcast_to_redis(self.positions, should_log=False)
        return updated_keys_in_api
   


    # async def process_account_update(self, account_key: str, data: Optional[list] = None, single: bool = False, skip_broadcast_save: bool = False) -> Set[str]:            
    #     if not self._loading_complete_event.is_set():
    #         try: await asyncio.wait_for(self._loading_complete_event.wait(), timeout=5.0)
    #         except: pass

    #     # 1. Fetch
    #     raw_positions_data = data
    #     if raw_positions_data is None:
    #         raw_positions_data = await self.fetch_positions_from_api(should_log=False)

    #     now = datetime.now(timezone.utc)
    #     updated_keys_in_api: Set[str] = set()
        
    #     if account_key not in self.positions_by_account:
    #         self.positions_by_account[account_key] = {}
    #     account_positions = self.positions_by_account[account_key]

    #     if raw_positions_data:
    #         for pos_api_data in raw_positions_data:
    #             symbol = pos_api_data.get("symbol", "").upper()
    #             if not symbol: continue
                
    #             try: raw_qty = float(pos_api_data.get("positionAmt", 0) or 0)
    #             except: continue
                
    #             amt_abs = abs(raw_qty)
    #             if amt_abs == 0: continue
                
    #             position_side = "LONG" if raw_qty > 0 else "SHORT"
    #             position_key = construct_position_key(account_key, symbol, position_side)
    #             updated_keys_in_api.add(position_key)
                
    #             # API Data
    #             cost_basis = float(pos_api_data.get("cost_basis", 0) or 0)
    #             entry_price = abs(cost_basis / raw_qty) if raw_qty != 0 else 0.0
    #             unrealized_pnl = float(pos_api_data.get("unrealized_profit", 0) or 0)
    #             date_acquired = pos_api_data.get("date_acquired", now.isoformat())

    #             # Local Object
    #             existing_position = account_positions.get(position_key)
                
    #             if not existing_position:
    #                 # NEW POSITION: Create and Augment
    #                 new_pos = self._create_default_position(symbol, position_side)
    #                 account_positions[position_key] = new_pos
    #                 self.positions[position_key] = new_pos
                    
    #                 new_pos.entry_time = date_acquired
    #                 new_pos.unrealized_pnl = unrealized_pnl
                    
    #                 await self.handle_augmentation(
    #                     new_pos, position_key, 0.0, amt_abs, amt_abs, 
    #                     entry_price, entry_price
    #                 )
    #             else:
    #                 prev_amt = float(existing_position.positionAmt)
    #                 diff = amt_abs - prev_amt
    #                 current_price = existing_position.mark_price or entry_price
    #                 # Sync Metadata
    #                 existing_position.entry_price = entry_price
    #                 existing_position.unrealized_pnl = unrealized_pnl

    #                 if abs(diff) < 0.0001:
    #                     await self.handle_unchanged_position(existing_position, position_key, amt_abs, current_price)
    #                 elif diff > 0:
    #                     await self.handle_augmentation(existing_position, position_key, prev_amt, amt_abs, diff, current_price, entry_price)
    #                 elif diff < 0:
    #                     await self.handle_reduction(existing_position, position_key, prev_amt, amt_abs, abs(diff), current_price, entry_price, reduction_source="api_sync")
                    
    #                 existing_position.last_updated = now

    #     # 3. Handle Missing (Zero out)
    #     await self._handle_missing_positions(account_key, updated_keys_in_api, account_positions, now)

    #     # 4. Update Prices
    #     await self._update_prices_for_positions(account_key, updated_keys_in_api, account_positions, now)

    #     # 5. Broadcast & Save
    #     if updated_keys_in_api:
    #         self._mark_positions_dirty()
    #         if not skip_broadcast_save:
    #             grouped = {"LONG": {}, "SHORT": {}}
    #             for pk, pos in account_positions.items():
    #                 if pos.position_side == "LONG": grouped["LONG"][pos.symbol] = pos
    #                 else: grouped["SHORT"][pos.symbol] = pos
    #             await self.broadcast_to_redis(account_key, grouped, should_log=False)
    #     return updated_keys_in_api

    async def _handle_missing_positions(self, account_key: str, updated_keys_in_api: set, account_positions: dict, now: datetime):
        """Handle positions absent from Tradier API response.

        Two modes gated by config_tradier.GHOST_CLOSE_REQUIRE_CONFIRMATION (default False).

        Mode A (flag=False, current/legacy behavior, default):
          THRESHOLD=1, on first miss zero positionAmt directly. Preserved as default until
          sweep validation of the safer path. NOTE: this is the path that misfired 697 times
          in 30 days on NVDA/GOOGL/GLD per audit data/research_20260516/ghost_close_audit.md.

        Mode B (flag=True, new safer behavior):
          THRESHOLD = config_tradier.ZERO_CONFIRMATION_THRESHOLD_API (default 5).
          Per-(account, position_key) absence counter tracked across calls; only routes
          through handle_reduction() once count >= THRESHOLD. handle_reduction() preserves
          sacred fields (entry_price, max_gain, opened_at) and is the canonical reduce path.
          Caller in fetch_positions_from_api treats None from get_account_positions as
          API failure and skips this entirely, so counter only increments on confirmed
          broker absence.
        """
        if not hasattr(self, '_api_absence_count'):
            self._api_absence_count = {}
        _require_confirmation = bool(getattr(config, 'GHOST_CLOSE_REQUIRE_CONFIRMATION', False))
        if _require_confirmation:
            THRESHOLD = int(getattr(config, 'ZERO_CONFIRMATION_THRESHOLD_API', 5) or 5)
        else:
            THRESHOLD = 1
        for pk, pos in list(account_positions.items()):
            if not pk.startswith(f"{account_key}:"):
                continue
            amt = abs(float(getattr(pos, 'positionAmt', 0)))
            if amt < 0.0001:
                self._api_absence_count.pop(pk, None)
                continue
            if pk in updated_keys_in_api:
                self._api_absence_count.pop(pk, None)
                continue
            self._api_absence_count[pk] = self._api_absence_count.get(pk, 0) + 1
            count = self._api_absence_count[pk]
            if count < THRESHOLD:
                logger.warning(f"[GHOST_ABSENT] {pk}: absent {count}x from Tradier API (threshold={THRESHOLD}, mode={'CONFIRM' if _require_confirmation else 'LEGACY_T1'}) — holding state, not zeroing yet")
                continue
            _ghost_qty = float(getattr(pos, 'positionAmt', 0) or 0)
            _ghost_price = float(getattr(pos, 'mark_price', 0) or getattr(pos, 'entry_price', 0) or 0)
            _ghost_entry = float(getattr(pos, 'entry_price', 0) or 0)
            _ghost_gain = float(getattr(pos, 'gain', 0) or 0)
            if _require_confirmation:
                logger.warning(f"[GHOST_CLOSE_CONFIRMED] {pk}: absent {count}x ≥ THRESHOLD={THRESHOLD} — routing through handle_reduction() (full-close, preserves sacred fields)")
                _reason = f"tradier_api_absence_{count}x_{THRESHOLD}_lastgain={_ghost_gain:.2f}%_entry={_ghost_entry:.2f}"
                self._api_absence_count.pop(pk, None)
                try:
                    await self.handle_reduction(pos, pk, _ghost_qty, 0.0, abs(_ghost_qty), _ghost_price, _ghost_entry, reduction_source="api_absence_confirmed", reason=_reason)
                except Exception as _red_err:
                    logger.error(f"[GHOST_CLOSE_CONFIRMED] {pk}: handle_reduction raised: {_red_err} — falling back to direct zero")
                    pos.positionAmt = 0.0
                    pos.gain = 0.0
                    pos.unrealized_pnl = 0.0
                    pos.last_updated = now
                    try: pos.cycle_peak_gain = 0.0
                    except Exception: pass
                    try:
                        await self.append_to_position_history_file(pk, "GHOST_CLOSE", abs(_ghost_qty), _ghost_price, rich_context={"reason": _reason + "_fallback", "indicators": {}})
                    except Exception as _hist_err:
                        logger.error(f"[GHOST_CLOSE_CONFIRMED] {pk}: history write failed: {_hist_err}")
            else:
                logger.warning(f"[GHOST_ZERO] {pk}: absent {count}x from Tradier API — zeroing positionAmt (Tradier returns ALL held positions, absence = closed)")
                pos.positionAmt = 0.0
                pos.gain = 0.0
                pos.unrealized_pnl = 0.0
                pos.last_updated = now
                # 2026-04-29 USER ABSOLUTE: do NOT touch max_gain (it has its own decay function and is
                # used for REENTRY logic, not exit). Reset only `cycle_peak_gain` — a separate per-cycle
                # peak tracker that PEAK_GIVEBACK exit uses. max_gain stays for reentry semantics.
                try: pos.cycle_peak_gain = 0.0
                except Exception: pass
                self._api_absence_count.pop(pk, None)
                try:
                    await self.append_to_position_history_file(pk, "GHOST_CLOSE", abs(_ghost_qty), _ghost_price, rich_context={"reason": f"tradier_api_absence_{count}x_lastgain={_ghost_gain:.2f}%_entry={_ghost_entry:.2f}", "indicators": {}})
                except Exception as _hist_err:
                    logger.error(f"[GHOST_ZERO] {pk}: history write failed: {_hist_err}")

    async def _update_prices_for_positions(self, account_key: str, updated_keys_in_api: set, account_positions: dict, now: datetime):
        if not updated_keys_in_api: return
        prices = await self.fetch_prices_from_external_cache()
        async with self._positions_lock:
            for position_key in updated_keys_in_api:
                position = account_positions.get(position_key)
                if position and isinstance(position, TradierPosition):
                    price = prices.get(position.symbol)
                    if price and price > 0:
                        position.mark_price = price
                        position.mark_price_last_updated = now

    # async def _update_prices_for_positions(self, account_key: str, updated_keys_in_api: set,  account_positions: dict, now: datetime):
    #     if not updated_keys_in_api: return
    #     symbols_to_update = set()
    #     for position_key in updated_keys_in_api:
    #         position = account_positions.get(position_key)
    #         if position and isinstance(position, TradierPosition):
    #             symbols_to_update.add(position.symbol)
        
    #     if symbols_to_update:
    #         # CALL UNIFIED FETCHER (Uses 'tra' client)
    #         # This updates the global cache automatically
    #         await self.fetch_current_prices(list(symbols_to_update))
            
    #         # Apply from Cache
    #         async with self._positions_lock:
    #             for position_key in updated_keys_in_api:
    #                 position = account_positions.get(position_key)
    #                 if position and isinstance(position, TradierPosition):
    #                     symbol = position.symbol
    #                     # Read from global cache which was just updated
    #                     price = self._price_cache.get(symbol)
                        
    #                     if price and price > 0:
    #                         position.mark_price = price
    #                         position.mark_price_last_updated = now
                            
    #                         # Update gain
    #                         if position.entry_price > 0:
    #                             position.prev_gain = position.gain
    #                             if position.position_side == "LONG":
    #                                 position.gain = ((price - position.entry_price) / position.entry_price) * 100.0
    #                                 position.unrealized_pnl = (price - position.entry_price) * position.positionAmt
    #                             else:
    #                                 position.gain = ((position.entry_price - price) / position.entry_price) * 100.0
    #                                 position.unrealized_pnl = (position.entry_price - price) * position.positionAmt
    #                             position.max_gain = max(position.gain, position.max_gain)

    async def append_to_position_history_file(self, position_key: str, trade_type: str, qty: float, price: float, rich_context: dict = None):
        try:
            account_key, symbol, side = parse_position_key(position_key)
            symbol_side = f"{symbol}_{side}"
            hist_dir = Path(self.config.DATA_DIR) / "history" / account_key
            hist_dir.mkdir(parents=True, exist_ok=True)
            filename = hist_dir / f"{symbol_side}.jsonl"
            MAX_SIZE_BYTES = 200 * 1024
            if filename.exists() and filename.stat().st_size > MAX_SIZE_BYTES:
                backup = filename.with_name(f"{filename.name}.bak")
                if backup.exists(): backup.unlink()
                filename.rename(backup)
            reason = (rich_context or {}).get('reason') or (rich_context or {}).get('reason_text') or "Manual/System Detection"
            snapshot = (rich_context or {}).get('snapshot') or (rich_context or {}).get('indicators', {})
            entry = { "ts": datetime.now(timezone.utc).isoformat(), "type": trade_type, "qty": round(float(qty), 6), "price": round(float(price), 8), "value": round(float(qty * price), 2), "reason": reason, "indicators": snapshot }
            async with aiofiles.open(filename, "a") as f:
                await f.write(json.dumps(entry, default=str) + "\n")
            if rich_context and self.redis_manager:
                await record_decision_context(self.redis_manager, account_key, position_key, trade_type, reason, snapshot, rich_context.get('tech_score', 0), rich_context.get('market_context', {}))
                redis_key = f"decision:{position_key}"
                await self.redis_manager.delete(redis_key)
        except Exception as e:
            logger.error(f"[HISTORY] Write error {position_key}: {e}")
                
                            
    async def handle_unchanged_position(self, position, position_key: str, amt_abs:float, current_price: float):
        now = datetime.now(timezone.utc)
        account_key, _, _ = parse_position_key(position_key)
        async with self._positions_lock:
            # Update mark price if we have a fresh one - use timestamp from price source
            if current_price and current_price > 0:
                position.mark_price = current_price
                price_ts = None
                try:
                    if hasattr(self, 'get_current_price'):
                        _, price_ts = await self.get_current_price(position.symbol)
                except Exception:
                    pass
                position.mark_price_last_updated = ensure_tz(price_ts) if price_ts else now
            current_price = position.mark_price or current_price
            position_amt = amt_abs
            # CRITICAL: positionAmt is ONLY changed by handle_augmentation/handle_reduction - NEVER modify directly!
            # position.positionAmt=amt_abs  # DISABLED - use handle_augmentation/handle_reduction instead
            if position_amt == 0.0:
                position.prev_gain=position.gain
                position.gain = 0.0
                if position.realized_pnl != 0.0:
                    position.realized_pnl=position.unrealized_pnl
                    position.unrealized_pnl=0.0
                    position.positionAmt=float(abs(position_amt))
            else:
                base_entry = position.entry_price if position.entry_price not in (None, 0.0) else current_price
                if base_entry <= 0 or base_entry < current_price * 0.01 or base_entry > current_price * 100:
                    position.prev_gain=position.gain
                    position.gain = 0.0
                    position.positionAmt=float(abs(position_amt))
                else:
                    position.prev_gain=position.gain
                    if is_option(position.symbol):
                        position.gain = ((current_price * 100 - base_entry) / base_entry) * 100 if position.position_side == "LONG" else ((base_entry - current_price * 100) / base_entry) * 100
                    else:
                        position.gain = calculate_gain(position.position_side, current_price, base_entry or current_price)
                position.max_gain = max(position.gain, position.max_gain)
            position.last_updated = now  # CRITICAL: Update timestamp so position doesn't appear stale
        positionAmt = safe_float(position.positionAmt)
        if positionAmt:
            if (position.position_side or "").upper() == "LONG":
                position.unrealized_pnl = (current_price - base_entry) * positionAmt
            else:
                position.unrealized_pnl = (base_entry - current_price) * positionAmt
        else:
            position.unrealized_pnl = None
        self.positions[position_key] = position
        try:
            account_key, _, _ = parse_position_key(position_key)
            self.positions_by_account.setdefault(account_key, {})[position_key] = position
        except Exception:
            pass
        self._mark_positions_dirty()
        await self.save_state_all()

    async def handle_augmentation(self, position, position_key, positionAmt, position_amt_abs, augment_qty, current_price, entry_price):
        now = datetime.now(timezone.utc)
        if current_price and current_price > 0:
            position.mark_price = current_price
            price_ts = None
            try:
                if hasattr(self, 'get_current_price'):
                    _, price_ts = await self.get_current_price(position.symbol)
            except Exception:
                pass
            position.mark_price_last_updated = ensure_tz(price_ts) if price_ts else now
        account_key, symbol, position_side = parse_position_key(position_key)
        min_qty = self.min_qty.get(symbol, 0.0001)*1.2
        was_tiny_position = positionAmt * current_price <= max(config.MIN_POSITION_SIZE * 2, min_qty * current_price)
        position_value = positionAmt * current_price
        position_value_str = f"{position_value:.2f}"
        if position.last_signal in ['PROFIT_TAKE','REDUCE','CLOSE']:
            position.last_signal = 'AUGMENT'
        augment_value = augment_qty * current_price
        if augment_value >= config.START_POSITION_SIZE:
            position.last_augmentation_amount = augment_qty
            position.last_augmentation_price = current_price
            position.last_augmentation_time = now
            # Remove from limbo and set actual cooldown (order is now confirmed)
            side = "BUY" if position_side == "LONG" else "SELL"
            if position_key in self.orders_in_limbo and side in self.orders_in_limbo[position_key]:
                del self.orders_in_limbo[position_key][side]
                if not self.orders_in_limbo[position_key]:
                    del self.orders_in_limbo[position_key]
            self.augmentation_cooldown_map[position_key] = {'time': now, 'usd': augment_value}
            if hasattr(self, 'order_queue') and self.order_queue:
                self.order_queue.last_executed_time[(position_key, side)] = time.time()
            logger.debug(f"[{position_key}] Order confirmed - removed from limbo, cooldown set: {augment_qty:.6f} (${augment_value:.2f})")
        else:
            logger.debug(f"[{position_key}] Augmentation ${augment_value:.2f} below START_POSITION_SIZE - cooldown not applied")
        reentry_dirty = False
        reentry_deleted = False
        reenter_data_pos = self.reenter_data.get(position_key)
        if isinstance(reenter_data_pos, dict):
            try:
                current_reenter_amount = float(reenter_data_pos.get("reenter_amount", 0.0) or 0.0)
            except (TypeError, ValueError):
                current_reenter_amount = 0.0
            if current_reenter_amount > 0:
                remaining_amount = max(0.0, current_reenter_amount - augment_qty)
                threshold_qty = max(min_qty, config.MIN_POSITION_SIZE / current_price if current_price else 0.0)
                if remaining_amount <= threshold_qty:
                    reentry_deleted = True
                    self.reenter_data.pop(position_key, None)
                    logger.debug(f"[{position_key}] Reenter plan fulfilled - removing entry")
                else:
                    reenter_data_pos["reenter_amount"] = remaining_amount
                    reenter_data_pos["timestamp"] = now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    reentry_dirty = True
            else:
                reentry_deleted = True
                self.reenter_data.pop(position_key, None)
        elif reenter_data_pos is not None:
            reentry_deleted = True
            self.reenter_data.pop(position_key, None)
        if reentry_dirty or reentry_deleted:
            try:
                await self.save_reenter_data(position_key)
            except Exception as re_save_err:
                logger.warning(f"[{position_key}] Failed to persist reenter update: {re_save_err}")
        if was_tiny_position: #position.positionAmt * current_price < max(2 * config.MIN_POSITION_SIZE, min_qty * current_price):
            if position.last_signal == 'AUGMENT':
                position.last_signal = 'OPEN'
            position.opened_at = now
        else:
            try:
                indicators_now = await self.get_indicators_for_symbol(position.symbol, force_refresh=True)
                if indicators_now:
                    if position.position_side == 'LONG':
                        invalidation_level = float(indicators_now.get('dc_low_3m', 0) or 0.0)
                    else: # SHORT
                        invalidation_level = float(indicators_now.get('dc_high_3m', 0) or 0.0)
                    if invalidation_level:
                        self.invalidation_levels[position_key] = { 'level': invalidation_level,'timestamp': now,'position_side': position.position_side }
                        #logger.info(f"[{position_key}] Augmentation successful. New invalidation price set to: {invalidation_level:.4f}")
            except Exception as e:
                logger.error(f"[{position_key}] Could not set invalidation price during augmentation: {e}")
        position.positionAmt = float(position_amt_abs)
        if augment_qty > 0 and position.positionAmt > 0:
            old_positionAmt = position.positionAmt - augment_qty
            if old_positionAmt > 0:
                # Calculate weighted average entry price
                old_entry_price = position.entry_price
                old_value = old_positionAmt * old_entry_price
                new_value = augment_qty * current_price
                total_positionAmt = position.positionAmt
                position.entry_price = (old_value + new_value) / total_positionAmt
            else:
                position.entry_price = current_price
        base_entry_for_gain = position.entry_price if position.entry_price not in (None, 0.0) else entry_price
        if base_entry_for_gain <= 0 or base_entry_for_gain < current_price * 0.01 or base_entry_for_gain > current_price * 100:
            position.prev_gain=position.gain
            position.gain = 0.0
        else:
            position.prev_gain=position.gain
            position.gain = calculate_gain(position.position_side, current_price, base_entry_for_gain)
        if position.positionAmt > 0:
            position.max_gain = max(position.gain, position.max_gain)
        deteriorated_max_qty = self.deteriorate_max_quantity(account_key, position, current_price)
        if position.positionAmt > deteriorated_max_qty:
            position.max_quantity = position.positionAmt
        else:
            position.max_quantity = deteriorated_max_qty
        position.max_positionSize = config.MAX_POSITION_SIZE
        position.was_reduced = False 
        self.augmented_positions[position_key] = now

        reversed_side = "SHORT" if position_side == "LONG" else "LONG" 
        reversed_key = construct_position_key(account_key, symbol, reversed_side)
        if reversed_key in self.reversed_positions:
            self.unmark_reversed(reversed_key)
        if current_price > 0 and position_amt_abs > config.START_POSITION_SIZE / current_price:
            self.mark_augmented(position_key, timestamp=now)
       
        await self.save_invalidation_levels(account_key)
        augment_value_str = f"{augment_qty * current_price:.2f}"
        conviction = None
        if position.augment_reason:
            conv_match = re.search(r'conv(-?[\d\.]+)', position.augment_reason)
            if conv_match:
                try:
                    conviction = float(conv_match.group(1))
                    # if 'conv' in position.augment_reason and conviction > 0:
                    #     conviction = -conviction
                except ValueError:
                    conviction = None
        # Track augmentation reason in the dict
        rich_context = await self.fetch_decision_context(position_key)
        if position_key not in self.position_reasons:
            self.position_reasons[position_key] = {}
        self.position_reasons[position_key]['last_augment_reason'] = position.augment_reason
        self.position_reasons[position_key]['last_augment_timestamp'] = now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        self.position_reasons[position_key]['last_augment_amount'] = augment_qty
        self.position_reasons[position_key]['last_augment_price'] = current_price
        if rich_context:
            self.position_reasons[position_key]['last_augment_full_context'] = rich_context
            if 'history' not in self.position_reasons[position_key]:
                self.position_reasons[position_key]['history'] = []
            
            self.position_reasons[position_key]['history'].append({
                'type': 'AUGMENT',
                'timestamp': now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'amount': augment_qty,
                'price': current_price,
                'context': rich_context   })
            await self.append_to_position_history_file(position_key, "AUGMENT", augment_qty, current_price, rich_context)
            
        try: indicators_log = indicators_now if indicators_now else None
        except NameError: indicators_log = None
        if not indicators_log and hasattr(self, 'get_indicators_for_symbol'): indicators_log = await self.get_indicators_for_symbol(symbol, force_refresh=False)
        timestamp_15m = indicators_log.get('timestamp_15m') if indicators_log else None
        k_15m = indicators_log.get('stoch_k_15m') if indicators_log else None
        k_15m_prv = indicators_log.get('stoch_k_15m_prev') if indicators_log else None
        log_augment_action( position_key=position_key, position_value_str=position_value_str,augment_value_str=augment_value_str, gain=position.gain, reason=position.augment_reason, conviction=conviction, origin="CONFIRMED", timestamp_15m=timestamp_15m, k_15m=k_15m, k_15m_prv=k_15m_prv )        
        # if self.stop_manager:
        #     asyncio.create_task(self.stop_manager.manage(account_key=account_key, symbol=symbol, position_key=position_key, position_side=position_side, position=position, current_price=current_price, entry_price=entry_price, event="augmentation", force_sync=True))
        #     logger.info(f"[HANDLE_AUGMENTATION] {position_key}: Triggered stop level sync after augmentation (final_pos={position_amt_abs:.6f})")
        position.last_updated = now
        account_positions = self.positions_by_account.setdefault(account_key, {})
        account_positions[position_key] = position
        self.positions[position_key] = position
        logger.info(f"[HANDLE_AUGMENTATION] {position_key}: Added to dict (total positions: {len(self.positions)}, account positions: {len(account_positions)})")
        self._mark_positions_dirty()
        await self.save_state_all()

    async def handle_reduction(self, position, position_key,  positionAmt, position_amt_abs, reduce_qty, current_price, entry_price, reduction_source="system", reason=None):
        now = datetime.now(timezone.utc) 
        if current_price and current_price > 0:
            position.mark_price = current_price
            price_ts = None
            try:
                if hasattr(self, 'get_current_price'):
                    _, price_ts = await self.get_current_price(position.symbol)
            except Exception:
                pass
            position.mark_price_last_updated = ensure_tz(price_ts) if price_ts else now
        position_key = clean_position_key(str(position_key))
        account_key, symbol, position_side = parse_position_key(position_key)
        if not current_price or current_price <= 0:
            if entry_price and entry_price > 0: current_price = entry_price
            elif position and hasattr(position, 'mark_price') and position.mark_price and position.mark_price > 0: current_price = position.mark_price
            elif position and hasattr(position, 'entry_price') and position.entry_price and position.entry_price > 0: current_price = position.entry_price
            elif position and hasattr(position, 'last_reduction_price') and position.last_reduction_price and position.last_reduction_price > 0: current_price = position.last_reduction_price
            elif position and hasattr(position, 'last_augmentation_price') and position.last_augmentation_price and position.last_augmentation_price > 0: current_price = position.last_augmentation_price
        if not current_price or current_price <= 0: logger.error(f"[handle_reduction][{position_key}] CRITICAL: No valid price! Using fallback."); current_price = position.mark_price if position and hasattr(position, 'mark_price') and position.mark_price else (position.entry_price if position and hasattr(position, 'entry_price') and position.entry_price else 1.0)
        min_qty = self.min_qty.get(symbol, 0.0001)*1.2
        is_tiny_reduction = reduce_qty * current_price <= max( 4 * config.MIN_POSITION_SIZE * 2, min_qty * current_price)
        was_tiny_position = positionAmt * current_price <= max(4 * config.MIN_POSITION_SIZE * 2, min_qty * current_price)
        if position is not None and reduce_qty > 0 and reduction_source != "api_absence_confirmed":
            position.last_reduction_amount = reduce_qty
            position.last_reduction_price = current_price
            position.last_reduction_time = now 
            if not is_tiny_reduction and not was_tiny_position:
                position.was_reduced = True
                # Remove from limbo and set actual cooldown (order is now confirmed)
                side = "SELL" if position_side == "LONG" else "BUY"
                if position_key in self.orders_in_limbo and side in self.orders_in_limbo[position_key]:
                    del self.orders_in_limbo[position_key][side]
                    if not self.orders_in_limbo[position_key]:
                        del self.orders_in_limbo[position_key]
                self.reduction_cooldown_map[position_key] = now
                if hasattr(self, 'order_queue') and self.order_queue:
                    self.order_queue.last_executed_time[(position_key, side)] = time.time()
                logger.debug(f"[{position_key}] Order confirmed - removed from limbo, cooldown set: {reduce_qty:.6f}")
        if position is not None and reduce_qty > 0 and reduction_source != "api_absence_confirmed":
            await self.create_ladder_levels_for_reentry(position_key, account_key, position, current_price, now)
        is_tiny_position = positionAmt - reduce_qty < max(config.MIN_POSITION_SIZE * 3, min_qty * current_price)
        if position.entry_price > 0 and reduce_qty > 0 and reduction_source != "api_absence_confirmed":
            gain_at_reduction = position.gain
            reduction_ratio = reduce_qty / positionAmt if positionAmt > 0 else 1.0
            realized_gain = gain_at_reduction * reduction_ratio
            position.realized_pnl += realized_gain
            logger.debug(f"[{position_key}] PnL Update: Realized {gain_at_reduction*100:.2f}% (weighted: {realized_gain*100:.2f}%) from this reduction. "
                        f"New cumulative Realized PnL: {position.realized_pnl*100:.2f}%")
        if is_tiny_position:
            position.entry_price = current_price
            position.prev_gain=position.gain
            position.gain = 0.0  
            position.realized_pnl=position.unrealized_pnl
            position.unrealized_pnl=0.0
        position.positionAmt = position_amt_abs
        min_qty_value = self.min_qty.get(symbol, 0.0001)
        calc_min_size = (3 * config.MIN_POSITION_SIZE / current_price) if current_price > 0 else 999999.0
        pos_min_qty = max(calc_min_size, min_qty_value)


        logger.debug(f"[DEBUG] handle_reduction updated position.positionAmt to {position.positionAmt}")
        deteriorated_max_qty = self.deteriorate_max_quantity(account_key,position, current_price)
        if positionAmt > deteriorated_max_qty:
            position.max_quantity = positionAmt
        else:
            position.max_quantity = deteriorated_max_qty
        reversed_side = "SHORT" if position_side == "LONG" else "LONG" 
        reversed_key = construct_position_key(account_key, symbol, reversed_side)
        if reversed_key in self.reversed_positions:
            self.unmark_reversed(reversed_key)
        # Reset entry only if remaining position is tiny (or zero handled elsewhere)
        if was_tiny_position and positionAmt > 0.0:
            position.entry_price = current_price
            if position.was_reentered or position.was_reduced:
                position.was_reentered = False
                position.was_reduced = True
        if position.last_signal != 'PROFIT_TAKE':
            position.last_signal = 'REDUCE'
        if positionAmt > 0:
            position.max_gain = max(position.gain, position.max_gain)
            base_entry_for_gain = position.entry_price if position.entry_price not in (None, 0.0) else current_price
            if base_entry_for_gain <= 0 or base_entry_for_gain < current_price * 0.01 or base_entry_for_gain > current_price * 100:
                position.prev_gain=position.gain
                position.gain = 0.0
            else:
                position.prev_gain=position.gain
                position.gain = calculate_gain(position.position_side, current_price, base_entry_for_gain)
        else:
            position.prev_gain=position.gain
            position.gain = 0.0
        position.last_updated = now
        existing_reentry_amount = 0.0
        if position_key in self.reenter_data:
            existing_entry = self.reenter_data[position_key]
            if not isinstance(existing_entry, dict):
                logger.warning(f"[process_account] Corrupted reenter entry for {position_key}: {type(existing_entry).__name__}. Resetting entry.")
                self.reenter_data[position_key] = {}
            else:
                existing_reentry_amount = existing_entry.get("reenter_amount", 0.0)
        else:
            for old_key in list(self.reenter_data.keys()):
                if clean_position_key(str(old_key)) == position_key:
                    existing_entry = self.reenter_data.pop(old_key)
                    if isinstance(existing_entry, dict):
                        existing_reentry_amount = existing_entry.get("reenter_amount", 0.0)
                    break
        total_reduction_amount = min(position.max_quantity, existing_reentry_amount + reduce_qty)
        # Ensure reason is stored as a string, not a list
        reason_str = position.reduction_reason
        if isinstance(reason_str, list):
            reason_str = ', '.join(str(r) for r in reason_str)
        elif not isinstance(reason_str, str):
            reason_str = str(reason_str)
            
        self.reenter_data[position_key] = {
            "reenter_level": float(current_price) if current_price is not None else 0.0,
            "reenter_amount": float(total_reduction_amount),
            "timestamp": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            "reason": reason_str,  }
        logger.debug(f"[process_account] {position_key} Reenter level set: {self.reenter_data[position_key]}. 2313")
        await self.save_reenter_data(position_key)
        if position_key in self.augmented_positions:
            self.unmark_augmented(position_key)
        # Removed legacy stop_level tracking
        position_value_str = f"{positionAmt * current_price:.2f}" if current_price else "N/A"
        reduction_value_str = f"{reduce_qty * current_price:.2f}" if current_price else "N/A"
        conviction = None
        # Enhanced reduction reason detection - preserve existing reduction_reason unless new reason provided
        # CRITICAL: Never overwrite reduction_reason set by ez_manage unless explicitly provided
        if reason:
            # New reason explicitly provided - use it
            reduction_reason = reason
            position.reduction_reason = reason
        elif position.reduction_reason and position.reduction_reason.strip():
            # Preserve existing reduction_reason (set by ez_manage or previous reduction) - DO NOT OVERWRITE
            reduction_reason = position.reduction_reason
            conv_match = re.search(r'conv(-?[\d\.]+)', position.reduction_reason)
            if conv_match:
                try:
                    conviction = float(conv_match.group(1))
                except ValueError:
                    conviction = None
        elif reduction_source == "detected":
            # Auto-detected reduction - determine reason only if none exists
            reduction_reason = await self.determine_reduction_type(position_key, account_key, symbol, position_side, current_price, now)
            if reduction_reason:
                position.reduction_reason = reduction_reason
        else:
            # No reason exists and not detected - use empty string
            reduction_reason = ""
        rich_context = await self.fetch_decision_context(position_key)

        if position_key not in self.position_reasons:
            self.position_reasons[position_key] = {}
            
        self.position_reasons[position_key]['last_reduction_reason'] = reduction_reason
        self.position_reasons[position_key]['last_reduction_timestamp'] = now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        self.position_reasons[position_key]['last_reduction_amount'] = reduce_qty       
        self.position_reasons[position_key]['last_reduction_source'] = reduction_source
        if rich_context:
            self.position_reasons[position_key]['last_reduction_full_context'] = rich_context
            
            if 'history' not in self.position_reasons[position_key]:
                self.position_reasons[position_key]['history'] = []
                
            self.position_reasons[position_key]['history'].append({
                'type': 'REDUCE',
                'timestamp': now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'amount': reduce_qty,
                'price': current_price,
                'reason': reduction_reason,
                'context': rich_context  })
            
            await self.append_to_position_history_file(position_key, "REDUCE", reduce_qty, current_price, rich_context)

        log_reduce_action( position_key=position_key,  position_value_str=position_value_str,  reduction_value_str=reduction_value_str,   gain=position.gain, reason=reduction_reason, conviction=conviction,  origin=reduction_reason )
        account_positions = self.positions_by_account.setdefault(account_key, {})
        account_positions[position_key] = position
        self.positions[position_key] = position
        self._mark_positions_dirty()
        await self.save_state_all()


    def unmark_reversed(self, position_key: str):
        if position_key in self.reversed_positions:
            self.reversed_positions.pop(position_key, None)

    async def determine_reduction_type(self, position_key: str, account_key: str, symbol: str, position_side: str, current_price: float, now: datetime) -> str:
        try:
            if await self._was_stop_market_triggered(position_key, account_key, symbol, position_side, current_price):
                return "STOP_MARKET_ORDER_TRIGGERED"
            if await self._was_stop_level_hit(position_key, account_key, symbol, position_side, current_price):
                return "STOP_LEVEL_HIT"
            return "detected_reduction"
        except Exception as e:
            logger.error(f"[determine_reduction_type] Error for {position_key}: {e}")
            return "detected_reduction"

    async def _was_stop_market_triggered(self, position_key: str, account_key: str, symbol: str, position_side: str, current_price: float) -> bool:
        try:
            orders = await self.api_client.get_orders()
            target_side = 'sell' if position_side == 'LONG' else 'buy'
            for order in orders:
                if (order.get('type') in ['stop', 'stop_limit'] and order.get('status') == 'filled' and 
                    order.get('symbol') == symbol and order.get('side') == target_side):
                    order_time_str = order.get('transaction_date')
                    if order_time_str:
                        order_time = safe_datetime(order_time_str)
                        if order_time and (datetime.now(timezone.utc) - order_time).total_seconds() < 300:
                            return True
            return False
        except Exception as e:
            logger.error(f"[_was_stop_market_triggered] Error for {position_key}: {e}")
            return False

    async def _was_stop_level_hit(self, position_key: str, account_key: str, symbol: str, position_side: str, current_price: float) -> bool:
        position = self.positions.get(position_key)
        if position and position.stop_loss and position.stop_loss > 0:
            if position_side == "LONG" and current_price <= position.stop_loss:
                return True
            if position_side == "SHORT" and current_price >= position.stop_loss:
                return True
        return False

    def _ensure_all_fields(self, position: TradierPosition):
        """Ensure all fields are present with proper defaults"""
        now = datetime.now(timezone.utc)
        if position.stop_loss is None:
            position.stop_loss = 0.0
        if position.take_profit is None:
            position.take_profit = 0.0
        # if not position.entry_time:
        #     position.entry_time = now.isoformat()
        if not position.last_update:
            position.last_update = now.isoformat()
        if position.max_positionSize == 0.0:
            position.max_positionSize = config.MAX_POSITION_SIZE
        # if position.opened_at is None:
        #     position.opened_at = None
        if position.last_updated is None:
            position.last_updated = now

    async def load_account_positions_from_files(self):
        async with self._positions_lock:
            for fpath in [self.long_file, self.short_file]:
                if fpath.exists():
                    try:
                        with open(fpath, 'r') as f:
                            data = json.load(f)
                            loaded = 0
                            failed = 0
                            for k, v in data.items():
                                if k.startswith(f"{self.account_key}:"):
                                    try:
                                        self.positions[k] = TradierPosition(**v)
                                        self.positions_by_account[self.account_key][k] = self.positions[k]
                                        loaded += 1
                                    except Exception as pos_err:
                                        logger.error(f"[{self.account_key}] Failed to load position {k}: {pos_err}")
                                        failed += 1
                            logger.info(f"[{self.account_key}] Loaded {loaded} positions from {fpath.name} ({failed} failed)")
                    except Exception as e:
                        logger.error(f"[{self.account_key}] CRITICAL: Failed to load {fpath}: {e}", exc_info=True)

        async with self._positions_lock:
            raw_longs = {k: v.to_dict() for k, v in self.positions.items() if v.position_side == "LONG"}
            raw_shorts = {k: v.to_dict() for k, v in self.positions.items() if v.position_side == "SHORT"}
            
            # 2. Define Sorting Logic: Descending Value, then Alphabetical Key
            def get_sort_key(item):
                key, data = item
                try:
                    # Value = abs(Amount) * Price
                    amt = abs(float(data.get('positionAmt', 0)))
                    price = float(data.get('mark_price', 0) or data.get('entry_price', 0))
                    value = amt * price
                except (ValueError, TypeError):
                    value = 0.0
                # Python sorts tuples element-by-element:
                # 1. -value (Ascending negative = Descending positive)
                # 2. key (Alphabetical ascending for ties/zeros)
                return (-value, key)

            # 3. Apply Sort
            longs = dict(sorted(raw_longs.items(), key=get_sort_key))
            shorts = dict(sorted(raw_shorts.items(), key=get_sort_key))
            
            # 4. Write to disk
            for side, data in [("LONG", longs), ("SHORT", shorts)]:
                f = self.get_position_file(side)
                temp = f.with_suffix('.tmp')
                with open(temp, 'w') as fp: 
                    json.dump(data, fp, indent=2)
                shutil.move(str(temp), str(f))

    async def continuous_mark_price_update_loop(self):
        """Update mark prices for ALL positions every 30 seconds"""
        logger.info("[continuous_mark_price_update] Task started")
        while self.running:
            try:
                symbols_to_update = set()
                if self.position_manager:
                    for pk, pos in self.position_manager.positions.items():
                        if pos and hasattr(pos, 'symbol'):
                            symbols_to_update.add(pos.symbol)
                symbols_to_update.update(self.symbols_long_trb[:400])
                symbols_to_update.update(self.symbols_short_trb[:400])
                symbols_to_update.update(self.symbols_long_trc[:400])
                symbols_to_update.update(self.symbols_short_trc[:400])
                updated_count = 0
                now = datetime.now(timezone.utc)
                
                for symbol in symbols_to_update:
                    try:
                        # Get current price
                        current_price, price_ts = await self.get_current_price(symbol)
                        if not current_price or current_price <= 0:
                            continue
                        
                        # Update all positions with this symbol
                        for pk, pos in self.positions.items():
                            if not pos or not hasattr(pos, 'symbol'):
                                continue
                                
                            if pos.symbol == symbol:
                                # Update mark price if it's significantly different or stale
                                old_price = getattr(pos, 'mark_price', 0)
                                price_diff_pct = abs(current_price - old_price) / max(old_price, 1) * 100
                                
                                mark_ts = getattr(pos, 'mark_price_last_updated', None)
                                mark_age = 999
                                if mark_ts:
                                    if isinstance(mark_ts, str):
                                        mark_ts = isoparse(mark_ts)
                                    if mark_ts:
                                        mark_age = (now - mark_ts).total_seconds()
                                
                                # Update if price changed >0.1% or >30 seconds old
                                if price_diff_pct > 0.1 or mark_age > 30:
                                    pos.mark_price = current_price
                                    pos.mark_price_last_updated = now
                                    updated_count += 1
                                    
                    except Exception as e:
                        logger.debug(f"[continuous_mark_price_update] Error updating {symbol}: {e}")
                
                # Log periodically
                if updated_count > 0:
                    logger.debug(f"[continuous_mark_price_update] Updated {updated_count} mark prices")
                
                # Wait 30 seconds
                await asyncio.sleep(30)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[continuous_mark_price_update] Error: {e}")
                await asyncio.sleep(5)


    async def save_all(self):
        """Save all positions to disk with all fields"""
        async with self._positions_lock:
            try:
                # Convert objects to dicts with all fields
                raw_longs = {}
                raw_shorts = {}
                
                for key, position in self.positions.items():
                    if not isinstance(position, TradierPosition):
                        continue
                    
                    # Ensure all fields are present before saving
                    self._ensure_all_fields(position)
                    
                    pos_dict = position.to_dict()
                    
                    if position.position_side == "LONG":
                        raw_longs[key] = pos_dict
                    else:
                        raw_shorts[key] = pos_dict
                
                # Sort by value (descending), then by key
                def get_sort_key(item):
                    key, data = item
                    try:
                        amt = abs(float(data.get('positionAmt', 0)))
                        price = float(data.get('mark_price', 0) or data.get('entry_price', 0))
                        value = amt * price
                    except (ValueError, TypeError):
                        value = 0.0
                    return (-value, key)
                
                longs = dict(sorted(raw_longs.items(), key=get_sort_key))
                shorts = dict(sorted(raw_shorts.items(), key=get_sort_key))
                
                # Write to disk
                for side, data in [("LONG", longs), ("SHORT", shorts)]:
                    f = self.get_position_file(side)
                    temp = f.with_suffix('.tmp')
                    with open(temp, 'w') as fp:
                        json.dump(data, fp, indent=2, ensure_ascii=False, default=str)
                    shutil.move(str(temp), str(f))
                
                #logger.debug(f"[SAVE] Saved {len(longs)} long and {len(shorts)} short positions")
                
            except Exception as e:
                logger.error(f"[SAVE] Error saving positions: {e}")
    
    def _mark_positions_dirty(self):
        """Mark positions as dirty (need saving)"""
        self._positions_dirty = True
    
    def mark_augmented(self, position_key: str, timestamp: datetime):
        """Mark position as augmented"""
        self.augmented_positions[position_key] = timestamp
    
    def unmark_augmented(self, position_key: str):
        """Remove augmented mark from position"""
        self.augmented_positions.pop(position_key, None)
    
    async def _broadcast_position_updates_to_redis(self, account_key: str, updated_keys: Set[str]):
        """Broadcast specific updates - STRICTLY ISOLATED KEY"""
        try:
            if not self.redis_manager: return
            if account_key != self.account_key: return

            account_positions = self.positions_by_account[account_key]
            broadcast_data = {}
            
            for position_key, position in account_positions.items():
                self._ensure_all_fields(position)
                broadcast_data[position_key] = position.to_dict()
            redis_key = f"tradier_positions:{self.account_key}"
            await self.redis_manager.set(redis_key, broadcast_data)
        except Exception as e:
            logger.debug(f"[BROADCAST] Redis unavailable: {e}")

    async def get_current_price(self, symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
        symbol = symbol.strip().upper()
        now = datetime.now(timezone.utc)
        MAX_AGE = 30.0  

        try:
            # --- LAYER 1: LOCAL MEMORY ---
            # Using getattr handles the "has no attribute" error gracefully
            local_cache = getattr(self, 'price_cache', {})
            if symbol in local_cache:
                entry = local_cache[symbol]
                if isinstance(entry, (int, float)):
                    p = float(entry)
                    if p > 0:
                        return p, now
                elif isinstance(entry, dict):
                    p = float(entry.get('price', 0))
                    t = safe_parse_ts(entry.get('timestamp'))
                    if p > 0 and t and (now - t).total_seconds() < MAX_AGE:
                        return p, t

            # --- LAYER 2: REDIS (Metadata-Aware) ---
            if getattr(self, 'redis_manager', None):
                try:
                    payload = await asyncio.wait_for(self.redis_manager.get("tradier_prices_latest"), timeout=2.0)
                    if isinstance(payload, dict):
                        update_str = payload.get("_metadata", {}).get("updated_at")
                        bundle_time = safe_parse_ts(update_str)
                        
                        if bundle_time and (now - bundle_time).total_seconds() < MAX_AGE:
                            item = payload.get("data", {}).get(symbol)
                            if item:
                                p = float(item.get('price') or item.get('last', 0))
                                t = safe_parse_ts(item.get('timestamp') or item.get('date')) or bundle_time
                                # Store back in local cache
                                if hasattr(self, 'price_cache'):
                                    self.price_cache[symbol] = {'price': p, 'timestamp': t}
                                return p, t
                except Exception: pass

            # --- LAYER 3: DISK JSON ---
            try:
                latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
                if latest_file.exists() and (time.time() - latest_file.stat().st_mtime) < MAX_AGE:
                    async with aiofiles.open(latest_file, 'r') as f:
                        payload = json.loads(await f.read())
                        symbol_data = payload.get("data", payload)
                        if symbol in symbol_data:
                            item = symbol_data[symbol]
                            p = float(item.get('price') or item.get('last', 0))
                            t = safe_parse_ts(item.get('timestamp')) or now
                            return p, t
            except Exception: pass

            # --- FINAL FALLBACK: Owned Position Mark Price ---
            # Access self.positions safely
            positions = getattr(self, 'positions', {})
            for pk, pos in positions.items():
                try:
                    _, pk_sym, _ = parse_position_key(pk)
                except Exception:
                    pk_sym = ""
                if pk_sym == symbol:
                    p = getattr(pos, 'mark_price', 0)
                    t = getattr(pos, 'mark_price_last_updated', None)
                    if p > 0: return float(p), safe_parse_ts(t)

            return None, None
        except Exception as e:
            logger.error(f"get_current_price crash for {symbol}: {e}")
            return None, None
    # async def get_current_price(self, symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
    #     try:
    #         symbol = symbol.strip().upper()
            
    #         # 1. Internal Memory Cache (Fastest) - Check strict timestamp
    #         cached_price = self._price_cache.get(symbol)
    #         cached_time = self._price_cache_time.get(symbol)
            
    #         if cached_price and cached_price > 0 and cached_time:
    #             # You can add logic here: if (now - cached_time) > 60s, ignore it.
    #             # But for now, we return the Valid Source Timestamp.
    #             return float(cached_price), cached_time

    #         # 2. Redis/JSON Cache (Shared)
    #         # Helper to extract price/time from dict
    #         def extract_from_entry(entry):
    #             if not isinstance(entry, dict): return None, None
    #             p = entry.get('price') or entry.get('last') or entry.get('current_price')
                
    #             # STRICT TIMESTAMP EXTRACTION
    #             t_val = entry.get('timestamp') or entry.get('date') or entry.get('updated_at')
    #             t_obj = None
                
    #             if t_val:
    #                 try:
    #                     if isinstance(t_val, (int, float)):
    #                         # Check if ms or seconds
    #                         if t_val > 1e11: t_obj = datetime.fromtimestamp(t_val / 1000.0, tz=timezone.utc)
    #                         else: t_obj = datetime.fromtimestamp(t_val, tz=timezone.utc)
    #                     elif isinstance(t_val, str):
    #                         t_obj = isoparse(t_val)
    #                         if t_obj.tzinfo is None: t_obj = t_obj.replace(tzinfo=timezone.utc)
    #                 except: pass
                
    #             if p and float(p) > 0 and t_obj:
    #                 return float(p), t_obj
    #             return None, None

    #         # Try Redis
    #         if self.redis_manager:
    #             try:
    #                 md = await self.redis_manager.get(self.config.REDIS_KEY_MARKET_DATA)
    #                 if isinstance(md, dict) and symbol in md:
    #                     p, t = extract_from_entry(md[symbol])
    #                     if p and t: return p, t
    #             except: pass

    #         # Try JSON File
    #         try:
    #             latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
    #             if latest_file.exists():
    #                 # Optimization: Check file mtime first? 
    #                 # For now read to get exact quote time
    #                 async with aiofiles.open(latest_file, 'r') as f:
    #                     content = await f.read()
    #                     md = json.loads(content)
    #                     if isinstance(md, dict) and symbol in md:
    #                         p, t = extract_from_entry(md[symbol])
    #                         if p and t: return p, t
    #         except: pass

    #         # 3. Last Resort: Existing position mark_price
    #         # (Only use if we can't find a fresh quote, but do NOT update timestamp to now)
    #         for pk, pos in self.positions.items():
    #             if hasattr(pos, 'symbol') and pos.symbol == symbol:
    #                 p = getattr(pos, 'mark_price', 0)
    #                 t = getattr(pos, 'mark_price_last_updated', None)
    #                 if p > 0 and t:
    #                      return float(p), t

    #         return None, None

    #     except Exception as e:
    #         logger.error(f"Error in get_current_price({symbol}): {e}")
    #         return None, None

    async def update_positions_loop(self):
        offset = 0
        if self.account_key == "tra": offset = 4
        if self.account_key == "trc": offset = 10
        while self.running:
            try:
                if not _is_trading_hours():
                    await asyncio.sleep(30)
                    continue
                await self.process_account_update()
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.account_key}] Update Loop Error: {e}")
                await asyncio.sleep(15)

    async def load_all_account_positions(self):
        """Load positions for ALL accounts from their respective directories."""
        async with self._positions_lock:
            d = self.account_dir
            for side in ['long', 'short']:
                fpath = d / f"{side}_positions.json"
                if fpath.exists():
                    try:
                        with open(fpath, 'r') as f:
                            data = json.load(f)
                            for k, v in data.items():
                                # Ensure key integrity
                                if k.startswith(f"{self.account_key}:"):
                                    self.positions[k] = TradierPosition(**v)
                    except Exception as e:
                        logger.error(f"[{self.account_key}] Failed loading {fpath.name}: {e}")

    async def save_all_accounts(self):
        """Save positions for ALL accounts to their respective directories."""
        async with self._positions_lock:
            data_map = defaultdict(lambda: {'LONG': {}, 'SHORT': {}})
            for k, pos in self.positions.items():
                try:
                    acc, sym, side = parse_position_key(k)
                    data_map[acc][side][k] = pos.to_dict()
                except Exception: continue

            # Write files
            for acc, sides in data_map.items():
                d = self.account_dir
                for side, content in sides.items():
                    # Sort
                    sorted_content = dict(sorted(content.items(), key=lambda item: (
                        -abs(float(item[1].get('positionAmt', 0))) * float(item[1].get('mark_price', 0) or 0), 
                        item[0]
                    )))
                    fpath = d / f"{side.lower()}_positions.json"
                    await atomic_write_json(fpath, sorted_content)

    async def ensure_permanent_positions_all(self):
        """DISABLED — positions are NEVER created from zero."""
        return

    async def load_aux_data(self):
        for side in ['long', 'short']:
            f = self.account_dir / f"reenter_data_{side}.json"
            if f.exists():
                try:
                    data = await load_json_safe(f)
                    for k, v in data.items():
                        if k.startswith(f"{self.account_key}:"):
                            self.reenter_data[k] = v
                except Exception as e:
                    logger.error(f"[{self.account_key}] Failed to load {f.name}: {e}")
        for side in ['long', 'short']:
            f = self.account_dir / f"ladder_levels_{side}.json"
            if f.exists():
                try:
                    data = await load_json_safe(f)
                    for k, v in data.items():
                        if k.startswith(f"{self.account_key}:"):
                            self.ladder_levels[k] = v
                except Exception as e:
                    logger.error(f"[{self.account_key}] Failed to load {f.name}: {e}")
        f = self.account_dir / "invalidation_levels.json"
        if f.exists():
            try:
                data = await load_json_safe(f)
                for k, v in data.items():
                    if k.startswith(f"{self.account_key}:"):
                        self.invalidation_levels[k] = v
            except Exception as e:
                logger.error(f"[{self.account_key}] Failed to load {f.name}: {e}")

    async def save_aux_data(self):
        # Save Reentry
        for side in ['long', 'short']:
            subset = {k:v for k,v in self.reenter_data.items() if k.startswith(f"{self.account_key}:") and k.endswith(f"_{side.upper()}")}
            await atomic_write_json(self.account_dir / f"reenter_data_{side}.json", subset)
        
        # Save Ladders
        for side in ['long', 'short']:
            subset = {k:v for k,v in self.ladder_levels.items() if k.startswith(f"{self.account_key}:") and k.endswith(f"_{side.upper()}")}
            await atomic_write_json(self.account_dir / f"ladder_levels_{side}.json", subset)

        # Save Invalidation
        subset = {k:v for k,v in self.invalidation_levels.items() if k.startswith(f"{self.account_key}:")}
        await atomic_write_json(self.account_dir / "invalidation_levels.json", subset)

    async def save_invalidation_levels(self, account_key: str):
        """Save invalidation levels to persistent storage."""
        try:
            if not account_key or not isinstance(account_key, str):
                logger.error(f"[save_invalidation_levels] Invalid account_key: {account_key}")
                return
            account_invalidation_levels = {}
            for position_key, invalidation_data in self.invalidation_levels.items():
                if position_key.startswith(f"{account_key}:"):
                    if isinstance(invalidation_data, dict):
                        sanitized = dict(invalidation_data)
                        if 'timestamp' in sanitized and isinstance(sanitized['timestamp'], datetime):
                            sanitized['timestamp'] = sanitized['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        account_invalidation_levels[position_key] = sanitized
                    else:
                        account_invalidation_levels[position_key] = invalidation_data
            if not account_invalidation_levels:
                return  # 
            file_path = f"data/invalidation_levels_{account_key}.json"
            await atomic_write_json(file_path, account_invalidation_levels)
        except Exception as e:
            logger.exception(f"[save_invalidation_levels] Failed to save invalidation levels for account '{account_key}': {e}")

    async def load_invalidation_levels(self, account_key: str):
        """Load invalidation levels from persistent storage."""
        try:
            if not account_key or not isinstance(account_key, str):
                logger.error(f"[load_invalidation_levels] Invalid account_key: {account_key}")
                return
            file_path = f"data/invalidation_levels_{account_key}.json"
            invalidation_data = await load_json_safe(file_path)
            if isinstance(invalidation_data, dict):
                loaded_count = 0
                for position_key, data in invalidation_data.items():
                    if isinstance(data, dict) and 'level' in data and 'timestamp' in data and 'position_side' in data:
                        try:
                            if isinstance(data['timestamp'], str):
                                ts = pd.to_datetime(data['timestamp'], utc=True).to_pydatetime()
                                if ts.tzinfo is None:
                                    ts = ts.replace(tzinfo=timezone.utc)
                                data['timestamp'] = ts
                            self.invalidation_levels[position_key] = data
                            loaded_count += 1
                        except (ValueError, TypeError) as e:
                            logger.warning(f"[load_invalidation_levels] Invalid timestamp for {position_key}: {e}")
                            continue
        except Exception as e:
            logger.exception(f"[load_invalidation_levels] Error loading invalidation levels for account '{account_key}': {e}")

    async def cleanup_old_invalidation_levels(self):
        """Clean up invalidation levels older than 20 minutes."""
        try:
            now = datetime.now(timezone.utc)
            keys_to_remove = []
            for position_key, invalidation_data in self.invalidation_levels.items():
                if isinstance(invalidation_data, dict) and 'timestamp' in invalidation_data:
                    timestamp = invalidation_data['timestamp']
                    if isinstance(timestamp, datetime):
                        if minutes_since(timestamp, now) > 20:
                            keys_to_remove.append(position_key)
            for key in keys_to_remove:
                del self.invalidation_levels[key]
        except Exception as e:
            logger.exception(f"[cleanup_old_invalidation_levels] Error during cleanup: {e}")

    async def _invalidation_cleanup_loop(self):
        """Periodically clean up old invalidation levels"""
        while self.running:
            try:
                await asyncio.sleep(300) # Every 5 mins
                await self.cleanup_old_invalidation_levels()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.account_key}] Invalidation cleanup error: {e}")

    async def _pnl_decay_loop(self) -> None:
        """Decay PnL and max gain for zero positions"""
        first_run = True
        try:
            while self.running:
                if not first_run:
                    await asyncio.sleep(60)
                else:
                    first_run = False
                now = datetime.now(timezone.utc)
                touched_accounts: Set[str] = set()
                async with self._positions_lock:
                    for position_key, position in self.positions.items():
                        if not isinstance(position, TradierPosition):
                            continue
                        account_key, _, _ = parse_position_key(position_key)
                        if not position or position.positionAmt != 0: 
                            continue
                        updated = False
                        new_pnl = self.deteriorate_realized_pnl(account_key, position)
                        if abs(new_pnl - position.realized_pnl) > 0.01:
                            position.realized_pnl = new_pnl
                            updated = True
                        new_max_gain = self.deteriorate_max_gain(account_key, position)
                        if abs(new_max_gain - position.max_gain) > 0.01:
                            position.max_gain = new_max_gain
                            updated = True
                        if updated:
                            try:
                                account_key, _, _ = parse_position_key(position_key)
                                touched_accounts.add(account_key)
                            except Exception:
                                continue
                for account_key in touched_accounts:
                    self._mark_positions_dirty()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"[pnl_decay] loop error: {exc}")
    
    async def _position_flush_loop(self) -> None:
        """Periodically flush positions to disk"""
        interval = 5.0
        event = self._position_flush_event
        try:
            while self.running:
                triggered = False
                if event:
                    try:
                        await asyncio.wait_for(event.wait(), timeout=interval)
                        triggered = True
                    except asyncio.TimeoutError:
                        pass
                    if event:
                        event.clear()
                else:
                    await asyncio.sleep(interval)
                if not self.running:
                    break
                
                if self._positions_dirty:
                    try:
                        await self.save_all()
                        self._positions_dirty = False
                    except Exception as exc:
                        logger.debug(f"[position_flush] save error: {exc}")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug(f"[position_flush] loop error: {exc}")
    
    async def _periodic_update_zero_positions(self) -> None:
        """Periodic task to update deteriorating values for all 0 positionAmt positions every hour"""
        await asyncio.sleep(600)
        while self.running:
            try:
                logger.warning("[_periodic_update_zero_positions] 🔄 Starting periodic update of 0 positionAmt positions...")
                updated_count = 0
                for account_key in [self.account_key]:
                    account_positions = self.positions_by_account[account_key]
                    for position_key, position in list(account_positions.items()):
                        if not isinstance(position, TradierPosition):
                            continue
                        position_amt = abs(getattr(position, 'positionAmt', 0))
                        if position_amt == 0.0:
                            try:
                                symbol = position.symbol
                                entry_price = getattr(position, 'entry_price', 0) or 0.0
                                position_side = getattr(position, 'position_side', 'LONG') or 'LONG'
                                current_price = getattr(position, 'mark_price', 0) or 0.0
                                if current_price <= 0:
                                    try:
                                        price_tuple = await self.get_current_price(symbol)
                                        if price_tuple and price_tuple[0]:
                                            current_price = price_tuple[0]
                                    except Exception:
                                        continue
                                if current_price > 0 and entry_price > 0:
                                    gain = calculate_gain(position_side, current_price, entry_price)
                                else:
                                    gain = 0.0

                                position.mark_price = current_price
                                position.max_gain = max(position.max_gain, gain)
                                position.prev_gain = gain
                                position.prev_gain_last_updated = datetime.now(timezone.utc)
                                
                                updated_count += 1
                                if updated_count % 50 == 0:
                                    if self.config.VERBOSE_FETCH_LOGGING: 
                                        logger.info(f"[_periodic_update_zero_positions] Progress: {updated_count} positions updated so far...")
                            except Exception as e:
                                logger.error(f"[_periodic_update_zero_positions] Error updating {position_key}: {e}", exc_info=True)
                logger.warning(f"[_periodic_update_zero_positions] Periodic update completed: {updated_count} zero positions updated")
            except Exception as e:
                logger.error(f"[_periodic_update_zero_positions] Error in periodic task: {e}", exc_info=True)
            await asyncio.sleep(3600)
    
    async def _periodic_full_save_loop(self) -> None:
        """Periodic full save loop"""
        await asyncio.sleep(1.0)
        while self.running:
            try:
                now = time.time()
                if now - self._last_full_save_time >= self._full_save_interval:
                    logger.debug(f"[_periodic_full_save_loop] Running periodic full save (every {self._full_save_interval}s)")
                    await self.save_all()
                    logger.debug(f"[_periodic_full_save_loop] Periodic full save completed for all accounts")
                    self._last_full_save_time = now
                await asyncio.sleep(self._full_save_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[_periodic_full_save_loop] Error: {e}", exc_info=True)
                await asyncio.sleep(self._full_save_interval)

    async def update_prev_gain_periodically(self) -> None:
        first_run = True
        try:
            while self.running:
                if not first_run:
                    await asyncio.sleep(180) # 3 minute interval
                else:
                    first_run = False
                now = datetime.now(timezone.utc)
                snapshot: List[Tuple[str, str, str, float, float, Optional[datetime]]] = []         
                async with self._positions_lock:
                    for position_key, position in self.positions.items():
                        symbol = getattr(position, "symbol", "")
                        position_side = getattr(position, "position_side", "")
                        entry = safe_fetch_float(getattr(position, "entry_price", 0.0), 0.0)
                        mark_price = safe_fetch_float(getattr(position, "mark_price", 0.0), 0.0)
                        mark_ts = getattr(position, "mark_price_last_updated", None)
                        snapshot.append((position_key, symbol, position_side, entry, mark_price, mark_ts))
                updates: Dict[str, Tuple[float, float, datetime]] = {}
                touched_accounts: Set[str] = set()
                for position_key, symbol, position_side, entry_price, mark_price, mark_ts in snapshot:
                    account_key, _, _ = parse_position_key(position_key)
                    touched_accounts.add(account_key)
                    current_price = mark_price or 0.0
                    mark_dt = None
                    if isinstance(mark_ts, str):
                        try: mark_dt = isoparse(mark_ts)
                        except Exception: mark_dt = None
                    elif isinstance(mark_ts, datetime):
                        mark_dt = mark_ts
                    if mark_dt and mark_dt.tzinfo is None:
                        mark_dt = mark_dt.replace(tzinfo=timezone.utc)
                    price_age = (now - mark_dt).total_seconds() if mark_dt else None
                    if current_price <= 0.0 or (price_age is not None and price_age > 90):
                        refreshed_price,ts = await self.get_current_price(symbol)
                        if refreshed_price and refreshed_price > 0:
                            current_price = refreshed_price
                    if current_price <= 0.0:
                        current_price = entry_price if entry_price > 0 else 0.0
                    calc_entry = entry_price if entry_price > 0 else current_price
                    if calc_entry <= 0 or calc_entry < current_price * 0.01 or calc_entry > current_price * 100 or position.positionAmt==0:
                        current_gain = 0.0
                    else:
                        position.prev_gain = position.gain
                        position.prev_gain_last_updated = now
                        current_gain = calculate_gain(position_side, current_price, calc_entry if calc_entry > 0 else current_price or 1.0)
                    updates[position_key] = (current_gain, current_price, now)
                async with self._positions_lock:
                    for position_key, (gain_value, price_value, timestamp_value) in updates.items():
                        position = self.positions.get(position_key)
                        if not position: continue
                        is_active = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0))) > 0
                        closed_at = getattr(position, 'last_reduction_time', None)
                        time_since_close = minutes_since(closed_at) if closed_at else 999999
                        if price_value > 0:
                            position.mark_price = price_value
                            position.mark_price_last_updated = timestamp_value
                        position.gain = gain_value
                        if is_active or time_since_close > 120:
                            position.prev_gain = position.gain
                            position.prev_gain_last_updated = timestamp_value
                        try:
                            parsed_account_key, _, _ = parse_position_key(position_key)
                            self.positions_by_account.setdefault(parsed_account_key, {})[position_key] = position
                        except Exception: pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"[prev_gain] loop error: {exc}")
   

    def deteriorate_realized_pnl(self, account_key: str, position: TradierPosition) -> float:
        """Deteriorate realized PnL over time"""
        if position.realized_pnl == 0:
            return 0.0
        if not position.last_updated:
            return position.realized_pnl
        
        now = datetime.now(timezone.utc)
        hours_passed = (now - position.last_updated).total_seconds() / 3600.0
        
        deterioration_factor = max(0, 1.0 - (hours_passed * 0.1))
        return position.realized_pnl * deterioration_factor
    
    def deteriorate_max_gain(self, account_key: str, position: TradierPosition) -> float:
        if position.max_gain == 0:
            return 0.0
        if position.positionAmt and position.positionAmt > 0.0:
            return position.max_gain
        last_reduction = position.last_reduction_time
        if not last_reduction:
            return position.max_gain
        now = datetime.now(timezone.utc)
        time_since = now - last_reduction
        decay_start = timedelta(hours=24)
        decay_complete = timedelta(days=7)
        if time_since < decay_start:
            return position.max_gain
        if time_since >= decay_complete:
            return position.max_gain * 0.5
        progress = (time_since - decay_start).total_seconds() / max((decay_complete - decay_start).total_seconds(), 1.0)
        decay_factor = 0.9 ** (progress * 5)
        return position.max_gain * decay_factor
    
    def deteriorate_max_quantity(self, account_key: str, position: TradierPosition, current_price: float) -> float:
        if position.positionAmt and position.positionAmt > 0.0:
            return position.max_quantity
        if not position.last_reduction_time or current_price <= 0:
            return position.max_quantity
        now = datetime.now(timezone.utc)
        time_since = now - position.last_reduction_time
        deterioration_start = timedelta(hours=24)
        deterioration_complete = timedelta(days=7)
        if time_since < deterioration_start:
            return position.max_quantity
        target_max_qty = 2 * self.config.START_POSITION_SIZE / current_price if current_price > 0 else position.max_quantity
        original_max_qty = position.max_quantity
        if time_since >= deterioration_complete:
            if original_max_qty > target_max_qty:
                return target_max_qty
            return original_max_qty
        elapsed = time_since - deterioration_start
        progress = min(1.0, elapsed.total_seconds() / max((deterioration_complete - deterioration_start).total_seconds(), 1.0))
        if original_max_qty <= target_max_qty:
            return original_max_qty
        deteriorated_qty = original_max_qty - (original_max_qty - target_max_qty) * progress
        deteriorated_qty = max(deteriorated_qty, target_max_qty)
        return deteriorated_qty


    async def start(self):
        logger.info(f"[{self.account_key}] Starting Manager...")
        await self.api_client.connect()
        self.redis_manager = RedisPositionManager(self.config)
        await self.redis_manager.connect()
        
        await self.load_account_positions_from_files()
        await self.load_aux_data() # Ensure this method now filters by self.account_key
        await self.ensure_permanent_positions()
        await self.load_reenter_data()
        await self.load_ladder_levels()
        await self.load_state_all()
        await self.load_invalidation_levels(self.account_key) 
        await self.cleanup_all_backups_on_startup()
        await self.refresh_active_symbols()
        
    
        self.running = True
        self._loading_complete_event.set()
        for symbol in self.symbols:
            self.min_qty[symbol] = 1.0

        self.background_tasks = [
            asyncio.create_task(self.update_positions_loop()),           # CORE: API Sync
            asyncio.create_task(self.update_all_prices_loop()),          # CORE: Prices
            asyncio.create_task(self._reentry_maintenance_loop()),       # LOGIC: Reentry Decay
            asyncio.create_task(self.update_prev_gain_periodically()),   # LOGIC: Prev Gain Snapshot
            asyncio.create_task(self._pnl_decay_loop()),                 # LOGIC: PnL Decay
            asyncio.create_task(self._position_flush_loop()),            # IO: Disk Flush
            asyncio.create_task(self._periodic_update_zero_positions()), # IO: Zero Pos Refresh
            asyncio.create_task(self._periodic_full_save_loop()),        # IO: Safety Save
            asyncio.create_task(self._periodic_permanence_check()),      # LOGIC: Restore deleted keys
            asyncio.create_task(self._invalidation_cleanup_loop()) ]# ,       
            # asyncio.create_task(self.continuous_mark_price_update_loop()) ]
        
        logger.info(f"[{self.account_key}] All {len(self.background_tasks)} loops started successfully.")

    async def load_reenter_data(self):
        """Load reenter data from files"""
        try:
            for position_side in ["LONG", "SHORT"]:
                reenter_file = self.config.DATA_DIR / f"reenter_data_{self.account_key}_{position_side.lower()}.json"
                if reenter_file.exists():
                    with open(reenter_file, 'r') as f:
                        data = json.load(f)
                        for key, entry in data.items():
                            self.reenter_data[key] = entry
            
            logger.debug(f"Loaded {len(self.reenter_data)} reenter entries")
        except Exception as e:
            logger.debug(f"Error loading reenter data: {e}")
    
    async def load_ladder_levels(self):
        """Load ladder levels from files"""
        try:
            for position_side in ["LONG", "SHORT"]:
                ladder_file = self.config.DATA_DIR / f"ladder_levels_{self.account_key}_{position_side.lower()}.json"
                if ladder_file.exists():
                    with open(ladder_file, 'r') as f:
                        data = json.load(f)
                        for key, entry in data.items():
                            self.ladder_levels[key] = entry
            
            logger.debug(f"Loaded {len(self.ladder_levels)} ladder levels")
        except Exception as e:
            logger.debug(f"Error loading ladder levels: {e}")
    
    async def _reentry_maintenance_loop(self):
        """Maintenance loop for reentry data"""
        await asyncio.sleep(5.0)
        while self.running:
            try:
                await self._hourly_deteriorate_reentries()
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[reentry] maintenance loop error: {exc}")
                await asyncio.sleep(60)
    
    async def _hourly_deteriorate_reentries(self):
        """Deteriorate reentry amounts hourly"""
        now = datetime.now(timezone.utc)
        for position_key, entry in list(self.reenter_data.items()):
            try:
                account_key, symbol, _ = parse_position_key(position_key)
                position = self.positions.get(position_key)
                if not isinstance(position, TradierPosition):
                    continue
                
                current_price = position.mark_price
                if current_price <= 0:
                    continue
                
                ts_value = entry.get("timestamp")
                if ts_value:
                    try:
                        ts_dt = isoparse(ts_value) if isinstance(ts_value, str) else ts_value
                        if ts_dt.tzinfo is None:
                            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                        minutes_out = (now - ts_dt).total_seconds() / 60.0
                        progress = min(1.0, max(0.0, minutes_out / 60.0))
                    except Exception:
                        progress = 1.0
                else:
                    progress = 1.0
                
                original_amount = entry.get("reenter_amount", 0.0)
                if original_amount > 0:
                    target_amount = self.config.START_POSITION_SIZE / max(current_price, 1e-9)
                    deteriorated_amount = original_amount - (original_amount - target_amount) * progress
                    deteriorated_amount = max(deteriorated_amount, target_amount)
                    entry["reenter_amount"] = deteriorated_amount
                    
                    if abs(deteriorated_amount - original_amount) > 0.01:
                        entry["timestamp"] = now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        await self.save_reenter_data(position_key)
                        
            except Exception as exc:
                logger.debug(f"[reentry] deterioration failed for {position_key}: {exc}")
    
    async def cleanup_all_backups_on_startup(self):
        """Clean up all backup files on startup"""
        try:
            for position_side in ["LONG", "SHORT"]:
                pos_file = self.get_position_file(position_side)
                backup_dir = pos_file.parent / "backups"
                if backup_dir.exists():
                    await self.prune_old_backups(str(backup_dir), pos_file.stem)
            
            await self.cleanup_old_position_files()
            logger.info("Cleaned up all backups on startup")
        except Exception as e:
            logger.error(f"Error cleaning up backups on startup: {e}")
    
    async def save_reenter_data(self, position_key: str):
        """Save reenter data to file"""
        try:
            account_key, symbol, position_side = parse_position_key(position_key)
            reenter_file = self.config.DATA_DIR / f"reenter_data_{account_key}_{position_side.lower()}.json"
            
            reenter_data_filtered = {}
            for key, entry in self.reenter_data.items():
                if key.startswith(f"{account_key}:") and key.endswith(f"_{position_side}"):
                    reenter_data_filtered[key] = entry
            
            await atomic_write_json(reenter_file, reenter_data_filtered)
            logger.debug(f"Saved {len(reenter_data_filtered)} reenter entries")
        except Exception as e:
            logger.error(f"Failed to save reenter data: {e}")
    
    async def create_ladder_levels_for_reentry(self, position_key: str, account_key: str, position: TradierPosition, current_price: float, now: datetime):
        """Create indicator-driven ladder levels for reentry after reduction"""
        try:
            symbol = position.symbol
            is_long = position.position_side == 'LONG'
            
            # 1. Fetch Indicators
            indicators = {}
            if self.redis_manager:
                try:
                    data = await self.redis_manager.get(config.REDIS_KEY_MARKET_DATA)
                    if data and symbol in data:
                        indicators = data[symbol]
                except Exception: pass
            
            # Helper to safely extract floats
            def _safe_indicator(key: str, default: float) -> float:
                try:
                    val = indicators.get(key, default)
                    if val is None: return default
                    val_float = float(val)
                    if math.isnan(val_float) or math.isinf(val_float): return default
                    return val_float
                except Exception: return default

            # 2. Extract Levels
            dc_basis_3m = _safe_indicator('dc_basis_3m', current_price)
            dc_basis_15m = _safe_indicator('dc_basis_15m', current_price)
            dc_basis_1h = _safe_indicator('dc_basis_1h', current_price)
            
            atr_3m = _safe_indicator('atr_3m', abs(current_price) * 0.01)
            atr_15m = _safe_indicator('atr_15m', abs(current_price) * 0.015)
            atr_1h = _safe_indicator('atr_1h', abs(current_price) * 0.02)
            atr_4h = _safe_indicator('atr_4h', abs(current_price) * 0.025)

            # Extract Channel Boundaries
            dc_low_3m = _safe_indicator('dc_low_3m', current_price)
            dc_low_15m = _safe_indicator('dc_low_15m', current_price)
            dc_low_1h = _safe_indicator('dc_low_1h', current_price)
            dc_low_4h = _safe_indicator('dc_low_4h', current_price)
            
            dc_high_3m = _safe_indicator('dc_high_3m', current_price)
            dc_high_15m = _safe_indicator('dc_high_15m', current_price)
            dc_high_1h = _safe_indicator('dc_high_1h', current_price)
            dc_high_4h = _safe_indicator('dc_high_4h', current_price)

            # 3. Calculate positionAmt
            ladder_qty = float(position.last_reduction_amount or 0.0)
            if ladder_qty <= 0:
                ladder_qty = position.positionAmt or (config.START_POSITION_SIZE / max(abs(current_price), 1e-9))
            
            if ladder_qty <= 0:
                logger.warning(f"[{position_key}] Invalid ladder qty {ladder_qty}, skipping")
                return

            # 4. Generate Price Levels
            ladder_prices = []
            if is_long:
                ladder_prices = [
                    dc_basis_3m,                    # Retest of short-term basis
                    dc_basis_15m,                   # Retest of medium-term basis
                    dc_basis_1h,                    # Retest of hourly basis
                    dc_low_3m + float(atr_3m) * 1.5, # Bounce off 3m low
                    dc_low_15m + float(atr_15m) * 1.5,
                    dc_low_1h + float(atr_1h) * 1.5,
                    dc_low_4h + float(atr_4h) * 1.5  # Major support
                ]
            else:
                ladder_prices = [
                    dc_basis_3m,
                    dc_basis_15m,
                    dc_basis_1h,
                    dc_high_3m - float(atr_3m) * 1.5, # Rejection off 3m high
                    dc_high_15m - float(atr_15m) * 1.5,
                    dc_high_1h - float(atr_1h) * 1.5,
                    dc_high_4h - float(atr_4h) * 1.5
                ]

            # 5. Sanitize & Filter Levels
            ladder_levels = []
            seen_levels = set()
            
            # Filter logic: Must be BELOW current for LONG, ABOVE for SHORT (Pullback logic)
            for price in ladder_prices:
                if not price or price <= 0: continue
                
                # Check directional logic (We want to buy dips, sell rips)
                if is_long and price >= current_price * 0.999: continue 
                if not is_long and price <= current_price * 1.001: continue
                
                # Deduplicate close levels
                rounded = round(price, 2)
                if rounded in seen_levels: continue
                seen_levels.add(rounded)

                ladder_levels.append({
                    "level": float(price),
                    "positionAmt": float(ladder_qty),
                    "created_at": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "crossed": False,
                    "stoch_reversed": False
                })

            # 6. Fallback if no indicators
            if not ladder_levels:
                logger.warning(f"[{position_key}] No valid indicator levels found. Using fallback.")
                multipliers = [0.985, 0.975, 0.965] if is_long else [1.015, 1.025, 1.035]
                for m in multipliers:
                    level = current_price * m
                    ladder_levels.append({
                        "level": float(level),
                        "positionAmt": float(ladder_qty),
                        "created_at": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                        "crossed": False,
                        "stoch_reversed": False
                    })

            # 7. Save
            self.ladder_levels[position_key] = {
                "levels": ladder_levels,
                "created_at": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "expires_at": None,
                "is_long": is_long,
                "symbol": symbol,
                "account_key": account_key,  }

            await self.save_ladder_levels(position_key)
            logger.debug(f"[{position_key}] Created {len(ladder_levels)} ladder levels (Indicator-Driven)")

        except Exception as e:
            logger.error(f"[{position_key}] Failed to create ladder levels: {e}")
    
    async def fetch_decision_context(self, position_key: str) -> dict:
        """
        Retrieve decision context from Redis with retries.
        The Strategy (Brain) writes this key. We (Memory) read it.
        """
        if not self.redis_manager: 
            # Try to lazy-connect if missing
            try:
                from utils import get_simple_redis_manager, orjson_default
                self.redis_manager = await get_simple_redis_manager()
            except Exception:
                return {}

        redis_key = f"decision:{position_key}"
        
        # Get the read client
        client = None
        if hasattr(self.redis_manager, 'connections'):
            client = self.redis_manager.connections.get('local')
        elif hasattr(self.redis_manager, 'get'):
            # It might be the direct redis client
            client = self.redis_manager

        if not client:
            return {}

        # RETRY LOOP: Wait up to 3 seconds for the decision to appear.
        # Often the websocket update beats the strategy's redis write.
        for _ in range(6): # 6 attempts * 0.5s = 3 seconds max
            try:
                data = await client.get(redis_key)
                if data:
                    return orjson.loads(data)  # pylint: disable=no-member
            except Exception as e:
                logger.debug(f"Redis read error: {e}")
            
            await asyncio.sleep(0.5)
            
        return {}

    async def _append_to_history(self, account_key: str, position_key: str, trade_type: str, qty: float, price: float, context: dict):
        try:
            # 1. Parse Key
            parts = position_key.split(':')
            symbol_side = parts[1] if len(parts) > 1 else position_key
            
            # 2. Setup Path
            hist_dir = self.config.DATA_DIR / "history" / account_key
            hist_dir.mkdir(parents=True, exist_ok=True)
            filename = hist_dir / f"{symbol_side}.jsonl"
            
            # 3. ROTATION LOGIC (Max 200KB)
            MAX_SIZE_BYTES = 200 * 1024 
            if filename.exists():
                try:
                    stat = filename.stat()
                    if stat.st_size > MAX_SIZE_BYTES:
                        backup = filename.with_name(f"{filename.name}.bak")
                        os.replace(filename, backup)
                except Exception: pass

            # 4. Reason Fallback Logic
            # Priority: Context > Position Object > Default
            reason = "Manual/Unknown"
            if context:
                reason = context.get('reason_text') or context.get('reason') or "Unknown_Context"
            else:
                # Fallback: Try to read from current position in memory if available
                pos = self.positions.get(position_key)
                if pos:
                    if trade_type == "AUGMENT": reason = getattr(pos, 'augment_reason', 'Manual_Augment')
                    elif trade_type == "REDUCE": reason = getattr(pos, 'reduction_reason', 'Manual_Reduce')

            # 5. Prepare Entry
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": trade_type,
                "qty": qty,
                "price": price,
                "value": qty * price,
                "reason": reason,
                "score": context.get('tech_score', context.get('score', 0)),
                "snapshot": context.get('indicators', context.get('snapshot', {})),
                "extra": context.get('extra', {})  }
            async with aiofiles.open(filename, "a") as f:
                await f.write(json.dumps(entry, default=str) + "\n")
                
        except Exception as e:
            logger.error(f"[HISTORY] Failed to write {position_key}: {e}") 

    async def cleanup_old_position_files(self):
        """Clean up old position JSON files"""
        try:
            now = datetime.now(timezone.utc)
            cutoff_15min = now - timedelta(minutes=15)
            cutoff_24hours = now - timedelta(hours=24)
            
            position_files = sorted(
                self.config.DATA_DIR.glob("tradier_positions_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            
            latest_file = self.config.DATA_DIR / "tradier_positions_latest.json"
            position_files = [f for f in position_files if f.name != "tradier_positions_latest.json"]
            
            files_to_keep = set()
            per_hour: Dict[str, List[Path]] = {}
            per_4hour: Dict[str, List[Path]] = {}
            per_day: Dict[str, List[Path]] = {}
            
            for file_path in position_files:
                try:
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                    
                    if file_mtime >= cutoff_15min:
                        files_to_keep.add(file_path)
                    elif file_mtime >= cutoff_24hours:
                        hour_key = file_mtime.strftime("%Y%m%d_%H")
                        per_hour.setdefault(hour_key, []).append(file_path)
                        
                        four_hour_slot = file_mtime.hour // 4
                        four_hour_key = f"{file_mtime.strftime('%Y%m%d')}_{four_hour_slot}"
                        per_4hour.setdefault(four_hour_key, []).append(file_path)
                    else:
                        day_key = file_mtime.strftime("%Y%m%d")
                        per_day.setdefault(day_key, []).append(file_path)
                except Exception as e:
                    logger.debug(f"Error processing file {file_path.name}: {e}")
                    continue
            
            for hour_key, files in per_hour.items():
                if files:
                    most_recent = max(files, key=lambda p: p.stat().st_mtime)
                    files_to_keep.add(most_recent)
            
            for four_hour_key, files in per_4hour.items():
                if files:
                    most_recent = max(files, key=lambda p: p.stat().st_mtime)
                    files_to_keep.add(most_recent)
            
            for day_key, files in per_day.items():
                if files:
                    most_recent = max(files, key=lambda p: p.stat().st_mtime)
                    files_to_keep.add(most_recent)
            
            deleted_count = 0
            deleted_size = 0
            for file_path in position_files:
                if file_path not in files_to_keep and file_path != latest_file:
                    try:
                        size = file_path.stat().st_size
                        file_path.unlink()
                        deleted_count += 1
                        deleted_size += size
                    except Exception as e:
                        logger.debug(f"Error deleting old file {file_path.name}: {e}")
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old position files ({deleted_size / 1024:.1f} KB)")
        except Exception as e:
            logger.error(f"Error cleaning up old position files: {e}")

    async def save_ladder_levels(self, position_key: str):
        """Save ladder levels to file"""
        try:
            account_key, symbol, position_side = parse_position_key(position_key)
            ladder_file = self.config.DATA_DIR / f"ladder_levels_{account_key}_{position_side.lower()}.json"
            ladder_data = {}
            for key, entry in self.ladder_levels.items():
                if key.startswith(f"{account_key}:") and key.endswith(f"_{position_side}"):
                    ladder_data[key] = entry
            await atomic_write_json(ladder_file, ladder_data)
            logger.debug(f"Saved {len(ladder_data)} ladder levels")
        except Exception as e:
            logger.error(f"Failed to save ladder levels: {e}")
    
    async def stop(self):
        logger.info(f"[{self.account_key}] Stopping Manager...")
        self.running = False
        for t in self.background_tasks: t.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        await self.save_all_accounts()
        await self.save_aux_data()
        await self.save_state_all()
        if self.api_client:
            await self.api_client.close()
        if self.redis_manager: 
            await self.redis_manager.close()
        logger.info(f"[{self.account_key}] Stopped.")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)

async def main():
    import argparse
    def signal_handler():
        logger.info("Received shutdown signal...")
        stop_event.set()
    parser = argparse.ArgumentParser(description="Tradier Position Manager")
    parser.add_argument(  "--accounts", 
        nargs="+", 
        default=["trc", "trb", "tra"], 
        help="Space-separated list of accounts to sync (e.g. --accounts tra trc)" )
    args = parser.parse_args()
    
    active_accounts = []
    for acc in args.accounts:
        if acc in config.ACCOUNTS:
            active_accounts.append(acc)
        else:
            logger.error(f"Account '{acc}' not defined in config.ACCOUNTS. Skipping.")
    if not active_accounts:
        logger.error("No valid accounts selected. Exiting.")
        return
        
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    logger.info(f"🚀 Starting Position Managers for: {active_accounts}")
    managers = []
    for acc_key in active_accounts:
        try:
            mgr = TradierPositionManager(account_key=acc_key)
            managers.append(mgr)
        except Exception as e:
            logger.error(f"Failed to initialize manager for {acc_key}: {e}")
    if managers:
        await asyncio.gather(*(m.start() for m in managers))
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Stopping all managers...")
            await asyncio.gather(*(m.stop() for m in managers), return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
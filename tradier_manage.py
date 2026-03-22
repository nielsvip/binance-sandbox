# pylint: disable=W,C,R,I
#!/usr/bin/env python3
import asyncio
import datetime as dt
import json
import logging
import os
import pickle
import signal
import sys
import threading
import time
import uuid
from asyncio.queues import QueueEmpty
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from datetime import datetime
from datetime import time as dt_time
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Tuple
import aiofiles
import pandas as pd
import psutil
import redis.asyncio as redis
from dateutil.parser import isoparse

from config_tradier import TradierConfig
from utils import (construct_position_key, current_account,
                   get_simple_redis_manager, load_environment_from_gpg,
                   parse_position_key, pk_is_long, pk_is_short, pk_symbol,
                   record_decision_context, safe_datetime,
                   tradier_action_logger)

load_environment_from_gpg(None)
from tradier_api import TradierAPIClient
from tradier_positions import TradierPosition, TradierPositionManager

_GLOBAL_JSON_CACHE={}
config = TradierConfig()
# BACKTEST_CHANGE_T19-T25: time-of-day zone logic for entry thresholds and sizing
def _get_trade_zone():
    now = datetime.now(timezone(timedelta(hours=-4)))  # ET
    h, m = now.hour, now.minute
    t = h * 60 + m
    if 570 <= t < 630: return "open"    # 9:30-10:30
    if 630 <= t < 870: return "mid"     # 10:30-14:30
    if 870 <= t < 960: return "close"   # 14:30-16:00
    return "closed"
# BACKTEST_CHANGE: k_4h cross (not k_1h) — tradier uses higher TF
_ls_ratio_4h_adj_tradier = {"adj": 0.0, "last_cross_ts": 0}
# BACKTEST_CHANGE_T37: daily loss circuit breaker tracking
_daily_loss_tracker = {"date": "", "realized_pnl": 0.0, "halted": False}
try:
    from typing import Any, Dict, List, Optional, Union

    import orjson
    def default_json_serializer(obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        raise TypeError
    def json_dumps(obj: Any, **kwargs) -> bytes:
        option = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS | orjson.OPT_INDENT_2 # type: ignore # pylint: disable=no-member,c-extension-no-member
        return orjson.dumps(obj, default=default_json_serializer, option=option)  # type: ignore # pylint: disable=no-member,c-extension-no-member
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if not s: return {}
        if isinstance(s, str):
            if not s.strip(): return {}
            s = s.encode('utf-8')
        elif isinstance(s, (bytes, bytearray, memoryview)):
            if not s.strip(): return {}
        return orjson.loads(s)  # type: ignore # pylint: disable=no-member,c-extension-no-member
    JSONDecodeError = orjson.JSONDecodeError  # type: ignore # pylint: disable=no-member,c-extension-no-member
except ImportError:  
    import json
    from typing import Any, Dict, List, Optional, Union
    def default_json_serializer(obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        return str(obj)
    def json_dumps(obj: Any, **kwargs) -> str:
        kwargs.setdefault('indent', 2)
        return json.dumps(obj, default=default_json_serializer, **kwargs)
    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if not s: return {}
        if isinstance(s, (bytes, bytearray, memoryview)):
            s = s.decode('utf-8')
        if not s.strip(): return {}
        return json.loads(s, **kwargs)
    JSONDecodeError = json.JSONDecodeError
_global_file_write_semaphore = asyncio.Semaphore(40)
os.makedirs(config.LOG_DIR, exist_ok=True)
GLOBAL_JSON_CACHE = {}
_GLOBAL_JSON_TS = 0.0
_GLOBAL_JSON_LOCK = threading.Lock()
class InjectAccountFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, 'account_key'):
            val = current_account.get()
            record.account_key = val if val else "SYS"
        return True

class WeatherLogFilter(logging.Filter):
    """tradier_manage_{acct}.log: ONLY clean forecasts. No WAITs, no other accounts."""
    def __init__(self, acct: str):
        super().__init__()
        self.acct = acct.upper()
    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, 'account_key', 'SYS') != self.acct: return False
        if not getattr(record, 'weather_snapshot', False): return False
        msg = record.getMessage()
        # Return False if it's a "Wait" line
        return not ("WAIT" in msg or "⏳" in msg or "🛡️" in msg)

class WaitLogFilter(logging.Filter):
    """tradier_manage_wait_{acct}.log: ONLY throttled/waiting snapshots for this account."""
    def __init__(self, acct: str):
        super().__init__()
        self.acct = acct.upper()
    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, 'account_key', 'SYS') != self.acct: return False
        if not getattr(record, 'weather_snapshot', False): return False
        msg = record.getMessage()
        # Return True ONLY if it's a "Wait" line
        return "WAIT" in msg or "⏳" in msg or "🛡️" in msg

class GeneralLogFilter(logging.Filter):
    """tradier_manage_general_{acct}.log: Actions, Errors, System Logs. NO snapshots."""
    def __init__(self, acct: str):
        super().__init__()
        self.acct = acct.upper()
    def filter(self, record: logging.LogRecord) -> bool:
        rec_acct = getattr(record, 'account_key', 'SYS')
        # Allow if it matches this account OR if it's a general system log (SYS)
        if rec_acct != self.acct and rec_acct != "SYS": return False
        # Exclude snapshots (they go to the other two files)
        return not getattr(record, 'weather_snapshot', False)

class ConsoleFilter(logging.Filter):
    """Stdout: Actions and clean Weather (No waits) for the primary account."""
    def __init__(self, acct: str = None):
        super().__init__()
        self.acct = acct.upper() if acct else None
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if 'ORDER' in msg or '[TRADE]' in msg: return True
        if not getattr(record, 'weather_snapshot', False): return False
        return not ("WAIT" in msg or "⏳" in msg)

# --- 2. LOGGER SETUP ---

def setup_logger_per_account(logger_name: str, base_path: str, target_accounts: List[str]) -> logging.Logger:
    global _logger_setup_done

    logger_obj = logging.getLogger(logger_name)
    if logger_obj.hasHandlers():
        for handler in list(logger_obj.handlers): handler.close()
        logger_obj.handlers.clear()

    logger_obj.setLevel(logging.DEBUG)
    logger_obj.propagate = False
    logger_obj.addFilter(InjectAccountFilter())

    # 1. Console (Stdout) - Filtered to clean weather
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.addFilter(ConsoleFilter(target_accounts[0] if target_accounts else None)) 
    stream_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s'))
    logger_obj.addHandler(stream_handler)

    for acct in target_accounts:
        acct_l = acct.lower()
        
        # --- WEATHER LOG: tradier_manage_trb.log (Clean Forecasts) ---
        w_path = os.path.join(base_path, f"tradier_manage_{acct_l}.log")
        w_h = RotatingFileHandler(w_path, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        w_h.setLevel(logging.INFO)
        w_h.addFilter(WeatherLogFilter(acct))
        w_h.setFormatter(logging.Formatter('[%(asctime)s] %(message)s'))
        logger_obj.addHandler(w_h)

        # --- WAIT LOG: tradier_manage_wait_trb.log (Wait/Hold lines) ---
        wait_path = os.path.join(base_path, f"tradier_manage_wait_{acct_l}.log")
        wait_h = RotatingFileHandler(wait_path, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        wait_h.setLevel(logging.INFO)
        wait_h.addFilter(WaitLogFilter(acct))
        wait_h.setFormatter(logging.Formatter('[%(asctime)s] %(message)s'))
        logger_obj.addHandler(wait_h)

        # --- GENERAL LOG: tradier_manage_general_trb.log (Action/Errors) ---
        g_path = os.path.join(base_path, f"tradier_manage_general_{acct_l}.log")
        g_h = RotatingFileHandler(g_path, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        g_h.setLevel(logging.DEBUG)
        g_h.addFilter(GeneralLogFilter(acct))
        g_h.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%m-%d %H:%M:%S'))
        logger_obj.addHandler(g_h)

    _logger_setup_done = True
    return logger_obj

logger = setup_logger_per_account("tradier_manage", config.LOG_DIR, config.ACCOUNT_KEYS)
account_keys = getattr(config, 'ACCOUNT_KEYS', ['trc'])
wait_logger = logging.getLogger("tradier_manage_wait") # This will be handled by the same setup above or we can define it separately
_log_throttle: Dict[str, float] = {}
async def log_stoch_snapshot(trade_manager, account_key: str, context_label: str, position_key: str, current_price: float, indicators: Dict[str, Any], score: float, sentiment_rank: float, rec: str, reason: str, force: bool = False):
    try:
        # --- 1. Setup & Data Extraction ---
        if score is None: score = 0.0
        # Access logger safely
        main_logger = getattr(trade_manager, 'logger', logging.getLogger(__name__))
        
        now_ts = time.time()
        is_long = position_key.endswith("_LONG")
        
        # Robust Float Extraction
        if not indicators: indicators = {}
        def _g(k, d=50.0): return safe_fetch_float(indicators.get(k, d), d)

        # --- 2. Throttling & Zombie Logic ---
        # Direct access to trade_manager dictionary to ensure GAP calculation works
        last_check_ts = trade_manager.last_monitored_positions.get(position_key, 0)

        # Define Action Keywords
        action_kw = ["BUY", "SELL", "CLOSE", "OPEN", "AUGMENT", "REDUCE", "BLOCKED", "REENTRY"]
        is_action = any(x in rec for x in action_kw)
        
        # Gap Calculation (Time since last visit)
        gap_disp = "INIT"
        is_zombie = False
        
        if last_check_ts > 0:
            gap_sec = now_ts - last_check_ts
            
            # Zombie check (e.g. > 5 mins)
            is_zombie = gap_sec > 300
            
            # Skip high-frequency non-action updates
            if gap_sec < 10.0 and not is_action and not force:
                trade_manager.last_monitored_positions[position_key] = now_ts
                return
            
            if gap_sec < 60.0: gap_disp = f"{gap_sec:.1f}s"
            elif gap_sec < 3600: gap_disp = f"{gap_sec/60:.1f}m"
            else: gap_disp = f"{gap_sec/3600:.1f}h"
            
            if gap_sec > 120: gap_disp = f"💀{gap_disp}"
        else:
            # First time seeing this position, set baseline
            trade_manager.last_monitored_positions[position_key] = now_ts
        
        # UPDATE the timestamp so the NEXT gap is correct
        trade_manager.last_monitored_positions[position_key] = now_ts

        # Throttle logic
        throttle_key = f"{account_key}:{position_key}:{context_label}"
        last_log_time = _log_throttle.get(throttle_key, 0.0)
        # Log if: Action OR Zombie OR Forced OR Time elapsed > 60s
        if not is_action and not is_zombie and not force and (now_ts - last_log_time < 60.0): 
            return
        _log_throttle[throttle_key] = now_ts

        # --- 3. Indicator Processing ---
        k_1m = _g('stoch_k_1m', _g('k_1m')); d_1m = _g('stoch_d_1m', _g('d_1m'))
        k_5m = _g('stoch_k_5m', _g('k_5m')); d_5m = _g('stoch_d_5m', _g('d_5m'))
        k_15m = _g('stoch_k_15m', _g('k_15m')); d_15m = _g('stoch_d_15m', _g('d_15m'))
        
        # Weather Icons
        def get_weather(k, d=None, prev=None):
            if prev is not None: return "☀️" if k > prev else "☁️"
            if d is not None: return "☀️" if k > d else "☁️"
            return "•"
            
        w_str = f"[{get_weather(k_1m, d=d_1m)}{get_weather(k_5m, d=d_5m)}{get_weather(k_15m, d=d_15m)}{get_weather(_g('stoch_k_1h'), d=_g('stoch_d_1h'))}{get_weather(_g('stoch_k_4h'), d=_g('stoch_d_4h'))}{get_weather(_g('stoch_k_D'), d=_g('stoch_d_D'))}]"
        # --- 3. REVISED Age Calculation (Match get_indicators) ---
        ts_raw = indicators.get('timestamp_1m') or indicators.get('timestamp')
        lag_disp = "UNK"
        if ts_raw:
            dt_obj = ts_raw if isinstance(ts_raw, datetime) else safe_datetime(ts_raw)
            if dt_obj:
                true_lag = (datetime.now(timezone.utc) - dt_obj).total_seconds()
                
                if true_lag < 90.0: 
                    lag_disp = f"{true_lag:.0f}s" # 1m is Good
                elif true_lag < 900.0: 
                    lag_disp = f"⚠️{true_lag:.0f}s" # Macro is Good, 1m is Stale
                else: 
                    lag_disp = f"❌{true_lag:.0f}s" # Total Death
                if true_lag > 60.0 and trade_manager:
                    sym = position_key.split(':')[1].split('_')[0]
                    last_clear = getattr(trade_manager, '_last_stale_clear', {}).get(sym, 0)
                    if now_ts - last_clear > 30:
                        trade_manager.get_indicators(sym, use_cache=False)
                        if not hasattr(trade_manager, '_last_stale_clear'): trade_manager._last_stale_clear = {}
                        trade_manager._last_stale_clear[sym] = now_ts
                        logger.warning(f"[{sym}] Indicators stale ({true_lag:.0f}s), forced refresh from Redis.")
        # --- 4. PnL & Position ---
        pos = trade_manager.position_manager.get_position(position_key)
        positionAmt = abs(float(getattr(pos, 'positionAmt', 0))) if pos else 0.0
        upnl_disp = "        "
        if positionAmt > 0 and current_price > 0:
            avg_entry = float(getattr(pos, 'entry_price', 0.0) or getattr(pos, 'mark_price', 0.0))
            if avg_entry > 0:
                pct = ((current_price - avg_entry) / avg_entry) * 100.0 if is_long else ((avg_entry - current_price) / avg_entry) * 100.0
                upnl_disp = f"{pct:>+7.2f}%"

        def _fmt(k, d): return f"{int(k)}/{int(d)}"
        i1 = _fmt(k_1m, d_1m)
        i5 = _fmt(k_5m, d_5m)
        i15 = _fmt(k_15m, d_15m)
        
        sc_val = int(score)
        sc_disp = f"Sc:{sc_val:<3}" if sc_val != 0 else "       "
        
        # Use 0market_sentiment_local for Sn value as requested
        sn_val = int(_g('0market_sentiment_local', 0))
        sn_disp = f"Sn:{sn_val:<3}"
        
        # Dollar value of position instead of positionAmt
        posAmt_str = f"${int(positionAmt * current_price):<8}" if positionAmt != 0 else "     "

        rec_clean = rec.replace(position_key, "").replace("trb:", "").replace("tra:", "").replace("trc:", "").strip()
        for char in["🚀", "🟢", "🔴", "💥", "❌", "⏳", "▫️", "🛡️"]:
            rec_clean = rec_clean.replace(char, "")
        rec_clean = rec_clean.strip()[:13]

        # Determine Single Icon
        icon = "⏳" # Default
        if "WEAK" in rec or "WEAK" in reason: 
            icon = "▫️ "
        elif "HOLD" in rec:
            icon = "🛡️"
        elif (("BUY" in rec and is_long) or ("SELL" in rec and not is_long)) or "OPEN" in rec or "AUGMENT" in rec: 
            icon = "🚀" if sc_val >= 28 else "🟢"
        elif "CLOSE" in rec or "REDUCE" in rec or "EMERGENCY" in rec: 
            icon = "💥" if sc_val <= -7 else "🔴"
        elif "BLOCKED" in rec:
            icon = "✋"

        prefix_icon = "💀" if is_zombie else ("🔵" if positionAmt == 0 else "🔴")
        
        # Add Sc: to reason start
        reason_str = (f"Sc:{sc_val} {reason}" if reason else f"Sc:{sc_val}")[:50]

        # --- 6. Final Log Line ---
        log_line = (
            f"{icon}  {rec_clean:<14} {prefix_icon} {position_key:<19} {w_str} | "
            f"{current_price:<8.4f} | "
            f"1m:{i1:<10} 5m:{i5:<10} 15m:{i15:<10} | "
            f"{sn_disp} | "
            f"{upnl_disp} | Age:{lag_disp:<7} | Gap:{gap_disp:<6} | {reason_str:<35} | {posAmt_str} "
        )
        
        # Tag as weather_snapshot so filters route it correctly:
        # WeatherLogFilter → tradier_manage_{acct}.log (non-WAIT)
        # WaitLogFilter    → tradier_manage_wait_{acct}.log (WAIT/⏳)
        # GeneralLogFilter → tradier_manage_general_{acct}.log (excluded)
        main_logger.info(log_line, extra={  "weather_snapshot": True,  "account_key": account_key.upper()})

    except Exception as e:
        main_logger.info(f"Error logging snapshot: {e}")

def safe_parse_ts(ts_val):
    """Unified fast parser for ISO strings, Unix timestamps, or datetimes."""
    if not ts_val: return None
    try:
        if isinstance(ts_val, datetime):
            return ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
        if isinstance(ts_val, (int, float)):
            # Detect ms (Tradier) vs seconds
            val = ts_val / 1000.0 if ts_val > 1e12 else ts_val
            return datetime.fromtimestamp(val, tz=timezone.utc)
        if isinstance(ts_val, str):
            # Fast check for ISO format
            dt = isoparse(ts_val)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception: return None
    return None

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
        # Log the problematic value so we can see what caused zeros
        try:
            logger.debug(f"[safe_float] failed to parse {repr(x)} -> {e}")
        except Exception:
            pass
        return default

def parse_ts(ts):
    if not ts: return None
    try:
        if isinstance(ts, (int, float)):
            val = ts / 1000.0 if ts > 1e11 else ts
            return datetime.fromtimestamp(val, tz=timezone.utc)
        if isinstance(ts, str):
            t = isoparse(ts)
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return ts if isinstance(ts, datetime) else None
    except Exception: return None

def minutes_since(timestamp_obj, now=None):
    if not timestamp_obj:
        return 999999  # Very large number if no timestamp
    if now is None:
        now = datetime.now(timezone.utc)#.replace(microsecond=0)
    try:
        if isinstance(timestamp_obj, str):
            dt = isoparse(timestamp_obj)
        elif isinstance(timestamp_obj, datetime):
            dt = timestamp_obj
        else:
            return 999999  # Invalid type
        if not isinstance(dt, datetime):
            return 999999  # Failed to parse
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() / 60.0
    except Exception:
        return 999999  
def is_regular_trading_hours():
    """Checks if current time is Mon-Fri, 9:30 AM - 4:00 PM ET (handles DST)."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except ImportError:
        import pytz
        now_et = datetime.now(pytz.timezone("America/New_York"))
    now_est = now_et
    if now_est.weekday() >= 5: return False
    market_open = dt_time(9, 30)
    market_close = dt_time(16, 0)
    current_time = now_est.time()
    return market_open <= current_time <= market_close

def calculate_gain(position_side, current_price, entry_price):
    if entry_price <= 0: return 0.0
    if position_side == 'LONG': 
        return ((current_price - entry_price) / entry_price) * 100
    else: 
        return ((entry_price - current_price) / entry_price) * 100 

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
async def load_json_safe(file_path: Union[str, Path], account_key: str = None) -> dict:
    file_path = Path(file_path)
    # No logging on entry
    
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
            # --- EXTREME RECOVERY MODE ---
            data = {}
            recovery_success = False
            
            # 1. Attempt Truncation Repair (Fix "Extra data" error)
            try:
                txt = content.decode('utf-8', errors='ignore').strip()
                # Find the last closing brace that makes valid JSON
                # Simple heuristic: rfind '}'
                end_idx = txt.rfind('}')
                if end_idx != -1:
                    fixed_txt = txt[:end_idx+1]
                    data = orjson.loads(fixed_txt)
                    recovery_success = True
                    # Silently overwrite fixed content back to disk
                    async with aiofiles.open(file_path, "wb") as f:
                        await f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
            except Exception:
                pass

            # 2. Fallback to Backup
            if not recovery_success:
                backup_path = file_path.with_suffix(file_path.suffix + ".bak")
                if backup_path.exists():
                    try:
                        async with aiofiles.open(backup_path, "rb") as f:
                            bk_content = await f.read()
                        data = safe_json_loads(bk_content)
                        recovery_success = True
                        # Restore backup to main file
                        async with aiofiles.open(file_path, "wb") as f:
                            await f.write(bk_content)
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

        # Timestamp conversion 
        data = _convert_timestamp_strings(data) 

        # # Self-heal: If we recovered it, save the good version back
        # # This part was missing in the manual copy-paste
        # try:
        #     if 'recovery_success' in locals() and recovery_success:
        #         asyncio.create_task(_heal_corrupted_file(file_path, data))
        # except Exception: pass
        
        return data

class TrackingSet(set):
    """Custom set that tracks when items are added"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._added_at = {}
    
    def add(self, item):
        super().add(item)
        self._added_at[item] = time.time()
    
    def discard(self, item):
        super().discard(item)
        self._added_at.pop(item, None)
    
    def remove(self, item):
        super().remove(item)
        self._added_at.pop(item, None)
    
    def clear(self):
        super().clear()
        self._added_at.clear()
    
    def get_added_at(self, item):
        return self._added_at.get(item, 0)


async def periodic_leaderboards(order_queue, trade_manager):
    while getattr(trade_manager, "running", False):
        try:
            await trade_manager.load_leaderboards()
            await asyncio.sleep(900)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[periodic_leaderboards] {e}")
            await asyncio.sleep(10)

async def periodic_evaluate_reentry_loop(trade_manager):
    """
    Scans for positions that were REDUCED and need RE-ENTRY.
    """
    logger.info("[TASK] Re-entry Scanner Started")
    while getattr(trade_manager, "running", False):
        try:
            await asyncio.sleep(45) # Check every 45s
            
            re_keys_by_acct = defaultdict(list)
            
            # Iterate ALL positions in the manager
            if trade_manager.position_manager:
                for pk, pos in trade_manager.position_manager.positions.items():
                    if not pos: continue
                    
                    # Check Re-entry Criteria
                    was_reduced = getattr(pos, 'was_reduced', False)
                    last_red_time = getattr(pos, 'last_reduction_time', None)
                    
                    if was_reduced and last_red_time:
                        # Extract Account Key
                        parts = pk.split(':')
                        if len(parts) > 0:
                            acc = parts[0]
                            re_keys_by_acct[acc].append(pk)

            # Dispatch to Monitor
            for acc, keys in re_keys_by_acct.items():
                if keys:
                    logger.info(f"[{acc}] ♻️ Evaluating {len(keys)} positions for Re-Entry...")
                    # await trade_manager.strategy.evaluate_reentry(symbol,position, indicators)
                    await monitor_entries( trade_manager.order_queue,  trade_manager,   acc,   keys,   event_type="reentry_scan",    is_priority_add=True,   force=True  )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[REENTRY_LOOP] Error: {e}",  exc_info=True )
            await asyncio.sleep(10)

def safe_fetch_float(value, default=0.0):
    try:
        if value is None: return default
        if isinstance(value, (float, int)): return float(value)
        return float(str(value).replace(',', '').strip())
    except Exception:
        return default

async def monitor_entries(order_queue: "OrderQueue", trade_manager, account_key: str, position_keys=None, event_type=None, is_priority_add: bool = False, force: bool = False):
    current_account.set(account_key)
    now_ts = time.time()
    
    try:
        # 1. Market Hours Gate (Removed hard return to allow Weather Monitoring off-hours)
        if not is_regular_trading_hours() and not force:
            if now_ts % 60 < 2: 
                logger.info(f"[{account_key}] 💤 Market Closed. Monitoring only.")

        # 2. Queue Management (Standard Deque Logic)
        if account_key not in trade_manager.account_position_queues:
            trade_manager.account_position_queues[account_key] = {'queue': deque(), 'set': set()}
        
        q_data = trade_manager.account_position_queues[account_key]
        
        if position_keys:
            # Normalize keys
            incoming = [position_keys] if isinstance(position_keys, str) else position_keys
            # De-duplicate
            unique_incoming = list(dict.fromkeys(incoming))
            
            for k in unique_incoming:
                if k not in q_data['set']:
                    if is_priority_add: q_data['queue'].appendleft(k)
                    else: q_data['queue'].append(k)
                    q_data['set'].add(k)

        if not q_data['queue']: 
            return

        # 3. Process Batch (Up to 100 at a time)
        batch = []
        for _ in range(min(len(q_data['queue']), 100)):
            batch.append(q_data['queue'].popleft())
            q_data['set'].discard(batch[-1])

        # 4. The Gates
        for position_key in batch:
            _, symbol, position_side = parse_position_key(position_key)
            if symbol.upper() in trade_manager.blacklist:
                if config.VERBOSE: 
                    logger.debug(f"[{account_key}] 🛑 BLACKLIST {symbol}: Ignored (External Management).")
                continue

            # --- CHECK 1: PROCESSING LOCK (Prevent double-processing) ---
            # If it's already running in another task, skip it to prevent race conditions.
            # We rarely force this unless we are debugging.
            if position_key in trade_manager.processing_keys and not force:
                # logger.debug(f"[{account_key}] SKIP {symbol}: Already processing.")
                continue

            # --- CHECK 2: COOLDOWN (Don't spam) ---
            # force=True BYPASSES THIS (e.g. Monitor Loop / Pulse)
            cd = float(trade_manager.recently_processed_signals.get(position_key, 0.0))
            if not force and (now_ts < cd):
                logger.debug(f"[{account_key}] SKIP {symbol}: Cooldown active ({int(cd-now_ts)}s).")
                continue

            # --- CHECK 3: DEDUPLICATION (Don't double order) ---
            # force=True BYPASSES THIS
            async with trade_manager.dedupe_lock:
                last_order = trade_manager.order_deduplication.get(position_key, 0)
                if not force and (now_ts - last_order < 90.0):
                    logger.info(f"[{account_key}] SKIP {symbol}: Order recently queued (<90s).")
                    continue

            # --- CHECK 4: LEADERBOARD/SYMBOL FILTER (The "Is this allowed?" Gate) ---
            # This is where trades often die silently.
            
            # Use centralized check
            is_allowed = trade_manager.is_symbol_tradeable(symbol, account_key, position_side)
            if force: is_allowed = True

            if not is_allowed:
                # LOG TO GENERAL: This tells you why a symbol isn't being traded!
                if config.VERBOSE: 
                    logger.info(f"[{account_key}] ⛔ FILTER BLOCK {symbol} {position_side}: Not in approved list.")
                continue


            trade_manager.processing_keys.add(position_key)
            try:
                result = await process_position(account_key, position_key, order_queue, trade_manager, event_type=event_type, force=force)
                if result == "PROCESSED":
                    trade_manager.position_last_processed[position_key] = time.time()
            finally:
                trade_manager.processing_keys.discard(position_key)
    except Exception as e:
        logger.error(f"[{account_key}] 💥 CRASH in monitor_entries: {e}",  exc_info=True )

async def process_position(account_key: str, position_key: str, order_queue: "OrderQueue", trade_manager, event_type=None, force: bool = False):
    current_account.set(account_key)
    try:
        ak, symbol, position_side = parse_position_key(position_key)
        if ak != account_key: return
        if force:
            logger.info(f"[{account_key}] 🔎 MONITORING {position_key} (Force=True)")
        now_ts = time.time()
        last_mon = trade_manager.last_monitored_positions.get(position_key, 0.0)
        position = trade_manager.position_manager.get_position(position_key)
        has_position = position and abs(float(getattr(position, 'positionAmt', 0))) > 0
        
        # --- CHECK OPPOSING POSITION ---
        opposing_side = "SHORT" if position_side == "LONG" else "LONG"
        from utils import construct_position_key
        opposing_key = construct_position_key(account_key, symbol, opposing_side)
        opposing_pos = trade_manager.position_manager.get_position(opposing_key)
        has_opposing_pos = opposing_pos and abs(float(getattr(opposing_pos, 'positionAmt', 0))) > 0
        current_price, ts = await trade_manager.get_current_price(symbol)
        current_price = safe_fetch_float(current_price, 0.0)
        macro_fresh, freshness_reason, m1_fresh, indicators_raw = await trade_manager.is_data_fresh(symbol, position_key)
        if not macro_fresh:
            # INDICATORS ARE DEAD (>900s)
            if has_position:
                # Switch to Price-Only Emergency Mode
                should_kill, kill_reason = trade_manager.strategy.evaluate_price_only_exit(position, current_price)
                if should_kill:
                    logger.warning(f"🚨 [EMERGENCY] Stale data exit for {symbol}: {kill_reason}")
                    await queue_trade_action(order_queue, trade_manager, position_key, "CLOSE", kill_reason, 100.0)
                    return "PROCESSED_EMERGENCY_EXIT"
                return "STALE_INDICATORS_HELD"
            else:
                return "STALE_ABSOLUTE_NO_POS"
        if not force and (now_ts - last_mon < 30):
            return "THROTTLED"
        is_stale = not macro_fresh
        m1_stale = not m1_fresh
        
        if not indicators_raw:
            if force: logger.info(f"[{account_key}] SKIP {symbol}: No indicators found.")
            return "NO_DATA"

        i = trade_manager.strategy.parse_market_data(indicators_raw)
        if symbol.upper() in trade_manager.blacklist and not has_position: return "BLOCKED_BLACKLIST"
        if (i.get('stoch_k_1m') == 50.0 and i.get('stoch_k_5m') == 50.0):
             if force: logger.info(f"[{account_key}] SKIP {symbol}: Flatline Data (50/50).")
             return "DATA_ERROR"

        # 3. DETERMINE PRICE
        current_price,ts = await trade_manager.get_current_price(symbol)
        position = trade_manager.position_manager.get_position(position_key)
        if current_price is None: current_price=position.mark_price if position else None
        if current_price is None: current_price=indicators_raw.get('current_price')

        current_price = safe_fetch_float(current_price, 0.0)

        if current_price <= 0 and position:
            current_price = safe_fetch_float(getattr(position, 'mark_price', 0), 0.0)
        
        if current_price <= 0:
            if force: logger.info(f"[{account_key}] SKIP {symbol}: Price is Zero.")
            return "NO_PRICE"
        is_long = (position_side == "LONG")
        was_reduced = getattr(position, 'was_reduced', False)
        last_red_time = getattr(position, 'last_reduction_time', None)
        market_context = await trade_manager.get_market_context(symbol)
        sentiment_rank = float(indicators_raw.get('0sentiment_rank') or i.get('0sentiment_rank') or 999)

        if not trade_manager.is_symbol_tradeable(symbol, account_key, position_side):
            return "SYMBOL NOT TRADEABLE"
            
        tech_score, _, strategy_reason = trade_manager.strategy.calculate_signal_score( account_key, symbol, is_long, current_price, i, position, is_exit=False  )
        
        # 5. DECISION LOGIC VARIABLES
        log_rec = "WAIT"
        
        # Priority: Show the score/reason. Append STALE ONLY if it actually blocks something (is_stale).
        log_reason = f"Sc:{int(tech_score)} {strategy_reason}"[:30]
        if is_stale: log_reason = f"STALE {log_reason}"
        
        market_open = is_regular_trading_hours()
        action_taken = False

        
        

        # =========================================================
        # LOGIC BRANCH A: HELD POSITION (EXIT / AUGMENT)
        # =========================================================
        if was_reduced and last_red_time:
            re_action, re_reason, re_conf, re_qty = await trade_manager.strategy.evaluate_reentry(symbol, position, indicators_raw)
            
            if re_action in ["OPEN", "REENTRY_OPEN", "AUGMENT"]:
                # if m1_stale:
                #     logger.warning(f"[{account_key}] 🛑 BLOCK REENTRY {symbol}: 1m Data Stale (>120s)")
                # elif is_stale:
                if is_stale:
                    logger.warning(f"[{account_key}] 🛑 BLOCK REENTRY {symbol}: All Data Stale ({freshness_reason})")
                else:# market_open or force:
                    log_rec = "♻️REENTRY"
                    log_reason = re_reason
                    logger.info(f"[{account_key}] ♻️ EXEC REENTRY {symbol}: {re_reason} Qty: {re_qty}")
                    
                    # Use "AUGMENT" action for existing positions, "OPEN" if it was fully closed
                    # But since this block is for "held positions" (mostly), use AUGMENT context if size > 0
                    exec_action = "AUGMENT" if has_position else "OPEN"
                    
                    await queue_trade_action(
                        order_queue, trade_manager, position_key, exec_action, 
                        re_reason, re_conf, override_qty=re_qty
                    )
                    action_taken = True

        if has_position:
            opened_at = position.opened_at
            if opened_at:
                if isinstance(opened_at, str):
                    try:
                        import pandas as pd
                        opened_at = pd.to_datetime(opened_at).timestamp()
                    except Exception:
                        opened_at = time.time()
                elif hasattr(opened_at, 'timestamp'): 
                    opened_at = opened_at.timestamp()
                elif isinstance(opened_at, (int, float)):
                    pass
                else:
                    opened_at = time.time()
            now = time.time()
            position_age_minutes = (now - opened_at) / 60.0 if opened_at else 0
            in_grace_period = position_age_minutes < 10.0
            should_exit, exit_reason, exit_qty = await trade_manager.strategy.evaluate_stop(
                symbol, position, indicators_raw, market_context, in_grace_period )
            
            if should_exit:
                log_rec = "💥CLOSE"
                log_reason = exit_reason
                logger.info(f"[{account_key}] 🛑 REVIEWING EXIT {symbol}: {exit_reason} Qty: {exit_qty}")
                await queue_trade_action( order_queue, trade_manager, position_key, "CLOSE",  exit_reason, 100.0, override_qty=exit_qty )
                action_taken = True
            
            # --- 3. EVALUATE AUGMENT ---
            elif position_age_minutes > 6.0:
                # Add a cooldown check for consecutive augmentations (15 mins)
                aug_at = getattr(position, 'last_augmentation_time', None)
                aug_dt = safe_datetime(aug_at) if aug_at else None
                aug_age = (time.time() - aug_dt.timestamp()) / 60.0 if aug_dt else 999.0
                
                if aug_age < 15.0:
                    log_rec = "HOLD"
                    log_reason = f"AugCD:{15.0-aug_age:.1f}m"
                elif m1_stale or is_stale:
                    log_rec = "HOLD"
                    log_reason = f"Stale ({freshness_reason})"
                else:
                    should_aug, aug_reason, aug_conf, aug_qty = await trade_manager.strategy.evaluate_augment(
                        symbol, position, indicators_raw, market_context  )
                    if should_aug:
                        log_rec = "🚀AUGMENT"
                        log_reason = aug_reason
                        logger.info(f"[{account_key}] 🟢 REVIEWING AUGMENT {symbol}: {aug_reason} Qty: {aug_qty}")
                        if market_open:
                            await queue_trade_action( order_queue, trade_manager, position_key, "AUGMENT",   aug_reason, aug_conf, override_qty=aug_qty )
                            action_taken = True
                    else:
                        log_rec = "HOLD"
                        log_reason = f"PnL:{getattr(position, 'gain', 0):.2f}% Sc:{int(tech_score)}"
            else:
                log_rec = "HOLD"
                log_reason = f"Grace:{10.0-position_age_minutes:.1f}m Sc:{int(tech_score)}"

        # =========================================================
        # LOGIC BRANCH B: NO POSITION (ENTRY)
        # =========================================================
        else:
            if has_opposing_pos:
                log_rec = "WAIT"
                log_reason = f"Opposing pos open"
            elif (m1_stale or is_stale) and not force:
                log_rec = "WAIT"
                log_reason = f"Stale ({freshness_reason})"

            else:
                # --- Reopen cooldown: don't re-enter within 5 min of a full close ---
                # DC breakout entries (price still below/above channel) get immediate re-entry
                # Stoch-based entries wait 5 min to avoid noise
                last_exit_ts = trade_manager.last_exit_times.get(symbol, 0)
                secs_since_close = time.time() - last_exit_ts
                # Check if price is currently outside any DC channel (DC breakout in progress)
                indicators_raw_check = trade_manager.get_indicators(symbol) or {}
                _cp = float(indicators_raw_check.get('current_price') or 0)
                _dcl5 = float(indicators_raw_check.get('dc_low_5m') or 0)
                _dch5 = float(indicators_raw_check.get('dc_high_5m') or 0)
                _is_dc_state = (_cp > 0 and (
                    (_dcl5 > 0 and _cp < _dcl5 * 0.999) or
                    (_dch5 > 0 and _cp > _dch5 * 1.001)
                ))
                reopen_wait_s = 0 if _is_dc_state else 5 * 60  # 0s for DC breakout, 5 min for stoch
                if last_exit_ts > 0 and secs_since_close < reopen_wait_s:
                    log_rec = "COOLDOWN"
                    log_reason = f"ReopenCD {(reopen_wait_s - secs_since_close)/60:.0f}m"
                    if force: logger.info(f"[{account_key}] ⏳ {symbol} REOPEN COOLDOWN {log_reason}")
                    return "COOLDOWN"
                # --- 4. EVALUATE OPEN ---
                action_type, reason, conf, qty = await trade_manager.strategy.evaluate_open(account_key, symbol, position_side, indicators_raw, market_context)                
                if action_type == "OPEN":
                    # Determine intended direction from strategy return
                    signal_is_long = "LONG" in reason.upper()
                    signal_is_short = "SHORT" in reason.upper()
                    
                    # Validate against Position Key (Are we processing the right key for this signal?)
                    # If we are processing AAPL_LONG key, but strategy says SHORT, we ignore.
                    if position_side == "LONG" and signal_is_short:
                        log_rec = "WAIT"
                        log_reason = "Signal Mismatch (Short)"
                    elif position_side == "SHORT" and signal_is_long:
                        log_rec = "WAIT"
                        log_reason = "Signal Mismatch (Long)"
                    else:
                        # --- BALANCER LOGIC START ---
                        global_sentiment = float(i.get('sentiment', 0))
                        trade_manager.update_sentiment_history(global_sentiment)
                        
                        blocked_by_balancer = False
                        block_reason = ""
                        
                        breadth = (trade_manager.calculate_unified_market_ratio() - 0.5) * 2.0  # -1=full bear, +1=full bull
                        current_balance = trade_manager.get_current_portfolio_balance()  # sync, no await
                        current_long_pct = current_balance.get('long_pct', 0.5)
                        target = 0.5 + (breadth * 0.4)

                        if position_side == "LONG":
                             if breadth < -0.4 and current_long_pct > target and not i.get('0is_top_sentiment'):
                                blocked_by_balancer = True; block_reason = f"Index Bearish ({breadth:.2f})"
                        elif position_side == "SHORT":
                             if breadth > 0.4 and current_long_pct < target and not i.get('0is_bottom_sentiment'):
                                blocked_by_balancer = True; block_reason = f"Index Bullish ({breadth:.2f})"
                        # --- BALANCER LOGIC END ---

                        if blocked_by_balancer:
                            log_rec = "BLOCKED"
                            log_reason = block_reason
                            logger.info(f"[{account_key}] BLOCKED {symbol}: {block_reason}")
                        else:
                            log_rec = "LONG BUY" if position_side == "LONG" else "SHORT SELL"
                            log_reason = reason
                            logger.info(f"[{account_key}] EXAMINING {position_side} entry {symbol}: {reason} Score={tech_score}")
                            # Heavy artillery: pullback-reexpansion setup boosts qty
                            ha_setup, ha_score, ha_dir, ha_detail = trade_manager.check_pullback_reexpansion(symbol, indicators_raw)
                            if ha_setup and ha_dir == position_side:
                                ha_mult = 3.0 if ha_score == 4 else 2.0
                                qty = int(qty * ha_mult)
                                log_rec = "🔫 HEAVY_ART" if position_side == "LONG" else "🔫 HEAVY_ART_S"
                                logger.info(f"[{account_key}] 🔫 HEAVY ARTILLERY {symbol} ×{ha_mult:.0f} score={ha_score}: {ha_detail}")
                            sw_ok, sw_reason = trade_manager.is_swing_entry_allowed(position_side, float(qty) * current_price)
                            if not sw_ok:
                                log_rec = "BUDGET"; log_reason = sw_reason
                                logger.info(f"[{account_key}] 💰 SWING BUDGET BLOCKED {symbol}: {sw_reason}")
                            elif market_open:
                                await queue_trade_action(order_queue, trade_manager, position_key, action_type, reason, conf, override_qty=qty)
                                action_taken = True
            
                else:
                    # Strategy returned NO_ACTION
                    log_rec = "WAIT"
                    log_reason = f"Sc:{int(tech_score)} {strategy_reason}"[:20]

        if action_taken:
             _entry = float(getattr(position, 'entry_price', 0.0) or 0.0) if position else 0.0
             _gain = calculate_gain(position_side, current_price, _entry) if _entry > 0 else 0.0
             _tdetails = {"qty": qty, "price": current_price, "value": round(float(qty) * current_price, 2), "entry_price": _entry, "gain_pct": round(_gain, 2), "position_amt": position.positionAmt, "action_type": action_type, "side": "BUY" if (action_type in ("AUGMENT", "OPEN") and is_long) or (action_type in ("REDUCE", "CLOSE") and not is_long) else "SELL"}
             await record_decision_context(trade_manager.redis_manager, account_key, position_key, log_rec, log_reason, i, tech_score, market_context, trade_details=_tdetails)

        # 6. UNIFIED VISUAL LOGGING
        # Pass RAW indicators to preserve timestamp for "Age" display
        await log_stoch_snapshot(
            trade_manager, account_key, event_type or "DECISION", position_key, 
            current_price, indicators_raw, 
            tech_score, sentiment_rank, 
            log_rec, log_reason, force=(action_taken or force)  )
        trade_manager.last_monitored_positions[position_key] = time.time()
        return "PROCESSED"

    except Exception as e:
        logger.error(f"[{account_key}] 💥 CRASH in process_position for {symbol}: {e}")
        return "ERROR"


class OrderQueue:
    def __init__(self, trade_manager, max_concurrent_orders=500):
        self.trade_manager = trade_manager
        self.max_concurrent_orders = max_concurrent_orders
        self._orders = asyncio.Queue()
        self.last_executed_time = {}
    
    async def add_order(self, order: dict) -> tuple[bool, str]:
        account_key = order.get('account_key', '')
        current_account.set(account_key)
        position_key = construct_position_key(account_key, order.get('symbol', ''), order.get('position_side', ''))

        now_ts = time.time()
        async with self.trade_manager.dedupe_lock:
            if position_key in self.trade_manager.order_deduplication:
                last_time = float(self.trade_manager.order_deduplication[position_key])
                if (now_ts - last_time) < 90.0:
                    return False, "DUPLICATE"
            self.trade_manager.order_deduplication[position_key] = now_ts
        try:
            await asyncio.wait_for(self._orders.put(order), timeout=2.0)
            return True, "SUCCESS"
        except asyncio.TimeoutError:
            logger.error(f"[add_order] Timeout adding order: {position_key}")
            return False, "TIMEOUT"
        except Exception as e:
            logger.error(f"[add_order] Error: {e}")
            return False, f"EXCEPTION_{str(e)[:50]}"
    
    async def process_orders(self):
        logger.info("[OrderQueue.process_orders]  Task started")
        while True:
            try:
                order = await self._orders.get()
                await self.handle_order(order)
                self._orders.task_done()
            except Exception as e:
                logger.error(f"[process_orders] Error: {e}",  exc_info=True )
                await asyncio.sleep(1)

    async def handle_order(self, order: dict):
        try:
            account_key = order.get('account_key', '')
            current_account.set(account_key)
            symbol = order.get('symbol', '')
            side = order.get('side', '')
            quantity = int(order.get('quantity', 0))
            action = order.get('action', 'OPEN')
            reason = order.get('reason', '')
            price = order.get('current_price', 0)
            order_id = order.get('order_id','None')
            if not is_regular_trading_hours():
                logger.debug(f"[HANDLE_ORDER] Not in trading hours, skipping order: {symbol} {action} {side} qty={quantity}")
                return
            logger.info(f"[ORDER_DEBUG] Processing order: {symbol} {action} {side} qty={quantity}")
            position_key = construct_position_key(account_key, symbol, order.get('position_side', ''))            
            result = await self.trade_manager.execute_trade_action(account_key=account_key,position_key=position_key,symbol=symbol,quantity=quantity,current_price=price,side=side,position_side=order.get('position_side', ''),unique_id=order.get('unique_id', f"{account_key}:{symbol}_{action}_{int(time.time())}"),is_full_close=(action == "CLOSE"),action=action,reason=reason,override_qty=order.get('override_qty')  )
            if result == "SUCCESS":
                status = "SUBMITTED"
                order_id = order.get('order_id', 'None')
                logger.info(f"[ORDER_RESULT] {symbol} {action} {side} qty={quantity} -> {status} (ID: {order_id})")
            else:
                status = str(result) if result else "FAILED"
                order_id = "N/A"

            # Calculate gain% and $ for trade log
            _pos = self.trade_manager.position_manager.get_position(position_key) if hasattr(self, 'trade_manager') else None
            _entry = float(getattr(_pos, 'entry_price', 0)) if _pos else 0.0
            _pos_side = order.get('position_side', '')
            _gain_pct = ((price - _entry) / _entry * 100) if _pos_side == 'LONG' and _entry > 0 and price > 0 else ((_entry - price) / _entry * 100) if _pos_side == 'SHORT' and _entry > 0 and price > 0 else 0.0
            _gain_dollar = quantity * price * _gain_pct / 100 if _gain_pct else 0.0
            _pos_val = float(getattr(_pos, 'positionAmt', 0)) * price if _pos and price > 0 else 0.0
            log_msg = (
                f"[TRADE] {symbol:<5} {action:<7} {side:<4} | "
                f"Qty: {quantity:<4} @ ${price:<7.2f} | "
                f"Status: {status:<10} | order_id: {order_id} | "
                f"Reason: {reason} | Gain: {_gain_pct:+.2f}% ${_gain_dollar:+.1f} | Entry: ${_entry:.2f} PosVal: ${_pos_val:.0f} | "
            )

            if "GHOST_CLEARED_COOLDOWN" in status or "REDIS_EXECUTION_LOCK" in status:
                pass
            elif status == "SUBMITTED":
                logger.info(log_msg)
                tradier_action_logger.info(f"{position_key}: {log_msg}")
                self.last_executed_time[(position_key, side)] = time.time()
            else:
                logger.warning(log_msg)
                async with self.trade_manager.dedupe_lock:
                    self.trade_manager.order_deduplication.pop(position_key, None)

        except Exception as e:
            logger.error(f"[handle_order] Critical Error: {e}",  exc_info=True )


async def queue_trade_action(order_queue: OrderQueue, trade_manager, position_key: str, action: str, reason: str, conviction: float = 50.0, override_qty: float = None):
    try:
        account_key, _, _ = parse_position_key(position_key)
        current_account.set(account_key)
        if not is_regular_trading_hours(): 
            logger.debug(f"[queue_trade_action] not in trading hours")
            return
        if position_key.count(":") != 1:
            logger.error(f"[queue_trade_action] !! CORRUPTED position_key with {position_key.count(':')} colons: {position_key}")
            from utils import clean_position_key, orjson_default
            position_key = clean_position_key(position_key)
            if position_key.count(":") != 1:
                return False
        position = trade_manager.position_manager.get_position(position_key)
        if position is not None:
            positionAmt = position.positionAmt
            try: positionAmt = abs(float(positionAmt))
            except Exception: redis_abs_qty = 0.0
            if action in ["CLOSE", "REDUCE", "AUGMENT"] and positionAmt <= 0:
                logger.warning(f"[queue_trade_action] !! Blocked {action}: {position_key} has no position in Redis (positionAmt={positionAmt})")
                return False
        parts = position_key.split(':')
        if len(parts) != 2:
            return False
        account_key,symbol,position_side=parse_position_key(position_key)
        current_price = 0.0
        if position:
            current_price = getattr(position, 'current_price', 0) or getattr(position, 'mark_price', 0) or 0
        if current_price <= 0:
            current_price,ts = await trade_manager.get_current_price(symbol)
        if current_price <= 0:
            logger.warning(f"[queue_trade_action] No price for {symbol}")
            return False
        
        if action == "OPEN":
            # BACKTEST_CHANGE_T37: daily loss circuit breaker
            if _daily_loss_tracker.get("halted", False):
                logger.warning(f"[DAILY_LOSS_HALT] Blocking OPEN {position_key}: daily loss limit breached")
                return False
            # BACKTEST_CHANGE_T35: max concurrent positions
            _max_conc = getattr(config, 'MAX_CONCURRENT_POSITIONS', 999)
            if _max_conc < 999 and trade_manager.position_manager:
                _oc = sum(1 for _pk, _p in trade_manager.position_manager.positions.items() if abs(float(getattr(_p, 'positionAmt', 0))) > 0)
                if _oc >= _max_conc:
                    logger.warning(f"[MAX_POS_BLOCK] Blocking OPEN {position_key}: {_oc} >= {_max_conc} max concurrent")
                    return False
            # BACKTEST_CHANGE_T36: L/S ratio enforcement
            if getattr(config, 'LS_RATIO_ENFORCE_TRADIER', False) and trade_manager.position_manager:
                _lc2 = sum(1 for _pk in trade_manager.position_manager.positions if _pk.endswith("_LONG") and abs(float(getattr(trade_manager.position_manager.positions[_pk], 'positionAmt', 0))) > 0)
                _sc2 = sum(1 for _pk in trade_manager.position_manager.positions if _pk.endswith("_SHORT") and abs(float(getattr(trade_manager.position_manager.positions[_pk], 'positionAmt', 0))) > 0)
                _adj2 = _ls_ratio_4h_adj_tradier.get("adj", 0.0)  # BACKTEST_CHANGE: k_4h cross (not k_1h) — tradier uses higher TF
                _ls_min2 = max(0.30, getattr(config, 'LS_RATIO_MIN_TRADIER', 0.50) + _adj2)
                _ls_max2 = min(3.00, getattr(config, 'LS_RATIO_MAX_TRADIER', 2.00) + _adj2)
                _ratio2 = _lc2 / max(_sc2, 1)
                if position_side == "LONG" and _ratio2 > _ls_max2:
                    logger.warning(f"[LS_RATIO_BLOCK] {position_key}: L/S ratio {_ratio2:.2f} > max {_ls_max2:.2f}, blocking LONG open")
                    return False
                if position_side == "SHORT" and _ratio2 < _ls_min2:
                    logger.warning(f"[LS_RATIO_BLOCK] {position_key}: L/S ratio {_ratio2:.2f} < min {_ls_min2:.2f}, blocking SHORT open")
                    return False
            side = "BUY" if position_side == "LONG" else "SELL"
        elif action in ["CLOSE", "REDUCE"]:
            side = "SELL" if position_side == "LONG" else "BUY"
        elif action == "AUGMENT":
            side = "BUY" if position_side == "LONG" else "SELL"
        else:
            side = "BUY"

        # logger.info(f"[queue_trade_action] 📋 ORDER LOGIC: action={action} position_side={position_side} -> side={side} symbol={symbol}")
        
        is_recent_reentry = False
        final_override_qty = None
        if action == "REENTER":
            exit_time = None
            if position:
                exit_time = getattr(position, 'last_reduction_time', None)
            if not exit_time:
                last_exit_time = getattr(trade_manager, 'last_exit_times', {}).get(symbol, 0)
                if last_exit_time > 0:
                    exit_time = datetime.fromtimestamp(last_exit_time, tz=timezone.utc)
            if exit_time:
                hours_since_exit = (datetime.now(timezone.utc) - exit_time).total_seconds() / 3600.0
                if hours_since_exit < 2.0:
                    is_recent_reentry = True
                    last_reduction_amount = getattr(position, 'last_reduction_amount', None) if position else None
                    if last_reduction_amount and float(last_reduction_amount) > 0:
                        final_override_qty = float(last_reduction_amount)
                        logger.info(f"[queue_trade_action] Recent reentry (<2h): Using position.last_reduction_amount={final_override_qty} as override_qty")
        if action == "OPEN":
            quantity = override_qty if override_qty else await trade_manager.calculate_position_size(symbol, current_price, account_key=account_key)
        elif action in ["REDUCE", "CLOSE"]:
            if position:
                position_qty = abs(getattr(position, 'quantity', 0))
                quantity = override_qty if override_qty else (position_qty * 0.5)
            else:
                return False
        elif action in ["AUGMENT", "REENTER"]:
            if position:
                position_qty = abs(getattr(position, 'positionAmt', 0))
                quantity = override_qty if override_qty else await trade_manager.calculate_position_size(symbol, current_price, account_key=account_key)
            else:
                quantity = override_qty if override_qty else await trade_manager.calculate_position_size(symbol, current_price, account_key=account_key)
        else:
            return False
        if quantity <= 0:
            return False
        if config.SYMBOL_PERF_ENABLED and action in ('OPEN', 'AUGMENT', 'REENTRY', 'QUICK_OPEN', 'QUICK_AUGMENT'):
            try:
                from ez_symbol_performance import get_performance_multiplier
                _perf_mult = get_performance_multiplier(symbol, default=1.0)
                quantity = max(1, int(quantity * (0.7 + _perf_mult * 0.3)))
            except Exception:
                pass
        max_order_value = config.MAX_ORDER_VALUE
        order_value = quantity * current_price
        if order_value > max_order_value:
            quantity = int(max_order_value / current_price)
            if config.VERBOSE: logger.debug(f"[queue_trade_action] {symbol} quantity adjusted to {quantity} to respect MAX_ORDER_VALUE={max_order_value}")
        if quantity <= 0:
            return False
        
        final_reason = reason
        if not is_recent_reentry and override_qty and override_qty > 0:
            if "_qty_" not in reason:
                final_reason = f"{reason}_qty_{override_qty}"
        order = {
            'account_key': account_key,
            'symbol': symbol,
            'position_side': position_side,
            'side': side,
            'quantity': quantity,
            'action': action, 
            'reason': final_reason,
            'current_price': current_price,
            'priority': conviction,
            'order_id': f"{position_key}_{action}_{int(time.time())}",
            'override_qty': override_qty   }
        success, msg = await order_queue.add_order(order)
        logger.info(f"[queue_trade_action] Queued {symbol} {action}: success={success}, msg={msg}")
        return "SUCCESS" 
    except Exception as e:
        logger.error(f"[queue_trade_action] Error: {e}",  exc_info=True )
        return False

async def process_symbols_periodically(order_queue: OrderQueue, trade_manager, account_key: str):
    current_account.set(account_key)
    logger.info(f"🚀 [TASK] Trading loop started for {account_key}")
    OPEN_INTERVAL = 30      # open positions re-evaluated every 30s
    CANDIDATE_INTERVAL = 90  # non-open candidates every 90s
    last_candidate_scan = 0.0

    while trade_manager.running:
        try:
            current_account.set(account_key)
            now = time.time()
            await trade_manager.reload_account_symbols()
            longs = getattr(trade_manager, f"symbols_long_{account_key}", [])
            shorts = getattr(trade_manager, f"symbols_short_{account_key}", [])

            # Open positions — always included every 30s
            open_keys = [
                pk for pk, pos in trade_manager.position_manager.positions.items()
                if pk.startswith(f"{account_key}:") and abs(getattr(pos, 'positionAmt', getattr(pos, 'quantity', 0))) > 0
            ]

            # Candidate keys (non-open) — every 90s only
            candidate_keys = []
            if now - last_candidate_scan >= CANDIDATE_INTERVAL:
                all_candidates = (
                    [f"{account_key}:{s}_LONG" for s in longs] +
                    [f"{account_key}:{s}_SHORT" for s in shorts]
                )
                candidate_keys = [k for k in all_candidates if k not in open_keys]
                last_candidate_scan = now

            all_keys = open_keys + candidate_keys

            if not all_keys:
                if not longs and not shorts:
                    logger.warning(f"[{account_key}] ⚠️ SYMBOL LISTS EMPTY! Check your json lists or data feed.")
                await asyncio.sleep(OPEN_INTERVAL)
                continue

            logger.info(f"[{account_key}] 🔄 SCAN: {len(open_keys)} open (every 30s) + {len(candidate_keys)} candidates (every 90s) = {len(all_keys)} total")
            await asyncio.gather(*[
                process_position(account_key, pk, order_queue, trade_manager, event_type="periodic_loop", force=True)
                for pk in all_keys
            ], return_exceptions=True)

            await asyncio.sleep(OPEN_INTERVAL)

        except Exception as e:
            logger.error(f"[{account_key}] 💥 CRASH in loop: {e}", exc_info=True)
            await asyncio.sleep(10)

            
async def continuous_queue_processor(order_queue: OrderQueue, trade_manager, account_key: str):
    current_account.set(account_key)
    logger.info(f"[continuous_queue_processor] Task started for {account_key}", extra={'account_key': account_key})
    
    while trade_manager.running:
        try:
            current_account.set(account_key)
            await asyncio.sleep(5)
            
            if account_key in trade_manager.account_position_queues:
                queue_data = trade_manager.account_position_queues[account_key]
                if queue_data['queue']:
                    position_keys = list(queue_data['queue'])[:10]
                    if position_keys:
                        await monitor_entries(order_queue, trade_manager, account_key, position_keys, event_type="queue_processor", is_priority_add=False, force=False)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[continuous_queue_processor] Error: {e}",  exc_info=True, extra={'account_key': account_key})
            await asyncio.sleep(5)

async def periodic_override_check(trade_manager, account_key):
    current_account.set(account_key)
    while getattr(trade_manager, "running", False):
        try:
            current_account.set(account_key)
            await asyncio.sleep(120)
            await trade_manager.load_leaderboards()
            positions = trade_manager.position_manager.get_positions_by_account(account_key)
            
            for position_key, position in positions.items():
                try:
                    position_amt = abs(float(getattr(position, 'positionAmt', 0)))
                    if position_amt <= 0: continue
                    
                    symbol = getattr(position, 'symbol', '')
                    if not symbol: continue
                    # Note: blacklisted symbols CAN be reduced (execute_trade_action allows REDUCE/CLOSE through)
                    current_price, ts = await trade_manager.get_current_price(symbol)
                    # if current_price <= 0: continue
                    
                    indicators = trade_manager.get_indicators(symbol)
                    
                    logger.debug(f"{symbol} Indicators Age: {indicators.get('timestamp')}")


                    if not indicators: continue
                    
                    i = trade_manager.strategy.parse_market_data(indicators)
                    position_value = position_amt * current_price
                    gain = calculate_gain(
                        getattr(position, 'position_side', 'LONG'),
                        current_price,
                        getattr(position, 'entry_price', current_price) )
                    market_context = await trade_manager.get_market_context(symbol)
                    market_bias = market_context.get('bias', 0.0)
                    is_long = getattr(position, 'position_side', 'LONG') == 'LONG'
                    
                    if (is_long and market_bias < -0.3) or (not is_long and market_bias > 0.3):
                        if gain < -0.5:
                            reduce_qty = max(1, int(position_amt * 0.3)) 
                            reason = f"Market_Against_Position_Bias_{market_bias:.2f}_Loss_{gain:.1f}%"
                            logger.warning(f"[OVERRIDE_CHECK] {position_key}: Market against position, reducing")
                            await queue_trade_action(
                                trade_manager.order_queue, trade_manager, position_key,
                                "REDUCE", reason, 70.0, override_qty=reduce_qty  )
                    
                    if is_long and i.get('stoch_k_5m', 50) > 85 and i.get('rsi_5m', 50) > 75 and i.get('stoch_k_5m', 50) < i.get('stoch_d_5m', 50) :
                        if gain > 2.0:
                            reduce_qty = max(1, int(position_amt * 0.25))
                            reason = f"Overbought_Take_Profit_Gain_{gain:.1f}%"
                            logger.info(f"[OVERRIDE_CHECK] {position_key}: Overbought, taking profits")
                            await queue_trade_action(
                                trade_manager.order_queue, trade_manager, position_key,
                                "REDUCE", reason, 75.0, override_qty=reduce_qty    )
                    
                    elif not is_long and i.get('stoch_k_5m', 50) < 15 and i.get('rsi_5m', 50) < 25 and i.get('stoch_k_5m', 50) > i.get('stoch_d_5m', 50) :
                        if gain > 2.0:
                            reduce_qty = max(1, int(position_amt * 0.25))
                            reason = f"Oversold_Take_Profit_Gain_{gain:.1f}%"
                            logger.info(f"[OVERRIDE_CHECK] {position_key}: Oversold, taking profits")
                            await queue_trade_action(
                                trade_manager.order_queue, trade_manager, position_key,
                                "REDUCE", reason, 75.0, override_qty=reduce_qty  )
                    
                except Exception as e:
                    logger.debug(f"[perio dic_override_check] Error processing {position_key}: {e}")
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[period ic_override_check] {e}")
            await asyncio.sleep(10)

async def monitor_stale_augmentations(trade_manager, account_key, order_queue):
    """Monitor positions that were augmented but have become stale (no update for 2 hours)"""
    current_account.set(account_key)
    while getattr(trade_manager, "running", False):
        try:
            current_account.set(account_key)
            await asyncio.sleep(180)

            now = datetime.now(timezone.utc)
            stale_positions = []

            # Check all positions
            positions = trade_manager.get_positions_by_account(account_key)

            for position_key, position in positions.items():               
                try:
                    # Skip if no position quantity
                    position_amt = abs(float(getattr(position, 'positionAmt', 0)))
                    if position_amt <= 0:
                        continue
                    
                    # Check last augmentation time
                    last_aug_time = getattr(position, 'last_augmentation_time', None)
                    if not last_aug_time:
                        continue
                    
                    # Parse timestamp
                    try:
                        if isinstance(last_aug_time, str):
                            last_aug_time = isoparse(last_aug_time)
                        
                        # Check if more than 2 hours since last augmentation
                        if (now - last_aug_time).total_seconds() > 7200:  # 2 hours
                            stale_positions.append(position_key)
                    except Exception:
                        continue
                        
                except Exception:
                    continue
            
            # Process stale positions
            if stale_positions:
                logger.info(f"[STALE_AUG] Found {len(stale_positions)} positions with stale augmentations")
                
                # Re-evaluate these positions
                await monitor_entries( order_queue, trade_manager, 'trb', stale_positions[:25],
                    event_type="stale_aug", is_priority_add=True, force=True )
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[moni tor_stale_augmentations] {e}")
            await asyncio.sleep(10)
                    
async def redis_listener_signals(trade_manager):
    while trade_manager.running:
        try:
            await asyncio.sleep(2)
            if not trade_manager.redis_manager: continue
            try:
                data = await trade_manager.redis_manager.get("tradier_signals")
                if data and isinstance(data, dict):
                    account_key = data.get('account_key', 'tra')
                    position_key = data.get('position_key', '')
                    if position_key:
                        from utils import clean_position_key, orjson_default
                        if position_key.count(":") > 1:
                            position_key = clean_position_key(position_key)
                        if account_key in trade_manager.account_position_queues:
                            queue_data = trade_manager.account_position_queues[account_key]
                            _, symbol_clean, position_side = parse_position_key(position_key)
                            if position_side == "SHORT" and symbol_clean.upper() in trade_manager.non_shortable_symbols:
                                continue
                            if position_key not in queue_data['set']:
                                queue_data['queue'].append(position_key)
                                queue_data['set'].add(position_key)
            except Exception:
                pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[redis_listener_signals] Error: {e}")
            await asyncio.sleep(5)

class PositionReader:
    def __init__(self, config):
        self.config = config
        self.redis_client = None
        self.positions: Dict[str, TradierPosition] = {} 
        self.ladder_levels = {}


    async def connect(self):
        """Fix: Added strict socket timeouts to prevent startup hang."""
        try:
            self.redis_client = redis.Redis( host=getattr(config, 'REDIS_HOST', 'localhost'),
                port=getattr(config, 'REDIS_PORT', 6379),
                decode_responses=False, socket_connect_timeout=1,  socket_timeout=1     )
            # Ping with timeout to ensure we don't hang
            await asyncio.wait_for(self.redis_client.ping(), timeout=1.0)
            return True
        except Exception:
            self.redis_client = None
            return False

    def _hydrate_position(self, pos_data: dict) -> Any:
        try:
            from dataclasses import fields

            from tradier_positions import TradierPosition
            valid_fields = {f.name for f in fields(TradierPosition)}
            clean_data = {k: v for k, v in pos_data.items() if k in valid_fields}
            if 'entry_price' not in clean_data:
                clean_data['entry_price'] = float(pos_data.get('avg_fill_price', 0.0) or 0.0)
            if 'positionAmt' not in clean_data:
                clean_data['positionAmt'] = float(pos_data.get('quantity', 0.0) or 0.0)
            if 'symbol' not in clean_data: clean_data['symbol'] = 'UNKNOWN'
            if 'position_side' not in clean_data: clean_data['position_side'] = 'LONG'
            if 'entry_price' not in clean_data: clean_data['entry_price'] = 0.0
            if 'positionAmt' not in clean_data: clean_data['positionAmt'] = 0.0
            return TradierPosition(**clean_data)
        except Exception as e:
            logger.error(f"[Reader] Hydration error: {e}")
            return None

    def set_stop_loss(self, symbol, position_side, stop_loss): pass
    async def save_all_positions(self):
        from dataclasses import asdict
        accounts_data: Dict[str, Dict[str, dict]] = {}
        for pk, pos in self.positions.items():
            acc = pk.split(":")[0]
            if acc not in accounts_data:
                accounts_data[acc] = {"long": {}, "short": {}}
            side_key = "short" if pk.endswith("_SHORT") else "long"
            accounts_data[acc][side_key][pk] = pos.to_dict() if hasattr(pos, 'to_dict') else (asdict(pos) if hasattr(pos, '__dataclass_fields__') else pos.__dict__)
        for acc, sides in accounts_data.items():
            acc_conf = config.get_account_config(acc)
            acc_dir = acc_conf['account_dir'] if acc_conf and 'account_dir' in acc_conf else config.BASE_PATH / acc
            if not os.path.exists(acc_dir):
                os.makedirs(acc_dir, exist_ok=True)
            for side_name, positions_dict in sides.items():
                fpath = os.path.join(acc_dir, f"{side_name}_positions.json")
                existing = {}
                if os.path.exists(fpath):
                    try:
                        existing = await load_json_safe(fpath)
                        if not isinstance(existing, dict):
                            existing = {}
                    except Exception:
                        existing = {}
                existing.update(positions_dict)
                try:
                    async with aiofiles.open(fpath, "wb") as f:
                        await f.write(orjson.dumps(existing, option=orjson.OPT_INDENT_2, default=default_json_serializer))
                    logger.info(f"[SAVE] {acc}/{side_name}_positions.json: {len(existing)} positions written")
                except Exception as e:
                    logger.error(f"[SAVE] Failed to write {fpath}: {e}")
    async def save_all(self): await self.save_all_positions()
    def deteriorate_max_gain(self, account_key, position): return getattr(position, 'max_gain', 0.0)
    def deteriorate_max_quantity(self, account_key, position, current_price): return getattr(position, 'max_quantity', 0.0)

    async def sync(self, account_keys: List[str]):
        if not self.redis_client:
            for acc in account_keys:
                await self._load_from_disk(acc)
            return

        for acc in account_keys:
            data_found = False
            try:
                key = f"tradier:positions:{acc}" 
                data = await self.redis_client.get(key)
                
                if data:
                    wrapper = pickle.loads(data)
                    raw_pos_dict = wrapper.get('positions', {})
                    
                    if raw_pos_dict:
                        data_found = True
                        for pk, p_obj in raw_pos_dict.items():
                            if isinstance(p_obj, dict):
                                pos_instance = self._hydrate_position(p_obj)
                                if pos_instance:
                                    self.positions[pk] = pos_instance
                            else:
                                self.positions[pk] = p_obj
            except Exception as e:
                logger.warning(f"[Reader] Redis sync failed for {acc}: {e}")
            
            if not data_found:
                await self._load_from_disk(acc)

    async def _load_from_disk(self, account_key: str):
        try:
            acc_conf = config.get_account_config(account_key)
            if acc_conf and 'account_dir' in acc_conf:
                acc_dir = acc_conf['account_dir']
            else:
                acc_dir = config.BASE_PATH / account_key

            if not os.path.exists(acc_dir):
                return

            files_to_read = [
                os.path.join(acc_dir, "long_positions.json"),
                os.path.join(acc_dir, "short_positions.json")
            ]

            for fpath in files_to_read:
                if not os.path.exists(fpath):
                    continue
                
                data = await load_json_safe(fpath)
                
                if not isinstance(data, dict):
                    continue

                for symbol, pos_data in data.items():
                    side = pos_data.get('position_side', 'LONG') 
                    if 'short' in fpath and side == 'LONG': side = 'SHORT'
                    
                    symbol_clean = pos_data.get('symbol', symbol).upper()
                    from utils import construct_position_key, orjson_default
                    pk = construct_position_key(account_key, symbol_clean, side)

                    try:
                        if 'symbol' not in pos_data: pos_data['symbol'] = symbol_clean
                        if 'position_side' not in pos_data: pos_data['position_side'] = side
                        
                        pos_instance = self._hydrate_position(pos_data)
                        if pos_instance:
                            self.positions[pk] = pos_instance
                    except Exception as e:
                        logger.error(f"[Reader] Failed to hydrate position {pk} from disk: {e}")

        except Exception as e:
            logger.error(f"[Reader] Disk Load failed for {account_key}: {e}")

    def get_position(self, position_key: str):
        return self.positions.get(position_key)

    def get_positions_by_account(self, account_key: str) -> Dict[str, Any]:
        return {k: v for k, v in self.positions.items() if k.startswith(f"{account_key}:")}

    async def close(self):
        if self.redis_client: await self.redis_client.aclose()

class StockStrategy:
    def __init__(self, config, trade_manager=None):
        self.config = config # Max for Re-entry/High Conviction
        self.trade_manager = trade_manager
        self.non_shortable_symbols = config.NON_SHORTABLE
        self.exceptions = self.trade_manager.exceptions
        self.limit_normal_order = self.trade_manager.limit_normal_order
        self.limit_exception_order = self.trade_manager.limit_exception_order
        self.limit_total_pos = self.trade_manager.limit_total_pos
        self._cvar_cache: dict = {}  # symbol -> (cvar_pct, computed_at_ts)

    # ------------------------------------------------------------------
    # CVaR 5% PRE-ENTRY FILTER (Quantitativo research)
    # Computes Conditional Value at Risk from daily klines.
    # Returns the average of the worst 5% of daily returns (as a %).
    # e.g. -3.9% for AAPL = "on a bad day it loses ~3.9% on average"
    # Filter threshold: -6.0% (skip stocks that are -6% or worse in tail risk)
    # Cache TTL: 4 hours (stale but not wrong — CVaR doesn't change intraday)
    # ------------------------------------------------------------------
    def _compute_cvar_5pct(self, symbol: str) -> float:
        """Read daily klines and return CVaR 5% as a negative percentage, e.g. -3.9."""
        try:
            import json as _json
            import time as _time
            from pathlib import Path as _Path
            klines_path = self.config.KLINES_CACHE_DIR / f"{symbol}_D.json"
            if not klines_path.exists():
                return -999.0  # No data → treat as unsafe (skip)
            data = _json.loads(klines_path.read_text())
            bars = data if isinstance(data, list) else data.get('bars', [])
            closes = []
            for b in bars:
                c = b.get('close') or b.get('c')
                if c: closes.append(float(c))
            if len(closes) < 60:
                return -999.0  # Need at least 60 days of history
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            returns.sort()
            n5 = max(1, int(0.05 * len(returns)))
            cvar = sum(returns[:n5]) / n5 * 100.0
            return round(cvar, 2)
        except Exception:
            return -999.0

    def _is_cvar_safe(self, symbol: str, threshold: float = -6.0) -> tuple:
        """
        Returns (is_safe, cvar_value) — safe means CVaR > threshold.
        Caches for 4 hours (TTL=14400s). Missing data treated as unsafe.
        """
        import time as _time
        now = _time.time()
        TTL = 14400.0  # 4 hours
        if symbol in self._cvar_cache:
            cvar, ts = self._cvar_cache[symbol]
            if now - ts < TTL:
                return (cvar > threshold), cvar
        cvar = self._compute_cvar_5pct(symbol)
        self._cvar_cache[symbol] = (cvar, now)
        return (cvar > threshold), cvar

    def _sf(self, val, default=0.0):
        try:
            return float(val) if val is not None else default
        except (ValueError, TypeError):
            return default

    def _minutes_since(self, dt_val):
        if not dt_val: return 9999.0
        try:
            if isinstance(dt_val, str):
                dt_val = isoparse(dt_val)
            if dt_val.tzinfo is None:
                dt_val = dt_val.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt_val).total_seconds() / 60.0
        except Exception:  return 9999.0

    def is_same_direction(self, side: str, position_side: str) -> bool:
        """Check if order side matches position direction"""
        if position_side == "LONG":
            return side.upper() == "BUY"
        elif position_side == "SHORT":
            return side.upper() in ["SELL", "SELL_SHORT"]
        return False

    def calculate_signal_score(self, account_key: str, symbol: str, is_long: bool, current_price: float, i: dict, position: Any, is_exit: bool = False) -> tuple[float, str, str]:
        now_ts = time.time()
        score = 0.0
        reasons =[]
        longs = getattr(self.trade_manager, f"symbols_long_{account_key}",[])
        shorts = getattr(self.trade_manager, f"symbols_short_{account_key}",[])
        has_position = position is not None and abs(float(getattr(position, 'positionAmt', 0))) > 0
        if not is_exit:
            if not self.trade_manager.is_symbol_tradeable(symbol, account_key, 'LONG' if is_long else 'SHORT'):
                # logger.warning(f"[{account_key}] ⚠️⚠️ SYMBOL {symbol} NOT TRADEABLE (Not in Discovery List)")
                return 0.0, "WAIT", "SYMBOL NOT TRADEABLE"
        
        if not is_regular_trading_hours(): 
            return 0.0, "WAIT", "Outside Market Hours"
        def g(k, default=0.0): return float(i.get(k, default))
        k_1m, d_1m, k_1m_prev = g('stoch_k_1m', 50), g('stoch_d_1m', 50), g('stoch_k_1m_prev', 50)
        k_5m, d_5m, k_5m_prev = g('stoch_k_5m', 50), g('stoch_d_5m', 50), g('stoch_k_5m_prev', 50)
        k_15m, d_15m = g('stoch_k_15m', 50), g('stoch_d_15m', 50)
        k_1h, d_1h = g('stoch_k_1h', 50), g('stoch_d_1h', 50)
        k_4h, d_4h = g('stoch_k_4h', 50), g('stoch_d_4h', 50)
        k_D, d_D = g('stoch_k_D', 50), g('stoch_d_D', 50)

        dc_high_15m, dc_low_15m = g('dc_high_15m'), g('dc_low_15m')
        dc_high_1h, dc_low_1h = g('dc_high_1h'), g('dc_low_1h')
        dc_high_4h, dc_low_4h = g('dc_high_4h'), g('dc_low_4h')
        dc_high_D, dc_low_D = g('dc_high_D'), g('dc_low_D')

        dc_high_15m_ant, dc_low_15m_ant = g('dc_high_15m_ant', dc_high_15m), g('dc_low_15m_ant', dc_low_15m)
        dc_high_1h_ant, dc_low_1h_ant = g('dc_high_1h_ant', dc_high_1h), g('dc_low_1h_ant', dc_low_1h)
        dc_high_4h_ant, dc_low_4h_ant = g('dc_high_4h_ant', dc_high_4h), g('dc_low_4h_ant', dc_low_4h)
        dc_high_D_ant, dc_low_D_ant = g('dc_high_D_ant', dc_high_D), g('dc_low_D_ant', dc_low_D)
        m1_ts_raw = i.get('timestamp_1m')
        m1_ts = now_ts
        try:
            if isinstance(m1_ts_raw, str):
                from dateutil.parser import isoparse
                dt = isoparse(m1_ts_raw)
                from datetime import timezone
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                m1_ts = dt.timestamp()
            elif hasattr(m1_ts_raw, 'timestamp'):
                m1_ts = m1_ts_raw.timestamp()
            elif m1_ts_raw is not None:
                m1_ts = float(m1_ts_raw)
        except Exception:
            pass
        true_lag = now_ts - m1_ts
        m1_is_fresh = (true_lag < 60.0) 
        if true_lag <= 0: true_lag = 999
        k_1mco = k_1m > d_1m and k_1m_prev <= d_1m and k_1m < 30 and m1_is_fresh
        k_1mcu = k_1m < d_1m and k_1m_prev >= d_1m and k_1m > 70 and m1_is_fresh
        k_5mco = k_5m > d_5m and k_5m_prev <= d_5m and k_5m < 35 and m1_is_fresh
        k_5mcu = k_5m < d_5m and k_5m_prev >= d_5m and k_5m > 65 and m1_is_fresh

        # --- Multi-TF DC Breakout Entry Gate ---
        # Break of DC low → SHORT; break of DC high → LONG
        # Higher TF = bigger size tier. Tight stops because stoch is typically extended.
        # Crossunder/crossover flags fire on the bar where price crosses the channel boundary.
        # Crossunder/crossover: fires only on the exact bar of the break
        dc_low_x_5m  = bool(i.get('dc_low_crossunder_5m',  False))
        dc_low_x_15m = bool(i.get('dc_low_crossunder_15m', False))
        dc_low_x_1h  = bool(i.get('dc_low_crossunder_1h',  False))
        dc_low_x_4h  = bool(i.get('dc_low_crossunder_4h',  False))
        dc_high_x_5m  = bool(i.get('dc_high_crossover_5m',  False))
        dc_high_x_15m = bool(i.get('dc_high_crossover_15m', False))
        dc_high_x_1h  = bool(i.get('dc_high_crossover_1h',  False))
        dc_high_x_4h  = bool(i.get('dc_high_crossover_4h',  False))

        # Current-state: price IS below/above the channel right now (breakout already in progress)
        # Use the tightest (5m) as baseline; higher TFs only if price is clearly outside them
        _dcl5  = g('dc_low_5m');  _dcl15 = g('dc_low_15m');  _dcl1h = g('dc_low_1h');  _dcl4h = g('dc_low_4h')
        _dch5  = g('dc_high_5m'); _dch15 = g('dc_high_15m'); _dch1h = g('dc_high_1h'); _dch4h = g('dc_high_4h')
        # Require at least 0.1% below/above channel to avoid noise at the boundary
        _min_buf = 0.0005  # Marathon winner: buffer=0.0005
        dc_below_5m  = current_price > 0 and _dcl5  > 0 and current_price < _dcl5  * (1 - _min_buf)
        dc_below_15m = current_price > 0 and _dcl15 > 0 and current_price < _dcl15 * (1 - _min_buf)
        dc_below_1h  = current_price > 0 and _dcl1h > 0 and current_price < _dcl1h * (1 - _min_buf)
        dc_below_4h  = current_price > 0 and _dcl4h > 0 and current_price < _dcl4h * (1 - _min_buf)
        dc_above_5m  = current_price > 0 and _dch5  > 0 and current_price > _dch5  * (1 + _min_buf)
        dc_above_15m = current_price > 0 and _dch15 > 0 and current_price > _dch15 * (1 + _min_buf)
        dc_above_1h  = current_price > 0 and _dch1h > 0 and current_price > _dch1h * (1 + _min_buf)
        dc_above_4h  = current_price > 0 and _dch4h > 0 and current_price > _dch4h * (1 + _min_buf)

        # Merge: triggered = crossunder THIS bar OR currently below (already broken)
        dc_low_x_5m  = dc_low_x_5m  or dc_below_5m
        dc_low_x_15m = dc_low_x_15m or dc_below_15m
        dc_low_x_1h  = dc_low_x_1h  or dc_below_1h
        dc_low_x_4h  = dc_low_x_4h  or dc_below_4h
        dc_high_x_5m  = dc_high_x_5m  or dc_above_5m
        dc_high_x_15m = dc_high_x_15m or dc_above_15m
        dc_high_x_1h  = dc_high_x_1h  or dc_above_1h
        dc_high_x_4h  = dc_high_x_4h  or dc_above_4h

        # Also allow stoch-based entry as secondary (existing logic preserved)
        stoch_long  = (k_5m < 35 and k_5m > d_5m) or (k_5m < 20) or k_5mco
        stoch_short = (k_5m > 65 and k_5m < d_5m) or (k_5m > 80) or k_5mcu

        # Determine DC tier (highest TF that fired wins; 4h > 1h > 15m > 5m)
        dc_size_mult = 1.0  # default
        dc_stop_price = 0.0
        dc_trigger_tf = None
        if is_long:
            dc_triggered = dc_high_x_5m or dc_high_x_15m or dc_high_x_1h or dc_high_x_4h
            if dc_high_x_4h:
                dc_size_mult = 3.0; dc_stop_price = g('dc_basis_4h'); dc_trigger_tf = "DC4H"
            elif dc_high_x_1h:
                dc_size_mult = 2.0; dc_stop_price = g('dc_basis_1h'); dc_trigger_tf = "DC1H"
            elif dc_high_x_15m:
                dc_size_mult = 1.5; dc_stop_price = g('dc_basis_15m'); dc_trigger_tf = "DC15M"
            elif dc_high_x_5m:
                dc_size_mult = 1.0; dc_stop_price = g('dc_basis_5m', g('dc_low_5m')); dc_trigger_tf = "DC5M"
        else:
            dc_triggered = dc_low_x_5m or dc_low_x_15m or dc_low_x_1h or dc_low_x_4h
            if dc_low_x_4h:
                dc_size_mult = 3.0; dc_stop_price = g('dc_basis_4h'); dc_trigger_tf = "DC4H"
            elif dc_low_x_1h:
                dc_size_mult = 2.0; dc_stop_price = g('dc_basis_1h'); dc_trigger_tf = "DC1H"
            elif dc_low_x_15m:
                dc_size_mult = 1.5; dc_stop_price = g('dc_basis_15m'); dc_trigger_tf = "DC15M"
            elif dc_low_x_5m:
                dc_size_mult = 1.0; dc_stop_price = g('dc_basis_5m', g('dc_high_5m')); dc_trigger_tf = "DC5M"

        good_entry = dc_triggered or (stoch_long if is_long else stoch_short)
        if not good_entry:
            return 0.0, "WAIT", f"NO_{'LONG' if is_long else 'SHORT'}_SETUP"

        if dc_trigger_tf:
            reasons.append(dc_trigger_tf)
            # Pass stop price through for the execute layer to use
            i['_dc_stop_price'] = dc_stop_price
            i['_dc_size_mult']  = dc_size_mult

        sent_rank = g('0sentiment_rank', 999)
        sent_score = g('0market_sentiment_score', 0)
        sent_local = g('0market_sentiment_local', 0)
        sent_class = i.get('0sentiment_classification', 'NEUTRAL')
        sent_points = 0
        if is_long:
            if sent_rank <= 20: sent_points += 4; reasons.append("Rank_Top20")
            elif sent_rank <= 50: sent_points += 2
            if sent_local > 20: sent_points += 2
            if sent_local > sent_score + 10: sent_points += 2; reasons.append("Rel_Strength")
            if "BULLISH" in sent_class: sent_points += 2
        else:
            if sent_rank >= 180: sent_points += 4; reasons.append("Rank_Bot20")
            elif sent_rank >= 150: sent_points += 2
            if sent_local < -20: sent_points += 2
            if sent_local < sent_score - 10: sent_points += 2; reasons.append("Rel_Weakness")
            if "BEARISH" in sent_class: sent_points += 2
        score += sent_points
        trend_points = 0

        if is_long:
            if dc_high_D >= dc_high_D_ant: trend_points += 2
            if dc_low_D > dc_low_D_ant: trend_points += 3; reasons.append("D_Trend_Up")

            # 4H: Immediate Trend Slope
            if dc_high_4h > dc_high_4h_ant: trend_points += 3; reasons.append("4h_Expanding")
            elif dc_low_4h > dc_low_4h_ant: trend_points += 2

            # 1H: Local Trend Support
            if dc_low_1h >= dc_low_1h_ant: trend_points += 2
            if current_price > g('ema_200_1h', 0): trend_points += 2

        else: # SHORT
            # Daily: Lower Lows or Lower Highs
            if dc_low_D <= dc_low_D_ant: trend_points += 2
            if dc_high_D < dc_high_D_ant: trend_points += 3; reasons.append("D_Trend_Down")

            # 4H: Immediate Trend Slope
            if dc_low_4h < dc_low_4h_ant: trend_points += 3; reasons.append("4h_Expanding_Down")
            elif dc_high_4h < dc_high_4h_ant: trend_points += 2

            # 1H: Local Trend Resistance
            if dc_high_1h <= dc_high_1h_ant: trend_points += 2
            if current_price < g('ema_200_1h', 999999): trend_points += 2

        score += trend_points

        # --- 3. PULLBACK/SETUP LAYER (The Wave) ---
        # "Buy Low, Sell High" within the trend.
        setup_points = 0
        
        if not is_exit:
            dc_low_15m = g('dc_low_15m')
            dc_high_15m = g('dc_high_15m')
            dc_low_5m = g('dc_low_5m')
            dc_high_5m = g('dc_high_5m')

            if is_long:
                if dc_low_15m > 0 and (current_price - dc_low_15m) / dc_low_15m < 0.015:
                    setup_points += 5; reasons.append("Bounce_15m_Low")
                elif dc_low_5m > 0 and (current_price - dc_low_5m) / dc_low_5m < 0.008:
                    setup_points += 4; reasons.append("Bounce_5m_Low")

                # 4H Stoch: If < 50, we have room. If > 80, we are chasing.
                if k_D > 95: setup_points -= 5
                elif k_D < 50 and k_D > d_D: setup_points += 5  

                if k_4h < 50: 
                    setup_points += 5; reasons.append("4h_Deep_Value")
                elif k_4h < 80: 
                    setup_points += 1
                if k_4h < 20 and k_4h > d_4h: 
                    setup_points += 4 
                elif k_4h < 50 and k_4h > d_4h: 
                    setup_points += 2  
                                     
                else: 
                    setup_points -= 3 # Chasing top
                
                # 1H Stoch: Ideally turning up
                if k_1h < 40 and k_1h > g('stoch_k_1h_prev'): 
                    setup_points += 3; reasons.append("1h_Turn_Up")
                elif k_1h < 80 and k_1h > d_1h:
                    setup_points += 2
                    
        
            else: # SHORT
                if dc_high_15m > 0 and (dc_high_15m - current_price) / dc_high_15m < 0.015:
                    setup_points += 5; reasons.append("Bounce_15m_High")
                elif dc_high_5m > 0 and (dc_high_5m - current_price) / dc_high_5m < 0.008:
                    setup_points += 4; reasons.append("Bounce_5m_High")

                if k_D < 5: setup_points -= 5
                if k_D < 50 and k_D > d_D: setup_points += 5  
                if k_4h > 50: 
                    setup_points += 5; reasons.append("4h_Premium_Short")
                elif k_4h > 20: 
                    setup_points += 2
                if k_4h > 80 and k_4h < d_4h: 
                    setup_points += 4 
                elif k_4h > 50 and k_4h < d_4h: 
                    setup_points += 2  
                else:
                    setup_points -= 3 # Chasing bottom
                if k_1h > 60 and k_1h < g('stoch_k_1h_prev'): 
                    setup_points += 3; reasons.append("1h_Turn_Down")
                elif k_1h > 20 and k_1h < d_1h:
                    setup_points += 2         
   
        score += setup_points

        trig_points = 0
        wt1, wt2 = g('wt1_15m'), g('wt2_15m')
        rel_vol = g('rel_vol_15m', 1.0)
        if is_long:
            if k_15m > d_15m: trig_points += 2
            if k_5m > d_5m: trig_points += 2
            if wt1 > wt2: trig_points += 2
            if k_5m < 30 and k_5m > d_5m: 
                trig_points += 3; reasons.append("5m_Sniper")
            
            if m1_is_fresh:
                # Crossover logic
                if k_1m > d_1m and k_1m_prev <= d_1m and k_1m < 30:
                    trig_points += 4; reasons.append("1m_Cross_Up")
                # Falling Knife penalty
                if k_1m < d_1m and k_5m < d_5m and score < 15:
                    trig_points -= 3
            else:
                reasons.append("1m_Stale_Ignored")         
                
        else: # SHORT
            if k_15m < d_15m: trig_points += 2
            if k_5m < d_5m: trig_points += 2
            if wt1 < wt2: trig_points += 2
            if k_5m > 70 and k_5m < d_5m: 
                trig_points += 3; reasons.append("5m_Sniper")

            # --- 1M CONDITIONAL BLOCK ---
            if m1_is_fresh:
                if k_1m < d_1m and k_1m_prev >= d_1m and k_1m > 70:
                    trig_points += 4; reasons.append("1m_Cross_Down")
                # Rocket Ship penalty
                if k_1m > d_1m and k_5m > d_5m and score < 15:
                    trig_points -= 3
            else:
                reasons.append("1m_Stale_Ignored")
        if rel_vol > 1.2: trig_points += 2; reasons.append("High_Vol")
        score += trig_points

        if is_exit and position:
            # For exits, we want to know if the trend is BROKEN.
            # A low score means "Sell Now". A high score means "Hold".
            
            gain = getattr(position, 'gain', 0)
            
            if is_long:
                # Structure Break
                if current_price <= dc_low_1h: 
                    score -= 15; reasons.append("Broken_1h_Low")
                elif current_price <= dc_low_15m: 
                    score -= 10; reasons.append("Broken_1h_Low") 
                # Momentum Roll
                if k_4h < d_4h and k_4h > 60:
                    score -= 5; reasons.append("4h_Peak")

                # Profit Take
                if gain > 5.0 and k_15m < d_15m:
                    score -= 5; reasons.append("Take_Profit")

            else: # SHORT
                if current_price >= dc_high_1h:
                    score -= 15; reasons.append("Broken_1h_High")
                elif current_price >= dc_high_15m:
                    score -= 10; reasons.append("Broken_1h_High")
                if k_4h > d_4h and k_4h < 20:
                    score -= 5; reasons.append("4h_Bottom")
                if gain > 5.0 and k_15m > d_15m:
                    score -= 5; reasons.append("Take_Profit")

        # URGENT_FIX: Bear market bias — penalize longs, favor shorts
        if getattr(config, 'BEAR_MARKET_MODE_TRADIER', False):
            if is_long:
                score -= 20; reasons.append("BEAR_PENALTY")
            else:
                score += 15; reasons.append("BEAR_BONUS")
        # --- FINAL CALCULATION ---
        final_score = float(max(0, min(30, int(score))))
        
        rec = "WAIT"
        if is_exit:
            # Exit Logic: Low score = GET OUT
            if final_score < 0: rec = "STRONG_REDUCE"
            elif final_score < 10: rec = "REDUCE"
            else: rec = "HOLD"
        else:
            # Entry Logic: High score = GET IN
            if final_score >= 28: rec = "STRONG_BUY" if is_long else "STRONG_SELL"
            elif final_score >= 20: rec = "GOOD_BUY" if is_long else "GOOD_SELL"
            elif final_score >= 15: rec = "WEAK_BUY" if is_long else "WEAK_SELL"
        return final_score, rec, ",".join(reasons)

    def compute_velocity_multiplier(self, i, current_price, position_side):
        try:
            g = lambda k: float(i.get(k, 0.0) or 0.0)
            pct = lambda a, b: 0.0 if abs(b) < 1e-9 else (float(a) - float(b)) / abs(float(b))
            ema20_4h, ema50_4h, ema200_4h = g('ema_20_4h'), g('ema_50_4h'), g('ema_200_4h')
            ema20_1h, ema50_1h, ema200_1h = g('ema_20_1h'), g('ema_50_1h'), g('ema_200_1h')
            ema20_15m, ema50_15m = g('ema_20_15m'), g('ema_50_15m')
            trend_4h = pct(ema20_4h, ema50_4h) + pct(ema50_4h, ema200_4h)
            trend_1h = pct(ema20_1h, ema50_1h) + pct(ema50_1h, ema200_1h)
            trend_15m = pct(ema20_15m, ema50_15m)
            total_trend_score = (trend_4h * 0.5) + (trend_1h * 0.3) + (trend_15m * 0.2)
            direction = 1.0 if position_side == "LONG" else -1.0
            multiplier = 1.0 + (total_trend_score * direction * 20.0)
            wt1_1h, wt2_1h = g('wt1_1h'), g('wt2_1h')
            wt1_4h, wt2_4h = g('wt1_4h'), g('wt2_4h')
            if position_side == "LONG":
                if wt1_4h > wt2_4h: multiplier += 0.3 # Major Momentum
                if wt1_1h > wt2_1h: multiplier += 0.2 # Minor Momentum
            else:
                if wt1_4h < wt2_4h: multiplier += 0.3
                if wt1_1h < wt2_1h: multiplier += 0.2
            return max(0.5, min(3.0, multiplier))
        except Exception:
            return 1.0


    async def calculate_quantity_complex(self, symbol: str, action: str, position_side: str, base_quantity: float, indicators: dict, position: Any, market_context: dict = None) -> float:
        i = self.parse_market_data(indicators)
        return await self.calculate_quantity_complex_extended(symbol, action, position_side, base_quantity, i, position, market_context)

 
# class StockStrategy:
#     def __init__(self, config):
#         self.config = config
#         self.non_shortable_symbols = {"ETHE", "TCEHY", "XIACF", "IBIT", "BITO", "GBTC", "MSTR", "MARA", "RIOT", "CLSK", "HIVE", "CAN", "BTBT"}

    def _get_val(self, indicators, key, default=0.0):
        try:
            val = indicators.get(key, default)
            if isinstance(val, str):
                val = val.strip()
                if val.lower() == 'nan': return default
                return float(val)
            return float(val) if val is not None else default
        except (ValueError, TypeError):
            return default

    def _get_str(self, indicators, key, default="neutral"):
        val = indicators.get(key, default)
        return str(val).lower().strip() if val else default

    def _get_bool(self, indicators, key, default=False):
        val = indicators.get(key, default)
        if isinstance(val, str): return val.lower() == 'true'
        return bool(val)

    def parse_market_data(self, indicators: dict):
        i = indicators or {}
        d = {}
        
        # --- Add this so timestamps survive the parsing ---
        d['timestamp_1m'] = i.get('timestamp_1m', i.get('timestamp', 0))
        d['timestamp'] = i.get('timestamp', 0)
        
        # --- 0. Core Data ---
        d['current_price'] = self._get_val(i, 'current_price') or self._get_val(i, 'current_price') or self._get_val(i, 'last')
        
        # --- 0. Core Data ---
        d['score'] = self._get_val(i, 'score', 0)
        d['symbol'] = str(i.get('symbol') or i.get('ticker') or 'UNKNOWN').upper()
        
        # --- 1. Sentiment Data (CRITICAL FOR "WHO IS WINNING") ---
        d['sentiment'] = self._get_val(i, '0market_sentiment_score', 0) # Global Market
        d['sentiment_local'] = self._get_val(i, '0market_sentiment_local', 0) # This Stock
        d['sentiment_rank'] = int(self._get_val(i, '0sentiment_rank', 999))
        d['sentiment_strength'] = self._get_val(i, '0sentiment_strength', 50)
        
        # Initialize all timeframe fields
        timeframes = ['1m', '5m', '15m', '1h', '4h', 'D']
        
        for tf in timeframes:
            # Stochastics & RSI
            d[f'stoch_k_{tf}'] = self._get_val(i, f'stoch_k_{tf}', 50); d[f'stoch_d_{tf}'] = self._get_val(i, f'stoch_d_{tf}', 50)
            d[f'stoch_k_{tf}_prev'] = self._get_val(i, f'stoch_k_{tf}_prev', 50)
            d[f'rsi_{tf}'] = self._get_val(i, f'rsi_{tf}', 50)
            
            # WaveTrend & HA
            d[f'wt1_{tf}'] = self._get_val(i, f'wt1_{tf}', 0); d[f'wt2_{tf}'] = self._get_val(i, f'wt2_{tf}', 0)
            d[f'ha_{tf}'] = self._get_str(i, f'ha_{tf}', 'neutral')
            
            # MA & DC
            d[f'sma_200_{tf}'] = self._get_val(i, f'sma_200_{tf}', 0)
            d[f'ema_20_{tf}'] = self._get_val(i, f'ema_20_{tf}', 0)
            d[f'ema_50_{tf}'] = self._get_val(i, f'ema_50_{tf}', 0)
            d[f'ema_200_{tf}'] = self._get_val(i, f'ema_200_{tf}', 0) # FIX: Added extraction of ema_200
            d[f'dc_high_{tf}'] = self._get_val(i, f'dc_high_{tf}', 0); d[f'dc_low_{tf}'] = self._get_val(i, f'dc_low_{tf}', 0)
            d[f'dc_basis_{tf}'] = self._get_val(i, f'dc_basis_{tf}', 0)
            
            # ATR & Vol & MFI
            d[f'atr_{tf}'] = self._get_val(i, f'atr_{tf}', 0)
            d[f'rel_vol_{tf}'] = self._get_val(i, f'relative_volume_{tf}', 1.0)
            d[f'lr_trend_{tf}'] = self._get_val(i, f'lr_trend_{tf}', 0.0)
            d[f'mfi_{tf}'] = self._get_val(i, f'mfi_{tf}', 50)
            
            # OHLC current & prev (needed for IBS, K5M bounce logic, structural checks)
            d[f'high_{tf}'] = self._get_val(i, f'high_{tf}', 0)
            d[f'low_{tf}'] = self._get_val(i, f'low_{tf}', 0)
            d[f'high_{tf}_prev'] = self._get_val(i, f'high_{tf}_prev', 0)
            d[f'low_{tf}_prev'] = self._get_val(i, f'low_{tf}_prev', 0)
            d[f'close_{tf}_prev'] = self._get_val(i, f'close_{tf}_prev', 0)

        # Special 1H
        d['slope_close_1h'] = self._get_val(i, 'slope_close_1h', 0.0)
        
        # Mappings
        if d.get('rsi_5m', 50) == 50 and ('rsi_5m' not in (i or {})): d['rsi_5m'] = self._get_val(i, 'rsi_5m', 50)

        return d

    # def compute_velocity_multiplier(self, i: dict, is_long: bool) -> float:
    #     # Keep your existing logic here, it was good for momentum detection
    #     pct = lambda a, b: 0.0 if abs(b) < 1e-9 else (float(a) - float(b)) / abs(float(b))
    #     long_speed = pct(i['ema_20_4h'], i['ema_50_4h']) + pct(i['ema_50_4h'], i['ema_200_4h']) + pct(i['ema_20_1h'], i['ema_50_1h'])
    #     short_speed = pct(i['ema_20_15m'], i['ema_50_15m']) + pct(i['ema_50_15m'], i['ema_200_15m'])
    #     direction = 1.0 if is_long else -1.0
    #     score = direction * (long_speed * 1.1 + short_speed * 0.9)
    #     return max(0.5, min(2.5, 1.0 + score))

    def _aggregate_numeric(self, data: dict) -> dict:
        nums = [float(v) for v in data.values() if isinstance(v, (int, float))]
        if not nums: return {'mean':0.0,'max':0.0,'min':0.0}
        return {'mean':sum(nums)/len(nums),'max':max(nums),'min':min(nums)}



    async def calculate_quantity_complex_extended(self, symbol: str, action: str, position_side: str, base_quantity: float,   i: dict, position: Any, market_context: dict = None,   account_key: str = None, reason: str = "") -> float:
        qty = float(base_quantity)
        is_long = position_side == 'LONG'
        current_price,_ = await self.trade_manager.get_current_price(symbol)
        if not current_price or current_price <= 0:
            current_price = float(i.get('current_price', 0) or 0)
        if current_price <= 0:
            return 0.0
        SP = config.START_POSITION_SIZE / current_price
        velocity_mult = 1.0                 
        log_parts = [f"[SIZE_CALC] {symbol} {position_side} Base: {qty:.2f}"]
        knife_penalty = 1.0
        ha_5m = i.get('ha_5m', 'neutral')
        if is_long:
            if ha_5m == 'red': 
                # Strict: If 5m is still red, we are likely early.
                knife_penalty *= 0.3 
                log_parts.append("Knife(HA_Red)")
        else: # SHORT
            if ha_5m == 'green': 
                knife_penalty *= 0.3
                log_parts.append("Knife(HA_Grn)")


        # Apply Knife Penalty immediately
        qty *= knife_penalty
        if qty <= 0: return 0.0
        # ============================================
        # 1. TIME-BASED STRATEGY SELECTION
        # ============================================
        now_est = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))
        current_time_est = now_est.time()
        market_open = dt_time(9, 30)
        market_close = dt_time(16, 0)
        
        # Determine if we should focus on long-term (1h/4h/D) or short-term (1m/5m/15m)
        is_long_term_focus = False
        is_short_term_focus = False
        
        # Before 11 AM or after 2 PM: Focus on longer timeframes
        if current_time_est < dt_time(11, 0) or current_time_est > dt_time(14, 0):
            is_long_term_focus = True
        # 11 AM - 2 PM: Focus on short-term intraday
        else:
            is_short_term_focus = True
        
        # After 3:30 PM, reduce all positions for market close
        is_near_close = current_time_est > dt_time(15, 30)
        
        # ============================================
        # 2. INITIAL REDUCTION FACTORS
        # ============================================
        reduction_factor = 1.0
        
        # Check if K aligns with D on 15m (basic alignment)
        k_15m = i.get('stoch_k_15m', 50)
        d_15m = i.get('stoch_d_15m', 50)
        k_ok = (k_15m >= d_15m) if is_long else (k_15m <= d_15m)
        if not k_ok:
            reduction_factor *= 0.3

        k_5m = i.get('stoch_k_5m', 50)
        k_1h = i.get('stoch_k_1h', 50)

        if is_long_term_focus:
            if is_long and k_1h > 80: reduction_factor *= 0.7
            elif not is_long and k_1h < 20: reduction_factor *= 0.7
        
        # Short-term overextension reduction
        if is_short_term_focus:
            if is_long and k_5m > 85: reduction_factor *= 0.6
            elif not is_long and k_5m < 15: reduction_factor *= 0.6
        
        if is_near_close: reduction_factor *= 0.5
        
        qty = qty * reduction_factor
        
        # ============================================
        # 3. SENTIMENT & MARKET STRUCTURE SCALING
        # ============================================
        
        # Extract sentiment data
        sent_global = i.get('0market_sentiment_score', 0)
        sent_local = i.get('0market_sentiment_local', 0)
        sent_strength = i.get('0sentiment_strength', 50)
        sentiment_rank = i.get('0sentiment_rank', 999)
        
        # Sentiment Multiplier (0.3x to 2.0x)
        sentiment_mult = 1.0
        
        if is_long:
            if sent_global > 0:
                if sent_local > sent_global + 40: sentiment_mult = 1.5
                elif sent_local > sent_global + 20: sentiment_mult = 1.2
                elif sent_local < -20: sentiment_mult = 0.3
            else: # Bear Market
                if sent_local > 60: sentiment_mult = 1.3
                elif sent_local < sent_global: sentiment_mult = 0.1
        else: # Short
            if sent_global < 0:
                if sent_local < sent_global - 40: sentiment_mult = 1.5
                elif sent_local < sent_global: sentiment_mult = 1.2
                elif sent_local > 20: sentiment_mult = 0.3
            else: # Bull Market
                if sent_local < -80: sentiment_mult = 1.3
                elif sent_local > sent_global: sentiment_mult = 0.1
        
        strength_boost = 1.0 + (sent_strength / 200.0)
        qty = qty * sentiment_mult * strength_boost
        log_parts.append(f"Sent({sentiment_mult:.1f}x)")

        # ============================================
        # 5. TECHNICAL STRUCTURE & POSITIONING
        # ============================================
        
        # Donchian Channel positioning (0.0 = bottom, 1.0 = top)
        def get_dc_position(price, dc_low, dc_high):
            if dc_high <= dc_low: return 0.5
            return (price - dc_low) / (dc_high - dc_low)
        
        # Multiple timeframe positioning
        pos_1m = get_dc_position(current_price, i.get('dc_low_1m', current_price), i.get('dc_high_1m', current_price))
        pos_5m = get_dc_position(current_price, i.get('dc_low_5m', current_price), i.get('dc_high_5m', current_price))
        pos_15m = get_dc_position(current_price, i.get('dc_low_15m', current_price), i.get('dc_high_15m', current_price))
        pos_1h = get_dc_position(current_price, i.get('dc_low_1h', current_price), i.get('dc_high_1h', current_price))
        pos_4h = get_dc_position(current_price, i.get('dc_low_4h', current_price), i.get('dc_high_4h', current_price))
        pos_D = get_dc_position(current_price, i.get('dc_low_D', current_price), i.get('dc_high_D', current_price))
        
        positioning_mult = 1.0
        
        # LONG-TERM FOCUS: Value near 4h/D lows
        if is_long_term_focus:
            if is_long:
                if pos_4h < 0.2:
                    positioning_mult = 1.8  # Deep value on 4h
                elif pos_4h < 0.4:
                    positioning_mult = 1.4
                elif pos_4h > 0.8:
                    positioning_mult = 0.6  # Chasing top
                
                if pos_D < 0.3:
                    positioning_mult *= 1.3  # Weekly deep value
            else:
                if pos_4h > 0.8:
                    positioning_mult = 1.8  # Top of 4h range
                elif pos_4h > 0.6:
                    positioning_mult = 1.4
                elif pos_4h < 0.2:
                    positioning_mult = 0.6  # Chasing bottom
        
        # SHORT-TERM FOCUS: Precision on 5m/15m
        elif is_short_term_focus:
            if is_long:
                if pos_5m < 0.3 and pos_15m < 0.4:
                    positioning_mult = 1.6  # Good intraday pullback
                elif pos_5m > 0.7:
                    positioning_mult = 0.7  # Extended on 5m
            else:
                if pos_5m > 0.7 and pos_15m > 0.6:
                    positioning_mult = 1.6  # Good intraday bounce
                elif pos_5m < 0.3:
                    positioning_mult = 0.7  # Oversold bounce
        
        log_parts.append(f"Velo({velocity_mult:.2f}x)")

        # Avoid extreme positioning
        if pos_1m > 0.95 or pos_1m < 0.05:
            positioning_mult *= 0.8  # Extreme on 1m
        
        qty = qty * positioning_mult
        
        # ============================================
        # 5. VOLUME & LIQUIDITY CONSIDERATIONS
        # ============================================
        
        # Relative volume multipliers
        rel_vol_5m = i.get('rel_vol_5m', 1.0)
        rel_vol_15m = i.get('rel_vol_15m', 1.0)
        rel_vol_1h = i.get('rel_vol_1h', 1.0)
        
        volume_mult = 1.0
        
        # High volume confirms moves
        if rel_vol_5m > 2.0:
            volume_mult = 1.3
        elif rel_vol_5m > 1.5:
            volume_mult = 1.15
        elif rel_vol_5m < 0.7:
            volume_mult = 0.8  # Low volume = weak move
        
        # Sustained volume on higher timeframes
        if rel_vol_1h > 1.8:
            volume_mult *= 1.2
        
        qty = qty * volume_mult
        
        # ============================================
        # 6. TREND VELOCITY & MOMENTUM (from crypto)
        # ============================================
        
        if action in ['OPEN', 'AUGMENT', 'REENTER']:
            velocity_mult = self.compute_velocity_multiplier(i, current_price, position_side)
            qty = qty * velocity_mult
        
        # ============================================
        # 7. EXTENSIVE TECHNICAL BONUSES (CRYPTO-STYLE)
        # ============================================
        # WaveTrend alignment
        wt1_5m, wt2_5m = i.get('wt1_5m',0), i.get('wt2_5m',0)
        wt1_1h, wt2_1h = i.get('wt1_1h',0), i.get('wt2_1h',0)

        # WaveTrend alignment bonuses
        if is_long:
            if wt1_5m > wt2_5m and k_15m < 50: qty += 0.3 * SP
            if wt1_1h > wt2_1h: qty += 0.5 * SP
        else:
            if i.get('wt1_5m', 0) < i.get('wt2_5m', 0) and i.get('stoch_k_15m', 50) > 50:
                qty += 0.3 * SP

            if i.get('wt1_1h', 0) < i.get('wt2_1h', 0) and i.get('wt1_4h', 0) < i.get('wt2_4h', 0):
                qty += 0.5 * SP
        
        # Donchian Breakout bonuses
        if is_long:
            if current_price > i.get('dc_high_15m', 999999) and i.get('stoch_k_15m', 50) < 70:
                qty += 0.5 * SP  # Breakout without being overbought

            if current_price > i.get('dc_high_1h', 999999) and i.get('ha_1h', 'neutral') == 'green':
                qty += 0.8 * SP  # Strong 1h breakout

        else:
            if current_price < i.get('dc_low_15m', 0) and i.get('stoch_k_15m', 50) > 30:
                qty += 0.5 * SP

            if current_price < i.get('dc_low_1h', 0) and i.get('ha_1h', 'neutral') == 'red':
                qty += 0.8 * SP

        # RSI extremes with volume
        if is_long:
            if i.get('rsi_5m', 50) < 30 and rel_vol_5m > 1.2:
                qty += 0.4 * SP
            if i.get('rsi_1h', 50) < 35 and rel_vol_1h > 1.5:
                qty += 0.6 * SP  # Hourly oversold with volume

        else:
            if i.get('rsi_5m', 50) > 70 and rel_vol_5m > 1.2:
                qty += 0.4 * SP
            if i.get('rsi_1h', 50) > 65 and rel_vol_1h > 1.5:
                qty += 0.6 * SP

        # Heikin Ashi alignment bonuses
        ha_alignment = 0
        if is_long:
            if i.get('ha_5m', 'neutral') == 'green': ha_alignment += 1
            if i.get('ha_15m', 'neutral') == 'green': ha_alignment += 1
            if i.get('ha_1h', 'neutral') == 'green': ha_alignment += 2
            if i.get('ha_4h', 'neutral') == 'green': ha_alignment += 3
        else:
            if i.get('ha_5m', 'neutral') == 'red': ha_alignment += 1
            if i.get('ha_15m', 'neutral') == 'red': ha_alignment += 1
            if i.get('ha_1h', 'neutral') == 'red': ha_alignment += 2
            if i.get('ha_4h', 'neutral') == 'red': ha_alignment += 3

        if ha_alignment >= 3:
            qty += (ha_alignment * 0.1) * SP

        # ============================================
        # 8. MARKET CONTEXT & BREADTH
        # ============================================

        # Market bias from context
        market_bias = market_context.get('bias', 0.0) if market_context else 0.0
        
        if is_long:
            if market_bias > 0.3:  # Strong bullish market
                qty *= 1.3
            elif market_bias < -0.3:  # Strong bearish market
                qty *= 0.6
            elif -0.1 < market_bias < 0.1:  # Neutral market
                qty *= 0.9  # Reduce size in uncertain markets
        else:
            if market_bias < -0.3:  # Strong bearish market
                qty *= 1.3
            elif market_bias > 0.3:  # Strong bullish market
                qty *= 0.6
            elif -0.1 < market_bias < 0.1:  # Neutral market
                qty *= 0.9

        # ============================================
        # 9. POSITION-SPECIFIC ADJUSTMENTS
        # ============================================

        if position:
            # Existing position adjustments
            position_amt = abs(float(getattr(position, 'positionAmt', 0)))
            position_gain = float(getattr(position, 'gain', 0))

            # For AUGMENT actions
            if action == 'AUGMENT':
                if position_gain < -2.0:
                    qty = 0.0  # Don't add to big losers
                elif position_gain < 0:
                    qty *= 0.3  # Small add to recover
                elif position_gain < 1.0:
                    qty *= 0.7  # Conservative add to small winners
                elif position_gain < 3.0:
                    qty *= 1.1  # Good momentum
                elif position_gain < 5.0:
                    qty *= 1.3  # Strong momentum
                else:
                    qty *= 1.5  # Big winner - pyramid

            # For REENTRY after reduction
            if action == 'REENTER':
                last_red_amt = float(getattr(position, 'last_reduction_amount', 0))
                if last_red_amt > 0:
                    # Try to reclaim at least the reduced amount
                    qty = max(qty, last_red_amt * 1.2)

        # ============================================
        # 10. SENTIMENT RANK BONUSES (TOP/BOTTOM 20)
        # ============================================CRAP

        if sentiment_rank >= 105:  # Top 5 bullish
            if is_long:
                qty += 2.0 * SP
            else:
                qty *= 0.3  # Hard to short top bullish

        elif sentiment_rank >= 195:  # Bottom 5 bearish
            if not is_long:
                qty += 2.0 * SP
            else:
                qty *= 0.3  # Hard to long bottom bearish

        elif 6 <= sentiment_rank <= 20:  # Top 6-20
            if is_long:
                qty += 1.0 * SP

        elif 180 <= sentiment_rank <= 194:  # Bottom 6-20
            if not is_long:
                qty += 1.0 * SP

        # ============================================
        # 11. FINAL SAFETY CHECKS & CAPS
        # ============================================
        
        is_exception = False
        
        # Check if "Exception" applies (High Conviction or Recovery)
        # REENTER: allow larger single order but NEVER exceed per-position total cap
        if action == "REENTER" or symbol in self.exceptions: 
            is_exception = True
            qty *= 3.0
            log_parts.append("🚀EXCEPTION_BOOST(3x)")
            
        if "RECOVERY" in reason.upper(): is_exception = True
        max_usd = self.limit_exception_order if is_exception else self.limit_normal_order
        
        # B. Calculate proposed USD value
        proposed_usd = qty * current_price
        
        # C. Clamp Single Order
        if proposed_usd > max_usd:
            old_qty = qty
            qty = max_usd / current_price
            log_parts.append(f"⚠️CAPPED(OrderLim ${max_usd:.0f})")
        
        # D. Clamp Total Position
        now_est = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))
        current_time_est = now_est.time()

        # --- 1. THE OPENING BELL BOOST (9:30 AM - 10:30 AM) ---
        is_opening_hour = dt_time(9, 30) <= current_time_est <= dt_time(10, 30)
        if is_opening_hour and action in ["OPEN", "REENTER"]:
            # Boost size by 1.5x - 2.0x during the initial session momentum
            opening_multiplier = 1.75 
            qty *= opening_multiplier
            log_parts.append(f"🚀OPEN_BOOST({opening_multiplier}x)")

        # --- 2. THE CLOSING BALANCER REDUCTION (3:00 PM - 4:00 PM) ---
        is_last_hour = dt_time(15, 0) <= current_time_est <= dt_time(16, 0)
        if is_last_hour:
            # Reduce new entry sizes significantly as we approach close to avoid overnight gap risk
            qty *= 0.5
            log_parts.append("⚖️EOD_CONSERVATIVE")
        positionAmt = abs(float(getattr(position, 'positionAmt', 0)))
        current_val = positionAmt * current_price
        max_Total = self.limit_total_pos  # ALWAYS cap total position — REENTER can boost single order but never exceed per-position max
        room_usd = max_Total - current_val
        if room_usd <= 0:
            qty = 0.0
            log_parts.append(f"⚠️BLOCKED(MaxPos ${self.limit_total_pos:.0f})")
        elif (qty * current_price) > room_usd:
            qty = room_usd / current_price
            log_parts.append(f"⚠️CAPPED(MaxPosRoom ${room_usd:.0f})")
        if qty * current_price < 50.0:
            if (positionAmt * current_price) + 50.0 <= self.limit_total_pos:
                qty = 50.0 / current_price
            else:
                qty = 0.0 #
        qty = max(0.0, float(int(qty)))
        if qty * current_price < 25.0: return 0.0
        return max(0.0, float(int(qty)))

    def evaluate_price_only_exit(self, position: Any, current_price: float) -> tuple[bool, str]:
        """
        Emergency logic used when indicators are stale (>900s).
        Decides to exit based strictly on PnL erosion and hard stops.
        """
        is_long = getattr(position, 'position_side', 'LONG') == 'LONG'
        entry = float(getattr(position, 'entry_price', 0))
        max_gain = float(getattr(position, 'max_gain', 0))
        
        if entry <= 0: return False, ""
        
        # Calculate current gain strictly from the fresh price feed
        gain = ((current_price - entry) / entry * 100) if is_long else ((entry - current_price) / entry * 100)

        # 1. HARD STOP (Absolute protection)
        if gain < -1.5:
            return True, f"STALE_DATA_HARD_STOP_{gain:.2f}%"

        # 2. GAIN EROSION (Trailing logic for dead indicators)
        # If we were up > 1%, and we've lost 50% of those gains while data is stale, KILL IT.
        if max_gain > 1.0 and gain < (max_gain * 0.5):
            return True, f"STALE_DATA_GAIN_EROSION_{gain:.2f}%_from_{max_gain:.2f}%"
        if max_gain > 0.5 and gain < 0.1:
            return True, "STALE_DATA_PROFIT_SHIELD"
        return False, ""

    async def evaluate_stop(self, symbol: str, position: Any, indicators: dict, market_context: dict = None, in_grace_period: bool = False) -> tuple[bool, str, float]:
        i = self.parse_market_data(indicators)
        is_long = getattr(position, 'position_side', 'LONG') == 'LONG'
        qty = abs(float(getattr(position, 'positionAmt', 0)))
        gain = float(getattr(position, 'gain', 0))
        current_price, ts = await self.trade_manager.get_current_price(symbol)
        current_price = float(current_price)

        opened_at = getattr(position, 'opened_at', None) or getattr(position, 'entry_time', None)
        hold_time_min = 0.0
        if opened_at:
            dt_opened = safe_datetime(opened_at)
            if dt_opened:
                hold_time_min = (datetime.now(timezone.utc) - dt_opened).total_seconds() / 60.0
        else:
            if in_grace_period:
                hold_time_min = 5.0 # fallback

        # --------------------------------------------------
        # 0. CATCH-ALL SAFETY NETS (Priority #1)
        # --------------------------------------------------

        # BACKTEST_CHANGE_T51: NO-LOSS NATURAL EXIT — block all exits below min profit %
        # Tradier backtest: HODL LONG D at 2% TP → 95.7% WR, +47.85% avg return
        _noloss_min_t = getattr(config, 'NOLOSS_MIN_PROFIT_PCT_TRADIER', 1.0)
        if _noloss_min_t > 0 and gain < _noloss_min_t and gain > -0.01:
            return False, f"NOLOSS_HOLD({gain:.2f}%<{_noloss_min_t}%)_bcT51", 0
        # A. HARD PERCENTAGE STOP (-1.5%) - ALWAYS ACTIVE
        if gain < -1.5:
            return True, f"HARD_STOP_LOSS_MAX_PAIN_(-1.5%)_Actual:{gain:.2f}%", qty

        # B. TIME-BASED STRUCTURAL STOPS (cascading: tight→medium→wide)
        # 0-20 min : dc_low4_5m  (4-period tight) — wide buffer (0.4%) to hold firm
        # 20-60 min: dc_low_5m   (20-period 5m)   — standard buffer (0.15%)
        # 60+ min  : dc_low_1h   (20-period 1h)   — standard buffer (0.1%)
        # Early stop also requires the PREVIOUS 5m candle to have closed below (confirmation).
        close_5m_prev = float(i.get('close_5m_prev', current_price) or current_price)

        if is_long:
            if hold_time_min <= 20.0:
                dc_stop = i.get('dc_low4_5m', 0)
                level_name = "DC4_5m"
                buf = 0.004  # 0.4% — wide buffer, hold firm vs noise
                # Also require prev 5m candle to confirm break
                confirmed = close_5m_prev < dc_stop if dc_stop > 0 else False
            elif hold_time_min <= 60.0:
                dc_stop = i.get('dc_low_5m', 0)
                level_name = "DC_5m"
                buf = 0.0015
                confirmed = True
            else:
                dc_stop = i.get('dc_low_1h', 0)
                level_name = "DC_1h"
                buf = 0.001
                confirmed = True

            if dc_stop > 0 and current_price < dc_stop * (1 - buf) and confirmed:
                return True, f"STRUCT_BREAK_{level_name}_LOW_({dc_stop:.2f})", qty
        else:
            if hold_time_min <= 20.0:
                dc_stop = i.get('dc_high4_5m', 999999)
                level_name = "DC4_5m"
                buf = 0.004
                close_5m_prev_h = float(i.get('close_5m_prev', current_price) or current_price)
                confirmed = close_5m_prev_h > dc_stop if dc_stop < 999999 else False
            elif hold_time_min <= 60.0:
                dc_stop = i.get('dc_high_5m', 999999)
                level_name = "DC_5m"
                buf = 0.0015
                confirmed = True
            else:
                dc_stop = i.get('dc_high_1h', 999999)
                level_name = "DC_1h"
                buf = 0.001
                confirmed = True

            if dc_stop < 999999 and current_price > dc_stop * (1 + buf) and confirmed:
                return True, f"STRUCT_BREAK_{level_name}_HIGH_({dc_stop:.2f})", qty

        # --------------------------------------------------
        # 2. EMERGENCY SIZE KILL (The Fix for Huge Pos)
        # --------------------------------------------------
        pos_value = qty * current_price
        is_huge_position = pos_value > 20000.0

        if is_huge_position and gain < -0.5:
            # Short-term momentum collapse check
            k1, d1 = i.get('stoch_k_1m', 50), i.get('stoch_d_1m', 50)
            k5, d5 = i.get('stoch_k_5m', 50), i.get('stoch_d_5m', 50)
            if is_long and k1 < d1 and k5 < d5:
                return True, f"EMERGENCY_KILL_SIZE_${pos_value:.0f}_MOMENTUM_LOST", qty
            elif not is_long and k1 > d1 and k5 > d5:
                 return True, f"EMERGENCY_KILL_SIZE_${pos_value:.0f}_MOMENTUM_LOST", qty

        # --------------------------------------------------
        # 3. SHIELD 
        # --------------------------------------------------
        # Shield weak technical exits: always under 20m; deadzone only up to 40m.
        is_in_deadzone = -0.3 < gain < 0.3
        if hold_time_min < 20.0 or (is_in_deadzone and hold_time_min < 40.0):
            return False, "", 0.0

        # --------------------------------------------------
        # 3.5. K5M MOMENTUM EXIT (BOUNCE-TURN / REAL DROP)
        # --------------------------------------------------
        k_5m = float(i.get('stoch_k_5m', 50)); k_5m_prev = float(i.get('stoch_k_5m_prev', 50)); k_15m = float(i.get('stoch_k_15m', 50)); k_1h = float(i.get('stoch_k_1h', 50))
        low_5m = float(i.get('low_5m', 0)); low_5m_prev = float(i.get('low_5m_prev', 0)); high_5m = float(i.get('high_5m', 0)); high_5m_prev = float(i.get('high_5m_prev', 0))
        dc_low_5m = float(i.get('dc_low_5m', 0)); dc_high_5m = float(i.get('dc_high_5m', 0)); close_5m_prev = float(i.get('close_5m_prev', current_price) or current_price)
        if gain > 0.5:
            if is_long and k_5m > 60 and k_5m < k_5m_prev and (k_15m > 80 or k_1h > 80) and low_5m > 0 and low_5m_prev > 0 and low_5m < low_5m_prev:
                return True, f"K5M_BOUNCE_TURN_L_k5:{k_5m:.1f}<prev:{k_5m_prev:.1f}_k15:{k_15m:.1f}_k1h:{k_1h:.1f}_lo5:{low_5m:.2f}<prev:{low_5m_prev:.2f}_limit:{(close_5m_prev if close_5m_prev > 0 else current_price):.2f}", qty
            elif is_long and low_5m_prev > 0 and current_price < low_5m_prev:
                cliff = dc_low_5m > 0 and current_price < dc_low_5m and k_5m < 30; exit_p = current_price if cliff else (close_5m_prev if close_5m_prev > 0 else current_price)
                return True, f"HARD_DROP_BELOW_PREV_LOW_5m:{current_price:.2f}<lo5prev:{low_5m_prev:.2f}{'_CLIFF_dc' if cliff else '_BOUNCE_WAIT'}_limit:{exit_p:.2f}", qty
            elif not is_long and k_5m < 40 and k_5m > k_5m_prev and (k_15m < 20 or k_1h < 20) and high_5m > 0 and high_5m_prev > 0 and high_5m > high_5m_prev:
                return True, f"K5M_BOUNCE_TURN_S_k5:{k_5m:.1f}>prev:{k_5m_prev:.1f}_k15:{k_15m:.1f}_k1h:{k_1h:.1f}_hi5:{high_5m:.2f}>prev:{high_5m_prev:.2f}_limit:{(close_5m_prev if close_5m_prev > 0 else current_price):.2f}", qty
            elif not is_long and high_5m_prev > 0 and current_price > high_5m_prev:
                cliff = dc_high_5m > 0 and current_price > dc_high_5m and k_5m > 70; exit_p = current_price if cliff else (close_5m_prev if close_5m_prev > 0 else current_price)
                return True, f"HARD_RISE_ABOVE_PREV_HIGH_5m:{current_price:.2f}>hi5prev:{high_5m_prev:.2f}{'_CLIFF_dc' if cliff else '_BOUNCE_WAIT'}_limit:{exit_p:.2f}", qty
        if is_long and dc_low_5m > 0 and current_price < dc_low_5m and k_5m < 30:
            return True, f"K5M_REAL_DROP_DC_k5:{k_5m:.1f}<dc5:{dc_low_5m:.2f}", qty
        if not is_long and dc_high_5m > 0 and current_price > dc_high_5m and k_5m > 70:
            return True, f"K5M_REAL_RISE_DC_k5:{k_5m:.1f}>dc5:{dc_high_5m:.2f}", qty
        # BACKTEST_CHANGE_100: 5m candle structure break exit (Tradier: PF 2.53, Sharpe 4.45 at 15m+1h)
        # lower_high on 5m for LONG (structure breaking down) + k_5m falling = exit
        # higher_low on 5m for SHORT (structure turning up) + k_5m rising = exit
        _higher_low_5m = low_5m > 0 and low_5m_prev > 0 and low_5m > low_5m_prev
        _lower_high_5m = high_5m > 0 and high_5m_prev > 0 and high_5m < high_5m_prev
        if gain > 0.2:
            if is_long and _lower_high_5m and k_5m < k_5m_prev and hold_time_min > 15:
                return True, f"STRUCT_LH5M_EXIT_bc100_k5:{k_5m:.1f}<prev:{k_5m_prev:.1f}_hi5:{high_5m:.2f}<prev:{high_5m_prev:.2f}", qty
            elif not is_long and _higher_low_5m and k_5m > k_5m_prev and hold_time_min > 15:
                return True, f"STRUCT_HL5M_EXIT_bc100_k5:{k_5m:.1f}>prev:{k_5m_prev:.1f}_lo5:{low_5m:.2f}>prev:{low_5m_prev:.2f}", qty

        # --------------------------------------------------
        # 3.6. IBS EXIT (Internal Bar Strength)
        # Quantitativo research: IBS > 0.9 = close in top 10% of range → exhaustion for longs
        #                        IBS < 0.1 = close in bottom 10% of range → exhaustion for shorts
        # Uses previous COMPLETED 5m bar for clean signal.
        # Minimum gain guard (>1.0%) to avoid premature exits on flat/losing trades.
        # --------------------------------------------------
        if gain > 1.0:
            h5p = float(i.get('high_5m_prev', 0)); l5p = float(i.get('low_5m_prev', 0)); c5p = float(i.get('close_5m_prev', 0) or 0)
            bar_range_5m = h5p - l5p
            if bar_range_5m > 0 and c5p > 0:
                ibs_5m = (c5p - l5p) / bar_range_5m
                if is_long and ibs_5m > 0.9:
                    return True, f"IBS_EXHAUSTION_LONG_ibs:{ibs_5m:.2f}_gain:{gain:.1f}%", qty
                elif not is_long and ibs_5m < 0.1:
                    return True, f"IBS_EXHAUSTION_SHORT_ibs:{ibs_5m:.2f}_gain:{gain:.1f}%", qty
            # Also check 15m bar for stronger confirmation
            h15p = float(i.get('high_15m_prev', 0)); l15p = float(i.get('low_15m_prev', 0)); c15p = float(i.get('close_15m_prev', 0) or 0)
            bar_range_15m = h15p - l15p
            if bar_range_15m > 0 and c15p > 0 and gain > 1.5:
                ibs_15m = (c15p - l15p) / bar_range_15m
                if is_long and ibs_15m > 0.9:
                    return True, f"IBS_15m_EXHAUSTION_LONG_ibs:{ibs_15m:.2f}_gain:{gain:.1f}%", qty
                elif not is_long and ibs_15m < 0.1:
                    return True, f"IBS_15m_EXHAUSTION_SHORT_ibs:{ibs_15m:.2f}_gain:{gain:.1f}%", qty

        # --------------------------------------------------
        # 4. NORMAL TECHNICAL EXITS
        # --------------------------------------------------
        score_exit, rec_exit, reason_exit = self.calculate_signal_score("SYS", symbol, is_long, current_price, i, position, is_exit=True)
        score_exit = safe_float(score_exit)

        if score_exit <= -7 or "STRONG_REDUCE" in rec_exit:
            return True, f"ALGO_EXIT_{rec_exit}: {reason_exit}", qty

        if score_exit <= -4:
            return True, f"ALGO_WEAK_EXIT: {reason_exit}", qty

        # Sentiment Collapse — close while still above commission cost, before position goes negative
        # Must hold >3 min (no instant dumps) AND gain must cover commissions (>0.12%)
        s_local = i.get('sentiment_local', 0)
        s_global = i.get('sentiment', 0)
        _hold_min = (datetime.now(timezone.utc) - position.opened_at).total_seconds() / 60 if position and hasattr(position, 'opened_at') and position.opened_at else 999
        if gain > 0.12 and _hold_min > 3:
            if is_long:
                if s_local < -30 and s_local < (s_global - 25):
                    return True, "Sentiment_Collapsed_Below_Global", qty
            else:
                if s_local > 30 and s_local > (s_global + 25):
                    return True, "Sentiment_Spiked_Above_Global", qty

        # Standard Technical Breakdowns (Profit Taking / Extended Trends)
        if is_long:
            if current_price < i.get('dc_basis_1h', 0) and i.get('lr_trend_15m', 0) < 0:
                return True, "Structure_Break_1H_Basis", qty

            if gain > 1.5 and current_price > i.get('dc_high_1h', 999999) and i.get('rsi_15m', 50) > 80:
                return True, "Extreme_Overbought_TP", qty

            if i.get('rsi_15m', 50) < 40 and i.get('stoch_k_15m', 50) < i.get('stoch_d_15m', 50) and i.get('ha_15m', 'neutral') == 'red':
                 return True, "Technical_Breakdown_15m", qty
        else:
            if current_price > i.get('dc_basis_1h', 999999) and i.get('lr_trend_15m', 0) > 0:
                return True, "Structure_Break_1H_Basis", qty

            if gain > 1.5 and current_price < i.get('dc_low_1h', 0) and i.get('rsi_15m', 50) < 20:
                return True, "Extreme_Oversold_TP", qty

            if i.get('rsi_15m', 50) > 60 and i.get('stoch_k_15m', 50) > i.get('stoch_d_15m', 50) and i.get('ha_15m', 'neutral') == 'green':
                 return True, "Technical_Breakdown_15m", qty
                 
        return False, "", 0.0
    
    async def evaluate_open(self, account_key, symbol: str, position_side: str, indicators: dict, market_context: dict = None) -> tuple[str, str, float, float]:
        symbol = symbol.upper()
        i = self.parse_market_data(indicators)
        final_qty = 0.0
        longs = getattr(self.trade_manager, f"symbols_long_{account_key}",[])
        shorts = getattr(self.trade_manager, f"symbols_short_{account_key}",[])
        current_price,ts = await self.trade_manager.get_current_price(symbol)
        current_price = float(current_price) if current_price else 0.0
        if not current_price or current_price <= 0:
            logger.warning(f"Skipping {symbol}: Price is zero or unavailable.")
            return "NO_ACTION", "No Price", 0.0, 0.0
            
        can_short = symbol not in config.NON_SHORTABLE
        is_long = (position_side == "LONG")

        # ----------------------------------------------------------
        # RATIO GATE: Custom Universe Index + QQQ momentum
        # Uses our OWN traded symbols as market proxy (not generic SPY/QQQ)
        # PLTR and oil exempt from ratio blocking
        # ----------------------------------------------------------
        _ratio_exempt = symbol.upper() in ('PLTR', 'XOM', 'CVX', 'OXY', 'COP', 'SLB', 'HAL', 'USO')
        if not _ratio_exempt:
            try:
                _bal = self.trade_manager.get_current_portfolio_balance()
                _long_pct = _bal.get('long_pct', 0.5) * 100
                _short_pct = _bal.get('short_pct', 0.5) * 100
                _custom_idx = self.trade_manager.get_custom_universe_index()
                _universe_score = _custom_idx.get('composite', 0.0)
                _qqq = self.trade_manager.market_snapshot.get('QQQ', indicators)
                _k1h = float(_qqq.get('stoch_k_1h', 50) or 50)
                _d1h = float(_qqq.get('stoch_d_1h', 50) or 50)
                _qqq_momentum = 30.0 if (_k1h > _d1h and _k1h > 50) else (-30.0 if (_k1h < _d1h and _k1h < 50) else 0.0)
                # Active book breadth (our actual traded symbols)
                _active_score = 0.0
                try:
                    _active_syms = set(getattr(self.trade_manager, f"symbols_long_{account_key}", []) +
                                       getattr(self.trade_manager, f"symbols_short_{account_key}", []))
                    _snap = self.trade_manager.market_snapshot
                    _a_up, _a_dn = 0, 0
                    for _s in _active_syms:
                        _d = _snap.get(_s, {})
                        _c = float(_d.get('current_price', 0) or 0)
                        _p = float(_d.get('close_1h_prev', 0) or 0)
                        if _c > 0 and _p > 0:
                            if _c > _p * 1.001: _a_up += 1
                            elif _c < _p * 0.999: _a_dn += 1
                    _a_total = _a_up + _a_dn
                    if _a_total > 0:
                        _active_score = ((_a_up - _a_dn) / _a_total) * 50.0
                except Exception:
                    pass
                # Composite: 50% universe + 30% active book + 20% QQQ momentum
                _composite = _universe_score * 0.5 + _active_score * 0.3 + _qqq_momentum * 0.2 * 5
                _target_long_pct = max(10.0, min(90.0, 50.0 + _composite * 0.9))
                _ad = _custom_idx.get('ad_ratio', 1.0)
                if is_long and _long_pct > _target_long_pct:
                    logger.warning(f"[RATIO_GATE] {account_key}:{symbol}_{position_side}: BLOCK LONG — universe={_universe_score:.0f} qqq_m={_qqq_momentum:.0f} composite={_composite:.0f} A/D={_ad:.2f} long={_long_pct:.0f}%>target={_target_long_pct:.0f}%")
                    return "NO_ACTION", f"RATIO_GATE(L{_long_pct:.0f}>T{_target_long_pct:.0f})", 0.0, 0.0
                if not is_long and _short_pct > (100 - _target_long_pct):
                    logger.warning(f"[RATIO_GATE] {account_key}:{symbol}_{position_side}: BLOCK SHORT — composite={_composite:.0f} short={_short_pct:.0f}%>target={100-_target_long_pct:.0f}%")
                    return "NO_ACTION", f"RATIO_GATE(S{_short_pct:.0f}>T{100-_target_long_pct:.0f})", 0.0, 0.0
            except Exception as e:
                logger.warning(f"[RATIO_GATE] Error {symbol}: {e} — blocking as safety")
                return "NO_ACTION", "RATIO_GATE_ERROR", 0.0, 0.0

        # ----------------------------------------------------------
        # CVaR 5% PRE-ENTRY GATE (Quantitativo research)
        # Skip stocks where tail risk > -6% (avg of worst 5% of days)
        # ----------------------------------------------------------
        cvar_safe, cvar_val = self._is_cvar_safe(symbol, threshold=-6.0)
        if not cvar_safe and cvar_val != -999.0:
            # Has data but fails CVaR: log and block
            logger.debug(f"[CVaR_BLOCK] {symbol}: CVaR={cvar_val:.1f}% < -6.0% threshold, skipping entry")
            return "NO_ACTION", f"CVaR_Block({cvar_val:.1f}%<-6.0%)", 0.0, 0.0
        # If cvar_val == -999 (no data), allow trade but log warning
        if cvar_val == -999.0:
            logger.debug(f"[CVaR_WARN] {symbol}: No daily klines for CVaR check, proceeding without filter")

        score, rec, reason = 0.0, "WAIT", "uncalculated"
        k5 = i.get('stoch_k_5m', 50)
        d5 = i.get('stoch_d_5m', 50)
        k5_prev = i.get('stoch_k_5m_prev', 50)
        
        # Entry requires price to be on a genuine pullback — at or near DC support/resistance.
        # This prevents chasing mid-range after a close.
        dc_low4 = i.get('dc_low4_5m', 0)
        dc_low5 = i.get('dc_low_5m', 0)
        dc_high4 = i.get('dc_high4_5m', 999999)
        dc_high5 = i.get('dc_high_5m', 999999)
        dc_basis1h = i.get('dc_basis_1h', 0)
        # Stoch direction vars — used by both LONG and SHORT DC paths below
        _k5e = float(i.get('stoch_k_5m', 50) or 50)
        _d5e = float(i.get('stoch_d_5m', 50) or 50)
        _k15e = float(i.get('stoch_k_15m', 50) or 50)
        _d15e = float(i.get('stoch_d_15m', 50) or 50)

        if is_long:
            # DC LONG fast-path: check BEFORE stoch hook guard
            _dch5e_l  = float(i.get('dc_high_5m', 0) or 0)
            _dch15e_l = float(i.get('dc_high_15m', 0) or 0)
            _dch1he_l = float(i.get('dc_high_1h', 0) or 0)
            _dch4he_l = float(i.get('dc_high_4h', 0) or 0)
            _buf_l = 0.001
            _dc_long_mult_l, _dc_long_stop_l, _dc_long_tf_l = 1.0, 0.0, None
            if _dch4he_l > 0 and current_price > _dch4he_l * (1 + _buf_l):
                _dc_long_mult_l, _dc_long_stop_l, _dc_long_tf_l = 3.0, float(i.get('dc_basis_4h', 0) or 0), "DC4H"
            elif _dch1he_l > 0 and current_price > _dch1he_l * (1 + _buf_l):
                _dc_long_mult_l, _dc_long_stop_l, _dc_long_tf_l = 2.0, float(i.get('dc_basis_1h', 0) or 0), "DC1H"
            elif _dch15e_l > 0 and current_price > _dch15e_l * (1 + _buf_l):
                _dc_long_mult_l, _dc_long_stop_l, _dc_long_tf_l = 1.5, float(i.get('dc_basis_15m', 0) or 0), "DC15M"
            elif _dch5e_l > 0 and current_price > _dch5e_l * (1 + _buf_l):
                _dc_long_mult_l, _dc_long_stop_l, _dc_long_tf_l = 1.0, float(i.get('dc_basis_5m', 0) or 0), "DC5M"

            _long_stoch_ok = (_k5e > _d5e) and (_k15e > _d15e)
            if _dc_long_tf_l and symbol in longs and _long_stoch_ok:
                _tier_mults_l = {"DC5M": 1.0, "DC15M": 2.0, "DC1H": 3.0, "DC4H": 5.0}
                _dc_long_mult_l = _tier_mults_l.get(_dc_long_tf_l, 1.0)
                base_qty = config.START_POSITION_SIZE * _dc_long_mult_l / max(current_price, 1e-9)
                final_qty = await self.calculate_quantity_complex(symbol, "OPEN", "LONG", base_qty, indicators, None, market_context)
                if final_qty > 0:
                    stop_tag = f"_stop:{_dc_long_stop_l:.2f}" if _dc_long_stop_l > 0 else ""
                    conviction = min(90.0, 60.0 + _dc_long_mult_l * 6)
                    logger.info(f"[{account_key}] 📈 DC_BREAK {_dc_long_tf_l} LONG {symbol} @ {current_price:.2f} k5={_k5e:.0f}>d5={_d5e:.0f} | ×{_dc_long_mult_l} stop={_dc_long_stop_l:.2f}")
                    return "OPEN", f"DC_BREAK_{_dc_long_tf_l}_LONG{stop_tag}", conviction, final_qty

            # === OVERSOLD_TURN entry (Grid test winner: 87% WR, PF 22.4) ===
            # k_15m < 30 (oversold on higher TF) AND k_5m rising (turn confirmed)
            _k15_os = float(i.get('stoch_k_15m', 50) or 50)
            _k1h_l = float(i.get('stoch_k_1h', 50) or 50)
            _d1h_l = float(i.get('stoch_d_1h', 50) or 50)
            # NEVER open LONG below dc_low_5m — wrong side of the channel
            if dc_low5 > 0 and current_price < dc_low5:
                return "NO_ACTION", f"Below_DC_Low5m_no_long({dc_low5:.2f})", 0.0, 0.0
            is_oversold_turn = (_k15e < 30) and (k5 > k5_prev) and (k5 > d5)
            # Quality gate: 2/3 stoch TFs aligned for LONG
            _sq_votes_l = int(_k5e > _d5e) + int(_k15e > _d15e) + int(_k1h_l > _d1h_l)
            if not is_oversold_turn or _sq_votes_l < 2:
                return "NO_ACTION", f"Wait_Long_OversoldTurn_k15:{_k15e:.0f}_k5:{k5:.0f}_votes:{_sq_votes_l}", 0.0, 0.0
            if dc_basis1h > 0 and current_price > dc_basis1h * 1.008:
                return "NO_ACTION", f"Extended_Above_Basis1h({dc_basis1h:.2f})", 0.0, 0.0
        else:
            # --- DC current-state check for SHORT: price IS below a DC channel ---
            # Runs BEFORE stoch hook check — DC breakouts are valid regardless of stoch level
            _dcl5e  = float(i.get('dc_low_5m', 0) or 0)
            _dcl15e = float(i.get('dc_low_15m', 0) or 0)
            _dcl1he = float(i.get('dc_low_1h', 0) or 0)
            _dcl4he = float(i.get('dc_low_4h', 0) or 0)
            _buf = 0.0005  # Marathon winner: buffer=0.0005
            _dc_short_mult, _dc_short_stop, _dc_short_tf = 1.0, 0.0, None
            if _dcl4he > 0 and current_price < _dcl4he * (1 - _buf):
                _dc_short_mult, _dc_short_stop, _dc_short_tf = 3.0, float(i.get('dc_basis_4h', 0) or 0), "DC4H"
            elif _dcl1he > 0 and current_price < _dcl1he * (1 - _buf):
                _dc_short_mult, _dc_short_stop, _dc_short_tf = 2.0, float(i.get('dc_basis_1h', 0) or 0), "DC1H"
            elif _dcl15e > 0 and current_price < _dcl15e * (1 - _buf):
                _dc_short_mult, _dc_short_stop, _dc_short_tf = 1.5, float(i.get('dc_basis_15m', 0) or 0), "DC15M"
            elif _dcl5e > 0 and current_price < _dcl5e * (1 - _buf):
                _dc_short_mult, _dc_short_stop, _dc_short_tf = 1.0, float(i.get('dc_basis_5m', 0) or 0), "DC5M"

            # Direction confirmation: stoch must confirm active SHORT momentum
            _short_stoch_confirms = (_k5e < _d5e) and (_k15e < _d15e)

            if _dc_short_tf and symbol in shorts and can_short and _short_stoch_confirms:
                # Tier sizes: 5m=1×, 15m=2×, 1h=3×, 4H=5× — each deeper TF = bigger obligatory short
                _tier_mults = {"DC5M": 1.0, "DC15M": 2.0, "DC1H": 3.0, "DC4H": 5.0}
                _dc_short_mult = _tier_mults.get(_dc_short_tf, 1.0)
                base_qty = config.START_POSITION_SIZE * _dc_short_mult / max(current_price, 1e-9)
                final_qty = await self.calculate_quantity_complex(symbol, "OPEN", "SHORT", base_qty, indicators, None, market_context)
                if final_qty > 0:
                    stop_tag = f"_stop:{_dc_short_stop:.2f}" if _dc_short_stop > 0 else ""
                    conviction = min(90.0, 60.0 + _dc_short_mult * 6)
                    logger.info(f"[{account_key}] 📉 DC_BREAK {_dc_short_tf} SHORT {symbol} @ {current_price:.2f} k5={_k5e:.0f}<d5={_d5e:.0f} | ×{_dc_short_mult} stop={_dc_short_stop:.2f}")
                    return "OPEN", f"DC_BREAK_{_dc_short_tf}_SHORT{stop_tag}", conviction, final_qty

            # === OVERBOUGHT_SHORT entry (Grid test winner: 89% WR, PF 40.2) ===
            # k_15m > 70 (overbought on higher TF) AND k_5m falling (turn confirmed)
            _k1h_s = float(i.get('stoch_k_1h', 50) or 50)
            _d1h_s = float(i.get('stoch_d_1h', 50) or 50)
            # NEVER open SHORT above dc_high_5m — wrong side of the channel
            if dc_high5 < 999999 and dc_high5 > 0 and current_price > dc_high5:
                return "NO_ACTION", f"Above_DC_High5m_no_short({dc_high5:.2f})", 0.0, 0.0
            is_overbought_turn = (_k15e > 70) and (k5 < k5_prev) and (k5 < d5)
            # Quality gate: 2/3 stoch TFs aligned for SHORT
            _sq_votes_s = int(_k5e < _d5e) + int(_k15e < _d15e) + int(_k1h_s < _d1h_s)
            if not is_overbought_turn or _sq_votes_s < 2:
                return "NO_ACTION", f"Wait_Short_OverboughtTurn_k15:{_k15e:.0f}_k5:{k5:.0f}_votes:{_sq_votes_s}", 0.0, 0.0
            if dc_basis1h > 0 and current_price < dc_basis1h * 0.992:
                return "NO_ACTION", f"Extended_Below_Basis1h({dc_basis1h:.2f})", 0.0, 0.0

        if is_long and symbol in longs:
            score, rec, reason = self.calculate_signal_score(account_key, symbol, True, current_price, i, None, is_exit=False)
            score = safe_float(score)
            if score >= 12:
                base_qty = (config.START_POSITION_SIZE / max(current_price, 1e-9))
                final_qty = await self.calculate_quantity_complex(symbol, "OPEN", "LONG", base_qty, indicators, None, market_context)
                if final_qty > 0:
                    conviction = 50.0  # BACKTEST_CHANGE_T40: conviction only works for SHORT
                    return "OPEN", f"LONG_{rec}: {reason}", conviction, final_qty
            if score >= 6 and "Sniper" in reason:
                 base_qty = (config.START_POSITION_SIZE * 0.5 / max(current_price, 1e-9))
                 return "OPEN", f"SNIPER_LONG_{rec}", 60.0, base_qty

        elif not is_long and symbol in shorts and can_short:
            score, rec, reason = self.calculate_signal_score(account_key, symbol, False, current_price, i, None, is_exit=False)
            score = safe_float(score)
            if score >= 12:
                base_qty = (config.START_POSITION_SIZE / max(current_price, 1e-9))
                final_qty = await self.calculate_quantity_complex(symbol, "OPEN", "SHORT", base_qty, indicators, None, market_context)
                if final_qty > 0:
                    conviction = min(95.0, score * 3.5)
                    return "OPEN", f"SHORT_{rec}: {reason}", conviction, final_qty
            if score >= 6 and "Sniper" in reason:
                 base_qty = (config.START_POSITION_SIZE * 0.5 / max(current_price, 1e-9))
                 return "OPEN", f"SNIPER_SHORT_{rec}", 60.0, base_qty

        # 2. Strategy Variables
        uptrend_1h = i.get('lr_trend_1h', 0) > 0 and current_price > i.get('ema_200_1h', 0)
        downtrend_1h = i.get('lr_trend_1h', 0) < 0 and current_price < i.get('ema_200_1h', 999999)

        dist_to_low = (current_price - i.get('dc_low_15m', 0)) / max(current_price, 1e-9)
        dist_to_high = (i.get('dc_high_15m', 999999) - current_price) / max(current_price, 1e-9)

        pullback_long = dist_to_low < 0.01
        pullback_short = dist_to_high < 0.01

        trigger_long = k5 > d5 and k5 < 35 and k5 > k5_prev   # Was oversold (<35) and turning up
        trigger_short = k5 < d5 and k5 > 65 and k5 < k5_prev  # Was overbought (>65) and turning down
        # BACKTEST_CHANGE_100: 5m candle structure entry (higher_low for LONG, lower_high for SHORT)
        # Backtest: structure entry + 15m confirm = PF 2.53, Sharpe 4.45 on stocks
        _low5 = float(i.get('low_5m', 0) or 0); _low5p = float(i.get('low_5m_prev', 0) or 0)
        _high5 = float(i.get('high_5m', 0) or 0); _high5p = float(i.get('high_5m_prev', 0) or 0)
        _struct_hl5 = _low5 > 0 and _low5p > 0 and _low5 > _low5p  # higher_low on 5m
        _struct_lh5 = _high5 > 0 and _high5p > 0 and _high5 < _high5p  # lower_high on 5m
        if is_long and _struct_hl5 and k5 > d5 and k5 < 60 and _k15e > _d15e:
            trigger_long = True  # Structure confirms long entry
        elif not is_long and _struct_lh5 and k5 < d5 and k5 > 40 and _k15e < _d15e:
            trigger_short = True  # Structure confirms short entry

        sent_long_ok = (i.get('sentiment_local', 0) > i.get('sentiment', 0)) or (i.get('sentiment_local', 0) > 10)
        sent_short_ok = (i.get('sentiment_local', 0) < i.get('sentiment', 0)) or (i.get('sentiment_local', 0) < -10)
        
        if is_long and symbol in longs:
            if uptrend_1h and pullback_long and trigger_long and sent_long_ok:
                base_qty = (config.START_POSITION_SIZE / max(current_price, 1e-9))
                final_qty = await self.calculate_quantity_complex(symbol, "OPEN", "LONG", base_qty, indicators, None, market_context)
                if final_qty > 0:
                    return "OPEN", "Invest_Long_Pullback", 85.0, final_qty
                    
            # Fast bounce off 5m low
            dist_5m_low = (current_price - i.get('dc_low_5m', 0)) / max(current_price, 1e-9)
            bounce_1m_long = i.get('stoch_k_1m', 50) > i.get('stoch_d_1m', 50) and i.get('stoch_k_1m', 50) < 30
            if dist_5m_low < 0.008 and bounce_1m_long:
                base_qty = (config.START_POSITION_SIZE / max(current_price, 1e-9))
                final_qty = await self.calculate_quantity_complex(symbol, "OPEN", "LONG", base_qty, indicators, None, market_context)
                if final_qty > 0:
                    return "OPEN", "Fast_Bounce_Long", 80.0, final_qty

        elif not is_long and symbol in shorts and can_short:
            if downtrend_1h and pullback_short and trigger_short and sent_short_ok:
                base_qty = (config.START_POSITION_SIZE / max(current_price, 1e-9))
                final_qty = await self.calculate_quantity_complex(symbol, "OPEN", "SHORT", base_qty, indicators, None, market_context)
                if final_qty > 0:
                    return "OPEN", "Invest_Short_Rip", 85.0, final_qty
                    
            # Fast bounce off 5m high
            dist_5m_high = (i.get('dc_high_5m', 999999) - current_price) / max(current_price, 1e-9)
            bounce_1m_short = i.get('stoch_k_1m', 50) < i.get('stoch_d_1m', 50) and i.get('stoch_k_1m', 50) > 70
            if dist_5m_high < 0.008 and bounce_1m_short:
                base_qty = (config.START_POSITION_SIZE / max(current_price, 1e-9))
                final_qty = await self.calculate_quantity_complex(symbol, "OPEN", "SHORT", base_qty, indicators, None, market_context)
                if final_qty > 0:
                    return "OPEN", "Fast_Bounce_Short", 80.0, final_qty


        # --- LOGGING FOR DEBUGGING (Why didn't we trade?) ---
        if is_long and (i.get('stoch_k_5m', 50) > i.get('stoch_d_5m', 50)):
            if not uptrend_1h: return "NO_ACTION", f"Wait_Trend_1H (Price < EMA200)", 0.0, 0.0
            if not pullback_long: return "NO_ACTION", f"Wait_Pullback (DistToLow {dist_to_low:.2%})", 0.0, 0.0
            if not trigger_long: return "NO_ACTION", f"Stoch_High ({i.get('stoch_k_5m', 50):.0f} > 50)", 0.0, 0.0
        return "NO_ACTION", "", 0.0, 0.0


    async def evaluate_augment(self, symbol, position, indicators, market_context=None):
        """
        DC Tier Augment: position sizing per DC tier.
        Tiers: 5m=1×, 15m=2×, 1h=3×, 4h=5× of START_POSITION_SIZE.
        REQUIRES gain > 0.15% before augmenting — never add to a losing position.
        """
        try:
            i = self.parse_market_data(indicators)
            current_price = float(i.get('current_price', 0) or 0)
            if current_price <= 0: return False, "", 0.0, 0.0

            is_long = getattr(position, 'position_side', 'LONG') == 'LONG'
            current_qty = abs(float(getattr(position, 'quantity', 0) or getattr(position, 'positionAmt', 0) or 0))
            current_value = current_qty * current_price
            entry_price = float(getattr(position, 'entry_price', 0) or 0)
            if entry_price > 0:
                gain = (current_price - entry_price) / entry_price * 100 if is_long else (entry_price - current_price) / entry_price * 100
            else:
                gain = 0.0

            # MUST be in profit before augmenting — never add to a loser
            if gain < 0.15:
                return False, "", 0.0, 0.0

            # Cooldown: don't augment if we augmented in the last 5 min
            last_aug_time = getattr(position, 'last_augmentation_time', None)
            if last_aug_time:
                try:
                    from datetime import datetime, timezone
                    if isinstance(last_aug_time, str):
                        from dateutil.parser import isoparse
                        lat = isoparse(last_aug_time)
                        if lat.tzinfo is None: lat = lat.replace(tzinfo=timezone.utc)
                        secs_since_aug = (datetime.now(timezone.utc) - lat).total_seconds()
                    else:
                        secs_since_aug = time.time() - float(last_aug_time)
                    if secs_since_aug < 300:
                        return False, "", 0.0, 0.0
                except Exception:
                    pass

            buf = 0.001
            tier_mults = {1: 1.0, 2: 2.0, 3: 3.0, 4: 5.0}
            active_tier = 0

            if is_long:
                dch5  = float(i.get('dc_high_5m', 0) or 0)
                dch15 = float(i.get('dc_high_15m', 0) or 0)
                dch1h = float(i.get('dc_high_1h', 0) or 0)
                dch4h = float(i.get('dc_high_4h', 0) or 0)
                if dch5  > 0 and current_price > dch5  * (1 + buf): active_tier = 1
                if dch15 > 0 and current_price > dch15 * (1 + buf): active_tier = 2
                if dch1h > 0 and current_price > dch1h * (1 + buf): active_tier = 3
                if dch4h > 0 and current_price > dch4h * (1 + buf): active_tier = 4
            else:
                dcl5  = float(i.get('dc_low_5m', 0) or 0)
                dcl15 = float(i.get('dc_low_15m', 0) or 0)
                dcl1h = float(i.get('dc_low_1h', 0) or 0)
                dcl4h = float(i.get('dc_low_4h', 0) or 0)
                if dcl5  > 0 and current_price < dcl5  * (1 - buf): active_tier = 1
                if dcl15 > 0 and current_price < dcl15 * (1 - buf): active_tier = 2
                if dcl1h > 0 and current_price < dcl1h * (1 - buf): active_tier = 3
                if dcl4h > 0 and current_price < dcl4h * (1 - buf): active_tier = 4

            if active_tier > 0:
                target_value = config.START_POSITION_SIZE * tier_mults[active_tier]
                if current_value < target_value * 0.75:
                    aug_value = target_value - current_value
                    aug_qty = aug_value / current_price
                    if aug_qty >= 0.5:
                        direction = "LONG" if is_long else "SHORT"
                        qty = await self.calculate_quantity_complex(symbol, "AUGMENT", direction, aug_qty, indicators, position, market_context)
                        if qty > 0:
                            tf_name = {1:"DC5M",2:"DC15M",3:"DC1H",4:"DC4H"}[active_tier]
                            return True, f"DC_TIER{active_tier}_{tf_name}_AUG target={target_value:.0f} cur={current_value:.0f}", 80.0, qty

            return False, "", 0.0, 0.0
        except Exception as e:
            logger.error(f"[evaluate_augment] {symbol}: {e}")
            return False, f"Error: {e}", 0.0, 0.0

    async def evaluate_reentry(self, symbol: str, position: Any, indicators: dict) -> tuple[str, str, float, float]:
        """
        STOCH_TURN re-entry (Grid test winner: Sharpe 27.7 for stocks).
        Simple and fast: k_5m crosses d_5m + k_15m confirms direction.
        30-bar cooldown after exit.
        """
        i = self.parse_market_data(indicators)
        current_price, _ = await self.trade_manager.get_current_price(symbol)
        if current_price is None:
            return "NO_ACTION", "No Price", 0.0, 0.0
        current_price = float(current_price)

        is_long = getattr(position, 'position_side', 'LONG') == 'LONG'
        positionAmt = abs(float(getattr(position, 'quantity', 0) or 0))
        entry_price = float(getattr(position, 'entry_price', current_price) or current_price)
        max_q = float(getattr(position, 'max_quantity', 0) or getattr(position, 'max_positionSize', 0) or positionAmt)
        last_red_time = getattr(position, 'last_reduction_time', None)
        gain = (current_price - entry_price) / entry_price * 100 if is_long else (entry_price - current_price) / entry_price * 100
        max_gain = max(gain, getattr(position, 'max_gain', 0))
        position.max_gain = max_gain
        position.gain = gain
        now = datetime.now(timezone.utc)

        # --- At max position? ---
        if max_q and positionAmt >= max_q:
            return "NO_ACTION", "At_max_qty", 0.0, 0.0

        # --- 30-bar cooldown (~30 min for 1m bars) after reduction ---
        last_red_age_min = 999.0
        if last_red_time:
            try:
                lt = last_red_time if not isinstance(last_red_time, str) else datetime.fromisoformat(str(last_red_time).replace("Z", "+00:00"))
                if lt.tzinfo is None: lt = lt.replace(tzinfo=timezone.utc)
                last_red_age_min = (now - lt).total_seconds() / 60.0
            except Exception: pass
        if last_red_age_min < 30.0:
            return "NO_ACTION", f"Reentry_cooldown_{30-last_red_age_min:.0f}m", 0.0, 0.0

        # === STOCH_TURN: k_5m crosses d_5m + k_15m confirms ===
        k_5m = float(i.get('stoch_k_5m', 50))
        d_5m = float(i.get('stoch_d_5m', 50))
        k_5m_prev = float(i.get('stoch_k_5m_prev', 50))
        k_15m = float(i.get('stoch_k_15m', 50))
        d_15m = float(i.get('stoch_d_15m', 50))
        k_1h = float(i.get('stoch_k_1h', 50))
        d_1h = float(i.get('stoch_d_1h', 50))
        if is_long:
            # k_5m crosses ABOVE d_5m + k_15m confirms bullish
            stoch_cross = (k_5m > d_5m) and (k_5m > k_5m_prev)
            k15_confirms = (k_15m > d_15m) or (k_15m < 30)  # Either aligned or oversold
            if not (stoch_cross and k15_confirms):
                return "NO_ACTION", f"Wait_StochTurn_L_k5:{k_5m:.0f}_d5:{d_5m:.0f}_k15:{k_15m:.0f}", 0.0, 0.0
        else:
            # k_5m crosses BELOW d_5m + k_15m confirms bearish
            stoch_cross = (k_5m < d_5m) and (k_5m < k_5m_prev)
            k15_confirms = (k_15m < d_15m) or (k_15m > 70)  # Either aligned or overbought
            if not (stoch_cross and k15_confirms):
                return "NO_ACTION", f"Wait_StochTurn_S_k5:{k_5m:.0f}_d5:{d_5m:.0f}_k15:{k_15m:.0f}", 0.0, 0.0

        # ── ALL GATES PASSED — DC tier sizing ──
        base_qty = config.START_POSITION_SIZE / max(current_price, 1e-9)
        if max_q: base_qty = min(base_qty * 1.5, max(1.0, max_q - positionAmt))

        dc_high_15m_val = float(i.get('dc_high_15m', 0))
        dc_low_15m_val = float(i.get('dc_low_15m', 0))
        dc_high_1h = float(i.get('dc_high_1h', 0))
        dc_low_1h = float(i.get('dc_low_1h', 0))
        dc_high_4h = float(i.get('dc_high_4h', 0))
        dc_low_4h = float(i.get('dc_low_4h', 0))

        size_mult = 1.0
        dc_info = "above_basis"
        if is_long:
            if dc_high_15m_val > 0 and current_price > dc_high_15m_val:
                size_mult = 2.0; dc_info = "above_DC15m"
            if dc_high_1h > 0 and current_price > dc_high_1h:
                size_mult = 3.0; dc_info = "above_DC1h"
            if dc_high_4h > 0 and current_price > dc_high_4h:
                size_mult = 5.0; dc_info = "above_DC4h"
        else:
            if dc_low_15m_val > 0 and current_price < dc_low_15m_val:
                size_mult = 2.0; dc_info = "below_DC15m"
            if dc_low_1h > 0 and current_price < dc_low_1h:
                size_mult = 3.0; dc_info = "below_DC1h"
            if dc_low_4h > 0 and current_price < dc_low_4h:
                size_mult = 5.0; dc_info = "below_DC4h"
        stoch_count = sum(k_5m>d_5m, k_15m>d_15m, k_1h>d_1h)
        qty = base_qty * size_mult
        confidence = 85.0 if stoch_count == 3 else 75.0
        reason = (f"REENTRY_SOLID stoch={stoch_count}/3 dc={dc_info} "
                  f"k5={k_5m:.0f} k15={k_15m:.0f} k1h={k_1h:.0f} mult={size_mult}x")

        return "REENTRY_OPEN", reason, confidence, qty

class DailyHistoryManager:
    def __init__(self, config):
        self.config = config
        self.file_path = config.DATA_DIR / "daily_klines.json"
        self.cache = {} # { "SYMBOL": { "2024-01-01": {open, high...}, ... } }
        self.lock = asyncio.Lock()
        self.dirty = False

    async def load(self):
        """Load accumulated history from disk"""
        try:
            if self.file_path.exists():
                async with aiofiles.open(self.file_path, 'r') as f:
                    content = await f.read()
                    if content.strip():
                        self.cache = json.loads(content)
                        logger.info(f"[History] Loaded daily data for {len(self.cache)} symbols")
        except Exception as e:
            logger.error(f"[History] Load error: {e}")

    async def save(self):
        """Save accumulated history to disk"""
        if not self.dirty: return
        try:
            async with self.lock:
                async with aiofiles.open(self.file_path, 'w') as f:
                    await f.write(json.dumps(self.cache, sort_keys=True))
                self.dirty = False
                logger.info("[History] Saved daily klines to disk")
        except Exception as e:
            logger.error(f"[History] Save error: {e}")

    async def update_symbol(self, symbol: str, api_history: List[Dict]):
        """Merge new API data with existing cache (deduplicate by date)"""
        if not api_history: return

        symbol = symbol.upper()
        if symbol not in self.cache: self.cache[symbol] = {}

        changed = False
        for candle in api_history:
            # Tradier history keys: date, open, high, low, close, volume
            date = candle.get('date')
            if not date: continue

            # Only update if missing or if data looks more complete
            if date not in self.cache[symbol]:
                self.cache[symbol][date] = candle
                changed = True

        if changed:
            self.dirty = True

    def calculate_daily_stats(self, symbol: str) -> Dict[str, float]:
        """Calculate SMA200, EMA20, ATR14 from cached history"""
        symbol = symbol.upper()
        if symbol not in self.cache: return {}

        # 1. Sort candles by date
        candles = sorted(self.cache[symbol].values(), key=lambda x: x['date'])
        if len(candles) < 20: return {} # Not enough data
        closes = [float(c['close']) for c in candles]
        highs = [float(c['high']) for c in candles]
        lows = [float(c['low']) for c in candles]
        stats = {}
        def calc_sma(period):
            if len(closes) < period: return 0.0
            return sum(closes[-period:]) / period

        # Helper: EMA
        def calc_ema(period):
            if len(closes) < period: return 0.0
            k = 2 / (period + 1)
            ema = sum(closes[:period]) / period # Start with SMA
            for price in closes[period:]:
                ema = (price * k) + (ema * (1 - k))
            return ema

        # Helper: ATR (Simplified WMA)
        def calc_atr(period=14):
            if len(closes) < period + 1: return 0.0
            tr_list = []
            for i in range(1, len(closes)):
                h, l, pc = highs[i], lows[i], closes[i-1]
                tr = max(h - l, abs(h - pc), abs(l - pc))
                tr_list.append(tr)

            # Simple average for initial, smoothed for rest (optional, keeping it simple SMA for robustness here)
            return sum(tr_list[-period:]) / period

        # Calculate Indicators
        stats['sma_200_D'] = calc_sma(200)
        stats['ema_20_D'] = calc_ema(20)
        stats['ema_50_D'] = calc_ema(50)
        stats['atr_D'] = calc_atr(14)

        # High/Low D (Last candle)
        stats['high_D'] = highs[-1]
        stats['low_D'] = lows[-1]

        # Donchian Daily (20 day)
        if len(highs) >= 20:
            stats['dc_high_D'] = max(highs[-20:])
            stats['dc_low_D'] = min(lows[-20:])

        return stats



class TradierTradeManager:
    def __init__(self, account_list: List[str] = None):
        self.config = config
        self.target_accounts = account_list or ['trc','trb']
        self.last_symbol_refresh = 0
        self.refresh_interval = 60  
        for acc in self.target_accounts:
            setattr(self, f"symbols_long_{acc}", [])
            setattr(self, f"symbols_short_{acc}", [])
        self.order_queue = OrderQueue(self)
        self.reader = PositionReader(config)
        self.position_manager = self.reader
        self.logger = logger
        self.api_client: Optional[TradierAPIClient] = None      
        self.non_shortable_symbols = config.NON_SHORTABLE
        self.always_tradeable_trb = config.ALWAYS_TRADEABLE
        self.exceptions = config.EXCEPTIONS
        self.blacklist = getattr(config, 'BLACKLIST', set())
        self.limit_normal_order = getattr(config, 'MAX_ORDER_VALUE', 2000.0) 
        self.limit_exception_order = self.limit_normal_order * 4.0  
        self.limit_total_pos = getattr(config, 'MAX_POSITION_SIZE', 5000.0)
        self.limit_exception_total_pos =  getattr(config, 'MAX_POSITION_SIZE', 5000.0) * 4.0  
        self.strategy = StockStrategy(config, trade_manager=self)
        self.running = False
        self.background_tasks = []
        self.account_position_queues = defaultdict(lambda: {'queue': deque(), 'set': set()})
        self.recently_processed_signals: Dict[str, float] = {}
        self.order_deduplication: Dict[str, float] = {}
        self.dedupe_lock = asyncio.Lock()
        self.active_order_locks = {}
        self.price_cache = {}
        self.price_update_time = {}
        self.managers: Dict[str, Any] = {} 
        self.symbols: List[str] = []
        self.last_exit_prices = {}
        self.last_exit_times = {}  # Track when positions were exited (symbol -> timestamp)
        # self.position_manager: Optional[TradierPositionManager] = None
        # Indicators cache (from ez_indicators)
        self.redis_manager = None
        self.indicators_cache: Dict[str, Dict] = {}
        self.last_indicators_update = 0.0
        self.redis_indicators_full_cache: Dict[str, Any] = {}
        self.redis_indicators_cache_time = 0.0
        self.last_breadth_calc_time = 0
        self.cached_breadth_score = 0.0
        self.rankings_cache: Dict[str, Any] = {}
        self.sentiment_history = deque(maxlen=40) # Keep last 10 readings
        self.last_sentiment_update = 0
        self.redis_client = None
        self.symbols_long_tra: List[str] = []
        self.symbols_short_tra: List[str] = []
        self.symbols_long_trb: List[str] = []
        self.symbols_short_trb: List[str] = []
        self.symbols_long_trc: List[str] = []
        self.symbols_short_trc: List[str] = []
        self.min_qty: Dict[str, float] = {}  # symbol -> min_qty (in shares) - set to 1 for now
        self.positions: Dict[str, Any] = {}  # position_key -> position
        self.positions_by_account: Dict[str, Dict[str, Any]] = defaultdict(dict)  # account_key -> {position_key -> position}
        self.processing_keys: TrackingSet = TrackingSet()
        self.position_last_processed: Dict[str, float] = {}
        self._gap_fill_positions: Dict[str, Dict] = {}
        self._gap_fill_ran_today: str = ''
        self.recently_queued_signals: Dict[str, float] = {}
        self.indicators_snapshot: Dict[str, Dict] = {}
        self.active_maker_orders: Dict[str, Dict] = {}
        self.orders_in_limbo: Dict[str, Dict[str, float]] = {}
        self.augmentation_cooldown_map: Dict[str, Any] = {}
        self.reduction_cooldown_map: Dict[str, Any] = {}
        self.augmented_positions: set = set()
        self.positions_to_gracefully_exit: set = set()
        self.tradeable_keys: set = set()
        self._last_tradeable_update: float = 0.0
        self.tradeable_lock = asyncio.Lock()
        self.stop_levels: Dict[str, List] = {}
        self.stop_breach_tracker: Dict[str, float] = {}
        self.accounts: Dict[str, Any] = {'tra': SimpleNamespace(account_key='tra')}
        self.active_order_locks: Dict[str, Dict[str, Any]] = {}
        self._local_locks: Dict[str, Dict[str, float]] = {}
        self.last_symbol_monitored: Dict[str, float] = defaultdict(float)
        self.last_monitored_positions: Dict[str, float] = defaultdict(float)
        self.price_cache_2: Dict[str, Dict[str, Any]] = {}
        self.price_cache_3: Dict[str, Dict[str, Any]] = {}
        self.queue_metrics: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.concurrency_health: Dict[str, Dict[str, float]] = defaultdict(dict)
        self._position_update_timestamps: Dict[str, datetime] = {}
        self._last_stale_reload_ts = 0.0
        self._metrics_runner = None
        self._metrics_site = None
        self.reversed_positions: Dict[str, float] = {}
        self.direct_high_gain: Dict[str, Any] = {}
        self.reenter_data: Dict[str, Any] = {}
        self.reentry_plans: Dict[str, Any] = {}
        self._stable_valid_keys_cache: Dict[str, set] = {}
        self.reduced_positions = {}
        self.position_validation_task = None
        self.rejection_cooldowns = {}
        if not hasattr(config, 'ACCOUNT_KEYS'):
            config.ACCOUNT_KEYS = ['trc','trb']
        if not hasattr(config, 'SLEEP_TIME_PER_TASKS'):
            config.SLEEP_TIME_PER_TASKS = 15
        self.market_snapshot = {}
        self._dc_range_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=8))
        self.last_market_data_ts = None  # To track the "version" of data
        self.last_json_mtime = 0   
        self._redis_circuit_broken = False
        self.clock_offset = 0.0 
        self.ready_to_trade = True

    async def initialize_all_position_fields(self):
        """Ensure ALL position fields are initialized"""
        if not self.position_manager:
            return
        for position_key, position in self.position_manager.positions.items():
            await self.validate_and_complete_position(position_key, position)

    def get_position(self, position_key: str):
        return self.reader.positions.get(position_key)
    # async def get_position(self, position_key: str) -> Optional[Any]:
    #     """Get position by position_key"""
    #     if not self.position_manager:
    #         return None
    #     # FIXED: Direct access to flat dictionary
    #     return self.position_manager.positions.get(position_key)
    #     try:
    #         if position_key.count(":") != 1:
    #             logger.error(f"[get_position] !! CORRUPTED position_key with {position_key.count(':')} colons: {position_key}")
    #             from utils import clean_position_key, orjson_default
    #             position_key = clean_position_key(position_key)
    #             if position_key.count(":") != 1:
    #                 return None
    #         parts = position_key.split(':', 1)
    #         if len(parts) != 2:
    #             return None
    #         account_key, symbol_side = parts
    #         symbol_side_parts = symbol_side.split('_', 1)
    #         if len(symbol_side_parts) != 2:
    #             return None
    #         symbol, position_side = symbol_side_parts
    #         return self.position_manager.positions.get(position_side, {}).get(symbol)
    #     except Exception:
    #         return None


    async def load_account_symbols(self, force=False):
        """
        Periodically syncs disk JSONs to memory.
        Trading loops should read self.symbols_long_tra, etc.
        """
        now = time.time()
        # Only touch the disk if forced or if interval has passed
        if not force and (now - self.last_symbol_refresh < self.refresh_interval):
            return

        try:
            base = Path(self.config.BASE_PATH)
            
            for acc in self.target_accounts:
                for side in ["long", "short"]:
                    file_path = base / f"symbols_{acc}_{side}.json"
                    
                    if file_path.exists():
                        # Non-blocking read
                        def read_file():
                            with open(file_path, 'r') as f:
                                return json.load(f)
                        
                        raw_data = await asyncio.to_thread(read_file)
                        
                        # Process data into a clean list
                        if isinstance(raw_data, list):
                            processed = [str(s).strip().upper() for s in raw_data if s]
                        elif isinstance(raw_data, dict):
                            processed = [str(k).strip().upper() for k in raw_data.keys() if k]
                        else:
                            processed = []

                        # Update the MEMORY variable (Atomic pointer swap)
                        setattr(self, f"symbols_{side}_{acc}", sorted(list(set(processed))))
            self.last_symbol_refresh = now
            logger.info(f"💾 [DISK_SYNC] Symbols reloaded into memory.")

        except Exception as e:
            logger.error(f"!! [SYMBOL_SYNC_FAIL] {e}")


    def update_sentiment_history(self, current_score: float):
        """Updates the rolling history of market sentiment."""
        # Only update if enough time has passed (e.g., 5 mins) to capture meaningful trends
        if time.time() - self.last_sentiment_update > 300:
            self.sentiment_history.append(current_score)
            self.last_sentiment_update = time.time()

    def get_sentiment_direction(self) -> str:
        """Returns 'IMPROVING', 'DEGRADING', or 'FLAT'"""
        if len(self.sentiment_history) < 3: return "FLAT"

        # Simple average of recent vs older to determine slope
        recent = sum(list(self.sentiment_history)[-3:]) / 3
        older = sum(list(self.sentiment_history)[:3]) / 3

        if recent > older + 5: return "IMPROVING"
        if recent < older - 5: return "DEGRADING"
        return "FLAT"

    def is_symbol_allowed(self, account_key: str, symbol: str, position_side: str) -> bool:
        symbol = symbol.upper().strip()

        # 1. ALWAYS ALLOW EXISTING POSITIONS (Management/Exit)
        pk = f"{account_key}:{symbol}_{position_side}"
        pos = self.position_manager.positions.get(pk)
        if pos and abs(float(getattr(pos, 'positionAmt', 0))) > 0:
            return True

        # 2. ACCOUNT SPECIFIC LOGIC

        # --- TRB (Leaderboards + Whitelist) ---
        if account_key == 'trb':
            # A. Check Whitelist
            if symbol in [s.upper() for s in self.always_tradeable_trb]:
                # Intersection Logic:
                in_winners = symbol in [s.upper() for s in self.symbols_long_trb]
                in_losers = symbol in [s.upper() for s in self.symbols_short_trb]

                # If "Nowhere" (not in either leaderboard) -> Allow BOTH sides
                if not in_winners and not in_losers:
                    return True

                # If it IS in a leaderboard, it must match that list's side
                if position_side == 'LONG' and in_winners: return True
                if position_side == 'SHORT' and in_losers: return True

                # If it's in Winners but we want Short -> Block
                return False

            # B. Check Standard Leaderboards for TRB
            if position_side == 'LONG':
                return symbol in [s.upper() for s in self.symbols_long_trb]
            elif position_side == 'SHORT':
                return symbol in [s.upper() for s in self.symbols_short_trb]

        # --- TRA (Strict Leaderboards) ---
        elif account_key == 'tra':
            if position_side == 'LONG':
                return symbol in [s.upper() for s in self.symbols_long_tra]
            elif position_side == 'SHORT':
                return symbol in [s.upper() for s in self.symbols_short_tra]

        # --- TRC (Strict Leaderboards) ---
        elif account_key == 'trc':
            if position_side == 'LONG':
                return symbol in [s.upper() for s in self.symbols_long_trc]
            elif position_side == 'SHORT':
                return symbol in [s.upper() for s in self.symbols_short_trc]
        return False

    def allows_side(self, account_key: str, position_side: str, symbol: str = None) -> bool:
        """Check if account allows trading this side - compatibility method for ez_manage"""
        # For Tradier, both LONG and SHORT are allowed
        return True

    def get_positions_by_account(self, account_key: str) -> Dict[str, Any]:
            """Get all positions for an account using flat dict"""
            if not self.position_manager:
                return {}

            result = {}
            # FIXED: Iterate the flat dictionary directly
            for pk, position in self.position_manager.positions.items():
                # Basic filter: check if key starts with account or assumes 'tra'
                if pk.startswith(f"{account_key}:") and position:
                    # FIXED: use positionAmt
                    if abs(float(getattr(position, 'positionAmt', 0))) >= 0:
                        result[pk] = position
            return result

    async def is_data_fresh(self, symbol: str, position_key: str = None) -> tuple[bool, str, bool, Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        indicators = self.get_indicators(symbol)
        ts_obj = indicators.get('timestamp_1m') or indicators.get('timestamp')
        if not isinstance(ts_obj, datetime):
            ts_obj = safe_datetime(ts_obj)
        if not ts_obj:
            return False, "No Timestamp", False, indicators
        age = (now - ts_obj).total_seconds()
        if age < -1.0: age = 0.1 
        m1_fresh = (age < 90.0)
        macro_fresh = (age < 1200.0)  # 20 min — indicators cycle is ~2-5 min
        return macro_fresh, f"Age:{age:.0f}s", m1_fresh, indicators

    def load_indicators_from_json(self, symbol: str) -> Dict[str, Any]:
        global _GLOBAL_JSON_CACHE, _GLOBAL_JSON_TS, _GLOBAL_JSON_LOCK
        try:
            symbol = symbol.strip().upper()
            now = time.time()
            
            with _GLOBAL_JSON_LOCK:
                if now - _GLOBAL_JSON_TS < 3.0 and _GLOBAL_JSON_CACHE:
                    return _GLOBAL_JSON_CACHE.get(symbol, {})

                search_dirs =[config.DATA_DIR, config.DATA_DIR / "tradier"]
                for d in search_dirs:
                    latest_static = d / "tradier_indicators_latest.json"
                    if latest_static.exists():
                        try:
                            with open(latest_static, 'rb') as f:
                                content = f.read()
                                if content.strip():
                                    data = safe_json_loads(content)
                                    if isinstance(data, dict):
                                        _GLOBAL_JSON_CACHE = data
                                        _GLOBAL_JSON_TS = now
                                        return data.get(symbol, {})
                        except Exception: pass
                candidates =[]
                import re
                pattern = re.compile(r"tradier_indicators_\d{8}_\d{6}\.json")
                for d in search_dirs:
                    if not d.exists(): continue
                    for f in d.iterdir():
                        if f.is_file() and pattern.match(f.name):
                            candidates.append(f)
                if not candidates: return {}
                candidates.sort(key=lambda p: p.name, reverse=True)
                for file_path in candidates[:3]:
                    try:
                        with open(file_path, 'rb') as f:
                            content = f.read()
                            if not content.strip(): continue
                            data = safe_json_loads(content)
                            if isinstance(data, dict):
                                _GLOBAL_JSON_CACHE = data
                                _GLOBAL_JSON_TS = now
                                return data.get(symbol, {})
                    except (JSONDecodeError, OSError):
                        continue 
            return {}
        except Exception as e:
            logger.error(f"[JSON_LOAD] Error loading for {symbol}: {e}")
            return {}

    async def ensure_position_present(self, account_key: str, symbol: str, position_side: str, position_key: str) -> Optional[Any]:
        """Return existing position from memory. NEVER fabricate empty positions — positions are created once when symbol is added."""
        if not self.position_manager:
            return None
        position = self.position_manager.positions.get(position_key)
        if not position:
            logger.debug(f"[ensure_position_present] {position_key} not in memory — searching disk/backups")
            try:
                side_file = f"{position_side.lower()}_positions.json"
                file_path = Path(config.BASE_PATH) / account_key / side_file
                if file_path.exists():
                    import json as _json
                    with open(file_path) as f:
                        data = _json.load(f)
                    if isinstance(data, dict) and 'positions' in data: data = data['positions']
                    pos_data = data.get(position_key)
                    if pos_data and isinstance(pos_data, dict):
                        from tradier_positions import TradierPosition
                        position = TradierPosition.from_dict(pos_data) if hasattr(TradierPosition, 'from_dict') else TradierPosition(**{k: v for k, v in pos_data.items() if k in TradierPosition.__dataclass_fields__}) if hasattr(TradierPosition, '__dataclass_fields__') else None
                        if position:
                            self.position_manager.positions[position_key] = position
                            logger.debug(f"[ensure_position_present] Loaded {position_key} from disk")
            except Exception as e:
                logger.debug(f"[ensure_position_present] Disk load failed for {position_key}: {e}")
        return position

    async def periodic_sentiment_rebalancing(self):
        """
        Scans all positions. Calculates ideal size based on CURRENT sentiment.
        Reduces size if sentiment degrades. Augments if sentiment improves.
        """
        logger.info("[sentiment_rebalancer] Task started")
        while self.running:
            try:
                # Run every 60 seconds (or 2m) to avoid over-trading
                await asyncio.sleep(60)

                if not self.position_manager: continue

                # Iterate a COPY of the items to avoid modification issues
                current_positions = list(self.position_manager.positions.items())

                for pk, pos in current_positions:
                    try:
                        # 1. Basic Parse
                        if not pos or abs(getattr(pos, 'positionAmt', 0)) == 0: continue

                        symbol = getattr(pos, 'symbol', '')
                        side = getattr(pos, 'position_side', 'LONG')
                        current_qty = abs(float(pos.positionAmt))

                        current_price = float(getattr(pos, 'mark_price', 0))

                        if current_price <= 0: current_price,ts=await self.get_current_price(symbol)
                        current_price = float(current_price)

                        # Cooldown: skip if reduced or augmented within last 30 min
                        _rebal_cooldown_s = 30 * 60
                        _last_red = getattr(pos, 'last_reduction_time', None)
                        _last_aug = getattr(pos, 'last_augmentation_time', None)
                        _now_ts = time.time()
                        def _age_s(dt):
                            if not dt: return 9999
                            try:
                                ts_ = dt.timestamp() if hasattr(dt, 'timestamp') else float(dt)
                                return _now_ts - ts_
                            except Exception: return 9999
                        if _age_s(_last_red) < _rebal_cooldown_s or _age_s(_last_aug) < _rebal_cooldown_s:
                            continue

                        # 2. Get Fresh Indicators
                        i = self.get_indicators(symbol)
                        if not i: continue
                        base_qty = config.START_POSITION_SIZE / current_price
                        ideal_qty = await self.strategy.calculate_quantity_complex(
                            symbol, "REBALANCE", side, base_qty, i, pos)
                        if current_qty == 0: continue
                        deviation_pct = (ideal_qty - current_qty) / current_qty
                        if deviation_pct < -0.20:
                            qty_to_reduce = current_qty - ideal_qty
                            if qty_to_reduce * current_price > 100: # Only if moving > $100
                                reason = f"SENTIMENT_FADE ideal={int(ideal_qty)} cur={int(current_qty)} loc={i.get('0market_sentiment_local',0):.1f}"
                                logger.info(f"📉 {symbol} REBALANCE REDUCE: {reason}")
                                await self.execute_trade_action(
                                    'tra', pk, symbol, qty_to_reduce, current_price,
                                    "SELL" if side == "LONG" else "BUY",
                                    side, f"rebal_{int(time.time())}",
                                    action="REDUCE", reason=reason, override_qty=qty_to_reduce)
                        elif deviation_pct > 0.25:
                            # Use higher threshold for adding to be conservative
                            qty_to_add = ideal_qty - current_qty

                            # Check Profitability Gate (don't add to losers usually, unless deep value strategy)
                            # Assuming trend following: only add to winners
                            pnl_pct = getattr(pos, 'gain', 0)
                            if pnl_pct > 0.5: # Only add if slightly green
                                if qty_to_add * current_price > 100:
                                    reason = f"SENTIMENT_BOOST ideal={int(ideal_qty)} cur={int(current_qty)} loc={i.get('0market_sentiment_local',0):.1f}"
                                    logger.info(f"📈 {symbol} REBALANCE AUGMENT: {reason}")
                                    await self.execute_trade_action(
                                        'tra', pk, symbol, qty_to_add, current_price,
                                        "BUY" if side == "LONG" else "SELL",
                                        side, f"rebal_{int(time.time())}",
                                        action="AUGMENT", reason=reason, override_qty=qty_to_add  )

                    except Exception as inner_e:
                        logger.error(f"[rebalancer] Error processing {pk}: {inner_e}")
                        continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[rebalancer] Global Error: {e}",  exc_info=True )
                await asyncio.sleep(10)

    async def get_market_context(self, symbol: str) -> dict:
        """
        Determines market bias based on SPY and QQQ.
        Returns a dict: {'bias': float (-1.0 to 1.0), 'is_tech': bool}
        """
        # 1. Determine if target is likely Tech (Heavy QQQ correlation)
        # Simple list for example - ideally load from a config/json
        tech_tickers = {'NVDA', 'AMD', 'TSLA', 'AAPL', 'MSFT', 'GOOG', 'META', 'AMZN', 'NFLX', 'INTC', 'QCOM', 'MU'}
        is_tech = symbol.upper() in tech_tickers

        # 2. Fetch Index Data
        spy_ind = self.get_indicators("SPY")
        qqq_ind = self.get_indicators("QQQ")

        bias_score = 0.0

        # Helper to calculate score for an index
        def calc_index_score(ind):
            if not ind: return 0.0
            score = 0.0
            # 15m Trend (Primary)
            price = float(ind.get('current_price', 0) or ind.get('close_15m', 0))
            ema20 = float(ind.get('ema_20_15m', 0))
            if price > ema20: score += 0.5
            else: score -= 0.5

            # Momentum (Secondary)
            ha = ind.get('ha_15m', 'neutral')
            if ha == 'green': score += 0.5
            elif ha == 'red': score -= 0.5

            return score

        # 3. Weighting
        spy_score = calc_index_score(spy_ind)
        qqq_score = calc_index_score(qqq_ind)

        if is_tech:
            # If Tech, weight QQQ 70%, SPY 30%
            bias_score = (qqq_score * 0.7) + (spy_score * 0.3)
        else:
            # Otherwise, weight SPY 70%, QQQ 30%
            bias_score = (spy_score * 0.7) + (qqq_score * 0.3)

        return {'bias': bias_score, 'is_tech': is_tech}

    # --------------------------------------------------------------------------
    # PORTFOLIO BALANCING LOGIC
    # --------------------------------------------------------------------------
    def get_market_movement_intensity(self) -> float:
        snap = self.market_snapshot
        if not snap: return 50.0
        
        movement_components = []
        for symbol, data in snap.items():
            symbol_score = 0
            count = 0
            price = float(data.get('current_price', 0))
            if price <= 0: continue

            # 1. Volatility (ATR)
            atr15, atr5 = float(data.get('atr_15m', 0)), float(data.get('atr_5m', 0))
            if atr15 > 0:
                symbol_score += min((atr15 / price) * 1000, 50); count += 1
            if atr5 > 0:
                symbol_score += min((atr5 / price) * 1000, 30); count += 1

            # 2. Relative Volume
            rv15, rv5 = float(data.get('rel_vol_15m', 1.0)), float(data.get('rel_vol_5m', 1.0))
            if rv15 > 1.0: symbol_score += (rv15 - 1.0) * 20; count += 1
            if rv5 > 1.0: symbol_score += (rv5 - 1.0) * 10; count += 1

            # 3. RSI Momentum
            rsi15, rsi5 = float(data.get('rsi_15m', 50)), float(data.get('rsi_5m', 50))
            symbol_score += (abs(rsi15 - 50) / 50.0) * 10; count += 1
            symbol_score += (abs(rsi5 - 50) / 50.0) * 5; count += 1

            if count > 0:
                movement_components.append(min(symbol_score / count, 100))

        return sum(movement_components) / len(movement_components) if movement_components else 50.0

    def calculate_unified_market_ratio(self) -> float:
        snap = self.market_snapshot
        if not snap: return 0.5
        bullish_count, total_valid = 0, 0
        global_sent_total, sent_count = 0.0, 0
        
        for sym, data in snap.items():
            price = float(data.get('current_price', 0))
            ema15 = float(data.get('ema_20_15m', 0)) # Intraday anchor
            sent = float(data.get('0market_sentiment_score', 0))
            
            if price > 0 and ema15 > 0:
                total_valid += 1
                if price > ema15: bullish_count += 1
            
            if abs(sent) > 0:
                global_sent_total += sent
                sent_count += 1

        # Calculate Components
        breadth_bias = (bullish_count / total_valid) if total_valid > 0 else 0.5
        
        # B. Scraper Sentiment (-100 to 100 mapped to 0 to 1.0)
        avg_global_sent = (global_sent_total / sent_count) if sent_count > 0 else 0.0
        sentiment_ratio = (avg_global_sent + 100) / 200.0
        
        # C. Intensity Scaling
        # If intensity is high, we lean harder into the bias. 
        # If intensity is low, we stay near 0.5 (Neutral).
        intensity = self.get_market_movement_intensity() / 100.0 # 0.0 to 1.0
        
        # Weighted Fusion
        # 40% Breadth, 40% Scraper Sentiment, 20% Intensity
        base_ratio = (breadth_bias * 0.5) + (sentiment_ratio * 0.5)
        
        # Exaggerate: amplify the ratio signal 2x, then dampen slightly by intensity
        # Old: pulled hard toward 0.5 → killed the signal
        _ratio_mult = getattr(config, 'RATIO_MULTIPLIER_TRADIER', 3.5)  # BACKTEST_CHANGE_T61: was 2.0. 3.5x = best Sharpe in 65-config sweep
        amplified = 0.5 + (base_ratio - 0.5) * _ratio_mult
        final_ratio = 0.5 + (amplified - 0.5) * max(intensity, 0.5)

        return max(0.05, min(0.95, final_ratio))

    def get_custom_universe_index(self) -> dict:
        """Build a custom market index from ALL our traded symbols.
        Returns breadth (A/D ratio), avg change, and a composite score
        that's a better market proxy than SPY/QQQ for our specific universe."""
        try:
            snap = self.market_snapshot
            if not snap:
                return {'breadth': 0.0, 'avg_change_1h': 0.0, 'up': 0, 'down': 0, 'composite': 0.0}
            up, down, flat = 0, 0, 0
            changes_1h = []
            for sym, d in snap.items():
                cur = float(d.get('current_price', 0) or 0)
                prev = float(d.get('close_1h_prev', 0) or 0)
                if cur > 0 and prev > 0:
                    pct = (cur - prev) / prev * 100
                    changes_1h.append(pct)
                    if pct > 0.1: up += 1
                    elif pct < -0.1: down += 1
                    else: flat += 1
            total = up + down + flat
            if total == 0:
                return {'breadth': 0.0, 'avg_change_1h': 0.0, 'up': 0, 'down': 0, 'composite': 0.0}
            breadth = (up - down) / total  # -1 to +1
            avg_change = sum(changes_1h) / len(changes_1h) if changes_1h else 0.0
            # Composite: breadth * 50 + avg_change * 20, clamped to -100..+100
            composite = max(-100.0, min(100.0, breadth * 50 + avg_change * 20))
            return {
                'breadth': breadth,
                'avg_change_1h': avg_change,
                'up': up, 'down': down, 'flat': flat,
                'total': total,
                'ad_ratio': up / max(down, 1),
                'composite': composite
            }
        except Exception as e:
            logger.debug(f"[custom_universe_index] Error: {e}")
            return {'breadth': 0.0, 'avg_change_1h': 0.0, 'up': 0, 'down': 0, 'composite': 0.0}

    def get_current_portfolio_balance(self) -> dict:
        """Calculates current Long/Short dollar exposure percentage."""
        long_val, short_val = 0.0, 0.0
        
        for pk, pos in self.position_manager.positions.items():
            qty = abs(float(getattr(pos, 'positionAmt', 0)))
            if qty <= 0: continue
            
            # Use memory price
            price_data = self.get_indicators(pos.symbol)
            price = float(price_data.get('current_price', 0))
            if price <= 0: continue
            
            val = qty * price
            if pk.endswith("_LONG"): long_val += val
            else: short_val += val
            
        total = long_val + short_val
        if total <= 0: return {'long_pct': 0.5, 'short_pct': 0.5, 'total': 0.0}
        return {
            'long_pct': long_val / total,
            'short_pct': short_val / total,
            'total': total  }
  
    async def get_portfolio_exposure_ratio(self) -> dict:
        """
        Calculates current Long vs Short exposure in dollars.
        Returns dict with percentages.
        """
        total_long_value = 0.0
        total_short_value = 0.0

        if self.position_manager:
            for pk, pos in self.position_manager.positions.items():
                qty = abs(float(getattr(pos, 'positionAmt', 0) or 0))
                current_price,_ = await self.get_current_price(pos.symbol)
                current_price = float(current_price)

                if qty > 0 and current_price > 0:
                    val = qty * current_price
                    # Parse side from key (tra:AAPL_LONG)
                    if "_LONG" in pk:
                        total_long_value += val
                    elif "_SHORT" in pk:
                        total_short_value += val

        total_exposure = total_long_value + total_short_value

        if total_exposure <= 0:
            return {'long_pct': 0.0, 'short_pct': 0.0, 'total_exposure': 0.0}

        return {
            'long_pct': (total_long_value / total_exposure),
            'short_pct': (total_short_value / total_exposure),
            'total_exposure': total_exposure  }

    def get_wing_exposure(self, wing: str, side: str) -> float:
        """Return current dollar exposure for a given trade wing + side."""
        total = 0.0
        if not self.position_manager: return total
        for pk, pos in self.position_manager.positions.items():
            if getattr(pos, 'trade_wing', 'swing') != wing: continue
            if side.upper() not in pk.upper(): continue
            qty = abs(float(getattr(pos, 'positionAmt', 0)))
            if qty <= 0: continue
            price_data = self.get_indicators(pos.symbol)
            price = float(price_data.get('current_price', 0))
            if price > 0: total += qty * price
        return total

    def is_swing_entry_allowed(self, side: str, order_value: float) -> tuple:
        """
        Check if a swing entry fits within the ratio-adjusted budget.
        ratio=0.7 → long cap ×140%, short cap ×60%. Clamped to ±50% of base.
        Returns (allowed: bool, reason: str).
        """
        ratio = self.calculate_unified_market_ratio()
        if side == "LONG":
            base = getattr(config, 'SWING_LONG_BUDGET', 20000.0)
            effective = min(base * 1.5, base * (ratio / 0.5))
        else:
            base = getattr(config, 'SWING_SHORT_BUDGET', 20000.0)
            effective = min(base * 1.5, base * ((1.0 - ratio) / 0.5))
        current = self.get_wing_exposure('swing', side)
        if current + order_value > effective:
            return False, f"SwingBudget_{side} {current:.0f}+{order_value:.0f}>{effective:.0f}"
        return True, ""

    def check_pullback_reexpansion(self, symbol: str, i_raw: dict) -> tuple:
        """
        Detects the pullback-reexpansion setup across all symbols:
          1. HTF range peaked: 15m (or 1h) DC range was expanding, now contracting.
          2. Price at key level: dc_basis_15m ±0.5% OR sma_200_1m ±0.3%.
          3. STF contracted: 5m and 1m DC ranges have tightened vs 2 readings ago.
          4. Reigniting: 1m DC range just started expanding again (coil breaking).

        Score 4 = perfect; 3 = strong. Returns (is_setup, score, direction, detail).
        direction: "LONG" if price >= sma_200_1m, "SHORT" otherwise.
        Updates self._dc_range_history[symbol] (maxlen=8, one entry per ~10s cycle).
        """
        def _r(hk, lk):
            h = safe_fetch_float(i_raw.get(hk, 0))
            l = safe_fetch_float(i_raw.get(lk, 0))
            return (h - l) / l if h > 0 and l > 0.001 else 0.0
        ratios = {
            '1m':  _r('dc_high_1m',  'dc_low_1m'),
            '5m':  _r('dc_high_5m',  'dc_low_5m'),
            '1h':  _r('dc_high_1h',  'dc_low_1h'),
        }
        hist = self._dc_range_history[symbol]
        hist.append({'ts': time.time(), **ratios})
        if len(hist) < 3:
            return False, 0, "", "insufficient_history"
        prev = hist[-2]
        prev2 = hist[-3]
        price = safe_fetch_float(i_raw.get('current_price', 0))
        basis_1h = safe_fetch_float(i_raw.get('dc_basis_1h', 0))
        sma200 = safe_fetch_float(i_raw.get('sma_200_1m', 0))
        # Condition 1 – HTF range peaked (was rising, now falling)
        peaked_5m = prev2['5m'] < prev['5m'] > ratios['5m'] and prev['5m'] > 0.003
        peaked_1h = len(hist) >= 4 and hist[-4]['1h'] < prev2['1h'] < prev['1h'] > ratios['1h']
        htf_peaked = peaked_5m or peaked_1h
        # Condition 2 – Price at key support/resistance level
        at_basis = basis_1h > 0 and abs(price - basis_1h) / basis_1h < 0.008
        at_sma200 = sma200 > 0 and abs(price - sma200) / sma200 < 0.003
        at_key_level = at_basis or at_sma200
        # Condition 3 – Short-term contracted vs 2 cycles ago
        tight_5m = ratios['5m'] < prev2['5m'] * 0.85 and prev2['5m'] > 0.001
        tight_1m = ratios['1m'] < prev2['1m'] * 0.80 and prev2['1m'] > 0.001
        contracted = tight_5m or tight_1m
        # Condition 4 – 1m range reigniting (expanding from tight base)
        reigniting = ratios['1m'] > prev['1m'] * 1.20 and ratios['1m'] > 0.0008
        score = sum([htf_peaked, at_key_level, contracted, reigniting])
        detail = f"peaked={int(htf_peaked)} key={int(at_key_level)} tight={int(contracted)} fire={int(reigniting)} 5m={ratios['5m']:.4f} 1m={ratios['1m']:.4f}"
        if score < 3:
            return False, score, "", detail
        direction = "LONG" if (sma200 <= 0 or price >= sma200) else "SHORT"
        return True, score, direction, detail

    def _check_htf_confirmation(self, is_long: bool, indicators: Dict, k_1h: float, k_1h_prev: float, k_4h: float, k_4h_prev: float, k_15m: float, k_15m_prev: float, d_15m: float, k_5m: float, k_5m_prev: float, wt1_5m: float, wt2_5m: float, wt1_15m: float, wt2_15m: float, current_price: float, sma_200_1m: float, sma_200_1m_prev: float, config) -> bool:
        """Replicate ez_manage HTF confirmation logic - returns True when trade should be blocked."""
        if getattr(config, 'EXTREME_MODE', False):
            return False
        if not getattr(config, 'HTF1_CONF', False) and not getattr(config, 'HTF4_CONF', False):
            return False
        def _f(val, default=0.0):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default
        i = indicators or {}
        d_1h = _f(i.get('stoch_d_1h', 0))
        d_4h = _f(i.get('stoch_d_4h', 0))
        high_1h = _f(i.get('high_1h', 0)); high_1h_prev = _f(i.get('high_1h_prev', 0))
        low_1h = _f(i.get('low_1h', 0)); low_1h_prev = _f(i.get('low_1h_prev', 0))
        high_4h = _f(i.get('high_4h', 0)); high_4h_prev = _f(i.get('high_4h_prev', 0))
        low_4h = _f(i.get('low_4h', 0)); low_4h_prev = _f(i.get('low_4h_prev', 0))
        high_15m = _f(i.get('high_15m', 0)); high_15m_prev = _f(i.get('high_15m_prev', 0))
        low_15m = _f(i.get('low_15m', 0)); low_15m_prev = _f(i.get('low_15m_prev', 0))
        ha_1h = i.get('ha_1h', i.get('ha_15m', 'neutral')) or 'neutral'
        ha_4h = i.get('ha_4h', i.get('ha_1h', 'neutral')) or 'neutral'
        blocked = False
        if getattr(config, 'HTF1_CONF', False):
            higher_high_1h = high_1h > high_1h_prev and (low_1h > low_1h_prev or ha_1h == 'green')
            higher_high_15m = high_15m > high_15m_prev and low_15m > low_15m_prev
            lower_low_1h = high_1h < high_1h_prev and low_1h < low_1h_prev
            lower_low_15m = high_15m < high_15m_prev and low_15m < low_15m_prev
            if is_long:
                if not (k_1h > d_1h or (higher_high_1h and higher_high_15m)):
                    blocked = True
            else:
                if not (k_1h < d_1h or (lower_low_1h and lower_low_15m)):
                    blocked = True
        if getattr(config, 'HTF4_CONF', False):
            higher_high_4h = high_4h > high_4h_prev and (low_4h > low_4h_prev or ha_4h == 'green')
            higher_high_15m = high_15m > high_15m_prev and low_15m > low_15m_prev
            lower_low_4h = (high_4h < high_4h_prev or ha_4h == 'red') and low_4h < low_4h_prev
            lower_low_15m = high_15m < high_15m_prev and low_15m < low_15m_prev
            if is_long:
                if not (k_4h > d_4h or (higher_high_4h and higher_high_15m)):
                    blocked = True
            else:
                if not (k_4h < d_4h or (lower_low_4h and lower_low_15m)):
                    blocked = True
        if blocked:
            logger.debug(f"[HTF_CHECK] block={blocked} is_long={is_long} htf1={getattr(config,'HTF1_CONF',False)} htf4={getattr(config,'HTF4_CONF',False)} k_1h={k_1h:.1f} k_4h={k_4h:.1f}")
        return blocked

    def load_symbols(self) -> List[str]:
        try:
            if config.SYMBOLS_FILE.exists():
                with open(config.SYMBOLS_FILE, 'r') as f:
                    symbols = json.load(f)
                    if isinstance(symbols, list): return symbols
                    return []
            return []
        except Exception as e:
            logger.error(f"Error loading symbols: {e}")
            return []
    def _adapt_indicators_for_ez_manage(self, i: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(i, dict): return {}
        
        # Standard Mappings
        i['stoch_k_3m'] = i.get('stoch_k_5m')
        i['stoch_d_3m'] = i.get('stoch_d_5m')
        
        # Punctual Timestamp Fix: Convert all your specific JSON keys to UTC Datetimes
        ts_keys = ['timestamp', 'timestamp_1m', 'timestamp_5m', 'timestamp_15m', 'timestamp_1h', 'timestamp_4h']
        for k in ts_keys:
            if k in i and isinstance(i[k], str):
                i[k] = safe_datetime(i[k])    
        return i

    # def _adapt_indicators_for_ez_manage(self, i: Dict[str, Any]) -> Dict[str, Any]:
    #     if not isinstance(i, dict): return {}
    #     i['stoch_k_3m'] = i.get('stoch_k_5m')
    #     i['stoch_d_3m'] = i.get('stoch_d_5m')
    #     for ts_key in ['timestamp', 'timestamp_1m', 'timestamp_5m', 'timestamp_15m', 'timestamp_1h', 'timestamp_4h', 'timestamp_D']:
    #         if ts_key in i and isinstance(i[ts_key], str):
    #             i[ts_key] = safe_datetime(i[ts_key])
    #     adapted = i.copy()
    #     now = datetime.now(timezone.utc)
    #     ts_1m_val = adapted.get('1m_updated_at') or adapted.get('timestamp_1m')
    #     if ts_1m_val:
    #         dt_1m = safe_datetime(ts_1m_val)
    #         if dt_1m and (now - dt_1m).total_seconds() > 120.0:
    #             # Remove 1m keys to prevent stale decision making, but keep timestamps for accurate Age logging
    #             m1_keys = [k for k in adapted.keys() if '_1m' in k or k in ['k_1m', 'd_1m']]
    #             m1_keys = [k for k in m1_keys if k not in ['1m_updated_at', 'timestamp_1m']]
    #             for k in m1_keys: adapted.pop(k, None)
    #     mappings_1m = {  'k_1m': 'stoch_k_1m', 'd_1m': 'stoch_d_1m',  'k_1m_prev': 'stoch_k_1m_prev', 'd_1m_prev': 'stoch_d_1m_prev' }
    #     for target_key, source_key in mappings_1m.items():
    #         if source_key in adapted:
    #             adapted[target_key] = adapted[source_key]
    #     return adapted

    async def _expire_limbo_order(self, position_key: str, side: str, delay_seconds: float):
        """Remove order from limbo dict after delay if not confirmed"""
        await asyncio.sleep(delay_seconds)
        if position_key in self.orders_in_limbo and side in self.orders_in_limbo[position_key]:
            limbo_time = self.orders_in_limbo[position_key][side]
            if time.time() - limbo_time >= delay_seconds:
                del self.orders_in_limbo[position_key][side]
                if not self.orders_in_limbo[position_key]:
                    del self.orders_in_limbo[position_key]
                logger.debug(f"[{position_key}] Limbo order expired (not confirmed): {side}")
                # CRITICAL: Clear deduplication entry since order was NOT executed
                async with self.dedupe_lock:
                    if position_key in self.order_deduplication:
                        self.order_deduplication.pop(position_key, None)
                        logger.debug(f"[CLEAR_DEDUPE_NOT_EXECUTED] {position_key}: Cleared deduplication entry (order not executed)")

    def validate_order_side(self, side: str, position_side: str, action: str, position_key: str) -> tuple[str, bool]:
        is_opening_or_augmenting = action in ['OPEN', 'QUICK_OPEN', 'AUGMENT', 'REENTER']
        is_reducing_or_closing = action in ['REDUCE', 'QUICK_CLOSE', 'PROFIT_TAKE', 'CLOSE']
        if is_opening_or_augmenting:
            expected_side = "BUY" if position_side == "LONG" else "SELL"
        elif is_reducing_or_closing:
            expected_side = "SELL" if position_side == "LONG" else "BUY"
        else:
            expected_side = "BUY" if position_side == "LONG" else "SELL"
        if side == expected_side:
            return side, True
        logger.error(f"🚨 CRITICAL ORDER SIDE ERROR: {position_key}")
        logger.error(f"   Position: {position_side}, Action: {action}")
        logger.error(f"   Received side: {side}, Expected side: {expected_side}")
        logger.error(f"   CORRECTING to: {expected_side}")
        return expected_side, False
   
    def get_indicators(self, symbol: str, use_cache: bool = True) -> Dict[str, Any]:
        symbol = symbol.strip().upper()
        data = self.market_snapshot.get(symbol, {})
        if not data: return {}
        price_entry = self.price_cache.get(symbol)
        if price_entry:
            data['current_price'] = price_entry['price']
        ts_raw = data.get('timestamp_1m') or data.get('timestamp')
        if ts_raw:
            dt_obj = ts_raw if isinstance(ts_raw, datetime) else safe_datetime(ts_raw)
            if dt_obj:
                now_utc = datetime.now(timezone.utc)
                true_lag = (now_utc - dt_obj).total_seconds()
                if true_lag < -1.0:
                    logger.debug(f"[{symbol}] Scraper ahead of Manager by {abs(true_lag):.1f}s")
                    true_lag = 0.1 
                if true_lag > 300:
                    logger.warning(f"[get_indicators] [{symbol}] Snapshot internal age is stale ({true_lag:.1f}s)")
        return data

    def _get_full_market_snapshot(self) -> Dict[str, Dict]:
        return self.market_snapshot
   
    async def _on_indicators_update(self, data: Any):
        """FAST-PATH: Instant update via Redis message."""
        try:
            payload = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(payload, dict) and len(payload) > 0:
                # Update individual keys so the existing dictionary reference stays alive
                # but the content is fresh.
                for sym, vals in payload.items():
                    self.market_snapshot[sym.upper()] = self._adapt_indicators_for_ez_manage(vals)
                
                # Update mtime tracker to match (prevents the Poller from re-reading same data)
                self.last_json_mtime = time.time()
                
                if not self.ready_to_trade:
                    self.ready_to_trade = True
                    logger.info("🚀 [SYSTEM] Ready via PubSub.")
        except Exception as e:
            logger.error(f"PubSub Update Error: {e}")



    def _finalize_indicators(self, symbol: str, raw_indicators: dict) -> dict:
        """Helper to normalize data and inject real-time price"""
        if not raw_indicators: return {}

        # 1. Normalize (Timestamps & Floats)
        final = self._normalize_indicators(raw_indicators)

        # 2. Inject Price Cache (if available locally and fresher)
        # Fixes "0.00" price logs
        price_entry = self.price_cache.get(symbol, {})
        cached_price = price_entry.get('current_price', 0)

        if cached_price > 0:
            final['current_price'] = cached_price
            final['current_price'] = cached_price
        return final

    def _normalize_indicators(self, indicators: Dict[str, Any]) -> Dict[str, Any]:
        if not indicators or not isinstance(indicators, dict):
            return {}
        adapted = indicators.copy()
        ts_keys = ['timestamp', 'timestamp_1m', 'timestamp_5m', 'timestamp_15m', '1m_updated_at']
        for k in ts_keys:
            if k in adapted and isinstance(adapted[k], str):
                try:
                    dt = isoparse(adapted[k])
                    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                    adapted[k] = dt
                except Exception: pass

        # 2. NUMERIC CONVERSION
        float_keys = [
            'current_price', 'current_price',
            'stoch_k_1m', 'stoch_d_1m', 'stoch_k_5m', 'stoch_d_5m',
            'rsi_1m', 'rsi_5m', '0sentiment_rank'
        ]
        for k in float_keys:
            if k in adapted:
                try: adapted[k] = float(adapted[k])
                except Exception: pass
        return adapted

    async def last_hour_balancing_loop(self):
        logger.info("⚖️ [TASK] Afternoon Slim-Down Balancer Active")
        while self.running:
            try:
                est = timezone(timedelta(hours=-5))
                now_est = datetime.now(timezone.utc).astimezone(est)
                
                if now_est.weekday() < 5 and dt_time(15, 15) <= now_est.time() <= dt_time(15, 58):
                    
                    # PUNCTUAL FIX: Removed 'await' because these are now synchronous memory lookups
                    target_long_pct = self.calculate_unified_market_ratio()
                    balance = self.get_current_portfolio_balance()
                    
                    actual_long_pct = balance['long_pct']
                    deviation = actual_long_pct - target_long_pct

                    if abs(deviation) > 0.10:
                        side_to_trim = "LONG" if deviation > 0 else "SHORT"
                        
                        candidates = []
                        for pk, pos in self.position_manager.positions.items():
                            if side_to_trim in pk and abs(float(getattr(pos, 'positionAmt', 0))) > 0:
                                candidates.append((pk, pos))
                        
                        candidates.sort(key=lambda x: getattr(x[1], 'gain', 0))
                        
                        if candidates:
                            target_pk, target_pos = candidates[0]
                            qty = abs(float(target_pos.positionAmt))
                            trim_qty = max(1, int(qty * 0.30))
                            
                            # PUNCTUAL FIX: Removed 'await'
                            price_data = self.get_indicators(target_pos.symbol)
                            price = float(price_data.get('current_price', 0))
                            
                            await self.execute_trade_action(
                                account_key=target_pk.split(':')[0],
                                position_key=target_pk,
                                symbol=target_pos.symbol,
                                quantity=trim_qty,
                                current_price=price,
                                side="SELL" if side_to_trim == "LONG" else "BUY",
                                position_side=side_to_trim,
                                unique_id=f"slim_{int(time.time())}",
                                action="REDUCE",
                                reason=f"EOD_SLIM_RATIO_{target_long_pct:.2f}",
                                override_qty=trim_qty
                            )

                await asyncio.sleep(240) 
            except Exception as e:
                # This catches the "object dict" error and prevents loop death
                logger.error(f"Balancer Error: {e}")
                await asyncio.sleep(60)
                
                
    async def get_rankings(self) -> Dict[str, Any]:
        """Get rankings from Redis or cache"""
        try:
            if not self.redis_manager:
                try:
                    self.redis_manager = await asyncio.wait_for(get_simple_redis_manager(), timeout=1.0)
                except Exception:
                    self.redis_manager = None
            rankings_key = "tradier_rankings"
            data = await self.redis_manager.get(rankings_key)
            if data:
                self.rankings_cache = data if isinstance(data, dict) else {}
                return self.rankings_cache

            return self.rankings_cache
        except Exception as e:
            logger.debug(f"Error getting rankings: {e}")
            return {}
    
    async def get_current_price(self, symbol: str) -> Tuple[Optional[float], Optional[datetime]]:

        symbol = symbol.strip().upper()
        now = datetime.now(timezone.utc)
        MAX_AGE_SECONDS = 40.0  
        symbol = symbol.strip().upper()
        now = datetime.now(timezone.utc)
        MAX_AGE_SECONDS = 15.0  

        def safe_parse_ts(ts_val):
            if not ts_val: return None
            try:
                if isinstance(ts_val, datetime):
                    return ts_val if ts_val.tzinfo else ts_val.replace(tzinfo=timezone.utc)
                if isinstance(ts_val, (int, float)):
                    val = ts_val / 1000.0 if ts_val > 1e12 else ts_val
                    return datetime.fromtimestamp(val, tz=timezone.utc)
                if isinstance(ts_val, str):
                    # Manual parse for speed if format is known, else isoparse
                    if 'T' in ts_val:
                        dt = isoparse(ts_val)
                        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception: return None
            return None

        try:
            # --- LAYER 1: LOCAL CACHE (Zero I/O) ---
            if hasattr(self, 'price_cache') and symbol in self.price_cache:
                entry = self.price_cache[symbol]
                p = float(entry.get('price', 0.0))
                t = safe_parse_ts(entry.get('timestamp'))
                if p > 0 and t and (now - t).total_seconds() < MAX_AGE_SECONDS:
                    return p, t
            if hasattr(self, 'redis_manager') and self.redis_manager:
                try:
                    raw_md = await asyncio.wait_for(
                        self.redis_manager.get("tradier_prices_latest"), 
                        timeout=2.0
                    )
                    
                    if raw_md:
                        md = json.loads(raw_md) if isinstance(raw_md, str) else raw_md
                        if isinstance(md, dict):
                            # FIX: Unwrap the 'data' key!
                            symbol_data = md.get("data", md)
                            if symbol in symbol_data:
                                item = symbol_data[symbol]
                                p = float(item.get('price') or item.get('last', 0.0))
                                t = safe_parse_ts(item.get('timestamp') or item.get('date'))
                                if p > 0 and t:
                                    self.price_cache[symbol] = {'price': p, 'timestamp': t}
                                    if (now - t).total_seconds() < MAX_AGE_SECONDS:
                                        return p, t
                except (asyncio.TimeoutError, Exception):
                    pass 

            # --- LAYER 3: DISK JSON (Reliable Fallback) ---
            try:
                latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
                if latest_file.exists():
                    if (time.time() - latest_file.stat().st_mtime) < 30:
                        async with aiofiles.open(latest_file, 'r') as f:
                            content = await f.read()
                            if content:
                                md = json.loads(content)
                                if isinstance(md, dict):
                                    # FIX: Unwrap the 'data' key!
                                    symbol_data = md.get("data", md)
                                    if symbol in symbol_data:
                                        item = symbol_data[symbol]
                                        p = float(item.get('price') or item.get('last', 0.0))
                                        t = safe_parse_ts(item.get('timestamp'))
                                        if p > 0 and t:
                                            self.price_cache[symbol] = {'price': p, 'timestamp': t}
                                            if (now - t).total_seconds() < MAX_AGE_SECONDS:
                                                return p, t
            except Exception: pass
            if hasattr(self, 'api_client') and self.api_client:
                # Add a check: don't hammer API if we've recently been banned
                if time.time() > getattr(self.api_client, '_global_ban_expires', 0):
                    try:
                        quote = await self.api_client.get_quote(symbol)
                        if quote:
                            p = float(quote.get('last') or 0.0)
                            t = safe_parse_ts(quote.get('date')) or now
                            if p > 0:
                                self.price_cache[symbol] = {'price': p, 'timestamp': t}
                                return p, t
                    except Exception: pass

            # --- FINAL FALLBACK ---
            if hasattr(self, 'price_cache') and symbol in self.price_cache:
                entry = self.price_cache[symbol]
                return float(entry.get('price', 0.0)), safe_parse_ts(entry.get('timestamp'))

            return None, None

        except Exception as e:
            # Critical log to see why it crashed if it gets past the internal tries
            logger.error(f"get_current_price crash for {symbol}: {e}")
            return None, None
            
    async def place_order(self, symbol: str, side: str, quantity: float, order_type: str = "limit",  price: float = None, stop: float = None, duration: str = "day",
                          action: str = None, position_side: str = None, account_key: str="tra") -> Dict:
        current_account.set(account_key)
        now = time.time()
        if symbol in self.rejection_cooldowns:            
            if now < self.rejection_cooldowns[symbol]:
                wait_remaining = int(self.rejection_cooldowns[symbol] - now)
                logger.warning(f"🚫 [BACK-OFF] Skipping {symbol}. Cooldown: {wait_remaining}s")
                return {}
        if not is_regular_trading_hours():
            logger.debug(f"[PLACE_ORDER] Not in trading hours, skipping order: {symbol} {action} {side} qty={quantity}")
            return
        client = TradierAPIClient(config, account_key=account_key)
        try:
            # 2. TRADEABILITY & HOURS CHECK
            if not self.is_symbol_tradeable(symbol, account_key, position_side):
                return {}
            
            if not is_regular_trading_hours(): 
                return {}

            await client.connect()

            # 3. PRICE & POSITION SIZING
            quote = await client.get_quote(symbol)
            if not quote: 
                return {}

            bid, ask = float(quote.get('bid', 0)), float(quote.get('ask', 0))
            last = float(quote.get('last', ask))
            
            # --- FETCH TRUE API HOLDINGS ---
            api_positions = await client.get_account_positions(account_key)
            pos_list =[]
            if isinstance(api_positions, dict):
                p = api_positions.get('positions', {})
                if isinstance(p, dict) and 'position' in p:
                    pos_list = p['position'] if isinstance(p['position'], list) else [p['position']]
                elif isinstance(p, list):
                    pos_list = p
            elif isinstance(api_positions, list):
                pos_list = api_positions

            # Determine API quantity based on intended position side
            current_qty = 0.0
            found_in_api = False
            for p in pos_list:
                api_sym = p.get('symbol', '').strip().upper()
                if api_sym == symbol.strip().upper():
                    found_in_api = True
                    q = float(p.get('quantity', 0))
                    if (q > 0 and position_side == 'LONG') or (q < 0 and position_side == 'SHORT'):
                        current_qty = abs(q)
                        break

            is_exception = symbol.upper() in self.exceptions
            max_order = self.limit_exception_order if is_exception else self.limit_normal_order
            max_pos = self.limit_exception_total_pos if is_exception else self.limit_total_pos

            # --- QUANTITY CALCULATION ---
            if action in["OPEN", "AUGMENT", "REENTER"]:
                remaining_usd = max(0, max_pos - (current_qty * last))
                base_target = config.START_POSITION_SIZE if current_qty == 0 else (quantity * last)
                target_usd = base_target * 3.0 if is_exception else base_target
                calc_qty = int(min(target_usd, max_order, remaining_usd) / last)
            else:
                # EXITS: Strictly clamp to true API holdings to prevent rejection loops
                # Only "ghost clear" if we got a valid response and the symbol IS NOT in the API map for that side
                if not found_in_api or current_qty == 0:
                    if api_positions is None or (isinstance(api_positions, dict) and 'errors' in api_positions):
                         logger.warning(f"[{account_key}] ⚠️ API Check failed for {symbol} on {account_key}. Skipping exit attempt.")
                         return {}
                         
                    logger.warning(f"[{account_key}] 👻 GHOST POSITION DETECTED: {symbol} side {position_side} not held in API {account_key}. Faking close to clear local memory.")
                    held_symbols = [p.get('symbol') for p in pos_list if float(p.get('quantity', 0)) != 0]
                    logger.debug(f"[{account_key}] API Holdings for {account_key}: {held_symbols}")
                    return {"order": {"id": "GHOST_CLEARED", "status": "filled"}}
                
                calc_qty = int(min(quantity, current_qty))

            if calc_qty < 1: 
                logger.warning(f"[{account_key}] ⚠️ Order size too small: {calc_qty} shares ({symbol})")
                return {"errors": {"error":["Order size < 1"]}}

            # --- PREPARE SIDE ---
            tradier_side = None
            if action in ["OPEN", "AUGMENT", "REENTER"]:
                tradier_side = "buy" if position_side == "LONG" else "sell_short"
            else:
                tradier_side = "sell" if position_side == "LONG" else "buy_to_cover"

            # 4. THE 10-SECOND AGGRESSIVE CHASE LOOP
            loop_start = time.time()
            remaining_to_fill = calc_qty
            attempt = 0
            last_order_result = {}

            while (time.time() - loop_start) < 10 and remaining_to_fill > 0:
                attempt += 1
                q_loop = await client.get_quote(symbol)
                curr_bid, curr_ask = float(q_loop.get('bid', 0)), float(q_loop.get('ask', 0))
                
                # PRICE LOGIC: Aim for Midpoint + aggressive offset
                midpoint = (curr_bid + curr_ask) / 2
                if tradier_side in ["buy", "buy_to_cover"]:
                    # Buy at midpoint or slightly above to jump the queue
                    limit_price = round(midpoint + 0.01, 2)
                    if limit_price > curr_ask: limit_price = curr_ask
                else:
                    # Sell at midpoint or slightly below
                    limit_price = round(midpoint - 0.01, 2)
                    if limit_price < curr_bid: limit_price = curr_bid

                logger.info(f"[{account_key}] Loop Attempt {attempt}: {tradier_side} {symbol} {remaining_to_fill} @ {limit_price}")

                order_res = await client.place_order(
                    account_key=account_key, symbol=symbol, side=tradier_side,
                    quantity=remaining_to_fill, order_type="limit", price=limit_price, duration="day"
                )

                order_info = order_res.get('order', {})
                order_id = order_info.get('id')
                
                if not order_id or order_info.get('status') == 'rejected':
                    logger.error(f"❌ Rejection on {symbol}. Reason: {order_res.get('errors')}")
                    self.rejection_cooldowns[symbol] = time.time() + 900
                    return order_res

                # Wait short burst to see if it fills
                await asyncio.sleep(2.5)
                
                status_res = await client.get_order_status(account_key, order_id)
                status = status_res.get('status', '').lower()
                exec_qty = float(status_res.get('exec_quantity', 0))
                
                if status == 'filled':
                    logger.info(f"✅ Filled {symbol} via aggressive limit.")
                    return status_res
                
                # If not filled, cancel and loop back for a better price
                await client.cancel_order(account_key, order_id)
                remaining_to_fill -= exec_qty
                last_order_result = status_res
                logger.info(f"⏳ Partial Fill: {exec_qty}/{remaining_to_fill + exec_qty}. Re-calculating...")

            # 5. FINAL FALLBACK: MARKET ORDER (If still not filled after 10s)
            if remaining_to_fill > 0:
                logger.warning(f"🚨 Chase timeout for {symbol}. Executing MARKET ORDER for remaining {remaining_to_fill} qty.")
                market_res = await client.place_order(
                    account_key=account_key,
                    symbol=symbol,
                    side=tradier_side,
                    quantity=remaining_to_fill,
                    order_type="market",
                    duration="day"
                )
                return market_res

            return last_order_result

        except Exception as e:
            logger.error(f"[smart_place_order] Error: {e}",  exc_info=True )
            return {}
        finally:
            await client.close()

    def _infer_htf_direction_fast(self, is_long, k_5m, k_5m_prev, k_15m, k_15m_prev, d_15m, wt1_5m, wt2_5m, wt1_15m, wt2_15m, current_price, dc_basis_1h, dc_basis_4h, sma_200_15m, sma_200_1m, sma_200_1m_prev):
        """Fast inference of 1h/4h direction using shorter timeframe momentum - reduces lag by not waiting for hourly updates"""
        if is_long:
            momentum_5m = k_5m > k_5m_prev and k_5m < 50
            momentum_15m = k_15m > k_15m_prev and k_15m < 50
            wt_bullish = wt1_5m > wt2_5m and wt1_15m > wt2_15m
            price_above_dc1h = current_price >= dc_basis_1h
            price_above_dc4h = current_price >= dc_basis_4h
            price_above_sma = current_price > sma_200_15m and sma_200_1m_prev < sma_200_1m
            stoch_aligned = k_15m > d_15m
            score = sum([momentum_5m, momentum_15m, wt_bullish, price_above_dc1h, price_above_dc4h, price_above_sma, stoch_aligned])
            return score >= 4
        else:
            momentum_5m = k_5m < k_5m_prev and k_5m > 50
            momentum_15m = k_15m < k_15m_prev and k_15m > 50
            wt_bearish = wt1_5m < wt2_5m and wt1_15m < wt2_15m
            price_below_dc1h = current_price <= dc_basis_1h
            price_below_dc4h = current_price <= dc_basis_4h
            price_below_sma = current_price < sma_200_15m and sma_200_1m_prev > sma_200_1m
            stoch_aligned = k_15m < d_15m
            score = sum([momentum_5m, momentum_15m, wt_bearish, price_below_dc1h, price_below_dc4h, price_below_sma, stoch_aligned])
            return score >= 4

    def _infer_htf_reversal_fast(self, is_long, k_5m, k_5m_prev, k_15m, k_15m_prev, d_15m, wt1_5m, wt2_5m, wt1_15m, wt2_15m, current_price, dc_basis_1h, dc_basis_4h, sma_200_15m, sma_200_1m, sma_200_1m_prev):
        """Fast inference of 1h/4h momentum reversal using shorter timeframe data - detects if HTF momentum is weakening/reversing"""
        if is_long:
            momentum_reversing_5m = k_5m < k_5m_prev
            momentum_reversing_15m = k_15m < k_15m_prev
            wt_bearish = wt1_5m < wt2_5m or wt1_15m < wt2_15m
            price_below_dc1h = current_price < dc_basis_1h
            price_below_dc4h = current_price < dc_basis_4h
            price_below_sma = current_price < sma_200_15m or sma_200_1m_prev > sma_200_1m
            stoch_misaligned = k_15m < d_15m
            score = sum([momentum_reversing_5m, momentum_reversing_15m, wt_bearish, price_below_dc1h, price_below_dc4h, price_below_sma, stoch_misaligned])
            return score >= 3
        else:
            momentum_reversing_5m = k_5m > k_5m_prev
            momentum_reversing_15m = k_15m > k_15m_prev
            wt_bullish = wt1_5m > wt2_5m or wt1_15m > wt2_15m
            price_above_dc1h = current_price > dc_basis_1h
            price_above_dc4h = current_price > dc_basis_4h
            price_above_sma = current_price > sma_200_15m or sma_200_1m_prev < sma_200_1m
            stoch_misaligned = k_15m > d_15m
            score = sum([momentum_reversing_5m, momentum_reversing_15m, wt_bullish, price_above_dc1h, price_above_dc4h, price_above_sma, stoch_misaligned])
            return score >= 3

    async def _maybe_restart_indicators(self, reason: str) -> None:
        """Simplified indicator restart for Tradier"""
        logger.warning(f"[INDICATOR_HEALTH] Potential indicator issue detected: {reason}")

    async def clear_recent_signal(self, position_key: str):
        """Placeholder for clearing recent signal cache"""
        self.recently_processed_signals.pop(position_key, None)

    async def clear_all_cooldowns_for_position(self, position_key: str, side: str = None):
        """Clear ALL cooldown-related data for a position"""
        try:
            self.recently_queued_signals.pop(position_key, None)
            self.augmentation_cooldown_map.pop(position_key, None)
            self.reduction_cooldown_map.pop(position_key, None)
            await self.clear_recent_signal(position_key)
            if hasattr(self, 'orders_in_limbo') and position_key in self.orders_in_limbo:
                if side:
                    self.orders_in_limbo[position_key].pop(side, None)
                    if not self.orders_in_limbo[position_key]:
                        del self.orders_in_limbo[position_key]
                else:
                    del self.orders_in_limbo[position_key]
            logger.debug(f"[CLEAR_ALL_COOLDOWNS] {position_key}: Cleared all cooldown data (side={side})")
        except Exception as e:
            logger.warning(f"[CLEAR_ALL_COOLDOWNS] {position_key}: Error clearing cooldowns: {e}")

    def _robust_json_decode(self, content):
        """Robust JSON decoder for tradeable keys and other files"""
        try:
            if isinstance(content, bytes):
                content = content.decode('utf-8')
            return json.loads(content)
        except Exception as e:
            logger.error(f"JSON Decode Error: {e}")
            return None

    async def load_tradeable(self):
        """Load tradeable keys from tradeable_keys.json"""
        current_time = time.time()
        if hasattr(self, 'tradeable_keys') and self.tradeable_keys and (current_time - self._last_tradeable_update < 180):
            return list(self.tradeable_keys)

        async with self.tradeable_lock:
            if hasattr(self, 'tradeable_keys') and self.tradeable_keys and (current_time - self._last_tradeable_update < 180):
                return list(self.tradeable_keys)

            temp_keys = set()
            # 1. Try Redis
            if self.redis_manager:
                try:
                    redis_data = await asyncio.wait_for(self.redis_manager.get("tradeable_keys"), timeout=0.5)
                    if redis_data:
                        data = self._robust_json_decode(redis_data)
                        if isinstance(data, list):
                            temp_keys = {str(k).strip() for k in data}
                except Exception: pass

            # 2. Try File
            if not temp_keys:
                main_path = self.config.BASE_PATH / "tradeable_keys.json"
                if main_path.exists():
                    try:
                        async with aiofiles.open(main_path, "r") as f:
                            content = await f.read()
                            data = self._robust_json_decode(content)
                            if isinstance(data, list):
                                temp_keys = {str(k).strip() for k in data}
                    except Exception as e:
                        logger.error(f"[SYMBOLS] Error loading tradeable_keys.json: {e}")

            if temp_keys:
                self.tradeable_keys = temp_keys
                self._last_tradeable_update = current_time
            return list(self.tradeable_keys)

    def is_symbol_tradeable(self, symbol: str, account_key: str, side: str) -> bool:
        """Centralized tradeability check with debug logging."""
        symbol = symbol.upper()
        side = side.upper()
        
        # 1. Blacklist
        if symbol in self.blacklist: 
            return False
        
        # 2. Always Tradeable / Exceptions (Both ways)
        whitelist = [s.upper() for s in getattr(config, 'ALWAYS_TRADEABLE', [])]
        whitelist += [s.upper() for s in getattr(config, 'EXCEPTIONS', [])]
        if symbol in whitelist: 
            return True
        
        # 3. Check if we already have a position (Management always allowed)
        if self.position_manager:
            pk_long = f"{account_key}:{symbol}_LONG"
            pk_short = f"{account_key}:{symbol}_SHORT"
            pos_long = self.position_manager.get_position(pk_long)
            pos_short = self.position_manager.get_position(pk_short)
            if pos_long and pos_long.positionAmt > 0: return True
            if pos_short and pos_short.positionAmt > 0: return True

        # 4. Discovery Lists
        longs = [s.upper() for s in getattr(self, f"symbols_long_{account_key}", [])]
        shorts = [s.upper() for s in getattr(self, f"symbols_short_{account_key}", [])]
        
        if side == 'LONG' and symbol in longs: return True
        if side == 'SHORT' and symbol in shorts:
            if symbol in self.non_shortable_symbols:
                # logger.warning(f"[{account_key}] 🛑 {symbol} is NON-SHORTABLE. Blocking trade.")
                return False
            return True
        
        return False

    async def execute_trade_action(self, account_key, position_key, symbol, quantity, current_price, side, position_side, unique_id, is_full_close=False, action='', reason='', override_qty=None):
        if not is_regular_trading_hours(): return
        current_account.set(account_key)
        _is_exit_or_reduce = action in ('REDUCE', 'CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE', 'QUICK_CLOSE')
        if symbol.upper() in self.blacklist and not _is_exit_or_reduce:
            logger.critical(f"[{account_key}] 🛑 CRITICAL: Attempted to trade BLACKLISTED symbol {symbol}. ACTION BLOCKED.")
            return "BLOCKED_BLACKLIST"

        # 1. Variables and Flags
        is_long = position_side == 'LONG'
        is_entry_action = action in ['OPEN', 'REENTRY', 'QUICK_OPEN', 'REVERSE', 'HEDGE_OPEN']
        is_reduce = action in ['REDUCE', 'PROFIT_TAKE']
        is_exit_action = action in ['CLOSE', 'FULL_CLOSE', 'PROFIT_TAKE', 'REDUCE']
        is_hedge = action in ['HEDGE_OPEN', 'HEDGE_CLOSE']
        hedge_for = None # Tradier logic usually doesn't use hedge_for like ez_manage
        # BACKTEST_CHANGE_T31: hedge disabled, negative EV in all 5 backtest configs
        if is_hedge and not getattr(config, 'HEDGE_MODE_TRADIER', True):
            logger.info(f"[{account_key}] [HEDGE_DISABLED] Blocking {action} for {symbol}: HEDGE_MODE_TRADIER=False")
            return "BLOCKED_HEDGE_DISABLED"
        # Tradeability Check
        if not self.is_symbol_tradeable(symbol, account_key, position_side):
            logger.warning(f"[{account_key}] ⛔ EXECUTE BLOCK {symbol} {position_side}: Not in approved list.")
            return "BLOCKED_NOT_TRADEABLE"

        # 2. Indicators Fetch & Unpack
        i = self.get_indicators(symbol)
        if not i: 
            logger.error(f"[EXECUTE_DEBUG] {symbol}: NO_INDICATORS")
            return "NO_INDICATORS"

        # Map 5m to 3m for logic compatibility if needed, though we'll use 5m variables directly
        def g(k, default=0.0): return float(i.get(k, default))
        def gs(k, default='neutral'): return str(i.get(k, default))
        
        timestamp_str = i.get('timestamp', '')
        timestamp = safe_datetime(timestamp_str)
        
        # Unpack variables (Using 5m instead of 3m as per user instruction)
        k_1m, k_1m_prev, d_1m = g('stoch_k_1m', 50), g('stoch_k_1m_prev', 50), g('stoch_d_1m', 50)
        k_5m, k_5m_prev, d_5m = g('stoch_k_5m', 50), g('stoch_k_5m_prev', 50), g('stoch_d_5m', 50)
        k_15m, k_15m_prev, d_15m = g('stoch_k_15m', 50), g('stoch_k_15m_prev', 50), g('stoch_d_15m', 50)
        k_1h, k_1h_prev, d_1h = g('stoch_k_1h', 50), g('stoch_k_1h_prev', 50), g('stoch_d_1h', 50)
        k_4h, k_4h_prev, d_4h = g('stoch_k_4h', 50), g('stoch_k_4h_prev', 50), g('stoch_d_4h', 50)
        k_D, k_D_prev, d_D = g('stoch_k_D', 50), g('stoch_k_D_prev', 50), g('stoch_d_D', 50)

        wt1_5m, wt2_5m = g('wt1_5m'), g('wt2_5m')
        wt1_15m, wt2_15m = g('wt1_15m'), g('wt2_15m')
        wt1_1h, wt2_1h = g('wt1_1h'), g('wt2_1h')
        
        sma_200_1m, sma_200_1m_prev = g('sma_200_1m'), g('sma_200_1m_prev')
        sma_200_15m, sma_200_15m_prev = g('sma_200_15m'), g('sma_200_15m_prev')
        sma_200_1h = g('sma_200_1h')
        
        dc_basis_5m, dc_basis_15m, dc_basis_1h, dc_basis_4h = g('dc_basis_5m'), g('dc_basis_15m'), g('dc_basis_1h'), g('dc_basis_4h')
        dc_high_5m, dc_low_5m = g('dc_high_5m'), g('dc_low_5m')
        dc_high_15m, dc_low_15m = g('dc_high_15m'), g('dc_low_15m')
        dc_high_1h, dc_low_1h = g('dc_high_1h'), g('dc_low_1h')
        dc_high_4h, dc_low_4h = g('dc_high_4h'), g('dc_low_4h')
        
        dc_high_5m_ant, dc_low_5m_ant = g('dc_high_5m_ant', dc_high_5m), g('dc_low_5m_ant', dc_low_5m)
        dc_high_15m_ant, dc_low_15m_ant = g('dc_high_15m_ant', dc_high_15m), g('dc_low_15m_ant', dc_low_15m)
        dc_high_1h_ant, dc_low_1h_ant = g('dc_high_1h_ant', dc_high_1h), g('dc_low_1h_ant', dc_low_1h)
        dc_high_4h_ant, dc_low_4h_ant = g('dc_high_4h_ant', dc_high_4h), g('dc_low_4h_ant', dc_low_4h)
        dc_basis_15m_ant = g('dc_basis_15m_ant', dc_basis_15m)
        
        dc_high4_5m, dc_low4_5m = g('dc_high4_5m'), g('dc_low4_5m')
        high_5m, low_5m, high_5m_prev, low_5m_prev = g('high_5m'), g('low_5m'), g('high_5m_prev'), g('low_5m_prev')
        high_15m, low_15m = g('high_15m'), g('low_15m')
        high_1h, low_1h, high_1h_prev, low_1h_prev = g('high_1h'), g('low_1h'), g('high_1h_prev'), g('low_1h_prev')
        high_4h, low_4h, high_4h_prev, low_4h_prev = g('high_4h'), g('low_4h'), g('high_4h_prev'), g('low_4h_prev')
        
        ha_5m, ha_15m, ha_1h, ha_4h = gs('ha_5m'), gs('ha_15m'), gs('ha_1h'), gs('ha_4h')
        atr_5m, atr_15m = g('atr_5m'), g('atr_15m')
        t_up_5m = i.get('t_up_5m', False)

        # ═══ TRADIER V1: ZONE GATE + ALIGNMENT ═══
        # Primary TF = 1h for swing. During market open (9:30-10:00) and last 2h (14:00-16:00) use 15m for faster entries.
        _is_reentry = action == 'REENTRY' or 'REENTRY' in (reason or '').upper()
        _is_augment_or_entry = not _is_exit_or_reduce
        _reason_upper = (reason or '').upper()
        _is_rotation = 'ROTATION_ENTRY' in _reason_upper
        _is_rsi2 = 'RSI2_MEAN_REVERSION' in _reason_upper
        _is_gap_fill = 'GAP_FILL' in _reason_upper
        _is_proven_strategy = _is_rotation or _is_rsi2 or _is_gap_fill
        _est = timezone(timedelta(hours=-5))
        _now_est = datetime.now(timezone.utc).astimezone(_est).time()
        _is_fast_window = (dt_time(9, 30) <= _now_est <= dt_time(10, 0)) or (dt_time(14, 0) <= _now_est <= dt_time(16, 0))
        _zone_k = k_15m if _is_fast_window else k_1h
        _zone_tf = '15m' if _is_fast_window else '1h'
        if _is_augment_or_entry and not is_hedge:
            _ez = getattr(self.config, 'ENTRY_ZONE_LONG', 22.0); _esz = 100.0 - _ez
            if action in ('OPEN', 'QUICK_OPEN') and not _is_reentry and not _is_rotation and not _is_gap_fill:
                if (is_long and _zone_k > _ez) or (not is_long and _zone_k < _esz):
                    logger.warning(f"[TRADIER_ZONE_BLOCK] {position_key}: k_{_zone_tf}={_zone_k:.0f} outside zone {_ez}/{_esz} ({'FAST' if _is_fast_window else 'SWING'})")
                    return f"BLOCKED_ZONE_k{_zone_tf}={_zone_k:.0f}"
            # Alignment: count conditions agreeing with direction
            _al = 0
            for _k, _d in [(k_15m,d_15m),(k_1h,d_1h),(k_4h,d_4h),(k_D,d_D)]:
                if (is_long and _k > _d) or (not is_long and _k < _d): _al += 1
            for _ha in [ha_5m, ha_15m, ha_1h, ha_4h]:
                if (is_long and _ha == 'green') or (not is_long and _ha == 'red'): _al += 1
            for _dcb in [dc_basis_15m, dc_basis_1h, dc_basis_4h]:
                if _dcb > 0 and ((is_long and current_price > _dcb) or (not is_long and current_price < _dcb)): _al += 1
            for _w1, _w2 in [(wt1_5m,wt2_5m),(wt1_15m,wt2_15m),(wt1_1h,wt2_1h)]:
                if (is_long and _w1 > _w2) or (not is_long and _w1 < _w2): _al += 1
            _rsi_1h = g('rsi_1h', 50); _rsi_4h = g('rsi_4h', 50)
            if (is_long and _rsi_1h > 50) or (not is_long and _rsi_1h < 50): _al += 1
            if (is_long and _rsi_4h > 50) or (not is_long and _rsi_4h < 50): _al += 1
            if sma_200_1h > 0 and ((is_long and current_price > sma_200_1h) or (not is_long and current_price < sma_200_1h)): _al += 1
            _min_al = 6 if _is_proven_strategy else getattr(self.config, 'ENTRY_MIN_ALIGNMENT', 10)
            if _is_reentry:
                if _al >= _min_al: quantity = quantity * min(1.5, 1.0 + (_al - _min_al) * 0.05)
            elif not _is_reentry and 'RATIO_RECOVERY' not in _reason_upper:
                if _al < _min_al:
                    logger.warning(f"[TRADIER_ALIGNMENT_BLOCK] {position_key}: alignment={_al}/{_min_al}{' (relaxed)' if _is_proven_strategy else ''}")
                    return f"BLOCKED_ALIGNMENT_{_al}/{_min_al}"
            # dc_low4 hard bottom (applies to all entries including reentries)
            _dc4l_5m = dc_low4_5m; _dc4h_5m = dc_high4_5m
            if is_long and _dc4l_5m > 0 and current_price < _dc4l_5m:
                logger.warning(f"[TRADIER_DC4_BLOCK] {position_key}: price {current_price:.2f} < dc_low4_5m {_dc4l_5m:.2f}")
                return f"BLOCKED_DC4_BOTTOM"
            if not is_long and _dc4h_5m > 0 and current_price > _dc4h_5m:
                logger.warning(f"[TRADIER_DC4_BLOCK] {position_key}: price {current_price:.2f} > dc_high4_5m {_dc4h_5m:.2f}")
                return f"BLOCKED_DC4_TOP"
        # 3. Price Fetch & Validation
        current_price, _ = await self.get_current_price(symbol)
        current_price = float(current_price)
        if current_price <= 0: return "NO_PRICE"

        position = self.get_position(position_key)
        has_local_pos = position and abs(float(getattr(position, 'positionAmt', 0))) > 0.00001
        
        # Auto-switch AUGMENT to OPEN if no position exists
        if action == "AUGMENT" and not has_local_pos:
            logger.info(f"[{position_key}] Switching AUGMENT to OPEN (No local position found)")
            action = "OPEN"
            is_entry_action = True

        is_valid, validation_reason, actual_qty = await self.validate_position_before_trade(position_key, action, quantity)
        if not is_valid: return f"VALIDATION_FAILED:{validation_reason}"
        if validation_reason == "ADJUSTED_QTY" and actual_qty: quantity = actual_qty

        if position is None:
            position = await self.ensure_position_present(account_key, symbol, position_side, position_key)
            if not position:
                logger.error(f"[TRADE] {position_key}: Position not found in memory or disk — cannot execute trade without position data")
                return "POSITION_NOT_FOUND"

        # 4. Entry/Augment Blockers
        # URGENT_FIX: Never augment losing positions (config-gated)
        if action == "AUGMENT" and getattr(config, 'AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER', True) and position.gain < 0:
            logger.warning(f"[AUGMENT_PROFITABLE_ONLY] {position_key}: BLOCKED — losing position (gain={position.gain:.2f}%)")
            return "BLOCKED_NO_GAIN"
        if action == "AUGMENT" and position.gain <= 0:
            logger.warning(f"[{account_key}] BLOCKED AUGMENT {symbol}: Gain is {position.gain:.2f}% (Must be > 0%)")
            return "BLOCKED_NO_GAIN"
            
        if action == "OPEN" and position_side == "SHORT" and symbol.upper() in self.non_shortable_symbols:
            logger.warning(f"✋ {symbol} BLOCKED: Symbol not available for short sales on Tradier")
            return "NON_SHORTABLE"

        # 5. Execute Exit/Reduce if needed
        if is_exit_action and not is_entry_action:
            qty_to_close = float(quantity)
            pos_qty = abs(float(getattr(position, 'positionAmt', 0)))
            if is_full_close or qty_to_close <= 0: qty_to_close = pos_qty
            final_qty = max(1, int(min(qty_to_close, pos_qty)))
            if final_qty < 1: return "QTY_ZERO"
            logger.info(f"[{position_key}] EXIT EXECUTE: {action} {final_qty} @ {current_price}")
            return await self.execute_now(position_key, account_key, symbol, pos_qty, side, position_side, float(final_qty), current_price, unique_id, reason, is_full_close, action)

        # 6. Entry/Augment Quantity Calculation (The Quantizing Logic)
        SP = config.START_POSITION_SIZE / current_price
        
        # Exception Boost (3x size)
        if symbol.upper() in self.exceptions:
            SP *= 3.0
            logger.info(f"[{position_key}] 🚀 EXCEPTION BOOST: Using 3x base size")

        quantity_before_quantizing = float(quantity)
        
        # User Logic Block (Adapted)
        if account_key in ['trb', 'trc']:
            if dc_high_5m - dc_low_5m > 5 * float(atr_15m): quantity += 0.8 * SP
            if (is_long and current_price > dc_high_1h) or (not is_long and current_price < dc_low_1h): quantity += 0.3 * SP
            if (is_long and k_5m < 30 and k_5m > d_5m) or (not is_long and k_5m > 70 and k_5m < d_5m): quantity += 0.3 * SP
            if (is_long and current_price > dc_high_4h) or (not is_long and current_price < dc_low_4h):
                quantity += 0.3 * SP
                if (is_long and k_5m < 30 and k_5m > d_5m) or (not is_long and k_5m > 70 and k_5m < d_5m): quantity += 0.3 * SP
            
            # HTF Direction checks
            higher_high_15m = high_15m >= high_5m_prev
            lower_low_15m = low_15m <= low_5m_prev
            k_ok = (k_15m >= d_15m or wt1_15m >= wt2_15m or higher_high_15m) if is_long else (k_15m <= d_15m or wt1_15m <= wt2_15m or lower_low_15m)
            stoch1_ok = self._infer_htf_direction_fast(is_long, k_5m, k_5m_prev, k_15m, k_15m_prev, d_15m, wt1_5m, wt2_5m, wt1_15m, wt2_15m, current_price, dc_basis_1h, dc_basis_4h, sma_200_15m, sma_200_1m, sma_200_1m_prev)
            
            reduction_factor = 1.0
            if not k_ok: reduction_factor *= 0.6
            if not stoch1_ok: reduction_factor *= 0.8
            quantity = reduction_factor * quantity

            # Sentiment Boosts
            market_sentiment = g('0market_sentiment_score', 0)
            sentiment_classification = i.get('0sentiment_classification', 'NEUTRAL')
            if is_long and sentiment_classification == "EXTREME_BULLISH" and market_sentiment > 20: quantity += 0.3 * SP
            elif not is_long and sentiment_classification == "EXTREME_BEARISH" and market_sentiment < -20: quantity += 0.3 * SP
            
            # Minimum viable checks
            exchange_min_qty = self.min_qty.get(symbol, 1.0)
            min_viable_qty = max(exchange_min_qty, 6.0 / current_price)
            if quantity < min_viable_qty: quantity = min_viable_qty

        # 7. Final Execution
        final_shares = int(round(quantity))
        if final_shares < 1: return "QTY_ZERO_FINAL"
        
        logger.info(f"[{position_key}] {action} EXECUTE: {final_shares} shares @ {current_price}")
        return await self.execute_now(position_key, account_key, symbol, abs(position.positionAmt), side, position_side, float(final_shares), current_price, unique_id, reason, is_full_close, action)

    async def execute_now(self, position_key: str, account_key: str, symbol: str, original_position_amt: float, side: str, position_side: str, quantity: float, old_price: float, unique_id: str, reason: str, is_full_close: bool, action: str = None) -> str:
        if not is_regular_trading_hours(): return
        exec_lock_key = f"execute_now:{position_key}:{side}"
        MAX_EXECUTION_TIME = 120
        lock_acquired = False
        try:
            if self.redis_manager:
                try:
                    existing = await self.redis_manager.get(exec_lock_key)
                    if existing:
                        if isinstance(existing, dict) and existing.get('status') == 'ghost_cleared':
                            logger.warning(f"[EXECUTE_NOW_BLOCKED] {position_key}: GHOST_CLEARED cooldown active, skipping")
                            return "GHOST_CLEARED_COOLDOWN"
                        lock_age = 0
                        if isinstance(existing, dict) and 'timestamp' in existing:
                            lock_age = time.time() - existing.get('timestamp', 0)
                        if lock_age < MAX_EXECUTION_TIME:
                            logger.error(f"[EXECUTE_NOW_BLOCKED] {position_key}: REDIS LOCK EXISTS - Another execution is in progress!")
                            return "REDIS_EXECUTION_LOCK_EXISTS"
                    await self.redis_manager.set(exec_lock_key, {"timestamp": time.time(), "unique_id": unique_id}, ex=MAX_EXECUTION_TIME)
                    lock_acquired = True
                except Exception as lock_err:
                    logger.error(f"[EXECUTE_NOW_LOCK_ERROR] {position_key}: Error during lock acquisition: {lock_err}")
                    return "LOCK_ACQUISITION_ERROR"
            
            # Check cooldowns
            recent_signal_ts = self.recently_processed_signals.get(position_key, 0)
            is_reduce = (side.upper() == "SELL" and position_side == "LONG") or (side.upper() == "BUY" and position_side == "SHORT")
            is_augment = not is_reduce
            
            if is_augment and recent_signal_ts > 0 and time.time() - recent_signal_ts < getattr(config, 'AUGMENTATION_COOLDOWN_SECONDS', 120.0):
                logger.warning(f"[EXECUTE_NOW_BLOCKED] {position_key}: Augmentation cooldown active")
                if lock_acquired and self.redis_manager:
                    await self.redis_manager.delete(exec_lock_key)
                return "AUGMENTATION_COOLDOWN"
            
            if is_reduce and recent_signal_ts > 0 and time.time() - recent_signal_ts < getattr(config, 'REDUCTION_COOLDOWN_SECONDS', 30.0):
                position = self.position_manager.positions.get(position_side, {}).get(symbol) if self.position_manager else None
                position_gain = getattr(position, 'gain', 0.0) if position else 0.0
                # Cooldown applies regardless of gain — loss positions were bypassing this gate
                # Only allow bypass for catastrophic losses (< -5%) so we can still stop-loss
                if position_gain > -5.0:
                    logger.warning(f"[EXECUTE_NOW_BLOCKED] {position_key}: Reduction cooldown active (gain={position_gain:.2f}%)")
                    if lock_acquired and self.redis_manager:
                        await self.redis_manager.delete(exec_lock_key)
                    return "REDUCTION_COOLDOWN"
            
            # Get current position and price
            position = self.position_manager.positions.get(position_key)
            current_price = old_price
            if not current_price or current_price <= 0:
                current_price,ts = await self.get_current_price(symbol)
                current_price = float(current_price)
            if current_price <= 0:
                logger.error(f"[EXECUTE_NOW] {position_key}: No valid price")
                if lock_acquired and self.redis_manager:
                    await self.redis_manager.delete(exec_lock_key)
                return "NO_VALID_PRICE"
            
            # Validate quantity
            if quantity <= 0:
                logger.error(f"[EXECUTE_NOW] {position_key}: Invalid quantity {quantity}")
                if lock_acquired and self.redis_manager:
                    await self.redis_manager.delete(exec_lock_key)
                return "INVALID_QUANTITY"
            
            # Place market order directly
            original_quantity = quantity
            if quantity < 1.0:
                logger.warning(f"[EXECUTE_NOW] {position_key}: Quantity {quantity} < 1, enforcing minimum of 1 share")
                quantity = 1.0
            quantity = max(1, int(round(quantity))) 
            
            if not is_regular_trading_hours(): 
                if lock_acquired and self.redis_manager:
                    await self.redis_manager.delete(exec_lock_key)
                return "MARKET_CLOSED"
                
            result = await self.place_order(  symbol, side, quantity, "market", duration="day", 
                action=action, position_side=position_side, account_key=account_key  )
            
            # --- 5. RESPONSE PARSING (THE FIX) ---
            if result and isinstance(result, dict):
                # Tradier wraps success in 'order', but errors might be top level
                order_data = result.get('order', result) 
                
                order_id = order_data.get('id')
                order_status = order_data.get('status')
                
                # Check for errors
                errors = result.get('errors', {}).get('error', [])
                if isinstance(errors, list) and errors:
                    order_reject_reason = errors[0]
                elif not order_id:
                    # If no ID and no explicit error list, dump the whole thing
                    order_reject_reason = str(result)
                else:
                    order_reject_reason = None

                if order_id:
                    # SUCCESS
                    log_msg = f"[TRADE] SENT: ✅ {symbol:<5} {action:<7} {side:<4} | Qty: {quantity:<4} @ ${current_price:<7.2f} | Status: {order_status} | ID: {order_id}"
                    logger.info(log_msg)
                    tradier_action_logger.info(f"{position_key}: {log_msg}")
                    
                    self.recently_processed_signals[position_key] = time.time()
                    # BACKTEST_CHANGE_T37: track daily realized P&L for circuit breaker
                    if is_reduce and self.position_manager:
                        _pos_for_pnl = self.position_manager.positions.get(position_key)
                        if _pos_for_pnl:
                            _ep = float(getattr(_pos_for_pnl, 'entry_price', 0) or 0)
                            _ps = position_side
                            if _ep > 0 and current_price > 0:
                                _trade_pnl = (current_price - _ep) * quantity if _ps == 'LONG' else (_ep - current_price) * quantity
                                _daily_loss_tracker["realized_pnl"] += _trade_pnl
                                _max_loss_pct = getattr(config, 'MAX_DAILY_LOSS_PCT', 3.0)
                                _portfolio_bal = self.get_current_portfolio_balance()
                                _total_exp = _portfolio_bal.get('total', 10000.0) or 10000.0
                                _loss_pct = (_daily_loss_tracker["realized_pnl"] / _total_exp) * 100.0
                                if _loss_pct < -_max_loss_pct and not _daily_loss_tracker["halted"]:
                                    _daily_loss_tracker["halted"] = True
                                    logger.critical(f"[DAILY_LOSS_BREAKER] Daily P&L=${_daily_loss_tracker['realized_pnl']:.2f} ({_loss_pct:.2f}%) exceeds -{_max_loss_pct}% limit. HALTING ALL NEW OPENS.")
                    # Update Local Memory Instantly
                    if self.position_manager:
                        local_pos = self.position_manager.positions.get(position_key)
                        now_utc = datetime.now(timezone.utc)
                        if local_pos:
                            if is_reduce:
                                new_qty = 0.0 if order_id == "GHOST_CLEARED" else max(0.0, float(local_pos.positionAmt) - float(quantity))
                                local_pos.positionAmt = new_qty
                                local_pos.last_reduction_time = now_utc
                                local_pos.last_reduction_amount = float(quantity)
                                local_pos.last_reduction_price = float(current_price)
                                local_pos.was_reduced = True
                                # Full close: record exit time for reopen-cooldown
                                if new_qty <= 0:
                                    self.last_exit_times[symbol] = time.time()
                                    self.last_exit_prices[symbol] = float(current_price)
                                    if order_id == "GHOST_CLEARED":
                                        local_pos.gain = 0.0
                                        local_pos.max_gain = 0.0
                                        local_pos.entry_price = 0.0
                                        local_pos.positionAmt = 0.0
                                        try:
                                            await self.position_manager.save_all_positions()
                                            logger.info(f"[GHOST_CLEAR] {position_key}: Fully zeroed + saved to disk")
                                        except Exception as _save_err:
                                            logger.warning(f"[GHOST_CLEAR] {position_key}: Save failed: {_save_err}")
                            elif is_augment:
                                new_qty = float(local_pos.positionAmt) + float(quantity)
                                local_pos.positionAmt = float(local_pos.positionAmt) + float(quantity)
                                local_pos.last_augmentation_time = now_utc
                                old_cost = (new_qty - quantity) * local_pos.entry_price
                                new_cost = quantity * current_price
                                if new_qty > 0:
                                    local_pos.entry_price = (old_cost + new_cost) / new_qty

                    await self._append_to_history(position_key, action, quantity, current_price, reason)
                    if lock_acquired and self.redis_manager:
                        # Ghost-cleared positions get 5-min cooldown to stop repeat loops
                        ghost_ttl = 300 if order_id == "GHOST_CLEARED" else 5
                        ghost_status = "ghost_cleared" if order_id == "GHOST_CLEARED" else "filled"
                        await self.redis_manager.set(exec_lock_key, {"status": ghost_status, "timestamp": time.time()}, ex=ghost_ttl)

                    return "SUCCESS"

                elif order_reject_reason:
                    # REJECTION
                    logger.error(f"[TRADE] ❌ {symbol} {side} REJECTED: {order_reject_reason}")
                    if lock_acquired and self.redis_manager: await self.redis_manager.delete(exec_lock_key)
                    return f"ORDER_REJECTED:{order_reject_reason}"
            else:
                logger.error(f"[TRADE] ❌ {symbol} {side} Failed: Invalid API response.")
                if lock_acquired and self.redis_manager: await self.redis_manager.delete(exec_lock_key)
                return "ORDER_API_ERROR"

        except Exception as e:
            logger.error(f"[EXECUTE_CRASH] {position_key}: {e}",  exc_info=True )
            if lock_acquired and self.redis_manager:
                try: await self.redis_manager.delete(exec_lock_key)
                except Exception: pass
            return f"EXCEPTION_{str(e)[:50]}"

    async def _append_to_history(self, position_key: str, trade_type: str, qty: float, price: float, reason: str = ""):
        try:
            parts = position_key.split(':')
            account_key = parts[0] if len(parts) > 0 else "unknown"
            symbol_side = parts[1] if len(parts) > 1 else position_key
            hist_dir = Path(self.config.DATA_DIR) / "history" / account_key
            hist_dir.mkdir(parents=True, exist_ok=True)
            filename = hist_dir / f"{symbol_side}.jsonl"
            MAX_SIZE_BYTES = 200 * 1024
            if filename.exists():
                try:
                    if filename.stat().st_size > MAX_SIZE_BYTES:
                        backup = filename.with_name(f"{filename.name}.bak")
                        if backup.exists(): backup.unlink()
                        filename.rename(backup)
                except Exception: pass
            entry = {"ts": datetime.now(timezone.utc).isoformat(), "type": trade_type, "qty": round(float(qty), 6), "price": round(float(price), 4), "value": round(float(qty) * float(price), 2), "reason": reason or "Manual/System Detection", "indicators": {}}
            async with aiofiles.open(filename, "a") as f:
                await f.write(orjson.dumps(entry).decode('utf-8') + "\n")
        except Exception as e:
            logger.error(f"[HISTORY] Write error {position_key}: {e}")

    async def should_enter_long(self, symbol: str, indicators: Dict[str, Any]) -> bool:
        """Determine if we should enter a long position"""
        try:
            # BACKTEST_CHANGE_T55: RSI(10) entry gate — OOS-validated champion (Sharpe 6.43, WR 73.9%, PF 8.18, +311% PnL)
            _rsi_period = getattr(config, 'RSI_ENTRY_PERIOD_TRADIER', 10)
            _rsi_key = f'rsi_{_rsi_period}_D' if f'rsi_{_rsi_period}_D' in indicators else f'rsi_D'
            _rsi_val = float(indicators.get(_rsi_key, indicators.get('rsi_D', indicators.get('rsi_1h', 50))) or 50)
            _rsi_thresh = getattr(config, 'RSI_ENTRY_LONG_TRADIER', 30.0)
            if _rsi_val > _rsi_thresh:
                if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol} BLOCKED: RSI({_rsi_period})={_rsi_val:.1f} > {_rsi_thresh} (BACKTEST_CHANGE_T55)")
                return False
            # BACKTEST_CHANGE_T57: SMA filter — only LONG above SMA(100)
            _sma_period = getattr(config, 'SMA_FILTER_PERIOD_TRADIER', 100)
            _sma_key = f'sma_{_sma_period}_D' if f'sma_{_sma_period}_D' in indicators else 'sma_200_D'
            _sma_val = float(indicators.get(_sma_key, indicators.get('sma_200_D', indicators.get('sma_200_1h', 0))) or 0)
            _cur_p = float(indicators.get('current_price', 0) or 0)
            if _sma_val > 0 and _cur_p > 0 and _cur_p < _sma_val:
                if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol} BLOCKED: price {_cur_p:.2f} < SMA({_sma_period}) {_sma_val:.2f} (BACKTEST_CHANGE_T57)")
                return False
            final_score = float(indicators.get('0final_score_norm', 0))
            ranking_points = float(indicators.get('0ranking_points', 0))
            lr_trend_15m = float(indicators.get('lr_trend_15m', 0))
            k_1h = float(indicators.get('stoch_k_1h', 50))
            d_1h = float(indicators.get('stoch_d_1h', 50))
            k_4h = float(indicators.get('stoch_k_4h', 50))
            d_4h = float(indicators.get('stoch_d_4h', 50))
            if config.VERBOSE:
                logger.info(f"[VERBOSE][CALC][LONG] {symbol} ENTRY CALCULATION START: final_score={final_score:.3f} ranking_points={ranking_points:.1f} lr_trend_15m={lr_trend_15m:.3f} k_1h={k_1h:.1f} d_1h={d_1h:.1f} k_4h={k_4h:.1f} d_4h={d_4h:.1f}")
            # BACKTEST_CHANGE_T19: time zone stoch threshold gate
            if getattr(config, 'TIME_ZONE_ENABLED', False):
                _zone = _get_trade_zone()
                if _zone == "open":
                    _zt = getattr(config, 'ZONE_OPEN_THRESHOLD', 25)
                elif _zone == "mid":
                    _zt = getattr(config, 'ZONE_MID_THRESHOLD', 30)
                elif _zone == "close":
                    _zt = getattr(config, 'ZONE_CLOSE_THRESHOLD', 20)
                else:
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol} BLOCKED: market closed zone")
                    return False
                if k_1h > (100.0 - _zt):
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol} BLOCKED: zone={_zone} k_1h={k_1h:.1f} > threshold {100.0 - _zt:.0f}")
                    return False
            # BACKTEST_CHANGE_T3: SMA200 distance check on 4h
            if getattr(config, 'SMA200_DIST_ENTRY_ENABLED', False):
                _sma200_4h = float(indicators.get('sma_200_4h', 0) or 0)
                _cur_price = float(indicators.get('current_price', 0) or 0)
                if _sma200_4h > 0 and _cur_price > 0:
                    _dist_pct = ((_cur_price - _sma200_4h) / _sma200_4h) * 100.0
                    if _dist_pct < getattr(config, 'SMA200_DIST_LONG_THRESHOLD_4H', -10.0):
                        if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol} BLOCKED: SMA200_4h dist={_dist_pct:.1f}% < threshold {config.SMA200_DIST_LONG_THRESHOLD_4H}")
                        return False
            # BACKTEST_CHANGE_T4: MFI check on Daily
            if getattr(config, 'MFI_ENTRY_ENABLED', False):
                _mfi_d = float(indicators.get('mfi_D', 50) or 50)
                if _mfi_d > getattr(config, 'MFI_LONG_THRESHOLD_D', 20.0):
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol} BLOCKED: MFI_D={_mfi_d:.1f} > {config.MFI_LONG_THRESHOLD_D} (not oversold)")
                    return False
            # BACKTEST_CHANGE_T8: alignment gate
            if getattr(config, 'ALIGNMENT_GATE_MIN', 0) > 0:
                _al_count = 0
                if k_1h > d_1h: _al_count += 1
                if k_4h > d_4h: _al_count += 1
                if float(indicators.get('stoch_k_15m', 50)) > float(indicators.get('stoch_d_15m', 50)): _al_count += 1
                if lr_trend_15m > 0: _al_count += 1
                if _al_count < getattr(config, 'ALIGNMENT_GATE_MIN', 4):
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol} BLOCKED: alignment={_al_count} < {config.ALIGNMENT_GATE_MIN}")
                    return False
            # Leaderboard filter check
            if config.LEADERBOARD_FILTER:
                rankings = await self.get_rankings()
                if rankings:
                    top_symbols = rankings.get('top_20', [])
                    in_top_20 = symbol in top_symbols
                    if config.VERBOSE:
                        logger.info(f"[VERBOSE][CALC][LONG] {symbol} LEADERBOARD_FILTER: enabled={config.LEADERBOARD_FILTER} in_top_20={in_top_20}")
                    if not in_top_20:
                        if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol}  BLOCKED: not in top_20 leaderboard")
                        return False
                elif config.VERBOSE:
                    logger.info(f"[VERBOSE][CALC][LONG] {symbol} LEADERBOARD_FILTER: enabled but no rankings data available")
            elif config.VERBOSE:
                logger.info(f"[VERBOSE][CALC][LONG] {symbol} LEADERBOARD_FILTER: disabled (skipping)")
            # Trend gates check
            if config.TREND_GATES:
                trend_ok = lr_trend_15m > 0
                if config.VERBOSE:
                    logger.info(f"[VERBOSE][CALC][LONG] {symbol} TREND_GATES: enabled={config.TREND_GATES} lr_trend_15m={lr_trend_15m:.3f} trend_ok={trend_ok}")
                if not trend_ok:
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol}  BLOCKED: lr_trend_15m={lr_trend_15m:.3f} <= 0")
                    return False
            elif config.VERBOSE:
                logger.info(f"[VERBOSE][CALC][LONG] {symbol} TREND_GATES: disabled (skipping)")
            # HTF confirmation checks
            if config.HTF1_CONF or config.HTF4_CONF:
                htf1_ok = True
                htf4_ok = True
                if config.HTF1_CONF:
                    htf1_ok = k_1h > d_1h
                    if config.VERBOSE:
                        logger.info(f"[VERBOSE][CALC][LONG] {symbol} HTF1_CONF: enabled={config.HTF1_CONF} k_1h={k_1h:.1f} d_1h={d_1h:.1f} htf1_ok={htf1_ok}")
                    if not htf1_ok:
                        if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol}  BLOCKED: HTF1_CONF k_1h={k_1h:.1f} <= d_1h={d_1h:.1f}")
                        return False
                if config.HTF4_CONF:
                    htf4_ok = k_4h > d_4h
                    if config.VERBOSE:
                        logger.info(f"[VERBOSE][CALC][LONG] {symbol} HTF4_CONF: enabled={config.HTF4_CONF} k_4h={k_4h:.1f} d_4h={d_4h:.1f} htf4_ok={htf4_ok}")
                    if not htf4_ok:
                        if config.VERBOSE: logger.info(f"[VERBOSE][CALC][LONG] {symbol}  BLOCKED: HTF4_CONF k_4h={k_4h:.1f} <= d_4h={d_4h:.1f}")
                        return False
            elif config.VERBOSE:
                logger.info(f"[VERBOSE][CALC][LONG] {symbol} HTF_CONF: disabled (skipping)")
            # Final score check
            score_threshold = 0.6
            ranking_threshold = 50
            score_ok = True  # BACKTEST_CHANGE_T42: near-zero signal in 0xxx sweep
            ranking_ok = True  # BACKTEST_CHANGE_T41: near-zero signal in 0xxx sweep
            final_result = score_ok and ranking_ok
            if config.VERBOSE:
                logger.info(f"\n[DECISION SCORECARD] {symbol} LONG EVALUATION")
                logger.info(f"{'Metric':<15} | {'Value':<10} | {'Threshold':<10} | {'Result'}")
                logger.info("-" * 55)
                logger.info(f"{'Leaderboard':<15} | {str(in_top_20):<10} | {'True':<10} | {'✅' if in_top_20 else '!!'}")
                logger.info(f"{'Trend (15m)':<15} | {lr_trend_15m:<10.3f} | {'> 0':<10} | {'✅' if trend_ok else '!!'}")
                logger.info(f"{'HTF1 (1h)':<15} | {k_1h:.1f}/{d_1h:.1f}  | {'K > D':<10} | {'✅' if htf1_ok else '!!'}")
                logger.info(f"{'HTF4 (4h)':<15} | {k_4h:.1f}/{d_4h:.1f}  | {'K > D':<10} | {'✅' if htf4_ok else '!!'}")
                logger.info(f"{'Final Score':<15} | {final_score:<10.3f} | {'> '+str(score_threshold):<10} | {'✅' if final_score>score_threshold else '!!'}")
                logger.info(f"{'Rank Points':<15} | {ranking_points:<10.1f} | {'BYPASSED':<10} | {'✅ (T41)'}")
                logger.info("-" * 55)
                logger.info(f"FINAL DECISION: {'✅ ENTER LONG' if final_result else '⛔ BLOCKED'}\n")
                logger.info(f"[VERBOSE][CALC][LONG] {symbol} FINAL SCORE CHECK: final_score={final_score:.3f} > {score_threshold} = {score_ok} | ranking_points=BYPASSED(T41) | RESULT={final_result}")
            return final_result
        except Exception as e:
            logger.error(f"Error in should_enter_long for {symbol}: {e}",  exc_info=True )
            return False

    async def should_enter_short(self, symbol: str, indicators: Dict[str, Any]) -> bool:
        """Determine if we should enter a short position"""
        try:
            # BACKTEST_CHANGE_T55: RSI(10) entry gate for shorts
            _rsi_period = getattr(config, 'RSI_ENTRY_PERIOD_TRADIER', 10)
            _rsi_key = f'rsi_{_rsi_period}_D' if f'rsi_{_rsi_period}_D' in indicators else f'rsi_D'
            _rsi_val = float(indicators.get(_rsi_key, indicators.get('rsi_D', indicators.get('rsi_1h', 50))) or 50)
            _rsi_thresh = getattr(config, 'RSI_ENTRY_SHORT_TRADIER', 70.0)
            if _rsi_val < _rsi_thresh:
                if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} BLOCKED: RSI({_rsi_period})={_rsi_val:.1f} < {_rsi_thresh} (BACKTEST_CHANGE_T55)")
                return False
            # BACKTEST_CHANGE_T57: SMA filter — only SHORT below SMA(100)
            _sma_period = getattr(config, 'SMA_FILTER_PERIOD_TRADIER', 100)
            _sma_key = f'sma_{_sma_period}_D' if f'sma_{_sma_period}_D' in indicators else 'sma_200_D'
            _sma_val = float(indicators.get(_sma_key, indicators.get('sma_200_D', indicators.get('sma_200_1h', 0))) or 0)
            _cur_p = float(indicators.get('current_price', 0) or 0)
            if _sma_val > 0 and _cur_p > 0 and _cur_p > _sma_val:
                if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} BLOCKED: price {_cur_p:.2f} > SMA({_sma_period}) {_sma_val:.2f} (BACKTEST_CHANGE_T57)")
                return False
            final_score = indicators.get('0final_score_norm', 0)
            ranking_points = indicators.get('0ranking_points', 0)
            lr_trend_15m = indicators.get('lr_trend_15m', 0)
            k_1h = indicators.get('stoch_k_1h', 50)
            d_1h = indicators.get('stoch_d_1h', 50)
            k_4h = indicators.get('stoch_k_4h', 50)
            d_4h = indicators.get('stoch_d_4h', 50)
            if config.VERBOSE:
                logger.info(f"[VERBOSE][CALC][SHORT] {symbol} ENTRY CALCULATION START: final_score={final_score:.3f} ranking_points={ranking_points:.1f} lr_trend_15m={lr_trend_15m:.3f} k_1h={k_1h:.1f} d_1h={d_1h:.1f} k_4h={k_4h:.1f} d_4h={d_4h:.1f}")
            # BACKTEST_CHANGE_T19: time zone stoch threshold gate
            if getattr(config, 'TIME_ZONE_ENABLED', False):
                _zone = _get_trade_zone()
                if _zone == "open":
                    _zt = getattr(config, 'ZONE_OPEN_THRESHOLD', 25)
                elif _zone == "mid":
                    _zt = getattr(config, 'ZONE_MID_THRESHOLD', 30)
                elif _zone == "close":
                    _zt = getattr(config, 'ZONE_CLOSE_THRESHOLD', 20)
                else:
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} BLOCKED: market closed zone")
                    return False
                if k_1h < _zt:
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} BLOCKED: zone={_zone} k_1h={k_1h:.1f} < threshold {_zt:.0f}")
                    return False
                # BACKTEST_CHANGE_T24: mid zone requires WT crossunder on 15m for shorts
                if _zone == "mid" and getattr(config, 'WT_CROSSUNDER_15M_SHORT', False):
                    _wt1_15m = float(indicators.get('wt1_15m', 0) or 0)
                    _wt2_15m = float(indicators.get('wt2_15m', 0) or 0)
                    if _wt1_15m >= _wt2_15m:
                        if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} BLOCKED: mid zone requires wt_crossunder_15m (wt1={_wt1_15m:.1f} >= wt2={_wt2_15m:.1f})")
                        return False
            # BACKTEST_CHANGE_T9: conviction threshold for shorts
            if getattr(config, 'CONVICTION_SHORT_THRESHOLD', 0) > 0:
                _conviction = float(indicators.get('0conviction', 0) or 0)
                if _conviction < getattr(config, 'CONVICTION_SHORT_THRESHOLD', 20):
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} BLOCKED: conviction={_conviction:.0f} < {config.CONVICTION_SHORT_THRESHOLD}")
                    return False
            # BACKTEST_CHANGE_T8: alignment gate
            if getattr(config, 'ALIGNMENT_GATE_MIN', 0) > 0:
                _al_count = 0
                if k_1h < d_1h: _al_count += 1
                if k_4h < d_4h: _al_count += 1
                if float(indicators.get('stoch_k_15m', 50)) < float(indicators.get('stoch_d_15m', 50)): _al_count += 1
                if lr_trend_15m < 0: _al_count += 1
                if _al_count < getattr(config, 'ALIGNMENT_GATE_MIN', 4):
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} BLOCKED: alignment={_al_count} < {config.ALIGNMENT_GATE_MIN}")
                    return False
            # BACKTEST_CHANGE_T10: LR %B daily threshold for shorts
            if getattr(config, 'LR_PCTB_D_SHORT_THRESHOLD', 0) > 0:
                _lr_pctb_d = float(indicators.get('lr_pctb_D', 0.5) or 0.5)
                if _lr_pctb_d < getattr(config, 'LR_PCTB_D_SHORT_THRESHOLD', 0.1):
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} BLOCKED: lr_pctb_D={_lr_pctb_d:.2f} < {config.LR_PCTB_D_SHORT_THRESHOLD}")
                    return False
            # Leaderboard filter check
            if config.LEADERBOARD_FILTER:
                rankings = await self.get_rankings()
                if rankings:
                    bottom_symbols = rankings.get('bottom_20', [])
                    in_bottom_20 = symbol in bottom_symbols
                    if config.VERBOSE:
                        logger.info(f"[VERBOSE][CALC][SHORT] {symbol} LEADERBOARD_FILTER: enabled={config.LEADERBOARD_FILTER} in_bottom_20={in_bottom_20}")
                    if not in_bottom_20:
                        if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} !! BLOCKED: not in bottom_20 leaderboard")
                        return False
                elif config.VERBOSE:
                    logger.info(f"[VERBOSE][CALC][SHORT] {symbol} LEADERBOARD_FILTER: enabled but no rankings data available")
            elif config.VERBOSE:
                logger.info(f"[VERBOSE][CALC][SHORT] {symbol} LEADERBOARD_FILTER: disabled (skipping)")
            # Trend gates check
            if config.TREND_GATES:
                trend_ok = lr_trend_15m < 0
                if config.VERBOSE:
                    logger.info(f"[VERBOSE][CALC][SHORT] {symbol} TREND_GATES: enabled={config.TREND_GATES} lr_trend_15m={lr_trend_15m:.3f} trend_ok={trend_ok}")
                if not trend_ok:
                    if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} !! BLOCKED: lr_trend_15m={lr_trend_15m:.3f} >= 0")
                    return False
            elif config.VERBOSE:
                logger.info(f"[VERBOSE][CALC][SHORT] {symbol} TREND_GATES: disabled (skipping)")
            # HTF confirmation checks
            if config.HTF1_CONF or config.HTF4_CONF:
                htf1_ok = True
                htf4_ok = True
                if config.HTF1_CONF:
                    htf1_ok = k_1h < d_1h
                    if config.VERBOSE:
                        logger.info(f"[VERBOSE][CALC][SHORT] {symbol} HTF1_CONF: enabled={config.HTF1_CONF} k_1h={k_1h:.1f} d_1h={d_1h:.1f} htf1_ok={htf1_ok}")
                    if not htf1_ok:
                        if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} !! BLOCKED: HTF1_CONF k_1h={k_1h:.1f} >= d_1h={d_1h:.1f}")
                        return False
                if config.HTF4_CONF:
                    htf4_ok = k_4h < d_4h
                    if config.VERBOSE:
                        logger.info(f"[VERBOSE][CALC][SHORT] {symbol} HTF4_CONF: enabled={config.HTF4_CONF} k_4h={k_4h:.1f} d_4h={d_4h:.1f} htf4_ok={htf4_ok}")
                    if not htf4_ok:
                        if config.VERBOSE: logger.info(f"[VERBOSE][CALC][SHORT] {symbol} !! BLOCKED: HTF4_CONF k_4h={k_4h:.1f} >= d_4h={d_4h:.1f}")
                        return False
            elif config.VERBOSE:
                logger.info(f"[VERBOSE][CALC][SHORT] {symbol} HTF_CONF: disabled (skipping)")
            # Final score check
            score_threshold = 0.4
            ranking_threshold = -50
            score_ok = True  # BACKTEST_CHANGE_T42: near-zero signal in 0xxx sweep
            ranking_ok = True  # BACKTEST_CHANGE_T41: near-zero signal in 0xxx sweep
            final_result = score_ok and ranking_ok
            if config.VERBOSE:
                in_bottom_20_val = in_bottom_20 if config.LEADERBOARD_FILTER and 'in_bottom_20' in locals() else (True if not config.LEADERBOARD_FILTER else False)
                trend_ok_val = trend_ok if config.TREND_GATES and 'trend_ok' in locals() else (True if not config.TREND_GATES else (lr_trend_15m < 0))
                htf1_ok_val = htf1_ok if config.HTF1_CONF and 'htf1_ok' in locals() else (True if not config.HTF1_CONF else (k_1h < d_1h))
                htf4_ok_val = htf4_ok if config.HTF4_CONF and 'htf4_ok' in locals() else (True if not config.HTF4_CONF else (k_4h < d_4h))
                logger.info(f"\n[DECISION SCORECARD] {symbol} SHORT EVALUATION")
                logger.info(f"{'Metric':<15} | {'Value':<10} | {'Threshold':<10} | {'Result'}")
                logger.info("-" * 55)
                logger.info(f"{'Leaderboard':<15} | {str(in_bottom_20_val):<10} | {'True':<10} | {'✅' if in_bottom_20_val else '!!'}")
                logger.info(f"{'Trend (15m)':<15} | {lr_trend_15m:<10.3f} | {'< 0':<10} | {'✅' if trend_ok_val else '!!'}")
                logger.info(f"{'HTF1 (1h)':<15} | {k_1h:.1f}/{d_1h:.1f}  | {'K < D':<10} | {'✅' if htf1_ok_val else '!!'}")
                logger.info(f"{'HTF4 (4h)':<15} | {k_4h:.1f}/{d_4h:.1f}  | {'K < D':<10} | {'✅' if htf4_ok_val else '!!'}")
                logger.info(f"{'Final Score':<15} | {final_score:<10.3f} | {'< '+str(score_threshold):<10} | {'✅' if final_score<score_threshold else '!!'}")
                logger.info(f"{'Rank Points':<15} | {ranking_points:<10.1f} | {'BYPASSED':<10} | {'✅ (T41)'}")
                logger.info("-" * 55)
                logger.info(f"FINAL DECISION: {'✅ ENTER SHORT' if final_result else '⛔ BLOCKED'}\n")
                logger.info(f"[VERBOSE][CALC][SHORT] {symbol} FINAL SCORE CHECK: final_score={final_score:.3f} < {score_threshold} = {score_ok} | ranking_points=BYPASSED(T41) | RESULT={final_result}")
            return final_result
        except Exception as e:
            logger.error(f"Error in should_enter_short for {symbol}: {e}",  exc_info=True )
            return False

    async def should_exit_long(self, symbol: str, position: TradierPosition, indicators: Dict[str, Any]) -> bool:
        """Determine if we should exit a long position"""
        try:
            current_price,ts = await self.get_current_price(symbol)
            current_price = float(current_price)
            entry_price = position.entry_price
            stop_loss = position.stop_loss
            take_profit = position.take_profit
            gain = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
            cu_1h = indicators.get('stoch_crossunder_1h')
            stop_loss_hit = stop_loss and current_price <= stop_loss
            take_profit_hit = take_profit and current_price >= take_profit
            crossunder_hit = cu_1h == True
            # BACKTEST_CHANGE_T16: ATR 2x trailing stop exit
            _atr_trail_exit = False
            if getattr(config, 'ATR_TRAIL_2X_EXIT_ENABLED', False) and entry_price > 0:
                _atr_1h = float(indicators.get('atr_1h', 0) or 0)
                if _atr_1h > 0 and current_price < entry_price - (2.0 * _atr_1h) and gain > 0:
                    _atr_trail_exit = True
                    logger.info(f"[ATR_TRAIL_EXIT] LONG {symbol}: price={current_price:.2f} < entry-2*ATR={entry_price - 2*_atr_1h:.2f}")
            # BACKTEST_CHANGE_T17: stoch cross 1h exit (k crosses below d = exit long if in profit)
            _stoch_cross_1h_exit = False
            if getattr(config, 'STOCH_CROSS_1H_EXIT_ENABLED', False) and gain > 0:
                _k_1h = float(indicators.get('stoch_k_1h', 50) or 50)
                _d_1h = float(indicators.get('stoch_d_1h', 50) or 50)
                _k_1h_prev = float(indicators.get('stoch_k_1h_prev', 50) or 50)
                if _k_1h < _d_1h and _k_1h_prev >= _d_1h:
                    _stoch_cross_1h_exit = True
                    logger.info(f"[STOCH_CROSS_1H_EXIT] LONG {symbol}: k_1h={_k_1h:.1f} crossed below d_1h={_d_1h:.1f} gain={gain:.2f}%")
            final_result = stop_loss_hit or take_profit_hit or crossunder_hit or _atr_trail_exit or _stoch_cross_1h_exit
            if config.VERBOSE:
                logger.info(f"\n[DECISION SCORECARD] {symbol} LONG EXIT EVALUATION")
                logger.info(f"{'Metric':<20} | {'Value':<15} | {'Condition':<20} | {'Result'}")
                logger.info("-" * 75)
                logger.info(f"{'Current Price':<20} | ${current_price:<14.2f} | {'N/A':<20} | {'─'}")
                logger.info(f"{'Entry Price':<20} | ${entry_price:<14.2f} | {'N/A':<20} | {'─'}")
                logger.info(f"{'Gain %':<20} | {gain:<14.2f}% | {'N/A':<20} | {'─'}")
                logger.info(f"{'Stop Loss':<20} | ${stop_loss if stop_loss else 'None':<14} | {'Price <= SL':<20} | {'✅ EXIT' if stop_loss_hit else '!!'}")
                logger.info(f"{'Take Profit':<20} | ${take_profit if take_profit else 'None':<14} | {'Price >= TP':<20} | {'✅ EXIT' if take_profit_hit else '!!'}")
                logger.info(f"{'Stoch Crossunder':<20} | {str(cu_1h):<14} | {'Crossunder 1h':<20} | {'✅ EXIT' if crossunder_hit else '!!'}")
                logger.info("-" * 75)
                logger.info(f"FINAL DECISION: {'✅ EXIT LONG' if final_result else '⛔ HOLD'}\n")
            if stop_loss_hit:
                return True
            if take_profit_hit:
                return True
            if crossunder_hit:
                return True
            return False
        except Exception as e:
            logger.debug(f"Error in should_exit_long for {symbol}: {e}")
            return False

    async def should_exit_short(self, symbol: str, position: TradierPosition, indicators: Dict[str, Any]) -> bool:
        """Determine if we should exit a short position"""
        try:
            current_price,ts = await self.get_current_price(symbol)
            current_price = float(current_price)
            entry_price = position.entry_price
            stop_loss = position.stop_loss
            take_profit = position.take_profit
            gain = ((entry_price - current_price) / entry_price * 100) if entry_price > 0 else 0
            co_1h = indicators.get('stoch_crossover_1h')
            stop_loss_hit = stop_loss and current_price >= stop_loss
            take_profit_hit = take_profit and current_price <= take_profit
            crossover_hit = co_1h == True
            # BACKTEST_CHANGE_T16: ATR 2x trailing stop exit
            _atr_trail_exit = False
            if getattr(config, 'ATR_TRAIL_2X_EXIT_ENABLED', False) and entry_price > 0:
                _atr_1h = float(indicators.get('atr_1h', 0) or 0)
                if _atr_1h > 0 and current_price > entry_price + (2.0 * _atr_1h) and gain > 0:
                    _atr_trail_exit = True
                    logger.info(f"[ATR_TRAIL_EXIT] SHORT {symbol}: price={current_price:.2f} > entry+2*ATR={entry_price + 2*_atr_1h:.2f}")
            # BACKTEST_CHANGE_T17: stoch cross 1h exit (k crosses above d = exit short if in profit)
            _stoch_cross_1h_exit = False
            if getattr(config, 'STOCH_CROSS_1H_EXIT_ENABLED', False) and gain > 0:
                _k_1h = float(indicators.get('stoch_k_1h', 50) or 50)
                _d_1h = float(indicators.get('stoch_d_1h', 50) or 50)
                _k_1h_prev = float(indicators.get('stoch_k_1h_prev', 50) or 50)
                if _k_1h > _d_1h and _k_1h_prev <= _d_1h:
                    _stoch_cross_1h_exit = True
                    logger.info(f"[STOCH_CROSS_1H_EXIT] SHORT {symbol}: k_1h={_k_1h:.1f} crossed above d_1h={_d_1h:.1f} gain={gain:.2f}%")
            final_result = stop_loss_hit or take_profit_hit or crossover_hit or _atr_trail_exit or _stoch_cross_1h_exit
            if config.VERBOSE:
                logger.info(f"\n[DECISION SCORECARD] {symbol} SHORT EXIT EVALUATION")
                logger.info(f"{'Metric':<20} | {'Value':<15} | {'Condition':<20} | {'Result'}")
                logger.info("-" * 75)
                logger.info(f"{'Current Price':<20} | ${current_price:<14.2f} | {'N/A':<20} | {'─'}")
                logger.info(f"{'Entry Price':<20} | ${entry_price:<14.2f} | {'N/A':<20} | {'─'}")
                logger.info(f"{'Gain %':<20} | {gain:<14.2f}% | {'N/A':<20} | {'─'}")
                logger.info(f"{'Stop Loss':<20} | ${stop_loss if stop_loss else 'None':<14} | {'Price >= SL':<20} | {'✅ EXIT' if stop_loss_hit else '!!'}")
                logger.info(f"{'Take Profit':<20} | ${take_profit if take_profit else 'None':<14} | {'Price <= TP':<20} | {'✅ EXIT' if take_profit_hit else '!!'}")
                logger.info(f"{'Stoch Crossover':<20} | {str(co_1h):<14} | {'Crossover 1h':<20} | {'✅ EXIT' if crossover_hit else '!!'}")
                logger.info("-" * 75)
                logger.info(f"FINAL DECISION: {'✅ EXIT SHORT' if final_result else '⛔ HOLD'}\n")
            if stop_loss_hit:
                return True
            if take_profit_hit:
                return True
            if crossover_hit:
                return True
            return False
        except Exception as e:
            logger.debug(f"Error in should_exit_short for {symbol}: {e}")
            return False
            
    async def calculate_position_size(self, symbol: str, price: float, account_key: str = "tra") -> float:
        """Calculate position size based on config"""
        try:
            if price <= 0:
                logger.error(f"Error calculating position size for {symbol}: Price is {price}")
                return 0.0
            target_value = config.START_POSITION_SIZE
            # BACKTEST_CHANGE_T23: close zone size multiplier
            if getattr(config, 'TIME_ZONE_ENABLED', False):
                _zone = _get_trade_zone()
                if _zone == "close":
                    target_value *= getattr(config, 'CLOSE_ZONE_SIZE_MULT', 1.5)
            max_value = min(target_value, config.MAX_ORDER_VALUE)
            shares = max_value / price
            shares_int = max(1, int(round(shares)))
            return float(shares_int)
        except Exception as e:
            logger.error(f"Error calculating position size for {symbol}: {e}")
            return 0.0

    async def calculate_stop_loss(self, symbol: str, entry_price: float, side: str, indicators: Dict[str, Any]) -> float:
        """
        Calculate stop loss.
        Rule 1: Respect Daily DC levels (Macro structure).
        Rule 2: MUST be outside 15m DC levels (Micro structure) - never stop inside the noise.
        Rule 3: Max risk cap of 6% to prevent "infinite" structural stops.
        """
        try:
            d = self.strategy.parse_market_data(indicators)
            
            # Daily Levels (Macro)
            dc_low_d = d.get('dc_low_D', 0) or d.get('dc_low_D', 0)
            dc_high_d = d.get('dc_high_D', 0) or d.get('dc_high_D', 0)
            
            # 15m Levels (Micro)
            dc_low_15m = d.get('dc_low_15m', 0)
            dc_high_15m = d.get('dc_high_15m', 0)
            
            atr = d.get('atr_15m', 0)
            stop_price = 0.0

            if side == "LONG":
                # 1. Base Stop: 2% below Daily Low, or 2x ATR if Daily missing
                if dc_low_d > 0:
                    base_stop = dc_low_d * 0.98
                else:
                    base_stop = entry_price - (atr * 2.0 if atr > 0 else entry_price * 0.03)  # Marathon winner: stop_atr=2.0

                # 2. Structural Constraint: Stop MUST be below 15m Low
                # If base_stop is higher than 15m Low (inside the noise), push it down.
                if dc_low_15m > 0:
                    structural_limit = dc_low_15m * 0.995  # 0.5% buffer below 15m low
                    stop_price = min(base_stop, structural_limit)
                else:
                    stop_price = base_stop

                # 3. Sanity Cap: Don't risk more than 6% on entry
                max_risk_price = entry_price * 0.94
                if stop_price < max_risk_price:
                    stop_price = max_risk_price
                    logger.info(f"[{symbol}] Structural stop too wide. Capping at -6%: {stop_price:.2f}")

                logger.info(f"[{symbol}] Stop Loss: {stop_price:.2f} (Entry: {entry_price:.2f}, 15mLow: {dc_low_15m:.2f})")

            else: # SHORT
                # 1. Base Stop: 2% above Daily High
                if dc_high_d > 0:
                    base_stop = dc_high_d * 1.02
                else:
                    base_stop = entry_price + (atr * 2.0 if atr > 0 else entry_price * 0.03)

                # 2. Structural Constraint: Stop MUST be above 15m High
                if dc_high_15m > 0:
                    structural_limit = dc_high_15m * 1.005 # 0.5% buffer above 15m high
                    stop_price = max(base_stop, structural_limit)
                else:
                    stop_price = base_stop

                # 3. Sanity Cap: 6% max risk
                max_risk_price = entry_price * 1.06
                if stop_price > max_risk_price:
                    stop_price = max_risk_price
                    logger.info(f"[{symbol}] Structural stop too wide. Capping at -6%: {stop_price:.2f}")

                logger.info(f"[{symbol}] Stop Loss: {stop_price:.2f} (Entry: {entry_price:.2f}, 15mHigh: {dc_high_15m:.2f})")

            return float(stop_price)

        except Exception as e:
            logger.error(f"Error calculating stop loss for {symbol}: {e}")
            # Emergency Fallback: 4% fixed
            return entry_price * 0.96 if side == "LONG" else entry_price * 1.04
            
    async def process_symbol(self, symbol: str, account_key: str):  
        """
        Process a specific symbol for Entries (if allowed) and Exits (if held).
        Strictly enforces allow-lists for new entries.
        """
        try:
            symbol = symbol.upper().strip()
            
            # 1. Get Indicators (Shared for both sides)
            indicators = self.get_indicators(symbol)
            if not indicators:
                logger.debug(f"No indicators available for {symbol}")
                return

            current_price,_ = await self.get_current_price(symbol)
            current_price = float(current_price)
            # BACKTEST_CHANGE: k_4h cross (not k_1h) — tradier uses higher TF
            global _ls_ratio_4h_adj_tradier
            _k4h_ps = float(indicators.get('stoch_k_4h', 50) or 50)
            _d4h_ps = float(indicators.get('stoch_d_4h', 50) or 50)
            _k4h_prev_ps = float(indicators.get('stoch_k_4h_prev', 50) or 50)
            if _k4h_ps > _d4h_ps and _k4h_prev_ps <= _d4h_ps:
                _ls_ratio_4h_adj_tradier["adj"] = min(0.50, _ls_ratio_4h_adj_tradier.get("adj", 0.0) + 0.10)
                _ls_ratio_4h_adj_tradier["last_cross_ts"] = time.time()
                _ls_ratio_4h_adj_tradier["adj"] = max(-0.50, min(0.50, _ls_ratio_4h_adj_tradier["adj"]))
            elif _k4h_ps < _d4h_ps and _k4h_prev_ps >= _d4h_ps:
                _ls_ratio_4h_adj_tradier["adj"] = max(-0.50, _ls_ratio_4h_adj_tradier.get("adj", 0.0) - 0.10)
                _ls_ratio_4h_adj_tradier["last_cross_ts"] = time.time()
                _ls_ratio_4h_adj_tradier["adj"] = max(-0.50, min(0.50, _ls_ratio_4h_adj_tradier["adj"]))
            # 2. Get Positions directly
            from utils import construct_position_key, orjson_default
            long_key = construct_position_key(account_key, symbol, 'LONG')
            short_key = construct_position_key(account_key, symbol, 'SHORT')
            
            long_pos = self.position_manager.positions.get(long_key)
            short_pos = self.position_manager.positions.get(short_key)

            has_long = long_pos and abs(float(getattr(long_pos, 'positionAmt', 0))) > 0
            has_short = short_pos and abs(float(getattr(short_pos, 'positionAmt', 0))) > 0
            # BACKTEST_CHANGE_T37: daily loss circuit breaker
            global _daily_loss_tracker
            _today_str = datetime.now(timezone.utc).strftime('%Y%m%d')
            if _daily_loss_tracker["date"] != _today_str:
                _daily_loss_tracker = {"date": _today_str, "realized_pnl": 0.0, "halted": False}
            _entry_blocked_daily_loss = _daily_loss_tracker["halted"]
            # BACKTEST_CHANGE_T35: max concurrent positions check
            _entry_blocked_max_pos = False
            _max_concurrent = getattr(config, 'MAX_CONCURRENT_POSITIONS', 999)
            if _max_concurrent < 999:
                _open_count = sum(1 for _pk, _p in self.position_manager.positions.items() if abs(float(getattr(_p, 'positionAmt', 0))) > 0)
                if _open_count >= _max_concurrent:
                    _entry_blocked_max_pos = True
            # BACKTEST_CHANGE_T36: L/S ratio enforcement
            _entry_blocked_ls_long, _entry_blocked_ls_short = False, False
            if getattr(config, 'LS_RATIO_ENFORCE_TRADIER', False):
                _lc = sum(1 for _pk in self.position_manager.positions if _pk.endswith("_LONG") and abs(float(getattr(self.position_manager.positions[_pk], 'positionAmt', 0))) > 0)
                _sc = sum(1 for _pk in self.position_manager.positions if _pk.endswith("_SHORT") and abs(float(getattr(self.position_manager.positions[_pk], 'positionAmt', 0))) > 0)
                # BACKTEST_CHANGE: k_4h cross (not k_1h) — tradier uses higher TF
                _adj = _ls_ratio_4h_adj_tradier.get("adj", 0.0)
                if _ls_ratio_4h_adj_tradier.get("last_cross_ts", 0) > 0 and (time.time() - _ls_ratio_4h_adj_tradier["last_cross_ts"]) > 14400:
                    _ls_ratio_4h_adj_tradier["adj"] = 0.0
                    _adj = 0.0
                _ls_min = max(0.30, getattr(config, 'LS_RATIO_MIN_TRADIER', 0.50) + _adj)
                _ls_max = min(3.00, getattr(config, 'LS_RATIO_MAX_TRADIER', 2.00) + _adj)
                _ratio = _lc / max(_sc, 1)
                if _ratio > _ls_max:
                    _entry_blocked_ls_long = True
                    logger.warning(f"[LS_RATIO_BLOCK] L/S ratio {_ratio:.2f} > max {_ls_max:.2f}, blocking LONG open")
                if _ratio < _ls_min:
                    _entry_blocked_ls_short = True
                    logger.warning(f"[LS_RATIO_BLOCK] L/S ratio {_ratio:.2f} < min {_ls_min:.2f}, blocking SHORT open")
            # ==========================================================
            # LONG SIDE PROCESSING
            # ==========================================================

            # A. EXIT CHECK (Always allowed if we have a position)
            if has_long:
                if await self.should_exit_long(symbol, long_pos, indicators):
                    logger.info(f"!! Exit signal for LONG {symbol}")
                    qty_to_close = abs(float(long_pos.positionAmt))
                    result = await self.place_order(symbol, "SELL", qty_to_close, "market", duration="day", action="REDUCE", position_side="LONG", account_key=account_key)
                    self._log_order_result(result, symbol, "LONG", "CLOSE")
            
            # B. ENTRY CHECK (Only if allowed AND no opposing position)
            elif not has_short:
                if _entry_blocked_daily_loss:  # BACKTEST_CHANGE_T37
                    logger.warning(f"[DAILY_LOSS_HALT] Blocking LONG {symbol}: daily loss limit breached")
                elif _entry_blocked_max_pos:  # BACKTEST_CHANGE_T35
                    logger.debug(f"[MAX_POS_BLOCK] Blocking LONG {symbol}: at max concurrent positions ({_max_concurrent})")
                elif _entry_blocked_ls_long:  # BACKTEST_CHANGE_T36
                    pass  # already logged above
                elif self.is_symbol_allowed(account_key, symbol, 'LONG'):
                    if await self.should_enter_long(symbol, indicators):
                        quantity = await self.calculate_position_size(symbol, current_price, account_key=account_key)
                        if quantity > 0:
                            stop_loss = await self.calculate_stop_loss(symbol, current_price, "LONG", indicators)
                            result = await self.place_order(symbol, "BUY", quantity, "market", duration="day", action="OPEN", position_side="LONG", account_key=account_key)
                            if result and isinstance(result, dict) and result.get('id'):
                                self.position_manager.set_stop_loss(symbol, "LONG", stop_loss)
                            self._log_order_result(result, symbol, "LONG", "OPEN")
                # else: Symbol ignored for LONG because it's not in Leaderboard/Whitelist

            # ==========================================================
            # SHORT SIDE PROCESSING
            # ==========================================================

            # A. EXIT CHECK (Always allowed if we have a position)
            if has_short:
                if await self.should_exit_short(symbol, short_pos, indicators):
                    logger.info(f"!! Exit signal for SHORT {symbol}")
                    qty_to_close = abs(float(short_pos.positionAmt))
                    result = await self.place_order(symbol, "BUY", qty_to_close, "market", duration="day", action="REDUCE", position_side="SHORT", account_key=account_key)
                    self._log_order_result(result, symbol, "SHORT", "CLOSE")

            # B. ENTRY CHECK (Only if allowed AND no opposing position)
            elif not has_long:
                if _entry_blocked_daily_loss:  # BACKTEST_CHANGE_T37
                    logger.warning(f"[DAILY_LOSS_HALT] Blocking SHORT {symbol}: daily loss limit breached")
                elif _entry_blocked_max_pos:  # BACKTEST_CHANGE_T35
                    logger.debug(f"[MAX_POS_BLOCK] Blocking SHORT {symbol}: at max concurrent positions ({_max_concurrent})")
                elif _entry_blocked_ls_short:  # BACKTEST_CHANGE_T36
                    pass  # already logged above
                elif self.is_symbol_allowed(account_key, symbol, 'SHORT'):
                    if symbol not in self.non_shortable_symbols:
                        if await self.should_enter_short(symbol, indicators):
                            quantity = await self.calculate_position_size(symbol, current_price, account_key=account_key)
                            if quantity > 0:
                                stop_loss = await self.calculate_stop_loss(symbol, current_price, "SHORT", indicators)
                                result = await self.place_order(symbol, "SELL", quantity, "market", duration="day", action="OPEN", position_side="SHORT", account_key=account_key)
                                if result and isinstance(result, dict) and result.get('id'):
                                    self.position_manager.set_stop_loss(symbol, "SHORT", stop_loss)
                                self._log_order_result(result, symbol, "SHORT", "OPEN")
                # else: Symbol ignored for SHORT because it's not in Leaderboard/Whitelist

        except Exception as e:
            logger.error(f"Error processing symbol {symbol}: {e}",  exc_info=True )

    def _log_order_result(self, result, symbol, side, action):
        """Helper to log order outcomes"""
        if result and isinstance(result, dict) and result.get('id'):
            logger.info(f"✅ {action} {side} {symbol} OrderID={result.get('id')} Status={result.get('status')}")
        elif result and isinstance(result, dict):
            logger.error(f"!! Failed {action} {side} {symbol}: {result.get('reject_reason') or result.get('error')}")
        else:
            logger.error(f"!! Failed {action} {side} {symbol}: No valid response")
            
    async def trading_loop(self):
        """Main trading loop - waits for shutdown"""
        while self.running:
            try:
                await asyncio.sleep(60)
                logger.debug("[trading_loop] System running - all processing via background tasks")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in trading loop: {e}",  exc_info=True )
                await asyncio.sleep(5)

    async def update_all_positions_prices(self):
        """Update all positions' mark_price from Redis/cache — pubsub handles real-time, this is a fallback"""
        logger.info("[update_all_positions_prices] Task started")
        while self.running:
            try:
                await asyncio.sleep(5.0)
                symbols = set()               

                if self.position_manager:
                    for pos in self.position_manager.positions.values():
                        if pos and pos.symbol:
                            symbols.add(pos.symbol)
                if self.symbols_long_tra:
                    symbols.update(self.symbols_long_tra[:50])
                if self.symbols_short_tra:
                    symbols.update(self.symbols_short_tra[:50])
                if self.symbols_long_trb:
                    symbols.update(self.symbols_long_trb[:50])
                if self.symbols_short_trb:
                    symbols.update(self.symbols_short_trb[:50])                                              
                if self.symbols_long_trc:
                    symbols.update(self.symbols_long_trc[:50])
                if self.symbols_short_trc:
                    symbols.update(self.symbols_short_trc[:50])
                    
                if not symbols: continue
                
                now = datetime.now(timezone.utc)
                for symbol in symbols:
                    try:
                        indicators = self.get_indicators(symbol, use_cache=True)
                        if indicators:
                            price,ts = await self.get_current_price(symbol)
                            if price > 0:
                                self.price_cache[symbol] = {"price": price, "timestamp": ts}
                                self.price_update_time[symbol] = ts
                    except Exception:
                        pass
            except asyncio.CancelledError:
                logger.info("[update_all_positions_prices] Task cancelled")
                break
            except Exception as e:
                logger.error(f"[update_all_positions_prices] Error: {e}",  exc_info=True )
                await asyncio.sleep(1)

    async def periodic_tasks(self, account_key, order_queue):
        """Main periodic task loop"""
        logger.info(f"☀️[periodic_tasks] Task started for {account_key}")
        while self.running:
            try:
                await asyncio.sleep(30)
                await self.load_leaderboards()
                await self.load_account_symbols()
                
                if self.position_manager and self.order_queue:
                    position_keys = []
                    for pk, pos in self.position_manager.positions.items():
                        if pos and abs(getattr(pos, 'quantity', getattr(pos, 'positionAmt', 0))) > 0:
                            if pk.startswith(f"{account_key}:"):
                                position_keys.append(pk)

                    if position_keys:
                        await monitor_entries(self.order_queue, self, account_key, position_keys, event_type="periodic_tasks", is_priority_add=False, force=False)

                    # --- CANDIDATE SCAN: symbols with no open position ---
                    # Without this, only AAPL/NVDA (with open positions) ever get evaluated
                    await self.reload_account_symbols()
                    longs  = getattr(self, f"symbols_long_{account_key}", [])
                    shorts = getattr(self, f"symbols_short_{account_key}", [])
                    open_set = set(position_keys)
                    candidates = (
                        [f"{account_key}:{s}_LONG"  for s in longs  if f"{account_key}:{s}_LONG"  not in open_set] +
                        [f"{account_key}:{s}_SHORT" for s in shorts if f"{account_key}:{s}_SHORT" not in open_set]
                    )
                    if candidates:
                        logger.info(f"[{account_key}] 🔍 CANDIDATE SCAN: {len(candidates)} symbols ({len(longs)}L/{len(shorts)}S)")
                        await monitor_entries(self.order_queue, self, account_key, candidates[:60], event_type="candidate_scan", is_priority_add=False, force=False)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[PERIODIC] Error: {e}", exc_info=True)

    def _sync_positions_from_manager(self):
        """Sync positions from ALL managers to local tracking - with ALL fields"""
        # Clear existing local cache to prevent stale cross-contamination
        self.positions.clear()
        
        updated_count = 0

        # Iterate through all active account managers (tra, trc, etc.)
        for acc_key, mgr in self.managers.items():
            # Clear specific account cache
            self.positions_by_account[acc_key].clear()
            
            # Iterate this manager's positions
            for pk, position in mgr.positions.items():
                if not position: continue

                # 1. Enforce Key Integrity
                # Ensure the position key starts with the correct account prefix
                if not pk.startswith(f"{acc_key}:"):
                    # If we find a 'tra:' key inside the 'trc' manager, FIX IT.
                    parts = pk.split(":")
                    if len(parts) > 1:
                        # strip old prefix, add correct one
                        clean_suffix = parts[-1] 
                        new_pk = f"{acc_key}:{clean_suffix}"
                        # logger.warning(f"[SYNC] 🧹 Fixing corrupted key: {pk} -> {new_pk}")
                        pk = new_pk
                    else:
                        continue

                # 2. Ensure position object has ALL fields
                if not hasattr(position, 'price_fetch_count'):
                    position.price_fetch_count = 0
                
                # 3. Copy to local tracking
                self.positions_by_account[acc_key][pk] = position
                self.positions[pk] = position
                
                # 4. Update timestamp
                self._position_update_timestamps[pk] = datetime.now(timezone.utc)
                if not position.last_updated:
                    position.last_updated = datetime.now(timezone.utc)
                
                updated_count += 1
        # logger.debug(f"[_sync_positions_from_manager] Synced {updated_count} positions across {len(self.managers)} accounts")

    async def monitor_ladder_levels(self, order_queue):
        """
        Monitors active ladder levels. 
        Triggers REENTER if price hits level AND momentum confirms (Stoch K cross).
        """
        logger.info("[monitor_ladder_levels] Task started")
        while self.running:
            try:
                await asyncio.sleep(5)
                
                if not self.position_manager:
                    continue

                # Iterate over a copy of the keys to avoid modification issues during iteration
                ladder_keys = list(self.position_manager.ladder_levels.keys())
                
                for position_key in ladder_keys:
                    ladder_data = self.position_manager.ladder_levels.get(position_key)
                    if not ladder_data: continue

                    symbol = ladder_data.get('symbol')
                    is_long = ladder_data.get('is_long', True)
                    levels = ladder_data.get('levels', [])
                    
                    # Cleanup empty or expired ladders
                    if not levels or all(l.get('crossed', False) for l in levels):
                        # Optional: Remove empty entries to clean up map
                        # self.position_manager.ladder_levels.pop(position_key, None)
                        continue
                    i = self.get_indicators(symbol)
                    if not i: continue
                    
                    current_price,ts = await self.get_current_price(symbol)
                    current_price = float(current_price)
                    if current_price <= 0: continue
                    k_5m = i.get('stoch_k_5m', 50)
                    d_5m = i.get('stoch_d_5m', 50)
                    
                    momentum_confirmed = (k_5m > d_5m) if is_long else (k_5m < d_5m)

                    levels_changed = False
                    
                    for level_blob in levels:
                        if level_blob.get('crossed', False):
                            continue

                        target_price = level_blob.get('level', 0)
                        qty = level_blob.get('quantity', 0)
                        
                        # LOGIC: 
                        # 1. Price must be "at or beyond" the level (Dip occurred)
                        # 2. Momentum must be reversing (Price coming back up/stabilizing)
                        
                        hit_zone = False
                        if is_long:
                            # Price fell to or below target
                            if current_price <= target_price * 1.001: 
                                hit_zone = True
                        else:
                            # Price rose to or above target
                            if current_price >= target_price * 0.999:
                                hit_zone = True
                        
                        if hit_zone and momentum_confirmed:
                            # EXECUTE REENTRY
                            reason = f"LADDER_LEVEL_HIT target={target_price:.2f} cur={current_price:.2f}"
                            logger.info(f"🪜 {symbol} LADDER TRIGGER: {reason}")
                            
                            await queue_trade_action(
                                order_queue, self, position_key, 
                                "REENTER", reason, 85.0, override_qty=qty  )
                            level_blob['crossed'] = True
                            levels_changed = True
                    if levels_changed:
                        await self.position_manager.save_ladder_levels(position_key)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[monitor_ladder_levels] Error: {e}",  exc_info=True )
                await asyncio.sleep(10)

    async def monitor_system_state(self):
        logger.info("☀️ [SYSTEM] MONITOR STATE Started (Unified Logic)")
        await asyncio.sleep(5)
        
        while self.running:
            try:
                await self.load_leaderboards()
                scan_set = set()
                if self.position_manager:
                    for pk, pos in self.position_manager.positions.items():
                        if abs(float(getattr(pos, 'positionAmt', 0))) > 0:
                            scan_set.add(pk)
                
                # B. Discovery / Target Lists (Monitor for New Entries)
                for acc in self.target_accounts:
                    longs = getattr(self, f"symbols_long_{acc}",[])
                    shorts = getattr(self, f"symbols_short_{acc}",[])
                    for s in longs: scan_set.add(f"{acc}:{s}_LONG")
                    for s in shorts: scan_set.add(f"{acc}:{s}_SHORT")
              
                unique_keys = sorted(list(scan_set))
                if not unique_keys:
                    logger.info(f"[MONITOR] No symbols found. Sleeping...")
                    await asyncio.sleep(30)
                    continue
                
                # FIX 2: CONCURRENT BATCH EXECUTION (Kills the lag)
                batch_size = 40
                for i in range(0, len(unique_keys), batch_size):
                    batch = unique_keys[i:i + batch_size]
                    tasks =[]
                    
                    for position_key in batch:
                        account_key = position_key.split(':')[0]
                        tasks.append(  asyncio.wait_for( process_position(  account_key, position_key, self.order_queue,  self, event_type="MONITOR", force=False ), timeout=15.0  )   )
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for r in results:
                        if isinstance(r, Exception) and not isinstance(r, asyncio.TimeoutError):
                            logger.error(f"[MONITOR] Batch task error: {r}")
                    await asyncio.sleep(0.15) # Tiny breather between big batches
                     

                # Wait before next full scan
                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[MONITOR] 💥 Error in loop: {e}",  exc_info=True )
                await asyncio.sleep(15)


    async def monitor_redis_connectivity(self):
        """Monitor Redis connection health"""
        while self.running:
            try:
                await asyncio.sleep(30)
                if not self.redis_manager:
                    try:
                        self.redis_manager = await asyncio.wait_for(get_simple_redis_manager(), timeout=1.0)
                    except Exception:
                        self.redis_manager = None
                else:
                    try:
                        await self.redis_manager.get("tradier_indicators_latest")
                        logger.debug("[REDIS] Connection healthy")
                    except Exception as e:
                        logger.warning(f"[REDIS] Health check failed: {e}")
                        self.redis_manager = None
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[REDIS] Monitor error: {e}")
        

    async def load_leaderboards(self):
        try:
            await self.load_account_symbols()
            w20 = await self._get_symbol_set("winners_20")
            w15 = await self._get_symbol_set("winners_15m")
            l20 = await self._get_symbol_set("losers_20")
            l15 = await self._get_symbol_set("losers_15m")

            valid_longs_ctx = w20.union(w15)
            valid_shorts_ctx = l20.union(l15)
            
            # Load Master
            master_set = await self._get_symbol_set("symbols_tradier")
            if not master_set:
                # Fallback to config accounts if master missing
                for acc in self.target_accounts:
                    master_set.update(await self._get_symbol_set(f"symbols_{acc}_long"))
                    master_set.update(await self._get_symbol_set(f"symbols_{acc}_short"))

            # Calculate Intersection (The "Smart" List)
            if valid_longs_ctx or valid_shorts_ctx:
                dyn_long = {s for s in master_set if s in valid_longs_ctx}
                dyn_short = {s for s in master_set if s in valid_shorts_ctx}
            else:
                # If no context, use master set directly
                dyn_long = master_set.copy()
                dyn_short = master_set.copy()

            # --- 2. Build Final Account Lists ---
            whitelist = set(getattr(config, 'ALWAYS_TRADEABLE', []))

            for acc in self.target_accounts:
                # A. Start with Dynamic Smart List
                final_longs = dyn_long.copy()
                final_shorts = dyn_short.copy()

                # B. Merge Explicit Files (FORCE MONITOR these)
                # This ensures your requested "symbols_{acc}_long" are ALWAYS included
                file_longs = await self._get_symbol_set(f"symbols_{acc}_long")
                file_shorts = await self._get_symbol_set(f"symbols_{acc}_short")
                
                final_longs.update(file_longs)
                final_shorts.update(file_shorts)

                # C. Merge Account Specific Whitelist
                acc_whitelist = whitelist.copy()
                if acc == 'trb' and hasattr(self, 'always_tradeable_trb'):
                    acc_whitelist.update(self.always_tradeable_trb)
                
                for sym in acc_whitelist:
                    sym = sym.strip().upper()
                    if not sym: continue
                    # Add to both sides for monitoring (Strategy decides entry)
                    final_longs.add(sym)
                    if sym not in self.non_shortable_symbols:
                        final_shorts.add(sym)

                # D. Save to Memory
                setattr(self, f"symbols_long_{acc}", sorted(list(final_longs)))
                setattr(self, f"symbols_short_{acc}", sorted(list(final_shorts)))
                
                # logger.info(f"[{acc}] Loaded Lists: Long={len(final_longs)} Short={len(final_shorts)}")

        except Exception as e:
            logger.error(f"[LEADERBOARDS] Error: {e}",  exc_info=True )


    async def _get_symbol_set(self, filename: str, is_context: bool = False) -> set:
        try:
            # Context files (winners/losers) are in DATA_DIR. Targets are in BASE_PATH.
            dir_path = config.BASE_PATH if filename.startswith('symbols') else config.DATA_DIR
            path = dir_path / f"{filename}.json"
            
            if not path.exists(): return set()

            data = await load_json_safe(path)
            if not data: return set()

            if isinstance(data, dict):
                return {str(k).upper().strip() for k in data.keys()}

            # Handle List of dicts: [{"symbol": "SNDK", "score": 100.0}, ...]
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                return {item['symbol'].upper().strip() for item in data if 'symbol' in item}

            # Handle List of Strings: ["UNH", "MSTR"]
            if isinstance(data, list):
                return {str(s).upper().strip() for s in data if s}

            return set()
        except Exception as e:
            logger.error(f"Error loading {filename}: {e}")
            return set()

    async def reload_account_symbols(self):
        """Strictly loads symbols_{acc}_long.json from BASE_PATH."""
        try:
            found_any = False
            for acc in self.target_accounts:
                l_path = config.BASE_PATH / f"symbols_{acc}_long.json"
                s_path = config.BASE_PATH / f"symbols_{acc}_short.json"
                
                l_list, s_list = [], []
                if l_path.exists():
                    with open(l_path, 'r') as f:
                        l_list = [str(s).upper().strip() for s in json.load(f) if s]
                        setattr(self, f"symbols_long_{acc}", l_list)
                
                if s_path.exists():
                    with open(s_path, 'r') as f:
                        s_list = [str(s).upper().strip() for s in json.load(f) if s]
                        setattr(self, f"symbols_short_{acc}", s_list)
                
                if l_list or s_list:
                    found_any = True
                    # We use 'Sc:' so this log passes your WeatherLogFilter
                    logger.info(f"☀️ [LOAD] {acc.upper()} Sc: {len(l_list)}L / {len(s_list)}S")

            if not found_any:
                logger.warning(f"☁️ [LOAD] No symbols found in {config.BASE_PATH}. Sc: 0")
        except Exception as e:
            logger.error(f"!! [LOAD_ERR] Sc: {e}")

    async def periodic_cleanup(self):
        """Periodic cleanup task"""
        while self.running:
            try:
                await asyncio.sleep(30)  # Run every 30 seconds instead of 600
                # AGGRESSIVE cleanup: Remove processing_keys stuck >30 seconds
                if self.processing_keys:
                    now_ts = time.time()
                    stale_keys = []
                    for position_key in list(self.processing_keys):
                        added_at = self.processing_keys.get_added_at(position_key)
                        last_processed = self.position_last_processed.get(position_key, 0)
                        # Use added_at if available, otherwise last_processed, otherwise assume stale
                        age = 0
                        if added_at > 0:
                            age = now_ts - added_at
                        elif last_processed > 0:
                            age = now_ts - last_processed
                        else:
                            age = 999  # No timestamp - assume very stale
                        if age > 30:  # 30 seconds
                            stale_keys.append(position_key)
                    for key in stale_keys:
                        self.processing_keys.discard(key)
                    if stale_keys:
                        logger.warning(f"[CLEANUP] Removed {len(stale_keys)} stale processing_keys (stuck >30s): {stale_keys[:5]}")
                if self.indicators_cache:
                    old_keys = [k for k, v in self.indicators_cache.items() if time.time() - self.last_indicators_update > 3600]
                    for key in old_keys:
                        self.indicators_cache.pop(key, None)
                    if old_keys:
                        logger.debug(f"[CLEANUP] Removed {len(old_keys)} stale indicator entries")
            except asyncio.CancelledError:
                logger.info("[update_all_positions_prices] Task cancelled")
                break
            except Exception as e:
                logger.error(f"[update_all_positions_prices] Error: {e}",  exc_info=True )
                await asyncio.sleep(1)
    
    async def initialize_all_monitored_positions(self):
        if not self.position_manager: return
        
        await self.load_leaderboards()
        
        # Context needed ONLY for Whitelist logic
        w20 = await self._get_symbol_set("winners_20")
        w15 = await self._get_symbol_set("winners_15m")
        l20 = await self._get_symbol_set("losers_20")
        l15 = await self._get_symbol_set("losers_15m")
        
        valid_longs_ctx = w20.union(w15)
        valid_shorts_ctx = l20.union(l15)
        context_avail = len(valid_longs_ctx) > 0

        count = 0
        
        for acc in self.target_accounts:
            # 1. Get the base list (Populated by load_leaderboards)
            c_long = set(getattr(self, f"symbols_long_{acc}", []))
            c_short = set(getattr(self, f"symbols_short_{acc}", []))

            # 2. Add TRB Whitelist Logic (The "Always Tradeable" Injection)
            if acc == 'trb' and hasattr(self, 'always_tradeable_trb'):
                for sym in self.always_tradeable_trb:
                    sym = sym.strip().upper()
                    if not sym: continue
                    
                    is_winner = sym in valid_longs_ctx
                    is_loser = sym in valid_shorts_ctx
                    is_nowhere = not is_winner and not is_loser
                    
                    if not context_avail: is_nowhere = True

                    # "If in winners -> Long. If in losers -> Short. If nowhere -> Both."
                    if is_winner or is_nowhere: c_long.add(sym)
                    if is_loser or is_nowhere: c_short.add(sym)

            # 3. Create Positions
            for sym in c_long:
                pk = f"{acc}:{sym}_LONG"
                if pk not in self.position_manager.positions:
                    await self.ensure_position_present(acc, sym, 'LONG', pk)
                    count += 1

            for sym in c_short:
                if sym in self.non_shortable_symbols: continue
                pk = f"{acc}:{sym}_SHORT"
                if pk not in self.position_manager.positions:
                    await self.ensure_position_present(acc, sym, 'SHORT', pk)
                    count += 1

        if count > 0:
            logger.info(f"[INIT] Created {count} placeholders")
            await self.position_manager.save_all_positions()

    async def sync_real_positions_from_api(self):
        try:
            for account_key in self.target_accounts:
                client = TradierAPIClient(config, account_key=account_key)
                await client.connect()
                
                # 1. Get Active Positions from API
                api_positions = await client.get_account_positions(account_key)
                await client.close()
                
                # Handle API response structure
                pos_list =[]
                if isinstance(api_positions, dict):
                    if 'positions' in api_positions:
                        p = api_positions['positions']
                        if p == 'null' or p is None:
                            pass
                        elif isinstance(p, dict) and 'position' in p:
                            pos_list = p['position'] if isinstance(p['position'], list) else [p['position']]
                        elif isinstance(p, list):
                            pos_list = p
                    elif 'position' in api_positions:
                        pos_list = api_positions['position'] if isinstance(api_positions['position'], list) else [api_positions['position']]
                elif isinstance(api_positions, list):
                    pos_list = api_positions

                logger.info(f"[SYNC] API returned {len(pos_list)} active positions for {account_key}")

                # Map API data for easy lookup
                api_map = {}
                for pos in pos_list:
                    sym = pos.get('symbol')
                    if sym:
                        api_map[sym.upper()] = pos

                # 2. Iterate ALL Monitored Symbols for this account
                longs = getattr(self, f"symbols_long_{account_key}",[])
                shorts = getattr(self, f"symbols_short_{account_key}",[])
                all_symbols = set(longs + shorts)
                
                if self.position_manager:
                    for pk, pos in self.position_manager.positions.items():
                        if pk.startswith(f"{account_key}:") and pos and getattr(pos, 'symbol', None):
                            all_symbols.add(pos.symbol)
                
                # Add any symbols found in API that we might not be tracking
                for sym in api_map.keys():
                    all_symbols.add(sym)

                updated_count = 0
                now = datetime.now(timezone.utc)

                for symbol in all_symbols:
                    symbol = symbol.upper()
                    
                    # Check for active data from API
                    api_data = api_map.get(symbol)
                    
                    # Determine values
                    if api_data:
                        raw_qty = float(api_data.get('quantity', 0))
                        cost_basis = float(api_data.get('cost_basis', 0))
                        avg_price = abs(cost_basis / raw_qty) if raw_qty != 0 else 0.0
                        date_acquired = api_data.get('date_acquired', '')
                        unrealized_profit = float(api_data.get('unrealized_profit', 0))
                        realized_profit = float(api_data.get('realized_profit', 0))
                    else:
                        raw_qty = 0.0
                        avg_price = 0.0
                        date_acquired = ""
                        unrealized_profit = 0.0
                        realized_profit = 0.0

                    # Get current price
                    current_price = 0.0
                    try:
                        current_price, ts = await self.get_current_price(symbol)
                        current_price = float(current_price)
                        if current_price <= 0:
                            price_cache_entry = self.price_cache.get(symbol)
                            if price_cache_entry:
                                current_price = float(price_cache_entry.get('price', 0) or 0)
                    except Exception:
                        pass

                    # 3. Update LONG side — only if position exists in memory AND API confirms it
                    long_key = f"{account_key}:{symbol}_LONG"
                    long_pos = self.position_manager.positions.get(long_key) if self.position_manager else None
                    if long_pos:
                        if raw_qty > 0:
                            long_pos.positionAmt = abs(raw_qty)
                            if avg_price > 0: long_pos.entry_price = avg_price
                            if date_acquired: long_pos.entry_time = date_acquired
                            long_pos.unrealized_pnl = unrealized_profit
                            long_pos.realized_pnl = realized_profit
                            long_pos.price_fetch_count = getattr(long_pos, 'price_fetch_count', 0) + 1
                        
                        # Update mark price
                        if current_price > 0:
                            long_pos.mark_price = current_price
                            long_pos.mark_price_last_updated = now
                        
                        # Calculate gain and update ALL related fields
                        entry_price = long_pos.entry_price if long_pos.entry_price > 0 else (current_price if current_price > 0 else 0.0)
                        if long_pos.positionAmt == 0.0:
                            long_pos.gain = 0.0
                        elif entry_price > 0 and current_price > 0:
                            base_entry = entry_price if entry_price > 0 else current_price
                            if base_entry > 0 and not (base_entry < current_price * 0.01 or base_entry > current_price * 100):
                                long_pos.gain = calculate_gain('LONG', current_price, base_entry)
                                if long_pos.positionAmt > 0:
                                    long_pos.max_gain = max(long_pos.gain, long_pos.max_gain)
                        
                        # Apply deteriorations
                        if self.position_manager and hasattr(self.position_manager, 'deteriorate_max_gain'):
                            if long_pos.max_gain != 0.0 and long_pos.positionAmt == 0.0:
                                deteriorated_max_gain = self.position_manager.deteriorate_max_gain(account_key, long_pos)
                                if abs(deteriorated_max_gain - long_pos.max_gain) > 0.01:
                                    long_pos.max_gain = deteriorated_max_gain
                            if long_pos.positionAmt > 0 and current_price > 0:
                                deteriorated_max_qty = self.position_manager.deteriorate_max_quantity(account_key, long_pos, current_price)
                                if long_pos.positionAmt > deteriorated_max_qty:
                                    long_pos.max_quantity = long_pos.positionAmt
                                else:
                                    long_pos.max_quantity = deteriorated_max_qty
                        
                        # Update other calculated fields
                        if long_pos.positionAmt > 0 and entry_price > 0 and current_price > 0:
                            long_pos.unrealized_pnl = (current_price - entry_price) * long_pos.positionAmt
                        
                        # Set ALL timestamp fields
                        long_pos.last_updated = now
                        if not getattr(long_pos, 'opened_at', None) and long_pos.positionAmt > 0:
                            long_pos.opened_at = now
                        
                        updated_count += 1

                    # 4. Update SHORT side — only if position exists in memory AND API confirms it
                    short_key = f"{account_key}:{symbol}_SHORT"
                    short_pos = self.position_manager.positions.get(short_key) if self.position_manager else None
                    if short_pos:
                        if raw_qty < 0:
                            short_pos.positionAmt = abs(raw_qty)
                            if avg_price > 0: short_pos.entry_price = avg_price
                            if date_acquired: short_pos.entry_time = date_acquired
                            short_pos.unrealized_pnl = unrealized_profit
                            short_pos.realized_pnl = realized_profit
                            short_pos.price_fetch_count = getattr(short_pos, 'price_fetch_count', 0) + 1
                        
                        # Update mark price
                        if current_price > 0:
                            short_pos.mark_price = current_price
                            short_pos.mark_price_last_updated = now
                        
                        # Calculate gain and update ALL related fields
                        entry_price = short_pos.entry_price if short_pos.entry_price > 0 else (current_price if current_price > 0 else 0.0)
                        if short_pos.positionAmt == 0.0:
                            short_pos.gain = 0.0
                        elif entry_price > 0 and current_price > 0:
                            base_entry = entry_price if entry_price > 0 else current_price
                            if base_entry > 0 and not (base_entry < current_price * 0.01 or base_entry > current_price * 100):
                                short_pos.gain = calculate_gain('SHORT', current_price, base_entry)
                                if short_pos.positionAmt > 0:
                                    short_pos.max_gain = max(short_pos.gain, short_pos.max_gain)
                        
                        # Apply deteriorations
                        if self.position_manager and hasattr(self.position_manager, 'deteriorate_max_gain'):
                            if short_pos.max_gain != 0.0 and short_pos.positionAmt == 0.0:
                                deteriorated_max_gain = self.position_manager.deteriorate_max_gain(account_key, short_pos)
                                if abs(deteriorated_max_gain - short_pos.max_gain) > 0.01:
                                    short_pos.max_gain = deteriorated_max_gain
                            if short_pos.positionAmt > 0 and current_price > 0:
                                deteriorated_max_qty = self.position_manager.deteriorate_max_quantity(account_key, short_pos, current_price)
                                if short_pos.positionAmt > deteriorated_max_qty:
                                    short_pos.max_quantity = short_pos.positionAmt
                                else:
                                    short_pos.max_quantity = deteriorated_max_qty
                        
                        # Update other calculated fields
                        if short_pos.positionAmt > 0 and entry_price > 0 and current_price > 0:
                            short_pos.unrealized_pnl = (entry_price - current_price) * short_pos.positionAmt
                        
                        # Set ALL timestamp fields
                        short_pos.last_updated = now
                        if not getattr(short_pos, 'opened_at', None) and short_pos.positionAmt > 0:
                            short_pos.opened_at = now
                        
                        updated_count += 1

                logger.info(f"[SYNC] Synchronized {updated_count} positions with ALL fields from API for {account_key}")

            # Force a save to disk to persist the sync
            if self.position_manager and hasattr(self.position_manager, 'save_all'):
                await self.position_manager.save_all()

        except Exception as e:
            logger.error(f"[SYNC] Critical error syncing positions: {e}",  exc_info=True )

    async def validate_and_complete_position(self, position_key: str, position: Any) -> bool:
        """Validate that a position has ALL required fields, fill in missing ones"""
        if not position:
            return False
        
        required_fields = [
            'symbol', 'position_side', 'positionAmt', 'entry_price', 'mark_price',
            'unrealized_pnl', 'realized_pnl', 'gain', 'max_gain', 'prev_gain',
            'max_quantity', 'last_augmentation_amount', 'last_augmentation_price',
            'last_reduction_amount', 'last_reduction_price', 'max_positionSize',
            'was_reentered', 'was_reduced', 'last_signal', 'augment_reason',
            'reduction_reason', 'price_fetch_count'
        ]
        
        now = datetime.now(timezone.utc)
        
        # Check and set missing fields
        missing_fields = []
        
        for field in required_fields:
            if not hasattr(position, field):
                missing_fields.append(field)
                
                # Set default values based on field type
                if field in ['symbol', 'position_side', 'last_signal', 'augment_reason', 'reduction_reason']:
                    setattr(position, field, '')
                elif field in ['positionAmt', 'entry_price', 'mark_price', 'unrealized_pnl', 'realized_pnl',
                            'gain', 'max_gain', 'prev_gain', 'max_quantity', 'last_augmentation_amount',
                            'last_augmentation_price', 'last_reduction_amount', 'last_reduction_price',
                            'max_positionSize']:
                    setattr(position, field, 0.0)
                elif field in ['was_reentered', 'was_reduced']:
                    setattr(position, field, False)
                elif field == 'price_fetch_count':
                    setattr(position, field, 0)
        
        # Ensure datetime fields exist
        datetime_fields = [
            'last_augmentation_time', 'last_reduction_time', 'opened_at',
            'last_updated', 'prev_gain_last_updated', 'mark_price_last_updated'
        ]
        
        for field in datetime_fields:
            if not hasattr(position, field):
                setattr(position, field, None)
        
        # Ensure nullable fields exist
        nullable_fields = ['stop_loss', 'take_profit', 'entry_time', 'last_update']
        for field in nullable_fields:
            if not hasattr(position, field):
                setattr(position, field, None)
        
        # Update timestamps
        if not position.last_updated:
            position.last_updated = now
        
        if missing_fields:
            logger.debug(f"[VALIDATE] Position {position_key} had missing fields: {missing_fields}")
        
        return True

    async def validate_positions_periodic(self):
        """Validates API holdings to clean ghost positions every 45s"""
        logger.info("[validate_pos itions_periodic] Task started")
        while self.running:
            try:
                await asyncio.sleep(45)
                await self.sync_real_positions_from_api()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[VALIDATE] Error: {e}")
                await asyncio.sleep(5)

    def _update_position_entry(self, account_key: str, symbol: str, position_side: str, position: TradierPosition):
        if not position or not symbol or not position_side:
            return
        pk = construct_position_key(account_key, symbol, position_side)
        self.positions_by_account[account_key][pk] = position
        self.positions[pk] = position
        self._position_update_timestamps[pk] = datetime.now(timezone.utc)
        position.last_updated = datetime.now(timezone.utc)
        
    async def is_order_locked(self, position_key: str) -> bool:
        """Compatibility helper for ez_manage queu e_trade_action."""
        if not position_key:
            return False
        try:
            keys = [position_key]
            if not position_key.startswith("execute_now:"):
                keys.append(f"execute_now:{position_key}")
            keys.append(f"execute_now:{position_key}:BUY")
            keys.append(f"execute_now:{position_key}:SELL")
            checked = set(keys)
            if self.redis_manager:
                for key in checked:
                    try:
                        value = await self.redis_manager.get(key)
                        if value is not None:
                            return True
                    except Exception:
                        continue
            now = time.time()
            for key in checked:
                lock_payload = self.active_order_locks.get(key)
                if lock_payload and now - lock_payload.get('timestamp', 0.0) < 180.0:
                    return True
                local_payload = self._local_locks.get(key)
                if local_payload and local_payload.get('expires', 0.0) > now:
                    return True
            return False
        except Exception as e:
            logger.debug(f"[is_order_locked] Error checking {position_key}: {e}")
            return False
                
    async def cleanup_stale_execution_locks(self):
        now = time.time()
        pending_ttl = 70.0
        complete_ttl = 45.0
        stale = []
        for key, payload in list(self.active_order_locks.items()):
            ts = payload.get('timestamp', 0.0)
            status = payload.get('status', 'pending')
            ttl = pending_ttl if status == 'pending' else complete_ttl
            if now - ts > ttl:
                stale.append(key)
        for key in stale:
            self.active_order_locks.pop(key, None)
            
    async def periodic_lock_cleanup_loop(self):
        logger.info("[periodic_lock_cleanup]  Task started")
        while self.running:
            try:
                await self.cleanup_stale_execution_locks()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[periodic_lock_cleanup] Error: {e}")
                await asyncio.sleep(5)
                
    async def heartbeat_loop(self):
        logger.info("[heartbeat_loop]  Task started")
        heartbeat_file = Path.home() / "logs" / "tradier_manage_heartbeat"
        while self.running:
            try:
                heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
                heartbeat_file.write_text(datetime.now(timezone.utc).isoformat())
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[heartbeat_loop] Error writing heartbeat: {e}")
                await asyncio.sleep(5)
                
    async def log_timing_summary_loop(self):
        logger.info("[log_timing_summary]  Task started")
        while self.running:
            try:
                await asyncio.sleep(60)
                queue_sizes = {ak: len(data.get('queue', [])) for ak, data in self.account_position_queues.items()}
                processing_len = len(self.processing_keys)
                order_queue_depth = self.order_queue._orders.qsize() if self.order_queue else 0
                logger.info(f"[TIMING] queues={queue_sizes} processing={processing_len} order_queue={order_queue_depth}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[log_timing_summary] Error: {e}")
                
    async def symbol_watchdog_loop(self):
        logger.info("[symbol_watchdog]  Task started")
        while self.running:
            try:
                await asyncio.sleep(30)
                now = time.time()
                stale_positions = [pk for pk, ts in self.last_monitored_positions.items() if now - ts > 60]
                for pk in stale_positions[:100]:
                    account_key = pk.split(':', 1)[0]
                    queue = self.account_position_queues[account_key]
                    if pk not in queue['set']:
                        queue['queue'].append(pk)
                        queue['set'].add(pk)
                        logger.warning(f"[symbol_watchdog] Re-queued stale position {pk}")
                stale_symbols = [sym for sym, ts in self.last_symbol_monitored.items() if now - ts > 90]
                for symbol in stale_symbols[:50]:
                    indicators = self.get_indicators(symbol)
                    if indicators:
                        self.indicators_cache[symbol.upper()] = indicators
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[symbol_watchdog] Error: {e}")
    
    async def market_data_sync_loop(self):
        """Safe-Path: Force-overwrites memory cache if file on disk is newer."""
        logger.info("🔍 [SAFE-PATH] File Poller Active")
        json_path = config.DATA_DIR / "tradier_indicators_latest.json"
        
        while self.running:
            try:
                if json_path.exists():
                    mtime = os.path.getmtime(json_path)
                    # Check if file has been updated since our last successful load
                    if mtime > self.last_json_mtime:
                        async with _global_file_write_semaphore:
                            async with aiofiles.open(json_path, "rb") as f:
                                content = await f.read()
                                if content:
                                    raw_data = safe_json_loads(content)
                                    if isinstance(raw_data, dict) and len(raw_data) > 0:
                                        # PUNCTUAL FIX: Build a fresh map and replace the reference
                                        # This is atomic and ensures no "ghost" data from 30 mins ago remains
                                        new_snapshot = {}
                                        for sym, vals in raw_data.items():
                                            new_snapshot[sym.upper()] = self._adapt_indicators_for_ez_manage(vals)
                                        
                                        self.market_snapshot = new_snapshot
                                        self.last_json_mtime = mtime
                                        # logger.debug(f"💾 Snapshot Overwritten: {len(new_snapshot)} symbols.")

                await asyncio.sleep(1.0) # Check every second
            except Exception as e:
                logger.error(f"Sync Loop Error: {e}")
                await asyncio.sleep(5)
    
    async def market_snapshot_refresh_loop(self, interval: float = 5.0):
        logger.info("[market_snapshot_refresh]  Task started")
        while self.running:
            try:
                await asyncio.sleep(interval)
                sample_symbols = (self.symbols_long_trc + self.symbols_short_trc)[:20]
                for symbol in sample_symbols:
                    self.get_indicators(symbol)
                self.last_indicators_update = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[market_snapshot_refresh] Error: {e}")
                
    async def enforce_data_sync_loop(self, interval: float = 60.0):
        logger.info("[enforce_data_sync]  Task started")
        while self.running:
            try:
                self._sync_positions_from_manager()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[enforce_data_sync] Error: {e}")
                await asyncio.sleep(5)

    async def refresh_augmented_reduced_loop(self, interval: float = 10.0):
        logger.info("[refresh_augmented_reduced]  Task started")
        while self.running:
            try:
                await asyncio.sleep(interval)
                now = time.time()
                self.augmentation_cooldown_map = {k: v for k, v in self.augmentation_cooldown_map.items() if now - v < 600}
                self.reduction_cooldown_map = {k: v for k, v in self.reduction_cooldown_map.items() if now - v < 600}
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[refresh_augmented_reduced] Error: {e}")
                
    async def cleanup_stale_locks_loop(self):
        logger.info("[cleanup_stale_locks]  Task started")
        while self.running:
            try:
                now = time.time()
                async with self.dedupe_lock:
                    stale = []
                    for key, ts in list(self.order_deduplication.items()):
                        if isinstance(ts, (int, float)):
                            ts_val = float(ts)
                        elif isinstance(ts, dict):
                            ts_val = float(ts.get('timestamp', 0.0) or 0.0)
                        else:
                            ts_val = 0.0
                        if ts_val and (now - ts_val) > 120.0:
                            stale.append(key)
                    for key in stale:
                        self.order_deduplication.pop(key, None)
                await asyncio.sleep(20)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[cleanup_stale_locks] Error: {e}")
                
    async def monitor_memory_loop(self, max_gb: float = 3.0):
        logger.info("[monitor_memory]  Task started")
        process = psutil.Process(os.getpid())
        while self.running:
            try:
                await asyncio.sleep(60)
                mem_gb = process.memory_info().rss / (1024 ** 3)
                if mem_gb > max_gb:
                    logger.warning(f"[monitor_memory] Memory usage high: {mem_gb:.2f} GB")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[monitor_memory] Error: {e}")
                
                
    async def metrics_server_loop(self, host: str = "127.0.0.1", port: int = 8820):
        logger.info(f"[metrics_server]  Starting on {host}:{port}")
        async def handler(reader, writer):
            data = {
                "timestamp": time.time(),
                "queues": {ak: len(qd.get('queue', [])) for ak, qd in self.account_position_queues.items()},
                "processing": len(self.processing_keys),
                "order_queue": self.order_queue._orders.qsize() if self.order_queue else 0
            }
            writer.write((json.dumps(data) + "\n").encode())
            await writer.drain()
            writer.close()
        try:
            server = await asyncio.start_server(handler, host, port)
        except Exception as e:
            logger.warning(f"[metrics_server] Failed to start: {e}")
            return
        self._metrics_runner = server
        try:
            while self.running:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass
        finally:
            server.close()
            await server.wait_closed()
            logger.info("[metrics_server] Stopped")
    
    async def validate_position_before_trade(self, position_key: str, action: str, proposed_order_qty: float) -> Tuple[bool, str, Optional[float]]:
        try:
            from utils import orjson_default, parse_position_key
            account_key, symbol, position_side = parse_position_key(position_key)
            
            local_pos = self.position_manager.get_position(position_key)
            local_holdings = abs(float(getattr(local_pos, 'positionAmt', 0))) if local_pos else 0.0
            
            # --- ACTION LOGIC ---
            if action in["CLOSE", "REDUCE", "FULL_CLOSE", "PROFIT_TAKE"]:
                if local_holdings > 0.00001:
                    final_qty = min(proposed_order_qty, local_holdings)
                    return True, "VALID_LOCAL", final_qty
                return False, "NO_POS_HELD", 0.0

            if action == "AUGMENT":
                if local_holdings > 0.00001:
                    return True, "VALID", proposed_order_qty
                return False, "CANNOT_AUGMENT_FLAT", 0.0

            if action in ["OPEN", "REENTER"]:
                if local_holdings > 0.00001:
                    return False, f"ALREADY_HELD ({local_holdings})", local_holdings
                
                # Ultimate Safety Net: Block execution if opposing position is held!
                opposing_side = "SHORT" if position_side == "LONG" else "LONG"
                from utils import construct_position_key
                opposing_key = construct_position_key(account_key, symbol, opposing_side)
                opposing_pos = self.position_manager.get_position(opposing_key)
                if opposing_pos and abs(float(getattr(opposing_pos, 'positionAmt', 0))) > 0.00001:
                    return False, f"OPPOSING_HELD ({opposing_side})", 0.0
                    
                return True, "VALID", proposed_order_qty
            
            return True, "VALID", proposed_order_qty
        except Exception as e:
            logger.error(f"[validate_position_before_trade] Error: {e}",  exc_info=True )
            if action in ["CLOSE", "REDUCE"]: return True, "FAILSAFE_OPEN", proposed_order_qty
            return False, f"EXCEPTION: {str(e)[:50]}", None         
            
    async def redis_listener_indicators(self):
        logger.info("⚡ [FAST-PATH] PubSub Listener Active")
        while self.running:
            try:
                if not self.redis_manager:
                    self.redis_manager = await get_simple_redis_manager()
                client = await self.redis_manager._get_or_connect("local")
                pubsub = client.pubsub()
                await pubsub.subscribe("tradier_indicators_channel")
                while self.running:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                    if message and message['data']:
                        data = orjson.loads(message['data'])
                        payload = data.get("data", data)
                        if isinstance(payload, dict) and len(payload) > 0:
                            for sym, vals in payload.items():
                                self.market_snapshot[sym.upper()] = self._adapt_indicators_for_ez_manage(vals)
                            self.last_json_mtime = time.time()
                            self.last_market_data_ts = time.time()
            except Exception:
                await asyncio.sleep(5)

    async def sync_positions_loop(self):
        """Robust sync that falls back to disk if Redis dies"""
        logger.info("[SyncLoop] Started Reading Source...")
        while self.running:
            try:
                # Reader.sync already handles internal try/except
                # but we add a small check for speed
                await self.reader.sync(self.target_accounts)
                await asyncio.sleep(1.0) 
            except Exception as e:
                # Clean one-liner
                logger.error(f"[SyncLoop] Redis connection failed, using Disk. ({e})")
                await asyncio.sleep(5)

    async def redis_listener_prices(self):
        """Listen for real-time price updates via Redis Pub/Sub"""
        logger.info("[redis_listener_prices] Task started")
        while self.running:
            try:
                if not self.redis_manager:
                    try:
                        self.redis_manager = await asyncio.wait_for(get_simple_redis_manager(), timeout=1.0)
                    except Exception:
                        self.redis_manager = None
                        logger.warning("Redis not found. Running in pure Disk/Memory mode.")                
                client = await self.redis_manager._get_or_connect("local")
                if not client:
                    logger.warning("[redis_listener_prices] No Redis connections available, retrying in 10s")
                    await asyncio.sleep(10)
                    continue

                channel_name = "tradier_prices_channel"
                pubsub = client.pubsub()
                await pubsub.subscribe(channel_name)
                
                while self.running:
                    try:
                        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                        if message:
                            try:
                                payload = message['data']
                                if isinstance(payload, bytes): payload = payload.decode('utf-8')
                                data = orjson.loads(payload)
                                if data and isinstance(data, dict):
                                    symbol_data = data.get("data", data)
                                    for sym, entry in symbol_data.items():
                                        if not isinstance(entry, dict): continue
                                        p_raw = entry.get('price') or entry.get('last') or entry.get('mark')
                                        if p_raw is None: continue
                                        p = float(p_raw)
                                        t_raw = entry.get('timestamp') or entry.get('date')
                                        if p > 0:
                                            t_obj = parse_ts(t_raw) or datetime.now(timezone.utc)
                                            current = self.price_cache.get(sym, {})
                                            curr_ts = current.get('timestamp', datetime.min.replace(tzinfo=timezone.utc))
                                            if t_obj >= curr_ts:
                                                self.price_cache[sym] = {'price': p, 'timestamp': t_obj}
                            except Exception as e:
                                logger.debug(f"Redis Price Parse Error: {e}")
                        
                        # Fallback for prices if needed? 
                        # Price cache is usually updated by other loops too, but we can add a check here.
                        
                    except Exception as e:
                        logger.debug(f"[redis_listener_prices] Loop error: {e}")
                        await asyncio.sleep(1)
                        if "Timeout" in str(e): break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[redis_listener_prices] Connection error: {e}")
                await asyncio.sleep(5)
                
    async def evaluate_rotation_entry(self, account_key):
        """Relative Strength Rotation: rank all stocks by N-day return (config ROTATION_LOOKBACK_DAYS), BUY top N, SHORT bottom N. Hold 5 days then rebalance."""
        if not getattr(config, 'ROTATION_ENABLED', False): return
        if not is_regular_trading_hours(): return
        snapshot = self._get_full_market_snapshot()
        if not snapshot: return
        scored = []
        for sym, data in snapshot.items():
            if not isinstance(data, dict): continue
            if sym.upper() in self.blacklist: continue
            price = safe_fetch_float(data.get('current_price', 0))
            if price <= 0: continue
            klines_path = config.KLINES_CACHE_DIR / f"{sym.upper()}_D.json"
            if not klines_path.exists(): continue
            try:
                raw = json.loads(klines_path.read_text())
                bars = raw if isinstance(raw, list) else raw.get('bars', [])
                _lb = getattr(self.config, 'ROTATION_LOOKBACK_DAYS', 10)
                if len(bars) < _lb + 1: continue
                closes = [float(b.get('close') or b.get('c') or 0) for b in bars]
                closes = [c for c in closes if c > 0]
                if len(closes) < _lb + 1: continue
                ret_nd = (closes[-1] - closes[-1 - _lb]) / closes[-1 - _lb] if closes[-1 - _lb] > 0 else 0
                scored.append({'symbol': sym.upper(), 'return_nd': ret_nd, 'price': price})
            except Exception:
                continue
        if len(scored) < 8:
            logger.debug(f"[{account_key}] [ROTATION] Not enough symbols with daily data ({len(scored)})")
            return
        scored.sort(key=lambda x: x['return_nd'], reverse=True)
        top_n = getattr(config, 'ROTATION_TOP_N', 5)
        bottom_n = getattr(config, 'ROTATION_BOTTOM_N', 3)
        winners = scored[:top_n]
        losers = scored[-bottom_n:]
        positions = self.position_manager.get_positions_by_account(account_key)
        balance = self.get_current_portfolio_balance()
        long_pct = balance.get('long_pct', 0.5)
        rot_size = getattr(config, 'ROTATION_POSITION_SIZE', 800.0)
        hold_days = getattr(config, 'ROTATION_HOLD_DAYS', 5)
        now_ts = time.time()
        for item in winners:
            sym = item['symbol']
            pk = f"{account_key}:{sym}_LONG"
            pos = positions.get(pk)
            has_pos = pos and abs(float(getattr(pos, 'positionAmt', 0))) > 0
            if has_pos:
                opened_at = getattr(pos, 'opened_at', None)
                if opened_at:
                    ots = opened_at.timestamp() if hasattr(opened_at, 'timestamp') else float(opened_at) if isinstance(opened_at, (int, float)) else now_ts
                    age_days = (now_ts - ots) / 86400.0
                    if age_days >= hold_days and 'ROTATION' in str(getattr(pos, 'augment_reason', '')):
                        logger.info(f"[{account_key}] [ROTATION] REBALANCE EXIT {sym}: held {age_days:.1f} days >= {hold_days}")
                        await queue_trade_action(self.order_queue, self, pk, "CLOSE", f"ROTATION_REBALANCE_{age_days:.0f}d", 80.0)
                continue
            if long_pct > 0.67:
                logger.debug(f"[{account_key}] [ROTATION] Skip LONG {sym}: L/S ratio too high ({long_pct:.2f})")
                continue
            if not self.is_symbol_tradeable(sym, account_key, 'LONG'): continue
            # BACKTEST_CHANGE_T47: rotation SMA200 filter — only long when price below SMA200 (mean-reversion)
            if getattr(config, 'ROTATION_SMA200_FILTER', False):
                _rot_sma = safe_fetch_float(data.get('sma_200_D', 0)) or safe_fetch_float(data.get('sma_200_1h', 0))
                if _rot_sma > 0 and item['price'] > _rot_sma:
                    logger.debug(f"[{account_key}] [ROTATION] Skip LONG {sym}: price {item['price']:.2f} > SMA200 {_rot_sma:.2f}")
                    continue
            qty = max(1, int(rot_size / item['price']))
            logger.info(f"[{account_key}] [ROTATION] LONG {sym}: 10d-return={item['return_nd']:.2%} qty={qty}")
            await queue_trade_action(self.order_queue, self, pk, "OPEN", f"ROTATION_ENTRY_L ret={item['return_nd']:.2%}", 75.0, override_qty=qty)
        for item in losers:
            sym = item['symbol']
            if sym in self.non_shortable_symbols: continue
            pk = f"{account_key}:{sym}_SHORT"
            pos = positions.get(pk)
            has_pos = pos and abs(float(getattr(pos, 'positionAmt', 0))) > 0
            if has_pos:
                opened_at = getattr(pos, 'opened_at', None)
                if opened_at:
                    ots = opened_at.timestamp() if hasattr(opened_at, 'timestamp') else float(opened_at) if isinstance(opened_at, (int, float)) else now_ts
                    age_days = (now_ts - ots) / 86400.0
                    if age_days >= hold_days and 'ROTATION' in str(getattr(pos, 'augment_reason', '')):
                        logger.info(f"[{account_key}] [ROTATION] REBALANCE EXIT {sym}: held {age_days:.1f} days >= {hold_days}")
                        await queue_trade_action(self.order_queue, self, pk, "CLOSE", f"ROTATION_REBALANCE_{age_days:.0f}d", 80.0)
                continue
            if long_pct < 0.33:
                logger.debug(f"[{account_key}] [ROTATION] Skip SHORT {sym}: L/S ratio too low ({long_pct:.2f})")
                continue
            if not self.is_symbol_tradeable(sym, account_key, 'SHORT'): continue
            # BACKTEST_CHANGE_T47: rotation SMA200 filter — only short when price above SMA200
            if getattr(config, 'ROTATION_SMA200_FILTER', False):
                _rot_sma_s = safe_fetch_float(data.get('sma_200_D', 0)) or safe_fetch_float(data.get('sma_200_1h', 0))
                if _rot_sma_s > 0 and item['price'] < _rot_sma_s:
                    logger.debug(f"[{account_key}] [ROTATION] Skip SHORT {sym}: price {item['price']:.2f} < SMA200 {_rot_sma_s:.2f}")
                    continue
            qty = max(1, int(rot_size / item['price']))
            logger.info(f"[{account_key}] [ROTATION] SHORT {sym}: 10d-return={item['return_nd']:.2%} qty={qty}")
            await queue_trade_action(self.order_queue, self, pk, "OPEN", f"ROTATION_ENTRY_S ret={item['return_nd']:.2%}", 75.0, override_qty=qty)

    async def evaluate_rsi2_entry(self, account_key):
        """RSI(2) Mean Reversion: RSI(2)<5 + above SMA200 = BUY, RSI(2)>95 + below SMA200 = SHORT. Exit on RSI(2) cross 65/35."""
        if not getattr(config, 'RSI2_ENABLED', False): return
        if not is_regular_trading_hours(): return
        snapshot = self._get_full_market_snapshot()
        if not snapshot: return
        rsi2_entry = getattr(config, 'RSI2_ENTRY_THRESHOLD', 5.0)
        rsi2_exit_long = getattr(config, 'RSI2_EXIT_THRESHOLD_LONG', 65.0)
        rsi2_exit_short = getattr(config, 'RSI2_EXIT_THRESHOLD_SHORT', 35.0)
        rsi2_size = getattr(config, 'RSI2_POSITION_SIZE', 800.0)
        positions = self.position_manager.get_positions_by_account(account_key)
        balance = self.get_current_portfolio_balance()
        long_pct = balance.get('long_pct', 0.5)
        for sym, data in snapshot.items():
            if not isinstance(data, dict): continue
            sym = sym.upper()
            if sym in self.blacklist: continue
            price = safe_fetch_float(data.get('current_price', 0))
            if price <= 0: continue
            klines_path = config.KLINES_CACHE_DIR / f"{sym}_D.json"
            if not klines_path.exists(): continue
            try:
                raw = json.loads(klines_path.read_text())
                bars = raw if isinstance(raw, list) else raw.get('bars', [])
                if len(bars) < 4: continue
                closes = [float(b.get('close') or b.get('c') or 0) for b in bars]
                closes = [c for c in closes if c > 0]
                if len(closes) < 4: continue
            except Exception:
                continue
            gains = [max(0, closes[j] - closes[j-1]) for j in range(1, len(closes))]
            losses = [max(0, closes[j-1] - closes[j]) for j in range(1, len(closes))]
            if len(gains) < 2: continue
            avg_gain = sum(gains[-2:]) / 2.0
            avg_loss = sum(losses[-2:]) / 2.0
            if avg_loss == 0:
                rsi2 = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi2 = 100.0 - (100.0 / (1.0 + rs))
            sma_200 = safe_fetch_float(data.get('sma_200_D', 0)) or safe_fetch_float(data.get('sma_200_1h', 0))
            pk_long = f"{account_key}:{sym}_LONG"
            pk_short = f"{account_key}:{sym}_SHORT"
            pos_long = positions.get(pk_long)
            pos_short = positions.get(pk_short)
            has_long = pos_long and abs(float(getattr(pos_long, 'positionAmt', 0))) > 0
            has_short = pos_short and abs(float(getattr(pos_short, 'positionAmt', 0))) > 0
            if has_long and 'RSI2' in str(getattr(pos_long, 'augment_reason', '')):
                if rsi2 > rsi2_exit_long:
                    logger.info(f"[{account_key}] [RSI2] EXIT LONG {sym}: RSI(2)={rsi2:.1f} > {rsi2_exit_long}")
                    await queue_trade_action(self.order_queue, self, pk_long, "CLOSE", f"RSI2_EXIT_LONG rsi2={rsi2:.1f}", 85.0)
                continue
            if has_short and 'RSI2' in str(getattr(pos_short, 'augment_reason', '')):
                if rsi2 < rsi2_exit_short:
                    logger.info(f"[{account_key}] [RSI2] EXIT SHORT {sym}: RSI(2)={rsi2:.1f} < {rsi2_exit_short}")
                    await queue_trade_action(self.order_queue, self, pk_short, "CLOSE", f"RSI2_EXIT_SHORT rsi2={rsi2:.1f}", 85.0)
                continue
            if not has_long and rsi2 < rsi2_entry and sma_200 > 0 and price > sma_200:
                if long_pct > 0.67:
                    continue
                if not self.is_symbol_tradeable(sym, account_key, 'LONG'): continue
                qty = max(1, int(rsi2_size / price))
                logger.info(f"[{account_key}] [RSI2] LONG {sym}: RSI(2)={rsi2:.1f} < {rsi2_entry}, price={price:.2f} > SMA200={sma_200:.2f}, qty={qty}")
                await queue_trade_action(self.order_queue, self, pk_long, "OPEN", f"RSI2_MEAN_REVERSION_L rsi2={rsi2:.1f}", 80.0, override_qty=qty)
            if not has_short and rsi2 > (100.0 - rsi2_entry) and sma_200 > 0 and price < sma_200:
                if sym in self.non_shortable_symbols: continue
                if long_pct < 0.33:
                    continue
                if not self.is_symbol_tradeable(sym, account_key, 'SHORT'): continue
                qty = max(1, int(rsi2_size / price))
                logger.info(f"[{account_key}] [RSI2] SHORT {sym}: RSI(2)={rsi2:.1f} > {100.0 - rsi2_entry}, price={price:.2f} < SMA200={sma_200:.2f}, qty={qty}")
                await queue_trade_action(self.order_queue, self, pk_short, "OPEN", f"RSI2_MEAN_REVERSION_S rsi2={rsi2:.1f}", 80.0, override_qty=qty)

    async def evaluate_gap_fill(self, account_key):
        """Gap Fill: at 9:35 AM, find stocks that gapped 1-5%, enter opposite direction targeting 50% fill. OOS Sharpe 9.05."""
        if not getattr(config, 'GAP_FILL_ENABLED', False): return
        if not is_regular_trading_hours(): return
        _est = timezone(timedelta(hours=-5))
        _today_key = datetime.now(timezone.utc).astimezone(_est).strftime('%Y%m%d')
        if self._gap_fill_ran_today == _today_key: return
        self._gap_fill_ran_today = _today_key
        snapshot = self._get_full_market_snapshot()
        if not snapshot: return
        min_gap = getattr(config, 'GAP_FILL_MIN_GAP_PCT', 1.0)
        max_gap = getattr(config, 'GAP_FILL_MAX_GAP_PCT', 5.0)
        stop_mult = getattr(config, 'GAP_FILL_STOP_MULT', 0.3)
        tp_fill = getattr(config, 'GAP_FILL_TP_FILL_PCT', 0.5)
        gf_size = getattr(config, 'GAP_FILL_POSITION_SIZE', 400.0)
        positions = self.position_manager.get_positions_by_account(account_key)
        balance = self.get_current_portfolio_balance()
        long_pct = balance.get('long_pct', 0.5)
        _entered = 0
        for sym, data in snapshot.items():
            if not isinstance(data, dict): continue
            sym = sym.upper()
            if sym in self.blacklist: continue
            price = safe_fetch_float(data.get('current_price', 0))
            if price <= 0: continue
            klines_path = config.KLINES_CACHE_DIR / f"{sym}_D.json"
            if not klines_path.exists(): continue
            try:
                raw = json.loads(klines_path.read_text())
                bars = raw if isinstance(raw, list) else raw.get('bars', [])
                if len(bars) < 2: continue
                closes = [float(b.get('close') or b.get('c') or 0) for b in bars]
                closes = [c for c in closes if c > 0]
                if len(closes) < 2: continue
                prev_close = closes[-1]
            except Exception:
                continue
            today_open = price
            if prev_close <= 0: continue
            gap_pct = (today_open - prev_close) / prev_close * 100.0
            gap_abs = today_open - prev_close
            if abs(gap_pct) < min_gap or abs(gap_pct) > max_gap: continue
            if gap_pct < -min_gap:
                pk = f"{account_key}:{sym}_LONG"
                pos = positions.get(pk)
                has_pos = pos and abs(float(getattr(pos, 'positionAmt', 0))) > 0
                if has_pos: continue
                if long_pct > 0.67: continue
                if not self.is_symbol_tradeable(sym, account_key, 'LONG'): continue
                stop_price = today_open - (stop_mult * abs(gap_abs))
                tp_price = today_open + (tp_fill * abs(gap_abs))
                qty = max(1, int(gf_size / price))
                logger.info(f"[{account_key}] [GAP_FILL] BUY {sym}: gap={gap_pct:.2f}% prev_close={prev_close:.2f} open={today_open:.2f} stop={stop_price:.2f} tp={tp_price:.2f} qty={qty}")
                self._gap_fill_positions[pk] = {'stop': stop_price, 'tp': tp_price, 'entry': today_open, 'gap_pct': gap_pct, 'prev_close': prev_close, 'opened_at': datetime.now(timezone.utc)}
                await queue_trade_action(self.order_queue, self, pk, "OPEN", f"GAP_FILL_BUY gap={gap_pct:.2f}%", 90.0, override_qty=qty)
                _entered += 1
            elif gap_pct > min_gap:
                if sym in self.non_shortable_symbols: continue
                pk = f"{account_key}:{sym}_SHORT"
                pos = positions.get(pk)
                has_pos = pos and abs(float(getattr(pos, 'positionAmt', 0))) > 0
                if has_pos: continue
                if long_pct < 0.33: continue
                if not self.is_symbol_tradeable(sym, account_key, 'SHORT'): continue
                stop_price = today_open + (stop_mult * abs(gap_abs))
                tp_price = today_open - (tp_fill * abs(gap_abs))
                qty = max(1, int(gf_size / price))
                logger.info(f"[{account_key}] [GAP_FILL] SHORT {sym}: gap={gap_pct:.2f}% prev_close={prev_close:.2f} open={today_open:.2f} stop={stop_price:.2f} tp={tp_price:.2f} qty={qty}")
                self._gap_fill_positions[pk] = {'stop': stop_price, 'tp': tp_price, 'entry': today_open, 'gap_pct': gap_pct, 'prev_close': prev_close, 'opened_at': datetime.now(timezone.utc)}
                await queue_trade_action(self.order_queue, self, pk, "OPEN", f"GAP_FILL_SELL gap={gap_pct:.2f}%", 90.0, override_qty=qty)
                _entered += 1
        logger.info(f"[{account_key}] [GAP_FILL] Scan complete: {_entered} gap fill entries queued")

    async def monitor_gap_fill_exits(self, account_key):
        """Monitor open gap fill positions for stop, TP, or EOD forced exit."""
        if not self._gap_fill_positions: return
        _est = timezone(timedelta(hours=-5))
        _now_est = datetime.now(timezone.utc).astimezone(_est)
        _now_t = _now_est.time()
        _is_eod = _now_t >= dt_time(15, 55)
        _today_key = _now_est.strftime('%Y%m%d')
        _to_remove = []
        positions = self.position_manager.get_positions_by_account(account_key)
        for pk, gf in list(self._gap_fill_positions.items()):
            if not pk.startswith(f"{account_key}:"): continue
            pos = positions.get(pk)
            has_pos = pos and abs(float(getattr(pos, 'positionAmt', 0))) > 0
            if not has_pos:
                _to_remove.append(pk)
                continue
            sym = pk.split(':')[1].rsplit('_', 1)[0]
            price, _ = await self.get_current_price(sym)
            price = float(price)
            if price <= 0: continue
            is_long = pk.endswith('_LONG')
            stop_hit = (is_long and price <= gf['stop']) or (not is_long and price >= gf['stop'])
            tp_hit = (is_long and price >= gf['tp']) or (not is_long and price <= gf['tp'])
            if stop_hit:
                logger.info(f"[{account_key}] [GAP_FILL] STOP HIT {pk}: price={price:.2f} stop={gf['stop']:.2f}")
                await queue_trade_action(self.order_queue, self, pk, "CLOSE", f"GAP_FILL_STOP price={price:.2f}", 95.0)
                _to_remove.append(pk)
            elif tp_hit:
                logger.info(f"[{account_key}] [GAP_FILL] TP HIT {pk}: price={price:.2f} tp={gf['tp']:.2f} (50% fill)")
                await queue_trade_action(self.order_queue, self, pk, "CLOSE", f"GAP_FILL_TP price={price:.2f}", 95.0)
                _to_remove.append(pk)
            elif _is_eod:
                logger.info(f"[{account_key}] [GAP_FILL] EOD EXIT {pk}: price={price:.2f} (forced close at 15:55)")
                await queue_trade_action(self.order_queue, self, pk, "CLOSE", f"GAP_FILL_EOD price={price:.2f}", 95.0)
                _to_remove.append(pk)
        for pk in _to_remove:
            self._gap_fill_positions.pop(pk, None)
        if _now_t >= dt_time(16, 0):
            _stale = [pk for pk in self._gap_fill_positions if pk.startswith(f"{account_key}:")]
            for pk in _stale:
                self._gap_fill_positions.pop(pk, None)

    async def _rotation_rsi2_loop(self):
        """Background loop: runs rotation at 9:35/15:55, RSI2 every 15min, gap fill at 9:35 + exit monitoring every 60s."""
        logger.info("[ROTATION+RSI2+GAP_FILL] Strategy loop started")
        await asyncio.sleep(20)
        _last_rotation_run = 0
        _last_rsi2_run = 0
        _last_gf_monitor = 0
        while self.running:
            try:
                if not is_regular_trading_hours():
                    await asyncio.sleep(60)
                    continue
                _est = timezone(timedelta(hours=-5))
                _now_est = datetime.now(timezone.utc).astimezone(_est)
                _now_t = _now_est.time()
                _today_key = _now_est.strftime('%Y%m%d')
                rotation_windows = [(dt_time(9, 34), dt_time(9, 40)), (dt_time(15, 50), dt_time(15, 59))]
                in_rotation_window = any(lo <= _now_t <= hi for lo, hi in rotation_windows)
                rot_key = f"{_today_key}_{_now_t.hour}"
                if in_rotation_window and _last_rotation_run != rot_key:
                    _last_rotation_run = rot_key
                    for acc in self.target_accounts:
                        try:
                            await self.evaluate_rotation_entry(acc)
                        except Exception as e:
                            logger.error(f"[ROTATION] Error for {acc}: {e}", exc_info=True)
                    for acc in self.target_accounts:
                        try:
                            await self.evaluate_gap_fill(acc)
                        except Exception as e:
                            logger.error(f"[GAP_FILL] Error for {acc}: {e}", exc_info=True)
                if time.time() - _last_rsi2_run >= 900:
                    _last_rsi2_run = time.time()
                    for acc in self.target_accounts:
                        try:
                            await self.evaluate_rsi2_entry(acc)
                        except Exception as e:
                            logger.error(f"[RSI2] Error for {acc}: {e}", exc_info=True)
                if self._gap_fill_positions and time.time() - _last_gf_monitor >= 60:
                    _last_gf_monitor = time.time()
                    for acc in self.target_accounts:
                        try:
                            await self.monitor_gap_fill_exits(acc)
                        except Exception as e:
                            logger.error(f"[GAP_FILL_MONITOR] Error for {acc}: {e}", exc_info=True)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ROTATION+RSI2+GAP_FILL] Loop error: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def start(self):
        json_path = config.DATA_DIR / "tradier_indicators_latest.json"
        if json_path.exists():
            try:
                with open(json_path, 'rb') as f:
                    raw = safe_json_loads(f.read())
                    self.market_snapshot = {k.upper(): self._adapt_indicators_for_ez_manage(v) for k, v in raw.items()}
                logger.info(f"✅ Initialized snapshot with {len(self.market_snapshot)} symbols.")
            except Exception: pass
        try:
            logger.info("📡 Connecting to Redis (2s Timeout)...")
            self.redis_manager = await asyncio.wait_for(get_simple_redis_manager(), timeout=2.0)
            if self.redis_manager:
                # Register the PubSub callback
                await self.redis_manager.subscribe("tradier_indicators_channel", self._on_indicators_update)
                logger.info("📡 Fast-Path PubSub Active.")
        except Exception:
            logger.warning("🔌 Redis timed out. Running in Safe-Path (JSON) mode.")
        await self.reader.connect()
        self.api_client = TradierAPIClient(config, account_key=self.target_accounts[0])
        await self.api_client.connect()
        await self.reader.sync(self.target_accounts)
        self.running = True        
        self.background_tasks.append(asyncio.create_task(self.metrics_server_loop(host="0.0.0.0", port=8820)))
        self.background_tasks.append(asyncio.create_task(self.periodic_sentiment_rebalancing()))
        self.scalp_strategy = StockScalpStrategy(self)
        self.background_tasks.append(asyncio.create_task(self.scalp_strategy.run_loop()))
        self.background_tasks.append(asyncio.create_task(self.monitor_ladder_levels(self.order_queue)))
        self.background_tasks.append(asyncio.create_task(monitor_direct_high_gain_positions(self, self.order_queue)))
        self.background_tasks.append(asyncio.create_task(self.sync_positions_loop())) 
        self.background_tasks.append(asyncio.create_task(self.validate_positions_periodic()))
        self.background_tasks.append(asyncio.create_task(redis_listener_signals(self)))
        self.background_tasks.append(asyncio.create_task(self.redis_listener_indicators())) # Fast Path
        self.background_tasks.append(asyncio.create_task(self.redis_listener_prices()))
        self.background_tasks.append(asyncio.create_task(self.market_data_sync_loop())) # Safe Path
        self.background_tasks.append(asyncio.create_task(self.update_all_positions_prices()))
        self.background_tasks.append(asyncio.create_task(self.monitor_system_state()))
        self.background_tasks.append(asyncio.create_task(self.order_queue.process_orders()))
        self.background_tasks.append(asyncio.create_task(self.last_hour_balancing_loop()))
        self.background_tasks.append(asyncio.create_task(self._rotation_rsi2_loop()))
        for acc in self.target_accounts:
            self.background_tasks.append(asyncio.create_task(monitor_stale_augmentations(self, acc, self.order_queue)))
            self.background_tasks.append(asyncio.create_task(periodic_direct_high_gain_reopen(self, self.order_queue, acc)))
            self.background_tasks.append(asyncio.create_task(self.periodic_tasks(acc, self.order_queue)))
            self.background_tasks.append(asyncio.create_task(continuous_queue_processor(self.order_queue, self, acc)))
            self.background_tasks.append(asyncio.create_task(process_symbols_periodically(self.order_queue, self, acc)))
            self.background_tasks.append(asyncio.create_task(periodic_override_check(self, acc)))
        self.background_tasks.append(asyncio.create_task(tradier_performance_report_loop(self)))
        self.background_tasks.append(asyncio.create_task(tradier_outlier_scan_loop(self)))
        self.background_tasks.append(asyncio.create_task(tradier_capital_reallocation_loop(self)))
        logger.info("☀️ ✅ Manager Running.")
        await self.trading_loop()
        
    async def stop(self):
        self.running = False
        logger.info("Stopping Manager...")
        for t in self.background_tasks: t.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        await self.reader.close()
        if self.api_client: await self.api_client.close()

# ═══════════════════════════════════════════════════════════════════
# PERFORMANCE TRACKING + OUTLIER DETECTION + CAPITAL ALLOCATION
# Inline loops — write reports to data/reports/tradier_*
# ═══════════════════════════════════════════════════════════════════

async def tradier_performance_report_loop(trade_manager):
    """Every 5 min: compute per-symbol rolling stats, write data/reports/tradier_performance_report.txt + .json."""
    await asyncio.sleep(30.0)
    report_dir = Path(config.BASE_PATH) / 'data' / 'reports'
    report_dir.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            if not config.SYMBOL_PERF_ENABLED:
                await asyncio.sleep(300)
                continue
            from ez_symbol_performance import refresh_cache
            stats = refresh_cache()
            if not stats:
                await asyncio.sleep(300)
                continue
            tiers = {'A': [], 'B': [], 'C': []}
            for sym, s in sorted(stats.items(), key=lambda x: x[1].get('total_pnl_pct', 0), reverse=True):
                tiers[s.get('tier', 'B')].append(s)
            now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            open_symbols = set()
            for pk, pos in trade_manager.positions.items():
                if pos and abs(getattr(pos, 'positionAmt', 0) or getattr(pos, 'quantity', 0) or 0) > 0:
                    sym = pk.split(':')[-1].rsplit('_', 1)[0] if ':' in pk else pk.rsplit('_', 1)[0]
                    open_symbols.add(sym)
            lines = [f"TRADIER PERFORMANCE REPORT — {now_str}", f"{'=' * 80}", f"Total: {len(stats)} symbols | A={len(tiers['A'])} B={len(tiers['B'])} C={len(tiers['C'])}", ""]
            for tier_name in ('A', 'B', 'C'):
                items = tiers[tier_name]
                lines.append(f"--- TIER {tier_name} ({len(items)} symbols) ---")
                for s in items:
                    marker = " ** OPEN **" if s['symbol'] in open_symbols else ""
                    lines.append(f"  {s['symbol']:>15s}  WR={s['win_rate']:.0%}  avg={s['avg_gain_pct']:+.2f}%  total={s['total_pnl_pct']:+.1f}%  trades={s['trade_count']:3d}  mult={s['order_multiplier']:.2f}{marker}")
                lines.append("")
            total_pnl = sum(s['total_pnl_pct'] for s in stats.values())
            lines.extend(["SUMMARY:", f"  Total PnL: {total_pnl:+.1f}%", ""])
            with open(report_dir / 'tradier_performance_report.txt', 'w') as f:
                f.write('\n'.join(lines))
            logger.info(f"[PERF_REPORT] Written: {len(stats)} symbols (A={len(tiers['A'])} B={len(tiers['B'])} C={len(tiers['C'])})")
        except Exception as e:
            logger.error(f"[PERF_REPORT] Error: {e}", exc_info=True)
        await asyncio.sleep(config.SYMBOL_PERF_REFRESH_SECONDS)

async def tradier_outlier_scan_loop(trade_manager):
    """Every 60s: scan all positions for stuck/runaway/stale, write data/reports/tradier_outlier_report.txt."""
    await asyncio.sleep(45.0)
    report_dir = Path(config.BASE_PATH) / 'data' / 'reports'
    report_dir.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            if not config.OUTLIER_DETECTOR_ENABLED:
                await asyncio.sleep(60)
                continue
            now = datetime.now(timezone.utc)
            all_alerts = []
            total_positions = 0
            for pk, pos in trade_manager.positions.items():
                if not pos:
                    continue
                amt = abs(getattr(pos, 'positionAmt', 0) or getattr(pos, 'quantity', 0) or 0)
                if amt == 0:
                    continue
                total_positions += 1
                entry_price = safe_fetch_float(getattr(pos, 'entry_price', 0) or getattr(pos, 'entryPrice', 0) or getattr(pos, 'average_cost', 0), 0.0)
                mark_price = safe_fetch_float(getattr(pos, 'mark_price', 0) or getattr(pos, 'markPrice', 0) or getattr(pos, 'current_price', 0), 0.0)
                if entry_price <= 0 or mark_price <= 0:
                    continue
                sym = pk.split(':')[-1].rsplit('_', 1)[0] if ':' in pk else pk.rsplit('_', 1)[0]
                is_long = pk.endswith('_LONG')
                # RUNAWAY_LOSS
                loss_pct = ((entry_price - mark_price) / entry_price * 100) if is_long else ((mark_price - entry_price) / entry_price * 100)
                if loss_pct > 5.0:
                    loss_usd = loss_pct / 100 * amt * mark_price
                    severity = 'CRITICAL' if loss_pct > 10.0 else 'WARNING'
                    all_alerts.append({'position_key': pk, 'symbol': sym, 'type': 'RUNAWAY_LOSS', 'severity': severity, 'loss_pct': round(loss_pct, 3), 'est_loss_usd': round(loss_usd, 2)})
                # STALE_ACTIVITY
                last_activity = getattr(pos, 'last_augmentation_time', None) or getattr(pos, 'last_reduction_time', None)
                if last_activity:
                    try:
                        if isinstance(last_activity, datetime):
                            idle_hours = (now - last_activity).total_seconds() / 3600
                        else:
                            idle_hours = (now - datetime.fromisoformat(str(last_activity).replace('Z', '+00:00'))).total_seconds() / 3600
                    except Exception:
                        idle_hours = 0
                    if idle_hours >= config.OUTLIER_STALE_HOURS:
                        all_alerts.append({'position_key': pk, 'symbol': sym, 'type': 'STALE_ACTIVITY', 'severity': 'WARNING', 'idle_h': round(idle_hours, 1)})
            now_str = now.strftime('%Y-%m-%d %H:%M UTC')
            lines = [f"TRADIER OUTLIER REPORT — {now_str}", f"{'=' * 80}", f"Scanned: {total_positions} positions | Alerts: {len(all_alerts)}", ""]
            for atype in ('RUNAWAY_LOSS', 'STALE_ACTIVITY'):
                typed = [a for a in all_alerts if a['type'] == atype]
                if typed:
                    lines.append(f"--- {atype} ({len(typed)}) ---")
                    for a in typed:
                        sev = 'CRIT' if a['severity'] == 'CRITICAL' else 'WARN'
                        detail_parts = [f"{k}={v}" for k, v in a.items() if k not in ('position_key', 'symbol', 'type', 'severity')]
                        lines.append(f"  [{sev}] {a['position_key']:>30s}  {' '.join(detail_parts)}")
                    lines.append("")
            if not all_alerts:
                lines.append("  No outliers detected.")
            with open(report_dir / 'tradier_outlier_report.txt', 'w') as f:
                f.write('\n'.join(lines))
            if all_alerts:
                logger.info(f"[OUTLIER_SCAN] {len(all_alerts)} alerts across {total_positions} positions")
        except Exception as e:
            logger.error(f"[OUTLIER_SCAN] Error: {e}", exc_info=True)
        await asyncio.sleep(config.OUTLIER_SCAN_INTERVAL)

async def tradier_capital_reallocation_loop(trade_manager):
    """Every 5 min: capital distribution analysis, write data/reports/tradier_capital_report.txt."""
    await asyncio.sleep(60.0)
    report_dir = Path(config.BASE_PATH) / 'data' / 'reports'
    report_dir.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            if not config.SYMBOL_PERF_ENABLED:
                await asyncio.sleep(300)
                continue
            from ez_symbol_performance import get_symbol_tier, get_performance_multiplier
            now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            positions_data = []
            total_capital = 0.0
            for pk, pos in trade_manager.positions.items():
                if not pos:
                    continue
                amt = abs(getattr(pos, 'positionAmt', 0) or getattr(pos, 'quantity', 0) or 0)
                if amt == 0:
                    continue
                mark = safe_fetch_float(getattr(pos, 'mark_price', 0) or getattr(pos, 'current_price', 0), 0.0)
                if mark <= 0:
                    continue
                value_usd = amt * mark
                gain = safe_fetch_float(getattr(pos, 'gain', 0), 0.0)
                sym = pk.split(':')[-1].rsplit('_', 1)[0] if ':' in pk else pk.rsplit('_', 1)[0]
                tier = get_symbol_tier(sym, 'B')
                perf_mult = get_performance_multiplier(sym, 1.0)
                total_capital += value_usd
                positions_data.append({'pk': pk, 'symbol': sym, 'value_usd': round(value_usd, 2), 'gain_pct': round(gain, 3), 'tier': tier, 'perf_mult': round(perf_mult, 2)})
            if not positions_data:
                await asyncio.sleep(300)
                continue
            positions_data.sort(key=lambda p: p['value_usd'], reverse=True)
            tier_capital = {'A': 0.0, 'B': 0.0, 'C': 0.0}
            tier_count = {'A': 0, 'B': 0, 'C': 0}
            for p in positions_data:
                tier_capital[p['tier']] += p['value_usd']
                tier_count[p['tier']] += 1
            winners = [p for p in positions_data if p['tier'] == 'A' and p['gain_pct'] > 0.1]
            losers = [p for p in positions_data if p['tier'] == 'C' or (p['tier'] == 'B' and p['gain_pct'] < -2.0)]
            mediocre = [p for p in positions_data if abs(p['gain_pct']) < 1.0 and p['perf_mult'] <= 1.0 and p['tier'] != 'A']
            lines = [f"TRADIER CAPITAL REPORT — {now_str}", f"{'=' * 80}", f"Total: ${total_capital:,.2f} across {len(positions_data)} positions", ""]
            lines.append("TIER DISTRIBUTION:")
            for t in ('A', 'B', 'C'):
                pct = tier_capital[t] / total_capital * 100 if total_capital > 0 else 0
                lines.append(f"  Tier {t}: ${tier_capital[t]:>10,.2f} ({pct:5.1f}%) — {tier_count[t]} positions")
            lines.append("")
            lines.append(f"WINNERS [{len(winners)}]:")
            for p in sorted(winners, key=lambda x: x['gain_pct'], reverse=True)[:10]:
                lines.append(f"  {p['pk']:>30s}  ${p['value_usd']:>8,.2f}  gain={p['gain_pct']:+.2f}%")
            lines.append("")
            lines.append(f"LOSERS [{len(losers)}]:")
            for p in sorted(losers, key=lambda x: x['gain_pct'])[:10]:
                lines.append(f"  {p['pk']:>30s}  ${p['value_usd']:>8,.2f}  gain={p['gain_pct']:+.2f}%  tier={p['tier']}")
            lines.append("")
            lines.append(f"MEDIOCRE [{len(mediocre)}]:")
            for p in sorted(mediocre, key=lambda x: x['value_usd'], reverse=True)[:10]:
                lines.append(f"  {p['pk']:>30s}  ${p['value_usd']:>8,.2f}  gain={p['gain_pct']:+.2f}%")
            lines.append("")
            with open(report_dir / 'tradier_capital_report.txt', 'w') as f:
                f.write('\n'.join(lines))
            logger.info(f"[CAPITAL_REPORT] ${total_capital:,.0f} | A=${tier_capital['A']:,.0f}({tier_count['A']}) B=${tier_capital['B']:,.0f}({tier_count['B']}) C=${tier_capital['C']:,.0f}({tier_count['C']})")
        except Exception as e:
            logger.error(f"[CAPITAL_REPORT] Error: {e}", exc_info=True)
        await asyncio.sleep(config.SYMBOL_PERF_REFRESH_SECONDS)

class StockSentimentStrategy:
    def __init__(self, trade_manager):
        self.trade_manager = trade_manager
        self.config = config
        self.target_accounts = ['trc'] # Start conservative, maybe add 'tra' later
        self.strategy_tag = "SENT_STRAT"
        self.base_target_gain = 3.0
        self.stop_loss_pct = -0.3 # Stocks need slightly wider stops than crypto scalps
        self.ratio_sensitivity = 0.015 
        
        # History for velocity calc (Symbol -> Deque)
        self.history = defaultdict(lambda: deque(maxlen=30)) 
        
        self._lock = asyncio.Lock()
        
    async def run_loop(self):
        logger.info(f"🧠 [SENT_STRAT] Stock Strategy Started. Accounts: {self.target_accounts}")
        await asyncio.sleep(30) # Warmup
        
        while self.trade_manager.running:
            try:
                # Market Hours Check
                if not is_regular_trading_hours():
                    await asyncio.sleep(60)
                    continue

                async with self._lock:
                    # 1. Fetch Global Snapshot (Using Redis or File)
                    snapshot = await self.trade_manager._get_full_market_snapshot()
                    if not snapshot: 
                        await asyncio.sleep(5)
                        continue

                    # 2. Determine Regime (Global Score vs EMA)
                    # We pick a proxy symbol (e.g. SPY or AAPL) to find the global score stored in indicators
                    global_score = 0.0
                    global_ema = 0.0
                    if 'SPY' in snapshot:
                        global_score = safe_fetch_float(snapshot['SPY'].get('0market_sentiment_score', 0))
                        global_ema = safe_fetch_float(snapshot['SPY'].get('0market_sentiment_score_ema', 0))
                    
                    self._update_history(snapshot, global_score)

                    # Dynamic Ratio
                    diff = global_score - global_ema
                    target_long_ratio = 0.5 + (diff * self.ratio_sensitivity)
                    target_long_ratio = max(0.2, min(0.8, target_long_ratio)) # Clamp

                    for acc in self.target_accounts:
                        await self._manage_active_positions(acc, target_long_ratio)
                        await self._scan_for_entries(acc, target_long_ratio, snapshot, global_score)
                
                await asyncio.sleep(10)

            except Exception as e:
                logger.error(f"🧠 [SENT_STRAT] Loop Error: {e}",  exc_info=True )
                await asyncio.sleep(30)


    def _update_history(self, snapshot, global_score):
        now = time.time()
        for symbol, data in snapshot.items():
            if not isinstance(data, dict): continue
            local = safe_fetch_float(data.get('0market_sentiment_local', 0.0))
            self.history[symbol].append((now, local, global_score))

    def _get_5m_deltas(self, symbol):
        dq = self.history.get(symbol)
        if not dq or len(dq) < 2: return 0.0, 0.0
        
        cur_ts, cur_loc, cur_glob = dq[-1]
        target_ts = cur_ts - 180
        past = dq[0]
        
        for item in dq:
            if item[0] >= target_ts:
                past = item
                break
        if (cur_ts - past[0]) < 60: return 0.0, 0.0
        local_delta = cur_loc - past[1]
        div_delta = (cur_loc - cur_glob) - (past[1] - past[2])
        return local_delta, div_delta

    async def _manage_active_positions(self, account_key, target_long_ratio):
        positions = self.trade_manager.position_manager.get_positions_by_account(account_key)
        
        for pk, pos in positions.items():
            reasons = (str(getattr(pos, 'augment_reason', '')) + str(getattr(pos, 'reduction_reason', '')))
            if self.strategy_tag not in reasons: 
                continue # Skip positions not opened by this strat

            symbol = pos.symbol
            current_price,_ = await self.trade_manager.get_current_price(symbol)
            current_price = float(current_price)
            if current_price <= 0: continue

            amt = abs(float(pos.positionAmt))
            entry = float(pos.entry_price)
            is_long = pos.position_side == 'LONG'
            
            if amt == 0 or entry == 0: continue
            
            gain_pct = ((current_price - entry) / entry) * 100 if is_long else ((entry - current_price) / entry) * 100
            
            # 1. STOP LOSS
            if gain_pct < self.stop_loss_pct:
                reason = f"{self.strategy_tag}_STOP_LOSS"
                logger.warning(f"🧠 [SENT_STRAT][{account_key}] 🔪 CUT {symbol} at {gain_pct:.2f}%")
                await self.trade_manager.execute_trade_action(
                    account_key, pk, symbol, amt, current_price,
                    "SELL" if is_long else "BUY", pos.position_side,
                    f"sent_stop_{int(time.time())}", action="CLOSE", reason=reason, override_qty=amt
                )
                continue

            # 2. ADAPTIVE PROFIT TARGET
            dynamic_target = self.base_target_gain
            if is_long and target_long_ratio < 0.4: dynamic_target = 1.0
            elif not is_long and target_long_ratio > 0.6: dynamic_target = 1.0

            # BACKTEST_CHANGE_T51: NO-LOSS gate — only trim above min profit
            _noloss_min_sent = getattr(config, 'NOLOSS_MIN_PROFIT_PCT_TRADIER', 1.0)
            if gain_pct > max(dynamic_target, _noloss_min_sent):
                reduce_qty = max(1, int(amt * 0.25))
                action = 'REDUCE'
                
                # Close dust
                if (amt - reduce_qty) * current_price < 50.0:
                    action = 'CLOSE'
                    reduce_qty = amt

                reason = f"{self.strategy_tag}_TRIM_g{gain_pct:.1f}"
                logger.info(f"🧠 [SENT_STRAT][{account_key}] 💰 {action} {symbol} at {gain_pct:.2f}%")
                
                await self.trade_manager.execute_trade_action(
                    account_key, pk, symbol, reduce_qty, current_price,
                    "SELL" if is_long else "BUY", pos.position_side,
                    f"sent_trim_{int(time.time())}", action=action, reason=reason, override_qty=reduce_qty
                )

    async def _scan_for_entries(self, account_key, target_long_ratio, snapshot, global_score):
        max_pos = 15 # Conservative for stocks
        
        # Count Active
        current_longs = 0
        current_shorts = 0
        positions = self.trade_manager.position_manager.get_positions_by_account(account_key)
        for p in positions.values():
            if abs(float(p.positionAmt)) > 0:
                if p.position_side == 'LONG': current_longs += 1
                else: current_shorts += 1

        # Capacity Logic
        max_long = int(max_pos * target_long_ratio)
        max_short = max_pos - max_long
        
        allow_long = current_longs < max_long
        allow_short = current_shorts < max_short
        
        if not allow_long and not allow_short: return

        # 1. Build restricted candidate set (Waste no resources)
        longs = [s.upper() for s in getattr(self.trade_manager, f"symbols_long_{account_key}", [])]
        shorts = [s.upper() for s in getattr(self.trade_manager, f"symbols_short_{account_key}", [])]
        whitelist = [s.upper() for s in getattr(config, 'ALWAYS_TRADEABLE', [])]
        exceptions = [s.upper() for s in getattr(config, 'EXCEPTIONS', [])]
        
        allowed_symbols = set(longs + shorts + whitelist + exceptions)
        
        candidates = []
        for symbol, data in snapshot.items():
            symbol = symbol.upper()
            if symbol not in allowed_symbols: continue
            if not isinstance(data, dict): continue
            
            # --- TRADEABILITY CHECK ---
            is_long_allowed = self.trade_manager.is_symbol_tradeable(symbol, account_key, 'LONG')
            is_short_allowed = self.trade_manager.is_symbol_tradeable(symbol, account_key, 'SHORT')
            
            if not is_long_allowed and not is_short_allowed:
                continue

            # Skip if ANY side is open (Proper Quantity Check)
            pk_long = f"{account_key}:{symbol}_LONG"
            pk_short = f"{account_key}:{symbol}_SHORT"
            
            pos_long = positions.get(pk_long)
            pos_short = positions.get(pk_short)
            
            has_long = pos_long and abs(float(getattr(pos_long, 'positionAmt', 0))) > 0.00001
            has_short = pos_short and abs(float(getattr(pos_short, 'positionAmt', 0))) > 0.00001
            
            if has_long or has_short: continue

            # Metrics
            local = safe_fetch_float(data.get('0market_sentiment_local', 0))
            divergence = local - global_score
            local_delta, div_delta = self._get_5m_deltas(symbol)
            price = safe_fetch_float(data.get('current_price', 0))
            if price <= 0: continue

            # Entry Logic (Adjusted for Stocks - Slightly slower pace)
            # LONG
            if allow_long and is_long_allowed and divergence > 8 and div_delta > 3:
                k15 = safe_fetch_float(data.get('stoch_k_15m', 50))
                if k15 < 85:
                    candidates.append({
                        'symbol': symbol, 'side': 'LONG',
                        'score': divergence + div_delta, 'price': price,
                        'reason': f"DIV:{divergence:.1f}_VEL:{div_delta:.1f}"
                    })

            # SHORT (Check non-shortable)
            elif allow_short and is_short_allowed and divergence < -8 and div_delta < -3:
                if symbol in self.trade_manager.non_shortable_symbols: continue
                k15 = safe_fetch_float(data.get('stoch_k_15m', 50))
                if k15 > 15:
                    candidates.append({
                        'symbol': symbol, 'side': 'SHORT',
                        'score': abs(divergence + div_delta), 'price': price,
                        'reason': f"DIV:{divergence:.1f}_VEL:{div_delta:.1f}"
                    })

        # Execute Top 1
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        for cand in candidates[:1]: # One per loop to avoid spam
            symbol = cand['symbol']
            side = cand['side']
            price = cand['price']
            
            # Size Calc
            base_qty = (config.START_POSITION_SIZE / price)
            qty = max(1, int(base_qty))
            
            pk = f"{account_key}:{symbol}_{side}"
            logger.info(f"🧠 [SENT_STRAT] Opening {pk} (Score: {cand['score']:.1f})")
            
            await self.trade_manager.execute_trade_action(
                account_key, pk, symbol, qty, price,
                "BUY" if side == "LONG" else "SELL", side,
                f"sent_entry_{int(time.time())}", action="OPEN", 
                reason=f"{self.strategy_tag}_{cand['reason']}", override_qty=qty
            )
            return # Only one per scan loop
            
async def monitor_direct_high_gain_positions(trade_manager, order_queue):
    while getattr(trade_manager, "running", False):
        try:
            await asyncio.sleep(75)
            positions = trade_manager.position_manager.get_positions_by_account('tra')
            # Find high gain positions (> 5%)
            high_gain_positions = []
            for position_key, position in positions.items():
                try:
                    position_amt = abs(float(getattr(position, 'positionAmt', 0)))
                    if position_amt <= 0:
                        continue
                    
                    current_price = getattr(position, 'mark_price', 0) or 0
                    if current_price <= 0: current_price,ts=await trade_manager.get_current_price(position.symbol)
                    
                    entry_price = getattr(position, 'entry_price', 0) or current_price
                    if entry_price <= 0:
                        continue
                    
                    position_side = getattr(position, 'position_side', 'LONG')
                    gain = calculate_gain(position_side, current_price, entry_price)
                    
                    if gain > 5.0:  # 5% gain threshold
                        high_gain_positions.append(position_key)
                        
                except Exception:
                    continue
            
            # Process high gain positions
            if high_gain_positions:
                logger.info(f"[HIGH_GAIN_MONITOR] Found {len(high_gain_positions)} positions with >5% gain")
                
                for position_key in high_gain_positions[:25]:  # Limit to 25 at a time
                    try:
                        await direct_high_gain_augmentation(position_key, order_queue, trade_manager)
                    except Exception as e:
                        logger.debug(f"[ ] Error processing {position_key}: {e}")
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[monitor_dir ect_high_gain_positions] {e}")
            await asyncio.sleep(10)


async def periodic_direct_high_gain_reopen(trade_manager, order_queue, account_key):
    """Periodically check for re-opening high gain positions that were reduced"""
    current_account.set(account_key)
    while getattr(trade_manager, "running", False):
        try:
            current_account.set(account_key)
            await asyncio.sleep(150)
            await monitor_entries(
                order_queue, trade_manager, account_key, None, 
                event_type="reopen_high_gain", is_priority_add=False, force=False
            )
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[per iodic_direct_high_gain_reopen] {e}")
            await asyncio.sleep(10)


async def direct_high_gain_augmentation(position_key: str, order_queue: OrderQueue, trade_manager: TradierTradeManager):
    """Direct high gain augmentation for Tradier - checks if profitable position should be augmented"""
    try:
        account_key, symbol, position_side = parse_position_key(position_key)
        current_account.set(account_key)
        
        # Check if symbol is shortable before trying to augment short
        if position_side == "SHORT" and symbol.upper() in trade_manager.non_shortable_symbols:
            logger.debug(f"[HIGH_GAIN_AUGMENT] {symbol} BLOCKED: Symbol not available for short sales on Tradier")
            return
        
        # Get position
        position =  trade_manager.position_manager.get_position(position_key)
        if not position:
            logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: Position not found")
            return
        
        # Check if position has positionAmt
        position_amt = abs(float(getattr(position, 'positionAmt', 0)))
        if position_amt <= 0:
            logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: No position positionAmt")
            return
        
        # Get current price
        current_price = getattr(position, 'mark_price', 0) or 0
        if current_price <= 0:
            current_price, _ = await trade_manager.get_current_price(symbol)
            current_price = float(current_price)
            if current_price <= 0:
                logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: No valid price")
                return
        
        # Calculate gain
        entry_price = getattr(position, 'entry_price', 0) or current_price
        if entry_price <= 0:
            logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: No entry price")
            return
        
        gain = calculate_gain(position_side, current_price, entry_price)
        
        # Only augment if gain is positive and significant
        if gain < 0.5:  # At least 0.5% gain
            logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: Gain too low ({gain:.2f}%)")
            return
        
        # Check cooldown
        now = datetime.now(timezone.utc)
        last_aug = getattr(position, 'last_augmentation_time', None)
        if last_aug:
            try:
                if isinstance(last_aug, str):
                    last_aug = isoparse(last_aug)
                if (now - last_aug).total_seconds() < 300:  # 5 minute cooldown
                    logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: Augmentation cooldown active")
                    return
            except Exception:
                pass
        indicators = trade_manager.get_indicators(symbol)
        if not indicators:
            logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: No indicators")
            return
        
        # Parse market data
        i = trade_manager.strategy.parse_market_data(indicators)
        
        # Check if conditions are favorable for augmentation
        is_long = position_side == "LONG"
        if is_long:
            # For LONG: RSI not overbought, Stoch crossing up, Heikin Ashi green
            rsi_ok = i.get('rsi_5m', 50) < 75
            stoch_ok = i.get('stoch_k_5m', 50) > i.get('stoch_d_5m', 50)
            ha_ok = i.get('ha_5m', 'neutral') == 'green'
            volume_ok = i.get('rel_vol_5m', 1.0) > 1.2
            
            if not (rsi_ok and stoch_ok and ha_ok and volume_ok):
                logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: Conditions not met for LONG augment")
                return
        else:
            # For SHORT: RSI not oversold, Stoch crossing down, Heikin Ashi red
            rsi_ok = i.get('rsi_5m', 50) > 25
            stoch_ok = i.get('stoch_k_5m', 50) < i.get('stoch_d_5m', 50)
            ha_ok = i.get('ha_5m', 'neutral') == 'red'
            volume_ok = i.get('rel_vol_5m', 1.0) > 1.2
            
            if not (rsi_ok and stoch_ok and ha_ok and volume_ok):
                logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: Conditions not met for SHORT augment")
                return
        base_qty = (config.START_POSITION_SIZE * 0.5) / current_price
        final_qty = await trade_manager.strategy.calculate_quantity_complex(
            symbol, "AUGMENT", position_side, base_qty, indicators, position )
        final_qty = max(1, int(round(final_qty))) 
        if final_qty <= 0:
            logger.debug(f"[HIGH_GAIN_AUGMENT] {position_key}: Calculated quantity <= 0")
            return
        reason = f"High_Gain_Augment_{gain:.1f}%_Vol_{i.get('rel_vol_5m', 1.0):.1f}x"
        await queue_trade_action( order_queue, trade_manager, position_key,   "AUGMENT", reason, 80.0, override_qty=final_qty  )
        logger.info(f"[HIGH_GAIN_AUGMENT] {position_key}: Queued augmentation, gain={gain:.2f}%, qty={final_qty}")
        
    except Exception as e:
        logger.error(f"[HIGH_GAIN_AUGMENT] Error: {e}",  exc_info=True )


class StockScalpStrategy:
    """
    Scalp wing: fast in/out trades on the biggest movers of the moment.
    Runs on trb alongside the swing wing. Budget: $10k long / $10k short.
    Entry: top movers by rel_vol × 5m-deviation, triggered by stoch hook on 5m.
    Exit: 1.5% stop, 2% target, or 45-min timeout.
    """
    def __init__(self, trade_manager):
        self.trade_manager = trade_manager
        self.account_key = "trb"
        self.movers_cache: list = []
        self._lock = asyncio.Lock()

    async def run_loop(self):
        logger.info("⚡ [SCALP] Scalp wing started")
        await asyncio.sleep(15)
        while self.trade_manager.running:
            try:
                if not is_regular_trading_hours():
                    await asyncio.sleep(30)
                    continue
                async with self._lock:
                    snapshot = self.trade_manager._get_full_market_snapshot()
                    if not snapshot:
                        await asyncio.sleep(5)
                        continue
                    self._refresh_movers(snapshot)
                    await self._manage_scalp_positions()
                    await self._scan_entries(snapshot)
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"⚡ [SCALP] Loop Error: {e}", exc_info=True)
                await asyncio.sleep(15)

    def _refresh_movers(self, snapshot: dict):
        """Rank symbols by rel_vol × |deviation from 5m EMA|. Cache top N."""
        n = getattr(config, 'SCALP_TOP_MOVERS_N', 14)
        min_rv = getattr(config, 'SCALP_MIN_REL_VOL', 1.3)
        min_move = getattr(config, 'SCALP_MIN_MOVE_PCT', 0.003)
        scored = []
        for sym, data in snapshot.items():
            if not isinstance(data, dict): continue
            rv = safe_fetch_float(data.get('relative_volume_5m', 0))
            price = safe_fetch_float(data.get('current_price', 0))
            ema5 = safe_fetch_float(data.get('ema_20_5m', price))
            if price <= 0 or ema5 <= 0 or rv < min_rv: continue
            move_pct = abs(price - ema5) / ema5
            if move_pct < min_move: continue
            score = rv * move_pct * 100
            scored.append({'symbol': sym.upper(), 'score': score, 'price': price, 'data': data})
        scored.sort(key=lambda x: x['score'], reverse=True)
        self.movers_cache = scored[:n]

    async def _manage_scalp_positions(self):
        """Exit scalp positions on stop, profit target, or timeout."""
        acc = self.account_key
        stop_pct = getattr(config, 'SCALP_STOP_PCT', 0.015)
        target_pct = getattr(config, 'SCALP_TARGET_PCT', 0.02)
        max_hold = getattr(config, 'SCALP_MAX_HOLD_MINUTES', 45.0)
        positions = self.trade_manager.position_manager.get_positions_by_account(acc)
        for pk, pos in list(positions.items()):
            if getattr(pos, 'trade_wing', 'swing') != 'scalp': continue
            qty = abs(float(getattr(pos, 'positionAmt', 0)))
            if qty <= 0: continue
            entry = float(getattr(pos, 'entry_price', 0))
            if entry <= 0: continue
            is_long = pos.position_side == 'LONG'
            price, _ = await self.trade_manager.get_current_price(pos.symbol)
            price = float(price or 0)
            if price <= 0: continue
            gain_pct = ((price - entry) / entry) if is_long else ((entry - price) / entry)
            opened_at = getattr(pos, 'opened_at', None)
            age_min = 0.0
            if opened_at:
                ots = opened_at.timestamp() if hasattr(opened_at, 'timestamp') else float(opened_at)
                age_min = (time.time() - ots) / 60.0
            should_exit, exit_reason = False, ""
            # BACKTEST_CHANGE_T51: NO-LOSS gate for scalps — only exit if profitable above min
            _noloss_min_s = getattr(config, 'NOLOSS_MIN_PROFIT_PCT_TRADIER', 1.0) / 100.0
            if gain_pct <= -stop_pct and gain_pct < -0.015:
                should_exit = True; exit_reason = f"SCALP_STOP {gain_pct:.2%}"
            elif gain_pct >= max(target_pct, _noloss_min_s):
                should_exit = True; exit_reason = f"SCALP_TARGET {gain_pct:.2%}"
            elif age_min >= max_hold and gain_pct >= _noloss_min_s:
                should_exit = True; exit_reason = f"SCALP_TIMEOUT_PROFIT {age_min:.0f}m {gain_pct:.2%}"
            if should_exit:
                logger.info(f"⚡ [SCALP] EXIT {pos.symbol} ({pos.position_side}): {exit_reason}")
                await self.trade_manager.execute_trade_action(acc, pk, pos.symbol, qty, price, "SELL" if is_long else "BUY", pos.position_side, f"scalp_exit_{int(time.time())}", action="CLOSE", reason=exit_reason, override_qty=qty)

    async def _scan_entries(self, snapshot: dict):
        """Enter on top movers with stoch hook trigger. One entry per loop."""
        acc = self.account_key
        long_budget = getattr(config, 'SCALP_LONG_BUDGET', 10000.0)
        short_budget = getattr(config, 'SCALP_SHORT_BUDGET', 10000.0)
        max_per_side = getattr(config, 'SCALP_MAX_POSITIONS_PER_SIDE', 8)
        long_exp = self.trade_manager.get_wing_exposure('scalp', 'LONG')
        short_exp = self.trade_manager.get_wing_exposure('scalp', 'SHORT')
        positions = self.trade_manager.position_manager.get_positions_by_account(acc)
        n_long = sum(1 for p in positions.values() if getattr(p, 'trade_wing', 'swing') == 'scalp' and p.position_side == 'LONG' and abs(float(getattr(p, 'positionAmt', 0))) > 0)
        n_short = sum(1 for p in positions.values() if getattr(p, 'trade_wing', 'swing') == 'scalp' and p.position_side == 'SHORT' and abs(float(getattr(p, 'positionAmt', 0))) > 0)
        allow_long = long_exp < long_budget and n_long < max_per_side
        allow_short = short_exp < short_budget and n_short < max_per_side
        if not allow_long and not allow_short: return
        for mover in self.movers_cache:
            symbol = mover['symbol']
            price = mover['price']
            data = mover['data']
            if symbol in self.trade_manager.blacklist: continue
            if not self.trade_manager.is_symbol_tradeable(symbol, acc, 'LONG') and not self.trade_manager.is_symbol_tradeable(symbol, acc, 'SHORT'): continue
            pk_long = f"{acc}:{symbol}_LONG"
            pk_short = f"{acc}:{symbol}_SHORT"
            pos_l = positions.get(pk_long)
            pos_s = positions.get(pk_short)
            has_any = (pos_l and abs(float(getattr(pos_l, 'positionAmt', 0))) > 0) or (pos_s and abs(float(getattr(pos_s, 'positionAmt', 0))) > 0)
            if has_any: continue
            k5 = safe_fetch_float(data.get('stoch_k_5m', 50))
            k5_prev = safe_fetch_float(data.get('stoch_k_5m_prev', k5))
            d5 = safe_fetch_float(data.get('stoch_d_5m', 50))
            k1 = safe_fetch_float(data.get('stoch_k_1m', 50))
            # Check pullback-reexpansion setup for heavy artillery sizing
            ha_setup, ha_score, ha_dir, ha_detail = self.trade_manager.check_pullback_reexpansion(symbol, data)
            if allow_long and k5 < 25 and k5 > k5_prev and k5 > d5 and k1 < 40:
                heavy = ha_setup and ha_dir == 'LONG'
                logger.info(f"⚡ [SCALP] LONG {'🔫HEAVY ' if heavy else ''}signal {symbol}: k5={k5:.0f} score={mover['score']:.2f}{' '+ha_detail if heavy else ''}")
                await self._open_scalp(acc, symbol, 'LONG', price, ha_score if heavy else 0)
                return
            if allow_short and k5 > 75 and k5 < k5_prev and k5 < d5 and k1 > 60:
                if symbol in self.trade_manager.non_shortable_symbols: continue
                heavy = ha_setup and ha_dir == 'SHORT'
                logger.info(f"⚡ [SCALP] SHORT {'🔫HEAVY ' if heavy else ''}signal {symbol}: k5={k5:.0f} score={mover['score']:.2f}{' '+ha_detail if heavy else ''}")
                await self._open_scalp(acc, symbol, 'SHORT', price, ha_score if heavy else 0)
                return

    async def _open_scalp(self, acc: str, symbol: str, side: str, price: float, ha_score: int = 0):
        """Open a scalp position and tag it as scalp wing. ha_score 3/4 = heavy artillery."""
        start_size = getattr(config, 'SCALP_START_SIZE', 800.0)
        max_pos = getattr(config, 'SCALP_MAX_POSITION_SIZE', 2000.0)
        if ha_score == 4: start_size = min(start_size * 3.0, max_pos)
        elif ha_score == 3: start_size = min(start_size * 2.0, max_pos)
        qty = max(1, int(start_size / max(price, 0.01)))
        pk = f"{acc}:{symbol}_{side}"
        api_side = "buy" if side == 'LONG' else "sell_short"
        logger.info(f"⚡ [SCALP] OPEN {side} {symbol} @ {price:.2f} × {qty} shares")
        await self.trade_manager.execute_trade_action(acc, pk, symbol, qty, price, api_side, side, f"scalp_{int(time.time())}", action="OPEN", reason=f"SCALP_{side}", override_qty=qty)
        await asyncio.sleep(3)
        pos = self.trade_manager.position_manager.get_position(pk)
        if pos:
            pos.trade_wing = "scalp"
            await self.trade_manager.position_manager.save_all_positions()


async def main():
    import argparse
    def signal_handler(signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        raise KeyboardInterrupt
        
    parser = argparse.ArgumentParser(description="Tradier Trade Manager")
    parser.add_argument(
        "--accounts", 
        nargs="+", 
        default=[ "trb", "trc"], 
        help="Space-separated list of accounts to manage (e.g. --accounts tra)"   )
    args = parser.parse_args()
    
    # Validate accounts against config
    valid_accounts = [acc for acc in args.accounts if acc in config.ACCOUNTS]
    if not valid_accounts:
        print(f"!! No valid accounts found in config. Looking for: {args.accounts}")
        return
    setup_logger_per_account("tradier_manage", config.LOG_DIR, valid_accounts)   
    logger.info(f"🚀 INSTANCE STARTED for: {valid_accounts}")
    logger.info(f"📁 Weather Logs -> Stdout (captured in tradier_manage_{valid_accounts[0]}.log)")
    logger.info(f"📁 General Logs -> tradier_manage_general_{valid_accounts[0]}.log")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    manager = TradierTradeManager(account_list=valid_accounts)
    sent_strat = StockSentimentStrategy(manager)
    
    try:
        asyncio.create_task(sent_strat.run_loop())
        await manager.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}",  exc_info=True )
    finally:
        await manager.stop()

if __name__ == "__main__":
    asyncio.run(main())
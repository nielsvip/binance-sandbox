import asyncio
import atexit
import contextvars
import hashlib
import json
import logging
import math
import os
import platform
import pwd
import random
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from logging import Logger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import (Any, Callable, Coroutine, Dict, List, Optional,
                    ParamSpecArgs, Set, Tuple, Union)

class RobustRotatingFileHandler(RotatingFileHandler):
    def doRollover(self):
        try:
            super().doRollover()
        except (OSError, PermissionError):
            if self.stream:
                self.stream.close()
                self.stream = None
            self.stream = self._open()

import aiofiles
import aiofiles.os as aio_os
import aiohttp
import numpy as np
import pandas as pd
import psutil
import redis.asyncio as redis
from dateutil.parser import isoparse
from dotenv import dotenv_values
from redis import exceptions as redis_exceptions

from config import Config as AppConfig

try:
    import orjson
except ImportError:
    orjson = None
try:
    original_on_connect = redis.connection.Connection.on_connect
    async def patched_on_connect(self):
        try:
            await original_on_connect(self)
        except redis_exceptions.ResponseError as e:
            if "SETINFO" in str(e):
                pass  # Ignore SETINFO errors for older Redis versions
            else:
                raise
        except (redis_exceptions.ConnectionError, ConnectionResetError, OSError) as e:
            # Handle connection errors gracefully - will retry on next operation
            import logging
            logger = logging.getLogger(__name__)
            logger.debug(f"Redis connection error during on_connect (will retry): {type(e).__name__}: {e}")
            raise  # Re-raise so connection pool can handle it
    redis.connection.Connection.on_connect = patched_on_connect
except Exception:
    pass
from contextlib import suppress
from fnmatch import fnmatch
from functools import wraps
from types import SimpleNamespace

from config import Config

config = Config()
ENV_LOADED = False
_env_cache = None
_hostname_cache = None
_env_lock = threading.Lock()
_env_semaphore_macbook = threading.Semaphore(1) if platform.system() == "Darwin" else None
FILE_READ_SEMAPHORE = asyncio.Semaphore(10 if platform.system() == "Darwin" else 240)  # MacBook: 10 (prevent "too many open files"), Server/Gateway: 240
current_account = contextvars.ContextVar('current_account', default='unknown')
def is_hedge_account(config_obj, account_key: str) -> bool:
    """Checks if hedging is enabled for this specific account."""
    if not getattr(config_obj, 'HEDGE_MODE', False): return False
    hedge_accounts = getattr(config_obj, 'HEDGE_ACCOUNTS', [])
    if isinstance(hedge_accounts, tuple): hedge_accounts = list(hedge_accounts)
    return account_key in hedge_accounts

def is_strict_no_loss_account(config_obj, account_key: str) -> bool:
    """Checks if this account is strictly forbidden from selling at a loss."""
    strict_accounts = getattr(config_obj, 'STRICT_NO_LOSS_ACCOUNTS', [])
    return account_key in strict_accounts

def is_sandbox_account(config_obj, account_key: str) -> bool:
    """Checks if this is a sandbox/paper-trading account (no real API calls)."""
    return getattr(config_obj, 'SANDBOX_MODE', False) and account_key in getattr(config_obj, 'SANDBOX_ACCOUNTS', [])

def orjson_default(obj):
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.floating, float)):
        return float(obj) if not np.isnan(obj) else None
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Type {type(obj)} not serializable")
def default_serializer(obj):
    if isinstance(obj, (np.bool_, np.bool8)):
        return bool(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj) if not np.isnan(obj) else None
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (datetime, pd.Timestamp)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")
    
async def record_decision_context_crypto(redis_manager, account_key, position_key, action, reason, indicators, extra_data=None):
    try:
        timestamp = datetime.now(timezone.utc)
        snapshot = {
            "price": indicators.get('current_price') or indicators.get('price'),
            "k_1m": indicators.get('stoch_k_1m'), "d_1m": indicators.get('stoch_d_1m'),
            "k_3m": indicators.get('stoch_k_3m'), "d_3m": indicators.get('stoch_d_3m'),
            "k_15m": indicators.get('stoch_k_15m'), "d_15m": indicators.get('stoch_d_15m'),
            "ha_3m": indicators.get('ha_3m'), "ha_15m": indicators.get('ha_15m'),
            "wt1_15m": indicators.get('wt1_15m'), "wt2_15m": indicators.get('wt2_15m'),
            "sentiment": indicators.get('0market_sentiment_score'),
            "local_sent": indicators.get('0market_sentiment_local')
        }

        context_payload = {
            "timestamp": timestamp.isoformat(),
            "position_key": position_key,
            "account": account_key,
            "action": action,
            "reason": reason,
            "snapshot": snapshot,
            "extra": extra_data or {}
        }

        # 2. Save to Redis (Key: decision:{position_key})
        # TTL: 300 seconds (5 minutes) - plenty of time for order to fill
        if redis_manager:
            redis_key = f"decision:{position_key}"
            if hasattr(redis_manager, 'set'):
                await redis_manager.set(redis_key, json.dumps(context_payload, default=str), ex=300)
            elif hasattr(redis_manager, 'connections'):
                client = redis_manager.connections.get('local') or redis_manager.connections.get('server')
                if client:
                    await client.set(redis_key, json.dumps(context_payload, default=str), ex=300)
        log_dir = Path("data/decisions")
        log_dir.mkdir(parents=True, exist_ok=True)
        day_str = timestamp.strftime('%Y%m%d')
        async with aiofiles.open(log_dir / f"decisions_{account_key}_{day_str}.jsonl", "a") as f:
            await f.write(json.dumps(context_payload, default=str) + "\n")
    except Exception as e:
        print(f"[DECISION_RECORD_FAIL] {e}")

async def record_decision_context(redis_manager, account_key, position_key, action, reason, indicators, score, market_context, trade_details=None):
    try:
        timestamp = datetime.now(timezone.utc)
        context_data = {
            "timestamp": timestamp.isoformat(),
            "position_key": position_key,
            "account_key": account_key,
            "action": action,
            "reason_text": reason,
            "tech_score": score,
            "market_context": market_context,
            "indicators": {
                "current_price": indicators.get('current_price'),
                "stoch_k_5m": indicators.get('stoch_k_5m'),
                "stoch_d_5m": indicators.get('stoch_d_5m'),
                "stoch_k_15m": indicators.get('stoch_k_15m'),
                "stoch_d_15m": indicators.get('stoch_d_15m'),
                "stoch_k_1h": indicators.get('stoch_k_1h'),
                "stoch_d_1h": indicators.get('stoch_d_1h'),
                "rsi_5m": indicators.get('rsi_5m'),
                "rsi_15m": indicators.get('rsi_15m'),
                "ha_5m": indicators.get('ha_5m'),
                "sentiment_rank": indicators.get('0sentiment_rank'),
                "sentiment_score": indicators.get('0market_sentiment_score'),
                "volatility_atr": indicators.get('atr_15m'),
                "relative_volume": indicators.get('rel_vol_5m'),
                # Add full indicators if you want EVERYTHING:
                # "full_raw": indicators 
            }
        }

        if trade_details:
            context_data["trade"] = trade_details
        # 2. Save to Redis (TTL 5 minutes - enough time for order to fill and position manager to pick it up)
        # We use a specific key pattern that tradier_positions will look for
        if redis_manager:
            redis_key = f"tradier:decision:{position_key}"
            await redis_manager.set(redis_key, json.dumps(context_data, default=str), ex=300)
        # 3. Append to Daily Decision Log File (JSON Lines)
        log_dir = Path("data/decisions")
        log_dir.mkdir(parents=True, exist_ok=True)
        filename = log_dir / f"decisions_{account_key}_{timestamp.strftime('%Y%m%d')}.jsonl"
        async with aiofiles.open(filename, "a") as f:
            await f.write(json.dumps(context_data, default=str) + "\n")

        return True
    except Exception as e:
        print(f"[DECISION_RECORD_ERROR] {e}")
        return False
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
            # Heal broken %06d literal from previous bug
            if "%06d" in candidate:
                candidate = candidate.replace("%06d", "000000")
            numeric = None
            try:
                # Fast check: if it contains '-', it's likely ISO date, don't try float()
                if '-' not in candidate:
                    numeric = float(candidate[:-1] if candidate.endswith(('Z', 'z')) else candidate)
            except (TypeError, ValueError):
                numeric = None
            if numeric is not None and not math.isnan(numeric) and not math.isinf(numeric):
                try:
                    return datetime.fromtimestamp(numeric / (1000.0 if numeric > 1e12 else 1.0), tz=timezone.utc)
                except (OSError, OverflowError, ValueError):
                    numeric = None
            # FAST PATH (2026-05-09): try datetime.fromisoformat before pandas.
            # Same change applied in ez_manage.safe_datetime — see comments there.
            try:
                _iso = candidate
                if _iso.endswith('Z') or _iso.endswith('z'):
                    _iso = _iso[:-1] + '+00:00'
                dt = datetime.fromisoformat(_iso)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
            try:
                dt = pd.to_datetime(candidate, utc=True).to_pydatetime()
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.debug(f"[safe_datetime] Failed to parse timestamp string: {candidate} ({e})")
    if ts is not None:
        logger.debug(f"[safe_datetime] Unexpected timestamp type: {type(ts)} -> {ts}")
    return fallback

# def safe_datetime(ts, fallback: Optional[datetime] = None) -> Optional[datetime]:
#     if isinstance(ts, datetime):
#         return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
#     if isinstance(ts, (int, float)):
#         try:
#             return datetime.fromtimestamp(float(ts), tz=timezone.utc)
#         except (OSError, OverflowError, ValueError):
#             pass
#     if isinstance(ts, str):
#         candidate = ts.strip()
#         if candidate:
#             numeric = None
#             try:
#                 numeric = float(candidate[:-1] if candidate.endswith(('Z', 'z')) else candidate)
#             except (TypeError, ValueError):
#                 numeric = None
#             if numeric is not None and not math.isnan(numeric) and not math.isinf(numeric):
#                 try:
#                     return datetime.fromtimestamp(numeric / (1000.0 if numeric > 1e12 else 1.0), tz=timezone.utc)
#                 except (OSError, OverflowError, ValueError):
#                     numeric = None
#             try:
#                 dt = pd.to_datetime(candidate, utc=True).to_pydatetime()
#                 return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
#             except Exception as e:
#                 logger.debug(f"[safe_datetime] Failed to parse timestamp string: {candidate} ({e})")
#     if ts is not None:
#         logger.debug(f"[safe_datetime] Unexpected timestamp type: {type(ts)} -> {ts}")
#     return fallback
# def safe_datetime(ts, fallback: Optional[datetime] = None) -> Optional[datetime]:
#     """THE UNIVERSAL PARSER: Handles 'Z' strings, Unix ms, Unix seconds, and datetimes."""
#     if ts is None: return fallback
    
#     # 1. Already a datetime? Ensure UTC.
#     if isinstance(ts, datetime):
#         return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    
#     # 2. String Parsing
#     if isinstance(ts, str):
#         ts = ts.strip()
#         if not ts: return fallback
#         # A. Detect numeric strings (seconds or ms)
#         try:
#             val = float(ts[:-1] if ts.endswith(('Z', 'z')) else ts)
#             # If it parses as float, it might be a Unix timestamp
#             # But wait - if it was "2026..." it won't have 13 digits for ms logic usually
#             # Standard Unix seconds is 10 digits (1.7e9), ms is 13 digits (1.7e12)
#             if val > 1e11: val /= 1000.0 # Treat > 100 Billion as ms
#             return datetime.fromtimestamp(val, tz=timezone.utc)
#         except (ValueError, TypeError):
#             # B. Not numeric? Try ISO parsing
#             try:
#                 # Handle 'Z' manually if isoparse fails or for speed
#                 clean_ts = ts.replace('Z', '+00:00').replace('z', '+00:00')
#                 dt = datetime.fromisoformat(clean_ts)
#                 return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
#             except:
#                 try:
#                     # Final fallback to pandas for exotic formats
#                     dt = pd.to_datetime(ts, utc=True).to_pydatetime()
#                     return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
#                 except:
#                     return fallback

#     # 3. Numeric Parsing (int/float)
#     if isinstance(ts, (int, float)):
#         try:
#             val = float(ts)
#             if val > 1e11: val /= 1000.0
#             return datetime.fromtimestamp(val, tz=timezone.utc)
#         except: return fallback

#     return fallback

# def safe_isoformat(dt: Any) -> str:
#     """Safely converts any timestamp-like object to a UTC ISO string with 'Z'."""
#     obj = safe_datetime(dt)
#     if not obj: return ""
#     return obj.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z' 

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

def to_ts(val: Any) -> float:
    """Universal converter to Unix float timestamp."""
    if val is None: return 0.0
    if isinstance(val, (int, float)): return float(val / 1000.0 if val > 1e12 else val)
    if isinstance(val, (datetime, pd.Timestamp)): return val.timestamp()
    if isinstance(val, str):
        v = val.strip()
        if not v: return 0.0
        try:
            if 'T' in v or '-' in v: return isoparse(v.replace('Z', '+00:00')).timestamp()
            f = float(v)
            return f / 1000.0 if f > 1e12 else f
        except Exception:
            try: return pd.to_datetime(v, utc=True).timestamp()
            except Exception: return 0.0
    return 0.0

def to_iso(ts: Union[float, datetime, pd.Timestamp]) -> str:
    """Standardized ISO format: YYYY-MM-DDTHH:MM:SS.sssZ"""
    if ts is None: return ""
    if isinstance(ts, (datetime, pd.Timestamp)): return ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    try: return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    except Exception: return ""

def protect_symbols_json():
    """
    CRITICAL: Make symbols.json read-only to prevent accidental writes by scripts.
    Call this at startup to ensure symbols.json can NEVER be modified by running scripts.
    """
    try:
        symbols_path = Path(__file__).parent / "symbols.json"
        if not symbols_path.exists():
            print("⚠️ [SECURITY] symbols.json not found - cannot apply protection")
            return False
        
        # Make file read-only for owner, group, and others
        import stat
        current_mode = symbols_path.stat().st_mode
        read_only_mode = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH  # r--r--r--
        
        if current_mode != read_only_mode:
            symbols_path.chmod(read_only_mode)
            print(f"✅ [SECURITY] symbols.json is now READ-ONLY (protected from script writes)")
        else:
            print(f"✅ [SECURITY] symbols.json is already READ-ONLY")
        
        return True
    except Exception as e:
        print(f"❌ [SECURITY] Error protecting symbols.json: {e}")
        return False


class AccountFilter(logging.Filter):
    """Filter to add account context to log records."""
    def filter(self, record):
        record.account = current_account.get('unknown')
        return True

class ColoredFormatter(logging.Formatter):
    """Formatter that adds colors to log levels."""

    COLORS = {
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[0m',      # Default
        'WARNING': '\033[93m',  # Yellow
        'ERROR': '\033[91m',    # Red
        'CRITICAL': '\033[95m'  # Magenta
    }
    RESET = '\033[0m'

    def format(self, record):
        # Add color to the message based on level
        color = self.COLORS.get(record.levelname, self.RESET)
        record.msg = f"{color}{record.msg}{self.RESET}"
        return super().format(record)

class RateLimitDuplicateFilter(logging.Filter):
    def __init__(self, window_seconds: float = 120.0):
        super().__init__()
        self.window = float(window_seconds)
        self._last_seen = {}
        self._lock = __import__("threading").Lock()
        self._now = __import__("time").time

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        now = self._now()
        with self._lock:
            last = self._last_seen.get(msg)
            if last is not None and (now - last) < self.window:
                return False
            self._last_seen[msg] = now
            if len(self._last_seen) > 5000:
                cutoff = now - self.window
                self._last_seen = {m: t for m, t in self._last_seen.items() if t >= cutoff}
        return True

def get_standardized_timestamp() -> str:
    """Returns timestamp in standardized format: YYYY-MM-DDTHH:MM:SS.sssZ"""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

def parse_standardized_timestamp(timestamp_str: str) -> datetime:
    """Parses standardized timestamp string to datetime (handles 'Z' format)"""
    from dateutil.parser import isoparse
    return isoparse(timestamp_str)

def setup_logger_with_rotation(logger_name: str, log_file_name: str, level=logging.INFO):
    """
    Sets up a robust logger that writes to both the console and a rotating file.
    """
    log_file = Path.home() / "logs" / log_file_name
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(logger_name)

    # Check if logger already has a RotatingFileHandler pointing to the same file
    if logger.hasHandlers():
        # Look for existing RotatingFileHandler with the same log file
        existing_file_handler = None
        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                # Check if this handler is pointing to our target log file
                if hasattr(handler, 'baseFilename') and handler.baseFilename == str(log_file):
                    existing_file_handler = handler
                    break

        # If we found a matching file handler, keep the existing setup
        if existing_file_handler:
            return logger

        # Otherwise, clear handlers to reconfigure
        logger.handlers.clear()

    logger.setLevel(level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Console Handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Add restart separator to log file BEFORE creating handler (ensures it's always written)
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"{'='*80}\n")
            #f.write(f"SCRIPT RESTART - {logger_name} - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
            # f.write(f"{'='*80}")
    except Exception:
        pass
    # Rotating File Handler (100MB per file, 5 backups) - ALWAYS APPEND MODE
    fh = RotatingFileHandler(log_file, maxBytes=1*1024*1024, backupCount=8, encoding='utf-8', mode='a')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger

def setup_logger(name: str, log_file: str, level=logging.INFO, account_key: str = None) -> Logger:
    """Set up a logger with file and console handlers."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Check if logger already has a RotatingFileHandler pointing to the same file
    if logger.hasHandlers():
        # Look for existing RotatingFileHandler with the same log file
        existing_file_handler = None
        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                # Check if this handler is pointing to our target log file
                if hasattr(handler, 'baseFilename') and handler.baseFilename == log_file:
                    existing_file_handler = handler
                    break

        # If we found a matching file handler, keep the existing setup
        if existing_file_handler:
            return logger

        # Otherwise, clear handlers to reconfigure
        logger.handlers.clear()

    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
    except Exception:
        # Fall back to ~/logs if path invalid
        log_file = os.path.join(str(Path.home() / "logs"), os.path.basename(log_file))
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
        except Exception:
            pass

    # File handler with rotation
    app_cfg = AppConfig()
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=getattr(app_cfg, "LOG_MAX_BYTES", 20*1024*1024),
        backupCount=getattr(app_cfg, "LOG_BACKUP_COUNT", 30),
        encoding='utf-8',
        mode='a'
    )
    file_handler.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    
    # Create account filter
    account_filter = AccountFilter()
    
    # Plain formatter for file (no ANSI codes)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(account)s] %(message)s',
        datefmt='%m-%d %H:%M:%S'
    )
    
    # Colored formatter for console (with ANSI codes)
    console_formatter = ColoredFormatter(
        '%(asctime)s - %(levelname)s - [%(account)s] %(message)s',
        datefmt='%m-%d %H:%M:%S'
    )
    
    # Apply different formatters
    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)
    file_handler.addFilter(account_filter)
    console_handler.addFilter(account_filter)
    
    # Add restart separator to log file BEFORE adding handlers (ensures it's always written)
    # Skip restart separator for action logs to keep them clean and uninterrupted
    if name not in ('actions', 'tradier_actions'):
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                #f.write(f"\n{'='*80}\n")
                f.write(f"{'='*30}SCRIPT RESTART - {name} - {datetime.now(timezone.utc).strftime('%m-%d %H:%M:%S UTC')}{'='*30}\n")
                #f.write(f"{'='*80}\n")
        except Exception:
            pass
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

# Create default loggers (portable, no UID lookup - PROHIBITED)
# 2026-08-10 FIX: sandbox denies getpwuid via /var/db/dslocal -> fallback to /tmp without pwd lookup
try:
    _default_log_dir = os.environ.get('EZ_LOG_DIR') or os.environ.get("EZ_LOG_DIR") or "/tmp"
    import pathlib as _pl
    _probe = _pl.Path(_default_log_dir)
    try:
        _probe.mkdir(parents=True, exist_ok=True)
        _test = _probe / ".uid_probe"
        _test.touch(exist_ok=True)
        _test.unlink(missing_ok=True)
    except Exception:
        _default_log_dir = os.environ.get('EZ_LOG_DIR') or "/tmp"
except Exception:
    _default_log_dir = "/tmp"
logger = setup_logger('default', os.path.join(_default_log_dir, 'utils.log'))
action_logger = setup_logger('actions', os.path.join(_default_log_dir, 'actions.log'))
action_logger.addFilter(RateLimitDuplicateFilter(window_seconds=float(getattr(AppConfig(), 'LOG_DUPLICATE_WINDOW_SECONDS', 120))))
tradier_action_logger = setup_logger('tradier_actions', os.path.join(_default_log_dir, 'tradier_actions.log'))
tradier_action_logger.addFilter(RateLimitDuplicateFilter(window_seconds=float(getattr(AppConfig(), 'LOG_DUPLICATE_WINDOW_SECONDS', 120))))

def setup_ez_script_logger(script_name: str, level=logging.INFO) -> Logger:
    log_file = os.path.join(_default_log_dir, f'{script_name}.log')
    logger = logging.getLogger(script_name)
    logger.setLevel(level)

    # Check if logger already has a RotatingFileHandler pointing to the same file
    if logger.hasHandlers():
        # Look for existing RotatingFileHandler with the same log file
        existing_file_handler = None
        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                # Check if this handler is pointing to our target log file
                if hasattr(handler, 'baseFilename') and handler.baseFilename == log_file:
                    existing_file_handler = handler
                    break

        # If we found a matching file handler, keep the existing setup
        if existing_file_handler:
            return logger

        # Otherwise, clear handlers to reconfigure
        logger.handlers.clear()

    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
    except Exception:
        # Fall back to ~/logs if path invalid
        log_file = os.path.join(str(Path.home() / "logs"), f'{script_name}.log')
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
        except Exception:
            pass
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8',
        mode='a'
    )
    file_handler.setLevel(level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
        datefmt='%m-%d %H:%M:%S'
    )
    # Add restart separator to log file BEFORE adding handlers (ensures it's always written)
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"{'='*80}\n")
            #f.write(f"SCRIPT RESTART - {script_name} - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
           # f.write(f"{'='*80}\n")
    except Exception:
        pass
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

def load_environment_from_gpg(logger=None) -> bool:
    global ENV_LOADED
    if logger is None:
        from utils import logger
    if ENV_LOADED:
        logger.info("Environment variables already loaded. Skipping GPG decryption.")
        return True
    possible_env_paths = [".env.gpg", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.gpg")] # Script directory
        # os.path.join(os.environ.get("HOME") or "/tmp", "Documents", "binance", ".env.gpg"),  # Home Documents/binance
        # os.path.join(os.getcwd(), ".env.gpg"),  ]
    # Remove duplicates while preserving order
    seen = set()
    unique_paths = []
    for path in possible_env_paths:
        if path not in seen:
            seen.add(path)
            unique_paths.append(path)
    logger.info(f"Searching for .env.gpg in: {unique_paths}")
    for env_path in unique_paths:
        if os.path.exists(env_path):
            logger.info(f"Found .env.gpg at: {env_path}")
            try:
                # Try GPG decryption with absolute path
                decrypt_command = f"gpg --batch --yes --decrypt '{env_path}'"
                gpg_process = subprocess.run(decrypt_command, shell=True, capture_output=True, text=True, check=False)
                
                if gpg_process.returncode == 0:
                    decrypted_env_content = gpg_process.stdout
                    if decrypted_env_content and decrypted_env_content.strip():
                        logger.info("GPG decryption successful.")
                        decrypted_stream = StringIO(decrypted_env_content)
                        env_vars_from_gpg = dotenv_values(stream=decrypted_stream)
                        clean_env_vars = {
                            key: value for key, value in env_vars_from_gpg.items() 
                            if value is not None and value.strip() != ""
                        }
                        if clean_env_vars:
                            os.environ.update(clean_env_vars)
                            ENV_LOADED = True
                            logger.info(f"Loaded {len(clean_env_vars)} non-empty variables from {env_path}.")
                            return True
                        else:
                            logger.warning("GPG decrypted, but resulted in no non-empty variables.")
                    else:
                        logger.warning("GPG decryption command was successful but produced no content.")
                else:
                    logger.warning(f"GPG decryption failed for {env_path}. Stderr: {gpg_process.stderr.strip()}")
                    continue  # Try next path
                    
            except FileNotFoundError:
                logger.warning("GPG command not found. Ensure GPG is installed and in your PATH.")
                continue  # Try next path
            except Exception as e:
                logger.warning(f"An unexpected error occurred during GPG decryption of {env_path}: {e}")
                continue  # Try next path
        else:
            logger.debug(f".env.gpg not found at: {env_path}")
    logger.info("Attempting to load from plain .env file...")
    try:
        from dotenv import load_dotenv
        if load_dotenv():
            ENV_LOADED = True
            logger.info("Loaded variables from .env file.")
            return True
        else:
            logger.warning("No .env file found. Environment variables may be missing.")
            return False
    except Exception as e:
        logger.error(f"Failed to load from .env file: {e}")
        return False

def log_augment_action(position_key: str, position_value_str: str, augment_value_str: str, 
                      gain: float, reason: str, conviction=None):
    """Log augmentation actions."""
    conviction_str = f" | Conv: {conviction:.1f}" if conviction is not None else ""
    action_logger.info(
        f"AUGMENT: {position_key} | Position: ${position_value_str} | "
        f"Augment: ${augment_value_str} | Gain: {gain:.2f}%{conviction_str} | Reason: {reason}"
    )

def log_reduce_action(position_key: str, position_value_str: str, reduction_value_str: str,
                     gain: float, reason: str, conviction=None):
    """Log reduction actions."""
    conviction_str = f" | Conv: {conviction:.1f}" if conviction is not None else ""
    action_logger.info(
        f"REDUCE: {position_key} | Position: ${position_value_str} | "
        f"Reduction: ${reduction_value_str} | Gain: {gain:.2f}%{conviction_str} | Reason: {reason}"
    )

def parse_interval_to_timedelta(interval_str: str) -> timedelta:
    """Converts a Binance interval string (e.g., '3m', '4h', 'D') to a timedelta object."""
    try:
        interval_lower = interval_str.lower()
        unit = interval_lower[-1]
        value_str = interval_lower[:-1]
        value = int(value_str) if value_str else 1
        if unit == 'm':
            return timedelta(minutes=value)
        elif unit == 'h':
            return timedelta(hours=value)
        elif unit == 'd':
            return timedelta(days=value)
        else:
            raise ValueError(f"Unknown interval unit: {unit}")
    except (ValueError, TypeError, IndexError):
        print(f"ERROR: Could not parse interval string '{interval_str}'.")
        return timedelta(hours=1) # Fallback

async def atomic_write_json(file_path: Path, data: dict):
    """Atomically write JSON data to file"""
    tmp_file = file_path.with_suffix('.tmp')
    try:
        parent = file_path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(tmp_file, 'w') as f:
            await f.write(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
        os.replace(tmp_file, file_path)
        return True
    except Exception as e:
        try:
            if tmp_file.exists():
                tmp_file.unlink()
        except Exception:
            pass
        logger.error(f"Atomic write failed for {file_path}: {e}")
        raise


def get_current_environment():
    """Determine the current environment and return exact Redis connections needed."""
    global _env_cache
    if _env_cache is not None:
        return _env_cache
    with _env_lock:
        if _env_cache is not None:
            return _env_cache
        is_macbook = platform.system() == "Darwin"
        if is_macbook and _env_semaphore_macbook:
            _env_semaphore_macbook.acquire()
            try:
                _env_cache = _get_env_internal()
            finally:
                _env_semaphore_macbook.release()
        else:
            _env_cache = _get_env_internal()
        return _env_cache

def _get_env_internal():
    """Internal function to determine environment (called with semaphore on MacBook)."""
    global _hostname_cache
    import os
    import platform
    import socket
    env_override = os.environ.get("EZ_ENVIRONMENT", "").strip().lower()
    if _hostname_cache is None:
        _hostname_cache = socket.gethostname().lower()
    hostname = _hostname_cache
    ips = set()
    try: 
        ips.update(socket.gethostbyname_ex(hostname)[2])
    except Exception: 
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(("8.8.8.8", 80))
            ips.add(probe.getsockname()[0])
    except Exception: 
        pass
    docker_host = os.environ.get("HOSTNAME", "").lower()
    # MacBook detection: Must be Darwin (macOS)
    if env_override == "macbook" or (platform.system() == "Darwin" and ("mac" in hostname.lower() or env_override == "")):
        return {
            'env': 'macbook',
            'redis_connections': {
                'local': ('localhost', 6379),
                'gateway': ('localhost', 6380),
                'server': ('localhost', 6381)
            }
        }
    if env_override == "gateway" or any(tag in hostname for tag in ("157.90.168.35", "gateway")) or any(tag in docker_host for tag in ("157.90.168.35", "gateway")) or any(ip in ("157.90.168.35", "10.0.0.2") or ip.startswith("10.0.0.") for ip in ips):
        return {
            'env': 'gateway',
            'redis_connections': {
                'local': ('localhost', 6379),
                'server': ('10.0.0.3', 6379)
            }
        }
    if env_override == "server" or any(ip in ("157.180.125.52", "10.0.0.3") for ip in ips) or (docker_host and any(tag in docker_host for tag in ("157.180.125.52", "server"))) or hostname.startswith(("ubuntu", "server", "ip-")):
        return {
            'env': 'server',
            'redis_connections': {
                'local': ('localhost', 6379)
            }
        }
    return {
        'env': 'macbook',
        'redis_connections': {
            'local': ('localhost', 6379),
            'gateway': ('localhost', 6380),
            'server': ('localhost', 6381)
        }
    }

REDIS_KEYS = {
    "market_data": "latest_market_data",
    "price_cache": "price_cache", 
    "klines": "klines:{symbol}:{interval}",
    "signals": "trading_signals"
}

REDIS_CHANNELS = {
    "klines_updates": "klines_updates",
    "mark_prices": "mark_prices", 
    "market_data": "market_data_updates",
    "signals": "trading_signals",
    "signals_data": "signals_data",
    "indicators": "indicators",
    "latest_market_data": "latest_market_data",
    "position_updates": "position_updates",
    "direct_high_gain_augmented": "direct_high_gain_augmented"
}

class DataCoordinator:
    """Coordinates data freshness across machines to avoid duplicates."""
    
    def __init__(self, environment):
        self.environment = environment
        self.data_sources = {}
        self.source_priority = {
            "gateway": 3,
            "server": 2, 
            "macbook": 1
        }

    def should_process_data(self, data_type: str, symbol: str, source: str, timestamp: str, event_type: str = None) -> bool:
        key = f"{data_type}:{symbol}" if not event_type else f"{data_type}:{symbol}:{event_type}"

        if timestamp is None:
            logger.debug(f"Received None timestamp for {key} from {source}. Processing to be safe.")
            return True
            
        try:
            if isinstance(timestamp, str):
                if 'T' in timestamp:  # ISO format
                    data_time = isoparse(timestamp)
                else:
                    data_time = pd.to_datetime(timestamp, errors='coerce', utc=True)
            else:
                data_time = pd.to_datetime(timestamp, errors='coerce', utc=True)
                
            if pd.isna(data_time):
                raise ValueError(f"Timestamp parsed to NaT: {timestamp}")
        except Exception as e:
            logger.warning(f"Invalid timestamp '{timestamp}' for {key}. Processing as precaution. Error: {e}")
            return True
            
        current_best = self.data_sources.get(key)
        if current_best is None:
            self.data_sources[key] = {
                'source': source, 
                'timestamp': data_time, 
                'priority': self.source_priority.get(source, 1)
            }
            return True
            
        current_time = current_best.get('timestamp')
        current_priority = current_best.get('priority', 0)
        
        if current_time is None or pd.isna(current_time):
            self.data_sources[key] = {
                'source': source, 
                'timestamp': data_time, 
                'priority': self.source_priority.get(source, 1)
            }
            return True
            
        if data_time > current_time:
            self.data_sources[key] = {
                'source': source, 
                'timestamp': data_time, 
                'priority': self.source_priority.get(source, 1)
            }
            return True
            
        if data_time == current_time and self.source_priority.get(source, 1) > current_priority:
            self.data_sources[key] = {
                'source': source, 
                'timestamp': data_time, 
                'priority': self.source_priority.get(source, 1) }
            return True
            
        return False


def calculate_1m_stoch_rsi(mark_price_dict: Dict[str, List[Tuple[float, float]]], symbol: str, length: int = 14, k: int = 3, d: int = 3) -> Optional[Dict[str, float]]:
    if symbol not in mark_price_dict or not mark_price_dict[symbol]: return None
    history = sorted(mark_price_dict[symbol], key=lambda x: x[0])
    if len(history) < length: return None
    df = pd.DataFrame(history, columns=['timestamp', 'price'])
    if len(df) < length: return None
    last_price_timestamp = df.iloc[-1]['timestamp']
    prices = df['price'].astype(float)
    delta = prices.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    alpha = 1.0 / float(length)
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    if rsi.dropna().empty: return None
    
    lowest = rsi.rolling(length, min_periods=length).min()
    highest = rsi.rolling(length, min_periods=length).max()
    range_span = (highest - lowest).replace(0.0, np.nan)
    
    stoch = ((rsi - lowest) / range_span).clip(lower=0.0, upper=1.0) * 100.0
    
    k_series = stoch.rolling(k, min_periods=k).mean()
    d_series = k_series.rolling(d, min_periods=d).mean()
    
    k_vals = k_series.clip(lower=0, upper=100)
    d_vals = d_series.clip(lower=0, upper=100)
    
    if k_vals.empty or d_vals.empty: return None
    k_curr = float(k_vals.iloc[-1]) if pd.notna(k_vals.iloc[-1]) else 50.0
    d_curr = float(d_vals.iloc[-1]) if pd.notna(d_vals.iloc[-1]) else 50.0
    k_prev = float(k_vals.iloc[-2]) if len(k_vals) > 1 and pd.notna(k_vals.iloc[-2]) else k_curr
    d_prev = float(d_vals.iloc[-2]) if len(d_vals) > 1 and pd.notna(d_vals.iloc[-2]) else d_curr
    return {
        'k_1m': k_curr, 
        'd_1m': d_curr, 
        'k_1m_prev': k_prev, 
        'd_1m_prev': d_prev,
        'last_price_timestamp': last_price_timestamp }

def load_symbols_from_json(symbols_file: str = "symbols.json") -> List[str]:
    """Load all symbols from symbols.json file. Returns list of symbol strings."""
    symbols_path = Path(__file__).parent / symbols_file
    if not symbols_path.exists(): return []
    try:
        with open(symbols_path, 'r') as f:
            symbols = json.load(f)
            return symbols if isinstance(symbols, list) else []
    except Exception as e:
        logger.error(f"Error loading symbols from {symbols_file}: {e}")
        return []

def initialize_1m_mark_prices_dict(symbols: Optional[List[str]] = None) -> Dict[str, List[Tuple[float, float]]]:
    """Initialize 1m mark prices dict for all active symbols. Returns dict: symbol -> [(timestamp, mark_price), ...]"""
    if symbols is None: symbols = load_symbols_from_json()
    return {symbol: [] for symbol in symbols}

def update_1m_mark_price(mark_price_dict: Dict[str, List[Tuple[float, float]]], symbol: str, mark_price: float, timestamp: Optional[float] = None, max_age_seconds: int = 840) -> None:
    """Update 1m mark price history for a symbol. Removes entries older than max_age_seconds (default 840 = 14 minutes)."""
    if symbol not in mark_price_dict: mark_price_dict[symbol] = []
    if timestamp is None: timestamp = time.time()
    history = mark_price_dict[symbol]
    history[:] = [(ts, price) for ts, price in history if (timestamp - ts) <= max_age_seconds]
    if not history or abs(history[-1][1] - mark_price) > 0.0001: history.append((timestamp, mark_price))

async def save_1m_mark_prices_dict(mark_price_dict: Dict[str, List[Tuple[float, float]]], file_path: Optional[Path] = None) -> bool:
    """Save 1m mark prices dict to JSON file. Converts tuples to lists for JSON serialization."""
    if file_path is None: file_path = Path(__file__).parent / "data" / "1m_mark_prices.json"
    try:
        json_data = {symbol: [[float(ts), float(price)] for ts, price in history] for symbol, history in mark_price_dict.items()}
        await atomic_write_json(file_path, json_data)
        return True
    except Exception as e:
        logger.error(f"Error saving 1m mark prices dict to {file_path}: {e}")
        return False

async def load_1m_mark_prices_dict(file_path: Optional[Path] = None, symbols: Optional[List[str]] = None) -> Dict[str, List[Tuple[float, float]]]:
    """Load 1m mark prices dict from JSON file. Returns dict: symbol -> [(timestamp, mark_price), ...]"""
    if file_path is None: file_path = Path(__file__).parent / "data" / "1m_mark_prices.json"
    result = initialize_1m_mark_prices_dict(symbols)
    if not file_path.exists(): return result
    try:
        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()
            json_data = json.loads(content)
            for symbol, history_list in json_data.items():
                if isinstance(history_list, list):
                    result[symbol] = [(float(entry[0]), float(entry[1])) for entry in history_list if len(entry) >= 2]
        return result
    except Exception as e:
        logger.error(f"Error loading 1m mark prices dict from {file_path}: {e}")
        return result

def clean_position_key(position_key: str) -> str:
    """Normalize malformed position keys that contain duplicated account prefixes or extra colons."""
    if not position_key or not isinstance(position_key, str):
        return position_key
    key = position_key.strip()
    colon_count = key.count(":")
    if colon_count == 0:
        logger.warning(f"Position key has no colons: '{position_key}'")
        return key
    if colon_count > 1:
        logger.warning(f"Cleaning malformed position key with {colon_count} colons: '{position_key}'")
    base_account, remainder = key.split(":", 1)
    base_account = base_account.strip()
    remainder = remainder.strip()
    parsed_account, parsed_symbol, parsed_side = ("unknown", "unknown", "unknown")
    try:
        parsed_account, parsed_symbol, parsed_side = parse_position_key(key)
    except Exception as exc:
        logger.debug(f"clean_position_key parse failed for '{position_key}': {exc}")
    account_key = parsed_account if parsed_account not in ("unknown", None) else base_account
    raw_symbol = parsed_symbol if parsed_symbol not in ("unknown", None) else remainder
    if ":" in raw_symbol:
        pieces = [part for part in raw_symbol.split(":") if part and part.upper() != account_key.upper()]
        if pieces:
            raw_symbol = pieces[-1]
        else:
            raw_symbol = raw_symbol.replace(":", "")
    symbol_candidates = raw_symbol.rsplit("_", 1)[0] if raw_symbol.endswith(("_LONG", "_SHORT")) else raw_symbol
    symbol = symbol_candidates.replace("_LONG", "").replace("_SHORT", "").replace("__", "_").strip().upper()
    if not symbol:
        logger.error(f"Could not extract valid symbol from malformed position key: '{position_key}'")
        return position_key
    raw_side = parsed_side if parsed_side in ("LONG", "SHORT") else ""
    if not raw_side and "_" in remainder:
        raw_side = remainder.rsplit("_", 1)[-1]
    side = (raw_side or "").upper()
    if side not in ("LONG", "SHORT"):
        if side in ("SELL", "SHORT_SELL"):
            side = "SHORT"
        elif side in ("BUY", "LONG_BUY"):
            side = "LONG"
        else:
            logger.warning(f"clean_position_key: unknown side '{side}' for key '{position_key}', defaulting LONG")
            side = "LONG"
    clean_key = f"{account_key or 'men'}:{symbol}_{side}"
    if clean_key != position_key:
        logger.info(f"Cleaned position key '{position_key}' -> '{clean_key}'")
    return clean_key


async def scan_and_fix_position_files(root_dir: str = ".") -> dict:
    """
    Scan all JSON files in the given directory and subdirectories for malformed position keys.
    Fixes any malformed keys found and reports the results.
    
    Args:
        root_dir: Root directory to scan (defaults to current directory)
        
    Returns:
        Dictionary with scan results and statistics
    """
    import json
    import os
    from pathlib import Path
    
    results = {
        "files_scanned": 0,
        "files_with_malformed_keys": 0,
        "total_keys_fixed": 0,
        "errors": [],
        "fixed_files": []
    }
    
    try:
        root_path = Path(root_dir)
        
        # Find only position-related JSON files (don't scan everything)
        position_file_patterns = [
            "*_positions.json",           # Main position files
            "*_reenter.json",             # Reentry files
            "*_reversed_positions.json",  # Reversed positions
            "*_augmented_positions.json", # Augmented positions
            "*_stop.json",                # Stop loss files
            "*_graceful_exit.json"        # Graceful exit files
        ]
        
        json_files = []
        for pattern in position_file_patterns:
            json_files.extend(root_path.glob(pattern))
        
        # Also check account directories for position files
        account_dirs = ["flz", "men", "ang", "inf"]
        for account_dir in account_dirs:
            account_path = root_path / account_dir
            if account_path.exists() and account_path.is_dir():
                for pattern in position_file_patterns:
                    json_files.extend(account_path.glob(pattern))
        
        # Remove duplicates
        json_files = list(set(json_files))
        results["files_scanned"] = len(json_files)
        
        for json_file in json_files:
            try:
                # Skip backup files to avoid modifying them
                if "backup" in json_file.name or json_file.name.endswith(".bak"):
                    continue
                
                # Skip klines cache files entirely - these are handled by ez_prices
                if "klines_cache" in str(json_file):
                    continue
                
                # Read the file
                with open(json_file, 'r', encoding='utf-8') as f:
                    content = json.load(f)
                
                file_modified = False
                keys_fixed = 0
                
                # Check if this is a position file (contains position data)
                if isinstance(content, dict):
                    # Look for position keys in the data
                    for key, value in content.items():
                        if isinstance(value, dict) and "position_side" in value:
                            # This looks like position data
                            if key.count(":") > 1:
                                # Malformed key detected
                                old_key = key
                                new_key = clean_position_key(key)
                                
                                if new_key != old_key:
                                    # Replace the key
                                    content[new_key] = content.pop(old_key)
                                    keys_fixed += 1
                                    file_modified = True
                                    logger.info(f"Fixed malformed key in {json_file}: '{old_key}' -> '{new_key}'")
                
                if file_modified:
                    # Write the fixed content back to the file
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(content, f, indent=2, ensure_ascii=False) 
                    
                    results["files_with_malformed_keys"] += 1
                    results["total_keys_fixed"] += keys_fixed
                    results["fixed_files"].append(str(json_file))
                    logger.info(f"Fixed {keys_fixed} malformed keys in {json_file}")
                
            except Exception as e:
                error_msg = f"Error processing {json_file}: {e}"
                results["errors"].append(error_msg)
                logger.error(error_msg)
        
        logger.info(f"Position file scan complete. Scanned {results['files_scanned']} files, "
                   f"fixed {results['total_keys_fixed']} malformed keys in {results['files_with_malformed_keys']} files.")
        
    except Exception as e:
        error_msg = f"Error during position file scan: {e}"
        results["errors"].append(error_msg)
        logger.error(error_msg)
    
    return results

def get_reversed_position_key(position_key: str) -> str:
    """Get the reversed position key for a given position."""
    account_key, symbol, position_side = parse_position_key(position_key)
    reversed_side = "SHORT" if position_side == "LONG" else "LONG"
    return f"{account_key}:{symbol}_{reversed_side}"

def clean_and_repair_symbol(symbol: str) -> str:
    """Clean symbol by removing position key format, but preserve valid symbols."""
    if not symbol or not isinstance(symbol, str):
        return symbol or ""
    symbol = symbol.strip().upper()
    if ":" in symbol:
        try:
            parts = symbol.split(":", 1)
            if len(parts) == 2:
                account_part, symbol_side = parts
                if symbol_side.endswith("_LONG") and len(symbol_side) > 5:
                    return symbol_side[:-5]
                elif symbol_side.endswith("_SHORT") and len(symbol_side) > 6:
                    return symbol_side[:-6]
                return symbol_side
        except Exception:
            pass
    if symbol.endswith("_LONG") and len(symbol) > 5:
        return symbol[:-5]
    elif symbol.endswith("_SHORT") and len(symbol) > 6:
        return symbol[:-6]
    return symbol

def construct_position_key(account_key: str, symbol: str, position_side: str) -> Optional[str]:
    normalized_account = (account_key or "men").strip() or "men"
    try:
        if isinstance(symbol, str) and (":" in symbol or symbol.endswith("_LONG") or symbol.endswith("_SHORT")):
            parsed_acc, parsed_sym, parsed_side = parse_position_key(symbol)
            # Only adopt parsed account if it's a simple token; drop any extra prefixes
            if parsed_acc:
                normalized_account = str(parsed_acc).strip()
                if ":" in normalized_account:
                    normalized_account = normalized_account.split(":")[-1].strip()
            if parsed_sym:
                sym_clean = str(parsed_sym).strip()
                if ":" in sym_clean:
                    sym_clean = sym_clean.split(":")[-1].strip()
                symbol = sym_clean
            if not position_side and parsed_side in ("LONG", "SHORT"):
                position_side = parsed_side
    except Exception:
        pass
    normalized_symbol = (symbol or "").strip().upper()
    normalized_side = (position_side or "").strip().upper()
    if normalized_side not in ("LONG", "SHORT"):
        if normalized_symbol.endswith("_LONG"):
            normalized_symbol = normalized_symbol[:-5]
            normalized_side = "LONG"
        elif normalized_symbol.endswith("_SHORT"):
            normalized_symbol = normalized_symbol[:-6]
            normalized_side = "SHORT"
    if not normalized_symbol:
        logger.error(f"construct_position_key: empty symbol after normalization (account={normalized_account}, side={normalized_side})")
        raise ValueError("construct_position_key requires a non-empty symbol")
    if normalized_side not in ("LONG", "SHORT"):
        _ns = normalized_side.upper()
        if _ns in ("BUY", "LONG_BUY"):
            normalized_side = "LONG"
        elif _ns in ("SELL", "SHORT_SELL", "SELL_SHORT"):
            normalized_side = "SHORT"
        else:
            logger.error(f"construct_position_key: invalid side '{normalized_side}' for symbol {normalized_symbol}; returning None")
            return None
    if not normalized_account:
        normalized_account = "men"
    key = f"{normalized_account}:{normalized_symbol}_{normalized_side}"
    if key.count(":") != 1:
        cleaned = clean_position_key(key)
        if cleaned.count(":") == 1:
            return cleaned
        raise ValueError(f"construct_position_key produced malformed key: {key}")
    return key


async def _load_klines_from_fallback_interval(
    local_redis_client, gateway_redis_client, symbol: str, interval: str, klines_cache_dir: str, unified_manager=None
) -> pd.DataFrame:
    """Attempts to load and resample from a more granular timeframe."""
    fallback_chain = {'D': '4h', '4h': '1h', '1h': '15m', '15m': '3m'}
    fallback_interval = fallback_chain.get(interval)
    if not fallback_interval:
        return pd.DataFrame()
    fallback_df = await get_comprehensive_klines(local_redis_client, gateway_redis_client, symbol, fallback_interval, klines_cache_dir, unified_manager=unified_manager)
    if fallback_df.empty:
        return pd.DataFrame()
    return _coerce_interval_for_resampling(fallback_df, interval)

def parse_position_key(position_key: str) -> tuple[str, str, str]:
    try:
        if not position_key or not isinstance(position_key, str):
            logger.error(f"Invalid position_key: {position_key} (must be non-empty string)")
            return "unknown", "unknown", "unknown"
        position_key = position_key.strip()
        if position_key.upper() in ('POSITIONS', 'META', 'TIMESTAMP', 'TIME', 'DATA', 'INFO', 'ACCOUNT_KEY', 'SIDE'):
            return "unknown", "unknown", "unknown"
        if ":" in position_key:
            parts = position_key.split(":", 1)  # Split only on first colon
            if len(parts) == 2:
                account_key, symbol_side = parts
                symbol_side_parts = symbol_side.split("_", 1)
                if len(symbol_side_parts) == 2:
                    symbol, position_side = symbol_side_parts
                    if account_key and symbol and position_side and position_side in ["LONG", "SHORT"]:
                        return account_key, symbol, position_side
                    else:
                        logger.warning(f"Invalid components in position key: account='{account_key}', symbol='{symbol}', side='{position_side}'")
                else:
                    logger.warning(f"Cannot split symbol_side in position key: {position_key}")
            else:
                logger.warning(f"Invalid position key format with colon: {position_key}")
        if position_key.endswith("_LONG") or position_key.endswith("_SHORT"):
            logger.warning(f"Malformed position key missing account prefix: {position_key}")
            
            if position_key.endswith("_LONG"):
                symbol = position_key[:-5]  # Remove "_LONG"
                position_side = "LONG"
            else:  # _SHORT
                symbol = position_key[:-6]  # Remove "_SHORT"
                position_side = "SHORT"
            
            if symbol and len(symbol) > 0:
                logger.info(f"Extracted from malformed key: symbol='{symbol}', side='{position_side}'")
                # Return with empty account_key to indicate malformed key
                return "", symbol, position_side
            else:
                logger.error(f"Could not extract valid symbol from malformed key: {position_key}")
                return "unknown", "unknown", "unknown"
        
        # Case 3: Just a symbol (e.g., "ETHUSDC")
        # This might be a symbol passed where a position key was expected
        logger.warning(f"Input appears to be a symbol, not a position key: {position_key}")
        return "", position_key, "UNKNOWN"
        
    except Exception as e:
        logger.error(f"Error parsing position key {position_key}: {e}")
        return "unknown", "unknown", "unknown"


def pk_is_long(position_key: str) -> bool:
    """Returns True iff position_key is a LONG position. Use instead of 'LONG' in key."""
    return isinstance(position_key, str) and position_key.endswith("_LONG")


def pk_is_short(position_key: str) -> bool:
    """Returns True iff position_key is a SHORT position. Use instead of 'SHORT' in key."""
    return isinstance(position_key, str) and position_key.endswith("_SHORT")


def pk_symbol(position_key: str) -> str:
    """Extract clean symbol from position_key, handling symbols with underscores."""
    if not isinstance(position_key, str):
        return ""
    sym_side = position_key.split(":", 1)[-1] if ":" in position_key else position_key
    if sym_side.endswith("_LONG"):
        return sym_side[:-5]
    if sym_side.endswith("_SHORT"):
        return sym_side[:-6]
    return sym_side


_SIDE_MEMBERSHIP_CACHE: Dict[str, Tuple[float, set]] = {}
_SIDE_MEMBERSHIP_TTL_S: float = 30.0


def _load_side_membership(account_key: str, side: str) -> set:
    """Load symbols_{account}_{side}.json membership set with 30s TTL cache.
    Returns empty set if file missing/unreadable. side ∈ {"long","short"}."""
    side = side.lower()
    if side not in ("long", "short"):
        return set()
    cache_key = f"{account_key}:{side}"
    now = time.time()
    cached = _SIDE_MEMBERSHIP_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _SIDE_MEMBERSHIP_TTL_S:
        return cached[1]
    fpath = Path(__file__).resolve().parent / f"symbols_{account_key}_{side}.json"
    members: set = set()
    if fpath.exists():
        try:
            data = json.loads(fpath.read_text())
            if isinstance(data, list):
                members = {str(s).strip().upper() for s in data if isinstance(s, str)}
            elif isinstance(data, dict):
                members = {str(k).strip().upper() for k in data.keys()}
        except Exception:
            members = set()
    _SIDE_MEMBERSHIP_CACHE[cache_key] = (now, members)
    return members


def primary_side_for_symbol(account_key: str, symbol: str) -> Optional[str]:
    """USER 2026-05-16 mandate (PHBUSDT account-wipe): "when unclear which is the hedge check
    symbols_{acc_key}_long/short". Given (account, symbol) returns the side ('LONG' or 'SHORT')
    that the symbol is allow-listed on. Used to disambiguate origin-vs-hedge when both sides
    have positions open. Returns None when membership is ambiguous (in both / in neither).

    Rules:
      symbol in symbols_{acc}_long.json AND not in _short → primary='LONG' (hedge = SHORT)
      symbol in symbols_{acc}_short.json AND not in _long → primary='SHORT' (hedge = LONG)
      both / neither → None (caller must fall back to opened_at / notional / explicit reason)
    """
    if not account_key or not symbol:
        return None
    sym_u = symbol.strip().upper()
    long_members = _load_side_membership(account_key, "long")
    short_members = _load_side_membership(account_key, "short")
    in_long = sym_u in long_members
    in_short = sym_u in short_members
    if in_long and not in_short:
        return "LONG"
    if in_short and not in_long:
        return "SHORT"
    return None


def hedge_side_for_origin(account_key: str, symbol: str, origin_side: str) -> Tuple[str, bool]:
    """Returns (hedge_side, origin_matches_primary). hedge_side is always the opposite of origin
    (same-symbol hedge convention). origin_matches_primary is True when origin_side matches
    primary_side_for_symbol — caller can log a warning if False to flag potentially-backwards
    origin/hedge classification (e.g., a SHORT that was actually born as a hedge for a LONG
    that closed long ago)."""
    hedge_side = "SHORT" if str(origin_side).upper() == "LONG" else "LONG"
    primary = primary_side_for_symbol(account_key, symbol)
    matches = (primary is None) or (primary == str(origin_side).upper())
    return hedge_side, matches


async def get_live_usdc_pairs(session: Optional[aiohttp.ClientSession] = None) -> set:
    """Hit Binance futures exchangeInfo for live USDC perpetuals. Cache 15min on disk.
    Fallback to hardcoded set only on API failure. Previous version was gutted and returned
    only the stale fallback — caused inf to open USDT positions for symbols that now have
    USDC versions (no-commission preferred)."""
    import os, time as _t
    cache_file = None
    try:
        import config as _cfg
        cache_file = _cfg.Config.LIVE_USDC_PAIRS_FILE if hasattr(_cfg, 'Config') and hasattr(_cfg.Config, 'LIVE_USDC_PAIRS_FILE') else None
    except Exception:
        cache_file = None
    # Try cache if fresh (< 15 min)
    if cache_file and os.path.exists(cache_file):
        try:
            age = _t.time() - os.path.getmtime(cache_file)
            if age < 900:
                with open(cache_file) as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        return set(data)
        except Exception:
            pass
    # Fetch live
    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True
    try:
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10.0)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"exchangeInfo HTTP {resp.status}")
            data = await resp.json()
        pairs = set()
        for s in data.get("symbols", []):
            if (s.get("quoteAsset") == "USDC" and s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"):
                pairs.add(s.get("symbol"))
        if len(pairs) < 10:
            raise RuntimeError(f"only {len(pairs)} USDC pairs returned — suspicious")
        logger.info(f"[USDC_PAIRS_FETCHED] {len(pairs)} live USDC perpetuals from Binance exchangeInfo")
        # Persist cache
        if cache_file:
            try:
                with open(cache_file, 'w') as f:
                    json.dump(sorted(pairs), f)
            except Exception as e:
                logger.warning(f"[USDC_PAIRS] cache write failed: {e}")
        return pairs
    except Exception as e:
        logger.error(f"[USDC_PAIRS_FETCH_FAIL] {e} — using fallback (may be stale)")
        return get_fallback_usdc_pairs()
    finally:
        if close_session:
            try: await session.close()
            except Exception: pass

def get_fallback_usdc_pairs() -> set:
    fallback_pairs = { "1000BONKUSDC","1000PEPEUSDC", "1000SHIBUSDC","AAVEUSDC","ADAUSDC","ARBUSDC","AVAXUSDC","BCHUSDC","BNBUSDC","BOMEUSDC","BTCUSDC","CRVUSDC","DOGEUSDC","ENAUSDC","ETHFIUSDC","ETHUSDC","FILUSDC","HBARUSDC","IPUSDT","KAITOUSDT","LINKUSDC","LTCUSDC","NEARUSDC","NEOUSDC","ORDIUSDC","PENGUUSDC","PNUTUSDT","SOLUSDC","SUIUSDC","TIAUSDC","TRUMPUSDC","UNIUSDC","WIFUSDC","WLDUSDC","XRPUSDC" }
    logger.info(f"Using fallback USDC pairs: {len(fallback_pairs)} symbols")
    return fallback_pairs


def _coerce_interval_for_resampling(df: pd.DataFrame, target_interval: str) -> pd.DataFrame:
    """Resamples a DataFrame to a new, larger interval."""
    try:
        df.index = pd.to_datetime(df['timestamp'], utc=True)
        freq_map = {'D': 'D', '4h': '4H', '1h': '1H', '15m': '15T', '3m': '3T'}
        freq = freq_map.get(target_interval)
        if not freq:
            return df

        resampled = df.resample(freq).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        resampled = resampled.reset_index()
        return clean_kline_data(resampled)
    except Exception as e:
        logger.error(f"[Resample] Coercion to {target_interval} failed: {e}")
        return pd.DataFrame()

def is_kline_data_stale(df: pd.DataFrame, interval: str, allowed_stale_bars: int = 3, startup_mode: bool = False) -> bool:
    if df.empty:return True
    try:
        if 'timestamp_dt' in df.columns:timestamps=df['timestamp_dt']
        elif 'timestamp' in df.columns:
            if df['timestamp'].dtype==object:
                try:timestamps=pd.to_datetime(df['timestamp'],format='%Y-%m-%dT%H:%M:%S.%fZ',utc=True,errors='raise')
                except Exception: timestamps=pd.to_datetime(df['timestamp'],utc=True,errors='coerce')
            else:timestamps=pd.to_datetime(df['timestamp'],unit='ms',utc=True,errors='coerce')
        else:return True
        valid_timestamps=timestamps.dropna()
        if valid_timestamps.empty:return True
        last_timestamp=valid_timestamps.iloc[-1]
        now_utc=datetime.now(timezone.utc)
        interval_timedelta=parse_interval_to_timedelta(interval)
        max_allowed_age=interval_timedelta*allowed_stale_bars
        if startup_mode:max_allowed_age=interval_timedelta*(allowed_stale_bars*4)
        actual_age=now_utc-last_timestamp
        return actual_age>max_allowed_age
    except Exception as e:
        print(f"ERROR in is_kline_data_stale for {interval}: {e}")
        return True

def clean_timestamps_recursively(data):
    """Recursively convert pandas Timestamps to ISO format strings in nested data structures."""
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

def clean_nans(obj):
    """Recursively clean NaN values from nested data structures, replacing them with None."""
    if isinstance(obj, dict):
        return {k: clean_nans(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_nans(item) for item in obj]
    elif pd.isna(obj):
        return None
    else:
        return obj

def force_usdc_if_needed(symbol: str, available_usdc_pairs: set) -> str:
    """Correct a single symbol if it has a USDC version."""
    if symbol.endswith("USDT"):
        usdc_symbol = symbol.replace("USDT", "USDC")
        # --- FIX: Checks against the provided set ---
        if usdc_symbol in available_usdc_pairs:
            return usdc_symbol
    return symbol

def force_usdc_in_list(symbols: list[str], available_usdc_pairs: set) -> list[str]:
    """Convert USDT symbols to USDC if available."""
    converted = []
    for symbol in symbols:
        if symbol.endswith("USDT"):
            usdc_version = symbol.replace("USDT", "USDC")
            if usdc_version in available_usdc_pairs:
                converted.append(usdc_version)
            else:
                converted.append(symbol)
        else:
            converted.append(symbol)
    return converted

def safe_fetch_float(value, default=None):
    """Safely convert a value to float, returning default if conversion fails."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def safe_fetch_int(value, default=0):
    """Safely convert a value to int, returning default if conversion fails."""
    if value is None:
        return default
    try:
        return int(float(value))  # Handle float strings like "123.0"
    except (ValueError, TypeError):
        return default

_global_redis_manager = None
def set_global_redis_manager(manager):
    global _global_redis_manager
    _global_redis_manager = manager


async def _fetch_redis_kline_safe(client, source_name: str, symbol: str, interval: str) -> pd.DataFrame:
    """Helper to fetch and clean klines from a specific Redis client safely."""
    try:
        redis_key = f"klines:{symbol}:{interval}"
        # Short timeout to ensure we don't block the main process waiting for a slow remote Redis
        raw = await asyncio.wait_for(client.get(redis_key), timeout=0.5)
        
        if not raw:
            return pd.DataFrame()
            
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:  # pylint: disable=no-member
            return pd.DataFrame()

        # Extract list from various potential payload formats
        if isinstance(payload, dict):
            klines = payload.get("klines") or payload.get("data")
        elif isinstance(payload, list):
            klines = payload
        else:
            return pd.DataFrame()

        if not klines:
            return pd.DataFrame()

        df = pd.DataFrame(klines)
        return clean_kline_data(df, is_raw_api_data=False)
    except Exception as e:
        # logger.debug(f"Redis fetch failed for {source_name} {symbol}:{interval}: {e}")
        return pd.DataFrame()

async def _fetch_disk_kline_safe(dir_path: Path, symbol: str, interval: str) -> pd.DataFrame:
    try:
        if not dir_path.exists():
            return pd.DataFrame()
        df = await _read_kline_file(symbol, interval, str(dir_path))
        if df is None or df.empty:
            return pd.DataFrame()
        return df
    except Exception:
        return pd.DataFrame()

async def get_comprehensive_klines(local_redis_client, gateway_redis_client, symbol: str, interval: str, klines_cache_dir: str, unified_manager=None) -> pd.DataFrame:
    log_prefix = f"[CompKlines][{symbol}_{interval}]"
    min_required_bars = {'1m': 1440, '3m': 220, '15m': 200, '1h': 200, '4h': 200, 'D': 50}
    min_bars = min_required_bars.get(interval, 80)

    # 1. Prepare Sources
    fetch_tasks = []
    
    # --- A. Disk Sources (High Priority, Local) ---
    # We explicitly look for your rsync folders
    base_path = Path.home() / 'binance'
    potential_dirs = [
        Path(klines_cache_dir) if klines_cache_dir else None,
        base_path / 'klines_cache',
        base_path / 'klines_cache_gateway',
        base_path / 'klines_cache_macbook',
        base_path / 'klines_cache_server'
    ]
    
    # Deduplicate and validate
    valid_dirs = []
    seen_paths = set()
    for d in potential_dirs:
        if d and d not in seen_paths and d.exists():
            valid_dirs.append(d)
            seen_paths.add(d)

    # Add disk tasks
    for d in valid_dirs:
        # We assume _fetch_disk_kline_safe handles the file read.
        # Ideally, this runs in a thread to avoid blocking the loop during JSON parse.
        fetch_tasks.append(_fetch_disk_kline_safe(d, symbol, interval))

    # --- B. Redis Sources (Network, Low Timeout) ---
    redis_timeout = 0.15  # Strict 150ms timeout
    
    redis_mgr = await get_simple_redis_manager()
    redis_connections = []
    
    if redis_mgr and redis_mgr.connections:
        for name, client in redis_mgr.connections.items():
            if client: redis_connections.append((name, client))
    
    # Add legacy clients if not present
    if local_redis_client and not any(c == local_redis_client for _, c in redis_connections):
        redis_connections.append(("arg_local", local_redis_client))
    if gateway_redis_client and not any(c == gateway_redis_client for _, c in redis_connections):
        redis_connections.append(("arg_gateway", gateway_redis_client))

    # Wrapper to enforce strict timeout on Redis
    async def _timed_redis_fetch(client, name):
        try:
            return await asyncio.wait_for(
                _fetch_redis_kline_safe(client, name, symbol, interval),
                timeout=redis_timeout
            )
        except asyncio.TimeoutError:
            # Silently fail on timeout to keep speed up
            return pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    for name, client in redis_connections:
        fetch_tasks.append(_timed_redis_fetch(client, name))

    # 2. Execute All Fetches Concurrently
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    # 3. Consolidate Data
    valid_dfs = []
    
    for res in results:
        if isinstance(res, pd.DataFrame) and not res.empty and 'timestamp_dt' in res.columns:
            valid_dfs.append(res)

    if not valid_dfs:
        # Fallback to resampling
        return await _load_klines_from_fallback_interval(
            local_redis_client, gateway_redis_client, symbol, interval, klines_cache_dir, unified_manager
        )

    # 4. Merge
    try:
        # Concatenate and sort
        consolidated_df = pd.concat(valid_dfs, ignore_index=True)
        consolidated_df = consolidated_df.drop_duplicates(subset=['timestamp'])
        consolidated_df = consolidated_df.sort_values('timestamp_dt').reset_index(drop=True)
        
        # Fast clean (assume types are mostly correct from sources)
        if 'open' in consolidated_df.columns:
             for col in ['open', 'high', 'low', 'close', 'volume']:
                 consolidated_df[col] = pd.to_numeric(consolidated_df[col], errors='coerce')
        
        # 5. Check sufficiency
        if len(consolidated_df) >= min_bars:
            return consolidated_df
        else:
            # Attempt fill from lower timeframe
            resampled_df = await _load_klines_from_fallback_interval(
                local_redis_client, gateway_redis_client, symbol, interval, klines_cache_dir, unified_manager
            )
            
            if not resampled_df.empty:
                combined_df = pd.concat([consolidated_df, resampled_df], ignore_index=True)
                combined_df = combined_df.drop_duplicates(subset=['timestamp'])
                combined_df = combined_df.sort_values('timestamp_dt').reset_index(drop=True)
                
                # Ensure numeric again
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    combined_df[col] = pd.to_numeric(combined_df[col], errors='coerce')
                
                return combined_df
            
            return consolidated_df

    except Exception as e:
        logger.error(f"{log_prefix} Error during consolidation: {e}")
        return max(valid_dfs, key=len) if valid_dfs else pd.DataFrame()

def _resolve_klines_directories():
    env = get_current_environment()["env"].lower()
    base = config.BASE_PATH
    env_map = {
        "macbook": ["klines_cache", "klines_cache_gateway"],
        "gateway": ["klines_cache"],
        "server": ["klines_cache", "klines_cache_macbook", "klines_cache_gateway"]
    }
    names = env_map.get(env, env_map["server"])
    candidates = []
    seen = set()
    for name in names:
        path = config.KLINES_CACHE_DIR if name == "klines_cache" else base / name
        if not isinstance(path, Path):
            path = Path(path)
        if path in seen:
            continue
        seen.add(path)
        # Only add if directory exists
        if path.exists() and path.is_dir():
            candidates.append(path)
    return candidates

async def _read_latest_kline_from_dirs(symbol: str, interval: str):
    min_required_bars = {'3m': 220, '15m': 200, '1h': 200, '4h': 200, 'D': 50}
    min_bars = min_required_bars.get(interval, 80)
    staleness_tolerance = {'3m': 240, '15m': 1200, '1h': 4800, '4h': 18000, 'D': 172800}
    tolerance_seconds = staleness_tolerance.get(interval, 600)
    candidates = []
    all_dirs = []
    for dir_path in _resolve_klines_directories():
        all_dirs.append(dir_path)
        consolidated_dir = dir_path / "consolidated_klines"
        if consolidated_dir.exists() and consolidated_dir.is_dir():
            all_dirs.append(consolidated_dir)
    for dir_path in all_dirs:
        try:
            if not dir_path or not dir_path.is_dir():
                continue
            df = await _read_kline_file(symbol, interval, dir_path)
        except Exception:
            continue
        if df.empty or "timestamp_dt" not in df.columns:
            continue
        ts = df["timestamp_dt"].iloc[-1]
        if pd.isna(ts):
            continue
        candidates.append({'df': df, 'ts': ts, 'dir': dir_path, 'bars': len(df)})
    if not candidates:
        return pd.DataFrame(), None, pd.Timestamp(0, tz="UTC")
    candidates.sort(key=lambda x: x['ts'], reverse=True)
    freshest = candidates[0]
    if freshest['bars'] >= min_bars:
        logger.debug(f"[DiskScan][{symbol}_{interval}] ✅ Fresh+Full data in {freshest['dir']} ({freshest['bars']}/{min_bars} bars, ts={freshest['ts']})")
        return freshest['df'], freshest['dir'], freshest['ts']
    full_candidate = next((c for c in candidates if c['bars'] >= min_bars), None)
    if full_candidate and (freshest['ts'] - full_candidate['ts']).total_seconds() < tolerance_seconds:
        logger.debug(f"[DiskScan][{symbol}_{interval}] ✅ Using full data from {full_candidate['dir']} ({full_candidate['bars']} bars, only {(freshest['ts']-full_candidate['ts']).total_seconds():.0f}s older, tolerance={tolerance_seconds}s)")
        return full_candidate['df'], full_candidate['dir'], full_candidate['ts']
    if full_candidate:
        fresh_latest_ts = freshest['df']['timestamp_dt'].max()
        full_latest_ts = full_candidate['df']['timestamp_dt'].max()
        newer_bars = freshest['df'][freshest['df']['timestamp_dt'] > full_latest_ts]
        if not newer_bars.empty:
            merged_df = pd.concat([full_candidate['df'], newer_bars], ignore_index=True)
            merged_df = merged_df.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
            logger.info(f"[DiskScan][{symbol}_{interval}] ✅ MERGED full from {full_candidate['dir']} + fresh from {freshest['dir']} = {len(merged_df)} bars (freshest ts={merged_df['timestamp_dt'].max()})")
            return merged_df, f"{full_candidate['dir']}+{freshest['dir']}", merged_df['timestamp_dt'].max()
        logger.debug(f"[DiskScan][{symbol}_{interval}] ✅ Full data from {full_candidate['dir']} ({full_candidate['bars']} bars)")
        return full_candidate['df'], full_candidate['dir'], full_candidate['ts']
    logger.warning(f"[DiskScan][{symbol}_{interval}] ⚠️ Using freshest partial data from {freshest['dir']} ({freshest['bars']}/{min_bars} bars)")
    return freshest['df'], freshest['dir'], freshest['ts']

async def _get_freshest_kline(symbol: str, interval: str, redis_sources=None):
    best_df = pd.DataFrame()
    best_ts = pd.Timestamp(0, tz="UTC")
    best_source = None
    redis_sources = redis_sources or []
    log_prefix = f"[Freshest][{symbol}_{interval}]"
    async def read_from_redis(source_name, client):
        redis_key = f"klines:{symbol}:{interval}"
        try:
            raw = await asyncio.wait_for(client.get(redis_key), timeout=1.0)
        except (redis_exceptions.ConnectionError, ConnectionError, OSError, ConnectionResetError, asyncio.TimeoutError):
            return None  # Skip failed Redis source
        except Exception:
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        if isinstance(payload, dict):
            if "klines" in payload:
                klines = payload["klines"]
            elif "data" in payload:
                klines = payload["data"]
            else:
                return None
        elif isinstance(payload, list):
            klines = payload
        else:
            return None
        df = pd.DataFrame(klines)
        if df.empty:
            return None
        ts_sample = None
        try:
            first = klines[0]
            if isinstance(first, dict):
                ts_sample = first.get("timestamp")
        except Exception:
            ts_sample = None
        is_raw = isinstance(ts_sample, (int, float))
        cleaned = clean_kline_data(df, is_raw_api_data=is_raw)
        if cleaned.empty or "timestamp_dt" not in cleaned.columns:
            return None
        return source_name, cleaned
    interval_durations = { '3m': 180,  '15m': 900,  '1h': 3600, '4h': 14400,  'D': 86400}
    interval_seconds = interval_durations.get(interval, 3600)
    min_required_bars = {'3m': 220, '15m': 200, '1h': 200, '4h': 200, 'D': 50}
    min_bars = min_required_bars.get(interval, 80)
    if redis_sources:
        tasks = [read_from_redis(name, client) for name, client in redis_sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if not res or isinstance(res, Exception):
                continue
            source_name, df = res
            ts = df["timestamp_dt"].iloc[-1]
            if pd.isna(ts):
                continue
            logger.debug(f"{log_prefix} Redis source '{source_name}' has {len(df)} bars, latest: {ts}")
            if ts > best_ts:
                best_df = df
                best_ts = ts
                best_source = f"redis:{source_name}"
        if not best_df.empty and not pd.isna(best_ts):
            now_utc = pd.Timestamp.now(tz="UTC")
            time_since_latest = (now_utc - best_ts).total_seconds()
            if time_since_latest <= interval_seconds * 1.5 and len(best_df) >= min_bars:
                logger.debug(f"{log_prefix} ✅ Found latest kline from {best_source} (ts={best_ts}, {time_since_latest:.0f}s ago, {len(best_df)} bars), skipping file check")
                return best_df, best_source, best_ts
    else:
        logger.debug(f"{log_prefix} No Redis sources available, falling back to disk files")
    file_df, file_dir, file_ts = await _read_latest_kline_from_dirs(symbol, interval)
    if not file_df.empty and not pd.isna(file_ts):
        logger.debug(f"{log_prefix} File source '{file_dir}' has {len(file_df)} bars, latest: {file_ts}")
        if not best_df.empty and not pd.isna(best_ts):
            logger.debug(f"{log_prefix} Comparing: Redis ts={best_ts} vs File ts={file_ts}")
            if best_ts > file_ts:
                file_latest_ts = file_df['timestamp_dt'].max()
                redis_newer = best_df[best_df['timestamp_dt'] > file_latest_ts]
                if not redis_newer.empty:
                    merged_df = pd.concat([file_df, redis_newer], ignore_index=True)
                    merged_df = merged_df.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
                    best_df = merged_df
                    best_ts = merged_df['timestamp_dt'].max()
                    best_source = f"merged:file+redis"
                    logger.debug(f"{log_prefix} Merged Redis+file: {len(best_df)} bars from {file_dir}")
                else:
                    best_df = file_df
                    best_ts = file_ts
                    best_source = f"file:{file_dir}" if file_dir else "file"
                    #logger.info(f"{log_prefix} ✅ Using FILE (Redis had no newer bars): {len(best_df)} bars, latest: {best_ts}")
            else:
                best_df = file_df
                best_ts = file_ts
                best_source = f"file:{file_dir}" if file_dir else "file"
                #logger.info(f"{log_prefix} ✅ Using FILE (fresher than Redis): {len(best_df)} bars, latest: {best_ts}")
        else:
            best_df = file_df
            best_ts = file_ts
            best_source = f"file:{file_dir}" if file_dir else "file"
            logger.debug(f"[Freshest][{symbol}_{interval}] Using file data: {len(best_df)} bars from {file_dir} (no Redis data)")
    elif not best_df.empty:
        logger.debug(f"[Freshest][{symbol}_{interval}] Using Redis data: {len(best_df)} bars from {best_source} (no file data)")
    return best_df, best_source, best_ts
    
async def get_current_price(symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
    """Robust multi-source price fetcher: Memory -> Redis -> Disk -> Indicators -> Positions -> Klines."""
    now_utc = datetime.now(timezone.utc); best_price, best_timestamp = None, None
    redis_manager = await get_simple_redis_manager(); redis_sources = []
    if redis_manager:
        for name in ["local", "gateway", "server"]:
            cli = redis_manager.connections.get(name)
            if cli: redis_sources.append((name, cli))
    for name, client in redis_sources:
        try:
            data = await asyncio.wait_for(client.get(f"mark_price:{symbol}"), timeout=0.2)
            if data:
                payload = json.loads(data) if isinstance(data, (str, bytes)) else data
                if isinstance(payload, dict):
                    p = float(payload.get("price", 0)); ts_val = payload.get("timestamp") or payload.get("time")
                    ts = (datetime.fromtimestamp(ts_val/1000.0 if ts_val > 1e12 else ts_val, tz=timezone.utc) if isinstance(ts_val, (int, float)) else isoparse(ts_val)) if ts_val else None
                    if ts and ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                    if p > 0:
                        if ts and (now_utc - ts).total_seconds() < 2.0: return p, ts
                        if not best_timestamp or (ts and ts > best_timestamp): best_price, best_timestamp = p, ts
                elif isinstance(payload, (float, int, str)): return float(payload), now_utc
        except Exception: pass
    for cache_file in [config.PRICE_CACHE_FILE, getattr(config, "PRICE_CACHE_FILE_2", None), config.PRICE_CACHE_FILE_3]:
        if cache_file and Path(cache_file).exists():
            try:
                async with FILE_READ_SEMAPHORE:
                    async with aiofiles.open(cache_file, "r") as f: cache_data = json.loads(await f.read())
                    if symbol in cache_data:
                        entry = cache_data[symbol]; p = float(entry.get("price", 0)) if isinstance(entry, dict) else float(entry)
                        ts = (isoparse(entry.get("timestamp")) if isinstance(entry, dict) and entry.get("timestamp") else None)
                        if ts and ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                        if p > 0:
                            if ts and (now_utc - ts).total_seconds() < 2.0: return p, ts
                            if not best_timestamp or (ts and ts > best_timestamp): best_price, best_timestamp = p, ts
            except Exception: continue
    try:
        data_dir = getattr(config, 'DATA_DIR', Path('data')); market_data_files = sorted(list(Path(data_dir).glob("market_data_*.json")), key=lambda p: p.stat().st_mtime, reverse=True)
        for market_file in market_data_files[:3]:
            try:
                async with FILE_READ_SEMAPHORE:
                    async with aiofiles.open(market_file, 'r') as f:
                        file_data = json.loads(await f.read())
                        if symbol in file_data:
                            p = safe_fetch_float(file_data[symbol].get('current_price'), 0.0)
                            if p > 0:
                                ts = datetime.fromtimestamp(market_file.stat().st_mtime, tz=timezone.utc)
                                if not best_timestamp or ts > best_timestamp: best_price, best_timestamp = p, ts
            except Exception: continue
    except Exception: pass
    try:
        base_path = Path(getattr(config, 'BASE_PATH', Path.home() / 'binance'))
        for account in ["ang", "inf", "flz", "men", "fin"]:
            for side in ['long', 'short']:
                pos_file = base_path / account / f"{side}_positions.json"
                if pos_file.exists():
                    try:
                        async with aiofiles.open(pos_file, 'r') as f:
                            pos_data = json.loads(await f.read())
                            for pk, p_info in pos_data.items():
                                if symbol in pk:
                                    p = safe_fetch_float(p_info.get('mark_price'), 0.0)
                                    if p > 0:
                                        ts = datetime.fromtimestamp(pos_file.stat().st_mtime, tz=timezone.utc)
                                        if not best_timestamp or ts > best_timestamp: best_price, best_timestamp = p, ts
                    except Exception: continue
    except Exception: pass
    if not best_price:
        for _, client in redis_sources:
            try:
                data = await client.get(f"klines:{symbol}:1m")
                if data:
                    payload = json.loads(data)
                    if payload:
                        p = float(payload[-1].get("close", 0)); ts = isoparse(payload[-1].get("timestamp"))
                        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                        if p > 0: return p, ts
            except Exception: pass
    if not best_price or best_price <= 0:
        try:
            _valid_symbols = set(json.load(open(Path(getattr(config, 'BASE_PATH', Path.home() / 'binance')) / 'symbols.json')))
        except Exception:
            _valid_symbols = set()
        if _valid_symbols and symbol not in _valid_symbols:
            logger.warning(f"⚠️ [PRICE_SKIP] {symbol} not in symbols.json (delisted?) — returning None instead of crashing"); return None, None
        try:
            import ez_alert
            ez_alert.alert_missing_price(symbol)
        except Exception as e:
            logger.error(f"Error calling alert_missing_price for {symbol}: {e}")
        logger.critical(f"🛑 [PRICE_MISSING] No price found for {symbol} in ANY source — returning None (skip this symbol) instead of crashing the whole account. 2026-06-04: stock-symbol contaminants (UUUU/UEC/TTD) in the crypto symbols.json crash-looped S1 crypto (which has no stock price feed). A single priceless symbol must NEVER crash-loop the process; genuine feed outages are covered by the data-service watchdogs. Matches the delisted-symbol path above."); return None, None
    return best_price, best_timestamp

async def get_klines_from_redis(local_redis_client, gateway_redis_client, symbol: str, interval: str, unified_manager=None) -> pd.DataFrame:
    """Get klines from Redis - uses direct Redis clients, no managers needed."""
    log_prefix = f"[KlinesFetch][{symbol}_{interval}]"
    best_df = pd.DataFrame()
    best_timestamp = pd.Timestamp(0, tz='UTC')
    best_source = "None"
    environment = get_current_environment()["env"].lower()
    redis_sources = []
    # Use direct Redis clients (preferred - simple and fast)
    if local_redis_client:
        redis_sources.append(("local", local_redis_client))
    if gateway_redis_client:
        redis_sources.append(("gateway", gateway_redis_client))
    # Fallback to manager if provided (for backward compatibility)
    if unified_manager and unified_manager.connections:
        for name, conn in unified_manager.connections.items():
            if conn and (name, conn) not in redis_sources:
                redis_sources.append((name, conn))

    if not redis_sources:
        file_df = _load_klines_from_file(symbol, interval)
        return file_df
    if environment == "macbook":
        preferred_order = ["local", "gateway", "server"]
    elif environment == "gateway":
        preferred_order = ["local", "server"]
    else:  # server
        preferred_order = ["gateway", "local"]

    redis_sources.sort(key=lambda x: preferred_order.index(x[0]) if x[0] in preferred_order else 999)

    async def read_from_redis(source_name, client):
        redis_key = f"klines:{symbol}:{interval}"
        try:
            raw = await asyncio.wait_for(client.get(redis_key), timeout=0.25)
            if not raw:
                return None
            payload = json.loads(raw)
            if isinstance(payload, dict) and "klines" in payload:
                klines = payload["klines"]
            elif isinstance(payload, list):
                klines = payload
            elif isinstance(payload, dict) and "data" in payload:
                klines = payload["data"]
            else:
                return None
            df = pd.DataFrame(klines)
            df = clean_kline_data(df, is_raw_api_data=False)
            if not df.empty and 'timestamp_dt' in df.columns:
                ts = df["timestamp_dt"].iloc[-1]
                return df, ts
        except Exception:
            return None
        return None

    results = await asyncio.gather(*[read_from_redis(name, cli) for name, cli in redis_sources], return_exceptions=True)
    for res in results:
        if not res or isinstance(res, Exception):
            continue
        df, ts = res
        source = "redis"
        if ts > best_timestamp:
            best_timestamp = ts
            best_df = df
            best_source = source

    if not best_df.empty:
        logger.debug(f"{log_prefix} ✅ Selected '{best_source}' Redis as best ({len(best_df)} bars, timestamp={best_timestamp}).")
        return best_df
    best_df, best_source, best_ts = await _get_freshest_kline(symbol, interval, redis_sources)
    return best_df

async def _fix_file_permissions(file_path: Path) -> bool:
    """Try to fix file permissions"""
    try:
        # Ensure parent directory exists and has permissions
        parent = file_path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        # Try to set ownership and permissions
        try:
            uid = pwd.getpwnam("niels").pw_uid
            gid = pwd.getpwnam("niels").pw_gid
            os.chown(str(parent), uid, gid)
            os.chmod(str(parent), 0o755)
            if file_path.exists():
                os.chown(str(file_path), uid, gid)
                os.chmod(str(file_path), 0o644)
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"Failed to fix permissions for {file_path}: {e}")
        return False

async def safe_write_file(file_path: Path, content: str, mode: str = 'w', encoding: str = 'utf-8') -> bool:
    """Write to file with automatic permission recovery on failure"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            async with aiofiles.open(file_path, mode, encoding=encoding) as f:
                await f.write(content)
            return True
        except PermissionError as e:
            logger.warning(f"Permission error writing to {file_path}, attempt {attempt + 1}/{max_attempts}")
            if attempt < max_attempts - 1:
                await _fix_file_permissions(file_path)
                await asyncio.sleep(0.1)
            else:
                logger.error(f"Failed to write to {file_path} after {max_attempts} attempts: {e}")
                return False
        except Exception as e:
            logger.error(f"Unexpected error writing to {file_path}: {e}")
            return False

def _extract_kline_sequence(raw):
    if isinstance(raw, list): return raw
    if isinstance(raw, dict): return raw.get("data") or raw.get("klines") or []
    return []

def _repair_truncated_json_array(content: str):
    start = content.find('[')
    end = content.rfind('}')
    if start == -1 or end == -1 or end <= start: return []
    trimmed = content[start:end + 1]
    if trimmed.count('{') == 0 or trimmed[-1] != '}': return []
    candidate = trimmed + ']'
    try:
        return _extract_kline_sequence(json.loads(candidate))
    except Exception:
        return []

def clean_kline_data(df: pd.DataFrame, is_raw_api_data: bool = False) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    try:
        df = df.copy()
        if 'timestamp' not in df.columns:
            return pd.DataFrame()

        # Handle timestamp conversion with proper format support for 'Z' suffix
        if df['timestamp'].dtype == object:
            # Check if timestamps have 'Z' suffix - use isoparse for proper UTC handling
            sample_ts = str(df['timestamp'].iloc[0]) if len(df) > 0 else ''
            if 'Z' in sample_ts or sample_ts.endswith('+00:00'):
                # Use isoparse for ISO format with Z timezone - preserves UTC correctly
                parsed_times = [isoparse(str(ts)) if pd.notna(ts) and str(ts).strip() else pd.NaT for ts in df['timestamp']]
                df['timestamp_dt'] = pd.to_datetime(parsed_times, utc=True, errors='coerce')
            else:
                # Fallback to flexible parsing
                df['timestamp_dt'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
        else:
            df['timestamp_dt'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True, errors='coerce')

        valid_mask = df['timestamp_dt'].notna()
        if not valid_mask.all():
            df = df[valid_mask]

        if not df.empty:
            # Filter out extreme years (e.g. 20202) that might have been parsed but are clearly wrong
            # current_year = datetime.now().year # Already have datetime available? Check imports.
            # Using 2000-2100 as safe range.
            df = df[(df['timestamp_dt'].dt.year >= 2000) & (df['timestamp_dt'].dt.year <= 2100)]

        if df.empty:
            return pd.DataFrame()

        # Ensure all numeric columns are properly converted to numeric types
        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_columns:
            if col in df.columns:
                # Force conversion to numeric, replacing any non-numeric values with NaN
                df[col] = pd.to_numeric(df[col], errors='coerce')
                # Fill NaN values with forward fill, then backward fill
                df[col] = df[col].ffill().bfill()

        critical_cols = ['open', 'high', 'low', 'close']
        available_critical = [col for col in critical_cols if col in df.columns]
        if available_critical:
            # After filling, check again for any remaining NaN values
            df = df.dropna(subset=available_critical)

        df = df.sort_values('timestamp_dt').reset_index(drop=True)
        return df

    except Exception as e:
        return pd.DataFrame()

def standardize_kline_data_for_json(df: pd.DataFrame) -> List[Dict]:
    if df is None or df.empty:
        return []
    try:
        df_clean = df.copy()
        if 'timestamp_dt' in df_clean.columns:
            df_clean['timestamp'] = df_clean['timestamp_dt'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        elif pd.api.types.is_datetime64_any_dtype(df_clean.get('timestamp')):
            df_clean['timestamp'] = df_clean['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        elif df_clean['timestamp'].dtype == object:
            df_clean['timestamp'] = df_clean['timestamp'].astype(str)
        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_columns:
            if col in df_clean.columns:
                df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce')
        df_clean = df_clean.dropna(subset=numeric_columns + ['timestamp'])
        return df_clean.to_dict('records')

    except Exception as e:
        logger.error(f"Error standardizing kline data for JSON: {e}")
        return []

async def _read_kline_file(symbol: str, interval: str, klines_cache_dir: str) -> pd.DataFrame:
    """
    Reads klines from 3 locations (Local, Gateway, Server).
    Repairs corruption. Deletes unrecoverable files.
    Returns the DataFrame from the NEWEST valid file found.
    """
    filename = f"{symbol}_{interval}.json"
    
    # 1. Define all scan paths
    base_binance = Path.home() / 'binance'
    candidate_dirs = [
        Path(klines_cache_dir),                 # Standard Local
        base_binance / 'klines_cache_gateway',  # Gateway
        base_binance / 'klines_cache_server'    # Server
    ]

    best_data = []
    best_mtime = 0.0
    files_checked = 0

    # 2. Iterate all folders
    for folder in candidate_dirs:
        file_path = folder / filename
        if not file_path.exists():
            continue
        
        files_checked += 1
        
        try:
            # Check modification time first
            mtime = file_path.stat().st_mtime
            
            # Read content
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()

            # Attempt Lenient Load
            data = _load_klines_json_lenient(content, str(file_path))

            if data:
                # If valid data found, check if it's the newest
                if mtime > best_mtime:
                    best_data = data
                    best_mtime = mtime
                # Or if it's significantly larger (more history)
                elif len(data) > len(best_data) + 50:
                    best_data = data
                    # Update mtime so we don't revert to a smaller file just because it's slightly newer
                    best_mtime = mtime 
            else:
                # 3. Auto-Deletion for Unrecoverable Garbage
                # If lenient load failed to extract even a single regex match, the file is trash.
                logger.error(f"🗑️ [_read_kline_file] Unrecoverable corruption in {file_path}. Deleting.")
                try: os.remove(file_path)
                except Exception: pass

        except Exception as e:
            logger.error(f"[_read_kline_file] IO Error reading {file_path}: {e}")

    # 4. Return Best Result
    if best_data:
        try:
            df = pd.DataFrame(best_data)
            # Assuming clean_kline_data is available in your utils scope
            # If not, ensure columns are numeric here
            if 'close' in df.columns:
                cols = ['open', 'high', 'low', 'close', 'volume']
                for c in cols:
                    if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
            
            # Use external cleaner if available, otherwise return raw DF
            if 'clean_kline_data' in globals():
                return clean_kline_data(df, is_raw_api_data=False)
            return df
        except Exception as e:
            logger.error(f"[_read_kline_file] DataFrame conversion failed: {e}")
            return pd.DataFrame()

    return pd.DataFrame()

def _load_klines_json_lenient(content: str, file_path_str: str) -> list:
    """
    Aggressive JSON recovery. 
    Prioritizes speed, then repair, then nuclear regex.
    """
    content = content.strip()
    if not content:
        return []

    # --- Strategy 1: Standard Load (Fastest) ---
    try:
        # Try orjson first if available (faster)
        if orjson:
            return orjson.loads(content)  # type: ignore # pylint: disable=no-member,c-extension-no-member
        return json.loads(content)
    except Exception as e:
        # Save error message for logging if needed
        err_msg = str(e)

    # --- Strategy 2: Fix "Double Bracket" ][ concatenation error ---
    if "][" in content:
        try:
            # This is the most common error from appending to files blindly
            fixed = content.replace("][", ",")
            data = json.loads(fixed)
            logger.info(f"🔧 [JSON_REPAIR] Fixed '][' concatenation in {file_path_str}")
            return data
        except Exception: pass

    # --- Strategy 3: Handle "Extra data" (valid JSON followed by garbage) ---
    if "Extra data" in err_msg:
        try:
            # Extract the json error position from the message if possible, 
            # OR just split by the first closing bracket of the list
            end_idx = content.rfind(']')
            if end_idx != -1:
                # Try taking everything up to the last bracket
                valid_part = content[:end_idx+1]
                data = json.loads(valid_part)
                logger.warning(f"🔧 [JSON_REPAIR] Recovered from 'Extra data' in {file_path_str}. Items: {len(data)}")
                return data
        except Exception: pass

    # --- Strategy 4: Handle Truncation (Missing closing bracket) ---
    # Look for the last valid object closure '}'
    last_brace_idx = content.rfind('}')
    if last_brace_idx != -1:
        try:
            # Construct a valid list string: [ ... } ]
            fixed_content = content[:last_brace_idx+1] + "]"
            data = json.loads(fixed_content)
            logger.warning(f"🔧 [JSON_REPAIR] Repaired truncated JSON. Salvaged {len(data)} items.")
            return data
        except Exception: pass

    # --- Strategy 5: Nuclear Regex Extraction (The "Without Whining" Option) ---
    # Extracts anything looking like a flat JSON object {...}
    try:
        # Matches non-nested dicts: { followed by anything not { or } followed by }
        # This is very robust for kline data which is a list of flat dicts
        matches = re.findall(r'\{[^{}]*\}', content)
        if matches:
            salvaged_data = []
            for m in matches:
                try:
                    obj = json.loads(m)
                    salvaged_data.append(obj)
                except Exception: continue
            
            if salvaged_data:
                logger.warning(f"☢️ [JSON_REPAIR] Regex salvage extracted {len(salvaged_data)} items from totally malformed file {file_path_str}.")
                return salvaged_data
    except Exception: pass

    return []

def _load_klines_from_file(symbol: str, interval: str) -> pd.DataFrame:
    """Synchronous version for asyncio.to_thread with standardized format support"""
    klines_dirs = _resolve_klines_directories()
    corrupted_files = []
    for kline_dir in klines_dirs:
        file_path = kline_dir / f"{symbol}_{interval}.json"
        if not file_path.is_file():
            continue
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            if not content:
                continue
            data = json.loads(content)
            if isinstance(data, list) and len(data) > 0:
                if isinstance(data[0], list):
                    klines_data = data[0]
                else:
                    klines_data = data
            elif isinstance(data, dict) and 'data' in data: 
                klines_data = data['data']
            else:
                klines_data = data
            df = pd.DataFrame(klines_data) if klines_data else pd.DataFrame()
            if 'timestamp' in df.columns and not df.empty and df['timestamp'].dtype == 'object':
                try:
                    df['timestamp'] = pd.to_datetime(
                        df['timestamp'], 
                        utc=True, 
                        format='%Y-%m-%dT%H:%M:%S.%fZ', 
                        errors='coerce'
                    )
                except Exception as e:
                    logger.debug(f"[_load_klines_from_file] Failed to parse standardized timestamps: {e}")
            
            return clean_kline_data(df, is_raw_api_data=False)
        except Exception as e:
            logger.error(f"[_load_klines_from_file] Failed to read {file_path}: {e}")
            corrupted_files.append(str(file_path))
            continue
    return pd.DataFrame()

# --- CORE REDIS CLIENT (With Fallback) ---
class ResilientRedisClient:
    """The base worker for Redis operations with local file fallback."""
    def __init__(self, host='127.0.0.1', port=6379, db=0, fallback_dir='/tmp/redis_fallback'):
        self.host = '127.0.0.1' if host in ['localhost', '::1'] else host
        self.port = port
        self.db = db
        self.fallback_dir = Path(fallback_dir)
        self.fallback_dir.mkdir(exist_ok=True, parents=True)
        self._client = None
        self._connected = False
        self._local_store = {}
        self._local_cache_file = self.fallback_dir / f"cache_{self.port}.json"
        self._load_local_store()

    def _load_local_store(self):
        try:
            if self._local_cache_file.exists():
                with open(self._local_cache_file, "r") as f:
                    self._local_store = json.load(f)
        except Exception: self._local_store = {}

    def _save_local_store(self):
        try:
            with open(self._local_cache_file, "w") as f:
                json.dump(self._local_store, f)
        except Exception: pass

    async def connect(self):
        try:
            self._client = redis.Redis(
                host=self.host, port=self.port, db=self.db, decode_responses=True,
                socket_connect_timeout=10, socket_timeout=10, health_check_interval=30
            )
            self._connected = False # Connection happens in background
            return True
        except Exception as e:
            logger.warning(f"Redis setup failed for {self.host}:{self.port}: {e}")
            return False

    async def get(self, key):
        try:
            if self._client:
                val = await asyncio.wait_for(self._client.get(key), timeout=1.0)
                if val is not None: return val
        except Exception: pass
        entry = self._local_store.get(key)
        if entry and (entry.get('exp', 0) == 0 or entry.get('exp', 0) > time.time()):
            return entry.get('val')
        return None

    async def set(self, key, value, ex=None):
        try:
            if self._client:
                await asyncio.wait_for(self._client.set(key, value, ex=ex), timeout=1.0)
        except Exception: pass
        self._local_store[key] = {'val': value, 'exp': time.time() + ex if ex else 0}
        self._save_local_store()
        return True

    def get_client(self): return self._client

class SimpleRedisManager:
    def __init__(self):
        self.connections = {"local": None, "gateway": None, "server": None}
        self._initialized = False
        self._initializing = False
        self.env = get_current_environment()["env"]
        self.data_coordinator = DataCoordinator(self.env)
        self._stop_event = asyncio.Event()

    async def initialize(self):
        if self._initialized or self._initializing: return
        self._initializing = True
        env_info = get_current_environment()
        for name, (host, port) in env_info['redis_connections'].items():
            try:
                target = '127.0.0.1' if host in ['localhost', '::1'] else host
                # Use instant timeouts as requested
                self.connections[name] = redis.Redis(
                    host=target, port=port, db=0, decode_responses=True,
                    socket_connect_timeout=1, socket_timeout=1, health_check_interval=30
                )
            except Exception as e:
                logger.warning(f"Redis link {name} creation failed: {e}")
        self._initializing = False
        self._initialized = True
        return self.connections

    def has_any_connection(self) -> bool:
        return any(c is not None for c in self.connections.values())

    async def wait_for_connections(self, timeout: float = 30.0) -> bool:
        """Blocks until at least one Redis node is pingable."""
        start = time.time()
        while time.time() - start < timeout:
            for name in ["local", "gateway", "server"]:
                conn = self.connections.get(name)
                if conn:
                    try:
                        await asyncio.wait_for(conn.ping(), timeout=0.1)
                        return True
                    except Exception: continue
            await asyncio.sleep(1)
        return False

    def get_available_connections(self) -> List[str]:
        return [target for target, client in self.connections.items() if client is not None]

    def get_broadcast_clients(self) -> List[redis.Redis]:
        return [c for c in self.connections.values() if c is not None]

    # --- DATA OPERATIONS ---
    async def get(self, key: str, target_preference: List[str] = None):
        prefs = target_preference or ["local", "gateway", "server"]
        for name in prefs:
            conn = self.connections.get(name)
            if conn:
                try:
                    val = await asyncio.wait_for(conn.get(key), timeout=0.1)
                    if val is not None:
                        try: return json.loads(val)
                        except Exception: return val
                except Exception: continue
        return None

    async def set(self, key: str, value: Any, ex: int = None, nx: bool = False):
        data = json.dumps(value, default=str) if not isinstance(value, (str, bytes)) else value
        success = False
        for conn in self.get_broadcast_clients():
            try:
                await asyncio.wait_for(conn.set(key, data, ex=ex, nx=nx), timeout=0.1)
                success = True
            except Exception: continue
        return success

    async def delete(self, *keys):
        for conn in self.get_broadcast_clients():
            try: await conn.delete(*keys)
            except Exception: pass

    async def rpush(self, key: str, value: Any):
        """Required by action loggers."""
        data = json.dumps(value, default=str)
        for conn in self.get_broadcast_clients():
            try: await conn.rpush(key, data)
            except Exception: pass

    # --- PUB/SUB METHODS ---
    async def publish(self, channel: str, message: Any, expiry_seconds: int = None, data_type: str = None):
        payload = message if isinstance(message, dict) else {"data": message}
        payload.update({
            "source": self.env, 
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data_type": data_type
        })
        body = json.dumps(payload, default=str)
        count = 0
        for conn in self.get_broadcast_clients():
            try:
                # 2026-05-08: bound conn.publish/conn.set so a stalled backend (e.g.
                # disabled server tunnel @ 6381 dummy) cannot hang the caller. Mirrors
                # the timeouts already in get()/set(). Was: unbounded → handle_reduction
                # → save_ladder_levels → _broadcast_ladder_levels_to_redis stalled
                # 105s+, tripping the 120s PAU watchdog (flz 3× in 30 min on 2026-05-08).
                await asyncio.wait_for(conn.publish(channel, body), timeout=0.1)
                if expiry_seconds:
                    await asyncio.wait_for(conn.set(channel, body, ex=expiry_seconds), timeout=0.1)
                count += 1
            except Exception: continue
        return count

    async def publish_to_all(self, channel, message, expiry_seconds=None):
        """Rum legacy alias."""
        return await self.publish(channel, message, expiry_seconds=expiry_seconds)

    async def publish_klines(self, symbol, interval, klines_data, expiry_seconds=3600, data_type=None):
        """Specialized kline broadcaster."""
        key = f"klines:{symbol}:{interval}"
        payload = {
            "symbol": symbol, "interval": interval, "klines": klines_data,
            "published_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": f"{self.env}_consolidated", "data_type": data_type
        }
        return await self.publish(key, payload, expiry_seconds=expiry_seconds)

    async def subscribe(self, channel, callback, data_type="generic", target=None):
        async def listener(name, conn):
            while not self._stop_event.is_set():
                try:
                    ps = conn.pubsub()
                    await ps.subscribe(channel)
                    while not self._stop_event.is_set():
                        try:
                            msg = await ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                            if msg:
                                data = json.loads(msg['data']) if isinstance(msg['data'], str) else msg['data']
                                await callback(data)
                        except Exception: await asyncio.sleep(0.1)
                except Exception:
                    await asyncio.sleep(5)  # Redis down — wait and retry, NEVER crash

        if target and self.connections.get(target):
            asyncio.create_task(listener(target, self.connections[target]))
        else:
            for n, c in self.connections.items():
                if c: asyncio.create_task(listener(n, c))

    async def _get_or_connect(self, name):
        """Rum legacy alias."""
        return self.connections.get(name)

    async def close(self):
        self._stop_event.set()
        for c in self.connections.values():
            if c: await c.aclose()

# --- GLOBAL SINGLETON & INSTANT ALIASES ---
# This ensures that no matter what your script imports, it gets the fully-featured manager.

simple_redis_manager = SimpleRedisManager()
rum_manager = simple_redis_manager
unified_redis_manager = simple_redis_manager

async def get_simple_redis_manager():
    if not simple_redis_manager._initialized:
        await simple_redis_manager.initialize()
    return simple_redis_manager

async def get_rum_manager(): return await get_simple_redis_manager()
async def get_redis_manager(): return await get_simple_redis_manager()
async def get_prices_redis_manager(): return await get_simple_redis_manager()
async def get_prices_local_redis():
    mgr = await get_simple_redis_manager()
    return mgr.connections.get('local')


# # pylint: disable=W,C,R,I
# import asyncio
# import atexit
# import contextvars
# import hashlib
# import json
# import logging
# import math
# import os
# import platform
# import pwd
# import random
# import re
# import socket
# import subprocess
# import sys
# import time
# from datetime import datetime, timedelta, timezone
# from io import StringIO
# from logging import Logger
# from logging.handlers import RotatingFileHandler
# from pathlib import Path
# from typing import (Any, Callable, Coroutine, Dict, List, Optional,
#                     ParamSpecArgs, Set, Tuple, Union)

# import aiofiles
# import aiofiles.os as aio_os
# import aiohttp
# import numpy as np
# import pandas as pd
# import psutil
# import redis.asyncio as redis
# from dateutil.parser import isoparse
# from dotenv import dotenv_values
# from redis import exceptions as redis_exceptions


# def safe_datetime(v: Any) -> datetime:
#     """
#     Standardizes input to a UTC datetime object.
#     Handles: float/int (timestamps), strings (ISO), and datetime objects.
#     """
#     if v is None:
#         return datetime.now(timezone.utc)
    
#     if isinstance(v, datetime):
#         if v.tzinfo is None:
#             return v.replace(tzinfo=timezone.utc)
#         return v
    
#     if isinstance(v, (int, float)):
#         # Handle ms timestamps vs seconds
#         if v > 1e11: v = v / 1000.0
#         return datetime.fromtimestamp(v, tz=timezone.utc)
        
#     if isinstance(v, str):
#         try:
#             dt = isoparse(v)
#             if dt.tzinfo is None:
#                 dt = dt.replace(tzinfo=timezone.utc)
#             return dt
#         except:
#             pass   
#     return datetime.now(timezone.utc)

# class ResilientRedisClient:
#     """Redis client with automatic reconnection and local file fallback"""
    
#     def __init__(self, name_or_host='localhost', config_or_port=6379, db=0, fallback_dir='/tmp/redis_fallback', **kwargs):
#         # Handle multiple usage patterns
#         if isinstance(name_or_host, str) and isinstance(config_or_port, dict):
#             # Old usage: ResilientRedisClient("local", cfg("localhost", 6379))
#             self.host = config_or_port.get('host', 'localhost')
#             self.port = config_or_port.get('port', 6379)
#             self.db = config_or_port.get('db', 0)
#         elif kwargs and 'host' in kwargs:
#             # Keyword usage: ResilientRedisClient(host='localhost', port=6379, db=0)
#             self.host = kwargs.get('host', 'localhost')
#             self.port = kwargs.get('port', 6379)
#             self.db = kwargs.get('db', 0)
#         else:
#             # Positional usage: ResilientRedisClient(host, port, db)
#             self.host = name_or_host
#             self.port = config_or_port
#             self.db = db
#         self.fallback_dir = Path(fallback_dir)
#         self.fallback_dir.mkdir(exist_ok=True)
#         self._client = None
#         self._connected = False
#         self._reconnect_task = None
#         self._local_locks = {}  # In-memory lock storage
#         self._lock_file = self.fallback_dir / 'locks.json'
#         self._load_local_locks()
#         self._local_cache_file = self.fallback_dir / "cache.json"
#         self._local_store = {}
#         self._load_local_store()
#         self._local_set={}
#         self._local_exists={}
#         self._local_get=()

        
#     def _load_local_locks(self):
#         """Load locks from local file"""
#         try:
#             if self._lock_file.exists():
#                 with open(self._lock_file, 'r') as f:
#                     data = json.load(f)
#                     now = time.time()
#                     self._local_locks = {k: v for k, v in data.items() if v.get('expires', 0) > now}
#                     self._save_local_locks()
#         except Exception as e:
#             logger.error(f"Failed to load local locks: {e}")
#             self._local_locks = {}
    
#     def _save_local_locks(self):
#         """Save locks to local file"""
#         try:
#             with open(self._lock_file, 'w') as f:
#                 json.dump(self._local_locks, f)
#         except Exception as e:
#             logger.error(f"Failed to save local locks: {e}")
    
#     def _load_local_store(self):
#         try:
#             if self._local_cache_file.exists():
#                 with open(self._local_cache_file, "r") as f:
#                     data = json.load(f)
#                     now = time.time()
#                     cleaned = {}
#                     for key, entry in data.items():
#                         expiry = entry.get("expires", 0)
#                         if expiry and expiry <= 0:
#                             expiry = 0
#                         if expiry and expiry < now:
#                             continue
#                         cleaned[key] = entry
#                     self._local_store = cleaned
#                     self._save_local_store()
#             else:
#                 self._local_store = {}
#         except Exception as e:
#             logger.error(f"Failed to load local fallback cache: {e}")
#             self._local_store = {}

#     def _save_local_store(self):
#         try:
#             with open(self._local_cache_file, "w") as f:
#                 json.dump(self._local_store, f)
#         except Exception as e:
#             logger.error(f"Failed to save local fallback cache: {e}")
    
#     async def connect(self):
#         """Establish Redis connection with retries and connection pooling - NON-BLOCKING"""
#         # Create client with long timeouts - don't wait for connection to succeed immediately
#         try:
#             # Use connection pooling to prevent "Too many connections" errors
#             # Fix IPv6 issue
#             host = self.host
#             if host in ['localhost', '::1']:
#                 host = '127.0.0.1'
#             # Determine if gateway (port 6380+) or local (6379) - gateway needs longer timeouts
#             is_gateway = self.port >= 6380
#             connect_timeout = 60 if is_gateway else 30
#             socket_timeout = 300 if is_gateway else 60
#             self._client = redis.Redis(host=host, port=self.port, db=self.db, decode_responses=True, socket_connect_timeout=connect_timeout, socket_timeout=socket_timeout, health_check_interval=30, max_connections=3000, retry_on_timeout=True,  retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError])
            
#             # NON-BLOCKING: Just create the client, no ping wait
#             # Connection happens in background, health_check_interval retries every 30s
#             self._connected = False
#             logger.info(f"⏳ Redis client created ({host}:{self.port}) - connecting in background")
#             return False
#         except Exception as e:
#             logger.warning(f"Redis connection setup failed: {e}")
#             self._connected = False
#             return False
    
#     async def _ensure_connection(self):
#         """Ensure Redis connection is alive - non-blocking with background reconnection"""
#         if not self._client:
#             # Start connection in background, don't wait
#             asyncio.create_task(self.connect())
#             return
#         if not self._connected:
#             # Connection in progress, check if ready (non-blocking)
#             try:
#                 await asyncio.wait_for(self._client.ping(), timeout=0.5)
#                 self._connected = True
#             except:
#                 # Still connecting or failed, start reconnect in background
#                 asyncio.create_task(self.connect())
#         else:
#             # Quick health check - don't block if it fails
#             try:
#                 await asyncio.wait_for(self._client.ping(), timeout=0.5)
#             except:
#                 # Connection lost, reconnect in background
#                 self._connected = False
#                 asyncio.create_task(self.connect())
    
#     async def set_lock(self, key: str, value: str = "1", ex: int = 300) -> bool:
#         """Set a lock with Redis fallback to local storage"""
#         try:
#             await self._ensure_connection()
#             if self._connected and self._client:
#                 result=await asyncio.wait_for(self._client.set(key,value,ex=ex,nx=True), timeout=2.0)
#                 return result is not None
#         except Exception as e:
#             logger.warning(f"Redis set_lock failed, using local fallback: {e}")
#         now=time.time()
#         expires=now+ex
#         if key not in self._local_locks or self._local_locks[key].get('expires',0)<=now:
#             self._local_locks[key]={'value':value,'expires':expires}
#             self._save_local_locks()
#             self._local_set(key,value,ex)
#             logger.info(f"Lock {key} set in local storage (expires in {ex}s)")
#             return True
#         return False
    
#     async def set(self,key,value,ex=None,nx=False):
#         try:
#             await self._ensure_connection()
#             if self._connected and self._client:
#                 if nx:
#                     result=await asyncio.wait_for(self._client.set(key,value,ex=ex,nx=True), timeout=2.0)
#                 else:
#                     result=await asyncio.wait_for(self._client.set(key,value,ex=ex), timeout=2.0)
#                 if result:
#                     return True
#         except Exception as e:
#             logger.warning(f"Redis set failed, using local fallback: {e}")
#         if nx and self._local_exists(key):
#             return False
#         return self._local_set(key,value,ex)

#     async def setex(self,key,ex,value):
#         return await self.set(key,value,ex=ex)

#     async def get(self,key):
#         try:
#             await self._ensure_connection()
#             if self._connected and self._client:
#                 data=await asyncio.wait_for(self._client.get(key), timeout=2.0)
#                 if data is not None:
#                     if isinstance(data,bytes):
#                         data=data.decode()
#                     self._local_set(key,data,ex=None)
#                     return data
#         except Exception as e:
#             logger.warning(f"Redis get failed, using local fallback: {e}")
#         return self._local_get(key)

#     async def delete(self, key: str) -> bool:
#         """Delete key with fallback"""
#         try:
#             await self._ensure_connection()
#             if self._connected and self._client:
#                 await asyncio.wait_for(self._client.delete(key), timeout=2.0)
#         except Exception as e:
#             logger.warning(f"Redis delete failed, using local fallback: {e}")
#         if self._local_delete(key):
#             return True
#         if key in self._local_locks:
#             del self._local_locks[key]
#             self._save_local_locks()
#             return True
#         return False
    
#     async def exists(self, key: str) -> bool:
#         """Check if key exists with fallback"""
#         try:
#             await self._ensure_connection()
#             if self._connected and self._client:
#                 return await asyncio.wait_for(self._client.exists(key), timeout=2.0) > 0
#         except Exception as e:
#             logger.warning(f"Redis exists failed, using local fallback: {e}")
#         if self._local_exists(key):
#             return True
#         if key in self._local_locks:
#             if self._local_locks[key].get('expires', 0) > time.time():
#                 return True
#             else:
#                 del self._local_locks[key]
#                 self._save_local_locks()
#         return False
    
#     async def ttl(self, key: str) -> int:
#         """Get TTL with fallback"""
#         try:
#             await self._ensure_connection()
#             if self._connected and self._client:
#                 ttl = await self._client.ttl(key)
#                 if ttl and ttl>0 and self._local_exists(key):
#                     self._local_set(key,self._local_get(key),ex=ttl)
#                 return ttl
#         except Exception as e:
#             logger.warning(f"Redis ttl failed, using local fallback: {e}")
#         if key in self._local_locks:
#             expires = self._local_locks[key].get('expires', 0)
#             ttl = int(expires - time.time())
#             if ttl <= 0:
#                 self._local_locks.pop(key, None)
#                 self._save_local_locks()
#                 return -2
#             return ttl
#         return self._local_ttl(key)

#     async def keys(self,pattern="*"):
#         results=set()
#         try:
#             await self._ensure_connection()
#             if self._connected and self._client:
#                 redis_keys=await self._client.keys(pattern)
#                 results.update(redis_keys)
#         except Exception as e:
#             logger.warning(f"Redis keys failed, using local fallback: {e}")
#         results.update(self._local_keys(pattern))
#         return list(results)
    
#     async def scan_iter(self,match="*"):
#         """Iterate keys matching pattern with local fallback."""
#         yielded=set()
#         try:
#             await self._ensure_connection()
#             if self._connected and self._client:
#                 async for key in self._client.scan_iter(match=match):
#                     yielded.add(key if isinstance(key,str) else key.decode())
#                     yield key
#         except Exception as e:
#             logger.warning(f"Redis scan_iter failed, using local fallback: {e}")
#         for key in self._local_keys(match):
#             if key not in yielded:
#                 yielded.add(key)
#                 yield key
    
#     async def close(self):
#         """Close Redis connection"""
#         if self._client:
#             await self._client.aclose()
#             self._connected = False

#     async def publish(self,channel,message):
#         try:
#             await self._ensure_connection()
#             if self._connected and self._client:
#                 return await asyncio.wait_for(self._client.publish(channel,message), timeout=2.0)
#         except Exception as e:
#             logger.warning(f"Redis publish failed for {channel}: {e}")
#         logger.debug(f"Publish fallback: storing {channel} -> {message}")
#         return 0

#     def get_client(self):
#         """Get the underlying Redis client (for compatibility with old code)"""
#         return self._client if self._connected else None

#     def pubsub(self):
#         """Get a pubsub object for Redis pub/sub operations"""
#         try:
#             if self._connected and self._client:
#                 return self._client.pubsub()
#         except Exception as e:
#             logger.warning(f"Redis pubsub failed: {e}")
#         # Return a dummy pubsub object that doesn't crash
#         return DummyPubSub()

# class DummyPubSub:
#     """Dummy pubsub object for when Redis is not available"""
#     def __init__(self):
#         self.subscribed_channels = set()
    
#     async def subscribe(self, *channels):
#         """Dummy subscribe - just track channels"""
#         self.subscribed_channels.update(channels)
#         logger.debug(f"Dummy pubsub: subscribed to {channels}")
    
#     async def listen(self):
#         """Dummy listen - return empty async generator"""
#         while True:
#             await asyncio.sleep(1)  # Prevent busy waiting
#             yield None
    
#     async def get_message(self, ignore_subscribe_messages=False, timeout=None):
#         """Dummy get_message - return None"""
#         await asyncio.sleep(0.1)  # Small delay to prevent busy waiting
#         return None
    
#     async def close(self):
#         """Dummy close"""
#         self.subscribed_channels.clear()

#     def _local_set(self,key,value,ex=None):
#         """Store value in local cache honoring TTL semantics."""
#         now=time.time()
#         expires=int(now+ex) if ex else 0
#         self._local_store[key]={"value":value,"expires":expires}
#         self._save_local_store()
#         return True

#     def _local_get(self,key):
#         """Retrieve value from local cache if not expired."""
#         now=time.time()
#         entry=self._local_store.get(key)
#         if not entry:
#             return None
#         expiry=entry.get("expires",0)
#         if expiry and expiry<now:
#             self._local_store.pop(key,None)
#             self._save_local_store()
#             return None
#         return entry.get("value")

#     def _local_delete(self,key):
#         """Remove local cache entry."""
#         if key in self._local_store:
#             self._local_store.pop(key,None)
#             self._save_local_store()
#             return True
#         return False

#     def _local_exists(self,key):
#         """Check local cache existence respecting TTL."""
#         return self._local_get(key) is not None

#     def _local_ttl(self,key):
#         """Return TTL for local cache entry following Redis semantics."""
#         entry=self._local_store.get(key)
#         if not entry:
#             return -2
#         expiry=entry.get("expires",0)
#         if not expiry:
#             return -1
#         ttl=int(expiry-time.time())
#         if ttl<=0:
#             self._local_store.pop(key,None)
#             self._save_local_store()
#             return -2
#         return ttl

#     def _local_keys(self,pattern="*"):
#         """Return local keys matching pattern and clean expired entries."""
#         now=time.time()
#         results=[]
#         for key,entry in list(self._local_store.items()):
#             expiry=entry.get("expires",0)
#             if expiry and expiry<now:
#                 self._local_store.pop(key,None)
#                 continue
#             if fnmatch(key,pattern):
#                 results.append(key)
#         if results:
#             self._save_local_store()
#         return results

# # Global resilient Redis client instances
# _resilient_clients = {}

# async def get_resilient_redis(name='default', host='localhost', port=6379, db=0) -> ResilientRedisClient:
#     """Get or create a resilient Redis client"""
#     if name not in _resilient_clients:
#         client = ResilientRedisClient(host, port, db)
#         await client.connect()
#         _resilient_clients[name] = client
#     return _resilient_clients[name]

# def with_redis_fallback(func):
#     """Decorator to automatically handle Redis failures with fallback"""
#     @wraps(func)
#     async def wrapper(*args, **kwargs):
#         try:
#             return await func(*args, **kwargs)
#         except Exception as e:
#             logger.error(f"Redis operation failed in {func.__name__}: {e}")
#             # Return sensible defaults based on function name
#             if 'lock' in func.__name__:
#                 return False
#             elif 'get' in func.__name__:
#                 return None
#             elif 'exists' in func.__name__:
#                 return False
#             else:
#                 raise
#     return wrapper
# FILE_READ_SEMAPHORE = asyncio.Semaphore(10 if platform.system() == "Darwin" else 240)  # MacBook: 10 (prevent "too many open files"), Server/Gateway: 240
# try:
#     _sym_path=getattr(config,'SYMBOLS_FILE',None)
#     ALLOWED_SYMBOLS=set(json.loads(Path(_sym_path).read_text(encoding='utf-8'))) if _sym_path and Path(_sym_path).exists() else set()
# except Exception as e:
#     print(f"[ALLOWED_SYMBOLS] load failed: {e}")
#     ALLOWED_SYMBOLS=set()
# def is_allowed_symbol(symbol:str)->bool:return not ALLOWED_SYMBOLS or symbol in ALLOWED_SYMBOLS

# def verify_symbols_integrity():
#     """Verify symbols.json integrity - call this at startup"""
#     try:
#         symbols_path = Path(__file__).parent / "symbols.json"
#         if not symbols_path.exists():
#             print("❌ [SECURITY] symbols.json not found!")
#             return False

#         # Known good hash (update this when symbols.json is legitimately changed)
#         known_hash = "3c04ccf93b9411fbc9881b6a2942a0deb13e42b8d7f5f8e93f21d94bc1c567f2"

#         current_hash = hashlib.sha256(symbols_path.read_bytes()).hexdigest()
#         if current_hash != known_hash:
#             print(f"❌ [SECURITY] symbols.json has been modified! Hash mismatch.")
#             print(f"   Expected: {known_hash}")
#             print(f"   Current:  {current_hash}")
#             return False

#         # Quick validation of content
#         data = json.loads(symbols_path.read_text())
#         if not isinstance(data, list) or len(data) < 100:
#             print("❌ [SECURITY] symbols.json content validation failed!")
#             return False

#         return True
#     except Exception as e:
#         print(f"❌ [SECURITY] Error verifying symbols.json: {e}")
#         return False

# # async def safe_redis_operation(operation, *args, **kwargs):
# #     try:
# #         redis_manager = await get_redis_manager()
# #         if not redis_manager or not redis_manager._initialized:
# #             raise Exception("Redis manager not available")
# #         client = redis_manager.connections.get("local")
# #         if not client:
# #             for target, conn in redis_manager.connections.items():
# #                 if conn:
# #                     client = conn
# #                     break
# #         if not client:
# #             raise Exception("No Redis connections available")
# #         return await operation(client, *args, **kwargs)
# #     except Exception as e:
# #         logger.debug(f"Redis operation failed: {e}")
# #         raise

# async def init_redis(
#     redis_host: str = None,
#     redis_port: int = None,
#     redis_db: int = None,
#     redis_password: str = None,
#     use_resilient: bool = True,
# ):
#     """Initialize Redis connection using args, environment, or app defaults.

#     Priority order for configuration:
#       1) Explicit function args
#       2) Environment variables: REDIS_URL (preferred), REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD
#       3) App config defaults from config.Config (host, port, db)
    
#     Args:
#         use_resilient: If True, use ResilientRedisClient with fallback mechanisms
#     """
#     import os

#     import redis.asyncio as redis

#     # 1) Start with provided args
#     host = redis_host
#     port = redis_port
#     db = redis_db
#     password = redis_password

#     # 2) Environment variables
#     # Support REDIS_URL first if present (e.g., redis://host:port/db)
#     redis_url = os.environ.get("REDIS_URL")
#     env_host = os.environ.get("REDIS_HOST")
#     env_port = os.environ.get("REDIS_PORT")
#     env_db = os.environ.get("REDIS_DB")
#     env_password = os.environ.get("REDIS_PASSWORD")

#     # 3) App config defaults
#     try:
#         from config import Config as AppCfg
#         app_cfg = AppCfg()
#         cfg_host = getattr(app_cfg, "REDIS_HOST", None)
#         cfg_port = getattr(app_cfg, "REDIS_PORT", None)
#         cfg_db = getattr(app_cfg, "REDIS_DB", None)
#         cfg_password = getattr(app_cfg, "REDIS_PASSWORD", None)
#     except Exception:
#         cfg_host = None
#         cfg_port = None
#         cfg_db = None
#         cfg_password = None

#     # Resolve final settings
#     host = host or env_host or cfg_host or "localhost"
#     # Fix IPv6 issue - convert localhost/::1 to 127.0.0.1
#     if host in ['localhost', '::1']:
#         host = '127.0.0.1'
#     try:
#         port = int(port or (env_port if env_port is not None else cfg_port if cfg_port is not None else 6379))
#     except (TypeError, ValueError):
#         port = 6379
#     try:
#         db = int(db or (env_db if env_db is not None else cfg_db if cfg_db is not None else 0))
#     except (TypeError, ValueError):
#         db = 0
#     password = password or env_password or cfg_password or None

#     try:
#         if redis_url:
#             redis_client = redis.from_url(
#                 redis_url,
#                 decode_responses=True,
#                 socket_connect_timeout=60,
#                 socket_timeout=300,
#                 health_check_interval=30,
#                 max_connections=3000,
#                 retry_on_timeout=True,
#                 retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError]
#             )
#         else:
#             redis_client = redis.Redis(
#                 host=host,
#                 port=port,
#                 db=db,
#                 password=password,
#                 decode_responses=True,
#                 socket_connect_timeout=30,
#                 socket_timeout=60,
#                 health_check_interval=30,
#                 max_connections=3000,
#                 retry_on_timeout=True,
#                 retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError]
#             )
#         # NON-BLOCKING: Just create the client, no ping wait - connection happens in background
#         logger.info(f"⏳ Redis client created ({host}:{port}/db{db}) - connecting in background")
#         return redis_client
#     except Exception as e:
#         logger.error(f"[init_redis] => Error initializing Redis: {e}")
#         # If standard connection failed and use_resilient is True, try resilient client
#         if use_resilient:
#             logger.info("Falling back to resilient Redis client due to connection error...")
#             return await get_resilient_redis(name='default', host=host, port=port, db=db)
#         return None

# async def close_redis(redis_client):
#     """Close Redis connection."""
#     try:
#         if redis_client:
#             await redis_client.aclose()
#             logger.info("Redis connection closed.")
#     except Exception as e:
#         logger.error(f"Error closing Redis connection: {e}")

# async def periodic_redis_health_check(redis_client, interval: int = None):
#     check_interval = interval or 60
#     initial_wait = 30
    
#     await asyncio.sleep(initial_wait)  # Wait for initial setup
    
#     while True:
#         try:
#             if not redis_client:
#                 #logger.warning("⚠️ Redis connection lost, attempting reconnect...")
#                 redis_client = await init_redis()
#                 # if redis_client:
#                 #     logger.info("✅ Redis connection restored")
#                 # else:
#                 #     logger.error("❌ Failed to restore Redis connection")
#             else:
#                 # Test the connection with a ping
#                 try:
#                     await redis_client.ping()
#                     #logger.debug("✅ Redis connection healthy")
#                 except Exception as e:
#                     #logger.warning(f"⚠️ Redis ping failed: {e}")
#                     redis_client = None
#                     redis_client = await init_redis()
            
#             await asyncio.sleep(check_interval)
            
#         except Exception as e:
#             logger.error(f"Redis health check error: {e}")
#             await asyncio.sleep(check_interval)

# class RedisConnectionManager:
#     """Manages Redis connections with robust reconnection logic."""
    
#     def __init__(self):
#         self.connection_attempts = 0
#         self.last_connection_attempt = None
#         self.max_retries = 50
#         self.base_delay = 2
#         self.max_delay = 300  # 5 minutes
        
#     async def connect_with_retry(self, connection_func, *args, **kwargs):
#         """Connect with exponential backoff retry logic."""
#         import asyncio
#         import random
        
#         while self.connection_attempts < self.max_retries:
#             try:
#                 client = await connection_func(*args, **kwargs)
#                 if client:
#                     # Test the connection
#                     await client.ping()
#                     self.connection_attempts = 0
#                     return client
#             except Exception as e:
#                 self.connection_attempts += 1
#                 delay = min(self.base_delay * (2 ** (self.connection_attempts - 1)) + random.uniform(0, 1), 
#                            self.max_delay)
                
#                 logger.warning(f"Redis connection attempt {self.connection_attempts} failed: {e}. Retrying in {delay:.1f}s")
                
#                 if self.connection_attempts >= self.max_retries:
#                     logger.error(f"Max Redis connection retries ({self.max_retries}) exceeded")
#                     return None
                
#                 await asyncio.sleep(delay)
        
#         return None

# # Global connection manager
# redis_manager = RedisConnectionManager()

# async def initialize_redis_connections() -> Tuple[Optional[redis.Redis], Optional[redis.Redis], Optional[redis.Redis]]:
#     app_cfg = Config()
#     env = get_current_environment()["env"]
#     logger.info(f"Initializing Redis connections for '{env}' environment...")

#     local_client, gateway_client, server_client = None, None, None

#     # 1. Initialize Local Redis Client (for writing)
#     try:
#         local_host = app_cfg.REDIS_HOST
#         if local_host in ['localhost', '::1']:
#             local_host = '127.0.0.1'
#         local_client = redis.Redis(
#             host=local_host, port=app_cfg.REDIS_PORT, db=app_cfg.REDIS_DB,
#             decode_responses=True, socket_connect_timeout=30, socket_timeout=60,
#             health_check_interval=30, max_connections=3000, retry_on_timeout=True,
#             retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError]
#         )
#         logger.info(f"⏳ Local Redis client created ({local_host}:{app_cfg.REDIS_PORT}) - connecting in background")
#     except Exception as e:
#         logger.error(f"❌ Failed to connect to Local Redis: {e}")
#         local_client = None

#     # 2. Initialize Gateway Redis Client (for reading klines)
#     try:
#         env_info = get_current_environment()
#         gw_host, gw_port = env_info['redis_connections'].get('gateway', ('localhost', 6379))
        
#         gateway_client = redis.Redis(
#             host=gw_host, port=gw_port, db=0, decode_responses=True,
#             socket_connect_timeout=30, socket_timeout=60, health_check_interval=60,
#             max_connections=2000, retry_on_timeout=True
#         )
#         await gateway_client.ping()
#         logger.info(f"✅ Connected to Gateway Redis at {gw_host}:{gw_port}")
#     except Exception as e:
#         logger.error(f"❌ Failed to connect to Gateway Redis: {e}")
#         gateway_client = None

#     # 3. Initialize Server Redis Client (for backup broadcasting)
#     try:
#         # The init_server_redis function is good, let's use it.
#         # We'll just await it since it's an async function now.
#         server_client = await init_server_redis()
#         if server_client:
#             logger.info("⏳ Server Redis client created - connecting in background")
#     except Exception as e:
#         logger.error(f"❌ Failed to connect to Server Redis: {e}")
#         server_client = None
        
#     return local_client, gateway_client, server_client

# async def init_server_redis():
#     """
#     Initialize server Redis connection for backup data broadcasting. (Ensure it's async)
#     """
#     try:
#         import redis.asyncio as redis
#         env_info = get_current_environment()
#         host, port = env_info['redis_connections'].get('server', ('localhost', 6379))

#         if host in ['localhost', '::1']:
#             host = '127.0.0.1'
#         redis_client = redis.Redis(
#             host=host, port=port, db=0, decode_responses=True,
#             socket_connect_timeout=30, socket_timeout=60, health_check_interval=30,
#             max_connections=3000, retry_on_timeout=True,
#             retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError]
#         )
#         logger.info(f"⏳ Server Redis client created ({host}:{port}) - connecting in background")
#         return redis_client
        
#     except Exception as e:
#         logger.warning(f"⚠️ Redis server failed: {e}")
#         return None

# # 🔒 LOCKED REDIS CONFIGURATION (EXACT COPY FROM EZ_PRICES - DO NOT CHANGE)

# async def init_ez_prices_redis_config():
#     """
#     Initialize Redis clients using EXACT same configuration as ez_prices.
#     This is LOCKED and must not be changed.
#     """
#     try:
#         import redis.asyncio as redis

#         from config import Config
        
#         app_cfg = Config()
        
#         # Connect to local Redis for writing market data and locks (EXACT COPY FROM EZ_PRICES)
#         local_host = app_cfg.REDIS_HOST
#         if local_host in ['localhost', '::1']:
#             local_host = '127.0.0.1'
#         local_redis = redis.Redis(
#             host=local_host,
#             port=app_cfg.REDIS_PORT,
#             db=app_cfg.REDIS_DB,
#             decode_responses=True,
#             socket_connect_timeout=30,
#             socket_timeout=60,
#             health_check_interval=30,
#             max_connections=3000,
#             retry_on_timeout=True,
#             retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError]
#         )
#         logger.info(f"⏳ Local Redis client created ({local_host}:{app_cfg.REDIS_PORT}) - connecting in background")
        
#         # Connect to gateway Redis for reading klines data (EXACT COPY FROM EZ_PRICES)
#         try:
#             env_info = get_current_environment()
#             gw_host, gw_port = env_info['redis_connections'].get('gateway', ('localhost', 6379))
            
#             logger.info(f"🔒 LOCKED: Attempting gateway Redis connection to {gw_host}:{gw_port}")
            
#             if gw_host in ['localhost', '::1']:
#                 gw_host = '127.0.0.1'
#             gateway_redis = redis.Redis(
#                 host=gw_host,
#                 port=gw_port,
#                 db=app_cfg.REDIS_DB,
#                 decode_responses=True,
#                 socket_connect_timeout=60,
#                 socket_timeout=300,
#                 health_check_interval=30,
#                 max_connections=3000,
#                 retry_on_timeout=True,
#                 retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError]
#             )
#             logger.info(f"⏳ Gateway Redis client created ({gw_host}:{gw_port}) - connecting in background")
            
#             # Test gateway Redis connectivity (EXACT COPY FROM EZ_PRICES)
#             test_keys = {'klines:BTCUSDC:3m': 1, 'klines:BTCUSDC:15m': 1, 'klines:ETHUSDC:3m': 1}
#             exists_result = {}
#             for key, expected in test_keys.items():
#                 exists_result[key] = await asyncio.wait_for(gateway_redis.exists(key), timeout=2.0)
#             logger.info(f"🔒 LOCKED: [gateway diag] {gw_host}:{gw_port} exists={exists_result}")
            
#             return local_redis, gateway_redis
            
#         except Exception as e:
#             logger.warning(f"🔒 LOCKED: Could not connect to gateway Redis: {e}. Will only use local Redis.")
#             return local_redis, None
            
#     except Exception as e:
#         logger.error(f"🔒 LOCKED: Redis initialization failed: {e}")
#         return None, None

# # import asyncio
# # import json
# # import logging
# # import subprocess
# # import time
# # from datetime import datetime, timezone
# # from typing import Dict, List, Optional, Callable

# # import pandas as pd
# # import redis.asyncio as redis

# # # Configure logging
# # logger = logging.getLogger(__name__)

# # Cache for get_current_environment to avoid repeated socket operations (prevents "too many open files" on MacBook)
# _env_cache = None
# _hostname_cache = None
# import threading

# # class UnifiedRedisManager:
# #     def __init__(self):
# #         env = get_current_environment()["env"]
# #         self.environment = env
# #         self.connections = {"local": None, "gateway": None, "server": None}
# #         self._initialized = False
# #         self._initialization_task = None

# #     async def _ensure_initialization(self):
# #         """Ensure Redis connections are initialized."""
# #         if self._initialized:
# #             return
            
# #         if self._initialization_task is None:
# #             self._initialization_task = asyncio.create_task(self._initialize_connections())
        
# #         await self._initialization_task

# #     async def _initialize_connections(self):
# #         """Initialize Redis connections."""
# #         try:
# #             local_client, gateway_client, server_client = await initialize_redis_connections()
# #             self.connections["local"] = local_client
# #             self.connections["gateway"] = gateway_client  
# #             self.connections["server"] = server_client
# #             self._initialized = True
# #         except Exception as e:
# #             logger.error(f"Failed to initialize Redis connections: {e}")
# #             self._initialized = False

# #     async def _get_connection(self, target: str):
# #         """Get Redis connection for target."""
# #         if not self._initialized:
# #             await self._ensure_initialization()
# #         return self.connections.get(target)

# #     # ------------------------------------------------------------------ #
# #     # MISSING FUNCTIONS - ADDED BACK
# #     # ------------------------------------------------------------------ #
    
# #     async def start_health_checks(self):
# #         """Start minimal health checks - only when needed."""
# #         pass  # Disabled aggressive health checks
    
# #     def get_broadcast_clients(self) -> List[redis.Redis]:
# #         """Get all connected Redis clients for broadcasting."""
# #         return [client for client in self.connections.values() if client is not None]

# #     def has_any_connection(self) -> bool:
# #         """Check if any Redis connections are available."""
# #         return any(client is not None for client in self.connections.values())

# #     def get_available_connections(self) -> List[str]:
# #         """Get list of available connection targets."""
# #         return [target for target, client in self.connections.items() if client is not None]

# #     async def wait_for_connections(self, timeout: float = 30.0) -> bool:
# #         """Wait for at least one connection to be established."""
# #         start_time = asyncio.get_event_loop().time()
# #         while asyncio.get_event_loop().time() - start_time < timeout:
# #             if self.has_any_connection():
# #                 return True
# #             await asyncio.sleep(0.1)
# #         return False
    
# #     async def publish_to_all(self, channel: str, message: dict, expiry_seconds: Optional[int] = None):
# #         payload = message if isinstance(message, dict) else {"data": message}
# #         payload.update({"source": self.environment, "timestamp": datetime.now(timezone.utc).isoformat()})
# #         body = json.dumps(payload, default=str)
# #         success = 0
# #         for name in self.connections:
# #             client = await self._get_connection(name)
# #             if not client:
# #                 continue
# #             try:
# #                 await asyncio.wait_for(client.publish(channel, body), timeout=2)
# #                 if expiry_seconds:
# #                     with suppress(Exception):
# #                         await asyncio.wait_for(client.setex(channel, expiry_seconds, body), timeout=2)
# #                 success += 1
# #             except Exception as exc:
# #                 logger.warning(f"Redis publish failed {name}: {exc}")
# #         return success

# #     async def publish_klines(self, symbol: str, interval: str, klines_data: list, expiry_seconds: int = 3600, data_type=None):
# #         """
# #         Specialized method for publishing klines data with the expected format.
# #         """
# #         redis_key = f"klines:{symbol}:{interval}"
        
# #         payload = {
# #             "symbol": symbol,
# #             "interval": interval,
# #             "published_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
# #             "klines": klines_data,
# #             "count": len(klines_data),
# #             "last_timestamp": klines_data[-1]['timestamp'] if klines_data else None,
# #             "source": f"{self.environment}_consolidated",
# #             "data_type": data_type,
# #         }
        
# #         return await self.publish_to_all(redis_key, payload, expiry_seconds)

# #     # ------------------------------------------------------------------ #
# #     # CONNECTION MANAGEMENT
# #     # ------------------------------------------------------------------ #
    
# #     async def initialize(self):
# #         if self._initialized:return self.connections
# #         import redis.asyncio as redis
# #         env_info=get_current_environment()
# #         env=env_info['env']
# #         redis_connections=env_info['redis_connections']
# #         logger.info(f"🔄 Initializing unified Redis for {env}")
        
# #         # Connect to all Redis connections for this environment
# #         for name, (host, port) in redis_connections.items():
# #             try:
# #                 connect_timeout = 60 if name == 'gateway' else 30
# #                 socket_timeout = 300 if name == 'gateway' else 60
# #                 self.connections[name]=redis.Redis(host=host,port=port,db=0,decode_responses=True,
# #                                                    socket_connect_timeout=connect_timeout,socket_timeout=socket_timeout,
# #                                                    health_check_interval=30,max_connections=2000,
# #                                                    retry_on_timeout=True,
# #                                                    retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError, ConnectionResetError, OSError],
# #                                                    single_connection_client=False)
# #                 # NON-BLOCKING: No ping wait - connection happens in background
# #                 logger.info(f"⏳ Redis {name} client created for {host}:{port} - connecting in background")
# #             except Exception as e:
# #                 logger.warning(f"⚠️ {name.title()} Redis failed: {e}")
# #                 self.connections[name]=None
        
# #         ready=[n for n,c in self.connections.items() if c]
# #         if ready:
# #             logger.info(f"✅ Redis ready on {ready}")
# #         else:
# #             logger.warning("⚠️ No Redis connections available - system will use file fallbacks")
# #         self._initialized=True
# #         return self.connections

# #     # ------------------------------------------------------------------ #
# #     # PUBLISH/SUBSC RIBE
# #     # ------------------------------------------------------------------ #
    
# #     async def publish(self, channel, message, expiry_seconds=None, data_type=None):
# #         """Publish reliably to all connected Redis nodes."""
# #         if not isinstance(message, dict):
# #             message = {"data": message}
# #         message.update({
# #             "source": self.environment,
# #             "timestamp": datetime.now(timezone.utc).isoformat(),
# #             "expiry_seconds": expiry_seconds,
# #             "data_type": data_type,
# #         })
# #         msg_json = json.dumps(message, default=str)
# #         success = 0
# #         for target, client in list(self.connections.items()):
# #             if not client:
# #                 continue
# #             try:
# #                 await client.publish(channel, msg_json)
# #                 if expiry_seconds:
# #                     try:
# #                         await asyncio.wait_for(client.setex(channel, expiry_seconds, msg_json), timeout=2)
# #                         logger.info(f"✅ Set Redis key {channel} with {expiry_seconds}s expiry on {target}")
# #                     except Exception as setex_error:
# #                         logger.warning(f"⚠️ Failed to setex {channel} on {target}: {setex_error}")
# #                 success += 1
# #             except Exception as e:
# #                 logger.warning(f"⚠️ publish to {target} failed: {e}")
# #         return success

# #     async def subscribe(self, channel, callback, data_type="generic", target=None):
# #         """Stable subscriber that connects once and stays connected."""
# #         async def stable_listener(target_name, client):
# #             try:
# #                 pubsub = client.pubsub()
# #                 await pubsub.subscribe(channel)
# #                 logger.info(f"👂 [{target_name}] subscribed to {channel}")
# #                 async for msg in pubsub.listen():
# #                     if msg["type"] != "message":
# #                         continue
# #                     try:
# #                         data = json.loads(msg["data"])
# #                         await callback(data)
# #                     except Exception as e:
# #                         logger.error(f"Message error on {target_name}: {e}")
# #             except Exception as e:
# #                 logger.error(f"🔌 [{target_name}] subscriber failed: {e}")
        
# #         if target:
# #             client = self.connections.get(target)
# #             if client:
# #                 asyncio.create_task(stable_listener(target, client))
# #             else:
# #                 logger.warning(f"Requested subscribe target '{target}' not found")
# #         else:
# #             for target_name, client in list(self.connections.items()):
# #                 if client:
# #                     asyncio.create_task(stable_listener(target_name, client))
                
# #     async def get(self, key: str, target_preference: List[str] = None):
# #         """Get value from Redis - resilient with automatic fallback."""
# #         if target_preference is None:
# #             target_preference = list(self.connections.keys())
# #         for target in target_preference:
# #             client = self.connections.get(target)
# #             if not client:
# #                 continue
# #             try:
# #                 value = await asyncio.wait_for(client.get(key), timeout=2.0)
# #                 if value:
# #                     try:
# #                         return json.loads(value)
# #                     except json.JSONDecodeError:  # pylint: disable=no-member
# #                         return value
# #             except Exception as e:
# #                 logger.debug(f"get from {target} failed: {e}")
# #                 # Don't mark connection as dead - let pool handle reconnection
# #                 continue
# #         return None
    
# #     async def set(self, key: str, value: Any, ex: int = None):
# #         """Set value in Redis - broadcast to all available connections."""
# #         if not isinstance(value, str):
# #             value = json.dumps(value, default=str)
# #         success_count = 0
# #         for target, client in list(self.connections.items()):
# #             if not client:
# #                 continue
# #             try:
# #                 if ex:
# #                     await asyncio.wait_for(client.setex(key, ex, value), timeout=2.0)
# #                 else:
# #                     await asyncio.wait_for(client.set(key, value), timeout=2.0)
# #                 success_count += 1
# #             except Exception as e:
# #                 logger.debug(f"set on {target} failed: {e}")
# #         return success_count > 0

# #     async def _get_connection_with_health_check(self, target: str) -> Optional[redis.Redis]:
# #         client = self.connections.get(target)
# #         if client:
# #             try:
# #                 await asyncio.wait_for(client.ping(), timeout=5.0)
# #                 return client
# #             except Exception:
# #                 self.connections[target] = None
# #         # Return None if no fresh connection available
# #         return None
    
# #     async def delete(self, *keys):
# #         """Delete keys from Redis - broadcast to all connections."""
# #         success_count = 0
# #         for target in list(self.connections.keys()):
# #             client = await self._get_connection(target)
# #             if not client:
# #                 continue
# #             try:
# #                 await asyncio.wait_for(client.delete(*keys), timeout=2.0)
# #                 success_count += 1
# #             except Exception as e:
# #                 logger.debug(f"delete on {target} failed: {e}")
# #         return success_count > 0
    
# #     async def exists(self, key: str):
# #         """Check if key exists in any Redis instance."""
# #         for target, client in list(self.connections.items()):
# #             if not client:
# #                 continue
# #             try:
# #                 result = await asyncio.wait_for(client.exists(key), timeout=1.0)
# #                 if result:
# #                     return True
# #             except Exception as e:
# #                 logger.debug(f"exists on {target} failed: {e}")
# #         return False
    
# #     async def keys(self, pattern: str = "*"):
# #         """Get keys matching pattern from all Redis instances."""
# #         all_keys = set()
# #         for target, client in list(self.connections.items()):
# #             if not client:
# #                 continue
# #             try:
# #                 keys = await asyncio.wait_for(client.keys(pattern), timeout=3.0)
# #                 all_keys.update(keys)
# #             except Exception as e:
# #                 logger.debug(f"keys on {target} failed: {e}")
# #         return list(all_keys)

# #     # ------------------------------------------------------------------ #
# #     # CLEANUP
# #     # ------------------------------------------------------------------ #
    
# #     async def close(self):
# #         """Graceful shutdown."""
# #         self._stop_event.set()
# #         # Close connections
# #         for client in self.connections.values():
# #             if client:
# #                 try:
# #                     await client.close()
# #                 except Exception:
# #                     pass
# #         # Close pools
# #         for pool in self.pools.values():
# #             try:
# #                 await pool.disconnect()
# #             except Exception:
# #                 pass
# #         logger.info("🧹 Redis manager shutdown complete.")

# # # Global instance
# # unified_redis_manager = UnifiedRedisManager()

# async def get_redis_manager():
#     global unified_redis_manager
#     if unified_redis_manager._initialized:
#         return unified_redis_manager
#     if getattr(unified_redis_manager, "_initializing", False):
#         while not unified_redis_manager._initialized and getattr(unified_redis_manager, "_initializing", False):
#             await asyncio.sleep(0.1)
#         if unified_redis_manager._initialized:
#             return unified_redis_manager
#     unified_redis_manager._initializing = True
#     try:
#         init_task = getattr(unified_redis_manager, "_initialization_task", None)
#         if init_task is None or init_task.done():
#             unified_redis_manager._initialization_task = init_task = asyncio.create_task(unified_redis_manager.initialize())
#         await init_task
#         unified_redis_manager._initialized = True
#         unified_redis_manager._initialization_task = None
#         return unified_redis_manager
#     except Exception:
#         unified_redis_manager._initialization_task = None
#         raise
#     finally:
#         unified_redis_manager._initializing = False

# class Rum:
#     def __init__(self):
#         self.environment = get_current_environment()["env"]
#         self.data_coordinator = DataCoordinator(self.environment)
#         self.clients = self._build_clients()
#         self.connections = {name: None for name in self.clients}
#         self._initialized = False
#         self._stop_event = asyncio.Event()

#     def _build_clients(self) -> Dict[str, ResilientRedisClient]:
#         env = self.environment
#         def cfg(host: str, port: int) -> dict:return {"host": host, "port": port, "db": 0}
#         clients: Dict[str, ResilientRedisClient] = {"local": ResilientRedisClient("local", cfg("localhost", 6379))}
#         if env == "macbook":
#             clients["server"] = ResilientRedisClient("server", cfg("157.180.125.52", 6381))
#             clients["gateway"] = ResilientRedisClient("gateway", cfg("157.90.168.35", 6380))
#         elif env == "gateway":
#             clients["server"] = ResilientRedisClient("server", cfg("10.0.0.3", 6379))
#         elif env == "server":
#             clients["gateway"] = ResilientRedisClient("gateway", cfg("10.0.0.2", 6379))
#         else:
#             clients["gateway"] = ResilientRedisClient("gateway", cfg("157.90.168.35", 6380))
#         return clients

#     async def initialize(self):
#         """Initialize connections - NON-BLOCKING (EXACT pattern from guide)"""
#         if self._initialized:return self.connections
#         logger.info(f"🔄 Initializing Rum for {self.environment}")
#         # Create clients but don't wait for connections (non-blocking per guide)
#         for name in self.connections:
#             if name in self.clients:
#                 resilient_client = self.clients[name]
#                 # Get the underlying Redis client (already created with non-blocking pattern)
#                 redis_client = resilient_client.get_client()
#                 if redis_client:
#                     # Connection exists but may not be ready - set it anyway (health_check_interval will retry)
#                     self.connections[name] = redis_client
#                     logger.info(f"⏳ {name} Redis client available - connecting in background")
#                 else:
#                     # Start connection in background, don't wait
#                     asyncio.create_task(resilient_client.connect())
#                     logger.info(f"⏳ {name} Redis client created - connecting in background")
#         ready=[name for name,conn in self.connections.items() if conn]
#         logger.info(f"✅ Rum initialized: {ready} (connections happening in background)")
#         self._initialized=True
#         return self.connections

#     def get_broadcast_clients(self)->List[redis.Redis]:return [c for c in self.connections.values() if c]
#     def has_any_connection(self)->bool:return any(self.connections.values())
#     def get_available_connections(self)->List[str]:return [name for name,conn in self.connections.items() if conn]

#     async def _get_or_connect(self,name:str)->Optional[redis.Redis]:
#         """Get Redis connection - returns None immediately if unavailable (EXACT pattern from guide)"""
#         client=self.connections.get(name)
#         if client:
#             # Quick non-blocking health check (max 0.5s as per guide)
#             try:
#                 await asyncio.wait_for(client.ping(), timeout=0.5)
#                 return client
#             except (redis_exceptions.ConnectionError, ConnectionError, OSError, ConnectionResetError, asyncio.TimeoutError):
#                 # Connection lost - remove and return None immediately (skip this source)
#                 self.connections[name]=None
#                 return None
#             except Exception:
#                 self.connections[name]=None
#                 return None
#         return None

#     async def publish_to_all(self,channel:str,message:dict,expiry_seconds:Optional[int]=None):
#         if not isinstance(message,dict):message={"data":message}
#         message.update({"source":self.environment,"published_at_utc":datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),"timestamp":datetime.now(timezone.utc).isoformat()})
#         msg=json.dumps(message,default=str)
#         sent=0
#         for name in list(self.connections.keys()):
#             client=await self._get_or_connect(name)
#             if not client:continue
#             try:
#                 await asyncio.wait_for(client.publish(channel,msg),timeout=2.0)
#                 if expiry_seconds:
#                     with suppress(Exception):await asyncio.wait_for(client.setex(channel,expiry_seconds,msg),timeout=2.0)
#                 sent+=1
#             except Exception as exc:logger.debug(f"Rum publish {name}:{channel} -> {exc}")
#         return sent

#     async def publish(self,channel:str,message:dict,expiry_seconds:Optional[int]=None,data_type:Optional[str]=None):
#         if not isinstance(message,dict):message={"data":message}
#         message.update({"source":self.environment,"timestamp":datetime.now(timezone.utc).isoformat(),"data_type":data_type})
#         msg=json.dumps(message,default=str)
#         sent=0
#         for name in list(self.connections.keys()):
#             client=await self._get_or_connect(name)
#             if not client:continue
#             try:
#                 await asyncio.wait_for(client.publish(channel,msg),timeout=2.0)
#                 if expiry_seconds:
#                     with suppress(Exception):await asyncio.wait_for(client.setex(channel,expiry_seconds,msg),timeout=2.0)
#                 sent+=1
#             except Exception as exc:logger.debug(f"Rum publish {name}:{channel} -> {exc}")
#         return sent

#     async def subscribe(self, channel: str, callback, data_type: str = "generic", target: str = None):
#         async def listener(name: str):
#             while not self._stop_event.is_set():
#                 client = await self._get_or_connect(name)
#                 if not client:
#                     await asyncio.sleep(1)
#                     continue
#                 try:
#                     pubsub = client.pubsub()
#                     await pubsub.subscribe(channel)
#                     logger.info(f"👂 [Rum:{name}] {channel} ({data_type})")
#                     async for msg in pubsub.listen():
#                         if msg.get("type") != "message": continue
#                         try: data = json.loads(msg["data"])
#                         except Exception: data = msg["data"]
#                         try: await callback(data)
#                         except Exception as exc: logger.error(f"Rum callback {name}:{channel} -> {exc}")
#                 except Exception as exc:
#                     logger.warning(f"Rum subscriber drop {name}:{channel} -> {exc}")
#                     await asyncio.sleep(2)
        
#         if target:
#             if target in self.connections:
#                 asyncio.create_task(listener(target))
#             else:
#                 logger.warning(f"Requested subscribe target '{target}' not found in Rum connections")
#         else:
#             for name in self.connections: 
#                 asyncio.create_task(listener(name))

#     async def get(self,key:str,target_preference:List[str]=None):
#         preference=target_preference or list(self.connections.keys())
#         for name in preference:
#             client=await self._get_or_connect(name)
#             if not client:continue
#             try:
#                 value=await asyncio.wait_for(client.get(key),timeout=2.0)
#                 if value is None:continue
#                 try:return json.loads(value)
#                 except json.JSONDecodeError:return value  # pylint: disable=no-member
#             except Exception as exc:logger.debug(f"Rum get {name}:{key} -> {exc}")
#         return None

#     async def set(self,key:str,value:Any,ex:int=None):
#         body=value if isinstance(value,str) else json.dumps(value,default=str)
#         success=False
#         for name in self.connections:
#             client=await self._get_or_connect(name)
#             if not client:continue
#             try:
#                 if ex:await asyncio.wait_for(client.setex(key,ex,body),timeout=2.0)
#                 else:await asyncio.wait_for(client.set(key,body),timeout=2.0)
#                 success=True
#             except Exception as exc:logger.debug(f"Rum set {name}:{key} -> {exc}")
#         return success

#     async def delete(self,*keys):
#         success=False
#         for name in self.connections:
#             client=await self._get_or_connect(name)
#             if not client:continue
#             try:
#                 await asyncio.wait_for(client.delete(*keys),timeout=2.0)
#                 success=True
#             except Exception as exc:logger.debug(f"Rum delete {name}:{keys} -> {exc}")
#         return success

#     async def close(self):
#         self._stop_event.set()
#         for client in self.connections.values():
#             if not client:continue
#             with suppress(Exception):await client.close()
#         self._initialized=False
#         logger.info("🧹 Rum shutdown complete.")

# rum_manager = Rum()

# # Global prices Redis manager variables
# _shared_prices_manager = None
# _shared_prices_local = None
# _shared_prices_gateway = None

# # Global lock Redis client to prevent connection exhaustion
# _shared_lock_redis_client = None

# async def get_rum_manager():
#     global rum_manager
#     if rum_manager._initialized:
#         return rum_manager
#     if getattr(rum_manager, "_initializing", False):
#         while not rum_manager._initialized and getattr(rum_manager, "_initializing", False):
#             await asyncio.sleep(0.1)
#         if rum_manager._initialized:
#             return rum_manager
#     rum_manager._initializing = True
#     try:
#         await rum_manager.initialize()
#         rum_manager._initialized = True
#         return rum_manager
#     finally:
#         rum_manager._initializing = False

# async def get_shared_lock_redis():
#     """Get or create a shared Redis client for locks to prevent connection exhaustion"""
#     global _shared_lock_redis_client
#     if _shared_lock_redis_client is None:
#         _shared_lock_redis_client = ResilientRedisClient(host='localhost', port=6379, db=0)
#         await _shared_lock_redis_client.connect()
#     return _shared_lock_redis_client

# def _resolve_gateway_host(env:str)->str:
#     if env=="macbook":return "157.90.168.35"
#     if env=="server":return "10.0.0.2"
#     if env=="gateway":return "localhost"
#     return "157.90.168.35"

# def _resolve_server_host(env:str)->str:
#     if env=="macbook":return "157.180.125.52"
#     if env=="gateway":return "10.0.0.3"
#     if env=="server":return "localhost"
#     return "157.180.125.52"

# async def ensure_prices_redis_manager():
#     global _shared_prices_manager,_shared_prices_local,_shared_prices_gateway
#     if _shared_prices_manager or _shared_prices_local or _shared_prices_gateway:
#         return SimpleNamespace(manager=_shared_prices_manager,local=_shared_prices_local,gateway=_shared_prices_gateway)
#     result=SimpleNamespace(manager=None,local=None,gateway=None)
#     rum=None
#     try:rum=await get_rum_manager()
#     except Exception as e:logger.warning(f"Rum manager unavailable: {e}")
#     if rum and getattr(rum,'has_any_connection',lambda:False)():
#         result.manager=rum
#     else:
#         legacy=None
#         logger.warning("Rum manager missing or offline, trying legacy Redis manager")
#         try:legacy=await get_redis_manager()
#         except Exception as e:logger.warning(f"Legacy Redis manager unavailable: {e}")
#         if legacy and getattr(legacy,'has_any_connection',lambda:False)():
#             result.manager=legacy
#     if result.manager:
#         available=result.manager.get_available_connections()
#         if 'local' in available:
#             try:result.local=await result.manager._get_or_connect('local')
#             except Exception as e:logger.warning(f"Rum local connect failed: {e}")
#         if 'gateway' in available:
#             try:result.gateway=await result.manager._get_or_connect('gateway')
#             except Exception as e:logger.warning(f"Rum gateway connect failed: {e}")
#     if not result.local:
#         try:
#             # Use 127.0.0.1 explicitly to avoid IPv6 issues
#             host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
#             # LONG timeout - Redis can take time to appear
#             result.local=redis.Redis(host=host,port=config.REDIS_PORT,db=config.REDIS_DB,decode_responses=True,
#                                      socket_connect_timeout=30,socket_timeout=60,max_connections=300,
#                                      health_check_interval=30,retry_on_timeout=True,
#                                      retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError])
#             # NON-BLOCKING: No ping wait, connects in background every 30s
#             logger.info(f"Local Redis client created ({host}:{config.REDIS_PORT}) - connecting in background. System continues with JSON files.")
#             result.local = None  # Don't use yet - will be available when connected
#         except Exception as e:logger.warning(f"Local Redis fallback failed: {e}");result.local=None
#     if not result.gateway:
#         try:
#             env_info=get_current_environment()
#             gw_host,gw_port=env_info['redis_connections'].get('gateway',('localhost',6379))
#             # Fix IPv6 issue - use 127.0.0.1 for localhost
#             if gw_host in ['localhost', '::1']:
#                 gw_host = '127.0.0.1'
#             # MUCH longer timeout for gateway - SSH tunnels can be slow
#             result.gateway=redis.Redis(host=gw_host,port=gw_port,db=config.REDIS_DB,decode_responses=True,
#                                        socket_connect_timeout=60,socket_timeout=300,max_connections=3000,
#                                        health_check_interval=30,retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError],
#                                        retry_on_timeout=True)
#             # NON-BLOCKING: No ping wait, connects in background every 30s
#             logger.info(f"Gateway Redis client created ({gw_host}:{gw_port}) - connecting in background. System continues with JSON files.")
#             result.gateway = None  # Don't use yet - will be available when connected
#         except Exception as e:logger.warning(f"Gateway Redis fallback failed: {e}");result.gateway=None
#     _shared_prices_manager=result.manager
#     _shared_prices_local=result.local
#     _shared_prices_gateway=result.gateway
#     return result

# async def get_prices_redis_manager():
#     result=await ensure_prices_redis_manager()
#     return result.manager

# async def get_prices_local_redis():
#     result=await ensure_prices_redis_manager()
#     return result.local

# async def get_prices_gateway_redis():
#     result=await ensure_prices_redis_manager()
#     return result.gateway

# environment = get_current_environment()["env"].lower()
# # Global SimpleRedisManager instance
# _simple_redis_manager = None

# async def get_simple_redis_manager():
#     """Get or create the SimpleRedisManager instance - IDEMPOTENT"""
#     global _simple_redis_manager
#     if _simple_redis_manager is None:
#         _simple_redis_manager = SimpleRedisManager()
#     if _simple_redis_manager._initialized:
#         return _simple_redis_manager
#     if getattr(_simple_redis_manager, "_initializing", False):
#         while not _simple_redis_manager._initialized and getattr(_simple_redis_manager, "_initializing", False):
#             await asyncio.sleep(0.1)
#         if _simple_redis_manager._initialized:
#             return _simple_redis_manager
#     _simple_redis_manager._initializing = True
#     try:
#         await _simple_redis_manager.initialize()
#         _simple_redis_manager._initialized = True
#         return _simple_redis_manager
#     finally:
#         _simple_redis_manager._initializing = False

# class SimpleRedisManager:
#     def __init__(self):
#         self.connections={"local":None,"gateway":None,"server":None}
#         self._initialized=False
#     async def initialize(self):
#         if self._initialized:return self.connections
#         import redis.asyncio as redis
#         env_info=get_current_environment()
#         env=env_info['env']
#         redis_connections=env_info['redis_connections']
#         logger.info(f"🔄 Initializing simple Redis for {env}")
        
#         # Connect to all Redis connections for this environment
#         for name, (host, port) in redis_connections.items():
#             try:
#                 # Fix IPv6 issue - use 127.0.0.1 for localhost
#                 if host in ['localhost', '::1']:
#                     host = '127.0.0.1'
#                 # Use MUCH longer timeouts for connections - Redis has time to appear
#                 # Gateway needs extra time due to SSH tunnels
#                 connect_timeout = 60 if name == 'gateway' else 30
#                 socket_timeout = 300 if name == 'gateway' else 60
                
#                 # Create client with long timeouts - NO BLOCKING WAIT
#                 # auto_close_connection_pool=False prevents premature pool closure
#                 self.connections[name]=redis.Redis(host=host,port=port,db=0,decode_responses=True, socket_connect_timeout=connect_timeout,
#                                                    socket_timeout=socket_timeout,
#                                                    health_check_interval=30,
#                                                    max_connections=50,
#                                                    retry_on_timeout=True,
#                                                    retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError, ConnectionResetError, OSError],
#                                                    single_connection_client=False)
                
#                 # NON-BLOCKING: Just create the client, continue immediately
#                 # Connection happens in background, will retry every 30s (health_check_interval)
#                 logger.info(f"⏳ Redis {name} client created for {host}:{port} - connecting in background. System continues with JSON files.")
#             except Exception as e:
#                 logger.warning(f"⚠️ {name.title()} Redis client creation failed: {e}")
#                 self.connections[name]=None
        
#         ready=[n for n,c in self.connections.items() if c]
#         if ready:
#             logger.info(f"✅ Redis ready on {ready}")
#         else:
#             logger.warning("⚠️ No Redis connections available - system will use file fallbacks")
#         self._initialized=True
#         return self.connections
#     def get_broadcast_clients(self):return [c for c in self.connections.values() if c]
#     def has_any_connection(self):return any(self.connections.values())
#     def get_available_connections(self):return [name for name,conn in self.connections.items() if conn]
#     async def _get_or_connect(self,name:str):
#         client=self.connections.get(name)
#         if client:
#             try:
#                 await asyncio.wait_for(client.ping(),timeout=1.0)
#                 return client
#             except (redis_exceptions.ConnectionError, ConnectionResetError, OSError, asyncio.TimeoutError) as e:
#                 # Connection failed, will try to reconnect below
#                 logger.debug(f"Redis {name} ping failed: {type(e).__name__}")
#                 self.connections[name] = None
#             except Exception as e:
#                 logger.debug(f"Redis {name} ping unexpected error: {type(e).__name__}: {e}")
#                 self.connections[name] = None
#         try:
#             import redis.asyncio as redis
#             env_info=get_current_environment()
#             redis_connections=env_info['redis_connections']
#             if name in redis_connections:
#                 host,port=redis_connections[name]
#                 # Fix IPv6 issue - use 127.0.0.1 for localhost
#                 if host in ['localhost', '::1']:
#                     host = '127.0.0.1'
#                 # Use MUCH longer timeouts for gateway connections (SSH tunnel)
#                 connect_timeout = 60 if name == 'gateway' else 30
#                 socket_timeout = 300 if name == 'gateway' else 60
                
#                 from redis import exceptions as redis_ex
#                 fresh=redis.Redis(host=host,port=port,db=0,decode_responses=True, socket_connect_timeout=connect_timeout,  socket_timeout=socket_timeout,
#                                   health_check_interval=30,
#                                   max_connections=2000,
#                                   retry_on_timeout=True,
#                                   retry_on_error=[redis_ex.TimeoutError, redis_ex.ConnectionError, ConnectionResetError, OSError],
#                                   single_connection_client=False)
                
#                 self.connections[name]=fresh
#                 return fresh
#         except Exception:
#             pass
#         return None
#     async def publish(self,channel,message,expiry_seconds=None,data_type=None):
#         """Publish to all available Redis connections"""
#         import json
#         if isinstance(message,dict):msg=json.dumps(message,default=str)
#         else:msg=str(message)
#         success=0
#         for name,client in self.connections.items():
#             if not client:
#                 continue
#             try:
#                 # NON-BLOCKING: Add timeout to publish
#                 await asyncio.wait_for(client.publish(channel,msg), timeout=2.0)
#                 if expiry_seconds:
#                     try:
#                         await asyncio.wait_for(client.setex(channel, expiry_seconds, msg), timeout=2.0)
#                         logger.info(f"✅ SimpleRedisManager: Set Redis key {channel} with {expiry_seconds}s expiry on {name}")
#                     except Exception as setex_error:
#                         logger.debug(f"⚠️ SimpleRedisManager: Failed to setex {channel} on {name}: {setex_error}")
#                 success+=1
#             except (redis_exceptions.ConnectionError, ConnectionResetError, OSError, asyncio.TimeoutError) as e:
#                 logger.debug(f"Redis publish connection error on {name}: {type(e).__name__} - will retry next time")
#                 self.connections[name] = None  # Mark as disconnected for reconnect
#             except Exception as e:
#                 logger.debug(f"Redis publish error on {name}: {type(e).__name__}: {e}")
#         if success==0:
#             try:
#                 fallback=await get_resilient_redis(name='local_fallback',host='127.0.0.1',port=6379,db=0)
#                 await fallback.set(f"fallback:{channel}:{time.time()}",msg,ex=expiry_seconds or 60)
#                 logger.info(f"Fallback cached publish for {channel}")
#             except Exception as e:
#                 logger.warning(f"Failed to cache fallback publish for {channel}: {e}")
#         return success

#     async def subscribe(self, channel, callback, data_type="generic", target=None):
#         """Resilient subscriber that retries on failure and stays connected."""
#         async def resilient_listener(target_name, initial_client):
#             retry_delay = 5
#             consecutive_failures = 0
#             while True:
#                 try:
#                     # Get or reconnect client
#                     client = await self._get_or_connect(target_name)
#                     if not client:
#                         consecutive_failures += 1
#                         if consecutive_failures % 3 == 0:  # Only log every 3rd failure
#                             logger.debug(f"🔌 [{target_name}] Redis not ready for {channel}, retrying in {retry_delay}s")
#                         await asyncio.sleep(retry_delay)
#                         continue
#                     consecutive_failures = 0
#                     # Verify connection with ping
#                     try:
#                         await asyncio.wait_for(client.ping(), timeout=2.0)
#                     except Exception:
#                         await asyncio.sleep(1)
#                         continue
#                     pubsub = client.pubsub()
#                     await asyncio.wait_for(pubsub.subscribe(channel), timeout=3.0)
#                     logger.info(f"👂 [{target_name}] subscribed to {channel} - listening for updates")
#                     # Use get_message with timeout instead of listen() to prevent blocking
#                     while True:
#                         try:
#                             msg = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=1.0)
#                             if msg and msg["type"] == "message":
#                                 try:
#                                     data = msg["data"]
#                                     if isinstance(data, bytes):
#                                         data = data.decode('utf-8')
#                                     if isinstance(data, str):
#                                         data = json.loads(data)
#                                     await callback(data)
#                                 except json.JSONDecodeError as e:  # pylint: disable=no-member
#                                     logger.debug(f"[{target_name}] JSON decode error on {channel}: {e}")
#                                 except Exception as e:
#                                     logger.error(f"[{target_name}] Callback error on {channel}: {e}")
#                         except asyncio.TimeoutError:
#                             continue
#                 except asyncio.CancelledError:
#                     logger.info(f"🔌 [{target_name}] subscriber cancelled for {channel}")
#                     break
#                 except Exception as e:
#                     consecutive_failures += 1
#                     if consecutive_failures % 3 == 0:  # Only log every 3rd failure
#                         logger.debug(f"🔌 [{target_name}] subscriber failed for {channel}: {type(e).__name__}, retrying in {retry_delay}s")
#                     await asyncio.sleep(retry_delay)
        
#         if target:
#             client = self.connections.get(target)
#             if client:
#                 asyncio.create_task(resilient_listener(target, client))
#             else:
#                 logger.warning(f"Requested subscribe target '{target}' not found in SimpleRedisManager connections")
#         else:
#             for target_name, client in list(self.connections.items()):
#                 if client:
#                     asyncio.create_task(resilient_listener(target_name, client))
   
#     async def _get_connection(self, target: str):#TEMP IN
#         """Get Redis connection for target."""
#         if not self._initialized:
#             await self._ensure_initialization()
#         return self.connections.get(target)     

#     async def rpush(self,key:str,value:Any):
#         """Push value to Redis list - broadcast to all available connections"""
#         import json
#         body=value if isinstance(value,str) else json.dumps(value,default=str)
#         success=0
#         for name,client in self.connections.items():
#             if not client:continue
#             try:
#                 await asyncio.wait_for(client.rpush(key,body),timeout=2.0)
#                 success+=1
#             except (redis_exceptions.ConnectionError, ConnectionResetError, OSError, asyncio.TimeoutError) as e:
#                 logger.debug(f"Redis rpush connection error on {name}: {type(e).__name__} - will retry next time")
#                 self.connections[name] = None
#             except Exception as e:
#                 logger.debug(f"Redis rpush error on {name}: {type(e).__name__}: {e}")
#         return success

#     async def publish_to_all(self, channel: str, message: dict, expiry_seconds: Optional[int] = None):
#         payload = message if isinstance(message, dict) else {"data": message}
#         payload.update({"source": environment, "timestamp": datetime.now(timezone.utc).isoformat()})
#         body = json.dumps(payload, default=str)
#         success = 0
#         for name in self.connections:
#             client = await self._get_connection(name)
#             if not client:
#                 continue
#             try:
#                 await asyncio.wait_for(client.publish(channel, body), timeout=2)
#                 if expiry_seconds:
#                     with suppress(Exception):
#                         await asyncio.wait_for(client.setex(channel, expiry_seconds, body), timeout=2)
#                 success += 1
#             except Exception as exc:
#                 logger.warning(f"Redis publish failed {name}: {exc}")
#         return success

#     async def publish_klines(self, symbol: str, interval: str, klines_data: list, expiry_seconds: int = 3600, data_type=None):
#         redis_key = f"klines:{symbol}:{interval}"
        
#         payload = {
#             "symbol": symbol,
#             "interval": interval,
#             "published_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
#             "klines": klines_data,
#             "count": len(klines_data),
#             "last_timestamp": klines_data[-1]['timestamp'] if klines_data else None,
#             "source": f"{environment}_consolidated",
#             "data_type": data_type,  }
        
#         return await self.publish_to_all(redis_key, payload, expiry_seconds)

#     def pipeline(self, target_preference: List[str] = None):
#         preference = target_preference or ['local', 'gateway', 'server']
#         for name in preference:
#             client = self.connections.get(name)
#             if client:
#                 try:
#                     return client.pipeline()
#                 except Exception as e:
#                     logger.debug(f"Failed to create pipeline on {name}: {e}")
#         raise RuntimeError("No Redis connection available for pipeline")

#     async def get(self,key:str,target_preference:List[str]=None):
#         preference=target_preference or list(self.connections.keys())
#         for name in preference:
#             client=await self._get_or_connect(name)
#             if not client:continue
#             try:
#                 value=await asyncio.wait_for(client.get(key),timeout=2.0)
#                 if value is None:continue
#                 try:return json.loads(value)
#                 except json.JSONDecodeError:return value  # pylint: disable=no-member
#             except Exception as exc:logger.debug(f"Rum get {name}:{key} -> {exc}")
#         return None

#     async def set(self,key:str,value:Any,ex:int=None):
#         body=value if isinstance(value,str) else json.dumps(value,default=str)
#         success=False
#         for name in self.connections:
#             client=await self._get_or_connect(name)
#             if not client:continue
#             try:
#                 if ex:await asyncio.wait_for(client.setex(key,ex,body),timeout=2.0)
#                 else:await asyncio.wait_for(client.set(key,body),timeout=2.0)
#                 success=True
#             except Exception as exc:logger.debug(f"Rum set {name}:{key} -> {exc}")
#         return success

#     async def delete(self,*keys):
#         success=False
#         for name in self.connections:
#             client=await self._get_or_connect(name)
#             if not client:continue
#             try:
#                 await asyncio.wait_for(client.delete(*keys),timeout=2.0)
#                 success=True
#             except Exception as exc:logger.debug(f"Rum delete {name}:{keys} -> {exc}")
#         return success

#     async def close(self):
#         self._stop_event.set()
#         for client in self.connections.values():
#             if not client:continue
#             with suppress(Exception):await client.close()
#         self._initialized=False
#         logger.info("🧹 Rum shutdown complete.")


# ═══ SYMBOL PERFORMANCE (was ez_symbol_performance.py — merged here) ═══
_perf_config = Config()
# 2026-08-11 FIX: sandboxed V8 runs block /Users/niels/logs writes → fallback to /tmp without crashing
try:
    _perf_logger = setup_logger('ez_symbol_performance', str(_perf_config.LOG_DIR / 'ez_symbol_performance.log'), logging.INFO)
except Exception:
    try:
        import tempfile as _tmp
        _fallback = pathlib.Path(_tmp.gettempdir()) / 'ez_symbol_performance.log'
        _perf_logger = setup_logger('ez_symbol_performance', str(_fallback), logging.INFO)
    except Exception:
        _perf_logger = setup_logger('ez_symbol_performance', '/tmp/ez_symbol_performance.log', logging.INFO)
_PERF_BASE_PATH = Path(os.getenv('EZ_BASE_PATH', str(getattr(_perf_config, 'BASE_PATH', Path.home() / 'binance'))))
_PERF_DATA_DIR = _PERF_BASE_PATH / 'data'
_PERF_HISTORY_DIR = _PERF_DATA_DIR / 'history'
_PERF_FILE = _PERF_DATA_DIR / 'symbol_performance.json'
_PERF_OUTLIER_FILE = _PERF_DATA_DIR / 'outlier_alerts.json'
_perf_cache: Dict[str, dict] = {}
_perf_cache_ts: float = 0.0
_PERF_ENTRY_TYPES = frozenset({'AUGMENT', 'OPEN', 'QUICK_OPEN', 'REENTRY'})
_PERF_EXIT_TYPES = frozenset({'REDUCE', 'CLOSE', 'QUICK_CLOSE'})

def _perf_parse_history_file(filepath: Path, cutoff: datetime) -> list:
    records = []
    seen = set()
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = rec.get('ts', '')
                rtype = rec.get('type', '')
                qty = rec.get('qty', 0.0)
                price = rec.get('price', 0.0)
                value = rec.get('value', 0.0)
                if not ts_str or not rtype:
                    continue
                dedup_key = (ts_str[:19], rtype, str(qty))
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                try:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except Exception:
                    continue
                if ts < cutoff:
                    continue
                records.append((ts, rtype, float(qty), float(price), float(value)))
    except Exception as e:
        _perf_logger.debug(f"Error parsing {filepath.name}: {e}")
    return records

def _perf_reconstruct_trades(records: list) -> list:
    if not records:
        return []
    records.sort(key=lambda r: r[0])
    trades = []
    entry_cost = 0.0
    entry_qty = 0.0
    entry_time = None
    for ts, rtype, qty, price, value in records:
        if rtype in _PERF_ENTRY_TYPES:
            if entry_time is None:
                entry_time = ts
            entry_cost += value if value > 0 else qty * price
            entry_qty += qty
        elif rtype in _PERF_EXIT_TYPES and entry_qty > 0:
            exit_price = price
            entry_avg = entry_cost / entry_qty if entry_qty > 0 else price
            hold_sec = (ts - entry_time).total_seconds() if entry_time else 0
            pnl_pct = (exit_price - entry_avg) / entry_avg * 100 if entry_avg > 0 else 0.0
            trades.append({'entry_avg': entry_avg, 'exit_avg': exit_price, 'qty': min(qty, entry_qty), 'pnl_pct': pnl_pct, 'hold_seconds': hold_sec})
            closed_qty = min(qty, entry_qty)
            entry_qty -= closed_qty
            if entry_qty > 0:
                entry_cost = entry_avg * entry_qty
            else:
                entry_cost = 0.0
                entry_qty = 0.0
                entry_time = None
    return trades

def _perf_get_outlier_penalty(symbol: Optional[str]) -> float:
    try:
        if _PERF_OUTLIER_FILE.exists():
            with open(_PERF_OUTLIER_FILE, 'r') as f:
                alerts = json.load(f)
            if symbol:
                for a in alerts.get('active', []):
                    if a.get('symbol') == symbol and a.get('severity') == 'CRITICAL':
                        return 0.8
    except Exception:
        pass
    return 1.0

def _perf_compute_multiplier(win_rate: float, avg_gain: float, n_trades: int) -> float:
    if n_trades < _perf_config.SYMBOL_PERF_MIN_TRADES:
        return 1.0
    win_score = (win_rate - 0.5) * 4.0
    gain_score = max(-2.0, min(2.0, avg_gain * 1.5))
    confidence = min(1.0, n_trades / 20.0)
    raw_score = (win_score + gain_score) * confidence
    if raw_score > 0:
        raw = 1.0 + raw_score * 4.5
    else:
        raw = max(0.1, 1.0 / (1.0 - raw_score * 2.0))
    outlier_penalty = _perf_get_outlier_penalty(None)
    raw *= outlier_penalty
    return max(_perf_config.SYMBOL_PERF_MIN_MULT, min(_perf_config.SYMBOL_PERF_MAX_MULT, raw))

def _perf_compute_tier(win_rate: float, avg_gain: float, n_trades: int) -> str:
    if n_trades < _perf_config.TIER_B_MIN_TRADES:
        return 'B'
    if win_rate >= _perf_config.TIER_A_WIN_RATE and avg_gain >= _perf_config.TIER_A_MIN_GAIN and n_trades >= _perf_config.TIER_A_MIN_TRADES:
        return 'A'
    if win_rate >= _perf_config.TIER_B_WIN_RATE or (avg_gain > 0 and n_trades >= _perf_config.TIER_B_MIN_TRADES):
        return 'B'
    return 'C'

def compute_all_stats(window_days: int = None) -> Dict[str, dict]:
    if window_days is None:
        window_days = _perf_config.SYMBOL_PERF_WINDOW_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    stats: Dict[str, dict] = {}
    if not _PERF_HISTORY_DIR.exists():
        _perf_logger.warning(f"[PERF] History dir not found: {_PERF_HISTORY_DIR}")
        return stats
    for acct_dir in _PERF_HISTORY_DIR.iterdir():
        if not acct_dir.is_dir():
            continue
        for jfile in acct_dir.glob('*.jsonl'):
            fname = jfile.stem
            if '_LONG' in fname:
                symbol = fname.replace('_LONG', '')
                side = 'LONG'
            elif '_SHORT' in fname:
                symbol = fname.replace('_SHORT', '')
                side = 'SHORT'
            else:
                continue
            records = _perf_parse_history_file(jfile, cutoff)
            if not records:
                continue
            trades = _perf_reconstruct_trades(records)
            if not trades:
                continue
            key = f"{symbol}_{side}"
            if key not in stats:
                stats[key] = {'symbol': symbol, 'side': side, 'trades': [], 'accounts': set()}
            stats[key]['trades'].extend(trades)
            stats[key]['accounts'].add(acct_dir.name)
    result = {}
    for key, data in stats.items():
        symbol = data['symbol']
        all_trades = data['trades']
        n_trades = len(all_trades)
        if n_trades == 0:
            continue
        wins = sum(1 for t in all_trades if t['pnl_pct'] > 0)
        win_rate = wins / n_trades
        avg_gain = sum(t['pnl_pct'] for t in all_trades) / n_trades
        avg_hold_sec = sum(t['hold_seconds'] for t in all_trades) / n_trades
        # FIX 2026-08-18 COPX_LONG -143%: sum(pnl_pct) can exceed -100% for LONG
        # (additive) while equity cannot. Use compounded gain and dollar gain;
        # keep sum as diagnostic only. See BACKTEST_BIBLE §2 avg-trade-deployed-2000-v1.
        total_pnl_pct_sum = sum(t['pnl_pct'] for t in all_trades)
        # compounded: product(1+p/100)-1, capped for LONG at -100%
        compounded = 1.0
        for t in all_trades:
            compounded *= (1.0 + float(t['pnl_pct']) / 100.0)
        total_pnl_pct_compounded = (compounded - 1.0) * 100.0
        # for LONG, floor at -100% equity; SHORT can exceed -100% not modelled here
        total_pnl_pct = max(-100.0, total_pnl_pct_compounded) if any(k.endswith('_LONG') for k in [key]) else total_pnl_pct_compounded
        # preserve diagnostic sum for audit
        total_pnl_pct_diagnostic_sum = total_pnl_pct_sum
        best_trade = max(t['pnl_pct'] for t in all_trades)
        worst_trade = min(t['pnl_pct'] for t in all_trades)
        if symbol not in result or result[symbol].get('trade_count', 0) < n_trades:
            multiplier = _perf_compute_multiplier(win_rate, avg_gain, n_trades)
            tier = _perf_compute_tier(win_rate, avg_gain, n_trades)
            now_iso = datetime.now(timezone.utc).isoformat()
            result[symbol] = {'symbol': symbol, 'trade_count': n_trades, 'win_rate': round(win_rate, 4), 'avg_gain_pct': round(avg_gain, 4), 'total_pnl_pct': round(total_pnl_pct, 2), 'total_pnl_pct_compounded': round(total_pnl_pct, 2), 'total_pnl_pct_sum_diagnostic': round(total_pnl_pct_diagnostic_sum, 2), 'best_trade_pct': round(best_trade, 2), 'worst_trade_pct': round(worst_trade, 2), 'avg_hold_hours': round(avg_hold_sec / 3600, 2), 'accounts': list(data['accounts']), 'raw_multiplier': round(multiplier, 4), 'order_multiplier': round(multiplier, 3), 'tier': tier, 'set_at': now_iso, 'updated_at': now_iso}
    return result

def refresh_cache() -> Dict[str, dict]:
    global _perf_cache, _perf_cache_ts
    if not _perf_config.SYMBOL_PERF_ENABLED:
        return {}
    try:
        stats = compute_all_stats()
        _perf_cache = stats
        _perf_cache_ts = time.time()
        try:
            with open(_PERF_FILE, 'w') as f:
                json.dump(stats, f, indent=1)
            outperformers = sum(1 for s in stats.values() if s.get('raw_multiplier', 1.0) > 2.0)
            underperformers = sum(1 for s in stats.values() if s.get('raw_multiplier', 1.0) < 0.5)
            _perf_logger.info(f"[PERF] Refreshed: {len(stats)} symbols, A={sum(1 for s in stats.values() if s['tier']=='A')}, B={sum(1 for s in stats.values() if s['tier']=='B')}, C={sum(1 for s in stats.values() if s['tier']=='C')} | outperf(>2x)={outperformers} underperf(<0.5x)={underperformers}")
        except Exception as e:
            _perf_logger.warning(f"[PERF] Failed to write {_PERF_FILE.name}: {e}")
        return stats
    except Exception as e:
        _perf_logger.error(f"[PERF] Refresh error: {e}", exc_info=True)
        return _perf_cache

def _ensure_cache() -> Dict[str, dict]:
    global _perf_cache, _perf_cache_ts
    if _perf_cache and (time.time() - _perf_cache_ts) < _perf_config.SYMBOL_PERF_REFRESH_SECONDS:
        return _perf_cache
    if _PERF_FILE.exists():
        try:
            with open(_PERF_FILE, 'r') as f:
                _perf_cache = json.load(f)
            _perf_cache_ts = time.time()
            return _perf_cache
        except Exception:
            pass
    return refresh_cache()

def _perf_apply_decay(raw_mult: float, set_at_str: str) -> float:
    try:
        set_at = datetime.fromisoformat(set_at_str.replace('Z', '+00:00'))
    except Exception:
        return 1.0
    elapsed_hours = (datetime.now(timezone.utc) - set_at).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return raw_mult
    half_life = getattr(_perf_config, 'SYMBOL_PERF_DECAY_HOURS', 60.0)
    decay = 0.5 ** (elapsed_hours / half_life)
    return 1.0 + (raw_mult - 1.0) * decay

def get_performance_multiplier(symbol: str, default: float = 1.0) -> float:
    if not _perf_config.SYMBOL_PERF_ENABLED:
        return default
    cache = _ensure_cache()
    entry = cache.get(symbol)
    if not entry:
        return default
    raw = entry.get('raw_multiplier', entry.get('order_multiplier', default))
    set_at = entry.get('set_at', entry.get('updated_at', ''))
    if not set_at:
        return raw
    return max(_perf_config.SYMBOL_PERF_MIN_MULT, min(_perf_config.SYMBOL_PERF_MAX_MULT, _perf_apply_decay(raw, set_at)))

def get_symbol_tier(symbol: str, default: str = 'B') -> str:
    if not _perf_config.TIER_ENABLED:
        return default
    cache = _ensure_cache()
    entry = cache.get(symbol)
    if not entry:
        return default
    return entry.get('tier', default)

def get_tier_multiplier(symbol: str) -> float:
    tier = get_symbol_tier(symbol)
    if tier == 'A':
        return _perf_config.TIER_A_MULTIPLIER
    if tier == 'C':
        return _perf_config.TIER_C_MULTIPLIER
    return 1.0

def get_full_stats(symbol: str) -> Optional[dict]:
    cache = _ensure_cache()
    return cache.get(symbol)

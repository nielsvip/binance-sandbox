# ── PositionsServiceClient (was service_clients.py — merged here) ────────────
import asyncio
import asyncio.subprocess as aio_subprocess
import fnmatch
import glob
import hashlib
import importlib
import json
import logging
import math
import os
import random
import re
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from asyncio import Semaphore
from collections import defaultdict, deque
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, getcontext
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    DefaultDict,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

import aiofiles
import aiofiles.os as aio_os
import aiohttp
import pandas as pd
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
from dateutil.parser import isoparse
from redis.asyncio import Redis
from requests.adapters import HTTPAdapter

from config import Config
from utils import get_current_environment

# 2026-05-01: Global Binance IP-ban tracker. The watchdog used to reset the user-data
# WS every 60s during a -1003 ban, which spiraled (each reset POSTs listenKey → another
# -1003 → IP ban window EXTENDED). On Mac IP rotation is disabled (single home IP), so
# the only sane response to -1003 is to STOP all REST traffic until the ban expires.
_BAN_RE = re.compile(r"banned until (\d+)")
_GLOBAL_BAN_UNTIL_MS = 0
_GLOBAL_BAN_LOCK = threading.Lock()
_GLOBAL_BAN_LAST_LOG_TS = 0.0


def _record_ip_ban_from_exc(exc) -> int:
    """If exc text matches Binance -1003 'banned until <ms>', update tracker. Returns ms."""
    global _GLOBAL_BAN_UNTIL_MS
    try:
        m = _BAN_RE.search(str(exc))
        if not m:
            return 0
        ms = int(m.group(1))
    except Exception:
        return 0
    with _GLOBAL_BAN_LOCK:
        if ms > _GLOBAL_BAN_UNTIL_MS:
            _GLOBAL_BAN_UNTIL_MS = ms
    return ms


def _ban_remaining_seconds() -> float:
    """Seconds until the global Binance IP ban clears (0 if not banned)."""
    with _GLOBAL_BAN_LOCK:
        until_ms = _GLOBAL_BAN_UNTIL_MS
    if until_ms <= 0:
        return 0.0
    rem = (until_ms / 1000.0) - time.time()
    return max(0.0, rem)


def _log_ban_skip_throttled(logger_obj, tag: str, action: str) -> None:
    """Log 'skipped <action> during ban' but throttle to once per 30s across whole process."""
    global _GLOBAL_BAN_LAST_LOG_TS
    now = time.time()
    if now - _GLOBAL_BAN_LAST_LOG_TS < 30.0:
        return
    _GLOBAL_BAN_LAST_LOG_TS = now
    rem = _ban_remaining_seconds()
    logger_obj.warning(f"[{tag}] 🚫 IP banned by Binance for ~{rem:.0f}s — {action}")


# 2026-05-05: Per-account auth-fail cooldown. -2015 (Invalid API-key/IP/permissions)
# means the outbound IP is not on Binance's whitelist for this key. Tearing down the
# user-data WS does NOT help — the immediate fresh-listen-key fetch hits the same -2015,
# and the existing user stream may still deliver events. Without this cooldown the
# watchdog churns every ~63s (probe → reset → reconnect with stale key → silent → repeat).
# When -2015 is observed on probe/keepalive, set a per-account cooldown during which the
# watchdog skips probing AND skips the reset, treating the silence as expected-until-fixed.
_AUTH_BAN_UNTIL: Dict[str, float] = {}
_AUTH_BAN_LAST_LOG_TS: Dict[str, float] = {}
_AUTH_BAN_LOCK = threading.Lock()
_AUTH_BAN_COOLDOWN_SEC = 300.0


def _record_auth_ban(account_key: str, exc) -> bool:
    """If exc indicates -2015 (invalid API-key/IP/permissions), record per-account cooldown.
    Returns True iff the exception is -2015 (caller should skip teardown)."""
    if "-2015" not in str(exc):
        return False
    with _AUTH_BAN_LOCK:
        _AUTH_BAN_UNTIL[account_key] = time.time() + _AUTH_BAN_COOLDOWN_SEC
    return True


def _auth_ban_remaining(account_key: str) -> float:
    with _AUTH_BAN_LOCK:
        until = _AUTH_BAN_UNTIL.get(account_key, 0.0)
    rem = until - time.time()
    return rem if rem > 0 else 0.0


def _log_auth_ban_throttled(logger_obj, account_key: str, action: str) -> None:
    """Log 'auth ban active' for an account, throttled to once per 60s per account."""
    now = time.time()
    with _AUTH_BAN_LOCK:
        last = _AUTH_BAN_LAST_LOG_TS.get(account_key, 0.0)
        if now - last < 60.0:
            return
        _AUTH_BAN_LAST_LOG_TS[account_key] = now
    rem = _auth_ban_remaining(account_key)
    logger_obj.warning(f"[{account_key}] 🔑 Binance API key -2015 (IP not whitelisted) cooldown ~{rem:.0f}s — {action}. FIX: whitelist current outbound IP on Binance API key settings.")


class PositionsServiceClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765, *, timeout: float = 5.0, connect_timeout: Optional[float] = None, long_timeout: Optional[float] = None, endpoints: Optional[Sequence[Union[str, Tuple[str, int]]]] = None, logger: Optional[logging.Logger] = None) -> None:
        self.timeout = max(timeout, 1.0)
        base_connect = max(self.timeout * 0.5, 1.5)
        self.connect_timeout = connect_timeout or min(base_connect, 5.0)
        self.logger = logger or logging.getLogger("positions_rpc_client")
        self._env = get_current_environment() or {}
        self._endpoints = self._normalize_endpoints(endpoints, host, port)
        self._preferred_idx = 0
        self._state_lock = asyncio.Lock()
        self._long_methods = {"get_snapshot", "get_latest_market_data", "manage_all_stops"}
        self._long_timeout = max(long_timeout or self.timeout * 3.0, self.timeout)
        if not self._endpoints:
            self._endpoints = [(host, port)]

    def _normalize_endpoints(self, endpoints: Optional[Sequence[Union[str, Tuple[str, int]]]], primary_host: str, primary_port: int) -> List[Tuple[str, int]]:
        raw: List[Tuple[str, int]] = []
        if endpoints:
            for entry in endpoints:
                if isinstance(entry, str):
                    host, port = (entry.split(":", 1) + [str(primary_port)])[:2]
                    raw.append((host.strip() or primary_host, int(port.strip() or primary_port)))
                else:
                    host, port = entry
                    raw.append((str(host).strip() or primary_host, int(port)))
        else:
            raw.extend(self._default_endpoints(primary_host, primary_port))
        env_hosts = os.environ.get("EZ_POSITIONS_RPC_HOSTS", "")
        if env_hosts:
            for entry in env_hosts.split(","):
                candidate = entry.strip()
                if not candidate:
                    continue
                host, port = (candidate.split(":", 1) + [str(primary_port)])[:2]
                raw.append((host.strip() or primary_host, int(port.strip() or primary_port)))
        unique: List[Tuple[str, int]] = []
        seen = set()
        for host, port in raw:
            key = f"{host}:{port}"
            if host and key not in seen:
                seen.add(key)
                unique.append((host, port))
        return unique

    def _default_endpoints(self, primary_host: str, primary_port: int) -> List[Tuple[str, int]]:
        env = self._env.get("env", "")
        baseline = [
            (primary_host, primary_port),
            ("127.0.0.1", primary_port),
            ("localhost", primary_port)
        ]
        if env == "macbook":
            baseline.extend([
                ("157.90.168.35", primary_port),
                ("157.180.125.52", primary_port)
            ])
        elif env == "gateway":
            baseline.extend([
                ("10.0.0.3", primary_port),
                ("157.180.125.52", primary_port)
            ])
        elif env == "server":
            baseline.extend([
                ("10.0.0.2", primary_port),
                ("157.90.168.35", primary_port)
            ])
        return baseline

    async def _set_preferred(self, idx: int) -> None:
        async with self._state_lock:
            self._preferred_idx = idx % len(self._endpoints)

    async def _advance_preferred(self, failed_idx: int) -> None:
        async with self._state_lock:
            if not self._endpoints:
                return
            if failed_idx == self._preferred_idx:
                self._preferred_idx = (failed_idx + 1) % len(self._endpoints)

    async def _open_connection(self, host: str, port: int) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.wait_for(asyncio.open_connection(host, port), timeout=self.connect_timeout)

    async def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if not self._endpoints:
            raise RuntimeError("positions_service RPC has no endpoints configured")
        last_exc: Optional[BaseException] = None
        failures: List[str] = []
        total = len(self._endpoints)
        for offset in range(total):
            idx = (self._preferred_idx + offset) % total
            host, port = self._endpoints[idx]
            reader: Optional[asyncio.StreamReader] = None
            writer: Optional[asyncio.StreamWriter] = None
            try:
                reader, writer = await self._open_connection(host, port)
                payload = json.dumps({"method": method, "params": params or {}}, ensure_ascii=False, separators=(",", ":")) + "\n"
                writer.write(payload.encode("utf-8"))
                await writer.drain()
                method_timeout = self._long_timeout if method in self._long_methods else self.timeout
                response_line = await asyncio.wait_for(reader.readline(), timeout=method_timeout)
                if not response_line:
                    raise RuntimeError("positions_service returned empty response")
                response = json.loads(response_line.decode("utf-8"))
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error", "unknown error")))
                await self._set_preferred(idx)
                return response.get("result")
            except Exception as exc:
                last_exc = exc
                failures.append(f"{host}:{port}({type(exc).__name__})")
                await self._advance_preferred(idx)
            finally:
                if writer:
                    writer.close()
                    with suppress(Exception):
                        await writer.wait_closed()
        summary = "; ".join(failures) if failures else "no endpoints attempted"
        message = f"positions_service RPC failed for {method}: {summary}"
        if self.logger:
            now = time.time()
            if not hasattr(self, "_last_failure_log_ts") or now - getattr(self, "_last_failure_log_ts", 0) > 15:
                setattr(self, "_last_failure_log_ts", now)
                self.logger.warning(message)
        if last_exc:
            raise RuntimeError(message) from last_exc
        raise RuntimeError(message)

    async def ping(self) -> str:
        return await self._call("ping")

    async def get_snapshot(self, account_key: Optional[str] = None) -> Dict[str, Any]:
        params = {"account_key": account_key} if account_key else None
        result = await self._call("get_snapshot", params)
        if not isinstance(result, dict):
            raise RuntimeError("positions_service snapshot response malformed")
        return result

    async def get_indicators(self, symbol: str, *, force_refresh: bool = False) -> Dict[str, Any]:
        if not symbol:
            raise ValueError("symbol required")
        params = {"symbol": symbol, "force_refresh": force_refresh}
        result = await self._call("get_indicators", params)
        if not isinstance(result, dict):
            return {}
        return result

    async def get_latest_market_data(self) -> Dict[str, Any]:
        """Fetch the latest full indicator snapshot via RPC."""
        result = await self._call("get_latest_market_data")
        if isinstance(result, dict):
            return result
        return {}

    async def purge_obsolete_symbols(self, *, dry_run: bool = False, include_backups: bool = False) -> Dict[str, Any]:
        params = {"dry_run": bool(dry_run), "include_backups": bool(include_backups)}
        result = await self._call("purge_obsolete_symbols", params)
        if isinstance(result, dict):
            return result
        return {}

    async def cleanup_temp_files(self, account_key: str) -> int:
        if not account_key:
            raise ValueError("account_key required")
        result = await self._call("cleanup_temp_files", {"account_key": account_key})
        if isinstance(result, int):
            return result
        try:
            return int(result)
        except Exception:
            return 0

    async def manage_all_stops(self, account_key: str) -> Dict[str, List[Dict[str, Any]]]:
        if not account_key:
            raise ValueError("account_key required")
        result = await self._call("manage_all_stops", {"account_key": account_key})
        if isinstance(result, dict):
            return {k: v for k, v in result.items() if isinstance(v, list)}
        return {}

    async def manage_stop(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be dict")
        result = await self._call("manage_stop", payload)
        if isinstance(result, list):
            return [entry for entry in result if isinstance(entry, dict)]
        return []

    async def shutdown_service(self, reason: str = "client_request") -> bool:
        try:
            result = await self._call("shutdown", {"reason": reason})
            return bool(result)
        except Exception:
            return False

from ez_indicators import bootstrap_indicators_service, get_indicators_service

# price_svc middleman removed — service loads price caches directly from disk
from utils import (
    REDIS_CHANNELS,
    RateLimitDuplicateFilter,
    action_logger,
    clean_and_repair_symbol,
    clean_position_key,
    construct_position_key,
    current_account,
    force_usdc_if_needed,
    get_current_environment,
    get_current_price,
    get_simple_redis_manager,
    is_hedge_account,
    is_sandbox_account,
    is_strict_no_loss_account,
    load_environment_from_gpg,
    parse_position_key,
    safe_fetch_float,
)

try :
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 10000), hard))
except Exception: 
    pass
_last_events_cache_interval = 60 
try :
    from typing import Any, Dict, List, Optional, Union

    import orjson

    def default_json_serializer(obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        raise TypeError

    def json_dumps(obj: Any, **kwargs) -> bytes:
        option = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS 
        return orjson.dumps(obj, default=default_json_serializer, option=option) 

    def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
        if isinstance(s, str):
            s = s.encode('utf-8')
        return orjson.loads(s) 
    JSONDecodeError = orjson.JSONDecodeError 
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
load_environment_from_gpg(None)

def _get_hedge_engine_module():
    """Lazy import of ez_positions_quick module."""
    try :
        return importlib.import_module('ez_positions_quick')
    except (ImportError, AttributeError) as e:
        logger.warning(f"[PositionService] Failed to import ez_positions_quick: {e}")
        return None

def _get_trade_manager_class():
    try :
        _ez_manage_module = importlib.import_module('ez_manage')
        return getattr(_ez_manage_module, 'MultiAccountTradeManager', None)
    except (ImportError, AttributeError) as e:
        logger.warning( "[PositionService] Failed to import MultiAccountTradeManager: %s", str(e) )
        return None
config = Config()
HedgeEngine = None 
logger = logging.getLogger("ez_positions_service")
logger.propagate = False
logger.setLevel(logging.INFO)
for h in list(logger.handlers): logger.removeHandler(h)
base_path = config.BASE_PATH
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(stream_handler)
paper_logs_dir = Path.home()/'logs'
logs_dir = Path.home()/'logs'
logs_dir.mkdir(parents=True, exist_ok=True)
file_handler_path = logs_dir / "ez_positions_service.log"
file_handler = RotatingFileHandler(file_handler_path, maxBytes=100*1024*1024, backupCount=5, encoding='utf-8', mode='a')
file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(file_handler)
master_stop_enter_count: Dict[str, int] = {}; master_stop_monitor_counter: Dict[str, int] = {}; master_stop_last_seen: Dict[str, float] = {}; master_stop_last_alert: Dict[str, float] = {}; MASTER_STOP_STALE_SECONDS = 90.0; MASTER_STOP_ALERT_COOLDOWN = 30.0
SERVER_HEARTBEAT_HOST = "s1-int"
SERVER_HEARTBEAT_BASE = "/home/niels/binance/data"
SERVER_HEARTBEAT_STALE_SECONDS = 30
current_env = get_current_environment()
service_global = None
positions_engine = None
_positions_loaded_once_global = False 

async def safe_check_server_heartbeat(account_key: str = None) -> bool:
    name = f"ez_manage_running_{account_key}" if account_key else "ez_manage_running"
    if current_env['env'] == 'server':
        path = Path(config.DATA_DIR) / name
        try :
            if not path.exists(): return False
            return (time.time() - path.stat().st_mtime) < SERVER_HEARTBEAT_STALE_SECONDS
        except Exception:
            return False
    server_path = f"{SERVER_HEARTBEAT_BASE}/{name}"
    try :
        proc = await asyncio.create_subprocess_exec("ssh", "-o", "ConnectTimeout=2", "-o", "StrictHostKeyChecking=no", SERVER_HEARTBEAT_HOST, "stat", "-c", "%Y", server_path, stdout=aio_subprocess.PIPE, stderr=aio_subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if proc.returncode != 0: return False
        try :
            mtime = float(stdout.decode().strip())
        except Exception:
            return False
        return (time.time() - mtime) < SERVER_HEARTBEAT_STALE_SECONDS
    except Exception as exc:
        if logger: logger.debug(f"[HEARTBEAT] check failed for {account_key}: {exc}")
        return False

async def safe_get_stop_levels_data(position_key: str, service) -> Optional[List]:
    if not position_key or not service: return None
    levels = getattr(service, "stop_levels", {}).get(position_key)
    if levels: return levels
    stop_manager = getattr(service, "stop_manager", None)
    if stop_manager:
        cached = stop_manager.stop_levels.get(position_key)
        if cached: return cached
        try :
            account_key, _, side = parse_position_key(position_key)
        except Exception:
            account_key = service._parse_account_from_key(position_key) if hasattr(service, "_parse_account_from_key") else None
            side = position_key.rsplit("_", 1)[-1] if isinstance(position_key, str) and "_" in position_key else None
        if account_key and side and hasattr(stop_manager, "get_stop_file"):
            file_path = stop_manager.get_stop_file(account_key, side)
            try :
                if await aio_os.path.exists(file_path):
                    async with aiofiles.open(file_path, "r") as handle:
                        payload = await handle.read()
                    if payload.strip():
                        data = json.loads(payload)
                        entry = data.get(position_key)
                        if isinstance(entry, dict):
                            raw_levels = entry.get("stop_levels")
                            if isinstance(raw_levels, list) and raw_levels:
                                return raw_levels
            except Exception as exc:
                if logger: logger.debug(f"[stop_levels] load failed for {position_key}: {exc}")
    return None

def now() -> datetime:
    return datetime.now(timezone.utc)

def ensure_tz(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _safe_float(value: Any, default: float = 0.0) -> float:
    try : return float(value)
    except (TypeError, ValueError): return default

def safe_float(x, default=0.0):
    """Convert value to float, ensuring we never return None for Position creation"""
    try :
        if x is None:
            return default
        if isinstance(x, (float, int)):
            if math.isnan(x) or math.isinf(x):
                return default
            return float(x)
        if isinstance(x, Decimal):
            return float(x)
        if isinstance(x, str):
            s = x.strip()
            if not s: return default
            s = s.replace(", ", "")
            return float(s)
        if isinstance(x, (int, float, Decimal)):
            return float(x)
        if isinstance(x, str):
            s = x.strip()
            if s == "":
                return default
            s = s.replace(", ", "")
            return float(Decimal(s))
        if isinstance(x, dict):
            for k in ("amount", "qty", "positionAmt", "value"):
                if k in x:
                    return safe_float(x[k], default)
        return float(x)
    except (InvalidOperation, ValueError, TypeError) as e:
        try :
            logger.debug(f"[safe_float] failed to parse {repr(x)} -> {e}")
        except Exception:
            pass
        return default

def coerce_price_value(value, default=0.0):
    if isinstance(value, (tuple, list)):
        if not value:
            return default
        return safe_float(value[0], default)
    if isinstance(value, dict):
        for key in ("price", "current_price", "mark_price", "value"):
            if key in value:
                return safe_float(value.get(key), default)
    return safe_float(value, default)

def quantize_qty(value: float, step_size: float) -> Optional[str]:
    try :
        step = Decimal(str(step_size))
        qty = Decimal(str(max(value, 0.0)))
        if step <= 0:
            return None
        quantized = (qty // step) * step
        if quantized <= 0:
            return None
        return format(quantized.normalize(), "f")
    except (InvalidOperation, ValueError):
        return None

def safe_datetime(ts, fallback: Optional[datetime] = None) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        try :
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            pass
    if isinstance(ts, str):
        candidate = ts.strip()
        if candidate:
            numeric = None
            try :
                numeric = float(candidate)
            except (TypeError, ValueError):
                numeric = None
            if numeric is not None and not math.isnan(numeric) and not math.isinf(numeric):
                try :
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
            try :
                dt = pd.to_datetime(candidate, utc=True).to_pydatetime()
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.debug(f"[safe_datetime] Failed to parse timestamp string: {candidate} ({e})")
    if ts is not None and not isinstance(ts, (dict, list)):
        logger.debug(f"[safe_datetime] Unexpected timestamp type: {type(ts)} -> {ts}")
    return fallback

def calculate_gain(position_side, current_price, entry_price):
    if entry_price <= 0:
        return 0.0
    if position_side == 'LONG':
        return ((current_price - entry_price) / entry_price) * 100
    else:
        return ((entry_price - current_price) / entry_price) * 100

def get_max_position_size(symbol: str, account_key: str = None) -> float:
    if account_key == 'men':
        return config.MAX_POSITION_SIZE_MEN
    if account_key == 'fin':
        return config.MAX_POSITION_SIZE_FIN
    if symbol == "BTCUSDC":
        return config.MAX_POSITION_SIZE_BTC
    return config.MAX_POSITION_SIZE

def _normalize_timestamp_to_z(timestamp_str: str) -> str:
    """Normalize timestamp string to end with 'Z' for UTC - only processes actual ISO timestamp strings"""
    if not isinstance(timestamp_str, str) or not timestamp_str:
        return timestamp_str
    ts = timestamp_str.strip()
    if ts.endswith('Z'):
        base = ts[:-1]
        if base:
            try :
                float(base)
                return base
            except ValueError:
                pass
        return ts
    if '+' in ts or '-' in ts[10:]:
        return ts
    if not ('-' in ts or 'T' in ts):
        return ts
    if ts.count('-') < 2:
        return ts
    if len(ts) < 10:
        return ts
    if 'T' in ts:
        return ts + 'Z'
    return ts
INDICATOR_STALE_SECONDS = float(getattr(config, "INDICATOR_STALE_SECONDS", 60.0))
INDICATOR_CRITICAL_SECONDS = float(getattr(config, "INDICATOR_CRITICAL_SECONDS", 120.0))
MAX_MARKET_DATA_AGE_SECONDS = float(getattr(config, "INDICATOR_MAX_AGE_SECONDS", 180.0))

def _coerce_indicator_epoch(value) -> Optional[float]:
    if value is None: return None
    if isinstance(value, (int, float)):
        try : epoch = float(value)
        except (TypeError, ValueError): return None
        if math.isnan(epoch) or math.isinf(epoch): return None
        return epoch / (1000.0 if epoch > 1e12 else 1.0)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate: return None
        try :
            numeric = float(candidate)
            if math.isnan(numeric) or math.isinf(numeric): numeric = None
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None:
            return numeric / (1000.0 if numeric > 1e12 else 1.0)
        try :
            if 'T' in candidate or (candidate.count('-') >= 2 and len(candidate) >= 10):
                normalized = candidate.replace('Z', '+00:00') if candidate.endswith('Z') else candidate
                try :
                    dt = datetime.fromisoformat(normalized)
                except ValueError:
                    normalized = _normalize_timestamp_to_z(candidate)
                    dt = isoparse(normalized)
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
        except Exception:
            return None
    return None

def _indicator_payload_age(payload: dict) -> tuple[Optional[float], Optional[str]]:
    if not isinstance(payload, dict): return (None, None)
    now_ts = time.time()
    newest_epoch = None
    newest_key = None
    for key, value in payload.items():
        if not isinstance(key, str): continue
        if key in {"timestamp", "time", "ts", "updated_at"} or "timestamp" in key or key.endswith("_ts") or key.endswith("_time"):
            epoch = _coerce_indicator_epoch(value)
            if epoch is None: continue
            if newest_epoch is None or epoch > newest_epoch:
                newest_epoch = epoch
                newest_key = key
    if newest_epoch is None: return (None, newest_key)
    return (max(0.0, now_ts - newest_epoch), newest_key)

def _indicator_payload_timestamp(payload: dict) -> Optional[datetime]:
    if not isinstance(payload, dict) or not payload: return None
    newest_epoch = None
    candidates = {"timestamp", "time", "ts", "last_updated", "generated_at", "created_at"}
    for key, value in payload.items():
        if not isinstance(key, str): continue
        if key in candidates or "timestamp" in key or key.endswith("_ts") or key.endswith("_time") or key.endswith("_at"):
            epoch = _coerce_indicator_epoch(value)
            if epoch is None: continue
            if newest_epoch is None or epoch > newest_epoch: newest_epoch = epoch
    return datetime.fromtimestamp(newest_epoch, tz=timezone.utc) if newest_epoch is not None else None

def _snapshot_timestamp(snapshot: dict) -> Optional[datetime]:
    if not isinstance(snapshot, dict) or not snapshot: return None
    best_epoch = None
    for key in ("timestamp", "generated_at", "last_updated", "created_at"):
        epoch = _coerce_indicator_epoch(snapshot.get(key))
        if epoch is not None and (best_epoch is None or epoch > best_epoch): best_epoch = epoch
    for value in snapshot.values():
        if not isinstance(value, dict): continue
        ts = _indicator_payload_timestamp(value)
        if ts is None: continue
        epoch = ts.timestamp()
        if best_epoch is None or epoch > best_epoch: best_epoch = epoch
    return datetime.fromtimestamp(best_epoch, tz=timezone.utc) if best_epoch is not None else None

def _snapshot_is_fresh(snapshot: dict, max_age: float = MAX_MARKET_DATA_AGE_SECONDS, fallback_ts: Optional[Any] = None) -> bool:
    if not isinstance(snapshot, dict) or not snapshot: return False

    def _normalize(candidate: Any) -> Optional[datetime]:
        if candidate is None: return None
        if isinstance(candidate, datetime): return candidate if candidate.tzinfo else candidate.replace(tzinfo=timezone.utc)
        if isinstance(candidate, (int, float)):
            try : return datetime.fromtimestamp(float(candidate), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError): return None
        return None
    candidates: list[datetime] = []
    primary_ts = _snapshot_timestamp(snapshot)
    normalized_primary = _normalize(primary_ts)
    if normalized_primary: candidates.append(normalized_primary)
    if isinstance(fallback_ts, (list, tuple, set)):
        fallback_iterable = fallback_ts
    else:
        fallback_iterable = (fallback_ts, )
    for fb in fallback_iterable:
        normalized_fb = _normalize(fb)
        if normalized_fb: candidates.append(normalized_fb)
    if not candidates: return False
    now_utc = datetime.now(timezone.utc)
    for candidate in candidates:
        age = (now_utc - candidate).total_seconds()
        if age <= max_age or age < 0: return True
    return False

def _tag_data_stale(payload: dict, *, age: Optional[float], ts_key: Optional[str], source: str) -> dict:
    tagged = dict(payload)
    tagged["DATA_STALE"] = True
    tagged["DATA_STALE_SOURCE"] = source
    if age is not None: tagged["DATA_STALE_AGE"] = age
    if ts_key: tagged["DATA_STALE_TS_KEY"] = ts_key
    return tagged

def _finalize_indicator_payload(payload: dict, required: Optional[List[str]]) -> dict:
    if not required: return payload
    subset = {k: payload.get(k) for k in required if k in payload}
    for meta_key in ("timestamp", "time", "DATA_STALE", "DATA_STALE_AGE", "DATA_STALE_SOURCE", "DATA_STALE_TS_KEY"):
        if meta_key in payload: subset[meta_key] = payload[meta_key]
    return subset

def minutes_since(timestamp_obj, now=None):
    if not timestamp_obj:
        return 999999 
    if now is None:
        now = datetime.now(timezone.utc)
    try :
        if isinstance(timestamp_obj, str):
            dt = isoparse(timestamp_obj)
        else:
            dt = timestamp_obj
        return (now - dt).total_seconds() / 60.0
    except Exception:
        return 999999 

def _get_smart_default(indicator_key: str):
    """Provides a sane default value for a missing indicator key."""
    if indicator_key == 'current_price': return None
    if "crossover" in indicator_key or "crossunder" in indicator_key: return False
    if "signal" in indicator_key: return "NEUTRAL"
    if "ha_" in indicator_key: return "neutral"
    return 0.0

async def ensure_path_writable(path: Path, *, file_mode: int = 0o664, dir_mode: int = 0o775) -> None:
    path_obj = Path(path)
    try :
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        if not os.access(path_obj.parent, os.W_OK):
            try :
                os.chmod(path_obj.parent, dir_mode)
            except PermissionError:
                pass
    except Exception as exc:
        logger.debug(f"[ensure_path] Directory prep failed for {path_obj.parent}: {exc}")
    try :
        if not path_obj.exists():
            path_obj.touch(mode=file_mode, exist_ok=True)
        if not os.access(path_obj, os.W_OK):
            try :
                os.chmod(path_obj, file_mode)
            except PermissionError:
                pass
    except Exception as exc:
        logger.debug(f"[ensure_path] Permission check failed for {path_obj}: {exc}")

def ensure_path_writable_sync(path: Path, *, file_mode: int = 0o664, dir_mode: int = 0o775) -> None:
    path_obj = Path(path)
    try :
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        try :
            if not os.access(path_obj.parent, os.W_OK):
                os.chmod(path_obj.parent, dir_mode)
        except PermissionError:
            pass
    except Exception as exc:
        logger.debug(f"[ensure_path_sync] Directory prep failed for {path_obj.parent}: {exc}")
    try :
        if not path_obj.exists():
            try :
                path_obj.touch(mode=file_mode, exist_ok=True)
            except PermissionError:
                pass
        try :
            if not os.access(path_obj, os.W_OK):
                os.chmod(path_obj, file_mode)
        except PermissionError:
            pass
    except Exception as exc:
        logger.debug(f"[ensure_path_sync] Permission check failed for {path_obj}: {exc}")

def decode_payload(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try :
            raw = raw.decode("utf-8")
        except Exception:
            raw = raw.decode("utf-8", "ignore")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try :
            payload = json.loads(raw)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}
_file_write_semaphore: Optional[asyncio.Semaphore] = None
_file_write_loop: Optional[asyncio.AbstractEventLoop] = None

def _get_file_write_semaphore() -> asyncio.Semaphore:
    global _file_write_semaphore, _file_write_loop
    loop = asyncio.get_running_loop()
    if _file_write_semaphore is None or _file_write_loop is not loop:
        _file_write_semaphore = asyncio.Semaphore(2) 
        _file_write_loop = loop
    return _file_write_semaphore

class LimitedAioOpen:

    def __init__(self, path, mode, **kwargs):
        self.path = path
        self.mode = mode
        self.kwargs = kwargs
        self._sem = _get_file_write_semaphore()
        self._fh = None

    async def __aenter__(self):
        await self._sem.acquire()
        self._fh = await aiofiles.open(self.path, self.mode, **self.kwargs)
        return self._fh

    async def __aexit__(self, exc_type, exc, tb):
        try :
            if self._fh is not None:
                await self._fh.close()
        finally:
            self._sem.release()

async def _atomic_write_json_impl(file_path: Union[str, Path], data: dict):
    try :
        file_path_obj = Path(file_path)
    except Exception:
        return False
    name_lower = file_path_obj.name.lower()
    if ("long_positions.json" in name_lower or "short_positions.json" in name_lower) and isinstance(data, dict):
        try:
            if file_path_obj.exists():
                with open(file_path_obj, "r") as _ef:
                    _existing = json.load(_ef)
                if isinstance(_existing, dict):
                    _protected = 0
                    for _pk, _old in _existing.items():
                        if not isinstance(_old, dict):
                            continue
                        _o_amt = abs(float(_old.get("positionAmt", 0) or 0))
                        _o_ep = float(_old.get("entry_price", 0) or 0)
                        _o_iq = float(_old.get("initial_quantity", 0) or 0)
                        _o_oa = _old.get("opened_at")
                        if _o_amt > 0 or _o_ep > 0 or _o_iq > 0 or _o_oa is not None:
                            _new = data.get(_pk)
                            if isinstance(_new, dict):
                                _n_amt = abs(float(_new.get("positionAmt", 0) or 0))
                                _n_ep = float(_new.get("entry_price", 0) or 0)
                                _n_iq = float(_new.get("initial_quantity", 0) or 0)
                                _n_oa = _new.get("opened_at")
                                if _n_amt == 0 and _n_ep == 0 and _n_iq == 0 and _n_oa is None:
                                    data[_pk] = _old
                                    _protected += 1
                                else:
                                    _field_fixed = 0
                                    for _cf in ("entry_price", "max_gain", "last_reduction_price", "last_reduction_amount", "last_augmentation_price", "last_augmentation_amount", "initial_quantity", "max_quantity", "max_positionSize", "augment_reason", "reduction_reason", "opened_at", "last_augmentation_time", "last_reduction_time"):
                                        _ov = _old.get(_cf)
                                        _nv = _new.get(_cf)
                                        if _ov and _ov != "" and _ov != 0 and _ov != "0" and _ov != "None" and (_nv is None or _nv == "" or _nv == 0 or _nv == "0" or _nv == "None"):
                                            _new[_cf] = _ov
                                            _field_fixed += 1
                                    if _field_fixed > 0:
                                        import subprocess as _fgsp
                                        import threading
                                        import traceback as _fgtb
                                        _fg_stack = "".join(_fgtb.format_stack()[-10:])
                                        _fg_threads = "\n".join([f"  Thread {t.name} (id={t.ident}, daemon={t.daemon})" for t in threading.enumerate()])
                                        _fg_erased_fields = []
                                        for _cf2 in ("entry_price", "max_gain", "last_reduction_price", "last_reduction_amount", "last_augmentation_price", "last_augmentation_amount", "initial_quantity", "max_quantity", "max_positionSize", "augment_reason", "reduction_reason", "opened_at", "last_augmentation_time", "last_reduction_time"):
                                            _ov2 = _old.get(_cf2)
                                            _nv2_orig = data.get(_pk, {}).get(_cf2) if _pk in data else None
                                            if _ov2 and _ov2 != "" and _ov2 != 0 and (_nv2_orig is None or _nv2_orig == "" or _nv2_orig == 0):
                                                _fg_erased_fields.append(f"    {_cf2}: {_ov2} → {_nv2_orig}")
                                        _fg_crash_log = f"""
{'='*80}
🚨🚨🚨 FIELD ERASURE CAUGHT — {_pk} — {datetime.now(timezone.utc).isoformat()}
{'='*80}
ERASED FIELDS ({_field_fixed}):
{chr(10).join(_fg_erased_fields)}

FULL CALL STACK:
{_fg_stack}

ALL THREADS ({threading.active_count()}):
{_fg_threads}

PID: {os.getpid()}
FILE: {file_path_obj.name}

POSITION BEFORE (on disk):
{json.dumps(_old, default=str, indent=2)[:2000]}

POSITION AFTER (attempted write):
{json.dumps(_new, default=str, indent=2)[:2000]}
{'='*80}
"""
                                        logger.critical(_fg_crash_log)
                                        try:
                                            _crash_path = Path(os.path.expanduser("~/logs")) / "FIELD_ERASURE_CRASH.log"
                                            with open(_crash_path, "a") as _crash_f:
                                                _crash_f.write(_fg_crash_log)
                                                try:
                                                    _ps_out = _fgsp.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
                                                    _crash_f.write(f"\nALL PROCESSES:\n{_ps_out.stdout}\n")
                                                except Exception:
                                                    pass
                                        except Exception:
                                            pass
                                        _protected += 1
                            elif _pk not in data:
                                data[_pk] = _old
                                _protected += 1
                    if _protected > 0:
                        import traceback as _tb
                        _stack = "".join(_tb.format_stack()[-6:])
                        logger.critical(f"🛡️ [WRITE_GUARD_IMPL] {file_path_obj.name}: Protected {_protected} positions from null overwrite. PID={os.getpid()}\nStack:\n{_stack}")
        except Exception as _ge:
            logger.error(f"[WRITE_GUARD_IMPL] Guard check failed for {file_path_obj.name}: {_ge}")
    random_suffix = uuid.uuid4().hex
    temp_path_obj = file_path_obj.with_name(f".{file_path_obj.name}.{random_suffix}.atom")
    str_file_path = str(file_path_obj)
    str_temp_path = str(temp_path_obj)
    try :
        if not file_path_obj.parent.exists():
            file_path_obj.parent.mkdir(parents=True, exist_ok=True)
            try :
                os.chmod(str(file_path_obj.parent), 0o775)
            except Exception: pass
        try :
            if 'json_dumps' in globals():
                json_bytes = json_dumps(data)
            else:
                import json
                json_bytes = json.dumps(data).encode('utf-8')
        except Exception as e:
            logger.error(f"Serialization failed for {str_file_path}: {e}")
            return False
        if isinstance(json_bytes, str):
            json_bytes = json_bytes.encode('utf-8')
        async with aiofiles.open(str_temp_path, "wb") as f:
            await f.write(json_bytes)
            await f.flush()
            await asyncio.to_thread(os.fsync, f.fileno())
        if not await aio_os.path.exists(str_temp_path):
            logger.error(f"Atomic write failed: Temp file disappeared before rename: {str_temp_path}")
            return False
        await asyncio.to_thread(os.replace, str_temp_path, str_file_path)
        try :
            os.chmod(str_file_path, 0o664)
        except Exception: pass
        return True
    except Exception as e:
        logger.error(f"Atomic write failed for {str_file_path}: {e}", exc_info=True)
        if await aio_os.path.exists(str_temp_path):
            try : await aio_os.remove(str_temp_path)
            except Exception: pass
        return False

def _sanitize_json_value(value: Any) -> Any:
    """Recursively sanitize JSON values to ensure no NaN/inf."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return 0.0
        return value
    elif isinstance(value, dict):
        return {k: _sanitize_json_value(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return [_sanitize_json_value(item) for item in value]
    return value

async def atomic_write_json(file_path: Union[str, Path], data: dict):
    clean_data = _sanitize_json_value(data)
    async with _get_file_write_semaphore():
        return await _atomic_write_json_impl(file_path, clean_data)

async def write_changed_positions_file(file_path: Union[str, Path], changed_positions: dict) -> bool:
    file_path_obj = Path(file_path)
    if "_changed.json" not in file_path_obj.name.lower():
        logger.error(f"[ ] File path must end with _changed.json: {file_path_obj}")
        return False
    await ensure_path_writable(file_path)
    if not isinstance(changed_positions, dict):
        logger.error(f"write_cha ged_positions_file: data must be a dict, got {type(changed_positions)}")
        return False
    data = _sanitize_json_value(changed_positions)
    async with _get_file_write_semaphore():
        return await _atomic_write_json_impl(file_path, data)

async def write_updated_positions_file(file_path: Union[str, Path], updated_positions: dict) -> bool:
    file_path_obj = Path(file_path)
    if "_updated.json" not in file_path_obj.name.lower():
        logger.error(f"[write_updated_positions_file] File path must end with _updated.json: {file_path_obj}")
        return False
    await ensure_path_writable(file_path)
    if not isinstance(updated_positions, dict):
        logger.error(f"write_updated_positions_file: data must be a dict, got {type(updated_positions)}")
        return False
    data = _sanitize_json_value(updated_positions)
    async with _get_file_write_semaphore():
        return await _atomic_write_json_impl(file_path, data)

def get_most_recent_timestamp(*timestamps: Optional[datetime]) -> datetime:
    now = datetime.now(timezone.utc)
    valid = [ts for ts in timestamps if isinstance(ts, datetime)]
    return max(valid) if valid else now - timedelta(minutes=1000)

async def quick_price(symbol: str) -> float:
    global service_global
    service = service_global
    candidates = []
    now_utc = datetime.now(timezone.utc)
    if not service:
        return None
    symbol_upper = symbol.strip().upper()
    for account_key, positions_dict in service.positions_by_account.items():
        for pos_key, position in positions_dict.items():
            if position and position.symbol == symbol_upper and hasattr(position, 'mark_price') and position.mark_price and position.mark_price > 0:
                ts = getattr(position, 'mark_price_last_updated', None)
                if ts:
                    if isinstance(ts, str):
                        try :
                            ts = isoparse(ts)
                        except Exception:
                            ts = None
                    if isinstance(ts, datetime):
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        age = (now_utc - ts).total_seconds()
                        if age < 2.0:
                            return position.mark_price
                        candidates.append((age, position.mark_price, "position", ts))
    manager = getattr(service, "primary_ws_manager", None)
    if not manager and getattr(service, "websocket_managers", None):
        manager = next(iter(service.websocket_managers.values()), None)
    if service:
        try :
            read_client = None
            if hasattr(service, 'redis_manager') and service.redis_manager and service.redis_manager._initialized:
                read_client = (service.redis_manager.connections.get("gateway") or service.redis_manager.connections.get("local"))
            if read_client:
                redis_key = f"mark_price:{symbol}"
                try:
                    data = await asyncio.wait_for(read_client.get(redis_key), timeout=0.5)
                except asyncio.TimeoutError:
                    data = None
                if data:
                    try :
                        p_data = json.loads(data)
                        price = float(p_data["price"])
                        if price > 0:
                            ts_str = p_data.get("timestamp", "")
                            normalized_ts = _normalize_timestamp_to_z(ts_str) if ts_str and isinstance(ts_str, str) else ts_str
                            timestamp = isoparse(normalized_ts) if normalized_ts and isinstance(normalized_ts, str) else normalized_ts
                            if timestamp and timestamp.tzinfo is None:
                                timestamp = timestamp.replace(tzinfo=timezone.utc)
                            age = (now_utc - timestamp).total_seconds() if timestamp else 999999
                            if age < 2.0 and price > 0: return price
                            if age < 90 and price > 0: candidates.append((age, price, "Redis", timestamp))
                    except Exception: pass
        except Exception: pass
    if service:
        if hasattr(service, 'price_cache') and symbol in service.price_cache:
            try :
                cache_entry = service.price_cache[symbol]
                if isinstance(cache_entry, dict) and 'price' in cache_entry:
                    price = float(cache_entry['price'])
                    ts = cache_entry.get('timestamp')
                    ts_dt = ensure_tz(isoparse(ts)) if isinstance(ts, str) else ensure_tz(ts) if isinstance(ts, datetime) else now_utc
                    age = (now_utc - ts_dt).total_seconds()
                    if age < 2.0 and price > 0: return price
                    if age < 90 and price > 0: candidates.append((age, price, "price_cache", ts_dt))
                elif isinstance(cache_entry, (tuple, list)) and cache_entry:
                    price = float(cache_entry[0])
                    ts_dt = ensure_tz(cache_entry[1]) if len(cache_entry) > 1 and cache_entry[1] else now_utc
                    age = (now_utc - ts_dt).total_seconds()
                    if age < 2.0 and price > 0: return price
                    if age < 90 and price > 0: candidates.append((age, price, "price_cache", ts_dt))
            except Exception: pass
        if hasattr(service, 'price_cache_2') and symbol in service.price_cache_2:
            try :
                cache_entry = service.price_cache_2[symbol]
                if isinstance(cache_entry, dict) and 'price' in cache_entry:
                    price = float(cache_entry['price'])
                    ts = cache_entry.get('timestamp')
                    ts_dt = ensure_tz(isoparse(ts)) if isinstance(ts, str) else ensure_tz(ts) if isinstance(ts, datetime) else now_utc
                    age = (now_utc - ts_dt).total_seconds()
                    if age < 90 and price > 0 and manager:
                        if age < 3.0:
                            await manager._apply_mark_price(symbol, price, ts_dt)
                        return price
                    candidates.append((age, price, "price_cache_2", ts_dt))
                elif isinstance(cache_entry, (tuple, list)) and cache_entry:
                    price = float(cache_entry[0])
                    ts_dt = ensure_tz(cache_entry[1]) if len(cache_entry) > 1 and cache_entry[1] else now_utc
                    age = (now_utc - ts_dt).total_seconds()
                    if age < 2.0 and price > 0: return price
                    if age < 90 and price > 0: candidates.append((age, price, "price_cache_2", ts_dt))
            except Exception: pass
        if hasattr(service, 'price_cache_3') and symbol in service.price_cache_3:
            try :
                cache_entry = service.price_cache_3[symbol]
                if isinstance(cache_entry, dict) and 'price' in cache_entry:
                    price = float(cache_entry['price'])
                    ts = cache_entry.get('timestamp')
                    ts_dt = ensure_tz(isoparse(ts)) if isinstance(ts, str) else ensure_tz(ts) if isinstance(ts, datetime) else now_utc
                    age = (now_utc - ts_dt).total_seconds()
                    if age < 2.0 and price > 0: return price
                    if age < 90 and price > 0: candidates.append((age, price, "price_cache_3", ts_dt))
                elif isinstance(cache_entry, (tuple, list)) and cache_entry:
                    price = float(cache_entry[0])
                    ts_dt = ensure_tz(cache_entry[1]) if len(cache_entry) > 1 and cache_entry[1] else now_utc
                    age = (now_utc - ts_dt).total_seconds()
                    if age < 2.0 and price > 0: return price
                    if age < 90 and price > 0: candidates.append((age, price, "price_cache_3", ts_dt))
            except Exception: pass
        for account_key, positions_dict in service.positions_by_account.items():
            for pos_key, position in positions_dict.items():
                if position and position.symbol == symbol and hasattr(position, 'mark_price') and position.mark_price and position.mark_price > 0:
                    age = (now_utc - position.last_updated).total_seconds() if hasattr(position, 'last_updated') and position.last_updated else 999
                    if age < 90 and position.mark_price > 0: return position.mark_price
                    candidates.append((age, position.mark_price, "position", position.last_updated if hasattr(position, 'last_updated') else None))
                    break
        try :
            if hasattr(service, 'get_current_price'):
                price, price_ts = await service.get_current_price(symbol)
                if price and price > 0:
                    age = (now_utc - price_ts).total_seconds() if price_ts else 0
                    if age < 90 and price > 0: return price
                    candidates.append((age, price, "get_current_price", price_ts))
        except Exception: pass
    if candidates:
        candidates.sort(key=lambda x: x[0])
        best_age, best_price, best_source, best_ts = candidates[0]
        if best_price <= 0:
            logger.error(f"[quick_price] CRITICAL: Best candidate has invalid price {best_price} for {symbol}!")
            return None
        candidate_ts = best_ts if isinstance(best_ts, datetime) else now_utc - timedelta(seconds=best_age) if best_age else datetime.now(timezone.utc)
        cand_age = (datetime.now(timezone.utc) - candidate_ts).total_seconds() if isinstance(candidate_ts, datetime) else 999.0
        if manager and cand_age < 3.0:
            try : await manager._apply_mark_price(symbol, best_price, candidate_ts)
            except Exception: pass
        return best_price

class DummyLock:

    async def __aenter__(self):
        pass

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def acquire(self): pass

    def release(self): pass

    def locked(self): return False

class PositionCallbackManager:

    def __init__(self):
        self._callbacks: DefaultDict[str, list] = defaultdict(list)
        self._latest: Dict[str, Any] = {}
        self._lock = DummyLock()

    async def register_callback(self, position_key: str, callback: Callable[[Dict[str, Any]], Awaitable[Any] | Any]) -> None:
        if not position_key or not callable(callback): return
        async with self._lock:
            if callback not in self._callbacks[position_key]: self._callbacks[position_key].append(callback)

    async def unregister_callback(self, position_key: str, callback: Callable[[Dict[str, Any]], Awaitable[Any] | Any]) -> None:
        if not position_key or not callable(callback): return
        async with self._lock:
            callbacks = self._callbacks.get(position_key)
            if not callbacks: return
            if callback in callbacks: callbacks.remove(callback)
            if not callbacks: self._callbacks.pop(position_key, None)

    async def notify(self, position_key: str, payload: Dict[str, Any]) -> None:
        async with self._lock:
            callbacks = list(self._callbacks.get(position_key, ()))
        for callback in callbacks:
            try :
                result = callback(payload)
                if asyncio.iscoroutine(result): await result
            except Exception as exc:
                logger.debug(f"[position_callback] notify failed for {position_key}: {exc}")
        self._latest[position_key] = payload

    async def get_latest_position(self, position_key: str) -> Optional[Dict[str, Any]]:
        return self._latest.get(position_key)

    async def has_recent_update(self, position_key: str, max_age_seconds: float) -> bool:
        payload = self._latest.get(position_key)
        if not isinstance(payload, dict): return False
        ts = payload.get("timestamp")
        if isinstance(ts, (int, float)): return (time.time() - float(ts)) <= max_age_seconds
        if isinstance(ts, datetime): return (datetime.now(timezone.utc) - ts).total_seconds() <= max_age_seconds
        return False

def log_augment_action(position_key, position_value_str, augment_value_str, gain, reason, conviction=None, origin: str = "UNKNOWN", timestamp_3m=None, k_3m=None, k_3m_prv=None):
    safe = lambda x: f"{x:.2f}" if isinstance(x, (int, float)) and x is not None else "None"
    account_key = position_key.split(':')[0] if ':' in position_key else "unknown"
    current_account.set(account_key)
    conviction_str = f", Conviction: {conviction:.1f}" if conviction is not None else ""
    ts_str = f", ts_3m = {timestamp_3m}" if timestamp_3m else ""
    k_str = f", k_3m = {k_3m:.1f}" if k_3m is not None else ""
    k_prv_str = f", k_3m_prv = {k_3m_prv:.1f}" if k_3m_prv is not None else ""
    action_logger.info( f"{position_key}: M Position was augmented. Position Value before: {position_value_str}, Augment by: {augment_value_str}, Gain: {safe(gain)}{conviction_str}{ts_str}{k_str}{k_prv_str} . Reason: {reason} | origin={origin}", extra={'ticker': position_key, 'action': 'AUGMENT', 'position_value_str': position_value_str, 'details': f"Augment by {augment_value_str}", 'conviction': conviction, 'origin': origin, 'timestamp_3m': timestamp_3m, 'k_3m': k_3m, 'k_3m_prv': k_3m_prv } )
@dataclass

class AccountConfig:
    prefix: str
    api_key: str = field(init=False)
    api_secret: str = field(init=False)
    webhook_url: str = field(init=False)
    webhook_secret: str = field(init=False)
    webhook_url_2: Optional[str] = field(default=None, init=False)
    webhook_secret_2: Optional[str] = field(default=None, init=False)
    webhook_url_3: Optional[str] = field(default=None, init=False)
    webhook_secret_3: Optional[str] = field(default=None, init=False)
    client: Optional[Client] = field(default=None, init=False)
    _last_used_weight: int = field(default=0, init=False) 
    _connector_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _ip_cycle: Optional[deque] = field(default=None, init=False)
    _current_ip: Optional[str] = field(default=None, init=False)
    _banned_ips: dict = field(default_factory=dict, init=False)

    def __post_init__(self):
        self.time_offset = 0.0
        self._last_time_sync = 0.0
        p = self.prefix.lower()
        P = self.prefix.upper()

        def get_val(base_name, suffix=""):
            val = os.getenv(f"{p}_{base_name}{suffix}")
            if val: return val
            val = os.getenv(f"{P}_{base_name}{suffix}")
            if val: return val
            return None
        self.api_key = get_val("API_KEY")
        self.api_secret = get_val("API_SECRET")
        self.webhook_url = get_val("WEBHOOK_URL")
        self.webhook_secret = get_val("WEBHOOK_SECRET")
        self.webhook_url_2 = get_val("WEBHOOK_URL", "2")
        self.webhook_secret_2 = get_val("WEBHOOK_SECRET", "2")
        self.webhook_url_3 = get_val("WEBHOOK_URL", "3")
        self.webhook_secret_3 = get_val("WEBHOOK_SECRET", "3")
        missing = []
        if not self.api_key: missing.append(f"{p}_API_KEY")
        if not self.api_secret: missing.append(f"{p}_API_SECRET")
        if not self.webhook_url: missing.append(f"{p}_WEBHOOK_URL")
        if not self.webhook_secret: missing.append(f"{p}_WEBHOOK_SECRET")
        if missing:
            raise ValueError(f"Missing environment variables for account '{p}': {', '.join(missing)}")
        self._init_ip_cycle()

    def _init_ip_cycle(self):
        """Initialize IP rotation for API calls."""
        self._ip_cycle = None
        self._current_ip = None
        self._banned_ips = {}
        if self.prefix.lower() in {"ang", "inf", "men", "flz", "fin"} and sys.platform != "darwin" and os.environ.get("EZ_DISABLE_IP_BINDING") != "1":
            self._ip_cycle = deque(["5.75.211.216", "49.13.39.233", "157.180.125.52"])
            logger.debug(f"[{self.prefix}] IP switching enabled with IPs: {list(self._ip_cycle)}")
        else:
            logger.debug(f"[{self.prefix}] IP switching disabled - prefix: {self.prefix.lower()}, platform: {sys.platform}, env: {os.environ.get('EZ_DISABLE_IP_BINDING')}")

    def _get_next_ip(self) -> Optional[str]:
        """Get next available IP for API calls."""
        if not self._ip_cycle:
            return None
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
        """Mark an IP as banned for API calls."""
        if not ip:
            return
        self._banned_ips[ip] = time.time() + cooldown_seconds

    def _create_bound_adapter(self, ip: str) -> HTTPAdapter:
        if not ip:
            return HTTPAdapter()

        class SourceAddressAdapter(HTTPAdapter):

            def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
                pool_kwargs["source_address"] = (ip, 0)
                super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

            def proxy_manager_for(self, *args, **kwargs):
                kwargs.setdefault("pool_kwargs", {})["source_address"] = (ip, 0)
                return super().proxy_manager_for(*args, **kwargs)
        return SourceAddressAdapter(max_retries=3)

    def _apply_ip_binding(self, client: Client):
        """Apply IP binding to the Binance client for API calls."""
        if sys.platform == "darwin" or os.environ.get("EZ_DISABLE_IP_BINDING") == "1" or not self._ip_cycle:
            return
        timeout_seconds = 20
        tried_ips = set()
        working_ip = None
        while True:
            candidate_ip = self._get_next_ip()
            if not candidate_ip or candidate_ip in tried_ips:
                break
            try :
                import socket
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((candidate_ip, 0))
                probe.close()
                working_ip = candidate_ip
                logger.debug(f"[{self.prefix}] IP {candidate_ip} is bindable")
                break
            except OSError as e:
                logger.warning(f"[{self.prefix}] REST IP {candidate_ip} binding failed: {e}")
                self._mark_ip_banned(candidate_ip)
                tried_ips.add(candidate_ip)
        if working_ip:
            adapter = self._create_bound_adapter(working_ip)
            client.session.mount("https://", adapter)
            client.session.mount("http://", adapter)
            logger.debug(f"[{self.prefix}] Bound client session to dedicated IP {working_ip}")
            self._current_ip = working_ip
        else:
            logger.warning(f"[{self.prefix}] No dedicated REST IP available, using default routing.")
            self._current_ip = None

    def handle_api_ban(self, cooldown_seconds: int = 900):
        banned_ip = getattr(self, "_current_ip", None)
        logger.warning(f"[{self.prefix}] 🚫 IP BAN DETECTED on {banned_ip}, switching IPs...")
        self._mark_ip_banned(banned_ip, cooldown_seconds)
        if self.client:
            self._apply_ip_binding(self.client) if hasattr(self, '_apply_ip_binding') else None
            logger.warning(f"[{self.prefix}] Rotated REST IP after ban; previous={banned_ip}, active={self._current_ip}")
        try :
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(delete_heartbeat_on_ban())
            else:
                loop.run_until_complete(delete_heartbeat_on_ban())
        except Exception: pass

    async def initialize(self):
        """Initialize the Binance client with a hard 10s timeout to prevent blocking bootstrap."""
        if not self.client:
            def _sync_init():
                c = Client(api_key=self.api_key, api_secret=self.api_secret)
                _force_ipv4_for_client(c)
                return c
            try :
                logger.debug(f"[{self.prefix}] Initializing Binance client with API key: {self.api_key[:8]}...{self.api_key[-4:]} (len: {len(self.api_key)})")
                self.client = await asyncio.wait_for(asyncio.to_thread(_sync_init), timeout=10.0)
                self._apply_ip_binding(self.client)
                self.sync_binance_time()
                logger.debug(f"Initialized Binance client for account '{self.prefix}'", extra={'color': "green"})
            except asyncio.TimeoutError:
                logger.warning(f"[{self.prefix}] Binance client init timed out (10s) — will retry in background", extra={'color': "red"})
                self.client = None
                asyncio.create_task(self._deferred_client_init())
            except BinanceAPIException as e:
                logger.error(f"[{self.prefix}] Binance API error during initialization: {e} (code: {e.code if hasattr(e, 'code') else 'N/A'})", extra={'color': "red"})
                asyncio.create_task(self._deferred_client_init())
            except Exception as e:
                logger.error(f"Failed to initialize Binance client for account '{self.prefix}': {e}", extra={'color': "red"})
                asyncio.create_task(self._deferred_client_init())

    async def _deferred_client_init(self):
        """Retry Binance client init in background with backoff."""
        for attempt in range(5):
            await asyncio.sleep(10 * (attempt + 1))
            if self.client:
                return
            def _sync_init():
                c = Client(api_key=self.api_key, api_secret=self.api_secret)
                _force_ipv4_for_client(c)
                return c
            try:
                self.client = await asyncio.wait_for(asyncio.to_thread(_sync_init), timeout=15.0)
                self._apply_ip_binding(self.client)
                self.sync_binance_time()
                logger.info(f"[{self.prefix}] Binance client initialized (deferred attempt {attempt+1})", extra={'color': "green"})
                return
            except Exception as e:
                logger.warning(f"[{self.prefix}] Deferred client init attempt {attempt+1}/5 failed: {e}")

    def sync_binance_time(self):
        """Sync local time with Binance server time to prevent signature errors."""
        try :
            server_time_response = self.client.futures_time()
            server_time = server_time_response['serverTime']
            local_time = int(time.time() * 1000)
            time_diff = server_time - local_time
            if abs(time_diff) > 1000:
                logger.warning(f"[{self.prefix}] ⚠️ Significant time difference detected: {time_diff}ms. This may cause signature errors.")
                logger.warning(f"[{self.prefix}] Please check system clock synchronization.")
            self.time_offset = time_diff
            if hasattr(self.client, '_timestamp_offset'):
                self.client._timestamp_offset = time_diff
            return True
        except Exception as e:
            logger.error(f"[{self.prefix}] Failed to sync time with Binance: {e}")
            return False

    async def safe_api_call(self, func, *args, max_retries=2, **kwargs):
        """Wrapper for API calls that handles timestamp errors by re-syncing time."""
        for attempt in range(max_retries + 1):
            try :
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return await asyncio.to_thread(func, *args, **kwargs)
            except BinanceAPIException as e:
                if e.code == -1021 and attempt < max_retries:
                    logger.warning(f"[{self.prefix}] Timestamp error (code -1021), re-syncing time and retrying (attempt {attempt + 1}/{max_retries + 1})")
                    self.sync_binance_time()
                    await asyncio.sleep(0.5)
                    continue
                raise
            except Exception as e:
                if "Timestamp" in str(e) and attempt < max_retries:
                    logger.warning(f"[{self.prefix}] Timestamp error detected, re-syncing time and retrying (attempt {attempt + 1}/{max_retries + 1})")
                    self.sync_binance_time()
                    await asyncio.sleep(0.5)
                    continue
                raise

    def get_binance_timestamp(self):
        """Get timestamp adjusted for Binance server time."""
        if hasattr(self, 'time_offset'):
            return int(time.time() * 1000) + self.time_offset
        return int(time.time() * 1000)

    async def close(self):
        """Close the Binance client."""
        if self.client:
            logger.debug(f"Closed Binance client for account '{self.prefix}'", extra={'color': "green"})

async def load_accounts_from_config(config_obj: Config, logger_obj: Optional[logging.Logger] = None) -> Dict[str, AccountConfig]:
    accounts = {}
    for account_key in getattr(config_obj, "ACCOUNT_KEYS", []):
        try :
            account = AccountConfig(prefix=account_key)
            await account.initialize()
            accounts[account_key] = account
        except ValueError as exc:
            if logger_obj:
                logger_obj.warning(f"[positions_service] {exc}")
        except Exception as exc:
            if logger_obj:
                logger_obj.error(f"[positions_service] Failed to initialize account {account_key}: {exc}")
    return accounts

async def delete_heartbeat_on_ban():
    try :
        from ez_manage import delete_heartbeat_on_ban as manage_delete_heartbeat_on_ban
    except Exception:
        return
    try :
        await manage_delete_heartbeat_on_ban()
    except Exception:
        pass

def log_reduce_action(position_key, position_value_str, reduction_value_str, gain, reason, conviction=None, origin: str = "UNKNOWN", timestamp_3m=None, k_3m=None, k_3m_prv=None):
    safe = lambda x: f"{x:.2f}" if isinstance(x, (int, float)) and x is not None else "None"
    account_key = position_key.split(':')[0] if ':' in position_key else "unknown"
    current_account.set(account_key)
    conviction_str = f", Conviction: {conviction:.1f}" if conviction is not None else ""
    ts_str = f", ts_3m = {timestamp_3m}" if timestamp_3m else ""
    k_str = f", k_3m = {k_3m:.1f}" if k_3m is not None else ""
    k_prv_str = f", k_3m_prv = {k_3m_prv:.1f}" if k_3m_prv is not None else ""
    action_logger.info( f"{position_key}: M Position was reduced. Position Value before: {position_value_str}, Reduce by: {reduction_value_str}, Gain: {safe(gain)}{conviction_str}{ts_str}{k_str}{k_prv_str} . Reason: {reason} | origin={origin}", extra={ 'ticker': position_key, 'action': 'REDUCE', 'position_value_str': position_value_str, 'details': f"Reduce by {reduction_value_str}", 'conviction': conviction, 'origin': origin, 'timestamp_3m': timestamp_3m, 'k_3m': k_3m, 'k_3m_prv': k_3m_prv } )
@dataclass

class Signal:
    action: str 
    reason: str 
    conviction: float 
    quantity: float = 0.0
    reduction_amount: float = 0.0
    stop_levels: Optional[List[Dict[str, float]]] = None

def _convert_timestamp_strings_positions(data: Any) -> Any:
    """Recursively convert all timestamp strings in dict/list to datetime objects - CRITICAL for all JSON loads"""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(value, str) and any(ts_key in key.lower() for ts_key in ['time', 'timestamp', 'updated', 'at', 'date']):
                try :
                    if 'T' in value and ('Z' in value or '+' in value or value.count('-') >= 2):
                        from dateutil.parser import isoparse
                        parsed = isoparse(value)
                        result[key] = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
                    else:
                        result[key] = value
                except (ValueError, TypeError):
                    try :
                        parsed = pd.to_datetime(value, utc=True).to_pydatetime()
                        result[key] = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
                    except (ValueError, TypeError):
                        result[key] = value
            elif isinstance(value, (dict, list)):
                result[key] = _convert_timestamp_strings_positions(value)
            else:
                result[key] = value
        return result
    elif isinstance(data, list):
        return [_convert_timestamp_strings_positions(item) for item in data]
    return data
_POSITION_PROTECTED_FIELDS = frozenset({"entry_price", "max_gain", "last_reduction_price", "last_reduction_amount", "last_augmentation_price", "last_augmentation_amount", "initial_quantity", "max_quantity", "max_positionSize", "augment_reason", "reduction_reason", "opened_at", "last_augmentation_time", "last_reduction_time", "sba_add_count", "last_sba_time", "sba_total_added_usd", "entry_price_before_sba"})
_POSITION_SERVICE_ONLY_FIELDS = frozenset({"positionAmt", "entry_price", "last_reduction_price", "last_reduction_amount", "last_reduction_time", "last_augmentation_price", "last_augmentation_amount", "last_augmentation_time", "initial_quantity", "was_reduced", "is_reduced", "reduced_at", "was_reentered", "opened_at", "augment_reason", "reduction_reason"})
_POSITION_WRITE_ALLOWED_FILES = frozenset({"ez_positions_service.py", "ez_positions.py", "tradier_positions.py", "ez_manage.py", "ez_positions_quick.py", "tradier_manage.py"})

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
    max_positionSize: float
    opened_at: Optional[datetime]
    last_updated: Optional[datetime]
    last_signal: str
    realized_pnl: float = 0.0
    unrealized_pnl_USD: float = 0.0
    was_reentered: bool = False
    was_reduced: bool = False
    is_reduced: bool = False
    reduced_at: Optional[datetime] = None
    prev_gain_last_updated: Optional[datetime] = None
    augment_reason: str = ""
    reduction_reason: str = ""
    sba_add_count: int = 0  # BACKTEST_CHANGE_145: Strategic Bounce Averaging add count
    last_sba_time: float = 0.0  # BACKTEST_CHANGE_145: timestamp of last SBA add
    sba_total_added_usd: float = 0.0  # BACKTEST_CHANGE_145: cumulative USD added via SBA
    entry_price_before_sba: float = 0.0  # BACKTEST_CHANGE_145: original entry before any SBA
    is_hedge: bool = False
    hedge_for: Optional[str] = None
    r1_stop_price: float = 0.0

    def __setattr__(self, name, value):
        if name in _POSITION_SERVICE_ONLY_FIELDS and hasattr(self, name):
            old_val = object.__getattribute__(self, name)
            if old_val == value:
                object.__setattr__(self, name, value)
                return
            import traceback as _setattr_tb
            _frame = _setattr_tb.extract_stack(limit=6)
            _caller_file = _frame[-2].filename if len(_frame) >= 2 else ""
            _caller_basename = os.path.basename(_caller_file) if _caller_file else ""
            _caller_func = _frame[-2].name if len(_frame) >= 2 else ""
            if _caller_func == "__init__" or _caller_func == "from_dict":
                object.__setattr__(self, name, value)
                return
            if _caller_basename and _caller_basename not in _POSITION_WRITE_ALLOWED_FILES:
                _sym = getattr(self, 'symbol', '?')
                _side = getattr(self, 'position_side', '?')
                _crash_msg = f"\n{'!'*80}\n🚫 ILLEGAL POSITION WRITE from {_caller_basename}:{_caller_func}: {_sym}_{_side}.{name}: {old_val} → {value}\nCaller: {_frame[-2]}\nPID={os.getpid()}\n{'!'*80}\n"
                logger.critical(_crash_msg)
                try:
                    with open(os.path.expanduser("~/logs/ILLEGAL_POSITION_WRITE.log"), "a") as _ef:
                        _ef.write(f"[{datetime.now(timezone.utc).isoformat()}] {_crash_msg}\n")
                except Exception:
                    pass
                return
        if name in _POSITION_PROTECTED_FIELDS and hasattr(self, name):
            old_val = object.__getattribute__(self, name)
            _old_is_set = old_val is not None and old_val != "" and old_val != 0 and old_val != 0.0 and old_val != "0" and old_val != "None"
            _new_is_empty = value is None or value == "" or value == 0 or value == 0.0 or value == "0" or value == "None"
            if _old_is_set and _new_is_empty:
                import traceback as _setattr_tb2
                _stack = "".join(_setattr_tb2.format_stack()[-8:])
                _sym = getattr(self, 'symbol', '?')
                _side = getattr(self, 'position_side', '?')
                _crash_msg = f"\n{'!'*80}\n🚨 FIELD ERASURE IN-MEMORY: {_sym}_{_side}.{name}: {old_val} → {value}\nPID={os.getpid()}\nStack:\n{_stack}{'!'*80}\n"
                logger.critical(_crash_msg)
                try:
                    with open(os.path.expanduser("~/logs/FIELD_ERASURE_CRASH.log"), "a") as _ef:
                        _ef.write(_crash_msg)
                except Exception:
                    pass
                return
        object.__setattr__(self, name, value)
    mark_price_last_updated: Optional[datetime] = None
    @classmethod

    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        if not isinstance(data, dict):
            raise TypeError(f"Position.from_dict() requires a dict, got {type(data).__name__}")
        data = _convert_timestamp_strings_positions(data) 

        def safe_float(x):
            if x is None:
                return 0.0
            if isinstance(x, (int, float, Decimal)):
                return float(x)
            if isinstance(x, str):
                s = x.strip()
                if s == "": return 0.0
                s = s.replace(", ", "")
                try : return float(Decimal(s))
                except Exception: return 0.0
            if isinstance(x, dict):
                for k in ("amount", "qty", "positionAmt", "value"):
                    if k in x: return safe_float(x[k])
            try :
                return float(x)
            except (TypeError, ValueError):
                return 0.0

        def safe_time(x):
            if isinstance(x, datetime):
                return x
            if isinstance(x, str):
                try :
                    return pd.to_datetime(x, utc=True).to_pydatetime()
                except ValueError:
                    pass
            return None
        return cls( symbol=str(data.get("symbol", "")), position_side=str(data.get("position_side", "")), entry_price=safe_float(data.get("entry_price")), mark_price=safe_float(data.get("mark_price")), positionAmt=abs(safe_float(data.get("positionAmt")) or 0.0), initial_quantity=safe_float(data.get("initial_quantity")), gain=safe_float(data.get("gain")), max_gain=safe_float(data.get("max_gain")), prev_gain=safe_float(data.get("prev_gain")), max_quantity=safe_float(data.get("max_quantity")), last_augmentation_amount=safe_float(data.get("last_augmentation_amount")), last_augmentation_price=safe_float(data.get("last_augmentation_price")), last_augmentation_time=safe_time(data.get("last_augmentation_time")), last_reduction_amount=safe_float(data.get("last_reduction_amount")), last_reduction_price=safe_float(data.get("last_reduction_price")), last_reduction_time=safe_time(data.get("last_reduction_time")), max_positionSize=safe_float(data.get("max_positionSize")), opened_at=safe_time(data.get("opened_at")), last_updated=safe_time(data.get("last_updated")), last_signal=str(data.get("last_signal", "")), realized_pnl=(safe_float(data.get("realized_pnl"))), unrealized_pnl_USD=safe_float(data.get("unrealized_pnl_USD")), was_reentered=data.get("was_reentered", False), was_reduced=data.get("was_reduced", False), is_reduced=data.get("is_reduced", False), reduced_at=safe_time(data.get("reduced_at")), prev_gain_last_updated=safe_time(data.get("prev_gain_last_updated")), augment_reason=str(data.get("augment_reason", data.get("reason", ""))), reduction_reason=str(data.get("reduction_reason", data.get("reason", ""))), mark_price_last_updated=safe_time(data.get("mark_price_last_updated")), is_hedge=bool(data.get("is_hedge", False)), hedge_for=(str(data.get("hedge_for")) if data.get("hedge_for") else None), )

    def to_json(self) -> Dict[str, Any]:
        payload = asdict(self)
        for key, value in payload.items():
            if isinstance(value, datetime): payload[key] = value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if 'positionAmt' in payload:
            pos_amt_val = payload.get('positionAmt', 0) or 0
            payload['positionAmt'] = abs(safe_float(pos_amt_val, 0.0))
        return payload

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        now = datetime.now(timezone.utc)
        for key, value in result.items():
            if isinstance(value, datetime):
                result[key] = value.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            elif value is None:
                result[key] = None
        if 'positionAmt' in result:
            pos_amt_val = result.get('positionAmt', 0) or 0
            result['positionAmt'] = abs(safe_float(pos_amt_val, 0.0))
        if result.get('mark_price') and result.get('mark_price') > 0:
            mark_updated_str = result.get('mark_price_last_updated')
            if mark_updated_str:
                try :
                    mark_dt = datetime.fromisoformat(mark_updated_str.replace('Z', '+00:00'))
                    age_seconds = (now - mark_dt).total_seconds()
                    if age_seconds > 300: 
                        result['mark_price_last_updated'] = now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                except Exception:
                    result['mark_price_last_updated'] = now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            else:
                result['mark_price_last_updated'] = now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        return result
@dataclass(slots=True)

class ReentryPlan:
    position_key: str
    reentry_level: float
    reentry_amount: float
    reason: str
    updated_at: datetime
@dataclass(slots=True)

class LadderPlan:
    position_key: str
    levels: List[Dict[str, float]]
    created_at: datetime

class IndicatorSnapshot:

    def __init__(self, indicators: dict | None):
        self._data = indicators or {}
        self._symbol = None
        self._stale_timeframes = set()
        now = datetime.now(timezone.utc)
        limits = {'1m': 90, '3m': 300, '5m': 600, '15m': 1800, '1h': 5400, '4h': 16800, 'D': 86400}
        for tf, limit in limits.items():
            ts_key = f'timestamp_{tf}'
            if ts_key not in self._data:
                continue 
            val = self._data[ts_key]
            try :
                if isinstance(val, str):
                    if val.endswith('Z'): val = val[:-1] + '+00:00'
                    dt = datetime.fromisoformat(val)
                elif isinstance(val, (int, float)):
                    dt = datetime.fromtimestamp(val, tz=timezone.utc)
                else:
                    self._stale_timeframes.add(tf)
                    continue
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                if (now - dt).total_seconds() > limit:
                    self._stale_timeframes.add(tf)
            except Exception:
                self._stale_timeframes.add(tf)

    def get(self, key: str, default_value=None, symbol: str = None):
        """Ultra-fast access with pre-computed staleness checks."""
        for tf in self._stale_timeframes:
            if f'_{tf}' in key:
                return _get_smart_default(key)
        try :
            value = self._data.get(key, default_value)
            if value is None:
                return _get_smart_default(key)
            return value
        except Exception:
            return _get_smart_default(key)

    def many(self, *key_default_pairs, symbol: str = None):
        return tuple(self.get(key, default) for key, default in key_default_pairs)

    def tf(self, timeframe: str, keys_with_defaults: list[tuple[str, object]], symbol: str = None):
        return tuple(self.get(f"{name}_{timeframe}", default) for name, default in keys_with_defaults)

    def __contains__(self, key: str) -> bool: 
        return key in self._data

    def __getitem__(self, key: str): 
        return self.get(key)

    def items(self): return self._data.items()

    def keys(self): return self._data.keys()

    def values(self): return self._data.values()

    def __iter__(self): return iter(self._data)

    def __len__(self): return len(self._data)

class IndicatorsBridge:
    def __init__(self, service: "PositionService", *, logger: Optional[logging.Logger] = None, poll_interval: float = 1.0, cache_ttl: float = 10.0) -> None:
        self.service = service
        self.logger = logger or logging.getLogger("IndicatorsBridge")
        self.poll_interval = poll_interval
        self.cache_ttl = cache_ttl
        self._cold_data: Dict[str, Any] = {} 
        self._hot_data: Dict[str, Any] = {} 
        self.redis_manager: Optional[Any] = None
        self._lock = DummyLock()
        self._refresh_task: Optional[asyncio.Task] = None
        self._hot_path_task: Optional[asyncio.Task] = None
        self._cold_sync_task: Optional[asyncio.Task] = None
        self._initialized = False
        self._shutdown = False
        self.shared_proxy = None
        self._connect_shared_memory()
    
    @property
    def cache(self):
        """Allows PositionService to call .cache"""
        return self._cold_data

    @property
    def timestamp(self):
        """Allows PositionService to call .timestamp"""
        return getattr(self.service, 'indicators_timestamp', datetime.now(timezone.utc))

    async def _refresh_from_source(self, force: bool = False):
        """Implements the manual refresh call"""
        try:
            data_dir = getattr(config, 'DATA_DIR', Path('data'))
            candidates = list(data_dir.glob("market_data_*.json"))
            if candidates:
                candidates.sort(key=lambda p: p.name, reverse=True)
                target = candidates[0]
                async with aiofiles.open(target, 'r') as f:
                    content = await f.read()
                    if content:
                        import orjson
                        self._cold_data = orjson.loads(content) 
                        self.service.indicators_snapshot = self._cold_data
        except Exception as e:
            logger.error(f"IndicatorsBridge refresh failed: {e}")
    def _connect_shared_memory(self):
        try :
            from ez_share_ind import get_shared_memory_client
            client = get_shared_memory_client()
            if client:
                self.shared_proxy = client.get_store() 
        except Exception: pass

    async def initialize(self, force: bool = False, enable_auto_fetch: bool = True) -> None:
        if self._initialized: return
        try :
            from utils import get_simple_redis_manager, orjson_default
            self.redis_manager = await get_simple_redis_manager()
        except Exception: self.redis_manager = None
        self._refresh_task = asyncio.create_task(self._redis_fallback_loop())
        self._hot_path_task = asyncio.create_task(self._hot_path_loop())
        self._cold_sync_task = asyncio.create_task(self._cold_data_loop()) 
        self._initialized = True

    async def shutdown(self) -> None:
        self._shutdown = True
        for task in [self._refresh_task, self._hot_path_task, self._cold_sync_task]:
            if task: task.cancel()

    async def _hot_path_loop(self) -> None:
        """Reads High-Frequency data from Shared Memory."""
        while not self._shutdown:
            try :
                if self.shared_proxy is None: self._connect_shared_memory()
                updates = {}
                if self.shared_proxy:
                    try : 
                        updates = self.shared_proxy.get_all()
                    except Exception: 
                        self.shared_proxy = None
                if updates:
                    self._hot_data = updates
            except Exception: pass
            await asyncio.sleep(0.1)

    async def _redis_fallback_loop(self) -> None:
        """Reads from Redis if Shared Memory is failing."""
        while not self._shutdown:
            await asyncio.sleep(2.0)
            if not self.redis_manager: continue
            pass 

    async def _cold_data_loop(self) -> None:
        """Scans for the NEWEST market_data_*.json file."""
        last_loaded_file = None
        data_dir = getattr(config, 'DATA_DIR', Path('data'))
        while not self._shutdown:
            try :
                candidates = list(data_dir.glob("market_data_*.json"))
                target = None
                if candidates:
                    candidates.sort(key=lambda p: p.name, reverse=True)
                    target = candidates[0]
                else:
                    static = data_dir / "latest_market_data.json"
                    if static.exists(): target = static
                if target and target != last_loaded_file:
                    async with aiofiles.open(target, 'r') as f:
                        content = await f.read()
                        if content:
                            self._cold_data = orjson.loads(content) 
                            last_loaded_file = target
                            self.service.indicators_snapshot = self._cold_data
            except Exception as e:
                pass
            await asyncio.sleep(5)

    async def get(self, symbol: str, *, force_refresh: bool = False, required_indicators: Optional[List[str]] = None) -> Dict[str, Any]:
        symbol = symbol.strip().upper()
        data = self._cold_data.get(symbol, {}).copy()
        hot_item = self._hot_data.get(symbol)
        if not hot_item and self.redis_manager:
            try :
                raw = await self.redis_manager.get(f"hot_metrics:{symbol}")
                if raw: hot_item = orjson.loads(raw) 
            except Exception: pass
        if hot_item:
            mappings = { 'k_1m': 'stoch_k_1m', 'd1': 'stoch_d_1m', 'k_3m': 'stoch_k_3m', 'd3': 'stoch_d_3m', 'k_1m_p': 'k_1m_prev', 'd1p': 'd_1m_prev', 'k_3m_p': 'k_3m_prev', 'd3p': 'd_3m_prev', 'price': 'current_price' }
            for s_key, l_key in mappings.items():
                if s_key in hot_item:
                    val = hot_item[s_key]
                    data[l_key] = val
                    data[s_key] = val
            if '_tick_ts' in hot_item:
                ts = float(hot_item['_tick_ts'])
                iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                data['timestamp'] = iso
                data['timestamp_1m'] = iso
                data['timestamp_3m'] = iso
        fill_map = { 'k_1m_prev': 'k_1m_p', 'd_1m_prev': 'd1p', 'k_3m_prev': 'k_3m_p', 'd_3m_prev': 'd3p' }
        for l_key, s_key in fill_map.items():
            if l_key in data and s_key not in data:
                data[s_key] = data[l_key]
        return data

def ii(service, symbol: str) -> dict:
    ALIASES = { 'k_1m': 'stoch_k_1m', 'd1': 'stoch_d_1m', 'k_3m': 'stoch_k_3m', 'd3': 'stoch_d_3m', 'k_15m': 'stoch_k_15m', 'd_15m': 'stoch_d_15m' }
    bridge = getattr(service, 'indicators_bridge', None)
    data = {}
    if bridge:
        cold = bridge._cold_data.get(symbol, {})
        hot = bridge._hot_data.get(symbol, {})
        data = cold.copy()
        data.update(hot)
        if 'k_1m' in hot: data['stoch_k_1m'] = hot['k_1m']
        if 'd1' in hot: data['stoch_d_1m'] = hot['d1']
        if 'k_3m' in hot: data['stoch_k_3m'] = hot['k_3m']
        if 'd3' in hot: data['stoch_d_3m'] = hot['d3']

    class IndicatorDict(dict):

        def get(self, key: str, default=None):
            resolved_key = ALIASES.get(key, key)
            value = super().get(resolved_key, default)
            if value is None: 
                if 'stoch' in key or 'rsi' in key: return 50.0
                if 'price' in key: return 0.0
                return default
            return value
    return IndicatorDict(data)

def _get_recent_mark_price_from_positions(service, symbol: str, max_age_seconds: float = 180.0):
    if not service: return None, None
    positions_dict = getattr(service, 'positions', {})
    if not isinstance(positions_dict, dict) or not positions_dict: return None, None
    now = datetime.now(timezone.utc)
    recent_price = None
    recent_ts = None
    for position in positions_dict.values():
        if not position or getattr(position, 'symbol', '').upper() != symbol.upper(): continue
        price_val = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
        if price_val <= 0: continue
        ts_val = getattr(position, 'mark_price_last_updated', getattr(position, 'last_updated', None))
        if isinstance(ts_val, str):
            try : ts_val = isoparse(ts_val)
            except Exception: ts_val = None
        if ts_val and ts_val.tzinfo is None: ts_val = ts_val.replace(tzinfo=timezone.utc)
        if not ts_val: continue
        if (now - ts_val).total_seconds() > max_age_seconds: continue
        if recent_ts is None or ts_val > recent_ts:
            recent_price = price_val
            recent_ts = ts_val
    return recent_price, recent_ts

async def fetch_fresh_snapshot_for_symbol(service, symbol: str, required_indicators: list = None, force_refresh: bool = False, _skip_bridge: bool = False) -> dict:
    symbol = symbol.strip().upper()
    bridge = getattr(service, 'indicators_bridge', None)
    if bridge:
        payload = await bridge.get(symbol)
        if payload:
            return _finalize_indicator_payload(payload, required_indicators)
    if hasattr(service, 'indicators_snapshot'):
        snap = service.indicators_snapshot.get(symbol)
        if snap:
            return _finalize_indicator_payload(snap, required_indicators)
    return {}

def _compare_and_merge_market_data_freshness(symbol: str, primary_data: dict, secondary_data: dict, source_name: str = "secondary") -> dict:
    """Compare freshness of market data from two sources and merge the fresher values. Uses timestamp_<timeframe> from both sources to determine freshness. Returns merged dict with fresher values for each timeframe."""
    if not secondary_data or not isinstance(secondary_data, dict):
        return primary_data
    if not primary_data:
        primary_data = {}
    try :
        from datetime import datetime, timezone

        from dateutil.parser import isoparse
        merged = primary_data.copy()
        timeframes = ['3m', '15m', '1h', '4h', 'D']
        for tf in timeframes:
            primary_ts_key = f'timestamp_{tf}'
            secondary_ts_key = f'timestamp_{tf}'
            primary_ts = None
            secondary_ts = None
            if primary_ts_key in primary_data and primary_data[primary_ts_key]:
                try :
                    normalized_primary = _normalize_timestamp_to_z(primary_data[primary_ts_key]) if isinstance(primary_data[primary_ts_key], str) else primary_data[primary_ts_key]
                    primary_ts = isoparse(normalized_primary) if isinstance(normalized_primary, str) else normalized_primary
                    if primary_ts.tzinfo is None:
                        primary_ts = primary_ts.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            if secondary_ts_key in secondary_data and secondary_data[secondary_ts_key]:
                try :
                    normalized_secondary = _normalize_timestamp_to_z(secondary_data[secondary_ts_key]) if isinstance(secondary_data[secondary_ts_key], str) else secondary_data[secondary_ts_key]
                    secondary_ts = isoparse(normalized_secondary) if isinstance(normalized_secondary, str) else normalized_secondary
                    if secondary_ts.tzinfo is None:
                        secondary_ts = secondary_ts.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            use_secondary = False
            if secondary_ts and primary_ts:
                use_secondary = secondary_ts >= primary_ts
            elif secondary_ts and not primary_ts:
                use_secondary = True
            if use_secondary:
                tf_prefixes = [f'stoch_k_{tf}', f'stoch_d_{tf}', f'dc_high_{tf}', f'dc_low_{tf}', f'wt1_{tf}', f'wt2_{tf}', f'rsi_{tf}', f'mfi_{tf}', f'atr_{tf}', f'ha_{tf}', f'sma_200_{tf}', f'ema_20_{tf}', f'ema_50_{tf}', f'close_{tf}', f'high_{tf}', f'low_{tf}']
                for key_prefix in tf_prefixes:
                    if key_prefix in secondary_data:
                        merged[key_prefix] = secondary_data[key_prefix]
                    key_prev = f'{key_prefix}_prev'
                    if key_prev in secondary_data:
                        merged[key_prev] = secondary_data[key_prev]
                if secondary_ts_key in secondary_data:
                    merged[primary_ts_key] = secondary_data[secondary_ts_key]
        return merged
    except Exception as e:
        logger.debug(f"Error comparing market data freshness for {symbol}: {e}")
        return primary_data

async def load_json_safe(file_path: str | Path, account_key: str = None) -> dict:
    file_path = Path(file_path)
    backup_path = file_path.with_suffix(".bak")

    async def write_clean_json(data: dict):
        try :
            asyncio.create_task(atomic_write_json(file_path, data))
        except Exception as e:
            logger.error(f"Failed to write cleaned JSON to {file_path}: {e}")

    def _attempt_repair(content_str: str):
        stripped = content_str.strip()
        if not stripped:
            return None
        decoder = json.JSONDecoder()
        try :
            repaired_obj, _ = decoder.raw_decode(stripped)
            if repaired_obj: 
                return repaired_obj
        except json.JSONDecodeError:
            pass
        for idx in range(len(stripped) - 1, 0, -1):
            candidate = stripped[:idx].rstrip()
            if not candidate or candidate[-1] not in '}': 
                continue
            try :
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    def _collect_backup_candidates() -> List[Path]:
        candidates: List[Path] = []
        if backup_path.exists():
            candidates.append(backup_path)
        backups_dir = file_path.parent / "backups"
        if backups_dir.exists() and backups_dir.is_dir():
            try :
                found = []
                for entry in backups_dir.iterdir():
                    if entry.is_file() and entry.name.startswith(file_path.stem) and entry.suffix.lower() == ".json":
                        found.append(entry)
                found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                candidates.extend(found)
            except Exception as scan_err:
                logger.debug(f"Backup scan failed for {backups_dir}: {scan_err}")
        return candidates
    content_bytes = None
    async with _get_file_write_semaphore():
        if not file_path.exists():
            return {}
        try :
            async with aiofiles.open(file_path, "rb") as f:
                content_bytes = await f.read()
        except Exception as e:
            logger.error(f"[load_json_safe] IO Error reading {file_path}: {e}")
            return {}
    if not content_bytes:
        return {}
    data = None
    try :
        data = safe_json_loads(content_bytes)
    except (json.JSONDecodeError, Exception):
        try :
            text_content = content_bytes.decode('utf-8', errors='ignore')
            data = json.loads(text_content)
        except Exception:
            logger.warning(f"[load_json_safe] Parsing failed for {file_path}. Attempting repairs...")
            text_content = content_bytes.decode('utf-8', errors='ignore')
            data = _attempt_repair(text_content)
            if data is None:
                logger.error(f"[load_json_safe] Repair failed for {file_path}. Scanning backups...")
                for candidate in _collect_backup_candidates():
                    try :
                        async with aiofiles.open(candidate, "rb") as f:
                            bk_content = await f.read()
                        try :
                            data = safe_json_loads(bk_content)
                        except Exception:
                            data = _attempt_repair(bk_content.decode('utf-8', errors='ignore'))
                        if isinstance(data, dict):
                            logger.info(f"[load_json_safe] RECOVERED data from backup: {candidate}")
                            await write_clean_json(data) 
                            break
                    except Exception as recover_err:
                        logger.debug(f"Backup recovery failed for {candidate}: {recover_err}")
    if not isinstance(data, (dict, list)):
        logger.error(f"[load_json_safe] Critical: Could not recover valid JSON for {file_path}. Resetting to empty.")
        await write_clean_json({})
        return {} 
    if account_key and isinstance(data, dict):
        fixed_data = {}
        modified = False
        for key, value in data.items():
            if not key.startswith(f"{account_key}:"):
                new_key = f"{account_key}:{key}"
                fixed_data[new_key] = value
                modified = True
            else:
                fixed_data[key] = value
        if modified:
            await write_clean_json(fixed_data)
        return fixed_data
    return data

def _refresh_last_events_cache():
    """Refresh the in-memory cache of last_events.json data."""
    global _last_events_cache, _last_events_cache_time
    try :
        last_events_path = os.path.join('data', 'last_events.json')
        if not os.path.exists(last_events_path):
            logger.warning(f"WARNING: last_events.json not found at {last_events_path}")
            _last_events_cache = {}
            _last_events_cache_time = time.time()
            return
        with open(last_events_path, "r", encoding="utf-8") as f:
            _last_events_cache = json.load(f)
        _last_events_cache_time = time.time()
        logger.debug(f"Refreshed last_events.json cache with {len(_last_events_cache)} symbols")
    except Exception as e:
        logger.warning(f"WARNING: Error refreshing last_events.json cache: {e}")
        _last_events_cache = {}
        _last_events_cache_time = time.time()

def force_refresh_last_events_cache():
    """Force refresh the last_events.json cache (call this on listener signals)."""
    global _last_events_cache_time
    _last_events_cache_time = 0 
    _refresh_last_events_cache()

def _inject_stoch_rsi_from_last_events(symbol: str, indicators_dict: dict) -> dict:
    """Inject stoch_k and stoch_d values from last_events, comparing freshness with ez_indicators data."""
    global _last_events_cache, _last_events_cache_time, _last_events_cache_interval
    try :
        from datetime import datetime, timezone

        from dateutil.parser import isoparse
        current_time = time.time()
        if (_last_events_cache is None or current_time - _last_events_cache_time > _last_events_cache_interval):
            _refresh_last_events_cache()
        if not symbol or not _last_events_cache or symbol not in _last_events_cache:
            return indicators_dict
        symbol_data = _last_events_cache[symbol]
        if not isinstance(symbol_data, dict):
            return indicators_dict
        for tf in ['3m', '15m', '1h', '4h', 'D']:
            if tf not in symbol_data:
                continue
            tf_data = symbol_data[tf]
            if not isinstance(tf_data, dict):
                continue
            last_events_ts = None
            if 'stoch_timestamp' in tf_data and tf_data['stoch_timestamp']:
                try :
                    normalized_stoch = _normalize_timestamp_to_z(tf_data['stoch_timestamp']) if isinstance(tf_data['stoch_timestamp'], str) else tf_data['stoch_timestamp']
                    last_events_ts = isoparse(normalized_stoch) if isinstance(normalized_stoch, str) else normalized_stoch
                    if last_events_ts.tzinfo is None:
                        last_events_ts = last_events_ts.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            indicators_ts = None
            indicators_stoch_ts_key = f'stoch_timestamp_{tf}'
            if indicators_stoch_ts_key in indicators_dict and indicators_dict[indicators_stoch_ts_key]:
                try :
                    normalized_stoch_indicators = _normalize_timestamp_to_z(indicators_dict[indicators_stoch_ts_key]) if isinstance(indicators_dict[indicators_stoch_ts_key], str) else indicators_dict[indicators_stoch_ts_key]
                    indicators_ts = isoparse(normalized_stoch_indicators) if isinstance(normalized_stoch_indicators, str) else normalized_stoch_indicators
                    if indicators_ts.tzinfo is None:
                        indicators_ts = indicators_ts.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            if not indicators_ts:
                indicators_ts_key = f'timestamp_{tf}'
                if indicators_ts_key in indicators_dict and indicators_dict[indicators_ts_key]:
                    try :
                        normalized_indicators = _normalize_timestamp_to_z(indicators_dict[indicators_ts_key]) if isinstance(indicators_dict[indicators_ts_key], str) else indicators_dict[indicators_ts_key]
                        indicators_ts = isoparse(normalized_indicators) if isinstance(normalized_indicators, str) else normalized_indicators
                        if indicators_ts.tzinfo is None:
                            indicators_ts = indicators_ts.replace(tzinfo=timezone.utc)
                    except Exception:
                        pass
            use_last_events = True
            if last_events_ts and indicators_ts:
                use_last_events = last_events_ts >= indicators_ts
            elif not last_events_ts and indicators_ts:
                use_last_events = False
            elif last_events_ts and not indicators_ts:
                use_last_events = True
            if use_last_events:
                if 'stoch_k' in tf_data and tf_data['stoch_k'] is not None:
                    indicators_dict[f'stoch_k_{tf}'] = tf_data['stoch_k']
                if 'stoch_d' in tf_data and tf_data['stoch_d'] is not None:
                    indicators_dict[f'stoch_d_{tf}'] = tf_data['stoch_d']
                if 'stoch_k_prev' in tf_data and tf_data['stoch_k_prev'] is not None:
                    indicators_dict[f'stoch_k_{tf}_prev'] = tf_data['stoch_k_prev']
                if 'stoch_d_prev' in tf_data and tf_data['stoch_d_prev'] is not None:
                    indicators_dict[f'stoch_d_{tf}_prev'] = tf_data['stoch_d_prev']
            else:
                logger.debug(f"Using ez_indicators stoch data for {symbol}_{tf} (ez_indicators ts: {indicators_ts}, last_events ts: {last_events_ts})")
    except Exception as e:
        logger.debug(f"Error injecting stoch RSI from last_events for {symbol}: {e}")
    return indicators_dict

async def get_reentry_data(symbol, account_key, position, service):
    position_key = construct_position_key(account_key, position.symbol, position.position_side)
    reentry_data_dict = getattr(service, "reentry_data", {}) if isinstance(getattr(service, "reentry_data", {}), dict) else {}
    reentry_data_raw = reentry_data_dict.get(position_key)
    reentry_data = reentry_data_raw if isinstance(reentry_data_raw, dict) else {}
    position_reduction_amount = position.last_reduction_amount or 0.0
    position_reduction_price = position.last_reduction_price or 0.0
    position_reduction_time = safe_datetime(position.last_reduction_time)
    reentry_reduction_amount = safe_float(reentry_data.get("reentry_amount", 0.0))
    reentry_reduction_price = safe_float(reentry_data.get("reentry_level", 0.0))
    timestamp_value = reentry_data.get("timestamp")
    if isinstance(timestamp_value, dict):
        timestamp_value = timestamp_value.get("timestamp") or timestamp_value.get("time") or timestamp_value.get("created_at") or timestamp_value.get("updated_at")
    if not timestamp_value:
        timestamp_value = position.last_reduction_time
    reentry_reduction_time = safe_datetime(timestamp_value)
    if position.position_side == "LONG":
        final_reduction_price = min(position_reduction_price, reentry_reduction_price) if reentry_reduction_price > 0 else position_reduction_price
    else:
        final_reduction_price = max(position_reduction_price, reentry_reduction_price) if reentry_reduction_price > 0 else position_reduction_price
    final_reduction_amount = max(position_reduction_amount, reentry_reduction_amount)
    reentry_timestamp = max(filter(None, [position_reduction_time, reentry_reduction_time])) if reentry_reduction_time else position_reduction_time
    reentry_level = final_reduction_price
    fallback_qty = config.START_POSITION_SIZE / max(position.mark_price, 1e-6)
    max_qty_safe = position.max_quantity
    reentry_amount = final_reduction_amount if final_reduction_amount > 0 else min(max_qty_safe, fallback_qty)
    return {"level": reentry_level, "amount": reentry_amount, "timestamp": reentry_timestamp, "reason": str(reentry_data.get("reason", ""))}
_ipv4_lock = threading.Lock()

def _force_ipv4_for_client(client: Client):
    """Force IPv4 for Binance client to avoid [Errno 22] Invalid argument in socket.getaddrinfo"""
    import socket

    def create_connection_v4(address, *args, **kwargs):
        host, port = address
        for res in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            sock = None
            try :
                sock = socket.socket(af, socktype, proto)
                sock.connect(sa)
                return sock
            except OSError:
                if sock: sock.close()
                continue
        raise OSError(f"Unable to create IPv4 connection to {host}:{port}")

    class IPv4HTTPAdapter(HTTPAdapter):

        def init_poolmanager(self, *args, **kwargs):
            import urllib3.util.connection
            with _ipv4_lock:
                original_create_connection = urllib3.util.connection.create_connection
                urllib3.util.connection.create_connection = create_connection_v4
                try :
                    return super().init_poolmanager(*args, **kwargs)
                finally:
                    urllib3.util.connection.create_connection = original_create_connection

        def proxy_manager_for(self, *args, **kwargs):
            import urllib3.util.connection
            with _ipv4_lock:
                original_create_connection = urllib3.util.connection.create_connection
                urllib3.util.connection.create_connection = create_connection_v4
                try :
                    return super().proxy_manager_for(*args, **kwargs)
                finally:
                    urllib3.util.connection.create_connection = original_create_connection
    adapter = IPv4HTTPAdapter(max_retries=3)
    client.session.mount("https://", adapter)
    client.session.mount("http://", adapter)

class WebSocketManager:

    def __init__(self, account_keys, api_key: str, api_secret: str, config, symbols=None, service=None):
        self.account_keys = account_keys if isinstance(account_keys, list) else [account_keys]
        self.api_key = api_key
        self.api_secret = api_secret
        self.config = config
        self.symbols = symbols
        self.service = service
        self.client = None
        self.bm = None
        self._running = True
        self.semaphore = Semaphore(config.EZ_MANAGE_WS_SEMAPHORE.get(current_env['env'], 140))
        self.ws_managers = []
        self.positions = self.service.positions if self.service else {}
        self.price_cache = self.service.price_cache if self.service else {}
        self.price_update_time = self.service.price_update_time if self.service else {}
        self.markprice_lock = DummyLock()
        self._price_cache_lock = DummyLock()
        self.listen_keys = {}
        self.positions_by_account = self.service.positions_by_account if self.service else {}
        self.time_offset = 0
        self.is_primary = False
        self._mark_price_session: Optional[aiohttp.ClientSession] = None
        self._mark_price_task: Optional[asyncio.Task] = None
        self._fallback_task: Optional[asyncio.Task] = None
        self._guard_task: Optional[asyncio.Task] = None
        # 2026-05-09: REST poll fallback for when Binance WS is silent (connect-but-no-messages).
        # Confirmed empirically (Mac AND S1, both continents): WS to !markPrice@arr accepts the
        # connection then sends 0 messages. Account-level shadow-throttle most likely. REST endpoint
        # /fapi/v1/premiumIndex still works fine, so we poll it every ~2s and route through
        # _apply_mark_price (which already does the in-memory + Redis write + pubsub).
        self._rest_poll_task: Optional[asyncio.Task] = None
        self._mark_price_stop = False
        self._fallback_interval = float(getattr(self.config, "PRICE_FALLBACK_INTERVAL", 1.0))
        self._mark_price_guard_interval = float(getattr(self.config, "MARK_PRICE_GUARD_INTERVAL", 1.0))
        self._mark_price_max_staleness = float(getattr(self.config, "MARK_PRICE_MAX_STALENESS", 2.0))
        self._price_cache_files = tuple(dict.fromkeys([self.config.PRICE_CACHE_FILE, getattr(self.config, "PRICE_CACHE_FILE_2", self.config.PRICE_CACHE_FILE), getattr(self.config, "PRICE_CACHE_FILE_3", self.config.PRICE_CACHE_FILE)]))
        self._price_cache_file_data: Dict[Path, Dict[str, Any]] = {}
        self._price_cache_file_mtime: Dict[Path, float] = {}

    async def _init_session(self) -> aiohttp.ClientSession:
        timeout = aiohttp.ClientTimeout(total=120, connect=60, sock_read=60, sock_connect=60)
        connector = aiohttp.TCPConnector(limit=120, limit_per_host=60, ttl_dns_cache=300, keepalive_timeout=90, enable_cleanup_closed=True, force_close=False)
        return aiohttp.ClientSession(connector=connector, timeout=timeout)

    def _init_binance_client_blocking(self):
        try :
            client = Client(api_key=self.api_key, api_secret=self.api_secret)
            try :
                from utils import _force_ipv4_for_client, orjson_default
                _force_ipv4_for_client(client)
            except ImportError:
                pass
            server_time = client.futures_time()['serverTime']
            diff = server_time - int(time.time() * 1000)
            client._timestamp_offset = diff
            return client
        except Exception as e:
            logger.warning(f"[_init_binance_client] Failed: {e}")
            raise

    async def start(self, account_key: str):
        """Start WebSocket for ONE specific account with robust retry loop"""
        if account_key not in self.account_keys:
            logger.error(f"[WebSocketManager.start] account_key {account_key} not in self.account_keys")
            return
        logger.info(f"[{account_key}] 🟢 Starting WebSocket Manager Loop")
        backoff = 2
        while self._running:
            try :
                if not self.client:
                    try :
                        self.client = await asyncio.to_thread(self._init_binance_client_blocking)
                    except Exception as client_err:
                        logger.warning(f"[{account_key}] Client init failed (API 503/Timeout?): {client_err}. Retrying in {backoff}s...")
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 1.5, 60)
                        continue
                ban_rem = _ban_remaining_seconds()
                if ban_rem > 0:
                    sleep_for = min(ban_rem + 2, 60.0)
                    _log_ban_skip_throttled(logger, account_key, f"deferring initial listen-key fetch ({sleep_for:.0f}s)")
                    await asyncio.sleep(sleep_for)
                    continue
                auth_rem = _auth_ban_remaining(account_key)
                if auth_rem > 0:
                    sleep_for = min(auth_rem + 2, _AUTH_BAN_COOLDOWN_SEC)
                    _log_auth_ban_throttled(logger, account_key, f"deferring initial listen-key fetch ({sleep_for:.0f}s)")
                    await asyncio.sleep(sleep_for)
                    continue
                try :
                    listen_key = await asyncio.to_thread(self.client.futures_stream_get_listen_key)
                    self.listen_keys[account_key] = listen_key
                    backoff = 2
                except Exception as key_err:
                    _record_ip_ban_from_exc(key_err)
                    if _record_auth_ban(account_key, key_err):
                        _log_auth_ban_throttled(logger, account_key, f"initial listen-key fetch -2015 — pausing {_AUTH_BAN_COOLDOWN_SEC:.0f}s")
                        await asyncio.sleep(_AUTH_BAN_COOLDOWN_SEC)
                        continue
                    logger.warning(f"[{account_key}] Listen key fetch failed: {key_err}. Retrying in {backoff}s...")
                    if "client" in str(key_err).lower() or "object" in str(key_err).lower():
                        self.client = None
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.5, 60)
                    continue
                if not getattr(self, 'session', None) or self.session.closed:
                    self.session = await self._init_session()
                # 2026-05-10: REVERTED to /ws/{lk}. Binance's own Python SDK (binance-futures-connector-python) still uses /ws/{listen_key}.
                # Earlier I tried /private/{lk} based on a blog post — both /private/{lk} and /private/stream?streams={lk} delivered 0 messages.
                # The user-data WS dying may be a Binance-side or account-level issue, not a URL change. Falling back to REST polling
                # (position.mark_price + fetch_positions every 4-6s) keeps positions current; this is the "as always" backup pattern.
                user_url = f"wss://fstream.binance.com/ws/{listen_key}"
                logger.info(f"[{account_key}] 🔌 Connecting WS to {user_url[:30]}...")
                keepalive_task = asyncio.create_task(self.keep_listen_key_alive(account_key))
                asyncio.create_task(self.ensure_mark_price_tasks())
                await self.user_data_websocket_loop(user_url, account_key)
                keepalive_task.cancel()
                logger.warning(f"[{account_key}] ⚠️ WS Loop exited. Re-initializing in 2s...")
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                logger.info(f"[{account_key}] WebSocket Manager cancelled")
                break
            except Exception as e:
                logger.error(f"[{account_key}] Critical WS Manager Loop Crash: {e}", exc_info=True)
                await asyncio.sleep(5)

    def sync_binance_time(self):
        pass

    async def user_data_websocket_loop(self, url, account_key):
        logger.debug(f"[{account_key}] Starting user data websocket loop")
        reconnect_delay = 2
        max_reconnect_delay = 30
        while self._running:
            try :
                if self.session.closed:
                    self.session = await self._init_session()
                if _ban_remaining_seconds() > 0:
                    _log_ban_skip_throttled(logger, account_key, "skipping fresh listen-key on reconnect, reusing URL")
                elif _auth_ban_remaining(account_key) > 0:
                    _log_auth_ban_throttled(logger, account_key, "skipping fresh listen-key on reconnect, reusing URL")
                else:
                    try :
                        new_listen_key = await asyncio.to_thread(self.client.futures_stream_get_listen_key)
                        self.listen_keys[account_key] = new_listen_key
                        # 2026-05-10: REVERTED to /ws/{lk} (see comment at user_url above)
                        url = f"wss://fstream.binance.com/ws/{new_listen_key}"
                        logger.info(f"[{account_key}] 🔑 Fresh listen key obtained for reconnect")
                    except Exception as lk_err:
                        _record_ip_ban_from_exc(lk_err)
                        if _record_auth_ban(account_key, lk_err):
                            _log_auth_ban_throttled(logger, account_key, "fresh-listen-key POST -2015 on reconnect — reusing existing URL")
                        else:
                            logger.warning(f"[{account_key}] Failed to get fresh listen key: {lk_err} - using existing URL")
                async with self.session.ws_connect(url, heartbeat=15, timeout=30) as ws:
                    logger.info(f"[{account_key}] ✅ WebSocket connected!")
                    reconnect_delay = 2
                    # Wrap last_message_time in a list so the watchdog closure can mutate it
                    # via index assignment without needing `nonlocal` (cleaner than rewriting the
                    # outer assignments below).
                    lmt = [time.time()]
                    if self.service and hasattr(self.service, '_ws_update_timestamps'):
                        self.service._ws_update_timestamps[f"{account_key}_connected"] = time.time()

                    async def message_watchdog():
                        # 2026-04-30: prior watchdog reset the WS whenever no user-data
                        # message arrived for 60s. But Binance USER-DATA streams are
                        # naturally silent unless the account has an order/balance/margin
                        # event — quiet periods of 60-300s are normal even on busy accounts.
                        # The blind reset thrashed every account ~once a minute (29
                        # resets/30min per account), losing real events during the ~3s
                        # reconnect window AND wasting POST listenKey calls. Now: when
                        # silence > 60s, FIRST probe the listen key with PUT keepalive.
                        # If keepalive succeeds the connection is healthy, account is just
                        # quiet — extend tolerance to 10min and reset the timer. Only force
                        # WS teardown when the probe FAILS (key truly invalid) or silence
                        # exceeds the 10-min hard cap.
                        idle_probe_ok_count = 0
                        while self._running:
                            await asyncio.sleep(30)
                            silence = time.time() - lmt[0]
                            if silence <= 60:
                                idle_probe_ok_count = 0
                                continue
                            lk = self.listen_keys.get(account_key)
                            probe_ok = False
                            ban_rem = _ban_remaining_seconds()
                            if ban_rem > 0:
                                # IP-banned: probing & resetting just adds load to the same banned bucket.
                                # The existing WS may still be alive (Binance keeps it open even when
                                # listenKey HTTP is rate-limited). Sit tight until the ban clears.
                                _log_ban_skip_throttled(logger, account_key, f"deferring WS reset ({ban_rem:.0f}s remaining, silence={silence:.0f}s)")
                                lmt[0] = time.time()
                                continue
                            auth_rem = _auth_ban_remaining(account_key)
                            if auth_rem > 0:
                                _log_auth_ban_throttled(logger, account_key, f"deferring WS reset (silence={silence:.0f}s)")
                                lmt[0] = time.time()
                                continue
                            if lk and self.client:
                                try:
                                    await asyncio.wait_for(asyncio.to_thread(self.client.futures_stream_keepalive, lk), timeout=25.0)
                                    probe_ok = True
                                except asyncio.TimeoutError:
                                    # 2026-05-09: timeout != bad listen key. With 5 accounts probing through a
                                    # shared to_thread executor, the keepalive HTTP can queue behind other calls
                                    # (network latency to Binance from this Mac is ~770ms — 25s should always
                                    # be plenty). Treat timeout as a transient blip: do NOT reset the WS, just
                                    # extend the silence tolerance and try again next cycle. Resetting on every
                                    # 60s silence used to thrash the listen-key endpoint (we'd been seeing
                                    # ws_alive=False forever on all 5 crypto accounts).
                                    logger.warning(f"[{account_key}] Listen-key probe TIMEOUT during {silence:.0f}s WS silence — transient (network/executor), NOT resetting WS")
                                    lmt[0] = time.time()
                                    continue
                                except Exception as probe_err:
                                    _record_ip_ban_from_exc(probe_err)
                                    if _record_auth_ban(account_key, probe_err):
                                        _log_auth_ban_throttled(logger, account_key, f"probe failed during {silence:.0f}s WS silence — skipping reset")
                                        lmt[0] = time.time()
                                        continue
                                    logger.warning(f"[{account_key}] Listen-key probe failed during {silence:.0f}s WS silence: {probe_err!r}")
                            if probe_ok and silence < 600:
                                idle_probe_ok_count += 1
                                if idle_probe_ok_count == 1 or idle_probe_ok_count % 10 == 0:
                                    logger.info(f"[{account_key}] WS silent {silence:.0f}s but listen key valid — idle stream (probes_ok={idle_probe_ok_count})")
                                lmt[0] = time.time()
                                continue
                            if _ban_remaining_seconds() > 0:
                                # Probe just hit a fresh -1003 — defer reset.
                                _log_ban_skip_throttled(logger, account_key, "probe hit -1003, deferring WS reset")
                                lmt[0] = time.time()
                                continue
                            logger.warning(f"[{account_key}] WS frozen (no msg > {silence:.0f}s, probe={'ok' if probe_ok else 'FAIL'}). Resetting with fresh listen key.")
                            self.listen_keys.pop(account_key, None)
                            try : await ws.close()
                            except Exception: pass
                            break
                    watchdog_task = asyncio.create_task(message_watchdog())
                    try :
                        async for message in ws:
                            if not self._running: break
                            lmt[0] = time.time()
                            if self.service and hasattr(self.service, '_ws_update_timestamps'):
                                self.service._ws_update_timestamps[account_key] = time.time()
                            if message.type == aiohttp.WSMsgType.TEXT:
                                try :
                                    data = safe_json_loads(message.data)
                                    event_type = data.get("e")
                                    if event_type == "ACCOUNT_UPDATE":
                                        # CRASH-ON-HANG: handle_account_update feeds the same handle_ chain
                                        # as process_account_update. If it stalls > 60s the WS appears alive
                                        # (timestamp updated above) but no further messages get processed
                                        # AND REST fallback never kicks in — silent death. Crash so watchdog respawns.
                                        try:
                                            await asyncio.wait_for(self.handle_account_update(data, account_key), timeout=60.0)
                                        except asyncio.TimeoutError:
                                            logger.critical(f"[WS][{account_key}] 🚨🚨🚨 handle_account_update HUNG > 60s — CRASHING worker so watchdog respawns.")
                                            try: sys.stdout.flush(); sys.stderr.flush()
                                            except Exception: pass
                                            os._exit(44)
                                    elif event_type == "listenKeyExpired":
                                        logger.warning(f"[{account_key}] Listen key expired. Triggering full restart.")
                                        return
                                except Exception as e:
                                    logger.error(f"[{account_key}] Msg parse error: {e}")
                            elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning(f"[{account_key}] WS Closed/Error. Reconnecting.")
                                break
                    finally:
                        watchdog_task.cancel()
            except Exception as e:
                logger.error(f"[{account_key}] WS Connection error: {e}. Retry in {reconnect_delay}s")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            if not self._running: return

    async def handle_account_update(self, data: dict, account_key: str):
        logger.warning(f"[WS_ARRIVAL][{account_key}] 🚨🚨🚨 WebSocket message ARRIVED - data keys: {list(data.keys()) if data else 'None'}")
        try :
            update = data.get("a", {})
            payloads = update.get("P", [])
            if not payloads:
                logger.debug(f"[handle_account_update][{account_key}] ACCOUNT_UPDATE received but no position payloads (P) found. Update keys: {update.keys() if update else 'None'}")
                return
            logger.warning(f"[handle_account_update][{account_key}] 🔄🔔🔔🔔 WebSocket ACCOUNT_UPDATE: Processing {len(payloads)} position updates")
            now = datetime.now(timezone.utc)
            event_ts: datetime = now
            event_ts_raw = data.get("E") or data.get("eventTime")
            if event_ts_raw:
                try :
                    event_ts = ensure_tz(datetime.fromtimestamp(event_ts_raw / 1000, tz=timezone.utc))
                except Exception:
                    event_ts = now

            async def _resolve_current_price(position_obj, sym: str) -> Tuple[float, Optional[datetime]]:
                if position_obj and getattr(position_obj, 'mark_price', 0):
                    try :
                        candidate = float(position_obj.mark_price)
                        if candidate > 0:
                            ts = getattr(position_obj, 'mark_price_last_updated', None)
                            if ts:
                                return candidate, ensure_tz(ts) if isinstance(ts, datetime) else ts
                            return candidate, None
                    except (TypeError, ValueError):
                        pass
                try :
                    if self.service and hasattr(self.service, 'get_current_price'):
                        price, price_ts = await self.service.get_current_price(sym)
                        if price and price > 0:
                            return price, price_ts
                except Exception:
                    pass
                try :
                    price = coerce_price_value(await quick_price(sym))
                    if price and price > 0:
                        return price, datetime.now(timezone.utc)
                except Exception:
                    pass
                return 0.0, None
            broadcast_sides: Set[str] = set()
            updated_position_keys: Set[str] = set()
            if not hasattr(self.service, '_positions_update_lock'):
                self.service._positions_update_lock = asyncio.Lock()
            async with self.service._positions_update_lock:
                account_positions = self.service.positions_by_account.setdefault(account_key, {})
            for pos_update in payloads:
                symbol = pos_update.get("s")
                if not symbol:
                    logger.debug(f"[{account_key}] Ignoring WS payload without symbol: {pos_update}")
                    continue
                symbol = symbol.strip().upper()
                side = (pos_update.get("ps") or "BOTH").upper()
                broadcast_sides.add(side)
                raw_pa = pos_update.get("pa")
                if raw_pa is None or raw_pa == "":
                    logger.debug(f"[{account_key}] WS position update for {symbol} has no 'pa' field — skipping (would falsely zero position)")
                    continue
                raw_amt = safe_fetch_float(raw_pa)
                if raw_amt is None:
                    continue
                amt_abs = abs(raw_amt)
                position_key = construct_position_key(account_key, symbol, side)
                expected_prefix = f"{account_key}:"
                if not isinstance(position_key, str) or not position_key.startswith(expected_prefix):
                    logger.error(f"[handle_account_update][{account_key}] 🚨 REJECTED position with wrong account prefix: {position_key} (expected {expected_prefix}*)")
                    continue
                existing_position = account_positions.get(position_key)
                if not existing_position:
                    logger.warning(f"[handle_account_update][{account_key}] ⚠️ Position {position_key} not found in memory, ensuring it exists...")
                    existing_position = await self.service.ensure_position_present(account_key, symbol, side, position_key)
                    if not existing_position:
                        logger.error(f"[handle_account_update][{account_key}] Failed to ensure position {position_key} exists, skipping update")
                        continue
                    account_positions[position_key] = existing_position
                current_price, price_timestamp = await _resolve_current_price(existing_position, symbol)
                price_ts = ensure_tz(price_timestamp) if price_timestamp else now
                prev_amt = abs(safe_fetch_float(existing_position.positionAmt))
                tolerance = max(0.001, prev_amt * 0.001)
                amount_diff = abs(amt_abs - prev_amt)
                if prev_amt > 0.0 and amt_abs == 0.0:
                    if self.service and hasattr(self.service, '_log_zero_report'):
                        self.service._log_zero_report(position_key, now, source="WS")
                    if self.service and hasattr(self.service, '_is_zero_confirmed') and self.service._is_zero_confirmed(position_key, threshold=config.ZERO_CONFIRMATION_THRESHOLD_WS):
                        real_size = 0.0
                        if real_size == 0.0:
                            logger.info(f"[WS_CONFIRMED_ZERO][{position_key}] Zero amount confirmed by WS after {config.ZERO_CONFIRMATION_THRESHOLD_WS} reports (prev_amt={prev_amt}). Closing via reduction handler.")
                            await self.service.handle_reduction(existing_position, position_key, prev_amt, 0.0, prev_amt, current_price, existing_position.entry_price or current_price, reduction_source="ws_confirmed_zero")
                            self.service._clear_zero_report(position_key)
                            _old_ws = existing_position.positionAmt
                            existing_position.positionAmt = amt_abs
                            _acct = position_key.split(':')[0] if ':' in position_key else ''
                            existing_position.max_positionSize = float(config.get_account_setting(_acct, 'MAX_POSITION_SIZE') or config.MAX_POSITION_SIZE)
                            logger.critical(f"[POSAMT_WRITE][WS_ZERO][{position_key}] {_old_ws} -> {amt_abs} (ws_confirmed_zero)")
                            account_positions[position_key] = existing_position
                            self.service.positions[position_key] = existing_position
                        updated_position_keys.add(position_key)
                        continue
                    else:
                        zero_count = self.service.zero_report_tracker.get(position_key, {}).get('count', 1) if self.service and hasattr(self.service, 'zero_report_tracker') else 1
                        logger.warning(f"[WS_ZERO_REPORT][{position_key}] WebSocket reports zero amount (count: {zero_count}/{config.ZERO_CONFIRMATION_THRESHOLD_WS}). Not zeroing yet.")
                        continue
                if current_price > 0 and hasattr(existing_position, 'mark_price'):
                    existing_position.mark_price = current_price
                    if hasattr(existing_position, 'mark_price_last_updated'):
                        existing_position.mark_price_last_updated = price_ts
                        pass
                if prev_amt > 0 and amt_abs != prev_amt:
                    logger.warning(f"[WS_AMT_CHANGE][{position_key}] prev={prev_amt} ws={amt_abs} diff={amount_diff:.6f}")
                if amount_diff < tolerance:
                    if self.service and hasattr(self.service, 'handle_unchanged_position'):
                        await self.service.handle_unchanged_position(existing_position, position_key, amt_abs, current_price, price_is_fresh=True)
                elif amt_abs > prev_amt:
                    logger.critical(f"[POSAMT_WRITE][WS_AUG][{position_key}] {prev_amt} -> {amt_abs} (+{amt_abs-prev_amt}) via WS")
                    if self.service and hasattr(self.service, 'handle_augmentation'):
                        await self.service.handle_augmentation(existing_position, position_key, prev_amt, amt_abs, amt_abs - prev_amt, current_price, existing_position.entry_price or current_price, price_is_fresh=True)
                elif amt_abs < prev_amt:
                    logger.critical(f"[POSAMT_WRITE][WS_RED][{position_key}] {prev_amt} -> {amt_abs} (-{prev_amt-amt_abs}) via WS")
                    if self.service and hasattr(self.service, 'handle_reduction'):
                        await self.service.handle_reduction(existing_position, position_key, prev_amt, amt_abs, prev_amt - amt_abs, current_price, existing_position.entry_price or current_price, reduction_source="ws_update") 
                account_positions[position_key] = existing_position
                if config.VERBOSE_FETCH_LOGGING: logger.info(f"[WS_UPDATE][{existing_position}")
                if self.service:
                    self.service.positions[position_key] = existing_position
                    if hasattr(self.service, '_position_update_timestamps'):
                        self.service._position_update_timestamps[position_key] = now
                    if hasattr(self.service, '_ws_update_timestamps'):
                        self.service._ws_update_timestamps[position_key] = time.time()
                        self.service._ws_update_timestamps[account_key] = time.time()
                    if hasattr(self.service, '_mark_positions_dirty'):
                        self.service._mark_positions_dirty()
                updated_position_keys.add(position_key)
                logger.info(f"pac1: [WS_UPDATE][{position_key}] amt={amt_abs:.6f} prev={prev_amt:.6f} mark={current_price:.6f}")
            account_positions_count = len(account_positions)
            logger.warning(f"[handle_account_update][{account_key}] 🔍🔍🔍 DEBUG: updated_position_keys={len(updated_position_keys)}, keys={list(updated_position_keys)[:5] if updated_position_keys else []}, account_positions_count={account_positions_count}")
            if updated_position_keys:
                from ez_positions import atomic_save_positions
                await self.service._broadcast_positions_to_redis(account_key, updated_position_keys)
                logger.warning(f"[handle_account_update][{account_key}] Broadcasted {len(updated_position_keys)} position update(s) from WebSocket to Redis")
                try :
                    logger.warning(f"[handle_account_update][{account_key}] 💾💾💾 Saving {len(updated_position_keys)} WebSocket position updates to files...")
                    await atomic_save_positions(self.service, account_key, force=True)
                    logger.warning(f"[handle_account_update][{account_key}] Saved {len(updated_position_keys)} WebSocket position updates to files")
                except Exception as save_err:
                    logger.error(f"[handle_account_update][{account_key}] Failed to save WebSocket updates to files: {save_err}", exc_info=True)
            elif account_positions_count > 0:
                logger.warning(f"[handle_account_update][{account_key}] ⚠️⚠️⚠️ WebSocket update with NO updated keys, but {account_positions_count} positions exist - positions updated in memory, fetcher will save")
            else:
                logger.warning(f"[handle_account_update][{account_key}] ⚠️ WebSocket update with no positions in memory")
            self.positions_live = True
            self.positions_source = "live"
            self.positions_last_sync = now
        except Exception as e:
            logger.error(f"[{account_key}] Error in handle_account_update: {e}", exc_info=True)

    async def keep_listen_key_alive(self, account_key):
        """Loop to extend listen key every 25 mins with retry on failure"""
        consecutive_failures = 0
        while self._running:
            try :
                await asyncio.sleep(25 * 60)
                ban_rem = _ban_remaining_seconds()
                if ban_rem > 0:
                    _log_ban_skip_throttled(logger, account_key, f"deferring listen-key keepalive ({ban_rem:.0f}s)")
                    await asyncio.sleep(min(ban_rem + 5, 600))
                    continue
                auth_rem = _auth_ban_remaining(account_key)
                if auth_rem > 0:
                    _log_auth_ban_throttled(logger, account_key, f"deferring listen-key keepalive ({auth_rem:.0f}s)")
                    await asyncio.sleep(min(auth_rem + 5, _AUTH_BAN_COOLDOWN_SEC))
                    continue
                if self.client and self.listen_keys.get(account_key):
                    await asyncio.to_thread(self.client.futures_stream_keepalive, self.listen_keys[account_key])
                    logger.info(f"[{account_key}] Listen key extended successfully")
                    consecutive_failures = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                _record_ip_ban_from_exc(e)
                if _record_auth_ban(account_key, e):
                    _log_auth_ban_throttled(logger, account_key, "keepalive POST -2015 — pausing keepalive, NOT forcing WS reconnect")
                    await asyncio.sleep(_AUTH_BAN_COOLDOWN_SEC)
                    continue
                consecutive_failures += 1
                logger.warning(f"[{account_key}] Keepalive failed (attempt {consecutive_failures}): {e}")
                if consecutive_failures >= 3:
                    logger.error(f"[{account_key}] Keepalive failed {consecutive_failures}x - listen key likely expired, forcing WS reconnect")
                    break
                await asyncio.sleep(min(30 * consecutive_failures, 120))

    async def ensure_mark_price_tasks(self) -> None:
        if self.service and getattr(self.service, "primary_ws_manager", None) and self.service.primary_ws_manager is not self:
            return
        if self.service and getattr(self.service, "primary_ws_manager", None) is None:
            self.service.primary_ws_manager = self
        if self._mark_price_task and not self._mark_price_task.done():
            return
        self.is_primary = True
        self._mark_price_stop = False
        if not self._mark_price_session or self._mark_price_session.closed:
            self._mark_price_session = await self._init_session()
        self._mark_price_task = asyncio.create_task(self._mark_price_ws_loop())
        if not self._fallback_task or self._fallback_task.done():
            self._fallback_task = asyncio.create_task(self._fallback_refresh_loop())
        if not self._guard_task or self._guard_task.done():
            self._guard_task = asyncio.create_task(self._mark_price_guard_loop())
        if not self._rest_poll_task or self._rest_poll_task.done():
            self._rest_poll_task = asyncio.create_task(self._mark_price_rest_poll_loop())

    async def stop_mark_price_tasks(self) -> None:
        if not self.is_primary:
            return
        self._mark_price_stop = True
        for task in (self._mark_price_task, self._fallback_task, self._rest_poll_task):
            if task and not task.done():
                task.cancel()
                try : await task
                except asyncio.CancelledError: pass
        self._mark_price_task = None
        self._fallback_task = None
        self._rest_poll_task = None
        if self._guard_task and not self._guard_task.done():
            self._guard_task.cancel()
            try : await self._guard_task
            except asyncio.CancelledError: pass
        self._guard_task = None
        if self._mark_price_session and not self._mark_price_session.closed:
            await self._mark_price_session.close()
        self._mark_price_session = None
        self._price_cache_file_data = {}
        self.is_primary = False
        if self.service and getattr(self.service, "primary_ws_manager", None) is self:
            self.service.primary_ws_manager = None

    async def _mark_price_ws_loop(self) -> None:
        # 2026-05-10: Binance retired unrouted /ws/ for /market streams. The OLD url silently
        # accepts WS handshake then sends 0 messages. NEW: routed /market/stream?streams=… works.
        # Confirmed empirically (3 msgs received in 5.3s vs 0 for /ws/). Direct path /market/!markPrice@arr
        # returns 404 — must use combined-stream format. Per Binance change-log on routed paths.
        url = "wss://fstream.binance.com/market/stream?streams=!markPrice@arr"
        backoff = 1.0
        try :
            while not self._mark_price_stop:
                try :
                    if not self._mark_price_session or self._mark_price_session.closed:
                        self._mark_price_session = await self._init_session()
                    async with self._mark_price_session.ws_connect(url, heartbeat=60.0, timeout=30.0) as ws:
                        backoff = 1.0
                        async for msg in ws:
                            if self._mark_price_stop: break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_mark_price_message(msg.data)
                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                break
                except Exception as exc:
                    logger.warning(f"[mark_price] websocket error: {exc}")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
        finally:
            if self._mark_price_session and not self._mark_price_session.closed:
                await self._mark_price_session.close()
            self._mark_price_session = None

    async def _mark_price_rest_poll_loop(self) -> None:
        """REST fallback for mark prices when Binance WS stream is silent.
        2026-05-09: confirmed empirically that wss://fstream.binance.com/ws/!markPrice@arr
        accepts connections but sends 0 messages from this account/IP set (Mac AND S1).
        REST /fapi/v1/premiumIndex still works fine (~800ms-1s per call) and returns ALL
        symbols in a single response, so we poll it every ~2s and route each entry through
        the same _apply_mark_price method the WS handler uses (which writes Redis + pubsub)."""
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        interval = float(getattr(self.config, "MARK_PRICE_REST_POLL_INTERVAL", 2.0))
        backoff = interval
        consecutive_fail = 0
        log_first_success = True
        while not self._mark_price_stop:
            try:
                if not self._mark_price_session or self._mark_price_session.closed:
                    self._mark_price_session = await self._init_session()
                async with self._mark_price_session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        if isinstance(payload, list):
                            ts_now = datetime.now(timezone.utc)
                            applied = 0
                            for entry in payload:
                                if not isinstance(entry, dict): continue
                                sym = entry.get("symbol") or entry.get("s")
                                mp_raw = entry.get("markPrice") or entry.get("p")
                                ts_ms = entry.get("time") or entry.get("E")
                                if not sym or mp_raw is None: continue
                                try:
                                    mp = float(mp_raw)
                                except (TypeError, ValueError):
                                    continue
                                if mp <= 0: continue
                                if isinstance(ts_ms, (int, float)) and ts_ms > 0:
                                    try: ts = datetime.fromtimestamp(ts_ms/1000.0, tz=timezone.utc)
                                    except Exception: ts = ts_now
                                else:
                                    ts = ts_now
                                try:
                                    if await self._apply_mark_price(sym, mp, ts):
                                        applied += 1
                                except Exception:
                                    continue
                            if log_first_success and applied > 0:
                                logger.info(f"[mark_price_rest_poll] ✅ first batch applied: {applied} symbols, polling every {interval:.1f}s")
                                log_first_success = False
                            consecutive_fail = 0
                            backoff = interval
                    elif resp.status in (418, 429):
                        consecutive_fail += 1
                        backoff = min(interval * (2 ** consecutive_fail), 120.0)
                        logger.warning(f"[mark_price_rest_poll] HTTP {resp.status} (rate-limited) — backoff to {backoff:.0f}s")
                    else:
                        consecutive_fail += 1
                        backoff = min(interval * (1 + consecutive_fail), 30.0)
                        logger.warning(f"[mark_price_rest_poll] HTTP {resp.status} — backoff to {backoff:.0f}s")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                consecutive_fail += 1
                backoff = min(interval * (1 + consecutive_fail), 30.0)
                logger.debug(f"[mark_price_rest_poll] {type(exc).__name__}: {exc} — backoff {backoff:.0f}s")
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break

    async def _handle_mark_price_message(self, raw: str) -> None:
        try :
            data = safe_json_loads(raw)
        except Exception:
            return
        if isinstance(data, dict) and "data" in data:
            payloads = data["data"] if isinstance(data["data"], list) else [data["data"]]
        elif isinstance(data, list):
            payloads = data
        else:
            payloads = [data]
        for entry in payloads:
            if not isinstance(entry, dict):
                continue
            symbol = entry.get("s") or entry.get("symbol")
            price_candidate = entry.get("p") or entry.get("markPrice") or entry.get("price")
            if not symbol or price_candidate is None:
                continue
            try :
                price = float(price_candidate)
            except (TypeError, ValueError):
                continue
            event_ts_raw = entry.get("E") or entry.get("eventTime") or entry.get("T")
            ts = None
            if isinstance(event_ts_raw, (int, float)):
                ts = ensure_tz(datetime.fromtimestamp(event_ts_raw / 1000, tz=timezone.utc))
            elif isinstance(event_ts_raw, str):
                with suppress(Exception):
                    ts = ensure_tz(isoparse(event_ts_raw))
            await self._apply_mark_price(symbol, price, ts)

    async def _fallback_refresh_loop(self) -> None:
        while not self._mark_price_stop:
            try :
                if self.service and hasattr(self.service, '_refresh_usdc_pairs'):
                    await self.service._refresh_usdc_pairs(refresh_window=600)
                symbols = list(self.service.symbols_active() if self.service and hasattr(self.service, 'symbols_active') else [])
                if not symbols:
                    await asyncio.sleep(self._fallback_interval)
                    continue
                for symbol in symbols:
                    await self.get_mark_price(symbol, allow_fallback=True)
            except Exception as exc:
                logger.debug(f"[mark_price] fallback loop error: {exc}")
            await asyncio.sleep(self._fallback_interval)

    async def _mark_price_guard_loop(self) -> None:
        try :
            while not self._mark_price_stop:
                try :
                    now_ts = datetime.now(timezone.utc)
                    symbols: Set[str] = set()
                    if self.service:
                        async with self.service._positions_lock:
                            for position in self.service.positions.values():
                                if position and position.symbol:
                                    symbols.add(position.symbol)
                            for account_positions in self.service.positions_by_account.values():
                                for position in account_positions.values():
                                    if position and position.symbol:
                                        symbols.add(position.symbol)
                    for symbol in symbols:
                        ts = self.service.price_update_time.get(symbol) if self.service else None
                        stale = True
                        if isinstance(ts, datetime):
                            stale = (now_ts - ts).total_seconds() > self._mark_price_max_staleness
                        if stale:
                            try :
                                await self.get_mark_price(symbol, allow_fallback=True, max_staleness=self._mark_price_max_staleness)
                            except Exception as refresh_exc:
                                logger.debug(f"[mark_price_guard] refresh failed for {symbol}: {refresh_exc!r}")
                except asyncio.CancelledError:
                    break
                except Exception as loop_exc:
                    logger.debug(f"[mark_price_guard] loop error: {loop_exc!r}")
                await asyncio.sleep(self._mark_price_guard_interval)
        except asyncio.CancelledError:
            pass

    async def _apply_mark_price(self, symbol: str, price: float, timestamp: Optional[datetime]) -> bool:
        if price <= 0:
            return False
        norm = self.service._normalize_symbol(symbol) if hasattr(self.service, '_normalize_symbol') else symbol.upper()
        ts = ensure_tz(timestamp) if timestamp else None
        applied_ts = ts or datetime.now(timezone.utc)
        updated = False
        updated_keys = set()
        async with self.service._positions_lock:
            for position_key, position in self.service.positions.items():
                if position and position.symbol == norm:
                    position.mark_price = price
                    position.mark_price_last_updated = applied_ts
                    updated = True
                    updated_keys.add(position_key)
            for account_key, account_positions in (self.service.positions_by_account.items() if self.service else {}):
                for position_key, position in account_positions.items():
                    if position and position.symbol == norm:
                        position.mark_price = price
                        position.mark_price_last_updated = applied_ts
                        updated = True
        if self.service:
            async with self.service._price_cache_lock:
                self.service.price_cache[norm] = {"price": price, "timestamp": applied_ts}
                self.service.price_update_time[norm] = applied_ts
        iso_ts = applied_ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try :
            client = await self.service._ensure_redis_client() if self.service and hasattr(self.service, '_ensure_redis_client') else None
            if client:
                payload = json.dumps({"price": price, "timestamp": iso_ts})
                await client.set(f"mark_price:{norm}", payload, ex=120)
                channel = REDIS_CHANNELS.get("mark_prices", "mark_prices")
                await client.publish(channel, json.dumps({"symbol": norm, "price": price, "timestamp": iso_ts}))
        except Exception as exc:
            logger.debug(f"[mark_price] redis publish failed for {norm}: {exc}")
        if updated:
            if self.service and hasattr(self.service, '_mark_positions_dirty'):
                self.service._mark_positions_dirty()
        return updated

    async def _get_mark_price_from_redis(self, symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
        client = await self.service._ensure_redis_client() if self.service and hasattr(self.service, '_ensure_redis_client') else None
        if not client:
            return (None, None)
        candidates = (symbol, ) 
        for candidate in candidates:
            try :
                raw = await client.get(f"mark_price:{candidate}")
                if not raw:
                    continue
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode()
                if raw.startswith("{"):
                    payload = json.loads(raw)
                    price = _safe_float(payload.get("price") or payload.get("mark_price"), 0.0)
                    ts_raw = payload.get("timestamp") or payload.get("time")
                else:
                    price = _safe_float(raw, 0.0)
                    ts_raw = None
                if price <= 0:
                    continue
                ts = None
                if isinstance(ts_raw, str):
                    with suppress(Exception):
                        ts = ensure_tz(isoparse(ts_raw))
                return (price, ts)
            except Exception as exc:
                logger.debug(f"[mark_price] redis error {candidate}: {exc}")
        return (None, None)

    async def _get_mark_price_from_file(self, symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
        best_price: Optional[float] = None
        best_ts: Optional[datetime] = None
        best_mtime = -1.0
        candidate_symbols = (symbol, ) 
        for path in self._price_cache_files:
            if not path:
                continue
            try :
                stat_result = await aio_os.stat(str(path))
            except Exception:
                continue
            mtime = stat_result.st_mtime
            if self._price_cache_file_mtime.get(path) != mtime:
                try :
                    async with aiofiles.open(path, "r") as handle:
                        content = await handle.read()
                    data = json.loads(content) if content else {}
                except Exception as exc:
                    logger.debug(f"[mark_price] price cache read error: {exc}")
                    data = {}
                self._price_cache_file_data[path] = data if isinstance(data, dict) else {}
                self._price_cache_file_mtime[path] = mtime
            cache_data = self._price_cache_file_data.get(path) or {}
            for candidate in candidate_symbols:
                if not candidate:
                    continue
                entry = cache_data.get(candidate)
                if not entry:
                    continue
                if isinstance(entry, dict):
                    price = _safe_float(entry.get("price") or entry.get("mark_price") or entry.get("value"), 0.0)
                    ts_raw = entry.get("timestamp") or entry.get("time")
                else:
                    price = _safe_float(entry, 0.0)
                    ts_raw = None
                if price <= 0:
                    continue
                ts = None
                if isinstance(ts_raw, str):
                    with suppress(Exception):
                        ts = ensure_tz(isoparse(ts_raw))
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_price = price
                    best_ts = ts
        return (best_price, best_ts)

    async def get_mark_price(self, symbol: str, *, allow_fallback: bool = True, max_staleness: float = 3.0) -> float:
        norm = self.service._normalize_symbol(symbol)
        now = datetime.now(timezone.utc)
        async with self._price_cache_lock:
            cached = self.service.price_cache.get(norm) if self.service else None
        if isinstance(cached, dict):
            price = _safe_float(cached.get("price"), 0.0)
            ts = cached.get("timestamp")
            ts_dt = None
            if isinstance(ts, datetime):
                ts_dt = ensure_tz(ts)
            elif isinstance(ts, str):
                with suppress(Exception):
                    ts_dt = ensure_tz(isoparse(ts))
            if price > 0 and ts_dt and (now - ts_dt).total_seconds() <= max_staleness:
                return price
        price = None
        ts = None
        if allow_fallback:
            price, ts = await self._get_mark_price_from_redis(norm)
            if price is None:
                price, ts = await self._get_mark_price_from_file(norm)
        if price is None:
            if self.service:
                async with self.service._positions_lock:
                    for position in self.service.positions.values():
                        if position and position.symbol == norm and position.mark_price > 0:
                            price = position.mark_price
                            ts = getattr(position, "mark_price_last_updated", None)
                            break
        if price is not None and price > 0:
            ts_age = (datetime.now(timezone.utc) - ts).total_seconds() if isinstance(ts, datetime) else 999.0
            if ts is None or ts_age < 3.0:
                await self._apply_mark_price(norm, price, ts)
            return price
        return 0.0

    async def stop(self):
        """Stop WebSocket manager and close all sessions"""
        logger.debug(f"[WebSocketManager] Stopping WebSocket manager for {self.account_keys}")
        self._running = False
        if getattr(self, 'session', None) and not self.session.closed:
            await self.session.close()
            logger.debug(f"[WebSocketManager] Closed main session")
        if self._mark_price_session and not self._mark_price_session.closed:
            await self._mark_price_session.close()
            logger.debug(f"[WebSocketManager] Closed mark price session")
        if self._mark_price_task and not self._mark_price_task.done():
            self._mark_price_task.cancel()
        if self._fallback_task and not self._fallback_task.done():
            self._fallback_task.cancel()
        if self._guard_task and not self._guard_task.done():
            self._guard_task.cancel()
        await self.stop_mark_price_tasks()
        if self.bm:
            await self.bm.stop() 
        if self.client:
            pass
        logger.debug(f"WebSocketManager for {self.account_keys} stopped.")
@dataclass(slots=True, eq=True)

class StopLevel:
    level: float
    reduction_amount: float
    position_side: str
    strategy: str
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)
    source: str = "system"
    order_id: Optional[str] = None
    breakeven_applied: bool = False
    binance_stop_price: Optional[float] = None
    managed_on_exchange: bool = False
    last_synced_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return { "level": float(self.level), "reduction_amount": float(self.reduction_amount), "position_side": self.position_side, "strategy": self.strategy, "created_at": ensure_tz(self.created_at).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "updated_at": ensure_tz(self.updated_at).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "source": self.source, "order_id": self.order_id, "breakeven_applied": self.breakeven_applied, "binance_stop_price": float(self.binance_stop_price) if self.binance_stop_price is not None else None, "managed_on_exchange": self.managed_on_exchange, "last_synced_at": ensure_tz(self.last_synced_at).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.last_synced_at else None, }
    @classmethod

    def from_dict(cls, payload: Dict[str, Any]) -> "StopLevel":
        last_synced = payload.get("last_synced_at")
        if last_synced:
            try :
                last_synced_dt = ensure_tz(isoparse(str(last_synced)))
            except Exception:
                last_synced_dt = None
        else:
            last_synced_dt = None
        created = payload.get("created_at")
        updated = payload.get("updated_at")
        return cls( level=_safe_float(payload.get("level")), reduction_amount=_safe_float(payload.get("reduction_amount")), position_side=str(payload.get("position_side", "")), strategy=str(payload.get("strategy", "system")), created_at=ensure_tz(isoparse(str(created))) if created else now(), updated_at=ensure_tz(isoparse(str(updated))) if updated else now(), source=str(payload.get("source", "system")), order_id=payload.get("order_id"), breakeven_applied=bool(payload.get("breakeven_applied", False)), binance_stop_price=_safe_float(payload.get("binance_stop_price")) if payload.get("binance_stop_price") is not None else None, managed_on_exchange=bool(payload.get("managed_on_exchange", False)), last_synced_at=last_synced_dt, )

class StopLevelsManager:

    def __init__(self, *, config, logger, get_min_qty, get_symbol_config, get_indicators, get_client, execute_now, get_account_config=None, stop_levels_store=None, order_queue=None, get_cached_open_orders=None, price_buffer_pct=0.001, near_stop_pct=0.01, indicator_cache_ttl=30, get_positions=None, get_positions_by_account=None, get_position=None, service=None, max_stop_levels=2, sync_cooldown=0.1): 
        self.config = config
        self.logger = logger
        self._get_min_qty = get_min_qty
        self.get_symbol_config = get_symbol_config
        self._get_indicators = get_indicators
        self._get_client = get_client
        execute_now = execute_now
        self.get_account_config = get_account_config
        self.stop_levels = stop_levels_store if stop_levels_store is not None else {}
        self.order_queue = order_queue
        self.get_cached_open_orders = get_cached_open_orders
        self.price_buffer_pct = price_buffer_pct
        self.near_stop_pct = near_stop_pct
        self.indicator_cache_ttl = indicator_cache_ttl
        self.max_stop_levels = max_stop_levels
        self.sync_cooldown = sync_cooldown
        self.get_positions = get_positions
        self.get_positions_by_account = get_positions_by_account
        self.get_position = get_position
        self.service = service
        if self.service:
            for attr in ("min_qty", "dedupe_lock", "send_webhook", "active_maker_orders", "managed_maker_order_registry", "cancel_order_with_confirmation", "clear_active_maker_order"):
                if hasattr(self.service, attr):
                    setattr(self, attr, getattr(self.service, attr))
            if not hasattr(self, 'min_qty') and hasattr(self.service, 'min_qty'):
                self.min_qty = self.service.min_qty
        self.stop_order_prefix = f"EZMSTOP_{uuid.uuid4().hex[:4].upper()}" 
        self.generic_stop_prefix = "EZMSTOP_"
        self._managed_stop_ids=set()
        self._indicator_cache = {}
        self._order_cache = {}
        self._last_sync_ts = {}
        self._last_prices = {}
        self._rate_limit_semaphore=asyncio.Semaphore(config.EZ_MANAGE_RATE_LIMIT_SEMAPHORE.get(current_env['env'], 35))
        self._ban_detected = False
        self._last_api_call = 0.0
        self._api_call_interval = 0.1
        self._api_call_times = []
        self._registry_next_flush_at = 0.0
        self._registry_flush_inflight = False
        self.max_api_calls_per_minute = 2500
        self.managed_stop_registry = {}
        self._recent_stop_updates = {}
        self._initial_min_distance: Dict[str, float] = {}
        self._ban_start_time = {}
        if self.service:
            if not hasattr(self.service, "managed_stop_registry"): 
                self.service.managed_stop_registry = {}; self.service.managed_stop_registry_dirty = False
            self._managed_stop_ids.update(str(v.get("orderId")) for v in self.service.managed_stop_registry.values() if v.get("orderId"))

    def _is_managed_stop(self, order: Dict[str, Any], position_key: str = None) -> bool:
        """ STRICT check: Only return True if this is an order WE created. Does not touch manual orders or orders from other bots. """
        if not order or order.get("type") != "STOP_MARKET": 
            return False
        client_id = str(order.get("clientOrderId") or order.get("origClientOrderId") or "")
        is_ours = client_id.startswith(self.stop_order_prefix) or client_id.startswith(self.generic_stop_prefix)
        if is_ours:
            return True
        order_id = str(order.get("orderId") or "")
        if order_id and order_id in self._managed_stop_ids:
            return True
        if self.service and position_key:
             pass
        return False

    def _build_client_order_id(self, position_key: str) -> str:
        digest = hashlib.sha1(f"{position_key}:{time.time_ns()}".encode("utf-8")).hexdigest()[:12]
        return f"{self.stop_order_prefix}_{digest}"

    async def _fetch_indicators_for_symbol(self, symbol: str) -> Dict[str, Any]:
        now = time.time()
        cached_data, cached_ts = self._indicator_cache.get(symbol, ({}, 0.0))
        if cached_data and (now - cached_ts) <= self.indicator_cache_ttl:
            return cached_data
        indicators = await self._invoke_indicator_fetcher(symbol, force=bool(cached_data))
        if not indicators and cached_data:
            return cached_data
        indicators = indicators or {}
        self._indicator_cache[symbol] = (indicators, time.time())
        return indicators

    async def _invoke_indicator_fetcher(self, symbol: str, *, force: bool = False) -> Dict[str, Any]:
        fetcher = self._get_indicators
        try :
            if asyncio.iscoroutinefunction(fetcher):
                return await fetcher(symbol, force_refresh=force) if force else await fetcher(symbol)
            else:
                return fetcher(symbol, force_refresh=force) if force else fetcher(symbol)
        except Exception:
            return {}

    def build_levels(self, *, position_key: str, position_side: str, positionAmt: float, current_price: float, indicators: Dict[str, Any], event: str = "sync", position: Any = None) -> List[StopLevel]:
        """ Revised logic: 1. Max 2 levels internally. 2. Time-based tightening (15 min rule). 3. Breakout logic (Safe trailing). """
        if positionAmt <= 0.0 or current_price <= 0.0: 
            return []
        is_long = position_side.upper() == "LONG"
        now_ts = datetime.now(timezone.utc)
        pos_timestamp = getattr(position, "timestamp", 0) or getattr(position, "created_at", 0)
        if isinstance(pos_timestamp, datetime):
            pos_timestamp = pos_timestamp.timestamp()
        age_seconds = time.time() - (pos_timestamp if pos_timestamp > 0 else time.time())
        is_mature_position = age_seconds > (15 * 60) 
        _acct = position_key.split(':')[0] if ':' in position_key else 'ang'
        _dc_low_k, _dc_high_k, _dc_low4_k, _dc_high4_k, _dc_basis_k = config.get_stop_indicator_keys(_acct)
        dc_low = float(indicators.get(_dc_low_k, 0.0) or indicators.get("dc_low_3m", 0.0) or 0.0)
        dc_high = float(indicators.get(_dc_high_k, 0.0) or indicators.get("dc_high_3m", 0.0) or 0.0)
        dc_low4 = float(indicators.get(_dc_low4_k, 0.0) or indicators.get("dc_low4_3m", 0.0) or 0.0)
        dc_high4 = float(indicators.get(_dc_high4_k, 0.0) or indicators.get("dc_high4_3m", 0.0) or 0.0)
        if dc_low4 == 0: dc_low4 = dc_low * 0.995
        if dc_high4 == 0: dc_high4 = dc_high * 1.005
        _tf = config.get_account_setting(_acct, 'STOP_TIMEFRAME')
        atr_key = f"atr_{_tf}" if _tf != '3m' else "atr_3m"
        atr_val = float(indicators.get(atr_key, 0.0) or indicators.get("atr_3m", 0.0) or 0.0)
        entry_price = getattr(position, "entry_price", current_price)
        is_breakout = False
        if entry_price > 0:
            if is_long and abs(entry_price - dc_high) / entry_price < 0.005:
                is_breakout = True
            elif not is_long and abs(entry_price - dc_low) / entry_price < 0.005:
                is_breakout = True
        if getattr(position, "strategy", "") == "breakout":
            is_breakout = True
        raw_stop_price = 0.0
        stop_reason = "init"
        if is_long:
            if is_breakout:
                prev_dc_low = float(indicators.get(f"{_dc_low_k}_prev", dc_low) or indicators.get("dc_low_3m_prev", dc_low))
                raw_stop_price = min(dc_low, prev_dc_low)
                stop_reason = f"breakout_trail_{_tf}"
            else:
                if is_mature_position:
                    raw_stop_price = dc_low
                    stop_reason = f"mature_dc_{_tf}"
                else:
                    raw_stop_price = dc_low4
                    stop_reason = f"new_dc4_{_tf}"
            if raw_stop_price >= current_price:
                 raw_stop_price = current_price - (2 * atr_val)
        else:
            if is_breakout:
                prev_dc_high = float(indicators.get(f"{_dc_high_k}_prev", dc_high) or indicators.get("dc_high_3m_prev", dc_high))
                raw_stop_price = max(dc_high, prev_dc_high)
                stop_reason = f"breakout_trail_{_tf}"
            else:
                if is_mature_position:
                    raw_stop_price = dc_high
                    stop_reason = f"mature_dc_{_tf}"
                else:
                    raw_stop_price = dc_high4
                    stop_reason = f"new_dc4_{_tf}"
            if raw_stop_price <= current_price:
                raw_stop_price = current_price + (2 * atr_val)
        min_dist = self._calculate_min_stop_distance(entry_price, indicators, is_long, current_price)
        final_price = self._enforce_min_distance(raw_stop_price, entry_price, min_dist, is_long, dc_low_3m=dc_low, dc_high_3m=dc_high)
        levels = []
        if final_price > 0:
            levels.append(StopLevel( level=final_price, reduction_amount=positionAmt, position_side=position_side, strategy=stop_reason, created_at=now_ts, updated_at=now_ts ))
        return levels[:self.max_stop_levels]

    async def _sync_with_exchange(self, *, account_key: str, symbol: str, position_key: str, position_side: str, open_orders: List[Dict[str, Any]], desired_levels: List[StopLevel], current_price: float, positionAmt: float, pos_min_qty: float) -> None:
        return
        """ Revised Sync: 1. Identifies OUR order strictly. 2. Preserves ID if price is close (no churn). 3. Never cancels external orders. """
        if config.HEDGE_MODE: return 
        client = self._get_client(account_key)
        if not client: return
        open_orders = open_orders or []
        side_stops = [o for o in open_orders if o.get('type') == 'STOP_MARKET' and o.get('positionSide') == position_side]
        my_managed_stops = []
        external_stops = []
        for o in side_stops:
            if self._is_managed_stop(o, position_key):
                my_managed_stops.append(o)
            else:
                external_stops.append(o)
        if not desired_levels or positionAmt < pos_min_qty:
            for order in my_managed_stops:
                await self._cancel_single_order(client, symbol, order, position_key)
            return
        target_level = desired_levels[0] 
        target_price = float(target_level.level)
        symbol_cfg = self.get_symbol_config(symbol) or {}
        tick_size = float(symbol_cfg.get("tickSize") or symbol_cfg.get("tick_size") or 0.0)
        if tick_size > 0:
            import math
            precision = int(-math.log10(tick_size))
            target_price = float(f"{target_price:.{precision}f}")
        if len(my_managed_stops) == 1:
            existing_order = my_managed_stops[0]
            existing_price = float(existing_order.get('stopPrice', 0.0))
            existing_qty = float(existing_order.get('origQty', 0.0))
            qty_match = abs(existing_qty - positionAmt) < pos_min_qty
            price_diff_pct = abs(target_price - existing_price) / existing_price if existing_price > 0 else 1.0
            price_match = price_diff_pct < 0.001 
            if qty_match and price_match:
                self.logger.debug(f"[{position_key}] Stop order OK (P: {existing_price} vs T: {target_price}). Keeping ID {existing_order.get('orderId')}")
                return
            self.logger.info(f"[{position_key}] Updating Stop: Price {existing_price}->{target_price} or Qty {existing_qty}->{positionAmt}")
            await self._cancel_single_order(client, symbol, existing_order, position_key)
        elif len(my_managed_stops) > 1:
            self.logger.warning(f"[{position_key}] Found {len(my_managed_stops)} managed stops. Consolidating to 1.")
            for order in my_managed_stops:
                await self._cancel_single_order(client, symbol, order, position_key)
        is_long = position_side.upper() == "LONG"
        if (is_long and target_price >= current_price) or (not is_long and target_price <= current_price):
            self.logger.warning(f"[{position_key}] Target stop {target_price} is wrong side of current {current_price}. Skipping.")
            return
        self.logger.error(f"[{position_key}] STOP_MARKET_DISABLED: direct futures_create_order killed — all orders must route through execute_now. stop={target_price} qty={positionAmt}")
        return

    async def _cancel_single_order(self, client, symbol, order, position_key):
        order_id = str(order.get("orderId"))
        try :
            await self._rate_limited_api_call(asyncio.to_thread( client.futures_cancel_order, symbol=symbol, orderId=order_id ))
            self._managed_stop_ids.discard(order_id)
            self._clear_stop(position_key, order_id)
        except Exception as e:
            if "Unknown order" in str(e):
                self._managed_stop_ids.discard(order_id)
            else:
                self.logger.warning(f"[{position_key}] Cancel failed for {order_id}: {e}")

    async def manage(self, *, account_key:str, symbol:str, position_key:str, position_side:str, position:Any, current_price:float, entry_price:Optional[float]=None, event:str="sync", force_sync:bool=False) -> List[StopLevel]:
        if not config.SERVICE_STOP: return []
        now_ts = time.time()
        last = self._last_sync_ts.get(position_key, 0)
        if not force_sync and now_ts - last < self.sync_cooldown: 
            return self.stop_levels.get(position_key, [])
        if current_price <= 0:
            return self.stop_levels.get(position_key, [])
        pos_amt = abs(float(getattr(position, "positionAmt", 0)))
        min_qty_val = self._get_min_qty(symbol) if self._get_min_qty else 0.001
        pos_min_qty = max(self.config.MIN_POSITION_SIZE / current_price, min_qty_val * 1.2)
        indicators = await self._fetch_indicators_for_symbol(symbol)
        desired = self.build_levels( position_key=position_key, position_side=position_side, positionAmt=pos_amt, current_price=current_price, indicators=indicators, position=position )
        open_orders = []
        if self.get_cached_open_orders:
            open_orders = await self.get_cached_open_orders(account_key, symbol)
        await self._sync_with_exchange( account_key=account_key, symbol=symbol, position_key=position_key, position_side=position_side, open_orders=open_orders, desired_levels=desired, current_price=current_price, positionAmt=pos_amt, pos_min_qty=pos_min_qty )
        self.stop_levels[position_key] = desired
        self._last_sync_ts[position_key] = now_ts
        self._last_prices[position_key] = current_price
        return desired

    def __getattr__(self, name):
        if name == "service":
            logger.error(f"[BLOCKED] StopLevelsManager attempted to access service - THIS IS FORBIDDEN")
            return None
        if self.service:
            try :
                return getattr(self.service, name)
            except AttributeError:
                pass
        raise AttributeError(f"{self.__class__.__name__} object has no attribute '{name}'")

    def _registry_key(self, position_key: str) -> str:
        return position_key

    def _register_stop(self, position_key: str, order_id: str, stop_price: float, quantity: str) -> None:
        if self.service:
            self.service.managed_stop_registry[self._registry_key(position_key)]={"orderId":order_id, "stopPrice":stop_price, "quantity":quantity, "timestamp":datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
            self.service.managed_stop_registry_dirty = True
            asyncio.create_task(self._schedule_registry_flush())

    def _clear_stop(self, position_key: str, order_id: Optional[str]=None) -> None:
        entry=self.service.managed_stop_registry.get(self._registry_key(position_key))
        if not entry or (order_id and str(entry.get("orderId"))!=str(order_id)): return
        self.service.managed_stop_registry.pop(self._registry_key(position_key), None); self.service.managed_stop_registry_dirty=True
        asyncio.create_task(self._schedule_registry_flush())

    async def _invoke_indicator_fetcher(self, symbol: str, *, force: bool = False) -> Dict[str, Any]:
        fetcher = self._get_indicators
        try :
            if asyncio.iscoroutinefunction(fetcher):
                if force:
                    try :
                        return await fetcher(symbol, force_refresh=True)
                    except TypeError:
                        return await fetcher(symbol)
                return await fetcher(symbol)
            else:
                if force:
                    try :
                        return fetcher(symbol, force_refresh=True)
                    except TypeError:
                        return fetcher(symbol)
                return fetcher(symbol)
        except Exception as exc:
            logger.debug(f"[indicator_fetch] {symbol} fetch failed (force={force}): {exc}")
            return {}

    async def _fetch_indicators_for_symbol(self, symbol: str) -> Dict[str, Any]:
        now = time.time()
        cached_data, cached_ts = self._indicator_cache.get(symbol, ({}, 0.0))
        if cached_data and (now - cached_ts) <= self.indicator_cache_ttl:
            return cached_data
        indicators = await self._invoke_indicator_fetcher(symbol, force=bool(cached_data))
        if not indicators and cached_data:
            return cached_data
        indicators = indicators or {}
        self._indicator_cache[symbol] = (indicators, time.time())
        return indicators

    async def _cancel_all_managed_stops(self, client, symbol: str, managed_orders: List[Dict[str, Any]], position_key: str) -> None:
        if client and managed_orders:
            for order in managed_orders:
                order_id=str(order.get("orderId") or "")
                if not order_id: continue
                try :
                    await self._rate_limited_api_call(asyncio.to_thread(client.futures_cancel_order, symbol=symbol, orderId=order_id))
                except Exception as exc:
                    logger.warning(f"[{position_key}] cancel managed stop cleanup failed {exc}")
                self._managed_stop_ids.discard(order_id)
                self._clear_stop(position_key, order_id)
            await self._schedule_registry_flush()
        self._clear_stop(position_key, None)
        self.stop_levels.pop(position_key, None)

    async def _schedule_registry_flush(self, delay: float = 15.0) -> None:
        now = time.time()
        if now < self._registry_next_flush_at:
            return
        self._registry_next_flush_at = now + delay
        if self._registry_flush_inflight:
            return
        self._registry_flush_inflight = True

        async def _flush():
            try :
                await asyncio.sleep(delay)
                if getattr(self.service, "managed_stop_registry_dirty", False):
                    await self.save_managed_stops()
            finally:
                self._registry_flush_inflight = False
        asyncio.create_task(_flush())

    async def _flush_registry(self) -> None:
        saver=getattr(self.service, "c", None) if self.service else None
        await self.save_managed_stops()

    def _calculate_safe_stop_price(self, level: float, current_price: float, is_long: bool) -> float:
        buffer = self.price_buffer_pct
        return level*(1-buffer) if is_long else level*(1+buffer)

    def _calculate_min_stop_distance(self, entry_price: float, indicators: Dict[str, Any], is_long: bool, current_price: float = 0.0, position_gain: float = 0.0) -> float:
        if entry_price <= 0: return 0.0
        ema_20_std_3m = float(indicators.get("ema_20_std_3m", 0.0) or 0.0); atr_3m = float(indicators.get("atr_3m", 0.0) or 0.0); dc_low_3m=float(indicators.get('dc_low_3m', 0) or 0); dc_high_3m=float(indicators.get('dc_high_3m', 0) or 0)
        if is_long:
            dist1 = entry_price - dc_low_3m if dc_low_3m > 0 and entry_price > dc_low_3m else 0.0
        else:
            dist1 = dc_high_3m - entry_price if dc_high_3m > 0 and dc_high_3m > entry_price else 0.0
        dist2 = ema_20_std_3m / 2.0 if ema_20_std_3m > 0 else 0.0
        dist3 = atr_3m if atr_3m > 0 else 0.0
        if dist1 > 0 and current_price > 0 and entry_price > 0:
            breakeven_threshold = max(3.0 * atr_3m, entry_price * 0.006) if atr_3m > 0 else entry_price * 0.006
            price_moved_enough = (is_long and current_price >= entry_price + breakeven_threshold) or (not is_long and current_price <= entry_price - breakeven_threshold)
            if not price_moved_enough: return max(dist1, dist2, dist3)
            return max(dist2, dist3)
        return max(dist1, dist2, dist3)

    def _enforce_min_distance(self, stop_price: float, entry_price: float, min_distance: float, is_long: bool, initial_min_distance: Optional[float] = None, existing_stop_price: Optional[float] = None, dc_low_3m: float = 0.0, dc_high_3m: float = 0.0, current_price: float = 0.0, atr_3m: float = 0.0) -> float:
        if entry_price <= 0 or min_distance <= 0: return stop_price
        breakeven_allowed = False
        if current_price > 0 and entry_price > 0:
            breakeven_threshold = max(3.0 * atr_3m, entry_price * 0.006) if atr_3m > 0 else entry_price * 0.006
            breakeven_allowed = (is_long and current_price >= entry_price + breakeven_threshold) or (not is_long and current_price <= entry_price - breakeven_threshold)
        if is_long:
            min_allowed = entry_price - min_distance
            enforced = max(stop_price, min_allowed)
            if dc_low_3m > 0: enforced = min(enforced, dc_low_3m)
            if initial_min_distance and initial_min_distance > 0:
                initial_min_allowed = entry_price - initial_min_distance
                if existing_stop_price and existing_stop_price > 0:
                    if enforced > existing_stop_price:
                        return enforced
                    else:
                        return max(enforced, initial_min_allowed, existing_stop_price)
                return max(enforced, initial_min_allowed)
            return enforced
        else:
            max_allowed = entry_price + min_distance
            enforced = min(stop_price, max_allowed)
            if dc_high_3m > 0: enforced = max(enforced, dc_high_3m)
            if initial_min_distance and initial_min_distance > 0:
                initial_max_allowed = entry_price + initial_min_distance
                if existing_stop_price and existing_stop_price > 0:
                    if enforced < existing_stop_price:
                        return enforced
                    else:
                        return min(enforced, initial_max_allowed, existing_stop_price)
                return min(enforced, initial_max_allowed)
            return enforced

    def _compute_reduction_amounts(self, *, positionAmt:float, position_value:float, pos_min_qty:float, current_price:float, last_aug_qty:float=0.0, num_steps:int=3) -> List[float]:
        if positionAmt <= pos_min_qty or current_price <= 0.0: return []
        running = positionAmt; reductions = []
        if last_aug_qty > 0.0:
            aug_reduction = min(last_aug_qty, running - pos_min_qty)
            if aug_reduction > 0: reductions.append(aug_reduction); running -= aug_reduction
        remaining_steps = num_steps - len(reductions)
        if remaining_steps > 0 and running > pos_min_qty:
            if remaining_steps == 1: reductions.append(running - pos_min_qty)
            else:
                step_size = (running - pos_min_qty) / remaining_steps
                for i in range(remaining_steps):
                    if i == remaining_steps - 1: reductions.append(running - pos_min_qty)
                    else:
                        reduction = min(step_size, running - pos_min_qty)
                        if reduction > 0: reductions.append(reduction); running -= reduction
        reductions = [r for r in reductions if r > 0]
        return reductions[:num_steps]

    async def _validate_existing_levels(self, existing_levels, position_key, position_side, current_price):
        validated = []; is_long = position_side.upper() == "LONG"
        for level in existing_levels:
            if isinstance(level, dict): stop_price = level.get('level', 0); strategy = level.get('strategy', '')
            else: stop_price = getattr(level, 'level', 0); strategy = getattr(level, 'strategy', '')
            price_diff_pct = abs(stop_price - current_price) / current_price if current_price > 0 else 1.0
            if price_diff_pct < 0.1: validated.append(level)
        return validated

    async def _rate_limited_api_call(self, coro):
        if self._ban_detected:
            if time.time() - self._ban_start_time < 300: 
                logger.warning("⏸️ API ban detected - skipping call")
                raise Exception("API temporarily banned")
            else:
                self._ban_detected = False
        self._check_rate_limit()
        async with self._rate_limit_semaphore:
            now = time.time()
            time_since_last = now - self._last_api_call
            if time_since_last < self._api_call_interval:
                await asyncio.sleep(self._api_call_interval - time_since_last)
            try :
                result = await coro
                self._last_api_call = time.time()
                if self._ban_detected:
                    self._ban_detected = False
                return result
            except Exception as e:
                if "Too many requests" in str(e) or "ban" in str(e).lower():
                    self._ban_detected = True
                    self._ban_start_time = time.time()
                    logger.error("🚨 API BAN DETECTED - pausing API calls for 5 minutes")
                    if isinstance(e, BinanceAPIException) and hasattr(self, "accounts"):
                        ctx_account = current_account.get()
                        targets = [ctx_account] if ctx_account and ctx_account in self.accounts else list(self.accounts.keys())
                        for key in targets:
                            account = self.accounts.get(key)
                            if not account or not hasattr(account, "handle_api_ban"):
                                continue
                            account.handle_api_ban(cooldown_seconds=900)
                raise e 

    def _check_rate_limit(self):
        now = time.time()
        self._api_call_times = [t for t in self._api_call_times if now - t < 60]
        if len(self._api_call_times) >= self.max_api_calls_per_minute * 0.8: 
            logger.warning("⚠️ Approaching API rate limit - slowing down")
            time.sleep(1.0)
        self._api_call_times.append(now)

    def get_stop_file(self, account_key: str, side: str) -> Path:
        base_path = Path(config.BASE_PATH)
        account_dir = base_path / account_key
        account_dir.mkdir(exist_ok=True)
        file_path = account_dir / f"{side.lower()}_stop_levels.json"
        if not file_path.exists():
            file_path.touch()
        return file_path

    async def stop_json_repair_from_backup(self, corrupted_file_path: str, account_key: str, side: str) -> dict:
        try :
            backup_dir = Path(f"./{account_key}/backups")
            if not backup_dir.exists():
                logger.warning(f"[REPAIR] No backup directory found for {account_key}")
                return {}
            backup_pattern = f"{side.lower()}_stop_levels.json_backup_*.json"
            search_path = os.path.join(str(backup_dir), backup_pattern)
            backup_files = glob.glob(search_path)
            if not backup_files:
                logger.warning(f"[REPAIR] {account_key}: No backup files found for {side.lower()}_stop_levels.json")
                return {}
            backup_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for backup_file in backup_files:
                try :
                    async with LimitedAioOpen(backup_file, 'r') as f:
                        content = await f.read()
                        if content.strip():
                            backup_data = json.loads(content)
                            if isinstance(backup_data, dict):
                                cleaned_data = {}
                                for key, value_dict in backup_data.items():
                                    if isinstance(value_dict, dict) and "stop_levels" in value_dict:
                                        stop_levels = value_dict["stop_levels"]
                                        if isinstance(stop_levels, list):
                                            clean_stop_levels = []
                                            for stop_obj in stop_levels:
                                                if isinstance(stop_obj, dict) and "level" in stop_obj:
                                                    clean_stop_obj = { "level": float(stop_obj.get("level", 0.0)), "reduction_amount": float(stop_obj.get("reduction_amount", 0.0)), "augmentation_price": float(stop_obj.get("augmentation_price", 0.0)), "created_at": str(stop_obj.get("created_at", "")), "position_side": str(stop_obj.get("position_side", "")) }
                                                    clean_stop_levels.append(clean_stop_obj)
                                            if clean_stop_levels:
                                                cleaned_data[key] = { "stop_levels": clean_stop_levels, "last_updated": value_dict.get("last_updated", "") }
                                return cleaned_data
                except (json.JSONDecodeError, Exception) as e:
                    logger.debug(f"[REPAIR] Backup file {backup_file} also corrupted: {e}")
                    continue
            logger.warning(f"[REPAIR] All backup files for {side.lower()}_stop_levels.json are corrupted")
            return {}
        except Exception as e:
            logger.error(f"[REPAIR] Failed to repair multi stop levels JSON: {e}")
            return {}

    async def _attempt_json_repair(self, content: str) -> str:
        """Attempt to repair common JSON issues"""
        try :
            content = content.lstrip('\ufeff')
            import re
            content = re.sub(r', (\s*[}\]])', r'\1', content)
            content = re.sub(r'}(\s*){', r'}, ', content)
            return content
        except Exception:
            return None

    async def cleanup_empty_stop_levels(self):
        for account_key, account in self.accounts.items():
            client = getattr(account, "client", None)
            if not client: continue
            try :
                open_orders = await self.get_cached_open_orders(client)
            except Exception as exc:
                logger.error(f"[{account_key}] cleanup stops failed to fetch orders: {exc}")
                continue
            account_positions = self.positions_by_account.get(account_key, {})
            for order in open_orders:
                if order.get("type") != "STOP_MARKET": continue
                order_id = str(order.get("orderId") or "")
                symbol = order.get("symbol") or ""
                pos_side = order.get("positionSide") or ""
                if not order_id or not symbol or not pos_side: continue
                position_key = construct_position_key(account_key, symbol, pos_side)
                position = account_positions.get(position_key)
                mark_price_val = safe_fetch_float(getattr(position, "mark_price", 0), 0.0) if position else 0.0
                notional = (getattr(position, "positionAmt", 0) or 0) * mark_price_val
                if position and notional > max(self.config.MIN_POSITION_SIZE, self.stop_manager._get_min_qty(symbol) * mark_price_val):
                    continue
                try :
                    await asyncio.to_thread(client.futures_cancel_order, symbol=symbol, orderId=order_id)
                    self.stop_manager._managed_stop_ids.discard(order_id)
                    self.stop_manager._clear_stop(position_key, order_id)
                except Exception as exc:
                    logger.warning(f"[{position_key}] cleanup stop cancel failed for {order_id}: {exc}")
        await self.stop_manager._flush_registry() 

    async def stop_levels_save(self, account_key: str, force: bool = False, min_interval: int = 60):
        if not account_key or not isinstance(account_key, str): return
        now_ts = time.time()
        if not hasattr(self, "_last_stop_levels_save_time"):
            self._last_stop_levels_save_time: Dict[str, float] = {}
        if not force and account_key in self._last_stop_levels_save_time:
            last_save_ts = self._last_stop_levels_save_time[account_key]
            if (now_ts - last_save_ts) < min_interval:
                return
        now = datetime.now(timezone.utc)
        for side in ['LONG', 'SHORT']:
            file_path = self.get_stop_file(account_key, side)
            existing_data = {}
            try :
                if await aio_os.path.exists(file_path):
                    async with LimitedAioOpen(file_path, 'r') as f:
                        content = await f.read()
                        if content.strip(): existing_data = json.loads(content)
            except json.JSONDecodeError: existing_data = await self.stop_json_repair_from_backup(file_path, account_key, side)
            except Exception: existing_data = {}
            if not isinstance(existing_data, dict): existing_data = {}
            current_side_keys = set()
            stop_levels_store = self.stop_manager.stop_levels if self.stop_manager else {}
            for position_key, stop_levels in stop_levels_store.items():
                if position_key.count(":") == 1 and position_key.count("_") >= 1:
                    try :
                        acc, symbol_side = position_key.split(":", 1)
                        symbol, key_side = symbol_side.rsplit("_", 1)
                        if acc == account_key and key_side == side:
                            if not stop_levels or len(stop_levels) == 0: continue
                            stop_levels = list(stop_levels)[:2]
                            serialized_stop_levels = []
                            for stop_obj in stop_levels:
                                if hasattr(stop_obj, 'level'):
                                    clean_stop_obj = {"level": float(stop_obj.level), "reduction_amount": float(stop_obj.reduction_amount), "augmentation_price": float(getattr(stop_obj, 'augmentation_price', 0.0)), "created_at": str(stop_obj.created_at), "position_side": str(stop_obj.position_side)}
                                    serialized_stop_levels.append(clean_stop_obj)
                                elif isinstance(stop_obj, dict):
                                    clean_stop_obj = {"level": float(stop_obj.get("level", 0.0)), "reduction_amount": float(stop_obj.get("reduction_amount", 0.0)), "augmentation_price": float(stop_obj.get("augmentation_price", 0.0)), "created_at": str(stop_obj.get("created_at", "")), "position_side": str(stop_obj.get("position_side", ""))}
                                    serialized_stop_levels.append(clean_stop_obj)
                            if serialized_stop_levels:
                                existing_data[position_key] = {"stop_levels": serialized_stop_levels, "last_updated": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
                                current_side_keys.add(position_key)
                    except (ValueError, TypeError, KeyError) as e: logger.error(f"Error processing stop levels for {position_key}: {e}")
            stop_levels_store = self.stop_manager.stop_levels if self.stop_manager else {}
            memory_count = sum(1 for k in stop_levels_store.keys() if k.startswith(f"{account_key}:") and k.endswith(f"_{side}"))
            if getattr(self.config, 'VERBOSE_FETCH_LOGGING', getattr(self.config, 'VERBOSE', False)):
                if config.VERBOSE_STOPS: logger.info(f"[stop_levels_save] {account_key}:{side}: memory_data={memory_count} entries, existing_data={len(existing_data)} entries: {list(existing_data.keys())[:10]}")
            try :
                await ensure_path_writable(file_path)
                json_str = json.dumps(existing_data, indent=2)
                json_str_bytes = len(json_str.encode('utf-8'))
                async with LimitedAioOpen(file_path, 'w') as f:
                    await f.write(json_str)
                    await f.flush()
                final_size = file_path.stat().st_size if file_path.exists() else 0
                if final_size != json_str_bytes:
                    logger.error(f"[stop_levels_save] File size mismatch for {file_path}: expected {json_str_bytes} bytes, got {final_size} bytes")
                await self._broadcast_stop_levels_to_redis(account_key, side, existing_data)
            except Exception as e: logger.error(f"Error saving stop levels to {file_path}: {e}")
        self._last_stop_levels_save_time[account_key] = now_ts

    async def load_stop_levels(self, account_key: str):
        if not account_key or not isinstance(account_key, str): return
        loaded_count = 0
        for position_side in ['LONG', 'SHORT']:
            file_path = self.get_stop_file(account_key, position_side)
            stop_data = {}
            try :
                if await aio_os.path.exists(file_path):
                    async with LimitedAioOpen(file_path, 'r') as f:
                        content = await f.read()
                        if content.strip(): stop_data = json.loads(content)
            except json.JSONDecodeError: stop_data = await self.stop_json_repair_from_backup(file_path, account_key, position_side)
            except Exception: stop_data = {}
            if isinstance(stop_data, dict):
                for key, value_dict in stop_data.items():
                    if not (key.count(":") == 1 and key.count("_") >= 1 and key.startswith(f"{account_key}:") and key.endswith(f"_{position_side}")): continue
                    if isinstance(value_dict, dict) and "stop_levels" in value_dict:
                        stop_levels = value_dict["stop_levels"]
                        if isinstance(stop_levels, list):
                            clean_stop_levels = []
                            for stop_obj in stop_levels:
                                try :
                                    if isinstance(stop_obj, StopLevel):
                                        clean_stop_levels.append(stop_obj)
                                    elif isinstance(stop_obj, dict) and "level" in stop_obj:
                                        clean_stop_levels.append(StopLevel.from_dict(stop_obj))
                                except Exception:
                                    continue
                            if clean_stop_levels:
                                clean_stop_levels = clean_stop_levels[:2]
                                self.stop_levels[key] = clean_stop_levels
                                loaded_count += 1
        logger.debug(f"[load_stop_levels] Loaded {loaded_count} stops for {account_key}")

class PositionService: 

    def __init__(self, *, config: Optional[Config] = None, base_path: Optional[Path] = None, logger=None, loop: Optional[asyncio.AbstractEventLoop] = None, accounts: Optional[Dict[str, Any]] = None, execute_now: Optional[Callable[..., Awaitable[Any]]] = None, order_queue: Optional[Any] = None, get_cached_open_orders: Optional[Callable[..., Awaitable[Any]]] = None, indicator_fetcher: Optional[Callable[[str], Awaitable[Dict[str, Any]]] | Callable[[str], Dict[str, Any]]] = None) -> None:
        self.config = config or Config()
        self.base_path = Path(base_path or self.config.BASE_PATH)
        self.logger = logger or logging.getLogger("ez_positions_service")
        self.loop = loop or asyncio.get_event_loop()
        self._shutdown_event = asyncio.Event()
        self.positions: Dict[str, Position] = {}
        self.positions_by_account: Dict[str, Dict[str, Position]] = defaultdict(dict)
        self._initial_positions_snapshot: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._positions_reference_snapshot: Dict[str, Position] = {}
        self._positions_by_account_reference_snapshot: Dict[str, Dict[str, Position]] = {}
        self._reference_snapshot_lock = DummyLock()
        self._last_deletion_check: Dict[str, float] = {}
        self._restore_negative_cache: Dict[str, float] = {}
        self._restore_negative_cache_ttl: float = 300.0
        self.stop_levels: Dict[str, List[StopLevel]] = {}
        self.reentry_plans: Dict[str, ReentryPlan] = {}
        self.ladder_plans: Dict[str, LadderPlan] = {}
        self.augmented_positions: Dict[str, datetime] = {}
        self.reduced_positions: Dict[str, datetime] = {}
        self.reversed_positions: Dict[str, datetime] = {}
        self.direct_high_gain_augmented: Dict[str, Dict[str, Any]] = {}
        self._callbacks: Dict[str, List[Callable[[Position], Awaitable[None] | None]]] = defaultdict(list)
        self._load_lock = DummyLock()
        self._write_lock = asyncio.Lock()
        self._debounce: Dict[str, float] = {}
        self._debounce_interval = 0.8
        self._account_paths_cache: Dict[str, Path] = {}
        self.accounts: Dict[str, Any] = accounts or {}
        self.order_queue = order_queue
        self._allowed_accounts = {}
        self.get_cached_open_orders = get_cached_open_orders
        self.indicator_fetcher = indicator_fetcher
        self._execution_locks: Dict[str, float] = {} 
        self._recent_executions: Dict[str, Dict[str, Any]] = {} 
        self.indicators_bridge: Optional[IndicatorsBridge] = None
        self.redis_manager: Optional[Any] = None
        self.latest_indicators_cache: Dict[str, Dict[str, Any]] = {}
        self.raw_indicators_json_content: Optional[bytes] = None
        self.indicators_cache_timestamps: Dict[str, float] = {}
        self.indicators_timestamp: datetime = datetime.now(timezone.utc)
        self.indicators_cache_ttl: float = float(getattr(self.config, "INDICATORS_CACHE_TTL", 5.0))
        self._redis_client: Optional[Redis] = None
        self.websocket_managers: Dict[str, WebSocketManager] = {}
        self.primary_ws_manager: Optional[WebSocketManager] = None
        self.managed_stop_registry: Dict[str, Dict[str, Any]] = {}
        self.managed_stop_registry_dirty = False
        self.managed_stop_registry_path = self.base_path / "managed_stops.json"
        self._account_listen_keys: Dict[str, str] = {}
        self._account_ws_tasks: Dict[str, asyncio.Task] = {}
        self._account_keepalive_tasks: Dict[str, asyncio.Task] = {}
        self._initializing = False
        self._initialized = False
        self._positions_loaded_once = False
        self._rest_poll_tasks: Dict[str, asyncio.Task] = {}
        self._periodic_full_save_task: Optional[asyncio.Task] = None
        self._periodic_update_zero_positions_task: Optional[asyncio.Task] = None
        self._account_sessions: Dict[str, aiohttp.ClientSession] = {}
        self._account_monitor_stop = True
        self._rest_poll_interval = float(getattr(self.config, "POSITION_POLL_INTERVAL", 6.0))  # 2026-04-24 USER DIRECTIVE: Keep REST at 6s — already hitting Binance -1003 bans. WS is the PRIMARY source. REST is for mark price freshness only. If WS is frozen → fix WS, don't hammer REST.
        self._enable_auto_fetch = True
        self._rest_poll_jitter = float(getattr(self.config, "POSITION_POLL_JITTER", 1.0)) 
        self._listen_key_refresh_seconds = float(getattr(self.config, "LISTEN_KEY_REFRESH_SECONDS", 25 * 60))
        self._positions_dirty = False
        self.price_cache: Dict[str, Dict[str, Any]] = {}
        self.price_cache_2: Dict[str, Dict[str, Any]] = {}
        self.price_cache_3: Dict[str, Dict[str, Any]] = {}
        self.price_update_time: Dict[str, datetime] = {}
        self._price_cache_lock = DummyLock()
        # price_cache dicts are self-owned — loaded directly from disk in initialize(), shared with trade_manager via getattr
        self._positions_lock = DummyLock()
        self._usdc_pairs: Set[str] = set()
        self._usdc_pairs_last_loaded: float = 0.0
        self._stop_refresh_interval: float = float(getattr(self.config, "STOP_REFRESH_INTERVAL", 45))
        self._stop_levels_task: Optional[asyncio.Task] = None
        self._file_locks: Dict[str, asyncio.Lock] = {}
        self._load_reentry_locks: Dict[str, DummyLock] = {}
        self._load_reentry_locks_lock = DummyLock()
        self._lock = DummyLock()
        self._save_accounts_lock = DummyLock()
        self._save_task: Optional[asyncio.Task] = None
        self._save_cooldown: float = float(getattr(self.config, "POSITIONS_SAVE_COOLDOWN", 1.0))
        self._last_save_time: float = 0.0
        self._last_augment_save_time: Dict[str, Dict[str, Any]] = {}
        self._last_reduced_save_time: Dict[str, Dict[str, Any]] = {}
        self._last_direct_high_gain_save_time: Dict[str, Dict[str, Any]] = {}
        self._webhook_semaphore = asyncio.Semaphore(int(getattr(self.config, "WEBHOOK_CONCURRENCY_LIMIT", 4)))
        self._last_reversal_save_time: Dict[str, datetime] = {}
        self._last_positions_file_write: Dict[str, float] = defaultdict(float)
        self._last_full_save_time: float = 0.0
        self._full_save_interval: float = 10.0  # Was 3.0 — Redis broadcast every 3s is excessive, 10s is fine
        self._updated_positions_tracker: Dict[str, Set[str]] = defaultdict(set)
        self._last_ladder_save_time: Dict[str, float] = defaultdict(float)
        self._last_reentry_save_time: Dict[str, float] = defaultdict(float)
        self.last_ladder_save_time: Dict[str, datetime] = {}
        self.last_reentry_save_time: Dict[str, datetime] = {}
        self.last_evaluation_time: Dict[str, datetime] = {}
        self.evaluation_cooldown: float = float(getattr(self.config, "EVALUATION_COOLDOWN", 300.0))
        self.price_change_tracker: Dict[str, List[Tuple[datetime, float, float]]] = {}
        self.failed_price_counts: Dict[str, int] = {}
        self.augmentation_cooldown_map: Dict[str, Dict[str, Any]] = {}
        self._augmentation_dedup: Dict[str, Tuple[float, datetime]] = {}
        self.price_filter_window_priority: float = float(getattr(self.config, "PRICE_FILTER_WINDOW_PRIORITY", 5.0))
        self.price_filter_window_default: float = float(getattr(self.config, "PRICE_FILTER_WINDOW_DEFAULT", 10.0))
        self.price_filter_min_pct_priority: float = float(getattr(self.config, "PRICE_FILTER_MIN_PCT_PRIORITY", 0.05))
        self.price_filter_min_pct_default: float = float(getattr(self.config, "PRICE_FILTER_MIN_PCT_DEFAULT", 0.12))
        self.priority_position_threshold: float = float(getattr(self.config, "PRIORITY_POSITION_THRESHOLD", 5000.0))
        self.symbols_ang_long: Set[str] = self._load_symbols_list(self.config.SYMBOLS_ANG_LONG)
        self.symbols_ang_short: Set[str] = self._load_symbols_list(self.config.SYMBOLS_ANG_SHORT)
        self.symbols_inf_long: Set[str] = self._load_symbols_list(self.config.SYMBOLS_INF_LONG)
        self.symbols_inf_short: Set[str] = self._load_symbols_list(self.config.SYMBOLS_INF_SHORT)
        self.symbols_men: Set[str] = self._load_symbols_list(self.config.SYMBOLS_MEN)
        self.symbols_fin: Set[str] = self._load_symbols_list(self.config.SYMBOLS_FIN)
        self.symbols_flz: Set[str] = self._load_symbols_list(self.config.SYMBOLS_FLZ)
        self.symbols_active: Set[str] = self._load_symbols_list(self.config.SYMBOLS_ACTIVE)
        self.min_qty: Dict[str, float] = self._load_min_qty()
        self.symbol_configs: Dict[str, Any] = self._load_symbol_configs()
        self.reduction_cooldown_map: Dict[str, datetime] = {}
        self.orders_in_limbo: Dict[str, Dict[str, datetime]] = {}
        self.max_stop_levels: int = int(getattr(self.config, "STOP_MANAGER_MAX_LEVELS", 3))
        self._housekeeping_stop = False
        self._prev_gain_task: Optional[asyncio.Task] = None
        self._pnl_decay_task: Optional[asyncio.Task] = None
        self._pnl_deterioration_task: Optional[asyncio.Task] = None
        self._position_logging_task: Optional[asyncio.Task] = None
        self._position_flush_task: Optional[asyncio.Task] = None
        self._redis_refresh_task: Optional[asyncio.Task] = None
        self._data_sync_task: Optional[asyncio.Task] = None
        self._ladder_save_task: Optional[asyncio.Task] = None
        self._augmented_save_task: Optional[asyncio.Task] = None
        self._reduced_save_task: Optional[asyncio.Task] = None
        self._reentry_maintenance_task: Optional[asyncio.Task] = None
        self._position_flush_event: Optional[asyncio.Event] = None
        self._positions_update_lock = DummyLock()
        self._periodic_save_lock = DummyLock() 
        self._position_update_timestamps: Dict[str, datetime] = {}
        self.zero_report_tracker: Dict[str, Dict[str, Any]] = self._load_zero_report_tracker()
        self.nonzero_report_tracker: Dict[str, List[datetime]] = {}
        self._positions_refreshing: Dict[str, bool] = {}
        self.reduced_in_monitor_reductions: Dict[str, float] = {}
        self.last_monitored: Dict[str, float] = {}
        self.last_monitored_positions: Dict[str, float] = {}
        self.last_symbol_monitored: Dict[str, float] = {}
        self._monitor_reduction_tasks: Dict[str, asyncio.Task] = {}
        self._monitor_heartbeats: Dict[str, float] = {}
        self._monitor_reductions_started_accounts: Set[str] = set()
        self._monitor_guard_task: Optional[asyncio.Task] = None
        self._symbol_watchdog_task: Optional[asyncio.Task] = None
        self._maker_reduce_backoff: Dict[str, float] = {}
        self._maker_webhook_fallback: Dict[str, float] = {}
        self.gain_deterioration_count: Dict[str, int] = {}
        self.gain_deterioration_reentry: Dict[str, Dict[str, Any]] = {}
        self.positions_to_gracefully_exit: Set[str] = set()
        self._pending_reentry_accounts: Set[str] = set()
        self.dedupe_lock = DummyLock()
        self._last_api_seen: Dict[str, Set[str]] = defaultdict(set)
        maker_limits = getattr(self.config, "EZ_MANAGE_MAKER_SEMAPHORE", {}) or {}
        env_key = (current_env or {}).get('env', 'default')
        self.maker_semaphore = asyncio.Semaphore(maker_limits.get(env_key, 100)) 
        self._last_positions_fetch: Dict[str, float] = {}
        self.available_usdc_pairs: Set[str] = set()
        self.service = self
        self.positions_live = False
        self.positions_source = "filesystem"
        self.ladder_levels = {}
        self.last_ladder_save_time = {} 
        self.positions_last_sync: Optional[datetime] = None
        self.circuit_breakers: Dict[str, Dict[str, Any]] = {}
        self.circuit_breaker_config = {"failure_threshold": 5, "recovery_timeout": 60, "success_threshold": 3}
        self._recent_api_failures: Dict[str, int] = {}
        self.trade_manager: Optional[Any] = None
        self.tracker_manager: Optional[Any] = None
        self.data_manager: Optional[Any] = None
        self.hedge_engine: Optional[Any] = None
        self._hedge_engine_initialized = False
        self._last_failure_decay: Dict[str, float] = {}
        self.reentry_data = {} 
        self.last_reentry_save_time = {}
        self.position_reasons = {}
        self.invalidation_levels = {}
        self._reentry_save_lock = DummyLock()
        self._save_all_ladder_levels_active = False
        self.active_maker_orders = {} 
        self.managed_maker_order_registry = {} 
        self.price_band_tracked_order_ids: DefaultDict[str, Set[str]] = defaultdict(set)
        self.order_deduplication = {}
        self.position_callback_manager = PositionCallbackManager()
        self.stop_manager = StopLevelsManager( config=self.config, logger=self.logger or logger, get_min_qty=self._get_min_qty, get_symbol_config=self.get_symbol_config, get_indicators=lambda symbol: getattr(self, 'indicators_snapshot', {}).get(symbol.upper(), {}), get_client=self._get_account_client, execute_now=execute_now, get_account_config=lambda account_key: self.accounts.get(account_key), stop_levels_store=self.stop_levels, order_queue=self.order_queue, get_cached_open_orders=self.get_cached_open_orders, get_positions=self.get_positions, get_positions_by_account=lambda account_key=None: (self.positions_by_account if account_key is None else self.positions_by_account.get(account_key, {})), get_position=self.positions.get, service=self, max_stop_levels=self.max_stop_levels, )
        self._ws_update_timestamps = {}
        self.recently_queued_signals = {}
        self._persist_force_interval = max(55.0, float(getattr(self.config, "POSITIONS_FILE_FORCE_INTERVAL", 55.0)))
        self._reversed_save_task: Optional[asyncio.Task] = None
        self._data_sync_lock = DummyLock()
        self._last_data_sync_log = 0.0
        global service_global, positions_engine
        service_global = self
        positions_engine = self
        self._disk_loaded = False
        self._loading_complete_event = asyncio.Event()
        self._housekeeping_started = False
        self._accounts_loaded_once: Set[str] = set()
        self._stop_levels_loaded_accounts: Set[str] = set()
        self._reentry_loaded_accounts: Set[str] = set()
        self._ladder_loaded_accounts: Set[str] = set()
        self._augmented_loaded_accounts: Set[str] = set()
        self._reduced_loaded_accounts: Set[str] = set()
        self._reversed_loaded_accounts: Set[str] = set()
        self._direct_high_gain_loaded_accounts: Set[str] = set()
        self._positions_loaded_once = False
        self._last_reentry_flush_ts: float = 0.0
        self._reentry_flush_in_progress: bool = False
        self._high_gain_monitoring: Dict[str, Dict[str, Any]] = {} 
        self.tradeable_keys = {} 
        self.force_retry_counts = {}
        self._universe_maintenance_task = None

    def _schedule_task(self, coro: Awaitable[Any]) -> None:
        if coro is None:
            return
        try :
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return
        loop.create_task(coro)

    def _normalize_timestamp(self, ts):
        if not ts:
            return None
        if isinstance(ts, (int, float)):
            try :
                ts = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            except Exception:
                return None
        elif isinstance(ts, str):
            try :
                ts = isoparse(ts)
            except Exception:
                return None
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts if isinstance(ts, datetime) else None

    def _extract_position_timestamp(self, position):
        if not position:
            return None
        for attr in ("last_updated", "prev_gain_last_updated", "opened_at"):
            ts = getattr(position, attr, None)
            normalized = self._normalize_timestamp(ts)
            if normalized:
                return normalized
        return None

    def _assign_position_if_newer(self, account_key: str, position_key: str, candidate, candidate_ts=None):
        expected_prefix = f"{account_key}:"
        if not isinstance(position_key, str) or not position_key.startswith(expected_prefix):
            return None
        if isinstance(candidate, dict):
            try :
                candidate = Position.from_dict(candidate)
            except Exception:
                return None
        existing = self.positions.get(position_key)
        existing_ts = self._extract_position_timestamp(existing)
        new_ts = self._normalize_timestamp(candidate_ts) if candidate_ts is not None else self._extract_position_timestamp(candidate)
        if existing and existing_ts and new_ts and existing_ts > new_ts:
            self.positions_by_account.setdefault(account_key, {})[position_key] = existing
            self.positions[position_key] = existing
            return existing
        if new_ts:
            self._position_update_timestamps[position_key] = new_ts
        else:
            self._position_update_timestamps[position_key] = datetime.now(timezone.utc)
        self.positions_by_account.setdefault(account_key, {})[position_key] = candidate
        self.positions[position_key] = candidate
        return candidate

    async def _delayed_lock_cleanup(self, lock_key: str, delay_seconds: int):
        try :
            await asyncio.sleep(delay_seconds)
            redis_manager = getattr(self, "redis_manager", None)
            if redis_manager and hasattr(redis_manager, "get") and hasattr(redis_manager, "delete"):
                current_value = await redis_manager.get(lock_key)
                if isinstance(current_value, bytes): current_value = current_value.decode()
                if isinstance(current_value, str) and current_value.startswith("post_fill"): await redis_manager.delete(lock_key); return
            client = await self._ensure_redis_client()
            if client:
                current_value = await client.get(lock_key)
                if isinstance(current_value, bytes): current_value = current_value.decode()
                if isinstance(current_value, str) and current_value.startswith("post_fill"): await client.delete(lock_key)
        except Exception as err:
            logger.debug(f"[DELAYED_LOCK_CLEANUP_ERROR] {lock_key}: {err}")

    def _mark_positions_dirty(self) -> None:
        self._positions_dirty = True
        if self._position_flush_event:
            self._position_flush_event.set()

    def _get_expected_symbol_count(self) -> int:
        try :
            symbols_file = Path(getattr(self.config, "SYMBOLS_FILE", Path(self.config.BASE_PATH) / "symbols.json"))
            if symbols_file.exists():
                with open(symbols_file, "r", encoding="utf-8") as f:
                    content = f.read()
                    if not content.strip():
                        return 236
                    try :
                        import orjson
                        symbols_data = orjson.loads(content) 
                    except Exception:
                        import json
                        symbols_data = json.loads(content)
                if isinstance(symbols_data, list):
                    return len(symbols_data)
            return 236
        except Exception as e:
            logger.error(f"[_get_expected_symbol_count] Error reading symbols.json: {e}")
            return 236

    def get_long_short_ratio(self, account_key: str) -> Dict[str, float]:
        positions = self.positions_by_account.get(account_key, {})
        long_value = sum(abs(float(getattr(p, 'positionAmt', 0))) * float(getattr(p, 'mark_price', 0) or getattr(p, 'entry_price', 0)) for k, p in positions.items() if k.endswith('_LONG') and abs(float(getattr(p, 'positionAmt', 0))) > 0)
        short_value = sum(abs(float(getattr(p, 'positionAmt', 0))) * float(getattr(p, 'mark_price', 0) or getattr(p, 'entry_price', 0)) for k, p in positions.items() if k.endswith('_SHORT') and abs(float(getattr(p, 'positionAmt', 0))) > 0)
        total = long_value + short_value
        return {'long_value': long_value, 'short_value': short_value, 'long_pct': (long_value / total * 100) if total > 0 else 50.0, 'short_pct': (short_value / total * 100) if total > 0 else 50.0, 'ratio': (long_value / short_value) if short_value > 0 else float('inf')}
    def _load_symbols_list(self, file_path: Path) -> Set[str]:
        try :
            if not file_path:
                return set()
            path = Path(file_path)
            if not path.exists():
                return set()
            with open(path, 'rb') as handle:
                content = handle.read()
            if not content:
                return set()
            payload = safe_json_loads(content)
            if isinstance(payload, dict):
                for key in ("symbols", "items", "data"):
                    data = payload.get(key)
                    if isinstance(data, list):
                        payload = data
                        break
                else:
                    payload = list(payload.values())
            if isinstance(payload, list):
                return { str(symbol).strip().upper() for symbol in payload if isinstance(symbol, (str, bytes)) }
        except Exception as exc:
            logger.debug(f"[positions_service] Failed to load symbols from {file_path}: {exc}")
        return set()

    def _init_circuit_breaker(self, account_key: str) -> None:
        if account_key not in self.circuit_breakers:
            self.circuit_breakers[account_key] = {"state": "CLOSED", "failure_count": 0, "success_count": 0, "last_failure_time": None, "last_success_time": None}

    def _is_circuit_open(self, account_key: str) -> bool:
        self._init_circuit_breaker(account_key)
        breaker = self.circuit_breakers[account_key]
        if breaker["state"] != "OPEN":
            return False
        last_failure = breaker["last_failure_time"]
        if last_failure and (datetime.now(timezone.utc) - last_failure).total_seconds() > self.circuit_breaker_config["recovery_timeout"]:
            breaker["state"] = "HALF_OPEN"
            breaker["success_count"] = 0
            return False
        return True

    def _record_success(self, account_key: str) -> None:
        self._init_circuit_breaker(account_key)
        breaker = self.circuit_breakers[account_key]
        breaker["success_count"] += 1
        breaker["last_success_time"] = datetime.now(timezone.utc)
        if breaker["state"] == "HALF_OPEN" and breaker["success_count"] >= self.circuit_breaker_config["success_threshold"]:
            breaker["state"] = "CLOSED"
            breaker["failure_count"] = 0

    def _record_failure(self, account_key: str) -> None:
        self._init_circuit_breaker(account_key)
        breaker = self.circuit_breakers[account_key]
        breaker["failure_count"] += 1
        breaker["last_failure_time"] = datetime.now(timezone.utc)
        if breaker["failure_count"] >= self.circuit_breaker_config["failure_threshold"]:
            breaker["state"] = "OPEN"

    def _track_api_failure(self, account_key: str) -> None:
        now = time.time()
        current = self._recent_api_failures.get(account_key, 0) + 1
        self._recent_api_failures[account_key] = current
        last_decay = self._last_failure_decay.get(account_key, now)
        if now - last_decay >= 300 and current > 0:
            self._recent_api_failures[account_key] = max(0, current - 1)
        self._last_failure_decay[account_key] = now

    def _parse_account_from_key(self, position_key: str) -> Optional[str]:
        if not isinstance(position_key, str) or ":" not in position_key:
            return None
        try :
            account_key, _, _ = parse_position_key(position_key)
            return account_key
        except Exception:
            return position_key.split(":", 1)[0].strip() or None

    def _account_path(self, account_key: str) -> Path:
        if account_key not in self._account_paths_cache:
            path = self.base_path / account_key
            path.mkdir(parents=True, exist_ok=True)
            (path / "backups").mkdir(parents=True, exist_ok=True)
            self._account_paths_cache[account_key] = path
        return self._account_paths_cache[account_key]

    async def _get_file_lock(self, path: Path) -> asyncio.Lock:
        key = str(path)
        lock = self._file_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._file_locks[key] = lock
        return lock

    def _load_min_qty(self) -> Dict[str, float]:
        try :
            if self.config.MIN_QTY_FILE.exists():
                with open(self.config.MIN_QTY_FILE, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return {str(symbol).upper(): _safe_float(value) for symbol, value in data.items()}
        except Exception as exc:
            logger.warning(f"[positions_service] Failed to load min_qty.json: {exc}")
        return {"DEFAULT": getattr(self.config, "DEFAULT_MIN_QTY", 0.0001)}

    async def get_current_price(self, symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
        """Get current price from positions, cache, or Redis."""
        clean_symbol = self.service._normalize_symbol(symbol)
        price, timestamp = None, None
        now_utc = datetime.now(timezone.utc)
        try_positions = []
        for positions_dict in self.positions_by_account.values():
            try_positions.extend([p for p in positions_dict.values() if p and p.symbol == clean_symbol])
        for position in try_positions:
            price_val = safe_fetch_float(getattr(position, "mark_price", 0.0), 0.0)
            if price_val <= 0: continue
            ts = getattr(position, "mark_price_last_updated", None) or getattr(position, "last_updated", None)
            if isinstance(ts, str):
                try : ts = isoparse(ts)
                except Exception: ts = None
            if isinstance(ts, datetime) and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts and (now_utc - ts).total_seconds() < 2:
                return price_val, ts
        if self.redis_manager:
            try :
                redis_key = f"mark_price:{clean_symbol}"
                # 2026-04-30: bound this redis.get. SimpleRedisManager iterates 3 backends
                # at 2s each → up to 6s; called per-position from handle_unchanged_position
                # while holding _positions_lock. 50 positions × 6s drove process_account_update
                # past the 120s wait_for tripwire (pau_stall fired 4× between 01:35 and 02:40).
                try:
                    p_data = await asyncio.wait_for(self.redis_manager.get(redis_key), timeout=0.5)
                except asyncio.TimeoutError:
                    p_data = None
                if p_data is not None:
                    if isinstance(p_data, dict):
                        price = float(p_data.get("price", 0))
                        ts_raw = p_data.get("timestamp") or p_data.get("time")
                        if isinstance(ts_raw, (int, float)):
                            divisor = 1000.0 if ts_raw > 1e12 else 1.0
                            timestamp = datetime.fromtimestamp(ts_raw / divisor, tz=timezone.utc)
                        elif isinstance(ts_raw, str):
                            try : timestamp = isoparse(ts_raw)
                            except Exception: pass
                    elif isinstance(p_data, (int, float, str)):
                        try :
                            price = float(p_data)
                            timestamp = None 
                        except Exception:
                            pass
                    if price and timestamp:
                         if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=timezone.utc)
                         if (now_utc - timestamp).total_seconds() < 15:
                            return price, timestamp
            except Exception:
                pass
        async with self._price_cache_lock:
            cached = self.price_cache.get(clean_symbol)
        if isinstance(cached, dict):
            price = _safe_float(cached.get("price"), 0.0)
            ts = cached.get("timestamp")
            if isinstance(ts, datetime):
                if (now_utc - ts).total_seconds() < 15:
                    return price, ts 
        return None, None

    async def cancel_empty_stop(self, account_key, symbol, position_key, current_price=None, positionAmt=None, require_tiny=True):
        """Cancel stop order for empty/tiny positions"""
        if require_tiny:
            now = datetime.now(timezone.utc) 
            position = self.get_position(position_key)
            min_qty = self.min_qty.get(symbol, 0.0001) * 1.2
            tiny_threshold = max(config.MIN_POSITION_SIZE * 3, min_qty * current_price)
            if positionAmt is not None and positionAmt * current_price > tiny_threshold:
                return
            else:
                if positionAmt is not None and positionAmt > min_qty:
                    return
        registry = getattr(self.stop_manager.service, "managed_stop_registry", {}) if getattr(self.stop_manager, "service", None) else self.managed_stop_registry
        entry = registry.get(position_key)
        order_id = str(entry.get("orderId") or "") if entry else ""
        account = self.accounts.get(account_key)
        client = account.client if account else None
        if not client or not order_id:
            return
        try :
            # 2026-05-07: hard 10s ceiling on Binance cancel. Without this, a slow Binance
            # response stalls handle_reduction which is awaited from inside _process_account_update_impl,
            # which trips the 120s PAU tripwire. Three pau_stall events fired between 18:43 and 19:00
            # this way (flz/fin/flz, all on the API_CONFIRMED_CLOSED zero-out path).
            await asyncio.wait_for(asyncio.to_thread(client.futures_cancel_order, symbol=symbol, orderId=order_id), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(f"[{position_key}] cancel_empty_stop: futures_cancel_order >10s — abandoning to keep PAU live (next stop sync will retry)")
        except BinanceAPIException as exc:
            if exc.code not in (-2011, -2013):
                logger.warning(f"[{position_key}] Failed to cancel managed stop {order_id}: {exc}")
        except Exception as exc:
            logger.warning(f"[{position_key}] Unexpected error cancelling managed stop {order_id}: {exc}")
        self.stop_manager._managed_stop_ids.discard(order_id)
        self.stop_manager._clear_stop(position_key, order_id or None)
        await self.stop_manager._schedule_registry_flush()

    async def save_managed_stops(self) -> None:
        try :
            payload = json_dumps(self.managed_stop_registry)
            if isinstance(payload, str): payload = payload.encode('utf-8')
            tmp = self.managed_stop_registry_path.with_suffix(".tmp")
            tmp.write_bytes(payload)
            tmp.replace(self.managed_stop_registry_path)
        except Exception as exc:
            logger.error(f"[managed_stops] Failed to persist registry: {exc}")

    def _load_symbol_configs(self) -> Dict[str, Any]:
        try :
            if self.config.SYMBOL_CONFIGS_FILE.exists():
                with open(self.config.SYMBOL_CONFIGS_FILE, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except Exception as exc:
            logger.warning(f"[positions_service] Failed to load symbol_configs.json: {exc}")
        return {}

    def _get_min_qty(self, symbol: str) -> float:
        symbol = (symbol or "").upper()
        if symbol in self.min_qty:
            return _safe_float(self.min_qty[symbol], 0.0001)
        base = symbol[:-4] if symbol.endswith("USDC") or symbol.endswith("USDT") else symbol
        if base in self.min_qty:
            return _safe_float(self.min_qty[base], 0.0001)
        return _safe_float(self.min_qty.get("DEFAULT", 0.0001), 0.0001)

    def get_symbol_config(self, symbol: str) -> Dict[str, Any]:
        symbol = (symbol or "").upper()
        return self.symbol_configs.get(symbol, {})

    def is_same_direction(self, side, position_side) -> bool:
        return (side == "BUY" and position_side == "LONG") or (side == "SELL" and position_side == "SHORT")

    def is_opposite_direction(self, side, position_side) -> bool:
        return (side == "SELL" and position_side == "LONG") or (side == "BUY" and position_side == "SHORT")

    def validate_order_side(self, side: str, position_side: str, action: str, position_key: str) -> tuple[str, bool]:
        is_opening_or_augmenting = action in ['OPEN', 'AUGMENT', 'REENTRY']
        is_reducing_or_closing = action in ['REDUCE', 'PROFIT_TAKE', 'CLOSE']
        if is_opening_or_augmenting:
            expected_side = "BUY" if position_side == "LONG" else "SELL"
        elif is_reducing_or_closing:
            expected_side = "SELL" if position_side == "LONG" else "BUY"
        else:
            expected_side = "BUY" if position_side == "LONG" else "SELL"
        if side == expected_side:
            return side, True
        logger.error(f"🚨 CRITICAL ORDER SIDE ERROR: {position_key}")
        logger.error(f" Position: {position_side}, Action: {action}")
        logger.error(f" Received side: {side}, Expected side: {expected_side}")
        logger.error(f" CORRECTING to: {expected_side}")
        return expected_side, False

    def is_symbol_allowed(self, account_key: str, symbol: str, position_key: str = None) -> bool:
        if position_key:
            pos = self.positions.get(position_key)
            if pos and abs(float(getattr(pos, 'positionAmt', 0.0))) > 0:
                return True
        long_key = construct_position_key(account_key, symbol, "LONG")
        short_key = construct_position_key(account_key, symbol, "SHORT")
        if position_key and position_key in self.positions_to_gracefully_exit: return True
        if position_key and position_key in self.direct_high_gain_augmented: return True
        if long_key in self.reversed_positions or short_key in self.reversed_positions: return True
        try :
            if not hasattr(self, '_master_symbols_cache') or not isinstance(self._master_symbols_cache, set): self._master_symbols_cache = set()
            if not self._master_symbols_cache:
                try :
                    symbols_path = Path(self.config.SYMBOLS_FILE)
                    if symbols_path.exists():
                        with open(symbols_path, 'r') as f: content = json.load(f)
                        if isinstance(content, dict) and 'symbols' in content: self._master_symbols_cache = set(s.strip().upper() for s in content['symbols'] if isinstance(s, str))
                        elif isinstance(content, list): self._master_symbols_cache = set(s.strip().upper() for s in content if isinstance(s, str))
                except Exception: self._master_symbols_cache = set()
        except Exception: self._master_symbols_cache = set()
        symbol_to_check = symbol.strip().upper()
        if self._master_symbols_cache and symbol_to_check not in self._master_symbols_cache:
            if symbol_to_check not in self._master_symbols_cache: return False
        return True

    def _position_file(self, account_key: str, side: str) -> Path:
        return self._account_path(account_key) / f"{side.lower()}_positions.json"

    def _stop_file(self, account_key: str, side: str) -> Path:
        return self._account_path(account_key) / f"{side.lower()}_stop_levels.json"

    def _reentry_file(self, account_key: str, side: str) -> Path:
        return self._account_path(account_key) / f"{side.lower()}_reentry.json"

    def _ladder_file(self, account_key: str, side: str) -> Path:
        return self._account_path(account_key) / f"{side.lower()}_ladder.json"

    async def _load_json(self, path: Path) -> Any:
        try :
            return await load_json_safe(str(path))
        except Exception:
            return {}

    def _sort_positions_dict(self, positions: Dict[str, Any], sort_by_value: bool = True) -> Dict[str, Any]:

        def sort_key(item):
            key, pos = item
            pos_amt = float(pos.get('positionAmt', 0) or 0)
            mark_price = float(pos.get('mark_price', 0) or 0)
            abs_amt = abs(pos_amt)
            value = abs_amt * mark_price
            symbol = str(pos.get('symbol', '') or '')
            if abs_amt == 0.0:
                return (1, key) 
            else:
                return (0, -value, symbol) 
        return dict(sorted(positions.items(), key=sort_key))

    async def _atomic_write(self, path: Path, payload: Dict[str, Any]) -> None:
        data_to_write = payload
        try :
            name = path.name.lower() if isinstance(path.name, str) else ""
            is_positions_file = name.endswith("_positions.json") and ("long_positions" in name or "short_positions" in name)
            if is_positions_file and isinstance(payload, dict):
                try:
                    if path.exists():
                        async with LimitedAioOpen(path, "r") as ef:
                            existing = json.loads(await ef.read())
                        if isinstance(existing, dict):
                            protected = 0
                            for pk, old_data in existing.items():
                                if not isinstance(old_data, dict):
                                    continue
                                old_amt = abs(float(old_data.get("positionAmt", 0)))
                                old_ep = float(old_data.get("entry_price", 0))
                                old_iq = float(old_data.get("initial_quantity", 0) or 0)
                                old_mg = float(old_data.get("max_gain", 0) or 0)
                                old_oa = old_data.get("opened_at")
                                has_real = old_amt > 0 or old_ep > 0 or old_iq > 0 or old_mg != 0 or old_oa is not None
                                if has_real:
                                    new_data = payload.get(pk)
                                    if isinstance(new_data, dict):
                                        new_amt = abs(float(new_data.get("positionAmt", 0)))
                                        new_ep = float(new_data.get("entry_price", 0))
                                        new_iq = float(new_data.get("initial_quantity", 0) or 0)
                                        new_oa = new_data.get("opened_at")
                                        if new_amt == 0 and new_ep == 0 and new_iq == 0 and new_oa is None:
                                            payload[pk] = old_data
                                            protected += 1
                            if protected > 0:
                                import traceback
                                stack = "".join(traceback.format_stack()[-6:])
                                logger.critical(f"🛡️ [WRITE_GUARD] {path.name}: Protected {protected} positions from null overwrite. PID={os.getpid()}\nStack:\n{stack}")
                except Exception as guard_err:
                    logger.error(f"[WRITE_GUARD] Error checking {path.name}: {guard_err}")
                data_to_write = self._sort_positions_dict(payload)
            json_bytes = json_dumps(data_to_write)
            if isinstance(json_bytes, str): json_bytes = json_bytes.encode('utf-8')
            do_backup = False
            backup_dir = path.parent / "backups"
            if not hasattr(self, '_last_backup_time'):
                self._last_backup_time = {}
            last_bk = self._last_backup_time.get(str(path), 0)
            if (time.time() - last_bk) > 300: 
                do_backup = True
            if do_backup:
                try :
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    backup_path = backup_dir / f"{path.stem}_backup_{ts}.json"
                    async with LimitedAioOpen(backup_path, "wb") as bak:
                        await bak.write(json_bytes)
                    self._last_backup_time[str(path)] = time.time()
                    asyncio.create_task(self.prune_old_backups(str(backup_dir), path.stem))
                except Exception as e:
                    logger.warning(f"Backup failed: {e}")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_suffix = f".tmp.{id(self)}.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            tmp_path = path.with_suffix(path.suffix + tmp_suffix)
            async with LimitedAioOpen(tmp_path, "wb") as handle:
                await handle.write(json_bytes)
            try :
                os.replace(tmp_path, path)
            except FileNotFoundError:
                pass
        except ValueError as ve:
            raise
        except Exception as e:
            logger.error(f"[_atomic_write] Unexpected error: {e}")

    async def load_account_from_redis(self, account_key: str) -> bool:
        try :
            client = await self._ensure_redis_client()
            if not client: return False
            redis_key = f"positions:{account_key}"
            data = await client.get(redis_key)
            if not data: return False
            if isinstance(data, bytes): 
                data = data.decode('utf-8')
            payload = json.loads(data)
            raw_positions = payload.get("positions", {})
            if not raw_positions: return False
            account_positions = self.positions_by_account.setdefault(account_key, {})
            loaded_count = 0
            for pos_key, pos_data in raw_positions.items():
                try :
                    pos_obj = Position.from_dict(pos_data)
                    # GUARD: never overwrite good in-memory data with zeroed Redis data
                    existing = account_positions.get(pos_key)
                    if existing:
                        ex_ep = float(getattr(existing, 'entry_price', 0) or 0)
                        new_ep = float(getattr(pos_obj, 'entry_price', 0) or 0)
                        if ex_ep > 0 and new_ep == 0:
                            logger.critical(f"[REDIS_LOAD_BLOCKED] {pos_key}: Redis has entry_price=0 but memory has {ex_ep}. KEEPING memory.")
                            continue
                    account_positions[pos_key] = pos_obj
                    self.positions[pos_key] = pos_obj
                    loaded_count += 1
                except Exception: pass
            if loaded_count > 0:
                self._accounts_loaded_once.add(account_key)
                logger.info(f"[load_account_from_redis] {account_key}: Hydrated {loaded_count} positions from Redis.")
                return True 
            return False
        except Exception as e:
            logger.debug(f"[load_account_from_redis] Failed for {account_key}: {e}")
            return False

    async def load_account(self, account_key: str, *, force: bool = False) -> None:
        global _positions_loaded_once_global
        account_positions = self.positions_by_account.get(account_key, {})
        expected_count = self._get_expected_symbol_count()
        account_has_positions = account_positions and len(account_positions) >= expected_count * 0.9 
        if account_has_positions:
            if account_key not in self._accounts_loaded_once:
                self._accounts_loaded_once.add(account_key)
                logger.info(f"[_load_account][{account_key}] Positions already in memory ({len(account_positions)}). Marked as loaded.")
            if not force:
                return
        if not force and account_key in self._accounts_loaded_once and len(account_positions) > 0:
            return
        logger.info(f"[_load_account] 🚨 LOADING {account_key} (Force={force}) - Current Memory: {len(account_positions)}")
        account_positions = self.positions_by_account.setdefault(account_key, {})
        try :
            file_payload, file_ts = await self._load_positions_raw_from_files(account_key)
            loaded_count = 0
            expected_prefix = f"{account_key}:"
            for position_key, payload in file_payload.items():
                if not isinstance(position_key, str) or not position_key.startswith(expected_prefix):
                    continue
                try :
                    parsed_account, _, _ = parse_position_key(position_key)
                    if parsed_account != account_key:
                        continue
                except Exception:
                    pass
                if not isinstance(payload, dict): continue
                existing_position = account_positions.get(position_key)
                if existing_position:
                    continue
                try :
                    position_obj = Position.from_dict(payload)
                    account_positions[position_key] = position_obj
                    self.positions[position_key] = position_obj
                    loaded_count += 1
                except Exception as exc:
                    logger.error(f"[_load_account] [{account_key}] bad position {position_key}: {exc}")
                    continue
            logger.debug(f"[_load_account] 🚨 LOADED {account_key} - AFTER: {len(account_positions)} positions in memory (loaded {loaded_count} new positions)")
            async with self._reference_snapshot_lock:
                if account_key not in self._positions_by_account_reference_snapshot:
                    self._positions_by_account_reference_snapshot[account_key] = {}
                for pos_key, pos_obj in account_positions.items():
                    if pos_obj and pos_key not in self._positions_by_account_reference_snapshot[account_key]:
                        self._positions_by_account_reference_snapshot[account_key][pos_key] = pos_obj
                for pos_key, pos_obj in account_positions.items():
                    if pos_obj and pos_key not in self._positions_reference_snapshot:
                        self._positions_reference_snapshot[pos_key] = pos_obj
            asyncio.create_task(self._load_stop_levels(account_key, force=force))
            asyncio.create_task(self._load_reentry(account_key, force=force))
            asyncio.create_task(self._load_ladder(account_key, force=force))
            asyncio.create_task(self._load_augmented_positions(account_key, force=force))
            asyncio.create_task(self._load_reduced_positions(account_key, force=force))
            asyncio.create_task(self._load_reversed_positions(account_key, force=force))
            asyncio.create_task(self._ensure_full_pk_coverage(account_key))
            self._accounts_loaded_once.add(account_key)
        except Exception as e:
            logger.error(f"[_load_account] CRITICAL FAILURE loading {account_key}: {e}", exc_info=True)
            if len(account_positions) > 0:
                self._accounts_loaded_once.add(account_key)
                logger.warning(f"[_load_account] {account_key} marked as loaded despite error because {len(account_positions)} positions exist.")

    async def load_all(self, *, force: bool = False, startup: bool = False) -> None:
        global _positions_loaded_once_global
        total_in_memory = sum(len(acc_pos) for acc_pos in self.positions_by_account.values())
        if total_in_memory > 0 and not force:
            if _positions_loaded_once_global or self._positions_loaded_once:
                logger.warning(f"[load_all] ⚠️ Positions already loaded ({total_in_memory} in memory) - skipping reload to protect state")
                if not self._loading_complete_event.is_set():
                    self._loading_complete_event.set()
                return
        if total_in_memory == 0 and (_positions_loaded_once_global or self._positions_loaded_once):
             logger.warning(f"[load_all] ⚠️ Flags say loaded, but MEMORY IS EMPTY (0 positions). Forcing reload.")
        if _positions_loaded_once_global or self._positions_loaded_once:
            logger.warning("[load_all] ⚠️⚠️⚠️ ABSOLUTE BLOCK: Positions already loaded once - reload BLOCKED FOREVER!")
            if not self._loading_complete_event.is_set():
                self._loading_complete_event.set()
            return
        try:
            async with self._load_lock:
                _load_keys = list(self.accounts.keys()) or list(self.positions_by_account.keys()) or list(getattr(self, '_allowed_accounts', {}).keys()) or getattr(self.config, "ACCOUNT_KEYS", [])
                for account_key in _load_keys:
                    await self.load_account(account_key, force=force)
                # for account_key in getattr(self.config, "ACCOUNT_KEYS", []):
                #     await self._load_direct_high_gain(account_key, force=False)
                # high_gain_min_size = getattr(self.config, 'HIGH_GAIN_AUGMENTATION_MIN_SIZE', 200.0)
                now_dt = datetime.now(timezone.utc)
                for account_key in _load_keys:
                    account_positions = self.positions_by_account.get(account_key, {})
                    for position_key, position in account_positions.items():
                        if not position or not hasattr(position, 'positionAmt') or not hasattr(position, 'mark_price'):
                            continue
                        position_value = abs(float(getattr(position, 'positionAmt', 0.0))) * float(getattr(position, 'mark_price', 0.0))
                        # if position_value > high_gain_min_size and position_key not in self.direct_high_gain_augmented:
                        #     account_key_pos, symbol, position_side = parse_position_key(position_key)
                        #     self.mark_direct_high_gain(position_key, timestamp=now_dt, stop_price='managed', entry_price=float(getattr(position, 'entry_price', getattr(position, 'mark_price', 0.0))), max_size=position_value, position_side=position_side, direct_order_ts=None, in_flight_ts=None)
                        #     logger.info(f"[load _all] Auto-added {position_key} to direct_high_gain_augmented: value=${position_value:.2f} > ${high_gain_min_size:.2f}")
                # CRITICAL: Check for missing positions and make A LOT OF NOISE if positions are missing
                expected_symbol_count = self._get_expected_symbol_count()
                import traceback
                for account_key in _load_keys:
                    account_positions = self.positions_by_account.get(account_key, {})
                    # Check per side (LONG and SHORT should each have expected_symbol_count positions)
                    for side in ("LONG", "SHORT"):
                        side_positions = {k: v for k, v in account_positions.items() if hasattr(v, "position_side") and v.position_side == side}
                        position_count = len(side_positions)
                        if position_count < expected_symbol_count:
                            missing_count = expected_symbol_count - position_count
                            logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] CRITICAL: MISSING {missing_count} POSITIONS! Expected {expected_symbol_count}, found {position_count}")
                            file_path = self.get_position_file(account_key, side)
                            if file_path.exists():
                                file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                                file_age = (datetime.now(timezone.utc) - file_mtime).total_seconds()
                                file_size = file_path.stat().st_size
                                logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] File {file_path.name} exists: mtime={file_mtime}, age={file_age:.0f}s, size={file_size} bytes")
                                backup_dir = file_path.parent / "backups"
                                if backup_dir.exists():
                                    backup_files = list(backup_dir.glob(f"*{side.lower()}*backup*.json"))
                                    if backup_files:
                                        latest_backup = max(backup_files, key=lambda p: p.stat().st_mtime)
                                        backup_mtime = datetime.fromtimestamp(latest_backup.stat().st_mtime, tz=timezone.utc)
                                        backup_age = (datetime.now(timezone.utc) - backup_mtime).total_seconds()
                                        logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] Latest backup: {latest_backup.name}, age={backup_age:.0f}s")
                                    else:
                                        logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] NO BACKUP FILES FOUND in {backup_dir}")
                                else:
                                    logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] NO BACKUP DIRECTORY: {backup_dir}")
                            else:
                                logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] FILE DOES NOT EXIST: {file_path}")
                                # Stack trace to see where we are
                            logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] Stack trace:\n{''.join(traceback.format_stack()[-5:])}")
                            logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] Attempting recovery from backups AT STARTUP ONLY...")
                            # CRITICAL: Only restore from backups at STARTUP - after startup, positions are NEVER reloaded
                            # At startup, we need expected_symbol_count positions (268 per account) - load from backups if needed
                            try:
                                backup_dir = self.get_position_file(account_key, side).parent / "backups"
                                if backup_dir.exists():
                                    backup_files = list(backup_dir.glob(f"*{side.lower()}*backup*.json"))
                                    if backup_files:
                                        latest_backup = max(backup_files, key=lambda p: p.stat().st_mtime)
                                        backup_content = await self._load_json_file_content(latest_backup)
                                        if backup_content and isinstance(backup_content, dict):
                                            recovered = 0
                                            for pos_key, pos_data in backup_content.items():
                                                # CRITICAL: At startup, only add missing positions - never overwrite existing ones
                                                if pos_key not in account_positions and isinstance(pos_data, dict):
                                                    try:
                                                        pos_obj = Position.from_dict(pos_data)
                                                        if pos_obj.position_side == side:
                                                            account_positions[pos_key] = pos_obj
                                                            self.positions[pos_key] = pos_obj
                                                            recovered += 1
                                                    except Exception as e:
                                                        logger.error(f"[load _all][{account_key}:{side}] Failed to restore {pos_key} from backup: {e}")
                                                elif pos_key in account_positions:
                                                    logger.debug(f"[load _all][{account_key}:{side}] ⚠️ SKIPPING backup restore for {pos_key} - already in memory")
                                            if recovered > 0:
                                                logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] RECOVERED {recovered} positions from backup {latest_backup.name} AT STARTUP")
                                            # Check if we still need more positions
                                            side_positions_after = {k: v for k, v in account_positions.items() if hasattr(v, "position_side") and v.position_side == side}
                                            if len(side_positions_after) < expected_symbol_count:
                                                logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] STILL MISSING {expected_symbol_count - len(side_positions_after)} positions after backup recovery!")
                            except Exception as recovery_err:
                                logger.critical(f"🚨🚨🚨 [load _all][{account_key}:{side}] Recovery attempt failed: {recovery_err}", exc_info=True)
            self.positions_live = False
            self.positions_source = "filesystem"
            self.positions_last_sync = None
            self._disk_loaded = True
            async with self._positions_update_lock:
                self._initial_positions_snapshot = {ak: {pk: pos.to_dict() for pk, pos in acc_pos.items()} for ak, acc_pos in self.positions_by_account.items()}
            total_loaded = sum(len(acc_pos) for acc_pos in self.positions_by_account.values())
            logger.debug(f"[load _all] Created immutable snapshot of {sum(len(acc) for acc in self._initial_positions_snapshot.values())} positions, {total_loaded} positions loaded into memory")
            _positions_loaded_once_global = True
            self._positions_loaded_once = True
            self._loading_complete_event.set()
            logger.debug(f"[load _all] Loading complete - {total_loaded} positions in memory, flags set, event set")
        except Exception as e:
            logger.error(f"[load _all] Error during load: {e}", exc_info=True)
            self._loading_complete_event.set()
            logger.warning(f"[load _all] ⚠️ Loading event set despite error - process_account_update can proceed")


    async def _load_stop_levels(self, account_key: str, *, force: bool = False) -> None:
        if account_key in self._stop_levels_loaded_accounts and not force:
            return
        for side in ("LONG", "SHORT"):
            for file_path in (self._stop_file(account_key, side), self._account_path(account_key) / f"{side.lower()}_multi_stop_levels.json"):
                snapshot = await self._load_json(file_path)
                if not isinstance(snapshot, dict): continue
                if "positions" in snapshot and isinstance(snapshot["positions"], dict):
                    snapshot = snapshot["positions"]
                for key, payload in snapshot.items():
                    if not isinstance(key, str) or not isinstance(payload, dict):
                        continue
                    key_upper = key.upper()
                    if key_upper in ('POSITIONS', 'META', 'TIMESTAMP', 'TIME', 'DATA', 'INFO'):
                        continue
                    if not (':' in key and '_' in key and (key_upper.endswith('_LONG') or key_upper.endswith('_SHORT'))):
                        continue
                    levels = payload.get("stop_levels") or []
                    clean: List[StopLevel] = []
                    for entry in levels:
                        try :
                            if isinstance(entry, StopLevel):
                                clean.append(entry)
                            elif isinstance(entry, dict):
                                clean.append(StopLevel.from_dict(entry))
                        except Exception:
                            continue
                    if clean:
                        final_key = clean_position_key(key) if isinstance(key, str) else key
                        if not final_key or not (':' in final_key and '_' in final_key):
                            continue
                        position_key = final_key if final_key.startswith(account_key) else construct_position_key(account_key, final_key.split(":")[-1].split("_")[0], side)
                        self.stop_levels[position_key] = clean
        self._stop_levels_loaded_accounts.add(account_key)

    async def _load_reentry(self, account_key: str, *, force: bool = False) -> None:
        if account_key in self._reentry_loaded_accounts and not force:
            return
        for side in ("LONG", "SHORT"):
            file_path = self._reentry_file(account_key, side)
            snapshot = await self._load_json(file_path)
            if not isinstance(snapshot, dict): continue
            if "positions" in snapshot and isinstance(snapshot["positions"], dict):
                snapshot = snapshot["positions"]
            for key, payload in snapshot.items():
                if not isinstance(key, str) or not isinstance(payload, dict):
                    continue
                key_upper = key.upper()
                if key_upper in ('POSITIONS', 'META', 'TIMESTAMP', 'TIME', 'DATA', 'INFO'):
                    continue
                if not (':' in key and '_' in key and (key_upper.endswith('_LONG') or key_upper.endswith('_SHORT'))):
                    continue
                try :
                    final_key = clean_position_key(str(key))
                    if not final_key or not (':' in final_key and '_' in final_key):
                        continue
                    position_key = final_key if final_key.startswith(account_key) else construct_position_key(account_key, final_key.split(":")[-1].split("_")[0], side)
                    reentry_level = _safe_float(payload.get("reentry_level") or payload.get("reentry_data", 0.0))
                    self.reentry_data[position_key] = { "reentry_level": reentry_level, "reentry_amount": _safe_float(payload.get("reentry_amount")), "timestamp": str(payload.get("timestamp") or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')), "reason": str(payload.get("reason", "")), }
                except Exception:
                    continue
        self._reentry_loaded_accounts.add(account_key)

    async def _load_ladder(self, account_key: str, *, force: bool = False) -> None:
        if account_key in self._ladder_loaded_accounts and not force:
            return
        for side in ("LONG", "SHORT"):
            file_path = self._ladder_file(account_key, side)
            snapshot = await self._load_json(file_path)
            if not isinstance(snapshot, dict): continue
            if "positions" in snapshot and isinstance(snapshot["positions"], dict):
                snapshot = snapshot["positions"]
            for key, payload in snapshot.items():
                if not isinstance(key, str) or not isinstance(payload, dict):
                    continue
                key_upper = key.upper()
                if key_upper in ('POSITIONS', 'META', 'TIMESTAMP', 'TIME', 'DATA', 'INFO'):
                    continue
                if not (':' in key and '_' in key and (key_upper.endswith('_LONG') or key_upper.endswith('_SHORT'))):
                    continue
                try :
                    final_key = clean_position_key(str(key))
                    if not final_key or not (':' in final_key and '_' in final_key):
                        continue
                    levels = payload.get("levels") if isinstance(payload, dict) else []
                    created = safe_datetime(payload.get("created_at"), datetime.now(timezone.utc)) or datetime.now(timezone.utc)
                    plan = LadderPlan(position_key=final_key if final_key.startswith(account_key) else construct_position_key(account_key, final_key.split(":")[-1].split("_")[0], side), levels=levels if isinstance(levels, list) else [], created_at=created)
                    if plan.levels: self.ladder_plans[plan.position_key] = plan
                except Exception:
                    continue
        self._ladder_loaded_accounts.add(account_key)

    async def _load_augmented_positions(self, account_key: str, *, force: bool = False) -> None:
        if account_key in self._augmented_loaded_accounts and not force:
            return
        path = self._account_path(account_key) / "augmented_positions.json"
        snapshot = await self._load_json(path)
        if not isinstance(snapshot, dict): return
        for key, value in snapshot.items():
            if not isinstance(key, str) or not key.startswith(f"{account_key}:"): continue
            dt = safe_datetime(value)
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                self.augmented_positions[key] = dt
        self._augmented_loaded_accounts.add(account_key)

    async def _load_reduced_positions(self, account_key: str, *, force: bool = False) -> None:
        if account_key in self._reduced_loaded_accounts and not force:
            return
        path = self._account_path(account_key) / "reduced_positions.json"
        snapshot = await self._load_json(path)
        if not isinstance(snapshot, dict): return
        for key, value in snapshot.items():
            if not isinstance(key, str) or not key.startswith(f"{account_key}:"): continue
            dt = safe_datetime(value)
            if dt: self.reduced_positions[key] = dt
        self._reduced_loaded_accounts.add(account_key)

    async def _load_reversed_positions(self, account_key: str, *, force: bool = False) -> None:
        if account_key != "fin" or not self.config.REV_MODE: return
        if account_key in self._reversed_loaded_accounts and not force:
            return
        path = self._account_path(account_key) / "reversed_positions.json"
        snapshot = await self._load_json(path)
        if not isinstance(snapshot, dict): return
        for key, value in snapshot.items():
            if not isinstance(key, str) or not key.startswith(f"{account_key}:"): continue
            dt = safe_datetime(value)
            if dt: self.reversed_positions[key] = dt
        self._reversed_loaded_accounts.add(account_key)

    async def _load_direct_high_gain(self, account_key: str, *, force: bool = False) -> None:
        if account_key in self._direct_high_gain_loaded_accounts and not force:
            return
        path = self._account_path(account_key) / "direct_high_gain_augmented.json"
        snapshot = await self._load_json(path)
        if not isinstance(snapshot, dict): return
        valid_entries = {}
        keys_to_purge = []
        for key, payload in snapshot.items():
            if not isinstance(key, str) or not key.startswith(f"{account_key}:"): continue
            if not isinstance(payload, dict): continue
            position = self.positions.get(key)
            if not position:
                position = self.positions_by_account.get(account_key, {}).get(key)
            positionAmt = abs(safe_fetch_float(getattr(position, "positionAmt", 0.0), 0.0))
            if not position or positionAmt <= 0.0001:
                keys_to_purge.append(key)
                continue
            entry = dict(payload)
            entry["timestamp"] = safe_datetime(payload.get("timestamp")) or datetime.now(timezone.utc)
            valid_entries[key] = entry
        self.direct_high_gain_augmented.update(valid_entries)
        self._direct_high_gain_loaded_accounts.add(account_key)
        if keys_to_purge:
            logger.info(f"[_load_direct_high_gain] 🧹 Discarded {len(keys_to_purge)} closed/stale positions from load (preventing virus)")
            self._schedule_task(self.save_direct_high_gain_augmented(account_key, force=True))

    def _should_enable_account_monitors(self) -> bool:
        return bool(self.accounts)

    async def _ensure_account_client(self, account_key: str):
        account = self.accounts.get(account_key)
        if not account:
            return None
        client = getattr(account, "client", None)
        if client:
            return client
        api_key = getattr(account, "api_key", None)
        api_secret = getattr(account, "api_secret", None)
        if not api_key or not api_secret:
            logger.warning(f"[positions_service] Missing API credentials for {account_key}, skipping client init")
            return None
        try :
            client = Client(api_key=api_key, api_secret=api_secret)
            _force_ipv4_for_client(client)
            account.client = client
            return client
        except Exception as exc:
            logger.error(f"[positions_service] Failed to initialize client for {account_key}: {exc}")
            return None

    def get_client(self, account_key: str):
        account = self.accounts.get(account_key) if isinstance(self.accounts, dict) else None
        if not account:
            return None
        client = getattr(account, "client", None)
        if client:
            return client
        try :
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self._ensure_account_client(account_key))
            else:
                asyncio.run(self._ensure_account_client(account_key))
        except RuntimeError:
            try :
                asyncio.run(self._ensure_account_client(account_key))
            except RuntimeError:
                pass
        return getattr(account, "client", None)

    async def _ensure_redis_client(self) -> Optional[Redis]:
        if self._redis_client:
            return self._redis_client
        if self.redis_manager:
            for target in ["local", "gateway", "server"]:
                if hasattr(self.redis_manager, 'connections'):
                    client = self.redis_manager.connections.get(target)
                    if client:
                        self._redis_client = client
                        return client
        try :
            from utils import get_simple_redis_manager, orjson_default
            if not self.redis_manager:
                self.redis_manager = await get_simple_redis_manager()
            if self.redis_manager:
                for target in ["local", "gateway", "server"]:
                    client = self.redis_manager.connections.get(target)
                    if client:
                        self._redis_client = client
                        return client
        except Exception as exc:
            self.logger.debug(f"[positions_service] Redis unavailable: {exc}")
        return None

    async def _redis_get(self, key: str):
        client = await self._ensure_redis_client()
        return await client.get(key) if client else None

    async def _redis_set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        client = await self._ensure_redis_client()
        if client: await client.set(key, value, ex=ex)

    async def _redis_delete(self, key: str) -> None:
        client = await self._ensure_redis_client()
        if client: await client.delete(key)

    async def _ensure_redis_connectivity(self) -> bool:
        client = await self._ensure_redis_client()
        if client:
            return True
        manager = getattr(self, 'redis_manager', None)
        if manager and getattr(manager, 'connections', None):
            for target in ('gateway', 'server', 'local'):
                candidate = manager.connections.get(target)
                if candidate:
                    self._redis_client = candidate
                    return True
        try :
            shared_manager = await get_simple_redis_manager()
        except Exception:
            return False
        if not shared_manager or not getattr(shared_manager, 'connections', None):
            return False
        self.redis_manager = shared_manager
        for target in ('gateway', 'server', 'local'):
            candidate = shared_manager.connections.get(target)
            if candidate:
                self._redis_client = candidate
                return True
        return False

    async def _data_sync_loop(self, interval: float = 2.0):
        await asyncio.sleep(max(0.5, interval))
        indicator_interval = max(5.0, float(getattr(self.config, 'INDICATOR_DATA_REFRESH_SECONDS', 30.0)))
        last_indicator_sync = 0.0
        while not self._housekeeping_stop:
            loop_start = time.perf_counter()
            try :
                await self._enforce_data_sync_once(full_refresh=False)
                if loop_start - last_indicator_sync >= indicator_interval:
                    await self._enforce_data_sync_once(full_refresh=True)
                    last_indicator_sync = loop_start
            except Exception as exc:
                logger.error(f"[DATA_SYNC] enforcement loop failed: {exc}")
            await asyncio.sleep(interval)

    async def _enforce_data_sync_once(self, full_refresh: bool = True) -> None:
        redis_ready = await self._ensure_redis_connectivity()
        if not redis_ready:
            logger.debug('[DATA_SYNC] Redis unavailable; continuing with filesystem data')
        async with self._data_sync_lock:
            if full_refresh:
                await self._enforce_market_data_sync()

    async def _force_start_all_monitors(self) -> None:
        try :
            if not getattr(self, "_monitor_guard_task", None) or self._monitor_guard_task.done():
                self._monitor_guard_task = asyncio.create_task(self._monitor_reductions_watchdog())
                logger.debug("[MONITORS] reductions watchdog started")
            if not getattr(self, "_symbol_watchdog_task", None) or self._symbol_watchdog_task.done():
                self._symbol_watchdog_task = asyncio.create_task(self._symbol_monitoring_watchdog())
                logger.debug("[MONITORS] symbol watchdog started")
            if not getattr(self, "_reentry_maintenance_task", None) or self._reentry_maintenance_task.done():
                self._reentry_maintenance_task = asyncio.create_task(self._reentry_maintenance_loop())
                logger.debug("[MONITORS] reentry maintenance started")
            if not getattr(self, "_weekly_symbol_cleanup_task", None) or self._weekly_symbol_cleanup_task.done():
                self._weekly_symbol_cleanup_task = asyncio.create_task(self._weekly_symbol_cleanup_loop())
                logger.debug("[MONITORS] weekly symbol cleanup started")
            if not hasattr(self, "_monitor_reduction_tasks"): self._monitor_reduction_tasks = {}
            for account_key in list(self.accounts.keys()):
                t = self._monitor_reduction_tasks.get(account_key)
                if not t or t.done():
                    self._monitor_reduction_tasks[account_key] = asyncio.create_task(self.monitor_reductions_for_account(account_key))
                    logger.debug(f"[MONITORS] reductions started for {account_key}")
        except Exception as exc:
            logger.critical(f"[MONITORS] start failed: {exc}", exc_info=True)

    async def _ensure_monitors_running(self) -> None:
        try :
            if not getattr(self, "_monitor_guard_task", None) or self._monitor_guard_task.done():
                self._monitor_guard_task = asyncio.create_task(self._monitor_reductions_watchdog())
                logger.debug("[MONITORS] _monitor_reductions_watchdog started")
            if not getattr(self, "_symbol_watchdog_task", None) or self._symbol_watchdog_task.done():
                self._symbol_watchdog_task = asyncio.create_task(self._symbol_monitoring_watchdog())
                logger.debug("[MONITORS] _symbol_monitoring_watchdog started")
            if not getattr(self, "_reentry_maintenance_task", None) or self._reentry_maintenance_task.done():
                self._reentry_maintenance_task = asyncio.create_task(self._reentry_maintenance_loop())
                logger.debug("[MONITORS] _reentry_maintenance_loop started")
            if not getattr(self, "_weekly_symbol_cleanup_task", None) or self._weekly_symbol_cleanup_task.done():
                self._weekly_symbol_cleanup_task = asyncio.create_task(self._weekly_symbol_cleanup_loop())
                logger.debug("[MONITORS] _weekly_symbol_cleanup_loop started")
            if not hasattr(self, "_monitor_reduction_tasks"):
                self._monitor_reduction_tasks = {}
            for account_key in list(self.accounts.keys()):
                task = self._monitor_reduction_tasks.get(account_key)
                if not task or task.done():
                    self._monitor_reduction_tasks[account_key] = asyncio.create_task( self.monitor_reductions_for_account(account_key) )
                    logger.debug(f"[MONITORS] monitor_reductions_for_account started for {account_key}")
        except Exception as exc:
            logger.critical(f"[MONITORS] Failed to ensure monitors running: {exc}", exc_info=True)
    @staticmethod

    def _decode_payload(raw: Any) -> Dict[str, Any]:
        return decode_payload(raw)

    async def _enforce_market_data_sync(self) -> None:
        latest_path = Path(getattr(self.config, 'LATEST_MARKET_DATA_FILE', self.config.DATA_DIR / 'latest_market_data.json'))
        file_payload = await self._load_json_file_content(latest_path) if await aio_os.path.exists(str(latest_path)) else {}
        if not isinstance(file_payload, dict):
            file_payload = {}
        file_ts = None
        if latest_path.exists():
            try :
                file_ts = datetime.fromtimestamp(latest_path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                file_ts = None
        redis_key = getattr(self.config, 'REDIS_KEY_MARKET_DATA', 'latest_market_data')
        try :
            raw = await self._redis_get(redis_key)
        except Exception:
            raw = None
        redis_payload = self._decode_payload(raw)
        if not isinstance(redis_payload, dict):
            redis_payload = {}
        redis_ts = _snapshot_timestamp(redis_payload) if redis_payload else None
        payload_source = None
        ts_source = None
        if redis_payload and file_payload:
            if redis_ts and file_ts:
                payload_source, ts_source = (redis_payload, redis_ts) if redis_ts >= file_ts else (file_payload, file_ts)
            elif redis_ts:
                payload_source, ts_source = redis_payload, redis_ts
            elif file_ts:
                payload_source, ts_source = file_payload, file_ts
            else:
                payload_source = redis_payload or file_payload
        elif redis_payload:
            payload_source, ts_source = redis_payload, redis_ts
        elif file_payload:
            payload_source, ts_source = file_payload, file_ts
        else:
            payload_source = {}
        now_ts = time.time()
        if now_ts - self._last_data_sync_log > 1.0:
            if payload_source:
                source_name = 'redis' if payload_source is redis_payload else 'file' if payload_source is file_payload else 'fallback'
                logger.debug(f"[DATA_SYNC] market_data loaded from {source_name}")
            else:
                logger.critical('[DATA_SYNC] market_data missing in redis and filesystem')
            self._last_data_sync_log = now_ts
        encoded = None
        if isinstance(payload_source, dict) and payload_source:
            try :
                encoded = orjson.dumps(payload_source) 
            except Exception:
                encoded = json.dumps(payload_source, separators=(', ', ':'), default=str).encode()
            if encoded != self.raw_indicators_json_content:
                self.raw_indicators_json_content = encoded
                self.indicators_timestamp = ts_source if isinstance(ts_source, datetime) else datetime.now(timezone.utc)
        elif not redis_payload and not file_payload:
            self.raw_indicators_json_content = None

    async def _load_positions_raw_from_files(self, account_key: str) -> Tuple[Dict[str, Any], Optional[datetime]]:
            combined: Dict[str, Any] = {}
            latest_ts: Optional[datetime] = None
            try :
                symbols_file = Path(getattr(self.config, "SYMBOLS_FILE", Path(self.config.BASE_PATH) / "symbols.json"))
                if symbols_file.exists():
                    sym_content = await self._load_json(symbols_file)
                    if isinstance(sym_content, dict) and "symbols" in sym_content:
                        master_symbols = set(str(s).strip().upper() for s in sym_content["symbols"])
                    elif isinstance(sym_content, list):
                        master_symbols = set(str(s).strip().upper() for s in sym_content)
                    else:
                        master_symbols = set()
                else:
                    master_symbols = set()
            except Exception as e:
                logger.error(f"[{account_key}] Failed to load symbols list: {e}")
                master_symbols = set()
            for side in ('LONG', 'SHORT'):
                file_path = self.get_position_file(account_key, side)
                snapshot = await self._load_json(file_path)
                if isinstance(snapshot, dict):
                    expected_prefix = f"{account_key}:"
                    filtered_snapshot = {}
                    for k, v in snapshot.items():
                        if isinstance(k, str) and k.startswith(expected_prefix):
                            try :
                                parsed_account, _, _ = parse_position_key(k)
                                if parsed_account == account_key:
                                    filtered_snapshot[k] = v
                                else:
                                    logger.warning(f"[_load_positions_raw_from_files][{account_key}:{side}] ⚠️ SKIPPING position with mismatched account: {k} (parsed: {parsed_account}, expected: {account_key})")
                            except Exception:
                                filtered_snapshot[k] = v
                        else:
                            pass
                    combined.update(filtered_snapshot)
                try :
                    if await aio_os.path.exists(str(file_path)):
                        stat_result = await aio_os.stat(str(file_path))
                        ts = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc)
                        if not latest_ts or ts > latest_ts:
                            latest_ts = ts
                except Exception: pass
                missing_symbols = set()
                if master_symbols:
                    for symbol in master_symbols:
                        pk_std = construct_position_key(account_key, symbol, side)
                        pk_simple = f"{account_key}:{symbol}_{side}"
                        if pk_std not in combined and pk_simple not in combined:
                            missing_symbols.add(symbol)
                if missing_symbols:
                    missing_count = len(missing_symbols)
                    logger.warning(f"[{account_key}:{side}] Missing {missing_count} positions (e.g. {list(missing_symbols)[:3]}). Scanning backups to recover...")
                    backup_dir = file_path.parent / "backups"
                    if backup_dir.exists():
                        patterns = [ f"{side.lower()}_positions.json_backup_*.json", f"{side.lower()}_positions_backup_*.json", f"*{side.lower()}*backup*.json" ]
                        backup_files = []
                        for pat in patterns:
                            backup_files.extend(list(backup_dir.glob(pat)))
                        backup_files = sorted(list(set(backup_files)), key=lambda x: x.stat().st_mtime, reverse=True)
                        for backup_file in backup_files:
                            if not missing_symbols:
                                break 
                            try :
                                bk_data = await self._load_json(backup_file)
                                if not bk_data or not isinstance(bk_data, dict):
                                    continue
                                recovered_from_this_file = 0
                                expected_prefix = f"{account_key}:"
                                for bk_key, bk_val in bk_data.items():
                                    if not isinstance(bk_key, str) or not bk_key.startswith(expected_prefix):
                                        continue
                                    if not isinstance(bk_val, dict): continue
                                    sym = bk_val.get("symbol")
                                    if not sym:
                                        try :
                                            bk_account_key, sym, _ = parse_position_key(bk_key)
                                            if bk_account_key != account_key:
                                                continue
                                        except Exception: 
                                            if ":" in bk_key:
                                                parts = bk_key.split(":")
                                                if len(parts) >= 2 and parts[0] != account_key:
                                                    continue
                                                sym = parts[1].split("_")[0]
                                    if sym:
                                        sym = str(sym).strip().upper()
                                        if sym in missing_symbols:
                                            try :
                                                std_key = construct_position_key(account_key, sym, side)
                                            except Exception:
                                                std_key = f"{account_key}:{sym}_{side}"
                                            bk_val["symbol"] = sym
                                            bk_val["position_side"] = side
                                            combined[std_key] = bk_val
                                            missing_symbols.remove(sym)
                                            recovered_from_this_file += 1
                                if recovered_from_this_file > 0:
                                    logger.info(f"[{account_key}:{side}] Recovered {recovered_from_this_file} positions from {backup_file.name}")
                            except Exception as e:
                                logger.debug(f"Error reading backup {backup_file.name}: {e}")
                                continue
                    if missing_symbols:
                        logger.error(f"[{account_key}:{side}] CRITICAL: Still missing {len(missing_symbols)} positions after checking ALL backups! {list(missing_symbols)[:5]}...")
                    else:
                        logger.info(f"[{account_key}:{side}] ✅ All missing positions successfully recovered from backups.")
            return combined, latest_ts

    async def ensure_position_present(self, account_key: str, symbol: str, position_side: str, position_key: Optional[str] = None) -> Optional[Position]:
        try :
            if not position_key:
                position_key = construct_position_key(account_key, symbol, position_side)
            position = self.positions_by_account.get(account_key, {}).get(position_key)
            if position:
                self.positions[position_key] = position
                return position
            existing_global = self.positions.get(position_key)
            if existing_global:
                self.positions_by_account.setdefault(account_key, {})[position_key] = existing_global
                return existing_global
            self.logger.warning(f"⚠️ [ensure_position_present] {position_key} missing from memory/backups. Forcing account reload...")
            await self.load_account(account_key, force=True)
            restored = await self.restore_position_from_backups(account_key, symbol, position_side)
            if restored:
                self.logger.warning(f"🔄 [ensure_position_present] RESTORED {position_key} from backup")
                self.positions_by_account.setdefault(account_key, {})[position_key] = restored
                self.positions[position_key] = restored
                return restored 
            position = self.positions_by_account.get(account_key, {}).get(position_key)
            if position:
                self.logger.info(f"✅ [ensure_position_present] {position_key} recovered after forced reload.")
                return position
            self.logger.critical(f"❌❌ [ensure_position_present] CRITICAL: Position {position_key} ABSOLUTELY MISSING (Memory, Backup, Disk).")
            self.logger.critical(f"❌❌ [ensure_position_present] Returning None to protect data integrity. INVESTIGATE FILE SYSTEM.")
            return None
        except Exception as e:
            self.logger.error(f"[ensure_position_present] Failure ensuring position {position_key}: {e}", exc_info=True)
            return None

    async def _write_positions_payload_to_files(self, account_key: str, payload: Dict[str, Any]) -> None:
        logger.debug(f"[_write_positions_payload_to_files][{account_key}] ⚠️ DISABLED - only atomic_save_positions should write position files")
        return
        expected_count = self._get_expected_symbol_count()
        if not isinstance(payload, dict) or len(payload) == 0 or len(payload) < expected_count * 2:
            return
        per_side: Dict[str, Dict[str, Any]] = {'LONG': {}, 'SHORT': {}}
        for position_key, entry in payload.items():
            if not isinstance(position_key, str) or not isinstance(entry, dict):
                continue
            if position_key.upper() in ('POSITIONS', 'META', 'TIMESTAMP', 'TIME', 'DATA', 'INFO', 'ACCOUNT_KEY', 'SIDE'):
                continue
            side = (entry.get('positionSide') or entry.get('position_side') or '').upper()
            if side not in ('LONG', 'SHORT'):
                continue
            per_side[side][position_key] = entry
        for side, content in per_side.items():
            if len(content) == 0 or len(content) < expected_count:
                continue
            file_path = self.get_position_file(account_key, side)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            await self._atomic_write(file_path, content)

    async def try_add_order_redis(self, lock_key: str, expiry_seconds: int = 45) -> bool:
        if not hasattr(self, "_local_locks"):
            self._local_locks: Dict[str, Dict[str, float]] = {}
            self._local_locks_cleanup_task: Optional[asyncio.Task] = None
        normalized = lock_key if lock_key.startswith("execute_now:") else f"execute_now:{lock_key}"
        client = await self._ensure_redis_client()
        if client:
            try :
                result = await client.set(normalized, "1", ex=expiry_seconds, nx=True)
                if result:
                    return True
            except Exception as exc:
                logger.debug(f"[locks] Redis setnx failed for {normalized}: {exc}")
        return await self._try_local_lock(normalized, expiry_seconds)

    async def _try_local_lock(self, normalized_key: str, expiry_seconds: int) -> bool:
        now = time.time()
        expired = [key for key, meta in self._local_locks.items() if meta["expires"] <= now]
        for key in expired:
            self._local_locks.pop(key, None)
        if normalized_key in self._local_locks and self._local_locks[normalized_key]["expires"] > now:
            return False
        self._local_locks[normalized_key] = {"expires": now + expiry_seconds}
        if not self._local_locks_cleanup_task or self._local_locks_cleanup_task.done():
            self._local_locks_cleanup_task = asyncio.create_task(self._cleanup_local_locks())
        return True

    async def _cleanup_local_locks(self) -> None:
        try :
            while self._local_locks:
                await asyncio.sleep(5.0)
                now = time.time()
                expired = [key for key, meta in self._local_locks.items() if meta["expires"] <= now]
                for key in expired:
                    self._local_locks.pop(key, None)
        except asyncio.CancelledError:
            pass

    async def clear_active_lock(self, position_key: str) -> None:
        keys = [position_key]
        if position_key and not position_key.startswith("execute_now:"):
            keys.extend([f"execute_now:{position_key}", f"execute_now:{position_key}:BUY", f"execute_now:{position_key}:SELL"])
        try :
            redis_manager = getattr(self, "redis_manager", None)
            if redis_manager and hasattr(redis_manager, "delete"):
                for key in keys:
                    try :
                        await redis_manager.delete(key)
                    except Exception as exc:
                        logger.debug(f"[locks] redis_manager delete failed for {key}: {exc}")
            client = await self._ensure_redis_client()
            if client:
                for key in keys:
                    try :
                        await client.delete(key)
                    except Exception as exc:
                        logger.debug(f"[locks] redis delete failed for {key}: {exc}")
            if hasattr(self, "_local_locks"):
                for key in keys:
                    self._local_locks.pop(key, None)
        except Exception as exc:
            logger.debug(f"[locks] clear_active_lock error for {position_key}: {exc}")

    async def is_order_locked(self, position_key: str) -> bool:
        keys = [position_key]
        if position_key and not position_key.startswith("execute_now:"):
            keys.extend([f"execute_now:{position_key}", f"execute_now:{position_key}:BUY", f"execute_now:{position_key}:SELL"])
        normalized_keys = [k if k.startswith("execute_now:") else f"execute_now:{k}" for k in keys]
        all_keys = list(set(keys + normalized_keys))
        try :
            redis_manager = getattr(self, "redis_manager", None)
            if redis_manager and hasattr(redis_manager, "get"):
                for key in all_keys:
                    try :
                        value = await redis_manager.get(key)
                        if value is not None:
                            return True
                    except Exception as exc:
                        logger.debug(f"[locks] redis_manager get failed for {key}: {exc}")
                        continue
            client = await self._ensure_redis_client()
            if client:
                for key in all_keys:
                    try :
                        value = await client.get(key)
                        if value is not None:
                            return True
                    except Exception as exc:
                        logger.debug(f"[locks] redis get failed for {key}: {exc}")
                        continue
            if hasattr(self, "_local_locks"):
                now = time.time()
                for key in all_keys:
                    meta = self._local_locks.get(key)
                    if meta and meta.get("expires", 0.0) > now:
                        return True
            return False
        except Exception as exc:
            logger.debug(f"[locks] is_order_locked error for {position_key}: {exc}")
            return False

    async def get_cached_positions(self, account_key: str) -> Dict[str, Position]:
        async with self._positions_lock:
            return dict(self.positions_by_account.get(account_key, {}))

    async def start_account_monitors(self) -> None:
        try :
            for account_key in self.accounts.keys():
                if not self._should_enable_account_monitors() or account_key == 'flz':
                    logger.warning("[sta rt_account_monitors] Account monitors disabled (no accounts)")
                    return
            internal_fetcher_flag = self.base_path / ".internal_fetcher_enabled"
            if internal_fetcher_flag.exists():
                logger.critical(f"[st art_account_monitors] 🚨 Watchdog signaled external fetcher failed - ENABLING INTERNAL FETCHER")
                self._enable_auto_fetch = True
            disabled_flag = self.base_path / ".internal_fetcher_disabled"
            if disabled_flag.exists():
                logger.debug(f"[start_ account_monitors] Watchdog signaled external fetcher working - disabling internal fetcher")
                self._enable_auto_fetch = False
            logger.debug(f"[start_acco unt_monitors] Starting monitors for {len(self.accounts)} accounts: {list(self.accounts.keys())} (REST fetch: 30s, WebSocket: real-time, auto_fetch={self._enable_auto_fetch})")
            self._account_monitor_stop = False
            if not hasattr(self, '_periodic_full_save_task') or self._periodic_full_save_task is None or self._periodic_full_save_task.done():
                self._periodic_full_save_task = asyncio.create_task(self._periodic_full_save_loop())
                logger.debug("[start_acc ount_monitors] Started periodic full save loop")
            if not hasattr(self, '_periodic_update_zero_positions_task') or self._periodic_update_zero_positions_task is None or self._periodic_update_zero_positions_task.done():
                self._periodic_update_zero_positions_task = asyncio.create_task(self._periodic_update_zero_positions())
                logger.debug("[start_account_mo nitors] Started periodic update zero positions task (every hour)")
            for account_key, account in self.accounts.items():
                if getattr(self, '_enable_auto_fetch', True):
                    if account_key not in self._rest_poll_tasks or self._rest_poll_tasks[account_key].done():

                        async def _poll_loop_wrapper(ak):
                            try :
                                if config.VERBOSE_FETCH_LOGGING: logger.info(f"[start_ account_monitors][{ak}] 🚨 CALLING _rest_poll_loop NOW")
                                await self._rest_poll_loop(ak)
                                if config.VERBOSE_FETCH_LOGGING: logger.info(f"[start_ account_monitors][{ak}] _rest_poll_loop COMPLETED")
                            except Exception as poll_err:
                                logger.error(f"[start _account_monitors][{ak}] CRITICAL: _rest_poll_loop FAILED: {poll_err}", exc_info=True)
                        self._rest_poll_tasks[account_key] = asyncio.create_task(_poll_loop_wrapper(account_key))
                    else:
                        task_status = "running" if not self._rest_poll_tasks[account_key].done() else "done"
                        logger.warning(f"[start_acc ount_monitors][{account_key}] ⚠️ REST polling loop task already exists (status: {task_status}) - recreating if done")
                        if self._rest_poll_tasks[account_key].done():
                            try :
                                result = self._rest_poll_tasks[account_key].result()
                                logger.warning(f"[star t_account_monitors][{account_key}] Previous task completed with result: {result}")
                            except Exception as e:
                                logger.error(f"[start_a ccount_monitors][{account_key}] Previous task failed: {e}")
                            self._rest_poll_tasks[account_key] = asyncio.create_task(self._rest_poll_loop(account_key))
                            logger.debug(f"[start_accou nt_monitors][{account_key}] REST polling loop task recreated (30s interval)")
                if account_key not in self.websocket_managers:
                    api_key = getattr(account, 'api_key', None) or getattr(account, 'api_key_plain', None)
                    api_secret = getattr(account, 'api_secret', None) or getattr(account, 'api_secret_plain', None)
                    if not api_key or not api_secret:
                        logger.warning(f"[start_accou nt_monitors][{account_key}] ⚠️ Missing API credentials; skipping WebSocket manager initialization")
                    else:
                        manager = WebSocketManager(account_keys=[account_key], api_key=api_key, api_secret=api_secret, config=self.config, symbols=None, service=self)
                        self.websocket_managers[account_key] = manager
                        if not self.primary_ws_manager:
                            self.primary_ws_manager = manager

                        async def _ws_start_wrapper(ak, mgr):
                            try :
                                logger.info(f"[start_ account_monitors][{ak}] 🚨 Starting WebSocket manager for real-time updates")
                                await mgr.start(ak)
                                logger.info(f"[start_ account_monitors][{ak}] WebSocket manager started and listening")
                            except Exception as ws_err:
                                logger.error(f"[start _account_monitors][{ak}] CRITICAL: WebSocket manager start FAILED: {ws_err}", exc_info=True)
                        asyncio.create_task(_ws_start_wrapper(account_key, manager))
                        logger.info(f"[start_accou nt_monitors][{account_key}] WebSocket manager task created for real-time position updates")
        except Exception as e:
            logger.error(f"[start_ac count_monitors] CRITICAL: Failed to start account monitors: {e}", exc_info=True)

    async def stop_account_monitors(self) -> None:
        self._account_monitor_stop = True
        for task in list(self._rest_poll_tasks.values()):
            if task and not task.done():
                task.cancel()
        for task in list(self._rest_poll_tasks.values()):
            if task:
                try :
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug(f"[positions_service] monitor task exit error: {exc}")
        self._rest_poll_tasks.clear()
        for manager in list(self.websocket_managers.values()):
            try :
                await manager.stop()
            except Exception as exc:
                logger.debug(f"[{', '.join(manager.account_keys)}] websocket manager stop error: {exc}")
        self.websocket_managers.clear()
        self.primary_ws_manager = None
        for session in list(self._account_sessions.values()):
            try :
                if session and not session.closed:
                    await session.close()
            except Exception:
                pass
        self._account_sessions.clear()
        self._account_listen_keys.clear()

    async def _broadcast_positions_to_redis(self, account_key: str, arg2: Any = None, arg3: Any = None, allow_incomplete: bool = True):
        """ Handles both signatures: 1. (account_key, updates_dict) 2. (account_key, side_string, updates_dict)
        2026-04-30: All redis ops wrapped in asyncio.wait_for(timeout=5.0) to prevent process_account_update HUNG > 120s when Redis is slow on multi-MB snapshot SET. Broadcast is best-effort — a failed broadcast must NOT block PAU; the next cycle re-sends the full snapshot."""
        if isinstance(arg2, str) and isinstance(arg3, dict):
            actual_updates = arg3
        elif isinstance(arg2, dict):
            actual_updates = arg2
        else:
            actual_updates = {}
        now = time.time()
        if not hasattr(self, '_last_full_redis_sync'):
            self._last_full_redis_sync = {}
        try :
            redis_manager = self.redis_manager or await get_simple_redis_manager()
            if not redis_manager: return
            if actual_updates:
                channel = REDIS_CHANNELS.get("position_updates", "position_updates")
                delta_payload = json_dumps({ "account_key": account_key, "positions": actual_updates, "type": "delta" })
                if isinstance(delta_payload, bytes): delta_payload = delta_payload.decode('utf-8')
                try:
                    await asyncio.wait_for(redis_manager.publish(channel, delta_payload), timeout=5.0)
                except asyncio.TimeoutError:
                    self.logger.error(f"[REDIS_SYNC_FAIL][{account_key}] publish delta timed out >5s — dropping (next PAU will re-broadcast)")
            last_full = self._last_full_redis_sync.get(account_key, 0)
            if (now - last_full) >= 3.0 or not actual_updates:
                account_positions = self.positions_by_account.get(account_key, {})
                if not account_positions: return
                full_account_snapshot = { pk: p.to_dict() for pk, p in account_positions.items() if hasattr(p, "to_dict") }
                if full_account_snapshot:
                    redis_key = f"positions:{account_key}"
                    full_payload = json_dumps({ "positions": full_account_snapshot, "meta": {"timestamp": now, "account": account_key} })
                    if isinstance(full_payload, bytes): full_payload = full_payload.decode('utf-8')
                    try:
                        await asyncio.wait_for(redis_manager.set(redis_key, full_payload, ex=300), timeout=5.0)
                        self._last_full_redis_sync[account_key] = now
                    except asyncio.TimeoutError:
                        self.logger.error(f"[REDIS_SYNC_FAIL][{account_key}] full-snapshot SET timed out >5s ({len(full_payload)} bytes) — dropping (next PAU will retry)")
        except Exception as e:
            self.logger.error(f"[REDIS_SYNC_FAIL][{account_key}] {e}")

    async def _periodic_update_zero_positions(self) -> None:
        await asyncio.sleep(10)
        while not self._account_monitor_stop:
            try :
                if getattr(self.config, 'VERBOSE_FETCH_LOGGING', False):
                    logger.info("[_periodic_update_zero_positions] 🔄 Starting evaluation of zero positions...")
                updated_count = 0
                for account_key in list(self.accounts.keys()):
                    account_positions = self.positions_by_account.get(account_key, {})
                    for position_key, position in list(account_positions.items()):
                        if not position:
                            continue
                        positionAmt = abs(float(getattr(position, 'positionAmt', 0) or 0))
                        if positionAmt == 0.0:
                            try :
                                symbol = position.symbol
                                current_price = float(getattr(position, 'mark_price', 0) or 0.0)
                                if current_price <= 0:
                                    try :
                                        current_price = await quick_price(symbol) or 0.0
                                    except Exception:
                                        pass
                                if current_price > 0:
                                    await self.handle_unchanged_position( position, position_key, 0.0, current_price )
                                    updated_count += 1
                            except Exception as e:
                                logger.error(f"[_periodic_update_zero_positions] Error evaluating {position_key}: {e}", exc_info=True)
                # Auto-confirm stuck zero reports older than 60 seconds
                now = datetime.now(timezone.utc)
                for pk in list(self.zero_report_tracker.keys()):
                    rec = self.zero_report_tracker.get(pk)
                    if not rec:
                        continue
                    first_seen = rec.get("first_seen_zero_at")
                    if not first_seen:
                        continue
                    if isinstance(first_seen, str):
                        try:
                            first_seen = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
                        except Exception:
                            continue
                    if not hasattr(first_seen, 'tzinfo') or first_seen.tzinfo is None:
                        first_seen = first_seen.replace(tzinfo=timezone.utc)
                    age_seconds = (now - first_seen).total_seconds()
                    if age_seconds >= 60 and rec.get("count", 0) < config.ZERO_CONFIRMATION_THRESHOLD_WS:
                        position = self.positions.get(pk)
                        if position and abs(float(getattr(position, 'positionAmt', 0) or 0)) > 0:
                            prev_amt = abs(float(position.positionAmt))
                            current_price = float(getattr(position, 'mark_price', 0) or 0.0)
                            logger.critical(f"[STUCK_ZERO_RECONCILE][{pk}] Zero report stuck for {age_seconds:.0f}s (count={rec.get('count')}/{config.ZERO_CONFIRMATION_THRESHOLD_WS}). Auto-confirming and zeroing. prev_amt={prev_amt}")
                            try:
                                await self.handle_reduction(position, pk, prev_amt, 0.0, prev_amt, current_price, position.entry_price or current_price, reduction_source="stuck_zero_reconcile")
                                _old = position.positionAmt
                                position.positionAmt = 0.0
                                logger.critical(f"[POSAMT_WRITE][STUCK_ZERO][{pk}] {_old} -> 0.0 (stuck_zero_reconcile after {age_seconds:.0f}s)")
                                self._clear_zero_report(pk)
                                updated_count += 1
                            except Exception as e:
                                logger.error(f"[STUCK_ZERO_RECONCILE][{pk}] Failed: {e}", exc_info=True)
                if updated_count > 0:
                    logger.info(f"[_periodic_update_zero_positions] Updated {updated_count} positions (including stuck zero reconciliation)")
            except Exception as e:
                logger.error(f"[_periodic_update_zero_positions] Error in periodic task: {e}", exc_info=True)
            await asyncio.sleep(300) 

    async def _periodic_full_save_loop(self) -> None:
        await asyncio.sleep(1.0)
        while not self._account_monitor_stop:
            try :
                now = time.time()
                if now - self._last_full_save_time >= self._full_save_interval:
                    for acc_key in list(self.positions_by_account.keys()):
                        try:
                            all_keys = set(self.positions_by_account.get(acc_key, {}).keys())
                            if all_keys:
                                await self._broadcast_positions_to_redis(acc_key, all_keys)
                        except Exception as be:
                            logger.debug(f"[_periodic_full_save_loop] Redis broadcast failed for {acc_key}: {be}")
                    self._last_full_save_time = now
                    self._mark_positions_dirty()
                await asyncio.sleep(self._full_save_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[_periodic_full_save_loop] Error: {e}", exc_info=True)
                await asyncio.sleep(self._full_save_interval)

    async def _broadcast_direct_high_gain_to_redis(self, account_key: str, direct_high_gain_data: Dict[str, Any]) -> None:
        try :
            redis_manager = self.redis_manager
            if not redis_manager:
                try : redis_manager = await get_simple_redis_manager()
                except Exception: return
            if not redis_manager: return
            redis_key = f"direct_high_gain_augmented:{account_key}"
            channel = REDIS_CHANNELS.get("direct_high_gain_augmented", "direct_high_gain")
            payload_bytes = json_dumps({direct_high_gain_data})
            payload_str = payload_bytes.decode('utf-8')
            await self.redis_manager.set(redis_key, payload_str, ex=300)
            pub_bytes = json_dumps({ "account_key": account_key, "type": "direct_high_gain_augmented", "data": direct_high_gain_data })
            await self.redis_manager.publish(channel, pub_bytes.decode('utf-8'))
        except Exception as exc:
            logger.debug(f"[_broadcast_stop_levels_to_redis] Failed: {exc}")

    async def _broadcast_stop_levels_to_redis(self, account_key: str, side: str, stop_data: Dict[str, Any]) -> None:
        try :
            if not self.redis_manager: return
            payload_bytes = json_dumps({"account_key": account_key, "side": side, "data": stop_data})
            payload_str = payload_bytes.decode('utf-8')
            redis_key = f"stop_levels:{account_key}:{side.lower()}"
            await self.redis_manager.set(redis_key, payload_str, ex=300)
            channel = REDIS_CHANNELS.get("position_updates", "position_updates")
            pub_bytes = json_dumps({ "account_key": account_key, "type": "stop_levels", "side": side.lower(), "data": stop_data })
            await self.redis_manager.publish(channel, pub_bytes.decode('utf-8'))
        except Exception as exc:
            logger.debug(f"[_broadcast_stop_levels_to_redis] Failed: {exc}")

    async def _broadcast_ladder_levels_to_redis(self, account_key: str, side: str, ladder_data: Dict[str, Any]) -> None:
        # 2026-05-08: outer wait_for guards each redis op. handle_reduction →
        # save_ladder_levels → here was the path that hung 105s on 2026-05-08
        # (PAU watchdog fired 3× on flz). Mirrors _broadcast_positions_to_redis
        # which was hardened the same way on 2026-04-30.
        try :
            if not self.redis_manager: return
            payload_bytes = json_dumps(ladder_data)
            payload_str = payload_bytes.decode('utf-8')
            redis_key = f"ladder_levels:{account_key}:{side.lower()}"
            try:
                await asyncio.wait_for(self.redis_manager.set(redis_key, payload_str, ex=300), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_ladder_levels][{account_key}:{side}] set timed out >5s — dropping (next save will retry)")
            channel = REDIS_CHANNELS.get("position_updates", "position_updates")
            pub_bytes = json_dumps({ "account_key": account_key, "type": "ladder_levels", "side": side.lower(), "data": ladder_data })
            try:
                await asyncio.wait_for(self.redis_manager.publish(channel, pub_bytes.decode('utf-8')), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_ladder_levels][{account_key}:{side}] publish timed out >5s — dropping")
        except Exception as e:
            logger.debug(f"[broadcast_ladder_levels] Redis broadcast failed: {e}")

    async def _broadcast_reentry_levels_to_redis(self, account_key: str, side: str, reentry_data: Dict[str, Any]) -> None:
        # 2026-05-08: outer wait_for guards each redis op (see _broadcast_ladder_levels_to_redis).
        try :
            if not self.redis_manager: return
            payload_bytes = json_dumps(reentry_data)
            payload_str = payload_bytes.decode('utf-8')
            redis_key = f"reentry_levels:{account_key}:{side.lower()}"
            try:
                await asyncio.wait_for(self.redis_manager.set(redis_key, payload_str, ex=300), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_reentry_levels][{account_key}:{side}] set timed out >5s — dropping")
            channel = REDIS_CHANNELS.get("position_updates", "position_updates")
            pub_bytes = json_dumps({ "account_key": account_key, "type": "reentry_levels", "side": side.lower(), "data": reentry_data })
            try:
                await asyncio.wait_for(self.redis_manager.publish(channel, pub_bytes.decode('utf-8')), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_reentry_levels][{account_key}:{side}] publish timed out >5s — dropping")
        except Exception as e:
            logger.debug(f"[broadcast_reentry_levels] Redis broadcast failed: {e}")

    async def _broadcast_augmented_positions_to_redis(self, account_key: str, augmented_data: Dict[str, Any]) -> None:
        # 2026-05-08: outer wait_for guards each redis op (see _broadcast_ladder_levels_to_redis).
        try :
            if not self.redis_manager: return
            payload_bytes = json_dumps(augmented_data)
            payload_str = payload_bytes.decode('utf-8')
            redis_key = f"augmented_positions:{account_key}"
            try:
                await asyncio.wait_for(self.redis_manager.set(redis_key, payload_str, ex=300), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_augmented][{account_key}] set timed out >5s — dropping")
            channel = REDIS_CHANNELS.get("position_updates", "position_updates")
            pub_bytes = json_dumps({ "account_key": account_key, "type": "augmented_positions", "data": augmented_data })
            try:
                await asyncio.wait_for(self.redis_manager.publish(channel, pub_bytes.decode('utf-8')), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_augmented][{account_key}] publish timed out >5s — dropping")
        except Exception as e:
            logger.debug(f"[broadcast_augmented] Redis broadcast failed: {e}")

    async def _broadcast_reduced_positions_to_redis(self, account_key: str, reduced_data: Dict[str, Any]) -> None:
        # 2026-05-08: outer wait_for guards each redis op (see _broadcast_ladder_levels_to_redis).
        try :
            if not self.redis_manager: return
            payload_bytes = json_dumps(reduced_data)
            payload_str = payload_bytes.decode('utf-8')
            redis_key = f"reduced_positions:{account_key}"
            try:
                await asyncio.wait_for(self.redis_manager.set(redis_key, payload_str, ex=300), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_reduced][{account_key}] set timed out >5s — dropping")
            channel = REDIS_CHANNELS.get("position_updates", "position_updates")
            pub_bytes = json_dumps({ "account_key": account_key, "type": "reduced_positions", "data": reduced_data })
            try:
                await asyncio.wait_for(self.redis_manager.publish(channel, pub_bytes.decode('utf-8')), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_reduced][{account_key}] publish timed out >5s — dropping")
        except Exception as e:
            logger.debug(f"[broadcast_reduced] Redis broadcast failed: {e}")

    async def _broadcast_reversed_positions_to_redis(self, account_key: str, reversed_data: Dict[str, Any]) -> None:
        # 2026-05-08: outer wait_for guards each redis op (see _broadcast_ladder_levels_to_redis).
        try :
            if not self.redis_manager: return
            payload_bytes = json_dumps(reversed_data)
            payload_str = payload_bytes.decode('utf-8')
            redis_key = f"reversed_positions:{account_key}"
            try:
                await asyncio.wait_for(self.redis_manager.set(redis_key, payload_str, ex=300), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_reversed][{account_key}] set timed out >5s — dropping")
            channel = REDIS_CHANNELS.get("position_updates", "position_updates")
            pub_bytes = json_dumps({ "account_key": account_key, "type": "reversed_positions", "data": reversed_data })
            try:
                await asyncio.wait_for(self.redis_manager.publish(channel, pub_bytes.decode('utf-8')), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"[broadcast_reversed][{account_key}] publish timed out >5s — dropping")
        except Exception as e:
            logger.debug(f"[broadcast_reversed] Redis broadcast failed: {e}")

    async def _verify_positions_written(self, file_path: Path, expected: Dict[str, Any]) -> bool:
        try :
            disk_snapshot = await self._load_json_file_content(file_path)
            if not isinstance(disk_snapshot, dict):
                logger.error(f"[{file_path.name}] Verification failed: invalid JSON structure")
                return False
            try :
                account_key = file_path.parent.name
            except Exception:
                account_key = ""
            name_lower = file_path.name.lower()
            side = "LONG" if "long_positions.json" == name_lower else ("SHORT" if "short_positions.json" == name_lower else "")
            for key, expected_entry in expected.items():
                norm_key = str(key)
                if ":" not in norm_key and side and account_key:
                    sym = norm_key.split(":")[-1].replace("_LONG", "").replace("_SHORT", "").strip().upper()
                    try :
                        norm_key = construct_position_key(account_key, sym, side)
                    except Exception:
                        norm_key = f"{account_key}:{sym}_{side}"
                disk_entry = disk_snapshot.get(norm_key)
                if not isinstance(disk_entry, dict):
                    logger.error(f"[{file_path.name}] Verification failed: missing entry for {norm_key}")
                    return False
                amt_expected = safe_fetch_float(expected_entry.get("positionAmt", 0.0))
                amt_disk = safe_fetch_float(disk_entry.get("positionAmt", 0.0))
                if abs(amt_expected - amt_disk) > 1e-6:
                    logger.error(f"[{file_path.name}] Verification mismatch for {norm_key}: expected {amt_expected:.6f}, got {amt_disk:.6f}")
                    return False
            expected_pk_keys = set()
            for key in expected.keys():
                k = str(key)
                if ":" in k:
                    expected_pk_keys.add(k)
                elif account_key and side:
                    sym = k.split(":")[-1].replace("_LONG", "").replace("_SHORT", "").strip().upper()
                    expected_pk_keys.add(f"{account_key}:{sym}_{side}")
            extra_keys = set(disk_snapshot.keys()) - expected_pk_keys
            if extra_keys:
                logger.warning(f"[{file_path.name}] Verification warning: unexpected keys {extra_keys}")
            return True
        except Exception as e:
            logger.error(f"[{file_path.name}] Verification error: {e}")
            return False

    async def _is_time_for_backup(self, backup_dir: str, prefix: str, hours: int = 12) -> bool:
        now = datetime.now(timezone.utc) 
        backup_path = Path(backup_dir)
        if not backup_path.is_dir():
            return True 
        try :
            backup_files = []
            for file_path in backup_path.iterdir():
                if (file_path.is_file() and file_path.name.startswith(f"{prefix}_positions_backup_") and file_path.name.endswith(".json")):
                    try :
                        mtime = file_path.stat().st_mtime
                        backup_files.append((mtime, file_path))
                    except OSError:
                        continue
            if not backup_files:
                return True
            backup_files.sort(key=lambda x: x[0], reverse=True)
            newest_mtime = backup_files[0][0]
            newest_time = datetime.fromtimestamp(newest_mtime, tz=timezone.utc)
            diff_hours = (now - newest_time).total_seconds() / 3600
            return diff_hours >= hours
        except Exception as e:
            logger.warning(f"Error checking backup time for {backup_dir}: {e}")
            return True

    async def prune_old_backups(self, backup_folder: str, prefix: str):
        backup_path = Path(backup_folder)
        if not backup_path.is_dir(): return
        try :
            now = datetime.now(timezone.utc)
            files = []
            for f in backup_path.glob(f"{prefix}*_backup_*.json"):
                try :
                    files.append((datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc), f))
                except Exception: continue
            if not files: return
            files.sort(key=lambda x: x[0], reverse=True)
            keep = set()
            buckets = set()
            for dt, path in files:
                age_s = (now - dt).total_seconds()
                if age_s <= 300:
                    keep.add(path)
                    continue
                if age_s <= 3600: 
                    slot = f"15m_{dt.hour}_{dt.minute // 15}"
                elif age_s <= 14400: 
                    slot = f"1h_{dt.hour}"
                elif age_s <= 86400: 
                    slot = f"4h_{dt.hour // 4}"
                elif age_s <= 604800:
                    slot = f"1d_{dt.date()}"
                else: 
                    slot = f"1w_{dt.isocalendar()[1]}"
                if slot not in buckets:
                    keep.add(path)
                    buckets.add(slot)
            for _, path in files:
                if path not in keep:
                    path.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"[PRUNE] Logic failed for {prefix} in {backup_folder}: {e}")

    def __delitem__(self, key):
        if "positions_by_account" in str(key) or "position" in str(key):
            stack = traceback.format_stack()
            stack_str = "\n".join(stack[-10:]) 
            logger.critical(f"🚨 CRITICAL: Attempted to delete position data: {key}")
            logger.critical(f"🚨 POSITION DELETION IS FORBIDDEN - System will crash to prevent data loss")
            logger.critical(f"🚨 STACK TRACE:\n{stack_str}")
            raise RuntimeError(f"POSITION DELETION FORBIDDEN: {key}")

    async def _check_for_deletions(self, account_key: str, context: str = "") -> None:
        return
        try :
            async with self._reference_snapshot_lock:
                now = time.time()
                last_check = self._last_deletion_check.get(account_key, 0)
                if now - last_check < 0.5:
                    return
                self._last_deletion_check[account_key] = now
                reference_account = self._positions_by_account_reference_snapshot.get(account_key, {})
                mutable_account = self.positions_by_account.get(account_key, {})
                deleted_keys = set(reference_account.keys()) - set(mutable_account.keys())
                if deleted_keys:
                    logger.warning(f"[_check_for_deletions][{account_key}] 🚨 DELETION DETECTED: {len(deleted_keys)} positions missing! Context: {context}")
                    restored = 0
                    for key in deleted_keys:
                        pos = reference_account.get(key)
                        if pos:
                            self.positions_by_account[account_key][key] = pos
                            self.positions[key] = pos
                            restored += 1
                            logger.warning(f"[_check_for_deletions] 🩹 Restored {key}")
                    if restored > 0:
                        logger.info(f"[_check_for_deletions] ✅ Auto-healed {restored} positions. Processing continues.")
        except Exception as e:
            logger.error(f"[_check_for_deletions] Error during check: {e}")

    def _restore_position_from_backups_blocking(self, account_key: str, symbol: str, position_side: str) -> Optional[Position]:
        """Synchronous worker for restore_position_from_backups. Performs all disk I/O. MUST be invoked via asyncio.to_thread — never on the event loop directly. Event-loop blocking here caused process_account_update HUNG > 120s on 2026-04-29."""
        position_key = f"{account_key}:{symbol}_{position_side}"
        try:
            backup_dir_str = os.path.join(str(self.config.BASE_PATH), account_key, "backups")
            position_side_str = position_side.lower()
            patterns = [f"{position_side_str}_positions_backup_*.json", f"{position_side_str}_positions.json_backup_*.json"]
            if not os.path.exists(backup_dir_str):
                logger.warning(f"[RESTORE_BACKUP] No backup directory for {account_key}: {backup_dir_str}")
                return None
            backup_files = []
            for pat in patterns:
                backup_files.extend(glob.glob(os.path.join(backup_dir_str, pat)))
            backup_files.sort(reverse=True)
            if not backup_files:
                logger.warning(f"[RESTORE_BACKUP] No backup files found for {position_key}")
                return None
            for bf in backup_files:
                try:
                    with open(bf, "r") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "positions" in data:
                        data = data["positions"]
                    pos_data = data.get(position_key)
                    if not pos_data or not isinstance(pos_data, dict):
                        continue
                    backup_amt = abs(safe_fetch_float(pos_data.get("positionAmt", 0), 0))
                    pos_data["positionAmt"] = 0.0
                    restored = Position.from_dict(pos_data)
                    restored.positionAmt = 0.0
                    ep = safe_fetch_float(pos_data.get("entry_price", 0), 0)
                    logger.critical(f"[RESTORE_FROM_BACKUP] {position_key}: Recovered from {os.path.basename(bf)} — backup_amt={backup_amt} (SET TO 0) entry={ep} max_gain={getattr(restored, 'max_gain', 0)}")
                    return restored
                except Exception as bf_err:
                    logger.debug(f"[RESTORE_BACKUP] Failed to parse {os.path.basename(bf)} for {position_key}: {bf_err}")
                    continue
            also_check_main = os.path.join(str(self.config.BASE_PATH), account_key, f"{position_side_str}_positions.json")
            if os.path.exists(also_check_main):
                try:
                    with open(also_check_main, "r") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "positions" in data:
                        data = data["positions"]
                    pos_data = data.get(position_key)
                    if pos_data and isinstance(pos_data, dict):
                        pos_data["positionAmt"] = 0.0
                        restored = Position.from_dict(pos_data)
                        restored.positionAmt = 0.0
                        logger.critical(f"[RESTORE_FROM_MAIN_FILE] {position_key}: Recovered from main file (positionAmt SET TO 0)")
                        return restored
                except Exception:
                    pass
            logger.warning(f"[RESTORE_BACKUP] Not found in {account_key} backups/main — searching OTHER accounts for {symbol}_{position_side}")
            all_accounts = ["ang", "inf", "flz", "men", "fin"]
            for other_acct in all_accounts:
                if other_acct == account_key:
                    continue
                other_pk = f"{other_acct}:{symbol}_{position_side}"
                other_main = os.path.join(str(self.config.BASE_PATH), other_acct, f"{position_side_str}_positions.json")
                if os.path.exists(other_main):
                    try:
                        with open(other_main, "r") as f:
                            data = json.load(f)
                        if isinstance(data, dict) and "positions" in data:
                            data = data["positions"]
                        pos_data = data.get(other_pk)
                        if pos_data and isinstance(pos_data, dict):
                            pos_data["symbol"] = symbol
                            pos_data["position_side"] = position_side
                            pos_data["positionAmt"] = 0.0
                            restored = Position.from_dict(pos_data)
                            restored.positionAmt = 0.0
                            logger.critical(f"[RESTORE_FROM_OTHER_ACCOUNT] {position_key}: Copied structure from {other_acct} (amt zeroed, all other fields preserved)")
                            return restored
                    except Exception:
                        continue
                other_backup_dir = os.path.join(str(self.config.BASE_PATH), other_acct, "backups")
                if os.path.exists(other_backup_dir):
                    other_backups = []
                    for pat in patterns:
                        other_backups.extend(glob.glob(os.path.join(other_backup_dir, pat)))
                    other_backups.sort(reverse=True)
                    for obf in other_backups[:3]:
                        try:
                            with open(obf, "r") as f:
                                data = json.load(f)
                            if isinstance(data, dict) and "positions" in data:
                                data = data["positions"]
                            pos_data = data.get(other_pk)
                            if pos_data and isinstance(pos_data, dict):
                                pos_data["symbol"] = symbol
                                pos_data["position_side"] = position_side
                                pos_data["positionAmt"] = 0.0
                                restored = Position.from_dict(pos_data)
                                restored.positionAmt = 0.0
                                logger.critical(f"[RESTORE_FROM_OTHER_ACCOUNT_BACKUP] {position_key}: Copied from {other_acct} backup {os.path.basename(obf)}")
                                return restored
                        except Exception:
                            continue
            logger.critical(f"[RESTORE_BACKUP] ABSOLUTE FAILURE: {position_key} not found in ANY account, ANY backup, ANY file on the entire system")
            return None
        except Exception as e:
            logger.error(f"[RESTORE_BACKUP] Error searching backups for {position_key}: {e}")
            return None

    async def restore_position_from_backups(self, account_key: str, symbol: str, position_side: str) -> Optional[Position]:
        """Search ALL backup files (newest first) for this position. Any backup data is sacred — even zero-amt positions have history (entry_price, max_gain, etc). The most recent backup with this key wins. positionAmt is preserved from backup; caller overrides with API amt if needed.
        Disk I/O runs in a worker thread to avoid blocking the asyncio event loop (was source of process_account_update HUNG > 120s). Negative results are cached for `_restore_negative_cache_ttl` seconds so dead orphan symbols (delisted USDC perps, etc.) don't trigger ~50-file rescans on every WS tick.
        """
        position_key = f"{account_key}:{symbol}_{position_side}"
        cache_hit = self._restore_negative_cache.get(position_key)
        now_ts = time.time()
        if cache_hit and (now_ts - cache_hit) < self._restore_negative_cache_ttl:
            return None
        result = await asyncio.to_thread(self._restore_position_from_backups_blocking, account_key, symbol, position_side)
        if result is None:
            self._restore_negative_cache[position_key] = now_ts
            if len(self._restore_negative_cache) > 5000:
                cutoff = now_ts - self._restore_negative_cache_ttl
                self._restore_negative_cache = {k: v for k, v in self._restore_negative_cache.items() if v > cutoff}
        else:
            self._restore_negative_cache.pop(position_key, None)
        return result

    async def get_symbols_for_account(self, account_key: str) -> set:
        allowed_symbols = set()
        account_attr_map = { 'ang': ['symbols_ang_long', 'symbols_ang_short'], 'inf': ['symbols_inf_long', 'symbols_inf_short'], 'men': ['symbols_men'], 'flz': ['symbols_flz'], 'fin': ['symbols_fin'] }
        target_attrs = account_attr_map.get(account_key, [])
        for attr_name in target_attrs:
            symbol_data = getattr(self, attr_name, None)
            if asyncio.iscoroutine(symbol_data):
                try :
                    symbol_data = await symbol_data
                    setattr(self, attr_name, symbol_data)
                except Exception as e:
                    self.logger.error(f"[{account_key}] Failed to resolve async symbol attribute '{attr_name}': {e}")
                    symbol_data = set()
                    setattr(self, attr_name, symbol_data) 
            if symbol_data and hasattr(symbol_data, '__iter__'):
                allowed_symbols.update(symbol_data)
        if not allowed_symbols:
            try :
                path_attr_map = { 'ang': ['SYMBOLS_ANG_LONG', 'SYMBOLS_ANG_SHORT'], 'inf': ['SYMBOLS_INF_LONG', 'SYMBOLS_INF_SHORT'], 'flz': ['SYMBOLS_FLZ'], 'men': ['SYMBOLS_MEN'], 'fin': ['SYMBOLS_FIN'] }
                attrs = path_attr_map.get(account_key, [])
                for attr in attrs:
                    path = getattr(self.config, attr, None)
                    if path:
                        loaded = self._load_symbols_list(Path(self.config.BASE_PATH) / path)
                        if loaded:
                            allowed_symbols.update(loaded)
            except Exception as e:
                self.logger.error(f"[{account_key}] Emergency symbol load failed: {e}")
        return allowed_symbols

    async def _load_json_file_content(self, file_path: Path) -> Dict[str, Any]:
        if not file_path: return {}
        path_str = str(file_path)
        try :
            if not await aio_os.path.exists(path_str): return {} 
            async with LimitedAioOpen(path_str, "rb") as f:
                raw_content = await f.read()
            if not raw_content: return {}
            return safe_json_loads(raw_content)
        except Exception as e:
            logger.error(f"Error loading '{path_str}': {e}")
            return {}

    async def clean_all_position_files(self) -> dict:
        try :
            from utils import orjson_default, scan_and_fix_position_files
            results = await scan_and_fix_position_files(".")
            if results['errors']:
                for error in results['errors']:
                    logger.error(f"[clean_all_position_files] Error: {error}")
            return results
        except Exception as e:
            logger.error(f"[clean_all_position_files] Error during cleanup: {e}")
            return {"error": str(e)}

    def get_position_file(self, account_key: str, position_side_str: str) -> Path:
        base_path = Path(self.config.BASE_PATH)
        account_dir = base_path / account_key.lower()
        filename = f"{position_side_str.lower()}_positions.json"
        return account_dir / filename

    async def load_all_positions(self):
        global _positions_loaded_once_global
        if _positions_loaded_once_global or self._positions_loaded_once:
            logger.warning("[load_all_positions] ⚠️⚠️⚠️ ABSOLUTE BLOCK: Positions already loaded once - reload BLOCKED to protect fresh data!")
            return
        master_symbols_list = []
        try :
            async with LimitedAioOpen(self.config.SYMBOLS_FILE, 'r') as f:
                content = await f.read()
                data = json.loads(content)
                if isinstance(data, dict) and 'symbols' in data:
                    master_symbols_list = data['symbols']
                elif isinstance(data, list):
                    master_symbols_list = data
                else:
                    logger.error(f"Could not parse master symbol list from {self.config.SYMBOLS_FILE}. Format is unexpected.")
        except FileNotFoundError:
            logger.error(f"CRITICAL: Master symbol file not found at {self.config.SYMBOLS_FILE}. Cannot filter positions.")
            return 
        except Exception as e:
            logger.error(f"Error loading master symbol file: {e}")
            return
        master_allowed_symbols = set(master_symbols_list)
        if not master_allowed_symbols:
            logger.error("Master symbol list is empty. No positions will be loaded.")
            return
        if self._lock:
            async with self._lock:
                await self._load_positions_with_lock(master_allowed_symbols)
        else:
            await self._load_positions_with_lock(master_allowed_symbols)

    async def _load_positions_with_lock(self, master_allowed_symbols):
        total_loaded_count = 0
        total_ignored_count = 0
        for account_config in self.accounts.values():
            account_key = account_config.prefix
            current_account_positions_in_memory = self.positions_by_account.setdefault(account_key, {})
            for position_side_str in ["LONG", "SHORT"]:
                file_path = self.get_position_file(account_key, position_side_str)
                symbols_data_from_disk = await self._load_and_merge_backups(file_path, position_side_str)
                for raw_key, position_data_on_disk in symbols_data_from_disk.items():
                    if not isinstance(position_data_on_disk, dict):
                        continue
                    try :
                        disk_pos_obj_candidate = Position.from_dict(position_data_on_disk)
                        symbol = disk_pos_obj_candidate.symbol
                    except Exception as e:
                        total_ignored_count += 1
                        continue
                    if not symbol:
                        total_ignored_count += 1
                        continue
                    if symbol not in master_allowed_symbols:
                        logger.debug(f"[load _all_positions] Loading position for '{symbol}' (not in active symbols.json but preserving data)")
                    normalized_position_key = raw_key
                    if normalized_position_key in current_account_positions_in_memory:
                        logger.debug(f"[load _all_positions] Preserving existing memory position: {normalized_position_key}")
                    else:
                        candidate_ts = safe_datetime(position_data_on_disk.get("last_updated") or position_data_on_disk.get("updated_at") or position_data_on_disk.get("timestamp") or position_data_on_disk.get("opened_at"))
                        final_position = self._assign_position_if_newer(account_key, normalized_position_key, disk_pos_obj_candidate, candidate_ts)
                        if final_position is disk_pos_obj_candidate:
                            logger.debug(f"[load _all_positions] Added new position from disk: {normalized_position_key}")
                        else:
                            logger.debug(f"[load _all_positions] Preserving existing memory position: {normalized_position_key}")
                    total_loaded_count += 1
        logger.debug(f"[load _all_positions] Finished. Loaded {total_loaded_count} positions (including positions for symbols not in symbols.json to preserve data).")
        for account_key, account_positions in self.positions_by_account.items():
            for position_key, position in account_positions.items():
                self.positions[position_key] = position
        logger.debug(f"[load _all_positions] Synced {len(self.positions)} positions to positions dict")
        async with self._reference_snapshot_lock:
            for account_key, account_positions in self.positions_by_account.items():
                if account_key not in self._positions_by_account_reference_snapshot:
                    self._positions_by_account_reference_snapshot[account_key] = {}
                for pos_key, pos_obj in account_positions.items():
                    if pos_obj:
                        self._positions_by_account_reference_snapshot[account_key][pos_key] = pos_obj
                        self._positions_reference_snapshot[pos_key] = pos_obj
        fixed_count = self.validate_positions_floats()
        if fixed_count > 0:
            logger.debug(f"[load _all_positions] Fixed {fixed_count} positions with string numeric values during load")
        consistency_fixes = 0
        for pk, pos in self.positions.items():
            consistency_fixes += self._validate_position_consistency(pos, pk)
        if consistency_fixes > 0: logger.info(f"[load_all_positions] Fixed {consistency_fixes} position consistency issues during load")
        if total_ignored_count > 0:
            logger.debug(f"[load _all_positions] Saving all positions back to disk...")
        pass

    async def cleanup_temp_files(self, account_key: str = None) -> int:
        import os
        cleaned = 0
        now = time.time()
        target_dirs = [str(self.base_path), str(self.base_path / "data")]
        if account_key:
            target_dirs.append(str(self._account_path(account_key)))
        for dr in target_dirs:
            if not os.path.exists(dr): continue
            try :
                with os.scandir(dr) as it:
                    for entry in it:
                        if not entry.is_file(): continue
                        name = entry.name
                        is_trash = ( name.startswith(".") and (".tmp" in name or ".atom" in name) or name.endswith(".tmp") or name.endswith(".temp") or name.endswith(".bak") )
                        is_leaderboard = name.startswith("winners_") or name.startswith("losers_")
                        try :
                            mtime = entry.stat().st_mtime
                            age = now - mtime
                            if (is_leaderboard and age > 600) or (is_trash and age > 120):
                                os.remove(entry.path)
                                cleaned += 1
                        except Exception: pass
            except Exception as e:
                self.logger.error(f"Cleanup failed for {dr}: {e}")
        if account_key:
            for side in ["long", "short"]:
                b_path = self._account_path(account_key) / "backups"
                if b_path.exists():
                    await self.prune_old_backups(str(b_path), f"{side}_positions")
        return cleaned

    async def _load_and_merge_backups(self, main_file_path: Path, position_side_str: str) -> Dict[str, dict]:
        main_content = await self._load_json_file_content(main_file_path)
        if not main_content:
            main_content = {}
        expected_symbol_count = self._get_expected_symbol_count()
        main_symbols = set()
        for pos_data in main_content.values():
            if isinstance(pos_data, dict) and "symbol" in pos_data:
                main_symbols.add(pos_data["symbol"])
        if len(main_symbols) >= expected_symbol_count:
            return main_content
        backup_dir = main_file_path.parent / "backups"
        patterns = [ f"{position_side_str.lower()}_positions.json_backup_*.json", f"{position_side_str.lower()}_positions_backup_*.json", ]
        if await aio_os.path.isdir(backup_dir):
            backup_files_with_mtime: List[Tuple[float, Path]] = []
            try :
                for file_path in backup_dir.iterdir():
                    if not file_path.is_file():
                        continue
                    for pat in patterns:
                        if fnmatch.fnmatch(file_path.name, pat):
                            try :
                                mtime = file_path.stat().st_mtime
                                backup_files_with_mtime.append((mtime, file_path))
                            except OSError:
                                continue
                            break
            except Exception as e:
                logger.warning(f"Error scanning backup directory {backup_dir}: {e}")
            backup_files_with_mtime.sort(key=lambda x: x[0], reverse=True)
            for _idx, (_mtime, backup_file_path) in enumerate(backup_files_with_mtime[:3]):
                backup_content = await self._load_json_file_content(backup_file_path)
                if backup_content:
                    for pos_key, pos_data in backup_content.items():
                        if pos_key not in main_content:
                            main_content[pos_key] = pos_data
                            if isinstance(pos_data, dict) and "symbol" in pos_data:
                                main_symbols.add(pos_data["symbol"])
                    if len(main_symbols) >= expected_symbol_count:
                        break
        return main_content

    async def _rest_poll_loop(self, account_key: str) -> None:
        """Fetch positions every ~4 seconds (with jitter), process via process_account_update, broadcast, and save - runs independently for each account. WebSocket updates handle real-time updates."""
        base_interval = 4.0
        if config.VERBOSE_FETCH_LOGGING: logger.info(f"[_rest_poll_loop][{account_key}] Started polling loop (interval: {base_interval}s base - WebSocket handles real-time)")
        if config.VERBOSE_FETCH_LOGGING: logger.info(f"[_rest_poll_loop][{account_key}] 🚨 _account_monitor_stop={self._account_monitor_stop}")
        await asyncio.sleep(1.0)
        poll_interval = base_interval 
        consecutive_errors = 0
        last_successful_fetch = time.time()
        loop_count = 0
        while not self._account_monitor_stop:
            loop_count += 1
            internal_fetcher_flag = self.base_path / ".internal_fetcher_enabled"
            disabled_flag = self.base_path / ".internal_fetcher_disabled"
            if internal_fetcher_flag.exists() and not self._enable_auto_fetch:
                logger.critical(f"[_rest_poll_loop][{account_key}] 🚨 Watchdog signaled external fetcher failed - ENABLING INTERNAL FETCHER")
                self._enable_auto_fetch = True
            elif disabled_flag.exists() and self._enable_auto_fetch and loop_count > 1:
                logger.debug(f"[_rest_poll_loop][{account_key}] Watchdog signaled external fetcher working - disabling internal fetcher")
                self._enable_auto_fetch = False
            if config.VERBOSE_FETCH_LOGGING or loop_count % 5 == 0: logger.info(f"[_rest_poll_loop][{account_key}] 🔄 Loop iteration #{loop_count}, last successful fetch: {time.time() - last_successful_fetch:.1f}s ago, auto_fetch={self._enable_auto_fetch}")
            ban_rem = _ban_remaining_seconds()
            if ban_rem > 0:
                _log_ban_skip_throttled(logger, account_key, f"pausing REST poll for {ban_rem:.0f}s")
                await asyncio.sleep(min(ban_rem + 2, 60.0))
                continue
            time_since_last_fetch = time.time() - last_successful_fetch
            if time_since_last_fetch > 6.0 and loop_count > 2:
                logger.warning(f"[_rest_poll_loop][{account_key}] 🚨 Not updating for {time_since_last_fetch:.1f}s - fetching positions from API")
                try :
                    await self.fetch_positions(account_key)
                    logger.warning(f"[_rest_poll_loop][{account_key}] Fetched positions from API")
                    last_successful_fetch = time.time()
                except Exception as sync_err:
                    _record_ip_ban_from_exc(sync_err)
                    logger.error(f"[_rest_poll_loop][{account_key}] Fallback fetch failed: {sync_err}", exc_info=True)
            if not self._enable_auto_fetch:
                if loop_count % 20 == 0:
                    logger.warning(f"[_rest_poll_loop][{account_key}] ⚠️ Auto-fetch disabled, sleeping (loop {loop_count}) - positions may be stale!")
                await asyncio.sleep(poll_interval)
                continue
            try :
                result = await self.fetch_positions(account_key)
                if result and isinstance(result, list) and len(result) > 0:
                    logger.info(f"[_rest_poll_loop][{account_key}] ✅ Fetched {len(result)} positions from API") 
                    last_successful_fetch = time.time()
                    positions_in_memory = len(self.positions_by_account.get(account_key, {}))
                    if loop_count % 10 == 0:
                        logger.info(f"[_rest_poll_loop][{account_key}] Fetched {len(result)} positions, {positions_in_memory} in memory")
                    if positions_in_memory == 0:
                        logger.critical(f"[_rest_poll_loop][{account_key}] 🚨 CRITICAL: Fetched {len(result)} positions from API but 0 positions in memory! Positions were not saved!")
                elif result == {}:
                    time_since_success = time.time() - last_successful_fetch
                    if time_since_success > 12.0:
                        logger.critical(f"[_rest_poll_loop][{account_key}] 🚨🚨🚨 CRITICAL: No successful fetch in {int(time_since_success)}s - positions are STALE! Fetch was skipped/rate limited. Forcing immediate fetch...")
                        asyncio.create_task(self.fetch_positions(account_key))
                    elif time_since_success > 8.0:
                        logger.warning(f"[_rest_poll_loop][{account_key}] ⚠️ No successful fetch in {int(time_since_success)}s - positions may be stale (rate limited?)")
                else:
                    time_since_success = time.time() - last_successful_fetch
                    if time_since_success > 12.0:
                        logger.warning(f"[_rest_poll_loop][{account_key}] ⚠️ No successful fetch in {int(time_since_success)}s - positions may be stale")
                consecutive_errors = 0
            except asyncio.CancelledError:
                logger.debug(f"[_rest_poll_loop][{account_key}] Loop cancelled")
                break
            except Exception as exc:
                consecutive_errors += 1
                logger.error(f"[_rest_poll_loop][{account_key}] REST poll error (consecutive: {consecutive_errors}): {exc}", exc_info=True)
                if consecutive_errors > 10:
                    logger.error(f"[_rest_poll_loop][{account_key}] ⚠️ Too many consecutive errors ({consecutive_errors}), waiting 30s before retry")
                    await asyncio.sleep(30.0)
                    consecutive_errors = 0
            import random
            jitter = random.uniform(0, self._rest_poll_jitter)
            sleep_time = poll_interval + jitter
            if config.VERBOSE_FETCH_LOGGING: logger.info(f"[_rest_poll_loop][{account_key}] 🚨 About to sleep for {sleep_time:.1f}s (base: {poll_interval}s + jitter: {jitter:.1f}s), _account_monitor_stop={self._account_monitor_stop}")
            await asyncio.sleep(sleep_time)
            if config.VERBOSE_FETCH_LOGGING: logger.info(f"[_rest_poll_loop][{account_key}] 🚨 Woke up from sleep, _account_monitor_stop={self._account_monitor_stop}")
        logger.warning(f"[_rest_poll_loop][{account_key}] ⚠️ Loop exiting - _account_monitor_stop={self._account_monitor_stop}")

    async def fetch_positions(self, account_key: str):
        if is_sandbox_account(config, account_key): return {}
        if hasattr(self, '_allowed_accounts') and self._allowed_accounts:
            if account_key not in self._allowed_accounts:
                logger.warning(f"[fetch_positions] BLOCKED: Account '{account_key}' not in allowed accounts {self._allowed_accounts}")
                return {}
        now = time.time()
        if account_key not in self._accounts_loaded_once:
            acc_pos = self.positions_by_account.get(account_key, {})
            if len(acc_pos) > 10: 
                logger.warning(f"[fetch_positions] ⚠️ {account_key} flag missing but {len(acc_pos)} positions found. Setting flag and proceeding.")
                self._accounts_loaded_once.add(account_key)
            else:
                if not self._positions_refreshing.get(account_key, False):
                    logger.warning(f"Skipping API fetch for {account_key}; first load not complete. Triggering background load.")
                    asyncio.create_task(self.load_account(account_key))
                return {}
        if self._positions_refreshing.get(account_key, False): 
            return {}
        min_interval = getattr(config, "POSITION_REFRESH_MIN_INTERVAL", 3.0)
        last = self._last_positions_fetch.get(account_key, 0.0)
        if last <= 0.0 or last > now:
            last = now - min_interval - 1.0
            self._last_positions_fetch[account_key] = last
        time_since_last = now - last
        account = self.accounts.get(account_key)
        if not account:
            return {}
        last_ws = self._ws_update_timestamps.get(account_key, 0) if hasattr(self, '_ws_update_timestamps') else 0
        ws_connected_ts = self._ws_update_timestamps.get(f"{account_key}_connected", 0) if hasattr(self, '_ws_update_timestamps') else 0
        ws_last_any = max(last_ws, ws_connected_ts)
        ws_age = now - ws_last_any if ws_last_any > 0 else 9999
        ws_alive = ws_age < 90.0
        force_fetch = not ws_alive and time_since_last > 3.0
        if not force_fetch and time_since_last < min_interval:
            if time_since_last > 10.0:
                logger.warning(f"[fetch_positions][{account_key}] ⚠️ SKIPPING - rate limited but stale (last: {time_since_last:.2f}s ago, min: {min_interval}s) - positions may be outdated!")
            else:
                if time_since_last > 5.0:
                    logger.info(f"[fetch_positions][{account_key}] ⏸️ SKIPPING - rate limited (last: {time_since_last:.2f}s ago, min: {min_interval}s)")
            return {}
        if not force_fetch and account and hasattr(account, 'rate_limit_until') and account.rate_limit_until and time.time() < account.rate_limit_until: 
            logger.debug(f"[fetch_positions][{account_key}] 🚨 SKIPPING - account rate limited (last: {time_since_last:.1f}s ago, rate_limit_until: {account.rate_limit_until})")
            return {}
        if force_fetch: 
            logger.warning(f"[fetch_positions][{account_key}] ⚠️ Forcing fetch - last fetch was {time_since_last:.1f}s ago, ws_alive={ws_alive}")
        self._positions_refreshing[account_key] = True
        if not force_fetch and self._is_circuit_open(account_key): 
            logger.debug(f"[fetch_positions][{account_key}] 🚨 SKIPPING - circuit breaker open (last: {time_since_last:.1f}s ago)")
            self._positions_refreshing[account_key] = False
            return {}
        result = {}
        try :
            retries = 3
            positions_data = None
            for attempt in range(retries):
                try :
                    if account_key not in self.accounts:
                        return {}
                    account = self.accounts.get(account_key)
                    if not getattr(account, "client", None):
                        try :
                            client = await self._ensure_account_client(account_key)
                            if not client:
                                logger.error(f"[{account_key}] Failed to create client (api_key present: {bool(getattr(account, 'api_key', None))}, api_secret present: {bool(getattr(account, 'api_secret', None))})")
                                return {}
                        except Exception as client_err:
                            logger.error(f"[{account_key}] Exception creating client: {client_err}", exc_info=True)
                            return {}
                        account = self.accounts.get(account_key)
                    client_obj = getattr(account, "client", None) if account else None
                    if not client_obj:
                        logger.error(f"[{account_key}] Client still missing after ensure_account_client; cannot fetch positions")
                        return {}
                    try :
                        async with self.maker_semaphore:
                            if account and hasattr(account, 'safe_api_call'):
                                positions_data = await account.safe_api_call(client_obj.futures_position_information)
                            else:
                                positions_data = await asyncio.wait_for(asyncio.to_thread(client_obj.futures_position_information), timeout=30.0)
                    except asyncio.TimeoutError:
                        logger.warning(f"Timeout while fetching positions for '{account_key}' (attempt {attempt + 1}/{retries})")
                        raise TimeoutError(f"API call timed out after 30 seconds")
                    self._record_success(account_key)
                    if hasattr(self, '_recent_api_failures'): self._recent_api_failures[account_key] = 0
                    break 
                except TypeError as e:
                    if "asyncio.Future" in str(e) or "coroutine" in str(e) or "awaitable" in str(e): break 
                except asyncio.CancelledError:
                    if attempt < retries - 1: continue
                    else:
                        self._record_failure(account_key)
                        self._track_api_failure(account_key)
                        return {}
                except asyncio.TimeoutError:
                    if attempt < retries - 1:
                        await asyncio.sleep(min(2 ** attempt, 10))
                    else:
                        self._record_failure(account_key)
                        self._track_api_failure(account_key)
                        return {}
                except BinanceAPIException as e:
                    if e.code == -2014: logger.error(f"[{account_key}] API Key Format Invalid (-2014)")
                    account = self.accounts.get(account_key)
                    if e.code == -1021:
                        logger.warning(f"[fetch_positions][{account_key}] Timestamp error (code -1021), re-syncing time and retrying (attempt {attempt + 1}/{retries})")
                        if account and hasattr(account, 'sync_binance_time'):
                            account.sync_binance_time()
                        await asyncio.sleep(0.5)
                        if attempt < retries - 1: continue
                        else: return {}
                    elif e.status_code == 429 or getattr(e, "code", None) == -1003:
                        _record_ip_ban_from_exc(e)
                        if account and hasattr(account, "handle_api_ban"): account.handle_api_ban(cooldown_seconds=900)
                        if attempt < retries - 1: continue
                        else: return {}
                    elif e.status_code in [500, 502, 503, 504]:
                        if attempt < retries - 1: await asyncio.sleep(min(2 ** attempt, 15))
                        else: return {}
                    else:
                        self._record_failure(account_key)
                        self._track_api_failure(account_key)
                        return {}
                except (ConnectionError, OSError):
                    if attempt < retries - 1: await asyncio.sleep(min(2 ** attempt, 15))
                    else:
                        self._record_failure(account_key)
                        self._track_api_failure(account_key)
                        return {}
                except Exception as e:
                    logger.error(f"[fetch_positions][{account_key}] Unexpected error (attempt {attempt + 1}/{retries}): {e}", exc_info=True)
                    if attempt < retries - 1: await asyncio.sleep(min(2 ** attempt, 10))
                    else:
                        self._record_failure(account_key)
                        self._track_api_failure(account_key)
                        return {}
            if positions_data is None: 
                logger.error(f"[fetch_positions][{account_key}] API returned None - no positions data")
                self._last_positions_fetch[account_key] = time.time()
                self._positions_refreshing[account_key] = False
                return {}
            if not isinstance(positions_data, list): 
                logger.warning(f"[fetch_positions][{account_key}] API returned non-list data: {type(positions_data)}")
                self._last_positions_fetch[account_key] = time.time()
                self._positions_refreshing[account_key] = False
                return {}
            if config.VERBOSE_FETCH_LOGGING: logger.info(f"[fetch_positions][{account_key}] 🚨 Fetched {len(positions_data) if isinstance(positions_data, list) else 'non-list'} positions from API - CALLING process_account_update NOW")
            logger.info(f"[FETCHFETCH]ez_pos_serv[{account_key}] 🚨 Fetched {positions_data}")
            try :
                # CRASH-ON-HANG: process_account_update is the CORE — handle_ functions feed
                # the entire system. If it stalls > 60s the worker is dead in all but name;
                # exit so the watchdog respawns within 5s instead of trading on stale data.
                try:
                    # 2026-04-29 user: bumped 60→120s. Same fix as ez_positions_realtime.
                    _pau_timeout = float(getattr(config, 'PAU_TIMEOUT_SEC', 120.0))
                    updated_keys = await asyncio.wait_for(self.process_account_update(account_key, positions_data, single=False, skip_broadcast_save=False), timeout=_pau_timeout)
                except asyncio.TimeoutError:
                    logger.critical(f"[fetch_positions][{account_key}] 🚨🚨🚨 process_account_update HUNG > {_pau_timeout}s — CRASHING worker so watchdog respawns. Stale positions WILL trade wrong if we continue.")
                    try: sys.stdout.flush(); sys.stderr.flush()
                    except Exception: pass
                    os._exit(42)
                if config.VERBOSE_FETCH_LOGGING: logger.info(f"[fetch_positions][{account_key}] 🚨 process_account_update RETURNED: {len(updated_keys)} updated keys")
                if config.VERBOSE_FETCH_LOGGING: logger.info(f'[fetch_positions][{account_key}] process_account_update processed and saved {len(updated_keys)} updated position keys, {len(self.positions_by_account.get(account_key, {}))} total positions in memory')
                if not updated_keys and len(positions_data) > 0:
                    logger.warning(f"[fetch_positions][{account_key}] process_account_update returned no updated keys despite {len(positions_data)} positions from API - this may indicate a bug")
                self.positions_last_sync = datetime.now(timezone.utc)
                asyncio.create_task(self.monitor_reductions_priority(account_key))
                if config.VERBOSE_FETCH_LOGGING: logger.debug(f"[fetch_positions][{account_key}] Processing complete, positions_dirty={self._positions_dirty}, updated_keys={len(updated_keys)}")
                result = positions_data
            except Exception as e:
                logger.critical(f"[fetch_positions][{account_key}] 🚨🚨🚨 process_account_update RAISED — CRASHING worker so watchdog respawns. Error: {e}", exc_info=True)
                try: sys.stdout.flush(); sys.stderr.flush()
                except Exception: pass
                os._exit(43)
        except Exception as e:
            logger.error(f"[fetch_positions][{account_key}] Unexpected error in fetch_positions: {e}", exc_info=True)
            result = {}
        finally:
            self._last_positions_fetch[account_key] = time.time()
            self._positions_refreshing[account_key] = False
        return result

    async def process_account_update(self, account_key: str, data: Optional[dict] = None, single: bool = False, skip_broadcast_save: bool = False) -> Set[str]:
        """Update position dicts from API/WebSocket data. Returns set of updated position keys. If skip_broadcast_save = True, caller handles broadcasting/saving."""
        if account_key == "tra":
            logger.debug(f"[process_account_update][{account_key}] ⏭️ Skipping 'tra' account - completely handled by tradier_positions.py")
            return set()
        if not self._loading_complete_event.is_set():
            logger.warning(f"[process_account_update][{account_key}] ⏳ Waiting for positions to load from files before processing API update...")
            try :
                await asyncio.wait_for(self._loading_complete_event.wait(), timeout=30.0)
                logger.debug(f"[process_account_update][{account_key}] Loading complete, proceeding with API update")
            except asyncio.TimeoutError:
                logger.error(f"[process_account_update][{account_key}] ⚠️ Timeout waiting for loading to complete, proceeding anyway")
        if config.VERBOSE_FETCH_LOGGING: logger.info(f'[fetch_positions][{account_key}] process_account_update called with {len(data) if isinstance(data, list) else "non-list"} positions')
        raw_positions_data = self.extract_positions_data(data)
        if raw_positions_data is None:
            raw_positions_data = []
        if config.VERBOSE_FETCH_LOGGING: logger.debug(f"[process_account_update][{account_key}] Processing {len(raw_positions_data)} positions, data type: {type(data)}")
        now = datetime.now(timezone.utc)
        updated_keys_in_api: Set[str] = set()
        account_positions = self.positions_by_account.get(account_key)
        expected_count = self._get_expected_symbol_count() * 2 
        if account_positions is None or len(account_positions) == 0:
            logger.critical(f"[process_account_update][{account_key}] 🚨 No positions in memory - Loading ONCE from files (positions dict IS ALREADY EMPTY)...")
            try :
                account_positions = self.positions_by_account.setdefault(account_key, {})
            except Exception as e:
                logger.error(f"[process_account_update][{account_key}] Error loading: {e}", exc_info=True)
                account_positions = self.positions_by_account.setdefault(account_key, {})
                if not account_positions or len(account_positions) == 0:
                    logger.critical(f"[process_account_update][{account_key}] 🚨🚨🚨 CRITICAL: account_positions is EMPTY after error - positions should NEVER be empty!")
                    return set()
        elif len(account_positions) < expected_count:
            logger.warning(f"[process_account_update][{account_key}] ⚠️ Only {len(account_positions)} positions in memory (expected {expected_count}) - positions may be missing!")
        positions_before = len(account_positions)
        positions_before_keys = set(account_positions.keys())
        if positions_before == 0:
            logger.critical(f"[process_account_update][{account_key}] 🚨 CRITICAL: account_positions is EMPTY when it should have positions! This indicates positions were CLEARED or never loaded. API reports {len(raw_positions_data)} positions.")
        if config.VERBOSE_FETCH_LOGGING: logger.debug(f"[process_account_update][{account_key}] BEFORE: {positions_before} positions in memory: {list(positions_before_keys)[:5]}")
        await self._check_for_deletions(account_key, f"before _process_account_update_impl")
        await self._process_account_update_impl(account_key, raw_positions_data, now, updated_keys_in_api, account_positions)
        await self._check_for_deletions(account_key, f"after _process_account_update_impl")
        positions_after_keys = set(account_positions.keys())
        positions_lost = positions_before_keys - positions_after_keys
        if positions_lost:
            logger.critical(f"[process_account_update][{account_key}] 🚨 POSITIONS LOST: {len(positions_lost)} positions disappeared during processing: {list(positions_lost)[:5]}")
        self._mark_positions_dirty()
        self.positions_live = True
        self.positions_source = "live"
        self.positions_last_sync = now
        positions_after = len(self.positions_by_account.get(account_key, {}))
        if config.VERBOSE_FETCH_LOGGING: logger.info(f"[process_account_update][{account_key}] 🚨 SAVE CHECK - skip_broadcast_save={skip_broadcast_save}, updated_keys={len(updated_keys_in_api)}, positions_after={positions_after}")
        if not skip_broadcast_save:
            if positions_after == 0:
                logger.critical(f"[process_account_update][{account_key}] 🚨 CRITICAL: Cannot broadcast/save - positions_after is 0! This should never happen after processing.")
            else:
                all_position_keys = set(self.positions_by_account.get(account_key, {}).keys())
                try :
                    if config.VERBOSE_FETCH_LOGGING: logger.info(f"[process_account_update][{account_key}] 📡 Broadcasting ALL {len(all_position_keys)} positions to Redis")
                    await self._broadcast_positions_to_redis(account_key, all_position_keys)
                except Exception as broadcast_err:
                    logger.error(f"[process_account_update][{account_key}] Broadcast failed: {broadcast_err}", exc_info=True)
                try :
                    if config.VERBOSE_FETCH_LOGGING: logger.info(f"[process_account_update][{account_key}] 💾 Saving ALL {positions_after} positions to file")
                    saved_file_long = self.get_position_file(account_key, "LONG")
                    saved_file_short = self.get_position_file(account_key, "SHORT")
                    long_exists = saved_file_long.exists()
                    short_exists = saved_file_short.exists()
                    if long_exists and short_exists:
                        logger.debug(f"[process_account_update][{account_key}] Saved ALL positions to file - LONG: {saved_file_long.name} ({saved_file_long.stat().st_size} bytes), SHORT: {saved_file_short.name} ({saved_file_short.stat().st_size} bytes)")
                    else:
                        logger.error(f"[process_account_update][{account_key}] CRITICAL: Save reported success but files missing! LONG exists: {long_exists}, SHORT exists: {short_exists}")
                except Exception as save_err:
                    logger.error(f"[process_account_update][{account_key}] CRITICAL: Save failed: {save_err}", exc_info=True)
                    raise
        else:
            logger.debug(f"[process_account_update][{account_key}] ⚠️ SKIPPING save because skip_broadcast_save=True")
        try :
            self._last_api_seen[account_key] = set(updated_keys_in_api)
        except Exception:
            pass
        return updated_keys_in_api

    async def _process_account_update_impl(self, account_key: str, raw_positions_data: list, now: datetime, updated_keys_in_api: set, account_positions: dict):
        try :
            if not account_positions:
                account_positions = self.positions_by_account.setdefault(account_key, {})
            logger.warning(f"[_process_account_update_impl][{account_key}] 🔄 Processing {len(raw_positions_data)} positions from API")
            processed_count = 0
            skipped_count = 0
            for pos_api_data in raw_positions_data:
                symbol = pos_api_data.get("s") or pos_api_data.get("symbol")
                if not symbol:
                    logger.warning(f"[_process_account_update_impl][{account_key}] ⚠️ Skipping position payload without symbol: {pos_api_data}")
                    skipped_count += 1
                    continue
                symbol = symbol.strip().upper() 
                raw_amt = safe_fetch_float(pos_api_data.get("pa") or pos_api_data.get("positionAmt"))
                if raw_amt is None:
                    logger.warning(f"[_process_account_update_impl][{account_key}] ⚠️ Invalid position amount for {symbol}: {pos_api_data.get('pa') or pos_api_data.get('positionAmt')}")
                    skipped_count += 1
                    continue
                amt_abs = abs(raw_amt)
                position_side = (pos_api_data.get("ps") or pos_api_data.get("positionSide") or ("LONG" if raw_amt >= 0 else "SHORT")).upper()
                unrealized_pnl_USD = safe_fetch_float(pos_api_data.get("up") or pos_api_data.get("unrealizedProfit")) if ("up" in pos_api_data or "unrealizedProfit" in pos_api_data) else None
                api_entry_price = safe_fetch_float(pos_api_data.get("ep") or pos_api_data.get("entryPrice"))
                position_key = construct_position_key(account_key, symbol, position_side)
                expected_prefix = f"{account_key}:"
                if not isinstance(position_key, str) or not position_key.startswith(expected_prefix):
                    logger.error(f"[_process_account_update_impl][{account_key}] 🚨 REJECTED position with wrong account prefix: {position_key} (expected {expected_prefix}*)")
                    skipped_count += 1
                    continue
                logger.debug(f"[_process_account_update_impl][{account_key}] 🔍 Processing API position: {position_key} amt={amt_abs:.6f} symbol={symbol} position_side={position_side}")
                existing_position = account_positions.get(position_key)
                if not existing_position:
                    logger.warning(f"[_process_account_update_impl][{account_key}] ⚠️ Position {position_key} not in memory - attempting to restore from backup")
                    existing_position = await self.restore_position_from_backups(account_key, symbol, position_side)
                    if not existing_position:
                        # 2026-04-24: UNIVERSAL API TRACKING — NEVER skip an API-returned position.
                        # Previous behaviour dropped orphan positions silently, which caused the 60×
                        # NMR hedge double-open cascade: ez_positions_service saw NMR_LONG=32 on API
                        # but refused to track it → existing_hedge check returned 0 → every 5-min
                        # hedge retry opened another webhook. Fix: create a minimal Position object
                        # from the API payload. tradeable_keys filtering happens downstream, NOT here.
                        if amt_abs > 0:
                            try:
                                _mk = safe_fetch_float(pos_api_data.get("mp"), 0) or safe_fetch_float(pos_api_data.get("markPrice"), 0) or api_entry_price
                                _seed = {
                                    "symbol": symbol,
                                    "position_side": position_side,
                                    "entry_price": api_entry_price or _mk,
                                    "mark_price": _mk,
                                    "positionAmt": amt_abs,
                                    "initial_quantity": amt_abs,
                                    "max_quantity": amt_abs,
                                    "max_positionSize": amt_abs * (_mk or 0),
                                    "opened_at": now,
                                    "last_updated": now,
                                    "augment_reason": "API_ORPHAN_ADOPTED",
                                }
                                existing_position = Position.from_dict(_seed)
                                account_positions[position_key] = existing_position
                                self.positions[position_key] = existing_position
                                logger.critical(f"🧬 [ORPHAN_EXCHANGE_POSITION_ADOPTED][{account_key}] {position_key}: created tracker from API (amt={amt_abs:.6f}, ep={api_entry_price}, mk={_mk}). Will be managed from next cycle.")
                            except Exception as _adopt_e:
                                logger.critical(f"🚨 [ORPHAN_EXCHANGE_POSITION][ADOPT_FAILED][{account_key}] {position_key}: could not create Position from API data ({_adopt_e}). Skipping this cycle; retry next poll.")
                                skipped_count += 1
                                continue
                        else:
                            # zero-amt orphan = nothing to adopt, safe to skip
                            skipped_count += 1
                            continue
                    else:
                        account_positions[position_key] = existing_position
                        self.positions[position_key] = existing_position
                last_update_time = self._position_update_timestamps.get(position_key)
                if last_update_time and amt_abs > 0:
                    time_since_last_update = (now - last_update_time).total_seconds()
                    if time_since_last_update < 0.2:
                        logger.debug(f"[_process_account_update_impl][{account_key}] ⏭️ Skipping {position_key} processing - WS updated {time_since_last_update:.3f}s ago (but will still broadcast)")
                        continue
                processed_count += 1
                # FIX: If our stored entry_price is not credible, replace with Binance API value
                if api_entry_price and api_entry_price > 0 and amt_abs > 0:
                    _mem_ep = safe_fetch_float(getattr(existing_position, 'entry_price', 0), 0)
                    _ep_credible = _mem_ep > 0 and api_entry_price > 0 and _mem_ep >= api_entry_price * 0.01 and _mem_ep <= api_entry_price * 100
                    if not _ep_credible:
                        logger.warning(f"[ENTRY_PRICE_FIX][{position_key}] Memory entry_price={_mem_ep:.8f} not credible vs API={api_entry_price:.8f}. Replacing with API value.")
                        existing_position.entry_price = api_entry_price
                prev_amt = abs(existing_position.positionAmt) if existing_position.positionAmt else 0.0
                # 2026-04-24: DISTINGUISH FRESH vs STALE price sources. Previously any price
                # source could overwrite mark_price — including quick_price() returning the cached
                # stale value from this same Position. That created a self-reinforcing staleness
                # loop where mark_price_last_updated kept advancing but value never changed.
                # Fix: track whether the source is genuinely fresh (from API payload or Redis
                # mark_price:SYMBOL feed) vs a self-referencing cache. Only update timestamp
                # when the source is fresh.
                _api_mp = safe_fetch_float(pos_api_data.get("mp"), 0)
                _api_markprice = safe_fetch_float(pos_api_data.get("markPrice"), 0)
                _fresh_price = _api_mp if _api_mp > 0 else (_api_markprice if _api_markprice > 0 else 0)
                price_from_getter = None
                _price_is_fresh = False
                if _fresh_price > 0:
                    price_from_getter = _fresh_price
                    _price_is_fresh = True
                else:
                    # fallback chain — cached or existing (NOT fresh)
                    try:
                        _qp = await quick_price(symbol)
                        if _qp and _qp > 0:
                            price_from_getter = _qp
                    except Exception: pass
                    if price_from_getter is None:
                        _ex_mp = safe_fetch_float(existing_position.mark_price, 0)
                        if _ex_mp > 0: price_from_getter = _ex_mp
                mt_price = safe_fetch_float(pos_api_data.get("mt"))
                current_price = price_from_getter or mt_price or safe_fetch_float(existing_position.mark_price, 0.0)
                tolerance = max(0.000001, prev_amt * 0.001)
                amount_diff = abs(amt_abs - prev_amt)
                if unrealized_pnl_USD is not None:
                    existing_position.unrealized_pnl_USD = unrealized_pnl_USD
                if current_price and current_price > 0:
                    _prev_mp = existing_position.mark_price or 0
                    existing_position.mark_price = current_price
                    # Only advance timestamp when the source is genuinely fresh OR value changed.
                    # Prevents self-reinforcing stale loop where cached price keeps refreshing its
                    # own timestamp, blocking ez_mark_prices / quick_price fall-through to a real
                    # fresh Redis value.
                    if _price_is_fresh or abs(current_price - _prev_mp) / max(_prev_mp, 1e-12) > 1e-6:
                        existing_position.mark_price_last_updated = now
                elif (not existing_position.mark_price or existing_position.mark_price <= 0) and current_price and current_price > 0:
                    existing_position.mark_price = current_price
                    existing_position.mark_price_last_updated = now
                trade_just_executed = False
                last_trade_ts = self._position_update_timestamps.get(f"_trade_exec_{position_key}", 0)
                if last_trade_ts and isinstance(last_trade_ts, (int, float)) and (time.time() - last_trade_ts) < 120:
                    trade_just_executed = True
                if prev_amt > 0 and amt_abs != prev_amt:
                    logger.warning(f"[API_AMT_CHANGE][{position_key}] prev={prev_amt} api={amt_abs} diff={amount_diff:.6f} tol={tolerance:.6f} trade_just_exec={trade_just_executed}")
                if amount_diff < tolerance:
                    await self.handle_unchanged_position(existing_position, position_key, amt_abs, current_price, price_is_fresh=_price_is_fresh)
                elif amt_abs > prev_amt:
                    logger.critical(f"[POSAMT_WRITE][API_AUG][{position_key}] {prev_amt} -> {amt_abs} (+{amt_abs-prev_amt}) via API")
                    await self.handle_augmentation(existing_position, position_key, prev_amt, amt_abs, amt_abs - prev_amt, current_price, existing_position.entry_price or current_price, price_is_fresh=_price_is_fresh)
                elif amt_abs < prev_amt:
                    if trade_just_executed:
                        logger.warning(f"[API_SYNC_BLOCKED][{position_key}] API says amt={amt_abs} < prev={prev_amt} but a trade was JUST executed (120s cooldown). Keeping local amt. API data is stale.")
                    elif amt_abs == 0 and prev_amt > 0:
                        current_count = self._log_zero_report(position_key, now, source="API_ZERO")
                        is_confirmed = self._is_zero_confirmed(position_key, threshold=config.ZERO_CONFIRMATION_THRESHOLD_API)
                        if is_confirmed:
                            logger.warning(f"[API_ZERO_CONFIRMED][{position_key}] API reports amt=0 confirmed after {current_count} cycles (prev={prev_amt}). Processing reduction.")
                            await self.handle_reduction(existing_position, position_key, prev_amt, 0.0, prev_amt, current_price, existing_position.entry_price or current_price, reduction_source="api_zero_confirmed")
                            self._clear_zero_report(position_key)
                        else:
                            logger.warning(f"[API_ZERO_REPORT][{position_key}] API reports amt=0 but NOT confirmed yet ({current_count}/{config.ZERO_CONFIRMATION_THRESHOLD_API}). Keeping prev_amt={prev_amt}. REFUSING to zero.")
                    else:
                        await self.handle_reduction(existing_position, position_key, prev_amt, amt_abs, prev_amt - amt_abs, current_price, existing_position.entry_price or current_price, reduction_source="api_sync")
                else:
                    await self.handle_unchanged_position(existing_position, position_key, amt_abs, current_price, price_is_fresh=_price_is_fresh)
                existing_position.last_updated = now
                account_positions[position_key] = existing_position
                self.positions[position_key] = existing_position
                self._position_update_timestamps[position_key] = now
                updated_keys_in_api.add(position_key)
            logger.warning(f"[_process_account_update_impl][{account_key}] Processed {processed_count} positions, skipped {skipped_count}, total updated_keys: {len(updated_keys_in_api)}")
            try:
                keys_in_memory_not_in_api = set(account_positions.keys()) - updated_keys_in_api
                
                for pk_ghost in keys_in_memory_not_in_api:
                    position_obj = account_positions.get(pk_ghost)
                    if not position_obj:
                        continue

                    prev_amt = abs(position_obj.positionAmt) if position_obj.positionAmt else 0.0
                    
                    # If position has an amount but is missing from API, apply the "Two-Strike" rule
                    if prev_amt > 0:
                        current_count = self._log_zero_report(pk_ghost, now, source="API_ABSENCE")
                        is_confirmed = self._is_zero_confirmed(pk_ghost, threshold=config.ZERO_CONFIRMATION_THRESHOLD_API)
                        
                        if is_confirmed:
                            logger.critical(f"[API_CONFIRMED_CLOSED][{pk_ghost}] 🚨 Position missing from API for {current_count} cycles. ZEROING OUT.")

                            # Resolve a final price for the record
                            final_price = position_obj.mark_price or position_obj.entry_price or 1.0
                            try:
                                qp = await quick_price(position_obj.symbol)
                                if qp: final_price = qp
                            except: pass

                            # 2026-05-08: per-phantom 30s timeout. If one zero-out stalls
                            # (e.g. on a slow disk write or downstream redis op), bail on
                            # this phantom and continue with the next — do NOT let one
                            # bad ghost trip the 120s outer PAU watchdog. The next PAU
                            # cycle will see the same absence and retry.
                            try:
                                await asyncio.wait_for(
                                    self.handle_reduction(
                                        position_obj, pk_ghost, prev_amt, 0.0, prev_amt,
                                        final_price, position_obj.entry_price,
                                        reduction_source="api_absence_confirmed"),
                                    timeout=30.0)
                                position_obj.positionAmt = 0.0
                                position_obj.last_updated = now
                                updated_keys_in_api.add(pk_ghost)
                                self._clear_zero_report(pk_ghost)
                            except asyncio.TimeoutError:
                                logger.critical(f"[GHOST_RECONCILE_TIMEOUT][{pk_ghost}] handle_reduction stalled >30s — skipping this cycle, will retry. Last positionAmt={prev_amt} kept on disk.")
                                continue
                        else:
                            logger.warning(f"[API_ABSENCE_PENDING][{pk_ghost}] Missing from API (Strike {current_count}/{config.ZERO_CONFIRMATION_THRESHOLD_API}). Waiting for confirmation.")
                    
                    # Always keep the timestamp moving so the system knows the tracker is alive
                    if hasattr(position_obj, 'last_updated'):
                        position_obj.last_updated = now

                logger.info(f"[_process_account_update_impl][{account_key}] Ghost reconciliation complete.")

            except Exception as ghost_err:
                logger.error(f"[{account_key}] Ghost position reconciliation failed: {ghost_err}", exc_info=True)


        except Exception as err:
            logger.error(f"[{account_key}] Error during process_account_update loop: {err}", exc_info=True)
            return

    def extract_positions_data(self, data):
        if data is None:
            return []
        if isinstance(data, list):
            logger.debug(f"[extract_positions_data] Data is list with {len(data)} items")
            return data
        if isinstance(data, dict):
            if "a" in data and "P" in data["a"]:
                logger.debug(f"[extract_positions_data] Data is dict with 'a.P' containing {len(data['a']['P']) if isinstance(data['a'].get('P'), list) else 'non-list'} items")
                result = data["a"]["P"]
                return result if isinstance(result, list) else []
            if "positions" in data:
                result = data["positions"]
                return result if isinstance(result, list) else []
        logger.warning(f"[extract_positions_data] Unexpected data format: type={type(data)}, keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}")
        return []

    async def handle_missing_price(self, symbol, max_failures):
        self.failed_price_counts[symbol] = self.failed_price_counts.get(symbol, 0) + 1
        if self.failed_price_counts[symbol] >= max_failures:
            logger.error(f"Exceeded max failures for {symbol}. Closing position.")

    def deteriorate_reentry_amounts(self, position: Position, progress: float, current_price: float):
        if not current_price or current_price <= 0:
            return
        try :
            position_key = None
            for account_key, account_positions in self.positions_by_account.items():
                for pk, pos in account_positions.items():
                    if pos == position:
                        position_key = pk
                        break
                if position_key:
                    break
            if not position_key or position_key not in self.reentry_data:
                return
            reentry_data = self.reentry_data[position_key]
            original_amount = reentry_data.get("reentry_amount", 0.0)
            if original_amount <= 0:
                return
            target_amount = 2 * config.START_POSITION_SIZE / current_price
            if original_amount <= target_amount:
                return
            deteriorated_amount = original_amount - (original_amount - target_amount) * progress
            deteriorated_amount = max(deteriorated_amount, target_amount)
            reentry_data["reentry_amount"] = deteriorated_amount
            reentry_data["timestamp"] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            try :
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.save_reentry_data(position_key))
                else:
                    asyncio.run(self.save_reentry_data(position_key))
            except Exception as save_error:
                logger.warning(f"[DETERIORAT E_REENTRY] Could not save reentry level for {position_key}: {save_error}")
            logger.debug(f"[DETERIORAT E_REENTRY][{position.symbol}] reentry_amount: {original_amount:.6f} -> {deteriorated_amount:.6f} " f"(progress: {progress:.3f})")
        except Exception as e:
            logger.error(f"[DETERIORAT E_REENTRY] Error deteriorating reentry amounts for {position.symbol}: {e}")

    async def update_pnl_deterioration_periodically(self):
        first_run = True
        while True:
            try :
                if first_run:
                    await asyncio.sleep(600) 
                    first_run = False
                else:
                    await asyncio.sleep(3600) 
                now = datetime.now(timezone.utc)
                updated_count = 0
                for account_key, account_positions in self.positions_by_account.items():
                    for position_key, position in account_positions.items():
                        if position and position.positionAmt == 0.0:
                            updated = False
                            if position.realized_pnl != 0.0:
                                original_pnl = position.realized_pnl
                                deteriorated_pnl = self.deteriorate_realized_pnl(account_key, position)
                                if original_pnl > 0: deteriorated_pnl = max(0.0, deteriorated_pnl)
                                elif original_pnl < 0: deteriorated_pnl = min(0.0, deteriorated_pnl)
                                if abs(deteriorated_pnl - original_pnl) > 0.01:
                                    position.realized_pnl = deteriorated_pnl
                                    updated = True
                            if position.max_gain != 0.0:
                                original_max_gain = position.max_gain
                                deteriorated_max_gain = self.deteriorate_max_gain(account_key, position)
                                if abs(deteriorated_max_gain - original_max_gain) > 0.01:
                                    position.max_gain = deteriorated_max_gain
                                    updated = True
                            reentry_data_pos = self.reentry_data.get(position_key)
                            if isinstance(reentry_data_pos, dict) and reentry_data_pos.get("reentry_amount", 0.0) > 0.0:
                                minutes_out = minutes_since(getattr(position, "last_reduction_time", None), now)
                                if minutes_out is not None and minutes_out >= 0:
                                    progress = min(1.0, minutes_out / 60.0)
                                    ref_price = safe_fetch_float(getattr(position, "mark_price", 0.0), 0.0) or safe_fetch_float(getattr(position, "entry_price", 0.0), 0.0) or safe_fetch_float(getattr(position, "last_reduction_price", 0.0), 0.0)
                                    if ref_price > 0:
                                        before_amount = reentry_data_pos.get("reentry_amount", 0.0)
                                        self.deteriorate_reentry_amounts(position, progress, ref_price)
                                        after_amount = self.reentry_data.get(position_key, {}).get("reentry_amount", before_amount)
                                        if abs(after_amount - before_amount) > 1e-6:
                                            updated = True
                            if updated:
                                updated_count += 1
            except Exception as e:
                logger.error(f"[PNL_DETERIORATION_TIMER] Error applying PnL deterioration: {e}", exc_info=True)
                await asyncio.sleep(300) 

    async def update_prev_gain_periodically(self) -> None:
        first_run = True
        try :
            while not self._housekeeping_stop:
                if first_run:
                    await asyncio.sleep(300)
                    first_run = False
                else:
                    await asyncio.sleep(180)
                now = datetime.now(timezone.utc)
                snapshot: List[Tuple[str, str, str, float, float, Optional[datetime], float]] = []
                async with self._positions_lock:
                    for position_key, position in self.positions.items():
                        symbol = getattr(position, "symbol", "")
                        position_side = getattr(position, "position_side", "")
                        entry = safe_fetch_float(getattr(position, "entry_price", 0.0), 0.0)
                        mark_price = safe_fetch_float(getattr(position, "mark_price", 0.0), 0.0)
                        mark_ts = getattr(position, "mark_price_last_updated", None)
                        pos_amt = abs(safe_fetch_float(getattr(position, "positionAmt", 0.0), 0.0))
                        snapshot.append((position_key, symbol, position_side, entry, mark_price, mark_ts, pos_amt))
                updates: Dict[str, Tuple[float, float, datetime]] = {}
                touched_accounts: Set[str] = set()
                for position_key, symbol, position_side, entry_price, mark_price, mark_ts, snap_pos_amt in snapshot:
                    account_key, _, _ = parse_position_key(position_key)
                    touched_accounts.add(account_key)
                    current_price = mark_price or 0.0
                    mark_dt = None
                    if isinstance(mark_ts, str):
                        try : mark_dt = isoparse(mark_ts)
                        except Exception: mark_dt = None
                    elif isinstance(mark_ts, datetime):
                        mark_dt = mark_ts
                    if mark_dt and mark_dt.tzinfo is None:
                        mark_dt = mark_dt.replace(tzinfo=timezone.utc)
                    price_age = (now - mark_dt).total_seconds() if mark_dt else None
                    if current_price <= 0.0 or (price_age is not None and price_age > 90):
                        refreshed_price = await self.get_mark_price(symbol, allow_fallback=True)
                        if refreshed_price and refreshed_price > 0:
                            current_price = refreshed_price
                    if current_price <= 0.0:
                        current_price = entry_price if entry_price > 0 else 0.0
                    calc_entry = entry_price if entry_price > 0 else current_price
                    if calc_entry <= 0 or calc_entry < current_price * 0.01 or calc_entry > current_price * 100:
                        if snap_pos_amt > 0 and entry_price > 0: logger.critical(f"[GAIN_ZEROED_PREVGAIN] {position_key}: calc_entry={calc_entry} cp={current_price} ep={entry_price} snap_amt={snap_pos_amt} REASON: {'ce<=0' if calc_entry<=0 else 'ce<cp*0.01' if calc_entry<current_price*0.01 else 'ce>cp*100'}")
                        # Use API unrealizedProfit for gain when entry is not credible
                        _snap_pos = self.positions.get(position_key)
                        _snap_pnl = safe_fetch_float(getattr(_snap_pos, 'unrealized_pnl_USD', None)) if _snap_pos else None
                        _snap_notional = snap_pos_amt * current_price if current_price > 0 else 0
                        if _snap_pnl is not None and _snap_notional > 0 and abs((_snap_pnl / _snap_notional) * 100) < 200:
                            current_gain = (_snap_pnl / _snap_notional) * 100
                        else:
                            current_gain = 0.0
                    elif snap_pos_amt == 0:
                        current_gain = None
                    else:
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
                        if gain_value is None:
                            if is_active and position.entry_price and position.entry_price > 0 and price_value > 0:
                                gain_value = calculate_gain(getattr(position, 'position_side', 'LONG'), price_value, position.entry_price)
                            else:
                                gain_value = 0.0
                        if is_active or time_since_close > 120:
                            if position.gain != gain_value:
                                position.prev_gain = position.gain
                                position.prev_gain_last_updated = timestamp_value
                        position.gain = gain_value
                        try :
                            parsed_account_key, _, _ = parse_position_key(position_key)
                            self.positions_by_account.setdefault(parsed_account_key, {})[position_key] = position
                        except Exception: pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.error(f"[prev_gain] loop error: {exc}")

    async def handle_unchanged_position(self, position, position_key: str, amt_abs:float, current_price: float, price_is_fresh: bool = False):
        now = datetime.now(timezone.utc)
        account_key, _, _ = parse_position_key(position_key)
        if abs(position.positionAmt) > 0 and amt_abs == 0:
            logger.critical(f"[UNCHANGED_ZEROING][{position_key}] handle_unchanged called with amt_abs=0 but position.positionAmt={position.positionAmt}! Caller is trying to zero a live position via unchanged handler. BLOCKING.")
            return
        async with self._positions_lock:
            if current_price and current_price > 0:
                # 2026-04-24: SELF-REINFORCING STALENESS FIX. Previously this path refreshed
                # mark_price_last_updated = now EVERY cycle regardless of whether the value
                # actually changed. Fix: ONLY advance timestamp when value changed OR caller
                # signals price_is_fresh=True (API mp / WS payload).
                # 2026-04-30: Removed inline get_current_price() — at ~200 positions × 0.5s
                # Redis timeout it stacked to >120s and tripped the PAU safety net (3 hangs
                # in 30 min on ang/inf). Caller already knows whether its source is fresh.
                _old_mark = position.mark_price or 0
                _value_changed = abs(current_price - _old_mark) / max(_old_mark, 1e-12) > 1e-6
                if _value_changed or price_is_fresh:
                    position.mark_price = current_price
                    position.mark_price_last_updated = now
                # else: leave mark_price_last_updated AT ITS ORIGINAL — staleness will surface to callers
            current_price = position.mark_price or current_price
            if amt_abs == 0.0:
                pass
            else:
                base_entry = position.entry_price if position.entry_price not in (None, 0.0) else current_price
                _entry_credible = base_entry > 0 and base_entry >= current_price * 0.01 and base_entry <= current_price * 100
                if _entry_credible:
                    new_gain = calculate_gain(position.position_side, current_price, base_entry)
                    if abs(new_gain) > 200:
                        _entry_credible = False
                if _entry_credible:
                    if abs(new_gain - position.gain) > 0.1:
                        position.prev_gain = position.gain
                        position.prev_gain_last_updated = now
                    position.gain = new_gain
                else:
                    # Entry not credible — use API unrealizedProfit for gain until next API cycle fixes entry_price
                    _api_pnl = getattr(position, 'unrealized_pnl_USD', None)
                    _notional = amt_abs * current_price if current_price > 0 else 0
                    if _api_pnl is not None and _notional > 0 and abs((_api_pnl / _notional) * 100) < 200:
                        _api_gain = (_api_pnl / _notional) * 100
                        if abs(_api_gain - position.gain) > 0.1:
                            position.prev_gain = position.gain
                            position.prev_gain_last_updated = now
                        position.gain = _api_gain
                    else:
                        position.gain = 0.0
                position.max_gain = max(position.gain, position.max_gain)
            position.last_updated = now
        qty = _safe_float(position.positionAmt)
        if qty:
            if (position.position_side or "").upper() == "LONG":
                position.unrealized_pnl_USD = (current_price - base_entry) * qty
            else:
                position.unrealized_pnl_USD = (base_entry - current_price) * qty
        else:
            position.unrealized_pnl_USD = None
        self.positions[position_key] = position
        try :
            account_key, _, _ = parse_position_key(position_key)
            self.positions_by_account.setdefault(account_key, {})[position_key] = position
        except Exception:
            pass
        self._mark_positions_dirty()

    async def handle_augmentation(self, position, position_key, positionAmt, positionAmt_abs, augment_qty, current_price, entry_price, price_is_fresh: bool = False):
        now = datetime.now(timezone.utc)
        if current_price and current_price > 0:
            # 2026-04-30: Removed inline get_current_price() — at ~200 positions × 0.5s
            # Redis timeout it stacked to >120s and tripped the PAU safety net. Caller
            # already knows whether its source is fresh (API mp / WS payload).
            position.mark_price = current_price
            position.mark_price_last_updated = now
        account_key, symbol, position_side = parse_position_key(position_key)
        min_qty = self.min_qty.get(symbol, 0.0001)*1.2
        pos_min_qty = max(3 * config.MIN_POSITION_SIZE / current_price, min_qty) if current_price > 0 else min_qty
        was_tiny_position = positionAmt * current_price <= max(config.MIN_POSITION_SIZE * 2, min_qty * current_price)
        position_value = positionAmt * current_price
        position_value_str = f"{position_value:.2f}"
        # last_signal set after decision_context fetch below (line ~6673)
        augment_value = augment_qty * current_price
        if augment_qty > 0:
            position.last_augmentation_amount = augment_qty
            position.last_augmentation_price = current_price
            position.last_augmentation_time = now
        if augment_value >= config.START_POSITION_SIZE:
            side = "BUY" if position_side == "LONG" else "SELL"
            if position_key in self.orders_in_limbo and side in self.orders_in_limbo[position_key]:
                del self.orders_in_limbo[position_key][side]
                if not self.orders_in_limbo[position_key]: del self.orders_in_limbo[position_key]
            self.augmentation_cooldown_map[position_key] = {'time': now, 'usd': augment_value}
            if hasattr(self, 'order_queue') and self.order_queue: self.order_queue.last_executed_time[(position_key, side)] = time.time()
            logger.debug(f"[{position_key}] Order confirmed - removed from limbo, cooldown set: {augment_qty:.6f} (${augment_value:.2f})")
        else:
            logger.debug(f"[{position_key}] Augmentation ${augment_value:.2f} below START_POSITION_SIZE - cooldown not applied")
        reentry_dirty = False
        reentry_deleted = False
        reentry_data_pos = self.reentry_data.get(position_key)
        if isinstance(reentry_data_pos, dict):
            try :
                current_reentry_amount = float(reentry_data_pos.get("reentry_amount", 0.0) or 0.0)
            except (TypeError, ValueError):
                current_reentry_amount = 0.0
            if current_reentry_amount > 0:
                remaining_amount = max(0.0, current_reentry_amount - augment_qty)
                threshold_qty = max(min_qty, config.MIN_POSITION_SIZE / current_price if current_price else 0.0)
                if remaining_amount <= threshold_qty:
                    reentry_deleted = True
                    self.reentry_data.pop(position_key, None)
                    logger.debug(f"[{position_key}] Reenter plan fulfilled - removing entry")
                else:
                    reentry_data_pos["reentry_amount"] = remaining_amount
                    reentry_data_pos["timestamp"] = now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    reentry_dirty = True
            else:
                reentry_deleted = True
                self.reentry_data.pop(position_key, None)
        elif reentry_data_pos is not None:
            reentry_deleted = True
            self.reentry_data.pop(position_key, None)
        if reentry_dirty or reentry_deleted:
            try :
                await self.save_reentry_data(position_key, force=True)
            except Exception as re_save_err:
                logger.warning(f"[{position_key}] Failed to persist reentry update: {re_save_err}")
        if was_tiny_position:
            if position.last_signal == 'AUGMENT':
                position.last_signal = 'OPEN'
            # USER 2026-05-16 mandate (PHBUSDT account-wipe incident): a position transitioning
            # from amt≈0 to amt>0 IS a fresh open — ALWAYS reset opened_at to now. Pre-fix this
            # only set opened_at when None, so a stale value from a prior closed cycle survived
            # and made RIDICULOUS_HOLD_GUARD see age=1000+ hours on a fresh hedge, killing it
            # 17 seconds after open. opened_at is in _POSITION_PROTECTED_FIELDS so cross-merge
            # alone can't repair it — must explicitly stamp here.
            position.opened_at = now
        else:
            try :
                indicators_now = await self.get_indicators_for_symbol(position.symbol, force_refresh=True)
                if indicators_now:
                    if position.position_side == 'LONG':
                        invalidation_level = float(indicators_now.get('dc_low_3m', 0) or 0.0)
                    else: 
                        invalidation_level = float(indicators_now.get('dc_high_3m', 0) or 0.0)
                    if invalidation_level:
                        self.invalidation_levels[position_key] = { 'level': invalidation_level, 'timestamp': now, 'position_side': position.position_side }
            except Exception as e:
                logger.error(f"[{position_key}] Could not set invalidation price during augmentation: {e}")
        _old_aug = position.positionAmt
        position.positionAmt = positionAmt_abs
        position.max_positionSize = float(config.get_account_setting(account_key, 'MAX_POSITION_SIZE') or config.MAX_POSITION_SIZE)
        logger.critical(f"[POSAMT_WRITE][HANDLE_AUG][{position_key}] {_old_aug} -> {positionAmt_abs} (augment_qty={augment_qty})")
        if augment_qty > 0 and position.positionAmt > 0:
            old_quantity = position.positionAmt - augment_qty
            # 2026-04-26 GUARD: entry_price is SACRED per CLAUDE.md. Refuse to write 0 — log and keep prior value.
            if old_quantity > pos_min_qty:
                old_entry_price = position.entry_price if position.entry_price > 0 else current_price
                old_value = old_quantity * old_entry_price
                new_value = augment_qty * current_price
                total_quantity = position.positionAmt
                _new_ep = (old_value + new_value) / total_quantity if total_quantity > 0 else 0
                if _new_ep > 0:
                    position.entry_price = _new_ep
                else:
                    logger.error(f"[ENTRY_PRICE_GUARD][{getattr(position,'symbol','?')}] Refusing to write entry_price=0 (computed={_new_ep}, total_qty={total_quantity}). Keeping prior entry_price={position.entry_price}.")
            else:
                if current_price > 0:
                    position.entry_price = current_price
                    position.initial_quantity = augment_qty
                else:
                    logger.error(f"[ENTRY_PRICE_GUARD][{getattr(position,'symbol','?')}] Refusing to write entry_price=0 (current_price={current_price}). Keeping prior entry_price={position.entry_price}.")
        base_entry_for_gain = position.entry_price
        new_gain = calculate_gain(position.position_side, current_price, base_entry_for_gain)
        if abs(new_gain - position.gain) > 0.1:
            position.prev_gain = position.gain
            position.prev_gain_last_updated = now
        position.gain = new_gain
        position.max_gain = max(position.max_gain, new_gain)
        deteriorated_max_qty = self.deteriorate_max_quantity(account_key, position, current_price)
        if position.positionAmt > deteriorated_max_qty:
            position.max_quantity = position.positionAmt
        else:
            position.max_quantity = deteriorated_max_qty
        # NEVER reset is_reduced — reduction history must survive augmentation
        self.augmented_positions[position_key] = now
        reversed_side = "SHORT" if position_side == "LONG" else "LONG" 
        reversed_key = construct_position_key(account_key, symbol, reversed_side)
        if reversed_key in self.reversed_positions:
            self.unmark_reversed(reversed_key)
        if current_price > 0 and positionAmt_abs > config.START_POSITION_SIZE / current_price:
            self.mark_augmented(position_key, timestamp=now)
        self._validate_position_consistency(position, position_key)
        await self.save_invalidation_levels(account_key)
        augment_value_str = f"{augment_qty * current_price:.2f}"
        conviction = None
        if position.augment_reason:
            conv_match = re.search(r'conv(-?[\d\.]+)', position.augment_reason)
            if conv_match:
                try :
                    conviction = float(conv_match.group(1))
                except ValueError:
                    conviction = None
        decision_context = await self._fetch_decision_context(position_key)
        if position_key not in self.position_reasons:
            self.position_reasons[position_key] = {}
        reason_text = decision_context.get('reason') if decision_context else (position.augment_reason or "Unknown")
        _action_from_ctx = decision_context.get('action', '') if decision_context else ''
        position.last_signal = _action_from_ctx or reason_text or 'AUGMENT'
        if not position.augment_reason and reason_text and reason_text != "Unknown":
            position.augment_reason = reason_text
        self.position_reasons[position_key]['last_augment_reason'] = reason_text
        self.position_reasons[position_key]['last_augment_full_context'] = decision_context
        await self._append_to_history(position_key, "AUGMENT", augment_qty, current_price, decision_context )
        try : indicators_log = indicators_now if indicators_now else None
        except NameError: indicators_log = None
        if not indicators_log and hasattr(self, 'get_indicators_for_symbol'): indicators_log = await self.get_indicators_for_symbol(symbol, force_refresh=False)
        timestamp_3m = indicators_log.get('timestamp_3m') if indicators_log else None
        k_3m = indicators_log.get('stoch_k_3m') if indicators_log else None
        k_3m_prv = indicators_log.get('k_3m_prev') if indicators_log else None
        _aug_log_key = f"{position_key}:{round(float(augment_qty),6)}"
        _aug_log_ts = self._augmentation_dedup.get(_aug_log_key, 0)
        if time.time() - _aug_log_ts > 10:
            self._augmentation_dedup[_aug_log_key] = time.time()
            log_augment_action( position_key=position_key, position_value_str=position_value_str, augment_value_str=augment_value_str, gain=position.gain, reason=position.augment_reason, conviction=conviction, origin="CONFIRMED", timestamp_3m=timestamp_3m, k_3m=k_3m, k_3m_prv=k_3m_prv )
        else:
            logger.debug(f"[AUGMENT_LOG_DEDUP] {position_key}: skipping duplicate action log within 10s")
        if self.stop_manager:
            asyncio.create_task(self.stop_manager.manage(account_key=account_key, symbol=symbol, position_key=position_key, position_side=position_side, position=position, current_price=current_price, entry_price=entry_price, event="augmentation", force_sync=True))
            logger.info(f"[HANDLE_AUGMENTATION] {position_key}: Triggered stop level sync after augmentation (final_pos={positionAmt_abs:.6f})")
        position.last_updated = now
        account_positions = self.positions_by_account.setdefault(account_key, {})
        account_positions[position_key] = position
        self.positions[position_key] = position
        logger.info(f"[HANDLE_AUGMENTATION] {position_key}: Added to dict (total positions: {len(self.positions)}, account positions: {len(account_positions)})")
        self._mark_positions_dirty()
        asyncio.create_task(self._broadcast_positions_to_redis(account_key, {position_key}))
        from ez_positions import atomic_save_positions
        await atomic_save_positions(self, account_key, force=True)

    def _fetch_decision_context_blocking(self, position_key: str) -> dict:
        """Synchronous JSONL/disk scan for decision context. MUST run in asyncio.to_thread — never on event loop. Each JSONL file can be 1–12 MB; line scan blocks event loop and was contributing to process_account_update HUNG > 120s on 2026-04-29."""
        try:
            account_key = position_key.split(':')[0] if ':' in position_key else ''
            if not account_key:
                return {}
            from pathlib import Path as _P
            _now = datetime.now(timezone.utc)
            for _days_back in range(2):
                _date = (_now - timedelta(days=_days_back)).strftime('%Y%m%d')
                _jfile = _P(config.BASE_PATH) / 'data' / 'decisions' / f'decisions_{account_key}_{_date}.jsonl'
                if not _jfile.exists(): continue
                _last_match = None
                try:
                    with open(_jfile, 'r') as _f:
                        for _line in _f:
                            _line = _line.strip()
                            if not _line: continue
                            if position_key in _line:
                                try: _last_match = orjson.loads(_line)
                                except Exception: pass
                except Exception: pass
                if _last_match: return _last_match
            _sym_side = position_key.split(':')[1] if ':' in position_key else ''
            _hfile = _P(config.BASE_PATH) / 'data' / 'history' / account_key / f'{_sym_side}.jsonl'
            if _hfile.exists():
                _last = None
                try:
                    with open(_hfile, 'r') as _f:
                        for _line in _f:
                            _line = _line.strip()
                            if _line:
                                try: _last = orjson.loads(_line)
                                except Exception: pass
                except Exception: pass
                if _last:
                    return {'action': _last.get('type', ''), 'reason': _last.get('reason', ''), 'snapshot': _last.get('indicators', {}), 'timestamp': _last.get('ts', '')}
        except Exception: pass
        return {}

    async def _fetch_decision_context(self, position_key: str) -> dict:
        """Fetch decision context: Redis first, then JSONL fallback (in worker thread), then history fallback.
        2026-04-30: Disk scans moved to asyncio.to_thread. Each redis.get is bounded by asyncio.wait_for(timeout=0.5)."""
        # 1. Try Redis (fastest, 5 min TTL) — bounded
        if self.redis_manager:
            redis_key = f"decision:{position_key}"
            client = self.redis_manager.connections.get('local') if hasattr(self.redis_manager, 'connections') else None
            if client:
                for _ in range(3):
                    try:
                        data = await asyncio.wait_for(client.get(redis_key), timeout=0.5)
                        if data: return orjson.loads(data)
                    except Exception: pass
                    await asyncio.sleep(0.2)
        # 2/3. JSONL/history scans run in worker thread — never on event loop
        try:
            return await asyncio.to_thread(self._fetch_decision_context_blocking, position_key)
        except Exception:
            return {}

    _history_dedup = {}
    async def _append_to_history(self, position_key: str, trade_type: str, qty: float, price: float, decision_context: dict = None):
        try :
            dedup_key = f"{position_key}:{trade_type}:{round(float(qty),6)}"
            now_ts = time.time()
            last_ts = self._history_dedup.get(dedup_key, 0)
            if now_ts - last_ts < 10.0:
                return
            self._history_dedup[dedup_key] = now_ts
            if len(self._history_dedup) > 5000:
                cutoff = now_ts - 60
                self._history_dedup = {k: v for k, v in self._history_dedup.items() if v > cutoff}
            parts = position_key.split(':')
            account_key = parts[0] if len(parts) > 0 else "unknown"
            symbol_side = parts[1] if len(parts) > 1 else position_key
            hist_dir = Path(self.config.DATA_DIR) / "history" / account_key
            hist_dir.mkdir(parents=True, exist_ok=True)
            filename = hist_dir / f"{symbol_side}.jsonl"
            MAX_SIZE_BYTES = 200 * 1024
            if filename.exists():
                try :
                    stat = filename.stat()
                    if stat.st_size > MAX_SIZE_BYTES:
                        backup = filename.with_name(f"{filename.name}.bak")
                        if backup.exists(): backup.unlink()
                        filename.rename(backup)
                except Exception: pass
            context = decision_context or {}
            # 2026-05-08 CURSE FIX: prefer qty-matched recent-order reason on trade_manager.
            # The Redis `decision:<pkey>` key is overwritten by competing concurrent strategies
            # so the most-recent decision often belongs to a DIFFERENT order than this fill.
            # Match by qty within 1% over the last 10s — a single-pkey ring populated by
            # send_webhook at order placement time.
            explicit_reason = None
            try:
                tm = getattr(self, 'trade_manager', None)
                if tm and hasattr(tm, '_recent_order_reasons'):
                    _ror_list = tm._recent_order_reasons.get(position_key, [])
                    _ror_now = time.time()
                    _ror_qty = float(qty or 0.0)
                    for _ror_ts, _ror_amt, _ror_reason in reversed(_ror_list):
                        if _ror_now - _ror_ts > 10.0:
                            break
                        if _ror_amt > 0 and _ror_qty > 0 and abs(_ror_amt - _ror_qty) / max(_ror_amt, 1e-9) < 0.01:
                            explicit_reason = _ror_reason
                            break
            except Exception:
                pass
            redis_key = f"decision:{position_key}"
            if explicit_reason is None and not context and self.redis_manager:
                try :
                    raw = await asyncio.wait_for(self.redis_manager.get(redis_key), timeout=0.5)
                    if raw: context = json.loads(raw)
                except Exception: pass
            reason = explicit_reason or context.get('reason') or context.get('reason_text') or "Manual/System Detection"
            snapshot = context.get('snapshot') or context.get('indicators', {})
            entry = { "ts": datetime.now(timezone.utc).isoformat(), "type": trade_type, "qty": round(float(qty), 6), "price": round(float(price), 8), "value": round(float(qty * price), 2), "reason": reason, "indicators": snapshot }
            async with aiofiles.open(filename, "a") as f:
                await f.write(orjson.dumps(entry).decode('utf-8') + "\n")
            if self.redis_manager:
                try: await asyncio.wait_for(self.redis_manager.delete(redis_key), timeout=0.5)
                except Exception: pass
        except Exception as e:
            logger.error(f"[HISTORY] Write error {position_key}: {e}")

    def record_reduction_fields(self, position_key: str, quantity: float, price: float, reason: str = ""):
        now = datetime.now(timezone.utc)
        for pos in self._get_all_position_refs(position_key):
            pos.last_reduction_amount = quantity
            pos.last_reduction_price = price
            pos.last_reduction_time = now
            pos.reduced_at = now
            pos.is_reduced = True
            pos.was_reduced = True
            pos.reduction_reason = reason
        logger.critical(f"[RECORD_REDUCTION] {position_key}: qty={quantity:.6f} price={price:.6f} reason={reason}")

    def record_augmentation_fields(self, position_key: str, quantity: float, price: float, reason: str = ""):
        now = datetime.now(timezone.utc)
        for pos in self._get_all_position_refs(position_key):
            pos.last_augmentation_amount = quantity
            pos.last_augmentation_price = price
            pos.last_augmentation_time = now
            pos.augment_reason = reason
        logger.critical(f"[RECORD_AUGMENTATION] {position_key}: qty={quantity:.6f} price={price:.6f} reason={reason}")

    def _get_all_position_refs(self, position_key: str) -> list:
        refs = []
        if position_key in self.positions:
            refs.append(self.positions[position_key])
        ak = position_key.split(':')[0] if ':' in position_key else ''
        if ak and ak in self.positions_by_account:
            p = self.positions_by_account[ak].get(position_key)
            if p and p not in refs:
                refs.append(p)
        return refs

    async def handle_reduction(self, position, position_key, positionAmt, positionAmt_abs, reduce_qty, current_price, entry_price, reduction_source="system", reason=None):
        now = datetime.now(timezone.utc) 
        account_key, symbol, position_side = parse_position_key(position_key)
        if current_price and current_price > 0:
            position.mark_price = current_price
            price_ts = None
            try :
                if hasattr(self, 'get_current_price'):
                    current_price, price_ts = await self.get_current_price(symbol)
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
        if reduce_qty > 0:
            position.last_reduction_amount = reduce_qty
            position.last_reduction_price = current_price
            position.last_reduction_time = now 
            position.reduced_at = now
            position.is_reduced = True
            if not is_tiny_reduction and not was_tiny_position:
                position.was_reduced = True
                self.reduced_positions[position_key] = now
                self._schedule_task(self.save_reduced_positions(account_key))
                side = "SELL" if position_side == "LONG" else "BUY"
                if position_key in self.orders_in_limbo and side in self.orders_in_limbo[position_key]:
                    del self.orders_in_limbo[position_key][side]
                    if not self.orders_in_limbo[position_key]:
                        del self.orders_in_limbo[position_key]
                self.reduction_cooldown_map[position_key] = now
                if hasattr(self, 'order_queue') and self.order_queue:
                    self.order_queue.last_executed_time[(position_key, side)] = time.time()
                logger.debug(f"[{position_key}] Order confirmed - removed from limbo, cooldown set: {reduce_qty:.6f}")
            await self.create_ladder_levels_for_reentry(position_key, account_key, position, current_price, now)
        is_tiny_position = positionAmt - reduce_qty < max(config.MIN_POSITION_SIZE * 3, min_qty * current_price)
        if position.entry_price > 0 and reduce_qty > 0:
            gain_at_reduction = position.gain
            reduction_ratio = reduce_qty / positionAmt if positionAmt > 0 else 1.0
            realized_gain = gain_at_reduction * reduction_ratio
            position.realized_pnl += realized_gain
            logger.debug(f"[{position_key}] PnL Update: Realized {gain_at_reduction*100:.2f}% (weighted: {realized_gain*100:.2f}%) from this reduction. " f"New cumulative Realized PnL: {position.realized_pnl*100:.2f}%")
        if is_tiny_position:
            position.max_gain = max(position.gain, position.max_gain)
        _old_red = position.positionAmt
        position.positionAmt = positionAmt_abs
        position.max_positionSize = float(config.get_account_setting(account_key, 'MAX_POSITION_SIZE') or config.MAX_POSITION_SIZE)
        logger.critical(f"[POSAMT_WRITE][HANDLE_RED][{position_key}] {_old_red} -> {positionAmt_abs} (reduction_source={reduction_source})")
        min_qty = self.min_qty.get(symbol, 0.0001)
        pos_min_qty = max(3 * config.MIN_POSITION_SIZE / current_price, min_qty)
        if positionAmt_abs <= pos_min_qty * 1.1:
            if self.service and hasattr(self.service, 'cancel_empty_stop'):
                await self.service.cancel_empty_stop(account_key, symbol, position_key, current_price, positionAmt_abs, require_tiny=False)
            logger.debug(f"[HANDLE_REDUCTION] {position_key}: Cancelled stop level - position at min_qty ({positionAmt_abs:.6f} <= {pos_min_qty * 1.1:.6f})")
        elif self.stop_manager:
            asyncio.create_task(self.stop_manager.manage(account_key=account_key, symbol=symbol, position_key=position_key, position_side=position_side, position=position, current_price=current_price, event="reduction", force_sync=True))
            logger.info(f"[HANDLE_REDUCTION] {position_key}: Triggered stop level sync after reduction (final_pos={positionAmt_abs:.6f})")
        logger.debug(f"[DEBUG] handle_reduction updated position.positionAmt to {position.positionAmt}")
        deteriorated_max_qty = self.deteriorate_max_quantity(account_key, position, current_price)
        if positionAmt > deteriorated_max_qty:
            position.max_quantity = positionAmt
        else:
            position.max_quantity = deteriorated_max_qty
        reversed_side = "SHORT" if position_side == "LONG" else "LONG" 
        reversed_key = construct_position_key(account_key, symbol, reversed_side)
        if reversed_key in self.reversed_positions:
            self.unmark_reversed(reversed_key)
        if was_tiny_position and positionAmt > pos_min_qty:
            # 2026-04-26 GUARD: entry_price is SACRED — refuse to write 0
            if current_price > 0:
                position.entry_price = current_price
            else:
                logger.error(f"[ENTRY_PRICE_GUARD][{getattr(position,'symbol','?')}] Refusing tiny-position entry_price write — current_price={current_price}. Keeping prior entry_price={position.entry_price}.")
            if position.was_reentered or position.was_reduced:
                position.was_reentered = False
                position.was_reduced = True
        # last_signal set after decision_context fetch below (line ~6905)
        if positionAmt > pos_min_qty:
            new_gain = calculate_gain(position.position_side, current_price, position.entry_price)
            if abs(new_gain - position.gain) > 0.1:
                position.prev_gain = position.gain
                position.prev_gain_last_updated = now
            position.gain = new_gain
            position.max_gain = max(new_gain, position.max_gain)
        position.last_updated = now
        existing_reentry_amount = 0.0
        if position_key in self.reentry_data:
            existing_entry = self.reentry_data[position_key]
            if not isinstance(existing_entry, dict):
                logger.warning(f"[process_account] Corrupted reentry entry for {position_key}: {type(existing_entry).__name__}. Resetting entry.")
                self.reentry_data[position_key] = {}
            else:
                existing_reentry_amount = existing_entry.get("reentry_amount", 0.0)
        else:
            for old_key in list(self.reentry_data.keys()):
                if clean_position_key(str(old_key)) == position_key:
                    existing_entry = self.reentry_data.pop(old_key)
                    if isinstance(existing_entry, dict):
                        existing_reentry_amount = existing_entry.get("reentry_amount", 0.0)
                    break
        total_reduction_amount = min(position.max_quantity, existing_reentry_amount + reduce_qty)
        reduction_reason = position.reduction_reason
        reason_str = position.reduction_reason
        if isinstance(reason_str, list):
            reason_str = ', '.join(str(r) for r in reason_str)
        elif not isinstance(reason_str, str):
            reason_str = str(reason_str)
        self.reentry_data[position_key] = { "reentry_level": float(current_price) if current_price is not None else 0.0, "reentry_amount": float(total_reduction_amount), "timestamp": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "reason": reason_str, }
        logger.debug(f"[process_account] {position_key} Reenter level set: {self.reentry_data[position_key]}. 2313")
        await self.save_reentry_data(position_key)
        if position_key in self.augmented_positions:
            self.unmark_augmented(position_key)
        position_value_str = f"{positionAmt * current_price:.2f}" if current_price else "N/A"
        reduction_value_str = f"{reduce_qty * current_price:.2f}" if current_price else "N/A"
        conviction = None
        if reason:
            reduction_reason = reason
            position.reduction_reason = reason
        elif position.reduction_reason and position.reduction_reason.strip():
            reduction_reason = position.reduction_reason
            conv_match = re.search(r'conv(-?[\d\.]+)', position.reduction_reason)
            if conv_match:
                try :
                    conviction = float(conv_match.group(1))
                except ValueError:
                    conviction = None
        elif reduction_source == "detected":
            reduction_reason = await self.determine_reduction_type(position_key, account_key, symbol, position_side, current_price, now)
            if reduction_reason:
                position.reduction_reason = reduction_reason
        decision_context = await self._fetch_decision_context(position_key)
        if position_key not in self.position_reasons:
            self.position_reasons[position_key] = {}
        reason_text = decision_context.get('reason') if decision_context else (reason or "Unknown")
        _red_action = decision_context.get('action', '') if decision_context else ''
        position.last_signal = _red_action or reason_text or reduction_reason or 'REDUCE'
        if not position.reduction_reason and reason_text and reason_text != "Unknown":
            position.reduction_reason = reason_text
            reduction_reason = reason_text
        elif not reduction_reason or not reduction_reason.strip():
            fallback_reason = reduction_source if reduction_source and reduction_source != "system" else "detected_reduction"
            position.reduction_reason = fallback_reason
            reduction_reason = fallback_reason
        self.position_reasons[position_key]['last_reduction_reason'] = reason_text
        self.position_reasons[position_key]['last_reduction_full_context'] = decision_context
        await self._append_to_history(position_key, "REDUCE", reduce_qty, current_price, decision_context )
        log_reduce_action( position_key=position_key, position_value_str=position_value_str, reduction_value_str=reduction_value_str, gain=position.gain, reason=reduction_reason, conviction=conviction, origin=reduction_reason )
        self._validate_position_consistency(position, position_key)
        account_positions = self.positions_by_account.setdefault(account_key, {})
        account_positions[position_key] = position
        self.positions[position_key] = position
        self._mark_positions_dirty()
        asyncio.create_task(self._broadcast_positions_to_redis(account_key, {position_key}))
        from ez_positions import atomic_save_positions
        await atomic_save_positions(self, account_key, force=True)
        try :
            # 2026-05-07: bounded; this path issues open-orders fetch + cancel/place via
            # asyncio.to_thread Binance calls and was a second hang point inside the
            # API_CONFIRMED_CLOSED zero-out flow. PAU MUST NOT block on Binance cleanup —
            # if the sync can't complete in 15s, fire-and-forget so the next sync can retry.
            await asyncio.wait_for(self.stop_manager.manage(account_key=account_key, symbol=symbol, position_key=position_key, position_side=position_side, position=position, current_price=current_price, entry_price=entry_price, event="reduction", force_sync=True), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning(f"[{position_key}] post-reduction stop_manager.manage >15s — backgrounding to keep PAU live")
            asyncio.create_task(self.stop_manager.manage(account_key=account_key, symbol=symbol, position_key=position_key, position_side=position_side, position=position, current_price=current_price, entry_price=entry_price, event="reduction", force_sync=True))
        except Exception as update_err:
            stack = traceback.format_exc().replace("\n", " | ")
            logger.warning(f"[{position_key}] Failed to update stops after reduction: {update_err} | stack={stack[:1024]}")
        position_value_before_reduction = positionAmt * current_price if current_price > 0 else 0.0
        position_value_after_reduction = positionAmt_abs * current_price if current_price > 0 else 0.0
        should_track = position_value_before_reduction > config.HIGH_GAIN_AUGMENTATION_MIN_SIZE
        if should_track and positionAmt_abs > 0:
            self.mark_direct_high_gain( position_key, stop_price="managed", entry_price=current_price, max_size=position_value_after_reduction, position_side=position_side, timestamp=datetime.now(timezone.utc), )
            logger.info(f"[REDUCTION] {position_key}: Added/updated in direct_high_gain_augmented (value_before=${position_value_before_reduction:.2f}, value_after=${position_value_after_reduction:.2f}) - will be monitored for 24h")

    async def emergency_refresh_indicators(self):
        """🚨 EMERGENCY: Force refresh all indicators - call this when data seems stale."""
        logger.critical("🚨 EMERGENCY INDICATORS REFRESH TRIGGERED")
        await self.force_refresh_all_indicators()
        logger.critical("🚨 EMERGENCY INDICATORS REFRESH COMPLETE")

    def should_evaluate_position(self, position_key: str) -> bool:
        """Check if position should be evaluated based on cooldown."""
        now = datetime.now(timezone.utc)
        last_eval = self.last_evaluation_time.get(position_key)
        if not last_eval:
            return True
        return (now - last_eval).total_seconds() >= self.evaluation_cooldown

    def mark_position_evaluated(self, position_key: str):
        """Mark position as evaluated to start cooldown."""
        self.last_evaluation_time[position_key] = datetime.now(timezone.utc)

    async def determine_reduction_type(self, position_key: str, account_key: str, symbol: str, position_side: str, current_price: float, now: datetime) -> str:
        try :
            if await self._was_stop_market_triggered(position_key, account_key, symbol, position_side, current_price):
                return "STOP_MARKET_ORDER_TRIGGERED"
            if await self._was_stop_level_hit(position_key, account_key, symbol, position_side, current_price):
                return "STOP_LEVEL_HIT"
            return "detected_reduction"
        except Exception as e:
            logger.error(f"[determine_reduction _type] Error determining reduction type for {position_key}: {e}")
            return "detected_reduction"

    async def _was_stop_market_triggered(self, position_key: str, account_key: str, symbol: str, position_side: str, current_price: float) -> bool:
        try :
            client = self.get_client(account_key)
            if not client:
                return False
            orders = await self.get_cached_open_orders(account_key, symbol) if self.get_cached_open_orders else None
            if not orders:
                async with self.maker_semaphore:
                    all_orders = await asyncio.wait_for( asyncio.to_thread(client.futures_get_all_orders, symbol=symbol, limit=10), timeout=10.0 )
                for order in all_orders:
                    if (order.get('type') == 'STOP_MARKET' and order.get('status') == 'FILLED' and order.get('positionSide') == position_side):
                        order_time = datetime.fromtimestamp(order.get('time', 0) / 1000, tz=timezone.utc)
                        current_time = datetime.now(timezone.utc)
                        if (current_time - order_time).total_seconds() < 300: 
                            logger.info(f"[{position_key}] STOP_MARKET order detected: {order.get('orderId')} filled at {order.get('avgPrice')}")
                            return True
            return False
        except Exception as e:
            logger.error(f"[_was_stop_market_triggered] Error checking STOP_MARKET for {position_key}: {e}")
            return False

    async def _was_stop_level_hit(self, position_key: str, account_key: str, symbol: str, position_side: str, current_price: float) -> bool:
        try :
            if position_key in self.stop_levels:
                stop_levels = self.stop_levels[position_key]
                if isinstance(stop_levels, list):
                    for stop_obj in stop_levels:
                        if isinstance(stop_obj, dict) and 'level' in stop_obj:
                            stop_level = stop_obj.get('level', 0)
                            if stop_level > 0:
                                if position_side == "LONG" and current_price <= stop_level:
                                    logger.info(f"[{position_key}] Stop level hit: {stop_level} (LONG position at {current_price})")
                                    return True
                                elif position_side == "SHORT" and current_price >= stop_level:
                                    logger.info(f"[{position_key}] Stop level hit: {stop_level} (SHORT position at {current_price})")
                                    return True
            return False
        except Exception as e:
            logger.error(f"[_was_stop_level_hit] Error checking stop levels for {position_key}: {e}")
            return False

    async def create_ladder_levels_for_reentry(self, position_key: str, account_key: str, position: Position, current_price: float, now: datetime):
        try :
            symbol = position.symbol
            is_long = position.position_side == 'LONG'
            indicators_raw = await fetch_fresh_snapshot_for_symbol(self, symbol, force_refresh=False)
            indicators = indicators_raw
            if not isinstance(indicators, dict):
                indicators = {}

            def _safe_indicator(key: str, default: float) -> float:
                try :
                    value = indicators.get(key, default)
                    if value is None:
                        return default
                    value_float = float(value)
                    if math.isnan(value_float) or math.isinf(value_float):
                        return default
                    return value_float
                except (TypeError, ValueError, RecursionError) as e:
                    return default
            dc_basis_3m = _safe_indicator('dc_basis_3m', current_price)
            dc_basis_15m = _safe_indicator('dc_basis_15m', current_price)
            dc_basis_1h = _safe_indicator('dc_basis_1h', current_price)
            dc_low_3m = _safe_indicator('dc_low_3m', current_price)
            dc_low_15m = _safe_indicator('dc_low_15m', current_price)
            dc_low_1h = _safe_indicator('dc_low_1h', current_price)
            dc_low_4h = _safe_indicator('dc_low_4h', current_price)
            dc_high_3m = _safe_indicator('dc_high_3m', current_price)
            dc_high_15m = _safe_indicator('dc_high_15m', current_price)
            dc_high_1h = _safe_indicator('dc_high_1h', current_price)
            dc_high_4h = _safe_indicator('dc_high_4h', current_price)
            atr_3m = _safe_indicator('atr_3m', abs(current_price) * 0.01)
            atr_15m = _safe_indicator('atr_15m', abs(current_price) * 0.015)
            atr_1h = _safe_indicator('atr_1h', abs(current_price) * 0.02)
            atr_4h = _safe_indicator('atr_4h', abs(current_price) * 0.025)
            ladder_qty = float(position.last_reduction_amount or 0.0)
            if ladder_qty <= 0:
                ladder_qty = abs(position.positionAmt) or (config.START_POSITION_SIZE / max(abs(current_price), 1e-9)) if position.positionAmt else (config.START_POSITION_SIZE / max(abs(current_price), 1e-9))
            if ladder_qty <= 0 and position.positionAmt > 0:
                ladder_qty = abs(position.positionAmt)
            if ladder_qty <= 0:
                logger.warning(f"[{position_key}] Skipping ladder creation - invalid ladder quantity {ladder_qty}")
                return
            if is_long:
                ladder_prices = [ dc_basis_3m, dc_basis_15m, dc_basis_1h, dc_low_3m + float(atr_3m) * 1.5, dc_low_15m + float(atr_15m) * 1.5, dc_low_1h + float(atr_1h) * 1.5, dc_low_4h + float(atr_4h) * 1.5 ]
            else:
                ladder_prices = [ dc_basis_3m, dc_basis_15m, dc_basis_1h, dc_high_3m - float(atr_3m) * 1.5, dc_high_15m - float(atr_15m) * 1.5, dc_high_1h - float(atr_1h) * 1.5, dc_high_4h - float(atr_4h) * 1.5 ]
            ladder_levels = []
            for price in ladder_prices:
                if price and isinstance(price, (int, float)) and price > 0:
                    ladder_levels.append({ "level": float(price), "quantity": float(ladder_qty), "created_at": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "crossed": False, "stoch_reversed": False })
            if not ladder_levels:
                logger.warning(f"[{position_key}] Indicator-driven ladder creation failed. Using percentage-based fallback levels.")
                if current_price <= 0:
                    return
                multipliers = [0.985, 0.975, 0.965] if is_long else [1.015, 1.025, 1.035]
                ladder_levels = [{ "level": float(max(current_price * m, 1e-10)), "quantity": float(ladder_qty), "created_at": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "crossed": False, "stoch_reversed": False } for m in multipliers]
            sanitized_levels = []
            for entry in ladder_levels:
                try :
                    level_val = float(entry.get("level", 0.0))
                    qty_val = float(entry.get("quantity", 0.0))
                    if math.isnan(level_val) or math.isinf(level_val) or level_val <= 0:
                        logger.warning(f"[{position_key}] Skipping invalid ladder level: {level_val}")
                        continue
                    if math.isnan(qty_val) or math.isinf(qty_val) or qty_val <= 0:
                        logger.warning(f"[{position_key}] Skipping invalid ladder quantity: {qty_val}")
                        continue
                    sanitized_entry = { "level": level_val, "quantity": qty_val, "created_at": str(entry.get("created_at", now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'))), "crossed": bool(entry.get("crossed", False)), "stoch_reversed": bool(entry.get("stoch_reversed", False)), }
                    sanitized_levels.append(sanitized_entry)
                except (TypeError, ValueError) as sanitize_error:
                    logger.warning(f"[{position_key}] Skipping malformed ladder entry during sanitization: {sanitize_error}")
                except Exception as sanitize_error:
                    logger.warning(f"[{position_key}] Skipping malformed ladder entry during sanitization: {sanitize_error}")
            if not sanitized_levels:
                logger.warning(f"[{position_key}] No valid ladder levels after sanitization, skipping ladder creation")
                return
            ladder_record = { "levels": sanitized_levels, "created_at": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "is_long": bool(is_long), "symbol": str(symbol), "account_key": str(account_key), }
            self.ladder_levels[position_key] = ladder_record
            await self.save_ladder_levels(position_key)
            logger.debug(f"[{position_key}] Created {len(sanitized_levels)} ladder levels for reentry after reduction and saved immediately")
        except Exception as e:
            logger.error(f"[{position_key}] Failed to create ladder levels after reduction: {e}")

    def _load_zero_report_tracker(self) -> Dict[str, Dict[str, Any]]:
        try:
            zrt_path = Path(config.BASE_PATH) / "data" / "zero_report_tracker.json"
            if zrt_path.exists():
                with open(zrt_path, 'r') as f:
                    data = json.loads(f.read())
                if isinstance(data, dict):
                    for pk, rec in data.items():
                        if 'first_seen_zero_at' in rec and isinstance(rec['first_seen_zero_at'], str):
                            try: rec['first_seen_zero_at'] = isoparse(rec['first_seen_zero_at'])
                            except Exception: pass
                        if 'last_increment_at' in rec and isinstance(rec['last_increment_at'], str):
                            try: rec['last_increment_at'] = isoparse(rec['last_increment_at'])
                            except Exception: pass
                    logger.info(f"[ZERO_TRACKER] Loaded {len(data)} entries from disk")
                    return data
        except Exception as e:
            logger.warning(f"[ZERO_TRACKER] Failed to load from disk: {e}")
        return {}

    def _save_zero_report_tracker(self):
        try:
            zrt_path = Path(config.BASE_PATH) / "data" / "zero_report_tracker.json"
            serializable = {}
            for pk, rec in self.zero_report_tracker.items():
                entry = {}
                for k, v in rec.items():
                    if isinstance(v, datetime): entry[k] = v.isoformat()
                    else: entry[k] = v
                serializable[pk] = entry
            with open(str(zrt_path) + ".tmp", 'w') as f:
                f.write(json.dumps(serializable, indent=2, default=str))
            os.replace(str(zrt_path) + ".tmp", str(zrt_path))
        except Exception as e:
            logger.warning(f"[ZERO_TRACKER] Failed to save to disk: {e}")

    def _log_zero_report(self, position_key: str, now: datetime, source: str = "UNKNOWN", debounce_seconds: int = 20):
        record = self.zero_report_tracker.get(position_key)
        if record is None:
            self.zero_report_tracker[position_key] = { "count": 1, "first_seen_zero_at": now, "last_increment_at": now, "sources": {source: 1}, }
            try :
                pos = self.positions.get(position_key)
                if pos is None:
                    try :
                        ak, _, _ = parse_position_key(position_key)
                        pos = self.positions_by_account.get(ak, {}).get(position_key)
                    except Exception:
                        pos = None
                if pos is not None:
                    if not pos.last_signal:
                        pos.last_signal = "ZERO_REPORTED"
                    elif "ZERO_REPORTED" not in pos.last_signal:
                        pos.last_signal = f"{pos.last_signal} | ZERO_REPORTED"
            except Exception:
                pass
            return 1
        last_inc = record.get("last_increment_at")
        if last_inc and (now - last_inc).total_seconds() < debounce_seconds:
            return record.get("count", 1)
        new_count = record.get("count", 0) + 1
        record["count"] = new_count
        record["last_increment_at"] = now
        sources_map = record.get("sources") or {}
        sources_map[source] = sources_map.get(source, 0) + 1
        record["sources"] = sources_map
        try :
            pos = self.positions.get(position_key)
            if pos is None:
                try :
                    ak, _, _ = parse_position_key(position_key)
                    pos = self.positions_by_account.get(ak, {}).get(position_key)
                except Exception:
                    pos = None
            if pos is not None and (not pos.last_signal or "ZERO_REPORTED" not in pos.last_signal):
                if not pos.last_signal:
                    pos.last_signal = "ZERO_REPORTED"
                else:
                    pos.last_signal = f"{pos.last_signal} | ZERO_REPORTED"
        except Exception:
            pass
        self.zero_report_tracker[position_key] = record
        self._save_zero_report_tracker()
        return new_count

    def _clear_zero_report(self, position_key: str):
        if position_key in self.zero_report_tracker:
            del self.zero_report_tracker[position_key]
            self._save_zero_report_tracker()

    def _is_zero_confirmed(self, position_key: str, threshold: int = None) -> bool:
        if threshold is None:
            threshold = config.ZERO_CONFIRMATION_THRESHOLD_WS
        if position_key in self.zero_report_tracker:
            return self.zero_report_tracker[position_key]["count"] >= threshold
        return False

    async def _confirm_nonzero_delta(self, position_key: str, reported_amt: float, prev_amt: float, source: str = "UNKNOWN", window_seconds: int = 15) -> bool:
        try :
            now = datetime.now(timezone.utc)
            try :
                ak, sym, side = parse_position_key(position_key)
            except Exception:
                sym = None
            min_qty = self.min_qty.get(sym, 0.001) * 1.2 if sym else 0.001
            position = self.positions.get(position_key)
            current_price = float(position.mark_price) if position.mark_price > 0 else await quick_price(sym)
            if not current_price: current_price, ts = await get_current_price (sym) 
            min_usd = max(5.50, min_qty*current_price)
            delta = abs(reported_amt - prev_amt)
            if current_price:
                if delta < max(min_qty, min_usd / max(current_price, 1e-9)):
                    logger.debug(f"[_confirm_nonzero_delta] IGNORE small delta for {position_key}: {delta:.6f}")
                    return False
            else:
                if delta < min_qty:
                    return False
            force_accept = False
            if source and source.upper() == "API" and current_price:
                usd_delta = delta * current_price
                if prev_amt == 0 or usd_delta >= config.MIN_USD_DELTA_CONFIRM:
                    force_accept = True
            if force_accept:
                logger.debug(f"[_confirm_nonzero_delta] ACCEPT delta for {position_key}: {delta:.6f} (source=API, prev={prev_amt:.6f}, reported={reported_amt:.6f})")
                return True
            lst = self.nonzero_report_tracker.setdefault(position_key, [])
            lst = [r for r in lst if (now - r[0]).total_seconds() <= window_seconds]
            lst.append((now, reported_amt, source))
            self.nonzero_report_tracker[position_key] = lst
            same_dir = [r for r in lst if (r[1] - prev_amt) * (reported_amt - prev_amt) > 0]
            if len(same_dir) >= 2:
                return True
            srcs = set([r[2] for r in lst])
            if "WS" in srcs and "API" in srcs:
                return True
            return False
        except Exception as e:
            logger.debug(f"[_confirm_nonzero_delta] Fallback True due to error: {e}")
            return True

    def _is_priority_position(self, position_key: str, position: Optional[Position]) -> bool:
        if not position:
            return False
        try :
            mark_price_val = safe_fetch_float(position.mark_price, 0.0)
            notional = abs(position.positionAmt) * max(mark_price_val or 0.0, 0.0)
            return notional >= self.priority_position_threshold
        except Exception:
            return False

    async def _confirm_significant_price_change(self, position_key: str, current_price: float, window_minutes: int = 5, min_change_pct: float = 0.12) -> bool:
        try :
            now = datetime.now(timezone.utc)
            try :
                ak, sym, side = parse_position_key(position_key)
            except Exception:
                sym = None
            position_obj = None
            try :
                if ak:
                    position_obj = self.positions_by_account.get(ak, {}).get(position_key)
            except Exception:
                position_obj = None
            is_priority = self._is_priority_position(position_key, position_obj)
            if is_priority:
                window_minutes = self.price_filter_window_priority
                min_change_pct = self.price_filter_min_pct_priority
            else:
                window_minutes = self.price_filter_window_default
                min_change_pct = self.price_filter_min_pct_default
            lst = self.price_change_tracker.setdefault(position_key, [])
            window_seconds = max(int(window_minutes * 60), 60)
            max_history_window = max(window_seconds * 3, window_seconds + 120)
            lst = [r for r in lst if (now - r[0]).total_seconds() <= max_history_window]
            baseline_entry = None
            for entry in lst:
                age = (now - entry[0]).total_seconds()
                if age >= window_seconds:
                    baseline_entry = entry
                    break
            if not lst or baseline_entry is None:
                if not lst:
                    logger.debug(f"[_confirm_significant_price_change] First time processing {position_key}, allowing")
                else:
                    logger.debug(f"[_confirm_significant_price_change] Insufficient history (< {window_minutes}m) for {position_key}, allowing")
                lst.append((now, current_price, 0.0))
                self.price_change_tracker[position_key] = lst
                return True
            base_price = baseline_entry[1]
            if not base_price or base_price <= 0:
                lst.append((now, current_price, 0.0))
                self.price_change_tracker[position_key] = lst
                return True
            price_change_pct = abs(current_price - base_price) / base_price * 100
            lst.append((now, current_price, price_change_pct))
            self.price_change_tracker[position_key] = lst
            if price_change_pct < min_change_pct and not is_priority:
                return False
            return True
        except Exception as e:
            logger.debug(f"[_confirm_significant_price_change] Fallback True due to error: {e}")
            return True

    def get_account_positions(self, account_key, side=None):
        subdict = self.positions_by_account.get(account_key, {})
        if side is None:
            return subdict
        return { k: v for k, v in subdict.copy().items() if v.position_side.upper() == side.upper()}

    def _get_augment_file_path(self, account_key: str) -> Path:
        if not account_key:
            raise ValueError("account_key cannot be empty")
        base_dir = Path(self.config.BASE_PATH) / account_key 
        filepath = base_dir / "augmented_positions.json"
        try :
            filepath.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create directory {filepath.parent}: {e}")
            raise 
        return filepath

    def _get_reduced_file_path(self, account_key: str) -> Path:
        if not account_key:
            raise ValueError("account_key cannot be empty")
        base_dir = Path(self.config.BASE_PATH) / account_key
        filepath = base_dir / "reduced_positions.json"
        try :
            filepath.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create directory {filepath.parent}: {e}")
            raise
        return filepath

    async def _file_age_seconds(self, filepath: Path) -> Optional[float]:
        try :
            if not await aio_os.path.exists(filepath):
                return None
            stat_info = await aio_os.stat(filepath)
            modified = datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc)
            age = (datetime.now(timezone.utc) - modified).total_seconds()
            return max(age, 0.0)
        except Exception as exc:
            logger.debug(f"[file_age] Failed to stat {filepath}: {exc}")
            return None

    async def save_augmented_positions(self, account_key: str, min_interval: int = 4, force: bool = False):
        if not account_key:
            return
        try :
            memory_data = {}
            for k, v in self.augmented_positions.items():
                if k.startswith(f"{account_key}:"):
                    if isinstance(v, datetime):
                        ts = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
                        memory_data[k] = ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    else:
                        dt = safe_datetime(v)
                        if isinstance(dt, datetime):
                            ts = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                            memory_data[k] = ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        elif v is not None:
                            memory_data[k] = str(v)
            filepath = self._get_augment_file_path(account_key)
            existing_data = await load_json_safe(str(filepath))
            if not isinstance(existing_data, dict):
                existing_data = {}
            if existing_data:
                merged_data = dict(existing_data)
                merged_data.update(memory_data)
                final_data = merged_data
            else:
                final_data = memory_data
            if memory_data or final_data or force:
                data_to_write = final_data if final_data else memory_data if memory_data else {}
                if getattr(self.config, 'VERBOSE_FETCH_LOGGING', getattr(self.config, 'VERBOSE', False)):
                    logger.debug(f"[save_augmented_positions] {account_key}: memory_data={len(memory_data)} entries, data_to_write={len(data_to_write)} entries: {list(data_to_write.keys())[:10]}")
                await atomic_write_json(filepath, data_to_write)
                await self._broadcast_augmented_positions_to_redis(account_key, data_to_write)
        except Exception as e:
            logger.error(f"[save_augmented_positions] Failed for {account_key}: {e}", exc_info=True)

    async def save_reduced_positions(self, account_key: str, min_interval: int = 60, force: bool = False):
        if not account_key:
            return
        try :
            memory_data = {}
            for key, ts in self.reduced_positions.items():
                if key.startswith(f"{account_key}:"):
                    if isinstance(ts, datetime):
                        iso = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
                        memory_data[key] = iso.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    else:
                        dt = safe_datetime(ts)
                        if isinstance(dt, datetime):
                            iso = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                            memory_data[key] = iso.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        elif ts is not None:
                            memory_data[key] = str(ts)
            filepath = self._get_reduced_file_path(account_key)
            existing_data = await load_json_safe(str(filepath))
            if not isinstance(existing_data, dict):
                existing_data = {}
            if existing_data:
                merged_data = dict(existing_data)
                merged_data.update(memory_data)
                final_data = merged_data
            else:
                final_data = memory_data
            if memory_data or final_data or force:
                data_to_write = final_data if final_data else memory_data if memory_data else {}
                if getattr(self.config, 'VERBOSE_FETCH_LOGGING', getattr(self.config, 'VERBOSE', False)):
                    logger.debug(f"[save_reduced_positions] {account_key}: memory_data={len(memory_data)} entries, data_to_write={len(data_to_write)} entries: {list(data_to_write.keys())[:10]}")
                await atomic_write_json(filepath, data_to_write)
                await self._broadcast_reduced_positions_to_redis(account_key, data_to_write)
        except Exception as e:
            logger.error(f"[save_reduced_positions] Failed for {account_key}: {e}", exc_info=True)

    def _get_direct_high_gain_file_path(self, account_key: str) -> Path:
        if not account_key:
            raise ValueError("account_key cannot be empty")
        base_dir = Path(self.config.BASE_PATH) / account_key
        filepath = base_dir / "direct_high_gain_augmented.json"
        try :
            filepath.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create directory {filepath.parent}: {e}")
            raise
        return filepath

    async def save_direct_high_gain_augmented(self, account_key: str, min_interval: int = 60, force: bool = False):
        if not account_key:
            logger.error("[direct_high_gain] Save called with empty account_key.")
            return
        now = datetime.now(timezone.utc)
        last_save_info = self._last_direct_high_gain_save_time.get(account_key, {})
        last_save_ts = last_save_info.get("timestamp")
        filepath = self._get_direct_high_gain_file_path(account_key)
        file_age_seconds = await self._file_age_seconds(filepath)
        force_due_to_stale = force or (file_age_seconds is None or file_age_seconds >= max(60.0, self._persist_force_interval))
        if last_save_ts and (now - last_save_ts) < timedelta(seconds=min_interval) and not force_due_to_stale:
            return
        memory_data_for_account = {}
        memory_keys_for_account = set()
        for k, v in self.direct_high_gain_augmented.items():
            if k.startswith(f"{account_key}:"):
                if isinstance(v, dict) and 'timestamp' in v:
                    save_entry = v.copy()
                    ts = v['timestamp'] if isinstance(v['timestamp'], datetime) else safe_datetime(v['timestamp'])
                    if ts:
                        save_entry['timestamp'] = ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ') if ts else None
                    memory_data_for_account[k] = save_entry
                    memory_keys_for_account.add(k)
        existing_data_in_file = await load_json_safe(str(filepath))
        if not isinstance(existing_data_in_file, dict):
            logger.error(f"[direct_high_gain] Corrupt data in {filepath} for account '{account_key}'. Aborting save.")
            self._last_direct_high_gain_save_time[account_key] = {"timestamp": now, "status": "corrupt_existing"}
            return
        final_data_to_save = dict(existing_data_in_file)
        final_data_to_save.update(memory_data_for_account)
        keys_to_remove = []
        for key in list(final_data_to_save.keys()):
            if key not in memory_keys_for_account:
                entry = final_data_to_save.get(key)
                if isinstance(entry, dict):
                    ts_str = entry.get('timestamp')
                    ts = safe_datetime(ts_str) if ts_str else None
                    if ts:
                        age_seconds = (now - ts).total_seconds()
                        if age_seconds >= 86400:
                            account_key_from_key = key.split(':')[0] if ':' in key else None
                            if account_key_from_key == account_key:
                                position = self.positions_by_account.get(account_key, {}).get(key)
                                if position and hasattr(position, 'realized_pnl'):
                                    realized_pnl = float(getattr(position, 'realized_pnl', 0.0))
                                    if realized_pnl < 1.0:
                                        keys_to_remove.append(key)
        for key in keys_to_remove:
            final_data_to_save.pop(key, None)
        keys_removed = set(existing_data_in_file.keys()) - set(final_data_to_save.keys())
        keys_added = memory_keys_for_account - set(existing_data_in_file.keys())
        values_changed = any(final_data_to_save.get(key) != existing_data_in_file.get(key) for key in memory_keys_for_account.intersection(set(existing_data_in_file.keys())))
        if getattr(self.config, 'VERBOSE_FETCH_LOGGING', getattr(self.config, 'VERBOSE', False)):
            logger.debug(f"[save_direct_high_gain_augmented] {account_key}: memory_data={len(self.direct_high_gain_augmented)} entries, final_data={len(final_data_to_save)} entries: {list(final_data_to_save.keys())[:10]}")
        if not keys_removed and not keys_added and not values_changed and not force_due_to_stale:
            if not memory_data_for_account:
                self._last_direct_high_gain_save_time[account_key] = {"timestamp": now, "status": "no_changes"}
                return
        file_specific_lock = await self._get_file_lock(filepath)
        async with file_specific_lock:
            current_locked_last_save_info = self._last_direct_high_gain_save_time.get(account_key, {})
            current_locked_last_save_ts = current_locked_last_save_info.get("timestamp")
            if not force_due_to_stale and current_locked_last_save_ts and current_locked_last_save_info.get("status") == "success" and (now - current_locked_last_save_ts) < timedelta(seconds=min_interval):
                logger.debug(f"[direct_high_gain] {account_key}: Skipped write inside lock due to very recent successful save by another task.")
                return
            tmp_path = filepath.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex[:4]}.tmp")
            backup_path = filepath.with_suffix(".bak")
            try :
                await aio_os.makedirs(filepath.parent, exist_ok=True)
                if await aio_os.path.exists(filepath) and (await aio_os.stat(filepath)).st_size > 0:
                    try :
                        async with LimitedAioOpen(str(filepath), 'rb') as src:
                            async with LimitedAioOpen(str(backup_path), 'wb') as dst:
                                while chunk := await src.read(8192):
                                    await dst.write(chunk)
                        logger.debug(f"[direct_high_gain] Backup for '{account_key}' created: {backup_path}")
                    except Exception as backup_error:
                        logger.warning(f"[direct_high_gain] Failed to create backup for '{account_key}': {backup_error}")
                json_str = json.dumps(final_data_to_save, indent=2)
                if await aio_os.path.exists(tmp_path):
                    try : await aio_os.remove(tmp_path)
                    except Exception: pass
                async with LimitedAioOpen(tmp_path, "w", encoding="utf-8") as f:
                    await f.write(json_str)
                    await f.flush()
                try :

                    def _sync_file(p): 
                        if os.path.exists(p): fd = os.open(p, os.O_RDONLY); os.fsync(fd); os.close(fd)
                    await asyncio.to_thread(_sync_file, str(tmp_path))
                except Exception: pass
                await asyncio.sleep(0.01)
                max_verify_attempts = 5
                verify_delay = 0.01
                temp_file_verified = False
                expected_size = len(json_str.encode('utf-8'))
                for attempt in range(max_verify_attempts):
                    try :
                        if await aio_os.path.exists(tmp_path):
                            stat_info_temp = await aio_os.stat(tmp_path)
                            size_diff = abs(stat_info_temp.st_size - expected_size)
                            if (expected_size == 0 and stat_info_temp.st_size == 0) or (stat_info_temp.st_size > 0 and size_diff <= 1):
                                temp_file_verified = True; break
                            else: logger.warning(f"Attempt {attempt+1}/{max_verify_attempts}: Temp file {tmp_path} size mismatch. Expected: {expected_size}, Got: {stat_info_temp.st_size}, Diff: {size_diff}")
                        else: logger.warning(f"Attempt {attempt+1}/{max_verify_attempts}: Temp file {tmp_path} not found after write")
                    except Exception as verify_err: logger.debug(f"Verification attempt {attempt+1} error: {verify_err}")
                    if attempt < max_verify_attempts - 1: await asyncio.sleep(verify_delay)
                    verify_delay = min(verify_delay * 1.5, 0.1)
                if not temp_file_verified:
                    try :
                        actual_size = (await aio_os.stat(tmp_path)).st_size if await aio_os.path.exists(tmp_path) else -1
                        msg = f"Temp file {tmp_path} verification failed after {max_verify_attempts} attempts. Expected: {expected_size}, Got: {actual_size}"
                    except Exception: msg = f"Temp file {tmp_path} verification failed after {max_verify_attempts} attempts"
                    logger.error(msg)
                    raise RuntimeError(msg)
                await asyncio.to_thread(os.replace, str(tmp_path), str(filepath))
                self._last_direct_high_gain_save_time[account_key] = {"timestamp": datetime.now(timezone.utc), "status": "success"}
                await self._broadcast_direct_high_gain_to_redis(account_key, final_data_to_save)
            except Exception as e:
                logger.error(f"[direct_high_gain] Save failed for account '{account_key}' (lock held): {e}", exc_info=True)
                self._last_direct_high_gain_save_time[account_key] = {"timestamp": datetime.now(timezone.utc), "status": "failed"}
                if await aio_os.path.exists(backup_path):
                    try :
                        await asyncio.to_thread(shutil.copy2, str(backup_path), str(filepath))
                        logger.warning(f"[direct_high_gain] Restored '{account_key}' from backup: {filepath} (lock held)")
                    except Exception as restore_error:
                        logger.error(f"[direct_high_gain] Failed to restore backup for '{account_key}': {restore_error} (lock held)")
            finally:
                if await aio_os.path.exists(tmp_path):
                    try : await aio_os.remove(tmp_path)
                    except OSError: logger.warning(f"[direct_high_gain] Could not remove temporary file: {tmp_path} (lock held)")

    async def load_direct_high_gain_augmented(self, account_key: str):
        if not account_key:
            logger.error("[direct_high_gain] Load called with empty account_key.")
            return
        filepath = self._get_direct_high_gain_file_path(account_key)
        loaded_data = await load_json_safe(str(filepath))
        if not isinstance(loaded_data, dict):
            logger.error(f"[direct_high_gain] Unexpected data format in {filepath} for account '{account_key}': {type(loaded_data).__name__}. Skipping load.")
            return
        loaded_count = 0
        added_count = 0
        skipped_count = 0
        keys_to_process = {k: v for k, v in loaded_data.items() if k.startswith(f"{account_key}:")}
        if len(keys_to_process) != len(loaded_data):
            logger.warning(f"[direct_high_gain] File {filepath} contained keys not matching account '{account_key}'. Processing only matching keys.")
        for key, value_dict in keys_to_process.items():
            if not isinstance(key, str) or key.count(":") != 1 or key.count("_") < 1:
                logger.warning(f"[direct_high_gain] Skipping malformed key '{key}' during load for {account_key}.")
                skipped_count += 1
                continue
            if not isinstance(value_dict, dict) or 'timestamp' not in value_dict:
                logger.warning(f"[direct_high_gain] Skipping key '{key}' for {account_key}: invalid value format.")
                skipped_count += 1
                continue
            loaded_ts_str = value_dict.get('timestamp')
            loaded_dt = safe_datetime(loaded_ts_str)
            if loaded_dt is None:
                logger.warning(f"[direct_high_gain] Skipping key '{key}' for {account_key} due to invalid timestamp.")
                skipped_count += 1
                continue
            restore_entry = value_dict.copy()
            restore_entry['timestamp'] = loaded_dt
            if key not in self.direct_high_gain_augmented:
                self.direct_high_gain_augmented[key] = restore_entry
                added_count += 1
                loaded_count += 1

    def _get_reversal_file_path(self, account_key: str) -> Path:
        """Gets the Path object for the reversed positions file for a specific account."""
        base_dir = Path(self.config.BASE_PATH) / account_key
        filepath = base_dir / "reversed_positions.json"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        return filepath

    async def save_reversed_positions(self, account_key: str, min_interval: int = 5, force: bool = False):
        """Saves the state of reversed positions for a given account."""
        if account_key != "fin" or not self.config.REV_MODE:
            return
        now = datetime.now(timezone.utc)
        last_save = self._last_reversal_save_time.get(account_key)
        filepath = self._get_reversal_file_path(account_key)
        file_age_seconds = await self._file_age_seconds(filepath)
        force_due_to_stale = force or (file_age_seconds is None or file_age_seconds >= max(60.0, self._persist_force_interval))
        if last_save and (now - last_save) < timedelta(seconds=min_interval) and not force_due_to_stale:
            return
        data_to_save = { k: v.strftime('%Y-%m-%dT%H:%M:%S.%fZ') for k, v in self.reversed_positions.items() if k.startswith(f"{account_key}:") and isinstance(v, datetime) }
        if getattr(self.config, 'VERBOSE_FETCH_LOGGING', getattr(self.config, 'VERBOSE', False)):
            logger.debug(f"[save_reversed_positions] {account_key}: memory_data={len(self.reversed_positions)} entries, data_to_save={len(data_to_save)} entries: {list(data_to_save.keys())[:10]}")
        try :
            await atomic_write_json(filepath, data_to_save)
            self._last_reversal_save_time[account_key] = now
            await self._broadcast_reversed_positions_to_redis(account_key, data_to_save)
            logger.debug(f"[reversal_system] Saved {len(data_to_save)} reversed position entries for '{account_key}'.")
        except Exception as e:
            logger.error(f"[reversal_system] Save failed for '{account_key}': {e}", exc_info=True)

    async def load_reversed_positions(self, account_key: str):
        """Loads the state of reversed positions for a given account."""
        if account_key != "fin" or not self.config.REV_MODE: return
        filepath = self._get_reversal_file_path(account_key)
        loaded_data = await load_json_safe(str(filepath))
        if not isinstance(loaded_data, dict):
            logger.error(f"[reversal_system] Unexpected data format in {filepath}. Skipping load.")
            return
        keys_to_remove = []
        for key, value_str in loaded_data.items():
            if not key.startswith(account_key):
                continue
            existing_position = self.positions.get(key)
            if not existing_position or existing_position.positionAmt <= 0:
                keys_to_remove.append(key)
                logger.warning(f"[reversal_system] Removing orphaned reversed position entry: {key} (position no longer exists)")
                continue
            loaded_dt = safe_datetime(value_str)
            if loaded_dt:
                self.reversed_positions[key] = loaded_dt
        for key in keys_to_remove:
            del loaded_data[key]
        if keys_to_remove:
            await atomic_write_json(str(filepath), loaded_data)

    async def reversed_position_sync(self):
        for account_key in self.accounts.keys():
            if account_key != "fin" or not self.config.REV_MODE:
                continue
            if account_key not in self.positions_by_account:
                continue
            positions_by_symbol = defaultdict(list)
            for pos_key, position in self.positions_by_account[account_key].items():
                if position and position.positionAmt > 0:
                    positions_by_symbol[position.symbol].append(position)
            made_changes = False
            positions_to_remove = []
            for reversed_key in list(self.reversed_positions.keys()):
                if reversed_key.startswith(f"{account_key}:"):
                    account, symbol, side = parse_position_key(reversed_key)
                    if account == account_key and symbol in positions_by_symbol:
                        pos_list = positions_by_symbol[symbol]
                        if len(pos_list) == 2:
                            long_pos = next((p for p in pos_list if p.position_side == 'LONG'), None)
                            short_pos = next((p for p in pos_list if p.position_side == 'SHORT'), None)
                            if long_pos and short_pos:
                                larger_amt = max(long_pos.positionAmt, short_pos.positionAmt)
                                smaller_amt = min(long_pos.positionAmt, short_pos.positionAmt)
                                if smaller_amt < 0.6 * larger_amt: 
                                    positions_to_remove.append(reversed_key)
                                    logger.info(f"[{account_key}:{symbol}] REMOVING reversal tracking - positions no longer within 20% (LONG: {long_pos.positionAmt:.4f}, SHORT: {short_pos.positionAmt:.4f})")
                                    made_changes = True
                        else:
                            positions_to_remove.append(reversed_key)
                            logger.info(f"[{account_key}:{symbol}] REMOVING reversal tracking - one position no longer exists")
                            made_changes = True
            for reversed_key in positions_to_remove:
                del self.reversed_positions[reversed_key]
            for symbol, pos_list in positions_by_symbol.items():
                if len(pos_list) == 2: 
                    long_pos = next((p for p in pos_list if p.position_side == 'LONG'), None)
                    short_pos = next((p for p in pos_list if p.position_side == 'SHORT'), None)
                    if long_pos and short_pos: 
                        larger_amt = max(long_pos.positionAmt, short_pos.positionAmt)
                        smaller_amt = min(long_pos.positionAmt, short_pos.positionAmt)
                        if smaller_amt < 0.9 * larger_amt: 
                            continue
                        long_mod_time = get_most_recent_timestamp(long_pos.opened_at, long_pos.last_augmentation_time)
                        short_mod_time = get_most_recent_timestamp(short_pos.opened_at, short_pos.last_augmentation_time)
                        reversed_pos, original_pos = ( (short_pos, long_pos) if (long_mod_time > short_mod_time or long_pos.positionAmt > short_pos.positionAmt) else (long_pos, short_pos) )
                        original_key = construct_position_key(account_key, symbol, original_pos.position_side)
                        reversed_key = construct_position_key(account_key, symbol, reversed_pos.position_side)
                        if reversed_key not in self.reversed_positions:
                            logger.warning( f"[{account_key}:{symbol}] DISCOVERED untracked reversal. Original: {original_key}." )
                            self.reversed_positions[reversed_key] = get_most_recent_timestamp( reversed_pos.opened_at, reversed_pos.last_augmentation_time )
                            made_changes = True
            if made_changes:
                await self.save_reversed_positions(account_key)

    async def load_managed_stops(self) -> None:
        try :
            if self.managed_stop_registry_path.exists():
                raw = self.managed_stop_registry_path.read_text()
                data = json.loads(raw) if raw.strip() else {}
                if isinstance(data, dict):
                    self.managed_stop_registry.clear(); self.managed_stop_registry.update(data)
        except Exception as exc:
            logger.error(f"[managed_stops] Failed to load registry: {exc}")
            self.managed_stop_registry.clear()

    async def get_reentry_file(self, account_key: str, position_side: str) -> str:
        assert position_side in ['LONG', 'SHORT'], f"Invalid position_side: {position_side}"
        base_dir = os.path.join(self.config.BASE_PATH, account_key)
        await aio_os.makedirs(base_dir, exist_ok=True)
        filename = f"{position_side.lower()}_reentry.json"
        file_path = os.path.join(base_dir, filename)
        if not await aio_os.path.exists(file_path):
            try :
                await atomic_write_json(file_path, {}) 
            except Exception as e:
                logger.error(f"Error ensuring file existence: {file_path}. Error: {e}")
                raise
        return Path(file_path)

    async def save_reentry_data(self, position_key: str, force: bool = False):
        try :
            if not position_key or not isinstance(position_key, str):
                logger.error(f"[save_reentry_data] Invalid position_key: {position_key}")
                return
            position_key = clean_position_key(position_key)
            account_key, _, side = parse_position_key(position_key)
            debounce_key = f"{account_key}:{side}"
            now_dt = datetime.now(timezone.utc)
            entry = self.reentry_data.get(position_key)
            if isinstance(entry, dict):
                sanitized = { "reentry_level": float(entry.get("reentry_level", 0.0)), "reentry_amount": float(entry.get("reentry_amount", 0.0)), "timestamp": str(entry.get("timestamp", now_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ'))), "reason": str(entry.get("reason", "")), }
                self.reentry_data[position_key] = sanitized
            else:
                self.reentry_data.pop(position_key, None)
            self._pending_reentry_accounts.add(account_key)
            now_ts = time.time()
            should_flush = force or (now_ts - self._last_reentry_flush_ts >= 60.0)
            self.last_reentry_save_time[debounce_key] = now_dt
            self._last_reentry_save_time[debounce_key] = now_ts
            if should_flush and not self._reentry_flush_in_progress:
                asyncio.create_task(self._flush_reentry_levels())
        except ValueError as exc:
            logger.error(f"[save_reentry_data] Skipping save due to key parsing error: {exc}")
        except Exception as exc:
            logger.exception(f"[save_reentry_data] Failed to stage reentry level for '{position_key}': {exc}")

    async def _flush_reentry_levels(self) -> None:
        if self._reentry_flush_in_progress:
            return
        self._reentry_flush_in_progress = True
        try :
            await self.save_all_reentry_levels()
            self._last_reentry_flush_ts = time.time()
        except Exception as exc:
            logger.debug(f"[_flush_reentry_levels] flush failed: {exc}")
        finally:
            self._reentry_flush_in_progress = False

    async def save_all_reentry_levels(self, account_key: str = None, force: bool = False, min_interval: int = 60):
        if not hasattr(self, "_reentry_save_lock"):
            self._reentry_save_lock = DummyLock()
        async with self._reentry_save_lock:
            now_ts = time.time()
            if hasattr(self, "_last_reentry_flush_ts") and (now_ts - self._last_reentry_flush_ts) < 60.0:
                return
            if isinstance(account_key, str):
                accounts_to_process = [account_key]
                debounce_key = f"{account_key}:ALL"
            else:
                pending_accounts = list(self._pending_reentry_accounts)
                accounts_to_process = pending_accounts if pending_accounts else list(self.accounts.keys())
                debounce_key = "ALL"
            total_saved = 0
            total_updated = 0
            for acc_key in accounts_to_process:
                if not acc_key or not isinstance(acc_key, str):
                    logger.warning(f"[sa ve_all_reentry_levels] Invalid account_key: {acc_key}")
                    continue
                for position_side in ("LONG", "SHORT"):
                    try :
                        file_path = await self.get_reentry_file(acc_key, position_side)
                        existing_data = await load_json_safe(file_path)
                        if not isinstance(existing_data, dict):
                            logger.warning(f"[sa ve_all_reentry_levels] Loaded reentry data from {file_path} is not a dict ({type(existing_data).__name__}). Resetting.")
                            existing_data = {}
                        cleaned_existing = {}
                        for raw_key, entry_data in existing_data.items():
                            if not isinstance(entry_data, dict):
                                continue
                            cleaned_key = clean_position_key(str(raw_key))
                            try :
                                key_account, _, key_side = parse_position_key(cleaned_key)
                            except Exception:
                                continue
                            if key_account == acc_key and key_side == position_side:
                                cleaned_existing[cleaned_key] = entry_data
                        existing_data = cleaned_existing
                        memory_entries: Dict[str, Dict[str, Any]] = {}
                        for position_key, entry_data in self.reentry_data.items():
                            try :
                                key_account, _, key_side = parse_position_key(position_key)
                            except Exception as exc:
                                logger.warning(f"[sa ve_all_reentry_levels] Error parsing position_key '{position_key}': {exc}")
                                continue
                            if key_account == acc_key and key_side == position_side:
                                if isinstance(entry_data, dict) and "timestamp" in entry_data:
                                    memory_entries[position_key] = entry_data
                                else:
                                    logger.warning(f"[sa ve_all_reentry_levels] Skipping malformed entry for {position_key}: missing timestamp or not dict")
                        updated_count = 0
                        cleaned_keys_map = {}
                        for position_key, memory_entry in memory_entries.items():
                            try :
                                cleaned_key = clean_position_key(position_key)
                                save_entry = {"reentry_level": float(memory_entry.get("reentry_level", 0.0)), "reentry_amount": float(memory_entry.get("reentry_amount", 0.0)), "timestamp": str(memory_entry.get("timestamp", datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'))), "reason": str(memory_entry.get("reason", ""))}
                                if all(k in save_entry for k in ("reentry_level", "reentry_amount", "timestamp", "reason")):
                                    existing_data[cleaned_key] = save_entry
                                    if cleaned_key != position_key:
                                        cleaned_keys_map[position_key] = cleaned_key
                                    updated_count += 1
                                else:
                                    logger.warning(f"[sa ve_all_reentry_levels] Skipping {position_key}: incomplete entry")
                            except Exception as exc:
                                logger.warning(f"[sa ve_all_reentry_levels] Error processing {position_key}: {exc}")
                                continue
                        keys_to_remove = []
                        cleaned_count = 0
                        for key in list(existing_data.keys()):
                            entry_data = existing_data.get(key)
                            if not all(field in entry_data for field in ("reentry_level", "reentry_amount", "timestamp", "reason")) if isinstance(entry_data, dict) else True:
                                keys_to_remove.append(key)
                                cleaned_count += 1
                            elif isinstance(entry_data, dict):
                                cleaned_key = clean_position_key(key)
                                if cleaned_key != key:
                                    if cleaned_key not in existing_data:
                                        existing_data[cleaned_key] = entry_data
                                    keys_to_remove.append(key)
                                    cleaned_count += 1
                        for key in keys_to_remove:
                            existing_data.pop(key, None)
                        if getattr(self.config, 'VERBOSE_FETCH_LOGGING', getattr(self.config, 'VERBOSE', False)):
                            memory_count = sum(1 for k in self.reentry_data.keys() if k.startswith(f"{acc_key}:") and k.endswith(f"_{position_side}"))
                            logger.debug(f"[save_all_reentry_levels] {acc_key}:{position_side}: memory_data={memory_count} entries, existing_data={len(existing_data)} entries: {list(existing_data.keys())[:10]}")
                        try :
                            await atomic_write_json(file_path, existing_data)
                            await self._broadcast_reentry_levels_to_redis(acc_key, position_side, existing_data)
                            total_saved += 1
                            total_updated += updated_count
                            logger.debug(f"[sa ve_all_reentry_levels] Saved {acc_key}:{position_side} - updated {updated_count} entries, cleaned {cleaned_count}")
                        except Exception as exc:
                            logger.error(f"[sa ve_all_reentry_levels] Failed to save {file_path}: {exc}")
                    except Exception as exc:
                        logger.error(f"[sa ve_all_reentry_levels] Error processing {acc_key}:{position_side}: {exc}")
                        continue
                self._pending_reentry_accounts.discard(acc_key)
            if total_saved > 0:
                logger.debug(f"[sa ve_all_reentry_levels] Completed - saved {total_saved} files, updated {total_updated} entries total")
                self._last_reentry_flush_ts = time.time()

    async def _refresh_reentries_for_account(self, account_key: str) -> None:
        positions_dict = self.positions_by_account.get(account_key, {})
        if not positions_dict:
            return
        await asyncio.sleep(0) 
        updated = 0
        for idx, (position_key, position) in enumerate(list(positions_dict.items())):
            if idx % 10 == 0: await asyncio.sleep(0) 
            if not position:
                continue
            cleaned_key = clean_position_key(str(position_key))
            try :
                _, symbol, _ = parse_position_key(cleaned_key)
            except Exception:
                continue
            try :
                data = await get_reentry_data(symbol, account_key, position, self)
            except Exception as exc:
                logger.debug(f"[reentry] get_reentry_data failed for {cleaned_key}: {exc}")
                continue
            if not isinstance(data, dict):
                continue
            ts_value = data.get("timestamp")
            if isinstance(ts_value, datetime):
                ts_str = ts_value.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            elif ts_value:
                ts_str = str(ts_value)
            else:
                ts_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            new_rl = float(data.get("level", 0.0) or 0.0)
            new_ra = float(data.get("amount", 0.0) or 0.0)
            existing = self.reentry_data.get(cleaned_key, {})
            existing_rl = float(existing.get("reentry_level", 0) or 0) if isinstance(existing, dict) else 0
            if new_rl <= 0 and existing_rl > 0:
                continue
            entry = { "reentry_level": new_rl, "reentry_amount": new_ra, "timestamp": ts_str, "reason": str(data.get("reason", "")), }
            self.reentry_data[cleaned_key] = entry
            updated += 1
        prefix = f"{account_key}:"
        for key in list(self.reentry_data.keys()):
            if key.startswith(prefix) and key not in positions_dict:
                try :
                    position = self.positions.get(key)
                    if not position:
                        position = self.positions_by_account.get(account_key, {}).get(key)
                    if position and abs(float(position.positionAmt)) >= float(position.max_quantity):
                        self.reentry_data.pop(key, None)
                except Exception:
                    pass
        await asyncio.sleep(0) 
        try :
            self._pending_reentry_accounts.add(account_key)
            now_ts = time.time()
            if (now_ts - self._last_reentry_flush_ts) >= 60.0 and not self._reentry_flush_in_progress: asyncio.create_task(self._flush_reentry_levels())
        except Exception:
            pass
        if updated:
            logger.debug(f"[reentry] refreshed {updated} entries for {account_key}")

    async def _refresh_reentries_for_all_accounts(self) -> None:
        await asyncio.sleep(0) 
        for account_key in sorted(self.positions_by_account.keys()):
            await asyncio.sleep(0) 
            await self._refresh_reentries_for_account(account_key)

    async def _hourly_deteriorate_reentries(self) -> None:
        now = datetime.now(timezone.utc)
        for position_key, entry in list(self.reentry_data.items()):
            try :
                account_key, symbol, _ = parse_position_key(position_key)
            except Exception:
                continue
            position = self.positions.get(position_key)
            if not position:
                position = self.positions_by_account.get(account_key, {}).get(position_key)
            if not position: 
                continue
            if abs(float(position.positionAmt)) >= float(position.max_quantity):
                continue
            current_price = _safe_float(getattr(position, "mark_price", 0.0), 0.0)
            if current_price <= 0:
                try :
                    current_price, ts = await get_current_price(symbol) 
                except Exception:
                    current_price = 0.0
            if current_price <= 0:
                continue
            ts_value = safe_datetime(entry.get("timestamp"))
            if ts_value:
                minutes_out = (now - ts_value).total_seconds() / 60.0
                progress = min(1.0, max(0.0, minutes_out / 60.0))
            else:
                progress = 1.0
            try :
                self.deteriorate_reentry_amounts(position, progress, current_price)
            except Exception as exc:
                logger.debug(f"[reentry] deterioration failed for {position_key}: {exc}")

    async def _check_reentry_triggers(self):
        try :
            now = datetime.now(timezone.utc)
            if not self.trade_manager:
                await self._ensure_trade_manager()
            if not self.trade_manager:
                return
            for position_key, data in list(self.reentry_data.items()):
                try :
                    if position_key not in self.tradeable_keys:
                        return
                    reentry_level = float(data.get('reentry_level', 0))
                    reentry_amt = float(data.get('reentry_amount', 0))
                    if reentry_level <= 0 or reentry_amt <= 0: continue
                    account_key, symbol, side = parse_position_key(position_key)
                    current_price = await self.get_mark_price(symbol, allow_fallback=True)
                    if current_price <= 0: continue
                    is_long = (side == 'LONG')
                    triggered = False
                    if is_long and current_price > reentry_level:
                        triggered = True
                    elif not is_long and current_price < reentry_level:
                        triggered = True
                    if triggered:
                        i = ii(self, symbol)
                        k_3m = i.get('stoch_k_3m', 50)
                        d3 = i.get('stoch_d_3m', 50)
                        confirmed = (is_long and k_3m > d3) or (not is_long and k_3m < d3)
                        if confirmed:
                            logger.info(f"♻️ [REENTRY_TRIGGER] {position_key}: Price {current_price} crossed level {reentry_level}. Re-entering {reentry_amt}!")
                            unique_id = f"REENTRY_{int(time.time())}"
                            await self.trade_manager.execute_now( position_key, account_key, symbol, 0.0, 'BUY' if is_long else 'SELL', side, reentry_amt, current_price, unique_id, "WATCHED_LEVEL_REENTRY", False, "AUGMENT" )
                            del self.reentry_data[position_key]
                            await self.save_reentry_data(position_key, force=True)
                except Exception as e:
                    logger.error(f"Reentry check failed for {position_key}: {e}")
        except Exception as main_e:
            logger.error(f"Reentry loop error: {main_e}")

    async def _reentry_maintenance_loop(self) -> None:
        await asyncio.sleep(5.0)
        last_deterioration = datetime.now(timezone.utc)
        while not self._housekeeping_stop:
            try :
                await asyncio.sleep(0) 
                await self._refresh_reentries_for_all_accounts()
                await asyncio.sleep(0) 
                now = datetime.now(timezone.utc)
                if (now - last_deterioration).total_seconds() >= 60.0:
                    await self._hourly_deteriorate_reentries()
                    last_deterioration = now
                await self._check_reentry_triggers() 
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[reentry] maintenance loop error: {exc}")
            await asyncio.sleep(10.0)

    async def _weekly_symbol_cleanup_loop(self) -> None:
        """Weekly: fetch active Binance futures symbols, remove delisted from symbols.json, reentry/tracker/reduced files, then run add_new_symbols.py."""
        await asyncio.sleep(60.0)
        logger.info("[WEEKLY_SYMBOL_CLEANUP] Started — checks every hour, runs cleanup on Sundays 00:00-01:00 UTC")
        _last_cleanup_day = -1
        while not self._housekeeping_stop:
            try:
                now = datetime.now(timezone.utc)
                if now.weekday() == 6 and now.hour == 0 and _last_cleanup_day != now.timetuple().tm_yday:
                    _last_cleanup_day = now.timetuple().tm_yday
                    logger.critical("[WEEKLY_SYMBOL_CLEANUP] 🔄 Starting weekly delisted symbol cleanup")
                    await self._run_symbol_cleanup()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[WEEKLY_SYMBOL_CLEANUP] Loop error: {exc}")
            await asyncio.sleep(3600.0)

    async def _run_symbol_cleanup(self) -> None:
        """Fetch active Binance futures, remove delisted from symbols.json + all data files, run add_new_symbols.py."""
        try:
            import aiohttp
            active_symbols = set()
            async with aiohttp.ClientSession() as session:
                async with session.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for s in data.get("symbols", []):
                            if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING":
                                active_symbols.add(s["symbol"])
            if len(active_symbols) < 100:
                logger.error(f"[WEEKLY_SYMBOL_CLEANUP] Only {len(active_symbols)} active symbols from API — too few, aborting (API issue?)")
                return
            symbols_file = self.base_path / "symbols.json"
            async with aiofiles.open(symbols_file, "r") as f:
                current_symbols = json.loads(await f.read())
            if not isinstance(current_symbols, list):
                logger.error("[WEEKLY_SYMBOL_CLEANUP] symbols.json is not a list — aborting")
                return
            before_count = len(current_symbols)
            delisted = [s for s in current_symbols if s not in active_symbols]
            if not delisted:
                logger.info(f"[WEEKLY_SYMBOL_CLEANUP] All {before_count} symbols still active on Binance — no cleanup needed")
                return
            logger.critical(f"[WEEKLY_SYMBOL_CLEANUP] Found {len(delisted)} delisted symbols: {delisted[:20]}{'...' if len(delisted) > 20 else ''}")
            new_symbols = [s for s in current_symbols if s in active_symbols]
            shutil.copy(symbols_file, self.base_path / "backups" / f"before_weekly_cleanup_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}_symbols.json")
            async with aiofiles.open(symbols_file, "w") as f:
                await f.write(json.dumps(new_symbols, indent=2))
            logger.info(f"[WEEKLY_SYMBOL_CLEANUP] symbols.json: {before_count} → {len(new_symbols)} (removed {len(delisted)})")
            delisted_set = set(delisted)
            accounts = list(self.accounts.keys()) if self.accounts else ["ang", "inf", "flz", "men", "fin"]
            for acct in accounts:
                acct_dir = self.base_path / acct
                if not acct_dir.exists():
                    continue
                for fname in ["long_reentry.json", "short_reentry.json", "reduced_positions.json", "tracker.json"]:
                    fpath = acct_dir / fname
                    if not fpath.exists():
                        continue
                    try:
                        async with aiofiles.open(fpath, "r") as f:
                            d = json.loads(await f.read())
                        if not isinstance(d, dict):
                            continue
                        before = len(d)
                        cleaned = {}
                        for k, v in d.items():
                            sym = k.split(":")[-1].rsplit("_", 1)[0] if ":" in k else k.rsplit("_", 1)[0] if "_" in k else k
                            if sym not in delisted_set:
                                cleaned[k] = v
                        removed = before - len(cleaned)
                        if removed > 0:
                            async with aiofiles.open(fpath, "w") as f:
                                await f.write(json.dumps(cleaned, indent=2))
                            logger.info(f"[WEEKLY_SYMBOL_CLEANUP] {acct}/{fname}: removed {removed} delisted entries")
                    except Exception as e:
                        logger.error(f"[WEEKLY_SYMBOL_CLEANUP] Error cleaning {acct}/{fname}: {e}")
            logger.info("[WEEKLY_SYMBOL_CLEANUP] Running add_new_symbols.py to sync position files...")
            python_path = sys.executable
            proc = await asyncio.create_subprocess_exec(python_path, str(self.base_path / "add_new_symbols.py"), cwd=str(self.base_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode == 0:
                logger.critical(f"[WEEKLY_SYMBOL_CLEANUP] ✅ add_new_symbols.py completed successfully. Output: {stdout.decode()[:500]}")
            else:
                logger.error(f"[WEEKLY_SYMBOL_CLEANUP] add_new_symbols.py failed (rc={proc.returncode}): {stderr.decode()[:500]}")
            if self.reentry_data:
                stale_keys = [k for k in self.reentry_data if k.split(":")[-1].rsplit("_", 1)[0] in delisted_set]
                for k in stale_keys:
                    self.reentry_data.pop(k, None)
                if stale_keys:
                    logger.info(f"[WEEKLY_SYMBOL_CLEANUP] Purged {len(stale_keys)} delisted keys from in-memory reentry_data")
        except Exception as exc:
            logger.error(f"[WEEKLY_SYMBOL_CLEANUP] Cleanup failed: {exc}", exc_info=True)

    async def _reentry_guardian_loop(self) -> None:
        """GUARDIAN: Scans all sources every 60s to ensure no position ever loses its reentry level.
        Sources: reentry_data dict, *_reentry.json, reduced_positions dict, tracker.json, *_positions.json.bak, ladder files.
        Also writes last_reduction_price back to position object every cycle."""
        await asyncio.sleep(15.0)
        logger.info("[REENTRY_GUARDIAN] Started — scanning all sources every 60s")
        while not self._housekeeping_stop:
            try:
                recovered = 0
                for account_key in sorted(self.positions_by_account.keys()):
                    acc_positions = self.positions_by_account.get(account_key, {})
                    for pk, pos in list(acc_positions.items()):
                        if not pos:
                            continue
                        existing_rd = self.reentry_data.get(pk, {})
                        existing_rl = float(existing_rd.get('reentry_level', 0)) if isinstance(existing_rd, dict) else 0
                        pos_lrp = float(getattr(pos, 'last_reduction_price', 0) or 0)
                        if existing_rl > 0 and pos_lrp > 0:
                            continue
                        if existing_rl > 0 and pos_lrp <= 0:
                            pos.last_reduction_price = existing_rl
                            logger.info(f"[REENTRY_GUARDIAN] {pk}: Restored position.last_reduction_price={existing_rl:.6f} from reentry_data")
                            continue
                        best_rl = 0.0
                        best_ra = 0.0
                        best_source = ""
                        best_ts = ""
                        _, symbol, side_str = parse_position_key(pk)
                        side_lower = "long" if side_str == "LONG" else "short"
                        bp = self.config.BASE_PATH if hasattr(self.config, 'BASE_PATH') else Path(".")
                        sources_to_check = [bp / account_key / f"{side_lower}_reentry.json", bp / account_key / "tracker.json", bp / account_key / f"{side_lower}_positions.json", bp / account_key / f"{side_lower}_positions.json.bak", bp / account_key / f"{side_lower}_ladder.json", bp / account_key / f"{side_lower}_stop_levels.json", bp / account_key / "reduced_positions.json"]
                        _price_fields = ('reentry_level', 'last_reduction_price', 'exit_price', 'average_exit_price')
                        _amt_fields = ('reentry_amount', 'last_reduction_amount', 'positionAmt')
                        _ts_fields = ('timestamp', 'last_reduction_time', 'last_exit_timestamp', 'reduced_at', 'updated_at')
                        for src_path in sources_to_check:
                            try:
                                if not src_path.exists():
                                    continue
                                async with aiofiles.open(src_path, 'r') as f:
                                    raw = await f.read()
                                if not raw or len(raw) < 3:
                                    continue
                                src_data = json.loads(raw)
                                if isinstance(src_data, dict):
                                    entry = src_data.get(pk, {})
                                    if isinstance(entry, dict):
                                        rl = 0.0
                                        for _pf in _price_fields:
                                            _v = float(entry.get(_pf, 0) or 0)
                                            if _v > 0:
                                                rl = _v
                                                break
                                        ra = 0.0
                                        for _af in _amt_fields:
                                            _v = float(entry.get(_af, 0) or 0)
                                            if _v > 0:
                                                ra = _v
                                                break
                                        src_ts = ""
                                        for _tf in _ts_fields:
                                            _v = str(entry.get(_tf, "") or "")
                                            if _v and len(_v) > 10:
                                                src_ts = _v
                                                break
                                        if rl > 0 and (not best_ts or src_ts > best_ts):
                                            best_rl = rl
                                            best_ra = ra if ra > 0 else best_ra
                                            best_source = str(src_path.name)
                                            best_ts = src_ts
                            except Exception:
                                continue
                        if best_rl <= 0:
                            red_price = float(getattr(pos, 'last_reduction_price', 0) or 0)
                            red_amt = float(getattr(pos, 'last_reduction_amount', 0) or 0)
                            if red_price > 0:
                                best_rl = red_price
                                best_ra = red_amt if red_amt > 0 else abs(float(pos.positionAmt)) * 0.5
                                best_source = "position.last_reduction_price"
                        if best_rl <= 0:
                            pass
                        if best_rl > 0:
                            self.reentry_data[pk] = {"reentry_level": best_rl, "reentry_amount": best_ra, "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "reason": f"GUARDIAN_RECOVERED_from_{best_source}"}
                            pos.last_reduction_price = best_rl
                            if best_ra > 0: pos.last_reduction_amount = best_ra
                            recovered += 1
                            logger.warning(f"[REENTRY_GUARDIAN] {pk}: RECOVERED reentry_level={best_rl:.6f} amt={best_ra:.4f} from {best_source} → wrote to position object")
                if recovered > 0:
                    logger.info(f"[REENTRY_GUARDIAN] Cycle complete: recovered {recovered} missing reentry levels")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[REENTRY_GUARDIAN] Error: {exc}")
            await asyncio.sleep(60.0)

    async def save_all_ladder_levels(self, account_key: str = None):
        if self._save_all_ladder_levels_active:
            logger.debug("[save_all_ladder_levels] Already active, skipping duplicate run")
            return
        self._save_all_ladder_levels_active = True
        try :
            if not isinstance(self.ladder_levels, dict) or not self.ladder_levels:
                return
            configured_accounts = list(self.accounts.keys())
            memory_accounts = []
            for key in self.ladder_levels.keys():
                if isinstance(key, str) and ":" in key:
                    memory_accounts.append(key.split(":", 1)[0])
            accounts_to_process = [account_key] if isinstance(account_key, str) else list(dict.fromkeys(configured_accounts + memory_accounts))
            now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            for acc_key in accounts_to_process:
                if not isinstance(acc_key, str) or not acc_key:
                    continue
                prefix = f"{acc_key}:"
                for side in ("LONG", "SHORT"):
                    file_path = await self.get_ladder_file(acc_key, side)
                    existing_data = await load_json_safe(file_path)
                    if not isinstance(existing_data, dict):
                        existing_data = {}
                    updated_data = {}
                    for position_key, entry in self.ladder_levels.items():
                        if not isinstance(position_key, str) or not position_key.startswith(prefix):
                            continue
                        try :
                            _, _, entry_side = parse_position_key(position_key)
                        except Exception:
                            continue
                        if entry_side.upper() != side:
                            continue
                        if not isinstance(entry, dict):
                            continue
                        levels_payload = entry.get("levels") if isinstance(entry.get("levels"), list) else []
                        sanitized_levels = []
                        for item in levels_payload:
                            if not isinstance(item, dict):
                                continue
                            level_val = _safe_float(item.get("level"))
                            qty_val = _safe_float(item.get("quantity"))
                            if level_val <= 0 or qty_val <= 0:
                                continue
                            sanitized_levels.append({ "level": level_val, "quantity": qty_val, "created_at": str(item.get("created_at") or now_iso), "crossed": bool(item.get("crossed", False)), "stoch_reversed": bool(item.get("stoch_reversed", False)) })
                        if not sanitized_levels:
                            continue
                        updated_data[position_key] = { "levels": sanitized_levels, "created_at": str(entry.get("created_at") or sanitized_levels[0]["created_at"]), "is_long": side == "LONG", "symbol": str(entry.get("symbol", "")), "account_key": acc_key }
                    if existing_data and isinstance(existing_data, dict):
                        merged_data = dict(existing_data)
                        merged_data.update(updated_data)
                        updated_data = merged_data
                    should_write = updated_data != existing_data
                    file_age = await self._file_age_seconds(Path(file_path))
                    if not should_write and file_age is not None and file_age > 60.0:
                        logger.debug(f"[save_all_ladder_levels] Touching stale file {file_path} age={file_age:.1f}s")
                        should_write = True
                    if should_write:
                        if getattr(self.config, 'VERBOSE_FETCH_LOGGING', getattr(self.config, 'VERBOSE', False)):
                            memory_count = sum(1 for k in self.ladder_levels.keys() if k.startswith(f"{acc_key}:") and (k.endswith(f"_{side}") or k.endswith(f"_{side.lower()}")))
                            logger.debug(f"[save_all_ladder_levels] {acc_key}:{side}: memory_data={memory_count} entries, updated_data={len(updated_data)} entries: {list(updated_data.keys())[:5]}")
                        await atomic_write_json(file_path, updated_data)
                        await self._broadcast_ladder_levels_to_redis(acc_key, side, updated_data)
                        debounce_key = f"{acc_key}:{side}"
                        now_ts = time.time()
                        self.last_ladder_save_time[debounce_key] = datetime.now(timezone.utc)
                        self._last_ladder_save_time[debounce_key] = now_ts
        except Exception as exc:
            logger.exception(f"[save_all_ladder_levels] Failed: {exc}")
        finally:
            self._save_all_ladder_levels_active = False

    async def save_invalidation_levels(self, account_key: str):
        """Save invalidation levels to persistent storage."""
        try :
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
                return 
            file_path = f"data/invalidation_levels_{account_key}.json"
            await atomic_write_json(file_path, account_invalidation_levels)
        except Exception as e:
            logger.exception(f"[save_invalidation_levels] Failed to save invalidation levels for account '{account_key}': {e}")

    async def load_invalidation_levels(self, account_key: str):
        """Load invalidation levels from persistent storage."""
        try :
            if not account_key or not isinstance(account_key, str):
                logger.error(f"[load_invalidation_levels] Invalid account_key: {account_key}")
                return
            file_path = f"data/invalidation_levels_{account_key}.json"
            invalidation_data = await load_json_safe(file_path)
            if isinstance(invalidation_data, dict):
                loaded_count = 0
                for position_key, data in invalidation_data.items():
                    if isinstance(data, dict) and 'level' in data and 'timestamp' in data and 'position_side' in data:
                        try :
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
        try :
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

    async def save_graceful_exit_list(self) -> None:
        """Persist the graceful exit list to disk."""
        try :
            path = self.base_path / "graceful_exits.json"
            data = list(self.positions_to_gracefully_exit)
            async with aiofiles.open(str(path), "w") as f:
                await f.write(json.dumps(data))
        except Exception as e:
            self.logger.error(f"[save_graceful_exit_list] Failed: {e}")

    async def add_symbol_to_fin_list(self, symbol: str) -> bool:
        rel_path = getattr(self.config, 'SYMBOLS_FIN', None)
        path_obj = Path(rel_path)
        if not path_obj.is_absolute():
            path_obj = self.base_path / rel_path
        current_symbols = set()
        if await aio_os.path.exists(str(path_obj)):
            try :
                async with aiofiles.open(str(path_obj), "rb") as f:
                    content = await f.read()
                    if content:
                        data = json.loads(content)
                        if isinstance(data, list):
                            current_symbols = set(data)
                        elif isinstance(data, dict):
                            current_symbols = set(data.get("symbols", []))
            except Exception as e:
                self.logger.error(f"[fin] Error reading existing file: {e}")
        if symbol in current_symbols:
            return True
        current_symbols.add(symbol)
        sorted_list = sorted(list(current_symbols))
        try :
            async with aiofiles.open(str(path_obj), "w") as f:
                await f.write(json.dumps(sorted_list, indent=2))
            self.logger.info(f"[fin] Added {symbol} to symbols_fin.json (Total: {len(sorted_list)})")
        except Exception as e:
            self.logger.error(f"[fin] Failed to write file: {e}")

    async def _universe_maintenance_loop(self) -> None:
        self.logger.info("[UNIVERSE_MAINTENANCE] 🚀 Loop started - Waiting for positions to load before first cleanup...")
        try:
            await asyncio.wait_for(self._loading_complete_event.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            self.logger.warning("[UNIVERSE_MAINTENANCE] ⏳ Timed out waiting for positions to load, proceeding anyway")
        self.logger.info("[UNIVERSE_MAINTENANCE] Positions loaded, running initial cleanup...")
        try :
            await asyncio.wait_for(self.cleanup_positions(force=True), timeout=60.0)
        except asyncio.TimeoutError:
            self.logger.error("[UNIVERSE_MAINTENANCE] ⏳ Initial cleanup TIMED OUT")
        except Exception as e:
            self.logger.error(f"[UNIVERSE_MAINTENANCE] Initial cleanup failed: {e}", exc_info=True)
        while not self._housekeeping_stop:
            await asyncio.sleep(180.0)
            try :
                await asyncio.wait_for(self.cleanup_positions(force=True), timeout=60.0)
            except asyncio.TimeoutError:
                self.logger.warning("[UNIVERSE_MAINTENANCE] ⏳ Cleanup timed out (skipping)")
            except Exception as e:
                self.logger.error(f"[UNIVERSE_MAINTENANCE] Error: {e}", exc_info=True)

    def _robust_json_decode(self, content_bytes):
        """ Attempts to decode JSON even if corrupted, concatenated, or missing commas. """
        if not content_bytes: return None
        try :
            return orjson.loads(content_bytes) 
        except Exception:
            pass
        try :
            text = content_bytes.decode('utf-8', errors='ignore').strip()
        except Exception:
            return None
        if not text: return None
        try :
            obj, _ = json.JSONDecoder().raw_decode(text)
            return obj
        except Exception:
            pass
        try :
            text_fixed = re.sub(r'"\s+"', '", "', text)
            text_fixed = re.sub(r'}\s+{', '}, {', text_fixed)
            return orjson.loads(text_fixed) 
        except Exception:
            pass
        try :
            if text.startswith('['):
                last_idx = text.rfind(']')
                if last_idx > 0: return orjson.loads(text[:last_idx+1]) 
            elif text.startswith('{'):
                last_idx = text.rfind('}')
                if last_idx > 0: return orjson.loads(text[:last_idx+1]) 
        except Exception:
            pass
        return None

    async def load_symbols_files(self):
        return
        current_time = time.time()
        keys_loaded = False
        loaded_source = "None"

        async def _get_symbols(path):
            if not path: return set()
            p = Path(path)
            if not p.is_absolute(): p = Path(self.config.BASE_PATH) / path
            if not await aio_os.path.exists(p): return set()
            try :
                async with aiofiles.open(p, "rb") as f:
                    data = self._robust_json_decode(await f.read())
                if isinstance(data, dict): data = data.get('symbols', []) or data.get('pairs', [])
                return {str(s).strip().upper() for s in data} if isinstance(data, list) else set()
            except Exception: return set()
        self.symbols_ang_long = await _get_symbols(getattr(self.config, 'SYMBOLS_ANG_LONG', 'symbols_ang_long.json'))
        self.symbols_ang_short = await _get_symbols(getattr(self.config, 'SYMBOLS_ANG_SHORT', 'symbols_ang_short.json'))
        self.symbols_inf_long = await _get_symbols(getattr(self.config, 'SYMBOLS_INF_LONG', 'symbols_inf_long.json'))
        self.symbols_inf_short = await _get_symbols(getattr(self.config, 'SYMBOLS_INF_SHORT', 'symbols_inf_short.json'))
        self.symbols_flz = await _get_symbols(getattr(self.config, 'SYMBOLS_FLZ', 'symbols_flz.json'))
        self.symbols_men = await _get_symbols(getattr(self.config, 'SYMBOLS_MEN', 'symbols_men.json'))
        self.symbols_fin = await _get_symbols(getattr(self.config, 'SYMBOLS_FIN', 'symbols_fin.json'))
        if self.redis_manager:
            try :
                client = self.redis_manager.connections.get('local')
                if client:
                    raw = await client.get("tradeable_keys")
                    if raw:
                        if isinstance(raw, str): raw = raw.encode('utf-8')
                        keys_data = self._robust_json_decode(raw)
                        if isinstance(keys_data, list) and len(keys_data) > 5:
                            self.tradeable_keys = set(keys_data)
                            keys_loaded = True
                            loaded_source = "Redis"
            except Exception: pass
        json_path = Path(self.config.BASE_PATH) / "tradeable_keys.json"
        if not keys_loaded:
            try :
                if await aio_os.path.exists(json_path):
                    stat = await aio_os.stat(json_path)
                    age = current_time - stat.st_mtime
                    if age < 600: 
                        async with aiofiles.open(json_path, "rb") as f:
                            content = await f.read()
                            keys_data = self._robust_json_decode(content)
                            if isinstance(keys_data, list) and len(keys_data) > 5:
                                self.tradeable_keys = set(keys_data)
                                keys_loaded = True
                                loaded_source = "Global JSON"
            except Exception as e:
                logger.error(f"[SYMBOLS] Failed to load tradeable_keys.json: {e}")
        if not keys_loaded:
            try :
                # await self.trade_manager.load_leaderboards()
                aggregated_keys = set()
                accounts_checked = 0
                for acc_key in self.accounts.keys():
                    tracker_file = Path(self.config.BASE_PATH) / acc_key / "tracker.json"
                    if await aio_os.path.exists(tracker_file):
                        async with aiofiles.open(tracker_file, "rb") as f:
                            try :
                                t_data = self._robust_json_decode(await f.read())
                                if t_data:
                                    t_keys = t_data.get('tradeable_position_keys', [])
                                    if isinstance(t_keys, list):
                                        valid_acc_keys = {k for k in t_keys if k.startswith(f"{acc_key}:")}
                                        aggregated_keys.update(valid_acc_keys)
                                        accounts_checked += 1
                            except Exception: pass
                if len(aggregated_keys) > 5:
                    self.tradeable_keys = aggregated_keys
                    keys_loaded = True
                    loaded_source = f"Tracker Aggregation ({accounts_checked} accs)"
            except Exception as e:
                logger.error(f"[SYMBOLS] Tracker aggregation failed: {e}")

    def _sync_memory_master_symbols_blocking(self) -> tuple:
        """Synchronous worker for _sync_memory_with_master_symbols. Reads symbols.json
        + per-account/per-side positions JSON files from disk. MUST run in asyncio.to_thread —
        running on the event loop blocked process_account_update on the same loop and
        contributed to the 120s PAU stalls observed 2026-04-30.

        Returns (master_symbols:set, disk_positions_by_pk:dict[pk -> dict]) — caller
        applies the dict mutations to in-memory state on the event loop (cheap)."""
        try:
            symbols_file = self.base_path / "symbols.json"
            if not symbols_file.exists():
                return (None, {})
            with open(symbols_file, "r") as f:
                content = json.load(f)
            if isinstance(content, list):
                master_symbols = set([s.strip().upper() for s in content])
            elif isinstance(content, dict) and 'symbols' in content:
                master_symbols = set([s.strip().upper() for s in content['symbols']])
            else:
                return (None, {})
            disk_blobs = {}
            account_keys = [ac.prefix for ac in self.accounts.values()]
            for account_key in account_keys:
                for side in ("LONG", "SHORT"):
                    side_file = self.base_path / account_key / f"{side.lower()}_positions.json"
                    if not side_file.exists():
                        disk_blobs[(account_key, side)] = {}
                        continue
                    try:
                        with open(side_file, "r") as df:
                            disk_data = json.load(df)
                        if isinstance(disk_data, dict) and "positions" in disk_data:
                            disk_data = disk_data["positions"]
                        if not isinstance(disk_data, dict):
                            disk_data = {}
                    except Exception:
                        disk_data = {}
                    disk_blobs[(account_key, side)] = disk_data
            return (master_symbols, disk_blobs)
        except Exception as e:
            self.logger.error(f"[SYMBOLS] _sync_memory_master_symbols_blocking failed: {e}")
            return (None, {})

    async def _sync_memory_with_master_symbols(self):
        """Dynamically instantiates empty positions for any new symbols in symbols.json
        and removes keys not in symbols.json if their positionAmt is 0.0.

        2026-04-30: All disk I/O moved to asyncio.to_thread. Previously this read up to
        2 × N_accounts × N_symbols files synchronously on the event loop (~10 files for
        the symbols.json scan + 10 per-side blobs); on slow disk + busy loop it pegged
        the loop long enough that process_account_update tripped its 120s safety net.
        """
        try:
            master_symbols, disk_blobs = await asyncio.to_thread(self._sync_memory_master_symbols_blocking)
            if master_symbols is None:
                return
            added_count = 0
            removed_count = 0
            for account_config in self.accounts.values():
                account_key = account_config.prefix
                if account_key not in self.positions_by_account:
                    self.positions_by_account[account_key] = {}
                for symbol in master_symbols:
                    for side in ["LONG", "SHORT"]:
                        pos_key = f"{account_key}:{symbol}_{side}"
                        if pos_key in self.positions_by_account[account_key]:
                            continue
                        if pos_key in self.positions:
                            existing = self.positions[pos_key]
                            self.positions_by_account[account_key][pos_key] = existing
                            continue
                        disk_pos = None
                        disk_data = disk_blobs.get((account_key, side), {})
                        pos_blob = disk_data.get(pos_key)
                        if isinstance(pos_blob, dict):
                            try:
                                disk_pos = Position.from_dict(pos_blob)
                                logger.debug(f"[_sync_memory] Loaded {pos_key} from disk (amt={disk_pos.positionAmt})")
                            except Exception:
                                disk_pos = None
                        if not disk_pos:
                            disk_pos = Position.from_dict({"symbol": symbol, "position_side": side})
                        self.positions_by_account[account_key][pos_key] = disk_pos
                        self.positions[pos_key] = disk_pos
                        added_count += 1
                keys_to_remove = []
                for pos_key, pos_obj in list(self.positions_by_account[account_key].items()):
                    if pos_obj.symbol not in master_symbols and abs(float(pos_obj.positionAmt)) == 0.0:
                        keys_to_remove.append(pos_key)
                for k in keys_to_remove:
                    self.positions_by_account[account_key].pop(k, None)
                    self.positions.pop(k, None)
                    removed_count += 1
            if added_count > 0 or removed_count > 0:
                self.logger.info(f"[SYMBOLS] Memory Sync: Added {added_count} new positions, Removed {removed_count} obsolete positions.")
        except Exception as e:
            self.logger.error(f"[SYMBOLS] Error in _sync_memory_with_master_symbols: {e}")

    async def cleanup_positions(self, force: bool = False):
        await self._sync_memory_with_master_symbols()
        import json
        persistence_file = self.base_path / "universe_persistence.json"
        tradeable_file = self.base_path / "tradeable_keys.json"
        now_ts = time.time()
        config_persist_hours = float(getattr(self.config, 'PERSIST', 96.0))
        retention_seconds = config_persist_hours * 3600.0
        short_term_seconds = 150.0 * 60.0

        async def load_set_from_file(path):
            """Load symbols set DIRECTLY from file path — always fresh read, never cached."""
            try:
                if not isinstance(path, Path): path = Path(path)
                if not path.is_absolute(): path = self.base_path / path
                if not path.exists(): return set()
                async with aiofiles.open(str(path), 'rb') as f:
                    content = await f.read()
                    data = orjson.loads(content)
                    if isinstance(data, dict):
                        data = data.get('symbols', []) or data.get('pairs', [])
                    if isinstance(data, list):
                        result_set = set()
                        for item in data:
                            if isinstance(item, (str, int)):
                                result_set.add(str(item).strip().upper())
                            elif isinstance(item, dict):
                                sym = item.get('symbol')
                                if sym:
                                    result_set.add(str(sym).strip().upper())
                        return result_set
            except Exception as e:
                self.logger.error(f"Error loading {path}: {e}")
                return set()
            return set()

        async def load_set(attr):
            rel = getattr(self.config, attr, None)
            if not rel: return set()
            return await load_set_from_file(rel)
        ang_l, ang_s = await asyncio.gather(load_set('SYMBOLS_ANG_LONG'), load_set('SYMBOLS_ANG_SHORT'))
        inf_l, inf_s = await asyncio.gather(load_set('SYMBOLS_INF_LONG'), load_set('SYMBOLS_INF_SHORT'))
        men, fin, flz = await asyncio.gather(load_set('SYMBOLS_MEN'), load_set('SYMBOLS_FIN'), load_set('SYMBOLS_FLZ'))
        w20, l20 = await asyncio.gather(load_set('WINNERS_20_FILE'), load_set('LOSERS_20_FILE'))
        w15, l15 = await asyncio.gather(load_set('WINNERS_15M_FILE'), load_set('LOSERS_15M_FILE'))
        all_winners = w20.union(w15)
        all_losers = l20.union(l15)
        old_persistence = {}
        try :
            if persistence_file.exists():
                async with aiofiles.open(str(persistence_file), 'rb') as f:
                    old_persistence = orjson.loads(await f.read()) 
        except Exception: pass
        global_known_hedges_history = set()
        current_active_hedges = set()
        managed_accounts = list(self.accounts.keys()) 
        for acc in managed_accounts:
            try :
                t_path = self.base_path / acc / "tracker.json"
                if await aio_os.path.exists(t_path):
                    async with aiofiles.open(str(t_path), 'rb') as f:
                        t_data = orjson.loads(await f.read()) 
                        hedges_meta = t_data.get('hedges', {})
                        if isinstance(hedges_meta, dict): global_known_hedges_history.update(hedges_meta.keys())
                        exits = t_data.get('exit_candidates', {})
                        if isinstance(exits, dict):
                            for k, v in exits.items():
                                if v.get('is_hedge') is True or v.get('hedge_for'):
                                    global_known_hedges_history.add(k)
                        active_h_list = t_data.get('active_hedges', [])
                        if isinstance(active_h_list, list):
                            for h in active_h_list:
                                if isinstance(h, dict) and h.get('position_key'):
                                    current_active_hedges.add(h['position_key'])
                                    global_known_hedges_history.add(h['position_key'])
            except Exception: pass
        fresh_generated_persistence = {} 

        def add_key(account, symbol, side):
            key = f"{account}:{symbol}_{side}"
            fresh_generated_persistence[key] = now_ts 
        for s in ang_l: add_key('ang', s, 'LONG')
        for s in ang_s: add_key('ang', s, 'SHORT')
        for s in inf_l: add_key('inf', s, 'LONG')
        for s in inf_s: add_key('inf', s, 'SHORT')
        for s in men:
            if s in all_winners: add_key('men', s, 'LONG')
            if s in all_losers: add_key('men', s, 'SHORT')
        for s in fin:
            if s in all_winners: add_key('fin', s, 'LONG')
            if s in all_losers: add_key('fin', s, 'SHORT')
        for s in flz:
            # Use w20/l20 directly (not combined all_winners/all_losers) — flz classification
            # is based on 20-period rankings only. Checks are independent (symbol can be in
            # both, in which case both LONG and SHORT are tradeable).
            _flz_winner = s in w20
            _flz_loser = s in l20
            if _flz_winner: add_key('flz', s, 'LONG')
            if _flz_loser: add_key('flz', s, 'SHORT')
            if not _flz_winner and not _flz_loser:
                add_key('flz', s, 'LONG')
                add_key('flz', s, 'SHORT')
        # Per-account persistence: ang=300 days, inf=4h, flz/men/fin=24h
        # inf extends for minutes-hours only; ang is long-term; all others default 24h.
        _persist_map = {
            'ang': float(getattr(self.config, 'PERSIST', 7200.0)) * 3600.0,
            'inf': float(getattr(self.config, 'PERSIST_INF', 4.0)) * 3600.0,
            'flz': float(getattr(self.config, 'PERSIST_FLZ', 24.0)) * 3600.0,
            'men': float(getattr(self.config, 'PERSIST_MEN', 24.0)) * 3600.0,
            'fin': float(getattr(self.config, 'PERSIST_FIN', 24.0)) * 3600.0,
        }
        if any(v > 0.1 for v in _persist_map.values()):
            for old_k, old_ts in old_persistence.items():
                if old_k in fresh_generated_persistence: continue
                if old_k in global_known_hedges_history: continue
                _old_acc = old_k.split(':')[0] if ':' in old_k else ''
                _retain_s = _persist_map.get(_old_acc, 0.0)
                if _retain_s > 0.1:
                    if now_ts - old_ts < _retain_s:
                        fresh_generated_persistence[old_k] = old_ts
        final_local_persistence = {} 
        final_keys_set = set()
        stats = {acc: {'cfg': 0, 'pst': 0, 'pos': 0} for acc in managed_accounts}
        for account_key in managed_accounts:
            account_prefix = f"{account_key}:"
            account_universe = {k: v for k, v in fresh_generated_persistence.items() if k.startswith(account_prefix)}
            positions = self.positions_by_account.get(account_key, {})
            valid_keys_for_this_account = set()
            for k, ts in account_universe.items():
                age = now_ts - ts
                is_short_term = False
                try :
                    sym = k.split(':')[1].replace('_LONG', '').replace('_SHORT', '')
                    is_short_term = (sym in w15 or sym in l15) and (sym not in w20 and sym not in l20)
                except Exception: pass
                limit = short_term_seconds if is_short_term else retention_seconds
                if age < limit or limit < 1.0: 
                    final_local_persistence[k] = ts
                    valid_keys_for_this_account.add(k)
                    if age < 60: stats[account_key]['cfg'] += 1
                    else: stats[account_key]['pst'] += 1
            _master_symbols = set()
            try:
                _master_symbols = set(json.load(open(self.base_path / "symbols.json")))
            except Exception:
                pass
            for k, v in positions.items():
                if not k.startswith(account_prefix): continue
                amt = abs(float(getattr(v, 'positionAmt', 0.0)))
                if amt > 0:
                    # 2026-04-17 USER RULE: "Open positions are tradeable_keys by default."
                    # Symbols.json guard REMOVED — any position with amt>0 MUST be tradeable so it
                    # can be closed / hedged. Previously skipped positions would bleed unhedged.
                    _sym = getattr(v, 'symbol', '')
                    if k not in valid_keys_for_this_account: stats[account_key]['pos'] += 1
                    valid_keys_for_this_account.add(k)  # Always tradeable (must be closeable)
                    _is_hedge_key = k in global_known_hedges_history or k in current_active_hedges
                    if not _is_hedge_key and k in fresh_generated_persistence:
                        final_local_persistence[k] = now_ts  # Only persist original (non-hedge) keys from symbols files
            for h_key in current_active_hedges:
                if h_key.startswith(account_prefix):
                    valid_keys_for_this_account.add(h_key)  # Tradeable while open — but NEVER persisted

            # --- HEDGE PAIR CLEANUP ---
            # If both LONG and SHORT exist for the same symbol, it's a hedge pair.
            # Remove BOTH keys. Then re-add: winner → LONG only, loser → SHORT only.
            symbols_in_account = {}
            for k in list(valid_keys_for_this_account):
                try:
                    _parts = k.split(':')[1] if ':' in k else k
                    if '_LONG' in _parts:
                        _sym = _parts.replace('_LONG', '')
                        symbols_in_account.setdefault(_sym, {})['LONG'] = k
                    elif '_SHORT' in _parts:
                        _sym = _parts.replace('_SHORT', '')
                        symbols_in_account.setdefault(_sym, {})['SHORT'] = k
                except Exception:
                    pass

            # HEDGE PAIR CLEANUP — only for ang/inf/men/fin (accounts that use winners/losers)
            # flz symbols are intentionally added with BOTH sides — do NOT clean them up
            _hedge_cleanup_accounts = {'ang', 'inf', 'men', 'fin'}
            if account_key in _hedge_cleanup_accounts:
                for _sym, sides in symbols_in_account.items():
                    if 'LONG' in sides and 'SHORT' in sides:
                        long_key = sides['LONG']
                        short_key = sides['SHORT']
                        long_pos = positions.get(long_key)
                        short_pos = positions.get(short_key)
                        long_open = long_pos and abs(float(getattr(long_pos, 'positionAmt', 0.0))) > 0
                        short_open = short_pos and abs(float(getattr(short_pos, 'positionAmt', 0.0))) > 0
                        if not long_open and not short_open:
                            valid_keys_for_this_account.discard(long_key)
                            valid_keys_for_this_account.discard(short_key)
                            final_local_persistence.pop(long_key, None)
                            final_local_persistence.pop(short_key, None)
                            if _sym in all_winners:
                                valid_keys_for_this_account.add(long_key)
                                final_local_persistence[long_key] = now_ts
                            elif _sym in all_losers:
                                valid_keys_for_this_account.add(short_key)
                                final_local_persistence[short_key] = now_ts
                            self.logger.info(f"[HEDGE_CLEANUP] {account_key}:{_sym} — both closed. Keep={'LONG' if _sym in all_winners else 'SHORT' if _sym in all_losers else 'NONE'}")
                        elif not long_open:
                            _long_is_hedge = long_key in global_known_hedges_history or long_key in current_active_hedges
                            _lk_ts = old_persistence.get(long_key, 0)
                            _inf_grace_s = float(getattr(self.config, 'PERSIST_INF', 4.0)) * 3600.0
                            if account_key == 'inf' and not _long_is_hedge and (now_ts - _lk_ts) < _inf_grace_s:
                                self.logger.info(f"[HEDGE_CLEANUP_GRACE] inf:{_sym} — LONG closed (original, not hedge), {(now_ts-_lk_ts)/3600:.1f}h < {_inf_grace_s/3600:.0f}h grace, keeping LONG for reentry")
                            else:
                                valid_keys_for_this_account.discard(long_key)
                                final_local_persistence.pop(long_key, None)
                                self.logger.info(f"[HEDGE_CLEANUP] {account_key}:{_sym} — LONG closed{'(hedge→instant discard)' if _long_is_hedge else ''}, SHORT still open")
                        elif not short_open:
                            _short_is_hedge = short_key in global_known_hedges_history or short_key in current_active_hedges
                            _sk_ts = old_persistence.get(short_key, 0)
                            _inf_grace_s = float(getattr(self.config, 'PERSIST_INF', 4.0)) * 3600.0
                            if account_key == 'inf' and not _short_is_hedge and (now_ts - _sk_ts) < _inf_grace_s:
                                self.logger.info(f"[HEDGE_CLEANUP_GRACE] inf:{_sym} — SHORT closed (original, not hedge), {(now_ts-_sk_ts)/3600:.1f}h < {_inf_grace_s/3600:.0f}h grace, keeping SHORT for reentry")
                            else:
                                valid_keys_for_this_account.discard(short_key)
                                final_local_persistence.pop(short_key, None)
                                self.logger.info(f"[HEDGE_CLEANUP] {account_key}:{_sym} — SHORT closed{'(hedge→instant discard)' if _short_is_hedge else ''}, LONG still open")

            final_keys_set.update(valid_keys_for_this_account)
        external_keys = set()
        try :
            if tradeable_file.exists():
                async with aiofiles.open(str(tradeable_file), 'rb') as f:
                    content = await f.read()
                    if content:
                        d = orjson.loads(content) 
                        if isinstance(d, list): 
                            for k in d:
                                k = str(k).strip()
                                parts = k.split(':')
                                if not parts: continue
                                acc = parts[0]
                                if acc not in managed_accounts:
                                    external_keys.add(k)
        except Exception as e:
            self.logger.error(f"[cleanup] Error reading old tradeable_keys: {e}")
        final_keys_set.update(external_keys)
        final_list = sorted(list(final_keys_set))
        if len(final_list) < 5 and len(external_keys) > 0:
             self.logger.warning(f"[cleanup] ⚠️ Generated list too small ({len(final_list)}). Aborting.")
             return
        try :
            final_persistence_to_save = {k: v for k, v in old_persistence.items() if k.split(':')[0] not in managed_accounts}
            final_persistence_to_save.update(final_local_persistence)
            async with aiofiles.open(str(persistence_file), 'w') as f:
                await f.write(orjson.dumps(final_persistence_to_save).decode('utf-8')) 
            json_str = json.dumps(final_list, indent=2) 
            temp_path = tradeable_file.with_suffix(".tmp")
            async with aiofiles.open(str(temp_path), 'w') as f:
                await f.write(json_str)
                await f.flush()
                await asyncio.to_thread(os.fsync, f.fileno())
            os.replace(temp_path, tradeable_file)
            log_msg = f"[cleanup] ✅ Synced {len(final_list)} keys (Persist: {config_persist_hours}h). "
            for acc, counts in stats.items():
                log_msg += f"[{acc}: Cfg = {counts['cfg']} Pst = {counts['pst']} Pos = {counts['pos']}] "
            if len(external_keys) > 0:
                log_msg += f"[External: {len(external_keys)}]"
            self.logger.info(log_msg)
        except Exception as e:
            self.logger.error(f"[cleanup] Save failed: {e}")

    async def get_ladder_file(self, account_key: str, position_side: str) -> str:
        """Get the file path for ladder levels storage."""
        assert position_side in ['LONG', 'SHORT'], f"Invalid position_side: {position_side}"
        base_dir = os.path.join(self.config.BASE_PATH, account_key)
        await aio_os.makedirs(base_dir, exist_ok=True)
        filename = f"{position_side.lower()}_ladder.json"
        file_path = os.path.join(base_dir, filename)
        if not await aio_os.path.exists(file_path):
            try :
                await atomic_write_json(file_path, {}) 
                logger.debug(f"Created empty ladder file: {file_path}")
            except Exception as e:
                logger.error(f"Failed to create empty ladder file {file_path}: {e}")
        return file_path

    def _normalize_ladder_entry(self, account_key: str, position_side: str, final_key: str, value: Any) -> tuple[dict[str, Any] | None, bool]:
        now = datetime.now(timezone.utc)
        ttl_minutes = getattr(config, "LADDER_TTL_MINUTES", 360)
        sanitized = False
        raw_meta: dict[str, Any] = {}
        raw_levels: list[Any] = []

        def _parse_dt(dt_val: Any) -> datetime | None:
            if isinstance(dt_val, datetime):
                return dt_val if dt_val.tzinfo else dt_val.replace(tzinfo=timezone.utc)
            if isinstance(dt_val, str) and dt_val:
                try :
                    parsed = pd.to_datetime(dt_val, utc=True).to_pydatetime()
                    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                except Exception:
                    return None
            return None

        def _coerce_entry(entry: Any, created_at_fallback: str) -> dict[str, Any] | None:
            nonlocal sanitized
            if isinstance(entry, dict):
                level = entry.get("level")
                qty = entry.get("quantity")
                if level is None or qty is None:
                    return None
                try :
                    level_f = float(level)
                    qty_f = float(qty)
                except (TypeError, ValueError):
                    return None
                created_at = entry.get("created_at") or created_at_fallback
                created_dt = _parse_dt(created_at)
                if not created_dt:
                    sanitized = True
                    created_dt = now
                return { "level": level_f, "quantity": qty_f, "created_at": created_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "crossed": bool(entry.get("crossed", False)), "stoch_reversed": bool(entry.get("stoch_reversed", False)) }
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                try :
                    level_f = float(entry[0])
                    qty_f = float(entry[1])
                except (TypeError, ValueError):
                    return None
                sanitized = True
                return { "level": level_f, "quantity": qty_f, "created_at": now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "crossed": False, "stoch_reversed": False }
            return None
        if isinstance(value, dict):
            raw_meta = dict(value)
            levels_field = raw_meta.get("levels")
            if isinstance(levels_field, dict):
                raw_levels = list(levels_field.values())
                sanitized = True
            elif isinstance(levels_field, list):
                raw_levels = levels_field
            else:
                candidates = []
                if {"level", "quantity"} <= set(raw_meta.keys()):
                    candidates.append({k: raw_meta.get(k) for k in ["level", "quantity", "created_at", "crossed", "stoch_reversed"]})
                candidates.extend(v for v in raw_meta.values() if isinstance(v, dict) and {"level", "quantity"} <= set(v.keys()))
                raw_levels = candidates
                if raw_levels:
                    sanitized = True
            raw_meta.pop("levels", None)
        elif isinstance(value, list):
            raw_levels = value
            sanitized = True
        else:
            return None, False
        if not raw_levels:
            return None, False
        account_part, symbol_part = final_key.split(":", 1)
        symbol_only, _, side_suffix = symbol_part.rpartition("_")
        created_at_dt = _parse_dt(raw_meta.get("created_at")) or now
        normalized_levels: list[dict[str, Any]] = []
        created_at_fallback = created_at_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        for entry in raw_levels:
            normalized = _coerce_entry(entry, created_at_fallback)
            if normalized:
                normalized_levels.append(normalized)
        if not normalized_levels:
            return None, False
        meta_out = { "levels": normalized_levels, "created_at": created_at_fallback, "is_long": side_suffix.upper() == "LONG", "symbol": symbol_only or symbol_part, "account_key": account_part or account_key }
        if raw_meta.get("is_long") is not None and bool(raw_meta.get("is_long")) != meta_out["is_long"]:
            sanitized = True
        if raw_meta.get("account_key") and raw_meta.get("account_key") != meta_out["account_key"]:
            sanitized = True
        return meta_out, sanitized

    async def save_ladder_levels(self, position_key: str):
        """Save ladder levels to persistent storage with debouncing."""
        try :
            if not position_key or not isinstance(position_key, str):
                logger.error(f"[save_ladder_levels] Invalid position_key: {position_key}")
                return
            account_key, symbol, side = parse_position_key(position_key)
            debounce_key = f"{account_key}:{side}"
            now_dt_debounce = datetime.now(timezone.utc)
            last_save = self.last_ladder_save_time.get(debounce_key)
            if last_save and (now_dt_debounce - last_save).total_seconds() < 60:
                logger.debug(f"[save_ladder_levels] Debounce active, skipping write for {debounce_key}")
                return
            file_path = await self.get_ladder_file(account_key, side)
            existing_data = await load_json_safe(file_path)
            if not isinstance(existing_data, dict):
                logger.error(f"Loaded ladder data from {file_path} is not a dict ({type(existing_data).__name__}). Resetting.")
                existing_data = {}
            current_entry = self.ladder_levels.get(position_key)
            updated_entry_in_file = False
            if current_entry and isinstance(current_entry, dict) and "created_at" in current_entry:
                try :
                    memory_ts_str = current_entry.get("created_at")
                    if memory_ts_str:
                        memory_ts = pd.to_datetime(memory_ts_str, utc=True).to_pydatetime()
                        if memory_ts.tzinfo is None:
                            memory_ts = memory_ts.replace(tzinfo=timezone.utc)
                        sanitized_levels = []
                        for level in current_entry.get("levels", []):
                            if not isinstance(level, dict):
                                continue
                            try :
                                level_val = float(level.get("level", 0.0))
                                qty_val = float(level.get("quantity", 0.0))
                                if math.isnan(level_val) or math.isinf(level_val) or level_val <= 0:
                                    logger.warning(f"[save_ladder_levels] Skipping invalid ladder level: {level_val} for {position_key}")
                                    continue
                                if math.isnan(qty_val) or math.isinf(qty_val) or qty_val <= 0:
                                    logger.warning(f"[save_ladder_levels] Skipping invalid ladder quantity: {qty_val} for {position_key}")
                                    continue
                                sanitized_levels.append({ "level": level_val, "quantity": qty_val, "created_at": str(level.get("created_at", memory_ts_str)), "crossed": bool(level.get("crossed", False)), "stoch_reversed": bool(level.get("stoch_reversed", False)), })
                            except (TypeError, ValueError) as e:
                                logger.warning(f"[save_ladder_levels] Skipping malformed level entry for {position_key}: {e}")
                                continue
                        if not sanitized_levels:
                            logger.warning(f"[save_ladder_levels] No valid levels after sanitization for {position_key}, skipping save")
                            return
                        sanitized_record = { "levels": sanitized_levels, "created_at": str(current_entry.get("created_at", memory_ts_str)), "is_long": bool(current_entry.get("is_long", False)), "symbol": str(current_entry.get("symbol", "")), "account_key": str(current_entry.get("account_key", account_key)), }
                        existing_data[position_key] = sanitized_record
                        updated_entry_in_file = True
                        logger.debug(f"[save_ladder_levels] Updated/Added entry for {position_key} in save data using memory timestamp {memory_ts_str}.")
                except (ValueError, TypeError) as e:
                    logger.warning(f"[save_ladder_levels] Error parsing memory timestamp '{current_entry.get('created_at')}' for {position_key}. Skipping update. Error: {e}")
                except Exception as e:
                    logger.exception(f"[save_ladder_levels] Unexpected error processing entry for {position_key}. Skipping update.")
            else:
                logger.warning(f"[save_ladder_levels] No valid entry found in memory for {position_key} to process.")
            if updated_entry_in_file:
                await atomic_write_json(file_path, existing_data)
            now_ts = time.time()
            self.last_ladder_save_time[debounce_key] = now_dt_debounce
            self._last_ladder_save_time[debounce_key] = now_ts
            await self._broadcast_ladder_levels_to_redis(account_key, side, existing_data)
            logger.debug(f"[save_ladder_levels] Successfully saved ladder data to {file_path}")
        except Exception as e:
            logger.exception(f"[save_ladder_levels] Error saving ladder levels for position '{position_key}': {e}")

    async def load_ladder_levels(self, account_key: str):
        try :
            if not account_key or not isinstance(account_key, str):
                logger.error(f"[load_ladder_levels] Invalid account_key: {account_key}")
                return
            loaded_count = 0
            sanitized_in_memory = False
            keys_to_resave: set[str] = set()
            for position_side in ['LONG', 'SHORT']:
                file_path = await self.get_ladder_file(account_key, position_side)
                ladder_data = await load_json_safe(file_path)
                if isinstance(ladder_data, dict):
                    entries_in_file = len(ladder_data)
                    side_loaded_count = 0
                    for key, value in ladder_data.items():
                        final_key = key
                        if final_key.count(":") == 2:
                            parts = final_key.split(":")
                            if len(parts) == 3:
                                final_key = construct_position_key(parts[0], parts[1], parts[2])
                                logger.debug(f"[load_ladder_levels] Converted key format: {key} -> {final_key}")
                        if not isinstance(key, str):
                            logger.warning(f"[load_ladder_levels] Skipping non-string key in {file_path}: {key}")
                            continue
                        if not key.startswith(f"{account_key}:"):
                            if ":" not in key and "_" in key:
                                final_key = f"{account_key}:{key}"
                                logger.warning(f"[load_ladder_levels] Sanitizing key in memory: {key} -> {final_key} from {file_path}")
                                sanitized_in_memory = True
                            else:
                                logger.warning(f"[load_ladder_levels] Skipping key '{key}' from {file_path}: Does not start with '{account_key}:'")
                                continue
                        if not (final_key.count(":") == 1 and final_key.count("_") >= 1 and final_key.endswith(f"_{position_side}")):
                            logger.warning(f"[load_ladder_levels] Skipping key '{final_key}' (from '{key}') from {file_path}: Mismatched format or side.")
                            continue
                        normalized_entry, entry_sanitized = self._normalize_ladder_entry(account_key, position_side, final_key, value)
                        if not normalized_entry:
                            logger.warning(f"[load_ladder_levels] Skipping key '{final_key}' from {file_path}: Invalid value format or missing levels ({type(value).__name__}).")
                            continue
                        if entry_sanitized:
                            sanitized_in_memory = True
                            keys_to_resave.add(final_key)
                        self.ladder_levels[final_key] = normalized_entry
                        loaded_count += 1
                        side_loaded_count += 1
            if sanitized_in_memory:
                logger.warning(f"[load_ladder_levels] Key sanitization occurred for {account_key}. Files will be corrected upon next save operation for the respective sides.")
            for key in keys_to_resave:
                try :
                    await self.save_ladder_levels(key)
                except Exception as save_exc:
                    logger.warning(f"[load_ladder_levels] Failed to persist sanitized ladder for {key}: {save_exc}")
        except Exception as e:
            logger.exception(f"[load_ladder_levels] Error loading ladder level data for account '{account_key}': {e}")

    async def refresh_all_stop_levels(self) -> Dict[str, int]:
        if not self.positions_live:
            return {}
        account_keys: Set[str] = set(self.positions_by_account.keys())
        account_keys.update(self.accounts.keys() if isinstance(self.accounts, dict) else [])
        results: Dict[str, int] = {}
        for account_key in sorted(account_keys):
            try :
                results[account_key] = await self.refresh_stop_levels_for_account(account_key)
            except Exception as exc:
                results[account_key] = 0
                logger.debug(f"[stop_levels] refresh_all failed for {account_key}: {exc}")
        return results

    async def refresh_stop_levels_for_account(self, account_key: str) -> int:
        account_positions = self.positions_by_account.get(account_key, {})
        if not account_positions:
            return 0
        api_seen = self._last_api_seen.get(account_key, set())
        updated = 0
        for position_key, position in list(account_positions.items()):
            if not position or _safe_float(getattr(position, "positionAmt", 0.0)) <= 0:
                self.stop_levels.pop(position_key, None)
                continue
            if api_seen and position_key not in api_seen:
                self.stop_levels.pop(position_key, None)
                continue
            try :
                await self.refresh_stop_levels_for_position(position_key, position=position)
                if position_key in self.stop_levels:
                    updated += 1
            except Exception as exc:
                logger.debug(f"[stop_levels] refresh failed for {position_key}: {exc}")
        return updated

    async def _save_auxiliary(self, account_key: str, force: bool = False, min_interval: int = 60) -> None:
        if not hasattr(self, "_last_auxiliary_save_time"):
            self._last_auxiliary_save_time: Dict[str, float] = {}
        now_ts = time.time()
        if not force and account_key in self._last_auxiliary_save_time:
            last_save_ts = self._last_auxiliary_save_time[account_key]
            if (now_ts - last_save_ts) < min_interval:
                return
        tasks: List[Awaitable[Any]] = []
        try :
            tasks.append(self.save_augmented_positions(account_key, force=True))
        except Exception:
            pass
        try :
            tasks.append(self.save_reduced_positions(account_key, force=True))
        except Exception:
            pass
        try :
            tasks.append(self.save_direct_high_gain_augmented(account_key, force=True))
        except Exception:
            pass
        try :
            tasks.append(self.save_reversed_positions(account_key, force=True))
        except Exception:
            pass
        try :
            await self.stop_manager.stop_levels_save(account_key, force=True)
        except Exception as exc:
            logger.debug(f"[_save_auxiliary] stop_levels_save error for {account_key}: {exc}")
        prefix = f"{account_key}:"
        for position_key in list(self.ladder_levels.keys()):
            if position_key.startswith(prefix):
                tasks.append(self.save_ladder_levels(position_key))
        for position_key in list(self.reentry_data.keys()):
            if position_key.startswith(prefix):
                tasks.append(self.save_reentry_data(position_key, force=True))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.debug(f"[_save_auxiliary] {account_key} save error: {result}")
        self._last_auxiliary_save_time[account_key] = now_ts

    async def _enforce_stop_reduction(self, position_key: str, account_key: str, symbol: str, position_side: str, positionAmt: float, price: float, pos_min_qty: float, reduction_qty: float) -> bool:
        if config.HEDGE_MODE:
            position_obj = self.positions.get(position_key) or self.positions_by_account.get(account_key, {}).get(position_key)
            if position_obj and hasattr(position_obj, 'gain') and position_obj.gain < 0.12:
                logger.warning(f"[HEDGE_MODE_BLOCK] {position_key}: Blocking STOP_ENFORCEMENT - position gain < 0.12% (gain={position_obj.gain:.2f}%) when HEDGE_MODE=True")
                return False
        if reduction_qty <= 0:
            return True
        is_long = position_side.upper() == "LONG"
        close_side = "SELL" if is_long else "BUY"
        reason = "REDUCE_STOP_LEVEL_ENFORCEMENT"
        unique_id = f"STOP_ENFORCE_{uuid.uuid4().hex[:8].upper()}"
        reduction_qty=min(reduction_qty, positionAmt-pos_min_qty)
        if current_env['env'] != 'server' and await safe_check_server_heartbeat(account_key):
            logger.warning(f"[SERVER_HEARTBEAT_BLOCK] {position_key}: Server instance is running for {account_key}, blocking stop enforcement from {current_env['env']}")
            return False
        position_obj = self.positions.get(position_key) or self.positions_by_account.get(account_key, {}).get(position_key)
        try :
            exec_result = await self.trade_manager.execute_now(position_key=position_key, account_key=account_key, symbol=symbol, original_positionAmt=positionAmt, side=close_side, position_side=position_side, quantity=reduction_qty, old_price=price, unique_id=unique_id, reason=reason, is_full_close=False, action="REDUCE")
            if exec_result and "SUCCESS" in exec_result:
                logger.critical(f"🚨🚨🚨 [STOP_ENFORCEMENT] {position_key}: Reduction via execute_now qty={reduction_qty:.6f} (pos={positionAmt:.6f} -> {pos_min_qty:.6f})")
                return True
        except Exception: pass
        return False

    async def refresh_stop_levels_for_position(self, position_key: str, *, position: Optional[Position] = None, current_price: Optional[float] = None, indicators: Optional[Dict[str, Any]] = None) -> None:
        position = position or self.positions.get(position_key)
        if not position:
            self.stop_levels.pop(position_key, None)
            return
        account_key, symbol, position_side = parse_position_key(position_key)
        price = current_price or _safe_float(getattr(position, "mark_price", 0.0))
        if price <= 0:
            price = await self.get_mark_price(symbol, allow_fallback=True)
        if price <= 0:
            return
        if config.HEDGE_MODE:
            return
        positionAmt = abs(_safe_float(getattr(position, "positionAmt", 0.0)))
        if positionAmt > 0:
            if not hasattr(self, "_last_enforced_positionAmt"):
                self._last_enforced_positionAmt = {}
            if not hasattr(self, "_last_enforced_check_time"):
                self._last_enforced_check_time = {}
            last_amt = self._last_enforced_positionAmt.get(position_key, positionAmt)
            last_check_time = self._last_enforced_check_time.get(position_key, 0.0)
            current_time = time.time()
            min_qty = self._get_min_qty(symbol)
            pos_min_qty = max(2 * self.config.MIN_POSITION_SIZE / price, min_qty * 1.2) if price > 0 else min_qty
            stop_hit = await self._was_stop_level_hit(position_key, account_key, symbol, position_side, price)
            enforce_cooldown = max(3.0, float(getattr(self.config, "STOP_ENFORCEMENT_MIN_INTERVAL", 3.0)))
            tolerance = max(positionAmt * 0.01, pos_min_qty * 0.25)
            should_enforce = stop_hit and positionAmt > 0 and (last_check_time == 0.0 or (current_time - last_check_time) >= enforce_cooldown or abs(positionAmt - last_amt) <= tolerance)
            if stop_hit and should_enforce:
                reduction_qty = max(0.0, positionAmt - pos_min_qty)
                if reduction_qty > 0:
                    logger.critical(f"🚨🚨🚨 [STOP_ENFORCEMENT] {position_key}: Stop level triggered. price={price:.6f} amt={positionAmt:.6f} min={pos_min_qty:.6f} last_amt={last_amt:.6f}")
                    enforcement_success = await self._enforce_stop_reduction(position_key, account_key, symbol, position_side, positionAmt, price, pos_min_qty, reduction_qty)
                    if enforcement_success:
                        self.reduced_in_monitor_reductions[position_key] = current_time
                else:
                    logger.warning(f"[STOP_ENFORCEMENT] {position_key}: Position already at minimum size ({positionAmt:.6f} <= {pos_min_qty:.6f}), skipping enforcement")
            self._last_enforced_positionAmt[position_key] = positionAmt
            self._last_enforced_check_time[position_key] = current_time
        try :
            levels = await self.stop_manager.manage( account_key=account_key, symbol=symbol, position_key=position_key, position_side=position_side, position=position, current_price=price, entry_price=position.entry_price, event="refresh", )
        except Exception as exc:
            logger.debug(f"[stop_levels] manage error for {position_key}: {exc}")
            return
        if levels:
            self.stop_levels[position_key] = levels
        else:
            self.stop_levels.pop(position_key, None)

    async def _ensure_indicators_bridge(self) -> None:
        if self.indicators_bridge and self.indicator_fetcher:
            return
        if self.indicator_fetcher and not self.indicators_bridge:
            return
        self.indicators_bridge = IndicatorsBridge(self, logger=self.logger, poll_interval=float(getattr(self.config, "MARKET_DATA_REFRESH_INTERVAL_SECONDS", 20.0)), cache_ttl=float(getattr(self.config, "INDICATOR_CACHE_TTL", 1.0)))
        asyncio.create_task(self.indicators_bridge.initialize()) 

        async def _bridge_fetch(symbol: str, *, force_refresh: bool = False, required_indicators: Optional[List[str]] = None):
            payload = await self.indicators_bridge.get(symbol, force_refresh=force_refresh, required_indicators=required_indicators)
            if payload:
                return payload
            return await fetch_fresh_snapshot_for_symbol(self, symbol, required_indicators=required_indicators, force_refresh=True, _skip_bridge=True)
        self.indicator_fetcher = _bridge_fetch

    async def _await_initial_indicator_snapshot(self, timeout: float = 45.0) -> None:
        _ = timeout
        symbols_needed: Set[str] = {getattr(pos, "symbol", "").upper() for pos in self.positions.values() if getattr(pos, "symbol", None)}
        if not symbols_needed:
            return

        async def _materialize_snapshot() -> Dict[str, Any]:
            snapshot = getattr(self, "indicators_snapshot", {}) or {}
            if snapshot:
                return snapshot
            raw_content = getattr(self, "raw_indicators_json_content", None)

            def _parse_raw(raw: Any) -> Dict[str, Any]:
                if not raw:
                    return {}
                try :
                    if isinstance(raw, (bytes, bytearray, memoryview)):
                        candidate = orjson.loads(raw) 
                    else:
                        candidate = orjson.loads(raw.encode() if isinstance(raw, str) else raw) 
                except Exception:
                    try :
                        text = raw.decode() if isinstance(raw, (bytes, bytearray, memoryview)) else str(raw)
                        candidate = json.loads(text)
                    except Exception:
                        return {}
                return candidate if isinstance(candidate, dict) else {}
            snapshot = _parse_raw(raw_content)
            if snapshot:
                return snapshot
            latest_path = Path(getattr(self.config, "LATEST_MARKET_DATA_FILE", self.config.DATA_DIR / "latest_market_data.json"))
            if await aio_os.path.exists(str(latest_path)):
                file_payload = await self._load_json_file_content(latest_path)
                if isinstance(file_payload, dict) and file_payload:
                    return file_payload
            if self.indicators_bridge and hasattr(self.indicators_bridge, "_refresh_from_source"):
                try :
                    await self.indicators_bridge._refresh_from_source(force=True)
                except Exception as exc:
                    logger.debug(f"[indicators] bridge refresh failed during startup: {exc}")
                snapshot = getattr(self, "indicators_snapshot", {}) or {}
            return snapshot
        try :
            await self._enforce_market_data_sync()
        except Exception as exc:
            logger.debug(f"[indicators] market data sync skipped during startup: {exc}")
        snapshot_payload = await _materialize_snapshot()
        if not snapshot_payload:
            logger.warning(f"[indicators] No startup snapshot available; continuing without cached indicators for {len(symbols_needed)} symbols")
            return
        self.indicators_snapshot = snapshot_payload
        if not getattr(self, "indicators_timestamp", None):
            ts_candidates: List[datetime] = []
            detected_ts = _snapshot_timestamp(snapshot_payload)
            if detected_ts:
                ts_candidates.append(ensure_tz(detected_ts))
            latest_path = Path(getattr(self.config, "LATEST_MARKET_DATA_FILE", self.config.DATA_DIR / "latest_market_data.json"))
            try :
                if await aio_os.path.exists(str(latest_path)):
                    stat_result = await aio_os.stat(str(latest_path))
                    ts_candidates.append(datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc))
            except Exception:
                pass
            self.indicators_timestamp = ts_candidates[0] if ts_candidates else datetime.now(timezone.utc)
        loaded_symbols = {sym.upper() for sym in snapshot_payload.keys()}
        missing = symbols_needed - loaded_symbols
        if missing:
            logger.debug(f"[indicators] Startup snapshot missing {len(missing)} of {len(symbols_needed)} symbols; continuing without waiting")
        logger.debug(f"[indicators] Startup snapshot primed for {len(loaded_symbols)} symbols")

    async def force_refresh_all_indicators(self) -> None:
        try :
            await self._ensure_indicators_bridge()
            bridge = getattr(self, "indicators_bridge", None)
            if bridge and hasattr(bridge, "_refresh_from_source"):
                self.indicators_bridge._refresh_from_source(force=True)
        except Exception as exc:
            logger.error("[positions_service] Indicator refresh failed: %s", exc, exc_info=True)

    async def _force_refresh_indicators_aggressive(self):
        """Aggressively refresh indicators from all available sources - used when stale data is detected"""
        FORCE_REFRESH_MAX_AGE_SECONDS = float(getattr(self.config, "FORCE_REFRESH_MAX_AGE_SECONDS", 240.0))
        MAX_FILE_AGE_SECONDS = float(getattr(self.config, "MAX_MARKET_DATA_FILE_AGE_SECONDS", 780.0))
        refreshed = False
        if self.redis_manager:
            try :
                redis_key = getattr(self.config, "REDIS_KEY_MARKET_DATA", "market_data:latest")
                raw_data = await self.redis_manager.get(redis_key)
                if raw_data:
                    data = json.loads(raw_data) if isinstance(raw_data, (str, bytes)) else raw_data
                    if isinstance(data, dict) and data:
                        should_refresh = True
                        btc_data = data.get("BTCUSDC", data.get("ETHUSDC", {}))
                        if isinstance(btc_data, dict):
                            payload = btc_data.get('complete', btc_data) if 'complete' in btc_data else btc_data
                            if isinstance(payload, dict):
                                ts_raw = payload.get("timestamp") or payload.get("time") or payload.get("timestamp_1m")
                                if ts_raw:
                                    try :
                                        divisor = 1000.0 if isinstance(ts_raw, (int, float)) and ts_raw > 1e12 else 1.0
                                        indicator_ts = datetime.fromtimestamp(float(ts_raw) / divisor, tz=timezone.utc)
                                        data_age = (datetime.now(timezone.utc) - indicator_ts).total_seconds()
                                        if data_age >= FORCE_REFRESH_MAX_AGE_SECONDS:
                                            logger.warning(f"⚠️ Redis data is {data_age:.0f}s old, trying file instead")
                                            should_refresh = False
                                    except Exception:
                                        pass
                        if should_refresh:
                            new_snapshot = {}
                            for symbol, indicators in data.items():
                                if isinstance(indicators, dict) and 'complete' not in indicators:
                                    new_snapshot[symbol] = indicators
                                elif isinstance(indicators, dict):
                                    new_snapshot[symbol] = indicators.get('complete', indicators)
                                else:
                                    new_snapshot[symbol] = indicators
                            self.indicators_snapshot = new_snapshot
                            if not hasattr(self, 'indicators_cache_timestamps'):
                                self.indicators_cache_timestamps = {}
                            self.indicators_cache_timestamps.clear(); self.indicators_cache_timestamps.update({symbol: time.time() for symbol in data.keys()})
                            self.indicators_timestamp = datetime.now(timezone.utc)
                            if hasattr(self, 'indicators_source_label'):
                                self.indicators_source_label = "redis_aggressive_refresh"
                            if hasattr(self, 'latest_indicator_payload_timestamp'):
                                self.latest_indicator_payload_timestamp = _snapshot_timestamp(data)
                            if hasattr(self, 'currently_loaded_indicator_source_path'):
                                self.currently_loaded_indicator_source_path = None
                            if hasattr(self, 'indicators_snapshot_refresh_time'):
                                self.indicators_snapshot_refresh_time = time.time()
                            logger.debug(f"[AGGRESSIVE_REFRESH] Refreshed from Redis with {len(data)} symbols")
                            refreshed = True
            except Exception as redis_err:
                logger.debug(f"[A GRESSIVE_REFRESH] Redis failed: {redis_err}")
        if not refreshed:
            try :
                import aiofiles
                latest_file = Path(getattr(self.config, "LATEST_MARKET_DATA_FILE", self.config.DATA_DIR / "latest_market_data.json"))
                if not latest_file.exists():
                    logger.error(f"[A GRESSIVE_REFRESH] No valid market data file found")
                    return refreshed
                file_mtime = latest_file.stat().st_mtime
                file_age = time.time() - file_mtime
                if file_age > MAX_FILE_AGE_SECONDS:
                    logger.error(f"🚫 [A GRESSIVE_REFRESH] REJECTED STALE FILE: {latest_file.name} (age={file_age:.0f}s > {MAX_FILE_AGE_SECONDS:.0f}s)")
                    return refreshed
                async with aiofiles.open(latest_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    if isinstance(data, dict) and data:
                        payload_ts = _snapshot_timestamp(data)
                        if payload_ts:
                            payload_ts = safe_datetime(payload_ts)
                            if payload_ts:
                                data_age = (datetime.now(timezone.utc) - payload_ts).total_seconds()
                                if data_age > MAX_FILE_AGE_SECONDS:
                                    logger.error(f"🚫 [A GRESSIVE_REFRESH] REJECTED STALE DATA: {latest_file.name} payload timestamp is {data_age:.0f}s old (> {MAX_FILE_AGE_SECONDS:.0f}s)")
                                    return refreshed
                        if not hasattr(self, 'indicators_snapshot'):
                            self.indicators_snapshot = {}
                        self.indicators_snapshot.clear()
                        for symbol, indicators in data.items():
                            self.indicators_snapshot[symbol] = indicators if isinstance(indicators, dict) and 'complete' not in indicators else {'complete': indicators} if not isinstance(indicators, dict) else indicators
                        if not hasattr(self, 'indicators_cache_timestamps'):
                            self.indicators_cache_timestamps = {}
                        self.indicators_cache_timestamps.clear(); self.indicators_cache_timestamps.update({symbol: time.time() for symbol in data.keys()})
                        self.indicators_timestamp = datetime.fromtimestamp(file_mtime, tz=timezone.utc)
                        if hasattr(self, 'indicators_source_label'):
                            self.indicators_source_label = latest_file.name
                        if hasattr(self, 'latest_indicator_payload_timestamp'):
                            self.latest_indicator_payload_timestamp = payload_ts
                        if hasattr(self, 'currently_loaded_indicator_source_path'):
                            self.currently_loaded_indicator_source_path = latest_file
                        if hasattr(self, 'indicators_snapshot_refresh_time'):
                            self.indicators_snapshot_refresh_time = time.time()
                        logger.debug(f"[A GRESSIVE_REFRESH] Refreshed from file {latest_file.name} with {len(data)} symbols")
                        refreshed = True
            except Exception as file_err:
                logger.warning(f"⚠️ [A GRESSIVE_REFRESH] File refresh failed: {file_err}")
        if not refreshed:
            logger.critical(f"🚨🚨🚨 [A GRESSIVE_REFRESH] All sources failed, trying bridge refresh")
            try :
                await self.force_refresh_all_indicators()
            except Exception as bridge_err:
                logger.critical(f"🚨🚨🚨 [A GRESSIVE_REFRESH] Bridge refresh failed: {bridge_err}")
        return refreshed

    async def get_indicators_for_symbol(self, symbol: str, force_refresh: bool = False) -> Dict[str, Any]:
        if not self.indicator_fetcher:
            return {}
        try :
            fetcher = self.indicator_fetcher
            try :
                snapshot = fetcher(symbol, force_refresh=force_refresh)
            except TypeError:
                snapshot = fetcher(symbol)
            if asyncio.iscoroutine(snapshot):
                snapshot = await snapshot
            return snapshot or {}
        except Exception as exc:
            logger.debug(f"[positions_service] indicator fetch failed for {symbol}: {exc}")
            return {}

    async def is_duplicate_order(self, position_key: str, quantity: float, side: str, unique_id: str) -> bool:
        async with self.dedupe_lock:
            key = f"{position_key}:{side}"
            if key in self.order_deduplication:
                last_time = self.order_deduplication[key]
                time_since = time.time() - last_time
                if time_since < 200: 
                    logger.warning(f"[DEDUPE] Duplicate order detected: {key} (last queued {time_since:.1f}s ago)")
                    return True
            self.order_deduplication[key] = time.time()
            return False

    async def mark_recent_signal(self, position_key: str, cooldown: float = 15.0): 
        try :
            if self.redis_manager:
                await self.redis_manager.set(f"recent_signal:{position_key}", "1", ex=max(1, int(cooldown)))
                return
        except Exception as e:
            logger.warning(f"Error setting redis recent signal for {position_key}: {e}")
        ts = time.time()
        self.recently_queued_signals[position_key] = ts
        self._schedule_recent_signal_expiry(position_key, ts, cooldown)

    async def track_active_maker_order(self, position_key: str, order_id: int, symbol: str, side: str, position_side: str):
        async with self.dedupe_lock:
            order_id_str = str(order_id)
            self.active_maker_orders[position_key] = { 'order_id': order_id, 'start_time': time.time(), 'status': 'active' }
            self.managed_maker_order_registry[order_id_str] = { 'position_key': position_key, 'symbol': symbol, 'side': side, 'position_side': position_side, 'order_id': order_id_str, 'timestamp': time.time() }

    async def clear_active_maker_order(self, position_key: str, order_id: int = None):
        """Clear active maker order tracking"""
        async with self.dedupe_lock:
            if position_key in self.active_maker_orders:
                tracked_order_id = str(self.active_maker_orders[position_key].get("order_id", ""))
                self.active_maker_orders.pop(position_key, None)
                if tracked_order_id in self.managed_maker_order_registry:
                    self.managed_maker_order_registry.pop(tracked_order_id, None)
            if order_id:
                order_id_str = str(order_id)
                if order_id_str in self.managed_maker_order_registry:
                    self.managed_maker_order_registry.pop(order_id_str, None)

    async def cancel_order_with_confirmation(self, position_key: str, order_id: int, expected_remaining_qty: float) -> bool:
        try :
            account_key, symbol, position_side = parse_position_key(position_key)
            client = self.accounts.get(account_key).client
            cancel_result = await asyncio.to_thread(client.futures_cancel_order, symbol=symbol, orderId=order_id)
            if cancel_result.get("status") not in {"CANCELED", "NEW"}:
                await asyncio.sleep(0.5)
                status = await asyncio.to_thread(client.futures_get_order, symbol=symbol, orderId=order_id)
                final = status.get("status")
                if final not in {"CANCELED", "FILLED", "EXPIRED", "REJECTED"}:
                    logger.warning(f"[CANCEL_CONFIRM] {symbol} order {order_id} status {final}")
                    return False
            if self.stop_manager:
                order_id_str = str(order_id)
                self.stop_manager._managed_stop_ids.discard(order_id_str)
                self.stop_manager._clear_stop(position_key, order_id_str)
                asyncio.create_task(self.stop_manager._flush_registry())
            return True
        except BinanceAPIException as e:
            if e.code in (-2011, -2013):
                return True
            logger.error(f"[CANCEL_CONFIRM] Binance error cancelling {symbol}:{order_id}: {e}")
            return False
        except Exception as exc:
            logger.error(f"[CANCEL_CONFIRM] Unexpected error cancelling {symbol}:{order_id}: {exc}")
            return False

    async def cancel_orders_for_position_within_price_band(self, account_key: str, symbol: str, position_side: str, center_price: float, pct: float = 0.0015, include_types: set[str] | None = {"STOP_MARKET"}, include_sides: set[str] | None = None) -> bool:
        account = self.accounts.get(account_key); 
        if not account or not getattr(account, "client", None): logger.error(f"[CANCEL_NEAR_PRICE] No client for {account_key}."); return False
        client = account.client; position_key = construct_position_key(account_key, symbol, position_side)
        try : center = float(center_price)
        except Exception: center = 0.0
        if center <= 0: logger.warning(f"[{position_key}] [CANCEL_NEAR_PRICE] Invalid center price. Skipping targeted cancel."); return True
        lower = center * (1.0 - float(pct)); upper = center * (1.0 + float(pct))
        try :
            if self.get_cached_open_orders:
                open_orders = await self.get_cached_open_orders(account_key, symbol)
            else:
                account = self.accounts.get(account_key) if hasattr(self, 'accounts') else None
                if account and hasattr(account, 'safe_api_call'):
                    open_orders = await account.safe_api_call(client.futures_get_open_orders, symbol=symbol)
                else:
                    open_orders = await asyncio.to_thread(client.futures_get_open_orders, symbol=symbol)
        except Exception as exc:
            logger.error(f"[CANCEL_NEAR_PRICE] Failed to fetch orders for {position_key}: {exc}")
            return False
        candidates = []
        for order in open_orders:
            if order.get("positionSide") != position_side: continue
            if include_types and order.get("type") not in include_types: continue
            if include_sides and order.get("side") not in include_sides: continue
            price = None
            for key in ("stopPrice", "price", "activatePrice"):
                val = order.get(key)
                if val and val not in ("0", 0):
                    try : price = float(val); break
                    except Exception: pass
            if price is None or price <= 0: continue
            if lower <= price <= upper: candidates.append(order)
        if not candidates: return True
        registry_checked = []
        for order in candidates:
            order_id = str(order.get("orderId") or "")
            order_type = order.get("type", "")
            is_in_registry = False
            if order_type == "STOP_MARKET":
                return
                if self.stop_manager:
                    if order_id in self.stop_manager._managed_stop_ids:
                        is_in_registry = True
                    else:
                        registry = getattr(self.stop_manager.service, "managed_stop_registry", {}) if getattr(self.stop_manager, "service", None) else {}
                        entry = registry.get(position_key)
                        if entry and str(entry.get("orderId") or "") == order_id:
                            is_in_registry = True
            elif order_type == "LIMIT":
                return
                order_id_str = str(order_id)
                if order_id_str in self.managed_maker_order_registry:
                    is_in_registry = True
            if is_in_registry:
                registry_checked.append(order)
        if not registry_checked: return True
        success = True
        for order in registry_checked:
            order_id = str(order.get("orderId") or "")
            try :
                await asyncio.to_thread(client.futures_cancel_order, symbol=symbol, orderId=order_id)
                if self.stop_manager and order.get("type") == "STOP_MARKET":
                    self.stop_manager._managed_stop_ids.discard(order_id)
                    self.stop_manager._clear_stop(position_key, order_id)
                elif order.get("type") == "LIMIT":
                    await self.clear_active_maker_order(position_key, int(order_id) if order_id.isdigit() else None)
            except Exception as exc:
                success = False
        if success and self.stop_manager: asyncio.create_task(self.stop_manager._flush_registry())
        return success

    async def wait_for_maker_orders_clear_and_verify_position(self, position_key: str, account_key: str, symbol: str, position_side: str, current_price: float, max_wait_seconds: float = 5.0, price_band_pct: float = 0.0015) -> tuple[bool, float]:
        """Wait for active maker orders to be cancelled/confirmed and verify position before allowing webhook. Returns (success, verified_positionAmt)."""
        try :
            orders_to_cancel = []
            async with self.dedupe_lock:
                if position_key in self.active_maker_orders:
                    active_order = self.active_maker_orders[position_key]
                    order_id = active_order.get('order_id')
                    if order_id:
                        orders_to_cancel.append(order_id)
                for order_id_str, order_info in list(self.managed_maker_order_registry.items()):
                    if order_info.get('position_key') == position_key:
                        try :
                            order_id = int(order_id_str)
                            orders_to_cancel.append(order_id)
                        except (ValueError, TypeError):
                            pass
            client = self._get_account_client(account_key)
            for order_id in orders_to_cancel:
                try :
                    logger.info(f"[WAIT_MAKER_CLEAR] {position_key}: Cancelling maker order {order_id}...")
                    cancelled = await self.cancel_order_with_confirmation(position_key, order_id, 0.0)
                    if cancelled:
                        await self.clear_active_maker_order(position_key, order_id)
                        logger.info(f"[WAIT_MAKER_CLEAR] {position_key}: Maker order {order_id} cancelled and confirmed")
                    await asyncio.sleep(0.2)
                except Exception as exc:
                    logger.warning(f"[WAIT_MAKER_CLEAR] {position_key}: Error cancelling order {order_id}: {exc}")
            cancelled_orders = await self.cancel_orders_for_position_within_price_band(account_key, symbol, position_side, current_price, price_band_pct, {"LIMIT"}, None)
            if cancelled_orders or orders_to_cancel:
                logger.info(f"[WAIT_MAKER_CLEAR] {position_key}: Cancelled orders, waiting for confirmation...")
                await asyncio.sleep(1.0)
            start_wait = time.time()
            while (time.time() - start_wait) < max_wait_seconds:
                async with self.dedupe_lock:
                    if position_key not in self.active_maker_orders:
                        has_registry_orders = any(info.get('position_key') == position_key for info in self.managed_maker_order_registry.values())
                        if not has_registry_orders:
                            break
                await asyncio.sleep(0.3)
            async with self.dedupe_lock:
                if position_key in self.active_maker_orders or any(info.get('position_key') == position_key for info in self.managed_maker_order_registry.values()):
                    logger.warning(f"[WAIT_MAKER_CLEAR] {position_key}: Active maker orders still exist after wait")
                    return False, 0.0
            await asyncio.sleep(0.5)
            if hasattr(self, "position_callback_manager"):
                ws_latest = await self.position_callback_manager.get_latest_position(position_key)
                if ws_latest and ws_latest.get('timestamp'):
                    ws_timestamp = ws_latest.get('timestamp')
                    if isinstance(ws_timestamp, str):
                        from dateutil.parser import isoparse
                        ws_timestamp = isoparse(ws_timestamp)
                    time_since_ws = (datetime.now(timezone.utc) - ws_timestamp).total_seconds()
                    if time_since_ws < 3.0:
                        verified_pos_amt = abs(float(ws_latest.get('positionAmt', 0)))
                        logger.info(f"[WAIT_MAKER_CLEAR] {position_key}: Position verified via WS: {verified_pos_amt:.6f}")
                        return True, verified_pos_amt
            position = self.positions.get(position_key)
            if position:
                verified_pos_amt = abs(float(getattr(position, 'positionAmt', 0)))
                logger.info(f"[WAIT_MAKER_CLEAR] {position_key}: Position verified via cache: {verified_pos_amt:.6f}")
                return True, verified_pos_amt
            return True, 0.0
        except Exception as exc:
            logger.error(f"[WAIT_MAKER_CLEAR] {position_key}: Error waiting for maker orders clear: {exc}")
            return False, 0.0

    async def clear_recent_signal(self, position_key):
        try :
            if self.redis_manager:
                await self.redis_manager.delete(f"recent_signal:{position_key}")
        except Exception as e:
            logger.warning(f"Error clearing redis recent signal for {position_key}: {e}")

    async def close_clients(self):
        for acct_key, acct in self.accounts.items():
            try :
                await acct.close()
                logger.info(f"Closed client for account '{acct_key}'")
            except Exception as e:
                logging.error(f"Error closing client for '{acct_key}': {e}")

    async def safe_clear_execution_lock(self, position_key: str, delay: float = 2.0):
        """Safely clear execution lock after a delay to ensure order completion"""
        await asyncio.sleep(delay)
        if self.redis_manager:
            try :
                for side in ["", ":BUY", ":SELL"]:
                    await self.redis_manager.delete(f"execute_now:{position_key}{side}")
                logger.info(f"[LOCK_CLEAR] Cleared execution locks for {position_key}")
            except Exception as e:
                logger.warning(f"[LOCK_CLEAR] Failed to clear locks for {position_key}: {e}")
        await self.clear_active_lock(position_key)
        await self.clear_recent_signal(position_key)

    def _schedule_recent_signal_expiry(self, position_key: str, stamp: float, cooldown: float):

        async def expire_signal():
            try :
                await asyncio.sleep(cooldown)
                if self.recently_queued_signals.get(position_key) == stamp:
                    self.recently_queued_signals.pop(position_key, None)
            except Exception:
                pass
        asyncio.create_task(expire_signal())

    def reset_force_retry(self, position_key: str):
        self.force_retry_counts.pop(position_key, None)

    async def schedule_force_retry(self, account_key: str, position_key: str, reason: str = "", max_retries: int = 3):
        count = self.force_retry_counts.get(position_key, 0) + 1
        self.force_retry_counts[position_key] = count
        is_master_stop_failure = "MASTER_STOP_FAILED" in reason
        if is_master_stop_failure:
            max_retries = 5 
            logger.warning(f"[{account_key}] MASTER_STOP_FAILED retry {count}/{max_retries} for {position_key} (reason: {reason})")
        else:
            logger.warning(f"[{account_key}] Force retry {count}/{max_retries} scheduled for {position_key} (reason: {reason})")
        if count > max_retries:
            if is_master_stop_failure:
                logger.error(f"[{account_key}] MASTER_STOP_FAILED retry limit exceeded for {position_key}. Last reason: {reason}. Position may need manual intervention.")
            else:
                logger.error(f"[{account_key}] Force retry limit exceeded for {position_key}. Last reason: {reason}")
            return

        async def _do_retry():
            try :
                if is_master_stop_failure:
                    backoff_delay = min(2 ** count, 30) 
                    logger.info(f"[{account_key}] MASTER_STOP_FAILED retry {count} waiting {backoff_delay}s before retry")
                    await asyncio.sleep(backoff_delay)
                else:
                    await asyncio.sleep(min(count, 3))
                position = self.positions.get(position_key)
                if not position:
                    account_positions = self.positions_by_account.get(account_key, {})
                    position = account_positions.get(position_key)
                parsed_account, parsed_symbol, parsed_side = parse_position_key(position_key)
                symbol = getattr(position, "symbol", "") or parsed_symbol or ""
                position_side = (getattr(position, "position_side", "") or parsed_side or "").upper()
                if not symbol or not position_side:
                    logger.error(f"[{account_key}] Force retry aborted for {position_key}: missing symbol/side")
                    return
                try : positionAmt = abs(float(getattr(position, "positionAmt", 0.0)))
                except (TypeError, ValueError): positionAmt = 0.0
                if positionAmt <= 0:
                    logger.warning(f"[{account_key}] Force retry skipped for {position_key}: zero position")
                    return
                try : price = float(getattr(position, "mark_price", 0.0))
                except (TypeError, ValueError): price = 0.0
                if price <= 0:
                    current_price, ts = await get_current_price (symbol) 
                min_qty_value = self._get_min_qty(symbol)
                pos_min_qty = max(3 * self.config.MIN_POSITION_SIZE / price if price > 0 else min_qty_value, min_qty_value)
                reduction_qty = max(0.0, positionAmt - pos_min_qty)
                if reduction_qty <= 0:
                    logger.info(f"[{account_key}] Force retry unnecessary for {position_key}: amt={positionAmt:.6f} min={pos_min_qty:.6f}")
                    return
                side = "SELL" if position_side == "LONG" else "BUY"
                unique_id = f"FORCE_RETRY_{uuid.uuid4().hex[:8].upper()}"
                reason_label = reason or "FORCE_RETRY"
                result = await self.trade_manager.execute_now(position_key=position_key, account_key=account_key, symbol=symbol, original_positionAmt=positionAmt, side=side, position_side=position_side, quantity=reduction_qty, old_price=price, unique_id=unique_id, reason=reason_label, is_full_close=False, action="REDUCE")
                logger.info(f"[{account_key}] Force retry result for {position_key}: {result}")
            except Exception as e:
                logger.error(f"[{account_key}] Force retry task failed for {position_key}: {e}", exc_info=True)
        asyncio.create_task(_do_retry())


    async def validate_position(self, position_key: str, account_key: str, original_positionAmt: float, order_queue, stage: str = "initial", threshold_pct: float = 5.0) -> tuple:
        try :
            stage_key = (stage or "initial").lower()
            position = self.get_position(position_key)
            if position and self._is_position_data_stale(position):
                position = self.get_position(position_key)
            current_amt_raw = abs(float(getattr(position, "positionAmt", 0.0))) if position else 0.0
            baseline_amt = abs(float(original_positionAmt or 0.0))
            symbol = getattr(position, "symbol", None) if position else None
            min_qty = self.min_qty.get(symbol, 0.0001) if symbol else 0.0001
            anchor = baseline_amt if baseline_amt > min_qty else max(current_amt_raw, min_qty)
            delta_pct = abs(current_amt_raw - baseline_amt) / anchor if anchor else 0.0
            executed_qty = abs(baseline_amt - current_amt_raw)
            effective_current = current_amt_raw if baseline_amt <= current_amt_raw else max(0.0, current_amt_raw - min_qty)
            if delta_pct > 0.40:
                logger.warning(f"[VALIDATION] {position_key}: {stage_key} discrepancy {delta_pct*100:.2f}% (baseline {baseline_amt:.6f}, actual {current_amt_raw:.6f})")
                return False, f"Discrepancy {delta_pct*100:.2f}%", current_amt_raw, executed_qty
            if stage_key == "initial": return True, "Initial validation", effective_current, executed_qty
            if stage_key in {"after_maker", "after_cancel", "after_maker_augment"}: return True, f"{stage_key} executed {executed_qty:.6f}", current_amt_raw, executed_qty
            if stage_key == "final" and baseline_amt > current_amt_raw and current_amt_raw > min_qty:
                logger.warning(f"[VALIDATION] {position_key}: final residual {current_amt_raw:.6f} above floor {min_qty:.6f}")
                return False, f"Residual {current_amt_raw:.6f}", current_amt_raw, executed_qty
            return True, f"Stage {stage_key} validated", current_amt_raw, executed_qty
        except Exception as e:
            logger.error(f"[VALIDATION] {position_key} error: {e}")
            return False, f"Validation error: {e}", 0.0, 0.0

    def _is_position_data_stale(self, position) -> bool:
        if not position or not hasattr(position, 'last_updated') or not position.last_updated: return True
        return (datetime.now(timezone.utc) - position.last_updated).total_seconds() > config.VALIDATE_REFRESH

    def _get_account_client(self, account_key: str):
        account = self.accounts.get(account_key) if isinstance(self.accounts, dict) else None
        if account is None:
            return None
        return getattr(account, "client", None)

    async def initialize(self, force: bool = False, enable_auto_fetch: bool = True) -> None:
        global _positions_loaded_once_global
        try :
            if self._initialized: return
            if config.PRICE_CACHE_FILE.exists():
                _pc = await load_json_safe(str(config.PRICE_CACHE_FILE))
                if isinstance(_pc, dict): self.price_cache.clear(); self.price_cache.update(_pc)
            if config.PRICE_CACHE_FILE_2.exists():
                _pc2 = await load_json_safe(str(config.PRICE_CACHE_FILE_2))
                if isinstance(_pc2, dict): self.price_cache_2.clear(); self.price_cache_2.update(_pc2)
            if config.PRICE_CACHE_FILE_3.exists():
                _pc3 = await load_json_safe(str(config.PRICE_CACHE_FILE_3))
                if isinstance(_pc3, dict): self.price_cache_3.clear(); self.price_cache_3.update(_pc3)
            if self.logger: self.logger.debug(f"[SERVICES] Loaded price caches directly: {len(self.price_cache)} prices")
        except Exception as e:
            if self.logger: self.logger.warning(f"[SERVICES] Price service bootstrap failed: {e}")
        try :
            indicators_svc = await asyncio.wait_for(bootstrap_indicators_service(), timeout=2.0)
            if indicators_svc and hasattr(indicators_svc, 'data'):
                self.latest_indicators_cache = indicators_svc.data
                if self.logger: self.logger.debug(f"[SERVICES] Connected to indicators service: {len(self.latest_indicators_cache)} symbols")
        except (asyncio.TimeoutError, Exception) as e:
            if self.logger: self.logger.warning(f"[SERVICES] Indicators service bootstrap skipped ({e})")
        total_in_memory = sum(len(acc_pos) for acc_pos in self.positions_by_account.values())
        if _positions_loaded_once_global:
            if total_in_memory > 0:
                logger.warning(f"[initialize] 🚫 Positions already loaded (global flag), skipping.")
                if not self._loading_complete_event.is_set():
                    self._loading_complete_event.set()
                self._initialized = True
                self._positions_loaded_once = True
                for acc in self.positions_by_account:
                    self._accounts_loaded_once.add(acc)
                return
        if self._initializing:
            logger.warning("[initialize] ⚠️ Already initializing - skipping duplicate call")
            return
        self._initializing = True
        try :
            if force or not self._initialized:
                logger.info("[initialize] Starting initialization...")
            self._enable_auto_fetch = enable_auto_fetch
            total_in_memory = sum(len(acc_pos) for acc_pos in self.positions_by_account.values())
            should_load = total_in_memory == 0 and (not _positions_loaded_once_global and not self._positions_loaded_once)
            if should_load:
                logger.info(f"[initialize] Loading positions from files (force={force})...")
                try :
                    await asyncio.wait_for(self.load_all(force=force, startup=True), timeout=30.0)
                    total_loaded = sum(len(acc_pos) for acc_pos in self.positions_by_account.values())
                    logger.info(f"[initialize] Loaded {total_loaded} positions from files")
                    if total_loaded > 0:
                        _positions_loaded_once_global = True
                        self._positions_loaded_once = True
                    else:
                        logger.error("[initialize] CRITICAL: ZERO positions loaded! Files may be empty or missing!")
                    self._loading_complete_event.set()
                except asyncio.TimeoutError:
                    logger.error("[initialize] CRITICAL: load_all() timed out after 30s! Data may be incomplete.")
                    self._loading_complete_event.set() 
                except Exception as e:
                    logger.error(f"[initialize] load_all() FAILED: {e}", exc_info=True)
                    self._loading_complete_event.set()
            else:
                logger.warning(f"[initialize] ⚠️ Memory already has {total_in_memory} positions - marking as loaded once, SKIPPING file reload")
                self._positions_loaded_once = True
                _positions_loaded_once_global = True
                if not self._loading_complete_event.is_set():
                    self._loading_complete_event.set()
            if enable_auto_fetch and self._should_enable_account_monitors():
                for account_key in self.accounts.keys():
                    self._last_positions_fetch[account_key] = 0.0
                async def _bg_api_fetch():
                    fetch_tasks = []
                    for account_key in self.accounts.keys():
                        fetch_tasks.append(self.fetch_positions(account_key))
                    if fetch_tasks:
                        try :
                            results = await asyncio.wait_for(asyncio.gather(*fetch_tasks, return_exceptions=True), timeout=30.0)
                            successful = sum(1 for r in results if isinstance(r, list) and len(r) > 0)
                            logger.info(f"[initialize] Background API fetch done: {successful}/{len(fetch_tasks)} accounts synced")
                        except asyncio.TimeoutError:
                            logger.warning(f"[initialize] Background API fetch timed out — disk data is current")
                        except Exception as e:
                            logger.warning(f"[initialize] Background API fetch error: {e}")
                asyncio.create_task(_bg_api_fetch())
                logger.info("[initialize] API position fetch launched in background — continuing startup")
            asyncio.create_task(self._refresh_usdc_pairs())
            asyncio.create_task(self.initialize_prev_gain_for_existing_positions())
            asyncio.create_task(self._ensure_indicators_bridge())
            asyncio.create_task(self._await_initial_indicator_snapshot())
            if config.HEDGE_MODE:
                asyncio.create_task(self._ensure_hedge_engine_initialized())
            if enable_auto_fetch and self._should_enable_account_monitors():
                logger.info("[initialize] 🚨 Creating st art_account_monitors task")

                async def _start_monitors_wrapper():
                    try :
                        logger.info("[initialize] 🚨 CALLING st art_account_monitors NOW")
                        await self.start_account_monitors()
                        logger.debug("[initialize] start_account_mo nitors COMPLETED")
                    except Exception as monitor_err:
                        logger.error(f"[initialize] CRITICAL: start_accou nt_monitors FAILED: {monitor_err}", exc_info=True)
                asyncio.create_task(_start_monitors_wrapper())
                logger.info("[initialize] task created")
            asyncio.create_task(self._refresh_reentries_for_all_accounts())
            asyncio.create_task(self.refresh_all_stop_levels())
            if enable_auto_fetch:
                asyncio.create_task(self.start_housekeeping_tasks())
            self._initialized = True
        except Exception as e:
            logger.error(f"[initialize] Initialization failed: {e}", exc_info=True)
            self._initialized = True
        finally:
            self._initializing = False

    async def _ensure_trade_manager(self) -> None:
        """Ensure MultiAccountTradeManager is initialized and attached to service."""
        if self.trade_manager:
            return
        try :
            logger.info("[PositionService] Lazy loading MultiAccountTradeManager...")
            TradeManagerClass = _get_trade_manager_class()
            if TradeManagerClass:
                try :
                    symbols = [] 
                    self.trade_manager = TradeManagerClass(accounts=self.accounts, symbols=symbols, positions_service=self)
                    logger.info("[PositionService] MultiAccountTradeManager instantiated locally")
                except Exception as e:
                     logger.warning(f"[PositionService] Could not instantiate MultiAccountTradeManager locally: {e}. Waiting for external assignment.")
            else:
                logger.error("[PositionService] MultiAccountTradeManager class not found in ez_manage")
        except Exception as e:
            logger.error(f"[PositionService] Error ensuring TradeManager: {e}", exc_info=True)

    async def _ensure_hedge_engine_initialized(self) -> None:
        """Ensure HedgeEngine is initialized if HEDGE_MODE is enabled and dependencies are available."""
        if self._hedge_engine_initialized or self.hedge_engine:
            return
        if not getattr(self.config, 'HEDGE_MODE', False):
            return
        try :
            quick_module = _get_hedge_engine_module()
            if not quick_module:
                logger.warning("[PositionService] ez_positions_quick module not found")
                return
            HedgeEngineClass = getattr(quick_module, 'HedgeEngine', None)
            TrackerManagerClass = getattr(quick_module, 'TrackerManager', None)
            FastDataManagerClass = getattr(quick_module, 'FastDataManager', None)
            if not HedgeEngineClass:
                logger.warning("[PositionService] HedgeEngine class not found")
                return
            await self._ensure_trade_manager()
            trade_manager = getattr(self, 'trade_manager', None)
            if not trade_manager:
                trade_manager = self 
            redis_manager = getattr(self, 'redis_manager', None)
            if not redis_manager:
                 try :
                     redis_manager = await asyncio.wait_for(get_simple_redis_manager(), timeout=0.5)
                     self.redis_manager = redis_manager
                 except (asyncio.TimeoutError, Exception): pass
            tracker_manager = getattr(self, 'tracker_manager', None)
            if not tracker_manager and TrackerManagerClass:
                try :
                    tracker_manager = TrackerManagerClass(self.base_path, trade_manager=trade_manager)
                    tracker_manager.accounts = self.accounts
                    self.tracker_manager = tracker_manager
                    logger.info("[PositionService] Local TrackerManager initialized")
                    for account_key in sorted(list(self.accounts.keys())):
                        try :
                            await tracker_manager.load_tracker(account_key)
                        except Exception: pass
                except Exception as e:
                    logger.warning(f"[PositionService] Failed to init local TrackerManager: {e}")
            data_manager = getattr(self, 'data_manager', None)
            if not data_manager and FastDataManagerClass:
                try :
                    data_path = self.base_path / 'data'
                    data_manager = FastDataManagerClass(redis_manager, data_path, trade_manager=trade_manager)
                    self.data_manager = data_manager
                    if tracker_manager:
                        tracker_manager.data_manager = data_manager
                    logger.info("[PositionService] Local FastDataManager initialized")
                except Exception as e:
                    logger.warning(f"[PositionService] Failed to init local FastDataManager: {e}")
            if tracker_manager and data_manager:
                hedge_engine = HedgeEngineClass( trade_manager=trade_manager, tracker_manager=tracker_manager, data_manager=data_manager, config=self.config, redis_manager=redis_manager )
                self.hedge_engine = hedge_engine
                if hasattr(tracker_manager, 'hedge_engine'):
                    tracker_manager.hedge_engine = hedge_engine
                if trade_manager and hasattr(trade_manager, 'hedge_engine'):
                    trade_manager.hedge_engine = hedge_engine
                logger.info("[PositionService] HedgeEngine initialized locally and linked.")
                logger.info("[PositionService] HedgeEngine initialized locally")
                for account_key in sorted(self.accounts.keys()):
                    try :
                        await hedge_engine.load_hedge_records(account_key)
                    except Exception: pass
                self._hedge_engine_initialized = True
            else:
                logger.warning(f"[PositionService] HedgeEngine missing dependencies: tracker={tracker_manager is not None}, data={data_manager is not None}")
        except Exception as e:
             logger.error(f"[PositionService] Error initializing HedgeEngine: {e}", exc_info=True)

    async def start_housekeeping_tasks(self) -> None:
        if self._housekeeping_started:
            logger.warning("[start_housekeeping_tasks] ⚠️ Housekeeping tasks already started, skipping duplicate call")
            return
        if not self._loading_complete_event.is_set():
            logger.warning("[start_housekeeping_tasks] ⏳ BLOCKING: Waiting for positions to load before starting housekeeping tasks...")
            try :
                await asyncio.wait_for(self._loading_complete_event.wait(), timeout=60.0)
                logger.info("[start_housekeeping_tasks] Positions loaded, starting housekeeping tasks")
            except asyncio.TimeoutError:
                logger.error("[start_housekeeping_tasks] ⚠️ Timeout waiting for loading, starting anyway")
        self._housekeeping_started = True
        self._housekeeping_stop = False
        self._position_flush_event = asyncio.Event()
        self._prev_gain_task = asyncio.create_task(self.update_prev_gain_periodically())
        self._pnl_deterioration_task = asyncio.create_task(self.update_pnl_deterioration_periodically())
        self._pnl_decay_task = asyncio.create_task(self._pnl_decay_loop())
        self._position_logging_task = asyncio.create_task(self._log_top_positions_loop())
        self._position_flush_task = asyncio.create_task(self._position_flush_loop())
        self._redis_refresh_task = asyncio.create_task(self._positions_redis_refresh_loop())
        self._data_sync_task = asyncio.create_task(self._data_sync_loop())
        self._ensure_reduction_tasks_started()
        self._monitor_guard_task = asyncio.create_task(self._monitor_reductions_watchdog())
        self._symbol_watchdog_task = asyncio.create_task(self._symbol_monitoring_watchdog())
        self._ladder_save_task = asyncio.create_task(self._ladder_save_loop())
        self._augmented_save_task = asyncio.create_task(self._augmented_positions_persist_loop())
        self._reduced_save_task = asyncio.create_task(self._reduced_positions_persist_loop())
        self._direct_high_gain_save_task = asyncio.create_task(self._direct_high_gain_augmented_persist_loop())
        if getattr(self.config, "REV_MODE", False):
            self._reversed_save_task = asyncio.create_task(self._reversed_positions_persist_loop())
        else:
            self._reversed_save_task = None
        self._auxiliary_snapshot_task = asyncio.create_task(self._auxiliary_snapshot_loop()) 
        self._reentry_maintenance_task = asyncio.create_task(self._reentry_maintenance_loop())
        self._reentry_guardian_task = asyncio.create_task(self._reentry_guardian_loop())
        self._positions_periodic_save_task = asyncio.create_task(self._positions_periodic_save_loop())
        # DEDUP_STEP2: mark price loop — authoritative: manage.mark_price_aggregate_loop (WS 1s writes to same positions dict)
        # self._mark_price_update_task = asyncio.create_task(self._update_mark_prices_loop())
        logger.info("[HOUSEKEEPING] tasks started (all loaded)")

    async def stop_housekeeping_tasks(self) -> None:
        self._housekeeping_stop = True
        for task in (self._prev_gain_task, self._pnl_decay_task, self._stop_levels_task, self._reentry_maintenance_task):
            if task and not task.done():
                task.cancel()
                try : await task
                except asyncio.CancelledError: pass
        self._prev_gain_task = None
        self._pnl_decay_task = None
        self._stop_levels_task = None
        self._reentry_maintenance_task = None
        if self._position_logging_task and not self._position_logging_task.done():
            self._position_logging_task.cancel()
            try : await self._position_logging_task
            except asyncio.CancelledError: pass
        self._position_logging_task = None
        if self._redis_refresh_task and not self._redis_refresh_task.done():
            self._redis_refresh_task.cancel()
            try : await self._redis_refresh_task
            except asyncio.CancelledError: pass
        self._redis_refresh_task = None
        if self._data_sync_task and not self._data_sync_task.done():
            self._data_sync_task.cancel()
            try : await self._data_sync_task
            except asyncio.CancelledError: pass
        self._data_sync_task = None
        if self._position_flush_task and not self._position_flush_task.done():
            self._position_flush_task.cancel()
            try : await self._position_flush_task
            except asyncio.CancelledError: pass
        self._position_flush_task = None
        _positions_periodic_save_task = getattr(self, '_positions_periodic_save_task', None)
        if _positions_periodic_save_task and not _positions_periodic_save_task.done():
            _positions_periodic_save_task.cancel()
            try : await _positions_periodic_save_task
            except asyncio.CancelledError: pass
        if hasattr(self, '_positions_periodic_save_task'):
            self._positions_periodic_save_task = None
        _ladder_save_task = getattr(self, '_ladder_save_task', None)
        if _ladder_save_task and not _ladder_save_task.done():
            _ladder_save_task.cancel()
            try : await _ladder_save_task
            except asyncio.CancelledError: pass
        if hasattr(self, '_ladder_save_task'):
            self._ladder_save_task = None
        _monitor_guard_task = getattr(self, '_monitor_guard_task', None)
        if _monitor_guard_task and not _monitor_guard_task.done():
            _monitor_guard_task.cancel()
            try : await _monitor_guard_task
            except asyncio.CancelledError: pass
        if hasattr(self, '_monitor_guard_task'):
            self._monitor_guard_task = None
        _symbol_watchdog_task = getattr(self, '_symbol_watchdog_task', None)
        if _symbol_watchdog_task and not _symbol_watchdog_task.done():
            _symbol_watchdog_task.cancel()
            try : await _symbol_watchdog_task
            except asyncio.CancelledError: pass
        if hasattr(self, '_symbol_watchdog_task'):
            self._symbol_watchdog_task = None
        _mark_price_update_task = getattr(self, '_mark_price_update_task', None)
        if _mark_price_update_task and not _mark_price_update_task.done():
            _mark_price_update_task.cancel()
            try : await _mark_price_update_task
            except asyncio.CancelledError: pass
        if hasattr(self, '_mark_price_update_task'):
            self._mark_price_update_task = None
        _auxiliary_snapshot_task = getattr(self, '_auxiliary_snapshot_task', None)
        if _auxiliary_snapshot_task and not _auxiliary_snapshot_task.done():
            _auxiliary_snapshot_task.cancel()
            try : await _auxiliary_snapshot_task
            except asyncio.CancelledError: pass
        if hasattr(self, '_auxiliary_snapshot_task'):
            self._auxiliary_snapshot_task = None
        for account_key, task in list(self._monitor_reduction_tasks.items()):
            if task and not task.done():
                task.cancel()
                try : await task
                except asyncio.CancelledError: pass
        if getattr(self, '_universe_maintenance_task', None) and not self._universe_maintenance_task.done():
            self._universe_maintenance_task.cancel()
            try : await self._universe_maintenance_task
            except asyncio.CancelledError: pass
        self._universe_maintenance_task = None
        self._monitor_reduction_tasks.clear()
        self._monitor_heartbeats.clear()
        self._monitor_reductions_started_accounts.clear()
        try :
            await self.save_all_ladder_levels()
        except Exception as exc:
            logger.debug(f"[stop_housekeeping_tasks] Final ladder save failed: {exc}")
        self._position_flush_event = None

    async def _augmented_positions_persist_loop(self) -> None:
        monitor_logger = self.logger or logger
        monitor_logger.info("[MONITOR_REDUCTIONS] initializing (10s delay)")
        await asyncio.sleep(10)
        save_interval = 60.0 
        while not self._housekeeping_stop:
            try :
                account_keys = set()
                if isinstance(self.accounts, dict): account_keys.update(self.accounts.keys())
                account_keys.update(self.positions_by_account.keys())
                for account_key in sorted(account_keys):
                    try :
                        await self.save_augmented_positions(account_key, force=True)
                    except Exception as exc:
                        monitor_logger.error(f"[augmented_positions_persist_loop] Failed to save {account_key}: {exc}", exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                monitor_logger.error(f"[augmented_positions_persist_loop] Loop error: {exc}", exc_info=True)
            await asyncio.sleep(save_interval)

    async def _reduced_positions_persist_loop(self) -> None:
        await asyncio.sleep(10)
        save_interval = 60.0 
        while not self._housekeeping_stop:
            try :
                account_keys = set()
                if isinstance(self.accounts, dict): account_keys.update(self.accounts.keys())
                account_keys.update(self.positions_by_account.keys())
                for account_key in sorted(account_keys):
                    try :
                        await self.save_reduced_positions(account_key, force=True)
                    except Exception as exc:
                        logger.error(f"[reduced_positions_persist_loop] Failed to save {account_key}: {exc}", exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[reduced_positions_persist_loop] Loop error: {exc}", exc_info=True)
            await asyncio.sleep(save_interval)

    async def _direct_high_gain_augmented_persist_loop(self) -> None: 
        monitor_logger = self.logger or logger
        monitor_logger.info("[direct_high_gain_augmented] persist loop initializing (10s delay)")
        await asyncio.sleep(10)
        save_interval = 60.0
        monitor_interval = 15.0
        reload_interval = 60.0
        last_reload = 0.0
        last_save = 0.0
        while not self._housekeeping_stop:
            try :
                now = time.time()
                now_dt = datetime.now(timezone.utc)
                account_keys = set()
                if isinstance(self.accounts, dict): account_keys.update(self.accounts.keys())
                account_keys.update(self.positions_by_account.keys())
                if now - last_reload >= reload_interval:
                    for account_key in sorted(account_keys):
                        try :
                            await self._load_direct_high_gain(account_key, force=True)
                        except Exception as exc:
                            monitor_logger.debug(f"[direct_high_gain_loop] Reload failed {account_key}: {exc}")
                    last_reload = now
                keys_to_remove = []
                redis_deletions_by_account = defaultdict(list)
                for position_key, entry in list(self.direct_high_gain_augmented.items()):
                    try :
                        account_key = position_key.split(':')[0]
                        if account_key == 'flz':continue
                    except Exception:
                        keys_to_remove.append(position_key)
                        continue
                    position = self.positions.get(position_key)
                    if not position:
                        position = self.positions_by_account.get(account_key, {}).get(position_key)
                    positionAmt = abs(safe_fetch_float(getattr(position, "positionAmt", 0.0), 0.0))
                    if positionAmt <= 0.0001:
                        keys_to_remove.append(position_key)
                        redis_deletions_by_account[account_key].append(position_key)
                        continue
                    ts = entry.get('timestamp')
                    if isinstance(ts, datetime):
                        age_seconds = (now_dt - ts).total_seconds()
                        if age_seconds >= 10800: 
                            gain = safe_fetch_float(getattr(position, 'gain', 0.0), 0.0)
                            if gain < 1.0: 
                                keys_to_remove.append(position_key)
                                redis_deletions_by_account[account_key].append(position_key)
                for k in keys_to_remove:
                    self.direct_high_gain_augmented.pop(k, None)
                client = await self._ensure_redis_client()
                if client:
                    for acc, keys in redis_deletions_by_account.items():
                        if keys:
                            try :
                                await client.hdel(f"direct_high_gain_augmented:{acc}", *keys)
                                monitor_logger.info(f"[DIRECT_GAIN_CLEAN] 🧹 Purged {len(keys)} zombies from Redis for {acc}")
                            except Exception as e:
                                monitor_logger.error(f"[DIRECT_GAIN_CLEAN] Redis delete failed: {e}")
                high_gain_min_size = getattr(self.config, 'HIGH_GAIN_AUGMENTATION_MIN_SIZE', 200.0)
                for account_key in sorted(account_keys):
                    account_positions = self.positions_by_account.get(account_key, {})
                    for position_key, position in account_positions.items():
                        if not position: continue
                        pos_amt = abs(safe_fetch_float(getattr(position, 'positionAmt', 0.0), 0.0))
                        mark_price = safe_fetch_float(getattr(position, 'mark_price', 0.0), 0.0)
                        if pos_amt > 0 and mark_price > 0:
                            val = pos_amt * mark_price
                            if val > high_gain_min_size and position_key not in self.direct_high_gain_augmented:
                                _, _, side = parse_position_key(position_key)
                                self.mark_direct_high_gain( position_key, timestamp=now_dt, entry_price=getattr(position, 'entry_price', mark_price), max_size=val, position_side=side )
                if now - last_save >= save_interval:
                    for account_key in sorted(account_keys):
                        await self.save_direct_high_gain_augmented(account_key, force=True)
                    last_save = now
            except asyncio.CancelledError:
                break
            except Exception as exc:
                monitor_logger.error(f"[direct_high_gain_augmented_persist_loop] Loop error: {exc}", exc_info=True)
            await asyncio.sleep(monitor_interval)

    async def _reversed_positions_persist_loop(self) -> None:
        await asyncio.sleep(10)
        if not getattr(self.config, "REV_MODE", False):
            return
        save_interval = 60.0 
        while not self._housekeeping_stop:
            try :
                account_keys = set()
                if isinstance(self.accounts, dict): account_keys.update({k for k in self.accounts.keys() if k == "fin"})
                account_keys.update({k for k in self.positions_by_account.keys() if k == "fin"})
                for account_key in sorted(account_keys):
                    try :
                        await self.save_reversed_positions(account_key, force=True)
                    except Exception as exc:
                        logger.error(f"[reversed_positions_persist_loop] Failed to save {account_key}: {exc}", exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[reversed_positions_persist_loop] Loop error: {exc}", exc_info=True)
            await asyncio.sleep(save_interval)

    async def _auxiliary_snapshot_loop(self) -> None:
        await asyncio.sleep(10)
        save_interval = 60.0 
        while not self._housekeeping_stop:
            try :
                account_keys: Set[str] = set()
                if isinstance(self.accounts, dict):
                    account_keys.update(self.accounts.keys())
                account_keys.update(self.positions_by_account.keys())
                pending_reentry = list(self._pending_reentry_accounts)
                for account_key in sorted(account_keys):
                    try :
                        await self._save_auxiliary(account_key, force=True)
                    except Exception as exc:
                        logger.debug(f"[_auxiliary_snapshot_loop] save_auxiliary error for {account_key}: {exc}")
                if pending_reentry:
                    try :
                        await self.save_all_reentry_levels(force=True)
                    except Exception as exc:
                        logger.debug(f"[_auxiliary_snapshot_loop] save_all_reentry_levels error: {exc}")
                await asyncio.sleep(save_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"[_auxiliary_snapshot_loop] loop error: {exc}")
                await asyncio.sleep(save_interval)

    async def _ensure_full_pk_coverage(self, account_key: str) -> None:
        acc_positions = self.positions_by_account.setdefault(account_key, {})
        try :
            symbols_file = Path(getattr(self.config, "SYMBOLS_FILE", self.config.BASE_PATH / "symbols.json"))
            if symbols_file.exists():
                content = await self._load_json(symbols_file)
                if isinstance(content, dict) and "symbols" in content:
                    master_symbols = content["symbols"]
                elif isinstance(content, list):
                    master_symbols = content
                else:
                    master_symbols = []
            else:
                master_symbols = []
        except Exception:
            master_symbols = []
        expected_symbols = set(str(s).strip().upper() for s in master_symbols)
        recovered_count = 0
        created_count = 0
        for side in ("LONG", "SHORT"):
            file_path = self.get_position_file(account_key, side)
            disk_snapshot = await self._load_json(file_path)
            if not isinstance(disk_snapshot, dict):
                disk_snapshot = {}
            disk_keys = set()
            for k in disk_snapshot.keys():
                if isinstance(k, str) and ':' in k and '_' in k:
                    if k.upper().endswith(f'_{side}'):
                        disk_keys.add(k)
            all_expected_keys = set(disk_keys)
            for sym in expected_symbols:
                try :
                    pk = construct_position_key(account_key, sym, side)
                    all_expected_keys.add(pk)
                except Exception: 
                    pass
            for pk in all_expected_keys:
                if pk in acc_positions and acc_positions[pk] is not None:
                    continue
                candidate_pos = None
                if pk in disk_snapshot:
                    payload = disk_snapshot[pk]
                    if isinstance(payload, dict):
                        try :
                            candidate_pos = Position.from_dict(payload)
                            recovered_count += 1
                        except Exception as e:
                            logger.error(f"[_ensure_full_pk_coverage] Failed to parse disk entry for {pk}: {e}")
                if not candidate_pos:
                    try:
                        _, sym, sd = parse_position_key(pk)
                        candidate_pos = await self.restore_position_from_backups(account_key, sym, sd)
                        if candidate_pos:
                            recovered_count += 1
                    except Exception:
                        pass
                if candidate_pos:
                    acc_positions[pk] = candidate_pos
                    self.positions[pk] = candidate_pos
                else:
                    logger.error(f"[_ensure_full_pk_coverage] {pk}: NOT on disk, NOT in backups — cannot populate")
        if recovered_count > 0 or created_count > 0:
            logger.info(f"[_ensure_full_pk_coverage] {account_key}: Recovered {recovered_count} from disk/backup, Created {created_count} fresh positions. Total now: {len(acc_positions)}")
            logger.debug(f"[_ensure_full_pk_coverage] ⏸️ SAVE DISABLED - updates queued for periodic saver")

    async def _run_reduction_checks_for_account(self, account_key: str, positions_dict: Dict[str, Position], now_ts: float, small_threshold: float, get_monitor_interval: Callable[[float], float]) -> Optional[float]:
        if account_key == 'flz':return
        account_positions_to_check = []
        for position_key, position in positions_dict.items():
            if not position:
                continue
            action = 'REDUCE'
            symbol = getattr(position, "symbol", None)
            positionAmt = abs(float(getattr(position, "positionAmt", 0)))
            if account_key in ['flz', 'men', 'fin'] and position.gain < 0.6:
                return
            current_price = safe_fetch_float(getattr(position, "mark_price", 0), 0.0)
            if current_price <= 0 and symbol:
                try :
                    current_price = safe_fetch_float(await self.get_mark_price(symbol, allow_fallback=True), 0.0)
                except Exception as price_err:
                    logger.debug(f"[MONITOR_REDUCTIONS] [{account_key}] Failed mark price for {symbol}: {price_err}")
                    current_price = safe_fetch_float(getattr(position, "mark_price", 0), 0.0)
            if current_price <= 0:
                continue
            pos_min_qty = max(2 * config.MIN_POSITION_SIZE / current_price, self.min_qty.get(position.symbol, 0.0001))
            if positionAmt < pos_min_qty:
                interval = 60.0
                last_check = self.last_monitored.get(position_key, 0.0)
                if now_ts - last_check >= interval:
                    self.last_monitored[position_key] = now_ts
                    if not hasattr(self, 'last_monitored_positions'): self.last_monitored_positions = {}
                    self.last_monitored_positions[position_key] = now_ts
                    account_positions_to_check.append((position_key, account_key, position, 0.0, interval, now_ts - last_check))
                if position_key in self.reduced_in_monitor_reductions and now_ts - self.reduced_in_monitor_reductions.get(position_key, 0.0) > 540:
                    self.reduced_in_monitor_reductions.pop(position_key, None)
                continue
            value_usd = abs(positionAmt * current_price)
            if value_usd <= 0.0:
                continue
            position_side = getattr(position, "position_side", "LONG")
            entry_price = safe_fetch_float(getattr(position, "entry_price", 0), 0.0)
            if entry_price <= 0 or entry_price < current_price * 0.01 or entry_price > current_price * 100:
                gain = safe_fetch_float(getattr(position, "gain", 0.0), 0.0)
            else:
                gain = calculate_gain(position_side, current_price, entry_price)
            effective_value = value_usd if value_usd >= small_threshold else small_threshold
            interval = get_monitor_interval(effective_value)
            last_check = self.last_monitored.get(position_key, 0.0)
            if last_check <= 0.0:
                last_check = now_ts - interval
            staleness = now_ts - last_check
            stale_threshold = float(getattr(self.config, "MONITOR_REDUCTION_STALE_THRESHOLD", 180.0))
            if staleness > stale_threshold and positionAmt >= pos_min_qty and self.logger:
                logger.warning(f"[MONITOR_REDUCTIONS] [{account_key}] {position_key} staleness={staleness:.1f}s>{stale_threshold:.0f}s")
            if positionAmt > 0 and positionAmt >= pos_min_qty:
                if interval <= 0.0 or staleness >= interval:
                    self.last_monitored[position_key] = now_ts
                    if not hasattr(self, 'last_monitored_positions'):
                        self.last_monitored_positions.clear()
                    self.last_monitored_positions[position_key] = now_ts
                    symbol = getattr(position, 'symbol', None)
                    if symbol:
                        self.last_symbol_monitored[symbol] = now_ts
                    account_positions_to_check.append((position_key, account_key, position, effective_value, interval, staleness))
            elif position_key in self.reduced_in_monitor_reductions:
                reduction_time = self.reduced_in_monitor_reductions[position_key]
                time_since_reduction = now_ts - reduction_time
                if time_since_reduction <= 540:
                    account_positions_to_check.append((position_key, account_key, position, effective_value, 15.0, staleness))
                else:
                    self.reduced_in_monitor_reductions.pop(position_key, None)
        account_positions_to_check.sort(key=lambda x: -x[3])
        if not account_positions_to_check:
            return None
        logger.info(f"[MONITOR_REDUCTIONS] [{account_key}] Checking {len(account_positions_to_check)} positions")
        top_positions = account_positions_to_check[:5]
        for idx, (pk, ak, pos, effective_value, interval, staleness) in enumerate(top_positions, 1):
            positionAmt = abs(float(getattr(pos, "positionAmt", 0)))
            current_price = safe_fetch_float(getattr(pos, "mark_price", 0), 0.0)
            actual_value = abs(positionAmt * current_price)
            gain = getattr(pos, "gain", 0.0) * 100 if hasattr(pos, "gain") else 0.0
            logger.info(f"[MONITOR_REDUCTIONS] [{account_key}] Top {idx}: {pk} value=${actual_value:.2f} amt={positionAmt:.6f} price={current_price:.6f} gain={gain:.2f}%")
        tasks = [asyncio.create_task(check_position_reductions(self, pk, ak, pos)) for pk, ak, pos, _, _, _ in account_positions_to_check]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                pk, _, _, _, _, _ = account_positions_to_check[idx]
                logger.error(f"[MONITOR_REDUCTIONS] [{account_key}] Task failed for {pk}: {result}")
        return min(get_monitor_interval(effective_value) for _, _, _, effective_value, _, _ in account_positions_to_check)

    async def _run_stale_monitor_for_account(self, account_key: str, positions_dict: Dict[str, Position], now_ts: float, stale_threshold: float) -> None:
        account_stale = []
        for position_key, position in positions_dict.items():
            if not position:
                continue
            last_time = self.last_monitored.get(position_key, 0.0)
            if last_time <= 0.0:
                continue
            elapsed = now_ts - last_time
            master_last = master_stop_last_seen.get(position_key, 0.0)
            if master_last > 0.0:
                master_gap = now_ts - master_last
                if master_gap >= MASTER_STOP_STALE_SECONDS and now_ts - master_stop_last_alert.get(position_key, 0.0) >= MASTER_STOP_ALERT_COOLDOWN:
                    logger.critical(f"[MASTER_STOP_MONITOR] {position_key} stale gap={master_gap:.1f}s>{MASTER_STOP_STALE_SECONDS:.0f}s count={master_stop_monitor_counter.get(position_key, 0)}")
                    master_stop_last_alert[position_key] = now_ts
            if elapsed >= stale_threshold:
                self.last_monitored[position_key] = now_ts
                if not hasattr(self, 'last_monitored_positions'):
                    self.last_monitored_positions.clear()
                self.last_monitored_positions[position_key] = now_ts
                symbol = getattr(position, 'symbol', None)
                if symbol:
                    self.last_symbol_monitored[symbol] = now_ts
                account_stale.append((position_key, account_key, position))
        if not account_stale:
            return
        logger.warning(f"[STALE_MONITOR] [{account_key}] Dispatching {len(account_stale)} positions (>{stale_threshold:.0f}s)")
        tasks = [asyncio.create_task(check_position_reductions(self, pk, ak, pos)) for pk, ak, pos in account_stale]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                pk, _, _ = account_stale[idx]
                logger.error(f"[STALE_MONITOR] [{account_key}] Task failed for {pk}: {result}")
                logger.debug(traceback.format_exc())

    async def monitor_reductions_priority(self, account_key: str) -> None:
        if account_key == 'flz': return
        """High-priority monitor reductions that runs immediately with latest indicators and positions"""
        if not self._loading_complete_event.is_set():
            logger.warning(f"[moni tor_reductions_priority][{account_key}] ⏳ BLOCKING: Waiting for positions to load...")
            try :
                await asyncio.wait_for(self._loading_complete_event.wait(), timeout=30.0)
                logger.info(f"[monitor _reductions_priority][{account_key}] Positions loaded, proceeding")
            except asyncio.TimeoutError:
                logger.error(f"[monitor _reductions_priority][{account_key}] ⚠️ Timeout waiting for loading, proceeding anyway")
        try :
            now_ts = time.time(); now_dt = datetime.now(timezone.utc)
            combined_positions: Dict[str, Position] = {}
            base_positions = self.positions_by_account.get(account_key, {})
            if base_positions: combined_positions.update(base_positions)
            tm_positions = getattr(self.service, "positions_by_account", {})
            if isinstance(tm_positions, dict):
                tm_account_positions = tm_positions.get(account_key, {})
                if tm_account_positions: combined_positions.update(tm_account_positions)
            if not combined_positions: return
            has_nonzero_positions = any( pos and abs(float(getattr(pos, 'positionAmt', 0) or 0)) > 0 for pos in combined_positions.values() )
            if not has_nonzero_positions:
                logger.debug(f"[MONITO R_REDUCTIONS_PRIORITY] [{account_key}] Skipping - no positions with positionAmt > 0")
                return
            if hasattr(self, 'ensure_indicator_snapshot_ready'):
                await self.ensure_indicator_snapshot_ready({'service': self, 'symbol': None, 'logger': self.logger})
            symbols_to_refresh = {pos.symbol for pos in combined_positions.values() if pos and getattr(pos, 'symbol', None)}
            for symbol in symbols_to_refresh:
                try :
                    await self.get_mark_price(symbol, allow_fallback=True, max_staleness=2.0)
                    if hasattr(self, 'refresh_indicator_for_symbol'):
                        await self.refresh_indicator_for_symbol(symbol)
                    self.last_symbol_monitored[symbol] = now_ts
                except Exception: pass
            START_USD = float(getattr(config, "START_POSITION_SIZE", 55.0)); SMALL_THRESHOLD = START_USD

            def get_monitor_interval(value_usd: float) -> float: return 0.0
            await self._run_reduction_checks_for_account(account_key, combined_positions, now_ts, SMALL_THRESHOLD, get_monitor_interval)
            stale_threshold = float(getattr(self.config, "STALE_MONITOR_THRESHOLD", 90.0))
            await self._run_stale_monitor_for_account(account_key, combined_positions, now_ts, stale_threshold)
            logger.info(f"[MONIT OR_REDUCTIONS_PRIORITY] [{account_key}] Completed priority check for {len(combined_positions)} positions")
        except Exception as e:
            logger.error(f"[MONITOR_RE DUCTIONS_PRIORITY] [{account_key}] Error: {e}", exc_info=True)

    async def monitor_reductions_for_account(self, account_key: str) -> None:
        if not self._loading_complete_event.is_set():
            logger.warning(f"[monitor_reductions_for_account][{account_key}] ⏳ BLOCKING: Waiting for positions to load...")
            try :
                await asyncio.wait_for(self._loading_complete_event.wait(), timeout=60.0)
                logger.info(f"[monitor_reductions_for_account][{account_key}] Positions loaded, starting monitor loop")
            except asyncio.TimeoutError:
                logger.error(f"[monitor_reductions_for_account][{account_key}] ⚠️ Timeout waiting for loading, starting anyway")
        START_USD = float(getattr(config, "START_POSITION_SIZE", 55.0))
        BIG_THRESHOLD = 3.0 * START_USD
        SMALL_THRESHOLD = START_USD

        def get_monitor_interval(value_usd: float) -> float:
            if value_usd > 0: return 5.0
            return 12.0 if value_usd >= BIG_THRESHOLD else 15.0 if value_usd > 200.0 else 24.0 if value_usd > 50 else 48.0
        await asyncio.sleep(10)
        while not self._housekeeping_stop:
            try :
                now_ts = time.time()
                self._monitor_heartbeats[account_key] = now_ts
                if account_key not in self._monitor_reductions_started_accounts:
                    self._monitor_reductions_started_accounts.add(account_key)
                    logger.info(f"[MONITOR_REDUCTIONS] [{account_key}] loop started")
                now_dt = datetime.now(timezone.utc)
                mark_age_limit = float(getattr(self.config, "HOUSEKEEPING_MARK_PRICE_MAX_AGE", getattr(self.config, "MARK_PRICE_MAX_STALENESS", 2.0)))
                price_update_time = getattr(self.service, "price_update_time", {})
                symbols_to_refresh: Set[str] = set()
                for source in ( self.positions_by_account.get(account_key, {}), getattr(self.service, "positions_by_account", {}).get(account_key, {}), ):
                    if isinstance(source, dict):
                        for position in source.values():
                            if not position or not getattr(position, "symbol", None):
                                continue
                            symbol = position.symbol
                            last_ts = price_update_time.get(symbol)
                            last_dt = ensure_tz(last_ts) if isinstance(last_ts, datetime) else None
                            age = (now_dt - last_dt).total_seconds() if last_dt else mark_age_limit + 1.0
                            if age > mark_age_limit:
                                symbols_to_refresh.add(symbol)
                for symbol in symbols_to_refresh:
                    try :
                        await self.get_mark_price(symbol, allow_fallback=True, max_staleness=mark_age_limit)
                    except Exception as mark_err:
                        logger.debug(f"[MONITOR_REDUCTIONS] [{account_key}] Mark price refresh failed for {symbol}: {mark_err}")
                max_positions_age = float(getattr(self.config, "HOUSEKEEPING_POSITIONS_MAX_AGE", 4.0))
                last_sync_dt = ensure_tz(self.positions_last_sync) if isinstance(self.positions_last_sync, datetime) else None
                if not last_sync_dt or (now_dt - last_sync_dt).total_seconds() > max_positions_age:
                    if hasattr(self, "fetch_positions"):
                        try :
                            await self.fetch_positions(account_key)
                        except Exception as fetch_err:
                            logger.debug(f"[MONITOR_REDUCTIONS] [{account_key}] f etch_positions failed: {fetch_err}")
                combined_positions: Dict[str, Position] = {}
                base_positions = self.positions_by_account.get(account_key, {})
                if base_positions:
                    combined_positions.update(base_positions)
                tm_positions = getattr(self.service, "positions_by_account", {})
                if isinstance(tm_positions, dict):
                    tm_account_positions = tm_positions.get(account_key, {})
                    if tm_account_positions:
                        combined_positions.update(tm_account_positions)
                if not combined_positions:
                    await asyncio.sleep(30)
                    continue
                next_interval = await self._run_reduction_checks_for_account( account_key, combined_positions, now_ts, SMALL_THRESHOLD, get_monitor_interval )
                stale_threshold = float(getattr(self.config, "STALE_MONITOR_THRESHOLD", 90.0))
                await self._run_stale_monitor_for_account(account_key, combined_positions, now_ts, stale_threshold)
                sleep_candidates: List[float] = []
                if isinstance(next_interval, (int, float)):
                    sleep_candidates.append(float(next_interval))
                sleep_interval = min(sleep_candidates, default=30.0)
                await asyncio.sleep(max(5.0, sleep_interval))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[MONITOR_REDUCTIONS] [{account_key}] Error: {exc}")
                logger.debug(traceback.format_exc())
                await asyncio.sleep(30)

    def _ensure_reduction_tasks_started(self) -> None:
        account_keys: Set[str] = set()
        if isinstance(self.accounts, dict):
            account_keys.update(self.accounts.keys())
        account_keys.update(self.positions_by_account.keys())
        tm_positions = getattr(self.service, "positions_by_account", {})
        if isinstance(tm_positions, dict):
            account_keys.update(tm_positions.keys())
        for account_key in sorted(account_keys):
            if not account_key:
                continue
            task = self._monitor_reduction_tasks.get(account_key)
            if not task or task.done():
                self._monitor_reduction_tasks[account_key] = asyncio.create_task( self.monitor_reductions_for_account(account_key) )

    async def _restart_reduction_task(self, account_key: str, reason: str) -> None:
        existing = self._monitor_reduction_tasks.get(account_key)
        if existing and not existing.done():
            existing.cancel()
            with suppress(asyncio.CancelledError):
                await existing
        self._monitor_reduction_tasks[account_key] = asyncio.create_task( self.monitor_reductions_for_account(account_key) )
        logger.warning(f"[MONITOR_WATCHDOG] Restarted reduction loop for {account_key}: {reason}")

    async def _symbol_monitoring_watchdog(self) -> None:
        """Watchdog that raises hell if any symbol is not monitored for >90s and identifies/fixes blockers - only for loaded accounts"""
        await asyncio.sleep(10)
        while not self._housekeeping_stop:
            try :
                now_ts = time.time(); now_dt = datetime.now(timezone.utc)
                loaded_accounts = list(getattr(self, 'accounts', {}).keys())
                if not loaded_accounts: await asyncio.sleep(15); continue
                all_symbols = set()
                symbol_attrs = [ 'symbols_ang_long', 'symbols_ang_short', 'symbols_men', 'symbols_fin', 'symbols_inf_long', 'symbols_inf_short', 'symbols_flz' ]
                for account_key in loaded_accounts:
                    for attr_name in symbol_attrs:
                        sym_set = getattr(self, attr_name, set())
                self.last_symbol_monitored = getattr(self, 'last_symbol_monitored', {})
                symbols_stale = []
                for symbol in all_symbols:
                    try :
                        last_monitored = self.last_symbol_monitored.get(symbol, 0.0)
                        age = now_ts - last_monitored if last_monitored > 0 else 999.0
                        if age > 90.0:
                            symbols_stale.append((symbol, age))
                    except Exception: pass
                if symbols_stale:
                    symbols_stale.sort(key=lambda x: -x[1])
                    for symbol, age in symbols_stale[:10]:
                        try :
                            blocker_found = False
                            indicators = ii(self, symbol) if self else {}
                            if not indicators or len(indicators) < 10:
                                blocker_found = True
                                logger.info(f"[SYMBOL_WATCHDOG] 🔥 {symbol} STALE_INDICATORS age={age:.1f}s - reloading indicators")
                                if hasattr(self, 'refresh_indicator_for_symbol'):
                                    asyncio.create_task(self.refresh_indicator_for_symbol(symbol))
                            indicator_ts = getattr(self, 'indicators_timestamp', None)
                            if isinstance(indicator_ts, datetime):
                                indicator_age = (now_dt - (indicator_ts.replace(tzinfo=timezone.utc) if indicator_ts.tzinfo is None else indicator_ts)).total_seconds()
                                if indicator_age > 180.0:
                                    blocker_found = True
                                    logger.critical(f"[SYMBOL_WATCHDOG] 🔥 {symbol} INDICATOR_SNAPSHOT_STALE age={indicator_age:.1f}s - forcing refresh")
                                    if hasattr(self, 'ensure_indicator_snapshot_ready'):
                                        asyncio.create_task(self.ensure_indicator_snapshot_ready({'service': self, 'symbol': symbol, 'logger': self.logger}))
                            positions_stale = []
                            accounts_with_stale = set()
                            for account_key in getattr(self, 'accounts', {}).keys():
                                account_positions = getattr(self, 'positions_by_account', {}).get(account_key, {})
                                for position_key, position in account_positions.items():
                                    if position and getattr(position, 'symbol', None) == symbol:
                                        positionAmt = getattr(position, 'positionAmt', 0.0) or 0.0
                                        if abs(float(positionAmt)) > 0.0:
                                            pos_last = getattr(position, 'last_updated', None) or getattr(position, 'mark_price_last_updated', None)
                                            if isinstance(pos_last, datetime):
                                                pos_age = (now_dt - (pos_last.replace(tzinfo=timezone.utc) if pos_last.tzinfo is None else pos_last)).total_seconds()
                                                mark_price_val = safe_fetch_float(getattr(position, 'mark_price', 0), 0.0)
                                                position_value = abs(float(positionAmt) * mark_price_val) if mark_price_val > 0 else 0.0
                                                stale_threshold = 20.0 if position_value >= 165.0 else 30.0 if position_value > 50.0 else 55.0
                                                if pos_age > stale_threshold:
                                                    positions_stale.append((position_key, account_key, pos_age))
                                                    accounts_with_stale.add(account_key)
                            if positions_stale:
                                blocker_found = True
                                stale_keys = ', '.join([f"{pos_key}@{acc_key}({age:.1f}s)" for pos_key, acc_key, age in positions_stale[:5]])
                                logger.info(f"[SYMBOL_WATCHDOG] 🔥 {symbol} POSITIONS_STALE {len(positions_stale)} open positions age>threshold: {stale_keys} - forcing refresh")
                                for acc_key in accounts_with_stale:
                                    asyncio.create_task(self.fetch_positions(acc_key))
                                if hasattr(self, 'get_mark_price'):
                                    asyncio.create_task(self.get_mark_price(symbol, allow_fallback=True, max_staleness=2.0))
                            if not blocker_found:
                                logger.debug(f"[SYMBOL_WATCHDOG] 🔥 {symbol} NOT_MONITORED age={age:.1f}s - NO_BLOCKER_FOUND - forcing priority check")
                                for account_key in getattr(self, 'accounts', {}).keys():
                                    if self.is_symbol_allowed(account_key, symbol):
                                        asyncio.create_task(self.monitor_reductions_priority(account_key))
                            self.last_symbol_monitored[symbol] = now_ts
                        except Exception as sym_err:
                            logger.error(f"[SYMBOL_WATCHDOG] Error checking {symbol}: {sym_err}")
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[SYMBOL_WATCHDOG] Watchdog error: {exc}", exc_info=True)
                await asyncio.sleep(15)

    async def _monitor_reductions_watchdog(self) -> None:
        await asyncio.sleep(30)
        if not config.SERVICE_REDUCE: return
        while not self._housekeeping_stop:
            try :
                self._ensure_reduction_tasks_started()
                now = time.time()
                for account_key, task in list(self._monitor_reduction_tasks.items()):
                    if account_key == 'flz':continue
                    if task.done():
                        reason = "task completed"
                        exc = None
                        try :
                            exc = task.exception()
                        except asyncio.CancelledError:
                            exc = None
                        if exc and self.logger:
                            logger.error(f"[MONITOR_WATCHDOG] Loop for {account_key} ended with error: {exc}")
                            reason = f"task exception: {exc}"
                        await self._restart_reduction_task(account_key, reason)
                        continue
                    heartbeat = self._monitor_heartbeats.get(account_key, 0.0)
                    if heartbeat and (now - heartbeat) > 120.0:
                        logger.warning(f"[MONITOR_WATCHDOG] heartbeat stale for {account_key} ({now - heartbeat:.1f}s)")
                        await self._restart_reduction_task(account_key, f"heartbeat stale {now - heartbeat:.1f}s")
                    last_monitored_positions = getattr(self, 'last_monitored_positions', {})
                    account_positions = getattr(self, 'positions_by_account', {}).get(account_key, {})
                    positions_not_monitored = []
                    for position_key, position in account_positions.items():
                        if not position: continue
                        positionAmt = getattr(position, 'positionAmt', 0.0) or 0.0
                        if abs(float(positionAmt)) <= 0.0: continue
                        last_monitored_ts = last_monitored_positions.get(position_key, 0.0)
                        if last_monitored_ts <= 0.0: continue
                        age = now - last_monitored_ts
                        mark_price_val = safe_fetch_float(getattr(position, 'mark_price', 0), 0.0)
                        position_value = abs(float(positionAmt) * mark_price_val) if mark_price_val > 0 else 0.0
                        stale_threshold = 11.0 if position_value >= 165.0 else 20.0 if position_value > 50.0 else 35.0
                        if age > stale_threshold:
                            positions_not_monitored.append((position_key, age, position_value, stale_threshold))
                    if positions_not_monitored:
                        positions_not_monitored.sort(key=lambda x: -x[1])
                        stale_keys = ', '.join([f"{pk}(${pv:.0f}, {age:.1f}s>{th:.0f}s)" for pk, age, pv, th in positions_not_monitored[:5]])
                        max_threshold = max(th for _, _, _, th in positions_not_monitored)
                        logger.warning(f"[MONITOR_WATCHDOG] {account_key}: {len(positions_not_monitored)} positions not monitored >threshold: {stale_keys}")
                        await self._restart_reduction_task(account_key, f"{len(positions_not_monitored)} positions not monitored >{max_threshold:.0f}s")
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"[MONITOR_WATCHDOG] error: {exc}")
                await asyncio.sleep(30)

    async def _ladder_save_loop(self) -> None:
        await asyncio.sleep(5)
        save_interval = 60.0 
        while not self._housekeeping_stop:
            try :
                await self.save_all_ladder_levels()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"[_ladder_save_loop] Save failed: {exc}")
            await asyncio.sleep(save_interval)

    async def _positions_periodic_save_loop(self) -> None:
        await asyncio.sleep(5)
        save_interval = 15.0
        logger.info(f"[_positions_periodic_save_loop] Started - position count check every {save_interval}s")
        if not hasattr(self, "_last_periodic_save_time"):
            self._last_periodic_save_time: float = 0.0
        save_count = 0
        consecutive_errors = 0
        while not self._housekeeping_stop:
            try :
                async with self._periodic_save_lock:
                    total_positions = sum(len(acc_pos) for acc_pos in self.positions_by_account.values())
                    expected_count = self._get_expected_symbol_count() * 2 * len(self.accounts)
                    if total_positions == 0:
                        logger.warning(f"[_positions_periodic_save_loop] ⚠️ No positions in memory - skipping save")
                        await asyncio.sleep(save_interval)
                        continue
                    save_count += 1
                    pass
                    self._last_periodic_save_time = time.time()
                    consecutive_errors = 0
                    if total_positions < expected_count:
                        if save_count % 10 == 0:
                            logger.debug(f"[_positions_periodic_save_loop] Save #{save_count} completed ({total_positions}/{expected_count} positions) - saved to *_active.json")
                    elif save_count % 20 == 0:
                        logger.debug(f"[_positions_periodic_save_loop] Save #{save_count} completed ({total_positions} positions)")
            except asyncio.CancelledError:
                break
            except OSError as ose:
                if "Too many open files" in str(ose) or ose.errno == 24:
                    consecutive_errors += 1
                    wait_time = save_interval * (2 ** min(consecutive_errors, 4))
                    logger.error(f"[_positions_periodic_save_loop] File handle exhaustion (error #{consecutive_errors}) - waiting {wait_time:.1f}s: {ose}")
                    await asyncio.sleep(wait_time)
                    continue
            except Exception as exc:
                consecutive_errors += 1
                logger.error(f"[_positions_periodic_save_loop] Save failed (error #{consecutive_errors}): {exc}", exc_info=True)
                if consecutive_errors >= 5:
                    await asyncio.sleep(save_interval * 2)
            await asyncio.sleep(save_interval)

    async def _update_mark_prices_loop(self) -> None:
        """Periodically update mark prices for all positions to prevent stale position errors"""
        await asyncio.sleep(10) 
        update_interval = 5.0 
        logger.info(f"[_update_mark_prices_loop] Started - updating mark prices every {update_interval}s")
        while not self._housekeeping_stop:
            try :
                now = datetime.now(timezone.utc)
                symbols_to_update = set()
                positions_to_update = []
                for position_key, position in self.positions.items():
                    if not position or not hasattr(position, 'symbol') or not position.symbol:
                        continue
                    symbol = str(position.symbol).strip().upper()
                    if symbol:
                        symbols_to_update.add(symbol)
                        positions_to_update.append((position_key, position, symbol))
                for symbol in symbols_to_update:
                    try :
                        current_price, price_ts = await self.get_current_price(symbol) if hasattr(self, 'get_current_price') else (await self.get_mark_price(symbol, allow_fallback=True, max_staleness=5.0), None)
                        if current_price and current_price > 0:
                            price_timestamp = ensure_tz(price_ts) if price_ts else now
                            for position_key, position, pos_symbol in positions_to_update:
                                if pos_symbol == symbol:
                                    if not hasattr(position, 'mark_price') or position.mark_price != current_price:
                                        position.mark_price = current_price
                                        position.mark_price_last_updated = price_timestamp
                                        self.positions[position_key] = position
                                        account_key, _, _ = parse_position_key(position_key)
                                        self.positions_by_account.setdefault(account_key, {})[position_key] = position
                    except Exception as exc:
                        logger.debug(f"[_update_mark_prices_loop] Failed to update mark price for {symbol}: {exc}")
                if positions_to_update:
                    self._mark_positions_dirty()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[_update_mark_prices_loop] Error: {exc}", exc_info=True)
            await asyncio.sleep(update_interval)

    def _serialize_stop_levels_for_snapshot(self, position_key: str, levels: List[Any]) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for entry in levels or []:
            if isinstance(entry, StopLevel):
                serialized.append(entry.to_dict())
            elif isinstance(entry, dict):
                serialized.append(entry)
        return serialized
    @staticmethod

    def _iso(value: Any) -> Any:
        if isinstance(value, datetime):
            return ensure_tz(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return value

    async def _export_latest_market_data(self) -> Tuple[Dict[str, Any], datetime]:
        payload: Dict[str, Any] = {}
        indicator_ts = self.indicators_timestamp if isinstance(self.indicators_timestamp, datetime) else datetime.now(timezone.utc)
        if isinstance(indicator_ts, datetime) and indicator_ts.tzinfo is None:
            indicator_ts = indicator_ts.replace(tzinfo=timezone.utc)
        raw_content = self.raw_indicators_json_content
        if raw_content:
            try :
                payload = orjson.loads(raw_content) 
            except Exception:
                try :
                    if isinstance(raw_content, bytes):
                        payload = json.loads(raw_content.decode("utf-8"))
                    elif isinstance(raw_content, str):
                        payload = json.loads(raw_content)
                except Exception:
                    payload = {}

        if not payload and self.indicators_bridge:
            payload = self.indicators_bridge.cache
            indicator_ts = self.indicators_bridge.timestamp

        snapshot_ts = _snapshot_timestamp(payload) if payload else None
        freshness_candidates = [indicator_ts]
        if snapshot_ts:
            freshness_candidates.append(snapshot_ts)
        if payload and not _snapshot_is_fresh(payload, MAX_MARKET_DATA_AGE_SECONDS, freshness_candidates):
            try :
                await self.force_refresh_all_indicators()
                refreshed_payload: Dict[str, Any] = {}
                refreshed_raw = self.raw_indicators_json_content
                if refreshed_raw:
                    try :
                        refreshed_payload = orjson.loads(refreshed_raw) 
                    except Exception:
                        try :
                            if isinstance(refreshed_raw, bytes):
                                refreshed_payload = json.loads(refreshed_raw.decode("utf-8"))
                            elif isinstance(refreshed_raw, str):
                                refreshed_payload = json.loads(refreshed_raw)
                        except Exception:
                            refreshed_payload = {}
                if not refreshed_payload and getattr(self, "indicators_bridge", None):
                    bridge = self.indicators_bridge
                    try :
                        async with bridge._lock:
                            refreshed_payload = dict(bridge.cache)
                            if isinstance(bridge.timestamp, datetime):
                                indicator_ts = bridge.timestamp if bridge.timestamp.tzinfo else bridge.timestamp.replace(tzinfo=timezone.utc)
                    except Exception:
                        refreshed_payload = dict(getattr(bridge, "cache", {})) if hasattr(bridge, "cache") else {}
                if isinstance(refreshed_payload, dict) and refreshed_payload:
                    payload = refreshed_payload
                    refreshed_ts = _snapshot_timestamp(payload)
                    if refreshed_ts:
                        snapshot_ts = refreshed_ts
            except Exception:
                pass
        effective_ts = snapshot_ts or indicator_ts
        if isinstance(effective_ts, datetime) and effective_ts.tzinfo is None:
            effective_ts = effective_ts.replace(tzinfo=timezone.utc)
        return payload, effective_ts if isinstance(effective_ts, datetime) else datetime.now(timezone.utc)

    async def shutdown(self) -> None:
        await self.stop_account_monitors()
        await self.stop_housekeeping_tasks()
        if self.indicators_bridge:
            try :
                await self.indicators_bridge.shutdown()
            finally:
                self.indicators_bridge = None
        self.indicator_fetcher = None
        if self.managed_stop_registry_dirty:
            await self.save_managed_stops()
        global service_global, positions_engine
        if service_global is self:
            service_global = None
        if positions_engine is self:
            positions_engine = None
        if not self._shutdown_event.is_set():
            self._shutdown_event.set()

    async def _pnl_decay_loop(self) -> None:
        first_run = True
        try :
            while not self._housekeeping_stop:
                if not first_run:
                    await asyncio.sleep(60)
                else:
                    first_run = False
                now = datetime.now(timezone.utc)
                touched_accounts: Set[str] = set()
                async with self._positions_lock:
                    for position_key, position in self.positions.items():
                        account_key, _, _ = parse_position_key(position_key)
                        if not position or position.positionAmt != 0: continue
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
                            try :
                                account_key, _, _ = parse_position_key(position_key)
                                touched_accounts.add(account_key)
                            except Exception:
                                continue
                for account_key in touched_accounts:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"[pnl_decay] loop error: {exc}")

    async def _log_top_positions_loop(self) -> None:
        interval = float(getattr(self.config, "POSITION_LOG_INTERVAL", 60.0))
        interval = max(15.0, interval)
        first = True
        while not self._housekeeping_stop:
            if first:
                first = False
            else:
                await asyncio.sleep(interval)
                if self._housekeeping_stop:
                    break
            try :
                async with self._positions_lock:
                    snapshot = {acct: list(positions.values()) for acct, positions in self.positions_by_account.items()}
                async with self._price_cache_lock:
                    price_cache = dict(self.price_cache)
                if not snapshot:
                    continue
                for account_key, positions in snapshot.items():
                    last_fetch = self._last_positions_fetch.get(account_key, 0.0)
                    if time.time() - last_fetch > 10.0:
                        try :
                            await self.fetch_positions(account_key)
                        except Exception as exc:
                            logger.debug(f"[position_log] refresh failed for {account_key}: {exc}")
                        positions_dict = await self.get_cached_positions(account_key)
                        positions = list(positions_dict.values())
                    if not positions:
                        continue
                    ranked = []
                    for pos in positions:
                        qty = abs(_safe_float(getattr(pos, "positionAmt", 0.0)))
                        if qty <= 0:
                            continue
                        price = _safe_float(getattr(pos, "mark_price", 0.0))
                        if price <= 0:
                            cache_entry = price_cache.get(pos.symbol)
                            if isinstance(cache_entry, dict):
                                price = _safe_float(cache_entry.get("price"), 0.0)
                            elif isinstance(cache_entry, (tuple, list)) and cache_entry:
                                price = _safe_float(cache_entry[0], 0.0)
                        if price <= 0:
                            continue
                        notional = qty * price
                        ranked.append((notional, pos, price))
                    if not ranked:
                        continue
                    ranked.sort(key=lambda item: item[0], reverse=True)
                    top = ranked[:5]
                    lines = []
                    for notional, pos, _ in top:
                        lines.append(f"{pos.symbol}{pos.position_side}:{notional:.2f}")
                    if lines and self.logger:
                        logger.info(f"[positions][{account_key}] top5 {' '.join(lines)}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(f"[position_log] loop error: {exc}")

    async def _position_flush_loop(self) -> None:
        interval = float(getattr(self.config, "POSITION_SAVE_INTERVAL", 5.0))
        interval = max(5.0, interval)
        event = self._position_flush_event
        try :
            while not self._housekeeping_stop:
                triggered = False
                if event:
                    try :
                        await asyncio.wait_for(event.wait(), timeout=interval)
                        triggered = True
                    except asyncio.TimeoutError:
                        pass
                    if event:
                        event.clear()
                else:
                    await asyncio.sleep(interval)
                if self._housekeeping_stop:
                    break
                has_positions = bool(self.positions) or any(self.positions_by_account.values())
                if not has_positions:
                    continue
                try :
                    self._positions_dirty = True
                    from ez_positions import atomic_save_positions
                    for account_key in self.accounts.keys():
                        try :
                            await atomic_save_positions(self, account_key, force=False)
                            await self.save_augmented_positions(account_key, min_interval=0)
                            await self.save_reduced_positions(account_key, min_interval=0)
                            await self.save_reversed_positions(account_key, min_interval=0)
                        except Exception as exc:
                            logger.debug(f"[position_flush] save error for {account_key}: {exc}")
                except Exception as exc:
                    logger.debug(f"[position_flush] save error: {exc}")
        except asyncio.CancelledError:
            pass

    def _build_position_payload(self, account_key: str, position_side: str) -> Dict[str, Any]:
        positions_dict = self.positions_by_account.get(account_key, {})
        if not isinstance(positions_dict, dict):
            return {}
        payload: Dict[str, Any] = {}
        now = datetime.now(timezone.utc)
        expected_prefix = f"{account_key}:"
        for raw_key, position_obj in positions_dict.items():
            if not isinstance(raw_key, str) or not raw_key.startswith(expected_prefix):
                logger.warning(f"[_build_position_payload][{account_key}:{position_side}] ⚠️ SKIPPING position with wrong account prefix: {raw_key} (expected {expected_prefix}*)")
                continue
            if not position_obj or not hasattr(position_obj, "symbol") or getattr(position_obj, "position_side", "").upper() != position_side:
                continue
            symbol = str(position_obj.symbol).strip().upper()
            if symbol:
                try :
                    payload[raw_key] = position_obj.to_dict()
                except Exception:
                    continue
        return payload

    async def _positions_redis_refresh_loop(self) -> None:
        await asyncio.sleep(5)
        while not self._housekeeping_stop:
            try :
                for account_key in list(self.positions_by_account.keys()):
                    await self._broadcast_positions_to_redis(account_key)
                await asyncio.sleep(5.0) 
            except Exception as exc:
                self.logger.debug(f"[refresh_loop] {exc}")
                await asyncio.sleep(10.0)

    async def _stop_levels_loop(self) -> None:
        return

    # def symbols_active(self) -> Set[str]:
    #     return {self.service._normalize_symbol(pos.symbol) for pos in self.positions.values() if pos and pos.symbol and abs(_safe_float(pos.positionAmt)) > 0}

    async def _refresh_usdc_pairs(self, refresh_window: int = 0) -> None:
        if refresh_window and time.time() - self._usdc_pairs_last_loaded < refresh_window: return
        path = self.config.LIVE_USDC_PAIRS_FILE
        try :
            async with aiofiles.open(path, "r") as handle:
                content = await handle.read()
            data = json.loads(content) if content else []
            if isinstance(data, dict): data = data.get("symbols") or data.get("pairs") or []
            if isinstance(data, list): self._usdc_pairs = {str(item).upper() for item in data if isinstance(item, (str, bytes))}
            self._usdc_pairs_last_loaded = time.time()
            self.available_usdc_pairs = set(self._usdc_pairs)
        except Exception:
            if not self._usdc_pairs: self._usdc_pairs = set()
            self.available_usdc_pairs = set(self._usdc_pairs)

    def _normalize_symbol(self, symbol: str) -> str:
        if not symbol: return ""
        symbol = symbol.strip().upper()
        try : return symbol 
        except Exception: return symbol

    async def _update_position_mark_price(self, symbol: str, price: float, ts: Optional[datetime] = None) -> bool:
        manager = getattr(self, "primary_ws_manager", None)
        if manager:
            return await manager._apply_mark_price(symbol, price, ts)
        return False

    async def get_mark_price(self, symbol: str, *, allow_fallback: bool = True, max_staleness: float = 3.0) -> float:
        norm = self.service._normalize_symbol(symbol)
        manager = getattr(self, "primary_ws_manager", None)
        if not manager and self.websocket_managers:
            manager = next(iter(self.websocket_managers.values()))
            self.primary_ws_manager = manager
        if manager:
            return await manager.get_mark_price(norm, allow_fallback=allow_fallback, max_staleness=max_staleness)
        async with self._price_cache_lock:
            cached = self.price_cache.get(norm)
        if isinstance(cached, dict):
            ts = cached.get("timestamp")
            try :
                ts_dt = ensure_tz(isoparse(ts)) if isinstance(ts, str) else ensure_tz(ts) if isinstance(ts, datetime) else None
            except Exception:
                ts_dt = None
            if ts_dt and (datetime.now(timezone.utc) - ts_dt).total_seconds() <= max_staleness:
                return _safe_float(cached.get("price"), 0.0)
        return 0.0

    def subscribe(self, position_key: str, callback: Callable[[Position], Awaitable[None] | None]) -> None:
        self._callbacks[position_key].append(callback)

    def _ensure_account_positions_cache(self, account_key: Optional[str]) -> Dict[str, Position]:
        if not account_key or not isinstance(account_key, str):
            return {}
        if account_key in self.positions_by_account and self.positions_by_account[account_key]:
            return self.positions_by_account[account_key]
        logger.warning(f"[_ensure_account_positions_cache] {account_key} not in positions_by_account - returning empty. Should be loaded via _load_a ccount first.")
        return self.positions_by_account.setdefault(account_key, {})

    def _get_position_obj(self, position_key: str, account_key: Optional[str] = None) -> Optional[Position]:
        if not isinstance(position_key, str):
            return None
        cached = self.positions.get(position_key)
        if cached:
            return cached
        derived_account = account_key
        if not derived_account:
            try :
                derived_account, _, _ = parse_position_key(position_key)
            except Exception:
                derived_account = None
        if derived_account:
            account_positions = self._ensure_account_positions_cache(derived_account)
            candidate = account_positions.get(position_key)
            if candidate:
                return candidate
        for account_positions in self.positions_by_account.values():
            candidate = account_positions.get(position_key)
            if candidate:
                return candidate
        return None

    def get_position(self, position_key: str) -> Optional[Position]:
        return self._get_position_obj(position_key)

    def get_positions(self, account_key: Optional[str] = None) -> Dict[str, Position]:
        if not account_key:
            return self.positions
        return self._ensure_account_positions_cache(account_key)

    def get_stop_levels(self, position_key: str) -> List[StopLevel]:
        return self.stop_levels.get(position_key, [])

    def get_reentry_plan(self, position_key: str) -> Optional[ReentryPlan]:
        return self.reentry_plans.get(position_key)

    def get_augmented_positions(self) -> Dict[str, datetime]:
        return self.augmented_positions

    def get_reversed_positions(self) -> Dict[str, datetime]:
        return self.reversed_positions

    def get_direct_high_gain(self) -> Dict[str, Dict[str, Any]]:
        return

    async def phantom_kill_on_bootstrap(self) -> int:
        """After disk load, fetch live positions from Binance API and use zero_report_tracker
        to count how many times a position is missing. Only zero positionAmt when the count
        reaches PHANTOM_KILL_THRESHOLD (configurable in config.py, default 2).
        NEVER zeros entry_price, max_gain, opened_at. NEVER deletes dict entries.
        WS data is NEVER used — only full API fetches (futures_position_information)."""
        threshold = getattr(config, 'ZERO_CONFIRMATION_THRESHOLD_API', 1)
        total_killed = 0
        for account_key in list(self.accounts.keys()):
            if account_key in ("tra", "trb", "trc"):
                continue
            if is_sandbox_account(config, account_key):
                continue
            account = self.accounts.get(account_key)
            if not account:
                continue
            account_positions = self.positions_by_account.get(account_key, {})
            active_disk_keys = {pk for pk, pos in account_positions.items() if pos and abs(float(getattr(pos, 'positionAmt', 0) or 0)) > 0}
            if not active_disk_keys:
                continue
            ban_rem = _ban_remaining_seconds()
            if ban_rem > 0:
                _log_ban_skip_throttled(logger, f"PHANTOM_KILL][{account_key}", f"skipping during IP ban ({ban_rem:.0f}s)")
                continue
            try:
                client_obj = getattr(account, "client", None)
                if not client_obj:
                    client_obj = await self._ensure_account_client(account_key)
                if not client_obj:
                    logger.warning(f"[PHANTOM_KILL][{account_key}] No client — skipping")
                    continue
                if hasattr(account, 'safe_api_call'):
                    positions_data = await account.safe_api_call(client_obj.futures_position_information)
                else:
                    positions_data = await asyncio.wait_for(asyncio.to_thread(client_obj.futures_position_information), timeout=30.0)
            except Exception as e:
                _record_ip_ban_from_exc(e)
                logger.warning(f"[PHANTOM_KILL][{account_key}] API failed ({e}) — skipping (safe)")
                continue
            if not positions_data or not isinstance(positions_data, list):
                logger.warning(f"[PHANTOM_KILL][{account_key}] API empty/invalid — skipping (safe)")
                continue
            api_active_keys = set()
            for pos_api in positions_data:
                symbol = (pos_api.get("symbol") or pos_api.get("s") or "").strip().upper()
                raw_amt = float(pos_api.get("positionAmt") or pos_api.get("pa") or 0)
                if abs(raw_amt) == 0:
                    continue
                ps = (pos_api.get("positionSide") or pos_api.get("ps") or ("LONG" if raw_amt >= 0 else "SHORT")).upper()
                api_active_keys.add(f"{account_key}:{symbol}_{ps}")
            if len(api_active_keys) == 0 and len(active_disk_keys) > 10:
                logger.critical(f"[PHANTOM_KILL][{account_key}] API returned 0 active but disk has {len(active_disk_keys)} — likely API issue. SKIPPING.")
                continue
            missing = active_disk_keys - api_active_keys
            confirmed = active_disk_keys & api_active_keys
            # Reset zero reports for positions confirmed by API
            for pk in confirmed:
                if pk in self.zero_report_tracker:
                    del self.zero_report_tracker[pk]
            # Increment zero reports for missing positions
            for pk in missing:
                if pk not in self.zero_report_tracker:
                    self.zero_report_tracker[pk] = {"count": 0, "first_seen": datetime.now(timezone.utc).isoformat(), "source": "phantom_kill_bootstrap"}
                self.zero_report_tracker[pk]["count"] = self.zero_report_tracker[pk].get("count", 0) + 1
                self.zero_report_tracker[pk]["last_seen"] = datetime.now(timezone.utc).isoformat()
            killed = 0
            for pk in missing:
                zr = self.zero_report_tracker.get(pk, {})
                count = zr.get("count", 0)
                if count < threshold:
                    logger.warning(f"[PHANTOM_SUSPECT] {pk}: missing from API ({count}/{threshold} zero reports). Will kill at {threshold}.")
                    continue
                pos = account_positions.get(pk)
                if not pos:
                    continue
                old_amt = abs(float(getattr(pos, 'positionAmt', 0) or 0))
                if old_amt == 0:
                    continue
                logger.critical(f"[PHANTOM_KILL] {pk}: disk positionAmt={old_amt}, missing from API {count}x (threshold={threshold}). ZEROED.")
                pos.positionAmt = 0
                pos.last_updated = datetime.now(timezone.utc)
                account_positions[pk] = pos
                self.positions[pk] = pos
                killed += 1
            if killed > 0:
                logger.critical(f"[PHANTOM_KILL][{account_key}] Zeroed {killed} phantoms ({threshold}-confirmation). Saving.")
                try:
                    from ez_positions import atomic_save_positions
                    await atomic_save_positions(self, account_key, force=True)
                except Exception as save_err:
                    logger.error(f"[PHANTOM_KILL][{account_key}] Save failed: {save_err}", exc_info=True)
                total_killed += killed
            logger.info(f"[PHANTOM_KILL][{account_key}] API={len(api_active_keys)} active, disk={len(active_disk_keys)}, confirmed={len(confirmed)}, missing={len(missing)}, killed={killed}")
        self._save_zero_report_tracker()
        if total_killed > 0:
            logger.critical(f"[PHANTOM_KILL] TOTAL: Zeroed {total_killed} phantoms ({threshold}-confirmation)")
        return total_killed

    async def _periodic_phantom_kill(self):
        """Run phantom kill every 5 minutes to catch phantoms that slip through bootstrap."""
        await asyncio.sleep(30)
        while True:
            try:
                has_client = any(getattr(acc, 'client', None) for acc in self.accounts.values())
                if has_client:
                    killed = await self.phantom_kill_on_bootstrap()
                    if killed > 0:
                        logger.critical(f"[PERIODIC_PHANTOM_KILL] Killed {killed} phantoms")
            except Exception as e:
                logger.warning(f"[PERIODIC_PHANTOM_KILL] Error: {e}")
            await asyncio.sleep(300)

    async def initialize_prev_gain_for_existing_positions(self) -> None:
        now = datetime.now(timezone.utc)
        touched_accounts: Set[str] = set()
        async with self._positions_lock:
            for position_key, position in list(self.positions.items()):
                if not position: 
                    continue
                if isinstance(position, dict):
                    try :
                        position = Position.from_dict(position)
                        self.positions[position_key] = position
                    except Exception as e:
                        self.logger.error(f"[init_prev_gain] Failed to convert dict to Position for {position_key}: {e}")
                        continue
                if not hasattr(position, 'prev_gain_last_updated') or position.prev_gain_last_updated is None:
                    position.prev_gain_last_updated = now
                    current_gain = getattr(position, 'gain', 0.0)
                    prev_gain = getattr(position, 'prev_gain', 0.0)
                    if prev_gain == 0.0 and current_gain != 0.0:
                        position.prev_gain = current_gain
                    try :
                        account_key, _, _ = parse_position_key(position_key)
                        touched_accounts.add(account_key)
                        if account_key not in self.positions_by_account:
                            self.positions_by_account[account_key] = {}
                        self.positions_by_account[account_key][position_key] = position
                    except Exception:
                        continue

    def deteriorate_max_gain(self, account_key, position: Position) -> float:
        now = datetime.now(timezone.utc)
        position_key = construct_position_key(account_key, position.symbol, position.position_side)
        if position.positionAmt and position.positionAmt > 0.0:
            return position.max_gain
        last_reduction = position.last_reduction_time
        if not last_reduction:
            return position.max_gain
        time_since = now - last_reduction
        decay_start = timedelta(hours=self.config.PNL_DECAY_START_HOURS)
        decay_complete = timedelta(days=self.config.MAX_GAIN_DECAY_COMPLETE_DAYS)
        if time_since < decay_start:
            return position.max_gain
        if time_since >= decay_complete:
            return position.max_gain * self.config.PNL_DECAY_FINAL_PERCENTAGE
        progress = (time_since - decay_start).total_seconds() / max((decay_complete - decay_start).total_seconds(), 1.0)
        decay_factor = 0.9 ** (progress * 5)
        return position.max_gain * decay_factor

    def deteriorate_realized_pnl(self, account_key, position: Position) -> float:
        now = datetime.now(timezone.utc)
        pnl = (position.realized_pnl)
        if abs(position.realized_pnl) > 10.0:
            position.realized_pnl = pnl
        if position.positionAmt and position.positionAmt > 0.0:
            return pnl
        last_reduction = position.last_reduction_time
        if not last_reduction:
            return pnl
        time_since = now - last_reduction
        decay_start = timedelta(hours=self.config.PNL_DECAY_START_HOURS)
        decay_complete = timedelta(days=self.config.PNL_DECAY_COMPLETE_DAYS)
        if time_since < decay_start:
            return pnl
        if time_since >= decay_complete:
            return pnl * self.config.PNL_DECAY_FINAL_PERCENTAGE
        progress = (time_since - decay_start).total_seconds() / max((decay_complete - decay_start).total_seconds(), 1.0)
        decay_factor = 0.9 ** (progress * 5)
        return pnl * decay_factor

    def deteriorate_max_quantity(self, account_key, position: Position, current_price: float) -> float:
        now = datetime.now(timezone.utc)
        position_key = construct_position_key(account_key, position.symbol, position.position_side)
        if position.positionAmt and position.positionAmt > 0.0:
            return position.max_quantity
        if not position.last_reduction_time or current_price <= 0:
            return position.max_quantity
        time_since = now - position.last_reduction_time
        deterioration_start = timedelta(hours=self.config.MAX_DECAY_START_HOURS)
        deterioration_complete = timedelta(days=self.config.MAX_DECAY_COMPLETE_DAYS)
        if time_since < deterioration_start:
            return position.max_quantity
        target_max_qty = 2 * self.config.START_POSITION_SIZE / current_price
        original_max_qty = position.max_quantity
        if time_since >= deterioration_complete:
            if original_max_qty > target_max_qty:
                self._deteriorate_reentry_amount(position_key, 1.0, current_price)
                return target_max_qty
            return original_max_qty
        elapsed = time_since - deterioration_start
        progress = min(1.0, elapsed.total_seconds() / max((deterioration_complete - deterioration_start).total_seconds(), 1.0))
        if original_max_qty <= target_max_qty:
            return original_max_qty
        deteriorated_qty = original_max_qty - (original_max_qty - target_max_qty) * progress
        deteriorated_qty = max(deteriorated_qty, target_max_qty)
        self._deteriorate_reentry_amount(position_key, progress, current_price)
        return deteriorated_qty

    def _deteriorate_reentry_amount(self, position_key: str, progress: float, current_price: float) -> None:
        plan = self.reentry_plans.get(position_key)
        if not plan or plan.reentry_amount <= 0 or current_price <= 0:
            return
        target_amount = 2 * self.config.START_POSITION_SIZE / current_price
        original_amount = plan.reentry_amount
        if original_amount <= target_amount:
            return
        deteriorated_amount = original_amount - (original_amount - target_amount) * progress
        deteriorated_amount = max(deteriorated_amount, target_amount)
        self.reentry_plans[position_key] = replace(plan, reentry_amount=deteriorated_amount, updated_at=datetime.now(timezone.utc))

    def ensure_position_floats(self, position: Position) -> Position:
        numeric_fields = [ "entry_price", "mark_price", "positionAmt", "initial_quantity", "gain", "max_gain", "prev_gain", "max_quantity", "last_augmentation_amount", "last_augmentation_price", "last_reduction_amount", "last_reduction_price", "max_positionSize", "realized_pnl", "unrealized_pnl_USD" ]
        for field in numeric_fields:
            if hasattr(position, field):
                val = getattr(position, field)
                setattr(position, field, _safe_float(val, 0.0) if val is not None else 0.0)
        return position

    def validate_positions_floats(self) -> int:
        fixed = 0
        for position_key, position in list(self.positions.items()):
            if not position:
                continue
            before_snapshot = ( getattr(position, "entry_price", None), getattr(position, "mark_price", None), getattr(position, "positionAmt", None), getattr(position, "max_gain", None), getattr(position, "max_quantity", None) )
            self.ensure_position_floats(position)
            after_snapshot = ( getattr(position, "entry_price", None), getattr(position, "mark_price", None), getattr(position, "positionAmt", None), getattr(position, "max_gain", None), getattr(position, "max_quantity", None) )
            if before_snapshot != after_snapshot:
                fixed += 1
            try :
                account_key, _, _ = parse_position_key(position_key)
                self.positions_by_account.setdefault(account_key, {})[position_key] = position
            except Exception:
                continue
        return fixed

    def _validate_position_consistency(self, position, position_key: str = "") -> int:
        fixes = 0
        if getattr(position, 'was_reduced', False) and getattr(position, 'last_reduction_amount', 0) == 0:
            rd = self.reentry_data.get(position_key)
            if isinstance(rd, dict) and rd.get("reentry_amount", 0) > 0:
                position.last_reduction_amount = rd["reentry_amount"]; fixes += 1
            if isinstance(rd, dict) and rd.get("timestamp"):
                try: position.last_reduction_time = safe_datetime(rd["timestamp"]); fixes += 1
                except Exception: pass
            if position_key in self.reduced_positions and not getattr(position, 'last_reduction_time', None):
                position.last_reduction_time = self.reduced_positions[position_key]; fixes += 1
            if fixes > 0: logger.info(f"[CONSISTENCY_FIX][{position_key}] Recovered reduction fields for was_reduced=True position (fixes={fixes})")
        if getattr(position, 'is_reduced', False) and not getattr(position, 'reduced_at', None):
            if getattr(position, 'last_reduction_time', None):
                position.reduced_at = position.last_reduction_time; fixes += 1
        if getattr(position, 'last_reduction_time', None) and not getattr(position, 'was_reduced', False):
            if getattr(position, 'last_reduction_amount', 0) > 0:
                position.was_reduced = True; fixes += 1; logger.info(f"[CONSISTENCY_FIX][{position_key}] Set was_reduced=True based on existing reduction fields")
        if getattr(position, 'was_reduced', False) and not getattr(position, 'reduced_at', None) and getattr(position, 'last_reduction_time', None):
            position.reduced_at = position.last_reduction_time; fixes += 1
        if getattr(position, 'last_augmentation_time', None) and getattr(position, 'last_augmentation_amount', 0) == 0:
            logger.warning(f"[CONSISTENCY_WARN][{position_key}] last_augmentation_time set but amount=0")
        return fixes

    async def update_existing_position(self, position_key: str, current_price: float, gain: float, api_positionAmt_abs: float) -> None:
        now = datetime.now(timezone.utc)
        account_key, _, _ = parse_position_key(position_key)
        async with self._positions_lock:
            position = self.positions.get(position_key)
            if not position:
                return
            position.gain = gain
            if api_positionAmt_abs > 0:
                position.max_gain = max(gain, position.max_gain)
            position.max_quantity = max(api_positionAmt_abs, self.deteriorate_max_quantity(account_key, position, current_price))
            position.last_updated = now
        qty = _safe_float(api_positionAmt_abs)
        if qty:
            if (position.position_side or "").upper() == "LONG":
                position.unrealized_pnl_USD = (current_price - position.entry_price) * qty
            else:
                position.unrealized_pnl_USD = (position.entry_price - current_price) * qty
        else:
            position.unrealized_pnl_USD = None
            self.positions[position_key] = position
            try :
                account_key, _, _ = parse_position_key(position_key)
                self.positions_by_account.setdefault(account_key, {})[position_key] = position
            except Exception:
                pass
        self._mark_positions_dirty()

    async def ensure_position(self, account_key: str, symbol: str, position_side: str) -> Optional[Position]:
        return
        position_key = construct_position_key(account_key, symbol, position_side)
        position = self.positions.get(position_key)
        if position: return position
        else: return await self._restore_position(account_key, symbol, position_side)

    async def _restore_position(self, account_key: str, symbol: str, position_side: str) -> Optional[Position]:
        return
        account_dir = self._account_path(account_key)
        backup_dir = account_dir / "backups"
        pattern = f"{position_side.lower()}_positions*.json"
        search_pat = os.path.join(str(backup_dir), pattern) 
        candidates = sorted(glob.glob(search_pat), key=os.path.getmtime, reverse=True)
        for candidate in candidates:
            payload = await self._load_json(candidate)
            if not isinstance(payload, dict): continue
            symbol_payload = payload.get(symbol)
            if not symbol_payload: continue
            position = Position.from_dict({**symbol_payload, "symbol": symbol, "position_side": position_side})
            self.positions[construct_position_key(account_key, symbol, position_side)] = position
            self.positions_by_account.setdefault(account_key, {})[construct_position_key(account_key, symbol, position_side)] = position
            return position
        for other in list(self.accounts.keys()):
            if other == account_key: continue
            other_backup = self._account_path(other) / "backups"
            candidates = sorted(other_backup.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            for candidate in candidates:
                payload = await self._load_json(candidate)
                if not isinstance(payload, dict): continue
                symbol_payload = payload.get(symbol)
                if not symbol_payload: continue
                position = Position.from_dict({**symbol_payload, "symbol": symbol, "position_side": position_side})
                self.positions[construct_position_key(account_key, symbol, position_side)] = position
                self.positions_by_account.setdefault(account_key, {})[construct_position_key(account_key, symbol, position_side)] = position
                return position
        return None

    async def _ensure_price(self, symbol: str) -> float:
        try :
            return coerce_price_value(await get_current_price(symbol))
        except Exception:
            return 0.0

    def mark_augmented(self, position_key: str, timestamp: Optional[datetime] = None) -> None:
        account_key = self._parse_account_from_key(position_key)
        if not account_key:
            logger.error(f"[augmented_positions] Cannot mark, invalid key: {position_key}")
            return
        ts = ensure_tz(timestamp or datetime.now(timezone.utc))
        self.augmented_positions[position_key] = ts
        self._schedule_task(self.save_augmented_positions(account_key))
        if position_key in self.reduced_positions:
            self.reduced_positions.pop(position_key, None)
            self._schedule_task(self.save_reduced_positions(account_key))

    def unmark_augmented(self, position_key: str) -> None:
        account_key = self._parse_account_from_key(position_key)
        if not account_key:
            logger.error(f"[augmented_positions] Cannot unmark, invalid key: {position_key}")
            return
        if position_key in self.augmented_positions:
            self.augmented_positions.pop(position_key, None)
            self._schedule_task(self.save_augmented_positions(account_key))

    def mark_reversed(self, position_key: str, timestamp: Optional[datetime] = None) -> None:
        account_key = self._parse_account_from_key(position_key)
        if not account_key:
            logger.error(f"[reversed_positions] Cannot mark, invalid key: {position_key}")
            return
        if account_key != "fin" or not self.config.REV_MODE:
            return
        ts = ensure_tz(timestamp or datetime.now(timezone.utc))
        self.reversed_positions[position_key] = ts
        self._schedule_task(self.save_reversed_positions(account_key))

    def unmark_reversed(self, position_key: str) -> None:
        account_key = self._parse_account_from_key(position_key)
        if not account_key:
            logger.error(f"[reversed_positions] Cannot unmark, invalid key: {position_key}")
            return
        if position_key in self.reversed_positions:
            self.reversed_positions.pop(position_key, None)
            self._schedule_task(self.save_reversed_positions(account_key))

    def mark_direct_high_gain(self, position_key: str, *, stop_price: Any = None, entry_price: Any = None, max_size: Any = None, position_side: Optional[str] = None, timestamp: Optional[datetime] = None, direct_order_ts: Optional[datetime] = None, in_flight_ts: Optional[datetime] = None) -> None:
        account_key = self._parse_account_from_key(position_key)
        if not account_key: return
        position = self.positions.get(position_key)
        if not position:
            position = self.positions_by_account.get(account_key, {}).get(position_key)
        pos_amt = abs(safe_fetch_float(getattr(position, "positionAmt", 0.0), 0.0))
        if pos_amt <= 0.0001:
            return
        existing = self.direct_high_gain_augmented.get(position_key, {})
        entry = { "timestamp": ensure_tz(timestamp or existing.get("timestamp") or datetime.now(timezone.utc)), "stop_price": stop_price if stop_price is not None else existing.get("stop_price"), "entry_price": entry_price if entry_price is not None else existing.get("entry_price"), "max_size": max_size if max_size is not None else existing.get("max_size"), "position_side": position_side if position_side is not None else existing.get("position_side"), }
        if direct_order_ts is not None: entry["direct_order_ts"] = direct_order_ts
        elif "direct_order_ts" in existing: entry["direct_order_ts"] = existing["direct_order_ts"]
        if in_flight_ts is not None: entry["in_flight_ts"] = in_flight_ts
        elif "in_flight_ts" in existing: entry["in_flight_ts"] = existing["in_flight_ts"]
        self.direct_high_gain_augmented[position_key] = entry
        self._schedule_task(self.save_direct_high_gain_augmented(account_key))

async def _check_recent_entry_protection(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    now = datetime.now(timezone.utc)
    RECENT_THRESHOLD_MIN = 15.0
    mins_since_open = minutes_since(position.opened_at, now)
    mins_since_aug = minutes_since(position.last_augmentation_time, now)
    if mins_since_aug < 10 and position.last_augmentation_price and position.last_augmentation_amount > 0:
        fail_buffer = current_price * 0.0015
        aug_fail_long = is_long and current_price < (position.last_augmentation_price - fail_buffer)
        aug_fail_short = not is_long and current_price > (position.last_augmentation_price + fail_buffer)
        if aug_fail_long or aug_fail_short:
            cut_qty = position.last_augmentation_amount
            ctx['logger'].warning(f"⚔️ [AUGMENT_FAIL_GUARD] {ctx['position_key']} Recent add failed. Price {current_price} crossed aug_price {position.last_augmentation_price}. Cutting {cut_qty}")
            return Signal(action='REDUCE', reason=f"AUGMENT_IMMEDIATE_FAIL (AugPrice: {position.last_augmentation_price:.2f})", conviction=100.0, reduction_amount=cut_qty)
    if mins_since_open < RECENT_THRESHOLD_MIN:
        dc_low_3m = indicators.get('dc_low_3m', 0)
        dc_high_3m = indicators.get('dc_high_3m', 0)
        if not dc_low_3m or not dc_high_3m:
            return None
        if is_long:
            if current_price >= dc_low_3m:
                return None 
            else:
                return Signal(action='CLOSE', reason=f"NEW_POS_BROKE_DC_LOW_3M ({dc_low_3m:.2f})", conviction=-100.0)
        else:
            if current_price <= dc_high_3m:
                return None 
            else:
                return Signal(action='CLOSE', reason=f"NEW_POS_BROKE_DC_HIGH_3M ({dc_high_3m:.2f})", conviction=-100.0)
    return None

async def _is_in_cooldown_period(ctx: dict, position) -> bool:
    """Check if position is in cooldown period"""
    now = datetime.now(timezone.utc)
    service = ctx['service']
    position_key = ctx['position_key']
    if position_key in service.recently_queued_signals:
        time_since_signal = now.timestamp() - service.recently_queued_signals[position_key]
        if time_since_signal < 20:
            return True
    if minutes_since(position.last_reduction_time, now) < 3:
        return True
    if minutes_since(position.last_augmentation_time, now) < 3:
        return True
    if minutes_since(position.opened_at, now) < 3:
        return True
    return False

async def _check_predefined_stop_levels(ctx: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    """Check predefined stop loss levels"""
    if not current_price or current_price <= 0:
        return None
    position_key = ctx['position_key']
    service = ctx['service']
    stop_levels = await safe_get_stop_levels_data(position_key, service)
    if not stop_levels:
        return None
    position_value = abs(position.positionAmt * current_price)
    config = ctx['config']
    symbol_min_qty = service.min_qty.get(position.symbol, 0.001)
    pos_min_qty = max(2 * config.MIN_POSITION_SIZE / current_price, symbol_min_qty * 1.2)
    for idx, stop_level in enumerate(stop_levels):
        level = stop_level.get('level') if isinstance(stop_level, dict) else getattr(stop_level, 'level', 0.0)
        if not level or level <= 0:
            continue
        target_qty = pos_min_qty
        if idx == 0 and position_value > 200:
            target_qty = max(target_qty, 200.0 / current_price)
        elif idx == 0 and position_value > 50:
            target_qty = max(target_qty, 50.0 / current_price)
        reduction_amt = max(0.0, abs(position.positionAmt) - target_qty)
        if reduction_amt <= 0:
            continue
        if level and ((is_long and current_price < level) or (not is_long and current_price > level)):
            action_type = 'PROFIT_TAKE' if ctx['gain'] > 0.5 else 'REDUCE'
            logger.info(f"[STOP_HIT] {position_key}: {action_type} at {level:.6f}, current={current_price:.6f}")
            return Signal( action=action_type, reason=f"STOP_LOSS_HIT level={level:.6f} amt={reduction_amt:.6f}", conviction=95.0 if is_long else -95.0, reduction_amount=reduction_amt, stop_levels=ctx.get('enforced_stop_levels') )
    return None

async def _check_immediate_reduction_triggers(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    """ Overhauled Trigger (REV 3): 1. Aggressive Profit Protection (Exit before loss). 2. Momentum Exhaustion checks. 3. Structural Breakdown detection. """
    try :
        config = ctx.get('config')
        if not config or not getattr(config, 'SERVICE_REDUCE', False): return None
        position_key = ctx.get('position_key')
        account_key = ctx.get('account_key')
        gain = safe_fetch_float(ctx.get('gain', 0.0), 0.0)
        max_gain = safe_fetch_float(getattr(position, 'max_gain', gain), gain)
        logger = ctx.get('logger', logging.getLogger())
        k_1m = safe_fetch_float(indicators.get('stoch_k_1m', 50))
        d_1m = safe_fetch_float(indicators.get('stoch_d_1m', 50))
        k_3m = safe_fetch_float(indicators.get('stoch_k_3m', 50))
        d_3m = safe_fetch_float(indicators.get('stoch_d_3m', 50))
        k_15m = safe_fetch_float(indicators.get('stoch_k_15m', 50))
        k_15m_prev = safe_fetch_float(indicators.get('stoch_k_15m_prev', 50))
        ha_3m = str(indicators.get('ha_3m', 'neutral')).lower()
        t_up_3m = bool(indicators.get('t_up_3m', False))
        low_3m_prev = safe_fetch_float(indicators.get('low_3m_prev', 0))
        high_3m_prev = safe_fetch_float(indicators.get('high_3m_prev', 0))
        k_3m_prev = safe_fetch_float(indicators.get('k_3m_prev', 50))
        low_3m = safe_fetch_float(indicators.get('low_3m', 0))
        high_3m = safe_fetch_float(indicators.get('high_3m', 0))
        dc_low_3m = safe_fetch_float(indicators.get('dc_low_3m', 0))
        dc_high_3m = safe_fetch_float(indicators.get('dc_high_3m', 0))
        k_1h = safe_fetch_float(indicators.get('stoch_k_1h', 50))
        if gain > 0.01:
            erosion = max_gain - gain
            should_kill_profit = False
            if max_gain > 0.4 and erosion > 0.15: should_kill_profit = True
            elif max_gain > 0.15 and erosion > 0.08: should_kill_profit = True
            elif gain < 0.04 and max_gain > 0.1: should_kill_profit = True 
            if should_kill_profit:
                return Signal(action='PROFIT_TAKE', reason=f"PROFIT_PROTECT_erosion_{erosion:.2f}%_peak_{max_gain:.2f}%", conviction=100.0)
            mom_flip = (is_long and (k_1m < d_1m or ha_3m == 'red')) or (not is_long and (k_1m > d_1m or ha_3m == 'green'))
            if mom_flip and gain < 0.12:
                return Signal(action='PROFIT_TAKE', reason=f"MOM_FLIP_WINNER_EXIT_{gain:.2f}%", conviction=90.0)
        if is_hedge_account(config, account_key) and gain < -0.05:
            return None 
        triggered = False
        reason = ""
        _delta_exit = ctx.get('delta_exit_ok', False) if isinstance(ctx, dict) else False
        _delta_tfs = ctx.get('delta_tfs', 0) if isinstance(ctx, dict) else 0
        _min_tf = getattr(config, 'DELTA_MIN_TF_FOR_ACTION', 2)
        _svc_gate = getattr(config, 'DELTA_SERVICE_REDUCE_GATE', False)
        if is_long:
            lower_low = low_3m > 0 and low_3m_prev > 0 and low_3m < low_3m_prev
            if (k_15m < k_15m_prev and lower_low and not t_up_3m):
                triggered, reason = True, "STRUCT_BREAK_L"
            elif (k_15m > 80 and (k_3m < d_3m or ha_3m == 'red')):
                triggered, reason = True, "MOM_EXHAUST_L"
            elif k_3m > 60 and k_3m < k_3m_prev and (k_15m > 80 or k_1h > 80) and lower_low:
                triggered, reason = True, "K3M_BOUNCE_TURN_L"
            elif dc_low_3m > 0 and current_price < dc_low_3m and k_3m < 30:
                triggered, reason = True, "K3M_REAL_DROP_DC"
        else:
            higher_high = high_3m > 0 and high_3m_prev > 0 and high_3m > high_3m_prev
            if (k_15m > k_15m_prev and higher_high and t_up_3m):
                triggered, reason = True, "STRUCT_BREAK_S"
            elif (k_15m < 20 and (k_3m > d_3m or ha_3m == 'green')):
                triggered, reason = True, "MOM_EXHAUST_S"
            elif k_3m < 40 and k_3m > k_3m_prev and (k_15m < 20 or k_1h < 20) and higher_high:
                triggered, reason = True, "K3M_BOUNCE_TURN_S"
            elif dc_high_3m > 0 and current_price > dc_high_3m and k_3m > 70:
                triggered, reason = True, "K3M_REAL_RISE_DC"
        if triggered:
            if _svc_gate and not _delta_exit and _delta_tfs < _min_tf and gain > -0.5:
                logger.info(f"[DELTA_GATE_HOLD] {position_key}: {reason} triggered but delta says HOLD (tfs={_delta_tfs}<{_min_tf}, exit={_delta_exit})")
                return None
            action_type = 'PROFIT_TAKE' if gain > 0.2 else 'REDUCE'
            _d_tag = f"_DELTA_CONFIRMED" if _delta_exit else ""
            logger.info(f"[AGGRESSIVE_STOP] {position_key}: {action_type} - {reason}{_d_tag}")
            return Signal(action=action_type, reason=f"IMMED_REDUCE_{reason}_{gain:.2f}%{_d_tag}", conviction=95.0, stop_levels=ctx.get('enforced_stop_levels'))
        return None
    except Exception as e:
        try : ctx.get('logger').error(f"CRITICAL ERROR in _check_immediate_reduction_triggers: {e}")
        except Exception: print(f"CRITICAL ERROR in _check_immediate_reduction_triggers: {e}")
        return None

async def _check_aggressive_exit_conditions(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    if not config.SERVICE_STOP:return
    return

async def _check_technical_stop_conditions(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    if not config.SERVICE_STOP:return
    """Check technical stop conditions based on indicators"""
    position_key = ctx['position_key']
    now = datetime.now(timezone.utc)
    if ctx['config'].VERBOSE_STOPS:
        logger.debug(f"[STOP_CHECK] {position_key}: Checking technical conditions - gain={ctx['gain']:.2f}% k_3m={indicators.get('stoch_k_3m', 0):.1f} k_15m={indicators.get('stoch_k_15m', 0):.1f} ha_3m={indicators.get('ha_3m', 'neutral')}")
    if 'SIMPLE' in position.augment_reason or 'FALL_BOUNCE_FALL' in position.augment_reason:
        dc_low4_3m = indicators.get('dc_low_3m', 0) or 0
        dc_high4_3m = indicators.get('dc_high_3m', 0) or 0
        if dc_low4_3m > 0 and dc_high4_3m > 0:
            simple_exit = (is_long and current_price < dc_low4_3m and current_price < position.last_augmentation_price) or (not is_long and current_price > dc_high4_3m and current_price > position.last_augmentation_price)
            if simple_exit:
                return _create_stop_signal(ctx, position, "SIMPLE_EXIT", is_long)
    high_3m = indicators.get('high_3m', 0) or 0
    high_3m_prev = indicators.get('high_3m_prev', 0) or 0
    low_3m = indicators.get('low_3m', 0) or 0
    low_3m_prev = indicators.get('low_3m_prev', 0) or 0
    higher_high = (high_3m or current_price) >= high_3m_prev and low_3m >= low_3m_prev if high_3m_prev > 0 and low_3m_prev > 0 else False
    lower_low = high_3m <= high_3m_prev and (low_3m or current_price) <= low_3m_prev if high_3m_prev > 0 and low_3m_prev > 0 else False
    time_since_augment = minutes_since(position.last_augmentation_time, now)
    ha_3m = indicators.get('ha_3m', 'neutral') or 'neutral'
    if time_since_augment < 21 and time_since_augment > 4 and ((is_long and ha_3m == 'red' and lower_low) or (not is_long and ha_3m == 'green' and higher_high)):
        return _create_stop_signal(ctx, position, "AGGRESSIVE_HA_EXIT", is_long)
    if time_since_augment < 12 and time_since_augment > 4:
        stoch_k_3m = indicators.get('stoch_k_3m', 50) or 50
        k_3m_prev = indicators.get('k_3m_prev', 50) or 50
        atr_3m = indicators.get('atr_3m', 0) or 0
        atr_3m_prev = indicators.get('atr_3m_prev', 0) or 0
        k_atr_exit = (is_long and stoch_k_3m < k_3m_prev and atr_3m < atr_3m_prev) or (not is_long and stoch_k_3m > k_3m_prev and atr_3m < atr_3m_prev)
        if k_atr_exit:
            return _create_stop_signal(ctx, position, "AGGRESSIVE_K_ATR_EXIT", is_long)
    return None

async def _check_signal_driven_exits(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    """Check signal-driven exit conditions"""
    signal_data = ctx.get('signal_data')
    if not signal_data:
        return None
    event_type = signal_data.get('event_type', '')
    position_key = ctx['position_key']
    now = datetime.now(timezone.utc)
    time_since_augment = minutes_since(position.last_augmentation_time, now)
    if ('dc_low_crossunder' in event_type and is_long) or ('dc_high_crossover' in event_type and not is_long):
        return _create_stop_signal(ctx, position, f"SIGNAL_DRIVEN_DC_{'LOW' if is_long else 'HIGH'}_STOP", is_long)
    stoch_k_15m = indicators.get('stoch_k_15m', 50) or 50
    stoch_d_15m = indicators.get('stoch_d_15m', 50) or 50
    stoch_k_3m = indicators.get('stoch_k_3m', 50) or 50
    stoch_d_3m = indicators.get('stoch_d_3m', 50) or 50
    stoch_k_1m = indicators.get('stoch_k_1m', 50) or 50
    stoch_d_1m = indicators.get('stoch_d_1m', 50) or 50
    stoch_k_1h = indicators.get('stoch_k_1h', 50) or 50
    stoch_d_1h = indicators.get('stoch_d_1h', 50) or 50
    stoch_k_4h = indicators.get('stoch_k_4h', 50) or 50
    stoch_d_4h = indicators.get('stoch_d_4h', 50) or 50
    stoch_k_D = indicators.get('stoch_k_D', 50) or 50
    stoch_d_D = indicators.get('stoch_d_D', 50) or 50
    ha_D = indicators.get('ha_D', 'neutral')

    if is_long:
        # Exit LONG requires checking SHORT entry conditions
        ltf_aligned = sum([stoch_k_1m < stoch_d_1m, stoch_k_3m < stoch_d_3m, stoch_k_15m < stoch_d_15m]) >= 2
        htf_aligned = sum([stoch_k_1h < stoch_d_1h, stoch_k_4h < stoch_d_4h, ha_D == 'red' or stoch_k_D < stoch_d_D]) >= 2
        stoch_ok = (stoch_k_15m < 50 and stoch_k_3m < stoch_d_3m) or (stoch_k_15m > 70 and stoch_k_3m < stoch_d_3m)  # bearish 15m OR overbought extreme
        rsi_1h_le = float(indicators.get('rsi_1h', 50) or 50)
        rsi_exit_ok = rsi_1h_le > 50  # exit longs when RSI confirms overbought (1h RSI > 50)
        is_long_exit_zone = stoch_ok and ltf_aligned and htf_aligned and rsi_exit_ok
        is_short_exit_zone = False
    else:
        # Exit SHORT requires checking LONG entry conditions
        ltf_aligned = sum([stoch_k_1m > stoch_d_1m, stoch_k_3m > stoch_d_3m, stoch_k_15m > stoch_d_15m]) >= 2
        htf_aligned = sum([stoch_k_1h > stoch_d_1h, stoch_k_4h > stoch_d_4h, ha_D == 'green' or stoch_k_D > stoch_d_D]) >= 2
        stoch_ok = (stoch_k_15m > 50 and stoch_k_3m > stoch_d_3m) or (stoch_k_15m < 30 and stoch_k_3m > stoch_d_3m)
        rsi_short_exit_ok = float(indicators.get('rsi_1h', 50) or 50) < 50  # exit shorts when RSI falls below 50
        is_short_exit_zone = stoch_ok and ltf_aligned and htf_aligned and rsi_short_exit_ok
        is_long_exit_zone = False

    if (('wt_crossunder' in event_type or 'stoch_crossunder' in event_type) and is_long and is_long_exit_zone) or (('wt_crossover' in event_type or 'stoch_crossover' in event_type) and not is_long and is_short_exit_zone):
        return _create_stop_signal(ctx, position, f"SIGNAL_DRIVEN_WT_{'OVERBOUGHT' if is_long else 'OVERSOLD'}_STOP", is_long)
    wt_signal_3m = indicators.get('wt_signal_3m', '') or ''
    if ('wt_signal_3m' in event_type and ((is_long and wt_signal_3m == 'SELL' and (time_since_augment < 12 or stoch_k_15m > 90)) or (not is_long and wt_signal_3m == 'BUY' and (time_since_augment < 12 or stoch_k_15m < 10)))):
        return _create_stop_signal(ctx, position, f"SIGNAL_DRIVEN_WT_3M_{'BEARISH' if is_long else 'BULLISH'}_EXIT", is_long)
    return None

async def _check_critical_price_levels(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    """Check critical price level breaches"""
    position_key = ctx['position_key']
    if ctx['config'].VERBOSE_STOPS:
        logger.debug(f"[STOP_CHECK] {position_key}: Checking critical price levels - price={current_price:.6f} dc_low_3m={indicators.get('dc_low_3m', 0):.6f} dc_high_3m={indicators.get('dc_high_3m', 0):.6f}")
    dc_low_3m = indicators.get('dc_low_3m', 0) or 0
    dc_high_3m = indicators.get('dc_high_3m', 0) or 0
    dc_low_15m = indicators.get('dc_low_15m', 0) or 0
    dc_high_15m = indicators.get('dc_high_15m', 0) or 0
    if dc_low_3m > 0 and dc_high_3m > 0:
        if (is_long and current_price < dc_low_3m) or (not is_long and current_price > dc_high_3m):
            return _create_stop_signal(ctx, position, f"CRITICAL_DC_3M_{'LOW' if is_long else 'HIGH'}_STOP", is_long)
    if dc_low_15m > 0 and dc_high_15m > 0:
        if (is_long and current_price < dc_low_15m) or (not is_long and current_price > dc_high_15m):
            return _create_stop_signal(ctx, position, f"CRITICAL_DC_15M_{'LOW' if is_long else 'HIGH'}_STOP", is_long)
    dc_high4_15m = indicators.get('dc_high4_15m', 0) or 0
    dc_low4_15m = indicators.get('dc_low4_15m', 0) or 0
    prev_price = indicators.get('prev_price', 0) or 0
    if dc_low4_15m > 0 and dc_high4_15m > 0 and prev_price > 0:
        if (is_long and current_price < dc_low4_15m and prev_price >= dc_low4_15m) or (not is_long and current_price > dc_high4_15m and prev_price <= dc_high4_15m):
            return _create_stop_signal(ctx, position, f"{'UNDER' if is_long else 'OVER'}_1H_LEVEL", is_long)
    return None

async def _check_proactive_profit_taking(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    i = indicators
    now = datetime.now(timezone.utc)
    time_since_augment = minutes_since(position.last_augmentation_time, now)
    time_since_open = minutes_since(position.opened_at, now)
    time_since_reduction = minutes_since(position.last_reduction_time, now)
    if (ctx['gain'] > 0.5 and time_since_augment >= 12 and time_since_open >= 5 and time_since_reduction >= 15):
        wt1_3m = indicators.get('wt1_3m', 0) or 0
        wt2_3m = indicators.get('wt2_3m', 0) or 0
        ha_3m = indicators.get('ha_3m', 'neutral') or 'neutral'
        stoch_k_3m = indicators.get('stoch_k_3m', 50) or 50
        dc_basis_3m = indicators.get('dc_basis_3m', 0) or 0
        dc_basis_3m_ant = indicators.get('dc_basis_3m_ant', 0) or 0
        wt_reversing = (wt1_3m < wt2_3m) if is_long else (wt1_3m > wt2_3m)
        ha_flipped = (ha_3m == 'red') if is_long else (ha_3m == 'green')
        stoch_extreme = (stoch_k_3m > 90) if is_long else (stoch_k_3m < 10)
        price_weakness = (current_price < dc_basis_3m or dc_basis_3m_ant > dc_basis_3m) if is_long else (current_price > dc_basis_3m or dc_basis_3m_ant < dc_basis_3m)
        if ha_flipped and (wt_reversing or stoch_extreme or price_weakness):
            triggers = []
            if wt_reversing: triggers.append("WT_REVERSAL")
            if ha_flipped: triggers.append("HA_FLIP")
            if stoch_extreme: triggers.append("STOCH_EXTREME")
            if price_weakness: triggers.append("PRICE_WEAKNESS")
            return _create_stop_signal(ctx, position, f"PROACTIVE_EXIT_{'_'.join(triggers)}", is_long, action_type='PROFIT_TAKE')
    return None

async def _check_trailing_stops(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    if not current_price or current_price <= 0:
        return None
    i = indicators
    now = datetime.now(timezone.utc)
    time_since_augment = minutes_since(position.last_augmentation_time, now)

    if ctx['gain'] >= 3.0:
        return Signal(action='AUGMENT', reason=f"MAX_GAIN_3PCT_AUGMENT_{ctx['gain']:.2f}%", conviction=100.0)
    elif position.max_gain >= 3.0 and ctx['gain'] < 2.5:
        return Signal(action='REDUCE', reason=f"GAIN_PROTECTION_2.5PCT_REDUCE_{ctx['gain']:.2f}%", conviction=100.0)
    elif position.max_gain >= 2.5 and ctx['gain'] <= 2.0:
        is_unaugmented_3pct = (position.max_gain >= 3.0 and time_since_augment > 10)
        reason = f"GAIN_PROTECTION_2.0PCT_CLOSE_{ctx['gain']:.2f}%"
        if is_unaugmented_3pct: reason += "_NO_AUG_SELL"
        return Signal(action='PROFIT_TAKE', reason=reason, conviction=100.0)

    # DC Break Rule for Augmented Positions - Only if in profit AND NOT HEDGED
    symbol_min_qty = ctx['service'].min_qty.get(position.symbol, 0.001)
    pos_min_qty = max(ctx['config'].MIN_POSITION_SIZE / current_price, symbol_min_qty * 1.2)
    is_augmented = position.positionAmt > 1.2 * pos_min_qty
    dc_l3 = i.get('dc_low_3m', 0)
    dc_h3 = i.get('dc_high_3m', 999999)
    dc_broken = (is_long and current_price < dc_l3) or (not is_long and current_price > dc_h3)
    
    # Check for active hedge
    already_hedged = False
    if ctx['config'].HEDGE_MODE:
        async with ctx['service'].managed_stop_registry_lock if hasattr(ctx['service'], 'managed_stop_registry_lock') else asyncio.Lock():
            # Simplest check is via the tracker if available, but in service script we often check attributes
            if hasattr(position, 'is_hedged') and position.is_hedged: already_hedged = True

    if is_augmented and dc_broken and ctx['gain'] > 0.1 and not already_hedged:
        return Signal(action='REDUCE', reason=f"AUGMENTED_DC_BREAK_REDUCE_TO_MIN_{ctx['gain']:.2f}%", conviction=100.0, reduction_amount=max(0, position.positionAmt - pos_min_qty))

    if position.max_gain > 0.25 and time_since_augment >= 9:
        if ctx['gain'] < 0.07:
            return _create_stop_signal(ctx, position, "BREAKEVEN_GUARD_EXIT", is_long) 
        trailing_stop_level = position.max_gain - 1.0
        if ctx['gain'] < trailing_stop_level:
            action_type = 'PROFIT_TAKE' if ctx['gain'] > 0.5 else 'REDUCE'
            return _create_stop_signal(ctx, position, "TRAILING_STOP_HIT", is_long) 
    dc_basis_crossunder_3m = indicators.get('dc_basis_crossunder_3m', False) or False
    dc_basis_crossunder_15m = indicators.get('dc_basis_crossunder_15m', False) or False
    sma_crossunder_15m = indicators.get('sma_crossunder_15m', False) or False
    dc_basis_crossover_3m = indicators.get('dc_basis_crossover_3m', False) or False
    dc_basis_crossover_15m = indicators.get('dc_basis_crossover_15m', False) or False
    sma_crossover_15m = indicators.get('sma_crossover_15m', False) or False
    cross = (is_long and (dc_basis_crossunder_3m or dc_basis_crossunder_15m or sma_crossunder_15m)) or (not is_long and (dc_basis_crossover_3m or dc_basis_crossover_15m or sma_crossover_15m))
    if cross:
        _d_exit = ctx.get('delta_exit_ok', False)
        _d_tfs = ctx.get('delta_tfs', 0)
        if getattr(config, 'DELTA_SERVICE_TRAILING_STOP', False) and not _d_exit and _d_tfs < getattr(config, 'DELTA_MIN_TF_FOR_ACTION', 2) and ctx['gain'] > -0.3:
            return None
        return _create_stop_signal(ctx, position, f"SMA_DC_CROSS{'_DELTA' if _d_exit else ''}", is_long)
    return None

async def _check_account_specific_stops(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    """Check account-specific stop conditions"""
    account_key = ctx['account_key']
    position_key = ctx['position_key']
    if account_key in ['ang', 'inf', 'flz', 'men', 'fin']:
        dc_low_3m = indicators.get('dc_low_3m', 0) or 0
        dc_high_3m = indicators.get('dc_high_3m', 0) or 0
        stoch_k_15m = indicators.get('stoch_k_15m', 50) or 50
        ha_3m = indicators.get('ha_3m', 'neutral') or 'neutral'
        small_gain_condition = ( (is_long and ((dc_low_3m > 0 and current_price < dc_low_3m) or (stoch_k_15m > 75 and position.positionAmt * current_price > 1.5 * ctx['config'].START_POSITION_SIZE and ctx['gain'] < 0.25)) and ha_3m == 'red') or (not is_long and ((dc_high_3m > 0 and current_price > dc_high_3m) or (stoch_k_15m < 25 and position.positionAmt * current_price > ctx['config'].START_POSITION_SIZE and ctx['gain'] < 0.25)) and ha_3m == 'green'))
        if small_gain_condition:
            _d_exit = ctx.get('delta_exit_ok', False)
            _d_tfs = ctx.get('delta_tfs', 0)
            if getattr(config, 'DELTA_SERVICE_REDUCE_GATE', False) and not _d_exit and _d_tfs < getattr(config, 'DELTA_MIN_TF_FOR_ACTION', 2):
                return None
            return _create_stop_signal(ctx, position, f"NO_GAIN_SAFETY_NET{'_DELTA' if _d_exit else ''}", is_long)
    return None

async def _check_atr_stops(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    """Check ATR-based stop loss conditions"""
    atr_15m = indicators.get('atr_15m', 0) or 0
    if not atr_15m or atr_15m <= 0:
        return None
    stop_distance = 1.5 * atr_15m
    entry_price = position.entry_price
    if not entry_price or entry_price <= 0:
        return None
    if is_long:
        stop_price = entry_price - stop_distance
        if current_price <= stop_price:
            return _create_stop_signal(ctx, position, "ATR_STOP_LOSS", is_long)
    else:
        stop_price = entry_price + stop_distance
        if current_price >= stop_price:
            action_type = 'PROFIT_TAKE' if ctx['gain'] > 0.5 else 'REDUCE'
            return Signal(action=action_type, reason=f"ATR_STOP_LOSS (Stop: {stop_price:.4f})", conviction=95.0 if is_long else -95.0)
    return None

async def _check_momentum_stops(ctx: dict, indicators: dict, position, current_price: float, is_long: bool) -> Optional[Signal]:
    """Check momentum-based stop conditions"""
    if not current_price or current_price <= 0:
        return None
    signal_data = ctx.get('signal_data')
    if not signal_data:
        return None
    event_type = signal_data.get('event_type', '')
    momentum_signals = ['short_term_winners_up_rank', 'short_term_winners_down_rank', 'short_term_losers_jump_rank', 'short_term_losers_drop_rank', 'long_term_winners_up_rank', 'long_term_winners_down_rank', 'long_term_losers_jump_rank', 'long_term_losers_drop_rank']
    if any(signal in event_type for signal in momentum_signals):
        try :
            rank_str = event_type.split('rank')[-1]
            rank = int(rank_str)
            if position.positionAmt > 2 * max(ctx['config'].START_POSITION_SIZE / current_price, 1.2 * ctx['service'].min_qty.get(ctx['symbol'], 0.001)):
                now = datetime.now(timezone.utc)
                time_since_augment = minutes_since(position.last_augmentation_time, now)
                stoch_k_15m = indicators.get('stoch_k_15m', 50) or 50
                stoch_k_3m = indicators.get('stoch_k_3m', 50) or 50
                stoch_d_3m = indicators.get('stoch_d_3m', 50) or 50
                stoch_d_15m = indicators.get('stoch_d_15m', 50) or 50
                dc_basis_3m_ant = indicators.get('dc_basis_3m_ant', 0) or 0
                dc_basis_3m = indicators.get('dc_basis_3m', 0) or 0
                if (is_long and 'winners' in event_type and 'down' in event_type and rank > 15 and (time_since_augment < 12 or stoch_k_15m > 90 or dc_basis_3m_ant < dc_basis_3m or stoch_k_3m < stoch_d_3m or stoch_k_15m < stoch_d_15m)):
                    return _create_stop_signal(ctx, position, f"MOMENTUM_WINNER_DROPPED_RANK{rank}", is_long)
                if (not is_long and 'losers' in event_type and 'up' in event_type and rank > 15 and (time_since_augment < 12 or stoch_k_15m < 10 or dc_basis_3m_ant > dc_basis_3m or stoch_k_3m > stoch_d_3m or stoch_k_15m > stoch_d_15m)):
                    return _create_stop_signal(ctx, position, f"MOMENTUM_LOSER_JUMPED_RANK{rank}", is_long)
        except (ValueError, IndexError):
            pass
    return None

def _create_stop_signal(ctx: dict, position, reason: str, is_long: bool, action_type: str = None) -> Signal:
    """Create a standardized stop loss signal"""
    if action_type is None:
        action_type = 'PROFIT_TAKE' if ctx['gain'] > 0.5 else 'REDUCE'
    position_key = ctx['position_key']
    logger = ctx['logger']
    if ctx['config'].VERBOSE_STOPS:
        logger.info(f"[STOP_SIGNAL] {position_key}: {action_type} - {reason}")
    return Signal( action=action_type, reason=reason, conviction=95.0 if is_long else -95.0, stop_levels=ctx.get('enforced_stop_levels') )

async def evaluate_master_stop_loss(ctx: dict) -> Optional[Signal]:
    """Master Stop Loss logic: Prohibits loss-closure for hedge accounts."""
    config = ctx['config']
    if not config.SERVICE_STOP: return None
    account_key = ctx['account_key']
    position_key = ctx['position_key']
    gain = safe_fetch_float(ctx.get('gain', 0.0), 0.0)
    is_long = ctx['position_side'] == "LONG"
    current_price = ctx['current_price']
    indicators = ctx['indicators']
    service = ctx['service']
    if is_hedge_account(config, account_key) and gain < 0.0:
        return None
    if not current_price or current_price <= 0:
        return None
    pos_min_qty = max(config.MIN_POSITION_SIZE / current_price, service.min_qty.get(ctx['symbol'], 0.001))
    tier_mult = calculate_tier_mult(indicators, is_long)
    retention_qty = max(1, tier_mult) * pos_min_qty
    position = service.positions.get(position_key)
    if position and position.positionAmt <= retention_qty * 1.05:
        return None
    _dc_low_k, _dc_high_k, _, _, _ = config.get_stop_indicator_keys(account_key)
    dc_low_stop = safe_fetch_float(indicators.get(_dc_low_k, indicators.get('dc_low_3m')), 0.0)
    dc_high_stop = safe_fetch_float(indicators.get(_dc_high_k, indicators.get('dc_high_3m')), 0.0)
    if (is_long and current_price < dc_low_stop) or (not is_long and current_price > dc_high_stop):
        if gain > 0.1:
            reduction = max(0.0, abs(position.positionAmt) - retention_qty) if position else 0.0
            return Signal('REDUCE', f"CRITICAL_LEVEL_BREACH_PROFIT_{gain:.2f}%", 95.0, reduction_amount=reduction)
    return None
    atr_15m = indicators.get('atr_15m',0)
    if atr_15m and atr_15m > 0:
        stop_dist = 1.5 * atr_15m
        stop_price = position.entry_price - stop_dist if is_long else position.entry_price + stop_dist
        if (is_long and current_price <= stop_price) or (not is_long and current_price >= stop_price):
            return Signal('REDUCE', f"ATR_STOP", 95.0 if is_long else -95.0)
    return None 

async def evaluate_reversal_exit(ctx: dict) -> Optional[Signal]:
    logger = ctx['logger']
    now = datetime.now(timezone.utc)
    config = ctx['config']
    position_key = ctx['position_key']
    is_long = ctx['position_side'] == 'LONG'
    service = ctx['service']
    original_key = ctx['position_key']
    position = service.positions[position_key]
    current_price = float(position.mark_price) if position and hasattr(position, 'mark_price') and position.mark_price > 0 else await quick_price(ctx['symbol']) 
    gain = ctx['gain']
    account_key = ctx['account_key']
    symbol = ctx['symbol']
    position_side = ctx['position_side']
    reversed_side = "LONG" if position_side == "SHORT" else "SHORT"
    reversed_position_key = construct_position_key(account_key, symbol, reversed_side)
    reversed_position = service.positions_by_account[account_key].get(reversed_position_key)
    if config.VERBOSE_STOPS:
        logger.info(f"[REVERSAL_EXIT_INDICATORS] {position_key}: price={current_price:.6f} gain={gain:.2f}% positionAmt={position.positionAmt:.6f}")
    if minutes_since(position.last_reduction_time, now) < 6:
        if config.VERBOSE_STOPS:
            logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: None - Recent reduction cooldown minutes_since_reduction={minutes_since(position.last_reduction_time, now):.1f}min < 6min")
        return None
    if minutes_since(position.last_augmentation_time, now) < 6:
        if config.VERBOSE_STOPS:
            logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: None - Recent augmentation cooldown minutes_since_augmentation={minutes_since(position.last_augmentation_time, now):.1f}min < 6min")
        return None
    if minutes_since(position.opened_at, now) < 5:
        if config.VERBOSE_STOPS:
            logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: None - Position too new minutes_since_opened={minutes_since(position.opened_at, now):.1f}min < 5min")
        return None
    conviction = 95.0 if is_long else -95.0
    reasons = []
    rev_enabled = account_key == 'fin' and config.REV_MODE
    if not rev_enabled:
        return None
    if reversed_position_key not in service.reversed_positions:
        if config.VERBOSE_STOPS:
            logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: None - No reversed position exists reversed_key={reversed_position_key}")
        return None
    i = ii(ctx['service'], ctx['symbol'])
    if account_key == 'fin' and config.REV_MODE and reversed_position and reversed_position.positionAmt > 0:
        k_1m = i.get('stoch_k_1m', 50); d_1m = i.get('stoch_d_1m', 50)
        k_3m = i.get('stoch_k_3m', 50); d_3m = i.get('stoch_d_3m', 50)
        k_15m = i.get('stoch_k_15m', 50); d_15m = i.get('stoch_d_15m', 50)
        k_1h = i.get('stoch_k_1h', 50); d_1h = i.get('stoch_d_1h', 50)
        k_4h = i.get('stoch_k_4h', 50); d_4h = i.get('stoch_d_4h', 50)
        k_D = i.get('stoch_k_D', 50); d_D = i.get('stoch_d_D', 50)
        ha_D = i.get('ha_D', 'neutral')

        # Since this is an exit, we use the inverse condition
        if is_long:
            ltf_aligned = sum([k_1m < d_1m, k_3m < d_3m, k_15m < d_15m]) >= 2
            htf_aligned = sum([k_1h < d_1h, k_4h < d_4h, ha_D == 'red' or k_D < d_D]) >= 2
            stoch_condition = (k_15m < 50 and k_3m < d_3m) or (k_15m > 70 and k_3m < d_3m)  # bearish 15m OR overbought extreme
        else:
            ltf_aligned = sum([k_1m > d_1m, k_3m > d_3m, k_15m > d_15m]) >= 2
            htf_aligned = sum([k_1h > d_1h, k_4h > d_4h, ha_D == 'green' or k_D > d_D]) >= 2
            stoch_condition = (k_15m > 50 and k_3m > d_3m) or (k_15m < 30 and k_3m > d_3m)

        if not ltf_aligned or not htf_aligned:
            stoch_condition = False

        if k_3m is not None and d_3m is not None:
            prev_gain_check_valid_rev_exit = False
            prev_gain_rev_exit = 0.0
            if position and hasattr(position, 'prev_gain_last_updated') and position.prev_gain_last_updated:
                age_seconds = (datetime.now(timezone.utc) - position.prev_gain_last_updated).total_seconds()
                if age_seconds < 480.0:
                    prev_gain_rev_exit = safe_fetch_float(getattr(position, 'prev_gain', 0.0), 0.0)
                    if gain != 0.0 or prev_gain_rev_exit != 0.0:
                        prev_gain_check_valid_rev_exit = True
            if not prev_gain_check_valid_rev_exit:
                indicator_price_rev_exit = safe_fetch_float(i.get('current_price', 0), current_price)
                if indicator_price_rev_exit and indicator_price_rev_exit > 0:
                    price_deteriorated_rev_exit = (is_long and current_price < indicator_price_rev_exit) or (not is_long and current_price > indicator_price_rev_exit)
                else:
                    price_deteriorated_rev_exit = False 
            else:
                price_deteriorated_rev_exit = gain < prev_gain_rev_exit
            gain_condition = (price_deteriorated_rev_exit and gain < 0.5)
            if stoch_condition and gain_condition:
                if config.VERBOSE_STOPS:
                    logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: REVERSE_CLOSE - FIN account hedge exit condition met k_3m={k_3m:.1f} d_3m={d_3m:.1f} gain={gain:.2f}% prev_gain={position.prev_gain:.2f}%")
                return Signal(action='REVERSE_CLOSE', reason=f'FIN_HEDGE_EXIT_k_3mm<d3m={k_3m:.1f}<{d_3m:.1f}__gain={gain:.2f}%<prev={position.prev_gain:.2f}%', conviction=95.0 if is_long else -95.0)
    if reversed_position and reversed_position.positionAmt > 0:
        current_reversed_amt = reversed_position.positionAmt
        current_original_amt = position.positionAmt
        current_gain = ctx['gain']
        reversed_gain = reversed_position.gain if hasattr(reversed_position, 'gain') else 0
        reversed_prev_gain = getattr(reversed_position, 'prev_gain', 0.0)
        reversed_gain_rising = reversed_gain > reversed_prev_gain + 0.05
        if reversed_gain_rising and config.VERBOSE_STOPS:
            logger.info(f"[REVERSAL_EXIT_BLOCKED] {position_key}: Reversed position gain rising ({reversed_gain:.2f}% > {reversed_prev_gain:.2f}%) - BLOCKING ALL REVERSE EXITS")
        prev_gain_valid_rev_exit_comp = False
        prev_gain_rev_exit_comp = 0.0
        if position and hasattr(position, 'prev_gain_last_updated') and position.prev_gain_last_updated:
            age_seconds = (datetime.now(timezone.utc) - position.prev_gain_last_updated).total_seconds()
            if age_seconds < 480.0:
                prev_gain_rev_exit_comp = safe_fetch_float(getattr(position, 'prev_gain', 0.0), 0.0)
                if current_gain != 0.0 or prev_gain_rev_exit_comp != 0.0:
                    prev_gain_valid_rev_exit_comp = True
        if not reversed_gain_rising and current_reversed_amt > current_original_amt and (prev_gain_valid_rev_exit_comp and current_gain > prev_gain_rev_exit_comp):
            reduce_amount = current_reversed_amt - current_original_amt
            reason = f"REVERSE_REDUCE_qty_{reduce_amount:.4f}_(Original:{current_original_amt:.4f}, reversed:{current_reversed_amt:.4f}, Strengthening:{current_gain:.2f}%>{reversed_gain:.2f}%)"
            if config.VERBOSE_STOPS:
                logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: REVERSE_REDUCE - Reversed position larger and strengthening current_reversed_amt={current_reversed_amt:.6f} current_original_amt={current_original_amt:.6f} current_gain={current_gain:.2f}% prev_gain={position.prev_gain:.2f}% reduce_amount={reduce_amount:.6f} conviction={conviction:.2f}")
            return Signal(action='REVERSE_REDUCE', reason=f"{reason} | {', '.join(reasons)}", conviction=95.0 if is_long else -95.0)
    signal_data = ctx.get('signal_data')
    if signal_data:
        event_type = signal_data.get('event_type', '')
        reversed_gain = reversed_position.gain if hasattr(reversed_position, 'gain') else 0 if reversed_position else 0
        reversed_prev_gain = getattr(reversed_position, 'prev_gain', 0.0) if reversed_position else 0.0
        reversed_gain_rising = reversed_gain > reversed_prev_gain + 0.05
        if reversed_gain_rising:
            if config.VERBOSE_STOPS:
                logger.info(f"[REVERSAL_EXIT_BLOCKED] {position_key}: Reversed position gain rising ({reversed_gain:.2f}% > {reversed_prev_gain:.2f}%) - BLOCKING WT SIGNAL REVERSE_CLOSE")
            return None
        if 'wt_crossunder' in event_type and is_long:
            if config.VERBOSE_STOPS:
                logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: REVERSE_CLOSE - WT crossunder signal for long position event_type={event_type} conviction={conviction:.2f}")
            return Signal(action='REVERSE_CLOSE', reason=f'SIGNAL_DRIVEN_WT_BULLISH_REVERSAL_EXIT | {", ".join(reasons)}', conviction=95.0 if is_long else -95.0)
        elif 'wt_crossover' in event_type and not is_long:
            if config.VERBOSE_STOPS:
                logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: REVERSE_CLOSE - WT crossover signal for short position event_type={event_type} conviction={conviction:.2f}")
            return Signal(action='REVERSE_CLOSE', reason=f'SIGNAL_DRIVEN_WT_BEARISH_REVERSAL_EXIT | {", ".join(reasons)}', conviction=95.0 if is_long else -95.0)
    reversal_timestamp = service.reversed_positions[reversed_position_key]
    reversed_gain = reversed_position.gain if hasattr(reversed_position, 'gain') else 0 if reversed_position else 0
    reversed_prev_gain = getattr(reversed_position, 'prev_gain', 0.0) if reversed_position else 0.0
    reversed_gain_rising = reversed_gain > reversed_prev_gain + 0.05
    if reversed_gain_rising:
        if config.VERBOSE_STOPS:
            logger.info(f"[REVERSAL_EXIT_BLOCKED] {position_key}: Reversed position gain rising ({reversed_gain:.2f}% > {reversed_prev_gain:.2f}%) - BLOCKING ALL REVERSE_CLOSE")
        return None
    if minutes_since(reversal_timestamp, now) > 6:
        if ctx['gain'] < 0.0:
            reason = f"REVERSAL_EXIT_ON_LOSS"
            if config.VERBOSE_STOPS:
                logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: REVERSE_CLOSE - Loss condition gain={ctx['gain']:.2f}% < 0.0% conviction={conviction:.2f}")
            return Signal(action='REVERSE_CLOSE', reason=f"{reason} | {', '.join(reasons)}", conviction=95.0 if is_long else -95.0)
        momentum_recovered_3m = (i.get( 'wt1_3m', 0) > i.get( 'wt2_3m', 0) and i.get( 'wt1_3m', 0) > 56) if is_long else (i.get( 'wt1_3m', 0) < i.get( 'wt2_3m', 0) and i.get( 'wt1_3m', 0) < -56)
        momentum_recovered_15m = (i.get( 'wt1_15m', 0) > i.get( 'wt2_15m', 0)) if is_long else (i.get( 'wt1_15m', 0) < i.get( 'wt2_15m', 0))
        if momentum_recovered_3m and momentum_recovered_15m:
            reason = f"REVERSAL_EXIT_ON_MOMENTUM_RECOVERY"
            if config.VERBOSE_STOPS:
                wt1_3m = i.get( 'wt1_3m', 0)
                wt2_3m = i.get( 'wt2_3m', 0)
                wt1_15m = i.get( 'wt1_15m', 0)
                wt2_15m = i.get( 'wt2_15m', 0)
                logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: REVERSE_CLOSE - Momentum recovery wt1_3m={wt1_3m:.1f} wt2_3m={wt2_3m:.1f} wt1_15m={wt1_15m:.1f} wt2_15m={wt2_15m:.1f} momentum_3m={momentum_recovered_3m} momentum_15m={momentum_recovered_15m} conviction={conviction:.2f}")
            return Signal(action='REVERSE_CLOSE', reason=f"{reason} | {', '.join(reasons)}", conviction=95.0 if is_long else -95.0)
    logger.debug(f'{position_key} reversal_exit FOUND NOTHING')
    if config.VERBOSE_STOPS:
        logger.info(f"[REVERSAL_EXIT_RETURN] {position_key}: None - No conditions met for reversal exit")
    return None

def calculate_tier_mult(i: dict, is_long: bool) -> int:
    """Calculates the safe retention tier based on Stoch RSI alignment across timeframes."""
    k_1m = safe_fetch_float(i.get('stoch_k_1m', 50))
    k_1m_p = safe_fetch_float(i.get('k_1m_prev', 50))
    k_3m = safe_fetch_float(i.get('stoch_k_3m', 50))
    k_3m_p = safe_fetch_float(i.get('k_3m_prev', 50))
    k_15m = safe_fetch_float(i.get('stoch_k_15m', 50))
    d_15m = safe_fetch_float(i.get('stoch_d_15m', 50))
    k_1h = safe_fetch_float(i.get('stoch_k_1h', 50))
    d_1h = safe_fetch_float(i.get('stoch_d_1h', 50))
    k_4h = safe_fetch_float(i.get('stoch_k_4h', 50))
    d_4h = safe_fetch_float(i.get('stoch_d_4h', 50))
    tier_mult = 0
    arrow1_good = (is_long and k_1m >= k_1m_p) or (not is_long and k_1m <= k_1m_p)
    if arrow1_good:
        tier_mult = 1
        arrow3_good = (is_long and k_3m >= k_3m_p) or (not is_long and k_3m <= k_3m_p)
        if arrow3_good:
            tier_mult = 2
            arrow15_good = (is_long and k_15m >= d_15m) or (not is_long and k_15m <= d_15m)
            if arrow15_good:
                tier_mult = 4
                arrow1h_good = (is_long and k_1h >= d_1h) or (not is_long and k_1h <= d_1h)
                if arrow1h_good:
                    tier_mult = 6
                    arrow4h_good = (is_long and k_4h >= d_4h) or (not is_long and k_4h <= d_4h)
                    if arrow4h_good:
                        tier_mult = 8
    return tier_mult

async def check_position_reductions(service, position_key: str, account_key: str, position: Any):
    if not config.SERVICE_REDUCE: return
    if not service.trade_manager:
        await service._ensure_trade_manager()
    if position_key not in service.tradeable_keys:
        return
    account_key, symbol, position_side = parse_position_key(position_key)
    is_long = position_side=='LONG'
    if not service.trade_manager:
        for _ in range(5): 
            if service.trade_manager: break
            await asyncio.sleep(1)
        if not service.trade_manager:
            logger.error(f"[CHECK_POSITION_REDUCTIONS] {position_key}: trade_manager not available after 30s wait")
            return
    try :
        current_price = safe_fetch_float(getattr(position, 'mark_price', 0), 0.0)
        if not current_price or current_price <= 0:
            return
        gain = getattr(position, 'gain', 0.0)
        pos_amt = abs(float(getattr(position, 'positionAmt', 0)))
        pos_min_qty = max(2 * config.MIN_POSITION_SIZE / current_price, service.min_qty.get(symbol, 0.0001))
        i = ii(service, symbol)
        if gain < -1.5 and pos_amt > pos_min_qty:
            # We verify if an active hedge ACTUALLY exists before trusting STRICT_NO_LOSS
            has_active_hedge_for_bleed = False
            if hasattr(service, 'tracker_manager') and service.tracker_manager:
                async with service.tracker_manager._hedges_lock:
                    has_active_hedge_for_bleed = any(h.get('losing_position_key') == position_key for h in service.tracker_manager.active_hedges)

            dc_low_15m = i.get('dc_low_15m', 0.0)
            dc_high_15m = i.get('dc_high_15m', 0.0)
            floor_broken = (is_long and dc_low_15m > 0 and current_price < dc_low_15m) or (not is_long and dc_high_15m > 0 and current_price > dc_high_15m)

            if is_strict_no_loss_account(config, account_key) and has_active_hedge_for_bleed and not floor_broken:
                logger.warning(f"[EMERGENCY_SKIP][{account_key}] {position_key}: BLEED DETECTED ({gain:.2f}%) but STRICT_NO_LOSS is enabled AND Active Hedge Verified. Skipping exit.")
            elif getattr(config, 'DELTA_SERVICE_BLEED_STOP', True) or _delta_exit_ok or gain < -3.0 or floor_broken:
                reason = f"BLEED_STOP_{gain:.2f}%"
                if floor_broken: reason = f"DC15M_FLOOR_BREAK_CLOSE_{gain:.2f}%"
                if _delta_exit_ok: reason += "_DELTA_CONFIRMED"
                logger.critical(f"🚨 [EMERGENCY_EXIT] {position_key}: {reason}. Cutting to floor.")
                side = 'SELL' if position_side=='LONG' else 'BUY'
                unique_id = f"EMERGENCY_CUT_{int(time.time())}"
                reduction_qty = pos_amt - pos_min_qty
                await service.trade_manager.execute_now(position_key, account_key, symbol, pos_amt, side, position_side, reduction_qty, current_price, unique_id, reason, False, "REDUCE")
                return
        now_ts = time.time()        
        now_dt = datetime.now(timezone.utc)
        ak, symbol, position_side = parse_position_key(position_key)
        is_long = position_side == "LONG"
        current_price = safe_fetch_float(getattr(position, 'mark_price', 0), 0.0)
        if current_price <= 0:
            current_price = await quick_price(symbol) or 0.0
        if current_price <= 0: return
        
        tier_mult = calculate_tier_mult(i, is_long)
        retention_qty = max(1, tier_mult) * pos_min_qty
        if position.positionAmt <= retention_qty * 1.05 and position.gain > -0.1: 
            service.last_monitored[position_key] = now_ts
            return 
        service.last_monitored[position_key] = now_ts
        if not hasattr(service, 'last_monitored_positions'):
            service.last_monitored_positions = {}
        service.last_monitored_positions[position_key] = now_ts
        symbol = getattr(position, 'symbol', symbol) 
        if symbol:
            service.last_symbol_monitored[symbol] = now_ts 
        action = 'REDUCE'
        START_USD = float(getattr(config, 'START_POSITION_SIZE', 55.0))
        value_usd = (position.positionAmt) * current_price
        
        if position.positionAmt < 1.8 * retention_qty:
            service.reduced_positions[position_key] = now_ts 
            service.augmented_positions.pop(position_key, None)
            service.reduced_in_monitor_reductions[position_key] = now_ts
            return
        value_usd = (position.positionAmt) * current_price
        base_ctx = {'service': service, 'symbol': symbol, 'logger': logger, 'position_key': position_key, 'account_key': account_key}
        effective_gain = position.gain if position.positionAmt != 0 else max(position.max_gain, position.prev_gain, 0.0)
        _delta_sig = None
        _delta_exit_ok = False
        _delta_tfs = 0
        _dt = getattr(service.trade_manager, 'delta_tracker', None) if service.trade_manager else None
        if _dt and getattr(config, 'DELTA_ENGINE_ENABLED', False):
            try:
                _delta_sig = _dt.update(symbol, i, {'side': position_side, 'max_speed': 0, 'n_entries': 0})
                if _delta_sig:
                    _delta_tfs = _delta_sig.bull_tf_count if is_long else _delta_sig.bear_tf_count
                    _delta_exit_ok = (is_long and _delta_sig.exit_long) or (not is_long and _delta_sig.exit_short)
            except Exception:
                pass
        ctx = {**base_ctx, 'now': now_dt, 'position': position, 'position_side': position_side, 'is_long': is_long, 'current_price': current_price, 'current_price_ts': getattr(position, 'mark_price_last_updated', now_dt), 'gain': effective_gain, 'positionAmt': position.positionAmt, 'indicators': i, 'config': config, 'delta_sig': _delta_sig, 'delta_exit_ok': _delta_exit_ok, 'delta_tfs': _delta_tfs}
        is_h_acc = is_hedge_account(config, account_key)
        must_hedge_loss = position.gain < -0.1
        should_hedge_profit = is_h_acc and -0.1 <= position.gain < 0.0 
        if (must_hedge_loss or should_hedge_profit) and position.gain < 0.0:
            has_active_hedge = False
            if hasattr(service, 'tracker_manager') and service.tracker_manager:
                async with service.tracker_manager._hedges_lock:
                    has_active_hedge = any(h.get('losing_position_key') == position_key for h in service.tracker_manager.active_hedges)
            if has_active_hedge:
                service.last_monitored[position_key] = now_ts
                return
            if hasattr(service, 'hedge_engine') and service.hedge_engine and current_price > 0:
                failure_reason = f"MANDATORY_HEDGE_LOSS_{position.gain:.2f}%" if must_hedge_loss else f"HEDGE_MODE_PROFIT_{position.gain:.2f}%"
                logger.warning(f"[check_position_reductions][{account_key}] {position_key}: 🛡️ Triggering {failure_reason}")
                positionAmt_svc = abs(safe_fetch_float(getattr(position, "positionAmt", 0.0), 0.0))
                if account_key in ['flz']:
                    hedge_side_svc = 'SHORT' if is_long else 'LONG'
                    hedge_key_svc = construct_position_key(account_key, symbol, hedge_side_svc)
                    existing_hedge_svc = service.positions_by_account.get(account_key, {}).get(hedge_key_svc)
                    if not (existing_hedge_svc and abs(safe_fetch_float(existing_hedge_svc.positionAmt, 0.0)) > 0):
                        await service.hedge_engine.execute_same_symbol_hedge(account_key, position, symbol, position_side, positionAmt_svc, current_price)
                else:
                    losing_value_usd_svc = positionAmt_svc * current_price
                    hedge_result_svc = await service.hedge_engine.execute_dual_hedge(account_key=account_key, losing_position_key=position_key, losing_symbol=symbol, losing_side=position_side, losing_value_usd=losing_value_usd_svc, dry_run=False)
                    if hedge_result_svc.get('overall_status') not in ['success', 'partial']:
                        if is_strict_no_loss_account(config, account_key) and position.gain >= -2.0:
                            logger.critical(f"🚨[STRICT_HEDGE_FAIL_BLOCK][{account_key}] Dual hedge failed for {position_key}: {hedge_result_svc.get('error')}. STRICT_NO_LOSS is enabled and loss > -2.0%. Delaying kill.")
                        else:
                            logger.critical(f"🚨 [HEDGE_FAILURE_KILL] Dual hedge failed for {position_key}: {hedge_result_svc.get('error')}. KILLING ORIGINAL POSITION to mitigate loss (Strict No Loss Overridden).")
                            side_kill = 'SELL' if is_long else 'BUY'
                            await service.trade_manager.execute_now( position_key, account_key, symbol, position.positionAmt, side_kill, position_side, position.positionAmt, current_price, f"HEDGE_FAIL_EMERGENCY_KILL_{int(time.time())}", f"HEDGE_FAILURE_MITIGATION_{hedge_result_svc.get('error')}", True, 'CLOSE' )
            service.last_monitored[position_key] = now_ts
            return
        START_USD = float(getattr(config, 'START_POSITION_SIZE', 55.0))
        value_usd = (position.positionAmt) * current_price
        base_ctx = {'service': service, 'symbol': symbol, 'logger': logger, 'position_key': position_key, 'account_key': account_key}
        effective_gain = position.gain if position.positionAmt != 0 else max(position.max_gain, position.prev_gain, 0.0)
        ctx = {**base_ctx, 'now': now_dt, 'position': position, 'position_side': position_side, 'is_long': is_long, 'current_price': current_price, 'current_price_ts': getattr(position, 'mark_price_last_updated', now_dt), 'gain': effective_gain, 'positionAmt': position.positionAmt, 'indicators': i, 'config': config}
        if position.positionAmt < 1.8 * retention_qty and effective_gain >= -0.5:
            service.reduced_positions[position_key] = now_ts 
            service.augmented_positions.pop(position_key, None)
            service.reduced_in_monitor_reductions[position_key] = now_ts
            return
        adv_signal = await _check_immediate_reduction_triggers(ctx, i, position, current_price, is_long)
        if not adv_signal:
            adv_signal = await _check_trailing_stops(ctx, i, position, current_price, is_long)
        if not adv_signal:
            adv_signal = await evaluate_reversal_exit(ctx)
            
        if adv_signal:
            sig_action = adv_signal.action
            sig_reason = adv_signal.reason
            is_full_close = 'CLOSE' in sig_action
            sig_qty = adv_signal.reduction_amount if getattr(adv_signal, 'reduction_amount', 0) > 0 else (position.positionAmt if is_full_close else (position.positionAmt - retention_qty))
            
            if sig_qty > 0:
                side = 'SELL' if position_side == 'LONG' else 'BUY'
                unique_id = f"adv_trigger_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                if current_env['env'] != 'server' and await safe_check_server_heartbeat(account_key):
                    logger.warning(f"[SERVER_HEARTBEAT_BLOCK] {position_key}: Server running. Blocking advanced trigger.")
                    return
                    
                logger.warning(f"🚨 [ADVANCED_TRIGGER] {position_key}: Executing {sig_action} ({sig_reason})")
                result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, sig_qty, current_price, unique_id, sig_reason, is_full_close, sig_action)
                if result and result in["SUCCESS", "SUCCESS_MAKER", "SUCCESS_MARKET", "SUCCESS_WEBHOOK"]:
                    service.reduced_in_monitor_reductions[position_key] = now_ts
                    return

        strict_close_info = None

        if hasattr(service, 'strict_close_positions') and position_key in service.strict_close_positions:
            strict_close_info = service.strict_close_positions[position_key]
            strict_loss_threshold = strict_close_info.get('strict_loss_threshold', -0.12)
            strict_gain_threshold = strict_close_info.get('strict_gain_threshold', 0.1)
            if effective_gain <= strict_loss_threshold:
                reduction_qty = max(0.0, abs(position.positionAmt) - retention_qty)
                if reduction_qty > retention_qty:
                    logger.critical(f"[STRICT_CLOSE_LIMITS_ENFORCE] {position_key}: LOSS threshold hit ({effective_gain:.2f}% <= {strict_loss_threshold:.2f}%) - REDUCING immediately")
                    side = 'SELL' if position_side == 'LONG' else 'BUY'
                    unique_id = f"strict_close_loss_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                    result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, reduction_qty, current_price, unique_id, f"STRICT_CLOSE_LOSS_THRESHOLD_{effective_gain:.2f}%", False, 'REDUCE')
                    if result and "SUCCESS" in result:
                        service.reduced_in_monitor_reductions[position_key] = now_ts
                        logger.info(f"[STRICT_CLOSE_LIMITS] {position_key}: Reduction executed due to strict loss threshold")
                        return
            elif effective_gain >= strict_gain_threshold:
                reduction_qty = max(position.positionAmt - retention_qty, position.positionAmt * 0.3)
                if reduction_qty > retention_qty:
                    logger.warning(f"[STRICT_CLOSE_LIMITS_ENFORCE] {position_key}: GAIN threshold hit ({effective_gain:.2f}% >= {strict_gain_threshold:.2f}%) - TAKING PROFIT quickly")
                    side = 'SELL' if position_side == 'LONG' else 'BUY'
                    unique_id = f"strict_close_profit_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                    result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, reduction_qty, current_price, unique_id, f"STRICT_CLOSE_PROFIT_THRESHOLD_{effective_gain:.2f}%", False, 'PROFIT_TAKE')
                    if result and "SUCCESS" in result:
                        service.reduced_in_monitor_reductions[position_key] = now_ts
                        logger.info(f"[STRICT_CLOSE_LIMITS] {position_key}: Profit take executed due to strict gain threshold")
                        return
            opened_at = getattr(position, 'opened_at', None)
            is_new_position = False
            age_seconds = 999999.0
            if opened_at:
                try :
                    if isinstance(opened_at, str):
                        opened_at = isoparse(opened_at)
                    if isinstance(opened_at, datetime):
                        if opened_at.tzinfo is None:
                            opened_at = opened_at.replace(tzinfo=timezone.utc)
                        age_seconds = (now_dt - opened_at).total_seconds()
                        is_new_position = age_seconds < config.NEW_POSITION_MIN_AGE_SECONDS
                except Exception:
                    pass
            has_crossunder_signal = False
            wt_cross_3m = i.get('wt_cross_3m') if i else None
            wt_cross_15m = i.get('wt_cross_15m') if i else None
            dc_basis_crossunder_3m = i.get('dc_basis_crossunder_3m', False) or False
            dc_basis_crossover_3m = i.get('dc_basis_crossover_3m', False) or False
            # WT cross replaces both stoch crossover and wt_crossunder booleans
            has_crossunder_signal = (is_long and (wt_cross_3m == "BEAR" or dc_basis_crossunder_3m)) or (not is_long and (wt_cross_3m == "BULL" or dc_basis_crossover_3m))
            wt1_3m = safe_fetch_float(i.get('wt1_3m', 0) if i else 0, 0); wt2_3m = safe_fetch_float(i.get('wt2_3m', 0) if i else 0, 0)
            wt_bearish = (is_long and wt1_3m < wt2_3m) or (not is_long and wt1_3m > wt2_3m)
            has_bearish_signal = has_crossunder_signal or wt_bearish
            if is_new_position and effective_gain >= config.NEW_POSITION_MAX_LOSS_THRESHOLD and not has_bearish_signal:
                logger.warning(f"[NEW_POSITION_PROTECTION] {position_key}: BLOCKING reduction - position is new (age={age_seconds:.1f}s < {config.NEW_POSITION_MIN_AGE_SECONDS}s) and gain {effective_gain:.3f}% >= threshold {config.NEW_POSITION_MAX_LOSS_THRESHOLD:.3f}% (no crossunder signal)")
                service.last_monitored[position_key] = now_ts
                if not hasattr(service, 'last_monitored_positions'):
                    service.last_monitored_positions = {}
                service.last_monitored_positions[position_key] = now_ts
                return
            if is_new_position and effective_gain >= config.NEW_POSITION_MAX_LOSS_THRESHOLD and has_bearish_signal:
                logger.warning(f"[NEW_POSITION_PROTECTION] {position_key}: ALLOWING reduction - position is new but bearish signal detected (age={age_seconds:.1f}s, gain={effective_gain:.3f}%, crossunder={has_crossunder_signal}, stoch_bearish={stoch_bearish})")
            master = None
            try :
                master = await evaluate_master_stop_loss(ctx)
            except Exception as master_err:
                logger.error(f"[CH ECK_POSITION_REDUCTIONS] evaluate _master_stop_loss_new failed for {position_key}: {master_err}")
                logger.debug(traceback.format_exc())
            if not master:
                try :
                    pass 
                except Exception as master_err2:
                    logger.error(f"[CHECK _POSITION_REDUCTIONS] evalua te_master_stop_loss failed for {position_key}: {master_err2}")
                    logger.debug(traceback.format_exc())
            if master:
                logger.warning(f"[MO NITOR_REDUCTIONS] {position_key} MASTER_STOP_LOSS: {action} {master.reason} {position.positionAmt} value=${value_usd:.0f}")
                k_1m = safe_fetch_float(i.get('stoch_k_1m', 50))
                k_1m_p = safe_fetch_float(i.get('k_1m_prev', 50))
                k_3m = safe_fetch_float(i.get('stoch_k_3m', 50))
                k_3m_p = safe_fetch_float(i.get('k_3m_prev', 50))
                k_15m = safe_fetch_float(i.get('stoch_k_15m', 50))
                d_15m = safe_fetch_float(i.get('stoch_d_15m', 50))
                k_1h = safe_fetch_float(i.get('stoch_k_1h', 50))
                d_1h = safe_fetch_float(i.get('stoch_d_1h', 50))
                k_4h = safe_fetch_float(i.get('stoch_k_4h', 50))
                d_4h = safe_fetch_float(i.get('stoch_d_4h', 50))
                safeguard_mult = 0
                arrow1_good = (is_long and k_1m >= k_1m_p) or (not is_long and k_1m <= k_1m_p)
                if arrow1_good:
                    safeguard_mult = 1
                    arrow3_good = (is_long and k_3m >= k_3m_p) or (not is_long and k_3m <= k_3m_p)
                    if arrow3_good:
                        safeguard_mult = 2
                        arrow15_good = (is_long and k_15m >= d_15m) or (not is_long and k_15m <= d_15m)
                        if arrow15_good:
                            safeguard_mult = 4
                            arrow1h_good = (is_long and k_1h >= d_1h) or (not is_long and k_1h <= d_1h)
                            if arrow1h_good: 
                                safeguard_mult = 6
                                arrow4h_good = (is_long and k_4h >= d_4h) or (not is_long and k_4h <= d_4h)
                                if arrow4h_good:
                                    safeguard_mult = 8
                pos_min_qty = max(2 * config.MIN_POSITION_SIZE / current_price, service.min_qty.get(symbol, 0.0001))
                retention_qty = safeguard_mult * pos_min_qty
                standard_reduction = position.positionAmt - retention_qty
                if position.positionAmt <= retention_qty * 1.05:
                    logger.info(f"🛡️ [SAFEGUARD_SERVICE] {position_key}: Blocking Master Stop. Retaining {position.positionAmt:.6f} (Tier {safeguard_mult}x Safe)")
                    return 
            about_to_be_in_loss = position.gain < 0.1
            is_choppy = -2.0 < position.gain < 0.4
            should_auto_reduce = False
            if not is_choppy:
                should_auto_reduce = position.positionAmt > 2 * pos_min_qty
                if has_crossunder_signal:
                    should_auto_reduce = position.positionAmt > retention_qty * 1.1 and position.gain < 0.5
                logger.warning(f"[MO NITOR_REDUCTIONS] {position_key} MASTER_STOP_LOSS: {action} {master.reason} {position.positionAmt} value=${value_usd:.0f}")
                quantity = position.positionAmt - retention_qty
                if quantity < retention_qty:
                    logger.info(f"[MO NITOR_REDUCTIONS] {position_key}: Position too small for reduction, skipping")
                    service.last_monitored[position_key] = now_ts
                    if not hasattr(service, 'last_monitored_positions'):
                        service.last_monitored_positions = {}
                    service.last_monitored_positions[position_key] = now_ts
                    return
                action = (getattr(master, 'action', 'REDUCE') or 'REDUCE').upper()
                reason = getattr(master, 'reason', 'MASTER_STOP')
                symbol_allowed = service.is_symbol_allowed(account_key, symbol, position_key)
                is_aggressive_stop = 'AGGRESSIVE_STOP' in reason.upper()
                if position_key in service.positions_to_gracefully_exit or position_key in service.reversed_positions:
                    action = 'CLOSE'
                    is_full_close = True
                elif position.gain > 0.5:
                    action = 'PROFIT_TAKE'
                    is_full_close = False
                else:
                    action = 'REDUCE'
                    is_full_close = False
                if is_aggressive_stop:
                    is_full_close = False
                    quantity = min(quantity, position.positionAmt - retention_qty)
                elif is_full_close and not symbol_allowed:
                    quantity = position.positionAmt
                elif is_full_close and symbol_allowed:
                    quantity = position.positionAmt - retention_qty
                    is_full_close = False
                else:
                    quantity = min(quantity, position.positionAmt - retention_qty)
                if quantity <= 0:
                    logger.info(f"[MO NITOR_REDUCTIONS] {position_key}: Reduction quantity <= 0, skipping")
                    service.last_monitored[position_key] = now_ts
                    if not hasattr(service, 'last_monitored_positions'):
                        service.last_monitored_positions = {}
                    service.last_monitored_positions[position_key] = now_ts
                    return
                side = 'SELL' if position_side == 'LONG' else 'BUY'
                is_long_check = position_side == 'LONG'
                if (is_long_check and side != 'SELL') or (not is_long_check and side != 'BUY'):
                    logger.error(f"[🚨🚨🚨 CRITICAL_AGGRESSIVE_STOP_SIDE_MISMATCH] {position_key}: side={side} does NOT match position_side={position_side} (is_long={is_long_check})! BLOCKING ORDER!")
                    return
                unique_id = f"monitor_reduce_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                if current_env['env'] != 'server' and await safe_check_server_heartbeat(account_key):
                    logger.warning(f"[SERVER_HEARTBEAT_BLOCK] {position_key}: Server instance is running for {account_key}, blocking monitor reduction from {current_env['env']}")
                    return
                logger.warning(f"[🚨🚨 AGGRESSIVE_STOP_EXECUTE] {position_key}: side={side} position_side={position_side} is_long={is_long} quantity={quantity:.6f} action={action} reason={reason}")
                result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, quantity, current_price, unique_id, reason, is_full_close, action)
                if result and result in ["SUCCESS", "SUCCESS_MAKER", "SUCCESS_MARKET", "SUCCESS_WEBHOOK"]:
                    logger.info(f"[MO NITOR_REDUCTIONS] {position_key}: {action} executed and verified: {result}")
                    service.reduced_in_monitor_reductions[position_key] = now_ts
                else:
                    logger.debug(f"[MO NITOR_REDUCTIONS] {position_key}: {action} execution failed or not verified: {result}")
                    if result and ("NOT_VERIFIED" in result or "NO_EXECUTION" in result):
                        logger.warning(f"[MO NITOR_REDUCTIONS] {position_key}: Lock released, webhook may have been sent")
                k_15m = i.get('stoch_k_15m', 50.0); high_3m = i.get('high_3m', 0.0); high_3m_prev = i.get('high_3m_prev', 0.0); low_3m = i.get('low_3m', 0.0); low_3m_prev = i.get('low_3m_prev', 0.0); ha_3m=i.get('ha_3m')
                prev_gain_valid_big_pos = False
                prev_gain_big_pos = 0.0
                if position and hasattr(position, 'prev_gain_last_updated') and position.prev_gain_last_updated:
                    age_seconds = (now_dt - position.prev_gain_last_updated).total_seconds()
                    if age_seconds < 480.0:
                        prev_gain_big_pos = safe_fetch_float(getattr(position, 'prev_gain', 0.0), 0.0)
                        if position.gain != 0.0 or prev_gain_big_pos != 0.0:
                            prev_gain_valid_big_pos = True
                if value_usd > 200.0 and k_15m > 90.0 and prev_gain_valid_big_pos and prev_gain_big_pos > 1.0 and position.gain < 1.0:
                    reduction_qty = position.positionAmt - retention_qty
                    if reduction_qty > retention_qty and minutes_since(position.last_reduction_time, now_dt) >= 3:
                        logger.warning(f"[BIG_POSITION_k_15mM_REDUCTION] {position_key}: k_15m={k_15m:.1f} >90, prev_gain={position.prev_gain:.2f}% >1.0%, gain={position.gain:.2f}% <1.0%, value=${value_usd:.0f} - reducing to pos_min_qty")
                        side = 'SELL' if position_side == 'LONG' else 'BUY'
                        unique_id = f"big_pos_k_15mm_reduction_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                        if current_env['env'] != 'server' and await safe_check_server_heartbeat(account_key):
                            logger.warning(f"[SERVER_HEARTBEAT_BLOCK] {position_key}: Server instance is running for {account_key}, blocking big position k_15m reduction from {current_env['env']}")
                            return
                        result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, reduction_qty, current_price, unique_id, "BIG_POSITION_k_15mM_REDUCTION", False, 'REDUCE')
                        if result and result in ["SUCCESS", "SUCCESS_MAKER", "SUCCESS_MARKET", "SUCCESS_WEBHOOK"]:
                            service.reduced_in_monitor_reductions[position_key] = now_ts
                            logger.info(f"[BIG_POSITION_k_15mM_REDUCTION] {position_key}: Reduction to pos_min_qty verified: {result}")
                        else:
                            logger.error(f"[BIG_POSITION_k_15mM_REDUCTION] {position_key}: Reduction failed or not verified: {result}")
                lower_low = high_3m and high_3m_prev and low_3m and low_3m_prev and high_3m <= high_3m_prev and (low_3m or current_price) <= low_3m_prev
                higher_high = high_3m and high_3m_prev and low_3m and low_3m_prev and (high_3m or current_price) >= high_3m_prev and low_3m >= low_3m_prev
                prev_gain_valid_good_gain = False
                prev_gain_good_gain = 0.0
                if position and hasattr(position, 'prev_gain_last_updated') and position.prev_gain_last_updated:
                    age_seconds = (now_dt - position.prev_gain_last_updated).total_seconds()
                    if age_seconds < 480.0:
                        prev_gain_good_gain = safe_fetch_float(getattr(position, 'prev_gain', 0.0), 0.0)
                        if position.gain != 0.0 or prev_gain_good_gain != 0.0:
                            prev_gain_valid_good_gain = True
                good_gain = position.gain > 0.5 and (prev_gain_valid_good_gain and prev_gain_good_gain > position.gain)
                extreme_stoch = (is_long and k_15m > 85 and ha_3m == 'red') or (not is_long and k_15m < 15 and ha_3m=='green')
                should_reduce_immediate = (good_gain or extreme_stoch) and ((is_long and lower_low) or (not is_long and higher_high))
                reduction_qty = position.positionAmt - retention_qty
                if should_reduce_immediate and minutes_since(position.last_reduction_time, now_dt) >= 3:
                    reduction_qty = min(reduction_qty, position.positionAmt - retention_qty)
                    if position.positionAmt * current_price > 250:
                        reduction_qty = position.positionAmt - 200 / current_price
                    if reduction_qty > retention_qty:
                        logger.warning(f"[IMMEDIATE_REDUCTION] {position_key}: Good gain/extreme stoch + {'lower_low' if is_long else 'higher_high'}. gain={position.gain:.2f}% k_15m={k_15m:.1f} value=${value_usd:.0f}")
                        side = 'SELL' if position_side == 'LONG' else 'BUY'
                        unique_id = f"immediate_reduction_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                        result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, reduction_qty, current_price, unique_id, f"IMMEDIATE_REDUCTION_{'lower_low' if is_long else 'higher_high'}", False, 'REDUCE')
                        if result and result in ["SUCCESS", "SUCCESS_MAKER", "SUCCESS_MARKET", "SUCCESS_WEBHOOK"]:
                            service.reduced_in_monitor_reductions[position_key] = now_ts
                            logger.info(f"[IMMEDIATE_REDUCTION] {position_key}: Reduction verified, tracking for reentry: {result}")
                        else:
                            logger.error(f"[IMMEDIATE_REDUCTION] {position_key}: Reduction failed or not verified: {result}")
                medium_position_threshold = 1.5 * START_USD
                symbol_allowed = service.is_symbol_allowed(account_key, symbol, position_key)
                prev_gain_check_valid_protection = False
                prev_gain_protection = 0.0
                if position and hasattr(position, 'prev_gain_last_updated') and position.prev_gain_last_updated:
                    age_seconds = (now_dt - position.prev_gain_last_updated).total_seconds()
                    if age_seconds < 480.0:
                        prev_gain_protection = safe_fetch_float(getattr(position, 'prev_gain', 0.0), 0.0)
                        if position.gain != 0.0 or prev_gain_protection != 0.0:
                            prev_gain_check_valid_protection = True
                if not prev_gain_check_valid_protection:
                    indicator_price_protection = safe_fetch_float(i.get('current_price', 0), current_price)
                    if indicator_price_protection and indicator_price_protection > 0:
                        price_deteriorated_protection = (is_long and current_price < indicator_price_protection) or (not is_long and current_price > indicator_price_protection)
                    else:
                        price_deteriorated_protection = False 
                else:
                    price_deteriorated_protection = position.gain < prev_gain_protection
                if value_usd > medium_position_threshold and price_deteriorated_protection and minutes_since(position.opened_at, now_dt) >= 5 and position.gain < -0.5:
                    if minutes_since(position.last_reduction_time, now_dt) >= 3:
                        target_value_first = 200.0
                        if value_usd > target_value_first:
                            reduction_qty = (value_usd - target_value_first) / current_price
                            logger.warning(f"[GAIN_PROTECTION] {position_key}: First reduction to $200. gain={position.gain:.2f}% prev_gain={position.prev_gain:.2f}% value=${value_usd:.0f}")
                        elif value_usd > retention_qty * current_price:
                            reduction_qty = position.positionAmt - retention_qty
                            logger.warning(f"[GAIN_PROTECTION] {position_key}: Reducing to pos_min_qty. gain={position.gain:.2f}% prev_gain={position.prev_gain:.2f}% value=${value_usd:.0f}")
                        else:
                            reduction_qty = 0
                        if reduction_qty > retention_qty:
                            reduction_qty = min(reduction_qty, position.positionAmt - retention_qty)
                            if True: 
                                side = 'SELL' if position_side == 'LONG' else 'BUY'
                                unique_id = f"gain_protection_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                                if current_env['env'] != 'server' and await safe_check_server_heartbeat(account_key):
                                    logger.warning(f"[SERVER_HEARTBEAT_BLOCK] {position_key}: Server instance is running for {account_key}, blocking gain protection from {current_env['env']}")
                                    return
                                result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, reduction_qty, current_price, unique_id, "GAIN_PROTECTION_3xSS", False, 'REDUCE')
                                if result and result in ["SUCCESS", "SUCCESS_MAKER", "SUCCESS_MARKET", "SUCCESS_WEBHOOK"]:
                                    logger.info(f"[GAIN_PROTECTION] {position_key}: Reduction verified: {result}")
                                    service.reduced_in_monitor_reductions[position_key] = now_ts
                                else:
                                    logger.error(f"[GAIN_PROTECTION] {position_key}: Reduction failed or not verified: {result}")
                large_position_threshold = 2.5 * START_USD
                if value_usd > large_position_threshold:                
                    prev_gain_check_valid_deterioration = False
                    prev_gain_deterioration = 0.0
                    if position and hasattr(position, 'prev_gain_last_updated') and position.prev_gain_last_updated:
                        age_seconds = (now_dt - position.prev_gain_last_updated).total_seconds()
                        if age_seconds < 480.0:
                            prev_gain_deterioration = safe_fetch_float(getattr(position, 'prev_gain', 0.0), 0.0)
                            if position.gain != 0.0 or prev_gain_deterioration != 0.0:
                                prev_gain_check_valid_deterioration = True
                    if not prev_gain_check_valid_deterioration:
                        indicator_price_deterioration = safe_fetch_float(i.get('current_price', 0), current_price)
                        if indicator_price_deterioration and indicator_price_deterioration > 0:
                            price_deteriorated_deterioration = (is_long and current_price < indicator_price_deterioration) or (not is_long and current_price > indicator_price_deterioration)
                        else:
                            price_deteriorated_deterioration = False 
                    else:
                        price_deteriorated_deterioration = position.gain < prev_gain_deterioration
                    if price_deteriorated_deterioration:
                        deterioration_count = service.gain_deterioration_count.get(position_key, 0) + 1
                        service.gain_deterioration_count[position_key] = deterioration_count
                        if deterioration_count >= 5 and position_key not in service.gain_deterioration_reentry and position.gain < 0.0:
                            reduction_qty = max(position.positionAmt * 0.5, position.positionAmt - 2 * retention_qty)
                            reduction_qty = min(reduction_qty, position.positionAmt - retention_qty)
                            if reduction_qty > retention_qty:
                                logger.warning(f"[GAIN_DETERIORATION] {position_key}: Reducing after {deterioration_count} consecutive gain<prev_gain occurrences. gain={position.gain:.2f}% prev_gain={position.prev_gain:.2f}% value=${value_usd:.0f}")
                                side = 'SELL' if position_side == 'LONG' else 'BUY'
                                unique_id = f"gain_deterioration_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                                if current_env['env'] != 'server' and await safe_check_server_heartbeat(account_key):
                                    logger.warning(f"[SERVER_HEARTBEAT_BLOCK] {position_key}: Server instance is running for {account_key}, blocking gain deterioration from {current_env['env']}")
                                    return
                                result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, reduction_qty, current_price, unique_id, f"GAIN_DETERIORATION_{deterioration_count}x", False, 'REDUCE')
                                if result and result in ["SUCCESS", "SUCCESS_MAKER", "SUCCESS_MARKET", "SUCCESS_WEBHOOK"]:
                                    service.gain_deterioration_reentry[position_key] = {'reduced_amount': reduction_qty, 'reduced_price': current_price, 'reduced_gain': position.gain, 'timestamp': now_dt.isoformat()}
                                    service.gain_deterioration_count[position_key] = 0
                                    service.reduced_in_monitor_reductions[position_key] = now_ts
                                    logger.info(f"[GAIN_DETERIORATION] {position_key}: Reduction verified, tracking for reentry: {result}")
                                else:
                                    logger.error(f"[GAIN_DETERIORATION] {position_key}: Reduction failed or not verified: {result}")
                    prev_gain_valid_recovery = False
                    prev_gain_recovery = 0.0
                    if position and hasattr(position, 'prev_gain_last_updated') and position.prev_gain_last_updated:
                        age_seconds = (now_dt - position.prev_gain_last_updated).total_seconds()
                        if age_seconds < 480.0:
                            prev_gain_recovery = safe_fetch_float(getattr(position, 'prev_gain', 0.0), 0.0)
                            if position.gain != 0.0 or prev_gain_recovery != 0.0:
                                prev_gain_valid_recovery = True
                    if position_key not in service.tradeable_keys:
                        return
                    elif prev_gain_valid_recovery and position.gain > prev_gain_recovery:
                        if position_key in service.gain_deterioration_reentry:
                            reentry_info = service.gain_deterioration_reentry[position_key]
                            reduced_amount = reentry_info.get('reduced_amount', 0)
                            if reduced_amount > 0 and position.gain > reentry_info.get('reduced_gain', 0):
                                logger.info(f"[GAIN_RECOVERY] {position_key}: Gain recovered ({position.gain:.2f}% > {position.prev_gain:.2f}%), reentrying {reduced_amount:.6f}")
                                reentry_qty = min(reduced_amount, position.positionAmt * 0.8)
                                max_order_value_usd = config.MAX_ORDER_VALUE_MEN if account_key == 'men' else config.MAX_ORDER_VALUE_FIN if account_key == 'fin' else config.MAX_ORDER_VALUE
                                if reentry_qty * current_price > max_order_value_usd:
                                    reentry_qty = max_order_value_usd / current_price
                                max_pos_size_usd = get_max_position_size(symbol, account_key=account_key)
                                current_notional = abs(position.positionAmt) * current_price
                                if current_notional + (reentry_qty * current_price) > max_pos_size_usd:
                                    allowed_additional_usd = max_pos_size_usd - current_notional
                                    reentry_qty = max(0.0, allowed_additional_usd / max(current_price, 1e-9))
                                side = 'BUY' if position_side == 'LONG' else 'SELL'
                                unique_id = f"gain_recovery_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                                if current_env['env'] != 'server' and await safe_check_server_heartbeat(account_key):
                                    logger.warning(f"[SERVER_HEARTBEAT_BLOCK] {position_key}: Server instance is running for {account_key}, blocking gain recovery reentry from {current_env['env']}")
                                    return
                                result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, reentry_qty, current_price, unique_id, "GAIN_RECOVERY_REENTRY", False, 'AUGMENT')
                                if result and "SUCCESS" in result:
                                    del service.gain_deterioration_reentry[position_key]
                                    service.gain_deterioration_count[position_key] = 0
                                    logger.info(f"[GAIN_RECOVERY] {position_key}: Reentry successful")
                                else:
                                    logger.error(f"[GAIN_RECOVERY] {position_key}: Reentry failed: {result}")
                        service.gain_deterioration_count[position_key] = 0
            if position.last_reduction_amount and position.last_reduction_amount > 0 and position.last_reduction_price and position.last_reduction_price > 0:
                if position_key not in service.tradeable_keys:
                    return
                time_since_reduction = minutes_since(position.last_reduction_time, now_dt)
                if time_since_reduction <= 9 and time_since_reduction >= 0:
                    price_crossed_back = (is_long and current_price > position.last_reduction_price) or (not is_long and current_price < position.last_reduction_price)
                    if price_crossed_back and minutes_since(position.last_augmentation_time, now_dt) >= 3:
                        k_3m = i.get('stoch_k_3m', 50.0)
                        d_3m = i.get('stoch_d_3m', 50.0)
                        t_up_3m = i.get('t_up_3m', False)
                        stoch_condition = (is_long and k_3m > d_3m and t_up_3m) or (not is_long and k_3m < d_3m and not t_up_3m)
                        k_15m_r = safe_fetch_float(i.get('stoch_k_15m', 50.0), 50.0)
                        d_15m_r = safe_fetch_float(i.get('stoch_d_15m', 50.0), 50.0)
                        htf_aligned = (is_long and k_15m_r > d_15m_r) or (not is_long and k_15m_r < d_15m_r)
                        if stoch_condition and htf_aligned and position.gain > 0.3:
                            pre_reduction_value = (position.positionAmt + position.last_reduction_amount) * position.last_reduction_price
                            was_large_position = pre_reduction_value > 3.0 * START_USD
                            if was_large_position:
                                reentry_qty = max(position.last_reduction_amount, config.START_POSITION_SIZE/current_price)
                                max_order_value_usd = config.MAX_ORDER_VALUE_MEN if account_key == 'men' else config.MAX_ORDER_VALUE_FIN if account_key == 'fin' else config.MAX_ORDER_VALUE
                                if reentry_qty * current_price > max_order_value_usd:
                                    reentry_qty = max_order_value_usd / current_price
                                max_pos_size_usd = get_max_position_size(symbol, account_key=account_key)
                                current_notional = abs(position.positionAmt) * current_price
                                if current_notional + (reentry_qty * current_price) > max_pos_size_usd:
                                    allowed_additional_usd = max_pos_size_usd - current_notional
                                    reentry_qty = allowed_additional_usd / current_price if current_price else reentry_qty
                                logger.info(f"[IMMEDIATE_REENTRY] {position_key}: Price crossed back ({current_price:.6f} {'>' if is_long else '<'} {position.last_reduction_price:.6f}) within {time_since_reduction:.1f}min, k_3m={k_3m:.1f} d_3m={d_3m:.1f} t_up_3m={t_up_3m}, reentrying full reduction amount {reentry_qty:.6f}")
                                side = 'BUY' if position_side == 'LONG' else 'SELL'
                                unique_id = f"immediate_reentry_{int(time.time())}_{uuid.uuid4().hex[:8]}"
                                if current_env['env'] != 'server' and await safe_check_server_heartbeat(account_key):
                                    logger.warning(f"[SERVER_HEARTBEAT_BLOCK] {position_key}: Server instance is running for {account_key}, blocking immediate reentry from {current_env['env']}")
                                    return
                                result = await service.trade_manager.execute_now(position_key, account_key, symbol, position.positionAmt, side, position_side, reentry_qty, current_price, unique_id, "IMMEDIATE_REENTRY_PRICE_CROSSBACK", False, 'AUGMENT')
                                if result and "SUCCESS" in result:
                                    logger.info(f"[IMMEDIATE_REENTRY] {position_key}: Reentry successful")
                                else:
                                    logger.error(f"[IMMEDIATE_REENTRY] {position_key}: Reentry failed: {result}")
            service.last_monitored[position_key] = now_ts
            if not hasattr(service, 'last_monitored_positions'):
                service.last_monitored_positions = {}
            service.last_monitored_positions[position_key] = now_ts
    except Exception as e:
        logger.error(f"[CHE CK_POSIT ION_REDUCTIONS] Error processing {position_key}: {e}")
        logger.debug(traceback.format_exc())

async def bootstrap_position_service(logger=None, accounts: Optional[Dict[str, Any]] = None, enable_auto_fetch: bool = True, trade_manager: Optional[Any] = None, start_maintenance: bool = True, load_priority: str = 'disk') -> PositionService:
    _t0 = time.time()
    if logger: logger.info(f"[bootstrap] START | Mode: {load_priority.upper()}")
    if accounts is not None:
        resolved_accounts = accounts
    else:
        resolved_accounts = await load_accounts_from_config(config, logger)
    if not resolved_accounts:
        if logger: logger.error("[bootstrap] CRITICAL: No accounts available!")
        raise RuntimeError("No accounts available")
    if len(resolved_accounts) > 1:
        if logger: logger.critical(f"[bootstrap] ⚠️ MULTI-ACCOUNT DETECTED: {list(resolved_accounts.keys())} — each process must only load its own account!")
    _t1 = time.time()
    if logger: logger.critical(f"[bootstrap] ⏱️ Account loading took {_t1-_t0:.2f}s")
    service = PositionService(logger=logger, accounts=resolved_accounts)
    if trade_manager:
        service.trade_manager = trade_manager
    try :
        if logger: logger.info("[bootstrap] 🔄 Initializing Redis Manager (5s timeout)...")
        service.redis_manager = await asyncio.wait_for(get_simple_redis_manager(), timeout=5.0)
    except (asyncio.TimeoutError, Exception) as e:
        if logger: logger.warning(f"[bootstrap] ⚠️ Redis Manager init timeout/failed ({e}) — continuing without Redis (disk-only mode)")
    _t2 = time.time()
    if logger: logger.critical(f"[bootstrap] ⏱️ Redis init took {_t2-_t1:.2f}s")
    if start_maintenance:
        if logger: logger.info("[bootstrap] Starting Universe Maintenance...")
        asyncio.create_task(service._universe_maintenance_loop())
        await asyncio.sleep(0.5)

    def robust_decode(raw_data):
        if raw_data is None: return None
        content = raw_data
        if isinstance(content, (bytes, bytearray)):
            try : content = content.decode('utf-8')
            except Exception: pass
        if not isinstance(content, (str, dict)): return None
        if isinstance(content, dict): return content
        try :
            decoded = json.loads(content)
            if isinstance(decoded, str):
                if decoded.startswith("b'") or decoded.startswith('b"'):
                    import ast
                    try :
                        decoded = ast.literal_eval(decoded).decode('utf-8')
                        return json.loads(decoded)
                    except Exception: pass
                if decoded.strip().startswith('{'):
                    return robust_decode(decoded)
            return decoded
        except json.JSONDecodeError:
            try :
                if content.startswith("'") and content.endswith("'"):
                    return json.loads(content[1:-1])
                if content.startswith('"') and content.endswith('"'):
                    import ast
                    return json.loads(ast.literal_eval(content))
            except Exception: pass
        return None
    redis_loaded_successfully = False
    if load_priority == 'redis' and service.redis_manager and service.redis_manager.has_any_connection():
        if logger: logger.info("[bootstrap] ⚡ Attempting Redis Hydration...")
        for acc in resolved_accounts:
            loaded_from_redis = False
            try :
                redis_key = f"positions:{acc}"
                raw_data = await service.redis_manager.get(redis_key)
                data_packet = robust_decode(raw_data)
                if data_packet and isinstance(data_packet, dict):
                    raw_positions_dict = data_packet.get("positions", {})
                    if not raw_positions_dict and "meta" in data_packet:
                         raw_positions_dict = data_packet
                    if acc not in service.positions_by_account:
                        service.positions_by_account[acc] = {}
                    loaded_count = 0
                    for pos_key, pos_data in raw_positions_dict.items():
                        if pos_key == 'meta': continue
                        if isinstance(pos_data, dict):
                            try :
                                pos_obj = Position.from_dict(pos_data)
                                # GUARD: skip Redis entries with zeroed critical fields — load from disk instead
                                _ep = float(getattr(pos_obj, 'entry_price', 0) or 0)
                                _oa = getattr(pos_obj, 'opened_at', None)
                                _amt = abs(float(getattr(pos_obj, 'positionAmt', 0) or 0))
                                if _amt > 0 and _ep == 0:
                                    if logger: logger.critical(f"[bootstrap] [{acc}] REDIS_POISON_BLOCKED: {pos_key} has amt={_amt} but entry_price=0. Skipping Redis, will load from disk.")
                                    continue
                                service.positions_by_account[acc][pos_key] = pos_obj
                                service.positions[pos_key] = pos_obj
                                loaded_count += 1
                            except Exception as e:
                                pass
                    if loaded_count > 0:
                        if logger: logger.info(f"[bootstrap] [{acc}] Redis loaded {loaded_count} positions.")
                        loaded_from_redis = True
                    else:
                        if logger: logger.warning(f"[bootstrap] [{acc}] Redis data valid but contained 0 positions.")
            except Exception as e:
                if logger: logger.error(f"[bootstrap] [{acc}] Redis load error: {e}")
            if not loaded_from_redis:
                if logger: logger.info(f"[bootstrap] [{acc}] Fallback to disk load.")
                await service.load_account(acc, force=True)
        service._positions_loaded_once = True
        service._loading_complete_event.set()
        service._initialized = True
        redis_loaded_successfully = True
        asyncio.create_task(service._refresh_usdc_pairs())
        asyncio.create_task(service._ensure_indicators_bridge())
        asyncio.create_task(service.initialize_prev_gain_for_existing_positions())
        if config.HEDGE_MODE:
             asyncio.create_task(service._ensure_hedge_engine_initialized())
        for acc in resolved_accounts:
            asyncio.create_task(service._load_stop_levels(acc))
            asyncio.create_task(service._load_reentry(acc))
            asyncio.create_task(service._load_ladder(acc))
        if enable_auto_fetch and service._should_enable_account_monitors():
             asyncio.create_task(service.start_account_monitors())
    else:
        await service.initialize(force=False, enable_auto_fetch=enable_auto_fetch)
    asyncio.create_task(service._enforce_data_sync_once())
    try:
        _t_phantom = time.time()
        phantom_count = await service.phantom_kill_on_bootstrap()
        if logger: logger.info(f"[bootstrap] Phantom kill pass took {time.time()-_t_phantom:.2f}s, killed {phantom_count} phantoms")
    except Exception as phantom_err:
        if logger: logger.error(f"[bootstrap] Phantom kill pass failed (non-fatal): {phantom_err}", exc_info=True)
    asyncio.create_task(service._periodic_phantom_kill())
    total_count = sum(len(p) for p in service.positions_by_account.values())
    _t3 = time.time()
    # 2026-04-26 FIX: was `logger.critical(...)` — when caller passes logger=None this AttributeError'd 758× in 2 days,
    # crashing ez_positions_realtime workers (which call bootstrap_position_service(logger=None, ...) early in main()).
    #if logger: logger.critical(f"[bootstrap] ⏱️ Data loading + init took {_t3-_t2:.2f}s | TOTAL bootstrap: {_t3-_t0:.2f}s")
    #if logger: logger.info(f"[bootstrap] DONE. Service initialized with {total_count} total positions.")
    return service
if __name__ == "__main__":

    async def _run() -> None:
        service = await bootstrap_position_service(logger=logger, enable_auto_fetch=False)
        loop = asyncio.get_running_loop()

        async def _graceful_shutdown(reason: str) -> None:
            if service.logger:
                service.logger.info(f"[positions_service] Shutdown requested ({reason})")
            await service.shutdown()

        def _signal_handler(sig):
            logger.warning(f"[positions_service] 🛑 Signal {sig.name} received - shutting down immediately")
            asyncio.create_task(_graceful_shutdown(f"signal:{sig.name}"))
        for sig in (signal.SIGINT, signal.SIGTERM):
            try :
                loop.add_signal_handler(sig, lambda s=sig: _signal_handler(s))
            except NotImplementedError:
                pass
        try :
            iteration = 0
            while not service._shutdown_event.is_set():
                if iteration % 10 == 0:
                    try :
                        data_dir = Path(config.DATA_DIR) if hasattr(config, 'DATA_DIR') else Path.home() / 'binance' / 'data'
                        kill_flag = data_dir / '.kill_ez_positions_service'
                        if kill_flag.exists():
                            logger.warning(f"[positions_service] Kill flag file found - exiting cleanly for deployment")
                            try : kill_flag.unlink()
                            except Exception: pass
                            await _graceful_shutdown("kill_flag")
                            break
                    except Exception: pass
                try :
                    await asyncio.wait_for(service._shutdown_event.wait(), timeout=1.0)
                    break
                except asyncio.TimeoutError:
                    iteration += 1
                    continue
        except KeyboardInterrupt:
            await _graceful_shutdown("KeyboardInterrupt")
        finally:
            if not service._shutdown_event.is_set():
                await service.shutdown()
    restart_delay = 5
    while True:
        try :
            asyncio.run(_run())
            break
        except KeyboardInterrupt:
            break
        except Exception as exc:
            logger.exception(f"[positions_service] Fatal crash: {exc}. Restarting in {restart_delay}s")
            time.sleep(restart_delay)
            restart_delay = min(restart_delay * 2, 60)

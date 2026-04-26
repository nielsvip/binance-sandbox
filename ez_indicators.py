# pylint: disable=W,C,R,I
import os
import resource

try:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 10000), hard))
except Exception: pass
import asyncio
import hashlib
import json
import logging
import math
import os
import sys
import tempfile
import time
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import aiofiles
import aiohttp
import numpy as np
import orjson
import pandas as pd
import redis.asyncio as redis

from config import Config

# wt_composite logic inlined into _inject_wt_composite() — no external dependency
PositionsServiceClient = None  # lazy import — avoid circular dep with ez_positions_service
from utils import (
    REDIS_CHANNELS,
    clean_nans,
    default_serializer,
    get_current_environment,
    orjson_default,
)

config = Config()
try:
    from ez_share_ind import get_shared_memory_client
except ImportError:
    print("CRITICAL: ez_share_ind.py not found. Shared memory features disabled.")
    get_shared_memory_client = None

env_info = get_current_environment()
env = env_info.get("env", "macbook") if isinstance(env_info, dict) else "macbook"
def _ensure_float_series(series: pd.Series) -> pd.Series:
    return series.astype(float)

def rsi_series(series: pd.Series, length: int) -> Optional[pd.Series]:
    if series is None or len(series) < length + 1:
        return None
    data = _ensure_float_series(series)
    delta = data.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    alpha = 1.0 / float(length)
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def stoch_rsi(series: pd.Series, length: int, k: int, d: int) -> Optional[pd.DataFrame]:
    rsi = rsi_series(series, length)
    if rsi is None or rsi.dropna().empty:
        return None
    lowest = rsi.rolling(length, min_periods=length).min()
    highest = rsi.rolling(length, min_periods=length).max()
    range_span = (highest - lowest).replace(0.0, np.nan)
    stoch = ((rsi - lowest) / range_span).clip(lower=0.0, upper=1.0) * 100.0
    k_series = stoch.rolling(k, min_periods=k).mean()
    d_series = k_series.rolling(d, min_periods=d).mean()
    result = pd.DataFrame({"k": k_series, "d": d_series})
    return result

def atr_series(df: pd.DataFrame, length: int) -> Optional[pd.Series]:
    if df is None or df.empty or len(df) < length + 1:
        return None
    high = _ensure_float_series(df["high"])
    low = _ensure_float_series(df["low"])
    close = _ensure_float_series(df["close"])
    prev_close = close.shift(1)
    tr_components = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    true_range = tr_components.max(axis=1)
    alpha = 1.0 / float(length)
    atr = true_range.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    return atr

def adx_value(df: pd.DataFrame, length: int = 14) -> Optional[float]:
    if df is None or df.empty or len(df) < length * 2 + 1:
        return None
    high = _ensure_float_series(df["high"])
    low = _ensure_float_series(df["low"])
    plus_dm = high.diff().clip(lower=0.0)
    minus_dm = (-low.diff()).clip(lower=0.0)
    mask = plus_dm < minus_dm
    plus_dm = plus_dm.where(~mask, 0.0)
    minus_dm = minus_dm.where(mask, 0.0)
    atr = atr_series(df, length)
    if atr is None:
        return None
    alpha = 1.0 / float(length)
    plus_di = 100.0 * (plus_dm.ewm(alpha=alpha, adjust=False, min_periods=length).mean() / atr.replace(0.0, np.nan))
    minus_di = 100.0 * (minus_dm.ewm(alpha=alpha, adjust=False, min_periods=length).mean() / atr.replace(0.0, np.nan))
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = ((plus_di - minus_di).abs() / di_sum) * 100.0
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    val = adx.iloc[-1]
    return float(val) if pd.notna(val) else None

def macd_values(series: pd.Series) -> Tuple[Optional[float], Optional[float], Optional[float], bool, bool]:
    if series is None or len(series) < 35:
        return None, None, None, False, False
    data = _ensure_float_series(series)
    ema12 = data.ewm(span=12, adjust=False).mean()
    ema26 = data.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal
    crossover = bool(macd_line.iloc[-1] > signal.iloc[-1] and macd_line.iloc[-2] <= signal.iloc[-2])
    crossunder = bool(macd_line.iloc[-1] < signal.iloc[-1] and macd_line.iloc[-2] >= signal.iloc[-2])
    return float(macd_line.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1]), crossover, crossunder

def ha_streak_count(df: pd.DataFrame) -> int:
    if df is None or len(df) < 3:
        return 0
    streak = 0
    for idx in range(len(df) - 1, max(len(df) - 21, 1), -1):
        ha_c = (df["open"].iloc[idx] + df["high"].iloc[idx] + df["low"].iloc[idx] + df["close"].iloc[idx]) / 4.0
        if idx > 0:
            prev_c = (df["open"].iloc[idx - 1] + df["high"].iloc[idx - 1] + df["low"].iloc[idx - 1] + df["close"].iloc[idx - 1]) / 4.0
            prev_o = (df["open"].iloc[idx - 1] + df["close"].iloc[idx - 1]) / 2.0
        else:
            break
        ha_o = (prev_c + prev_o) / 2.0
        color = 1 if ha_c >= ha_o else -1
        if streak == 0:
            streak = color
        elif (streak > 0 and color > 0) or (streak < 0 and color < 0):
            streak += color
        else:
            break
    return streak

def choppiness_index(df: pd.DataFrame, length: int = 14) -> Optional[float]:
    if df is None or len(df) < length + 2:
        return None
    atr_1 = atr_series(df, 1)
    if atr_1 is None:
        return None
    atr_sum = float(atr_1.iloc[-length:].sum())
    high_max = float(df["high"].iloc[-length:].max())
    low_min = float(df["low"].iloc[-length:].min())
    hl_range = high_max - low_min
    if hl_range <= 0:
        return None
    chop = 100.0 * math.log10(atr_sum / hl_range) / math.log10(float(length))
    return max(0.0, min(100.0, chop))


logger = logging.getLogger("ez_indicators")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    logger.addHandler(handler)
    # Add file handler with append mode and restart separator
    from logging.handlers import RotatingFileHandler
    logs_dir = Path.home() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    _wid = int(os.getenv("WORKER_INSTANCE_ID", "0"))
    _wtot = int(os.getenv("WORKER_TOTAL_INSTANCES", "1"))
    log_file = logs_dir / (f"ez_indicators_worker{_wid}.log" if _wtot > 1 else "ez_indicators.log")
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"SCRIPT RESTART - ez_indicators - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
            f.write(f"{'='*80}\n")
    except Exception:
        pass
    file_handler = RotatingFileHandler(log_file, maxBytes=100*1024*1024, backupCount=5, encoding='utf-8', mode='a')
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

TIMEFRAMES: Dict[str, Dict[str, Any]] = {
    "3m": {"seconds": 180, "half": 90, "dc_window": 20, "ema": [20], "sma": [("sma_200_1m", 70)], "atr": 14},
    "15m": {"seconds": 900, "half": 450, "dc_window": 20, "ema": [9, 14, 20], "sma": [("sma_200_15m", 200)], "atr": 14},
    "1h": {"seconds": 3600, "half": 1800, "dc_window": 20, "ema": [9, 14, 20], "sma": [("sma_200_1h", 200)], "atr": 14},
    "4h": {"seconds": 14400, "half": 7200, "dc_window": 20, "ema": [20], "sma": [("sma_200_4h", 200)], "atr": 14},
    "D": {"seconds": 86400, "half": 43200, "dc_window": 20, "ema": [20], "sma": [("sma_200_D", 200)], "atr": 14}
}
ATR_LONG_LENGTH = getattr(config, "ATR_LONG_WINDOW", 100)
REL_VOL_LENGTH = 20
LINREG_LENGTH = 50
WT_N1 = 10
WT_N2 = 21
WT_SMOOTH = 3
STOCH_LEN = 14
STOCH_K = 7  # BACKTEST_CHANGE_101: marathon winner PF 2.28 WR 52.9% Sharpe 4.41 (was 5)
STOCH_D = 7  # BACKTEST_CHANGE_101: slower smoothing (was 5)

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def isoformat(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def load_scores_file(path: Path) -> Dict[str, float]:
    try:
        if not path.exists():
            return {}
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                if isinstance(value, (int, float)):
                    cleaned[key.upper()] = float(value)
            return cleaned
    except Exception:
        pass
    return {}

async def ensure_path_writable(path: Path, file_mode: int = 0o664, dir_mode: int = 0o775) -> None:
    path_obj = Path(path)
    try:
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        if not os.access(path_obj.parent, os.W_OK):
            try: os.chmod(path_obj.parent, dir_mode)
            except Exception: pass
    except Exception: pass
    try:
        if not path_obj.exists():
            try: path_obj.touch(mode=file_mode, exist_ok=True)
            except Exception: pass
        if not os.access(path_obj, os.W_OK):
            try: os.chmod(path_obj, file_mode)
            except Exception: pass
    except Exception: pass

def ensure_path_writable_sync(path: Path, file_mode: int = 0o664, dir_mode: int = 0o775) -> None:
    # Synchronous version needed for some calls
    path_obj = Path(path)
    try:
        path_obj.parent.mkdir(parents=True, exist_ok=True)
    except Exception: pass
    
# def ensure_path_writable_sync(path: Path, file_mode: int = 0o664, dir_mode: int = 0o775) -> None:
#     path_obj = Path(path)
#     try:
#         path_obj.parent.mkdir(parents=True, exist_ok=True)
#         try:
#             if not os.access(path_obj.parent, os.W_OK):
#                 os.chmod(path_obj.parent, dir_mode)
#         except PermissionError:
#             pass
#     except Exception:
#         pass
#     try:
#         if not path_obj.exists():
#             try:
#                 path_obj.touch(mode=file_mode, exist_ok=True)
#             except PermissionError:
#                 pass
#         try:
#             if not os.access(path_obj, os.W_OK):
#                 os.chmod(path_obj, file_mode)
#         except PermissionError:
#             pass
#     except Exception:
#         pass


# async def ensure_path_writable(path: Path, file_mode: int = 0o664, dir_mode: int = 0o775) -> None:
#     path_obj = Path(path)
#     try:
#         path_obj.parent.mkdir(parents=True, exist_ok=True)
#         if not os.access(path_obj.parent, os.W_OK):
#             try:
#                 os.chmod(path_obj.parent, dir_mode)
#             except PermissionError:
#                 pass
#     except Exception:
#         pass
#     try:
#         if not path_obj.exists():
#             try:
#                 path_obj.touch(mode=file_mode, exist_ok=True)
#             except PermissionError:
#                 pass
#         if not os.access(path_obj, os.W_OK):
#             try:
#                 os.chmod(path_obj, file_mode)
#             except PermissionError:
#                 pass
#     except Exception:
#         pass

def load_score_ranges(path: Path) -> Dict[str, Dict[str, float]]:
    try:
        if not path.exists():
            return {}
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cleaned: Dict[str, Dict[str, float]] = {}
            for key, value in data.items():
                if isinstance(value, dict):
                    try:
                        cleaned[key.upper()] = {
                            "raw_min": float(value.get("raw_min", 0.0)),
                            "raw_max": float(value.get("raw_max", 0.0))
                        }
                    except (TypeError, ValueError):
                        continue
            return cleaned
    except Exception:
        pass
    return {}

async def save_json_file(path: Path, payload: Dict[str, Any]) -> None:
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{int(time.time()*1000)}.tmp")
        
        # Use orjson + default_serializer
        content_bytes = orjson.dumps(payload, option=orjson.OPT_INDENT_2, default=default_serializer)
        
        async with aiofiles.open(tmp, 'wb') as f:
            await f.write(content_bytes)
            await f.flush()
            await asyncio.to_thread(os.fsync, f.fileno())
        await asyncio.to_thread(os.replace, tmp, path)
    except Exception as e:
        logger.error(f"Failed to save JSON {path}: {e}")

# async def save_json_file(path: Path, payload: Dict[str, Any]) -> None:
#     try:
#         path = Path(path)
#         if not path.parent.exists():
#             path.parent.mkdir(parents=True, exist_ok=True)
#         random_suffix = int(time.time() * 1000000)
#         tmp = path.parent / f"{path.name}.{random_suffix}.atom"
#         if len(payload) > 500:
#             content = await asyncio.to_thread(json.dumps, payload, indent=2)
#         else:
#             content = json.dumps(payload, indent=2)
#         async with aiofiles.open(tmp, 'w', encoding='utf-8') as f:
#             await f.write(content)
#             await f.flush()
#             await asyncio.to_thread(os.fsync, f.fileno())
#         await asyncio.to_thread(os.replace, tmp, path)
            
#     except Exception as e:
#         logger.error(f"Failed to save JSON {path}: {e}")
#         try:
#             if 'tmp' in locals() and tmp.exists():
#                 os.remove(tmp)
#         except: pass


def with_mark_price(df: pd.DataFrame, mark_price: Optional[float]) -> pd.DataFrame:
    if mark_price is None or df.empty:
        return df
    copy = df.copy()
    for column in ("open", "high", "low", "close"):
        if column in copy.columns:
            copy[column] = copy[column].astype(float)
    idx = copy.index[-1]
    copy.at[idx, "close"] = mark_price
    if "high" in copy.columns:
        copy.at[idx, "high"] = max(float(copy.at[idx, "high"]), mark_price)
    if "low" in copy.columns:
        copy.at[idx, "low"] = min(float(copy.at[idx, "low"]), mark_price)
    return copy

def load_symbols() -> List[str]:
    collected: Set[str] = set()
    files = [config.SYMBOLS_FILE, Path.cwd() / "symbols.json"]
    unique_paths = []
    seen_paths = set()
    for p in files:
        if str(p) not in seen_paths:
            unique_paths.append(p)
            seen_paths.add(str(p))

    logger.info(f"🔍 [DEBUG] Resolved BASE_PATH: {config.BASE_PATH}")
    
    found_any_file = False

    for path_obj in unique_paths:
        try:
            path = Path(path_obj)
            if not path.exists():
                logger.debug(f"   [DEBUG] ❌ File not found: {path}")
                continue
            
            found_any_file = True
            file_size = path.stat().st_size
            if file_size == 0:
                logger.warning(f"   [DEBUG] ⚠️ File exists but is EMPTY: {path}")
                continue

            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content: 
                    continue
                    
                data = json.loads(content)
                
                count_before = len(collected)
                
                if isinstance(data, dict):
                    # Try common keys
                    for key in ['symbols', 'pairs', 'data']:
                        if key in data and isinstance(data[key], list):
                            collected.update(str(s).strip().upper() for s in data[key])
                            break
                elif isinstance(data, list):
                    collected.update(str(symbol).strip().upper() for symbol in data)
                
                added = len(collected) - count_before
                if added > 0:
                    logger.info(f"   [DEBUG] ✅ Loaded {added} symbols from: {path}")

        except Exception as e:
            logger.error(f"   [DEBUG] 💥 Error reading {path}: {e}")

    all_symbols = sorted(collected)
    
    if not all_symbols:
        logger.critical("🚨🚨🚨 CRITICAL: NO SYMBOLS LOADED FROM ANY PATH!")
        logger.critical(f"checked paths: {[str(p) for p in unique_paths]}")
        # Return empty list, don't crash, but orchestrator will idle
        return []

    # Instance splitting logic
    instance_id = getattr(config, "WORKER_INSTANCE_ID", 0)
    total_instances = getattr(config, "WORKER_TOTAL_INSTANCES", 1)
    
    # If on macbook, default to single instance unless explicitly enabled
    if env == "macbook" and not getattr(config, "ENABLE_MULTI_INSTANCE_ON_MACBOOK", False):
        total_instances = 1

    if total_instances > 1 and 0 <= instance_id < total_instances:
        chunk_size = len(all_symbols) // total_instances
        start_idx = instance_id * chunk_size
        if instance_id == total_instances - 1:
            end_idx = len(all_symbols)
        else:
            end_idx = start_idx + chunk_size
        worker_symbols = all_symbols[start_idx:end_idx]
        logger.info(f"[WORKER {instance_id}/{total_instances-1}] Processing {len(worker_symbols)} symbols ({start_idx}-{end_idx-1}) out of {len(all_symbols)} total")
        return worker_symbols
    logger.info(f"✅ Final Load: {len(all_symbols)} symbols (Single Instance)")
    return all_symbols


# def load_symbols() -> List[str]:
#     collected: Set[str] = set()
#     for file_path in files:
#         try:
#             path = Path(file_path)
#             if path.exists():
#                 with open(path, "r") as f:
#                     data = json.load(f)
#                 if isinstance(data, dict):
#                     for key in ['symbols', 'pairs', 'data']:
#                         if key in data and isinstance(data[key], list):
#                             collected.update(str(s).strip().upper() for s in data[key])
#                             break
#                 elif isinstance(data, list):
#                     collected.update(str(symbol).strip().upper() for symbol in data)
#         except Exception as e:
#             logger.error(f"Error loading symbols from {file_path}: {e}")
#     all_symbols = sorted(collected)
#     if not all_symbols:
#         logger.warning("⚠️ No symbols loaded! Check symbols.json paths.")
#         return []
#     instance_id = getattr(config, "WORKER_INSTANCE_ID", 0)
#     total_instances = getattr(config, "WORKER_TOTAL_INSTANCES", 1)
#     if env == "macbook":
#         total_instances = 1
#     if total_instances > 1 and 0 <= instance_id < total_instances:
#         chunk_size = len(all_symbols) // total_instances
#         start_idx = instance_id * chunk_size
#         if instance_id == total_instances - 1:
#             end_idx = len(all_symbols)
#         else:
#             end_idx = start_idx + chunk_size
#         worker_symbols = all_symbols[start_idx:end_idx]
#         logger.info(f"[WORKER {instance_id}/{total_instances-1}] Processing {len(worker_symbols)} symbols ({start_idx}-{end_idx-1}) out of {len(all_symbols)} total")
#         return worker_symbols
#     logger.info(f"✅ Loaded {len(all_symbols)} symbols (Single Instance Mode)")
#     return all_symbols

def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

@dataclass
class SymbolTimeframeState:
    timeframe: str
    last_close: Optional[datetime] = None
    full_done: bool = False
    mid_done: bool = False
    trivial_done: bool = False
    mid_due: Optional[datetime] = None
    last_full_finish: Optional[datetime] = None
    last_mid_finish: Optional[datetime] = None
    last_df: Optional[pd.DataFrame] = None

class PriceCacheManager:
    WS_ENDPOINT = "wss://fstream.binance.com/ws/!markPrice@arr"

    def __init__(self, path: Path, redis_client: Optional[redis.Redis] = None, symbols: Optional[List[str]] = None, refresh_seconds: float = 2.0, max_age: float = 5.0):
        self.path = path
        self.refresh_seconds = refresh_seconds
        self.max_age = max_age
        self._redis = redis_client
        self._symbols = {s.upper() for s in symbols} if symbols else set()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [asyncio.create_task(self._file_loop()), asyncio.create_task(self._ws_loop()), asyncio.create_task(self._save_loop())]
        if self._redis:
            self._tasks.append(asyncio.create_task(self._redis_loop()))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._session:
            await self._session.close()
            self._session = None

    async def _file_loop(self) -> None:
        while self._running:
            try:
                await self._reload()
            except Exception:
                pass
            await asyncio.sleep(self.refresh_seconds)

    async def _save_loop(self) -> None:
        interval = 31.0
        while self._running:
            try:
                async with self._lock:
                    snapshot: Dict[str, Dict[str, Any]] = {}
                    now = utc_now()
                    for symbol, entry in self._data.items():
                        if not isinstance(entry, dict):
                            continue
                        price = entry.get("price")
                        ts_raw = entry.get("timestamp")
                        if price is None or ts_raw is None:
                            continue
                        try:
                            price_val = float(price)
                        except (TypeError, ValueError):
                            continue
                        ts_dt = ts_raw if isinstance(ts_raw, datetime) else self._parse_timestamp(ts_raw, now)
                        if not isinstance(ts_dt, datetime):
                            continue
                        if ts_dt.tzinfo is None:
                            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                        snapshot[symbol] = {"price": price_val, "timestamp": isoformat(ts_dt)}
                await save_json_file(self.path, snapshot)
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def _ws_loop(self) -> None:
        while self._running:
            try:
                if not self._session:
                    self._session = aiohttp.ClientSession()
                async with self._session.ws_connect(self.WS_ENDPOINT, heartbeat=30, autoping=True) as ws:
                    async for msg in ws:
                        if not self._running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                payload = json.loads(msg.data)
                                await self._handle_ws_payload(payload)
                            except Exception:
                                pass
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            await asyncio.sleep(6.0)
        if self._session:
            await self._session.close()
            self._session = None

    async def _redis_loop(self) -> None:
        interval = 7.0
        while self._running and self._redis:
            try:
                symbols = list(self._symbols) if self._symbols else []
                if symbols:
                    await self._bulk_fetch_from_redis(symbols)
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def update_symbols(self, symbols: List[str]) -> None:
        async with self._lock:
            self._symbols = {s.upper() for s in symbols}

    async def _handle_ws_payload(self, payload: Any) -> None:
        items = payload if isinstance(payload, list) else [payload]
        now = utc_now()
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("s") or "").upper()
            if not symbol:
                continue
            if self._symbols and symbol not in self._symbols:
                continue
            price_raw = item.get("p") or item.get("markPrice")
            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                continue
            event_ms = item.get("E") or item.get("T")
            ts = self._parse_timestamp(event_ms, now)
            await self._update_price(symbol, price, ts, "ws")

    async def _reload(self) -> None:
        if not self.path.exists():
            return
        async with aiofiles.open(self.path, "r") as f:
            raw = await f.read()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return
        now = utc_now()
        updates: List[Tuple[str, float, datetime]] = []
        if isinstance(data, dict):
            for symbol, value in data.items():
                symbol = str(symbol).upper()
                price, ts = self._extract_price(value, now)
                if price is not None:
                    updates.append((symbol, price, ts))
        async with self._lock:
            for symbol, price, ts in updates:
                current = self._data.get(symbol)
                if not current or current.get("timestamp") is None or ts >= current.get("timestamp"):
                    self._data[symbol] = {"price": price, "timestamp": ts, "source": "file"}

    async def _update_price(self, symbol: str, price: float, ts: datetime, source: str) -> None:
        async with self._lock:
            self._data[symbol] = {"price": price, "timestamp": ts, "source": source}

    async def get_price_with_ts(self, symbol: str) -> Tuple[Optional[float], Optional[datetime]]:
        """Returns (price, timestamp) tuple. Timestamp is the DATA ORIGIN time."""
        symbol = symbol.upper()
        now = utc_now()
        
        # 1. Check Local Memory Cache
        async with self._lock:
            entry = self._data.get(symbol)
        
        if entry:
            ts = entry.get("timestamp")
            price = entry.get("price")
            # If we have a price and it's not ancient (e.g. > 30s), use it
            # We are more lenient here because we want the *timestamp* even if slightly old
            if price is not None:
                # Ensure ts is a datetime object
                if not isinstance(ts, datetime):
                    ts = self._parse_timestamp(ts, now)
                return float(price), ts

        # 2. Check Redis (Fallback)
        # Note: _fetch_from_redis usually updates internal cache, but let's grab it directly
        if self._redis:
            try:
                raw = await self._redis.get(f"mark_price:{symbol}")
                price, ts = self._decode_price_with_ts(raw)
                if price is not None:
                    # Update internal cache for next time
                    await self._update_price(symbol, price, ts, "redis")
                    return price, ts
            except Exception: pass
            
        return None, None

    def _decode_price_with_ts(self, raw: Any) -> Tuple[Optional[float], datetime]:
        # Helper to parse redis payload {price: x, timestamp: y}
        now = utc_now()
        if raw is None: return None, now
        
        price = None
        ts = now
        
        if isinstance(raw, (bytes, str)):
            try:
                if isinstance(raw, bytes): raw = raw.decode()
                # Try JSON
                if raw.strip().startswith('{'):
                    data = json.loads(raw)
                    return self._extract_price(data, now)
                else:
                    # Raw float string
                    return float(raw), now
            except Exception: pass
            
        return price, ts

    async def _bulk_fetch_from_redis(self, symbols: List[str]) -> None:
        if not self._redis or not symbols:
            return
        keys = [f"mark_price:{sym}" for sym in symbols]
        try:
            values = await self._redis.mget(*keys)
        except Exception:
            pass
        now = utc_now()
        if not values:
            return
        for symbol, raw in zip(symbols, values):
            price = self._decode_price(raw)
            if price is not None:
                await self._update_price(symbol, price, now, "redis")

    async def _fetch_from_redis(self, symbol: str) -> Optional[float]:
        if not self._redis:
            return None
        try:
            raw = await self._redis.get(f"mark_price:{symbol}")
            price = self._decode_price(raw)
            if price is not None:
                await self._update_price(symbol, price, utc_now(), "redis")
                return price
        except Exception:
            pass
        return None

    def _decode_price(self, raw: Any) -> Optional[float]:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            payload = raw.strip()
            if not payload:
                return None
            if payload[0] in "[{":
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    return None
                return self._extract_price(data, utc_now())[0]
            try:
                return float(payload)
            except ValueError:
                return None
        if isinstance(raw, dict):
            return self._extract_price(raw, utc_now())[0]
        return None

    def _extract_price(self, value: Any, default_ts: datetime) -> Tuple[Optional[float], datetime]:
        ts = default_ts
        price: Optional[float] = None
        if isinstance(value, dict):
            for key in ("price", "mark_price", "markPrice", "p", "close", "c"):
                if key in value:
                    try:
                        price = float(value[key])
                        break
                    except (TypeError, ValueError):
                        continue
            ts_raw = value.get("timestamp") or value.get("ts") or value.get("E") or value.get("T") or value.get("time")
            if ts_raw is not None:
                ts = self._parse_timestamp(ts_raw, default_ts)
        else:
            try:
                price = float(value)
            except (TypeError, ValueError):
                price = None
        return price, ts

    def _parse_timestamp(self, raw: Any, default: datetime) -> datetime:
        if raw is None:
            return default
        if isinstance(raw, datetime):
            return raw.astimezone(timezone.utc)
        if isinstance(raw, (int, float)):
            value = raw / 1000.0 if raw > 1e12 else raw
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(raw, str):
            try:
                payload = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
                return datetime.fromisoformat(payload)
            except ValueError:
                return default
        return default

    async def get_price(self, symbol: str) -> Optional[float]:
        symbol = symbol.upper()
        now = utc_now()
        async with self._lock:
            entry = self._data.get(symbol)
        if entry:
            ts = entry.get("timestamp") or now
            price = entry.get("price")
            if price is not None and (now - ts).total_seconds() <= self.max_age:
                return float(price)
            stale_price = price
        else:
            stale_price = None
        price = await self._fetch_from_redis(symbol)
        if price is not None:
            return price
        return float(stale_price) if stale_price is not None else None

class KlineManager:
    def __init__(self, base_path: Path, env: str, target_bars: int = 1500):
        self.base_path = base_path
        self.env = env
        self.target_bars = target_bars
        self.cache: Dict[Tuple[str, Path], Tuple[float, float, pd.DataFrame]] = {}
        self.max_cache_size = 3000
        self.directories = self._resolve_directories()
        self._read_semaphore = asyncio.Semaphore(20)

    def _resolve_directories(self) -> List[Path]:
        base = self.base_path
        mapping = {
            "macbook": [base / "klines_cache", base / "klines_cache_gateway", base / "klines_cache_server"],
            "gateway": [base / "klines_cache_gateway", base / "klines_cache", base / "klines_cache_macbook"],
            "server": [base / "klines_cache_server", base / "klines_cache_gateway", base / "klines_cache"]
        }
        dirs = mapping.get(self.env.lower(), [base / "klines_cache", base / "klines_cache_gateway"])
        result: List[Path] = []
        seen = set()
        for path in dirs:
            if not isinstance(path, Path):
                path = Path(path)
            if path.exists() and path.is_dir() and str(path) not in seen:
                result.append(path)
                seen.add(str(path))
        return result

    async def _load_file(self, path: Path) -> Optional[pd.DataFrame]:
        try:
            stat = path.stat()
            cache_key = (path.name, path)
            cached = self.cache.get(cache_key)
            # Cache hit only if: st_mtime unchanged AND loaded <5 min ago
            # The 5-min TTL catches cases where mtime didn't change but data needs refresh
            _now = time.time()
            _cache_valid = (
                cached is not None and
                len(cached) == 3 and
                cached[0] == stat.st_mtime and
                (_now - cached[1]) < 300
            )
            if _cache_valid:
                return cached[2]
            # Use async file I/O to avoid blocking and limit concurrency
            async with self._read_semaphore:
                async with aiofiles.open(path, "r") as f:
                    raw = await f.read()
                # Offload JSON parsing to thread pool (CPU-bound)
                data = await asyncio.to_thread(json.loads, raw)
            if not isinstance(data, list) or not data:
                return None
            data = data[-self.target_bars:]
            df = pd.DataFrame(data)
            if "timestamp" in df.columns:
                df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            df.attrs["origin"] = str(path)
            self.cache[cache_key] = (stat.st_mtime, _now, df)  # (mtime, load_time, dataframe)
            if len(self.cache) > self.max_cache_size:
                self.cache.pop(next(iter(self.cache)))
            return df
        except Exception:
            pass
        return None

    async def _calculate_3m_from_1m(self, symbol: str) -> Optional[pd.DataFrame]:
        try:
            df_1m, _, _ = await self.get_latest(symbol, "1m")
            if df_1m is None or df_1m.empty or "timestamp_dt" not in df_1m.columns:
                return None
            df_1m = df_1m.copy()
            df_1m = df_1m.sort_values("timestamp_dt").dropna(subset=["timestamp_dt"])
            if df_1m.empty or len(df_1m) < 3:
                return None
            df_1m = df_1m.set_index("timestamp_dt")
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df_1m.columns:
                    df_1m[col] = pd.to_numeric(df_1m[col], errors="coerce")
            df_3m = df_1m.resample("3min", label="right", closed="right").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
            if df_3m.empty:
                return None
            df_3m = df_3m.reset_index()
            df_3m["timestamp"] = df_3m["timestamp_dt"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            return df_3m
        except Exception:
            return None

    async def _calculate_15m_from_3m(self, symbol: str) -> Optional[pd.DataFrame]:
        # Gap-fill: load native 15m (4-year source of truth), then only fill individual
        # missing bars from 3m. Never synthesize 15m from scratch — 3m covers only a few days.
        try:
            file_name = f"{symbol}_15m.json"
            native_df = None
            for directory in self.directories:
                path = directory / file_name
                if not path.exists():
                    continue
                candidate = await self._load_file(path)
                if candidate is not None and not candidate.empty and "timestamp_dt" in candidate.columns:
                    native_df = candidate
                    break
            if native_df is None:
                # No native 15m file — synthesize entirely from 3m (synthetic fragment better than None)
                df_3m, _, _ = await self.get_latest(symbol, "3m")
                if df_3m is None or df_3m.empty or "timestamp_dt" not in df_3m.columns:
                    return None
                df_3m = df_3m.sort_values("timestamp_dt").dropna(subset=["timestamp_dt"])
                if len(df_3m) < 5:
                    return None
                df_3m = df_3m.set_index("timestamp_dt")
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in df_3m.columns:
                        df_3m[col] = pd.to_numeric(df_3m[col], errors="coerce")
                df_15m = df_3m.resample("15min", label="right", closed="right").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
                if df_15m.empty:
                    return None
                df_15m = df_15m.reset_index()
                df_15m["timestamp"] = df_15m["timestamp_dt"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                return df_15m
            native_df = native_df.sort_values("timestamp_dt").copy()
            native_idx = native_df.set_index("timestamp_dt")
            last_ts = native_idx.index[-1]
            check_from = last_ts - pd.Timedelta(hours=2)
            expected = pd.date_range(check_from.ceil("15min"), last_ts + pd.Timedelta(minutes=15), freq="15min", tz=last_ts.tzinfo)
            missing_ts = expected.difference(native_idx.index)
            if missing_ts.empty:
                return native_df
            df_3m, _, _ = await self.get_latest(symbol, "3m")
            if df_3m is None or df_3m.empty or "timestamp_dt" not in df_3m.columns:
                return native_df
            df_3m = df_3m.sort_values("timestamp_dt").set_index("timestamp_dt")
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df_3m.columns:
                    df_3m[col] = pd.to_numeric(df_3m[col], errors="coerce")
            new_rows = []
            for ts in missing_ts:
                window = df_3m.loc[ts - pd.Timedelta(minutes=15):ts - pd.Timedelta(seconds=1)]
                if len(window) >= 3:
                    new_rows.append({"timestamp_dt": ts, "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "open": float(window["open"].iloc[0]), "high": float(window["high"].max()), "low": float(window["low"].min()), "close": float(window["close"].iloc[-1]), "volume": float(window["volume"].sum())})
            if not new_rows:
                return native_df
            result = pd.concat([native_df, pd.DataFrame(new_rows)], ignore_index=True).sort_values("timestamp_dt").drop_duplicates(subset=["timestamp_dt"]).reset_index(drop=True)
            return result
        except Exception:
            return None

    async def get_latest(self, symbol: str, timeframe: str) -> Tuple[Optional[pd.DataFrame], Optional[datetime], Optional[Path]]:
        file_name = f"{symbol}_{timeframe}.json"
        primary = self.directories[0] if self.directories else self.base_path / "klines_cache"
        fallbacks = [d for d in self.directories if d != primary]
        candidates = [primary] + fallbacks
        best_df = None
        best_ts = None
        best_path = None
        fallback_df = None
        fallback_ts = None
        fallback_path = None
        fallback_age = None
        now = utc_now()
        tolerance = TIMEFRAMES[timeframe]["seconds"]
        for directory in candidates:
            path = directory / file_name
            if not path.exists():
                continue
            df = await self._load_file(path)
            if df is None or df.empty or "timestamp_dt" not in df.columns:
                continue
            ts_series = df["timestamp_dt"].dropna()
            if ts_series.empty:
                continue
            close_ts = ts_series.iloc[-1].to_pydatetime().astimezone(timezone.utc)
            age_seconds = (now - close_ts).total_seconds()
            if age_seconds > tolerance:
                if fallback_df is None or fallback_age is None or age_seconds < fallback_age:
                    fallback_df = df
                    fallback_ts = close_ts
                    fallback_path = path
                    fallback_age = age_seconds
                continue
            best_df = df
            best_ts = close_ts
            best_path = path
            break
        if best_df is None and fallback_df is not None:
            best_df = fallback_df
            best_ts = fallback_ts
            best_path = fallback_path
        if best_df is None and timeframe == "3m":
            calc_df = await self._calculate_3m_from_1m(symbol)
            if calc_df is not None and not calc_df.empty and "timestamp_dt" in calc_df.columns:
                ts_series = calc_df["timestamp_dt"].dropna()
                if not ts_series.empty:
                    close_ts = ts_series.iloc[-1].to_pydatetime().astimezone(timezone.utc)
                    age_seconds = (now - close_ts).total_seconds()
                    if age_seconds <= tolerance:
                        best_df = calc_df
                        best_ts = close_ts
                        best_path = None
        if best_df is None and timeframe == "15m":
            calc_df = await self._calculate_15m_from_3m(symbol)
            if calc_df is not None and not calc_df.empty and "timestamp_dt" in calc_df.columns:
                ts_series = calc_df["timestamp_dt"].dropna()
                if not ts_series.empty:
                    close_ts = ts_series.iloc[-1].to_pydatetime().astimezone(timezone.utc)
                    age_seconds = (now - close_ts).total_seconds()
                    if age_seconds <= tolerance:
                        best_df = calc_df
                        best_ts = close_ts
                        best_path = None
        return best_df, best_ts, best_path
        # if timeframe == 'D' and (best_df is None or best_ts is None or (now - best_ts).total_seconds() > 86400 * 1.5):
        #     #await self._recalculate_D_from_4h(symbol, primary)
        #     file_path = primary / file_name
        #     if file_path.exists():
        #         cache_key = (file_path.name, file_path)
        #         if cache_key in self.cache:
        #             del self.cache[cache_key]
        #     for directory in candidates:
        #         path = directory / file_name
        #         if not path.exists():
        #             continue
        #         cache_key = (path.name, path)
        #         if cache_key in self.cache:
        #             del self.cache[cache_key]
        #         df = await self._load_file(path)
        #         if df is None or df.empty or "timestamp_dt" not in df.columns:
        #             continue
        #         ts_series = df["timestamp_dt"].dropna()
        #         if ts_series.empty:
        #             continue
        #         close_ts = ts_series.iloc[-1].to_pydatetime().astimezone(timezone.utc)
        #         best_df = df
        #         best_ts = close_ts
        #         best_path = path
        #         break
        # return best_df, best_ts, best_path

    # async def _recalculate_D_from_4h(self, symbol: str, klines_cache_dir: Path):
    #     """Recalculate 1-2 D klines from 4h klines when D klines are missing/stale"""
    #     try:
    #         candidate_dirs = []
    #         for item in [klines_cache_dir] + [d for d in self.directories if d != klines_cache_dir]:
    #             if item not in candidate_dirs:
    #                 candidate_dirs.append(item)
    #         source_4h = None
    #         for directory in candidate_dirs:
    #             path_4h = directory / f"{symbol}_4h.json"
    #             if path_4h.exists():
    #                 source_4h = path_4h
    #                 break
    #         if source_4h is None:
    #             return
    #         async with aiofiles.open(source_4h, 'r') as f:
    #             content = await f.read()
    #         data_4h = json.loads(content)
    #         if isinstance(data_4h, dict) and 'data' in data_4h:
    #             data_4h = data_4h['data']
    #         if not isinstance(data_4h, list) or not data_4h:
    #             return
    #         df_4h = pd.DataFrame(data_4h)
    #         if df_4h.empty or 'timestamp' not in df_4h.columns:
    #             return
    #         df_4h['timestamp'] = pd.to_datetime(df_4h['timestamp'], utc=True, errors='coerce')
    #         df_4h = df_4h.dropna(subset=['timestamp']).sort_values('timestamp')
    #         if len(df_4h) < 6:
    #             return
    #         df_D_existing = pd.DataFrame()
    #         latest_D_ts = None
    #         existing_D_path = None
    #         for directory in candidate_dirs:
    #             path_D = directory / f"{symbol}_D.json"
    #             if path_D.exists():
    #                 existing_D_path = path_D
    #                 break
    #         if existing_D_path is not None:
    #             try:
    #                 async with aiofiles.open(existing_D_path, 'r') as f:
    #                     content_D = await f.read()
    #                 data_D = json.loads(content_D)
    #                 if isinstance(data_D, dict) and 'data' in data_D:
    #                     data_D = data_D['data']
    #                 if isinstance(data_D, list) and data_D:
    #                     df_D_existing = pd.DataFrame(data_D)
    #                     if 'timestamp' in df_D_existing.columns:
    #                         df_D_existing['timestamp'] = pd.to_datetime(df_D_existing['timestamp'], utc=True, errors='coerce')
    #                         df_D_existing = df_D_existing.dropna(subset=['timestamp']).sort_values('timestamp')
    #                         if not df_D_existing.empty:
    #                             latest_D_ts = df_D_existing['timestamp'].max()
    #             except Exception:
    #                 pass
    #         now_utc = datetime.now(timezone.utc)
    #         if latest_D_ts is not None:
    #             age_hours = (now_utc - latest_D_ts.to_pydatetime()).total_seconds() / 3600
    #             if age_hours < 20:
    #                 return
    #             df_4h_new = df_4h[df_4h['timestamp'] > latest_D_ts]
    #         else:
    #             df_4h_new = df_4h.tail(12)
    #         if len(df_4h_new) < 6:
    #             return
    #         df_4h_indexed = df_4h_new.set_index('timestamp').sort_index()
    #         df_D_new = df_4h_indexed.resample("D", label='right', closed='right').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
    #         if df_D_new.empty:
    #             return
    #         df_D_new = df_D_new.reset_index()
    #         df_D_new['timestamp'] = df_D_new['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    #         if not df_D_existing.empty:
    #             df_D_existing['timestamp'] = df_D_existing['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    #             combined = pd.concat([df_D_existing, df_D_new], ignore_index=True)
    #             combined = combined.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
    #             final_df = combined
    #         else:
    #             final_df = df_D_new
    #         rows = final_df.to_dict('records')
    #         json_str = json.dumps(rows, indent=2)
    #         saved_paths: List[str] = []
    #         for directory in candidate_dirs:
    #             try:
    #                 await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    #             except Exception:
    #                 continue
    #             target_path = directory / f"{symbol}_D.json"
    #             tmp_file_path = None
    #             try:
    #                 def _write_tmp(payload: str, target: Path) -> str:
    #                     with tempfile.NamedTemporaryFile('w', dir=target.parent, delete=False, prefix='.tmp_ez_', suffix='.json', encoding='utf-8') as tmp:
    #                         tmp.write(payload)
    #                         tmp.flush()
    #                         os.fsync(tmp.fileno())
    #                         return tmp.name
    #                 tmp_file_path = await asyncio.to_thread(_write_tmp, json_str, target_path)
    #                 await asyncio.to_thread(os.replace, tmp_file_path, target_path)
    #                 cache_key = (target_path.name, target_path)
    #                 if cache_key in self.cache:
    #                     del self.cache[cache_key]
    #                 saved_paths.append(str(target_path))
    #             except Exception as e:
    #                 logger.error(f"Error saving D klines for {symbol} to {target_path}: {e}")
    #                 if tmp_file_path and os.path.exists(tmp_file_path):
    #                     try:
    #                         await asyncio.to_thread(os.remove, tmp_file_path)
    #                     except Exception:
    #                         pass
    #         if saved_paths:
    #             logger.info(f"✅ Recalculated {len(df_D_new)} D klines from 4h for {symbol} and saved to {', '.join(saved_paths)}")
    #     except Exception as e:
    #         logger.debug(f"Error recalculating D from 4h for {symbol}: {e}")
def linreg_features(series: pd.Series, length: int) -> Tuple[Optional[float], Optional[float]]:
    if series is None or len(series) < length:
        return None, None
    window = series.iloc[-length:]
    x = np.arange(len(window))
    y = window.values.astype(float)
    x_mean = np.mean(x)
    y_mean = np.mean(y)
    denominator = np.sum((x - x_mean) ** 2)
    if denominator == 0:
        return None, None
    slope = np.sum((x - x_mean) * (y - y_mean)) / denominator
    y_fit = x_mean + slope * (x - x_mean)
    residuals = y - y_fit
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y - y_mean) ** 2)
    linearity = 1 - ss_res / ss_tot if ss_tot != 0 else 0
    return float(slope), float(linearity)

def bb_features(series: pd.Series, length: int = 20, std_mult: float = 2.0) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if series is None or len(series) < length:
        return None, None, None
    window = series.iloc[-length:]
    mid = float(window.mean())
    std = float(window.std(ddof=0))
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    price = float(series.iloc[-1])
    band_width = upper - lower
    pct_b = (price - lower) / band_width if band_width > 0 else 0.5
    return upper, lower, max(0.0, min(1.0, pct_b))


def kc_features(close_series: pd.Series, high_series: pd.Series, low_series: pd.Series, length: int = 20, atr_mult: float = 1.5) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    # Keltner Channel: EMA(close, length) ± atr_mult × ATR(length). Used by Squeeze (BB-inside-KC compression).
    if close_series is None or len(close_series) < length:
        return None, None, None
    ema_mid = close_series.ewm(span=length, adjust=False).mean()
    df_atr = pd.DataFrame({"high": high_series, "low": low_series, "close": close_series})
    atr = atr_series(df_atr, length)
    if atr is None or atr.empty:
        return None, None, None
    mid = float(ema_mid.iloc[-1])
    a = float(atr.iloc[-1])
    if not (pd.notna(mid) and pd.notna(a) and a > 0):
        return None, None, None
    upper = mid + atr_mult * a
    lower = mid - atr_mult * a
    return upper, mid, lower


def squeeze_features(close_series: pd.Series, high_series: pd.Series, low_series: pd.Series, length: int = 20, bb_mult: float = 2.0, kc_mult: float = 1.5) -> Tuple[Optional[bool], Optional[int]]:
    # Squeeze (LazyBear / TTM): ON when BB is INSIDE KC (low volatility compression).
    # Fire: when squeeze RELEASES (BB exits KC) — direction from close vs midline. Returns (is_squeezed, fire_dir)
    # fire_dir: +1 = bull release (close > mid on release bar), -1 = bear release, 0 = no release this bar.
    if close_series is None or len(close_series) < length + 1:
        return None, None
    bb_u, bb_l, _ = bb_features(close_series, length=length, std_mult=bb_mult)
    kc_u, kc_m, kc_l = kc_features(close_series, high_series, low_series, length=length, atr_mult=kc_mult)
    if any(v is None for v in (bb_u, bb_l, kc_u, kc_m, kc_l)):
        return None, None
    is_squeezed_now = bool(bb_u <= kc_u and bb_l >= kc_l)
    # Prior bar squeeze state (need length+1 bars for prior eval)
    prev_close = close_series.iloc[:-1]
    prev_high = high_series.iloc[:-1] if high_series is not None else None
    prev_low = low_series.iloc[:-1] if low_series is not None else None
    bb_u_p, bb_l_p, _ = bb_features(prev_close, length=length, std_mult=bb_mult)
    kc_u_p, kc_m_p, kc_l_p = kc_features(prev_close, prev_high, prev_low, length=length, atr_mult=kc_mult) if prev_high is not None and prev_low is not None else (None, None, None)
    was_squeezed = bool(bb_u_p is not None and bb_l_p is not None and kc_u_p is not None and kc_l_p is not None and bb_u_p <= kc_u_p and bb_l_p >= kc_l_p)
    fire_dir = 0
    if was_squeezed and not is_squeezed_now:
        last_close = float(close_series.iloc[-1])
        fire_dir = 1 if last_close > kc_m else (-1 if last_close < kc_m else 0)
    return is_squeezed_now, int(fire_dir)


def find_pivots_arr(arr: np.ndarray, lookback: int = 5) -> Tuple[np.ndarray, np.ndarray]:
    # Confirmed pivots only (lagged by `lookback` bars after — no repaint).
    n = len(arr); ph = np.zeros(n, dtype=bool); pl = np.zeros(n, dtype=bool)
    for i in range(lookback, n - lookback):
        a = arr[i]; lwin = arr[i - lookback:i]; rwin = arr[i + 1:i + 1 + lookback]
        if a > lwin.max() and a > rwin.max(): ph[i] = True
        if a < lwin.min() and a < rwin.min(): pl[i] = True
    return ph, pl


def detect_divergence(price_arr: np.ndarray, ind_arr: np.ndarray, lookback: int = 5, decay: int = 10, match_window: int = 3) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    # Pivot-based regular/hidden divergence. Returns (reg_bull, reg_bear, hid_bull, hid_bear) int8 arrays.
    # reg_bull: price LL + indicator HL. reg_bear: price HH + indicator LH.
    # hid_bull: price HL + indicator LL. hid_bear: price LH + indicator HH.
    # Signal active for `decay` bars starting at confirmation bar (i_curr + lookback).
    n = len(price_arr)
    rb = np.zeros(n, dtype=np.int8); be = np.zeros(n, dtype=np.int8)
    hb = np.zeros(n, dtype=np.int8); hbe = np.zeros(n, dtype=np.int8)
    p_ph, p_pl = find_pivots_arr(price_arr, lookback)
    i_ph, i_pl = find_pivots_arr(ind_arr, lookback)
    p_lows = np.where(p_pl)[0]; p_highs = np.where(p_ph)[0]
    i_lows = np.where(i_pl)[0]; i_highs = np.where(i_ph)[0]
    for k in range(1, len(p_lows)):
        ic = p_lows[k]; ip = p_lows[k - 1]
        c_match = i_lows[(i_lows >= ic - match_window) & (i_lows <= ic + match_window)]
        p_match = i_lows[(i_lows >= ip - match_window) & (i_lows <= ip + match_window)]
        if len(c_match) == 0 or len(p_match) == 0: continue
        ic_i = c_match[np.argmin(np.abs(c_match - ic))]
        ip_i = p_match[np.argmin(np.abs(p_match - ip))]
        confirm = ic + lookback
        if confirm >= n: continue
        end = min(n, confirm + decay)
        if price_arr[ic] < price_arr[ip] and ind_arr[ic_i] > ind_arr[ip_i]: rb[confirm:end] = 1
        if price_arr[ic] > price_arr[ip] and ind_arr[ic_i] < ind_arr[ip_i]: hb[confirm:end] = 1
    for k in range(1, len(p_highs)):
        ic = p_highs[k]; ip = p_highs[k - 1]
        c_match = i_highs[(i_highs >= ic - match_window) & (i_highs <= ic + match_window)]
        p_match = i_highs[(i_highs >= ip - match_window) & (i_highs <= ip + match_window)]
        if len(c_match) == 0 or len(p_match) == 0: continue
        ic_i = c_match[np.argmin(np.abs(c_match - ic))]
        ip_i = p_match[np.argmin(np.abs(p_match - ip))]
        confirm = ic + lookback
        if confirm >= n: continue
        end = min(n, confirm + decay)
        if price_arr[ic] > price_arr[ip] and ind_arr[ic_i] < ind_arr[ip_i]: be[confirm:end] = 1
        if price_arr[ic] < price_arr[ip] and ind_arr[ic_i] > ind_arr[ip_i]: hbe[confirm:end] = 1
    return rb, be, hb, hbe


def bb_auto_tune(high_series: pd.Series, low_series: pd.Series, close_series: pd.Series, length: int = 20, lookback: int = 100, touch_pct: float = 0.002) -> Tuple[float, float, float, float, int]:
    """Auto-tune BB σ multiplier to maximize upper+lower band touches.
    Sweeps σ from 1.5 to 3.5, counts bars where high touches upper or low touches lower.
    Returns (best_mult, auto_upper, auto_lower, auto_pct_b, touch_count).
    touch_pct: how close price must be to band to count as a touch (0.2% default)."""
    n = len(close_series)
    if n < length + 10:
        return 2.0, 0, 0, 0.5, 0
    # Use more history for touch counting but BB params from rolling window
    lb = min(lookback, n - length)
    close_arr = close_series.values[-lb - length:].astype(np.float64)
    high_arr = high_series.values[-lb:].astype(np.float64) if high_series is not None and len(high_series) >= lb else close_arr[-lb:]
    low_arr = low_series.values[-lb:].astype(np.float64) if low_series is not None and len(low_series) >= lb else close_arr[-lb:]
    # Rolling SMA and std over the lookback period
    best_mult = 2.0
    best_touches = 0
    # Precompute rolling mean/std for the last `lb` bars
    _rolling_mid = np.array([np.mean(close_arr[i:i + length]) for i in range(lb)])
    _rolling_std = np.array([np.std(close_arr[i:i + length], ddof=0) for i in range(lb)])
    for mult_10 in range(15, 36):  # 1.5 to 3.5 in 0.1 steps
        mult = mult_10 / 10.0
        upper = _rolling_mid + mult * _rolling_std
        lower = _rolling_mid - mult * _rolling_std
        # Count touches: high within touch_pct of upper OR low within touch_pct of lower
        # ALSO require the touch is a real reversal: high touches upper but close < upper (rejection)
        upper_touch = int(np.sum((high_arr >= upper * (1 - touch_pct)) & (high_arr <= upper * (1 + touch_pct))))
        lower_touch = int(np.sum((low_arr <= lower * (1 + touch_pct)) & (low_arr >= lower * (1 - touch_pct))))
        # Score: total touches BUT penalize imbalance (want BOTH sides touched, not just one)
        total = upper_touch + lower_touch
        balance = min(upper_touch, lower_touch) / max(upper_touch, lower_touch, 1)
        score = total * (0.5 + 0.5 * balance)  # Perfect balance = full score, one-sided = 50%
        if score > best_touches:
            best_touches = score
            best_mult = mult
    # Compute final bands at best_mult using latest window
    window = close_series.iloc[-length:]
    mid = float(window.mean())
    std = float(window.std(ddof=0))
    auto_upper = mid + best_mult * std
    auto_lower = mid - best_mult * std
    price = float(close_series.iloc[-1])
    bw = auto_upper - auto_lower
    auto_pct_b = (price - auto_lower) / bw if bw > 0 else 0.5
    return best_mult, auto_upper, auto_lower, round(max(0.0, min(1.0, auto_pct_b)), 4), int(best_touches)

def linreg_channel(series: pd.Series, length: int, std_mult: float = 2.5) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if series is None or len(series) < length:
        return None, None, None
    window = series.iloc[-length:]
    x = np.arange(len(window))
    y = window.values.astype(float)
    x_mean = np.mean(x); y_mean = np.mean(y)
    denom = np.sum((x - x_mean) ** 2)
    if denom == 0:
        return None, None, None
    slope = np.sum((x - x_mean) * (y - y_mean)) / denom
    y_fit = y_mean + slope * (x - x_mean)
    residual_std = float(np.std(y - y_fit, ddof=0))
    price = float(series.iloc[-1])
    fit_end = float(y_fit[-1])
    upper = fit_end + std_mult * residual_std
    lower = fit_end - std_mult * residual_std
    band_width = upper - lower
    pct_b = (price - lower) / band_width if band_width > 0 else 0.5
    return upper, lower, max(0.0, min(1.0, pct_b))

def donchian(high: pd.Series, low: pd.Series, window: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(high) < window or len(low) < window:
        return None, None, None
    high_series = high.rolling(window, min_periods=window).max()
    low_series = low.rolling(window, min_periods=window).min()
    if high_series.empty or low_series.empty:
        return None, None, None
    high_val = high_series.iloc[-1]
    low_val = low_series.iloc[-1]
    if pd.isna(high_val) or pd.isna(low_val):
        return None, None, None
    basis_val = (high_val + low_val) / 2.0
    return float(high_val), float(low_val), float(basis_val)

def donchian_prev(high: pd.Series, low: pd.Series, window: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(high) < window + 1 or len(low) < window + 1:
        return None, None, None
    high_series = high.rolling(window, min_periods=window).max()
    low_series = low.rolling(window, min_periods=window).min()
    if len(high_series) < 2 or len(low_series) < 2:
        return None, None, None
    high_val = high_series.iloc[-2]
    low_val = low_series.iloc[-2]
    if pd.isna(high_val) or pd.isna(low_val):
        return None, None, None
    basis_val = (high_val + low_val) / 2.0
    return float(high_val), float(low_val), float(basis_val)

# Per-TF WaveTrend parameters (decoupled ESA/channel/signal)
# Validated by test_wt_optimization.py: 48 symbols, 4-6yr, 1h TF sweep (2026-03-25)
# Phase 0: ESA=6 DEMA (+7.97), sig=15 (+7.89), chan=8 (+7.89)
# Phase 1: ESA=8 (+7.33), chan=14 (+7.19)
# Phase 3: sig=12 (+7.15), smooth=3 (+7.13), CI=0.010 (+7.14)
# Phase 4: 1h_dom TF weights = Sharpe 10.2 (biggest single finding)
# Winner: esa8_chan10_1h_dom = Sharpe 10.25 SHORT, 9.27 LONG
# 15m validated separately by backtest_wt_15m_deep.py: ESA=8(DEMA) CHAN=25(EMA) SIG=30 SM=5 CI=0.020
WT_TF_PARAMS = {
    "1m": {"esa": 6, "chan": 8, "sig": 12, "smooth": 3, "esa_ma": "dema", "chan_ma": "ema", "ci": 0.010},
    "3m": {"esa": 6, "chan": 10, "sig": 12, "smooth": 3, "esa_ma": "dema", "chan_ma": "ema", "ci": 0.010},
    "5m": {"esa": 8, "chan": 12, "sig": 15, "smooth": 3, "esa_ma": "dema", "chan_ma": "ema", "ci": 0.010},
    "15m": {"esa": 8, "chan": 25, "sig": 30, "smooth": 5, "esa_ma": "dema", "chan_ma": "ema", "ci": 0.020},
    "1h": {"esa": 8, "chan": 10, "sig": 12, "smooth": 3, "esa_ma": "dema", "chan_ma": "ema", "ci": 0.010},
    "4h": {"esa": 8, "chan": 14, "sig": 15, "smooth": 3, "esa_ma": "dema", "chan_ma": "ema", "ci": 0.010},
    "D": {"esa": 10, "chan": 14, "sig": 15, "smooth": 3, "esa_ma": "dema", "chan_ma": "ema", "ci": 0.015},
}

def _dema_pd(series: pd.Series, span: int) -> pd.Series:
    e1 = series.ewm(span=span, adjust=False).mean()
    e2 = e1.ewm(span=span, adjust=False).mean()
    return 2 * e1 - e2

def _ma_pd(series: pd.Series, span: int, ma_type: str) -> pd.Series:
    if ma_type == "dema":
        return _dema_pd(series, span)
    elif ma_type == "sma":
        return series.rolling(window=span, min_periods=1).mean()
    return series.ewm(span=span, adjust=False).mean()

def wavetrend(df: pd.DataFrame, timeframe: str = "") -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    p = WT_TF_PARAMS.get(timeframe, {"esa": 10, "chan": 10, "sig": 21, "smooth": 3, "esa_ma": "ema", "chan_ma": "ema", "ci": 0.015})
    if len(df) < max(p["esa"], p["sig"]):
        return None, None
    typical = (df["high"] + df["low"] + df["close"]) / 3
    esa = _ma_pd(typical, p["esa"], p["esa_ma"])
    d = _ma_pd((typical - esa).abs(), p["chan"], p["chan_ma"])
    ci = (typical - esa) / (p["ci"] * d.replace(0, 1e-10))
    wt1 = ci.ewm(span=p["sig"], adjust=False).mean()
    wt2 = wt1.rolling(window=p["smooth"], min_periods=1).mean()
    return wt1, wt2

def wavetrend_intelligence(wt1_series: pd.Series, wt2_series: pd.Series, close_series: pd.Series, high_series: pd.Series, low_series: pd.Series, timeframe: str) -> Dict[str, Any]:
    result = {}
    tf = timeframe
    wt1_arr = wt1_series.values.astype(float)
    wt2_arr = wt2_series.values.astype(float)
    close_arr = close_series.values.astype(float)
    high_arr = high_series.values.astype(float)
    low_arr = low_series.values.astype(float)
    n = len(wt1_arr)
    if n < 5:
        return result
    wt1_val = float(wt1_arr[-1])
    wt2_val = float(wt2_arr[-1])
    result[f"wt1_{tf}"] = wt1_val
    result[f"wt2_{tf}"] = wt2_val
    result[f"wt_score_{tf}"] = wt1_val - wt2_val
    # --- Cross Detection & Memory ---
    cross_above = (wt1_arr[1:] > wt2_arr[1:]) & (wt1_arr[:-1] <= wt2_arr[:-1])
    cross_below = (wt1_arr[1:] < wt2_arr[1:]) & (wt1_arr[:-1] >= wt2_arr[:-1])
    cross_above_idx = np.where(cross_above)[0] + 1
    cross_below_idx = np.where(cross_below)[0] + 1
    # Current bar cross (fires only on the exact cross bar)
    current_cross = None
    if len(cross_above_idx) > 0 and cross_above_idx[-1] == n - 1:
        current_cross = "BULL"
    elif len(cross_below_idx) > 0 and cross_below_idx[-1] == n - 1:
        current_cross = "BEAR"
    result[f"wt_cross_{tf}"] = current_cross
    # Most recent cross (persists after the cross bar — needed for evaluate functions)
    recent_cross = None
    recent_cross_value = None
    recent_cross_prev_value = None
    recent_cross_rising = None
    recent_cross_bars_ago = 999
    if len(cross_above_idx) > 0 and len(cross_below_idx) > 0:
        last_bull_idx = cross_above_idx[-1]; last_bear_idx = cross_below_idx[-1]
        if last_bull_idx > last_bear_idx:
            recent_cross = "BULL"; recent_cross_bars_ago = n - 1 - last_bull_idx
            recent_cross_value = float(wt1_arr[last_bull_idx])
            if len(cross_above_idx) >= 2: recent_cross_prev_value = float(wt1_arr[cross_above_idx[-2]]); recent_cross_rising = recent_cross_value > recent_cross_prev_value
        else:
            recent_cross = "BEAR"; recent_cross_bars_ago = n - 1 - last_bear_idx
            recent_cross_value = float(wt1_arr[last_bear_idx])
            if len(cross_below_idx) >= 2: recent_cross_prev_value = float(wt1_arr[cross_below_idx[-2]]); recent_cross_rising = recent_cross_value < recent_cross_prev_value
    elif len(cross_above_idx) > 0:
        recent_cross = "BULL"; recent_cross_bars_ago = n - 1 - cross_above_idx[-1]
        recent_cross_value = float(wt1_arr[cross_above_idx[-1]])
        if len(cross_above_idx) >= 2: recent_cross_prev_value = float(wt1_arr[cross_above_idx[-2]]); recent_cross_rising = recent_cross_value > recent_cross_prev_value
    elif len(cross_below_idx) > 0:
        recent_cross = "BEAR"; recent_cross_bars_ago = n - 1 - cross_below_idx[-1]
        recent_cross_value = float(wt1_arr[cross_below_idx[-1]])
        if len(cross_below_idx) >= 2: recent_cross_prev_value = float(wt1_arr[cross_below_idx[-2]]); recent_cross_rising = recent_cross_value < recent_cross_prev_value
    # Use recent cross for all fields (so they're never missing when a cross happened recently)
    cross_value = recent_cross_value
    cross_prev_value = recent_cross_prev_value
    cross_rising = recent_cross_rising
    result[f"wt_cross_{tf}"] = recent_cross  # Override: show most recent cross direction, not just current-bar
    result[f"wt_cross_value_{tf}"] = cross_value
    result[f"wt_cross_prev_value_{tf}"] = cross_prev_value
    result[f"wt_cross_rising_{tf}"] = cross_rising
    result[f"wt_cross_bars_ago_{tf}"] = recent_cross_bars_ago  # How many bars since the last cross
    lookback = min(50, n - 1)
    start = n - 1 - lookback
    result[f"wt_cross_count_bull_{tf}"] = int(np.sum(cross_above[max(0, start - 1):]))
    result[f"wt_cross_count_bear_{tf}"] = int(np.sum(cross_below[max(0, start - 1):]))
    # --- Structure Analysis (peaks/troughs via 3-bar pivot) ---
    peaks_mask = np.zeros(n, dtype=bool)
    troughs_mask = np.zeros(n, dtype=bool)
    if n >= 3:
        peaks_mask[1:-1] = (wt1_arr[1:-1] > wt1_arr[:-2]) & (wt1_arr[1:-1] > wt1_arr[2:])
        troughs_mask[1:-1] = (wt1_arr[1:-1] < wt1_arr[:-2]) & (wt1_arr[1:-1] < wt1_arr[2:])
    peak_idx = np.where(peaks_mask)[0]
    trough_idx = np.where(troughs_mask)[0]
    wt_peak = float(wt1_arr[peak_idx[-1]]) if len(peak_idx) >= 1 else None
    wt_peak_prev = float(wt1_arr[peak_idx[-2]]) if len(peak_idx) >= 2 else None
    wt_trough = float(wt1_arr[trough_idx[-1]]) if len(trough_idx) >= 1 else None
    wt_trough_prev = float(wt1_arr[trough_idx[-2]]) if len(trough_idx) >= 2 else None
    result[f"wt_peak_{tf}"] = wt_peak
    result[f"wt_peak_prev_{tf}"] = wt_peak_prev
    result[f"wt_trough_{tf}"] = wt_trough
    result[f"wt_trough_prev_{tf}"] = wt_trough_prev
    peak_structure = None
    if wt_peak is not None and wt_peak_prev is not None:
        peak_structure = "HH" if wt_peak > wt_peak_prev else "LH"
    trough_structure = None
    if wt_trough is not None and wt_trough_prev is not None:
        trough_structure = "HL" if wt_trough > wt_trough_prev else "LL"
    result[f"wt_peak_structure_{tf}"] = peak_structure
    result[f"wt_trough_structure_{tf}"] = trough_structure
    result[f"wt_structure_{tf}"] = peak_structure if peak_structure is not None else trough_structure
    # --- Divergence Detection (WT structure vs price structure) ---
    price_peak = None
    price_peak_prev = None
    price_trough = None
    price_trough_prev = None
    price_peaks_mask = np.zeros(n, dtype=bool)
    price_troughs_mask = np.zeros(n, dtype=bool)
    if n >= 3:
        price_peaks_mask[1:-1] = (high_arr[1:-1] > high_arr[:-2]) & (high_arr[1:-1] > high_arr[2:])
        price_troughs_mask[1:-1] = (low_arr[1:-1] < low_arr[:-2]) & (low_arr[1:-1] < low_arr[2:])
    ppeak_idx = np.where(price_peaks_mask)[0]
    ptrough_idx = np.where(price_troughs_mask)[0]
    if len(ppeak_idx) >= 2:
        price_peak = float(high_arr[ppeak_idx[-1]])
        price_peak_prev = float(high_arr[ppeak_idx[-2]])
    if len(ptrough_idx) >= 2:
        price_trough = float(low_arr[ptrough_idx[-1]])
        price_trough_prev = float(low_arr[ptrough_idx[-2]])
    divergence = None
    divergence_strength = 0.0
    if price_trough is not None and price_trough_prev is not None and wt_trough is not None and wt_trough_prev is not None:
        price_ll = price_trough < price_trough_prev
        wt_hl = wt_trough > wt_trough_prev
        price_hl = price_trough > price_trough_prev
        wt_ll = wt_trough < wt_trough_prev
        if price_ll and wt_hl:
            divergence = "BULL"
            price_range = abs(price_trough_prev - price_trough) / (abs(price_trough_prev) + 1e-10)
            wt_range = abs(wt_trough - wt_trough_prev) / (abs(wt_trough_prev) + 1e-10)
            divergence_strength = min(1.0, (price_range + wt_range) / 2.0)
        elif price_hl and wt_ll:
            divergence = "HIDDEN_BULL"
            divergence_strength = min(1.0, abs(wt_trough_prev - wt_trough) / (abs(wt_trough_prev) + 1e-10))
    if divergence is None and price_peak is not None and price_peak_prev is not None and wt_peak is not None and wt_peak_prev is not None:
        price_hh = price_peak > price_peak_prev
        wt_lh = wt_peak < wt_peak_prev
        price_lh = price_peak < price_peak_prev
        wt_hh = wt_peak > wt_peak_prev
        if price_hh and wt_lh:
            divergence = "BEAR"
            price_range = abs(price_peak - price_peak_prev) / (abs(price_peak_prev) + 1e-10)
            wt_range = abs(wt_peak_prev - wt_peak) / (abs(wt_peak_prev) + 1e-10)
            divergence_strength = min(1.0, (price_range + wt_range) / 2.0)
        elif price_lh and wt_hh:
            divergence = "HIDDEN_BEAR"
            divergence_strength = min(1.0, abs(wt_peak - wt_peak_prev) / (abs(wt_peak_prev) + 1e-10))
    result[f"wt_divergence_{tf}"] = divergence
    result[f"wt_divergence_strength_{tf}"] = round(divergence_strength, 4)
    # --- Velocity & Acceleration (4-Quadrant Model) ---
    lag = 3
    velocity = wt1_arr[-1] - wt1_arr[-1 - lag] if n > lag else 0.0
    velocity_prev = wt1_arr[-1 - lag] - wt1_arr[-1 - 2 * lag] if n > 2 * lag else 0.0
    acceleration = velocity - velocity_prev
    result[f"wt_velocity_{tf}"] = round(velocity, 4)
    result[f"wt_acceleration_{tf}"] = round(acceleration, 4)
    wt_rising = velocity > 0
    if wt_rising and acceleration > 0:
        momentum_state = "IMPULSE_UP"
    elif wt_rising and acceleration <= 0:
        momentum_state = "EXHAUST_UP"
    elif not wt_rising and acceleration < 0:
        momentum_state = "IMPULSE_DOWN"
    else:
        momentum_state = "EXHAUST_DOWN"
    result[f"wt_momentum_state_{tf}"] = momentum_state
    # --- Adaptive Bands (percentile, zscore, extreme) ---
    window = min(200, n)
    wt1_window = wt1_arr[-window:]
    percentile = float(np.sum(wt1_window <= wt1_val) / window * 100.0)
    mean_w = float(np.mean(wt1_window))
    std_w = float(np.std(wt1_window))
    zscore = (wt1_val - mean_w) / std_w if std_w > 1e-10 else 0.0
    result[f"wt_percentile_{tf}"] = round(percentile, 2)
    result[f"wt_zscore_{tf}"] = round(zscore, 4)
    result[f"wt_extreme_{tf}"] = abs(zscore) > 2.0
    # --- Wave Position (expansion/contraction via zero-crossing peak tracking) ---
    zero_cross_idx = np.where((wt1_arr[1:] * wt1_arr[:-1]) < 0)[0] + 1
    wave_phase = "TRANSITIONING"
    if len(zero_cross_idx) >= 1 and zero_cross_idx[-1] == n - 1:
        wave_phase = "TRANSITIONING"
    elif len(peak_idx) >= 2:
        abs_last = abs(wt1_arr[peak_idx[-1]])
        abs_prev = abs(wt1_arr[peak_idx[-2]])
        wave_phase = "EXPANDING" if abs_last > abs_prev else "CONTRACTING"
    elif len(trough_idx) >= 2:
        abs_last = abs(wt1_arr[trough_idx[-1]])
        abs_prev = abs(wt1_arr[trough_idx[-2]])
        wave_phase = "EXPANDING" if abs_last > abs_prev else "CONTRACTING"
    result[f"wt_wave_phase_{tf}"] = wave_phase
    # --- Legacy signal (backward compat) ---
    prev_wt1 = float(wt1_arr[-2]) if n > 1 else wt1_val
    prev_wt2 = float(wt2_arr[-2]) if n > 1 else wt2_val
    if prev_wt1 <= prev_wt2 and wt1_val > wt2_val and wt1_val < -50:
        result[f"wt_signal_{tf}"] = "BUY"
    elif prev_wt1 >= prev_wt2 and wt1_val < wt2_val and wt1_val > 50:
        result[f"wt_signal_{tf}"] = "SELL"
    else:
        result[f"wt_signal_{tf}"] = "NEUTRAL"
    return result

def heikin_ashi(df: pd.DataFrame) -> Tuple[str, Optional[str]]:
    if df.empty:
        return "neutral", None
    ha_close = (df["open"].iloc[-1] + df["high"].iloc[-1] + df["low"].iloc[-1] + df["close"].iloc[-1]) / 4.0
    if len(df) >= 2:
        prev_close = (df["open"].iloc[-2] + df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 4.0
        prev_open = (df["open"].iloc[-2] + df["close"].iloc[-2]) / 2.0
    else:
        prev_close = df["close"].iloc[-1]
        prev_open = df["open"].iloc[-1]
    ha_open = (prev_close + prev_open) / 2.0
    current_color = "green" if ha_close >= ha_open else "red"
    prev_color = None
    if len(df) >= 2:
        if len(df) >= 3:
            p_close = (df["open"].iloc[-3] + df["high"].iloc[-3] + df["low"].iloc[-3] + df["close"].iloc[-3]) / 4.0
            p_open = (df["open"].iloc[-3] + df["close"].iloc[-3]) / 2.0
        else:
            p_close = df["close"].iloc[-2]
            p_open = df["open"].iloc[-2]
        prev_color = "green" if p_close >= p_open else "red"
    return current_color, prev_color

def wma(series: pd.Series, length: int) -> pd.Series:
    """Weighted Moving Average"""
    if len(series) < length or length <= 0: return pd.Series(index=series.index, dtype=float)
    weights = np.arange(1, length + 1)
    return series.rolling(length).apply(lambda x: np.sum(weights * x) / np.sum(weights), raw=True)

def hma(series: pd.Series, length: int) -> pd.Series:
    """Hull Moving Average"""
    if len(series) < length: return pd.Series(index=series.index, dtype=float)
    half_length = max(1, int(length / 2))
    sqrt_length = max(1, int(np.sqrt(length)))
    wma_half = wma(series, half_length)
    wma_full = wma(series, length)
    diff = 2 * wma_half - wma_full
    return wma(diff, sqrt_length)

def thma(series: pd.Series, length: int) -> pd.Series:
    """Triple Hull Moving Average"""
    if len(series) < length: return pd.Series(index=series.index, dtype=float)
    len_6, len_4, len_2 = max(1, length // 6), max(1, length // 4), max(1, length // 2)
    wma_6, wma_4, wma_2 = wma(series, len_6), wma(series, len_4), wma(series, len_2)
    return wma(3 * wma_6 - wma_4 - wma_2, len_2)

def hull_trend_indicators(close_series: pd.Series, length_short: int = 9, length_long: int = 21) -> Tuple[Optional[bool], Optional[bool], Optional[bool]]:
    if len(close_series) < max(length_long, 3): return None, None, None
    try:
        thma_short = thma(close_series, length_short)
        thma_long = thma(close_series, length_long)
        hulle_short = hma(thma_short, 3)
        hulle_long = hma(thma_long, 3)
        if len(hulle_short) < 3: return None, None, None
        
        # Trend Up?
        t_up = bool(hulle_short.iloc[-1] > hulle_long.iloc[-1])
        
        # Swing Signals
        cur, prev, prev2 = hulle_short.iloc[-1], hulle_short.iloc[-2], hulle_short.iloc[-3]
        swingbuy = (cur >= prev) and (prev < prev2)
        swingsell = (cur <= prev) and (prev > prev2)
        
        return t_up, swingbuy, swingsell
    except Exception: return None, None, None

STOCH_LEN = 14
STOCH_K = 7  # BACKTEST_CHANGE_101: marathon winner PF 2.28 WR 52.9% Sharpe 4.41 (was 5)
STOCH_D = 7  # BACKTEST_CHANGE_101: slower smoothing (was 5)

def stoch_result(series: pd.Series) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], bool, bool]:
    def fallback(window: pd.Series) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], bool, bool]:
        if window.empty:
            return None, None, None, None, False, False
        max_val = float(window.max())
        min_val = float(window.min())
        if max_val == min_val:
            k_curr = d_curr = 50.0
            k_prev = d_prev = 50.0
        else:
            k_curr = float((window.iloc[-1] - min_val) / (max_val - min_val) * 100.0)
            prev_idx = -2 if len(window) > 1 else -1
            prev_val = window.iloc[prev_idx]
            k_prev = float((prev_val - min_val) / (max_val - min_val) * 100.0)
            d_curr = k_curr
            d_prev = k_prev
        crossover = k_prev <= d_prev and k_curr > d_curr
        crossunder = k_prev >= d_prev and k_curr < d_curr
        return k_curr, d_curr, k_prev, d_prev, crossover, crossunder
    length = STOCH_LEN
    if len(series) >= length: 
        stoch = stoch_rsi(series, length=length, k=STOCH_K, d=STOCH_D)
        if stoch is not None and not stoch.empty:
            k = stoch.iloc[:, 0].clip(lower=0, upper=100)
            d = stoch.iloc[:, 1].clip(lower=0, upper=100)
            
            # Ensure we have valid data at the end
            if not k.empty and pd.notna(k.iloc[-1]):
                k_curr = float(k.iloc[-1])
                d_curr = float(d.iloc[-1]) if not d.empty and pd.notna(d.iloc[-1]) else k_curr
                
                # Handle previous values safely
                if len(k) > 1 and pd.notna(k.iloc[-2]):
                    k_prev = float(k.iloc[-2])
                else:
                    k_prev = k_curr
                    
                if len(d) > 1 and pd.notna(d.iloc[-2]):
                    d_prev = float(d.iloc[-2])
                else:
                    d_prev = d_curr

                crossover = k_prev <= d_prev and k_curr > d_curr
                crossunder = k_prev >= d_prev and k_curr < d_curr
                
                return k_curr, d_curr, k_prev, d_prev, crossover, crossunder
    window = series.iloc[-min(len(series), max(2, STOCH_LEN)) :]
    return fallback(window)

def atr_values(df: pd.DataFrame, short_len: int, long_len: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(df) < short_len:
        return None, None, None
    atr_short = atr_series(df, short_len)
    atr_long = atr_series(df, long_len) if len(df) >= long_len else None
    if atr_short is None or atr_short.empty:
        return None, None, None
    curr = atr_short.iloc[-1]
    prev = atr_short.iloc[-2] if len(atr_short) > 1 else curr
    long_val = atr_long.iloc[-1] if atr_long is not None and not atr_long.empty else None
    return (float(curr) if pd.notna(curr) else None, float(prev) if pd.notna(prev) else None, float(long_val) if long_val is not None and pd.notna(long_val) else None)

def detect_bar_patterns(df: pd.DataFrame, timeframe: str) -> Dict[str, Any]:
    """Detect candlestick patterns, multi-bar structure, vol regime, streak on HTF."""
    result: Dict[str, Any] = {}
    if df is None or len(df) < 10:
        return result
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    v = df["volume"].astype(float)
    n = len(df)
    def _b(i): return float(o.iloc[i]), float(h.iloc[i]), float(l.iloc[i]), float(c.iloc[i]), float(v.iloc[i])
    o1, h1, l1, c1, v1 = _b(-1)
    o2, h2, l2, c2, v2 = _b(-2)
    o3, h3, l3, c3, v3 = _b(-3)
    o4, h4, l4, c4, v4 = _b(-4)
    o5, h5, l5, c5, v5 = _b(-5)
    body1, body2, body3, body4 = abs(c1 - o1), abs(c2 - o2), abs(c3 - o3), abs(c4 - o4)
    range1 = max(h1 - l1, 1e-10)
    range2 = max(h2 - l2, 1e-10)
    range3 = max(h3 - l3, 1e-10)
    upper_wick1 = h1 - max(o1, c1)
    lower_wick1 = min(o1, c1) - l1
    body_ratio1 = body1 / range1
    is_bull1, is_bear1 = c1 > o1, c1 < o1
    is_bull2, is_bear2 = c2 > o2, c2 < o2
    is_bull3, is_bear3 = c3 > o3, c3 < o3
    # --- Volume analysis ---
    vol_window = min(20, n - 1)
    vol_avg = float(v.iloc[-vol_window - 1:-1].mean()) if vol_window > 0 else v1
    vol_ratio = v1 / vol_avg if vol_avg > 0 else 1.0
    vol_confirm = vol_ratio >= 1.3
    vol_spike = vol_ratio >= 2.0
    vol_dry = vol_ratio < 0.6
    vol_expanding = all(float(v.iloc[-i]) > float(v.iloc[-i - 1]) for i in range(1, min(4, n)))
    # --- ATR percentile (volatility regime) ---
    atr_arr = (h - l).astype(float)
    atr_curr = float(atr_arr.iloc[-1])
    atr_window = min(50, n)
    atr_hist = atr_arr.iloc[-atr_window:].sort_values()
    atr_rank = float((atr_hist < atr_curr).sum()) / max(len(atr_hist), 1)
    vol_regime = "low" if atr_rank < 0.25 else ("high" if atr_rank > 0.75 else "normal")
    # --- Streak (consecutive directional closes) ---
    streak = 0
    for i in range(1, min(8, n)):
        ci_v = float(c.iloc[-i])
        oi_v = float(o.iloc[-i])
        if ci_v > oi_v:
            if streak >= 0: streak += 1
            else: break
        elif ci_v < oi_v:
            if streak <= 0: streak -= 1
            else: break
        else:
            break
    # --- Swing structure (higher-high/higher-low or lower-low/lower-high) ---
    hh = h1 > h2 and h2 > h3
    hl = l1 > l2 and l2 > l3
    ll = l1 < l2 and l2 < l3
    lh = h1 < h2 and h2 < h3
    swing_bull = hh and hl
    swing_bear = ll and lh
    # --- Range compression (N bars narrowing) ---
    ranges_5 = [max(float(h.iloc[-i]) - float(l.iloc[-i]), 1e-10) for i in range(1, min(6, n))]
    compression = all(ranges_5[i] <= ranges_5[i + 1] for i in range(len(ranges_5) - 1)) if len(ranges_5) >= 3 else False
    compression_ratio = ranges_5[0] / ranges_5[-1] if len(ranges_5) >= 3 and ranges_5[-1] > 0 else 1.0
    # --- Multi-inside (2+ consecutive inside bars) ---
    inside_count = 0
    for i in range(1, min(5, n - 1)):
        if float(h.iloc[-i]) < float(h.iloc[-i - 1]) and float(l.iloc[-i]) > float(l.iloc[-i - 1]):
            inside_count += 1
        else:
            break
    # --- Pattern detection (priority order) ---
    pattern = "none"
    direction = 0
    strength = 0.0
    # 1. Morning Star (3-bar bull reversal: big bear + small body + big bull)
    if is_bear3 and body3 > range3 * 0.5 and body2 < range2 * 0.3 and is_bull1 and body1 > range1 * 0.5 and c1 > (o3 + c3) / 2:
        pattern = "morning_star"
        direction = 1
        strength = min(1.0, (body1 + body3) / (2 * range1 + 1e-10))
    # 2. Evening Star (3-bar bear reversal: big bull + small body + big bear)
    elif is_bull3 and body3 > range3 * 0.5 and body2 < range2 * 0.3 and is_bear1 and body1 > range1 * 0.5 and c1 < (o3 + c3) / 2:
        pattern = "evening_star"
        direction = -1
        strength = min(1.0, (body1 + body3) / (2 * range1 + 1e-10))
    # 3. Three White Soldiers (3 consecutive strong bull candles with higher closes)
    elif is_bull1 and is_bull2 and is_bull3 and c1 > c2 > c3 and body1 > range1 * 0.5 and body2 > range2 * 0.5 and body3 > range3 * 0.5:
        pattern = "three_white_soldiers"
        direction = 1
        strength = min(1.0, min(body1, body2, body3) / max(range1, range2, range3))
    # 4. Three Black Crows (3 consecutive strong bear candles with lower closes)
    elif is_bear1 and is_bear2 and is_bear3 and c1 < c2 < c3 and body1 > range1 * 0.5 and body2 > range2 * 0.5 and body3 > range3 * 0.5:
        pattern = "three_black_crows"
        direction = -1
        strength = min(1.0, min(body1, body2, body3) / max(range1, range2, range3))
    # 5. Bullish Engulfing
    elif is_bull1 and is_bear2 and c1 > o2 and o1 < c2 and body1 > body2:
        pattern = "bull_engulfing"
        direction = 1
        strength = min(1.0, (body1 / (body2 + 1e-10)) * 0.5)
    # 6. Bearish Engulfing
    elif is_bear1 and is_bull2 and c1 < o2 and o1 > c2 and body1 > body2:
        pattern = "bear_engulfing"
        direction = -1
        strength = min(1.0, (body1 / (body2 + 1e-10)) * 0.5)
    # 7. Tweezer Bottom (two bars with nearly identical lows, 2nd is bull)
    elif is_bull1 and abs(l1 - l2) < range1 * 0.05 and l1 < min(l3, l4):
        pattern = "tweezer_bottom"
        direction = 1
        strength = min(1.0, 1.0 - abs(l1 - l2) / range1)
    # 8. Tweezer Top (two bars with nearly identical highs, 2nd is bear)
    elif is_bear1 and abs(h1 - h2) < range1 * 0.05 and h1 > max(h3, h4):
        pattern = "tweezer_top"
        direction = -1
        strength = min(1.0, 1.0 - abs(h1 - h2) / range1)
    # 9. Hammer
    elif body_ratio1 < 0.35 and lower_wick1 > body1 * 2.0 and upper_wick1 < body1 * 0.5:
        pattern = "hammer"
        direction = 1
        strength = min(1.0, lower_wick1 / range1)
    # 10. Shooting Star
    elif body_ratio1 < 0.35 and upper_wick1 > body1 * 2.0 and lower_wick1 < body1 * 0.5:
        pattern = "shooting_star"
        direction = -1
        strength = min(1.0, upper_wick1 / range1)
    # 11. Bullish Harami (small bull inside prior big bear)
    elif is_bull1 and is_bear2 and body1 < body2 * 0.5 and h1 < h2 and l1 > l2:
        pattern = "bull_harami"
        direction = 1
        strength = 0.5 * (1.0 - body1 / (body2 + 1e-10))
    # 12. Bearish Harami (small bear inside prior big bull)
    elif is_bear1 and is_bull2 and body1 < body2 * 0.5 and h1 < h2 and l1 > l2:
        pattern = "bear_harami"
        direction = -1
        strength = 0.5 * (1.0 - body1 / (body2 + 1e-10))
    # 13. Multi-Inside Bar Breakout (2+ inside bars = coiled spring)
    elif inside_count >= 2:
        pattern = "multi_inside"
        direction = 0
        strength = min(1.0, inside_count * 0.3)
    # 14. Inside Bar
    elif h1 < h2 and l1 > l2:
        pattern = "inside_bar"
        direction = 0
        strength = 1.0 - (range1 / range2)
    # 15. Outside Bar
    elif h1 > h2 and l1 < l2 and body_ratio1 > 0.6:
        pattern = "outside_bar"
        direction = 1 if is_bull1 else -1
        strength = body_ratio1
    # 16. Pin Bar Bull
    elif lower_wick1 > range1 * 0.6 and body_ratio1 < 0.25:
        pattern = "pin_bar_bull"
        direction = 1
        strength = lower_wick1 / range1
    # 17. Pin Bar Bear
    elif upper_wick1 > range1 * 0.6 and body_ratio1 < 0.25:
        pattern = "pin_bar_bear"
        direction = -1
        strength = upper_wick1 / range1
    # 18. Three Bar Bull Reversal
    elif is_bull1 and is_bear2 and is_bear3 and c1 > h2:
        pattern = "three_bar_bull"
        direction = 1
        strength = min(1.0, body1 / (body2 + body3 + 1e-10))
    # 19. Three Bar Bear Reversal
    elif is_bear1 and is_bull2 and is_bull3 and c1 < l2:
        pattern = "three_bar_bear"
        direction = -1
        strength = min(1.0, body1 / (body2 + body3 + 1e-10))
    # 20. Doji
    elif body_ratio1 < 0.1:
        pattern = "doji"
        direction = 0
        strength = 0.3 + (0.4 if vol_confirm else 0.0)
    # --- Volume + direction amplifier ---
    if vol_confirm and direction != 0:
        strength = min(1.0, strength * 1.3)
    if vol_spike and direction != 0:
        strength = min(1.0, strength * 1.2)
    if vol_dry and direction != 0:
        strength *= 0.6
    # --- Core outputs ---
    result[f"bar_pattern_{timeframe}"] = pattern
    result[f"bar_direction_{timeframe}"] = direction
    result[f"bar_strength_{timeframe}"] = round(strength, 3)
    result[f"bar_vol_confirm_{timeframe}"] = vol_confirm
    result[f"bar_vol_ratio_{timeframe}"] = round(vol_ratio, 2)
    result[f"bar_body_ratio_{timeframe}"] = round(body_ratio1, 3)
    result[f"bar_upper_wick_{timeframe}"] = round(upper_wick1 / range1, 3) if range1 > 0 else 0.0
    result[f"bar_lower_wick_{timeframe}"] = round(lower_wick1 / range1, 3) if range1 > 0 else 0.0
    # --- Structure outputs ---
    result[f"bar_streak_{timeframe}"] = streak
    result[f"bar_swing_bull_{timeframe}"] = swing_bull
    result[f"bar_swing_bear_{timeframe}"] = swing_bear
    result[f"bar_compression_{timeframe}"] = compression
    result[f"bar_compression_ratio_{timeframe}"] = round(compression_ratio, 3)
    result[f"bar_inside_count_{timeframe}"] = inside_count
    result[f"bar_vol_spike_{timeframe}"] = vol_spike
    result[f"bar_vol_expanding_{timeframe}"] = vol_expanding
    result[f"bar_vol_regime_{timeframe}"] = vol_regime
    result[f"bar_atr_rank_{timeframe}"] = round(atr_rank, 3)
    return result

def mfi_value(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 15:
        return None
    data = df.loc[:, ["high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").astype(np.float64)#, copy=True)
    data = data.dropna()
    if len(data) < 15:
        return None
    typical_price = (data["high"] + data["low"] + data["close"]) / 3.0
    money_flow = typical_price * data["volume"]
    price_delta = typical_price.diff()
    positive_flow = money_flow.where(price_delta > 0.0, 0.0)
    negative_flow = money_flow.where(price_delta < 0.0, 0.0)
    positive_sum = positive_flow.rolling(14, min_periods=14).sum()
    negative_sum = negative_flow.rolling(14, min_periods=14).sum()
    if positive_sum.empty or negative_sum.empty:
        return None
    pos = positive_sum.iloc[-1]
    neg = negative_sum.iloc[-1]
    if pd.isna(pos) or pd.isna(neg):
        return None
    if pos == 0.0 and neg == 0.0:
        return 50.0
    if neg == 0.0:
        return 100.0
    ratio = pos / neg
    mfi = 100.0 - (100.0 / (1.0 + ratio))
    return float(mfi)

def ema_pair(series: pd.Series, length: int) -> Tuple[Optional[float], Optional[float]]:
    if len(series) < length:
        return None, None
    ema_series = series.ewm(span=length, adjust=False).mean()
    curr = ema_series.iloc[-1]
    prev = ema_series.iloc[-2] if len(ema_series) > 1 else curr
    return (float(curr) if pd.notna(curr) else None, float(prev) if pd.notna(prev) else None)

def ema_std(series: pd.Series, ema_length: int, std_window: int = 20) -> Optional[float]:
    if len(series) < max(ema_length, std_window):
        return None
    ema_series = series.ewm(span=ema_length, adjust=False).mean()
    deviation = (series - ema_series).abs()
    std_series = deviation.rolling(window=std_window, min_periods=std_window).std()
    if std_series.empty or pd.isna(std_series.iloc[-1]):
        return None
    return float(std_series.iloc[-1])

def sma_pair(series: pd.Series, length: int) -> Tuple[Optional[float], Optional[float]]:
    if len(series) < length:
        return None, None
    sma_series = series.rolling(length, min_periods=length).mean()
    if sma_series.empty:
        return None, None
    curr = sma_series.iloc[-1]
    prev = sma_series.iloc[-2] if len(sma_series) > 1 else curr
    return (float(curr) if pd.notna(curr) else None, float(prev) if pd.notna(prev) else None)

def relative_volume(df: pd.DataFrame, length: int) -> Optional[float]:
    if len(df) < length + 1:
        return None
    # Use the second-to-last bar (last completed bar) for stable relative volume calculation
    # denom is the average of the 'length' bars before the last one
    volumes = df["volume"].astype(float)
    numerator = volumes.iloc[-2]
    denominator = volumes.iloc[-(length+1):-1].mean()
    if pd.isna(denominator) or denominator == 0:
        return None
    return float(numerator / denominator)

def rsi_value(series: pd.Series, length: int) -> Optional[float]:
    if len(series) < length:
        return None
    rsi_series_obj = rsi_series(series, length)
    if rsi_series_obj is None or rsi_series_obj.empty:
        return None
    value = rsi_series_obj.iloc[-1]
    return float(value) if pd.notna(value) else None

def crossover_flags(price: float, price_prev: float, ref: Optional[float], ref_prev: Optional[float]) -> Tuple[bool, bool]:
    if ref is None or ref_prev is None:
        return False, False
    cross_over = price_prev <= ref_prev and price > ref
    cross_under = price_prev >= ref_prev and price < ref
    return cross_over, cross_under

# ---------------------------------------------------------
# UPDATE: ez_indicators.py -> IndicatorCalculator.compute
# ---------------------------------------------------------
# In ez_indicators.py

class IndicatorCalculator:
    def compute(self, df: pd.DataFrame, timeframe: str, mark_price: Optional[float], mid_run: bool, mark_price_ts: Optional[datetime] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if df is None or df.empty:
            return result
        if "timestamp_dt" not in df.columns:
            return result
        last_row = df.iloc[-1]
        data_ts = last_row["timestamp_dt"]
        if pd.isna(data_ts): return result
        data_ts = data_ts.to_pydatetime().astimezone(timezone.utc)
        final_ts = data_ts 
        if mid_run and mark_price is not None:
            if mark_price_ts:
                final_ts = mark_price_ts
            else:
                final_ts = datetime.now(timezone.utc)
        result[f"timestamp_{timeframe}"] = isoformat(final_ts)
        result["timestamp"] = result[f"timestamp_{timeframe}"]
        
        # USE ACTUAL DATA TIMESTAMP FOR TICK TS
        result['_tick_ts'] = final_ts.timestamp()
        result['ts'] = result['_tick_ts']
        adjusted_df = with_mark_price(df, mark_price)
        close_series = adjusted_df["close"].astype(float)
        high_series = adjusted_df["high"].astype(float)
        low_series = adjusted_df["low"].astype(float)
        open_series = adjusted_df["open"].astype(float)
        volume_series = adjusted_df["volume"].astype(float)
        current_price = float(close_series.iloc[-1])
        prev_price = float(close_series.iloc[-2]) if len(close_series) > 1 else current_price
        result["current_price"] = current_price
        result["prev_price"] = prev_price
        result[f"high_{timeframe}"] = float(high_series.iloc[-1])
        result[f"low_{timeframe}"] = float(low_series.iloc[-1])
        result[f"open_{timeframe}"] = float(open_series.iloc[-1])
        result[f"close_{timeframe}"] = float(close_series.iloc[-1])
        result[f"high_{timeframe}_prev"] = float(high_series.iloc[-2]) if len(high_series) > 1 else float(high_series.iloc[-1])
        result[f"low_{timeframe}_prev"] = float(low_series.iloc[-2]) if len(low_series) > 1 else float(low_series.iloc[-1])
        dc_window = TIMEFRAMES[timeframe]["dc_window"]
        dc_high, dc_low, dc_basis = donchian(high_series, low_series, dc_window)
        dc_high_prev, dc_low_prev, dc_basis_prev = donchian_prev(high_series, low_series, dc_window)
        if dc_high is not None:
            result[f"dc_high_{timeframe}"] = dc_high
            result[f"dc_low_{timeframe}"] = dc_low
            result[f"dc_basis_{timeframe}"] = dc_basis
            if dc_low > 0 and dc_high > dc_low: result[f"dc_width_{timeframe}"] = round(((dc_high - dc_low) / dc_low) * 100, 4); result[f"dc_position_{timeframe}"] = round(max(0.0, min(1.0, (current_price - dc_low) / (dc_high - dc_low))), 4)
        if dc_high_prev is not None:
            result[f"dc_high_{timeframe}_prev"] = dc_high_prev
            result[f"dc_low_{timeframe}_prev"] = dc_low_prev
            result[f"dc_basis_{timeframe}_prev"] = dc_basis_prev
        if len(high_series) >= dc_window + 30:
            high_hist = high_series.rolling(dc_window, min_periods=dc_window).max()
            low_hist = low_series.rolling(dc_window, min_periods=dc_window).min()
            idx = -22
            # Stricter check: absolute index must be within bounds
            if len(high_hist) >= abs(idx) and len(low_hist) >= abs(idx):
                result[f"dc_high_{timeframe}_ant"] = float(high_hist.iloc[idx])
                result[f"dc_low_{timeframe}_ant"] = float(low_hist.iloc[idx])
                result[f"dc_basis_{timeframe}_ant"] = (result[f"dc_high_{timeframe}_ant"] + result[f"dc_low_{timeframe}_ant"]) / 2.0
        dch4, dcl4, _ = donchian(high_series, low_series, 4)
        if dch4 is not None:
            result[f"dc_high4_{timeframe}"] = dch4
            result[f"dc_low4_{timeframe}"] = dcl4
        if dc_basis is not None and dc_basis_prev is not None:
            cross_up, cross_down = crossover_flags(current_price, prev_price, dc_basis, dc_basis_prev)
            result[f"dc_basis_crossover_{timeframe}"] = cross_up
            result[f"dc_basis_crossunder_{timeframe}"] = cross_down
        if dc_high is not None and dc_high_prev is not None:
            cross_up, cross_down = crossover_flags(current_price, prev_price, dc_high, dc_high_prev)
            result[f"dc_high_crossover_{timeframe}"] = cross_up
            result[f"dc_high_crossunder_{timeframe}"] = cross_down
        if dc_low is not None and dc_low_prev is not None:
            cross_up, cross_down = crossover_flags(current_price, prev_price, dc_low, dc_low_prev)
            result[f"dc_low_crossover_{timeframe}"] = cross_up
            result[f"dc_low_crossunder_{timeframe}"] = cross_down
        k_curr, d_curr, k_prev, d_prev, stoch_cross_over, stoch_cross_under = stoch_result(close_series)
        is_valid = k_curr is not None
        result[f"stoch_crossover_{timeframe}"] = bool(stoch_cross_over) if is_valid else False
        result[f"stoch_crossunder_{timeframe}"] = bool(stoch_cross_under) if is_valid else False
        if k_curr is not None:
            result[f"stoch_k_{timeframe}"] = k_curr
            result[f"stoch_d_{timeframe}"] = d_curr if d_curr is not None else k_curr
            result[f"k_{timeframe}_prev"] = k_prev if k_prev is not None else k_curr
            result[f"d_{timeframe}_prev"] = d_prev if d_prev is not None else d_curr
        wt1, wt2 = wavetrend(adjusted_df, timeframe=timeframe)
        if wt1 is not None and wt2 is not None and not wt1.empty and not wt2.empty:
            wt_intel = wavetrend_intelligence(wt1, wt2, close_series, high_series, low_series, timeframe)
            result.update(wt_intel)
        else:
            result[f"wt_signal_{timeframe}"] = "NEUTRAL"
        ha_color, ha_prev = heikin_ashi(adjusted_df)
        result[f"ha_{timeframe}"] = ha_color
        if ha_prev is not None:
            result[f"ha_{timeframe}_prev"] = ha_prev
        atr_curr, atr_prev, atr_long = atr_values(adjusted_df, TIMEFRAMES[timeframe]["atr"], ATR_LONG_LENGTH)
        if atr_curr is not None:
            result[f"atr_{timeframe}"] = atr_curr
        if atr_prev is not None:
            result[f"atr_{timeframe}_prev"] = atr_prev
        # atr_long REMOVED — 0 references in consumers
        pass
        rel_vol = relative_volume(adjusted_df, REL_VOL_LENGTH)
        if rel_vol is not None:
            result[f"relative_volume_{timeframe}"] = rel_vol
        mfi_val = mfi_value(adjusted_df)
        if mfi_val is not None:
            result[f"mfi_{timeframe}"] = mfi_val
        rsi_val = rsi_value(close_series, 14)
        if rsi_val is not None:
            result[f"rsi_{timeframe}"] = rsi_val
        # New indicators restricted to 1h only (where they're consumed) to save CPU
        if timeframe in ("1h", "4h"):
            rsi2_val = rsi_value(close_series, 2)
            if rsi2_val is not None:
                result[f"rsi_2_{timeframe}"] = rsi2_val
            adx_val = adx_value(adjusted_df, 14)
            if adx_val is not None:
                result[f"adx_{timeframe}"] = adx_val
            _macd, _macd_sig, _macd_hist, _macd_co, _macd_cu = macd_values(close_series)
            if _macd is not None:
                result[f"macd_{timeframe}"] = _macd
                result[f"macd_signal_{timeframe}"] = _macd_sig
                result[f"macd_hist_{timeframe}"] = _macd_hist
                result[f"macd_crossover_{timeframe}"] = _macd_co
                result[f"macd_crossunder_{timeframe}"] = _macd_cu
        if timeframe in ("1h", "4h", "15m"):
            _ha_streak = ha_streak_count(adjusted_df)
            result[f"ha_streak_{timeframe}"] = _ha_streak
        # chop REMOVED — computed but 0 consumers. Re-enable when REGIME_ADAPTIVE_ENABLED is wired.
        for ema_length in TIMEFRAMES[timeframe]["ema"]:
            ema_curr, ema_prev = ema_pair(close_series, ema_length)
            if ema_curr is not None:
                result[f"ema_{ema_length}_{timeframe}"] = ema_curr
            if ema_prev is not None:
                result[f"ema_{ema_length}_{timeframe}_prev"] = ema_prev
            if timeframe == "3m" and ema_length == 20:
                ema_std_val = ema_std(close_series, ema_length, std_window=20)
                if ema_std_val is not None:
                    result[f"ema_{ema_length}_std_{timeframe}"] = ema_std_val
        for field_name, length in TIMEFRAMES[timeframe]["sma"]:
            sma_curr, sma_prev = sma_pair(close_series, length)
            if sma_curr is not None:
                result[field_name] = sma_curr
            if sma_prev is not None:
                result[f"{field_name}_prev"] = sma_prev
            if sma_curr is not None and sma_prev is not None:
                cross_over, cross_under = crossover_flags(current_price, prev_price, sma_curr, sma_prev)
                suffix = field_name.split("_")[-1]
                result[f"sma_crossover_{suffix}"] = cross_over
                result[f"sma_crossunder_{suffix}"] = cross_under
        slope, linearity = linreg_features(close_series, LINREG_LENGTH)
        if slope is not None:
            result[f"lr_trend_{timeframe}"] = slope
        if timeframe == "4h" and linearity is not None:
            result["linearity_4h"] = linearity
            result["slope_close_4h"] = slope
        if True:  # BB on ALL timeframes — auto-tuned σ per symbol (natural S/R)
            # Auto-tune: find the σ where bands touch price highs+lows the most
            if len(adjusted_df) >= 120:
                _at_mult, _at_u, _at_l, _at_pb, _at_touches = bb_auto_tune(adjusted_df["high"], adjusted_df["low"], close_series, length=20, lookback=100, touch_pct=0.002)
            else:
                _at_mult, _at_u, _at_l, _at_pb, _at_touches = 2.0, 0, 0, 0.5, 0
                _fb = bb_features(close_series, length=20, std_mult=2.0)
                if _fb[2] is not None:
                    _at_u, _at_l, _at_pb = _fb
            if _at_u and _at_l:
                _bb_mid = (_at_u + _at_l) / 2.0
                result[f"bb_upper_{timeframe}"] = _at_u
                result[f"bb_lower_{timeframe}"] = _at_l
                result[f"bb_mid_{timeframe}"] = _bb_mid
                result[f"bb_pct_b_{timeframe}"] = _at_pb
                result[f"bb_width_{timeframe}"] = round((_at_u - _at_l) / _bb_mid * 100.0, 3) if _bb_mid > 0 else 0.0
                result[f"bb_mult_{timeframe}"] = round(_at_mult, 2)
                result[f"bb_touches_{timeframe}"] = _at_touches
            _lr_u, _lr_l, _lr_pb = linreg_channel(close_series, LINREG_LENGTH, std_mult=2.5)
            if _lr_pb is not None:
                result[f"lr_pct_b_{timeframe}"] = _lr_pb
                # lr_upper/lr_lower REMOVED — 0 references in consumers
            # bar_pat RESTORED — BACKTEST_CHANGE_150 compression breakout needs bar_atr_rank_{tf}
            try:
                bar_pat = detect_bar_patterns(adjusted_df, timeframe)
                if bar_pat:
                    result.update(bar_pat)
            except Exception:
                pass
            # Candle body comparison (current vs prev) — needed by Strategy 3 (EMA200+StochRSI)
            _cb_body = abs(float(close_series.iloc[-1]) - float(open_series.iloc[-1]))
            _cb_body_prev = abs(float(close_series.iloc[-2]) - float(open_series.iloc[-2])) if len(close_series) > 1 else _cb_body
            result[f"candle_body_{timeframe}"] = round(_cb_body, 8)
            result[f"candle_body_prev_{timeframe}"] = round(_cb_body_prev, 8)
            result[f"candle_body_ratio_{timeframe}"] = round(_cb_body / _cb_body_prev, 3) if _cb_body_prev > 0 else 1.0
            pass
        if timeframe == "3m":
            # Calculate THMA-based trend indicators with floor division
            t_up, tco, tcu = hull_trend_indicators(close_series, length_short=9, length_long=21)
            
            if t_up is not None:
                result["t_up_3m"] = t_up
            if tco is not None:
                result["tco_3m"] = tco
            if tcu is not None:
                result["tcu_3m"] = tcu
        if timeframe == "15m":
            # I-1 fix (2026-04-18): duplicate Hull 15m writer DELETED.
            # The block at L~2162 below overwrites t_up_15m/tco_15m/tcu_15m using EMA9/EMA21 — that is the
            # authoritative source. The THMA hull_trend_indicators write here was redundant and caused
            # a two-writer drift (different algorithm, same key). Keeping the sma_1m stub only.
            # Hull 1h REMOVED — only 3m/15m used in rankings. Saves ~20-50ms/symbol.
            # Duplicate 3m Hull computation REMOVED — already computed above.
            sma_1m_curr, sma_1m_prev = sma_pair(close_series, 70)
            if sma_1m_curr is not None:
                result["sma_200_1m"] = sma_1m_curr
            if sma_1m_prev is not None:
                result["sma_200_1m_prev"] = sma_1m_prev
            if sma_1m_curr is not None and sma_1m_prev is not None:
                prev_sma = sma_1m_prev
                cross_over, cross_under = crossover_flags(current_price, prev_price, sma_1m_curr, prev_sma)
                result["sma_crossover_1m"] = cross_over
                result["sma_crossunder_1m"] = cross_under
        if timeframe == "15m":
            ema9_15, ema9_15_prev = ema_pair(close_series, 9)
            ema21_15, ema21_15_prev = ema_pair(close_series, 21)
            if ema9_15 is not None and ema21_15 is not None:
                curr_up = ema9_15 > ema21_15
                prev_up = None
                if ema9_15_prev is not None and ema21_15_prev is not None:
                    prev_up = ema9_15_prev > ema21_15_prev
                result["t_up_15m"] = curr_up
                if prev_up is not None:
                    result["tco_15m"] = (not prev_up) and curr_up
                    result["tcu_15m"] = prev_up and (not curr_up)
        if timeframe == "3m":
            result["timestamp_1m"] = result.get(f"timestamp_{timeframe}", result.get("timestamp", ""))
            result["1m_updated_at"] = isoformat(utc_now())
            result["_tick_ts"] = final_ts.timestamp()
        if timeframe == "D":
            result["timestamp_D"] = result.get(f"timestamp_{timeframe}", result.get("timestamp", ""))
        if mid_run:
            result[f"timestamp_{timeframe}_mid"] = isoformat(utc_now())
        return result

class IndicatorOrchestrator:
    def __init__(self):
        self.env = env
        self.symbols = load_symbols()
        self.symbol_set: Set[str] = set(self.symbols)
        self._missing_history: Dict[str, int] = {}
        self.reactive_mode = bool(getattr(config, "REACTIVE_MODE", False))
        redis_url = os.environ.get("REDIS_URL")
        redis_password = os.environ.get("REDIS_PASSWORD") or getattr(config, "REDIS_PASSWORD", None)
        redis_host_env = os.environ.get("REDIS_HOST")
        redis_port_env = os.environ.get("REDIS_PORT")
        redis_local = ()
        if isinstance(env_info, dict):
            redis_local = env_info.get("redis_connections", {}).get("local", ())
        redis_host = redis_host_env or (redis_local[0] if isinstance(redis_local, (list, tuple)) and len(redis_local) >= 2 else config.REDIS_HOST)
        redis_port_raw = redis_port_env or (redis_local[1] if isinstance(redis_local, (list, tuple)) and len(redis_local) >= 2 else config.REDIS_PORT)
        try:
            redis_port = int(redis_port_raw)
        except (TypeError, ValueError):
            redis_port = config.REDIS_PORT
        if redis_host in ("localhost", "::1"):
            redis_host = "127.0.0.1"
        redis_kwargs = {"decode_responses": True, "socket_connect_timeout": 5, "socket_timeout": 5, "retry_on_timeout": True, "health_check_interval": 30}
        self.redis_client = redis.from_url(redis_url, **redis_kwargs) if redis_url else redis.Redis(host=redis_host, port=redis_port, db=config.REDIS_DB, password=redis_password, **redis_kwargs)
        self.shared_proxy = None
        self._connect_shared_memory()
        self.base_path = Path(config.BASE_PATH)
        self.kline_manager = KlineManager(self.base_path, env)
        self.price_cache = PriceCacheManager(config.PRICE_CACHE_FILE_2, redis_client=self.redis_client, symbols=self.symbols)
        self.calculator = IndicatorCalculator()
        self.final_scores = load_scores_file(Path(config.FINAL_SCORE_FILE))
        self.ranking_scores = load_scores_file(Path(config.RANKING_POINTS_FILE))
        self.score_ranges = load_score_ranges(Path(config.SCORE_RANGES_FILE))
        self.state: Dict[str, Dict[str, SymbolTimeframeState]] = {}
        self.data: Dict[str, Dict[str, Any]] = {}
        self.pending_mid: Dict[str, List[Tuple[str, datetime]]] = {}
        self.pending_trivial: Dict[str, bool] = {}
        self._redis_broadcast_failures = 0
        self._last_redis_success = time.time()
        self._redis_health_check_interval = 10.0
        self._last_redis_health_check = 0.0
        ensure_directory(Path(config.DATA_DIR))
        instance_id = getattr(config, "WORKER_INSTANCE_ID", 0)
        total_instances = getattr(config, "WORKER_TOTAL_INSTANCES", 1)
        if env == "macbook" and not getattr(config, "ENABLE_MULTI_INSTANCE_ON_MACBOOK", False):
            total_instances = 1
            logger.info(f"[MACBOOK] Forcing single instance mode (no merger) - total_instances=1")
        self.instance_id = instance_id
        self.total_instances = total_instances
        if total_instances > 1:
            self.market_data_file = Path(config.LATEST_MARKET_DATA_FILE).parent / f"indicators_worker{instance_id}.json"
        else:
            self.market_data_file = Path(config.LATEST_MARKET_DATA_FILE)
        self.ranking_points_file = Path(config.RANKING_POINTS_FILE)
        self.score_ranges_file = Path(config.SCORE_RANGES_FILE)
        self._save_lock = asyncio.Lock()
        self._last_dirty = utc_now()
        self.webhook_url = os.getenv("MARKET_DATA_WEBHOOK_URL")
        self.webhook_secret = os.getenv("MARKET_DATA_WEBHOOK_SECRET")
        self.zero_allowed_keywords = ("stoch", "rsi", "mfi", "0", "zconviction")
        self._timestamp_format = "%Y%m%d_%H%M%S"
        self.required_by_timeframe = self._build_required_mapping()
        self._signal_channel = REDIS_CHANNELS.get("signals_data", "signals_data")
        self._market_channel = REDIS_CHANNELS.get("latest_market_data", "latest_market_data")
        self._event_cooldown: Dict[Tuple[str, str], datetime] = {}
        self._previous_indicators: Dict[str, Dict[str, Any]] = {}
        self.priority_symbols = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC", "XRPUSDC", "DOGEUSDC", "AAVEUSDC", "AVAXUSDC", "XLMUSDT", "LINKUSDC", "DOTUSDT", "ADAUSDC", "SKYUSDT"]
        self.required_indicators = list(getattr(config, "REQUIRED_INDICATORS", []))
        self.required_indicator_set = set(self.required_indicators)
        self._schedule_order = ["D", "4h", "1h", "15m", "3m"]
        self._shutdown = asyncio.Event()
        self._dirty = False
        self._save_due = False
        self._last_payload_hash: Optional[str] = None
        self._sentiment_dirty = True
        self.sentiment_top_file = self.base_path / "sentiment_top_40.json"
        self.sentiment_bottom_file = self.base_path / "sentiment_bottom_40.json"
        self._last_cycle_time = utc_now()
        self._last_symbol_refresh = utc_now()
        self._scores_dirty = False
        self._score_ranges_dirty = False
        self._skip_threshold = 20
        self._consecutive_skips = 0
        self.instance_id = instance_id
        self.total_instances = total_instances
        self._load_existing_data()
        self._init_state()


    def _connect_shared_memory(self):
        if not hasattr(self, '_shared_mem_last_attempt'):
            self._shared_mem_last_attempt = 0.0
        now = time.time()
        if now - self._shared_mem_last_attempt < 10.0:
            return
        self._shared_mem_last_attempt = now
        try:
            if get_shared_memory_client:
                # Use 127.0.0.1 for local connection
                client = get_shared_memory_client(address=('127.0.0.1', 50005))
                if client:
                    self.shared_proxy = client.get_store()  # pylint: disable=no-member
                    # Test connection
                    self.shared_proxy.health_check()
                    logger.info("✅ [ORCHESTRATOR] Connected to Shared Indicator Memory")
                else:
                    logger.warning("⚠️ [ORCHESTRATOR] Could not connect to Shared Memory (Client None)")
                    self.shared_proxy = None
        except Exception as e:
            logger.error(f"Shared Memory connect fail: {e}")
            self.shared_proxy = None

    def _push_to_shared_memory(self, symbol: str, data: Dict[str, Any]):
        # Lazy Reconnect
        if self.shared_proxy is None:
            self._connect_shared_memory()
        
        if self.shared_proxy:
            try:
                # Add hot metadata for the client to verify freshness
                now_ts = time.time()
                
                # We need to copy the data to avoid modifying the internal state dictionary references
                # improperly, although for a push this is usually fine.
                payload = data.copy()
                # payload['_hot_ts'] = now_ts
                # payload['_tick_ts'] = now_ts
                # payload['_hot_source'] = 'ez_indicators'
                

                self.shared_proxy.update_symbol(symbol, payload)
                
            except (EOFError, BrokenPipeError, ConnectionRefusedError):
                logger.warning(f"⚠️ [SharedMem] Connection lost during push for {symbol}")
                self.shared_proxy = None # Trigger reconnect next time
            except Exception as e:
                logger.error(f"⚠️ [SharedMem] Push error: {e}")
                self.shared_proxy = None

    # def _connect_shared_memory(self):
    #     try:
    #         if get_shared_memory_client:
    #             client = get_shared_memory_client()
    #             if client:
    #                 self.shared_proxy = client.get_store()  # pylint: disable=no-member
    #                 logger.info("✅ Connected to Shared Indicator Memory")
    #     except Exception as e:
    #         logger.error(f"Shared Memory connect fail: {e}")


    # def _push_to_shared_memory(self, symbol: str, data: Dict[str, Any]):
    #     if self.shared_proxy is None:
    #         self._connect_shared_memory()
    #     if self.shared_proxy:
    #         try:
    #             now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    #             data['timestamp'] = now_iso
    #             data['_hot_ts'] = time.time()
    #             data['_hot_source'] = 'BACKEND_IND'
    #             self.shared_proxy.update_symbol(symbol, data)
    #         except Exception:
    #             self.shared_proxy = None # Trigger reconnect on next attempt

    def _build_required_mapping(self) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {tf: [] for tf in TIMEFRAMES.keys()}
        required = getattr(config, "REQUIRED_INDICATORS", [])
        for key in required:
            for tf in mapping.keys():
                if key.endswith(f"_{tf}"):
                    mapping[tf].append(key)
        mapping["3m"].extend(["sma_200_1m", "sma_200_1m_prev", "sma_crossover_1m", "sma_crossunder_1m", "tco_3m", "tcu_3m", "t_up_3m"])
        mapping["15m"].extend(["t_up_15m", "tco_15m", "tcu_15m"])
        for tf in mapping:
            mapping[tf] = sorted(list({item for item in mapping[tf]}))
        return mapping

    def _load_existing_data(self) -> None:
        existing = None
        # 1. Try Redis latest_market_data first
        try:
            import redis as sync_redis
            r = sync_redis.Redis(host='127.0.0.1', port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True, socket_timeout=3)
            raw = r.get(config.REDIS_KEY_MARKET_DATA)
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and len(data) > 100:
                    existing = data
                    logger.info(f"[LOAD] Loaded {len(data)} symbols from Redis latest_market_data")
            r.close()
        except Exception as e:
            logger.debug(f"[LOAD] Redis load failed: {e}")
        # 2. Fall back to most recent market_data_YYYYMMDD_HHMMSS.json
        if not existing:
            data_dir = self.market_data_file.parent
            ts_files = sorted(data_dir.glob("market_data_2*.json"), reverse=True)
            for ts_file in ts_files[:3]:
                try:
                    with open(ts_file, "r") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and len(data) > 100:
                        existing = data
                        logger.info(f"[LOAD] Loaded {len(data)} symbols from {ts_file.name}")
                        break
                except Exception:
                    continue
        if existing:
            self.data = existing
            for symbol, symbol_values in self.data.items():
                if isinstance(symbol_values, dict):
                    self._flag_errors(symbol_values)
                    self._previous_indicators[symbol] = self._snapshot_signals(symbol_values)
        else:
            self.data = {}
            logger.warning("[LOAD] No existing data found — starting fresh")

    def _init_state(self) -> None:
        for symbol in self.symbols:
            self.data.setdefault(symbol, {})
            symbol_data = self.data.get(symbol, {}) if isinstance(self.data, dict) else {}
            self.state[symbol] = {}
            for tf in TIMEFRAMES.keys():
                state = SymbolTimeframeState(timeframe=tf)
                ts_str = symbol_data.get(f"timestamp_{tf}") if isinstance(symbol_data, dict) else None
                if isinstance(ts_str, str):
                    try:
                        parsed = ts_str.replace("Z", "+00:00") if ts_str.endswith("Z") else ts_str
                        state.last_close = datetime.fromisoformat(parsed)
                    except Exception:
                        state.last_close = None
                self.state[symbol][tf] = state
            self.pending_mid[symbol] = []
            self.pending_trivial[symbol] = False

    async def run(self) -> None:
        logger.info(f"Starting orchestrator for {len(self.symbols)} symbols in {self.env}")
        await self.price_cache.start()
        for tf in self._schedule_order:
            await self._process_timeframe(tf, force=self.reactive_mode)
        self._sentiment_dirty = True
        self._save_due = True
        tasks = [asyncio.create_task(self._schedule_loop()), asyncio.create_task(self._midcycle_loop()), asyncio.create_task(self._save_loop()), asyncio.create_task(self._3m_refresh_loop()),asyncio.create_task(self._priority_fix_loop())]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._shutdown.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.price_cache.stop()
            if self.redis_client:
                await self.redis_client.aclose()

    def _is_15m_priority_time(self) -> bool:
        now = utc_now()
        minute = now.minute
        return minute in (0, 15, 30, 45)

    async def _ensure_15m_complete(self) -> bool:
        all_done = True
        for symbol in self.symbols:
            state = self.state[symbol]["15m"]
            required_keys = self.required_by_timeframe.get("15m", [])
            symbol_data = self.data.get(symbol, {})
            if not required_keys:
                continue
            missing = [key for key in required_keys if symbol_data.get(key) is None]
            if missing or not state.full_done:
                df, close_ts, _ = await self.kline_manager.get_latest(symbol, "15m")
                if df is None or close_ts is None:
                    df = await self.kline_manager._calculate_15m_from_3m(symbol)
                    if df is not None and not df.empty and "timestamp_dt" in df.columns:
                        ts_series = df["timestamp_dt"].dropna()
                        if not ts_series.empty:
                            close_ts = ts_series.iloc[-1].to_pydatetime().astimezone(timezone.utc)
                        else:
                            all_done = False
                            continue
                    else:
                        all_done = False
                        continue
                mark_price = await self.price_cache.get_price(symbol)
                if close_ts is not None:
                    await self._run_full(symbol, "15m", df, close_ts, mark_price, state)
                    state.last_close = close_ts
                    state.full_done = True
                    state.mid_done = False
                    state.trivial_done = False
                    state.mid_due = close_ts + timedelta(seconds=TIMEFRAMES["15m"]["half"])
                    state.last_full_finish = utc_now()
                    state.last_df = df.copy()
                    await self._try_trivial(symbol)
        return all_done

    async def _priority_fix_loop(self) -> None:
        """Listens for high-priority fix requests from the Merger"""
        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe("priority_3m_fix")
        logger.info("🚑 Priority Fix Listener active on channel: priority_3m_fix")
        
        while not self._shutdown.is_set():
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    logger.info("🚨 RECEIVED PRIORITY INTERRUPT SIGNAL")
                    await self._process_priority_symbols()
                await asyncio.sleep(0.2)
            except Exception as e:
                logger.error(f"Error in priority listener: {e}")
                await asyncio.sleep(5)

    async def _process_priority_symbols(self) -> None:
        """Immediately processes missing symbols and forces a save"""
        try:
            missing_json = await self.redis_client.get("missing_3m_symbols")
            if not missing_json:
                return
            
            all_missing = json.loads(missing_json)
            my_missing = [s for s in all_missing if s in self.symbol_set]
            
            if not my_missing:
                return
                
            logger.info(f"🚑 EMERGENCY FIX: Processing {len(my_missing)} symbols immediately: {my_missing}")
            for symbol in my_missing:
                try:
                    df, close_ts, _ = await self.kline_manager.get_latest(symbol, "3m")
                    if df is not None:
                        state = self.state[symbol]["3m"]
                        mark_price = await self.price_cache.get_price(symbol)
                        # FORCE calculation even if timestamps look valid (because fields might be missing)
                        sys.stdout.flush()
                        await self._run_full(symbol, "3m", df, close_ts, mark_price, state)
                        sys.stdout.flush()
                    else:
                        logger.warning(f"🚑 EMERGENCY FIX: No kline data for {symbol} — removing from missing list (unfixable)")
                except BaseException as e:
                    import traceback
                    traceback.print_exc()
                    sys.stdout.flush()
                    sys.stderr.flush()
                    logger.error(f"Failed to fix {symbol}: {e}")
            # Remove handled symbols from missing list so merger stops re-triggering
            try:
                remaining = [s for s in all_missing if s not in my_missing]
                if remaining:
                    await self.redis_client.set("missing_3m_symbols", json.dumps(remaining), ex=300)
                else:
                    await self.redis_client.delete("missing_3m_symbols")
            except Exception as _ce:
                logger.error(f"Failed to clear missing symbols: {_ce}")
            # Force save immediately bypassing the lock timer
            logger.info("🚑 EMERGENCY SAVE Triggered")
            await self._save_data()            
        except Exception as e:
            logger.error(f"Error processing priority symbols: {e}")

    async def _3m_refresh_loop(self) -> None:
        interval = 36.0
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
            if self._is_15m_priority_time():
                await self._ensure_15m_complete()
            await self._process_timeframe_3m_with_mark_price()

    async def _process_timeframe_3m_with_mark_price(self) -> None:
        if self._is_15m_priority_time():
            await self._ensure_15m_complete()
        timeframe = "3m"
        full_count = 0
        reactive_count = 0
        missing_symbols = await self._get_missing_symbols()
        symbols_to_process = list(self.symbols)
        if missing_symbols:
            symbols_to_process = [s for s in missing_symbols if s in self.symbol_set] + [s for s in symbols_to_process if s not in missing_symbols]
            logger.debug(f"🎯 Prioritizing {len(missing_symbols)} missing symbols for 3m processing")
        async def process_symbol(symbol: str) -> None:
            nonlocal full_count, reactive_count
            df, close_ts, source = await self.kline_manager.get_latest(symbol, timeframe)
            symbol_data = self.data.setdefault(symbol, {})
            state = self.state[symbol][timeframe]
            reuse_previous = False
            if df is None or close_ts is None:
                if state.last_df is not None and state.last_close is not None:
                    df = state.last_df.copy()
                    close_ts = state.last_close
                    reuse_previous = True
                else:
                    return
            now = utc_now()
            required_keys = self.required_by_timeframe.get(timeframe, [])
            needs_refresh = any(symbol_data.get(key) is None for key in required_keys)
            mark_price = await self.price_cache.get_price(symbol)
            new_bar = state.last_close is None or (close_ts is not None and close_ts > state.last_close)
            if new_bar or needs_refresh or required_keys:
                await self._run_full(symbol, timeframe, df, close_ts, mark_price, state)
                state.last_close = close_ts
                state.full_done = True
                state.mid_done = False
                state.trivial_done = False
                state.mid_due = close_ts + timedelta(seconds=TIMEFRAMES[timeframe]["half"])
                state.last_full_finish = now
                self.pending_mid[symbol] = [entry for entry in self.pending_mid[symbol] if entry[0] != timeframe]
                self.pending_mid[symbol].append((timeframe, state.mid_due))
                await self._try_trivial(symbol)
                full_count += 1
            else:
                state.full_done = True
                if reuse_previous or needs_refresh or mark_price is not None:
                    if await self._run_reactive(symbol, timeframe, df, close_ts, mark_price, state):
                        reactive_count += 1
        tasks = [asyncio.create_task(process_symbol(symbol)) for symbol in symbols_to_process]
        await asyncio.gather(*tasks)
        if full_count or reactive_count:
            logger.info(f"[3m-refresh] full={full_count} reactive={reactive_count}")

    async def _schedule_loop(self) -> None:
        interval = 39.0
        while not self._shutdown.is_set():
            now = utc_now()
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elapsed = (now - midnight).total_seconds()
            wait = interval - (elapsed % interval)
            if wait <= 0.1:
                wait += interval
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=wait)
                break
            except asyncio.TimeoutError:
                pass
            await self._refresh_symbols_if_needed()
            for tf in self._schedule_order:
                if self._shutdown.is_set():
                    break
                await self._process_timeframe(tf, force=self.reactive_mode)
            self._sentiment_dirty = True
            self._save_due = True

    async def _get_missing_symbols(self) -> List[str]:
        """Get list of missing 3m symbols from Redis for prioritization."""
        try:
            missing_json = await self.redis_client.get("missing_3m_symbols")
            if missing_json:
                missing = json.loads(missing_json)
                if isinstance(missing, list):
                    return [s for s in missing if s in self.symbol_set]
        except Exception:
            pass
        return []

    async def _process_timeframe(self, timeframe: str, force: bool = False) -> None:
        if timeframe == "15m" and self._is_15m_priority_time():
            await self._ensure_15m_complete()
        half_duration = TIMEFRAMES[timeframe]["half"]
        full_count = 0
        mid_count = 0
        reactive_count = 0
        missing_symbols = await self._get_missing_symbols() if timeframe == "3m" else []
        symbols_to_process = list(self.symbols)
        if missing_symbols:
            symbols_to_process = [s for s in missing_symbols if s in self.symbol_set] + [s for s in symbols_to_process if s not in missing_symbols]
            logger.debug(f"🎯 Prioritizing {len(missing_symbols)} missing symbols for 3m processing")

        async def process_symbol(symbol: str) -> None:
            nonlocal full_count, mid_count, reactive_count
            df, close_ts, source = await self.kline_manager.get_latest(symbol, timeframe)
            symbol_data = self.data.setdefault(symbol, {})
            state = self.state[symbol][timeframe]
            reuse_previous = False
            if df is None or close_ts is None:
                if timeframe == "15m":
                    df = await self.kline_manager._calculate_15m_from_3m(symbol)
                    if df is not None and not df.empty and "timestamp_dt" in df.columns:
                        ts_series = df["timestamp_dt"].dropna()
                        if not ts_series.empty:
                            close_ts = ts_series.iloc[-1].to_pydatetime().astimezone(timezone.utc)
                    if df is None or close_ts is None:
                        if state.last_df is not None and state.last_close is not None:
                            df = state.last_df.copy()
                            close_ts = state.last_close
                            reuse_previous = True
                        else:
                            state.full_done = False
                            return
                elif state.last_df is not None and state.last_close is not None:
                    df = state.last_df.copy()
                    close_ts = state.last_close
                    reuse_previous = True
                else:
                    state.full_done = False
                    return
            now = utc_now()
            required_keys = self.required_by_timeframe.get(timeframe, [])
            needs_refresh = any(symbol_data.get(key) is None for key in required_keys)
            mark_price = await self.price_cache.get_price(symbol) if required_keys or force or needs_refresh or reuse_previous or (close_ts is not None and (now - close_ts).total_seconds() >= 90) else None
            new_bar = state.last_close is None or (close_ts is not None and close_ts > state.last_close)
            if new_bar or needs_refresh or (required_keys and force):
                await self._run_full(symbol, timeframe, df, close_ts, mark_price, state)
                state.last_close = close_ts
                state.full_done = True
                state.mid_done = False
                state.trivial_done = False
                state.mid_due = close_ts + timedelta(seconds=half_duration)
                state.last_full_finish = now
                self.pending_mid[symbol] = [entry for entry in self.pending_mid[symbol] if entry[0] != timeframe]
                self.pending_mid[symbol].append((timeframe, state.mid_due))
                await self._try_trivial(symbol)
                full_count += 1
            else:
                state.full_done = True
                if reuse_previous or force or needs_refresh:
                    if await self._run_reactive(symbol, timeframe, df, close_ts, mark_price, state):
                        reactive_count += 1
                if await self._maybe_run_mid(symbol, timeframe, df, close_ts, mark_price):
                    mid_count += 1

        tasks = [asyncio.create_task(process_symbol(symbol)) for symbol in self.symbols]
        await asyncio.gather(*tasks)

        # OPTIMIZATION: Push to Shared Memory in a single BATCH
        if self.shared_proxy:
            try:
                batch_payload = {}
                now_ts = time.time()
                for symbol in self.symbols:
                    if symbol in self.data:
                        payload = self.data[symbol].copy()
                        payload['_hot_ts'] = now_ts
                        payload['_tick_ts'] = now_ts
                        payload['_hot_source'] = 'ez_indicators'
                        batch_payload[symbol] = payload
                if batch_payload:
                    # Run IPC call in thread to avoid blocking loop
                    await asyncio.to_thread(self.shared_proxy.update_batch, batch_payload)
            except Exception as e:
                logger.warning(f"[SharedMem] Batch update error: {e}")
                self._shared_mem_last_attempt = 0.0
                self.shared_proxy = None

        if full_count or mid_count or reactive_count:
            logger.info(f"[{timeframe}] full={full_count} mid={mid_count} reactive={reactive_count}")
        if timeframe == self._schedule_order[-1]:
            self._sentiment_dirty = True
            self._save_due = True

    async def _run_full(self, symbol: str, timeframe: str, df: pd.DataFrame, close_ts: datetime, mark_price: Optional[float], state: SymbolTimeframeState) -> None:
        required_keys = self.required_by_timeframe.get(timeframe, [])
        mark_price, mark_price_ts = await self.price_cache.get_price_with_ts(symbol)
        result = self.calculator.compute(df, timeframe, mark_price, mid_run=False, mark_price_ts=mark_price_ts)
        if not result:
            return
        symbol_data = self.data.setdefault(symbol, {})
        if not self._timeframe_result_valid(timeframe, result):
            return
        self._purge_timeframe_fields(symbol_data, timeframe)
        symbol_data.update(result)
        for key in required_keys:
            if key not in symbol_data or symbol_data[key] is None:
                mark_price, mark_price_ts = await self.price_cache.get_price_with_ts(symbol)
                result = self.calculator.compute(df, timeframe, mark_price, mid_run=False, mark_price_ts=mark_price_ts)
                if result and self._timeframe_result_valid(timeframe, result):
                    symbol_data.update(result)
                    break
        self._inject_external_scores(symbol, symbol_data)
        self._inject_dc_moment(symbol, symbol_data)
        self._inject_wt_composite(symbol, symbol_data)
        self._flag_errors(symbol_data)
        self._update_master_timestamp(symbol_data)
        state.last_df = df.copy()

        # Ensure tick timestamp is set from the result if not already there
        if '_tick_ts' not in symbol_data and 'timestamp' in symbol_data:
            try:
                dt_obj = datetime.fromisoformat(symbol_data['timestamp'].replace('Z', '+00:00'))
                symbol_data['_tick_ts'] = dt_obj.timestamp()
                symbol_data['ts'] = symbol_data['_tick_ts']
            except Exception:
                symbol_data['_tick_ts'] = time.time()
                symbol_data['ts'] = symbol_data['_tick_ts']
        
        # ALSO update top-level timestamp to match master timestamp for consistency
        if 'timestamp' in symbol_data:
            symbol_data['ts'] = symbol_data.get('_tick_ts', time.time())
        
        self._push_to_shared_memory(symbol, symbol_data)
        await self._emit_signals(symbol)
        await self._mark_dirty()

    async def _maybe_run_mid(self, symbol: str, timeframe: str, df: pd.DataFrame, close_ts: datetime, mark_price: Optional[float]) -> bool:
        state = self.state[symbol][timeframe]
        if state.mid_due is None or state.mid_done:
            return False
        if utc_now() < state.mid_due:
            return False
        if not await self._trivial_ready(symbol):
            return False
        mark_price, mark_price_ts = await self.price_cache.get_price_with_ts(symbol)
        result = self.calculator.compute(df, timeframe, mark_price, mid_run=False, mark_price_ts=mark_price_ts)
        if not result:
            return False
        if not self._timeframe_result_valid(timeframe, result):
            return False
        symbol_data = self.data.setdefault(symbol, {})
        symbol_data.update(result)
        self._inject_external_scores(symbol, symbol_data)
        self._inject_dc_moment(symbol, symbol_data)
        self._inject_wt_composite(symbol, symbol_data)
        self._flag_errors(symbol_data)
        state.mid_done = True
        state.last_mid_finish = utc_now()
        state.last_df = df.copy()
        self._update_master_timestamp(symbol_data)
        self._push_to_shared_memory(symbol, symbol_data)
        await self._emit_signals(symbol)
        await self._mark_dirty()
        return True


    async def _run_reactive(self, symbol: str, timeframe: str, df: pd.DataFrame, close_ts: Optional[datetime], mark_price: Optional[float], state: SymbolTimeframeState) -> bool:
        if df is None:
            return False
        
        # ... (Fetching prices logic) ...
        cache_price, cache_ts = await self.price_cache.get_price_with_ts(symbol)
        if cache_price is not None:
            final_price = cache_price
            final_ts = cache_ts
        else:
            final_price = mark_price
            final_ts = close_ts if close_ts else utc_now()

        # Compute
        result = self.calculator.compute(df, timeframe, final_price, mid_run=True, mark_price_ts=final_ts)
        
        if not result: return False
        if not self._timeframe_result_valid(timeframe, result): return False

        # ... (Updating local data) ...
        symbol_data = self.data.setdefault(symbol, {})
        timestamp_key = f"timestamp_{timeframe}"
        ts_str = isoformat(final_ts if final_ts else datetime.now(timezone.utc))
        result[timestamp_key] = ts_str
        
        # Only update global timestamp if this is the freshest data
        # result["timestamp"] = ts_str 
        
        symbol_data.update(result)
        
        # ... (Scores and Error flags) ...
        
        # CRITICAL: PUSH TO SHARED MEMORY HERE
        # Ensure tick timestamp is updated from final_ts
        symbol_data['_tick_ts'] = final_ts.timestamp() if final_ts else time.time()
        symbol_data['ts'] = symbol_data['_tick_ts']
        
        self._push_to_shared_memory(symbol, symbol_data)
        
        await self._emit_signals(symbol)
        await self._mark_dirty()
        return True

    # async def _run_reactive(self, symbol: str, timeframe: str, df: pd.DataFrame, close_ts: Optional[datetime], mark_price: Optional[float], state: SymbolTimeframeState) -> bool:
    #     if df is None:
    #         return False
    #     cache_price, cache_ts = await self.price_cache.get_price_with_ts(symbol)
        
    #     # Decide which price/ts to use
    #     if cache_price is not None:
    #         final_price = cache_price
    #         final_ts = cache_ts
    #     else:
    #         final_price = mark_price
    #         final_ts = close_ts if close_ts else cache_ts
    #     result = self.calculator.compute(df, timeframe, final_price, mid_run=True, mark_price_ts=final_ts)
        
    #     if not result:
    #         return False
    #     if not self._timeframe_result_valid(timeframe, result):
    #         return False

    #     symbol_data = self.data.setdefault(symbol, {})
    #     timestamp_key = f"timestamp_{timeframe}"

    #     # 3. STRICT TIMESTAMP ASSIGNMENT
    #     # We use isoformat() on the specific data timestamp we found.
    #     ts_str = isoformat(final_ts if final_ts else datetime.now(timezone.utc))
        
    #     result[timestamp_key] = ts_str
    #     result["timestamp"] = ts_str
        
    #     # Add metadata for debugging/merging
    #     if final_price is not None:
    #         result[f"{timestamp_key}_hot"] = ts_str
            
    #     symbol_data.update(result)
    #     self._inject_external_scores(symbol, symbol_data)
    #     self._flag_errors(symbol_data)
    #     state.last_df = df.copy()
    #     self._update_master_timestamp(symbol_data)
    #     self._push_to_shared_memory(symbol, symbol_data)
        
    #     await self._emit_signals(symbol)
    #     await self._mark_dirty()
    #     return True

    async def _midcycle_loop(self) -> None:
        while not self._shutdown.is_set():
            now = utc_now()
            for symbol in self.symbols:
                queue = self.pending_mid[symbol]
                if not queue:
                    continue
                remaining: List[Tuple[str, datetime]] = []
                for timeframe, due in queue:
                    state = self.state[symbol][timeframe]
                    if state.mid_done:
                        continue
                    if now >= due:
                        df, close_ts, source = await self.kline_manager.get_latest(symbol, timeframe)
                        if df is None or close_ts is None:
                            if state.last_df is not None and state.last_close is not None:
                                df = state.last_df.copy()
                                close_ts = state.last_close
                            else:
                                remaining.append((timeframe, due))
                                continue
                        mark_price = await self.price_cache.get_price(symbol)
                        await self._maybe_run_mid(symbol, timeframe, df, close_ts, mark_price)
                    else:
                        remaining.append((timeframe, due))
                self.pending_mid[symbol] = remaining
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=1.0)
                break
            except asyncio.TimeoutError:
                continue

    async def _refresh_symbols_if_needed(self) -> None:
        if (utc_now() - self._last_symbol_refresh).total_seconds() < 360:
            return
        new_symbols = load_symbols()
        if not new_symbols:
            self._last_symbol_refresh = utc_now()
            return
        new_set = set(new_symbols)
        current_set = set(self.symbols)
        if new_set == current_set:
            self._last_symbol_refresh = utc_now()
            return
        for symbol in new_symbols:
            if symbol not in self.data:
                self.data[symbol] = {}
            if symbol not in self.state:
                self.state[symbol] = {tf: SymbolTimeframeState(timeframe=tf) for tf in TIMEFRAMES.keys()}
                self.pending_mid[symbol] = []
                self.pending_trivial[symbol] = False
        for symbol in current_set - new_set:
            self.pending_mid.pop(symbol, None)
            self.pending_trivial.pop(symbol, None)
            self.state.pop(symbol, None)
        self.symbols = new_symbols
        self.symbol_set = set(new_symbols)
        await self.price_cache.update_symbols(self.symbols)
        self._last_symbol_refresh = utc_now()

    async def _try_trivial(self, symbol: str) -> None:
        if self.pending_trivial[symbol]:
            return
        if await self._trivial_ready(symbol):
            await self._run_trivial(symbol)

    async def _trivial_ready(self, symbol: str) -> bool:
        return all(self.state[symbol][tf].full_done for tf in TIMEFRAMES.keys())

    async def _run_trivial(self, symbol: str) -> None:
        self.pending_trivial[symbol] = True
        for tf in TIMEFRAMES.keys():
            self.state[symbol][tf].trivial_done = True
        self.pending_trivial[symbol] = False
        await self._mark_dirty()

    def _symbol_complete(self, symbol: str) -> bool:
        """Check if a symbol has completed all timeframes"""
        if symbol not in self.state:
            return False
        for tf in self._schedule_order:
            state = self.state[symbol].get(tf)
            if not state or not state.full_done:
                return False
        return True
    def _all_symbols_complete(self) -> bool:
        """Check if all symbols have completed all timeframes"""
        if not self.symbols:
            return False
        return all(self._symbol_complete(sym) for sym in self.symbols)
    async def _mark_dirty(self) -> None:
        self._last_dirty = utc_now()
        self._dirty = True

    async def _save_loop(self) -> None:
        base_interval = float(getattr(config, "INDICATORS_SAVE_INTERVAL_SECONDS", 60.0))
        save_interval = 90.0 if self.env == "macbook" else base_interval
        last_periodic_save = time.time()
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=1.0)
                break
            except asyncio.TimeoutError:
                pass
            now = time.time()
            time_since_last_save = now - last_periodic_save
            if time_since_last_save >= save_interval:
                self._save_due = True
                self._dirty = True
                last_periodic_save = now
            if not (self._save_due and self._dirty):
                continue
            await self._save_data()
    
    # async def _save_data(self) -> None:
    #     async with self._save_lock:
    #         incomplete_count = sum(1 for sym in self.symbols if not self._symbol_complete(sym)) if self.symbols else 0
    #         if incomplete_count > 0:
    #             logger.debug(f"💾 Saving all {len(self.symbols)} symbols ({incomplete_count} incomplete)")
            
    #         if self._sentiment_dirty:
    #             await self._refresh_sentiment()
    #             self._sentiment_dirty = False
    #         if self._scores_dirty:
    #             await save_json_file(self.ranking_points_file, OrderedDict(self.ranking_scores.items()))
    #             self._scores_dirty = False
    #         if self._score_ranges_dirty:
    #             await save_json_file(self.score_ranges_file, self.score_ranges)
    #             self._score_ranges_dirty = False
                
    #         safe: Dict[str, Dict[str, Any]] = {}
    #         for symbol, values in self.data.items():
    #             if symbol not in self.symbol_set:
    #                 continue
    #             if not isinstance(values, dict):
    #                 continue
    #             filtered: Dict[str, Any] = {}
    #             for key, value in values.items():
    #                 if key == "_updated_timeframes":
    #                     continue
    #                 cleaned = clean_nans(value)
    #                 if key in self.required_indicator_set:
    #                     filtered[key] = cleaned
    #                     continue
    #                 if isinstance(cleaned, (int, float)) and not isinstance(cleaned, bool) and cleaned == 0.0 and key.startswith(("dc_", "ema", "atr", "high", "low", "wt", "stoch", "sma")):
    #                     continue
    #                 filtered[key] = cleaned
    #             if filtered:
    #                 safe[symbol] = OrderedDict(sorted(filtered.items()))
    #             else:
    #                 safe[symbol] = OrderedDict(sorted({k: clean_nans(v) for k, v in values.items()}.items()))
            
    #         # Fill missing required keys with fallbacks
    #         missing_summary: Dict[str, List[str]] = {}
    #         for symbol, values in safe.items():
    #             missing = self._missing_required_keys(values)
    #             if missing:
    #                 missing_summary[symbol] = missing
    #         if missing_summary:
    #             for symbol, keys in missing_summary.items():
    #                 values = safe.get(symbol)
    #                 if not values:
    #                     continue
    #                 for key in keys:
    #                     if self._is_noncritical_key(key):
    #                         if key.endswith(("_classification", "_state")):
    #                             values[key] = values.get(key) or "NEUTRAL"
    #                         else:
    #                             values[key] = values.get(key) or 0.0
    #                     else:
    #                         fallback_value = self._fallback_value(symbol, key, values)
    #                         if fallback_value is not None:
    #                             values[key] = fallback_value
    #             for symbol in missing_summary:
    #                 self._missing_history[symbol] = 0
            
    #         self._consecutive_skips = 0
    #         ordered_symbols = OrderedDict()
    #         remaining_symbols = dict(safe)
    #         for sym in self.priority_symbols:
    #             values = remaining_symbols.pop(sym, None)
    #             if isinstance(values, dict):
    #                 ordered_symbols[sym] = values
    #         for sym in sorted(remaining_symbols.keys()):
    #             ordered_symbols[sym] = remaining_symbols[sym]
    #         for sym in self.symbols:
    #             if sym not in ordered_symbols:
    #                 ordered_symbols[sym] = OrderedDict()
            
    #         # Serialization
    #         try:
    #             # Update tick timestamp before serialization
    #             # Use current system time for serialization timestamp to ensure freshness for consumers
    #             # but keep the individual symbol data timestamps intact
    #             now_ts = time.time()
    #             for symbol, symbol_data in ordered_symbols.items():
    #                 if isinstance(symbol_data, dict):
    #                     # Use the actual timestamp from the data for _tick_ts if it exists
    #                     # otherwise fallback to system time
    #                     ts_str = symbol_data.get('timestamp')
    #                     if ts_str:
    #                         try:
    #                             dt_obj = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    #                             symbol_data['_tick_ts'] = dt_obj.timestamp()
    #                             symbol_data['ts'] = symbol_data['_tick_ts']
    #                         except:
    #                             symbol_data['_tick_ts'] = now_ts
    #                             symbol_data['ts'] = symbol_data['_tick_ts']
    #                     else:
    #                         symbol_data['_tick_ts'] = now_ts
    #                         symbol_data['ts'] = symbol_data['_tick_ts']
    #             try:
    #                 compact_bytes = orjson.dumps(ordered_symbols, default=default_serializer)
    #                 file_payload_bytes = orjson.dumps(
    #                     ordered_symbols, 
    #                     option=orjson.OPT_INDENT_2, 
    #                     default=default_serializer
    #                 )
    #             except Exception as e:
    #                 logger.error(f"Serialization failed: {e}")
    #                 return

    #             redis_payload = compact_bytes.decode('utf-8')                
    #             async with aiofiles.open(tmp_path, "wb") as f: 
    #                 await f.write(file_payload_bytes)


    #         file_payload = json.dumps(ordered_symbols, indent=2)
    #         payload_hash = hashlib.sha1(compact_bytes).hexdigest()
            
    #         if payload_hash == self._last_payload_hash:
    #             self._dirty = False
    #             self._save_due = False
    #             return

    #         # --- FILENAME FIX ---
    #         # Explicitly ensure single underscore separator
    #         ts_str = utc_now().strftime("%Y%m%d_%H%M%S") # Ensures 20260129_035407
    #         filename = f"market_data_{ts_str}.json"       # Ensures market_data_20260129_035407.json
            
    #         tmp_path = self.market_data_file.with_suffix(".tmp")
    #         backup_file = None if self.total_instances > 1 else self.market_data_file.parent / filename

    #         try:
    #             await self._broadcast_market_data(redis_payload)
    #             await ensure_path_writable(tmp_path)
    #             await ensure_path_writable(self.market_data_file)
                
    #             async with aiofiles.open(tmp_path, "w") as f:
    #                 await f.write(file_payload)
    #             os.replace(tmp_path, self.market_data_file)
                
    #             if backup_file:
    #                 await ensure_path_writable(backup_file)
    #                 async with aiofiles.open(backup_file, "w") as f:
    #                     await f.write(file_payload)
    #                 await self._prune_market_data_backups()
    #                 logger.info(f"✅ Wrote complete file {self.market_data_file.name} size={self.market_data_file.stat().st_size} and backup {backup_file.name}")
    #             else:
    #                 logger.debug(f"Wrote worker file {self.market_data_file.name}")
    #         except Exception as e:
    #             logger.error(f"Error saving market data: {e}")
    #         finally:
    #             if tmp_path.exists():
    #                 try:
    #                     os.unlink(tmp_path)
    #                 except Exception:
    #                     pass
            
    #         self._last_payload_hash = payload_hash
    #         self._dirty = False
    #         self._save_due = False
    #         self._last_cycle_time = utc_now()
    #         self._consecutive_skips = 0
    async def _save_data(self) -> None:
        async with self._save_lock:
            # 1. Handle background updates
            if self._sentiment_dirty:
                await self._refresh_sentiment()
                self._sentiment_dirty = False
            if self._scores_dirty:
                await save_json_file(self.ranking_points_file, OrderedDict(self.ranking_scores.items()))
                self._scores_dirty = False
            if self._score_ranges_dirty:
                await save_json_file(self.score_ranges_file, self.score_ranges)
                self._score_ranges_dirty = False

            # 2. Build the sanitized data dictionary
            safe: Dict[str, Dict[str, Any]] = {}
            for symbol, values in self.data.items():
                if symbol not in self.symbol_set or not isinstance(values, dict):
                    continue
                
                filtered: Dict[str, Any] = {}
                for key, value in values.items():
                    if key == "_updated_timeframes": continue
                    
                    cleaned = clean_nans(value)
                    # Filter out useless 0.0 values to keep file size small
                    if isinstance(cleaned, (int, float)) and not isinstance(cleaned, bool) and cleaned == 0.0:
                        if key.startswith(("dc_", "ema", "atr", "high", "low", "wt", "stoch", "sma")) and key not in self.required_indicator_set:
                            continue
                    filtered[key] = cleaned
                
                # Apply fallbacks for missing required keys
                missing = self._missing_required_keys(filtered)
                if missing:
                    for key in missing:
                        if self._is_noncritical_key(key):
                            filtered[key] = "NEUTRAL" if key.endswith(("_classification", "_state")) else 0.0
                        else:
                            fallback = self._fallback_value(symbol, key, filtered)
                            if fallback is not None: filtered[key] = fallback
                
                safe[symbol] = OrderedDict(sorted(filtered.items()))

            # 3. Create priority-ordered dictionary
            ordered_symbols = OrderedDict()
            for sym in self.priority_symbols:
                if sym in safe: ordered_symbols[sym] = safe.pop(sym)
            for sym in sorted(safe.keys()):
                ordered_symbols[sym] = safe[sym]
            
            # Ensure every symbol in the official list exists in output
            for sym in self.symbols:
                if sym not in ordered_symbols: ordered_symbols[sym] = OrderedDict()


            # # 3.5 Merge 1m/3m real-time stochastic data from ez_market_data (hot_metrics)
            # # Only overlay stochastic VALUES — timestamps stay from kline candle close times
            # try:
            #     if self.redis_client:
            #         hot_keys = [f"hot_metrics:{sym}" for sym in ordered_symbols]
            #         raw_values = await self.redis_client.mget(hot_keys)
            #         merge_count = 0
            #         hot_to_ind = {'k_1m': 'stoch_k_1m', 'd_1m': 'stoch_d_1m', 'k_3m': 'stoch_k_3m', 'd_3m': 'stoch_d_3m', 'k_1m_prev': 'k_1m_prev', 'd_1m_prev': 'd_1m_prev', 'k_3m_prev': 'k_3m_prev', 'd_3m_prev': 'd_3m_prev'}
            #         for sym, raw in zip(ordered_symbols, raw_values):
            #             if not raw:
            #                 continue
            #             try:
            #                 hot = orjson.loads(raw)
            #             except Exception:
            #                 continue
            #             sym_data = ordered_symbols[sym]
            #             if not isinstance(sym_data, dict):
            #                 continue
            #             hot_tick = hot.get('_tick_ts', 0)
            #             if not hot_tick or (time.time() - hot_tick) > 120:
            #                 continue
            #             merged = False
            #             for hot_fld, ind_fld in hot_to_ind.items():
            #                 val = hot.get(hot_fld)
            #                 if val is not None:
            #                     sym_data[ind_fld] = val
            #                     merged = True
            #             if merged:
            #                 merge_count += 1
            #         if merge_count:
            #             logger.info(f"[HOT_MERGE] Merged 1m/3m stoch from ez_market_data for {merge_count} symbols")
            # except Exception as e:
            #     logger.debug(f"[HOT_MERGE] Error: {e}")

            # 4. Update Timestamps and Metadata
            now_ts = time.time()
            for symbol, symbol_data in ordered_symbols.items():
                if isinstance(symbol_data, dict):
                    ts_str = symbol_data.get('timestamp')
                    try:
                        if ts_str:
                            dt_obj = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                            symbol_data['_tick_ts'] = dt_obj.timestamp()
                        else:
                            symbol_data['_tick_ts'] = now_ts
                    except Exception:
                        symbol_data['_tick_ts'] = now_ts
                    symbol_data['ts'] = symbol_data['_tick_ts']

            # 5. Path Setup
            ts_label = utc_now().strftime("%Y%m%d_%H%M%S")
            tmp_path = self.market_data_file.with_suffix(f".{int(now_ts)}.tmp")
            backup_file = None if self.total_instances > 1 else self.market_data_file.parent / f"market_data_{ts_label}.json"

            # 6. SERIALIZATION
            try:
                # COMPACT version for Redis and Hashing
                compact_bytes = orjson.dumps(ordered_symbols, default=default_serializer)
                
                # Deduplication check
                payload_hash = hashlib.sha1(compact_bytes).hexdigest()
                if payload_hash == self._last_payload_hash:
                    self._dirty = False
                    self._save_due = False
                    return

                # PRETTY version for Disk
                file_payload_bytes = orjson.dumps(
                    ordered_symbols, 
                    option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS, 
                    default=default_serializer
                )
            except Exception as e:
                logger.error(f"Serialization Failed: {e}")
                return

            # 7. WRITE AND BROADCAST
            try:
                # Broadcast to Redis
                await self._broadcast_market_data(compact_bytes.decode('utf-8'))

                # Write Main File
                await ensure_path_writable(tmp_path)
                async with aiofiles.open(tmp_path, "wb") as f:
                    await f.write(file_payload_bytes)
                
                # Atomic swap
                await asyncio.to_thread(os.replace, tmp_path, self.market_data_file)

                # Write Backup File
                if backup_file:
                    await ensure_path_writable(backup_file)
                    async with aiofiles.open(backup_file, "wb") as f:
                        await f.write(file_payload_bytes)
                    await self._prune_market_data_backups()
                    logger.info(f"✅ Saved indicators to {self.market_data_file.name}")

            except Exception as e:
                logger.error(f"IO Error during save: {e}")
            finally:
                if tmp_path.exists():
                    try: os.unlink(tmp_path)
                    except Exception: pass
            
            self._last_payload_hash = payload_hash
            self._dirty = False
            self._save_due = False

    async def _prune_market_data_backups(self) -> None:
        directory = self.market_data_file.parent
        if directory is None or not directory.exists():
            return
        
        # Matches market_data_*.json
        files = list(directory.glob("market_data_*.json"))
        if not files:
            return

        now = utc_now()
        keep: Set[Path] = set()
        
        cutoff_3m = 3 * 60
        cutoff_1h = 60 * 60
        cutoff_24h = 24 * 60 * 60
        cutoff_7d = 7 * 24 * 60 * 60

        interval_15m = 15 * 60
        interval_1h = 60 * 60
        interval_4h = 4 * 60 * 60
        interval_1d = 24 * 60 * 60

        candidates_15m: Dict[int, Tuple[Path, datetime]] = {}
        candidates_1h: Dict[int, Tuple[Path, datetime]] = {}
        candidates_4h: Dict[int, Tuple[Path, datetime]] = {}
        candidates_1d: Dict[int, Tuple[Path, datetime]] = {}

        prefix = "market_data_"

        for path in files:
            name = path.name
            if name.startswith("market_data_worker") or path == self.market_data_file:
                continue
            
            # Robust Parsing for Pruning
            if not name.startswith(prefix) or not name.endswith(".json"):
                continue

            # Extract timestamp part
            # Use name[len(prefix):-5] to get "20260129_035407"
            # .lstrip("_") handles accidentally generated double underscores (market_data__...)
            label = name[len(prefix):-5].lstrip("_")
            
            try:
                # Expects 20260129_035407 format
                ts = datetime.strptime(label, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                # If parsing fails, skip (don't delete immediately here, just don't manage)
                continue

            age_seconds = (now - ts).total_seconds()
            ts_epoch = int(ts.timestamp())

            if age_seconds < 0:
                keep.add(path)
                continue

            if age_seconds <= cutoff_3m:
                keep.add(path)
                continue

            if age_seconds <= cutoff_1h:
                bucket_key = ts_epoch // interval_15m
                current_best = candidates_15m.get(bucket_key)
                if not current_best or ts > current_best[1]:
                    candidates_15m[bucket_key] = (path, ts)
                continue

            if age_seconds <= cutoff_24h:
                bucket_key = ts_epoch // interval_1h
                current_best = candidates_1h.get(bucket_key)
                if not current_best or ts > current_best[1]:
                    candidates_1h[bucket_key] = (path, ts)
                continue

            if age_seconds <= cutoff_7d:
                bucket_key = ts_epoch // interval_4h
                current_best = candidates_4h.get(bucket_key)
                if not current_best or ts > current_best[1]:
                    candidates_4h[bucket_key] = (path, ts)
                continue

            bucket_key = ts_epoch // interval_1d
            current_best = candidates_1d.get(bucket_key)
            if not current_best or ts > current_best[1]:
                candidates_1d[bucket_key] = (path, ts)

        keep.update(p[0] for p in candidates_15m.values())
        keep.update(p[0] for p in candidates_1h.values())
        keep.update(p[0] for p in candidates_4h.values())
        keep.update(p[0] for p in candidates_1d.values())

        for path in files:
            if path == self.market_data_file: 
                continue
            
            # Only delete if it matches pattern but isn't in keep
            if path.name.startswith(prefix) and path not in keep:
                try:
                    await asyncio.to_thread(path.unlink)
                except Exception:
                    pass

    async def _check_redis_health(self) -> bool:
        """Check if Redis connection is healthy"""
        if not self.redis_client:
            return False
        now = time.time()
        if (now - self._last_redis_health_check) < self._redis_health_check_interval:
            return self._redis_broadcast_failures == 0
        try:
            await self.redis_client.ping()
            self._last_redis_health_check = now
            if self._redis_broadcast_failures > 0:
                logger.info(f"✅ Redis connection restored after {self._redis_broadcast_failures} failures")
                self._redis_broadcast_failures = 0
            return True
        except Exception as e:
            self._last_redis_health_check = now
            return False

    # async def _broadcast_market_data(self, payload: str) -> None:
    #     if self.total_instances > 1:
    #         return
    #     if self.redis_client:
    #         max_retries = 3
    #         retry_delay = 0.1
    #         success = False
    #         broadcast_timeout = 5.0 if self.env == "macbook" else 10.0
    #         for attempt in range(max_retries):
    #             try:
    #                 if attempt > 0:
    #                     await asyncio.sleep(retry_delay * (2 ** (attempt - 1)))
    #                 if not await asyncio.wait_for(self._check_redis_health(), timeout=2.0):
    #                     if attempt == 0:
    #                         logger.warning(f"⚠️ Redis health check failed, attempting reconnect...")
    #                     continue
    #                 await asyncio.wait_for(self.redis_client.set(config.REDIS_KEY_MARKET_DATA, payload), timeout=broadcast_timeout)
    #                 await asyncio.wait_for(self.redis_client.publish(self._market_channel, payload), timeout=broadcast_timeout)
    #                 success = True
    #                 if self._redis_broadcast_failures > 0:
    #                     logger.info(f"✅ Redis broadcast restored after {self._redis_broadcast_failures} failures")
    #                     self._redis_broadcast_failures = 0
    #                 self._last_redis_success = time.time()
    #                 break
    #             except (redis.ConnectionError, asyncio.TimeoutError) as e:
    #                 self._redis_broadcast_failures += 1
    #                 if attempt == max_retries - 1:
    #                     logger.error(f"❌ Redis broadcast failed after {max_retries} attempts (ConnectionError: {e}) - ez_manage will fallback to files")
    #             except redis.TimeoutError as e:
    #                 self._redis_broadcast_failures += 1
    #                 if attempt == max_retries - 1:
    #                     logger.error(f"❌ Redis broadcast failed after {max_retries} attempts (TimeoutError: {e}) - ez_manage will fallback to files")
    #             except Exception as e:
    #                 self._redis_broadcast_failures += 1
    #                 if attempt == max_retries - 1:
    #                     logger.error(f"❌ Redis broadcast failed after {max_retries} attempts ({type(e).__name__}: {e}) - ez_manage will fallback to files")
    #         if not success and self._redis_broadcast_failures % 10 == 1:
    #             logger.warning(f"⚠️ Redis broadcast failures: {self._redis_broadcast_failures} (last success: {time.time() - self._last_redis_success:.1f}s ago)")
    #     if self.webhook_url:
    #         try:
    #             headers = {"Content-Type": "application/json"}
    #             if self.webhook_secret:
    #                 headers["Authorization"] = self.webhook_secret
    #             async with aiohttp.ClientSession() as session:
    #                 await session.post(self.webhook_url, data=payload, headers=headers, timeout=10)
    #         except Exception:
    #             pass
    # In ez_indicators.py

    async def _broadcast_market_data(self, payload: Union[str, dict]) -> None:
        if self.total_instances > 1: return
        compact_json = ""
        # ---------------------------------------------------------
        # 1. CLEAN DATA (Plan B Preparation)
        # ---------------------------------------------------------
        compact_json = ""
        try:
            if isinstance(payload, dict):
                compact_bytes = orjson.dumps(payload, default=orjson_default)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                compact_json = compact_bytes.decode('utf-8')
            elif isinstance(payload, str):
                if "\n" in payload:
                    # If it's a pretty string, round-trip it through orjson to crush it
                    data = orjson.loads(payload)  # type: ignore # pylint: disable=no-member,c-extension-no-member
                    compact_json = orjson.dumps(data, default=orjson_default).decode('utf-8')  # type: ignore # pylint: disable=no-member,c-extension-no-member
                else:
                    compact_json = payload
        except Exception as e:
            logger.error(f"⚠️ JSON Compact Failed: {e}")
            return

        # ---------------------------------------------------------
        # 2. PLAN B: REDIS (Network Backbone)
        # ---------------------------------------------------------
        if self.redis_client:
            try:
                # Write the COMPACT JSON (Fixes parsing errors in consumers)
                await self.redis_client.set(config.REDIS_KEY_MARKET_DATA, compact_json)
                await self.redis_client.publish(self._market_channel, compact_json)
                self._redis_broadcast_failures = 0
            except Exception as e:
                self._redis_broadcast_failures += 1
                if self._redis_broadcast_failures % 10 == 1:
                    logger.warning(f"⚠️ [Plan B] Redis write failed: {e}")
        try:
            file_path = config.DATA_DIR / "latest_market_data.json"
            temp_path = config.DATA_DIR / "latest_market_data.tmp"
            
            # Atomic Write
            async with aiofiles.open(temp_path, 'w') as f:
                await f.write(compact_json)
            
            os.rename(temp_path, file_path)
        except Exception as e:
            logger.error(f"⚠️ [Plan C] File write failed: {e}")

        # 4. Webhook (Existing)
        if self.webhook_url:
            try:
                headers = {"Content-Type": "application/json"}
                if self.webhook_secret: headers["Authorization"] = self.webhook_secret
                async with aiohttp.ClientSession() as session:
                    await session.post(self.webhook_url, data=compact_json, headers=headers, timeout=5)
            except Exception: pass


    # async def _broadcast_market_data(self, payload: Union[str, dict]) -> None:
    #     if self.total_instances > 1: return

    #     # 1. COMPACT DATA (Crucial fix for "bad redis info")
    #     try:
    #         if isinstance(payload, dict):
    #             # separators=(',', ':') removes all whitespace/newlines
    #             payload = json.dumps(payload, separators=(',', ':'))
    #         elif isinstance(payload, str) and "\n" in payload:
    #             # If it's already a string but "pretty", crush it
    #             payload = json.dumps(json.loads(payload), separators=(',', ':'))
    #     except Exception as e:
    #         logger.error(f"⚠️ JSON compaction failed: {e}")
    #         return # Don't write garbage to Redis

    #     # 2. SEND TO REDIS (SimpleRedisManager compatible)
    #     # We assume self.redis_client is your SimpleRedisManager instance
    #     if self.redis_client:
    #         try:
    #             # Get the raw aio-redis connection from the manager
    #             client = self.redis_client.connections.get("local")
    #             if not client and hasattr(self.redis_client, 'get_connection'):
    #                  client = await self.redis_client.get_connection("local")

    #             if client:
    #                 # Write to the MAIN key that the Bridge watches
    #                 await client.set("latest_market_data", payload)
    #                 # Publish for immediate listeners
    #                 await client.publish("latest_market_data_pub", payload)
                    
    #                 self._last_redis_success = time.time()
    #                 self._redis_broadcast_failures = 0
    #         except Exception as e:
    #             self._redis_broadcast_failures += 1
    #             if self._redis_broadcast_failures % 10 == 1:
    #                 logger.warning(f"⚠️ Redis broadcast failed: {e}")

    #     # 3. WEBHOOK (Optional, kept from your code)
    #     if self.webhook_url:
    #         try:
    #             headers = {"Content-Type": "application/json"}
    #             if self.webhook_secret: headers["Authorization"] = self.webhook_secret
    #             async with aiohttp.ClientSession() as session:
    #                 await session.post(self.webhook_url, data=payload, headers=headers, timeout=5)
    #         except: pass           

    # async def _broadcast_market_data(self, payload: Union[str, dict]) -> None:
    #     if self.total_instances > 1:
    #         return
            
    #     # --- FIX: Ensure Payload is Compact JSON ---
    #     try:
    #         if isinstance(payload, dict):
    #             # If input is a dict, dump it as a compact string (no spaces, no newlines)
    #             payload = json.dumps(payload, separators=(',', ':'))
    #         elif isinstance(payload, str):
    #             # If input is a string that looks pretty-printed (contains newlines),
    #             # load it and re-dump it tightly.
    #             if "\n" in payload:
    #                 payload = json.dumps(json.loads(payload), separators=(',', ':'))
    #     except Exception as e:
    #         logger.error(f"⚠️ Failed to compact JSON payload: {e}")
    #         # Proceeding with original payload as fallback
    #     # -------------------------------------------

    #     if self.redis_client:
    #         max_retries = 3
    #         retry_delay = 0.1
    #         success = False
    #         broadcast_timeout = 5.0 if self.env == "macbook" else 10.0
            
    #         for attempt in range(max_retries):
    #             try:
    #                 if attempt > 0:
    #                     await asyncio.sleep(retry_delay * (2 ** (attempt - 1)))
                    
    #                 if not await asyncio.wait_for(self._check_redis_health(), timeout=2.0):
    #                     if attempt == 0:
    #                         logger.warning(f"⚠️ Redis health check failed, attempting reconnect...")
    #                     continue
                        
    #                 # Write the cleaned, compact payload
    #                 await asyncio.wait_for(self.redis_client.set(config.REDIS_KEY_MARKET_DATA, payload), timeout=broadcast_timeout)
    #                 await asyncio.wait_for(self.redis_client.publish(self._market_channel, payload), timeout=broadcast_timeout)
                    
    #                 success = True
    #                 if self._redis_broadcast_failures > 0:
    #                     logger.info(f"✅ Redis broadcast restored after {self._redis_broadcast_failures} failures")
    #                     self._redis_broadcast_failures = 0
    #                 self._last_redis_success = time.time()
    #                 break
    #             except (redis.ConnectionError, asyncio.TimeoutError) as e:
    #                 self._redis_broadcast_failures += 1
    #                 if attempt == max_retries - 1:
    #                     logger.error(f"❌ Redis broadcast failed after {max_retries} attempts (ConnectionError: {e}) - ez_manage will fallback to files")
    #             except Exception as e:
    #                 self._redis_broadcast_failures += 1
    #                 if attempt == max_retries - 1:
    #                     logger.error(f"❌ Redis broadcast failed after {max_retries} attempts ({type(e).__name__}: {e}) - ez_manage will fallback to files")
            
    #         if not success and self._redis_broadcast_failures % 10 == 1:
    #             logger.warning(f"⚠️ Redis broadcast failures: {self._redis_broadcast_failures} (last success: {time.time() - self._last_redis_success:.1f}s ago)")
    #     if self.webhook_url:
    #         try:
    #             headers = {"Content-Type": "application/json"}
    #             if self.webhook_secret:
    #                 headers["Authorization"] = self.webhook_secret
    #             async with aiohttp.ClientSession() as session:
    #                 await session.post(self.webhook_url, data=payload, headers=headers, timeout=10)
    #         except Exception:
    #             pass
            
    def _purge_timeframe_fields(self, symbol_data: Dict[str, Any], timeframe: str) -> None:
        suffix = f"_{timeframe}"
        match = f"_{timeframe}_"
        keys_to_delete = []
        for key in list(symbol_data.keys()):
            if key.endswith(suffix) or match in key or key == f"timestamp_{timeframe}":
                keys_to_delete.append(key)
        for key in keys_to_delete:
            symbol_data.pop(key, None)
        symbol_data.pop("_source", None)
        symbol_data.pop("_updated_timeframes", None)

    def _inject_external_scores(self, symbol: str, symbol_data: Dict[str, Any]) -> None:
        key = symbol.upper()
        score = self.final_scores.get(key) if isinstance(self.final_scores, dict) else None
        if isinstance(score, (int, float)):
            symbol_data["0final_score_norm"] = score
        else:
            symbol_data.setdefault("0final_score_norm", 0.0)
        ranking = self.ranking_scores.get(key) if isinstance(self.ranking_scores, dict) else None
        if isinstance(ranking, (int, float)):
            symbol_data["0ranking_points"] = ranking
            symbol_data["0ranking_points_global"] = ranking
        else:
            symbol_data.setdefault("0ranking_points", 0.0)
            symbol_data.setdefault("0ranking_points_global", 0.0)

    _dc_rankings_cache = {}
    _dc_rankings_ts = 0.0

    def _inject_dc_moment(self, symbol: str, symbol_data: Dict[str, Any]) -> None:
        """Compute 0dc_moment (-100..+100) and 0dc_qty (-100..+100) from dc_width/dc_position across all TFs."""
        tfs_all = ['3m', '15m', '1h', '4h', 'D']
        w, p = {}, {}
        cp = float(symbol_data.get('current_price') or 0)
        if cp <= 0: return
        for tf in tfs_all:
            dw = float(symbol_data.get(f'dc_width_{tf}') or 0)
            dp = float(symbol_data.get(f'dc_position_{tf}') or 0)
            # dc_position from rankings is 0-100%, convert to 0-1 scale
            if dp > 1.0: dp = dp / 100.0
            if dw <= 0 or dp < 0:
                dch = float(symbol_data.get(f'dc_high_{tf}') or 0)
                dcl = float(symbol_data.get(f'dc_low_{tf}') or 0)
                if dch > 0 and dcl > 0 and dch > dcl:
                    if dw <= 0: dw = ((dch - dcl) / dcl) * 100
                    if dp < 0: dp = max(0.0, min(1.0, (cp - dcl) / (dch - dcl)))
            if dw > 0: w[tf] = dw
            if 0 <= dp <= 1.0: p[tf] = dp
        if len(w) < 2 or len(p) < 2: return
        wc = w.get('D', 0) * 0.50 + w.get('4h', 0) * 0.30 + w.get('1h', 0) * 0.20
        ltf_w = sum(w.get(t, 0) for t in ['3m', '15m']) / max(1, sum(1 for t in ['3m', '15m'] if t in w))
        htf_w = sum(w.get(t, 0) for t in ['D', '4h', '1h']) / max(1, sum(1 for t in ['D', '4h', '1h'] if t in w))
        exp = ltf_w / htf_w if htf_w > 0 else 0.0
        hp = p.get('D', 0.5) * 0.5 + p.get('4h', 0.5) * 0.3 + p.get('1h', 0.5) * 0.2
        lp = p.get('3m', 0.5) * 0.5 + p.get('15m', 0.5) * 0.5
        trend = (hp - 0.5) * 2.0
        if trend > 0:
            pbd = max(0.0, hp - lp) / max(hp, 0.01)
        else:
            pbd = max(0.0, lp - hp) / max(1.0 - hp, 0.01)
        pbd = min(1.0, pbd)
        eb = min(1.3, max(1.0, exp * 0.65 + 0.35)) if exp > 1.0 else max(0.7, exp)
        moment = max(-100.0, min(100.0, trend * pbd * eb * 100.0))
        symbol_data['0dc_moment'] = round(moment, 1)
        symbol_data['0dc_width_composite'] = round(wc, 2)
        symbol_data['0dc_expansion'] = round(exp, 3)
        symbol_data['0dc_htf_pos'] = round(hp, 3)
        symbol_data['0dc_ltf_pos'] = round(lp, 3)
        # 0dc_qty = moment * width_rank from rankings.json (cross-symbol)
        now = time.time()
        if now - self.__class__._dc_rankings_ts > 60:
            try:
                rfile = Path(config.DATA_DIR) / 'rankings.json'
                if rfile.exists():
                    with open(rfile) as f: self.__class__._dc_rankings_cache = json.load(f)
                    self.__class__._dc_rankings_ts = now
            except Exception: pass
        sym = symbol
        rd = self.__class__._dc_rankings_cache.get(sym, {})
        wr = float(rd.get('dc_width_rank', 0.5))
        symbol_data['0dc_qty'] = round(moment * wr, 1)

    def _inject_wt_composite(self, symbol: str, symbol_data: Dict[str, Any]) -> None:
        """Cross-TF WaveTrend composite — inlined from wt_composite.py.
        Produces wt_bull_alignment, wt_composite_bias, wt_hh_count, etc."""
        try:
            tfs = ["3m", "15m", "1h", "4h", "D"]
            _sf = lambda v, d=0.0: float(v) if v is not None else d
            n_tfs = len(tfs)
            bull_count = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt1_{tf}")) > _sf(symbol_data.get(f"wt2_{tf}")))
            symbol_data["wt_bull_alignment"] = bull_count
            symbol_data["wt_bear_alignment"] = n_tfs - bull_count
            htf_tfs = [tf for tf in tfs if tf not in ("3m", "5m")]
            hl_count = sum(1 for tf in htf_tfs if symbol_data.get(f"wt_trough_structure_{tf}") == "HL")
            lh_count = sum(1 for tf in htf_tfs if symbol_data.get(f"wt_peak_structure_{tf}") == "LH")
            ll_count = sum(1 for tf in htf_tfs if symbol_data.get(f"wt_trough_structure_{tf}") == "LL")
            hh_count = sum(1 for tf in htf_tfs if symbol_data.get(f"wt_peak_structure_{tf}") == "HH")
            symbol_data["wt_hl_count"] = hl_count
            symbol_data["wt_lh_count"] = lh_count
            symbol_data["wt_ll_count"] = ll_count
            symbol_data["wt_hh_count"] = hh_count
            symbol_data["wt_any_bull_div"] = False
            symbol_data["wt_any_bear_div"] = False
            symbol_data["wt_strongest_bull_div_tf"] = None
            symbol_data["wt_strongest_bear_div_tf"] = None
            for tf in tfs:
                div = symbol_data.get(f"wt_divergence_{tf}")
                if div in ("BULL", "HIDDEN_BULL"):
                    symbol_data["wt_any_bull_div"] = True
                    symbol_data["wt_strongest_bull_div_tf"] = tf
                if div in ("BEAR", "HIDDEN_BEAR"):
                    symbol_data["wt_any_bear_div"] = True
                    symbol_data["wt_strongest_bear_div_tf"] = tf
            states = {tf: (symbol_data.get(f"wt_momentum_state_{tf}") or "") for tf in tfs}
            symbol_data["wt_momentum_narrative"] = states
            rising_crosses = sum(1 for tf in tfs if symbol_data.get(f"wt_cross_rising_{tf}") is True)
            falling_crosses = sum(1 for tf in tfs if symbol_data.get(f"wt_cross_rising_{tf}") is False)
            symbol_data["wt_rising_cross_count"] = rising_crosses
            symbol_data["wt_falling_cross_count"] = falling_crosses
            os_count = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt_percentile_{tf}"), 50) < 20)
            ob_count = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt_percentile_{tf}"), 50) > 80)
            symbol_data["wt_oversold_tf_count"] = os_count
            symbol_data["wt_overbought_tf_count"] = ob_count
            vel_up = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt_velocity_{tf}")) > 0)
            vel_down = sum(1 for tf in tfs if _sf(symbol_data.get(f"wt_velocity_{tf}")) < 0)
            symbol_data["wt_velocity_up_count"] = vel_up
            symbol_data["wt_velocity_down_count"] = vel_down
            bull_cross_count = sum(1 for tf in tfs if symbol_data.get(f"wt_cross_{tf}") == "BULL")
            bear_cross_count = sum(1 for tf in tfs if symbol_data.get(f"wt_cross_{tf}") == "BEAR")
            symbol_data["wt_bull_cross_count"] = bull_cross_count
            symbol_data["wt_bear_cross_count"] = bear_cross_count
            _tw = {"D": 5, "4h": 4, "1h": 3, "15m": 2, "3m": 1, "5m": 1}
            long_score, short_score = 0.0, 0.0
            long_score += (bull_count - n_tfs / 2.0) * 10
            short_score += ((n_tfs - bull_count) - n_tfs / 2.0) * 10
            long_score += hl_count * 5 - lh_count * 5 + hh_count * 3 - ll_count * 3
            short_score += lh_count * 5 - hl_count * 5 + ll_count * 3 - hh_count * 3
            if symbol_data["wt_any_bull_div"]: long_score += 20
            if symbol_data["wt_any_bear_div"]: short_score += 20
            long_score += os_count * 5 - ob_count * 3
            short_score += ob_count * 5 - os_count * 3
            long_score += rising_crosses * 3
            short_score += falling_crosses * 3
            long_score += bull_cross_count * 3 - bear_cross_count * 2
            short_score += bear_cross_count * 3 - bull_cross_count * 2
            long_score += vel_up * 2 - vel_down
            short_score += vel_down * 2 - vel_up
            for tf in tfs:
                w = _tw.get(tf, 1)
                st = states.get(tf, "")
                if st == "EXHAUST_DOWN": long_score += w
                elif st == "IMPULSE_UP": long_score += w * 0.7
                elif st == "EXHAUST_UP": short_score += w
                elif st == "IMPULSE_DOWN": short_score += w * 0.7
            symbol_data["wt_composite_long"] = max(-100.0, min(100.0, long_score))
            symbol_data["wt_composite_short"] = max(-100.0, min(100.0, short_score))
            symbol_data["wt_composite_bias"] = "LONG" if long_score > short_score + 10 else ("SHORT" if short_score > long_score + 10 else "NEUTRAL")
            symbol_data["wt_composite_delta"] = round(long_score - short_score, 2)
        except Exception:
            pass

    _nullable_keywords = {'divergence', 'wt_cross', 'wt_peak', 'wt_trough', 'bar_pattern', 'wt_strongest', 'wt_any', 'wt_wave_phase', 'wt_extreme'}
    def _flag_errors(self, symbol_data: Dict[str, Any]) -> None:
        issues: Dict[str, str] = {}
        for key, value in symbol_data.items():
            if key.startswith("_"):
                continue
            if value is None:
                if not any(nk in key for nk in self._nullable_keywords):
                    issues[key] = "missing"
                continue
            if isinstance(value, str):
                if value == "ERROR":
                    issues[key] = "error"
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value == 0.0 and not any(marker in key for marker in self.zero_allowed_keywords) and 'bb_pct' not in key and 'dc_position' not in key:
                    issues[key] = "zero"
        if issues:
            symbol_data["_calculation_errors"] = issues
        else:
            symbol_data.pop("_calculation_errors", None)

    def _timeframe_result_valid(self, timeframe: str, result: Dict[str, Any]) -> bool:
        required = self.required_by_timeframe.get(timeframe, [])
        if not required:
            return True
        for key in required:
            value = result.get(key)
            if value is None or value == "ERROR":
                return False
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0.0:
                if not any(marker in key for marker in self.zero_allowed_keywords):
                    return False
        return True

    def _missing_required_keys(self, values: Dict[str, Any]) -> List[str]:
        missing: List[str] = []
        for key in self.required_indicators:
            if key not in values or values[key] is None:
                missing.append(key)
        return missing

    def _is_noncritical_key(self, key: str) -> bool:
        return key.startswith("0") or key.startswith("zconviction")

    def _fallback_value(self, symbol: str, key: str, values: Dict[str, Any]) -> Optional[Any]:
        if key in ("current_price", "prev_price"):
            price = values.get("close") or values.get("open")
            if price is not None:
                return float(price)
            return 0.0
        if key.endswith("_prev") and key[:-5] in values and isinstance(values[key[:-5]], (int, float)):
            return float(values[key[:-5]])
        if key.startswith("stoch_k_D") or key.startswith("stoch_d_D"):
            fallback_key = key.replace("_D", "_4h")
            if fallback_key in values:
                return values.get(fallback_key)
        if key.startswith("dc_") and key.endswith("_D"):
            map_from = key.replace("_D", "_4h")
            if map_from in values:
                return values.get(map_from)
        if key.startswith("dc_") and key.endswith("_D_ant"):
            map_from = key.replace("_D", "_4h")
            if map_from in values:
                return values.get(map_from)
        if key.startswith("atr_D"):
            return values.get("atr_4h")
        if key.startswith("sma_200_D"):
            map_from = key.replace("_D", "_4h")
            if map_from in values:
                return values.get(map_from)
        if key == "timestamp_D":
            return values.get("timestamp_4h")
        return None
        
    async def _refresh_sentiment(self) -> None:
        """
        Calculates Global & Local sentiment with:
        1. LIQUIDITY WEIGHTING: Large caps get more weight on volume moves (Log scale).
        2. RELATIVE VOLUME: Uses ratios (1.5x avg) instead of raw volume to prevent pinning.
        3. DYNAMIC NORMALIZATION: Scales everything relative to the market leader.
        """
        raw_entries: List[Dict[str, Any]] = []
        local_sum = 0.0
        local_count = 0
        local_max_abs = 0.0

        # --- PASS 1: Calculate Raw Unbounded Scores ---
        for symbol, values in self.data.items():
            if not isinstance(values, dict):
                continue
            
            # Helper to safely get float
            def g(k, default=0.0):
                v = values.get(k)
                if v is None: return default
                try: return float(v)
                except Exception: return default

            current_price = g("current_price", 0.0)
            
            # --- A. TREND COMPONENT (Price Action) ---
            # WaveTrend
            wt_diff_3m = g("wt1_3m") - g("wt2_3m")
            wt_diff_15m = g("wt1_15m") - g("wt2_15m")
            wt_diff_1h = g("wt1_1h") - g("wt2_1h")
            wt_diff_4h = g("wt1_4h") - g("wt2_4h")
            
            wt_score = (wt_diff_3m * 1.0) + (wt_diff_15m * 1.5) + (wt_diff_1h * 2.0) + (wt_diff_4h * 2.5)

            # Hull Trend
            hull_score = 0.0
            if values.get("t_up_3m") is True: hull_score += 10.0
            elif values.get("t_up_3m") is False: hull_score -= 10.0
            if values.get("t_up_15m") is True: hull_score += 15.0
            elif values.get("t_up_15m") is False: hull_score -= 15.0
            if values.get("t_up_1h") is True: hull_score += 25.0
            elif values.get("t_up_1h") is False: hull_score -= 25.0

            # SMA Distances
            sma_score = 0.0
            sma_1m = g("sma_200_1m")
            if current_price > 0 and sma_1m > 0:
                dist_pct = (current_price - sma_1m) / sma_1m
                sma_score = dist_pct * 2000.0 

            # --- B. MOMENTUM ---
            # Stoch RSI
            k3, d3 = g("stoch_k_3m", 50), g("stoch_d_3m", 50)
            k15, d15 = g("stoch_k_15m", 50), g("stoch_d_15m", 50)
            stoch_mix = ((k3 - d3) * 1.0) + ((k15 - d15) * 1.5)
            
            # Heikin Ashi
            ha_score = 0.0
            ha_colors = [values.get(f"ha_{tf}") for tf in ["3m", "15m", "1h", "4h"]]
            ha_score += ha_colors.count("green") * 5.0
            ha_score -= ha_colors.count("red") * 5.0

            # --- C. VOLATILITY & VOLUME (FIXED) ---
            # 1. Relative Volume (Ratio, not raw number)
            rv3 = g("relative_volume_3m", 1.0)
            rv15 = g("relative_volume_15m", 1.0)
            
            # Soft Cap: Limit impact of absurd memecoin volume spikes (e.g. 50x vol)
            # We cap the ratio at 3.0x for calculation purposes
            rv_input = min(3.0, (rv3 * 0.6 + rv15 * 0.4))
            
            # Base Vol Score: 1.0 (Normal) -> 0 pts. 2.0 (High) -> 20 pts.
            vol_base = (rv_input - 1.0) * 20.0 

            # 2. Liquidity Weighting (Large Caps get more attention)
            # Estimate Dollar Volume of the current bar to gauge significance
            # 'volume' key usually holds the volume of the last closed candle or current tick
            vol_unit = g("volume", 0.0) 
            if vol_unit <= 0: vol_unit = g("volume_15m", 0.0) # Fallback
            
            liquidity_mult = 1.0
            if current_price > 0 and vol_unit > 0:
                usd_vol = vol_unit * current_price
                # Log Scale: 
                # $10k vol -> log10=4
                # $10M vol -> log10=7
                # BTC ($100M+) -> log10=8+
                try:
                    log_val = math.log10(usd_vol)
                    # Normalize: Center around 6.0 ($1M volume). 
                    # Range: ~0.8 (Small) to ~1.4 (Whale)
                    liquidity_mult = 1.0 + ((log_val - 6.0) * 0.15)
                    liquidity_mult = max(0.5, min(2.0, liquidity_mult)) # Safety Clamp
                except Exception: pass

            # Directional Bias (Volume confirms direction)
            direction = 1.0 if values.get("ha_3m") == "green" else -1.0
            
            # Final Volume Score
            vol_score = direction * abs(vol_base) * liquidity_mult

            # Linear Regression (% Slope per bar)
            lr_score = 0.0
            if current_price > 0:
                pct_slope_3m = (g("lr_trend_3m") / current_price) * 100 
                pct_slope_15m = (g("lr_trend_15m") / current_price) * 100
                lr_score = (pct_slope_3m * 1000.0) + (pct_slope_15m * 1500.0)

            # --- TOTAL RAW SCORE ---
            raw_sum = wt_score + hull_score + sma_score + stoch_mix + ha_score + vol_score + lr_score
            
            # Add external score bias
            raw_sum += g("0final_score_norm") * 0.5 

            raw_entries.append({"symbol": symbol, "raw": raw_sum})
            
            local_sum += raw_sum
            local_count += 1
            if abs(raw_sum) > local_max_abs:
                local_max_abs = abs(raw_sum)

        # --- INTERMISSION: REDIS AGGREGATION ---
        global_sum = local_sum
        global_count = local_count
        global_max_abs = local_max_abs

        if self.redis_client:
            try:
                shard_key = f"sentiment_shard_v2:{self.instance_id}"
                payload = json.dumps({
                    "sum": local_sum,
                    "count": local_count,
                    "max_abs": local_max_abs,
                    "ts": time.time()
                })
                await self.redis_client.setex(shard_key, 60, payload)

                shard_keys = [f"sentiment_shard_v2:{i}" for i in range(self.total_instances)]
                results = await self.redis_client.mget(shard_keys)
                
                global_sum = 0.0
                global_count = 0
                global_max_abs = 0.0

                for res in results:
                    if res:
                        try:
                            d = json.loads(res)
                            if time.time() - d.get("ts", 0) < 90:
                                global_sum += float(d.get("sum", 0))
                                global_count += int(d.get("count", 0))
                                shard_max = float(d.get("max_abs", 0))
                                if shard_max > global_max_abs:
                                    global_max_abs = shard_max
                        except Exception: pass
            except Exception as e:
                logger.warning(f"Sentiment aggregation failed: {e}")
                global_sum = local_sum
                global_count = local_count
                global_max_abs = local_max_abs

        # Calculate Globals
        if global_max_abs < 50.0: global_max_abs = 50.0  # Floor at 50 — raw scores routinely hit 50-100+. Floor of 1.0 caused 7000+ sentiment values
        
        raw_global_avg = (global_sum / global_count) if global_count > 0 else 0.0
        global_score_normalized = (raw_global_avg / global_max_abs) * 100.0

        # --- PASS 2: Normalization & Velocity Signaling ---
        ranking_list = []
        
        for entry in raw_entries:
            symbol = entry["symbol"]
            raw_val = entry["raw"]
            values = self.data[symbol]

            # 1. Normalize against the loudest symbol in the market
            norm_val = (raw_val / global_max_abs) * 100.0
            
            values["0market_sentiment_local"] = norm_val
            values["0market_sentiment_score"] = global_score_normalized
            values["0sentiment_strength"] = abs(norm_val)
            values["0sentiment_classification"] = self._classify_sentiment(norm_val)
            
            # 2. Calculate Divergence & Velocity
            current_divergence = norm_val - global_score_normalized
            prev_divergence = values.get("_prev_divergence", current_divergence)
            velocity = current_divergence - prev_divergence
            values["_prev_divergence"] = current_divergence
            
            # Explicitly store velocity for strategies to use
            values["velocity"] = velocity 

            # 3. Generate Signals (Momentum Divergence)
            DIV_THRESHOLD = 15.0
            VEL_THRESHOLD = 2.0  

            signal_payload = None

            # AUGMENT LONG: Accelerating UP away from average
            if current_divergence > DIV_THRESHOLD and velocity > VEL_THRESHOLD:
                signal_payload = { "action": "AUGMENT_LONG", "strength": min(100, (current_divergence * 0.5) + (velocity * 5.0)) }
            
            # REDUCE LONG: Decelerating/Reverting
            elif current_divergence > DIV_THRESHOLD and velocity < -VEL_THRESHOLD:
                signal_payload = { "action": "REDUCE_LONG", "strength": min(100, abs(velocity * 5.0)) }

            # AUGMENT SHORT: Accelerating DOWN away from average
            elif current_divergence < -DIV_THRESHOLD and velocity < -VEL_THRESHOLD:
                signal_payload = { "action": "AUGMENT_SHORT", "strength": min(100, (abs(current_divergence) * 0.5) + (abs(velocity) * 5.0)) }

            # REDUCE SHORT: Decelerating/Reverting
            elif current_divergence < -DIV_THRESHOLD and velocity > VEL_THRESHOLD:
                signal_payload = { "action": "REDUCE_SHORT", "strength": min(100, velocity * 5.0) }

            if signal_payload and self.redis_client:
                signal_payload.update({
                    "event_type": "SENTIMENT_MOMENTUM",
                    "symbol": symbol,
                    "price": values.get("current_price"),
                    "local_score": round(norm_val, 2),
                    "global_score": round(global_score_normalized, 2),
                    "timestamp": isoformat(utc_now())
                })
                try:
                    await self.redis_client.publish(self._signal_channel, json.dumps(signal_payload))
                except Exception: pass

            ranking_list.append((symbol, norm_val))
            self._compute_conviction(values)
            # wt_composite now runs in _inject_wt_composite() during indicator computation — no need here

        # --- 3. Update EMA and Ranks ---
        prev_ema = None
        first_sym_data = next(iter(self.data.values()), {}) if self.data else {}
        if isinstance(first_sym_data, dict):
            p = first_sym_data.get("0market_sentiment_score_ema")
            if p is not None: prev_ema = float(p)
            
        if prev_ema is None:
            ema_val = global_score_normalized
        else:
            alpha = 2.0 / 51.0
            ema_val = (global_score_normalized - prev_ema) * alpha + prev_ema

        sorted_entries = sorted(ranking_list, key=lambda x: x[1], reverse=True)
        top_symbols = [s for s, _ in sorted_entries[:40]]
        bottom_symbols = [s for s, _ in sorted_entries[-40:]]
        top_flag = {s for s, _ in sorted_entries[:10]}
        bottom_flag = {s for s, _ in sorted_entries[-10:]}
        
        ranking_points = {}
        total_symbols = len(sorted_entries)

        for rank, (symbol, val) in enumerate(sorted_entries, start=1):
            values = self.data.get(symbol, {})
            values["0market_sentiment_score_ema"] = ema_val
            values["0sentiment_rank"] = rank
            values["0is_top_sentiment"] = symbol in top_flag
            values["0is_bottom_sentiment"] = symbol in bottom_flag
            
            if total_symbols > 1:
                points = max(0.0, 100.0 - ((rank - 1) / (total_symbols - 1)) * 100.0)
            else:
                points = 100.0
            
            values["0ranking_points"] = round(points, 4)
            values["0ranking_points_global"] = round(points, 4)
            ranking_points[symbol] = points

        self.ranking_scores = dict(sorted(ranking_points.items(), key=lambda kv: kv[1], reverse=True))
        self._scores_dirty = True

        try:
            async def _write_list(path, data_list):
                if not path.parent.exists(): path.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(path, "w") as f:
                    await f.write(json.dumps(data_list, indent=2))
            
            await _write_list(self.sentiment_top_file, top_symbols)
            await _write_list(self.sentiment_bottom_file, bottom_symbols)
        except Exception: pass
    #
    # def _refresh_sentiment(self) -> None:
    #     entries: List[Tuple[str, float]] = []
    #     def f(data: Dict[str, Any], key: str, default: Optional[float] = None) -> Optional[float]:
    #         value = data.get(key)
    #         if value is None:
    #             return default
    #         try:
    #             return float(value)
    #         except (TypeError, ValueError):
    #             return default
    #     for symbol, values in self.data.items():
    #         if not isinstance(values, dict):
    #             continue
    #         values.pop("metadata", None)
    #         wt_long = [f(values, "wt1_3m"), f(values, "wt1_15m"), f(values, "wt1_1h") , f(values, "wt1_4h")]
    #         wt_short = [f(values, "wt2_3m"), f(values, "wt2_15m"), f(values, "wt2_1h"), f(values, "wt2_4h")]
    #         wt_score = sum((a - b) for a, b in zip(wt_long, wt_short) if a is not None and b is not None)
    #         ha_score = 0.0
    #         for key in ("ha_3m", "ha_15m", "ha_1h", "ha_4h", "ha_D"):
    #             state = values.get(key)
    #             if state == "green":
    #                 ha_score += 10.0
    #             elif state == "red":
    #                 ha_score -= 10.0
    #         stoch_k_3m = f(values, "stoch_k_3m", 50.0)
    #         stoch_d_3m = f(values, "stoch_d_3m", 50.0)
    #         stoch_k_15m = f(values, "stoch_k_15m", 50.0)
    #         stoch_d_15m = f(values, "stoch_d_15m", 50.0)
    #         stoch_score = 0.0
    #         if stoch_k_3m is not None and stoch_d_3m is not None:
    #             stoch_score += max(min(stoch_k_3m - stoch_d_3m, 20.0), -20.0)
    #         if stoch_k_15m is not None and stoch_d_15m is not None:
    #             stoch_score += max(min(stoch_k_15m - stoch_d_15m, 20.0), -20.0)
    #         price = f(values, "current_price")
    #         sma_1m = f(values, "sma_200_1m")
    #         sma_15m = f(values, "sma_200_15m")
    #         sma_score = 0.0
    #         if price is not None and sma_1m is not None:
    #             sma_score += 10.0 if price > sma_1m else -10.0
    #         if price is not None and sma_15m is not None:
    #             sma_score += 10.0 if price > sma_15m else -10.0
    #         rel_vol_3m = f(values, "relative_volume_3m")
    #         rel_vol_15m = f(values, "relative_volume_15m")
    #         vol_score = 0.0
    #         if rel_vol_3m is not None:
    #             vol_score += (rel_vol_3m - 1.0) * 15.0
    #         if rel_vol_15m is not None:
    #             vol_score += (rel_vol_15m - 1.0) * 10.0
    #         lr_3m = f(values, "lr_trend_3m", 0.0)
    #         lr_15m = f(values, "lr_trend_15m", 0.0)
    #         lr_score = (lr_3m or 0.0) * 40.0 + (lr_15m or 0.0) * 30.0
    #         local_sentiment = wt_score * 3.0 + ha_score + stoch_score + sma_score + vol_score + lr_score
    #         final_component = self.final_scores.get(symbol, 0.0)
    #         local_sentiment += final_component * 0.1
    #         local_sentiment = max(-100.0, min(100.0, local_sentiment))
    #         classification = self._classify_sentiment(local_sentiment)
    #         values["0market_sentiment_local"] = local_sentiment
    #         values["0sentiment_strength"] = abs(local_sentiment)
    #         values["0sentiment_classification"] = classification
    #         entries.append((symbol, local_sentiment))
    #         self._compute_conviction(values)
    #     if not entries:
    #         return
    #
    #     # Calculate Global Score
    #     global_score = float(sum(score for _, score in entries) / len(entries))
    #
    #     # --- 50 EMA Calculation Start ---
    #     prev_ema = None
    #     # Try to recover the previous EMA from the data (since it's global, any symbol will do)
    #     for val_dict in self.data.values():
    #         if isinstance(val_dict, dict) and "0market_sentiment_score_ema" in val_dict:
    #             try:
    #                 p = float(val_dict["0market_sentiment_score_ema"])
    #                 if not math.isnan(p):
    #                     prev_ema = p
    #                     break
    #             except (ValueError, TypeError):
    #                 continue
    #
    #     if prev_ema is not None:
    #         # EMA formula: (Close - PrevEMA) * multiplier + PrevEMA
    #         # Multiplier for 50 periods = 2 / (50 + 1) = ~0.0392
    #         alpha = 2.0 / 51.0
    #         ema_val = (global_score - prev_ema) * alpha + prev_ema
    #     else:
    #         # Initialization
    #         ema_val = global_score
    #     # --- 50 EMA Calculation End ---
    #
    #     sorted_entries = sorted(entries, key=lambda x: x[1], reverse=True)
    #     top_symbols = [symbol for symbol, _ in sorted_entries[:40]]
    #     bottom_symbols = [symbol for symbol, _ in sorted_entries[-40:]]
    #     top_flag = {symbol for symbol, _ in sorted_entries[:10]}
    #     bottom_flag = {symbol for symbol, _ in sorted_entries[-10:]}
    #     total_symbols = len(sorted_entries)
    #     ranking_points: Dict[str, float] = {}
    #     global_range = self.score_ranges.setdefault("GLOBAL", {"raw_min": global_score, "raw_max": global_score})
    #     global_range["raw_min"] = min(global_range.get("raw_min", global_score), global_score)
    #     global_range["raw_max"] = max(global_range.get("raw_max", global_score), global_score)
    #
    #     for rank, (symbol, local) in enumerate(sorted_entries, start=1):
    #         values = self.data.get(symbol, {})
    #         if not isinstance(values, dict):
    #             continue
    #         values["0market_sentiment_score"] = global_score
    #         values["0market_sentiment_score_ema"] = ema_val # <--- New Field
    #         values["0sentiment_rank"] = rank
    #         values["0is_top_sentiment"] = symbol in top_flag
    #         values["0is_bottom_sentiment"] = symbol in bottom_flag
    #         if total_symbols > 1:
    #             points = max(0.0, 100.0 - ((rank - 1) / (total_symbols - 1)) * 100.0)
    #         else:
    #             points = 100.0
    #         points = round(points, 4)
    #         values["0ranking_points"] = points
    #         values["0ranking_points_global"] = points
    #         ranking_points[symbol] = points
    #         ranges = self.score_ranges.setdefault(symbol, {"raw_min": local, "raw_max": local})
    #         ranges["raw_min"] = min(ranges.get("raw_min", local), local)
    #         ranges["raw_max"] = max(ranges.get("raw_max", local), local)
    #     self.ranking_scores = dict(sorted(ranking_points.items(), key=lambda kv: kv[1], reverse=True))
    #     self._scores_dirty = True
    #     self._score_ranges_dirty = True
    #     try:
    #         ensure_path_writable_sync(self.sentiment_top_file)
    #         ensure_path_writable_sync(self.sentiment_bottom_file)
    #         with open(self.sentiment_top_file, "w") as f:
    #             json.dump(top_symbols, f, indent=2)
    #         with open(self.sentiment_bottom_file, "w") as f:
    #             json.dump(bottom_symbols, f, indent=2)
    #     except Exception:
    #         pass
    #

    def _classify_sentiment(self, value: float) -> str:
        if value > 75:
            return "EXTREME_BULLISH"
        if value > 50:
            return "STRONG_BULLISH"
        if value > 25:
            return "MODERATE_BULLISH"
        if value > -25:
            return "NEUTRAL"
        if value > -50:
            return "MODERATE_BEARISH"
        if value > -75:
            return "STRONG_BEARISH"
        return "EXTREME_BEARISH"

    def _compute_conviction(self, values: Dict[str, Any]) -> None:
        def flag(condition: bool, weight: float, reason: str, bucket: List[str]) -> float:
            if condition:
                bucket.append(reason)
                return weight
            return 0.0
        long_reasons: List[str] = []
        short_reasons: List[str] = []
        price = values.get("current_price")
        wt1_3m = values.get("wt1_3m")
        wt2_3m = values.get("wt2_3m")
        wt1_15m = values.get("wt1_15m")
        wt2_15m = values.get("wt2_15m")
        wt1_1h = values.get("wt1_1h")
        wt2_1h = values.get("wt2_1h")
        sma_1m = values.get("sma_200_1m")
        sma_15m = values.get("sma_200_15m")
        stoch_cross = values.get("stoch_crossover_3m") or values.get("stoch_crossover_15m")
        stoch_cross_under = values.get("stoch_crossunder_3m") or values.get("stoch_crossunder_15m")
        dc_breakout = values.get("dc_high_crossover_3m") or values.get("dc_basis_crossover_3m")
        dc_breakdown = values.get("dc_low_crossunder_3m") or values.get("dc_basis_crossunder_3m")
        wt_long = flag(wt1_3m is not None and wt2_3m is not None and wt1_3m > wt2_3m and wt1_15m is not None and wt2_15m is not None and wt1_15m > wt2_15m, 20.0, "WT_TREND", long_reasons)
        wt_short = flag(wt1_3m is not None and wt2_3m is not None and wt1_3m < wt2_3m and wt1_15m is not None and wt2_15m is not None and wt1_15m < wt2_15m, 20.0, "WT_TREND", short_reasons)
        ha_long = flag(values.get("ha_3m") == "green" and values.get("ha_15m") == "green", 15.0, "HA", long_reasons)
        ha_short = flag(values.get("ha_3m") == "red" and values.get("ha_15m") == "red", 15.0, "HA", short_reasons)
        sma_long = flag(price is not None and sma_1m is not None and sma_15m is not None and price > sma_1m and price > sma_15m, 15.0, "SMA_ABOVE", long_reasons)
        sma_short = flag(price is not None and sma_1m is not None and sma_15m is not None and price < sma_1m and price < sma_15m, 15.0, "SMA_BELOW", short_reasons)
        stoch_long = flag(bool(stoch_cross), 10.0, "STOCH_X", long_reasons)
        stoch_short = flag(bool(stoch_cross_under), 10.0, "STOCH_X", short_reasons)
        dc_long = flag(bool(dc_breakout), 15.0, "DC_BRK", long_reasons)
        dc_short = flag(bool(dc_breakdown), 15.0, "DC_BRK", short_reasons)
        wt_multi_long = flag(wt1_1h is not None and wt2_1h is not None and wt1_1h > wt2_1h, 10.0, "WT_1H", long_reasons)
        wt_multi_short = flag(wt1_1h is not None and wt2_1h is not None and wt1_1h < wt2_1h, 10.0, "WT_1H", short_reasons)
        lr_3m = values.get("lr_trend_3m") or 0.0
        lr_15m = values.get("lr_trend_15m") or 0.0
        lr_long = flag(lr_3m > 0 and lr_15m > 0, 10.0, "LR_UP", long_reasons)
        lr_short = flag(lr_3m < 0 and lr_15m < 0, 10.0, "LR_DOWN", short_reasons)
        rel_vol_3m = values.get("relative_volume_3m") or 0.0
        rel_vol_15m = values.get("relative_volume_15m") or 0.0
        vol_long = flag(rel_vol_3m > 1.1 or rel_vol_15m > 1.1, 5.0, "VOL", long_reasons)
        vol_short = flag(rel_vol_3m < 0.9 or rel_vol_15m < 0.9, 5.0, "VOL", short_reasons)
        sentiment_local = values.get("0market_sentiment_local")
        sent_long = 0.0
        sent_short = 0.0
        if isinstance(sentiment_local, (int, float)):
            if sentiment_local > 5.0:
                sent_long += flag(True, min(20.0, float(sentiment_local) / 4.0), "SENT", long_reasons)
            if sentiment_local < -5.0:
                sent_short += flag(True, min(20.0, abs(float(sentiment_local)) / 4.0), "SENT", short_reasons)
        long_score = wt_long + ha_long + sma_long + stoch_long + dc_long + wt_multi_long + lr_long + vol_long + sent_long
        short_score = wt_short + ha_short + sma_short + stoch_short + dc_short + wt_multi_short + lr_short + vol_short + sent_short
        values["zconviction_augment_long"] = round(min(95.0, long_score), 2)
        values["zconviction_augment_short"] = round(min(95.0, short_score), 2)
        values["zconviction_reasons_augment_long"] = long_reasons
        values["zconviction_reasons_augment_short"] = short_reasons

    def _snapshot_signals(self, symbol_data: Dict[str, Any]) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {}
        if not isinstance(symbol_data, dict):
            return snapshot
        for tf in TIMEFRAMES.keys():
            wt_key = f"wt_signal_{tf}"
            if wt_key in symbol_data:
                snapshot[wt_key] = symbol_data.get(wt_key)
            ha_key = f"ha_{tf}"
            if ha_key in symbol_data:
                snapshot[ha_key] = symbol_data.get(ha_key)
        for key, value in symbol_data.items():
            if any(key.startswith(prefix) for prefix in ("stoch_crossover_", "stoch_crossunder_", "sma_crossover_", "sma_crossunder_", "dc_high_crossover_", "dc_high_crossunder_", "dc_low_crossover_", "dc_low_crossunder_", "dc_basis_crossover_", "dc_basis_crossunder_", "tco_", "tcu_")):
                snapshot[key] = value
        for key in ("t_up_3m", "t_up_15m", "current_price"):
            if key in symbol_data:
                snapshot[key] = symbol_data.get(key)
        return snapshot

    async def _emit_signals(self, symbol: str) -> None:
        #return
        if not self.redis_client:
            logger.debug(f"[SIGNAL] Skipping signal emission for {symbol}: redis_client not available")
        if self.env == "gateway":
            logger.debug(f"[SIGNAL] Skipping signal emission for {symbol}: running on gateway")
            return
        symbol_data = self.data.get(symbol)
        if not isinstance(symbol_data, dict):
            return
        snapshot = self._snapshot_signals(symbol_data)
        prev_snapshot = self._previous_indicators.get(symbol, {})
        now = utc_now()
        price = symbol_data.get("current_price")
        signals: List[Dict[str, Any]] = []

        for tf in TIMEFRAMES.keys():
            key = f"wt_signal_{tf}"
            curr = snapshot.get(key)
            prev_val = prev_snapshot.get(key)
            if curr in {"BUY", "SELL"} and curr != prev_val:
                signals.append({"event_type": key, "symbol": symbol, "timeframe": tf, "action": curr, "signal": curr, "price": price})

        bool_actions = {
            "stoch_crossover_": "BUY",
            "stoch_crossunder_": "SELL",
            "sma_crossover_": "BUY",
            "sma_crossunder_": "SELL",
            "dc_high_crossover_": "BUY",
            "dc_high_crossunder_": "SELL",
            "dc_low_crossover_": "BUY",
            "dc_low_crossunder_": "SELL",
            "dc_basis_crossover_": "BUY",
            "dc_basis_crossunder_": "SELL",
            "tco_": "BUY",
            "tcu_": "SELL"
        }
        for prefix, action in bool_actions.items():
            for key, value in snapshot.items():
                if not key.startswith(prefix):
                    continue
                timeframe_hint = key[len(prefix):]
                if prefix in ("stoch_crossover_", "stoch_crossunder_") and timeframe_hint == "3m":
                    continue
                curr = bool(value)
                prev_val = bool(prev_snapshot.get(key))
                if curr and not prev_val:
                    signals.append({"event_type": key, "symbol": symbol, "timeframe": timeframe_hint, "action": action, "signal_type": prefix.rstrip("_"), "price": price})

        # for tf in ("3m", "15m", "1h", "4h", "D"):
        #     key = f"ha_{tf}"
        #     curr = snapshot.get(key)
        #     prev_val = prev_snapshot.get(key)
        #     if curr != prev_val and curr in {"green", "red"}:
        #         action = "BUY" if curr == "green" else "SELL"
        #         signals.append({"event_type": f"{key}_{curr}", "symbol": symbol, "timeframe": tf, "action": action, "signal_type": key, "state": curr, "price": price})

        if not signals:
            self._previous_indicators[symbol] = snapshot
            return

        emission_time = isoformat(now)
        filtered: List[Dict[str, Any]] = []
        for signal in signals:
            event_key = (symbol, signal.get("event_type"))
            last = self._event_cooldown.get(event_key)
            timeframe = signal.get("timeframe", "3m")
            # Calculate cooldown until next bar close based on when signal fires
            if timeframe == "3m":
                seconds_into_bar = (now.minute % 3) * 60 + now.second + (now.microsecond / 1000000.0)
                cooldown_seconds = 180 - seconds_into_bar
            elif timeframe == "15m":
                seconds_into_bar = (now.minute % 15) * 60 + now.second + (now.microsecond / 1000000.0)
                cooldown_seconds = 900 - seconds_into_bar
            elif timeframe == "1h":
                seconds_into_bar = now.minute * 60 + now.second + (now.microsecond / 1000000.0)
                cooldown_seconds = 3600 - seconds_into_bar
            elif timeframe == "4h":
                hours_into_bar = now.hour % 4
                seconds_into_bar = hours_into_bar * 3600 + now.minute * 60 + now.second + (now.microsecond / 1000000.0)
                cooldown_seconds = 14400 - seconds_into_bar
            elif timeframe == "D":
                seconds_into_day = now.hour * 3600 + now.minute * 60 + now.second + (now.microsecond / 1000000.0)
                cooldown_seconds = 86400 - seconds_into_day
            else:
                seconds_into_bar = (now.minute % 3) * 60 + now.second + (now.microsecond / 1000000.0)
                cooldown_seconds = 180 - seconds_into_bar
            cooldown_seconds = max(int(cooldown_seconds), 60)  # Minimum 1 minute
            if last and (now - last).total_seconds() < cooldown_seconds:
                continue
            signal.setdefault("timestamp", emission_time)
            signal.setdefault("price", price)
            signal.setdefault("source", "ez_indicators")
            filtered.append(signal)
            self._event_cooldown[event_key] = now

        if not filtered:
            self._previous_indicators[symbol] = snapshot
            return

        for signal in filtered:
            try:
                await self.redis_client.publish(self._signal_channel, json.dumps(signal, default=str))
                logger.debug(f"[SIGNAL] Published {signal.get('event_type')} for {symbol} on {signal.get('timeframe')}: {signal.get('action')}")
            except Exception as e:
                logger.warning(f"[SIGNAL] Failed to publish signal for {symbol}: {e}")
        self._previous_indicators[symbol] = snapshot

    def _update_master_timestamp(self, symbol_data: Dict[str, Any]) -> None:
        latest = None
        # Prioritize 3m timestamp for overall freshness
        ts_3m_str = symbol_data.get("timestamp_3m")
        if ts_3m_str:
            try:
                if ts_3m_str.endswith("Z"): ts_3m_str = ts_3m_str.replace("Z", "+00:00")
                latest = datetime.fromisoformat(ts_3m_str)
            except Exception: pass

        for tf in TIMEFRAMES.keys():
            if tf == "3m": continue # Already checked
            ts_str = symbol_data.get(f"timestamp_{tf}")
            if not ts_str:
                continue
            try:
                if ts_str.endswith("Z"):
                    ts_str = ts_str.replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_str)
            except Exception:
                continue
            if latest is None or ts > latest:
                latest = ts
        if latest is not None:
            symbol_data["timestamp"] = isoformat(latest)
            # Update tick timestamps from the master timestamp
            symbol_data["_tick_ts"] = latest.timestamp()
            symbol_data["ts"] = symbol_data["_tick_ts"]
            # Also ensure timestamp_1m is set if we have a latest timestamp
            if "timestamp_1m" not in symbol_data:
                symbol_data["timestamp_1m"] = symbol_data["timestamp"]

async def main() -> None:
    global indicators_service_global, price_service_global
    logger.info("Initializing IndicatorOrchestrator...")
    orchestrator = IndicatorOrchestrator()
    indicators_service_global = orchestrator
    price_service_global = orchestrator.price_cache
    logger.info("Initializing PositionsServiceClient...")
    from ez_positions_service import PositionsServiceClient
    positions_client = PositionsServiceClient(
        host=str(getattr(config, "POSITIONS_RPC_HOST", "127.0.0.1")),
        port=int(getattr(config, "POSITIONS_RPC_PORT", 8765)), )
    logger.info("Starting orchestrator.run()...")
    try:
        await orchestrator.run()
    finally:
        try:
            await positions_client.shutdown_service("ez_indicators_shutdown")
        except Exception:
            pass
        indicators_service_global = None
        price_service_global = None

indicators_service_global: Optional[IndicatorOrchestrator] = None
price_service_global: Optional[PriceCacheManager] = None

def get_indicators_service() -> Optional[IndicatorOrchestrator]:
    """Get the global indicators service instance for direct access to indicators data."""
    return indicators_service_global

def get_price_service() -> Optional[PriceCacheManager]:
    """Get the global price service instance for direct access to price cache."""
    return price_service_global

async def bootstrap_indicators_service() -> IndicatorOrchestrator:
    """Bootstrap and return the indicators service instance."""
    global indicators_service_global, price_service_global
    if indicators_service_global is None:
        indicators_service_global = IndicatorOrchestrator()
        price_service_global = indicators_service_global.price_cache
    return indicators_service_global


# ============================================================================
# CONSENSUS INDICATORS (CRYPTO) — migrated from ez_indicators_extra.py 2026-04-26
# Crypto Consensus Indicators: VWAP, 9/21 EMA, Keltner, TTM Squeeze, Episodic
# Pivot, Clenow (ann_factor=365), Minervini SEPA, SMFI, Funding Z-score, OI
# velocity. RSI / ORB / lunch_dead_zone NOT migrated (RSI banned, RTH-specific).
# ============================================================================
def compute_vwap_from_bars(bars: List[Dict], reset_daily: bool = True, session_hours: int = 24) -> Dict[str, float]:
    """Crypto session-anchored VWAP + bands. Crypto session = 24h continuous."""
    if not bars or len(bars) < 2:
        return {}
    try:
        highs, lows, closes, volumes = [], [], [], []
        for b in bars:
            h = float(b.get('high') or b.get('h') or 0)
            l = float(b.get('low') or b.get('l') or 0)
            c = float(b.get('close') or b.get('c') or 0)
            v = float(b.get('volume') or b.get('v') or 0)
            if c <= 0 or v <= 0:
                continue
            highs.append(h)
            lows.append(l)
            closes.append(c)
            volumes.append(v)
        if len(closes) < 2:
            return {}
        tp = np.array([(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)])
        vol = np.array(volumes)
        cum_tp_vol = np.cumsum(tp * vol)
        cum_vol = np.cumsum(vol)
        vwap_arr = cum_tp_vol / np.maximum(cum_vol, 1e-10)
        vwap = float(vwap_arr[-1])
        if vwap <= 0:
            return {}
        sq_diff = np.cumsum(((tp - vwap_arr) ** 2) * vol)
        variance = sq_diff / np.maximum(cum_vol, 1e-10)
        std = float(np.sqrt(max(variance[-1], 0)))
        current_price = closes[-1]
        dist_pct = ((current_price - vwap) / vwap) * 100.0 if vwap > 0 else 0
        return {
            "vwap": round(vwap, 6),
            "vwap_upper1": round(vwap + std, 6),
            "vwap_lower1": round(vwap - std, 6),
            "vwap_upper2": round(vwap + 2 * std, 6),
            "vwap_lower2": round(vwap - 2 * std, 6),
            "vwap_distance_pct": round(dist_pct, 4),
            "vwap_session_hours": session_hours,
        }
    except Exception as e:
        logger.debug(f"[VWAP] Error: {e}")
        return {}


def compute_ema(values: List[float], period: int) -> float:
    """Compute EMA of the last `period` values. Returns latest EMA value."""
    if not values or len(values) < period:
        return 0.0
    mult = 2.0 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * mult + ema * (1.0 - mult)
    return ema


def compute_ema_9_21(closes: List[float]) -> Dict[str, float]:
    """Compute EMA 9 and EMA 21 from close prices."""
    if not closes or len(closes) < 21:
        return {}
    ema9 = compute_ema(closes, 9)
    ema21 = compute_ema(closes, 21)
    if ema21 <= 0:
        return {}
    dist = ((ema9 - ema21) / ema21) * 100.0
    return {
        "ema_9": round(ema9, 6),
        "ema_21": round(ema21, 6),
        "ema_9_above_21": 1.0 if ema9 > ema21 else 0.0,
        "ema_9_21_dist_pct": round(dist, 4),
    }


def compute_keltner_channels(closes: List[float], highs: List[float], lows: List[float], ema_period: int = 20, atr_period: int = 20, atr_mult: float = 1.5) -> Dict[str, float]:
    """Compute Keltner Channels (EMA ± mult * ATR)."""
    if not closes or len(closes) < max(ema_period, atr_period):
        return {}
    try:
        mid = compute_ema(closes, ema_period)
        trs = []
        for j in range(1, len(closes)):
            h = highs[j] if j < len(highs) else closes[j]
            l = lows[j] if j < len(lows) else closes[j]
            pc = closes[j - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        if len(trs) < atr_period:
            return {}
        atr = compute_ema(trs[-atr_period * 3:], atr_period) if len(trs) >= atr_period else sum(trs[-atr_period:]) / atr_period
        return {
            "kc_upper": round(mid + atr_mult * atr, 6),
            "kc_middle": round(mid, 6),
            "kc_lower": round(mid - atr_mult * atr, 6),
            "kc_atr": round(atr, 6),
        }
    except Exception:
        return {}


def detect_squeeze(bb_upper: float, bb_lower: float, kc_upper: float, kc_lower: float) -> Dict[str, Any]:
    """TTM Squeeze: BB inside KC = squeeze on."""
    if not all([bb_upper, bb_lower, kc_upper, kc_lower]):
        return {"squeeze_on": False}
    squeeze_on = bb_upper < kc_upper and bb_lower > kc_lower
    return {"squeeze_on": squeeze_on}


def detect_episodic_pivot(daily_bars: List[Dict], min_gap_pct: float = 5.0, min_vol_mult: float = 3.0, max_consolidation_days: int = 8, max_retrace_pct: float = 25.0) -> Dict[str, Any]:
    """Qullamaggie Episodic Pivot. Crypto perps: gaps rare on 24/7 markets — mostly inert."""
    if not daily_bars or len(daily_bars) < 10:
        return {"ep_detected": False}
    try:
        closes = [float(b.get('close') or b.get('c') or 0) for b in daily_bars]
        volumes = [float(b.get('volume') or b.get('v') or 0) for b in daily_bars]
        opens = [float(b.get('open') or b.get('o') or 0) for b in daily_bars]
        highs = [float(b.get('high') or b.get('h') or 0) for b in daily_bars]
        lows = [float(b.get('low') or b.get('l') or 0) for b in daily_bars]
        if any(c <= 0 for c in closes[-10:]):
            return {"ep_detected": False}
        avg_vol_20 = np.mean(volumes[-25:-5]) if len(volumes) >= 25 else np.mean(volumes[:-5]) if len(volumes) > 5 else 0
        if avg_vol_20 <= 0:
            return {"ep_detected": False}
        for gap_idx in range(-15, -2):
            if abs(gap_idx) >= len(closes):
                continue
            prev_close = closes[gap_idx - 1]
            gap_open = opens[gap_idx]
            gap_close = closes[gap_idx]
            gap_vol = volumes[gap_idx]
            if prev_close <= 0:
                continue
            gap_pct = ((gap_open - prev_close) / prev_close) * 100.0
            vol_mult = gap_vol / avg_vol_20 if avg_vol_20 > 0 else 0
            if abs(gap_pct) >= min_gap_pct and vol_mult >= min_vol_mult:
                is_bullish = gap_pct > 0
                gap_high = highs[gap_idx]
                post_bars = list(range(gap_idx + 1, 0)) if gap_idx < -1 else []
                if not post_bars or len(post_bars) < 2:
                    continue
                if len(post_bars) > max_consolidation_days:
                    post_bars = post_bars[:max_consolidation_days]
                post_highs = [highs[j] for j in post_bars]
                post_lows = [lows[j] for j in post_bars]
                post_closes = [closes[j] for j in post_bars]
                if is_bullish:
                    max_retrace = ((gap_high - min(post_lows)) / (gap_high - prev_close)) * 100.0 if (gap_high - prev_close) > 0 else 100
                    if max_retrace <= max_retrace_pct:
                        breakout_level = max(post_highs)
                        current_price = closes[-1]
                        if current_price >= breakout_level * 0.99:
                            return {
                                "ep_detected": True,
                                "ep_gap_pct": round(gap_pct, 2),
                                "ep_vol_mult": round(vol_mult, 2),
                                "ep_consolidation_days": len(post_bars),
                                "ep_breakout_level": round(breakout_level, 6),
                                "ep_direction": "LONG",
                                "ep_max_retrace_pct": round(max_retrace, 2),
                            }
                else:
                    gap_low = lows[gap_idx]
                    max_retrace = ((max(post_highs) - gap_low) / (prev_close - gap_low)) * 100.0 if (prev_close - gap_low) > 0 else 100
                    if max_retrace <= max_retrace_pct:
                        breakout_level = min(post_lows)
                        current_price = closes[-1]
                        if current_price <= breakout_level * 1.01:
                            return {
                                "ep_detected": True,
                                "ep_gap_pct": round(gap_pct, 2),
                                "ep_vol_mult": round(vol_mult, 2),
                                "ep_consolidation_days": len(post_bars),
                                "ep_breakout_level": round(breakout_level, 6),
                                "ep_direction": "SHORT",
                                "ep_max_retrace_pct": round(max_retrace, 2),
                            }
        return {"ep_detected": False}
    except Exception as e:
        logger.debug(f"[EPISODIC_PIVOT] Error: {e}")
        return {"ep_detected": False}


def compute_clenow_score(closes: List[float], lookback: int = 90, ann_factor: int = 365) -> Dict[str, float]:
    """Clenow exp-regression score. Crypto: ann_factor=365 (24/7)."""
    if len(closes) < lookback + 5:
        return {}
    try:
        arr = np.array(closes[-lookback:], dtype=np.float64)
        if np.any(arr <= 0):
            return {}
        y = np.log(arr)
        x = np.arange(lookback, dtype=np.float64)
        mx, my = np.mean(x), np.mean(y)
        xc = x - mx
        yc = y - my
        ssxx = np.dot(xc, xc)
        if ssxx == 0:
            return {}
        b = np.dot(xc, yc) / ssxx
        slope_ann = (np.exp(b * ann_factor) - 1) * 100
        ss_res = np.sum((yc - b * xc) ** 2)
        ss_tot = np.sum(yc ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0
        score = slope_ann * r2
        return {
            "clenow_slope": round(slope_ann, 2),
            "clenow_r2": round(r2, 4),
            "clenow_score": round(score, 2),
            "clenow_ann_factor": ann_factor,
        }
    except Exception:
        return {}


def compute_minervini_sepa(closes: List[float], highs: List[float], lows: List[float], volumes: List[float]) -> Dict[str, Any]:
    """Minervini SEPA template. 50/150/200/252 calendar-day windows for crypto."""
    n = len(closes)
    if n < 252:
        return {}
    try:
        c = np.array(closes)
        h = np.array(highs)
        v = np.array(volumes)
        sma50 = float(np.mean(c[-50:]))
        sma150 = float(np.mean(c[-150:])) if n >= 150 else float(np.mean(c))
        sma200 = float(np.mean(c[-200:])) if n >= 200 else float(np.mean(c))
        sma200_prev = float(np.mean(c[-222:-22])) if n >= 222 else sma200
        price = float(c[-1])
        high_52w = float(np.max(h[-252:]))
        low_52w = float(np.min(c[-252:]))
        avg_vol = float(np.mean(v[-20:])) if n >= 20 else 1
        cur_vol = float(v[-1])
        cond1 = price > sma150 and price > sma200
        cond2 = sma150 > sma200
        cond3 = sma200 > sma200_prev
        cond4 = price >= low_52w * 1.25 if low_52w > 0 else False
        cond5 = price >= high_52w * 0.75 if high_52w > 0 else False
        cond6 = price > sma50
        vol_surge = cur_vol > avg_vol * 1.5 if avg_vol > 0 else False
        score = sum([cond1, cond2, cond3, cond4, cond5, cond6])
        sepa_pass = score >= 5 and vol_surge
        return {
            "sepa_pass": sepa_pass,
            "sepa_score": score,
            "sepa_vol_surge": vol_surge,
            "sepa_price_vs_sma150": round((price / sma150 - 1) * 100, 2) if sma150 > 0 else 0,
            "sepa_pct_from_52w_high": round((price / high_52w - 1) * 100, 2) if high_52w > 0 else 0,
        }
    except Exception:
        return {}


def compute_smfi(opens: List[float], highs: List[float], lows: List[float], closes: List[float], period: int = 20) -> Dict[str, float]:
    """Smart Money Flow Index. Divergence = signal."""
    n = len(closes)
    if n < period + 5:
        return {}
    try:
        o = np.array(opens)
        h = np.array(highs)
        l = np.array(lows)
        c = np.array(closes)
        midrange = (h + l) / 2.0
        smfi = np.zeros(n)
        for i in range(1, n):
            smart = c[i] - midrange[i]
            retail = midrange[i] - o[i]
            smfi[i] = smfi[i - 1] + smart - retail
        smfi_sma = float(np.mean(smfi[-period:])) if n >= period else float(smfi[-1])
        price_sma = float(np.mean(c[-period:])) if n >= period else float(c[-1])
        smfi_val = float(smfi[-1])
        price_val = float(c[-1])
        bull_div = price_val < price_sma and smfi_val > smfi_sma
        bear_div = price_val > price_sma and smfi_val < smfi_sma
        return {
            "smfi": round(smfi_val, 4),
            "smfi_sma": round(smfi_sma, 4),
            "smfi_bull_divergence": bull_div,
            "smfi_bear_divergence": bear_div,
            "smfi_signal": "LONG" if bull_div else ("SHORT" if bear_div else "NEUTRAL"),
        }
    except Exception:
        return {}


def compute_funding_zscore(funding_rates: List[float], lookback: int = 21) -> Dict[str, Any]:
    """Funding-rate z-score for perpetual futures. |z|>2 = crowded longs/shorts."""
    if not funding_rates or len(funding_rates) < lookback + 1:
        return {}
    try:
        arr = np.array(funding_rates[-(lookback + 1):], dtype=np.float64)
        history = arr[:-1]
        current = float(arr[-1])
        mean = float(np.mean(history))
        std = float(np.std(history))
        if std <= 0 or not np.isfinite(std):
            return {
                "funding_zscore": 0.0,
                "funding_extreme": False,
                "funding_current": round(current, 8),
                "funding_mean": round(mean, 8),
                "funding_std": 0.0,
            }
        z = (current - mean) / std
        return {
            "funding_zscore": round(float(z), 4),
            "funding_extreme": bool(abs(z) > 2.0),
            "funding_current": round(current, 8),
            "funding_mean": round(mean, 8),
            "funding_std": round(std, 8),
        }
    except Exception as e:
        logger.debug(f"[FUNDING_Z] Error: {e}")
        return {}


def compute_oi_velocity(oi_series: List[float]) -> Dict[str, Any]:
    """Open-interest velocity. Hourly samples. 1h/4h pct change + regime flag."""
    if not oi_series or len(oi_series) < 2:
        return {}
    try:
        arr = [float(x) for x in oi_series if float(x) > 0]
        if len(arr) < 2:
            return {}
        current = arr[-1]
        prev_1h = arr[-2]
        prev_4h = arr[-5] if len(arr) >= 5 else arr[0]
        ch1 = ((current - prev_1h) / prev_1h) * 100.0 if prev_1h > 0 else 0.0
        ch4 = ((current - prev_4h) / prev_4h) * 100.0 if prev_4h > 0 else 0.0
        if abs(ch1) < 0.5 and abs(ch4) < 1.5:
            regime = "flat"
        elif ch4 > 0 and ch1 > 0:
            regime = "rising"
        elif ch4 < 0 and ch1 < 0:
            regime = "falling"
        else:
            regime = "flat"
        return {
            "oi_change_1h_pct": round(float(ch1), 4),
            "oi_change_4h_pct": round(float(ch4), 4),
            "oi_regime": regime,
            "oi_current": round(float(current), 4),
        }
    except Exception as e:
        logger.debug(f"[OI_VELOCITY] Error: {e}")
        return {}


def _consensus_load_klines(klines_cache_dir: Path, symbol: str, tf: str) -> List[Dict]:
    """Load klines from disk cache."""
    paths = [
        klines_cache_dir / f"{symbol.upper()}_{tf}.json",
        klines_cache_dir / f"{symbol.upper()}_{tf}.json.bak",
    ]
    for p in paths:
        if p.exists():
            try:
                raw = json.loads(p.read_text())
                bars = raw if isinstance(raw, list) else raw.get('bars', raw.get('candles', []))
                if isinstance(bars, list) and len(bars) > 0:
                    return bars
            except Exception:
                continue
    return []


def compute_extra_indicators_crypto(symbol: str, klines_cache_dir: Path, funding_rates: Optional[List[float]] = None, oi_series: Optional[List[float]] = None) -> Dict[str, Any]:
    """Compute ALL extra crypto indicators. Reads klines from disk cache."""
    result = {}
    bars_3m = _consensus_load_klines(klines_cache_dir, symbol, "3m")
    if bars_3m and len(bars_3m) >= 21:
        closes_3m = [float(b.get('close') or b.get('c') or 0) for b in bars_3m if float(b.get('close') or b.get('c') or 0) > 0]
        ema_3m = compute_ema_9_21(closes_3m)
        if ema_3m:
            result.update({f"{k}_3m": v for k, v in ema_3m.items()})
        today_bars = bars_3m[-480:] if len(bars_3m) >= 480 else bars_3m
        vwap_data = compute_vwap_from_bars(today_bars, session_hours=24)
        if vwap_data:
            result.update(vwap_data)
    bars_15m = _consensus_load_klines(klines_cache_dir, symbol, "15m")
    if bars_15m and len(bars_15m) >= 21:
        closes_15m = [float(b.get('close') or b.get('c') or 0) for b in bars_15m if float(b.get('close') or b.get('c') or 0) > 0]
        highs_15m = [float(b.get('high') or b.get('h') or 0) for b in bars_15m if float(b.get('close') or b.get('c') or 0) > 0]
        lows_15m = [float(b.get('low') or b.get('l') or 0) for b in bars_15m if float(b.get('close') or b.get('c') or 0) > 0]
        ema_15m = compute_ema_9_21(closes_15m)
        if ema_15m:
            result.update({f"{k}_15m": v for k, v in ema_15m.items()})
        kc_15m = compute_keltner_channels(closes_15m, highs_15m, lows_15m)
        if kc_15m:
            result.update({f"{k}_15m": v for k, v in kc_15m.items()})
    bars_1h = _consensus_load_klines(klines_cache_dir, symbol, "1h")
    if bars_1h and len(bars_1h) >= 20:
        closes_1h = [float(b.get('close') or b.get('c') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        highs_1h = [float(b.get('high') or b.get('h') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        lows_1h = [float(b.get('low') or b.get('l') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        kc_1h = compute_keltner_channels(closes_1h, highs_1h, lows_1h)
        if kc_1h:
            result.update({f"{k}_1h": v for k, v in kc_1h.items()})
        ema_1h = compute_ema_9_21(closes_1h)
        if ema_1h:
            result.update({f"{k}_1h": v for k, v in ema_1h.items()})
    bars_d = _consensus_load_klines(klines_cache_dir, symbol, "D")
    if bars_d and len(bars_d) >= 100:
        closes_d = [float(b.get('close') or b.get('c') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        opens_d = [float(b.get('open') or b.get('o') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        highs_d = [float(b.get('high') or b.get('h') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        lows_d = [float(b.get('low') or b.get('l') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        vols_d = [float(b.get('volume') or b.get('v') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        clenow = compute_clenow_score(closes_d, 90, ann_factor=365)
        if clenow:
            result.update(clenow)
        sepa = compute_minervini_sepa(closes_d, highs_d, lows_d, vols_d)
        if sepa:
            result.update(sepa)
        smfi = compute_smfi(opens_d, highs_d, lows_d, closes_d, 20)
        if smfi:
            result.update(smfi)
    if funding_rates:
        fz = compute_funding_zscore(funding_rates, lookback=21)
        if fz:
            result.update(fz)
    if oi_series:
        oiv = compute_oi_velocity(oi_series)
        if oiv:
            result.update(oiv)
    return result


def get_rvol_gate_for_strategy(strategy: str) -> float:
    """Returns minimum RVOL required for each strategy type. Crypto-aligned."""
    gates = {
        "MOMENTUM": 1.5,
        "DC_BREAKOUT": 1.5,
        "SCALP": 1.0,
        "HODL": 0.5,
        "SATOSHIT": 0.3,
        "ROTATION": 0.3,
        "EPISODIC_PIVOT": 2.0,
        "FUNDING_FADE": 0.5,
        "OI_BREAKOUT": 1.5,
    }
    return gates.get(strategy, 0.5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.exception(f"Fatal unhandled exception in ez_indicators: {e}")
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)


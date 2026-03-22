#!/home/niels/.conda/envs/binance_env/bin/python3
import asyncio
import resource
try:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 10000), hard))
except Exception: pass
import aiohttp
import sys
import os
import json
import uuid
import ssl
import time
import tempfile
import certifi
import websockets
import pandas as pd
import numpy as np
import redis.asyncio as redis
from redis import exceptions as redis_exceptions
import shutil
from typing import Dict, Optional, List, Tuple, Any
import platform
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path
from io import StringIO
import subprocess
from dotenv import dotenv_values
import gc
import warnings
import uuid
import aiofiles
from contextvars import ContextVar
from dateutil.parser import isoparse
warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")
from utils import logger, action_logger, log_augment_action, log_reduce_action, current_account,  AccountFilter, get_live_usdc_pairs, clean_kline_data, force_usdc_if_needed, setup_logger_with_rotation, standardize_kline_data_for_json, get_rum_manager, get_simple_redis_manager, get_current_environment, _resolve_klines_directories, orjson_default
from config import Config
config = Config()
logger = setup_logger_with_rotation("ez_prices", "ez_prices.log")
try:
    from typing import Any, Dict, List, Optional, Union ###ORJSON NOT APPLIED YET!!!
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
    """Atomically write JSON file with unique temp name, fsync, and pre-rename validation."""
    random_suffix = uuid.uuid4().hex
    temp_file = file_path.with_name(f".{file_path.name}.{random_suffix}.tmp")
    try:
        if not file_path.parent.exists():
            await asyncio.to_thread(file_path.parent.mkdir, parents=True, exist_ok=True)
        try:
            content = json_dumps(data)
            if isinstance(content, str):
                content = content.encode('utf-8')
        except Exception:
            content = json.dumps(data, indent=2, default=str).encode('utf-8')
        async with aiofiles.open(temp_file, 'wb') as f:
            await f.write(content)
            await f.flush()
            await asyncio.to_thread(os.fsync, f.fileno())
        # --- OPTION C: validate before rename ---
        try:
            async with aiofiles.open(temp_file, 'rb') as vf:
                verify_bytes = await vf.read()
            safe_json_loads(verify_bytes)
        except Exception as ve:
            await asyncio.to_thread(os.remove, temp_file)
            logger.error(f"🚫 Write-validation FAILED for {file_path} (temp deleted, original kept): {ve}")
            return
        await asyncio.to_thread(os.replace, str(temp_file), str(file_path))
    except Exception as e:
        if await asyncio.to_thread(os.path.exists, temp_file):
            try: await asyncio.to_thread(os.remove, temp_file)
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
class PriceService:
    """Service class to expose price caches for direct import access."""
    def __init__(self):
        self.price_cache: Dict[str, Dict[str, Any]] = {}
        self.price_cache_2: Dict[str, Dict[str, Any]] = {}
        self.price_cache_3: Dict[str, Dict[str, Any]] = {}
        self.price_update_time: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()

price_service_global: Optional[PriceService] = None

def get_price_service() -> Optional[PriceService]:
    """Get the global price service instance for direct access to price caches."""
    return price_service_global

async def bootstrap_price_service() -> PriceService:
    """Bootstrap and return the price service instance."""
    global price_service_global
    if price_service_global is None:
        price_service_global = PriceService()
        try:
            # Use resilient loader
            if config.PRICE_CACHE_FILE.exists():
                price_service_global.price_cache = await load_json_resilient(config.PRICE_CACHE_FILE) or {}
            if config.PRICE_CACHE_FILE_2.exists():
                price_service_global.price_cache_2 = await load_json_resilient(config.PRICE_CACHE_FILE_2) or {}
            if config.PRICE_CACHE_FILE_3.exists():
                price_service_global.price_cache_3 = await load_json_resilient(config.PRICE_CACHE_FILE_3) or {}
        except Exception as e:
            logger.warning(f"Failed to load price caches: {e}")
    return price_service_global

def update_price_cache(symbol: str, price: float, timestamp: datetime, cache_type: str = "price_cache") -> None:
    """Update price cache in both file and service dict."""
    global price_service_global
    if price_service_global is None:
        return
    ts_str = timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ') if isinstance(timestamp, datetime) else str(timestamp)
    entry = {"price": price, "timestamp": ts_str}
    
    # Update memory
    cache_dict = getattr(price_service_global, cache_type, None)
    if cache_dict is not None:
        cache_dict[symbol] = entry
        
    # Async write to file
    file_path = getattr(config, f"PRICE_CACHE_FILE{'_{}'.format(cache_type.split('_')[-1]) if cache_type != 'price_cache' else ''}", None) 
    # Fallbacks for path resolution logic
    if not file_path:
        if cache_type == "price_cache": file_path = config.PRICE_CACHE_FILE
        elif cache_type == "price_cache_2": file_path = config.PRICE_CACHE_FILE_2
        elif cache_type == "price_cache_3": file_path = config.PRICE_CACHE_FILE_3

    async def _write():
        try:
            # Read-Modify-Write safely
            current_data = await load_json_resilient(file_path)
            if not isinstance(current_data, dict): current_data = {}
            
            current_data[symbol] = entry
            await atomic_write_json(file_path, current_data)
        except Exception as e:
            logger.debug(f"Failed to update {cache_type} file for {symbol}: {e}")

    try:
        asyncio.get_running_loop().create_task(_write())
    except RuntimeError:
        pass

def update_price_cache_sync(symbol: str, price: float, timestamp: datetime, cache_type: str = "price_cache") -> None:
    """Synchronous version - update price cache in service dict only (file write handled separately)."""
    global price_service_global
    if price_service_global is None:
        return
    ts_str = timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ') if isinstance(timestamp, datetime) else str(timestamp)
    entry = {"price": price, "timestamp": ts_str}
    cache_dict = getattr(price_service_global, cache_type, None)
    if cache_dict is not None:
        cache_dict[symbol] = entry

class ConsolidatedBackupSystem:
    def __init__(self, klines_cache_dir: Path, backup_dir: Optional[Path] = None):
        self.klines_cache_dir = klines_cache_dir

        default_backup_dirs = [
            Path("/Volumes/SSD2T/backup/klines_cache_consolidated"),
            klines_cache_dir / "backups_consolidated"
        ]

        self.backup_dirs = []
        for d in default_backup_dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                self.backup_dirs.append(d)
            except Exception:
                continue

        if backup_dir:
            backup_dir.mkdir(parents=True, exist_ok=True)
            self.backup_dirs.insert(0, backup_dir)

        self.original_backup_dirs = [
            Path("/Volumes/SSD2T/backup/klines_cache"),
            klines_cache_dir / "backup",
            klines_cache_dir / "backups",
            klines_cache_dir / "backups_mac",
            klines_cache_dir / "backups_consolidated"
        ]
        self.higher_timeframes = ["15m", "1h", "4h", "D"]
        self.max_file_size_mb = 10  # Maximum output file size
        self.consolidation_lock = asyncio.Lock()

    async def consolidate_all_symbols(self):
        symbols = set()
        for origin_dir in self.original_backup_dirs:
            if not origin_dir.exists():
                continue
            for item in origin_dir.iterdir():
                if item.is_dir() and "_backup_" in item.name:
                    try:
                        symbol = item.name.split("_backup_")[0]
                        symbols.add(symbol)
                    except Exception as e:
                        logger.warning(f"Error parsing backup directory {item.name}: {e}")
        logger.info(f"Found {len(symbols)} symbols to consolidate: {sorted(symbols)}")

        for symbol in symbols:
            await self.create_consolidated_backup(symbol)
            await asyncio.sleep(0.1)  # Prevent resource exhaustion
            gc.collect()

    async def create_consolidated_backup(self, symbol: str) -> bool:
        async with self.consolidation_lock:
            try:
                consolidation_report = {
                    "symbol": symbol,
                    "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "consolidated_files": [],
                    "original_backups_removed": []
                }

                backup_dirs = []
                for origin_dir in self.original_backup_dirs:
                    if not origin_dir.exists():
                        continue
                    for item in origin_dir.iterdir():
                        if item.is_dir() and item.name.startswith(f"{symbol}_backup_"):
                            backup_dirs.append(item)

                if not backup_dirs:
                    logger.info(f"No existing backups found for {symbol}")
                    return False

                for tf in self.higher_timeframes:
                    all_data = []
                    for backup_dir in backup_dirs:
                        backup_file = backup_dir / f"{symbol}_{tf}.json"
                        if backup_file.exists():
                            try:
                                with open(backup_file, 'r') as f:
                                    data = json.load(f)
                                if isinstance(data, list):
                                    all_data.extend(data)
                                elif isinstance(data, dict) and 'klines' in data:
                                    all_data.extend(data['klines'])
                            except Exception as e:
                                logger.warning(f"Error reading {backup_file}: {e}")
                                continue

                    if not all_data:
                        logger.info(f"No data found for {symbol}_{tf}")
                        continue

                    df = pd.DataFrame(all_data)

                    if 'timestamp' not in df.columns:
                        logger.warning(f"Missing timestamp column in {symbol}_{tf} data")
                        continue

                    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce', utc=True)
                    df = df.dropna(subset=['timestamp']).sort_values('timestamp').drop_duplicates(subset=['timestamp'])

                    gap_info = self._detect_gaps(df, tf)

                    if gap_info['has_gaps']:
                        logger.warning(f"Gaps detected in {symbol}_{tf} data: {gap_info['gap_count']} gaps")

                    chunks = self._split_data_into_chunks(df, tf)

                    for i, chunk_df in enumerate(chunks):
                        chunk_filename = f"{symbol}_{tf}_part{(i+1):03d}.json"
                        chunk_path = self.backup_dirs[0] / chunk_filename

                        chunk_df_serializable = chunk_df.copy()
                        if 'timestamp' in chunk_df.columns:
                            chunk_df_serializable['timestamp'] = chunk_df['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        chunk_data = chunk_df_serializable.to_dict('records')

                        chunk_metadata = {
                            "symbol": symbol,
                            "timeframe": tf,
                            "part_number": i + 1,
                            "total_parts": len(chunks),
                            "start_timestamp": pd.to_datetime(chunk_df['timestamp'].min()).strftime('%Y-%m-%dT%H:%M:%S.%fZ') if not chunk_df.empty else None,
                            "end_timestamp": pd.to_datetime(chunk_df['timestamp'].max()).strftime('%Y-%m-%dT%H:%M:%S.%fZ') if not chunk_df.empty else None,
                            "record_count": len(chunk_df),
                            "consolidated_at": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            "gap_info": gap_info
                        }
                        with open(chunk_path, 'w') as f:
                            json.dump({
                                "metadata": chunk_metadata,
                                "data": chunk_data
                            }, f, indent=2)
                        consolidation_report["consolidated_files"].append({
                            "filename": chunk_filename,
                            "record_count": len(chunk_df),
                            "size_mb": os.path.getsize(chunk_path) / (1024 * 1024)
                        })

                for backup_dir in backup_dirs:
                    try:
                        shutil.rmtree(backup_dir)
                        consolidation_report["original_backups_removed"].append(backup_dir.name)
                    except Exception as e:
                        logger.warning(f"Error removing {backup_dir}: {e}")

                report_path = self.backup_dirs[0] / f"{symbol}_consolidation_report.json"
                with open(report_path, 'w') as f:
                    json.dump(consolidation_report, f, indent=2)

                logger.info(f"Consolidated backup created for {symbol}: {len(consolidation_report['consolidated_files'])} files")
                return True

            except Exception as e:
                logger.exception(f"Consolidation failed for {symbol}: {e}")
                return False

    def _detect_gaps(self, df: pd.DataFrame, timeframe: str) -> Dict[str, object]:
        # Use the tf argument for interval, fix previous variable name confusion
        expected_delta = {
            "3m": pd.Timedelta(minutes=3),
            "15m": pd.Timedelta(minutes=15),
            "1h": pd.Timedelta(hours=1),
            "4h": pd.Timedelta(hours=4),
            "D": pd.Timedelta(days=1),
        }.get(timeframe, pd.Timedelta(minutes=3))

        timestamps = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        timestamps = timestamps.dropna().sort_values()
        deltas = timestamps.diff().iloc[1:]
        gaps: List[Dict[str, object]] = []
        for delta, start_ts in zip(deltas, timestamps.iloc[:-1]):
            if delta > expected_delta:
                missing = int(delta / expected_delta) - 1
                if missing > 0:
                    gaps.append({
                        "start": start_ts.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                        "end": (start_ts + delta).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                        "missing_bars": missing,
                        "duration_seconds": delta.total_seconds(),
                    })
        return {"has_gaps": bool(gaps), "gap_count": len(gaps), "gaps": gaps}

    def _split_data_into_chunks(self, df: pd.DataFrame, timeframe: str) -> List[pd.DataFrame]:
        """Split DataFrame into chunks based on target file size and max bars."""
        if df.empty:
            return [df]
        max_bars_per_chunk = 10000
        sample_size = min(100, len(df))
        sample_df = df.head(sample_size)
        if 'timestamp' in sample_df.columns:
            sample_serializable = sample_df.copy()
            sample_serializable['timestamp'] = sample_df['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        else:
            sample_serializable = sample_df
        sample_data = sample_serializable.to_dict('records')
        sample_json = json.dumps(sample_data)
        avg_bytes_per_record = len(sample_json.encode('utf-8')) / len(sample_data) if sample_data else 1000
        metadata_overhead = 500
        target_chunk_bytes = (self.max_file_size_mb * 1024 * 1024) - metadata_overhead
        records_per_chunk = max(1, int(target_chunk_bytes / avg_bytes_per_record))
        records_per_chunk = min(records_per_chunk, max_bars_per_chunk)
        return [df.iloc[i:i + records_per_chunk].copy() for i in range(0, len(df), records_per_chunk)]


class HigherTimeframeBackup:
    def __init__(self, klines_cache_dir: Path):
        self.klines_cache_dir = klines_cache_dir
        self.backup_dir = self._resolve_backup_dir()
        self.max_backups = 20
        self.higher_timeframes = ["15m", "1h", "4h", "D"]

    def _resolve_backup_dir(self) -> Path:
        if platform.system() == "Darwin":
            # Use external SSD drive for backups if available
            backup_dir = Path("/Volumes/SSD2T/backup/klines_cache")
            if not backup_dir.exists():
                backup_dir = self.klines_cache_dir / "backups"
        else:
            backup_dir = self.klines_cache_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    async def create_backup(self, symbol: str) -> Optional[Path]:
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_name = f"{symbol}_backup_{timestamp}"
            backup_path = self.backup_dir / backup_name
            backup_path.mkdir(exist_ok=True)

            backed_up_files = []
            total_data_size = 0

            for tf in self.higher_timeframes:
                source_file = self.klines_cache_dir / f"{symbol}_{tf}.json"
                if source_file.exists():
                    backup_file = backup_path / f"{symbol}_{tf}.json"
                    shutil.copy2(source_file, backup_file)
                    file_size = source_file.stat().st_size
                    total_data_size += file_size
                    try:
                        with open(source_file, 'r') as f:
                            data = json.load(f)
                            bar_count = len(data) if isinstance(data, list) else 0
                    except Exception:
                        bar_count = 0
                    backed_up_files.append({
                        'timeframe': tf,
                        'size_bytes': file_size,
                        'size_kb': file_size / 1024,
                        'bar_count': bar_count
                    })

            if backed_up_files:
                metadata = {
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "backup_time": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "backup_type": "COMPLETE_HISTORICAL",
                    "timeframes_backed_up": backed_up_files,
                    "total_data_size_bytes": total_data_size,
                    "total_data_size_mb": total_data_size / (1024 * 1024),
                    "source_directory": str(self.klines_cache_dir),
                    "backup_version": "2.0",
                }
                metadata_file = backup_path / "backup_metadata.json"
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
                logger.info(f"Backup complete for {symbol}: {metadata}")
                return backup_path
            return None

        except Exception as e:
            logger.error(f"Backup failed for {symbol}: {e}")
            return None

    # Add more backup/cleanup/list/restore functions here as per your needs

# Usage (async):
# cbs = ConsolidatedBackupSystem(Path("/data/klines"))
# await cbs.consolidate_all_symbols()
# htb = HigherTimeframeBackup(Path("/data/klines"))
# await htb.create_backup("BTCUSDT")

# class HigherTimeframeBackup:
#     def __init__(self, klines_cache_dir: Path):
#         self.klines_cache_dir = klines_cache_dir
#         if platform.system() == "Darwin":  # macOS
#             # Use external SSD2T drive for backups
#             self.backup_dir = Path("/Volumes/SSD2T/backup/klines_cache")
#             logger.info(f"macOS detected - Using external SSD2T backup location: {self.backup_dir}")
#         else:
#             # Use local backup directory for other systems
#             self.backup_dir = klines_cache_dir / "backups"
#             logger.info(f"{platform.system()} detected - Using local backup location: {self.backup_dir}")
        
#         # Ensure backup directory exists; fall back to local if external is unavailable
#         try:
#             self.backup_dir.mkdir(parents=True, exist_ok=True)
#         except Exception as e:
#             logger.warning(f"Cannot use {self.backup_dir} ({e}). Falling back to local backups.")
#             self.backup_dir = klines_cache_dir / "backups"
#             self.backup_dir.mkdir(parents=True, exist_ok=True)
        
#         self.max_backups = 20  # Keep more backups for historical completeness
#         self.higher_timeframes = ["15m", "1h", "4h", "D"]
        
#         # Verify backup location is accessible
#         self._verify_backup_location()
    
#     def _verify_backup_location(self):
#         """Verifies that the backup location is accessible and writable."""
#         try:
#             # Test write access
#             test_file = self.backup_dir / ".test_write_access"
#             test_file.write_text("test")
#             test_file.unlink()
            
#             # Check available space (if on macOS with SSD2T)
#             if "/Volumes/SSD2T" in str(self.backup_dir):
#                 try:
#                     total, used, free = shutil.disk_usage(self.backup_dir)
#                     free_gb = free / (1024**3)
#                     logger.debug(f"SSD2T backup drive: {free_gb:.1f} GB available")
                    
#                     if free_gb < 10:  # Less than 10GB free
#                         logger.warning(f"Only {free_gb:.1f} GB free on backup drive!")
#                 except Exception:
#                     pass
                    
#             logger.info(f"Backup location verified: {self.backup_dir}")
            
#         except Exception as e:
#             logger.warning(f"Backup location verification failed: {e}")
#             logger.info(f"Falling back to local backup location: {self.klines_cache_dir / 'backups'}")
#             self.backup_dir = self.klines_cache_dir / "backups"
#             self.backup_dir.mkdir(parents=True, exist_ok=True)

#     async def create_backup(self, symbol: str) -> Optional[Path]:
#         """Creates a timestamped backup of ALL higher timeframe data - NO CLIPPING."""
#         try:
#             timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#             backup_name = f"{symbol}_backup_{timestamp}"
#             backup_path = self.backup_dir / backup_name
#             backup_path.mkdir(exist_ok=True)
            
#             backed_up_files = []
#             total_data_size = 0
            
#             for tf in self.higher_timeframes:
#                 source_file = self.klines_cache_dir / f"{symbol}_{tf}.json"
#                 if source_file.exists():
#                     # NO CLIPPING - backup complete files
#                     backup_file = backup_path / f"{symbol}_{tf}.json"
                    
#                     # Copy the COMPLETE file without any modifications
#                     shutil.copy2(source_file, backup_file)
                    
#                     # Get file info for metadata
#                     file_size = source_file.stat().st_size
#                     total_data_size += file_size
                    
#                     # Count bars in the file
#                     try:
#                         with open(source_file, 'r') as f:
#                             data = json.load(f)
#                             bar_count = len(data) if isinstance(data, list) else 0
#                     except:
#                         bar_count = 0
                    
#                     backed_up_files.append({
#                         'timeframe': tf,
#                         'size_bytes': file_size,
#                         'size_kb': file_size / 1024,
#                         'bar_count': bar_count
#                     })
            
#             if backed_up_files:
#                 # Create comprehensive backup metadata
#                 metadata = {
#                     "symbol": symbol,
#                     "timestamp": timestamp,
#                     "backup_time": datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
#                     "backup_type": "COMPLETE_HISTORICAL",
#                     "description": "Complete historical backup for backtesting and fallback - no data clipping",
#                     "timeframes_backed_up": backed_up_files,
#                     "total_data_size_bytes": total_data_size,
#                     "total_data_size_mb": total_data_size / (1024 * 1024),
#                     "source_directory": str(self.klines_cache_dir),
#                     "backup_version": "2.0",
#                 }
                
#                 metadata_file = backup_path / "backup_metadata.json"
#                 with open(metadata_file, 'w') as f:
#                     json.dump(metadata, f, indent=2)
                
#                 total_bars = sum(tf['bar_count'] for tf in backed_up_files)
#                 self.logger.info(f"COMPLETE HISTORICAL BACKUP CREATED for {symbol}: {total_bars} total bars")
#                 return backup_path
                
#             return None
                    
#         except Exception as e:
#             self.logger.error(f"Backup failed for {symbol}: {e}")
#             return None
            
    async def cleanup_old_backups(self):
        """Removes old backups while preserving historical completeness.
        
        Instead of just keeping the last N backups, this system:
        1. Keeps ALL backups for symbols with growing historical data
        2. Only removes backups when newer ones have MORE historical data
        3. Preserves the most complete historical dataset for each symbol
        """
        try:
            # Group backups by symbol
            symbol_backups = {}
            for backup_dir in self.backup_dir.iterdir():
                if not backup_dir.is_dir() or not backup_dir.name.endswith('_backup_'):
                    continue
                
                # Extract symbol from backup name
                parts = backup_dir.name.split('_backup_')
                if len(parts) != 2:
                    continue
                    
                symbol = parts[0]
                timestamp = parts[1]
                
                if symbol not in symbol_backups:
                    symbol_backups[symbol] = []
                
                # Get backup metadata
                metadata_file = backup_dir / "backup_metadata.json"
                if metadata_file.exists():
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                        
                        # Calculate historical completeness score
                        total_bars = sum(tf.get('bar_count', 0) for tf in metadata.get('timeframes_backed_up', []))
                        completeness_score = total_bars
                        
                        symbol_backups[symbol].append({
                            'path': backup_dir,
                            'timestamp': timestamp,
                            'completeness_score': completeness_score,
                            'metadata': metadata
                        })
                    except Exception:
                        # If metadata is corrupted, use file size as fallback
                        total_size = sum(f.stat().st_size for f in backup_dir.glob('*.json') if f.is_file())
                        symbol_backups[symbol].append({
                            'path': backup_dir,
                            'timestamp': timestamp,
                            'completeness_score': total_size,
                            'metadata': None
                        })
            
            # For each symbol, keep the most complete backups
            for symbol, backups in symbol_backups.items():
                # Sort by completeness score (descending) and timestamp (newest first)
                backups.sort(key=lambda x: (x['completeness_score'], x['timestamp']), reverse=True)
                
                # Keep the most complete backups (up to max_backups)
                to_keep = backups[:self.max_backups]
                to_remove = backups[self.max_backups:]
                
                for backup in to_remove:
                    try:
                        shutil.rmtree(backup['path'])
                        logger.debug(f"Removed old backup: {backup['path'].name} (completeness: {backup['completeness_score']})")
                    except Exception as e:
                        logger.warning(f"Failed to remove backup {backup['path'].name}: {e}")
                
                if to_keep:
                    best_backup = to_keep[0]
                    logger.info(f"{symbol}: Keeping {len(to_keep)} best backups (best: {best_backup['completeness_score']} completeness)")
                await asyncio.sleep(0.1)
                    
        except Exception as e:
            logger.error(f"Backup cleanup failed: {e}")
    
    async def list_backups(self, symbol: str = None) -> List[Dict]:
        """Lists all available backups with historical completeness information."""
        try:
            backups = []
            for backup_dir in self.backup_dir.iterdir():
                if not backup_dir.is_dir() or not backup_dir.name.endswith('_backup_'):
                    continue
                    
                if symbol and not backup_dir.name.startswith(f"{symbol}_backup_"):
                    continue
                
                metadata_file = backup_dir / "backup_metadata.json"
                if metadata_file.exists():
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                        
                        # Calculate completeness metrics
                        total_bars = sum(tf.get('bar_count', 0) for tf in metadata.get('timeframes_backed_up', []))
                        total_size_mb = metadata.get('total_data_size_mb', 0)
                        
                        metadata['completeness_score'] = total_bars
                        metadata['total_bars'] = total_bars
                        metadata['total_size_mb'] = total_size_mb
                        
                        backups.append(metadata)
            
            # Sort by completeness score (descending) and timestamp (newest first)
            return sorted(backups, key=lambda x: (x.get('completeness_score', 0), x['timestamp']), reverse=True)
            
        except Exception as e:
            logger.error(f"Failed to list backups: {e}")
            return []
    
    async def restore_from_backup(self, symbol: str, backup_timestamp: str) -> bool:
        """Restores higher timeframe data from a specific backup.
        
        This restoration preserves the COMPLETE historical dataset for backtesting.
        """
        try:
            backup_name = f"{symbol}_backup_{backup_timestamp}"
            backup_path = self.backup_dir / backup_name
            
            if not backup_path.exists():
                logger.info(f"Backup {backup_name} not found")
                return False
            
            restored_files = []
            for tf in self.higher_timeframes:
                backup_file = backup_path / f"{symbol}_{tf}.json"
                if backup_file.exists():
                    target_file = self.klines_cache_dir / f"{symbol}_{tf}.json"
                    
                    # Restore the COMPLETE file
                    shutil.copy2(backup_file, target_file)
                    restored_files.append(tf)
            
            if restored_files:
                logger.info(f"COMPLETE HISTORICAL RESTORE: {symbol} from backup {backup_timestamp}")
                logger.info(f"   Restored timeframes: {', '.join(restored_files)}")
                logger.info("   Ready for backtesting and live trading")
                return True
            else:
                logger.info(f"No files restored for {symbol} from backup {backup_timestamp}")
                return False
                
        except Exception as e:
            logger.error(f"Restore failed for {symbol} from {backup_timestamp}: {e}")
            return False
    
    async def get_most_complete_backup(self, symbol: str) -> Optional[Dict]:
        """Gets the backup with the most complete historical data for a symbol."""
        backups = await self.list_backups(symbol)
        if backups:
            return backups[0]  # Already sorted by completeness score
        return None
    
    async def analyze_backup_completeness(self, symbol: str) -> Dict:
        """Analyzes the completeness of backups for a symbol."""
        backups = await self.list_backups(symbol)
        
        if not backups:
            return {"status": "no_backups", "message": f"No backups found for {symbol}"}
        
        best_backup = backups[0]
        total_backups = len(backups)
        
        # Calculate growth over time
        completeness_trend = []
        for backup in backups:
            completeness_trend.append({
                'timestamp': backup['timestamp'],
                'completeness_score': backup.get('completeness_score', 0),
                'total_bars': backup.get('total_bars', 0)
            })
        
        return {
            "status": "analyzed",
            "symbol": symbol,
            "total_backups": total_backups,
            "best_backup": best_backup,
            "completeness_trend": completeness_trend,
            "recommendation": "Ready for backtesting" if best_backup.get('completeness_score', 0) > 1000 else "Limited historical data"
        }


# ==============================================================================
# CONFIGURATION & CONSTANTS
# ==============================================================================
# from config import (
#     LOG_DIR,
#     KLINES_CACHE_DIR,
#     SYMBOLS_FILE,
#     REDIS_DB,
#     DATA_READY_FLAG_FILE,
#     FAPI_BASE_URL ,
#     FSTREAM_WS_URL_BASE,
#     ALL_TIMEFRAMES,
#     KLINE_COLUMNS,
#     LIVE_USDC_PAIRS_FILE,
#     ssl_context,
# )
ALL_TIMEFRAMES = ["3m", "15m", "1h", "4h", "D"]
IDEAL_BARS_TARGETS = {"3m": 1200, "15m": 1200, "1h": 1200, "4h": 1200, "D": 300}
MAX_BARS_PER_API_FETCH = 1500
STARTUP_BATCH_SIZE = 85
env_info=get_current_environment()
env=env_info['env']
FILE_IO_CONCURRENCY = config.EZ_PRICES_FILE_IO_SEMAPHORE.get(env,10)
API_CONCURRENCY = config.EZ_PRICES_API_SEMAPHORE.get(env,3)
REDIS_EXPIRY_SECONDS = 300  # Increased to 5 minutes to allow time for 3m resampling
M_READY_FLAG_FILE = config.BASE_PATH / "m_ready.flag"
API_REQUEST_DELAY_SECONDS = config.EZ_PRICES_API_DELAY.get(env,0.5)
API_DISABLE_SECONDS = 600
QUICK_TOPUP_LIMITS = {"3m": 1500, "15m": 1500, "1h": 1500, "4h": 1500, "D": 200}
SMALL_BOUNDARY_TOPUP_LIMITS = {"15m": 5, "1h": 3, "4h": 3, "D": 2}
STALENESS_LIMITS_SECONDS = {"3m": 180, "15m": 900, "1h": 3600, "4h": 14400, "D": 86400}


class ResamplingAndGapFillEngine:
    def __init__(self):
        self.session=None;self.redis_client=None;self.redis_client_read=None;self.redis_manager=None;self.symbols_to_run=[];self.file_io_semaphore=asyncio.Semaphore(FILE_IO_CONCURRENCY);self.backup_system=HigherTimeframeBackup(config.KLINES_CACHE_DIR);self.api_semaphore=asyncio.Semaphore(API_CONCURRENCY);self.fapi_semaphore=asyncio.Semaphore(config.EZ_PRICES_FAPI_SEMAPHORE.get(env,2));self.ideal_bars_targets=IDEAL_BARS_TARGETS;self.active_timeframes=ALL_TIMEFRAMES;self.daily_clipping_allowed=False;self.last_3m_boundary_processed=None;self._file_write_locks={};self._bg_tasks=[];self._shutting_down=False;self.logger=logger;self.use_external_3m=True;self._missing_bar_attempts={};self._missing_bar_cooldown=300;self._last_cache_cleanup=0;self.fetch_1m_from_api=False;self.fetch_3m_from_api=False;self.last_boundary_processed={"15m":None,"1h":None,"4h":None,"D":None};self._fully_fetched_cache=set();self.validator=None;self.gap_filler=None;self.data_quality_check_interval=120;self.last_quality_check=0;self.quality_threshold=0.9;self._tf_resample_args={"15m":"15min","1h":"1h","4h":"4h","D":"D"};self._tf_required_3m={"15m":5,"1h":20,"4h":80,"D":480};self._fast_tf_order=("15m","1h","4h","D");self._api_disabled_until=0

    async def _get_manager_connection(self, manager, target: str):
        if not manager:return None
        try:
            if hasattr(manager,"_get_or_connect"):
                return await manager._get_or_connect(target)
            if hasattr(manager,"_get_connection"):
                return await manager._get_connection(target)
        except Exception as e:
            self.logger.debug(f"manager {type(manager).__name__} connect {target} failed: {e}")
        conn=getattr(manager,"connections",{}).get(target)
        if not conn:return None
        try:
            await asyncio.wait_for(conn.ping(),timeout=1.0)
            return conn
        except Exception:
            return None


    def _filter_future_and_align(self, df: pd.DataFrame, interval: str) -> pd.DataFrame:
        """Drop future-dated rows and preserve historical data integrity."""
        if df is None or df.empty:
            return pd.DataFrame(columns=config.KLINE_COLUMNS)
        out = df.copy()
        out['timestamp'] = pd.to_datetime(out['timestamp'], errors='coerce', utc=True)
        out = out.dropna(subset=['timestamp'])
        now_utc = datetime.now(timezone.utc) + timedelta(minutes=2)
        out = out[out['timestamp'] <= now_utc]

        # Apply lenient alignment only - just remove obviously malformed timestamps
        # Don't apply strict alignment to historical data during sanitization
        one_hour_ago = now_utc - timedelta(hours=1)

        def basic_validation(ts: pd.Timestamp) -> bool:
            # For recent data, apply some basic validation
            if ts > one_hour_ago:
                # Just ensure seconds are reasonable (0-59)
                return 0 <= ts.second <= 59
            # For historical data, be very lenient
            return True

        out = out[out['timestamp'].apply(basic_validation)]
        out = out.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp').reset_index(drop=True)
        return out

    async def sanitize_existing_klines(self) -> None:
        """One-time cleanup of existing klines files to remove future/misaligned bars.
        
        DISABLED: This function was destroying files by over-aggressive cleaning.
        """
        self.logger.info("⚠️ sanitize_existing_klines() is DISABLED to prevent data destruction")
        return
        
        # DISABLED CODE BELOW - DO NOT RE-ENABLE WITHOUT FIXING
        # try:
        #     processed = 0
        #     for fp in await asyncio.to_thread(list, config.KLINES_CACHE_DIR.glob('*.json')):
        #         if not fp.is_file():
        #                     continue
        #         name = fp.name
        #         interval = None
        #         for tf in ( "3m", "15m", "1h", "4h", "D"):
        #             if name.endswith(f"_{tf}.json"):
        #                 interval = tf
        #                 break
        #         if not interval:
        #                 continue
        #         
        #         df = await self._read_file_unlocked(fp)
        #         if df.empty:
        #             continue
        #         cleaned = self._filter_future_and_align(df, interval)
        #         if len(cleaned) and (len(cleaned) != len(df) or not cleaned.equals(df.reset_index(drop=True))):
        #             await self._write_file_unlocked(cleaned, fp, existing_bar_count=len(df))
        #             processed += 1
        #     if processed:
        #         self.logger.info(f" Startup sanitization: files rewritten={processed}")
        # except Exception as e:
        #     self.logger.warning(f"Startup sanitization error: {e}")
        
    def _sanitize_klines(self, df: pd.DataFrame, interval: str) -> pd.DataFrame:
        """Remove malformed/future/misaligned timestamps and ensure sorted unique bars."""
        if df is None or df.empty:
            return pd.DataFrame(columns=config.KLINE_COLUMNS)
        out = df.copy()
        # Ensure timestamp is datetime UTC
        out['timestamp'] = pd.to_datetime(out['timestamp'], errors='coerce', utc=True)
        out = out.dropna(subset=['timestamp'])
        # Drop future bars (with slight grace)
        now_utc = datetime.now(timezone.utc) + timedelta(minutes=2)
        out = out[out['timestamp'] <= now_utc]

        # Lenient alignment for historical data: only apply strict alignment to recent data (within 1 hour)
        one_hour_ago = now_utc - timedelta(hours=1)

        def is_aligned(ts: pd.Timestamp) -> bool:
            # For very recent data (within 1 hour), apply strict alignment
            if ts > one_hour_ago:
                if interval == '3m':
                    return ts.second == 0 and ts.minute % 3 == 0
                if interval == '15m':
                    return ts.second == 0 and ts.minute % 15 == 0
                if interval == '1h':
                    return ts.minute == 0 and ts.second == 0
                if interval == '4h':
                    return ts.minute == 0 and ts.second == 0 and ts.hour % 4 == 0
                if interval == 'D':
                    return ts.hour == 0 and ts.minute == 0 and ts.second == 0

            # For historical data (older than 1 hour), be more lenient
            # Just ensure basic structure: seconds should be 0 for most intervals
            if ts <= one_hour_ago:
                if interval in ['3m', '15m', '1h', '4h']:
                    return ts.second == 0  # Only require seconds to be 0
                if interval == 'D':
                    return ts.hour == 0 and ts.minute == 0 and ts.second == 0
            return True

        out = out[out['timestamp'].apply(is_aligned)]
        # Final cleanup
        out = out.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp').reset_index(drop=True)
        return out

    async def ensure_latest_topup(self):
        """Forward-fill to the latest bar for higher TFs using small API pulls before heavy backfills."""
        tasks = []
        intervals = ["15m", "1h", "4h", "D"]
        # if self.fetch_3m_from_api:
        #     intervals.insert(0, "3m")
        # if self.fetch_1m_from_api:
        #     intervals.insert(0, "1m")
        for symbol in self.symbols_to_run:
            for interval in intervals:
                tasks.append(self._topup_symbol_interval(symbol, interval))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _topup_symbol_interval(self, symbol: str, interval: str, limit_override: int | None = None):
        try:
            existing = await self.get_klines_df(symbol, interval)
            limit = limit_override if limit_override is not None else QUICK_TOPUP_LIMITS.get(interval, 200)
            fetched = await self.fetch_klines_in_chunks(symbol, interval, limit, end_time_ms=None)
            if not fetched.empty:
                cleaned = self._sanitize_klines(fetched, interval)
                if not cleaned.empty:
                    await self._merge_preserving_history(symbol, interval, cleaned, existing)
        except Exception as e:
            self.logger.warning(f"Top-up failed for {symbol}_{interval}: {e}")

    async def _merge_preserving_history(self, symbol: str, interval: str, fetched: pd.DataFrame, existing: pd.DataFrame) -> None:
        if existing.empty:
            await self.merge_and_write_df(fetched, symbol, interval)
            return
        newest_existing_ts = pd.to_datetime(existing['timestamp']).max()
        fetched = fetched[pd.to_datetime(fetched['timestamp']) > newest_existing_ts]
        if fetched.empty:
            return
        combined = pd.concat([existing, fetched], ignore_index=True)
        combined = combined.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
        await self.merge_and_write_df(combined, symbol, interval)

    async def _topup_symbol_interval_small(self, symbol: str, interval: str, limit: int):
        try:
            fetched = await self.fetch_klines_in_chunks(symbol, interval, limit, end_time_ms=None)
            if not fetched.empty:
                cleaned = self._sanitize_klines(fetched, interval)
                if not cleaned.empty:
                    await self.merge_and_write_df(cleaned, symbol, interval)
        except Exception as e:
            self.logger.warning(f"Small top-up failed for {symbol}_{interval}: {e}")

    async def sanitize_existing_cache_files(self) -> None:
        """One-time cleanup: sanitize all existing higher TF cache files on disk.
        
        DISABLED: This function was destroying files by over-aggressive cleaning.
        """
        self.logger.info("⚠️ sanitize_existing_cache_files() is DISABLED to prevent data destruction")
        return
        
        # DISABLED CODE BELOW - DO NOT RE-ENABLE WITHOUT FIXING
        try:
            flag = config.BASE_PATH / "klines_sanitized.flag"
            # Always run now as requested; create flag afterward to avoid future reruns
            processed = 0
            fixed_rows = 0
            removed_rows = 0
            # Collect files
            entries = await asyncio.to_thread(list, config.KLINES_CACHE_DIR.iterdir())
            for fp in entries:
                if not fp.is_file():
                            continue
                name = fp.name
                interval = None
                for tf in ("15m", "1h", "4h", "D"):
                    if name.endswith(f"_{tf}.json"):
                        interval = tf
                        break
                if not interval:
                        continue
                try:
                    df = await self._read_file_unlocked(fp)
                    if df.empty:
                        continue
                    before = len(df)
                    cleaned = self._sanitize_klines(df, interval)
                    after = len(cleaned)
                    if after == 0:
                        self.logger.warning(f"Sanitizer produced 0 rows for {name}; skipping write.")
                        continue
                    if after != before or not cleaned.equals(df.reset_index(drop=True)):
                        await self._write_file_unlocked(cleaned, fp, existing_bar_count=before)
                        processed += 1
                        fixed_rows += after
                        removed_rows += max(0, before - after)
                except Exception as e:
                    self.logger.warning(f"Sanitize failed for {name}: {e}")
            # Create flag indicating cleanup has run
            try:
                flag.touch(exist_ok=True)
            except Exception:
                pass
            self.logger.info(f" Sanitization complete: files rewritten={processed}, rows_removed≈{removed_rows}, rows_kept≈{fixed_rows}")
        except Exception as e:
            self.logger.error(f"Sanitize existing cache files failed: {e}")

    async def initial_api_backfill_all(self):
        """DISABLED - Was calling _ensure_min_bars_via_api which destroys data."""
        self.logger.info("⚠️ initial_api_backfill_all DISABLED - was destroying klines data")
        return
        
        """Ensure minimal bars per TF using API backfill without resampling."""
        tasks = []
        intervals = ["15m", "1h", "4h", "D"]
        # if self.fetch_3m_from_api:
        #     intervals.insert(0, "3m")
        # if self.fetch_1m_from_api:
        #     intervals.insert(0, "1m")
        for symbol in self.symbols_to_run:
            for interval in intervals:
                target = 300 if interval == "D" else 1200
                tasks.append(self._ensure_min_bars_via_api(symbol, interval, target))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
    async def periodic_file_size_management(self, interval_seconds: int = 3600):
        await asyncio.sleep(300)  # Wait 5 minutes after startup
        
        while True:
            try:
                self.logger.info("📏 Checking JSON file sizes...")
                processed_files = 0
                
                for json_file in config.KLINES_CACHE_DIR.glob("*.json"):
                    try:
                        if not json_file.is_file():
                            continue
                        # DISABLED CLIPPING: Never reduce klines files, only append
                        # Keep all historical data for complete backtesting
                    except Exception as e:
                        self.logger.warning(f"Error processing {json_file}: {e}")
                
                if processed_files > 0:
                    self.logger.info(f"📏 File size management: processed {processed_files} files")
                    
            except Exception as e:
                self.logger.error(f"File size management error: {e}")
            
            await asyncio.sleep(interval_seconds)

    async def _ensure_min_bars_via_api(self, symbol: str, interval: str, min_bars: int):
        """DISABLED - This function was erasing ALL bars and wasting API resources."""
        # DO NOT USE - This function destroys existing data
        return
        
        """Ensure minimal bars per TF - ALWAYS try resampling from 3m FIRST before API."""
        try:
            existing = await self.get_klines_df(symbol, interval)
            have = len(existing)
            if have >= 900:
                return
            self.logger.info(f"🔄 {symbol}_{interval}: Have {have} bars, trying resample from 3m first...")
            if interval in ['15m', '1h', '4h', 'D']:
                if interval == '15m':
                    await self._cascade_3m_to_15m(symbol)
                elif interval == '1h':
                    await self._cascade_15m_to_1h(symbol)
                elif interval == '4h':
                    await self._cascade_1h_to_4h(symbol)
                elif interval == 'D':
                    await self._cascade_4h_to_D(symbol)
                existing = await self.get_klines_df(symbol, interval)
                have_after_resample = len(existing)
                if have_after_resample > have:
                    self.logger.info(f"✅ {symbol}_{interval}: Resampled {have} -> {have_after_resample} bars")
                if have_after_resample >= 900:
                    return
            target_bars = self.ideal_bars_targets.get(interval, 1200)
            if have >= target_bars:
                return
            self.logger.info(f"📡 {symbol}_{interval}: Resampling insufficient, fetching from API...")
            need = target_bars - have
            end_ts = int(existing['timestamp'].min().timestamp() * 1000) if not existing.empty else None
            while need > 0:
                batch = min(need, MAX_BARS_PER_API_FETCH)
                fetched = await self.fetch_klines_in_chunks(symbol, interval, batch, end_time_ms=end_ts)
                if fetched.empty:
                    break
                cleaned = self._sanitize_klines(fetched, interval)
                if cleaned.empty:
                    break
                await self.merge_and_write_df(cleaned, symbol, interval)
                need -= len(cleaned)
                end_ts = int(cleaned.iloc[0]['timestamp'].timestamp() * 1000) - 1
        except Exception as e:
            self.logger.warning(f"Backfill failed for {symbol}_{interval}: {e}")

    async def periodic_tmp_cleanup_task(self, interval_seconds: int = 900, min_age_seconds: int = 300):
        """Periodically remove leftover temp files in the klines cache directory."""
        await asyncio.sleep(60)
        patterns = (".tmp_ez_", ".tmp_ez3m_")
        while True:
            try:
                now = time.time()
                removed = 0
                for entry in await asyncio.to_thread(list, config.KLINES_CACHE_DIR.iterdir()):
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
                    self.logger.info(f" Temp cleanup: removed {removed} stale temp files from klines_cache")
            except Exception as e:
                self.logger.warning(f"Temp cleanup error: {e}")
            await asyncio.sleep(interval_seconds)

    async def cleanup(self):
        """Clean up resources before shutdown."""
        try:
            if self.session and not self.session.closed:
                await self.session.close()
                self.logger.info("HTTP session closed")
        except Exception as e:
            self.logger.error(f"Error closing HTTP session: {e}")
        
        try:
            if self.redis_client:
                await self.redis_client.aclose()
                self.logger.info("Local Redis connection closed")
        except Exception as e:
            self.logger.error(f"Error closing local Redis: {e}")

        try:
            if self.redis_client_read:
                await self.redis_client_read.aclose()
                self.logger.info("Gateway Redis connection closed")
        except Exception as e:
            self.logger.error(f"Error closing gateway Redis: {e}")


    # =======================================================================
    # FILE I/O AND API FETCH LOGIC (from 0408)
    # =======================================================================
    def _blocking_read_helper(self, fp: Path) -> str:
            with open(fp, "r", encoding="utf-8") as f:
                return f.read()

    async def _read_file_unlocked(self, fp: Path) -> pd.DataFrame:
        """Safely reads and parses a kline JSON file using resilient loader."""
        try:
            # Use the new resilient loader (handles corruption/trimming)
            data = await load_json_resilient(fp)
            
            if not data:
                return pd.DataFrame(columns=config.KLINE_COLUMNS)

            # Normalization logic (same as original, but adapted for pre-loaded dict)
            def _normalize_kline_columns(df: pd.DataFrame) -> pd.DataFrame:
                if df.empty:
                    return df
                df = df.copy()
                existing_cols = [str(col) for col in df.columns]
                if set(config.KLINE_COLUMNS).issubset(existing_cols):
                    df = df[config.KLINE_COLUMNS]
                else:
                    for col in config.KLINE_COLUMNS:
                        if col not in df.columns:
                            df[col] = pd.NA
                    df = df[config.KLINE_COLUMNS]
                return df

            # Handle different JSON structures
            klines_data = []
            if isinstance(data, dict) and 'data' in data:
                klines_data = data['data']
            elif isinstance(data, list):
                klines_data = data
            elif isinstance(data, dict) and 'klines' in data: # Handle potential Redis dumps saved to disk
                klines_data = data['klines']

            if not klines_data:
                return pd.DataFrame(columns=config.KLINE_COLUMNS)

            df = pd.DataFrame(klines_data)
            df = _normalize_kline_columns(df)
            
            # Timestamp parsing
            try:
                # Pandas is faster than recursive loop for large datasets
                df['timestamp'] = pd.to_datetime(df['timestamp'], format='ISO8601', utc=True, errors='coerce')
            except (ValueError, TypeError):
                df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', utc=True, errors='coerce')

            # Determine interval from filename for flooring
            file_interval = '3m'
            for tf in ("3m", "15m", "1h", "4h", "D"):
                if fp.name.endswith(f"_{tf}_part") or fp.name.endswith(f"_{tf}.json"):
                    file_interval = tf
                    break
            
            df['timestamp'] = self._normalize_and_floor_timestamp(df['timestamp'], file_interval)
            
            # Numeric conversion
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            return df.dropna(subset=['timestamp']).sort_values('timestamp').drop_duplicates(subset=['timestamp'], keep='last').reset_index(drop=True)

        except Exception as e:
            self.logger.warning(f"Could not read/parse {fp.name}: {e}")
            return pd.DataFrame(columns=config.KLINE_COLUMNS)

    async def _write_file_atomic(self, df: pd.DataFrame, fp: Path):
        """Wrapper for atomic_write_json accepting DataFrame."""
        try:
            df_safe = df.dropna(subset=['timestamp']).copy()
            
            # Remove future bars
            try:
                now_utc = datetime.now(timezone.utc)
                df_safe['timestamp'] = pd.to_datetime(df_safe['timestamp'], utc=True, errors='coerce')
                df_safe = df_safe[df_safe['timestamp'] <= now_utc]
            except Exception:
                pass

            # Convert to list of dicts using the standardized helper
            rows = standardize_kline_data_for_json(df_safe)
            
            # Use the new ultra-resilient write
            await atomic_write_json(fp, rows)
                
        except Exception as e:
            self.logger.error(f"Error atomic writing {fp}: {e}")
    
    async def _write_file_unlocked(self, df: pd.DataFrame, fp: Path, existing_bar_count: int = 0):
        """
        Atomically writes a DataFrame to a JSON file using atomic_write_json 
        WITH SAFEGUARDS against data loss. Non-blocking.
        """
        try:
            # 1. READ CHECK (Async I/O)
            # If file exists, read actual bar count for safety checks
            actual_existing_count = existing_bar_count
            if await asyncio.to_thread(fp.exists) and existing_bar_count == 0:
                try:
                    # We use the new resilient read here
                    temp_df = await self._read_file_unlocked(fp)
                    if not temp_df.empty:
                        actual_existing_count = len(temp_df)
                except Exception:
                    pass
            
            # 2. HEAVY LIFTING (Move to Thread)
            # We run the cleaning, filtering, and standardization in a separate thread
            # so the bot doesn't freeze while processing 10,000 candles.
            def _prepare_data_safe():
                _df_safe = df.dropna(subset=['timestamp']).copy()
                if _df_safe.empty:
                    return None, 0

                try:
                    now_utc = datetime.now(timezone.utc)
                    _df_safe['timestamp'] = pd.to_datetime(_df_safe['timestamp'], utc=True, errors='coerce')
                    _df_safe = _df_safe[_df_safe['timestamp'] <= now_utc]
                except Exception:
                    pass

                _count = len(_df_safe)
                
                # --- SAFEGUARDS (Inside thread) ---
                if actual_existing_count > 0:
                    # If we lost > 70% of data
                    if _count < actual_existing_count * 0.3:
                        return "CRITICAL_LOSS", _count
                    # If we shrunk, but result is small (< 1800 bars)
                    if _count < actual_existing_count and _count < 1800:
                        return "CRITICAL_SHRINK", _count
                
                # If we have very few bars replacing a healthy file
                if _count < 50 and actual_existing_count > 100:
                    return "CRITICAL_LOW", _count
                
                # Standardize data to List[Dict]
                # Assuming standardize_kline_data_for_json is your helper function
                return standardize_kline_data_for_json(_df_safe), _count

            # Await the thread result
            result, new_bar_count = await asyncio.to_thread(_prepare_data_safe)

            # 3. HANDLE RESULTS
            if result is None:
                return # Empty dataframe

            if isinstance(result, str):
                # Handle error codes from thread
                if result == "CRITICAL_LOSS":
                     self.logger.error(f"🚨 CRITICAL BLOCK: {fp.name}: would destroy data ({actual_existing_count} -> {new_bar_count}) - KEEPING EXISTING")
                elif result == "CRITICAL_SHRINK":
                     self.logger.error(f"🚨 CRITICAL BLOCK: {fp.name}: shrinking from {actual_existing_count} to {new_bar_count} (< 1800) - KEEPING EXISTING")
                elif result == "CRITICAL_LOW":
                     self.logger.error(f"🚨 CRITICAL BLOCK: {fp.name}: only {new_bar_count} bars vs existing {actual_existing_count} - KEEPING EXISTING")
                return

            rows = result # This is now our List[Dict]

            # 4. ATOMIC WRITE (Async I/O)
            # Ensure atomic_write_json accepts Union[dict, list] per previous fix
            await atomic_write_json(fp, rows)
            if new_bar_count < actual_existing_count:
                self.logger.warning(f"⚠️ {fp.name}: shrunk from {actual_existing_count} -> {new_bar_count} bars (but >= 1800, allowed)")
        except Exception as e:
            self.logger.error(f"Error writing unlocked {fp}: {e}", exc_info=False)            
    async def _fetch_klines_from_api_with_semaphore(self, symbol: str, interval: str, limit: int = 1500) -> pd.DataFrame:
        if not self.session:
            self.logger.error("HTTP session not initialized")
            return pd.DataFrame(columns=config.KLINE_COLUMNS)
        async with self.api_semaphore:
            async with self.fapi_semaphore:
                try:
                    api_symbol = symbol.replace("USDC", "USDT") if symbol.endswith("USDC") else symbol
                    api_interval = "1d" if interval.lower() == "d" else interval.lower()
                    url = f"{config.FAPI_BASE_URL}/klines"
                    params = {"symbol": api_symbol, "interval": api_interval, "limit": min(limit, 1500)}
                    async with self.session.get(url, params=params, timeout=10) as response:
                        if response.status != 200:
                            self.logger.error(f"API error for {symbol}:{interval} - status {response.status}")
                            return pd.DataFrame(columns=config.KLINE_COLUMNS)
                        data = await response.json()
                        if not data:
                            return pd.DataFrame(columns=config.KLINE_COLUMNS)
                        df = pd.DataFrame(data, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'])
                        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
                        for col in ['open', 'high', 'low', 'close', 'volume']:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                        df = df[config.KLINE_COLUMNS]
                        df = df.sort_values('timestamp').drop_duplicates(subset=['timestamp'], keep='last')
                        self.logger.info(f"✅ Fetched {len(df)} {interval} bars for {symbol} from API")
                        await asyncio.sleep(config.EZ_PRICES_API_SLEEP_AFTER.get(env,0.3))
                        return df
                except Exception as e:
                    self.logger.error(f"Error fetching {interval} klines for {symbol} from API: {e}")
                    return pd.DataFrame(columns=config.KLINE_COLUMNS)
                    
    async def get_klines_df_redis_first(self, symbol: str, interval: str) -> pd.DataFrame:
        log_prefix = f"[KlinesFetch][{symbol}_{interval}]"
        for name, client in (("gateway", self.redis_client_read), ("local", self.redis_client)):
            if not client:
                continue
            try:
                payload = await client.get(f"klines:{symbol}:{interval}")
                if not payload:
                    continue
                parsed = json.loads(payload)
                klines = parsed.get("klines") if isinstance(parsed, dict) else parsed
                if not klines:
                    continue
                df = pd.DataFrame(klines)
                if df.empty:
                    continue
                ts_sample = klines[0].get("timestamp") if isinstance(klines[0], dict) else None
                df = clean_kline_data(df, is_raw_api_data=isinstance(ts_sample, (int, float)))
                if df.empty:
                    continue
                self.logger.debug(f"{log_prefix} ✅ Using {name} Redis data ({len(df)} bars)")
                return df
            except Exception as e:
                self.logger.debug(f"{log_prefix} {name} Redis error: {e}")

        df_file = await self.get_klines_df(symbol, interval)
        if not df_file.empty:
            self.logger.debug(f"{log_prefix} ✅ Using file cache data ({len(df_file)} bars)")
            return df_file
        df_backup = await self._read_consolidated_backup(symbol, interval)
        if not df_backup.empty:
            existing_file = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
            existing_count = len(df_file) if not df_file.empty else 0
            await self._write_file_unlocked(df_backup, existing_file, existing_bar_count=existing_count)
            self.logger.info(f"{log_prefix} ✅ Restored from backup ({len(df_backup)} bars)")
            return df_backup
        if interval != '3m':
            self.logger.warning(f"{log_prefix} 🚨 No data in Redis, cache or backups")
            return pd.DataFrame(columns=config.KLINE_COLUMNS)
        if getattr(self, "_api_disabled_until", 0) and time.time() < self._api_disabled_until:
            self.logger.warning(f"{log_prefix} ⛔ API disabled until {self._api_disabled_until}, skipping request")
            return pd.DataFrame(columns=config.KLINE_COLUMNS)
        self.logger.info(f"{log_prefix} 🔄 No 3m data located, fetching 1500 bars from API")
        df_api = await self._fetch_klines_from_api_with_semaphore(symbol, '3m', limit=1500)
        if df_api.empty:
            self.logger.warning(f"{log_prefix} 🚨 API fetch returned no data")
            return pd.DataFrame(columns=config.KLINE_COLUMNS)
        existing_file = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
        existing_count = len(df_file) if not df_file.empty else 0
        await self._write_file_unlocked(df_api, existing_file, existing_bar_count=existing_count)
        await self.publish_to_redis(symbol, interval, df_api)
        self.logger.info(f"{log_prefix} ✅ Fetched {len(df_api)} bars from API")
        return df_api

    async def _get_redis_data(self, redis_client, symbol: str, interval: str, source_name: str) -> pd.DataFrame:
        """Get data from a specific Redis source"""
        try:
            redis_key = f"klines:{symbol}:{interval}"
            json_data_bytes = await redis_client.get(redis_key)
            if json_data_bytes:
                try:
                    payload = json.loads(json_data_bytes)
                    if payload is None:
                        return pd.DataFrame(columns=config.KLINE_COLUMNS)

                    # Handle both dict format (with metadata) and list format
                    klines_list = payload.get('klines') if isinstance(payload, dict) else payload

                    if klines_list and isinstance(klines_list, list) and len(klines_list) > 0:
                        df_redis = pd.DataFrame(klines_list)

                        # Auto-detect timestamp format: numeric (ms) vs ISO string
                        ts_sample = None
                        try:
                            if isinstance(klines_list[0], dict):
                                ts_sample = klines_list[0].get('timestamp')
                        except Exception:
                            ts_sample = None

                        is_raw_api = isinstance(ts_sample, (int, float))
                        cleaned_df = clean_kline_data(df_redis, is_raw_api_data=is_raw_api)

                        if not cleaned_df.empty:
                            return cleaned_df

                except (json.JSONDecodeError, TypeError, KeyError) as e:
                    self.logger.debug(f"Error parsing {source_name} Redis data for {symbol}:{interval}: {e}")

        except Exception as e:
            self.logger.debug(f"Error reading from {source_name} Redis for {symbol}:{interval}: {e}")

        return pd.DataFrame(columns=config.KLINE_COLUMNS)

    async def get_klines_df(self, symbol: str, interval: str) -> pd.DataFrame:
        """Reads a kline DataFrame from ALL klines directories, finding the freshest data."""
        from utils import _resolve_klines_directories, orjson_default
        
        # Get ALL klines directories in priority order
        klines_dirs = _resolve_klines_directories()
        best_df = pd.DataFrame()
        best_timestamp = pd.Timestamp(0, tz='UTC')
        best_source = None
        
        for klines_dir in klines_dirs:
            if not klines_dir or not klines_dir.exists():
                continue
                
            file_path = klines_dir / f"{symbol}_{interval}.json"
            if not file_path.exists():
                continue
                
            async with self.file_io_semaphore:
                df = await self._read_file_unlocked(file_path)
                
            if not df.empty and 'timestamp' in df.columns:
                # Find the latest timestamp in this data
                try:
                    timestamps = pd.to_datetime(df['timestamp'], format='mixed', utc=True, errors='coerce')
                    latest_ts = timestamps.max()
                    if latest_ts > best_timestamp:
                        best_df = df.copy()
                        best_timestamp = latest_ts
                        best_source = klines_dir.name
                except Exception:
                    # If timestamp parsing fails, still use this data if we don't have any
                    if best_df.empty:
                        best_df = df.copy()
                        best_source = klines_dir.name
        
        if not best_df.empty:
            if best_source:
                self.logger.debug(f"✅ Found {symbol}_{interval} data in {best_source} (latest: {best_timestamp})")
            if interval == 'D':
                now_utc = datetime.now(timezone.utc)
                if best_timestamp and (now_utc - best_timestamp.to_pydatetime()).total_seconds() > 86400 * 1.5:
                    fixed = await self._check_and_recalculate_D_from_4h(symbol)
                    if not fixed:
                        await self._ensure_D_klines_via_api(symbol)
                    best_df = await self.get_klines_df(symbol, interval)
            return best_df
        elif interval == 'D':
            fixed = await self._check_and_recalculate_D_from_4h(symbol)
            if not fixed:
                await self._ensure_D_klines_via_api(symbol)
            best_df = await self.get_klines_df(symbol, interval)
            return best_df
        
        # If no data found anywhere, return empty DataFrame
        return pd.DataFrame(columns=config.KLINE_COLUMNS)

    async def _create_synthetic_klines_from_mark_price(self, symbol: str, window: int) -> pd.DataFrame:
        """FALLBACK: Creates synthetic 1m klines from mark prices if no other data source is available."""
        if not self.redis_client:
            return pd.DataFrame(columns=config.KLINE_COLUMNS)

        try:
            # Assumes mark price is stored in a simple key, e.g., 'mark_price:BTCUSDC'
            # The value is assumed to be a JSON string like '{"price": "65000.50"}'
            mark_price_key = f"mark_price:{symbol}"
            raw_data = await self.redis_client.get(mark_price_key)
            if not raw_data:
                self.logger.debug(f"Mark price key not found for {symbol}")
                return pd.DataFrame(columns=config.KLINE_COLUMNS)

            data = json.loads(raw_data)
            price = float(data.get('price', 0))

            if price == 0:
                return pd.DataFrame(columns=config.KLINE_COLUMNS)

            # Create synthetic OHLCV data for the last `window` minutes
            now_utc = datetime.now(timezone.utc)
            # Align to the beginning of the current minute
            end_minute = now_utc.replace(second=0, microsecond=0)

            timestamps = [end_minute - timedelta(minutes=i) for i in range(window)]
            timestamps.reverse()  # Ensure data is in ascending chronological order

            synthetic_data = [{
                'timestamp': ts,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': 0.0
            } for ts in timestamps]

            return pd.DataFrame(synthetic_data, columns=config.KLINE_COLUMNS)
        except Exception as e:
            self.logger.warning(f"Could not create synthetic klines for {symbol}: {e}")
            return pd.DataFrame(columns=config.KLINE_COLUMNS)

    # async def get_recent_1m_df(self, symbol: str, window: int = 400) -> pd.DataFrame:
    #     if self.disable_1m:
    #         return pd.DataFrame(columns=config.KLINE_COLUMNS)

    #     dfs: List[pd.DataFrame] = []
    #     redis_sources = []
    #     if self.redis_client:
    #         redis_sources.append(('local', self.redis_client))
    #     # Add gateway Redis if available and healthy
    #     if self.redis_client_read:
    #         try:
    #             # Quick health check before adding to sources
    #             await self.redis_client_read.ping()
    #             redis_sources.append(('gateway', self.redis_client_read))
    #         except Exception as e:
    #             self.logger.debug(f"Gateway Redis health check failed: {e}, removing from sources")
    #             self.redis_client_read = None

    #     source_names = [name for name, _ in redis_sources]
    #     if not hasattr(self, '_logged_sources_1m'):
    #         self._logged_sources_1m = set()
        
    #     source_key = f"{symbol}_1m"
    #     if source_key not in self._logged_sources_1m:
    #     #    self.logger.info(f"Available Redis sources: {source_names}")
    #         self._logged_sources_1m.add(source_key)
        
    #     # Give warnings if sources are missing
    #     if 'local' not in source_names:
    #         self.logger.warning(f"WARNING: Local Redis not available for {symbol}")
    #     if 'gateway' not in source_names:
    #         self.logger.warning(f"WARNING: Gateway Redis not available for {symbol}")
        
    #     if not redis_sources:
    #         self.logger.warning(f"No Redis sources available! Both local and gateway Redis are None.")
    #         # Only fallback to disk cache if absolutely no Redis sources exist
    #         self.logger.info(f"Redis miss; using disk cache as fallback.")
    #         fp = config.KLINES_CACHE_DIR / f"{symbol}_1m.json"
    #         df_file = await self._read_file_unlocked(fp)
    #         return df_file

    #     # # Add gateway Redis if available
    #     if self.redis_client_read:
    #         redis_sources.append(('gateway', self.redis_client_read))

    #     # Try all available Redis sources and collect the best data
    #     best_redis_df = None
    #     best_timestamp = None
    #     best_source = None
        
    #     for source_name, redis_client in redis_sources:
    #         try:
    #             redis_key = f"klines:{symbol}:1m"
    #             raw = await redis_client.get(redis_key)
    #             if raw:
    #                 try:
    #                     redis_payload = json.loads(raw)
    #                     if redis_payload is None:
    #                         continue
    #                     if isinstance(redis_payload, dict) and 'klines' in redis_payload:
    #                         redis_list = redis_payload['klines']
    #                     else:
    #                         redis_list = redis_payload  # Fallback for old list format
    #                 except (json.JSONDecodeError, TypeError):
    #                     continue
    #                 df_redis = pd.DataFrame(redis_list, columns=config.KLINE_COLUMNS)
    #                 if not df_redis.empty:
    #                     ts_series = pd.Series(df_redis['timestamp'].astype(str).str.replace(r"\+00:00Z$","+00:00", regex=True))
    #                     # Handle timestamps with optional microseconds using flexible format
    #                     df_redis['timestamp'] = pd.to_datetime(ts_series, utc=True, errors='coerce')
                        
    #                     # Check if this data is better than what we have
    #                     if not df_redis.empty:
    #                         last_timestamp = df_redis['timestamp'].iloc[-1]
    #                         if best_redis_df is None or last_timestamp > best_timestamp:
    #                             best_redis_df = df_redis
    #                             best_timestamp = last_timestamp
    #                             best_source = source_name
    #                             self.logger.debug(f"✅ {source_name} Redis candidate: {len(df_redis)} 1m bars, last ts: {last_timestamp}")
    #         except Exception as e:
    #             self.logger.debug(f"{source_name} Redis read failed for {symbol} 1m: {e}")
        
    #     # Use the best Redis data if we found any
    #     if best_redis_df is not None:
    #         dfs.append(best_redis_df)
    #         self.logger.info(f"📊 Using {best_source} Redis data for {symbol} 1m ({len(best_redis_df)} bars, last ts: {best_timestamp})")
    #         if len(redis_sources) > 1:
    #             self.logger.info(f"📊 Data quality: {len(redis_sources)} sources checked, {best_source} selected as best.")

    #     fp = config.KLINES_CACHE_DIR / f"{symbol}_1m.json"
    #     df_file = await self._read_file_unlocked(fp)
    #     if not df_file.empty:
    #         dfs.append(df_file.tail(window))
        
    #     # If we have some data, return it
    #     if dfs:
    #         merged = pd.concat(dfs, ignore_index=True)
    #         merged = merged.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp').tail(window)
    #         return merged.reset_index(drop=True)
        
    #     # FALLBACK: Create synthetic klines from mark prices to prevent ez_indicators from suffocating
    #     try:
    #         synthetic_df = await self._create_synthetic_klines_from_mark_price(symbol, window)
    #         if not synthetic_df.empty:
    #             self.logger.info(f" Created synthetic {window} 1m klines for {symbol} from mark price fallback")
    #             return synthetic_df
    #     except Exception as e:
    #         self.logger.warning(f"Mark price fallback failed for {symbol}: {e}")
        
    #     return pd.DataFrame(columns=config.KLINE_COLUMNS)

    def _normalize_raw_klines(self, payload) -> pd.DataFrame:
        if not payload:
            return pd.DataFrame(columns=config.KLINE_COLUMNS)
        df = pd.DataFrame(payload)
        if df.empty:
            return pd.DataFrame(columns=config.KLINE_COLUMNS)

        if isinstance(df.columns, pd.RangeIndex):
            col_count = len(df.columns)
            raw_cols = [
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades',
                'taker_buy_base', 'taker_buy_quote', 'ignore'
            ]
            take = min(col_count, len(raw_cols))
            df.columns = raw_cols[:take]

        if 'timestamp' not in df.columns:
            if 'open_time' in df.columns:
                df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms', utc=True, errors='coerce')
            else:
                return pd.DataFrame(columns=config.KLINE_COLUMNS)
        else:
            # Handle ISO8601 format with 'Z' suffix (UTC timezone)
            try:
                df['timestamp'] = pd.to_datetime(df['timestamp'], format='ISO8601', utc=True, errors='coerce')
            except (ValueError, TypeError):
                # Fallback to mixed format for older pandas versions or non-standard formats
                df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', utc=True, errors='coerce')

        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            else:
                return pd.DataFrame(columns=config.KLINE_COLUMNS)

        df = df.dropna(subset=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        if df.empty:
            return df
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df = df.sort_values('timestamp')
        return df
            
    async def publish_to_redis(self, symbol, interval, final_df):
        """ONLY PUBLISH IF WE HAVE THE LATEST BAR"""
        if not self.redis_client or final_df.empty:
            return
            
        if not await self._has_latest_closed_bar(symbol, interval):
            cache_key = f"resample_attempt_{symbol}_{interval}"
            now = time.time()
            if cache_key not in self._missing_bar_attempts or now - self._missing_bar_attempts[cache_key] > 60:
                self._missing_bar_attempts[cache_key] = now
                await self._resample_missing_latest_bar(symbol, interval)
            
        try:
            symbol = force_usdc_if_needed(symbol, getattr(self, "live_usdc_pairs", set()))
            redis_key = f"klines:{symbol}:{interval}"
            df_for_redis = final_df.tail(1500).copy()
            df_for_redis['timestamp'] = df_for_redis['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            payload = {
                "symbol": symbol, 
                "interval": interval,
                "published_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "klines": df_for_redis.to_dict(orient='records')  }
            
            expiry = REDIS_EXPIRY_SECONDS * 6
            await self.redis_client.setex(redis_key, expiry, json.dumps(payload))
            
            notification = {'symbol': symbol, 'interval': interval, 'action': 'klines_updated'}
            await self.redis_client.publish("klines_updates", json.dumps(notification))

        except Exception as e:
            self.logger.warning(f"Redis publish failed for {symbol}:{interval}: {e}")

    async def merge_and_write_df(self, df_new: pd.DataFrame, symbol: str, interval: str):
        """Merge new klines with existing data and write to file WITH SAFEGUARDS against data loss.
        
        CRITICAL SAFEGUARDS:
        - Never write files with < 100 bars
        - Never shrink existing files by more than 50%
        - Always pass existing_bar_count to _write_file_unlocked for validation
        """
        if df_new is None or df_new.empty:
            return
        df_new = df_new.drop_duplicates(subset=['timestamp'], keep='last')
        from utils import _resolve_klines_directories, orjson_default
        klines_dirs = _resolve_klines_directories()
        if not klines_dirs:
            klines_dirs = [config.KLINES_CACHE_DIR]
        primary_fp = klines_dirs[0] / f"{symbol}_{interval}.json"
        lock = self._file_write_locks.setdefault(str(primary_fp), asyncio.Lock())
        async with lock:
            async with self.file_io_semaphore:
                df_new['timestamp'] = pd.to_datetime(df_new['timestamp'], utc=True, errors='coerce')
                existing_frames = []
                existing_counts: Dict[str, int] = {}
                for directory in klines_dirs:
                    file_path = directory / f"{symbol}_{interval}.json"
                    df_existing = await self._read_file_unlocked(file_path)
                    existing_counts[str(file_path)] = len(df_existing) if not df_existing.empty else 0
                    if not df_existing.empty:
                        df_existing['timestamp'] = pd.to_datetime(df_existing['timestamp'], utc=True, errors='coerce')
                        existing_frames.append(df_existing)
                if existing_frames:
                    existing_df = pd.concat(existing_frames, ignore_index=True)
                    existing_df = existing_df.dropna(subset=['timestamp'])
                    df_new = pd.concat([existing_df, df_new], ignore_index=True)
                final_df = self._filter_future_and_align(df_new, interval)
            max_existing = max(existing_counts.values(), default=0)
            if final_df.empty:
                return
            if existing_frames and len(final_df) < sum(len(df) for df in existing_frames) * 0.5:
                self.logger.warning(f"🚨 BLOCKED write for {symbol}_{interval}: would shrink existing aggregate - KEEPING EXISTING")
                return
            if len(final_df) < 100 and max_existing >= 100:
                self.logger.warning(f"🚨 BLOCKED write for {symbol}_{interval}: only {len(final_df)} bars (minimum 100 required) - KEEPING EXISTING")
                return
            for directory in klines_dirs:
                target_path = directory / f"{symbol}_{interval}.json"
                await self._write_file_unlocked(final_df, target_path, existing_bar_count=existing_counts.get(str(target_path), 0))
        await self.publish_to_redis(symbol, interval, final_df.reset_index(drop=True))

    async def _append_records_to_cache(self, symbol: str, interval: str, records, source: str) -> bool:
        """Merge incoming klines into the local cache without overwriting history."""
        if records is None:
            return False
        try:
            df_candidate = records.copy() if isinstance(records, pd.DataFrame) else pd.DataFrame(records)
        except Exception as e:
            self.logger.warning(f"{source}: failed to convert incoming data for {symbol}:{interval}: {e}")
            return False

        if df_candidate.empty:
            return False

        raw_cols = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume', 'trades', 'taker_buy_base', 'taker_buy_quote', 'ignore']
        if 'timestamp' not in df_candidate.columns:
            rename_map = {}
            for idx, col in enumerate(df_candidate.columns):
                if idx < len(raw_cols):
                    rename_map[col] = raw_cols[idx]
            if rename_map:
                df_candidate = df_candidate.rename(columns=rename_map)
        if 'timestamp' not in df_candidate.columns and 'open_time' in df_candidate.columns:
            df_candidate['timestamp'] = df_candidate['open_time']

        required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        missing = [col for col in required_cols if col not in df_candidate.columns]
        if missing:
            self.logger.warning(f"{source}: missing columns {missing} for {symbol}:{interval}")
            return False

        try:
            extra_cols = sorted([col for col in df_candidate.columns if col not in required_cols and col != 'open_time'])
            if extra_cols:
                df_candidate = df_candidate.drop(columns=extra_cols)

            ts_series = df_candidate['timestamp']
            if pd.api.types.is_numeric_dtype(ts_series):
                df_candidate['timestamp'] = pd.to_datetime(ts_series, unit='ms', utc=True, errors='coerce')
            else:
                # Handle ISO8601 format with 'Z' suffix (UTC timezone)
                # Use format='ISO8601' to properly parse strings like "2025-10-27T20:00:00.000000Z"
                try:
                    df_candidate['timestamp'] = pd.to_datetime(ts_series, format='ISO8601', utc=True, errors='coerce')
                except (ValueError, TypeError):
                    # Fallback to mixed format for older pandas versions or non-standard formats
                    df_candidate['timestamp'] = pd.to_datetime(ts_series, format='mixed', utc=True, errors='coerce')

            for col in required_cols[1:]:
                df_candidate[col] = pd.to_numeric(df_candidate[col], errors='coerce')

            df_candidate = df_candidate.dropna(subset=required_cols)
            if df_candidate.empty:
                self.logger.warning(f"{source}: no valid klines to append for {symbol}:{interval}")
                return False

            df_candidate = df_candidate.sort_values('timestamp').drop_duplicates(subset=['timestamp'], keep='last')

            await self.merge_and_write_df(df_candidate[required_cols], symbol, interval)
            self.logger.info(f"✅ {source}: appended {len(df_candidate)} bars to {symbol}:{interval}")
            return True
        except Exception as e:
            self.logger.warning(f"{source}: failed to merge klines for {symbol}:{interval}: {e}")
            return False

    async def fetch_klines_in_chunks(self, symbol: str, interval: str, total_bars_needed: int, end_time_ms: Optional[int] = None) -> pd.DataFrame:
        """Fetches klines from Binance in chunks using the proper /klines endpoint; returns clean ascending DataFrame."""
        all_dfs = []
        remaining_bars = total_bars_needed
        current_end_time = end_time_ms
        last_fetch_size = 0

        safety_cutoff = datetime.now(timezone.utc) - timedelta(days=548)

        # Try to fill from backups while waiting for API - run this once at the start
        backup_fill_task = None
        if total_bars_needed > 500:  # Only for larger requests
            backup_fill_task = asyncio.create_task(
                self._async_fill_from_backups_while_waiting(symbol, interval)
            )

        while remaining_bars > 0:
            fetch_size = min(remaining_bars, MAX_BARS_PER_API_FETCH)
            async with self.api_semaphore:
                # Use USDT for API calls as per Binance convention, even for USDC pairs
                api_symbol = symbol.replace("USDC", "USDT")
                api_interval = "1d" if interval == "D" else interval
                params = {"symbol": api_symbol, "interval": api_interval, "limit": fetch_size}
                if current_end_time:
                    params['endTime'] = current_end_time
                

                
                # Check session health before making API call
                if not self.session or self.session.closed:
                    self.logger.warning(f" Session is closed, reinitializing for {api_symbol} {api_interval}")
                    try:
                        self.session = await self._init_http_session()
                    except Exception as e:
                        self.logger.error(f" Failed to reinitialize session for {api_symbol} {api_interval}: {e}")
                        break
                async with self.fapi_semaphore:
                    try:
                        async with self.session.get(f"{config.FAPI_BASE_URL}/klines", params=params) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if not data or not isinstance(data, list):
                                    break
                                df = self._normalize_raw_klines(data)
                                if df.empty:
                                    break
                                df = df[df['timestamp'] >= safety_cutoff]
                                if df.empty:
                                    break
                                all_dfs.append(df)
                                # Page backward by setting endTime to just before the first returned candle
                                current_end_time = int(df.iloc[0]['timestamp'].timestamp() * 1000) - 1
                                remaining_bars -= len(df)
                                last_fetch_size = len(df)
                                if len(df) < fetch_size:
                                    self.logger.debug(f"Reached beginning of available data for {api_symbol} {api_interval}: fetched {len(df)} bars, requested {fetch_size}")
                                    break
                                # If we've been getting the same small amount of data multiple times, we've likely reached the beginning
                                elif len(df) < 100 and last_fetch_size == len(df):
                                    self.logger.debug(f"Consistently getting small batches for {api_symbol} {api_interval}: {len(df)} bars. May have reached beginning of available data.")
                                    # Don't break immediately - give it one more try
                                # Gentle pacing
                                await asyncio.sleep(API_REQUEST_DELAY_SECONDS)
                            else:
                                self.logger.warning(f"API Error for {api_symbol} {api_interval}: {resp.status}")
                                break
                    except aiohttp.ClientError as e:
                        self.logger.error(f"Client error for {api_symbol} {api_interval}: {e}")
                        self._api_disabled_until = time.time() + API_DISABLE_SECONDS
                        self.logger.warning(f"API suspended for {API_DISABLE_SECONDS}s due to client error")
                        break
                    except Exception as e:
                        self.logger.error(f"Critical fetch error for {api_symbol} {api_interval}: {e}")
                        self._api_disabled_until = time.time() + API_DISABLE_SECONDS
                        self.logger.warning(f"API suspended for {API_DISABLE_SECONDS}s due to critical error")
                        break
        if not all_dfs:
            # Cancel background backup task if no API data was fetched
            if backup_fill_task and not backup_fill_task.done():
                backup_fill_task.cancel()
            return pd.DataFrame(columns=config.KLINE_COLUMNS)
        out = pd.concat(all_dfs, ignore_index=True)
        out = out[out['timestamp'] >= safety_cutoff]
        out = out.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp').reset_index(drop=True)

        # Cancel background backup task since we got API data
        if backup_fill_task and not backup_fill_task.done():
            backup_fill_task.cancel()

        return self._sanitize_klines(out, interval)


    def _normalize_and_floor_timestamp(self, ts_column: pd.Series, interval: str) -> pd.Series:
        """
        (THE GOLDEN RULE) Normalizes timestamps to a canonical interval using modern
        frequency strings ('min', 'H', 'D'). This ensures perfect consistency.
        """
        ts_column = pd.to_datetime(ts_column, utc=True, errors='coerce')
        freq_map = {
            '3m': '3min', 
            '15m': '15min', 
            '1h': '1h', 
            '4h': '4h', 
            'D': 'D'  # 'D' is correct for daily frequency
        }
        freq = freq_map.get(interval, '3min') 
        
        return ts_column.dt.floor(freq)
    async def _init_http_session(self) -> aiohttp.ClientSession:
        """Creates an aiohttp session. Attempts to bind to dedicated IP first; if that
        fails (e.g., network unreachable) it falls back to default binding
        immediately without blocking the rest of startup."""
        dedicated_ip = "49.13.32.80"
        timeout = aiohttp.ClientTimeout(total=60, connect=15, sock_read=30)  # Increased timeout for better reliability
        try:
            connector = aiohttp.TCPConnector(
                local_addr=(dedicated_ip, 0),
                limit=100,  # Connection pool limit
                limit_per_host=config.EZ_PRICES_LIMIT_PER_HOST.get(env,5),  # Per-host connection limit
                ttl_dns_cache=300,  # DNS cache TTL
                keepalive_timeout=60,  # Keep-alive timeout
                enable_cleanup_closed=True  # Clean up closed connections
            )
            session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            # quick connectivity test
            async with self.fapi_semaphore:
                async with session.get("https://fapi.binance.com/fapi/v1/ping") as resp:
                    if resp.status == 200:
                        self.logger.info(f" Using dedicated IP {dedicated_ip} for API calls.")
                        return session
        except Exception as e:
            self.logger.warning(f" Dedicated IP {dedicated_ip} not usable ({e}). Falling back to default IP.")
        # Fallback: any available IP
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=config.EZ_PRICES_LIMIT_PER_HOST.get(env,5),
            ttl_dns_cache=300,
            keepalive_timeout=60,
            enable_cleanup_closed=True
        )
        return aiohttp.ClientSession(connector=connector, timeout=timeout)

    # =======================================================================
    # CORE FEATURE: AGGRESSIVE GAP FILLING (from 0408)
    # =======================================================================
    async def aggressive_gap_filling_task(self):
        await asyncio.sleep(300) # Initial delay
        
        # This set will keep track of symbols that we've confirmed have no more history.
        # It will be reset each time the script restarts, which is a safe approach.
        history_fully_fetched = set()

        while True:
            self.logger.info(" AGGRESSIVE GAP FILLING: Starting scan for missing klines...")
            start_time = time.time()
            
            symbols_to_check = [s for s in self.symbols_to_run if s not in history_fully_fetched]
            
            if not symbols_to_check:
                self.logger.info(" All symbols have been fully backfilled. Next major check in 15 minutes.")
                await asyncio.sleep(900)
                history_fully_fetched.clear() # Periodically re-check everything in case of data corruption.
                continue

            gap_queue = asyncio.Queue()

            # Scan all symbols and timeframes to identify potential backfill opportunities
            for symbol in symbols_to_check:
                for interval in self.active_timeframes:
                    # Respect fetch switches: skip 1m unless enabled; skip 3m unless enabled
                    # 3m backfill re-enabled — deep history needed for backtesting
                    # if interval == "3m":
                    #     continue

                    try:
                        existing_df = await self.get_klines_df(symbol, interval)
                        target_bars = self.ideal_bars_targets.get(interval, 0)
                        # Always fetch full 1500 klines for incomplete files
                        if len(existing_df) < target_bars:
                            oldest_ts = existing_df['timestamp'].min() if not existing_df.empty else None
                            bars_to_fetch = 1500  # Always fetch full 1500 klines for incomplete symbols
                            end_ts_ms = int(oldest_ts.timestamp() * 1000) if oldest_ts else None
                            await gap_queue.put((symbol, interval, bars_to_fetch, end_ts_ms))
                            self.logger.info(f" BACKFILL queued for {symbol}_{interval}: has {len(existing_df)}/{target_bars}, will fetch 1500 bars.")
                    except Exception as e:
                        self.logger.error(f"Error scanning {symbol}_{interval} for gaps: {e}")
            
            if gap_queue.empty():
                self.logger.info(" No gaps found in current workload. Next cycle in 3 minutes.")
                await asyncio.sleep(180)
                continue
                
            self.logger.info(f" Found {gap_queue.qsize()} potential backfills. Starting fetch workers...")

            async def worker(worker_id):
                while not gap_queue.empty():
                    try:
                        symbol, interval, bars_to_fetch, end_ts = await gap_queue.get()
                        
                        # Get the bar count BEFORE the fetch
                        pre_fetch_count = len(await self.get_klines_df_redis_first(symbol, interval)) # <-- Use new function

                        fetched_df = await self.fetch_klines_in_chunks(symbol, interval, bars_to_fetch, end_ts)
                        
                        if not fetched_df.empty:
                            await self.merge_and_write_df(fetched_df, symbol, interval)
                            
                            # Get the bar count AFTER the fetch
                            post_fetch_count = len(await self.get_klines_df_redis_first(symbol, interval)) # <-- Use new function
                            bars_added = post_fetch_count - pre_fetch_count

                            if bars_added > 0:
                                self.logger.info(f" Worker-{worker_id}: Successfully added {bars_added} bars to {symbol}_{interval}")
                            else:
                                # If we tried to fetch but added 0 bars, we've hit the start of history.
                                self.logger.info(f" Worker-{worker_id}: {symbol}_{interval} hit start of history. No new bars added.")
                                history_fully_fetched.add(symbol)
                        else:
                            # If the fetch function returned nothing, we are definitely done.
                            self.logger.info(f" Worker-{worker_id}: {symbol}_{interval} confirmed start of history. No more data available.")
                            history_fully_fetched.add(symbol)

                        gap_queue.task_done()
                    except Exception as e:
                        self.logger.error(f"Worker-{worker_id} error: {e}", exc_info=True)

            await asyncio.gather(*(worker(i) for i in range(API_CONCURRENCY)))
            
            elapsed = time.time() - start_time
            self.logger.info(f" AGGRESSIVE GAP FILLING CYCLE COMPLETE in {elapsed:.1f}s. Next cycle in 3 minutes.")
            gc.collect()
            await asyncio.sleep(180)

    # =======================================================================
    # REAL-TIME BOUNDARY DETECTION & RESAMPLING
    # =======================================================================

    async def kline_update_listener(self):
        """
        Real-time event-driven resampler that listens for 3m kline updates
        and triggers HTF resampling at boundary minutes.
        """
        if not self.redis_client:
            self.logger.error("Cannot start kline listener: Redis client not available.")
            return

        pubsub = self.redis_client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe("klines_updates")
        self.logger.info("🔄 Real-time Resampler started. Listening for 3m boundary events...")
        
        # Track last processed boundaries to avoid duplicates
        last_boundaries = {}
        
        while True:
            try:
                message = await pubsub.get_message(timeout=30.0)
                if message is None:
                    continue
                                
                notification = json.loads(message['data'])
                symbol = notification.get('symbol')
                interval = notification.get('interval')
                
                if not symbol or interval != '3m':
                    continue
                
                # Get the latest 3m bar timestamp from Redis
                latest_3m_ts = await self._get_latest_3m_timestamp(symbol)
                if not latest_3m_ts:
                    continue
                
                # Check if this is a boundary minute that should trigger HTF resampling
                await self._check_and_resample_htf_boundaries(symbol, latest_3m_ts, last_boundaries)
                            
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.logger.error(f"Error in kline_update_listener: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _get_latest_3m_timestamp(self, symbol: str) -> Optional[pd.Timestamp]:
        """Get the latest 3m bar timestamp from Redis."""
        try:
            redis_key = f"klines:{symbol}:3m"
            redis_data = await self.redis_client.get(redis_key)
            if not redis_data:
                return None
            data = json.loads(redis_data)
            klines = data.get('klines', [])
            if not klines:
                return None
            latest_bar = klines[-1]
            timestamp_str = latest_bar.get('timestamp')
            if not timestamp_str:
                return None
            return pd.to_datetime(timestamp_str, utc=True)
        except Exception as e:
            self.logger.debug(f"Error getting latest 3m timestamp for {symbol}: {e}")
            return None
    async def _has_specific_3m_bar(self, symbol: str, target_time: pd.Timestamp) -> bool:
        """Check if we have the specific 3m bar at the target time"""
        try:
            df_3m = await self.get_klines_df_redis_first(symbol, '3m')
            if df_3m.empty:
                return False
            target_timestamp = target_time.timestamp() * 1000
            return target_timestamp in df_3m['timestamp'].values
        except Exception as e:
            self.logger.error(f"Error checking specific 3m bar for {symbol}: {e}")
            return False

    async def _has_latest_closed_bar(self, symbol: str, interval: str) -> bool:
        """Check if we have the latest COMPLETED bar for this timeframe - RELAXED VERSION"""
        try:
            now = datetime.now(timezone.utc)
            
            # Calculate when the latest bar should have closed
            if interval == '3m':
                # Latest 3m bar closed 3 minutes ago
                latest_completed = now.replace(second=0, microsecond=0) - timedelta(minutes=3)
            elif interval == '15m':
                # Latest 15m bar closed 15 minutes ago  
                latest_completed = now.replace(second=0, microsecond=0) - timedelta(minutes=15)
            elif interval == '1h':
                # Latest 1h bar closed 1 hour ago
                latest_completed = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
            elif interval == '4h':
                # Latest 4h bar closed 4 hours ago
                latest_hour = (now.hour // 4) * 4
                latest_completed = now.replace(hour=latest_hour, minute=0, second=0, microsecond=0) - timedelta(hours=4)
            elif interval == 'D':
                # Latest daily bar closed at previous UTC midnight
                latest_completed = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
            else:
                return False
            
            # Use priority order for getting klines data
            df = await self.get_klines_df_redis_first(symbol, interval)
            if df.empty:
                self.logger.debug(f"No data available for {symbol}_{interval}")
                return False
            
            df_timestamps = pd.to_datetime(df['timestamp'], format='mixed', utc=True, errors='coerce')
            
            # # More relaxed check: look for bars within 2 intervals
            # if interval == '3m':
            #     tolerance = timedelta(minutes=6)  # 2 * 3min
            if interval == '15m':
                tolerance = timedelta(minutes=30)  # 2 * 15min  
            elif interval == '1h':
                tolerance = timedelta(hours=2)     # 2 * 1h
            elif interval == '4h':
                tolerance = timedelta(hours=8)     # 2 * 4h
            elif interval == 'D':
                tolerance = timedelta(days=2)      # 2 * 1D
            else:
                tolerance = timedelta(minutes=10)
            
            # Check if we have any bar within the tolerance window
            time_diff = abs(df_timestamps - latest_completed)
            has_latest = (time_diff <= tolerance).any()
            
            if not has_latest:
                # Show the closest bar we have (reduce logging frequency)
                closest_idx = time_diff.idxmin()
                closest_time = df_timestamps.iloc[closest_idx]
                time_diff_minutes = (latest_completed - closest_time).total_seconds() / 60
                # Only log warning every 5 minutes to reduce spam
                cache_key = f"missing_bar_log_{symbol}_{interval}"
                now = time.time()
                if cache_key not in self._missing_bar_attempts or now - self._missing_bar_attempts[cache_key] > 300:
                    self.logger.warning(f"⚠️  Missing recent {interval} bar for {symbol}: latest should be {latest_completed}, closest is {closest_time} ({time_diff_minutes:.1f} minutes ago)")
                    self._missing_bar_attempts[cache_key] = now
            else:
                # Find the actual latest bar we have (only debug level)
                latest_idx = time_diff.idxmin()
                actual_latest = df_timestamps.iloc[latest_idx]
                self.logger.debug(f"✅ Found latest {interval} bar for {symbol}: {actual_latest}")

            return has_latest

        except Exception as e:
            self.logger.error(f"Error checking latest bar for {symbol}_{interval}: {e}")
            return True  # Return True on error to avoid blocking updates
    # async def _has_latest_closed_bar(self, symbol: str, interval: str) -> bool:
    #     """CRITICAL: Check if we have the latest COMPLETED bar for this timeframe"""
    #     try:
    #         now = datetime.now(timezone.utc)
    #         if interval == '3m':
    #             latest_minute = (now.minute // 3) * 3
    #             latest_completed = now.replace(minute=latest_minute, second=0, microsecond=0) - timedelta(minutes=3)
    #         elif interval == '15m':
    #             latest_minute = (now.minute // 15) * 15
    #             latest_completed = now.replace(minute=latest_minute, second=0, microsecond=0) - timedelta(minutes=15)
    #         elif interval == '1h':
    #             latest_completed = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    #         elif interval == '4h':
    #             latest_hour = (now.hour // 4) * 4
    #             latest_completed = now.replace(hour=latest_hour, minute=0, second=0, microsecond=0) - timedelta(hours=4)
    #         elif interval == 'D':
    #             latest_completed = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    #         else:
    #             return False
    #         # Use priority order for getting klines data
    #         df = await self.get_klines_df_redis_first(symbol, interval)
    #         if df.empty:
    #             return False
    #         df_timestamps = pd.to_datetime(df['timestamp'], utc=True)
            
    #         # More robust check: look for bars within a reasonable time window
    #         # Allow up to 1 interval tolerance for timing differences
    #         if interval == '3m':
    #             tolerance = timedelta(minutes=3)
    #         elif interval == '15m':
    #             tolerance = timedelta(minutes=15)
    #         elif interval == '1h':
    #             tolerance = timedelta(hours=1)
    #         elif interval == '4h':
    #             tolerance = timedelta(hours=4)
    #         elif interval == 'D':
    #             tolerance = timedelta(days=1)
    #         else:
    #             tolerance = timedelta(minutes=1)
            
    #         # Check if we have any bar within the tolerance window
    #         time_diff = abs(df_timestamps - latest_completed)
    #         has_latest = (time_diff <= tolerance).any()
            
    #         if not has_latest:
    #             # Show the closest bar we have
    #             closest_idx = time_diff.idxmin()
    #             closest_time = df_timestamps.iloc[closest_idx]
    #             self.logger.error(f"🚨 MISSING LATEST {interval} BAR: {symbol} - latest completed should be {latest_completed}, closest we have is {closest_time}")
    #         else:
    #             # Find the actual latest bar we have
    #             latest_idx = time_diff.idxmin()
    #             actual_latest = df_timestamps.iloc[latest_idx]
    #             self.logger.debug(f"✅ Found latest {interval} bar for {symbol}: {actual_latest}")

    #         return has_latest

    #     except Exception as e:
    #         self.logger.error(f"Error checking latest bar for {symbol}_{interval}: {e}")
    #         return False

    async def _resample_missing_latest_bar(self, symbol: str, interval: str) -> bool:
        """Resample the missing latest bar from lower timeframe data using existing cascading functions. Returns True if successful."""
        try:
            # Check if we recently attempted this symbol/interval combination
            cache_key = f"{symbol}_{interval}"
            now = time.time()
            if cache_key in self._missing_bar_attempts:
                last_attempt = self._missing_bar_attempts[cache_key]
                if now - last_attempt < self._missing_bar_cooldown:
                    self.logger.debug(f"⏭️ Skipping {symbol}_{interval} - recent attempt {(now - last_attempt):.0f}s ago")
                    return False
            
            # Clean up old cache entries periodically
            if now - self._last_cache_cleanup > 3600:  # Clean every hour
                self._cleanup_missing_bar_cache(now)
                self._last_cache_cleanup = now
            
            # Record this attempt
            self._missing_bar_attempts[cache_key] = now
            
            # Use the existing cascading resample methods - much more efficient than API calls!
            if interval == '15m':
                await self._cascade_3m_to_15m(symbol)
            elif interval == '1h':
                await self._cascade_15m_to_1h(symbol)
            elif interval == '4h':
                await self._cascade_1h_to_4h(symbol)
            elif interval == 'D':
                await self._cascade_4h_to_D(symbol)
            else:
                self.logger.warning(f"❌ No resampling method available for {interval}")
                return False

            # Verify we now have the latest bar after resampling
            if await self._has_latest_closed_bar(symbol, interval):
                self.logger.info(f"✅ Successfully resampled missing {interval} bar for {symbol}")
                return True

            self.logger.warning(f"❌ Still missing {interval} bar after resampling for {symbol}. Forcing backup + cascade repair.")
            if await self._recover_symbol_from_backups(symbol, ("3m", interval)):
                await self._resample_all_htf_from_3m(symbol)
                if await self._has_latest_closed_bar(symbol, interval):
                    self.logger.info(f"✅ Backup recovery resolved missing {interval} bar for {symbol}")
                    return True

            self.logger.info(f"🔄 Attempting targeted fetch for {symbol}_{interval}")
            try:
                fetched_df = await self.fetch_klines_in_chunks(symbol, interval, 1500, end_time_ms=None)
                if fetched_df.empty:
                    self.logger.error(f"❌ Targeted fetch returned empty data for {symbol}_{interval}")
                    return False
                cleaned_df = self._sanitize_klines(fetched_df, interval)
                if cleaned_df.empty:
                    self.logger.error(f"❌ Targeted fetch sanitized to empty for {symbol}_{interval}")
                    return False
                await self.merge_and_write_df(cleaned_df, symbol, interval)
                await self._resample_all_htf_from_3m(symbol)
                ok = await self._has_latest_closed_bar(symbol, interval)
                if ok:
                    self.logger.info(f"✅ Targeted fetch repaired {symbol}_{interval}")
                else:
                    self.logger.error(f"❌ Still missing {interval} bar for {symbol} after targeted fetch")
                return ok
            except Exception as api_e:
                self.logger.error(f"❌ Targeted fetch failed for {symbol}_{interval}: {api_e}")
                return False

        except Exception as e:
            self.logger.error(f"Error resampling latest bar for {symbol}_{interval}: {e}")
            return False

    def _cleanup_missing_bar_cache(self, now: float) -> None:
        """Clean up old entries from the missing bar attempts cache."""
        try:
            expired_keys = [key for key, timestamp in self._missing_bar_attempts.items() 
                          if now - timestamp > self._missing_bar_cooldown * 2]
            for key in expired_keys:
                del self._missing_bar_attempts[key]
            if expired_keys:
                self.logger.debug(f"🧹 Cleaned up {len(expired_keys)} expired cache entries")
        except Exception as e:
            self.logger.error(f"Error cleaning up missing bar cache: {e}")

    async def _resample_15m_from_3m_real_time(self, symbol: str, boundary_timestamp: pd.Timestamp):
        try:
            # Check for the NEW 3m kline that just arrived at the boundary
            target_3m_time = boundary_timestamp.floor('3min')
            
            # Retry logic: wait for the 3m bar to arrive (max 10 retries, 1 second apart)
            max_retries = 10
            for attempt in range(max_retries):
                if await self._has_specific_3m_bar(symbol, target_3m_time):
                    break
                if attempt < max_retries - 1:
                    self.logger.debug(f"⏳ Waiting for 3m bar {target_3m_time} for {symbol} (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(1)
                # else:
                #     self.logger.error(f"🚨 TIMEOUT: {symbol} missing NEW 3m bar at {target_3m_time} after {max_retries} attempts")
                #     #return
            df_3m = await self.get_klines_df_redis_first(symbol, '3m')
            if df_3m.empty:
                self.logger.warning(f"No 3m data available for {symbol}, cannot resample 15m")
                return

            # Get the 5 3m bars needed for this 15m period
            end_time = target_3m_time
            start_time = end_time - pd.Timedelta(minutes=12) # Window covers 5 bars

            df_3m_filtered = df_3m[(pd.to_datetime(df_3m['timestamp']) >= start_time) & (pd.to_datetime(df_3m['timestamp']) <= end_time)]
            if len(df_3m_filtered) < 5:
                self.logger.warning(f"⚠️ Skipping real-time 15m resample for {symbol}: found only {len(df_3m_filtered)}/5 bars (need bars from {start_time} to {end_time})")
                return
                
            df_3m_indexed = df_3m_filtered.set_index(pd.to_datetime(df_3m_filtered['timestamp'])).sort_index()
            df_15m = (
                df_3m_indexed
                .resample("15min", label='right', closed='right')
                .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
            ).dropna()
            
            if not df_15m.empty:
                await self.merge_and_write_df(df_15m.reset_index(), symbol, "15m")
                await self.publish_to_redis(symbol, "15m", df_15m.reset_index())
                self.logger.info(f"✅ Real-time 15m resampling for {symbol} at {boundary_timestamp}")
                
        except Exception as e:
            self.logger.error(f"Error in real-time 15m resampling for {symbol}: {e}")

    # [!] MODIFIED: Added guardrail to prevent creating incomplete klines.
    async def _resample_1h_from_3m_real_time(self, symbol: str, boundary_timestamp: pd.Timestamp):
        """Resample 1h from 3m data at boundary timestamp, ensuring data completeness."""
        try:
            # Check for the NEW 3m kline that just arrived at the boundary
            target_3m_time = boundary_timestamp.floor('3min')
            
            # Retry logic: wait for the 3m bar to arrive (max 10 retries, 1 second apart)
            max_retries = 10
            for attempt in range(max_retries):
                if await self._has_specific_3m_bar(symbol, target_3m_time):
                    break
                if attempt < max_retries - 1:
                    self.logger.debug(f"⏳ Waiting for 3m bar {target_3m_time} for {symbol} 1h resample (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(1)
                else:
                    self.logger.error(f"🚨 TIMEOUT: {symbol} missing NEW 3m bar at {target_3m_time} for 1h resample after {max_retries} attempts")
                    return
            
            # Use priority order for 3m data
            df_3m = await self.get_klines_df_redis_first(symbol, '3m')
            if df_3m.empty:
                self.logger.debug(f"No 3m data available for {symbol} 1h resample")
                return
            end_time = target_3m_time
            start_time = end_time - pd.Timedelta(minutes=57)
            df_3m_filtered = df_3m[(pd.to_datetime(df_3m['timestamp']) >= start_time) & (pd.to_datetime(df_3m['timestamp']) <= end_time)]
            
            if len(df_3m_filtered) < 20:
                self.logger.debug(f"Skipping real-time 1h resample for {symbol}: found only {len(df_3m_filtered)}/20 bars.")
                return
            df_3m_indexed = df_3m_filtered.set_index(pd.to_datetime(df_3m_filtered['timestamp'])).sort_index()
            df_1h = (
                df_3m_indexed
                .resample("1h", label='right', closed='right')
                .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
            ).dropna()
            
            if not df_1h.empty:
                await self.merge_and_write_df(df_1h.reset_index(), symbol, "1h")
                await self.publish_to_redis(symbol, "1h", df_1h.reset_index())
                self.logger.info(f"✅ Real-time 1h resampling for {symbol} at {boundary_timestamp}")
                
        except Exception as e:
            self.logger.error(f"Error in real-time 1h resampling for {symbol}: {e}")
    async def _resample_4h_from_3m_real_time(self, symbol: str, boundary_timestamp: pd.Timestamp):
        """Resample 4h from 3m data at boundary timestamp."""
        try:
            # Check for the NEW 3m kline that just arrived at the boundary
            target_3m_time = boundary_timestamp.floor('3min')
            
            # Retry logic: wait for the 3m bar to arrive (max 10 retries, 1 second apart)
            max_retries = 10
            for attempt in range(max_retries):
                if await self._has_specific_3m_bar(symbol, target_3m_time):
                    break
                if attempt < max_retries - 1:
                    self.logger.debug(f"⏳ Waiting for 3m bar {target_3m_time} for {symbol} 4h resample (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(1)
                else:
                    self.logger.error(f"🚨 TIMEOUT: {symbol} missing NEW 3m bar at {target_3m_time} for 4h resample after {max_retries} attempts")
                    return
            
            # Use priority order for 3m data
            df_3m = await self.get_klines_df_redis_first(symbol, '3m')
            if df_3m.empty:
                self.logger.debug(f"No 3m data available for {symbol} 4h resample")
                return
                
            # Filter for the last 4 hours
            start_time = boundary_timestamp - pd.Timedelta(hours=4)
            df_3m_filtered = df_3m[pd.to_datetime(df_3m['timestamp']) >= start_time]
            
            if len(df_3m_filtered) < 80:  # Need at least 80 bars for 4h
                return
                
            df_3m_indexed = df_3m_filtered.set_index(pd.to_datetime(df_3m_filtered['timestamp'])).sort_index()
            df_4h = (
                df_3m_indexed
                .resample("4h", label='right', closed='right')
                .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
            ).dropna()
            
            if not df_4h.empty:
                await self.merge_and_write_df(df_4h.reset_index(), symbol, "4h")
                await self.publish_to_redis(symbol, "4h", df_4h.reset_index())
                self.logger.info(f"✅ Real-time 4h resampling for {symbol} at {boundary_timestamp}")
                
        except Exception as e:
            self.logger.error(f"Error in real-time 4h resampling for {symbol}: {e}")

    async def _resample_D_from_3m_real_time(self, symbol: str, boundary_timestamp: pd.Timestamp):
        """Resample daily from 3m data at boundary timestamp."""
        try:
            # Use priority order for 3m data
            df_3m = await self.get_klines_df_redis_first(symbol, '3m')
            if df_3m.empty:
                self.logger.debug(f"No 3m data available for {symbol} daily resample")
                return
                
            # Filter for the last day
            start_time = boundary_timestamp - pd.Timedelta(days=1)
            df_3m_filtered = df_3m[pd.to_datetime(df_3m['timestamp']) >= start_time]
            
            if len(df_3m_filtered) < 480:  # Need at least 480 bars for daily
                return
                
            df_3m_indexed = df_3m_filtered.set_index(pd.to_datetime(df_3m_filtered['timestamp'])).sort_index()
            df_D = (
                df_3m_indexed
                .resample("D", label='right', closed='right')
                .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
            ).dropna()
            
            if not df_D.empty:
                await self.merge_and_write_df(df_D.reset_index(), symbol, "D")
                await self.publish_to_redis(symbol, "D", df_D.reset_index())
                self.logger.info(f"✅ Real-time D resampling for {symbol} at {boundary_timestamp}")
                
        except Exception as e:
            self.logger.error(f"Error in real-time D resampling for {symbol}: {e}")
            

    async def _resample_higher_tf(self, symbol: str, target_interval: str, source_interval: str):
        """
        Generic function to resample a target timeframe from a source timeframe.
        Example: _resample_higher_tf(symbol, "1h", "15m")
        """
        try:
            df_source = await self.get_klines_df(symbol, source_interval)
            if df_source.empty:
                self.logger.debug(f"Skipping {target_interval} resampling for {symbol}; source ({source_interval}) is empty.")
                return

            df_source_indexed = df_source.set_index(pd.to_datetime(df_source['timestamp'])).sort_index()
            
            # Use '' for minutes, 'H' for hours, 'D' for days in resample rule
            rule_map = {'15m': '15m', '1h': '1h', '4h': '4h', 'D': 'D'}
            resample_rule = rule_map.get(target_interval)
            if not resample_rule:
                return

            df_resampled = (
                df_source_indexed
                .resample(resample_rule, label='right', closed='right')
                .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
            ).dropna()

            if not df_resampled.empty:
                now_utc = datetime.now(timezone.utc)
                df_resampled = df_resampled[df_resampled.index <= now_utc]
                
                if not df_resampled.empty:
                    await self.merge_and_write_df(df_resampled.reset_index(), symbol, target_interval)
                    await self.publish_to_redis(symbol, target_interval, df_resampled.reset_index())
                    self.logger.debug(f"✅ Resampled {symbol} {target_interval} from {source_interval}: {len(df_resampled)} bars produced.")
        except Exception as e:
            self.logger.error(f"Cascading resampling failed for {symbol} {target_interval} from {source_interval}: {e}")

    # # [!] REFACTORED: Use new cascading logic
    # async def periodic_htf_resampling_task(self, interval_minutes: int = 5):
    #     """Periodically run a cascading HTF resampling to ensure data integrity."""
    #     await asyncio.sleep(45)
    #     self.logger.info(f"🔄 Starting CASCADING periodic HTF resampling task (every {interval_minutes} minutes)")
        
    #     cascade_map = {
    #         "15m": "3m",
    #         "1h": "15m",
    #         "4h": "1h",
    #         "D": "4h"
    #     }

    #     while True:
    #         try:
    #             self.logger.info("🔄 Running CASCADING periodic HTF resampling...")
                
    #             for symbol in self.symbols_to_run:
    #                 # Process timeframes in order to ensure data flows up the cascade
    #                 for target_tf, source_tf in cascade_map.items():
    #                     await self._resample_higher_tf(symbol, target_tf, source_tf)
                    
    #                 await asyncio.sleep(0.02) # Small delay between symbols
                
    #             self.logger.info(f"✅ Cascading periodic HTF resampling completed for {len(self.symbols_to_run)} symbols.")
                
    #         except Exception as e:
    #             self.logger.error(f"Cascading periodic HTF resampling task failed: {e}")
            
    #         await asyncio.sleep(interval_minutes * 60)

    async def emergency_fix_stale_htf(self):
        """EMERGENCY: Force update all HTF data from current 3m data"""
        self.logger.warning("🚨 EMERGENCY: Fixing stale HTF data from current 3m data")
        
        fixed_count = 0
        for symbol in self.symbols_to_run:
            try:
                # Get current 3m data
                df_3m = await self.get_klines_df(symbol, "3m")
                if df_3m.empty or len(df_3m) < 5:
                    continue
                    
                # Force resample all HTF
                await self._resample_15m_from_3m(symbol, df_3m)
                await self._resample_1h_from_3m(symbol, df_3m)
                await self._resample_4h_from_3m(symbol, df_3m)
                await self._resample_D_from_3m(symbol, df_3m)
                
                fixed_count += 1
                self.logger.info(f"✅ Emergency HTF fix for {symbol}")

            except Exception as e:
                self.logger.error(f"❌ Emergency HTF fix failed for {symbol}: {e}")
        
        self.logger.warning(f"🚨 EMERGENCY COMPLETE: Fixed {fixed_count} symbols")


    async def cascading_resample_symbol(self, symbol: str):
        """
        Smart cascading resampling that flows data up the timeframe hierarchy:
        3m → 15m → 1h → 4h → D

        Only resamples missing bars and ensures data completeness at each level.
        """
        try:
            self.logger.info(f"🔄 Starting cascading resample for {symbol}")

            # Start with 3m to 15m resampling
            await self._cascade_3m_to_15m(symbol)

            # Then 15m to 1h
            await self._cascade_15m_to_1h(symbol)

            # Then 1h to 4h
            await self._cascade_1h_to_4h(symbol)

            # Finally 4h to D
            await self._cascade_4h_to_D(symbol)

            self.logger.info(f"✅ Cascading resample completed for {symbol}")

        except Exception as e:
            self.logger.error(f"Cascading resample failed for {symbol}: {e}", exc_info=True)

    async def _cascade_3m_to_15m(self, symbol: str):
        """Resample missing 15m bars from 3m data"""
        try:
            # Get 3m data using priority order: Redis -> Cache -> Backups
            df_3m = await self.get_klines_df_redis_first(symbol, '3m')
            if df_3m.empty or len(df_3m) < 5:
                self.logger.debug(f"Insufficient 3m data for {symbol} 15m resample")
                return

            # Get existing 15m data for comparison
            df_15m = await self.get_klines_df(symbol, '15m')
                
            # Find missing 15m bars that we can create from available 3m data
            if df_15m.empty:
                # No existing 15m data, resample all available 3m
                df_3m_indexed = df_3m.set_index(pd.to_datetime(df_3m['timestamp'])).sort_index()
                df_15m_new = (
                    df_3m_indexed
                    .resample("15min", label='right', closed='right')
                    .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                ).dropna()
            else:
                # Find the latest 15m bar we have
                latest_15m = pd.to_datetime(df_15m['timestamp']).max()

                # Get 3m data after the latest 15m bar (we need at least 5 bars for one 15m bar)
                df_3m_new = df_3m[pd.to_datetime(df_3m['timestamp']) > latest_15m]
                
                if len(df_3m_new) >= 5:
                    df_3m_new_indexed = df_3m_new.set_index(pd.to_datetime(df_3m_new['timestamp'])).sort_index()
                    df_15m_new = (
                        df_3m_new_indexed
                        .resample("15min", label='right', closed='right')
                        .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                    ).dropna()
                else:
                    df_15m_new = pd.DataFrame()
            
            if not df_15m_new.empty:
                await self.merge_and_write_df(df_15m_new.reset_index(), symbol, "15m")
                self.logger.debug(f"✅ 3m→15m: Added {len(df_15m_new)} bars for {symbol}")
                
        except Exception as e:
            self.logger.error(f"3m→15m cascade failed for {symbol}: {e}")

    async def _cascade_15m_to_1h(self, symbol: str):
        """Resample missing 1h bars from 15m data"""
        try:
            df_15m = await self.get_klines_df(symbol, '15m')
            df_1h = await self.get_klines_df(symbol, '1h')

            if df_15m.empty or len(df_15m) < 4:
                self.logger.debug(f"Insufficient 15m data for {symbol} 1h resample")
                return
                
            if df_1h.empty:
                # No existing 1h data, resample all available 15m
                df_15m_indexed = df_15m.set_index(pd.to_datetime(df_15m['timestamp'])).sort_index()
                df_1h_new = (
                    df_15m_indexed
                    .resample("1h", label='right', closed='right')
                    .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                ).dropna()
            else:
                # Find the latest 1h bar we have
                latest_1h = pd.to_datetime(df_1h['timestamp']).max()

                # Get 15m data after the latest 1h bar (we need at least 4 bars for one 1h bar)
                df_15m_new = df_15m[pd.to_datetime(df_15m['timestamp']) > latest_1h]
                
                if len(df_15m_new) >= 4:
                    df_15m_new_indexed = df_15m_new.set_index(pd.to_datetime(df_15m_new['timestamp'])).sort_index()
                    df_1h_new = (
                        df_15m_new_indexed
                        .resample("1h", label='right', closed='right')
                        .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                    ).dropna()
                else:
                    df_1h_new = pd.DataFrame()
            
            if not df_1h_new.empty:
                await self.merge_and_write_df(df_1h_new.reset_index(), symbol, "1h")
                self.logger.debug(f"✅ 15m→1h: Added {len(df_1h_new)} bars for {symbol}")
                
        except Exception as e:
            self.logger.error(f"15m→1h cascade failed for {symbol}: {e}")

    async def _cascade_1h_to_4h(self, symbol: str):
        """Resample missing 4h bars from 1h data"""
        try:
            df_1h = await self.get_klines_df(symbol, '1h')
            df_4h = await self.get_klines_df(symbol, '4h')

            if df_1h.empty or len(df_1h) < 4:
                self.logger.debug(f"Insufficient 1h data for {symbol} 4h resample")
                return
                
            if df_4h.empty:
                # No existing 4h data, resample all available 1h
                df_1h_indexed = df_1h.set_index(pd.to_datetime(df_1h['timestamp'])).sort_index()
                df_4h_new = (
                    df_1h_indexed
                    .resample("4h", label='right', closed='right')
                    .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                ).dropna()
            else:
                # Find the latest 4h bar we have
                latest_4h = pd.to_datetime(df_4h['timestamp']).max()

                # Get 1h data after the latest 4h bar (we need at least 4 bars for one 4h bar)
                df_1h_new = df_1h[pd.to_datetime(df_1h['timestamp']) > latest_4h]
                
                if len(df_1h_new) >= 4:
                    df_1h_new_indexed = df_1h_new.set_index(pd.to_datetime(df_1h_new['timestamp'])).sort_index()
                    df_4h_new = (
                        df_1h_new_indexed
                        .resample("4h", label='right', closed='right')
                        .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                    ).dropna()
                else:
                    df_4h_new = pd.DataFrame()
            
            if not df_4h_new.empty:
                await self.merge_and_write_df(df_4h_new.reset_index(), symbol, "4h")
                self.logger.debug(f"✅ 1h→4h: Added {len(df_4h_new)} bars for {symbol}")
                
        except Exception as e:
            self.logger.error(f"1h→4h cascade failed for {symbol}: {e}")

    async def _cascade_4h_to_D(self, symbol: str):
        """Resample missing daily bars from 4h data"""
        try:
            df_4h = await self.get_klines_df(symbol, '4h')
            df_D = await self.get_klines_df(symbol, 'D')

            if df_4h.empty or len(df_4h) < 6:
                self.logger.debug(f"Insufficient 4h data for {symbol} daily resample")
                return
                
            if df_D.empty:
                # No existing daily data, resample all available 4h
                df_4h_indexed = df_4h.set_index(pd.to_datetime(df_4h['timestamp'])).sort_index()
                df_D_new = (
                    df_4h_indexed
                    .resample("D", label='right', closed='right')
                    .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                ).dropna()
            else:
                # Find the latest daily bar we have
                latest_D = pd.to_datetime(df_D['timestamp']).max()

                # Get 4h data after the latest daily bar (we need at least 6 bars for one daily bar)
                df_4h_new = df_4h[pd.to_datetime(df_4h['timestamp']) > latest_D]
                
                if len(df_4h_new) >= 6:
                    df_4h_new_indexed = df_4h_new.set_index(pd.to_datetime(df_4h_new['timestamp'])).sort_index()
                    df_D_new = (
                        df_4h_new_indexed
                        .resample("D", label='right', closed='right')
                        .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                    ).dropna()
                else:
                    df_D_new = pd.DataFrame()
            
            if not df_D_new.empty:
                await self.merge_and_write_df(df_D_new.reset_index(), symbol, "D")
                self.logger.debug(f"✅ 4h→D: Added {len(df_D_new)} bars for {symbol}")
                
        except Exception as e:
            self.logger.error(f"4h→D cascade failed for {symbol}: {e}")

    async def _check_and_recalculate_D_from_4h(self, symbol: str):
        """Check if D klines are missing/stale and recalculate from 4h if needed"""
        try:
            df_D = await self.get_klines_df(symbol, 'D')
            df_4h = await self.get_klines_df(symbol, '4h')
            if df_4h.empty or len(df_4h) < 6:
                return False
            now_utc = datetime.now(timezone.utc)
            if df_D.empty:
                self.logger.info(f"🔄 D klines missing for {symbol}, recalculating from 4h...")
            else:
                latest_D = pd.to_datetime(df_D['timestamp']).max()
                age_hours = (now_utc - latest_D.to_pydatetime()).total_seconds() / 3600
                if age_hours < 20:
                    return True
                self.logger.info(f"🔄 D klines stale for {symbol} (age: {age_hours:.1f}h), recalculating from 4h...")
            await self._cascade_4h_to_D(symbol)
            df_D_after = await self.get_klines_df(symbol, 'D')
            if not df_D_after.empty:
                latest_D_after = pd.to_datetime(df_D_after['timestamp']).max()
                age_hours_after = (now_utc - latest_D_after.to_pydatetime()).total_seconds() / 3600
                if age_hours_after < 25:
                    return True
            return False
        except Exception as e:
            self.logger.debug(f"Error checking D klines for {symbol}: {e}")
            return False

    async def _ensure_D_klines_via_api(self, symbol: str):
        """Fetch D klines from API if recalculation didn't work"""
        try:
            self.logger.info(f"📡 Fetching D klines from API for {symbol}...")
            fetched = await self.fetch_klines_in_chunks(symbol, 'D', 10, end_time_ms=None)
            if not fetched.empty:
                cleaned = self._sanitize_klines(fetched, 'D')
                if not cleaned.empty:
                    existing = await self.get_klines_df(symbol, 'D')
                    await self._merge_preserving_history(symbol, 'D', cleaned, existing)
                    df_D_final = await self.get_klines_df(symbol, 'D')
                    if not df_D_final.empty:
                        latest_D = pd.to_datetime(df_D_final['timestamp']).max()
                        age_hours = (datetime.now(timezone.utc) - latest_D.to_pydatetime()).total_seconds() / 3600
                        if age_hours < 25:
                            self.logger.info(f"✅ API fetch successful for {symbol} D klines (age: {age_hours:.1f}h)")
                            return True
            return False
        except Exception as e:
            self.logger.error(f"API fetch failed for {symbol} D klines: {e}")
            return False

    async def periodic_D_klines_health_check(self, interval_minutes: int = 5):
        """Periodic task to ensure D klines are always available and fresh"""
        await asyncio.sleep(120)
        self.logger.info(f"🔄 Starting D klines health check (every {interval_minutes} minutes)")
        while True:
            try:
                symbols_needing_fix = []
                for symbol in self.symbols_to_run:
                    try:
                        df_D = await self.get_klines_df(symbol, 'D')
                        now_utc = datetime.now(timezone.utc)
                        if df_D.empty:
                            symbols_needing_fix.append((symbol, "missing"))
                        else:
                            latest_D = pd.to_datetime(df_D['timestamp']).max()
                            age_hours = (now_utc - latest_D.to_pydatetime()).total_seconds() / 3600
                            if age_hours > 25:
                                symbols_needing_fix.append((symbol, f"stale_{age_hours:.1f}h"))
                    except Exception:
                        symbols_needing_fix.append((symbol, "error"))
                if symbols_needing_fix:
                    self.logger.warning(f"🔍 Found {len(symbols_needing_fix)} symbols with D klines issues: {[s[0] for s in symbols_needing_fix[:5]]}")
                    for symbol, issue in symbols_needing_fix:
                        try:
                            fixed = await self._check_and_recalculate_D_from_4h(symbol)
                            if not fixed:
                                await self._ensure_D_klines_via_api(symbol)
                        except Exception as e:
                            self.logger.debug(f"Error fixing D klines for {symbol}: {e}")
            except Exception as e:
                self.logger.error(f"D klines health check failed: {e}")
            await asyncio.sleep(interval_minutes * 60)

    async def smart_periodic_htf_resampling_task(self, interval_minutes: int = 3):
        """Smart periodic resampling using cascading logic"""
        await asyncio.sleep(45)
        self.logger.info(f"🔄 Starting SMART cascading HTF resampling (every {interval_minutes} minutes)")
        
        while True:
            try:
                self.logger.info("🔄 Running SMART cascading HTF resampling...")
                
                # Process symbols in smaller batches for better resource management
                batch_size = 64
                symbols_batches = [self.symbols_to_run[i:i + batch_size] 
                                for i in range(0, len(self.symbols_to_run), batch_size)]
                
                for batch_num, symbol_batch in enumerate(symbols_batches):
                    self.logger.info(f"🔄 Processing batch {batch_num + 1}/{len(symbols_batches)} ({len(symbol_batch)} symbols)")
                    
                    # Run cascading resample for each symbol in the batch
                    tasks = [self.cascading_resample_symbol(symbol) for symbol in symbol_batch]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # Small delay between batches
                    if batch_num < len(symbols_batches) - 1:
                        await asyncio.sleep(2)
                
                self.logger.info(f"✅ SMART cascading HTF resampling completed for {len(self.symbols_to_run)} symbols")
                
            except Exception as e:
                self.logger.error(f"SMART cascading HTF resampling task failed: {e}")
            
            await asyncio.sleep(interval_minutes * 60)

    async def _check_and_resample_htf_boundaries(self, symbol: str, timestamp: pd.Timestamp, last_boundaries: dict):
        """Check if timestamp triggers any HTF boundaries and resample if needed."""
        minute = timestamp.minute
        hour = timestamp.hour
        
        # Generate boundary key for deduplication
        boundary_key = f"{symbol}_{timestamp.strftime('%Y%m%d%H%M')}"
        
        # 15m boundaries: :00, :15, :30, :45
        if minute in [0, 15, 30, 45]:
            if boundary_key not in last_boundaries:
                self.logger.info(f"🎯 15m boundary detected for {symbol} at {timestamp}")
                await self._resample_15m_from_3m_real_time(symbol, timestamp)
                last_boundaries[boundary_key] = True
                
                # Clean old boundaries (keep last 100)
                if len(last_boundaries) > 100:
                    oldest_key = next(iter(last_boundaries))
                    del last_boundaries[oldest_key]
        
        # 1h boundaries: :00 (except when it's also a 4h boundary)
        if minute == 0 and hour % 1 == 0:  # Every hour
            boundary_key_1h = f"{symbol}_1h_{timestamp.strftime('%Y%m%d%H')}"
            if boundary_key_1h not in last_boundaries:
                self.logger.info(f"🎯 1h boundary detected for {symbol} at {timestamp}")
                await self._resample_1h_from_3m_real_time(symbol, timestamp)
                last_boundaries[boundary_key_1h] = True
        
        # 4h boundaries: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
        if minute == 0 and hour % 4 == 0:
            boundary_key_4h = f"{symbol}_4h_{timestamp.strftime('%Y%m%d%H')}"
            if boundary_key_4h not in last_boundaries:
                self.logger.info(f"🎯 4h boundary detected for {symbol} at {timestamp}")
                await self._resample_4h_from_3m_real_time(symbol, timestamp)
                last_boundaries[boundary_key_4h] = True
        
        # Daily boundaries: 00:00 UTC
        if minute == 0 and hour == 0:
            boundary_key_d = f"{symbol}_D_{timestamp.strftime('%Y%m%d')}"
            if boundary_key_d not in last_boundaries:
                self.logger.info(f"🎯 Daily boundary detected for {symbol} at {timestamp}")
                await self._resample_D_from_3m_real_time(symbol, timestamp)
                last_boundaries[boundary_key_d] = True

    async def force_update_all_htf(self):
        """Manual trigger to force update all HTF data from 3m"""
        self.logger.warning("🔧 MANUAL TRIGGER: Forcing HTF update for all symbols from 3m data")
        await self.emergency_fix_stale_htf()

    async def diagnostic_htf_update(self, symbol: str = None):
        """Diagnostic function to manually trigger HTF updates and check what's happening"""
        self.logger.warning("🔍 DIAGNOSTIC: Manual HTF update check")

        symbols_to_check = [symbol] if symbol else self.symbols_to_run[:5]  # Check first 5 symbols

        for test_symbol in symbols_to_check:
            try:
                self.logger.info(f"🔍 Checking HTF data for {test_symbol}")

                # Check current HTF data timestamps
                for interval in ['15m', '1h', '4h', 'D']:
                    try:
                        df = await self.get_klines_df(test_symbol, interval)
                        if not df.empty:
                            latest_ts = df['timestamp'].max()
                            now = datetime.now(timezone.utc)
                            age_minutes = (now - latest_ts).total_seconds() / 60
                            self.logger.info(f"  {test_symbol} {interval}: latest={latest_ts}, age={age_minutes:.1f}min")
                        else:
                            self.logger.warning(f"  {test_symbol} {interval}: NO DATA")
                    except Exception as e:
                        self.logger.error(f"  {test_symbol} {interval}: ERROR - {e}")

                # Try to update this symbol's HTF data
                self.logger.info(f"🔄 Triggering HTF update for {test_symbol}")
                await self.cascading_resample_symbol(test_symbol)

            except Exception as e:
                self.logger.error(f"Error in diagnostic check for {test_symbol}: {e}")

        self.logger.info("🔍 DIAGNOSTIC COMPLETE")


    # =======================================================================

    async def emergency_15m_resampling(self):
        """Emergency function to force 15m resampling from latest 3m data"""
        self.logger.warning("🚨 EMERGENCY: Starting forced 15m resampling from 3m data")

        success_count = 0
        for symbol in self.symbols_to_run:
            try:
                # Get latest 3m data using priority order
                df_3m = await self.get_klines_df_redis_first(symbol, '3m')

                if not df_3m.empty and len(df_3m) >= 5:
                    # Use pandas resampling for reliability
                    df_3m_indexed = df_3m.set_index(pd.to_datetime(df_3m['timestamp'])).sort_index()
                    df_15m = (
                        df_3m_indexed
                        .resample("15min", label='right', closed='right')
                        .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                    ).dropna()
                    
                    if not df_15m.empty:
                        await self.merge_and_write_df(df_15m.reset_index(), symbol, "15m")
                        await self.publish_to_redis(symbol, "15m", df_15m.reset_index())
                        success_count += 1
                        self.logger.info(f"✅ Emergency 15m resampling: {symbol}")
                        
            except Exception as e:
                self.logger.error(f"❌ Emergency 15m resampling failed for {symbol}: {e}")
        
        self.logger.info(f"🚨 EMERGENCY COMPLETE: {success_count} symbols updated")

    async def force_update_all_15m(self):
        """Manual trigger to force update all 15m data from 3m"""
        self.logger.warning("🔧 MANUAL TRIGGER: Forcing 15m update for all symbols")
        await self.emergency_15m_resampling()


    # async def _backup_resample_symbol_htf(self, symbol: str):
    #     """Backup resample all higher timeframes for a symbol from JSON files."""
    #     try:
    #         # Resample 15m from 3m
    #         df_3m = await self.get_klines_df(symbol, "3m")
    #         if not df_3m.empty and len(df_3m) >= 5:
    #             await self._resample_15m_from_3m_json(symbol, df_3m)
            
    #         # Resample 1h from 3m (direct from 3m, not from 15m)
    #         if not df_3m.empty and len(df_3m) >= 20:
    #             await self._resample_1h_from_3m_json(symbol, df_3m)
            
    #         # Resample 4h from 3m (direct from 3m)
    #         if not df_3m.empty and len(df_3m) >= 80:
    #             await self._resample_4h_from_3m_json(symbol, df_3m)
            
    #         # Resample D from 3m (direct from 3m)
    #         if not df_3m.empty and len(df_3m) >= 480:
    #             await self._resample_D_from_3m_json(symbol, df_3m)
                
    #     except Exception as e:
    #         self.logger.error(f"Backup resampling failed for {symbol}: {e}")

    # async def _resample_15m_from_3m_json(self, symbol: str, df_3m: pd.DataFrame):
    #     """Resample 15m bars from 3m JSON data."""
    #     if df_3m.empty or len(df_3m) < 5:
    #         return
            
    #     df_3m = df_3m.sort_values('timestamp').reset_index(drop=True)
    #     df_3m_indexed = df_3m.set_index('timestamp')
        
    #     df_15m = (
    #         df_3m_indexed
    #         .resample("15min", label='right', closed='right')
    #         .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    #     ).dropna()
        
    #     if not df_15m.empty:
    #         now_utc = datetime.now(timezone.utc)
    #         df_15m = df_15m[df_15m.index <= now_utc]
            
    #         if not df_15m.empty:
    #             await self.merge_and_write_df(df_15m.reset_index(), symbol, "15m")
    #             await self.publish_to_redis(symbol, "15m", df_15m.reset_index())
    #             self.logger.debug(f"✅ Backup 15m resampling for {symbol}: {len(df_15m)} bars")

    # async def _resample_1h_from_3m_json(self, symbol: str, df_3m: pd.DataFrame):
    #     """Resample 1h bars from 3m JSON data."""
    #     if df_3m.empty or len(df_3m) < 20:
    #         return
            
    #     df_3m = df_3m.sort_values('timestamp').reset_index(drop=True)
    #     df_3m_indexed = df_3m.set_index('timestamp')
        
    #     df_1h = (
    #         df_3m_indexed
    #         .resample("1h", label='right', closed='right')
    #         .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    #     ).dropna()
        
    #     if not df_1h.empty:
    #         now_utc = datetime.now(timezone.utc)
    #         df_1h = df_1h[df_1h.index <= now_utc]
            
    #         if not df_1h.empty:
    #             await self.merge_and_write_df(df_1h.reset_index(), symbol, "1h")
    #             await self.publish_to_redis(symbol, "1h", df_1h.reset_index())
    #             self.logger.debug(f"✅ Backup 1h resampling for {symbol}: {len(df_1h)} bars")

    # async def _resample_4h_from_3m_json(self, symbol: str, df_3m: pd.DataFrame):
    #     """Resample 4h bars from 3m JSON data."""
    #     if df_3m.empty or len(df_3m) < 80:
    #         return
            
    #     df_3m = df_3m.sort_values('timestamp').reset_index(drop=True)
    #     df_3m_indexed = df_3m.set_index('timestamp')
        
    #     df_4h = (
    #         df_3m_indexed
    #         .resample("4h", label='right', closed='right')
    #         .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    #     ).dropna()
        
    #     if not df_4h.empty:
    #         now_utc = datetime.now(timezone.utc)
    #         df_4h = df_4h[df_4h.index <= now_utc]
            
    #         if not df_4h.empty:
    #             await self.merge_and_write_df(df_4h.reset_index(), symbol, "4h")
    #             await self.publish_to_redis(symbol, "4h", df_4h.reset_index())
    #             self.logger.debug(f"✅ Backup 4h resampling for {symbol}: {len(df_4h)} bars")

    # async def _resample_D_from_3m_json(self, symbol: str, df_3m: pd.DataFrame):
    #     """Resample daily bars from 3m JSON data."""
    #     if df_3m.empty or len(df_3m) < 480:
    #         return
            
    #     df_3m = df_3m.sort_values('timestamp').reset_index(drop=True)
    #     df_3m_indexed = df_3m.set_index('timestamp')
        
    #     df_D = (
    #         df_3m_indexed
    #         .resample("D", label='right', closed='right')
    #         .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    #     ).dropna()
        
    #     if not df_D.empty:
    #         now_utc = datetime.now(timezone.utc)
    #         df_D = df_D[df_D.index <= now_utc]
            
    #         if not df_D.empty:
    #             await self.merge_and_write_df(df_D.reset_index(), symbol, "D")
    #             await self.publish_to_redis(symbol, "D", df_D.reset_index())
    #             self.logger.debug(f"✅ Backup D resampling for {symbol}: {len(df_D)} bars")
    async def periodic_heartbeat_task(self):
        heartbeat_flag = config.BASE_PATH / "ez_prices_heartbeat.flag"
        while True:
            await asyncio.sleep(60)
            heartbeat_flag.touch(exist_ok=True)


    async def periodic_redis_health_check(self, interval: int = 30):
        """Periodically check Redis connection health and reconnect if needed."""
        await asyncio.sleep(30)  # Wait for initial setup
        
        while True:
            try:
                # Check local Redis (for writing)
                if not self.redis_client:
                    self.logger.warning(" Local Redis connection lost, attempting reconnect...")
                    # Reinitialize local Redis connection - NON-BLOCKING
                    try:
                        # Use 127.0.0.1 explicitly to avoid IPv6 issues
                        host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
                        self.redis_client = redis.Redis(
                            host=host,
                            port=config.REDIS_PORT,
                            db=config.REDIS_DB,
                            decode_responses=True,
                            socket_connect_timeout=30,
                            socket_timeout=60,
                            health_check_interval=30,
                            max_connections=3000,
                            retry_on_timeout=True,
                            retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError]
                        )
                        # NON-BLOCKING: No ping wait, connects in background
                        self.logger.info(f"⏳ Local Redis reconnecting ({host}:{config.REDIS_PORT}) - will connect in background")
                    except Exception as e:
                        self.logger.error(f" Failed to restore local Redis connection: {e}")
                        self.redis_client = None
                else:
                    # Test the local Redis connection with a ping
                    try:
                        self.logger.debug(" Local Redis connection healthy")
                    except Exception as e:
                        self.logger.warning(f" Local Redis ping failed: {e}")
                        self.redis_client = None
                
                # Check gateway Redis (for reading)
                if self.redis_client_read:
                    try:
                        self.logger.debug(" Gateway Redis connection healthy")
                    except Exception as e:
                        self.logger.warning(f" Gateway Redis ping failed: {e}")
                        if not hasattr(self, '_gateway_redis_failures'):
                            self._gateway_redis_failures = 0
                        self._gateway_redis_failures += 1
                        if self._gateway_redis_failures >= 3:
                            self.logger.warning(" Gateway Redis failed 3 times, removing from sources")
                            self.redis_client_read = None
                            self._gateway_redis_failures = 0
                        else:
                            self.logger.debug(f" Gateway Redis failure {self._gateway_redis_failures}/3, keeping connection")
                else:
                    # AGGRESSIVE RECONNECT: Try to reconnect to gateway Redis - NON-BLOCKING
                    try:
                        env_info = get_current_environment()
                        gw_host, gw_port = env_info['redis_connections'].get('gateway', ('localhost', 6379))
                        # Fix IPv6 issue
                        if gw_host in ['localhost', '::1']:
                            gw_host = '127.0.0.1'
                        # MUCH longer timeout for gateway - SSH tunnels can be slow
                        self.redis_client_read = redis.Redis(
                            host=gw_host, port=gw_port, db=0, decode_responses=True, 
                            socket_connect_timeout=60, socket_timeout=300, max_connections=3000, 
                            health_check_interval=30, retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError],
                            retry_on_timeout=True
                        )
                        # NON-BLOCKING: No ping wait, connects in background
                        self.logger.info(f"⏳ Gateway Redis reconnecting ({gw_host}:{gw_port}) - will connect in background")
                        if hasattr(self, '_gateway_redis_failures'):
                            self._gateway_redis_failures = 0
                    except Exception as e:
                        self.logger.debug(f"Could not restore gateway Redis connection to {gw_host}:{gw_port}: {e}")
                        self.redis_client_read = None
                
                # Check HTTP session health
                if self.session and self.session.closed:
                    self.logger.warning(" HTTP session is closed, reinitializing...")
                    try:
                        self.session = await self._init_http_session()
                        self.logger.info(" HTTP session reinitialized successfully")
                    except Exception as e:
                        self.logger.error(f" Failed to reinitialize HTTP session: {e}")
                        self.session = None
                
                await asyncio.sleep(interval)  # Check every 30 seconds
                
            except Exception as e:
                self.logger.error(f"Redis health check error: {e}")
                await asyncio.sleep(interval)

    async def periodic_backup_cleanup_task(self, interval: int = 300):
        """Periodically cleans up old backups to prevent disk space issues."""
        await asyncio.sleep(60)  # Wait for initial setup
        
        while True:
            try:
                await self.backup_system.cleanup_old_backups()
                self.logger.info(" Backup cleanup completed")
            except Exception as e:
                self.logger.error(f" Backup cleanup failed: {e}")
            
            await asyncio.sleep(interval)  # Run every 5 minutes by default

    async def periodic_gateway_redis_sync_task(self, interval_seconds: int = 60):
        """Periodically sync klines data from gateway Redis to local disk."""
        if hasattr(self, '_gateway_sync_task_started') and self._gateway_sync_task_started:
            return
        self._gateway_sync_task_started = True
        
        # Start immediately - gateway Redis already has all the data
        
        while True:
            try:
                self.logger.info("🔄 === STARTING PERIODIC GATEWAY REDIS SYNC ===")
                
                if not self.redis_client_read:
                    self.logger.warning("Gateway Redis not available, skipping sync - system will continue using local data source")
                    await asyncio.sleep(interval_seconds)
                    continue
                
                # Get all symbols from gateway Redis
                gateway_keys = []
                for interval in ['3m', '15m', '1h', '4h', 'D']:
                    pattern = f"klines:*:{interval}"
                    try:
                        keys = await self.redis_client_read.keys(pattern)
                        self.logger.info(f"Gateway Redis {interval}: {len(keys)} keys found")
                        gateway_keys.extend(keys)
                    except Exception as e:
                        self.logger.warning(f"Failed to get keys for {interval}: {e}")
                
                if not gateway_keys:
                    self.logger.warning("No gateway Redis keys found")
                    await asyncio.sleep(interval_seconds)
                    continue
                
                self.logger.info(f"Found {len(gateway_keys)} gateway Redis keys to sync")
                
                # Sync each key to local disk with concurrency control
                synced_count = 0
                env = get_current_environment()['env']
                semaphore = asyncio.Semaphore(config.EZ_PRICES_FILE_IO_SEMAPHORE.get(env, 5))  # Use centralized rate limiting
                
                async def sync_key(key):
                    nonlocal synced_count
                    async with semaphore:
                        try:
                            parts = key.split(':')
                            if len(parts) != 3:
                                return
                            symbol, interval = parts[1], parts[2]
                            redis_data = await self.redis_client_read.get(key)
                            if not redis_data:
                                return
                            klines_data = json.loads(redis_data)
                            if isinstance(klines_data, dict) and 'klines' in klines_data:
                                klines_list = klines_data['klines']
                            elif isinstance(klines_data, list):
                                klines_list = klines_data
                            else:
                                return
                            if not klines_list or not isinstance(klines_list, list):
                                return

                            df = pd.DataFrame(klines_list)
                            if df.empty:
                                return
                            if 'timestamp' in df.columns:
                                ts = df['timestamp'].iloc[0]
                                if isinstance(ts, (int, float)):
                                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True, errors='coerce')
                                else:
                                    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
                            else:
                                return
                            df = df.dropna(subset=['timestamp'])
                            for col in ['open', 'high', 'low', 'close', 'volume']:
                                if col in df.columns:
                                    df[col] = pd.to_numeric(df[col], errors='coerce')
                            df = df.dropna(subset=['timestamp'])
                            df = df.sort_values('timestamp')
                            if df.empty:
                                return

                            # Check local file length efficiently without fully reading
                            local_file = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
                            local_len = 0
                            if await asyncio.to_thread(local_file.exists):
                                try:
                                    # Use os.path.getsize instead of reading the file
                                    size = await asyncio.to_thread(os.path.getsize, local_file)
                                    # Quick estimate: approximately 100 bytes per bar
                                    local_len = size // 100
                                except Exception:
                                    local_len = 0
                            if local_len >= len(df):
                                return

                            if await self._append_records_to_cache(symbol, interval, df, "Gateway Redis sync"):
                                synced_count += 1
                        except Exception as e:
                            self.logger.warning(f"Gateway Redis sync: failed to merge klines for {symbol}:{interval}: {e}")
                
                # Process all keys with controlled concurrency
                await asyncio.gather(*[sync_key(key) for key in gateway_keys], return_exceptions=True)
                
                self.logger.info(f"✅ Gateway Redis sync completed: {synced_count} files synced")
                
            except Exception as e:
                self.logger.error(f"Error in periodic gateway Redis sync: {e}")
            
            await asyncio.sleep(interval_seconds)

    async def aggressive_periodic_resampling(self, interval_minutes: int = 2):
        """Aggressive periodic resampling that ensures ALL HTF data is current"""
        await asyncio.sleep(30)  # Short initial delay
        
        self.logger.info(f"🚀 Starting AGGRESSIVE periodic resampling (every {interval_minutes} minutes)")

        while not self._shutting_down:
            try:
                start_time = time.time();self.logger.info("🔄 Starting aggressive HTF resampling cycle...")
                env = get_current_environment()['env']
                semaphore = asyncio.Semaphore(config.EZ_PRICES_API_SEMAPHORE.get(env, 10))

                async def process(symbol: str):
                    async with semaphore:
                        try:
                            df_3m = await self.get_klines_df_redis_first(symbol, '3m')
                            if df_3m.empty or len(df_3m) < 5:
                                return False
                            df_3m_idx = df_3m.set_index(pd.to_datetime(df_3m['timestamp'], format='mixed', utc=True, errors='coerce')).sort_index()
                            await asyncio.gather(*(self._fast_resample_tf(symbol, df_3m_idx, tf) for tf in self._fast_tf_order if len(df_3m) >= self._tf_required_3m[tf]))
                            return True
                        except Exception as exc:
                            self.logger.error(f"Error resampling {symbol}: {exc}")
                            return False

                results = await asyncio.gather(*(process(symbol) for symbol in self.symbols_to_run), return_exceptions=False)
                processed_count = sum(1 for ok in results if ok)
                elapsed = time.time() - start_time
                self.logger.info(f"✅ Aggressive HTF resampling completed: {processed_count} symbols in {elapsed:.2f}s")

            except Exception as e:
                self.logger.error(f"Aggressive periodic resampling failed: {e}")

            await asyncio.sleep(interval_minutes * 60)

    async def high_speed_periodic_resampling(self, interval_minutes: int = 2):
        """High-speed periodic resampling that processes symbols in parallel"""
        await asyncio.sleep(30)  # Short initial delay

        self.logger.info(f"🚀 Starting HIGH-SPEED periodic resampling (every {interval_minutes} minutes)")

        while not self._shutting_down:
            try:
                start_time = time.time()
                self.logger.info("🔄 Starting high-speed parallel resampling cycle...")

                # Use the ultra-fast parallel resampler
                await self.high_speed_parallel_resampling()

                elapsed = time.time() - start_time
                self.logger.info(f"✅ HIGH-SPEED periodic resampling completed in {elapsed:.2f}s")

            except Exception as e:
                self.logger.error(f"High-speed periodic resampling failed: {e}", exc_info=True)

            await asyncio.sleep(interval_minutes * 60)

    async def high_speed_parallel_resampling(self):
        self.logger.warning("🚀 ULTRA-FAST: Starting parallel HTF resampling from 3m data")
        start = time.time();env = get_current_environment()['env'];semaphore = asyncio.Semaphore(config.EZ_PRICES_API_SEMAPHORE.get(env, 10))

        async def process(symbol: str):
            async with semaphore:
                try:
                    df_3m = await self.get_klines_df_redis_first(symbol, '3m')
                    if df_3m.empty or len(df_3m) < 5:
                        return
                    df_3m_idx = df_3m.set_index(pd.to_datetime(df_3m['timestamp'], format='mixed', utc=True, errors='coerce')).sort_index()
                    await asyncio.gather(
                        *(self._fast_resample_tf(symbol, df_3m_idx, tf)
                          for tf in ("15m", "1h", "4h", "D")
                          if len(df_3m) >= self._tf_required_3m.get(tf, 5))
                    )
                except Exception as e:
                    self.logger.error(f"Fast resample failed for {symbol}: {e}")

        await asyncio.gather(*(process(symbol) for symbol in self.symbols_to_run), return_exceptions=True)
        self.logger.warning(f"🚀 ULTRA-FAST COMPLETE: Resampled {len(self.symbols_to_run)} symbols in {time.time()-start:.2f}s")

    async def _fast_resample_tf(self, symbol: str, df_indexed: pd.DataFrame, tf: str):
        try:
            bars = df_indexed.resample(self._tf_resample_args[tf], label='right', closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
            if bars.empty:return
            bars = bars[bars.index <= datetime.now(timezone.utc)]
            if bars.empty:return
            await self.merge_and_write_df(bars.reset_index(), symbol, tf)
        except Exception as e:
            self.logger.debug(f"Fast {tf} resample failed for {symbol}: {e}")

    async def _resample_all_htf_from_3m(self, symbol: str) -> bool:
        try:
            df_3m = await self.get_klines_df_redis_first(symbol, '3m')
            if df_3m.empty or len(df_3m) < 5:
                return False
            df_idx = df_3m.set_index(pd.to_datetime(df_3m['timestamp'], format='mixed', utc=True, errors='coerce')).sort_index()
            await asyncio.gather(*(self._fast_resample_tf(symbol, df_idx, tf) for tf in self._fast_tf_order if len(df_3m) >= self._tf_required_3m[tf]))
            return True
        except Exception as e:
            self.logger.error(f"Resample cascade failed for {symbol}: {e}")
            return False

    async def _recover_symbol_from_backups(self, symbol: str, intervals: tuple[str, ...] | list[str] | None = None) -> bool:
        restored = False
        targets = intervals or ("3m",) + self._fast_tf_order
        for interval in targets:
            try:
                backup_df = await self._read_consolidated_backup(symbol, interval)
                if backup_df.empty:
                    continue
                await self.merge_and_write_df(backup_df, symbol, interval)
                restored = True
            except Exception as e:
                self.logger.debug(f"Backup hydrate failed for {symbol}_{interval}: {e}")
        return restored

    async def real_time_boundary_resampler(self):
        """Real-time resampler that triggers IMMEDIATELY after 3m bar closes"""
        if not self.redis_client:
            self.logger.error("Cannot start real-time resampler: Redis client not available.")
            return

        pubsub = self.redis_client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe("klines_updates")
        self.logger.info("🎯 ULTRA-FAST Real-time Resampler started. Listening for 3m boundary events...")

        # Track last processed boundaries to avoid duplicates
        last_boundaries = {}

        while not self._shutting_down:
            try:
                message = await pubsub.get_message(timeout=10.0)
                if message is None:
                    continue

                notification = json.loads(message['data'])
                symbol = notification.get('symbol')
                interval = notification.get('interval')
                timestamp_str = notification.get('timestamp')  # Use timestamp from notification

                if not symbol or interval != '3m' or not timestamp_str:
                    continue

                # Parse the timestamp from the notification (avoid extra Redis query)
                try:
                    latest_3m_ts = pd.to_datetime(timestamp_str, utc=True)
                except Exception as e:
                    self.logger.debug(f"Could not parse timestamp {timestamp_str} for {symbol}: {e}")
                    continue

                # IMMEDIATELY check if this is a boundary and trigger resampling
                # A 3m bar at :15:00 represents :12:00-:15:00, so it completes the 15m bar
                await self._check_and_resample_htf_boundaries(symbol, latest_3m_ts, last_boundaries)
                            
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.logger.error(f"Error in real_time_boundary_resampler: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _immediate_15m_resample(self, symbol: str, boundary_timestamp: pd.Timestamp):
        """IMMEDIATE 15m resampling triggered at boundary minute"""
        try:
            # Get 3m data using priority order
            df_3m = await self.get_klines_df_redis_first(symbol, '3m')
            if df_3m.empty or len(df_3m) < 5:
                self.logger.debug(f"Insufficient 3m data for immediate 15m resample of {symbol}")
                return
            
            # Fast resample
            df_3m_indexed = df_3m.set_index(pd.to_datetime(df_3m['timestamp'])).sort_index()
            df_15m = (
                df_3m_indexed
                .resample("15min", label='right', closed='right')
                .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
            ).dropna()
            
            if not df_15m.empty:
                # Get the latest 15m bar (should be the one that just closed)
                latest_15m = df_15m.iloc[-1:].reset_index()
                
                # Merge only the latest bar
                await self.merge_and_write_df(latest_15m, symbol, "15m")
                await self.publish_to_redis(symbol, "15m", latest_15m)
                
                self.logger.info(f"✅ IMMEDIATE 15m resample for {symbol}: {latest_15m['timestamp'].iloc[0]}")
                
        except Exception as e:
            self.logger.error(f"IMMEDIATE 15m resample failed for {symbol}: {e}")

    async def periodic_htf_broadcast_task(self, interval_seconds: int = 900):
        """Periodically ensure all HTF timeframes (15m, 1h, 4h, D) are populated in Redis."""
        if hasattr(self, '_htf_broadcast_task_started') and self._htf_broadcast_task_started:
            return
        self._htf_broadcast_task_started = True
        
        
        while not self._shutting_down:
            try:
                self.logger.info("🔄 === STARTING PERIODIC HTF BROADCAST ===")

                # Check ALL symbols for missing HTF data (not just first 20)
                symbols_to_check = self.symbols_to_run  # Check ALL symbols

                self.logger.info(f"Checking {len(symbols_to_check)} symbols for missing HTF data...")

                symbols_processed = 0
                for symbol in symbols_to_check:
                    try:
                        await self._ensure_htf_data_for_symbol(symbol)
                        symbols_processed += 1
                        # Rate limiting: delay between symbols to prevent API overload
                        await asyncio.sleep(0.2)  # 200ms delay between symbols
                    except Exception as e:
                        self.logger.warning(f"Failed to ensure HTF data for {symbol}: {e}")

                self.logger.info(f"✅ Periodic HTF broadcast completed: {symbols_processed} symbols processed")

            except Exception as e:
                self.logger.error(f"Error in periodic HTF broadcast: {e}", exc_info=True)

            await asyncio.sleep(interval_seconds)

    async def _ensure_htf_data_for_symbol(self, symbol: str):
        """Ensure HTF data exists for a specific symbol."""
        htf_intervals = ['15m', '1h', '4h', 'D']
        
        for interval in htf_intervals:
            try:
                # Check if we have data in Redis
                redis_key = f"klines:{symbol}:{interval}"
                redis_data = await self.redis_client.get(redis_key)
                
                # Always check for data gaps and fetch if needed
                # Don't skip just because we have "enough" bars - check if they're recent
                
                # Check Redis data
                redis_data = None
                if self.redis_client:
                    try:
                        redis_key = f"klines:{symbol}:{interval}"
                        redis_data = await self.redis_client.get(redis_key)
                    except Exception:
                        pass
                
                # Check local disk cache
                local_data = None
                cache_file = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
                if cache_file.exists():
                    try:
                        with open(cache_file, 'r') as f:
                            local_data = json.load(f)
                    except Exception:
                        pass  # File corrupted, continue to API fetch
                
                # Determine if we need to fetch: actually fetch missing data instead of just checking
                need_to_fetch = True
                
                if local_data and len(local_data) > 0:
                    try:
                        # URGENT: Check actual file size - tiny files need immediate attention
                        cache_file = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
                        # if cache_file.exists(): #TEMP OUT
                        #     file_size_bytes = cache_file.stat().st_size
                        #     if file_size_bytes < 10 * 1024:
                        #         self.logger.info(f"Low-data {symbol}:{interval} cache ({file_size_bytes} bytes) restored from gateway; scheduling consolidation.")
                        #         await self.enqueue_symbol_for_backup(symbol)
                        
                        # Check if this symbol/interval has already been fully fetched
                        cache_key = f"{symbol}_{interval}"
                        if cache_key in self._fully_fetched_cache:
                            self.logger.debug(f"{symbol}:{interval} already marked as fully fetched, skipping")
                            continue

                        # For daily data: check if we need more historical data
                        if interval == 'D' and len(local_data) < 80:
                            self.logger.info(f"{symbol}:{interval} has only {len(local_data)} bars; relying on backup/Redis.") #TEMP
                            need_to_fetch = False
                            # For newer symbols, be more lenient - check if Binance has more data available
                            # try: #TEMP OUT
                            #     async with self.session.get(f"{config.FAPI_BASE_URL}/klines", params={"symbol": symbol.replace("USDC", "USDT"), "interval": "1d", "limit": 1}) as resp:
                            #         if resp.status == 200:
                            #             test_data = await resp.json()
                            #             if test_data and len(test_data) > 0:
                            #                 # If we have some data and Binance has data, we might need more
                            #                 self.logger.info(f"{symbol}:{interval} has only {len(local_data)} bars, need to fetch more historical data")
                            #                 need_to_fetch = True
                            # except Exception:
                            #     need_to_fetch = True  # Fallback to old behavior
                        # For 4h data: check if we need more historical data
                        elif interval == '4h' and len(local_data) < 200:
                            self.logger.info(f"{symbol}:{interval} has only {len(local_data)} bars; relying on backup/Redis.")#TEMP
                            need_to_fetch = False
                            # For newer symbols, be more lenient - check if Binance has more data available
                            # try:#TEMP OUT
                            #     async with self.session.get(f"{config.FAPI_BASE_URL}/klines", params={"symbol": symbol.replace("USDC", "USDT"), "interval": "4h", "limit": 1}) as resp:
                            #         if resp.status == 200:
                            #             test_data = await resp.json()
                            #             if test_data and len(test_data) > 0:
                            #                 # If we have some data and Binance has data, we might need more
                            #                 self.logger.info(f"{symbol}:{interval} has only {len(local_data)} bars, need to fetch more historical data")
                            #                 need_to_fetch = True
                            # except Exception:
                            #     need_to_fetch = True  # Fallback to old behavior
                        # For other timeframes: check if data is recent
                        else:
                            last_timestamp = pd.to_datetime(local_data[-1]['timestamp'], format='mixed', errors='coerce', utc=True)
                            now = pd.Timestamp.now(tz='UTC')
                            
                            if interval == 'D':
                                # For daily: check if we have today's data
                                days_old = (now - last_timestamp).days
                                need_to_fetch = days_old > 1
                            elif interval == '1h':
                                # For 1h: check if data is recent (within 2 hours)
                                hours_old = (now - last_timestamp).total_seconds() / 3600
                                need_to_fetch = hours_old > 2
                            elif interval == '15m':
                                # For 15m: check if data is recent (within 1 hour)
                                hours_old = (now - last_timestamp).total_seconds() / 3600
                                need_to_fetch = hours_old > 1
                            else:
                                # For 4h: check if data is recent (within 4 hours)
                                hours_old = (now - last_timestamp).total_seconds() / 3600
                                need_to_fetch = hours_old > 4
                            
                            if not need_to_fetch:
                                self.logger.debug(f"{symbol}:{interval} has recent data, skipping fetch")
                                continue
                    except Exception as e:
                        self.logger.debug(f"Error checking data freshness for {symbol}:{interval}: {e}")
                        # If we can't determine freshness, fetch anyway to be safe
                        need_to_fetch = True
                
                # FIRST PRIORITY: Check gateway Redis for data (has the real data)
                if self.redis_client_read:
                    try:
                        gateway_key = f"klines:{symbol}:{interval}"
                        gateway_data = await self.redis_client_read.get(gateway_key)
                        if gateway_data:
                            gateway_klines = json.loads(gateway_data)
                            if len(gateway_klines) > 0:
                                # VALIDATE: Check if gateway Redis data is actually klines or metadata
                                if isinstance(gateway_klines, list):
                                    # Check if it's actual klines (has timestamp in first item)
                                    if gateway_klines and isinstance(gateway_klines[0], dict) and 'timestamp' in gateway_klines[0]:
                                        # Valid klines data - save it
                                        self.logger.info(f"Using gateway Redis klines for {symbol}:{interval} ({len(gateway_klines)} bars)")
                                        if await self._append_records_to_cache(symbol, interval, gateway_klines, "Gateway Redis"):
                                            continue  # Skip API call, we got valid klines
                                    else:
                                        # Gateway Redis has list but not valid klines - skip it
                                        self.logger.warning(f"Gateway Redis data for {symbol}:{interval} is not valid klines (missing timestamp)")
                                elif isinstance(gateway_klines, dict) and 'klines' in gateway_klines:
                                    # Gateway Redis has metadata with klines - extract the klines
                                    actual_klines = gateway_klines['klines']
                                    if isinstance(actual_klines, list) and len(actual_klines) > 0:
                                        self.logger.info(f"Extracting klines from gateway Redis metadata for {symbol}:{interval} ({len(actual_klines)} bars)")
                                        if await self._append_records_to_cache(symbol, interval, actual_klines, "Gateway Redis metadata"):
                                            continue
                                    else:
                                        # Metadata has no valid klines - skip it
                                        self.logger.warning(f"Gateway Redis metadata for {symbol}:{interval} has no valid klines")
                                else:
                                    # Gateway Redis has unknown data structure - skip it
                                    self.logger.warning(f"Gateway Redis data for {symbol}:{interval} has unknown structure: {type(gateway_klines)}")
                    except Exception as e:
                        self.logger.warning(f"Failed to process gateway Redis data for {symbol}:{interval}: {e}")
                
                # SECOND PRIORITY: Use local Redis data if available and save to JSON
                # Only if gateway Redis had no data
                if redis_data:
                    try:
                        redis_klines = json.loads(redis_data)
                        if len(redis_klines) > 0:
                            # VALIDATE: Check if Redis data is actually klines or metadata
                            if isinstance(redis_klines, list):
                                # Check if it's actual klines (has timestamp in first item)
                                if redis_klines and isinstance(redis_klines[0], dict) and 'timestamp' in redis_klines[0]:
                                    # Valid klines data - save it
                                    self.logger.info(f"Using local Redis klines for {symbol}:{interval} ({len(redis_klines)} bars)")
                                    if await self._append_records_to_cache(symbol, interval, redis_klines, "Local Redis"):
                                        continue  # Skip API call, we got valid klines
                                else:
                                    # Redis has list but not valid klines - skip it
                                    self.logger.warning(f"Local Redis data for {symbol}:{interval} is not valid klines (missing timestamp)")
                            elif isinstance(redis_klines, dict) and 'klines' in redis_klines:
                                # Redis has metadata with klines - extract the klines
                                actual_klines = redis_klines['klines']
                                if isinstance(actual_klines, list) and len(actual_klines) > 0:
                                    self.logger.info(f"Extracting klines from local Redis metadata for {symbol}:{interval} ({len(actual_klines)} bars)")
                                    if await self._append_records_to_cache(symbol, interval, actual_klines, "Local Redis metadata"):
                                        continue
                                else:
                                    # Metadata has no valid klines - skip it
                                    self.logger.warning(f"Local Redis metadata for {symbol}:{interval} has no valid klines")
                            else:
                                # Redis has unknown data structure - skip it
                                self.logger.warning(f"Local Redis data for {symbol}:{interval} has unknown structure: {type(redis_klines)}")
                    except Exception as e:
                        self.logger.warning(f"Failed to process local Redis data for {symbol}:{interval}: {e}")
                
                # Last resort: Check all klines folders (gateway, server, macbook) and use the one with latest kline
                from utils import _resolve_klines_directories, orjson_default
                klines_dirs = _resolve_klines_directories()
                all_dirs = list(klines_dirs)
                for dir_path in klines_dirs:
                    consolidated_dir = dir_path / "consolidated_klines"
                    if consolidated_dir.exists() and consolidated_dir.is_dir():
                        all_dirs.append(consolidated_dir)
                best_klines = None
                best_timestamp = 0
                for klines_dir in all_dirs:
                    if not klines_dir or not klines_dir.exists():
                        continue
                    cache_file = klines_dir / f"{symbol}_{interval}.json"
                    if not cache_file.exists():
                        continue
                    try:
                        async with self.file_io_semaphore:
                            content = await asyncio.to_thread(cache_file.read_text)
                        data = json.loads(content)
                        klines_list = data.get('data', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                        if klines_list and isinstance(klines_list[0], dict) and 'timestamp' in klines_list[0]:
                            latest_ts = max(k.get('timestamp', 0) for k in klines_list if isinstance(k, dict))
                            if latest_ts > best_timestamp:
                                best_klines = klines_list
                                best_timestamp = latest_ts
                    except Exception:
                        continue
                if best_klines:
                    if await self._append_records_to_cache(symbol, interval, best_klines, f"Klines folder (latest: {best_timestamp})"):
                        continue
                
            except Exception as e:
                self.logger.warning(f"Error ensuring HTF data for {symbol}:{interval}: {e}")

    def _get_interval_ms(self, interval: str) -> int:
        """Get milliseconds for an interval."""
        interval_map = {
            '3m': 3 * 60 * 1000,
            '15m': 15 * 60 * 1000,
            '1h': 60 * 60 * 1000,
            '4h': 4 * 60 * 60 * 1000,
            'D': 24 * 60 * 60 * 1000
        }
        return interval_map.get(interval, 60 * 1000)

# # [+] ADDED: Helper function to contain the cascading logic for a single symbol.
#     async def _resample_symbol_htf_cascading(self, symbol: str):
#         """Runs the full cascade of resampling for a single symbol."""
#         cascade_map = {
#             "15m": "3m",
#             "1h": "15m",
#             "4h": "1h",
#             "D": "4h"
#         }
#         try:
#             for target_tf, source_tf in cascade_map.items():
#                 await self._resample_higher_tf(symbol, target_tf, source_tf)
#         except Exception as e:
#             self.logger.error(f"Error during cascading resample for {symbol}: {e}")

    # # [!] MODIFIED: This task now runs resampling for all symbols in parallel.
    # async def periodic_htf_resampling_task(self, interval_minutes: int = 5):
    #     """Periodically run a cascading HTF resampling to ensure data integrity, processed in a concurrent batch."""
    #     await asyncio.sleep(45)
    #     self.logger.info(f"🔄 Starting CASCADING periodic HTF resampling task (every {interval_minutes} minutes)")

    #     while True:
    #         try:
    #             self.logger.info(f"🔄 Building concurrent batch for cascading HTF resampling across {len(self.symbols_to_run)} symbols...")
                
    #             # Create a task for each symbol to run its own cascade in parallel.
    #             tasks = [self._resample_symbol_htf_cascading(symbol) for symbol in self.symbols_to_run]
    #             await asyncio.gather(*tasks)
                
    #             self.logger.info(f"✅ Concurrent cascading resampling complete for all symbols.")
                
    #         except Exception as e:
    #             self.logger.error(f"Main loop of cascading periodic HTF resampling task failed: {e}")
            
    #         await asyncio.sleep(interval_minutes * 60)

# [+] ADDED: New high-speed, parallel API top-up for emergencies.

    async def emergency_stale_data_fixer(self):
        """EMERGENCY: Fix all stale 15m data immediately"""
        self.logger.info("🚨 Starting immediate stale data fix for ALL symbols")
        
        # Quick scan to find stale symbols - PARALLEL EXECUTION
        self.logger.info(f"🔍 Scanning {len(self.symbols_to_run)} symbols for stale data in parallel...")

        # Create tasks for all symbols
        scan_tasks = [self._check_symbol_staleness(symbol) for symbol in self.symbols_to_run]

        # Execute all scans in parallel
        scan_results = await asyncio.gather(*scan_tasks, return_exceptions=True)

        # Collect stale symbols from results
        stale_symbols = []
        for i, result in enumerate(scan_results):
            if isinstance(result, Exception):
                self.logger.debug(f"Error checking {self.symbols_to_run[i]}: {result}")
                continue

            symbol, is_stale, details = result
            if is_stale:
                stale_symbols.append(symbol)
                if details:
                    self.logger.warning(f"🚨 STALE: {details}")

        # Process stale symbols in parallel batches
        if stale_symbols:
            self.logger.warning(f"🚨 EMERGENCY: Fixing {len(stale_symbols)} stale symbols")

            # Process in smaller batches for even faster performance
            batch_size = 30
            for i in range(0, len(stale_symbols), batch_size):
                batch = stale_symbols[i:i + batch_size]
                self.logger.info(f"🚀 Processing batch {i//batch_size + 1}/{(len(stale_symbols)-1)//batch_size + 1}")

                tasks = [self._emergency_fix_symbol(symbol) for symbol in batch]
                await asyncio.gather(*tasks, return_exceptions=True)

                # Small delay between batches
                if i + batch_size < len(stale_symbols):
                    await asyncio.sleep(0.5)

            self.logger.warning(f"✅ EMERGENCY COMPLETE: Fixed {len(stale_symbols)} stale symbols")
        else:
            self.logger.info("✅ No stale symbols found")

    async def _check_symbol_staleness(self, symbol: str) -> tuple:
        """Check if a symbol has stale data, return (symbol, is_stale, details)"""
        try:
            df_15m = await self.get_klines_df(symbol, "15m")
            if df_15m.empty:
                return symbol, True, f"{symbol} 15m - no data"

            latest_15m = pd.to_datetime(df_15m['timestamp']).max()
            time_diff = datetime.now(timezone.utc) - latest_15m

            if time_diff > timedelta(minutes=30):
                return symbol, True, f"{symbol} 15m - last bar {latest_15m} ({time_diff})"
            else:
                return symbol, False, None

        except Exception as e:
            return symbol, False, None

    async def _emergency_fix_symbol(self, symbol: str):
        """Emergency fix for a single symbol - TRY BACKUP FOLDERS FIRST, then fetch from API if needed"""
        try:
            # CRITICAL: Try backup folders FIRST before hitting API
            from utils import _read_latest_kline_from_dirs, orjson_default
            
            timeframes = ['15m', '1h', '4h', 'D']
            for tf in timeframes:
                try:
                    # Step 1: Try to restore from backup klines_cache folders
                    backup_df, backup_dir, backup_ts = await _read_latest_kline_from_dirs(symbol, tf)
                    
                    if not backup_df.empty and len(backup_df) >= 200:
                        # Found good data in backup folders!
                        await self.merge_and_write_df(backup_df, symbol, tf)
                        self.logger.info(f"✅ EMERGENCY FIX: {symbol} {tf} - RESTORED {len(backup_df)} bars from {backup_dir}")
                        continue  # Skip API call, we have good data
                    
                    # Step 2: Only fetch from API if backup folders don't have good data
                    fetched = await self.fetch_klines_in_chunks(symbol, tf, 1500, end_time_ms=None)
                    if not fetched.empty:
                        cleaned = self._sanitize_klines(fetched, tf)
                        if not cleaned.empty and len(cleaned) >= 200:
                            await self.merge_and_write_df(cleaned, symbol, tf)
                            self.logger.info(f"✅ EMERGENCY FIX: {symbol} {tf} - API fetched {len(cleaned)} bars")
                        else:
                            self.logger.warning(f"⚠️ EMERGENCY FIX: {symbol} {tf} - API returned only {len(cleaned)} bars, SKIPPING")
                except Exception as e:
                    self.logger.debug(f"Emergency fix failed for {symbol} {tf}: {e}")
        except Exception as e:
            self.logger.debug(f"Emergency fix failed for {symbol}: {e}")

    async def emergency_api_topup_stale_symbols(self, stale_symbols: List[str]):
        if not stale_symbols:
            return
        self.logger.warning(f"⚡ EMERGENCY TOP-UP for {len(stale_symbols)} stale symbols")
        env = get_current_environment()['env']
        semaphore = asyncio.Semaphore(config.EZ_PRICES_API_SEMAPHORE.get(env, 8))

        async def fix(symbol: str):
            async with semaphore:
                resampled = await self._resample_all_htf_from_3m(symbol)
                if resampled:
                    self.logger.info(f"✅ Emergency cascade repaired {symbol}")
                    return
                if await self._recover_symbol_from_backups(symbol, ("3m","15m")):
                    resampled = await self._resample_all_htf_from_3m(symbol)
                    if resampled:
                        self.logger.info(f"✅ Emergency backup repair for {symbol}")
                        return
                # Last resort fetch
                api_results = await asyncio.gather(*(self.fetch_klines_in_chunks(symbol, tf, 1500, end_time_ms=None) for tf in self._fast_tf_order), return_exceptions=True)
                merged = False
                for tf, result in zip(self._fast_tf_order, api_results):
                    if isinstance(result, Exception) or result.empty:
                        continue
                    cleaned = self._sanitize_klines(result, tf)
                    if cleaned.empty:
                        continue
                    await self.merge_and_write_df(cleaned, symbol, tf)
                    merged = True
                if merged:
                    await self._resample_all_htf_from_3m(symbol)
                    self.logger.info(f"✅ Emergency API fetched HTFs for {symbol}")
                else:
                    self.logger.warning(f"❌ Emergency cascade failed for {symbol}")

        await asyncio.gather(*(fix(symbol) for symbol in stale_symbols), return_exceptions=True)
    async def periodic_15m_staleness_check(self, interval_seconds: int = 60):
        """Periodically check 15m files for staleness and fix immediately"""
        await asyncio.sleep(60)
        while True:
            try:
                now_utc = datetime.now(timezone.utc)
                stale_count = 0
                fixed_count = 0
                for symbol in self.symbols_to_run:
                    try:
                        target_fp = config.KLINES_CACHE_DIR / f"{symbol}_15m.json"
                        if not target_fp.exists():
                            continue
                        df_15m = await self._read_file_unlocked(target_fp)
                        if df_15m.empty:
                            continue
                        latest_15m_ts = pd.to_datetime(df_15m['timestamp']).max()
                        age_minutes = (now_utc - latest_15m_ts).total_seconds() / 60
                        if age_minutes > 20:
                            stale_count += 1
                            self.logger.warning(f"⚠️ {symbol} 15m STALE: {age_minutes:.1f} min old. FIXING NOW...")
                            resampled = await self._resample_all_htf_from_3m(symbol)
                            if not resampled:
                                restored = await self._recover_symbol_from_backups(symbol, ("3m","15m"))
                                if restored:
                                    resampled = await self._resample_all_htf_from_3m(symbol)
                            if resampled:
                                df_15m_after = await self._read_file_unlocked(target_fp)
                                if not df_15m_after.empty:
                                    latest_after = pd.to_datetime(df_15m_after['timestamp']).max()
                                    age_after = (now_utc - latest_after).total_seconds() / 60
                                    if age_after < age_minutes:
                                        fixed_count += 1
                                        self.logger.info(f"✅ FIXED {symbol} 15m: {age_minutes:.1f}m → {age_after:.1f}m")
                    except Exception as e:
                        self.logger.error(f"Error checking {symbol} 15m staleness: {e}")
                if stale_count > 0:
                    self.logger.warning(f"⚠️ 15m staleness check: {stale_count} stale, {fixed_count} fixed")
            except Exception as e:
                self.logger.error(f"Error in 15m staleness check: {e}")
            await asyncio.sleep(interval_seconds)
    async def emergency_api_topup_stale_symbols_old(self, stale_symbols: List[str]):
        """
        When multiple symbols are detected as stale, this function triggers a high-concurrency
        API fetch to update them all in parallel, fetching 1500 bars for HTF (NOT 3m).
        """
        self.logger.warning(f"🚨 EMERGENCY: Kicking off parallel API top-up for {len(stale_symbols)} stale symbols - fetching 1500 bars for HTF (15m, 1h, 4h, D).")
        # Create a batch of tasks to fetch the latest 1500 bars for higher timeframes (3m comes from websocket)
        tasks = []
        for symbol in stale_symbols:
            for tf in ['15m', '1h', '4h', 'D']:
                tasks.append(self._topup_symbol_interval(symbol, tf, limit_override=1500))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        self.logger.warning(f"🚨 EMERGENCY COMPLETE: API top-up finished. Successfully updated {success_count}/{len(tasks)} symbol+timeframe combinations.")

    # [!] MODIFIED: The emergency check now triggers the new high-speed API top-up function.
    async def _emergency_stale_data_check(self):
        """Check if 15m data is stale and trigger a direct, parallel API fetch if needed."""
        self.logger.info("🔍 Checking for stale 15m data...")
        
        stale_symbols = []
        for symbol in self.symbols_to_run: # Check ALL symbols, not just the first 10
            try:
                df_15m = await self.get_klines_df(symbol, "15m")
                if not df_15m.empty:
                    latest_15m = pd.to_datetime(df_15m['timestamp']).max()
                    time_diff = datetime.now(timezone.utc) - latest_15m
                    
                    # If data is more than 30 minutes old, it's stale.
                    if time_diff > timedelta(minutes=30):
                        stale_symbols.append(symbol)
                        self.logger.warning(f"🚨 STALE 15m data: {symbol} last update {latest_15m} ({time_diff})")
            except Exception as e:
                self.logger.debug(f"Error checking {symbol} 15m data: {e}")
        
        if stale_symbols:
            # Instead of a slow resampling task, trigger the fast, parallel API fetch.
            await self.emergency_api_topup_stale_symbols(stale_symbols)

    async def restore_destroyed_klines_from_backups(self):
        """STARTUP: Restore any klines files that were destroyed from backups.
        Uses REALISTIC minimum bar counts per timeframe and prioritizes 15m (CRITICAL).
        """
        self.logger.info("🔄 STARTUP: Checking for destroyed klines files and restoring from backups...")
        restored_count = 0
        checked_count = 0
        min_acceptable_bars = {'3m': 200, '15m': 200, '1h': 100, '4h': 50, 'D': 20}
        ideal_bars = {'3m': 1500, '15m': 1500, '1h': 1000, '4h': 400, 'D': 400}
        priority_order = ['15m', '1h', '4h', '3m', 'D']
        for interval in priority_order:
            min_bars = min_acceptable_bars[interval]
            for symbol in self.symbols_to_run:
                checked_count += 1
                file_path = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
                try:
                    existing_df = await self._read_file_unlocked(file_path)
                    existing_count = len(existing_df) if not existing_df.empty else 0
                    if existing_count >= min_bars:
                        continue
                    if existing_count == 0:
                        self.logger.debug(f"📄 {symbol}_{interval}: empty file - attempting restore from backup...")
                    else:
                        self.logger.warning(f"🚨 {symbol}_{interval}: only {existing_count} bars (< {min_bars} minimum) - attempting restore from backup...")
                    backup_df = await self._read_consolidated_backup(symbol, interval)
                    if not backup_df.empty and len(backup_df) > existing_count:
                        if existing_count > 0:
                            backup_df['timestamp'] = pd.to_datetime(backup_df['timestamp'], utc=True)
                            existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'], utc=True)
                            merged = pd.concat([existing_df, backup_df], ignore_index=True)
                            merged = merged.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
                            backup_df = merged
                        await self._write_file_unlocked(backup_df, file_path, existing_bar_count=0)
                        self.logger.info(f"✅ RESTORED {symbol}_{interval}: {existing_count} → {len(backup_df)} bars from backup")
                        restored_count += 1
                    elif not backup_df.empty:
                        self.logger.debug(f"Backup for {symbol}_{interval} has {len(backup_df)} bars (not better than existing {existing_count})")
                    else:
                        if existing_count < min_bars:
                            self.logger.debug(f"No backup found for {symbol}_{interval} (has {existing_count}/{min_bars} bars)")
                except Exception as e:
                    self.logger.error(f"Error restoring {symbol}_{interval}: {e}")
        self.logger.info(f"🔄 STARTUP RESTORATION COMPLETE: Checked {checked_count} files, restored {restored_count} files from backups")

    async def main(self):
        self.logger.info("="*50); self.logger.info("ez_prices Resampler & Gap-Filler - Starting"); self.logger.info("="*50)
        max_tunnel_retries = 5
        for retry in range(max_tunnel_retries):
            try:
                env_info = get_current_environment()
                gw_host, gw_port = env_info['redis_connections'].get('gateway', ('localhost', 6379))

            except Exception as e:
                if retry < max_tunnel_retries - 1:
                    self.logger.warning(f"⚠️ SSH tunnel setup failed (attempt {retry+1}/{max_tunnel_retries}): {e}. Retrying in 5s...")
                    await asyncio.sleep(5)
                else:
                    self.logger.error(f"❌ SSH tunnel setup failed after {max_tunnel_retries} attempts: {e}")
        try:
            with open(config.SYMBOLS_FILE, 'r') as f:
                symbols_from_file = json.load(f)
            self.session = await self._init_http_session()
            self.redis_manager=None
            rum_manager=None
            try:
                rum_manager=await get_rum_manager()
            except Exception as e:
                self.logger.warning(f"Rum manager unavailable: {e}")
            if rum_manager and getattr(rum_manager,'has_any_connection',lambda:False)():
                self.redis_manager=rum_manager
                self.logger.info("Using Rum manager for Redis")
            else:
                self.logger.warning("Rum manager missing or offline, trying simple Redis manager")
                legacy_manager=None
                try:
                    legacy_manager=await get_simple_redis_manager()
                except Exception as e:
                    self.logger.warning(f"Simple Redis manager unavailable: {e}")
                if legacy_manager and getattr(legacy_manager,'has_any_connection',lambda:False)():
                    self.redis_manager=legacy_manager
                    self.logger.info("Using simple Redis manager")
            if not self.redis_manager:
                self.logger.warning("⚠️ No Redis managers online – continuing without Redis, using file-only mode")

            # Grab dedicated clients from manager if available
            if self.redis_manager:
                available=self.redis_manager.get_available_connections()
                self.logger.info(f"Redis targets available: {available}")
                self.redis_client=await self._get_manager_connection(self.redis_manager,'local') if 'local' in available else None
                self.redis_client_read=await self._get_manager_connection(self.redis_manager,'gateway') if 'gateway' in available else None

            # Initialize enhanced validation and gap filling systems with Redis client if present
            if not self.redis_client:
                try:
                    # Use 127.0.0.1 explicitly to avoid IPv6 issues
                    host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
                    # LONG timeout - Redis can take time to appear
                    self.redis_client=redis.Redis(host=host,port=config.REDIS_PORT,db=config.REDIS_DB,decode_responses=True,
                                                  socket_connect_timeout=30,socket_timeout=60,max_connections=3000,
                                                  retry_on_timeout=True,
                                                  retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError])
                    # NON-BLOCKING: No ping wait, connects in background
                    self.logger.info(f"Local Redis client created ({host}:{config.REDIS_PORT}) - connecting in background. System continues with JSON files.")
                    self.redis_client=None  # Don't use yet
                except Exception as e:
                    self.logger.warning(f"Local Redis fallback failed: {e}")
                    self.redis_client=None
            if not self.redis_client_read:
                try:
                    env_info = get_current_environment()
                    gw_host, gw_port = env_info['redis_connections'].get('gateway', ('localhost', 6379))
                    # Fix IPv6 issue
                    if gw_host in ['localhost', '::1']:
                        gw_host = '127.0.0.1'
                    # MUCH longer timeout for gateway - SSH tunnels can be slow
                    self.redis_client_read=redis.Redis(host=gw_host,port=gw_port,db=config.REDIS_DB,decode_responses=True,
                                                       socket_connect_timeout=60,socket_timeout=300,max_connections=3000,
                                                       health_check_interval=30,retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError],
                                                       retry_on_timeout=True)
                    # NON-BLOCKING: No ping wait, connects in background
                    self.logger.info(f"Gateway Redis client created ({gw_host}:{gw_port}) - connecting in background. System continues with JSON files.")
                    self._gateway_redis_failures=0
                    self.redis_client_read=None  # Don't use yet
                except Exception as e:
                    self.logger.warning(f"Gateway Redis fallback failed: {e}")
                    self.redis_client_read=None
            self.validator = EnhancedDataValidator(config.KLINES_CACHE_DIR, self.backup_system, self.redis_client)
            self.gap_filler = EnhancedGapFiller(config.KLINES_CACHE_DIR, self.backup_system, self.redis_client)
        except Exception as e:
            self.logger.critical(f"Fatal setup error: {e}")
            return
        
        self.logger.info(f"Proceeding with immediate top-up to latest bars and setting data-ready flag early: {M_READY_FLAG_FILE}")
        
        # ------------------------------------------------------------
        # HTTP SESSION SETUP WITH DEDICATED-IP FALLBACK
        # ------------------------------------------------------------
        # Session is already initialized above, keep it open for background tasks
        try:
            live_usdc_pairs = set()
            try:
                with open(config.LIVE_USDC_PAIRS_FILE, 'r') as f:
                    live_usdc_pairs = set(json.load(f))
                self.logger.info(f"Loaded {len(live_usdc_pairs)} live USDC pairs from file for conversion.")
            except (FileNotFoundError, json.JSONDecodeError) as e:
                self.logger.error(f"Could not load USDC pairs file ({e}). Falling back to API fetch.")
            # Cache on instance for later publish conversions
            self.live_usdc_pairs = live_usdc_pairs

            live_usdc_pairs = None
            retry_delay = 5  # Start with a 5-second delay
            while live_usdc_pairs is None:
                live_usdc_pairs = await get_live_usdc_pairs(self.session)
                if live_usdc_pairs is None:
                    self.logger.error(f"Could not fetch live USDC pairs. Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 300)

            final_workload = set()
            for symbol in symbols_from_file:
                # If a USDT pair has a corresponding USDC version, use the USDC pair.
                if symbol.endswith("USDT") and f"{symbol.replace('USDT', 'USDC')}" in live_usdc_pairs:
                    final_workload.add(symbol.replace("USDT", "USDC"))
                else:
                    final_workload.add(symbol)
        
            self.symbols_to_run = sorted(list(final_workload))
            self.logger.info(f" Final workload determined. Processing {len(self.symbols_to_run)} symbols after USDC conversion.")
            self.logger.info("🔄 Checking for destroyed klines and restoring from backups...")
            await self.restore_destroyed_klines_from_backups()
            total_start_time = time.time()
            await self.emergency_stale_data_fixer()

            # HTF processing tasks - these are critical for updating stale data
            self.logger.info("🚀 Starting HTF processing background tasks...")
            #self._bg_tasks.append(asyncio.create_task(self.high_speed_periodic_resampling())) #TEMP OUT

            self._bg_tasks.append(asyncio.create_task(self.aggressive_periodic_resampling()))



            self._bg_tasks.append(asyncio.create_task(self.real_time_boundary_resampler()))
            self._bg_tasks.append(asyncio.create_task(self.periodic_htf_broadcast_task()))
            self._bg_tasks.append(asyncio.create_task(self.periodic_heartbeat_task()))
            self._bg_tasks.append(asyncio.create_task(self.kline_update_listener()))
            self._bg_tasks.append(asyncio.create_task(self.aggressive_gap_filling_task()))
            self._bg_tasks.append(asyncio.create_task(self.periodic_redis_health_check()))
            self._bg_tasks.append(asyncio.create_task(self.periodic_backup_cleanup_task()))
            self._bg_tasks.append(asyncio.create_task(self.periodic_tmp_cleanup_task()))
            self._bg_tasks.append(asyncio.create_task(self.periodic_disk_space_monitor()))
            self._bg_tasks.append(asyncio.create_task(self.periodic_15m_staleness_check()))
            self._bg_tasks.append(asyncio.create_task(self.periodic_D_klines_health_check()))

            await self._populate_cache_from_consolidated()
            await self._ensure_primary_topup()
            primary_ready = await self._wait_for_primary_readiness()
            await self._set_data_ready_flags(primary_ready)
            await self._emergency_stale_data_check()

            await self._schedule_additional_tasks()
            await self._post_flag_bootstrap(total_start_time)
            
            # Keep the session alive for background tasks
            try:
                await asyncio.Future()  # Run forever
            except asyncio.CancelledError:
                self.logger.info("Shutdown requested, cleaning up...")
            finally:
                await self.cleanup()
                
        except Exception as e:
            self.logger.critical(f"Fatal setup error: {e}")
            await self.cleanup()
            return

    async def _consolidate_existing_backups(self):
        """One-time consolidation of existing backups into consolidated format."""
        consolidation_flag = config.KLINES_CACHE_DIR / "consolidation_complete.flag"

        # Skip if already consolidated
        if consolidation_flag.exists():
            self.logger.info("Consolidation already completed, skipping")
            return

        self.logger.info("🔄 === ONE-TIME BACKUP CONSOLIDATION STARTING ===")

        try:
            consolidated_count = 0
            skipped_count = 0

            # Define backup directories to scan
            backup_dirs = [
                getattr(config, 'BACKUP_KLINES_CACHE', None),  # klines_cache/backup
                config.KLINES_CACHE_DIR / "backups_mac"  # klines_cache/backups_mac
            ]

            # Filter out None values
            backup_dirs = [d for d in backup_dirs if d is not None]

            # Also check for any other backup directories that might exist
            potential_dirs = [
                config.KLINES_CACHE_DIR / "backup",
                config.KLINES_CACHE_DIR / "backups_mac",
                config.KLINES_CACHE_DIR / "backups"
            ]

            for backup_dir in potential_dirs:
                if not backup_dir.exists():
                    continue

                self.logger.info(f"Scanning backup directory: {backup_dir}")

                # Process all JSON files in this backup directory
                json_files = list(backup_dir.glob("*.json"))
                self.logger.info(f"Found {len(json_files)} JSON files in {backup_dir}")

                for json_file in json_files:
                    try:
                        # Read the existing backup file
                        df = await self._read_file_unlocked(json_file)
                        if df.empty:
                            skipped_count += 1
                            continue

                        # Parse symbol and interval from filename
                        filename = json_file.stem
                        parts = filename.split('_')
                        if len(parts) < 2:
                            self.logger.warning(f"Skipping file with unexpected format: {filename}")
                            skipped_count += 1
                            continue

                        symbol = parts[0]
                        interval = parts[1]

                        # Validate interval
                        valid_intervals = ["3m", "15m", "1h", "4h", "D"]
                        if interval not in valid_intervals:
                            self.logger.warning(f"Skipping file with invalid interval '{interval}': {filename}")
                            skipped_count += 1
                            continue

                        # Clean and consolidate the data
                        cleaned_df = self._sanitize_klines(df, interval)

                        if cleaned_df.empty:
                            self.logger.warning(f"Empty data after sanitization for {filename}")
                            skipped_count += 1
                            continue

                        # Write to consolidated backup location
                        # Use the backup system's consolidated directory structure
                        consolidated_filename = f"{symbol}_{interval}.json"
                        consolidated_file = self.backup_system.backup_dir / consolidated_filename
                        existing_consolidated = await self._read_file_unlocked(consolidated_file) if consolidated_file.exists() else pd.DataFrame()
                        existing_count = len(existing_consolidated) if not existing_consolidated.empty else 0
                        await self._write_file_unlocked(cleaned_df, consolidated_file, existing_bar_count=existing_count)
                        consolidated_count += 1

                        self.logger.debug(f"Consolidated {filename} -> {consolidated_file}")

                    except Exception as e:
                        self.logger.error(f"Error consolidating {json_file}: {e}")
                        skipped_count += 1

            # Create consolidation complete flag
            consolidation_flag.touch()
            self.logger.info(f"✅ Consolidation completed: {consolidated_count} files consolidated, {skipped_count} skipped")

        except Exception as e:
            self.logger.error(f"Consolidation failed: {e}")
            # Don't re-raise - allow startup to continue even if consolidation fails

    async def _populate_higher_timeframes_from_consolidated_backup(self):
        """Populate Redis with 4h and D timeframe data from consolidated backup on startup to avoid websocket delays."""
        self.logger.info("🚀 === STARTUP: POPULATING 4h/D REDIS FROM CONSOLIDATED BACKUP ===")
        try:
            populated_count = 0
            failed_count = 0
            higher_timeframes = ["4h", "D"]
            for symbol in self.symbols_to_run:
                for interval in higher_timeframes:
                    try:
                        consolidated_df = await self._read_consolidated_backup(symbol, interval)

                        if not consolidated_df.empty:
                            # Convert to Redis format
                            redis_klines = []
                            # Get the last 1800 bars (or all if less)
                            max_bars = min(len(consolidated_df), 4800)
                            for _, row in consolidated_df.tail(max_bars).iterrows():
                                redis_klines.append({
                                    'timestamp': (row['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ') if hasattr(row['timestamp'], 'strftime') else str(row['timestamp'])),
                                    'open': float(row['open']), 'high': float(row['high']),
                                    'low': float(row['low']), 'close': float(row['close']),
                                    'volume': float(row['volume'])
                                })

                            if redis_klines:
                                # Create payload in the same format as ez_prices_ws.py
                                payload = {
                                    "symbol": symbol,
                                    "interval": interval,
                                    "published_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                                    "klines": redis_klines,
                                    "count": len(redis_klines),
                                    "last_timestamp": redis_klines[-1]['timestamp'] if redis_klines else None,
                                    "source": "consolidated_backup"
                                }

                                # Publish to Redis
                                redis_key = f"klines:{symbol}:{interval}"
                                await self.redis_client.setex(
                                    redis_key,
                                    REDIS_EXPIRY_SECONDS * 6,  # Same expiry as other scripts
                                    json.dumps(payload)
                                )

                                # Also publish metadata
                                metadata_key = f"klines_meta:{symbol}:{interval}"
                                metadata = {
                                    "symbol": symbol,
                                    "interval": interval,
                                    "last_updated": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                                    "count": len(redis_klines),
                                    "last_timestamp": redis_klines[-1]['timestamp'] if redis_klines else None,
                                    "source": "consolidated_backup"
                                }
                                await self.redis_client.setex(metadata_key, REDIS_EXPIRY_SECONDS * 6, json.dumps(metadata))

                                populated_count += 1
                                self.logger.debug(f"✅ Startup: Populated Redis with {len(redis_klines)} {interval} bars for {symbol} from consolidated backup")

                                # Small delay to avoid overwhelming Redis
                                await asyncio.sleep(0.01)
                        else:
                            self.logger.debug(f"No consolidated backup data available for {symbol}_{interval}")

                    except Exception as e:
                        failed_count += 1
                        self.logger.warning(f"Failed to populate {interval} Redis for {symbol} from consolidated backup: {e}")

            self.logger.info(f"✅ === STARTUP 4H/D CONSOLIDATED BACKUP POPULATION COMPLETE: {populated_count} entries populated, {failed_count} failed ===")

        except Exception as e:
            self.logger.error(f"Failed to populate 4h/D Redis from consolidated backup on startup: {e}")



    async def _read_consolidated_backup(self, symbol: str, interval: str) -> pd.DataFrame:
        """Load historical klines from the consolidated backup archive."""
        # Use KLINES_CACHE/BACKUPS_CONSOLIDATED as requested
        klines_cache_dir = getattr(config, 'KLINES_CACHE_DIR', None)
        if not klines_cache_dir:
            return pd.DataFrame()

        consolidated_dir = klines_cache_dir / "backups_consolidated"
        if not consolidated_dir.exists():
            # Fallback to the configured consolidated cache
            consolidated_dir = getattr(config, 'CONSOLIDATED_KLINES_CACHE', None)
            if not consolidated_dir:
                return pd.DataFrame()

        try:
            consolidated_path = Path(consolidated_dir)
        except (TypeError, Exception):
            consolidated_path = None

        if not consolidated_path or not consolidated_path.exists():
            return pd.DataFrame()

        pattern = consolidated_path / f"{symbol}_{interval}_part*.json"
        try:
            files = list(pattern.parent.glob(pattern.name))
            if not files:
                return pd.DataFrame()

            # Load and concatenate all parts in parallel with semaphore protection
            dfs = []
            if files:
                # Helper to read a file with semaphore protection
                async def read_file_safe(file_path):
                    async with self.file_io_semaphore:
                        return await self._read_file_unlocked(file_path)
                
                # Create tasks for all files with semaphore protection
                file_tasks = [read_file_safe(file_path) for file_path in sorted(files)]

                # Execute all file reads in parallel
                file_results = await asyncio.gather(*file_tasks, return_exceptions=True)

                # Process results
                for i, result in enumerate(file_results):
                    if isinstance(result, Exception):
                        self.logger.debug(f"Failed to load consolidated part {files[i]}: {result}")
                        continue
                    df = result
                    if df is not None and not df.empty:
                        dfs.append(df)
            if dfs:
                combined_df = pd.concat(dfs, ignore_index=True)
                combined_df = combined_df.drop_duplicates(subset=['timestamp'], keep='last')
                combined_df = combined_df.sort_values('timestamp').reset_index(drop=True)
                return combined_df
        except Exception as e:
            self.logger.debug(f"Error accessing consolidated backup for {symbol}_{interval}: {e}")

        return pd.DataFrame()

    async def _async_fill_from_backups_while_waiting(self, symbol: str, interval: str):
        """Try to fill data from backups while waiting for API calls to complete."""
        try:
            file_path = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
            existing_df = await self.get_klines_df(symbol, interval)
            if not existing_df.empty and len(existing_df) >= 900:
                self.logger.debug(f"Skipping backup for {symbol}_{interval} - already have {len(existing_df)} bars (>= 900)")
                return
            consolidated_df = await self._read_consolidated_backup(symbol, interval)
            if not consolidated_df.empty:
                if not existing_df.empty:
                    existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'], utc=True)
                    consolidated_df['timestamp'] = pd.to_datetime(consolidated_df['timestamp'], utc=True)
                    existing_latest = existing_df['timestamp'].max()
                    backup_latest = consolidated_df['timestamp'].max()
                    if backup_latest > existing_latest:
                        merged_df = pd.concat([existing_df, consolidated_df], ignore_index=True)
                        merged_df = merged_df.drop_duplicates(subset=['timestamp'], keep='last').sort_values('timestamp')
                        await self._write_file_unlocked(merged_df, file_path, existing_bar_count=len(existing_df))
                        self.logger.info(f"Pre-filled {symbol}_{interval} from backup - merged to {len(merged_df)} bars (no clipping)")
                    else:
                        self.logger.debug(f"Skipping backup for {symbol}_{interval} - existing data is newer")
                else:
                    await self._write_file_unlocked(consolidated_df, file_path, existing_bar_count=0)
                    self.logger.info(f"Pre-filled {symbol}_{interval} from backup ({len(consolidated_df)} bars)")
        except Exception as e:
            self.logger.debug(f"Background backup fill failed for {symbol}_{interval}: {e}")

    async def periodic_disk_space_monitor(self, interval_seconds: int = 300):
        """Monitor disk space and trigger cleanup/consolidation when usage exceeds 90%."""
        if hasattr(self, '_disk_monitor_started') and self._disk_monitor_started:
            return
        self._disk_monitor_started = True
        
        # Wait for initial setup
        await asyncio.sleep(60)
        
        while True:
            try:
                # Check disk usage for the klines cache directory
                disk_usage = shutil.disk_usage(config.KLINES_CACHE_DIR)
                total_space = disk_usage.total
                used_space = disk_usage.used
                free_space = disk_usage.free
                usage_percent = (used_space / total_space) * 100
                
                self.logger.debug(f"💾 Disk usage: {usage_percent:.1f}% ({used_space / (1024**3):.1f}GB used, {free_space / (1024**3):.1f}GB free)")
                
                if usage_percent > 90:
                    self.logger.warning(f"🚨 HIGH DISK USAGE: {usage_percent:.1f}% - Starting emergency cleanup!")
                    
                    # First, try to consolidate existing data
                    try:
                        self.logger.info("🔄 Emergency consolidation starting...")
                        await self.backup_system.consolidate_all_symbols()
                        self.logger.info("✅ Emergency consolidation completed")
                    except Exception as e:
                        self.logger.error(f"Emergency consolidation failed: {e}")
                    
                    # Check disk usage again after consolidation
                    disk_usage = shutil.disk_usage(config.KLINES_CACHE_DIR)
                    usage_percent = (disk_usage.used / disk_usage.total) * 100
                    
                    if usage_percent > 85:  # Still high after consolidation
                        self.logger.warning(f"🚨 STILL HIGH DISK USAGE: {usage_percent:.1f}% - Starting aggressive cleanup!")
                        
                        # Delete old backup files (older than 7 days)
                        try:
                            await self._aggressive_cleanup_old_files()
                        except Exception as e:
                            self.logger.error(f"Aggressive cleanup failed: {e}")
                        
                        # Final check
                        disk_usage = shutil.disk_usage(config.KLINES_CACHE_DIR)
                        final_usage = (disk_usage.used / disk_usage.total) * 100
                        self.logger.info(f"💾 Final disk usage after cleanup: {final_usage:.1f}%")
                        
                elif usage_percent > 80:
                    self.logger.info(f"⚠️ Disk usage at {usage_percent:.1f}% - Monitoring closely")
                    
            except Exception as e:
                self.logger.error(f"Error in disk space monitoring: {e}")
            
            await asyncio.sleep(interval_seconds)

    async def _aggressive_cleanup_old_files(self):
        """Clip klines files to 1800 bars to free disk space without losing recent data."""
        self.logger.info("🧹 Starting aggressive cleanup - clipping files to 1800 bars...")
        try:
            backup_dirs = list(config.KLINES_CACHE_DIR.glob("*_backup_*"))
            current_time = time.time()
            seven_days_ago = current_time - (7 * 24 * 3600)
            removed_dirs = 0
            freed_space = 0
            for backup_dir in backup_dirs:
                try:
                    if backup_dir.is_dir():
                        dir_mtime = backup_dir.stat().st_mtime
                        if dir_mtime < seven_days_ago:
                            dir_size = sum(f.stat().st_size for f in backup_dir.rglob('*') if f.is_file())
                            freed_space += dir_size
                            shutil.rmtree(backup_dir)
                            removed_dirs += 1
                            self.logger.debug(f"Removed old backup directory: {backup_dir.name}")
                except Exception as e:
                    self.logger.warning(f"Failed to remove {backup_dir}: {e}")
            json_files = list(config.KLINES_CACHE_DIR.glob("*_*.json"))
            clipped_files = 0
            for json_file in json_files:
                try:
                    if json_file.is_file() and not json_file.name.startswith('.tmp'):
                        original_size = json_file.stat().st_size
                        df = await self._read_file_unlocked(json_file)
                        # Server: keep ALL bars for backtesting — never clip
                        # MacBook: all TFs clip >1800 → 1200 to save disk
                        if env == 'server':
                            continue
                        if not df.empty and len(df) > 1800:
                            before_bars = len(df)
                            df_clipped = df.tail(1200).copy()
                            await self._write_file_unlocked(df_clipped, json_file)
                            new_size = json_file.stat().st_size
                            freed_space += (original_size - new_size)
                            clipped_files += 1
                            self.logger.debug(f"Clipped {json_file.name}: {before_bars} → 1200 bars, saved {(original_size - new_size) / 1024:.1f}KB")
                except Exception as e:
                    self.logger.warning(f"Failed to clip {json_file}: {e}")
            self.logger.info(f"🧹 Aggressive cleanup completed: {removed_dirs} backup dirs removed, {clipped_files} files clipped to 1800 bars, {freed_space / (1024**3):.2f}GB freed")
        except Exception as e:
            self.logger.error(f"Error in aggressive cleanup: {e}")

    async def _perform_data_quality_check(self):
        """Perform comprehensive data quality check and automatic gap filling."""
        try:
            if not self.validator:
                self.logger.warning("Validator not initialized, skipping data quality check")
                return
                
            self.logger.info("🔍 Performing enhanced data quality check...")
            
            # Get all symbols and intervals to check
            intervals = ['3m', '15m', '1h', '4h', 'D']
            
            # Validate all symbols
            validation_results = await self.validator.validate_all_symbols(
                self.symbols_to_run, intervals
            )
            
            # Log summary
            summary = validation_results['summary']
            self.logger.info(f"📊 Data Quality Summary: {summary['complete']} complete, "
                           f"{summary['needs_restoration']} need restoration, "
                           f"{summary['critical_gaps']} critical gaps, "
                           f"{summary['total_gaps']} total gaps")
            
            # Identify symbols that need restoration
            symbols_needing_restoration = []
            for symbol, symbol_results in validation_results['validation_results'].items():
                for interval, validation in symbol_results.items():
                    if validation['needs_restoration']:
                        symbols_needing_restoration.append((symbol, interval))
            
            # Perform automatic gap filling for symbols that need it
            if symbols_needing_restoration:
                self.logger.info(f"🔧 Starting automatic gap filling for {len(symbols_needing_restoration)} symbol/interval combinations...")
                
                gap_fill_results = await self.gap_filler.fill_all_missing_data(
                    [s[0] for s in symbols_needing_restoration],
                    [s[1] for s in symbols_needing_restoration]
                )
                
                self.logger.info(f"✅ Gap filling completed: {gap_fill_results['successful_restorations']} successful, "
                               f"{gap_fill_results['failed_restorations']} failed")
            
            # Log critical gaps for manual review
            for symbol, symbol_results in validation_results['validation_results'].items():
                for interval, validation in symbol_results.items():
                    if validation['gap_count'] > 0:
                        critical_gaps = [g for g in validation['gaps'] if g['severity'] == 'critical']
                        if critical_gaps:
                            self.logger.warning(f"🚨 {symbol}_{interval}: {len(critical_gaps)} critical gaps detected")
                            for gap in critical_gaps[:3]:  # Show first 3 critical gaps
                                self.logger.warning(f"   Gap: {gap['start_time']} → {gap['end_time']} "
                                                  f"({gap['duration']}, {gap['missing_klines']} missing klines)")
            
        except Exception as e:
            self.logger.error(f"Error in data quality check: {e}")
    
    async def validate_specific_symbol(self, symbol: str, interval: str) -> Dict:
        """Validate data completeness for a specific symbol and interval."""
        if not self.validator:
            return {'status': 'error', 'error': 'Validator not initialized'}
        return await self.validator.validate_data_completeness(symbol, interval)
    
    async def fill_gaps_for_symbol(self, symbol: str, interval: str) -> bool:
        """Fill gaps for a specific symbol and interval."""
        if not self.gap_filler:
            return False
        return await self.gap_filler.fill_missing_data(symbol, interval)
    
    async def comprehensive_data_audit(self) -> Dict:
        """Perform a comprehensive audit of all data completeness."""
        if not self.validator:
            return {'status': 'error', 'error': 'Validator not initialized'}
            
        self.logger.info("🔍 Starting comprehensive data audit...")
        
        intervals = ['3m', '15m', '1h', '4h', 'D']
        
        # Perform validation
        validation_results = await self.validator.validate_all_symbols(
            self.symbols_to_run, intervals
        )
        
        # Generate detailed report
        report = {
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'total_symbols': len(self.symbols_to_run),
            'intervals_checked': intervals,
            'validation_results': validation_results,
            'recommendations': []
        }
        
        # Generate recommendations
        symbols_with_issues = []
        for symbol, symbol_results in validation_results['validation_results'].items():
            for interval, validation in symbol_results.items():
                if validation['completeness_score'] < 0.8:
                    symbols_with_issues.append(f"{symbol}_{interval}")
        
        if symbols_with_issues:
            report['recommendations'].append({
                'priority': 'high',
                'action': 'restore_from_backup',
                'symbols': symbols_with_issues[:10],  # Limit to first 10
                'description': f"{len(symbols_with_issues)} symbol/interval combinations need restoration"
            })
        
        # Log summary
        summary = validation_results['summary']
        self.logger.info(f"📊 Comprehensive Audit Complete:")
        self.logger.info(f"   Complete: {summary['complete']}")
        self.logger.info(f"   Need Restoration: {summary['needs_restoration']}")
        self.logger.info(f"   Critical Gaps: {summary['critical_gaps']}")
        self.logger.info(f"   Total Gaps: {summary['total_gaps']}")
        
        return report
    
    async def _analyze_all_gaps_for_filling(self) -> Dict:
        """Analyze gaps across all symbols for gap filling with MASSIVE parallelism."""
        self.logger.info("🔍 Starting MASSIVE parallel gap analysis...")

        # Create ALL tasks upfront for maximum parallelism
        all_tasks = []
        intervals = ['3m', '15m', '1h', '4h', 'D']

        for symbol in self.symbols_to_run:
            for interval in intervals:
                all_tasks.append(self._analyze_symbol_gaps_for_filling(symbol, interval))

        # Execute all gap analysis tasks in parallel
        total_tasks = len(all_tasks)
        self.logger.info(f"🔥 Executing {total_tasks} gap analysis tasks in parallel...")

        # Use semaphore for controlled concurrency
        env = get_current_environment()['env']
        semaphore = asyncio.Semaphore(config.EZ_PRICES_API_SEMAPHORE.get(env, 10))
        async def run_gap_analysis_with_semaphore(task):
            async with semaphore:
                return await task

        gap_results = await asyncio.gather(
            *[run_gap_analysis_with_semaphore(task) for task in all_tasks],
            return_exceptions=True
        )

        # Process results and organize by symbol
        gaps_by_symbol = {}
        task_idx = 0
        for symbol in self.symbols_to_run:
            symbol_gaps = {}
            for interval in intervals:
                result = gap_results[task_idx]
                task_idx += 1

                if isinstance(result, Exception):
                    self.logger.debug(f"Gap analysis failed for {symbol}_{interval}: {result}")
                    continue

                if result:  # If gaps were found
                    symbol_gaps[interval] = result

            if symbol_gaps:
                gaps_by_symbol[symbol] = symbol_gaps

        self.logger.info(f"✅ Gap analysis complete: {len(gaps_by_symbol)} symbols have gaps")
        return gaps_by_symbol
    
    async def _analyze_symbol_gaps_for_filling(self, symbol: str, interval: str) -> List[Dict]:
        """Analyze gaps for a specific symbol and interval for filling."""
        try:
            # Load current data
            df = await self.validator._load_klines_data(symbol, interval)
            
            if df.empty or len(df) < 2:
                return []
            
            # Convert timestamps and sort
            timestamps = pd.to_datetime(df['timestamp'], format='mixed', utc=True, errors='coerce').sort_values()
            
            # Get expected interval
            expected_interval = self.validator._get_expected_interval(interval)
            threshold = expected_interval * 2  # 2x expected interval as gap threshold
            
            gaps = []
            for i in range(1, len(timestamps)):
                time_diff = timestamps.iloc[i] - timestamps.iloc[i-1]
                
                if time_diff > threshold:
                    missing_klines = int(time_diff.total_seconds() / expected_interval.total_seconds())
                    
                    gap_info = {
                        'start_time': timestamps.iloc[i-1],
                        'end_time': timestamps.iloc[i],
                        'duration': time_diff,
                        'missing_klines': missing_klines,
                        'severity': self.validator._calculate_gap_severity(time_diff, expected_interval)
                    }
                    gaps.append(gap_info)
            
            return gaps
            
        except Exception as e:
            self.logger.error(f"Error analyzing gaps for {symbol}_{interval}: {e}")
            return []
    
    async def _fill_gaps_in_batches(self, gaps_by_symbol: Dict):
        """Fill gaps in batches to avoid overwhelming the system."""
        batch_size = 5  # Process 5 symbols at a time
        symbols = list(gaps_by_symbol.keys())
        
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            
            # Process batch in parallel
            tasks = []
            for symbol in batch:
                if symbol in gaps_by_symbol:
                    tasks.append(self._fill_symbol_gaps(symbol, gaps_by_symbol[symbol]))
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # Small delay between batches
            await asyncio.sleep(2)
    
    async def _fill_symbol_gaps(self, symbol: str, symbol_gaps: Dict):
        """Fill gaps for a specific symbol."""
        try:
            for interval, gaps in symbol_gaps.items():
                # Try backup restoration first
                if await self._try_backup_restoration(symbol, interval):
                    self.logger.info(f"✅ Restored {symbol}_{interval} from backup")
                    continue
                
                # Try API calls for recent gaps
                recent_gaps = [g for g in gaps if self._is_recent_gap(g)]
                if recent_gaps:
                    await self._fill_gaps_with_api(symbol, interval, recent_gaps)
                    
        except Exception as e:
            self.logger.error(f"Error filling gaps for {symbol}: {e}")
    
    async def _try_backup_restoration(self, symbol: str, interval: str) -> bool:
        """Try to restore data from backup."""
        try:
            # Check for backup files
            backup_dir = Path("/Volumes/SSD2T/backup/klines_cache")
            if not backup_dir.exists():
                return False
            
            # Look for most recent backup
            backup_folders = [f for f in backup_dir.glob(f"{symbol}_backup_*") if f.is_dir()]
            if not backup_folders:
                return False
            
            # Get most recent backup
            latest_backup = max(backup_folders, key=lambda p: p.stat().st_mtime)
            backup_file = latest_backup / f"{symbol}_{interval}.json"
            
            if not backup_file.exists():
                return False
            
            # Load backup data
            with open(backup_file, 'r') as f:
                backup_data = json.load(f)
            
            if isinstance(backup_data, dict) and 'data' in backup_data:
                df = pd.DataFrame(backup_data['data'])
            elif isinstance(backup_data, list):
                df = pd.DataFrame(backup_data)
            else:
                return False
            
            if df.empty:
                return False
            
            # Clean and save data
            df = clean_kline_data(df)
            if not df.empty:
                await self.merge_and_write_df(df, symbol, interval)
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error in backup restoration for {symbol}_{interval}: {e}")
            return False
    
    def _is_recent_gap(self, gap: Dict) -> bool:
        """Check if gap is recent enough to fill with API calls."""
        gap_start = gap['start_time']
        if isinstance(gap_start, str):
            gap_start = pd.to_datetime(gap_start, utc=True)
        
        days_ago = (datetime.now(timezone.utc) - gap_start).days
        return days_ago <= 30  # Only fill gaps from last 30 days
    
    async def _fill_gaps_with_api(self, symbol: str, interval: str, gaps: List[Dict]):
        """Fill gaps using API calls with rate limiting."""
        try:
            for gap in gaps:
                await self._fill_single_gap_with_api(symbol, interval, gap)
                # Rate limiting
                await asyncio.sleep(0.2)
                
        except Exception as e:
            self.logger.error(f"Error filling gaps with API for {symbol}_{interval}: {e}")
    
    async def _fill_single_gap_with_api(self, symbol: str, interval: str, gap: Dict):
        """Fill a single gap using API call."""
        try:
            start_time = gap['start_time']
            end_time = gap['end_time']
            
            if isinstance(start_time, str):
                start_time = pd.to_datetime(start_time, utc=True)
            if isinstance(end_time, str):
                end_time = pd.to_datetime(end_time, utc=True)
            
            # Convert to milliseconds
            start_ms = int(start_time.timestamp() * 1000)
            end_ms = int(end_time.timestamp() * 1000)
            
            # Make API call
            url = f"https://api.binance.com/api/v3/klines"
            params = {
                'symbol': symbol,
                'interval': interval,
                'startTime': start_ms,
                'endTime': end_ms,
                'limit': 1000
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data:
                        # Convert to DataFrame
                        df = pd.DataFrame(data, columns=[
                            'timestamp', 'open', 'high', 'low', 'close', 'volume',
                            'close_time', 'quote_asset_volume', 'number_of_trades',
                            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
                        ])
                        
                        # Clean and process data
                        df = clean_kline_data(df)
                        
                        if not df.empty:
                            # Merge with existing data
                            await self.merge_and_write_df(df, symbol, interval)
                            
        except Exception as e:
            self.logger.error(f"Error filling single gap for {symbol}_{interval}: {e}")

    def _analyze_kline_counts(
        self,
        symbol: str,
        interval: str,
        df: pd.DataFrame,
        stage: str,
        gaps: Optional[List[Tuple[pd.Timestamp, pd.Timestamp, int]]] = None,
    ):
        """Record the bar count and gap summary for later comparison."""
        if not hasattr(self, "_analysis_log"):
            self._analysis_log: Dict[str, List[Dict]] = {}

        key = f"{symbol}_{interval}"
        entry = {
            "stage": stage,
            "bars": int(len(df) if df is not None else 0),
            "first": pd.to_datetime(df["timestamp"].min()).strftime('%Y-%m-%dT%H:%M:%S.%fZ') if df is not None and not df.empty else None,
            "last": pd.to_datetime(df["timestamp"].max()).strftime('%Y-%m-%dT%H:%M:%S.%fZ') if df is not None and not df.empty else None,
        }

        if gaps:
            entry["gap_count"] = len(gaps)
            entry["gaps"] = [
                {
                    "start": start.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "end": end.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                    "missing": int(missing),
                }
                for start, end, missing in gaps[:10]
            ]

        self._analysis_log.setdefault(key, []).append(entry)

    def _detect_gaps_simple(self, df: pd.DataFrame, interval: str) -> List[Tuple[pd.Timestamp, pd.Timestamp, int]]:
        """Lightweight gap detector for test logging."""
        if df is None or df.empty or "timestamp" not in df.columns:
            return []

        df = df.sort_values("timestamp")
        interval_map = {
            "3m": pd.Timedelta(minutes=3),
            "15m": pd.Timedelta(minutes=15),
            "1h": pd.Timedelta(hours=1),
            "4h": pd.Timedelta(hours=4),
            "D": pd.Timedelta(days=1),
        }
        expected = interval_map.get(interval)
        if expected is None and self.validator:
            expected = self.validator._get_expected_interval(interval)
        if expected is None:
            expected = pd.Timedelta(minutes=1)
        threshold = expected * 1.5

        gaps = []
        ts = df["timestamp"].to_list()
        for i in range(1, len(ts)):
            delta = ts[i] - ts[i - 1]
            if delta > threshold:
                missing = max(1, int(delta / expected) - 1)
                gaps.append((ts[i - 1], ts[i], missing))
        return gaps

    async def test_symbol_restore(
        self,
        symbol: str,
        interval: str,
        use_gateway: bool = True,
        use_local_redis: bool = True,
        use_api: bool = False,
    ):
        """End-to-end restore test for a single symbol/timeframe."""

        self._analysis_log = {}

        # Load existing cache
        cache_fp = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
        existing_df = await self._read_file_unlocked(cache_fp)
        gaps_before = self._detect_gaps_simple(existing_df, interval)
        self._analyze_kline_counts(symbol, interval, existing_df, "initial_cache", gaps_before)

        consolidated_df = await self._read_consolidated_backup(symbol, interval)
        gaps_consolidated = self._detect_gaps_simple(consolidated_df, interval)
        self._analyze_kline_counts(symbol, interval, consolidated_df, "consolidated_backup", gaps_consolidated)

        merged_df = existing_df
        if consolidated_df is not None and not consolidated_df.empty:
            merged_df = pd.concat([existing_df, consolidated_df], ignore_index=True)
            merged_df = merged_df.drop_duplicates(subset=["timestamp"], keep="last")
            merged_df = merged_df.sort_values("timestamp").reset_index(drop=True)

        if merged_df is not None and not merged_df.empty:
            await self.merge_and_write_df(merged_df, symbol, interval)
            merged_df = await self._read_file_unlocked(cache_fp)

        gaps_after_merge = self._detect_gaps_simple(merged_df, interval)
        self._analyze_kline_counts(symbol, interval, merged_df, "after_backup_merge", gaps_after_merge)

        redis_frames = []
        if use_gateway and self.redis_client_read:
            try:
                payload = await self.redis_client_read.get(f"klines:{symbol}:{interval}")
                if payload:
                    data = json.loads(payload)
                    klines = data.get("klines", data if isinstance(data, list) else [])
                    if klines:
                        df_gateway = clean_kline_data(pd.DataFrame(klines), is_raw_api_data=False)
                        redis_frames.append(df_gateway)
                        gaps_gateway = self._detect_gaps_simple(df_gateway, interval)
                        self._analyze_kline_counts(symbol, interval, df_gateway, "gateway_redis", gaps_gateway)
            except Exception as e:
                self.logger.warning(f"Test gateway read failed: {e}")

        if use_local_redis and self.redis_client:
            try:
                payload = await self.redis_client.get(f"klines:{symbol}:{interval}")
                if payload:
                    data = json.loads(payload)
                    klines = data.get("klines", data if isinstance(data, list) else [])
                    if klines:
                        df_local = clean_kline_data(pd.DataFrame(klines), is_raw_api_data=False)
                        redis_frames.append(df_local)
                        gaps_local = self._detect_gaps_simple(df_local, interval)
                        self._analyze_kline_counts(symbol, interval, df_local, "local_redis", gaps_local)
            except Exception as e:
                self.logger.warning(f"Test local redis read failed: {e}")

        if redis_frames:
            redis_df = pd.concat(redis_frames, ignore_index=True)
            redis_df = redis_df.drop_duplicates(subset=["timestamp"], keep="last")
            redis_df = redis_df.sort_values("timestamp").reset_index(drop=True)
            await self.merge_and_write_df(redis_df, symbol, interval)

        final_df = await self._read_file_unlocked(cache_fp)
        gaps_final = self._detect_gaps_simple(final_df, interval)
        self._analyze_kline_counts(symbol, interval, final_df, "final_cache", gaps_final)

        if use_api and gaps_final:
            smallest_gap = sorted(gaps_final, key=lambda g: g[0])[0]
            end_time_ms = int(smallest_gap[0].timestamp() * 1000)
            needed = max(0, self.ideal_bars_targets.get(interval, 100) - len(final_df))
            if needed > 0:
                api_df = await self.fetch_klines_in_chunks(symbol, interval, needed, end_time_ms)
                if api_df is not None and not api_df.empty:
                    await self.merge_and_write_df(api_df, symbol, interval)
                    final_df = await self._read_file_unlocked(cache_fp)
                    gaps_final = self._detect_gaps_simple(final_df, interval)
                    self._analyze_kline_counts(symbol, interval, final_df, "after_api", gaps_final)

        report = self._analysis_log.get(f"{symbol}_{interval}")
        if report:
            summary = {
                "symbol": symbol,
                "interval": interval,
                "stages": report,
            }
            self.logger.info(json.dumps(summary, indent=2, default=str))

    # async def _run_consolidation_later(self, delay_seconds: int = 60):
    #     try:
    #         await asyncio.sleep(delay_seconds)
    #         await self._consolidate_existing_backups()
    #     except Exception as exc:
    #         self.logger.error(f"Deferred consolidation failed: {exc}")

    async def _ensure_primary_topup(self):
        """Ensure core data is fresh and complete before flag release."""
        try:
            await self._hydrate_primary_from_cache()
        except Exception as exc:
            self.logger.warning(f"Primary hydration encountered an issue: {exc}")

    async def _hydrate_primary_from_cache(self):
        """Guarantee local cache reflects consolidated history before any API fetch with MASSIVE parallelism."""
        self.logger.info("🚀 Starting MASSIVE parallel primary cache hydration...")

        # Create ALL hydration tasks upfront
        hydrate_tasks = []
        for symbol in self.symbols_to_run:
            hydrate_tasks.append(self._hydrate_symbol_from_consolidated(symbol))

        if hydrate_tasks:
            # Use semaphore to control concurrency - optimized for speed
            env = get_current_environment()['env']
            semaphore = asyncio.Semaphore(config.EZ_PRICES_API_SEMAPHORE.get(env, 10))  # Maximum concurrency for hydration

            async def run_hydrate_with_semaphore(task):
                async with semaphore:
                    return await task

            # Execute all hydration tasks in parallel
            results = await asyncio.gather(
                *[run_hydrate_with_semaphore(task) for task in hydrate_tasks],
                return_exceptions=True
            )

            success_count = sum(1 for r in results if not isinstance(r, Exception))
            error_count = sum(1 for r in results if isinstance(r, Exception))

            self.logger.info(f"✅ Primary hydration complete: {success_count} successful, {error_count} errors")

    async def _hydrate_symbol_from_consolidated(self, symbol: str):
        """Hydrate a single symbol across all timeframes in parallel."""
        # Create tasks for all timeframes
        hydration_tasks = []
        for interval in ("3m", "15m", "1h", "4h", "D"):
            hydration_tasks.append(self._hydrate_single_timeframe(symbol, interval))

        # Execute all timeframes in parallel
        if hydration_tasks:
            await asyncio.gather(*hydration_tasks, return_exceptions=True)

    async def _hydrate_single_timeframe(self, symbol: str, interval: str):
        """Hydrate a single symbol/timeframe combination."""
        try:
            file_path = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
            df_local = await self._read_file_unlocked(file_path) if file_path.exists() else pd.DataFrame()
            if df_local.empty:
                df_backup = await self._read_consolidated_backup(symbol, interval)
                if not df_backup.empty:
                    await self._write_file_unlocked(df_backup, file_path, existing_bar_count=0)
                    self.logger.debug(f"Hydrated {symbol}_{interval} from consolidated backup ({len(df_backup)} bars)")
        except Exception as e:
            self.logger.debug(f"Failed to hydrate {symbol}_{interval}: {e}")

    async def _topup_symbol_interval_quick(self, symbol: str, interval: str, limit: int):
        try:
            fetched = await self.fetch_klines_in_chunks(symbol, interval, limit, end_time_ms=None)
            if not fetched.empty:
                cleaned = self._sanitize_klines(fetched, interval)
                if not cleaned.empty:
                    await self.merge_and_write_df(cleaned, symbol, interval)
        except Exception as e:
            self.logger.warning(f"Quick top-up failed for {symbol}_{interval}: {e}")

    async def _topup_symbol_interval_small(self, symbol: str, interval: str, limit: int):
        try:
            fetched = await self.fetch_klines_in_chunks(symbol, interval, limit, end_time_ms=None)
            if not fetched.empty:
                cleaned = self._sanitize_klines(fetched, interval)
                if not cleaned.empty:
                    await self.merge_and_write_df(cleaned, symbol, interval)
        except Exception as e:
            self.logger.warning(f"Small top-up failed for {symbol}_{interval}: {e}")

    async def _wait_for_primary_readiness(self, timeout_seconds: int = 90) -> bool:
        """Block briefly until 3m Redis payloads are fresh and available."""
        if not self.redis_client:
            self.logger.warning("Redis client unavailable during readiness check.")
            return False
        deadline = time.time() + timeout_seconds
        symbols_needed = list(self.symbols_to_run[:150])  # cap for speed
        while time.time() < deadline:
            now_utc = datetime.now(timezone.utc)
            stale = []
            for symbol in symbols_needed:
                ok, reason = await self._is_primary_symbol_ready(symbol, now_utc)
                if not ok:
                    stale.append((symbol, reason))
                    if len(stale) >= 5:
                        break
            if not stale:
                return True
            await asyncio.sleep(2)
        for symbol, reason in stale:
            self.logger.warning(f"Primary readiness timeout for {symbol}: {reason}")
        return False

    async def _is_primary_symbol_ready(self, symbol: str, now_utc: datetime) -> tuple[bool, str]:
        key = f"klines:{symbol}:3m"
        try:
            payload_raw = await self.redis_client.get(key)
            if not payload_raw:
                return False, "no redis payload"
            payload = json.loads(payload_raw)
            klines = payload.get("klines") if isinstance(payload, dict) else None
            if not klines:
                return False, "empty redis klines"
            latest = klines[-1]
            ts = latest.get("timestamp")
            if not ts:
                return False, "missing timestamp"
            ts_dt = pd.to_datetime(ts, utc=True, errors="coerce")
            if ts_dt is None or pd.isna(ts_dt):
                return False, "invalid timestamp"
            if now_utc - ts_dt > timedelta(minutes=3):
                age = (now_utc - ts_dt).total_seconds()
                return False, f"stale {age:.1f}s"
            return True, "ok"
        except Exception as exc:
            return False, f"error {exc}"

    async def _set_data_ready_flags(self, primary_ready: bool):
        if primary_ready:
            try:
                config.DATA_READY_FLAG_FILE.touch(exist_ok=True)
                self.logger.info(" DATA_READY flag set after verifying 3m freshness. Other services can start.")
            except Exception as flag_exc:
                self.logger.warning(f"Failed to touch data_ready.flag: {flag_exc}")
        else:
            self.logger.warning("Primary readiness check failed; skipping data_ready.flag touch.")
        try:
            M_READY_FLAG_FILE.touch(exist_ok=True)
        except Exception:
            pass


    # async def periodic_backup_resampling_task(self, interval_seconds: int = 3600):
    #     """Periodically resamples all higher timeframes from the disk cache as a fallback mechanism."""
    #     await asyncio.sleep(600)  # Initial 10-minute delay to allow real-time systems to stabilize
    #     while True:
    #         self.logger.info(" Kicking off periodic backup HTF resampling from disk cache...")
    #         start_time = time.time()
            
    #         tasks = [self._backup_resample_symbol_htf(s) for s in self.symbols_to_run]
    #         results = await asyncio.gather(*tasks, return_exceptions=True)
            
    #         success_count = sum(1 for r in results if not isinstance(r, Exception))
    #         error_count = len(results) - success_count
            
    #         elapsed = time.time() - start_time
    #         self.logger.info(
    #             f" Periodic backup HTF resampling complete in {elapsed:.2f}s. "
    #             f"Success: {success_count}, Failures: {error_count}. Next run in {interval_seconds // 60} minutes." )
    #         await asyncio.sleep(interval_seconds)
    async def _schedule_additional_tasks(self):
        """Schedule additional background tasks - FINAL VERSION"""
        self._bg_tasks.append(asyncio.create_task(self.periodic_gateway_redis_sync_task()))
       # self._bg_tasks.append(asyncio.create_task(self.periodic_backup_resampling_task()))
        # Removed: _run_consolidation_later (one-time task)

    async def _post_flag_bootstrap(self, total_start_time: float):
        """Complete startup sequence after data-ready flags are set - FINAL VERSION"""
        await self._populate_higher_timeframes_from_consolidated_backup()
        await self.ensure_latest_topup()
        
        # This intensive, one-time task can sometimes corrupt fresh data if run too early.
        # It's best used as a command-line utility for manual cleanup if needed.
        # await self.sanitize_existing_cache_files()
        
        await self.initial_api_backfill_all()
        await self._complete_startup(total_start_time)

    async def _complete_startup(self, total_start_time: float):
        startup_elapsed = time.time() - total_start_time
        self.logger.info(f" STARTUP COMPLETE in {startup_elapsed:.2f} seconds! Live operations running. ")

    async def _populate_cache_from_consolidated(self):
        """Populate cache from consolidated backups with MASSIVE parallel processing."""
        self.logger.info("🚀 Starting MASSIVE parallel cache population from consolidated backups...")

        # Create ALL tasks upfront for maximum parallelism
        all_tasks = []

        # 3m timeframe tasks
        for symbol in self.symbols_to_run:
            all_tasks.append(self._populate_single_timeframe_from_consolidated(symbol, '3m'))

        # Higher timeframe tasks
        for symbol in self.symbols_to_run:
            for tf in ("15m", "1h", "4h", "D"):
                all_tasks.append(self._populate_single_timeframe_from_consolidated(symbol, tf))

        # Execute ALL tasks in parallel with controlled concurrency
        total_tasks = len(all_tasks)
        self.logger.info(f"🔥 Executing {total_tasks} cache population tasks in parallel...")

        # Use semaphore to limit concurrent disk operations - optimized for speed
        env = get_current_environment()['env']
        semaphore = asyncio.Semaphore(config.EZ_PRICES_API_SEMAPHORE.get(env, 10))  # Allow limited concurrent operations

        async def run_with_semaphore(task):
            async with semaphore:
                return await task

        # Run all tasks with concurrency control
        results = await asyncio.gather(
            *[run_with_semaphore(task) for task in all_tasks],
            return_exceptions=True
        )

        # Count results
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        error_count = sum(1 for r in results if isinstance(r, Exception))

        self.logger.info(f"✅ Cache population complete: {success_count} successful, {error_count} errors")

    async def _populate_single_timeframe_from_consolidated(self, symbol: str, interval: str) -> bool:
        """Populate a single symbol/timeframe combination from consolidated backup."""
        try:
            file_path = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"

            # Quick check if file already exists and has data
            if file_path.exists():
                df_existing = await self.get_klines_df(symbol, interval)
                if not df_existing.empty:
                    return True  # Already populated

            # Read from consolidated backup
            consolidated_df = await self._read_consolidated_backup(symbol, interval)
            if not consolidated_df.empty:
                await self._write_file_unlocked(consolidated_df, file_path)
                self.logger.debug(f"📦 Populated {symbol}_{interval} from consolidated backup ({len(consolidated_df)} bars)")
                return True
            else:
                return False  # No data available

        except Exception as e:
            self.logger.debug(f"Failed to populate {symbol}_{interval} from consolidated: {e}")
            return False

    async def _repair_lower_timeframe(self, symbol: str, interval: str, log_prefix: str) -> None:
        df_backup = await self.get_klines_df(symbol, interval)
        if df_backup.empty:
            df_backup = await self._read_consolidated_backup(symbol, interval)
            if not df_backup.empty:
                await self._write_file_unlocked(df_backup, config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json")
        if not df_backup.empty:
            await self.publish_to_redis(symbol, interval, df_backup)
            return

    async def _repair_higher_timeframe(self, symbol: str, interval: str, log_prefix: str) -> None:
        df_3m = await self.get_klines_df(symbol, '3m')
        if df_3m.empty:
            df_3m = await self._read_consolidated_backup(symbol, '3m')
            if not df_3m.empty:
                await self._write_file_unlocked(df_3m, config.KLINES_CACHE_DIR / f"{symbol}_3m.json")
        if df_3m.empty:
            await self._topup_symbol_interval(symbol, '3m', limit_override=QUICK_TOPUP_LIMITS.get('3m', 1500))
            df_3m = await self.get_klines_df(symbol, '3m')
            if df_3m.empty:
                self.logger.warning(f"{log_prefix} Repair skipped: 3m cache still empty after consolidation attempt.")
                return


# ==============================================================================
# ENHANCED DATA COMPLETENESS VALIDATION AND GAP DETECTION
# ==============================================================================

class EnhancedDataValidator:
    """Enhanced data validation and gap detection system for stricter data completeness checks."""
    
    def __init__(self, klines_cache_dir: Path, backup_system: HigherTimeframeBackup, redis_client=None):
        self.klines_cache_dir = klines_cache_dir
        self.backup_system = backup_system
        self.redis_client = redis_client
        self.logger = logger
        self.gap_thresholds = {
            '3m': pd.Timedelta(minutes=6),    # Allow 1 missing 3m bar
            '15m': pd.Timedelta(minutes=30),  # Allow 1 missing 15m bar
            '1h': pd.Timedelta(hours=2),      # Allow 1 missing hour
            '4h': pd.Timedelta(hours=8),      # Allow 1 missing 4h bar
            'D': pd.Timedelta(days=2)         # Allow 1 missing day
        }
        
    async def validate_data_completeness(self, symbol: str, interval: str) -> Dict:
        """Comprehensive data completeness validation with detailed gap analysis."""
        try:
            # Load current data
            df = await self._load_klines_data(symbol, interval)
            if df.empty:
                return {
                    'status': 'empty',
                    'symbol': symbol,
                    'interval': interval,
                    'gaps': [],
                    'completeness_score': 0.0,
                    'recommendation': 'No data available - needs full restoration'
                }
            
            # Analyze gaps
            gap_analysis = self._analyze_gaps(df, interval)
            
            # Calculate completeness score
            completeness_score = self._calculate_completeness_score(df, interval, gap_analysis)
            
            # Determine recommendation
            recommendation = self._get_recommendation(completeness_score, gap_analysis)
            
            return {
                'status': 'analyzed',
                'symbol': symbol,
                'interval': interval,
                'total_bars': len(df),
                'gaps': gap_analysis['gaps'],
                'gap_count': len(gap_analysis['gaps']),
                'total_gap_duration': gap_analysis['total_gap_duration'],
                'completeness_score': completeness_score,
                'recommendation': recommendation,
                'needs_restoration': completeness_score < 0.8
            }
            
        except Exception as e:
            self.logger.error(f"Error validating {symbol}_{interval}: {e}")
            return {
                'status': 'error',
                'symbol': symbol,
                'interval': interval,
                'error': str(e),
                'completeness_score': 0.0,
                'recommendation': 'Error occurred - manual investigation needed'
            }
    
    def _analyze_gaps(self, df: pd.DataFrame, interval: str) -> Dict:
        """Analyze gaps in the data with strict thresholds."""
        gaps = []
        total_gap_duration = pd.Timedelta(0)
        
        if df.empty or len(df) < 2:
            return {'gaps': gaps, 'total_gap_duration': total_gap_duration}
        
        # Convert timestamps and sort
        timestamps = pd.to_datetime(df['timestamp'], utc=True).sort_values()
        
        # Get expected interval
        expected_interval = self._get_expected_interval(interval)
        threshold = self.gap_thresholds.get(interval, expected_interval * 2)
        
        # Check for gaps
        for i in range(1, len(timestamps)):
            time_diff = timestamps.iloc[i] - timestamps.iloc[i-1]
            
            if time_diff > threshold:
                # Calculate missing klines
                missing_klines = int(time_diff.total_seconds() / expected_interval.total_seconds())
                
                gap_info = {
                    'start_time': timestamps.iloc[i-1],
                    'end_time': timestamps.iloc[i],
                    'duration': time_diff,
                    'missing_klines': missing_klines,
                    'severity': self._calculate_gap_severity(time_diff, expected_interval)
                }
                gaps.append(gap_info)
                total_gap_duration += time_diff
        
        return {
            'gaps': gaps,
            'total_gap_duration': total_gap_duration
        }
    
    def _get_expected_interval(self, interval: str) -> pd.Timedelta:
        """Get expected interval for the timeframe."""
        interval_map = {
            '3m': pd.Timedelta(minutes=3),
            '15m': pd.Timedelta(minutes=15),
            '1h': pd.Timedelta(hours=1),
            '4h': pd.Timedelta(hours=4),
            'D': pd.Timedelta(days=1)
        }
        return interval_map.get(interval, pd.Timedelta(minutes=1))
    
    def _calculate_gap_severity(self, gap_duration: pd.Timedelta, expected_interval: pd.Timedelta) -> str:
        """Calculate gap severity based on duration relative to expected interval."""
        ratio = gap_duration / expected_interval
        
        if ratio <= 2:
            return 'minor'
        elif ratio <= 10:
            return 'moderate'
        elif ratio <= 50:
            return 'major'
        else:
            return 'critical'
    
    def _calculate_completeness_score(self, df: pd.DataFrame, interval: str, gap_analysis: Dict) -> float:
        """Calculate a completeness score from 0.0 to 1.0."""
        if df.empty:
            return 0.0
        
        # Base score from data availability
        base_score = min(1.0, len(df) / 1000)  # Normalize to 1000 bars
        
        # Penalty for gaps
        gap_penalty = 0.0
        for gap in gap_analysis['gaps']:
            if gap['severity'] == 'critical':
                gap_penalty += 0.3
            elif gap['severity'] == 'major':
                gap_penalty += 0.2
            elif gap['severity'] == 'moderate':
                gap_penalty += 0.1
            else:  # minor
                gap_penalty += 0.05
        
        # Apply penalty (capped at 0.8 to avoid complete failure)
        final_score = max(0.0, base_score - gap_penalty)
        
        return round(final_score, 3)
    
    def _get_recommendation(self, completeness_score: float, gap_analysis: Dict) -> str:
        """Get recommendation based on completeness score and gaps."""
        if completeness_score >= 0.95:
            return 'Data is complete and healthy'
        elif completeness_score >= 0.8:
            return 'Data is mostly complete with minor gaps'
        elif completeness_score >= 0.5:
            return 'Data has significant gaps - restoration recommended'
        else:
            return 'Data is severely incomplete - full restoration required'
    
    async def _load_klines_data(self, symbol: str, interval: str) -> pd.DataFrame:
        """Load klines data from ALL available sources (Redis + all directories)."""
        try:
            # Try Redis first if available
            if self.redis_client:
                redis_key = f"klines:{symbol}:{interval}"
                redis_data = await self.redis_client.get(redis_key)
            else:
                redis_data = None
            
            if redis_data:
                data = json.loads(redis_data)
                if isinstance(data, dict) and 'klines' in data:
                    klines = data['klines']
                elif isinstance(data, list):
                    klines = data
                else:
                    klines = []
                
                if klines:
                    df = pd.DataFrame(klines)
                    if not df.empty and 'timestamp' in df.columns:
                        return df
            klines_dirs = _resolve_klines_directories()
            best_df = pd.DataFrame()
            best_timestamp = pd.Timestamp(0, tz='UTC')
            
            for klines_dir in klines_dirs:
                if not klines_dir or not klines_dir.exists():
                    continue
                    
                cache_file = klines_dir / f"{symbol}_{interval}.json"
                if not cache_file.exists():
                    continue
                    
                try:
                    async with self.file_io_semaphore:
                        content = await asyncio.to_thread(cache_file.read_text)
                    data = json.loads(content)
                    
                    if isinstance(data, dict) and 'data' in data:
                        df = pd.DataFrame(data['data'])
                    elif isinstance(data, list):
                        df = pd.DataFrame(data)
                    else:
                        df = pd.DataFrame()
                    
                    if not df.empty and 'timestamp' in df.columns:
                        # Find the latest timestamp in this data
                        try:
                            timestamps = pd.to_datetime(df['timestamp'], format='mixed', utc=True, errors='coerce')
                            latest_ts = timestamps.max()
                            if latest_ts > best_timestamp:
                                best_df = df.copy()
                                best_timestamp = latest_ts
                        except Exception:
                            # If timestamp parsing fails, still use this data if we don't have any
                            if best_df.empty:
                                best_df = df.copy()
                except Exception:
                    continue
            
            return best_df
            
        except Exception as e:
            self.logger.error(f"Error loading data for {symbol}_{interval}: {e}")
            return pd.DataFrame()
    
    async def validate_all_symbols(self, symbols: List[str], intervals: List[str]) -> Dict:
        """Validate data completeness for all symbols and intervals."""
        results = {
            'total_symbols': len(symbols),
            'total_intervals': len(intervals),
            'validation_results': {},
            'summary': {
                'complete': 0,
                'needs_restoration': 0,
                'critical_gaps': 0,
                'total_gaps': 0
            }
        }
        
        for symbol in symbols:
            results['validation_results'][symbol] = {}
            
            for interval in intervals:
                validation = await self.validate_data_completeness(symbol, interval)
                results['validation_results'][symbol][interval] = validation
                
                # Update summary
                if validation['completeness_score'] >= 0.8:
                    results['summary']['complete'] += 1
                else:
                    results['summary']['needs_restoration'] += 1
                
                if validation['gap_count'] > 0:
                    results['summary']['total_gaps'] += validation['gap_count']
                    
                    # Check for critical gaps
                    critical_gaps = [g for g in validation['gaps'] if g['severity'] == 'critical']
                    if critical_gaps:
                        results['summary']['critical_gaps'] += len(critical_gaps)
        
        return results

class EnhancedGapFiller:
    """Enhanced gap filling system that automatically restores missing data from backups."""
    
    def __init__(self, klines_cache_dir: Path, backup_system: HigherTimeframeBackup, redis_client=None):
        self.klines_cache_dir = klines_cache_dir
        self.backup_system = backup_system
        self.redis_client = redis_client
        self.logger = logger
        self.validator = EnhancedDataValidator(klines_cache_dir, backup_system, redis_client)
    
    async def fill_missing_data(self, symbol: str, interval: str) -> bool:
        """Fill missing data for a specific symbol and interval."""
        try:
            # First validate current data
            validation = await self.validator.validate_data_completeness(symbol, interval)
            
            if validation['completeness_score'] >= 0.95:
                self.logger.debug(f"{symbol}_{interval}: Data is already complete")
                return True
            
            # Try to restore from backup
            restored = await self._restore_from_best_backup(symbol, interval)
            
            if restored:
                # Re-validate after restoration
                new_validation = await self.validator.validate_data_completeness(symbol, interval)
                improvement = new_validation['completeness_score'] - validation['completeness_score']
                
                self.logger.info(f"{symbol}_{interval}: Restored from backup - completeness improved by {improvement:.3f}")
                return new_validation['completeness_score'] >= 0.8
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error filling missing data for {symbol}_{interval}: {e}")
            return False
    
    async def _restore_from_best_backup(self, symbol: str, interval: str) -> bool:
        """Restore data from the best available backup."""
        try:
            # Get the most complete backup
            best_backup = await self.backup_system.get_most_complete_backup(symbol)
            
            if not best_backup:
                self.logger.warning(f"No backups available for {symbol}")
                return False
            
            # Restore from backup
            backup_timestamp = best_backup['timestamp']
            restored = await self.backup_system.restore_from_backup(symbol, backup_timestamp)
            
            if restored:
                self.logger.info(f"Successfully restored {symbol} from backup {backup_timestamp}")
                return True
            else:
                self.logger.warning(f"Failed to restore {symbol} from backup {backup_timestamp}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error restoring {symbol} from backup: {e}")
            return False
    
    async def fill_all_missing_data(self, symbols: List[str], intervals: List[str]) -> Dict:
        """Fill missing data for all symbols and intervals."""
        results = {
            'total_attempts': len(symbols) * len(intervals),
            'successful_restorations': 0,
            'failed_restorations': 0,
            'details': {}
        }
        
        for symbol in symbols:
            results['details'][symbol] = {}
            
            for interval in intervals:
                success = await self.fill_missing_data(symbol, interval)
                results['details'][symbol][interval] = success
                
                if success:
                    results['successful_restorations'] += 1
                else:
                    results['failed_restorations'] += 1
        
        return results

# ==============================================================================
# COMMAND LINE INTERFACE FOR ENHANCED VALIDATION
# ==============================================================================

async def run_data_audit():
    """Run a comprehensive data audit and save results to file."""
    engine = ResamplingAndGapFillEngine()
    
    try:
        # Initialize Redis connection - NON-BLOCKING
        host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
        engine.redis_client = redis.Redis(
            host=host,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=30,
            socket_timeout=60,
            health_check_interval=30,
            retry_on_timeout=True,
            retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError]
        )
        # NON-BLOCKING: No ping wait
        logger.info(f"⏳ Redis client created ({host}:{config.REDIS_PORT}) - connecting in background")
        
        # Load symbols
        with open(config.SYMBOLS_FILE, 'r') as f:
            symbols_from_file = json.load(f)
        engine.symbols_to_run = symbols_from_file
        
        # Initialize validator and gap filler
        engine.validator = EnhancedDataValidator(config.KLINES_CACHE_DIR, engine.backup_system, engine.redis_client)
        engine.gap_filler = EnhancedGapFiller(config.KLINES_CACHE_DIR, engine.backup_system, engine.redis_client)
        
        # Run comprehensive audit
        audit_results = await engine.comprehensive_data_audit()
        
        # Save results to file
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        audit_file = f"data_audit_{timestamp}.json"
        
        with open(audit_file, 'w') as f:
            json.dump(audit_results, f, indent=2, default=str)
        
        logger.info(f"Data audit completed. Results saved to: {audit_file}")
        logger.info(f"Summary: {audit_results['validation_results']['summary']}")
        
        return audit_results
        
    except Exception as e:
        logger.error(f"Error running data audit: {e}")
        return None
    finally:
        if engine.redis_client:
            await engine.redis_client.aclose()

async def validate_specific_data(symbol: str, interval: str):
    """Validate specific symbol and interval data."""
    engine = ResamplingAndGapFillEngine()
    
    try:
        # Initialize Redis connection - NON-BLOCKING
        host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
        engine.redis_client = redis.Redis(
            host=host,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=30,
            socket_timeout=60,
            health_check_interval=30,
            retry_on_timeout=True,
            retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError]
        )
        # NON-BLOCKING: No ping wait
        logger.info(f"⏳ Redis client created ({host}:{config.REDIS_PORT}) - connecting in background")
        
        # Initialize validator and gap filler
        engine.validator = EnhancedDataValidator(config.KLINES_CACHE_DIR, engine.backup_system, engine.redis_client)
        engine.gap_filler = EnhancedGapFiller(config.KLINES_CACHE_DIR, engine.backup_system, engine.redis_client)
        
        # Validate specific symbol
        validation = await engine.validate_specific_symbol(symbol, interval)
        
        logger.info(f"Validation results for {symbol}_{interval}:")
        logger.info(f"   Status: {validation['status']}")
        logger.info(f"   Completeness Score: {validation['completeness_score']}")
        logger.info(f"   Total Bars: {validation.get('total_bars', 0)}")
        logger.info(f"   Gap Count: {validation.get('gap_count', 0)}")
        logger.info(f"   Recommendation: {validation['recommendation']}")
        
        if validation.get('gaps'):
            logger.info("   Gaps found:")
            for i, gap in enumerate(validation['gaps'][:5]):  # Show first 5 gaps
                logger.info(
                    "     %d. %s → %s (%s, %d missing, %s)",
                    i + 1,
                    gap['start_time'],
                    gap['end_time'],
                    gap['duration'],
                    gap['missing_klines'],
                    gap.get('severity', 'unknown') )
        return validation
        
    except Exception as e:
        logger.error(f"Error validating {symbol}_{interval}: {e}")
        return None
    finally:
        if engine.redis_client:
            await engine.redis_client.aclose()

async def fill_gaps_for_symbol(symbol: str, interval: str):
    """Fill gaps for a specific symbol and interval."""
    engine = ResamplingAndGapFillEngine()
    
    try:
        # Initialize Redis connection - NON-BLOCKING
        host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
        engine.redis_client = redis.Redis(
            host=host,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=30,
            socket_timeout=60,
            health_check_interval=30,
            retry_on_timeout=True,
            retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError]
        )
        # NON-BLOCKING: No ping wait
        logger.info(f"⏳ Redis client created ({host}:{config.REDIS_PORT}) - connecting in background")
        
        # Initialize validator and gap filler
        engine.validator = EnhancedDataValidator(config.KLINES_CACHE_DIR, engine.backup_system, engine.redis_client)
        engine.gap_filler = EnhancedGapFiller(config.KLINES_CACHE_DIR, engine.backup_system, engine.redis_client)
        
        # Fill gaps
        success = await engine.fill_gaps_for_symbol(symbol, interval)
        
        if success:
            logger.info(f"Successfully filled gaps for {symbol}_{interval}")
        else:
            logger.error(f"Failed to fill gaps for {symbol}_{interval}")
        
        return success
        
    except Exception as e:
        logger.error(f"Error filling gaps for {symbol}_{interval}: {e}")
        return False
    finally:
        if engine.redis_client:
            await engine.redis_client.aclose()

async def _run_single_symbol_restore(symbol: str, interval: str):
    engine = ResamplingAndGapFillEngine()
    try:
        engine.session = await engine._init_http_session()
        try:
            # NON-BLOCKING: No ping wait
            host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
            engine.redis_client = redis.Redis(
                host=host,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
                decode_responses=True,
                socket_connect_timeout=30,
                socket_timeout=60,
                health_check_interval=30,
                retry_on_timeout=True,
                retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError]
            )
            logger.info(f"⏳ Redis client created ({host}:{config.REDIS_PORT}) - connecting in background")
        except Exception:
            engine.redis_client = None

        try:
            env_info = get_current_environment()
            gw_host, gw_port = env_info['redis_connections'].get('gateway', ('localhost', 6379))
            # Fix IPv6 issue
            if gw_host in ['localhost', '::1']:
                gw_host = '127.0.0.1'
            # NON-BLOCKING: No ping wait
            engine.redis_client_read = redis.Redis(
                host=gw_host,
                port=gw_port,
                db=config.REDIS_DB,
                decode_responses=True,
                socket_connect_timeout=60,
                socket_timeout=300,
                health_check_interval=30,
                retry_on_timeout=True,
                retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError]
            )
            logger.info(f"⏳ Gateway Redis client created ({gw_host}:{gw_port}) - connecting in background")
        except Exception:
            engine.redis_client_read = None

        engine.validator = EnhancedDataValidator(config.KLINES_CACHE_DIR, engine.backup_system, engine.redis_client)
        await engine.test_symbol_restore(symbol, interval, use_api=False)

    finally:
        if engine.redis_client:
            await engine.redis_client.aclose()
        if engine.redis_client_read:
            await engine.redis_client_read.aclose()
        if engine.session and not engine.session.closed:
            await engine.session.close()


def run_single_symbol_restore(symbol: str, interval: str):
    asyncio.run(_run_single_symbol_restore(symbol, interval))


# async def run_restore(symbol: str, timestamp: Optional[str]) -> None:
#     engine = ResamplingAndGapFillEngine()
#     try:
#         if timestamp:
#             ok = await engine.restore_from_backup(symbol, timestamp)
#             if not ok:
#                 print(f"Restore failed for {symbol} {timestamp}")
#         else:
#             backup = await engine.get_most_complete_backup(symbol)
#             if not backup:
#                 print(f"No backups available for {symbol}")
#                 return
#             ok = await engine.restore_from_backup(symbol, backup['timestamp'])
#             if not ok:
#                 logger.error(f"Restore failed for {symbol} {backup['timestamp']}")
#     finally:
#         await engine.cleanup()
async def emergency_update_all_htf():
    """EMERGENCY: Force update ALL HTF data for ALL symbols immediately"""
    logger.warning("🚨 EMERGENCY: Starting immediate HTF update for ALL symbols")

    # Create engine instance
    engine = ResamplingAndGapFillEngine()

    try:
        # Initialize Redis connection - NON-BLOCKING
        host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
        engine.redis_client = redis.Redis(
            host=host,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=30,
            socket_timeout=60,
            health_check_interval=30,
            retry_on_timeout=True,
            retry_on_error=[redis_exceptions.TimeoutError,redis_exceptions.ConnectionError]
        )
        # NON-BLOCKING: No ping wait
        logger.info(f"⏳ Redis client created ({host}:{config.REDIS_PORT}) - connecting in background")

        # Load symbols
        with open(config.SYMBOLS_FILE, 'r') as f:
            symbols_from_file = json.load(f)
        engine.symbols_to_run = symbols_from_file

        start_time = time.time()
        success_count = 0

        for symbol in engine.symbols_to_run:
            try:
                # Get current 3m data
                df_3m = await engine.get_klines_df_redis_first(symbol, '3m')
                if df_3m.empty or len(df_3m) < 5:
                    continue

                # Resample ALL timeframes
                df_3m_indexed = df_3m.set_index(pd.to_datetime(df_3m['timestamp'])).sort_index()

                for interval in ['15m', '1h', '4h', 'D']:
                    if interval == '15m':
                        df_resampled = df_3m_indexed.resample("15min", label='right', closed='right')
                    elif interval == '1h':
                        df_resampled = df_3m_indexed.resample("1h", label='right', closed='right')
                    elif interval == '4h':
                        df_resampled = df_3m_indexed.resample("4h", label='right', closed='right')
                    elif interval == 'D':
                        df_resampled = df_3m_indexed.resample("D", label='right', closed='right')

                    df_htf = df_resampled.agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                        'volume': 'sum'
                    }).dropna()

                    if not df_htf.empty:
                        await engine.merge_and_write_df(df_htf.reset_index(), symbol, interval)

                success_count += 1
                logger.info(f"✅ Emergency update: {symbol}")

            except Exception as e:
                logger.error(f"❌ Emergency update failed for {symbol}: {e}")

        elapsed = time.time() - start_time
        logger.warning(f"🚨 EMERGENCY COMPLETE: Updated {success_count}/{len(engine.symbols_to_run)} symbols in {elapsed:.2f}s")

    except Exception as e:
        logger.error(f"Emergency update failed: {e}")
    finally:
        await engine.cleanup()

async def backfill_higher_timeframes_for_symbol(symbol: str):
    """CLI function to run a historical backfill for a single symbol."""
    engine = ResamplingAndGapFillEngine()
    try:
        engine.session = await engine._init_http_session()
        engine.symbols_to_run = [symbol]
        logger.info(f"Starting manual backfill for {symbol}...")
        await engine.initial_api_backfill_all()
        logger.info(f"Manual backfill for {symbol} complete.")
    finally:
        await engine.cleanup()

async def backfill_higher_timeframes_demo():
    """CLI function to run a historical backfill for a few demo symbols."""
    engine = ResamplingAndGapFillEngine()
    try:
        demo_symbols = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]
        engine.session = await engine._init_http_session()
        engine.symbols_to_run = demo_symbols
        logger.info(f"Starting demo backfill for {demo_symbols}...")
        await engine.initial_api_backfill_all()
        logger.info("Demo backfill complete.")
    finally:
        await engine.cleanup()

async def run_backfill(symbol: Optional[str]) -> None:
    if symbol:
        await backfill_higher_timeframes_for_symbol(symbol)
    else:
        await backfill_higher_timeframes_demo()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "audit":
            asyncio.run(run_data_audit())
        elif command == "force_15m_update":
            async def run_force_update():
                engine = ResamplingAndGapFillEngine()
                try:
                    # NON-BLOCKING: No ping wait
                    host = '127.0.0.1' if config.REDIS_HOST in ['localhost', '127.0.0.1'] else config.REDIS_HOST
                    engine.redis_client = redis.asyncio.Redis(
                        host=host,
                        port=config.REDIS_PORT, 
                        db=config.REDIS_DB,
                        decode_responses=True,
                        socket_connect_timeout=30,
                        socket_timeout=60,
                        health_check_interval=30,
                        retry_on_timeout=True,
                        retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError]
                    )
                    logger.info(f"⏳ Redis client created ({host}:{config.REDIS_PORT}) - connecting in background")
                    
                    with open(config.SYMBOLS_FILE, 'r') as f:
                        symbols_from_file = json.load(f)
                    engine.symbols_to_run = symbols_from_file
                    
                    await engine.force_update_all_15m()
                    
                except Exception as e:
                    logger.error(f"Force 15m update failed: {e}")
                finally:
                    if engine.redis_client:
                        await engine.redis_client.aclose()
                        
            asyncio.run(run_force_update())

        elif command == "emergency_update":
            async def run_emergency_update():
                with open(config.SYMBOLS_FILE, 'r') as f:
                    symbols_from_file = json.load(f)
                await emergency_update_all_htf()
            asyncio.run(run_emergency_update())

        elif command == "validate" and len(sys.argv) >= 4:
            symbol = sys.argv[2].upper()
            interval = sys.argv[3].lower()
            asyncio.run(validate_specific_data(symbol, interval))
        elif command == "fill" and len(sys.argv) >= 4:
            symbol = sys.argv[2].upper()
            interval = sys.argv[3].lower()
            asyncio.run(fill_gaps_for_symbol(symbol, interval))
        elif command == "gapfill":
            logger.info("Dedicated gap filling task is part of the main loop. Run without arguments.")
            pass
        # elif command == "restore" and len(sys.argv) >= 3:
        #     symbol = sys.argv[2].upper()
        #     timestamp = sys.argv[3] if len(sys.argv) >= 4 else None
        #     asyncio.run(run_restore(symbol, timestamp))
        # elif command == "test_restore" and len(sys.argv) >= 4:
        #     symbol = sys.argv[2].upper()
        #     interval = sys.argv[3].lower()
        #     run_single_symbol_restore(symbol, interval)
        elif command == "backfill":
            symbol_arg = sys.argv[2].upper() if len(sys.argv) >= 3 else None
            asyncio.run(run_backfill(symbol_arg))
        elif command == "diagnostic":
            symbol_arg = sys.argv[2].upper() if len(sys.argv) >= 3 else None
            async def run_diagnostic():
                engine = ResamplingAndGapFillEngine()
                try:
                    await engine.diagnostic_htf_update(symbol_arg)
                except Exception as e:
                    logger.error(f"Diagnostic failed: {e}")
            asyncio.run(run_diagnostic())
        else:
            logger.info("Usage:")
            logger.info("  python ez_prices.py                    # Run normal resampling engine")
            logger.info("  python ez_prices.py audit              # Run comprehensive data audit")
            logger.info("  python ez_prices.py validate SYMBOL INTERVAL    # Validate specific data completeness")
            logger.info("  python ez_prices.py fill SYMBOL INTERVAL        # Fill gaps for specific data")
            logger.info("  python ez_prices.py restore SYMBOL [TIMESTAMP]  # Restore symbol from backup")
            logger.info("  python ez_prices.py test_restore SYMBOL INTERVAL # Dry-run restore using backups + Redis")
            logger.info("  python ez_prices.py backfill [SYMBOL]         # Backfill higher timeframes for one or all symbols")
            logger.info("  python ez_prices.py diagnostic [SYMBOL]       # Run HTF diagnostic for symbol(s)")
    else:
        # Run the main engine
        asyncio.run(bootstrap_price_service())
        engine = ResamplingAndGapFillEngine()
        try:
            asyncio.run(engine.main())
        except KeyboardInterrupt:
            logger.info("Shutdown initiated by user.")
        finally:
            # Ensure cleanup is called on exit
            if not engine._shutting_down:
                engine._shutting_down = True
                asyncio.run(engine.cleanup())

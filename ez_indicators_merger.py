#!/usr/bin/env python3
"""Merges indicators_worker*.json files into latest_market_data.json"""
import asyncio
import json
import logging
import os
import orjson
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from collections import OrderedDict
import aiofiles
from config import Config
import redis.asyncio as redis
from utils import get_current_environment, orjson_default

config = Config()
logger = logging.getLogger("ez_indicators_merger")
redis_client = None
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(handler)
logs_dir = Path.home() / "logs"
try:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "ez_indicators_merger.log"
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(log_file, maxBytes=100*1024*1024, backupCount=5, encoding='utf-8', mode='a')
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
except Exception:
    pass

def _has_complete_3m_data(symbol_data: Dict[str, Any]) -> bool:
    """Check if symbol has complete 3m data. Returns True if all required 3m indicators exist."""
    required_3m_keys = ['timestamp_3m', 'dc_high_3m', 'dc_low_3m', 'dc_basis_3m', 'stoch_k_3m', 'stoch_d_3m', 'atr_3m']
    for key in required_3m_keys:
        if key not in symbol_data or symbol_data[key] is None:
            return False
    return True

def _check_all_symbols_have_3m(merged: Dict[str, Dict[str, Any]]) -> Tuple[bool, int, int]:
    """Check if all symbols have complete 3m data. Returns (all_complete, complete_count, total_count)."""
    if not merged:
        return (False, 0, 0)
    total = len(merged)
    complete = sum(1 for symbol_data in merged.values() if isinstance(symbol_data, dict) and _has_complete_3m_data(symbol_data))
    return (complete == total, complete, total)

async def _trigger_priority_calculation(missing_symbols: List[str]):
    """Publishes missing symbols to Redis to wake up workers immediately."""
    try:
        global redis_client
        if redis_client is None:
            env = get_current_environment()
            redis_local = env.get("redis_connections", {}).get("local", ())
            redis_host = redis_local[0] if isinstance(redis_local, (list, tuple)) and len(redis_local) >= 2 else config.REDIS_HOST
            redis_port_raw = redis_local[1] if isinstance(redis_local, (list, tuple)) and len(redis_local) >= 2 else config.REDIS_PORT
            try:
                redis_port = int(redis_port_raw)
            except (TypeError, ValueError):
                redis_port = config.REDIS_PORT
            if redis_host in ("localhost", "::1"):
                redis_host = "127.0.0.1"
            redis_kwargs = {"decode_responses": True, "socket_connect_timeout": 5, "socket_timeout": 5, "retry_on_timeout": True, "health_check_interval": 30}
            redis_password = getattr(config, "REDIS_PASSWORD", None)
            redis_client = redis.Redis(host=redis_host, port=redis_port, db=config.REDIS_DB, password=redis_password, **redis_kwargs)
        
        # 1. Store the specific list so workers know WHAT to calc
        await redis_client.set("missing_3m_symbols", json.dumps(missing_symbols), ex=60)
        # 2. Publish a high-priority interrupt signal
        await redis_client.publish("priority_3m_fix", "TRIGGER")
        logger.info(f"🚨 Broadcast PRIORITY FIX signal for {len(missing_symbols)} symbols")
    except Exception as e:
        logger.error(f"Failed to trigger priority calculation: {e}")

async def merge_worker_files(last_save_time: float = 0.0) -> Tuple[bool, Optional[str], int]:
    """Merge worker files. Returns (success, payload, symbol_count). Only saves if 40s have passed since last_save_time."""
    data_dir = Path(config.DATA_DIR)
    main_file = Path(config.LATEST_MARKET_DATA_FILE)
    worker_files = sorted(data_dir.glob("indicators_worker*.json"))
    if not worker_files:
        return (False, None, 0)
    merged: Dict[str, Dict[str, Any]] = {}
    total_symbols = 0
    worker_count = 0
    worker_symbol_counts = []
    for worker_file in worker_files:
        try:
            async with aiofiles.open(worker_file, "r") as f:
                content = await f.read()
                data = json.loads(content)
                if isinstance(data, dict):
                    symbols_in_file = len(data)
                    for symbol, symbol_data in data.items():
                        if isinstance(symbol_data, dict):
                            merged[symbol] = symbol_data
                            total_symbols += 1
                    worker_count += 1
                    worker_symbol_counts.append((worker_file.name, symbols_in_file))
                    logger.debug(f"Merging {symbols_in_file} symbols from {worker_file.name}")
        except Exception as e:
            logger.warning(f"Error reading {worker_file.name}: {e}")
            continue
    if not merged:
        logger.warning("No data to merge from worker files")
        return (False, None, 0)
    
    # Check completeness but DO NOT BLOCK save
    all_complete, complete_count, total_count = _check_all_symbols_have_3m(merged)
    
    if not all_complete:
        incomplete_symbols = [sym for sym, data in merged.items() if isinstance(data, dict) and not _has_complete_3m_data(data)]
        sample = incomplete_symbols[:5]
        
        # Trigger the fix only if not already triggered recently (60s cooldown per symbol set)
        try:
            cooldown_key = "priority_fix_cooldown"
            if not await redis_client.exists(cooldown_key):
                await _trigger_priority_calculation(incomplete_symbols)
                await redis_client.set(cooldown_key, "1", ex=60)
            else:
                logger.debug(f"⏳ Priority fix cooldown active — skipping re-trigger for {sample}")
        except Exception as _tpe:
            logger.error(f"Failed to trigger priority calculation: {_tpe}")
        # CRITICAL FIX: Log warning but PROCEED (do not return False)
        logger.warning(f"⚠️ Partial Data: {len(incomplete_symbols)} symbols missing 3m data. Priority Signal Sent. Proceeding to save available data. Sample: {sample}")
        
        # missing_3m_symbols already set by _trigger_priority_calculation above

    now = time.time()
    min_save_interval = 5.0
    time_since_last_save = now - last_save_time
    if time_since_last_save < min_save_interval:
        return (False, None, 0)
        
    logger.info(f"🔄 Starting merge and save - {total_count} symbols, {worker_count} workers")
    priority_symbols = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC", "XRPUSDC", "DOGEUSDC", "AAVEUSDC", "AVAXUSDC", "XLMUSDT", "LINKUSDC", "DOTUSDT", "ADAUSDC", "SKYUSDT"]
    ordered_symbols = OrderedDict()
    remaining_symbols = dict(merged)
    for sym in priority_symbols:
        values = remaining_symbols.pop(sym, None)
        if isinstance(values, dict):
            ordered_symbols[sym] = values
    logger.debug(f"📦 Ordered {len(ordered_symbols)} priority symbols, {len(remaining_symbols)} remaining")
    for sym in sorted(remaining_symbols.keys()):
        ordered_symbols[sym] = remaining_symbols[sym]
    
    # Merge real-time 1m/3m stochastic from ez_market_data (hot_metrics)
    # Only overlay stochastic VALUES — timestamps stay from kline candle close times
    try:
        if redis_client is not None:
            hot_keys = [f"hot_metrics:{sym}" for sym in ordered_symbols]
            raw_values = await redis_client.mget(hot_keys)
            hot_to_ind = {'k_1m': 'stoch_k_1m', 'd_1m': 'stoch_d_1m', 'k_3m': 'stoch_k_3m', 'd_3m': 'stoch_d_3m', 'k_1m_prev': 'k_1m_prev', 'd_1m_prev': 'd_1m_prev', 'k_3m_prev': 'k_3m_prev', 'd_3m_prev': 'd_3m_prev'}
            merge_count = 0
            now_ts = time.time()
            for sym, raw in zip(ordered_symbols, raw_values):
                if not raw:
                    continue
                try:
                    hot = json.loads(raw) if isinstance(raw, str) else orjson.loads(raw)
                except Exception:
                    continue
                sym_data = ordered_symbols.get(sym)
                if not isinstance(sym_data, dict):
                    continue
                hot_tick = float(hot.get('_tick_ts', 0) or 0)
                if not hot_tick or (now_ts - hot_tick) > 120:
                    continue
                merged_fld = False
                for hot_fld, ind_fld in hot_to_ind.items():
                    val = hot.get(hot_fld)
                    if val is not None:
                        sym_data[ind_fld] = float(val) if isinstance(val, (int, float)) else val
                        merged_fld = True
                if merged_fld:
                    merge_count += 1
            if merge_count:
                logger.info(f"[HOT_MERGE] Merged 1m/3m stoch from ez_market_data for {merge_count} symbols")
    except Exception as e:
        logger.debug(f"[HOT_MERGE] Error merging hot_metrics: {e}")

    logger.debug(f"📝 Serializing {len(ordered_symbols)} symbols to JSON...")
    payload_bytes = orjson.dumps(ordered_symbols, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS, default=orjson_default)
    payload = payload_bytes.decode('utf-8')
    logger.debug(f"✅ JSON serialized, size: {len(payload)} bytes")
    
    tmp_path = main_file.with_suffix(".tmp")
    timestamp_label = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = data_dir / f"market_data__{timestamp_label}.json"
    
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f"💾 Writing tmp file...")
        async with aiofiles.open(tmp_path, "wb") as f:
            await f.write(payload_bytes)
        logger.debug(f"💾 Replacing main file...")
        os.replace(str(tmp_path), str(main_file))
        logger.debug(f"✅ Files written successfully")
        logger.info(f"✅ Completed file update ({total_symbols} symbols merged from {worker_count} workers)")
        return (True, payload, total_symbols)
    except Exception as e:
        logger.error(f"Error writing merged file: {e}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return (False, None, 0)

async def broadcast_to_redis(payload: str, symbol_count: int, reason: str = "") -> bool:
    """Broadcast payload to Redis. Returns True if successful."""
    try:
        global redis_client
        if redis_client is None:
            env = get_current_environment()
            redis_local = env.get("redis_connections", {}).get("local", ())
            redis_host = redis_local[0] if isinstance(redis_local, (list, tuple)) and len(redis_local) >= 2 else config.REDIS_HOST
            redis_port_raw = redis_local[1] if isinstance(redis_local, (list, tuple)) and len(redis_local) >= 2 else config.REDIS_PORT
            try:
                redis_port = int(redis_port_raw)
            except (TypeError, ValueError):
                redis_port = config.REDIS_PORT
            if redis_host in ("localhost", "::1"):
                redis_host = "127.0.0.1"
            redis_kwargs = {"decode_responses": True, "socket_connect_timeout": 5, "socket_timeout": 5, "retry_on_timeout": True, "health_check_interval": 30}
            redis_password = getattr(config, "REDIS_PASSWORD", None)
            redis_client = redis.Redis(host=redis_host, port=redis_port, db=config.REDIS_DB, password=redis_password, **redis_kwargs)
        await redis_client.set(config.REDIS_KEY_MARKET_DATA, payload)
        await redis_client.publish("latest_market_data", payload)
        logger.info(f"📡 Broadcast {symbol_count} symbols to Redis{reason}")
        return True
    except Exception as e:
        logger.error(f"Redis broadcast failed: {e}")
        return False

async def merge_loop():
    check_interval = 2.0
    error_interval = 1.0 
    broadcast_interval = 15.0
    last_broadcast_time = 0.0
    last_save_time = 0.0
    last_payload = None
    last_symbol_count = 0
    while True:
        try:
            merged_result = await merge_worker_files(last_save_time)
            now = time.time()
            is_success = merged_result[0]
            if is_success:
                last_save_time = time.time()
                await asyncio.sleep(check_interval)
            else:
                # If we failed (likely due to missing data), check again sooner
                await asyncio.sleep(error_interval)
            merge_success, merge_payload, merge_symbol_count = merged_result if isinstance(merged_result, tuple) else (False, None, 0)
            if merge_success:
                last_save_time = now
                if merge_payload:
                    last_payload = merge_payload
                    last_symbol_count = merge_symbol_count
            time_since_broadcast = now - last_broadcast_time
            should_broadcast = time_since_broadcast >= broadcast_interval
            if merge_success or should_broadcast:
                # Load latest merged payload for broadcasting
                main_file = Path(config.LATEST_MARKET_DATA_FILE)
                if main_file.exists():
                    try:
                        async with aiofiles.open(main_file, "r") as f:
                            content = await f.read()
                            data = json.loads(content)
                            if isinstance(data, dict):
                                last_payload = json.dumps(data, indent=2)
                                last_symbol_count = len(data)
                    except Exception as e:
                        logger.debug(f"Error loading merged file for broadcast: {e}")
            
            # Force broadcast every 60s
            if should_broadcast and last_payload:
                try:
                    global redis_client
                    if redis_client is None:
                        redis_client = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB, password=getattr(config, "REDIS_PASSWORD", None), decode_responses=True)
                    await redis_client.set(config.REDIS_KEY_MARKET_DATA, last_payload)
                    await redis_client.publish("latest_market_data", last_payload)
                    logger.info(f"📡 Broadcast {last_symbol_count} symbols to Redis (60s interval)")
                    last_broadcast_time = now
                except Exception as e:
                    logger.error(f"Redis broadcast failed: {e}")
        except Exception as e:
            logger.error(f"Error in merge loop: {e}")
        await asyncio.sleep(check_interval)

async def main():
    global redis_client
    try:
        await merge_loop()
    except KeyboardInterrupt:
        logger.info("Merger stopped")
    finally:
        if redis_client:
            await redis_client.aclose()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Merger stopped")
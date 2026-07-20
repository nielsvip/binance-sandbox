import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import aiofiles
import shutil

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from utils import get_simple_redis_manager, load_environment_from_gpg, orjson_default

# Load Env
load_environment_from_gpg(None)
config = TradierConfig()

# Use fast JSON if available
try:
    import orjson
    def json_dumps(obj: Any) -> bytes:
        return orjson.dumps(obj, option=orjson.OPT_INDENT_2)  # pylint: disable=no-member
    def safe_json_loads(s: Any) -> Any:
        return orjson.loads(s)  # pylint: disable=no-member
except ImportError:
    import json
    def json_dumps(obj: Any) -> str:
        return json.dumps(obj, indent=2, default=str)
    def safe_json_loads(s: Any) -> Any:
        return json.loads(s)

# Logger Setup
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("tradier_prices")
os.makedirs(config.LOG_DIR, exist_ok=True)

from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(config.LOG_FILE_TRADIER_PRICES, maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT, encoding='utf-8', mode='a')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

stop_event = asyncio.Event()

class TradierPriceFetcher:
    def __init__(self):
        self.config = config
        self.symbols: List[str] = []
        self.price_cache: Dict[str, Dict] = {}
        self.running = False
        self.api_client: Optional[TradierAPIClient] = None
        self.redis_manager = None
        # --- FIX: Define fetcher_id ---
        self.fetcher_id = uuid.uuid4().hex[:6]
        logger.info(f"Initialized PriceFetcher [{self.fetcher_id}]")

    def load_symbols(self) -> List[str]:
        unique_symbols = set()
        try:
            if self.config.SYMBOLS_FILE.exists():
                with open(self.config.SYMBOLS_FILE, 'r') as f:
                    symbols = json.load(f)
                    if isinstance(symbols, list):
                        unique_symbols.update(symbols)
        except Exception as e:
            logger.error(f"Error loading main symbols file: {e}")
        return sorted(list(unique_symbols))

    def load_price_cache(self) -> Dict[str, Dict]:
        try:
            if self.config.PRICE_CACHE_FILE.exists():
                with open(self.config.PRICE_CACHE_FILE, 'rb') as f:
                    return safe_json_loads(f.read())
        except Exception: return {}
        return {}

    async def fetch_quotes(self) -> Dict[str, Dict]:
        if not self.api_client:
            self.api_client = TradierAPIClient(self.config, account_key='tra')
            await self.api_client.connect()
        
        if not self.symbols: 
            return {}
            
        all_quotes = {}
        batch_size = 50
        
        async def fetch_batch(batch):
            try:
                # Check for API ban
                if time.time() < getattr(self.api_client, '_global_ban_expires', 0):
                    return {}
                return await self.api_client.get_quotes(batch)
            except Exception as e:
                logger.error(f"Batch fetch error: {e}")
                return {}

        # Create tasks for all batches to run in parallel
        batches = [self.symbols[i:i + batch_size] for i in range(0, len(self.symbols), batch_size)]
        results = await asyncio.gather(*(fetch_batch(b) for b in batches))
        
        for quotes in results:
            for symbol, quote in quotes.items():
                try:
                    bid = float(quote.get('bid', 0.0))
                    ask = float(quote.get('ask', 0.0))
                    last = float(quote.get('last', 0.0))
                    price = (bid + ask) / 2.0 if bid > 0 and ask > 0 else last
                    if price > 0:
                        raw_date = quote.get('date', quote.get('timestamp'))
                        ts = datetime.now(timezone.utc)
                        if raw_date:
                            try:
                                val = raw_date / 1000.0 if raw_date > 1e11 else raw_date
                                ts = datetime.fromtimestamp(val, tz=timezone.utc)
                            except Exception: pass

                        all_quotes[symbol] = {
                            'symbol': symbol,
                            'price': price,
                            'bid': float(quote.get('bid', 0.0)),
                            'ask': float(quote.get('ask', 0.0)),
                            'last': price,
                            'volume': int(quote.get('volume', 0)),
                            'timestamp': ts.isoformat(),
                        }
                except Exception: continue

        # Ensure all self.symbols are present in all_quotes. Carry forward or alert.
        for symbol in self.symbols:
            if symbol not in all_quotes:
                cached = self.price_cache.get(symbol)
                if cached and isinstance(cached, dict) and cached.get('price', 0.0) > 0:
                    all_quotes[symbol] = cached
                else:
                    try:
                        import ez_alert
                        ez_alert.alert_missing_price(symbol)
                    except Exception as e:
                        logger.error(f"Error alerting missing price for {symbol}: {e}")
        return all_quotes

    async def broadcast_to_redis(self, prices: Dict[str, Dict]):
        now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        payload = {
            "_metadata": {
                "updated_at": now_iso,
                "count": len(prices)
            },
            "data": prices 
        }
        
        # 1. Update Redis via Manager
        if self.redis_manager:
            try:
                # SimpleRedisManager.set handles json conversion
                await self.redis_manager.set("tradier_prices_latest", payload)
                # Use PubSub for zero-latency notification
                await self.redis_manager.publish("tradier_prices_alert", payload)
            except Exception as e:
                logger.error(f"[{self.fetcher_id}] Redis Broadcast Failed: {e}")

        # 2. Update Disk Fallback
        try:
            latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
            async with aiofiles.open(latest_file, 'w') as f:
                await f.write(json.dumps(payload, default=str))
        except Exception as e:
            logger.error(f"[{self.fetcher_id}] Disk Save Failed: {e}")

    async def update_prices_loop(self):
        logger.info(f"🚀 Starting Price Loop [{self.fetcher_id}] - Interval: {self.config.PRICE_UPDATE_INTERVAL}s")
        while self.running:
            try:
                start_time = time.time()
                quotes = await self.fetch_quotes()
                
                if quotes:
                    self.price_cache.update(quotes)
                    await self.broadcast_to_redis(quotes)
                    
                    elapsed = time.time() - start_time
                    logger.info(f"[{self.fetcher_id}] Cycle: {len(quotes)} symbols in {elapsed:.2f}s")
                
                # Quota Protection
                exec_time = time.time() - start_time
                # Aim for 1 second latency, or the configured interval
                sleep_time = max(0.5, float(self.config.PRICE_UPDATE_INTERVAL) - exec_time)
                
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Loop error: {e}")
                await asyncio.sleep(10)

    async def start(self):
        """Start the price fetcher"""
        try:
            # Init Redis
            self.redis_manager = await get_simple_redis_manager()
            logger.info(f"[{self.fetcher_id}] Redis Manager linked.")
        except Exception as e:
            logger.error(f"[{self.fetcher_id}] Redis failure: {e}")

        # Config
        self.symbols = self.load_symbols()
        if not self.symbols:
            logger.error("No symbols found.")
            return
        
        self.price_cache = self.load_price_cache()
        self.config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        self.running = True
        logger.info(f"[{self.fetcher_id}] Main Loop Active.")
        await self.update_prices_loop()
        
    async def stop(self):
        logger.info(f"[{self.fetcher_id}] Stopping...")
        self.running = False
        if self.api_client:
            await self.api_client.close()

async def main():
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: stop_event.set())
    
    fetcher = TradierPriceFetcher()
    try:
        fetcher_task = asyncio.create_task(fetcher.start())
        await stop_event.wait()
    except Exception as e:
        logger.error(f"Main failure: {e}")
    finally:
        await fetcher.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

# import asyncio
# import json
# import logging
# import os
# import signal
# import sys
# import time
# import uuid
# from datetime import datetime, timezone, timedelta
# from pathlib import Path
# from typing import Dict, List, Optional, Any
# from collections import defaultdict
# import tempfile
# import shutil
# from config_tradier import TradierConfig
# from tradier_api import TradierAPIClient
# from utils import get_simple_redis_manager, load_environment_from_gpg, orjson_default
# load_environment_from_gpg(None)
# config = TradierConfig()
# client = TradierAPIClient()
# try:
#     from typing import Any, Dict, List, Optional, Union
#     import orjson
#     def default_json_serializer(obj):
#         if isinstance(obj, datetime):
#             return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
#         raise TypeError
#     def json_dumps(obj: Any, **kwargs) -> bytes:
#         option = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS  # pylint: disable=no-member
#         return orjson.dumps(obj, default=default_json_serializer, option=option)  # pylint: disable=no-member
#     def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
#         if isinstance(s, str):
#             s = s.encode('utf-8')
#         return orjson.loads(s)  # pylint: disable=no-member
#     JSONDecodeError = orjson.JSONDecodeError  # pylint: disable=no-member
# except ImportError:  
#     import json
#     from typing import Any, Dict, List, Optional, Union
#     def default_json_serializer(obj):
#         if isinstance(obj, datetime):
#             return obj.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
#         return str(obj)
#     def json_dumps(obj: Any, **kwargs) -> str:
#         if 'indent' not in kwargs:
#             kwargs['indent'] = 2
#         return json.dumps(obj, default=default_json_serializer, **kwargs)
#     def safe_json_loads(s: Union[bytes, bytearray, memoryview, str], **kwargs) -> Any:
#         if isinstance(s, (bytes, bytearray, memoryview)):
#             s = s.decode('utf-8')
#         return json.loads(s, **kwargs)
#     JSONDecodeError = json.JSONDecodeError
# logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
# logger = logging.getLogger("tradier_prices")
# os.makedirs(config.LOG_DIR, exist_ok=True)
# from logging.handlers import RotatingFileHandler
# file_handler = RotatingFileHandler(config.LOG_FILE_TRADIER_PRICES, maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT, encoding='utf-8', mode='a')
# file_handler.setLevel(logging.DEBUG)
# file_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
# file_handler.setFormatter(file_formatter)
# logger.addHandler(file_handler)
# logger.setLevel(logging.DEBUG)
# stop_event = asyncio.Event()

# class TradierPriceFetcher:
#     """Fetches and maintains constant price information from Tradier"""
#     def __init__(self):
#         self.config = config
#         self.symbols: List[str] = []
#         self.price_cache: Dict[str, Dict] = {}
#         self.running = False
#         self.api_client: Optional[TradierAPIClient] = None
#         self.redis_manager = None
        
#     def load_symbols(self) -> List[str]:
#         """Load symbols from symbols_tradier.json OR active account files"""
#         unique_symbols = set()
#         try:
#             if self.config.SYMBOLS_FILE.exists():
#                 with open(self.config.SYMBOLS_FILE, 'r') as f:
#                     symbols = json.load(f)
#                     if isinstance(symbols, list):
#                         unique_symbols.update(symbols)
#         except Exception as e:
#             logger.error(f"Error loading main symbols file: {e}")
#         # try:
#         #     # Assuming BASE_PATH or DATA_DIR holds these
#         #     search_dir = self.config.DATA_DIR 
#         #     for f in search_dir.glob("symbols_*_*.json"):
#         #         try:
#         #             with open(f, 'r') as fp:
#         #                 content = json.load(fp)
#         #                 if isinstance(content, list):
#         #                     unique_symbols.update(content)
#         #         except: pass
#         # except Exception: pass

#         final_list = list(unique_symbols)
#         logger.info(f"Loaded {len(final_list)} unique symbols to fetch.")
#         return final_list
            
#     def load_price_cache(self) -> Dict[str, Dict]:
#         """Load price cache from file with recovery"""
#         try:
#             if self.config.PRICE_CACHE_FILE.exists():
#                 with open(self.config.PRICE_CACHE_FILE, 'rb') as f:
#                     content = f.read()
                
#                 try:
#                     return safe_json_loads(content)
#                 except Exception:
#                     logger.warning(f"Corrupted price cache found. Attempting recovery.")
#                     # Attempt trim recovery
#                     try:
#                         txt = content.decode('utf-8', errors='ignore').strip()
#                         end_idx = txt.rfind('}')
#                         if end_idx != -1:
#                             return json.loads(txt[:end_idx+1])
#                     except: pass
                    
#                     return {}
#             return {}
#         except Exception as e:
#             logger.error(f"Error loading price cache: {e}")
#             return {}
            
#     def save_price_cache(self):
#         """Save price cache to file atomically with corruption protection"""
#         try:
#             # Use unique temp file to prevent collision
#             random_suffix = uuid.uuid4().hex
#             temp_file = self.config.PRICE_CACHE_FILE.with_name(f".{self.config.PRICE_CACHE_FILE.name}.{random_suffix}.tmp")
            
#             # Serialize
#             json_bytes = json_dumps(self.price_cache)
#             if isinstance(json_bytes, str): json_bytes = json_bytes.encode('utf-8')

#             # Write Binary + Fsync
#             with open(temp_file, 'wb') as f:
#                 f.write(json_bytes)
#                 f.flush()
#                 os.fsync(f.fileno())

#             # Atomic Rename
#             shutil.move(str(temp_file), str(self.config.PRICE_CACHE_FILE))
            
#             # Backup logic (optional but good)
#             if self.config.PRICE_CACHE_FILE.exists():
#                 backup_dir = self.config.PRICE_CACHE_FILE.parent / "backups"
#                 backup_dir.mkdir(parents=True, exist_ok=True)
#                 # Keep only recent backups
#                 timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
#                 backup_file = backup_dir / f"price_cache_tradier_backup_{timestamp}.json"
#                 # Copy instead of move to keep original safe during backup creation
#                 shutil.copy2(self.config.PRICE_CACHE_FILE, backup_file)
                
#             logger.debug(f"Price cache saved: {len(self.price_cache)} symbols")
            
#         except Exception as e:
#             logger.error(f"Error saving price cache: {e}")
#             if 'temp_file' in locals() and temp_file.exists():
#                 try: os.remove(temp_file)
#                 except: pass

#     async def fetch_quotes(self) -> Dict[str, Dict]:
#         if not self.api_client:
#             self.api_client = TradierAPIClient(self.config, account_key='tra')
#             await self.api_client.connect()
#         if not self.symbols: 
#             logger.warning('NO FUCKING SYMBOLS!!!')
#             return {}
#         all_quotes = {}
#         batch_size = 50
#         for i in range(0, len(self.symbols), batch_size):
#             batch = self.symbols[i:i + batch_size]
#             try:
#                 if time.time() < self.api_client._global_ban_expires:
#                     logger.warning("Skipping price fetch due to API Ban")
#                     await asyncio.sleep(1)
#                     continue

#                 quotes = await self.api_client.get_quotes(batch)
                
#                 for symbol, quote in quotes.items():
#                     try:
#                         price = float(quote.get('last', quote.get('bid', quote.get('ask', 0.0))))
#                         if price > 0:
#                             # TRADIER SPECIFIC: 'date' is the timestamp in milliseconds
#                             raw_date = quote.get('date', quote.get('timestamp'))
#                             if raw_date:
#                                 try:
#                                     ts = datetime.fromtimestamp(raw_date / 1000.0, tz=timezone.utc)
#                                 except:
#                                     # Fallback if already ISO format or seconds
#                                     try: ts = datetime.fromtimestamp(raw_date, tz=timezone.utc)
#                                     except: ts = datetime.now(timezone.utc) # Only if parsing fails completely
#                             else:
#                                 # If API returns NO timestamp, we record the read time, but strict preference is API time
#                                 ts = datetime.now(timezone.utc)

#                             all_quotes[symbol] = {
#                                 'symbol': symbol,
#                                 'price': price,
#                                 'bid': float(quote.get('bid', 0.0)),
#                                 'ask': float(quote.get('ask', 0.0)),
#                                 'last': float(quote.get('last', 0.0)),
#                                 'volume': int(quote.get('volume', 0)),
#                                 'timestamp': ts.isoformat(), # Save strictly as ISO
#                             }
#                     except: continue
#                 await asyncio.sleep(0.9) 
#             except Exception as e:
#                 logger.error(f"Error fetching batch {i}: {e}")
#         return all_quotes
        
#     def save_prices_to_json(self, prices: Dict[str, Dict]):
#         """Save prices to JSON file with timestamp - always succeeds as fallback"""
#         try:
#             timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
#             filename = f"tradier_prices_{timestamp}.json"
#             price_file = self.config.DATA_DIR / filename
            
#             # Ensure directory exists
#             self.config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            
#             # Unique temp file
#             random_suffix = uuid.uuid4().hex
#             temp_file = price_file.parent / f".{price_file.name}.{random_suffix}.tmp"
            
#             json_bytes = json_dumps(prices)
#             if isinstance(json_bytes, str): json_bytes = json_bytes.encode('utf-8')
            
#             with open(temp_file, 'wb') as f:
#                 f.write(json_bytes)
#                 f.flush()
#                 os.fsync(f.fileno())
#             shutil.move(str(temp_file), str(price_file))
            
#             # Also update latest file (copy instead of symlink for compatibility)
#             latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
            
#             # Atomic update for latest file too
#             temp_latest = latest_file.with_name(f".{latest_file.name}.{random_suffix}.tmp")
#             shutil.copy2(price_file, temp_latest)
#             shutil.move(str(temp_latest), str(latest_file))
            
#             logger.debug(f"Saved {len(prices)} prices to {filename}")
#         except Exception as e:
#             logger.error(f"Error saving prices to JSON: {e}")

#     async def broadcast_to_redis(self, prices: Dict[str, Dict]):
#         now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
#         payload = {
#             "_metadata": {
#                 "updated_at": now_iso,
#                 "count": len(prices)
#             },
#             "data": prices 
#         }
        
#         # 1. Update Redis
#         if self.redis_manager:
#             try:
#                 # Use your SimpleRedisManager's set method
#                 await self.redis_manager.set("tradier_prices_latest", payload)
#             except Exception as e:
#                 logger.error(f"Redis Broadcast Failed: {e}")

#         # 2. Update Disk
#         try:
#             latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
#             with open(latest_file, 'w') as f:
#                 json.dump(payload, f)
#         except Exception as e:
#             logger.error(f"Disk Save Failed: {e}")

#     # async def broadcast_to_redis(self, prices: Dict[str, Dict]):
#     #     # 1. Update JSON File on Disk (The Fallback)
#     #     try:
#     #         latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
#     #         # Atomic write to prevent reading a half-written file
#     #         temp = latest_file.with_suffix('.tmp')
#     #         with open(temp, 'w') as f:
#     #             json.dump(prices, f, indent=2, default=str)
#     #             f.flush()
#     #             os.fsync(f.fileno())
#     #         os.replace(temp, latest_file)
#     #     except Exception as e:
#     #         logger.error(f"Failed to update Disk JSON: {e}")

#     #     # 2. Update Redis (The Primary)
#     #     try:
#     #         if not self.redis_manager:
#     #             self.redis_manager = await get_simple_redis_manager()
            
#     #         # Store as string for easy 'get' or as hash for 'hget'
#     #         await self.redis_manager.set("tradier_prices_latest", json.dumps(prices, default=str))
            
#     #         # Publish so other scripts know new data is ready
#     #         await self.redis_manager.publish("tradier_prices_channel", "updated")
#     #     except Exception as e:
#     #         logger.error(f"Failed to update Redis: {e}")

#     # async def broadcast_to_redis(self, prices: Dict[str, Dict]):
#     #     # Save JSON Fallback
#     #     try:
#     #         latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
#     #         temp = latest_file.with_suffix('.tmp')
#     #         with open(temp, 'w') as f: json.dump(prices, f, indent=2, default=str)
#     #         shutil.move(str(temp), str(latest_file))
#     #     except: pass
#     #     try:
#     #         if not self.redis_manager: self.redis_manager = await get_simple_redis_manager()
#     #         # BROADCAST TO BOTH KEYS: tradier_indicators_latest (legacy/internal) and tradier_prices_latest (explicit)
#     #         await self.redis_manager.set("tradier_prices_latest", prices)
#     #         # Notify subscribers
#     #         await self.redis_manager.publish("tradier_prices_channel", {"type": "price_update"})
#     #     except: pass 

#     def cleanup_old_price_files(self):
#         """Clean up old price JSON files: keep 15min, then 1/hour, then 1/4hours, then 1/day"""
#         try:
#             now = datetime.now(timezone.utc)
#             cutoff_15min = now - timedelta(minutes=15)
#             cutoff_24hours = now - timedelta(hours=24)
            
#             # Get all price files sorted by timestamp
#             price_files = sorted(
#                 self.config.DATA_DIR.glob("tradier_prices_*.json"),
#                 key=lambda p: p.stat().st_mtime,
#                 reverse=True
#             )
            
#             # Don't touch latest file
#             latest_file = self.config.DATA_DIR / "tradier_prices_latest.json"
#             price_files = [f for f in price_files if f.name != "tradier_prices_latest.json"]
            
#             files_to_keep = set()
            
#             # Group files by time periods
#             per_hour: Dict[str, List[Path]] = {}      # For files < 24 hours old
#             per_4hour: Dict[str, List[Path]] = {}     # For files < 24 hours old (4-hour groups)
#             per_day: Dict[str, List[Path]] = {}       # For files >= 24 hours old
            
#             for file_path in price_files:
#                 try:
#                     file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                    
#                     # Keep all files from last 15 minutes
#                     if file_mtime >= cutoff_15min:
#                         files_to_keep.add(file_path)
#                     # Files older than 15min but less than 24 hours - group by hour and 4-hour periods
#                     elif file_mtime >= cutoff_24hours:
#                         hour_key = file_mtime.strftime("%Y%m%d_%H")
#                         per_hour.setdefault(hour_key, []).append(file_path)
                        
#                         # Also group by 4-hour periods (0-3, 4-7, 8-11, 12-15, 16-19, 20-23)
#                         four_hour_slot = file_mtime.hour // 4
#                         four_hour_key = f"{file_mtime.strftime('%Y%m%d')}_{four_hour_slot}"
#                         per_4hour.setdefault(four_hour_key, []).append(file_path)
#                     # Files older than 24 hours - group by day
#                     else:
#                         day_key = file_mtime.strftime("%Y%m%d")
#                         per_day.setdefault(day_key, []).append(file_path)
#                 except Exception as e:
#                     logger.debug(f"Error processing file {file_path.name}: {e}")
#                     continue
            
#             # Keep most recent file from each hour (for files < 24 hours old)
#             for hour_key, files in per_hour.items():
#                 if files:
#                     most_recent = max(files, key=lambda p: p.stat().st_mtime)
#                     files_to_keep.add(most_recent)
            
#             # Keep most recent file from each 4-hour period (for files < 24 hours old)
#             for four_hour_key, files in per_4hour.items():
#                 if files:
#                     most_recent = max(files, key=lambda p: p.stat().st_mtime)
#                     files_to_keep.add(most_recent)
            
#             # Keep most recent file from each day (for files >= 24 hours old)
#             for day_key, files in per_day.items():
#                 if files:
#                     most_recent = max(files, key=lambda p: p.stat().st_mtime)
#                     files_to_keep.add(most_recent)
            
#             # Delete files not in keep set
#             deleted_count = 0
#             deleted_size = 0
#             for file_path in price_files:
#                 if file_path not in files_to_keep and file_path != latest_file:
#                     try:
#                         size = file_path.stat().st_size
#                         file_path.unlink()
#                         deleted_count += 1
#                         deleted_size += size
#                     except Exception as e:
#                         logger.debug(f"Error deleting old file {file_path.name}: {e}")
            
#             if deleted_count > 0:
#                 logger.info(f"Cleaned up {deleted_count} old price files ({deleted_size / 1024:.1f} KB)")
#         except Exception as e:
#             logger.error(f"Error cleaning up old price files: {e}")

#     async def update_prices_loop(self):
#         logger.info(f"🚀 Starting Price Loop [{self.fetcher_id}] - Interval: {self.config.PRICE_UPDATE_INTERVAL}s")
        
#         while self.running:
#             try:
#                 start_time = time.time()
                
#                 # This function batches 114 symbols into groups of 50
#                 # MOVE THE LOGGING OUT OF fetch_quotes and put it here
#                 quotes = await self.fetch_quotes()
                
#                 if quotes:
#                     # Store locally
#                     self.price_cache.update(quotes)
                    
#                     # Broadcast the WHOLE bundle at once
#                     await self.broadcast_to_redis(quotes)
                    
#                     # SINGLE LOG LINE PER CYCLE
#                     elapsed = time.time() - start_time
#                     logger.info(f"[{self.fetcher_id}] Cycle Complete: {len(quotes)} symbols in {elapsed:.2f}s")
                
#                 # --- CRITICAL: PROTECT THE QUOTA ---
#                 # Calculate how much time is left to sleep to maintain your interval
#                 execution_time = time.time() - start_time
#                 sleep_time = max(1.0, self.config.PRICE_UPDATE_INTERVAL - execution_time)
                
#                 # If you are being banned, force a minimum of 10-15 seconds
#                 if sleep_time < 10:
#                     sleep_time = 15
                    
#                 await asyncio.sleep(sleep_time)

#             except Exception as e:
#                 logger.error(f"Error in update loop: {e}")
#                 await asyncio.sleep(15)

#     async def start(self):
#         try:
#             from utils import get_simple_redis_manager, orjson_default
#             self.redis_manager = await get_simple_redis_manager()
#             logger.info(f"[{self.fetcher_id}] Redis Manager ready.")
#         except Exception as e:
#             logger.error(f"[{self.fetcher_id}] Failed to init Redis: {e}")
#         self.symbols = self.load_symbols()
#         if not self.symbols:
#             logger.error("No symbols loaded!")
#             return
#         self.price_cache = self.load_price_cache()
#         self.config.DATA_DIR.mkdir(parents=True, exist_ok=True)
#         self.running = True
#         logger.info(f"[{self.fetcher_id}] Entering Main Loop...")
#         await self.update_prices_loop()
        
#     async def stop(self):
#         """Stop the price fetcher"""
#         logger.info("Stopping Tradier price fetcher...")
#         self.running = False
#         self.save_price_cache()
#         if self.api_client:
#             await self.api_client.close()
            
#         logger.info("Tradier price fetcher stopped")


# def signal_handler(signum, frame):
#     """Trigger graceful shutdown"""
#     logger.info(f"Received signal {signum}, stopping...")
#     stop_event.set()

# async def main():
#     """Main entry point"""
#     # Register signals to the stop_event
#     loop = asyncio.get_running_loop()
#     for sig in (signal.SIGINT, signal.SIGTERM):
#         loop.add_signal_handler(sig, lambda: stop_event.set())
    
#     fetcher = TradierPriceFetcher()
    
#     # Run the fetcher and wait for the stop event
#     try:
#         # We run start() as a background task so we can monitor the stop_event
#         fetcher_task = asyncio.create_task(fetcher.start())
        
#         # Wait until someone presses Ctrl+C or kills the process
#         await stop_event.wait()
        
#     except Exception as e:
#         logger.error(f"Fatal error in main: {e}", exc_info=True)
#     finally:
#         # Graceful cleanup
#         await fetcher.stop()
#         logger.info("Main process exited.")

# if __name__ == "__main__":
#     try:
#         asyncio.run(main())
#     except KeyboardInterrupt:
#         pass

# # def signal_handler(signum, frame):
# #     """Handle shutdown signals"""
# #     logger.info(f"Received signal {signum}, shutting down...")
# #     sys.exit(0)

# # async def main():
# #     """Main entry point"""
# #     signal.signal(signal.SIGINT, signal_handler)
# #     signal.signal(signal.SIGTERM, signal_handler)
    
# #     fetcher = TradierPriceFetcher()
    
# #     try:
# #         await fetcher.start()
# #     except KeyboardInterrupt:
# #         logger.info("Interrupted by user")
# #     except Exception as e:
# #         logger.error(f"Fatal error: {e}", exc_info=True)
# #     finally:
# #         await fetcher.stop()

# # if __name__ == "__main__":
# #     asyncio.run(main())


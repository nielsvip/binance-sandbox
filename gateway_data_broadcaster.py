import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set
import aiohttp
import pandas as pd
import redis.asyncio as redis
from binance import AsyncClient, BinanceSocketManager
from binance.exceptions import BinanceAPIException
import signal
import sys
import aiofiles
from contextlib import asynccontextmanager
import atexit
import shutil
import stat

def fix_file_permissions(file_path, retry_count=3):
    """Fix file permissions when writing fails - makes files writable and executable"""
    try:
        if os.path.exists(file_path):
            # Make file writable and executable
            os.chmod(file_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # 777
            logging.info(f"🔧 Fixed permissions for {file_path}")
        else:
            # Make parent directory writable if file doesn't exist
            parent_dir = os.path.dirname(file_path)
            if os.path.exists(parent_dir):
                os.chmod(parent_dir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # 777
                logging.info(f"🔧 Fixed permissions for parent directory {parent_dir}")
        return True
    except Exception as e:
        logging.error(f"❌ Failed to fix permissions for {file_path}: {e}")
        return False

def safe_file_write(file_path, content, mode='w'):
    """Safely write to file with atomic temp-file-then-rename pattern"""
    for attempt in range(3):
        try:
            parent_dir = os.path.dirname(file_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
                fix_file_permissions(parent_dir)
            temp_path = str(file_path) + ".tmp"
            with open(temp_path, mode) as f:
                if isinstance(content, str):
                    f.write(content)
                else:
                    json.dump(content, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, file_path)
            return True
        except PermissionError:
            logging.warning(f"⚠️ Permission denied writing to {file_path}, fixing permissions...")
            if fix_file_permissions(file_path):
                continue
            else:
                logging.error(f"❌ Failed to fix permissions for {file_path} after {attempt + 1} attempts")
        except Exception as e:
            logging.error(f"❌ Error writing to {file_path}: {e}")
            if os.path.exists(str(file_path) + ".tmp"):
                try:
                    os.remove(str(file_path) + ".tmp")
                except OSError:
                    pass
            return False
    return False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(Path.home() / 'logs' / 'gateway_broadcaster.log'))
    ]
)
logger = logging.getLogger(__name__)

# Process management
PID_FILE = str(Path.home() / "logs" / "gateway_broadcaster.pid")

def check_pid_file():
    """Check if another instance is already running."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            # Check if process is actually running
            os.kill(old_pid, 0)
            logger.critical(f"❌ Another gateway instance is already running (PID: {old_pid})")
            logger.critical("❌ Only one instance allowed. Exiting.")
            sys.exit(1)
        except (ValueError, OSError):
            # PID file exists but process is dead, remove it
            os.remove(PID_FILE)
    
    # Write our PID
    safe_file_write(PID_FILE, str(os.getpid()))
    
    # Register cleanup on exit
    atexit.register(cleanup_pid_file)

def cleanup_pid_file():
    """Remove PID file on exit."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass

# Configuration
class Config:
    # Binance API credentials (not needed for public data)
    BINANCE_API_KEY = None  # Public endpoints don't require authentication
    BINANCE_API_SECRET = None
    
    # Redis connection (using same as your existing setup)
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
    REDIS_DB = int(os.getenv('REDIS_DB', 0))
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD')
    
    # Data refresh intervals
    MARK_PRICE_UPDATE_INTERVAL = 1  # seconds
    HEALTH_CHECK_INTERVAL = 30      # seconds
    LOCAL_KLINES_CACHE_DIR = Path('/home/niels/binance/klines_cache')  # Shared cache directory for ez_indicators and ez_prices
    BASE_PATH = Path('/home/niels/binance')
    PROGRESS_CHECKPOINT_DIR = Path('/home/niels/binance/progress_checkpoints')  # Progress tracking directory

    # Kline timeframes to maintain (including 1m as per your setup)
    TIMEFRAMES = ['1m', '3m', '15m', '1h', '4h', 'D']
    
    # Kline limits matching ez_prices.py exactly
    IDEAL_BARS_TARGETS = {"1m": 4000, "3m": 1800, "15m": 1800, "1h": 1800, "4h": 1800, "D": 730}
    
    # Redis expiry matching ez_prices.py
    REDIS_EXPIRY_SECONDS = 300  # 5 minutes base, will be multiplied by 6 for higher timeframes
    
    # Symbols will be loaded from symbols.json file
    # Remote server configuration (gateway on .35, niels server on .52)
    REMOTE_BASE_PATH = os.getenv("REMOTE_BASE_PATH", "/home/niels/binance")
    REMOTE_SERVER_USER_HOST = os.getenv("REMOTE_SERVER_USER_HOST", "niels@10.0.0.3")
    SYMBOLS = []
    
    # Rate limiting
    MAX_REQUESTS_PER_MINUTE = 600  # More conservative rate limit
    REQUEST_DELAY = 0.2  # 200ms delay between requests (5 requests per second)

def get_fallback_usdc_pairs() -> set:
    """Get fallback USDC pairs if API call fails."""
    # (Paste the full fallback_pairs set here)
    fallback_pairs ={"1000BONKUSDC", "1000PEPEUSDC", "1000SHIBUSDC", "AAVEUSDC", "ADAUSDC", "ARBUSDC", "AVAXUSDC", "BCHUSDC", "BNBUSDC", "BOMEUSDC", "BTCUSDC", "CRVUSDC", "DOGEUSDC", "ENAUSDC", "ETHFIUSDC", "ETHUSDC", "FILUSDC", "HBARUSDC", "IPUSDT", "KAITOUSDT", "LINKUSDC", "LTCUSDC", "NEARUSDC", "NEOUSDC", "ORDIUSDC", "PENGUUSDC", "PNUTUSDT", "SOLUSDC", "SUIUSDC", "TIAUSDC", "TRUMPUSDC", "UNIUSDC", "WIFUSDC", "WLDUSDC", "XRPUSDC"}
    logger.warning(f"Using fallback list with {len(fallback_pairs)} USDC pairs.")
    return fallback_pairs

def force_usdc_if_needed(symbol: str, available_usdc_pairs: set) -> str:
    """Convert symbol to USDC if available, otherwise keep USDT."""
    if symbol.endswith("USDT"):
        usdc_equivalent = symbol.replace("USDT", "USDC")
        if usdc_equivalent in available_usdc_pairs:
            return usdc_equivalent
    return symbol

def force_usdc_in_list(symbols_list: list, available_usdc_pairs: set) -> list:
    """Convert a list of symbols to use USDC when available."""
    return [force_usdc_if_needed(s, available_usdc_pairs) for s in symbols_list if isinstance(s, str)]

async def get_live_usdc_pairs(session: aiohttp.ClientSession) -> set:
    """Fetches all actively trading perpetual USDC pairs from Binance."""
    usdc_pairs = set()
    exchange_info_url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    try:
        async with session.get(exchange_info_url) as resp:
            if resp.status == 200:
                data = await resp.json()
                for symbol_info in data.get('symbols', []):
                    symbol = symbol_info.get('symbol', '')
                    if symbol.endswith('USDC') and symbol_info.get('status') == 'TRADING':
                        usdc_pairs.add(symbol)
                logger.info(f"✅ Successfully fetched {len(usdc_pairs)} live USDC pairs from Binance API")
                return usdc_pairs
            else:
                logger.warning(f"Failed to fetch exchange info: HTTP {resp.status}. Using fallback.")
                return get_fallback_usdc_pairs()
    except Exception as e:
        logger.error(f"Error fetching live USDC pairs: {e}. Using fallback.")
        return get_fallback_usdc_pairs()

class GatewayDataBroadcaster:
    def _get_expiry_for_timeframe(self, interval: str) -> int:
        """Get appropriate Redis expiry for different timeframes."""
        now = datetime.now(timezone.utc)
        
        if interval == "1m":
            # Expire at next minute boundary
            next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            return int((next_minute - now).total_seconds()) + 60  # Add 1 minute buffer
        elif interval == "3m":
            # Expire at next 3-minute boundary (00, 03, 06, 09, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 57)
            current_minute = now.minute
            next_3m = ((current_minute // 3) + 1) * 3
            if next_3m >= 60:
                next_3m = 0
                next_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            else:
                next_time = now.replace(minute=next_3m, second=0, microsecond=0)
            return int((next_time - now).total_seconds()) + 180  # Add 3 minutes buffer
        elif interval == "15m":
            # Expire at next 15-minute boundary (00, 15, 30, 45)
            current_minute = now.minute
            next_15m = ((current_minute // 15) + 1) * 15
            if next_15m >= 60:
                next_15m = 0
                next_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            else:
                next_time = now.replace(minute=next_15m, second=0, microsecond=0)
            return int((next_time - now).total_seconds()) + 900  # Add 15 minutes buffer
        elif interval == "1h":
            # Expire at next hour
            next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            return int((next_hour - now).total_seconds()) + 3600  # Add 1 hour buffer
        elif interval == "4h":
            # Expire at next 4-hour boundary (00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
            current_hour = now.hour
            next_4h = ((current_hour // 4) + 1) * 4
            if next_4h >= 24:
                next_4h = 0
                next_time = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                next_time = now.replace(hour=next_4h, minute=0, second=0, microsecond=0)
            return int((next_time - now).total_seconds()) + 14400  # Add 4 hours buffer
        elif interval == "1d" or interval == "D":
            # Expire at next midnight UTC
            next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return int((next_midnight - now).total_seconds()) + 86400  # Add 1 day buffer
        else:
            return 1800  # Default to 30 minutes
            return base_expiry * 6  # Default to 30 minutes

    def __init__(self):
        """Initialize the gateway data broadcaster."""
        self.config = Config()
        self.symbols = []
        self.usdc_symbols = set()
        self.usdt_symbols = set()
        self.live_usdc_pairs = set()
        self.mark_prices = {}
        self.redis_client = None
        self.http_session = None
        self.running = False
        self.start_time = time.time()
        self.last_activity_time = time.time()
        self.last_request_time = time.time(); self.request_count = 0; self.request_reset_time = time.time() + 60
        
        # Health status tracking
        self.health_status = {
            'last_mark_price_update': None,
            'last_kline_update': {},
            'redis_connected': False,
            'binance_connected': False,
            'websocket_connected': False,
            'last_heartbeat': None,
            'uptime_seconds': 0
        }
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
    
    async def _rate_limit_check(self):
        """Ensure we don't exceed Binance rate limits."""
        current_time = time.time()
        
        # Reset counter if minute has passed
        if current_time >= self.request_reset_time:
            self.request_count = 0
            self.request_reset_time = current_time + 60
        
        # Check if we need to wait
        if self.request_count >= self.config.MAX_REQUESTS_PER_MINUTE:
            wait_time = self.request_reset_time - current_time
            if wait_time > 0:
                logger.debug(f"Rate limit reached, waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                self.request_count = 0
                self.request_reset_time = time.time() + 60
        
        # Ensure minimum delay between requests
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.config.REQUEST_DELAY:
            await asyncio.sleep(self.config.REQUEST_DELAY - time_since_last)
        
        self.last_request_time = time.time()
        self.request_count += 1

    async def load_symbols_from_remote(self):
        """
        Loads the base symbol list from the niels server (.52) via SSH and then
        intelligently converts USDT pairs to live USDC pairs.
        """
        remote_file_path = f"{self.config.REMOTE_BASE_PATH}/symbols.json"
        remote_target = self.config.REMOTE_SERVER_USER_HOST
        logger.info(f"Attempting to load base symbols from {remote_target}...")

        try:
            # 1. Fetch the raw symbol list from the niels server via SSH
            logger.info(f"Attempting SSH connection to {remote_target}...")
            
            # Try with SSH key authentication (no password prompt)
            process = await asyncio.create_subprocess_exec(
                'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', 
                remote_target, f'cat {remote_file_path}',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"SSH command failed while fetching symbols.json: {stderr.decode().strip()}")
                logger.warning("SSH failed - probably no key-based auth set up on gateway server")
                logger.warning("Using fallback symbol list instead...")
                return self._use_fallback_symbols()
            
            raw_symbols = json.loads(stdout.decode('utf-8'))

            # 2. Use the live USDC pair list to create the final workload
            logger.info("Converting symbol list using live USDC pair data...")
            final_workload = set()
            for symbol in raw_symbols:
                # Use force_usdc_if_needed to convert symbols consistently
                converted_symbol = force_usdc_if_needed(symbol, self.live_usdc_pairs)
                final_workload.add(converted_symbol)
                if converted_symbol != symbol:
                    logger.debug(f"Converted {symbol} to {converted_symbol}")
            
            # 3. Store the final, converted list as the authoritative source
            self.symbols = sorted(list(final_workload))
            
            # 4. Re-populate the helper sets from this final list
            self.usdc_symbols.clear()
            self.usdt_symbols.clear() # This will now be mostly empty, which is correct
            for s in self.symbols:
                if s.endswith("USDC"): self.usdc_symbols.add(s)
                elif s.endswith("USDT"): self.usdt_symbols.add(s)

            logger.info(f"✅ Final workload created with {len(self.symbols)} symbols after live verification.")
            logger.info(f"📊 Live USDC pairs in use: {len(self.usdc_symbols)}")
            return True

        except Exception as e:
            logger.error(f"An unexpected error occurred while processing remote symbols: {e}")
            logger.warning("Using fallback symbol list...")
            return self._use_fallback_symbols()
    
    def _use_fallback_symbols(self):
        """Use a hardcoded fallback list of popular symbols when remote loading fails."""
        fallback_symbols = get_fallback_usdc_pairs()
        
        logger.info(f"Using fallback symbol list with {len(fallback_symbols)} symbols")
        
        # Convert to USDC where available
        final_workload = set()
        for symbol in fallback_symbols:
            converted_symbol = force_usdc_if_needed(symbol, self.live_usdc_pairs)
            final_workload.add(converted_symbol)
            if converted_symbol != symbol:
                logger.debug(f"Converted {symbol} to {converted_symbol}")
        
        # Store the final, converted list
        self.symbols = sorted(list(final_workload))
        
        # Re-populate the helper sets
        self.usdc_symbols.clear()
        self.usdt_symbols.clear()
        for s in self.symbols:
            if s.endswith("USDC"): 
                self.usdc_symbols.add(s)
            elif s.endswith("USDT"): 
                self.usdt_symbols.add(s)

        logger.info(f"✅ Fallback workload created with {len(self.symbols)} symbols")
        logger.info(f"📊 USDC pairs in fallback: {len(self.usdc_symbols)}")
        return True


    async def _write_local_klines(self, symbol: str, interval: str, klines_data: List[Dict]):
        """(NEW) Asynchronously writes kline data to the gateway's local cache."""
        if not klines_data: return
        
        # Track activity to prevent hanging
        if hasattr(self, 'last_activity_time'):
            self.last_activity_time = time.time()
        
        file_path = self.config.LOCAL_KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
        
        merged_data = []
        try:
            if file_path.exists():
                try:
                    async with aiofiles.open(file_path, 'r', encoding='utf-8') as existing_file:
                        existing_raw = await existing_file.read()
                except PermissionError:
                    logging.warning(f"⚠️ Permission denied reading {file_path}, fixing permissions...")
                    fix_file_permissions(file_path)
                    async with aiofiles.open(file_path, 'r', encoding='utf-8') as existing_file:
                        existing_raw = await existing_file.read()
                if existing_raw.strip():
                    existing_data = json.loads(existing_raw)
                    if isinstance(existing_data, list):
                        merged = {}
                        for entry in existing_data:
                            if isinstance(entry, dict) and entry.get('timestamp'):
                                merged[entry['timestamp']] = entry
                        for entry in klines_data:
                            if isinstance(entry, dict) and entry.get('timestamp'):
                                merged[entry['timestamp']] = entry
                        merged_data = sorted(merged.values(), key=lambda x: x.get('timestamp', ''))
                        logger.info(f"📚 Merged klines for {symbol}_{interval}: {len(merged_data)} total bars")
            if not merged_data:
                merged_data = sorted([entry for entry in klines_data if isinstance(entry, dict) and entry.get('timestamp')], key=lambda x: x.get('timestamp', ''))

            target_bars = self.config.IDEAL_BARS_TARGETS.get(interval, 1800)
            if len(merged_data) > target_bars:
                merged_data = merged_data[-target_bars:]

            if not merged_data:
                logger.warning(f"No valid klines to write for {symbol}_{interval}")
                return []
         
            try:
                # Use safe file write with permission fixing
                if not safe_file_write(file_path, merged_data):
                    logger.error(f"Failed to write to {file_path} even after fixing permissions")
                    return []
                logger.debug(f"Updated local cache for {symbol}_{interval}")
                
                # Immediately backup this file to ensure data safety
                await self._backup_single_klines_file(symbol, interval, str(file_path))
                
            except Exception as e:
                logger.error(f"Failed to write local cache for {symbol}_{interval}: {e}")
        
        except Exception as e:
            logger.warning(f"Could not merge existing data for {symbol}_{interval}: {e}")
            merged_data = sorted([entry for entry in klines_data if isinstance(entry, dict) and entry.get('timestamp')], key=lambda x: x.get('timestamp', ''))

    async def _backup_single_klines_file(self, symbol: str, interval: str, cache_path: str):
        """Immediately backup a single klines file when it's updated."""
        try:
            backup_dir = self.config.LOCAL_KLINES_CACHE_DIR / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            backup_path = backup_dir / f"{symbol}_{interval}.json"
            shutil.copy2(cache_path, backup_path)
            logger.debug(f"📦 Immediately backed up {symbol}_{interval} to backups/")
            
        except Exception as e:
            logger.debug(f"Could not immediately backup {symbol}_{interval}: {e}")
            
        # Mark this symbol+timeframe as completed in progress tracking
        await self._mark_timeframe_completed(symbol, interval)
        
        # Clean up old backups if needed
        await self._cleanup_old_backups()

    async def _cleanup_old_backups(self):
        """Clean up old backup files when more than 2400 files exist."""
        try:
            backup_dir = self.config.LOCAL_KLINES_CACHE_DIR / "backups"
            if not backup_dir.exists():
                return
            backup_files = []
            for file_path in backup_dir.glob("*.json"):
                    mtime = os.path.getmtime(file_path)
                    backup_files.append((mtime, file_path))
            backup_files.sort(key=lambda x: x[0])
            if len(backup_files) > 2400:
                files_to_remove = len(backup_files) - 2400
                removed_count = 0
                for i in range(files_to_remove):
                    try:
                        os.remove(backup_files[i][1])
                        removed_count += 1
                    except Exception as e:
                        logger.debug(f"Could not remove old backup {backup_files[i][1]}: {e}")
                if removed_count > 0:
                    logger.info(f"🧹 Cleaned up {removed_count} old backup files (kept 2400 most recent)")
        except Exception as e:
            logger.error(f"Error in backup cleanup: {e}")

    async def _mark_timeframe_completed(self, symbol: str, interval: str):
        """Mark a specific symbol+timeframe as completed in progress tracking."""
        try:
            # Ensure progress checkpoint directory exists
            self.config.PROGRESS_CHECKPOINT_DIR.mkdir(exist_ok=True)
            
            # Create checkpoint file: {symbol}_{interval}_completed.flag
            checkpoint_file = self.config.PROGRESS_CHECKPOINT_DIR / f"{symbol}_{interval}_completed.flag"
            checkpoint_file.touch()
            
            # Add completion timestamp
            safe_file_write(checkpoint_file, f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}")
                
            logger.debug(f"✅ Progress checkpoint: {symbol}_{interval} marked as completed")
        except Exception as e:
            logger.debug(f"Could not create progress checkpoint for {symbol}_{interval}: {e}")
    
    def _is_timeframe_completed(self, symbol: str, interval: str) -> bool:
        """Check if a specific symbol+timeframe is already completed."""
        try:
            checkpoint_file = self.config.PROGRESS_CHECKPOINT_DIR / f"{symbol}_{interval}_completed.flag"
            return checkpoint_file.exists()
        except Exception:
            return False
    
    def _get_completed_timeframes(self) -> set:
        """Get all completed symbol+timeframe combinations."""
        try:
            completed = set()
            if not self.config.PROGRESS_CHECKPOINT_DIR.exists():
                return completed
                
            for checkpoint_file in self.config.PROGRESS_CHECKPOINT_DIR.glob("*_completed.flag"):
                # Extract symbol and interval from filename
                filename = checkpoint_file.stem  # Remove .flag extension
                if filename.endswith('_completed'):
                    parts = filename.replace('_completed', '').split('_')
                    if len(parts) >= 2:
                        # Handle symbols with underscores (like 1000SHIB)
                        interval = parts[-1]  # Last part is always interval
                        symbol = '_'.join(parts[:-1])  # Everything else is symbol
                        completed.add((symbol, interval))
            
            return completed
        except Exception as e:
            logger.warning(f"Error reading progress checkpoints: {e}")
            return set()

    async def startup_bulk_sync(self):
        """(NEW) One-time sync from Redis to populate the gateway's local cache at startup."""
        logger.info("🚀 Starting bulk sync from Redis to populate local cache...")
        
        # Get already completed timeframes to skip
        completed_timeframes = self._get_completed_timeframes()
        logger.info(f"📊 Found {len(completed_timeframes)} already completed timeframes, will skip these")
        
        synced_count = 0
        skipped_count = 0
        total_requests = len(self.symbols) * len(self.config.TIMEFRAMES)
        completed_requests = 0
        
        for symbol in self.symbols:
            for interval in self.config.TIMEFRAMES:
                completed_requests += 1
                
                # Skip if already completed
                if (symbol, interval) in completed_timeframes:
                    skipped_count += 1
                    logger.debug(f"⏭️ Skipping {symbol}_{interval} (already completed)")
                    continue
                
                try:
                    redis_key = f"klines:{symbol}:{interval}"
                    data_raw = await self.redis_client.get(redis_key)
                    if data_raw:
                        payload = json.loads(data_raw)
                        klines = payload.get('klines', [])
                        if klines:
                            merged = await self._write_local_klines(symbol, interval, klines)
                            if merged:
                                await self._broadcast_klines(symbol, interval, merged)
                                synced_count += 1
                                await self._mark_timeframe_completed(symbol, interval)
                            else:
                                logger.warning(f"Startup sync produced empty merge for {symbol}_{interval}")
                except Exception as e:
                    logger.warning(f"Could not sync {symbol}_{interval} during startup: {e}")
                
                # Log progress every 50 requests
                if completed_requests % 50 == 0:
                    progress = (completed_requests / total_requests) * 100
                    logger.info(f"📊 Startup sync progress: {completed_requests}/{total_requests} ({progress:.1f}%) - Synced: {synced_count}, Skipped: {skipped_count}")
        
        logger.info(f"✅ Startup bulk sync complete. Synced: {synced_count}, Skipped: {skipped_count}, Total: {total_requests}")

    async def process_kline_update(self, symbol: str, interval: str):
        """(NEW) Fetches update from Redis, updates local cache, and broadcasts."""
        try:
            redis_key = f"klines:{symbol}:{interval}"
            data_raw = await self.redis_client.get(redis_key)
            if not data_raw:
                logger.warning(f"Received update notification for {symbol}_{interval}, but key is empty.")
                return

            payload = json.loads(data_raw)
            klines = payload.get('klines')
            if not klines: return

            # 1. Update gateway's own local cache
            merged = await self._write_local_klines(symbol, interval, klines)
 
            # 2. Act as the primary broadcaster, reinforcing the data in Redis
            if merged:
                await self._broadcast_klines(symbol, interval, merged)

        except Exception as e:
            logger.error(f"Failed to process update for {symbol}_{interval}: {e}")

    async def _process_live_kline(self, kline_data: Dict):
        """(NEW) Processes a live kline for ANY timeframe, checks freshness, and broadcasts if newer."""
        try:
            symbol = kline_data['s']
            interval = kline_data['i']
            
            # Convert Binance timeframe to our internal format
            if interval == '1d':
                internal_interval = 'D'
            else:
                internal_interval = interval

            # Convert to our standard format
            new_kline = {
                'timestamp': pd.to_datetime(kline_data['t'], unit='ms', utc=True),
                'open': float(kline_data['o']),
                'high': float(kline_data['h']),
                'low': float(kline_data['l']),
                'close': float(kline_data['c']),
                'volume': float(kline_data['v']),
            }

            # Handle USDC/USDT symbol mapping
            original_symbol = symbol
            if symbol.endswith("USDT") and symbol.replace("USDT", "USDC") in self.usdc_symbols:
                original_symbol = symbol.replace("USDT", "USDC")

            # Convert symbol to best available format (USDC if available, otherwise USDT)
            original_symbol = force_usdc_if_needed(original_symbol, self.live_usdc_pairs)

            # --- THE CRITICAL CHECK ---
            redis_key = f"klines:{original_symbol}:{internal_interval}"
            existing_data_raw = await self.redis_client.get(redis_key)
            if existing_data_raw:
                try:
                    existing_payload = json.loads(existing_data_raw)
                    if existing_payload.get('klines'):
                        last_redis_ts = pd.to_datetime(existing_payload['klines'][-1]['timestamp'])
                        if new_kline['timestamp'] <= last_redis_ts:
                            logger.debug(f"Gateway skipping {internal_interval} broadcast for {original_symbol}, Redis is fresher.")
                            return # Do not overwrite
                except (json.JSONDecodeError, IndexError, TypeError):
                    pass # Proceed to write if parsing fails

            # If we are here, our data is fresher. Let's broadcast.
            logger.info(f"Gateway broadcasting new {internal_interval} kline for {original_symbol}")

            # Fetch full list to append to
            try:
                klines_list = json.loads(existing_data_raw)['klines'] if existing_data_raw else []
            except (json.JSONDecodeError, KeyError, TypeError):
                klines_list = []
            new_kline['timestamp'] = new_kline['timestamp'].strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            klines_list.append(new_kline)

            # Keep the list trimmed
            target_bars = self.config.IDEAL_BARS_TARGETS.get(internal_interval, 1800)
            if len(klines_list) > target_bars:
                klines_list = klines_list[-target_bars:]

            # Update local cache and then broadcast this timeframe
            merged = await self._write_local_klines(original_symbol, internal_interval, klines_list)
            if merged:
                await self._broadcast_klines(original_symbol, internal_interval, merged)

        except Exception as e:
            logger.error(f"Gateway failed to process live kline: {e}")
    


    async def _handle_combined_stream(self, stream_url: str):
        """(NEW) Handles both mark prices and klines from a single WebSocket."""
        import websockets
        while self.running:
            try:
                async with websockets.connect(stream_url) as websocket:
                    logger.info("✅ Gateway combined WebSocket connected.")
                    self.health_status['websocket_connected'] = True
                    while self.running:
                        msg = await websocket.recv()
                        data = json.loads(msg)['data']
                        event_type = data.get('e')
                        if event_type == 'markPriceUpdate':
                            await self._broadcast_mark_price(data['s'], float(data['p']))
                        elif event_type == 'kline' and data.get('k', {}).get('x'): # 'x': True means bar is closed
                            await self._process_live_kline(data['k'])
            except Exception as e:
                logger.error(f"Gateway WebSocket error: {e}. Reconnecting...")
                self.health_status['websocket_connected'] = False
                await asyncio.sleep(5)

    async def start_combined_websocket(self):
        """(REVISED) Starts separate WebSocket connections for different timeframes to avoid stream limits."""
        try:
            if not self.symbols: return False
            
            logger.info("📡 Starting separate WebSocket connections for different timeframes...")
            
            # Start mark price WebSocket
            await self.start_mark_price_websocket()
            
            # Start kline WebSockets for each timeframe
            timeframes = ['1m', '3m', '15m', '1h', '4h', 'D']
            
            for tf in timeframes:
                try:
                    await self.start_kline_websocket(tf)
                    await asyncio.sleep(0.1)  # Small delay between connections
                except Exception as e:
                    logger.error(f"Failed to start {tf} WebSocket: {e}")
                    continue
            
            return True
        except Exception as e:
            logger.error(f"Failed to start WebSocket connections: {e}")
            return False


    async def kline_update_listener(self):
        """(NEW) Listens to Redis Pub/Sub for update notifications."""
        if not self.redis_client: return
        logger.info("👂 Starting Kline update listener on 'klines_updates' channel...")
        
        pubsub = self.redis_client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe("klines_updates")

        while self.running:
            try:
                message = await pubsub.get_message(timeout=1.0)
                if message is None:
                    continue
                
                update_info = json.loads(message['data'])
                symbol = update_info.get('symbol')
                interval = update_info.get('interval')
                
                if symbol and interval:
                    logger.info(f"📢 Received update for {symbol}_{interval}. Processing...")
                    await self.process_kline_update(symbol, interval)

            except redis.ConnectionError:
                logger.error("Redis connection lost in listener. Attempting to reconnect...")
                await asyncio.sleep(5)
                # Attempt to resubscribe might be needed depending on library version
                await pubsub.subscribe("klines_updates")
            except Exception as e:
                logger.error(f"Error in kline listener: {e}")
                await asyncio.sleep(1)
        

    async def initialize_binance_client(self):
        """Initialize aiohttp session for futures API calls (no credentials needed)."""
        try:
            # Create aiohttp session for direct API calls to futures
            self.http_session = aiohttp.ClientSession()
            
            # Test connection by calling futures API
            test_url = "https://fapi.binance.com/fapi/v1/time"
            async with self.http_session.get(test_url) as resp:
                if resp.status == 200:
                    server_time = await resp.json()
                    logger.info(f"✅ Binance futures API connected. Server time: {server_time}")
                    self.health_status['binance_connected'] = True
                    return True
                else:
                    logger.error(f"Failed to connect to Binance futures API. Status: {resp.status}")
                    return False
            
        except Exception as e:
            logger.error(f"Failed to initialize Binance futures API client: {e}")
            self.health_status['binance_connected'] = False
            return False
    
    async def initialize_redis_client(self):
        """Initialize Redis client for broadcasting data."""
        try:
            self.redis_client = redis.Redis(
                host=self.config.REDIS_HOST,
                port=self.config.REDIS_PORT,
                db=self.config.REDIS_DB,
                password=self.config.REDIS_PASSWORD,
                decode_responses=True
            )
            
            # Test connection
            await self.redis_client.ping()
            logger.info(f"✅ Redis client connected to {self.config.REDIS_HOST}:{self.config.REDIS_PORT}")
            self.health_status['redis_connected'] = True
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.health_status['redis_connected'] = False
            return False
    
    async def fetch_historical_klines(self, symbol: str, interval: str, limit: int = 1500):
        """Fetch historical kline data from Binance API with timeout protection."""
        try:
            # Add timeout protection to prevent hanging
            return await asyncio.wait_for(
                self._fetch_historical_klines_internal(symbol, interval, limit),
                timeout=45
            )
        except asyncio.TimeoutError:
            logger.error(f"❌ Timeout fetching historical data for {symbol}_{interval} after 45 seconds")
            return []
        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}_{interval}: {e}")
            return []
    
    async def _fetch_historical_klines_internal(self, symbol: str, interval: str, limit: int = 1500):
        """Internal method for fetching historical klines."""
        if not self.http_session:
            return []
            
        # For USDC pairs, use the USDT endpoint as required by the API
        api_symbol = symbol.replace("USDC", "USDT") if symbol.endswith("USDC") else symbol
        
        # Convert internal interval to Binance format
        binance_interval = '1d' if interval == 'D' else interval
        
        url = f"https://fapi.binance.com/fapi/v1/klines"
        params = {
            'symbol': api_symbol,
            'interval': binance_interval,
            'limit': min(limit, 1500)  # Binance API limit
        }
        
        async with self.http_session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()

                if not data:
                    logger.warning(f"No data returned for {symbol} {interval}")
                    return []

                # Convert Binance format to our internal format
                klines = []
                for kline in data:
                    try:
                        kline_obj = {
                            'timestamp': pd.to_datetime(kline[0], unit='ms', utc=True).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            'open': float(kline[1]),
                            'high': float(kline[2]),
                            'low': float(kline[3]),
                            'close': float(kline[4]),
                            'volume': float(kline[5]),
                        }
                        klines.append(kline_obj)
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Invalid kline data for {symbol} {interval}: {e}")
                        continue

                return klines
            else:
                logger.warning(f"Failed to fetch {symbol} {interval}: HTTP {response.status}")
                return []
    
    async def populate_historical_data(self):
        """Populate historical kline data with time-aware scheduling and completion verification."""
        logger.info("🔄 Starting time-aware historical data population...")
        
        # Add overall timeout protection
        try:
            return await asyncio.wait_for(
                self._populate_historical_data_internal(),
                timeout=1800  # 30 minutes total timeout
            )
        except asyncio.TimeoutError:
            logger.critical("❌ Historical data population timeout after 30 minutes - script may be stuck")
            logger.critical("🔄 Continuing with live updates while historical data is incomplete")
            return False
    
    async def _populate_historical_data_internal(self):
        """Internal method for historical data population with timeout protection."""
        # Define timeframe priorities and optimal update times
        timeframe_schedule = {
            '3m': {'priority': 1, 'update_interval': 180, 'description': 'Every 3 minutes (highest priority)'},
            '15m': {'priority': 2, 'update_interval': 900, 'description': 'Every 15 minutes at :00, :15, :30, :45'},
            '1h': {'priority': 3, 'update_interval': 3600, 'description': 'Every hour at :00:00'},
            '4h': {'priority': 4, 'update_interval': 14400, 'description': 'Every 4 hours at :00:00'},
            'D': {'priority': 5, 'update_interval': 86400, 'description': 'Daily at 00:00:00'},
            '1m': {'priority': 6, 'update_interval': 60, 'description': 'Every minute (lowest priority)'}
        }
        
        total_requests = len(self.symbols) * len(timeframe_schedule)
        completed_requests = 0
        
        logger.info(f"🚀 Starting time-aware symbol population of {len(self.symbols)} symbols...")
        logger.info("📅 Timeframe priorities:")
        for tf, info in sorted(timeframe_schedule.items(), key=lambda x: x[1]['priority']):
            logger.info(f"  {tf}: {info['description']}")
        
        # Track completion status for each symbol
        symbol_completion_status = {}
        for symbol in self.symbols:
            symbol_completion_status[symbol] = {tf: False for tf in timeframe_schedule.keys()}
        
        # Process symbols sequentially to maintain alphabetical order
        for symbol_index, symbol in enumerate(self.symbols):
            # Add per-symbol timeout protection
            try:
                await asyncio.wait_for(
                    self._process_single_symbol(symbol, symbol_index, timeframe_schedule, symbol_completion_status, completed_requests, total_requests),
                    timeout=60  # 1 minute per symbol
                )
            except asyncio.TimeoutError:
                logger.error(f"❌ Timeout processing symbol {symbol} - moving to next symbol")
                completed_requests += len(timeframe_schedule)
                continue
        
        # VERIFICATION PHASE: Check if all symbols are complete
        logger.info("🔍 Starting completion verification phase...")
        incomplete_symbols = []
        
        # SPECIAL CHECK: 3m is highest priority - ensure ALL symbols have 3m data
        missing_3m_symbols = []
        for symbol in self.symbols:
            if not symbol_completion_status[symbol]['3m']:
                missing_3m_symbols.append(symbol)
        
        if missing_3m_symbols:
            logger.warning(f"🚨 CRITICAL: {len(missing_3m_symbols)} symbols missing 3m data (highest priority)")
            logger.warning(f"🚨 Missing 3m: {missing_3m_symbols[:10]}...")  # Show first 10
            logger.warning("🔄 3m data will be prioritized in retry phase")
        
        for symbol in self.symbols:
            symbol_complete = all(symbol_completion_status[symbol].values())
            if not symbol_complete:
                incomplete_timeframes = [tf for tf, complete in symbol_completion_status[symbol].items() if not complete]
                incomplete_symbols.append((symbol, incomplete_timeframes))
                logger.warning(f"❌ {symbol}: Missing timeframes: {incomplete_timeframes}")
        
        if incomplete_symbols:
            logger.warning(f"⚠️ Found {len(incomplete_symbols)} symbols with incomplete data")
            logger.warning("🔄 Starting retry phase for incomplete symbols...")
            
            # Retry incomplete symbols with 3m priority
            await self._retry_incomplete_symbols(incomplete_symbols, symbol_completion_status, missing_3m_symbols)
        else:
            logger.info("✅ All symbols completed successfully!")
        
        # Final verification with 3m priority check
        final_completion = await self._verify_final_completion_with_3m_priority()
        if final_completion:
            logger.info("🎉 Historical data population fully completed and verified!")
        else:
            logger.warning("⚠️ Some symbols may still have incomplete data")
        
        return True
    
    async def _process_single_symbol(self, symbol, symbol_index, timeframe_schedule, symbol_completion_status, completed_requests, total_requests):
        """Process a single symbol with all its timeframes."""
        logger.info(f"📊 Processing symbol {symbol_index + 1}/{len(self.symbols)}: {symbol}")
        
        # Process timeframes in priority order (3m first, then 15m, etc.)
        priority_timeframes = sorted(timeframe_schedule.keys(), key=lambda x: timeframe_schedule[x]['priority'])
        
        # Process all timeframes for this symbol concurrently
        timeframe_tasks = []
        for interval in priority_timeframes:
            task = self._populate_single_symbol_timeframe(symbol, interval, total_requests)
            timeframe_tasks.append(task)
        
        # Process all timeframes for this symbol concurrently
        if timeframe_tasks:
            try:
                results = await asyncio.gather(*timeframe_tasks, return_exceptions=True)
                
                # Verify completion for each timeframe
                symbol_complete = True
                for i, result in enumerate(results):
                    if not isinstance(result, Exception) and result:
                        completed_requests += 1
                        symbol_completion_status[symbol][priority_timeframes[i]] = True
                    else:
                        symbol_complete = False
                        logger.warning(f"⚠️ Failed to populate {symbol}_{priority_timeframes[i]}")
                
                # Log symbol completion status
                completed_timeframes = sum(symbol_completion_status[symbol].values())
                logger.info(f"📊 {symbol}: {completed_timeframes}/{len(priority_timeframes)} timeframes completed")
                
                if not symbol_complete:
                    logger.warning(f"⚠️ Symbol {symbol} has incomplete data - will be retried in next round")
                
                # Progress logging
                progress = (completed_requests / total_requests) * 100
                logger.info(f"📊 Historical data population progress: {completed_requests}/{total_requests} ({progress:.1f}%)")
                
            except Exception as e:
                logger.error(f"Error processing timeframes for {symbol}: {e}")
                completed_requests += len(priority_timeframes)  # Count as completed even if failed
        
        # Small delay between symbols to avoid overwhelming
        await asyncio.sleep(0.2)
    
    async def _retry_incomplete_symbols(self, incomplete_symbols, symbol_completion_status, missing_3m_symbols):
        """Retry population for symbols with incomplete data with 3m priority."""
        max_retries = 5  # Increased retries for 3m priority
        retry_count = 0
        
        # PHASE 1: Prioritize 3m data for ALL symbols
        if missing_3m_symbols:
            logger.info(f"🚨 PHASE 1: Prioritizing 3m data for {len(missing_3m_symbols)} missing symbols")
            
            while missing_3m_symbols and retry_count < max_retries:
                retry_count += 1
                logger.info(f"🔄 3m retry attempt {retry_count}/{max_retries} for {len(missing_3m_symbols)} symbols")
                
                still_missing_3m = []
                
                for symbol in missing_3m_symbols:
                    logger.info(f"🔄 Retrying 3m for {symbol} (attempt {retry_count})")
                    
                    try:
                        result = await self._populate_single_symbol_timeframe(symbol, '3m', 0)
                        
                        if result:
                            symbol_completion_status[symbol]['3m'] = True
                            logger.info(f"✅ {symbol} 3m completed on retry {retry_count}")
                        else:
                            still_missing_3m.append(symbol)
                            logger.warning(f"⚠️ {symbol} 3m still failed on retry {retry_count}")
                        
                        await asyncio.sleep(0.1)  # Small delay between retries
                        
                    except Exception as e:
                        logger.error(f"Error retrying 3m for {symbol}: {e}")
                        still_missing_3m.append(symbol)
                
                missing_3m_symbols = still_missing_3m
                
                if missing_3m_symbols:
                    logger.warning(f"⚠️ After 3m retry {retry_count}: {len(missing_3m_symbols)} symbols still missing 3m")
                    await asyncio.sleep(10)  # Wait longer between 3m retries
                else:
                    logger.info("🎉 All 241 symbols now have 3m data!")
                    break
            
            if missing_3m_symbols:
                logger.error(f"❌ CRITICAL: After {max_retries} retries, {len(missing_3m_symbols)} symbols still missing 3m data")
                logger.error(f"❌ Missing 3m symbols: {missing_3m_symbols}")
        
        # PHASE 2: Retry other incomplete timeframes
        logger.info("🔄 PHASE 2: Retrying other incomplete timeframes...")
        retry_count = 0
        
        while incomplete_symbols and retry_count < max_retries:
            retry_count += 1
            logger.info(f"🔄 General retry attempt {retry_count}/{max_retries} for {len(incomplete_symbols)} incomplete symbols")
            
            still_incomplete = []
            
            for symbol, missing_timeframes in incomplete_symbols:
                # Skip 3m if it's already complete
                missing_timeframes = [tf for tf in missing_timeframes if tf != '3m' or not symbol_completion_status[symbol]['3m']]
                
                if not missing_timeframes:
                    continue  # Symbol is complete
                
                logger.info(f"🔄 Retrying {symbol} for missing timeframes: {missing_timeframes}")
                
                # Retry only missing timeframes
                retry_tasks = []
                for interval in missing_timeframes:
                    task = self._populate_single_symbol_timeframe(symbol, interval, 0)
                    retry_tasks.append(task)
                
                if retry_tasks:
                    try:
                        results = await asyncio.gather(*retry_tasks, return_exceptions=True)
                        
                        # Update completion status
                        for i, result in enumerate(results):
                            if not isinstance(result, Exception) and result:
                                symbol_completion_status[symbol][missing_timeframes[i]] = True
                        
                        # Check if symbol is now complete
                        if all(symbol_completion_status[symbol].values()):
                            logger.info(f"✅ {symbol} completed on retry {retry_count}")
                        else:
                            still_missing = [tf for tf, complete in symbol_completion_status[symbol].items() if not complete]
                            still_incomplete.append((symbol, still_missing))
                            
                    except Exception as e:
                        logger.error(f"Error retrying {symbol}: {e}")
                        still_incomplete.append((symbol, missing_timeframes))
                
                await asyncio.sleep(0.1)  # Small delay between retries
            
            incomplete_symbols = still_incomplete
            
            if incomplete_symbols:
                logger.warning(f"⚠️ After retry {retry_count}: {len(incomplete_symbols)} symbols still incomplete")
                await asyncio.sleep(5)  # Wait before next retry
        
        if incomplete_symbols:
            logger.error(f"❌ After {max_retries} retries, {len(incomplete_symbols)} symbols still incomplete")
            for symbol, missing in incomplete_symbols:
                logger.error(f"❌ {symbol}: Still missing {missing}")
    
    async def _verify_final_completion_with_3m_priority(self):
        """Final verification with 3m priority check."""
        logger.info("🔍 Performing final completion verification with 3m priority...")
        
        # First, verify 3m data (highest priority)
        missing_3m = []
        for symbol in self.symbols:
            redis_key = f"klines:{symbol}:3m"
            try:
                data = await self.redis_client.get(redis_key)
                if not data:
                    missing_3m.append(symbol)
            except Exception as e:
                missing_3m.append(symbol)
        
        if missing_3m:
            logger.error(f"🚨 CRITICAL: {len(missing_3m)} symbols still missing 3m data after all retries")
            logger.error(f"🚨 Missing 3m: {missing_3m}")
            return False
        
        logger.info("✅ 3m verification passed - all 241 symbols have 3m data")
        
        # Then verify other timeframes
        missing_data = []
        
        for symbol in self.symbols:
            for interval in self.config.TIMEFRAMES:
                if interval == '3m':  # Already verified above
                    continue
                    
                redis_key = f"klines:{symbol}:{interval}"
                try:
                    data = await self.redis_client.get(redis_key)
                    if not data:
                        missing_data.append(f"{symbol}_{interval}")
                except Exception as e:
                    missing_data.append(f"{symbol}_{interval}")
        
        if missing_data:
            logger.warning(f"⚠️ Final verification found {len(missing_data)} missing data points (non-3m)")
            logger.warning(f"⚠️ Missing: {missing_data[:10]}...")  # Show first 10
            return False
        else:
            logger.info("✅ Final verification passed - all symbols have all timeframes")
            return True
    
    async def _force_independent_fetch(self, symbol: str, timeframe: str):
        """Force independent data fetching from API when data is insufficient."""
        try:
            logger.info(f"🚀 FORCE FETCH: {symbol}_{timeframe} - fetching independently from API")
            
            # Check current data count
            redis_key = f"klines:{symbol}:{timeframe}"
            current_data = await self.redis_client.get(redis_key)
            current_count = 0
            
            if current_data:
                try:
                    payload = json.loads(current_data)
                    current_count = len(payload.get('klines', []))
                except Exception:
                    current_count = 0
            
            # If we have less than 1000 bars, fetch fresh data
            if current_count < 1000:
                logger.warning(f"⚠️ {symbol}_{timeframe} has only {current_count} bars, fetching fresh data...")
                
                # Fetch historical data from API
                target_bars = 1500
                historical_klines = await self.fetch_historical_klines(symbol, timeframe, target_bars)
                
                if historical_klines and len(historical_klines) > 0:
                    # Write to local cache
                    await self._write_local_klines(symbol, timeframe, historical_klines)
                    
                    # Broadcast to Redis
                    await self._broadcast_klines(symbol, timeframe, historical_klines)
                    
                    logger.info(f"✅ FORCE FETCH SUCCESS: {symbol}_{timeframe} now has {len(historical_klines)} bars")
                    return True
                else:
                    logger.error(f"❌ FORCE FETCH FAILED: {symbol}_{timeframe} - no data from API")
                    return False
            else:
                logger.info(f"✅ {symbol}_{timeframe} already has sufficient data ({current_count} bars)")
                return True
                
        except Exception as e:
            logger.error(f"❌ FORCE FETCH ERROR: {symbol}_{timeframe} - {e}")
            return False

    async def _continuous_data_monitor(self):
        """Continuous monitoring that respects time-aware scheduling for data updates."""
        logger.info("🕐 Starting continuous data monitor with time-aware scheduling...")
        
        # Timeframe update schedule (when each should be updated)
        update_schedule = {
            '3m': {'interval': 180, 'last_update': {}, 'description': 'Every 3 minutes'},
            '15m': {'interval': 900, 'last_update': {}, 'description': 'Every 15 minutes'},
            '1h': {'interval': 3600, 'last_update': {}, 'description': 'Every hour'},
            '4h': {'interval': 14400, 'last_update': {}, 'description': 'Every 4 hours'},
            'D': {'interval': 86400, 'last_update': {}, 'description': 'Daily'},
            '1m': {'interval': 60, 'last_update': {}, 'description': 'Every minute'}
        }
        
        while self.running:
            try:
                current_time = time.time()
                
                # Check each timeframe for updates
                for timeframe, schedule_info in update_schedule.items():
                    # Check if it's time to update this timeframe
                    for symbol in self.symbols:
                        last_update = schedule_info['last_update'].get(symbol, 0)
                        time_since_update = current_time - last_update
                        
                        if time_since_update >= schedule_info['interval']:
                            # Check if data is stale or missing
                            redis_key = f"klines:{symbol}:{timeframe}"
                            try:
                                data = await self.redis_client.get(redis_key)
                                needs_update = False
                                
                                if not data:
                                    needs_update = True
                                    logger.info(f"🕐 {timeframe} update needed for {symbol}: No data found")
                                else:
                                    # Check data freshness
                                    payload = json.loads(data)
                                    if payload.get('published_at_utc'):
                                        published_time = pd.to_datetime(payload['published_at_utc'], utc=True).to_pydatetime()
                                        age_seconds = (datetime.now(timezone.utc) - published_time).total_seconds()
                                        
                                        if age_seconds > schedule_info['interval'] * 1.5:  # 50% tolerance
                                            needs_update = True
                                            logger.info(f"🕐 {timeframe} update needed for {symbol}: Data is {age_seconds:.0f}s old")
                                
                                if needs_update:
                                    # Update this symbol/timeframe
                                    logger.info(f"🔄 Updating {symbol}_{timeframe} (age: {time_since_update:.0f}s)")
                                    # ENHANCED: Force independent data fetching when data is insufficient
                                    await self._force_independent_fetch(symbol, timeframe)
                                    schedule_info['last_update'][symbol] = current_time
                                    
                                    # Small delay to avoid overwhelming
                                    await asyncio.sleep(0.1)
                                    
                            except Exception as e:
                                logger.warning(f"Error checking {symbol}_{timeframe}: {e}")
                
                # Log monitoring status every 5 minutes
                if int(current_time) % 300 == 0:
                    logger.info("🕐 Continuous monitor status:")
                    for timeframe, schedule_info in update_schedule.items():
                        active_symbols = len([s for s in self.symbols if current_time - schedule_info['last_update'].get(s, 0) < schedule_info['interval'] * 2])
                        logger.info(f"  {timeframe}: {active_symbols}/{len(self.symbols)} symbols recently updated")
                
                # Wait before next check
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Error in continuous data monitor: {e}")
                await asyncio.sleep(60)
    

    async def _populate_single_symbol_timeframe(self, symbol: str, interval: str, total_requests: int):
        """Helper method to populate a single symbol/timeframe combination with timeout protection."""
        try:
            # Add timeout protection to prevent hanging (Python 3.10 compatible)
            # CRITICAL: For 3m data, we MUST succeed - no excuses!
            is_critical_3m = (interval == '3m')
            
            # Check if we already have sufficient data
            redis_key = f"klines:{symbol}:{interval}"
            existing_data_raw = await self.redis_client.get(redis_key)
            
            target_bars = self.config.IDEAL_BARS_TARGETS.get(interval, 1800)
            
            if existing_data_raw:
                try:
                    existing_payload = json.loads(existing_data_raw)
                    existing_klines = existing_payload.get('klines', [])
                    if len(existing_klines) >= target_bars * 0.9:  # 90% threshold
                        logger.debug(f"Sufficient data exists for {symbol} {interval} ({len(existing_klines)} bars)")
                        return True
                except (json.JSONDecodeError, KeyError):
                    pass
            
            # CRITICAL 3m DATA: Try ALL sources with maximum effort
            if is_critical_3m:
                logger.critical(f"🚨 CRITICAL: {symbol}_3m missing - MUST populate this!")
            
            # GENTLE APPROACH: Try LOCAL klines_cache first, then backups, then API as last resort
            logger.info(f"📚 Looking for {symbol}_{interval} in local cache and backups...")
            
            # Try local klines_cache first
            local_cache_path = f"klines_cache/{symbol}_{interval}.json"
            if os.path.exists(local_cache_path):
                try:
                    with open(local_cache_path, 'r') as f:
                        klines_data = json.load(f)
                    if klines_data and len(klines_data) >= target_bars * 0.9:
                        logger.info(f"✅ Found {symbol}_{interval} in local klines_cache")
                        await self._broadcast_klines(symbol, interval, klines_data)
                        return True
                except Exception as e:
                    logger.debug(f"Could not read local cache for {symbol}_{interval}: {e}")
            
            # Try backups folder as second option
            backups_path = self.config.LOCAL_KLINES_CACHE_DIR / "backups" / f"{symbol}_{interval}.json"
            if backups_path.exists():
                try:
                    with backups_path.open('r') as f:
                        klines_data = json.load(f)
                    if klines_data and len(klines_data) >= target_bars * 0.9:
                        logger.info(f"✅ Found {symbol}_{interval} in backups folder")

                        await self._broadcast_klines(symbol, interval, klines_data)
                        return True
                except Exception as e:
                    logger.debug(f"Could not read backup for {symbol}_{interval}: {e}")
            
            # Try the existing fetch_and_broadcast_klines method (which might read from remote)
            logger.info(f"🔄 Trying remote cache for {symbol}_{interval}...")
            success = await self.fetch_and_broadcast_klines(symbol, interval)
            
            if success:
                logger.info(f"✅ Successfully populated {symbol}_{interval} from remote cache")
                return True
            
            # CRITICAL 3m: Use API with maximum retries if cache fails
            if is_critical_3m:
                logger.critical(f"🚨 CRITICAL 3m: {symbol}_3m not in cache - FORCING API call!")
                
                # Try API with multiple attempts for 3m
                for attempt in range(3):
                    try:
                        logger.critical(f"🚨 CRITICAL 3m: API attempt {attempt + 1} / 3 for {symbol}_3m")
                        historical_klines = await self.fetch_historical_klines(symbol, interval, target_bars)
                        if historical_klines:
                            await self._write_local_klines(symbol, interval, historical_klines)
                            await self._broadcast_klines(symbol, interval, historical_klines)
                            logger.critical(f"🚨 CRITICAL 3m: SUCCESS! {symbol}_3m populated via API")
                            return True
                    except Exception as e:
                        logger.critical(f"🚨 CRITICAL 3m: API attempt {attempt + 1} failed for {symbol}_3m: {e}")
                        if attempt < 2:  # Wait before retry
                            await asyncio.sleep(5)
                
                # If we get here, 3m failed - this is CRITICAL
                logger.critical(f"🚨 CRITICAL FAILURE: {symbol}_3m could not be populated from ANY source!")
                return False
            
            # GENTLE FALLBACK: Only use API if absolutely necessary (very rare cases)
            logger.warning(f"⚠️ No cache data found for {symbol}_{interval}, trying gentle API fallback...")
            try:
                historical_klines = await self.fetch_historical_klines(symbol, interval, target_bars)
                if historical_klines:
                    await self._write_local_klines(symbol, interval, historical_klines)
                    await self._broadcast_klines(symbol, interval, historical_klines)
                    logger.info(f"✅ Gently populated {len(historical_klines)} bars for {symbol} {interval} via API")
                    return True
            except Exception as e:
                logger.debug(f"Gentle API fallback failed for {symbol}_{interval}: {e}")
            
            logger.warning(f"⚠️ Failed to populate {symbol}_{interval} from all sources")
            return False
            
        except Exception as e:
            logger.error(f"Failed to populate historical data for {symbol} {interval}: {e}")
            return False

    
    async def _populate_single_symbol_timeframe_internal(self, symbol: str, interval: str, total_requests: int):
        """Internal method to populate a single symbol/timeframe combination - alias for compatibility."""
        return await self._populate_single_symbol_timeframe(symbol, interval, total_requests)
    
    async def start_mark_price_websocket(self):
        """Start WebSocket connection for real-time mark price updates with proper batching."""
        try:
            if not self.symbols:
                logger.error("No symbols loaded, cannot start WebSocket")
                return False
            
            # Batch symbols into groups of ~85 to stay within Binance limits
            batch_size = 85
            symbol_batches = [self.symbols[i:i + batch_size] for i in range(0, len(self.symbols), batch_size)]
            
            logger.info(f"Starting mark price WebSocket with {len(symbol_batches)} batches of ~{batch_size} symbols each")
            
            for batch_num, symbol_batch in enumerate(symbol_batches):
                try:
                    # Create mark price stream for this batch
                    symbol_streams = []
                    for symbol in symbol_batch:
                        # For USDC pairs, use the USDT endpoint as required by the API
                        api_symbol = symbol.replace("USDC", "USDT") if symbol.endswith("USDC") else symbol
                        symbol_streams.append(f"{api_symbol.lower()}@markPrice")
                    
                    stream_url = f"wss://fstream.binance.com/stream?streams={'/'.join(symbol_streams)}"
                    
                    # Start the WebSocket connection for this batch
                    asyncio.create_task(self._handle_mark_price_stream_direct(stream_url, batch_num))
                    
                    # Small delay between batch connections
                    await asyncio.sleep(0.2)
                    
                except Exception as e:
                    logger.error(f"Failed to start mark price WebSocket batch {batch_num}: {e}")
                    continue
            
            logger.info(f"✅ Mark price WebSocket started for {len(self.symbols)} symbols in {len(symbol_batches)} batches")
            self.health_status['websocket_connected'] = True
            
            # ENHANCED: Add debugging to track mark price WebSocket status
            logger.info("🔍 Mark price WebSocket debugging enabled - will log connection status")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start mark price WebSocket: {e}")
            self.health_status['websocket_connected'] = False
            return False
    
    async def start_kline_websocket(self, timeframe: str):
        """Start WebSocket connection for a specific kline timeframe with proper batching."""
        try:
            if not self.symbols:
                logger.error("No symbols loaded, cannot start WebSocket")
                return False
            
            # Batch symbols into groups of ~85 to stay within Binance limits
            batch_size = 85
            symbol_batches = [self.symbols[i:i + batch_size] for i in range(0, len(self.symbols), batch_size)]
            
            logger.info(f"Starting {timeframe} kline WebSocket with {len(symbol_batches)} batches of ~{batch_size} symbols each")
            
            for batch_num, symbol_batch in enumerate(symbol_batches):
                try:
                    # Create kline stream for this batch
                    symbol_streams = []
                    for symbol in symbol_batch:
                        # For USDC pairs, use the USDT endpoint as required by the API
                        api_symbol = symbol.replace("USDC", "USDT") if symbol.endswith("USDC") else symbol
                        # Convert internal timeframe to Binance WebSocket format
                        binance_timeframe = '1d' if timeframe == 'D' else timeframe
                        symbol_streams.append(f"{api_symbol.lower()}@kline_{binance_timeframe}")
                    
                    stream_url = f"wss://fstream.binance.com/stream?streams={'/'.join(symbol_streams)}"
                    
                    # Start the WebSocket connection for this batch
                    asyncio.create_task(self._handle_kline_stream(stream_url, timeframe, batch_num))
                    
                    # Small delay between batch connections
                    await asyncio.sleep(0.2)
                    
                except Exception as e:
                    logger.error(f"Failed to start {timeframe} kline WebSocket batch {batch_num}: {e}")
                    continue
            
            logger.info(f"✅ {timeframe} kline WebSocket started for {len(self.symbols)} symbols in {len(symbol_batches)} batches")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start {timeframe} kline WebSocket: {e}")
            return False
    
    async def _handle_mark_price_stream_direct(self, stream_url: str, batch_num: int = 0):
        """Handle incoming mark price WebSocket messages directly from futures stream."""
        try:
            import websockets
            logger.info(f"Attempting to connect mark price WebSocket batch {batch_num} to: {stream_url}")
            async with websockets.connect(stream_url) as websocket:
                logger.info(f"✅ Mark price WebSocket batch {batch_num} connected")
                # Add a small test to see if we can receive messages
                try:
                    first_msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    logger.info(f"First mark price message received on batch {batch_num}: {first_msg[:100]}...")
                except asyncio.TimeoutError:
                    logger.warning(f"No first mark price message received within 5 seconds for batch {batch_num}")
                
                # Track message count and last message time for health monitoring
                message_count = 0
                last_message_time = time.time()
                
                while self.running:
                    try:
                        # Add timeout to prevent hanging
                        msg = await asyncio.wait_for(websocket.recv(), timeout=60.0)
                        if msg:
                            message_count += 1
                            last_message_time = time.time()
                            
                            try:
                                data = json.loads(msg)
                                if 'data' in data:
                                    stream_name = data['stream']
                                    symbol = stream_name.split('@')[0].upper()  # Get symbol from stream name
                                    mark_price = float(data['data']['p'])
                                    
                                    # Handle USDC/USDT conversion
                                    final_symbol = symbol
                                    if symbol.endswith("USDT"):
                                        # Check if this USDT symbol has a corresponding USDC pair
                                        usdc_symbol = symbol.replace("USDT", "USDC")
                                        if usdc_symbol in self.usdc_symbols:
                                            final_symbol = usdc_symbol
                                    
                                    # Update local storage
                                    self.mark_prices[final_symbol] = mark_price
                                    self.health_status['last_mark_price_update'] = datetime.now(timezone.utc)
                                    
                                    # Broadcast to Redis
                                    await self._broadcast_mark_price(final_symbol, mark_price)
                                    
                                    # ENHANCED: More frequent logging for debugging
                                    if message_count % 10 == 0:  # Log every 10 messages instead of 100
                                        logger.info(f"📊 Mark price batch {batch_num}: Processed {message_count} messages, last symbol: {final_symbol} = ${mark_price}")
                                else:
                                    logger.warning(f"Unexpected mark price message format in batch {batch_num}: {data}")
                                    
                            except json.JSONDecodeError as e:
                                logger.error(f"Failed to parse mark price JSON in batch {batch_num}: {e}, message: {msg}")
                                continue
                            except Exception as e:
                                logger.error(f"Error processing mark price message in batch {batch_num}: {e}")
                                logger.error(f"Message was: {msg}")
                                continue
                                
                    except asyncio.TimeoutError:
                        # Check if we're still receiving messages
                        current_time = time.time()
                        if current_time - last_message_time > 120:  # 2 minutes without messages
                            logger.warning(f"Mark price WebSocket batch {batch_num} timeout - no messages for 2 minutes, reconnecting...")
                            break
                        else:
                            logger.debug(f"Mark price WebSocket batch {batch_num} timeout - continuing to wait...")
                            continue
                    except websockets.exceptions.ConnectionClosed:
                        logger.info(f"Mark price WebSocket batch {batch_num} connection closed by server, will reconnect...")
                        break
                    except Exception as e:
                        logger.error(f"WebSocket receive error in mark price batch {batch_num}: {e}")
                        break
                        
        except Exception as e:
            logger.error(f"Mark price WebSocket batch {batch_num} connection failed: {e}")
            # Add reconnection logic
            if self.running:
                logger.info(f"Mark price WebSocket batch {batch_num} will attempt reconnection...")
                await asyncio.sleep(5)  # Wait before reconnecting
        finally:
            logger.info(f"Mark price WebSocket batch {batch_num} disconnected")
            # If still running, schedule reconnection
            if self.running:
                logger.info(f"Mark price WebSocket batch {batch_num} scheduling reconnection...")
                # Create a new task for reconnection
                asyncio.create_task(self._reconnect_mark_price_batch(batch_num))
    
    async def _handle_kline_stream(self, stream_url: str, timeframe: str, batch_num: int = 0):
        """Handle incoming kline WebSocket messages for a specific timeframe."""
        try:
            import websockets
            logger.info(f"Attempting to connect {timeframe} kline WebSocket batch {batch_num} to: {stream_url}")
            async with websockets.connect(stream_url) as websocket:
                logger.info(f"✅ {timeframe} kline WebSocket batch {batch_num} connected")
                # Add a small test to see if we can receive messages
                try:
                    first_msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    logger.info(f"First message received on {timeframe} batch {batch_num}: {first_msg[:100]}...")
                except asyncio.TimeoutError:
                    logger.warning(f"No first message received within 5 seconds for {timeframe} batch {batch_num}")
                
                while self.running:
                    try:
                        msg = await websocket.recv()
                        if msg:
                            try:
                                data = json.loads(msg)
                                logger.debug(f"Received {timeframe} kline message: {data}")
                                
                                if 'data' in data:
                                    stream_name = data['stream']
                                    symbol = stream_name.split('@')[0].upper()  # Get symbol from stream name
                                    kline_data = data['data']['k']
                                    
                                    # Handle USDC/USDT conversion
                                    final_symbol = symbol
                                    if symbol.endswith("USDT"):
                                        # Check if this USDT symbol has a corresponding USDC pair
                                        usdc_symbol = symbol.replace("USDT", "USDC")
                                        if usdc_symbol in self.usdc_symbols:
                                            final_symbol = usdc_symbol
                                    
                                    # Process the kline data
                                    await self._process_live_kline(kline_data)
                                else:
                                    logger.warning(f"Unexpected message format in {timeframe} batch {batch_num}: {data}")
                                    
                            except json.JSONDecodeError as e:
                                logger.error(f"Failed to parse JSON in {timeframe} batch {batch_num}: {e}, message: {msg}")
                                continue
                            except Exception as e:
                                logger.error(f"Error processing {timeframe} kline message in batch {batch_num}: {e}")
                                logger.error(f"Message was: {msg}")
                                continue
                                
                    except websockets.exceptions.ConnectionClosed:
                        logger.info(f"{timeframe} kline WebSocket batch {batch_num} connection closed by server")
                        break
                    except Exception as e:
                        logger.error(f"WebSocket receive error in {timeframe} batch {batch_num}: {e}")
                        break
                        
        except Exception as e:
            logger.error(f"{timeframe} kline WebSocket batch {batch_num} connection failed: {e}")
        finally:
            logger.info(f"{timeframe} kline WebSocket batch {batch_num} disconnected")
    
    async def _reconnect_mark_price_batch(self, batch_num: int):
        """Reconnect a specific mark price WebSocket batch with exponential backoff."""
        try:
            # Calculate batch symbols
            batch_size = 85
            start_idx = batch_num * batch_size
            end_idx = start_idx + batch_size
            symbol_batch = self.symbols[start_idx:end_idx]
            
            # Create mark price stream for this batch
            symbol_streams = []
            for symbol in symbol_batch:
                # For USDC pairs, use the USDT endpoint as required by the API
                api_symbol = symbol.replace("USDC", "USDT") if symbol.endswith("USDC") else symbol
                symbol_streams.append(f"{api_symbol.lower()}@markPrice")
            
            stream_url = f"wss://fstream.binance.com/stream?streams={'/'.join(symbol_streams)}"
            
            logger.info(f"🔄 Reconnecting mark price WebSocket batch {batch_num} in 5 seconds...")
            await asyncio.sleep(5)
            
            # Start the reconnection
            asyncio.create_task(self._handle_mark_price_stream_direct(stream_url, batch_num))
            
        except Exception as e:
            logger.error(f"Failed to schedule reconnection for mark price batch {batch_num}: {e}")
    
    async def _broadcast_mark_price(self, symbol: str, mark_price: float):
        """Broadcast mark price to Redis using the same keys as existing scripts."""
        try:
            if not self.redis_client:
                return
            
            # Create mark price data structure (compatible with existing scripts)
            mark_price_data = {
                'symbol': symbol,
                'price': mark_price,  # ez_indicators.py expects 'price' key
                'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'source': 'gateway_broadcaster'
            }
            
            # Set individual mark price key (for polling - utils.py, ez_indicators.py)
            redis_key = f"mark_price:{symbol}"
            await self.redis_client.setex(
                redis_key,
                300,  # 5 minutes TTL
                json.dumps(mark_price_data)
            )
            
            # Set in live_mark_prices hash (for real-time subscriptions - ez_rankings.py)
            await self.redis_client.hset(
                'live_mark_prices',
                symbol,
                json.dumps(mark_price_data)
            )
            
            # Set in mark_prices hash (for bulk operations - ez_mark_prices.py)
            await self.redis_client.hset(
                'mark_prices',
                symbol,
                json.dumps(mark_price_data)
            )
            
            # Set expiration for both hashes
            await self.redis_client.expire('live_mark_prices', 300)
            await self.redis_client.expire('mark_prices', 300)
            
            logger.info(f"✅ Broadcast mark price for {symbol}: ${mark_price}")
            
        except Exception as e:
            logger.error(f"Failed to broadcast mark price for {symbol}: {e}")
    
    async def fetch_and_broadcast_klines(self, symbol: str, interval: str):
        """
        Read kline data from local klines_cache folder and broadcast to Redis.
        Gateway server reads from its own local files (no SSH needed).
        """
        try:
            if not self.redis_client:
                return False
            
            # Read directly from local klines_cache (no SSH needed)
            cache_file_path = f"klines_cache/{symbol}_{interval}.json"
            
            if not os.path.exists(cache_file_path):
                logger.debug(f"Local cache file not found for {symbol}_{interval}")
                return False
            
            try:
                # Read the JSON file directly from local filesystem
                with open(cache_file_path, 'r') as f:
                    klines_data = json.load(f)

                if not klines_data or not isinstance(klines_data, list):
                    logger.warning(f"No valid klines list found in JSON for {symbol}_{interval}")
                    return False
                
                # The data is already a list of dictionaries, no need for line-by-line parsing.
                
                # Sort by timestamp to ensure chronological order (good practice)
                klines_data.sort(key=lambda x: x.get('timestamp', ''))
                
                # Limit data based on timeframe (matching ez_prices.py exactly)
                target_bars = self.config.IDEAL_BARS_TARGETS.get(interval, 1800)
                
                if len(klines_data) > target_bars:
                    klines_data = klines_data[-target_bars:]
                
                # Broadcast to Redis
                await self._broadcast_klines(symbol, interval, klines_data)
                
                # Update health status (with safety check)
                if self.health_status is None:
                    logger.error(f"DEBUG: health_status is None for {symbol}_{interval}! Initializing...")
                    self.health_status = {}
                try:
                    # Fix the nested setdefault issue with safety checks
                    if "last_kline_update" not in self.health_status or self.health_status["last_kline_update"] is None:
                        self.health_status["last_kline_update"] = {}
                    if symbol not in self.health_status["last_kline_update"]:
                        self.health_status["last_kline_update"][symbol] = {}
                    self.health_status["last_kline_update"][symbol][interval] = datetime.now(timezone.utc)
                except Exception as e:
                    logger.error(f"DEBUG: setdefault error for {symbol}_{interval}: {e}, health_status type: {type(self.health_status)}")
                # Removed duplicate update
                
                logger.info(f"✅ Read and broadcast {len(klines_data)} klines for {symbol}_{interval} from cache")
                return True
                
            except Exception as e:
                logger.error(f"Local file processing error for {symbol}_{interval}: {e}")
                return False
            
        except Exception as e:
            logger.error(f"General failure in fetch_and_broadcast_klines for {symbol}_{interval}: {e}")
            return False
    
    async def _broadcast_klines(self, symbol: str, interval: str, klines_data: List[Dict]):
        """Broadcast kline data to Redis in the EXACT same format as ez_prices.py and ez_mark_prices.py."""
        try:
            if not self.redis_client:
                return False
            
            # Convert symbol to best available format (USDC if available, otherwise USDT)
            converted_symbol = force_usdc_if_needed(symbol, self.live_usdc_pairs)
            
            # Create payload in the EXACT same format as working scripts
            payload = {
                "symbol": converted_symbol,
                "interval": interval,
                "published_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "klines": klines_data,
                "count": len(klines_data),
                "last_timestamp": klines_data[-1]['timestamp'] if klines_data else None
            }
            
            # Set main klines data key (same format as existing scripts)
            redis_key = f"klines:{converted_symbol}:{interval}"
            
            # Use same expiry logic as ez_prices.py (5 minutes * 6 = 30 minutes for higher timeframes)
            expiry_seconds = self._get_expiry_for_timeframe(interval)
            
            await self.redis_client.setex(
                redis_key,
                expiry_seconds,
                json.dumps(payload)
            )
            
            # Set metadata key for quick access (same as ez_mark_prices.py)
            metadata_key = f"klines_meta:{symbol}:{interval}"
            metadata = {
                "symbol": symbol,
                "interval": interval,
                "last_updated": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                "count": len(klines_data),
                "last_timestamp": klines_data[-1]['timestamp'] if klines_data else None
            }
            await self.redis_client.setex(metadata_key, expiry_seconds, json.dumps(metadata))
            
            # Publish notification to Pub/Sub channel (EXACT same format as existing scripts)
            notification = {
                'symbol': symbol,
                'interval': interval,
                'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                'action': 'klines_updated',
                'count': len(klines_data)
            }
            await self.redis_client.publish("klines_updates", json.dumps(notification))
            
            logger.info(f"✅ Published {len(klines_data)} klines for {symbol} {interval} to Redis + metadata + notification")
            return True
            
        except Exception as e:
            logger.error(f"Failed to broadcast klines for {symbol} {interval}: {e}")
            return False
    

    
    async def periodic_health_check(self):
        """Periodically check and log system health with stuck detection."""
        last_heartbeat = time.time()
        stuck_threshold = 300  # 5 minutes without activity
        
        while self.running:
            try:
                current_time = time.time()
                
                # Check Redis connection
                if self.redis_client:
                    try:
                        await self.redis_client.ping()
                        self.health_status['redis_connected'] = True
                    except Exception:
                        self.health_status['redis_connected'] = False
                
                # Check Binance connection
                if hasattr(self, 'http_session') and self.http_session:
                    try:
                        # Test futures API connection
                        resp = await asyncio.wait_for(
                            self.http_session.get("https://fapi.binance.com/fapi/v1/time"),
                            timeout=10
                        )
                        if resp.status == 200:
                            self.health_status['binance_connected'] = True
                        else:
                            self.health_status['binance_connected'] = False
                    except asyncio.TimeoutError:
                        logger.warning("⚠️ Binance API connection timeout")
                        self.health_status['binance_connected'] = False
                    except Exception:
                        self.health_status['binance_connected'] = False
                
                # Update heartbeat
                self.health_status['last_heartbeat'] = current_time
                self.health_status['uptime_seconds'] = current_time - self.start_time
                
                # Check for stuck state
                if hasattr(self, 'last_activity_time'):
                    time_since_activity = current_time - self.last_activity_time
                    if time_since_activity > stuck_threshold:
                        logger.error(f"🚨 CRITICAL: Script appears stuck - no activity for {time_since_activity:.0f} seconds")
                        logger.error("🚨 Initiating emergency shutdown...")
                        await self.graceful_shutdown()
                        break
                
                # ENHANCED: Check mark price status
                mark_price_status = "❌ No updates"
                if self.health_status.get('last_mark_price_update'):
                    last_update = self.health_status['last_mark_price_update']
                    age_seconds = (datetime.now(timezone.utc) - last_update).total_seconds()
                    if age_seconds < 60:
                        mark_price_status = f"✅ Active ({age_seconds:.0f}s ago)"
                    else:
                        mark_price_status = f"⚠️ Stale ({age_seconds:.0f}s ago)"
                
                # Log health status
                logger.info("🏥 Health Check:")
                for key, value in self.health_status.items():
                    logger.info(f"  {key}: {value}")
                logger.info(f"  mark_price_status: {mark_price_status}")
                
                # Set health status in Redis for monitoring
                if self.redis_client:
                    try:
                        await self.redis_client.setex(
                            'gateway_broadcaster_health',
                            60,  # 1 minute TTL
                            json.dumps(self.health_status)
                        )
                    except Exception:
                        pass
                
                await asyncio.sleep(self.config.HEALTH_CHECK_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in health check: {e}")
                await asyncio.sleep(60)
    

    async def start(self):
        """(REVISED) Start the gateway data broadcaster with live verification."""
        logger.info("🚀 Starting Gateway Data Broadcaster...")

        if not await self.initialize_binance_client():
            logger.critical("FATAL: Could not initialize Binance client. Shutting down.")
            return

        logger.info("Fetching live USDC pair data from Binance...")
        self.live_usdc_pairs = await get_live_usdc_pairs(self.http_session)
        if not self.live_usdc_pairs:
             logger.critical("FATAL: Could not obtain any USDC pair data. Shutting down.")
             return

        if not await self.load_symbols_from_remote():
            logger.critical("FATAL: Could not load symbols from remote niels server. Shutting down.")
            return
        
        # Initialize Redis
        if not await self.initialize_redis_client():
            logger.critical("FATAL: Could not initialize Redis client. Shutting down.")
            return
        
        # Load existing data from Redis into local cache
        logger.info("📚 Loading existing kline data from Redis into local cache...")
        await self.startup_bulk_sync()
        
        # Ensure backup directories exist
        backup_dir = self.config.LOCAL_KLINES_CACHE_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        logger.info("📁 Backup directories initialized")
        
        # Ensure progress checkpoint directory exists
        self.config.PROGRESS_CHECKPOINT_DIR.mkdir(exist_ok=True)
        logger.info("📁 Progress checkpoint directory initialized")

        # Start the combined WebSocket with the correct, final symbol list FIRST
        logger.info("🔌 Starting WebSocket connections for live data...")
        if not await self.start_combined_websocket():
            logger.error("Failed to start combined WebSocket")
            return
        
        self.running = True
        
        # Start all tasks including historical data population in background
        tasks = []
        
        # Add tasks with proper error handling
        try:
            tasks.append(asyncio.create_task(self.kline_update_listener()))
            tasks.append(asyncio.create_task(self.periodic_health_check()))
            tasks.append(asyncio.create_task(self._background_historical_population()))
            tasks.append(asyncio.create_task(self.monitor_gateway_progress()))
            tasks.append(asyncio.create_task(self._active_symbol_scanner()))
            tasks.append(asyncio.create_task(self._continuous_data_monitor()))
            tasks.append(asyncio.create_task(self._periodic_backup_klines()))
            
            logger.info(f"✅ Started {len(tasks)} background tasks")
            
        except Exception as e:
            logger.error(f"Failed to start background tasks: {e}")
            raise
        
        logger.info("✅ Gateway Data Broadcaster started successfully!")
        logger.info(f"📊 Broadcasting data for {len(self.symbols)} symbols")
        logger.info(f"🔌 Redis target: {self.config.REDIS_HOST}:{self.config.REDIS_PORT}")
        logger.info("📚 Historical data population will continue in background...")
        
        try:
            # Add watchdog timer to prevent hanging
            watchdog_task = asyncio.create_task(self._watchdog_timer())
            tasks.append(watchdog_task)
            
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        finally:
            await self.cleanup()
    
    async def _background_historical_population(self):
        """Background task to populate historical data independently 24/7/365."""
        try:
            # Small delay to let other systems start up first
            await asyncio.sleep(5)
            
            logger.info("📚 Starting INDEPENDENT historical data population (24/7/365)...")
            
            # ENHANCED: Continuous independent data fetching
            while self.running:
                try:
                    # Check all symbols and timeframes for insufficient data
                    timeframes = ['1m', '3m', '15m', '1h', '4h', 'D']
                    insufficient_data = []
                    
                    for symbol in self.symbols:
                        for timeframe in timeframes:
                            redis_key = f"klines:{symbol}:{timeframe}"
                            try:
                                data = await self.redis_client.get(redis_key)
                                if not data:
                                    insufficient_data.append((symbol, timeframe))
                                else:
                                    payload = json.loads(data)
                                    klines = payload.get('klines', [])
                                    if len(klines) < 1000:  # Less than 1000 bars
                                        insufficient_data.append((symbol, timeframe))
                            except Exception:
                                insufficient_data.append((symbol, timeframe))
                    
                    if insufficient_data:
                        logger.warning(f"⚠️ Found {len(insufficient_data)} symbols with insufficient data, fetching independently...")
                        
                        # Fetch data for insufficient symbols
                        for symbol, timeframe in insufficient_data[:10]:  # Limit to 10 at a time
                            await self._force_independent_fetch(symbol, timeframe)
                            await asyncio.sleep(0.5)  # Rate limiting
                    else:
                        logger.info("✅ All symbols have sufficient data")
                    
                    # Wait before next check (5 minutes)
                    await asyncio.sleep(300)
                    
                except Exception as e:
                    logger.error(f"Error in independent data population: {e}")
                    await asyncio.sleep(60)  # Wait before retry
                    
        except Exception as e:
            logger.error(f"Fatal error in background historical population: {e}")
            # Don't let this task fail the entire system
            await asyncio.sleep(60)  # Wait before potentially retrying
        
        # CRITICAL: Stop this task after completion to prevent infinite loops
        logger.info("🛑 Historical data population task completed - stopping background task")
        return
    
    async def _watchdog_timer(self):
        """Watchdog timer to prevent the gateway from hanging indefinitely."""
        max_runtime = 7200  # 2 hours maximum runtime
        start_time = time.time()
        
        while self.running:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                current_time = time.time()
                runtime = current_time - start_time
                
                if runtime > max_runtime:
                    logger.critical(f"🚨 WATCHDOG: Gateway running for {runtime:.0f} seconds - FORCING SHUTDOWN")
                    await self.graceful_shutdown("WATCHDOG_TIMEOUT")
                    break
                
                # Check if we're stuck (no activity for 10 minutes)
                if hasattr(self, 'last_activity_time'):
                    time_since_activity = current_time - self.last_activity_time
                    if time_since_activity > 600:  # 10 minutes
                        logger.critical(f"🚨 WATCHDOG: No activity for {time_since_activity:.0f} seconds - FORCING SHUTDOWN")
                        await self.graceful_shutdown("WATCHDOG_STUCK")
                        break
                        
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
                await asyncio.sleep(60)
    
    async def get_progress_summary(self) -> dict:
        """Get a comprehensive progress summary for all symbols and timeframes."""
        try:
            completed_timeframes = self._get_completed_timeframes()
            total_expected = len(self.symbols) * len(self.config.TIMEFRAMES)
            completed_count = len(completed_timeframes)
            completion_percentage = (completed_count / total_expected) * 100 if total_expected > 0 else 0
            
            # Group by symbol to see which symbols are complete
            symbol_progress = {}
            for symbol in self.symbols:
                symbol_completed = 0
                for interval in self.config.TIMEFRAMES:
                    if (symbol, interval) in completed_timeframes:
                        symbol_completed += 1
                symbol_progress[symbol] = {
                    'completed': symbol_completed,
                    'total': len(self.config.TIMEFRAMES),
                    'percentage': (symbol_completed / len(self.config.TIMEFRAMES)) * 100
                }
            
            return {
                'overall': {
                    'completed': completed_count,
                    'total': total_expected,
                    'percentage': completion_percentage
                },
                'by_symbol': symbol_progress,
                'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            }
        except Exception as e:
            logger.error(f"Error getting progress summary: {e}")
            return {'error': str(e)}

    async def monitor_gateway_progress(self):
        """Monitor and log gateway progress to detect if it's getting stuck."""
        while self.running:
            try:
                # Check how many symbols have been processed recently
                current_time = datetime.now(timezone.utc)
                
                # Count symbols with recent updates
                recent_updates = 0
                total_symbols = len(self.symbols)
                
                for symbol in self.symbols:
                    # Check if symbol has recent data for any timeframe
                    has_recent_data = False
                    for interval in self.config.TIMEFRAMES:
                        redis_key = f"klines:{symbol}:{interval}"
                        try:
                            data_raw = await self.redis_client.get(redis_key)
                            if data_raw:
                                payload = json.loads(data_raw)
                                if payload.get('published_at_utc'):
                                    published_time = pd.to_datetime(payload['published_at_utc'], utc=True).to_pydatetime()
                                    if (current_time - published_time).total_seconds() < 300:  # 5 minutes
                                        has_recent_data = True
                                        break
                        except Exception:
                            continue
                    
                    if has_recent_data:
                        recent_updates += 1
                
                # Check mark price status
                mark_price_status = "unknown"
                try:
                    live_mark_prices_count = await self.redis_client.hlen('live_mark_prices')
                    mark_prices_count = await self.redis_client.hlen('mark_prices')
                    individual_mark_prices_count = len(await self.redis_client.keys("mark_price:*"))
                    
                    if live_mark_prices_count > 0 and mark_prices_count > 0 and individual_mark_prices_count > 0:
                        mark_price_status = f"✅ {live_mark_prices_count} live_mark_prices, {mark_prices_count} mark_prices, {individual_mark_prices_count} individual keys"
                    elif live_mark_prices_count > 0 and individual_mark_prices_count > 0:
                        mark_price_status = f"⚠️ {live_mark_prices_count} live_mark_prices, {mark_prices_count} mark_prices, {individual_mark_prices_count} individual keys"
                    elif live_mark_prices_count > 0:
                        mark_price_status = f"⚠️ {live_mark_prices_count} live_mark_prices only"
                    else:
                        mark_price_status = f"❌ No mark prices found"
                except Exception as e:
                    mark_price_status = f"❌ Error checking mark prices: {e}"
                
                # Log progress
                progress_percent = (recent_updates / total_symbols) * 100
                logger.info(f"📊 Gateway Progress: {recent_updates}/{total_symbols} symbols have recent data ({progress_percent:.1f}%)")
                
                # Get detailed progress summary
                progress_summary = await self.get_progress_summary()
                if 'overall' in progress_summary:
                    overall = progress_summary['overall']
                    logger.info(f"📊 Historical Data Progress: {overall['completed']}/{overall['total']} timeframes completed ({overall['percentage']:.1f}%)")
                
                logger.info(f"💰 Mark Prices: {mark_price_status}")
                
                # Check for stuck symbols (symbols that haven't been updated in a while)
                if recent_updates < total_symbols * 0.5:  # Less than 50% have recent data
                    logger.warning(f"⚠️ Gateway may be stuck: Only {recent_updates}/{total_symbols} symbols have recent data")
                
                # Wait before next check
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Error in gateway progress monitoring: {e}")
                await asyncio.sleep(60)
    
    async def _active_symbol_scanner(self):
        """Actively scan through all symbols to ensure none are missed."""
        while self.running:
            try:
                logger.info("🔍 Starting active symbol scan to check for missed symbols...")
                
                # Check which symbols might be missing data
                missing_symbols = []
                current_time = datetime.now(timezone.utc)
                
                for symbol in self.symbols:
                    symbol_missing = False
                    for interval in self.config.TIMEFRAMES:
                        redis_key = f"klines:{symbol}:{interval}"
                        try:
                            data_raw = await self.redis_client.get(redis_key)
                            if not data_raw:
                                symbol_missing = True
                                break
                            
                            # Check if data is recent (within last 10 minutes)
                            payload = json.loads(data_raw)
                            if payload.get('published_at_utc'):
                                published_time = pd.to_datetime(payload['published_at_utc'], utc=True).to_pydatetime()
                                if (current_time - published_time).total_seconds() > 600:  # 10 minutes
                                    symbol_missing = True
                                    break
                        except Exception:
                            symbol_missing = True
                            break
                    
                    if symbol_missing:
                        missing_symbols.append(symbol)
                
                if missing_symbols:
                    logger.info(f"🔍 Found {len(missing_symbols)} symbols that may need attention")
                    logger.info(f"🔍 Missing symbols: {missing_symbols[:10]}...")  # Show first 10
                    
                    # Try to fetch data for a few missing symbols
                    for symbol in missing_symbols[:5]:  # Process max 5 at a time
                        try:
                            await self._fetch_and_broadcast_symbol_data(symbol)
                            await asyncio.sleep(0.5)  # Small delay between symbols
                        except Exception as e:
                            logger.warning(f"Failed to fetch data for {symbol}: {e}")
                else:
                    logger.info("✅ All symbols appear to have recent data")
                
                # Wait before next scan
                await asyncio.sleep(300)  # Scan every 5 minutes
                
            except Exception as e:
                logger.error(f"Error in active symbol scanner: {e}")
                await asyncio.sleep(300)
    
    async def _fetch_and_broadcast_symbol_data(self, symbol: str):
        """Fetch and broadcast data for a specific symbol from local cache."""
        try:
            logger.info(f"🔍 Fetching data for {symbol} from local cache...")
            
            for interval in self.config.TIMEFRAMES:
                try:
                    # Try to fetch from local cache
                    cache_file_path = f"klines_cache/{symbol}_{interval}.json"
                    
                    if os.path.exists(cache_file_path):
                        with open(cache_file_path, 'r') as f:
                            klines_data = json.load(f)
                        
                        if klines_data and isinstance(klines_data, list):
                            # Broadcast the data
                            await self._broadcast_klines(symbol, interval, klines_data)
                            logger.info(f"✅ Fetched and broadcast {len(klines_data)} klines for {symbol}_{interval}")
                            break  # Found data for this symbol, move to next
                    
                except Exception as e:
                    logger.debug(f"Could not fetch {symbol}_{interval}: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {e}")
    
    async def cleanup(self):
        """Clean up resources on shutdown."""
        logger.info("🧹 Cleaning up resources...")
        
        # Stop all running tasks
        self.running = False
        
        # Cancel all background tasks
        if hasattr(self, '_background_tasks'):
            for task in self._background_tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        
        # Close HTTP session
        if hasattr(self, 'http_session') and self.http_session:
            await self.http_session.close()
        
        # Close Redis client
        if self.redis_client:
            await self.redis_client.close()
        
        logger.info("✅ Cleanup complete")
    
    async def graceful_shutdown(self, signal_received=None):
        """Graceful shutdown handler."""
        if signal_received:
            logger.info(f"🛑 Received signal {signal_received}, initiating graceful shutdown...")
        else:
            logger.info("🛑 Initiating graceful shutdown...")
        
        # Set running flag to false to stop all loops
        self.running = False
        
        # Wait a moment for tasks to finish
        await asyncio.sleep(2)
        
        # Clean up resources
        await self.cleanup()
        
        logger.info("✅ Graceful shutdown completed")
        sys.exit(0)

    async def _periodic_backup_klines(self):
        """Periodically backup fresh klines data from local cache to backups folder."""
        while self.running:
            try:
                await asyncio.sleep(900)  # Run every 15 minutes
                
                backup_dir = self.config.LOCAL_KLINES_CACHE_DIR / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                
                backup_count = 0
                for cache_file in self.config.LOCAL_KLINES_CACHE_DIR.iterdir():
                    cache_file = cache_file.name
                    if cache_file.endswith('.json'):
                        try:
                            # Read from local cache
                            cache_path = self.config.LOCAL_KLINES_CACHE_DIR / cache_file
                            backup_path = self.config.LOCAL_KLINES_CACHE_DIR / "backups" / cache_file
                            
                            # Check if backup is needed (file doesn't exist or is older)
                            if not backup_path.exists() or \
                               cache_path.stat().st_mtime > backup_path.stat().st_mtime:
                                
                                # Copy file to backup
                                shutil.copy2(cache_path, backup_path)
                                backup_count += 1
                                
                        except Exception as e:
                            logger.debug(f"Could not backup {cache_file}: {e}")
                
                if backup_count > 0:
                    logger.info(f"📦 Backed up {backup_count} klines files to backups/klines_cache/")
                
                # Clean up old backups after periodic backup
                await self._cleanup_old_backups()
                    
            except Exception as e:
                logger.error(f"Error in periodic backup: {e}")
                await asyncio.sleep(60)  # Wait before retrying

async def main():
    """Main entry point with proper signal handling and timeout protection."""
    # Check PID file to prevent multiple instances
    check_pid_file()
    
    broadcaster = None
    
    def signal_handler(signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"🛑 Received signal {signum}, shutting down gracefully...")
        if broadcaster:
            asyncio.create_task(broadcaster.graceful_shutdown(signum))
        else:
            cleanup_pid_file()
            sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # kill command
    
    try:
        broadcaster = GatewayDataBroadcaster()
        
        # Add timeout protection to startup
        try:
            await asyncio.wait_for(broadcaster.start(), timeout=300)  # 5 minutes startup timeout
        except asyncio.TimeoutError:
            logger.critical("❌ Gateway startup timed out after 5 minutes")
            if broadcaster:
                await broadcaster.cleanup()
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        if broadcaster:
            await broadcaster.cleanup()
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if broadcaster:
            await broadcaster.cleanup()
        sys.exit(1)
    finally:
        cleanup_pid_file()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

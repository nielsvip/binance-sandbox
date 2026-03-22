#!/usr/bin/env python3

"""

Dedicated Gap Filler Script for ez_prices

Fills all data holes from backups and API calls with rate limiting

Runs independently without interrupting ez_prices operations

"""

import asyncio

import aiohttp

import json

import time

import pandas as pd

import numpy as np

from pathlib import Path

from datetime import datetime, timezone, timedelta

from typing import Dict, List, Optional, Tuple

import redis.asyncio as redis

from collections import defaultdict

import logging

import aiofiles

import aiofiles.os as aio_os

from concurrent.futures import ThreadPoolExecutor

import multiprocessing

def load_json_file(file_path: Path) -> Optional[Dict]:
    """Load JSON file synchronously."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except Exception:
        return None

# try:

#     from ez_prices import ConsolidatedBackupSystem

# except ModuleNotFoundError:

#     ConsolidatedBackupSystem = None

# Import from existing modules

from config import Config

from utils import logger, clean_kline_data, get_live_usdc_pairs, orjson_default

config = Config()

class DedicatedGapFiller:
    """Dedicated gap filler that runs independently to fill all data holes."""
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.redis_client: Optional[redis.Redis] = None
        self.symbols: List[str] = []
        self.logger = logger
        # High-performance async settings for billions of bars
        self.max_concurrent_symbols = 100  # Process 100 symbols concurrently
        self.max_concurrent_gaps = 200     # Process 200 gaps concurrently
        self.max_concurrent_api_calls = 50 # Max 50 concurrent API calls
        self.chunk_size = 50000           # Process data in 50k record chunks
        self.executor = ThreadPoolExecutor(max_workers=multiprocessing.cpu_count() * 6)
        # Rate limiting (optimized for high throughput)
        self.api_semaphore = asyncio.Semaphore(self.max_concurrent_api_calls)
        self.request_delay = 0.1  # 100ms between requests (faster)
        self.last_request_time = 0
        # Gap filling settings (optimized for billions of bars)
        self.max_gap_days = 90  # Increased to 90 days for more comprehensive filling
        self.batch_size = 50   # Process 50 symbols in batches
        self.continuous_mode = True  # Run continuously until no gaps remain
        # Semaphores for concurrency control
        self.symbol_semaphore = asyncio.Semaphore(self.max_concurrent_symbols)
        self.gap_semaphore = asyncio.Semaphore(self.max_concurrent_gaps)
        # Statistics
        self.stats = { 'gaps_filled': 0, 'api_calls_made': 0, 'backup_restorations': 0, 'errors': 0, 'start_time': None, 'symbols_processed': 0, 'data_points_processed': 0 }
    async def initialize(self):
        """Initialize connections and load symbols."""
        try:
            # self.consolidated_dir = None
            # if ConsolidatedBackupSystem is not None:
            #     try:
            #         self.logger.info("🔄 Consolidating backups before gap analysis...")
            #         backup_system = ConsolidatedBackupSystem(config.KLINES_CACHE_DIR)
            #         await backup_system.consolidate_all_symbols()
            #         if backup_system.backup_dirs:
            #             self.consolidated_dir = backup_system.backup_dirs[0]
            #             self.logger.info(f"✅ Consolidated backups available at {self.consolidated_dir}")
            #     except Exception as e:
            #         self.logger.warning(f"Could not consolidate backups: {e}")
            #         self.consolidated_dir = None
            # Load symbols
            with open(config.SYMBOLS_FILE, 'r') as f:
                self.symbols = json.load(f)
            # Initialize HTTP session
            self.session = await self._init_http_session()
            # Connect to Redis
            self.redis_client = redis.Redis( host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True, socket_connect_timeout=5, socket_timeout=5 )
            await self.redis_client.ping()
            self.stats['start_time'] = time.time()
            self.logger.info(f"🚀 Dedicated Gap Filler initialized with {len(self.symbols)} symbols")
        except Exception as e:
            self.logger.error(f"Failed to initialize gap filler: {e}")
            raise
    async def _init_http_session(self) -> aiohttp.ClientSession:
        """Initialize HTTP session with proper configuration."""
        connector = aiohttp.TCPConnector( limit=100, limit_per_host=20, ttl_dns_cache=300, use_dns_cache=True, )
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        return aiohttp.ClientSession( connector=connector, timeout=timeout, headers={'User-Agent': 'ez_gap_filler/1.0'} )
    async def _rate_limit(self):
        """Apply rate limiting to API calls."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.request_delay:
            await asyncio.sleep(self.request_delay - time_since_last)
        self.last_request_time = time.time()
    async def analyze_all_gaps(self) -> Dict:
        """Analyze gaps across all symbols and timeframes with maximum async performance."""
        self.logger.info("🔍 Analyzing gaps across all symbols with high concurrency...")
        intervals = ['3m', '15m', '1h', '4h', 'D']
        # Process all symbols concurrently
        async def analyze_symbol(symbol):
            async with self.symbol_semaphore:
                try:
                    symbol_gaps = {}
                    # Process all intervals for this symbol concurrently
                    async def analyze_interval(interval):
                        gaps = await self._analyze_symbol_gaps(symbol, interval)
                        return interval, gaps
                    # Create tasks for all intervals
                    interval_tasks = [analyze_interval(interval) for interval in intervals]
                    interval_results = await asyncio.gather(*interval_tasks, return_exceptions=True)
                    # Collect results
                    for result in interval_results:
                        if not isinstance(result, Exception) and result[1]:
                            interval, gaps = result
                            symbol_gaps[interval] = gaps
                    return symbol, symbol_gaps if symbol_gaps else None
                except Exception as e:
                    self.logger.error(f"Error analyzing {symbol}: {e}")
                    return symbol, None
        # Process all symbols concurrently
        symbol_tasks = [analyze_symbol(symbol) for symbol in self.symbols]
        symbol_results = await asyncio.gather(*symbol_tasks, return_exceptions=True)
        # Collect results
        gaps_by_symbol = {}
        total_gaps = 0
        for result in symbol_results:
            if not isinstance(result, Exception) and result[1]:
                symbol, symbol_gaps = result
                gaps_by_symbol[symbol] = symbol_gaps
                total_gaps += sum(len(gaps) for gaps in symbol_gaps.values())
        self.logger.info(f"📊 Found {total_gaps} gaps across {len(gaps_by_symbol)} symbols")
        return gaps_by_symbol
    async def _analyze_symbol_gaps(self, symbol: str, interval: str) -> List[Dict]:
        """Analyze gaps for a specific symbol and interval."""
        try:
            # Load current data
            df = await self._load_symbol_data(symbol, interval)
            if df.empty or len(df) < 2:
                return []
            # Convert timestamps and sort
            timestamps = pd.to_datetime(df['timestamp'], utc=True).sort_values()
            # Get expected interval
            expected_interval = self._get_expected_interval(interval)
            threshold = expected_interval * 2  # 2x expected interval as gap threshold
            gaps = []
            for i in range(1, len(timestamps)):
                time_diff = timestamps.iloc[i] - timestamps.iloc[i-1]
                if time_diff > threshold:
                    missing_klines = int(time_diff.total_seconds() / expected_interval.total_seconds())
                    gap_info = { 'start_time': timestamps.iloc[i-1], 'end_time': timestamps.iloc[i], 'duration': time_diff, 'missing_klines': missing_klines, 'severity': self._calculate_gap_severity(time_diff, expected_interval) }
                    gaps.append(gap_info)
            return gaps
        except Exception as e:
            self.logger.error(f"Error analyzing gaps for {symbol}_{interval}: {e}")
            return []
    def _get_expected_interval(self, interval: str) -> pd.Timedelta:
        """Get expected interval for the timeframe."""
        interval_map = { '1m': pd.Timedelta(minutes=1), '3m': pd.Timedelta(minutes=3), '15m': pd.Timedelta(minutes=15), '1h': pd.Timedelta(hours=1), '4h': pd.Timedelta(hours=4), 'D': pd.Timedelta(days=1) }
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
    async def _load_symbol_data(self, symbol: str, interval: str) -> pd.DataFrame:
        """Load symbol data from Redis or file cache with async performance."""
        try:
            # Try Redis first
            redis_key = f"klines:{symbol}:{interval}"
            redis_data = await self.redis_client.get(redis_key)
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
            # Fallback to async file cache
            cache_file = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
            if cache_file.exists():
                async with aiofiles.open(cache_file, 'r') as f:
                    content = await f.read()
                    data = json.loads(content)
                    if isinstance(data, dict) and 'data' in data:
                        df = pd.DataFrame(data['data'])
                    elif isinstance(data, list):
                        df = pd.DataFrame(data)
                    else:
                        df = pd.DataFrame()
                    if not df.empty and 'timestamp' in df.columns:
                        return df
            return pd.DataFrame()
        except Exception as e:
            self.logger.error(f"Error loading data for {symbol}_{interval}: {e}")
            return pd.DataFrame()
    async def fill_all_gaps(self, gaps_by_symbol: Dict):
        """Fill all identified gaps using backups and API calls with maximum async performance."""
        self.logger.info("🔧 Starting comprehensive gap filling process...")
        total_symbols = len(gaps_by_symbol)
        # Run multiple cycles until no gaps remain (for continuous mode)
        max_cycles = 5 if self.continuous_mode else 1
        cycle = 0
        while cycle < max_cycles:
            cycle += 1
            self.logger.info(f"🔄 Starting gap filling cycle {cycle}/{max_cycles}")
            # Process all symbols concurrently with maximum performance
            async def process_symbol(symbol, symbol_gaps):
                async with self.symbol_semaphore:
                    try:
                        # Process all intervals for this symbol concurrently
                        async def process_interval(interval, gaps):
                            async with self.gap_semaphore:
                                await self._fill_symbol_interval_gaps(symbol, interval, gaps)
                        # Create tasks for all intervals
                        interval_tasks = [process_interval(interval, gaps) for interval, gaps in symbol_gaps.items()]
                        if interval_tasks:
                            await asyncio.gather(*interval_tasks, return_exceptions=True)
                        self.stats['symbols_processed'] += 1
                    except Exception as e:
                        self.logger.error(f"Error processing {symbol}: {e}")
                        self.stats['errors'] += 1
            # Create tasks for all symbols
            tasks = [process_symbol(symbol, symbol_gaps) for symbol, symbol_gaps in gaps_by_symbol.items()]
            # Execute all tasks concurrently
            await asyncio.gather(*tasks, return_exceptions=True)
            # Re-analyze gaps to see if any remain
            if cycle < max_cycles:
                self.logger.info(f"🔍 Re-analyzing gaps after cycle {cycle}...")
                remaining_gaps = await self.analyze_all_gaps()
                if not remaining_gaps:
                    self.logger.info(f"✅ No gaps remain after cycle {cycle} - gap filling complete!")
                    break
                else:
                    remaining_count = sum(len(gaps) for gaps in remaining_gaps.values())
                    self.logger.info(f"🔄 {remaining_count} gaps still remain - continuing with cycle {cycle + 1}")
                    gaps_by_symbol = remaining_gaps
        self.logger.info(f"✅ Gap filling completed after {cycle} cycles for {total_symbols} symbols")
    async def _fill_symbol_interval_gaps(self, symbol: str, interval: str, gaps: List[Dict]):
        """Fill gaps for a specific symbol and interval."""
        try:
            # First try to restore from backup
            if await self._try_backup_restoration(symbol, interval):
                self.stats['backup_restorations'] += 1
                self.logger.info(f"✅ Restored {symbol}_{interval} from backup")
                return
            # If no backup available, try API calls for recent gaps
            recent_gaps = [g for g in gaps if self._is_recent_gap(g)]
            if recent_gaps:
                await self._fill_gaps_with_api(symbol, interval, recent_gaps)
        except Exception as e:
            self.logger.error(f"Error filling gaps for {symbol}_{interval}: {e}")
            self.stats['errors'] += 1
    async def _try_backup_restoration(self, symbol: str, interval: str) -> bool:
        """Try to restore data from backup - optimized for speed."""
        try:
            loop = asyncio.get_running_loop()
            # First try consolidated multi-part backups
            if getattr(self, "consolidated_dir", None):
                part_files = sorted(self.consolidated_dir.glob(f"{symbol}_{interval}_part*.json"))
                if part_files:
                    frames = []
                    for part in part_files:
                        payload = await loop.run_in_executor(None, load_json_file, part)
                        if not payload:
                            continue
                        if isinstance(payload, dict) and 'data' in payload:
                            frames.append(pd.DataFrame(payload['data']))
                        elif isinstance(payload, list):
                            frames.append(pd.DataFrame(payload))
                    if frames:
                        df = pd.concat(frames, ignore_index=True)
                        df = clean_kline_data(df)
                        await self._save_symbol_data(symbol, interval, df)
                        return True
            # Fallback to legacy backup directories
            backup_dir = Path("/Volumes/SSD2T/backup/klines_cache")
            if backup_dir.exists():
                backup_folders = [f for f in backup_dir.glob(f"{symbol}_backup_*") if f.is_dir()]
                if backup_folders:
                    latest_backup = max(backup_folders, key=lambda p: p.stat().st_mtime)
                    backup_file = latest_backup / f"{symbol}_{interval}.json"
                    if backup_file.exists():
                        backup_data = await loop.run_in_executor(None, self._load_backup_file, backup_file)
                        if backup_data:
                            df = await loop.run_in_executor(None, self._process_backup_data, backup_data)
                            if not df.empty:
                                await self._save_symbol_data(symbol, interval, df)
                                return True
        except Exception as e:
            self.logger.error(f"Error in backup restoration for {symbol}_{interval}: {e}")
            return False
    def _load_backup_file(self, backup_file: Path) -> Optional[Dict]:
        """Load backup file synchronously."""
        try:
            with open(backup_file, 'r') as f:
                return json.load(f)
        except Exception:
            return None
    def _process_backup_data(self, backup_data: Dict) -> pd.DataFrame:
        """Process backup data synchronously."""
        try:
            if isinstance(backup_data, dict) and 'data' in backup_data:
                df = pd.DataFrame(backup_data['data'])
            elif isinstance(backup_data, list):
                df = pd.DataFrame(backup_data)
            else:
                return pd.DataFrame()
            if df.empty:
                return df
            # Clean data
            df = clean_kline_data(df)
            return df
        except Exception:
            return pd.DataFrame()
    def _is_recent_gap(self, gap: Dict) -> bool:
        """Check if gap is recent enough to fill with API calls."""
        gap_start = gap['start_time']
        if isinstance(gap_start, str):
            gap_start = pd.to_datetime(gap_start, utc=True)
        days_ago = (datetime.now(timezone.utc) - gap_start).days
        return days_ago <= self.max_gap_days
    async def _fill_gaps_with_api(self, symbol: str, interval: str, gaps: List[Dict]):
        """Fill gaps using API calls with maximum async performance and rate limiting."""
        try:
            # Process gaps in parallel with maximum concurrency
            async def fill_single_gap(gap):
                async with self.api_semaphore:
                    await self._rate_limit()
                    await self._fill_single_gap_with_api(symbol, interval, gap)
                    self.stats['gaps_filled'] += 1
                    self.stats['api_calls_made'] += 1
            # Create tasks for all gaps
            tasks = [fill_single_gap(gap) for gap in gaps]
            # Execute all tasks concurrently with maximum performance
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            self.logger.error(f"Error filling gaps with API for {symbol}_{interval}: {e}")
            self.stats['errors'] += 1
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
            params = { 'symbol': symbol, 'interval': interval, 'startTime': start_ms, 'endTime': end_ms, 'limit': 1000 }
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        # Convert to DataFrame
                        df = pd.DataFrame(data, columns=[ 'timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore' ])
                        # Clean and process data
                        df = clean_kline_data(df)
                        if not df.empty:
                            # Merge with existing data
                            await self._merge_gap_data(symbol, interval, df)
        except Exception as e:
            self.logger.error(f"Error filling single gap for {symbol}_{interval}: {e}")
            self.stats['errors'] += 1
    async def _merge_gap_data(self, symbol: str, interval: str, new_df: pd.DataFrame):
        """Merge new gap data with existing data."""
        try:
            # Load existing data
            existing_df = await self._load_symbol_data(symbol, interval)
            if existing_df.empty:
                # No existing data, just save new data
                await self._save_symbol_data(symbol, interval, new_df)
            else:
                # Ensure both DataFrames have consistent timestamp format
                existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'], utc=True)
                new_df['timestamp'] = pd.to_datetime(new_df['timestamp'], utc=True)
                # Merge data
                combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                combined_df = combined_df.drop_duplicates(subset=['timestamp'], keep='last')
                combined_df = combined_df.sort_values('timestamp').reset_index(drop=True)
                # Save merged data
                await self._save_symbol_data(symbol, interval, combined_df)
        except Exception as e:
            self.logger.error(f"Error merging gap data for {symbol}_{interval}: {e}")
            self.stats['errors'] += 1
    async def _save_symbol_data(self, symbol: str, interval: str, df: pd.DataFrame):
        """Save symbol data to both file and Redis with async performance."""
        try:
            # Save to file with async I/O
            cache_file = config.KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
            # Prepare data for saving in thread pool
            def prepare_data():
                df_clean = df.copy()
                # Convert timestamps to strings
                if 'timestamp' in df_clean.columns:
                    df_clean['timestamp'] = df_clean['timestamp'].astype(str)
                # Ensure all numeric values are proper types
                numeric_columns = ['open', 'high', 'low', 'close', 'volume']
                for col in numeric_columns:
                    if col in df_clean.columns:
                        df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce').fillna(0)
                return { 'symbol': symbol, 'interval': interval, 'data': df_clean.to_dict('records'), 'last_updated': datetime.now(timezone.utc).isoformat(), 'count': len(df_clean) }
            # Prepare data in thread pool
            loop = asyncio.get_running_loop()
            data_to_save = await loop.run_in_executor(self.executor, prepare_data)
            # Async atomic write
            temp_file = cache_file.with_suffix('.tmp')
            async with aiofiles.open(temp_file, 'w') as f:
                await f.write(json.dumps(data_to_save, indent=2, default=str, ensure_ascii=False))
            # Atomic move
            await aio_os.rename(temp_file, cache_file)
            # Save to Redis with async performance
            redis_key = f"klines:{symbol}:{interval}"
            # Prepare Redis data in thread pool
            def prepare_redis_data():
                redis_klines = []
                for _, row in df.iterrows():
                    # Ensure timestamp is properly formatted
                    timestamp = row['timestamp']
                    if hasattr(timestamp, 'strftime'):
                        timestamp_str = timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    else:
                        timestamp_str = str(timestamp)
                    redis_klines.append({ 'timestamp': timestamp_str, 'open': float(row['open']), 'high': float(row['high']), 'low': float(row['low']), 'close': float(row['close']), 'volume': float(row['volume']) })
                return { "symbol": symbol, "interval": interval, "published_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), "klines": redis_klines, "count": len(redis_klines), "last_timestamp": redis_klines[-1]['timestamp'] if redis_klines else None, "source": "gap_filler" }
            # Prepare Redis data in thread pool
            redis_payload = await loop.run_in_executor(self.executor, prepare_redis_data)
            if redis_payload['klines']:
                await self.redis_client.setex(redis_key, 300, json.dumps(redis_payload))  # 5 minute expiry
        except Exception as e:
            self.logger.error(f"Error saving data for {symbol}_{interval}: {e}")
            self.stats['errors'] += 1
    def print_stats(self):
        """Print statistics."""
        if self.stats['start_time']:
            elapsed = time.time() - self.stats['start_time']
            self.logger.info(f"📊 Gap Filler Statistics:")
            self.logger.info(f"   Runtime: {elapsed:.1f} seconds")
            self.logger.info(f"   Symbols Processed: {self.stats['symbols_processed']}")
            self.logger.info(f"   Data Points Processed: {self.stats['data_points_processed']:,}")
            self.logger.info(f"   Gaps Filled: {self.stats['gaps_filled']}")
            self.logger.info(f"   API Calls Made: {self.stats['api_calls_made']}")
            self.logger.info(f"   Backup Restorations: {self.stats['backup_restorations']}")
            self.logger.info(f"   Errors: {self.stats['errors']}")
            if elapsed > 0:
                throughput = self.stats['data_points_processed'] / elapsed
                self.logger.info(f"   Throughput: {throughput:,.0f} data points/second")
    async def cleanup(self):
        """Cleanup resources."""
        if self.session:
            await self.session.close()
        if self.redis_client:
            await self.redis_client.aclose()

async def main():
    """Main function."""
    gap_filler = DedicatedGapFiller()
    try:
        await gap_filler.initialize()
        # Analyze all gaps
        gaps_by_symbol = await gap_filler.analyze_all_gaps()
        if not gaps_by_symbol:
            gap_filler.logger.info("✅ No gaps found - data is complete!")
            return
        # Fill all gaps
        await gap_filler.fill_all_gaps(gaps_by_symbol)
        # Print statistics
        gap_filler.print_stats()
    except Exception as e:
        gap_filler.logger.error(f"Gap filler failed: {e}")
    finally:
        await gap_filler.cleanup()

if __name__ == "__main__":
    asyncio.run(main())

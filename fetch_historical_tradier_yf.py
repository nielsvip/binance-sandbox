#!/home/niels/.conda/envs/binance_env/bin/python3
"""Fetch maximum historical 15m bars for Tradier stock symbols from Yahoo Finance.
yfinance gives ~60 days of 15m data (free tier). Merges with existing cache without clipping
so backtesting retains full history. Safe to run at startup — writes directly to klines cache."""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import pytz
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from config_tradier import TradierConfig

logger = logging.getLogger("yf_historical")
ET = pytz.timezone('America/New_York')

def _yf_to_bars(df: pd.DataFrame) -> list:
    """Convert yfinance DataFrame (ET index) to tradier cache bar format."""
    bars = []
    for ts_et, row in df.iterrows():
        h, m = ts_et.hour, ts_et.minute
        if (h, m) < (9, 30) or (h, m) >= (16, 0):
            continue
        ts_utc = ts_et.tz_convert('UTC')
        bars.append({'time': ts_et.strftime('%Y-%m-%dT%H:%M:%S'), 'timestamp': ts_utc.strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z', 'open': round(float(row['Open']), 6), 'high': round(float(row['High']), 6), 'low': round(float(row['Low']), 6), 'close': round(float(row['Close']), 6), 'volume': int(row['Volume']) if not pd.isna(row['Volume']) else 0})
    return bars

def _fetch_yf_sync(symbol: str) -> list:
    import yfinance as yf
    try:
        df = yf.Ticker(symbol).history(period='60d', interval='15m', auto_adjust=True, prepost=False)
        if df.empty:
            return []
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize(ET)
        return _yf_to_bars(df)
    except Exception as e:
        logger.debug(f"yfinance error for {symbol}: {e}")
        return []

def _merge_bars(existing: list, new_bars: list) -> list:
    """Merge by timestamp — new_bars win on collision."""
    seen = {}
    for bar in existing:
        ts = bar.get('timestamp')
        if ts:
            seen[ts] = bar
    for bar in new_bars:
        ts = bar.get('timestamp')
        if ts:
            seen[ts] = bar
    return sorted(seen.values(), key=lambda b: b['timestamp'])

def _read_cache(path: Path) -> list:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return []

def _write_cache(path: Path, bars: list):
    if not bars:
        return
    tmp = path.with_name(f'.{path.name}.yftmp')
    try:
        with open(tmp, 'w') as f:
            json.dump(bars, f)
        os.replace(tmp, path)
    except Exception as e:
        logger.error(f"Write error {path.name}: {e}")
        try: tmp.unlink()
        except Exception: pass

async def fetch_all_yfinance_historical(cache_dir: Path = None, symbols_file: Path = None):
    """Fetch and merge yfinance 15m historical data for all tradier symbols. Run as background task."""
    cfg = TradierConfig()
    if cache_dir is None:
        cache_dir = cfg.KLINES_CACHE_DIR
    if symbols_file is None:
        symbols_file = cfg.BASE_PATH / 'symbols_tradier.json'
    try:
        with open(symbols_file, 'r') as f:
            symbols = json.load(f)
    except Exception as e:
        logger.error(f"yf_historical: could not load symbols: {e}")
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"yf_historical: fetching 15m history for {len(symbols)} symbols from Yahoo Finance...")
    total_added = 0
    for i, symbol in enumerate(symbols):
        try:
            new_bars = await asyncio.to_thread(_fetch_yf_sync, symbol)
            if not new_bars:
                await asyncio.sleep(0.3)
                continue
            path = cache_dir / f"{symbol}_15m.json"
            existing = _read_cache(path)
            merged = _merge_bars(existing, new_bars)
            added = len(merged) - len(existing)
            if added > 0:
                _write_cache(path, merged)
                logger.info(f"yf_historical [{i+1}/{len(symbols)}] {symbol} 15m: +{added} bars ({len(merged)} total)")
            total_added += max(added, 0)
            await asyncio.sleep(0.25)
        except Exception as e:
            logger.warning(f"yf_historical [{i+1}/{len(symbols)}] {symbol}: {e}")
    logger.info(f"yf_historical: done — +{total_added} bars added across {len(symbols)} symbols")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(fetch_all_yfinance_historical())

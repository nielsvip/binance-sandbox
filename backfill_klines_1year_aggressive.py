#!/usr/bin/env python3
"""
Aggressive 1-year kline backfill for S1
Fetches full 365 days of history for 15m+ timeframes, ignoring existing data
Designed for initial setup on new server with proper rate limiting
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import aiohttp
import pandas as pd

BASE_PATH = Path('/home/niels/binance-sandbox')
KLINES_DIR = BASE_PATH / 'klines_cache'
KLINES_DIR.mkdir(exist_ok=True)

MAX_CONCURRENT = 5
API_MAX_PER_SECOND = 2
API_MAX_PER_MINUTE = 50

TIMEFRAMES = {
    '15m': 15*60*1000,
    '1h': 60*60*1000,
    '4h': 4*60*60*1000,
    'D': 24*60*60*1000,
}

BARS_PER_REQUEST = 1000
BACKFILL_DAYS = 365

_throttle = {'tokens_sec': API_MAX_PER_SECOND, 'tokens_min': API_MAX_PER_MINUTE, 'last_reset_sec': 0, 'last_reset_min': 0}

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)

def get_active_symbols():
    """Load active trading symbols from symbols.json"""
    try:
        with open(BASE_PATH / 'symbols.json') as f:
            return json.load(f)
    except:
        log("❌ Could not load symbols.json")
        return []

async def throttle():
    """Respect API rate limits"""
    now = time.time()
    if now - _throttle['last_reset_sec'] >= 1:
        _throttle['tokens_sec'] = API_MAX_PER_SECOND
        _throttle['last_reset_sec'] = now
    if now - _throttle['last_reset_min'] >= 60:
        _throttle['tokens_min'] = API_MAX_PER_MINUTE
        _throttle['last_reset_min'] = now

    if _throttle['tokens_sec'] <= 0 or _throttle['tokens_min'] <= 0:
        await asyncio.sleep(0.5)
        return await throttle()

    _throttle['tokens_sec'] -= 1
    _throttle['tokens_min'] -= 1

async def fetch_klines(symbol, interval, start_time, end_time, session, semaphore):
    """Fetch klines from Binance API"""
    await throttle()
    async with semaphore:
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {
            'symbol': symbol,
            'interval': interval,
            'startTime': int(start_time * 1000),
            'endTime': int(end_time * 1000),
            'limit': BARS_PER_REQUEST,
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        return [(row[0], {
                            'timestamp': datetime.fromtimestamp(row[0]/1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                            'open': float(row[1]),
                            'high': float(row[2]),
                            'low': float(row[3]),
                            'close': float(row[4]),
                            'volume': float(row[7])
                        }) for row in data]
                    return []
                elif resp.status == 429:
                    log(f"⚠️  Rate limited, backing off...")
                    await asyncio.sleep(5)
                    return []
                else:
                    return []
        except asyncio.TimeoutError:
            return []
        except Exception as e:
            log(f"  Error fetching {symbol} {interval}: {e}")
            return []

async def backfill_symbol_timeframe(symbol, interval, session, semaphore):
    """Fetch full 1-year history for a symbol/timeframe"""
    interval_ms = TIMEFRAMES[interval]
    end_time = datetime.now(timezone.utc).timestamp()
    start_time = end_time - (BACKFILL_DAYS * 24 * 3600)

    file_path = KLINES_DIR / f"{symbol}_{interval}.json"
    all_bars = []
    pages_fetched = 0

    # Fetch in BARS_PER_REQUEST chunks from start_time to end_time
    current_start = start_time
    while current_start < end_time:
        current_end = min(current_start + (BARS_PER_REQUEST * interval_ms / 1000), end_time)
        bars = await fetch_klines(symbol, interval, current_start, current_end, session, semaphore)

        if not bars:
            break

        all_bars.extend(bars)
        current_start = current_end
        pages_fetched += 1

        if pages_fetched % 10 == 0:
            log(f"  {symbol} {interval}: fetched {pages_fetched} pages ({len(all_bars)} bars)")

    if all_bars:
        # Deduplicate and sort
        all_bars.sort(key=lambda x: x[0])
        seen = set()
        unique_bars = []
        for ts, bar in all_bars:
            if ts not in seen:
                seen.add(ts)
                unique_bars.append(bar)

        # Write to file
        try:
            tmp_path = f"{file_path}.tmp"
            with open(tmp_path, 'w') as f:
                json.dump(unique_bars, f, indent=2)
            os.replace(tmp_path, file_path)
            days_coverage = (unique_bars[-1]['timestamp'][:10] != unique_bars[0]['timestamp'][:10])
            return len(unique_bars), pages_fetched
        except Exception as e:
            log(f"  ❌ Error writing {symbol} {interval}: {e}")
            return 0, pages_fetched

    return 0, pages_fetched

async def main():
    log(f"🔄 Starting aggressive 1-year backfill to {KLINES_DIR}")
    log(f"   Backfill period: {BACKFILL_DAYS} days")
    log(f"   Timeframes: {', '.join(TIMEFRAMES.keys())}")
    log("")

    symbols = get_active_symbols()
    if not symbols:
        log("❌ No symbols found")
        return

    log(f"📊 Fetching {len(symbols)} symbols x {len(TIMEFRAMES)} timeframes")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT, limit=MAX_CONCURRENT)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for timeframe in TIMEFRAMES.keys():
            log(f"\n📥 Starting {timeframe} backfill...")
            total_bars = 0
            total_pages = 0

            for i, symbol in enumerate(symbols):
                task = backfill_symbol_timeframe(symbol, timeframe, session, semaphore)
                tasks.append((symbol, timeframe, task))

                if (i + 1) % 20 == 0 or (i + 1) == len(symbols):
                    results = await asyncio.gather(*[t for _, _, t in tasks[-20:]])
                    for (sym, tf, _), (bars, pages) in zip(tasks[-20:], results):
                        if bars > 0:
                            total_bars += bars
                            total_pages += pages
                            log(f"  ✅ {sym:15s} {tf:4s}: {bars:5d} bars ({pages:3d} pages)")

            log(f"✅ {timeframe} complete: {total_bars:,} total bars added")

    log("")
    log("✅ Backfill complete!")
    log("")
    log("Verification:")
    for tf in ['15m', '1h', '4h', 'D']:
        files = list(KLINES_DIR.glob(f'*_{tf}.json'))
        coverages = []
        for f in files[:10]:
            try:
                with open(f) as fp:
                    data = json.load(fp)
                    if len(data) > 1:
                        first = datetime.fromisoformat(data[0]['timestamp'].replace('Z', '+00:00'))
                        last = datetime.fromisoformat(data[-1]['timestamp'].replace('Z', '+00:00'))
                        days = (last - first).days
                        coverages.append(days)
            except:
                pass
        if coverages:
            avg = sum(coverages) / len(coverages)
            log(f"  {tf}: {len(files)} files, avg {avg:.0f} days coverage")

if __name__ == '__main__':
    asyncio.run(main())

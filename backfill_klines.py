#!/usr/bin/env python3
"""
Backfill klines for ALL symbols × ALL timeframes to maximum API depth.
Paginates backwards from now until no more data is returned.
Merges with existing data — NEVER overwrites or clips.

Standalone — no config.py dependency. Paths hardcoded for server.

Usage: python3 backfill_klines.py [--tf 3m] [--workers 8] [--days 365]
       python3 backfill_klines.py --tf all --days 365   # backfill everything
"""
import json, os, sys, time, argparse, asyncio, aiohttp
from pathlib import Path
from datetime import datetime, timezone, timedelta

KLINES_DIR = Path("/home/niels/binance/klines_cache")
SYMBOLS_FILE = Path("/home/niels/binance-sandbox/symbols.json")
API_URL = "https://fapi.binance.com/fapi/v1/klines"
MAX_LIMIT = 1500
RATE_LIMIT_DELAY = 0.12
BINANCE_TF_MAP = {"D": "1d", "W": "1w", "M": "1M"}
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "D": 1440}


def load_existing(symbol, tf):
    p = KLINES_DIR / f"{symbol}_{tf}.json"
    if not p.exists():
        return []
    try:
        with open(p) as f:
            return json.load(f)
    except:
        return []


def save_klines(symbol, tf, data):
    p = KLINES_DIR / f"{symbol}_{tf}.json"
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    tmp.replace(p)


def merge_klines(existing, new_bars):
    by_ts = {}
    for bar in existing:
        by_ts[bar["timestamp"]] = bar
    for bar in new_bars:
        by_ts[bar["timestamp"]] = bar
    return sorted(by_ts.values(), key=lambda x: x["timestamp"])


async def fetch_page(session, symbol, tf, end_time=None):
    binance_tf = BINANCE_TF_MAP.get(tf, tf)
    params = {"symbol": symbol, "interval": binance_tf, "limit": MAX_LIMIT}
    if end_time:
        params["endTime"] = end_time
    await asyncio.sleep(RATE_LIMIT_DELAY)
    try:
        async with session.get(API_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                raw = await resp.json()
                return [{"timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in raw]
            elif resp.status == 429:
                await asyncio.sleep(5)
                return None
            return None
    except:
        return None


async def backfill_one(session, symbol, tf, max_days):
    existing = load_existing(symbol, tf)
    existing_count = len(existing)
    all_new = []
    end_time = None
    min_ts = int((datetime.now(timezone.utc) - timedelta(days=max_days)).timestamp() * 1000)
    pages = 0
    while True:
        bars = await fetch_page(session, symbol, tf, end_time)
        if not bars:
            break
        pages += 1
        all_new.extend(bars)
        first_ms = int(datetime.fromisoformat(bars[0]["timestamp"].replace("Z", "+00:00")).timestamp() * 1000)
        if first_ms <= min_ts or len(bars) < MAX_LIMIT or pages > 500:
            break
        end_time = first_ms - 1
    if not all_new:
        return existing_count, 0
    merged = merge_klines(existing, all_new)
    save_klines(symbol, tf, merged)
    return len(merged), len(merged) - existing_count


async def backfill_tf(symbols, tf, max_days, max_concurrent=8):
    sem = asyncio.Semaphore(max_concurrent)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=max_concurrent)) as session:
        async def worker(s):
            async with sem:
                return s, await backfill_one(session, s, tf, max_days)
        results = await asyncio.gather(*[worker(s) for s in symbols], return_exceptions=True)
        total_added = 0
        for r in results:
            if isinstance(r, Exception):
                continue
            sym, (total, added) = r
            if added > 0:
                print(f"  {sym}_{tf}: {total} bars (+{added} new)")
                total_added += added
        return total_added


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", default="all")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    symbols = json.load(open(SYMBOLS_FILE))
    print(f"Backfilling {len(symbols)} symbols, max {args.days} days, klines_dir={KLINES_DIR}")
    tfs = ["3m", "5m", "15m", "1h", "4h", "D"] if args.tf == "all" else [args.tf]
    for tf in tfs:
        print(f"\n=== {tf} ({len(symbols)} symbols) ===")
        t0 = time.time()
        added = asyncio.run(backfill_tf(symbols, tf, args.days, args.workers))
        print(f"  {tf} done: +{added} new bars in {time.time() - t0:.1f}s")
    print("\nBackfill complete!")


if __name__ == "__main__":
    main()

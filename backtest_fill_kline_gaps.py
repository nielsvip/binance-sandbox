#!/usr/bin/env python3
"""
Fill gaps in 15m kline data for ALL backtest symbols.
Fetches missing bars from Binance API and merges into existing JSON files.

Run on server (has binance IP):
    python3 backtest_fill_kline_gaps.py

Then after filling:
    python3 backtest_fill_kline_gaps.py --verify
"""
import asyncio
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp

IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE = Path("/home/niels/binance-sandbox")
else:
    BASE = Path("/Users/niels/Documents/binance")

CACHE_DIR = BASE / "klines_cache"
BINANCE_API = "https://fapi.binance.com"
INTERVAL = "15m"
INTERVAL_MS = 15 * 60 * 1000
MAX_BARS_PER_REQUEST = 1500
RATE_LIMIT_DELAY = 0.5  # seconds between API calls


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def load_klines(symbol: str) -> list:
    path = CACHE_DIR / f"{symbol}_{INTERVAL}.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def save_klines(symbol: str, data: list):
    path = CACHE_DIR / f"{symbol}_{INTERVAL}.json"
    # Backup first
    backup = CACHE_DIR / f".{symbol}_{INTERVAL}.json.bak"
    if path.exists():
        path.rename(backup)
    with open(path, "w") as f:
        json.dump(data, f)
    log(f"  Saved {len(data):,} bars to {path.name}")


def get_timestamp_ms(bar) -> int:
    if isinstance(bar, dict):
        ts_str = bar.get("timestamp", "")
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    elif isinstance(bar, list):
        return int(bar[0])
    return 0


def find_gaps(data: list) -> list:
    """Find gaps in kline data. Returns list of (start_ms, end_ms, missing_bars)."""
    if len(data) < 2:
        return []
    gaps = []
    for i in range(1, len(data)):
        ts_prev = get_timestamp_ms(data[i - 1])
        ts_curr = get_timestamp_ms(data[i])
        diff_ms = ts_curr - ts_prev
        if diff_ms > INTERVAL_MS * 1.5:  # gap detected
            missing = int(diff_ms / INTERVAL_MS) - 1
            gaps.append((ts_prev + INTERVAL_MS, ts_curr - INTERVAL_MS, missing))
    return gaps


async def fetch_klines_range(session: aiohttp.ClientSession, symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch klines from Binance API for a time range. Handles pagination."""
    all_bars = []
    current_start = start_ms

    while current_start <= end_ms:
        url = f"{BINANCE_API}/fapi/v1/klines"
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": MAX_BARS_PER_REQUEST,
        }
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 429:
                    log(f"  Rate limited! Waiting 60s...")
                    await asyncio.sleep(60)
                    continue
                if resp.status != 200:
                    log(f"  API error {resp.status} for {symbol}")
                    break
                raw = await resp.json()
                if not raw:
                    break
                for bar in raw:
                    all_bars.append({
                        "timestamp": datetime.utcfromtimestamp(bar[0] / 1000).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
                        "open": float(bar[1]),
                        "high": float(bar[2]),
                        "low": float(bar[3]),
                        "close": float(bar[4]),
                        "volume": float(bar[5]),
                    })
                last_ts = int(raw[-1][0])
                if last_ts >= end_ms or len(raw) < MAX_BARS_PER_REQUEST:
                    break
                current_start = last_ts + INTERVAL_MS
                await asyncio.sleep(RATE_LIMIT_DELAY)
        except Exception as e:
            log(f"  Fetch error: {e}")
            break

    return all_bars


def merge_klines(existing: list, new_bars: list) -> list:
    """Merge new bars into existing data, sort by timestamp, deduplicate."""
    combined = existing + new_bars
    # Sort by timestamp
    combined.sort(key=lambda b: get_timestamp_ms(b))
    # Deduplicate by timestamp
    seen = set()
    result = []
    for bar in combined:
        ts = get_timestamp_ms(bar)
        if ts not in seen:
            seen.add(ts)
            result.append(bar)
    return result


async def fill_gaps_for_symbol(session: aiohttp.ClientSession, symbol: str) -> int:
    """Fill all gaps for one symbol. Returns number of bars fetched."""
    data = load_klines(symbol)
    if not data:
        log(f"  {symbol}: No existing data — skipping (need initial fetch)")
        return 0

    gaps = find_gaps(data)
    if not gaps:
        return 0

    total_missing = sum(g[2] for g in gaps)
    log(f"  {symbol}: {len(gaps)} gaps, {total_missing:,} missing bars")

    total_fetched = 0
    for start_ms, end_ms, n_missing in gaps:
        start_dt = datetime.utcfromtimestamp(start_ms / 1000).strftime("%Y-%m-%d")
        log(f"    Filling {n_missing:,} bars from {start_dt}...")
        new_bars = await fetch_klines_range(session, symbol, start_ms, end_ms)
        if new_bars:
            data = merge_klines(data, new_bars)
            total_fetched += len(new_bars)
            log(f"    Got {len(new_bars)} bars")
        await asyncio.sleep(RATE_LIMIT_DELAY)

    if total_fetched > 0:
        save_klines(symbol, data)

    return total_fetched


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Just verify, don't fetch")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols (empty=all)")
    args = parser.parse_args()

    bt_path = BASE / "backtest_48_symbols.json"
    if bt_path.exists():
        with open(bt_path) as f:
            symbols = json.load(f)
    else:
        symbols = sorted(p.stem.rsplit("_", 1)[0] for p in CACHE_DIR.glob(f"*_{INTERVAL}.json"))

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]

    log(f"Checking {len(symbols)} symbols for 15m gaps...")

    # First pass: identify gaps
    total_gaps = 0
    symbols_with_gaps = []
    for sym in sorted(symbols):
        data = load_klines(sym)
        if not data:
            log(f"  {sym}: NO DATA")
            continue
        gaps = find_gaps(data)
        if gaps:
            n_missing = sum(g[2] for g in gaps)
            total_gaps += n_missing
            symbols_with_gaps.append((sym, len(gaps), n_missing))
            start_dt = datetime.utcfromtimestamp(get_timestamp_ms(data[0]) / 1000).strftime("%Y-%m-%d")
            end_dt = datetime.utcfromtimestamp(get_timestamp_ms(data[-1]) / 1000).strftime("%Y-%m-%d")
            log(f"  {sym}: {len(data):>7,} bars {start_dt}→{end_dt} | {len(gaps)} gaps ({n_missing:,} missing)")
        else:
            start_dt = datetime.utcfromtimestamp(get_timestamp_ms(data[0]) / 1000).strftime("%Y-%m-%d")
            end_dt = datetime.utcfromtimestamp(get_timestamp_ms(data[-1]) / 1000).strftime("%Y-%m-%d")
            log(f"  {sym}: {len(data):>7,} bars {start_dt}→{end_dt} | CONTINUOUS ✓")

    log(f"\nTotal: {total_gaps:,} missing bars across {len(symbols_with_gaps)} symbols")

    if args.verify:
        return

    if total_gaps == 0:
        log("All klines continuous — nothing to fill!")
        return

    # Second pass: fill gaps
    log(f"\nFilling gaps for {len(symbols_with_gaps)} symbols...")
    async with aiohttp.ClientSession() as session:
        total_fetched = 0
        for sym, n_gaps, n_missing in symbols_with_gaps:
            fetched = await fill_gaps_for_symbol(session, sym)
            total_fetched += fetched

    log(f"\nDone! Fetched {total_fetched:,} bars total")

    # Verify
    log("\nPost-fill verification:")
    remaining_gaps = 0
    for sym, _, _ in symbols_with_gaps:
        data = load_klines(sym)
        gaps = find_gaps(data)
        if gaps:
            remaining_gaps += sum(g[2] for g in gaps)
            log(f"  {sym}: STILL HAS {len(gaps)} gaps ({sum(g[2] for g in gaps):,} bars)")
        else:
            log(f"  {sym}: FIXED ✓")
    log(f"Remaining gaps: {remaining_gaps:,} bars")


if __name__ == "__main__":
    asyncio.run(main())

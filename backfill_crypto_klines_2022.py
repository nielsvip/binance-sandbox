#!/usr/bin/env python3
"""
backfill_crypto_klines_2022.py — Fetch 2020-2024 historical klines for crypto symbols
that are missing from klines_cache_backtest. Runs on S1 (not Mac — different IP).

Paginates BACKWARDS from the earliest bar in each existing JSON file until
Binance returns no more data or we reach BACKFILL_STOP_DATE.

Usage: python backfill_crypto_klines_2022.py [--symbols SYM1,SYM2,...] [--interval 15m] [--dry-run]
"""
import asyncio, aiohttp, json, os, sys, argparse, datetime
from pathlib import Path
from dateutil.parser import isoparse

FAPI_URL = "https://fapi.binance.com/fapi/v1/klines"
BACKFILL_STOP_DATE = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
LIMIT = 1500
MAX_CONCURRENT = 1  # one at a time — avoid 418 rate ban

# Canonical 14 symbols used in v8_vec_sweep GR exit tests
DEFAULT_SYMBOLS = [
    "BTCUSDC", "ETHUSDC", "SOLUSDC", "ADAUSDC", "BNBUSDC", "AVAXUSDC",
    "XRPUSDC", "LINKUSDC", "LTCUSDC", "UNIUSDC",
    "DOTUSDT", "ATOMUSDT", "SANDUSDT", "MANAUSDT",
]

# USDC perp majors launched late 2023/early 2024. For pre-launch history we
# fetch from the matching USDT perpetual and save labeled as USDC — matching
# the CLAUDE.md convention "Label=USDC; source rows=USDT."
USDC_TO_USDT_FETCH = {
    "BTCUSDC": "BTCUSDT", "ETHUSDC": "ETHUSDT", "SOLUSDC": "SOLUSDT",
    "ADAUSDC": "ADAUSDT", "BNBUSDC": "BNBUSDT", "AVAXUSDC": "AVAXUSDT",
    "XRPUSDC": "XRPUSDT", "LINKUSDC": "LINKUSDT", "LTCUSDC": "LTCUSDT",
    "UNIUSDC": "UNIUSDT",
}

INTERVAL_MAP = {"15m": "15m", "3m": "3m", "1h": "1h", "4h": "4h"}

def get_klines_path(base_dir: Path, symbol: str, interval: str) -> Path:
    return base_dir / f"{symbol}_{interval}.json"

def load_existing(path: Path):
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)

def save_klines(path: Path, bars: list):
    bars.sort(key=lambda b: b["timestamp"])
    with open(path, "w") as f:
        json.dump(bars, f)

def merge(existing: list, new_bars: list) -> tuple[list, int]:
    existing_ts = {b["timestamp"] for b in existing}
    added = [b for b in new_bars if b["timestamp"] not in existing_ts]
    merged = existing + added
    merged.sort(key=lambda b: b["timestamp"])
    return merged, len(added)

def parse_bar(raw) -> dict:
    ts = datetime.datetime.fromtimestamp(raw[0] / 1000, tz=datetime.timezone.utc)
    return {
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        "open": float(raw[1]),
        "high": float(raw[2]),
        "low": float(raw[3]),
        "close": float(raw[4]),
        "volume": float(raw[5]),
    }

async def backfill_symbol(symbol: str, interval: str, base_dir: Path,
                          session: aiohttp.ClientSession, semaphore: asyncio.Semaphore,
                          dry_run: bool) -> int:
    path = get_klines_path(base_dir, symbol, interval)
    existing = load_existing(path)
    total_added = 0

    # For USDC symbols: fetch from USDT pair for pre-launch history, save as USDC label
    fetch_symbol = USDC_TO_USDT_FETCH.get(symbol, symbol)

    if existing:
        earliest_ts = min(isoparse(b["timestamp"]) for b in existing)
    else:
        earliest_ts = datetime.datetime.now(tz=datetime.timezone.utc)

    if earliest_ts <= BACKFILL_STOP_DATE:
        print(f"  {symbol} {interval}: already at {earliest_ts.date()}, nothing to backfill")
        return 0

    label = f"{symbol}" + (f" (fetch as {fetch_symbol})" if fetch_symbol != symbol else "")
    print(f"  {label} {interval}: backfilling from {earliest_ts.date()} backwards to {BACKFILL_STOP_DATE.date()}")
    page = 0
    while earliest_ts > BACKFILL_STOP_DATE:
        params = {
            "symbol": fetch_symbol,
            "interval": INTERVAL_MAP.get(interval, interval),
            "limit": LIMIT,
            "endTime": int(earliest_ts.timestamp() * 1000) - 1,
        }
        page += 1
        try:
            async with semaphore:
                async with session.get(FAPI_URL, params=params,
                                       timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 429:
                        print(f"    {symbol} rate-limited, sleeping 5s")
                        await asyncio.sleep(5)
                        continue
                    if resp.status != 200:
                        print(f"    {symbol} HTTP {resp.status}, stopping")
                        break
                    raw = await resp.json()
        except Exception as e:
            print(f"    {symbol} error: {e}, stopping")
            break

        if not raw:
            break

        new_bars = [parse_bar(r) for r in raw]
        new_earliest = min(isoparse(b["timestamp"]) for b in new_bars)

        if not dry_run:
            existing, added = merge(existing, new_bars)
            if added > 0:
                save_klines(path, existing)
            total_added += added
        else:
            added = len(new_bars)
            total_added += added

        print(f"    page {page}: fetched {len(new_bars)} bars, earliest={new_earliest.date()}, +{added} new")

        if new_earliest >= earliest_ts:
            break
        earliest_ts = new_earliest

        if earliest_ts <= BACKFILL_STOP_DATE:
            break
        await asyncio.sleep(0.25)

    print(f"  {symbol} {interval}: total +{total_added} bars added")
    return total_added

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--interval", default="15m", choices=list(INTERVAL_MAP.keys()))
    parser.add_argument("--base-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if args.base_dir:
        base_dir = Path(args.base_dir)
    else:
        # auto-detect: prefer klines_cache_backtest
        candidates = [
            Path("/home/niels/binance-sandbox/klines_cache_backtest"),
            Path("/Users/niels/Documents/binance/klines_cache_backtest"),
        ]
        base_dir = next((p for p in candidates if p.exists()), candidates[0])

    print(f"Backfill crypto klines 2022")
    print(f"  Base dir:  {base_dir}")
    print(f"  Symbols:   {symbols}")
    print(f"  Interval:  {args.interval}")
    print(f"  Stop date: {BACKFILL_STOP_DATE.date()}")
    print(f"  Dry run:   {args.dry_run}")
    print()

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            backfill_symbol(sym, args.interval, base_dir, session, semaphore, args.dry_run)
            for sym in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    print()
    print("=== Summary ===")
    grand_total = 0
    for sym, result in zip(symbols, results):
        if isinstance(result, Exception):
            print(f"  {sym}: ERROR {result}")
        else:
            grand_total += result
            print(f"  {sym}: +{result} bars")
    print(f"  TOTAL: +{grand_total} bars added")

if __name__ == "__main__":
    asyncio.run(main())

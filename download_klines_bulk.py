#!/usr/bin/env python3
"""Bulk download 6+ months of klines from Binance API for backtesting.
Writes to klines_cache/{SYMBOL}_{TF}.json — merges with existing data.
Usage: python download_klines_bulk.py [--tf 15m] [--months 6] [--symbols BTCUSDT,ETHUSDT]
"""
import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
import requests

if platform.system() == "Linux":
    BASE_PATH = Path("/home/niels/binance-sandbox")
else:
    BASE_PATH = Path("/Users/niels/Documents/binance")
KLINES_DIR = BASE_PATH / "klines_cache"
KLINES_DIR.mkdir(parents=True, exist_ok=True)

BINANCE_API = "https://fapi.binance.com"
BATCH_SIZE = 1500  # Binance max per request

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def get_all_symbols() -> List[str]:
    """Get all active USDT/USDC futures symbols."""
    try:
        r = requests.get(f"{BINANCE_API}/fapi/v1/exchangeInfo", timeout=10)
        data = r.json()
        symbols = []
        for s in data.get("symbols", []):
            if s.get("status") == "TRADING" and (s["symbol"].endswith("USDT") or s["symbol"].endswith("USDC")):
                symbols.append(s["symbol"])
        return sorted(symbols)
    except Exception as e:
        log(f"Error fetching symbols: {e}")
        return []

def tf_to_ms(tf: str) -> int:
    mapping = {"1m": 60000, "3m": 180000, "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "D": 86400000}
    return mapping.get(tf, 900000)

def download_klines(symbol: str, tf: str, start_ms: int, end_ms: int) -> List[dict]:
    """Download klines in batches, returns list of dicts."""
    all_klines = []
    current_start = start_ms
    tf_api = "1d" if tf == "D" else tf
    while current_start < end_ms:
        try:
            params = {"symbol": symbol, "interval": tf_api, "startTime": current_start, "endTime": end_ms, "limit": BATCH_SIZE}
            r = requests.get(f"{BINANCE_API}/fapi/v1/klines", params=params, timeout=15)
            if r.status_code == 429:
                log(f"  Rate limited, sleeping 60s...")
                time.sleep(60)
                continue
            if r.status_code == 418:
                log(f"  IP banned, sleeping 120s...")
                time.sleep(120)
                continue
            if r.status_code != 200:
                log(f"  {symbol} {tf}: HTTP {r.status_code}, retrying in 10s")
                time.sleep(10)
                retries = retries + 1 if 'retries' in dir() else 1
                if retries > 3:
                    break
                continue
            retries = 0
            data = r.json()
            if not data:
                break
            for k in data:
                all_klines.append({"timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
            if len(data) < BATCH_SIZE:
                break
            current_start = data[-1][0] + 1
            time.sleep(0.3)  # Respect rate limits
        except Exception as e:
            log(f"  {symbol} {tf}: Error {e}, retrying in 5s")
            time.sleep(5)
            retries = retries + 1 if 'retries' in dir() else 1
            if retries > 3:
                break
            continue
    return all_klines

def merge_and_save(symbol: str, tf: str, new_klines: List[dict]):
    """Merge new klines with existing file, dedup by timestamp."""
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    existing = []
    if path.exists():
        try:
            with open(path) as f:
                existing = json.load(f)
        except Exception:
            pass
    # Merge: use timestamp as key
    ts_map = {}
    for k in existing:
        ts = k.get("timestamp", "")
        ts_map[ts] = k
    for k in new_klines:
        ts = k.get("timestamp", "")
        ts_map[ts] = k
    merged = sorted(ts_map.values(), key=lambda x: x.get("timestamp", ""))
    with open(path, "w") as f:
        json.dump(merged, f)
    return len(merged)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", default="15m", help="Timeframe (15m, 1h, 4h, D)")
    parser.add_argument("--months", type=int, default=6, help="Months of history")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols (empty=all)")
    parser.add_argument("--tfs", default="", help="Comma-separated TFs to download (overrides --tf)")
    args = parser.parse_args()
    tfs = args.tfs.split(",") if args.tfs else [args.tf]
    tfs = [t.strip() for t in tfs if t.strip()]
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        log("Fetching all active symbols...")
        symbols = get_all_symbols()
    log(f"Downloading {args.months} months of {','.join(tfs)} for {len(symbols)} symbols → {KLINES_DIR}")
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=args.months * 30)).timestamp() * 1000)
    total = len(symbols) * len(tfs)
    done = 0
    for tf in tfs:
        for sym in symbols:
            done += 1
            path = KLINES_DIR / f"{sym}_{tf}.json"
            # Skip if we already have enough data
            if path.exists():
                try:
                    with open(path) as f:
                        existing = json.load(f)
                    expected_bars = args.months * 30 * 24 * 60 // max(1, tf_to_ms(tf) // 60000)
                    if len(existing) >= expected_bars * 0.8:
                        if done % 50 == 0:
                            log(f"  [{done}/{total}] {sym} {tf}: already have {len(existing)} bars, skipping")
                        continue
                except Exception:
                    pass
            klines = download_klines(sym, tf, start_ms, end_ms)
            if klines:
                total_bars = merge_and_save(sym, tf, klines)
                if done % 10 == 0 or len(klines) > 1000:
                    log(f"  [{done}/{total}] {sym} {tf}: downloaded {len(klines)} bars → {total_bars} total")
            else:
                if done % 50 == 0:
                    log(f"  [{done}/{total}] {sym} {tf}: no data")
            time.sleep(0.2)
    log(f"Done! {done} symbol-TF combinations processed.")

if __name__ == "__main__":
    main()

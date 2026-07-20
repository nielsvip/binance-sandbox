#!/usr/bin/env python3
"""Download REAL 5m stock klines from Massive.com (formerly Polygon.io).

Same API as 15m downloader but with 5/minute interval.
Saves to klines_cache_backtest/tradier/{SYMBOL}_5m.json.
Run on server: nohup python download_stock_klines_5m.py > /home/niels/logs/download_5m.log 2>&1 &
"""

import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = "2kX_tMy4PWx4JZMQZxqSJxQnznuOYAC0"
BASE_URL = "https://api.massive.com"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
if platform.system() == "Darwin":
    CACHE_DIR = Path("/Users/niels/Documents/binance/klines_cache_backtest/tradier")
else:
    CACHE_DIR = Path("/home/niels/binance-sandbox/klines_cache_backtest/tradier")
PROGRESS_FILE = CACHE_DIR / "_download_5m_progress.json"
MAX_LIMIT = 50000
MIN_REQUEST_INTERVAL = 13
FROM_DATE = "2024-01-01"
TO_DATE = "2026-03-30"

SYMBOLS = ["VT", "AAPL", "ABBV", "ABT", "ACN", "ADBE", "ADP", "AMD", "AMZN", "APA", "APO", "ARM", "ASML", "ASTS", "AVGO", "BA", "BABA", "BIDU", "BITO", "BK", "BLOK", "BTCL", "BWXT", "CAT", "CLX", "CME", "COPX", "COST", "CRM", "CRWD", "CRWV", "CVS", "CVX", "DHR", "DIME", "DUOL", "ETH", "ETHD", "FDX", "FIVN", "GE", "GILD", "GLD", "GM", "GME", "GOLD", "GOOGL", "HD", "HON", "IBIT", "IBM", "INOD", "JNJ", "JOBY", "JPM", "KO", "LLY", "LMT", "LOW", "LRCX", "LYFT", "MA", "MCD", "MDT", "META", "MO", "MRK", "MRVL", "MSFT", "MU", "NFLX", "NKE", "NVDA", "OLED", "ORCL", "OXY", "PATH", "PEP", "PFE", "PYPL", "QBTS", "QCOM", "QLYS", "QQQ", "QUBT", "RBLX", "RDDT", "RKLB", "ROKU", "RTX", "SAP", "SBIT", "SBUX", "SCHW", "SHOP", "SHY", "SLV", "SMCI", "SNDK", "SNOW", "SPOT", "SPY", "STZ", "T", "TCEHY", "TGT", "TMO", "TSLA", "TSM", "TTD", "TXN", "ULTA", "UNH", "UPS", "USAR", "USO", "V", "VZ", "WDAY", "WMT", "XOM", "ZETA"]


def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"completed": [], "failed": {}}


def save_progress(progress):
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def load_existing(symbol):
    path = CACHE_DIR / f"{symbol}_5m.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return []


def save_klines(symbol, bars):
    path = CACHE_DIR / f"{symbol}_5m.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(bars))
    os.replace(tmp, path)


def merge_bars(existing, new_bars):
    by_ts = {}
    for bar in existing:
        by_ts[bar["timestamp"]] = bar
    for bar in new_bars:
        by_ts[bar["timestamp"]] = bar
    return sorted(by_ts.values(), key=lambda b: b["timestamp"])


def rate_limit_wait(last_request_time):
    elapsed = time.time() - last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)


def fetch_5m_bars(symbol, last_req_time):
    """Fetch all available 5m bars for a symbol with pagination."""
    all_bars = []
    url = f"{BASE_URL}/v2/aggs/ticker/{symbol}/range/5/minute/{FROM_DATE}/{TO_DATE}?limit={MAX_LIMIT}&sort=asc"
    page = 0
    while url:
        page += 1
        rate_limit_wait(last_req_time[0])
        last_req_time[0] = time.time()
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"  Network error page {page}: {e}")
            time.sleep(30)
            continue
        if resp.status_code == 429 or "exceeded the maximum" in resp.text:
            print(f"  Rate limited on page {page}, waiting 65s...")
            time.sleep(65)
            continue
        data = resp.json()
        status = data.get("status", "")
        if status == "NOT_AUTHORIZED":
            print(f"  NOT_AUTHORIZED — skipping")
            return None
        if status == "ERROR":
            print(f"  ERROR: {data.get('error', '?')}")
            if "exceeded" in data.get("error", ""):
                time.sleep(65)
                continue
            return None
        results = data.get("results", [])
        if results:
            for r in results:
                ts_ms = r["t"]
                ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
                all_bars.append({
                    "timestamp": ts_str,
                    "open": r["o"],
                    "high": r["h"],
                    "low": r["l"],
                    "close": r["c"],
                    "volume": r.get("v", 0),
                })
        count = data.get("resultsCount", 0)
        print(f"  Page {page}: {count} bars (total so far: {len(all_bars)})")
        next_url = data.get("next_url")
        if next_url:
            url = next_url
        else:
            break
    return all_bars


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    progress = load_progress()
    completed = set(progress["completed"])
    remaining = [s for s in SYMBOLS if s not in completed]
    print(f"=== Massive.com REAL 5m Stock Klines Downloader ===")
    print(f"Symbols: {len(SYMBOLS)} total, {len(completed)} done, {len(remaining)} remaining")
    print(f"Date range: {FROM_DATE} → {TO_DATE}")
    print(f"Output: {CACHE_DIR}")
    print(f"Rate limit: 1 request per {MIN_REQUEST_INTERVAL}s")
    print(f"=" * 50)
    last_req_time = [0.0]
    for i, symbol in enumerate(remaining):
        print(f"\n[{len(completed)+1}/{len(SYMBOLS)}] {symbol}...")
        existing = load_existing(symbol)
        bars = fetch_5m_bars(symbol, last_req_time)
        if bars is None:
            progress["failed"][symbol] = datetime.now(timezone.utc).isoformat()
            save_progress(progress)
            continue
        if not bars:
            print(f"  No data returned")
            progress["failed"][symbol] = "no_data"
            save_progress(progress)
            continue
        if existing:
            merged = merge_bars(existing, bars)
            print(f"  Merged: {len(existing)} existing + {len(bars)} new = {len(merged)} total")
        else:
            merged = bars
        save_klines(symbol, merged)
        first_ts = merged[0]["timestamp"][:10]
        last_ts = merged[-1]["timestamp"][:10]
        print(f"  Saved {len(merged)} bars ({first_ts} → {last_ts})")
        completed.add(symbol)
        progress["completed"].append(symbol)
        save_progress(progress)
    print(f"\n{'=' * 50}")
    print(f"DONE. {len(completed)}/{len(SYMBOLS)} symbols downloaded.")
    if progress["failed"]:
        print(f"Failed ({len(progress['failed'])}): {list(progress['failed'].keys())}")


if __name__ == "__main__":
    main()

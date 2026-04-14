#!/usr/bin/env python3
"""Download deep 15m stock klines from Massive.com (formerly Polygon.io).

Plan gives 2 years of 15m data, 50k bars/request, ~5 req/min rate limit.
Saves to klines_cache_backtest/tradier/ in same dict format as existing data.
Merges with existing data if present (no duplicates, sorted by timestamp).
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = "2kX_tMy4PWx4JZMQZxqSJxQnznuOYAC0"
BASE_URL = "https://api.massive.com"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
CACHE_DIR = Path("/Users/niels/Documents/binance/klines_cache_backtest/tradier")
SYMBOLS_FILE = Path("/Users/niels/Documents/binance/klines_cache_backtest/stock_symbols.json")
PROGRESS_FILE = CACHE_DIR / "_download_progress.json"
MAX_LIMIT = 50000
MIN_REQUEST_INTERVAL = 13  # seconds between requests (safe for 5/min limit)
FROM_DATE = "2024-01-01"
TO_DATE = "2026-03-27"

SYMBOLS = ["AAPL", "ABBV", "ABT", "ABNB", "ACN", "ADBE", "ADP", "AEM", "AG", "AGCO", "AGI", "ALB", "AM", "AMD", "AMZN", "APA", "APO", "AR", "ARM", "ASC", "ASML", "ASTS", "ATI", "AU", "AVGO", "AXON", "AA", "ADM", "BA", "BABA", "BG", "BHP", "BIDU", "BITO", "BK", "BKR", "BLOK", "BOIL", "BTCL", "BTG", "BWXT", "CALM", "CAT", "CCJ", "CDE", "CENX", "CF", "CHRD", "CLF", "CLX", "CMC", "CME", "CNHI", "COIN", "COP", "COPX", "CORN", "COST", "CRM", "CRK", "CRWD", "CRWV", "CTRA", "CTVA", "CVS", "CVX", "DAC", "DAR", "DBA", "DE", "DHR", "DHT", "DIME", "DINO", "DIS", "DNN", "DUOL", "DVN", "EGO", "EOG", "EPD", "EQT", "ET", "ETH", "ETHD", "EXE", "EGLE", "FANG", "FCX", "FDX", "FIVN", "FMC", "FNV", "FRO", "GD", "GDX", "GDXJ", "GE", "GILD", "GLD", "GM", "GME", "GNK", "GOGL", "GOLD", "GOOGL", "HAL", "HD", "HES", "HII", "HL", "HON", "HWM", "IBIT", "IBM", "ICL", "INGR", "INOD", "INSW", "INTC", "IPI", "ITA", "JNJ", "JOBY", "JPM", "KGC", "KMI", "KO", "KTOS", "LAC", "LDOS", "LEU", "LHX", "LLY", "LMT", "LNG", "LOW", "LRCX", "LSB", "LYFT", "MA", "MAG", "MCD", "MDT", "META", "MO", "MOO", "MOS", "MPC", "MP", "MRK", "MRO", "MRVL", "MSFT", "MU", "NAT", "NEM", "NFLX", "NKE", "NNE", "NOC", "NTR", "NUE", "NVDA", "NXE", "OIH", "OKE", "OKLO", "OLED", "ORCL", "OXY", "PAAS", "PATH", "PBF", "PDBC", "PEP", "PFE", "PLL", "PLTR", "PPA", "PR", "PSX", "PYPL", "QBTS", "QCOM", "QLYS", "QQQ", "QUBT", "RBLX", "RDDT", "REMX", "RGLD", "RIO", "RKLB", "ROKU", "RRC", "RS", "RTX", "SAP", "SBIT", "SBUX", "SBLK", "SCHW", "SCCO", "SGML", "SHOP", "SHY", "SLB", "SLV", "SMCI", "SMG", "SMR", "SNDK", "SNOW", "SPOT", "SPY", "SQ", "SQM", "STLD", "STNG", "STZ", "T", "TCEHY", "TECK", "TDG", "TGT", "TMO", "TNK", "TRGP", "TSLA", "TSM", "TSN", "TTD", "TXN", "UAN", "UBER", "UEC", "ULTA", "UNG", "UNH", "UPS", "URA", "URNM", "USAR", "USO", "UUUU", "V", "VALE", "VLO", "VZ", "WDAY", "WEAT", "WMB", "WMT", "WPM", "X", "XLE", "XME", "XOM", "XOP", "ZETA", "ZIM"]


def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"completed": [], "failed": {}}


def save_progress(progress):
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def load_existing(symbol, tf="15m"):
    path = CACHE_DIR / f"{symbol}_{tf}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return []


def save_klines(symbol, tf, bars):
    path = CACHE_DIR / f"{symbol}_{tf}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(bars))
    os.replace(tmp, path)


def merge_bars(existing, new_bars):
    """Merge and deduplicate bars by timestamp, sorted ascending."""
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


def fetch_15m_bars(symbol, last_req_time):
    """Fetch all available 15m bars for a symbol with pagination."""
    all_bars = []
    url = f"{BASE_URL}/v2/aggs/ticker/{symbol}/range/15/minute/{FROM_DATE}/{TO_DATE}?limit={MAX_LIMIT}&sort=asc"
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
            if "apiKey=" not in url and "Authorization" not in url:
                pass  # headers handle auth
        else:
            break
    return all_bars


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    progress = load_progress()
    completed = set(progress["completed"])
    remaining = [s for s in SYMBOLS if s not in completed]
    print(f"=== Massive.com 15m Stock Klines Downloader ===")
    print(f"Symbols: {len(SYMBOLS)} total, {len(completed)} done, {len(remaining)} remaining")
    print(f"Date range: {FROM_DATE} → {TO_DATE}")
    print(f"Output: {CACHE_DIR}")
    print(f"Rate limit: 1 request per {MIN_REQUEST_INTERVAL}s")
    print(f"=" * 50)
    last_req_time = [0.0]
    for i, symbol in enumerate(remaining):
        print(f"\n[{len(completed)+1}/{len(SYMBOLS)}] {symbol}...")
        existing = load_existing(symbol)
        bars = fetch_15m_bars(symbol, last_req_time)
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
        save_klines(symbol, "15m", merged)
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
    # Summary
    total_bars = 0
    for s in SYMBOLS:
        path = CACHE_DIR / f"{s}_15m.json"
        if path.exists():
            try:
                total_bars += len(json.loads(path.read_text()))
            except Exception:
                pass
    print(f"Total bars across all symbols: {total_bars:,}")


if __name__ == "__main__":
    main()

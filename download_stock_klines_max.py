#!/usr/bin/env python3
"""Download maximum historical klines for all stock symbols using yfinance.

Yahoo Finance limits:
- 1m: 7 days
- 2m/5m/15m/30m: 60 days
- 1h: 730 days (2 years) — we paginate in 59-day chunks to avoid issues
- 1d: since inception (max history)

Saves to klines_cache/{SYMBOL}_{TF}.json, merging with existing data.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd

CACHE_DIR = Path("/home/niels/binance-sandbox/klines_cache")
SYMBOLS_FILE = Path("/home/niels/binance-sandbox/stock_symbols.json")

# Yahoo interval -> our TF naming
TF_MAP = {
    "1d": "D",
    "1h": "1h",
    "15m": "15m",
    "5m": "5m",
    "1m": "1m",
}

# Download configs: (yf_interval, yf_period_or_strategy, our_tf_name)
DOWNLOAD_CONFIGS = [
    # Daily: max history
    {"interval": "1d", "tf": "D", "strategy": "max"},
    # Weekly: max history
    {"interval": "1wk", "tf": "W", "strategy": "max"},
    # Monthly: max history
    {"interval": "1mo", "tf": "M", "strategy": "max"},
    # 1h: paginate in 59-day chunks over 730 days
    {"interval": "1h", "tf": "1h", "strategy": "paginate_730d"},
    # 4h: synthesize from 1h data (yfinance doesn't have 4h)
    {"interval": "4h_synth", "tf": "4h", "strategy": "synthesize"},
    # 15m: 60 days max
    {"interval": "15m", "tf": "15m", "strategy": "60d"},
    # 5m: 60 days max
    {"interval": "5m", "tf": "5m", "strategy": "60d"},
    # 1m: 7 days max
    {"interval": "1m", "tf": "1m", "strategy": "7d"},
]


def load_existing(symbol, tf):
    """Load existing klines from cache file."""
    path = CACHE_DIR / f"{symbol}_{tf}.json"
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            return data
        except Exception:
            return []
    return []


def save_klines(symbol, tf, bars):
    """Save klines to cache file."""
    path = CACHE_DIR / f"{symbol}_{tf}.json"
    with open(path, "w") as f:
        json.dump(bars, f)


def df_to_bars(df):
    """Convert yfinance DataFrame to our bar format."""
    bars = []
    for idx, row in df.iterrows():
        ts = idx
        if hasattr(ts, 'isoformat'):
            ts_str = ts.isoformat()
        else:
            ts_str = str(ts)
        if pd.isna(row["Open"]) or pd.isna(row["Close"]):
            continue
        bars.append({
            "timestamp": ts_str,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]) if not pd.isna(row["Volume"]) else 0.0,
        })
    return bars


def merge_bars(existing, new_bars):
    """Merge new bars into existing, dedup by timestamp, sort chronologically."""
    by_ts = {}
    for bar in existing:
        by_ts[bar["timestamp"]] = bar
    for bar in new_bars:
        by_ts[bar["timestamp"]] = bar  # new overwrites old (fresher data)
    merged = sorted(by_ts.values(), key=lambda x: x["timestamp"])
    return merged


def download_max(symbol, interval):
    """Download maximum available history."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="max", interval=interval)
    return df_to_bars(df) if not df.empty else []


def download_period(symbol, interval, period):
    """Download with a specific period string."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval)
    return df_to_bars(df) if not df.empty else []


def download_paginated_1h(symbol, total_days=730, chunk_days=59):
    """Download 1h data in chunks to get full 730 days."""
    all_bars = []
    end = datetime.now()
    start = end - timedelta(days=total_days)
    current_start = start
    while current_start < end:
        current_end = min(current_start + timedelta(days=chunk_days), end)
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=current_start.strftime("%Y-%m-%d"), end=current_end.strftime("%Y-%m-%d"), interval="1h")
            if not df.empty:
                bars = df_to_bars(df)
                all_bars.extend(bars)
        except Exception as e:
            print(f"  Warning: chunk {current_start.date()}-{current_end.date()} failed: {e}")
        current_start = current_end
        time.sleep(0.3)  # Rate limit
    return all_bars


def synthesize_4h_from_1h(symbol):
    """Build 4h bars from 1h data."""
    path_1h = CACHE_DIR / f"{symbol}_1h.json"
    if not path_1h.exists():
        return []
    with open(path_1h) as f:
        bars_1h = json.load(f)
    if not bars_1h:
        return []
    # Group into 4h buckets: 09:30-13:29, 13:30-15:59 (market hours)
    # Simpler: just group every 4 consecutive bars
    bars_4h = []
    i = 0
    while i + 3 < len(bars_1h):
        chunk = bars_1h[i:i+4]
        bars_4h.append({
            "timestamp": chunk[0]["timestamp"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b["volume"] for b in chunk),
        })
        i += 4
    # Handle remainder
    if i < len(bars_1h):
        chunk = bars_1h[i:]
        bars_4h.append({
            "timestamp": chunk[0]["timestamp"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b["volume"] for b in chunk),
        })
    return bars_4h


def download_symbol(symbol, configs):
    """Download all timeframes for a symbol."""
    results = {}
    for cfg in configs:
        tf = cfg["tf"]
        strategy = cfg["strategy"]
        interval = cfg["interval"]
        existing = load_existing(symbol, tf)
        existing_count = len(existing)
        try:
            if strategy == "synthesize":
                # 4h is built from 1h after 1h download
                new_bars = synthesize_4h_from_1h(symbol)
                if new_bars:
                    merged = merge_bars(existing, new_bars)
                    save_klines(symbol, tf, merged)
                    results[tf] = len(merged)
                    print(f"  {tf}: {existing_count} -> {len(merged)} bars (synthesized from 1h)")
                else:
                    results[tf] = existing_count
                    print(f"  {tf}: {existing_count} bars (no 1h data to synthesize)")
                continue
            if strategy == "max":
                new_bars = download_max(symbol, interval)
            elif strategy == "paginate_730d":
                new_bars = download_paginated_1h(symbol)
            elif strategy == "60d":
                new_bars = download_period(symbol, interval, "60d")
            elif strategy == "7d":
                new_bars = download_period(symbol, interval, "7d")
            else:
                new_bars = []
            if new_bars:
                merged = merge_bars(existing, new_bars)
                save_klines(symbol, tf, merged)
                results[tf] = len(merged)
                added = len(merged) - existing_count
                print(f"  {tf}: {existing_count} -> {len(merged)} bars (+{added} new)")
            else:
                results[tf] = existing_count
                print(f"  {tf}: {existing_count} bars (no new data)")
            time.sleep(0.2)  # Rate limit between requests
        except Exception as e:
            results[tf] = existing_count
            print(f"  {tf}: ERROR - {e} (kept {existing_count} existing)")
    return results


def try_schwab_download(symbol, token):
    """Try Schwab API for additional intraday history."""
    import requests
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://api.schwabapi.com/marketdata/v1/pricehistory"
    results = {}
    for period_type, frequency_type, frequency, tf, periods in [
        ("year", "daily", 1, "D", 20),
        ("year", "weekly", 1, "W", 20),
        ("month", "daily", 1, "D", 6),
    ]:
        params = {
            "symbol": symbol,
            "periodType": period_type,
            "period": periods,
            "frequencyType": frequency_type,
            "frequency": frequency,
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                candles = data.get("candles", [])
                if candles:
                    bars = []
                    for c in candles:
                        ts = datetime.fromtimestamp(c["datetime"] / 1000).isoformat()
                        bars.append({"timestamp": ts, "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": float(c.get("volume", 0))})
                    results[tf] = bars
            elif resp.status_code == 401:
                print(f"  Schwab: token expired (401)")
                return {}
        except Exception as e:
            print(f"  Schwab {tf}: {e}")
    return results


def main():
    with open(SYMBOLS_FILE) as f:
        symbols = json.load(f)
    print(f"Downloading max klines for {len(symbols)} stock symbols")
    print(f"Cache dir: {CACHE_DIR}")
    print(f"Timeframes: {[c['tf'] for c in DOWNLOAD_CONFIGS]}")
    print("=" * 60)
    # Try Schwab first for one symbol to see if token works
    schwab_token = "Mqy3f3gFOmCQQOADGkwaXi8WRyz0"
    print("\nTesting Schwab API...")
    schwab_works = False
    try:
        import requests
        resp = requests.get("https://api.schwabapi.com/marketdata/v1/pricehistory", headers={"Authorization": f"Bearer {schwab_token}"}, params={"symbol": "AAPL", "periodType": "month", "period": 1, "frequencyType": "daily", "frequency": 1}, timeout=10)
        if resp.status_code == 200:
            schwab_works = True
            print("Schwab API: WORKING")
        else:
            print(f"Schwab API: status {resp.status_code} - skipping")
    except Exception as e:
        print(f"Schwab API: {e} - skipping")
    all_results = {}
    for i, symbol in enumerate(symbols):
        print(f"\n[{i+1}/{len(symbols)}] {symbol}")
        results = download_symbol(symbol, DOWNLOAD_CONFIGS)
        # Try Schwab for additional daily data if it works
        if schwab_works:
            try:
                schwab_data = try_schwab_download(symbol, schwab_token)
                for tf, bars in schwab_data.items():
                    if bars:
                        existing = load_existing(symbol, tf)
                        merged = merge_bars(existing, bars)
                        save_klines(symbol, tf, merged)
                        if len(merged) > results.get(tf, 0):
                            print(f"  {tf}: Schwab added -> {len(merged)} bars total")
                            results[tf] = len(merged)
            except Exception as e:
                print(f"  Schwab: {e}")
        all_results[symbol] = results
        time.sleep(0.5)  # Rate limit between symbols
    # Summary
    print("\n" + "=" * 60)
    print("DOWNLOAD COMPLETE - SUMMARY")
    print("=" * 60)
    tf_totals = {}
    tf_counts = {}
    for symbol, results in all_results.items():
        for tf, count in results.items():
            tf_totals[tf] = tf_totals.get(tf, 0) + count
            tf_counts.setdefault(tf, []).append(count)
    for tf in ["D", "W", "M", "1h", "4h", "15m", "5m", "1m"]:
        if tf in tf_counts:
            counts = tf_counts[tf]
            avg = tf_totals[tf] / len(counts)
            mn = min(counts)
            mx = max(counts)
            print(f"  {tf:>4s}: {len(counts):>3d} symbols, avg={avg:>8.0f}, min={mn:>6d}, max={mx:>6d}, total={tf_totals[tf]:>10d}")
    # Per-symbol detail for daily
    print("\nPer-symbol daily bar counts:")
    for symbol in sorted(all_results.keys()):
        d_count = all_results[symbol].get("D", 0)
        h_count = all_results[symbol].get("1h", 0)
        print(f"  {symbol:>6s}: D={d_count:>6d}  1h={h_count:>5d}")


if __name__ == "__main__":
    main()

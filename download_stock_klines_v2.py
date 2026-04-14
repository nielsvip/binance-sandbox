#!/usr/bin/env python3
"""V2: Download remaining stock klines — only missing timeframes/symbols.

Fixes from v1:
- Skip 1h pagination entirely (already have 3658 bars, Yahoo max is 730d = ~4900 bars)
- Use period="max" for 1h instead (simpler, faster)
- Skip symbols already done for W/M
- Faster rate limiting
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


def load_existing(symbol, tf):
    path = CACHE_DIR / f"{symbol}_{tf}.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_klines(symbol, tf, bars):
    path = CACHE_DIR / f"{symbol}_{tf}.json"
    with open(path, "w") as f:
        json.dump(bars, f)


def df_to_bars(df):
    bars = []
    for idx, row in df.iterrows():
        if pd.isna(row["Open"]) or pd.isna(row["Close"]):
            continue
        bars.append({
            "timestamp": idx.isoformat(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]) if not pd.isna(row["Volume"]) else 0.0,
        })
    return bars


def merge_bars(existing, new_bars):
    by_ts = {}
    for bar in existing:
        by_ts[bar["timestamp"]] = bar
    for bar in new_bars:
        by_ts[bar["timestamp"]] = bar
    return sorted(by_ts.values(), key=lambda x: x["timestamp"])


def synthesize_4h(symbol):
    bars_1h = load_existing(symbol, "1h")
    if not bars_1h:
        return []
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


def try_schwab(symbol, token):
    """Try Schwab API for extra daily/weekly data."""
    import requests
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://api.schwabapi.com/marketdata/v1/pricehistory"
    results = {}
    for period_type, freq_type, freq, tf, periods in [
        ("year", "daily", 1, "D", 20),
        ("year", "weekly", 1, "W", 20),
    ]:
        try:
            resp = requests.get(url, headers=headers, params={"symbol": symbol, "periodType": period_type, "period": periods, "frequencyType": freq_type, "frequency": freq}, timeout=10)
            if resp.status_code == 200:
                candles = resp.json().get("candles", [])
                if candles:
                    bars = [{"timestamp": datetime.fromtimestamp(c["datetime"]/1000).isoformat(), "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": float(c.get("volume", 0))} for c in candles]
                    results[tf] = bars
            elif resp.status_code == 401:
                return None  # Token expired
        except Exception:
            pass
    return results


def main():
    with open(SYMBOLS_FILE) as f:
        symbols = json.load(f)
    print(f"V2: Filling gaps for {len(symbols)} stock symbols")
    print("=" * 60)

    # Test Schwab
    schwab_token = "Mqy3f3gFOmCQQOADGkwaXi8WRyz0"
    schwab_works = False
    try:
        import requests
        resp = requests.get("https://api.schwabapi.com/marketdata/v1/pricehistory", headers={"Authorization": f"Bearer {schwab_token}"}, params={"symbol": "AAPL", "periodType": "month", "period": 1, "frequencyType": "daily", "frequency": 1}, timeout=10)
        schwab_works = resp.status_code == 200
        print(f"Schwab API: {'WORKING' if schwab_works else f'status {resp.status_code}'}")
    except Exception as e:
        print(f"Schwab API: {e}")

    all_results = {}

    for i, symbol in enumerate(symbols):
        print(f"\n[{i+1}/{len(symbols)}] {symbol}")
        results = {}

        # === DAILY (max) — skip if already >5000 bars ===
        existing_d = load_existing(symbol, "D")
        if len(existing_d) < 5000:
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(period="max", interval="1d")
                if not df.empty:
                    new_bars = df_to_bars(df)
                    merged = merge_bars(existing_d, new_bars)
                    save_klines(symbol, "D", merged)
                    results["D"] = len(merged)
                    print(f"  D: {len(existing_d)} -> {len(merged)}")
                else:
                    results["D"] = len(existing_d)
                time.sleep(0.15)
            except Exception as e:
                results["D"] = len(existing_d)
                print(f"  D: ERROR {e}")
        else:
            results["D"] = len(existing_d)
            print(f"  D: {len(existing_d)} (already done)")

        # === WEEKLY (max) ===
        existing_w = load_existing(symbol, "W")
        if len(existing_w) < 500:
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(period="max", interval="1wk")
                if not df.empty:
                    new_bars = df_to_bars(df)
                    merged = merge_bars(existing_w, new_bars)
                    save_klines(symbol, "W", merged)
                    results["W"] = len(merged)
                    print(f"  W: {len(existing_w)} -> {len(merged)}")
                else:
                    results["W"] = len(existing_w)
                time.sleep(0.15)
            except Exception as e:
                results["W"] = len(existing_w)
                print(f"  W: ERROR {e}")
        else:
            results["W"] = len(existing_w)
            print(f"  W: {len(existing_w)} (already done)")

        # === MONTHLY (max) ===
        existing_m = load_existing(symbol, "M")
        if len(existing_m) < 100:
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(period="max", interval="1mo")
                if not df.empty:
                    new_bars = df_to_bars(df)
                    merged = merge_bars(existing_m, new_bars)
                    save_klines(symbol, "M", merged)
                    results["M"] = len(merged)
                    print(f"  M: {len(existing_m)} -> {len(merged)}")
                else:
                    results["M"] = len(existing_m)
                time.sleep(0.15)
            except Exception as e:
                results["M"] = len(existing_m)
                print(f"  M: ERROR {e}")
        else:
            results["M"] = len(existing_m)
            print(f"  M: {len(existing_m)} (already done)")

        # === 1H (730d = period max for 1h) — use period, not date chunks ===
        existing_1h = load_existing(symbol, "1h")
        if len(existing_1h) < 4500:
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(period="730d", interval="1h")
                if not df.empty:
                    new_bars = df_to_bars(df)
                    merged = merge_bars(existing_1h, new_bars)
                    save_klines(symbol, "1h", merged)
                    results["1h"] = len(merged)
                    print(f"  1h: {len(existing_1h)} -> {len(merged)}")
                else:
                    results["1h"] = len(existing_1h)
                time.sleep(0.15)
            except Exception as e:
                results["1h"] = len(existing_1h)
                print(f"  1h: ERROR {e}")
        else:
            results["1h"] = len(existing_1h)
            print(f"  1h: {len(existing_1h)} (already done)")

        # === 15m (60d) ===
        existing_15m = load_existing(symbol, "15m")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="60d", interval="15m")
            if not df.empty:
                new_bars = df_to_bars(df)
                merged = merge_bars(existing_15m, new_bars)
                save_klines(symbol, "15m", merged)
                results["15m"] = len(merged)
                if len(merged) > len(existing_15m):
                    print(f"  15m: {len(existing_15m)} -> {len(merged)}")
            else:
                results["15m"] = len(existing_15m)
            time.sleep(0.15)
        except Exception as e:
            results["15m"] = len(existing_15m)
            print(f"  15m: ERROR {e}")

        # === 5m (60d) ===
        existing_5m = load_existing(symbol, "5m")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="60d", interval="5m")
            if not df.empty:
                new_bars = df_to_bars(df)
                merged = merge_bars(existing_5m, new_bars)
                save_klines(symbol, "5m", merged)
                results["5m"] = len(merged)
                if len(merged) > len(existing_5m):
                    print(f"  5m: {len(existing_5m)} -> {len(merged)}")
            else:
                results["5m"] = len(existing_5m)
            time.sleep(0.15)
        except Exception as e:
            results["5m"] = len(existing_5m)
            print(f"  5m: ERROR {e}")

        # === 1m (7d) ===
        existing_1m = load_existing(symbol, "1m")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="7d", interval="1m")
            if not df.empty:
                new_bars = df_to_bars(df)
                merged = merge_bars(existing_1m, new_bars)
                save_klines(symbol, "1m", merged)
                results["1m"] = len(merged)
                if len(merged) > len(existing_1m):
                    print(f"  1m: {len(existing_1m)} -> {len(merged)}")
            else:
                results["1m"] = len(existing_1m)
            time.sleep(0.15)
        except Exception as e:
            results["1m"] = len(existing_1m)
            print(f"  1m: ERROR {e}")

        # === Synthesize 4h from 1h ===
        existing_4h = load_existing(symbol, "4h")
        new_4h = synthesize_4h(symbol)
        if new_4h:
            merged_4h = merge_bars(existing_4h, new_4h)
            save_klines(symbol, "4h", merged_4h)
            results["4h"] = len(merged_4h)
        else:
            results["4h"] = len(existing_4h)

        # === Schwab extra daily/weekly ===
        if schwab_works:
            try:
                schwab_data = try_schwab(symbol, schwab_token)
                if schwab_data is None:
                    schwab_works = False
                    print("  Schwab token expired")
                elif schwab_data:
                    for tf, bars in schwab_data.items():
                        existing = load_existing(symbol, tf)
                        merged = merge_bars(existing, bars)
                        save_klines(symbol, tf, merged)
                        if len(merged) > results.get(tf, 0):
                            print(f"  {tf}: Schwab -> {len(merged)}")
                            results[tf] = len(merged)
            except Exception:
                pass

        all_results[symbol] = results
        time.sleep(0.3)

    # === SUMMARY ===
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    tf_data = {}
    for symbol, results in all_results.items():
        for tf, count in results.items():
            tf_data.setdefault(tf, []).append(count)
    for tf in ["D", "W", "M", "1h", "4h", "15m", "5m", "1m"]:
        if tf in tf_data:
            counts = tf_data[tf]
            total = sum(counts)
            avg = total / len(counts)
            mn = min(counts)
            mx = max(counts)
            print(f"  {tf:>4s}: {len(counts):>3d} symbols, avg={avg:>8.0f}, min={mn:>6d}, max={mx:>6d}, total={total:>10d}")

    print("\nPer-symbol bar counts (D / 1h / 15m):")
    for symbol in sorted(all_results.keys()):
        r = all_results[symbol]
        print(f"  {symbol:>6s}: D={r.get('D',0):>6d}  W={r.get('W',0):>5d}  1h={r.get('1h',0):>5d}  15m={r.get('15m',0):>5d}  5m={r.get('5m',0):>5d}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Additively backfill klines_cache_gateway/{SYMBOL}_{TF}.json from Binance futures.

The live klines_cache is a rolling ~1700-bar window (and on S1 klines_cache_backtest
is a symlink to it), so NPZ rebuilds lose history. backtest_v8_precompute unions
klines_cache_gateway into every gap, so filling the gateway file restores coverage.
Existing gateway bars are kept on overlap; only missing timestamps are added.
"""
import argparse
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent.parent
GATEWAY_DIR = BASE_PATH / "klines_cache_gateway"
BACKUP_DIR = BASE_PATH / "backups"
INTERVALS = {"15m": ("15m", 900), "1h": ("1h", 3600), "4h": ("4h", 14400), "D": ("1d", 86400)}


def fetch(symbol, interval, start_ms, end_ms):
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        query = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": 1500})
        page = None
        for attempt in range(6):
            try:
                with urllib.request.urlopen(f"https://fapi.binance.com/fapi/v1/klines?{query}", timeout=20) as resp:
                    page = json.loads(resp.read())
                break
            except Exception as exc:
                print(f"  retry {attempt + 1} {symbol} {interval}: {exc}", flush=True)
                time.sleep(5 * (attempt + 1))
        if page is None:
            raise RuntimeError(f"fetch failed {symbol} {interval} at {cursor}")
        if not page:
            break
        rows.extend(page)
        cursor = int(page[-1][0]) + 1
        time.sleep(0.35)
    return rows


def to_bar(row):
    stamp = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {"timestamp": stamp, "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--days", type=int, default=400)
    parser.add_argument("--tfs", default="15m,1h,4h,D")
    args = parser.parse_args()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 86400 * 1000
    stamp = datetime.now().strftime("%Y%m%d%H%M")
    for tf in args.tfs.split(","):
        interval, step = INTERVALS[tf]
        path = GATEWAY_DIR / f"{args.symbol}_{tf}.json"
        existing = []
        if path.exists():
            existing = json.load(open(path))
            shutil.copy2(path, BACKUP_DIR / f"before_gateway_backfill_{args.symbol}_{tf}_{stamp}.json")
        merged = {bar["timestamp"][:19]: bar for bar in existing}
        fetched = [to_bar(row) for row in fetch(args.symbol, interval, start_ms, end_ms)]
        added = 0
        for bar in fetched:
            if bar["timestamp"][:19] not in merged:
                merged[bar["timestamp"][:19]] = bar
                added += 1
        bars = [merged[key] for key in sorted(merged)]
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w") as handle:
            json.dump(bars, handle)
        os.replace(tmp_path, path)
        epochs = [datetime.strptime(bar["timestamp"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() for bar in bars]
        max_gap = max((b - a for a, b in zip(epochs, epochs[1:])), default=0) / step
        print(f"{args.symbol} {tf}: existing {len(existing)} fetched {len(fetched)} added {added} total {len(bars)} {bars[0]['timestamp'][:16]} -> {bars[-1]['timestamp'][:16]} max_gap_bars {max_gap:.0f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

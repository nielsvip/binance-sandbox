#!/usr/bin/env python3
"""One-time fix: convert all klines files with ET timestamps (-04:00/-05:00) to UTC (Z).

Scans klines_cache/ and klines_cache_backtest/tradier/ locally.
Run on server separately with --server flag.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dateutil import parser as dtparser


def convert_file(fpath):
    """Convert ET timestamps to UTC in a single klines JSON file. Returns (converted, bar_count)."""
    with open(fpath) as f:
        data = json.load(f)
    if not data or not isinstance(data[0], dict):
        return False, 0
    ts0 = data[0].get("timestamp", "")
    if "-04:00" not in ts0 and "-05:00" not in ts0:
        return False, 0
    for bar in data:
        ts = bar["timestamp"]
        dt = dtparser.isoparse(ts)
        bar["timestamp"] = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    tmp = str(fpath) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, fpath)
    return True, len(data)


def main():
    is_server = "--server" in sys.argv
    if is_server:
        dirs = [
            Path("/home/niels/binance-sandbox/klines_cache/tradier"),
            Path("/home/niels/binance/klines_cache"),
        ]
    else:
        dirs = [
            Path("/Users/niels/Documents/binance/klines_cache"),
            Path("/Users/niels/Documents/binance/klines_cache_backtest/tradier"),
        ]
    total_files = 0
    total_bars = 0
    for kdir in dirs:
        if not kdir.exists():
            print(f"SKIP {kdir} (not found)")
            continue
        print(f"\nScanning {kdir}...")
        converted = 0
        for fpath in sorted(kdir.glob("*.json")):
            if fpath.name.startswith("_"):
                continue
            try:
                did_convert, n_bars = convert_file(fpath)
                if did_convert:
                    converted += 1
                    total_bars += n_bars
                    print(f"  {fpath.name}: {n_bars} bars → UTC")
            except Exception as e:
                print(f"  {fpath.name}: ERROR — {e}")
        total_files += converted
        print(f"  Converted {converted} files in {kdir.name}")
    print(f"\nDONE: {total_files} files, {total_bars:,} bars converted to UTC")


if __name__ == "__main__":
    main()

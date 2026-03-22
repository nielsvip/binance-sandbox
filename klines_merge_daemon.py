#!/home/niels/.conda/envs/binance_env/bin/python
"""Continuous klines merge daemon: merges gateway JSON bars into main klines_cache."""
import json
import os
import time
import logging
from pathlib import Path

GATEWAY_DIR = Path("/home/niels/binance/klines_cache_gateway")
CACHE_DIR = Path("/home/niels/binance/klines_cache")
LOG_FILE = "/home/niels/logs/klines_merge.log"
CYCLE_SECONDS = 120
MTIME_TRACK = {}

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("klines_merge")


def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_json(path, data):
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, str(path))


def merge_bars(cache_bars, gateway_bars):
    """Merge gateway bars into cache bars by timestamp. Returns (merged_list, added_count)."""
    cache_timestamps = {bar["timestamp"] for bar in cache_bars}
    new_bars = [bar for bar in gateway_bars if bar["timestamp"] not in cache_timestamps]
    if not new_bars:
        return cache_bars, 0
    merged = cache_bars + new_bars
    merged.sort(key=lambda b: b["timestamp"])
    return merged, len(new_bars)


def run_cycle():
    if not GATEWAY_DIR.exists():
        return
    total_merged = 0
    files_processed = 0
    files_updated = 0
    for gw_file in GATEWAY_DIR.glob("*.json"):
        fname = gw_file.name
        try:
            gw_mtime = gw_file.stat().st_mtime
        except OSError:
            continue
        prev_mtime = MTIME_TRACK.get(fname, 0)
        if gw_mtime <= prev_mtime:
            continue
        files_processed += 1
        cache_file = CACHE_DIR / fname
        gw_bars = load_json(gw_file)
        if gw_bars is None or not isinstance(gw_bars, list):
            MTIME_TRACK[fname] = gw_mtime
            continue
        if cache_file.exists():
            cache_bars = load_json(cache_file)
            if cache_bars is None or not isinstance(cache_bars, list):
                cache_bars = []
        else:
            cache_bars = []
        merged, added = merge_bars(cache_bars, gw_bars)
        if added > 0:
            save_json(cache_file, merged)
            total_merged += added
            files_updated += 1
        MTIME_TRACK[fname] = gw_mtime
    if files_processed > 0:
        logger.info(f"Cycle done: {files_processed} files checked, {files_updated} updated, {total_merged} bars added")


def main():
    logger.info(f"klines_merge_daemon started (pid {os.getpid()})")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"Cycle error: {e}")
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()

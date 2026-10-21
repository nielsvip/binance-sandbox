"""data_never_miss_probe — stdlib-only, read-only freshness probe; runs locally or piped over ssh (`python3 - base...`).

Prints one JSON object: {"now": epoch_s, "host": name, "bases": {base: {rel: [mtime_s, last_bar_ms|null, size]}}}.
Kline files are tail-read (last 2 KB) for their last bar; market/price files report mtime only.
"""
import glob
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone

TS = re.compile(rb'"timestamp"\s*:\s*"([0-9T:\- ]{19})')
KLINE_GLOBS = ("klines_cache/*.json", "klines_cache/gateway/*.json", "klines_cache/tradier/*.json")
MARKET_FILES = ("price_cache_1.json", "price_cache_2.json", "price_cache_3.json", "data/latest_market_data.json", "data/tradier/tradier_indicators_latest.json")


def last_ms(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 2048))
            hits = TS.findall(fh.read())
        if hits:
            s = hits[-1].decode().replace(" ", "T")
            return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (OSError, ValueError):
        pass
    return None


def probe(base):
    out = {}
    base = os.path.expanduser(base)
    for g in KLINE_GLOBS:
        for p in glob.glob(os.path.join(base, g)):
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, base)] = [round(st.st_mtime, 1), last_ms(p), st.st_size]
    for rel in MARKET_FILES:
        p = os.path.join(base, rel)
        try:
            st = os.stat(p)
            out[rel] = [round(st.st_mtime, 1), None, st.st_size]
        except OSError:
            continue
    return out


if __name__ == "__main__":
    bases = sys.argv[1:] or ["."]
    print(json.dumps({"now": time.time(), "host": socket.gethostname(), "bases": {b: probe(b) for b in bases}}))

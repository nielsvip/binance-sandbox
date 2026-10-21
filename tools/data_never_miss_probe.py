"""data_never_miss_probe — stdlib-only, read-only freshness probe; runs locally or piped over ssh (`python3 - base...`).

Prints one JSON object: {"now": epoch_s, "host": name, "bases": {base: {rel: [mtime_s, last_bar_ms|null, size]}}}.
Kline files are tail-read (last 2 KB) for their last bar; market/price files report mtime only.
"""
import glob
import json
import os
import re
import shutil
import socket
import sys
import time
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = None

# stock D files may carry date-only stamps ("2026-10-20"); everything else "YYYY-MM-DDTHH:MM:SS"
TS = re.compile(rb'"timestamp"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?)')
KLINE_GLOBS = ("klines_cache/*.json", "klines_cache/gateway/*.json", "klines_cache/tradier/*.json")
MARKET_FILES = ("price_cache_1.json", "price_cache_2.json", "price_cache_3.json", "data/latest_market_data.json", "data/tradier/tradier_indicators_latest.json",
                "ez_s1_stack/price_cache_1.json", "ez_s1_stack/price_cache_2.json", "ez_s1_stack/price_cache_3.json", "ez_s1_stack/data/latest_market_data.json")


def canon(ts):
    """one key for every stamp style seen in the stores: "2026-10-20", "2026-10-20T19:30", "2026-10-20 19:30:00",
    "2026-10-20T19:30:00.000000Z" -> "2026-10-20T19:30:00" (2026-10-21: three styles coexist in stock files)."""
    s = str(ts).replace(" ", "T")
    if len(s) >= 19 and s[16] == ":":
        return s[:19]
    if len(s) >= 16 and s[13] == ":":
        return s[:16] + ":00"
    return s[:10] + "T00:00:00"


def last_ms(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 2048))
            hits = TS.findall(fh.read())
        if hits:
            raw = hits[-1].decode()
            s = canon(raw)
            tz = timezone.utc
            # 2026-10-21 MEASURED: stock intraday files stamped "YYYY-MM-DD HH:MM" (space) are New-York-labelled
            # (USO "2026-10-20 13:30" == UTC 17:30 bar) — read them in ET so they compare with the UTC stores
            if "/tradier/" in path.replace(os.sep, "/") and len(raw) > 10 and raw[10] == " " and ET is not None:
                tz = ET
            return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=tz).timestamp() * 1000)
    except (OSError, ValueError):
        pass
    return None


def mixed_labels(path):
    """stock intraday file whose tail holds New-York "YYYY-MM-DD HH:MM" rows — its UTC-looking rows can be ET bars too
    (USO 2026-10-21), so its last bar cannot be compared with the UTC stores. Reported, never auto-healed."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 4096))
            return re.search(rb'"timestamp"\s*:\s*"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}', fh.read()) is not None
    except OSError:
        return False


def probe(base):
    out = {}
    base = os.path.expanduser(base)
    for g in KLINE_GLOBS:
        for p in glob.glob(os.path.join(base, g)):
            try:
                st = os.stat(p)
            except OSError:
                continue
            ent = [round(st.st_mtime, 1), last_ms(p), st.st_size]
            if "/tradier/" in p.replace(os.sep, "/") and not p.endswith("_D.json") and mixed_labels(p):
                ent.append("mixed")
            out[os.path.relpath(p, base)] = ent
    for rel in MARKET_FILES:
        p = os.path.join(base, rel)
        try:
            st = os.stat(p)
            out[rel] = [round(st.st_mtime, 1), None, st.st_size]
        except OSError:
            continue
    # newest Mac-own crypto snapshot (the failover's SNAPSHOT signal): market_data_2*.json, not *_s1 / worker copies
    snaps = [p for p in glob.glob(os.path.join(base, "data", "market_data_2*.json")) if not p.endswith("_s1.json") and "worker" not in p]
    if snaps:
        newest = max(snaps, key=os.path.getmtime)
        out["data/market_data_NEWEST"] = [round(os.path.getmtime(newest), 1), None, os.path.getsize(newest)]
    try:
        out["_disk_free"] = [time.time(), None, shutil.disk_usage(base).free]
    except OSError:
        pass
    return out


if __name__ == "__main__":
    bases = sys.argv[1:] or ["."]
    print(json.dumps({"now": time.time(), "host": socket.gethostname(), "bases": {b: probe(b) for b in bases}}))

"""data_never_miss_tail — stdlib-only, read-only: rows newer than a given bar, per file. Piped over ssh so only the
missing bars cross the wire (whole-file rsync of S1's multi-MB stock files timed out at 180 s, 2026-10-21).

  ssh host 'python3 - BASE REQ_JSON' < tools/data_never_miss_tail.py      REQ_JSON = {"klines_cache/X_15m.json": after_ms}
"""
import json
import os
import sys
import time


def canon(ts):
    """one key for every stamp style seen in the stores: "2026-10-20", "2026-10-20T19:30", "2026-10-20 19:30:00",
    "2026-10-20T19:30:00.000000Z" -> "2026-10-20T19:30:00" (2026-10-21: three styles coexist in stock files)."""
    s = str(ts).replace(" ", "T")
    if len(s) >= 19 and s[16] == ":":
        return s[:19]
    if len(s) >= 16 and s[13] == ":":
        return s[:16] + ":00"
    return s[:10] + "T00:00:00"


def rows_after(path, after_ms):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    after = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(after_ms / 1000))
    n = 10 if path.endswith("_D.json") else 19  # D: one row per DATE (stock D files mix "2026-10-20" and "...T20:00:00")
    return [r for r in data if isinstance(r, dict) and canon(r.get("timestamp", ""))[:n] > after[:n]]


if __name__ == "__main__":
    base = os.path.expanduser(sys.argv[1])
    req = json.loads(sys.argv[2])
    out = {}
    for rel, after in req.items():
        rows = rows_after(os.path.join(base, rel), after)
        if rows:
            out[rel] = rows
    print(json.dumps(out))

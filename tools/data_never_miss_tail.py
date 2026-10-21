"""data_never_miss_tail — stdlib-only, read-only: rows newer than a given bar, per file. Piped over ssh so only the
missing bars cross the wire (whole-file rsync of S1's multi-MB stock files timed out at 180 s, 2026-10-21).

  ssh host 'python3 - BASE REQ_JSON' < tools/data_never_miss_tail.py      REQ_JSON = {"klines_cache/X_15m.json": after_ms}
"""
import json
import os
import sys
import time


def rows_after(path, after_ms):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    after = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(after_ms / 1000))
    return [r for r in data if isinstance(r, dict) and str(r.get("timestamp", "")).replace(" ", "T")[:19] > after]


if __name__ == "__main__":
    base = os.path.expanduser(sys.argv[1])
    req = json.loads(sys.argv[2])
    out = {}
    for rel, after in req.items():
        rows = rows_after(os.path.join(base, rel), after)
        if rows:
            out[rel] = rows
    print(json.dumps(out))

#!/usr/bin/env python3
"""stamp_tripwire - report every kline / NPZ timestamp that is not the canonical UTC stamp (2026-10-28 operator law).

Canonical kline stamp = 27 chars `YYYY-MM-DDTHH:MM:SS.ffffffZ` (UTC). Canonical NPZ `timestamps` = int64 epoch SECONDS. Scans, per host:
  <base>/klines_cache/*.json, klines_cache/tradier/*.json, klines_canonical/*.json  (every "timestamp"/"timestamp_dt" string value, full file)
  <base>/backtest_v8/indicators/*.npz (timestamps dtype/magnitude/order, last 5 rows) and .tail/*.jsonl rows
usage: stamp_tripwire.py [--base DIR] [--json OUT] [--quiet]   exit 1 and an `ALERT` line when anything is non-canonical.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

CANON = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")
FIND = re.compile(r'"(timestamp|timestamp_dt)"\s*:\s*("(?:[^"\\]*)"|-?\d+(?:\.\d+)?|null|NaN)')


def classify(value):
    if value.startswith('"'):
        s = value[1:-1]
        if CANON.match(s):
            return None
        if re.match(r"^\d{4}-\d\d-\d\d$", s):
            return "date-only"
        if re.match(r"^\d{4}-\d\d-\d\d\.0+Z$", s):
            return "date-dot-Z"
        if s == "NaT":
            return "NaT"
        if re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", s):
            return "no-fraction"
        if re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?[+-]00:?00$", s):
            return "utc-offset-not-Z"
        if re.match(r"^\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(\.\d+)?([+-]\d\d:?\d\d)?$", s):
            return "no-Z-or-offset"
        return "other-string"
    return "numeric" if value[0] in "-0123456789" else "null-or-nan"


def scan_json(path):
    kinds = {}
    try:
        text = open(path, "r", errors="replace").read()
    except OSError as exc:
        return {"unreadable": 1}
    for m in FIND.finditer(text):
        k = classify(m.group(2))
        if k:
            if m.group(1) == "timestamp_dt" and k == "null-or-nan":
                continue  # a secondary field that is simply empty carries no wrong stamp; "NaT" strings and offset forms are still reported
            k = ("" if m.group(1) == "timestamp" else "timestamp_dt:") + k
            kinds[k] = kinds.get(k, 0) + 1
    return kinds


def scan_npz(path):
    import numpy as np
    try:
        with np.load(path, allow_pickle=True) as z:
            t = np.asarray(z["timestamps"])
    except Exception as exc:
        return {"unreadable-npz": 1}
    kinds = {}
    if t.dtype.kind not in "iu":
        kinds["npz-ts-dtype-" + t.dtype.kind] = 1
    elif len(t) and (int(t[-1]) > 10**11 or int(t[0]) > 10**11):
        kinds["npz-ts-milliseconds"] = 1
    elif len(t) > 1 and not bool((t[1:] > t[:-1]).all()):
        kinds["npz-ts-not-increasing"] = 1
    return kinds


def scan_tail(path):
    kinds = {}
    for line in open(path, errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            ts = json.loads(line)["timestamps"]
        except Exception:
            kinds["tail-unparseable"] = kinds.get("tail-unparseable", 0) + 1
            continue
        if not isinstance(ts, int) or ts > 10**11:
            kinds["tail-ts-not-epoch-seconds"] = kinds.get("tail-ts-not-epoch-seconds", 0) + 1
    return kinds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.path.expanduser("~/binance-sandbox"))
    ap.add_argument("--json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-npz", action="store_true")
    ap.add_argument("--since-minutes", type=float, default=0, help="only files modified in the last N minutes (the 30-min cron); 0 = every file")
    a = ap.parse_args()
    base = a.base
    t0 = time.time()
    report = {"host": os.uname()[1], "base": base, "at": time.strftime("%FT%TZ", time.gmtime()), "dirs": {}, "offenders": []}
    for rel in ("klines_cache", "klines_cache/tradier", "klines_canonical"):
        files = sorted(glob.glob(os.path.join(base, rel, "*.json")))
        if a.since_minutes:
            files = [f for f in files if time.time() - os.path.getmtime(f) <= a.since_minutes * 60]
        agg, bad_files = {}, 0
        for f in files:
            k = scan_json(f)
            if k:
                bad_files += 1
                for kk, n in k.items():
                    agg[kk] = agg.get(kk, 0) + n
                report["offenders"].append({"file": os.path.relpath(f, base), "kinds": k})
        report["dirs"][rel] = {"files": len(files), "files_with_noncanonical": bad_files, "stamps": agg}
    if not a.no_npz:
        files = sorted(glob.glob(os.path.join(base, "backtest_v8", "indicators", "*.npz")))
        if a.since_minutes:
            files = [f for f in files if time.time() - os.path.getmtime(f) <= a.since_minutes * 60]
        agg, bad_files = {}, 0
        for f in files:
            k = scan_npz(f)
            if k:
                bad_files += 1
                for kk, n in k.items():
                    agg[kk] = agg.get(kk, 0) + n
                report["offenders"].append({"file": os.path.relpath(f, base), "kinds": k})
        tails = sorted(glob.glob(os.path.join(base, "backtest_v8", "indicators", ".tail", "*.jsonl")))
        for f in tails:
            k = scan_tail(f)
            if k:
                bad_files += 1
                for kk, n in k.items():
                    agg[kk] = agg.get(kk, 0) + n
                report["offenders"].append({"file": os.path.relpath(f, base), "kinds": k})
        report["dirs"]["npz+tail"] = {"files": len(files) + len(tails), "files_with_noncanonical": bad_files, "stamps": agg}
    total = sum(d["files_with_noncanonical"] for d in report["dirs"].values())
    report["noncanonical_files"] = total
    report["secs"] = round(time.time() - t0, 1)
    if a.json:
        tmp = a.json + ".tmp"
        json.dump(report, open(tmp, "w"), indent=1)
        os.replace(tmp, a.json)
    if not a.quiet:
        for rel, d in report["dirs"].items():
            print(f"{report['host']} {rel}: files {d['files']} non-canonical files {d['files_with_noncanonical']} stamps {d['stamps']}")
    print(f"{'ALERT' if total else 'OK'} stamp_tripwire host={report['host']} noncanonical_files={total} secs={report['secs']}", flush=True)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())

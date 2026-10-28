#!/usr/bin/env python3
"""kline_sort_instants - put kline rows in chronological order by canonical instant (stable; NO row added or removed).
For stores whose stamps were of mixed forms the file order followed the raw string, not time (W/M files with -04:00 rows, legacy+canonical twin blocks).
Duplicates stay (dropping them is a shrink + an OHLCV-twin decision, see report); twins end up adjacent. Dry run by default; --apply = gzip backup, asserts the same row multiset, atomic write.
usage: kline_sort_instants.py --dir DIR [--apply] [--backup-dir DIR]
"""
import argparse, glob, gzip, json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kline_stamp as K


def key(r):
    try:
        return K.canonical_stamp(r["timestamp"])
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dir", required=True); ap.add_argument("--apply", action="store_true"); ap.add_argument("--backup-dir", default=os.path.expanduser("~/stamp_norm_backup/sort_pre"))
    a = ap.parse_args()
    tot = {"files": 0, "unordered": 0, "fixed": 0, "skipped": 0}
    for f in sorted(glob.glob(os.path.join(a.dir, "*.json"))):
        st0 = os.stat(f)
        try:
            rows = json.load(open(f))
        except Exception:
            continue
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            continue
        tot["files"] += 1
        ks = [key(r) for r in rows]
        if all(ks[i] <= ks[i + 1] for i in range(len(ks) - 1)):
            continue
        tot["unordered"] += 1
        out = [r for _, r in sorted(zip(ks, range(len(rows)), rows), key=lambda t: (t[0], t[1]))] if False else [rows[i] for i in sorted(range(len(rows)), key=lambda i: (ks[i], i))]
        assert len(out) == len(rows)
        print(f"{time.strftime('%FT%TZ', time.gmtime())} {os.uname()[1]} {os.path.basename(f)} rows {len(rows)} reordered {'APPLIED' if a.apply else 'dry'}", flush=True)
        if a.apply:
            os.makedirs(a.backup_dir, exist_ok=True)
            with open(f, "rb") as s, gzip.open(os.path.join(a.backup_dir, f"{os.path.basename(f)}.{int(time.time())}.unsorted.gz"), "wb", compresslevel=1) as d:
                shutil.copyfileobj(s, d)
            if os.stat(f).st_mtime_ns != st0.st_mtime_ns:
                tot["skipped"] += 1
                continue
            tmp = f + f".so{os.getpid()}.tmp"
            with open(tmp, "w") as fh:
                json.dump(out, fh, separators=(",", ":")); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, f); tot["fixed"] += 1
    print(("APPLIED " if a.apply else "DRY ") + json.dumps(tot))

if __name__ == "__main__":
    main()

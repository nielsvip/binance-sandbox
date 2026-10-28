#!/usr/bin/env python3
"""kline_stamp_normalize - rewrite kline store files so every stamp is the canonical UTC Z stamp (see kline_stamp.py).

Default = DRY RUN (counts only). --apply: per file, backup to <backup-dir>/<name>.<ts>.bak, convert (never drops rows unless --drop-day-duplicates),
verify the per-row OHLCV sequence is unchanged apart from dropped duplicates, write atomically, keep mtime-guard so a concurrent writer wins (file skipped).
usage: kline_stamp_normalize.py --dir klines_cache/tradier [--glob '*_D.json'] [--apply] [--drop-day-duplicates] [--backup-dir DIR]
WARNING: --drop-day-duplicates shrinks a store: klines_guard refuses shrinking writes and klines_vault --enforce (S1, */2) SIGKILLs writers and restores the sealed
generation when a live file has fewer rows than its seal. Re-seal (klines_vault --seal) / pause enforce first. Convert-only keeps the row count and is vault-safe.
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kline_stamp as K


def ohlcv(row):
    return tuple(row.get(k) for k in ("open", "high", "low", "close", "volume"))


def process(path, apply, drop, backup_dir):
    st0 = os.stat(path)
    rows = json.load(open(path))
    if not isinstance(rows, list):
        return None
    out, stats = K.normalize_rows(rows, drop_day_duplicates=drop)
    if stats["converted"] == 0 and stats["dropped"] == 0 and stats["secondary_nulled"] == 0:
        return {"touched": 0}
    kept = [r for r in rows if not (drop and isinstance(r, dict) and K.DAY_ONLY.match(str(r.get("timestamp", ""))) and False)]
    if not drop:
        assert [ohlcv(r) for r in out] == [ohlcv(r) for r in rows], "OHLCV sequence changed"
    else:
        want = [ohlcv(r) for r in rows]
        got = [ohlcv(r) for r in out]
        it = iter(want)
        assert all(any(g == w for w in it) for g in got), "OHLCV subsequence violated"
    result = {"touched": 1, **stats}
    if apply:
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(path, os.path.join(backup_dir, f"{os.path.basename(path)}.{int(time.time())}.bak"))
        if os.stat(path).st_mtime_ns != st0.st_mtime_ns:
            return {"touched": 0, "skipped_writer_active": 1}
        tmp = path + f".norm{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump(out, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--glob", default="*.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--drop-day-duplicates", action="store_true")
    ap.add_argument("--backup-dir", default=None)
    a = ap.parse_args()
    backup = a.backup_dir or os.path.join(a.dir, ".stamp_normalize_backup")
    total = {"files": 0, "touched": 0, "converted": 0, "dropped": 0, "secondary_nulled": 0, "skipped_writer_active": 0, "errors": 0}
    for path in sorted(glob.glob(os.path.join(a.dir, a.glob))):
        total["files"] += 1
        try:
            r = process(path, a.apply, a.drop_day_duplicates, backup)
        except Exception as exc:
            total["errors"] += 1
            print("ERROR", path, type(exc).__name__, exc, flush=True)
            continue
        for k, v in (r or {}).items():
            if k in total:
                total[k] += v
    print(("APPLIED " if a.apply else "DRY-RUN ") + json.dumps(total))


if __name__ == "__main__":
    main()

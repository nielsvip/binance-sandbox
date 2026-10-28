#!/usr/bin/env python3
"""kline_restore_normalized - undo the row growth klines_append caused AFTER a store was convert-normalised (2026-10-28 incident).

For each file that has a first-pass gzip backup (the ORIGINAL rows, written by kline_stamp_normalize --apply) the correct content is
    normalize_rows(original)  +  every row of the current file whose stamp is not already in that set (bars appended since)
i.e. the original row count (>= the sealed row count klines_vault/klines_guard hold, so no shrink alarm vs the seal) with only the genuine new bars added.
Dry run by default; --apply backs up the current file (gzip) and rewrites atomically (skips a file whose mtime moved).
usage: kline_restore_normalized.py --dir DIR --orig-backups DIR [--apply] [--shard i/n] [--backup-dir DIR]
"""
import argparse, glob, gzip, json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kline_stamp as K


def rebuild(current, original):
    base, _ = K.normalize_rows(original)
    have = {r.get("timestamp") for r in base if isinstance(r, dict)}
    current, _ = K.normalize_rows(current)  # a vault restore unions by RAW stamp and brings legacy-form twins back: compare instants, not strings
    extra, seen = [], set()
    for r in current:
        t = r.get("timestamp") if isinstance(r, dict) else None
        if t is None or t in have or t in seen:
            continue
        seen.add(t)
        extra.append(r)
    out = base + extra
    if extra:
        out.sort(key=lambda r: r.get("timestamp", ""))
    return out, len(extra)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--orig-backups", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--backup-dir", default=os.path.expanduser("~/stamp_norm_backup/restore_pre"))
    a = ap.parse_args()
    i_, n_ = (int(x) for x in a.shard.split("/"))
    tot = {"files": 0, "grown": 0, "rows_removed": 0, "new_bars_kept": 0, "skipped": 0}
    for f in sorted(glob.glob(os.path.join(a.dir, "*.json"))):
        name = os.path.basename(f)
        if n_ > 1 and (sum(name.encode()) % n_) != i_:
            continue
        bks = sorted(glob.glob(os.path.join(a.orig_backups, name + ".*.bak.gz")))
        if not bks:
            continue
        tot["files"] += 1
        st0 = os.stat(f)
        try:
            cur = json.load(open(f)); orig = json.load(gzip.open(bks[0]))
        except Exception:
            continue
        out, kept_new = rebuild(cur, orig)
        if len(out) >= len(cur):
            continue
        tot["grown"] += 1; tot["rows_removed"] += len(cur) - len(out); tot["new_bars_kept"] += kept_new
        print(f"{time.strftime('%FT%TZ', time.gmtime())} {os.uname()[1]} {name} rows {len(cur)} -> {len(out)} (original {len(orig)}, new bars kept {kept_new}) {'APPLIED' if a.apply else 'dry'}", flush=True)
        if a.apply:
            os.makedirs(a.backup_dir, exist_ok=True)
            with open(f, "rb") as s, gzip.open(os.path.join(a.backup_dir, f"{name}.{int(time.time())}.grown.gz"), "wb", compresslevel=1) as d:
                shutil.copyfileobj(s, d)
            if os.stat(f).st_mtime_ns != st0.st_mtime_ns:
                tot["skipped"] += 1
                continue
            tmp = f + f".rs{os.getpid()}.tmp"
            with open(tmp, "w") as fh:
                json.dump(out, fh, separators=(",", ":")); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, f)
    print(("APPLIED " if a.apply else "DRY ") + json.dumps(tot))


if __name__ == "__main__":
    main()

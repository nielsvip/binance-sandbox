#!/usr/bin/env python3
"""kline_dedupe_instants - remove the duplicate bars that appear when a canonical-stamp store is merged (by raw stamp string) with a source that still
holds the same bars as "+00:00" / date-only rows (2026-10-28 incident: BARDUSDT_15m 38.9k -> 75.0k rows minutes after stores were normalised).
A row is dropped only if (a) its ORIGINAL stamp was non-canonical, (b) a canonical-format row with the same instant exists. OHLCV differences between the pair are
counted (the canonical-format row is kept). Dry run by default; --apply backs up (gzip) and rewrites atomically, skipping files whose mtime moved.
usage: kline_dedupe_instants.py --dir DIR [--since-minutes N] [--apply] [--backup-dir DIR]
"""
import argparse, glob, gzip, json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kline_stamp as K


def dedupe(rows):
    canon_instants = {r["timestamp"] for r in rows if isinstance(r, dict) and K.is_canonical(r.get("timestamp"))}
    seen_conv, out, dropped, differ = set(), [], 0, 0
    by_stamp = {r["timestamp"]: r for r in rows if isinstance(r, dict) and K.is_canonical(r.get("timestamp"))}
    for r in rows:
        if not isinstance(r, dict) or "timestamp" not in r or K.is_canonical(r["timestamp"]):
            out.append(r)
            continue
        try:
            c = K.canonical_stamp(r["timestamp"])
        except ValueError:
            out.append(r)
            continue
        if c in canon_instants or c in seen_conv:
            dropped += 1
            twin = by_stamp.get(c)
            if twin is not None and any(twin.get(k) != r.get(k) for k in ("open", "high", "low", "close", "volume")):
                differ += 1
            continue
        seen_conv.add(c)
        out.append(r)
    return out, dropped, differ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--since-minutes", type=float, default=0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--shard", default="0/1", help="i/n: only files whose name hash %% n == i")
    ap.add_argument("--backup-dir", default=None)
    a = ap.parse_args()
    bdir = a.backup_dir or os.path.expanduser("~/stamp_norm_backup/dedupe")
    tot = {"files": 0, "with_dups": 0, "dropped": 0, "ohlcv_differs": 0, "skipped": 0}
    for f in sorted(glob.glob(os.path.join(a.dir, "*.json"))):
        if a.since_minutes and time.time() - os.path.getmtime(f) > a.since_minutes * 60:
            continue
        i_, n_ = (int(x) for x in a.shard.split("/"))
        if n_ > 1 and (sum(os.path.basename(f).encode()) % n_) != i_:
            continue
        tot["files"] += 1
        st0 = os.stat(f)
        try:
            rows = json.load(open(f))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        out, dropped, differ = dedupe(rows)
        if not dropped:
            continue
        tot["with_dups"] += 1; tot["dropped"] += dropped; tot["ohlcv_differs"] += differ
        print(f"{time.strftime('%FT%TZ', time.gmtime())} {os.uname()[1]} {os.path.basename(f)} rows {len(rows)} -> {len(out)} dropped {dropped} ohlcv_differs {differ} {'APPLIED' if a.apply else 'dry'}", flush=True)
        if a.apply:
            os.makedirs(bdir, exist_ok=True)
            with open(f, "rb") as s, gzip.open(os.path.join(bdir, f"{os.path.basename(f)}.{int(time.time())}.dup.gz"), "wb", compresslevel=1) as d:
                shutil.copyfileobj(s, d)
            if os.stat(f).st_mtime_ns != st0.st_mtime_ns:
                tot["skipped"] += 1
                continue
            tmp = f + f".dd{os.getpid()}.tmp"
            with open(tmp, "w") as fh:
                json.dump(out, fh, separators=(",", ":")); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, f)
    print(("APPLIED " if a.apply else "DRY ") + json.dumps(tot))

if __name__ == "__main__":
    main()

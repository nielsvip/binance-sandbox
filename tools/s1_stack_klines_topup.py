#!/usr/bin/env python3
"""s1_stack_klines_topup.py - runs ON S1 (cron */1). Keeps the S1 crypto producer's klines_cache from going stale while
its own REST feed is banned.

USER 2026-10-22: the snapshot must NEVER be >90 s stale and S1 is the fallback producer. MEASURED 2026-10-22 05:01Z:
S1's ez_indicators wrote a snapshot 17 s old, but its 15m bars were 5.5 h old (median) because the S1 stack's ez_klines
sat in "RATE-LIMIT PAUSE" (data/ez_klines_ban_state.json ban_until_ms=1792657079367 -> 08:17:59Z): Binance banned S1's
default egress 157.180.125.52 (curl from that address: HTTP 418 "banned until 1792657079367"; from the second address
5.75.211.216: HTTP 200), and ez_klines pauses ALL REST until the file's time. The stack's 3m/1m come from websockets and
stayed live, 15m/1h/4h/D come ONLY from REST - so a "fresh" S1 file carried 5 h old 15m/1h/4h indicators and the Mac's
relative content gate (scripts/s1_snapshot_fresh_gate.py) correctly refused it.

S1's live ~/binance/klines_cache is current (fed by the Mac pipeline: 15m last bar 04:45Z at 05:01Z). This copies
bars from there into the stack's own klines_cache:
  * MERGE BY TIMESTAMP, never replace the file: dst bars older than the first src bar are kept untouched (depth is never
    lost - USER 2026-10-21 "NOTHING destroys klines"); src bars with a timestamp >= dst's newest overwrite/extend (the
    forming bar is re-written each pass, so a half-formed bar can never be left behind as history).
  * only when src has a strictly newer bar OR a changed forming bar; never writes an older tail over a newer one.
  * atomic tmp + os.replace; a source/dest that is unreadable or not a list of dicts with ISO "timestamp" is skipped.
  * touches only the TFs listed (default 15m,1h,4h,D); 1m/3m are live from websockets.
Idempotent, safe to run while ez_klines/ez_indicators run (they re-read by mtime). Logs one line per pass.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path


def load(path):
    try:
        with open(path, "rb") as handle:
            rows = json.loads(handle.read())
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list) or not rows or not isinstance(rows[-1], dict) or "timestamp" not in rows[-1]:
        return None
    return rows


def key(row):
    return str(row.get("timestamp", ""))[:19].replace(" ", "T") if isinstance(row, dict) else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.expanduser("~/binance/klines_cache"))
    ap.add_argument("--dst", default=os.path.expanduser("~/binance/ez_s1_stack/klines_cache"))
    ap.add_argument("--tfs", default="15m,1h,4h,D")
    ap.add_argument("--max-seconds", type=float, default=50.0)
    args = ap.parse_args()
    src_dir, dst_dir = Path(args.src), Path(args.dst)
    tfs = [t for t in args.tfs.split(",") if t]
    started = time.time()
    scanned = updated = bars_added = skipped = 0
    for tf in tfs:
        for dst in sorted(dst_dir.glob(f"*_{tf}.json")):
            if time.time() - started > args.max_seconds:
                print(f"{time.strftime('%FT%TZ', time.gmtime())} topup: time budget hit after {scanned} files", flush=True)
                break
            src = src_dir / dst.name
            try:
                s_mtime, d_mtime = src.stat().st_mtime, dst.stat().st_mtime
            except OSError:
                continue
            scanned += 1
            if s_mtime <= d_mtime:
                continue
            s_rows, d_rows = load(src), load(dst)
            if s_rows is None or d_rows is None:
                skipped += 1
                continue
            d_last = key(d_rows[-1])
            s_last = key(s_rows[-1])
            if not s_last or not d_last or s_last < d_last:
                continue
            tail = [r for r in s_rows if isinstance(r, dict) and key(r) >= d_last]
            if not tail:
                continue
            if s_last == d_last and tail[-1] == d_rows[-1]:
                continue
            kept = [r for r in d_rows if key(r) < d_last]
            merged = kept + tail
            tmp = dst.with_name(f".{dst.name}.topup.{os.getpid()}.tmp")
            with open(tmp, "w") as handle:
                json.dump(merged, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, dst)
            updated += 1
            bars_added += len(merged) - len(d_rows)
    print(f"{time.strftime('%FT%TZ', time.gmtime())} topup: scanned={scanned} updated={updated} bars_added={bars_added} skipped_unreadable={skipped} in {time.time() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

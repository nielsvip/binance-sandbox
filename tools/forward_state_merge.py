#!/usr/bin/env python3
"""tools/forward_state_merge.py — USER 2026-10-27: the forward-tested per-sym_side state
computed on S1/S2/S5/S6 arrives on the Mac via rsync into data/forward_state_inbox/<host>/,
but nothing was copying it into data/forward_state/, the canonical location every consumer
(board_state_converge tier 2, VEC_BACKSTOP_ENABLED, parity_monitor.py) actually reads. That gap
meant every parity mechanism ran on an empty/stale directory despite fresh data existing.

Pure copy, no computation: for each sym_side, the freshest file across all host inboxes wins.
Read-only on the inboxes, writes only into data/forward_state/. No execute_now, no new decisions.

Usage: python3 tools/forward_state_merge.py [--loop SECONDS]
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
INBOX_HOSTS = ("s1", "s2", "s5", "s6")
DEST = BASE / "data" / "forward_state"


def merge_once():
    DEST.mkdir(parents=True, exist_ok=True)
    best = {}  # filename -> (mtime, path)
    for host in INBOX_HOSTS:
        src_dir = BASE / "data" / "forward_state_inbox" / host
        if not src_dir.is_dir():
            continue
        for fp in src_dir.glob("*.json"):
            try:
                mtime = fp.stat().st_mtime
            except OSError:
                continue
            cur = best.get(fp.name)
            if cur is None or mtime > cur[0]:
                best[fp.name] = (mtime, fp)

    copied, skipped_same, failed = 0, 0, 0
    for name, (mtime, src) in best.items():
        dest = DEST / name
        try:
            if dest.exists() and dest.stat().st_mtime >= mtime:
                skipped_same += 1
                continue
            # validate it's real JSON before trusting it as a live source
            json.loads(src.read_text())
            shutil.copy2(src, dest)
            copied += 1
        except Exception:
            failed += 1
    return {"ts": time.time(), "candidates": len(best), "copied": copied,
            "already_fresh": skipped_same, "failed": failed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, default=0.0, help="repeat every N seconds; 0 = run once")
    args = ap.parse_args()
    while True:
        r = merge_once()
        print(f"[forward_state_merge {time.strftime('%FT%TZ', time.gmtime(r['ts']))}] "
              f"candidates={r['candidates']} copied={r['copied']} "
              f"already_fresh={r['already_fresh']} failed={r['failed']}")
        if args.loop <= 0:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())

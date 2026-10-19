#!/usr/bin/env python3
"""eval_cache_merge_master — merge staged S2 bench caches into the ext-drive master.

USER 2026-10-19: S1's disk must stay free for backtests while every eval row stays
available for self-learning (v17_priors_mine --db <master>).

Master resolution (in order): $V15_EVAL_MASTER, then the registry path
/Volumes/TOSHIBA_EXT/binance_archive/eval_cache/eval_cache_v1_master.db.
Drive absent -> exit 0 quietly; staging accumulates, nothing is lost, merge runs
when the drive appears. Merged staging files are deleted (master + S2 retain them).
Merge itself is INSERT OR IGNORE (identical keys are identical results by construction).
"""
import glob
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE = os.path.join(BASE, "data", "eval_cache_incoming")
LOG = "/tmp/eval_cache_merge.log"
MASTER = (os.environ.get("V15_EVAL_MASTER")
          or "/Volumes/TOSHIBA_EXT/binance_archive/eval_cache/eval_cache_v1_master.db")


def log(msg):
    with open(LOG, "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")


def main():
    srcs = sorted(glob.glob(os.path.join(STAGE, "*", "*", "eval_cache.db")))
    if not os.path.exists(MASTER):
        log(f"SKIP master absent ({MASTER}); staging holds {len(srcs)} dbs")
        return 0
    if not srcs:
        log("SKIP nothing staged")
        return 0
    t0 = time.time()
    cmd = [sys.executable, os.path.join(BASE, "tools", "eval_cache_rebuild.py"),
           "--merge-native", MASTER] + [x for s in srcs for x in ("--from", s)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    log(f"MERGE rc={r.returncode} srcs={len(srcs)} secs={int(time.time()-t0)}")
    for line in (r.stdout or "").splitlines()[-4:]:
        log(f"  {line}")
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines()[-4:]:
            log(f"  ERR {line}")
        return 1
    for s in srcs:
        try:
            os.remove(s)
        except OSError as exc:
            log(f"  prune {s} failed: {exc}")
    log(f"PRUNED {len(srcs)} staged dbs")
    return 0


if __name__ == "__main__":
    sys.exit(main())

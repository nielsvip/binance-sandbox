#!/usr/bin/env python3
"""USDC venue repair (ON s1): replace USDT-mislabeled USDC cache files with true-venue fapi bars.

2026-10-10 finding: klines_cache + klines_cache_gateway *USDC_*.json contain USDT-perp klines (hybrid-era fossils;
proven: local ENAUSDC == fapi ENAUSDT bar-for-bar, true fapi ENAUSDC differs 40x in volume). The live collector now
appends true-USDC (verbatim fetch), so only pre-~Oct-05 spans are wrong. s5 (clean egress) fetched true-venue history;
this script swaps it in (shelf + gateway), backs up the fossils, and drops forming bars (live re-adds them closed).
usage: usdc_venue_repair.py --src DIR --symbols-file F --tfs 15m,1h,4h [--write]
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "D": 86400}
DIRS = ["klines_cache", "klines_cache_gateway"]


def bar_ts(v):
    return int(datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--tfs", default="15m,1h,4h")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    syms = [x.strip() for x in open(a.symbols_file) if x.strip()]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    code = 0
    for sym in syms:
        for tf in a.tfs.split(","):
            step = TF_SEC[tf]
            cutoff = (int(time.time()) // step) * step - step
            sp = os.path.join(a.src, f"{sym}_{tf}.json")
            if not os.path.exists(sp):
                print(f"{sym} {tf}: NO SRC FILE — skipped")
                code = 1
                continue
            bars = [b for b in json.load(open(sp)) if bar_ts(b["timestamp"]) <= cutoff]
            print(f"{sym} {tf}: src={len(bars)} closed bars {bars[0]['timestamp'][:16]} > {bars[-1]['timestamp'][:16]}")
            for d in DIRS:
                lp = os.path.join(ROOT, d, f"{sym}_{tf}.json")
                n_local = len(json.load(open(lp))) if os.path.exists(lp) else 0
                print(f"  {d}: local={n_local} -> src={len(bars)}")
                if a.write:
                    if os.path.exists(lp):
                        shutil.copy2(lp, os.path.join(ROOT, "backups", f"usdc_fossil_{sym}_{tf}_{d}_{stamp}.json"))
                    tmp = lp + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(bars, f)
                    os.replace(tmp, lp)
                    print(f"  {d}: REPLACED (+backup)")
    return code


if __name__ == "__main__":
    sys.exit(main())

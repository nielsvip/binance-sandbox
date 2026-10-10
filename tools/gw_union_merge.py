#!/usr/bin/env python3
"""Union-merge peer-fetched gateway klines into S1's klines_cache_gateway (additive only).

usage: gw_union_merge.py --src DIR --symbols-file F --tfs 15m,1h,4h [--write]
  --src: dir holding {SYM}_{TF}.json from the peer host (fetched on clean egress).
  Merges per (sym,tf): S1 bars win on overlap (same authentic source), peer bars fill gaps.
  Verifies overlap equality (allclose 1e-9); mismatch aborts that file loudly. Backs up originals.
  Default = report only; --write performs the merge.
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GW = os.path.join(ROOT, "klines_cache_gateway")


def load(path):
    if not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path))
    except Exception:
        return {}
    out = {}
    for b in data:
        try:
            out[b["timestamp"][:19]] = (b["timestamp"], float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), float(b["volume"]))
        except Exception:
            continue
    return out


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
            lp = os.path.join(GW, f"{sym}_{tf}.json")
            pp = os.path.join(a.src, f"{sym}_{tf}.json")
            local = load(lp)
            peer = load(pp)
            if not peer:
                print(f"{sym} {tf}: NO PEER FILE — skipped")
                code = 1
                continue
            shared = sorted(set(local) & set(peer))
            if shared:
                d = 0.0
                for j in range(1, 6):
                    x = np.array([local[k][j] for k in shared])
                    y = np.array([peer[k][j] for k in shared])
                    d = max(d, float(np.max(np.abs(x - y) / np.maximum(np.abs(y), 1e-12))))
                status = "OK" if d < 1e-9 else "MISMATCH-ABORT"
                if d >= 1e-9:
                    code = 1
                    print(f"{sym} {tf}: overlap={len(shared)} maxreldiff={d:.2e} {status}")
                    continue
            else:
                status = "no-overlap"
            newk = sorted(set(peer) - set(local))
            print(f"{sym} {tf}: local={len(local)} peer={len(peer)} overlap={len(shared)} new={len(newk)} {status}")
            if a.write and newk:
                if os.path.exists(lp):
                    shutil.copy2(lp, os.path.join(ROOT, "backups", f"gw_union_{sym}_{tf}_{stamp}.json"))
                merged = dict(local)
                for k in newk:
                    merged[k] = peer[k]
                rows = [{"timestamp": v[0], "open": v[1], "high": v[2], "low": v[3], "close": v[4], "volume": v[5]} for k, v in sorted(merged.items())]
                tmp = lp + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(rows, f)
                os.replace(tmp, lp)
                print(f"{sym} {tf}: WROTE {len(rows)} bars (+{len(newk)})")
    return code


if __name__ == "__main__":
    sys.exit(main())

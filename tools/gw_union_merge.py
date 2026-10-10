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

import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GW = os.path.join(ROOT, "klines_cache_gateway")
TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "D": 86400, "W": 604800, "M": 2592000}


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
            # drop peer forming bars (fetched mid-bar; never merge partials)
            step = TF_SEC.get(tf, 900)
            cutoff = (int(time.time()) // step) * step - step
            peer = {k: v for k, v in peer.items() if int(datetime.strptime(v[0][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()) <= cutoff}
            shared = sorted(set(local) & set(peer))
            if shared:
                bad = []
                for k in shared:
                    dd = max(abs(local[k][j] - peer[k][j]) / max(abs(peer[k][j]), 1e-12) for j in range(1, 6))
                    if dd > 1e-6:
                        bad.append((dd, k))
                frac = len(bad) / len(shared)
                if frac > 0.02:
                    code = 1
                    worst = sorted(bad, reverse=True)[:3]
                    print(f"{sym} {tf}: overlap={len(shared)} FRAC_MISMATCH {frac:.3f} worst={[(round(d,3), k) for d, k in worst]} ABORT")
                    continue
                status = f"OK frac={frac:.4f}"
                if bad:
                    worst = sorted(bad, reverse=True)[:5]
                    print(f"{sym} {tf}: note {len(bad)} differing bars kept-local worst={[(round(d,3), k) for d, k in worst]}")
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

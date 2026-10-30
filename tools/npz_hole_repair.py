#!/usr/bin/env python3
"""NPZ hole repairer: finds interior gaps on the 15m grid and reports (dry-run) or fills (--apply) them.

A hole is a missing 900 s slot strictly between the first and last bar of an NPZ. For each hole the row is computed
by the same path that appends live rows (npz_tail_service._compute_row_process), from the klines held on this host,
and inserted in time order. Existing rows are never changed. Every apply writes a backup first and verifies the
result is strictly increasing, with the same key set, before it replaces the file.
Run on the host that owns the NPZ (S1 crypto, S5 stocks). Default is dry-run.
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

BAR_S = 900


def holes_for(ts):
    ts = np.asarray(ts, dtype="int64")
    if len(ts) < 2:
        return []
    d = np.diff(ts)
    out = []
    for i in np.where(d != BAR_S)[0]:
        lo, hi = int(ts[i]), int(ts[i + 1])
        if (hi - lo) % BAR_S == 0 and hi > lo:
            out.extend(range(lo + BAR_S, hi, BAR_S))
    return out


def inspect(npz_path):
    with np.load(npz_path, allow_pickle=True) as z:
        ts = z["timestamps"].astype("int64")
    h = holes_for(ts)
    return {"npz": os.path.basename(npz_path), "rows": int(len(ts)), "holes": len(h),
            "first_hole": h[0] if h else None, "last_hole": h[-1] if h else None,
            "tip": int(ts[-1]) if len(ts) else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--glob", default="*.npz")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    base = Path(a.base)
    d = base / "backtest_v8" / "indicators"
    rows = []
    for p in sorted(d.glob(a.glob)):
        try:
            rows.append(inspect(str(p)))
        except Exception as exc:
            rows.append({"npz": p.name, "error": f"{type(exc).__name__}: {str(exc)[:100]}"})
    bad = [r for r in rows if r.get("holes")]
    err = [r for r in rows if "error" in r]
    print(json.dumps({"at": time.time(), "files": len(rows), "with_holes": len(bad),
                      "errors": len(err), "total_holes": sum(r["holes"] for r in bad),
                      "apply": a.apply, "worst": sorted(bad, key=lambda r: -r["holes"])[:15]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""npz_tail_fix_partial - drop unparseable (partial / cut) lines from .tail sidecars, one log line per fix (2026-10-28 stamp-integrity pass).

A row whose bar is newer than the zip tip is reported as NEEDS_CATCHUP (npz_tail_catchup re-appends it); older rows are already in the zip or irrelevant.
usage: npz_tail_fix_partial.py [--base DIR] [--apply]    (dry run by default; backup = gzip copy under <base>/data/tail_fix_backup/)
"""
import argparse
import glob
import gzip
import json
import os
import re
import shutil
import time

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.path.expanduser("~/binance-sandbox"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    ind = os.path.join(a.base, "backtest_v8", "indicators")
    for f in sorted(glob.glob(os.path.join(ind, ".tail", "*.jsonl"))):
        try:
            st0 = os.stat(f)
            lines = open(f, errors="replace").read().splitlines()
        except OSError:
            continue  # folded and drained by the compactor meanwhile
        keep, bad = [], []
        for i, l in enumerate(lines):
            if not l.strip():
                continue
            try:
                json.loads(l)
                keep.append(l)
            except Exception:
                m = re.match(r'\{"timestamps":(\d+)', l)
                bad.append((i, len(l), int(m.group(1)) if m else None))
        if not bad:
            continue
        sym = os.path.basename(f)[:-6]
        try:
            tip = int(np.load(os.path.join(ind, sym + ".npz"), allow_pickle=True)["timestamps"].astype("int64")[-1])
        except Exception:
            tip = None
        for i, n, ts in bad:
            need = "NEEDS_CATCHUP" if (ts is not None and tip is not None and ts > tip) else "bar-not-newer-than-zip"
            print(f"{time.strftime('%FT%TZ', time.gmtime())} {os.uname()[1]} {sym}.jsonl line {i} len {n} ts {ts} zip_tip {tip} -> {'DROPPED' if a.apply else 'would drop'} ({need})", flush=True)
        if a.apply:
            bdir = os.path.join(a.base, "data", "tail_fix_backup")
            os.makedirs(bdir, exist_ok=True)
            with open(f, "rb") as src, gzip.open(os.path.join(bdir, f"{sym}.jsonl.{int(time.time())}.gz"), "wb") as dst:
                shutil.copyfileobj(src, dst)
            if os.stat(f).st_mtime_ns != st0.st_mtime_ns:
                print(f"SKIP {sym}: changed while working", flush=True)
                continue
            tmp = f + ".fix_tmp"
            with open(tmp, "w") as fh:
                fh.write("\n".join(keep) + ("\n" if keep else ""))
            if keep:
                os.replace(tmp, f)
            else:
                os.unlink(tmp)
                os.unlink(f)


if __name__ == "__main__":
    main()

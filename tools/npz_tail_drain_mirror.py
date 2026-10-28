#!/usr/bin/env python3
"""npz_tail_drain_mirror - S2/S6 only: drop mirrored .tail rows the synced zip already holds (no compute, no zip write).

S1/S5 mirror each appended .tail/<SYM>.jsonl by scp and later fold it into their zip, which sync_indicators.sh ships. The mirror copy is never
drained there, so it grows (ZILUSDT 147 KB) and every merged-view read re-parses it. A row with timestamps <= the zip's last bar is redundant.
Untouched while the file changed in the last 120 s (the scp writer) or if its mtime moved while we worked.
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np


def drain(base):
    ind = Path(base) / "backtest_v8" / "indicators"
    out = {"files": 0, "drained": 0, "trimmed": 0, "kept": 0}
    for tail in sorted((ind / ".tail").glob("*.jsonl")):
        out["files"] += 1
        try:
            st = tail.stat()
            if time.time() - st.st_mtime < 120:
                out["kept"] += 1
                continue
            npz = ind / (tail.stem + ".npz")
            if not npz.is_file():
                continue
            with np.load(str(npz), allow_pickle=True) as z:
                zip_tip = int(np.asarray(z["timestamps"]).astype("int64")[-1])
            keep, lines = [], tail.read_text().splitlines()
            for line in lines:
                if not line.strip():
                    continue
                try:
                    ts = int(json.loads(line)["timestamps"])
                except Exception:
                    keep.append(line)
                    continue
                if ts > zip_tip:
                    keep.append(line)
            if len(keep) == len([l for l in lines if l.strip()]):
                out["kept"] += 1
                continue
            if tail.stat().st_mtime_ns != st.st_mtime_ns:
                continue
            if not keep:
                tail.unlink()
                out["drained"] += 1
            else:
                tmp = tail.with_name(tail.name + ".drain_tmp")
                tmp.write_text("\n".join(keep) + "\n")
                os.replace(str(tmp), str(tail))
                out["trimmed"] += 1
        except Exception as exc:
            print(f"drain error {tail.name}: {type(exc).__name__}: {exc}", flush=True)
    return out


if __name__ == "__main__":
    print(time.strftime("%FT%TZ", time.gmtime()), json.dumps(drain(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/binance-sandbox"))), flush=True)

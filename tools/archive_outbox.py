#!/usr/bin/env python3
"""S1 side of the NPZ archive append job.

Exports, per crypto symbol, only the bars added since the last export into archive_outbox/pending/<SYM>__<tip>.npz.
State lives in archive_outbox/exported/<SYM>.json (last exported tip). A pending delta is deleted only after the Mac
has acknowledged it by writing archive_outbox/acks/<SYM>.json with a tip at or beyond the delta's tip.
Nothing is trimmed here: the 60-day window on S1 is a separate step.
Run: archive_outbox.py --loop (every INTERVAL_S) or --once.
"""
import argparse
import glob
import json
import os
import time
from pathlib import Path

import numpy as np

ROOT = Path(os.environ.get("ARCHIVE_BASE", str(Path.home() / "binance-sandbox")))
SRC = ROOT / "backtest_v8" / "indicators"
OUT = ROOT / "archive_outbox"
INTERVAL_S = int(os.environ.get("ARCHIVE_INTERVAL_S", "60"))


def _json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def export_symbol(npz_path):
    sym = Path(npz_path).stem
    state_path = OUT / "exported" / f"{sym}.json"
    last = int(_json(state_path, {}).get("tip", 0))
    with np.load(npz_path, allow_pickle=True) as data:
        ts = data["timestamps"].astype("int64")
        if len(ts) == 0 or int(ts[-1]) <= last:
            return None
        keep = np.where(ts > last)[0]
        n = len(ts)
        payload = {}
        for key in data.files:
            arr = data[key]
            payload[key] = arr[keep] if getattr(arr, "ndim", 0) >= 1 and len(arr) == n else arr
    tip = int(ts[-1])
    pending = OUT / "pending" / f"{sym}__{tip}.npz"
    pending.parent.mkdir(parents=True, exist_ok=True)
    tmp = pending.with_name("." + pending.name + ".tmp")
    with open(tmp, "wb") as fh:
        np.savez(fh, **payload)
    os.replace(tmp, pending)
    _write_json_atomic(state_path, {"tip": tip, "rows_exported": int(len(keep)), "at": time.time()})
    return {"symbol": sym, "rows": int(len(keep)), "tip": tip}


def prune_acked():
    removed = 0
    for pending in glob.glob(str(OUT / "pending" / "*.npz")):
        name = Path(pending).stem
        sym, tip = name.rsplit("__", 1)
        acked = int(_json(OUT / "acks" / f"{sym}.json", {}).get("tip", 0))
        if int(tip) <= acked:
            os.remove(pending)
            removed += 1
    return removed


def run_once():
    done = []
    for npz in sorted(glob.glob(str(SRC / "*.npz"))):
        try:
            res = export_symbol(npz)
        except Exception as exc:
            res = {"symbol": Path(npz).stem, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        if res:
            done.append(res)
    pruned = prune_acked()
    report = {"at": time.time(), "exported": len(done), "pruned": pruned, "items": done[:20]}
    _write_json_atomic(OUT / "report.json", report)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    if a.once or not a.loop:
        print(json.dumps(run_once()))
        return 0
    while True:
        try:
            print(json.dumps({k: v for k, v in run_once().items() if k != "items"}), flush=True)
        except Exception as exc:
            print(f"pass failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    raise SystemExit(main())

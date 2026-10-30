#!/usr/bin/env python3
"""Mac side of the NPZ archive append job: merges S1 deltas into the WD25 archive.

Pulls archive_outbox/pending/ from S1 (over the s1-int tunnel, falling back to s1-pub), merges each delta into
<ARCHIVE>/<SYM>.npz with a temp file and os.replace (never a partial file under a real name), verifies the result
loads with the delta tip, and only then acknowledges the delta on S1 (archive_outbox/acks/<SYM>.json).
Reports any symbol whose archive tip is behind S1's exported tip.
"""
import argparse
import glob
import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np

ARCHIVE = Path(os.environ.get("ARCHIVE_DIR", "/Volumes/WD25/binance_npz_archive/crypto"))
STAGE = Path(os.environ.get("ARCHIVE_STAGE", str(Path.home() / "binance_archive_stage")))
S1_OUT = "/home/niels/binance-sandbox/archive_outbox"
HOSTS = ["s1-int", "s1-pub"]
SSH = ["-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10", "-o", "ControlMaster=no", "-o", "ControlPath=none"]


def pull_pending():
    STAGE.mkdir(parents=True, exist_ok=True)
    for host in HOSTS:
        r = subprocess.run(["rsync", "-a", "--timeout=60", "-e", " ".join(["ssh", *SSH]),
                            f"{host}:{S1_OUT}/pending/", str(STAGE) + "/"], capture_output=True, text=True)
        if r.returncode == 0:
            return host
    return None


def s1_write(relpath, text):
    for host in HOSTS:
        r = subprocess.run(["ssh", *SSH, host, f"mkdir -p {S1_OUT}/acks && cat > {S1_OUT}/{relpath}.tmp && mv {S1_OUT}/{relpath}.tmp {S1_OUT}/{relpath}"],
                           input=text, capture_output=True, text=True)
        if r.returncode == 0:
            return True
    return False


def merge_one(delta_path, sym, tip):
    target = ARCHIVE / f"{sym}.npz"
    with np.load(delta_path, allow_pickle=True) as d:
        delta = {k: d[k] for k in d.files}
    dts = delta["timestamps"].astype("int64")
    if np.any(np.diff(dts) <= 0) or int(dts[-1]) != tip:
        return "bad_delta"
    if target.exists():
        with np.load(target, allow_pickle=True) as b:
            base = {k: b[k] for k in b.files}
        if set(base) != set(delta):
            return "key_mismatch"
        bts = base["timestamps"].astype("int64")
        if int(bts[-1]) >= tip:
            return "already_current"
        keep = np.where(dts > int(bts[-1]))[0]
        merged = {}
        for k in base:
            b_arr, d_arr = base[k], delta[k]
            if getattr(b_arr, "ndim", 0) >= 1 and len(b_arr) == len(bts) and len(d_arr) == len(dts):
                merged[k] = np.concatenate([b_arr, d_arr[keep]])
            else:
                merged[k] = b_arr
    else:
        merged = delta
    tmp = target.with_name("." + target.name + ".merge")
    with open(tmp, "wb") as fh:
        np.savez(fh, **merged)
    with np.load(tmp, allow_pickle=True) as chk:
        mts = chk["timestamps"].astype("int64")
        if int(mts[-1]) != tip or np.any(np.diff(mts) <= 0):
            os.remove(tmp)
            return "verify_failed"
    os.replace(tmp, target)
    return "merged"


def lag_report(ac_tips):
    behind = []
    for sym, s1_tip in ac_tips.items():
        p = ARCHIVE / f"{sym}.npz"
        try:
            with np.load(p) as z:
                tip = int(z["timestamps"][-1])
        except Exception:
            tip = 0
        if tip < s1_tip:
            behind.append((sym, tip, s1_tip))
    return behind


def run_once():
    host = pull_pending()
    results = {}
    for delta in sorted(glob.glob(str(STAGE / "*.npz"))):
        sym, tip = Path(delta).stem.rsplit("__", 1)
        tip = int(tip)
        try:
            status = merge_one(delta, sym, tip)
        except Exception as exc:
            status = f"error:{type(exc).__name__}"
        results[sym] = status
        if status in ("merged", "already_current"):
            s1_write(f"acks/{sym}.json", json.dumps({"tip": tip, "at": time.time()}))
    rep = {"at": time.time(), "pulled_from": host, "results": results}
    print(json.dumps(rep), flush=True)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

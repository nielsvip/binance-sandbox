#!/usr/bin/env python3
"""Second + third copies for the 2026-10-28 Mac disk offload: Mac -> S2 (sha256 verified) -> GCS (md5 verified).

WHY a trickle: measured 2026-10-28 02:00Z the Mac uplink gave ~80-100 KB/s to S1, S2, S5 and GCS alike (the live
feeds share it), and WD25/TOSHIBA_EXT dropped off the bus at ~01:55Z. So the Mac files were shelved IN PLACE
(lossless zstd, decompressed sha256 == original sha256, data/OFFLOAD_MANIFEST.md) and this job builds the off-Mac
copies slowly with rsync --bwlimit, one file at a time, resumable.

Queue: data/reports/mac_offload_20261028/queue.json  [{mac_path, sha256, bytes, remote_rel, mode}]
  mode "keep"  : the S2 copy stays (server copy of data that may be needed soon)  -> S2 + GCS
  mode "stage" : S2 is only the launch pad for GCS; the S2 copy is removed after GCS md5 verification -> GCS only
Status: data/reports/mac_offload_20261028/trickle.jsonl (one row per step). A row with "done": true is final.
Never deletes anything on the Mac. Refuses to send when S2 would drop below 40 GB free (USER LAW).
Run: nohup python3 tools/mac_offload_20261028_trickle.py >> ~/logs/mac_offload_20261028_trickle.log 2>&1 &
"""
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "data" / "reports" / "mac_offload_20261028"
QUEUE = DIR / "queue.json"
STATUS = DIR / "trickle.jsonl"
HOST = os.environ.get("MAC_OFFLOAD_HOST", "s2")
REMOTE_ROOT = "/home/niels/cold_offload/mac_20261028"
GCS_PREFIX = "mac/offload_20261028"
BWLIMIT_KB = os.environ.get("MAC_OFFLOAD_BWLIMIT_KB", "100")
FLOOR_GB = 40
SSH = ["ssh", "-o", "ControlPath=none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=30"]
PY = "/opt/anaconda3/envs/binance_env/bin/python"


def log(**row):
    row = {"ts": datetime.now(timezone.utc).isoformat(), **row}
    with STATUS.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(row, sort_keys=True), flush=True)


def done_set():
    out = set()
    if STATUS.exists():
        for line in STATUS.open():
            r = json.loads(line)
            if r.get("done"):
                out.add(r["mac_path"])
    return out


def remote(cmd, timeout=600):
    return subprocess.run(SSH + [HOST, cmd], capture_output=True, text=True, timeout=timeout)


def remote_free_gb():
    r = remote("df -BG --output=avail /home/niels | tail -1")
    return int(r.stdout.strip().rstrip("G")) if r.returncode == 0 else -1


def remote_sha(path):
    r = remote(f"sha256sum {shlex.quote(path)} 2>/dev/null | cut -d' ' -f1", timeout=1800)
    return r.stdout.strip()


def one(item):
    mac, sha, rel = item["mac_path"], item["sha256"], item["remote_rel"]
    if not os.path.isfile(mac):
        log(mac_path=mac, step="skip", error="missing on Mac (pruned elsewhere?)", done=True)
        return
    rpath = f"{REMOTE_ROOT}/{rel}"
    if remote_sha(rpath) != sha:
        free = remote_free_gb()
        need = item["bytes"] / 2**30
        if free < 0 or free - need < FLOOR_GB:
            raise RuntimeError(f"{HOST} free {free} GB - {need:.1f} GB would break the {FLOOR_GB} GB floor")
        remote(f"mkdir -p {shlex.quote(os.path.dirname(rpath))}")
        r = subprocess.run(["nice", "-n", "15", "rsync", "-a", "--partial", f"--bwlimit={BWLIMIT_KB}", "-e", " ".join(SSH), mac, f"{HOST}:{rpath}"], capture_output=True, text=True, timeout=7 * 86400)
        if r.returncode:
            raise RuntimeError(f"rsync rc={r.returncode} {r.stderr[-200:]}")
        got = remote_sha(rpath)
        if got != sha:
            raise RuntimeError(f"{HOST} sha256 {got} != {sha}")
    log(mac_path=mac, step="server_verified", host=HOST, remote_path=rpath, sha256=sha)
    obj = f"{GCS_PREFIX}/{rel}"
    r = subprocess.run([PY, str(ROOT / "tools" / "gcs_cold_store.py"), "push", "--host", HOST, "--source", rpath, "--raw", "--name", obj], capture_output=True, text=True, timeout=86400, cwd=ROOT)
    rows = [json.loads(x) for x in r.stdout.splitlines() if x.startswith("{")]
    rec = rows[-1] if rows else {}
    if r.returncode or rec.get("verified") is not True or rec.get("sha256") != sha:
        raise RuntimeError(f"gcs push not verified rc={r.returncode} sha={rec.get('sha256')} {r.stderr[-200:]}")
    gcs = f"gs://{rec.get('bucket')}/{obj}"
    log(mac_path=mac, step="gcs_verified", gcs=gcs, md5=rec.get("remote_md5"), sha256=sha)
    if item["mode"] == "stage":
        remote(f"rm -f {shlex.quote(rpath)}")
        log(mac_path=mac, step="done", copies=[gcs], server_copy_removed=True, done=True)
    else:
        log(mac_path=mac, step="done", copies=[f"{HOST}:{rpath}", gcs], done=True)


def main():
    DIR.mkdir(parents=True, exist_ok=True)
    while True:
        items = json.loads(QUEUE.read_text())
        finished = done_set()
        todo = [i for i in items if i["mac_path"] not in finished]
        if not todo:
            log(step="all_done", n=len(items))
            return 0
        for item in todo:
            try:
                one(item)
            except Exception as exc:
                log(mac_path=item["mac_path"], step="error", error=str(exc)[:400])
                time.sleep(300)
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())

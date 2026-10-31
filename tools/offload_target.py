"""Where a bulky backup may be written: the 5 TB drive (WD25), or nowhere. Never the Mac's own disk.

USER 2026-10-31 (verbatim): "we need ALL those tools to go to 5T directly wihout putting in danger mac integrity ever
again (or any of the s1 2 5 6 servers".

Why: the same class of failure twice. 2026-10-27 the Mac fell to 249 MB free and S1 to 0 MB (hourly per_sym_store
copies from tools/v15_h2h_cycle.sh). 2026-10-31 the Mac fell to 143 MiB twice — backups/ held 118 GB, of which
board_name_truth_guard tars were 56.7 GB (a ~214 MB tar every few minutes) and h2h .db.zst copies 48.6 GB — and a full
disk killed a running job mid-write.

The rule this module enforces: a backup goes to WD25 or it does not happen, and the caller decides what "not
happening" means (h2h must not apply without one; the board guard must not touch a board without one). There is no
fallback to a local path, because a fallback is how the Mac filled.

Safety against the drive itself: WD25 shares a USB hub with TOSHIBA_EXT, whose stalls have reset that hub and dropped
WD25 (2026-10-28), and a stalled USB volume hangs any process that touches it — on 2026-10-31 a plain `df` hung for
120 s on TOSHIBA_EXT. So the health check never touches the drive from the caller's process: mount state comes from
the kernel mount table, and the space + write probe run in a child process with a hard timeout. A drive that does not
answer in time is unhealthy, full stop.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DRIVE = Path(os.environ.get("OFFLOAD_DRIVE", "/Volumes/WD25"))
ROOT_ON_DRIVE = os.environ.get("OFFLOAD_ROOT", "binance_backups")
FLOOR_GB = float(os.environ.get("OFFLOAD_FLOOR_GB", "200"))   # never fill the 5T past this
PROBE_TIMEOUT_S = float(os.environ.get("OFFLOAD_PROBE_TIMEOUT_S", "6"))

_PROBE = r"""
import os, sys, time, json
d = sys.argv[1]
os.makedirs(d, exist_ok=True)
st = os.statvfs(d)
probe = os.path.join(d, f".offload_probe_{os.getpid()}")
with open(probe, "w") as fh:
    fh.write("ok")
    fh.flush()
    os.fsync(fh.fileno())
os.remove(probe)
print(json.dumps({"free_bytes": st.f_bavail * st.f_frsize}))
"""


def mounted(drive=DRIVE):
    """From the kernel mount table only: listing mounts never touches the volume itself."""
    try:
        out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=5).stdout
    except (subprocess.TimeoutExpired, OSError):
        return False
    return any(f" on {drive} " in line or line.split(" on ")[1].startswith(f"{drive} (") for line in out.splitlines() if " on " in line)


def healthy_target(subdir, need_bytes=0, drive=DRIVE, timeout=PROBE_TIMEOUT_S):
    """(path, None) when the drive is mounted, answers, is writable and has room; (None, why) otherwise."""
    if not mounted(drive):
        return None, f"{drive} is not mounted"
    target = drive / ROOT_ON_DRIVE / subdir
    try:
        r = subprocess.run([sys.executable, "-I", "-c", _PROBE, str(target)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"{drive} did not answer within {timeout:.0f} s (stalled USB volume)"
    except OSError as exc:
        return None, f"{drive} probe could not start: {exc}"
    if r.returncode != 0:
        return None, f"{drive} is not writable: {(r.stderr or r.stdout).strip()[-160:]}"
    try:
        free = int(json.loads(r.stdout.strip().splitlines()[-1])["free_bytes"])
    except (ValueError, KeyError, IndexError):
        return None, f"{drive} probe answered nonsense: {r.stdout[-120:]}"
    if free - need_bytes < FLOOR_GB * 1024 ** 3:
        return None, f"{drive} would fall below its {FLOOR_GB:.0f} GB floor ({free / 1024 ** 3:.0f} GB free, need {need_bytes / 1024 ** 3:.1f} GB)"
    return target, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--subdir", required=True, help="e.g. h2h, board_name_truth, servers/s1")
    ap.add_argument("--need-mb", type=float, default=0.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    path, why = healthy_target(a.subdir, int(a.need_mb * 1024 * 1024))
    if a.json:
        print(json.dumps({"path": str(path) if path else None, "why": why, "at": time.time()}))
    elif path:
        print(path)
    else:
        print(f"OFFLOAD_UNAVAILABLE: {why}", file=sys.stderr)
    return 0 if path else 1


if __name__ == "__main__":
    sys.exit(main())

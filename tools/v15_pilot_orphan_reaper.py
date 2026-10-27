#!/usr/bin/env python3
"""Reap v15_pilot.py fork-workers whose parent died (OOM kill), so their RSS returns to the box.

2026-10-27 incident: S1 (30 GB, no swap) OOM-killed pilot PARENTS (oom_score_adj 800) mid-board.
Each parent forks `--workers N` children; when the parent dies the children are reparented to init,
hold ~1.2-2.6 GB each forever, and can never publish anything because the parent owned the workbook.
28 such orphans held 7.9 GB, which OOM-killed the next parent, which orphaned 7 more. The fleet
scheduler's wedge reaper cannot see them: it looks for parents with no workers, these are workers
with no parent. Measured result: zero boards published for ~14 h while 4 hosts billed by the minute.

Reap criterion (all must hold) -- deliberately narrow so a healthy pilot is never touched:
  * cmdline matches v15_pilot.py --sym-side
  * ppid == 1  (reparented to init)
  * no children  (a live top-level pilot that already forked has children)
  * its own stdout (fd 1) has not been written for --stale-s  (a top-level pilot is ALSO ppid==1
    when launched detached, so staleness is the only safe discriminator)
  * process age > --grace-s  (a pilot still loading NPZ writes nothing for minutes)

Dry-run by default. --apply to actually signal (TERM, then KILL after --kill-after-s).
"""
import argparse
import json
import os
import pathlib
import signal
import sys
import time

PILOT_MATCH = "v15_pilot.py"


def _cmdline(pid):
    try:
        return pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except Exception:
        return ""


def _stat_fields(pid):
    try:
        raw = pathlib.Path(f"/proc/{pid}/stat").read_text()
    except Exception:
        return None
    rp = raw.rfind(")")
    if rp < 0:
        return None
    rest = raw[rp + 2:].split()
    return {"ppid": int(rest[1]), "starttime": int(rest[19])}


def _boot_time():
    for line in pathlib.Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime"):
            return float(line.split()[1])
    return 0.0


def _rss_mb(pid):
    try:
        return int(pathlib.Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576.0
    except Exception:
        return 0.0


def _children(pid):
    try:
        out = []
        for t in pathlib.Path(f"/proc/{pid}/task").iterdir():
            ch = (t / "children")
            if ch.exists():
                out += ch.read_text().split()
        return out
    except Exception:
        return []


def _fd1_age_s(pid, now):
    try:
        target = os.readlink(f"/proc/{pid}/fd/1")
    except Exception:
        return None
    if not target.startswith("/") or target.startswith("/dev") or "(deleted)" in target:
        return None
    try:
        return now - os.stat(target).st_mtime
    except Exception:
        return None


def _sym_side(cmd):
    parts = cmd.split()
    for i, p in enumerate(parts):
        if p == "--sym-side" and i + 1 < len(parts):
            return parts[i + 1]
    return "?"


def scan(stale_s, grace_s):
    now = time.time()
    btime = _boot_time()
    hz = os.sysconf("SC_CLK_TCK") or 100
    orphans, healthy = [], []
    for ent in pathlib.Path("/proc").iterdir():
        if not ent.name.isdigit():
            continue
        pid = int(ent.name)
        cmd = _cmdline(pid)
        if PILOT_MATCH not in cmd or "--sym-side" not in cmd:
            continue
        if f"/proc/{pid}" in cmd or "orphan_reaper" in cmd:
            continue
        st = _stat_fields(pid)
        if not st:
            continue
        age = now - (btime + st["starttime"] / hz)
        kids = _children(pid)
        fd1 = _fd1_age_s(pid, now)
        rec = {"pid": pid, "sym_side": _sym_side(cmd), "ppid": st["ppid"], "kids": len(kids),
               "age_s": round(age), "log_age_s": None if fd1 is None else round(fd1), "rss_mb": round(_rss_mb(pid))}
        is_orphan = (st["ppid"] == 1 and not kids and age > grace_s and fd1 is not None and fd1 > stale_s)
        (orphans if is_orphan else healthy).append(rec)
    return orphans, healthy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually signal; default is dry-run")
    ap.add_argument("--stale-s", type=float, default=1500.0, help="stdout untouched for this long (default 25min)")
    ap.add_argument("--grace-s", type=float, default=900.0, help="never touch a process younger than this")
    ap.add_argument("--kill-after-s", type=float, default=10.0)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if not pathlib.Path("/proc/stat").exists():
        print(json.dumps({"event": "unsupported", "note": "needs /proc (Linux); no-op on macOS"}))
        return 0
    orphans, healthy = scan(a.stale_s, a.grace_s)
    host = os.uname().nodename
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not orphans:
        if not a.quiet:
            print(json.dumps({"ts": ts, "host": host, "event": "clean", "orphans": 0, "live_pilots": len(healthy)}))
        return 0
    freed = sum(o["rss_mb"] for o in orphans)
    print(json.dumps({"ts": ts, "host": host, "event": "orphans_found", "n": len(orphans),
                      "rss_mb_total": freed, "apply": a.apply, "orphans": orphans}))
    if not a.apply:
        return 0
    for o in orphans:
        try:
            os.kill(o["pid"], signal.SIGTERM)
        except Exception:
            pass
    time.sleep(a.kill_after_s)
    killed = []
    for o in orphans:
        try:
            os.kill(o["pid"], 0)
            os.kill(o["pid"], signal.SIGKILL)
            killed.append(o["pid"])
        except Exception:
            pass
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host,
                      "event": "reaped", "termed": [o["pid"] for o in orphans], "killed": killed,
                      "rss_mb_reclaimed_est": freed}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

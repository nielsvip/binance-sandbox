#!/usr/bin/env python3
"""Reap v15_pilot.py fork-workers whose parent died (OOM kill), so their RSS returns to the box.

2026-10-27 incident: S1 (30 GB, no swap) OOM-killed pilot PARENTS (oom_score_adj 800) mid-board.
Each parent forks `--workers N` children; when the parent dies the children are reparented to init,
hold ~1.2-2.6 GB each forever, and can never publish anything because the parent owned the workbook.
28 such orphans held 7.9 GB, which OOM-killed the next parent, which orphaned 7 more. The fleet
scheduler's wedge reaper cannot see them: it looks for parents with no workers, these are workers
with no parent. Measured result: zero boards published for ~14 h while 4 hosts billed by the minute.

Reap criterion (per sym_side GROUP, all must hold; liveness signal = freshest of stdout mtime,
data/reports/lifecycle_pilot/<SS>_*progress*.json mtime, /tmp/sweep_<SS>_30D.log mtime):
  * no member has a child (kernel task children file OR /proc ppid scan)
  * no member is a session leader (setsid'd top-level pilot has pid == sid) -- a top-level pilot is never a candidate
  * every member has ppid 1 and age > --grace-s
  * group has >= 2 members (a lone process is never reaped)
  * freshest liveness signal older than --stale-s

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
    return {"ppid": int(rest[1]), "sid": int(rest[3]), "starttime": int(rest[19])}


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


def _pids():
    return [int(e.name) for e in pathlib.Path("/proc").iterdir() if e.name.isdigit()]


def _children(pid):
    out = set()
    try:
        for t in pathlib.Path(f"/proc/{pid}/task").iterdir():
            ch = t / "children"
            if ch.exists():
                out.update(ch.read_text().split())
    except Exception:
        pass
    return sorted(out)


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


def _liveness_age_s(pid, symbol_side, now):
    """Freshest signal that this sym_side is making progress: stdout, progress JSON, sweep log."""
    ages = []
    fd1 = _fd1_age_s(pid, now)
    if fd1 is not None:
        ages.append(fd1)
    cands = [f"/tmp/sweep_{symbol_side}_30D.log"]
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        lp = pathlib.Path(cwd) / "data" / "reports" / "lifecycle_pilot"
        cands += [str(lp / f"{symbol_side}_v14_progress.json")]
        cands += [str(x) for x in lp.glob(f"{symbol_side}_*progress*.json")]
    except Exception:
        pass
    for c in cands:
        try:
            ages.append(now - os.stat(c).st_mtime)
        except Exception:
            pass
    return min(ages) if ages else None


def scan(stale_s, grace_s):
    """Group pilot-cmdline processes by sym_side. A group is an OOM orphan set ONLY if:
    no member has a child (kernel children file OR ppid scan), no member is a session leader
    (the setsid'd top-level pilot is pid==sid), every member has ppid 1, every member is older than
    grace, the group has >=2 members (a lone process is never reaped), and the freshest liveness
    signal (stdout, progress JSON, sweep log) is older than stale_s."""
    now = time.time()
    btime = _boot_time()
    hz = os.sysconf("SC_CLK_TCK") or 100
    procs = {}
    for pid in _pids():
        cmd = _cmdline(pid)
        if PILOT_MATCH not in cmd or "--sym-side" not in cmd:
            continue
        if f"/proc/{pid}" in cmd or "orphan_reaper" in cmd:
            continue
        st = _stat_fields(pid)
        if not st:
            continue
        procs[pid] = {"cmd": cmd, "st": st}
    ppid_kids = {}
    for pid in _pids():
        st = _stat_fields(pid)
        if st:
            ppid_kids.setdefault(st["ppid"], []).append(pid)
    groups = {}
    for pid, pr in procs.items():
        groups.setdefault(_sym_side(pr["cmd"]), []).append(pid)
    orphans, healthy = [], []
    for symbol_side, pids in groups.items():
        recs = []
        live_tree = False
        for pid in pids:
            st = procs[pid]["st"]
            kids = set(_children(pid)) | {str(k) for k in ppid_kids.get(pid, [])}
            age = now - (btime + st["starttime"] / hz)
            live = _liveness_age_s(pid, symbol_side, now)
            if kids or st.get("sid") == pid or st["ppid"] != 1 or age <= grace_s:
                live_tree = True
            recs.append({"pid": pid, "sym_side": symbol_side, "ppid": st["ppid"], "sid": st.get("sid"),
                         "kids": len(kids), "age_s": round(age), "log_age_s": None if live is None else round(live),
                         "rss_mb": round(_rss_mb(pid))})
        ages = [r["log_age_s"] for r in recs if r["log_age_s"] is not None]
        quiet = bool(ages) and min(ages) > stale_s
        if not live_tree and len(recs) >= 2 and quiet:
            orphans += recs
        else:
            healthy += recs
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

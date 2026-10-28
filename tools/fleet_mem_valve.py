#!/usr/bin/env python3
"""fleet_mem_valve — turn uncontrolled OOM kills of 30D boards into controlled, resumable preemption.

Measured 2026-10-28 (19:30-04:40Z, fleet_dispatch era): 73 of 125 dispatched boards died with no outcome line, and the
kernel logged 48 (s1), ~100 (s2) and 57 (s5) OOM kills. A killed pilot PARENT loses its board and orphans its fork
workers (the 10-27 cascade); a killed WORKER breaks the fork pool and the pilot falls back to its single-threaded legacy
loop ("[spec-fill-INCOMPLETE]"), which occupies a slot for hours. This valve, run every minute (internally every 5 s):
  1. marks pilot fork-WORKERS (a v15_pilot process with no children whose parent is a v15_pilot) oom_score_adj=1000,
     so the kernel takes a 1-1.5 GB worker, never a board's parent and never a live process (raising is unprivileged);
     skipped where another tool owns test priorities (S1: tools/s1_live_priority.sh sets tests to +800);
  2. when headroom (MemAvailable; on S1 also the cron.service cgroup's max - anon - kernel) falls under VALVE_MB and
     >= 2 boards run, SIGTERMs the YOUNGEST board parent (the pilot saves at the next cell boundary; progress is kept and
     the dispatcher resumes it) and books "restarted" in ~/fleet_dispatch/launches/<SS>.json so the dispatcher does
     not count it as a death (no host quarantine);
  3. SIGTERMs a board whose log shows the pool collapsed into the legacy loop since its launch, so it is relaunched with
     a full pool instead of crawling single-threaded.
Never touches a non-pilot process, never SIGKILLs, never acts on the last running board. Log: ~/logs/fleet_mem_valve.jsonl
"""
import json
import os
import signal
import socket
import sys
import time

HOME = os.path.expanduser("~")
LOG = os.path.join(HOME, "logs", "fleet_mem_valve.jsonl")
LAUNCH_DIR = os.path.join(HOME, "fleet_dispatch", "launches")
STATE = "/tmp/fleet_mem_valve_state.json"
VALVE_MB = int(os.environ.get("FLEET_VALVE_MB", "2000"))
COOLDOWN_S = int(os.environ.get("FLEET_VALVE_COOLDOWN_S", "120"))
LOOP_S = int(os.environ.get("FLEET_VALVE_LOOP_S", "55"))
DRY = "--dry-run" in sys.argv
CG = "/sys/fs/cgroup/system.slice/cron.service"


def log(rec):
    rec = dict(rec, ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), host=socket.gethostname(), dry=DRY)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def headroom_mb():
    avail = 0
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            avail = int(line.split()[1]) // 1024
    try:
        # memory.high is where the cgroup starts reclaim-throttling every task (S1 16:5xZ: cron.service anon 21.5 GB > high 20 GB,
        # load 52, the dispatcher probe timed out and S1 dropped out of the fleet) -> the lower of high and max is the ceiling
        lims = [open(CG + f).read().strip() for f in ("/memory.high", "/memory.max")]
        lims = [int(x) for x in lims if x != "max"]
        if lims:
            stat = dict(l.split() for l in open(CG + "/memory.stat"))
            used = int(stat.get("anon", 0)) + int(stat.get("kernel", 0)) + int(stat.get("shmem", 0))
            avail = min(avail, (min(lims) - used) // (1 << 20))
    except (OSError, ValueError):
        pass
    return avail


def procs():
    """-> {pid: (ppid, symside, start_ticks)} for every v15_pilot process."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
            if not any(b"v15_pilot" in a and a.endswith(b".py") for a in argv[:3]) or b"--sym-side" not in argv:
                continue
            stat = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
            out[int(pid)] = (int(stat[1]), argv[argv.index(b"--sym-side") + 1].decode(), int(stat[19]))
        except (OSError, ValueError, IndexError):
            continue
    return out


def tree(ps):
    kids = {}
    for pid, (ppid, _, _) in ps.items():
        kids.setdefault(ppid, []).append(pid)
    tops = {pid: v for pid, v in ps.items() if v[0] not in ps}
    workers = [pid for pid, v in ps.items() if v[0] in ps and not kids.get(pid)]
    return tops, workers


def owns_test_priority():
    try:
        import subprocess
        tab = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return True
    return any("s1_live_priority.sh" in line and not line.lstrip().startswith("#") for line in tab.splitlines())


def mark_workers(workers):
    n = 0
    for pid in workers:
        try:
            path = f"/proc/{pid}/oom_score_adj"
            if int(open(path).read()) < 1000 and not DRY:
                open(path, "w").write("1000")
                n += 1
        except (OSError, ValueError):
            pass
    return n


def book_restart(ss, why):
    path = os.path.join(LAUNCH_DIR, ss + ".json")
    try:
        rec = json.load(open(path))
        rec["restarted"] = int(time.time())
        rec["restarted_by"] = "fleet_mem_valve:" + why
        if not DRY:
            tmp = path + ".valve_tmp"
            json.dump(rec, open(tmp, "w"))
            os.replace(tmp, path)
        return True
    except (OSError, ValueError):
        return False


def collapsed(ss, since):
    """True when the board's log shows the fork pool collapsed into the legacy loop after `since`."""
    path = f"/tmp/sweep_{ss}_30D.log"
    try:
        if os.path.getmtime(path) < since:
            return False
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 400_000))
            tail = fh.read().decode("utf8", "replace")
        cut = tail.rfind("[DEDUP] acquired exclusive lock for " + ss + " (")
        return cut >= 0 and "[spec-fill-INCOMPLETE]" in tail[cut:]
    except OSError:
        return False


def main():
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {}
    boot = next(int(l.split()[1]) for l in open("/proc/stat") if l.startswith("btime"))
    hz = os.sysconf("SC_CLK_TCK")
    manage_adj = not owns_test_priority()
    t_end = time.time() + LOOP_S
    while True:
        now = time.time()
        ps = procs()
        tops, workers = tree(ps)
        marked = mark_workers(workers) if manage_adj else 0
        if marked:
            log({"event": "workers_marked", "n": marked})
        boards = sorted(((boot + v[2] / hz, pid, v[1]) for pid, v in tops.items()), reverse=True)
        for started, pid, ss in boards:
            if now - started > 300 and collapsed(ss, started) and now - float(state.get(f"col_{pid}", 0)) > 600:
                state[f"col_{pid}"] = now
                booked = book_restart(ss, "pool_collapsed")
                if not DRY:
                    os.kill(pid, signal.SIGTERM)
                log({"event": "sigterm_pool_collapsed", "pid": pid, "ss": ss, "age_s": int(now - started), "booked": booked})
        head = headroom_mb()
        if head < VALVE_MB and len(boards) >= 2 and now - float(state.get("last_valve", 0)) > COOLDOWN_S:
            started, pid, ss = boards[0]
            state["last_valve"] = now
            booked = book_restart(ss, f"headroom {head}MB<{VALVE_MB}")
            try:
                if not DRY:
                    os.kill(pid, signal.SIGTERM)
                log({"event": "sigterm_youngest", "pid": pid, "ss": ss, "age_s": int(now - started), "headroom_mb": head, "boards": len(boards), "booked": booked})
            except OSError as error:
                log({"event": "sigterm_failed", "pid": pid, "ss": ss, "error": str(error)})
        if time.time() + 5 > t_end:
            break
        time.sleep(5)
    for key in [k for k, v in state.items() if k.startswith("col_") and time.time() - float(v) > 7200]:
        state.pop(key)
    tmp = STATE + ".tmp"
    json.dump(state, open(tmp, "w"))
    os.replace(tmp, STATE)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""v15_pusher_supervisor — per-host worker pool: NEVER let compute sit idle (USER 2026-10-09).

Every tick (cron */2, flock): reclaim orphaned running/ units to inbox, then
spawn niced v15_pusher_worker procs while CPU busy < 90% AND free RAM covers
reserve + headroom AND count < max_workers. Workers are never killed (units
drain in minutes; overshoot self-corrects). Pilots (nice 0) always preempt
pushers (nice 15), so filling to 90%+ cannot starve the sweep.

Writes heartbeat.json for the S1 coordinator (workers, cpu, RAM, inbox depth).
Local-only: no ssh (the S1 coordinator pushes inbox / pulls done+heartbeat).
"""
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]

CPU_TARGET = 90.0
WORKER_RSS_GUARD_MB = 600


def log(msg):
    print(f"[pushsup] {msg}", flush=True)


def cpu_busy():
    def read():
        p = open("/proc/stat").readline().split()
        vals = [int(x) for x in p[1:8]]
        return vals[0] + vals[1] + vals[2], sum(vals)
    try:
        b0, t0 = read()
        time.sleep(1.0)
        b1, t1 = read()
        return round(100.0 * (b1 - b0) / max(1, t1 - t0), 1)
    except Exception:
        return 0.0


def mem_avail_mb():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        return 0
    return 0


def worker_pids():
    out = []
    try:
        p = subprocess.run(["pgrep", "-f", "v15_pusher_worker.py"], capture_output=True, text=True)
        for line in (p.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit() and int(line) != os.getpid():
                out.append(int(line))
    except Exception:
        pass
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/v15_pusher"))
    ap.add_argument("--reserve-mb", type=int, default=4000)
    ap.add_argument("--max-workers", type=int, default=0, help="0 = nproc")
    a = ap.parse_args()
    base = pathlib.Path(a.root)
    inbox, running = base / "inbox", base / "running"
    for d in (inbox, running, base / "done", base / "logs", base / "anchors", base / "registry"):
        d.mkdir(parents=True, exist_ok=True)
    reclaimed = 0
    for f in running.glob("*.json"):
        if f.name.startswith("tmp_"):
            continue
        try:
            os.rename(f, inbox / f.name)
            reclaimed += 1
        except OSError:
            pass
    for tmp in running.glob("tmp_*"):
        try:
            if time.time() - tmp.stat().st_mtime > 3600:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
    nproc = os.cpu_count() or 8
    maxw = a.max_workers or nproc
    busy, avail = cpu_busy(), mem_avail_mb()
    workers = worker_pids()
    log(f"cpu={busy}% avail={avail}MB workers={len(workers)} inbox={len(list(inbox.glob('*.json')))} reclaimed={reclaimed}")
    spawned = 0
    while busy < CPU_TARGET and avail > a.reserve_mb + WORKER_RSS_GUARD_MB and len(workers) + spawned < maxw:
        lf = open(base / "logs" / f"worker_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{spawned}.log", "a")
        subprocess.Popen(["nice", "-n", "15", sys.executable or "python3", "-u", str(ROOT / "tools" / "v15_pusher_worker.py"), "--root", str(base)],
                         stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(ROOT))
        spawned += 1
        avail -= WORKER_RSS_GUARD_MB
        busy = min(99.0, busy + 100.0 / max(1, nproc))
    if spawned:
        log(f"spawned {spawned} workers (target cpu>={CPU_TARGET}%)")
    hb = {"ts": time.time(), "host": os.uname().nodename, "cpu": busy, "avail_mb": mem_avail_mb(),
          "workers": len(workers) + spawned, "spawned": spawned, "inbox": len(list(inbox.glob("*.json"))),
          "running": len([f for f in running.glob("*.json") if not f.name.startswith("tmp_")]),
          "done": len(list((base / "done").glob("*.json")))}
    (base / "heartbeat.json").write_text(json.dumps(hb, indent=1))


if __name__ == "__main__":
    main()

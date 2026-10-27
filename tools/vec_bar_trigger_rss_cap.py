#!/usr/bin/env python3
"""Restart tools/vec_bar_trigger.py when it leaks past an RSS cap.

2026-10-27 incident: the STOCK shards of vec_bar_trigger leak hard -- stock_short on s6 reached
25.3 GB over 18 h (77% of a 31 GB box) and stock_long on s5 reached 9.9 GB in 1.3 h (~7.6 GB/h).
The crypto shards were fine (0.2 GB after 18 h), so this is shard-specific, not a box problem.

Why it silently stopped all backtesting on that host: overload_run.sh gates every launch behind free
memory and answers `MEM_WAIT-<SYM>-<avail>MB`. The worker's self_dispatch therefore looped every
minute, found running=0, called overload_run for all 56 queued sides, got MEM_WAIT for each, printed
neither LAUNCHED nor ALREADY, and logged nothing about memory. s6 published zero boards while idle
and billed. Nothing watched the trigger's RSS, and its 1-minute cron keepalive only checks liveness,
so a leaking-but-alive process is invisible to it and is never replaced.

This only kills; the existing per-host `# BAR_TRIGGER` cron keepalive restarts a fresh one within a
minute. Each shard is judged on its own process.
"""
import argparse
import json
import os
import pathlib
import signal
import sys
import time

MATCH = "vec_bar_trigger"


def _cmdline(pid):
    try:
        return pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except Exception:
        return ""


def _rss_mb(pid):
    try:
        return int(pathlib.Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576.0
    except Exception:
        return 0.0


def _shard(cmd):
    parts = cmd.split()
    for i, p in enumerate(parts):
        if p == "--shard" and i + 1 < len(parts):
            return parts[i + 1]
    return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-rss-mb", type=float, default=6000.0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if not pathlib.Path("/proc/stat").exists():
        print(json.dumps({"event": "unsupported", "note": "needs /proc (Linux)"}))
        return 0
    host, ts = os.uname().nodename, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    over, seen = [], []
    for ent in pathlib.Path("/proc").iterdir():
        if not ent.name.isdigit():
            continue
        pid = int(ent.name)
        cmd = _cmdline(pid)
        if MATCH not in cmd or "rss_cap" in cmd or f"/proc/{pid}" in cmd:
            continue
        rec = {"pid": pid, "shard": _shard(cmd), "rss_mb": round(_rss_mb(pid))}
        seen.append(rec)
        if rec["rss_mb"] > a.max_rss_mb:
            over.append(rec)
    if not over:
        if not a.quiet:
            print(json.dumps({"ts": ts, "host": host, "event": "under_cap", "cap_mb": a.max_rss_mb, "procs": seen}))
        return 0
    print(json.dumps({"ts": ts, "host": host, "event": "over_cap", "cap_mb": a.max_rss_mb,
                      "restarting": over, "note": "cron '# BAR_TRIGGER' keepalive respawns within 1 min", "apply": a.apply}))
    if not a.apply:
        return 0
    for o in over:
        try:
            os.kill(o["pid"], signal.SIGTERM)
        except Exception:
            pass
    time.sleep(8)
    for o in over:
        try:
            os.kill(o["pid"], 0)
            os.kill(o["pid"], signal.SIGKILL)
        except Exception:
            pass
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host,
                      "event": "restarted", "pids": [o["pid"] for o in over],
                      "rss_mb_reclaimed_est": sum(o["rss_mb"] for o in over)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

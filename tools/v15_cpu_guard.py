#!/usr/bin/env python3
"""v15_cpu_guard — 2-minute CPU watchdog (runs on s1 cron). Samples real busy% (/proc/stat 5s delta, niced work counts) on s1/s2/s5 over ssh,
appends one JSON line per host to ~/binance-sandbox/logs/cpu_watchdog.jsonl, and flags hosts below 85% (alert line in logs/cpu_watchdog_alert.log).
Cause hint per low host: slots used/cap from the last scheduler log line, mem avail. The scheduler admits on real busy% (not load1), so a low host gets more pairs on the next tick."""
import json, subprocess, time, os, pathlib
HOME = pathlib.Path.home() / "binance-sandbox"
HOSTS = {"s1": "127.0.0.1", "s2": "10.0.0.4", "s5": "10.0.0.5"}
CMD = "python3 -c \"import time;f=lambda:[int(x) for x in open('/proc/stat').readline().split()[1:]];a=f();time.sleep(5);b=f();t=sum(b)-sum(a);i=(b[3]+b[4])-(a[3]+a[4]);print(round(100*(1-i/max(1,t)),1))\"; grep MemAvailable /proc/meminfo | awk '{print int($2/1024)}'; pgrep -fc 'v15_pilot.py --sym-side'"
def sample(n, ip):
    try:
        o = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no", ip, CMD], capture_output=True, text=True, timeout=40).stdout.split()
        return {"host": n, "busy_pct": float(o[0]), "mem_avail_mb": int(o[1]), "pilot_procs": int(o[2])}
    except Exception as e:
        return {"host": n, "error": str(e)[:80]}
(HOME / "logs").mkdir(exist_ok=True)
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
with open(HOME / "logs" / "cpu_watchdog.jsonl", "a") as f, open(HOME / "logs" / "cpu_watchdog_alert.log", "a") as al:
    for n, ip in HOSTS.items():
        r = sample(n, ip); r["ts"] = now
        f.write(json.dumps(r) + "\n")
        if r.get("busy_pct") is not None and r["busy_pct"] < 85:
            al.write(f"{now} {n} busy={r['busy_pct']}% mem_avail={r['mem_avail_mb']}MB pilots={r['pilot_procs']}\n")

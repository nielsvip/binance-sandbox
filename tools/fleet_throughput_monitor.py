#!/usr/bin/env python3
"""Publish truthful host load and one-hour board throughput from the S1 scheduler."""
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/reports/fleet_throughput.json"
LOG = Path("/tmp/fleet_throughput.log")
REMOTE = r'''
import json
for line in reversed(open('/tmp/v15_fleet_sched.log').read().splitlines()):
    try:
        d = json.loads(line)
    except (ValueError, TypeError):
        continue
    if isinstance(d, dict) and 'hosts' in d and 'now' in d:
        print(json.dumps({k: d.get(k) for k in ('now', 'hosts', 'coverage', 'round_completion', 'launched')}))
        break
'''


def summarize(d, now=None):
    now = time.time() if now is None else now
    stamp = dt.datetime.fromisoformat(d["now"]).timestamp()
    age = round(now - stamp)
    throughput = (d.get("coverage") or {}).get("throughput") or {}
    rates = throughput.get("published_per_host") or {}
    pending = int(((d.get("round_completion") or {}).get("n_launchable_pending") or 0))
    hosts = {}
    alerts = []
    if age > 300:
        alerts.append(f"SCHEDULER_STALE:{age}s")
    for name, h in (d.get("hosts") or {}).items():
        if not isinstance(h, dict) or h.get("draining"):
            continue
        cpu = h.get("cpu_real") if h.get("cpu_real") is not None else h.get("cpu")
        rate = int(rates.get(name, 0) or 0)
        slots = h.get("slots")
        hosts[name] = {"cpu_pct": cpu, "slots": slots, "observed_running": h.get("observed_running") or [],
                       "published_last_hour": rate, "idle_reason": h.get("idle_reason"),
                       "mem_avail_mb": h.get("mem_avail_mb")}
        if pending and rate < 5:
            alerts.append(f"LOW_THROUGHPUT:{name}:{rate}/h")
        if pending and cpu is not None and float(cpu) < (90 if name == "s1" else 80):
            alerts.append(f"LOW_CPU:{name}:{cpu}%")
        if pending and str(slots or "").startswith("0/"):
            alerts.append(f"IDLE:{name}:{h.get('idle_reason') or 'unknown'}")
    return {"ts": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(), "tick": d["now"],
            "tick_age_s": age, "round": d.get("round_completion"), "hosts": hosts,
            "one_hour_total": throughput.get("sym_sides"), "alerts": alerts}


def main():
    doc = None
    for alias in ("s1-pub", "s1-int"):
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
               "-o", "ControlMaster=no", "-o", "ControlPath=none", alias,
               "python3 -c " + shlex.quote(REMOTE)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            if proc.returncode == 0:
                doc = summarize(json.loads(proc.stdout))
                break
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            pass
    if doc is None:
        doc = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "alerts": ["S1_SCHEDULER_UNREACHABLE"]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, OUT)
    with LOG.open("a") as f:
        f.write(json.dumps(doc, separators=(",", ":")) + "\n")
    print(json.dumps({"tick": doc.get("tick"), "alerts": doc["alerts"], "hosts": doc.get("hosts")}, separators=(",", ":")))
    return 1 if doc["alerts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

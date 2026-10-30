#!/usr/bin/env python3
"""Fleet board throughput monitor (rewritten 2026-10-28; the old one read the retired v15_fleet_scheduler log and had
reported only S1_SCHEDULER_UNREACHABLE since the 10-27 fleet_dispatch switch).

Truth source = the publish manifest sidecar (`SPREADSHEETS/V15_V16_CELL_BY_CELL/*_manifest.json`, written by v15_pilot
only when a board is published, window_days 30). Per host: manifests whose mtime falls in the last full 60 min, running
boards and their worker count (vs the 28-worker law), MemAvailable, disk. Alerts when a host published < ALERT_PER_H
(default 10) in the last hour while pending work exists, or when the dispatcher heartbeat is stale on both leaders.
Safe auto-repair: a stale dispatcher (both leaders' heartbeat > 10 min) gets one forced tick on s2 (flocked, the
worker-side launch lock refuses duplicates). Writes data/reports/fleet_throughput.json + .jsonl history.
"""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/reports/fleet_throughput.json"
HIST = ROOT / "data/reports/fleet_throughput_history.jsonl"
ALERT_PER_H = float(os.environ.get("FLEET_ALERT_PER_H", "12"))
TARGET_PER_H = 12
# USER 2026-10-30 ("I AM PAYING IT TO COMPUTE NOT TO STORE OLD BULLSHIT", "IT NEEDS NOTHING"): a host under DISK_REPAIR_GB
# loses eval_cache_v1.db (regrown by the pilots; S6 held 73 GB and sat idle at 0.5 GB free) and ~/rebuild_scratch_* at once.
DISK_REPAIR_GB = int(os.environ.get("FLEET_DISK_REPAIR_GB", "15"))
STUCK_BOARD_S = 7200
DISK_REPAIR = r"""rm -f ~/binance-sandbox/data/eval_cache_v1.db ~/binance-sandbox/data/eval_cache_v1.db-wal ~/binance-sandbox/data/eval_cache_v1.db-shm; rm -rf ~/rebuild_scratch_*; df -BG --output=avail ~ | tail -1"""
REMOTE = r'''
import glob, json, os, time, subprocess
now = time.time()
sb = os.path.expanduser("~/binance-sandbox")
pub = []
for p in glob.glob(sb + "/SPREADSHEETS/V15_V16_CELL_BY_CELL/*_manifest.json"):
    try:
        m = os.path.getmtime(p)
        if now - m > 3 * 3600:
            continue
        wd = json.load(open(p)).get("window_days")
    except Exception:
        continue
    if wd in (30, None):
        pub.append([os.path.basename(p).split("_bh")[0], m])
txt = subprocess.run(["bash", "-c", "pgrep -af 'v15_pilot[^ ]*[.]py --sym-side' | grep -oE -- '--sym-side [A-Z0-9_]+' | sort | uniq -c"], capture_output=True, text=True).stdout
boards = {l.split()[2]: int(l.split()[0]) - 1 for l in txt.splitlines() if len(l.split()) == 3}
ages = {}
for l in subprocess.run(["bash", "-c", "ps -eo etimes=,args= | grep -E 'v15_pilot[^ ]*[.]py --sym-side' | grep -v grep"], capture_output=True, text=True).stdout.splitlines():
    parts = l.split()
    if "--sym-side" in parts:
        ss = parts[parts.index("--sym-side") + 1]
        ages[ss] = max(ages.get(ss, 0), int(parts[0]))
mem = {l.split(":")[0]: int(l.split()[1]) // 1024 for l in open("/proc/meminfo")}
st = os.statvfs(os.path.expanduser("~"))
hb = {}
try:
    hb = json.load(open(os.path.expanduser("~/fleet_dispatch/heartbeat.json")))
except Exception:
    pass
print(json.dumps({"now": now, "pub": pub, "boards": boards, "mem_avail_mb": mem.get("MemAvailable"), "disk_free_gb": st.f_bavail * st.f_frsize // (1 << 30), "load1": os.getloadavg()[0], "hb": hb, "ages": ages}))
'''
HOSTS = {
    "s1": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "s1"],
    "s2": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ControlPath=none", "s2"],
    "s5": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ControlPath=none", "s2", "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "niels@10.0.0.5"],
    "s6": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ControlPath=none", "s2", "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "niels@10.0.0.6"],
}


def probe(cmd):
    try:
        proc = subprocess.run(cmd + ["python3 -"], input=REMOTE, capture_output=True, text=True, timeout=60)
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def pending_total():
    for name in ("s1", "s2"):
        try:
            cmd = HOSTS[name] + ["tail -n 3 ~/binance-sandbox/logs/fleet_dispatch.jsonl"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
            for line in reversed(out.splitlines()):
                rec = json.loads(line)
                if "pending" in rec:
                    return sum(rec["pending"].values()), rec.get("idle_why") or {}
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
    return None, {}


def main():
    now = time.time()
    hosts, alerts, repairs = {}, [], []
    pend, idle_why = pending_total()
    hbs = []
    for name, cmd in HOSTS.items():
        d = probe(cmd)
        if d is None:
            hosts[name] = {"unreachable": True}
            alerts.append(f"UNREACHABLE:{name}")
            continue
        last_h = sorted(ss for ss, m in d["pub"] if d["now"] - m <= 3600)
        workers = sum(d["boards"].values())
        hosts[name] = {"published_last_60m": len(last_h), "published_last_3h": len(d["pub"]), "boards": d["boards"],
                       "boards_running": len(d["boards"]), "workers": workers, "workers_vs_law_28": f"{workers}/28",
                       "mem_avail_mb": d["mem_avail_mb"], "disk_free_gb": d["disk_free_gb"], "load1": round(d["load1"], 1),
                       "dispatch_idle_why": idle_why.get(name), "published_ss": last_h}
        if d.get("hb", {}).get("ts"):
            hbs.append(now - float(d["hb"]["ts"]))
        if len(last_h) < ALERT_PER_H and (pend is None or pend > 0):
            alerts.append(f"LOW_THROUGHPUT:{name}:{len(last_h)}/h<{ALERT_PER_H:g} (idle_why={idle_why.get(name)})")
        if d["disk_free_gb"] < 40:
            alerts.append(f"DISK_FLOOR:{name}:{d['disk_free_gb']}GB<40")
        if d["disk_free_gb"] < DISK_REPAIR_GB:
            try:
                after = subprocess.run(cmd + [DISK_REPAIR], capture_output=True, text=True, timeout=300).stdout.strip().splitlines()[-1]
                repairs.append(f"DISK_REPAIR:{name}:{d['disk_free_gb']}GB->{after.strip()} (eval_cache_v1.db + rebuild_scratch_* deleted)")
            except (OSError, subprocess.TimeoutExpired, IndexError) as error:
                repairs.append(f"DISK_REPAIR_FAILED:{name}:{error}")
        for ss, age in sorted((d.get("ages") or {}).items()):
            if age > STUCK_BOARD_S:
                alerts.append(f"STUCK_BOARD:{name}:{ss}:{age // 60}min")
    repair = "; ".join(repairs) or None
    if hbs and min(hbs) > 600:
        alerts.append(f"DISPATCHER_STALE:{int(min(hbs))}s")
        try:
            subprocess.run(HOSTS["s2"] + ["cd ~/binance-sandbox && flock -n /tmp/fleet_dispatch_cron.lock timeout 600 /home/niels/.conda/envs/binance_env_mac/bin/python tools/fleet_dispatch.py --force-act >> ~/logs/fleet_dispatch.out 2>&1"], timeout=620)
            repair = ((repair + "; ") if repair else "") + "forced one dispatcher tick on s2"
        except (OSError, subprocess.TimeoutExpired) as error:
            repair = f"forced tick failed: {error}"
    total = sum(h.get("published_last_60m", 0) for h in hosts.values())
    doc = {"ts": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(), "source": "publish manifests (window_days 30)",
           "target_per_host_h": TARGET_PER_H, "fleet_published_last_60m": total, "pending_sym_sides": pend,
           "hosts": hosts, "alerts": alerts, "repair": repair}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, OUT)
    with HIST.open("a") as fh:
        fh.write(json.dumps(doc, separators=(",", ":")) + "\n")
    low = [a for a in alerts if a.startswith(("LOW_THROUGHPUT", "STUCK_BOARD", "UNREACHABLE"))]
    if low or repair:
        msg = (f"{len(low)} alerts: " + " | ".join(low)[:180] + (f" — {repair}"[:120] if repair else "")).replace('"', "'")
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Fleet < {TARGET_PER_H}/h per server"'], timeout=20)
    print(json.dumps({"ts": doc["ts"], "fleet_60m": total, "per_host": {k: v.get("published_last_60m") for k, v in hosts.items()}, "alerts": alerts}))
    return 1 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())

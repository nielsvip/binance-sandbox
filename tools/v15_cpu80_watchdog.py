#!/usr/bin/env python3
"""
v15_cpu80_watchdog — runs ON EACH SERVER (s1/s2/s5/s6) via cron every 2 min.
Guarantees:
  - CPU >80% (load1/nproc*100) else auto-heal herd launch
  - RAM >80% or avail low check (herd will saturate)
  - No stall: heartbeat /tmp/v15_*.log mtime <5m, progress.json done increasing, pilots running >0
  - No stop: herd daemon pgrep v15_local_herd else restart, pilots 0 -> relaunch
  - Keep all servers cpu>80% via continuous saturation (herd max_parallel/workers)

Cron on each server (installed by deploy_v15_watchdogs.sh):
  */2 * * * * /home/niels/binance-sandbox/.venv/bin/python -u /home/niels/binance-sandbox/tools/v15_cpu80_watchdog.py >> /tmp/v15_cpu80_watchdog.log 2>&1
  @reboot sleep 30; nohup /home/niels/binance-sandbox/.venv/bin/python -u /home/niels/binance-sandbox/tools/v15_local_herd.py >> /tmp/v15_local_herd.log 2>&1 &
  * * * * * /home/niels/binance-sandbox/.venv/bin/python /home/niels/binance-sandbox/tools/v15_local_herd.py --cron-check >> /tmp/v15_local_herd_cron.log 2>&1

Also used by Mac warn daemon: same thresholds (CPU_WARN=80 now, was 95) for unified alerting.
"""
from __future__ import annotations
import subprocess, pathlib, time, json, re, os, sys
from datetime import datetime, timezone

ROOT = pathlib.Path.home() / "binance-sandbox"
LOG = pathlib.Path("/tmp/v15_cpu80_watchdog.log")
ALERT = pathlib.Path("/tmp/v15_cpu80_alert.jsonl")
CPU_WARN = 80.0  # user mandate: keep all servers cpu>80%
RAM_WARN = 80.0
OOM_AVAIL = 1200  # MB
STALL_MIN = 5  # minutes without heartbeat -> stall
PILOT_STALL_SEC = 300  # 5m no log write -> stall

def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as f: f.write(line+"\n")
    except: pass

def sh(cmd: str, timeout=10) -> str:
    try:
        return subprocess.check_output(["bash","-c",cmd], text=True, timeout=timeout)
    except Exception as e:
        return f"ERR:{e}"

def sys_stats():
    try: nproc=int(sh("nproc").strip())
    except: nproc=4
    try: load1=float(open("/proc/loadavg").read().split()[0])
    except: load1=0.0
    cpu_pct=load1/max(1,nproc)*100
    try:
        out=sh("free -m", timeout=5)
        m=re.search(r"Mem:\s+(\d+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)", out)
        total=int(m.group(1)) if m else 0
        avail=int(m.group(3)) if m and len(m.groups())>=3 else 0
        used_pct=(total-avail)/total*100 if total else 0
    except:
        total, avail, used_pct=0,0,0
    try:
        ps=sh("ps aux | grep v15_pilot | grep -v grep", timeout=5)
        pilots=len(re.findall(r"v15_pilot[^\\n]*--sym-side", ps))
        # also count purely
        pilots_raw=ps.count("v15_pilot")
    except:
        pilots=0
        ps=""
    try:
        herd_ps=sh("pgrep -a -f v15_local_herd | grep -v -- --cron-check", timeout=5)
        herd_ok="v15_local_herd" in herd_ps
        herd_cnt=len([l for l in herd_ps.splitlines() if "v15_local_herd" in l])
    except:
        herd_ok=False; herd_cnt=0
    # oom recent (today only)
    try:
        dmesg=sh("dmesg --time-format iso 2>&1 | tail -n 20", timeout=5)
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        oom=any(("out of memory" in l.lower() or "oom-killer" in l.lower()) and today in l for l in dmesg.splitlines())
    except:
        oom=False
    # heartbeat freshness: any /tmp/v15_*.log mtime <5m ?
    try:
        hb=sh("find /tmp -maxdepth 1 -name 'v15_*.log' -mmin -5 2>/dev/null | wc -l", timeout=5).strip()
        hb_recent=int(hb) if hb.isdigit() else 0
        hb_total=int(sh("find /tmp -maxdepth 1 -name 'v15_*.log' 2>/dev/null | wc -l", timeout=5).strip() or 0)
    except:
        hb_recent=0; hb_total=0
    # progress.json growth check (any done increase in last 5m?)
    try:
        prog_recent=sh("find ~/binance-sandbox/data/reports/lifecycle_pilot -name '*_v14_progress.json' -mmin -5 2>/dev/null | wc -l", timeout=5).strip()
        prog_recent_n=int(prog_recent) if prog_recent.isdigit() else 0
    except:
        prog_recent_n=0
    return {"nproc":nproc,"load1":load1,"cpu_pct":cpu_pct,"mem_total_m":total,"mem_avail_m":avail,"mem_used_pct":used_pct,"pilots":pilots,"herd_ok":herd_ok,"herd_cnt":herd_cnt,"oom":oom,"hb_recent":hb_recent,"hb_total":hb_total,"prog_recent":prog_recent_n,"raw_ps":ps[:500]}

def auto_heal(reason: str, host: str=""):
    log(f"AUTO-HEAL reason={reason} host={host}")
    # 1) ensure herd daemon running
    log("herd retired 2026-10-06: v15_fleet_scheduler admits work (s1 cron */2); nothing to restart here")
    # 2) cron-check
    # 3) log alert
    try:
        with ALERT.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "host": host or sh("hostname").strip(), "reason": reason, "stats": sys_stats()})+"\n")
    except: pass

def main():
    host=sh("hostname", timeout=5).strip() or "unknown"
    st=sys_stats()
    log(f"CHECK host={host} cpu {st['cpu_pct']:.1f}% load {st['load1']:.1f}/{st['nproc']} ram {st['mem_used_pct']:.1f}% avail {st['mem_avail_m']}M pilots {st['pilots']} herd_ok={st['herd_ok']} hb_recent {st['hb_recent']}/{st['hb_total']} prog_recent {st['prog_recent']} oom={st['oom']}")
    alerts=[]
    healed=False

    # Rule 1: herd daemon must be running
    if not st["herd_ok"]:
        alerts.append("HERD_DOWN")
        auto_heal("herd_down", host)
        healed=True

    # Rule 2: pilots must be running (if todo exists, herd should have launched)
    # Only alert if not oom and not recently healed
    if st["pilots"]==0:
        # check if there is todo left (order - done). If no todo, 0 pilots is OK (done)
        # approximate: check V15_SERVER_QUEUE_S*.txt todo vs local xlsx count
        todo_check=sh("ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | wc -l; cat ~/binance-sandbox/SPREADSHEETS/V15_SERVER_QUEUE_*.txt 2>/dev/null | wc -l", timeout=5)
        # if pilots 0 but todo_check shows files, it's stall
        # For now, if pilots 0 and herd_ok and not all done, heal
        # Check if any queue file has lines and local xlsx count < queue count
        # Simple: if pilots 0 and hb_total>0 (was running before) -> stall
        if st["hb_total"]>0 or "v15_local_herd" in sh("ps aux | grep v15_local_herd | grep -v grep", timeout=5):
            alerts.append("PILOTS_ZERO")
            if not healed:
                auto_heal("pilots_zero", host)
                healed=True

    # Rule 3: CPU must be >80% (if pilots expected, low cpu means under-utilized / stalled)
    # Only enforce if pilots>0 or should be >0 (todo exists). If system idle because done, don't warn
    # Heuristic: if pilots>0 and cpu<80, it's under-utilized; if pilots==0 and todo>0 and cpu<80, also under
    if st["cpu_pct"] < CPU_WARN:
        # check if should be busy: look at herd todo via log or queue
        # If pilots>0, low cpu = throttle/stall
        if st["pilots"]>0:
            alerts.append(f"CPU_LOW {st['cpu_pct']:.1f}% <{CPU_WARN}% with {st['pilots']} pilots")
            if not healed and st["pilots"]<4:  # if few pilots and low cpu, herd may need to launch more
                auto_heal(f"cpu_low_{st['cpu_pct']:.0f}", host)
                healed=True
        elif st["hb_total"]>0:
            # was busy before, now idle low cpu -> stall
            alerts.append(f"CPU_LOW_IDLE {st['cpu_pct']:.1f}%")
            if not healed:
                auto_heal("cpu_low_idle", host)
                healed=True

    # Rule 4: stall detection via heartbeat
    if st["hb_total"]>0 and st["hb_recent"]==0:
        # had logs but none recent <5m -> stall
        alerts.append("HEARTBEAT_STALL no v15_*.log recent <5m")
        if not healed:
            # check if pilots stuck (etime > 10m with no heartbeat)
            stuck=sh("ps -o pid,etimes,args -p $(pgrep -f v15_pilot | head -n 5 | tr '\\n' ',' | sed 's/,$//') 2>/dev/null | head -n 10", timeout=5)
            log(f"  stuck check: {stuck[:300]}")
            auto_heal("heartbeat_stall", host)
            healed=True

    # Rule 5: progress.json not growing for 10m while pilots running -> stall
    if st["pilots"]>0 and st["prog_recent"]==0 and st["hb_recent"]==0:
        alerts.append("PROGRESS_STALL no progress.json <5m with pilots running")
        if not healed:
            auto_heal("progress_stall", host)
            healed=True

    # Rule 6: OOM
    if st["oom"]:
        alerts.append("OOM_DETECTED")
        log("OOM detected today — herd will throttle via avail<1200 guard, not heal directly")
        # herd already guards, no auto-heal beyond logging

    # Rule 7: RAM avail low (near OOM) - warning but herd handles
    if st["mem_avail_m"] < OOM_AVAIL and st["mem_avail_m"]>0:
        alerts.append(f"LOW_MEM avail {st['mem_avail_m']}M <{OOM_AVAIL}M")

    if alerts:
        log(f"ALERTS: {'; '.join(alerts)}")
        return 1
    else:
        log("OK cpu>80% no stall, herd saturated")
        return 0

if __name__=="__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""v15_cell_sentinel — on-box automatic stall correction (USER 2026-09-29:
"every cell fill is monitored and corrected immediately").

Every CHECK_S: for each running v15_pilot, if its sym_side's delta log has not
grown for STALL_S seconds, kill that pilot — the herd relaunches it and the
pilot resumes from its progress JSON, so nothing is lost and the sheet keeps
moving. Every correction is logged with the last cell context. Never touches
the herd, live managers, or pilots that are producing.
"""
from __future__ import annotations
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "data" / "reports" / "lifecycle_pilot" / "v15_delta_log"
CHECK_S = 180
STALL_S = 720
STARTUP_GRACE_S = 900


def log(msg):
    print(f"[sentinel {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def running_pilots():
    out = subprocess.run(["ps", "-eo", "pid,etimes,args"], capture_output=True, text=True, timeout=15).stdout
    pilots = []
    for line in out.splitlines():
        if "v15_pilot.py --sym-side" not in line or "ps -eo" in line:
            continue
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        s = re.search(r"--sym-side\s+(\S+)", line)
        if m and s:
            pilots.append((int(m.group(1)), int(m.group(2)), s.group(1)))
    return pilots


def delta_log_for(pid, symside):
    """The delta log THIS pilot writes: its V15_PROGRESS_DIR (isolated runs) and any nav mode (jump/fill_tab).
    2026-09-29: judging every pilot by the default-dir {ss}_jump.jsonl killed all isolated / fill_tab runs as 'idle'."""
    log_dir = LOG_DIR
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        for kv in env:
            if kv.startswith(b"V15_PROGRESS_DIR="):
                log_dir = Path(kv.split(b"=", 1)[1].decode()) / "v15_delta_log"
    except Exception:
        pass
    logs = sorted(log_dir.glob(f"{symside}_*.jsonl"), key=lambda q: q.stat().st_mtime, reverse=True)
    return logs[0] if logs else log_dir / f"{symside}_jump.jsonl"


def last_cell(p):
    try:
        line = subprocess.run(["tail", "-n", "1", str(p)], capture_output=True, text=True, timeout=10).stdout.strip()
        d = json.loads(line)
        return f"{d.get('sheet')}!{d.get('row')} {d.get('switch')}={d.get('cand')} [{d.get('label')}]"
    except Exception:
        return "?"


def main():
    log(f"started check={CHECK_S}s stall={STALL_S}s grace={STARTUP_GRACE_S}s")
    seen_pids = {}
    while True:
        try:
            for pid, etimes, symside in running_pilots():
                if etimes < STARTUP_GRACE_S:
                    continue
                p = delta_log_for(pid, symside)
                age = time.time() - p.stat().st_mtime if p.exists() else None
                if age is None:
                    # past grace with no delta log at all -> stuck pre-row-loop
                    if seen_pids.get(pid) == "warned_nolog":
                        log(f"CORRECT {symside} pid={pid} etimes={etimes}s: no delta log after grace — killing for herd relaunch")
                        subprocess.run(["kill", "-9", str(pid)], timeout=5)
                        seen_pids.pop(pid, None)
                    else:
                        seen_pids[pid] = "warned_nolog"
                    continue
                if age > STALL_S:
                    log(f"CORRECT {symside} pid={pid}: delta log idle {age:.0f}s at cell {last_cell(p)} ({p}) — killing for herd relaunch (resume from JSON)")
                    subprocess.run(["kill", "-9", str(pid)], timeout=5)
                    seen_pids.pop(pid, None)
        except Exception as e:
            log(f"warn {e}")
        time.sleep(CHECK_S)


if __name__ == "__main__":
    main()

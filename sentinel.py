#!/usr/bin/env python3
"""
sentinel.py — single-instance log + sweep watcher.

- PID-locked (refuses duplicate). Location-agnostic (macbook / S1 / S2).
- Watches backtest_v8/sweeps/*.csv for: dead-params (low Sharpe stdev),
  duplicate result rows (same Sharpe+PnL+Trades+W/L across configs).
- Watches ~/logs/*.log and ~/binance/logs/*.log for: Traceback, ERROR,
  IP bans (-1003), "poisoned", "duplicate order".
- On detection: SIGSTOP the offending process group (reversible), write
  incident JSON to data/sentinel/incidents/, optionally spawn a Claude
  Code fix-agent subprocess if SENTINEL_AUTO_FIX=1.
- Continues monitoring; clears incident only when /tmp/sentinel.ack touched.

Usage:
  python3 sentinel.py                  # run forever
  python3 sentinel.py --once           # single scan, exit
  SENTINEL_AUTO_FIX=1 python3 sentinel.py  # enable agent auto-spawn

Env:
  SENTINEL_AUTO_FIX        : "1" to spawn fix agent on incident (default off)
  SENTINEL_DEAD_STDEV      : dead-sweep threshold (default 0.01)
  SENTINEL_DEAD_MIN_ROWS   : rows needed before dead check (default 10)
  SENTINEL_POLL_SEC        : scan interval seconds (default 30)
"""
import csv
import glob
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import pstdev
from datetime import datetime, timezone

HOST = socket.gethostname()
HOME = Path(os.path.expanduser("~"))
BASE = Path(__file__).resolve().parent
INCIDENT_DIR = BASE / "data" / "sentinel" / "incidents"
STATE_FILE = BASE / "data" / "sentinel" / "state.json"
PID_FILE = Path("/tmp/sentinel.pid")
ACK_FILE = Path("/tmp/sentinel.ack")
INCIDENT_DIR.mkdir(parents=True, exist_ok=True)

POLL_SEC = int(os.environ.get("SENTINEL_POLL_SEC", "30"))
DEAD_STDEV = float(os.environ.get("SENTINEL_DEAD_STDEV", "0.01"))
DEAD_MIN_ROWS = int(os.environ.get("SENTINEL_DEAD_MIN_ROWS", "10"))
AUTO_FIX = os.environ.get("SENTINEL_AUTO_FIX", "1") == "1"
BACKUP_DIR = BASE / "backups" / "sentinel"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
CRITICAL_FILES = [
    "ez_manage.py", "ez_positions_service.py", "ez_positions_quick.py",
    "tradier_manage.py", "tradier_positions.py", "config.py", "config_tradier.py",
    "utils.py", "backtest_v8_engine.py", "backtest_v8_sweep.py",
]
FRESH_SEC = int(os.environ.get("SENTINEL_FRESH_SEC", "21600"))  # only scan files modified in last 6h

SWEEP_CSV_GLOBS = [
    str(BASE / "backtest_v8" / "sweeps" / "v8_sweep_*.csv"),
    str(HOME / "binance" / "backtest_v8" / "sweeps" / "v8_sweep_*.csv"),
    str(HOME / "binance-sandbox" / "backtest_v8" / "sweeps" / "v8_sweep_*.csv"),
]
LOG_GLOBS = [
    str(HOME / "logs" / "*.log"),
    str(HOME / "binance" / "logs" / "*.log"),
    str(BASE / "logs" / "*.log"),
    "/tmp/v8_*.log",
]
LOG_PATTERNS = [
    "Traceback (most recent",
    "-1003",
    "banned",
    "poisoned",
    "duplicate order",
    "orphaned symbol",
    "FATAL",
    "MemoryError",
]
LOG_IGNORE = [
    "test ",
    "expected error",
    "INFO",
]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def acquire_lock():
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            os.kill(old, 0)
            log(f"sentinel already running pid={old}; exit")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            PID_FILE.unlink(missing_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def release_lock():
    try:
        if PID_FILE.exists() and int(PID_FILE.read_text().strip()) == os.getpid():
            PID_FILE.unlink()
    except Exception:
        pass


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def find_sweep_pids():
    try:
        out = subprocess.check_output(["pgrep", "-af", "backtest_v8_sweep.py"], text=True)
    except subprocess.CalledProcessError:
        return []
    pids = []
    for line in out.strip().splitlines():
        parts = line.split(None, 1)
        if parts:
            try:
                pids.append(int(parts[0]))
            except ValueError:
                pass
    return pids


def sigstop(pids):
    stopped = []
    for p in pids:
        try:
            os.kill(p, signal.SIGSTOP)
            stopped.append(p)
        except Exception as e:
            log(f"SIGSTOP {p} failed: {e}")
    return stopped


def write_incident(kind, detail, offending_pids=None, stopped_pids=None):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    incident = {
        "ts": ts,
        "host": HOST,
        "kind": kind,
        "detail": detail,
        "offending_pids": offending_pids or [],
        "stopped_pids": stopped_pids or [],
        "ack_file": str(ACK_FILE),
    }
    path = INCIDENT_DIR / f"incident_{ts}_{kind}.json"
    path.write_text(json.dumps(incident, indent=2, default=str))
    log(f"INCIDENT {kind} -> {path}")
    return path


def snapshot_critical_files(tag):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snap = BACKUP_DIR / f"{ts}_{tag}"
    snap.mkdir(parents=True, exist_ok=True)
    for f in CRITICAL_FILES:
        src = BASE / f
        if src.exists():
            try:
                (snap / f).write_bytes(src.read_bytes())
            except Exception as e:
                log(f"backup {f} failed: {e}")
    log(f"snapshot -> {snap}")
    return snap


def spawn_fix_agent(incident_path):
    if not AUTO_FIX:
        log("AUTO_FIX off; skipping agent spawn")
        return
    snap = snapshot_critical_files(f"pre_fix_{incident_path.stem}")
    is_macbook = sys.platform == "darwin"
    prompt = (
        f"HANDS_OFF sentinel incident auto-fix. Read incident at {incident_path}. "
        f"Pre-fix backup at {snap}. Diagnose root cause in {BASE} and apply minimal "
        f"fix. MANDATORY before ANY edit: cp <file> backups/before_sentinel_fix_$(date +%Y%m%d%H%M).py. "
        f"NEVER revert newer code to older. Prefer editing sweep/engine scripts over live "
        f"trading scripts (ez_manage, ez_positions_*, tradier_manage). "
        f"After fix: rm {ACK_FILE} so sentinel resumes."
    )
    if not is_macbook:
        log(f"non-macbook host; writing pending-fix flag for macbook pickup")
        pending = INCIDENT_DIR / f"{incident_path.stem}.pending_macbook"
        pending.write_text(json.dumps({"incident": str(incident_path), "prompt": prompt, "snap": str(snap), "host": HOST}))
        return
    try:
        safe_prompt = prompt.replace('"', '\\"').replace("\n", " ")
        cmd_line = f"cd {BASE} && echo 'SENTINEL FIX AGENT — incident {incident_path.name}' && claude --dangerously-skip-permissions \\\"{safe_prompt}\\\""
        applescript = (
            'tell application "iTerm2"\n'
            '  activate\n'
            '  if (count of windows) = 0 then\n'
            '    create window with default profile\n'
            '  end if\n'
            '  tell current window\n'
            '    create tab with default profile\n'
            '    tell current session of current tab\n'
            f'      write text "{cmd_line}"\n'
            '    end tell\n'
            '  end tell\n'
            'end tell'
        )
        subprocess.Popen(["osascript", "-e", applescript], start_new_session=True)
        log(f"spawned fix agent in iTerm2 tab for {incident_path.name}")
    except Exception as e:
        log(f"agent spawn failed: {e}")


def pull_remote_incidents():
    """macbook only — rsync S1/S2 incidents dirs so their DUPE/ERROR findings trigger local Terminal agents."""
    if sys.platform != "darwin":
        return
    remotes = [
        ("s1", "niels@157.180.125.52", "/home/niels/binance/data/sentinel/incidents/"),
        ("s2", "niels@204.168.181.211", "/home/niels/binance/data/sentinel/incidents/"),
    ]
    for tag, host, path in remotes:
        local = INCIDENT_DIR.parent / f"incidents_{tag}"
        local.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["rsync", "-az", "--timeout=10", f"{host}:{path}", str(local) + "/"],
                timeout=20, capture_output=True,
            )
        except Exception as e:
            log(f"rsync {tag} failed: {e}")
            continue
        for pending in local.glob("*.pending_macbook"):
            try:
                data = json.loads(pending.read_text())
            except Exception:
                continue
            marker = INCIDENT_DIR / f"{tag}_{pending.stem}.seen"
            if marker.exists():
                continue
            marker.write_text(data.get("host", tag))
            fake_inc = INCIDENT_DIR / f"{tag}_{pending.stem}.json"
            fake_inc.write_text(json.dumps({"remote": tag, "original": data}, indent=2))
            log(f"picked up remote incident from {tag}: {pending.name}")
            try:
                safe_prompt = data["prompt"].replace('"', '\\"').replace("\n", " ")
                cmd_line = f"cd {BASE} && echo 'SENTINEL REMOTE FIX ({tag}) — {pending.name}' && claude --dangerously-skip-permissions \\\"{safe_prompt}\\\""
                applescript = (
                    'tell application "iTerm2"\n'
                    '  activate\n'
                    '  if (count of windows) = 0 then\n'
                    '    create window with default profile\n'
                    '  end if\n'
                    '  tell current window\n'
                    '    create tab with default profile\n'
                    '    tell current session of current tab\n'
                    f'      write text "{cmd_line}"\n'
                    '    end tell\n'
                    '  end tell\n'
                    'end tell'
                )
                subprocess.Popen(["osascript", "-e", applescript], start_new_session=True)
            except Exception as e:
                log(f"remote agent spawn failed: {e}")


def check_sweep_csvs(state):
    incidents = []
    now = time.time()
    for g in SWEEP_CSV_GLOBS:
        for csv_path in glob.glob(g):
            try:
                if now - os.path.getmtime(csv_path) > FRESH_SEC:
                    continue
            except OSError:
                continue
            try:
                with open(csv_path) as f:
                    rows = list(csv.DictReader(f))
            except Exception:
                continue
            if len(rows) < DEAD_MIN_ROWS:
                continue
            key = f"csv::{csv_path}"
            last_rows = state.get(key, 0)
            if len(rows) == last_rows:
                continue
            state[key] = len(rows)
            sharpe_col = next((c for c in rows[0].keys() if c.lower() == "sharpe"), None)
            if not sharpe_col:
                continue
            try:
                sharpes = [float(r[sharpe_col]) for r in rows if r.get(sharpe_col) not in (None, "", "nan")]
            except ValueError:
                continue
            if len(sharpes) >= DEAD_MIN_ROWS:
                sd = pstdev(sharpes)
                if sd < DEAD_STDEV:
                    incidents.append(("DEAD_PARAMS", {
                        "csv": csv_path,
                        "rows": len(rows),
                        "sharpe_stdev": sd,
                        "sharpe_range": [min(sharpes), max(sharpes)],
                        "threshold": DEAD_STDEV,
                    }))
            fingerprint_cols = [c for c in rows[0].keys() if c.lower() in ("sharpe", "pnl", "trades", "wins", "losses")]
            if fingerprint_cols:
                buckets = defaultdict(list)
                for r in rows:
                    fp = tuple(r.get(c, "") for c in fingerprint_cols)
                    name = r.get("name") or r.get("config") or ""
                    buckets[fp].append(name)
                dup_groups = [(fp, names) for fp, names in buckets.items() if len(names) >= 3]
                if dup_groups:
                    incidents.append(("DUPE_RESULTS", {
                        "csv": csv_path,
                        "groups": len(dup_groups),
                        "sample_fp": list(dup_groups[0][0]),
                        "sample_names": dup_groups[0][1][:5],
                        "rows": len(rows),
                    }))
    return incidents


def check_logs(state):
    incidents = []
    now = time.time()
    for g in LOG_GLOBS:
        for log_path in glob.glob(g):
            try:
                st = os.stat(log_path)
            except OSError:
                continue
            if now - st.st_mtime > FRESH_SEC:
                continue
            key = f"log::{log_path}"
            last_pos = state.get(key, st.st_size)
            if st.st_size < last_pos:
                last_pos = 0
            if st.st_size == last_pos:
                continue
            try:
                with open(log_path, "rb") as f:
                    f.seek(last_pos)
                    chunk = f.read(200_000).decode("utf-8", errors="replace")
            except Exception:
                continue
            state[key] = st.st_size
            hits = []
            for line in chunk.splitlines():
                if any(p in line for p in LOG_PATTERNS) and not any(q in line for q in LOG_IGNORE):
                    hits.append(line[:300])
            if hits:
                incidents.append(("LOG_ERROR", {
                    "log": log_path,
                    "hits": hits[:10],
                    "count": len(hits),
                }))
    return incidents


def scan_once(state):
    if ACK_FILE.exists():
        log("ack present; skipping scan")
        return
    incidents = []
    incidents.extend(check_sweep_csvs(state))
    incidents.extend(check_logs(state))
    if not incidents:
        return
    sweep_pids = find_sweep_pids()
    stopped = sigstop(sweep_pids) if sweep_pids else []
    seen_keys = set()
    unique = []
    for kind, detail in incidents:
        dedup_key = (kind, detail.get("csv") or detail.get("log") or "")
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        unique.append((kind, detail))
    for kind, detail in unique:
        inc_path = write_incident(kind, detail, offending_pids=sweep_pids, stopped_pids=stopped)
        spawn_fix_agent(inc_path)
    log(f"detected {len(unique)} incidents; stopped pids={stopped}; touch {ACK_FILE} to resume")


def main():
    if "--once" in sys.argv:
        state = load_state()
        scan_once(state)
        save_state(state)
        return
    acquire_lock()
    try:
        state = load_state()
        log(f"sentinel up host={HOST} base={BASE} auto_fix={AUTO_FIX} poll={POLL_SEC}s")
        while True:
            try:
                pull_remote_incidents()
                scan_once(state)
                save_state(state)
            except Exception as e:
                log(f"scan error: {e}")
            time.sleep(POLL_SEC)
    finally:
        release_lock()


if __name__ == "__main__":
    main()

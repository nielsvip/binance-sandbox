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
AUTO_FIX = os.environ.get("SENTINEL_AUTO_FIX", "0") == "1"
FRESH_SEC = int(os.environ.get("SENTINEL_FRESH_SEC", "21600"))  # only scan files modified in last 6h

SWEEP_CSV_GLOBS = [
    str(BASE / "backtest_v8" / "sweeps" / "v8_sweep_*.csv"),
    str(HOME / "binance" / "backtest_v8" / "sweeps" / "v8_sweep_*.csv"),
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


def spawn_fix_agent(incident_path):
    if not AUTO_FIX:
        log("AUTO_FIX off; skipping agent spawn")
        return
    prompt = (
        f"HANDS_OFF sentinel incident. Read {incident_path}. Diagnose root cause "
        f"in /Users/niels/Documents/binance. DO NOT modify ez_manage.py, "
        f"ez_positions_service.py, ez_positions_quick.py, tradier_manage.py "
        f"without explicit user approval. Propose a patch written to "
        f"data/sentinel/proposed_fixes/. Do not apply to live code."
    )
    try:
        subprocess.Popen(
            ["claude", "--dangerously-skip-permissions", "-p", prompt],
            cwd=str(BASE),
            stdout=open(INCIDENT_DIR / f"{incident_path.stem}_agent.log", "w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log(f"spawned fix agent for {incident_path.name}")
    except Exception as e:
        log(f"agent spawn failed: {e}")


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
                scan_once(state)
                save_state(state)
            except Exception as e:
                log(f"scan error: {e}")
            time.sleep(POLL_SEC)
    finally:
        release_lock()


if __name__ == "__main__":
    main()

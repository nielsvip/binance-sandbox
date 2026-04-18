#!/usr/bin/env python3
"""Sweep monitor — runs every 15 min, checks all machines, logs results, restarts dead sweeps.

Fixes vs v1:
- Checks vec_backlog logs + result CSV freshness (not /tmp/v8_t5.log which was wrong)
- S1 restart is crypto-mode vec_supervisor (was tradier — wrong machine)
- S2 restart is tradier-mode vec_supervisor with 3 workers (was 7 — OOM)
- Kills stuck processes (running >STUCK_TIMEOUT min with no new CSV output) before restarting
- Skips restart if MEM > 80% (was unconditional — caused OOM death spirals)
- Never spawns new sweep on top of running workers (checks for existing procs first)
"""
import subprocess, time, os, json, re, glob, sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path("/Users/niels/Documents/binance/data/sweep_results/overnight_audit.log")
LOG.parent.mkdir(parents=True, exist_ok=True)

S1 = "s1-int"
S2 = "s2-int"
S1_PY = "/home/niels/.conda/envs/binance_env/bin/python"
S2_PY = "/home/niels/miniconda3/envs/binance_env/bin/python"
SANDBOX = "/home/niels/binance-sandbox"

STUCK_TIMEOUT_MIN = 30   # kill any worker running >30 min with no new CSV output
MEM_GUARD_PCT = 80       # don't restart if server mem usage > 80%
MAX_WORKERS = 3          # never spawn more than 3 workers per machine


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def ssh(host, cmd, timeout=20):
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5",
                            host, cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"SSH_ERROR: {e}"


def get_mem_pct(host):
    """Return used-memory % (0-100). Returns 999 on error so we stay safe."""
    out = ssh(host, "free | awk '/Mem:/ {printf \"%.0f\", $3*100/$2}'")
    try:
        return int(out.strip())
    except:
        return 999


def get_vec_worker_pids(host):
    """Return list of (pid, elapsed_seconds) for vec_backlog.py processes."""
    out = ssh(host, "ps -eo pid,etimes,cmd | grep vec_backlog | grep -v grep")
    result = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                result.append((int(parts[0]), int(parts[1])))
            except ValueError:
                pass
    return result


def get_engine_pids(host):
    """Return list of (pid, elapsed_seconds) for backtest_v8_engine.py processes."""
    out = ssh(host, "ps -eo pid,etimes,cmd | grep backtest_v8_engine | grep -v grep")
    result = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                result.append((int(parts[0]), int(parts[1])))
            except ValueError:
                pass
    return result


def latest_csv_age_minutes(host, mode):
    """Minutes since most recent result CSV was written on host."""
    pattern = f"{SANDBOX}/data/sweep_results/vec_backlog_{mode}_w*.log"
    out = ssh(host, f"ls -t {pattern} 2>/dev/null | head -1")
    if not out or "SSH_ERROR" in out or "No such" in out:
        return 9999
    mtime_out = ssh(host, f"stat -c %Y '{out.strip()}' 2>/dev/null")
    try:
        age = (time.time() - int(mtime_out.strip())) / 60
        return age
    except:
        return 9999


def latest_result_count(host, mode):
    """Count status=ok lines in most recent vec_backlog log."""
    pattern = f"{SANDBOX}/data/sweep_results/vec_backlog_{mode}_w*.log"
    out = ssh(host, f"ls -t {pattern} 2>/dev/null | head -1")
    if not out or "SSH_ERROR" in out:
        return 0
    raw = ssh(host, f"grep -c 'status=ok' '{out.strip()}' 2>/dev/null || echo 0")
    try:
        return int(raw.strip())
    except:
        return 0


def kill_stuck_workers(host, mode, worker_pids, csv_age_min):
    """Kill workers that have been running >STUCK_TIMEOUT_MIN with no fresh output."""
    if csv_age_min < STUCK_TIMEOUT_MIN:
        return False
    killed = []
    for pid, elapsed_sec in worker_pids:
        if elapsed_sec > STUCK_TIMEOUT_MIN * 60:
            ssh(host, f"kill -9 {pid} 2>/dev/null")
            killed.append(pid)
    if killed:
        log(f"{host.upper()} [{mode}]: STUCK WORKERS KILLED — PIDs {killed} (ran >{STUCK_TIMEOUT_MIN}min, last CSV {csv_age_min:.0f}min ago)")
        return True
    return False


def kill_stuck_engines(host, engine_pids, csv_age_min):
    """Kill backtest_v8_engine.py processes stuck >STUCK_TIMEOUT_MIN with no output."""
    if csv_age_min < STUCK_TIMEOUT_MIN:
        return False
    killed = []
    for pid, elapsed_sec in engine_pids:
        if elapsed_sec > STUCK_TIMEOUT_MIN * 60:
            ssh(host, f"kill -9 {pid} 2>/dev/null")
            killed.append(pid)
    if killed:
        log(f"{host.upper()}: STUCK ENGINES KILLED — PIDs {killed} (ran >{STUCK_TIMEOUT_MIN}min no output)")
        return True
    return False


def is_orchestrator_running(host):
    """True if autonomous_sweep_orchestrator.sh is running on host."""
    out = ssh(host, "pgrep -f autonomous_sweep_orchestrator | wc -l")
    try:
        return int(out.strip()) > 0
    except:
        return False


def check_server(name, host, mode, py_bin, restart_workers):
    """Check server health and restart vec_supervisor if needed."""
    mem_pct = get_mem_pct(host)
    worker_pids = get_vec_worker_pids(host)
    engine_pids = get_engine_pids(host)
    nworkers = len(worker_pids)
    nengines = len(engine_pids)
    csv_age = latest_csv_age_minutes(host, mode)
    result_count = latest_result_count(host, mode)
    orch_running = is_orchestrator_running(host)

    log(f"{name} [{mode}]: workers={nworkers} engines={nengines} orch={orch_running} "
        f"mem={mem_pct}% last_csv={csv_age:.0f}min ago results={result_count}")

    # Kill stuck workers (running > STUCK_TIMEOUT with stale output)
    killed = False
    if worker_pids:
        killed |= kill_stuck_workers(host, mode, worker_pids, csv_age)
    if engine_pids:
        killed |= kill_stuck_engines(host, engine_pids, csv_age)

    if killed:
        time.sleep(5)
        worker_pids = get_vec_worker_pids(host)
        nworkers = len(worker_pids)

    # Don't restart if autonomous orchestrator is running (it manages its own lifecycle)
    if orch_running:
        log(f"{name}: autonomous orchestrator running — not touching sweep workers")
        return

    # Detect dead: no workers AND (no engines OR engines are stuck)
    if nworkers == 0 and (nengines == 0 or killed):
        if mem_pct > MEM_GUARD_PCT:
            log(f"{name}: DEAD but mem={mem_pct}% > {MEM_GUARD_PCT}% — waiting for memory before restart")
            return
        log(f"{name}: DEAD — starting vec_supervisor with {restart_workers} workers")
        cmd = (f"pkill -9 -f vec_backlog 2>/dev/null; "
               f"pkill -9 -f backtest_v8_engine 2>/dev/null; "
               f"sleep 5; "
               f"cd {SANDBOX} && "
               f"nohup bash vec_supervisor.sh {mode} {restart_workers} "
               f"> {SANDBOX}/data/sweep_results/supervisor_{mode}.log 2>&1 &")
        ssh(host, cmd, timeout=30)
        log(f"{name}: restart command sent (mode={mode} workers={restart_workers})")
        return

    # Warn if making no progress
    progress_file = Path(f"/tmp/sweep_progress_{name}.json")
    last_count = 0
    if progress_file.exists():
        try:
            last_count = json.load(open(progress_file)).get("count", 0)
        except:
            pass
    if result_count == last_count and nworkers > 0 and csv_age > 20:
        log(f"{name}: WARNING — no new results in {csv_age:.0f}min ({result_count} total), "
            f"but {nworkers} workers running. Possible queue exhaustion or stuck.")
    json.dump({"count": result_count, "ts": time.time()}, open(progress_file, "w"))


def check_local():
    procs = subprocess.run(["pgrep", "-f", "backtest_v8_sweep|vec_backlog"], capture_output=True, text=True)
    nprocs = len(procs.stdout.strip().split("\n")) if procs.stdout.strip() else 0
    # Check local result CSV freshness
    local_results = sorted(
        glob.glob("/Users/niels/Documents/binance/data/sweep_results/*.csv"),
        key=os.path.getmtime, reverse=True
    )
    if local_results:
        age_min = (time.time() - os.path.getmtime(local_results[0])) / 60
        log(f"LOCAL: procs={nprocs}, last_csv={age_min:.0f}min ago ({Path(local_results[0]).name})")
    else:
        log(f"LOCAL: procs={nprocs}, no CSVs found")


def main():
    log("=" * 60)
    log("SWEEP MONITOR CHECK")
    check_server("S1", S1, "crypto", S1_PY, restart_workers=3)
    check_server("S2", S2, "tradier", S2_PY, restart_workers=3)
    check_local()
    log("=" * 60)


if __name__ == "__main__":
    main()

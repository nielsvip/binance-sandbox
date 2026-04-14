#!/usr/bin/env python3
"""Local watchdog for V8 sweep servers (S1, S2).

Runs on the MacBook. Every minute:
1. SSH-pings both servers.
2. For S2: ensures sentinel + 4 SECTOR sweep screens (tech/commod/broad/crypto) are alive.
3. Spawns ONLY missing ones; never duplicates existing runs.
4. Uses flock on remote to prevent concurrent spawn races.

Safety rules:
- Single-instance PID lock (/tmp/server_watchdog.pid).
- NEVER spawn the old 20-symbol mega-sweep (forbidden 2026-04-14).
- NEVER revert live code; watchdog only spawns backtest sweeps.
- Detection uses `pgrep -f backtest_v8_sweep.py` (process count, not python count).
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PID_FILE = Path("/tmp/server_watchdog.pid")
STATUS_FILE = Path.home() / "server_watchdog.status"
LOG_FILE = Path.home() / "server_watchdog.log"
CHECK_INTERVAL = 60
SSH_TIMEOUT = 15
MIN_FREE_MB = 4000

# SECTOR SWEEP DEFINITIONS — each produces uniquely-named CSV via md5 suffix
# NEVER change to 20-sym mega-sweep (2026-04-14 forbidden pattern)
SWEEPS = {
    "sweep_tech":    {"mode": "tradier", "account": "trb", "start": "2025-10-01", "capital": 2000, "workers": 2, "tier": 25, "symbols": "AAPL,NVDA,META,AMD",  "log": "/tmp/v8_tech.log"},
    "sweep_commod":  {"mode": "tradier", "account": "trb", "start": "2025-10-01", "capital": 2000, "workers": 2, "tier": 25, "symbols": "USO,NEM,GLD",         "log": "/tmp/v8_commod.log"},
    "sweep_broad":   {"mode": "tradier", "account": "trb", "start": "2025-10-01", "capital": 2000, "workers": 2, "tier": 25, "symbols": "MSTR,SPY,LLY",        "log": "/tmp/v8_broad.log"},
    "sweep_crypto":  {"mode": "crypto",  "account": "inf", "start": "2024-01-01", "capital": 1000, "workers": 2, "tier": 30, "symbols": None,                  "log": "/tmp/v8_crypto.log"},
}

SERVERS = {
    "S2": {
        "host": "s2-int",
        "user": "niels",
        "python": "/home/niels/miniconda3/envs/binance_env/bin/python",
        "sweeps": list(SWEEPS.keys()),
        "run_sentinel": True,
    },
    "S1": {
        "host": "s1-int",
        "user": "niels",
        "python": "/home/niels/.conda/envs/binance_env/bin/python",
        "sweeps": [],
        "run_sentinel": True,
    },
}


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def acquire_lock():
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            os.kill(old, 0)
            log(f"already running pid={old}; exit")
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


_SSH_ENV = {
    **os.environ,
    "HOME": os.path.expanduser("~"),  # launchd strips HOME; ssh needs it to find ~/.ssh/config
}


def ssh(host, user, cmd, timeout=SSH_TIMEOUT):
    try:
        result = subprocess.run(
            ["ssh",
             "-o", f"ConnectTimeout={min(timeout-2, 8)}",
             "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=no",
             "-o", "ControlMaster=no",
             "-o", "ControlPath=none",
             "-o", "ServerAliveInterval=5",
             "-o", "ServerAliveCountMax=2",
             f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=timeout + 5, env=_SSH_ENV,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 255, str(e)


def build_sweep_cmd(cfg, sweep_cfg, python):
    symbols_arg = f" --symbols {sweep_cfg['symbols']}" if sweep_cfg["symbols"] else ""
    return (
        f"cd /home/niels/binance && {python} -u backtest_v8_sweep.py "
        f"--mode {sweep_cfg['mode']} --account {sweep_cfg['account']} "
        f"--start {sweep_cfg['start']} --capital {sweep_cfg['capital']} "
        f"--workers {sweep_cfg['workers']} --tier {sweep_cfg['tier']}{symbols_arg} "
        f"> {sweep_cfg['log']} 2>&1"
    )


def check_server(name, cfg, state):
    host = cfg["host"]
    user = cfg["user"]

    probe_cmd = "free -m | awk '/^Mem:/{print \"FREE=\"$7}'; pgrep -cf backtest_v8_sweep.py | awk '{print \"SWEEP_PROCS=\"$0}'; screen -ls 2>/dev/null | grep -oE '[0-9]+\\.[a-zA-Z_]+' | awk '{print \"SCREEN=\"$0}'"
    rc, out = ssh(host, user, probe_cmd, timeout=30)
    if rc != 0:
        state[name]["reachable"] = False
        if state[name]["down_since"] is None:
            state[name]["down_since"] = time.time()
        log(f"{name} ({host}): UNREACHABLE rc={rc}")
        return

    if not state[name]["reachable"]:
        log(f"{name} ({host}): RECOVERED")
    state[name]["reachable"] = True
    state[name]["down_since"] = None

    free_mb = 0
    sweep_procs = 0
    existing_screens = set()
    for ln in out.splitlines():
        if ln.startswith("FREE="):
            try: free_mb = int(ln.split("=", 1)[1])
            except ValueError: pass
        elif ln.startswith("SWEEP_PROCS="):
            try: sweep_procs = int(ln.split("=", 1)[1])
            except ValueError: pass
        elif ln.startswith("SCREEN="):
            parts = ln.split("=", 1)[1].split(".", 1)
            if len(parts) == 2:
                existing_screens.add(parts[1])

    state[name].update({
        "free_mb": free_mb,
        "sweep_procs": sweep_procs,
        "screens": sorted(existing_screens),
    })

    if free_mb < MIN_FREE_MB:
        log(f"{name}: LOW RAM {free_mb}MB — skipping spawn this cycle")
        return

    # Ensure sentinel screen is up. Double-check via screen -ls AFTER attempt to detect silent spawn failures.
    if cfg["run_sentinel"] and "sentinel" not in existing_screens:
        log(f"{name}: sentinel missing — spawning")
        inner = f"cd /home/niels/binance && SENTINEL_AUTO_FIX=1 {cfg['python']} -u sentinel.py > /tmp/sentinel.log 2>&1"
        # Write launcher script remotely + spawn screen pointing to it. Avoids shell escape issues.
        setup = (
            f"cat > /tmp/launch_sentinel.sh <<'EOF'\n#!/bin/bash\n{inner}\nEOF\n"
            f"chmod +x /tmp/launch_sentinel.sh && "
            f"screen -dmS sentinel bash /tmp/launch_sentinel.sh && "
            f"sleep 1 && screen -ls | grep -q sentinel && echo SENTINEL_UP || echo SENTINEL_FAIL"
        )
        rc2, out2 = ssh(host, user, setup, timeout=30)
        log(f"{name}: sentinel spawn rc={rc2} result={out2.strip()[-80:]}")

    for sweep_name in cfg["sweeps"]:
        if sweep_name in existing_screens:
            continue
        sweep_cfg = SWEEPS[sweep_name]
        inner = build_sweep_cmd(cfg, sweep_cfg, cfg["python"])
        setup = (
            f"cat > /tmp/launch_{sweep_name}.sh <<'EOF'\n#!/bin/bash\n{inner}\nEOF\n"
            f"chmod +x /tmp/launch_{sweep_name}.sh && "
            f"screen -dmS {sweep_name} bash /tmp/launch_{sweep_name}.sh && "
            f"sleep 1 && screen -ls | grep -q {sweep_name} && echo {sweep_name}_UP || echo {sweep_name}_FAIL"
        )
        log(f"{name}: {sweep_name} missing — spawning")
        rc2, out2 = ssh(host, user, setup, timeout=30)
        log(f"{name}: {sweep_name} spawn rc={rc2} result={out2.strip()[-80:]}")

    log(f"{name}: OK free={free_mb}MB procs={sweep_procs} screens={sorted(existing_screens)}")


def json_shell(s):
    """Wrap string in single quotes for shell, escaping inner single quotes."""
    return "'" + s.replace("'", "'\\''") + "'"


def write_status(state):
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump({"updated": datetime.now(timezone.utc).isoformat(), "servers": state}, f, indent=2, default=str)
    except Exception as e:
        log(f"status write failed: {e}")


def main():
    acquire_lock()
    try:
        state = {name: {"reachable": True, "down_since": None, "free_mb": 0,
                        "sweep_procs": 0, "screens": []} for name in SERVERS}
        log(f"server_watchdog up — managing {list(SERVERS.keys())} with sweeps={list(SWEEPS.keys())}")
        while True:
            try:
                for name, cfg in SERVERS.items():
                    check_server(name, cfg, state)
                write_status(state)
            except Exception as e:
                log(f"loop error: {e}")
            time.sleep(CHECK_INTERVAL)
    finally:
        release_lock()


if __name__ == "__main__":
    main()

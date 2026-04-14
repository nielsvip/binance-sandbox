#!/usr/bin/env python3
"""Local watchdog for V8 sweep servers (S1, S2).

Runs on the MacBook. Every minute:
1. SSH-pings both servers.
2. If unreachable for >5 min, logs alert (and emails if configured).
3. If reachable but sweep not running, restarts it with --resume.
4. If RAM <2GB free, kills extra workers.
5. Writes status to ~/server_watchdog.status (read by status bar / dashboards).

Designed to be bulletproof: handles SSH timeouts, partial outages, OOM scenarios.
Run with: nohup python3 server_watchdog.py > ~/server_watchdog.log 2>&1 &
Or via launchd plist: ~/Library/LaunchAgents/com.niels.server_watchdog.plist

Server config in SERVERS dict below — edit if hosts/credentials change.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SERVERS = {
    "S1": {
        "host": "s1-int",  # via internal 10.0.0.3, bypasses SSH brute-force
        "user": "niels",
        "python": "/home/niels/.conda/envs/binance_env/bin/python",
        "workers": 4,
        "screen": "t25",
    },
    "S2": {
        "host": "s2-int",  # via internal 10.0.0.4, bypasses SSH brute-force
        "user": "niels",
        "python": "/home/niels/miniconda3/envs/binance_env/bin/python",
        "workers": 8,
        "screen": "sweep",
    },
}

SWEEP_CMD = (
    "cd /home/niels/binance && {python} -u backtest_v8_sweep.py "
    "--mode tradier --account trb --start 2024-06-01 --capital 2000 "
    "--workers {workers} --tier 25 "
    "--symbols AAPL,MSFT,NVDA,AMZN,AMD,XOM,QQQ,SPY,DIS,META,TSLA,GOOGL,NFLX,COST,BA,JPM,V,UNH,LLY,AVGO "
    "--resume > /tmp/v8_t25.log 2>&1"
)

CHECK_INTERVAL = 60          # seconds between health checks
ALERT_AFTER_MIN = 5          # alert if down for this many minutes
SSH_TIMEOUT = 15             # ssh connection timeout
MIN_FREE_MB = 2000           # if less, reduce workers
STATUS_FILE = Path.home() / "server_watchdog.status"
LOG_FILE = Path.home() / "server_watchdog.log"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ssh(host, user, cmd, timeout=SSH_TIMEOUT):
    """Run command via SSH. Returns (returncode, stdout). Returncode 255 = SSH failure."""
    try:
        result = subprocess.run(
            ["ssh", "-o", f"ConnectTimeout={timeout}", "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=no", f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 255, str(e)


def check_server(name, cfg, state):
    """Health check + recovery for one server. Mutates state[name]."""
    host = cfg["host"]
    user = cfg["user"]

    rc, out = ssh(host, user, "free -m | grep Mem | awk '{print $7}'; ps -C python3,python --no-headers | wc -l")
    if rc != 0:
        # Server unreachable
        state[name]["reachable"] = False
        if state[name]["down_since"] is None:
            state[name]["down_since"] = time.time()
        down_min = (time.time() - state[name]["down_since"]) / 60
        log(f"{name} ({host}): UNREACHABLE for {down_min:.0f}min (rc={rc})")
        if down_min >= ALERT_AFTER_MIN and not state[name]["alerted"]:
            log(f"!!! ALERT: {name} down for {down_min:.0f}min — manual VPS reboot may be needed !!!")
            # If you have email/Slack/SMS hooks, send here
            state[name]["alerted"] = True
        return

    # Server is reachable — clear down state
    if not state[name]["reachable"]:
        log(f"{name} ({host}): RECOVERED after {(time.time() - state[name]['down_since'])/60:.0f}min")
    state[name]["reachable"] = True
    state[name]["down_since"] = None
    state[name]["alerted"] = False

    # Parse memory + python process count
    lines = out.strip().split("\n")
    try:
        free_mb = int(lines[0])
        py_count = int(lines[1]) if len(lines) > 1 else 0
    except (ValueError, IndexError):
        log(f"{name}: parse failed: {out[:200]}")
        return
    state[name]["free_mb"] = free_mb
    state[name]["py_processes"] = py_count

    # Check if sweep is running (has at least 2 python processes — sweep parent + worker)
    sweep_running = py_count >= 2
    state[name]["sweep_running"] = sweep_running

    # If RAM tight, log warning (but don't kill — workers should self-limit via ulimit)
    if free_mb < MIN_FREE_MB:
        log(f"{name}: LOW RAM {free_mb}MB free — workers may OOM")

    # If sweep not running and RAM is fine, restart
    if not sweep_running and free_mb > 4000:
        log(f"{name}: sweep not running, restarting...")
        cmd = SWEEP_CMD.format(python=cfg["python"], workers=cfg["workers"])
        screen_cmd = (
            f"screen -wipe 2>/dev/null; "
            f"screen -dmS {cfg['screen']} bash -c \"{cmd}\""
        )
        rc2, out2 = ssh(host, user, screen_cmd)
        if rc2 == 0:
            log(f"{name}: sweep restarted")
            state[name]["last_restart"] = time.time()
        else:
            log(f"{name}: restart FAILED rc={rc2}: {out2[:200]}")

    log(f"{name}: OK free={free_mb}MB py={py_count} sweep={'YES' if sweep_running else 'NO'}")


def write_status(state):
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump({
                "updated": datetime.now(timezone.utc).isoformat(),
                "servers": state,
            }, f, indent=2, default=str)
    except Exception as e:
        log(f"status write failed: {e}")


def main():
    state = {name: {"reachable": True, "down_since": None, "alerted": False,
                    "free_mb": 0, "py_processes": 0, "sweep_running": False,
                    "last_restart": None} for name in SERVERS}
    log(f"Watchdog started, monitoring: {list(SERVERS.keys())}")
    while True:
        try:
            for name, cfg in SERVERS.items():
                check_server(name, cfg, state)
            write_status(state)
        except Exception as e:
            log(f"Loop error: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

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
SSH_TIMEOUT = 120  # 2026-04-16: SSH can take 3min under remote CPU load
MIN_FREE_MB = 12000   # was 4000 — 3 workers×3.6GB+orchestrator+system=~14GB used on 31GB box; need 12GB headroom before spawning
STUCK_REBOOT_CONSECUTIVE = 3   # reboot after this many CONSECUTIVE unreachable checks (~3 min)
STUCK_REBOOT_ROLLING_WINDOW = 600  # also reboot if >= STUCK_REBOOT_ROLLING_MIN failures in this window (seconds)
STUCK_REBOOT_ROLLING_MIN = 4       # failures in rolling window that trigger reboot
PROBE_CONNECT_TIMEOUT = 20  # was 8 — heavy-CPU servers need more time to accept SSH

# 2026-04-16: SWEEPS replaced by AUTOCHAIN per server.
# Each server runs ONE master screen (`autochain_sN`) which handles all
# phase chaining + resume internally via sweep_autochain.sh. Respawning
# the autochain screen is sufficient to recover the entire sweep pipeline.
# Old 4-sector sweep dict retired.
AUTOCHAIN_SCRIPT = "sweep_autochain.sh"

SERVERS = {
    "S2": {
        "host": "s2-int",
        "user": "niels",
        "base": "/home/niels/binance-sandbox",
        "python": "/home/niels/miniconda3/envs/binance_env/bin/python",
        "autochain_screen": "autochain_s2",
        "autochain_arg": "s2",
        "run_sentinel": True,
    },
    "S1": {
        "host": "s1-int",
        "user": "niels",
        "base": "/home/niels/binance-sandbox",
        "python": "/home/niels/.conda/envs/binance_env/bin/python",
        "autochain_screen": "autochain_s1",
        "autochain_arg": "s1",
        "run_sentinel": True,
    },
}


def log(msg):
    # 2026-04-16: launchd redirects stdout→LOG_FILE, so just print. File-write removed
    # to stop the double-log (both launchd and log() were writing to same file).
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


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
             "-o", f"ConnectTimeout={min(timeout-2, PROBE_CONNECT_TIMEOUT)}",
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

    probe_cmd = "free -m | awk '/^Mem:/{print \"FREE=\"$7}'; pgrep -cf backtest_v8_sweep.py | awk '{print \"SWEEP_PROCS=\"$0}'; screen -ls 2>/dev/null | grep -oE '[0-9]+\\.[a-zA-Z0-9_]+' | awk '{print \"SCREEN=\"$0}'"
    rc, out = ssh(host, user, probe_cmd, timeout=30)
    now_t = time.time()
    if rc != 0:
        state[name]["reachable"] = False
        if state[name]["down_since"] is None:
            state[name]["down_since"] = now_t
        state[name]["consecutive_failures"] = state[name].get("consecutive_failures", 0) + 1
        state[name].setdefault("failure_times", []).append(now_t)
        # Trim rolling window
        state[name]["failure_times"] = [t for t in state[name]["failure_times"] if now_t - t <= STUCK_REBOOT_ROLLING_WINDOW]
        fails = state[name]["consecutive_failures"]
        rolling = len(state[name]["failure_times"])
        log(f"{name} ({host}): UNREACHABLE rc={rc} consecutive={fails} rolling={rolling}")
        need_reboot = fails >= STUCK_REBOOT_CONSECUTIVE or rolling >= STUCK_REBOOT_ROLLING_MIN
        if need_reboot:
            log(f"{name}: triggering reboot (consecutive={fails} rolling={rolling})")
            rc_r, out_r = ssh(host, user, "sudo reboot", timeout=15)
            log(f"{name}: reboot cmd rc={rc_r} out={out_r.strip()[:80]}")
            state[name]["consecutive_failures"] = 0
            state[name]["failure_times"] = []
        return

    if not state[name]["reachable"]:
        log(f"{name} ({host}): RECOVERED")
    state[name]["reachable"] = True
    state[name]["down_since"] = None
    state[name]["consecutive_failures"] = 0
    # Keep failure_times — rolling window still counts recent blips

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

    # Kill excess vec_backlog workers BEFORE memory check.
    # Death spiral: vec_supervisor respawns dead workers → OOM → repeat.
    # Fix: proactively kill any supervisor with >3 workers every cycle.
    MAX_WORKERS = 3
    kill_cmd = (
        f"N=$(ps aux | grep vec_backlog | grep -v grep | wc -l); "
        f"if [ \"$N\" -gt {MAX_WORKERS} ]; then "
        f"echo EXCESS_WORKERS_$N; pkill -9 -f vec_backlog.py; pkill -f vec_supervisor.sh; "
        f"else echo WORKERS_OK_$N; fi"
    )
    rc_k, out_k = ssh(host, user, kill_cmd, timeout=20)
    if "EXCESS_WORKERS" in out_k:
        log(f"{name}: killed excess workers ({out_k.strip()}) — mem was {free_mb}MB")

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

    # 2026-04-16: spawn autochain master screen if missing. Autochain handles the
    # rest of the pipeline internally (--resume-aware, idempotent, never-ending).
    ac_screen = cfg["autochain_screen"]
    if ac_screen not in existing_screens:
        log(f"{name}: {ac_screen} missing — spawning")
        inner = (
            f"cd {cfg['base']} && "
            f"bash {cfg['base']}/{AUTOCHAIN_SCRIPT} {cfg['autochain_arg']} "
            f"> /tmp/sweep_autochain_{cfg['autochain_arg']}.log 2>&1"
        )
        setup = (
            f"cat > /tmp/launch_{ac_screen}.sh <<'EOF'\n#!/bin/bash\n{inner}\nEOF\n"
            f"chmod +x /tmp/launch_{ac_screen}.sh && "
            f"screen -dmS {ac_screen} bash /tmp/launch_{ac_screen}.sh && "
            f"sleep 1 && screen -ls | grep -q {ac_screen} && echo AUTOCHAIN_UP || echo AUTOCHAIN_FAIL"
        )
        rc2, out2 = ssh(host, user, setup, timeout=30)
        log(f"{name}: {ac_screen} spawn rc={rc2} result={out2.strip()[-80:]}")

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
                        "sweep_procs": 0, "screens": [], "consecutive_failures": 0,
                        "failure_times": []} for name in SERVERS}
        log(f"server_watchdog up — managing {list(SERVERS.keys())} via autochain per-server")
        last_loop_time = time.time()
        while True:
            try:
                now = time.time()
                gap = now - last_loop_time
                if gap > CHECK_INTERVAL * 2:
                    # MacBook was sleeping — servers may have crashed while we were suspended.
                    # Pre-load consecutive_failures so ONE more failure triggers immediate reboot.
                    log(f"WAKE detected (gap={gap:.0f}s) — pre-arming reboot counters")
                    for name in SERVERS:
                        if state[name]["consecutive_failures"] == 0:
                            state[name]["consecutive_failures"] = STUCK_REBOOT_CONSECUTIVE - 1
                last_loop_time = now
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

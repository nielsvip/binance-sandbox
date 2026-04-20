#!/usr/bin/env python3
"""SSH tunnel keeper — maintains persistent ControlMaster connections.

Runs on MacBook as a LaunchAgent (auto-starts at boot, restarts if killed).
Every 30s, checks each SSH multiplex socket with `ssh -O check`. If down,
reconnects with `ssh -fN` (fork, no command, persistent).

Why a separate daemon (not the existing watchdog):
- Watchdog cares about sweep state — runs every 60s
- Tunnel keeper cares about raw connectivity — runs every 30s, faster recovery
- Separation of concerns: tunnels alive => watchdog can do its job

Hosts to keep alive (defined in ~/.ssh/config):
  gateway-internal -> 157.90.168.35 (the gateway, not under attack)
  s1-int           -> ProxyJump via gateway-internal -> 10.0.0.3
  s2-int           -> ProxyJump via gateway-internal -> 10.0.0.4

After MacBook sleep/wake, connections drop — the keeper reconnects within 30s.
"""
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HOSTS = ["gateway-internal", "s1-int", "s2-int", "s1-sftp", "s2-sftp"]
CHECK_INTERVAL = 30  # seconds
LOG_FILE = Path.home() / "ssh_tunnel_keeper.log"
STATUS_FILE = Path.home() / "ssh_tunnel_keeper.status"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_alive(host):
    """Returns True if multiplex socket exists and master is responding."""
    try:
        result = subprocess.run(
            ["ssh", "-O", "check", host],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def connect(host):
    """Establish a backgrounded persistent SSH master to host. Idempotent."""
    try:
        result = subprocess.run(
            ["ssh", "-fN", "-o", "ControlMaster=auto", "-o", "ControlPersist=yes",
             "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30",
             "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes",
             host],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0, (result.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def main():
    state = {h: {"alive": False, "last_reconnect": None, "consecutive_fails": 0} for h in HOSTS}
    log(f"Tunnel keeper started, hosts: {HOSTS}")
    while True:
        try:
            status = {}
            for host in HOSTS:
                alive = is_alive(host)
                if not alive:
                    state[host]["consecutive_fails"] += 1
                    log(f"{host}: DOWN (fail #{state[host]['consecutive_fails']}) — reconnecting")
                    ok, err = connect(host)
                    if ok:
                        state[host]["last_reconnect"] = time.time()
                        state[host]["alive"] = True
                        log(f"{host}: RECONNECTED")
                    else:
                        log(f"{host}: reconnect FAILED — {err[:200]}")
                        state[host]["alive"] = False
                else:
                    if not state[host]["alive"]:
                        log(f"{host}: UP")
                    state[host]["alive"] = True
                    state[host]["consecutive_fails"] = 0
                status[host] = state[host]
            # write status
            try:
                import json
                with open(STATUS_FILE, "w") as f:
                    json.dump({"updated": datetime.now(timezone.utc).isoformat(),
                               "hosts": status}, f, indent=2, default=str)
            except Exception:
                pass
        except Exception as e:
            log(f"Loop error: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

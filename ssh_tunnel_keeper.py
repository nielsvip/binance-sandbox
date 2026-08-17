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
import fcntl
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HOSTS = ["gateway-internal", "s1-int", "s1-sftp"]  # 2026-05-28 S2 DEAD permanently — s2-int/s2-sftp removed
CHECK_INTERVAL = 30  # seconds
LOG_FILE = Path.home() / "ssh_tunnel_keeper.log"
STATUS_FILE = Path.home() / "ssh_tunnel_keeper.status"
TRANSPORT_LOCK = Path("/Users/niels/Documents/binance/data/sync/.s1_transport.lock")


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
    """Return True only when the master and its required forward both work."""
    try:
        if host == "s1-sftp":
            target = ["-p", "2201", "niels@127.0.0.1"]
        else:
            target = [host]
        # A control socket being alive is not sufficient: ChatGPT sessions
        # need a real end-to-end command path.  Bypass multiplexing here so a
        # stale socket cannot produce a false healthy status.
        result = subprocess.run(
            ["ssh", "-4", "-i", str(Path.home() / ".ssh/id_ed25519"),
             "-o", "BatchMode=yes", "-o", "ControlMaster=no",
             "-o", "ControlPath=none", "-o", "ConnectTimeout=12",
             *target, "true"],
            capture_output=True, text=True, timeout=18,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def connect(host):
    """Establish a backgrounded persistent SSH master to host. Idempotent."""
    try:
        if host == "s1-sftp":
            subprocess.run(
                ["ssh", "-O", "cancel", "-L", "2201:127.0.0.1:22", "s1-int"],
                capture_output=True, text=True, timeout=10,
            )
            forwarded = subprocess.run(
                ["ssh", "-O", "forward", "-L", "2201:127.0.0.1:22", "s1-int"],
                capture_output=True, text=True, timeout=10,
            )
            if forwarded.returncode == 0:
                return True, "s1-int-control-master"
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


def disconnect(host):
    """Close a stale master so ControlMaster=auto cannot reuse a dead forward."""
    try:
        subprocess.run(
            ["ssh", "-O", "exit", host],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        pass


def main():
    singleton_path = Path("/tmp/ssh_tunnel_keeper.singleton")
    singleton_path.parent.mkdir(parents=True, exist_ok=True)
    singleton = open(singleton_path, "w")
    try:
        fcntl.flock(singleton.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another tunnel keeper is already running; exiting")
        return
    state = {h: {"alive": False, "last_reconnect": None, "consecutive_fails": 0} for h in HOSTS}
    log(f"Tunnel keeper started, hosts: {HOSTS}")
    while True:
        try:
            status = {}
            for host in HOSTS:
                if host == "s1-sftp" and TRANSPORT_LOCK.is_dir():
                    # A verified source/result transaction owns the tunnel and
                    # has its own bounded recycle/retry logic. Never tear its
                    # master down from the concurrent keeper health loop.
                    status[host] = state[host]
                    continue
                alive = is_alive(host)
                if not alive:
                    state[host]["consecutive_fails"] += 1
                    log(f"{host}: DOWN (fail #{state[host]['consecutive_fails']}) — reconnecting")
                    disconnect(host)
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

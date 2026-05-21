#!/usr/bin/env python3
"""Resilient SSH Tunnel Manager — uses SSH ControlMaster multiplexing.

Three tunnels to server share ONE SSH connection (ControlMaster).
Gateway gets its own connection. Both auto-reconnect with exponential backoff.

Redis tunnels use localhost:6381 (server) and localhost:6380 (gateway) to avoid
conflicting with local brew Redis on localhost:6379. MacBook's detect_environment()
in utils.py maps these ports; server/gateway configs still use 6379 on internal IPs.
"""
import subprocess
import time
import sys
import signal
import os
import socket

CONTROL_DIR = os.path.expanduser("~/.ssh/tunnel_controls")
LOG_DIR = os.path.join(os.path.expanduser("~"), "logs")

# Group tunnels by host — each host gets ONE multiplexed SSH connection
HOSTS = {
    # "server" host entry DISABLED 2026-05-21 — all three former forwards (Server Redis 6381, Analytics 5050, OpenClaw 18789) are now disabled. OpenClaw remote :18789 has no listener; tunnel spammed ssh_tunnels.log with 1837 "SSH process died" + 534 "Connection refused" events. Restore this block (with the desired forwards) when an S1 service needs a Mac-localhost forward again.
    # "server": {
    #     "host": "niels@157.180.125.52",
    #     "forwards": [
    #         {"name": "Server Redis", "local_port": 6381, "remote_port": 6379},  # DO NOT re-enable per CLAUDE.md (1.5s latency)
    #         {"name": "Analytics Dashboard", "local_port": 5050, "remote_port": 5050},
    #         {"name": "OpenClaw Dashboard", "local_port": 18789, "remote_port": 18789},
    #     ],
    # },
    "gateway": {
        "host": "niels@157.90.168.35",
        "forwards": [
            {"name": "Gateway Redis", "local_port": 6380, "remote_port": 6379},
        ],
    },
}

CHECK_INTERVAL = 30
MAX_BACKOFF = 300  # 5 min max between retries
processes = {}  # host_key -> subprocess
backoff = {}  # host_key -> seconds
running = True


def signal_handler(sig, frame):
    global running
    running = False
    print("\nShutting down tunnels...")
    for key in list(processes.keys()):
        _stop_host(key)
    # Clean up control sockets
    for f in os.listdir(CONTROL_DIR) if os.path.isdir(CONTROL_DIR) else []:
        try:
            os.unlink(os.path.join(CONTROL_DIR, f))
        except Exception:
            pass
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def is_port_open(port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("localhost", port))
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def _stop_host(host_key):
    proc = processes.get(host_key)
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            proc.wait()
    processes.pop(host_key, None)
    # Clean up control socket
    ctrl = os.path.join(CONTROL_DIR, f"{host_key}.sock")
    if os.path.exists(ctrl):
        try:
            subprocess.run(["ssh", "-S", ctrl, "-O", "exit", "dummy"], timeout=5, capture_output=True)
        except Exception:
            pass
        try:
            os.unlink(ctrl)
        except Exception:
            pass


def _kill_stale_port_holders(host_key):
    """Kill only SSH processes holding our ports (not other processes)."""
    cfg = HOSTS[host_key]
    my_pid = os.getpid()
    my_children = {p.pid for p in processes.values() if p and p.poll() is None}
    for fwd in cfg["forwards"]:
        port = fwd["local_port"]
        try:
            result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=5)
            if result.stdout.strip():
                for pid_str in result.stdout.strip().split("\n"):
                    pid = int(pid_str.strip())
                    if pid == my_pid or pid in my_children:
                        continue
                    # Only kill if it's an ssh process
                    ps_result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=3)
                    if "ssh" in ps_result.stdout.lower():
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(0.5)
        except Exception:
            pass


def start_host(host_key):
    cfg = HOSTS[host_key]
    host = cfg["host"]
    ctrl = os.path.join(CONTROL_DIR, f"{host_key}.sock")
    _stop_host(host_key)
    _kill_stale_port_holders(host_key)
    time.sleep(1)
    # Build forward args: -L local:localhost:remote for each tunnel
    forward_args = []
    for fwd in cfg["forwards"]:
        forward_args.extend(["-L", f"localhost:{fwd['local_port']}:localhost:{fwd['remote_port']}"])
    cmd = ["ssh", "-N"] + forward_args + [
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "TCPKeepAlive=yes",
        "-o", "ConnectTimeout=15",
        "-o", "ConnectionAttempts=2",
        "-o", f"ControlPath={ctrl}",
        host,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    processes[host_key] = proc
    fwd_names = ", ".join(f['name'] for f in cfg['forwards'])
    fwd_ports = ", ".join(str(f['local_port']) for f in cfg['forwards'])
    print(f"  [{host_key}] Started SSH to {host} (pid {proc.pid}) — ports: {fwd_ports} ({fwd_names})")
    return proc


def check_host_health(host_key):
    """Returns True if all forwards for this host are working."""
    cfg = HOSTS[host_key]
    proc = processes.get(host_key)
    if proc is None or proc.poll() is not None:
        exit_code = proc.returncode if proc else "N/A"
        stderr_out = ""
        if proc and proc.stderr:
            try:
                stderr_out = proc.stderr.read().decode("utf-8", errors="replace").strip()[-200:]
            except Exception:
                pass
        print(f"  [{host_key}] SSH process died (exit={exit_code}){f': {stderr_out}' if stderr_out else ''}")
        return False
    # Check each forwarded port
    for fwd in cfg["forwards"]:
        if not is_port_open(fwd["local_port"]):
            print(f"  [{host_key}] Port {fwd['local_port']} ({fwd['name']}) unreachable despite SSH alive")
            return False
    return True


def get_backoff(host_key):
    """Exponential backoff: 5, 10, 20, 40, 80, 160, 300 (capped)."""
    current = backoff.get(host_key, 0)
    if current == 0:
        backoff[host_key] = 5
        return 0  # First attempt: no wait
    next_val = min(current * 2, MAX_BACKOFF)
    backoff[host_key] = next_val
    return current


def reset_backoff(host_key):
    backoff[host_key] = 0


def main():
    os.makedirs(CONTROL_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    print("SSH Tunnel Manager — multiplexed connections with exponential backoff")
    print(f"Hosts: {len(HOSTS)} | Total forwards: {sum(len(h['forwards']) for h in HOSTS.values())}")
    print(f"Check interval: {CHECK_INTERVAL}s | Max backoff: {MAX_BACKOFF}s")
    print()
    # Initial start
    for host_key in HOSTS:
        start_host(host_key)
    time.sleep(5)
    # Verify initial connectivity
    for host_key in HOSTS:
        if check_host_health(host_key):
            fwd_ports = ", ".join(str(f['local_port']) for f in HOSTS[host_key]['forwards'])
            print(f"  [{host_key}] All ports verified OK: {fwd_ports}")
            reset_backoff(host_key)
        else:
            print(f"  [{host_key}] Initial connection failed — will retry with backoff")
    print()
    print("Monitoring tunnels... (Ctrl+C to stop)")
    print()
    _last_restart = {}  # host_key -> timestamp
    _healthy_since = {}  # host_key -> timestamp (for stable connection logging)
    while running:
        for host_key in HOSTS:
            if not check_host_health(host_key):
                # Check backoff
                wait = get_backoff(host_key)
                last = _last_restart.get(host_key, 0)
                elapsed = time.time() - last
                if elapsed < wait:
                    remaining = int(wait - elapsed)
                    if remaining % 60 == 0 and remaining > 0:  # Log every 60s during long waits
                        print(f"  [{host_key}] Backoff: {remaining}s remaining before retry")
                    continue
                print(f"  [{host_key}] Reconnecting... (backoff: {backoff.get(host_key, 5)}s)")
                start_host(host_key)
                _last_restart[host_key] = time.time()
                _healthy_since.pop(host_key, None)
            else:
                # Connection healthy — reset backoff after 60s of stability
                if host_key not in _healthy_since:
                    _healthy_since[host_key] = time.time()
                elif time.time() - _healthy_since[host_key] > 60:
                    if backoff.get(host_key, 0) > 0:
                        print(f"  [{host_key}] Stable for 60s — backoff reset")
                    reset_backoff(host_key)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

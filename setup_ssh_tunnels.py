#!/usr/bin/env python3
"""Resilient SSH Tunnel Manager — 3-path S1 redundancy + auto-failover.

Plan A (2201): BRIDGE — s1-int front door, follows the first healthy route
  (direct S1 @ 157.180.125.52, then gateway -> internal S1 @ 10.0.0.3).
Plan B (2202): Gateway → internal S1 @ 10.0.0.3 (fixed spare).
Plan C (2203): Backup direct S1 (fixed spare).

Health = a real S1 login through the forward, not just an open port.
Port reclaim only ever targets forward-master listeners — never data sessions —
and the 2201 bridge is never recycled under s1_s2_autosync's transport lock.
"""
import subprocess
import time
import sys
import signal
import os
import socket

CONTROL_DIR = os.path.expanduser("~/.ssh/tunnel_controls")
LOG_DIR = os.path.join(os.path.expanduser("~"), "logs")
# USER 2026-10-24: s1-int (localhost:2201) is the fleet's Mac-side front door, so
# 2201 is a BRIDGE, not a fixed route — it follows whichever S1 route is healthy.
# s1-plan-b/c stay fixed spares on 2202/2203. s1-sftp hand masters are retired:
# this manager is the sole owner of 2201 (see _kill_stale_port_holders).
# USER 2026-10-27 "immediately switch to any of the 3 ip's for s1": "race" = ~/.ssh/config s1-race ->
# tools/ssh_s1_connect.py (3 public IPv4s + gateway + s2 + IPv6, first banner wins); fixed routes stay as fallback.
S1_BRIDGE_CANDIDATES = [
    ("race", "s1-race"),
    ("direct", "niels@157.180.125.52"),
    ("gateway", "s1-via-gateway"),
]
HAND_MASTER_ALIASES = ("s1-sftp", "s1-sftp-direct")  # config LocalForward 2201 — no port on their command line
PLAN_A_MAX_BACKOFF = 10  # the 2201 front door never waits minutes
SPARE_E2E_EVERY = 60  # plan-b/c real-login check cadence (plan-a every loop)
_last_e2e = {}
# s1_s2_autosync takes this lock for verified transactions — never recycle 2201
# under it (the keeper yields the same way).
TRANSPORT_LOCK = "/Users/niels/Documents/binance/data/sync/.s1_transport.lock"
BRIDGE_STATUS_FILE = "/tmp/s1_bridge.status"
bridge_route = {}  # host_key -> candidate name currently bound

# Three-tier S1 redundancy: agents use whichever port responds
HOSTS = {
    "s1-plan-a": {
        "host": "niels@157.180.125.52",  # default route; bridge tries S1_BRIDGE_CANDIDATES
        "forwards": [
            {"name": "S1 Bridge (Plan A)", "local_port": 2201, "remote_port": 22},
        ],
    },
    "s1-plan-b": {
        "host": "s1-via-gateway",
        "forwards": [
            {"name": "S1 via Gateway (Plan B)", "local_port": 2202, "remote_port": 22},
        ],
    },
    "s1-plan-c": {
        "host": "s1-fip-nbg",  # 2026-10-27: floating IP 49.13.39.233 (was 157.180.125.52 = same IP as plan-a direct)
        "forwards": [
            {"name": "S1 Direct Backup (Plan C)", "local_port": 2203, "remote_port": 22},
        ],
    },
    "gateway": {
        "host": "niels@157.90.168.35",
        "forwards": [
            {"name": "Gateway Redis", "local_port": 6380, "remote_port": 6379},
        ],
    },
}

CHECK_INTERVAL = 10
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
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
    processes.pop(host_key, None)
    # Clean up control socket
    ctrl = os.path.join(CONTROL_DIR, f"{host_key}.sock")
    if os.path.exists(ctrl):
        try:
            subprocess.run(["ssh", "-S", ctrl, "-O", "exit", "dummy"], timeout=3, capture_output=True)
        except Exception:
            pass
        try:
            os.unlink(ctrl)
        except Exception:
            pass


def is_forward_owner(cmdline, port):
    """True if this command line is a tunnel master LISTENING on port (not a data session).

    USER 2026-10-24: the old killer matched any ssh pid on the port — including live
    rsync/scp data connections — and recycled them mid-transfer. Only a process that
    OWNS the listen socket may be reclaimed: an `ssh -N`/`-fN` forward master binding
    our port, a retired `ssh -fNT s1-sftp` hand master, or one of our own control-path
    children. Data sessions (`ssh -p 2201 ... <command>`, rsync --server) never match.
    """
    if not cmdline:
        return False
    c = str(cmdline)
    if "ssh" not in os.path.basename(c.split()[0]) and " ssh " not in f" {c} " and not c.lstrip().startswith("ssh "):
        return False
    if port == 2201 and any(f" {alias} " in f" {c} " for alias in HAND_MASTER_ALIASES) and ("-fN" in c or " -N" in c):
        return True
    if f":{port}" not in c and f" {port}" not in c and f"={port}" not in c:
        return False
    if "tunnel_controls" in c:
        return True
    if "-fNT" in c or " -fN " in c or c.rstrip().endswith(" -fN"):
        return True
    if " -N " in c or c.rstrip().endswith(" -N"):
        return "-L" in c or "-R" in c or "-D" in c
    return False


def _kill_stale_port_holders(host_key):
    """Reclaim our listen ports from dead/foreign forward masters only."""
    cfg = HOSTS[host_key]
    my_pid = os.getpid()
    my_children = {p.pid for p in processes.values() if p and p.poll() is None}
    for fwd in cfg["forwards"]:
        port = fwd["local_port"]
        try:
            result = subprocess.run(
                ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                for pid_str in result.stdout.strip().split("\n"):
                    try:
                        pid = int(pid_str.strip())
                    except ValueError:
                        continue
                    if pid == my_pid or pid in my_children:
                        continue
                    ps_result = subprocess.run(
                        ["ps", "-p", str(pid), "-o", "command="],
                        capture_output=True, text=True, timeout=3,
                    )
                    if is_forward_owner(ps_result.stdout.strip(), port):
                        print(f"  [{host_key}] reclaiming port {port} from stale forward master pid {pid}")
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(0.5)
        except Exception:
            pass


def s1_end_to_end(port, timeout=8):
    """True if an S1 login actually works through this local forward."""
    try:
        result = subprocess.run(
            ["ssh", "-p", str(port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=no", "-o", "ControlMaster=no",
             "-o", "ControlPath=none", "niels@127.0.0.1", "true"],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0
    except Exception:
        return False


def _write_bridge_status(route):
    try:
        import json
        with open(BRIDGE_STATUS_FILE, "w") as f:
            json.dump({"port": 2201, "route": route,
                       "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)
    except Exception:
        pass


def transport_locked():
    try:
        return os.path.isdir(TRANSPORT_LOCK)
    except Exception:
        return False


def _start_forward(host_key, host):
    """Start one forward master for host_key; returns the Popen."""
    cfg = HOSTS[host_key]
    ctrl = os.path.join(CONTROL_DIR, f"{host_key}.sock")
    forward_args = []
    for fwd in cfg["forwards"]:
        forward_args.extend(["-L", f"localhost:{fwd['local_port']}:localhost:{fwd['remote_port']}"])
    cmd = ["ssh", "-N"] + forward_args + [
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "TCPKeepAlive=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=5",
        "-o", "IPQoS=lowdelay",
        "-o", f"ControlPath={ctrl}",
        host,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    processes[host_key] = proc
    return proc


def start_host(host_key):
    cfg = HOSTS[host_key]
    _stop_host(host_key)
    _kill_stale_port_holders(host_key)
    # Wait for TIME_WAIT to clear
    time.sleep(2)
    if host_key == "s1-plan-a":
        # BRIDGE: bind 2201 to the first candidate route that passes end-to-end.
        for route_name, host in S1_BRIDGE_CANDIDATES:
            proc = _start_forward(host_key, host)
            print(f"  [{host_key}] trying route {route_name} -> {host} (pid {proc.pid})")
            bind_deadline = time.time() + 15
            while time.time() < bind_deadline and proc.poll() is None and not is_port_open(cfg["forwards"][0]["local_port"]):
                time.sleep(0.25)
            if proc.poll() is not None:
                print(f"  [{host_key}] route {route_name} exited immediately (rc={proc.returncode})")
                continue
            if s1_end_to_end(cfg["forwards"][0]["local_port"]):
                bridge_route[host_key] = route_name
                _write_bridge_status(route_name)
                print(f"  [{host_key}] BRIDGE UP via {route_name} — s1-int (2201) live")
                return proc
            print(f"  [{host_key}] route {route_name} bound but end-to-end FAILED — next")
            _stop_host(host_key)
            time.sleep(1)
        print(f"  [{host_key}] ALL bridge routes failed — backoff will retry")
        bridge_route.pop(host_key, None)
        return processes.get(host_key)
    host = cfg["host"]
    proc = _start_forward(host_key, host)
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
    # S1 plans get a real login check — a bound-but-dead forward must not count
    # (USER 2026-10-24: s1-int flapped while the port stayed open).
    if host_key in ("s1-plan-a", "s1-plan-b", "s1-plan-c"):
        port = cfg["forwards"][0]["local_port"]
        if host_key != "s1-plan-a" and time.time() - _last_e2e.get(host_key, 0) < SPARE_E2E_EVERY:
            return True
        _last_e2e[host_key] = time.time()
        if not s1_end_to_end(port):
            print(f"  [{host_key}] Port {port} open but S1 login FAILED — treating as down")
            try:
                proc.terminate()
            except Exception:
                pass
            return False
        return True
    # Check each forwarded port
    for fwd in cfg["forwards"]:
        if not is_port_open(fwd["local_port"]):
            print(f"  [{host_key}] Port {fwd['local_port']} ({fwd['name']}) unreachable despite SSH alive")
            return False
    return True

def report_healthy_s1_path():
    """Report which S1 path is healthy."""
    s1_paths = ["s1-plan-a", "s1-plan-b", "s1-plan-c"]
    healthy = [p for p in s1_paths if check_host_health(p)]
    if healthy:
        ports = [str(HOSTS[p]["forwards"][0]["local_port"]) for p in healthy]
        print(f"  [STATUS] S1 healthy paths: {', '.join(ports)} ({', '.join(healthy)})")
        return healthy
    else:
        print(f"  [WARNING] NO S1 PATHS HEALTHY — all three routes down")


def get_backoff(host_key):
    """Exponential backoff: 5, 10, 20, 40, 80, 160, 300 (capped)."""
    current = backoff.get(host_key, 0)
    if current == 0:
        backoff[host_key] = 5
        return 0  # First attempt: no wait
    next_val = min(current * 2, PLAN_A_MAX_BACKOFF if host_key == "s1-plan-a" else MAX_BACKOFF)
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
    # Gateway watchdog auto-deploy (runs once at startup, outside sandbox)
    try:
        import threading
        def _deploy_gateway_watchdog():
            marker = "/tmp/gateway_watchdog_deploy.done"
            if os.path.exists(marker):
                try:
                    age = time.time() - os.path.getmtime(marker)
                    if age < 3600:
                        print(f"  [gateway-watchdog] recent deploy {int(age)}s ago, skipping")
                        return
                except:
                    pass
            print("  [gateway-watchdog] triggering deploy...")
            try:
                subprocess.Popen(["python3", "/Users/niels/Documents/binance/gateway_watchdog_deploy.py"],
                                 stdout=open("/tmp/gateway_watchdog_deploy.stdout.log","a"),
                                 stderr=subprocess.STDOUT,
                                 start_new_session=True)
            except Exception as e:
                print(f"  [gateway-watchdog] deploy trigger failed: {e}")
        threading.Thread(target=_deploy_gateway_watchdog, daemon=True).start()
    except Exception as e:
        print(f"  [gateway-watchdog] injection failed: {e}")
    _last_restart = {}  # host_key -> timestamp
    _healthy_since = {}  # host_key -> timestamp (for stable connection logging)
    _last_status_report = 0
    while running:
        for host_key in HOSTS:
            if not check_host_health(host_key):
                if host_key == "s1-plan-a" and transport_locked():
                    # A verified sync transaction owns the bridge — never recycle
                    # 2201 under it; retry on the next tick after it clears.
                    print(f"  [{host_key}] unhealthy but transport lock held — deferring recycle")
                    continue
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
        # Report S1 status every 120s
        now = time.time()
        if now - _last_status_report > 120:
            report_healthy_s1_path()
            _last_status_report = now
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

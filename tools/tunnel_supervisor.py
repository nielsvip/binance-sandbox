#!/usr/bin/env python3
"""Keep one localhost SSH forward per server alive: 2201+2203 S1, 2202 gateway, 2204 S2, 2205 S5, 2206 S6.

Each port walks its candidate routes in order (direct public, fallback IP, gateway ProxyJump). A route is only
considered up when a probe THROUGH the port returns the expected hostname, so a forward that lands on the wrong box
or a dead route never counts. Two failed probes move on to the next route. Runs under launchd (KeepAlive).
"""
import socket
import subprocess
import sys
import threading
import time

# Route forms: "alias" (direct ssh alias) or "J:<jump>|<target>" (plan B: reach a private 10.0.0.x through a
# live server that is interconnected with it). Each port is verified by the hostname it returns.
PRIVATE_JUMP = "gateway-internal"
PORTS = {
    2201: {"name": "s1", "expect": "niels", "routes": [f"J:{PRIVATE_JUMP}|10.0.0.3", "s1-pub", "s1-fip-nbg", "s1-fip-fsn"]},
    2202: {"name": "gateway", "expect": "gateway", "routes": ["gateway-internal", "gateway-redis"]},
    2203: {"name": "s1", "expect": "niels", "routes": [f"J:{PRIVATE_JUMP}|10.0.0.3", "s1-fip-nbg", "s1-pub"]},
    2204: {"name": "s2", "expect": "s2", "routes": [f"J:{PRIVATE_JUMP}|10.0.0.4", "s2", "s2-pub"]},
    2205: {"name": "s5", "expect": "s5", "routes": [f"J:{PRIVATE_JUMP}|10.0.0.5", "s5", "s5-pub"]},
    2206: {"name": "s6", "expect": "s6", "routes": [f"J:{PRIVATE_JUMP}|10.0.0.6", "s6", "s6-pub"]},
}
COMMON = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ControlMaster=no", "-o", "ControlPath=none",
          "-o", "StrictHostKeyChecking=accept-new"]
PROBE_EVERY = 20
PROBE_FAILS = 2
LOG = "/Users/niels/Documents/binance/logs/tunnel_supervisor.log"


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def probe(port, expect):
    try:
        out = subprocess.run(["ssh", *COMMON, "-o", "ServerAliveInterval=5", "-p", str(port), "niels@127.0.0.1", "hostname"],
                             capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    got = out.stdout.strip()
    return got == expect, got or out.stderr.strip()[-120:]


def wait_bound(port, proc, limit=60):
    end = time.time() + limit
    while time.time() < end and proc.poll() is None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError:
            time.sleep(1)
    return False


def run_port(port, spec):
    idx = 0
    while True:
        route = spec["routes"][idx % len(spec["routes"])]
        if route.startswith("J:"):
            jump, target = route[2:].split("|", 1)
            dest = ["-J", jump, f"niels@{target}"]
        else:
            dest = [route]
        cmd = ["ssh", "-N", *COMMON, "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=10",
               "-o", "ServerAliveCountMax=2", "-L", f"127.0.0.1:{port}:127.0.0.1:22", *dest]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"{spec['name']} port {port}: route {route} started (pid {proc.pid})")
        if not wait_bound(port, proc):
            log(f"{spec['name']} port {port}: route {route} did not bind within 60s")
        fails = 0
        while proc.poll() is None:
            time.sleep(PROBE_EVERY)
            ok, got = probe(port, spec["expect"])
            if ok:
                fails = 0
                continue
            fails += 1
            log(f"{spec['name']} port {port}: probe via {route} failed ({fails}/{PROBE_FAILS}): {got}")
            if fails >= PROBE_FAILS:
                break
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        log(f"{spec['name']} port {port}: route {route} down, moving to next route")
        idx += 1
        time.sleep(5)


def main():
    threads = []
    for port, spec in PORTS.items():
        t = threading.Thread(target=run_port, args=(port, spec), daemon=True)
        t.start()
        threads.append(t)
    log("tunnel supervisor started: " + ", ".join(f"{p}={s['name']}" for p, s in PORTS.items()))
    while True:
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())

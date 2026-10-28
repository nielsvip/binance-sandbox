#!/usr/bin/env python3
"""Keep one localhost SSH forward per server alive: 2201 S1, 2202 S2, 2205 S5, 2206 S6.

Each port walks its candidate routes in order (direct public, fallback IP, gateway ProxyJump). A route is only
considered up when a probe THROUGH the port returns the expected hostname, so a forward that lands on the wrong box
or a dead route never counts. Two failed probes move on to the next route. Runs under launchd (KeepAlive).
"""
import subprocess
import sys
import threading
import time

# Route forms: "alias" (direct ssh alias) or "J:<jump>|<target>" (plan B: reach a private 10.0.0.x through a
# live server that is interconnected with it). Each port is verified by the hostname it returns.
PLAN_B_JUMPS = ["gateway-internal", "s6-pub", "s5-pub", "s1-pub"]
PORTS = {
    2201: {"name": "s1", "expect": "niels", "routes": ["s1-pub", "s1-fip-nbg", "s1-fip-fsn"]
           + [f"J:{j}|10.0.0.3" for j in PLAN_B_JUMPS]},
    2202: {"name": "gateway", "expect": "gateway", "routes": ["gateway-internal", "gateway-redis"]
           + [f"J:{j}|10.0.0.2" for j in PLAN_B_JUMPS if j != "gateway-internal"]},
    2204: {"name": "s2", "expect": "s2", "routes": ["s2", "s2-int"]
           + [f"J:{j}|10.0.0.4" for j in PLAN_B_JUMPS]},
    2205: {"name": "s5", "expect": "s5", "routes": ["s5-pub", "s5"]
           + [f"J:{j}|10.0.0.5" for j in PLAN_B_JUMPS if j != "s5-pub"]},
    2206: {"name": "s6", "expect": "s6", "routes": ["s6-pub", "s6"]
           + [f"J:{j}|10.0.0.6" for j in PLAN_B_JUMPS if j != "s6-pub"]},
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
        time.sleep(8)
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

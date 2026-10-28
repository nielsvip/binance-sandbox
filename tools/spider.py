#!/usr/bin/env python3
"""Spider web transport: the Mac races every route to a server at once and uses the fastest live one.

Routes per server: direct aliases, public IPs, and plan-B jumps through another live server to the private
10.0.0.x address. Every probe runs concurrently; the first route whose probe returns the expected hostname wins
(latency measured). Winners are cached for CACHE_S seconds and re-raced on failure. Big rsyncs can stripe across
the N fastest routes at once. Host identity is always verified, so a route can never land on the wrong box.

CLI:
  spider.py status                       race every host, print winner and latency
  spider.py ssh <host> <command...>      run a command on the fastest live route
  spider.py rsync <host> <src> <dst>     rsync to <host>:<dst> over the fastest route
  spider.py rsync-stripe <host> <src_dir> <dst_dir> [--ways N]   split files across N routes in parallel
"""
import argparse
import concurrent.futures as cf
import json
import os
import shlex
import subprocess
import sys
import time

USER = "niels"
JUMPS = ["gateway-internal", "s6-pub", "s5-pub", "s1-pub"]
HOSTS = {
    "s1": {"expect": "niels", "ip": "10.0.0.3",
           "direct": ["s1-pub", "s1-fip-nbg", "s1-fip-fsn"]},
    "s2": {"expect": "s2", "ip": "10.0.0.4", "direct": ["s2", "s2-int"]},
    "s5": {"expect": "s5", "ip": "10.0.0.5", "direct": ["s5-pub", "s5"]},
    "s6": {"expect": "s6", "ip": "10.0.0.6", "direct": ["s6-pub", "s6"]},
    "gateway": {"expect": "gateway", "ip": "10.0.0.2", "direct": ["gateway-internal", "gateway-redis"]},
}
CACHE_S = 60
PROBE_TIMEOUT = 8
_cache = {}
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"ConnectTimeout={PROBE_TIMEOUT}"]


def routes(host):
    spec = HOSTS[host]
    out = [{"kind": "direct", "via": alias, "target": alias, "jump": None} for alias in spec["direct"]]
    for j in JUMPS:
        if j in spec["direct"]:
            continue
        out.append({"kind": "jump", "via": j, "target": f"{USER}@{spec['ip']}", "jump": j})
    return out


def ssh_argv(route, remote_cmd=None):
    argv = ["ssh", *SSH_OPTS]
    if route["jump"]:
        argv += ["-J", route["jump"]]
    argv.append(route["target"])
    if remote_cmd is not None:
        argv.append(remote_cmd)
    return argv


def ssh_e_string(route):
    parts = ["ssh", *SSH_OPTS]
    if route["jump"]:
        parts += ["-J", route["jump"]]
    return " ".join(shlex.quote(p) for p in parts)


def probe(host, route):
    t0 = time.time()
    try:
        out = subprocess.run(ssh_argv(route, "hostname"), capture_output=True, text=True, timeout=PROBE_TIMEOUT + 4)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0 or out.stdout.strip() != HOSTS[host]["expect"]:
        return None
    return {"route": route, "latency": round(time.time() - t0, 3)}


def race(host, want=1, force=False):
    """Probe every route concurrently; return the `want` fastest live routes."""
    now = time.time()
    hit = _cache.get(host)
    if hit and not force and now - hit["at"] < CACHE_S and len(hit["live"]) >= want:
        return hit["live"][:want]
    rs = routes(host)
    live = []
    with cf.ThreadPoolExecutor(max_workers=len(rs)) as ex:
        for res in ex.map(lambda r: probe(host, r), rs):
            if res:
                live.append(res)
    live.sort(key=lambda x: x["latency"])
    _cache[host] = {"at": time.time(), "live": live}
    if not live:
        raise RuntimeError(f"{host}: no live route (tried {len(rs)})")
    return live[:want]


def run_ssh(host, command):
    last = None
    for attempt in range(2):
        best = race(host, force=attempt > 0)[0]["route"]
        out = subprocess.run(ssh_argv(best, command), capture_output=True, text=True)
        if out.returncode == 0:
            return out.stdout
        last = out.stderr.strip()
        _cache.pop(host, None)
    raise RuntimeError(f"{host}: command failed on every route: {last}")


def rsync_once(route, src, dst_path, extra=()):
    target = route["target"] if route["jump"] else route["via"]
    cmd = ["rsync", "-az", "--timeout=30", "-e", ssh_e_string(route), *extra, src, f"{target}:{dst_path}"]
    return subprocess.run(cmd, capture_output=True, text=True)


def run_rsync(host, src, dst_path):
    last = None
    for attempt in range(2):
        winners = race(host, want=3, force=attempt > 0)
        for w in winners:
            out = rsync_once(w["route"], src, dst_path)
            if out.returncode == 0:
                return {"via": w["route"]["via"], "latency": w["latency"]}
            last = out.stderr.strip()[-200:]
        _cache.pop(host, None)
    raise RuntimeError(f"{host}: rsync failed on every live route: {last}")


def run_rsync_stripe(host, src_dir, dst_dir, ways):
    src_dir = os.path.abspath(src_dir)
    files = []
    for root, _dirs, names in os.walk(src_dir):
        for n in names:
            files.append(os.path.relpath(os.path.join(root, n), src_dir))
    files.sort()
    routes_w = race(host, want=ways)
    chunks = [files[i::len(routes_w)] for i in range(len(routes_w))]

    def push(i):
        w = routes_w[i]
        lst = f"/tmp/spider_list_{os.getpid()}_{i}.txt"
        with open(lst, "w") as fh:
            fh.write("\n".join(chunks[i]) + "\n")
        out = subprocess.run(["rsync", "-az", "--timeout=60", "-e", ssh_e_string(w["route"]),
                              f"--files-from={lst}", f"{src_dir}/",
                              f"{w['route']['target'] if w['route']['jump'] else w['route']['via']}:{dst_dir}/"],
                             capture_output=True, text=True)
        os.remove(lst)
        return {"via": w["route"]["via"], "files": len(chunks[i]), "rc": out.returncode,
                "err": out.stderr.strip()[-160:]}

    with cf.ThreadPoolExecutor(max_workers=len(routes_w)) as ex:
        results = list(ex.map(push, range(len(routes_w))))
    if any(r["rc"] for r in results):
        raise RuntimeError(json.dumps(results))
    return results


def cmd_status():
    rows = {}
    for h in HOSTS:
        try:
            w = race(h, want=3, force=True)
            rows[h] = [{"via": x["route"]["via"], "kind": x["route"]["kind"], "latency": x["latency"]} for x in w]
        except RuntimeError as exc:
            rows[h] = {"error": str(exc)}
    print(json.dumps(rows, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("ssh")
    s.add_argument("host")
    s.add_argument("command", nargs=argparse.REMAINDER)
    r = sub.add_parser("rsync")
    r.add_argument("host")
    r.add_argument("src")
    r.add_argument("dst")
    st = sub.add_parser("rsync-stripe")
    st.add_argument("host")
    st.add_argument("src_dir")
    st.add_argument("dst_dir")
    st.add_argument("--ways", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "status":
        return cmd_status()
    if a.cmd == "ssh":
        sys.stdout.write(run_ssh(a.host, " ".join(a.command)))
        return 0
    if a.cmd == "rsync":
        print(json.dumps(run_rsync(a.host, a.src, a.dst)))
        return 0
    if a.cmd == "rsync-stripe":
        print(json.dumps(run_rsync_stripe(a.host, a.src_dir, a.dst_dir, a.ways)))
        return 0


if __name__ == "__main__":
    sys.exit(main())

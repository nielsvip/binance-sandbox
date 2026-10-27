#!/usr/bin/python3
"""Every-minute S1 path monitor + stale ssh master reaper (launchd com.niels.s1-path-monitor).

Measures every S1 path the racer (tools/ssh_s1_connect.py) can use — SSH banner latency per
path, a full login through the race alias, and a login through each bridge port 2201/2202/2203 —
and appends one line per run to ~/logs/s1_paths.log (status JSON: ~/logs/s1_paths.status.json),
so the next drop shows exactly which path failed and when.
Also: (1) sets ~/.ssh/s1_path.last to a healthy path when the remembered one fails, so new
connections skip a dead path without waiting a stagger; (2) checks every fleet ControlMaster
socket with a real command and kills a master that is hung 3 runs in a row (stale masters hang
clients instead of failing).
"""
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ssh_s1_connect as R  # noqa: E402

HOME = os.path.expanduser("~")
LOG = os.path.join(HOME, "logs", "s1_paths.log")
STATUS = os.path.join(HOME, "logs", "s1_paths.status.json")
STATE = os.path.join(HOME, "logs", ".s1_paths.state.json")
MASTER_GLOBS = re.compile(r"^cm-(s1f-.+|s1-sftp-f|s1-pub|s1-int|s2|s5|s6|niels@10\.0\.0\.\d+:22)$")
ROTATE_BYTES = 20 * 1024 * 1024


def rotate(path):
    try:
        if os.path.getsize(path) > ROTATE_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


def probe_path(path):
    winner, notes, took = R.race([path])
    if winner is None:
        return {"ok": False, "s": round(took, 2), "why": " ".join(notes)[:80]}
    R.stop(winner)
    return {"ok": True, "s": round(took, 2)}


def login(args, timeout=20):
    t0 = time.monotonic()
    try:
        res = subprocess.run(["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none", *args, "true"],
                             capture_output=True, text=True, timeout=timeout)
        ok = res.returncode == 0
        why = "" if ok else (res.stderr.strip().splitlines() or ["rc=%d" % res.returncode])[-1][:80]
    except subprocess.TimeoutExpired:
        ok, why = False, "timeout"
    return {"ok": ok, "s": round(time.monotonic() - t0, 2), **({} if ok else {"why": why})}


def master_pid(sock):
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if f"ssh: {sock} [mux]" in line:
            return int(line.split()[0])
    return None


def check_master(sock):
    try:
        chk = subprocess.run(["/usr/bin/ssh", "-S", sock, "-O", "check", "mux"], capture_output=True, text=True, timeout=4)
        if chk.returncode != 0:
            return "gone"
    except subprocess.TimeoutExpired:
        return "hung"
    try:
        res = subprocess.run(["/usr/bin/ssh", "-S", sock, "-o", "ControlMaster=no", "-o", "BatchMode=yes", "mux", "true"],
                             capture_output=True, text=True, timeout=12)
        return "ok" if res.returncode == 0 else "fail"
    except subprocess.TimeoutExpired:
        return "hung"


def reap_masters(state):
    sshdir = os.path.join(HOME, ".ssh")
    socks = [os.path.join(sshdir, n) for n in sorted(os.listdir(sshdir)) if MASTER_GLOBS.match(n)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        verdicts = dict(zip(socks, pool.map(check_master, socks)))
    bad = state.setdefault("bad_masters", {})
    killed = []
    for sock, verdict in verdicts.items():
        if verdict in ("hung", "fail"):
            bad[sock] = bad.get(sock, 0) + 1
            if bad[sock] >= 3:
                pid = master_pid(sock)
                if pid:
                    try:
                        os.kill(pid, 15)
                        killed.append(f"{os.path.basename(sock)}:{pid}")
                    except OSError:
                        pass
                bad.pop(sock, None)
        else:
            bad.pop(sock, None)
    counts = {}
    for sock, verdict in verdicts.items():
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict != "ok":
            counts.setdefault("bad", []).append(f"{os.path.basename(sock)}:{verdict}")
    return counts, killed


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    try:
        with open(STATE) as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    jobs = {name: (probe_path, (name, kind, addr, alias)) for name, kind, addr, alias in R.PATHS}
    jobs["race-login"] = (login, ["s1-race"])
    for port in (2201, 2202, 2203):
        jobs[f"bridge{port}"] = (login, ["-p", str(port), "-o", "HostKeyAlias=157.180.125.52", "niels@127.0.0.1"])
    with ThreadPoolExecutor(max_workers=len(jobs) + 1) as pool:
        futures = {name: pool.submit(fn, arg) for name, (fn, arg) in jobs.items()}
        reaper = pool.submit(reap_masters, state)
        results = {name: fut.result() for name, fut in futures.items()}
        masters, killed = reaper.result()
    last = next(iter(R.read_names(R.LAST)), "")
    healthy = sorted((r["s"], name) for name, r in results.items() if r["ok"] and name in {p[0] for p in R.PATHS})
    pref_note = ""
    if healthy and not results.get(last, {}).get("ok"):
        R.remember(healthy[0][1])
        pref_note = f" pref:{last or '-'}->{healthy[0][1]}"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    parts = []
    for name, r in results.items():
        parts.append(f"{name}={'ok' if r['ok'] else 'FAIL'}:{r['s']}" + ("" if r["ok"] else f"({r.get('why', '')})"))
    n_ok = sum(1 for name, r in results.items() if r["ok"] and name in {p[0] for p in R.PATHS})
    line = f"{stamp} paths_ok={n_ok}/{len(R.PATHS)} " + " ".join(parts) + f" masters={masters}" + (f" killed={killed}" if killed else "") + pref_note
    rotate(LOG)
    rotate(R.LOG)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as handle:
        json.dump({"ts": stamp, "paths_ok": n_ok, "results": results, "masters": masters, "killed": killed,
                   "preferred": next(iter(R.read_names(R.LAST)), "")}, handle, indent=1)
    os.replace(tmp, STATUS)
    with open(STATE + ".tmp", "w") as handle:
        json.dump(state, handle)
    os.replace(STATE + ".tmp", STATE)


if __name__ == "__main__":
    main()

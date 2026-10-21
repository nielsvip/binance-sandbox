#!/usr/bin/env python3
"""Fleet watchdog: liveness monitor + last-resort rebooter for the V15 fleet.

Runs on Mac (launchd), s2 and gateway (cron). Watches gateway, S1
(dispatcher), s2/s5/s6 (workers). Reboots ONLY a host that fails EVERY path
(direct SSH + via-gateway SSH + hcloud status) for --fail-threshold
consecutive checks AND is past --cooldown; everything is logged, nothing is
silent. --dry-run reports what WOULD happen. Exit 0 always on success path
(checks completed), 2 on internal error.

Reboot needs hcloud CLI + token (env HCLOUD_TOKEN or ~/.config/hcloud/cli.toml).
Without it the watchdog still detects, logs, and --alerts, but cannot reboot.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time

TOPOLOGY = [
    {"name": "gateway", "hcloud": "gateway", "ssh": ["gateway-internal"], "ips": ["10.0.0.2"], "role": "net"},
    {"name": "s1", "hcloud": "niels", "ssh": ["s1-int", "s1-pub"], "ips": ["10.0.0.3"],
     "jump": "gateway-internal", "role": "dispatcher",
     "tick_log": "/tmp/v15_fleet_sched.log", "tick_max_age_s": 480},
    {"name": "s2", "hcloud": "s2", "ssh": ["s2"], "ips": ["10.0.0.4"], "role": "worker"},
    {"name": "s5", "hcloud": "s5", "ssh": ["s5"], "ips": ["10.0.0.5"], "via_gw": True, "role": "worker"},
    {"name": "s6", "hcloud": "s6", "ssh": ["s6"], "ips": ["10.0.0.6"], "via_gw": True, "role": "worker"},
]
GW_JUMP = "gateway-internal"

STATE_DIR = os.environ.get("FLEET_WD_STATE", "/tmp/fleet_watchdog")


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()[:200]
    except Exception as e:
        return 127, "", "exc: %s" % str(e)[:200]


def ssh_uptime(target, timeout=10, via=None):
    # Isolated known_hosts: fleet private IPs get recycled (s4->s2, deleted
    # s5/s6), so shared known_hosts collects stale keys -> false DOWNs.
    # TOFU once per watchdog state dir; a genuinely reimaged host logs
    # a clear changed-key error instead of silently passing.
    kh = os.path.join(STATE_DIR, "known_hosts")
    cmd = ["ssh", "-o", "ConnectTimeout=%d" % timeout, "-o", "BatchMode=yes",
           "-o", "StrictHostKeyChecking=accept-new",
           "-o", "UserKnownHostsFile=%s" % kh]
    if via:
        cmd += ["-J", via]
    cmd += [target, "cat /proc/uptime"]
    rc, out, _ = sh(cmd, timeout=timeout + 5)
    if rc == 0 and out:
        try:
            return float(out.split()[0])
        except ValueError:
            return -1.0
    return None


def hcloud_status(name, timeout=25):
    rc, out, _ = sh(["hcloud", "server", "describe", name, "-o", "json"], timeout=timeout)
    if rc != 0:
        return None
    try:
        return json.loads(out)["status"]
    except (ValueError, KeyError):
        return None


def check_host(h, self_name):
    """Return (ok_bool, detail_str). A host is DOWN only if every path fails."""
    if h["name"] == self_name:
        return True, "self"
    paths = []
    for t in h["ssh"]:
        up = ssh_uptime(t)
        paths.append((t, up))
        if up is not None and up >= 0:
            return True, "ssh:%s up=%ds" % (t, int(up))
    for ip in h.get("ips", []):
        up = ssh_uptime("niels@%s" % ip)
        paths.append((ip, up))
        if up is not None and up >= 0:
            return True, "ssh:%s up=%ds" % (ip, int(up))
    if self_name != "gateway":
        for tgt in [h["ssh"][0]] + ["niels@%s" % ip for ip in h.get("ips", [])]:
            up = ssh_uptime(tgt, timeout=6, via=h.get("jump", GW_JUMP))
            paths.append(("jump:%s" % tgt, up))
            if up is not None and up >= 0:
                return True, "gw-jump:%s up=%ds" % (tgt, int(up))
    st = hcloud_status(h["hcloud"]) if h.get("hcloud") else None
    paths.append(("hcloud", st))
    return False, "all-paths-fail %s hcloud=%s" % (
        ",".join("%s=%s" % (t, ("down" if u is None else u)) for t, u in paths[:-1]), st)


def dispatcher_ok(timeout=15):
    """S1 extra: scheduler tick log fresh. Returns (ok, detail). Only checked
    when S1 itself is reachable; a stale tick with live SSH = wedged
    scheduler, NOT a reboot case (rebooting would kill the dispatcher)."""
    probe = ("python3 -c \"import time,os;p='/tmp/v15_fleet_sched.log';"
               "print(int(time.time()-os.path.getmtime(p)))\"")
    kh = ["-o", "UserKnownHostsFile=%s" % os.path.join(STATE_DIR, "known_hosts")]
    attempts = [["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                 "-o", "StrictHostKeyChecking=accept-new"] + kh + ["s1-int", probe],
                ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                 "-o", "StrictHostKeyChecking=accept-new"] + kh + ["-J", GW_JUMP,
                 "niels@10.0.0.3", probe]]
    out = ""
    for cmd in attempts:
        rc, out, _ = sh(cmd, timeout=timeout)
        if rc == 0 and out.strip():
            break
    if rc != 0:
        return None, "tick log unreadable via all S1 paths"
    try:
        age = int(out.strip().splitlines()[-1])
    except ValueError:
        return None, "tick age unparsable"
    return age, "tick age %ds" % age


def load_state():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "state.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "state.json"), "w") as f:
            json.dump(st, f)
    except OSError:
        pass


def maybe_reboot(h, st, now, args, log):
    """Last-resort reboot. Returns action string. NEVER reboots gateway from
    gateway itself, never reboots without cooldown, never in dry-run."""
    key = "fail_%s" % h["name"]
    fails = int(st.get(key, 0)) + 1
    st[key] = fails
    if fails < args.fail_threshold:
        return "watching (%d/%d)" % (fails, args.fail_threshold)
    last_rb = float(st.get("reboot_%s" % h["name"], 0))
    if now - last_rb < args.cooldown:
        return "reboot-cooldown %ds left" % int(args.cooldown - (now - last_rb))
    if h["name"] == "gateway" and args.self_name == "gateway":
        return "REFUSE-gateway-suicide (needs Mac/s2 operator)"
    if args.dry_run:
        return "WOULD-REBOOT (dry-run)"
    rc, out, err = sh(["hcloud", "server", "reboot", h["hcloud"]], timeout=90)
    if rc == 0:
        st["reboot_%s" % h["name"]] = now
        st[key] = 0
        return "REBOOTED via hcloud"
    return "reboot-FAILED rc=%d %s %s" % (rc, out[:80], err[:80])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fail-threshold", type=int, default=3)
    ap.add_argument("--cooldown", type=int, default=1800)
    ap.add_argument("--self-name", default=socket.gethostname().split(".")[0])
    ap.add_argument("--tick-max-age-s", type=int, default=480)
    a = ap.parse_args()
    now = time.time()
    st = load_state()
    log = []
    for h in TOPOLOGY:
        ok, detail = check_host(h, a.self_name)
        if ok:
            st["fail_%s" % h["name"]] = 0
            extra = ""
            if h["name"] == "s1" and h.get("tick_log"):
                age, tdetail = dispatcher_ok()
                if age is None:
                    extra = " scheduler-UNREADABLE(%s)" % tdetail
                elif age > a.tick_max_age_s:
                    extra = " scheduler-STALE(%s, no reboot: dispatcher must be fixed by hand)" % tdetail
                else:
                    extra = " scheduler-ok(%s)" % tdetail
            log.append("%s OK %s%s" % (h["name"], detail, extra))
            continue
        action = maybe_reboot(h, st, now, a, log)
        log.append("%s DOWN %s -> %s" % (h["name"], detail, action))
    save_state(st)
    print("[fleet-watchdog %s self=%s dry=%s]" % (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), a.self_name, a.dry_run))
    for line in log:
        print("  " + line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

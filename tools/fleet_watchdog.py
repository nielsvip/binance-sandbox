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
    {"name": "gateway", "hcloud": "gateway", "ssh": ["gateway-internal"], "role": "net"},
    {"name": "s1", "hcloud": "niels", "ssh": ["s1-int", "s1-pub"], "role": "dispatcher",
     "tick_log": "/tmp/v15_fleet_sched.log", "tick_max_age_s": 480},
    {"name": "s2", "hcloud": "s2", "ssh": ["s2"], "role": "worker"},
    {"name": "s5", "hcloud": "s5", "ssh": ["s5"], "via_gw": True, "role": "worker"},
    {"name": "s6", "hcloud": "s6", "ssh": ["s6"], "via_gw": True, "role": "worker"},
]

STATE_DIR = os.environ.get("FLEET_WD_STATE", "/tmp/fleet_watchdog")


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()[:200]
    except Exception as e:
        return 127, "", "exc: %s" % str(e)[:200]


def ssh_uptime(target, timeout=10, via=None):
    cmd = ["ssh", "-o", "ConnectTimeout=%d" % timeout, "-o", "BatchMode=yes",
           "-o", "StrictHostKeyChecking=accept-new"]
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
    if h.get("via_gw") and self_name != "gateway":
        up = ssh_uptime(h["ssh"][0], via="gateway-internal")
        paths.append(("gw-jump", up))
        if up is not None and up >= 0:
            return True, "gw-jump up=%ds" % int(up)
    st = hcloud_status(h["hcloud"]) if h.get("hcloud") else None
    paths.append(("hcloud", st))
    return False, "all-paths-fail %s hcloud=%s" % (
        ",".join("%s=%s" % (t, ("down" if u is None else u)) for t, u in paths[:-1]), st)

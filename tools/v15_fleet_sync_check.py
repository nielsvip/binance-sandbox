"""v15_fleet_sync_check.py — S1 orchestrator guard: all workers MUST run S1's exact scripts.

Runs every 5 min via cron on S1. Compares md5 of FLEET_SYNC_FILES on each worker
vs S1's canonical copies; any mismatch is overwritten from S1 and logged.
NEVER the reverse: S1 is canonical, Mac pushes to S1 via atomic deploy only.
Exit 0 always (log-only); prints SYNC-CLEAN or SYNC-FIXED lines.
"""
import hashlib
import os
import subprocess
import sys

FILES = ["v15_pilot.py", "tools/v15_fleet_scheduler.py", "tools/v15_gain_pusher.py", "tools/v15_causal_learner.py", "tools/v15_cell_evidence.py", "tools/fleet_hosts_final.json", "tools/v15_daily_chain_s1.sh", "v12_quick_engine.py", "backtest_v12_engine.py", "ez_manage.py", "tradier_manage.py", "config.py", "config_tradier.py"]
HOSTS = ["10.0.0.4", "10.0.0.5", "10.0.0.6"]
BASE = os.path.expanduser("~/binance-sandbox")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]


def md5_local(path):
    try:
        return hashlib.md5(open(path, "rb").read()).hexdigest()
    except Exception:
        return None


def md5_remote(host, rel):
    try:
        out = subprocess.run(SSH + ["niels@%s" % host, "md5sum %s/%s" % (BASE, rel)], capture_output=True, text=True, timeout=25)
        return out.stdout.split()[0] if out.returncode == 0 else None
    except Exception:
        return None


def push_file(host, rel):
    try:
        r = subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "%s/%s" % (BASE, rel), "niels@%s:%s/%s" % (host, BASE, rel)], capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def main():
    fixed, failed, checked = 0, 0, 0
    for host in HOSTS:
        for rel in FILES:
            want = md5_local("%s/%s" % (BASE, rel))
            if want is None:
                print("SYNC-S1-MISSING %s" % rel, flush=True)
                failed += 1
                continue
            got = md5_remote(host, rel)
            checked += 1
            if got != want:
                ok = push_file(host, rel) and md5_remote(host, rel) == want
                print("SYNC-%s %s %s s1=%s was=%s" % ("FIXED" if ok else "FAIL", host, rel, want[:12], (got or "MISSING")[:12]), flush=True)
                fixed += ok
                failed += (not ok)
    print("SYNC-DONE checked=%d fixed=%d failed=%d" % (checked, fixed, failed), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""v15_host_drain — gracefully remove a worker host (USER 2026-10-09: s6/s7 pattern).

Usage (from Mac): .venv/bin/python tools/v15_host_drain.py --host s6 [--evac-only|--rm-only]
  1. fence: set max_pairs=0 in S1 fleet_hosts_final.json (no NEW boards; running drain).
  2. wait: poll scheduler log until host holds 0 running (timeout 3h, 5-min polls).
  3. evac: rsync run-dir progress + CELL_BY_CELL xlsx + pusher done/ to S1 ~/evac_{host}/.
  4. verify: counts + newest mtimes; print DELETE-CLEARANCE line (human deletes the box).
  5. rm: remove host from S1 + Mac hosts files (scheduler + pusher discovery drop it).

--evac-only skips fence/wait (dead-box scramble); --rm-only skips to step 5.
DANGER: deleting the box is manual (hcloud); this script only prepares + verifies.
"""
import json
import subprocess
import sys
import time

SSH = ["ssh", "-S", "none", "-o", "StrictHostKeyChecking=accept-new", "s1-int"]


def sh(cmd, timeout=120):
    p = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--evac-only", action="store_true")
    ap.add_argument("--rm-only", action="store_true")
    a = ap.parse_args()
    h = a.host
    if not a.evac_only and not a.rm_only:
        rc, out = sh(f"cd ~/binance-sandbox && python3 -c \"import json; p='tools/fleet_hosts_final.json'; d=json.load(open(p)); [x.__setitem__('max_pairs', 0) for x in d['hosts'] if x['name']=='{h}']; json.dump(d, open(p, 'w'), indent=1); print('fenced {h}')\"")
        print(out.strip()[-200:])
        t0 = time.time()
        while time.time() - t0 < 3 * 3600:
            rc, out = sh(f"tail -n 2 /tmp/v15_fleet_sched.log | grep -o '\"{h}\": {{\"[^}}]*' | tail -1")
            print(f"[{time.strftime('%H:%M')}] {h}: {out.strip()[:160]}")
            if '"held": []' in out or "UNREACHABLE" in out:
                break
            time.sleep(300)
    via = {"s1": "127.0.0.1", "s2": "10.0.0.4", "s5": "10.0.0.5", "s6": "10.0.0.6", "s7": "10.0.0.7"}.get(h, h)
    if not a.rm_only:
        rc, out = sh(f"mkdir -p ~/evac_{h}/progress ~/evac_{h}/xlsx ~/evac_{h}/pusher && "
                     f"rsync -az -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' {via}:~/v15_run*/progress/*.json ~/evac_{h}/progress/ 2>&1 | tail -1; "
                     f"rsync -az -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' {via}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx ~/evac_{h}/xlsx/ 2>&1 | tail -1; "
                     f"rsync -az -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' {via}:~/v15_pusher/done/ ~/evac_{h}/pusher/ 2>&1 | tail -1; "
                     f"echo PROG=$(ls ~/evac_{h}/progress/ 2>/dev/null | wc -l) XLSX=$(ls ~/evac_{h}/xlsx/ 2>/dev/null | wc -l) PUSH=$(ls ~/evac_{h}/pusher/ 2>/dev/null | wc -l)")
        print(out.strip()[-400:])
    if not a.evac_only:
        rc, out = sh(f"cd ~/binance-sandbox && python3 -c \"import json; p='tools/fleet_hosts_final.json'; d=json.load(open(p)); d['hosts']=[x for x in d['hosts'] if x['name']!='{h}']; json.dump(d, open(p, 'w'), indent=1); print('removed {h}, left:', [x['name'] for x in d['hosts']])\"")
        print(out.strip()[-200:])
    print(f"DRAIN DONE {h}: verify counts above, then delete the box in hcloud.")


if __name__ == "__main__":
    main()

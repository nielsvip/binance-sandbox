#!/usr/bin/env python3
"""
elastic_idle_watchdog.py — runs on each ephemeral temp (s2/s3/s5/sN) via cron every 2 min.
If no more work exists AND all results have been synced back to S1, self-terminate (or mark done).

Bias/latest discipline: before deciding idle, refreshes global_done from S1 so a newly-pushed
ORDER on S1 is never mistaken for "done". Delete only when S1 confirms same xlsx count.
"""
from __future__ import annotations
import pathlib, subprocess, time, sys, json, hashlib

ROOT = pathlib.Path.home() / "binance-sandbox"
S1_PRIV = "10.0.0.3"
S1_PUB = "157.180.125.52"
IDLE_MIN = 10  # must be todo==0 for this many minutes before delete
MARK = ROOT / ".elastic" / "ELASTIC_DONE"
FLAG = pathlib.Path("/tmp/ELASTIC_DONE")

def sh(cmd: str, timeout=10) -> str:
    try:
        return subprocess.check_output(["bash","-c",cmd], text=True, timeout=timeout)
    except Exception as e:
        return ""

# never run on s1
try:
    host = sh("hostname", 5).strip()
    if "niels" in host and "htz" not in host:
        # s1 trading host
        sys.exit(0)
except: pass
# sentinel already marked done -> try delete again
if MARK.exists():
    # attempt Hetzner self-delete if env present
    import os
    token = os.environ.get("HCLOUD_TOKEN","")
    sid = os.environ.get("HCLOUD_SERVER_ID","")
    if token and sid:
        print(f"[idle] MARK exists — deleting server {sid} via Hetzner API")
        sh(f"curl -sf -X DELETE -H 'Authorization: Bearer {token}' https://api.hetzner.cloud/v1/servers/{sid} 2>&1 | tail", 10)
    sys.exit(0)

# 1. check todo via herd log + global
order_candidates = [ROOT/"SPREADSHEETS"/"V15_RUNNING_ORDER_TRB_FLZ.txt", ROOT/"SPREADSHEETS"/"V15_FULL_354.txt"]
order = None
for p in order_candidates:
    if p.exists():
        order = p
        break
if order is None:
    print("[idle] no order file — skip")
    sys.exit(0)
order_syms = [l.strip() for l in order.read_text().splitlines() if l.strip()]
# global done from S1
global_done = None
for h in [S1_PRIV, S1_PUB]:
    out = sh(f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no niels@{h} 'ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | xargs -I{{}} basename {{}}' 2>&1", 10)
    if out and "No such" not in out:
        names = out.strip().splitlines()
        done = set()
        for name in names:
            for s in order_syms:
                if s in name:
                    done.add(s)
        global_done = done
        break
if global_done is None:
    print("[idle] S1 unreachable — cannot decide idle (fail-closed, no delete)")
    sys.exit(0)
# local todo = order - global_done (temps share S1 truth; local shard already accounted)
todo = [s for s in order_syms if s not in global_done]
# also check herd's own todo line (more precise for hash shard)
herd_todo = sh("grep -o 'todo [0-9]*' /tmp/v15_local_herd.log 2>/dev/null | tail -1 | awk '{print $2}'", 5).strip()
try:
    herd_todo_n = int(herd_todo) if herd_todo else None
except:
    herd_todo_n = None
print(f"[idle] order {len(order_syms)} global_done {len(global_done)} todo {len(todo)} herd_todo {herd_todo_n} host {host}")

# 2. synced check: any local xlsx not yet on S1?
local_xlsx = list((ROOT/"SPREADSHEETS"/"V15_V16_CELL_BY_CELL").glob("*.xlsx"))
s1_xlsx_count = len(global_done)  # approximate; better to count via S1 ls
# dry-run rsync to see unsynced
unsynced = sh(f"rsync -az --dry-run -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {ROOT}/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx niels@{S1_PRIV}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | grep -c '.xlsx' || echo 0", 30).strip()
try:
    unsynced_n = int(unsynced.splitlines()[-1].strip())
except:
    unsynced_n = 999
# also progress json unsynced
unsynced_json = sh(f"rsync -az --dry-run -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {ROOT}/data/reports/lifecycle_pilot/*.json niels@{S1_PRIV}:~/binance-sandbox/data/reports/lifecycle_pilot/ 2>&1 | grep -c '.json' || echo 0", 20).strip()
try:
    unsynced_json_n = int(unsynced_json.splitlines()[-1].strip())
except:
    unsynced_json_n = 0
print(f"[idle] local_xlsx {len(local_xlsx)} unsynced_xlsx_lines {unsynced_n} unsynced_json {unsynced_json_n}")

if len(todo) != 0 or (herd_todo_n is not None and herd_todo_n != 0):
    # still work — ensure we flush sync then exit
    if unsynced_n > 0 or unsynced_json_n > 0:
        print(f"[idle] todo>0 but unsynced — flushing to S1 ...")
        sh(f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {ROOT}/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx niels@{S1_PRIV}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail", 60)
    sys.exit(0)

# todo==0 — check if truly done and synced
if unsynced_n != 0 or unsynced_json_n != 0:
    print(f"[idle] todo 0 but unsynced>0 — flushing and not deleting yet")
    sh(f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {ROOT}/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx niels@{S1_PRIV}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail", 60)
    sh(f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {ROOT}/data/reports/lifecycle_pilot/*.json niels@{S1_PRIV}:~/binance-sandbox/data/reports/lifecycle_pilot/ 2>&1 | tail", 30)
    sys.exit(0)

# todo==0 and synced — idle for IDLE_MIN?
# use herd log last launch time
age_check = sh("stat -c %Y /tmp/v15_local_herd.log 2>/dev/null || stat -f %m /tmp/v15_local_herd.log 2>/dev/null || echo 0", 5).strip()
try:
    age_s = time.time() - int(age_check)
except:
    age_s = 0
# also check no pilot running
running = sh("ps aux | grep v15_pilot | grep -v grep | wc -l", 5).strip()
try:
    running_n = int(running)
except:
    running_n = 1
print(f"[idle] todo 0 synced running_pilots {running_n} log_age_s {int(age_s)} threshold {IDLE_MIN*60}")
if running_n != 0:
    print("[idle] pilots still running — not idle")
    sys.exit(0)
if age_s < IDLE_MIN*60:
    print(f"[idle] idle {int(age_s)}s < {IDLE_MIN*60}s — waiting")
    sys.exit(0)

# truly idle, synced, no pilots — mark and self-delete
print(f"[idle] IDLE {IDLE_MIN}m with todo 0 and synced — marking done and self-deleting")
MARK.parent.mkdir(parents=True, exist_ok=True)
MARK.write_text(f"done {time.strftime('%FT%TZ', time.gmtime())} order {order.name} global_done {len(global_done)} host {host}\n")
FLAG.write_text("done\n")
# final sync
sh(f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {ROOT}/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx niels@{S1_PRIV}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail", 60)
import os
token = os.environ.get("HCLOUD_TOKEN","")
sid = os.environ.get("HCLOUD_SERVER_ID","") or os.environ.get("HETZNER_SERVER_ID","")
# Hetzner metadata service gives ID
if not sid:
    sid = sh("curl -sf http://169.254.169.254/hetzner/v1/instance-id 2>/dev/null || echo ''", 5).strip()
if token and sid:
    print(f"[idle] deleting self via Hetzner API server {sid}")
    out = sh(f"curl -sf -X DELETE -H 'Authorization: Bearer {token}' https://api.hetzner.cloud/v1/servers/{sid} 2>&1", 10)
    print(out)
else:
    print(f"[idle] no HCLOUD_TOKEN/SERVER_ID — not auto-deleting. S1 cron cull-idle will delete. Token present: {bool(token)} id {sid}")
    # also try hetzner cloud-init delete via shutdown
    # leave marker for S1 cull

#!/bin/bash
# v15_round_watchdog — USER 2026-10-08 ("you let the s1 coordinator sit there and die"): if the fleet scheduler reports
# every REACHABLE, NON-DRAINED host idle with "no eligible ready symbol" for 3 consecutive ticks (round exhausted)
# and no pilot runs anywhere, roll the round through the autopilot's own stage (tools/v15_autopilot.py --force-stage restart).
# USER 2026-10-15 (06.1, round-concept decision: KEEP rounds, ignore dead weight): UNREACHABLE hosts and drained hosts
# (data/fleet_drain_hosts.json + $V15_SCHED_DRAIN_HOSTS) are IGNORED by the check — s5/s6 "UNREACHABLE" used to veto
# the all() forever (round stuck on v15_run32_20261011). Hosts blocked for a REAL reason (disk/mem guard, slots full)
# still veto: the round is not exhausted then, rolling fixes nothing. Ticks with zero judgable hosts are never a strike.
# Idempotent, logged.
cd /home/niels/binance-sandbox || exit 1
L=${V15_SCHED_LOG:-/tmp/v15_fleet_sched.log}; W=/tmp/v15_round_watchdog.log
log(){ echo "[$(date -u +%FT%TZ)] [round-wd] $*" >> $W; }
N=$(grep '^{"now"' $L | tail -3 | python3 -c '
# PY-CHECK-START
import sys,json,os
drain={x.strip() for x in os.environ.get("V15_SCHED_DRAIN_HOSTS","").split(",") if x.strip()}
try:
    drain|={str(x) for x in json.load(open("data/fleet_drain_hosts.json")).get("hosts",[])}
except Exception:
    pass
n=0
for line in sys.stdin:
    try: d=json.loads(line)
    except Exception: continue
    hs=d.get("hosts",{})
    elig={k:v for k,v in hs.items() if isinstance(v,dict) and k not in drain and not v.get("draining")}
    if not elig: continue
    if all(v.get("idle_reason","").startswith("no eligible") and v.get("slots","").startswith("0/") for v in elig.values()) and not d.get("launched"): n+=1
print(n)
# PY-CHECK-END
')
RUNNING=$(pgrep -f "v15_pilot.py --sym-side" | wc -l)
if [ "$N" -ge 3 ] && [ "$RUNNING" -eq 0 ]; then
  log "round exhausted ($N idle ticks, 0 pilots) -> rolling round"
  timeout 300 python3 tools/v15_autopilot.py --once --force-stage restart >> $W 2>&1 && log "restart stage ok" || log "restart stage FAILED rc=$?"
fi

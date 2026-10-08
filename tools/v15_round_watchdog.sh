#!/bin/bash
# v15_round_watchdog — USER 2026-10-08 ("you let the s1 coordinator sit there and die"): if the fleet scheduler reports
# every host idle with "no eligible ready symbol" for 3 consecutive ticks (round exhausted) and no pilot runs anywhere,
# roll the round through the autopilot's own stage (tools/v15_autopilot.py --force-stage restart). Idempotent, logged.
cd /home/niels/binance-sandbox || exit 1
L=/tmp/v15_fleet_sched.log; W=/tmp/v15_round_watchdog.log
log(){ echo "[$(date -u +%FT%TZ)] [round-wd] $*" >> $W; }
N=$(grep '^{"now"' $L | tail -3 | python3 -c '
import sys,json
n=0
for line in sys.stdin:
    try: d=json.loads(line)
    except Exception: continue
    hs=d.get("hosts",{}); 
    if hs and all(isinstance(v,dict) and v.get("idle_reason","").startswith("no eligible") and v.get("slots","").startswith("0/") for v in hs.values()) and not d.get("launched"): n+=1
print(n)')
RUNNING=$(pgrep -f "v15_pilot.py --sym-side" | wc -l)
if [ "$N" -ge 3 ] && [ "$RUNNING" -eq 0 ]; then
  log "round exhausted ($N idle ticks, 0 pilots) -> rolling round"
  timeout 300 python3 tools/v15_autopilot.py --once --force-stage restart >> $W 2>&1 && log "restart stage ok" || log "restart stage FAILED rc=$?"
fi

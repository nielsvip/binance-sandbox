#!/bin/bash
# v15_stall_sentinel.sh — S1 cron */15. Read-only: any worker silent >25min
# while work is pending = STALL (loud log; never kills — reaping is the
# scheduler's job). USER 2026-10-08: no server stalls, death penalty otherwise.
ALERT=/tmp/stall_alert.log
PEND=$(tail -1 /tmp/v15_fleet_sched.log 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('pending_symbols',0))" 2>/dev/null)
for H in 127.0.0.1 10.0.0.4 10.0.0.5 10.0.0.6; do
  if [ "$H" = "127.0.0.1" ]; then
    AGE=$(find /tmp/sweep_*.log -mmin +25 2>/dev/null | head -1)
    NEW=$(ls -t /tmp/sweep_*.log 2>/dev/null | head -1)
    N=$(pgrep -c -f 'v15_pilot|v15_graph_search|v15_365_cycle' 2>/dev/null)
  else
    OUT=$(ssh -S none -o BatchMode=yes -o ConnectTimeout=10 "niels@$H" "ls -t /tmp/sweep_*.log 2>/dev/null | head -1; pgrep -c -f 'v15_pilot|v15_graph_search|v15_365_cycle' 2>/dev/null; find \$(ls -t /tmp/sweep_*.log 2>/dev/null | head -1) -mmin +25 2>/dev/null" 2>/dev/null)
    NEW=$(echo "$OUT" | sed -n 1p); N=$(echo "$OUT" | sed -n 2p); AGE=$(echo "$OUT" | sed -n 3p)
  fi
  if [ -n "$AGE" ] && [ "${PEND:-0}" -gt 0 ] 2>/dev/null; then
    echo "[$(date -u +%FT%TZ)] STALL $H newest=$NEW procs=$N pending=$PEND" >> "$ALERT"
    echo "[$(date -u +%FT%TZ)] STALL $H newest=$NEW procs=$N pending=$PEND"
  fi
done
exit 0

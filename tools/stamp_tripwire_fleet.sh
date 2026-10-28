#!/bin/bash
# stamp_tripwire_fleet.sh - Mac cron every 30 min: scan the Mac and every host (files modified in the last 45 min; FULL=1 scans everything) and log one line per host.
# 2026-10-28 operator: every kline / NPZ stamp must be the canonical UTC Z stamp on all servers and the MacBook. Alert = a line starting with ALERT in the log.
cd /Users/niels/Documents/binance || exit 1
LOG=/Users/niels/logs/stamp_tripwire.log; mkdir -p /Users/niels/logs
ARGS="--quiet"; [ -z "$FULL" ] && ARGS="$ARGS --since-minutes 45"
echo "$(date -u +%FT%TZ) fleet scan start full=${FULL:-0}" >> $LOG
/usr/bin/python3 tools/stamp_tripwire.py --base . --no-npz $ARGS --json data/reports/stamp_tripwire_mac.json >> $LOG 2>&1
for h in s1-pub s2 s5 s6; do
  timeout 280 ssh -o BatchMode=yes -o ConnectTimeout=20 -o ControlPath=none $h "cd ~/binance-sandbox && nice -n 15 /home/niels/.conda/envs/binance_env_mac/bin/python tools/stamp_tripwire.py --base ~/binance-sandbox --quiet $ARGS --json data/reports/stamp_tripwire.json" >> $LOG 2>&1 || echo "ALERT stamp_tripwire host=$h unreachable-or-timeout" >> $LOG
done

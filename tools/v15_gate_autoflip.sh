#!/bin/bash
# v15_gate_autoflip.sh — remove stocks-only scheduler gate once stocks chain drains,
# then refresh avg-deltas so crypto updates flow immediately. Fail-open everywhere.
LOG=/home/niels/logs/v15_gate_flip.log
TICK=/tmp/v15_fleet_sched.log
[ -f "$TICK" ] || exit 0
LEFT=$(tail -1 "$TICK" | python3 -c "import json,sys; print(json.load(sys.stdin).get('stocks_chain_left',-1))" 2>/dev/null)
[ "$LEFT" = "0" ] || exit 0
crontab -l 2>/dev/null | grep -q 'V15_SCHED_VENUE_ONLY=stocks' || exit 0
crontab -l 2>/dev/null > ~/cron_backup_before_flip_$(date -u +%Y%m%d%H%M).txt
crontab -l 2>/dev/null | sed 's/V15_SCHED_VENUE_ONLY=stocks //' | crontab -
echo "$(date -u +%FT%TZ) FLIP: stocks_chain_left=0 gate removed" >> "$LOG"
cd /home/niels/binance-sandbox && setsid -f bash -c '.venv/bin/python -u tools/v15_avg_delta_rebuild.py --date $(date -u +%Y%m%d) >> /home/niels/logs/v15_avg_flip.log 2>&1' </dev/null >/dev/null 2>&1
echo "$(date -u +%FT%TZ) FLIP: post-stocks avg rebuild launched" >> "$LOG"

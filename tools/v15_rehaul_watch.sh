#!/bin/bash
# v15_rehaul_watch.sh — USER 2026-10-12: watch the stocks complete-rehaul round until every
# tradeable stocks sym_side is done. Runs from Mac cron every 15 min. Appends progress to
# data/daily_chain/stocks_rehaul_progress.log; alerts (log + notification) on STALL /
# NO_PROGRESS / COMPLETE. Read-only against the fleet (one ssh tick-parse).
cd /Users/niels/Documents/binance || exit 0
LOG=data/daily_chain/stocks_rehaul_progress.log
ALERT=data/daily_chain/ALERT_STOCKS_REHAUL.txt
SENT=data/daily_chain/stocks_rehaul_complete.sentinel
mkdir -p data/daily_chain
SNAP=$(ssh -S none -o BatchMode=yes -o ConnectTimeout=20 s1-int "python3 -c \"
import json,time
ticks=[json.loads(l) for l in open('/tmp/v15_fleet_sched.log') if l.startswith('{')]
t=ticks[-1]
c=t.get('cats',{})
import datetime
age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(t['now'])).total_seconds()/60
print(int(age), c.get('STOCKS_LONG',{}).get('done30',0)+c.get('STOCKS_SHORT',{}).get('done30',0), c.get('STOCKS_LONG',{}).get('running',0)+c.get('STOCKS_SHORT',{}).get('running',0), t.get('pending_symbols',-1), t['now'][11:16])
\"" 2>/dev/null) || { echo "$(date -u +%FT%TZ) SNAP_FAIL s1 unreachable" >> "$LOG"; exit 0; }
# shellcheck disable=SC2086
set -- $SNAP
AGE=$1; DONE=$2; RUN=$3; PEND=$4; TICK=$5
echo "$(date -u +%FT%TZ) tick=${TICK}Z age_min=${AGE} done30=${DONE} running=${RUN} pending=${PEND}" >> "$LOG"
notify() { echo "$(date -u +%FT%TZ) $1" >> "$ALERT"; osascript -e "display notification \"$1\" with title \"stocks rehaul\"" 2>/dev/null || true; }
if [ "$AGE" -gt 12 ] 2>/dev/null; then notify "STALL: no scheduler tick for ${AGE} min"; exit 0; fi
if [ "$PEND" = "0" ] && [ "$RUN" = "0" ] 2>/dev/null; then
  if [ ! -f "$SENT" ]; then echo "$(date -u +%FT%TZ) done30=${DONE}" > "$SENT"; notify "COMPLETE: pending=0 running=0 done30=${DONE} — verify 12:15Z chain + 13:00Z Mac apply"; fi
  exit 0
fi
# NO_PROGRESS: done30 unchanged over last 6 samples (~90 min) while work remains
LAST6=$(tail -n 6 "$LOG" | grep -o "done30=[0-9]*" | sort -u | wc -l | tr -d ' ')
TAILN=$(tail -n 6 "$LOG" | wc -l | tr -d ' ')
if [ "$TAILN" -ge 6 ] && [ "$LAST6" = "1" ] && [ "$PEND" != "0" ]; then notify "NO_PROGRESS: done30=${DONE} flat 90min pending=${PEND}"; fi

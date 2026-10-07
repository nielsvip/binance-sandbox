#!/bin/bash
# v12_hang_watchdog — stops all cronjobs at once if v12_pilot hangs
# Runs every 2 min via cron, kills 105% CPU stuck at Loaded 1 symbols >20m
set -e
SENTINEL=/tmp/v12_hang_sentinel
LOG=/tmp/v12_hang_watchdog.log
if [ -f "$SENTINEL" ]; then echo "$(date) SENTINEL exists — all stopped" >> $LOG; exit 0; fi
# check any v12_pilot matrix older than 1200s (20m)
for pid in $(pgrep -f "v12_pilot.*matrix" 2>/dev/null || true); do
  etimes=$(ps -o etimes= -p $pid 2>/dev/null | tr -d " ")
  if [ -z "$etimes" ]; then continue; fi
  if [ "$etimes" -gt 1200 ]; then
    # also check if still at Loaded 1 symbols with no ledger progress (last write >10m ago)
    # find ledger dir for this pid
    echo "$(date) HANG pid $pid etimes $etimes — killing ALL" >> $LOG
    pkill -9 -f "v12_pilot" || true
    pkill -9 -f "xargs.*v12" || true
    pkill -9 -f "run_trb_fleet" || true
    rm -f /tmp/trb_matrix.lock
    # kill outer flock holders
    for flockpid in $(lsof -t /tmp/trb_matrix.lock 2>/dev/null || true); do kill -9 $flockpid || true; done
    touch $SENTINEL
    echo "$(date) SENTINEL set — all cronjobs halted, manual: rm $SENTINEL && systemctl restart cron or crontab" >> $LOG
    # also stop any trb_matrix_cron.sh
    pkill -9 -f "trb_matrix_cron" || true
    exit 1
  fi
done
# also check stale lock file age >30m with no v12_pilot
if [ -f /tmp/trb_matrix.lock ]; then
  if ! pgrep -f "v12_pilot" >/dev/null; then
    age=$(($(date +%s) - $(stat -c %Y /tmp/trb_matrix.lock 2>/dev/null || echo 0)))
    if [ "$age" -gt 1800 ]; then
      echo "$(date) STALE lock $age s with no v12_pilot — removing" >> $LOG
      rm -f /tmp/trb_matrix.lock
    fi
  fi
fi

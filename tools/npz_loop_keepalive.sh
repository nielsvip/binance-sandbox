#!/bin/bash
# npz_loop_keepalive.sh — keep tools/npz_bar_refresh.py --loop alive AND PROGRESSING (runs on S1 from cron */5).
#
# USER 2026-10-16 ("revise and fix so they are always up to date from now on"). The cron this replaces tested
# liveness with `pgrep` alone, so a loop that had stopped doing work still counted as healthy. MEASURED the same
# day, 21:24-21:28Z: a SIGTERMed loop hung in do_wait() (Pool.join() has no timeout, one worker was inside builder
# C code) and the keepalive logged "locked (previous daemon active)" and started nothing — every NPZ would have
# gone stale behind a live pid. Same trap that let the Mac position writers wedge for 4.5 h on 2026-10-15, fixed
# the same way: judge the HEARTBEAT (the loop's own log), not the pid.
set -u
ROOT=/home/niels/binance-sandbox
LOG=/tmp/npz_loop.log
MAX_LOG_AGE=${NPZ_LOOP_MAX_LOG_AGE:-900}   # a healthy cycle logs a line per installed symbol; 900 s = one bar of silence
export NPZ_LOOP_SETTLE_S=${NPZ_LOOP_SETTLE_S:-300}
export NPZ_LOOP_MAX_WORKERS=${NPZ_LOOP_MAX_WORKERS:-8}
WORKERS=${NPZ_LOOP_WORKERS:-6}

klog() { echo "$(date -u +%FT%TZ) $*"; }

pids=$(pgrep -f "npz_bar_refresh\.py.*--loop" || true)
parent=""
for p in $pids; do
  [ "$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')" = "1" ] && parent=$p
done
[ -z "$parent" ] && [ -n "$pids" ] && parent=$(echo "$pids" | head -1)

if [ -n "$parent" ]; then
  age=999999
  [ -f "$LOG" ] && age=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
  if [ "$age" -le "$MAX_LOG_AGE" ]; then
    exit 0
  fi
  klog "loop pid $parent ALIVE but its log has been silent ${age}s (>${MAX_LOG_AGE}) — killing it and its workers"
  pkill -KILL -f "npz_bar_refresh\.py.*--loop" || true
  sleep 3
fi

cd "$ROOT" || exit 1
klog "starting loop (workers $WORKERS, cap $NPZ_LOOP_MAX_WORKERS, settle ${NPZ_LOOP_SETTLE_S}s)"
exec setsid nice -n 15 "$ROOT/.venv/bin/python" -u tools/npz_bar_refresh.py --mode both --workers "$WORKERS" --w15 2500 --loop >> "$LOG" 2>&1 < /dev/null

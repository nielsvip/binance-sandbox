#!/bin/bash
# Kill mega-sweep pilot processes (env V15_PROGRESS_DIR=~/v15_mega_progress) whose sym_side log has not been
# written for > N minutes: hung pilots and orphaned fork-pool workers. Sheets resume from progress on relaunch.
# Usage: v15_mega_reap_stale.sh <venue> [minutes=20]
VENUE=$1
MIN=${2:-20}
LOGDIR=~/v15_mega_logs/MEGA_${VENUE}_jump
NOW=$(date +%s)
killed=0
for p in $(pgrep -f "v15_mega_pilot.py --sym-side"); do
  grep -q v15_mega_progress /proc/$p/environ 2>/dev/null || continue
  ss=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -oE -- "--sym-side [^ ]+" | cut -d' ' -f2)
  [ -n "$ss" ] || continue
  log="$LOGDIR/$ss.log"
  age=$(( (NOW - $(stat -c %Y "$log" 2>/dev/null || echo 0)) / 60 ))
  lim=$MIN
  # DONE stage (all rows + final recheck written, LIVE verification running) prints nothing for 10-60+ min:
  # 2026-09-28 this reaper killed 20 fully-filled s2 sheets there — allow 120 min before calling it hung
  tail -c 8192 "$log" 2>/dev/null | grep -q "\[final-recheck\]" && ! tail -c 8192 "$log" | grep -q "spec-fill\] DONE" && lim=120
  if [ "$age" -gt "$lim" ]; then
    kill -9 "$p" 2>/dev/null && killed=$((killed + 1)) && echo "[$(date -u +%FT%TZ)] reaped pid $p $ss (log idle ${age}m > ${lim}m)"
  fi
done
echo "reaped=$killed"

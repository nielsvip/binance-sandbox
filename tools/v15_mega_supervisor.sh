#!/bin/bash
# Keeps the v15 mega sweep alive and CPU-saturated on this server (s1 crypto / s2 stocks).
# Every 60s: restart the driver if it died (pilots resume from progress, nothing recomputed); scale symbol slots
# to hold CPU >90% without RAM >90% (no OOM). Usage: v15_mega_supervisor.sh <crypto|stocks> <python>
VENUE=$1
PY=$2
CTRL=~/.v15_mega_parallel_$VENUE
LOG=~/v15_mega_supervisor_$VENUE.log
cd ~/binance-sandbox || exit 1
[ -f "$CTRL" ] || echo 4 > "$CTRL"
while true; do
  if [ -f ~/.v15_mega_pause_$VENUE ]; then  # HOLD (e.g. until filter-wiring census clears): no driver, no mega pilots
    for p in $(pgrep -f "^[^ ]*python -u tools/v15_mega_sweep.py --venue $VENUE"); do kill "$p"; done
    for p in $(pgrep -f "v15_mega_pilot.py --sym-side"); do grep -q v15_mega_progress /proc/$p/environ 2>/dev/null && kill "$p"; done
    echo "[$(date -u +%FT%TZ)] PAUSED ($(cat ~/.v15_mega_pause_$VENUE 2>/dev/null))" >> "$LOG"
    sleep 60
    continue
  fi
  if ! pgrep -f "python -u tools/v15_mega_sweep.py --venue $VENUE" > /dev/null; then
    cat ~/v15_mega_$VENUE.log >> ~/v15_mega_${VENUE}_run1.log 2>/dev/null
    setsid nohup $PY -u tools/v15_mega_sweep.py --venue $VENUE --parallel "$(cat $CTRL)" --workers 2 --nav-mode jump --target-per-side 0 --reverse --sym-sides-file SPREADSHEETS/V15_FULL_354.txt > ~/v15_mega_$VENUE.log 2>&1 < /dev/null &
    echo "[$(date -u +%FT%TZ)] driver restarted" >> "$LOG"
  fi
  bash tools/v15_mega_reap_stale.sh "$VENUE" 20 | grep -v "^reaped=0" >> "$LOG"  # hung pilots/orphan workers
  CORES=$(nproc)
  CPU=$(awk -v c="$CORES" '{printf "%d", $1 / c * 100}' /proc/loadavg)
  RAM=$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf "%d", (1 - a / t) * 100}' /proc/meminfo)
  P=$(cat "$CTRL")
  # pilots grow in RAM as sheets fill: scale up only with headroom, back off early (no OOM)
  if [ "$RAM" -gt 85 ] && [ "$P" -gt 2 ]; then
    P=$((P - 1))
  elif [ "$CPU" -lt 90 ] && [ "$RAM" -lt 78 ] && [ "$P" -lt "$(cat ~/.v15_mega_maxslots_$VENUE 2>/dev/null || echo 6)" ]; then
    P=$((P + 1))
  fi
  echo "$P" > "$CTRL"
  echo "[$(date -u +%FT%TZ)] cpu=${CPU}% ram=${RAM}% slots=$P" >> "$LOG"
  sleep 60
done

#!/bin/bash
# (Re)start the v15 mega sweep supervisor for one venue. Pilots keep running and are adopted by the new driver.
# Usage: start_v15_mega.sh <crypto|stocks> <python>
V=$1
PY=$2
for p in $(pgrep -f "v15_mega_supervisor.sh $V"); do
  [ "$p" != "$$" ] && [ "$p" != "$PPID" ] && kill "$p"
done
for p in $(pgrep -f "tools/v15_mega_sweep.py --venue $V"); do
  kill "$p"
done
sleep 2
[ -f ~/.v15_mega_parallel_$V ] || echo 4 > ~/.v15_mega_parallel_$V
cd ~/binance-sandbox || exit 1
setsid nohup bash tools/v15_mega_supervisor.sh "$V" "$PY" > /dev/null 2>&1 < /dev/null &
echo "started supervisor for $V"

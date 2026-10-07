#!/bin/bash
# v15_mac_harvest_loop — every INTERVAL sec: run the server-side harvester on
# s1 (crypto) + s2 (stocks), pull their ~/v15_mac_done into the Mac's permanent
# SPREADSHEETS/V15_MAC_DONE (no cron deletes this dir), and print a running count
# toward the 354 target. xlsx only — charts are a separate Mac-side pass.
set -u
ROOT="/Users/niels/Documents/binance"
DEST="$ROOT/SPREADSHEETS/V15_MAC_DONE"
LOG="$ROOT/SPREADSHEETS/V15_MAC_DONE/harvest_loop.log"
INTERVAL="${1:-300}"
mkdir -p "$DEST"
SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
while true; do
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  # s1 has ample RAM: harvest xlsx + charts. Crypto NPZ is local here.
  timeout 200 $SSH s1-pub 'cd ~/binance-sandbox && BASE_PATH=$HOME/binance-sandbox nice -n 19 .venv/bin/python -u tools/v15_harvest_done.py --charts --chart-cap 12 >/tmp/v15_harvest.log 2>&1; tail -1 /tmp/v15_harvest.log' >>"$LOG" 2>&1
  # s2 is RAM-tight: always harvest xlsx; only add charts when >1200M free (never OOM a herd pilot).
  timeout 200 $SSH s2 'cd ~/binance-sandbox && AV=$(free -m | awk "/Mem:/{print \$7}"); if [ "$AV" -gt 1200 ]; then FLAGS="--charts --chart-cap 6"; else FLAGS=""; fi; BASE_PATH=$HOME/binance-sandbox nice -n 19 .venv/bin/python -u tools/v15_harvest_done.py $FLAGS >/tmp/v15_harvest.log 2>&1; echo "avail=${AV}M charts=[$FLAGS]"; tail -1 /tmp/v15_harvest.log' >>"$LOG" 2>&1
  timeout 150 rsync -az -e "$SSH" s1-pub:~/v15_mac_done/ "$DEST/" >>"$LOG" 2>&1
  timeout 150 rsync -az -e "$SSH" s2:~/v15_mac_done/     "$DEST/" >>"$LOG" 2>&1
  timeout 200 $SSH s5 'cd ~/binance-sandbox && BASE_PATH=$HOME/binance-sandbox nice -n 19 .venv/bin/python -u tools/v15_harvest_done.py >/tmp/v15_harvest.log 2>&1; tail -1 /tmp/v15_harvest.log' >>"$LOG" 2>&1
  timeout 150 rsync -az -e "$SSH" s5:~/v15_mac_done/     "$DEST/" >>"$LOG" 2>&1
  N=$(ls "$DEST"/*_30d_matrix.xlsx 2>/dev/null | wc -l | tr -d ' ')
  C=$(ls "$DEST"/*_30d_zoom.html 2>/dev/null | wc -l | tr -d ' ')
  echo "[$TS] harvested xlsx=$N/354 charts=$C" >>"$LOG"
  echo "[$TS] harvested xlsx=$N/354 charts=$C"
  sleep "$INTERVAL"
done

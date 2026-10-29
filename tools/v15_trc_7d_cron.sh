#!/bin/bash
# trc 7D schedule + watchdog (S5, cron every minute). USER 2026-10-29:
#   20:00 ET Sun-Thu  bottom half of the tradeable stock ranking, slowly overnight, all 30D tests keep running
#   08:50 ET Mon-Fri  top half (best-first): 30D paused to disk; 09:00 ET lower part, 09:15 ET best part (08:45/09:00/09:15 temp bars)
# A pass whose state exists but is not complete and has no process is restarted from its state (it resumes the paused fleet).
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
SB=/home/niels/binance-sandbox
cd "$SB" || exit 2
[ -e "$SB/data/preopen_7d/TRC7D_SCHEDULE_OFF" ] && exit 0
HM=$(TZ=America/New_York date +%H%M)
DOW=$(TZ=America/New_York date +%u)
TODAY=$(TZ=America/New_York date +%Y%m%d)
start() {  # $1 day  $2 mode  $3 half  $4 dir
  pgrep -f "^$PY -u tools/v15_trc_7d_run.py .*--day $1( |$)" >/dev/null && return
  [ -s "data/preopen_7d/trc_run/$4/state.json" ] && grep -q '"complete_utc": "' "data/preopen_7d/trc_run/$4/state.json" && return
  setsid -f $PY -u tools/v15_trc_7d_run.py --mode "$2" --half "$3" --day "$1" >> /home/niels/logs/trc7d_run.log 2>&1 < /dev/null
  echo "[$(date -u +%FT%TZ)] started $2/$3 day=$1"
}
if [[ "$HM" > "1959" && "$HM" < "2005" ]] && [[ "$DOW" = 7 || "$DOW" -le 4 ]]; then
  NEXT=$(TZ=America/New_York date -d tomorrow +%Y%m%d)
  start "$NEXT" overnight bottom "${NEXT}_overnight"
fi
if [[ "$HM" > "0849" && "$HM" < "0855" ]] && [ "$DOW" -le 5 ]; then
  start "$TODAY" premarket top "$TODAY"
fi
# watchdog: restart any unfinished pass of today / tonight from its own state
for f in data/preopen_7d/trc_run/*/state.json; do
  [ -s "$f" ] || continue
  [ $(( $(date +%s) - $(stat -c %Y "$f") )) -lt 86400 ] || continue
  grep -q '"complete_utc": "' "$f" && continue
  read -r day mode half < <($PY -c 'import json,sys;s=json.load(open(sys.argv[1]));print(s.get("trade_day") or s["day"], s["mode"], s.get("half","all"))' "$f")
  pgrep -f "^$PY -u tools/v15_trc_7d_run.py .*--day $day( |$)" >/dev/null && continue
  pgrep -f "^$PY -u tools/v15_trc_7d_run.py$" >/dev/null && [ "$mode" = intraday ] && continue
  start "$day" "$mode" "$half" "$(basename "$(dirname "$f")")"
done

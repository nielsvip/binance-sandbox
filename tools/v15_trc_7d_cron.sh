#!/bin/bash
# trc 7D schedule + watchdog (S5, cron every minute). USER 2026-10-29, UTC only (USER 2026-10-30: "UTC ONLY ALWAYS EVERYWHERE"):
#   overnight  at today's UTC session close (20:00Z, 21:00Z when New York is on winter time): bottom half of the tradeable stock
#              ranking plus every premarket-thin side (USER 2026-10-30: no premarket trades -> "calculated after 20:00utc they have all
#              night"), best-first, slowly, all 30D tests keep running
#   premarket  40 min before today's UTC open: top half (best-first); 30D paused to disk; lower part from open-30 min, best part
#              from open-15 min (temporary overlay of the three 15m bars ending at the open)
# Every time is an epoch from the exchange calendar (tools/npz_cutoffs.stock_session_bounds, UTC). A pass whose state exists but is
# not complete and has no process is restarted from its state (it resumes the paused fleet).
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
SB=/home/niels/binance-sandbox
cd "$SB" || exit 2
[ -e "$SB/data/preopen_7d/TRC7D_SCHEDULE_OFF" ] && exit 0
NOW=$(date -u +%s)
read -r TODAY TODAY_OPEN TODAY_CLOSE NEXT NEXT_OPEN < <($PY - <<'PY'
import datetime as d, sys, time
sys.path.insert(0, ".")
from tools.npz_cutoffs import stock_session_bounds
now = time.time()
t = d.datetime.fromtimestamp(now, d.timezone.utc).date()
b = stock_session_bounds(t)
n = t
while True:
    nb = stock_session_bounds(n)
    if nb and nb[0] > now:
        break
    n += d.timedelta(days=1)
print(t.strftime("%Y%m%d"), b[0] if b else 0, b[1] if b else 0, n.strftime("%Y%m%d"), nb[0])
PY
)
start() {  # $1 day  $2 mode  $3 half  $4 dir
  pgrep -f "^$PY -u tools/v15_trc_7d_run.py .*--day $1( |$)" >/dev/null && return
  [ -s "data/preopen_7d/trc_run/$4/state.json" ] && grep -q '"complete_utc": "' "data/preopen_7d/trc_run/$4/state.json" && return
  setsid -f $PY -u tools/v15_trc_7d_run.py --mode "$2" --half "$3" --day "$1" >> /home/niels/logs/trc7d_run.log 2>&1 < /dev/null
  echo "[$(date -u +%FT%TZ)] started $2/$3 day=$1"
}
if [ "${TODAY_CLOSE:-0}" -gt 0 ] && [ "$NOW" -ge "$TODAY_CLOSE" ] && [ "$NOW" -lt $((TODAY_CLOSE + 300)) ]; then
  start "$NEXT" overnight bottom "${NEXT}_overnight"
fi
if [ "${TODAY_OPEN:-0}" -gt 0 ] && [ "$NOW" -ge $((TODAY_OPEN - 2400)) ] && [ "$NOW" -lt $((TODAY_OPEN - 2100)) ]; then
  start "$TODAY" premarket top "${TODAY}_premarket"
fi
# watchdog: restart any unfinished pass of today / tonight from its own state
for f in data/preopen_7d/trc_run/*/state.json; do
  [ -s "$f" ] || continue
  [ $(( NOW - $(stat -c %Y "$f") )) -lt 86400 ] || continue
  grep -q '"complete_utc": "' "$f" && continue
  read -r day mode half < <($PY -c 'import json,sys;s=json.load(open(sys.argv[1]));print(s.get("trade_day") or s["day"], s["mode"], s.get("half","all"))' "$f")
  pgrep -f "^$PY -u tools/v15_trc_7d_run.py .*--day $day( |$)" >/dev/null && continue
  pgrep -f "^$PY -u tools/v15_trc_7d_run.py$" >/dev/null && [ "$mode" = intraday ] && continue
  start "$day" "$mode" "$half" "$(basename "$(dirname "$f")")"
done

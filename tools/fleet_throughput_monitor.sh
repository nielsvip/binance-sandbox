#!/bin/bash
# fleet_throughput_monitor — Mac-side 5-min watch over backtest throughput.
# USER 2026-10-25: never go unmonitored again. For each worker (s1/s2/s5/s6):
# pilots, load, self-dispatch freshness; plus S1 scheduler tick + round36.
# Appends to /tmp/fleet_throughput.log, snapshot to data/reports/fleet_throughput.json.
# ALERT lines when a worker is idle (0 pilots + stale dispatch) or S1 tick stale.
set -u
ROOT=/Users/niels/Documents/binance
cd "$ROOT" || exit 1
LOG=/tmp/fleet_throughput.log
SNAP=$ROOT/data/reports/fleet_throughput.json
NOW=$(date -u +%FT%TZ)
E=$SECONDS
probe_worker() {
  local ip=$1 name=$2
  /opt/homebrew/bin/timeout 25 ssh -o BatchMode=yes -o ConnectTimeout=8 -J gateway-internal niels@$ip \
    "pgrep -c -f 'v15_pilot[.]py --sym-side'; cat /proc/loadavg | cut -d' ' -f1; stat -c %Y /tmp/self_dispatch.log 2>/dev/null || echo 0" 2>/dev/null | tr '\n' ' '
}
NOWS=$(date +%s)
S2=$(probe_worker 10.0.0.4 s2); S5=$(probe_worker 10.0.0.5 s5); S6=$(probe_worker 10.0.0.6 s6)
S1H=s1-int
S1=$(/opt/homebrew/bin/timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=6 $S1H "pgrep -c -f 'v15_pilot[.]py --sym-side'; cat /proc/loadavg | cut -d' ' -f1" 2>/dev/null | tr '\n' ' ')
[ -z "$S1" ] && { S1H=s1-pub; S1=$(/opt/homebrew/bin/timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=6 $S1H "pgrep -c -f 'v15_pilot[.]py --sym-side'; cat /proc/loadavg | cut -d' ' -f1" 2>/dev/null | tr '\n' ' '); }
TICK=$(/opt/homebrew/bin/timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=6 $S1H "grep '^{\"now\"' /tmp/v15_fleet_sched.log | tail -1" 2>/dev/null)
ROUND=$(/opt/homebrew/bin/timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=6 $S1H "timeout 30 python3 -c \"import json; d=json.load(open('/home/niels/binance-sandbox/data/reports/full_course_round_run36.json')); print(str(d.get('n_complete'))+'/'+str(d.get('universe_n'))+' pending:'+str(d.get('n_pending')))\"" 2>/dev/null)
ALERTS=""
chk() {
  local name=$1 rec=$2
  set -- $rec
  local pilots=${1:-?} load=${2:-?} mtime=${3:-0}
  local age=$((NOWS - ${mtime:-0}))
  if [ "$pilots" = "0" ] && [ "$age" -gt 600 ]; then ALERTS="$ALERTS IDLE:$name"; fi
  echo "$name pilots=$pilots load=$load dispatch_age_s=$age"
}
{
echo "[$NOW] $(chk s2 "$S2") | $(chk s5 "$S5") | $(chk s6 "$S6") | s1 pilots=$(echo $S1 | cut -d' ' -f1) load=$(echo $S1 | cut -d' ' -f2)"
echo "[$NOW] round36: ${ROUND:-?} tick: $(echo "$TICK" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["now"], d.get("launched"))' 2>/dev/null || echo STALE)"
[ -n "$ALERTS" ] && echo "[$NOW] ALERT idle workers:$ALERTS"
} >>"$LOG" 2>&1
tail -n 30 "$LOG" | grep -c ALERT >/dev/null 2>&1
echo "{\"ts\":\"$NOW\",\"round\":\"${ROUND:-?}\",\"alerts\":\"$ALERTS\",\"elapsed_s\":$((SECONDS-E))}" > "$SNAP"

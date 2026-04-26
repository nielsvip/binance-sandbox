#!/bin/bash
# Market-hours wrapper for the options shadow runner.
# launchd invokes this every 5min. Exits silent outside Mon-Fri 13:30-20:00 UTC.
#
# Manual test:
#   ./tradier_options_shadow_runner_market.sh
#   FORCE_RUN=1 ./tradier_options_shadow_runner_market.sh
#   FAKE_HOUR=14 ./tradier_options_shadow_runner_market.sh

set -u
BASE="/Users/niels/Documents/binance"
PY="/opt/anaconda3/envs/binance_env/bin/python"
LOG_DIR="$BASE/data/options_supervisor"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/shadow_runner_$(date -u +%Y%m%d).log"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

DOW=$(date -u +%u)
HOUR=${FAKE_HOUR:-$(date -u +%H)}
MINUTE=$(date -u +%M)
HOUR=$((10#$HOUR))
MINUTE=$((10#$MINUTE))

if [[ "${FORCE_RUN:-0}" != "1" ]]; then
    if (( DOW > 5 )); then exit 0; fi
    in_window=0
    if (( HOUR > 13 && HOUR < 20 )); then in_window=1; fi
    if (( HOUR == 13 && MINUTE >= 30 )); then in_window=1; fi
    (( in_window == 0 )) && exit 0
fi

cd "$BASE" || exit 1
echo "[$TS] shadow tick start" >> "$LOG"
"$PY" tradier_options_shadow_runner.py >> "$LOG" 2>&1
RC=$?
echo "[$TS] shadow tick done rc=$RC" >> "$LOG"
exit 0

#!/bin/bash
# Market-hours wrapper for the options state + recommendations refresh.
# Invoked by launchd every 60s. Exits silently outside Mon-Fri 13:30-20:00 UTC.
#
# Install via:
#   cp com.niels.options-supervisor.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.niels.options-supervisor.plist
#
# Manual test:
#   ./tradier_options_market_runner.sh                # respects market hours
#   FORCE_RUN=1 ./tradier_options_market_runner.sh    # bypass time check (dev)
#   FAKE_HOUR=14 ./tradier_options_market_runner.sh   # pretend it's 14 UTC

set -u
BASE="/Users/niels/Documents/binance"
PY="/opt/anaconda3/envs/binance_env/bin/python"
LOG_DIR="$BASE/data/options_supervisor"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/runner_$(date -u +%Y%m%d).log"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Time gate: Mon-Fri 13:30-20:00 UTC unless FORCE_RUN=1.
DOW=$(date -u +%u)               # 1=Mon..7=Sun
HOUR=${FAKE_HOUR:-$(date -u +%H)}
MINUTE=$(date -u +%M)
HOUR=$((10#$HOUR))
MINUTE=$((10#$MINUTE))

if [[ "${FORCE_RUN:-0}" != "1" ]]; then
    if (( DOW > 5 )); then
        # Weekend — silent exit.
        exit 0
    fi
    # Inside [13:30, 20:00) UTC?
    in_window=0
    if (( HOUR > 13 && HOUR < 20 )); then in_window=1; fi
    if (( HOUR == 13 && MINUTE >= 30 )); then in_window=1; fi
    if (( in_window == 0 )); then
        exit 0
    fi
fi

cd "$BASE" || exit 1
echo "[$TS] tick start" >> "$LOG"
"$PY" tradier_options_state.py --quiet >> "$LOG" 2>&1
RC1=$?
"$PY" tradier_options_recommendations.py --json-only >> "$LOG" 2>&1
RC2=$?
echo "[$TS] tick done state=$RC1 recs=$RC2" >> "$LOG"
exit 0

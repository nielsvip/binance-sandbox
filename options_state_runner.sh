#!/bin/bash
# Options-state snapshot runner — runs during market hours (13:25-20:15 UTC weekdays).
# Mirrors the options_watchdog_runner.sh pattern.
# launchd KeepAlive restarts on crash; exit 1 outside hours so launchd retries later.

HOUR=$(date -u +%H)
MIN=$(date -u +%M)
DOW=$(date -u +%u)  # 1=Mon, 7=Sun

if [ "$DOW" -gt 5 ]; then
    echo "Weekend — will retry later"
    exit 1
fi

MINS=$(( 10#$HOUR * 60 + 10#$MIN ))
if [ "$MINS" -lt 805 ] || [ "$MINS" -gt 1215 ]; then
    echo "Outside market hours (${HOUR}:${MIN} UTC) — will retry later"
    exit 1
fi

cd /Users/niels/Documents/binance
exec /opt/anaconda3/envs/binance_env/bin/python tradier_options_state.py --account all --loop 30 --quiet

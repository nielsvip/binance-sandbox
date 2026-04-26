#!/bin/bash
# Options-shadow paper-variants runner — runs during market hours (13:25-20:15 UTC weekdays).
# Cycles every 5 minutes. launchd KeepAlive restarts on crash.

HOUR=$(date -u +%H)
MIN=$(date -u +%M)
DOW=$(date -u +%u)

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
exec /opt/anaconda3/envs/binance_env/bin/python tradier_options_shadow_runner.py --loop 300

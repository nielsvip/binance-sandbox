#!/bin/bash
# Emergency-brake runner — independent process polling Tradier API every 60s
# during market hours. Restart-on-crash via launchd KeepAlive. Exits on weekend
# or outside market hours so launchd retries via ThrottleInterval.

HOUR=$(date -u +%H)
MIN=$(date -u +%M)
DOW=$(date -u +%u)

if [ "$DOW" -gt 5 ]; then
    echo "Weekend — will retry later"
    exit 1
fi

# Run from 13:25 UTC (5m pre-open) to 20:15 UTC (15m past close).
# The brake itself skips on MARKET_CLOSED, but having it spin up early/late
# helps catch tier breaches at the boundary.
MINS=$(( 10#$HOUR * 60 + 10#$MIN ))
if [ "$MINS" -lt 805 ] || [ "$MINS" -gt 1215 ]; then
    echo "Outside market hours (${HOUR}:${MIN} UTC) — will retry later"
    exit 1
fi

cd /Users/niels/Documents/binance
exec /opt/anaconda3/envs/binance_env/bin/python tradier_emergency_brake.py --loop 60

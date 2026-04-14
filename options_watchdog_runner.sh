#!/bin/bash
# Options watchdog runner — runs during market hours (13:30-20:00 UTC), exits after close.
# KeepAlive in launchd restarts it if it crashes during market hours.

HOUR=$(date -u +%H)
MIN=$(date -u +%M)
DOW=$(date -u +%u)  # 1=Mon, 7=Sun

# Only run on weekdays (Mon-Fri)
if [ "$DOW" -gt 5 ]; then
    echo "Weekend — sleeping until Monday"
    exit 0
fi

# Only run during market hours + buffer (13:25 - 20:15 UTC)
MINS=$((HOUR * 60 + MIN))
if [ "$MINS" -lt 805 ] || [ "$MINS" -gt 1215 ]; then
    echo "Outside market hours (${HOUR}:${MIN} UTC) — exiting"
    exit 0
fi

cd /Users/niels/Documents/binance
exec /opt/anaconda3/envs/binance_env/bin/python tradier_options_analyzer.py watch --daemon --auto-sell --interval 300

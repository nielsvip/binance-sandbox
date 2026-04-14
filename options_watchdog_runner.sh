#!/bin/bash
# Options watchdog runner — runs during market hours (13:30-20:00 UTC), exits after close.
# KeepAlive in launchd restarts it if it crashes during market hours.
# FIX 2026-04-14: use 10# prefix for decimal arithmetic (avoids octal parse error on 08/09).
# FIX 2026-04-14: exit 1 outside market hours/weekends so launchd always restarts (was exit 0
#   which told launchd "success, don't restart" → daemon never relaunched after market close).

HOUR=$(date -u +%H)
MIN=$(date -u +%M)
DOW=$(date -u +%u)  # 1=Mon, 7=Sun

# Only run on weekdays (Mon-Fri) — exit 1 so launchd retries next ThrottleInterval
if [ "$DOW" -gt 5 ]; then
    echo "Weekend — will retry later"
    exit 1
fi

# Only run during market hours + buffer (13:25 - 20:15 UTC)
# Use 10# prefix to force decimal interpretation (prevents octal parse error on 08/09)
MINS=$(( 10#$HOUR * 60 + 10#$MIN ))
if [ "$MINS" -lt 805 ] || [ "$MINS" -gt 1215 ]; then
    echo "Outside market hours (${HOUR}:${MIN} UTC) — will retry later"
    exit 1
fi

cd /Users/niels/Documents/binance
exec /opt/anaconda3/envs/binance_env/bin/python tradier_options_analyzer.py watch --daemon --auto-sell --interval 300

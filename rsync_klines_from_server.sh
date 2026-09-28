#!/bin/bash
# Continuous rsync of klines_cache from GATEWAY to local macbook for trading
# Runs every 120 seconds in a loop
# NOTE: S1 is accumulation source (1+ year history), Gateway is backup for Mac trading (1200-1800 bars)

LOG="/Users/niels/logs/klines_sync_from_gateway.log"
SRC="gateway-internal:/home/niels/binance/klines_cache/"
DST="/Users/niels/Documents/binance/klines_cache/"

mkdir -p "$DST"
mkdir -p "$(dirname "$LOG")"

echo "$(date '+%Y-%m-%d %H:%M:%S') — klines sync from server started (pid $$)" >> "$LOG"

while true; do
    rsync -a --update --timeout=110 "$SRC" "$DST" >> "$LOG" 2>&1
    EXIT_CODE=$?
    if [ $EXIT_CODE -ne 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') — rsync failed (exit $EXIT_CODE)" >> "$LOG"
    fi
    # Truncate log if > 10MB
    LOG_SIZE=$(stat -f%z "$LOG" 2>/dev/null || echo 0)
    if [ "$LOG_SIZE" -gt 10485760 ]; then
        tail -n 1000 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
    fi
    sleep 120
done

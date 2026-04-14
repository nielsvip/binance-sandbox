#!/bin/bash
# Continuous rsync from gateway (157.90.168.35) klines_cache to server klines_cache_gateway (staging)
# Runs every 60 seconds in a loop

LOG="/home/niels/logs/klines_sync_continuous.log"
SRC="gateway-internal:/home/niels/binance/klines_cache/"
DST="/home/niels/binance/klines_cache_gateway/"

mkdir -p "$DST"

echo "$(date '+%Y-%m-%d %H:%M:%S') — klines continuous sync started (pid $$)" >> "$LOG"

while true; do
    START=$(date +%s)
    rsync -a --update --timeout=55 "$SRC" "$DST" >> "$LOG" 2>&1
    EXIT_CODE=$?
    END=$(date +%s)
    ELAPSED=$((END - START))
    if [ $EXIT_CODE -ne 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') — rsync failed (exit $EXIT_CODE) after ${ELAPSED}s" >> "$LOG"
    fi
    # Truncate log if > 10MB
    LOG_SIZE=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
    if [ "$LOG_SIZE" -gt 10485760 ]; then
        tail -n 1000 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
    fi
    sleep 60
done

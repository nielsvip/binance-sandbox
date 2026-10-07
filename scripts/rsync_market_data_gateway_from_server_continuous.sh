#!/bin/bash
# Continuously sync market_data files from server to gateway
SERVER_HOST="s1-int"
SERVER_SOURCE="/home/niels/binance/data/"
GATEWAY_TARGET="/home/niels/binance/data/"
LOG_FILE="$HOME/logs/rsync_market_data_gateway_from_server.log"
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$GATEWAY_TARGET"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous rsync market_data from server to gateway..." | tee -a "$LOG_FILE"
while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📥 Syncing market_data from server to gateway..." >> "$LOG_FILE"
    rsync -avz --update --timeout=30 --include="market_data_*.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "${SERVER_HOST}:${SERVER_SOURCE}" "$GATEWAY_TARGET" >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        FILE_COUNT=$(find "$GATEWAY_TARGET" -name "market_data_*.json" -type f 2>/dev/null | wc -l | tr -d ' ')
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files" >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ❌ Rsync failed!" >> "$LOG_FILE"
    fi
    sleep 60
done




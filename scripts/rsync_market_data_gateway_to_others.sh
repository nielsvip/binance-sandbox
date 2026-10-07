#!/bin/bash
# Gateway rsync script - syncs gateway market_data files to macbook and server
MACBOOK_HOST="niels@192.168.1.100"
SERVER_HOST="s1-int"
GATEWAY_BASE="/home/niels/binance/data/"
MACBOOK_BASE="/Users/niels/Documents/binance/data/"
SERVER_BASE="/home/niels/binance/data/"
LOG_FILE="$HOME/logs/rsync_market_data_gateway_to_others.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting gateway -> macbook/server market_data rsync..." | tee -a "$LOG_FILE"
while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing gateway market_data files..." >> "$LOG_FILE"
    rsync -avz --update --timeout=30 --include="market_data_*.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "${GATEWAY_BASE}" "${MACBOOK_HOST}:${MACBOOK_BASE}" >> "$LOG_FILE" 2>&1
    MACBOOK_STATUS=$?
    rsync -avz --update --timeout=30 --include="market_data_*.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "${GATEWAY_BASE}" "${SERVER_HOST}:${SERVER_BASE}" >> "$LOG_FILE" 2>&1
    SERVER_STATUS=$?
    FILE_COUNT=$(find "${GATEWAY_BASE}" -name "market_data_*.json" -type f 2>/dev/null | wc -l | tr -d ' ')
    if [ $MACBOOK_STATUS -eq 0 ] && [ $SERVER_STATUS -eq 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Gateway sync complete: $FILE_COUNT files synced" >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ⚠️ Gateway rsync partial failure (macbook=$MACBOOK_STATUS, server=$SERVER_STATUS)" >> "$LOG_FILE"
    fi
    sleep 60
done

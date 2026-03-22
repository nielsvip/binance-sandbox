#!/bin/bash
# Continuously sync market_data files from macbook to server
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
SERVER_HOST="niels@157.180.125.52"
MACBOOK_SOURCE="/Users/niels/Documents/binance/data/"
SERVER_TARGET="/home/niels/binance/data/"
LOG_FILE="$HOME/logs/rsync_market_data_to_server.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous rsync market_data to server..." | tee -a "$LOG_FILE"
while true; do
    check_local_size "$MACBOOK_SOURCE"
    if ! check_remote_size "$SERVER_HOST" "$SERVER_TARGET"; then sleep 60; continue; fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing market_data to server..." >> "$LOG_FILE"
    rsync -avz --timeout=30 --include="*/" --include="market_data_*.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" --max-size=100M "$MACBOOK_SOURCE" "${SERVER_HOST}:${SERVER_TARGET}" >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        FILE_COUNT=$(find "$MACBOOK_SOURCE" -name "market_data_*.json" -type f | wc -l | tr -d ' ')
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files pushed" >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ❌ Rsync failed!" >> "$LOG_FILE"
    fi
    sleep 60
done




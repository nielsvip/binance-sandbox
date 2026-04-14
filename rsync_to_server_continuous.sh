#!/bin/bash
# Continuously sync klines from macbook to server
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
SERVER_HOST="s1-int"
MACBOOK_SOURCE="/Users/niels/Documents/binance/klines_cache/"
SERVER_TARGET="/home/niels/binance/klines_cache_macbook/"
LOG_FILE="$HOME/logs/rsync_to_server.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous rsync to server..." | tee -a "$LOG_FILE"
while true; do
    check_local_size "$MACBOOK_SOURCE"
    if ! check_remote_size "$SERVER_HOST" "$SERVER_TARGET"; then sleep 60; continue; fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing to server..." >> "$LOG_FILE"
    rsync -av --whole-file --delay-updates --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" --exclude=".rsync-partial*" --max-size=1G "$MACBOOK_SOURCE" "${SERVER_HOST}:${SERVER_TARGET}" >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        FILE_COUNT=$(find "$MACBOOK_SOURCE" -name "*.json" -type f | wc -l | tr -d ' ')
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files pushed" >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ❌ Rsync failed!" >> "$LOG_FILE"
    fi
    sleep 60
done


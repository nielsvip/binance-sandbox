#!/bin/bash
# Continuously sync market_data files from gateway to macbook
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
GATEWAY_HOST="gateway-internal"
GATEWAY_SOURCE="/home/niels/binance/data/"
MACBOOK_TARGET="/Users/niels/Documents/binance/data/"
LOG_FILE="$HOME/logs/rsync_market_data_from_gateway.log"
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$MACBOOK_TARGET"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous rsync market_data from gateway..." | tee -a "$LOG_FILE"
while true; do
    check_local_size "$MACBOOK_TARGET"
    if ! check_remote_size "$GATEWAY_HOST" "$GATEWAY_SOURCE"; then sleep 60; continue; fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📥 Syncing market_data from gateway..." >> "$LOG_FILE"
    rsync -avz --update --timeout=30 --include="market_data_*.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" --max-size=100M "${GATEWAY_HOST}:${GATEWAY_SOURCE}" "$MACBOOK_TARGET" >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        check_local_size "$MACBOOK_TARGET"
        FILE_COUNT=$(find "$MACBOOK_TARGET" -name "market_data_*.json" -type f | wc -l | tr -d ' ')
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files" >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ❌ Rsync failed!" >> "$LOG_FILE"
    fi
    sleep 60
done




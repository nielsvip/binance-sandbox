#!/bin/bash
# Continuously sync market_data files from server to macbook
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
SERVER_HOST="s1-int"
SERVER_SOURCE="/home/niels/binance/data/"
MACBOOK_TARGET="/Users/niels/Documents/binance/data/"
LOG_FILE="$HOME/logs/rsync_market_data_from_server.log"
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$MACBOOK_TARGET"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous rsync market_data from server..." | tee -a "$LOG_FILE"
while true; do
    check_local_size "$MACBOOK_TARGET"
    if ! check_remote_size "$SERVER_HOST" "$SERVER_SOURCE"; then sleep 60; continue; fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📥 Syncing market_data from server..." >> "$LOG_FILE"
    # Bidirectional fallback: pull latest_market_data + price_cache + klines if local is stale
    rsync -avz --timeout=30 --include="latest_market_data.json" --include="price_cache*.json" --include="market_data_*.json" --include="klines_cache/**" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" --max-size=100M "${SERVER_HOST}:${SERVER_SOURCE}" "$MACBOOK_TARGET" >> "$LOG_FILE" 2>&1
    rsync -avz --timeout=30 --exclude="*.tmp" --exclude="*.lock" --max-size=100M "${SERVER_HOST}:/home/niels/binance/klines_cache/" "/Users/niels/Documents/binance/klines_cache/" >> "$LOG_FILE" 2>&1
    rsync -avz --timeout=30 --exclude="*.tmp" --exclude="*.lock" --max-size=100M "${SERVER_HOST}:/home/niels/binance/price_cache*.json" "/Users/niels/Documents/binance/" >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        check_local_size "$MACBOOK_TARGET"
        FILE_COUNT=$(find "$MACBOOK_TARGET" -name "market_data_*.json" -o -name "latest_market_data.json" -o -name "price_cache*.json" | wc -l | tr -d ' ')
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files (latest+price_cache+klines fallback)" >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ❌ Rsync failed!" >> "$LOG_FILE"
    fi
    sleep 60
done




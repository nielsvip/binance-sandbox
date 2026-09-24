#!/bin/bash
# Continuously sync market_data files from macbook to server
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
SERVER_HOST="s1-int"
MACBOOK_SOURCE="/Users/niels/Documents/binance/data/"
SERVER_TARGET="/home/niels/binance/data/"
LOG_FILE="$HOME/logs/rsync_market_data_to_server.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous rsync market_data to server..." | tee -a "$LOG_FILE"
while true; do
    check_local_size "$MACBOOK_SOURCE"
    if ! check_remote_size "$SERVER_HOST" "$SERVER_TARGET"; then sleep 60; continue; fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing market_data to server..." >> "$LOG_FILE"
    # Sync latest_market_data + price_cache + market_data + klines (bidirectional fallback so lagging host gets filled in)
    rsync -avz --timeout=30 --include="*/" --include="latest_market_data.json" --include="price_cache*.json" --include="market_data_*.json" --include="klines_cache/**" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" --max-size=100M "$MACBOOK_SOURCE" "${SERVER_HOST}:${SERVER_TARGET}" >> "$LOG_FILE" 2>&1
    # Also sync klines_cache separately (different root)
    rsync -avz --timeout=30 --exclude="*.tmp" --exclude="*.lock" --max-size=100M "/Users/niels/Documents/binance/klines_cache/" "${SERVER_HOST}:/home/niels/binance/klines_cache/" >> "$LOG_FILE" 2>&1
    rsync -avz --timeout=30 --exclude="*.tmp" --exclude="*.lock" --max-size=100M "/Users/niels/Documents/binance/price_cache*.json" "${SERVER_HOST}:/home/niels/binance/" >> "$LOG_FILE" 2>&1
    # Push to gateway as well (gateway has no latest_market_data, so Mac fills it)
    rsync -avz --timeout=30 --exclude="*.tmp" --exclude="*.lock" --max-size=100M "/Users/niels/Documents/binance/data/latest_market_data.json" "gateway-internal:/home/niels/binance/data/latest_market_data.json" >> "$LOG_FILE" 2>&1
    rsync -avz --timeout=30 --exclude="*.tmp" --exclude="*.lock" --max-size=100M "/Users/niels/Documents/binance/price_cache*.json" "gateway-internal:/home/niels/binance/" >> "$LOG_FILE" 2>&1
    if [ $? -eq 0 ]; then
        FILE_COUNT=$(find "$MACBOOK_SOURCE" -name "market_data_*.json" -o -name "latest_market_data.json" -o -name "price_cache*.json" | wc -l | tr -d ' ')
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files pushed (latest+price_cache+klines)" >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ❌ Rsync failed!" >> "$LOG_FILE"
    fi
    sleep 60
done




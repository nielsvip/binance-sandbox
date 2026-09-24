#!/bin/bash
# Continuously sync klines from gateway to macbook
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
GATEWAY_HOST="gateway-internal"
GATEWAY_SOURCE="/home/niels/binance/klines_cache/"
MACBOOK_TARGET="/Users/niels/Documents/binance/klines_cache_gateway/"
LOG_FILE="$HOME/logs/rsync_from_gateway.log"
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$MACBOOK_TARGET"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous rsync from gateway..." | tee -a "$LOG_FILE"
while true; do
    check_local_size "$MACBOOK_TARGET"
    if ! check_remote_size "$GATEWAY_HOST" "$GATEWAY_SOURCE"; then sleep 60; continue; fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📥 Syncing from gateway..." >> "$LOG_FILE"
    rsync -av --whole-file --delay-updates --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" --exclude=".rsync-partial*" --max-size=1G "${GATEWAY_HOST}:${GATEWAY_SOURCE}" "$MACBOOK_TARGET" >> "$LOG_FILE" 2>&1
    # Also sync mark prices + latest_market_data from gateway (bidirectional fallback)
    rsync -av --whole-file --delay-updates --timeout=30 --exclude="*.tmp" --exclude="*.lock" --max-size=100M "${GATEWAY_HOST}:/home/niels/binance/price_cache*.json" "/Users/niels/Documents/binance/" >> "$LOG_FILE" 2>&1
    rsync -av --whole-file --delay-updates --timeout=30 --exclude="*.tmp" --exclude="*.lock" --max-size=100M "${GATEWAY_HOST}:/home/niels/binance/data/latest_market_data.json" "/Users/niels/Documents/binance/data/latest_market_data.json.gateway" >> "$LOG_FILE" 2>&1
    # If gateway has fresher latest_market_data, use it as fallback for ez_indicators
    if [ -f "/Users/niels/Documents/binance/data/latest_market_data.json.gateway" ]; then
        GW_AGE=$(stat -f %m "/Users/niels/Documents/binance/data/latest_market_data.json.gateway" 2>/dev/null || stat -c %Y "/Users/niels/Documents/binance/data/latest_market_data.json.gateway" 2>/dev/null || echo 0)
        LOCAL_AGE=$(stat -f %m "/Users/niels/Documents/binance/data/latest_market_data.json" 2>/dev/null || stat -c %Y "/Users/niels/Documents/binance/data/latest_market_data.json" 2>/dev/null || echo 0)
        if [ "$GW_AGE" -gt "$LOCAL_AGE" ]; then
            cp "/Users/niels/Documents/binance/data/latest_market_data.json.gateway" "/Users/niels/Documents/binance/data/latest_market_data.json"
            echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Gateway latest_market_data fresher, copied to local" >> "$LOG_FILE"
        fi
    fi
    if [ $? -eq 0 ]; then
        check_local_size "$MACBOOK_TARGET"
        FILE_COUNT=$(find "$MACBOOK_TARGET" -name "*.json" -type f | wc -l | tr -d ' ')
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT klines + marks from gateway" >> "$LOG_FILE"
        /opt/anaconda3/envs/binance_env/bin/python3 /Users/niels/Documents/binance/validate_klines_cache.py >> "$LOG_FILE" 2>&1
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ❌ Rsync failed!" >> "$LOG_FILE"
    fi
    sleep 60
done


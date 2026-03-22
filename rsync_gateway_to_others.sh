#!/bin/bash
# Gateway rsync script - syncs gateway _active files to macbook and server
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
MACBOOK_HOST="niels@192.168.1.100"
SERVER_HOST="niels@157.180.125.52"
GATEWAY_BASE="/home/niels/binance/"
MACBOOK_BASE="/Users/niels/Documents/binance/"
SERVER_BASE="/home/niels/binance/"
LOG_FILE="$HOME/logs/rsync_gateway_to_others.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting gateway -> macbook/server rsync..." | tee -a "$LOG_FILE"
while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing gateway _active files..." >> "$LOG_FILE"
    for account in "${ACCOUNT_FOLDERS[@]}"; do
        ssh -o ConnectTimeout=5 "$MACBOOK_HOST" "mkdir -p ${MACBOOK_BASE}${account}/gateway" 2>/dev/null
        ssh -o ConnectTimeout=5 "$SERVER_HOST" "mkdir -p ${SERVER_BASE}${account}/gateway" 2>/dev/null
        if check_remote_size "$MACBOOK_HOST" "${MACBOOK_BASE}${account}/gateway"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing ${account} to macbook..." >> "$LOG_FILE"
            rsync -avz --timeout=5 --include="*_positions_active.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" --max-size=10M "${GATEWAY_BASE}${account}/" "${MACBOOK_HOST}:${MACBOOK_BASE}${account}/gateway/" >> "$LOG_FILE" 2>&1
        fi
        if check_remote_size "$SERVER_HOST" "${SERVER_BASE}${account}/gateway"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing ${account} to server..." >> "$LOG_FILE"
            rsync -avz --timeout=5 --include="*_positions_active.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" --max-size=10M "${GATEWAY_BASE}${account}/" "${SERVER_HOST}:${SERVER_BASE}${account}/gateway/" >> "$LOG_FILE" 2>&1
        fi
    done
    LOCAL_COUNT=$(find "${GATEWAY_BASE}" -name "*_positions_active.json" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Gateway sync complete: $LOCAL_COUNT files synced" >> "$LOG_FILE"
    sleep 0.5
done


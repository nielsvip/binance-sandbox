#!/bin/bash
# Macbook rsync script - syncs macbook _active files to gateway and server
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
GATEWAY_HOST="gateway-internal"
SERVER_HOST="s1-int"
MACBOOK_BASE="/Users/niels/Documents/binance/"
GATEWAY_BASE="/home/niels/binance/"
SERVER_BASE="/home/niels/binance/"
LOG_FILE="$HOME/logs/rsync_macbook_to_others.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting macbook -> gateway/server rsync..." | tee -a "$LOG_FILE"
while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing macbook _active files..." >> "$LOG_FILE"
    for account in "${ACCOUNT_FOLDERS[@]}"; do
        ssh -o ConnectTimeout=5 "$GATEWAY_HOST" "mkdir -p ${GATEWAY_BASE}${account}/macbook" 2>/dev/null
        ssh -o ConnectTimeout=5 "$SERVER_HOST" "mkdir -p ${SERVER_BASE}${account}/macbook" 2>/dev/null
        if check_remote_size "$GATEWAY_HOST" "${GATEWAY_BASE}${account}/macbook"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing ${account} to gateway..." >> "$LOG_FILE"
            rsync -avz --update --timeout=5 --include="*_positions_active.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" --max-size=10M "${MACBOOK_BASE}${account}/" "${GATEWAY_HOST}:${GATEWAY_BASE}${account}/macbook/" >> "$LOG_FILE" 2>&1
        fi
        if check_remote_size "$SERVER_HOST" "${SERVER_BASE}${account}/macbook"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing ${account} to server..." >> "$LOG_FILE"
            rsync -avz --update --timeout=5 --include="*_positions_active.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" --max-size=10M "${MACBOOK_BASE}${account}/" "${SERVER_HOST}:${SERVER_BASE}${account}/macbook/" >> "$LOG_FILE" 2>&1
        fi
    done
    LOCAL_COUNT=$(find "${MACBOOK_BASE}" -name "*_positions_active.json" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Macbook sync complete: $LOCAL_COUNT files synced" >> "$LOG_FILE"
    sleep 0.5
done


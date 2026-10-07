#!/bin/bash
# Server rsync script - syncs server _active files to macbook and gateway
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
MACBOOK_HOST="niels@192.168.1.100"
GATEWAY_HOST="gateway-internal"
SERVER_BASE="/home/niels/binance/"
MACBOOK_BASE="/Users/niels/Documents/binance/"
GATEWAY_BASE="/home/niels/binance/"
LOG_FILE="$HOME/logs/rsync_server_to_others.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting server -> macbook/gateway rsync..." | tee -a "$LOG_FILE"
while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing server _active files..." >> "$LOG_FILE"
    for account in "${ACCOUNT_FOLDERS[@]}"; do
        ssh -o ConnectTimeout=5 "$MACBOOK_HOST" "mkdir -p ${MACBOOK_BASE}${account}/server" 2>/dev/null
        ssh -o ConnectTimeout=5 "$GATEWAY_HOST" "mkdir -p ${GATEWAY_BASE}${account}/server" 2>/dev/null
        if check_remote_size "$MACBOOK_HOST" "${MACBOOK_BASE}${account}/server"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing ${account} to macbook..." >> "$LOG_FILE"
            rsync -avz --update --timeout=5 --include="*_positions_active.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" --max-size=10M "${SERVER_BASE}${account}/" "${MACBOOK_HOST}:${MACBOOK_BASE}${account}/server/" >> "$LOG_FILE" 2>&1
        fi
        if check_remote_size "$GATEWAY_HOST" "${GATEWAY_BASE}${account}/server"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing ${account} to gateway..." >> "$LOG_FILE"
            rsync -avz --update --timeout=5 --include="*_positions_active.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" --max-size=10M "${SERVER_BASE}${account}/" "${GATEWAY_HOST}:${GATEWAY_BASE}${account}/server/" >> "$LOG_FILE" 2>&1
        fi
    done
    LOCAL_COUNT=$(find "${SERVER_BASE}" -name "*_positions_active.json" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Server sync complete: $LOCAL_COUNT files synced" >> "$LOG_FILE"
    sleep 0.5
done


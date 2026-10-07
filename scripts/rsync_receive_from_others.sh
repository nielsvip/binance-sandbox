#!/bin/bash
# Receive _active position files FROM gateway and server TO macbook
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rsync_helpers.sh"
GATEWAY_HOST="gateway-internal"
SERVER_HOST="s1-int"
MACBOOK_BASE="/Users/niels/Documents/binance/"
GATEWAY_BASE="/home/niels/binance/"
SERVER_BASE="/home/niels/binance/"
LOG_FILE="$HOME/logs/rsync_receive_from_others.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting gateway/server -> macbook rsync..." | tee -a "$LOG_FILE"
while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📥 Receiving _active files from gateway/server..." >> "$LOG_FILE"
    for account in "${ACCOUNT_FOLDERS[@]}"; do
        mkdir -p "${MACBOOK_BASE}${account}/gateway"
        mkdir -p "${MACBOOK_BASE}${account}/server"
        check_local_size "${MACBOOK_BASE}${account}/gateway"
        check_local_size "${MACBOOK_BASE}${account}/server"
        if check_remote_size "$GATEWAY_HOST" "${GATEWAY_BASE}${account}"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - 📥 Receiving ${account} from gateway..." >> "$LOG_FILE"
            rsync -avz --update --timeout=5 --include="*_positions_active.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" --exclude="macbook/" --max-size=10M "${GATEWAY_HOST}:${GATEWAY_BASE}${account}/" "${MACBOOK_BASE}${account}/gateway/" >> "$LOG_FILE" 2>&1
        fi
        if check_remote_size "$SERVER_HOST" "${SERVER_BASE}${account}"; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - 📥 Receiving ${account} from server..." >> "$LOG_FILE"
            rsync -avz --update --timeout=5 --include="*_positions_active.json" --exclude="*" --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" --exclude="macbook/" --max-size=10M "${SERVER_HOST}:${SERVER_BASE}${account}/" "${MACBOOK_BASE}${account}/server/" >> "$LOG_FILE" 2>&1
        fi
    done
    GATEWAY_COUNT=$(find "${MACBOOK_BASE}" -path "*/gateway/*_positions_active.json" -type f 2>/dev/null | wc -l | tr -d ' ')
    SERVER_COUNT=$(find "${MACBOOK_BASE}" -path "*/server/*_positions_active.json" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Receive complete: Gateway=$GATEWAY_COUNT, Server=$SERVER_COUNT files" >> "$LOG_FILE"
    sleep 0.5
done







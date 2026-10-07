#!/bin/bash
# Continuously sync logs from macbook to server
# This ensures that when the macbook is leading, logs are still viewable on the server

SERVER_HOST="s1-int"
MACBOOK_LOGS_DIR="$HOME/logs"
MACBOOK_BINANCE_LOGS_DIR="/Users/niels/Documents/binance/logs"
SERVER_LOGS_DIR="/home/niels/logs"
LOG_FILE="$HOME/logs/rsync_logs_to_server.log"

mkdir -p "$(dirname "$LOG_FILE")"

echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous log rsync to server..." | tee -a "$LOG_FILE"

while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing logs to server..." >> "$LOG_FILE"
    
    # 1. Sync ~/logs/ (realtime and quick logs)
    # Using --update to only overwrite if source is newer
    rsync -avz --update --timeout=30 \
        --include="ez_positions_realtime_*.log*" \
        --include="ez_positions_quick_*.log*" \
        --include="actions.log*" \
        --include="tradier_*.log*" \
        --exclude="*" \
        "$MACBOOK_LOGS_DIR/" "${SERVER_HOST}:${SERVER_LOGS_DIR}/" >> "$LOG_FILE" 2>&1
        
    # 2. Sync ~/Documents/binance/logs/ (ez_manage logs)
    rsync -avz --update --timeout=30 \
        --include="ez_manage_*.log*" \
        --exclude="*" \
        "$MACBOOK_BINANCE_LOGS_DIR/" "${SERVER_HOST}:${SERVER_LOGS_DIR}/" >> "$LOG_FILE" 2>&1

    if [ $? -eq 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Log sync complete" >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ❌ Log sync failed!" >> "$LOG_FILE"
    fi
    
    # Sync every 10 seconds for logs (less frequent than positions but still responsive)
    sleep 10
done

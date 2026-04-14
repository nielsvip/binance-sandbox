#!/bin/bash
# Continuously sync positions from macbook to server
SERVER_HOST="s1-int"
MACBOOK_BASE="/Users/niels/Documents/binance/"
SERVER_BASE="/home/niels/binance/"
LOG_FILE="$HOME/logs/rsync_positions_to_server.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
POSITION_SIDES=("LONG" "SHORT")
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous position rsync to server..." | tee -a "$LOG_FILE"
while true; do
echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing positions to server..." >> "$LOG_FILE"
for account in "${ACCOUNT_FOLDERS[@]}"; do
for side in "${POSITION_SIDES[@]}"; do
macbook_source="${MACBOOK_BASE}${account}/macbook/${side}_positions_active.json"
server_target="${SERVER_HOST}:${SERVER_BASE}${account}/server/${side}_positions_active.json"
if [ -f "$macbook_source" ]; then
rsync -avz --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" "$macbook_source" "$server_target" >> "$LOG_FILE" 2>&1
fi
macbook_source_updated="${MACBOOK_BASE}${account}/${side}_positions.updated.json"
server_target_updated="${SERVER_HOST}:${SERVER_BASE}${account}/${side}_positions.updated.json"
if [ -f "$macbook_source_updated" ]; then
rsync -avz --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" "$macbook_source_updated" "$server_target_updated" >> "$LOG_FILE" 2>&1
fi
macbook_source_main="${MACBOOK_BASE}${account}/${side}_positions.json"
server_target_main="${SERVER_HOST}:${SERVER_BASE}${account}/${side}_positions.json"
if [ -f "$macbook_source_main" ]; then
rsync -avz --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" "$macbook_source_main" "$server_target_main" >> "$LOG_FILE" 2>&1
fi
done
done
FILE_COUNT=$(find "${MACBOOK_BASE}" -path "*/macbook/*_positions_active.json" -o -path "*/*_positions.updated.json" -o -path "*/*_positions.json" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files pushed" >> "$LOG_FILE"
sleep 60
done
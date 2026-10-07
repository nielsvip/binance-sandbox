#!/bin/bash
# Continuously sync positions from gateway to server (runs on gateway)
SERVER_HOST="s1-int"
GATEWAY_BASE="/home/niels/binance/"
SERVER_BASE="/home/niels/binance/"
LOG_FILE="$HOME/logs/rsync_positions_gateway_to_server.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
POSITION_SIDES=("LONG" "SHORT")
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous position rsync gateway to server..." | tee -a "$LOG_FILE"
while true; do
echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing positions from gateway to server..." >> "$LOG_FILE"
for account in "${ACCOUNT_FOLDERS[@]}"; do
for side in "${POSITION_SIDES[@]}"; do
gateway_source="${GATEWAY_BASE}${account}/gateway/${side}_positions_active.json"
server_target="${SERVER_HOST}:${SERVER_BASE}${account}/gateway/${side}_positions_active.json"
if [ -f "$gateway_source" ]; then
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$gateway_source" "$server_target" >> "$LOG_FILE" 2>&1
fi
gateway_source_updated="${GATEWAY_BASE}${account}/${side}_positions.updated.json"
server_target_updated="${SERVER_HOST}:${SERVER_BASE}${account}/${side}_positions.updated.json"
if [ -f "$gateway_source_updated" ]; then
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$gateway_source_updated" "$server_target_updated" >> "$LOG_FILE" 2>&1
fi
gateway_source_main="${GATEWAY_BASE}${account}/${side}_positions.json"
server_target_main="${SERVER_HOST}:${SERVER_BASE}${account}/${side}_positions.json"
if [ -f "$gateway_source_main" ]; then
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$gateway_source_main" "$server_target_main" >> "$LOG_FILE" 2>&1
fi
done
done
FILE_COUNT=$(find "${GATEWAY_BASE}" -path "*/gateway/*_positions_active.json" -o -path "*/*_positions.updated.json" -o -path "*/*_positions.json" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files pushed" >> "$LOG_FILE"
sleep 60
done

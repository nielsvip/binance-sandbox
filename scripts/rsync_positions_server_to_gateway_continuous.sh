#!/bin/bash
# Continuously sync positions from server to gateway (runs on server)
GATEWAY_HOST="niels@10.0.0.2"
SERVER_BASE="/home/niels/binance/"
GATEWAY_BASE="/home/niels/binance/"
LOG_FILE="$HOME/logs/rsync_positions_server_to_gateway.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
POSITION_SIDES=("LONG" "SHORT")
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous position rsync server to gateway..." | tee -a "$LOG_FILE"
while true; do
echo "$(date '+%Y-%m-%d %H:%M:%S') - 📤 Syncing positions from server to gateway..." >> "$LOG_FILE"
for account in "${ACCOUNT_FOLDERS[@]}"; do
for side in "${POSITION_SIDES[@]}"; do
server_source="${SERVER_BASE}${account}/server/${side}_positions_active.json"
gateway_target="${GATEWAY_HOST}:${GATEWAY_BASE}${account}/server/${side}_positions_active.json"
if [ -f "$server_source" ]; then
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$server_source" "$gateway_target" >> "$LOG_FILE" 2>&1
fi
server_source_updated="${SERVER_BASE}${account}/${side}_positions.updated.json"
gateway_target_updated="${GATEWAY_HOST}:${GATEWAY_BASE}${account}/${side}_positions.updated.json"
if [ -f "$server_source_updated" ]; then
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$server_source_updated" "$gateway_target_updated" >> "$LOG_FILE" 2>&1
fi
server_source_main="${SERVER_BASE}${account}/${side}_positions.json"
gateway_target_main="${GATEWAY_HOST}:${GATEWAY_BASE}${account}/${side}_positions.json"
if [ -f "$server_source_main" ]; then
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$server_source_main" "$gateway_target_main" >> "$LOG_FILE" 2>&1
fi
done
done
FILE_COUNT=$(find "${SERVER_BASE}" -path "*/server/*_positions_active.json" -o -path "*/*_positions.updated.json" -o -path "*/*_positions.json" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files pushed" >> "$LOG_FILE"
sleep 60
done

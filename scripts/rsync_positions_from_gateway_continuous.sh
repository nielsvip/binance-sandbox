#!/bin/bash
# Continuously sync positions from gateway to macbook
GATEWAY_HOST="gateway-internal"
GATEWAY_BASE="/home/niels/binance/"
MACBOOK_BASE="/Users/niels/Documents/binance/"
LOG_FILE="$HOME/logs/rsync_positions_from_gateway.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
POSITION_SIDES=("LONG" "SHORT")
mkdir -p "$(dirname "$LOG_FILE")"
for account in "${ACCOUNT_FOLDERS[@]}"; do mkdir -p "${MACBOOK_BASE}${account}/gateway"; done
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous position rsync from gateway..." | tee -a "$LOG_FILE"
while true; do
echo "$(date '+%Y-%m-%d %H:%M:%S') - 📥 Syncing positions from gateway..." >> "$LOG_FILE"
for account in "${ACCOUNT_FOLDERS[@]}"; do
for side in "${POSITION_SIDES[@]}"; do
gateway_source="${GATEWAY_HOST}:${GATEWAY_BASE}${account}/gateway/${side}_positions_active.json"
macbook_target="${MACBOOK_BASE}${account}/gateway/${side}_positions_active.json"
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$gateway_source" "$macbook_target" >> "$LOG_FILE" 2>&1
gateway_source_updated="${GATEWAY_HOST}:${GATEWAY_BASE}${account}/${side}_positions.updated.json"
macbook_target_updated="${MACBOOK_BASE}${account}/${side}_positions.updated.json"
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$gateway_source_updated" "$macbook_target_updated" >> "$LOG_FILE" 2>&1
gateway_source_main="${GATEWAY_HOST}:${GATEWAY_BASE}${account}/${side}_positions.json"
macbook_target_main="${MACBOOK_BASE}${account}/${side}_positions.json"
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$gateway_source_main" "$macbook_target_main" >> "$LOG_FILE" 2>&1
done
done
FILE_COUNT=$(find "${MACBOOK_BASE}" -path "*/gateway/*_positions_active.json" -o -path "*/*_positions.updated.json" -o -path "*/*_positions.json" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files" >> "$LOG_FILE"
sleep 60
done
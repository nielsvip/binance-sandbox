#!/bin/bash
# Continuously sync positions from server to macbook
SERVER_HOST="s1-int"
SERVER_BASE="/home/niels/binance/"
MACBOOK_BASE="/Users/niels/Documents/binance/"
LOG_FILE="$HOME/logs/rsync_positions_from_server.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
POSITION_SIDES=("LONG" "SHORT")
mkdir -p "$(dirname "$LOG_FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting continuous position rsync from server..." | tee -a "$LOG_FILE"
while true; do
echo "$(date '+%Y-%m-%d %H:%M:%S') - 📥 Syncing positions from server..." >> "$LOG_FILE"
for account in "${ACCOUNT_FOLDERS[@]}"; do
for side in "${POSITION_SIDES[@]}"; do
side_lower=$(echo "$side" | tr '[:upper:]' '[:lower:]')
server_source="${SERVER_HOST}:${SERVER_BASE}${account}/${side_lower}_positions.json"
macbook_target="${MACBOOK_BASE}${account}/${side_lower}_positions.json"
if [ -f "$macbook_target" ]; then
# Backup existing file before overwriting
cp "$macbook_target" "${macbook_target}.backup.$(date +%Y%m%d_%H%M%S)" 2>/dev/null
fi
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$server_source" "$macbook_target" >> "$LOG_FILE" 2>&1
server_source_updated="${SERVER_HOST}:${SERVER_BASE}${account}/${side_lower}_positions_updated.json"
macbook_target_updated="${MACBOOK_BASE}${account}/${side_lower}_positions_updated.json"
rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude=".git" --exclude="backups*" "$server_source_updated" "$macbook_target_updated" >> "$LOG_FILE" 2>&1
done
done
FILE_COUNT=$(find "${MACBOOK_BASE}" -name "*_positions.json" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Sync complete: $FILE_COUNT files synced from server" >> "$LOG_FILE"
sleep 10
done


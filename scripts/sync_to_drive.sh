#!/bin/bash

# ==============================================================================
# SCRIPT: sync_to_drive.sh
# DESCRIPTION: Syncs ez_ and tradier_ scripts to local Google Drive folder.
# ==============================================================================

# CONFIGURATION
GDRIVE_PATH="/Users/niels/Library/CloudStorage/GoogleDrive-nielsvip@gmail.com/My Drive/trading_scripts"
LOCAL_DIR="/Users/niels/Documents/binance"
LOG_FILE="/Users/niels/logs/sync_to_drive.log"

# Create log and target directories if they don't exist
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$GDRIVE_PATH"

echo "$(date '+%Y-%m-%d %H:%M:%S') - 🔄 Starting local sync to Google Drive..." >> "$LOG_FILE"

# 1. Sync specific files
# -a: archive mode
# -v: verbose
# -z: compress (optional for local, but safe)
# --include: only these patterns
# --exclude: exclude everything else
rsync -av --include="ez_*.py" --include="tradier_*.py" \
         --include="config.py" --include="config_tradier.py" \
         --include="utils.py" --include="bridge.py" \
         --exclude="*" \
         "$LOCAL_DIR/" "$GDRIVE_PATH/" >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Local sync successful. Google Drive app will upload now." >> "$LOG_FILE"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ❌ Sync FAILED" >> "$LOG_FILE"
fi

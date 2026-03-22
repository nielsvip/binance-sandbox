#!/bin/bash
# Script to continuously sync position files from x86 gateway to server
SERVER_HOST="niels@157.180.125.52"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
POSITION_SIDES=("long" "short")
SYNC_INTERVAL=1  # Check for changes every second
LOG_DIR="/home/niels/logs"
LOG_FILE="$LOG_DIR/rsync_gateway_positions.log"

# Create log directory
mkdir -p "$LOG_DIR"

# Log function with rotation
log() {
    local log_msg="$(date '+%Y-%m-%d %H:%M:%S') - $1"
    echo "$log_msg" | tee -a "$LOG_FILE"
    
    # Rotate log if it gets too large (10MB)
    local log_size
    if [ -f "$LOG_FILE" ]; then
        log_size=$(wc -c < "$LOG_FILE")
        if [ "$log_size" -gt 10485760 ]; then  # 10MB in bytes
            mv "$LOG_FILE" "${LOG_FILE}.old"
            gzip "${LOG_FILE}.old" &
            log "📜 Log rotated"
        fi
    fi
}

# Sync a single file
sync_file() {
    local src="$1"
    local dest="$2"
    
    # Skip if source file doesn't exist
    if [ ! -f "$src" ]; then
        log "⚠️  Source file not found: $src"
        return 0
    fi
    
    log "🔄 Syncing $src to $dest"
    
    # Use the exact working rsync command
    local rsync_cmd="rsync -avz --progress \"$src\" \"$dest\""
    log "Running: $rsync_cmd"
    
    # Execute the command and capture output
    local output
    output=$(eval "$rsync_cmd" 2>&1)
    local status=$?
    
    if [ $status -eq 0 ]; then
        log "✅ Successfully synced $src"
        return 0
    else
        log "❌ Failed to sync $src (Exit code: $status)"
        log "Error output: $output"
        return 1
    fi
}

# Sync all position files
sync_all_positions() {
    for account in "${ACCOUNT_FOLDERS[@]}"; do
        local gateway_dir="/home/niels/binance/$account/"
        local server_dir="${SERVER_HOST}:/home/niels/binance/$account/"
        
        # Create directory on server if it doesn't exist
        ssh "${SERVER_HOST}" "mkdir -p /home/niels/binance/$account"
        
        for side in "${POSITION_SIDES[@]}"; do
            # Sync all position files
            for file in "${side}_positions.json" "${side}_positions.updated.json" "${side}_positions_active.json"; do
                sync_file "${gateway_dir}${file}" "${server_dir}${file}"
            done
        done
    done
}

# Main loop
log "🚀 Starting gateway position sync service..."
log "Source: /home/niels/binance/[account]/"
log "Destination: ${SERVER_HOST}:/home/niels/binance/[account]/"
log "Sync interval: ${SYNC_INTERVAL} second(s)"
log "Current PATH: $PATH"
log "Current user: $(whoami)"

while true; do
    sync_all_positions
    sleep "$SYNC_INTERVAL"
done
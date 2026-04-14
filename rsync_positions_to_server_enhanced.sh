#!/bin/bash
# Enhanced rsync script with fswatch for immediate syncing on file changes
SERVER_HOST="s1-int"
MACBOOK_BASE="/Users/niels/Documents/binance/"
SERVER_BASE="/home/niels/binance/"
LOG_FILE="$HOME/logs/rsync_positions_to_server.log"
ACCOUNT_FOLDERS=("ang" "fin" "flz" "men" "inf")
POSITION_SIDES=("long" "short")
RSYNC_OPTS="-avz --update --timeout=30 --exclude=\"*.tmp\" --exclude=\"*.lock\" --exclude=\"backups*\""

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"

# Log function
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
    
    if [ -f "$src" ]; then
        log "🔄 Syncing $src to $dest"
        # Use --update to only copy if source is newer than destination
        rsync -avz --update --timeout=30 --exclude="*.tmp" --exclude="*.lock" --exclude="backups*" "$src" "$dest" >> "$LOG_FILE" 2>&1
        if [ $? -eq 0 ]; then
            log "✅ Successfully synced $src"
            return 0
        else
            log "❌ Failed to sync $src"
            return 1
        fi
    else
        log "⚠️ File not found: $src"
        return 2
    fi
}

# Initial sync of all files
initial_sync() {
    log "🚀 Starting initial sync of all position files..."
    for account in "${ACCOUNT_FOLDERS[@]}"; do
        for side in "${POSITION_SIDES[@]}"; do
            # Sync active positions (from macbook/ to account root)
            sync_file "${MACBOOK_BASE}${account}/macbook/${side}_positions_active.json" \
                     "${SERVER_HOST}:${SERVER_BASE}${account}/${side}_positions_active.json"
            
            # Sync updated positions
            sync_file "${MACBOOK_BASE}${account}/${side}_positions.updated.json" \
                     "${SERVER_HOST}:${SERVER_BASE}${account}/${side}_positions.updated.json"
            
            # Sync main positions
            sync_file "${MACBOOK_BASE}${account}/${side}_positions.json" \
                     "${SERVER_HOST}:${SERVER_BASE}${account}/${side}_positions.json"
        done
    done
    log "✨ Initial sync completed"
}

# Watch for file changes using fswatch if available
start_fswatch() {
    if command -v fswatch >/dev/null 2>&1; then
        log "🔍 Starting fswatch to monitor file changes..."
        # Create a list of files to watch
        local watch_files=()
        for account in "${ACCOUNT_FOLDERS[@]}"; do
            for side in "${POSITION_SIDES[@]}"; do
                watch_files+=("${MACBOOK_BASE}${account}/macbook/${side}_positions_active.json")
                watch_files+=("${MACBOOK_BASE}${account}/${side}_positions.updated.json")
                watch_files+=("${MACBOOK_BASE}${account}/${side}_positions.json")
            done
        done

        # Start fswatch in the background
        fswatch -0 -o "${watch_files[@]}" | while read -d "" event; do
            # Get the changed file path
            changed_file=$(fswatch -l 1 -0 "${watch_files[@]}" | head -n 1)
            if [ -n "$changed_file" ]; then
                # Get account name from path
                account=$(echo "$changed_file" | grep -oP "(?<=${MACBOOK_BASE})[^/]+")
                filename=$(basename "$changed_file")
                
                # If this is a macbook file, sync to account root
                if [[ "$changed_file" == *"/macbook/"* ]]; then
                    target_file="${SERVER_HOST}:${SERVER_BASE}${account}/${filename}"
                else
                    target_file="${SERVER_HOST}:${SERVER_BASE}${account}/${filename}"
                fi
                
                # Sync the changed file
                sync_file "$changed_file" "$target_file"
            fi
        done &
        FS_WATCH_PID=$!
        log "👀 fswatch started with PID $FS_WATCH_PID"
    else
        log "⚠️ fswatch not found, falling back to polling every second"
        while true; do
            initial_sync
            sleep 1
        done
    fi
}

# Cleanup function
cleanup() {
    log "🛑 Shutting down rsync service..."
    if [ -n "$FS_WATCH_PID" ]; then
        kill "$FS_WATCH_PID" 2>/dev/null
    fi
    exit 0
}

# Set up trap for clean exit
trap cleanup EXIT

# Start the service
log "🚀 Starting enhanced rsync service with immediate file change detection..."
initial_sync
start_fswatch

# Keep the script running
while true; do
    sleep 60
    log "💓 Background service still running..."
done
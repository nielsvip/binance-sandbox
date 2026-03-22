#!/bin/bash
# DISABLED - Use start_macbook_rsync.sh and start_receive_rsync.sh instead
exit 0
# Start the 3-way rsync for _active position files
# This script should be run on macbook

LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"

# Kill any existing rsync processes
pkill -f "rsync_active_positions_3way" 2>/dev/null

echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting 3-way _active positions rsync..."

# Start the rsync script in background
nohup /Users/niels/Documents/binance/rsync_active_positions_3way.sh > "$LOG_DIR/rsync_active_positions_3way.out" 2>&1 &

# Get the PID
RSYNC_PID=$!
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Rsync started with PID: $RSYNC_PID"

# Save PID to file for easy management
echo $RSYNC_PID > "$LOG_DIR/rsync_active_positions_3way.pid"

echo "$(date '+%Y-%m-%d %H:%M:%S') - 📁 Monitoring log: $LOG_DIR/rsync_active_positions_3way.log"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🛑 To stop: kill $RSYNC_PID or pkill -f rsync_active_positions_3way"


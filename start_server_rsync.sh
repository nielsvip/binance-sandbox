#!/bin/bash
# Start server rsync service
LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"

# Kill any existing server rsync processes
pkill -f "rsync_server_to_others" 2>/dev/null

echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting server rsync service..."

# Start the rsync script in background
nohup /home/niels/binance/rsync_server_to_others.sh > "$LOG_DIR/rsync_server_to_others.out" 2>&1 &

# Get the PID
RSYNC_PID=$!
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Server rsync started with PID: $RSYNC_PID"

# Save PID to file
echo $RSYNC_PID > "$LOG_DIR/rsync_server_to_others.pid"

echo "$(date '+%Y-%m-%d %H:%M:%S') - 📁 Monitoring log: $LOG_DIR/rsync_server_to_others.log"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🛑 To stop: kill $RSYNC_PID or pkill -f rsync_server_to_others"


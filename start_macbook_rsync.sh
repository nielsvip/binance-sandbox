#!/bin/bash
# Start macbook rsync service
LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"

# Kill any existing macbook rsync processes
pkill -f "rsync_macbook_to_others" 2>/dev/null
pkill -f "rsync_logs_to_server_continuous" 2>/dev/null

echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting macbook rsync services..."

# Start the rsync scripts in background
nohup /Users/niels/Documents/binance/rsync_macbook_to_others.sh > "$LOG_DIR/rsync_macbook_to_others.out" 2>&1 &
nohup /Users/niels/Documents/binance/rsync_logs_to_server_continuous.sh > "$LOG_DIR/rsync_logs_to_server.out" 2>&1 &

# Get the PIDs
RSYNC_PID=$!
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Macbook rsyncs started"

# Save PID to file (last one is enough for simple tracking)
echo $RSYNC_PID > "$LOG_DIR/rsync_macbook_to_others.pid"

echo "$(date '+%Y-%m-%d %H:%M:%S') - 📁 Monitoring logs: $LOG_DIR/rsync_macbook_to_others.log and $LOG_DIR/rsync_logs_to_server.log"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🛑 To stop: pkill -f rsync_macbook_to_others && pkill -f rsync_logs_to_server_continuous"


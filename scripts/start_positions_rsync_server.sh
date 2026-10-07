#!/bin/bash
# Start position rsync services on server
LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
pkill -f "rsync_positions_server_to_gateway_continuous.sh" 2>/dev/null
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting position rsync services on server..."
if ! pgrep -f "rsync_positions_server_to_gateway_continuous.sh" > /dev/null; then
nohup bash /home/niels/binance/rsync_positions_server_to_gateway_continuous.sh > "$LOG_DIR/rsync_positions_server_to_gateway.out" 2>&1 &
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Server->Gateway position rsync started (PID: $!)"
fi
if ! pgrep -f "watchdog_positions_rsync.sh" > /dev/null; then
nohup bash /home/niels/binance/watchdog_positions_rsync.sh > "$LOG_DIR/watchdog_positions_rsync.out" 2>&1 &
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Position rsync watchdog started (PID: $!)"
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') - 📊 Status:"
ps aux | grep -E "rsync_positions|watchdog_positions" | grep -v grep

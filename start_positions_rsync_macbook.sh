#!/bin/bash
# Start position rsync services on macbook
LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
pkill -f "rsync_positions_from_gateway_continuous.sh" 2>/dev/null
pkill -f "rsync_positions_to_server_continuous.sh" 2>/dev/null
pkill -f "rsync_positions_from_server_continuous.sh" 2>/dev/null
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🚀 Starting position rsync services on macbook..."
if ! pgrep -f "rsync_positions_from_gateway_continuous.sh" > /dev/null; then
nohup bash /Users/niels/Documents/binance/rsync_positions_from_gateway_continuous.sh > "$LOG_DIR/rsync_positions_from_gateway.out" 2>&1 &
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Gateway position rsync started (PID: $!)"
fi
if ! pgrep -f "rsync_positions_to_server_continuous.sh" > /dev/null; then
nohup bash /Users/niels/Documents/binance/rsync_positions_to_server_continuous.sh > "$LOG_DIR/rsync_positions_to_server.out" 2>&1 &
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Server position rsync (TO) started (PID: $!)"
fi
if ! pgrep -f "rsync_positions_from_server_continuous.sh" > /dev/null; then
nohup bash /Users/niels/Documents/binance/rsync_positions_from_server_continuous.sh > "$LOG_DIR/rsync_positions_from_server.out" 2>&1 &
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Server position rsync (FROM) started (PID: $!)"
fi
if ! pgrep -f "watchdog_positions_rsync.sh" > /dev/null; then
nohup bash /Users/niels/Documents/binance/watchdog_positions_rsync.sh > "$LOG_DIR/watchdog_positions_rsync.out" 2>&1 &
echo "$(date '+%Y-%m-%d %H:%M:%S') - ✅ Position rsync watchdog started (PID: $!)"
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') - 📊 Status:"
ps aux | grep -E "rsync_positions|watchdog_positions" | grep -v grep

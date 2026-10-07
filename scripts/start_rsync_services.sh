#!/bin/bash
# DISABLED - Use start_macbook_rsync.sh and start_receive_rsync.sh instead
exit 0
# Start continuous rsync services
LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
echo "🚀 Starting rsync services..."
if pgrep -f "rsync_from_gateway_continuous.sh" > /dev/null; then
    echo "⚠️  Gateway rsync already running"
else
    echo "📥 Starting gateway→macbook rsync..."
    nohup bash /Users/niels/Documents/binance/rsync_from_gateway_continuous.sh > "$LOG_DIR/rsync_gateway.out" 2>&1 &
    echo "✅ Gateway rsync started (PID: $!)"
fi
if pgrep -f "rsync_to_server_continuous.sh" > /dev/null; then
    echo "⚠️  Server rsync already running"
else
    echo "📤 Starting macbook→server rsync..."
    nohup bash /Users/niels/Documents/binance/rsync_to_server_continuous.sh > "$LOG_DIR/rsync_server.out" 2>&1 &
    echo "✅ Server rsync started (PID: $!)"
fi
echo ""
echo "📊 Status:"
ps aux | grep -E "rsync_from_gateway|rsync_to_server" | grep -v grep
echo ""
echo "📝 Logs:"
echo "  Gateway: tail -f $LOG_DIR/rsync_from_gateway.log"
echo "  Server:  tail -f $LOG_DIR/rsync_to_server.log"


#!/bin/bash
# Watchdog for rsync services - auto-restarts if they die
LOCK_FILE="$HOME/logs/rsync_watchdog.lock"
LOG_FILE="$HOME/logs/rsync_watchdog.log"
mkdir -p "$(dirname "$LOG_FILE")"
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE")
    if ps -p $PID > /dev/null 2>&1; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Watchdog already running (PID: $PID)"
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') - 🐕 Watchdog started (PID: $$)" | tee -a "$LOG_FILE"
while true; do
    sleep 30
    # Check gateway rsync
    if ! pgrep -f "rsync_from_gateway_continuous.sh" > /dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ⚠️ Gateway rsync dead, restarting..." >> "$LOG_FILE"
        nohup bash "$HOME/Documents/binance/rsync_from_gateway_continuous.sh" >> /dev/null 2>&1 &
    fi
    # Check server rsync
    if ! pgrep -f "rsync_to_server_continuous.sh" > /dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ⚠️ Server rsync dead, restarting..." >> "$LOG_FILE"
        nohup bash "$HOME/Documents/binance/rsync_to_server_continuous.sh" >> /dev/null 2>&1 &
    fi
    # Check log rsync
    if ! pgrep -f "rsync_logs_to_server_continuous.sh" > /dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ⚠️ Log rsync dead, restarting..." >> "$LOG_FILE"
        nohup bash "$HOME/Documents/binance/rsync_logs_to_server_continuous.sh" >> /dev/null 2>&1 &
    fi
done


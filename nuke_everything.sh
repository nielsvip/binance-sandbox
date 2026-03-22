#!/bin/bash
# nuke_everything.sh - Definitive cleanup for the trading system

LOG_FILE="/Users/niels/logs/system_nuke.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 🚀 STARTING FULL SYSTEM NUKE" | tee -a "$LOG_FILE"

# 1. Kill Python scripts (ez_ only)
echo "Killing ez_ Python scripts..." | tee -a "$LOG_FILE"
pkill -9 -f "python.*ez_" 2>/dev/null

# 2. Kill Shell Watchdogs (for ez_ scripts)
echo "Killing ez_ Watchdogs..." | tee -a "$LOG_FILE"
pkill -9 -f "bash.*run_with_watchdog.*ez_" 2>/dev/null
# Avoid killing the launcher itself
pgrep -f "bash.*start_everything" | grep -v $$ | grep -v $PPID | xargs kill -9 2>/dev/null

# 3. Kill Log stream helpers (Tail/Tee for ez_)
echo "Killing ez_ Log helpers (tail/tee)..." | tee -a "$LOG_FILE"
# Kill tail processes that are following ez_ app logs
pkill -9 -f "tail -n 0 -F.*ez_.*_app.log" 2>/dev/null
# Only kill tee processes that are related to ez_ logs and not the current log
pgrep -f "tee -a.*ez_.*\.log" | grep -v $$ | grep -v $PPID | xargs kill -9 2>/dev/null

# 4. Close Terminal windows (macOS specific - ez_ only)
echo "Closing ez_ Terminal windows..." | tee -a "$LOG_FILE"
osascript -e 'tell application "Terminal" to close (every window whose name contains "ez_")' 2>/dev/null

# 5. Final forceful cleanup by checking PID list manually
echo "Final process scan..." | tee -a "$LOG_FILE"
# Match any remaining python or bash processes starting with ez_
# Exclude the nuke script, the launcher, and common system tools
REMAINING=$(ps aux | grep -Ei "ez_" | grep -v grep | grep -v "nuke_everything" | grep -v "start_everything" | grep -v "Visual Studio Code" | awk '{print $2}')
if [ -n "$REMAINING" ]; then
    echo "Force killing remaining PIDs: $REMAINING" | tee -a "$LOG_FILE"
    for pid in $REMAINING; do
        # Don't kill ourselves or parent
        if [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ]; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
fi

# Kill ez_share_ind port 50005 explicitly to allow clean rebind
lsof -ti:50005 2>/dev/null | xargs kill -9 2>/dev/null || true

sleep 2
echo "[$(date +'%Y-%m-%d %H:%M:%S')] ✅ SYSTEM NUKE COMPLETE" | tee -a "$LOG_FILE"

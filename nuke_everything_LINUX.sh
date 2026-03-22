#!/bin/bash
# nuke_everything_LINUX.sh - Definitive cleanup for the trading system on Linux

LOG_FILE="/home/niels/logs/system_nuke.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 🚀 STARTING FULL SYSTEM NUKE (LINUX)" | tee -a "$LOG_FILE"

# 1. Kill Python scripts (Binance + Tradier)
echo "Killing Python scripts..." | tee -a "$LOG_FILE"
pkill -9 -f "python.*ez_" 2>/dev/null
pkill -9 -f "python.*tradier_" 2>/dev/null
pkill -9 -f "python.*pa.py" 2>/dev/null
pkill -9 -f "python.*bridge.py" 2>/dev/null

# 2. Kill Shell Watchdogs and Orchestrators
echo "Killing Watchdogs and Orchestrators..." | tee -a "$LOG_FILE"
pkill -9 -f "bash.*run_with_watchdog" 2>/dev/null
pkill -9 -f "bash.*start_everything" 2>/dev/null
pkill -9 -f "sh.*start_everything" 2>/dev/null

# 3. Kill Log stream helpers (Tail/Tee)
echo "Killing Log helpers (tail/tee)..." | tee -a "$LOG_FILE"
pkill -9 -f "tail -n 0 -F" 2>/dev/null
pkill -9 -f "tee -a" 2>/dev/null

# 4. Kill user systemd services if any
echo "Stopping systemd services..." | tee -a "$LOG_FILE"
systemctl --user stop "binance-*" 2>/dev/null || true

# 5. Final forceful cleanup by checking PID list manually
echo "Final process scan..." | tee -a "$LOG_FILE"
# Match any remaining python or bash processes in the binance directory
REMAINING=$(ps aux | grep -Ei "binance|ez_|tradier" | grep -v grep | grep -v "nuke_everything" | awk '{print $2}')
if [ -n "$REMAINING" ]; then
    echo "Force killing remaining PIDs: $REMAINING" | tee -a "$LOG_FILE"
    for pid in $REMAINING; do
        kill -9 "$pid" 2>/dev/null || true
    done
fi

sleep 2
echo "[$(date +'%Y-%m-%d %H:%M:%S')] ✅ SYSTEM NUKE COMPLETE" | tee -a "$LOG_FILE"

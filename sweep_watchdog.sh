#!/bin/bash
# Sweep watchdog — kills hung processes, restarts if no log output for 60s
# Usage: nohup bash sweep_watchdog.sh &

LOGDIR="/home/niels/binance/data/delta_sweep"
CHECK_INTERVAL=30

while true; do
    NOW=$(date +%s)
    for logfile in "$LOGDIR"/*.log; do
        [ -f "$logfile" ] || continue
        BASENAME=$(basename "$logfile")
        MTIME=$(stat -c %Y "$logfile" 2>/dev/null || echo 0)
        AGE=$(( NOW - MTIME ))

        # Find the PID writing to this log
        PID=$(fuser "$logfile" 2>/dev/null | awk '{print $1}')

        if [ -n "$PID" ] && [ "$AGE" -gt 60 ]; then
            echo "[$(date +%H:%M:%S)] STALE: $BASENAME age=${AGE}s PID=$PID — KILLING"
            kill "$PID" 2>/dev/null
            sleep 2
            kill -9 "$PID" 2>/dev/null
        elif [ -n "$PID" ] && [ "$AGE" -le 60 ]; then
            # Check if log has actual sweep output (not just startup)
            LINES=$(wc -l < "$logfile")
            if [ "$LINES" -lt 3 ] && [ "$AGE" -gt 30 ]; then
                echo "[$(date +%H:%M:%S)] STUCK: $BASENAME lines=$LINES age=${AGE}s PID=$PID — KILLING"
                kill "$PID" 2>/dev/null
            fi
        fi
    done
    sleep "$CHECK_INTERVAL"
done

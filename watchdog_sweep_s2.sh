#!/bin/bash
# watchdog_sweep_s2.sh — every 5min check that S2 tradier sweep is alive; relaunch via canonical launcher if dead.
LOG=/home/niels/logs/watchdog_sweep_s2.log
TS=$(date -u "+%Y-%m-%d %H:%M:%S UTC")
N=$(pgrep -afc "v8_quick_sweep.*--mode tradier" || echo 0)
if [ "$N" -lt 1 ]; then
    echo "[$TS] $N v8_quick_sweep tradier procs — relaunching via start_tradier_sweeps.sh entry_gates" >> "$LOG"
    bash /home/niels/binance-sandbox/start_tradier_sweeps.sh entry_gates >> "$LOG" 2>&1
    sleep 5
    echo "[$TS] post-relaunch procs=$(pgrep -afc 'v8_quick_sweep.*--mode tradier')" >> "$LOG"
fi

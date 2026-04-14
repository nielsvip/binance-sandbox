#!/bin/bash
# rsync_sweep_results.sh — Pull latest indicator matrix XLS from server to Desktop/results
LOG="/Users/niels/logs/rsync_sweep_results.log"
mkdir -p "$(dirname "$LOG")" ~/Desktop/results
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Syncing sweep results from server..." >> "$LOG"
rsync -av --include="*.xlsx" --include="*.log" --include="phase4_strategy_results.json" --include="MASTER_SUMMARY.json" --exclude="phase1_*.json" --exclude="phase2_*.json" --exclude="phase4_all_trades.json" s1-int:/home/niels/binance-sandbox/sweep_results/ ~/Desktop/results/ >> "$LOG" 2>&1
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Done. Files:" >> "$LOG"
ls -lh ~/Desktop/results/*.xlsx 2>/dev/null >> "$LOG"
echo "---" >> "$LOG"

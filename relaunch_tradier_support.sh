#!/bin/bash
# Relaunch tradier_indicators + tradier_rankings at pre-market.
# Paused 2026-04-28 evening to free local CPU during market-closed hours.
cd /Users/niels/Documents/binance
LOG=/Users/niels/logs/tradier_support_relaunch.log
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] relaunching tradier_indicators + tradier_rankings via watchdog" >> "$LOG"
nohup bash /Users/niels/Documents/binance/run_with_watchdog.sh tradier_indicators.py > /dev/null 2>&1 &
echo "  tradier_indicators bash PID=$!" >> "$LOG"
nohup bash /Users/niels/Documents/binance/run_with_watchdog.sh tradier_rankings.py > /dev/null 2>&1 &
echo "  tradier_rankings bash PID=$!" >> "$LOG"
sleep 3
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] post-relaunch state:" >> "$LOG"
pgrep -fl "tradier_indicators|tradier_rankings" >> "$LOG" 2>&1

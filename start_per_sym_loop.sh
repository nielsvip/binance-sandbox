#!/bin/bash
# start_per_sym_loop.sh — 24/7 per-symbol optimizer loop on S1 (crypto only)
# Cycle: fin → ang → men → fin → ang → men → ...
# Usage: bash start_per_sym_loop.sh [start_after_current]
#   start_after_current: if set, skips fin+ang on first pass (assumes flz8 just ran)

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
BINANCE=/home/niels/binance-sandbox
LOG=~/logs/per_sym_loop.log
YEARS=2.0

mkdir -p ~/logs

ACCOUNTS=("fin" "ang" "men")
SCRIPTS=("per_sym_fin_profiles.py" "per_sym_ang_profiles.py" "per_sym_men_profiles.py")

echo "[per_sym_loop] Starting continuous optimization loop at $(date -u)" >> "$LOG" 2>&1
echo "[per_sym_loop] Accounts: ${ACCOUNTS[*]}" >> "$LOG" 2>&1

iteration=0
while true; do
    iteration=$((iteration + 1))
    echo "" >> "$LOG"
    echo "=== LOOP ITERATION $iteration  $(date -u) ===" >> "$LOG" 2>&1
    for i in "${!ACCOUNTS[@]}"; do
        acct="${ACCOUNTS[$i]}"
        script="${SCRIPTS[$i]}"
        echo "[per_sym_loop] Starting $acct ($script) at $(date -u)" >> "$LOG" 2>&1
        cd "$BINANCE" && "$PYTHON" -u "$script" --years "$YEARS" >> "$LOG" 2>&1
        ec=$?
        echo "[per_sym_loop] $acct finished (exit=$ec) at $(date -u)" >> "$LOG" 2>&1
    done
    echo "[per_sym_loop] Cycle $iteration complete at $(date -u). Restarting." >> "$LOG" 2>&1
done

#!/bin/bash
# HTF Exit Grid — runs all configurations sequentially
# Each test writes equity CSV + trade log for comparison
PY="/opt/anaconda3/envs/binance_env/bin/python"
SCRIPT="backtest_v5_full_tradier.py"
LOG="/Users/niels/logs"
BASE="--all --start 2024-06-01 --noloss 0"
cd /Users/niels/Documents/binance
echo "$(date -u): HTF EXIT GRID STARTED"

# H01-H07: Single TF exits
$PY -u $SCRIPT $BASE --wt-exit-tfs "1h"        > $LOG/htf_H01_1h.log 2>&1;          echo "$(date -u): H01 done"
$PY -u $SCRIPT $BASE --wt-exit-tfs "4h"        > $LOG/htf_H02_4h.log 2>&1;          echo "$(date -u): H02 done"
$PY -u $SCRIPT $BASE --wt-exit-tfs "D"         > $LOG/htf_H03_D.log 2>&1;           echo "$(date -u): H03 done"
$PY -u $SCRIPT $BASE --wt-exit-tfs "off"       > $LOG/htf_H04_off.log 2>&1;         echo "$(date -u): H04 HODL done"

# H05-H09: Multi-TF combos
$PY -u $SCRIPT $BASE --wt-exit-tfs "1h,4h"     > $LOG/htf_H05_1h4h.log 2>&1;       echo "$(date -u): H05 done"
$PY -u $SCRIPT $BASE --wt-exit-tfs "4h,D"      > $LOG/htf_H06_4hD.log 2>&1;        echo "$(date -u): H06 done"
$PY -u $SCRIPT $BASE --wt-exit-tfs "1h,4h,D"   > $LOG/htf_H07_1h4hD.log 2>&1;      echo "$(date -u): H07 done"

# H08-H10: Velocity mode
$PY -u $SCRIPT $BASE --wt-exit-tfs "4h" --wt-exit-mode velocity --wt-vel-threshold -2  > $LOG/htf_H08_4h_vel2.log 2>&1;  echo "$(date -u): H08 done"
$PY -u $SCRIPT $BASE --wt-exit-tfs "D"  --wt-exit-mode velocity --wt-vel-threshold -1  > $LOG/htf_H09_D_vel1.log 2>&1;   echo "$(date -u): H09 done"
$PY -u $SCRIPT $BASE --wt-exit-tfs "4h,D" --wt-exit-mode both                          > $LOG/htf_H10_4hD_both.log 2>&1;  echo "$(date -u): H10 done"

# H11-H12: With Daily entry gate
$PY -u $SCRIPT $BASE --wt-exit-tfs "4h" --entry-d-gate    > $LOG/htf_H11_4h_Dgate.log 2>&1;   echo "$(date -u): H11 done"
$PY -u $SCRIPT $BASE --wt-exit-tfs "4h,D" --entry-d-gate  > $LOG/htf_H12_4hD_Dgate.log 2>&1;  echo "$(date -u): H12 done"

# Baseline comparisons
$PY -u $SCRIPT $BASE --wt-exit-tfs "5m,15m,1h"  > $LOG/htf_H00_original.log 2>&1;  echo "$(date -u): H00 original done"
$PY -u $SCRIPT --all --start 2024-06-01 --noloss 1.0 --wt-exit-tfs "4h,D" --entry-d-gate  > $LOG/htf_H13_best_noloss1.log 2>&1;  echo "$(date -u): H13 done"

echo "$(date -u): HTF EXIT GRID COMPLETE"
echo "=== RESULTS ==="
for f in $LOG/htf_H*.log; do
    name=$(basename $f .log)
    pnl=$(grep "PnL:" $f 2>/dev/null | tail -1 | grep -oP '\$[\d.]+' | head -1)
    eq=$(grep "eq:" $f 2>/dev/null | tail -1 | grep -oP 'eq: \$[\d.]+' | head -1)
    trades=$(grep "Realized:" $f 2>/dev/null | grep -oP '\d+ trades' | head -1)
    echo "$name: $pnl realized, $eq final equity, $trades"
done

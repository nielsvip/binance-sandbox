#!/bin/bash
# Pull WT+DC mega sweep results from Server 2, analyze, and show top configs
# Run: bash pull_wt_dc_results.sh

SERVER="s2-int"
LOCAL_DIR="/Users/niels/Documents/binance/data/sweep_results"
REMOTE_MASTER="/home/niels/binance-sandbox/wt_dc_master_results.csv"
REMOTE_P1="/home/niels/binance-sandbox/wt_dc_results/wt_dc_sweep.csv"
REMOTE_P2="/home/niels/binance-sandbox/wt_dc_results_p2/wt_dc_p2_sweep.csv"

mkdir -p "$LOCAL_DIR"

echo "=== Checking sweep status ==="
ssh -o ConnectTimeout=5 "$SERVER" "
  if [ -f /home/niels/SWEEP_RUNNING ]; then
    echo 'STILL RUNNING:'
    cat /home/niels/SWEEP_RUNNING
    echo '---'
    echo 'Phase 1 configs done:' \$(ls /home/niels/binance-sandbox/wt_dc_results/wt_e*.csv 2>/dev/null | wc -l)
    echo 'Phase 2 configs done:' \$(ls /home/niels/binance-sandbox/wt_dc_results_p2/p2_e*.csv 2>/dev/null | wc -l)
    ps aux | grep backtest_wt_dc | grep -v grep | wc -l
    echo ' workers active'
  else
    echo 'SWEEP COMPLETE'
  fi
"

echo ""
echo "=== Pulling results ==="
scp "$SERVER:$REMOTE_P1" "$LOCAL_DIR/wt_dc_phase1.csv" 2>/dev/null && echo "Phase 1: OK" || echo "Phase 1: not ready"
scp "$SERVER:$REMOTE_P2" "$LOCAL_DIR/wt_dc_phase2.csv" 2>/dev/null && echo "Phase 2: OK" || echo "Phase 2: not ready"
scp "$SERVER:$REMOTE_MASTER" "$LOCAL_DIR/wt_dc_master.csv" 2>/dev/null && echo "Master: OK" || echo "Master: not ready"

echo ""
echo "=== TOP 30 CONFIGS BY SHARPE ==="
if [ -f "$LOCAL_DIR/wt_dc_master.csv" ]; then
    head -31 "$LOCAL_DIR/wt_dc_master.csv" | column -t -s,
elif [ -f "$LOCAL_DIR/wt_dc_phase1.csv" ]; then
    head -31 "$LOCAL_DIR/wt_dc_phase1.csv" | column -t -s,
else
    echo "No results yet"
fi

echo ""
echo "=== STATS ==="
if [ -f "$LOCAL_DIR/wt_dc_master.csv" ]; then
    TOTAL=$(tail -n +2 "$LOCAL_DIR/wt_dc_master.csv" | wc -l)
    POSITIVE=$(tail -n +2 "$LOCAL_DIR/wt_dc_master.csv" | awk -F, '$12 > 0' | wc -l)
    echo "Total configs tested: $TOTAL"
    echo "Positive Sharpe: $POSITIVE"
    echo "Best Sharpe: $(tail -n +2 "$LOCAL_DIR/wt_dc_master.csv" | sort -t, -k12 -rn | head -1 | cut -d, -f1-6,12)"
fi

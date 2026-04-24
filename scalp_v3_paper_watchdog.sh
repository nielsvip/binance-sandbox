#!/bin/bash
# 2026-04-24: Weekend V3 paper A/B watchdog. 60s tick. macOS-compatible (no pgrep -c).
LOG=/Users/niels/logs/scalp_v3_paper_watchdog.log
PY=/opt/anaconda3/envs/binance_env/bin/python3
DIR=/Users/niels/Documents/binance

while true; do
  TS=$(date -u +%H:%M:%S)
  # Count close_ob (has --orderbook-filter)
  count_ob=$(pgrep -f "scalp_v3_paper.*--orderbook-filter" 2>/dev/null | wc -l | tr -d ' ')
  # Count close_pure (has --exit-strategy close but NOT --orderbook-filter)
  pids_all=$(pgrep -f "scalp_v3_paper.*--exit-strategy close" 2>/dev/null)
  count_pure=0
  for p in $pids_all; do
    if ! ps -p "$p" -o command= 2>/dev/null | grep -q "orderbook-filter"; then
      count_pure=$((count_pure+1))
    fi
  done
  if [ "$count_pure" -eq 0 ]; then
    echo "[$TS] close_pure DEAD → relaunching" >> $LOG
    nohup $PY -u $DIR/scalp_v3_paper.py --tf-mode 3M_ONLY --exit-mode 15M_ONLY --vol-mult 2.0 --k-1m-max 25 --exit-k-1m-min 95 --max-hold-min 5 --stall-gain -0.1 --poll-sec 60 --concurrency 2 --exit-strategy close > $DIR/data/scalp_v3_paper/run_close_pure.log 2>&1 &
    sleep 3
  fi
  if [ "$count_ob" -eq 0 ]; then
    echo "[$TS] close_ob DEAD → relaunching" >> $LOG
    nohup $PY -u $DIR/scalp_v3_paper.py --tf-mode 3M_ONLY --exit-mode 15M_ONLY --vol-mult 2.0 --k-1m-max 25 --exit-k-1m-min 95 --max-hold-min 5 --stall-gain -0.1 --poll-sec 60 --concurrency 2 --exit-strategy close --orderbook-filter --ob-min-long-score 70 --ob-wall-too-close-pct 0.5 --ob-void-extend-hold --ob-out-suffix _ob_score > $DIR/data/scalp_v3_paper/run_close_ob.log 2>&1 &
    sleep 3
  fi
  echo "[$TS] ALIVE: close_pure=$count_pure close_ob=$count_ob" >> $LOG
  sleep 60
done

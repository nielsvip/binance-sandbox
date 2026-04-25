#!/bin/bash
# 2026-04-25: Weekend V3 paper A/B watchdog. OB-close vs OB-hedge.
LOG=/Users/niels/logs/scalp_v3_paper_watchdog.log
PY=/opt/anaconda3/envs/binance_env/bin/python3
DIR=/Users/niels/Documents/binance

while true; do
  TS=$(date -u +%H:%M:%S)
  # Count close variant (--exit-strategy close)
  pids_close=$(pgrep -f "scalp_v3_paper.*--exit-strategy close" 2>/dev/null)
  count_close=0
  for p in $pids_close; do
    if ps -p "$p" -o command= 2>/dev/null | grep -q "orderbook-filter"; then
      count_close=$((count_close+1))
    fi
  done
  # Count hedge variant (--exit-strategy hedge)
  count_hedge=$(pgrep -f "scalp_v3_paper.*--exit-strategy hedge" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$count_close" -eq 0 ]; then
    echo "[$TS] close_ob DEAD → relaunching" >> $LOG
    nohup $PY -u $DIR/scalp_v3_paper.py --tf-mode 3M_ONLY --exit-mode 15M_ONLY --vol-mult 2.0 --k-1m-max 25 --exit-k-1m-min 95 --max-hold-min 5 --stall-gain -0.1 --poll-sec 60 --concurrency 2 --exit-strategy close --orderbook-filter --ob-min-long-score 70 --ob-wall-too-close-pct 0.5 --ob-void-extend-hold --ob-out-suffix _ob_close > $DIR/data/scalp_v3_paper/run_close_ob.log 2>&1 &
    sleep 3
  fi
  if [ "$count_hedge" -eq 0 ]; then
    echo "[$TS] hedge_ob DEAD → relaunching" >> $LOG
    nohup $PY -u $DIR/scalp_v3_paper.py --tf-mode 3M_ONLY --exit-mode 15M_ONLY --vol-mult 2.0 --k-1m-max 25 --exit-k-1m-min 95 --max-hold-min 5 --stall-gain -0.1 --poll-sec 60 --concurrency 2 --exit-strategy hedge --orderbook-filter --ob-min-long-score 70 --ob-wall-too-close-pct 0.5 --ob-void-extend-hold --ob-out-suffix _ob_hedge > $DIR/data/scalp_v3_paper/run_hedge_ob.log 2>&1 &
    sleep 3
  fi
  echo "[$TS] ALIVE: close_ob=$count_close hedge_ob=$count_hedge" >> $LOG
  sleep 60
done

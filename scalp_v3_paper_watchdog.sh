#!/bin/bash
# 2026-04-25: V3 paper multi-variant watchdog. Relaunches any variant that dies.
# Each variant has a unique --ob-out-suffix → unique run log + state + trades.
# All variants share ez_orderbook + tradeable_keys.json.
LOG=/Users/niels/logs/scalp_v3_paper_watchdog.log
PY=/opt/anaconda3/envs/binance_env/bin/python3
DIR=/Users/niels/Documents/binance
PAPER=$DIR/scalp_v3_paper.py
DATA=$DIR/data/scalp_v3_paper

# Variant definitions: SUFFIX | EXIT_STRATEGY | EXTRA_ARGS
# Common args (winner sweep): --tf-mode 3M_ONLY --exit-mode 15M_ONLY --vol-mult 2.0 --k-1m-max 25 --exit-k-1m-min 95 --max-hold-min 5 --stall-gain -0.1 --poll-sec 60 --concurrency 2
COMMON="--tf-mode 3M_ONLY --exit-mode 15M_ONLY --vol-mult 2.0 --k-1m-max 25 --exit-k-1m-min 95 --max-hold-min 5 --stall-gain -0.1 --poll-sec 60 --concurrency 2"
OBWIN="--orderbook-filter --ob-min-long-score 70 --ob-wall-too-close-pct 0.5 --ob-void-extend-hold"

# variant_id | extra args (must include --ob-out-suffix _<id>)
VARIANTS=(
  "ob_close|--exit-strategy close $OBWIN --ob-out-suffix _ob_close"
  "ob_hedge|--exit-strategy hedge $OBWIN --ob-out-suffix _ob_hedge"
  "ob_close_both|--exit-strategy close $OBWIN --side-mode BOTH --ob-out-suffix _ob_close_both"
  "ob_close_atr_tight|--exit-strategy close $OBWIN --atr-tp-mult 0.4 --ob-out-suffix _ob_close_atr_tight"
  "ob_close_atr_loose|--exit-strategy close $OBWIN --atr-tp-mult 1.5 --ob-out-suffix _ob_close_atr_loose"
  "ob_close_pg_tight|--exit-strategy close $OBWIN --pg-arm-pct 0.3 --pg-giveback-pct 0.1 --ob-out-suffix _ob_close_pg_tight"
  "ob_close_no_ob|--exit-strategy close --ob-out-suffix _close_no_ob"
  "ob_close_exit_any|--exit-strategy close $OBWIN --exit-mode ANY --ob-out-suffix _ob_close_exit_any"
  "ob_close_hold15|--exit-strategy close $OBWIN --max-hold-min 15 --ob-out-suffix _ob_close_hold15"
  "ob_close_stall_on|--exit-strategy close $OBWIN --stall-enabled 1 --ob-out-suffix _ob_close_stall_on"
)

while true; do
  TS=$(date -u +%H:%M:%S)
  ALIVE_COUNT=0
  STATUS=""
  for V in "${VARIANTS[@]}"; do
    NAME="${V%%|*}"
    ARGS="${V#*|}"
    # Match by suffix string
    PIDS=$(pgrep -f "scalp_v3_paper.py .*--ob-out-suffix _${NAME}\b" 2>/dev/null)
    COUNT=0
    for P in $PIDS; do
      ps -p "$P" -o command= 2>/dev/null > /dev/null && COUNT=$((COUNT+1))
    done
    if [ "$COUNT" -eq 0 ]; then
      echo "[$TS] $NAME DEAD → relaunching" >> $LOG
      LOG_OUT=$DATA/run_${NAME}.log
      nohup $PY -u $PAPER $COMMON $ARGS > $LOG_OUT 2>&1 &
      disown
      sleep 2
    else
      ALIVE_COUNT=$((ALIVE_COUNT+1))
    fi
    STATUS="$STATUS $NAME=$COUNT"
  done
  echo "[$TS] ALIVE=$ALIVE_COUNT/${#VARIANTS[@]} $STATUS" >> $LOG
  sleep 60
done

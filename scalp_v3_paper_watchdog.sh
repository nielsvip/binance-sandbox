#!/bin/bash
# 2026-04-26: V3 paper multi-variant watchdog. Relaunches any variant that dies.
# Each variant has a unique --ob-out-suffix → unique run log + state + trades.
# Match logic: use unique suffix as exact string match in `ps -ef` output.
LOG=/Users/niels/logs/scalp_v3_paper_watchdog.log
PY=/opt/anaconda3/envs/binance_env/bin/python3
DIR=/Users/niels/Documents/binance
PAPER=$DIR/scalp_v3_paper.py
DATA=$DIR/data/scalp_v3_paper

COMMON="--tf-mode 3M_ONLY --exit-mode 15M_ONLY --vol-mult 2.0 --k-1m-max 25 --exit-k-1m-min 95 --max-hold-min 5 --stall-gain -0.1 --poll-sec 60 --concurrency 2"
OBWIN="--orderbook-filter --ob-min-long-score 70 --ob-wall-too-close-pct 0.5 --ob-void-extend-hold"

# variant_id (also serves as suffix without leading _) | extra args
# Suffix in args is `--ob-out-suffix _<variant_id>` so it matches.
VARIANTS=(
  "ob_close|--exit-strategy close $OBWIN"
  "ob_hedge|--exit-strategy hedge $OBWIN"
  "ob_close_both|--exit-strategy close $OBWIN --side-mode BOTH"
  "ob_close_atrtight|--exit-strategy close $OBWIN --atr-tp-mult 0.4"
  "ob_close_pgtight|--exit-strategy close $OBWIN --pg-arm-pct 0.3 --pg-giveback-pct 0.1"
  "no_ob_close|--exit-strategy close"
)

is_alive() {
  local SUF="$1"
  # Match exact "--ob-out-suffix _$SUF " (trailing space/end) in full command
  local NEEDLE="--ob-out-suffix _${SUF}"
  ps -ef | grep "scalp_v3_paper.py" | grep -F -- "$NEEDLE" | grep -v grep | wc -l | tr -d ' '
}

while true; do
  TS=$(date -u +%H:%M:%S)
  STATUS=""
  for V in "${VARIANTS[@]}"; do
    NAME="${V%%|*}"
    ARGS="${V#*|}"
    COUNT=$(is_alive "$NAME")
    if [ "$COUNT" -eq 0 ]; then
      echo "[$TS] $NAME DEAD → relaunching" >> $LOG
      LOG_OUT=$DATA/run_${NAME}.log
      cd $DIR && nohup $PY -u $PAPER $COMMON $ARGS --ob-out-suffix _${NAME} > $LOG_OUT 2>&1 &
      disown
      sleep 2
    fi
    STATUS="$STATUS $NAME=$COUNT"
  done
  echo "[$TS]$STATUS" >> $LOG
  sleep 60
done

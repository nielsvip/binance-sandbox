#!/bin/bash
# Watchdog wrapper for backtest_redzone_perpetual.py
# - Launches N parallel workers (each handles a shard of the config grid)
# - Each worker has its own restart loop (auto-recovers from crash)
# - Runs forever
#
# Usage: ./run_redzone_watchdog.sh <crypto|tradier> [account] [start] [num_workers]
#
# Examples:
#   ./run_redzone_watchdog.sh crypto ang 2026-01-01 4
#   ./run_redzone_watchdog.sh tradier trb 2026-01-01 4

set -u
MODE="${1:-crypto}"
ACCOUNT="${2:-ang}"
START="${3:-2026-01-01}"
NUM_WORKERS="${4:-4}"

# Server detection — pick python from local conda env
if [ -x "/home/niels/.conda/envs/binance_env/bin/python3" ]; then
  PYTHON="/home/niels/.conda/envs/binance_env/bin/python3"
elif [ -x "/home/niels/miniconda3/envs/binance_env/bin/python3" ]; then
  PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python3"
elif [ -x "/opt/anaconda3/envs/binance_env/bin/python" ]; then
  PYTHON="/opt/anaconda3/envs/binance_env/bin/python"
else
  PYTHON="$(command -v python3)"
fi

# Default symbols and capital based on mode
if [ "$MODE" = "crypto" ]; then
  SYMBOLS="ANKRUSDT,BANDUSDT,BATUSDT,CHRUSDT,COMPUSDT,COTIUSDT,DOTUSDT,ENJUSDT,GRTUSDT,SANDUSDT"
  CAPITAL="1000"
else
  SYMBOLS="AAPL,MSFT,AMZN,GOOGL,TSLA,NVDA,AMD,META,SPY,QQQ"
  CAPITAL="10000"
fi

# Working directory: try sandbox first, fall back to home
if [ -d "/home/niels/binance-sandbox" ]; then
  WORKDIR="/home/niels/binance-sandbox"
elif [ -d "$HOME/Documents/binance" ]; then
  WORKDIR="$HOME/Documents/binance"
else
  WORKDIR="$(pwd)"
fi

LOG_DIR="$WORKDIR/backtest_v8/sweeps/redzone_perpetual"
mkdir -p "$LOG_DIR"

WATCHDOG_LOG="$LOG_DIR/watchdog_${MODE}_${ACCOUNT}.log"

cd "$WORKDIR" || exit 1

echo "$(date -u '+%Y-%m-%d %H:%M:%SZ') WATCHDOG START mode=$MODE account=$ACCOUNT start=$START num_workers=$NUM_WORKERS python=$PYTHON workdir=$WORKDIR" >> "$WATCHDOG_LOG"

# Worker restart loop — runs in background, restarts on crash
worker_loop() {
  local WORKER_ID="$1"
  local RESTART_COUNT=0
  while true; do
    RESTART_COUNT=$((RESTART_COUNT + 1))
    local RUN_LOG="$LOG_DIR/run_${MODE}_${ACCOUNT}_w${WORKER_ID}_${RESTART_COUNT}.log"
    echo "$(date -u '+%Y-%m-%d %H:%M:%SZ') WORKER $WORKER_ID RUN #$RESTART_COUNT log=$RUN_LOG" >> "$WATCHDOG_LOG"

    "$PYTHON" backtest_redzone_perpetual.py \
      --mode "$MODE" \
      --account "$ACCOUNT" \
      --start "$START" \
      --capital "$CAPITAL" \
      --symbols "$SYMBOLS" \
      --out-dir "$LOG_DIR" \
      --db "$LOG_DIR/results.sqlite" \
      --worker-id "$WORKER_ID" \
      --total-workers "$NUM_WORKERS" \
      > "$RUN_LOG" 2>&1
    local EXIT_CODE=$?

    echo "$(date -u '+%Y-%m-%d %H:%M:%SZ') WORKER $WORKER_ID RUN #$RESTART_COUNT EXITED code=$EXIT_CODE" >> "$WATCHDOG_LOG"

    sleep 30

    # Rotate old run logs (keep 5 most recent per worker)
    ls -t "$LOG_DIR"/run_${MODE}_${ACCOUNT}_w${WORKER_ID}_*.log 2>/dev/null | tail -n +6 | xargs -r rm -f
  done
}

# Launch N workers in parallel
PIDS=()
for WID in $(seq 0 $((NUM_WORKERS - 1))); do
  worker_loop "$WID" &
  PIDS+=($!)
  sleep 1
done

echo "$(date -u '+%Y-%m-%d %H:%M:%SZ') Launched $NUM_WORKERS workers PIDs=${PIDS[*]}" >> "$WATCHDOG_LOG"

# Wait forever (workers run in background)
wait

#!/bin/bash
# run_wt_backtest.sh — Wrapper that ensures backtest_wt_ultimate.py stays running
# Survives crashes, restarts, everything. Uses lock file to prevent duplicates.

WORKDIR="/Users/niels/Documents/binance"
if [ "$(uname)" = "Linux" ]; then
    WORKDIR="/home/niels/binance"
    PYTHON="/home/niels/.conda/envs/binance_env/bin/python"
else
    PYTHON="/opt/anaconda3/envs/binance_env/bin/python"
fi

LOGDIR="$(dirname "$WORKDIR")/logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/wt_backtest_runner.log"
PID_FILE="$WORKDIR/data/backtest_wt_ultimate/backtest.pid"
LOCK_FILE="$LOGDIR/.wt_backtest.lock"

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"
    # Rotate log at 5MB
    if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || stat -c%s "$LOG" 2>/dev/null)" -gt 5242880 ]; then
        mv "$LOG" "${LOG}.old"
    fi
}

# Prevent multiple runners
if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"
trap "rm -f '$LOCK_FILE'" EXIT

# Check if backtest is already running
if [ -f "$PID_FILE" ]; then
    BT_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$BT_PID" ] && kill -0 "$BT_PID" 2>/dev/null; then
        log "Backtest already running (PID $BT_PID)"
        exit 0
    fi
fi

log "Starting WaveTrend Ultimate Backtest"
cd "$WORKDIR"

# Activate conda if on Linux
if [ "$(uname)" = "Linux" ]; then
    source /home/niels/miniforge3/etc/profile.d/conda.sh 2>/dev/null
    conda activate binance_env 2>/dev/null
fi

# Run with nohup, output to /dev/null (script has its own logging)
nohup "$PYTHON" -u backtest_wt_ultimate.py --market both > /dev/null 2>&1 &
NEW_PID=$!
log "Launched backtest PID=$NEW_PID"

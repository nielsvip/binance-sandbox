#!/bin/bash
# V5 Crypto Sweep — Run on server with cron restart capability
# Add to crontab: @reboot /home/niels/binance/run_v5_sweep_server.sh
# Also: */5 * * * * /home/niels/binance/run_v5_sweep_server.sh
# The script checks for running instance before starting

LOCKFILE="/home/niels/binance/backtest_v5/sweep_master/server_sweep.lock"
PIDFILE="/home/niels/binance/backtest_v5/sweep_master/master.pid"
LOGFILE="/home/niels/binance/backtest_v5/sweep_master/cron_watchdog.log"
PYTHON="/home/niels/.conda/envs/binance_env/bin/python3"
SCRIPT="/home/niels/binance/backtest_v5_master.py"
WORKDIR="/home/niels/binance"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" >> "$LOGFILE"
}

# Check if already running
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        log "Master sweep already running (PID=$PID)"
        exit 0
    else
        log "Stale PID file found (PID=$PID was dead). Restarting."
        rm -f "$PIDFILE"
    fi
fi

# Create lock
mkdir -p "$(dirname "$LOCKFILE")"

log "Starting V5 crypto sweep"
cd "$WORKDIR"
nohup "$PYTHON" "$SCRIPT" --system crypto --resume >> "$LOGFILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
log "Started master sweep PID=$NEW_PID"

#!/bin/bash
# Options-shadow paper-variants runner.
#
# The installed Mac agent historically used KeepAlive=true. Keep this process
# alive and sleep outside market hours instead of exiting into a launchd restart
# loop. Each Python invocation is one bounded cycle, so a slow/heavy cycle is
# collected by macOS without taking down the supervisor or causing a --loop
# process to grow indefinitely.

set -u

BASE="/Users/niels/Documents/binance"
PY="/opt/anaconda3/envs/binance_env/bin/python"
INTERVAL_SECONDS=300

# Bound the supervisor's launchd log so a logger failure cannot take the paper
# collector down with ENOSPC.  Keep the last ~2,000 lines when it exceeds 10 MiB.
LOG_DIR="/Users/niels/logs"
SUPERVISOR_LOG="$LOG_DIR/options_shadow_stdout.log"
trim_log() {
    if [ -f "$SUPERVISOR_LOG" ] && [ "$(stat -f%z "$SUPERVISOR_LOG" 2>/dev/null || echo 0)" -gt 10485760 ]; then
        tail -n 2000 "$SUPERVISOR_LOG" > "$SUPERVISOR_LOG.trim" && mv "$SUPERVISOR_LOG.trim" "$SUPERVISOR_LOG"
    fi
}

in_market_hours() {
    local dow hour minute mins
    dow=$(date -u +%u)
    hour=$(date -u +%H)
    minute=$(date -u +%M)
    mins=$((10#$hour * 60 + 10#$minute))
    [ "$dow" -le 5 ] && [ "$mins" -ge 810 ] && [ "$mins" -lt 1200 ]
}

cd "$BASE" || exit 1
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] options shadow supervisor started (paper-only)"

while true; do
    trim_log
    if in_market_hours; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] shadow cycle start"
        "$PY" tradier_options_shadow_runner.py
        rc=$?
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] shadow cycle done rc=$rc"
    else
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] outside market hours; paper runner sleeping"
    fi
    sleep "$INTERVAL_SECONDS"
done

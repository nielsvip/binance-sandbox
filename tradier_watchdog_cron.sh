#!/bin/bash
# tradier_watchdog_cron.sh — Ensures Tradier system is alive before market open
# Runs via crontab at 9:20 ET (13:20 UTC) every weekday
# If any tradier process is down, launches start_everything_3.command headless
# Also runs every 5 min during market hours to verify health

WORKDIR="/Users/niels/Documents/binance"
PYTHON="/opt/anaconda3/envs/binance_env/bin/python"
LOGDIR="/Users/niels/logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/tradier_watchdog_cron.log"
WATCHDOG="$WORKDIR/run_with_watchdog.sh"

# Only run on weekdays (Mon=1 .. Fri=5)
DOW=$(date +%u)
if [ "$DOW" -gt 5 ]; then
    exit 0
fi

# Current ET time (for logging)
ET_TIME=$(TZ="America/New_York" date +"%H:%M")
ET_HOUR=$(TZ="America/New_York" date +"%H")
ET_MIN=$(TZ="America/New_York" date +"%M")
ET_MINS=$((ET_HOUR * 60 + ET_MIN))

# Only active 9:15 ET - 16:15 ET (market hours with buffer)
if [ "$ET_MINS" -lt 555 ] || [ "$ET_MINS" -gt 975 ]; then
    exit 0
fi

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [ET $ET_TIME] $1" >> "$LOG"
}

CRITICAL_SCRIPTS=(
    "tradier_prices.py"
    "tradier_positions.py"
    "tradier_indicators.py"
    "tradier_rankings.py"
    "tradier_manage.py.*trb"
)

ALL_OK=true
MISSING=""
for script in "${CRITICAL_SCRIPTS[@]}"; do
    if ! pgrep -f "$PYTHON.*$script" >/dev/null 2>&1; then
        ALL_OK=false
        MISSING="$MISSING $script"
    fi
done

if $ALL_OK; then
    # All processes running — just log heartbeat every 30 min
    LAST_HEARTBEAT="$LOGDIR/.tradier_watchdog_heartbeat"
    if [ ! -f "$LAST_HEARTBEAT" ] || [ $(( $(date +%s) - $(stat -f %m "$LAST_HEARTBEAT" 2>/dev/null || echo 0) )) -gt 1800 ]; then
        log "HEARTBEAT: All tradier processes healthy"
        touch "$LAST_HEARTBEAT"
    fi
    exit 0
fi

log "ALERT: Missing processes:$MISSING"

# Check if start_everything_3 is already running
if pgrep -f "start_everything_3" >/dev/null 2>&1; then
    log "start_everything_3 already running, waiting..."
    exit 0
fi

# Pre-market launch (9:15-9:30 ET) — full restart
if [ "$ET_MINS" -lt 570 ]; then
    log "PRE-MARKET LAUNCH: Starting full tradier system via start_everything_3.command"
    cd "$WORKDIR"
    # Launch headless (no iTerm required for cron)
    for full_cmd in "tradier_prices.py" "tradier_positions.py --accounts tra trb trc" "tradier_indicators.py" "tradier_rankings.py" "tradier_manage.py --accounts trb" "tradier_manage.py --accounts trc"; do
        script_name="${full_cmd%% *}"
        if pgrep -f "$PYTHON.*$script_name" >/dev/null 2>&1; then
            continue
        fi
        log "  Launching: $full_cmd"
        nohup bash "$WATCHDOG" $full_cmd >> "$LOGDIR/${script_name%.py}_watchdog.log" 2>&1 &
        sleep 2
    done
    sleep 10
    # Verify
    RUNNING=0
    for script in "${CRITICAL_SCRIPTS[@]}"; do
        if pgrep -f "$PYTHON.*$script" >/dev/null 2>&1; then
            RUNNING=$((RUNNING + 1))
        fi
    done
    log "POST-LAUNCH: $RUNNING/${#CRITICAL_SCRIPTS[@]} processes running"
    if [ "$RUNNING" -lt "${#CRITICAL_SCRIPTS[@]}" ]; then
        log "CRITICAL: Some processes failed to start! Manual intervention needed."
    fi
    exit 0
fi

# During market hours — targeted restart of missing processes only
log "MARKET HOURS RECOVERY: Restarting missing processes"
cd "$WORKDIR"
if ! pgrep -f "$PYTHON.*tradier_prices.py" >/dev/null 2>&1; then
    log "  Restarting tradier_prices.py"
    nohup bash "$WATCHDOG" tradier_prices.py >> "$LOGDIR/tradier_prices_watchdog.log" 2>&1 &
    sleep 3
fi
if ! pgrep -f "$PYTHON.*tradier_positions.py" >/dev/null 2>&1; then
    log "  Restarting tradier_positions.py"
    nohup bash "$WATCHDOG" tradier_positions.py --accounts tra trb trc >> "$LOGDIR/tradier_positions_watchdog.log" 2>&1 &
    sleep 3
fi
if ! pgrep -f "$PYTHON.*tradier_indicators.py" >/dev/null 2>&1; then
    log "  Restarting tradier_indicators.py"
    nohup bash "$WATCHDOG" tradier_indicators.py >> "$LOGDIR/tradier_indicators_watchdog.log" 2>&1 &
    sleep 3
fi
if ! pgrep -f "$PYTHON.*tradier_rankings.py" >/dev/null 2>&1; then
    log "  Restarting tradier_rankings.py"
    nohup bash "$WATCHDOG" tradier_rankings.py >> "$LOGDIR/tradier_rankings_watchdog.log" 2>&1 &
    sleep 3
fi
if ! pgrep -f "$PYTHON.*tradier_manage.py.*trb" >/dev/null 2>&1; then
    log "  Restarting tradier_manage.py --accounts trb"
    nohup bash "$WATCHDOG" tradier_manage.py --accounts trb >> "$LOGDIR/tradier_manage_watchdog.log" 2>&1 &
    sleep 3
fi
log "RECOVERY COMPLETE"

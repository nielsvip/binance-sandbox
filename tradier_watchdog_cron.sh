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
    "tradier_manage.py.*trc"
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

# Ignore start_everything_3 — always do targeted recovery of missing processes
# (start_everything_3 often stays alive as a dead shell, blocking recovery)

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
        nohup bash "$WATCHDOG" $full_cmd > /dev/null 2>&1 &
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
# IMPORTANT: Only restart if BOTH the python process AND any run_with_watchdog wrapper are dead.
# If a wrapper is alive, it will restart the python process itself — don't create a second wrapper.
log "MARKET HOURS RECOVERY: Checking missing processes"
cd "$WORKDIR"
TRADIER_RESTART_LIST=(
    "tradier_prices.py|tradier_prices.py"
    "tradier_positions.py|tradier_positions.py --accounts tra trb trc"
    "tradier_indicators.py|tradier_indicators.py"
    "tradier_rankings.py|tradier_rankings.py"
    "tradier_manage.py.*trb|tradier_manage.py --accounts trb"
    "tradier_manage.py.*trc|tradier_manage.py --accounts trc"
)
for entry in "${TRADIER_RESTART_LIST[@]}"; do
    pattern="${entry%%|*}"
    full_cmd="${entry##*|}"
    script_name="${full_cmd%% *}"
    if pgrep -f "$PYTHON.*$pattern" >/dev/null 2>&1; then
        continue  # Python process alive — all good
    fi
    if pgrep -f "run_with_watchdog.*$script_name" >/dev/null 2>&1; then
        log "  SKIP: $script_name — run_with_watchdog wrapper alive, will self-heal"
        continue
    fi
    log "  Restarting $full_cmd (both python + wrapper dead)"
    nohup bash "$WATCHDOG" $full_cmd > /dev/null 2>&1 &
    sleep 3
done
log "RECOVERY COMPLETE"

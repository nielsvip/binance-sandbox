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
export TRADIER_LOCAL_ONLY=1

# Only active Mon-Fri 13:00-20:00 UTC (2026-08-19: was 13:30 — tradier_prices pre-warms at 13:00 for 09:35 ET email).
UTC_TIME=$(date -u +"%H:%M")
DOW=$(date -u +%u)
UTC_HOUR=$(date -u +%H)
UTC_MIN=$(date -u +%M)
UTC_MINS=$((10#$UTC_HOUR * 60 + 10#$UTC_MIN))
if [ "$DOW" -gt 5 ] || [ "$UTC_MINS" -lt 780 ] || [ "$UTC_MINS" -ge 1200 ]; then
    exit 0
fi

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [UTC $UTC_TIME] $1" >> "$LOG"
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

# Opening launch (13:30-13:45 UTC) — full restart
if [ "$UTC_MINS" -lt 825 ]; then
    log "OPENING LAUNCH: Starting full tradier system via start_everything_3.command"
    cd "$WORKDIR"
    # Launch headless (no iTerm required for cron)
    for full_cmd in "tradier_prices.py" "tradier_positions.py --accounts tra trb trc" "tradier_indicators.py" "tradier_rankings.py" "tradier_manage.py --accounts trb" "tradier_manage.py --accounts trc"; do
        script_name="${full_cmd%% *}"
        if pgrep -f "$PYTHON.*$script_name" >/dev/null 2>&1; then
            continue
        fi
        log "  Launching: $full_cmd"
        nohup env TRADIER_LOCAL_ONLY=1 bash "$WATCHDOG" $full_cmd >> "$LOGDIR/wd_${script_name%.py}.out" 2>&1 &
        sleep 2
    done
    # 2026-08-13: was `sleep 10` — run_with_watchdog.sh:250-259 enforces
    # MIN_RESTART_INTERVAL=60 and logs "Waiting Ns before restart" BEFORE it ever
    # execs the python child, so at T+10s zero children exist yet. Verified
    # 2026-08-13: wrappers launched 13:30:01-13:30:12, watchdog logged
    # "Waiting 60s before restart" at 13:30:05 and "Starting tradier_rankings.py"
    # only at 13:31:05 — the 13:30:25 verify saw 0/6 and printed a false
    # "CRITICAL: Some processes failed to start", then 13:35 reported all healthy.
    # 80s clears the 60s floor plus interpreter startup. Verification only: this
    # sleep is AFTER every launch and changes no launch timing.
    sleep 80
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
    nohup env TRADIER_LOCAL_ONLY=1 bash "$WATCHDOG" $full_cmd >> "$LOGDIR/wd_${script_name%.py}.out" 2>&1 &
    sleep 3
done
log "RECOVERY COMPLETE"

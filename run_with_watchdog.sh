#!/bin/bash
# run_with_watchdog.sh - ULTRA ROBUST WATCHDOG FOR PYTHON SCRIPTS

# Configuration
WORKDIR="/Users/niels/Documents/binance"
PYTHON="/opt/anaconda3/envs/binance_env/bin/python"

# Use local logs if /Users/niels/logs is not writable
LOGDIR="/Users/niels/logs"
if [ ! -w "$LOGDIR" ]; then
    LOGDIR="$WORKDIR/logs"
    mkdir -p "$LOGDIR"
fi

# Timeouts and limits
TIMEOUT=0                 # DISABLED — never kill healthy processes. NO_OUTPUT_TIMEOUT handles stuck ones.
NO_OUTPUT_TIMEOUT=300     # 5 minutes default — overridden per-script below
MIN_RESTART_INTERVAL=3   # Minimum seconds between restarts
MAX_RAPID_RESTARTS=15      # Max restarts within rapid window
RAPID_WINDOW=300          # 5 minutes for rapid restart detection

# Parse arguments
if [ -z "$1" ]; then
    echo "Error: No script name provided"
    echo "Usage: $0 script_name.py [args...]"
    exit 1
fi

SCRIPT="$1"
shift
ARGS=("$@")

# ═══ SINGLETON GUARD: function called before every launch ═══
FULL_CMD="$SCRIPT ${ARGS[*]}"
MY_PID=$$
MY_PPID=$PPID
kill_duplicate_python() {
    local EXISTING_PIDS=""
    while IFS= read -r pid; do
        [ -z "$pid" ] && continue
        [ "$pid" = "$MY_PID" ] && continue
        [ "$pid" = "$MY_PPID" ] && continue
        EXISTING_PIDS="$EXISTING_PIDS $pid"
    done < <(pgrep -f "python.*-u $SCRIPT ${ARGS[*]}" 2>/dev/null || true)
    if [ -n "$EXISTING_PIDS" ]; then
        log "[SINGLETON] Killing duplicate python instances of '$FULL_CMD':$EXISTING_PIDS"
        for pid in $EXISTING_PIDS; do
            kill -TERM "$pid" 2>/dev/null || true
        done
        sleep 3
        for pid in $EXISTING_PIDS; do
            kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
        done
    fi
}
# Run singleton guard at startup (kills duplicate PYTHON children)
kill_duplicate_python

# Setup logging
mkdir -p "$LOGDIR"
SCRIPT_BASE="${SCRIPT%.py}"
# Extract --account value from ARGS for per-account log file names
ACCT_SUFFIX=""
for i in "${!ARGS[@]}"; do
    next=$((i+1))
    if [[ "${ARGS[$i]}" == "--account" || "${ARGS[$i]}" == "--accounts" ]] && [[ $next -lt ${#ARGS[@]} ]] && [[ -n "${ARGS[$next]}" ]]; then
        ACCT_SUFFIX="_${ARGS[$next]}"
        break
    fi
done

# ═══ WATCHDOG SINGLETON LOCK ═══
# Without this, two concurrent watchdogs for the same script+account fight
# each other: each runs kill_duplicate_python → SIGKILLs the other's child →
# 15 rapid restarts → both watchdogs give up. Caused men loop 2026-04-26 02:02-02:04.
WATCHDOG_LOCK="$LOGDIR/.watchdog_${SCRIPT_BASE}${ACCT_SUFFIX}.pid"
if [ -f "$WATCHDOG_LOCK" ]; then
    OLD_WD_PID=$(cat "$WATCHDOG_LOCK" 2>/dev/null)
    if [ -n "$OLD_WD_PID" ] && kill -0 "$OLD_WD_PID" 2>/dev/null; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] [WATCHDOG_SINGLETON] Another watchdog is alive (pid=$OLD_WD_PID) for ${SCRIPT_BASE}${ACCT_SUFFIX} — refusing to start to avoid SIGKILL fight" | tee -a "$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}_watchdog.log"
        exit 0
    fi
fi
echo "$$" > "$WATCHDOG_LOCK"
trap 'rm -f "$WATCHDOG_LOCK"; kill -TERM ${CHILD_PID:-0} 2>/dev/null; exit' EXIT INT TERM

APP_LOG="$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}_app.log"  # legacy, no longer written to
WD_LOG="$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}_watchdog.log"

# Per-script NO_OUTPUT_TIMEOUT and log file selection
# Each script has different output frequency — tight timeouts catch failures fast
case "$SCRIPT_BASE" in
    ez_indicators)       NO_OUTPUT_TIMEOUT=120  ;;  # writes every ~1s
    ez_indicators_merger) NO_OUTPUT_TIMEOUT=120  ;;  # writes every ~3s
    ez_market_data)      NO_OUTPUT_TIMEOUT=180  ;;  # writes every ~30s
    ez_crosses)          NO_OUTPUT_TIMEOUT=300  ;;  # writes every ~3min (signal save cycle)
    ez_rankings)         NO_OUTPUT_TIMEOUT=300  ;;  # writes every ~4s but init can be slow
    ez_prices|ez_prices_ws) NO_OUTPUT_TIMEOUT=600 ;;  # writes every ~3min, bursts
    ez_klines)           NO_OUTPUT_TIMEOUT=600  ;;  # scheduled every 5min
    ez_manage)           NO_OUTPUT_TIMEOUT=600  ;;  # quick_general writes every ~30-300s
    ez_share_ind)        NO_OUTPUT_TIMEOUT=600  ;;  # heartbeat every few minutes
    ez_mark_prices)      NO_OUTPUT_TIMEOUT=600  ;;  # periodic
    ez_orderbook)        NO_OUTPUT_TIMEOUT=0    ;;  # 2026-04-27: disabled — real heartbeat is Redis (orderbook:* keys), not the log file. The 600s log-mtime check was killing healthy processes every ~10 min in a loop.
    *)                   NO_OUTPUT_TIMEOUT=600  ;;  # safe default
esac

# Find the ACTUAL log file — check both locations (logs are in /Users/niels/logs/ on macbook)
PYTHON_LOG=""
for _candidate in "$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}.log" "$WORKDIR/logs/${SCRIPT_BASE}${ACCT_SUFFIX}.log"; do
    if [[ -f "$_candidate" ]]; then
        PYTHON_LOG="$_candidate"
        break
    fi
done
# For ez_manage, also check quick_general log (more reliable heartbeat)
if [[ "$SCRIPT_BASE" == "ez_manage" && -n "$ACCT_SUFFIX" ]]; then
    _quick_log="$LOGDIR/ez_positions_quick_general${ACCT_SUFFIX}.log"
    if [[ -f "$_quick_log" ]]; then
        PYTHON_LOG="$_quick_log"
    fi
fi
# If no log found yet, set expected path so watchdog can detect when it appears
if [[ -z "$PYTHON_LOG" ]]; then
    PYTHON_LOG="$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}.log"
fi

RESTART_TRACKER="$LOGDIR/.${SCRIPT_BASE}${ACCT_SUFFIX}_restarts"

# Logging function (with 5MB rotation)
WD_LOG_MAX=$((5 * 1024 * 1024))
log() {
    if [[ -f "$WD_LOG" ]]; then
        local sz
        sz=$(wc -c < "$WD_LOG" 2>/dev/null || echo 0)
        if (( sz > WD_LOG_MAX )); then
            mv -f "$WD_LOG" "${WD_LOG}.1" 2>/dev/null || true
        fi
    fi
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$WD_LOG"
}

# Function to check rapid restarts
check_rapid_restarts() {
    local now=$(date +%s)
    local count=0
    
    # Create restart tracker if it doesn't exist
    touch "$RESTART_TRACKER"
    
    # Remove old entries (older than rapid window)
    local cutoff=$((now - RAPID_WINDOW))
    awk -v cutoff="$cutoff" '$1 >= cutoff' "$RESTART_TRACKER" > "${RESTART_TRACKER}.tmp"
    mv "${RESTART_TRACKER}.tmp" "$RESTART_TRACKER"
    
    # Count recent restarts
    count=$(wc -l < "$RESTART_TRACKER" | tr -d ' ')
    
    # Add current restart
    echo "$now" >> "$RESTART_TRACKER"
    
    if [ "$count" -ge "$MAX_RAPID_RESTARTS" ]; then
        log "ERROR: Too many rapid restarts ($count in ${RAPID_WINDOW}s). Exiting to prevent crash loop."
        exit 1
    fi
    
    # Check minimum interval since last restart
    local last_restart=$(tail -n 2 "$RESTART_TRACKER" | head -n 1)
    if [ -n "$last_restart" ]; then
        local interval=$((now - last_restart))
        if [ "$interval" -lt "$MIN_RESTART_INTERVAL" ]; then
            log "Waiting $((MIN_RESTART_INTERVAL - interval))s before restart (min interval: ${MIN_RESTART_INTERVAL}s)"
            sleep $((MIN_RESTART_INTERVAL - interval))
        fi
    fi
}

# Function to run the script
run_script() {
    local start_time=$(date +%s)
    log "Starting $SCRIPT ${ARGS[*]}"

    # Scripts using Python logging write to their own log file — stdout to /dev/null
    # Exception: ez_klines uses print() so redirect stdout to its monitored log file
    if [[ "$SCRIPT_BASE" == "ez_klines" ]]; then
        "$PYTHON" -u "$SCRIPT" "${ARGS[@]}" >> "$PYTHON_LOG" 2>&1 &
    else
        "$PYTHON" -u "$SCRIPT" "${ARGS[@]}" > /dev/null 2>&1 &
    fi
    local script_pid=$!
    CHILD_PID=$script_pid
    
    # Monitor the script
    while kill -0 "$script_pid" 2>/dev/null; do
        sleep 5
        
        local now=$(date +%s)
        local runtime=$((now - start_time))
        
        # Check timeout
        if [ "$TIMEOUT" -gt 0 ] && [ "$runtime" -gt "$TIMEOUT" ]; then
            log "Timeout reached (${TIMEOUT}s). Restarting..."
            kill -TERM "$script_pid" 2>/dev/null || true
            sleep 2
            kill -KILL "$script_pid" 2>/dev/null || true
            break
        fi
        
        # Check for output timeout — re-resolve log file each cycle in case it appears later
        local _check_log="$PYTHON_LOG"
        if [[ ! -f "$_check_log" ]]; then
            # Try alternate locations
            for _try in "$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}.log" "$WORKDIR/logs/${SCRIPT_BASE}${ACCT_SUFFIX}.log"; do
                if [[ -f "$_try" ]]; then _check_log="$_try"; break; fi
            done
        fi
        if [[ "$SCRIPT" != "ez_vpn.py" && "$SCRIPT" != "ez_double.py" && -f "$_check_log" && "$runtime" -gt 120 ]]; then
            local last_mod
            if [[ "$(uname)" == "Darwin" ]]; then
                last_mod=$(stat -f "%m" "$_check_log" 2>/dev/null || echo 0)
            else
                last_mod=$(stat -c "%Y" "$_check_log" 2>/dev/null || echo 0)
            fi
            local no_output_time=$((now - last_mod))
            if [ "$NO_OUTPUT_TIMEOUT" -gt 0 ] && [ "$no_output_time" -gt "$NO_OUTPUT_TIMEOUT" ]; then
                log "⚠️ No output for ${no_output_time}s (limit: ${NO_OUTPUT_TIMEOUT}s) on ${_check_log}. Restarting..."
                kill -TERM "$script_pid" 2>/dev/null || true
                sleep 2
                kill -KILL "$script_pid" 2>/dev/null || true
                break
            fi
        fi
        # 2026-04-26: Preemptive RSS recycling for ez_manage workers — beat jetsam to the punch
        # with a graceful TERM so positions/locks save cleanly. Mac jetsam SIGKILLs at ~1GB+ when
        # whole-system memory pressure is high, losing in-flight state. Recycle at 900MB → 1.5GB
        # 2026-04-27: bumped 900MB → 1500MB. Live audit: inf was being recycled every 3-5 min
        # (RSS hit 900MB fast), causing constant cutouts user complained about. System has 36GB
        # RAM, 5 ez_manage workers ≈ 3.5GB total; 1.5GB ceiling per worker is plenty of headroom.
        # Apply to ez_orderbook too (long-running OB ingest, similar pattern).
        if [[ ( "$SCRIPT" == "ez_manage.py" || "$SCRIPT" == "ez_orderbook.py" ) && "$runtime" -gt 60 ]]; then
            local rss_kb
            if [[ "$(uname)" == "Darwin" ]]; then
                rss_kb=$(ps -p "$script_pid" -o rss= 2>/dev/null | tr -d ' ' || echo 0)
            else
                rss_kb=$(awk '/VmRSS/{print $2}' "/proc/$script_pid/status" 2>/dev/null || echo 0)
            fi
            local MAX_RSS_KB=1572864  # 1.5GB (was 900MB)
            if [[ -n "$rss_kb" && "$rss_kb" -gt "$MAX_RSS_KB" ]]; then
                log "🧹 RSS preemptive recycle: ${rss_kb}KB > ${MAX_RSS_KB}KB. Graceful restart before jetsam fires."
                kill -TERM "$script_pid" 2>/dev/null || true
                sleep 5
                kill -KILL "$script_pid" 2>/dev/null || true
                break
            fi
        fi
    done
    
    # Wait for process to exit
    wait "$script_pid" 2>/dev/null
    local exit_code=$?
    log "Script exited with code: $exit_code"
    return $exit_code
}

# Main watchdog loop
main() {
    log "=== WATCHDOG STARTED ==="
    log "Script: $SCRIPT"
    log "Args: ${ARGS[*]}"
    log "Timeout: ${TIMEOUT}s (0=disabled)"
    log "No-output timeout: ${NO_OUTPUT_TIMEOUT}s"
    log "Monitoring log: ${PYTHON_LOG}"
    
    # Verify script exists
    if [ ! -f "$WORKDIR/$SCRIPT" ]; then
        log "ERROR: Script not found: $WORKDIR/$SCRIPT"
        exit 1
    fi
    
    # Main restart loop
    while true; do
        # Check for rapid restarts
        check_rapid_restarts

        # Kill any duplicate python instances before launching
        kill_duplicate_python

        # Change to working directory
        cd "$WORKDIR" || {
            log "ERROR: Cannot change to $WORKDIR"
            exit 1
        }

        # Run the script
        run_script

        log "Script stopped. Restarting in 5 seconds..."
        sleep 5
    done
}

# Cleanup on exit (only on true exit, not SIGTERM of child)
cleanup() {
    log "Watchdog shutting down..."
    # Kill any remaining child processes
    pkill -P $$ 2>/dev/null || true
}

# SIGTERM to watchdog itself: kill child, let main loop restart it
CHILD_PID=""
handle_sigterm() {
    log "Watchdog received SIGTERM — killing child, will restart"
    if [ -n "$CHILD_PID" ] && kill -0 "$CHILD_PID" 2>/dev/null; then
        kill -TERM "$CHILD_PID" 2>/dev/null || true
        sleep 2
        kill -KILL "$CHILD_PID" 2>/dev/null || true
    fi
    # Do NOT exit — let the main loop handle restart
}

trap handle_sigterm TERM
trap cleanup EXIT

# Start the watchdog
main
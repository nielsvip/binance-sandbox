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
# Run singleton guard at startup
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

    # Python handles its own logging via RotatingFileHandler — redirect stdout to /dev/null
    "$PYTHON" -u "$SCRIPT" "${ARGS[@]}" > /dev/null 2>&1 &
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
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
TIMEOUT=10800             # 180 minutes max runtime before restart
NO_OUTPUT_TIMEOUT=1800    # 30 minutes without output triggers restart
MIN_RESTART_INTERVAL=3   # Minimum seconds between restarts
MAX_RAPID_RESTARTS=100     # Max restarts within rapid window
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

# Setup logging
mkdir -p "$LOGDIR"
SCRIPT_BASE="${SCRIPT%.py}"
# Extract --account value from ARGS for per-account log file names
ACCT_SUFFIX=""
for i in "${!ARGS[@]}"; do
    next=$((i+1))
    if [[ "${ARGS[$i]}" == "--account" ]] && [[ $next -lt ${#ARGS[@]} ]] && [[ -n "${ARGS[$next]}" ]]; then
        ACCT_SUFFIX="_${ARGS[$next]}"
        break
    fi
done
APP_LOG="$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}_app.log"
WD_LOG="$LOGDIR/${SCRIPT_BASE}${ACCT_SUFFIX}_watchdog.log"
RESTART_TRACKER="$LOGDIR/.${SCRIPT_BASE}${ACCT_SUFFIX}_restarts"

# Logging function
log() {
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

# Rotate APP_LOG if larger than 20MB
rotate_app_log() {
    local max_bytes=$((20 * 1024 * 1024))
    if [ -f "$APP_LOG" ]; then
        local size
        size=$(stat -f%z "$APP_LOG" 2>/dev/null || stat -c%s "$APP_LOG" 2>/dev/null || echo 0)
        if [ "$size" -gt "$max_bytes" ]; then
            local prev="${APP_LOG%.log}.prev.log"
            tail -n 5000 "$APP_LOG" > "$prev" 2>/dev/null || true
            : > "$APP_LOG"
            log "APP_LOG rotated (was ${size} bytes). Last 5000 lines saved to $(basename $prev)."
        fi
    fi
}

# Function to run the script
run_script() {
    local start_time=$(date +%s)
    rotate_app_log
    touch "$APP_LOG"
    log "Starting $SCRIPT ${ARGS[*]}"

    # Stream log output
    tail -n 0 -F "$APP_LOG" &
    local tail_pid=$!

    # Start the script with unbuffered output
    "$PYTHON" -u "$SCRIPT" "${ARGS[@]}" >> "$APP_LOG" 2>&1 &
    local script_pid=$!
    
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
        
        # Check for output timeout (skip for certain scripts)
        # Add 300s (5m) grace period after start to allow slow initializations
        if [[ "$SCRIPT" != "ez_vpn.py" && "$SCRIPT" != "ez_double.py" && -f "$APP_LOG" && "$runtime" -gt 300 ]]; then
            local last_mod
            if [[ "$(uname)" == "Darwin" ]]; then
                last_mod=$(stat -f "%m" "$APP_LOG" 2>/dev/null || echo 0)
            else
                last_mod=$(stat -c "%Y" "$APP_LOG" 2>/dev/null || echo 0)
            fi
            local no_output_time=$((now - last_mod))
            if [ "$NO_OUTPUT_TIMEOUT" -gt 0 ] && [ "$no_output_time" -gt "$NO_OUTPUT_TIMEOUT" ]; then
                log "No output for ${no_output_time}s (limit: ${NO_OUTPUT_TIMEOUT}s). Restarting..."
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

    kill "$tail_pid" 2>/dev/null || true
    wait "$tail_pid" 2>/dev/null
    
    log "Script exited with code: $exit_code"
    return $exit_code
}

# Main watchdog loop
main() {
    log "=== WATCHDOG STARTED ==="
    log "Script: $SCRIPT"
    log "Args: ${ARGS[*]}"
    log "Timeout: ${TIMEOUT}s"
    log "No-output timeout: ${NO_OUTPUT_TIMEOUT}s"
    
    # Verify script exists
    if [ ! -f "$WORKDIR/$SCRIPT" ]; then
        log "ERROR: Script not found: $WORKDIR/$SCRIPT"
        exit 1
    fi
    
    # Main restart loop
    while true; do
        # Check for rapid restarts
        check_rapid_restarts
        
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

# Cleanup on exit
cleanup() {
    log "Watchdog shutting down..."
    # Kill any remaining child processes
    pkill -P $$ 2>/dev/null || true
}

trap cleanup EXIT

# Start the watchdog
main
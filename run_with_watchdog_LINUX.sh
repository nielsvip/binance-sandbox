#!/bin/bash
# Watchdog Script - Consolidated Version

# --- Configuration ---
TARGET_SCRIPT_NAME="$1"
# Capture all arguments after the script name for the Python script
PYTHON_SCRIPT_ARGS=("${@:2}")

if [[ -z "$TARGET_SCRIPT_NAME" ]]; then
    # log_wd cannot be used yet, and this output goes to systemd journal
    echo "$(date +'%Y-%m-%d %H:%M:%S') | WD_CRITICAL_ERROR | Usage: $0 <script_name.py> [args...]. Exiting."
    exit 1 # Critical failure, script cannot run
fi

# --- Prevent Multiple Instances (EARLY - before any initialization) ---
SCRIPT_NAME_BASE="${TARGET_SCRIPT_NAME%.py}" # e.g., "ez_indicators"
UID_TAG=$(id -u)
# Extract account name early for lock file
ACCOUNT_NAME=""
for arg in "${PYTHON_SCRIPT_ARGS[@]}"; do
    if [[ "$arg" == "--account" ]]; then
        continue
    elif [[ "$arg" =~ ^--account= ]]; then
        ACCOUNT_NAME="${arg#--account=}"
        break
    elif [[ "$arg" != "--account" ]] && [[ "$ACCOUNT_NAME" == "" ]] && [[ "$arg" =~ ^[a-z]+$ ]]; then
        ACCOUNT_NAME="$arg"
        break
    fi
done
# Also extract --worker N for worker-partitioned scripts (e.g. ez_indicators.py --worker 0)
if [[ -z "$ACCOUNT_NAME" ]]; then
    _prev_warg=""
    for _warg in "${PYTHON_SCRIPT_ARGS[@]}"; do
        if [[ "$_prev_warg" == "--worker" ]]; then
            ACCOUNT_NAME="worker${_warg}"
            break
        fi
        _prev_warg="$_warg"
    done
fi
WATCHDOG_LOCK_FILE="/tmp/watchdog_lock_${SCRIPT_NAME_BASE}${ACCOUNT_NAME:+_${ACCOUNT_NAME}}}.lock"
if [[ -f "$WATCHDOG_LOCK_FILE" ]]; then
    EXISTING_PID=$(cat "$WATCHDOG_LOCK_FILE" 2>/dev/null || echo "")
    if [[ -n "$EXISTING_PID" ]] && ps -p "$EXISTING_PID" > /dev/null 2>&1; then
        echo "$(date +'%Y-%m-%d %H:%M:%S') | WD_CRITICAL(${TARGET_SCRIPT_NAME}) | Another watchdog instance (PID $EXISTING_PID) is already running! Exiting."
        exit 1
    else
        rm -f "$WATCHDOG_LOCK_FILE"
    fi
fi
echo $$ > "$WATCHDOG_LOCK_FILE"
trap "rm -f \"$WATCHDOG_LOCK_FILE\"" EXIT

# Try to find Python - check common locations
if [ -f "/home/niels/.conda/envs/binance_env/bin/python" ]; then
    PYTHON_EXECUTABLE="/home/niels/.conda/envs/binance_env/bin/python"
elif [ -f "/usr/bin/python3" ]; then
    PYTHON_EXECUTABLE="/usr/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_EXECUTABLE=$(command -v python3)
else
    PYTHON_EXECUTABLE="python3"
fi

BASE_DIR="/home/niels/binance" # Where Python scripts reside
SCRIPT_PATH="${BASE_DIR}/${TARGET_SCRIPT_NAME}"
# SCRIPT_NAME_BASE and ACCOUNT_NAME already defined earlier for lock file

# Watchdog logs go here; Python scripts manage their own logs via RotatingFileHandler
LOG_DIR="/home/niels/logs"

# Only append account name to log files if we actually have an account
if [[ -n "$ACCOUNT_NAME" ]]; then
    WATCHDOG_LOG_FILE="${LOG_DIR}/watchdog_${SCRIPT_NAME_BASE}_${ACCOUNT_NAME}.log"
    # Point at Python's RotatingFileHandler output for liveness monitoring (NOT stdout capture)
    PYTHON_SCRIPT_LOG_FILE="${BASE_DIR}/logs/${SCRIPT_NAME_BASE}_${ACCOUNT_NAME}.log"
    FLAPPING_RECORD_FILE="${LOG_DIR}/.watchdog_restarts_${SCRIPT_NAME_BASE}_${ACCOUNT_NAME}.txt" # Hidden file
    PID_FILE="/tmp/watchdog_pid_${SCRIPT_NAME_BASE}_${ACCOUNT_NAME}_${UID_TAG}.txt" # Store PID of monitored Python script
    PID_FILE_ALT="${BASE_DIR}/pids/watchdog_pid_${SCRIPT_NAME_BASE}_${ACCOUNT_NAME}_${UID_TAG}.txt"
else
    WATCHDOG_LOG_FILE="${LOG_DIR}/watchdog_${SCRIPT_NAME_BASE}.log"
    PYTHON_SCRIPT_LOG_FILE="${BASE_DIR}/logs/${SCRIPT_NAME_BASE}.log"
    FLAPPING_RECORD_FILE="${LOG_DIR}/.watchdog_restarts_${SCRIPT_NAME_BASE}.txt" # Hidden file
    PID_FILE="/tmp/watchdog_pid_${SCRIPT_NAME_BASE}_${UID_TAG}.txt" # Store PID of monitored Python script
    PID_FILE_ALT="${BASE_DIR}/pids/watchdog_pid_${SCRIPT_NAME_BASE}_${UID_TAG}.txt"
fi

mkdir -p "${BASE_DIR}/pids" 2>/dev/null || true
if [[ -e "$PID_FILE" ]] && [[ ! -w "$PID_FILE" ]]; then
    PID_FILE="$PID_FILE_ALT"
fi

# --- Behavior Configuration ---
MAX_MEMORY_MB=1500
MAX_RUN_TIME_SEC=$((3 * 3600))      # 3 hours
NO_OUTPUT_TIMEOUT_SEC=$((15 * 60))  # 15 minutes (script must write to its PYTHON_SCRIPT_LOG_FILE)
SCRIPT_STARTUP_GRACE_SEC=$((2 * 60)) # 2 minutes grace for script to produce first output

# Flapping detection
FLAPPING_TIME_FRAME_SEC=$((15 * 60)) # 15 minutes
MAX_RESTARTS_IN_FRAME=5 # Allow 5 restarts in the frame before declaring flapping
WATCHDOG_SELF_FLAP_GRACE_SEC=$((5 * 60)) # 5 min: Watchdog itself won't exit due to flapping soon after it starts

POLL_INTERVAL_SEC=30 # How often the inner monitoring loop checks the script
RESTART_DELAY_SEC=10 # Delay before restarting a script
KILL_WAIT_SEC=10     # How long to wait after SIGTERM before SIGKILL

UNAME_S=$(uname -s)
if [[ "$UNAME_S" == "Darwin" ]]; then
    STAT_CMD=(stat -f %m)
else
    STAT_CMD=(stat -L -c %Y)
fi

# --- State Variables ---
_CURRENT_PYTHON_PID=0 # Holds the PID of the Python script launched by this watchdog instance
_WATCHDOG_START_TIME=$(date +%s) # Epoch time when this watchdog script started

# --- Logging function for Watchdog ---
# Must be defined early, especially before TRAP.
WATCHDOG_LOG_MAX_BYTES=$((5 * 1024 * 1024)) # 5MB max for watchdog's own log
log_wd() {
    local msg
    if [[ -n "$ACCOUNT_NAME" ]]; then
        msg="$(date +'%Y-%m-%d %H:%M:%S') | WD(${TARGET_SCRIPT_NAME} ${ACCOUNT_NAME}) | $1"
    else
        msg="$(date +'%Y-%m-%d %H:%M:%S') | WD(${TARGET_SCRIPT_NAME}) | $1"
    fi
    # Rotate watchdog log if over 5MB
    if [[ -f "$WATCHDOG_LOG_FILE" ]]; then
        local sz
        sz=$(wc -c < "$WATCHDOG_LOG_FILE" 2>/dev/null || echo 0)
        if (( sz > WATCHDOG_LOG_MAX_BYTES )); then
            mv -f "$WATCHDOG_LOG_FILE" "${WATCHDOG_LOG_FILE}.1" 2>/dev/null || true
        fi
    fi
    echo "$msg" | tee -a "$WATCHDOG_LOG_FILE"
}

ensure_writable_file() {
    local target="$1"
    touch "$target" 2>/dev/null && return 0
    if [[ -e "$target" ]]; then rm -f "$target" 2>/dev/null || true; fi
    touch "$target" 2>/dev/null && { chmod 664 "$target" 2>/dev/null || true; return 0; }
    log_wd "CRITICAL_ERROR: Unable to ensure writable file $target in $LOG_DIR."
    return 1
}

# --- Ensure log directory and initial files exist ---
# These actions must succeed for the watchdog to operate.
if ! mkdir -p "$LOG_DIR"; then
    # If mkdir fails, log_wd might not work if WATCHDOG_LOG_FILE is in LOG_DIR.
    # Echo directly, this will go to systemd journal.
    echo "$(date +'%Y-%m-%d %H:%M:%S') | WD_CRITICAL_ERROR(${TARGET_SCRIPT_NAME}) | Failed to create LOG_DIR: $LOG_DIR. Exiting."
    exit 1 # Critical failure
fi
# Touch files to ensure they exist and have correct initial ownership if created now.
# This command failing means fundamental permission issues.
for init_file in "$WATCHDOG_LOG_FILE" "$FLAPPING_RECORD_FILE"; do
    ensure_writable_file "$init_file" || exit 1
done
# PYTHON_SCRIPT_LOG_FILE now points to Python's RotatingFileHandler output — don't touch/create it here

# --- Trap for Watchdog Script's Own Exit ---
cleanup_and_exit() {
    local original_exit_code=$?
    log_wd "☢️ Watchdog script self-exiting (PID $$ with original code: $original_exit_code)."

    if [[ "$_CURRENT_PYTHON_PID" -ne 0 ]]; then
        if ps -p "$_CURRENT_PYTHON_PID" > /dev/null 2>&1; then # Only try if PID existed a moment ago
            log_wd "ℹ️ Watchdog attempting to stop monitored Python script PID: $_CURRENT_PYTHON_PID before exiting."
            kill -SIGTERM "$_CURRENT_PYTHON_PID" 2>/dev/null || true; sleep 1
            if ps -p "$_CURRENT_PYTHON_PID" > /dev/null 2>&1; then
                 kill -SIGKILL "$_CURRENT_PYTHON_PID" 2>/dev/null || true
            fi
        else
            log_wd "ℹ️ Monitored Python script PID $_CURRENT_PYTHON_PID was already gone."
        fi
    fi
    
    if [[ -f "$PID_FILE" ]]; then
        local pid_in_file
        pid_in_file=$(cat "$PID_FILE" 2>/dev/null || echo "0")
        if [[ "$pid_in_file" == "$_CURRENT_PYTHON_PID" ]] || [[ "$_CURRENT_PYTHON_PID" -eq 0 ]]; then
            rm -f "$PID_FILE" 2>/dev/null || true
        else
            log_wd "ℹ️ PID file $PID_FILE content '$pid_in_file' does not match expected PID $_CURRENT_PYTHON_PID (or 0). Not removing during this trap."
        fi
    fi
    log_wd "☢️ Watchdog cleanup sequence complete."
    exit "$original_exit_code" # Exit with the original captured code
}
# Trap EXIT (any exit), SIGINT (Ctrl+C from terminal), SIGTERM (systemctl stop)
trap cleanup_and_exit EXIT SIGINT SIGTERM

# Activate "exit on error" AFTER basic setup (log_wd, trap) is in place.
set -e

# --- Helper Functions ---

is_special_script() {
    # This function's return code is used by 'if' statements, which is safe with 'set -e'.
    # For 'ez_prices.py', this will return 0 (true). For others, 1 (false).
    [[ "$TARGET_SCRIPT_NAME" == "ez_prices.py" ]] || [[ "$TARGET_SCRIPT_NAME" == "ez_share_ind.py" ]]
}

check_flapping_and_record_restart() {
    local current_time # 'local' is fine inside functions
    current_time=$(date +%s)

    # Watchdog self-grace period for flapping
    if (( current_time - _WATCHDOG_START_TIME < WATCHDOG_SELF_FLAP_GRACE_SEC )); then
        log_wd "🕊️ Watchdog flapping check: Watchdog self-grace period active (${WATCHDOG_SELF_FLAP_GRACE_SEC}s)."
        # Record the attempt even in grace period, but don't prevent start if flapping limit reached during grace
    fi

    local recent_timestamps=() # Array
    local count_restarts=0
    local temp_flap_file # For atomic update of the record file
    temp_flap_file=$(mktemp) # Creates a temporary file

    # Read, filter, and count recent restart timestamps
    if [[ -f "$FLAPPING_RECORD_FILE" ]]; then
        # Ensure reading line by line correctly
        while IFS= read -r ts_line || [[ -n "$ts_line" ]]; do # Process even if last line has no newline
            if [[ "$ts_line" =~ ^[0-9]+$ ]] && (( current_time - ts_line <= FLAPPING_TIME_FRAME_SEC )); then
                recent_timestamps+=("$ts_line")
                echo "$ts_line" >> "$temp_flap_file"
            fi
        done < "$FLAPPING_RECORD_FILE"
    fi
    count_restarts=${#recent_timestamps[@]}

    # Check if flapping limit reached
    if (( count_restarts >= MAX_RESTARTS_IN_FRAME )); then
        # If in watchdog's own grace period, log flapping but allow start
        if (( current_time - _WATCHDOG_START_TIME < WATCHDOG_SELF_FLAP_GRACE_SEC )); then
            log_wd "🐦 Flapping detected ($count_restarts/$MAX_RESTARTS_IN_FRAME restarts) but watchdog is in self-grace. Allowing start."
        else
            log_wd "❌ FLAPPING DETECTED! $count_restarts/$MAX_RESTARTS_IN_FRAME restarts for $TARGET_SCRIPT_NAME within $FLAPPING_TIME_FRAME_SEC seconds."
            log_wd "   To reset, clear or delete $FLAPPING_RECORD_FILE or wait for timestamps to age out."
            # Record this attempt that led to flapping exit, then exit
            echo "$current_time" >> "$temp_flap_file"
            mv "$temp_flap_file" "$FLAPPING_RECORD_FILE" # Save updated list
            exit 10 # Specific exit code for flapping; will trigger EXIT trap
        fi
    fi

    # Record current restart attempt by appending to temp file, then moving
    echo "$current_time" >> "$temp_flap_file"
    mv "$temp_flap_file" "$FLAPPING_RECORD_FILE" # Atomic update
    count_restarts=$((count_restarts + 1)) # Reflect the upcoming start in the log message

    log_wd "✅ Flapping check passed ($count_restarts/${MAX_RESTARTS_IN_FRAME} restarts in last $FLAPPING_TIME_FRAME_SEC sec). Proceeding with start."
    return 0 # Success
}

start_python_script() {
    # Flapping check also records the restart attempt if it passes
    if ! check_flapping_and_record_restart; then
        # This should ideally not be reached if check_flapping exits on detection
        log_wd "🛑 Flapping prevented start of $TARGET_SCRIPT_NAME. Watchdog will cycle."
        # check_flapping_and_record_restart would have exited with code 10
        return 1 # Indicate failure to start
    fi

    log_wd "🚀 Starting $TARGET_SCRIPT_NAME with args: ${PYTHON_SCRIPT_ARGS[*]}"
    log_wd "   Python Exec: $PYTHON_EXECUTABLE"
    log_wd "   Script Path: $SCRIPT_PATH"
    log_wd "   Output Log: $PYTHON_SCRIPT_LOG_FILE"

    # Python scripts handle their own logging via RotatingFileHandler.
    # Redirect stdout/stderr to /dev/null — DO NOT append to a file here (causes unbounded growth).
    nohup "$PYTHON_EXECUTABLE" -u "$SCRIPT_PATH" "${PYTHON_SCRIPT_ARGS[@]}" > /dev/null 2>&1 &
    _CURRENT_PYTHON_PID=$! # Capture PID of the backgrounded Python script

    sleep 0.5 # Brief pause to allow script to fail fast (e.g., Python exec not found, script not found)

    # Verify if PID was obtained and process seems to be running
    # 'ps -p' is more reliable than 'kill -0' for checking if process exists for scripting
    if [[ -z "$_CURRENT_PYTHON_PID" ]] || ! ps -p "$_CURRENT_PYTHON_PID" > /dev/null 2>&1; then
        log_wd "❌ ERROR: Failed to start $TARGET_SCRIPT_NAME or it exited immediately."
        log_wd "   PID obtained was '$_CURRENT_PYTHON_PID'."
        log_wd "   Check $PYTHON_SCRIPT_LOG_FILE for Python errors (e.g., ModuleNotFound, syntax errors)."
        _CURRENT_PYTHON_PID=0 # Clear PID as it's invalid or dead
        return 1 # Indicate failure to start
    fi

    # Write the valid PID to the PID file
    # This command can fail if /tmp is not writable, but that's a system issue.
    echo "$_CURRENT_PYTHON_PID" > "$PID_FILE"
    log_wd "   $TARGET_SCRIPT_NAME started with PID $_CURRENT_PYTHON_PID."
    return 0 # Indicate success
}

kill_python_script() {
    local pid_to_kill=$1
    local reason="$2"

    if ! ps -p "$pid_to_kill" > /dev/null 2>&1; then
        log_wd "ℹ️ Process $pid_to_kill (for $TARGET_SCRIPT_NAME) already gone when attempting to kill ($reason)."
        _CURRENT_PYTHON_PID=0 # Ensure global state reflects this
        rm -f "$PID_FILE" 2>/dev/null || true    # Clean up PID file if process is confirmed gone
        return
    fi

    log_wd "⚠️ Killing PID $pid_to_kill ($TARGET_SCRIPT_NAME due to $reason)... Sending SIGTERM."
    # Try to kill process group first (if parent is group leader), then specific PID.
    # The '|| true' ensures set -e doesn't exit if kill fails (e.g. process died between ps and kill)
    kill -SIGTERM -- "-$pid_to_kill" 2>/dev/null || kill -SIGTERM "$pid_to_kill" 2>/dev/null || true

    local count=0
    # Wait for process to disappear
    while ps -p "$pid_to_kill" > /dev/null 2>&1; do
        if (( count >= KILL_WAIT_SEC )); then
            log_wd "🛑 PID $pid_to_kill did not respond to SIGTERM. Sending SIGKILL."
            kill -SIGKILL -- "-$pid_to_kill" 2>/dev/null || kill -SIGKILL "$pid_to_kill" 2>/dev/null || true
            sleep 1 # Give OS time to process SIGKILL
            break   # Exit wait loop
        fi
        sleep 1
        ((count++))
    done

    if ps -p "$pid_to_kill" > /dev/null 2>&1; then
        log_wd "⛔️ FAILED to confirm stop of PID $pid_to_kill ($TARGET_SCRIPT_NAME for $reason) even with SIGKILL."
    else
        log_wd "✅ Process $pid_to_kill ($TARGET_SCRIPT_NAME for $reason) confirmed stopped."
    fi
    _CURRENT_PYTHON_PID=0 # Clear global PID tracker
    rm -f "$PID_FILE" 2>/dev/null || true    # Clean up PID file
}

check_memory_usage() {
    local pid_to_check=$1
    if is_special_script; then return 0; fi # Skip for special scripts

    local mem_kb mem_mb
    # `ps` can fail if pid_to_check just died; `|| echo 0` makes it safe for `set -e`
    mem_kb=$(ps -o rss= -p "$pid_to_check" 2>/dev/null || echo 0) # RSS in kilobytes
    mem_mb=$((mem_kb / 1024))

    log_wd " DBUG: Mem usage for PID $pid_to_check: ${mem_mb}MB / ${MAX_MEMORY_MB}MB"
    if (( mem_mb > MAX_MEMORY_MB )); then
        log_wd "🔴 Memory usage high for $TARGET_SCRIPT_NAME (PID $pid_to_check): ${mem_mb}MB > ${MAX_MEMORY_MB}MB."
        kill_python_script "$pid_to_check" "High memory usage"
        return 1 # Indicate a kill occurred
    fi
    return 0 # Memory OK
}

check_max_runtime() {
    local pid_to_check=$1
    local script_start_epoch=$2 # Start time of this specific Python script instance
    if is_special_script; then return 0; fi # Skip

    local current_time runtime
    current_time=$(date +%s)
    runtime=$((current_time - script_start_epoch))

    log_wd " DBUG: Runtime for PID $pid_to_check: ${runtime}s / ${MAX_RUN_TIME_SEC}s"
    if (( runtime > MAX_RUN_TIME_SEC )); then
        log_wd "🔴 Max runtime exceeded for $TARGET_SCRIPT_NAME (PID $pid_to_check): ${runtime}s > ${MAX_RUN_TIME_SEC}s."
        kill_python_script "$pid_to_check" "Max runtime exceeded"
        return 1 # Indicate a kill occurred
    fi
    return 0 # Runtime OK
}

check_no_output() {
    local pid_to_check=$1
    local script_start_epoch=$2 # Start time of this specific Python script instance
    if is_special_script; then return 0; fi # Skip

    local current_time time_since_script_start
    current_time=$(date +%s)
    time_since_script_start=$((current_time - script_start_epoch))

    # Grace period for script to produce initial output
    if (( time_since_script_start < SCRIPT_STARTUP_GRACE_SEC )); then
        log_wd " DBUG: No-output check for PID $pid_to_check: Script in startup grace (${time_since_script_start}s / ${SCRIPT_STARTUP_GRACE_SEC}s)."
        return 0
    fi

    # Check modification time of the Python script's dedicated log file
    local last_log_mod_time time_since_last_log
    # `stat` can fail if file was just deleted; `|| echo 0` makes it safe
    last_log_mod_time=$("${STAT_CMD[@]}" "$PYTHON_SCRIPT_LOG_FILE" 2>/dev/null || echo 0)

    if [[ "$last_log_mod_time" -eq 0 ]]; then
        # File doesn't exist (unexpected after startup grace) or stat failed.
        # This could be an issue if script should be running and logging.
        log_wd " DBUG: No-output check for PID $pid_to_check: Python log $PYTHON_SCRIPT_LOG_FILE not found or stat failed after grace."
        # If enough time has passed beyond grace, this is a problem.
        if (( time_since_script_start > ( SCRIPT_STARTUP_GRACE_SEC + NO_OUTPUT_TIMEOUT_SEC / 2 ) )); then
             log_wd "🔴 No output: Python log $PYTHON_SCRIPT_LOG_FILE for PID $pid_to_check has no content/modification after extended period."
             kill_python_script "$pid_to_check" "No output (log file missing/stale)"
             return 1 # Indicate a kill occurred
        fi
        return 0 # Give it more time if it's not excessively long
    fi

    time_since_last_log=$((current_time - last_log_mod_time))
    log_wd " DBUG: No-output check for PID $pid_to_check: ${time_since_last_log}s since last output to $PYTHON_SCRIPT_LOG_FILE (timeout ${NO_OUTPUT_TIMEOUT_SEC}s)."

    if (( time_since_last_log > NO_OUTPUT_TIMEOUT_SEC )); then
        log_wd "🔴 No output for $TARGET_SCRIPT_NAME (PID $pid_to_check): ${time_since_last_log}s > ${NO_OUTPUT_TIMEOUT_SEC}s since last update to $PYTHON_SCRIPT_LOG_FILE."
        kill_python_script "$pid_to_check" "No output (log file stale)"
        return 1 # Indicate a kill occurred
    fi
    return 0 # Output recently seen
}
# --- End Helper Functions ---


# --- Main Logic Start ---
# Lock file already created earlier to prevent multiple instances
log_wd "✅ Watchdog initialized for $TARGET_SCRIPT_NAME (Account: ${ACCOUNT_NAME}, Watchdog PID: $$)."
log_wd "   PYTHON_EXECUTABLE: $PYTHON_EXECUTABLE"
log_wd "   BASE_DIR: $BASE_DIR, LOG_DIR: $LOG_DIR"
log_wd "   Will monitor Python script PID from $PID_FILE"

if is_special_script; then
    log_wd "ℹ️ '$TARGET_SCRIPT_NAME' is a special script. Resource/timeout checks will be skipped."
fi

# Initial cleanup of a potentially stale PID file from a previous watchdog instance
# This block is now safe with 'set -e'
log_wd " DBUG: Checking for stale PID file: $PID_FILE"
if [[ -f "$PID_FILE" ]]; then
    # File exists, read its content. If cat fails or file empty, STALE_PID is empty.
    STALE_PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
    log_wd " DBUG: Stale PID file $PID_FILE found. Content: '$STALE_PID'"
    if [[ -n "$STALE_PID" ]]; then # If STALE_PID is not empty
        # Check if this PID is actually running. 'ps' in 'if' is safe with set -e.
        if ps -p "$STALE_PID" > /dev/null 2>&1; then
            log_wd "⚠️ Stale PID $STALE_PID (for $TARGET_SCRIPT_NAME) found RUNNING from $PID_FILE. Attempting to stop it."
            # The '|| true' ensures set -e is not triggered if kill fails (e.g., process died just now)
            kill -SIGTERM "$STALE_PID" 2>/dev/null || true; sleep 2
            if ps -p "$STALE_PID" > /dev/null 2>&1; then # Check again before SIGKILL
                kill -SIGKILL "$STALE_PID" 2>/dev/null || true
            fi
            log_wd "ℹ️ Stale PID $STALE_PID handling complete."
        else
            log_wd " DBUG: Stale PID $STALE_PID (from $PID_FILE) was NOT running or was invalid."
        fi
    else
        log_wd " DBUG: Stale PID file $PID_FILE was empty."
    fi
    log_wd " DBUG: Removing stale PID file: $PID_FILE (after processing content)."
    rm -f "$PID_FILE" 2>/dev/null || true # Remove the stale PID file
else
    log_wd " DBUG: No stale PID file found at $PID_FILE."
fi
log_wd " DBUG: Stale PID check complete."


# --- Main Execution Loop ---
main_loop_iteration=0
while true; do
    main_loop_iteration=$((main_loop_iteration + 1)) # Safe increment with set -e
    log_wd "--- Main Loop Iteration: $main_loop_iteration for $TARGET_SCRIPT_NAME (${ACCOUNT_NAME}) ---"

    if ! start_python_script; then
        # start_python_script logs its own errors and handles flapping exit
        log_wd "↪️ Python script $TARGET_SCRIPT_NAME failed to start. Watchdog will retry after $RESTART_DELAY_SEC sec delay."
        # _CURRENT_PYTHON_PID should be 0 if start_python_script failed
        # PID_FILE should also be cleaned by start_python_script or here for robustness
        rm -f "$PID_FILE" 2>/dev/null || true
        sleep "$RESTART_DELAY_SEC"
        continue # Go to next iteration of the main while true loop to restart
    fi

    # If start_python_script succeeded, _CURRENT_PYTHON_PID is set.
    SCRIPT_ACTUAL_START_TIME=$(date +%s) # For this instance of the Python script
    log_wd "DBUG: $TARGET_SCRIPT_NAME (PID $_CURRENT_PYTHON_PID) started. Actual start epoch: $SCRIPT_ACTUAL_START_TIME. Entering monitoring."

    # Inner monitoring loop for the current Python script instance
    while true; do
        # Variable for PID read from file - NOT local here, it's main script scope
        pid_from_file=$(cat "$PID_FILE" 2>/dev/null || echo "0") # Safe from set -e

        # Essential check: Is the PID we launched (_CURRENT_PYTHON_PID) still alive?
        # And does the PID file still contain our PID? (sanity check against external interference)
        # 'ps' in 'if' condition is safe with 'set -e'.
        if [[ "$_CURRENT_PYTHON_PID" -eq 0 ]] || ! ps -p "$_CURRENT_PYTHON_PID" > /dev/null 2>&1 || [[ "$pid_from_file" != "$_CURRENT_PYTHON_PID" ]]; then
            if [[ "$_CURRENT_PYTHON_PID" -ne 0 ]]; then # Only log if we thought it was running
                 log_wd "💀 Monitored script $TARGET_SCRIPT_NAME (expected PID $_CURRENT_PYTHON_PID) is no longer running or PID file tampered."
                 if [[ "$pid_from_file" != "$_CURRENT_PYTHON_PID" ]]; then
                     log_wd "   Mismatch: PID in file $PID_FILE is '$pid_from_file', expected $_CURRENT_PYTHON_PID."
                 elif ! ps -p "$_CURRENT_PYTHON_PID" > /dev/null 2>&1; then
                     log_wd "   Confirmed: Process $_CURRENT_PYTHON_PID does not exist."
                 fi
            else
                log_wd " DBUG: Monitored script $TARGET_SCRIPT_NAME was already considered stopped (_CURRENT_PYTHON_PID was 0)."
            fi
            _CURRENT_PYTHON_PID=0 # Clear our tracked PID
            rm -f "$PID_FILE" 2>/dev/null || true   # Clean up PID file
            break # Exit inner monitoring sub-loop, will trigger restart in outer loop
        fi

        # Perform health checks if script is confirmed running
        log_wd " DBUG: Checking status of $TARGET_SCRIPT_NAME (PID $_CURRENT_PYTHON_PID)..."
        # Each check function will call kill_python_script if conditions met, which returns 1.
        # 'if ! command; then break; fi' structure is safe with set -e.
        if ! check_memory_usage "$_CURRENT_PYTHON_PID"; then break; fi
        if ! check_max_runtime "$_CURRENT_PYTHON_PID" "$SCRIPT_ACTUAL_START_TIME"; then break; fi
        if ! check_no_output "$_CURRENT_PYTHON_PID" "$SCRIPT_ACTUAL_START_TIME"; then break; fi

        log_wd " DBUG: All checks passed for PID $_CURRENT_PYTHON_PID. Sleeping for $POLL_INTERVAL_SEC s."
        sleep "$POLL_INTERVAL_SEC"
    done # End of inner monitoring loop

    # If we reach here, the Python script has exited/crashed or was killed by one of the checks.
    log_wd "ℹ️ $TARGET_SCRIPT_NAME (expected PID $_CURRENT_PYTHON_PID before stop/crash) is no longer running."
    # _CURRENT_PYTHON_PID might have been cleared by kill_python_script or if script died and break was hit.
    # Ensure it's cleared if loop broken for other reasons.
    _CURRENT_PYTHON_PID=0
    rm -f "$PID_FILE" 2>/dev/null || true # Defensive removal if not already done

    log_wd "↪️ Restarting $TARGET_SCRIPT_NAME in $RESTART_DELAY_SEC seconds..."
    sleep "$RESTART_DELAY_SEC"
done # End of main execution loop (outer while true)

# This part should ideally not be reached due to the infinite loop and EXIT trap.
log_wd "☢️ UNEXPECTED: Watchdog script for $TARGET_SCRIPT_NAME has exited the main while true loop normally!"
exit 200 # Should be caught by EXIT trap, resulting in logged exit code
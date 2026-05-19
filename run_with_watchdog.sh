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
MIN_RESTART_INTERVAL=60  # 2026-04-28 user: bumped 3→60s. Workers OOM-killing every ~60s with 3s respawn = no recovery time for memory pages. 60s lets the OS reclaim before next allocation spike.
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

# ═══ TRADIER MARKET-HOURS GATE ═══
# tradier_* scripts are not allowed to run outside Mon-Fri 09:30-16:00 ET.
# Otherwise this wrapper will respawn them after we manually kill them on
# weekends, and they consume CPU + spam supervisor with stale-log alerts.
# Refuse to launch; exit cleanly so launchd/cron don't treat it as failure.
case "$SCRIPT" in
    tradier_manage.py|tradier_positions.py|tradier_prices.py|tradier_indicators.py|tradier_rankings.py|tradier_premarket_scanner.py|tradier_options_csp_monitor.py|tradier_options_analyzer.py|tradier_options_agent.py|tradier_hourly_reconfig.py|tradier_webhook_bridge.py)
        DOW=$(TZ="America/New_York" date +%u)
        ET_HOUR=$(TZ="America/New_York" date +%H)
        ET_MIN=$(TZ="America/New_York" date +%M)
        ET_MINS=$((10#$ET_HOUR * 60 + 10#$ET_MIN))
        if [ "$DOW" -gt 5 ] || [ "$ET_MINS" -lt 570 ] || [ "$ET_MINS" -gt 960 ]; then
            mkdir -p "$LOGDIR"
            echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [TRADIER_MARKET_GATE] refusing to launch $SCRIPT ${ARGS[*]} — outside Mon-Fri 09:30-16:00 ET (dow=$DOW et_mins=$ET_MINS)" >> "$LOGDIR/tradier_market_gate.log"
            exit 0
        fi
        ;;
esac

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

# Read macOS pages-free as KB. On non-Darwin returns a large value (always healthy).
# 2026-05-01: used by SIGKILL_BACKOFF to decide whether the host has actually recovered
# enough memory to respawn an ez_manage worker without triggering another jetsam kill.
mac_pages_free_kb() {
    if [[ "$(uname)" != "Darwin" ]]; then echo 999999; return; fi
    local pages
    pages=$(vm_stat 2>/dev/null | awk '/Pages free/ {gsub(/\./, "", $3); print $3; exit}')
    [[ -z "$pages" || ! "$pages" =~ ^[0-9]+$ ]] && { echo 999999; return; }
    echo $((pages * 16))
}

# Read macOS jetsam pressure level. Returns: 1=normal, 2=warning, 4=critical, 8=sustained-critical.
# 2026-05-01: pages_free is a misleading metric on Mac — it stays at 60-80MB
# constantly because macOS uses RAM as cache. The true "is jetsam about to fire"
# signal is kern.memorystatus_vm_pressure_level. Workers should respawn when this is
# ≤ 2 (normal or warning); avoid spawning when it is ≥ 4 (critical).
mac_pressure_level() {
    if [[ "$(uname)" != "Darwin" ]]; then echo 1; return; fi
    local lvl
    lvl=$(/usr/sbin/sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null)
    [[ -z "$lvl" || ! "$lvl" =~ ^[0-9]+$ ]] && { echo 1; return; }
    echo "$lvl"
}

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
    local rss_over_count=0

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
        # 2026-04-30: per-account ceiling — inf grew to 91 open positions; natural working set
        # ~1.75GB (200MB base + 91 × ~17MB klines+indicators+WT state per position). Was hitting
        # 1.5GB → SIGTERM→SIGKILL → exit 137 every 3 min (9× in 30 min). This is workload
        # scaling, NOT a leak: men=50pos@880MB, fin=24pos@685MB, ang=12pos@474MB all stable
        # under 1.5GB; only inf's 91-position load exceeds. 36GB RAM means even 5×2.2GB=11GB is
        # comfortable. Default unchanged at 1.5GB; inf gets 2.2GB.
        if [[ ( "$SCRIPT" == "ez_manage.py" || "$SCRIPT" == "ez_orderbook.py" ) && "$runtime" -gt 60 ]]; then
            local rss_kb
            if [[ "$(uname)" == "Darwin" ]]; then
                rss_kb=$(ps -p "$script_pid" -o rss= 2>/dev/null | tr -d ' ' || echo 0)
            else
                rss_kb=$(awk '/VmRSS/{print $2}' "/proc/$script_pid/status" 2>/dev/null || echo 0)
            fi
            local MAX_RSS_KB=1572864  # 1.5GB default (was 900MB)
            if [[ "$SCRIPT" == "ez_manage.py" ]]; then
                # 2026-05-19 OOM_CYCLE: men cycling 137 every 7-37min, ang/fin trending toward
                # ceiling. Observed RSS peaks at preemptive recycle: men 1.6-2.18GB, ang 1.8GB,
                # fin 1.7-1.77GB. The 2026-04-30 baseline (men=50pos@880MB) no longer holds —
                # working set has grown (new R1/R2/R3 state, per_sym overlay, HH/HL/LL framework,
                # STDEV_MACRO). flz with 1 open position remains stable at 469-643MB so this
                # is genuinely workload-scaled, not a leak: same evidence pattern that justified
                # inf's bump to 2.2GB. Apply same ceiling to men/ang/fin.
                case "${ARGS[*]}" in
                    *"--account inf"*) MAX_RSS_KB=2306867 ;;  # 2.2GB (2026-04-30 original)
                    *"--account men"*) MAX_RSS_KB=2306867 ;;  # 2.2GB (2026-05-19 OOM_CYCLE)
                    *"--account ang"*) MAX_RSS_KB=2306867 ;;  # 2.2GB (2026-05-19 OOM_CYCLE)
                    *"--account fin"*) MAX_RSS_KB=2306867 ;;  # 2.2GB (2026-05-19 OOM_CYCLE)
                esac
                # 2026-05-19: file-based override so future ceiling tweaks don't require
                # restarting the bash watchdog. Lets ops bump a single account without
                # rebooting all 5 ez_manage tty sessions. Empty/missing file = use case
                # default above. Path: data/.watchdog_max_rss_kb_<acct>
                local _override_acct=""
                for _arg in "${ARGS[@]}"; do
                    if [[ "$_arg" =~ ^(ang|inf|flz|men|fin)$ ]]; then
                        _override_acct="$_arg"
                        break
                    fi
                done
                if [[ -n "$_override_acct" ]]; then
                    local _override_file="$WORKDIR/data/.watchdog_max_rss_kb_${_override_acct}"
                    if [[ -f "$_override_file" ]]; then
                        local _override_val
                        _override_val=$(cat "$_override_file" 2>/dev/null | tr -d ' \n\r' || echo "")
                        if [[ "$_override_val" =~ ^[0-9]+$ ]] && [[ "$_override_val" -ge 524288 ]]; then
                            MAX_RSS_KB="$_override_val"
                        fi
                    fi
                fi
            fi
            if [[ -n "$rss_kb" && "$rss_kb" -gt "$MAX_RSS_KB" ]]; then
                rss_over_count=$((rss_over_count + 1))
                if [[ "$rss_over_count" -ge 4 ]]; then
                    log "🧹 RSS preemptive recycle: ${rss_kb}KB > ${MAX_RSS_KB}KB sustained for ${rss_over_count} samples (~$((rss_over_count * 5))s). Graceful restart before jetsam fires."
                    # 2026-05-14: SIGTERM grace bumped 5s→30s. Live evidence: every "exit 137"
                    # in the OOM_CYCLE detector was preceded by THIS log line ~5s earlier — i.e.
                    # the watchdog's own SIGKILL was firing because ez_manage's shutdown handler
                    # (signal_handler at ez_manage.py:46004 → shutdown_event.set() → background
                    # tasks unwind, position state flushes to JSON/Redis) takes longer than 5s.
                    # 30s lets the graceful path actually complete → exit 143 (clean) instead of
                    # 137 (kill). The ang log on 06:06:43 showed "SHUTDOWN & CLEANUP SEQUENCE
                    # INITIATED" with positions still being processed 3s later — SIGKILL came
                    # mid-flush. Workers fin (4h42m, 258MB) + flz (4h41m, 516MB) are long-term
                    # stable, so this isn't a runaway leak — it's workload-scaled RSS pressure
                    # on the larger accounts (inf 91pos, men 50pos, ang ~70pos) hitting the
                    # 1.5/2.2GB ceiling and the watchdog killing too aggressively.
                    kill -TERM "$script_pid" 2>/dev/null || true
                    sleep 30
                    kill -KILL "$script_pid" 2>/dev/null || true
                    break
                fi
            else
                rss_over_count=0
            fi
            # 2026-05-09: Pressure-aware evacuation. Per-process RSS ceilings only catch
            # leaks/scaling. Whole-system jetsam waves (e.g. 5 workers SIGKILLed simultaneously
            # 2026-05-09 06:07) hit workers BELOW their per-process ceiling because
            # jetsam picks the largest RSS regardless. Reading kern.memorystatus_vm_pressure_level
            # (≥4 = critical) and self-TERMing the largest workers BEFORE jetsam fires lets
            # them save state cleanly. Threshold 70% of MAX_RSS_KB so only well-loaded workers
            # self-evict — small workers stay alive, system pressure drops, jetsam stands down.
            local _pl=$(mac_pressure_level)
            if [[ -n "$rss_kb" && "$_pl" -ge 4 && "$rss_kb" -gt $((MAX_RSS_KB * 7 / 10)) ]]; then
                log "🚨 PRESSURE_EVAC: pressure_level=${_pl} (≥4 critical) rss=${rss_kb}KB > 70% of ${MAX_RSS_KB}KB. Graceful evac before jetsam SIGKILLs us."
                # 2026-05-14: 5s→30s grace for the same reason as preemptive recycle above.
                kill -TERM "$script_pid" 2>/dev/null || true
                sleep 30
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
    # 2026-05-01: SIGKILL-aware backoff. When the script exits with code 137 (SIGKILL,
    # typically Mac jetsam killing it under whole-system memory pressure), respawning
    # in 60s allocates the working set again and triggers another kill — a thrash loop
    # that just consumes CPU + adds memory pressure without recovering. Each consecutive
    # 137 doubles the wait (60→120→240→480→600 cap). Any clean / non-137 exit resets the
    # counter. Applied only to ez_manage workers (where this pattern was observed).
    # 2026-05-19 OOM_CYCLE: also reset when the last run lasted ≥300s. A 5-min stable run
    # before a single 137 = intermittent jetsam wave, NOT a thrash loop. Without this, men
    # got stuck at 600s backoff for hours because every restart eventually OOMed even after
    # 30+ min uptime, and the counter never reset. Distinguishes "ceiling exceeded after
    # legitimate work" from "thrash loop where every restart OOMs in <60s".
    local consecutive_sigkill=0
    local _run_started_at=0
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
        _run_started_at=$(date +%s)
        run_script
        local _last_exit=$?
        local _run_duration=$(( $(date +%s) - _run_started_at ))

        if [[ "$SCRIPT_BASE" == "ez_manage" && "$_last_exit" -eq 137 && "$_run_duration" -ge 300 ]]; then
            if [[ "$consecutive_sigkill" -gt 0 ]]; then
                log "[SIGKILL_BACKOFF] last run lasted ${_run_duration}s (≥300s stable) — resetting counter (was $consecutive_sigkill)"
            fi
            consecutive_sigkill=0
        fi

        if [[ "$SCRIPT_BASE" == "ez_manage" && "$_last_exit" -eq 137 ]]; then
            consecutive_sigkill=$((consecutive_sigkill + 1))
            local _backoff=$((60 * (1 << (consecutive_sigkill - 1))))
            if [[ "$_backoff" -gt 600 ]]; then _backoff=600; fi
            log "[SIGKILL_BACKOFF] consecutive 137 #$consecutive_sigkill — sleeping ${_backoff}s before next launch (system memory pressure)"
            sleep "$_backoff"
            # 2026-05-01: pressure-aware extension. Fixed sleep doesn't guarantee memory
            # is actually free. A fresh ez_manage spawn peaks ~600MB during state-load —
            # if the host is in jetsam-critical pressure, that allocation re-trips jetsam
            # and the kill loop continues.
            # Updated 2026-05-01 22:41: switched from pages_free (Mac always reports
            # 60-80MB free because of cache), to kern.memorystatus_vm_pressure_level
            # which is the actual jetsam signal. We launch when pressure ≤ 2 (warning),
            # block when pressure ≥ 4 (critical). pages_free retained as a fallback floor.
            local _wait_start=$(date +%s)
            local _patience=600
            while true; do
                local _level=$(mac_pressure_level)
                local _free_kb=$(mac_pages_free_kb)
                # Pressure level OK → launch immediately
                if [[ "$_level" -le 2 ]]; then
                    log "[SIGKILL_BACKOFF] pressure_level=${_level} (≤2 OK) free=${_free_kb}KB — launching"
                    break
                fi
                # Catastrophic pages_free floor: even with pressure ≥ 4, if the system
                # has clawed back ≥ 200MB free we're past the worst — launch.
                if [[ "$_free_kb" -ge 204800 ]]; then
                    log "[SIGKILL_BACKOFF] pages_free=${_free_kb}KB ≥ 200MB despite pressure_level=${_level} — launching"
                    break
                fi
                local _waited=$(( $(date +%s) - _wait_start ))
                if [[ "$_waited" -ge "$_patience" ]]; then
                    log "[SIGKILL_BACKOFF] pressure_level=${_level} free=${_free_kb}KB still tight, patience exhausted (${_patience}s) — launching anyway"
                    break
                fi
                log "[SIGKILL_BACKOFF] pressure_level=${_level} (≥4 critical) free=${_free_kb}KB — host still tight, waiting 60s more (${_waited}s/${_patience}s)"
                sleep 60
            done
        else
            if [[ "$consecutive_sigkill" -gt 0 ]]; then
                log "[SIGKILL_BACKOFF] resetting counter (last exit=$_last_exit, was $consecutive_sigkill)"
            fi
            consecutive_sigkill=0
            log "Script stopped. Restarting in 5 seconds..."
            sleep 5
        fi
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
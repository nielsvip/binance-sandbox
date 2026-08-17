#!/bin/bash
# start_everything_3.command - Tradier Trading System

if [[ "$TERM_PROGRAM" != "iTerm.app" ]] && [[ -z "$ITERM_RELAUNCHED" ]]; then
    osascript -e 'tell application "iTerm" to activate' -e 'tell application "iTerm" to tell current window to create tab with default profile' -e 'tell application "iTerm" to tell current window to tell current session to write text "ITERM_RELAUNCHED=1 bash /Users/niels/Documents/binance/start_everything_3.command"'
    exit 0
fi

# Configuration
WORKDIR="/Users/niels/Documents/binance"
PYTHON="/opt/anaconda3/envs/binance_env/bin/python"
WATCHDOG="$WORKDIR/run_with_watchdog.sh"
export TRADIER_LOCAL_ONLY=1
export START_EVERYTHING_3_LOCAL_ONLY=1

# Use local logs if /Users/niels/logs is not writable
LOGDIR="/Users/niels/logs"
if [ ! -w "$LOGDIR" ]; then
    LOGDIR="$WORKDIR/logs"
    mkdir -p "$LOGDIR"
fi
LAUNCHER_LOG="$LOGDIR/start_everything_3_command.log"
LOCK_FILE="$LOGDIR/start_everything_3.lock"

tradier_market_open_utc() {
    local dow hour minute mins
    dow=$(date -u +%u)
    hour=$(date -u +%H)
    minute=$(date -u +%M)
    mins=$((10#$hour * 60 + 10#$minute))
    [ "$dow" -le 5 ] && [ "$mins" -ge 805 ] && [ "$mins" -lt 1200 ]
}

# Prevent multiple instances
if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] Another instance is already running (PID $LOCK_PID). Exiting." | tee -a "$LAUNCHER_LOG"
        exit 0
    else
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"
trap "rm -f '$LOCK_FILE'" EXIT

# Stock paper-trading scripts: Python workers launched under run_with_watchdog.sh.
# Keep the three tradier_manage accounts separate so singleton locks and status
# checks are account-specific.
STOCK_SCRIPTS=(
    "tradier_prices|tradier_prices.py"
    "tradier_positions_all|tradier_positions.py --accounts tra trb trc"
    "tradier_indicators|tradier_indicators.py"
    "tradier_rankings|tradier_rankings.py"
    "tradier_manage_tra|tradier_manage.py --accounts tra"
    "tradier_manage_trb|tradier_manage.py --accounts trb"
    "tradier_manage_trc|tradier_manage.py --accounts trc"
    "ez_copilot|ez_copilot.py"
    "ez_news_scanner|ez_news_scanner.py"
)

# Options paper/shadow scripts.  These are intentionally not routed through
# run_with_watchdog.sh because that wrapper is Python-script oriented and has a
# Tradier market-hours gate.  The shell wrappers below own their own market-hour
# behavior and are paper/snapshot only:
#   - options_shadow_runner.sh: persistent paper/shadow options supervisor
#   - tradier_options_market_runner.sh: state + recommendations tick
OPTIONS_PAPER_SCRIPTS=(
    "options_shadow|bash options_shadow_runner.sh"
    "options_market_supervisor|while true; do bash tradier_options_market_runner.sh; sleep 60; done"
)

# Create log directory
mkdir -p "$LOGDIR"

# Change to working directory
cd "$WORKDIR" || exit 1

echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== TRADIER SYSTEM RESTART START =====" | tee -a "$LAUNCHER_LOG"

if ! tradier_market_open_utc; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Outside Tradier market window (Mon-Fri 13:25-20:00 UTC). Killing stale Tradier workers (paper shadow KeepAlive will relaunch) and exiting." | tee -a "$LAUNCHER_LOG"
    pkill -9 -f "python.*tradier_" 2>/dev/null || true
    pkill -9 -f "python.*ez_copilot" 2>/dev/null || true
    pkill -9 -f "bash.*run_with_watchdog.*tradier_" 2>/dev/null || true
    pkill -9 -f "bash.*run_with_watchdog.*ez_copilot" 2>/dev/null || true
    # Paper options supervisors are KeepAlive-managed (options-shadow, options-supervisor)
    # and own their own market-hours sleep — do not pkill them here; LaunchD will
    # keep them alive and they already sleep outside hours. Killing them here caused
    # the fragility where the inside-window relaunch only happened on manual runs.
    exit 0
fi

# SCORCHED EARTH: kill ALL tradier processes, watchdogs, and iTerm launchers
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Killing ALL tradier processes..." | tee -a "$LAUNCHER_LOG"
pkill -9 -f "python.*tradier_" 2>/dev/null || true
pkill -9 -f "python.*ez_copilot" 2>/dev/null || true
pkill -9 -f "bash.*run_with_watchdog.*tradier_" 2>/dev/null || true
pkill -9 -f "bash.*run_with_watchdog.*ez_copilot" 2>/dev/null || true
pkill -9 -f "bash.*iterm_launch_tradier" 2>/dev/null || true
pkill -9 -f "bash.*iterm_launch_ez_copilot" 2>/dev/null || true
pkill -9 -f "python.*tradier_options_shadow_runner.py" 2>/dev/null || true
pkill -9 -f "python.*tradier_options_state.py" 2>/dev/null || true
pkill -9 -f "python.*tradier_options_recommendations.py" 2>/dev/null || true
pkill -9 -f "bash.*options_shadow_runner.sh" 2>/dev/null || true
pkill -9 -f "bash.*tradier_options_market_runner.sh" 2>/dev/null || true
pkill -9 -f "bash.*iterm_launch_options_" 2>/dev/null || true
rm -f /Users/niels/logs/start_everything_3.lock 2>/dev/null
sleep 3

# Verify cleanup — force kill stragglers
REMAINING=$(pgrep -f "python.*tradier" 2>/dev/null | wc -l | tr -d ' ')
if [ "$REMAINING" -gt 0 ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $REMAINING Tradier processes survived, force killing..." | tee -a "$LAUNCHER_LOG"
    pkill -9 -f "python.*tradier" 2>/dev/null || true
    sleep 2
fi

echo "[$(date +'%Y-%m-%d %H:%M:%S')] Starting all Tradier scripts..." | tee -a "$LAUNCHER_LOG"

launch_tab() {
    local tab_name="$1"
    local command_text="$2"
    local fallback_log="$3"

    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Launching [$tab_name]: $command_text" | tee -a "$LAUNCHER_LOG"

    local tmpscript="/tmp/iterm_launch_${tab_name}.sh"
    cat > "$tmpscript" << LAUNCHER
#!/bin/bash
printf '\\e]1;${tab_name}\\a'
export TRADIER_LOCAL_ONLY=1
export START_EVERYTHING_3_LOCAL_ONLY=1
cd "$WORKDIR" && $command_text
LAUNCHER
    chmod +x "$tmpscript"
    if ! osascript -e "
tell application \"iTerm\"
    if (count of windows) = 0 then
        create window with default profile
    end if
    tell current window
        set newTab to (create tab with default profile)
        tell current session of newTab
            write text \"bash $tmpscript\"
        end tell
    end tell
end tell" > /dev/null 2>&1; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: osascript failed, launching $tab_name in background..." | tee -a "$LAUNCHER_LOG"
        nohup bash "$tmpscript" >> "$fallback_log" 2>&1 &
    fi

    # Small delay between launches
    sleep 1

    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Completed launch attempt for $tab_name" | tee -a "$LAUNCHER_LOG"
}

# Paper options must not depend on an interactive iTerm window.  start_everything_3
# is also invoked by launchd/watchdogs, where osascript may block or succeed without
# actually leaving a usable child process behind.  Keep these two supervisors as
# ordinary detached processes and give them durable logs.
launch_background() {
    local tab_name="$1"
    local command_text="$2"
    local output_log="$3"

    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Launching [$tab_name] in background: $command_text" | tee -a "$LAUNCHER_LOG"
    nohup bash -c "cd '$WORKDIR' && $command_text" >> "$output_log" 2>&1 < /dev/null &
    echo $! > "$LOGDIR/${tab_name}.pid"
    sleep 1
    if kill -0 "$(cat "$LOGDIR/${tab_name}.pid")" 2>/dev/null; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] Background launch confirmed for $tab_name (PID $(cat "$LOGDIR/${tab_name}.pid"))" | tee -a "$LAUNCHER_LOG"
    else
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: background launch exited immediately for $tab_name" | tee -a "$LAUNCHER_LOG"
    fi
}

# 2. LAUNCH LOOP
for entry in "${STOCK_SCRIPTS[@]}"; do
    tab_name="${entry%%|*}"
    full_cmd="${entry#*|}"
    launch_tab "$tab_name" "bash \"$WATCHDOG\" $full_cmd" "$LOGDIR/${tab_name}_startup.log"
done

for entry in "${OPTIONS_PAPER_SCRIPTS[@]}"; do
    tab_name="${entry%%|*}"
    full_cmd="${entry#*|}"
    launch_background "$tab_name" "$full_cmd" "$LOGDIR/${tab_name}_startup.log"
done

# Wait for scripts to initialize
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Waiting 10s for scripts to initialize..." | tee -a "$LAUNCHER_LOG"
sleep 10

# 3. FINAL STATUS CHECK
echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== FINAL STATUS =====" | tee -a "$LAUNCHER_LOG"
for entry in "${STOCK_SCRIPTS[@]}"; do
    tab_name="${entry%%|*}"
    full_cmd="${entry#*|}"
    script_name="${full_cmd%% *}"
    args="${full_cmd#"$script_name"}"
    # Wait a bit more for slow starts
    COUNT=$(pgrep -f "$PYTHON.*$script_name$args" 2>/dev/null | wc -l | tr -d ' ')
    
    if [ "$COUNT" -ge 1 ]; then
        echo "  ✅ $tab_name (Running)" | tee -a "$LAUNCHER_LOG"
    else
        echo "  ❌ $tab_name (FAILED)" | tee -a "$LAUNCHER_LOG"
        # Try to cat watchdog log for clues
        tail -n 5 "$LOGDIR/${tab_name}_watchdog.log" 2>/dev/null | sed 's/^/      [WD] /' | tee -a "$LAUNCHER_LOG"
    fi
done

SHADOW_COUNT=$(( \
    $(pgrep -f "python.*tradier_options_shadow_runner.py" 2>/dev/null | wc -l | tr -d ' ') + \
    $(pgrep -f "bash.*options_shadow_runner.sh" 2>/dev/null | wc -l | tr -d ' ') \
))
MARKET_SUP_COUNT=$(( \
    $(pgrep -f "bash.*tradier_options_market_runner.sh" 2>/dev/null | wc -l | tr -d ' ') + \
    $(pgrep -f "bash.*/tmp/iterm_launch_options_market_supervisor.sh" 2>/dev/null | wc -l | tr -d ' ') + \
    $(pgrep -f "while true; do bash tradier_options_market_runner.sh" 2>/dev/null | wc -l | tr -d ' ') \
))
if [ "$SHADOW_COUNT" -ge 1 ]; then
    echo "  ✅ options_shadow (Running)" | tee -a "$LAUNCHER_LOG"
else
    echo "  ❌ options_shadow (FAILED)" | tee -a "$LAUNCHER_LOG"
fi
if [ "$MARKET_SUP_COUNT" -ge 1 ]; then
    echo "  ✅ options_market_supervisor (Running)" | tee -a "$LAUNCHER_LOG"
else
    echo "  ❌ options_market_supervisor (FAILED)" | tee -a "$LAUNCHER_LOG"
fi

TRADIER_COUNT=$(pgrep -f "python.*tradier" 2>/dev/null | wc -l | tr -d ' ')
COPILOT_COUNT=$(pgrep -f "python.*ez_copilot" 2>/dev/null | wc -l | tr -d ' ')
TOTAL=$((TRADIER_COUNT + COPILOT_COUNT))
EXPECTED_TOTAL=$((${#STOCK_SCRIPTS[@]} + ${#OPTIONS_PAPER_SCRIPTS[@]}))
echo "[$(date +'%Y-%m-%d %H:%M:%S')] TOTAL PYTHON RUNNING: $TOTAL (tradier: $TRADIER_COUNT, copilot: $COPILOT_COUNT); expected launch targets: $EXPECTED_TOTAL" | tee -a "$LAUNCHER_LOG"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== DONE =====" | tee -a "$LAUNCHER_LOG"

# Keep window open only if run interactively (terminal attached)
if [ -t 1 ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Script completed. Press any key to close this window..." | tee -a "$LAUNCHER_LOG"
    read -n 1 -s
fi

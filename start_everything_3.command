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

# Use local logs if /Users/niels/logs is not writable
LOGDIR="/Users/niels/logs"
if [ ! -w "$LOGDIR" ]; then
    LOGDIR="$WORKDIR/logs"
    mkdir -p "$LOGDIR"
fi
LAUNCHER_LOG="$LOGDIR/start_everything_3_command.log"
LOCK_FILE="$LOGDIR/start_everything_3.lock"

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

# Scripts to launch (Filename + Arguments)
# Note: Positions runs for ALL, Manage runs for TRC only
SCRIPTS=(
    "tradier_prices.py"
    "tradier_positions.py --accounts tra trb trc"
    "tradier_indicators.py"
    "tradier_rankings.py"
    "tradier_manage.py --accounts tra"
    "tradier_manage.py --accounts trb"
    "tradier_manage.py --accounts trc"
    "ez_copilot.py"
)

# Create log directory
mkdir -p "$LOGDIR"

# Change to working directory
cd "$WORKDIR" || exit 1

echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== TRADIER SYSTEM RESTART START =====" | tee -a "$LAUNCHER_LOG"

# SCORCHED EARTH: kill ALL tradier processes, watchdogs, and iTerm launchers
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Killing ALL tradier processes..." | tee -a "$LAUNCHER_LOG"
pkill -9 -f "python.*tradier_" 2>/dev/null || true
pkill -9 -f "python.*ez_copilot" 2>/dev/null || true
pkill -9 -f "bash.*run_with_watchdog.*tradier_" 2>/dev/null || true
pkill -9 -f "bash.*run_with_watchdog.*ez_copilot" 2>/dev/null || true
pkill -9 -f "bash.*iterm_launch_tradier" 2>/dev/null || true
pkill -9 -f "bash.*iterm_launch_ez_copilot" 2>/dev/null || true
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

# 2. LAUNCH LOOP
for full_cmd in "${SCRIPTS[@]}"; do
    # Extract filename for pgrep check
    script_name="${full_cmd%% *}"
    
    # After scorched earth, nothing should be running — launch unconditionally
    
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Launching $full_cmd in Terminal..." | tee -a "$LAUNCHER_LOG"
    
    TAB_NAME="${script_name%.py}"
    TMPSCRIPT="/tmp/iterm_launch_${TAB_NAME}.sh"
    cat > "$TMPSCRIPT" << LAUNCHER
#!/bin/bash
printf '\\e]1;${TAB_NAME}\\a'
cd $WORKDIR && bash $WATCHDOG $full_cmd
LAUNCHER
    chmod +x "$TMPSCRIPT"
    if ! osascript -e "
tell application \"iTerm\"
    if (count of windows) = 0 then
        create window with default profile
    end if
    tell current window
        set newTab to (create tab with default profile)
        tell current session of newTab
            write text \"bash $TMPSCRIPT\"
        end tell
    end tell
end tell" > /dev/null 2>&1; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: osascript failed, launching $script_name in background..." | tee -a "$LAUNCHER_LOG"
        nohup bash "$WATCHDOG" $full_cmd >> "$LOGDIR/${script_name%.py}_startup.log" 2>&1 &
    fi
    
    # Small delay between launches
    sleep 1
    
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Completed launch attempt for $script_name" | tee -a "$LAUNCHER_LOG"
done

# Wait for scripts to initialize
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Waiting 10s for scripts to initialize..." | tee -a "$LAUNCHER_LOG"
sleep 10

# 3. FINAL STATUS CHECK
echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== FINAL STATUS =====" | tee -a "$LAUNCHER_LOG"
for full_cmd in "${SCRIPTS[@]}"; do
    script_name="${full_cmd%% *}"
    # Wait a bit more for slow starts
    COUNT=$(pgrep -f "$PYTHON.*$script_name" 2>/dev/null | wc -l | tr -d ' ')
    
    if [ "$COUNT" -ge 1 ]; then
        echo "  ✅ $script_name (Running)" | tee -a "$LAUNCHER_LOG"
    else
        echo "  ❌ $script_name (FAILED)" | tee -a "$LAUNCHER_LOG"
        # Try to cat watchdog log for clues
        tail -n 5 "$LOGDIR/${script_name%.py}_watchdog.log" 2>/dev/null | sed 's/^/      [WD] /' | tee -a "$LAUNCHER_LOG"
    fi
done

TRADIER_COUNT=$(pgrep -f "python.*tradier" 2>/dev/null | wc -l | tr -d ' ')
COPILOT_COUNT=$(pgrep -f "python.*ez_copilot" 2>/dev/null | wc -l | tr -d ' ')
TOTAL=$((TRADIER_COUNT + COPILOT_COUNT))
echo "[$(date +'%Y-%m-%d %H:%M:%S')] TOTAL RUNNING: $TOTAL/${#SCRIPTS[@]} (tradier: $TRADIER_COUNT, copilot: $COPILOT_COUNT)" | tee -a "$LAUNCHER_LOG"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== DONE =====" | tee -a "$LAUNCHER_LOG"

# Keep window open only if run interactively (terminal attached)
if [ -t 1 ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Script completed. Press any key to close this window..." | tee -a "$LAUNCHER_LOG"
    read -n 1 -s
fi
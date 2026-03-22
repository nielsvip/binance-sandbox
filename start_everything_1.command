#!/bin/bash
# start_everything_1.command - FIXED SIMPLE VERSION

if [[ "$TERM_PROGRAM" != "iTerm.app" ]] && [[ -z "$ITERM_RELAUNCHED" ]]; then
    osascript <<'EOF'
tell application "iTerm"
    activate
    delay 0.5
    if (count of windows) = 0 then
        create window with default profile
        delay 0.3
    end if
    tell current window
        create tab with default profile
        tell current session
            write text "ITERM_RELAUNCHED=1 bash /Users/niels/Documents/binance/start_everything_1.command"
        end tell
    end tell
end tell
EOF
    exit 0
fi

WORKDIR="/Users/niels/Documents/binance"
PYTHON="/opt/anaconda3/envs/binance_env/bin/python"
WATCHDOG="$WORKDIR/run_with_watchdog.sh"
LOGDIR="/Users/niels/logs"
LAUNCHER_LOG="$LOGDIR/start_everything_1_command.log"
LOCK_FILE="$LOGDIR/start_everything_1.lock"

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

# Scripts to launch in order
SCRIPTS=(
    "ez_prices_ws.py"
    "ez_klines.py"
    "ez_mark_prices.py"
    "ez_prices.py"
    "ez_share_ind.py"
    "ez_indicators.py"
    "ez_positions_watchdog.py"
    "ez_market_data.py"
    "ez_indicators_merger.py"
    "ez_crosses.py"
    "ez_rankings.py"
    "ez_news_scanner.py"
    #"ez_positions_quick.py"  # EMBEDDED in ez_manage.py since 2026-03-17 — no standalone process
    #"ez_gain_protector.py"
    "ez_loss_mitigator.py"
    "pa.py"
    "rsync_market_data_to_server_continuous.sh")

    # Create log directory
    mkdir -p "$LOGDIR"

    # Change to working directory
    cd "$WORKDIR" || exit 1

    # Function to check if a script is already running
    is_script_running() {
    local script_name="$1"
    if [ "$script_name" = "pa.py" ]; then
        pgrep -f "$PYTHON.*pa.py" >/dev/null 2>&1
    elif [ "$script_name" = "ez_indicators_merger.py" ]; then
        pgrep -f "$PYTHON.*ez_indicators_merger" >/dev/null 2>&1
    elif [ "$script_name" = "rsync_market_data_to_server_continuous.sh" ]; then
        pgrep -f "bash.*rsync_market_data_to_server_continuous" >/dev/null 2>&1
    else
        pgrep -f "$PYTHON.*$script_name" >/dev/null 2>&1
    fi
    }

echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== RESTART START =====" | tee -a "$LAUNCHER_LOG"

# Kill all existing processes using the central nuke script
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Running system nuke..." | tee -a "$LAUNCHER_LOG"
"$WORKDIR/nuke_everything.sh" >> "$LAUNCHER_LOG" 2>&1

sleep 2

# Verify cleanup
REMAINING=$(pgrep -f "python.*ez_" 2>/dev/null | wc -l | tr -d ' ')
if [ "$REMAINING" -gt 0 ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $REMAINING processes still running" | tee -a "$LAUNCHER_LOG"
fi

echo "[$(date +'%Y-%m-%d %H:%M:%S')] Starting all scripts..." | tee -a "$LAUNCHER_LOG"
# Launch each script with watchdog in Terminal windows
for script in "${SCRIPTS[@]}"; do
    # Check if script is already running
    if is_script_running "$script"; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] $script is already running. Skipping launch." | tee -a "$LAUNCHER_LOG"
        continue
    fi

    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Launching $script in Terminal window..." | tee -a "$LAUNCHER_LOG"

    if [ "$script" = "pa.py" ]; then
        CMD="cd $WORKDIR && $PYTHON -u pa.py --continuous --interval 300 --hours 1"
    elif [ "$script" = "ez_indicators_merger.py" ]; then
        CMD="cd $WORKDIR && $PYTHON -u ez_indicators_merger.py"
    elif [ "$script" = "rsync_market_data_to_server_continuous.sh" ]; then
        CMD="cd $WORKDIR && bash rsync_market_data_to_server_continuous.sh"
    else
        CMD="cd $WORKDIR && bash $WATCHDOG $script"
    fi
    TAB_NAME="${script%.py}"
    TAB_NAME="${TAB_NAME%.sh}"
    # Write a temp launcher script to avoid AppleScript quoting issues
    TMPSCRIPT="/tmp/iterm_launch_${TAB_NAME}.sh"
    cat > "$TMPSCRIPT" << LAUNCHER
#!/bin/bash
printf '\\e]1;${TAB_NAME}\\a'
$CMD
LAUNCHER
    chmod +x "$TMPSCRIPT"
    osascript -e "
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
end tell" >/dev/null 2>&1 &

    # Small delay between launches
    sleep 0.5

    # Verify we're still in the loop
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Completed launch attempt for $script" | tee -a "$LAUNCHER_LOG"
done

# Wait for all scripts to start
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Waiting 20s for scripts to initialize..." | tee -a "$LAUNCHER_LOG"
sleep 20

# Check final status
echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== FINAL STATUS =====" | tee -a "$LAUNCHER_LOG"
for script in "${SCRIPTS[@]}"; do
    COUNT=$(pgrep -f "python.*$script" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$COUNT" -eq 1 ]; then
        echo "  ✅ $script" | tee -a "$LAUNCHER_LOG"
    else
        echo "  ❌ $script (instances: $COUNT)" | tee -a "$LAUNCHER_LOG"
    fi
done

TOTAL=$(pgrep -f "python.*ez_" 2>/dev/null | wc -l | tr -d ' ')
PA_COUNT=$(pgrep -f "python.*pa.py" 2>/dev/null | wc -l | tr -d ' ')
echo "[$(date +'%Y-%m-%d %H:%M:%S')] TOTAL RUNNING: $TOTAL/${#SCRIPTS[@]} (ez scripts: $TOTAL, pa.py: $PA_COUNT)" | tee -a "$LAUNCHER_LOG"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== DONE =====" | tee -a "$LAUNCHER_LOG"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Launching start_everything_2.command..." | tee -a "$LAUNCHER_LOG"
START_EVERYTHING_2_CMD_PATH="/Users/niels/Documents/binance/start_everything_2.command"
nohup "$START_EVERYTHING_2_CMD_PATH" >> "$LAUNCHER_LOG" 2>&1 &
SE2_PID=$!
echo "[$(date +'%Y-%m-%d %H:%M:%S')] start_everything_2.command launched with PID $SE2_PID" | tee -a "$LAUNCHER_LOG"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Script completed." | tee -a "$LAUNCHER_LOG"

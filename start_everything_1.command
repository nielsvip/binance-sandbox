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
    #"ez_loss_mitigator.py"  # REMOVED 2026-03-26 — violates STRICT_NO_LOSS, closes positions at a loss
    "ez_copilot.py"
    "ez_backup.py"
    "pa.py"
    "rsync_market_data_to_server_continuous.sh"
    "rsync_from_gateway_continuous.sh"
    "trade_analytics.py"
    "sweep_cockpit.py"
    "sweep_monitor_agent.py")

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
    elif [ "$script_name" = "rsync_from_gateway_continuous.sh" ]; then
        pgrep -f "bash.*rsync_from_gateway_continuous" >/dev/null 2>&1
    else
        pgrep -f "$PYTHON.*$script_name" >/dev/null 2>&1
    fi
    }

echo "[$(date +'%Y-%m-%d %H:%M:%S')] ===== RESTART START =====" | tee -a "$LAUNCHER_LOG"

# DATA-SIDE: keep running across restarts — these are stable pollers that don't carry new
# trading logic. Keeping them up avoids the simultaneous Binance API burst that causes IP bans.
# TRADING-SIDE (ez_manage, ez_copilot, ez_reentry_daemon): always killed so new code takes effect.
# SE2 handles ez_manage + ez_reentry_daemon restart.
DATA_SIDE_SCRIPTS=(
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
    "ez_backup.py"
    "pa.py"
    "rsync_market_data_to_server_continuous.sh"
    "rsync_from_gateway_continuous.sh"
    "trade_analytics.py"
    "sweep_cockpit.py"
    "sweep_monitor_agent.py"
)

is_data_side() {
    local s="$1"
    for d in "${DATA_SIDE_SCRIPTS[@]}"; do [ "$d" = "$s" ] && return 0; done
    return 1
}

# Kill trading-side only. ez_manage and ez_reentry_daemon handled by SE2.
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Killing trading-side processes (ez_manage, ez_copilot, ez_reentry_daemon)..." | tee -a "$LAUNCHER_LOG"
pkill -9 -f "python.*ez_manage.py" 2>/dev/null || true
pkill -9 -f "bash.*run_with_watchdog.*ez_manage.py" 2>/dev/null || true
pkill -9 -f "python.*ez_reentry_daemon.py" 2>/dev/null || true
pkill -9 -f "bash.*run_with_watchdog.*ez_reentry_daemon" 2>/dev/null || true
pkill -9 -f "python.*ez_copilot.py" 2>/dev/null || true
pkill -9 -f "bash.*run_with_watchdog.*ez_copilot" 2>/dev/null || true
sleep 2
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Trading-side killed. Data fetchers kept running." | tee -a "$LAUNCHER_LOG"

# Use --force to also restart all data-side scripts (full scorched earth)
if [ "${1:-}" = "--force" ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] --force: killing all remaining ez_ processes..." | tee -a "$LAUNCHER_LOG"
    pkill -9 -f "python.*ez_" 2>/dev/null || true
    pkill -9 -f "bash.*run_with_watchdog.*ez_" 2>/dev/null || true
    pkill -9 -f "bash.*rsync_market_data" 2>/dev/null || true
    pkill -9 -f "bash.*rsync_from_gateway" 2>/dev/null || true
    pkill -9 -f "ez_indicators_merger" 2>/dev/null || true
    pkill -9 -f "python.*trade_analytics" 2>/dev/null || true
    sleep 3
fi

echo "[$(date +'%Y-%m-%d %H:%M:%S')] Starting all scripts..." | tee -a "$LAUNCHER_LOG"
# Launch each script with watchdog in Terminal windows
for script in "${SCRIPTS[@]}"; do
    # Keep data-side scripts running if they're already healthy
    if is_data_side "$script" && is_script_running "$script"; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] Keeping $script (already running)" | tee -a "$LAUNCHER_LOG"
        continue
    fi

    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Launching $script in Terminal window..." | tee -a "$LAUNCHER_LOG"

    if [ "$script" = "pa.py" ]; then
        CMD="cd $WORKDIR && $PYTHON -u pa.py --continuous --interval 300 --hours 1"
    elif [ "$script" = "ez_indicators_merger.py" ]; then
        CMD="cd $WORKDIR && $PYTHON -u ez_indicators_merger.py"
    elif [ "$script" = "rsync_market_data_to_server_continuous.sh" ]; then
        CMD="cd $WORKDIR && bash rsync_market_data_to_server_continuous.sh"
    elif [ "$script" = "rsync_from_gateway_continuous.sh" ]; then
        CMD="cd $WORKDIR && bash rsync_from_gateway_continuous.sh"
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

    # Staggered delay — prevents simultaneous Binance API burst → IP ban
    sleep 2

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

# Wait for SE2 to finish launching trading accounts
sleep 30

# Open iTerm log-tail tabs for EVERY running service so errors are visible
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Opening log-tail tabs in iTerm..." | tee -a "$LAUNCHER_LOG"
LOG_TABS=(
    "ez_manage_ang"
    "ez_manage_inf"
    "ez_manage_flz"
    "ez_manage_men"
    "ez_manage_fin"
    "ez_prices_ws"
    "ez_prices"
    "ez_klines"
    "ez_mark_prices"
    "ez_share_ind"
    "ez_indicators"
    "ez_indicators_merger"
    "ez_market_data"
    "ez_positions_watchdog"
    "ez_crosses"
    "ez_rankings"
    "ez_news_scanner"
    "ez_copilot"
    "ez_backup"
    "trade_analytics"
    "sweep_cockpit"
    "sweep_monitor_agent"
    "pa"
)
for tab in "${LOG_TABS[@]}"; do
    LOG_PATH="/Users/niels/logs/${tab}.log"
    if [ ! -f "$LOG_PATH" ]; then continue; fi
    osascript -e "
tell application \"iTerm\"
    tell current window
        set newTab to (create tab with default profile)
        tell current session of newTab
            set name to \"LOG-${tab}\"
            write text \"printf '\\\\e]0;LOG-${tab}\\\\a'; tail -F /Users/niels/logs/${tab}.log\"
        end tell
    end tell
end tell" >/dev/null 2>&1
    sleep 1.5
done
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Log tabs opened." | tee -a "$LAUNCHER_LOG"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Script completed." | tee -a "$LAUNCHER_LOG"

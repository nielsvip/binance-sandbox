#!/bin/bash
# start_everything.command - Main Mac Orchestrator (runs periodically)

# If launched from Terminal.app (double-click), relaunch in iTerm and exit
if [[ "$TERM_PROGRAM" != "iTerm.app" ]] && [[ -z "$ITERM_RELAUNCHED" ]]; then
    osascript <<'EOF'
tell application "iTerm"
    activate
    if (count of windows) = 0 then
        create window with default profile
    end if
    tell current window
        create tab with default profile
        tell current session
            write text "ITERM_RELAUNCHED=1 bash /Users/niels/Documents/binance/start_everything.command"
        end tell
    end tell
end tell
EOF
    exit 0
fi

LAUNCHER_LOG="/Users/niels/logs/start_everything_main_orchestrator.log"
mkdir -p "$(dirname "$LAUNCHER_LOG")"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Script run started." | tee -a "$LAUNCHER_LOG"

set -uo pipefail

# --- Configuration ---
MAC_PYTHON_EXEC_FOR_PGREP="/opt/anaconda3/envs/binance_env/bin/python"
WATCHDOG_SCRIPT_FILENAME="run_with_watchdog.sh" 

START_EVERYTHING_1_CMD_PATH="/Users/niels/Documents/binance/start_everything_1.command"
START_EVERYTHING_2_CMD_PATH="/Users/niels/Documents/binance/start_everything_2.command"
REDIS_PORT="6379"

# Check if Redis is available locally
if ! nc -z localhost "$REDIS_PORT"; then
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Redis not available on localhost:$REDIS_PORT" | tee -a "$LAUNCHER_LOG"
else
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Redis connection to localhost port $REDIS_PORT [tcp/*] succeeded!" | tee -a "$LAUNCHER_LOG"
fi

DATA_SCRIPTS_TO_CHECK=(
  "ez_backup.py"
  "ez_positions_watchdog.py" 
  "ez_prices_ws.py"
  "ez_klines.py"
  "ez_mark_prices.py"
  "ez_prices.py"
  "ez_indicators.py"
  "ez_indicators_merger.py"
  "ez_crosses.py"
  "ez_rankings.py"
)
DATA_SCRIPT_LEAVE_ALONE_IF_UPTIME_LESS_THAN_MINUTES=180 
DATA_SCRIPT_RELAUNCH_IF_UPTIME_MORE_THAN_MINUTES=300 

# Removed all remote/network configuration - script is purely local

# --- Helper Functions ---
get_script_pid() {
    local script_name_pattern="$1"
    # pgrep -f: match full command line
    # Match python interpreter, then any chars, then the script name pattern
    pgrep -f "$MAC_PYTHON_EXEC_FOR_PGREP.*$script_name_pattern" | head -n 1
}

get_script_uptime_seconds() {
    local pid="$1"
    if [ -z "$pid" ]; then echo 0; return; fi
    local etimes_output
    etimes_output=$(ps -p "$pid" -o etimes= 2>/dev/null | tr -d '[:space:]')
    if [[ "$etimes_output" =~ ^[0-9]+$ ]]; then echo "$etimes_output"; else echo 0; fi
}

kill_all_ez_scripts() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Killing ALL running ez_ scripts..." | tee -a "$LAUNCHER_LOG"
    for pid in $(pgrep -f "${MAC_PYTHON_EXEC_FOR_PGREP}.*ez_.*\.py" 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
    for pid in $(pgrep -f "bash.*run_with_watchdog.*ez_" 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
    for pid in $(pgrep -f "tee -a.*ez_.*\.log" 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
    pkill -9 -f "${MAC_PYTHON_EXEC_FOR_PGREP}.*ez_.*\.py" 2>/dev/null || true
    pkill -9 -f "bash.*run_with_watchdog.*ez_" 2>/dev/null || true
    pkill -9 -f "python.*ez_" 2>/dev/null || true
    sleep 1
    REMAINING=$(pgrep -f "python.*ez_" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$REMAINING" -gt 0 ]; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] WARNING: $REMAINING ez_ processes still running, force killing..." | tee -a "$LAUNCHER_LOG"
        for pid in $(pgrep -f "python.*ez_" 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
    else
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] All ez_ scripts killed successfully." | tee -a "$LAUNCHER_LOG"
    fi
}
stop_mac_ez_manage_and_remove_heartbeat() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Stopping Mac ez_manage, watchdogs, and tees." | tee -a "$LAUNCHER_LOG"
    PY_EZ_PATTERN_TO_KILL="${MAC_PYTHON_EXEC_FOR_PGREP}.*/Users/niels/Documents/binance/ez_manage.py --account"
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Killing Python ez_manage with pattern: $PY_EZ_PATTERN_TO_KILL" | tee -a "$LAUNCHER_LOG"
    pkill -9 -f "$PY_EZ_PATTERN_TO_KILL"
    WD_EZ_PATTERN_TO_KILL="${WATCHDOG_SCRIPT_FILENAME} ez_manage.py --account"
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Killing Watchdog ez_manage with pattern: $WD_EZ_PATTERN_TO_KILL" | tee -a "$LAUNCHER_LOG"
    pkill -9 -f "$WD_EZ_PATTERN_TO_KILL"
    TEE_EZ_PATTERN_TO_KILL="tee -a /Users/niels/logs/ez_manage_py___account_.*_app.log"
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Killing Tee ez_manage with pattern: $TEE_EZ_PATTERN_TO_KILL" | tee -a "$LAUNCHER_LOG"
    pkill -9 -f "$TEE_EZ_PATTERN_TO_KILL"
    sleep 1
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Mac ez_manage processes stopped (no network operations)." | tee -a "$LAUNCHER_LOG"
}
# --- State for this run ---
data_scripts_require_se1_relaunch=false

# --- 0. Kill ALL existing processes first ---
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Running definitive system nuke..." | tee -a "$LAUNCHER_LOG"
/Users/niels/Documents/binance/nuke_everything.sh >> "$LAUNCHER_LOG" 2>&1

# --- Always restart all scripts after killing ---
data_scripts_require_se1_relaunch=true
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] All ez_ scripts killed. Triggering SE1 to restart them." | tee -a "$LAUNCHER_LOG"

# --- Decide Action Based on DATA Script Health ---
if $data_scripts_require_se1_relaunch; then
    # Always kill SE1 if running to ensure fresh start
    for se1_pid in $(pgrep -f "start_everything_1.command" 2>/dev/null); do
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Killing existing SE1 (PID $se1_pid)..." | tee -a "$LAUNCHER_LOG"
        kill -9 "$se1_pid" 2>/dev/null || true
    done
    sleep 1
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Launching SE1: $START_EVERYTHING_1_CMD_PATH" | tee -a "$LAUNCHER_LOG"
    nohup "$START_EVERYTHING_1_CMD_PATH" >> "$LAUNCHER_LOG" 2>&1 &
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] SE1 launched in background. SE1 will launch SE2 automatically when finished." | tee -a "$LAUNCHER_LOG"
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] DATA scripts are being relaunched. Mac will NOT run ez_manage in this cycle." | tee -a "$LAUNCHER_LOG"
    stop_mac_ez_manage_and_remove_heartbeat
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Script run finished (SE1 triggered, all scripts starting)." | tee -a "$LAUNCHER_LOG"
    if [ -t 1 ]; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Press any key to close..." | tee -a "$LAUNCHER_LOG"
        read -n 1 -s
    fi
    exit 0
fi

# If we reach here, ALL DATA scripts were running AND their uptime was OK.
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] All DATA scripts appear healthy. Proceeding with ez_manage logic." | tee -a "$LAUNCHER_LOG"

# --- 2. Check and Manage ez_manage (Mac only) ---
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Checking Mac ez_manage status..." | tee -a "$LAUNCHER_LOG"

ez_manage_inf_pid=$(get_script_pid "ez_manage.py --account inf") # Check if one instance is already up
if [ -z "$ez_manage_inf_pid" ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Mac ez_manage (inf) not found. Launching all via $START_EVERYTHING_2_CMD_PATH." | tee -a "$LAUNCHER_LOG"
    # Launch SE2 directly in background instead of using Terminal.app
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Launching SE2 directly: $START_EVERYTHING_2_CMD_PATH" | tee -a "$LAUNCHER_LOG"
    # Use nohup to completely detach from Terminal
    nohup "$START_EVERYTHING_2_CMD_PATH" >> "$LAUNCHER_LOG" 2>&1 &
    SE2_PID=$!
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] SE2 launched in background with PID $SE2_PID" | tee -a "$LAUNCHER_LOG"
else
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Mac ez_manage (inf PID $ez_manage_inf_pid) already running. No launch action by SE2." | tee -a "$LAUNCHER_LOG"
fi

# --- 3. Final Status Check ---
mac_ez_manage_inf_pid_final_check=$(get_script_pid "ez_manage.py --account inf")

if [ -n "$mac_ez_manage_inf_pid_final_check" ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Mac ez_manage is confirmed running (PID $mac_ez_manage_inf_pid_final_check)." | tee -a "$LAUNCHER_LOG"
else
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Mac ez_manage is confirmed NOT running." | tee -a "$LAUNCHER_LOG"
fi

echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] Script run finished successfully." | tee -a "$LAUNCHER_LOG"

# Keep window open if running interactively
if [ -t 1 ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [MainOrchestrator] All operations completed. Press any key to close..." | tee -a "$LAUNCHER_LOG"
    read -n 1 -s
fi

exit 0
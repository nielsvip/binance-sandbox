#!/bin/bash
# start_everything_2.command
# Kills and launches local ez_manage.py scripts. Targets Python, Watchdog, and Tee.

LAUNCHER_LOG="/Users/niels/logs/start_everything_2_command.log"
mkdir -p "$(dirname "$LAUNCHER_LOG")"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Script started." | tee -a "$LAUNCHER_LOG"

set -e  # Exit on error, but allow unset variables for flexibility

MAC_PYTHON_EXEC_PATH_PREFIX="/opt/anaconda3/envs/binance_env/bin/python"
WORKDIR="/Users/niels/Documents/binance"
WATCHDOG_SCRIPT_FILENAME="run_with_watchdog.sh" 
WRAPPER_FULL_PATH="$WORKDIR/$WATCHDOG_SCRIPT_FILENAME"

SCRIPT_BASENAME_FOR_EZMANAGE="ez_manage.py"

echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Clearing ez_manage PID files..." | tee -a "$LAUNCHER_LOG"
for pid_file in "$WORKDIR"/pids/ez_manage_*.pid; do
  [ -e "$pid_file" ] || continue
  pid=$(tr -dc '0-9' < "$pid_file" 2>/dev/null)
  pid_name=$(basename "$pid_file")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Killing $pid_name (PID $pid)" | tee -a "$LAUNCHER_LOG"
    kill -9 "$pid" 2>/dev/null || true
  else
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Removing stale $pid_name" | tee -a "$LAUNCHER_LOG"
  fi
  rm -f "$pid_file" 2>/dev/null || true
done

echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Force killing existing ez_manage scripts for fresh restart..." | tee -a "$LAUNCHER_LOG"
pkill -9 -f "python.*ez_manage.py" 2>/dev/null || true
pkill -9 -f "bash.*run_with_watchdog.*ez_manage.py" 2>/dev/null || true

# 4. Clean up any remaining PID files
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Cleaning up PID files..." | tee -a "$LAUNCHER_LOG"
rm -f "$WORKDIR"/pids/ez_manage_*.pid 2>/dev/null || true

echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Finished nuke. Pausing for 3 seconds..." | tee -a "$LAUNCHER_LOG"
sleep 3 

echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Launching ez_manage.py scripts in Terminal windows..." | tee -a "$LAUNCHER_LOG"

EZ_MANAGE_ACCOUNTS=("ang" "inf" "flz" "men"  "fin")
for account in "${EZ_MANAGE_ACCOUNTS[@]}"; do
  script_arg1="--account"
  script_arg2="$account"

  echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Launching: $SCRIPT_BASENAME_FOR_EZMANAGE $script_arg1 $script_arg2" | tee -a "$LAUNCHER_LOG"

  # Launch each script in its own Terminal window
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Opening Terminal window for $SCRIPT_BASENAME_FOR_EZMANAGE $script_arg1 $script_arg2..." | tee -a "$LAUNCHER_LOG"
  
  TAB_NAME="manage_${account}"
  TMPSCRIPT="/tmp/iterm_launch_${TAB_NAME}.sh"
  cat > "$TMPSCRIPT" << LAUNCHER
#!/bin/bash
printf '\\e]1;${TAB_NAME}\\a'
cd $WORKDIR && bash $WRAPPER_FULL_PATH $SCRIPT_BASENAME_FOR_EZMANAGE $script_arg1 $script_arg2
LAUNCHER
  chmod +x "$TMPSCRIPT"
  if osascript -e "
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
end tell" 2>&1 | tee -a "$LAUNCHER_LOG"; then
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] iTerm tab opened for $SCRIPT_BASENAME_FOR_EZMANAGE $script_arg1 $script_arg2" | tee -a "$LAUNCHER_LOG"
  else
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] WARNING: Failed to open iTerm for $script_arg2" | tee -a "$LAUNCHER_LOG"
  fi

  # Small delay between script launches
  sleep 2
done

echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] All local ez_manage.py scripts launched." | tee -a "$LAUNCHER_LOG"

# 2026-04-28 — Launch ez_reentry_daemon.py (24/7 standalone reentry shadow + heartbeat watchdog).
# Independent of per-account ez_manage workers so a worker crash does not lose reentry observability.
# See ez_reentry.py / ez_reentry_daemon.py headers for architecture.
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Killing existing ez_reentry_daemon..." | tee -a "$LAUNCHER_LOG"
pkill -9 -f "python.*ez_reentry_daemon.py" 2>/dev/null || true
pkill -9 -f "bash.*run_with_watchdog.*ez_reentry_daemon.py" 2>/dev/null || true
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Launching ez_reentry_daemon.py under run_with_watchdog..." | tee -a "$LAUNCHER_LOG"
DAEMON_TAB_NAME="reentry_daemon"
DAEMON_TMPSCRIPT="/tmp/iterm_launch_${DAEMON_TAB_NAME}.sh"
cat > "$DAEMON_TMPSCRIPT" << LAUNCHER
#!/bin/bash
printf '\\e]1;${DAEMON_TAB_NAME}\\a'
cd $WORKDIR && bash $WRAPPER_FULL_PATH ez_reentry_daemon.py
LAUNCHER
chmod +x "$DAEMON_TMPSCRIPT"
osascript -e "
tell application \"iTerm\"
    if (count of windows) = 0 then
        create window with default profile
    end if
    tell current window
        set newTab to (create tab with default profile)
        tell current session of newTab
            write text \"bash $DAEMON_TMPSCRIPT\"
        end tell
    end tell
end tell" >/dev/null 2>&1 || echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] WARNING: Failed to open iTerm tab for ez_reentry_daemon" | tee -a "$LAUNCHER_LOG"
sleep 1

# Wait for quick loggers to initialize before tailing
sleep 5

# Open log tail tabs for ez_positions_quick per-account logs (weather forecasts + decisions)
LOGDIR="/Users/niels/logs"
echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Opening log tail tabs..." | tee -a "$LAUNCHER_LOG"
for account in "${EZ_MANAGE_ACCOUNTS[@]}"; do
  QUICK_LOG="$LOGDIR/ez_positions_quick_${account}.log"
  touch "$QUICK_LOG"
  TAB_NAME="quick_${account}"
  TMPSCRIPT="/tmp/iterm_launch_${TAB_NAME}.sh"
  cat > "$TMPSCRIPT" << LAUNCHER
#!/bin/bash
printf '\\e]1;${TAB_NAME}\\a'
tail -f "$QUICK_LOG"
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
  sleep 0.3
done

# Open a general error log tab (ez_manage errors across all accounts)
GENERAL_TAB_NAME="manage_errors"
GENERAL_TMPSCRIPT="/tmp/iterm_launch_${GENERAL_TAB_NAME}.sh"
cat > "$GENERAL_TMPSCRIPT" << LAUNCHER
#!/bin/bash
printf '\\e]1;${GENERAL_TAB_NAME}\\a'
# Follow rotations and include the stderr files where Python tracebacks and
# task warnings are written.  Keep the tab useful for both hard failures and
# stuck/rejected order paths.
tail -F "$LOGDIR"/ez_manage_*.log 2>/dev/null | grep --line-buffered -iE "ERROR|CRITICAL|EXCEPTION|TRACEBACK|RUNTIMEWARNING|TASK EXCEPTION|LOOP ERROR|TIMEOUT|EXECUTE_NOW|REDUCE|HEDGE_KILL|WEBHOOK_FAIL"
LAUNCHER
chmod +x "$GENERAL_TMPSCRIPT"
osascript -e "
tell application \"iTerm\"
    if (count of windows) = 0 then
        create window with default profile
    end if
    tell current window
        set newTab to (create tab with default profile)
        tell current session of newTab
            write text \"bash $GENERAL_TMPSCRIPT\"
        end tell
    end tell
end tell" >/dev/null 2>&1 &

echo "[$(date +'%Y-%m-%d %H:%M:%S')] [Launcher_SE2] Log tail tabs opened." | tee -a "$LAUNCHER_LOG"

# Stop server ez_manage services to prevent interference (Mac takeover)
SERVER_HOST="s1-int"
echo "[$(date +"%Y-%m-%d %H:%M:%S")] [Launcher_SE2] Requesting server ez_manage stop on ${SERVER_HOST}..." | tee -a "$LAUNCHER_LOG"
timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=no "$SERVER_HOST" \
  "/home/niels/binance/start_everything_2_LINUX.sh stop 'MAC_SE2' 'Mac takeover via start_everything_2.command'" \
  >> "$LAUNCHER_LOG" 2>&1 || echo "[$(date +"%Y-%m-%d %H:%M:%S")] [Launcher_SE2] WARNING: Could not contact server to stop ez_manage." | tee -a "$LAUNCHER_LOG"

# Create/update server leadership flag so server knows Mac is leading
SERVER_FLAG="/home/niels/binance/mac_leads_manage.flag"
echo "[$(date +"%Y-%m-%d %H:%M:%S")] [Launcher_SE2] Setting server leadership flag $SERVER_FLAG" | tee -a "$LAUNCHER_LOG"
timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 -o StrictHostKeyChecking=no "$SERVER_HOST" \
  "bash -lc 'mkdir -p /home/niels/binance && : > \"$SERVER_FLAG\"'" >> "$LAUNCHER_LOG" 2>&1 || echo "[$(date +"%Y-%m-%d %H:%M:%S")] [Launcher_SE2] WARNING: Could not set server leadership flag." | tee -a "$LAUNCHER_LOG"

# Background monitor: remove server flag when local ez_manage stops
nohup bash -c '
  while true; do
    if ! pgrep -f "/Users/niels/Documents/binance/ez_manage.py --account" >/dev/null 2>&1; then
      timeout 10 ssh -o BatchMode=yes -o ConnectTimeout=3 -o ConnectionAttempts=1 -o StrictHostKeyChecking=no "'$SERVER_HOST'" "rm -f \"$SERVER_FLAG\"" 2>/dev/null || true
      exit 0
    fi
    sleep 15
  done
' >/dev/null 2>&1 &

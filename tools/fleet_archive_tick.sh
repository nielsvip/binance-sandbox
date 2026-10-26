#!/bin/bash
set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
REPO=/Users/niels/Documents/binance
PY=/opt/anaconda3/envs/binance_env/bin/python
LOG="$HOME/Library/Logs/fleet_archive_manager_launchd.log"
LOCK="$HOME/Library/Caches/com.binance.fleetarchive/manager.lock"
mkdir -p "$(dirname "$LOG")" "$(dirname "$LOCK")"
echo "[$(/bin/date -u +%FT%TZ)] archive tick start" >> "$LOG"
/usr/bin/lockf -t 0 "$LOCK" /bin/bash -c 'cd "$1" && exec "$2" "$1/tools/fleet_archive_manager.py" --once --apply' _ "$REPO" "$PY" >> "$LOG" 2>&1
rc=$?
echo "[$(/bin/date -u +%FT%TZ)] archive tick end rc=$rc" >> "$LOG"
exit "$rc"

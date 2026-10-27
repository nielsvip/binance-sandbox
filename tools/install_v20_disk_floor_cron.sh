#!/usr/bin/env bash
# Install the continuous free-space floor on this host's crontab (run it ON the orchestrator, S2).
#
# The floor is the structural change USER asked for on 2026-10-27: ">40gb always on each server". A nightly
# compress frees whatever happens to be eligible; this checks the requirement every 30 minutes and only acts when a
# host is under it, so disk pressure can never build up unattended between nightly runs.
#
# File-based crontab write only, and it refuses an empty backup -- nested ssh quoting wiped S2's crontab 2026-10-13.
set -euo pipefail

STAMP=$(date -u +%Y%m%d%H%M)
BACKUP="$HOME/backups/crontab_before_v20_disk_floor_${STAMP}.txt"
STAGE=$(mktemp)
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
SANDBOX="$HOME/binance-sandbox"
TOOL="tools/v20_disk_floor.py"
HOSTS="s1,s2,s5,s6"
FLOOR=${V20_FLOOR_GB:-40}
LOG="$HOME/logs/v20_disk_floor.log"
trap 'rm -f "$STAGE"' EXIT

mkdir -p "$HOME/backups" "$HOME/logs"
crontab -l > "$BACKUP" 2>/dev/null || true
[ -s "$BACKUP" ] || { echo "REFUSING: empty crontab backup at $BACKUP"; exit 1; }
BEFORE=$(wc -l < "$BACKUP" | tr -d ' ')
echo "backed up $BEFORE lines to $BACKUP"

[ -f "$SANDBOX/$TOOL" ] || { echo "REFUSING: $SANDBOX/$TOOL is not installed"; exit 1; }
[ -x "$PY" ] || { echo "REFUSING: $PY is not executable"; exit 1; }

grep -v "V20_DISK_FLOOR" "$BACKUP" > "$STAGE"
cat >> "$STAGE" <<EOF
*/30 * * * * /usr/bin/flock -n /tmp/v20_disk_floor.lock /bin/bash -lc 'cd $SANDBOX && nice -n 19 ionice -c3 $PY $TOOL --hosts $HOSTS --floor-gb $FLOOR --stale-days 1 --rungs corpus,shelve --apply --detach >> $LOG 2>&1' # V20_DISK_FLOOR hold ${FLOOR}GB free on every host (acts only when a host is under the floor; shelve detaches, corpus delegates to the gated absorb cycle)
EOF

crontab "$STAGE"
AFTER=$(crontab -l | wc -l | tr -d ' ')
LINES=$(crontab -l | grep -c "V20_DISK_FLOOR" || true)
echo "crontab now $AFTER lines; V20_DISK_FLOOR=$LINES"
if [ "$LINES" -ne 1 ] || [ "$AFTER" -lt "$BEFORE" ]; then
  echo "VERIFY FAILED -- restoring $BACKUP"
  crontab "$BACKUP"
  exit 1
fi
echo "OK"

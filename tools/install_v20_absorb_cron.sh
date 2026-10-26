#!/usr/bin/env bash
# Install the scheduled V20 absorption cycle on this host's crontab. Idempotent.
#
# Run it ON the orchestrator: scp this file over and `bash install_v20_absorb_cron.sh`.
# NEVER drive a remote crontab through nested ssh quoting: on 2026-10-13 that re-split the command remotely so
# `bash -c crontab` ran bare `crontab`, which installed an EMPTY crontab from ssh's stdin and wiped every job on S2.
# This script therefore only ever writes a crontab from a FILE, and refuses to proceed on an empty backup.
set -euo pipefail

STAMP=$(date -u +%Y%m%d%H%M)
BACKUP_DIR="$HOME/backups"
BACKUP="$BACKUP_DIR/crontab_before_v20_absorb_${STAMP}.txt"
STAGE=$(mktemp)
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
SANDBOX="$HOME/binance-sandbox"
TOOL="tools/v20_absorb_cycle.py"
HOSTS="s1,s2,s5,s6"
LOG="$HOME/logs/v20_absorb_cycle.log"
trap 'rm -f "$STAGE"' EXIT

mkdir -p "$BACKUP_DIR" "$HOME/logs"
crontab -l > "$BACKUP" 2>/dev/null || true
if [ ! -s "$BACKUP" ]; then
  echo "REFUSING: the crontab backup at $BACKUP is empty. A host with no cron lines is possible, but it is also"
  echo "exactly what a wiped crontab looks like. Confirm by hand, then re-run with V20_ALLOW_EMPTY_CRONTAB=1."
  [ "${V20_ALLOW_EMPTY_CRONTAB:-0}" = "1" ] || exit 1
fi
BEFORE=$(wc -l < "$BACKUP" | tr -d ' ')
echo "crontab backed up to $BACKUP ($BEFORE lines)"

if [ ! -f "$SANDBOX/$TOOL" ]; then echo "REFUSING: $SANDBOX/$TOOL is not installed"; exit 1; fi
if [ ! -x "$PY" ]; then echo "REFUSING: $PY is not executable"; exit 1; fi

if grep -q "V20_ABSORB_CYCLE" "$BACKUP"; then
  echo "already installed; leaving the crontab untouched"
  grep "V20_ABSORB_CYCLE" "$BACKUP"
  exit 0
fi

cp "$BACKUP" "$STAGE"
cat >> "$STAGE" <<EOF
35 * * * * /usr/bin/flock -n /tmp/v20_absorb.lock /bin/bash -lc 'cd $SANDBOX && nice -n 19 ionice -c3 $PY $TOOL --phase absorb --hosts $HOSTS >> $LOG 2>&1' # V20_ABSORB_CYCLE absorb (hourly incremental extract fleet-wide; watermarked, so a quiet hour is nearly free)
40 4 * * * /usr/bin/flock -n /tmp/v20_absorb.lock /bin/bash -lc 'cd $SANDBOX && nice -n 19 ionice -c3 $PY $TOOL --phase inventory,compress,report --hosts $HOSTS --apply >> $LOG 2>&1' # V20_ABSORB_CYCLE shrink (daily, after the 03:20 brain export: compress only what a watermark proves was absorbed and that is past the brain window)
EOF

crontab "$STAGE"
AFTER=$(crontab -l | wc -l | tr -d ' ')
INSTALLED=$(crontab -l | grep -c "V20_ABSORB_CYCLE" || true)
echo "crontab now $AFTER lines (was $BEFORE); V20_ABSORB_CYCLE lines: $INSTALLED"
if [ "$INSTALLED" -ne 2 ] || [ "$AFTER" -lt "$BEFORE" ]; then
  echo "VERIFY FAILED -- restoring $BACKUP"
  crontab "$BACKUP"
  exit 1
fi
echo "OK"

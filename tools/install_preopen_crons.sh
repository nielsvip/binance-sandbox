#!/bin/bash
# install_preopen_crons — idempotent installer for the 2026-10-20 pre-open contract crons (Mac, UTC).
#
# CRONTAB SAFETY (CLAUDE.md): a bare `crontab` once emptied S2's table, so every edit goes through a script
# that (1) backs the table up and refuses to continue on an empty backup, (2) builds the new table in a temp
# file, (3) refuses to install anything SHORTER than the backup, (4) verifies the markers afterwards.
#
# What it installs, all marked so a re-run replaces instead of duplicating:
#   PREOPEN_GOLIVE      09:40/11:40/12:40Z Mon-Fri — apply the defaults DELTA so it finishes before the bell.
#                       The 13:30Z (EDT) / 14:30Z (EST) open is read from live_failover_common.nyse_session by
#                       the script itself, which budgets the run to end 20 min before it. 12:40 is the last
#                       slot that still clears an EDT open; it also takes the shared go-live lock, so the
#                       hourly :50 full apply skips that slot instead of running across the open as it did on
#                       2026-10-20 (12:51:58 -> still applying at 14:00Z).
#   PREOPEN_READINESS   every 20 min — measure the contract and leave the verdict in
#                       data/reports/preopen_readiness.json (fresh enough for the 1800 s window the
#                       stability pin trusts).
#   LF_STABILITY_PIN    every 2 min — hold the lease on S1 until the Mac is ready (--apply).
# macOS has no flock(1), so each entry runs under tools/v15_lock_run.sh.
set -u
ROOT=/Users/niels/Documents/binance
PY=/opt/anaconda3/envs/binance_env/bin/python
LOCKRUN="/bin/bash $ROOT/tools/v15_lock_run.sh"
STAMP=$(date -u +%Y%m%d%H%M)
BACKUP="$ROOT/backups/crontab_before_preopen_contract_$STAMP.txt"
NEW=$(mktemp /tmp/crontab_preopen.XXXXXX)
trap 'rm -f "$NEW"' EXIT
mkdir -p "$ROOT/backups"
crontab -l > "$BACKUP" 2>/dev/null || true
if [ ! -s "$BACKUP" ]; then
  echo "REFUSING: crontab backup $BACKUP is empty — not touching the table"
  exit 1
fi
BEFORE=$(wc -l < "$BACKUP")
grep -vE "# (PREOPEN_GOLIVE|PREOPEN_READINESS|LF_STABILITY_PIN)$" "$BACKUP" > "$NEW"
{
  echo "40 9,11,12 * * 1-5 $LOCKRUN /tmp/golive_preopen.lock /bin/bash $ROOT/tools/golive_preopen_apply.sh >> /tmp/golive_preopen.log 2>&1 # PREOPEN_GOLIVE"
  echo "*/20 * * * * cd $ROOT && $LOCKRUN /tmp/preopen_readiness.lock $PY -u $ROOT/tools/preopen_readiness_gate.py --host s1-pub --quiet >> /tmp/preopen_readiness.log 2>&1 # PREOPEN_READINESS"
  echo "*/2 * * * * cd $ROOT && $LOCKRUN /tmp/lf_stability_pin.lock $PY -u $ROOT/tools/live_failover_stability_pin.py --apply --quiet >> /tmp/lf_stability_pin.log 2>&1 # LF_STABILITY_PIN"
} >> "$NEW"
AFTER=$(wc -l < "$NEW")
if [ "$AFTER" -lt "$BEFORE" ]; then
  echo "REFUSING: new table $AFTER lines < backup $BEFORE lines"
  exit 1
fi
crontab "$NEW"
INSTALLED=$(crontab -l | grep -cE "# (PREOPEN_GOLIVE|PREOPEN_READINESS|LF_STABILITY_PIN)$")
echo "crontab $BEFORE -> $(crontab -l | wc -l | tr -d ' ') lines, $INSTALLED of 3 markers present, backup $BACKUP"
[ "$INSTALLED" = "3" ] || { echo "REFUSING TO CLAIM SUCCESS: only $INSTALLED markers installed"; exit 1; }

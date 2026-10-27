#!/usr/bin/env bash
# Install the hermetic live guard on S1's crontab, every minute. Run it ON S1.
#
# File-based crontab write only, and it refuses an empty backup -- nested ssh quoting wiped a crontab on 2026-10-13.
set -euo pipefail

STAMP=$(date -u +%Y%m%d%H%M)
BACKUP="$HOME/backups/crontab_before_s1_hermetic_guard_${STAMP}.txt"
STAGE=$(mktemp)
GUARD="$HOME/binance/tools/s1_live_hermetic_guard.sh"
LOG="$HOME/logs/s1_live_hermetic_guard_cron.log"
trap 'rm -f "$STAGE"' EXIT

mkdir -p "$HOME/backups" "$HOME/logs"
crontab -l > "$BACKUP" 2>/dev/null || true
[ -s "$BACKUP" ] || { echo "REFUSING: empty crontab backup at $BACKUP"; exit 1; }
BEFORE=$(wc -l < "$BACKUP" | tr -d ' ')
echo "backed up $BEFORE lines to $BACKUP"

[ -x "$GUARD" ] || { echo "REFUSING: $GUARD is not executable"; exit 1; }

grep -v "S1_LIVE_HERMETIC_GUARD" "$BACKUP" > "$STAGE"
cat >> "$STAGE" <<EOF
* * * * * /bin/bash $GUARD >> $LOG 2>&1 # S1_LIVE_HERMETIC_GUARD (USER 2026-10-27: S1 may NEVER spend CPU on live while the MacBook produces; stop-only, decides solely on the age of the Mac's own heartbeat)
EOF

crontab "$STAGE"
AFTER=$(crontab -l | wc -l | tr -d ' ')
LINES=$(crontab -l | grep -c "S1_LIVE_HERMETIC_GUARD" || true)
echo "crontab now $AFTER lines; S1_LIVE_HERMETIC_GUARD=$LINES"
if [ "$LINES" -ne 1 ] || [ "$AFTER" -lt "$BEFORE" ]; then
  echo "VERIFY FAILED -- restoring $BACKUP"
  crontab "$BACKUP"
  exit 1
fi
echo "OK"

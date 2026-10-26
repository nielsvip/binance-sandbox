#!/usr/bin/env bash
# Rewrite the V20_ABSORB_CYCLE crontab lines so the nightly job TRAINS, not just absorbs and compresses.
#
# Absorbing without training changes nothing the pilot reads: the heads only learn in the train phase, so an
# absorb-only schedule would keep extracting for ever while the published models stayed frozen.
#
# File-based crontab write only, and it refuses an empty backup -- nested ssh quoting wiped S2's crontab on 2026-10-13.
set -euo pipefail

STAMP=$(date -u +%Y%m%d%H%M)
BACKUP="$HOME/backups/crontab_before_v20_train_${STAMP}.txt"
STAGE=$(mktemp)
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
SANDBOX="$HOME/binance-sandbox"
TOOL="tools/v20_absorb_cycle.py"
HOSTS="s1,s2,s5,s6"
LOG="$HOME/logs/v20_absorb_cycle.log"
trap 'rm -f "$STAGE"' EXIT

mkdir -p "$HOME/backups" "$HOME/logs"
crontab -l > "$BACKUP" 2>/dev/null || true
[ -s "$BACKUP" ] || { echo "REFUSING: empty crontab backup at $BACKUP"; exit 1; }
BEFORE=$(wc -l < "$BACKUP" | tr -d ' ')
echo "backed up $BEFORE lines to $BACKUP"

grep -v "V20_ABSORB_CYCLE" "$BACKUP" > "$STAGE"
cat >> "$STAGE" <<EOF
35 * * * * /usr/bin/flock -n /tmp/v20_absorb.lock /bin/bash -lc 'cd $SANDBOX && nice -n 19 ionice -c3 $PY $TOOL --phase absorb --hosts $HOSTS >> $LOG 2>&1' # V20_ABSORB_CYCLE absorb (hourly incremental extract fleet-wide; watermarked, so a quiet hour is nearly free)
40 4 * * * /usr/bin/flock -n /tmp/v20_absorb.lock /bin/bash -lc 'cd $SANDBOX && nice -n 19 ionice -c3 $PY $TOOL --phase absorb,train,inventory,compress,report --hosts $HOSTS --apply >> $LOG 2>&1' # V20_ABSORB_CYCLE nightly (after the 03:20 brain export: absorb, UNION TRAIN + export + MANIFEST, then compress only what a watermark proves absorbed and that is past the brain window)
EOF

crontab "$STAGE"
AFTER=$(crontab -l | wc -l | tr -d ' ')
LINES=$(crontab -l | grep -c "V20_ABSORB_CYCLE" || true)
TRAINS=$(crontab -l | grep -c "absorb,train,inventory,compress,report" || true)
echo "crontab now $AFTER lines; V20_ABSORB_CYCLE=$LINES with-train=$TRAINS"
if [ "$LINES" -ne 2 ] || [ "$TRAINS" -ne 1 ] || [ "$AFTER" -ne "$BEFORE" ]; then
  echo "VERIFY FAILED -- restoring $BACKUP"
  crontab "$BACKUP"
  exit 1
fi
echo "OK"

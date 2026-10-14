#!/bin/bash
# Install the V17 KPI cron lines on S1 (BIBLE "V17 KPI + AUTO-TUNE (2026-10-14)"). Idempotent: replaces lines tagged
# "# V17_KPI" / "# V17_AUTOTUNE". Refuses to install when the current crontab is empty/short (backup check) or when the
# new crontab would lose lines. Backup: ~/binance-sandbox/backups/crontab_s1_before_v17_kpi_<ts>.txt
set -euo pipefail
ROOT=/home/niels/binance-sandbox
TS=$(date -u +%Y%m%d%H%M%S)
BK=$ROOT/backups/crontab_s1_before_v17_kpi_$TS.txt
mkdir -p "$ROOT/backups"
crontab -l > "$BK"
N=$(grep -c . "$BK" || true)
if [ "$N" -lt 10 ]; then echo "REFUSE: current crontab has only $N lines (backup $BK) — not touching it"; exit 2; fi
KPI="3,33 * * * * cd $ROOT && flock -n /tmp/v17_kpi.cron.lock timeout 1500 nice -n 15 python3 tools/v17_kpi.py >> /tmp/v17_kpi.log 2>&1 # V17_KPI"
AT="30 14 * * * cd $ROOT && flock -w 900 /tmp/v17_kpi.cron.lock timeout 1500 nice -n 15 python3 tools/v17_kpi.py --autotune >> /tmp/v17_kpi.log 2>&1 # V17_AUTOTUNE"
NEW=$(mktemp)
grep -v -e '# V17_KPI$' -e '# V17_AUTOTUNE$' "$BK" > "$NEW" || true
printf '%s\n%s\n' "$KPI" "$AT" >> "$NEW"
KEPT=$(grep -c . "$NEW" || true)
OLD_OTHER=$(grep -v -e '# V17_KPI$' -e '# V17_AUTOTUNE$' "$BK" | grep -c . || true)
if [ "$KEPT" -ne $((OLD_OTHER + 2)) ]; then echo "REFUSE: line count mismatch ($KEPT vs $OLD_OTHER+2)"; exit 3; fi
crontab "$NEW"
rm -f "$NEW"
echo "installed: $(crontab -l | grep -c -e '# V17_KPI$' -e '# V17_AUTOTUNE$') V17 lines; total $(crontab -l | grep -c .) (was $N); backup $BK"

#!/bin/bash
# v15_preopen_verify_mac.sh — Mac cron 13:05 UTC Mon-Fri. Live managers up,
# decisions fresh, book present, deploys intact. Loud on failure.
FAIL=0
say() { [ "$2" = "FAIL" ] && FAIL=1; echo "[$2] $1"; }
cd /Users/niels/Documents/binance || exit 1
for A in ang inf flz men fin; do
  pgrep -f "ez_manage.py --account $A" >/dev/null && say "ez $A alive" OK || say "ez $A DEAD" FAIL
done
pgrep -f "tradier_manage.py" >/dev/null && say "tradier alive" OK || say "tradier DEAD" FAIL
find data/decisions -name "*.jsonl" -mmin -120 2>/dev/null | grep -q . && say "decisions fresh" OK || say "decisions STALE" FAIL
[ -f data/hourly_reconfig/per_sym_store.db ] || [ -f data/hourly_reconfig/per_sym_active_config.json ] && say "book present" OK || say "book MISSING" FAIL
while read -r MD F; do
  [ -z "$MD" ] && continue
  CUR=$(md5sum "$F" 2>/dev/null | cut -c1-32)
  [ "$CUR" = "$MD" ] && say "$F intact" OK || say "$F CHANGED $CUR vs $MD" FAIL
done < data/preopen_tripwire_20261008.txt
launchctl list 2>/dev/null | grep -q v15sync && say "v15sync agent" OK || say "v15sync agent DOWN" FAIL
crontab -l 2>/dev/null | grep -q V15_TRADIER_OPEN && say "tradier open cron" OK || say "tradier open cron GONE" FAIL
crontab -l 2>/dev/null | grep -q V15_BIBLE_CYCLE && say "bible cron" OK || say "bible cron GONE" FAIL
[ $FAIL -eq 0 ] && echo "PREOPEN MAC ALL GREEN $(date -u +%FT%TZ)" || echo "PREOPEN MAC FAILURES $(date -u +%FT%TZ)"
exit $FAIL

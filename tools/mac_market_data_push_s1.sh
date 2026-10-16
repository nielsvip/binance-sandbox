#!/bin/bash
# mac_market_data_push_s1.sh — push the Mac's OWN market_data snapshots to S1, 24/7, whoever holds the lease.
#
# USER 2026-10-15: "We can also rsync market_data TO s1 24/7 so when the entire system starts it is always ready
# with the latest data."
#
# Why this exists: S1 judges the Mac's data freshness from the newest ~/binance/data/market_data_2*.json on S1
# (live_failover_s1.mac_view -> mac_market_data_push_age), and it is also the data S1 starts from on a takeover.
# During the 2026-10-15 19:21Z disconnect that push stopped and never resumed — 68 minutes later S1 still showed
# mac_market_data_push_age=4048s. Nothing on the Mac was responsible for it, so nothing restarted it. Now something is.
#
# SAFETY: only the TIMESTAMPED snapshots are pushed, never latest_market_data.json. S1's own ez_indicators writes
# latest_market_data.json on S1, and while S1 is ACTIVE that file is fresher than the Mac's — overwriting it would
# hand S1 worse data than it produces itself. The timestamped files are additive and are exactly what S1 measures.
set -u
BASE=/Users/niels/Documents/binance
KEEP="${MAC_MD_PUSH_KEEP:-3}"        # newest N snapshots per run
LOCK=/tmp/.mac_market_data_push_s1.lock
/usr/bin/shlock -f "$LOCK" -p $$ || exit 0
trap 'rm -f "$LOCK"' EXIT INT TERM

cd "$BASE" || exit 1
# Mac-own snapshots only: exclude S1-delivered (_s1.json) and worker files. bash 3.2 on macOS has no mapfile.
FILES=$(ls -t data/market_data_2*.json 2>/dev/null | grep -v "_s1\.json$" | grep -v worker | head -n "$KEEP" | tr '\n' ' ')
[ -z "$FILES" ] && exit 0
NEWEST=$(echo "$FILES" | awk '{print $1}')
N=$(echo "$FILES" | wc -w | tr -d ' ')

SSH='ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o ControlPath=none'
rc=1
for HOST in s1-pub s1-int; do
  if rsync -az --timeout=25 -e "$SSH" $FILES "$HOST:~/binance/data/" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) pushed $N snapshot(s) to $HOST: $(basename "$NEWEST")"
    rc=0
    break
  fi
done
[ "$rc" -ne 0 ] && echo "$(date -u +%FT%TZ) PUSH FAILED to both s1-pub and s1-int (S1 will see mac_market_data_push_age grow)"
exit 0

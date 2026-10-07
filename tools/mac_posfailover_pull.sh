#!/bin/bash
# mac_posfailover_pull.sh — when S1 holds a position takeover, pull its fresh files (newer wins).
# Runs on Mac (launchd com.niels.s1-posfailover-pull, every 30s). No-op unless S1 has
# s1_posfailover_active_* flags. Uses rsync --update (never overwrites newer Mac files, never deletes).
set -u
REPO="/Users/niels/Documents/binance"
LOG="/Users/niels/logs/mac_posfailover_pull.log"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new"
FLAGS=$(ssh $SSH_OPTS s1-pub "ls /home/niels/binance/data/s1_posfailover_active_* 2>/dev/null" 2>/dev/null) || exit 0
[ -z "$FLAGS" ] && exit 0
mkdir -p "$(dirname "$LOG")"
for acct in ang inf flz men fin; do
    echo "$FLAGS" | grep -q "_${acct}$" || continue
    rsync -az --update --timeout=15 -e "ssh $SSH_OPTS" "s1-pub:/home/niels/binance/${acct}/{long,short}_positions.json" "$REPO/${acct}/" >>"$LOG" 2>&1 || true
    echo "$(date -u +%FT%TZ) pulled $acct (S1 takeover active)" >>"$LOG"
done
exit 0

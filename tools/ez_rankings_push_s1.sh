#!/bin/bash
# ez_rankings_push_s1.sh — USER 2026-10-14: "ez_rankings only runs on mac but should rsync to s1 so when s1 needs to kick in
# for a mac failure it always has the latest data from the moment it starts running".
# Mac cron every 2 min. Pushes ONLY the tradeable symbol lists ez_rankings configures (symbols_<acc>_<side>.json, symbols.json,
# symbols_active.json) to S1 ~/binance (failover live dir) and ~/binance-sandbox (v15 director/scheduler priorities).
# Additive, never deletes, atomic per file (rsync --delay-updates), ~100 KB. mac_livecode_push.sh (15 min) stays the code push.
set -u
REPO="/Users/niels/Documents/binance"
LOG="/Users/niels/logs/ez_rankings_push_s1.log"
mkdir -p "$(dirname "$LOG")"
cd "$REPO" || exit 1
FILES=()
for f in symbols.json symbols_active.json symbols_*_long.json symbols_*_short.json; do  # USER: "it is about symbols_*.json" (rankings.json not needed)
    [ -f "$f" ] && FILES+=("$f")
done
# USER 2026-10-15: "tradeable_keys is crypto only, symbols_trb_long/short are the stocks version that also keeps
# updating" + "make sure v15_fleet_scheduler always checks whether a sym_side is still tradeable before queuing it".
# tradeable_keys.json was NEVER in this push, so ~/binance-sandbox — the tree the fleet scheduler actually runs from
# (S1 cron */2) — held a 2.1-DAY-OLD crypto tradeable list while the stock lists were 3 minutes old. The scheduler was
# therefore queueing crypto sym_sides that had already dropped out of the universe. Both halves of the tradeable truth
# ship together now, plus the tokenized-stock denylist the universe subtracts.
for f in tradeable_keys.json data/excluded_tokenized_stocks.json; do
    [ -f "$f" ] && FILES+=("$f")
done
[ "${#FILES[@]}" -gt 0 ] || { echo "$(date -u +%FT%TZ) no files" >>"$LOG"; exit 0; }
SHELLS=("ssh -o BatchMode=yes -o ConnectTimeout=6 s1-int" "ssh -i /Users/niels/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=6 -o ControlMaster=no -o ControlPath=none niels@157.180.125.52")
push_to() { # push_to <dest-dir>
    local dest="$1" sh host rc
    for sh in "${SHELLS[@]}"; do
        host="${sh##* }"
        rsync -aR --delay-updates --timeout=60 -e "${sh% *}" "${FILES[@]}" "$host:$dest" >>"$LOG" 2>&1
        rc=$?
        [ $rc -eq 0 ] && return 0
    done
    return 1
}
ok=""
push_to "/home/niels/binance/" && ok="live"
push_to "/home/niels/binance-sandbox/" && ok="$ok+sandbox"
echo "$(date -u +%FT%TZ) push ${ok:-FAILED} files=${#FILES[@]}" >>"$LOG"
exit 0

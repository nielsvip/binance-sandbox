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
    # USER 2026-10-14 SINGLE POSITION WRITER: ez_positions_service is the only writer. S1 copies (whose realtime re-stamps
    # opened_at on every takeover) may land on the Mac ONLY while the Mac service heartbeat is stale (> 90 s).
    HB="$REPO/${acct}/.positions_service_heartbeat.json"
    if [ -f "$HB" ]; then
        HB_TS=$(/usr/bin/python3 -c "import json,sys;print(int(float(json.load(open(sys.argv[1])).get('ts',0))))" "$HB" 2>/dev/null || echo 0)
        if [ $(( $(date +%s) - HB_TS )) -le 90 ]; then
            echo "$(date -u +%FT%TZ) SKIP $acct pull — Mac ez_positions_service alive (heartbeat $(( $(date +%s) - HB_TS ))s)" >>"$LOG"
            continue
        fi
    fi
    # USER 2026-10-14 "opened_at can NEVER be overwritten by a positions pull": never copy over the live files — pull into a
    # staging dir and merge PER POSITION through position_writer_guard (held positions keep their opened_at; a pulled file
    # may only add a genuinely new position or mark a close).
    STAGE="$REPO/data/posfailover_incoming/${acct}"
    mkdir -p "$STAGE"
    rsync -az --timeout=15 -e "ssh $SSH_OPTS" "s1-pub:/home/niels/binance/${acct}/{long,short}_positions.json" "$STAGE/" >>"$LOG" 2>&1 || true
    for side in long short; do
        [ -s "$STAGE/${side}_positions.json" ] || continue
        (cd "$REPO" && /opt/anaconda3/envs/binance_env/bin/python position_writer_guard.py merge "$REPO/${acct}/${side}_positions.json" "$STAGE/${side}_positions.json") >>"$LOG" 2>&1 || echo "$(date -u +%FT%TZ) MERGE FAILED $acct $side" >>"$LOG"
    done
    echo "$(date -u +%FT%TZ) merged $acct from S1 (S1 takeover active, per-position merge)" >>"$LOG"
done
exit 0

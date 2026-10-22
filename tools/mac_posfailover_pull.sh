#!/bin/bash
# mac_posfailover_pull.sh — when S1 holds a position takeover, pull its fresh files (newer wins).
# Runs on Mac (launchd com.niels.s1-posfailover-pull, every 30s). No-op unless S1 has
# s1_posfailover_active_* flags. Uses rsync --update (never overwrites newer Mac files, never deletes).
set -u
# 2026-10-22 STALENESS GUARD + TEST HOOKS (all additive, defaults = legacy behaviour):
# S1's takeover flags say "S1 is producing", but flags can outlive S1's writers (retired loop, dead units —
# measured 2026-10-22: flags 07:03Z with S1 live-dir files from March and sandbox files 23 h old). rsync -a
# preserves the SOURCE mtime, so a staged file's age IS the S1 file's age; anything older than
# POSFAILOVER_MAX_S1_FILE_AGE_S is never merged (fail closed — stale S1 rows must not touch live files).
# POSFAILOVER_REPO/SSH/RSYNC/PY/LOG overrides exist so tests can drive this script with fakes.
REPO="${POSFAILOVER_REPO:-/Users/niels/Documents/binance}"
LOG="${POSFAILOVER_LOG:-/Users/niels/logs/mac_posfailover_pull.log}"
POSFAILOVER_SSH="${POSFAILOVER_SSH:-ssh}"
POSFAILOVER_RSYNC="${POSFAILOVER_RSYNC:-rsync}"
POSFAILOVER_PY="${POSFAILOVER_PY:-/opt/anaconda3/envs/binance_env/bin/python}"
POSFAILOVER_MAX_S1_FILE_AGE_S="${POSFAILOVER_MAX_S1_FILE_AGE_S:-600}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new"
FLAGS=$($POSFAILOVER_SSH $SSH_OPTS s1-pub "ls /home/niels/binance/data/s1_posfailover_active_* 2>/dev/null" 2>/dev/null) || exit 0
[ -z "$FLAGS" ] && exit 0
mkdir -p "$(dirname "$LOG")"
for acct in ang inf flz men fin; do
    echo "$FLAGS" | grep -q "_${acct}$" || continue
    # USER 2026-10-14 SINGLE POSITION WRITER: ez_positions_service is the only writer. S1 copies (whose realtime re-stamps
    # opened_at on every takeover) may land on the Mac ONLY while the Mac service heartbeat is stale (> 90 s).
    # 2026-10-22 (USER: "'blocked by binance' BULLSHIT the moment that happens s1 needs to start the script and kick
    # the info into macbook immediately"): ALIVE IS NOT THE QUESTION — REACHABLE IS. This gate used to skip the pull
    # whenever the Mac's writer heartbeat was under 90 s old, which is exactly the state a Binance-IP-banned Mac is
    # in: the process is healthy, it just cannot fetch. Measured today, fin took 70x HTTP 418 and 23 "IP ban" and men
    # 67x 429 + 41 -1003; both writers kept heartbeating "fetch ok", so this skipped every pull and fin sat on 671
    # unexecuted OPEN intents with ZERO positions while S1 — a different IP, able to fetch — had nothing asked of it.
    # A ban is the ONE fault local repair cannot fix by construction, so it is the one that must accept S1's data.
    # ban_remaining_s is written on every heartbeat by position_writer_guard.write_heartbeat.
    HB="$REPO/${acct}/.positions_service_heartbeat.json"
    if [ -f "$HB" ]; then
        read -r HB_TS HB_BAN <<EOF
$(/usr/bin/python3 -c "
import json,sys
try:
    d=json.load(open(sys.argv[1]))
    print(int(float(d.get('ts') or 0)), float(d.get('ban_remaining_s') or 0.0))
except Exception:
    print(0, 0.0)
" "$HB" 2>/dev/null || echo "0 0.0")
EOF
        HB_AGE=$(( $(date +%s) - ${HB_TS:-0} ))
        HB_BANNED=$(/usr/bin/python3 -c "import sys;print(1 if float(sys.argv[1])>0 else 0)" "${HB_BAN:-0}" 2>/dev/null || echo 0)
        if [ "$HB_AGE" -le 90 ] && [ "$HB_BANNED" = "0" ]; then
            MERGE_OK=0
        fi
        if [ "$HB_BANNED" = "1" ]; then
            echo "$(date -u +%FT%TZ) $acct Mac writer alive (heartbeat ${HB_AGE}s) but BINANCE-BANNED ${HB_BAN}s — S1 has a different IP; staging + merge allowed" >>"$LOG"
        fi
    fi
    # USER 2026-10-14 "opened_at can NEVER be overwritten by a positions pull": never copy over the live files — pull into a
    # staging dir and merge PER POSITION through position_writer_guard (held positions keep their opened_at; a pulled file
    # may only add a genuinely new position or mark a close).
    STAGE="$REPO/data/posfailover_incoming/${acct}"
    mkdir -p "$STAGE"
    $POSFAILOVER_RSYNC -az --timeout=15 -e "$POSFAILOVER_SSH $SSH_OPTS" "s1-pub:/home/niels/binance/${acct}/{long,short}_positions.json" "$STAGE/" >>"$LOG" 2>&1 || true
    for side in long short; do
        _staged="$STAGE/${side}_positions.json"
        [ -s "$_staged" ] || continue
        _stage_age=$(/usr/bin/python3 -c "import os,time,sys;print(int(time.time()-os.stat(sys.argv[1]).st_mtime))" "$_staged" 2>/dev/null || echo 9999999)
        case "$_stage_age" in ''|*[!0-9]*) _stage_age=9999999 ;; esac
        if [ "$_stage_age" -gt "$POSFAILOVER_MAX_S1_FILE_AGE_S" ]; then
            echo "$(date -u +%FT%TZ) SKIP $acct $side — staged S1 file ${_stage_age}s old (> ${POSFAILOVER_MAX_S1_FILE_AGE_S}s); S1 writers are down, never merging stale rows" >>"$LOG"
            continue
        fi
        (cd "$REPO" && "$POSFAILOVER_PY" position_writer_guard.py merge "$REPO/${acct}/${side}_positions.json" "$_staged") >>"$LOG" 2>&1 || echo "$(date -u +%FT%TZ) MERGE FAILED $acct $side" >>"$LOG"
    done
    echo "$(date -u +%FT%TZ) merged $acct from S1 (S1 takeover active, per-position merge)" >>"$LOG"
done
exit 0

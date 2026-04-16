#!/usr/bin/env bash
# s1_s2_autosync.sh — Polls S1 (157.180.125.52) and S2 (204.168.181.211) every 60s.
# On reachability + drift detected, rsyncs the 6 critical files (CLAUDE.md rules) and
# deletes the stale rate_cascade.py remnant from the reverted cascade experiment.
#
# Runs forever (daemon-style). Checks md5 parity every tick, only rsyncs when drift.
# Safe to run multiple instances (pkill before start ensures only one).
# Logs: /tmp/s1_s2_autosync.log. Kill via: pkill -f s1_s2_autosync
set -u

BASE=/Users/niels/Documents/binance
LOG=/tmp/s1_s2_autosync.log
FILES=(ez_positions_quick.py config.py ez_manage.py tradier_manage.py config_tradier.py ez_positions_service.py)
SSH_OPTS="-o ConnectTimeout=15 -o BatchMode=yes -o ServerAliveInterval=5 -o ServerAliveCountMax=2"

try_sync() {
    local host="$1" label="$2"
    ping -c 1 -W 2000 "$host" >/dev/null 2>&1 || return 1
    cd "$BASE" || return 1
    local local_md5 remote_md5
    local_md5=$(md5 -q ez_positions_quick.py)
    remote_md5=$(ssh $SSH_OPTS "niels@${host}" "md5sum /home/niels/binance-sandbox/ez_positions_quick.py 2>/dev/null | awk '{print \$1}'" 2>>"$LOG")
    [[ -z "$remote_md5" ]] && return 1
    if [[ "$local_md5" == "$remote_md5" ]]; then
        return 0
    fi
    echo "$(date -u +%FT%TZ) ${label} DRIFT_DETECTED local=$local_md5 remote=$remote_md5 — syncing" >>"$LOG"
    ssh $SSH_OPTS "niels@${host}" "rm -f /home/niels/binance-sandbox/rate_cascade.py" 2>>"$LOG"
    rsync -az --timeout=30 --existing --update "${FILES[@]}" "niels@${host}:/home/niels/binance-sandbox/" 2>>"$LOG" || return 1
    remote_md5=$(ssh $SSH_OPTS "niels@${host}" "md5sum /home/niels/binance-sandbox/ez_positions_quick.py 2>/dev/null | awk '{print \$1}'" 2>>"$LOG")
    if [[ "$local_md5" == "$remote_md5" ]]; then
        echo "$(date -u +%FT%TZ) ${label} SYNC_OK md5=$local_md5" >>"$LOG"
        return 0
    fi
    echo "$(date -u +%FT%TZ) ${label} SYNC_FAILED post_md5=$remote_md5" >>"$LOG"
    return 1
}

echo "$(date -u +%FT%TZ) autosync_start pid=$$" >>"$LOG"
while true; do
    try_sync 157.180.125.52 S1
    try_sync 204.168.181.211 S2
    sleep 60
done

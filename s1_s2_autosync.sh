#!/usr/bin/env bash
# s1_s2_autosync.sh — Keeps S1 + S2 sandboxes bit-identical to MacBook.
# Probes via SSH md5sum (Hetzner filters ICMP — ping-based probing gives false "down").
# On drift, rsyncs the 6 critical files (CLAUDE.md rules) + deletes rate_cascade.py remnant.
#
# Runs forever under launchd (com.niels.s1-s2-autosync, KeepAlive=true).
# Idempotent — safe to run multiple instances (pkill before start).
# Logs: /tmp/s1_s2_autosync.log. Kill via: pkill -f s1_s2_autosync
set -u

BASE=/Users/niels/Documents/binance
LOG=/tmp/s1_s2_autosync.log
FILES=(ez_positions_quick.py config.py ez_manage.py tradier_manage.py config_tradier.py ez_positions_service.py)
SSH_OPTS="-o ConnectTimeout=20 -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"

try_sync() {
    local host="$1" label="$2"
    cd "$BASE" || return 1
    local local_md5 remote_md5
    local_md5=$(md5 -q ez_positions_quick.py)
    remote_md5=$(ssh $SSH_OPTS "${host}" "md5sum /home/niels/binance-sandbox/ez_positions_quick.py 2>/dev/null | awk '{print \$1}'" 2>>"$LOG")
    if [[ -z "$remote_md5" ]]; then
        # SSH itself failed — host truly down (binance_supervisor.py handles hetzner reset)
        echo "$(date -u +%FT%TZ) ${label} SSH_UNREACHABLE (supervisor handles hw reset)" >>"$LOG"
        return 1
    fi
    if [[ "$local_md5" == "$remote_md5" ]]; then
        return 0
    fi
    echo "$(date -u +%FT%TZ) ${label} DRIFT_DETECTED local=$local_md5 remote=$remote_md5 — syncing" >>"$LOG"
    ssh $SSH_OPTS "${host}" "rm -f /home/niels/binance-sandbox/rate_cascade.py" 2>>"$LOG"
    rsync -az --timeout=30 --existing --update "${FILES[@]}" "${host}:/home/niels/binance-sandbox/" 2>>"$LOG" || return 1
    remote_md5=$(ssh $SSH_OPTS "${host}" "md5sum /home/niels/binance-sandbox/ez_positions_quick.py 2>/dev/null | awk '{print \$1}'" 2>>"$LOG")
    if [[ "$local_md5" == "$remote_md5" ]]; then
        echo "$(date -u +%FT%TZ) ${label} SYNC_OK md5=$local_md5" >>"$LOG"
        return 0
    fi
    echo "$(date -u +%FT%TZ) ${label} SYNC_FAILED post_md5=$remote_md5" >>"$LOG"
    return 1
}

echo "$(date -u +%FT%TZ) autosync_start pid=$$ via gateway (s1-int/s2-int per CLAUDE.md rule)" >>"$LOG"
while true; do
    try_sync s1-int S1
    try_sync s2-int S2
    sleep 60
done

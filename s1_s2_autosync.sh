#!/usr/bin/env bash
# s1_s2_autosync.sh — MacBook → S1 + S2 one-way script sync, checksum-based.
#
# User directive 2026-04-18: "ANY CHANGE HAPPENS ON ALL 3 MACHINES SIMULTANEOUSLY."
# 2026-04-19 update: "Trading scripts AND config should be auto-synced continuously
# (like market data). Monitors should focus on test progress, not code version."
#
# MacBook `/Users/niels/Documents/binance` is the AUTHORITY for every file.
# S1 `s1-int:/home/niels/binance-sandbox/` and S2 `s2-int:/home/niels/binance-sandbox/`
# must be bit-identical for every ez_*, tradier_*, wt_*, v8_*, backtest_v8_*,
# utils.py, symbols.json, breakout_multi_lung.py, config.py, config_tradier.py.
#
# Sweeps use V8_OVERRIDE_FILE overlays — they NEVER mutate config.py on sandboxes,
# so there's no reason for configs to diverge. Auto-syncing them eliminates the
# entire "sweep ran on stale config" bug class.
#
# EXCLUDED from sync (by design):
#   - *.pyc, __pycache__             (compiled junk)
#   - any .log / .json / .csv data files (not in the script globs anyway)
#   - per-account realtime stubs (MacBook-only)
#
# Push semantics:
#   rsync --checksum  → MacBook version wins on any md5 mismatch (regardless of mtime,
#                       so a stray server-side edit can't block the push by being newer).
#   rsync --existing  → don't CREATE new files on sandbox (prevents re-adding archived
#                       MacBook files per CLAUDE.md rule). New MacBook scripts need a
#                       one-time manual push to seed.
#
# Runs under launchd (com.niels.s1-s2-autosync, KeepAlive=true, 60s tick).
# Logs: /tmp/s1_s2_autosync.log. Kill via: pkill -f s1_s2_autosync
set -u

BASE=/Users/niels/Documents/binance
LOG=/tmp/s1_s2_autosync.log
# ControlMaster=no + ControlPath=none → bypass interactive-session sockets that launchd can't reach.
SSH_OPTS="-o ConnectTimeout=20 -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o ControlMaster=no -o ControlPath=none"
DEST_PATH=/home/niels/binance-sandbox/
SLEEP_SEC=2  # user directive 2026-04-18: "rsync should probably take place at least every second"

cd "$BASE" || { echo "$(date -u +%FT%TZ) CANNOT_CD $BASE" >>"$LOG"; exit 1; }
shopt -s nullglob

push_to() {
    local host="$1" label="$2"
    cd "$BASE" || return 1
    # Build file list once per tick (bash 3.2 compatible, no mapfile).
    local files=()
    # Skip per-account realtime stubs (MacBook-only, not on sandboxes).
    local skip_list=" ez_positions_realtime_ang.py ez_positions_realtime_fin.py ez_positions_realtime_flz.py ez_positions_realtime_inf.py ez_positions_realtime_men.py "
    for pat in ez_*.py tradier_*.py wt_*.py v8_*.py backtest_v8_*.py utils.py symbols.json breakout_multi_lung.py config.py config_tradier.py; do
        for f in $pat; do
            [[ -f "$f" ]] || continue
            [[ "$skip_list" == *" $f "* ]] && continue
            files+=("$f")
        done
    done
    [[ ${#files[@]:-0} -eq 0 ]] && return 1
    # Fetch all remote md5s in ONE ssh call (cheap).
    local remote_md5s
    remote_md5s=$(ssh $SSH_OPTS "${host}" "cd ${DEST_PATH} 2>/dev/null && md5sum ${files[*]} 2>/dev/null" 2>>"$LOG")
    if [[ -z "$remote_md5s" ]]; then
        echo "$(date -u +%FT%TZ) ${label} SSH_MD5_FETCH_FAIL — skipping tick" >>"$LOG"
        return 1
    fi
    # Diff: any file where local md5 != remote md5 goes to push list.
    local push_list=()
    local f local_md5 remote_md5
    for f in "${files[@]}"; do
        local_md5=$(md5 -q "$f" 2>/dev/null)
        [[ -z "$local_md5" ]] && continue
        remote_md5=$(echo "$remote_md5s" | awk -v f="$f" '$NF==f {print $1; exit}')
        # If remote md5 empty → file doesn't exist on sandbox → respect --existing semantic, skip.
        [[ -z "$remote_md5" ]] && continue
        [[ "$local_md5" != "$remote_md5" ]] && push_list+=("$f")
    done
    [[ ${#push_list[@]:-0} -eq 0 ]] && return 0  # no drift
    # Push only the drifted files, one rsync call (short arg list, no 3.4.1 abort).
    local n=${#push_list[@]}
    echo "$(date -u +%FT%TZ) ${label} DRIFT_PUSH ${n} file(s): ${push_list[*]:0:8}$([ $n -gt 8 ] && echo ' ...' )" >>"$LOG"
    rsync -a --checksum --existing \
        -e "ssh $SSH_OPTS" \
        "${push_list[@]}" "${host}:${DEST_PATH}" 2>>"$LOG"
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "$(date -u +%FT%TZ) ${label} RSYNC_FAIL rc=$rc files=${push_list[*]:0:4}" >>"$LOG"
        return 1
    fi
    echo "$(date -u +%FT%TZ) ${label} PUSHED_OK ${n} file(s)" >>"$LOG"
    return 0
}

echo "$(date -u +%FT%TZ) autosync_start pid=$$ mode=checksum_macbook_authoritative interval=${SLEEP_SEC}s" >>"$LOG"
while true; do
    push_to s1-int S1
    push_to s2-int S2
    sleep "$SLEEP_SEC"
done

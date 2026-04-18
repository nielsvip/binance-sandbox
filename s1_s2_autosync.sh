#!/usr/bin/env bash
# s1_s2_autosync.sh — MacBook → S1 + S2 one-way script sync, checksum-based.
#
# User directive 2026-04-18: "ANY CHANGE HAPPENS ON ALL 3 MACHINES SIMULTANEOUSLY.
# Sandboxes run ACTUAL scripts but change switches in config. ONLY config files differ."
#
# MacBook `/Users/niels/Documents/binance` is the AUTHORITY for every script.
# S1 `s1-int:/home/niels/binance-sandbox/` and S2 `s2-int:/home/niels/binance-sandbox/`
# must be bit-identical for every ez_*, tradier_*, wt_*, v8_*, backtest_v8_*, utils.py,
# symbols.json, breakout_multi_lung.py.
#
# EXCLUDED from sync (divergent by design):
#   - config.py, config_tradier.py  (the only files allowed to differ)
#   - *.pyc, __pycache__             (compiled junk)
#   - any .log / .json / .csv data files (not in the script globs anyway)
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
    # bash 3.2 compatible (macOS default) — no mapfile. Expand globs inline.
    local files=()
    for pat in ez_*.py tradier_*.py wt_*.py v8_*.py backtest_v8_*.py utils.py symbols.json breakout_multi_lung.py; do
        for f in $pat; do
            [[ -f "$f" ]] && files+=("$f")
        done
    done
    if [[ ${#files[@]:-0} -eq 0 ]]; then
        echo "$(date -u +%FT%TZ) ${label} NO_FILES_TO_SYNC" >>"$LOG"
        return 1
    fi
    local out
    out=$(rsync -a --checksum --existing --itemize-changes \
        -e "ssh $SSH_OPTS" \
        "${files[@]}" "${host}:${DEST_PATH}" 2>>"$LOG")
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "$(date -u +%FT%TZ) ${label} RSYNC_FAIL rc=$rc" >>"$LOG"
        return 1
    fi
    # Filter rsync's itemize output for actual file updates (lines starting with ">" = sent).
    local changed
    changed=$(echo "$out" | grep -E '^>' || true)
    if [[ -n "$changed" ]]; then
        local n
        n=$(echo "$changed" | wc -l | tr -d ' ')
        echo "$(date -u +%FT%TZ) ${label} PUSHED ${n} file(s) (checksum drift corrected — MacBook wins):" >>"$LOG"
        echo "$changed" | head -20 >>"$LOG"
    fi
    return 0
}

echo "$(date -u +%FT%TZ) autosync_start pid=$$ mode=checksum_macbook_authoritative interval=${SLEEP_SEC}s" >>"$LOG"
while true; do
    push_to s1-int S1
    push_to s2-int S2
    sleep "$SLEEP_SEC"
done

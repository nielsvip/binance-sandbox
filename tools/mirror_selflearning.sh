#!/bin/bash
# mirror_selflearning — every self-learning rebuild file on TOSHIBA_EXT exists elsewhere too
# (USER 2026-10-20: the drive is unreliable; mirror SQL DBs + rebuild data to E3T or s5/s6).
# SET (only what rebuilds the v15_pilot self-learning brain; delta logs stay on-host by rule):
#   big  (S5/S6 only): binance_archive/eval_cache/eval_cache_v1_master.db
#   small (S5/S6/E3T):  brain/{per_sym_settings.json, per_sym_full_sets.json,
#                        causal_learner.db, v17_priors.json}  (Mac data/ staged to TOSHIBA first,
#                        so TOSHIBA stays the single choke point: everything there exists elsewhere)
# SQLite safety: WAL-checkpoint the master before rsync; rsync deltas only. Never deletes.
set -u
BASE=/Users/niels/Documents/binance
TOSH=/Volumes/TOSHIBA_EXT
BRAIN="$TOSH/binance_archive/selflearning/brain"
MASTER="$TOSH/binance_archive/eval_cache/eval_cache_v1_master.db"
LOG=/tmp/mirror_selflearning.log
say() { echo "$(date -u +%FT%TZ) $*" >>"$LOG"; }
[ -d "$TOSH" ] || { say "SKIP TOSHIBA_EXT not mounted"; exit 0; }
mkdir -p "$BRAIN"
for f in per_sym_settings.json per_sym_full_sets.json causal_learner.db; do
    [ -f "$BASE/data/$f" ] && cp -f "$BASE/data/$f" "$BRAIN/$f" 2>>"$LOG"
done
[ -f "$BASE/data/causal/v17_priors.json" ] && cp -f "$BASE/data/causal/v17_priors.json" "$BRAIN/v17_priors.json" 2>>"$LOG"
if [ -f "$MASTER" ]; then
    sqlite3 "$MASTER" "PRAGMA wal_checkpoint(TRUNCATE);" >>"$LOG" 2>&1 || true
fi
RSYNC_BIN=/opt/homebrew/bin/rsync
[ -x "$RSYNC_BIN" ] || RSYNC_BIN=rsync
mirror_host() { # <ssh-name> <dest-base>
    "$RSYNC_BIN" -az --timeout=300 \
        -e "ssh -o ConnectTimeout=15 -o BatchMode=yes" \
        "$TOSH/binance_archive/selflearning/" "$1:$2/selflearning/" 2>>"$LOG" \
    && "$RSYNC_BIN" -az --timeout=300 \
        -e "ssh -o ConnectTimeout=15 -o BatchMode=yes" \
        "$TOSH/binance_archive/eval_cache/" "$1:$2/eval_cache/" 2>>"$LOG" \
    && say "MIRROR $1 ok" || say "MIRROR $1 FAILED (retries in 6h)"
}
[ -f "$MASTER" ] && say "master $(du -sm "$MASTER" 2>/dev/null | cut -f1)M present"
mirror_host s5 "~/mirror_toshiba"
timeout 10 ssh -o ConnectTimeout=6 -o BatchMode=yes s6 hostname >/dev/null 2>&1 \
    && mirror_host s6 "~/mirror_toshiba" || say "MIRROR s6 unreachable (retries in 6h)"
if [ -d /Volumes/E3T ]; then
    EXCLUDE_MASTER=1 "$RSYNC_BIN" -az --timeout=300 --exclude='eval_cache/' \
        "$TOSH/binance_archive/selflearning/" /Volumes/E3T/mirror_toshiba/ 2>>"$LOG" \
        && say "MIRROR E3T ok (brain only; 45G master does not fit 13G free)" \
        || say "MIRROR E3T FAILED (retries in 6h)"
fi
say "done"

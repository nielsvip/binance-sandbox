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
LOCK=/tmp/mirror_selflearning.lock
if ! mkdir "$LOCK" 2>/dev/null; then say "SKIP another mirror running"; exit 0; fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
# The drive is flaky (USER 2026-10-20): every touch below is time-boxed so a hung
# device degrades to a logged SKIP instead of wedging the 6h chain.
if ! timeout 60 mkdir -p "$BRAIN" 2>>"$LOG"; then say "SKIP TOSHIBA_EXT unresponsive"; exit 0; fi
for f in per_sym_settings.json per_sym_full_sets.json causal_learner.db; do
    [ -f "$BASE/data/$f" ] && timeout 120 cp -f "$BASE/data/$f" "$BRAIN/$f" 2>>"$LOG"
done
[ -f "$BASE/data/causal/v17_priors.json" ] && timeout 120 cp -f "$BASE/data/causal/v17_priors.json" "$BRAIN/v17_priors.json" 2>>"$LOG"
if [ -f "$MASTER" ]; then
    timeout 300 sqlite3 "$MASTER" "PRAGMA wal_checkpoint(TRUNCATE);" >>"$LOG" 2>&1 || say "WARN checkpoint timed out, rsync proceeds anyway"
fi
RSYNC_BIN=/opt/homebrew/bin/rsync
[ -x "$RSYNC_BIN" ] || RSYNC_BIN=rsync
RSYNC_SSH="ssh -o ConnectTimeout=15 -o BatchMode=yes"
# Intermittent drive (USER 2026-10-21): --partial resumes the 43G master across
# appearance windows; biggest/most-irreplaceable file goes FIRST so a short
# window still moves the master forward. Never deletes.
cp1() { # <src-file> <user@host:dest> — one resumable file
    [ -f "$1" ] || return 0
    timeout 1500 "$RSYNC_BIN" -az --partial --timeout=300 -e "$RSYNC_SSH" "$1" "$2" 2>>"$LOG"
}
verify_host() { # <ssh-name> <dest-base> — log local vs remote bytes per DB
    for f in eval_cache_v1_master.db eval_cache_rebuilt_v1.db bench_merge.db; do
        L=$(stat -f%z "$TOSH/binance_archive/eval_cache/$f" 2>/dev/null || echo 0)
        R=$(timeout 30 ssh -o ConnectTimeout=15 -o BatchMode=yes "$1" "stat -c%s $2/eval_cache/$f 2>/dev/null || echo 0" 2>/dev/null || echo 0)
        say "VERIFY $1 $f local=$L remote=$R"
    done
}
mirror_host() { # <ssh-name> <dest-base>
    timeout 30 ssh -o ConnectTimeout=15 -o BatchMode=yes "$1" "mkdir -p $2/selflearning $2/eval_cache" 2>>"$LOG" \
    || { say "MIRROR $1 FAILED mkdir (retries soon)"; return 1; }
    EC="$TOSH/binance_archive/eval_cache" SL="$TOSH/binance_archive/selflearning"
    cp1 "$EC/eval_cache_v1_master.db" "$1:$2/eval_cache/" \
    && cp1 "$EC/eval_cache_rebuilt_v1.db" "$1:$2/eval_cache/" \
    && cp1 "$EC/bench_merge.db" "$1:$2/eval_cache/" \
    && timeout 900 "$RSYNC_BIN" -az --partial --timeout=300 --exclude='eval_cache_v1_master.db' --exclude='eval_cache_rebuilt_v1.db' --exclude='bench_merge.db' \
        -e "$RSYNC_SSH" "$EC/" "$1:$2/eval_cache/" 2>>"$LOG" \
    && timeout 600 "$RSYNC_BIN" -az --partial --timeout=300 \
        -e "$RSYNC_SSH" "$SL/" "$1:$2/selflearning/" 2>>"$LOG" \
    && { verify_host "$1" "$2"; say "MIRROR $1 ok"; } || say "MIRROR $1 FAILED/partial (resume next window)"
}
[ -f "$MASTER" ] && say "master $(du -sm "$MASTER" 2>/dev/null | cut -f1)M present"
mirror_host s5 "/home/niels/mirror_toshiba"
timeout 10 ssh -o ConnectTimeout=6 -o BatchMode=yes s6 hostname >/dev/null 2>&1 \
    && mirror_host s6 "/home/niels/mirror_toshiba" || say "MIRROR s6 unreachable (retries in 6h)"
if [ -d /Volumes/E3T ]; then
    EXCLUDE_MASTER=1 "$RSYNC_BIN" -az --timeout=300 --exclude='eval_cache/' \
        "$TOSH/binance_archive/selflearning/" /Volumes/E3T/mirror_toshiba/ 2>>"$LOG" \
        && say "MIRROR E3T ok (brain only; 45G master does not fit 13G free)" \
        || say "MIRROR E3T FAILED (retries in 6h)"
fi
say "done"

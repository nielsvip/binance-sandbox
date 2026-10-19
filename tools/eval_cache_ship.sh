#!/bin/bash
# eval_cache_ship — pull S2 bench eval_cache.db files to Mac staging (USER 2026-10-19:
# eval rows must offload to the ext-drive master for self-learning).
# S1 fleet pilots bypass the cache by construction (sanitized/ledger path), so the REAL
# producers are S2's version-gate bench caches (~/v15_bench/runs/*/*/eval_cache.db).
# Read-only on S2; Mac staging only. Merge happens in eval_cache_merge_master.sh.
set -u
BASE=/Users/niels/Documents/binance
DEST="$BASE/data/eval_cache_incoming"
LOG=/tmp/eval_cache_ship.log
mkdir -p "$DEST"
RSYNC_BIN=/opt/homebrew/bin/rsync
[ -x "$RSYNC_BIN" ] || RSYNC_BIN=rsync
t0=$(date +%s)
# include-pattern form: no --ignore-missing-args needed (2026-10-19 openrsync lesson).
"$RSYNC_BIN" -az --timeout=120 \
    --include='*/' --include='eval_cache.db' --exclude='*' \
    -e "ssh -o ConnectTimeout=15 -o BatchMode=yes" \
    s2:v15_bench/runs/ "$DEST/" 2>>"$LOG"
rc=$?
n=$(find "$DEST" -name eval_cache.db 2>/dev/null | wc -l | tr -d ' ')
echo "$(date -u +%FT%TZ) SHIP rc=$rc files=$n secs=$(( $(date +%s) - t0 ))" >>"$LOG"
exit $rc

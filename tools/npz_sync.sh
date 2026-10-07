#!/bin/bash
# npz_sync.sh — Mac is SOURCE OF TRUTH for NPZ / indicator caches, push to S1 24/7
# Ensures every server gets latest versions of npz files and scripts and TEMPLATE*.xlsx 24/7
# Mac -> S1 every 5 min, S1 is source of truth for backtest, workers pull from S1
# Complements s1_s2_autosync.sh (scripts) and template_push.sh (TEMPLATE*.xlsx)
# Never calculates a number twice: pushes are checksum-based, idempotent
set -e
BASE="/Users/niels/Documents/binance"
LOG="/tmp/npz_sync.log"
S1="s1"
# Options: ControlMaster via s1 alias (127.0.0.1:2201 tunnel), fallback to s1-pub if tunnel down
SSH_OPTS="-o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no"
cd "$BASE" || { echo "$(date -u +%FT%TZ) CANNOT_CD $BASE" >>"$LOG"; exit 1; }

# Sources that must be on S1 for backtest: matrix_npz dirs + any *.npz + indicator cache
# Use rsync --checksum so latest version wins regardless of mtime, and -a to preserve
# Mac is authority: Mac version overwrites S1 if md5 differs (no --existing, we WANT creation)
sync_dir() {
    local src="$1" dst="$2" label="$3"
    if [ ! -e "$src" ]; then
        # Don't spam log if source doesn't exist
        return 0
    fi
    # Ensure destination parent exists on S1
    ssh $SSH_OPTS "$S1" "mkdir -p $dst 2>/dev/null || true" >>"$LOG" 2>&1 || true
    rsync -az --checksum --timeout=30 -e "ssh $SSH_OPTS" "$src" "$S1:$dst" >>"$LOG" 2>&1 || {
        echo "$(date -u +%FT%TZ) $label RSYNC_FAIL" >>"$LOG"
        # fallback try s1-pub direct if s1 (tunnel) failed
        rsync -az --checksum --timeout=30 -e "ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no" "$src" "s1-pub:$dst" >>"$LOG" 2>&1 || true
        return 1
    }
    echo "$(date -u +%FT%TZ) $label SYNC_OK $(du -sh "$src" 2>/dev/null | awk '{print $1}')" >>"$LOG"
}

# 1. matrix_npz: entire directory tree (contains per-campaign npz shards)
if [ -d "$BASE/data/matrix_npz" ]; then
    sync_dir "$BASE/data/matrix_npz/" "/home/niels/binance-sandbox/data/matrix_npz/" "MATRIX_NPZ"
fi

# 2. indicator_cache / klines cache if present
for d in indicator_cache klines_cache klines_cache_tradier; do
    if [ -d "$BASE/data/$d" ]; then
        sync_dir "$BASE/data/$d/" "/home/niels/binance-sandbox/data/$d/" "$d"
    fi
done

# 3. Any standalone *.npz in data/ (legacy)
if ls "$BASE"/data/*.npz >/dev/null 2>&1; then
    rsync -az --checksum --timeout=30 -e "ssh $SSH_OPTS" "$BASE"/data/*.npz "$S1:/home/niels/binance-sandbox/data/" >>"$LOG" 2>&1 || true
    echo "$(date -u +%FT%TZ) DATA_NPZ SYNC_OK" >>"$LOG"
fi

# 4. Also ensure SPREADSHEETS/V15_V16_CELL_BY_CELL progress integrity: don't overwrite, just ensure S1 has latest
# This is handled by S1's own s1_pull_from_s2s3s5.sh every 2min (S1 pulls from workers)
# Here we ensure Mac's copies are pushed to S1 as well (Mac may have newer after local edits)
if [ -d "$BASE/SPREADSHEETS/V15_V16_CELL_BY_CELL" ]; then
    # Only push newer files, don't delete S1's newer results: use -u (update)
    # FINAL-GUARD 2026-10-05 (USER: finals pushed once, never again): live sheets
    # update normally, bh/gain finals + chart + manifest with --ignore-existing.
    # Patterns owned by tools/v15_final_sync_guard.py (FINAL_GLOBS).
    rsync -auz --timeout=30 --exclude='*_bh*_gain*_30d_matrix.xlsx' --exclude='*_bh*_gain*_30d_matrix.html' --exclude='*_bh*_gain*_manifest.json' -e "ssh $SSH_OPTS" "$BASE/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "$S1:/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" >>"$LOG" 2>&1 || true
    rsync -auz --timeout=30 --ignore-existing --include='*_bh*_gain*_30d_matrix.xlsx' --include='*_bh*_gain*_30d_matrix.html' --include='*_bh*_gain*_manifest.json' --exclude='*' -e "ssh $SSH_OPTS" "$BASE/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "$S1:/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" >>"$LOG" 2>&1 || true
fi

# 5. data/reports/lifecycle_pilot progress.json (resume anchors)
if [ -d "$BASE/data/reports/lifecycle_pilot" ]; then
    rsync -auz --timeout=30 -e "ssh $SSH_OPTS" "$BASE/data/reports/lifecycle_pilot/" "$S1:/home/niels/binance-sandbox/data/reports/lifecycle_pilot/" >>"$LOG" 2>&1 || true
fi

tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
echo "$(date -u +%FT%TZ) npz_sync tick done" >>"$LOG"

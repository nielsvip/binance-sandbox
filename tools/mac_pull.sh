#!/bin/bash
set -euo pipefail
LOG="/tmp/mac_pull.log"
exec >> "$LOG" 2>&1
echo "=== mac_pull $(date -u) ==="
# NEW-ONLY 2026-10-06 (USER: ONLY new files may sync S1->Mac, never ancient):
# -u on every pull + remote find -mmin recency gate (V15_SYNC_MAX_AGE_MIN,
# default 4320 = 72h) feeding --files-from. ssh failure yields an empty list
# (fail-closed: that pull transfers nothing). Finals patterns owned by
# tools/v15_final_sync_guard.py (FINAL_GLOBS).
MAX_AGE_MIN="${V15_SYNC_MAX_AGE_MIN:-4320}"
RSYNC_SSH="ssh -o ConnectTimeout=10 -o ControlMaster=no -o StrictHostKeyChecking=no"
DST_CELL="/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/"
DST_PILOT="/Users/niels/Documents/binance/data/reports/lifecycle_pilot/"
recent_list() {
  local host="$1" rdir="$2" list="$3"
  : > "$list"
  ssh -o ConnectTimeout=10 -o ControlMaster=no -o StrictHostKeyChecking=no "$host" "find $rdir -maxdepth 1 -type f -mmin -$MAX_AGE_MIN -printf '%f\n'" >> "$list" 2>/dev/null || true
  return 0
}
LIST_CELL="$(mktemp /tmp/mac_pull_cell.XXXXXX)"
trap 'rm -f "$LIST_CELL"' EXIT
for s1host in s1 s1-pub; do
  if ssh -o ConnectTimeout=5 -o ControlMaster=no -o StrictHostKeyChecking=no $s1host "test -d ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL" 2>/dev/null; then
    echo "pull from $s1host (live update + finals-once, new-only; patterns owned by tools/v15_final_sync_guard.py)"
    recent_list "$s1host" "~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL" "$LIST_CELL"
    rsync -auz --timeout=120 --ignore-missing-args --files-from="$LIST_CELL" --exclude='*_bh*_gain*_30d_matrix.xlsx' --exclude='*_bh*_gain*_30d_matrix.html' --exclude='*_bh*_gain*_manifest.json' --include="*.xlsx" --exclude="*" -e "$RSYNC_SSH" "$s1host:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "$DST_CELL" 2>&1 | tail -5 || true
    rsync -auz --timeout=120 --ignore-missing-args --files-from="$LIST_CELL" --ignore-existing --include='*_bh*_gain*_30d_matrix.xlsx' --include='*_bh*_gain*_30d_matrix.html' --include='*_bh*_gain*_manifest.json' --exclude="*" -e "$RSYNC_SSH" "$s1host:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "$DST_CELL" 2>&1 | tail -5 || true
    rsync -auz --timeout=120 --ignore-missing-args --include="*_v14_progress.json" --exclude="*" -e "$RSYNC_SSH" "$s1host:binance-sandbox/data/reports/lifecycle_pilot/" "$DST_PILOT" 2>&1 | tail -5 || true
    echo "mac_pull from $s1host done: $(find /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL -name "*.xlsx" 2>/dev/null | wc -l | tr -d ' ') xlsx on Mac"
    break
  fi
done
for host in s2 s3 s5; do
  recent_list "$host" "~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL" "$LIST_CELL"
  rsync -auz --timeout=120 --ignore-missing-args --files-from="$LIST_CELL" --exclude='*_bh*_gain*_30d_matrix.xlsx' --exclude='*_bh*_gain*_30d_matrix.html' --exclude='*_bh*_gain*_manifest.json' --include="*.xlsx" --exclude="*" -e "$RSYNC_SSH" "$host:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "$DST_CELL" 2>&1 | tail -3 || true
  rsync -auz --timeout=120 --ignore-missing-args --files-from="$LIST_CELL" --ignore-existing --include='*_bh*_gain*_30d_matrix.xlsx' --include='*_bh*_gain*_30d_matrix.html' --include='*_bh*_gain*_manifest.json' --exclude="*" -e "$RSYNC_SSH" "$host:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "$DST_CELL" 2>&1 | tail -3 || true
done
final=$(find /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL -name "*.xlsx" 2>/dev/null | wc -l | tr -d ' ')
echo "mac_pull FINAL $final xlsx"
python3 /Users/niels/Documents/binance/tools/v15_final_sync_guard.py --audit /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL 2>&1 | head -n 1 || true

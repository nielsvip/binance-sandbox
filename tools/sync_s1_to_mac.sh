#!/bin/bash
set -e
# Keep MacBook in sync with S1 xls and charts for each sym_side, run sequentially
# 2026-09-05: now also syncs SPREADSHEETS/*COMPLETE_chart.html and *FINAL.html so Mac SPREADSHEETS matches S1's hires zoomable charts (MU_LONG_COMPLETE_chart.html) after every sym_side run
# NEW-ONLY 2026-10-06 (USER: ancient S1 files kept re-syncing to the MacBook and
# presenting as new arrivals — ONLY new files may sync S1->Mac, never again):
#   1. every pull carries -u/--update: a Mac file newer than S1's copy is NEVER
#      overwritten by the older S1 copy;
#   2. every bulk pull carries a recency gate: only S1 files modified within
#      V15_SYNC_MAX_AGE_MIN (default 4320 = 72h) transfer at all — an ancient
#      file missing on the Mac (quarantined/deleted) stays dead, never
#      resurrected. Remote find -mmin builds --files-from lists; ssh failure
#      yields an empty list (fail-closed: that pull transfers nothing).
# Deliberate one-shot catch-up of older files: run rsync without --files-from
# by hand — never from a loop, cron, launchd, or agent.
S1="niels@157.180.125.52"
RSYNC_SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"
SANDBOX_SPREAD="~/binance-sandbox/SPREADSHEETS"
SANDBOX_CELL="~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL"
SANDBOX_1M="~/binance-sandbox/data/reports/charts_1M"
RSYNC_SPREAD="binance-sandbox/SPREADSHEETS"
RSYNC_CELL="binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL"
RSYNC_1M="binance-sandbox/data/reports/charts_1M"
DST="/Users/niels/Documents/binance/SPREADSHEETS/"
DST_V15="/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/"
# FINAL-GUARD 2026-10-05 (USER: finals pulled once, never again): bh/gain finals are
# immutable — same name + new mtime recycling hid progress. Pass 1 updates live
# in-progress sheets only; pass 2 pulls finals + same-stem chart + manifest with
# --ignore-existing. Patterns owned by tools/v15_final_sync_guard.py (FINAL_GLOBS).
DST_CHARTS="/Users/niels/Documents/binance/data/reports/charts_1M/"
MAX_AGE_MIN="${V15_SYNC_MAX_AGE_MIN:-4320}"
mkdir -p "$DST" "$DST_V15" "$DST_CHARTS"
# recent_list <remote-dir> <list-file>: basenames in remote-dir modified within
# MAX_AGE_MIN, one per line. Always exits 0 with the file present; empty on
# ssh/find failure (fail-closed: the pull then transfers nothing).
recent_list() {
  local rdir="$1" list="$2"
  : > "$list"
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$S1" "find $rdir -maxdepth 1 -type f -mmin -$MAX_AGE_MIN -printf '%f\n'" >> "$list" 2>/dev/null || true
  return 0
}
LIST_SPREAD="$(mktemp /tmp/v15sync_spread.XXXXXX)"
LIST_CELL="$(mktemp /tmp/v15sync_cell.XXXXXX)"
LIST_1M="$(mktemp /tmp/v15sync_1m.XXXXXX)"
trap 'rm -f "$LIST_SPREAD" "$LIST_CELL" "$LIST_1M"' EXIT
recent_list "$SANDBOX_SPREAD" "$LIST_SPREAD"
recent_list "$SANDBOX_CELL" "$LIST_CELL"
recent_list "$SANDBOX_1M" "$LIST_1M"
# progress board first (tiny, written every minute on S1 by tools/v15_progress_board.py) so Mac monitoring never waits on xlsx
rsync -auz --timeout=30 --files-from="$LIST_SPREAD" --include='V15_PROGRESS.md' --include='V15_PROGRESS.csv' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 3
echo "[$(date)] Sync S1 -> Mac xls (new-only: max age ${MAX_AGE_MIN}min)... (TEMPLATE* excluded - Mac is source of truth, never S1->Mac)"
rsync -auvz --progress --files-from="$LIST_SPREAD" --exclude='*TEMPLATE*' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --exclude='V15_AVG*' --include='*.xlsx' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac V15_V16_CELL_BY_CELL xls (live update + finals-once, new-only)..."
rsync -auvz --progress --files-from="$LIST_CELL" --exclude='*_bh*_gain*_30d_matrix.xlsx' --exclude='*_bh*_gain*_30d_matrix.html' --exclude='*_bh*_gain*_manifest.json' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --exclude='V15_AVG*' --include='*.xlsx' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_CELL/" "$DST_V15" 2>&1 | tail -n 20
rsync -auvz --progress --files-from="$LIST_CELL" --ignore-existing --include='*_bh*_gain*_30d_matrix.xlsx' --include='*_bh*_gain*_30d_matrix.html' --include='*_bh*_gain*_manifest.json' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_CELL/" "$DST_V15" 2>&1 | tail -n 20
python3 /Users/niels/Documents/binance/tools/v15_final_sync_guard.py --audit "$DST_V15" 2>&1 | head -n 1 || true
echo "[$(date)] Sync S1 -> Mac SPREADSHEET COMPLETE/FINAL charts (new-only)..."
rsync -auvz --progress --files-from="$LIST_SPREAD" --exclude='*TEMPLATE*' --include='*COMPLETE_chart.html' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 20
rsync -auvz --progress --files-from="$LIST_SPREAD" --exclude='*TEMPLATE*' --include='*FINAL.html' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac per-tab charts (new-only)..."
rsync -auvz --progress --files-from="$LIST_SPREAD" --exclude='*TEMPLATE*' --include='*_chart.html' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac charts_1M (new-only)..."
rsync -auvz --progress --files-from="$LIST_1M" --include='*.html' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_1M/" "$DST_CHARTS" 2>&1 | tail -n 20
echo "[$(date)] Mac sync done: $(ls -lh "$DST"*.xlsx 2>&1 | wc -l) xlsx, $(ls -lh "$DST"*.html 2>&1 | wc -l) html in SPREADSHEETS, $(ls -lh "$DST_CHARTS"*.html 2>&1 | wc -l) charts in charts_1M"

# BADZIP FIX 2026-09-29: xlsx sources are atomic-writers only (see tools/xlsx_atomic.py).
# Belt-and-braces: quarantine any invalid xlsx that still arrives, never leave it in place.
python3 - <<'PYEOF'
import zipfile, os, pathlib
dst = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL")
q = pathlib.Path("/Users/niels/Documents/binance/backups/corrupt_xlsx_quarantine")
n = 0
for p in dst.glob("*.xlsx"):
    try:
        with zipfile.ZipFile(p) as z:
            if len(z.namelist()) >= 10 and z.testzip() is None:
                continue
    except Exception:
        pass
    q.mkdir(parents=True, exist_ok=True)
    os.replace(p, q / p.name)
    n += 1
if n:
    print(f"[badzip-quarantine] moved {n} invalid xlsx arrivals to {q}")
PYEOF

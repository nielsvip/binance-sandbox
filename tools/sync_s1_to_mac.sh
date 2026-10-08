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
S2="s2"
S5="s5"
RSYNC_SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"
RSYNC_SSH_J="ssh -o BatchMode=yes -o ConnectTimeout=15"
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
SANDBOX_S6="~/binance-sandbox/data/s6_365"
RSYNC_S6="binance-sandbox/data/s6_365"
DST_S6="/Users/niels/Documents/binance/SPREADSHEETS/S6_365/"
SANDBOX_S6CHARTS="~/binance-sandbox/data/s6_365/charts"
RSYNC_S6CHARTS="binance-sandbox/data/s6_365/charts"
DST_S6CHARTS="/Users/niels/Documents/binance/SPREADSHEETS/S6_365/charts/"
SANDBOX_ADOPT="~/binance-sandbox/data/generic_adopt"
RSYNC_ADOPT="binance-sandbox/data/generic_adopt"
DST_ADOPT="/Users/niels/Documents/binance/SPREADSHEETS/GENERIC_ADOPT/"
SANDBOX_CHAIN="~/chain"
RSYNC_CHAIN="chain"
DST_CHAIN="/Users/niels/Documents/binance/SPREADSHEETS/FLEET_CHAIN/"
SANDBOX_ENCY="~/binance-sandbox/data/encyclopedia_v2"
RSYNC_ENCY="binance-sandbox/data/encyclopedia_v2"
DST_ENCY="/Users/niels/Documents/binance/data/encyclopedia_v2"
MAX_AGE_MIN="${V15_SYNC_MAX_AGE_MIN:-4320}"
mkdir -p "$DST" "$DST_V15" "$DST_CHARTS" "$DST_S6" "$DST_S6CHARTS" "$DST_ADOPT" "$DST_CHAIN" "$DST_ENCY"
# recent_list <host> <ssh-opts> <remote-dir> <list-file>: basenames in remote-dir
# modified within MAX_AGE_MIN, one per line. Always exits 0 with the file present;
# empty on ssh/find failure (fail-closed: the pull then transfers nothing).
recent_list() {
  local host="$1" sopts="$2" rdir="$3" list="$4"
  : > "$list"
  ssh $sopts "$host" "find $rdir -maxdepth 1 -type f -mmin -$MAX_AGE_MIN -printf '%f\n'" >> "$list" 2>/dev/null || true
  return 0
}
recent_list_tree() {
  local host="$1" sopts="$2" rdir="$3" list="$4"
  : > "$list"
  ssh $sopts "$host" "find $rdir -mindepth 2 -maxdepth 2 -type f -mmin -$MAX_AGE_MIN -printf '%P\n'" >> "$list" 2>/dev/null || true
  return 0
}
LIST_SPREAD="$(mktemp /tmp/v15sync_spread.XXXXXX)"
LIST_CELL="$(mktemp /tmp/v15sync_cell.XXXXXX)"
LIST_CELL_S2="$(mktemp /tmp/v15sync_cell_s2.XXXXXX)"
LIST_CELL_S5="$(mktemp /tmp/v15sync_cell_s5.XXXXXX)"
LIST_1M="$(mktemp /tmp/v15sync_1m.XXXXXX)"
LIST_S6="$(mktemp /tmp/v15sync_s6.XXXXXX)"
LIST_S6CHARTS="$(mktemp /tmp/v15sync_s6charts.XXXXXX)"
LIST_ADOPT="$(mktemp /tmp/v15sync_adopt.XXXXXX)"
LIST_CHAIN_S1="$(mktemp /tmp/v15sync_chain_s1.XXXXXX)"
LIST_CHAIN_S2="$(mktemp /tmp/v15sync_chain_s2.XXXXXX)"
LIST_CHAIN_S5="$(mktemp /tmp/v15sync_chain_s5.XXXXXX)"
LIST_ENCY="$(mktemp /tmp/v15sync_ency.XXXXXX)"
trap 'rm -f "$LIST_SPREAD" "$LIST_CELL" "$LIST_CELL_S2" "$LIST_CELL_S5" "$LIST_1M" "$LIST_S6" "$LIST_S6CHARTS" "$LIST_ADOPT" "$LIST_CHAIN_S1" "$LIST_CHAIN_S2" "$LIST_CHAIN_S5" "$LIST_ENCY"' EXIT
recent_list "$S1" "-o BatchMode=yes -o ConnectTimeout=10" "$SANDBOX_SPREAD" "$LIST_SPREAD"
recent_list "$S1" "-o BatchMode=yes -o ConnectTimeout=10" "$SANDBOX_CELL" "$LIST_CELL"
recent_list "$S2" "-o BatchMode=yes -o ConnectTimeout=15" "$SANDBOX_CELL" "$LIST_CELL_S2"
recent_list "$S5" "-o BatchMode=yes -o ConnectTimeout=15" "$SANDBOX_CELL" "$LIST_CELL_S5"
recent_list "$S1" "-o BatchMode=yes -o ConnectTimeout=10" "$SANDBOX_1M" "$LIST_1M"
recent_list "$S1" "-o BatchMode=yes -o ConnectTimeout=10" "$SANDBOX_S6" "$LIST_S6"
recent_list "$S1" "-o BatchMode=yes -o ConnectTimeout=10" "$SANDBOX_S6CHARTS" "$LIST_S6CHARTS"
recent_list "$S1" "-o BatchMode=yes -o ConnectTimeout=10" "$SANDBOX_ADOPT" "$LIST_ADOPT"
recent_list_tree "$S1" "-o BatchMode=yes -o ConnectTimeout=10" "$SANDBOX_CHAIN" "$LIST_CHAIN_S1"
recent_list_tree "$S2" "-o BatchMode=yes -o ConnectTimeout=15" "$SANDBOX_CHAIN" "$LIST_CHAIN_S2"
recent_list_tree "$S5" "-o BatchMode=yes -o ConnectTimeout=15" "$SANDBOX_CHAIN" "$LIST_CHAIN_S5"
recent_list "$S1" "-o BatchMode=yes -o ConnectTimeout=10" "$SANDBOX_ENCY" "$LIST_ENCY"
# progress board first (tiny, written every minute on S1 by tools/v15_progress_board.py) so Mac monitoring never waits on xlsx
rsync -auz --timeout=30 --files-from="$LIST_SPREAD" --include='V15_PROGRESS.md' --include='V15_PROGRESS.csv' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 3
echo "[$(date)] Sync S1 -> Mac xls (new-only: max age ${MAX_AGE_MIN}min)... (TEMPLATE* excluded here - templates pull ONLY via tools/v15_daily_chain_mac_apply.sh after the S1 done-stamp; S1 chain is the template writer since 2026-10-06)"
rsync -auvz --progress --files-from="$LIST_SPREAD" --exclude='*TEMPLATE*' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --exclude='V15_AVG*' --include='*.xlsx' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac V15_V16_CELL_BY_CELL xls (live update + finals-once, new-only)..."
rsync -auvz --progress --files-from="$LIST_CELL" --exclude='*_bh*_gain*_30d_matrix.xlsx' --exclude='*_bh*_gain*_30d_matrix.html' --exclude='*_bh*_gain*_manifest.json' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --exclude='V15_AVG*' --include='*.xlsx' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_CELL/" "$DST_V15" 2>&1 | tail -n 20
rsync -auvz --progress --files-from="$LIST_CELL" --ignore-existing --include='*_bh*_gain*_30d_matrix.xlsx' --include='*_bh*_gain*_30d_matrix.html' --include='*_bh*_gain*_manifest.json' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_CELL/" "$DST_V15" 2>&1 | tail -n 20
echo "[$(date)] Sync S2 -> Mac V15_V16_CELL_BY_CELL xls (live update + finals-once, new-only)..."
rsync -auvz --progress --files-from="$LIST_CELL_S2" --exclude='*_bh*_gain*_30d_matrix.xlsx' --exclude='*_bh*_gain*_30d_matrix.html' --exclude='*_bh*_gain*_manifest.json' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --exclude='V15_AVG*' --include='*.xlsx' --exclude='*' -e "$RSYNC_SSH_J" "$S2:$RSYNC_CELL/" "$DST_V15" 2>&1 | tail -n 10
rsync -auvz --progress --files-from="$LIST_CELL_S2" --ignore-existing --include='*_bh*_gain*_30d_matrix.xlsx' --include='*_bh*_gain*_30d_matrix.html' --include='*_bh*_gain*_manifest.json' --exclude='*' -e "$RSYNC_SSH_J" "$S2:$RSYNC_CELL/" "$DST_V15" 2>&1 | tail -n 10
echo "[$(date)] Sync S5 -> Mac V15_V16_CELL_BY_CELL xls (live update + finals-once, new-only)..."
rsync -auvz --progress --files-from="$LIST_CELL_S5" --exclude='*_bh*_gain*_30d_matrix.xlsx' --exclude='*_bh*_gain*_30d_matrix.html' --exclude='*_bh*_gain*_manifest.json' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --exclude='V15_AVG*' --include='*.xlsx' --exclude='*' -e "$RSYNC_SSH_J" "$S5:$RSYNC_CELL/" "$DST_V15" 2>&1 | tail -n 10
rsync -auvz --progress --files-from="$LIST_CELL_S5" --ignore-existing --include='*_bh*_gain*_30d_matrix.xlsx' --include='*_bh*_gain*_30d_matrix.html' --include='*_bh*_gain*_manifest.json' --exclude='*' -e "$RSYNC_SSH_J" "$S5:$RSYNC_CELL/" "$DST_V15" 2>&1 | tail -n 10
python3 /Users/niels/Documents/binance/tools/v15_final_sync_guard.py --audit "$DST_V15" 2>&1 | head -n 1 || true
echo "[$(date)] Sync S1 -> Mac SPREADSHEET COMPLETE/FINAL charts (new-only)..."
rsync -auvz --progress --files-from="$LIST_SPREAD" --exclude='*TEMPLATE*' --include='*COMPLETE_chart.html' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 20
rsync -auvz --progress --files-from="$LIST_SPREAD" --exclude='*TEMPLATE*' --include='*FINAL.html' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac per-tab charts (new-only)..."
rsync -auvz --progress --files-from="$LIST_SPREAD" --exclude='*TEMPLATE*' --include='*_chart.html' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_SPREAD/" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac charts_1M (new-only)..."
rsync -auvz --progress --files-from="$LIST_1M" --include='*.html' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_1M/" "$DST_CHARTS" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac S6_365 365D+GS verdicts (new-only)..."
rsync -auvz --progress --files-from="$LIST_S6" --include='*.json' --include='*.md' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_S6/" "$DST_S6" 2>&1 | tail -n 10
echo "[$(date)] Sync S1 -> Mac S6_365 zoomable charts (new-only)..."
rsync -auvz --progress --files-from="$LIST_S6CHARTS" --include='*.html' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_S6CHARTS/" "$DST_S6CHARTS" 2>&1 | tail -n 10
echo "[$(date)] Sync S1 -> Mac generic_adopt proposals (new-only)..."
rsync -auvz --progress --files-from="$LIST_ADOPT" --include='*.json' --include='*.md' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_ADOPT/" "$DST_ADOPT" 2>&1 | tail -n 10
echo "[$(date)] Sync S1/s2/s5 -> Mac fleet chain verdicts v365/repair/gs (new-only)..."
mkdir -p "$DST_CHAIN/s1" "$DST_CHAIN/s2" "$DST_CHAIN/s5"
rsync -auvz --progress --files-from="$LIST_CHAIN_S1" --include='*/' --include='*.json' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_CHAIN/" "$DST_CHAIN/s1/" 2>&1 | tail -n 5
rsync -auvz --progress --files-from="$LIST_CHAIN_S2" --include='*/' --include='*.json' --exclude='*' -e "$RSYNC_SSH_J" "$S2:$RSYNC_CHAIN/" "$DST_CHAIN/s2/" 2>&1 | tail -n 5
rsync -auvz --progress --files-from="$LIST_CHAIN_S5" --include='*/' --include='*.json' --exclude='*' -e "$RSYNC_SSH_J" "$S5:$RSYNC_CHAIN/" "$DST_CHAIN/s5/" 2>&1 | tail -n 5
echo "[$(date)] Sync S1 -> Mac encyclopedia graph (new-only)..."
rsync -auvz --progress --files-from="$LIST_ENCY" --include='graph.json' --exclude='*' -e "$RSYNC_SSH" "$S1:$RSYNC_ENCY/" "$DST_ENCY/" 2>&1 | tail -n 3
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
    try:
        os.replace(p, q / p.name)
    except FileNotFoundError:
        continue
    n += 1
if n:
    print(f"[badzip-quarantine] moved {n} invalid xlsx arrivals to {q}")
PYEOF

#!/bin/bash
set -e
# Keep MacBook in sync with S1 xls and charts for each sym_side, run sequentially
# 2026-09-05: now also syncs SPREADSHEETS/*COMPLETE_chart.html and *FINAL.html so Mac SPREADSHEETS matches S1's hires zoomable charts (MU_LONG_COMPLETE_chart.html) after every sym_side run
SRC="niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/*.xlsx"
DST="/Users/niels/Documents/binance/SPREADSHEETS/"
SRC_V15DIR="niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/"
DST_V15="/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/"
# FINAL-GUARD 2026-10-05 (USER: finals pulled once, never again): bh/gain finals are
# immutable — same name + new mtime recycling hid progress. Pass 1 updates live
# in-progress sheets only; pass 2 pulls finals + same-stem chart + manifest with
# --ignore-existing. Patterns owned by tools/v15_final_sync_guard.py (FINAL_GLOBS).
SRC_CHARTS="niels@157.180.125.52:~/binance-sandbox/data/reports/charts_1M/*.html"
DST_CHARTS="/Users/niels/Documents/binance/data/reports/charts_1M/"
SRC_SPREADSHEET_CHARTS="niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/*COMPLETE_chart.html"
SRC_SPREADSHEET_FINALS="niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/*FINAL.html"
SRC_SPREADSHEET_TABS="niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/*_chart.html"
mkdir -p "$DST" "$DST_V15" "$DST_CHARTS"
# progress board first (tiny, written every minute on S1 by tools/v15_progress_board.py) so Mac monitoring never waits on xlsx
rsync -az --timeout=30 -e "ssh -o BatchMode=yes -o ConnectTimeout=10" "niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/V15_PROGRESS.md" "niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/V15_PROGRESS.csv" "$DST" 2>&1 | tail -n 3
echo "[$(date)] Sync S1 -> Mac xls... (TEMPLATE* excluded - Mac is source of truth, never S1->Mac)"
rsync -avz --progress --exclude='*TEMPLATE*' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --exclude='V15_AVG*' -e "ssh -o BatchMode=yes" "$SRC" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac V15_V16_CELL_BY_CELL xls (live update + finals-once)..."
rsync -avz --progress --exclude='*_bh*_gain*_30d_matrix.xlsx' --exclude='*_bh*_gain*_30d_matrix.html' --exclude='*_bh*_gain*_manifest.json' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --exclude='V15_AVG*' --include='*.xlsx' --exclude='*' -e "ssh -o BatchMode=yes" "$SRC_V15DIR" "$DST_V15" 2>&1 | tail -n 20
rsync -avz --progress --ignore-existing --include='*_bh*_gain*_30d_matrix.xlsx' --include='*_bh*_gain*_30d_matrix.html' --include='*_bh*_gain*_manifest.json' --exclude='*' -e "ssh -o BatchMode=yes" "$SRC_V15DIR" "$DST_V15" 2>&1 | tail -n 20
python3 /Users/niels/Documents/binance/tools/v15_final_sync_guard.py --audit "$DST_V15" 2>&1 | tail -n 3 || true
echo "[$(date)] Sync S1 -> Mac SPREADSHEET COMPLETE/FINAL charts..."
rsync -avz --progress -e "ssh -o BatchMode=yes" "$SRC_SPREADSHEET_CHARTS" "$DST" 2>&1 | tail -n 20
rsync -avz --progress -e "ssh -o BatchMode=yes" "$SRC_SPREADSHEET_FINALS" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac per-tab charts..."
rsync -avz --progress -e "ssh -o BatchMode=yes" "$SRC_SPREADSHEET_TABS" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac charts_1M..."
rsync -avz --progress -e "ssh -o BatchMode=yes" "$SRC_CHARTS" "$DST_CHARTS" 2>&1 | tail -n 20
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

#!/bin/bash
set -e
# Keep MacBook in sync with S1 xls and charts for each sym_side, run sequentially
# 2026-09-05: now also syncs SPREADSHEETS/*COMPLETE_chart.html and *FINAL.html so Mac SPREADSHEETS matches S1's hires zoomable charts (MU_LONG_COMPLETE_chart.html) after every sym_side run
SRC="niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/*.xlsx"
DST="/Users/niels/Documents/binance/SPREADSHEETS/"
SRC_CHARTS="niels@157.180.125.52:~/binance-sandbox/data/reports/charts_1M/*.html"
DST_CHARTS="/Users/niels/Documents/binance/data/reports/charts_1M/"
SRC_SPREADSHEET_CHARTS="niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/*COMPLETE_chart.html"
SRC_SPREADSHEET_FINALS="niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/*FINAL.html"
SRC_SPREADSHEET_TABS="niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/*_chart.html"
mkdir -p "$DST" "$DST_CHARTS"
echo "[$(date)] Sync S1 -> Mac xls..."
rsync -avz --progress --exclude='TEMPLATE.xlsx' -e "ssh -o BatchMode=yes" "$SRC" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac SPREADSHEET COMPLETE/FINAL charts..."
rsync -avz --progress -e "ssh -o BatchMode=yes" "$SRC_SPREADSHEET_CHARTS" "$DST" 2>&1 | tail -n 20
rsync -avz --progress -e "ssh -o BatchMode=yes" "$SRC_SPREADSHEET_FINALS" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac per-tab charts..."
rsync -avz --progress -e "ssh -o BatchMode=yes" "$SRC_SPREADSHEET_TABS" "$DST" 2>&1 | tail -n 20
echo "[$(date)] Sync S1 -> Mac charts_1M..."
rsync -avz --progress -e "ssh -o BatchMode=yes" "$SRC_CHARTS" "$DST_CHARTS" 2>&1 | tail -n 20
echo "[$(date)] Mac sync done: $(ls -lh "$DST"*.xlsx 2>&1 | wc -l) xlsx, $(ls -lh "$DST"*.html 2>&1 | wc -l) html in SPREADSHEETS, $(ls -lh "$DST_CHARTS"*.html 2>&1 | wc -l) charts in charts_1M"

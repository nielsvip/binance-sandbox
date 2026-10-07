#!/bin/bash
# S1 pulls CELL_BY_CELL sheets + progress from workers (s2/s5/s6). Mac-owned source
# of truth — deployed to S1:~/binance-sandbox/tools/. Cron entry on S1 (may be
# paused): */2 * * * * /home/niels/binance-sandbox/tools/s1_pull_from_s2s3s5.sh
# FINAL-GUARD 2026-10-05 (USER: finals pulled once, never again): live sheets
# update normally, bh/gain finals + same-stem chart + manifest with
# --ignore-existing. Patterns owned by tools/v15_final_sync_guard.py (FINAL_GLOBS).
set -euo pipefail
LOG="/tmp/s1_pull_from_s2s3s5.log"
exec >> "$LOG" 2>&1
echo "=== s1 pull $(date -u) ==="
for host in 10.0.0.4 10.0.0.5 10.0.0.6; do
  case $host in 10.0.0.4) label=s2;; 10.0.0.5) label=s3;; 10.0.0.6) label=s5;; esac
  echo "---$label $host---"
  rsync -auz --timeout=30 --ignore-missing-args --exclude='*_bh*_gain*_30d_matrix.xlsx' --exclude='*_bh*_gain*_30d_matrix.html' --exclude='*_bh*_gain*_manifest.json' --include="*.xlsx" --exclude="*" -e "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new" "niels@$host:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" 2>&1 | head -5 || true
  rsync -auz --timeout=30 --ignore-missing-args --ignore-existing --include='*_bh*_gain*_30d_matrix.xlsx' --include='*_bh*_gain*_30d_matrix.html' --include='*_bh*_gain*_manifest.json' --exclude="*" -e "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new" "niels@$host:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" "/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/" 2>&1 | head -5 || true
  rsync -auz --timeout=30 --ignore-missing-args --include="*_v14_progress.json" --exclude="*" -e "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new" "niels@$host:~/binance-sandbox/data/reports/lifecycle_pilot/" "/home/niels/binance-sandbox/data/reports/lifecycle_pilot/" 2>&1 | head -5 || true
done
echo "s1 pull done $(date -u) xlsx=$(find /home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL -name "*.xlsx" 2>/dev/null | wc -l | tr -d ' ')"
/home/niels/binance-sandbox/.venv/bin/python /home/niels/binance-sandbox/tools/v15_final_sync_guard.py --audit /home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL 2>&1 | head -n 1 || true

#!/bin/bash
# backup_to_ext.sh — CONSTANT BACKUPS to SSD2T + WD25/E3T so you NEVER lose data.
# Runs on Mac, mirrors live source Mac -> SSD2T/binance-sandbox (+WD25/E3T if present).
# No --delete on versioned snapshots; --delete only on rolling mirrors.
# Call via cron + on-demand. Never makes S1 immutable — S1 stays mutable for 1000s/day writes.
set -e
SRC=/Users/niels/Documents/binance
SSD=/Volumes/SSD2T
WD25=/Volumes/WD25
E3T=/Volumes/E3T
ts=$(date +%Y%m%d_%H%M%S)
log=/Users/niels/logs/backup_to_ext.log
mkdir -p "$(dirname "$log")"
echo "[$ts] backup_to_ext start" >> "$log"
# snapshot: keep last 20 versioned mirrors on SSD2T so we can rewind a month
snap="$SSD/binance_mirror_$ts"
mkdir -p "$snap" 2>/dev/null || { echo "[$ts] SSD2T not mounted, skip" >> "$log"; exit 0; }
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='.venv' --exclude='node_modules' \
  "$SRC/v12_quick_engine.py" "$SRC/SPREADSHEETS/" "$SRC/tools/" "$SRC/config.py" "$SRC/config_tradier.py" \
  "$snap/" >> "$log" 2>&1 || true
# rolling mirror (always current, --delete keeps it exact)
rsync -a --delete --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='.venv' --exclude='node_modules' \
  "$SRC/" "$SSD/binance-sandbox/" >> "$log" 2>&1 || true
if [ -d "$WD25" ]; then rsync -a --delete --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='.venv' "$SRC/" "$WD25/binance/" >> "$log" 2>&1 || true; fi
if [ -d "$E3T" ]; then mkdir -p "$E3T/binance" 2>/dev/null; rsync -a --delete --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='.venv' "$SRC/" "$E3T/binance/" >> "$log" 2>&1 || true; fi
# prune old snapshots: keep 20 newest, del oldest
ls -1d "$SSD"/binance_mirror_* 2>/dev/null | sort | head -n -20 | xargs rm -rf 2>/dev/null || true
echo "[$ts] backup_to_ext done snap=$snap" >> "$log"
# verify critical files exist on mirrors
ls -lh "$SSD/binance-sandbox/v12_quick_engine.py" "$SSD/binance-sandbox/SPREADSHEETS/TEMPLATE.xlsx" >> "$log" 2>&1 || true

#!/bin/bash
# sync_history_to_ext.sh — NEVER LOSE DATA AGAIN — runs every 15m via launchd, pushes to SSD2T+TOSHIBA_EXT
set -e
for vol in /Volumes/SSD2T /Volumes/TOSHIBA_EXT; do
  [ -d "$vol" ] || continue
  ts=$(date +%Y%m%d_%H%M%S)
  mkdir -p "$vol/binance/history" "$vol/binance/backups"
  rsync -a --checksum /Users/niels/Documents/binance/SPREADSHEETS/TEMPLATE.xlsx "$vol/binance/history/TEMPLATE_${ts}.xlsx"
  rsync -a /Users/niels/Documents/binance/config.py "$vol/binance/history/config_${ts}.py"
  rsync -a /Users/niels/Documents/binance/config_tradier.py "$vol/binance/history/config_tradier_${ts}.py"
  rsync -a /Users/niels/Documents/binance/v12_quick_engine.py "$vol/binance/history/v12_quick_engine_${ts}.py"
  rsync -a /Users/niels/Documents/binance/tradier_manage.py "$vol/binance/history/tradier_manage_${ts}.py"
  # also sync entire binance-sandbox to ext for full history (excluding data/market_data_*.json large)
  rsync -a --exclude='data/market_data_*' --exclude='__pycache__' /Users/niels/Documents/binance/ "$vol/binance/mirror/" 2>&1 | tail -5
  echo "[$ts] synced to $vol"
done
# 2026-09-28 GHOST-PUSH REMOVED (USER: "get rid of it"): this block shipped
# v12_quick_engine.py ALONE (no vec_decisions) + a stale TEMPLATE.xlsx to s1 every
# 15 min, snapshotting mid-edit Mac states (crashed 14+ pilots 17:2xZ, 4 more
# 19:31Z as md5 7c138882). Engine deploys are coordinated atomic batches ONLY;
# templates travel via tools/template_push.sh. The ext-drive history backups
# above are untouched — every engine version is still archived locally.

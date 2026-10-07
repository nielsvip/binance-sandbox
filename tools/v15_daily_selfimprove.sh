#!/bin/bash
# v15_daily_selfimprove — defaults self-improvement loop (USER 2026-10-03, BIBLE §64).
# rebuild (fresh cross-sym stats) -> promote MAIN+NORM (hardened gates) -> builder (P0)
# -> watchdog -> deploy S1/S2. Pilots read templates at startup: in-flight sheets finish
# undisturbed, new launches pick up new defaults. NEVER relies on *latest* names (clobbered
# by syncs): the dated aggregate file is passed explicitly through every step.
set -u
cd /Users/niels/Documents/binance || exit 1
VENV=.venv/bin/python
echo "=== $(date -u +%FT%TZ) selfimprove start"
timeout 1500 $VENV -u tools/v15_avg_delta_rebuild.py || exit 1
AGG=$(ls -t SPREADSHEETS/v15_avg_delta/v15_avg_delta_*.xlsx | head -1)
echo "AGG=$AGG"
timeout 500 $VENV -u tools/v15_daily_template_update.py --agg "$AGG" --apply || exit 1
timeout 500 $VENV -u tools/v15_daily_template_update.py --agg "$AGG" --template-dir SPREADSHEETS/TEMPLATE_FINAL_NORM --apply || exit 1
timeout 300 $VENV -u tools/build_cat_side_defaults_4.py || exit 1
cp data/cat_side_defaults_4.json data/sweep_defaults/cat_side_defaults_4.json
cp "$AGG" SPREADSHEETS/v15_avg_delta_latest.xlsx
cp "$AGG" SPREADSHEETS/v15_vector_delta_latest.xlsx
timeout 500 $VENV -u tools/v15_zero_delta_watchdog.py --agg "$AGG" --apply || true
for H in s1-pub niels@10.0.0.4; do
  rsync -az -e "ssh -o StrictHostKeyChecking=accept-new" SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx "$H:~/binance-sandbox/SPREADSHEETS/" || exit 1
  rsync -az -e "ssh -o StrictHostKeyChecking=accept-new" SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx "$H:~/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM/" || exit 1
  rsync -az -e "ssh -o StrictHostKeyChecking=accept-new" data/cat_side_defaults_4.json "$H:~/binance-sandbox/data/cat_side_defaults_4.json" || exit 1
  rsync -az -e "ssh -o StrictHostKeyChecking=accept-new" data/sweep_defaults/cat_side_defaults_4.json "$H:~/binance-sandbox/data/sweep_defaults/cat_side_defaults_4.json" || exit 1
  rsync -az -e "ssh -o StrictHostKeyChecking=accept-new" data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json "$H:~/binance-sandbox/data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json" || true
  rsync -az -e "ssh -o StrictHostKeyChecking=accept-new" "$AGG" SPREADSHEETS/v15_avg_delta_latest.xlsx SPREADSHEETS/v15_vector_delta_latest.xlsx "$H:~/binance-sandbox/SPREADSHEETS/" || true
done
echo "=== $(date -u +%FT%TZ) selfimprove done"

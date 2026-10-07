#!/bin/bash
# Kill BTCUSDC repeat — already done final 10.76, should not be re-run
pkill -f "v15_pilot.*BTCUSDC_LONG" || true
pkill -f "v15_pilot.*BTCUSDC" || true
echo "killed BTCUSDC pilots"
# Ensure generic matrix exists to mark done
ls -lh ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/BTCUSDC* 2>&1 | head

#!/bin/bash
cd "$(dirname "$0")/.."
STAMP="SPREADSHEETS/.template_defaults_verified.json"
if [ "$1" != "--force" ] && [ -f "$STAMP" ]; then
  verified=$(python3 -c "import json; print(json.load(open('$STAMP')).get('verified_at',0))" 2>/dev/null)
  now=$(python3 -c "import time; print(time.time())")
  age=$(python3 -c "print($now - $verified)" 2>/dev/null)
  if python3 -c "import sys; sys.exit(0 if $age < 86400 else 1)" 2>/dev/null; then
    exit 0
  fi
fi
PYTHONPATH=. python3 tools/verify_template_defaults.py 2>&1
if ssh -o ConnectTimeout=5 niels@157.180.125.52 "echo ok" 2>&1 | grep -q ok; then
  for f in TEMPLATE.xlsx TEMPLATE_STOCKS_LONG.xlsx TEMPLATE_STOCKS_SHORT.xlsx TEMPLATE_CRYPTO_LONG.xlsx TEMPLATE_CRYPTO_SHORT.xlsx; do
    rsync -avz -e "ssh -o ConnectTimeout=30" "SPREADSHEETS/$f" "niels@157.180.125.52:/home/niels/binance-sandbox/SPREADSHEETS/$f" 2>&1 | head -n 3
  done
  rsync -avz -e "ssh -o ConnectTimeout=30" SPREADSHEETS/.template_defaults_verified.json niels@157.180.125.52:/home/niels/binance-sandbox/SPREADSHEETS/.template_defaults_verified.json 2>&1 | head -n 5
fi

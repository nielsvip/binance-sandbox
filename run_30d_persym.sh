#!/bin/bash
cd /root/binance-sandbox
export PYTHONPATH=/root/binance-sandbox:$PYTHONPATH
# Fixed: v12_pilot_sheet_runner.py takes --sym-side singular, loop per_sym (410 crypto)
PER_SYM=$(python3 -c "import json; d=json.load(open('data/hourly_reconfig/per_sym_active_config.json')); print(' '.join(sorted([k for k in d.keys() if not k.startswith('_')])))")
COUNT=$(echo $PER_SYM | wc -w)
echo "per_sym ${COUNT} sides, starting 30D with universal template, all filters at end of tab like we used to"
LOG=/tmp/30d_all_persym.log
echo "[$(date -u)] starting 30D all per_sym ${COUNT} sides, 4 workers, log $LOG" | tee $LOG
for sym in $PER_SYM; do
  echo "=== $sym 30D ===" | tee -a $LOG
  python3 -u tools/opt/v12_pilot_sheet_runner.py --sym-side $sym --template SPREADSHEETS/TEMPLATE_UNIVERSAL_20260906.xlsx --window-days 30 --workers 4 --vector-only 2>&1 | tee -a $LOG
  echo "--- $sym done $(date -u) ---" | tee -a $LOG
done
echo "[$(date -u)] done 30D all per_sym" | tee -a $LOG
# Also handle 216 stocks if present (batch sweep 410 crypto + 216 stocks)
if [ -f data/hourly_reconfig/per_sym_active_config_stocks.json ]; then
  STOCKS=$(python3 -c "import json; d=json.load(open('data/hourly_reconfig/per_sym_active_config_stocks.json')); print(' '.join(sorted([k for k in d.keys() if not k.startswith('_')])))")
  SCOUNT=$(echo $STOCKS | wc -w)
  echo "[$(date -u)] starting stocks ${SCOUNT} sides" | tee -a $LOG
  for sym in $STOCKS; do
    echo "=== $sym 30D ===" | tee -a $LOG
    python3 -u tools/opt/v12_pilot_sheet_runner.py --sym-side $sym --template SPREADSHEETS/TEMPLATE_UNIVERSAL_20260906.xlsx --window-days 30 --workers 4 --vector-only 2>&1 | tee -a $LOG
  done
fi

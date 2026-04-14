#!/bin/bash
# 48-HOUR AUTONOMOUS SWEEP — runs configs sequentially, logs gain+sharpe per run
cd /Users/niels/Documents/binance
PY=/opt/anaconda3/envs/binance_env/bin/python
LOG=/Users/niels/logs/sweep_48h_master.log

configs=(
  "4h,D:velocity:-2"
  "4h,D:velocity:-3"
  "4h,D:velocity:-5"
  "4h,D:velocity:-8"
  "4h,D:cross:-2"
  "4h,D:both:-2"
  "4h,D:both:-3"
  "D:velocity:-2"
  "D:velocity:-5"
  "D:cross:-2"
  "D:both:-2"
  "4h:velocity:-2"
  "4h:velocity:-3"
  "4h:velocity:-5"
  "4h:cross:-2"
  "1h,4h,D:velocity:-2"
  "1h,4h,D:velocity:-3"
  "1h,4h,D:both:-2"
  "1h,4h:velocity:-2"
  "1h,4h:velocity:-3"
  "1h,4h:cross:-2"
  "4h,D:velocity:-1"
  "4h,D:velocity:-10"
  "D:velocity:-1"
  "D:velocity:-8"
)

echo "$(date) === 48H SWEEP START — ${#configs[@]} configs ===" >> $LOG

for cfg in "${configs[@]}"; do
  IFS=':' read -r tfs mode vel <<< "$cfg"
  name=$(echo "${tfs}_${mode}${vel}" | tr ',' '+')
  runlog=/Users/niels/logs/sweep_run_${name}.log
  
  echo "$(date) START $name" >> $LOG
  $PY -u backtest_v5_full_tradier.py --all --start 2024-06-01 --noloss 0 \
    --wt-exit-tfs "$tfs" --wt-exit-mode "$mode" --wt-vel-threshold "$vel" \
    > "$runlog" 2>&1
  
  # Extract results from JSONL
  latest=$(ls -t backtest_v5/logs/full_ALL_trb_*.jsonl 2>/dev/null | head -1)
  if [ -n "$latest" ]; then
    result=$($PY -c "
import json, numpy as np
trades=[json.loads(l) for l in open('$latest')]
closes=[t for t in trades if t.get('action')=='CLOSE']
gains=[t['gain'] for t in closes if 'gain' in t]
pnls=[t['pnl'] for t in closes if 'pnl' in t]
n=len(closes)
pnl=sum(pnls)
sharpe=0
if len(gains)>10 and np.std(gains)>0:
    sharpe=np.mean(gains)/np.std(gains)*np.sqrt(252*26)
print(f'{n} closes | PnL=\${pnl:.0f} | Sharpe={sharpe:.2f}')
" 2>/dev/null)
    echo "$(date) DONE $name: $result" >> $LOG
    echo "$name,$result" >> /Users/niels/Documents/binance/data/sweep_48h_results.csv
  else
    echo "$(date) DONE $name: NO OUTPUT" >> $LOG
  fi
done

echo "$(date) === 48H SWEEP COMPLETE ===" >> $LOG

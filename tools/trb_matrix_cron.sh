#!/bin/bash
# TRB MATRIX CRON — survives reboot and OOM, cell-by-cell resume, never stops until 121/121 TRB sym_sides gain>B&H
set -e
LOCK=/tmp/trb_matrix.lock
exec 9>$LOCK
if ! flock -n 9; then
  echo "$(date) trb_matrix_cron already running" >> /tmp/trb_matrix_cron.log
  exit 0
fi
if [ -f /tmp/v12_hang_sentinel ]; then echo "$(date) HANG SENTINEL active — all crons stopped" >> /tmp/trb_matrix_cron.log; exit 0; fi
echo "$(date) trb_matrix_cron start" >> /tmp/trb_matrix_cron.log
# hang watchdog: if any v12_pilot stuck >20m at Loaded 1 symbols, kill all and set sentinel
if pgrep -f "v12_pilot.*matrix" >/dev/null; then
  for pid in $(pgrep -f "v12_pilot.*matrix"); do
    age=$(ps -o etimes= -p $pid 2>/dev/null | tr -d " ")
    if [ "${age:-0}" -gt 1200 ]; then
      echo "$(date) HANG DETECTED pid $pid age ${age}s — killing all v12_pilot/xargs and setting sentinel" >> /tmp/trb_matrix_cron.log
      pkill -9 -f "v12_pilot"; pkill -9 -f "xargs.*v12"; rm -f /tmp/trb_matrix.lock
      touch /tmp/v12_hang_sentinel
      echo "$(date) SENTINEL set — all cronjobs halted until manual rm /tmp/v12_hang_sentinel" >> /tmp/trb_matrix_cron.log
      exit 1
    fi
  done
fi
# Ensure S1 FULL: CPU 80-99 RAM 65-90 via throttling in Python fleet, not here
# Check each TRB sym_side
PY=/home/niels/.conda/envs/binance_env/bin/python
TRB_LONG=/home/niels/binance-sandbox/symbols_trb_long.json
TRB_SHORT=/home/niels/binance-sandbox/symbols_trb_short.json
# Count done
DONE=$($PY << 'PY' 2>&1 | tail -1
import json, pathlib, openpyxl
from pathlib import Path
longs=json.loads(Path('/home/niels/binance-sandbox/symbols_trb_long.json').read_text())
shorts=json.loads(Path('/home/niels/binance-sandbox/symbols_trb_short.json').read_text())
done=0
total=len(longs)+len(shorts)
for sym in longs:
    p=f'/home/niels/binance-sandbox/SPREADSHEETS/{sym}_LONG_30d_matrix_v9_{sym}_LONG.xlsx'
    if Path(p).exists():
        try:
            wb=openpyxl.load_workbook(p, data_only=True)
            ws=wb['Results_30d_Deltas']
            # check if any YES has gain>B&H (pos delta)
            pos=sum(1 for r in ws.iter_rows(min_row=2, values_only=True) if r[3]=='YES' and r[4] is not None and r[4]>0)
            if pos>0:
                done+=1
        except: pass
for sym in shorts:
    p=f'/home/niels/binance-sandbox/SPREADSHEETS/{sym}_SHORT_30d_matrix_v9_{sym}_SHORT.xlsx'
    if Path(p).exists():
        try:
            wb=openpyxl.load_workbook(p, data_only=True)
            ws=wb['Results_30d_Deltas']
            pos=sum(1 for r in ws.iter_rows(min_row=2, values_only=True) if r[3]=='YES' and r[4] is not None and r[4]>0)
            if pos>0:
                done+=1
        except: pass
print(f"{done}/{total}")
PY
)
echo "$(date) TRB done $DONE" >> /tmp/trb_matrix_cron.log
if [[ "$DONE" == *"121/121"* ]]; then
  echo "$(date) ALL TRB 121/121 pos — done, not enqueuing" >> /tmp/trb_matrix_cron.log
  exit 0
fi
# Enqueue next missing TRB sym_side via fleet (first missing)
/bin/bash /home/niels/binance-sandbox/tools/run_trb_fleet.sh >> /tmp/trb_matrix_cron.log 2>&1 || echo "fleet run failed" >> /tmp/trb_matrix_cron.log
echo "$(date) enqueued" >> /tmp/trb_matrix_cron.log

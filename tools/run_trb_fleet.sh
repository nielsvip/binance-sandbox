#!/bin/bash
# run_trb_fleet.sh — TRB 121 fleet, 4 at a time xargs -P4 ×7 workers=28, hang-safe
# Fix for: flock deadlock (0B /tmp/trb_matrix.lock held by xargs -P4 global lock) + xargs -I SYM syntax bug
# POSITION only; AUGMENT_AT_LOSS+10 SCALP/HEDGE excluded per pilot spec
set -e
HANG_SENTINEL=/tmp/v12_hang_sentinel
if [ -f "$HANG_SENTINEL" ]; then echo "$(date) HANG SENTINEL active — fleet not launched"; exit 0; fi
PY=/home/niels/.conda/envs/binance_env/bin/python
# Build missing list via python helper
MISSING=$($PY << 'PYEOF'
import json, openpyxl
from pathlib import Path
longs=json.loads(Path("/home/niels/binance-sandbox/symbols_trb_long.json").read_text())
shorts=json.loads(Path("/home/niels/binance-sandbox/symbols_trb_short.json").read_text())
missing=[]
for sym in longs:
    p=Path(f"/home/niels/binance-sandbox/SPREADSHEETS/{sym}_LONG_30d_matrix_v9_{sym}_LONG.xlsx")
    if not p.exists():
        missing.append(f"{sym}_LONG"); continue
    try:
        wb=openpyxl.load_workbook(str(p), data_only=True)
        ws=wb["Results_30d_Deltas"]
        pos=sum(1 for r in ws.iter_rows(min_row=2, values_only=True) if r[3]=="YES" and r[4] is not None and r[4]>0)
        if pos==0: missing.append(f"{sym}_LONG")
    except: missing.append(f"{sym}_LONG")
for sym in shorts:
    p=Path(f"/home/niels/binance-sandbox/SPREADSHEETS/{sym}_SHORT_30d_matrix_v9_{sym}_SHORT.xlsx")
    if not p.exists():
        missing.append(f"{sym}_SHORT"); continue
    try:
        wb=openpyxl.load_workbook(str(p), data_only=True)
        ws=wb["Results_30d_Deltas"]
        pos=sum(1 for r in ws.iter_rows(min_row=2, values_only=True) if r[3]=="YES" and r[4] is not None and r[4]>0)
        if pos==0: missing.append(f"{sym}_SHORT")
    except: missing.append(f"{sym}_SHORT")
for m in missing: print(m)
PYEOF
)
if [ -z "$MISSING" ]; then echo "$(date) fleet: no missing TRB sym_sides"; exit 0; fi
echo "$MISSING" | head -n 20
COUNT=$(echo "$MISSING" | wc -l)
echo "$(date) fleet: $COUNT missing, launching 4 at a time with timeout"
# Correct xargs syntax: -I{} with {} placeholder, -P4 parallel
# Each shard: timeout 1200s (20m Bible kill threshold) + per-symside workers 7
echo "$MISSING" | xargs -I{} -P4 bash -c '
  SYM="{}"
  echo "$(date) START $SYM"
  timeout -k 10 1200 /home/niels/.conda/envs/binance_env/bin/python -u /home/niels/binance-sandbox/v12_pilot.py matrix --symbols "$SYM" --workers 7 2>&1 | tee "/tmp/v12_${SYM}.log"
  RC=${PIPESTATUS[0]}
  if [ $RC -eq 124 ] || [ $RC -eq 137 ]; then
    echo "$(date) HANG TIMEOUT $SYM rc $RC — killing all and setting sentinel"
    pkill -9 -f "v12_pilot" || true; pkill -9 -f "xargs.*v12" || true; rm -f /tmp/trb_matrix.lock; touch /tmp/v12_hang_sentinel
    exit 1
  fi
  echo "$(date) DONE $SYM rc $RC"
'
